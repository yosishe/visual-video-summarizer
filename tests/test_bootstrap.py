"""bootstrap.py: user-level setup that installs only into its own directory, verifies what it downloads,
and is what the workflow controller runs before blocking on a missing tool."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import bootstrap  # noqa: E402
import doctor  # noqa: E402
import hostenv  # noqa: E402
import workflow  # noqa: E402

READY = {"ready": True, "checks": [{"name": n, "required": True, "available": True}
                                   for n in ("Python", "ffmpeg", "ffprobe", "yt-dlp")]
         + [{"name": "Pillow", "required": False, "available": True}]}
MISSING_ALL = {"ready": False, "checks": [{"name": "Python", "required": True, "available": True},
                                          {"name": "ffmpeg", "required": True, "available": False},
                                          {"name": "ffprobe", "required": True, "available": False},
                                          {"name": "yt-dlp", "required": True, "available": False},
                                          {"name": "Pillow", "required": False, "available": False}]}


def _completed(code: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=out, stderr=err)


class ManagedHome(unittest.TestCase):
    """Every test points SUMMARIZE_VIDEO_HOME at a fresh temporary directory."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="vsum-bootstrap-")
        self.home = Path(self.temporary.name) / "managed"
        self.env = mock.patch.dict(os.environ, {hostenv.MANAGED_HOME_ENV: str(self.home)})
        self.env.start()
        self.original_sys_path = list(sys.path)

    def tearDown(self):
        sys.path[:] = self.original_sys_path
        self.env.stop()
        self.temporary.cleanup()


class ManagedDirectoryTests(ManagedHome):
    def test_managed_home_honours_the_override_and_is_not_created_by_asking(self):
        self.assertEqual(hostenv.managed_home(), self.home)
        self.assertEqual(hostenv.managed_bin_dir(), self.home / "bin")
        self.assertTrue(str(hostenv.managed_site_dir()).startswith(str(self.home / "site")))
        self.assertFalse(self.home.exists())

    def test_site_dir_is_per_interpreter(self):
        tag = hostenv.interpreter_tag()
        self.assertIn(str(sys.version_info[0]), tag)
        self.assertEqual(hostenv.managed_site_dir().name, tag)
        self.assertNotIn(".", tag.split("-", 1)[1])

    def test_activate_prepends_only_existing_directories_once(self):
        env = {"PATH": "/usr/bin", "PYTHONPATH": ""}
        self.assertEqual(hostenv.activate_managed_tools(env)["PATH"], "/usr/bin")
        hostenv.managed_bin_dir().mkdir(parents=True)
        hostenv.managed_site_dir().mkdir(parents=True)
        env = {"PATH": "/usr/bin"}
        hostenv.activate_managed_tools(env)
        hostenv.activate_managed_tools(env)
        self.assertEqual(env["PATH"].split(os.pathsep), [str(hostenv.managed_bin_dir()), "/usr/bin"])
        self.assertEqual(env["PYTHONPATH"], str(hostenv.managed_site_dir()))
        # in-process: PATH updated, the site directory appended after the interpreter's own packages
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin"}):
            hostenv.activate_managed_tools()
            self.assertTrue(os.environ["PATH"].startswith(str(hostenv.managed_bin_dir())))
        self.assertEqual(sys.path[-1], str(hostenv.managed_site_dir()))
        child = hostenv.child_env()
        self.assertTrue(child["PATH"].startswith(str(hostenv.managed_bin_dir())))
        self.assertIn(str(hostenv.managed_site_dir()), child["PYTHONPATH"])

    def test_doctor_reports_the_user_level_directory(self):
        result = doctor.check()
        row = next(r for r in result["checks"] if r["name"] == "user-level tools")
        self.assertFalse(row["required"])
        self.assertFalse(row["available"])
        self.assertEqual(row["path"], str(self.home))
        self.assertEqual(result["managed_home"], str(self.home))
        self.assertIn("bootstrap.py", row["note"])

    def test_install_hint_names_the_user_level_setup_first(self):
        hint = hostenv.install_hint("ffmpeg")
        self.assertIn("bootstrap.py", hint)
        self.assertIn("no admin rights", hint)
        self.assertIn("after the user approves", hint)  # the package-manager route still needs approval


class PinTests(unittest.TestCase):
    def test_every_pinned_archive_has_a_full_sha256_and_size(self):
        archives = bootstrap.FFMPEG_RELEASE["archives"]
        self.assertEqual(set(archives), {"linux-x86_64", "linux-arm64", "darwin-x86_64", "darwin-arm64", "win32-x86_64"})
        for entry in archives.values():
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(entry["size"], 10 * 1024 * 1024)
            self.assertTrue(entry["file"].endswith(".zip"))
        self.assertRegex(bootstrap.FFMPEG_RELEASE["commit"], r"^[0-9a-f]{40}$")

    def test_archive_url_is_pinned_to_the_commit_not_a_branch(self):
        url = bootstrap.ffmpeg_archive_url(bootstrap.FFMPEG_RELEASE["archives"]["linux-x86_64"])
        self.assertIn(bootstrap.FFMPEG_RELEASE["commit"], url)
        self.assertTrue(url.startswith("https://github.com/zackees/ffmpeg_bins/raw/"))
        self.assertNotIn("/main/", url)

    def test_archive_key_covers_the_five_builds_and_refuses_others(self):
        with mock.patch.object(bootstrap, "platform_key", return_value="linux"):
            self.assertEqual(bootstrap.archive_key("x86_64"), "linux-x86_64")
            self.assertEqual(bootstrap.archive_key("aarch64"), "linux-arm64")
            self.assertIsNone(bootstrap.archive_key("riscv64"))
        with mock.patch.object(bootstrap, "platform_key", return_value="darwin"):
            self.assertEqual(bootstrap.archive_key("arm64"), "darwin-arm64")
            self.assertEqual(bootstrap.archive_key("x86_64"), "darwin-x86_64")
        with mock.patch.object(bootstrap, "platform_key", return_value="win32"):
            self.assertEqual(bootstrap.archive_key("AMD64"), "win32-x86_64")
            self.assertIsNone(bootstrap.archive_key("ARM64"))  # no pinned Windows ARM build: hint, not a guess


def _fake_archive(names: dict[str, bytes], folder: str = "linux") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{folder}/README.txt", "fixture")
        for name, data in names.items():
            archive.writestr(f"{folder}/{name}", data)
    return buffer.getvalue()


def _entry_for(data: bytes) -> dict:
    return {"file": "fixture.zip", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


class FfmpegInstallTests(ManagedHome):
    def setUp(self):
        super().setUp()
        self.names = bootstrap._executable_names()
        script = (b"@echo off\r\necho ffmpeg version fixture\r\n" if os.name == "nt"
                  else b"#!/bin/sh\necho 'ffmpeg version fixture'\n")
        self.archive = _fake_archive({self.names["ffmpeg"]: script, self.names["ffprobe"]: script})
        self.entry = _entry_for(self.archive)
        self.fetched: list[tuple[str, int]] = []

    def fetch(self, url: str, size: int) -> bytes:
        self.fetched.append((url, size))
        return self.archive

    def test_verified_archive_is_extracted_into_the_managed_bin(self):
        receipt = bootstrap.install_ffmpeg(entry=self.entry, fetch=self.fetch, verify=lambda p: f"{p.name} fixture")
        bin_dir = hostenv.managed_bin_dir()
        for tool, filename in self.names.items():
            self.assertTrue((bin_dir / filename).is_file(), filename)
            if os.name != "nt":
                self.assertTrue(os.access(bin_dir / filename, os.X_OK))
        self.assertEqual(receipt["sha256"], self.entry["sha256"])
        self.assertEqual(receipt["method"], "static-build")
        self.assertEqual(self.fetched[0][1], len(self.archive))
        self.assertIn(bootstrap.FFMPEG_RELEASE["commit"], self.fetched[0][0])
        if os.name != "nt":  # the extracted file is what runs
            self.assertIn("fixture", bootstrap._version_line(bin_dir / "ffmpeg"))
        self.assertNotIn("README.txt", os.listdir(bin_dir))

    def test_sha256_mismatch_installs_nothing(self):
        bad = dict(self.entry, sha256="0" * 64)
        with self.assertRaises(bootstrap.BootstrapError) as ctx:
            bootstrap.install_ffmpeg(entry=bad, fetch=self.fetch, verify=lambda p: "x")
        self.assertIn("SHA-256", ctx.exception.message)
        self.assertFalse(hostenv.managed_bin_dir().exists())

    def test_size_mismatch_installs_nothing(self):
        bad = dict(self.entry, size=self.entry["size"] + 1)
        with self.assertRaises(bootstrap.BootstrapError) as ctx:
            bootstrap.install_ffmpeg(entry=bad, fetch=self.fetch, verify=lambda p: "x")
        self.assertIn("size", ctx.exception.message)
        self.assertFalse(hostenv.managed_bin_dir().exists())

    def test_archive_without_ffprobe_is_refused(self):
        archive = _fake_archive({self.names["ffmpeg"]: b"#!/bin/sh\n"})
        with self.assertRaises(bootstrap.BootstrapError) as ctx:
            bootstrap.install_ffmpeg(entry=_entry_for(archive), fetch=lambda u, s: archive, verify=lambda p: "x")
        self.assertIn("ffprobe", ctx.exception.message)
        self.assertFalse((hostenv.managed_bin_dir() / self.names["ffmpeg"]).exists())

    def test_download_over_the_pinned_size_is_cut_off(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        oversized = Response(b"x" * (bootstrap.DOWNLOAD_SLACK + 64))
        with mock.patch.object(bootstrap, "urlopen", return_value=oversized):
            with self.assertRaises(bootstrap.BootstrapError) as ctx:
                bootstrap.fetch_bytes("https://github.com/x/y/raw/abc/v8.0/linux.zip", expected_size=32)
        self.assertIn("exceeded", ctx.exception.message)

    def test_proxy_denial_is_named_as_the_environment_not_the_download(self):
        from urllib.error import URLError
        with mock.patch.object(bootstrap, "urlopen", side_effect=URLError(OSError("Tunnel connection failed: 403 Forbidden"))):
            with self.assertRaises(bootstrap.BootstrapError) as ctx:
                bootstrap.fetch_bytes("https://github.com/x/y/raw/abc/v8.0/linux.zip", expected_size=32)
        self.assertIn("network policy", ctx.exception.message)
        self.assertIn("github.com", ctx.exception.message)

    def test_unsupported_platform_gets_the_hint_instead_of_a_download(self):
        with mock.patch.object(bootstrap, "archive_key", return_value=None):
            with self.assertRaises(bootstrap.BootstrapError) as ctx:
                bootstrap.install_ffmpeg(fetch=self.fetch)
        self.assertEqual(self.fetched, [])
        self.assertIn("no pinned static build", ctx.exception.message)


class PipAndLauncherTests(ManagedHome):
    def test_pip_install_targets_the_per_interpreter_directory_with_wheels_only(self):
        calls: list[list[str]] = []

        def runner(command, **kwargs):
            calls.append(list(command))
            return _completed(0, "pip 24.0" if "--version" in command else "")

        receipt = bootstrap.pip_install(["Pillow>=10"], runner=runner)
        install = calls[-1]
        self.assertEqual(install[:4], [sys.executable, "-m", "pip", "install"])
        self.assertEqual(install[install.index("--target") + 1], str(hostenv.managed_site_dir()))
        self.assertIn("--only-binary", install)
        self.assertIn("--no-input", install)
        self.assertNotIn("--user", install)
        self.assertNotIn("--break-system-packages", install)
        self.assertEqual(receipt["step"], "pillow")
        self.assertTrue(hostenv.managed_site_dir().is_dir())

    def test_pip_failure_is_reported_with_a_sanitised_tail_and_no_traceback(self):
        def runner(command, **kwargs):
            if "--version" in command:
                return _completed(0, "pip")
            return _completed(1, "", "ERROR: No matching distribution https://files.example/secret?token=abc\n")

        with self.assertRaises(bootstrap.BootstrapError) as ctx:
            bootstrap.pip_install(["yt-dlp"], runner=runner)
        self.assertEqual(ctx.exception.step, "yt-dlp")
        self.assertIn("No matching distribution", ctx.exception.message)
        self.assertNotIn("token=abc", ctx.exception.message)

    def test_missing_pip_is_a_clear_message(self):
        with self.assertRaises(bootstrap.BootstrapError) as ctx:
            bootstrap.pip_install(["yt-dlp"], runner=lambda c, **k: _completed(1))
        self.assertIn("pip is not available", ctx.exception.message)

    def test_launcher_runs_the_target_install_with_this_interpreter(self):
        site = hostenv.managed_site_dir()
        (site / "yt_dlp").mkdir(parents=True)
        (site / "yt_dlp" / "__init__.py").write_text(
            "import sys\n\ndef main(argv=None):\n    print('fixture yt-dlp', *sys.argv[1:])\n    return 0\n",
            encoding="utf-8")
        if os.name == "nt" and bootstrap._windows_launcher_stub() is None:
            with self.assertRaises(bootstrap.BootstrapError) as ctx:
                bootstrap.write_ytdlp_launcher()
            self.assertIn("winget", ctx.exception.message)
            self.skipTest("this pip ships no console-script launcher; the refusal path is what was tested")
        launcher = bootstrap.write_ytdlp_launcher()
        self.assertEqual(launcher.parent, hostenv.managed_bin_dir())
        env = hostenv.child_env()
        found = __import__("shutil").which("yt-dlp", path=env["PATH"])
        self.assertIsNotNone(found)
        self.assertEqual(Path(found).resolve(), launcher.resolve())
        # Resolved explicitly: on Windows CreateProcess searches the parent's PATH, not the child env's.
        # The stage scripts run with child_env() as their own environment, so the bare name works there.
        result = subprocess.run([found, "--version"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fixture yt-dlp --version", result.stdout)


class PlanAndRunTests(ManagedHome):
    def test_plan_orders_steps_and_respects_skip_optional(self):
        self.assertEqual(bootstrap.plan(MISSING_ALL), ["ffmpeg", "yt-dlp", "pillow"])
        self.assertEqual(bootstrap.plan(MISSING_ALL, skip_optional=True), ["ffmpeg", "yt-dlp"])
        self.assertEqual(bootstrap.plan(READY), [])
        only_probe = {"checks": [{"name": "ffmpeg", "required": True, "available": True},
                                 {"name": "ffprobe", "required": True, "available": False},
                                 {"name": "yt-dlp", "required": False, "available": False}]}
        self.assertEqual(bootstrap.plan(only_probe, skip_optional=True), ["ffmpeg"])
        self.assertEqual(bootstrap.plan(only_probe), ["ffmpeg", "yt-dlp"])

    def test_run_records_failures_and_keeps_going_without_raising(self):
        snapshots = iter([dict(MISSING_ALL), dict(READY)])
        with mock.patch.object(doctor, "check", side_effect=lambda **k: next(snapshots)), \
             mock.patch.object(bootstrap, "install_ffmpeg", side_effect=bootstrap.BootstrapError("ffmpeg", "offline")), \
             mock.patch.object(bootstrap, "install_ytdlp", return_value={"step": "yt-dlp", "method": "pip --target"}), \
             mock.patch.object(bootstrap, "install_pillow", return_value={"step": "pillow", "method": "pip --target"}), \
             mock.patch.object(bootstrap, "install_ffmpeg_system") as system:
            result = bootstrap.run()
        system.assert_not_called()  # never without --system
        self.assertEqual(result["planned"], ["ffmpeg", "yt-dlp", "pillow"])
        self.assertEqual([r["step"] for r in result["installed"]], ["yt-dlp", "pillow"])
        self.assertEqual(result["errors"], [{"step": "ffmpeg", "message": "offline"}])
        receipt = json.loads((self.home / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual([r["step"] for r in receipt["installs"]], ["yt-dlp", "pillow"])

    def test_system_fallback_only_with_the_flag(self):
        snapshots = iter([dict(MISSING_ALL), dict(READY)])
        with mock.patch.object(doctor, "check", side_effect=lambda **k: next(snapshots)), \
             mock.patch.object(bootstrap, "install_ffmpeg", side_effect=bootstrap.BootstrapError("ffmpeg", "offline")), \
             mock.patch.object(bootstrap, "install_ytdlp", return_value={"step": "yt-dlp", "method": "pip --target"}), \
             mock.patch.object(bootstrap, "install_pillow", return_value={"step": "pillow", "method": "pip --target"}), \
             mock.patch.object(bootstrap, "install_ffmpeg_system",
                               return_value={"step": "ffmpeg", "method": "system-package"}) as system:
            result = bootstrap.run(system=True)
        system.assert_called_once()
        self.assertEqual(result["errors"], [])
        self.assertEqual([r["step"] for r in result["installed"]], ["ffmpeg", "yt-dlp", "pillow"])

    def test_dry_run_installs_nothing(self):
        with mock.patch.object(doctor, "check", return_value=dict(MISSING_ALL)), \
             mock.patch.object(bootstrap, "install_ffmpeg") as ffmpeg, \
             mock.patch.object(bootstrap, "pip_install") as pip:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = bootstrap.main(["--dry-run", "--json"])
        ffmpeg.assert_not_called()
        pip.assert_not_called()
        self.assertEqual(code, 1)
        payload = json.loads(buffer.getvalue())
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["planned"], ["ffmpeg", "yt-dlp", "pillow"])
        self.assertFalse(self.home.exists())

    def test_system_install_needs_a_privileged_runner_on_linux(self):
        with mock.patch.object(bootstrap, "platform_key", return_value="linux"), \
             mock.patch.object(bootstrap.shutil, "which", side_effect=lambda n: "/usr/bin/apt-get" if n == "apt-get" else None), \
             mock.patch.object(bootstrap, "_privileged_prefix", return_value=None):
            with self.assertRaises(bootstrap.BootstrapError) as ctx:
                bootstrap.install_ffmpeg_system(runner=lambda *a, **k: _completed(0))
        self.assertIn("sudo apt-get install -y ffmpeg", ctx.exception.message)
        calls = []
        with mock.patch.object(bootstrap, "platform_key", return_value="linux"), \
             mock.patch.object(bootstrap.shutil, "which", side_effect=lambda n: "/usr/bin/apt-get" if n == "apt-get" else None), \
             mock.patch.object(bootstrap, "_privileged_prefix", return_value=[]):
            receipt = bootstrap.install_ffmpeg_system(runner=lambda c, **k: (calls.append(list(c)), _completed(0))[1])
        self.assertEqual(calls, [["apt-get", "update"], ["apt-get", "install", "-y", "ffmpeg"]])
        self.assertEqual(receipt["method"], "system-package")


class WorkflowSetupTests(ManagedHome):
    """The controller installs before it blocks, records what happened, and can be told not to."""

    def setUp(self):
        super().setUp()
        self.work_dir = tempfile.TemporaryDirectory(prefix="vsum-wf-setup-")
        self.work = Path(self.work_dir.name) / "work"
        self.invoke = mock.patch.object(workflow, "_invoke", return_value=(0, "", ""))
        self.invoke.start()
        self.no_env = mock.patch.dict(os.environ, {"SUMMARIZE_VIDEO_NO_SETUP": ""})
        self.no_env.start()

    def tearDown(self):
        self.no_env.stop()
        self.invoke.stop()
        self.work_dir.cleanup()
        super().tearDown()

    def wf(self, *argv, work: Path | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = workflow.main([*argv, "--work", str(work or self.work)]) or 0
            except SystemExit as exc:
                code = int(exc.code) if isinstance(exc.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def run_json(self) -> dict:
        return json.loads((self.work / "run.json").read_text(encoding="utf-8"))

    def test_init_installs_missing_tools_and_records_the_receipt(self):
        snapshots = iter([dict(MISSING_ALL), dict(READY)])
        with mock.patch.object(doctor, "check", side_effect=lambda **k: next(snapshots)), \
             mock.patch.object(bootstrap, "run", return_value={"ready": True, "errors": [], "installed": [
                 {"step": "ffmpeg", "method": "static-build", "version": None},
                 {"step": "yt-dlp", "method": "pip --target", "version": "2026.09.01"}]}) as setup:
            code, _, err = self.wf("init", "https://www.youtube.com/watch?v=vid", "--lang", "en")
        self.assertEqual(code, 0)
        setup.assert_called_once_with(local=False, pdf=False)
        self.assertIn("installing missing tools for this user", err)
        run = self.run_json()
        self.assertTrue(run["doctor"]["ready"])
        self.assertEqual(run["setup"]["by"], "init")
        self.assertEqual([s["step"] for s in run["setup"]["installed"]], ["ffmpeg", "yt-dlp"])
        self.assertEqual(run["setup"]["managed_home"], str(self.home))
        self.assertFalse(run["request"]["no_setup"])

    def test_no_setup_flag_and_environment_variable_skip_the_install(self):
        with mock.patch.object(doctor, "check", return_value=dict(MISSING_ALL)), \
             mock.patch.object(bootstrap, "run") as setup:
            code, _, err = self.wf("init", "https://www.youtube.com/watch?v=vid", "--no-setup")
            self.assertEqual(code, 0)
            setup.assert_not_called()
            self.assertIn("automatic setup is off", err)
            self.assertTrue(self.run_json()["request"]["no_setup"])
            code, _, _ = self.wf("init", "https://www.youtube.com/watch?v=vid", "--force")
            self.assertEqual(code, 0)
            self.assertTrue(self.run_json()["request"]["no_setup"])
            code, out, _ = self.wf("run")
            self.assertEqual(code, 1)
            setup.assert_not_called()
            self.assertIn("NEXT (preflight, blocked)", out)
            self.assertIn("Automatic setup is off", out)
        with mock.patch.object(doctor, "check", return_value=dict(MISSING_ALL)), \
             mock.patch.object(bootstrap, "run") as setup, \
             mock.patch.dict(os.environ, {"SUMMARIZE_VIDEO_NO_SETUP": "1"}):
            code, _, err = self.wf("init", "https://www.youtube.com/watch?v=vid2", work=self.work.parent / "other")
            self.assertEqual(code, 0)
            setup.assert_not_called()
            self.assertIn("automatic setup is off", err)

    def test_preflight_installs_then_proceeds_or_blocks_with_the_setup_error(self):
        with mock.patch.object(doctor, "check", return_value=dict(READY)):
            self.assertEqual(self.wf("init", "https://www.youtube.com/watch?v=vid")[0], 0)
        run = self.run_json()
        run["doctor"]["ready"] = False
        (self.work / "run.json").write_text(json.dumps(run), encoding="utf-8")
        # setup cannot help: the block names the setup error and the hint, no stage runs
        with mock.patch.object(doctor, "check", return_value=dict(MISSING_ALL)), \
             mock.patch.object(bootstrap, "run", return_value={"ready": False, "installed": [],
                                                               "errors": [{"step": "ffmpeg", "message": "offline"}]}) as setup:
            code, out, _ = self.wf("run")
        self.assertEqual(code, 1)
        setup.assert_called_once()
        self.assertIn("NEXT (preflight, blocked)", out)
        self.assertIn("setup: ffmpeg: offline", out)
        self.assertIn("bootstrap.py --system", out)
        self.assertIn("hint:", out)
        self.assertIn("offline", self.run_json()["blocker"]["reason"])
        workflow._invoke.assert_not_called()
        # setup succeeds: preflight re-checks and the first stage runs
        snapshots = iter([dict(MISSING_ALL), dict(READY)])
        with mock.patch.object(doctor, "check", side_effect=lambda **k: next(snapshots)), \
             mock.patch.object(bootstrap, "run", return_value={"ready": True, "installed": [
                 {"step": "ffmpeg", "method": "static-build"}], "errors": []}):
            code, out, _ = self.wf("run")
        self.assertNotIn("preflight, blocked", out)
        self.assertTrue(workflow._invoke.called)
        self.assertEqual(self.run_json()["setup"]["by"], "preflight")


if __name__ == "__main__":
    unittest.main()


class NoHomeHostTests(unittest.TestCase):
    """A host that cannot name a home directory must degrade, not crash.

    `Path.home()` is partial: it raises `RuntimeError` on a Windows account with
    no `USERPROFILE`/`HOMEDRIVE` and on a POSIX account with no `HOME` and no
    passwd entry (service accounts, stripped containers, `runas /env:no`). A
    read-only lookup that raises turns `doctor` -- the one command whose job is
    to say what is wrong -- into an opaque traceback."""

    def setUp(self):
        self.real_environ = dict(os.environ)  # captured before the clear, for the subprocess check
        self.no_home = mock.patch.object(
            Path, "home", side_effect=RuntimeError("Could not determine home directory."))
        self.no_home.start()
        self.env = mock.patch.dict(os.environ, {}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.no_home.stop()

    def test_managed_home_still_answers_a_user_scoped_path(self):
        home = hostenv.managed_home()
        self.assertEqual(home.name, "summarize-video")
        self.assertFalse(home.exists())  # asking for the path never creates it

    def test_doctor_reports_instead_of_raising(self):
        with mock.patch.object(doctor.shutil, "which", return_value=None):
            result = doctor.check()
        self.assertFalse(result["config_present"])
        row = next(r for r in result["checks"] if r["name"] == "user-level tools")
        self.assertFalse(row["available"])

    def test_every_stage_module_still_imports(self):
        """In a fresh interpreter: `render` and `candidates` build a config path at import time.

        Run out of process rather than reloading modules in this one -- a reload
        rebinds globals other tests already hold references to."""
        script = ("import pathlib, sys; sys.path.insert(0, sys.argv[1]);"
                  "pathlib.Path.home = classmethod(lambda cls: (_ for _ in ()).throw("
                  "RuntimeError('Could not determine home directory.')));"
                  "import render, candidates, whisper, doctor, workflow; print('imported')")
        # The real environment, not this class's cleared one: the patch inside the
        # child is what creates the no-home condition, and CPython 3.10 on Windows
        # cannot start without `SystemRoot` -- it dies in preinit seeding hash
        # randomization, before any of this repository's code runs.
        child = {**self.real_environ, "PYTHONUTF8": "1"}
        proc = subprocess.run([sys.executable, "-c", script, str(ROOT / "scripts")],
                              capture_output=True, text=True, env=child)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("imported", proc.stdout)

    def test_credentials_are_absent_rather_than_read_from_a_shared_location(self):
        import whisper as whisper_module
        self.assertIsNone(hostenv.user_config_dir())
        self.assertEqual(whisper_module.load_api_key("groq"), (None, None))
