#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path
from typing import List, Tuple


def parse_args():
    p = argparse.ArgumentParser(
        description="Bin functions by complexity_score and rank each bin by vulnerability_score."
    )
    p.add_argument("csv_path", help="Path to analyzer CSV output")
    # Always bin per distinct complexity_score
    p.add_argument(
        "--out",
        help="Output CSV path (default: stdout)",
    )
    return p.parse_args()


def load_rows(csv_path: Path) -> List[dict]:
    rows = []
    with csv_path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Skip the trailing summary line from analyzer.py if present
            if row.get("file", "").startswith("len(all_metrics)"):
                continue
            rows.append(row)
    return rows


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

    # Normalize scores
    for r in rows:
        r["complexity_score"] = int(r["complexity_score"])
        r["vulnerability_score"] = int(r["vulnerability_score"])

    scores = [r["complexity_score"] for r in rows]
    bin_per_score = args.bin_per_score and not args.equal_width
    edges = compute_bins(scores)

    # Assign bins
    bins: List[List[dict]] = [[] for _ in range(len(edges))]
    for r in rows:
        idx = bin_index(r["complexity_score"], edges)
        bins[idx].append(r)

    out_f = open(args.out, "w", newline="") if args.out else None
    try:
        writer = csv.writer(out_f or print)
    except TypeError:
        # csv.writer expects a file-like object; use stdout via sys.stdout
        import sys

        writer = csv.writer(out_f or sys.stdout)

    if out_f:
        writer = csv.writer(out_f)

    writer.writerow(
        [
            "bin_id",
            "bin_range",
            "rank_in_bin",
            "file",
            "func",
            "line",
            "complexity_score",
            "vulnerability_score",
        ]
    )

    for i, b in enumerate(bins):
        lo, hi = edges[i]
        b_sorted = sorted(
            b, key=lambda r: (-r["vulnerability_score"], -r["complexity_score"])
        )
        for rank, r in enumerate(b_sorted, start=1):
            writer.writerow(
                [
                    i,
                    f"{lo}-{hi}",
                    rank,
                    r.get("file", ""),
                    r.get("func", ""),
                    r.get("line", ""),
                    r["complexity_score"],
                    r["vulnerability_score"],
                ]
            )

    if out_f:
        out_f.close()


if __name__ == "__main__":
    main()
