from typing import List, Dict, Any, Optional

class FunctionSelector:
    def select(self, functions: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
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

        # Return as list for future extension
        return [longest_function]
