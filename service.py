from resources.lib.scheduler import BackupScheduler
from resources.lib.tvos_settings_guard import TvOSSettingsGuard

# start the backup scheduler
BackupScheduler(settings_guard=TvOSSettingsGuard()).start()
