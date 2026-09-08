from __future__ import unicode_literals
import zipfile
import os.path
import sys
import xbmcvfs
import xbmcgui
from dropbox import dropbox
from . import utils as utils
from dropbox.files import WriteMode, CommitInfo, UploadSessionCursor
from . authorizers import DropboxAuthorizer


class Vfs:
    root_path = None

    def __init__(self, rootString):
        self.set_root(rootString)

    def clean_path(self, path):
        # fix slashes
        path = path.replace("\\", "/")

        # check if trailing slash is included
        if(path[-1:] != '/'):
            path = path + '/'

        return path

    def set_root(self, rootString):
        old_root = self.root_path
        self.root_path = self.clean_path(rootString)

        # return the old root
        return old_root

    def listdir(self, directory):
        return {}

    def mkdir(self, directory):
        return True

    def put(self, source, dest):
        return True

    def rmdir(self, directory):
        return True

    def rmfile(self, aFile):
        return True

    def exists(self, aFile):
        return True

    def rename(self, aFile, newName):
        return True

    def cleanup(self):
        return True

    def fileSize(self, filename):
        return 0  # result should be in KB


class XBMCFileSystem(Vfs):

    def listdir(self, directory):
        return xbmcvfs.listdir(directory)

    def mkdir(self, directory):
        return xbmcvfs.mkdir(xbmcvfs.translatePath(directory))

    def put(self, source, dest):
        return xbmcvfs.copy(xbmcvfs.translatePath(source), xbmcvfs.translatePath(dest))

    def rmdir(self, directory):
        return xbmcvfs.rmdir(directory, force=True)  # use force=True to make sure it works recursively

    def rmfile(self, aFile):
        return xbmcvfs.delete(aFile)

    def rename(self, aFile, newName):
        return xbmcvfs.rename(aFile, newName)

    def exists(self, aFile):
        return xbmcvfs.exists(aFile)

    def fileSize(self, filename):
        with xbmcvfs.File(filename) as f:
            result = f.size() / 1024  # bytes to kilobytes

        return result


class ZipFileSystem(Vfs):
    zip = None

    def __init__(self, rootString, mode):
        self.root_path = ""
        self.zip = zipfile.ZipFile(rootString, mode=mode, compression=zipfile.ZIP_DEFLATED, allowZip64=True)

    def listdir(self, directory):
        return [[], []]

    def mkdir(self, directory):
        # self.zip.write(directory[len(self.root_path):])
        return False

    def put(self, source, dest):
        try:
            with xbmcvfs.File(xbmcvfs.translatePath(source), 'r') as aFile:
                with self.zip.open(dest, 'w', force_zip64=True) as member:
                    while True:
                        chunk = aFile.readBytes(1024 * 1024)
                        if not chunk:
                            break
                        member.write(chunk)
            return True
        except Exception as error:
            utils.log("Unable to write ZIP member %s: %s" % (dest, error))
            return False

    def rmdir(self, directory):
        return False

    def exists(self, aFile):
        return False

    def cleanup(self):
        self.zip.close()

    def extract(self, aFile, path):
        # extract zip file to path
        self.zip.extract(aFile, path)

    def readFile(self, aFile):
        return self.zip.read(aFile)

    def openFile(self, aFile):
        return self.zip.open(aFile, 'r')

    def listFiles(self):
        return self.zip.infolist()


class DropboxFileSystem(Vfs):
    MAX_CHUNK = 50 * 1000 * 1000  # dropbox uses 150, reduced to 50 for small mem systems
    client = None
    APP_KEY = ''
    APP_SECRET = ''

    def __init__(self, rootString):
        self.set_root(rootString)

        authorizer = DropboxAuthorizer()

        if(authorizer.isAuthorized()):
            self.client = authorizer.getClient()
        else:
            # tell the user to go back and run the authorizer
            xbmcgui.Dialog().ok(utils.getString(30010), utils.getString(30105))
            sys.exit()

    def listdir(self, directory):
        directory = self._fix_slashes(directory)

        if(self.client is not None and self.exists(directory)):
            files = []
            dirs = []
            metadata = self.client.files_list_folder(directory)

            for aFile in metadata.entries:
                if(isinstance(aFile, dropbox.files.FolderMetadata)):
                    dirs.append(aFile.name)
                else:
                    files.append(aFile.name)

            return [dirs, files]
        else:
            return [[], []]

    def mkdir(self, directory):
        directory = self._fix_slashes(directory)
        if(self.client is not None):
            # sort of odd but always return true, folder create is implicit with file upload
            return True
        else:
            return False

    def rmdir(self, directory):
        directory = self._fix_slashes(directory)
        if(self.client is not None and self.exists(directory)):
            # dropbox is stupid and will refuse to do this sometimes, need to delete recursively
            dirs, files = self.listdir(directory)

            for aDir in dirs:
                self.rmdir(aDir)

            # finally remove the root directory
            self.client.files_delete(directory)

            return True
        else:
            return False

    def rmfile(self, aFile):
        aFile = self._fix_slashes(aFile)

        if(self.client is not None and self.exists(aFile)):
            self.client.files_delete(aFile)
            return True
        else:
            return False

    def exists(self, aFile):
        aFile = self._fix_slashes(aFile)

        if(self.client is not None):
            # can't list root metadata
            if(aFile == ''):
                return True

            try:
                self.client.files_get_metadata(aFile)
                # if we make it here the file does exist
                return True
            except:
                return False
        else:
            return False

    def put(self, source, dest, retry=True):
        dest = self._fix_slashes(dest)

        if(self.client is None):
            return False

        attempts = 2 if retry else 1
        for _attempt in range(attempts):
            try:
                file_size = os.path.getsize(source)
                with open(source, 'rb') as source_file:
                    if file_size <= self.MAX_CHUNK:
                        self.client.files_upload(
                            source_file.read(), dest,
                            mode=WriteMode('overwrite'))
                    else:
                        session = self.client.files_upload_session_start(
                            source_file.read(self.MAX_CHUNK))
                        cursor = UploadSessionCursor(
                            session.session_id, source_file.tell())
                        while source_file.tell() < file_size:
                            remaining = file_size - source_file.tell()
                            if remaining <= self.MAX_CHUNK:
                                self.client.files_upload_session_finish(
                                    source_file.read(self.MAX_CHUNK), cursor,
                                    CommitInfo(
                                        dest, mode=WriteMode('overwrite')))
                            else:
                                self.client.files_upload_session_append_v2(
                                    source_file.read(self.MAX_CHUNK), cursor)
                                cursor.offset = source_file.tell()
                return True
            except Exception as error:
                utils.log(str(error))
        return False

    def fileSize(self, filename):
        result = 0
        aFile = self._fix_slashes(filename)

        if(self.client is not None):
            metadata = self.client.files_get_metadata(aFile)
            result = metadata.size / 1024  # bytes to KB

        return result

    def get_file(self, source, dest):
        if(self.client is not None):
            # write the file locally
            self.client.files_download_to_file(dest, source)
            return True
        else:
            return False

    def _fix_slashes(self, filename):
        result = filename.replace('\\', '/')

        # root needs to be a blank string
        if(result == '/'):
            result = ""

        # if dir ends in slash, remove it
        if(result[-1:] == "/"):
            result = result[:-1]

        return result
