#!/usr/bin/env python3
"""
Server
======
Local Flask server for the LAFVT interactive report.

Responsibilities
----------------
- Serve the violation assessment HTML report dynamically from the
  current ``violation_assessments.json``.
- Expose ``/api/suggest_fix`` for on-demand LLM fix generation via
  :class:`fix_suggester.FixSuggester`.
- Expose ``/api/shutdown`` for graceful server stop from the report UI.
- Write a PID file so external tools (``stop_server.py``) can also
  terminate the process.

Usage
-----
    python src/server.py --output_dir <path> --project_dir <path> [options]
"""

from __future__ import annotations

import argparse
import atexit
import logging
import os
import signal
import sys
from pathlib import Path

import dotenv
from flask import Flask, request, jsonify

# Add src to path to import local modules
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(_REPO_ROOT / "src"))

from report_generator import ViolationAssessmentReport

log = logging.getLogger(__name__)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Global configuration (populated on startup)
# ---------------------------------------------------------------------------

_config = {}

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Serve the interactive HTML report by generating it dynamically from the current JSON."""
    assessment_json = _config["output_dir"] / f"{_config['project_dir'].name}-violation_assessments.json"
    if not assessment_json.exists():
        # Fallback to wildcard
        for f in _config["output_dir"].glob("*violation_assessments.json"):
            assessment_json = f
            break
            
    # Generate the HTML dynamically into memory
    report = ViolationAssessmentReport(
        assessment_json, 
        assessment_json.with_name("temp.html"),
        project_dir=str(_config["project_dir"]),
        model=_config["llm_model"]
    )
    report.load()
    html_text = report._render_html()
    return html_text

@app.route("/api/suggest_fix", methods=["POST"])
def suggest_fix():
    """API endpoint to run the fix suggester for a specific function."""
    from fix_suggester import FixSuggester, _setup_logging
    
    req = request.get_json()
    target_func = req.get("target_func")
    target_precon = req.get("target_precon")
    project_dir_str = req.get("project_dir")
    project_dir = Path(project_dir_str) if project_dir_str else _config["project_dir"]
    llm_model = req.get("model") or _config["llm_model"]
    
    if not target_func:
        return jsonify({"error": "No target_func provided"}), 400
        
    fix_log = _setup_logging(_config["output_dir"] / "fix_suggestions" / "fix_suggester_server.log")
    
    suggester = FixSuggester(
        output_dir=_config["output_dir"], 
        project_dir=project_dir, 
        llm_model=llm_model, 
        log=fix_log
    )
    
    try:
        # Run just the specific function requested
        results = suggester.run(target_func=target_func, target_precon=target_precon)
        if not results:
            return jsonify({"error": "Failed to generate fix (maybe skipped due to threat score filtering?). Check server logs."}), 500
            
        return jsonify({"success": True, "result": results[0]["Fix Suggestion"]})
    except Exception as e:
        fix_log.exception("Error during suggest_fix for %s: %s", target_func, e)
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

@app.route("/api/shutdown", methods=["POST"])
def shutdown():
    """Gracefully shut down the server."""
    pid_file = _config.get("pid_file")
    if pid_file and pid_file.exists():
        pid_file.unlink(missing_ok=True)
    
    # Send response before shutting down
    func = request.environ.get('werkzeug.server.shutdown')
    if func:
        func()
        return jsonify({"success": True, "message": "Server shutting down..."})
    
    # Fallback for newer Werkzeug versions
    os.kill(os.getpid(), signal.SIGTERM)
    return jsonify({"success": True, "message": "Server shutting down..."})

# ---------------------------------------------------------------------------
# PID management
# ---------------------------------------------------------------------------

def _write_pid(pid_file: Path) -> None:
    pid_file.write_text(str(os.getpid()))
    atexit.register(lambda: pid_file.unlink(missing_ok=True))

# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Run LAFVT Fix Suggester Local Server")
    parser.add_argument("--output_dir", required=True, help="Path to the directory containing output items.")
    parser.add_argument("--project_dir", required=True, help="Root directory of the project.")
    parser.add_argument("--llm_model", default="gpt-5.2", help="LLM model to use (default: gpt-5.2).")
    parser.add_argument("--port", type=int, default=5000, help="Port to run the local server on.")
    parser.add_argument("--lafvt_log", default=None, help="Path to the main lafvt.log file (logs are appended).")
    
    args = parser.parse_args()
    
    _config["output_dir"] = Path(args.output_dir).resolve()
    _config["project_dir"] = Path(args.project_dir).resolve()

    # Also write all server logs to the main lafvt.log if path was provided
    if args.lafvt_log:
        fmt = logging.Formatter(
            fmt="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        lafvt_fh = logging.FileHandler(args.lafvt_log, mode="a", encoding="utf-8")
        lafvt_fh.setLevel(logging.DEBUG)
        lafvt_fh.setFormatter(fmt)
        logging.getLogger().addHandler(lafvt_fh)
    _config["llm_model"] = args.llm_model
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        env_file = _REPO_ROOT / ".env"
        api_key = dotenv.get_key(str(env_file), "OPENAI_API_KEY") if env_file.exists() else None
    
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
    else:
        log.warning("No OPENAI_API_KEY found. Fix generation API will likely fail.")

    pid_file = Path(args.output_dir) / "server.pid"
    _config["pid_file"] = pid_file
    _write_pid(pid_file)

    log.info("Starting local server at http://127.0.0.1:%d/", args.port)
    log.info("PID file written to: %s", pid_file)
    log.info("Press Ctrl+C or use the Stop Server button in the report to stop.")
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False, threaded=True)

    return 0

if __name__ == "__main__":
    sys.exit(main())
