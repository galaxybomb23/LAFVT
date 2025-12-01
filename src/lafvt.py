import sys
import argparse
from pathlib import Path
from extractor import FunctionExtractor
from selector import FunctionSelector
from checkpointer import FunctionCheckpointer
from autoup_wrapper import AutoUPWrapper
from report_merger import ReportMerger
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
import dotenv

def main():
    # Input arguments
    parser = argparse.ArgumentParser(description="LAFVT: Lightweight Automated Function Verification Toolchain")
    parser.add_argument("--target_directory", help="Directory to scan for C/C++ functions")
    parser.add_argument("--output_dir", default="lafvt_output",help="Directory to store results and reports")
    parser.add_argument("--autoup_root", default="./AutoUP", help="Path to AutoUP root directory")
    parser.add_argument("--no-cache", default=False, action="store_true", help="Do not use cache")
    parser.add_argument("--OPENAI_API_KEY", default=dotenv.get_key(".env", "OPENAI_API_KEY"), help="OpenAI API Key for AutoUP usage")
    args = parser.parse_args()

    # if no OPENAI_API_KEY provided, exit
    if not args.OPENAI_API_KEY:
        print("Error: No OpenAI API Key provided. Set it via --OPENAI_API_KEY or in .env file.")
        sys.exit(1)
    
    target_dir = Path(args.target_directory).resolve()
    output_dir = Path(args.output_dir).resolve()
    autoup_root = Path(args.autoup_root).resolve()
    
    if not target_dir.exists():
        print(f"Error: Target directory '{target_dir}' does not exist.")
        sys.exit(1)
        
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Extract functions
    print("--- Step 1: Extracting functions ---")
    extractor = FunctionExtractor()
    functions = extractor.extract(target_dir)
    print(f"Found {len(functions)} functions.")
    
    # 2. Select functions    
    print("--- Step 2: Selecting target function ---")
    selector = FunctionSelector(algorithm='all')
    selected_funcs = selector.select(functions)
    
    if not selected_funcs:
        print("No functions found to select.")
        sys.exit(0)
        
    print(f"Selected functions ({len(selected_funcs)}): {', '.join([func['name'] for func in selected_funcs])}")

    
    print("--- Step 3: Checking cache ---")
    checkpointer = FunctionCheckpointer()
    if not args.no_cache:
        # filter out functions that are not new
        for selected_func in selected_funcs:
            if not checkpointer.is_new(selected_func):
                print(f"Function '{selected_func['name']}' has not changed since last run. Skipping verification.")
                selected_funcs.remove(selected_func)
            # For now, just skip AutoUP, but cache only stores hash, so need to keep track of old results.
            
    # 4. AutoUP
    print("--- Step 4: Running AutoUP ---")
    results = [] # Results for future merger expansion
    
    for selected_func in selected_funcs:
        autoup = AutoUPWrapper(autoup_root)
        success, message = autoup.run(selected_func, output_dir)
        
        result = {
            "name": selected_func['name'],
            "success": success,
            "message": message,
            "artifacts_path": str(output_dir / selected_func['name'])
        }
        results.append(result)
        print(f"Completed: {result['name']}")
        
    # 5. Merge Reports
    print("--- Step 5: Merging reports ---")
    merger = ReportMerger()
    merger.merge(output_dir)

    # 6. Validation
    print("--- Step 6: Validating results ---")
    # TODO: Add validation
    
    print("--- LAFVT Execution Complete ---")

if __name__ == "__main__":
    main()
