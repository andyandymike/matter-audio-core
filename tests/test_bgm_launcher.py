"""Isolated BGM launcher regressions; never load a real audio model.

The normal source is the maintained integration. Set MATTER_BGM_LAUNCHER to an
absolute staged run_bgm.py, and MATTER_BGM_TEST_TEMP_ROOT to constrain fixtures.
Only the timeout and strict no-child assertions mock the launcher's subprocess
boundary; dispatch tests execute the real launcher and a temporary fake product.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


SOURCE = Path(os.environ.get(
    "MATTER_BGM_LAUNCHER",
    Path(__file__).resolve().parents[1]
    / "integrations/codex/matter-audio/scripts/run_bgm.py",
)).resolve()


FAKE_CLI = '''
import argparse
from pathlib import Path

def _build_parser():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--prompt", required=True)
    for name in ("out", "record-root", "runtime-root"):
        generate.add_argument("--" + name, type=Path, required=True)
    for name, default in (("seconds", 20), ("steps", 8), ("threads", 8),
                          ("seed", None), ("timeout-seconds", 600)):
        generate.add_argument("--" + name, type=int, default=default)
    generate.add_argument("--cfg", type=float, default=1.0)
    generate.add_argument("--apg", type=float)
    generate.add_argument("--negative-prompt")
    return parser
'''

FAKE_AUTHORING = '''
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import wave

class SA3GenerationSettings(SimpleNamespace):
    pass

def resolve_sa3_runtime(root):
    root = Path(root)
    component = root / "model.bin"
    if not component.is_file() or component.stat().st_size == 0:
        raise ValueError("fake runtime component unavailable")
    return SimpleNamespace(root=root, python=Path(sys.executable),
                           script=root / "fake_runtime.py")

def build_sa3_command(*, runtime, prompt, output, settings, seed):
    if not prompt.strip():
        raise ValueError("prompt cannot be blank")
    if not 1 <= settings.seconds <= 120 or settings.steps < 1:
        raise ValueError("invalid generation settings")
    return [str(runtime.python), str(runtime.script), "--prompt", prompt,
            "--out", str(output), "--seed", str(seed)]

def generate_sa3_wav(*, prompt, output, record_root, **kwargs):
    log = Path(__file__).parent.parent / "backend-calls.jsonl"
    payload = {"argv":sys.argv[1:], "cwd":str(Path.cwd()),
               "output":str(output), "record_root":str(record_root),
               "prompt":prompt,
               "environment":{key:os.environ.get(key) for key in
                 ("HF_HUB_OFFLINE", "HF_DATASETS_OFFLINE", "TRANSFORMERS_OFFLINE",
                  "HF_HUB_DISABLE_TELEMETRY", "HF_HUB_DISABLE_IMPLICIT_TOKEN",
                  "DO_NOT_TRACK", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH")}}
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload) + "\\n")
    if os.environ.get("FAKE_BGM_BEHAVIOR") == "timeout":
        print(json.dumps({"status":"failed", "error":{
            "code":"sa3_timeout", "message":"fake local backend exceeded timeout; no retry attempted"}}))
        raise SystemExit(23)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # A tiny, valid deterministic fixture WAV: no audio model or provider exists.
    with output.open("xb") as raw:
        with wave.open(raw, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\\x00\\x00" * 8)
    record_root = Path(record_root)
    record_root.mkdir(parents=True, exist_ok=True)
    (record_root / "fake-record.json").write_text(json.dumps(payload), encoding="utf-8")
    print(json.dumps({"status":"fixture_written", "output":str(output),
                      "record_path":str(record_root / "fake-record.json")}))
'''

FAKE_MAIN = '''
from .cli import _build_parser
from .authoring import generate_sa3_wav

args = _build_parser().parse_args()
generate_sa3_wav(prompt=args.prompt, output=args.out, record_root=args.record_root)
'''


def load_launcher():
    """Import exactly this script and its sibling, without installed products."""
    if not SOURCE.is_file():
        raise RuntimeError(f"BGM launcher missing: {SOURCE}")
    spec = importlib.util.spec_from_file_location("matter_bgm_launcher_tests", SOURCE)
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.pop("run_audio", None)
    old_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SOURCE.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
        sys.dont_write_bytecode = old_bytecode
        sys.modules.pop("run_audio", None)
        if previous is not None:
            sys.modules["run_audio"] = previous
    return module


class BgmLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher = load_launcher()

    def setUp(self):
        temp_parent = os.environ.get("MATTER_BGM_TEST_TEMP_ROOT")
        if temp_parent:
            Path(temp_parent).mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="bgm-launcher-", dir=temp_parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "owner-one" / "same-project-name"
        self.cwd = self.project / "nested working directory"
        self.cwd.mkdir(parents=True)
        (self.project / ".git").write_text("gitdir: fixture-only", encoding="utf-8")
        self.tool = self.root / "fake product checkout"
        package = self.tool / "score_matter"
        package.mkdir(parents=True)
        for name, content in {
            "__init__.py":"", "cli.py":FAKE_CLI,
            "authoring.py":FAKE_AUTHORING, "__main__.py":FAKE_MAIN,
        }.items():
            (package / name).write_text(textwrap.dedent(content), encoding="utf-8")
        self.runtime = self.root / "fake runtime"
        self.runtime.mkdir()
        (self.runtime / "model.bin").write_bytes(b"fixture metadata, not a model")
        (self.runtime / "fake_runtime.py").write_text(
            "raise AssertionError('No test may launch an audio runtime')\n", encoding="utf-8")
        self.config = self.root / "config.json"
        self.configuration = {"python":sys.executable, "root":str(self.tool),
                              "sa3_runtime_root":str(self.runtime)}
        self.write_config()
        self.env = {**os.environ, "PYTHONDONTWRITEBYTECODE":"1", "PYTHONUTF8":"1"}
        self.env.pop("FAKE_BGM_BEHAVIOR", None)
        self.env.pop("PYTHONPATH", None)

    def write_config(self):
        self.config.write_text(json.dumps({"score":self.configuration}), encoding="utf-8")

    def command(self, *args):
        return [sys.executable, "-B", str(SOURCE), "--config", str(self.config), *map(str, args)]

    def invoke(self, *args, cwd=None, env=None):
        return subprocess.run(self.command(*args), cwd=cwd or self.cwd,
                              env=env or self.env, capture_output=True,
                              encoding="utf-8", timeout=20)

    def payload(self, result, expected=0):
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail("Launcher did not return one JSON result: " + result.stdout + result.stderr)

    def files(self):
        return {str(path.relative_to(self.root)):path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def backend_calls(self):
        log = self.tool / "backend-calls.jsonl"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    def assert_no_generation(self):
        self.assertEqual(self.backend_calls(), [])
        self.assertFalse((self.project / "artifacts").exists())
        self.assertFalse((self.tool / ".local").exists())

    def test_actual_dry_run_neither_starts_configured_interpreter_nor_creates_files(self):
        unusable = self.root / "never-launch-this.exe"
        unusable.write_bytes(b"not an executable")
        self.configuration["python"] = str(unusable)
        self.write_config()
        before = self.files()
        plan = self.payload(self.invoke("--dry-run", "--prompt", "雨夜，缓慢的旋律"))
        self.assertEqual(plan["argv"][0], str(unusable))
        self.assertEqual(plan["model_calls"], 0)
        self.assertIs(plan["runtime_checked"], False)
        self.assertEqual(before, self.files())
        self.assertFalse(Path(plan["output"]).parent.exists())
        self.assert_no_generation()

    def test_dry_run_has_no_subprocess_boundary_at_all(self):
        stream = io.StringIO()
        args = [str(SOURCE), "--config", str(self.config), "--project-root", str(self.project),
                "--dry-run", "--prompt", "one candidate"]
        with mock.patch.object(sys, "argv", args), redirect_stdout(stream):
            with mock.patch.object(self.launcher.subprocess, "run", side_effect=AssertionError("child started")) as run:
                self.assertEqual(self.launcher.main(), 0)
        run.assert_not_called()
        self.assertEqual(json.loads(stream.getvalue())["model_calls"], 0)

    def test_same_named_consumers_get_independent_output_and_record_trees(self):
        second = self.root / "owner-two" / self.project.name
        second.mkdir(parents=True)
        (second / ".git").mkdir()
        first_plan = self.payload(self.invoke("--dry-run", "--prompt", "first"))
        second_plan = self.payload(self.invoke("--dry-run", "--prompt", "second", cwd=second))
        for plan, project in ((first_plan,self.project), (second_plan,second)):
            self.assertEqual(Path(plan["project_root"]), project)
            self.assertTrue(Path(plan["output"]).is_relative_to(project / "artifacts/matter-audio/score/generation"))
            self.assertEqual(Path(plan["record_root"]), Path(plan["output"]).parent / "records")
            self.assertFalse(Path(plan["output"]).parent.exists())
        self.assertNotEqual(first_plan["output"], second_plan["output"])
        self.assertNotEqual(first_plan["record_root"], second_plan["record_root"])
        self.assert_no_generation()

    def test_default_versions_are_unique_without_creating_directories(self):
        a = self.payload(self.invoke("--dry-run", "--prompt", "same cue"))
        b = self.payload(self.invoke("--dry-run", "--prompt", "same cue"))
        self.assertNotEqual(a["output"], b["output"])
        self.assert_no_generation()

    def test_relative_output_and_records_are_bound_to_caller_before_tool_cwd(self):
        plan = self.payload(self.invoke("--dry-run", "--prompt", "bounded", "--out", "new mix/a.wav",
                                        "--record-root", "../my records"))
        self.assertEqual(Path(plan["output"]), self.cwd / "new mix/a.wav")
        self.assertEqual(Path(plan["record_root"]), self.project / "my records")
        self.assertEqual(Path(plan["cwd"]), self.tool)
        self.assert_no_generation()

    def test_explicit_output_default_records_are_colocated(self):
        plan = self.payload(self.invoke("--dry-run", "--prompt", "bounded", "--out", "exports/cue.wav"))
        self.assertEqual(Path(plan["record_root"]), self.cwd / "exports/records")

    def test_explicit_project_changes_default_tree_not_relative_output_base(self):
        other = self.root / "explicit consumer"
        other.mkdir()
        plan = self.payload(self.invoke("--dry-run", "--prompt", "bounded", "--project-root", other))
        self.assertEqual(Path(plan["project_root"]), other)
        self.assertTrue(Path(plan["output"]).is_relative_to(other))
        explicit = self.payload(self.invoke("--dry-run", "--prompt", "bounded", "--project-root", other,
                                            "--out", "caller.wav"))
        self.assertEqual(Path(explicit["output"]), self.cwd / "caller.wav")

    def test_existing_output_is_preserved_without_preflight_or_generation(self):
        output = self.cwd / "kept.wav"
        output.write_bytes(b"original must survive")
        # Corrupt the fake package so reaching preflight would change the error.
        (self.tool / "score_matter/authoring.py").write_text("raise RuntimeError('preflight was reached')", encoding="utf-8")
        result = self.payload(self.invoke("--prompt", "new", "--out", output), expected=2)
        self.assertIn("already exists", result["error"]["message"])
        self.assertEqual(output.read_bytes(), b"original must survive")
        self.assert_no_generation()

    def test_dangling_output_symlink_is_preserved_and_rejected(self):
        output = self.cwd / "dangling.wav"
        missing = self.cwd / "missing-target.wav"
        try:
            output.symlink_to(missing)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Host cannot create an unprivileged symlink: {exc}")
        result = self.payload(self.invoke("--prompt", "new", "--out", output), expected=2)
        self.assertIn("already exists", result["error"]["message"])
        self.assertTrue(output.is_symlink())
        self.assertFalse(missing.exists())
        self.assert_no_generation()

    def test_dangling_output_guard_without_platform_symlink_privilege(self):
        """Exercise rejection on Windows even if the real-symlink test skips."""
        output = self.cwd / "simulated-dangling.wav"
        real_is_symlink = Path.is_symlink
        def is_symlink(path):
            return True if path == output else real_is_symlink(path)
        args = [str(SOURCE), "--config", str(self.config), "--prompt", "test", "--out", str(output)]
        stream = io.StringIO()
        with mock.patch.object(sys, "argv", args), redirect_stdout(stream):
            with mock.patch.object(Path, "is_symlink", is_symlink), \
                 mock.patch.object(self.launcher.subprocess, "run", side_effect=AssertionError("child started")) as run:
                self.assertEqual(self.launcher.main(), 2)
        run.assert_not_called()
        self.assertIn("already exists", json.loads(stream.getvalue())["error"]["message"])
        self.assertFalse(output.exists())

    def test_runtime_is_required_even_when_dry_run_or_environment_has_one(self):
        del self.configuration["sa3_runtime_root"]
        self.write_config()
        env = {**self.env, "SCORE_MATTER_SA3_ROOT":str(self.runtime)}
        error = self.payload(self.invoke("--dry-run", "--prompt", "x", env=env), expected=2)
        self.assertIn("sa3_runtime_root", error["error"]["message"])
        plan = self.payload(self.invoke("--dry-run", "--prompt", "x", "--runtime-root", self.runtime))
        self.assertEqual(Path(plan["runtime_root"]), self.runtime)
        self.assert_no_generation()

    def test_preflight_is_read_only_and_reports_zero_model_calls(self):
        before = self.files()
        result = self.payload(self.invoke("--check"))
        self.assertIs(result["runtime_checked"], True)
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["preflight"]["model_calls"], 0)
        self.assertNotIn(result["preflight"]["status"], ("generated", "completed", "succeeded"))
        self.assertEqual(Path(result["preflight"]["product_module"]), self.tool / "score_matter/authoring.py")
        self.assertEqual(before, self.files())
        self.assert_no_generation()

    def test_bad_settings_and_missing_components_fail_before_dispatch(self):
        for args in (("--seconds", "0"), ("--steps", "0"), ("--prompt", "   ")):
            with self.subTest(args=args):
                result = self.payload(self.invoke("--prompt", "test", *args), expected=2)
                self.assertIn("preflight failed", result["error"]["message"].lower())
                self.assert_no_generation()
        (self.runtime / "model.bin").write_bytes(b"")
        result = self.payload(self.invoke("--prompt", "test"), expected=2)
        self.assertIn("component unavailable", result["error"]["message"])
        self.assert_no_generation()

    def test_old_product_without_record_root_is_rejected_before_dispatch(self):
        authoring = self.tool / "score_matter/authoring.py"
        authoring.write_text(authoring.read_text(encoding="utf-8").replace(
            "def generate_sa3_wav(*, prompt, output, record_root, **kwargs):",
            "def generate_sa3_wav(*, prompt, output, **kwargs):"), encoding="utf-8")
        result = self.payload(self.invoke("--prompt", "test"), expected=2)
        self.assertIn("record-root support", result["error"]["message"])
        self.assert_no_generation()

    def test_record_root_file_is_rejected_without_generation(self):
        record = self.cwd / "not-a-directory"
        record.write_bytes(b"preserve")
        result = self.payload(self.invoke("--prompt", "test", "--record-root", record), expected=2)
        self.assertIn("directory", result["error"]["message"])
        self.assertEqual(record.read_bytes(), b"preserve")
        self.assert_no_generation()

    def test_file_as_output_ancestor_is_rejected_before_backend_call(self):
        blocker = self.cwd / "blocked"
        blocker.write_bytes(b"preserve")
        checked = self.invoke("--check", "--out", blocker / "new.wav")
        self.assertNotEqual(checked.returncode, 0, "Known unusable destination was declared ready: " + checked.stdout)
        completed = self.invoke("--prompt", "test", "--out", blocker / "new.wav")
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(blocker.read_bytes(), b"preserve")
        self.assert_no_generation()

    def test_file_as_record_ancestor_is_rejected_before_backend_call(self):
        blocker = self.cwd / "blocked-records"
        blocker.write_bytes(b"preserve")
        output = self.cwd / "fresh.wav"
        completed = self.invoke("--prompt", "test", "--out", output,
                                "--record-root", blocker / "records")
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(blocker.read_bytes(), b"preserve")
        self.assertFalse(output.exists())
        self.assert_no_generation()

    def test_records_cannot_be_the_planned_wav_or_below_it(self):
        output = self.cwd / "planned.wav"
        for records in (output, output / "records"):
            with self.subTest(record_root=records):
                result = self.payload(self.invoke("--prompt", "test", "--out", output,
                                                  "--record-root", records), expected=2)
                self.assertEqual(result["status"], "failed")
                self.assertFalse(output.exists())
                self.assert_no_generation()

    def test_actual_default_dispatches_keep_same_named_consumers_separate(self):
        second = self.root / "owner-two" / self.project.name
        second.mkdir(parents=True)
        (second / ".git").mkdir()
        results = [self.payload(self.invoke("--prompt", "one", cwd=directory))
                   for directory in (self.cwd, second)]
        for result, project in zip(results, (self.project, second)):
            output = Path(result["output"])
            record = Path(result["record_path"])
            self.assertTrue(output.is_relative_to(project / "artifacts/matter-audio/score/generation"))
            self.assertEqual(record.parent, output.parent / "records")
            evidence = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual(Path(evidence["output"]), output)
            self.assertTrue(output.is_file())
        self.assertEqual(len(self.backend_calls()), 2)
        self.assertNotEqual(results[0]["record_path"], results[1]["record_path"])
        self.assertEqual(list(self.tool.rglob("*.wav")), [])

    def test_actual_dispatch_once_preserves_options_and_writes_only_consumer_output(self):
        output = self.cwd / "exports/my cue.wav"
        records = self.cwd / "chosen records"
        prompt = "慢速旋律; literal $() and a quoted \"word\""
        env = {**self.env, "PYTHONPATH":str(self.root / "must-not-leak")}
        result = self.payload(self.invoke(
            "--prompt", prompt, "--out", "exports/my cue.wav", "--record-root", "chosen records",
            "--seconds", "7", "--seed", "123", "--steps", "9", "--threads", "2",
            "--cfg", "1.4", "--apg", "0.5", "--negative-prompt", "no drums",
            "--timeout-seconds", "13", env=env))
        self.assertEqual(Path(result["output"]), output)
        self.assertTrue(output.read_bytes().startswith(b"RIFF"))
        self.assertTrue((records / "fake-record.json").is_file())
        calls = self.backend_calls()
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(Path(call["cwd"]), self.tool)
        self.assertEqual(Path(call["output"]), output)
        self.assertEqual(Path(call["record_root"]), records)
        self.assertEqual(call["argv"][0], "generate")
        options = dict(zip(call["argv"][1::2], call["argv"][2::2]))
        self.assertEqual(options["--prompt"], prompt)
        for flag, value in {"--seconds":"7", "--seed":"123", "--steps":"9", "--threads":"2",
                            "--cfg":"1.4", "--apg":"0.5", "--negative-prompt":"no drums",
                            "--timeout-seconds":"13"}.items():
            self.assertEqual(options[flag], value)
        for key, value in call["environment"].items():
            self.assertEqual(value, None if key == "PYTHONPATH" else "1")
        self.assertFalse((self.tool / ".local").exists())
        self.assertEqual(list(self.tool.rglob("*.wav")), [])
        self.assertEqual(list(self.runtime.rglob("*.wav")), [])

    def test_actual_backend_failure_is_returned_once_without_shared_job_advice(self):
        output = self.cwd / "failed.wav"
        result = self.payload(self.invoke("--prompt", "test", "--out", output,
                                          env={**self.env, "FAKE_BGM_BEHAVIOR":"timeout"}), expected=23)
        self.assertEqual(result["error"]["code"], "sa3_timeout")
        self.assertEqual(len(self.backend_calls()), 1)
        self.assertFalse(output.exists())
        self.assertNotIn("action show", result["error"]["message"].lower())
        self.assertNotIn("job show", result["error"]["message"].lower())

    def test_host_timeout_reports_uncertainty_without_shared_job_advice(self):
        output = self.cwd / "uncertain.wav"
        args = [str(SOURCE), "--config", str(self.config), "--prompt", "test", "--out", str(output)]
        stream = io.StringIO()
        launch_notice = io.StringIO()
        with mock.patch.object(sys, "argv", args), redirect_stdout(stream), redirect_stderr(launch_notice):
            with mock.patch.object(self.launcher, "check_backend", return_value={}), \
                 mock.patch.object(self.launcher.subprocess, "run", side_effect=subprocess.TimeoutExpired("fixture", 1)) as run:
                self.assertEqual(self.launcher.main(), 2)
        self.assertEqual(run.call_count, 1)
        failure = json.loads(stream.getvalue())
        self.assertEqual(Path(failure["output"]), output)
        self.assertEqual(Path(failure["record_root"]), output.parent / "records")
        notice = json.loads(launch_notice.getvalue())
        self.assertEqual(Path(notice["output"]), output)
        self.assertEqual(Path(notice["record_root"]), output.parent / "records")
        message = failure["error"]["message"].lower()
        self.assertIn("uncertain", message)
        self.assertIn("before any retry", message)
        self.assertNotIn("action show", message)
        self.assertNotIn("job show", message)
        self.assert_no_generation()

    def test_preflight_timeout_must_not_report_uncertain_generation(self):
        args = [str(SOURCE), "--config", str(self.config), "--check"]
        stream = io.StringIO()
        with mock.patch.object(sys, "argv", args), redirect_stdout(stream):
            with mock.patch.object(self.launcher.subprocess, "run", side_effect=subprocess.TimeoutExpired("preflight", 30)) as run:
                self.assertEqual(self.launcher.main(), 2)
        self.assertEqual(run.call_count, 1)
        message = json.loads(stream.getvalue())["error"]["message"].lower()
        self.assertIn("preflight", message)
        self.assertNotIn("outcome is uncertain", message)
        self.assert_no_generation()


if __name__ == "__main__":
    unittest.main()
