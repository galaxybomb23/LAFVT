"""
analyzer.algorithms
~~~~~~~~~~~~~~~~~~~
Import all algorithm modules here so their ``@register_algorithm`` decorators
run and register the classes into the global registry.

To add a new algorithm:
1. Create ``analyzer/algorithms/my_algorithm.py``
2. Define a class with ``name = "my_algorithm"`` decorated with ``@register_algorithm``
3. Add ``from . import my_algorithm`` below.
"""

from . import lizard  # noqa: F401
from . import loc     # noqa: F401
