"""
root_func_codebase — codebase-wide root-caller post-selector.
=============================================================

Same approach as ``root_func_file`` but traces callers across the **entire
codebase**, not just within a single file.  Uses the full analysis DataFrame
to know which files contain which functions, then does targeted on-demand
parsing with libclang — only files that might contain callers are parsed.

The search is BFS-based and runs until all root callers (functions with no
callers) are found.

Register name: ``"root_func_codebase"``
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from analyzer.base import PostSelectorAlgorithm, register_post_selector
from analyzer.selectors.post._callgraph import (
    clear_cache,
    extract_calls_from_file,
    extract_definitions,
    invert_call_map,
)

logger = logging.getLogger(__name__)


@register_post_selector
class RootFuncCodebasePostSelector(PostSelectorAlgorithm):
    """
    Expand the selection to include root callers across the whole codebase.

    Register name: ``"root_func_codebase"``
    """

    name = "root_func_codebase"

    def post_select(
        self,
        selected_df: pd.DataFrame,
        analysis_df: pd.DataFrame,
        source_root: Path,
    ) -> pd.DataFrame:
        if selected_df.empty:
            logger.warning("[root_func_codebase] Empty selection — nothing to expand")
            return selected_df

        clear_cache()

        # Build a function_name → set of filepaths index from the analysis
        func_to_files: Dict[str, Set[str]] = defaultdict(set)
        for _, row in analysis_df.iterrows():
            func_to_files[row["function_name"]].add(row["filepath"])

        # Collect the global call map incrementally: caller → {callees}
        # keyed by (filepath, function_name) to avoid cross-file ambiguity.
        # We also keep a flat name-based reverse index for BFS.
        global_callee_to_callers: Dict[str, Set[Tuple[str, str]]] = defaultdict(set)
        parsed_files: Set[str] = set()

        def _parse_and_index(filepath_str: str) -> None:
            """Parse a file and add its call relationships to the global index."""
            if filepath_str in parsed_files:
                return
            parsed_files.add(filepath_str)

            fp = Path(filepath_str)
            if not fp.is_file():
                return

            call_map = extract_calls_from_file(fp)
            for caller, callees in call_map.items():
                for callee in callees:
                    global_callee_to_callers[callee].add((filepath_str, caller))

        # Preserve all original selections
        result_keys: Set[Tuple[str, str]] = set()
        for _, row in selected_df.iterrows():
            result_keys.add((row["filepath"], row["function_name"]))

        # BFS: start from the selected function names as targets
        targets: Set[str] = set(selected_df["function_name"])
        visited_targets: Set[str] = set()
        depth = 0

        while targets:
            depth += 1
            logger.info(
                "[root_func_codebase] BFS depth %d — %d target(s) to trace",
                depth, len(targets),
            )

            next_targets: Set[str] = set()

            for target_name in targets:
                if target_name in visited_targets:
                    continue
                visited_targets.add(target_name)

                # Parse every file that might define a caller of target_name.
                # We check all files known to the analysis (they contain C/C++
                # functions) and parse them to discover call relationships.
                # To keep it tractable, parse files that contain any function
                # that *might* call target_name.  We don't know which files
                # those are until we parse, so we parse files that the analysis
                # tells us contain functions — but only those not yet parsed.
                #
                # Optimisation: first parse files where target_name itself
                # lives (callers in the same file are most likely), then
                # expand to other files if needed.
                candidate_files: List[str] = []
                # Priority 1: files containing the target
                for fp in func_to_files.get(target_name, set()):
                    candidate_files.append(fp)
                # Priority 2: all other known files (only if not already parsed)
                remaining = set(analysis_df["filepath"].unique()) - parsed_files
                candidate_files.extend(sorted(remaining))

                for fp_str in candidate_files:
                    _parse_and_index(fp_str)

                # Now check the global reverse index for callers
                callers = global_callee_to_callers.get(target_name, set())
                for caller_fp, caller_name in callers:
                    key = (caller_fp, caller_name)
                    if key not in result_keys:
                        result_keys.add(key)
                        # Check if this caller is itself called by others
                        # (i.e. it's not a root yet) — add to next BFS wave
                        if caller_name not in visited_targets:
                            next_targets.add(caller_name)

            targets = next_targets

        # Identify true roots: functions in result_keys that have no callers
        # in the global index (or whose callers are all outside result_keys)
        final_roots: Set[Tuple[str, str]] = set()
        for key in result_keys:
            fp_str, fname = key
            callers = global_callee_to_callers.get(fname, set())
            has_caller_in_set = any(
                (cfp, cn) in result_keys and (cfp, cn) != key
                for cfp, cn in callers
            )
            if not has_caller_in_set:
                final_roots.add(key)

        # Also keep original selections even if they're not roots
        final_roots.update(
            (row["filepath"], row["function_name"])
            for _, row in selected_df.iterrows()
        )

        logger.info(
            "[root_func_codebase] Expanded %d → %d functions "
            "(parsed %d files, BFS depth %d)",
            len(selected_df), len(final_roots), len(parsed_files), depth,
        )

        result_df = pd.DataFrame(
            sorted(final_roots), columns=["filepath", "function_name"]
        )
        return self._validate_output(result_df)
