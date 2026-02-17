# import external modules
import argparse
from pathlib import Path
import dotenv
import os

# Import internal modules
from analyzer import Analyzer
from checkpointer import FunctionCheckpointer
from autoup_wrapper import AutoUPWrapper
from report_merger import ReportMerger
from assessment_report_generator import ViolationAssessmentReport

# for analysis
import time
import json


def main():
    #timing /analytics
    timings = {}
    prev_time = time.time()
    # Input arguments
    parser = argparse.ArgumentParser(description="LAFVT: Lightweight Automated Function Verification Toolchain")
    parser.add_argument("--target_directory", help="Directory to scan for C/C++ functions")
    parser.add_argument("--root_dir", default=os.getcwd(), help="Root directory of the project (default: current working directory)")
    parser.add_argument("--autoup_root", default="./AutoUP", help="Path to AutoUP root directory")
    parser.add_argument("--no-cache", default=False, action="store_true", help="Do not use cache")
    parser.add_argument("--OPENAI_API_KEY", default=dotenv.get_key(".env", "OPENAI_API_KEY"), help="OpenAI API Key for AutoUP usage")
    args = parser.parse_args()

    # if no OPENAI_API_KEY provided, exit
    if not args.OPENAI_API_KEY:
        # try to pull from bash env
        args.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
        if not args.OPENAI_API_KEY:
            print("Error: No OpenAI API Key provided. Set it via --OPENAI_API_KEY or in .env file.")
            return (1)
    
    if not args.target_directory:
        print("Error: No target directory provided. Use --target_directory to specify the directory to analyze.")
        return (1)
    
    #output dir is subdirectory of target dir if not provided
  
    target_dir = Path(args.target_directory).resolve()
    root_dir = Path(args.root_dir).resolve()
    output_dir = root_dir / "lafvt_output"
    autoup_root = Path(args.autoup_root).resolve()
    
    if not target_dir.exists():
        print(f"Error: Target directory '{target_dir}' does not exist.")
        return (1)
        
    output_dir.mkdir(parents=True, exist_ok=True)

    # === 1. Extract and Analyze functions ===

    print("--- Step 1: Extracting and analyzing functions ---")
    analyzer = Analyzer(selection_algorithm='top_risk')
    functions = analyzer.analyze_and_extract(target_dir)
    if functions is None:
        print("No functions found. Exiting.")
        return (0)
    print(f"Found {len(functions)} functions.")
    
    timings['extraction_time'] = time.time() - prev_time
    prev_time = time.time()
    
    # === 2. Select functions ===
    print("--- Step 2: Selecting target functions ---")
    selected_funcs = analyzer.select(functions, N=1)  # Select top 5 high-risk functions
    if selected_funcs is None:
        print("No functions selected. Exiting.")
        return (0)
    print(f"Selected functions ({len(selected_funcs)}): {', '.join([func['name'] for func in selected_funcs])}")

    timings['selection_time'] = time.time() - prev_time
    prev_time = time.time()

    print(f"Sample selected function:\n{selected_funcs[0]}")

    # === 3. Check Cache ===
    print("--- Step 3: Checking cache ---")
    checkpointer = FunctionCheckpointer()
    uncached_funcs = selected_funcs.copy()
    if not args.no_cache:
        # filter out functions that are not new
        for selected_func in selected_funcs:
            if checkpointer.is_new(selected_func):
                uncached_funcs.append(selected_func)
            # For now, just skip AutoUP, but cache only stores hash, so need to keep track of old results.
    
    timings['cache_checking_time'] = time.time() - prev_time
            
    # === 4. AutoUP ===
    print("--- Step 4: Running AutoUP ---")
    print(f"Output directory for AutoUP results: {output_dir}")
    results = [] # Results for future merger expansion
    autoup = AutoUPWrapper(autoup_root)
    
    # for selected_func in uncached_funcs:
    #     start_time = time.time()
    #     success, message = autoup.run(selected_func, output_dir)
        
    #     result = {
    #         "name": selected_func['name'],
    #         "success": success,
    #         "message": message,
    #         "artifacts_path": str(output_dir / selected_func['name']),
    #         "runtime": time.time() - start_time
    #     }
    #     results.append(result)
    #     print(f"Completed: {result['name']}")

    timings['autoup_time'] = time.time() - prev_time
    prev_time = time.time()

        
    # 6. Review
    print("--- Step 6: Validating results ---")
    success, message = autoup.review(output_dir, project_root=root_dir)
    if not success:
        print(f"AutoUP review failed: {message}")
        return (1)
    
    timings['validation_time'] = time.time() - prev_time
    prev_time = time.time()

    print("--- Step 7: Merging reports ---")
    merger = ViolationAssessmentReport(output_dir / "violation_assessments.json",output_dir / "final_report.html")
    output = merger.generate()
    print(f"Violation assessment report generated at: {output}")
    timings['report_merging_time'] = time.time() - prev_time
    
    print("--- LAFVT Execution Complete ---")

    # === Timing Analytics ===
    # build print string for terminal
    ps = "\n--- Timing Analytics ---\n"
    
    # Function Extraction Time
    ps += f"==> Function Extraction and Analysis Time:\n"
    ps += f"\t Total Time: {timings['extraction_time']:.2f} seconds.\n"
    ps += f"\t Extracted and analyzed {len(functions)} functions.\n"
    ps += f"\t Average time per function: {timings['extraction_time'] / len(functions):.2f} seconds.\n"
    
    # Function Selection Time
    ps += f"==> Function Selection Time:\n"
    ps += f"\t Total Time: {timings['selection_time']:.2f} seconds.\n"
    ps += f"\t Selected {len(selected_funcs)} functions from {len(functions)} total functions.\n"
    ps += f"\t Average time per function: {timings['selection_time'] / len(selected_funcs):.2f} seconds.\n"
    
    # Cache Checking Time
    ps += f"==> Cache Checking Time:\n"
    ps += f"\t Total Time: {timings['cache_checking_time']:.2f} seconds.\n"
    ps += f"\t {len(uncached_funcs)} functions uncached.\n"
    ps += f"\t Average time per function: {timings['cache_checking_time'] / len(uncached_funcs) if uncached_funcs else 0:.2f} seconds.\n"
    
    # AutoUp time
    ps += f"==> AutoUP Execution Time:\n"
    ps += f"\t Total Time: {timings['autoup_time']:.2f} seconds.\n"
    ps += f"\t Total functions processed: {len(uncached_funcs)}\n"
    total_lines = sum([abs(uncached_func['end_line'] - uncached_func['start_line']) for uncached_func in uncached_funcs])
    ps += f"\t Total Lines of Code: {total_lines}\n"
    ps += f"\t Average time per function: {timings['autoup_time'] / len(uncached_funcs) if uncached_funcs else 0:.2f} seconds.\n"
    ps += f"\t Average time per line of code: {timings['autoup_time'] / total_lines if total_lines > 0 else 0:.2f} seconds.\n"
    
    # Report Merging Time
    ps += f"==> Report Merging Time:\n"
    ps += f"\t Total Time: {timings['report_merging_time']:.2f} seconds.\n"
    
    # Validation Time
    ps += f"==> Validation Time:\n"
    ps += f"\t Total Time: {timings['validation_time']:.2f} seconds.\n"
    
    print(ps)
    
    # Save timing data as JSON
    timing_data = {
        "timestamp": time.time(),
        "extraction": {
            "total_time": timings['extraction_time'],
            "functions_extracted": len(functions),
            "avg_time_per_function": timings['extraction_time'] / len(functions) if functions else 0
        },
        "selection": {
            "total_time": timings['selection_time'],
            "functions_selected": len(selected_funcs),
            "functions_total": len(functions),
            "avg_time_per_function": timings['selection_time'] / len(selected_funcs) if selected_funcs else 0
        },
        "cache_checking": {
            "total_time": timings['cache_checking_time'],
            "uncached_functions": len(uncached_funcs),
            "avg_time_per_function": timings['cache_checking_time'] / len(uncached_funcs) if uncached_funcs else 0
        },
        "autoup": {
            "total_time": timings['autoup_time'],
            "functions_processed": len(uncached_funcs),
            "total_lines_of_code": total_lines,
            "avg_time_per_function": timings['autoup_time'] / len(uncached_funcs) if uncached_funcs else 0,
            "avg_time_per_line": timings['autoup_time'] / total_lines if total_lines > 0 else 0
        },
        "report_merging": {
            "total_time": timings['report_merging_time']
        },
        "validation": {
            "total_time": timings['validation_time']
        }
    }
    
    timing_file = output_dir / "timing_data.json"
    with open(timing_file, 'w') as f:
        json.dump(timing_data, f, indent=2)
    print(f"Timing data saved to {timing_file}")


if __name__ == "__main__":
    main()
