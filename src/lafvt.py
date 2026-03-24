#!/usr/bin/env python3
"""
LAFVT — Lightweight Automated Function Verification Toolchain
=============================================================
Orchestrates six stages:
  1. Analyzer   — scan C/C++ source, rank functions by risk
  2. Proofer    — run AutoUP formal verification (j parallel workers)
  3. Review     — aggregate proof results via AutoUP review mode
  4. Report     — render an interactive HTML report
  5. Metrics    — compute cost and token metrics
  6. Server     — launch interactive report server with fix generation

Usage
-----
    python src/lafvt.py --project_dir /path/to/codebase [options]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import dotenv

# Internal modules (resolved relative to src/ on sys.path)
from analyzer import Analyzer
from autoup_wrapper import AutoUPWrapper
from metrics_calculator import MetricsCalculator
from report_generator import ViolationAssessmentReport

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# AutoUP lives at <repo_root>/AutoUP; this file lives at <repo_root>/src/lafvt.py
_REPO_ROOT = Path(__file__).resolve().parent.parent
_AUTOUP_ROOT = _REPO_ROOT / "AutoUP"

_DEFAULT_ALGORITHM = "lizard"
_DEFAULT_SELECTOR = "top_N"
_DEFAULT_LLM_MODEL = "gpt-5.2"
_DEFAULT_J = max(1, os.cpu_count() - 2) if os.cpu_count() else 1

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _setup_logging(log_file: Path) -> logging.Logger:
    """Configure root logger with a console handler and a file handler."""
    log_file.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console — INFO and above
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # File — DEBUG and above
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    return logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------

def _stage_banner(log: logging.Logger, n: int, title: str) -> None:
    log.info("=" * 60)
    log.info("  Stage %d: %s", n, title)
    log.info("=" * 60)


def _demo_pause(log: logging.Logger, stage_num: int, title: str, lines: list[str]) -> None:
    """Print a stage-completion summary and wait for the user to press Enter."""
    log.info("")
    log.info("─" * 60)
    log.info("  [DEMO] Stage %d complete: %s", stage_num, title)
    for line in lines:
        log.info("    %s", line)
    log.info("─" * 60)
    try:
        input("  Press Enter to continue to the next stage (Ctrl+C to abort)... ")
    except KeyboardInterrupt:
        log.info("Demo aborted by user.")
        raise SystemExit(0)
    log.info("")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LAFVT: Lightweight Automated Function Verification Toolchain",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--project_dir",
        required=True,
        help="Root directory of the C/C++ project to verify.",
    )
    parser.add_argument(
        "--algorithm",
        default=_DEFAULT_ALGORITHM,
        help="Static analysis algorithm (e.g. 'lizard', 'loc').",
    )
    parser.add_argument(
        "--selector",
        default=_DEFAULT_SELECTOR,
        help="Function selection strategy (e.g. 'top_N', 'top_risk').",
    )
    parser.add_argument(
        "--llm_model",
        default=_DEFAULT_LLM_MODEL,
        help="LLM model name forwarded to AutoUP agents.",
    )
    parser.add_argument(
        "--j",
        type=int,
        default=_DEFAULT_J,
        metavar="N",
        help="Maximum number of parallel AutoUP prover workers.",
    )
    parser.add_argument(
        "--target_directory",
        default=None,
        metavar="PATH",
        help=(
            "Subdirectory to analyze (must be inside --project_dir). "
            "Defaults to project_dir when omitted."
        ),
    )
    parser.add_argument(
        "--OPENAI_API_KEY",
        default=None,
        help="OpenAI API key (overrides .env and shell environment).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        default=False,
        help="Pause after each stage, print a brief summary, and prompt before continuing.",
    )
    parser.add_argument(
        "--skip-proof",
        dest="skip_proof",
        action="store_true",
        default=False,
        help=(
            "Skip the AutoUP proof stage (Stage 2) and proceed directly to Review "
            "and Report. Use when AutoUP has already been run and the output directory "
            "is populated."
        ),
    )
    parser.add_argument(
        "--skip-review",
        dest="skip_review",
        action="store_true",
        default=False,
        help=(
            "Skip the AutoUP review stage (Stage 3) and proceed directly to Report. "
            "Use when violation_assessments.json already exists in the output directory."
        ),
    )
    parser.add_argument(
        "--skip-metrics",
        dest="skip_metrics",
        action="store_true",
        default=False,
        help="Skip the metrics calculation stage (Stage 5).",
    )
    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    wall_start = time.perf_counter()
    timings: dict = {}

    # ── Parse args ──────────────────────────────────────────────────────────
    parser = _build_parser()
    args = parser.parse_args()

    project_dir = Path(args.project_dir).resolve()
    output_dir = project_dir / "lafvt_output"
    output_dir.mkdir(parents=True, exist_ok=True)

    log = _setup_logging(output_dir / "lafvt.log")
    log.info("LAFVT starting  (project_dir=%s)", project_dir)
    log.debug("AutoUP root: %s", _AUTOUP_ROOT)

    # ── Validate inputs ──────────────────────────────────────────────────────
    if not project_dir.is_dir():
        log.error("project_dir does not exist or is not a directory: %s", project_dir)
        return 1

    # Resolve target_directory (defaults to project_dir)
    if args.target_directory is not None:
        target_dir = Path(args.target_directory).resolve()
        if not target_dir.is_dir():
            log.error("target_directory does not exist or is not a directory: %s", target_dir)
            return 1
        try:
            target_dir.relative_to(project_dir)
        except ValueError:
            log.error(
                "target_directory '%s' is not a subdirectory of project_dir '%s'.",
                target_dir, project_dir,
            )
            return 1
        log.info("Analysis target restricted to: %s", target_dir)
    else:
        target_dir = project_dir
        log.debug("No target_directory specified; analyzing full project_dir.")

    # Resolve OPENAI_API_KEY: CLI arg > .env > shell env
    api_key = args.OPENAI_API_KEY
    if not api_key:
        env_file = _REPO_ROOT / ".env"
        api_key = dotenv.get_key(str(env_file), "OPENAI_API_KEY") if env_file.exists() else None
    if not api_key:
        api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        log.error(
            "No OPENAI_API_KEY found. "
            "Pass --OPENAI_API_KEY, set it in .env, or export it in your shell."
        )
        return 1
    os.environ["OPENAI_API_KEY"] = api_key

    if not _AUTOUP_ROOT.is_dir():
        log.error("AutoUP root not found at %s", _AUTOUP_ROOT)
        return 1

    log.info(
        "Configuration: algorithm=%s, selector=%s, llm_model=%s, j=%d",
        args.algorithm, args.selector, args.llm_model, args.j,
    )

    # ── Stage 1: Analyze ────────────────────────────────────────────────────
    _stage_banner(log, 1, "Analyzer")
    t0 = time.perf_counter()

    analyzer = Analyzer(
        project_root=output_dir,
        algorithm=args.algorithm,
        selector=args.selector,
    )
    analyzer.analyze(target_dir, output_dir=output_dir)
    analysis_df = analyzer.get_analysis_dataframe()

    if analysis_df is None or analysis_df.empty:
        log.error("No functions found in %s. Exiting.", project_dir)
        return 0

    num_found = len(analysis_df)
    log.info("Found %d functions.", num_found)

    # Stage 1b: Select — write directly to analysis_manifest.csv
    manifest_path = output_dir / "analysis_manifest.csv"
    selected_funcs = analyzer.select(N=10, output_path=manifest_path)
    if not selected_funcs:
        log.error("Selector returned no functions. Exiting.")
        return 0

    num_selected = len(selected_funcs)
    log.info(
        "Selected %d function(s): %s",
        num_selected,
        ", ".join(f["function_name"] for f in selected_funcs),
    )
    log.info("analysis_manifest.csv written to %s", manifest_path)

    timings["analysis"] = {
        "total_time_s": time.perf_counter() - t0,
        "functions_found": num_found,
        "functions_selected": num_selected,
        "avg_time_per_function_s": (time.perf_counter() - t0) / num_found if num_found else 0,
    }
    log.info("Stage 1 complete in %.2fs.", timings["analysis"]["total_time_s"])

    if args.demo:
        _demo_pause(log, 1, "Analyzer", [
            f"Analysis target    : {target_dir}",
            f"Functions found    : {num_found}",
            f"Functions selected : {num_selected}",
            f"Manifest           : {manifest_path}",
            f"Full analysis      : {output_dir / (args.algorithm + '_analysis.csv')}",
            f"Wall time          : {timings['analysis']['total_time_s']:.2f}s",
        ])

    # ── Stage 2: Proof ─────────────────────────────────────────────────────
    _stage_banner(log, 2, f"Proofer  (j={args.j} workers)")

    autoup = AutoUPWrapper(_AUTOUP_ROOT)

    if args.skip_proof:
        log.info("--skip-proof set: skipping AutoUP proof stage.")
        log.info("Assuming output directory already populated: %s", output_dir)
        proof_results: dict = {}
        succeeded = failed = 0
        proof_elapsed = 0.0
    else:
        t0 = time.perf_counter()
        try:
            proof_results = autoup.run_parallel(
                manifest_csv=manifest_path,
                output_dir=output_dir,
                project_root=project_dir,
                llm_model=args.llm_model,
                j=args.j,
            )
        except KeyboardInterrupt:
            log.info("")
            log.info("Keyboard interrupt — all AutoUP workers have been terminated. Exiting.")
            return 1
        succeeded = sum(1 for ok, _ in proof_results.values() if ok)
        failed = len(proof_results) - succeeded
        proof_elapsed = time.perf_counter() - t0

        for func_name, (ok, msg) in proof_results.items():
            level = logging.INFO if ok else logging.WARNING
            log.log(level, "  %-40s  %s", func_name, msg)

    timings["proof"] = {
        "total_time_s": proof_elapsed,
        "skipped": args.skip_proof,
        "functions_submitted": len(proof_results),
        "functions_succeeded": succeeded,
        "functions_failed": failed,
        "avg_time_per_function_s": proof_elapsed / len(proof_results) if proof_results else 0,
    }
    log.info(
        "Stage 2 complete in %.2fs. succeeded=%d  failed=%d%s",
        proof_elapsed, succeeded, failed,
        " (skipped)" if args.skip_proof else "",
    )

    if args.demo:
        _demo_pause(log, 2, "Proofer", [
            f"Skipped            : {args.skip_proof}",
            f"Workers used       : {args.j}",
            f"Submitted          : {len(proof_results)}",
            f"Succeeded          : {succeeded}",
            f"Failed             : {failed}",
            f"Artifacts dir      : {output_dir}",
            f"Wall time          : {proof_elapsed:.2f}s",
        ])

    # ── Stage 3: Review ─────────────────────────────────────────────────────
    _stage_banner(log, 3, "Review")

    if args.skip_review:
        log.info("--skip-review set: skipping AutoUP review stage.")
        log.info("Assuming violation_assessments.json already exists in: %s", output_dir)
        review_elapsed = 0.0
    else:
        t0 = time.perf_counter()
        ok, msg = autoup.review(output_dir=output_dir, project_root=project_dir)
        review_elapsed = time.perf_counter() - t0
        if not ok:
            log.error("AutoUP review failed: %s", msg)
            return 1

    timings["review"] = {"total_time_s": review_elapsed, "skipped": args.skip_review}
    log.info(
        "Stage 3 complete in %.2fs.%s",
        review_elapsed,
        " (skipped)" if args.skip_review else "",
    )

    if args.demo:
        _demo_pause(log, 3, "Review", [
            f"Skipped            : {args.skip_review}",
            f"Assessments JSON   : {output_dir / 'violation_assessments.json'}",
            f"Validation summary : {output_dir / 'validation_summary.json'}",
            f"Wall time          : {review_elapsed:.2f}s",
        ])

    # ── Stage 4: Report ─────────────────────────────────────────────────────
    _stage_banner(log, 4, "Report Generator")
    t0 = time.perf_counter()

    assessment_json = output_dir / "violation_assessments.json"
    report_html = output_dir / "final_report.html"

    report = ViolationAssessmentReport(
        assessment_json, report_html,
        project_dir=str(project_dir),
        model=args.llm_model
    )
    generated_path = report.generate()
    report_elapsed = time.perf_counter() - t0
    timings["report"] = {"total_time_s": report_elapsed}
    log.info("HTML report written to %s  (%.2fs)", generated_path, report_elapsed)

    if args.demo:
        _demo_pause(log, 4, "Report Generator", [
            f"HTML report        : {generated_path}",
            f"Wall time          : {report_elapsed:.2f}s",
        ])

    # ── Timing summary ───────────────────────────────────────────────────────
    total_elapsed = time.perf_counter() - wall_start
    timings["total_time_s"] = total_elapsed
    timings["j_workers"] = args.j
    timings["timestamp"] = time.time()

    timing_path = output_dir / "timing_data.json"
    timing_path.write_text(json.dumps(timings, indent=2), encoding="utf-8")

    log.info("")
    log.info("── Timing Summary ──────────────────────────────────────────")
    log.info("  Analysis  : %.2fs  (%d found, %d selected)",
             timings["analysis"]["total_time_s"],
             timings["analysis"]["functions_found"],
             timings["analysis"]["functions_selected"])
    log.info("  Proof     : %.2fs  (%d workers, %d ok / %d fail)",
             timings["proof"]["total_time_s"],
             args.j,
             timings["proof"]["functions_succeeded"],
             timings["proof"]["functions_failed"])
    log.info("  Review    : %.2fs", timings["review"]["total_time_s"])
    log.info("  Report    : %.2fs", timings["report"]["total_time_s"])
    log.info("  ─────────────────────────────────────────────────────────")
    log.info("  Total     : %.2fs", total_elapsed)
    log.info("  Timing data saved to %s", timing_path)
    log.info("")

    # ── Stage 5: Metrics ────────────────────────────────────────────────────
    _stage_banner(log, 5, "Metrics Calculator")
    t0 = time.perf_counter()

    if args.skip_metrics:
        log.info("--skip-metrics set: skipping metrics calculation.")
        timings["metrics"] = {"total_time_s": 0.0, "skipped": True}
    else:
        try:
            calculator = MetricsCalculator(
                output_dir=output_dir,
                llm_model=args.llm_model,
                source_dir=target_dir,
            )
            metrics_summary = calculator.calculate(codebase_name=project_dir.name)
            metrics_path = calculator.write_summary(
                output_dir / "LAFVT_metrics.json", metrics_summary
            )
            calculator.log_summary(metrics_summary)
            timings["metrics"] = {"total_time_s": time.perf_counter() - t0}
            log.info("Stage 5 complete in %.2fs → %s", timings["metrics"]["total_time_s"], metrics_path)
        except Exception as exc:
            log.warning("Metrics calculation failed (non-fatal): %s", exc)
            timings["metrics"] = {"total_time_s": 0.0, "error": str(exc)}

    if args.demo:
        _demo_pause(log, 5, "Metrics Calculator", [
            f"Metrics JSON       : {output_dir / 'LAFVT_metrics.json'}",
            f"Wall time          : {timings['metrics']['total_time_s']:.2f}s",
        ])

    log.info("LAFVT complete.  Report → %s", generated_path)

    # ── Stage 6: Interactive Report Server ─────────────────────────────────
    _stage_banner(log, 6, "Interactive Report Server")
    log.info("Launching local server for interactive fix generation...")

    server_cmd = [
        sys.executable, str(_REPO_ROOT / "src" / "server.py"),
        "--output_dir", str(output_dir),
        "--project_dir", str(project_dir),
        "--llm_model", args.llm_model,
        "--lafvt_log", str(output_dir / "lafvt.log"),
    ]

    server_proc = subprocess.Popen(server_cmd)
    log.info("Server started (PID %d). Opening browser...", server_proc.pid)

    # Give Flask a moment to start before opening the browser
    time.sleep(2)
    webbrowser.open("http://127.0.0.1:5000/")

    log.info("Press Ctrl+C or use the Stop Server button in the report to shut down.")
    try:
        server_proc.wait()
    except KeyboardInterrupt:
        log.info("Keyboard interrupt — stopping server.")
        server_proc.terminate()
        server_proc.wait(timeout=5)

    log.info("Server stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
