#!/usr/bin/env python3
"""Export a multimodal trace as a shareable, verifiable approval record.

Builds on plan-multimodal-trace (Day 57): that tool checks a run left
evidence for every step of an InspectablePlan. This tool turns the same
plan + trace into the record a human approver or auditor keeps:

- a Markdown approval record: one row per step, the modalities that
  prove it, and the gaps, if any;
- a JSON manifest: the SHA-256 of the trace itself plus the SHA-256 of
  every referenced evidence file, so anyone holding the record can
  re-hash the files and tell whether the evidence still matches.

Trace rules v1.0.0 (same as plan-multimodal-trace):
- Every step needs evidence in at least one modality.
- An irreversible step needs evidence in at least two modalities.
- A trace event must name a step in the plan, a known modality
  (text, image, audio, video), and a valid ISO-8601 timestamp;
  timestamps must be non-decreasing.
- A referenced file that cannot be found under --base-dir is listed
  as MISSING in the manifest, and that modality does not count as
  proof for the step. The export still completes, so the hole is
  visible in the record instead of hidden by a crash.
- A reference that escapes --base-dir (absolute path or "..") is
  UNSAFE: it is never read, and it does not count as proof.

Exit codes: 0 = export complete, every step proven; 1 = export
complete but with gaps; 2 = INVALID input; 3 = usage error.

Usage:
  python3 plan_trace_export.py examples/plan.json examples/trace.jsonl
  python3 plan_trace_export.py examples/plan.json examples/trace.jsonl --json
  python3 plan_trace_export.py examples/plan.json examples/trace.jsonl \\
      --md examples/record.md --manifest examples/manifest.json
  python3 plan_trace_export.py examples/plan.json examples/trace.jsonl \\
      --base-dir examples --md record.md --manifest manifest.json
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

MODALITIES = ("text", "image", "audio", "video")
RULES_VERSION = "1.0.0"
TOOL_VERSION = "1.0.0"


def parse_ts(value):
    """Parse an ISO-8601 timestamp; return an aware datetime or None.

    A timestamp with no timezone is read as UTC, so naive and aware
    values never meet in a comparison.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_plan(path):
    with open(path, encoding="utf-8") as f:
        plan = json.load(f)
    if not isinstance(plan, dict) or "steps" not in plan:
        raise ValueError("plan must be a JSON object with a 'steps' list")
    if not isinstance(plan["steps"], list):
        raise ValueError("plan 'steps' must be a list")
    return plan


def load_trace(path):
    events = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"trace line {lineno}: not valid JSON ({exc})")
            if not isinstance(event, dict):
                raise ValueError(f"trace line {lineno}: event must be an object")
            events.append(event)
    return events


def validate_trace(plan, events):
    """Return a list of problems; empty means the trace is well formed."""
    problems = []
    step_ids = {s.get("id") for s in plan["steps"] if isinstance(s, dict)}
    prev_ts = None
    for i, event in enumerate(events, 1):
        step = event.get("step_id")
        if step not in step_ids:
            problems.append(f"event {i}: unknown step '{step}'")
        modality = event.get("modality")
        if modality not in MODALITIES:
            problems.append(f"event {i}: unknown modality '{modality}'")
        ts = parse_ts(event.get("ts"))
        if ts is None:
            problems.append(f"event {i}: bad timestamp '{event.get('ts')}'")
        elif prev_ts is not None and ts < prev_ts:
            problems.append(f"event {i}: timestamp out of order")
        if ts is not None:
            prev_ts = ts
    return problems


def coverage(plan, events):
    """Per-step proof status: modalities seen, event count, gap text."""
    result = {}
    for step in plan["steps"]:
        if not isinstance(step, dict) or "id" not in step:
            continue
        step_id = step["id"]
        seen = {e["modality"] for e in events
                if e.get("step_id") == step_id and e.get("modality") in MODALITIES}
        count = sum(1 for e in events if e.get("step_id") == step_id)
        irreversible = step.get("reversible") is False
        needed = 2 if irreversible else 1
        gap = None
        if len(seen) < needed:
            gap = (f"needs {needed} modalities, has {len(seen)}"
                   if seen else "no evidence")
        result[step_id] = {
            "modalities": sorted(seen),
            "count": count,
            "irreversible": irreversible,
            "covered": gap is None,
            "gap": gap,
        }
    return result


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_join(base_dir, ref):
    """Resolve ref under base_dir; return None if it escapes the dir."""
    if not isinstance(ref, str) or not ref:
        return None
    if os.path.isabs(ref):
        return None
    base = os.path.realpath(base_dir)
    target = os.path.realpath(os.path.join(base, ref))
    if os.path.commonpath([base, target]) != base:
        return None
    return target


def build_manifest(plan, events, base_dir, trace_path):
    """Manifest: trace hash, per-file hashes, coverage, verdict.

    Evidence whose file is missing or unsafe is listed with sha256 None
    and does not count as proof: the manifest records the hole.
    """
    with open(trace_path, "rb") as f:
        trace_sha = hashlib.sha256(f.read()).hexdigest()
    evidence = []
    proven = {}  # step_id -> set of modalities with a present file
    for event in events:
        ref = event.get("ref")
        step_id = event.get("step_id")
        modality = event.get("modality")
        if ref is None:
            evidence.append({"step_id": step_id, "modality": modality,
                             "ref": None, "sha256": None, "status": "no-ref"})
            continue
        target = safe_join(base_dir, ref)
        if target is None:
            evidence.append({"step_id": step_id, "modality": modality,
                             "ref": ref, "sha256": None, "status": "unsafe"})
            continue
        if not os.path.isfile(target):
            evidence.append({"step_id": step_id, "modality": modality,
                             "ref": ref, "sha256": None, "status": "missing"})
            continue
        evidence.append({"step_id": step_id, "modality": modality,
                         "ref": ref, "sha256": sha256_file(target),
                         "status": "present"})
        proven.setdefault(step_id, set()).add(modality)
    cov = coverage(plan, events)
    # A modality only proves a step when its file is present and hashed.
    for step_id, info in cov.items():
        have = proven.get(step_id, set())
        needed = 2 if info["irreversible"] else 1
        if len(have) < needed:
            info["covered"] = False
            info["gap"] = (f"needs {needed} verified modalities, "
                           f"has {len(have)}" if have else "no verified evidence")
        else:
            info["covered"] = True
            info["gap"] = None
        info["verified_modalities"] = sorted(have)
    verdict = "PROVEN" if all(i["covered"] for i in cov.values()) else "GAPS"
    return {
        "tool": "plan-trace-export",
        "tool_version": TOOL_VERSION,
        "rules_version": RULES_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "plan_id": plan.get("id"),
        "plan_goal": plan.get("goal"),
        "trace_sha256": trace_sha,
        "evidence": evidence,
        "steps": {sid: {"modalities": info["modalities"],
                        "verified_modalities": info["verified_modalities"],
                        "events": info["count"],
                        "irreversible": info["irreversible"],
                        "covered": info["covered"],
                        "gap": info["gap"]}
                  for sid, info in cov.items()},
        "verdict": verdict,
    }


def render_record(plan, manifest):
    """Markdown approval record from the plan and manifest."""
    lines = [f"# Approval record — {plan.get('id', 'plan')}",
             "",
             f"Goal: {plan.get('goal', '(no goal stated)')}",
             "",
             f"**Verdict: {manifest['verdict']}** — "
             f"generated {manifest['generated_at']} by "
             f"plan-trace-export {manifest['tool_version']} "
             f"(trace rules {manifest['rules_version']}).",
             "",
             "## Steps",
             "",
             "| Step | Action | Irreversible | Modalities | Verified | Proof |",
             "| --- | --- | --- | --- | --- | --- |"]
    steps_by_id = {s["id"]: s for s in plan["steps"]
                   if isinstance(s, dict) and "id" in s}
    for step_id, info in manifest["steps"].items():
        step = steps_by_id.get(step_id, {})
        lines.append(
            f"| {step_id} | {step.get('action', '')} | "
            f"{'yes' if info['irreversible'] else 'no'} | "
            f"{', '.join(info['modalities']) or '—'} | "
            f"{', '.join(info['verified_modalities']) or '—'} | "
            f"{'proven' if info['covered'] else info['gap']} |")
    lines += ["", "## Evidence", "",
              "| Step | Modality | Reference | SHA-256 | Status |",
              "| --- | --- | --- | --- | --- |"]
    for item in manifest["evidence"]:
        lines.append(
            f"| {item['step_id']} | {item['modality']} | {item['ref'] or '—'} | "
            f"{(item['sha256'] or '—')[:12]} | {item['status']} |")
    missing = [i for i in manifest["evidence"] if i["status"] != "present"]
    if missing:
        lines += ["", "## Unverifiable evidence", "",
                  "These references could not be hashed, so they do not "
                  "count as proof. Re-run the export once the files are "
                  "in place.",
                  ""]
        for item in missing:
            lines.append(f"- {item['step_id']} / {item['modality']}: "
                         f"`{item['ref']}` — {item['status']}")
    lines += ["", f"Trace SHA-256: `{manifest['trace_sha256']}`", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("plan")
    parser.add_argument("trace")
    parser.add_argument("--base-dir", default=".",
                        help="root for evidence references (default: .)")
    parser.add_argument("--md", help="write the Markdown record here")
    parser.add_argument("--manifest", help="write the JSON manifest here")
    parser.add_argument("--json", action="store_true",
                        help="print the manifest as JSON")
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.plan)
        events = load_trace(args.trace)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return 3
    problems = validate_trace(plan, events)
    if problems:
        print("INVALID trace:")
        for problem in problems:
            print(f"  - {problem}")
        return 2
    manifest = build_manifest(plan, events, args.base_dir, args.trace)
    record = render_record(plan, manifest)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(record)
    if args.manifest:
        with open(args.manifest, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)
            f.write("\n")
    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        print(record)
    return 0 if manifest["verdict"] == "PROVEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
