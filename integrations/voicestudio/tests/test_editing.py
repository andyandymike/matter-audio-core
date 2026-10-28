"""Exercise local transcript-guided edits with synthetic PCM and saved receipts."""

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from matter_audio_core.actions import ActionService
from matter_audio_core.artifacts import ArtifactStore
from matter_audio_core.contracts import canonical, digest, fingerprint, parse_json
from matter_audio_core.errors import AudioError
from matter_audio_core.media import PCM, decode_wav, encode_wav
from matter_audio_core.sessions import SessionService
from matter_audio_voicestudio import client, editing
from matter_audio_voicestudio.cli import main
from matter_audio_voicestudio.service import TranscriptionService


class TranscriptEditingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="matter-transcript-edit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = ArtifactStore(self.root / "workspace")
        self.samples = [100 + 10 * index for index in range(24)]
        self.source_path = self.root / "source.wav"
        raw = self.wav(self.samples)
        # Non-audio RIFF bytes must survive in the immutable source as well.
        raw += b"JUNK" + struct.pack("<I", 4) + b"keep"
        self.original = raw[:4] + struct.pack("<I", len(raw) - 8) + raw[8:]
        self.source_path.write_bytes(self.original)
        self.source = self.store.import_wav(self.source_path, "source")["outputs"][0]
        self.replacement_samples = [2000 + 20 * index for index in range(30)]
        self.replacement = self.import_pcm("replacement", self.replacement_samples)
        self.config = {"schema": "matter-voicestudio-config/v1", "base_url": "http://127.0.0.1:9",
                       "model": "synthetic-asr", "service_revision": "synthetic-test", "timeout_seconds": 30}
        self.response = {"text": "Echo middle Echo end", "language": "en", "model": "synthetic-asr",
                         "words": [{"word": text, "start": start / 8000, "end": (start + 6) / 8000}
                                   for text, start in (("Echo", 0), ("middle", 6), ("Echo", 12), ("end", 18))],
                         "segments": [{"text": "Echo middle", "start": 0, "end": 12 / 8000},
                                      {"text": "Echo end", "start": 12 / 8000, "end": 24 / 8000}]}
        self.receipt = self.transcribe("transcript", self.response)
        self.transcript_id = self.receipt["transcript_asset_id"]
        self.request = {"schema": "matter-transcript-edit/v1", "request_id": "edit-plan",
                        "transcription_request_id": "transcript", "transcript_asset_id": self.transcript_id,
                        "source_asset_id": self.source["asset_id"], "selection": {"unit": "word", "index": 3},
                        "edit": {"kind": "extract"}}
        self.network = patch("socket.create_connection", side_effect=AssertionError("Unexpected network access"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.remote = patch.object(client, "transcribe", side_effect=AssertionError("Unexpected transcription"))
        self.remote.start()
        self.addCleanup(self.remote.stop)

    @staticmethod
    def wav(samples, rate=8000, channels=1):
        return encode_wav(PCM(struct.pack(f"<{len(samples)}h", *samples), rate, channels))

    def import_pcm(self, name, samples, rate=8000, channels=1):
        path = self.root / (name + ".wav")
        path.write_bytes(self.wav(samples, rate, channels))
        return self.store.import_wav(path, name)["outputs"][0]

    def transcribe(self, request_id, response, source=None):
        source = self.source if source is None else source
        request = {"schema": "matter-transcription-request/v1", "request_id": request_id,
                   "asset_id": source["asset_id"], "timestamps": "word"}
        service = TranscriptionService(self.store, self.config)
        with patch.object(client, "transcribe", return_value=copy.deepcopy(response)) as remote:
            result = service.execute(request, service.resolve(request)["digest"]["hex"])
        self.assertEqual(remote.call_count, 1)
        self.assertEqual(result["status"], "succeeded")
        return result

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(AudioError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    def assert_absent(self, request_id):
        self.assert_error("request_not_found", self.store.show_request, request_id)

    def plan(self, **changes):
        return editing.plan(self.store, {**self.request, **changes})

    def execute(self, saved):
        return editing.execute(self.store, saved["plan"]["asset_id"], saved["plan_digest"])

    def pcm(self, result):
        return list(decode_wav(self.store.asset(result["outputs"][0]["asset_id"])[1]).samples())

    def replace(self, **changes):
        return {"kind": "replace", "replacement_asset_id": self.replacement["asset_id"],
                "replacement_start_frame": 6, "transition_frames": 0, **changes}

    def session(self):
        service = SessionService(self.store)
        service.mutate("create", {"schema": "matter-session-create/v1", "request_id": "create-session",
                                  "session_id": "speech", "name": "Synthetic speech", "asset_id": self.source["asset_id"]})
        return service

    @staticmethod
    def lock(service, revision, regions, request_id):
        return service.mutate("constraints", {"schema": "matter-constraints-set/v1", "request_id": request_id,
                              "session_id": "speech", "expected_revision": revision, "regions": regions})

    def clone_plan(self, saved, name, *, document=None, media=None, parents=None, role=None):
        record, _ = self.store.asset(saved["plan"]["asset_id"])
        def produce(publication):
            publication.add(canonical(saved["document"] if document is None else document),
                            record["media"] if media is None else media,
                            parents=record["parents"] if parents is None else parents,
                            role=record["role"] if role is None else role)
            return {}
        return self.store.transact(name, {}, produce)["outputs"][0]["asset_id"]

    def rewrite_receipt(self, request_id, mutate):
        """Keep store checksums valid to test the adapter's semantic checks."""
        original = self.store.show_request(request_id)
        group = self.store.root / "objects" / original["group_id"]
        manifest = group / "manifest.json"
        sha = group / "manifest.sha256"
        claim = self.store._request_path(request_id) / "claim.json"
        backups = {path: path.read_bytes() for path in (manifest, sha, claim)}
        changed = copy.deepcopy(original)
        mutate(changed)
        changed["binding_digest"] = fingerprint(changed["binding"])
        manifest.write_bytes(canonical(changed))
        sha.write_text(fingerprint(changed)["hex"], encoding="ascii")
        claimed = parse_json(backups[claim])
        claimed["binding_digest"] = changed["binding_digest"]
        claim.write_bytes(canonical(claimed))
        return backups

    def test_exact_index_extract_is_local_and_keeps_all_source_bytes(self):
        before = self.store.list_assets()["assets"]
        with patch.object(ActionService, "execute", side_effect=AssertionError("Preview executed PCM")):
            saved = self.plan()
        self.assertEqual(len(self.store.list_assets()["assets"]), len(before) + 1)
        core = saved["document"]["core_request"]
        self.assertEqual(core["operation"], "trim/v1")
        self.assertEqual(core["parameters"], {"start_frame": 12, "end_frame": 18})
        self.assertEqual(core["inputs"], [self.source["asset_id"]])
        self.assertEqual(saved["document"], editing.show(self.store, saved["plan"]["asset_id"])["document"])
        result = self.execute(saved)
        self.assertEqual(self.pcm(result), self.samples[12:18])
        self.assertEqual(result["audio_model_calls"], 0)
        self.assertEqual(result["network_requests"], 0)
        self.assertEqual(self.source_path.read_bytes(), self.original)
        self.assertEqual(self.store.asset(self.source["asset_id"])[1], self.original)
        self.assertFalse((self.store.root / "sessions.sqlite3").exists())

    def test_text_occurrence_is_explicit_and_normalization_does_not_guess(self):
        self.assert_error("selection_ambiguous", self.plan, selection={"unit": "word", "text": "Echo"})
        self.assert_absent("edit-plan")
        for occurrence, expected in ((1, self.samples[:6]), (2, self.samples[12:18])):
            saved = self.plan(request_id=f"echo-{occurrence}", selection={"unit": "word", "text": "Echo", "occurrence": occurrence})
            self.assertEqual(self.pcm(self.execute(saved)), expected)
        for text in ("echo", "Echo.", "cho"):
            self.assert_error("selection_not_found", self.plan, selection={"unit": "word", "text": text})
        self.assert_error("selection_not_found", self.plan, selection={"unit": "word", "text": "Echo", "occurrence": 3})
        response = copy.deepcopy(self.response)
        response["words"][0]["word"] = "  café\t now "
        response["words"][2]["word"] = "cafe\u0301 now"
        transcript = self.transcribe("unicode-transcript", response)
        base = {**self.request, "transcription_request_id": "unicode-transcript", "transcript_asset_id": transcript["transcript_asset_id"]}
        self.assert_error("selection_ambiguous", editing.plan, self.store,
                          {**base, "selection": {"unit": "word", "text": "café now"}})
        saved = editing.plan(self.store, {**base, "selection": {"unit": "word", "text": "cafe\u0301  now", "occurrence": 2}})
        self.assertEqual(self.pcm(self.execute(saved)), self.samples[12:18])

    def test_inclusive_entry_ranges_and_padding_are_frozen_as_half_open_frames(self):
        saved = self.plan(selection={"unit": "word", "first_index": 2, "last_index": 3},
                          padding_frames={"before": 2, "after": 3})
        self.assertEqual(saved["document"]["core_request"]["parameters"], {"start_frame": 4, "end_frame": 21})
        self.assertEqual(self.pcm(self.execute(saved)), self.samples[4:21])
        segment = self.plan(request_id="segment", selection={"unit": "segment", "index": 2})
        self.assertEqual(self.pcm(self.execute(segment)), self.samples[12:24])
        for selection in ({"unit": "word", "index": 5}, {"unit": "word", "first_index": 3, "last_index": 2}):
            self.assert_error("invalid_selection", self.plan, request_id="invalid-range", selection=selection)
            self.assert_absent("invalid-range")
        self.assert_error("invalid_padding", self.plan, request_id="padding-before", selection={"unit": "word", "index": 1}, padding_frames={"before": 1, "after": 0})
        self.assert_error("invalid_padding", self.plan, request_id="padding-after", selection={"unit": "word", "index": 4}, padding_frames={"before": 0, "after": 1})
        self.assert_absent("padding-before")
        self.assert_absent("padding-after")

    def test_missing_and_empty_timings_cannot_create_edit_claims(self):
        variants = [({"text": "Echo", "segments": self.response["segments"]}, "selection_unavailable"),
                    ({"text": "Echo", "words": [{"word": "Echo", "start": 0, "end": 0}]}, "invalid_selection")]
        for index, (response, code) in enumerate(variants):
            name = f"timing-{index}"
            receipt = self.transcribe(name, response)
            request = {**self.request, "request_id": f"no-edit-{index}", "transcription_request_id": name,
                       "transcript_asset_id": receipt["transcript_asset_id"], "selection": {"unit": "word", "index": 1}}
            self.assert_error(code, editing.plan, self.store, request)
            self.assert_absent(request["request_id"])

    def test_inspection_paginates_one_based_entries_without_editing_or_sending(self):
        before = self.store.list_assets()["assets"]
        first = editing.inspect_transcript(self.store, "transcript", "word", 0, 2)
        self.assertEqual(first["total_items"], 4)
        self.assertEqual(first["next_offset"], 2)
        self.assertEqual([(item["index"], item["text"], item["start_frame"], item["end_frame"])
                          for item in first["items"]], [(1, "Echo", 0, 6), (2, "middle", 6, 12)])
        second = editing.inspect_transcript(self.store, "transcript", "word", first["next_offset"], 2)
        self.assertEqual([item["index"] for item in second["items"]], [3, 4])
        self.assertIsNone(second["next_offset"])
        self.assertEqual(first["source"]["asset_id"], self.source["asset_id"])
        self.assertEqual(first["transcript"]["asset_id"], self.transcript_id)
        self.assertEqual(first["receipt"]["digest"], fingerprint(self.receipt))
        self.assertEqual(first["network_requests"], 0)
        self.assertFalse(first["timing"]["precision_verified"])
        segment = editing.inspect_transcript(self.store, "transcript", "segment", 1, 1)
        self.assertEqual(segment["items"][0]["text"], "Echo end")
        self.assertEqual(segment["items"][0]["index"], 2)
        for unit, offset, limit in (("sentence", 0, 2), ("word", -1, 2), ("word", True, 2),
                                    ("word", 0, True), ("word", 0, 0), ("word", 0, 129)):
            self.assert_error("invalid_selection", editing.inspect_transcript, self.store, "transcript", unit, offset, limit)
        self.assertEqual(self.store.list_assets()["assets"], before)

    def test_overlapping_entries_use_minimum_start_maximum_end_and_report_other_speech(self):
        response = {"text": "one two three four", "words": [
            {"word": text, "start": start / 8000, "end": end / 8000}
            for text, start, end in (("one", 0, 12), ("two", 6, 18), ("three", 9, 10), ("four", 12, 24))]}
        receipt = self.transcribe("overlap", response)
        saved = self.plan(transcription_request_id="overlap", transcript_asset_id=receipt["transcript_asset_id"],
                          selection={"unit": "word", "first_index": 1, "last_index": 3})
        selection = saved["document"]["binding"]["selection"]
        self.assertEqual(selection["range"], {"start_frame": 0, "end_frame": 18})
        self.assertEqual(selection["overlap_with_unselected"], {"count": 1, "indices": [4], "truncated": False})
        self.assertEqual(selection["timing_kind"], "model_estimate")
        self.assertFalse(selection["precision_verified"])
        self.assertEqual(self.pcm(self.execute(saved)), self.samples[:18])

    def test_inspection_budget_includes_next_offset_and_keeps_pagination_moving(self):
        reference = editing.inspect_transcript(self.store, "transcript", "word", 0, 3)
        budget = len(canonical(reference)) - 1
        # The full response is one byte too large, though it fits if the cursor
        # is omitted. Shrink the page instead of failing a readable transcript.
        self.assertLess(len(canonical({key: value for key, value in reference.items() if key != "next_offset"})), budget)
        with patch.object(editing, "MAX_JSON_BYTES", budget):
            first = editing.inspect_transcript(self.store, "transcript", "word", 0, 3)
            self.assertLessEqual(len(canonical(first)), budget)
            self.assertEqual([item["index"] for item in first["items"]], [1, 2])
            self.assertEqual(first["next_offset"], 2)
            second = editing.inspect_transcript(self.store, "transcript", "word", first["next_offset"], 3)
            self.assertEqual([item["index"] for item in second["items"]], [3, 4])
            self.assertIsNone(second["next_offset"])
            self.assertLessEqual(len(canonical(second)), budget)

    def test_schema_rejects_mixed_selectors_noninteger_indices_and_unsupported_edits(self):
        selections = [{"unit": "word", "index": 0}, {"unit": "word", "index": True},
                      {"unit": "word", "index": 1.0}, {"unit": "word", "index": 1, "text": "Echo"},
                      {"unit": "word", "text": "Echo", "occurrence": 0},
                      {"unit": "word", "first_index": 1}, {"unit": "sentence", "index": 1}]
        variants = [{**self.request, "selection": selection} for selection in selections]
        variants += [{**self.request, "edit": {"kind": "fade"}},
                     {**self.request, "edit": {"kind": "extract", "db": -3}},
                     {**self.request, "padding_frames": {"before": -1, "after": 0}},
                     {**self.request, "padding_frames": {"before": 0, "after": True}},
                     {**self.request, "edit": self.replace(transition_frames=1.0)},
                     {**self.request, "unexpected": "ignored?"}]
        for key in ("transcription_request_id", "transcript_asset_id", "source_asset_id", "selection", "edit"):
            variants.append({name: value for name, value in self.request.items() if name != key})
        for index, request in enumerate(variants):
            with self.subTest(index=index):
                self.assert_error("invalid_request", editing.plan, self.store, request)
        self.assert_absent("edit-plan")

    def test_selection_and_complete_publication_size_limits_reject_before_claim(self):
        response = {"text": "small", "words": [{"word": "w", "start": 0, "end": 6 / 8000}] * 129}
        receipt = self.transcribe("many-entries", response)
        self.assert_error("invalid_selection", self.plan, transcription_request_id="many-entries",
                          transcript_asset_id=receipt["transcript_asset_id"],
                          selection={"unit": "word", "first_index": 1, "last_index": 129})
        response["words"] = [{"word": "w" * 4400, "start": 0, "end": 6 / 8000}] * 128
        receipt = self.transcribe("large-entries", response)
        with self.assertRaises(AudioError) as caught:
            self.plan(transcription_request_id="large-entries", transcript_asset_id=receipt["transcript_asset_id"],
                      selection={"unit": "word", "first_index": 1, "last_index": 128})
        self.assertIn(caught.exception.code, ("publication_too_large", "json_too_large"))
        self.assert_absent("edit-plan")

    def test_replace_uses_explicit_source_offset_and_only_changes_selected_pcm(self):
        for transition, window in ((0, self.replacement_samples[6:12]),
                                   (2, [220, 2140, 2160, 2180, 2200, 270])):
            saved = self.plan(request_id=f"replace-{transition}", edit=self.replace(transition_frames=transition))
            core = saved["document"]["core_request"]
            self.assertEqual(core["operation"], "splice/v1")
            self.assertEqual(core["inputs"], [self.source["asset_id"], self.replacement["asset_id"]])
            self.assertEqual(core["parameters"], {"start_frame": 12, "end_frame": 18, "replacement_start_frame": 6, "transition_frames": transition})
            output = self.pcm(self.execute(saved))
            self.assertEqual(output, self.samples[:12] + window + self.samples[18:])
            self.assertEqual(len(output), len(self.samples))
        self.assertEqual(self.store.asset(self.source["asset_id"])[1], self.original)
        self.assertEqual(self.store.asset(self.replacement["asset_id"])[1], self.wav(self.replacement_samples))

    def test_invalid_replacement_format_length_and_transitions_fail_before_claim(self):
        rate = self.import_pcm("other-rate", self.replacement_samples, rate=16000)
        stereo = self.import_pcm("stereo", self.replacement_samples * 2, channels=2)
        for index, edit in enumerate((self.replace(replacement_asset_id=rate["asset_id"]),
                                     self.replace(replacement_asset_id=stereo["asset_id"]),
                                     self.replace(replacement_start_frame=27), self.replace(transition_frames=4))):
            with self.subTest(edit=edit), self.assertRaises(AudioError):
                self.plan(request_id=f"bad-replacement-{index}", edit=edit)
            self.assert_absent(f"bad-replacement-{index}")

    def test_exact_transcription_receipt_source_and_asset_are_required(self):
        duplicate = self.root / "same-source.wav"
        duplicate.write_bytes(self.original)
        same = self.store.import_wav(duplicate, "same-source")["outputs"][0]
        other = self.transcribe("other-transcript", self.response)
        variants = [{"source_asset_id": same["asset_id"]}, {"transcript_asset_id": other["transcript_asset_id"]},
                    {"transcription_request_id": "other-transcript"}, {"transcript_asset_id": self.source["asset_id"]}]
        for changes in variants:
            with self.subTest(changes=changes):
                self.assert_error("transcript_source_mismatch", self.plan, **changes)
        self.assert_absent("edit-plan")

    def test_receipt_parameter_and_source_tampering_is_not_promoted_to_edit_evidence(self):
        mutations = [lambda r: r["binding"]["source"].update(asset_id=self.replacement["asset_id"]),
                     lambda r: r["binding"]["parameters"].update(language="fr"),
                     lambda r: r.update(transcript_asset_id=self.source["asset_id"]),
                     lambda r: r["outputs"][0].update(parents=[]),
                     lambda r: r["outputs"][0].update(media={"kind": "unrelated", "content_type": "application/json"})]
        for mutate in mutations:
            backups = self.rewrite_receipt("transcript", mutate)
            try:
                self.assert_error("transcript_source_mismatch", self.plan)
            finally:
                for path, data in backups.items():
                    path.write_bytes(data)
        self.assert_absent("edit-plan")

    def test_forged_normalized_frames_and_verified_claims_fail_despite_valid_store_checksums(self):
        record, raw = self.store.asset(self.transcript_id)
        path = self.store.root / record["locator"]
        mutations = [lambda doc: doc["words"][2].update(start_frame=13),
                     lambda doc: doc["timing"].update(precision_verified=True),
                     lambda doc: doc.update(source={"asset_id": self.replacement["asset_id"],
                                                   "digest": self.replacement["digest"], "media": self.replacement["media"]})]
        for mutate in mutations:
            document = parse_json(raw)
            mutate(document)
            changed = canonical(document)
            backups = self.rewrite_receipt("transcript", lambda receipt: receipt["outputs"][0].update(
                digest=digest(changed), byte_count=len(changed)))
            path.write_bytes(changed)
            try:
                self.assert_error("transcript_source_mismatch", self.plan)
            finally:
                path.write_bytes(raw)
                for location, data in backups.items():
                    location.write_bytes(data)
        self.assert_absent("edit-plan")

    def test_plan_request_conflict_and_confirmation_digest_are_enforced(self):
        saved = self.plan()
        self.assertEqual(saved, self.plan())
        self.assert_error("request_conflict", self.plan, selection={"unit": "word", "index": 1})
        self.assert_error("plan_digest_conflict", editing.execute, self.store, saved["plan"]["asset_id"], "0" * 64)
        self.assert_absent(saved["document"]["core_request"]["request_id"])
        completed = self.execute(saved)
        with patch.object(ActionService, "execute", side_effect=AssertionError("Completed edit was rerun")):
            self.assertEqual(completed, self.execute(saved))

    def test_plan_document_and_asset_lineage_tampering_are_rejected(self):
        saved = self.plan(edit=self.replace())
        variants = []
        document = copy.deepcopy(saved["document"])
        document["core_request"]["parameters"]["start_frame"] = 1
        variants.append({"document": document})
        document = copy.deepcopy(saved["document"])
        document["expected_resolution_digest"] = "0" * 64
        variants.append({"document": document})
        record, _ = self.store.asset(saved["plan"]["asset_id"])
        variants += [{"parents": []}, {"parents": list(reversed(record["parents"]))},
                     {"media": {"kind": "unrelated", "content_type": "application/json"}}, {"role": "transcript"}]
        for index, changes in enumerate(variants):
            forged = self.clone_plan(saved, f"forged-plan-{index}", **changes)
            for function, args in ((editing.show, (self.store, forged)),
                                   (editing.execute, (self.store, forged, saved["plan_digest"]))):
                with self.subTest(index=index, function=function.__name__), self.assertRaises(AudioError) as caught:
                    function(*args)
                self.assertIn(caught.exception.code, ("invalid_edit_plan", "edit_plan_mismatch", "plan_digest_conflict"))
        self.assert_absent(saved["document"]["core_request"]["request_id"])

    def test_protection_rejects_writes_and_new_lock_changes_but_completed_replays(self):
        service = self.session()
        self.lock(service, 1, [{"start_frame": 0, "end_frame": 6}], "lock-prefix")
        protection = {"session_id": "speech", "revision": 2}
        saved = self.plan(edit=self.replace(), protection=protection)
        pending = self.plan(request_id="unexecuted", edit=self.replace(), protection=protection)
        completed = self.execute(saved)
        self.assertEqual(service.show("speech")["session"]["head_revision"], 2)
        self.lock(service, 2, [{"start_frame": 12, "end_frame": 18}], "lock-target")
        with patch.object(ActionService, "execute", side_effect=AssertionError("Completed edit was rerun")):
            self.assertEqual(completed, self.execute(saved))
            self.assertEqual(saved, self.plan(edit=self.replace(), protection=protection))
        self.assert_error("constraint_conflict", self.execute, pending)
        self.assert_absent(pending["document"]["core_request"]["request_id"])
        self.assert_error("constraint_violation", self.plan, request_id="blocked", edit=self.replace(),
                          protection={"session_id": "speech", "revision": 3})
        self.assert_absent("blocked")
        self.assertEqual(service.show("speech")["session"]["head_revision"], 3)

    def test_pending_plan_and_action_are_not_retried(self):
        saved = self.plan()
        def interrupt(publication):
            raise KeyboardInterrupt("Synthetic interruption")
        core = saved["document"]
        with self.assertRaises(KeyboardInterrupt):
            self.store.transact(core["core_request"]["request_id"], {"resolution": core["core_resolution"]}, interrupt)
        with patch.object(ActionService, "execute", side_effect=AssertionError("Pending edit retried")):
            self.assert_error("recovery_pending", self.execute, saved)
        failed_plan = self.plan(request_id="failed-action-plan")
        failed_document = failed_plan["document"]
        def fail(publication):
            raise AudioError("synthetic_failure", "Interrupted PCM operation fixture")
        failure = self.store.transact(failed_document["core_request"]["request_id"],
                                      {"resolution": failed_document["core_resolution"]}, fail)
        with patch.object(ActionService, "execute", side_effect=AssertionError("Failed edit retried")):
            failed = self.execute(failed_plan)
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["error"], failure["error"])
            self.assertEqual(failed, self.execute(failed_plan))
        request = {**self.request, "request_id": "pending-plan"}
        with patch.object(self.store, "transact", side_effect=lambda request_id, binding, producer: ArtifactStore.transact(self.store, request_id, binding, interrupt)):
            with self.assertRaises(KeyboardInterrupt):
                editing.plan(self.store, request)
        self.assert_error("recovery_pending", editing.plan, self.store, request)

    def test_failed_and_pending_transcriptions_cannot_be_edited_or_retried(self):
        def fail(publication):
            raise AudioError("synthetic_failure", "No service call")
        def interrupt(publication):
            raise KeyboardInterrupt("Synthetic interruption")
        self.store.transact("failed-transcript", {}, fail)
        with self.assertRaises(KeyboardInterrupt):
            self.store.transact("pending-transcript", {}, interrupt)
        self.assert_error("transcript_source_mismatch", self.plan, transcription_request_id="failed-transcript")
        self.assert_error("recovery_pending", self.plan, transcription_request_id="pending-transcript")
        self.assert_absent("edit-plan")

    def test_cli_local_edit_does_not_require_config_or_network(self):
        request_path = self.root / "edit.json"
        request_path.write_bytes(canonical(self.request))
        prefix = ["--workspace", str(self.store.root)]
        def call(args):
            with patch.object(client, "load_config", side_effect=AssertionError("Editing loaded service config")), contextlib.redirect_stdout(io.StringIO()) as output:
                result = main([*prefix, *args])
            self.assertEqual(result, 0, output.getvalue())
            return json.loads(output.getvalue())
        saved = call(["edit", "plan", "--request", str(request_path)])
        shown = call(["edit", "show", saved["plan"]["asset_id"]])
        self.assertEqual(shown["document"], saved["document"])
        command = ["edit", "execute", saved["plan"]["asset_id"], "--expected-plan-digest", saved["plan_digest"]]
        result = call(command)
        self.assertEqual(result, call(command))
        self.assertEqual(self.pcm(result), self.samples[12:18])
        self.assertEqual(result["network_requests"], 0)
        # A genuinely fresh interpreter also needs no configuration file/secret.
        process = subprocess.run([sys.executable, "-m", "matter_audio_voicestudio", *prefix, *command],
                                 capture_output=True, text=True, timeout=20, env=os.environ.copy())
        self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
        self.assertEqual(json.loads(process.stdout), result)


if __name__ == "__main__":
    unittest.main()
