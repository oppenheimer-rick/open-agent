"""
Prediction-file schema validation for every benchmark.

Run BEFORE feeding predictions to an evaluator to catch format
mismatches early.  Each benchmark has its own required fields
and value constraints.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ── Schemas ────────────────────────────────────────────────────────────────────

SWEBENCH_SCHEMA = {
    "description": "SWE-bench predictions.jsonl",
    "required_fields": {"instance_id", "model_patch"},
    "field_types": {"instance_id": str, "model_patch": str},
    "extra_allowed": True,  # model_name_or_path, stats, etc. are fine
}

BIGCODEBENCH_SCHEMA = {
    "description": "BigCodeBench results.jsonl",
    "required_fields": {"task_id", "solution"},
    "field_types": {"task_id": str, "solution": str},
    "extra_allowed": True,
}

LIVECODEBENCH_SCHEMA = {
    "description": "LiveCodeBench predictions.jsonl",
    "required_fields": {"question_id", "solution"},
    "field_types": {"question_id": str, "solution": str},
    "extra_allowed": True,
}


def validate_file(
    path: str | Path,
    schema: dict | None = None,
    *,
    verbose: bool = True,
) -> int:
    """
    Validate a JSONL prediction file against a schema.

    Returns 0 on success, 1 on failure.
    """
    p = Path(path)
    if not p.exists():
        print(f"  ✗ File not found: {p}")
        return 1
    if p.suffix not in (".jsonl", ".json"):
        print(f"  ✗ Expected .jsonl or .json file, got: {p.suffix}")
        return 1

    if schema is None:
        # Auto-detect schema from filename
        stem = p.stem.lower()
        if "swe" in stem:
            schema = SWEBENCH_SCHEMA
        elif "bigcode" in stem:
            schema = BIGCODEBENCH_SCHEMA
        elif "livecode" in stem:
            schema = LIVECODEBENCH_SCHEMA
        else:
            print(
                f"  ✗ Cannot auto-detect schema for {p.name}; pass schema= explicitly"
            )
            return 1

    required = schema["required_fields"]
    types = schema["field_types"]
    errors = 0
    count = 0

    with open(p) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            count += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  ✗ Line {lineno}: invalid JSON — {e}")
                errors += 1
                continue

            if not isinstance(record, dict):
                print(f"  ✗ Line {lineno}: expected dict, got {type(record).__name__}")
                errors += 1
                continue

            for field in required:
                if field not in record:
                    print(f"  ✗ Line {lineno}: missing required field '{field}'")
                    errors += 1
                elif not isinstance(record[field], types.get(field, object)):
                    print(
                        f"  ✗ Line {lineno}: field '{field}' should be "
                        f"{types[field].__name__}, got {type(record[field]).__name__}"
                    )
                    errors += 1

    if verbose:
        status = "✓" if errors == 0 else "✗"
        print(f"  {status} {p.name}: {count} records, {errors} schema errors")

    return 0 if errors == 0 else 1


def main() -> int:
    """CLI entry: python -m benchmark.validate <file> [<file> ...]"""
    files = sys.argv[1:]
    if not files:
        print("Usage: python -m benchmark.validate <prediction_file> ...")
        return 1

    exit_code = 0
    for f in files:
        rc = validate_file(f)
        if rc != 0:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
