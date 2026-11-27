# plan-trace-export

Export a **multimodal trace** as a shareable, verifiable approval record
— part of the trustworthy agentic AI prototype for BRJP26100123
(Week 4: multimodal tracing, after `plan-multimodal-trace`).

## Why

Checking a trace is not the same as keeping a record of it. The
approver who signs off a plan, and the auditor who reads it a month
later, need something they can hold: one page that says what each step
was proven by, and a manifest that lets them re-hash the evidence
files and tell whether anything changed since. This tool produces
both from the same plan + trace files `plan-multimodal-trace` reads.

## Use

```bash
python3 plan_trace_export.py examples/plan.json examples/trace.jsonl
python3 plan_trace_export.py examples/plan.json examples/trace.jsonl --json
python3 plan_trace_export.py examples/plan.json examples/trace.jsonl \
    --base-dir examples --md examples/record.md --manifest examples/manifest.json
```

Exit codes: `0` = export complete, every step proven; `1` = export
complete but with gaps; `2` = INVALID input; `3` = usage error
(bad JSON, missing file).

## Rules (trace v1.0.0, same as plan-multimodal-trace)

- Every step needs evidence in at least one modality.
- An **irreversible step needs evidence in at least two modalities**.
- A trace event must name a step in the plan, a known modality
  (`text`, `image`, `audio`, `video`), and a valid ISO-8601 timestamp;
  timestamps must be non-decreasing.
- A modality only counts as proof when its referenced file exists
  under `--base-dir` and is hashed into the manifest. A missing file
  is listed as MISSING and the export still completes, so the hole is
  visible in the record instead of hidden by a crash.
- A reference that escapes `--base-dir` (an absolute path or `..`)
  is UNSAFE: it is never read and never counts as proof.

## Outputs

- **Markdown record** (`--md`): plan goal, one row per step with the
  modalities seen and verified, an evidence table with the first 12
  hex digits of each SHA-256, and an "Unverifiable evidence" section
  when anything could not be hashed.
- **JSON manifest** (`--manifest`): tool and rules versions, the
  SHA-256 of the trace file itself, every evidence file's SHA-256 and
  status (`present` / `missing` / `unsafe` / `no-ref`), per-step
  coverage, and the verdict (`PROVEN` / `GAPS`).

## Examples

`examples/` holds the same plan and trace as `plan-multimodal-trace`,
plus the small evidence files the trace points at, and the record and
manifest this tool generates from them (`record.md`, `manifest.json`).

## Tests

```bash
python3 -m unittest discover -s tests -v
```

11 tests, stdlib only.
