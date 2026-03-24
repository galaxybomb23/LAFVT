#!/usr/bin/env python3
"""
Train a VCCFinder-style LinearSVC from the CVEfixes database.
==============================================================

Reads the CVEfixes SQLite database (Bhandari et al., PROMISE '21),
extracts C/C++ keyword features from vulnerability-fixing diffs, and
trains a ``LinearSVC`` that can be loaded by the ``vccfinder`` analyzer
algorithm.

Usage
-----
::

    python src/train_vccfinder.py <path/to/CVEfixes.db> [--output vccfinder_model.joblib]

The script is **offline** — it only reads the local SQLite file and
writes a ``.joblib`` model.  No network calls are made.

Training strategy
-----------------
For each C/C++ ``file_change`` row linked to a CVE-fixing commit:

* **Positive sample (label=1)**: keyword features extracted from the
  **removed** diff lines (the vulnerable code that was deleted).
* **Negative sample (label=0)**: keyword features extracted from the
  **added** diff lines (the patched/fixed replacement code).

This gives natural paired samples: the same location in the same file,
before and after a security fix.  The SVM learns which keyword
distributions are characteristic of vulnerability-contributing code
versus fixed code.

The feature vector is identical to the one used by the ``vccfinder``
algorithm at inference time:

    [kw_if, kw_else, ..., kw__Atomic,   ← 62 keyword counts
     lines_added, lines_removed, churn,  ← diff metrics
     commit_count, author_count]         ← metadata (set to 1/1 for training)

References
----------
* CVEfixes: https://github.com/secureIT-project/CVEfixes
  (Zenodo DOI: 10.5281/zenodo.4476563)
* Perl et al. "VCCFinder" (CCS 2015)
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

import joblib
import numpy as np
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVC

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Must match the exact keyword list in vccfinder.py ─────────────────────
KEYWORDS: Tuple[str, ...] = (
    "if", "else", "for", "while", "do", "switch", "case", "break",
    "continue", "default", "return", "goto",
    "malloc", "calloc", "realloc", "free", "memcpy", "memmove",
    "memset", "strcpy", "strncpy", "strcat", "strncat", "strcmp",
    "strlen", "sprintf", "snprintf", "printf", "fprintf", "scanf",
    "sizeof", "typeof", "offsetof", "alignof",
    "NULL", "void", "static", "extern", "const", "volatile",
    "unsigned", "signed", "typedef", "struct", "union", "enum",
    "register", "inline", "restrict",
    "char", "int", "float", "double", "long", "short", "auto",
    "define", "include", "ifdef", "ifndef", "endif", "pragma",
    "assert", "exit", "abort",
    "_Bool", "_Complex", "_Atomic",
)

KW_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(kw) for kw in KEYWORDS) + r")\b"
)

FEATURE_COLS = [f"kw_{kw}" for kw in KEYWORDS] + [
    "lines_added", "lines_removed", "churn", "commit_count", "author_count",
]

# Languages in CVEfixes that correspond to C/C++
_C_LANGUAGES = {"C", "C++"}


# ── Feature extraction ────────────────────────────────────────────────────

def _count_keywords_in_text(text: str) -> Dict[str, int]:
    """Count occurrences of each keyword in a block of text."""
    counts: Dict[str, int] = defaultdict(int)
    for m in KW_PATTERN.finditer(text):
        counts[m.group()] += 1
    return dict(counts)


def _parse_diff_sides(diff: str) -> Tuple[List[str], List[str]]:
    """
    Split a unified diff into removed lines and added lines.

    Returns
    -------
    (removed_lines, added_lines)
    """
    removed: List[str] = []
    added: List[str] = []
    if not diff:
        return removed, added
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:])
    return removed, added


def _build_feature_vector(
    kw_counts: Dict[str, int],
    lines_added: int,
    lines_removed: int,
    commit_count: int = 1,
    author_count: int = 1,
) -> np.ndarray:
    """
    Assemble a feature vector matching the vccfinder algorithm's format.
    """
    features = [kw_counts.get(kw, 0) for kw in KEYWORDS]
    churn = lines_added + lines_removed
    features.extend([lines_added, lines_removed, churn, commit_count, author_count])
    return np.array(features, dtype=np.float64)


# ── Database queries ──────────────────────────────────────────────────────

_DIFF_QUERY = """
SELECT
    fc.diff,
    fc.num_lines_added,
    fc.num_lines_deleted
FROM file_change fc
JOIN commits c ON fc.hash = c.hash
JOIN fixes fx  ON c.hash = fx.hash
WHERE fc.programming_language IN ({langs})
  AND fc.diff IS NOT NULL
  AND fc.change_type = 'ModificationType.MODIFY'
""".format(langs=", ".join(f"'{l}'" for l in _C_LANGUAGES))


def _load_samples(db_path: Path) -> Tuple[np.ndarray, np.ndarray]:
    """
    Read CVEfixes and build (X, y) training arrays.

    For each C/C++ file_change with a diff:
      - label=1 (vulnerable):  keywords from removed lines
      - label=0 (fixed/clean): keywords from added lines

    Rows where either side is empty are skipped for that side.
    """
    logger.info("Opening database: %s", db_path)
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(_DIFF_QUERY)

    X_rows: List[np.ndarray] = []
    y_rows: List[int] = []
    skipped = 0
    total = 0

    for diff, num_added, num_deleted in cursor:
        total += 1
        removed_lines, added_lines = _parse_diff_sides(diff)

        # Positive sample: the vulnerable (removed) code
        if removed_lines:
            removed_text = "\n".join(removed_lines)
            kw = _count_keywords_in_text(removed_text)
            vec = _build_feature_vector(
                kw,
                lines_added=0,
                lines_removed=len(removed_lines),
            )
            X_rows.append(vec)
            y_rows.append(1)

        # Negative sample: the fixed (added) code
        if added_lines:
            added_text = "\n".join(added_lines)
            kw = _count_keywords_in_text(added_text)
            vec = _build_feature_vector(
                kw,
                lines_added=len(added_lines),
                lines_removed=0,
            )
            X_rows.append(vec)
            y_rows.append(0)

        if not removed_lines and not added_lines:
            skipped += 1

    conn.close()

    logger.info(
        "Loaded %d file_change rows → %d samples (%d positive, %d negative, %d skipped)",
        total, len(y_rows),
        sum(1 for y in y_rows if y == 1),
        sum(1 for y in y_rows if y == 0),
        skipped,
    )

    return np.array(X_rows), np.array(y_rows)


# ── Training ──────────────────────────────────────────────────────────────

def train(db_path: Path, output_path: Path) -> None:
    """Train the LinearSVC and save the model + scaler as .joblib."""
    X, y = _load_samples(db_path)

    if len(X) == 0:
        logger.error("No training samples found — check that the DB contains C/C++ diffs.")
        sys.exit(1)

    if len(np.unique(y)) < 2:
        logger.error("Only one class present — cannot train SVM.")
        sys.exit(1)

    # Train/test split for evaluation
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y,
    )

    # Scale features for SVM stability
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    logger.info(
        "Training LinearSVC on %d samples (%d train / %d test)",
        len(X), len(X_train), len(X_test),
    )

    svm = LinearSVC(max_iter=10000, dual="auto", class_weight="balanced")
    svm.fit(X_train_scaled, y_train)

    # Evaluate
    y_pred = svm.predict(X_test_scaled)
    report = classification_report(y_test, y_pred, target_names=["fixed", "vulnerable"])
    logger.info("Test set performance:\n%s", report)

    # Save model bundle: the SVM + scaler together so inference can
    # reconstruct the exact pipeline.
    bundle = {"svm": svm, "scaler": scaler, "feature_cols": FEATURE_COLS}
    joblib.dump(bundle, output_path)
    logger.info("Model saved to: %s", output_path)
    logger.info(
        "To use: place this file where the analyzer can find it, or pass "
        "--model-path to the analyzer."
    )


# ── CLI ───────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Train a VCCFinder LinearSVC from the CVEfixes SQLite database",
    )
    parser.add_argument(
        "db_path",
        type=Path,
        help="Path to CVEfixes.db (SQLite database)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("vccfinder_model.joblib"),
        help="Output path for the trained model (default: vccfinder_model.joblib)",
    )
    args = parser.parse_args()

    if not args.db_path.exists():
        logger.error("Database not found: %s", args.db_path)
        return 1

    train(args.db_path, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
