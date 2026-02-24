#!/usr/bin/env python3
"""
AutoUPWrapper
=============
Thin facade over the AutoUP formal-verification tool.

Responsibilities
----------------
- Set the subprocess working directory to ``autoup_root`` (required for
  AutoUP's relative container/makefile paths).
- Construct the ``run.py`` CLI command for each function.
- Expose ``run_parallel()`` for j-concurrent proof workers backed by
  ``concurrent.futures.ThreadPoolExecutor``.
- Expose ``review()`` to invoke AutoUP's aggregation / violation-review mode.

This wrapper is intentionally kept simple so that ``run`` and ``review``
can evolve independently of the LAFVT orchestrator (e.g., adding Apptainer
support, remote execution, or retry logic).
"""

from __future__ import annotations

import csv
import logging
import subprocess
import sys
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Tuple

log = logging.getLogger(__name__)


class AutoUPWrapper:
    """
    Manages subprocess invocations of AutoUP's ``run.py``.

    Parameters
    ----------
    autoup_root:
        Filesystem path to the root of the AutoUP repository
        (the directory that contains ``src/run.py``).
    """

    def __init__(self, autoup_root: Path) -> None:
        self.autoup_root = Path(autoup_root)
        self.run_script = self.autoup_root / "src" / "run.py"
        # Thread-safe registry of active Popen objects; used by cancel_all().
        self._active_procs: set = set()
        self._procs_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Single-function proof
    # ------------------------------------------------------------------

    def run(
        self,
        function_data: Dict,
        output_dir: Path,
        project_root: Path,
        llm_model: str = "gpt-5.2",
    ) -> Tuple[bool, str]:
        """
        Run AutoUP ``all`` mode for a single function.

        Parameters
        ----------
        function_data:
            Dict with at least ``filepath`` and ``function_name`` keys.
        output_dir:
            Root output directory.  Must be a subdirectory of
            ``project_root`` (Docker/Apptainer volume-mount requirement).
        project_root:
            Root of the C/C++ project being verified.
        llm_model:
            LLM model name forwarded to AutoUP agents.

        Returns
        -------
        (success, message)
        """
        func_name: str = function_data["function_name"]
        file_path = Path(function_data["filepath"])
        file_slug = file_path.stem

        harness_path = output_dir / file_slug / func_name
        harness_path.mkdir(parents=True, exist_ok=True)

        log_file = harness_path / "autoup_log.log"
        metrics_file = harness_path / "autoup_metrics.jsonl"

        cmd: List[str] = [
            str(sys.executable),
            str(self.run_script),
            "all",
            "--target_function_name", func_name,
            "--root_dir", str(project_root),
            "--harness_path", str(harness_path),
            "--target_file_path", str(file_path),
            "--log_file", str(log_file),
            "--metrics_file", str(metrics_file),
            "--llm_model", llm_model,
        ]

        log.info("Submitting AutoUP for %s (%s)", func_name, file_path)
        log.debug("Command: %s", " ".join(cmd))

        try:
            exec_log = harness_path / "execution.log"
            with exec_log.open("w", encoding="utf-8") as fh:
                proc = subprocess.Popen(
                    cmd,
                    cwd=self.autoup_root,   # AutoUP requires its own root as cwd
                    stdout=fh,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            with self._procs_lock:
                self._active_procs.add(proc)
            try:
                returncode = proc.wait()
            finally:
                with self._procs_lock:
                    self._active_procs.discard(proc)
            if returncode == 0:
                msg = f"AutoUP succeeded for {func_name}"
                log.info(msg)
                return True, msg
            else:
                msg = f"AutoUP failed for {func_name} (exit {returncode})"
                log.warning(msg)
                return False, msg
        except Exception as exc:
            msg = f"AutoUP execution error for {func_name}: {exc}"
            log.error(msg)
            return False, msg

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------

    def cancel_all(self) -> None:
        """
        Terminate every AutoUP subprocess currently tracked by this wrapper.

        Sends ``SIGTERM`` to each process, waits up to 5 seconds for a clean
        exit, then escalates to ``SIGKILL`` for any that are still running.
        Safe to call from any thread.
        """
        with self._procs_lock:
            procs = list(self._active_procs)
        if not procs:
            return
        log.warning("Terminating %d active AutoUP process(es)...", len(procs))
        for proc in procs:
            try:
                proc.terminate()
            except OSError:
                pass
        for proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                log.warning("Process %d did not exit cleanly — killing.", proc.pid)
                proc.kill()
        log.info("All AutoUP processes terminated.")

    # ------------------------------------------------------------------
    # Parallel proof (j workers)
    # ------------------------------------------------------------------

    def run_parallel(
        self,
        manifest_csv: Path,
        output_dir: Path,
        project_root: Path,
        llm_model: str = "gpt-5.2",
        j: int = 10,
    ) -> Dict[str, Tuple[bool, str]]:
        """
        Run AutoUP on all functions listed in ``manifest_csv`` using up to
        ``j`` concurrent worker threads.

        Parameters
        ----------
        manifest_csv:
            Path to ``analysis_manifest.csv`` with columns
            ``filepath`` and ``function_name``.
        output_dir:
            Root output directory (must be inside ``project_root``).
        project_root:
            Root of the C/C++ project.
        llm_model:
            LLM model name forwarded to each AutoUP worker.
        j:
            Maximum number of parallel worker threads.

        Returns
        -------
        dict mapping ``function_name`` → ``(success, message)``
        """
        # Load manifest
        functions: List[Dict] = []
        with manifest_csv.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                functions.append(dict(row))

        if not functions:
            log.warning("analysis_manifest.csv is empty — nothing to prove.")
            return {}

        log.info(
            "Starting parallel proof: %d function(s), %d worker(s), model=%s",
            len(functions), j, llm_model,
        )

        results: Dict[str, Tuple[bool, str]] = {}

        try:
            with ThreadPoolExecutor(max_workers=j) as pool:
                future_to_func = {
                    pool.submit(self.run, func, output_dir, project_root, llm_model): func
                    for func in functions
                }
                for future in as_completed(future_to_func):
                    func = future_to_func[future]
                    name = func["function_name"]
                    try:
                        ok, msg = future.result()
                    except Exception as exc:
                        ok, msg = False, f"Unexpected worker exception: {exc}"
                        log.error("Worker for %s raised: %s", name, exc)
                    results[name] = (ok, msg)
        except KeyboardInterrupt:
            log.warning(
                "Keyboard interrupt — terminating %d active AutoUP worker(s)...",
                len(self._active_procs),
            )
            self.cancel_all()
            raise

        succeeded = sum(1 for ok, _ in results.values() if ok)
        log.info(
            "Parallel proof complete: %d/%d succeeded.",
            succeeded, len(results),
        )
        return results

    # ------------------------------------------------------------------
    # Review (aggregation)
    # ------------------------------------------------------------------

    def review(
        self,
        output_dir: Path,
        project_root: Path,
    ) -> Tuple[bool, str]:
        """
        Run AutoUP in ``review`` mode to aggregate proof results.

        Parameters
        ----------
        output_dir:
            The ``lafvt_output/`` directory populated by :meth:`run_parallel`.
        project_root:
            Root of the C/C++ project.

        Returns
        -------
        (success, message)
        """
        log_file = output_dir / "review_log.log"
        metrics_file = output_dir / "review_metrics.jsonl"

        cmd: List[str] = [
            str(sys.executable),
            str(self.run_script),
            "review",
            "--harness_path", str(output_dir),
            "--log_file", str(log_file),
            "--metrics_file", str(metrics_file),
            # run.py requires these even in review mode; supply safe placeholders
            "--target_function_name", "none",
            "--root_dir", str(project_root),
            "--target_file_path", "none",
        ]

        log.info("Running AutoUP review on %s", output_dir)
        log.debug("Command: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                cwd=self.autoup_root,
                text=True,
            )
            if result.returncode == 0:
                msg = f"AutoUP review succeeded for {output_dir}"
                log.info(msg)
                return True, msg
            else:
                msg = f"AutoUP review failed (exit {result.returncode})"
                log.error(msg)
                return False, msg
        except Exception as exc:
            msg = f"AutoUP review error: {exc}"
            log.error(msg)
            return False, msg


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------
# Run individual stages without going through the full LAFVT orchestrator:
#
#   Proof (parallel):
#     python src/autoup_wrapper.py proof \
#         --manifest_csv  <path/to/analysis_manifest.csv> \
#         --output_dir    <path/to/lafvt_output> \
#         --project_root  <path/to/project> \
#         [--autoup_root  <path/to/AutoUP>] \
#         [--llm_model    gpt-5.2] \
#         [--j            10]
#
#   Review:
#     python src/autoup_wrapper.py review \
#         --output_dir   <path/to/lafvt_output> \
#         --project_root <path/to/project> \
#         [--autoup_root <path/to/AutoUP>]
#
#   All (proof then review):
#     python src/autoup_wrapper.py all \
#         --manifest_csv  <path/to/analysis_manifest.csv> \
#         --output_dir    <path/to/lafvt_output> \
#         --project_root  <path/to/project> \
#         [--autoup_root  <path/to/AutoUP>] \
#         [--llm_model    gpt-5.2] \
#         [--j            10]
# ---------------------------------------------------------------------------

def _build_standalone_parser() -> argparse.ArgumentParser:
    import argparse

    # AutoUP lives two levels above this file: <repo>/AutoUP
    _default_autoup = Path(__file__).resolve().parent.parent / "AutoUP"

    parser = argparse.ArgumentParser(
        prog="autoup_wrapper",
        description="Run AutoUP proof, review, or both stages standalone.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    # ── shared argument factory ───────────────────────────────────────────────
    def _add_proof_args(p: argparse.ArgumentParser) -> None:
        """Add all proof-related arguments to a subparser."""
        p.add_argument(
            "--manifest_csv", required=True,
            help="Path to analysis_manifest.csv (columns: filepath, function_name).",
        )
        p.add_argument(
            "--output_dir", required=True,
            help="Root output directory (must be inside project_root).",
        )
        p.add_argument(
            "--project_root", required=True,
            help="Root of the C/C++ project being verified.",
        )
        p.add_argument(
            "--autoup_root", default=str(_default_autoup),
            help="Path to the AutoUP repository root.",
        )
        p.add_argument("--llm_model", default="gpt-5.2", help="LLM model for AutoUP agents.")
        p.add_argument("--j", type=int, default=10, metavar="N",
                       help="Maximum parallel worker threads.")

    def _add_review_args(p: argparse.ArgumentParser) -> None:
        """Add all review-related arguments to a subparser."""
        p.add_argument(
            "--output_dir", required=True,
            help="lafvt_output directory populated by a previous proof run.",
        )
        p.add_argument(
            "--project_root", required=True,
            help="Root of the C/C++ project.",
        )
        p.add_argument(
            "--autoup_root", default=str(_default_autoup),
            help="Path to the AutoUP repository root.",
        )

    # ── proof ────────────────────────────────────────────────────────────────
    proof_p = sub.add_parser(
        "proof",
        help="Run AutoUP in parallel proof mode on all functions in a manifest CSV.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _add_proof_args(proof_p)

    # ── review ───────────────────────────────────────────────────────────────
    review_p = sub.add_parser(
        "review",
        help="Run AutoUP review mode to aggregate proof results.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _add_review_args(review_p)

    # ── all (proof then review) ───────────────────────────────────────────────
    all_p = sub.add_parser(
        "all",
        help="Run proof then review in sequence.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _add_proof_args(all_p)

    return parser


def main() -> int:
    import argparse
    import logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = _build_standalone_parser()
    args = parser.parse_args()

    wrapper = AutoUPWrapper(Path(args.autoup_root))
    output_dir = Path(args.output_dir).resolve()
    project_root = Path(args.project_root).resolve()

    try:
        output_dir.relative_to(project_root)
    except ValueError:
        log.error(
            "output_dir '%s' is not a subdirectory of project_root '%s'. "
            "AutoUP requires the output directory to be inside the project root "
            "(Docker/Apptainer volume-mount constraint).",
            output_dir, project_root,
        )
        return 1

    if args.mode in ("proof", "all"):
        manifest_csv = Path(args.manifest_csv).resolve()
        if not manifest_csv.exists():
            log.error("manifest_csv not found: %s", manifest_csv)
            return 1
        results = wrapper.run_parallel(
            manifest_csv=manifest_csv,
            output_dir=output_dir,
            project_root=project_root,
            llm_model=args.llm_model,
            j=args.j,
        )
        succeeded = sum(1 for ok, _ in results.values() if ok)
        failed = len(results) - succeeded
        print(f"\nProof complete: {succeeded}/{len(results)} succeeded.")
        if args.mode == "proof":
            return 0 if failed == 0 else 1

    if args.mode in ("review", "all"):
        ok, msg = wrapper.review(output_dir=output_dir, project_root=project_root)
        if not ok:
            log.error(msg)
            return 1
        print(f"\nReview complete. Output in: {output_dir}")
        if args.mode == "all":
            return 0 if failed == 0 else 1
        return 0

    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
