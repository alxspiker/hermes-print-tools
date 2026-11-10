"""No physical print jobs are submitted by this test suite."""
import importlib.util
import json
import os
import pwd
import subprocess
import sys
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

    def test_real_child_process_does_not_inherit_credentials(self):
        """Execute a harmless Python child, never a CUPS or printing command."""
        injected = {
            "OPENAI_API_KEY": "FAKE-SECRET-DO-NOT-FORWARD",
            "ANTHROPIC_API_KEY": "FAKE-SECOND-SECRET",
            "CUPS_SERVER": "malicious-host.example",
            "PATH": "/tmp/unsafe-test-bin",
            "HOME": "/tmp/unsafe-test-home",
        }
        with mock.patch.dict(os.environ, injected):
            result = plugin._run([
                sys.executable, "-c",
                "import json,os; print(json.dumps(dict(os.environ)))",
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            child = json.loads(result.stdout)
        self.assertEqual(set(child), {"PATH", "HOME", "LANG"})
        self.assertEqual(child["HOME"], pwd.getpwuid(os.getuid()).pw_dir)
        self.assertEqual(child["PATH"], plugin._SAFE_PATH)
        for secret in injected.values():
            self.assertNotIn(secret, child.values())

    def test_command_lookup_rejects_inherited_path(self):
        with mock.patch.dict(os.environ, {"PATH": "/tmp/unsafe-test-bin"}):
            with mock.patch.object(plugin.shutil, "which", return_value="/usr/bin/lp") as which:
                self.assertEqual(plugin._binary("lp"), "/usr/bin/lp")
            which.assert_called_once_with("lp", path=plugin._SAFE_PATH)

    def test_letter_pipeline_renders_without_touching_printer(self):
        """Real Pillow renderer; only print submission and status are mocked."""
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "FAKE-SECRET"}), \
             mock.patch.object(plugin, "_resolve_printer", return_value="Office"), \
             mock.patch.object(plugin, "_print_jpeg", return_value="Office-42") as submit, \
             mock.patch.object(plugin, "_printer_status", return_value="mock queue"):
            outcome = plugin._handle_print_letter({
                "title": "Security regression test",
                "body": "First paragraph tests layout.\n\nSecond paragraph tests rendering.",
                "signature": "Hermes",
                "style": "classic",
            })
        self.assertIn("Office-42", outcome)
        submit.assert_called_once()


class TestRenderer(TestCase):
    def test_fontconfig_gets_minimal_environment(self):
        spec = importlib.util.spec_from_file_location("renderer_for_env_test", ROOT / "letter_renderer.py")
        renderer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(renderer)
        fake = subprocess.CompletedProcess([], 0,
                                           "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf\n", "")
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "FAKE-SECRET"}), \
             mock.patch.object(renderer.shutil, "which", return_value="/usr/bin/fc-match") as which, \
             mock.patch.object(renderer.subprocess, "run", return_value=fake) as run:
            renderer.font("sans", 39)
        run.assert_called_once()
        env = run.call_args.kwargs["env"]
        self.assertEqual(set(env), {"PATH", "HOME", "LANG"})
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertEqual(env["HOME"], pwd.getpwuid(os.getuid()).pw_dir)
        which.assert_called_once_with("fc-match", path=renderer._SAFE_PATH)

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
