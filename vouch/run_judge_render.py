#!/usr/bin/env python
"""Render judge documents for probes: the v5 C2 re-judging instrument.

One plain-text document per probe: MARKET STATE (the probe's v1 rendering
plus the pinned wire-v2 context sentence from --context-file), COMPASS
READS, the 24h NEWS DIGEST from --news-file, and the entry question at the
END (docs/v5-phase1-spec.md, C2). A context cache missing a probe's minute
refuses the run loudly; an empty news window renders the pinned literal
line, so September probes re-judge with context but without news.

    .venv/bin/python run_judge_render.py --probes out/probe-set-v1.jsonl \
        --index 0 --context-file out/context-sol.jsonl \
        --news-file out/news/events.jsonl --out out/judge-doc-0.txt

    .venv/bin/python run_judge_render.py --probes out/probe-set-v1.jsonl \
        --all --context-file out/context-sol.jsonl \
        --news-file out/news/events.jsonl --out out/judge-docs-v2.jsonl

--all writes a JSONL of {"ts", "doc"} rows; without it, --out receives the
single probe's document as plain text.

Layering: root CLI over analysis (judge_doc).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from vouch.analysis.judge_doc import load_news, render
from vouch.engine.context_features import ContextSeries


def main() -> None:
    ap = argparse.ArgumentParser(description="render judge documents for probes")
    ap.add_argument("--probes", default="out/probe-set-v1.jsonl")
    ap.add_argument("--index", type=int, default=None, help="render only this probe row")
    ap.add_argument("--all", action="store_true", help="render every probe")
    ap.add_argument("--context-file", default="out/context-sol.jsonl")
    ap.add_argument("--news-file", default="out/news/events.jsonl")
    ap.add_argument(
        "--out",
        default="out/judge-doc.txt",
        help='doc text per probe, or JSONL of {"ts","doc"} rows for --all',
    )
    a = ap.parse_args()
    if not a.all and a.index is None:
        ap.error("--index N or --all is required")

    probes = [
        json.loads(line) for line in Path(a.probes).read_text().splitlines() if line.strip()
    ]
    series = ContextSeries.load_jsonl(a.context_file)
    news = load_news(a.news_file)

    out = Path(a.out)
    if a.all:
        with out.open("w") as f:
            for p in probes:
                f.write(json.dumps({"ts": p["ts"], "doc": render(p, series, news)}) + "\n")
        print(json.dumps({"out": str(out), "docs": len(probes)}, indent=2))
        return
    if not 0 <= a.index < len(probes):
        raise SystemExit(f"--index {a.index} outside 0..{len(probes) - 1} ({a.probes})")
    doc = render(probes[a.index], series, news)
    out.write_text(doc)
    print(
        json.dumps({"out": str(out), "ts": probes[a.index].get("ts"), "bytes": len(doc)}, indent=2)
    )


if __name__ == "__main__":
    main()
