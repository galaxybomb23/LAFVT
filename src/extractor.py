import os
import clang.cindex
from clang.cindex import CursorKind
from pathlib import Path
from typing import List, Dict, Any

class FunctionExtractor:
    def __init__(self):
        self.index = clang.cindex.Index.create()

    def extract(self, target_dir: Path) -> List[Dict[str, Any]]:
        all_functions = []
        target_dir = Path(target_dir)

        for file_path in target_dir.rglob('*'):
            if file_path.suffix in ['.c', '.cpp', '.cc', '.h', '.hpp']:
                try:
                    tu = self.index.parse(str(file_path))
                    funcs = self._find_functions(tu.cursor, str(file_path))
                    includes = self._get_includes(file_path)
                    
                    for f in funcs:
                        f['includes'] = includes
                        all_functions.append(f)
                        
                except Exception as e:
                    print(f"Error processing {file_path}: {e}")
        
        return all_functions

    def _find_functions(self, node, file_path: str) -> List[Dict[str, Any]]:
        functions = []
        if node.kind in [CursorKind.FUNCTION_DECL, CursorKind.CXX_METHOD, CursorKind.FUNCTION_TEMPLATE]:
            if node.location.file:
                node_file = Path(node.location.file.name).resolve()
                target_file = Path(file_path).resolve()
                
                if node_file == target_file:
                    start = node.extent.start
                    end = node.extent.end
                    
                    try:
                        with open(file_path, 'rb') as f:
                            content = f.read()
                            func_body = content[start.offset:end.offset].decode('utf-8', errors='ignore')

                        func_data = {
                            "name": node.spelling,
                            "file": Path(file_path).as_posix(),
                            "start_line": start.line,
                            "end_line": end.line,
                            "code": func_body,
                            "includes": [] 
                        }
                        functions.append(func_data)
                    except Exception as e:
                        print(f"Error reading function body in {file_path}: {e}")
                
        for child in node.get_children():
            functions.extend(self._find_functions(child, file_path))
        return functions

    def _get_includes(self, file_path: Path) -> List[str]:
        includes = []
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#include"):
                        includes.append(line)
        except Exception:
            pass
        return includes
