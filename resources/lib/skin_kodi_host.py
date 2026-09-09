from __future__ import unicode_literals

"""Kodi boundary for the crash-safe AF3 restore coordinator."""

import json

from .skin_adapter import (
    AF3_ID,
    SkinAdapterError,
    checked_appearance,
    checked_skin_setting_values,
    live_skin_setting_values,
    skin_setting_values,
    skin_settings_equal,
)


class KodiSkinHostError(RuntimeError):
    pass


class KodiSkinHost:
    """Expose verified Kodi operations without owning restore state."""

    def __init__(self, xbmc_module, xbmcvfs_module, xbmcaddon_module,
                 switch_skin, wait, progress=None):
        self.xbmc = xbmc_module
        self.xbmcvfs = xbmcvfs_module
        self.xbmcaddon = xbmcaddon_module
        if not callable(switch_skin) or not callable(wait):
            raise KodiSkinHostError('Kodi skin host callbacks are unavailable')
        self.switch_skin = switch_skin
        self.wait = wait
        self.progress_callback = progress

    def progress(self, percent, message):
        if callable(self.progress_callback):
            self.progress_callback(percent, message)

    def ensure_dependencies(self, skin, helper):
        for addon_id in (skin, helper):
            try:
                addon = self.xbmcaddon.Addon(addon_id)
                version = addon.getAddonInfo('version')
            except Exception as error:
                raise KodiSkinHostError(
                    'install {} before restoring'.format(addon_id)) from error
            if not isinstance(version, str) or not version.strip():
                raise KodiSkinHostError(
                    '{} did not expose an installed version'.format(addon_id))

    def stop_playback(self):
        try:
            player = self.xbmc.Player()
            if player.isPlaying():
                player.stop()
            for _attempt in range(40):
                if not player.isPlaying():
                    return
                self.wait(0.1)
        except KodiSkinHostError:
            raise
        except Exception as error:
            raise KodiSkinHostError('Kodi could not stop playback') from error
        raise KodiSkinHostError('playback did not stop')

    def is_playing(self):
        try:
            return bool(self.xbmc.Player().isPlaying())
        except Exception as error:
            raise KodiSkinHostError('Kodi could not inspect playback') from error

    def active_skin(self):
        try:
            skin = self.xbmc.getSkinDir()
        except Exception as error:
            raise KodiSkinHostError('Kodi could not inspect the active skin') from error
        if not isinstance(skin, str) or not skin:
            raise KodiSkinHostError('Kodi returned an invalid active skin')
        return skin

    def _fallback_skin(self, target):
        for addon_id in ('skin.estuary', 'skin.estouchy'):
            if addon_id == target:
                continue
            try:
                addon = self.xbmcaddon.Addon(addon_id)
                if addon.getAddonInfo('version'):
                    return addon_id
            except Exception:
                continue
        raise KodiSkinHostError(
            'install or activate another skin before restoring')

    def ensure_inactive(self, skin):
        if self.active_skin() != skin:
            return
        fallback = self._fallback_skin(skin)
        self.switch_skin(fallback)
        for _attempt in range(20):
            if self.active_skin() != skin:
                return
            self.wait(0.1)
        raise KodiSkinHostError('target skin remained active')

    def activate_skin(self, skin):
        if self.active_skin() != skin:
            self.switch_skin(skin)
        for _attempt in range(20):
            if self.active_skin() == skin:
                return
            self.wait(0.1)
        raise KodiSkinHostError('restored skin did not become active')

    def _rpc(self, method, **params):
        try:
            request = json.dumps({
                'jsonrpc': '2.0', 'method': method,
                'params': params, 'id': 1,
            })
            response = json.loads(self.xbmc.executeJSONRPC(request))
        except Exception as error:
            raise KodiSkinHostError(
                'Kodi could not complete ' + method) from error
        if not isinstance(response, dict) or 'error' in response:
            raise KodiSkinHostError('Kodi could not complete ' + method)
        return response.get('result')

    def verify_loaded_settings(self, skin, values):
        if self.active_skin() != skin:
            raise KodiSkinHostError(
                'target skin is not active for settings verification')
        try:
            checked = checked_skin_setting_values(values)
        except SkinAdapterError as error:
            raise KodiSkinHostError(str(error)) from error
        path = self._settings_path(skin)
        try:
            if not self.xbmcvfs.exists(path):
                raise KodiSkinHostError(
                    'staged skin settings document is unavailable')
            saved = skin_setting_values(self._read(path))
        except SkinAdapterError as error:
            raise KodiSkinHostError(str(error)) from error
        if not skin_settings_equal(saved, checked):
            raise KodiSkinHostError(
                'staged skin settings changed before activation')

        for _attempt in range(20):
            try:
                current = live_skin_setting_values(
                    self._rpc('Settings.GetSkinSettings'), skin)
            except SkinAdapterError as error:
                raise KodiSkinHostError(str(error)) from error
            if skin_settings_equal(current, checked):
                return
            self.wait(0.25)
            if self.active_skin() != skin:
                raise KodiSkinHostError(
                    'active skin changed during settings verification')
        raise KodiSkinHostError(
            'Kodi did not load every restored skin setting')

    def apply_appearance(self, values):
        try:
            checked = checked_appearance(values)
        except SkinAdapterError as error:
            raise KodiSkinHostError(str(error)) from error
        for setting, value in checked.items():
            result = self._rpc('Settings.GetSettingValue', setting=setting)
            if not isinstance(result, dict) or 'value' not in result:
                raise KodiSkinHostError(
                    'Kodi did not expose appearance setting ' + setting)
            if result['value'] != value:
                accepted = self._rpc(
                    'Settings.SetSettingValue', setting=setting, value=value)
                if accepted is not True:
                    raise KodiSkinHostError(
                        'Kodi refused appearance setting ' + setting)
            verified = self._rpc(
                'Settings.GetSettingValue', setting=setting)
            if (not isinstance(verified, dict)
                    or verified.get('value') != value):
                raise KodiSkinHostError(
                    'Kodi did not keep appearance setting ' + setting)

    @staticmethod
    def _settings_path(skin):
        return 'special://profile/addon_data/{}/settings.xml'.format(skin)

    def _read(self, path):
        handle = self.xbmcvfs.File(path)
        try:
            return bytes(handle.readBytes(handle.size()))
        finally:
            handle.close()

    def _write(self, path, data):
        handle = self.xbmcvfs.File(path, 'w')
        try:
            if handle.write(data) is False:
                raise OSError('VFS write returned false')
        finally:
            handle.close()

    def stage_settings(self, skin, document, values):
        if skin != AF3_ID or self.active_skin() == skin:
            raise KodiSkinHostError(
                'target AF3 skin must be inactive while staging settings')
        try:
            checked = checked_skin_setting_values(values)
            if not skin_settings_equal(skin_setting_values(document), checked):
                raise KodiSkinHostError(
                    'staged settings document does not match pending values')
        except SkinAdapterError as error:
            raise KodiSkinHostError(str(error)) from error

        directory = 'special://profile/addon_data/{}'.format(skin)
        path = self._settings_path(skin)
        previous = None
        write_started = False
        try:
            if (not self.xbmcvfs.mkdirs(directory)
                    and not self.xbmcvfs.exists(directory)):
                raise KodiSkinHostError(
                    'Kodi could not create the skin settings folder')
            if self.xbmcvfs.exists(path):
                previous = self._read(path)
            write_started = True
            self._write(path, document)
            saved = self._read(path)
            if not skin_settings_equal(skin_setting_values(saved), checked):
                raise KodiSkinHostError(
                    'Kodi changed the staged skin settings document')
        except KodiSkinHostError:
            self._restore_vfs(path, previous, write_started)
            raise
        except Exception as error:
            self._restore_vfs(path, previous, write_started)
            raise KodiSkinHostError(
                'Kodi could not stage and verify skin settings') from error

    def _restore_vfs(self, path, previous, write_started):
        if not write_started:
            return
        try:
            if previous is not None:
                self._write(path, previous)
            elif self.xbmcvfs.exists(path):
                self.xbmcvfs.delete(path)
        except Exception:
            pass
