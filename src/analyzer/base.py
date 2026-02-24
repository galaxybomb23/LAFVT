"""
Base abstractions and plugin registries for the LAFVT Analyzer.

Adding a new analysis algorithm
--------------------------------
1. Create a module under ``analyzer/algorithms/``.
2. Define a class that inherits from :class:`AnalysisAlgorithm`.
3. Set the ``name`` class attribute to a unique lowercase string.
4. Decorate the class with ``@register_algorithm``.
5. Import the module in ``analyzer/algorithms/__init__.py`` so the decorator runs.

Adding a new selector
----------------------
Same steps as above but inherit from :class:`SelectorAlgorithm`, use
``@register_selector``, and place the module under ``analyzer/selectors/``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, Union

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry storage
# ---------------------------------------------------------------------------

_ALGORITHM_REGISTRY: Dict[str, Type["AnalysisAlgorithm"]] = {}
_SELECTOR_REGISTRY: Dict[str, Type["SelectorAlgorithm"]] = {}


# ---------------------------------------------------------------------------
# Registration decorators
# ---------------------------------------------------------------------------

def register_algorithm(cls: Type["AnalysisAlgorithm"]) -> Type["AnalysisAlgorithm"]:
    """Decorator that registers an :class:`AnalysisAlgorithm` by its ``name``."""
    if not hasattr(cls, "name") or not cls.name:
        raise AttributeError(f"{cls.__qualname__} must define a non-empty class attribute 'name'")
    if cls.name in _ALGORITHM_REGISTRY:
        logger.warning("Algorithm '%s' is already registered — overwriting.", cls.name)
    _ALGORITHM_REGISTRY[cls.name] = cls
    logger.debug("Registered analysis algorithm: '%s' → %s", cls.name, cls.__qualname__)
    return cls


def register_selector(cls: Type["SelectorAlgorithm"]) -> Type["SelectorAlgorithm"]:
    """Decorator that registers a :class:`SelectorAlgorithm` by its ``name``."""
    if not hasattr(cls, "name") or not cls.name:
        raise AttributeError(f"{cls.__qualname__} must define a non-empty class attribute 'name'")
    if cls.name in _SELECTOR_REGISTRY:
        logger.warning("Selector '%s' is already registered — overwriting.", cls.name)
    _SELECTOR_REGISTRY[cls.name] = cls
    logger.debug("Registered selector algorithm: '%s' → %s", cls.name, cls.__qualname__)
    return cls


# ---------------------------------------------------------------------------
# Public registry access helpers
# ---------------------------------------------------------------------------

def get_algorithm(name: str) -> "AnalysisAlgorithm":
    """Instantiate a registered :class:`AnalysisAlgorithm` by name."""
    if name not in _ALGORITHM_REGISTRY:
        available = list(_ALGORITHM_REGISTRY.keys())
        raise ValueError(f"Unknown analysis algorithm '{name}'. Available: {available}")
    return _ALGORITHM_REGISTRY[name]()


def get_selector(name: str) -> "SelectorAlgorithm":
    """Instantiate a registered :class:`SelectorAlgorithm` by name."""
    if name not in _SELECTOR_REGISTRY:
        available = list(_SELECTOR_REGISTRY.keys())
        raise ValueError(f"Unknown selector '{name}'. Available: {available}")
    return _SELECTOR_REGISTRY[name]()


def list_algorithms() -> List[str]:
    """Return the names of all registered analysis algorithms."""
    return list(_ALGORITHM_REGISTRY.keys())


def list_selectors() -> List[str]:
    """Return the names of all registered selectors."""
    return list(_SELECTOR_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Abstract base classes
# ---------------------------------------------------------------------------

class AnalysisAlgorithm(ABC):
    """
    Contract that every analysis algorithm must fulfil.

    Subclasses must:
    * Set ``name`` (class attribute, unique lowercase string, e.g. ``"lizard"``).    
    * Implement :meth:`analyze`.

    The DataFrame returned by :meth:`analyze` **must** contain at least the
    two columns defined in :attr:`REQUIRED_COLUMNS`.  Additional
    algorithm-specific columns (scores, metrics, …) are encouraged.
    """

    #: Unique identifier used in file names and CLI/API arguments.
    name: str = ""

    #: Columns that every analysis DataFrame must expose.
    REQUIRED_COLUMNS: tuple = ("filepath", "function_name", "score")

    @abstractmethod
    def analyze(self, root_directory: Path) -> pd.DataFrame:
        """
        Scan *root_directory* for C/C++ source files and compute per-function
        metrics.

        Parameters
        ----------
        root_directory:
            Absolute path to the project root (or any sub-directory) that
            contains the source files to analyse.

        Returns
        -------
        pd.DataFrame
            At minimum the columns ``filepath`` (relative path string) and
            ``function_name`` (str).  Extra columns are algorithm-specific.

        Raises
        ------
        ValueError
            If *root_directory* does not exist or contains no analysable files.
        """

    def _validate_output(self, df: pd.DataFrame) -> pd.DataFrame:
        """Raise if the returned DataFrame is missing required columns."""
        missing = [c for c in self.REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(
                f"Algorithm '{self.name}' returned a DataFrame missing required columns: {missing}"
            )
        return df


class SelectorAlgorithm(ABC):
    """
    Contract that every selector must fulfil.

    Subclasses must:
    * Set ``name`` (class attribute, unique lowercase string, e.g. ``"top_risk"``).
    * Implement :meth:`select`.

    The DataFrame returned by :meth:`select` **must** contain at least
    ``filepath`` and ``function_name``.
    """

    #: Unique identifier used in file names and CLI/API arguments.
    name: str = ""

    #: Required output columns.
    REQUIRED_COLUMNS: tuple = ("filepath", "function_name")

    @abstractmethod
    def select(self, df: pd.DataFrame, N: Union[int, str] = 1) -> pd.DataFrame:
        """
        Choose at most *N* rows from *df* and return a tidy DataFrame.

        Parameters
        ----------
        df:
            Output produced by an :class:`AnalysisAlgorithm`.  Guaranteed to
            contain at least ``filepath``, ``function_name``, and ``score``.
        N:
            Number of rows to return.  Accepts an integer (e.g. ``5``) or a
            percent string (e.g. ``"10%"``) to select a fraction of the total.

        Returns
        -------
        pd.DataFrame
            Columns ``filepath`` and ``function_name`` at minimum.
        """

    def _validate_output(self, df: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in self.REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(
                f"Selector '{self.name}' returned a DataFrame missing required columns: {missing}"
            )
        return df

    # ------------------------------------------------------------------
    # Shared helper – subclasses may call this to keep their select()
    # implementations concise.
    # ------------------------------------------------------------------

    @staticmethod
    def _to_output_df(subset: pd.DataFrame) -> pd.DataFrame:
        """Return a copy that carries *only* ``filepath`` and ``function_name``."""
        return subset[["filepath", "function_name"]].reset_index(drop=True).copy()
