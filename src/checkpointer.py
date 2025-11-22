import json
import hashlib
import os
from typing import Dict, Any, List

class FunctionCheckpointer:
    def __init__(self, cache_file: str = ".function_cache"):
        self.cache_file = os.path.join(os.path.dirname(__file__), cache_file)
        self.cache = self._load_cache()

    def _load_cache(self) -> Dict[str, List[str]]:
        if not os.path.exists(self.cache_file):
            return {}
        try:
            with open(self.cache_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            return {}

    def _save_cache(self):
        with open(self.cache_file, 'w') as f:
            json.dump(self.cache, f, indent=4)

    def is_new(self, function: Dict[str, Any]) -> bool:
        if not function or 'code' not in function or 'name' not in function:
            return False

        code = function['code']
        func_name = function['name']
        
        func_hash = hashlib.sha256(code.encode('utf-8')).hexdigest()

        if func_name not in self.cache:
            self.cache[func_name] = []

        if func_hash in self.cache[func_name]:
            return False
        else:
            self.cache[func_name].append(func_hash)
            self._save_cache()
            return True

    def clear_cache(self):
        self.cache = {}
        self._save_cache()
    