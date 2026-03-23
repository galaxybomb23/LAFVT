"""
analyzer.selectors.post
~~~~~~~~~~~~~~~~~~~~~~~
Post-selector plugins that expand an initial function selection.

Import all post-selector modules here so their ``@register_post_selector``
decorators run and populate the global registry.
"""

from . import root_func_file  # noqa: F401
from . import root_func_codebase  # noqa: F401
