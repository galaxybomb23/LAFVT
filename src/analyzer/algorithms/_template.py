"""
Template for a new LAFVT analysis algorithm.
=============================================

QUICKSTART
----------
1. Copy this file to ``analyzer/algorithms/my_algorithm.py``
   (replace ``my_algorithm`` with a short, lowercase, underscore-separated name).
2. Work through every TODO comment below.
3. Add ``from . import my_algorithm`` to ``analyzer/algorithms/__init__.py``.
4. Run the standalone analyzer to verify::

       cd LAFVT/src
       python -m analyzer <path/to/source> --algorithm my_algorithm

WHAT THIS FILE MUST DO
-----------------------
* Declare a class that inherits :class:`~analyzer.base.AnalysisAlgorithm`.
* Set the ``name`` class attribute to a unique, stable, lowercase string —
  this becomes the ``--algorithm`` CLI flag value **and** the prefix of the
  output CSV file (``<name>_analysis.csv``).
* Decorate the class with ``@register_algorithm`` so the registry picks it up
  automatically when the module is imported.
* Implement the single abstract method ``analyze(root_directory)``, which must
  return a :class:`pandas.DataFrame` containing **at minimum** the three columns:

    * ``filepath``      – path to the source file (absolute POSIX string)
    * ``function_name`` – name of the function / method
    * ``score``         – numeric ranking value used by selectors (higher = higher priority)

  Any additional columns (raw metrics, bins, …) are welcome and will be
  preserved in the CSV.

OUTPUT DATA CONTRACT
---------------------
The DataFrame returned by ``analyze()`` will be written verbatim to
``<name>_analysis.csv`` by the :class:`~analyzer._analyzer.Analyzer`
orchestrator.  The ``selected_functions.csv`` produced by the selector will
always contain **only** ``filepath`` and ``function_name``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

# Re-export the two tools needed for every algorithm.
from analyzer.base import AnalysisAlgorithm, register_algorithm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TODO 1: rename the class to something meaningful, e.g. MyAlgorithm
# ---------------------------------------------------------------------------
@register_algorithm
class TemplateAlgorithm(AnalysisAlgorithm):
    """
    TODO 2: Write a one-line summary of what this algorithm measures.

    Longer description explaining the approach, any external tools required,
    and the additional columns this algorithm adds to the output DataFrame.

    Register name: ``"template"``  ← TODO 3: change to your algorithm name
    """

    # TODO 3: Set a unique lowercase name.  This is the --algorithm CLI flag value
    #         and the prefix of the output CSV (``template_analysis.csv``).
    name = "template"

    # -----------------------------------------------------------------------
    # Optional: declare algorithm-specific columns so callers know what to
    # expect.  Not enforced by the base class, but good documentation.
    # -----------------------------------------------------------------------
    EXTRA_COLUMNS: tuple = (
        # TODO 4 (optional): list any scoring / metric columns you add, e.g.:
        # "my_score",
        # "raw_metric_x",
    )

    # -----------------------------------------------------------------------
    # Optional __init__: load models, create external tool handles, etc.
    # -----------------------------------------------------------------------
    def __init__(self) -> None:
        # TODO 5 (optional): initialise any stateful resources once here
        # rather than inside analyze() so they can be reused across calls.
        #
        # Example:
        #   self._tool = MyExternalTool()
        pass

    # -----------------------------------------------------------------------
    # REQUIRED — this is the only method you must implement.
    # -----------------------------------------------------------------------
    def analyze(self, root_directory: Path) -> pd.DataFrame:
        """
        Scan *root_directory* for C/C++ source files and return per-function
        metrics.

        Parameters
        ----------
        root_directory:
            Absolute (or relative) path to the project you want to analyse.

        Returns
        -------
        pd.DataFrame
            Must contain at least ``filepath`` and ``function_name`` columns.
            Add as many extra metric columns as you like.
        """
        root_directory = Path(root_directory)
        logger.info("[%s] Starting analysis of: %s", self.name, root_directory)

        # -----------------------------------------------------------------------
        # TODO 6: Replace the stub below with your real analysis logic.
        #
        # Common pattern (used by the built-in Lizard algorithm):
        #   1. Discover all .c / .h / .cpp files under root_directory.
        #   2. For each file, iterate over its functions.
        #   3. Collect a list of row dicts and build a DataFrame at the end.
        #
        # Example skeleton:
        # -----------------------------------------------------------------------
        rows = []

        # for file_path in root_directory.rglob("*"):
        #     if file_path.suffix not in {".c", ".h", ".cpp", ".hpp", ".cc"}:
        #         continue
        #     for func in _extract_functions(file_path):          # your helper
        #         rows.append(
        #             {
        #                 "filepath":      str(file_path.relative_to(root_directory)),
        #                 "function_name": func.name,
        #                 "start_line":    func.start_line,
        #                 "end_line":      func.end_line,
        #                 # TODO: add your own metric columns here
        #                 "my_score":      compute_score(func),
        #             }
        #         )

        if not rows:
            logger.warning("[%s] No functions found in %s", self.name, root_directory)
            # Return an *empty* but correctly-shaped DataFrame so the orchestrator
            # handles the "nothing found" case gracefully.
            return pd.DataFrame(columns=["filepath", "function_name"])

        df = pd.DataFrame(rows)

        # Always call _validate_output before returning — it raises if the
        # required columns are missing, making bugs easy to catch.
        return self._validate_output(df)
