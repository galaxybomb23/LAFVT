"""
analyzer
~~~~~~~~
LAFVT Analyzer package.

Public API
----------
::

    from analyzer import Analyzer
    from analyzer.base import (
        AnalysisAlgorithm,
        SelectorAlgorithm,
        register_algorithm,
        register_selector,
        list_algorithms,
        list_selectors,
    )

Quick-start
-----------
::

    analyzer = Analyzer(project_root=Path("."), algorithm="lizard", selector="top_N")
    csv_path  = analyzer.analyze(Path("path/to/source"))
    selected  = analyzer.select(N=5)
"""

from analyzer._analyzer import Analyzer
from analyzer.base import (
    AnalysisAlgorithm,
    SelectorAlgorithm,
    get_algorithm,
    get_selector,
    list_algorithms,
    list_selectors,
    register_algorithm,
    register_selector,
)

__all__ = [
    "Analyzer",
    "AnalysisAlgorithm",
    "SelectorAlgorithm",
    "register_algorithm",
    "register_selector",
    "get_algorithm",
    "get_selector",
    "list_algorithms",
    "list_selectors",
]
