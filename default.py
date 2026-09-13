import xbmcgui
import xbmcvfs
import resources.lib.utils as utils
from resources.lib.backup import XbmcBackup
from resources.lib.tvos_settings_guard import TvOSSettingsGuard
from resources.lib.authorizers import DropboxAuthorizer
from resources.lib.advanced_editor import AdvancedBackupEditor
from resources.lib.help_content import build_help_text

# mode constants
BACKUP = 0
RESTORE = 1
SETTINGS = 2
ADVANCED_EDITOR = 3
LAUNCHER = 4
HELP = 5
STATUS = 6

# maps buildStatusReport()'s label keys to their localized string ids
STATUS_LABEL_STRING_IDS = {
    'last_backup_known': 30175,
    'last_backup_unknown': 30176,
    'remote_configured': 30177,
    'remote_not_configured': 30178,
    'recovery_pending': 30179,
    'recovery_clear': 30180,
    'scheduler_enabled_next': 30181,
    'scheduler_enabled_unknown': 30182,
    'scheduler_disabled': 30183,
}


def show_help():
    xbmcgui.Dialog().textviewer(
        utils.getString(30010), build_help_text(utils.getString))


def show_status(active_backup):
    # buildStatusReport() is read-only and local-only (no network access,
    # no mutation) - safe to call every time Status is opened
    report = active_backup.buildStatusReport()
    lines = []
    for label_key, detail in report:
        label = utils.getString(STATUS_LABEL_STRING_IDS[label_key])
        lines.append('%s %s' % (label, detail) if detail else label)
    xbmcgui.Dialog().textviewer(
        utils.getString(30010) + " - " + utils.getString(30174),
        '\n'.join(lines))


def authorize_cloud(cloudProvider):
    # dropbox
    if(cloudProvider == 'dropbox'):
        authorizer = DropboxAuthorizer()

        if(authorizer.authorize()):
            xbmcgui.Dialog().ok(utils.getString(30010), '%s %s' % (utils.getString(30027), utils.getString(30106)))
        else:
            xbmcgui.Dialog().ok(utils.getString(30010), '%s %s' % (utils.getString(30107), utils.getString(30027)))


def remove_auth():
    # triggered from settings.xml - asks if user wants to delete OAuth token information
    shouldDelete = xbmcgui.Dialog().yesno(utils.getString(30093), '%s\n%s' % (utils.getString(30094), utils.getString(30095)), autoclose=7000)

    if(shouldDelete):
        # delete any of the known token file types
        xbmcvfs.delete(xbmcvfs.translatePath(utils.data_dir() + "tokens.txt"))  # dropbox
        xbmcvfs.delete(xbmcvfs.translatePath(utils.data_dir() + "google_drive.dat"))  # google drive


def get_params():
    param = {}
    try:
        for i in sys.argv:
            args = i
            if('=' in args):
                if(args.startswith('?')):
                    args = args[1:]  # legacy in case of url params
                splitString = args.split('=')
                utils.log(splitString[1])
                param[splitString[0]] = splitString[1]
    except:
        pass

    return param


# the program mode
mode = -1
params = get_params()

if("mode" in params):
    if(params['mode'] == 'backup'):
        mode = BACKUP
    elif(params['mode'] == 'restore'):
        mode = RESTORE
    elif(params['mode'] == 'launcher'):
        mode = LAUNCHER

# On tvOS, Kodi can expose stale/default add-on settings after any live
# add-on-manager mutation. Gate before XbmcBackup construction because its
# constructor reads destination and staging settings immediately.
settingsGuard = TvOSSettingsGuard()
requested_action = {
    BACKUP: 'manual_backup',
    RESTORE: 'restore',
}.get(mode, 'program_open')
if not settingsGuard.allow_operation(requested_action):
    xbmcgui.Dialog().ok(utils.getString(30010), utils.getString(30237))
    raise SystemExit(0)

# a pending Arctic Fuse 3 restore takes precedence over every other Program
# action, including reading which mode was requested. If it is resolved (or
# never existed), fall through to normal Program behavior below.
backup = XbmcBackup()
skinRecoveryPending = backup.resolvePendingSkinRestore()

# if mode wasn't passed in as arg, get from user
if(skinRecoveryPending):
    mode = -1
elif(mode == -1):
    # by default, Backup,Restore,Open Settings
    menu_items = [(BACKUP, utils.getString(30016)), (RESTORE, utils.getString(30017)), (SETTINGS, utils.getString(30099))]

    # find out if we're using the advanced editor
    if(utils.getSettingInt('backup_selection_type') == 1):
        menu_items.append((ADVANCED_EDITOR, utils.getString(30125)))

    menu_items.append((HELP, utils.getString(30173)))
    menu_items.append((STATUS, utils.getString(30174)))

    # figure out if this is a backup or a restore from the user
    selected = xbmcgui.Dialog().select(utils.getString(30010) + " - " + utils.getString(30023), [label for _, label in menu_items])
    mode = menu_items[selected][0] if selected != -1 else -1

# check which mode should be run
if(mode != -1):

    action_name = {
        BACKUP: 'manual_backup',
        RESTORE: 'restore',
        SETTINGS: 'open_settings',
        ADVANCED_EDITOR: 'advanced_editor',
        LAUNCHER: 'launcher',
        STATUS: 'status',
    }.get(mode, 'help')
    # Read-only diagnostic snapshot immediately before the selected action.
    # It does not create another guard gate or alter its decision.
    settingsGuard.log_operation_boundary(action_name)

    if(mode == SETTINGS):
        # open the settings dialog
        settingsGuard.begin_settings_edit()
        try:
            utils.openSettings()
        finally:
            settingsGuard.finish_settings_edit()
    elif(mode == ADVANCED_EDITOR and utils.getSettingInt('backup_selection_type') == 1):
        # open the advanced editor but only if in advanced mode
        editor = AdvancedBackupEditor()
        editor.showMainScreen()
    elif(mode == HELP):
        show_help()
    elif(mode == STATUS):
        show_status(backup)
    elif(mode == LAUNCHER):
        # copied from old launcher.py
        if(params['action'] == 'authorize_cloud'):
            authorize_cloud(params['provider'])
        elif(params['action'] == 'remove_auth'):
            remove_auth()
        elif(params['action'] == 'advanced_editor'):
            editor = AdvancedBackupEditor()
            editor.showMainScreen()
        elif(params['action'] == 'advanced_copy_config'):
            editor = AdvancedBackupEditor()
            editor.copySimpleConfig()

    elif(mode == BACKUP or mode == RESTORE):
        # if mode was RESTORE
        if(mode == RESTORE and backup.remoteConfigured()):
            # get list of valid restore points
            restorePoints = backup.listBackups()
            pointNames = []
            folderNames = []

            for aDir in restorePoints:
                pointNames.append(aDir[1])
                folderNames.append(aDir[0])

            selectedRestore = -1

            if("archive" in params):
                # check that the user give archive exists
                if(params['archive'] in folderNames):
                    # set the index
                    selectedRestore = folderNames.index(params['archive'])
                    utils.log(str(selectedRestore) + " : " + params['archive'])
                else:
                    utils.showNotification(utils.getString(30045))
                    utils.log(params['archive'] + ' is not a valid restore point')
            else:
                # allow user to select the backup to restore from
                selectedRestore = xbmcgui.Dialog().select(utils.getString(30010) + " - " + utils.getString(30021), pointNames)

            if(selectedRestore != -1):
                if not settingsGuard.allow_operation('restore_preplan'):
                    xbmcgui.Dialog().ok(utils.getString(30010),
                                        utils.getString(30237))
                else:
                    settingsGuard.log_operation_boundary('restore_preplan')
                    backup.selectRestore(restorePoints[selectedRestore][0])
                    if('sets' in params):
                        backup.restore(selectedSets=params['sets'].split('|'))
                    else:
                        backup.restore()
        elif(mode == BACKUP and backup.remoteConfigured()):
            # mode was BACKUP
            # The user can spend time in the confirmation dialog after the
            # entry gate. Recheck immediately before planner settings reads.
            if not settingsGuard.allow_operation('manual_backup_preplan'):
                xbmcgui.Dialog().ok(utils.getString(30010),
                                    utils.getString(30237))
            else:
                settingsGuard.log_operation_boundary('manual_backup_preplan')
                backup.backup()
        else:
            # can't go any further
            xbmcgui.Dialog().ok(utils.getString(30010), utils.getString(30045))
            utils.openSettings()
    else:
        xbmcgui.Dialog().ok(utils.getString(30010), "%s %s" % (utils.getString(30159), params['mode']))
