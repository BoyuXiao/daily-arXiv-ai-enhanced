"""Validate published AI output before reusing it or declaring a run successful."""

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


def validate_file(path: Path) -> int:
    """Return the paper count, or reject empty, malformed and fallback output."""
    seen = set()
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            paper = json.loads(line)
            if not isinstance(paper, dict) or not has_valid_ai(paper):
                raise ValueError(f"{path.name}:{number}: missing or failed AI summary")
            if paper["id"] in seen:
                raise ValueError(f"{path.name}:{number}: duplicate paper ID")
            seen.add(paper["id"])
    if not seen:
        raise ValueError(f"{path.name}: no enhanced papers")
    return len(seen)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        count = validate_file(args.path)
    except (OSError, ValueError, TypeError) as error:
        print(f"No reusable AI output: {error}", file=sys.stderr)
        sys.exit(1)
    print(f"Validated {count} enhanced papers in {args.path.name}")
