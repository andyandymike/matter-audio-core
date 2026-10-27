"""Static doctor reports must remain separate from backend execution."""

from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from matter_audio_core import __version__
from matter_audio_core.diagnostics import backend_report, build_report, check
from matter_audio_core.media import MAX_AUDIO_BYTES


class DiagnosticTests(unittest.TestCase):
    def prerequisite(self):
        return check("files", "file", "ok", "files_present", "Required files are present.",
                     details={"component_count": 3})

    def test_core_report_contains_limits_without_private_environment_data(self):
        secret = "fixture-private-key-4391"
        with patch.dict(os.environ, {"API_KEY": secret, "CUDA_VISIBLE_DEVICES": secret}), \
                patch("platform.node", side_effect=AssertionError("No host identity query")), \
                patch("platform.machine", side_effect=AssertionError("No machine probe: it can spawn on Windows")), \
                patch("platform.uname", side_effect=AssertionError("No uname probe: it can spawn on Windows")):
            result = build_report("test-product")
        self.assertEqual(result["schema"], "matter-diagnostics/v1")
        self.assertEqual(result["runtime"]["core_version"], __version__)
        self.assertEqual(result["pcm"]["max_audio_bytes"], MAX_AUDIO_BYTES)
        self.assertEqual(result["pcm"]["channels"], [1, 2])
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(result["scope"], {"read_only": True, "network_calls": 0,
            "downloads": 0, "subprocesses": 0, "audio_model_calls": 0, "workspace_opened": False})

    def test_optional_missing_backend_is_attention_not_core_failure(self):
        missing = backend_report("test.optional", configured=False, checks=[
            check("config", "configuration", "error", "not_configured", "No optional runtime configured.",
                  remedy="Configure an optional runtime only when this operation is needed.")])
        with patch("matter_audio_core.diagnostics.sys.platform", "linux"):
            result = build_report("test", [missing])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["core"]["availability"], "available")
        self.assertEqual(result["summary"], {"status": "attention", "attention_backends": ["test.optional"]})
        self.assertEqual(result["backends"][0]["availability"], "unavailable")

    def test_static_ready_never_claims_loaded_or_execution_device(self):
        backend = backend_report("test.runtime", configured=True, checks=[self.prerequisite(),
            check("device", "device", "unknown", "device_not_probed", "Actual execution device was not probed.")])
        self.assertEqual(backend["availability"], "available")
        self.assertEqual(backend["loaded"], "unknown")
        self.assertEqual(backend["execution_device"], "unknown")
        backend["execution_device"] = "cuda:0"
        with self.assertRaises(ValueError):
            build_report("test", [backend])

    def test_unknown_prerequisites_are_not_ready_and_unsupported_platform_is_explicit(self):
        backend = backend_report("test.runtime", configured=True, checks=[
            check("files", "file", "unknown", "not_inspected", "File presence was not inspected.")])
        self.assertEqual(backend["availability"], "unknown")
        with patch("matter_audio_core.diagnostics.sys.platform", "darwin"):
            result = build_report("test", [backend])
        self.assertEqual(result["core"]["availability"], "unavailable")
        self.assertEqual(result["core"]["checks"][1]["reason"]["code"], "platform_unsupported")

    def test_provider_data_is_copied_and_duplicate_identity_is_rejected(self):
        backend = backend_report("test.runtime", configured=True, checks=[self.prerequisite()])
        result = build_report("test", [backend])
        backend["checks"][0]["details"]["component_count"] = 999
        self.assertEqual(result["backends"][0]["checks"][0]["details"]["component_count"], 3)
        with self.assertRaises(ValueError):
            build_report("test", [backend, backend])
        with self.assertRaises(ValueError):
            backend_report("test.runtime", configured=True, checks=[self.prerequisite(), self.prerequisite()])

    def test_private_or_unbounded_details_are_rejected_without_echo(self):
        secret = "fixture-secret-value"
        for details in ({"api_key": secret}, {"runtime_path": secret}, {"value": "https://host/?token=" + secret},
                        {"nested": {"secret": secret}}, {"count": float("nan")}, {"value": "x" * 129}):
            with self.subTest(details=details), self.assertRaises(ValueError) as caught:
                check("test", "configuration", "ok", "configured", "Configuration checked.", details=details)
            self.assertNotIn(secret, str(caught.exception))
        with self.assertRaises(ValueError):
            check("test", [], "ok", "configured", "Configuration checked.")

    def test_cli_doctor_uses_no_workspace_network_process_or_model_registry(self):
        from matter_audio_core.cli import run
        with tempfile.TemporaryDirectory() as directory:
            missing_workspace = Path(directory) / "must-not-exist"
            output = io.StringIO()
            with ExitStack() as stack:
                for name in ("socket.create_connection", "subprocess.Popen",
                             "matter_audio_core.artifacts.ArtifactStore._workspace",
                             "matter_audio_core.cli.Registry"):
                    stack.enter_context(patch(name, side_effect=AssertionError("doctor side effect")))
                stack.enter_context(redirect_stdout(output))
                code = run(["--workspace", str(missing_workspace), "doctor", "--json"])
            self.assertEqual(code, 0, output.getvalue())
            self.assertFalse(missing_workspace.exists())
            self.assertEqual(json.loads(output.getvalue())["schema"], "matter-diagnostics/v1")

    def test_cli_hook_reports_optional_provider_without_selecting_or_importing_it(self):
        from matter_audio_core.cli import run
        provider = backend_report("test.adapter", configured=True, checks=[self.prerequisite()])
        output = io.StringIO()
        with redirect_stdout(output):
            code = run(["doctor", "--json"], product="test", diagnostic_extra=lambda: [provider])
        self.assertEqual(code, 0, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["backends"], [provider])
