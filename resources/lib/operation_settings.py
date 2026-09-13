"""Immutable, per-backup Kodi settings captured before slow operation work."""
from __future__ import unicode_literals

from dataclasses import dataclass, field

import xbmcaddon
import xbmcvfs

from .tvos_settings_guard import normalized_settings_signature_from_values


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
