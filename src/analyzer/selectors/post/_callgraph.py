"""
Shared call-graph utilities backed by libclang.
================================================

Provides functions to parse C/C++ source files with ``clang.cindex`` and
extract intra-file or cross-file caller/callee relationships.

All functions are **stateless** — parsed translation units are cached via a
simple LRU dict so repeated requests for the same file are cheap.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from clang.cindex import (
    Config,
    CursorKind,
    Index,
    TranslationUnit,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level index (reusable across calls)
# ---------------------------------------------------------------------------

_index: Optional[Index] = None
_tu_cache: Dict[str, TranslationUnit] = {}

_C_EXTENSIONS: Set[str] = {".c", ".h", ".cpp", ".hpp", ".cc"}


def _get_index() -> Index:
    global _index
    if _index is None:
        _index = Index.create()
    return _index


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_file(
    filepath: Path,
    extra_args: Optional[List[str]] = None,
) -> Optional[TranslationUnit]:
    """
    Parse *filepath* into a libclang TranslationUnit.

    Results are cached by absolute path so repeated calls are free.
    Returns ``None`` if parsing fails.
    """
    key = str(filepath.resolve())
    if key in _tu_cache:
        return _tu_cache[key]

    args = extra_args or []
    # Common flags to improve parsing for standalone files
    args = ["-ferror-limit=0", "-w"] + args

    try:
        tu = _get_index().parse(
            key,
            args=args,
            options=TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
            | TranslationUnit.PARSE_SKIP_FUNCTION_BODIES * 0,  # we need bodies
        )
    except Exception:
        logger.debug("[callgraph] Failed to parse: %s", filepath)
        return None

    if tu is None:
        logger.debug("[callgraph] libclang returned None for: %s", filepath)
        return None

    _tu_cache[key] = tu
    return tu


def clear_cache() -> None:
    """Drop all cached TranslationUnits."""
    _tu_cache.clear()


# ---------------------------------------------------------------------------
# Call-graph extraction
# ---------------------------------------------------------------------------

def extract_calls_from_file(
    filepath: Path,
    extra_args: Optional[List[str]] = None,
) -> Dict[str, Set[str]]:
    """
    Build a caller → {callees} map for every function *defined* in *filepath*.

    Only direct ``CALL_EXPR`` nodes are considered (no function-pointer
    resolution).
    """
    tu = parse_file(filepath, extra_args)
    if tu is None:
        return {}

    resolved = str(filepath.resolve())
    call_map: Dict[str, Set[str]] = defaultdict(set)

    def _visit(cursor, parent_func: Optional[str] = None):
        # Only consider cursors from the target file
        if cursor.location.file and cursor.location.file.name == resolved:
            if cursor.kind == CursorKind.FUNCTION_DECL and cursor.is_definition():
                fname = cursor.spelling
                call_map.setdefault(fname, set())
                for child in cursor.get_children():
                    _visit(child, fname)
                return
        if parent_func is not None:
            if cursor.kind == CursorKind.CALL_EXPR:
                callee = cursor.spelling
                if callee:
                    call_map[parent_func].add(callee)
        for child in cursor.get_children():
            _visit(child, parent_func)

    _visit(tu.cursor)
    return dict(call_map)


def extract_definitions(
    filepath: Path,
    extra_args: Optional[List[str]] = None,
) -> Dict[str, Tuple[int, int]]:
    """
    Return ``{function_name: (start_line, end_line)}`` for every function
    defined in *filepath*.
    """
    tu = parse_file(filepath, extra_args)
    if tu is None:
        return {}

    resolved = str(filepath.resolve())
    defs: Dict[str, Tuple[int, int]] = {}

    for cursor in tu.cursor.get_children():
        if (
            cursor.kind == CursorKind.FUNCTION_DECL
            and cursor.is_definition()
            and cursor.location.file
            and cursor.location.file.name == resolved
        ):
            defs[cursor.spelling] = (
                cursor.extent.start.line,
                cursor.extent.end.line,
            )

    return defs


# ---------------------------------------------------------------------------
# Reverse call-graph traversal
# ---------------------------------------------------------------------------

def invert_call_map(call_map: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
    """Return callee → {callers} from a caller → {callees} map."""
    inv: Dict[str, Set[str]] = defaultdict(set)
    for caller, callees in call_map.items():
        for callee in callees:
            inv[callee].add(caller)
    return dict(inv)


def find_root_callers(
    call_map: Dict[str, Set[str]],
    target: str,
) -> Set[str]:
    """
    BFS backwards through *call_map* from *target* to find root callers
    (functions that are not called by any other function in the map).

    Parameters
    ----------
    call_map:
        caller → {callees} mapping (as returned by
        :func:`extract_calls_from_file`).
    target:
        The function name to trace callers for.

    Returns
    -------
    set of str
        Function names that are root callers of *target*.
    """
    callee_to_callers = invert_call_map(call_map)

    roots: Set[str] = set()
    visited: Set[str] = set()
    frontier: List[str] = []

    # Seed: direct callers of target
    for caller in callee_to_callers.get(target, set()):
        if caller != target:
            frontier.append(caller)

    while frontier:
        func = frontier.pop(0)
        if func in visited:
            continue
        visited.add(func)

        # A root is a function that nobody else in the call_map calls
        callers_of_func = callee_to_callers.get(func, set()) - {func}
        if not callers_of_func:
            roots.add(func)
        else:
            for caller in callers_of_func:
                if caller not in visited:
                    frontier.append(caller)

    return roots
