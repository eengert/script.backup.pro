from __future__ import unicode_literals
import time
import json
import xbmc
import xbmcgui
import xbmcaddon
import xbmcvfs
import os.path
import shutil
from . import utils as utils
from datetime import datetime
from . vfs import XBMCFileSystem, DropboxFileSystem, ZipFileSystem
from . progressbar import BackupProgressBar
from resources.lib.guisettings import GuiSettingsManager
from resources.lib.extractor import ZipExtractor
from resources.lib.archive import (
    MANIFEST_NAME,
    ArchiveValidationError,
    build_manifest,
    load_manifest,
    sha256_reader,
    verify_manifest_files,
    verify_zip_archive,
)
from resources.lib.planning import (
    FilePlanner,
    TMDB_HELPER_ID,
    summarize_file_groups,
    tmdb_helper_cache_exclusions,
)
from resources.lib.skin_adapter import (
    AF3_ID,
    APPEARANCE_SETTINGS,
    SKIN_VARIABLES_ID,
    SkinAdapterError,
    capture_af3_snapshot,
    managed_source_paths,
)


def folderSort(aKey):
    result = aKey[0]

    if(len(result) < 8):
        result = result + "0000"

    return result


class XbmcBackup:
    # constants for initiating a back or restore
    Backup = 0
    Restore = 1

    ZIP_TEMP_PATH = None

    # list of dirs for the "simple" file selection
    simple_directory_list = ['addons', 'addon_data', 'database', 'game_saves', 'playlists', 'profiles', 'thumbnails', 'config']

    # file systems
    xbmc_vfs = None
    remote_vfs = None
    saved_remote_vfs = None

    restoreFile = None
    remote_base_path = None

    # for the progress bar
    progressBar = None
    transferSize = 0
    transferLeft = 0

    restore_point = None
    skip_advanced = False   # if we should check for the existance of advancedsettings in the restore

    def __init__(self):
        self.xbmc_vfs = XBMCFileSystem(xbmcvfs.translatePath('special://home'))
        self.ZIP_TEMP_PATH = xbmcvfs.translatePath(utils.getSetting('zip_temp_path'))
        self.transferSize = 0
        self.transferLeft = 0
        self.backup_plan = None
        self.backup_manifest = None
        self.backup_manifest_file_hash = None
        self._automatic_exclusion_rules = None
        self._active_artifact = None
        self._active_artifact_compressed = False
        self._vfs_closed = True
        self._skin_stage_path = None
        self._skin_snapshot_metadata = None
        self._skin_managed_exclusions = []

        self.configureRemote()
        utils.log(utils.getString(30046))

    def configureRemote(self):
        if(utils.getSetting('remote_selection') == '1'):
            self.remote_vfs = XBMCFileSystem(utils.getSetting('remote_path_2'))
            utils.setSetting("remote_path", "")
        elif(utils.getSetting('remote_selection') == '0'):
            self.remote_vfs = XBMCFileSystem(utils.getSetting("remote_path"))
        elif(utils.getSetting('remote_selection') == '2'):
            self.remote_vfs = DropboxFileSystem("/")

        self.remote_base_path = self.remote_vfs.root_path

    def remoteConfigured(self):
        result = True

        if(self.remote_base_path == "" or not xbmcvfs.exists(self.ZIP_TEMP_PATH)):
            result = False

        return result

    # reverse - should reverse the resulting, default is true - newest to oldest
    def listBackups(self, reverse=True):
        result = []

        # get all the folders in the current root path
        dirs, files = self.remote_vfs.listdir(self.remote_base_path)

        for aDir in dirs:
            if(self.remote_vfs.exists(
                    self.remote_base_path + aDir + "/" + MANIFEST_NAME)):

                # format the name according to regional settings
                folderName = self._dateFormat(aDir)

                result.append((aDir, folderName))

        for aFile in files:
            file_ext = aFile.split('.')[-1]
            folderName = aFile.split('.')[0]

            if(file_ext == 'zip' and len(folderName) >= 12 and folderName[0:12].isdigit()):

                # format the name according to regional settings and display the file size
                folderName = "%s - %s" % (self._dateFormat(folderName), utils.diskString(self.remote_vfs.fileSize(self.remote_base_path + aFile)))

                result.append((aFile, folderName))

        result.sort(key=folderSort, reverse=reverse)

        return result

    def selectRestore(self, restore_point):
        self.restore_point = restore_point

    def skipAdvanced(self):
        self.skip_advanced = True

    def backup(self, progressOverride=False):
        try:
            return self._runBackup(progressOverride)
        except Exception as error:
            utils.log('Backup failed: %s' % error, xbmc.LOGWARNING)
            self._discardActiveArtifact()
            utils.showNotification(utils.getString(30092))
            return False
        finally:
            self._cleanupSkinStage()
            if not self._vfs_closed:
                self._closeVFS()

    def _runBackup(self, progressOverride=False):
        shouldContinue = self._setupVFS(self.Backup, progressOverride)

        if(shouldContinue):
            utils.log(utils.getString(30023) + " - " + utils.getString(30016))
            # Folder backups must never reuse a prior timestamp root. ZIP
            # archives create their logical root inside the local ZIP instead.
            if not isinstance(self.remote_vfs, ZipFileSystem):
                if self.remote_vfs.exists(self.remote_vfs.root_path):
                    utils.log('Backup target already exists: ' +
                              self.remote_vfs.root_path, xbmc.LOGWARNING)
                    utils.showNotification(utils.getString(30092))
                    self._closeVFS()
                    return False
                if not self.remote_vfs.mkdir(self.remote_vfs.root_path):
                    utils.log('Unable to create backup target: ' +
                              self.remote_vfs.root_path, xbmc.LOGWARNING)
                    utils.showNotification(utils.getString(30092))
                    self._closeVFS()
                    return False
                self._active_artifact = self.remote_vfs.root_path
                self._active_artifact_compressed = False

            utils.log(utils.getString(30051))
            utils.log('File Selection Type: ' + str(utils.getSetting('backup_selection_type')))
            allFiles = self._collectBackupFiles()

            try:
                writeCheck = self._createValidationFile(allFiles)
            except Exception as error:
                utils.log('Unable to create Backup Pro manifest: %s' % error,
                          xbmc.LOGWARNING)
                writeCheck = False

            if(not writeCheck):
                if isinstance(self.remote_vfs, ZipFileSystem):
                    utils.showNotification(utils.getString(30092))
                else:
                    self._finalizeBackup(
                        False, self.remote_vfs.root_path, compressed=False)
                self._closeVFS()
                return False

            orig_base_path = self.remote_vfs.root_path
            backup_success = True
            compressing = utils.getSettingBool("compress_backups")
            remote_artifact = None if compressing else orig_base_path

            # backup all the files
            self.transferLeft = self.transferSize
            for fileGroup in allFiles:
                self.xbmc_vfs.set_root(xbmcvfs.translatePath(fileGroup['source']))
                self.remote_vfs.set_root(fileGroup['dest'] + fileGroup['name'])
                filesCopied = self._copyFiles(fileGroup['files'], self.xbmc_vfs, self.remote_vfs)

                if(not filesCopied):
                    utils.log(utils.getString(30092))
                    backup_success = False

            # reset remote and xbmc vfs
            self.xbmc_vfs.set_root("special://home/")
            self.remote_vfs.set_root(orig_base_path)

            if(compressing):
                fileManager = FileManager(self.xbmc_vfs)

                # send the zip file to the real remote vfs
                zip_name = os.path.join(self.ZIP_TEMP_PATH, self.remote_vfs.root_path[:-1] + ".zip")
                self.remote_vfs.cleanup()
                renamed = self.xbmc_vfs.rename(
                    os.path.join(self.ZIP_TEMP_PATH, "xbmc_backup_temp.zip"),
                    zip_name)
                if not renamed:
                    backup_success = False
                else:
                    try:
                        verify_zip_archive(
                            zip_name, check_cancel=self.progressBar.checkCancel)
                    except ArchiveValidationError as error:
                        utils.log('Local ZIP verification failed: %s' % error,
                                  xbmc.LOGWARNING)
                        backup_success = False
                    fileManager.addFile(zip_name)

                # set root to data dir home and reset remote
                self.xbmc_vfs.set_root(self.ZIP_TEMP_PATH)
                self.remote_vfs = self.saved_remote_vfs

                # update the amount to transfer
                if backup_success:
                    remote_zip = (self.remote_vfs.root_path +
                                  os.path.basename(zip_name))
                    if self.remote_vfs.exists(remote_zip):
                        utils.log('Backup ZIP already exists: ' + remote_zip,
                                  xbmc.LOGWARNING)
                        backup_success = False
                    else:
                        remote_artifact = remote_zip
                        self._active_artifact = remote_zip
                        self._active_artifact_compressed = True
                if backup_success:
                    self.transferSize = fileManager.fileSize()
                    self.transferLeft = self.transferSize
                    fileCopied = self._copyFiles(
                        fileManager.getFiles(), self.xbmc_vfs,
                        self.remote_vfs)
                    backup_success = bool(fileCopied)
                if backup_success:
                    try:
                        self._verifyCompressedUpload(zip_name, remote_zip)
                    except ArchiveValidationError as error:
                        utils.log('Uploaded ZIP verification failed: %s' % error,
                                  xbmc.LOGWARNING)
                        backup_success = False

                # delete the temp zip file
                if renamed:
                    self.xbmc_vfs.rmfile(zip_name)
            elif backup_success:
                try:
                    self._verifyFolderBackup(orig_base_path)
                except ArchiveValidationError as error:
                    utils.log('Folder backup verification failed: %s' % error,
                              xbmc.LOGWARNING)
                    backup_success = False

            backup_success = self._finalizeBackup(
                backup_success, remote_artifact, compressing)

            self._closeVFS()
            return backup_success

        return False

    def restore(self, progressOverride=False, selectedSets=None):
        shouldContinue = self._setupVFS(self.Restore, progressOverride)

        if(shouldContinue):
            utils.log(utils.getString(30023) + " - " + utils.getString(30017))

            # catch for if the restore point is actually a zip file
            if(self.restore_point.split('.')[-1] == 'zip'):
                self.progressBar.updateProgress(2, utils.getString(30088))
                utils.log("copying zip file: " + self.restore_point)

                # set root to data dir home
                self.xbmc_vfs.set_root(self.ZIP_TEMP_PATH)
                restore_path = os.path.join(self.ZIP_TEMP_PATH, self.restore_point)
                if(not self.xbmc_vfs.exists(restore_path)):
                    # copy just this file from the remote vfs
                    self.transferSize = self.remote_vfs.fileSize(self.remote_base_path + self.restore_point)
                    zipFile = []
                    zipFile.append({'file': self.remote_base_path + self.restore_point, 'size': self.transferSize, 'is_dir': False})

                    # set transfer size
                    self.transferLeft = self.transferSize
                    self._copyFiles(zipFile, self.remote_vfs, self.xbmc_vfs)
                else:
                    utils.log("zip file exists already")

                # extract the zip file
                zip_vfs = ZipFileSystem(restore_path, 'r')
                extractor = ZipExtractor()

                if(not extractor.extract(zip_vfs, self.ZIP_TEMP_PATH, self.progressBar)):
                    # we had a problem extracting the archive, delete everything
                    zip_vfs.cleanup()
                    self.xbmc_vfs.rmfile(restore_path)

                    xbmcgui.Dialog().ok(utils.getString(30010), utils.getString(30101))
                    return

                zip_vfs.cleanup()

                self.progressBar.updateProgress(0, utils.getString(30049) + "......")
                # set the new remote vfs and fix xbmc path
                self.remote_vfs = XBMCFileSystem(os.path.join(self.ZIP_TEMP_PATH, self.restore_point.split(".")[0]))
                self.xbmc_vfs.set_root(xbmcvfs.translatePath("special://home/"))

            # for restores remote path must exist
            if(not self.remote_vfs.exists(self.remote_vfs.root_path)):
                xbmcgui.Dialog().ok(utils.getString(30010), '%s\n%s' % (utils.getString(30045), self.remote_vfs.root_path))
                return

            valFile = self._checkValidationFile(self.remote_vfs.root_path)
            if(valFile is None):
                # don't continue
                return

            utils.log(utils.getString(30051))
            allFiles = []
            fileManager = FileManager(self.remote_vfs)

            # check for the existance of an advancedsettings file
            if(self.remote_vfs.exists(self.remote_vfs.root_path + "config/advancedsettings.xml") and not self.skip_advanced):
                # let the user know there is an advanced settings file present
                restartXbmc = xbmcgui.Dialog().yesno(utils.getString(30038), "%s\n%s\n%s" % (utils.getString(30039), utils.getString(30040), utils.getString(30041)))

                if(restartXbmc):
                    # add only this file to the file list
                    self.transferSize = 1
                    self.transferLeft = 1
                    fileManager.addFile(self.remote_vfs.root_path + "config/advancedsettings.xml")
                    self._copyFiles(fileManager.getFiles(), self.remote_vfs, self.xbmc_vfs)

                    # let the service know to resume this backup on startup
                    self._createResumeBackupFile()

                    # do not continue running
                    if(xbmcgui.Dialog().yesno(utils.getString(30077), utils.getString(30078), autoclose=15000)):
                        xbmc.executebuiltin('Quit')

                    return

            # check if settings should be restored from this backup
            restoreSettings = not utils.getSettingBool('always_prompt_restore_settings')
            if(not restoreSettings and 'system_settings' in valFile):
                # prompt the user to restore settings yes/no
                restoreSettings = xbmcgui.Dialog().yesno(utils.getString(30149), utils.getString(30150))

            # use a multiselect dialog to select sets to restore
            restoreSets = [n['name'] for n in valFile['directories']]

            # if passed in list, skip selection
            if(selectedSets is None):
                selectedSets = xbmcgui.Dialog().multiselect(utils.getString(30131), restoreSets)
            else:
                selectedSets = [restoreSets.index(n) for n in selectedSets if n in restoreSets]  # if set name not found just skip it

            if(selectedSets is not None):
                if any(restoreSets[index].casefold() == 'skin_config'
                       for index in selectedSets):
                    utils.log(
                        'AF3 restore requires the transactional restore handler',
                        xbmc.LOGWARNING)
                    xbmcgui.Dialog().ok(
                        utils.getString(30010),
                        'Arctic Fuse 3 restore is not available in this development build.')
                    return False

                # go through each of the directories in the backup and write them to the correct location
                for index in selectedSets:

                    # add this directory
                    aDir = valFile['directories'][index]

                    self.xbmc_vfs.set_root(xbmcvfs.translatePath(aDir['path']))
                    if(self.remote_vfs.exists(self.remote_vfs.root_path + aDir['name'] + '/')):
                        # walk the directory
                        self.progressBar.updateProgress(0, f"{utils.getString(30049)}....{utils.getString(30162)}\n{utils.getString(30163)}: {aDir['name']}")
                        fileManager.walkTree(self.remote_vfs.root_path + aDir['name'] + '/')
                        self.transferSize = self.transferSize + fileManager.fileSize()

                        allFiles.append({"source": self.remote_vfs.root_path + aDir['name'], "dest": self.xbmc_vfs.root_path, "files": fileManager.getFiles()})
                    else:
                        utils.log("error path not found: " + self.remote_vfs.root_path + aDir['name'])
                        xbmcgui.Dialog().ok(utils.getString(30010), '%s\n%s' % (utils.getString(30045), self.remote_vfs.root_path + aDir['name']))

                # restore all the files
                self.transferLeft = self.transferSize
                for fileGroup in allFiles:
                    self.remote_vfs.set_root(fileGroup['source'])
                    self.xbmc_vfs.set_root(fileGroup['dest'])
                    self._copyFiles(fileGroup['files'], self.remote_vfs, self.xbmc_vfs)

            # update the Kodi settings - if we can
            if('system_settings' in valFile and restoreSettings):
                self.progressBar.updateProgress(98, "Restoring Kodi settings")
                gui_settings = GuiSettingsManager()
                gui_settings.restore(valFile['system_settings'])

            self.progressBar.updateProgress(99, "Clean up operations .....")

            if(self.restore_point.split('.')[-1] == 'zip'):
                # delete the zip file and the extracted directory
                self.xbmc_vfs.rmfile(os.path.join(self.ZIP_TEMP_PATH, self.restore_point))
                xbmc.sleep(1000)
                self.xbmc_vfs.rmdir(self.remote_vfs.clean_path(os.path.join(self.ZIP_TEMP_PATH, self.restore_point.split(".")[0])))
                xbmc.sleep(1000)

            # call update addons to refresh everything
            xbmc.executebuiltin('UpdateLocalAddons')

            # notify user that restart is recommended
            if(xbmcgui.Dialog().yesno(utils.getString(30077), utils.getString(30078), autoclose=15000)):
                xbmc.executebuiltin('Quit')


    def _setupVFS(self, mode=-1, progressOverride=False):
        # set windows setting to true
        window = xbmcgui.Window(10000)
        window.setProperty(utils.__addon_id__ + ".running", "true")

        # append backup folder name
        progressBarTitle = utils.getString(30010) + " - "
        if(mode == self.Backup and self.remote_vfs.root_path != ''):
            if(utils.getSettingBool("compress_backups")):
                # delete old temp file
                zip_path = os.path.join(self.ZIP_TEMP_PATH, 'xbmc_backup_temp.zip')
                if(self.xbmc_vfs.exists(zip_path)):
                    if(not self.xbmc_vfs.rmfile(zip_path)):
                        # we had some kind of error deleting the old file
                        xbmcgui.Dialog().ok(utils.getString(30010), '%s\n%s' % (utils.getString(30096), utils.getString(30097)))
                        return False

                # save the remote file system and use the zip vfs
                self.saved_remote_vfs = self.remote_vfs
                self.remote_vfs = ZipFileSystem(zip_path, "w")

            self.remote_vfs.set_root(self.remote_vfs.root_path + time.strftime("%Y%m%d%H%M%S") + utils.getSetting('backup_suffix').strip() + "/")
            progressBarTitle = progressBarTitle + utils.getString(30023) + ": " + utils.getString(30016)
        elif(mode == self.Restore and self.restore_point is not None and self.remote_vfs.root_path != ''):
            if(self.restore_point.split('.')[-1] != 'zip'):
                self.remote_vfs.set_root(self.remote_vfs.root_path + self.restore_point + "/")
            progressBarTitle = progressBarTitle + utils.getString(30023) + ": " + utils.getString(30017)
        else:
            # kill the program here
            self.remote_vfs = None
            return False

        utils.log(utils.getString(30047) + ": " + self.xbmc_vfs.root_path)
        utils.log(utils.getString(30048) + ": " + self.remote_vfs.root_path)
        utils.log(utils.getString(30152) + ": " + utils.getSetting('zip_temp_path'))

        # setup the progress bar
        self.progressBar = BackupProgressBar(progressOverride)
        self.progressBar.create(progressBarTitle, utils.getString(30049) + "......")
        self._vfs_closed = False

        # if we made it this far we're good
        return True

    def _closeVFS(self):
        if self._vfs_closed:
            return
        self._vfs_closed = True
        for cleanup in (
                self.xbmc_vfs.cleanup,
                self.remote_vfs.cleanup,
                self.progressBar.close):
            try:
                cleanup()
            except Exception as error:
                utils.log('Backup cleanup failed: %s' % error,
                          xbmc.LOGWARNING)
        try:
            window = xbmcgui.Window(10000)
            window.setProperty(utils.__addon_id__ + ".running", "")
        except Exception as error:
            utils.log('Unable to clear backup running state: %s' % error,
                      xbmc.LOGWARNING)

    def _copyFiles(self, fileList, source, dest):
        result = True

        utils.log("Source: " + source.root_path)
        utils.log("Destination: " + dest.root_path)

        # make sure the dest folder exists - can cause write errors if the full path doesn't exist
        if(not dest.exists(dest.root_path)):
            dest.mkdir(dest.root_path)

        for aFile in fileList:
            if(self.progressBar.checkCancel()):
                result = False
                break
            else:
                if(utils.getSettingBool('verbose_logging')):
                    utils.log('Writing file: ' + aFile['file'])

                if(aFile['is_dir']):
                    self._updateProgress('%s remaining\nwriting %s' % (utils.diskString(self.transferLeft), os.path.basename(aFile['file'][len(source.root_path):]) + "/"))
                    dest.mkdir(dest.root_path + aFile['file'][len(source.root_path):])
                else:
                    self._updateProgress('%s remaining\nwriting %s' % (utils.diskString(self.transferLeft), os.path.basename(aFile['file'][len(source.root_path):])))
                    self.transferLeft = self.transferLeft - aFile['size']

                    # copy the file
                    wroteFile = self._copyFile(source, dest, aFile['file'], dest.root_path + aFile['file'][len(source.root_path):])

                    # if result is still true but this file failed
                    if(not wroteFile and result):
                        utils.log("Failed to write " + aFile['file'])
                        result = False

        return result

    def _copyFile(self, source, dest, sourceFile, destFile):
        result = True

        if(isinstance(source, DropboxFileSystem)):
            # if copying from cloud storage we need the file handle, use get_file
            result = source.get_file(sourceFile, destFile)
        else:
            # copy using normal method
            result = dest.put(sourceFile, destFile)

        return result

    def _addBackupDir(self, folder_name, root_path, dirList):
        utils.log('Backup set: ' + folder_name)
        fileManager = FileManager(self.xbmc_vfs)

        self.xbmc_vfs.set_root(xbmcvfs.translatePath(root_path))
        for aDir in dirList:
            fileManager.addDir(aDir)
        for exclusion in self._automaticExclusions():
            fileManager.addDir(exclusion)

        # walk all the root trees
        fileManager.walk()

        summary = fileManager.summary()
        self.transferSize = self.transferSize + fileManager.fileSize()
        files = fileManager.getFiles()

        return {
            "name": folder_name,
            "source": root_path,
            "plan_root": xbmcvfs.translatePath(root_path),
            "dest": self.remote_vfs.root_path,
            "files": files,
            "summary": summary,
        }

    def _collectBackupFiles(self):
        allFiles = []
        self.transferSize = 0
        self._skin_snapshot_metadata = None
        self._skin_managed_exclusions = []
        self._automatic_exclusion_rules = None
        skin_group = None
        if utils.getSettingBool('backup_skin_config'):
            skin_group = self._captureSkinConfigGroup()

        if(utils.getSettingInt('backup_selection_type') == 0):
            selectedDirs = self._readBackupConfig(
                utils.addon_dir() + "/resources/data/default_files.json")
            for name in self.simple_directory_list:
                if(utils.getSettingBool('backup_' + name)):
                    selected = selectedDirs[name]
                    allFiles.append(self._addBackupDir(
                        name, selected['root'], selected['dirs']))
        else:
            selectedDirs = self._readBackupConfig(
                utils.data_dir() + "/custom_paths.json")
            for name in sorted(selectedDirs):
                selected = selectedDirs[name]
                allFiles.append(self._addBackupDir(
                    name, selected['root'], selected['dirs']))

        if skin_group is not None:
            allFiles.append(skin_group)

        self.backup_plan = summarize_file_groups(allFiles)
        utils.log('Backup plan: %s' % json.dumps(
            self.backup_plan, sort_keys=True))
        return allFiles

    def _skinRpc(self, method, **params):
        request = json.dumps({
            'jsonrpc': '2.0', 'method': method, 'params': params, 'id': 1,
        })
        response = json.loads(xbmc.executeJSONRPC(request))
        if not isinstance(response, dict) or 'error' in response:
            raise SkinAdapterError('Kodi could not complete ' + method)
        return response.get('result')

    def _skinAppearance(self):
        values = {}
        for setting in APPEARANCE_SETTINGS:
            try:
                result = self._skinRpc(
                    'Settings.GetSettingValue', setting=setting)
            except SkinAdapterError:
                # Kodi platforms may omit an individual appearance setting.
                # Preserve every value Kodi does expose without inventing one.
                continue
            if isinstance(result, dict) and result.get('value') is not None:
                values[setting] = result['value']
        return values

    def _captureSkinConfigGroup(self):
        if xbmc.getSkinDir() != AF3_ID:
            raise SkinAdapterError(
                'Arctic Fuse 3 must be active to capture its live settings')
        profile = xbmcvfs.translatePath('special://profile/')
        snapshot = capture_af3_snapshot(
            profile,
            self._skinRpc,
            source_device=xbmc.getInfoLabel('System.FriendlyName'),
            source_profile=xbmc.getInfoLabel('System.ProfileName'),
            skin_version=xbmcaddon.Addon(AF3_ID).getAddonInfo('version'),
            helper_version=xbmcaddon.Addon(
                SKIN_VARIABLES_ID).getAddonInfo('version'),
            settle=lambda: xbmc.sleep(2000),
            appearance_call=self._skinAppearance,
        )
        owned_paths = managed_source_paths(snapshot['files'])
        stage_root = os.path.abspath(os.path.join(
            xbmcvfs.translatePath(utils.data_dir()), 'skin-staging'))
        data_root = os.path.abspath(xbmcvfs.translatePath(utils.data_dir()))
        if os.path.dirname(stage_root) != data_root:
            raise SkinAdapterError('invalid skin staging path')
        if os.path.lexists(stage_root):
            if os.path.islink(stage_root) or not os.path.isdir(stage_root):
                raise SkinAdapterError('skin staging path is not a safe directory')
            shutil.rmtree(stage_root)
        os.makedirs(stage_root)
        self._skin_stage_path = stage_root

        for relative, data in snapshot['files'].items():
            destination = os.path.abspath(os.path.join(stage_root, relative))
            if os.path.commonpath((stage_root, destination)) != stage_root:
                raise SkinAdapterError('skin staging path escaped its root')
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with open(destination, 'wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())

        self._skin_snapshot_metadata = snapshot['metadata']
        self._skin_managed_exclusions = [{
            'type': 'exclude',
            'path': xbmcvfs.translatePath('special://profile/' + relative),
            'adapter': AF3_ID,
            'reason': 'Captured by the authoritative AF3 configuration adapter',
        } for relative in owned_paths]
        group = self._addBackupDir('skin_config', stage_root, [{
            'type': 'include', 'path': stage_root, 'recurse': True,
        }])
        group['restore_path'] = 'special://profile/'
        return group

    def _cleanupSkinStage(self):
        stage_root = getattr(self, '_skin_stage_path', None)
        if not stage_root:
            return
        try:
            data_root = os.path.abspath(xbmcvfs.translatePath(utils.data_dir()))
            expected = os.path.join(data_root, 'skin-staging')
            if os.path.abspath(stage_root) != expected or os.path.islink(stage_root):
                utils.log('Refusing unsafe skin-stage cleanup', xbmc.LOGWARNING)
                return
            if os.path.isdir(stage_root):
                shutil.rmtree(stage_root)
        finally:
            self._skin_stage_path = None

    def _automaticExclusions(self):
        if self._automatic_exclusion_rules is not None:
            return self._automatic_exclusion_rules

        self._automatic_exclusion_rules = []
        self._automatic_exclusion_rules.extend(
            getattr(self, '_skin_managed_exclusions', []))
        if not utils.getSettingBool('exclude_tmdbh_image_cache'):
            return self._automatic_exclusion_rules

        try:
            addon = xbmcaddon.Addon(TMDB_HELPER_ID)
            try:
                configured = addon.getSettingString('image_location').strip()
            except AttributeError:
                configured = addon.getSetting('image_location').strip()
        except Exception as error:
            utils.log('TMDb Helper cache adapter unavailable: %s' % error,
                      xbmc.LOGWARNING)
            return self._automatic_exclusion_rules

        location = configured or (
            'special://profile/addon_data/%s' % TMDB_HELPER_ID)
        translated = xbmcvfs.translatePath(location)
        self._automatic_exclusion_rules.extend(
            tmdb_helper_cache_exclusions(translated))
        return self._automatic_exclusion_rules

    def _dateFormat(self, dirName):
        # create date_time object from foldername YYYYMMDDHHmm
        date_time = datetime(int(dirName[0:4]), int(dirName[4:6]), int(dirName[6:8]), int(dirName[8:10]), int(dirName[10:12]))

        # format the string based on region settings
        result = utils.getRegionalTimestamp(date_time, ['dateshort', 'time'])

        return result

    def _updateProgress(self, message=None):
        self.progressBar.updateProgress(int((float(self.transferSize - self.transferLeft) / float(self.transferSize)) * 100), message)

    def _rotateBackups(self):
        total_backups = utils.getSettingInt('backup_rotation')

        if(total_backups > 0):
            # get a list of valid backup folders
            dirs = self.listBackups(reverse=False)

            if(len(dirs) > total_backups):
                # remove backups to equal total wanted
                remove_num = 0

                # update the progress bar if it is available
                while(remove_num < (len(dirs) - total_backups)):
                    if self.progressBar.checkCancel():
                        utils.log('Backup retention cancelled', xbmc.LOGWARNING)
                        return False
                    self._updateProgress(utils.getString(30054) + " " + dirs[remove_num][1])
                    utils.log("Removing backup " + dirs[remove_num][0])

                    if(dirs[remove_num][0].split('.')[-1] == 'zip'):
                        # this is a file, remove it that way
                        removed = self.remote_vfs.rmfile(self.remote_vfs.clean_path(self.remote_base_path) + dirs[remove_num][0])
                    else:
                        removed = self.remote_vfs.rmdir(self.remote_vfs.clean_path(self.remote_base_path) + dirs[remove_num][0] + "/")

                    if not removed:
                        utils.log('Backup retention deletion failed',
                                  xbmc.LOGWARNING)
                        return False

                    remove_num = remove_num + 1
        return True

    def _finalizeBackup(self, success, artifact_path, compressed):
        if success:
            self._active_artifact = None
            if self._rotateBackups() is False:
                utils.showNotification(utils.getString(30092))
                return False
            return True
        if artifact_path:
            if compressed:
                self.remote_vfs.rmfile(artifact_path)
            else:
                self.remote_vfs.rmdir(artifact_path)
        self._active_artifact = None
        utils.showNotification(utils.getString(30092))
        return False

    def _discardActiveArtifact(self):
        artifact = getattr(self, '_active_artifact', None)
        if not artifact or getattr(self, 'remote_vfs', None) is None:
            return
        try:
            if getattr(self, '_active_artifact_compressed', False):
                self.remote_vfs.rmfile(artifact)
            else:
                self.remote_vfs.rmdir(artifact)
        except Exception as error:
            utils.log('Unable to remove failed backup artifact: %s' % error,
                      xbmc.LOGWARNING)
        finally:
            self._active_artifact = None

    def _createValidationFile(self, dirList):
        gui_settings = GuiSettingsManager()
        metadata = {
            "name": "Backup Pro Manifest",
            "created_utc": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            "addon_id": utils.__addon_id__,
            "addon_version": xbmcaddon.Addon(
                utils.__addon_id__).getAddonInfo('version'),
            "kodi_version": xbmc.getInfoLabel('System.BuildVersion'),
            "type": 0,
            "system_settings": gui_settings.backup(),
            "addons": gui_settings.list_addons(),
            "plan": self.backup_plan,
        }
        if self._skin_snapshot_metadata is not None:
            metadata['skin_config'] = self._skin_snapshot_metadata
        valInfo = build_manifest(dirList, lambda path: self._hashFile(
            path, cancellable=True), metadata,
            check_cancel=self.progressBar.checkCancel)
        self.backup_manifest = valInfo

        local_manifest = xbmcvfs.translatePath(
            utils.data_dir() + MANIFEST_NAME)
        vFile = xbmcvfs.File(local_manifest, 'w')
        vFile.write(json.dumps(valInfo, sort_keys=True, separators=(',', ':')))
        vFile.write("")
        vFile.close()
        self.backup_manifest_file_hash = self._hashFile(local_manifest)

        success = self._copyFile(
            self.xbmc_vfs, self.remote_vfs, local_manifest,
            self.remote_vfs.root_path + MANIFEST_NAME)

        # remove the validation file
        xbmcvfs.delete(local_manifest)

        if(success):
            # android requires a .nomedia file to not index the directory as media
            if(not xbmcvfs.exists(xbmcvfs.translatePath(utils.data_dir() + ".nomedia"))):
                nmFile = xbmcvfs.File(xbmcvfs.translatePath(utils.data_dir() + ".nomedia"), 'w')
                nmFile.close()

            success = self._copyFile(self.xbmc_vfs, self.remote_vfs, xbmcvfs.translatePath(utils.data_dir() + ".nomedia"), self.remote_vfs.root_path + ".nomedia")

        return success

    def _checkValidationFile(self, path):
        result = None
        restore_manifest = xbmcvfs.translatePath(
            utils.data_dir() + "backup-pro.restore-manifest.json")

        try:
            copied = self._copyFile(
                self.remote_vfs, self.xbmc_vfs, path + MANIFEST_NAME,
                restore_manifest)
            if not copied:
                utils.log('Backup Pro manifest could not be read',
                          xbmc.LOGWARNING)
                return None
            with xbmcvfs.File(restore_manifest, 'r') as vFile:
                jsonString = vFile.read()
        except Exception as error:
            utils.log('Backup Pro manifest read failed: %s' % error,
                      xbmc.LOGWARNING)
            return None
        finally:
            if xbmcvfs.exists(restore_manifest):
                xbmcvfs.delete(restore_manifest)

        try:
            result = load_manifest(jsonString)

            if(xbmc.getInfoLabel('System.BuildVersion') != result['kodi_version']):
                shouldContinue = xbmcgui.Dialog().yesno(utils.getString(30085), "%s\n%s" % (utils.getString(30086), utils.getString(30044)))

                if(not shouldContinue):
                    result = None

        except (ArchiveValidationError, KeyError) as error:
            utils.log('Backup Pro manifest rejected: %s' % error,
                      xbmc.LOGWARNING)
            result = None

        return result

    def _hashFile(self, path, cancellable=False):
        with xbmcvfs.File(xbmcvfs.translatePath(path), 'r') as source:
            check_cancel = (self.progressBar.checkCancel
                            if cancellable else None)
            return sha256_reader(
                source.readBytes, check_cancel=check_cancel)

    def _hashVfsFile(self, vfs, path, cancellable=False):
        if not isinstance(vfs, DropboxFileSystem):
            return self._hashFile(path, cancellable=cancellable)

        local_copy = xbmcvfs.translatePath(
            utils.data_dir() + 'backup-pro.readback.tmp')
        try:
            if xbmcvfs.exists(local_copy):
                xbmcvfs.delete(local_copy)
            if not vfs.get_file(path, local_copy):
                raise IOError('remote download failed')
            if cancellable and self.progressBar.checkCancel():
                raise ArchiveValidationError('remote read-back cancelled')
            return self._hashFile(local_copy, cancellable=cancellable)
        finally:
            if xbmcvfs.exists(local_copy):
                xbmcvfs.delete(local_copy)

    def _verifyFolderBackup(self, backup_root):
        root = self.remote_vfs.clean_path(backup_root)
        try:
            manifest_hash = self._hashVfsFile(
                self.remote_vfs, root + MANIFEST_NAME, cancellable=True)
        except Exception as error:
            raise ArchiveValidationError(
                'unable to read back published manifest: %s' % error)
        if manifest_hash != self.backup_manifest_file_hash:
            raise ArchiveValidationError(
                'published manifest does not match the local manifest')
        result = verify_manifest_files(
            self.backup_manifest,
            lambda relative: self._hashVfsFile(
                self.remote_vfs, root + relative, cancellable=True),
            check_cancel=self.progressBar.checkCancel)
        utils.log('Verified folder backup: %s' % json.dumps(result))
        return result

    def _verifyCompressedUpload(self, local_zip, remote_zip):
        try:
            local_checksum, local_size = self._hashFile(
                local_zip, cancellable=True)
            remote_checksum, remote_size = self._hashVfsFile(
                self.remote_vfs, remote_zip, cancellable=True)
        except Exception as error:
            raise ArchiveValidationError(
                'unable to read back uploaded ZIP: %s' % error)
        if remote_size != local_size or remote_checksum != local_checksum:
            raise ArchiveValidationError(
                'uploaded ZIP does not match the verified local archive')
        result = {'file_count': 1, 'total_bytes': local_size}
        utils.log('Verified compressed backup upload: %s' % json.dumps(
            result, sort_keys=True))
        return result

    def _createResumeBackupFile(self):
        with xbmcvfs.File(xbmcvfs.translatePath(utils.data_dir() + "resume.txt"), 'w') as f:
            f.write(self.restore_point)

    def _readBackupConfig(self, aFile):
        with xbmcvfs.File(xbmcvfs.translatePath(aFile), 'r') as f:
            jsonString = f.read()
        return json.loads(jsonString)


class FileManager(FilePlanner):
    def __init__(self, vfs):
        FilePlanner.__init__(
            self,
            vfs,
            translate=xbmcvfs.translatePath,
            validate=xbmcvfs.validatePath,
            logger=utils.log,
            verbose=utils.getSettingBool('verbose_logging'),
        )

    def fileSize(self):
        # Preserve upstream's non-zero progress denominator for empty sets.
        return max(1.0, FilePlanner.fileSize(self))
