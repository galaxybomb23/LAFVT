"""
Lines-of-Code (LOC) analysis algorithm.

Scores every C/C++ function by its raw line count.  The ``score`` column is
the function's line count normalised to [0, 1] across the whole codebase, so
the longest function always gets score 1.0 and a single-line function gets 0.0.

Output columns
--------------
===================  ============================================================
Column               Description
===================  ============================================================
filepath             Absolute POSIX path to the source file
function_name        Function / method name
start_line           First line of the function
end_line             Last line of the function
lines                Raw line count of the function
score                lines normalised to [0, 1] across all functions
===================  ============================================================
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import lizard
import pandas as pd

from analyzer.base import AnalysisAlgorithm, register_algorithm

logger = logging.getLogger(__name__)

_C_EXTENSIONS = {".c", ".h", ".cpp", ".hpp", ".cc"}


@register_algorithm
class LOCAlgorithm(AnalysisAlgorithm):
    """
    Lines-of-Code scoring.

    The ``score`` for each function is its line count normalised to the
    [0, 1] range across the entire discovered function set.  Longer functions
    score higher.

    Register name: ``"loc"``
    """

    name = "loc"

    def analyze(self, root_directory: Path) -> pd.DataFrame:
        root_directory = Path(root_directory)
        logger.info("[loc] Starting analysis of: %s", root_directory)

        try:
            all_files = list(
                lizard.get_all_source_files([str(root_directory)], exclude_patterns=[], lans=None)
            )
        except Exception:
            logger.exception("[loc] File discovery failed")
            raise

        source_files = [f for f in all_files if Path(f).suffix in _C_EXTENSIONS]
        if not source_files:
            logger.warning("[loc] No C/C++ source files found in %s", root_directory)
            return pd.DataFrame(columns=list(self.REQUIRED_COLUMNS))

        logger.info("[loc] Found %d source files", len(source_files))

        raw: List[Dict[str, Any]] = []
        for file_path in source_files:
            try:
                analysis = lizard.analyze_file(file_path)
                for func in analysis.function_list:
                    raw.append(
                        {
                            "filepath": Path(file_path).resolve().as_posix(),
                            "function_name": func.name,
                            "start_line": func.start_line,
                            "end_line": func.end_line,
                            "lines": float(func.length),
                        }
                    )
            except Exception:
                logger.exception("[loc] Failed to analyse file: %s", file_path)

        if not raw:
            logger.warning("[loc] No functions found")
            return pd.DataFrame(columns=list(self.REQUIRED_COLUMNS))

        df = pd.DataFrame(raw)

        # Normalise line count to [0, 1]
        min_lines = df["lines"].min()
        max_lines = df["lines"].max()
        denom = max_lines - min_lines if max_lines != min_lines else 1.0
        df["score"] = (df["lines"] - min_lines) / denom

        df.sort_values("score", ascending=False, inplace=True)

        logger.info("[loc] Analysis complete: %d functions", len(df))
        return self._validate_output(df)
