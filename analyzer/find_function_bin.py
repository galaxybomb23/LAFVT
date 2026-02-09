#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path
from typing import List, Tuple


def parse_args():
    p = argparse.ArgumentParser(
        description="Locate a function in analyzer CSV and report its complexity bin and rank by vulnerability."
    )
    p.add_argument("csv_path", help="Path to analyzer CSV output")
    p.add_argument("func_name", help="Function name to locate")
    # Always bin per distinct complexity_score
    return p.parse_args()


def load_rows(csv_path: Path) -> List[dict]:
    rows = []
    with csv_path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("file", "").startswith("len(all_metrics)"):
                continue
            rows.append(row)
    return rows


def to_int(row: dict, key: str) -> int:
    try:
        return int(row[key])
    except Exception:
        return 0


def compute_bins(scores: List[int]) -> List[Tuple[int, int]]:
    if not scores:
        return []
    lo = min(scores)
    hi = max(scores)
    if lo == hi:
        return [(lo, hi)]
    uniq = sorted(set(scores))
    return [(v, v) for v in uniq]


def bin_index(value: int, edges: List[Tuple[int, int]]) -> int:
    for i, (lo, hi) in enumerate(edges):
        if lo <= value <= hi:
            return i
    return len(edges) - 1


def main():
    args = parse_args()
    csv_path = Path(args.csv_path).resolve()
    rows = load_rows(csv_path)

    for r in rows:
        r["complexity_score"] = to_int(r, "complexity_score")
        r["vulnerability_score"] = to_int(r, "vulnerability_score")

    scores = [r["complexity_score"] for r in rows]
    edges = compute_bins(scores)

    # assign bins
    bins: List[List[dict]] = [[] for _ in range(len(edges))]
    for r in rows:
        idx = bin_index(r["complexity_score"], edges)
        bins[idx].append(r)

    # find matching functions (could be multiple files)
    matches = [r for r in rows if r.get("func", "") == args.func_name]
    if not matches:
        print(f"NOT FOUND: {args.func_name}")
        return

    for m in matches:
        idx = bin_index(m["complexity_score"], edges)
        lo, hi = edges[idx]
        b_sorted = sorted(
            bins[idx],
            key=lambda r: (-r["vulnerability_score"], -r["complexity_score"]),
        )
        rank = next(
            (i for i, r in enumerate(b_sorted, start=1) if r is m),
            None,
        )
        print(
            f"{m.get('file','')}:{m.get('line','')} "
            f"func={m.get('func','')} "
            f"complexity={m['complexity_score']} "
            f"vulnerability={m['vulnerability_score']} "
            f"bin={idx} range={lo}-{hi} rank_in_bin={rank}/{len(b_sorted)}"
        )
        bin_scores = ",".join(str(r["vulnerability_score"]) for r in b_sorted)
        print(f"bin_vulnerability_scores={bin_scores}")


if __name__ == "__main__":
    main()
