"""Evaluation must inspect artifacts rather than trust completion claims."""

from contextlib import ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "evaluate_editing.py"
spec = importlib.util.spec_from_file_location("matter_editing_evaluation", SCRIPT)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def snapshot(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


class EditingEvaluationTests(unittest.TestCase):
    def test_timed_out_cli_is_counted_as_an_attempt_and_cannot_pass(self):
        runner = evaluation.Runner(Path.cwd(), Path.cwd() / "absent-eval-workspace")
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["fixture"], 90)):
            event = runner.call("matter_audio_voicestudio", "edit", "plan")
        self.assertEqual(len(runner.events), 1)
        self.assertIsNone(event["return_code"])
        self.assertEqual(event["response"]["error"]["code"], "evaluation_cli_timeout")
        with self.assertRaises(RuntimeError):
            evaluation._ok(event)

    def test_missing_evidence_never_passes_a_rejection_or_success(self):
        checks = dict.fromkeys(evaluation.REJECTION_CHECKS, True)
        checks["expected_rejection"] = None
        self.assertIsNone(evaluation.grade_checks("reject", checks)["completed"])
        checks["expected_rejection"] = True
        self.assertTrue(evaluation.grade_checks("reject", checks)["completed"])
        checks["no_publication"] = False
        self.assertFalse(evaluation.grade_checks("reject", checks)["completed"])
        self.assertIsNone(evaluation.grade_checks("success", {"exact_pcm": True})["completed"])

    def test_prepare_and_external_grade_do_not_execute_or_repair_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "prepared"
            with patch("subprocess.Popen", side_effect=AssertionError("prepare cannot launch task processes")):
                prepared = evaluation.prepare(root, ["extract_sentence", "reject_ambiguous_echo"])
            self.assertEqual(prepared["status"], "prepared")
            tasks = json.loads((root / "tasks.json").read_text())
            self.assertEqual(len(tasks["tasks"]), 2)
            self.assertNotIn("oracle", json.dumps(tasks))
            before = snapshot(root)
            with ExitStack() as stack:
                for target in ("subprocess.Popen", "matter_audio_voicestudio.editing.plan",
                               "matter_audio_voicestudio.editing.execute", "matter_audio_core.actions.ActionService.execute",
                               "matter_audio_core.sessions.SessionService.mutate", "matter_audio_core.delivery.ExportService.create"):
                    stack.enter_context(patch(target, side_effect=AssertionError("grader performed a task")))
                report = evaluation.grade_existing(root)
            self.assertEqual(snapshot(root), before)
            self.assertEqual(report["completed_tasks"], 0)
            self.assertEqual(report["failed_tasks"], 1)
            self.assertEqual(report["unverified_tasks"], 1)
            self.assertIsNone(report["cli_calls"])
            self.assertIsNone(report["task_elapsed_seconds"])
            self.assertIsNone(report["task_completion_rate"])
            self.assertEqual(report["verified_task_completion_rate"], 0)
            with self.assertRaises(FileExistsError):
                evaluation.prepare(root, ["extract_sentence"])
            self.assertEqual(snapshot(root), before)

    def test_real_baseline_regrade_is_read_only_and_rejects_changed_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "baseline"
            report = evaluation.evaluate(root, ["replace_third_echo"])
            self.assertEqual(report["status"], "passed", report)
            self.assertEqual(report["task_completion_rate"], 1)
            self.assertGreater(report["cli_calls"], 0)
            self.assertEqual(report["evidence_scope"]["llm_calls"], 0)
            before = snapshot(root)
            with patch("subprocess.Popen", side_effect=AssertionError("read-only regrade launched a process")):
                reread = evaluation.grade_existing(root)
            self.assertEqual(reread["status"], "passed", reread)
            self.assertEqual(snapshot(root), before)
            # The on-disk scripted trace is not trusted as an externally captured
            # trajectory, so its timings/call counts cannot become agent metrics.
            self.assertIsNone(reread["cli_calls"])
            self.assertIsNone(reread["evidence_scope"]["llm_calls"])
            case_root = root / "replace_third_echo"
            asset_id = report["results"][0]["evidence"]["output_asset_id"]
            store = evaluation.ArtifactStore(case_root / "workspace")
            record, payload = store.asset(asset_id)
            damaged = bytearray(payload)
            damaged[-1] ^= 1
            (store.root / record["locator"]).write_bytes(damaged)
            self.assertEqual(evaluation.grade_existing(root)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
