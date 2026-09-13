from __future__ import unicode_literals

import hashlib
import json
import os
import time
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs


ADDON_ID = 'script.backup.pro'
MARKER_SCHEMA = 1
# Kodi 21.3 tvOS persists userdata XML through its supported VFS/
# NSUserDefaults bridge. Non-XML userdata is cache-backed and is therefore not
# suitable for the across-restart session marker.
MARKER_NAME = 'settings-safety-session.xml'
POLL_INTERVAL_SECONDS = 1.0

UNSAFE_PROPERTY = ADDON_ID + '.settings_unsafe'
UNSAFE_REASON_PROPERTY = ADDON_ID + '.settings_unsafe_reason'
SETTINGS_SIGNATURE_PROPERTY = ADDON_ID + '.settings_signature'
INVENTORY_SIGNATURE_PROPERTY = ADDON_ID + '.addon_inventory_signature'
SETTINGS_EDIT_PROPERTY = ADDON_ID + '.settings_edit_active'
SETTINGS_DIAGNOSTIC_PROPERTY = ADDON_ID + '.settings_diagnostic_state'

# Values included here are operational choices, never credentials or paths.
_BOOL_SETTINGS = (
    'always_prompt_restore_settings',
    'backup_addon_data',
    'backup_addons',
    'backup_config',
    'backup_database',
    'backup_game_saves',
    'backup_playlists',
    'backup_profiles',
    'backup_skin_config',
    'backup_thumbnails',
    'compress_backups',
    'cron_shutdown',
    'enable_scheduler',
    'exclude_tmdbh_image_cache',
    'schedule_miss',
    'verbose_logging',
)
_INT_SETTINGS = (
    'backup_rotation',
    'backup_selection_type',
    'progress_mode',
    'remote_selection',
    'schedule_interval',
)
_STRING_SETTINGS = (
    'backup_suffix',
    'cron_schedule',
    'day_of_week',
    'schedule_time',
)
# Only presence is represented for these values. Their contents never enter a
# signature, window property, marker, or log message.
_PRESENCE_SETTINGS = (
    'dropbox_key',
    'dropbox_secret',
    'remote_path',
    'remote_path_2',
    'zip_temp_path',
)
_SIMPLE_SELECTION_SETTINGS = (
    'backup_addons',
    'backup_addon_data',
    'backup_database',
    'backup_game_saves',
    'backup_playlists',
    'backup_profiles',
    'backup_thumbnails',
    'backup_config',
    'backup_skin_config',
)


class SettingsSafetyError(Exception):
    pass


def _digest(value):
    document = json.dumps(
        value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(document.encode('utf-8')).hexdigest()


def normalized_settings_signature(addon):
    """Return an opaque digest of non-sensitive Backup Pro settings."""
    values = []
    for setting_id in _BOOL_SETTINGS:
        values.append(('bool', setting_id,
                       bool(addon.getSettingBool(setting_id))))
    for setting_id in _INT_SETTINGS:
        values.append(('int', setting_id,
                       int(addon.getSettingInt(setting_id))))
    for setting_id in _STRING_SETTINGS:
        values.append(('string', setting_id,
                       addon.getSetting(setting_id).strip()))
    for setting_id in _PRESENCE_SETTINGS:
        values.append(('configured', setting_id,
                       bool(addon.getSetting(setting_id).strip())))
    return _digest(values)


def diagnostic_settings_state(addon):
    """Return only non-sensitive setting fields suitable for mismatch logs.

    This deliberately excludes paths, credentials, and free-form strings. It
    exists solely to identify a stale/default settings view by field name.
    """
    values = []
    for setting_id in _BOOL_SETTINGS:
        values.append((setting_id, bool(addon.getSettingBool(setting_id))))
    for setting_id in _INT_SETTINGS:
        values.append((setting_id, int(addon.getSettingInt(setting_id))))
    for setting_id in _PRESENCE_SETTINGS:
        values.append((setting_id + '_configured',
                       bool(addon.getSetting(setting_id).strip())))
    return tuple(values)


def simple_selection_state(diagnostic):
    """Keep only explicitly approved boolean selection fields for logging."""
    values = dict(diagnostic or ())
    return tuple((name, bool(values.get(name, False)))
                 for name in _SIMPLE_SELECTION_SETTINGS)


def normalized_inventory_signature(response_text):
    """Return a stable digest of Kodi's supported installed-addon view."""
    try:
        response = json.loads(response_text)
        addons = response['result']['addons']
    except (KeyError, TypeError, ValueError) as exc:
        raise SettingsSafetyError(
            'Addons.GetAddons returned an invalid response') from exc
    if not isinstance(addons, list):
        raise SettingsSafetyError(
            'Addons.GetAddons did not return an addon list')

    inventory = []
    for addon in addons:
        if not isinstance(addon, dict) or not addon.get('addonid'):
            raise SettingsSafetyError(
                'Addons.GetAddons returned an invalid addon entry')
        inventory.append((
            str(addon['addonid']),
            str(addon.get('version', '')),
            bool(addon.get('enabled', False)),
            bool(addon.get('installed', False)),
        ))
    return _digest(sorted(inventory))


def marker_decision(marker, version, pid):
    """Classify the current process without consulting any settings."""
    if marker is None:
        return 'bootstrap'
    if (not isinstance(marker, dict)
            or set(marker) != {'schema', 'version', 'pid'}
            or type(marker.get('schema')) is not int
            or marker.get('schema') != MARKER_SCHEMA
            or not isinstance(marker.get('version'), str)
            or type(marker.get('pid')) is not int):
        return 'invalid'
    if marker['pid'] != pid:
        return 'restart'
    if marker['version'] != version:
        return 'live_update'
    return 'same_session'


class KodiSettingsSafetyHost:
    def __init__(self):
        self.window = xbmcgui.Window(10000)

    def is_tvos(self):
        return bool(xbmc.getCondVisibility('System.Platform.TVOS'))

    def pid(self):
        return os.getpid()

    def addon(self):
        # Intentionally fresh: do not retain a CAddon wrapper across manager
        # reloads in the long-running service interpreter.
        return xbmcaddon.Addon(ADDON_ID)

    def version(self):
        return self.addon().getAddonInfo('version')

    def marker_path(self):
        profile = self.addon().getAddonInfo('profile').rstrip('/\\')
        if not profile or profile.rsplit('/', 1)[-1] != ADDON_ID:
            raise SettingsSafetyError(
                'unexpected Backup Pro profile path')
        return profile + '/' + MARKER_NAME

    def read_marker(self):
        path = self.marker_path()
        if not xbmcvfs.exists(path):
            return None
        handle = xbmcvfs.File(path, 'r')
        try:
            contents = handle.read()
        finally:
            handle.close()
        try:
            root = ET.fromstring(contents)
            if root.tag != 'settings-safety' or set(root.attrib) != {
                    'schema', 'version', 'pid'}:
                raise ValueError('unexpected marker document')
            return {
                'schema': int(root.attrib['schema']),
                'version': root.attrib['version'],
                'pid': int(root.attrib['pid']),
            }
        except (ET.ParseError, KeyError, TypeError, ValueError):
            return {'invalid': True}

    def write_marker(self, marker):
        path = self.marker_path()
        profile = path.rsplit('/', 1)[0]
        if not xbmcvfs.exists(profile) and not xbmcvfs.mkdirs(profile):
            raise SettingsSafetyError(
                'could not create the settings-safety state directory')
        handle = xbmcvfs.File(path, 'w')
        try:
            document = ET.Element('settings-safety', {
                'schema': str(marker['schema']),
                'version': marker['version'],
                'pid': str(marker['pid']),
            })
            written = handle.write(ET.tostring(
                document, encoding='unicode', short_empty_elements=True))
        finally:
            handle.close()
        if written is False:
            raise SettingsSafetyError(
                'could not write the settings-safety session marker')

    def property_get(self, name):
        return self.window.getProperty(name)

    def property_set(self, name, value):
        self.window.setProperty(name, value)

    def property_clear(self, name):
        self.window.clearProperty(name)

    def settings_signature(self):
        return normalized_settings_signature(self.addon())

    def settings_observation(self):
        addon = self.addon()
        return (normalized_settings_signature(addon),
                diagnostic_settings_state(addon))

    def inventory_signature(self):
        request = {
            'jsonrpc': '2.0',
            'id': 'backup-pro-settings-safety',
            'method': 'Addons.GetAddons',
            'params': {
                'enabled': 'all',
                'installed': True,
                'properties': ['version', 'enabled', 'installed'],
            },
        }
        return normalized_inventory_signature(
            xbmc.executeJSONRPC(json.dumps(request)))

    def log(self, message, level=xbmc.LOGWARNING):
        xbmc.log(ADDON_ID + ' settings safety: ' + message, level=level)

    def monotonic(self):
        return time.monotonic()


class TvOSSettingsGuard:
    """Fail closed when tvOS may be exposing stale add-on settings."""

    def __init__(self, host=None, poll_interval=POLL_INTERVAL_SECONDS):
        self.host = host or KodiSettingsSafetyHost()
        self.poll_interval = poll_interval
        self._last_poll = None
        self._active = self.host.is_tvos()
        self._last_comparison_performed = False
        self._last_changed_fields = []

    def is_unsafe(self):
        return (self._active
                and self.host.property_get(UNSAFE_PROPERTY) == '1')

    def _mark_unsafe(self, reason):
        self.host.property_set(UNSAFE_PROPERTY, '1')
        self.host.property_set(UNSAFE_REASON_PROPERTY, reason)
        self.host.log('restart required; reason=' + reason)
        return False

    def _clear_process_state(self):
        for name in (
                UNSAFE_PROPERTY, UNSAFE_REASON_PROPERTY,
                SETTINGS_SIGNATURE_PROPERTY, INVENTORY_SIGNATURE_PROPERTY,
                SETTINGS_EDIT_PROPERTY, SETTINGS_DIAGNOSTIC_PROPERTY):
            self.host.property_clear(name)

    def _write_current_marker(self):
        self.host.write_marker({
            'schema': MARKER_SCHEMA,
            'version': self.host.version(),
            'pid': self.host.pid(),
        })

    def initialize(self):
        if not self._active:
            return True
        if self.is_unsafe():
            return False

        version = self.host.version()
        pid = self.host.pid()
        try:
            marker = self.host.read_marker()
            decision = marker_decision(marker, version, pid)
            if decision in ('bootstrap', 'invalid'):
                # Kodi exposes no supported install-vs-update signal to
                # Python. The first marker-aware tvOS release therefore
                # establishes its marker without reading or writing settings,
                # then requires one restart before it trusts a baseline.
                self._write_current_marker()
                return self._mark_unsafe(decision)
            if decision == 'live_update':
                self._write_current_marker()
                return self._mark_unsafe(decision)
            if decision == 'restart':
                self._clear_process_state()
                self._write_current_marker()
            return self._ensure_baselines()
        except Exception as exc:
            self.host.log(
                'could not establish safe session: %s: %s' % (
                    type(exc).__name__, exc))
            return self._mark_unsafe('initialization_failed')

    def _ensure_baselines(self):
        if not self.host.property_get(SETTINGS_SIGNATURE_PROPERTY):
            signature, diagnostic = self._settings_observation()
            self.host.property_set(SETTINGS_SIGNATURE_PROPERTY, signature)
            self._set_diagnostic_baseline(diagnostic)
        if not self.host.property_get(INVENTORY_SIGNATURE_PROPERTY):
            self.host.property_set(
                INVENTORY_SIGNATURE_PROPERTY,
                self.host.inventory_signature())
        return True

    def _settings_observation(self):
        observe = getattr(self.host, 'settings_observation', None)
        if observe is not None:
            return observe()
        return self.host.settings_signature(), None

    def _set_diagnostic_baseline(self, diagnostic):
        if diagnostic is not None:
            self.host.property_set(
                SETTINGS_DIAGNOSTIC_PROPERTY,
                json.dumps(diagnostic, separators=(',', ':')))

    def _log_diagnostic_difference(self, diagnostic):
        if diagnostic is None:
            return
        try:
            previous = dict(json.loads(self.host.property_get(
                SETTINGS_DIAGNOSTIC_PROPERTY)))
            current = dict(diagnostic)
            changed = sorted(name for name in current
                             if current[name] != previous.get(name))
        except (TypeError, ValueError):
            changed = []
        self._last_changed_fields = changed
        if changed:
            self.host.log('settings signature fields changed: ' +
                          ','.join(changed))
        else:
            self.host.log('settings signature changed outside diagnostic fields')

    def _diagnostic_baseline(self):
        try:
            return dict(json.loads(self.host.property_get(
                SETTINGS_DIAGNOSTIC_PROPERTY)))
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _format_selection_state(state):
        return ' '.join('%s=%s' % (name, str(value).lower())
                        for name, value in state)

    def _log_operation_boundary(self, action):
        """Record a read-only fresh-wrapper selection snapshot for tvOS.

        This deliberately has no return value used by Backup Pro. It must
        never alter the fail-closed guard decision or the planner's inputs.
        """
        if not self._active:
            return
        try:
            _signature, diagnostic = self._settings_observation()
            current = simple_selection_state(diagnostic)
            baseline = self._diagnostic_baseline()
            baseline_state = tuple(
                (name, bool(baseline[name]))
                for name in _SIMPLE_SELECTION_SETTINGS if name in baseline)
            changes = []
            if baseline:
                for name, value in current:
                    if name in baseline and value != bool(baseline[name]):
                        changes.append('%s:%s->%s' % (
                            name, str(bool(baseline[name])).lower(),
                            str(value).lower()))
            match = bool(baseline) and not changes
            self.host.log(
                'operation boundary: action=%s pid=%s version=%s '
                'baseline=%s selection_baseline_match=%s selection=%s%s' % (
                    action, self.host.pid(), self.host.version(),
                    'present' if baseline else 'missing', str(match).lower(),
                    self._format_selection_state(current),
                    (' baseline_selection=%s changes=%s' % (
                        self._format_selection_state(baseline_state),
                        ','.join(changes))) if baseline else ''))
        except Exception as exc:
            self.host.log(
                'operation boundary: action=%s diagnostic_failed=%s' % (
                    action, type(exc).__name__))

    def log_operation_boundary(self, action):
        """Expose a read-only diagnostic snapshot immediately before work."""
        self._log_operation_boundary(action)

    def _log_operation_decision(self, action, allowed, unsafe_before):
        if not self._active:
            return
        if allowed:
            reason = 'allowed'
        elif unsafe_before:
            reason = 'existing_unsafe_property'
        else:
            reason = self.host.property_get(UNSAFE_REASON_PROPERTY) or 'unknown'
        existing_reason = self.host.property_get(UNSAFE_REASON_PROPERTY)
        self.host.log(
            'operation guard: action=%s result=%s reason=%s '
            'unsafe_before=%s comparison=%s%s%s' % (
                action, 'allowed' if allowed else 'restart_required', reason,
                str(unsafe_before).lower(),
                'performed' if self._last_comparison_performed else 'skipped',
                ' changed_fields=' + ','.join(self._last_changed_fields)
                if self._last_changed_fields else '',
                ' existing_reason=' + existing_reason
                if unsafe_before and existing_reason else ''))

    def poll(self, force=False):
        self._last_comparison_performed = False
        self._last_changed_fields = []
        if not self._active:
            return True
        if not self.initialize():
            return False

        now = self.host.monotonic()
        if (not force and self._last_poll is not None
                and now - self._last_poll < self.poll_interval):
            return True
        self._last_poll = now

        try:
            inventory = self.host.inventory_signature()
            if inventory != self.host.property_get(
                    INVENTORY_SIGNATURE_PROPERTY):
                # An add-on-manager mutation is a reason to distrust a
                # cached settings wrapper, not proof that this add-on's
                # settings are stale.  Re-read the non-sensitive signature
                # below through a fresh wrapper before deciding whether a
                # Kodi restart is necessary.  Accept the new inventory here
                # so ordinary repository activity cannot poison a session.
                self.host.log(
                    'addon inventory changed; revalidating settings')
                self.host.property_set(
                    INVENTORY_SIGNATURE_PROPERTY, inventory)

            if self.host.property_get(SETTINGS_EDIT_PROPERTY) != '1':
                settings, diagnostic = self._settings_observation()
                self._last_comparison_performed = True
                if settings != self.host.property_get(
                        SETTINGS_SIGNATURE_PROPERTY):
                    self._log_diagnostic_difference(diagnostic)
                    return self._mark_unsafe('settings_view_changed')
        except Exception as exc:
            self.host.log(
                'safety check failed: %s: %s' % (
                    type(exc).__name__, exc))
            return self._mark_unsafe('safety_check_failed')
        return True

    def allow_operation(self, action='operation'):
        unsafe_before = self.is_unsafe()
        allowed = self.poll(force=True)
        self._log_operation_decision(action, allowed, unsafe_before)
        return allowed

    def begin_settings_edit(self):
        if self._active:
            self.host.property_set(SETTINGS_EDIT_PROPERTY, '1')

    def finish_settings_edit(self):
        if not self._active:
            return
        self.host.property_clear(SETTINGS_EDIT_PROPERTY)
        if not self.is_unsafe():
            self.rebaseline_settings()

    def rebaseline_settings(self):
        """Accept a legitimate Kodi onSettingsChanged callback."""
        if not self._active or self.is_unsafe():
            return False
        try:
            signature, diagnostic = self._settings_observation()
            self.host.property_set(SETTINGS_SIGNATURE_PROPERTY, signature)
            self._set_diagnostic_baseline(diagnostic)
            return True
        except Exception as exc:
            self.host.log(
                'settings rebaseline failed: %s: %s' % (
                    type(exc).__name__, exc))
            return self._mark_unsafe('settings_rebaseline_failed')
