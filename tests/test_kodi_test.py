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
