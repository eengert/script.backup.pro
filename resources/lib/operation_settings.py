"""Immutable, per-backup Kodi settings captured before slow operation work."""
from __future__ import unicode_literals

from dataclasses import dataclass, field

import xbmcaddon
import xbmcvfs

from .tvos_settings_guard import (
    critical_scheduler_baseline_from_values,
    normalized_settings_signature_from_values)


ADDON_ID = 'script.backup.pro'
SIMPLE_SET_IDS = (
    'addons', 'addon_data', 'database', 'game_saves', 'playlists',
    'profiles', 'thumbnails', 'config',
)


@dataclass(frozen=True, repr=False)
class BackupOperationSettings:
    remote_selection: int
    remote_path: str
    remote_path_2: str
    zip_temp_path: str
    dropbox_key: str
    dropbox_secret: str
    backup_selection_type: int
    selected_sets: tuple
    backup_skin_config: bool
    advanced_paths_json: str
    compress_backups: bool
    backup_suffix: str
    backup_rotation: int
    # Non-sensitive scheduler timing fields - not consumed by the backup
    # operation itself (findNextRun() reads them live after completion, as
    # before), captured here only so scheduler live-update recovery can
    # validate them against the trusted pre-update baseline alongside the
    # rest of this snapshot's critical fields.
    cron_schedule: str
    day_of_week: str
    schedule_time: str
    exclude_tmdbh_image_cache: bool
    progress_mode: int
    verbose_logging: bool
    schedule_interval: int
    schedule_miss: bool
    cron_shutdown: bool
    enable_scheduler: bool
    safety_signature: str = field(repr=False, compare=True)

    @classmethod
    def capture(cls):
        addon = xbmcaddon.Addon(ADDON_ID)
        bool_values = {
            'backup_addons': bool(addon.getSettingBool('backup_addons')),
            'backup_addon_data': bool(addon.getSettingBool('backup_addon_data')),
            'backup_database': bool(addon.getSettingBool('backup_database')),
            'backup_game_saves': bool(addon.getSettingBool('backup_game_saves')),
            'backup_playlists': bool(addon.getSettingBool('backup_playlists')),
            'backup_profiles': bool(addon.getSettingBool('backup_profiles')),
            'backup_thumbnails': bool(addon.getSettingBool('backup_thumbnails')),
            'backup_config': bool(addon.getSettingBool('backup_config')),
            'backup_skin_config': bool(addon.getSettingBool('backup_skin_config')),
            'compress_backups': bool(addon.getSettingBool('compress_backups')),
            'exclude_tmdbh_image_cache': bool(addon.getSettingBool(
                'exclude_tmdbh_image_cache')),
            'schedule_miss': bool(addon.getSettingBool('schedule_miss')),
            'cron_shutdown': bool(addon.getSettingBool('cron_shutdown')),
            'enable_scheduler': bool(addon.getSettingBool('enable_scheduler')),
            'always_prompt_restore_settings': bool(addon.getSettingBool(
                'always_prompt_restore_settings')),
            'verbose_logging': bool(addon.getSettingBool('verbose_logging')),
        }
        int_values = {
            'backup_selection_type': int(addon.getSettingInt(
                'backup_selection_type')),
            'backup_rotation': int(addon.getSettingInt('backup_rotation')),
            'progress_mode': int(addon.getSettingInt('progress_mode')),
            'remote_selection': int(addon.getSettingInt('remote_selection')),
            'schedule_interval': int(addon.getSettingInt('schedule_interval')),
        }
        string_values = {
            'backup_suffix': addon.getSetting('backup_suffix').strip(),
            'cron_schedule': addon.getSetting('cron_schedule').strip(),
            'day_of_week': addon.getSetting('day_of_week').strip(),
            'schedule_time': addon.getSetting('schedule_time').strip(),
        }
        private = {
            'remote_path': addon.getSetting('remote_path'),
            'remote_path_2': addon.getSetting('remote_path_2'),
            'zip_temp_path': addon.getSetting('zip_temp_path'),
            'dropbox_key': addon.getSetting('dropbox_key'),
            'dropbox_secret': addon.getSetting('dropbox_secret'),
        }
        presence_values = {
            name: bool(value.strip()) for name, value in private.items()
        }
        profile = addon.getAddonInfo('profile').rstrip('/\\')
        advanced_path = profile + '/custom_paths.json'
        advanced_paths_json = ''
        if xbmcvfs.exists(advanced_path):
            with xbmcvfs.File(advanced_path, 'r') as handle:
                advanced_paths_json = handle.read()
        selected_sets = tuple(
            (name, bool_values['backup_' + name]) for name in SIMPLE_SET_IDS)
        return cls(
            remote_selection=int_values['remote_selection'],
            remote_path=private['remote_path'],
            remote_path_2=private['remote_path_2'],
            zip_temp_path=private['zip_temp_path'],
            dropbox_key=private['dropbox_key'],
            dropbox_secret=private['dropbox_secret'],
            backup_selection_type=int_values['backup_selection_type'],
            selected_sets=selected_sets,
            backup_skin_config=bool_values['backup_skin_config'],
            advanced_paths_json=advanced_paths_json,
            compress_backups=bool_values['compress_backups'],
            backup_suffix=string_values['backup_suffix'],
            backup_rotation=int_values['backup_rotation'],
            cron_schedule=string_values['cron_schedule'],
            day_of_week=string_values['day_of_week'],
            schedule_time=string_values['schedule_time'],
            exclude_tmdbh_image_cache=bool_values[
                'exclude_tmdbh_image_cache'],
            progress_mode=int_values['progress_mode'],
            verbose_logging=bool_values['verbose_logging'],
            schedule_interval=int_values['schedule_interval'],
            schedule_miss=bool_values['schedule_miss'],
            cron_shutdown=bool_values['cron_shutdown'],
            enable_scheduler=bool_values['enable_scheduler'],
            safety_signature=normalized_settings_signature_from_values(
                bool_values, int_values, string_values, presence_values))

    def selected(self, name):
        return dict(self.selected_sets)[name]

    def destination_state_valid(self):
        """Structurally valid destination for the selected remote slot.

        Mirrors XbmcBackup.configureRemote()/remoteConfigured()'s
        selection logic without constructing any Vfs/network object - used
        only to gate scheduler live-update recovery admission. The real
        remoteConfigured() check still runs afterward as it always has;
        this is a cheaper, side-effect-free pre-check on the captured
        values alone. remote_selection: 0 = primary path, 1 = secondary
        path, 2 = Dropbox (requires both key and secret - a provider-
        specific requirement path-based slots don't have). Any other
        value is not a recognized destination and is rejected.
        """
        if self.remote_selection == 0:
            return bool(self.remote_path.strip())
        if self.remote_selection == 1:
            return bool(self.remote_path_2.strip())
        if self.remote_selection == 2:
            return bool(self.dropbox_key.strip()
                       and self.dropbox_secret.strip())
        return False

    def critical_scheduler_baseline(self):
        """Per-field digest of this snapshot's execution-affecting,
        non-sensitive fields, for comparison against the trusted
        pre-update baseline during scheduler live-update recovery only.
        See tvos_settings_guard.critical_scheduler_baseline_from_values()
        for exactly which fields and why; never exposes a raw value.
        """
        selected = dict(self.selected_sets)
        bool_values = {
            'backup_addons': selected['addons'],
            'backup_addon_data': selected['addon_data'],
            'backup_database': selected['database'],
            'backup_game_saves': selected['game_saves'],
            'backup_playlists': selected['playlists'],
            'backup_profiles': selected['profiles'],
            'backup_thumbnails': selected['thumbnails'],
            'backup_config': selected['config'],
            'backup_skin_config': self.backup_skin_config,
            'compress_backups': self.compress_backups,
            'exclude_tmdbh_image_cache': self.exclude_tmdbh_image_cache,
            'enable_scheduler': self.enable_scheduler,
            'schedule_miss': self.schedule_miss,
            'cron_shutdown': self.cron_shutdown,
        }
        int_values = {
            'remote_selection': self.remote_selection,
            'backup_selection_type': self.backup_selection_type,
            'backup_rotation': self.backup_rotation,
            'schedule_interval': self.schedule_interval,
        }
        string_values = {
            'backup_suffix': self.backup_suffix,
            'cron_schedule': self.cron_schedule,
            'day_of_week': self.day_of_week,
            'schedule_time': self.schedule_time,
        }
        presence_values = {
            'dropbox_key': bool(self.dropbox_key.strip()),
            'dropbox_secret': bool(self.dropbox_secret.strip()),
            'remote_path': bool(self.remote_path.strip()),
            'remote_path_2': bool(self.remote_path_2.strip()),
            'zip_temp_path': bool(self.zip_temp_path.strip()),
        }
        return critical_scheduler_baseline_from_values(
            bool_values, int_values, string_values, presence_values)
