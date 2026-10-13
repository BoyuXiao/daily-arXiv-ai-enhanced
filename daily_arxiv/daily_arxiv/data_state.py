"""Validate paper metadata independently of optional AI summaries."""

import json
import sys
from pathlib import Path


AI_FIELDS = ("tldr", "motivation", "method", "result", "conclusion")
FAILURE_VALUES = {
    "Summary generation failed",
    "Motivation analysis unavailable",
    "Method extraction failed",
    "Result analysis unavailable",
    "Conclusion extraction failed",
    "Processing failed",
}


def has_valid_ai(paper: dict) -> bool:
    ai = paper.get("AI")
    return bool(paper.get("id")) and isinstance(ai, dict) and all(
        isinstance(ai.get(field), str)
        and bool(ai[field].strip())
        and ai[field].strip() not in FAILURE_VALUES
        for field in AI_FIELDS
    )


def has_valid_raw(paper: dict) -> bool:
    return all(
        isinstance(paper.get(field), str) and bool(paper[field].strip())
        for field in ("id", "title", "summary")
    ) and all(
        isinstance(paper.get(field), list)
        and bool(paper[field])
        and all(isinstance(value, str) and value.strip() for value in paper[field])
        for field in ("authors", "categories")
    )


def validate_file(path: Path, require_ai: bool = True) -> int:
    """Return unique paper count; raw history may contain cross-category duplicates."""
    seen = set()
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            paper = json.loads(line)
            validator = has_valid_ai if require_ai else has_valid_raw
            if not isinstance(paper, dict) or not validator(paper):
                detail = "missing or failed AI summary" if require_ai else "incomplete paper metadata"
                raise ValueError(f"{path.name}:{number}: {detail}")
            if require_ai and paper["id"] in seen:
                raise ValueError(f"{path.name}:{number}: duplicate paper ID")
            seen.add(paper["id"])
    if not seen:
        raise ValueError(f"{path.name}: no papers")
    return len(seen)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--raw", action="store_true", help="Validate paper metadata without requiring AI")
    args = parser.parse_args()
    try:
        count = validate_file(args.path, require_ai=not args.raw)
    except (OSError, ValueError, TypeError) as error:
        print(f"No reusable output: {error}", file=sys.stderr)
        sys.exit(1)
    print(f"Validated {count} papers in {args.path.name}")
