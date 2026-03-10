#!/usr/bin/env python3
"""
Fix Suggester
=============
Standalone script to generate fix suggestions for bugs identified in violation assessments.

Usage:
    python src/fix_suggester.py --output_dir <path> --project_dir <path> [--limit N]
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# Add src and AutoUP to path to use commons
_REPO_ROOT = Path(__file__).resolve().parent.parent
_AUTOUP_ROOT = _REPO_ROOT / "AutoUP"
sys.path.append(str(_REPO_ROOT / "src"))
sys.path.append(str(_AUTOUP_ROOT / "src"))

import dotenv
import requests
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Output Schema
# ---------------------------------------------------------------------------

class FixSuggestion(BaseModel):
    is_fixable: bool = Field(description="Whether the bug can be reasonably fixed given the context.")
    explanation: str = Field(description="A brief explanation of why the proposed fix resolves the violation, or why it cannot be fixed.")
    suggested_code_diff: str = Field(description="The proposed code changes in standard diff format (or exactly what to replace). Leave empty if not fixable.")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging(log_file: Path) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(fmt="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    return logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM Wrapper
# ---------------------------------------------------------------------------

class SuggesterLLM:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self.url = "https://genai.rcac.purdue.edu/api/chat/completions"

    def generate_fix(self, prompt: str, target_func: str, logger: logging.Logger) -> FixSuggestion:
        system_msg_path = _REPO_ROOT / "prompts" / "fix_suggester_system.prompt"
        system_msg = system_msg_path.read_text(encoding="utf-8")
        
        headers = {
            "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY')}",
            "Content-Type": "application/json"
        }
        
        body = {
            "model": self.model_name.replace("openai/", ""), # Strip openai/ if it's still there
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt}
            ],
            "stream": False
        }
        
        response = requests.post(self.url, headers=headers, json=body)
        if response.status_code != 200:
            raise RuntimeError(f"API Error {response.status_code}: {response.text}")
            
        data = response.json()
        
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", 0)
        
        logger.debug(f"Target function [{target_func}] Token Usage - Prompt: {prompt_tokens}, Completion: {completion_tokens}, Total: {total_tokens}")
        
        content = data["choices"][0]["message"]["content"]
        
        # Clean up possible markdown wrappers from the LLM response
        content = content.replace("```json", "").replace("```", "").strip()
        
        # Heuristic fix for LLMs that hallucinate string concatenation inside the JSON
        # It replaces occurrences like: " ... " + "\n" + " ... " with standard newlines inside a single string
        import re
        content = re.sub(r'"\s*\+\s*"\n"\s*\+\s*"', r'\\n', content)
        content = re.sub(r'"\s*\+\s*"', r'', content)
        
        try:
            parsed_json = json.loads(content)
            return FixSuggestion(**parsed_json)
        except Exception as e:
            raise RuntimeError(f"Failed to parse LLM response as JSON. Response: {content}\nError: {e}")

# ---------------------------------------------------------------------------
# Core Logic
# ---------------------------------------------------------------------------

class FixSuggester:
    def __init__(self, output_dir: Path, project_dir: Path, llm_model: str, log: logging.Logger):
        # Create the new fix_suggestions directory early
        fix_suggestions_dir = output_dir / "fix_suggestions"
        fix_suggestions_dir.mkdir(parents=True, exist_ok=True)
        self.fix_suggestions_dir = fix_suggestions_dir
        
        self.output_dir = output_dir
        self.project_dir = project_dir
        self.log = log
        self.suggester = SuggesterLLM(llm_model)

    def _read_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            self.log.error(f"Failed to read file {path}: {e}")
            return ""

    def _extract_c_context(self, source_code: str, func_name: str, call_trace: str) -> str:
        import re
        # Find the function signature heuristically
        pattern = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_\s\*]*\b' + re.escape(func_name) + r'\b\s*\([^)]*\)\s*\{', re.MULTILINE)
        match = pattern.search(source_code)
        if not match:
            # Fallback: maybe it's static or inline, relax the regex slightly
            pattern = re.compile(r'\b' + re.escape(func_name) + r'\b\s*\([^)]*\)\s*\{', re.MULTILINE)
            match = pattern.search(source_code)
            if not match:
                return source_code[:2000] + "\n... [TRUNCATED]" # Fallback to top of file if we can't find it
        
        start_idx = match.start()
        
        # Get the first 10 lines of the function (usually the signature and initial vars)
        func_start_lines = source_code[start_idx:].split('\n')[:10]
        func_signature = "\n".join(func_start_lines)
        
        # Now try to find the crash site line number from the call trace
        # The trace usually looks like: file.c:123 function_name
        crash_line = -1
        trace_lines = call_trace.split('\n')
        for t_line in trace_lines:
            # Look for file.c:line_num
            line_match = re.search(r':(\d+)\s+', t_line)
            if line_match:
                crash_line = int(line_match.group(1))
                break
                
        if crash_line == -1:
            # If we can't find the crash line, just return the first 50 lines of the function
            return "\n".join(source_code[start_idx:].split('\n')[:50]) + "\n... [TRUNCATED]"
            
        # Extract 10 lines above and 10 lines below the crash site
        all_lines = source_code.split('\n')
        start_crash = max(0, crash_line - 15)
        end_crash = min(len(all_lines), crash_line + 10)
        
        crash_context = "\n".join(all_lines[start_crash:end_crash])
        
        # Combine signature and crash site
        context = f"// Function Signature & Start:\n{func_signature}\n\n// ... [CODE OMITTED] ...\n\n// Crash Site Context:\n{crash_context}"
        return context



    def _create_prompt(self, target_function: str, source_file_path: str, precondition: str, reasoning: str, source_code: str, call_trace: str) -> str:
        prompt_template_path = _REPO_ROOT / "prompts" / "fix_suggester_user.prompt"
        prompt_template = prompt_template_path.read_text(encoding="utf-8")
        
        return prompt_template.format(
            target_function=target_function,
            source_file_path=source_file_path,
            precondition=precondition,
            reasoning=reasoning,
            source_code=source_code,
            call_trace=call_trace
        )

    def run(self, limit: int = None, min_threat: int = 1):
        import re
        # Attempt to extract the codebase name from the end of the output_dir folder name
        # e.g., output-2026-01-24_16-32-18-RIOT -> RIOT
        match = re.search(r'-([A-Za-z0-9_]+)$', self.output_dir.name)
        codebase_name = match.group(1) if match else "RIOT"
        
        assessments_file = self.output_dir / f"{codebase_name}-violation_assessments.json"
        
        # Try to find the generic violation_assessments.json if the specific one fails
        if not assessments_file.exists():
            for f in self.output_dir.glob("*violation_assessments.json"):
                assessments_file = f
                break

        if not assessments_file.exists():
            self.log.error(f"Cannot find violation assessments file in {self.output_dir}")
            return

        self.log.info(f"Loading assessments from {assessments_file}")
        with open(assessments_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assessments = data.get("Sorted Assessments", [])
        buggy_assessments = [a for a in assessments if a.get("LLM Review", {}).get("Threat Score", -1) >= min_threat]
        
        self.log.info(f"Found {len(buggy_assessments)} buggy assessments.")
        
        if limit:
            buggy_assessments = buggy_assessments[:limit]
            self.log.info(f"Limiting to {limit} assessments for this run.")

        results = []

        import time
        last_request_time = 0.0
        request_delay = 60.0 / 20.0  # 3 seconds per request


        for item in buggy_assessments:
            target_func = item.get("Target Function")
            original_source_path = item.get("Source File", "")
            precondition = item.get("Precondition", "")
            assessment_data = item.get("Violation Assessment", {})
            reasoning = assessment_data.get("Reasoning", "")

            self.log.info(f"Processing target function: {target_func}")


            # Extract Call Trace
            llm_review = item.get("LLM Review", {})
            call_trace_list = llm_review.get("Call Trace", [])
            call_trace = "\n".join(call_trace_list) if call_trace_list else "Call trace not available."
            
            # Map source path
            source_content = "Source code not found."
            # Attempt to strip the codebase name prefix from the absolute path to map it locally
            prefix = f"{codebase_name}/"
            if prefix in original_source_path:
                rel_path = original_source_path.split(prefix, 1)[-1]
                local_source_path = self.project_dir / rel_path
                if local_source_path.exists():
                    full_source = self._read_file(local_source_path)
                    source_content = self._extract_c_context(full_source, target_func, call_trace)
                else:
                    self.log.warning(f"Could not find local source for {original_source_path} at {local_source_path}")

            prompt = self._create_prompt(target_func, original_source_path, precondition, reasoning, source_content, call_trace)
            
            try:
                self.log.info(f"Requesting fix suggestion from LLM for {target_func}...")
                
                # API Rate Limiter
                elapsed = time.time() - last_request_time
                if elapsed < request_delay:
                    time.sleep(request_delay - elapsed)
                
                suggestion = self.suggester.generate_fix(prompt, target_func, self.log)
                last_request_time = time.time()
                
                result_item = {
                    "Target Function": target_func,
                    "Source File": original_source_path,
                    "Violated Precondition": precondition,
                    "Fix Suggestion": suggestion.model_dump()
                }
                results.append(result_item)
                self.log.info(f"Successfully generated suggestion for {target_func}")

            except Exception as e:
                self.log.error(f"Failed to generate suggestion for {target_func}: {e}")

        out_file = self.fix_suggestions_dir / "fix_suggestions.json"
        
        # Sort results to match the order in the original 'buggy_assessments' list
        # using the target function and source file to map them back
        ordered_results = []
        result_map = {(r["Target Function"], r["Source File"]): r for r in results}
        
        for item in buggy_assessments:
            key = (item.get("Target Function"), item.get("Source File", ""))
            if key in result_map:
                ordered_results.append(result_map[key])
        
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(ordered_results, f, indent=4)
        
        self.log.info(f"Saved {len(ordered_results)} ordered fix suggestions to {out_file}")


def main():
    parser = argparse.ArgumentParser(description="Generate fix suggestions for confirmed bugs.")
    parser.add_argument("--output_dir", required=True, help="Path to the directory containing violation assessments and harnesses.")
    parser.add_argument("--project_dir", required=True, help="Root directory of the project (e.g., RIOT folder).")
    parser.add_argument("--llm_model", default="llama4:latest", help="LLM model to use (default: llama4:latest on GenAI).")
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of suggestions to generate (for testing).")
    parser.add_argument("--min_threat", type=int, default=1, help="Minimum threat score to consider for generating fixes (default: 1).")
    parser.add_argument("--GENAI_API_KEY", default=None, help="Purdue GenAI API key.")
    
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    project_dir = Path(args.project_dir).resolve()

    fix_suggestions_dir = output_dir / "fix_suggestions"
    fix_suggestions_dir.mkdir(parents=True, exist_ok=True)
    log = _setup_logging(fix_suggestions_dir / "fix_suggester.log")

    # Handle API Key
    api_key = args.GENAI_API_KEY
    if not api_key:
        env_file = _REPO_ROOT / ".env"
        api_key = dotenv.get_key(str(env_file), "GENAI_API_KEY") if env_file.exists() else None
    if not api_key:
        api_key = os.getenv("GENAI_API_KEY")
    if not api_key:
        log.error("No GENAI_API_KEY found in args, environment, or .env.")
        return 1
        
    # LiteLLM's openai/ provider checks OPENAI_API_KEY, so we map it over
    os.environ["OPENAI_API_KEY"] = api_key

    suggester = FixSuggester(output_dir=output_dir, project_dir=project_dir, llm_model=args.llm_model, log=log)
    suggester.run(limit=args.limit, min_threat=args.min_threat)

    return 0

if __name__ == "__main__":
    sys.exit(main())
