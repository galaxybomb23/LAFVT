"""
Standard function selectors shipped with LAFVT.

Each selector is registered automatically when this module is imported.

Registered names
-----------------
``top_N``    – top-N rows by ``score`` (highest first).
``bottom_N`` – bottom-N rows by ``score`` (lowest first).
``first``    – first row in the DataFrame (as ordered by the algorithm).
``last``     – last row in the DataFrame.
``all``      – every row, no filtering.

N parameter
-----------
Both ``top_N`` and ``bottom_N`` accept *N* as either:

* An **integer** (e.g. ``5``) — select exactly that many functions.
* A **percentage string** (e.g. ``"10%"``) — select that fraction of the
  total function count, rounded up to at least 1.
"""

from __future__ import annotations

import logging
import math
from typing import Union

import pandas as pd

from analyzer.base import SelectorAlgorithm, register_selector

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _resolve_n(n: Union[int, str], total: int) -> int:
    """
    Convert *n* to a concrete row count.

    Parameters
    ----------
    n:
        Either an integer count or a percentage string like ``"10%"``.
    total:
        Total number of rows available.

    Returns
    -------
    int
        Row count, clamped to [1, total].
    """
    if isinstance(n, str) and n.endswith("%"):
        pct = float(n[:-1]) / 100.0
        count = max(1, math.ceil(total * pct))
    else:
        count = int(n)
    return max(1, min(count, total))


# ---------------------------------------------------------------------------
# Concrete selectors
# ---------------------------------------------------------------------------

@register_selector
class TopNSelector(SelectorAlgorithm):
    """
    Select the top-N functions ordered by descending ``score``.

    Falls back to DataFrame order when the ``score`` column is absent.
    Register name: ``"top_N"``.
    """

    name = "top_N"

    def select(self, df: pd.DataFrame, N: Union[int, str] = 1) -> pd.DataFrame:
        if df.empty:
            logger.warning("[top_N] Received empty DataFrame")
            return self._to_output_df(df)

        count = _resolve_n(N, len(df))

        if "score" in df.columns:
            ranked = df.sort_values("score", ascending=False)
        else:
            logger.warning("[top_N] 'score' column not found — using original order")
            ranked = df

        result = ranked.head(count)
        logger.info("[top_N] Selected %d / %d functions (N=%s)", len(result), len(df), N)
        return self._validate_output(self._to_output_df(result))


@register_selector
class BottomNSelector(SelectorAlgorithm):
    """
    Select the bottom-N functions ordered by ascending ``score``.

    Falls back to DataFrame order when the ``score`` column is absent.
    Register name: ``"bottom_N"``.
    """

    name = "bottom_N"

    def select(self, df: pd.DataFrame, N: Union[int, str] = 1) -> pd.DataFrame:
        if df.empty:
            logger.warning("[bottom_N] Received empty DataFrame")
            return self._to_output_df(df)

        count = _resolve_n(N, len(df))

        if "score" in df.columns:
            ranked = df.sort_values("score", ascending=True)
        else:
            logger.warning("[bottom_N] 'score' column not found — using original order")
            ranked = df

        result = ranked.head(count)
        logger.info("[bottom_N] Selected %d / %d functions (N=%s)", len(result), len(df), N)
        return self._validate_output(self._to_output_df(result))


@register_selector
class FirstSelector(SelectorAlgorithm):
    """
    Select the first function in the analysis output.  Register name: ``"first"``.
    """

    name = "first"

    def select(self, df: pd.DataFrame, N: Union[int, str] = 1) -> pd.DataFrame:
        result = df.head(1)
        logger.info("[first] Selected first function")
        return self._validate_output(self._to_output_df(result))


@register_selector
class LastSelector(SelectorAlgorithm):
    """
    Select the last function in the analysis output.  Register name: ``"last"``.
    """

    name = "last"

    def select(self, df: pd.DataFrame, N: Union[int, str] = 1) -> pd.DataFrame:
        result = df.tail(1)
        logger.info("[last] Selected last function")
        return self._validate_output(self._to_output_df(result))


@register_selector
class AllSelector(SelectorAlgorithm):
    """
    Return every function unchanged.  Register name: ``"all"``.
    """

    name = "all"

    def select(self, df: pd.DataFrame, N: Union[int, str] = 1) -> pd.DataFrame:
        logger.info("[all] Returning all %d functions", len(df))
        return self._validate_output(self._to_output_df(df))

    def select(self, df: pd.DataFrame, N: int = 1) -> pd.DataFrame:
        logger.info("[all] Returning all %d functions", len(df))
        return self._validate_output(self._to_output_df(df))
