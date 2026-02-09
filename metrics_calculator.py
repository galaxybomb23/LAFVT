import os
import re
import json
import argparse
from datetime import datetime
from collections import defaultdict

def parse_log_file(log_file_path):
    tokens_per_agent = defaultdict(int)
    total_tokens = 0
    agent_times = defaultdict(float)
    total_agent_time = 0
    harness_path = None

    # Patterns
    timestamp_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")
    harness_path_pattern = re.compile(r"Harness path: (.+)")
    agent_start_pattern = re.compile(r"Agent '(.+?)':")
    # Updated Pattern for execution logs
    agent_exec_pattern = re.compile(r"Agent '(.+?)' (succeed|failed)")
    
    # State tracking
    in_metrics_summary = False
    current_metrics_agent = None
    json_buffer = []
    in_json = False

    first_log_time = None
    last_agent_finish_time = None

    try:
        with open(log_file_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Error reading {log_file_path}: {e}")
        return None

    def clean_log_line(line):
        # Removes the timestamp and log level info to get the actual message
        match = re.search(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \[.*?\] \(.*?\) (.*)$", line)
        if match:
            return match.group(1)
        return line.strip()

    # Tool Execution Time 
    
    last_ts = None

    for line in lines:
        clean_content = clean_log_line(line)
        
        # timestamp parsing
        ts_match = timestamp_pattern.search(line)
        current_ts = None
        if ts_match:
            current_ts = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S,%f")
            if first_log_time is None:
                first_log_time = current_ts

        # Harness Path
        if "Harness path: " in clean_content:
             match = harness_path_pattern.search(clean_content)
             if match:
                 harness_path = match.group(1).strip()

        if "===== Metrics Summary per Agent =====" in clean_content:
            in_metrics_summary = True
            continue
        
        if not in_metrics_summary:
            # Track Last Agent Finish Time
            exec_match = agent_exec_pattern.search(clean_content)
            if exec_match and current_ts:
                last_agent_finish_time = current_ts

        if in_metrics_summary:
            agent_match = agent_start_pattern.search(clean_content)
            if agent_match:
                current_metrics_agent = agent_match.group(1)
                in_json = False
                json_buffer = []
                continue
            
            if current_metrics_agent:
                if clean_content.strip() == "{":
                    in_json = True
                    json_buffer.append("{")
                    continue
                elif in_json:
                    json_buffer.append(clean_content)
                    if clean_content.strip() == "}":
                        in_json = False
                        try:
                            json_str = " ".join(json_buffer)
                            data = json.loads(json_str)
                            
                            # Logic derived from metric_summary.py in AutoUP:
                            # Total Attempts = (Num Successful Attempts) + (Num Failed Attempts)
                            # Num Successful Attempts = num_resolved
                            # Num Failed Attempts = Sum of error counts
                            # Total Tokens = Total Attempts * Avg Tokens per Attempt
                            
                            avg_tokens = data.get("avg_tokens_per_attempt", 0)
                            num_resolved = data.get("num_resolved", 0)
                            
                            sum_errors = 0
                            error_causes = data.get("attempt_error_causes", [])
                            for cause in error_causes:
                                parts = cause.split(":")
                                if len(parts) >= 2:
                                    try:
                                        sum_errors += float(parts[-1])
                                    except:
                                        pass
                            
                            attempts_count = sum_errors + num_resolved
                            
                            if attempts_count == 0 and avg_tokens > 0:
                                 attempts_count = 1
                            
                            agent_total = avg_tokens * attempts_count
                            tokens_per_agent[current_metrics_agent] = agent_total
                            total_tokens += agent_total
                        except json.JSONDecodeError:
                            pass
                        current_metrics_agent = None



    # Calculate Total Time
    total_time = 0
    if first_log_time and last_agent_finish_time:
        total_time = (last_agent_finish_time - first_log_time).total_seconds()

    return {
        "tokens_per_agent": dict(tokens_per_agent),
        "total_tokens": total_tokens,
        "total_time_seconds": total_time,
        "harness_path_log": harness_path
    }

def count_loc(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            return len(f.readlines())
    except:
        return 0

def main():
    parser = argparse.ArgumentParser(description="Calculate metrics from logs.")
    parser.add_argument("input_dir", help="Directory containing log files and harnesses.")
    args = parser.parse_args()

    input_dir = args.input_dir

    # Create LAFVT_metrics directory and reports subdirectory
    metrics_dir = os.path.join(input_dir, "LAFVT_metrics")
    reports_dir = os.path.join(metrics_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    
    global_total_time = 0.0
    global_total_tokens = 0.0
    processed_count = 0

    print(f"Reading logs from: {input_dir}")
    print(f"Saving reports to: {reports_dir}")

    for filename in os.listdir(input_dir):
        if filename.endswith(".log"):
            log_path = os.path.join(input_dir, filename)
            
            try:
                metrics = parse_log_file(log_path)
                if metrics:
                    # Resolve harness path
                    harness_path = "Unknown"
                    harness_log_path = metrics.get('harness_path_log')
                    if harness_log_path:
                        if os.path.exists(harness_log_path):
                             harness_path = harness_log_path
                        else:
                             harness_path = harness_log_path
                   
                    report_data = {
                        "log_file": filename,
                        "harness_path": harness_path,
                        "total_time_seconds": metrics.get('total_time_seconds', 0),
                        "total_tokens": metrics.get('total_tokens', 0),
                        "tokens_per_agent": metrics.get('tokens_per_agent', {})
                    }
                    
                    # Accumulate globals
                    global_total_time += metrics.get('total_time_seconds', 0)
                    global_total_tokens += metrics.get('total_tokens', 0)
                    processed_count += 1
                    
                    report_filename = f"{filename}_report.json"
                    report_path = os.path.join(reports_dir, report_filename)
                    
                    with open(report_path, 'w', encoding='utf-8') as f:
                        json.dump(report_data, f, indent=4)
                    
                    print(f"Generated report for {filename}")
            except Exception as e:
                print(f"Failed to process {filename}: {e}")

    # Write Global Summary
    summary_data = {
        "total_functions_processed": processed_count,
        "codebase_total_execution_time": global_total_time,
        "codebase_total_token_usage": global_total_tokens
    }
    
    summary_path = os.path.join(metrics_dir, "codebase_summary.json")
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary_data, f, indent=4)
    
    print(f"Codebase summary written to: {summary_path}")

if __name__ == "__main__":
    main()
