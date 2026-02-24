"""
Main Analyzer orchestrator.

Typical usage
-------------
::

    from analyzer import Analyzer

    analyzer = Analyzer(
        project_root="/path/to/project",
        algorithm="lizard",
        selector="top_risk",
    )

    # Phase 1 – scan the source tree and save <algorithm>_analysis.csv
    analysis_csv = analyzer.analyze(target_directory)

    # Phase 2 – pick functions and save selected_functions.csv
    selected = analyzer.select(N=5)
    for func in selected:
        print(func["function_name"], func.get("code", ""))
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from analyzer.base import (
    get_algorithm,
    get_selector,
    list_algorithms,
    list_selectors,
)

# Trigger registration by importing the concrete modules
import analyzer.algorithms  # noqa: F401
import analyzer.selectors  # noqa: F401

logger = logging.getLogger(__name__)


class Analyzer:
    """
    Orchestrates an :class:`~analyzer.base.AnalysisAlgorithm` and a
    :class:`~analyzer.base.SelectorAlgorithm` to produce standardised CSV
    outputs.

    Parameters
    ----------
    project_root:
        Root of the project being analysed; used as the output directory
        when no *output_dir* is passed to :meth:`analyze` / :meth:`select`.
    algorithm:
        Name of the registered :class:`~analyzer.base.AnalysisAlgorithm` to
        use (e.g. ``"lizard"``).  Defaults to ``"lizard"``.
    selector:
        Name of the registered :class:`~analyzer.base.SelectorAlgorithm` to
        use (e.g. ``"top_risk"``).  Defaults to ``"top_risk"``.
    """

    def __init__(
        self,
        project_root: Path = Path("."),
        algorithm: str = "lizard",
        selector: str = "top_risk",
    ) -> None:
        self.project_root = Path(project_root)
        self._algorithm = get_algorithm(algorithm)
        self._selector = get_selector(selector)
        self._analysis_df: Optional[pd.DataFrame] = None
        logger.info(
            "Initialised Analyzer [algorithm=%s, selector=%s, project_root=%s]",
            self._algorithm.name,
            self._selector.name,
            self.project_root,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def analyze(
        self,
        directory: Path,
        output_dir: Optional[Path] = None,
    ) -> Path:
        """
        Run the analysis algorithm on *directory* and save results to
        ``<algorithm>_analysis.csv``.

        Parameters
        ----------
        directory:
            Source directory to scan.
        output_dir:
            Where to write the CSV.  Defaults to *project_root*.

        Returns
        -------
        Path
            Absolute path to the written CSV file.
        """
        directory = Path(directory)
        output_dir = Path(output_dir) if output_dir else self.project_root
        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Running '%s' analysis on: %s", self._algorithm.name, directory)
        t0 = time.perf_counter()

        self._analysis_df = self._algorithm.analyze(directory)

        elapsed = time.perf_counter() - t0
        logger.info(
            "Analysis complete: %d functions in %.2fs",
            len(self._analysis_df),
            elapsed,
        )

        csv_path = output_dir / f"{self._algorithm.name}_analysis.csv"
        self._analysis_df.to_csv(csv_path, index=False)
        logger.info("Analysis CSV saved to: %s", csv_path)

        return csv_path

    def select(
        self,
        N: int = 1,
        output_dir: Optional[Path] = None,
        analysis_csv: Optional[Path] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Apply the selector to the analysis results and save
        ``selected_functions.csv``.

        Call :meth:`analyze` first, or pass *analysis_csv* to load from a
        previously saved CSV.

        Parameters
        ----------
        N:
            Maximum number of functions to select (interpretation is
            selector-specific; e.g. ``top_risk`` returns the *N* highest-
            scoring functions, while ``longest`` always returns 1).
        output_dir:
            Where to write ``selected_functions.csv``.  Defaults to
            *project_root*.
        analysis_csv:
            Path to a CSV produced by a previous :meth:`analyze` call.  When
            supplied, the in-memory DataFrame is replaced with the CSV
            contents.

        Returns
        -------
        list of dict or None
            Each dict represents one selected function.  The dict contains at
            least ``filepath`` and ``function_name``, plus all columns
            produced by the analysis algorithm (complexity, code, …).
            Returns ``None`` when no analysis data is available.
        """
        if analysis_csv is not None:
            logger.info("Loading analysis data from CSV: %s", analysis_csv)
            self._analysis_df = pd.read_csv(analysis_csv)

        if self._analysis_df is None or self._analysis_df.empty:
            logger.warning("No analysis data available — call analyze() first.")
            return None

        output_dir = Path(output_dir) if output_dir else self.project_root
        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Running '%s' selector (N=%d)", self._selector.name, N)
        selected_df = self._selector.select(self._analysis_df, N=N)

        csv_path = output_dir / "selected_functions.csv"
        selected_df.to_csv(csv_path, index=False)
        logger.info("selected_functions.csv saved to: %s", csv_path)

        # Return full rows from the in-memory DataFrame so downstream code
        # (e.g. AutoUP) can access code, includes, and other rich fields.
        key = ["filepath", "function_name"]
        merged = selected_df.merge(self._analysis_df, on=key, how="left")
        records = merged.to_dict(orient="records")
        logger.info("Returning %d selected function records", len(records))
        return records

    # ------------------------------------------------------------------
    # Accessors / convenience
    # ------------------------------------------------------------------

    def get_analysis_dataframe(self) -> Optional[pd.DataFrame]:
        """Return the raw analysis DataFrame (``None`` before :meth:`analyze` is called)."""
        return self._analysis_df

    def save_analysis_report(self, output_path: Path) -> None:
        """Save the analysis DataFrame to *output_path* as CSV."""
        if self._analysis_df is not None:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            self._analysis_df.to_csv(output_path, index=False)
            logger.info("Analysis report saved to: %s", output_path)
        else:
            logger.warning("No analysis data to save")

    @property
    def algorithm_name(self) -> str:
        return self._algorithm.name

    @property
    def selector_name(self) -> str:
        return self._selector.name


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="LAFVT Analyzer — scan a C/C++ directory for vulnerability risk"
    )
    parser.add_argument("directory", help="Root directory containing C/C++ source files")
    parser.add_argument(
        "--algorithm",
        default="lizard",
        choices=list_algorithms(),
        help="Analysis algorithm (default: lizard)",
    )
    parser.add_argument(
        "--selector",
        default="top_N",
        choices=list_selectors(),
        help="Selection algorithm (default: top_N)",
    )
    parser.add_argument(
        "--threshold",
        type=str,
        default="10",
        help="Selector threshold: integer (e.g. 5) or percent (e.g. 10%%) (default: 10)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for CSV files (default: current directory)",
    )
    args = parser.parse_args()

    root_dir = Path(args.directory)
    if not root_dir.is_dir():
        logger.error("Not a directory: %s", root_dir)
        return 1

    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd()

    analyzer = Analyzer(
        project_root=output_dir,
        algorithm=args.algorithm,
        selector=args.selector,
    )

    analysis_csv = analyzer.analyze(root_dir, output_dir=output_dir)
    print(f"Analysis CSV: {analysis_csv}")

    selected = analyzer.select(N=args.threshold, output_dir=output_dir)
    if selected:
        print(f"\nSelected {len(selected)} functions:")
        for i, func in enumerate(selected, 1):
            score = func.get("score", 0)
            print(
                f"  {i}. {func.get('function_name')} "
                f"({func.get('filepath')}, "
                f"lines {func.get('start_line')}-{func.get('end_line')}, "
                f"score={score:.4f})"
            )
    else:
        print("No functions selected.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
