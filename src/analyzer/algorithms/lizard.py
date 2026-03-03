"""
Lizard analysis algorithm.

Uses **Lizard** to scan every C/C++ source file and compute cyclomatic
complexity, nesting depth, parameter count, and line count per function.
These metrics are normalised within complexity bins to produce a **score**
heuristic that represents vulnerability risk.

Output columns
--------------
===================  ============================================================
Column               Description
===================  ============================================================
filepath             Absolute POSIX path to the source file
function_name        Function / method name
start_line           First line of the function
end_line             Last line of the function
complexity           Cyclomatic complexity
nesting              Top nesting level
params               Parameter count
lines                Function length in lines
bin                  Complexity quantile bin
norm_nesting         Bin-normalised nesting
norm_params          Bin-normalised parameter count
norm_lines           Bin-normalised line count
score                Sum of the three normalised columns
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
class LizardAlgorithm(AnalysisAlgorithm):
    """
    Lizard vulnerability-risk scoring.

    Scores every C/C++ function by normalising complexity, nesting, parameter
    count, and line count within quantile bins.  The canonical ``score`` column
    is the sum of the three normalised values.

    Register name: ``"lizard"``
    """

    name = "lizard"

    def analyze(self, root_directory: Path) -> pd.DataFrame:
        root_directory = Path(root_directory)
        logger.info("[lizard] Starting analysis of: %s", root_directory)

        df = self._run_lizard(root_directory)
        if df is None or df.empty:
            logger.warning("[lizard] No functions found")
            return pd.DataFrame(columns=list(self.REQUIRED_COLUMNS))

        df = self._validate_output(df)
        logger.info("[lizard] Analysis complete: %d functions", len(df))
        return df

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_lizard(self, root_directory: Path) -> Optional[pd.DataFrame]:
        logger.debug("[lizard] Discovering source files in %s", root_directory)

        try:
            all_files = list(
                lizard.get_all_source_files([str(root_directory)], exclude_patterns=[], lans=None)
            )
        except Exception:
            logger.exception("[lizard] File discovery failed")
            raise

        source_files = [f for f in all_files if Path(f).suffix in _C_EXTENSIONS]
        if not source_files:
            logger.warning("[lizard] No C/C++ source files found in %s", root_directory)
            return None

        logger.info("[lizard] Found %d source files", len(source_files))

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
                            "complexity": float(func.cyclomatic_complexity),
                            "nesting": float(getattr(func, "top_nesting_level", 0)),
                            "params": float(func.parameter_count),
                            "lines": float(func.length),
                        }
                    )
            except Exception:
                logger.exception("[lizard] Failed to analyse file: %s", file_path)

        if not raw:
            return None

        df = pd.DataFrame(raw)

        # Step 1: complexity binning
        num_bins = min(10, len(df))
        try:
            df["bin"] = pd.qcut(df["complexity"].rank(method="first"), num_bins, labels=False)
        except Exception:
            logger.exception("[lizard] Binning failed — using single bin")
            df["bin"] = 0

        # Step 2: per-bin normalisation of nesting, params, lines
        for col in ("nesting", "params", "lines"):
            c_min = df.groupby("bin")[col].transform("min")
            c_max = df.groupby("bin")[col].transform("max")
            denom = (c_max - c_min).replace(0, 1)
            df[f"norm_{col}"] = (df[col] - c_min) / denom

        df["score"] = df["norm_nesting"] + df["norm_params"] + df["norm_lines"]
        df.sort_values(["bin", "score"], ascending=[False, False], inplace=True)

        logger.info("[lizard] Scored %d functions", len(df))
        return df
