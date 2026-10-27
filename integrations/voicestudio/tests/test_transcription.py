"""Exercise real loopback HTTP and durable artifacts without speech models."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch

from matter_audio_core.artifacts import ArtifactStore
from matter_audio_core.contracts import MAX_JSON_BYTES, canonical, parse_json
from matter_audio_core.errors import AudioError
from matter_audio_core.media import PCM, decode_wav, encode_wav
from matter_audio_voicestudio import client
from matter_audio_voicestudio.cli import main
from matter_audio_voicestudio.service import TranscriptionService, export_result, normalize


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="matter-transcript-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = ArtifactStore(self.root / "workspace")
        self.audio = self.root / "original.wav"
        self.original = encode_wav(PCM(b"\0\0" * 16000, 8000, 1))
        self.audio.write_bytes(self.original)
        self.source = self.store.import_wav(self.audio, "source")["outputs"][0]
        self.response = {"text": "Hello world.", "language": "en", "model": "test-asr",
                         "segments": [{"start": 0, "end": 2, "text": "Hello world."}],
                         "words": [{"start": 0, "end": 1, "word": "Hello"}, {"start": 1, "end": 2, "word": "world."}]}
        self.calls = []
        self.status = 200
        self.response_bytes = None
        self.transport_failure = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                owner.calls.append({"path": self.path, "body": self.rfile.read(int(self.headers["Content-Length"])),
                                    "auth": self.headers.get("Authorization")})
                if owner.transport_failure == "bad_status":
                    self.wfile.write(b"private-provider-diagnostic\r\n")
                    self.close_connection = True
                    return
                if owner.transport_failure == "incomplete_chunk":
                    self.send_response(200)
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.write(b'40\r\n{"text":"private-provider-diagnostic')
                    self.close_connection = True
                    return
                raw = owner.response_bytes if owner.response_bytes is not None else canonical(owner.response)
                self.send_response(owner.status)
                self.send_header("Content-Type", "application/json")
                if owner.status == 302:
                    self.send_header("Location", "/elsewhere")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    self.wfile.write(raw)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(timeout=2)))
        self.config_path = self.root / "service.json"
        self.config_path.write_bytes(canonical({"schema": "matter-voicestudio-config/v1",
            "base_url": f"http://127.0.0.1:{server.server_port}", "model": "test-asr", "service_revision": "fixture-1"}))
        self.config = client.load_config(self.config_path)
        self.service = TranscriptionService(self.store, self.config)
        self.request = {"schema": "matter-transcription-request/v1", "request_id": "transcript-1", "asset_id": self.source["asset_id"], "timestamps": "word"}

    def execute(self):
        resolution = self.service.resolve(self.request)
        return self.service.execute(self.request, resolution["digest"]["hex"])

    def test_preview_and_doctor_do_not_contact_service_or_create_workspace(self):
        resolution = self.service.resolve(self.request)
        self.assertEqual(resolution["network_requests"], 0)
        absent = self.root / "absent"
        with patch("socket.create_connection", side_effect=AssertionError("network")), patch("subprocess.Popen", side_effect=AssertionError("process")), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--config", str(self.config_path), "--workspace", str(absent), "doctor"]), 0)
        self.assertEqual(json.loads(output.getvalue())["scope"]["network_calls"], 0)
        self.assertFalse(absent.exists())
        self.assertEqual(self.calls, [])

    def test_real_http_binds_source_publishes_subtitles_and_replay_never_resends(self):
        result = self.execute()
        self.assertEqual(result["status"], "succeeded")
        self.assertIsNone(result["audio_model_calls"])
        self.assertEqual(result["remote_requests"], 1)
        self.assertEqual(result, self.execute())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["path"], "/v1/audio/transcriptions")
        self.assertIn(self.original, self.calls[0]["body"])
        self.assertIn(b'name="timestamp_granularities[]"', self.calls[0]["body"])
        record, raw = self.store.asset(result["transcript_asset_id"])
        document = parse_json(raw)
        self.assertEqual(document["source"]["digest"], self.source["digest"])
        self.assertEqual(document["words"][1]["start_frame"], 8000)
        self.assertFalse(document["timing"]["precision_verified"])
        self.assertEqual(record["parents"][0]["asset_id"], self.source["asset_id"])
        self.assertEqual(self.store.asset(self.source["asset_id"])[1], self.original)
        self.assertFalse((self.store.root / "sessions.sqlite3").exists())
        self.assertEqual(set(result["subtitle_assets"]), {"srt", "vtt"})

    def test_request_conflict_and_changed_preview_never_send_again(self):
        resolution = self.service.resolve(self.request)
        changed = {**self.request, "language": "fr"}
        with self.assertRaisesRegex(AudioError, "Resolve"):
            self.service.execute(changed, resolution["digest"]["hex"])
        self.assertEqual(self.calls, [])
        self.execute()
        with self.assertRaisesRegex(AudioError, "different"):
            self.service.execute(changed, self.service.resolve(changed)["digest"]["hex"])
        self.assertEqual(len(self.calls), 1)

    def test_http_failure_is_durable_and_never_retried(self):
        self.status = 503
        result = self.execute()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "remote_http_error")
        self.assertIsNone(result["audio_model_calls"])
        self.assertEqual(result["outputs"], [])
        self.assertEqual(result, self.execute())
        self.assertEqual(len(self.calls), 1)

    def test_unconfirmed_timeout_records_unknown_inference_not_zero(self):
        with patch.object(client, "transcribe", side_effect=AudioError("remote_outcome_unknown", "Unconfirmed fixture")) as mocked:
            result = self.execute()
            self.assertEqual(result, self.execute())
        self.assertEqual(mocked.call_count, 1)
        self.assertIsNone(result["audio_model_calls"])
        self.assertEqual(result["outputs"], [])
        self.assertEqual(result["execution"]["uncertain_launch_count"], 1)

    def test_real_http_protocol_failures_are_durable_unknown_and_never_retried(self):
        for failure in ("bad_status", "incomplete_chunk"):
            with self.subTest(failure=failure):
                self.transport_failure = failure
                self.request["request_id"] = failure
                before = len(self.calls)
                result = self.execute()
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"]["code"], "remote_outcome_unknown")
                self.assertEqual(result["error"]["details"], {"remote_outcome": "unknown", "automatic_retry": False})
                self.assertIsNone(result["audio_model_calls"])
                self.assertEqual(result["execution"]["uncertain_launch_count"], 1)
                self.assertEqual(result["outputs"], [])
                self.assertEqual(result, self.store.show_request(failure))
                self.assertEqual(result, self.execute())
                self.assertEqual(len(self.calls), before + 1)
                self.assertNotIn(b"private-provider-diagnostic", canonical(result))

    def test_fresh_cli_protocol_failure_returns_json_and_saved_failure(self):
        self.transport_failure = "incomplete_chunk"
        request_path = self.root / "request.json"
        request_path.write_bytes(canonical(self.request))
        resolution = self.service.resolve(self.request)
        command = [sys.executable, "-m", "matter_audio_voicestudio", "--workspace", str(self.store.root),
                   "--config", str(self.config_path), "transcribe", "execute", "--request", str(request_path),
                   "--expected-resolution-digest", resolution["digest"]["hex"]]
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        saved = self.store.show_request(self.request["request_id"])
        self.assertEqual(json.loads(result.stdout), saved)
        self.assertEqual(saved["error"]["code"], "remote_outcome_unknown")
        self.assertEqual(len(self.calls), 1)

    def test_redirect_is_not_followed(self):
        self.status = 302
        result = self.execute()
        self.assertEqual(result["error"]["details"]["http_status"], 302)
        self.assertEqual(len(self.calls), 1)

    def test_bounded_response_and_bad_timestamps_publish_no_partial_annotations(self):
        variants = [b"x" * (1024 * 1024 + 1), b'{"text":"x","text":"y"}', b'{"text":"x","words":[{"start":0,"end":20,"word":"x"}]}']
        for index, raw in enumerate(variants):
            with self.subTest(index=index):
                self.request["request_id"] = f"invalid-{index}"
                self.response_bytes = raw
                result = self.execute()
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["outputs"], [])
                self.assertIsNone(result["audio_model_calls"])

    def test_missing_word_timings_are_reported_and_not_fabricated(self):
        self.response = {"text": "Hello world.", "language": "en"}
        result = self.execute()
        self.assertIn("requested_word_timestamps_missing", result["warnings"])
        self.assertEqual(result["subtitle_assets"], {})
        with self.assertRaisesRegex(AudioError, "no segment"):
            export_result(self.store, "transcript-1", "srt", self.root / "missing.srt")

    def test_real_http_empty_and_submillisecond_cues_keep_readable_transcript(self):
        for index, segment in enumerate((
                {"start": 0, "end": 0, "text": "zero"},
                {"start": 0, "end": 1, "text": " \t\n "},
                {"start": 0, "end": 1 / 8000, "text": "short"})):
            with self.subTest(segment=segment):
                self.request["request_id"] = f"no-cues-{index}"
                self.response = {"text": segment["text"], "segments": [segment]}
                result = self.execute()
                self.assertEqual(result["status"], "succeeded")
                self.assertEqual(result["subtitle_assets"], {})
                self.assertEqual([item["role"] for item in result["outputs"]], ["transcript"])
                self.assertIn("no_usable_subtitle_cues", result["warnings"])
                document = parse_json(self.store.asset(result["transcript_asset_id"])[1])
                self.assertEqual(document["segments"][0]["provider_end_seconds"], segment["end"])
                self.assertEqual(document["warnings"], result["warnings"])
                self.assertEqual(result, self.execute())
                for format in ("srt", "vtt"):
                    with self.assertRaisesRegex(AudioError, "no segment"):
                        export_result(self.store, self.request["request_id"], format, self.root / f"missing-{index}.{format}")
        self.assertEqual(len(self.calls), 3)

    def test_real_http_mixed_cues_export_only_positive_millisecond_intervals(self):
        self.response = {"text": "short keep blank", "segments": [
            {"start": 0, "end": 1 / 8000, "text": "short"},
            {"start": 1 / 8000, "end": 8 / 8000, "text": "keep"},
            {"start": 1, "end": 2, "text": " "}]}
        result = self.execute()
        for format, separator in (("srt", ","), ("vtt", ".")):
            raw = self.store.asset(result["subtitle_assets"][format])[1].decode()
            self.assertEqual(raw.count(" --> "), 1)
            self.assertIn(f"00:00:00{separator}000 --> 00:00:00{separator}001", raw)
            self.assertIn("\nkeep\n", raw)
            self.assertNotIn("short", raw)
        document = parse_json(self.store.asset(result["transcript_asset_id"])[1])
        self.assertEqual(len(document["segments"]), 3)

    def test_normalized_transcript_exact_size_boundary_is_enforced_after_real_http(self):
        self.response = {"text": "", "words": [{"start": 0, "end": 1, "word": "w"} for _ in range(10000)]}
        self.response["words"][0]["word"] = "w" * 2000
        binding = self.service.resolve(self.request)["binding"]
        document = normalize(self.response, decode_wav(self.original), binding["source"], binding["service"], binding["parameters"])
        padding = MAX_JSON_BYTES - len(canonical(document))
        self.assertTrue(0 < padding < 65536)
        self.response["text"] = "x" * padding
        self.assertLess(len(canonical(self.response)), MAX_JSON_BYTES)
        result = self.execute()
        raw = self.store.asset(result["transcript_asset_id"])[1]
        self.assertEqual(len(raw), MAX_JSON_BYTES)
        self.assertEqual(parse_json(raw)["text"], self.response["text"])
        self.assertEqual(result, self.execute())
        self.request["request_id"] = "too-large-normalized"
        self.response["text"] += "x"
        self.assertLess(len(canonical(self.response)), MAX_JSON_BYTES)
        failed = self.execute()
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"]["code"], "transcript_too_large")
        self.assertEqual(failed["outputs"], [])
        self.assertIsNone(failed["audio_model_calls"])
        self.assertEqual(failed, self.execute())
        self.assertEqual(len(self.calls), 2)

    def test_subtitle_export_matches_saved_bytes_and_refuses_overwrite(self):
        result = self.execute()
        destination = self.root / "dialogue.srt"
        exported = export_result(self.store, "transcript-1", "srt", destination)
        saved = self.store.asset(result["subtitle_assets"]["srt"])[1]
        self.assertEqual(destination.read_bytes(), saved)
        self.assertIn(b"00:00:00,000 --> 00:00:02,000", saved)
        self.assertEqual(exported["network_requests"], 0)
        with self.assertRaises(FileExistsError):
            export_result(self.store, "transcript-1", "srt", destination)
        self.assertEqual(destination.read_bytes(), saved)
        with self.assertRaisesRegex(AudioError, "outside"):
            export_result(self.store, "transcript-1", "srt", self.store.root / "output.srt")

    def test_failed_export_removes_only_its_new_partial_file(self):
        self.execute()
        destination = (self.root / "partial.srt").resolve()
        original_open = Path.open

        class FailingWrite:
            def __init__(self, stream):
                self.stream = stream

            def fileno(self):
                return self.stream.fileno()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def write(self, data):
                self.stream.write(data[:4])
                raise OSError("Synthetic write failure")

        def open_file(path, mode="r", *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            return FailingWrite(stream) if path == destination and mode == "xb" else stream

        with patch.object(Path, "open", open_file), self.assertRaisesRegex(OSError, "Synthetic"):
            export_result(self.store, "transcript-1", "srt", destination)
        self.assertFalse(destination.exists())
        export_result(self.store, "transcript-1", "srt", destination)
        self.assertTrue(destination.read_bytes())

    def test_failed_export_does_not_remove_a_concurrent_replacement(self):
        self.execute()
        destination = (self.root / "replaced.srt").resolve()
        replacement = self.root / "other.txt"
        replacement.write_bytes(b"other writer's file")
        abandoned = self.root / "original-partial.srt"
        original_open = Path.open

        class ReplacedWrite:
            def __init__(self, stream):
                self.stream = stream

            def fileno(self):
                return self.stream.fileno()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def write(self, data):
                self.stream.write(data[:4])
                self.stream.close()
                destination.rename(abandoned)
                replacement.rename(destination)
                raise OSError("Synthetic replacement before failure")

        def open_file(path, mode="r", *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            return ReplacedWrite(stream) if path == destination and mode == "xb" else stream

        with patch.object(Path, "open", open_file), self.assertRaisesRegex(OSError, "Synthetic"):
            export_result(self.store, "transcript-1", "srt", destination)
        self.assertEqual(destination.read_bytes(), b"other writer's file")

    def test_credentials_are_used_only_for_request_and_never_persisted(self):
        self.config["api_key_env"] = "MATTER_TEST_SERVICE_SECRET"
        secret = "private-service-credential-123"
        with patch.dict(os.environ, {"MATTER_TEST_SERVICE_SECRET": secret}):
            result = self.execute()
        self.assertEqual(self.calls[0]["auth"], "Bearer " + secret)
        for path in self.store.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(secret.encode(), path.read_bytes())
        self.assertEqual(result, self.execute())  # Completed replay needs no credential.

    def test_invalid_configuration_cannot_echo_credentials_or_enable_remote_plaintext(self):
        for url in ("http://example.com", "https://user:private-token@example.com", "https://example.com?key=private-token", "file:///secret", "https://example.com\\private-token"):
            self.config_path.write_bytes(canonical({"schema": "matter-voicestudio-config/v1", "base_url": url, "model": "asr"}))
            with self.assertRaises(AudioError) as caught:
                client.load_config(self.config_path)
            self.assertNotIn("private-token", str(caught.exception))
        self.assertEqual(self.calls, [])

    def test_fresh_cli_execute_show_and_export(self):
        path = self.root / "request.json"
        path.write_bytes(canonical(self.request))
        prefix = [sys.executable, "-m", "matter_audio_voicestudio", "--workspace", str(self.store.root)]
        configured = [*prefix, "--config", str(self.config_path)]
        def call(argv):
            outcome = subprocess.run(argv, capture_output=True, text=True, timeout=20)
            self.assertEqual(outcome.returncode, 0, outcome.stderr + outcome.stdout)
            return json.loads(outcome.stdout)
        resolution = call([*configured, "transcribe", "resolve", "--request", str(path)])
        result = call([*configured, "transcribe", "execute", "--request", str(path), "--expected-resolution-digest", resolution["digest"]["hex"]])
        self.assertEqual(result, call([*prefix, "transcribe", "show", "transcript-1"]))
        call([*prefix, "transcribe", "export", "transcript-1", "--format", "vtt", "--output", str(self.root / "cli.vtt")])
        self.assertTrue((self.root / "cli.vtt").read_bytes().startswith(b"WEBVTT\n\n"))
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
