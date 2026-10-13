"""Publish raw papers with optional AI data in the website's existing file format."""

import argparse
import json
import os
from pathlib import Path
import sys

from data_state import has_valid_ai, validate_file


def prepare_publication(source: Path, language: str, enhanced: Path | None = None) -> Path:
    validate_file(source, require_ai=False)
    papers = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        if line.strip():
            paper = json.loads(line)
            paper.pop("AI", None)
            papers.setdefault(paper["id"], paper)

    # Read only output from a successful AI step, never a stale file from history.
    if enhanced is not None:
        try:
            validate_file(enhanced)
            enhanced_papers = [json.loads(line) for line in enhanced.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, ValueError, TypeError) as error:
            print(f"AI output unavailable; publishing original abstracts: {error}", file=sys.stderr)
        else:
            for paper in enhanced_papers:
                if paper["id"] in papers and has_valid_ai(paper):
                    papers[paper["id"]]["AI"] = paper["AI"]
                    for field in ("code_url", "code_stars", "code_last_update"):
                        if field in paper:
                            papers[paper["id"]][field] = paper[field]

    # Keep the historical filename for compatibility with the website and CLI.
    # Rows without AI have no AI field; readers display their original abstract.
    target = source.with_name(f"{source.stem}_AI_enhanced_{language}.jsonl")
    temporary = target.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for paper in papers.values():
            stream.write(json.dumps(paper, ensure_ascii=False) + "\n")
    os.replace(temporary, target)
    ai_count = sum("AI" in paper for paper in papers.values())
    print(f"Prepared {len(papers)} papers; {ai_count} have optional AI summaries")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--language", default="Chinese")
    parser.add_argument("--enhanced", type=Path)
    args = parser.parse_args()
    prepare_publication(args.data, args.language, args.enhanced)
