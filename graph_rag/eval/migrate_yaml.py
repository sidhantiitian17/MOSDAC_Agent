"""Convert the legacy Phase-0 question YAML into golden JSONL (evaluation_plan.md §3).

The old set (tests/eval/multihop_questions.yaml) is a *seed*, not a gate: it has no
negative/formula/numeric strata and no quantity/formula annotations. This converter
preserves what it has (question, reference, entities, setup) and maps the legacy
``type`` to a golden ``stratum`` so the seed flows into the new harness unchanged;
humans then add the missing strata and annotations per §3.2.

Run:  python -m graph_rag.eval.migrate_yaml [src.yaml] [out.jsonl]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# legacy type → golden stratum (all legacy items are answerable)
_TYPE_TO_STRATUM = {
    "single": "single",
    "multihop": "multihop",
    "comparison": "comparison",
    "followup": "followup",
}


def convert(src: str | Path) -> list[dict]:
    import yaml

    raw = yaml.safe_load(Path(src).read_text(encoding="utf-8")) or []
    out: list[dict] = []
    for row in raw:
        stratum = _TYPE_TO_STRATUM.get(str(row.get("type", "single")), "single")
        rec = {
            "id": str(row.get("id", "")),
            "stratum": stratum,
            "user_input": str(row.get("question", "")),
            "reference": str(row.get("reference", "")),
            "expected_entities": list(row.get("expected_entities", []) or []),
            "answerable": True,
        }
        if row.get("setup"):
            rec["setup"] = [str(s) for s in row["setup"]]
        out.append(rec)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    src = argv[0] if argv else "tests/eval/multihop_questions.yaml"
    out = argv[1] if len(argv) > 1 else "tests/eval/golden/v1/from_seed.jsonl"
    rows = convert(src)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with Path(out).open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Converted {len(rows)} items → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
