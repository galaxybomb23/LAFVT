#!/usr/bin/env python3
"""
Fix Suggester
=============
Generate fix suggestions for bugs identified in violation assessments.

Usage:
    python src/fix_suggester.py --output_dir <path> --project_dir <path> --target_func <func> --target_precon <precon>
"""

import argparse
import json
import logging
import os
import re
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
from typing import Optional

# ---------------------------------------------------------------------------
# Output Schema
# ---------------------------------------------------------------------------

class FixSuggestion(BaseModel):
    is_fixable: bool = Field(description="Whether the bug can be reasonably fixed given the context.")
    explanation: str = Field(description="A brief explanation of why the proposed fix resolves the violation, or why it cannot be fixed.")
    suggested_code_diff: str = Field(description="The proposed code changes in standard diff format (or exactly what to replace). Leave empty if not fixable.")
    extra_changes_required: Optional[str] = Field(default=None, description="A text explanation of any extra changes required (e.g., updating header files or callers) to make the code compile.")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging(log_file: Path) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(fmt="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    
    logger = logging.getLogger("fix_suggester")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if not logger.handlers:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger

# ---------------------------------------------------------------------------
# LLM Wrapper
# ---------------------------------------------------------------------------

class SuggesterLLM:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self.url = "https://api.openai.com/v1/chat/completions"

    def generate_fix(self, prompt: str, target_func: str, logger: logging.Logger) -> tuple[FixSuggestion, dict]:
        system_msg_path = _REPO_ROOT / "prompts" / "fix_suggester_system.prompt"
        system_msg = system_msg_path.read_text(encoding="utf-8")
        
        headers = {
            "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY')}",
            "Content-Type": "application/json"
        }
        
        body = {
            "model": self.model_name,
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
        completion_detail = usage.get("completion_tokens_details", {})
        token_usage = {
            "input_tokens":     usage.get("prompt_tokens", 0),
            "cached_tokens":    usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
            "output_tokens":    usage.get("completion_tokens", 0),
            "reasoning_tokens": completion_detail.get("reasoning_tokens", 0),
            "total_tokens":     usage.get("total_tokens", 0),
        }
        
        logger.info("Target function [%s] token_usage: %s", target_func, json.dumps(token_usage))
        
        content = data["choices"][0]["message"]["content"]
        
        # Clean up possible markdown wrappers from the LLM response
        content = content.replace("```json", "").replace("```", "").strip()
        
        # Heuristic fix for LLMs that hallucinate string concatenation inside the JSON
        # It replaces occurrences like: " ... " + "\n" + " ... " with standard newlines inside a single string
        content = re.sub(r'"\s*\+\s*"\n"\s*\+\s*"', r'\\n', content)
        content = re.sub(r'"\s*\+\s*"', r'', content)
        
        try:
            parsed_json = json.loads(content)
            return FixSuggestion(**parsed_json), token_usage
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
            self.log.error("Failed to read file %s: %s", path, e)
            return ""

    def _extract_c_context(self, source_code: str, func_name: str, call_trace: str) -> str:
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

    def run(self, target_func: str, target_precon: str):
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
            self.log.error("Cannot find violation assessments file in %s", self.output_dir)
            return []

        self.log.info("Loading assessments from %s", assessments_file)
        with open(assessments_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assessments = data.get("Sorted Assessments", [])
        
        # Find the single target assessment
        target_assessments = [
            a for a in assessments 
            if a.get("Target Function") == target_func and a.get("Precondition") == target_precon
        ]
        
        if not target_assessments:
            self.log.error("Could not find assessment for function '%s' and precondition '%s'", target_func, target_precon)
            # Log all available functions and preconditions for debugging
            available = [(a.get("Target Function"), a.get("Precondition")) for a in assessments[:5]]
            self.log.error("Sample available assessments: %s", available)
            return []
            
        self.log.info("Found %d match(es): %s with precondition: %s", len(target_assessments), target_func, target_precon)

        results = []

        last_request_time = 0.0
        request_delay = 60.0 / 20.0  # 3 seconds per request

        for item in target_assessments:
            func_name = item.get("Target Function")
            original_source_path = item.get("Source File", "")
            precondition = item.get("Precondition", "")
            assessment_data = item.get("Violation Assessment", {})
            reasoning = assessment_data.get("Reasoning", "")

            self.log.info("Processing target function: %s", func_name)

            # Extract Call Trace
            llm_review = item.get("LLM Review", {})
            call_trace_list = llm_review.get("Call Trace", [])
            call_trace = "\n".join(call_trace_list) if call_trace_list else "Call trace not available."
            
            # Map source path — search for the file by name under project_dir
            source_content = "Source code not found."
            source_filename = Path(original_source_path).name  # e.g., "cache.c"
            local_matches = list(self.project_dir.rglob(source_filename))
            if local_matches:
                local_source_path = local_matches[0]  # Use first match
                if len(local_matches) > 1:
                    self.log.debug("Multiple matches for %s, using: %s", source_filename, local_source_path)
                full_source = self._read_file(local_source_path)
                source_content = self._extract_c_context(full_source, func_name, call_trace)
            else:
                self.log.warning("Could not find %s anywhere under %s", source_filename, self.project_dir)

            prompt = self._create_prompt(func_name, original_source_path, precondition, reasoning, source_content, call_trace)
            
            try:
                self.log.info("Requesting fix suggestion from LLM for %s...", func_name)
                
                # API Rate Limiter
                elapsed = time.time() - last_request_time
                if elapsed < request_delay:
                    time.sleep(request_delay - elapsed)
                
                suggestion, token_usage = self.suggester.generate_fix(prompt, func_name, self.log)
                last_request_time = time.time()
                
                result_item = {
                    "Target Function": func_name,
                    "Source File": original_source_path,
                    "Violated Precondition": precondition,
                    "Fix Suggestion": suggestion.model_dump(),
                    "Token Usage": token_usage,
                }
                results.append(result_item)
                self.log.info("Successfully generated suggestion for %s", func_name)

            except Exception as e:
                self.log.error("Failed to generate suggestion for %s: %s", func_name, e)

        # Save results to JSON (overwrite with current run only)
        out_file = self.fix_suggestions_dir / "fix_suggestions.json"
        
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=4)
        
        self.log.info("Saved %d fix suggestion(s) to %s", len(results), out_file)

        # Append to history log with timestamp
        history_file = self.fix_suggestions_dir / "fix_suggestions_history.jsonl"
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(history_file, "a", encoding="utf-8") as f:
            for result in results:
                entry = {"timestamp": timestamp, **result}
                f.write(json.dumps(entry) + "\n")
        
        self.log.info("Appended %d entry(ies) to %s", len(results), history_file)
        
        # Return the newly generated suggestions
        return results


def main():
    parser = argparse.ArgumentParser(description="Generate fix suggestions for confirmed bugs.")
    parser.add_argument("--output_dir", required=True, help="Path to the directory containing violation assessments and harnesses.")
    parser.add_argument("--project_dir", required=True, help="Root directory of the project (e.g., RIOT folder).")
    parser.add_argument("--target_func", required=True, help="Target function to generate a fix for.")
    parser.add_argument("--target_precon", required=True, help="Precondition that was violated.")
    parser.add_argument("--llm_model", default="gpt-5.2", help="LLM model to use (default: gpt-5.2).")
    parser.add_argument("--OPENAI_API_KEY", default=None, help="OpenAI API key.")
    
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    project_dir = Path(args.project_dir).resolve()

    fix_suggestions_dir = output_dir / "fix_suggestions"
    fix_suggestions_dir.mkdir(parents=True, exist_ok=True)
    log = _setup_logging(fix_suggestions_dir / "fix_suggester.log")

    # Handle API Key
    api_key = args.OPENAI_API_KEY
    if not api_key:
        env_file = _REPO_ROOT / ".env"
        api_key = dotenv.get_key(str(env_file), "OPENAI_API_KEY") if env_file.exists() else None
    if not api_key:
        api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        log.error("No OPENAI_API_KEY found in args, environment, or .env.")
        return 1
        
    os.environ["OPENAI_API_KEY"] = api_key

    suggester = FixSuggester(
        output_dir=output_dir, 
        project_dir=project_dir, 
        llm_model=args.llm_model, 
        log=log
    )
    suggester.run(target_func=args.target_func, target_precon=args.target_precon)

    return 0

if __name__ == "__main__":
    sys.exit(main())
