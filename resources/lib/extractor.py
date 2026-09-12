from . import utils as utils
from .archive import (
    find_manifest_member,
    load_manifest,
    validate_archive_members,
    verify_archive_payload,
)
from .restore_ui import restore_preparation_message


class ZipExtractor:

    def extract(self, zipFile, outLoc, progressBar):
        utils.log("extracting zip archive")

        result = True  # result is true unless we fail

        # update the progress bar
        progressBar.updateProgress(0, restore_preparation_message(
            utils.getString, 'verify_archive'))

        # Validate the complete central directory before materializing anything.
        files = zipFile.listFiles()
        try:
            validate_archive_members(files)
            manifest_member, _root = find_manifest_member(files)
            manifest_data = zipFile.readFile(manifest_member)
            if isinstance(manifest_data, bytes):
                manifest_data = manifest_data.decode('utf-8')
            manifest = load_manifest(manifest_data)
            verify_archive_payload(files, manifest, zipFile.openFile)
        except Exception as error:
            utils.log("Unsafe archive rejected: %s" % error)
            return False

        fileCount = float(len(files))
        currentFile = 0

        progressBar.updateProgress(0, restore_preparation_message(
            utils.getString, 'extract_archive'))

        try:
            for aFile in files:
                # update the progress bar
                currentFile += 1
                progressBar.updateProgress(int((currentFile / fileCount) * 100), utils.getString(30100))

                # extract the file
                zipFile.extract(aFile, outLoc)

        except Exception:
            utils.log("Error extracting file")
            result = False

        return result
