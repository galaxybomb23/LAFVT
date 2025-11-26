from typing import List, Dict, Any, Optional

class FunctionSelector:
    def __init__(self, algorithm: str = 'longest', ):
        """
        Initialize the selector with a specific algorithm.
        
        Args:
            algorithm: Selection algorithm to use ('longest', 'shortest', 'first', 'last')
        """
        algorithms = {
            'longest': self._select_longest,
            'shortest': self._select_shortest,
            'first': self._select_first,
            'last': self._select_last,
            'all': self._select_all
        }
        
        if algorithm not in algorithms:
            raise ValueError(f"Unknown algorithm: {algorithm}. Choose from {list(algorithms.keys())}")
        
        self.select = algorithms[algorithm]

    def select_top_N(self, functions: List[Dict[str, Any]], N: int) -> Optional[List[Dict[str, Any]]]:
        """Select top N functions based on the specified algorithm."""
        if not functions or N <= 0:
            return None
        
        # Sort functions based on the selection criteria
        if self.select == self._select_longest:
            sorted_funcs = sorted(functions, key=lambda f: f.get('end_line', 0) - f.get('start_line', 0), reverse=True)
        elif self.select == self._select_shortest:
            sorted_funcs = sorted(functions, key=lambda f: f.get('end_line', 0) - f.get('start_line', 0))
        elif self.select == self._select_first:
            sorted_funcs = functions
        elif self.select == self._select_last:
            sorted_funcs = list(reversed(functions))
        else:
            return None
        
        return sorted_funcs[:N]
    
    def _select_all(self, functions: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        return functions if functions else None
    
    def _select_longest(self, functions: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        if not functions:
            return None

        max_lines = -1
        longest_function = None

        for func in functions:
            try:
                start = func.get('start_line', 0)
                end = func.get('end_line', 0)
                lines = end - start + 1
                
                if lines > max_lines:
                    max_lines = lines
                    longest_function = func
            except Exception:
                continue

        return [longest_function] if longest_function else None
    
    def _select_shortest(self, functions: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        if not functions:
            return None

        min_lines = float('inf')
        shortest_function = None

        for func in functions:
            try:
                start = func.get('start_line', 0)
                end = func.get('end_line', 0)
                lines = end - start + 1
                
                if lines < min_lines:
                    min_lines = lines
                    shortest_function = func
            except Exception:
                continue

        return [shortest_function] if shortest_function else None
    
    def _select_first(self, functions: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        return [functions[0]] if functions else None
    
    def _select_last(self, functions: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        return [functions[-1]] if functions else None
