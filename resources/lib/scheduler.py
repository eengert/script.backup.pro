import time
from datetime import datetime
import xbmc
import xbmcvfs
import xbmcgui
from . import utils as utils
from resources.lib.croniter import croniter
from resources.lib.backup import XbmcBackup
from resources.lib.operation_settings import BackupOperationSettings

class BackupScheduler:
    monitor = None
    enabled = False
    next_run = 0
    next_run_path = None
    restore_point = None

    def __init__(self, settings_guard=None):
        self.settings_guard = settings_guard
        self.monitor = UpdateMonitor(update_method=self.settingsChanged)
        if (self.settings_guard is not None
                and not self.settings_guard.initialize()):
            # Keep the service alive so its process-local guard remains
            # available to Program invocations, but do not read settings or
            # resume/schedule work in an unsafe tvOS session.
            self.enabled = False
            self.next_run_path = None
            return
        self.enabled = utils.getSettingBool("enable_scheduler")
        self.next_run_path = xbmcvfs.translatePath(utils.data_dir()) + 'next_run.txt'

        # check if a backup should be resumed
        resumeRestore = self._resumeCheck()

        if(resumeRestore):
            restore = XbmcBackup()
            restore.selectRestore(self.restore_point)
            # skip the advanced settings check
            restore.skipAdvanced()
            restore.restore()

        if(self.enabled):

            # sleep for 2 minutes so Kodi can start and time can update correctly
            # Poll during the startup grace period too. A single blocking
            # 120-second wait would leave an avoidable add-on-manager blind
            # spot immediately after Kodi starts.
            for _unused in range(120):
                if self.monitor.waitForAbort(1):
                    return
                if (self.settings_guard is not None
                        and not self.settings_guard.poll(force=True)):
                    self.enabled = False
                    return

            nr = 0
            if(xbmcvfs.exists(self.next_run_path)):

                with xbmcvfs.File(self.next_run_path) as fh:
                    try:
                        # check if we saved a run time from the last run
                        nr = float(fh.read())
                    except ValueError:
                        nr = 0

            # if we missed and the user wants to play catch-up
            if(0 < nr <= time.time() and utils.getSettingBool('schedule_miss')):
                utils.log("scheduled backup was missed, doing it now...")
                progress_mode = utils.getSettingInt('progress_mode')

                if(progress_mode == 0):
                    progress_mode = 1  # Kodi just started, don't block it with a foreground progress bar

                self.doScheduledBackup(progress_mode)

            self.setup()

    def setup(self):
        # scheduler was turned on, find next run time
        utils.log("scheduler enabled, finding next run time")
        self.findNextRun(time.time())

    def start(self):

        while(not self.monitor.abortRequested()):

            blocked_reason = None
            if (self.settings_guard is not None
                    and not self.settings_guard.poll()):
                blocked_reason = self.settings_guard.unsafe_reason()
                if blocked_reason != 'live_update':
                    # Every unsafe reason other than a live update
                    # continues to fully disable the scheduler until a
                    # Kodi restart, exactly as before.
                    self.enabled = False
                    xbmc.sleep(500)
                    continue
                # A live update may still admit one due scheduled backup
                # through doScheduledBackup()'s own recovery check below.
                # self.enabled is left at its last known value, same as
                # the safe path - only that one narrow recovery path is
                # new here.

            if(self.enabled):
                # scheduler is still on
                now = time.time()

                if(self.next_run <= now):
                    if (blocked_reason == 'live_update'
                            and not self.settings_guard.scheduler_recovery_ready()):
                        # Still cooling down since the last recovery
                        # attempt - stay due, retry later without
                        # re-running (and re-logging) the check every
                        # 500ms tick.
                        xbmc.sleep(500)
                        continue

                    progress_mode = utils.getSettingInt('progress_mode')
                    attempted = self.doScheduledBackup(progress_mode)

                    if blocked_reason == 'live_update' and not attempted:
                        # Recovery was blocked; the schedule stays due and
                        # is retried at the next eligible opportunity -
                        # never consumed by a failed recovery attempt.
                        xbmc.sleep(500)
                        continue

                    # check if we should shut the computer down
                    if(utils.getSettingBool("cron_shutdown")):
                        # wait 10 seconds to make sure all backup processes and files are completed
                        time.sleep(10)
                        xbmc.executebuiltin('ShutDown()')
                    else:
                        # find the next run time like normal
                        self.findNextRun(now)

            xbmc.sleep(500)

        # delete monitor to free up memory
        del self.monitor

    def doScheduledBackup(self, progress_mode):
        """Run the due scheduled backup, if eligible.

        Returns True once a backup has actually been attempted (dispatched
        to XbmcBackup.backup()), False if admission/recovery was blocked
        before that point. The caller (start()) uses this to decide
        whether the schedule may advance - a blocked attempt must never
        consume the due schedule.
        """
        guard = getattr(self, 'settings_guard', None)
        operation_settings = None
        recovered_from_live_update = False

        if guard is not None and not guard.allow_operation('scheduler_backup'):
            operation_settings = guard.admit_scheduler_recovery_snapshot(
                BackupOperationSettings.capture)
            if operation_settings is None:
                reason = guard.last_block_reason() or 'unknown'
                if guard.recovery_attempt_was_first():
                    utils.log(
                        'scheduled backup blocked: Kodi restart required '
                        'for interactive use; scheduled recovery blocked '
                        '(reason=%s); will retry at the next eligible '
                        'opportunity' % reason, xbmc.LOGWARNING)
                    utils.showNotification(utils.getString(30237))
                elif reason != 'recovery_cooldown':
                    utils.log(
                        'scheduled backup blocked: Kodi restart required '
                        '(reason=%s)' % reason, xbmc.LOGWARNING)
                return False
            recovered_from_live_update = True
            utils.log(
                'scheduled backup recovered after a live update; '
                'interactive access still requires a Kodi restart',
                xbmc.LOGWARNING)
        elif guard is not None:
            guard.log_operation_boundary('scheduler_backup')
            operation_settings = guard.admit_backup_snapshot(
                BackupOperationSettings.capture)
            if operation_settings is None:
                utils.log('scheduled backup blocked: Kodi restart required',
                          xbmc.LOGWARNING)
                utils.showNotification(utils.getString(30237))
                return False
        else:
            # Non-tvOS has no process guard, but still captures one immutable
            # configuration for the entire scheduled backup operation.
            operation_settings = BackupOperationSettings.capture()

        effective_progress_mode = operation_settings.progress_mode
        if(effective_progress_mode != 2):
            utils.showNotification(utils.getString(30053))

        backup = XbmcBackup(
            settings_guard=guard, operation_settings=operation_settings,
            recovered_from_live_update=recovered_from_live_update)
        # background/scheduled execution must never open a recovery dialog
        # or switch skins; only log that interactive recovery is pending.
        backup.checkPendingSkinRestoreBackground()

        if(backup.remoteConfigured()):

            # The initial scheduler gate can be separated from planning by
            # recovery and destination work. Recheck immediately before the
            # backup reads its selection settings. A recovered operation
            # uses the same tolerant check backup.py's own selection
            # boundary uses (the sticky live_update reason it was already
            # validated against must not revoke it here either); every
            # other case uses the ordinary guard check unchanged.
            if recovered_from_live_update:
                if guard.operation_revoked(
                        admitted_snapshot=True,
                        recovered_from_live_update=True):
                    utils.log(
                        'scheduled backup blocked: Kodi restart required',
                        xbmc.LOGWARNING)
                    utils.showNotification(utils.getString(30237))
                    return False
            elif (guard is not None
                    and not guard.allow_operation('scheduler_backup_preplan')):
                utils.log('scheduled backup blocked: Kodi restart required',
                          xbmc.LOGWARNING)
                utils.showNotification(utils.getString(30237))
                return False
            if guard is not None:
                guard.log_operation_boundary('scheduler_backup_preplan')

            if(effective_progress_mode in [0, 1]):
                backup.backup(True)
            else:
                backup.backup(False)

            # check if this is a "one-off"
            if(operation_settings.schedule_interval == 0):
                # disable the scheduler after this run
                self.enabled = False
                utils.setSetting('enable_scheduler', 'false')

            return True
        else:
            utils.showNotification(utils.getString(30045))
            return False

    def findNextRun(self, now):
        progress_mode = utils.getSettingInt('progress_mode')

        # find the cron expression and get the next run time
        cron_exp = self.parseSchedule()

        cron_ob = croniter(cron_exp, datetime.fromtimestamp(now))
        new_run_time = cron_ob.get_next(float)

        if(new_run_time != self.next_run):
            self.next_run = new_run_time
            utils.log("scheduler will run again on " + utils.getRegionalTimestamp(datetime.fromtimestamp(self.next_run), ['dateshort', 'time']))

            # write the next time to a file
            with xbmcvfs.File(self.next_run_path, 'w') as fh:
                fh.write(str(self.next_run))

            # only show when not in silent mode
            if(progress_mode != 2):
                utils.showNotification(utils.getString(30081) + " " + utils.getRegionalTimestamp(datetime.fromtimestamp(self.next_run), ['dateshort', 'time']))

    def settingsChanged(self):
        guard = getattr(self, 'settings_guard', None)
        if (guard is not None
                and not guard.rebaseline_settings()):
            return
        current_enabled = utils.getSettingBool("enable_scheduler")

        if(current_enabled and not self.enabled):
            # scheduler was just turned on
            self.enabled = current_enabled
            self.setup()
        elif (not current_enabled and self.enabled):
            # schedule was turn off
            self.enabled = current_enabled

        if(self.enabled):
            # always recheck the next run time after an update
            self.findNextRun(time.time())

    def parseSchedule(self):
        schedule_type = utils.getSettingInt("schedule_interval")
        cron_exp = utils.getSetting("cron_schedule")

        hour_of_day = utils.getSetting("schedule_time")
        hour_of_day = int(hour_of_day[0:2])
        if(schedule_type == 0 or schedule_type == 1):
            # every day
            cron_exp = "0 " + str(hour_of_day) + " * * *"
        elif(schedule_type == 2):
            # once a week
            day_of_week = utils.getSetting("day_of_week")
            cron_exp = "0 " + str(hour_of_day) + " * * " + day_of_week
        elif(schedule_type == 3):
            # first day of month
            cron_exp = "0 " + str(hour_of_day) + " 1 * *"

        return cron_exp

    def _resumeCheck(self):
        shouldContinue = False
        if(xbmcvfs.exists(xbmcvfs.translatePath(utils.data_dir() + "resume.txt"))):
            rFile = xbmcvfs.File(xbmcvfs.translatePath(utils.data_dir() + "resume.txt"), 'r')
            self.restore_point = rFile.read()
            rFile.close()
            xbmcvfs.delete(xbmcvfs.translatePath(utils.data_dir() + "resume.txt"))
            shouldContinue = xbmcgui.Dialog().yesno(utils.getString(30042), "%s\n%s" % (utils.getString(30043), utils.getString(30044)))

        return shouldContinue


class UpdateMonitor(xbmc.Monitor):
    update_method = None

    def __init__(self, *args, **kwargs):
        xbmc.Monitor.__init__(self)
        self.update_method = kwargs['update_method']

    def onSettingsChanged(self):
        self.update_method()
