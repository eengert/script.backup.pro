import importlib.util
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("kodi_test", Path(__file__).parents[1] / "tools/kodi_test.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class KodiHarnessTests(unittest.TestCase):
    def test_disposable_paths_do_not_overlap_normal_profile(self):
        info = MODULE.verify_isolation()
        self.assertNotEqual(
            Path(info["appdata"]).resolve(), MODULE.NORMAL_APPDATA_DIR.resolve())
        self.assertNotEqual(
            Path(info["log"]).resolve(), MODULE.NORMAL_LOG_FILE.resolve())
        self.assertTrue(MODULE._inside(Path(info["userdata"]), MODULE.ROOT))
        self.assertTrue(MODULE._inside(Path(info["addons"]), MODULE.ROOT))
        self.assertTrue(MODULE._inside(Path(info["log"]), MODULE.ROOT))

    def test_verify_matches_the_layout_observed_from_a_real_kodi_launch(self):
        # Kodi 21.1 on macOS resolves special://home/ (the profile/addons
        # root) to $HOME/Library/Application Support/Kodi and
        # special://logpath/ to $HOME/Library/Logs, not a `.kodi/` layout
        # - confirmed against an actual isolated launch on 2026-09-10 (see
        # docs/MAC_KODI_VALIDATION.md). Guard against silently regressing
        # to the wrong assumed layout.
        info = MODULE.verify_isolation()
        self.assertEqual(
            Path(info["appdata"]), MODULE.HOME / "Library" / "Application Support" / "Kodi")
        self.assertEqual(Path(info["log"]), MODULE.HOME / "Library" / "Logs" / "kodi.log")

    def test_verify_refuses_appdata_overlapping_the_real_profile(self):
        old_appdata = MODULE.KODI_APPDATA_DIR
        MODULE.KODI_APPDATA_DIR = MODULE.NORMAL_APPDATA_DIR
        try:
            with self.assertRaises(RuntimeError):
                MODULE.verify_isolation()
        finally:
            MODULE.KODI_APPDATA_DIR = old_appdata

    def test_verify_refuses_log_overlapping_the_real_log(self):
        old_log = MODULE.KODI_LOG_FILE
        MODULE.KODI_LOG_FILE = MODULE.NORMAL_LOG_FILE
        try:
            with self.assertRaises(RuntimeError):
                MODULE.verify_isolation()
        finally:
            MODULE.KODI_LOG_FILE = old_log

    def test_reset_refuses_running_instance(self):
        old = MODULE.PID_FILE
        with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
            MODULE.PID_FILE = Path(d) / "kodi.pid"
            MODULE.PID_FILE.write_text("999999\n")
            with self.assertRaises(RuntimeError):
                MODULE.reset()
        MODULE.PID_FILE = old

    def _retarget(self, root: Path):
        MODULE.ROOT = root
        MODULE.HOME = root / "home"
        MODULE.KODI_APPDATA_DIR = MODULE.HOME / "Library" / "Application Support" / "Kodi"
        MODULE.KODI_USERDATA_DIR = MODULE.KODI_APPDATA_DIR / "userdata"
        MODULE.KODI_ADDONS_DIR = MODULE.KODI_APPDATA_DIR / "addons"
        MODULE.KODI_LOG_FILE = MODULE.HOME / "Library" / "Logs" / "kodi.log"

    def test_install_worktree_uses_allowlist_only(self):
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR, MODULE.KODI_LOG_FILE)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                self._retarget(Path(d) / "root")
                MODULE.install(MODULE.PROJECT)
                target = MODULE.KODI_ADDONS_DIR / MODULE.ADDON_ID
                self.assertTrue((target / "addon.xml").exists())
                self.assertFalse((target / ".agent").exists())
                self.assertFalse((target / "tests").exists())
        finally:
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR, MODULE.KODI_LOG_FILE) = old

    def test_install_places_the_addon_where_kodi_actually_reads_addons(self):
        # regression guard: an earlier version of this harness installed
        # into HOME/.kodi/addons/, a location the real macOS Kodi binary
        # never reads (see test_verify_matches_the_layout_observed_...).
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR, MODULE.KODI_LOG_FILE)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                self._retarget(Path(d) / "root")
                MODULE.install(MODULE.PROJECT)
                target = MODULE.KODI_APPDATA_DIR / "addons" / MODULE.ADDON_ID
                self.assertTrue((target / "addon.xml").exists())
                self.assertTrue(MODULE._inside(target, MODULE.HOME / "Library"))
        finally:
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR, MODULE.KODI_LOG_FILE) = old

    def test_declared_dependencies_excludes_xbmc_python(self):
        self.assertNotIn("xbmc.python", MODULE._declared_dependencies())
        for expected in ("script.module.dateutil", "script.module.future",
                          "script.module.dropbox", "script.module.pyqrcode"):
            self.assertIn(expected, MODULE._declared_dependencies())

    def test_declared_dependencies_excludes_any_virtual_xbmc_namespace(self):
        # regression guard: skin.arctic.fuse.3's own addon.xml declares
        # xbmc.gui (not xbmc.python) as a virtual platform dependency -
        # confirmed empirically 2026-09-10 - so the exclusion must cover
        # the whole xbmc.* namespace, not just the one literal id.
        with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
            source = Path(d)
            (source / "addon.xml").write_text(
                '<addon><requires>'
                '<import addon="xbmc.gui" version="5.17.0"/>'
                '<import addon="xbmc.python" version="3.0.0"/>'
                '<import addon="script.module.example" version="1.0.0"/>'
                '</requires></addon>')
            self.assertEqual(
                MODULE._declared_dependencies(source), ["script.module.example"])

    def test_install_dependencies_copies_from_the_real_profile_only(self):
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
               MODULE.KODI_LOG_FILE, MODULE.NORMAL_APPDATA_DIR)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                d = Path(d)
                self._retarget(d / "root")
                fake_real_addons = d / "fake-real-profile" / "addons"
                fake_dep_dir = fake_real_addons / "script.module.example"
                fake_dep_dir.mkdir(parents=True)
                (fake_dep_dir / "marker.py").write_text("# dependency\n")
                (fake_dep_dir / "addon.xml").write_text(
                    '<addon><requires></requires></addon>')
                MODULE.NORMAL_APPDATA_DIR = d / "fake-real-profile"

                source = d / "addon-source"
                source.mkdir()
                (source / "addon.xml").write_text(
                    '<addon><requires>'
                    '<import addon="xbmc.python" version="3.0.0"/>'
                    '<import addon="script.module.example" version="1.0.0"/>'
                    '</requires></addon>')

                installed = MODULE.install_dependencies(source)
                self.assertEqual(installed, ["script.module.example"])
                copied = MODULE.KODI_ADDONS_DIR / "script.module.example" / "marker.py"
                self.assertTrue(copied.exists())
        finally:
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
             MODULE.KODI_LOG_FILE, MODULE.NORMAL_APPDATA_DIR) = old

    def test_install_dependencies_resolves_transitive_dependencies(self):
        # regression guard: script.module.dropbox itself needs
        # script.module.requests, which Backup Pro's own addon.xml does
        # not declare - confirmed empirically 2026-09-10.
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
               MODULE.KODI_LOG_FILE, MODULE.NORMAL_APPDATA_DIR)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                d = Path(d)
                self._retarget(d / "root")
                fake_real_addons = d / "fake-real-profile" / "addons"

                top_dir = fake_real_addons / "script.module.top"
                top_dir.mkdir(parents=True)
                (top_dir / "addon.xml").write_text(
                    '<addon><requires>'
                    '<import addon="script.module.transitive" version="1.0.0"/>'
                    '</requires></addon>')

                transitive_dir = fake_real_addons / "script.module.transitive"
                transitive_dir.mkdir()
                (transitive_dir / "marker.py").write_text("# transitive\n")
                (transitive_dir / "addon.xml").write_text(
                    '<addon><requires></requires></addon>')

                MODULE.NORMAL_APPDATA_DIR = d / "fake-real-profile"

                source = d / "addon-source"
                source.mkdir()
                (source / "addon.xml").write_text(
                    '<addon><requires>'
                    '<import addon="script.module.top" version="1.0.0"/>'
                    '</requires></addon>')

                installed = MODULE.install_dependencies(source)
                self.assertEqual(
                    set(installed), {"script.module.top", "script.module.transitive"})
                self.assertTrue((MODULE.KODI_ADDONS_DIR / "script.module.transitive"
                                  / "marker.py").exists())
        finally:
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
             MODULE.KODI_LOG_FILE, MODULE.NORMAL_APPDATA_DIR) = old

    def test_copy_addon_closure_skips_addons_bundled_with_kodi_itself(self):
        # regression guard: script.module.pil (needed transitively for
        # skin.arctic.fuse.3) was wrongly reported "missing" because
        # this function only ever checked the real profile's addons/ -
        # it's actually bundled inside Kodi.app itself and therefore
        # already visible to every profile, including a fresh
        # disposable one, with nothing to copy - confirmed empirically
        # 2026-09-10.
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
               MODULE.KODI_LOG_FILE, MODULE.NORMAL_APPDATA_DIR,
               MODULE.KODI_SYSTEM_ADDONS_DIR)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                d = Path(d)
                self._retarget(d / "root")

                system_dir = d / "fake-system-addons" / "script.module.bundled"
                system_dir.mkdir(parents=True)
                (system_dir / "addon.xml").write_text(
                    '<addon><requires>'
                    '<import addon="script.module.profile_only" version="1.0.0"/>'
                    '</requires></addon>')
                MODULE.KODI_SYSTEM_ADDONS_DIR = d / "fake-system-addons"

                fake_real_addons = d / "fake-real-profile" / "addons"
                profile_only_dir = fake_real_addons / "script.module.profile_only"
                profile_only_dir.mkdir(parents=True)
                (profile_only_dir / "marker.py").write_text("# profile only\n")
                (profile_only_dir / "addon.xml").write_text(
                    '<addon><requires></requires></addon>')
                MODULE.NORMAL_APPDATA_DIR = d / "fake-real-profile"

                installed = MODULE._copy_addon_closure(["script.module.bundled"])

                # the bundled add-on itself was not copied ...
                self.assertEqual(installed, ["script.module.profile_only"])
                self.assertFalse(
                    (MODULE.KODI_ADDONS_DIR / "script.module.bundled").exists())
                # ... but its own dependency, only available from the
                # real profile, still was
                self.assertTrue((MODULE.KODI_ADDONS_DIR / "script.module.profile_only"
                                  / "marker.py").exists())
        finally:
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
             MODULE.KODI_LOG_FILE, MODULE.NORMAL_APPDATA_DIR,
             MODULE.KODI_SYSTEM_ADDONS_DIR) = old

    def test_install_skin_copies_the_skin_and_its_own_dependencies(self):
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
               MODULE.KODI_LOG_FILE, MODULE.NORMAL_APPDATA_DIR)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                d = Path(d)
                self._retarget(d / "root")
                fake_real_addons = d / "fake-real-profile" / "addons"

                skin_dir = fake_real_addons / "skin.example"
                skin_dir.mkdir(parents=True)
                (skin_dir / "addon.xml").write_text(
                    '<addon><requires>'
                    '<import addon="script.module.skinhelper" version="1.0.0"/>'
                    '</requires></addon>')

                helper_dir = fake_real_addons / "script.module.skinhelper"
                helper_dir.mkdir()
                (helper_dir / "marker.py").write_text("# skin helper\n")
                (helper_dir / "addon.xml").write_text(
                    '<addon><requires></requires></addon>')

                MODULE.NORMAL_APPDATA_DIR = d / "fake-real-profile"

                installed = MODULE.install_skin("skin.example")
                self.assertEqual(
                    set(installed), {"skin.example", "script.module.skinhelper"})
                self.assertTrue(
                    (MODULE.KODI_ADDONS_DIR / "skin.example" / "addon.xml").exists())
                self.assertTrue((MODULE.KODI_ADDONS_DIR / "script.module.skinhelper"
                                  / "marker.py").exists())
        finally:
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
             MODULE.KODI_LOG_FILE, MODULE.NORMAL_APPDATA_DIR) = old

    def test_install_dependencies_refuses_a_dependency_missing_from_the_real_profile(self):
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
               MODULE.KODI_LOG_FILE, MODULE.NORMAL_APPDATA_DIR)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                d = Path(d)
                self._retarget(d / "root")
                MODULE.NORMAL_APPDATA_DIR = d / "fake-real-profile-empty"

                source = d / "addon-source"
                source.mkdir()
                (source / "addon.xml").write_text(
                    '<addon><requires>'
                    '<import addon="script.module.missing" version="1.0.0"/>'
                    '</requires></addon>')

                with self.assertRaises(RuntimeError):
                    MODULE.install_dependencies(source)
        finally:
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
             MODULE.KODI_LOG_FILE, MODULE.NORMAL_APPDATA_DIR) = old

    def test_install_dependencies_refuses_a_symlinked_source(self):
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
               MODULE.KODI_LOG_FILE, MODULE.NORMAL_APPDATA_DIR)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                d = Path(d)
                self._retarget(d / "root")
                fake_real_addons = d / "fake-real-profile" / "addons"
                fake_real_addons.mkdir(parents=True)
                real_dep_dir = d / "actual-dep-location"
                real_dep_dir.mkdir()
                (fake_real_addons / "script.module.example").symlink_to(
                    real_dep_dir, target_is_directory=True)
                MODULE.NORMAL_APPDATA_DIR = d / "fake-real-profile"

                source = d / "addon-source"
                source.mkdir()
                (source / "addon.xml").write_text(
                    '<addon><requires>'
                    '<import addon="script.module.example" version="1.0.0"/>'
                    '</requires></addon>')

                with self.assertRaises(RuntimeError):
                    MODULE.install_dependencies(source)
        finally:
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
             MODULE.KODI_LOG_FILE, MODULE.NORMAL_APPDATA_DIR) = old

    def test_configure_writes_only_the_given_settings(self):
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR, MODULE.KODI_LOG_FILE)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                self._retarget(Path(d) / "root")
                MODULE.configure("script.backup.pro", {"remote_path": "/tmp/dest",
                                                         "remote_selection": "0"})
                settings_path = (MODULE.KODI_USERDATA_DIR / "addon_data"
                                  / "script.backup.pro" / "settings.xml")
                text = settings_path.read_text(encoding="utf-8")
                self.assertIn('<setting id="remote_path">/tmp/dest</setting>', text)
                self.assertIn('<setting id="remote_selection">0</setting>', text)
        finally:
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR, MODULE.KODI_LOG_FILE) = old

    def test_configure_escapes_xml_special_characters(self):
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR, MODULE.KODI_LOG_FILE)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                self._retarget(Path(d) / "root")
                MODULE.configure("script.backup.pro", {"remote_path": "/tmp/a&b<c>"})
                settings_path = (MODULE.KODI_USERDATA_DIR / "addon_data"
                                  / "script.backup.pro" / "settings.xml")
                text = settings_path.read_text(encoding="utf-8")
                self.assertIn("&amp;", text)
                self.assertIn("&lt;", text)
                self.assertNotIn("/tmp/a&b<c>", text)
        finally:
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR, MODULE.KODI_LOG_FILE) = old

    def test_configure_requires_at_least_one_setting(self):
        with self.assertRaises(RuntimeError):
            MODULE.configure("script.backup.pro", {})

    def test_configure_webserver_writes_expected_settings(self):
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
               MODULE.KODI_LOG_FILE, MODULE.KODI_GUISETTINGS_FILE)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                self._retarget(Path(d) / "root")
                MODULE.KODI_GUISETTINGS_FILE = MODULE.KODI_USERDATA_DIR / "guisettings.xml"
                MODULE.configure_webserver(port=1234, username="u", password="p")
                text = MODULE.KODI_GUISETTINGS_FILE.read_text(encoding="utf-8")
                self.assertIn('<setting id="services.webserver">true</setting>', text)
                self.assertIn('<setting id="services.webserverport">1234</setting>', text)
                self.assertIn(
                    '<setting id="services.webserverauthentication">true</setting>', text)
                self.assertIn('<setting id="services.webserverusername">u</setting>', text)
                self.assertIn('<setting id="services.webserverpassword">p</setting>', text)
        finally:
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
             MODULE.KODI_LOG_FILE, MODULE.KODI_GUISETTINGS_FILE) = old

    def test_configure_webserver_folds_in_extra_settings(self):
        # needed so a caller can activate a skin (lookandfeel.skin) in
        # the SAME pre-launch write as the webserver settings, since
        # configure_webserver() can only be called once per fresh
        # profile.
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
               MODULE.KODI_LOG_FILE, MODULE.KODI_GUISETTINGS_FILE)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                self._retarget(Path(d) / "root")
                MODULE.KODI_GUISETTINGS_FILE = MODULE.KODI_USERDATA_DIR / "guisettings.xml"
                MODULE.configure_webserver(
                    port=1234, username="u", password="p",
                    extra_settings={"lookandfeel.skin": "skin.example"})
                text = MODULE.KODI_GUISETTINGS_FILE.read_text(encoding="utf-8")
                self.assertIn('<setting id="services.webserver">true</setting>', text)
                self.assertIn(
                    '<setting id="lookandfeel.skin">skin.example</setting>', text)
        finally:
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
             MODULE.KODI_LOG_FILE, MODULE.KODI_GUISETTINGS_FILE) = old

    def test_configure_webserver_refuses_when_guisettings_already_exists(self):
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
               MODULE.KODI_LOG_FILE, MODULE.KODI_GUISETTINGS_FILE)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                self._retarget(Path(d) / "root")
                MODULE.KODI_GUISETTINGS_FILE = MODULE.KODI_USERDATA_DIR / "guisettings.xml"
                MODULE.KODI_USERDATA_DIR.mkdir(parents=True, exist_ok=True)
                MODULE.KODI_GUISETTINGS_FILE.write_text(
                    '<settings version="2"></settings>', encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    MODULE.configure_webserver(port=1234, username="u", password="p")
        finally:
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
             MODULE.KODI_LOG_FILE, MODULE.KODI_GUISETTINGS_FILE) = old

    def test_configure_webserver_requires_a_password(self):
        with self.assertRaises(RuntimeError):
            MODULE.configure_webserver(port=1234, username="u", password="")

    def test_jsonrpc_posts_expected_request_with_basic_auth(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"jsonrpc": "2.0", "id": 1, "result": "pong"}'

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["headers"] = dict(request.header_items())
            captured["body"] = MODULE.json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        original = MODULE.urllib.request.urlopen
        MODULE.urllib.request.urlopen = fake_urlopen
        try:
            result = MODULE.jsonrpc("JSONRPC.Ping", port=1234, username="u",
                                     password="p", timeout=5)
            self.assertEqual(result["result"], "pong")
            self.assertEqual(captured["url"], "http://127.0.0.1:1234/jsonrpc")
            self.assertEqual(captured["method"], "POST")
            self.assertEqual(captured["body"]["method"], "JSONRPC.Ping")
            self.assertEqual(captured["timeout"], 5)
            import base64
            expected_auth = "Basic " + base64.b64encode(b"u:p").decode("ascii")
            self.assertEqual(captured["headers"]["Authorization"], expected_auth)
        finally:
            MODULE.urllib.request.urlopen = original

    def test_jsonrpc_wraps_connection_errors(self):
        def failing_urlopen(_request, timeout=None):
            raise MODULE.urllib.error.URLError("connection refused")

        original = MODULE.urllib.request.urlopen
        MODULE.urllib.request.urlopen = failing_urlopen
        try:
            with self.assertRaises(RuntimeError):
                MODULE.jsonrpc("JSONRPC.Ping")
        finally:
            MODULE.urllib.request.urlopen = original

    def test_execute_addon_builds_addons_executeaddon_call(self):
        captured = {}

        def fake_jsonrpc(method, params=None, **kwargs):
            captured["method"] = method
            captured["params"] = params
            captured["kwargs"] = kwargs
            return {"result": "OK"}

        original = MODULE.jsonrpc
        MODULE.jsonrpc = fake_jsonrpc
        try:
            result = MODULE.execute_addon(
                "script.backup.pro", ["mode=backup"], port=1234)
            self.assertEqual(result["result"], "OK")
            self.assertEqual(captured["method"], "Addons.ExecuteAddon")
            self.assertEqual(captured["params"]["addonid"], "script.backup.pro")
            self.assertEqual(captured["params"]["params"], ["mode=backup"])
            self.assertEqual(captured["kwargs"], {"port": 1234})
        finally:
            MODULE.jsonrpc = original

    def test_enable_addon_builds_addons_setaddonenabled_call(self):
        # regression guard: a freshly install()-ed add-on is not enabled
        # by default, and Addons.ExecuteAddon fails against a disabled
        # add-on - confirmed empirically against a real launch
        # (2026-09-10, see docs/MAC_KODI_VALIDATION.md).
        captured = {}

        def fake_jsonrpc(method, params=None, **kwargs):
            captured["method"] = method
            captured["params"] = params
            captured["kwargs"] = kwargs
            return {"result": "OK"}

        original = MODULE.jsonrpc
        MODULE.jsonrpc = fake_jsonrpc
        try:
            result = MODULE.enable_addon("script.backup.pro", port=1234)
            self.assertEqual(result["result"], "OK")
            self.assertEqual(captured["method"], "Addons.SetAddonEnabled")
            self.assertEqual(captured["params"],
                              {"addonid": "script.backup.pro", "enabled": True})
            self.assertEqual(captured["kwargs"], {"port": 1234})
        finally:
            MODULE.jsonrpc = original

    def test_enable_addons_enables_each_id(self):
        # regression guard: skin.arctic.fuse.3 would not actually load
        # even once present, because it (and every add-on it depends
        # on) starts disabled just like any other freshly copied
        # add-on - confirmed empirically 2026-09-10.
        calls = []

        def fake_enable_addon(addon_id, **kwargs):
            calls.append((addon_id, kwargs))
            return {"result": "OK"}

        original = MODULE.enable_addon
        MODULE.enable_addon = fake_enable_addon
        try:
            MODULE.enable_addons(
                ["skin.arctic.fuse.3", "script.skinvariables"], port=1234)
            self.assertEqual(calls, [
                ("skin.arctic.fuse.3", {"port": 1234}),
                ("script.skinvariables", {"port": 1234}),
            ])
        finally:
            MODULE.enable_addon = original

    def test_installed_addon_closure_walks_disposable_and_system_addons(self):
        # regression guard: a human validation session hit
        # ModuleNotFoundError (dateutil, dropbox) launching Backup Pro
        # normally, because install_dependencies() only copies files -
        # every freshly copied add-on still needs a separate enable
        # step, and enumerating each dependency id by hand is exactly
        # what this closure computation exists to replace.
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
               MODULE.KODI_LOG_FILE, MODULE.KODI_SYSTEM_ADDONS_DIR)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                d = Path(d)
                self._retarget(d / "root")

                system_dir = d / "fake-system-addons" / "script.module.bundled"
                system_dir.mkdir(parents=True)
                (system_dir / "addon.xml").write_text(
                    '<addon><requires></requires></addon>')
                MODULE.KODI_SYSTEM_ADDONS_DIR = d / "fake-system-addons"

                seed_dir = MODULE.KODI_ADDONS_DIR / "script.backup.pro"
                seed_dir.mkdir(parents=True)
                (seed_dir / "addon.xml").write_text(
                    '<addon><requires>'
                    '<import addon="script.module.dateutil" version="1.0.0"/>'
                    '<import addon="script.module.bundled" version="1.0.0"/>'
                    '</requires></addon>')
                dep_dir = MODULE.KODI_ADDONS_DIR / "script.module.dateutil"
                dep_dir.mkdir(parents=True)
                (dep_dir / "addon.xml").write_text(
                    '<addon><requires></requires></addon>')

                closure = MODULE._installed_addon_closure(["script.backup.pro"])
                self.assertEqual(
                    closure, ["script.backup.pro", "script.module.dateutil"])
        finally:
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
             MODULE.KODI_LOG_FILE, MODULE.KODI_SYSTEM_ADDONS_DIR) = old

    def test_installed_addon_closure_refuses_uninstalled_dependency(self):
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
               MODULE.KODI_LOG_FILE)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                self._retarget(Path(d) / "root")
                seed_dir = MODULE.KODI_ADDONS_DIR / "script.backup.pro"
                seed_dir.mkdir(parents=True)
                (seed_dir / "addon.xml").write_text(
                    '<addon><requires>'
                    '<import addon="script.module.missing" version="1.0.0"/>'
                    '</requires></addon>')
                with self.assertRaises(RuntimeError):
                    MODULE._installed_addon_closure(["script.backup.pro"])
        finally:
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
             MODULE.KODI_LOG_FILE) = old

    def test_enable_closure_enables_the_full_computed_closure(self):
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
               MODULE.KODI_LOG_FILE)
        calls = []

        def fake_enable_addon(addon_id, **kwargs):
            calls.append((addon_id, kwargs))
            return {"result": "OK"}

        original_enable = MODULE.enable_addon
        MODULE.enable_addon = fake_enable_addon
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                self._retarget(Path(d) / "root")
                seed_dir = MODULE.KODI_ADDONS_DIR / "script.backup.pro"
                seed_dir.mkdir(parents=True)
                (seed_dir / "addon.xml").write_text(
                    '<addon><requires>'
                    '<import addon="script.module.dateutil" version="1.0.0"/>'
                    '</requires></addon>')
                dep_dir = MODULE.KODI_ADDONS_DIR / "script.module.dateutil"
                dep_dir.mkdir(parents=True)
                (dep_dir / "addon.xml").write_text(
                    '<addon><requires></requires></addon>')

                enabled = MODULE.enable_closure(
                    ["script.backup.pro"], port=1234)
                self.assertEqual(
                    enabled, ["script.backup.pro", "script.module.dateutil"])
                self.assertEqual(calls, [
                    ("script.backup.pro", {"port": 1234}),
                    ("script.module.dateutil", {"port": 1234}),
                ])
        finally:
            MODULE.enable_addon = original_enable
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR,
             MODULE.KODI_LOG_FILE) = old

    def test_wait_for_ready_succeeds_after_retries(self):
        attempts = []

        def fake_jsonrpc(method, params=None, **kwargs):
            attempts.append(method)
            if len(attempts) < 3:
                raise RuntimeError("JSON-RPC request failed: connection refused")
            return {"result": "pong"}

        original_jsonrpc, original_sleep = MODULE.jsonrpc, MODULE.time.sleep
        MODULE.jsonrpc = fake_jsonrpc
        MODULE.time.sleep = lambda _seconds: None
        try:
            MODULE.wait_for_ready(timeout=5.0)
            self.assertEqual(attempts, ["JSONRPC.Ping"] * 3)
        finally:
            MODULE.jsonrpc, MODULE.time.sleep = original_jsonrpc, original_sleep

    def test_wait_for_ready_raises_after_timeout(self):
        def fake_jsonrpc(method, params=None, **kwargs):
            raise RuntimeError("JSON-RPC request failed: connection refused")

        original_jsonrpc, original_sleep = MODULE.jsonrpc, MODULE.time.sleep
        MODULE.jsonrpc = fake_jsonrpc
        MODULE.time.sleep = lambda _seconds: None
        try:
            with self.assertRaises(RuntimeError) as ctx:
                MODULE.wait_for_ready(timeout=0.05)
            self.assertIn("did not become ready", str(ctx.exception))
            self.assertIn("connection refused", str(ctx.exception))
        finally:
            MODULE.jsonrpc, MODULE.time.sleep = original_jsonrpc, original_sleep

    def test_wait_for_ready_rejects_a_non_pong_response(self):
        original_jsonrpc, original_sleep = MODULE.jsonrpc, MODULE.time.sleep
        MODULE.jsonrpc = lambda method, params=None, **kwargs: {"result": "unexpected"}
        MODULE.time.sleep = lambda _seconds: None
        try:
            with self.assertRaises(RuntimeError) as ctx:
                MODULE.wait_for_ready(timeout=0.05)
            self.assertIn("unexpected JSONRPC.Ping response", str(ctx.exception))
        finally:
            MODULE.jsonrpc, MODULE.time.sleep = original_jsonrpc, original_sleep

    def _patch_prepare_validation_steps(self, order, jsonrpc_responder):
        originals = {
            name: getattr(MODULE, name) for name in (
                "verify_isolation", "reset", "configure_webserver", "install",
                "install_dependencies", "install_skin", "configure", "launch",
                "wait_for_ready", "enable_closure", "stop", "jsonrpc",
            )
        }
        MODULE.verify_isolation = lambda: order.append("verify_isolation")
        MODULE.reset = lambda: order.append("reset")
        MODULE.configure_webserver = lambda **kw: order.append(("configure_webserver", kw))
        MODULE.install = lambda source: order.append(("install", source))
        MODULE.install_dependencies = lambda source: order.append(
            ("install_dependencies", source))
        MODULE.install_skin = lambda skin_id: order.append(("install_skin", skin_id))
        MODULE.configure = lambda addon_id, values: order.append(
            ("configure", addon_id, values))
        MODULE.launch = lambda: order.append("launch")
        MODULE.wait_for_ready = lambda **kw: order.append("wait_for_ready")

        def fake_enable_closure(seeds, **kw):
            order.append(("enable_closure", seeds))
            return seeds
        MODULE.enable_closure = fake_enable_closure
        MODULE.stop = lambda: order.append("stop")

        def fake_jsonrpc(method, params=None, **kwargs):
            order.append(("jsonrpc", method, params))
            return jsonrpc_responder(method, params)
        MODULE.jsonrpc = fake_jsonrpc
        return originals

    def _restore_prepare_validation_steps(self, originals):
        for name, fn in originals.items():
            setattr(MODULE, name, fn)

    def test_prepare_validation_follows_the_documented_safe_order(self):
        order = []

        def jsonrpc_responder(method, params):
            if method == "Addons.GetAddonDetails":
                return {"result": {"addon": {"enabled": True}}}
            if method == "Settings.GetSettingValue":
                return {"result": {"value": MODULE.AF3_SKIN_ID}}
            return {"result": "pong"}

        originals = self._patch_prepare_validation_steps(order, jsonrpc_responder)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                dest = Path(d) / "dest"
                report = MODULE.prepare_validation(destination=dest)
        finally:
            self._restore_prepare_validation_steps(originals)

        names = [item if isinstance(item, str) else item[0] for item in order]
        self.assertEqual(names, [
            "verify_isolation", "reset", "configure_webserver", "install",
            "install_dependencies", "install_skin", "configure", "launch",
            "wait_for_ready", "enable_closure", "stop", "launch",
            "wait_for_ready", "jsonrpc", "jsonrpc", "jsonrpc",
        ])
        # configure_webserver runs before the first launch, with AF3 active
        self.assertEqual(order[2][1], {
            "extra_settings": {"lookandfeel.skin": MODULE.AF3_SKIN_ID}})
        self.assertLess(order.index(order[2]), order.index("launch"))
        # enable_closure computes the closure itself - no hand-enumerated ids
        self.assertEqual(order[9][1], [MODULE.ADDON_ID, MODULE.AF3_SKIN_ID])
        # enable_closure only runs after the first wait_for_ready succeeds
        self.assertEqual(order[8], "wait_for_ready")
        self.assertEqual(order[9][0], "enable_closure")
        self.assertEqual(report["active_skin"], MODULE.AF3_SKIN_ID)
        self.assertEqual(report["destination"], str(dest))
        self.assertEqual(report["enabled"], [MODULE.ADDON_ID, MODULE.AF3_SKIN_ID])

    def test_prepare_validation_fails_closed_if_an_addon_is_not_enabled(self):
        order = []

        def jsonrpc_responder(method, params):
            if method == "Addons.GetAddonDetails":
                return {"result": {"addon": {"enabled": False}}}
            if method == "Settings.GetSettingValue":
                return {"result": {"value": MODULE.AF3_SKIN_ID}}
            return {"result": "pong"}

        originals = self._patch_prepare_validation_steps(order, jsonrpc_responder)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                with self.assertRaises(RuntimeError) as ctx:
                    MODULE.prepare_validation(destination=Path(d) / "dest")
                self.assertIn("is not enabled", str(ctx.exception))
        finally:
            self._restore_prepare_validation_steps(originals)

    def test_prepare_validation_fails_closed_if_active_skin_is_wrong(self):
        order = []

        def jsonrpc_responder(method, params):
            if method == "Addons.GetAddonDetails":
                return {"result": {"addon": {"enabled": True}}}
            if method == "Settings.GetSettingValue":
                return {"result": {"value": "skin.estuary"}}
            return {"result": "pong"}

        originals = self._patch_prepare_validation_steps(order, jsonrpc_responder)
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                with self.assertRaises(RuntimeError) as ctx:
                    MODULE.prepare_validation(destination=Path(d) / "dest")
                self.assertIn("active skin is", str(ctx.exception))
        finally:
            self._restore_prepare_validation_steps(originals)

    def test_install_authorized_network_package_refuses_unauthorized_package(self):
        with self.assertRaises(RuntimeError):
            MODULE.install_authorized_network_package("script.module.something-else")

    def test_install_authorized_network_package_refuses_unexpected_repo_before(self):
        def fake_jsonrpc(method, params=None, **kwargs):
            self.assertEqual(method, "Addons.GetAddons")
            return {"result": {"addons": [
                {"addonid": "repository.xbmc.org"},
                {"addonid": "repository.some-third-party"},
            ]}}

        original = MODULE.jsonrpc
        MODULE.jsonrpc = fake_jsonrpc
        try:
            with self.assertRaises(RuntimeError):
                MODULE.install_authorized_network_package("script.module.pil")
        finally:
            MODULE.jsonrpc = original

    def test_install_authorized_network_package_refuses_unexpected_repo_after(self):
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR, MODULE.KODI_LOG_FILE)
        calls = {"n": 0}

        def fake_jsonrpc(method, params=None, **kwargs):
            if method == "Addons.GetAddons":
                calls["n"] += 1
                # clean before the install, a rogue repository after it
                addons = [{"addonid": "repository.xbmc.org"}]
                if calls["n"] > 1:
                    addons.append({"addonid": "repository.rogue"})
                return {"result": {"addons": addons}}
            if method == "Addons.SetAddonEnabled":
                return {"result": "OK"}
            if method == "Addons.ExecuteAddon":
                return {"result": "OK"}
            raise AssertionError(f"unexpected method: {method}")

        original = MODULE.jsonrpc
        MODULE.jsonrpc = fake_jsonrpc
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                self._retarget(Path(d) / "root")
                with self.assertRaises(RuntimeError):
                    MODULE.install_authorized_network_package("script.module.pil")
        finally:
            MODULE.jsonrpc = original
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR, MODULE.KODI_LOG_FILE) = old

    def test_install_authorized_network_package_writes_installer_and_triggers_it(self):
        old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
               MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR, MODULE.KODI_LOG_FILE)
        captured = []

        def fake_jsonrpc(method, params=None, **kwargs):
            captured.append((method, params))
            if method == "Addons.GetAddons":
                return {"result": {"addons": [{"addonid": "repository.xbmc.org"}]}}
            if method == "Addons.SetAddonEnabled":
                return {"result": "OK"}
            if method == "Addons.ExecuteAddon":
                return {"result": "OK"}
            raise AssertionError(f"unexpected method: {method}")

        original = MODULE.jsonrpc
        MODULE.jsonrpc = fake_jsonrpc
        try:
            with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
                self._retarget(Path(d) / "root")
                result = MODULE.install_authorized_network_package("script.module.pil")
                self.assertEqual(result["result"], "OK")

                installer_dir = MODULE.KODI_ADDONS_DIR / MODULE.INSTALLER_ADDON_ID
                self.assertTrue((installer_dir / "addon.xml").exists())
                default_py = (installer_dir / "default.py").read_text(encoding="utf-8")
                self.assertIn("InstallAddon", default_py)

                methods = [m for m, _p in captured]
                self.assertEqual(methods.count("Addons.GetAddons"), 2)
                self.assertIn("Addons.SetAddonEnabled", methods)
                execute_calls = [p for m, p in captured if m == "Addons.ExecuteAddon"]
                self.assertEqual(len(execute_calls), 1)
                self.assertEqual(execute_calls[0]["addonid"], MODULE.INSTALLER_ADDON_ID)
                self.assertEqual(execute_calls[0]["params"], ["addon=script.module.pil"])
        finally:
            MODULE.jsonrpc = original
            (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
             MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR, MODULE.KODI_LOG_FILE) = old

    def test_install_allowlist_excludes_development_files(self):
        with tempfile.TemporaryDirectory(dir=MODULE.PROJECT) as d:
            source = Path(d) / "addon"
            source.mkdir()
            (source / "addon.xml").write_text("<addon id='script.backup.pro'/>")
            (source / "default.py").write_text("# test\n")
            (source / "tests").mkdir()
            (source / "tests" / "secret").write_text("x")
            old = (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
                   MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR, MODULE.KODI_LOG_FILE)
            try:
                self._retarget(Path(d) / "root")
                MODULE.install(source)
                target = MODULE.KODI_ADDONS_DIR / MODULE.ADDON_ID
                self.assertTrue((target / "addon.xml").exists())
                self.assertFalse((target / "tests").exists())
            finally:
                (MODULE.ROOT, MODULE.HOME, MODULE.KODI_APPDATA_DIR,
                 MODULE.KODI_USERDATA_DIR, MODULE.KODI_ADDONS_DIR, MODULE.KODI_LOG_FILE) = old


if __name__ == "__main__":
    unittest.main()
