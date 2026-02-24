#!/usr/bin/env python3
"""
MetricsCalculator
=================
Parses AutoUP per-function telemetry (``.jsonl`` files) inside any directory
and aggregates token usage, cost, and timing data across all proved functions.

File discovery uses a two-stage strategy:

1. **LAFVT-structured** (preferred) — looks for
   ``autoup_metrics.jsonl`` files exactly two levels deep::

       output_dir/<file_slug>/<function_name>/autoup_metrics.jsonl

2. **Recursive fallback** — if no structured files are found, every
   ``*.jsonl`` file under ``output_dir`` is collected (any depth).  The
   function name is derived from the file stem after stripping common
   prefixes (``metrics-``, ``autoup_``).  This handles ad-hoc output
   directories from standalone AutoUP runs.

Can be used programmatically (imported by ``lafvt.py``) or as a
standalone CLI::

    python src/metrics_calculator.py <output_dir> [--model gpt-5.2] \\
                                     [--source_dir /path/to/src]

Output
------
``LAFVT_metrics.json`` is written into the provided output directory and
contains both per-function breakdowns and a codebase-wide summary.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import lizard

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing table  (per-token cost in USD)
# ---------------------------------------------------------------------------

MODEL_PRICING: Dict[str, Dict[str, float]] = {
    "gpt-5.2-pro": {
        "input":  0.000021,      # $21.00 / 1M tokens
        "output": 0.000168,      # $168.00 / 1M tokens
        "cached": 0.0,
    },
    "gpt-5.2": {
        "input":  0.00000175,    # $1.75 / 1M tokens
        "output": 0.000014,      # $14.00 / 1M tokens
        "cached": 0.000000175,   # $0.175 / 1M tokens
    },
    "gpt-5.2-mini": {
        "input":  0.00000025,    # $0.25 / 1M tokens
        "output": 0.000002,      # $2.00 / 1M tokens
        "cached": 0.000000025,   # $0.025 / 1M tokens
    },
    "gpt-4.1": {
        "input":  0.000003,      # $3.00 / 1M tokens
        "output": 0.000012,      # $12.00 / 1M tokens
        "cached": 0.00000075,    # $0.75 / 1M tokens
    },
    "gpt-4.1-mini": {
        "input":  0.0000008,     # $0.80 / 1M tokens
        "output": 0.0000032,     # $3.20 / 1M tokens
        "cached": 0.00000020,    # $0.20 / 1M tokens
    },
    "gpt-4.1-nano": {
        "input":  0.0000002,     # $0.20 / 1M tokens
        "output": 0.0000008,     # $0.80 / 1M tokens
        "cached": 0.00000005,    # $0.05 / 1M tokens
    },
    "gpt-o4-mini": {
        "input":  0.000004,      # $4.00 / 1M tokens
        "output": 0.000016,      # $16.00 / 1M tokens
        "cached": 0.000001,      # $1.00 / 1M tokens
    },
}


# ---------------------------------------------------------------------------
# MetricsCalculator
# ---------------------------------------------------------------------------

class MetricsCalculator:
    """
    Parses AutoUP telemetry files inside a LAFVT output directory and
    aggregates per-function and codebase-wide metrics.

    The expected per-function directory layout (produced by
    ``AutoUPWrapper.run()``) is::

        output_dir/
            <file_slug>/
                <function_name>/
                    autoup_metrics.jsonl   ← parsed here
                    autoup_log.log
                    execution.log
                    violation.json
                    build/

    Parameters
    ----------
    output_dir:
        Path to the ``lafvt_output/`` directory produced by LAFVT.
    llm_model:
        Name of the LLM model used during proof; selects the pricing row.
    source_dir:
        Optional root of the C/C++ source tree.  When provided, LOC is
        estimated for each proved function via a brace-counting heuristic.
    """

    def __init__(
        self,
        output_dir: Path,
        llm_model: str = "gpt-5.2",
        source_dir: Optional[Path] = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.llm_model = llm_model.lower()
        self.source_dir = Path(source_dir) if source_dir else None

        if self.llm_model not in MODEL_PRICING:
            raise ValueError(
                f"Unknown LLM model '{self.llm_model}'. "
                f"Available: {', '.join(MODEL_PRICING)}"
            )
        self.pricing = MODEL_PRICING[self.llm_model]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate(self, codebase_name: Optional[str] = None) -> dict:
        """
        Discover all metrics files under ``output_dir``, parse each one,
        and return a structured summary dict.

        Discovery priority:

        1. LAFVT structure — ``<slug>/<func>/autoup_metrics.jsonl``
        2. Recursive fallback — every ``*.jsonl`` under ``output_dir``

        The dict schema::

            {
              "codebase_name": str,
              "model": str,
              "total_functions_processed": int,
              "functions": [ { per-function entry }, ... ],
              "metrics": {
                "real_execution_time_seconds": float,
                "serial_execution_time_seconds": float,
                "total_lines_of_code": int | null,
                "token_usage": { ... },
                "cost": { ... }
              }
            }
        """
        codebase_name = codebase_name or self.output_dir.parent.name

        functions: List[dict] = []
        global_first_ts: Optional[float] = None
        global_last_ts:  Optional[float] = None

        global_serial_time   = 0.0
        global_input_tokens  = 0
        global_cached_tokens = 0
        global_output_tokens = 0
        global_total_tokens  = 0
        global_input_cost    = 0.0
        global_cached_cost   = 0.0
        global_output_cost   = 0.0
        global_total_cost    = 0.0
        global_loc           = 0

        # Build function→source-file map from the manifest written by Stage 1.
        # When present this avoids a full source-tree walk for every function.
        manifest = self._load_manifest()

        for jsonl_path, func_name, file_slug in self._discover_jsonl_files():
            log.debug("Parsing metrics: %s", jsonl_path)
            func_metrics = self._parse_function_metrics(jsonl_path)

            # LOC resolution priority:
            #  1. Manifest (direct file path — always available in LAFVT runs)
            #  2. source_dir tree scan (non-LAFVT or relocated trees)
            #  3. None (no source information available)
            manifest_path = manifest.get(func_name)
            if manifest_path is not None and manifest_path.exists():
                loc = self._count_loc_in_file(manifest_path, func_name)
            elif self.source_dir:
                loc = self._find_function_loc(self.source_dir, func_name)
            else:
                loc = None

            # Aggregate tokens / costs across all agents for this function
            agents   = func_metrics["agents"]
            f_input  = sum(a["input_tokens"]  for a in agents.values())
            f_cached = sum(a["cached_tokens"] for a in agents.values())
            f_output = sum(a["output_tokens"] for a in agents.values())
            f_total  = sum(a["total_tokens"]  for a in agents.values())
            f_i_cost = sum(a["input_cost"]    for a in agents.values())
            f_c_cost = sum(a["cached_cost"]   for a in agents.values())
            f_o_cost = sum(a["output_cost"]   for a in agents.values())
            f_t_cost = sum(a["total_cost"]    for a in agents.values())

            functions.append({
                "function_name": func_name,
                "file_slug":     file_slug,
                "lines_of_code": loc,
                "serial_execution_time_seconds": func_metrics["total_time_seconds"],
                "token_usage": {
                    "input_tokens":  f_input - f_cached,
                    "cached_tokens": f_cached,
                    "output_tokens": f_output,
                    "total_tokens":  f_total,
                },
                "cost": {
                    "input_cost":  f_i_cost,
                    "cached_cost": f_c_cost,
                    "output_cost": f_o_cost,
                    "total_cost":  f_t_cost,
                },
                "metrics_per_agent": agents,
            })

            # Global accumulators
            if loc is not None:
                global_loc += loc
            global_serial_time   += func_metrics["total_time_seconds"]
            global_input_tokens  += f_input
            global_cached_tokens += f_cached
            global_output_tokens += f_output
            global_total_tokens  += f_total
            global_input_cost    += f_i_cost
            global_cached_cost   += f_c_cost
            global_output_cost   += f_o_cost
            global_total_cost    += f_t_cost

            f_ts, l_ts = func_metrics["first_ts"], func_metrics["last_ts"]
            if f_ts is not None:
                global_first_ts = f_ts if global_first_ts is None else min(global_first_ts, f_ts)
            if l_ts is not None:
                global_last_ts = l_ts if global_last_ts is None else max(global_last_ts, l_ts)

        real_time = (
            global_last_ts - global_first_ts
            if global_first_ts is not None and global_last_ts is not None
            else 0.0
        )
        cost_per_100_loc = (
            (global_total_cost / global_loc) * 100 if global_loc > 0 else None
        )

        summary = {
            "codebase_name":             codebase_name,
            "model":                     self.llm_model,
            "total_functions_processed": len(functions),
            "functions":                 functions,
            "metrics": {
                "real_execution_time_seconds":   real_time,
                "serial_execution_time_seconds": global_serial_time,
                "total_lines_of_code":           global_loc if global_loc > 0 else None,
                "token_usage": {
                    "input_tokens":  global_input_tokens - global_cached_tokens,
                    "cached_tokens": global_cached_tokens,
                    "output_tokens": global_output_tokens,
                    "total_tokens":  global_total_tokens,
                },
                "cost": {
                    "input_cost":       global_input_cost,
                    "cached_cost":      global_cached_cost,
                    "output_cost":      global_output_cost,
                    "total_cost":       global_total_cost,
                    "cost_per_100_loc": cost_per_100_loc,
                },
            },
        }

        log.info(
            "Metrics calculated: %d function(s), total_cost=$%.4f, real_time=%.1fs",
            len(functions), global_total_cost, real_time,
        )
        return summary

    def write_summary(self, path: Path, summary: Optional[dict] = None) -> Path:
        """
        Serialise ``summary`` to ``path`` as formatted JSON.

        If ``summary`` is not supplied :meth:`calculate` is called first.
        Returns the resolved output path.
        """
        if summary is None:
            summary = self.calculate()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        log.info("Metrics summary written to %s", path)
        return path

    def log_summary(self, summary: dict) -> None:
        """Emit a human-readable metrics summary via the module logger (INFO)."""
        m    = summary["metrics"]
        tok  = m["token_usage"]
        cost = m["cost"]

        log.info("")
        log.info("── Metrics Summary ─────────────────────────────────────────")
        log.info("  Model              : %s", summary["model"])
        log.info("  Functions processed: %d", summary["total_functions_processed"])
        log.info("  Real wall time     : %.1fs", m["real_execution_time_seconds"])
        log.info("  Serial total time  : %.1fs", m["serial_execution_time_seconds"])
        if m["total_lines_of_code"] is not None:
            log.info("  Total LOC          : %d", m["total_lines_of_code"])
        log.info("  Tokens  input      : %d  (cached %d)", tok["input_tokens"], tok["cached_tokens"])
        log.info("  Tokens  output     : %d", tok["output_tokens"])
        log.info("  Tokens  total      : %d", tok["total_tokens"])
        log.info("  Cost    input      : $%.4f", cost["input_cost"])
        log.info("  Cost    cached     : $%.4f", cost["cached_cost"])
        log.info("  Cost    output     : $%.4f", cost["output_cost"])
        log.info("  Cost    TOTAL      : $%.4f", cost["total_cost"])
        if cost["cost_per_100_loc"] is not None:
            log.info("  Cost / 100 LOC     : $%.4f", cost["cost_per_100_loc"])
        log.info("────────────────────────────────────────────────────────────")
        log.info("")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    # Common filename prefixes that are not part of the function name.
    _STEM_PREFIXES: tuple[str, ...] = ("metrics-", "autoup_metrics", "autoup-", "metrics_")

    def _load_manifest(self) -> Dict[str, Path]:
        """
        Look for ``analysis_manifest.csv`` in ``output_dir`` and return a
        mapping of ``{function_name: absolute_source_path}``.

        The manifest is written by :class:`Analyzer` during Stage 1.  When
        present it lets :meth:`calculate` resolve LOC without a full
        source-tree walk — useful both in LAFVT pipeline runs (where the
        manifest always exists) and in any standalone run where the user
        points at a LAFVT output directory.

        Missing, empty, or unreadable manifests are silently skipped;
        callers should treat an empty return dict as "manifest not available".
        """
        candidate = self.output_dir / "analysis_manifest.csv"
        if not candidate.exists():
            return {}
        mapping: Dict[str, Path] = {}
        try:
            with candidate.open(encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    fp = (row.get("filepath") or "").strip()
                    fn = (row.get("function_name") or "").strip()
                    if fp and fn:
                        mapping[fn] = Path(fp)
            log.debug(
                "Manifest loaded: %d function(s) from %s", len(mapping), candidate
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not parse manifest %s: %s", candidate, exc)
        return mapping

    def _discover_jsonl_files(self) -> List[Tuple[Path, str, str]]:
        """
        Return a list of ``(jsonl_path, function_name, file_slug)`` tuples.

        **Strategy 1 — LAFVT structured** (preferred):
        Look for ``autoup_metrics.jsonl`` exactly two directory levels deep::

            output_dir/<file_slug>/<function_name>/autoup_metrics.jsonl

        ``function_name`` = immediate parent directory name.
        ``file_slug``     = grandparent directory name.

        **Strategy 2 — Recursive fallback**:
        When no structured files are found, every ``*.jsonl`` file anywhere
        under ``output_dir`` is returned.

        ``function_name`` = file stem; common prefixes (``metrics-``,
        ``autoup_``) are stripped first.
        ``file_slug``     = immediate parent directory name.
        """
        # --- Strategy 1: LAFVT structure ---
        structured: List[Tuple[Path, str, str]] = []
        for slug_dir in sorted(self.output_dir.iterdir()):
            if not slug_dir.is_dir():
                continue
            for func_dir in sorted(slug_dir.iterdir()):
                if not func_dir.is_dir():
                    continue
                candidate = func_dir / "autoup_metrics.jsonl"
                if candidate.exists():
                    structured.append((candidate, func_dir.name, slug_dir.name))

        if structured:
            log.debug(
                "LAFVT-structured discovery found %d metrics file(s).",
                len(structured),
            )
            return structured

        # --- Strategy 2: Recursive fallback ---
        log.info(
            "No autoup_metrics.jsonl found in LAFVT structure; "
            "falling back to recursive *.jsonl search in %s",
            self.output_dir,
        )
        fallback: List[Tuple[Path, str, str]] = []
        for jsonl_path in sorted(self.output_dir.rglob("*.jsonl")):
            stem = jsonl_path.stem
            for prefix in self._STEM_PREFIXES:
                if stem.startswith(prefix):
                    stem = stem[len(prefix):]
                    break
            func_name = stem or jsonl_path.stem   # guard against empty stem
            file_slug = jsonl_path.parent.name
            fallback.append((jsonl_path, func_name, file_slug))

        log.debug("Recursive fallback found %d .jsonl file(s).", len(fallback))
        return fallback

    def _parse_function_metrics(self, jsonl_path: Path) -> dict:
        """
        Parse a single ``autoup_metrics.jsonl`` file.

        Returns
        -------
        dict with keys:
          ``agents``              — per-agent token/cost stats
          ``total_tokens``        — sum of all token counts
          ``total_time_seconds``  — last_ts minus first_ts
          ``first_ts``, ``last_ts``
        """
        input_price  = self.pricing["input"]
        output_price = self.pricing["output"]
        cached_price = self.pricing.get("cached", 0.0)

        agents: Dict[str, dict] = defaultdict(lambda: {
            "input_tokens": 0, "cached_tokens": 0,
            "output_tokens": 0, "total_tokens": 0,
            "input_cost": 0.0, "cached_cost": 0.0,
            "output_cost": 0.0, "total_cost": 0.0,
        })
        total_tokens = 0
        first_ts: Optional[float] = None
        last_ts:  Optional[float] = None

        try:
            with jsonl_path.open(encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        record = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    # Timestamps
                    ts_raw = record.get("timestamp")
                    if ts_raw is not None:
                        try:
                            ts = float(ts_raw)
                            first_ts = ts if first_ts is None else min(first_ts, ts)
                            last_ts  = ts if last_ts  is None else max(last_ts,  ts)
                        except (ValueError, TypeError):
                            pass

                    # Token usage
                    if record.get("type") == "task_attempt" and "llm_data" in record:
                        agent_name = record.get("agent_name", "UnknownAgent")
                        usage = record["llm_data"].get("token_usage", {})

                        i_tok = int(usage.get("input_tokens",  0))
                        c_tok = int(usage.get("cached_tokens", 0))
                        o_tok = int(usage.get("output_tokens", 0))
                        t_tok = int(usage.get("total_tokens",  0)) or (i_tok + o_tok)

                        uncached_i = max(i_tok - c_tok, 0)
                        i_cost = uncached_i * input_price
                        c_cost = c_tok      * cached_price
                        o_cost = o_tok      * output_price

                        a = agents[agent_name]
                        a["input_tokens"]  += i_tok
                        a["cached_tokens"] += c_tok
                        a["output_tokens"] += o_tok
                        a["total_tokens"]  += t_tok
                        a["input_cost"]    += i_cost
                        a["cached_cost"]   += c_cost
                        a["output_cost"]   += o_cost
                        a["total_cost"]    += i_cost + c_cost + o_cost

                        total_tokens += t_tok

        except OSError as exc:
            log.warning("Could not read %s: %s", jsonl_path, exc)

        total_time = (
            last_ts - first_ts
            if first_ts is not None and last_ts is not None
            else 0.0
        )
        return {
            "agents":             dict(agents),
            "total_tokens":       total_tokens,
            "total_time_seconds": total_time,
            "first_ts":           first_ts,
            "last_ts":            last_ts,
        }

    @staticmethod
    def _count_loc_in_file(source_file: Path, function_name: str) -> Optional[int]:
        """
        Return the LOC (``end_line - start_line + 1``) of ``function_name``
        inside a *single* source file.

        **Primary method — Lizard** (same library used by the Analyzer):
        Runs the Lizard tokenizer on the file and matches against
        ``func.name``.  Lizard correctly handles multi-line signatures,
        preprocessor guards, string literals, and block comments, giving
        accurate ``start_line`` / ``end_line`` values without a full
        compilation.

        ``func.length`` is the inclusive line span
        (``end_line - start_line + 1``), consistent with the ``lines``
        column produced by :class:`LizardAlgorithm`.

        **Fallback — brace counting**:
        Used when Lizard either cannot parse the file or fails to locate
        the function (e.g. heavily macro-generated names).  Less accurate
        but robust.
        """
        # --- Primary: Lizard ---
        try:
            analysis = lizard.analyze_file(str(source_file))
            # Exact name match first, then partial (handles "Class::method" forms)
            for func in analysis.function_list:
                if func.name == function_name:
                    return func.length
            for func in analysis.function_list:
                if func.name.endswith(f"::{function_name}") or func.name.endswith(f">{function_name}"):
                    return func.length
        except Exception as exc:  # noqa: BLE001
            log.debug("Lizard failed on %s: %s — falling back to brace count", source_file, exc)

        # --- Fallback: brace counting ---
        pattern = re.compile(
            rf"\b{re.escape(function_name)}\s*\([^;]*\)\s*\{{",
            re.MULTILINE | re.DOTALL,
        )
        try:
            content = source_file.read_text(encoding="utf-8", errors="ignore")
            m = pattern.search(content)
            if not m:
                return None
            body  = content[m.end() - 1:]
            depth = 0
            for idx, ch in enumerate(body):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                if depth == 0:
                    return body[: idx + 1].count("\n") + 1
        except OSError:
            pass
        return None

    @staticmethod
    def _find_function_loc(source_dir: Path, function_name: str) -> Optional[int]:
        """
        Walk ``source_dir`` to find a C/C++ file containing ``function_name``
        and return its LOC via :meth:`_count_loc_in_file` (Lizard-backed).
        Used as a fallback when no ``analysis_manifest.csv`` is available.
        Returns ``None`` if not found.
        """
        extensions = {".c", ".cpp", ".h", ".hpp", ".cc"}
        for root, _dirs, files in os.walk(source_dir):
            for fname in files:
                if not any(fname.endswith(ext) for ext in extensions):
                    continue
                loc = MetricsCalculator._count_loc_in_file(
                    Path(root) / fname, function_name
                )
                if loc is not None:
                    return loc
        return None


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="metrics_calculator",
        description=(
            "Calculate token usage, cost, and timing from a LAFVT output directory. "
            "Writes LAFVT_metrics.json into that directory."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "output_dir",
        help="Path to the lafvt_output/ directory produced by a LAFVT run.",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.2",
        help=(
            "LLM model used during proof (determines pricing). "
            "Options: " + ", ".join(MODEL_PRICING)
        ),
    )
    parser.add_argument(
        "--source_dir",
        default=None,
        metavar="PATH",
        help=(
            "Optional path to the C/C++ source tree for per-function "
            "LOC calculation."
        ),
    )
    parser.add_argument(
        "--codebase_name",
        default=None,
        metavar="NAME",
        help=(
            "Override the codebase name in the output JSON "
            "(default: parent directory name)."
        ),
    )
    return parser


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = _build_parser()
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    if not output_dir.is_dir():
        log.error("output_dir does not exist or is not a directory: %s", output_dir)
        return 1

    model = args.model.lower()
    if model not in MODEL_PRICING:
        log.error("Unknown model '%s'. Available: %s", model, ", ".join(MODEL_PRICING))
        return 1

    source_dir = Path(args.source_dir).resolve() if args.source_dir else None

    calculator = MetricsCalculator(
        output_dir=output_dir,
        llm_model=model,
        source_dir=source_dir,
    )

    summary  = calculator.calculate(codebase_name=args.codebase_name)
    out_path = calculator.write_summary(output_dir / "LAFVT_metrics.json", summary)
    calculator.log_summary(summary)

    log.info("Done. Metrics summary written to: %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
