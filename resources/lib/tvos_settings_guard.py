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
# Minimum spacing between scheduler live-update recovery attempts. Bounds
# doScheduledBackup() retries to once per this window instead of once per
# scheduler tick (500ms), avoiding a retry storm while a due backup stays
# blocked; see admit_scheduler_recovery_snapshot().
RECOVERY_COOLDOWN_SECONDS = 30.0

UNSAFE_PROPERTY = ADDON_ID + '.settings_unsafe'
UNSAFE_REASON_PROPERTY = ADDON_ID + '.settings_unsafe_reason'
SETTINGS_SIGNATURE_PROPERTY = ADDON_ID + '.settings_signature'
INVENTORY_SIGNATURE_PROPERTY = ADDON_ID + '.addon_inventory_signature'
SETTINGS_EDIT_PROPERTY = ADDON_ID + '.settings_edit_active'
SETTINGS_DIAGNOSTIC_PROPERTY = ADDON_ID + '.settings_diagnostic_state'
SAFETY_READY_PROPERTY = ADDON_ID + '.settings_safety_ready'
# Per-field trusted baseline for exactly the settings an unattended
# scheduled backup's execution depends on - established before any live
# update the same way SETTINGS_SIGNATURE_PROPERTY is, and never cleared by
# a live_update transition (only by a genuine restart), so it remains
# available as the pre-update trusted reference during scheduler recovery.
CRITICAL_BASELINE_PROPERTY = ADDON_ID + '.settings_critical_baseline'
SCHEDULER_RECOVERY_ATTEMPT_PROPERTY = (
    ADDON_ID + '.scheduler_recovery_last_attempt')

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
# Exactly the settings that determine what a scheduled backup does and how
# it runs - reviewed and named explicitly per
# docs/TVOS_LIVE_UPDATE_SCHEDULER_RECOVERY_REVIEW.md. A field not listed
# here can never block or admit scheduler live-update recovery; a field
# listed here can never be skipped. All members must already be present in
# _BOOL_SETTINGS/_INT_SETTINGS above (checked as a module-load invariant).
_CRITICAL_BOOL_SETTINGS = _SIMPLE_SELECTION_SETTINGS + (
    'compress_backups',
    'exclude_tmdbh_image_cache',
    'enable_scheduler',
    'schedule_miss',
    'cron_shutdown',
)
_CRITICAL_INT_SETTINGS = (
    'remote_selection',
    'backup_selection_type',
    'backup_rotation',
    'schedule_interval',
)
assert set(_CRITICAL_BOOL_SETTINGS) <= set(_BOOL_SETTINGS)
assert set(_CRITICAL_INT_SETTINGS) <= set(_INT_SETTINGS)


class SettingsSafetyError(Exception):
    pass


def _digest(value):
    document = json.dumps(
        value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(document.encode('utf-8')).hexdigest()


def _captured_setting_values(addon):
    """Read every tracked setting once from one fresh Addon wrapper.

    Shared by normalized_settings_signature() (opaque whole-session digest)
    and critical_scheduler_baseline() (named per-field digest for scheduler
    live-update recovery) so both are always derived from the exact same
    single-pass read, never two separate live reads that could disagree.
    """
    return (
        {setting_id: bool(addon.getSettingBool(setting_id))
         for setting_id in _BOOL_SETTINGS},
        {setting_id: int(addon.getSettingInt(setting_id))
         for setting_id in _INT_SETTINGS},
        {setting_id: addon.getSetting(setting_id).strip()
         for setting_id in _STRING_SETTINGS},
        {setting_id: bool(addon.getSetting(setting_id).strip())
         for setting_id in _PRESENCE_SETTINGS},
    )


def normalized_settings_signature(addon):
    """Return an opaque digest of non-sensitive Backup Pro settings."""
    return normalized_settings_signature_from_values(
        *_captured_setting_values(addon))


def normalized_settings_signature_from_values(bool_values, int_values,
                                              string_values, presence_values):
    """Digest approved public state without retaining private values."""
    values = []
    for setting_id in _BOOL_SETTINGS:
        values.append(('bool', setting_id, bool(bool_values[setting_id])))
    for setting_id in _INT_SETTINGS:
        values.append(('int', setting_id, int(int_values[setting_id])))
    for setting_id in _STRING_SETTINGS:
        values.append(('string', setting_id, string_values[setting_id]))
    for setting_id in _PRESENCE_SETTINGS:
        values.append(('configured', setting_id,
                       bool(presence_values[setting_id])))
    return _digest(values)


def critical_scheduler_baseline_from_values(bool_values, int_values,
                                            string_values, presence_values):
    """Per-field digest of exactly the settings a scheduled backup's
    execution depends on: destination selection/presence, selected sets,
    selection mode, compression, cache exclusion, rotation, scheduler
    enablement and timing. Keyed by field name so a schema change to an
    UNRELATED setting can never invalidate these, and so a critical field
    this code no longer recognizes is simply absent (never silently
    treated as matching). Free-form string values (e.g. backup_suffix,
    the cron schedule fields) and private presence flags are digested,
    never returned or stored in the clear - this must never be used to
    recover a raw setting value, only to detect whether it changed.
    """
    baseline = {}
    for setting_id in _CRITICAL_BOOL_SETTINGS:
        baseline[setting_id] = _digest(
            ('bool', setting_id, bool(bool_values[setting_id])))
    for setting_id in _CRITICAL_INT_SETTINGS:
        baseline[setting_id] = _digest(
            ('int', setting_id, int(int_values[setting_id])))
    for setting_id in _STRING_SETTINGS:
        baseline[setting_id] = _digest(
            ('string', setting_id, string_values[setting_id]))
    for setting_id in _PRESENCE_SETTINGS:
        baseline[setting_id] = _digest(
            ('configured', setting_id, bool(presence_values[setting_id])))
    return baseline


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

    def critical_scheduler_baseline(self):
        return critical_scheduler_baseline_from_values(
            *_captured_setting_values(self.addon()))

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

    def __init__(self, host=None, poll_interval=POLL_INTERVAL_SECONDS,
                 service_initialization=False):
        self.host = host or KodiSettingsSafetyHost()
        self.poll_interval = poll_interval
        self.service_initialization = service_initialization
        self._service_initialized = False
        self._last_poll = None
        self._active = self.host.is_tvos()
        self._last_comparison_performed = False
        self._last_changed_fields = []
        self._last_block_reason = None
        self._last_recovery_first_attempt = False

    def is_unsafe(self):
        return (self._active
                and self.host.property_get(UNSAFE_PROPERTY) == '1')

    def _mark_unsafe(self, reason):
        self.host.property_set(UNSAFE_PROPERTY, '1')
        self.host.property_set(UNSAFE_REASON_PROPERTY, reason)
        self.host.property_set(SAFETY_READY_PROPERTY, 'unsafe')
        self.host.log('restart required; reason=' + reason)
        return False

    def _clear_process_state(self):
        for name in (
                UNSAFE_PROPERTY, UNSAFE_REASON_PROPERTY,
                SETTINGS_SIGNATURE_PROPERTY, INVENTORY_SIGNATURE_PROPERTY,
                SETTINGS_EDIT_PROPERTY, SETTINGS_DIAGNOSTIC_PROPERTY,
                SAFETY_READY_PROPERTY, CRITICAL_BASELINE_PROPERTY,
                SCHEDULER_RECOVERY_ATTEMPT_PROPERTY):
            self.host.property_clear(name)

    def _mark_ready(self):
        if self._active:
            self.host.property_set(SAFETY_READY_PROPERTY, 'ready')

    def _write_current_marker(self):
        self.host.write_marker({
            'schema': MARKER_SCHEMA,
            'version': self.host.version(),
            'pid': self.host.pid(),
        })

    def initialize(self):
        if not self._active:
            return True
        starting_service = (self.service_initialization
                            and not self._service_initialized)
        if starting_service:
            # The service is the long-running observer. Advertise the narrow
            # initialization window so another invocation never mistakes it
            # for an already-established safe session.
            self.host.property_set(SAFETY_READY_PROPERTY, 'initializing')
        if self.is_unsafe():
            self.host.property_set(SAFETY_READY_PROPERTY, 'unsafe')
            if starting_service:
                self._service_initialized = True
            return False

        version = self.host.version()
        pid = self.host.pid()
        try:
            marker = self.host.read_marker()
            decision = marker_decision(marker, version, pid)
            if decision in ('bootstrap', 'invalid'):
                if (self.service_initialization
                        and self.host.property_get(
                            SETTINGS_SIGNATURE_PROPERTY)):
                    # A Program invocation already established a safe
                    # process-local baseline. Kodi can start the service
                    # later in that same session and expose a cache view in
                    # which its marker is absent. That is not a new install
                    # or live update; adopt the existing baseline instead of
                    # racing an in-flight operation with a false bootstrap.
                    self.host.log('late service initialization adopted '
                                  'existing process baseline')
                    self._write_current_marker()
                    self._mark_ready()
                    if starting_service:
                        self._service_initialized = True
                    return True
                # Kodi exposes no supported install-vs-update signal to
                # Python. The first marker-aware tvOS release therefore
                # establishes its marker without reading or writing settings,
                # then requires one restart before it trusts a baseline.
                self._write_current_marker()
                result = self._mark_unsafe(decision)
                if starting_service:
                    self._service_initialized = True
                return result
            if decision == 'live_update':
                self._write_current_marker()
                result = self._mark_unsafe(decision)
                if starting_service:
                    self._service_initialized = True
                return result
            if decision == 'restart':
                self._clear_process_state()
                self._write_current_marker()
            ready = self._ensure_baselines()
            if ready:
                self._mark_ready()
            if starting_service:
                self._service_initialized = True
            return ready
        except Exception as exc:
            self.host.log(
                'could not establish safe session: %s: %s' % (
                    type(exc).__name__, exc))
            result = self._mark_unsafe('initialization_failed')
            if starting_service:
                self._service_initialized = True
            return result

    def _ensure_baselines(self):
        if not self.host.property_get(SETTINGS_SIGNATURE_PROPERTY):
            signature, diagnostic = self._settings_observation()
            self.host.property_set(SETTINGS_SIGNATURE_PROPERTY, signature)
            self._set_diagnostic_baseline(diagnostic)
        if not self.host.property_get(INVENTORY_SIGNATURE_PROPERTY):
            self.host.property_set(
                INVENTORY_SIGNATURE_PROPERTY,
                self.host.inventory_signature())
        if not self.host.property_get(CRITICAL_BASELINE_PROPERTY):
            baseline = self._critical_scheduler_baseline_observation()
            if baseline is not None:
                self._set_critical_baseline(baseline)
        return True

    def _set_critical_baseline(self, baseline):
        self.host.property_set(
            CRITICAL_BASELINE_PROPERTY,
            json.dumps(baseline, sort_keys=True, separators=(',', ':')))

    def _critical_baseline(self):
        try:
            return dict(json.loads(
                self.host.property_get(CRITICAL_BASELINE_PROPERTY)))
        except (TypeError, ValueError):
            return {}

    def _settings_observation(self):
        observe = getattr(self.host, 'settings_observation', None)
        if observe is not None:
            return observe()
        return self.host.settings_signature(), None

    def _critical_scheduler_baseline_observation(self):
        """None when the host doesn't support this (e.g. an older test
        double) - the caller then simply does not establish a critical
        baseline this pass, which correctly leaves scheduler recovery
        failing closed (no trusted baseline to validate against) rather
        than raising. Mirrors _settings_observation()'s existing
        getattr-based graceful degradation.
        """
        capture = getattr(self.host, 'critical_scheduler_baseline', None)
        if capture is None:
            return None
        return capture()

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
        elif self._last_block_reason:
            reason = self._last_block_reason
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
        self._last_block_reason = None
        unsafe_before = self.is_unsafe()
        # A distinct service interpreter can be in the small marker/baseline
        # setup window. Do not let Program or scheduler planning pass through
        # while that service has explicitly advertised that it is not ready.
        if (self._active
                and self.host.property_get(SAFETY_READY_PROPERTY)
                == 'initializing'):
            self._last_block_reason = 'service_initializing'
            allowed = False
        else:
            allowed = self.poll(force=True)
        self._log_operation_decision(action, allowed, unsafe_before)
        return allowed

    def operation_revoked(self, admitted_snapshot=False,
                          recovered_from_live_update=False):
        """Read shared state without ever refreshing operation settings.

        An admitted backup snapshot is complete and immutable.  A later
        stale-settings observation therefore still poisons the Kodi session
        for future operations, but cannot change that already-admitted plan.
        Other unsafe reasons remain revocations because they indicate a
        lifecycle or safety condition the snapshot does not make safe.

        `recovered_from_live_update` is set only for a scheduled backup
        admitted through admit_scheduler_recovery_snapshot(): that snapshot
        was validated specifically against a sticky `live_update` reason,
        so that SAME reason persisting must not revoke it either - exactly
        the same tolerance `admitted_snapshot`/`settings_view_changed`
        already has, extended to the one other reason this snapshot was
        already proven safe against. Any OTHER reason appearing (e.g. a
        fresh settings_view_changed observed mid-run) still revokes.
        """
        if not self._active:
            return False
        if self.host.property_get(SAFETY_READY_PROPERTY) == 'initializing':
            return True
        if not self.is_unsafe():
            return False
        reason = self.host.property_get(UNSAFE_REASON_PROPERTY)
        if admitted_snapshot and reason == 'settings_view_changed':
            return False
        if recovered_from_live_update and reason == 'live_update':
            return False
        return True

    def admit_backup_snapshot(self, capture):
        """Admit one complete immutable backup configuration or return None.

        The capture callable must make a fresh complete settings copy. Values
        remain in its return object only; this guard stores no private input.
        """
        if not self.allow_operation('backup_snapshot_admission'):
            return None
        try:
            first = capture()
            second = capture()
        except Exception as exc:
            self.host.log('backup snapshot capture failed: %s' %
                          type(exc).__name__)
            self._mark_unsafe('snapshot_capture_failed')
            return None
        if first != second:
            self.host.log('backup snapshot capture was inconsistent')
            self._mark_unsafe('snapshot_capture_inconsistent')
            return None
        if self._active and first.safety_signature != self.host.property_get(
                SETTINGS_SIGNATURE_PROPERTY):
            self.host.log('backup snapshot did not match session baseline')
            self._mark_unsafe('settings_view_changed')
            return None
        if self.operation_revoked():
            self._last_block_reason = 'shared_state_changed'
            self._log_operation_decision(
                'backup_snapshot_admission', False, True)
            return None
        return first

    def _last_recovery_attempt(self):
        raw = self.host.property_get(SCHEDULER_RECOVERY_ATTEMPT_PROPERTY)
        try:
            return float(raw) if raw else None
        except (TypeError, ValueError):
            return None

    def _set_last_recovery_attempt(self, when):
        self.host.property_set(
            SCHEDULER_RECOVERY_ATTEMPT_PROPERTY, repr(when))

    def scheduler_recovery_ready(self):
        """True if a scheduler recovery attempt is not currently throttled
        by RECOVERY_COOLDOWN_SECONDS. Read-only; does not itself count as
        an attempt. Lets a caller (the scheduler loop) avoid invoking
        allow_operation()/admit_scheduler_recovery_snapshot() - and their
        own logging - on every poll tick while a recovery attempt is
        still cooling down.
        """
        last_attempt = self._last_recovery_attempt()
        if last_attempt is None:
            return True
        return (self.host.monotonic() - last_attempt
               >= RECOVERY_COOLDOWN_SECONDS)

    def recovery_attempt_was_first(self):
        """True if the most recent admit_scheduler_recovery_snapshot() call
        performed the first real recovery attempt since this unsafe episode
        began (as opposed to a cooldown-throttled no-op). Used to show a
        user-facing notification at most once per episode, not on every
        retry.
        """
        return self._last_recovery_first_attempt

    def admit_scheduler_recovery_snapshot(self, capture):
        """Admit exactly one scheduled-backup snapshot after a live update.

        Eligible ONLY when the sticky unsafe reason is precisely
        'live_update' - not bootstrap, invalid, initialization_failed,
        safety_check_failed, snapshot_capture_failed,
        settings_rebaseline_failed, or any other reason - and not while
        the service is still advertising SAFETY_READY_PROPERTY as
        'initializing'. Every other unsafe reason continues to block with
        no recovery, exactly as before this method existed.

        Admission requires, in order: (1) two independently fresh, complete
        captures via `capture` that compare exactly equal to each other
        (consistency); (2) a structurally valid destination for the
        selected slot (see BackupOperationSettings.destination_state_valid);
        (3) the capture's critical, execution-affecting, non-sensitive
        fields matching the TRUSTED PRE-UPDATE baseline
        (CRITICAL_BASELINE_PROPERTY, established before the update and
        never touched by a live_update transition) field-by-field
        (correctness - two matching captures alone only prove Kodi's
        current view is internally stable, not that it is the real,
        current, non-default view). A critical field this baseline cannot
        validate (e.g. one newly introduced by the very update that
        triggered this recovery) fails closed rather than being skipped.

        Never clears UNSAFE_PROPERTY/SAFETY_READY_PROPERTY/
        SETTINGS_SIGNATURE_PROPERTY - interactive (Program) access remains
        restart-required regardless of the outcome here. Depends only on
        Window properties and fresh live reads, never on any object
        retained in this process's memory from before the update, so it
        does not depend on whether the running service interpreter kept
        executing old bytecode across the update or was itself restarted
        in place.

        self._last_block_reason is set to the specific reason on every
        failure, for logging only. self._last_recovery_first_attempt
        records whether this call actually attempted validation (True) or
        was skipped due to the retry cooldown (False) - even a "first
        attempt" may still fail every check below and return None.
        """
        self._last_block_reason = None
        self._last_recovery_first_attempt = False
        if not self._active:
            return None
        if self.host.property_get(SAFETY_READY_PROPERTY) == 'initializing':
            self._last_block_reason = 'service_initializing'
            return None
        if not self.is_unsafe():
            self._last_block_reason = 'not_unsafe'
            return None
        if self.host.property_get(UNSAFE_REASON_PROPERTY) != 'live_update':
            self._last_block_reason = 'unsafe_reason_not_recoverable'
            return None

        last_attempt = self._last_recovery_attempt()
        self._last_recovery_first_attempt = last_attempt is None
        now = self.host.monotonic()
        if (last_attempt is not None
                and now - last_attempt < RECOVERY_COOLDOWN_SECONDS):
            self._last_block_reason = 'recovery_cooldown'
            return None
        self._set_last_recovery_attempt(now)

        try:
            first = capture()
            second = capture()
        except Exception as exc:
            self.host.log('scheduler recovery capture failed: %s' %
                          type(exc).__name__)
            self._last_block_reason = 'recovery_capture_failed'
            return None
        if first != second:
            self.host.log('scheduler recovery capture was inconsistent')
            self._last_block_reason = 'recovery_capture_inconsistent'
            return None
        if not first.destination_state_valid():
            self.host.log('scheduler recovery destination state invalid')
            self._last_block_reason = 'recovery_destination_invalid'
            return None

        baseline = self._critical_baseline()
        if not baseline:
            self.host.log('scheduler recovery has no trusted baseline')
            self._last_block_reason = 'recovery_no_trusted_baseline'
            return None
        fresh = first.critical_scheduler_baseline()
        unvalidated = sorted(
            name for name in fresh if name not in baseline)
        if unvalidated:
            self.host.log('scheduler recovery cannot validate: ' +
                          ','.join(unvalidated))
            self._last_block_reason = 'recovery_field_unvalidated'
            return None
        mismatched = sorted(
            name for name in fresh if fresh[name] != baseline[name])
        if mismatched:
            self.host.log('scheduler recovery baseline mismatch: ' +
                          ','.join(mismatched))
            self._last_block_reason = 'recovery_baseline_mismatch'
            return None

        self.host.log(
            'scheduler recovery admitted a scheduled backup after a live '
            'update; interactive access remains restart-required')
        return first

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
            baseline = self._critical_scheduler_baseline_observation()
            if baseline is not None:
                self._set_critical_baseline(baseline)
            return True
        except Exception as exc:
            self.host.log(
                'settings rebaseline failed: %s: %s' % (
                    type(exc).__name__, exc))
            return self._mark_unsafe('settings_rebaseline_failed')

    def unsafe_reason(self):
        """The current sticky unsafe reason, or None if safe/inactive.

        Read-only; lets a caller decide whether a reason-specific recovery
        path (e.g. scheduler live-update recovery) applies before doing any
        real work.
        """
        if not self._active or not self.is_unsafe():
            return None
        return self.host.property_get(UNSAFE_REASON_PROPERTY) or None

    def last_block_reason(self):
        """The specific reason the most recent allow_operation()/
        admit_backup_snapshot()/admit_scheduler_recovery_snapshot() call
        was blocked, or None. For logging/notification only - never used
        to make a safety decision itself.
        """
        return self._last_block_reason
