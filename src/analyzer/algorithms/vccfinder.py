"""
VCCFinder-style git-history vulnerability analysis algorithm.
=============================================================

Implements the **VCCFinder** methodology (Perl et al.) adapted for the LAFVT
analyzer plugin framework.  The algorithm mines local git history with
**PyDriller**, extracts C/C++ structural-keyword features and commit metadata
from diffs, and classifies each function as vulnerability-contributing or
benign using a **LinearSVC** (scikit-learn).

Everything runs **offline** on consumer-grade hardware — no network calls, no
GPU, no external APIs.

Scoring pipeline
----------------
1. **Function discovery** — Lizard extracts every C/C++ function with its file
   path and line range (same helper used by the ``lizard`` algorithm).
2. **Git-history mining** — PyDriller walks local commits that touch C/C++
   files and maps diff hunks back to the discovered functions.
3. **Feature extraction** — For each function, counts of 62 C/C++ structural
   keywords in added/removed lines, diff size metrics, and author metadata
   are assembled into a fixed-width feature vector.
4. **SVM classification** — A ``LinearSVC`` is trained on heuristic weak
   labels (high dangerous-keyword density → positive) and its decision-function
   distance is normalised to [0, 1] as the ``score``.

If a pre-trained model exists at ``<root_directory>/vccfinder_model.joblib``
it is loaded instead of training from scratch.

Output columns
--------------
==================  ============================================================
Column              Description
==================  ============================================================
filepath            Absolute POSIX path to the source file
function_name       Function / method name
start_line          First line of the function
end_line            Last line of the function
score               SVM decision-function distance, normalised to [0, 1]
keyword_count       Total structural-keyword hits across all diffs for this func
lines_added         Total lines added to this function across commits
lines_removed       Total lines removed from this function across commits
churn               lines_added + lines_removed
commit_count        Number of commits that touched this function
author_count        Number of distinct authors who modified this function
==================  ============================================================

References
----------
* Perl, H. et al. "VCCFinder: Finding Potential Vulnerabilities in
  Open-Source Projects to Assist Code Audits." (CCS 2015)
* PyDriller — https://github.com/ishepard/pydriller
"""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import lizard
import numpy as np
import pandas as pd
from pydriller import Repository
from sklearn.preprocessing import MinMaxScaler
from sklearn.svm import LinearSVC

from analyzer.base import AnalysisAlgorithm, register_algorithm

logger = logging.getLogger(__name__)

# ── Default bundled model path ────────────────────────────────────────────
_DEFAULT_MODEL_PATH = Path(__file__).parent / "models" / "vccfinder_model.joblib"

# ── C/C++ extensions accepted for analysis ────────────────────────────────
_C_EXTENSIONS: Set[str] = {".c", ".h", ".cpp", ".hpp", ".cc"}

# ── 62 VCCFinder structural keywords ─────────────────────────────────────
# Sourced from Perl et al. (2015) — covers control flow, memory ops,
# type qualifiers, and preprocessor directives typical of vulnerability-
# contributing commits in C/C++ codebases.
_KEYWORDS: Tuple[str, ...] = (
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

# Subset of keywords that are strong indicators of dangerous operations.
_DANGEROUS_KEYWORDS: Set[str] = {
    "malloc", "calloc", "realloc", "free",
    "memcpy", "memmove", "memset",
    "strcpy", "strncpy", "strcat", "strncat",
    "sprintf", "printf", "fprintf", "scanf",
    "goto", "sizeof", "NULL",
}

# Pre-compiled regex: matches any keyword as a whole word.
_KW_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(kw) for kw in _KEYWORDS) + r")\b"
)

# Maximum number of commits to traverse (safety valve for huge repos).
_MAX_COMMITS = 5000

# Number of parallel workers for Lizard file scanning.
_LIZARD_WORKERS = min(8, (os.cpu_count() or 4))


# ── Helpers ───────────────────────────────────────────────────────────────

def _count_keywords(line: str) -> Dict[str, int]:
    """Return per-keyword counts for a single source line."""
    counts: Dict[str, int] = defaultdict(int)
    for m in _KW_PATTERN.finditer(line):
        counts[m.group()] += 1
    return dict(counts)


def _is_c_file(path: str) -> bool:
    return Path(path).suffix in _C_EXTENSIONS


# ── FunctionKey: (resolved_filepath, function_name) ──────────────────────

FunctionKey = Tuple[str, str]


# ── Algorithm ─────────────────────────────────────────────────────────────

@register_algorithm
class VCCFinderAlgorithm(AnalysisAlgorithm):
    """
    VCCFinder-style git-history vulnerability scoring.

    Combines PyDriller commit mining, C/C++ keyword feature extraction,
    and a LinearSVC classifier to score every function by its likelihood
    of being vulnerability-contributing.

    Register name: ``"vccfinder"``
    """

    name = "vccfinder"

    EXTRA_COLUMNS: tuple = (
        "keyword_count", "lines_added", "lines_removed",
        "churn", "commit_count", "author_count",
    )

    # ── public entry point ────────────────────────────────────────────

    def analyze(self, root_directory: Path) -> pd.DataFrame:
        root_directory = Path(root_directory).resolve()
        logger.info("[vccfinder] Starting analysis of: %s", root_directory)

        # Step 1: discover functions via Lizard
        func_index = self._discover_functions(root_directory)
        if not func_index:
            logger.warning("[vccfinder] No C/C++ functions found")
            return pd.DataFrame(columns=list(self.REQUIRED_COLUMNS))

        # Step 2: mine git history and map diffs → functions
        features = self._mine_history(root_directory, func_index)

        # Step 3: build feature matrix and classify
        df = self._build_dataframe(func_index, features)
        if df.empty:
            return pd.DataFrame(columns=list(self.REQUIRED_COLUMNS))

        df = self._classify(df, root_directory)
        df = self._validate_output(df)

        logger.info("[vccfinder] Analysis complete: %d functions scored", len(df))
        return df

    # ── Step 1: function discovery ────────────────────────────────────

    def _discover_functions(
        self, root_directory: Path
    ) -> Dict[FunctionKey, Dict[str, Any]]:
        """
        Use Lizard to extract every C/C++ function and record its file path
        and line range.  Returns a dict keyed by (filepath, function_name).
        """
        try:
            all_files = list(
                lizard.get_all_source_files(
                    [str(root_directory)], exclude_patterns=[], lans=None
                )
            )
        except Exception:
            logger.exception("[vccfinder] File discovery failed")
            raise

        source_files = [f for f in all_files if Path(f).suffix in _C_EXTENSIONS]
        if not source_files:
            return {}

        logger.info(
            "[vccfinder] Scanning %d source files with Lizard (%d workers)",
            len(source_files), _LIZARD_WORKERS,
        )

        func_index: Dict[FunctionKey, Dict[str, Any]] = {}

        def _scan_file(file_path: str) -> List[Dict[str, Any]]:
            results: List[Dict[str, Any]] = []
            analysis = lizard.analyze_file(file_path)
            resolved = Path(file_path).resolve().as_posix()
            for func in analysis.function_list:
                results.append({
                    "filepath": resolved,
                    "function_name": func.name,
                    "start_line": func.start_line,
                    "end_line": func.end_line,
                })
            return results

        with ThreadPoolExecutor(max_workers=_LIZARD_WORKERS) as pool:
            futures = {pool.submit(_scan_file, fp): fp for fp in source_files}
            for future in as_completed(futures):
                fp = futures[future]
                try:
                    for entry in future.result():
                        key: FunctionKey = (entry["filepath"], entry["function_name"])
                        func_index[key] = entry
                except Exception:
                    logger.exception("[vccfinder] Lizard failed on: %s", fp)

        logger.info("[vccfinder] Discovered %d functions", len(func_index))
        return func_index

    # ── Step 2: git-history mining ────────────────────────────────────

    def _mine_history(
        self,
        root_directory: Path,
        func_index: Dict[FunctionKey, Dict[str, Any]],
    ) -> Dict[FunctionKey, Dict[str, Any]]:
        """
        Walk local git history with PyDriller.  For each commit that touches
        C/C++ files, attribute diff hunks to discovered functions and
        accumulate per-function feature counters.
        """
        # Build a lookup: resolved_filepath → list of (start, end, key)
        file_funcs: Dict[str, List[Tuple[int, int, FunctionKey]]] = defaultdict(list)
        for key, info in func_index.items():
            file_funcs[info["filepath"]].append(
                (info["start_line"], info["end_line"], key)
            )

        # Per-function accumulators
        features: Dict[FunctionKey, Dict[str, Any]] = {
            key: {
                "kw_counts": defaultdict(int),
                "lines_added": 0,
                "lines_removed": 0,
                "commit_hashes": set(),
                "authors": set(),
            }
            for key in func_index
        }

        # Locate the git repo root (walk up from root_directory)
        repo_path = self._find_git_root(root_directory)
        if repo_path is None:
            logger.warning(
                "[vccfinder] No .git directory found above %s — "
                "git-history features will be zero", root_directory,
            )
            return features

        # Compute the relative path prefix for filtering commits to only
        # those that touch files under root_directory.
        try:
            rel_prefix = str(root_directory.relative_to(repo_path))
        except ValueError:
            rel_prefix = ""

        logger.info(
            "[vccfinder] Mining git history from %s (max %d commits, "
            "path filter=%r, skipping merges)",
            repo_path, _MAX_COMMITS, rel_prefix or "<none>",
        )

        repo_kwargs: Dict[str, Any] = {
            "only_modifications_with_file_types": list(_C_EXTENSIONS),
            "only_no_merge": True,
        }
        # Restrict to commits touching our target sub-directory when possible
        if rel_prefix:
            repo_kwargs["filepath"] = str(root_directory)

        commit_count = 0
        for commit in Repository(str(repo_path), **repo_kwargs).traverse_commits():
            if commit_count >= _MAX_COMMITS:
                logger.info("[vccfinder] Reached commit cap (%d)", _MAX_COMMITS)
                break
            commit_count += 1

            if commit_count % 200 == 0:
                logger.info("[vccfinder] … processed %d commits", commit_count)

            author_name = commit.author.name if commit.author else "unknown"

            for mod in commit.modified_files:
                # Resolve the file path against repo root
                mod_path = mod.new_path or mod.old_path
                if mod_path is None or not _is_c_file(mod_path):
                    continue

                resolved_mod = (repo_path / mod_path).resolve().as_posix()
                if resolved_mod not in file_funcs:
                    continue

                # Parse diff to extract touched line numbers and content
                added_lines, removed_lines = self._parse_diff_lines(mod.diff)

                # Attribute to functions whose range overlaps the diff
                for start, end, fkey in file_funcs[resolved_mod]:
                    touched = False

                    for lineno, content in added_lines:
                        if start <= lineno <= end:
                            touched = True
                            features[fkey]["lines_added"] += 1
                            for kw, cnt in _count_keywords(content).items():
                                features[fkey]["kw_counts"][kw] += cnt

                    for lineno, content in removed_lines:
                        if start <= lineno <= end:
                            touched = True
                            features[fkey]["lines_removed"] += 1
                            for kw, cnt in _count_keywords(content).items():
                                features[fkey]["kw_counts"][kw] += cnt

                    if touched:
                        features[fkey]["commit_hashes"].add(commit.hash)
                        features[fkey]["authors"].add(author_name)

        logger.info("[vccfinder] Processed %d commits", commit_count)
        return features

    # ── Step 3: DataFrame construction ────────────────────────────────

    def _build_dataframe(
        self,
        func_index: Dict[FunctionKey, Dict[str, Any]],
        features: Dict[FunctionKey, Dict[str, Any]],
    ) -> pd.DataFrame:
        """Merge function metadata and mined features into a DataFrame."""
        rows: List[Dict[str, Any]] = []
        for key, info in func_index.items():
            feat = features[key]
            total_kw = sum(feat["kw_counts"].values())
            rows.append({
                "filepath": info["filepath"],
                "function_name": info["function_name"],
                "start_line": info["start_line"],
                "end_line": info["end_line"],
                "keyword_count": total_kw,
                "lines_added": feat["lines_added"],
                "lines_removed": feat["lines_removed"],
                "churn": feat["lines_added"] + feat["lines_removed"],
                "commit_count": len(feat["commit_hashes"]),
                "author_count": len(feat["authors"]),
                # Per-keyword features for the SVM
                **{f"kw_{kw}": feat["kw_counts"].get(kw, 0) for kw in _KEYWORDS},
                # Dangerous-keyword subtotal (used for heuristic labelling)
                "_dangerous_kw_count": sum(
                    feat["kw_counts"].get(kw, 0) for kw in _DANGEROUS_KEYWORDS
                ),
            })

        return pd.DataFrame(rows)

    # ── Step 4: SVM classification ────────────────────────────────────

    def _classify(self, df: pd.DataFrame, root_directory: Path) -> pd.DataFrame:
        """
        Train a LinearSVC on heuristic weak labels derived from dangerous-
        keyword density, or load a pre-trained model if available.
        Normalise the SVM decision-function distance to [0, 1] as ``score``.
        """
        feature_cols = (
            [f"kw_{kw}" for kw in _KEYWORDS]
            + ["lines_added", "lines_removed", "churn", "commit_count", "author_count"]
        )

        X = df[feature_cols].values.astype(np.float64)

        # Try loading a pre-trained model:
        #   1. Project-root override (root_directory/vccfinder_model.joblib)
        #   2. Bundled default (analyzer/algorithms/models/vccfinder_model.joblib)
        #   3. Heuristic fallback
        override_path = root_directory / "vccfinder_model.joblib"
        if override_path.exists():
            model_path = override_path
        elif _DEFAULT_MODEL_PATH.exists():
            model_path = _DEFAULT_MODEL_PATH
        else:
            model_path = None

        if model_path is not None:
            import joblib
            logger.info("[vccfinder] Loading pre-trained model from %s", model_path)
            bundle = joblib.load(model_path)
            # Support both bundle format (dict with svm+scaler) and bare SVM
            if isinstance(bundle, dict):
                svm = bundle["svm"]
                feature_scaler = bundle.get("scaler")
                X_inf = feature_scaler.transform(X) if feature_scaler is not None else X
            else:
                svm = bundle
                X_inf = X
            decision = svm.decision_function(X_inf)
        else:
            # Heuristic labelling: functions with above-median dangerous-keyword
            # density (relative to total churn) are labelled as positive.
            decision = self._train_heuristic_svm(df, X, feature_cols)

        # Normalise decision values to [0, 1]
        if decision.max() != decision.min():
            scaler = MinMaxScaler()
            df["score"] = scaler.fit_transform(decision.reshape(-1, 1)).flatten()
        else:
            df["score"] = 0.5

        # Drop internal columns
        df.drop(
            columns=[c for c in df.columns if c.startswith("kw_") or c == "_dangerous_kw_count"],
            inplace=True,
        )

        df.sort_values("score", ascending=False, inplace=True)
        return df

    def _train_heuristic_svm(
        self,
        df: pd.DataFrame,
        X: np.ndarray,
        feature_cols: List[str],
    ) -> np.ndarray:
        """
        Train a LinearSVC using weak labels derived from dangerous-keyword density.

        Functions whose dangerous-keyword count per unit of churn exceeds the
        median are labelled positive (vulnerability-contributing).  If all
        labels are the same (e.g. no git history), fall back to a pure
        keyword-density score without SVM.
        """
        churn = np.asarray(df["churn"].values, dtype=np.float64)
        dangerous = np.asarray(df["_dangerous_kw_count"].values, dtype=np.float64)

        # Density = dangerous keywords per line of churn (avoid div-by-zero)
        density = np.divide(
            dangerous, churn, out=np.zeros_like(dangerous), where=(churn > 0)
        )
        has_churn = churn > 0
        median_density = float(np.median(density[has_churn])) if has_churn.any() else 0.0

        labels = np.where(density > median_density, 1, 0)

        # If all the same label, SVM cannot train — fall back to density score
        if len(np.unique(labels)) < 2:
            logger.info(
                "[vccfinder] Cannot train SVM (uniform labels) — "
                "falling back to keyword-density scoring"
            )
            return density

        logger.info(
            "[vccfinder] Training LinearSVC: %d positive / %d negative",
            int(labels.sum()), int((labels == 0).sum()),
        )

        svm = LinearSVC(max_iter=5000, dual="auto")  # type: ignore[arg-type]
        svm.fit(X, labels)

        return svm.decision_function(X)

    # ── Utilities ─────────────────────────────────────────────────────

    @staticmethod
    def _find_git_root(start: Path) -> Optional[Path]:
        """Walk up from *start* looking for a ``.git`` directory."""
        current = start
        while current != current.parent:
            if (current / ".git").is_dir():
                return current
            current = current.parent
        if (current / ".git").is_dir():
            return current
        return None

    @staticmethod
    def _parse_diff_lines(
        diff: Optional[str],
    ) -> Tuple[List[Tuple[int, str]], List[Tuple[int, str]]]:
        """
        Parse a unified diff string into added and removed lines with their
        target line numbers.

        Returns
        -------
        (added, removed)
            Each is a list of ``(line_number, line_content)`` tuples.
        """
        added: List[Tuple[int, str]] = []
        removed: List[Tuple[int, str]] = []

        if not diff:
            return added, removed

        new_lineno = 0
        old_lineno = 0

        for raw_line in diff.splitlines():
            if raw_line.startswith("@@"):
                # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
                m = re.search(r"\+(\d+)", raw_line)
                new_lineno = int(m.group(1)) if m else 0
                m2 = re.search(r"-(\d+)", raw_line)
                old_lineno = int(m2.group(1)) if m2 else 0
            elif raw_line.startswith("+") and not raw_line.startswith("+++"):
                added.append((new_lineno, raw_line[1:]))
                new_lineno += 1
            elif raw_line.startswith("-") and not raw_line.startswith("---"):
                removed.append((old_lineno, raw_line[1:]))
                old_lineno += 1
            else:
                new_lineno += 1
                old_lineno += 1

        return added, removed
