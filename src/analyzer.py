import lizard
import pandas as pd
import os
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional
import clang.cindex
from clang.cindex import CursorKind

# Configure logger
LOG_LEVEL = logging.DEBUG if os.environ.get("LAFVT_DEBUG") in ("1", "true", "True") else logging.INFO
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class Analyzer:
    """
    Unified analyzer that combines:
    - Lizard-based vulnerability risk analysis (leopard scoring)
    - Clang-based function extraction (detailed code extraction)
    - Function selection algorithms
    """
    
    def __init__(self, selection_algorithm: str = 'longest'):
        """
        Initialize the Analyzer.
        
        Args:
            selection_algorithm: Algorithm to use for function selection
                ('longest', 'shortest', 'first', 'last', 'all', 'top_risk')
        """
        self.selection_algorithm = selection_algorithm
        self.clang_index = clang.cindex.Index.create()
        self._analysis_df = None
        logger.info("Initialized Analyzer with selection algorithm: %s", selection_algorithm)
    
    def analyze_and_extract(self, root_directory: Path) -> Optional[List[Dict[str, Any]]]:
        """
        Step 1: Analyze vulnerability risk using Lizard (Leopard scoring)
        Step 2: Extract detailed function information using Clang
        
        Returns:
            List of function dictionaries with combined metrics and code details
        """
        logger.info("Starting analysis and extraction for: %s", root_directory)
        
        # Phase 1: Lizard Analysis for Leopard Scoring
        logger.info("Phase 1: Running Lizard analysis for vulnerability scoring...")
        analysis_df = self._analyze_with_lizard(root_directory)
        
        if analysis_df is None or analysis_df.empty:
            logger.warning("No functions found during Lizard analysis")
            return None
        
        self._analysis_df = analysis_df
        logger.info("Lizard analysis complete: %d functions analyzed", len(analysis_df))
        
        # Phase 2: Clang extraction for detailed code
        logger.info("Phase 2: Extracting detailed function information with Clang...")
        functions = self._extract_with_clang(root_directory, analysis_df)
        
        if not functions:
            logger.warning("No functions extracted with Clang")
            return None
        
        logger.info("Extraction complete: %d functions with detailed information", len(functions))
        return functions
    
    def _analyze_with_lizard(self, root_directory: Path) -> Optional[pd.DataFrame]:
        """
        Analyze code using Lizard and calculate Leopard vulnerability scores.
        Based on analyze.py logic.
        """
        logger.debug("Scanning directory: %s", root_directory)
        
        # 1. File Discovery
        extensions = ["c", "h", "cpp", "hpp"]
        try:
            all_files = list(lizard.get_all_source_files([str(root_directory)], exclude_patterns=[], lans=None))
        except Exception:
            logger.exception("Failed while discovering source files with lizard")
            raise
        
        # Filter for C/C++ extensions
        source_files = [f for f in all_files if f.split('.')[-1] in extensions]
        
        if not source_files:
            logger.warning("No C/C++ source files found in %s", root_directory)
            return None
        
        logger.info("Found %d source files", len(source_files))
        raw_data = []

        # 2. Metrics Extraction
        for file_path in source_files:
            logger.debug("Analyzing file: %s", file_path)
            try:
                analysis = lizard.analyze_file(file_path)
                for func in analysis.function_list:
                    nesting = getattr(func, 'top_nesting_level', 0)
                    
                    raw_data.append({
                        "function": func.name,
                        "file": os.path.relpath(file_path, root_directory),
                        "start_line": func.start_line,
                        "end_line": func.end_line,
                        "complexity": float(func.cyclomatic_complexity),
                        "nesting": float(nesting),
                        "params": float(func.parameter_count),
                        "lines": float(func.length)
                    })
            except Exception:
                logger.exception("Failed to analyze file: %s", file_path)
                continue

        if not raw_data:
            logger.warning("No functions extracted from source files")
            return None

        df = pd.DataFrame(raw_data)
        logger.debug("Extracted metrics for %d functions", len(df))

        # 3. Leopard Step 1: Complexity Binning
        num_bins = min(10, len(df))
        logger.debug("Creating %d bins for %d functions", num_bins, len(df))
        
        if num_bins > 0:
            try:
                df['bin'] = pd.qcut(df['complexity'].rank(method='first'), num_bins, labels=False)
            except Exception:
                logger.exception("Failed while binning complexity — falling back to single bin")
                df['bin'] = 0
        else:
            df['bin'] = 0

        # 4. Leopard Step 2: Ranking (Vectorized)
        for col in ['nesting', 'params', 'lines']:
            c_min = df.groupby('bin')[col].transform('min')
            c_max = df.groupby('bin')[col].transform('max')
            
            denom = c_max - c_min
            denom = denom.replace(0, 1)
            
            df[f'norm_{col}'] = (df[col] - c_min) / denom
            logger.debug("Computed normalized column: norm_%s", col)

        # Sum normalized scores
        df['leopard_score'] = df['norm_nesting'] + df['norm_params'] + df['norm_lines']
        logger.info("Computed leopard scores for %d functions", len(df))

        # 5. Final Sort
        final_report = df.sort_values(
            by=['bin', 'leopard_score'], 
            ascending=[False, False]
        )
        
        return final_report
    
    def _extract_with_clang(self, root_directory: Path, analysis_df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Extract detailed function information using Clang.
        Merges with Lizard analysis results.
        Based on extractor.py logic.
        """
        all_functions = []
        root_directory = Path(root_directory)
        
        # Create lookup for quick access to analysis data
        analysis_lookup = {}
        for _, row in analysis_df.iterrows():
            file_rel = row['file']
            func_name = row['function']
            key = (file_rel, func_name)
            analysis_lookup[key] = row.to_dict()
        
        logger.debug("Created analysis lookup with %d entries", len(analysis_lookup))

        for file_path in root_directory.rglob('*'):
            if file_path.suffix in ['.c', '.cpp', '.cc', '.h', '.hpp']:
                logger.debug("Extracting from file: %s", file_path)
                try:
                    tu = self.clang_index.parse(str(file_path))
                    funcs = self._find_functions_in_ast(tu.cursor, str(file_path))
                    includes = self._get_includes(file_path)
                    
                    for f in funcs:
                        f['includes'] = includes
                        
                        # Merge with Lizard analysis data
                        file_rel = os.path.relpath(str(file_path), root_directory)
                        key = (file_rel, f['name'])
                        
                        if key in analysis_lookup:
                            analysis_data = analysis_lookup[key]
                            f.update({
                                'complexity': analysis_data.get('complexity', 0),
                                'nesting': analysis_data.get('nesting', 0),
                                'params': analysis_data.get('params', 0),
                                'leopard_score': analysis_data.get('leopard_score', 0),
                                'bin': analysis_data.get('bin', 0),
                            })
                            logger.debug("Merged analysis data for function: %s", f['name'])
                        else:
                            logger.debug("No analysis data found for function: %s in %s", f['name'], file_rel)
                        
                        all_functions.append(f)
                        
                except Exception:
                    logger.exception("Error processing file: %s", file_path)
        
        return all_functions

    def _find_functions_in_ast(self, node, file_path: str) -> List[Dict[str, Any]]:
        """Recursively find function declarations in AST."""
        functions = []
        # type: ignore - CursorKind attributes exist at runtime despite linting warnings
        if node.kind in [CursorKind.FUNCTION_DECL, CursorKind.CXX_METHOD, CursorKind.FUNCTION_TEMPLATE]:  # type: ignore
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
                            "includes": []  # Will be filled later
                        }
                        functions.append(func_data)
                    except Exception:
                        logger.exception("Error reading function body in %s", file_path)
                
        for child in node.get_children():
            functions.extend(self._find_functions_in_ast(child, file_path))
        return functions

    def _get_includes(self, file_path: Path) -> List[str]:
        """Extract #include statements from a file."""
        includes = []
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#include"):
                        includes.append(line)
        except Exception:
            logger.exception("Error reading includes from %s", file_path)
        return includes
    
    def select(self, functions: List[Dict[str, Any]], N: int = 1) -> Optional[List[Dict[str, Any]]]:
        """
        Select functions based on the configured algorithm.
        Based on selector.py logic.
        
        Args:
            functions: List of function dictionaries
            N: Number of functions to select (only used for 'top_risk' algorithm)
            
        Returns:
            Selected functions or None if no functions available
        """
        if not functions or N <= 0:
            logger.warning("No functions to select or invalid N=%d", N)
            return None
        
        logger.info("Selecting functions using algorithm: %s", self.selection_algorithm)
        
        if self.selection_algorithm == 'all':
            return functions
        
        elif self.selection_algorithm == 'longest':
            longest = max(functions, key=lambda f: f.get('end_line', 0) - f.get('start_line', 0))
            logger.info("Selected longest function: %s (%d lines)", 
                       longest['name'], longest['end_line'] - longest['start_line'])
            return [longest]
        
        elif self.selection_algorithm == 'shortest':
            shortest = min(functions, key=lambda f: f.get('end_line', 0) - f.get('start_line', 0))
            logger.info("Selected shortest function: %s (%d lines)", 
                       shortest['name'], shortest['end_line'] - shortest['start_line'])
            return [shortest]
        
        elif self.selection_algorithm == 'first':
            logger.info("Selected first function: %s", functions[0]['name'])
            return [functions[0]]
        
        elif self.selection_algorithm == 'last':
            logger.info("Selected last function: %s", functions[-1]['name'])
            return [functions[-1]]
        
        elif self.selection_algorithm == 'top_risk':
            # Select top N by leopard_score
            sorted_funcs = sorted(functions, 
                                key=lambda f: f.get('leopard_score', 0), 
                                reverse=True)
            selected = sorted_funcs[:N]
            logger.info("Selected top %d highest risk functions", len(selected))
            return selected
        
        else:
            logger.error("Unknown selection algorithm: %s", self.selection_algorithm)
            return None
    
    def get_analysis_dataframe(self) -> Optional[pd.DataFrame]:
        """Return the full analysis DataFrame for reporting purposes."""
        return self._analysis_df
    
    def save_analysis_report(self, output_path: Path):
        """Save the complete analysis report as CSV."""
        if self._analysis_df is not None:
            output_path = Path(output_path)
            self._analysis_df.to_csv(output_path, index=False)
            logger.info("Analysis report saved to: %s", output_path)
        else:
            logger.warning("No analysis data to save")


def main():
    """Main entry point for running analyzer as a standalone script."""
    parser = argparse.ArgumentParser(
        description="Analyze C/C++ code for vulnerability risk and generate CSV report"
    )
    parser.add_argument(
        "directory",
        type=str,
        help="Root directory containing C/C++ source files to analyze"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="analysis_report.csv",
        help="Output CSV file path (default: analysis_report.csv)"
    )
    parser.add_argument(
        "--algorithm",
        type=str,
        choices=['longest', 'shortest', 'first', 'last', 'all', 'top_risk'],
        default='top_risk',
        help="Function selection algorithm (default: top_risk)"
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Number of top functions to display for top_risk algorithm (default: 10)"
    )
    
    args = parser.parse_args()
    
    root_dir = Path(args.directory)
    if not root_dir.exists():
        logger.error("Directory does not exist: %s", root_dir)
        return 1
    
    if not root_dir.is_dir():
        logger.error("Path is not a directory: %s", root_dir)
        return 1
    
    logger.info("=" * 60)
    logger.info("Starting vulnerability analysis")
    logger.info("Directory: %s", root_dir)
    logger.info("Algorithm: %s", args.algorithm)
    logger.info("=" * 60)
    
    # Initialize analyzer
    analyzer = Analyzer(selection_algorithm=args.algorithm)
    
    # Run analysis
    functions = analyzer.analyze_and_extract(root_dir)
    
    if not functions:
        logger.error("No functions found or analysis failed")
        return 1
    
    logger.info("Successfully analyzed %d functions", len(functions))
    
    # Save full analysis report
    output_path = Path(args.output)
    analyzer.save_analysis_report(output_path)
    
    # Display selected functions summary
    selected = analyzer.select(functions, N=args.top_n)
    if selected:
        logger.info("=" * 60)
        logger.info("Selected Functions (%s algorithm):", args.algorithm)
        logger.info("=" * 60)
        for i, func in enumerate(selected, 1):
            logger.info(
                "%d. %s (file: %s, lines: %d-%d, leopard_score: %.2f)",
                i,
                func.get('name', 'unknown'),
                func.get('file', 'unknown'),
                func.get('start_line', 0),
                func.get('end_line', 0),
                func.get('leopard_score', 0.0)
            )
        logger.info("=" * 60)
    
    logger.info("Analysis complete! Report saved to: %s", output_path.absolute())
    return 0


if __name__ == "__main__":
    exit(main())
