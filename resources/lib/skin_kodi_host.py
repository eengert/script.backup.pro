from __future__ import unicode_literals

"""Kodi boundary for the crash-safe AF3 restore coordinator."""

import json
import os
import re
import time
import uuid
from xml.etree import ElementTree

from .skin_adapter import (
    AF3_ID,
    MAX_FILE_BYTES,
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
                 switch_skin, wait, progress=None, xbmcgui_module=None):
        self.xbmc = xbmc_module
        self.xbmcvfs = xbmcvfs_module
        self.xbmcaddon = xbmcaddon_module
        if not callable(switch_skin) or not callable(wait):
            raise KodiSkinHostError('Kodi skin host callbacks are unavailable')
        self.switch_skin = switch_skin
        self.wait = wait
        self.progress_callback = progress
        self.xbmcgui = xbmcgui_module

    def progress(self, percent, message):
        if callable(self.progress_callback):
            try:
                self.progress_callback(percent, message)
            except Exception:
                pass

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

    def capture_appearance(self):
        values = {}
        for setting in (
                'lookandfeel.skintheme', 'lookandfeel.skincolors',
                'lookandfeel.font', 'lookandfeel.skinzoom'):
            try:
                result = self._rpc(
                    'Settings.GetSettingValue', setting=setting)
            except KodiSkinHostError:
                continue
            if isinstance(result, dict) and result.get('value') is not None:
                values[setting] = result['value']
        try:
            return checked_appearance(values)
        except SkinAdapterError as error:
            raise KodiSkinHostError(str(error)) from error

    @staticmethod
    def _settings_path(skin):
        return 'special://profile/addon_data/{}/settings.xml'.format(skin)

    def _read(self, path, maximum=MAX_FILE_BYTES):
        handle = self.xbmcvfs.File(path)
        try:
            size = handle.size()
            if size > maximum:
                raise KodiSkinHostError('Kodi VFS file exceeds its safe limit')
            return bytes(handle.readBytes(size))
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

    def _home_window(self):
        if self.xbmcgui is None:
            raise KodiSkinHostError('Kodi window API is unavailable')
        try:
            return self.xbmcgui.Window(10000)
        except Exception as error:
            raise KodiSkinHostError('Kodi home window is unavailable') from error

    def _clear_helper_cache(self, paths):
        window = self._home_window()
        prefix = 'addon_data/script.skinvariables/nodes/'
        for path in paths:
            if not path.startswith(prefix):
                continue
            suffix = path[len(prefix):]
            if '/' not in suffix:
                continue
            directory, filename = suffix.rsplit('/', 1)
            window.clearProperty(
                'SkinVariables.ShortcutsNode.{}-{}'.format(
                    directory, filename))
        window.setProperty(
            'SkinVariables.ShortcutsNode.Reload', str(time.time()))

    def _validate_generated_includes(self, skin_path):
        names = (
            'script-skinvariables-includes.xml',
            'script-skinvariables-labels-includes.xml',
            'script-skinvariables-images-includes.xml',
        )
        for name in names:
            path = os.path.join(skin_path, '1080i', name)
            try:
                if not self.xbmcvfs.exists(path):
                    raise KodiSkinHostError(
                        'AF3 generated include is missing: ' + name)
                root = ElementTree.fromstring(self._read(path))
            except (ElementTree.ParseError, ValueError) as error:
                raise KodiSkinHostError(
                    'AF3 generated include is invalid: ' + name) from error
            if root.tag != 'includes':
                raise KodiSkinHostError(
                    'AF3 generated include has an invalid root: ' + name)

    def rebuild_skin(self, skin, pending):
        if skin != AF3_ID or self.active_skin() != skin:
            raise KodiSkinHostError('AF3 must be active before rebuilding')
        try:
            enabled = self.xbmc.getCondVisibility(
                'System.AddonIsEnabled(script.skinvariables)')
        except Exception as error:
            raise KodiSkinHostError(
                'Kodi could not inspect Skin Variables') from error
        if not enabled:
            raise KodiSkinHostError('enable Skin Variables before rebuilding AF3')

        slug = self.xbmc.getInfoLabel(
            'Skin.String(SkinVariables.SkinUser)')
        if (not isinstance(slug, str)
                or (slug and not re.fullmatch(r'user-[A-Za-z0-9]+', slug))):
            raise KodiSkinHostError('AF3 selected an invalid skin profile')
        try:
            skin_path = self.xbmcvfs.translatePath(
                self.xbmcaddon.Addon(skin).getAddonInfo('path'))
        except Exception as error:
            raise KodiSkinHostError('AF3 install path is unavailable') from error
        if (not isinstance(skin_path, str) or not skin_path
                or '\x00' in skin_path):
            raise KodiSkinHostError('AF3 install path is invalid')

        selector_path = os.path.join(
            skin_path, '1080i',
            'script-skinvariables-skinusers.xml')
        selector_previous = None
        selector_existed = self.xbmcvfs.exists(selector_path)
        if selector_existed:
            selector_previous = self._read(selector_path)
        selector = ElementTree.Element('includes')
        if slug:
            ElementTree.SubElement(selector, 'include', {
                'file': ('script-skinvariables-generator-includes-{}.xml'
                         .format(slug)),
            })
        selector_data = ElementTree.tostring(
            selector, encoding='utf-8', xml_declaration=True)

        self._clear_helper_cache(pending.get('helper_hashes', {}))
        token = uuid.uuid4().hex
        completion_property = 'BackupPro.RebuildComplete'
        window = self._home_window()
        window.clearProperty(completion_property)
        actions = [
            'route=template=images&force=True&no_reload=True',
            'route=template=labels&force=True&no_reload=True',
            'route=force=True&no_reload=True',
            'route=action=buildviews&force=True&no_reload=True',
            'route=action=buildtemplate&force=True&no_reload=True',
            'SetProperty({},{},Home)'.format(
                completion_property, token),
        ]
        plan_path = (
            'special://profile/addon_data/script.backup.pro/'
            'rebuild-{}.json'.format(token))
        if self.xbmcvfs.exists(plan_path):
            raise KodiSkinHostError('AF3 rebuild plan path already exists')
        plan = json.dumps(
            {'actions': actions}, sort_keys=True,
            separators=(',', ':')).encode('utf-8')
        command = (
            'RunScript(script.skinvariables,'
            'run_executebuiltin=special://profile/addon_data/'
            'script.backup.pro/rebuild-{}.json,use_rules=True)'
            .format(token))
        try:
            self._write(selector_path, selector_data)
            if self._read(selector_path) != selector_data:
                raise KodiSkinHostError(
                    'AF3 skin-profile selector failed verification')
            self._write(plan_path, plan)
            if self._read(plan_path) != plan:
                raise KodiSkinHostError(
                    'AF3 rebuild plan failed verification')
            self.xbmc.executebuiltin(command)
            for attempt in range(240):
                if window.getProperty(completion_property) == token:
                    break
                if self.active_skin() != skin:
                    raise KodiSkinHostError(
                        'AF3 changed while its menus were rebuilding')
                if attempt % 20 == 0:
                    self.progress(70, 'Waiting for AF3 menus and widgets')
                self.wait(0.25)
            else:
                raise KodiSkinHostError('AF3 rebuild did not finish')
            self._validate_generated_includes(skin_path)
            self.xbmc.executebuiltin('ReloadSkin()', True)
            if self.active_skin() != skin:
                raise KodiSkinHostError(
                    'AF3 did not remain active after reload')
            self._validate_generated_includes(skin_path)
        except KodiSkinHostError:
            self._restore_vfs(
                selector_path, selector_previous, True)
            raise
        except Exception as error:
            self._restore_vfs(
                selector_path, selector_previous, True)
            raise KodiSkinHostError('AF3 rebuild failed') from error
        finally:
            window.clearProperty(completion_property)
            try:
                if self.xbmcvfs.exists(plan_path):
                    self.xbmcvfs.delete(plan_path)
            except Exception:
                pass
