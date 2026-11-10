"""No physical print jobs are submitted by this test suite."""
import importlib.util
import subprocess
from pathlib import Path
from unittest import TestCase, mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("hermes_print_tools_under_test", ROOT / "__init__.py")
plugin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plugin)


def cp(output, code=0, error=""):
    return subprocess.CompletedProcess([], code, output.encode(), error.encode())


class TestQueues(TestCase):
    def setUp(self):
        plugin._config_getter = lambda name, default="": ""

    def fake_run(self, cmd, **kwargs):
        if cmd[0].endswith("lpstat"):
            if "-p" in cmd and len(cmd) == 2:
                return cp("printer Office is idle. enabled since today\n"
                          "printer Upstairs is idle. enabled since today\n")
            if "-d" in cmd:
                return cp("system default destination: Office\n")
            if "-p" in cmd:
                return cp(f"printer {cmd[-1]} is idle\n")
            if "-o" in cmd:
                return cp("")
        if cmd[0].endswith("lp"):
            return cp("request id is Office-13 (1 file(s))\n")
        raise AssertionError(cmd)

    def test_default_and_explicit_printer(self):
        with mock.patch.object(plugin, "_run", side_effect=self.fake_run):
            self.assertEqual(plugin._resolve_printer(), "Office")
            self.assertEqual(plugin._resolve_printer("Upstairs"), "Upstairs")
            with self.assertRaisesRegex(ValueError, "Unknown"):
                plugin._resolve_printer("Unrecognized")

    def test_plugin_config_precedes_cups_default(self):
        plugin._config_getter = lambda name, default="": "Upstairs"
        with mock.patch.object(plugin, "_run", side_effect=self.fake_run):
            self.assertEqual(plugin._resolve_printer(), "Upstairs")
            self.assertEqual(plugin._resolve_printer("Office"), "Office")

    def test_no_default_and_multiple_queues_fails(self):
        def none_default(cmd, **kwargs):
            if "-d" in cmd:
                return cp("no system default destination\n", 1)
            return self.fake_run(cmd, **kwargs)
        with mock.patch.object(plugin, "_run", side_effect=none_default):
            with self.assertRaisesRegex(ValueError, "Multiple"):
                plugin._resolve_printer()

    def test_unknown_printer_never_submits(self):
        with mock.patch.object(plugin, "_installed_printers", return_value=["Office"]), \
             mock.patch.object(plugin, "_system_default", return_value="Office"), \
             mock.patch.object(plugin, "_print_jpeg") as print_job:
            result = plugin._handle_print_letter({"title": "Hi", "body": "Hello", "printer": "Nonexistent"})
            self.assertIn("Unknown", result)
            print_job.assert_not_called()

    def test_path_security_and_copy_bounds(self):
        self.assertEqual(plugin._copies("5"), 5)
        for bad in (0, 6, 1.5, True, "-2", "no"):
            with self.assertRaises(ValueError):
                plugin._copies(bad)
        with self.assertRaisesRegex(ValueError, "home or temp"):
            plugin._local_path("/etc/passwd")

    def test_queue_status(self):
        with mock.patch.object(plugin, "_run", side_effect=self.fake_run):
            status = plugin._handle_printer_status({})
            self.assertIn("Office", status)
            self.assertIn("Upstairs", status)

    def test_file_unsupported_rejected(self):
        with mock.patch.object(plugin, "_run", side_effect=self.fake_run):
            result = plugin._handle_print_file({
                "path": str(ROOT / "LICENSE"), "printer": "Upstairs",
            })
            self.assertIn("Unsupported file type", result)

    def test_file_jpeg_submits_one_job(self):
        with mock.patch.object(plugin, "_run", side_effect=self.fake_run):
            with mock.patch.object(plugin, "_local_path", return_value=Path("/tmp/image.jpg")):
                result = plugin._handle_print_file({"path": "/tmp/image.jpg"})
            self.assertIn("Office-13", result)

    def test_register_and_schema_consistency(self):
        seen = {}
        class Ctx:
            def get_config(self, name, default=""):
                return default
            def register_tool(self, **kwargs):
                seen[kwargs["name"]] = kwargs
        plugin.register(Ctx())
        self.assertEqual(set(seen), set(plugin.SCHEMAS))
        for name, entry in seen.items():
            self.assertEqual(entry["toolset"], "print-tools")
            self.assertEqual(entry["schema"]["name"], name)


class TestRenderer(TestCase):
    def test_valid_and_overlong_letter(self):
        from PIL import Image
        from tempfile import TemporaryDirectory
        spec = importlib.util.spec_from_file_location("letter_renderer", ROOT / "letter_renderer.py")
        renderer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(renderer)
        with TemporaryDirectory() as temp:
            jpg, pdf = Path(temp) / "a.jpg", Path(temp) / "a.pdf"
            info = renderer.render({
                "title": "A Message For You",
                "body": "Hello world.\n\nThis is a test letter.\n\nWarm wishes.",
                "signature": "Hermes",
            }, jpg, pdf)
            self.assertEqual(info["pages"], 1)
            self.assertGreater(info["body_lines"], 2)
            with Image.open(jpg) as im:
                self.assertEqual(im.size, (2550, 3300))
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF-"))
            with self.assertRaises(ValueError):
                renderer.render({
                    "title": "A Message",
                    "body": "word " * 1000,
                    "signature": "Hermes",
                }, jpg, pdf)
