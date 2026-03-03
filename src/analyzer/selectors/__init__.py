"""
analyzer.selectors
~~~~~~~~~~~~~~~~~~
Import all selector modules here so their ``@register_selector`` decorators
run and register the classes into the global registry.

To add a new selector:
1. Create ``analyzer/selectors/my_selector.py``
2. Define a class with ``name = "my_selector"`` decorated with ``@register_selector``
3. Add ``from . import my_selector`` below.
"""

from . import standard  # noqa: F401
