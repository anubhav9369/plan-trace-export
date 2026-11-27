"""Tests for plan_trace_export.py (stdlib only)."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import plan_trace_export as pte

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(HERE, "..", "examples")
MODULE = os.path.join(HERE, "..", "plan_trace_export.py")


def load(name="trace.jsonl"):
    return (pte.load_plan(os.path.join(EXAMPLES, "plan.json")),
            pte.load_trace(os.path.join(EXAMPLES, name)))


class TestTraceExport(unittest.TestCase):
    def test_good_trace_exports_proven(self):
        plan, events = load()
        self.assertEqual(pte.validate_trace(plan, events), [])
        manifest = pte.build_manifest(plan, events, EXAMPLES,
                                      os.path.join(EXAMPLES, "trace.jsonl"))
        self.assertEqual(manifest["verdict"], "PROVEN")
        self.assertTrue(all(s["covered"] for s in manifest["steps"].values()))
        self.assertEqual(manifest["steps"]["s4"]["verified_modalities"],
                         ["audio", "image", "text"])

    def test_manifest_hashes_match_real_files(self):
        plan, events = load()
        manifest = pte.build_manifest(plan, events, EXAMPLES,
                                      os.path.join(EXAMPLES, "trace.jsonl"))
        for item in manifest["evidence"]:
            self.assertEqual(item["status"], "present")
            with open(os.path.join(EXAMPLES, item["ref"]), "rb") as f:
                self.assertEqual(item["sha256"],
                                 hashlib.sha256(f.read()).hexdigest())
        with open(os.path.join(EXAMPLES, "trace.jsonl"), "rb") as f:
            self.assertEqual(manifest["trace_sha256"],
                             hashlib.sha256(f.read()).hexdigest())

    def test_missing_file_is_listed_and_breaks_proof(self):
        plan, events = load()
        events = [dict(e) for e in events]
        # Remove every verified file for s1, so it has no proof left.
        for e in events:
            if e["step_id"] == "s1":
                e["ref"] = "logs/does-not-exist.log"
        manifest = pte.build_manifest(plan, events, EXAMPLES,
                                      os.path.join(EXAMPLES, "trace.jsonl"))
        missing = [i for i in manifest["evidence"]
                   if i["status"] == "missing"]
        self.assertEqual(len(missing), 2)
        self.assertIsNone(missing[0]["sha256"])
        # s1 loses all verified modalities, so the export has gaps.
        self.assertEqual(manifest["verdict"], "GAPS")
        self.assertFalse(manifest["steps"]["s1"]["covered"])

    def test_irreversible_step_needs_two_verified_modalities(self):
        plan, events = load()
        events = [e for e in events
                  if not (e["step_id"] == "s4" and e["modality"] != "text")]
        manifest = pte.build_manifest(plan, events, EXAMPLES,
                                      os.path.join(EXAMPLES, "trace.jsonl"))
        self.assertFalse(manifest["steps"]["s4"]["covered"])
        self.assertIn("2 verified", manifest["steps"]["s4"]["gap"])

    def test_ref_escaping_base_dir_is_unsafe_and_unproven(self):
        plan, events = load()
        events = [dict(e) for e in events]
        # Point every s1 reference outside the base dir.
        for e in events:
            if e["step_id"] == "s1":
                e["ref"] = "../outside-secret.txt"
        manifest = pte.build_manifest(plan, events, EXAMPLES,
                                      os.path.join(EXAMPLES, "trace.jsonl"))
        unsafe = [i for i in manifest["evidence"]
                  if i["status"] == "unsafe"]
        self.assertEqual(len(unsafe), 2)
        self.assertEqual(manifest["verdict"], "GAPS")

    def test_absolute_ref_is_unsafe(self):
        plan, events = load()
        events = [dict(e) for e in events]
        events[0]["ref"] = "/etc/passwd"
        manifest = pte.build_manifest(plan, events, EXAMPLES,
                                      os.path.join(EXAMPLES, "trace.jsonl"))
        self.assertTrue(any(i["status"] == "unsafe"
                            for i in manifest["evidence"]))

    def test_record_names_plan_goal_steps_and_verdict(self):
        plan, events = load()
        manifest = pte.build_manifest(plan, events, EXAMPLES,
                                      os.path.join(EXAMPLES, "trace.jsonl"))
        record = pte.render_record(plan, manifest)
        self.assertIn("Approval record", record)
        self.assertIn(plan["goal"], record)
        self.assertIn("**Verdict: PROVEN**", record)
        self.assertIn("Trace SHA-256", record)
        for step_id in ("s1", "s2", "s3", "s4"):
            self.assertIn(step_id, record)

    def test_record_lists_unverifiable_evidence(self):
        plan, events = load()
        events = [dict(e) for e in events]
        events[0]["ref"] = "logs/does-not-exist.log"
        manifest = pte.build_manifest(plan, events, EXAMPLES,
                                      os.path.join(EXAMPLES, "trace.jsonl"))
        record = pte.render_record(plan, manifest)
        self.assertIn("Unverifiable evidence", record)
        self.assertIn("does-not-exist.log", record)

    def test_invalid_trace_and_usage_errors(self):
        plan, events = load()
        bad = [dict(e) for e in events]
        bad[0]["step_id"] = "no-such-step"
        self.assertTrue(pte.validate_trace(plan, bad))
        bad_ts = [dict(e) for e in events]
        bad_ts[1]["ts"] = "not-a-date"
        self.assertTrue(pte.validate_trace(plan, bad_ts))
        out_of_order = [dict(e) for e in events]
        out_of_order[0]["ts"], out_of_order[1]["ts"] = (
            out_of_order[1]["ts"], out_of_order[0]["ts"])
        self.assertTrue(pte.validate_trace(plan, out_of_order))

    def test_cli_exit_codes(self):
        plan_path = os.path.join(EXAMPLES, "plan.json")
        trace_path = os.path.join(EXAMPLES, "trace.jsonl")
        good = subprocess.run([sys.executable, MODULE, plan_path, trace_path,
                               "--base-dir", EXAMPLES],
                              capture_output=True, text=True)
        self.assertEqual(good.returncode, 0)
        self.assertIn("Verdict: PROVEN", good.stdout)
        with tempfile.TemporaryDirectory() as tmp:
            gap_trace = os.path.join(tmp, "trace.jsonl")
            with open(gap_trace, "w") as f:
                f.write('{"step_id": "s1", "ts": "2026-11-26T09:00:00Z", '
                        '"modality": "text", "kind": "fetch", '
                        '"ref": "logs/fetch.log", "note": "ok"}\n')
            gaps = subprocess.run([sys.executable, MODULE, plan_path,
                                   gap_trace, "--base-dir", EXAMPLES],
                                  capture_output=True, text=True)
            self.assertEqual(gaps.returncode, 1)
            self.assertIn("Verdict: GAPS", gaps.stdout)
            bad_trace = os.path.join(tmp, "bad.jsonl")
            with open(bad_trace, "w") as f:
                f.write('{"step_id": "zzz", "ts": "2026-11-26T09:00:00Z", '
                        '"modality": "text"}\n')
            invalid = subprocess.run([sys.executable, MODULE, plan_path,
                                      bad_trace],
                                     capture_output=True, text=True)
            self.assertEqual(invalid.returncode, 2)
            usage = subprocess.run([sys.executable, MODULE, plan_path,
                                    os.path.join(tmp, "missing.jsonl")],
                                   capture_output=True, text=True)
            self.assertEqual(usage.returncode, 3)

    def test_cli_writes_md_and_manifest_files(self):
        plan_path = os.path.join(EXAMPLES, "plan.json")
        trace_path = os.path.join(EXAMPLES, "trace.jsonl")
        with tempfile.TemporaryDirectory() as tmp:
            md_path = os.path.join(tmp, "record.md")
            man_path = os.path.join(tmp, "manifest.json")
            run = subprocess.run(
                [sys.executable, MODULE, plan_path, trace_path,
                 "--base-dir", EXAMPLES, "--md", md_path,
                 "--manifest", man_path],
                capture_output=True, text=True)
            self.assertEqual(run.returncode, 0)
            with open(md_path, encoding="utf-8") as f:
                self.assertIn("Verdict: PROVEN", f.read())
            with open(man_path, encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(manifest["verdict"], "PROVEN")
            self.assertEqual(len(manifest["evidence"]), 9)


if __name__ == "__main__":
    unittest.main()
