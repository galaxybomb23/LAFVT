"""
root_func_file — intra-file root-caller post-selector.
======================================================

For each function in the initial selection, parses its source file with
libclang and walks the call graph backwards to find **root callers**
(functions defined in that same file that are not called by any other
function in the file).

Example
-------
If function C is selected and, within its file, D calls C while A and B
call D, then A and B are identified as root callers of C in that file.
The output contains A, B **and** the original selection C.

Register name: ``"root_func_file"``
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Set, Tuple

import pandas as pd

from analyzer.base import PostSelectorAlgorithm, register_post_selector
from analyzer.selectors.post._callgraph import (
    extract_calls_from_file,
    extract_definitions,
    find_root_callers,
)

logger = logging.getLogger(__name__)


@register_post_selector
class RootFuncFilePostSelector(PostSelectorAlgorithm):
    """
    Expand the selection to include root callers within each file.

    Register name: ``"root_func_file"``
    """

    name = "root_func_file"

    def post_select(
        self,
        selected_df: pd.DataFrame,
        analysis_df: pd.DataFrame,
        source_root: Path,
    ) -> pd.DataFrame:
        if selected_df.empty:
            logger.warning("[root_func_file] Empty selection — nothing to expand")
            return selected_df

        result_keys: Set[Tuple[str, str]] = set()

        # Preserve all original selections
        for _, row in selected_df.iterrows():
            result_keys.add((row["filepath"], row["function_name"]))

        # Group selected functions by file for efficient parsing
        grouped = selected_df.groupby("filepath")["function_name"].apply(set).to_dict()

        for filepath_str, target_names in grouped.items():
            filepath = Path(str(filepath_str))
            if not filepath.is_file():
                logger.warning(
                    "[root_func_file] File not found, skipping: %s", filepath
                )
                continue

            call_map = extract_calls_from_file(filepath)
            if not call_map:
                logger.debug(
                    "[root_func_file] No call graph extracted from: %s", filepath
                )
                continue

            definitions = extract_definitions(filepath)

            for target in target_names:
                roots = find_root_callers(call_map, target)
                for root_name in roots:
                    if root_name in definitions:
                        result_keys.add((str(filepath_str), root_name))
                        logger.debug(
                            "[root_func_file] %s → root caller: %s (in %s)",
                            target, root_name, filepath.name,
                        )

        logger.info(
            "[root_func_file] Expanded %d → %d functions",
            len(selected_df), len(result_keys),
        )

        result_df = pd.DataFrame(
            sorted(result_keys), columns=["filepath", "function_name"]
        )
        return self._validate_output(result_df)
