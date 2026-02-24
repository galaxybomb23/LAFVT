import os
import re
import json
import argparse
from datetime import datetime
from collections import defaultdict

# Pricing Configuration (choose with --model, default is gpt-5.2)
MODEL_PRICING = {
    "gpt-5.2-pro": {
        "input": 0.000021,   # $21.00 per 1M tokens
        "output": 0.000168,   # $168.00 per 1M tokens
        "cached": 0.0 # $0
    },
    "gpt-5.2": {
        "input": 0.00000175,   # $1.75 per 1M tokens
        "output": 0.000014,   # $14.00 per 1M tokens
        "cached": 0.000000175 # $0.175 per 1M tokens
    },
    "gpt-5.2-mini": {
        "input": 0.00000025,   # $0.25 per 1M tokens
        "output": 0.000002,   # $2.00 per 1M tokens
        "cached": 0.000000025 # $0.025 per 1M tokens
    },
    "gpt-4.1": {
        "input": 0.000003,   # $3.00 per 1M tokens
        "output": 0.000012,   # $12.00 per 1M tokens
        "cached": 0.00000075 # $0.75 per 1M tokens
    },
     "gpt-4.1-mini": {
        "input": 0.0000008, # $0.80 per 1M tokens
        "output": 0.0000032,  # $3.20 per 1M tokens 
        "cached": 0.00000020 # $0.20 per 1M tokens
    },
    "gpt-4.1-nano": {
        "input": 0.0000002, # $0.20 per 1M tokens
        "output": 0.0000008,  # $0.80 per 1M tokens 
        "cached": 0.00000005 # $0.05 per 1M tokens
    },
    "gpt-o4-mini": {
        "input": 0.000004, # $4.00 per 1M tokens
        "output": 0.000016,  # $16.00 per 1M tokens
        "cached": 0.000001 # $1.0 per 1M tokens

    },
}

def parse_metrics_file(log_file_path, pricing):
    """
    Parses the AutoUP log/metrics files to calculate metrics for LAFVT
    """
    token_stats = defaultdict(lambda: {
        "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "input_cost": 0.0, "cached_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0
    })
    total_tokens = 0
    harness_path = None
    
    first_ts = None
    last_ts = None
    
    input_price = pricing["input"]
    output_price = pricing["output"]
    cached_price = pricing.get("cached", 0.0)

    # 1. Determine JSONL path
    dirname, basename = os.path.split(log_file_path)
    if basename.endswith(".log"):
        core_name = basename[:-4]
        jsonl_filename = f"metrics-{core_name}.jsonl"
        jsonl_file_path = os.path.join(dirname, jsonl_filename)
        
        if os.path.exists(jsonl_file_path):
            try:
                with open(jsonl_file_path, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                            
                            # Track Timestamps
                            if "timestamp" in record:
                                try:
                                    ts = float(record["timestamp"])
                                    if first_ts is None or ts < first_ts:
                                        first_ts = ts
                                    if last_ts is None or ts > last_ts:
                                        last_ts = ts
                                except ValueError:
                                    pass
                            
                            # Token Usage from task_attempt
                            if record.get("type") == "task_attempt" and "llm_data" in record:
                                agent_name = record.get("agent_name", "UnknownAgent")
                                usage = record["llm_data"].get("token_usage", {})
                                
                                i_tokens = usage.get("input_tokens", 0)
                                c_tokens = usage.get("cached_tokens", 0)
                                o_tokens = usage.get("output_tokens", 0)
                                t_tokens = usage.get("total_tokens", 0)
                                
                                # Fallback if total is missing
                                if t_tokens == 0 and (i_tokens > 0 or o_tokens > 0):
                                    t_tokens = i_tokens + o_tokens
                                
                                # Costs
                                uncached_i_tokens = i_tokens - c_tokens if i_tokens >= c_tokens else 0
                                i_cost = uncached_i_tokens * input_price
                                c_cost = c_tokens * cached_price
                                o_cost = o_tokens * output_price
                                t_cost = i_cost + c_cost + o_cost
                                
                                token_stats[agent_name]["input_tokens"] += i_tokens
                                token_stats[agent_name]["cached_tokens"] += c_tokens
                                token_stats[agent_name]["output_tokens"] += o_tokens
                                token_stats[agent_name]["total_tokens"] += t_tokens
                                
                                token_stats[agent_name]["input_cost"] += i_cost
                                token_stats[agent_name]["cached_cost"] += c_cost
                                token_stats[agent_name]["output_cost"] += o_cost
                                token_stats[agent_name]["total_cost"] += t_cost
                                
                                total_tokens += t_tokens
                                
                        except (json.JSONDecodeError, ValueError):
                            pass
            except Exception as e:
                print(f"Error reading jsonl {jsonl_file_path}: {e}")

    # 2. Parse Log File for Harness Path
    harness_path_pattern = re.compile(r"Harness path: (.+)")
    
    try:
        with open(log_file_path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                if not harness_path:
                    match = harness_path_pattern.search(line)
                    if match:
                         harness_path = match.group(1).strip()
                         break 
    except Exception as e:
        print(f"Error reading log {log_file_path}: {e}")

    # Calculate Total Time from JSONL timestamps
    total_time = 0.0
    if first_ts and last_ts:
        total_time = last_ts - first_ts

    return {
        "metrics_per_agent": dict(token_stats),
        "total_tokens": total_tokens,
        "total_time_seconds": total_time,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "harness_path_log": harness_path
    }

def find_function_loc(source_dir, function_name):
    """
    Scans source_dir for C/C++ files and attempts to find the function definition
    to count lines of code.
    Heuristic: Looks for "types function_name(...){" structure.
    """
    if not source_dir or not os.path.exists(source_dir):
        return None

    # Common C/C++ extensions
    extensions = {".c", ".cpp", ".h", ".hpp", ".cc"}
    

    # Look for the function name followed eventually by {
    pattern = re.compile(rf"\b{re.escape(function_name)}\s*\([^;]*\)\s*\{{", re.MULTILINE | re.DOTALL)
    
    for root, dirs, files in os.walk(source_dir):
        for file in files:
            if any(file.endswith(ext) for ext in extensions):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        
                        match = pattern.search(content)
                        if match:
                            # Start counting braces from the opening brace of the function
                            start_index = match.end() - 1 # Should be the '{'
                            
                            brace_count = 1
                            lines = 0
                            
                            # Slice content from the opening brace
                            function_body = content[start_index:]
                            
                            # Count lines until braces balance
                            # This is a simple parser
                            for i, char in enumerate(function_body):
                                if char == '{':
                                    brace_count += 1
                                elif char == '}':
                                    brace_count -= 1
                                
                                if brace_count == 0:
                                    # Found end of function
                                    # Count newlines in this segment
                                    func_code = function_body[:i+1]
                                    lines = func_code.count('\n') + 1
                                    return lines
                                    
                except Exception:
                    continue
    return None

def main():
    parser = argparse.ArgumentParser(description="Calculate metrics from logs and jsonl files.")
    parser.add_argument("input_dir", help="Directory containing log files and jsonl metrics.")
    parser.add_argument("--model", default="gpt-5.2", help="AI model to use for pricing (default: gpt-5.2). Options: " + ", ".join(MODEL_PRICING.keys()))
    parser.add_argument("--source_dir", help="Optional: Path to source code directory to calculate per-function LOC.")
    args = parser.parse_args()

    input_dir = args.input_dir
    model_name = args.model.lower()
    source_dir = args.source_dir

    if model_name not in MODEL_PRICING:
        print(f"Error: Model '{model_name}' not found. Available models: {', '.join(MODEL_PRICING.keys())}")
        return

    pricing = MODEL_PRICING[model_name]
    print(f"Using pricing for model: {model_name} (Input: ${pricing['input']}/token, Output: ${pricing['output']}/token)")
    
    if source_dir:
        print(f"Source directory provided: {source_dir}. Will attempt to calculate LOC per function.")

    # Extract codebase name
    abs_input_dir = os.path.abspath(input_dir)
    dir_name = os.path.basename(os.path.normpath(abs_input_dir))
    
    # Try to extract from output-YYYY-MM-DD... format
    # Example: output-2026-01-24_16-32-18-RIOT -> RIOT
    match = re.match(r"^output-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-(.+)$", dir_name)
    if match:
        codebase_name = match.group(1)
    else:
        codebase_name = dir_name

    # Create LAFVT_metrics directory and reports subdirectory
    metrics_dir = os.path.join(input_dir, "LAFVT_metrics")
    reports_dir = os.path.join(metrics_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    
    global_total_time = 0.0
    global_first_ts = None
    global_last_ts = None
    
    global_total_tokens = 0.0
    global_input_tokens = 0.0
    global_cached_tokens = 0.0
    global_output_tokens = 0.0
    
    global_input_cost = 0.0
    global_cached_cost = 0.0
    global_output_cost = 0.0
    global_total_cost = 0.0
    
    global_loc = 0
    
    processed_count = 0

    print(f"Reading logs from: {input_dir}")
    print(f"Saving reports to: {reports_dir}")

    for filename in os.listdir(input_dir):
        if filename.endswith(".log"):
            log_path = os.path.join(input_dir, filename)
            
            try:
                metrics = parse_metrics_file(log_path, pricing)
                if metrics:
                    # Resolve harness path
                    harness_path = metrics.get('harness_path_log') or "Unknown"
                    
                    # Extract function name from filename
                    # Assumption: filename matches "module-function.log"
                    # We try to extract function name from the filename
                    # Remove .log extension
                    base = filename[:-4]
                    function_name = "Unknown"
                    if '-' in base:
                        parts = base.split('-')
                        function_name = parts[-1]

                    # Calculate LOC if source_dir is provided
                    lines_of_code = None
                    if source_dir:
                        if function_name != "Unknown":
                            lines_of_code = find_function_loc(source_dir, function_name)
                    
                    if lines_of_code is not None:
                        global_loc += lines_of_code

                    # Calculate local totals for this function
                    local_input_tokens = 0
                    local_cached_tokens = 0
                    local_output_tokens = 0
                    local_input_cost = 0.0
                    local_cached_cost = 0.0
                    local_output_cost = 0.0
                    local_total_cost = 0.0

                    for agent_stats in metrics.get('metrics_per_agent', {}).values():
                        # Local accumulation
                        local_input_tokens += int(agent_stats.get("input_tokens", 0))
                        local_cached_tokens += int(agent_stats.get("cached_tokens", 0))
                        local_output_tokens += int(agent_stats.get("output_tokens", 0))
                        local_input_cost += agent_stats.get("input_cost", 0.0)
                        local_cached_cost += agent_stats.get("cached_cost", 0.0)
                        local_output_cost += agent_stats.get("output_cost", 0.0)
                        local_total_cost += agent_stats.get("total_cost", 0.0)

                        # Global accumulation
                        global_input_tokens += int(agent_stats.get("input_tokens", 0))
                        global_cached_tokens += int(agent_stats.get("cached_tokens", 0))
                        global_output_tokens += int(agent_stats.get("output_tokens", 0))
                        
                        global_input_cost += agent_stats.get("input_cost", 0.0)
                        global_cached_cost += agent_stats.get("cached_cost", 0.0)
                        global_output_cost += agent_stats.get("output_cost", 0.0)
                        global_total_cost += agent_stats.get("total_cost", 0.0)

                    # Prepare report data
                    report_data = {
                        "function_name": function_name,
                        "harness_path": harness_path,
                        "lines_of_code": lines_of_code,
                        "serial_execution_time_seconds": metrics.get('total_time_seconds', 0),
                        "token_usage": {
                            "input_tokens": local_input_tokens - local_cached_tokens,
                            "cached_tokens": local_cached_tokens,
                            "output_tokens": local_output_tokens,
                            "total_tokens": metrics.get('total_tokens', 0),

                        },
                        "cost": {
                            "input_cost": local_input_cost,
                            "cached_cost": local_cached_cost,
                            "output_cost": local_output_cost,
                            "total_cost": local_total_cost,
                        },
                        "metrics_per_agent": metrics.get('metrics_per_agent', {})
                    }
                    
                    # Accumulate globals
                    global_total_time += metrics.get('total_time_seconds', 0)
                    global_total_tokens += metrics.get('total_tokens', 0)
                    
                    file_first_ts = metrics.get('first_ts')
                    if file_first_ts is not None:
                        if global_first_ts is None or file_first_ts < global_first_ts:
                            global_first_ts = file_first_ts
                            
                    file_last_ts = metrics.get('last_ts')
                    if file_last_ts is not None:
                        if global_last_ts is None or file_last_ts > global_last_ts:
                            global_last_ts = file_last_ts

                    processed_count += 1
                    
                    report_filename = f"{filename}_report.json"
                    report_path = os.path.join(reports_dir, report_filename)
                    
                    with open(report_path, 'w', encoding='utf-8') as f:
                        json.dump(report_data, f, indent=4)
                    
                    print(f"Generated report for {filename}")
            except Exception as e:
                print(f"Failed to process {filename}: {e}")

    # Write Global Summary
    cost_per_100_loc = None
    if global_loc > 0:
        cost_per_100_loc = (global_total_cost / global_loc) * 100

    summary_data = {
        "codebase_name": codebase_name,
        "total_functions_processed": processed_count,
        "metrics": {
            "real_execution_time_seconds": (global_last_ts - global_first_ts) if global_first_ts and global_last_ts else 0.0,
            "serial_execution_time_seconds": global_total_time,
            "total_lines_of_code": global_loc if source_dir else None,
            "token_usage": {
                "input_tokens": global_input_tokens - global_cached_tokens,
                "cached_tokens": global_cached_tokens,
                "output_tokens": global_output_tokens,
                "total_tokens": global_total_tokens,
            },
            "cost": {
                "input_cost": global_input_cost,
                "cached_cost": global_cached_cost,
                "output_cost": global_output_cost,
                "total_cost": global_total_cost,
                "cost_per_100_loc": cost_per_100_loc,
            }
            
        }
    }    

    summary_path = os.path.join(metrics_dir, "codebase_summary.json")
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary_data, f, indent=4)
    
    print(f"Codebase summary written to: {summary_path}")

if __name__ == "__main__":
    main()