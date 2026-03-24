This is the LLM-Assisted Formal Verification Toolchain (LAFVT). It builds upon the AutoUP tool to formally verify C/C++ functions using CBMC.

To use LAFVT,

### Create a virtual environment and install the requirements.txt file

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

NOTE: This should incorporate the AutoUP requirements too.

### Pull in the AutoUP submodule

```bash
git submodule update --remote --merge
```

## Run the LAFVT Tool

Run LAFVT by pointing it at the root of the C/C++ project you want to verify:

```bash
python src/lafvt.py --project_dir <path/to/project>
```

Example:

```bash
python src/lafvt.py --project_dir RIOT/sys
```

Results are written to `<project_dir>/lafvt_output/` automatically. The AutoUP submodule path is resolved from the repository structure and does not need to be specified.

### Running stages standalone

Each stage can be invoked independently without running the full pipeline.

**Stage 1 — Analyzer**
```bash
python -m analyzer <path/to/source> \
    --algorithm lizard \
    --selector top_N \
    --threshold 10 \
    --output-dir ./lafvt_output
```
See the [Analyzer](#analyzer) section below for full details.

**Stage 2 — Proofer**
```bash
python src/autoup_wrapper.py proof \
    --manifest_csv  lafvt_output/analysis_manifest.csv \
    --output_dir    lafvt_output \
    --project_root  <path/to/project> \
    --llm_model     gpt-5.2 \
    --j             10
```

**Stage 3 — Review**
```bash
python src/autoup_wrapper.py review \
    --output_dir   lafvt_output \
    --project_root <path/to/project>
```

**Stages 2+3 — Proof then Review**
```bash
python src/autoup_wrapper.py all \
    --manifest_csv  lafvt_output/analysis_manifest.csv \
    --output_dir    lafvt_output \
    --project_root  <path/to/project> \
    --llm_model     gpt-5.2 \
    --j             10
```

**Stage 5 — Metrics** (also runs automatically at end of full pipeline)
```bash
python src/metrics_calculator.py <output_dir> \
    --model gpt-5.2 \
    [--source_dir <path/to/source>]
```

**Stage 6 — Interactive Report Server** (also runs automatically at end of full pipeline)
```bash
python src/server.py \
    --output_dir lafvt_output \
    --project_dir <path/to/project> \
    --llm_model gpt-5.2 \
    --lafvt_log lafvt.log
```

### Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--project_dir` | Yes | — | Root directory of the C/C++ project to verify |
| `--algorithm` | No | `lizard` | Static analysis algorithm (`lizard`, `loc`, `vccfinder`, `leopard`) |
| `--selector` | No | `top_N` | Function selection strategy (`top_N`, `top_risk`, etc.) |
| `--post-selector` | No | — | Optional post-selector for call-graph expansion (`root_func_file`, `root_func_codebase`) |
| `--llm_model` | No | `gpt-5.2` | LLM model forwarded to AutoUP agents |
| `--j` | No | `10` | Maximum number of parallel AutoUP prover workers |
| `--OPENAI_API_KEY` | No | reads `.env` | Override the OpenAI API key |
| `--target_directory` | No | `project_dir` | Restrict analysis to this subdirectory (must be inside `project_dir`) |
| `--skip-proof` | No | `False` | Skip Stage 2 (AutoUP); go straight to Review + Report assuming output dir is already populated |
| `--skip-review` | No | `False` | Skip Stage 3 (Review); go straight to Report assuming `violation_assessments.json` already exists |
| `--skip-metrics` | No | `False` | Skip Stage 5 (Metrics); go straight to Interactive Report Server with Fix Suggestions |
| `--demo` | No | `False` | Pause after each stage and print a brief summary before continuing |

The `OPENAI_API_KEY` is resolved in priority order: `--OPENAI_API_KEY` flag → `.env` file at the repo root → shell environment variable.

### Output

```
<project_dir>/lafvt_output/
├── lafvt.log                    # combined run log
├── timing_data.json             # per-stage wall-clock timings
├── LAFVT_metrics.json           # token usage, cost, timing summary (Stage 5)
├── lizard_analysis.csv          # full analyzer output
├── analyzer_interm.csv          # pre-post-selector selection (only with --post-selector)
├── analysis_manifest.csv        # selected functions passed to the proofer
├── <file_slug>/<function>/      # per-function AutoUP artifacts
│   ├── build/
│   ├── autoup_metrics.jsonl
│   ├── violation.json
│   └── execution.log
├── fix_suggestions/                 
│   ├── fix_suggestions.json          # most recent (single) fix suggestion LLM response
|   ├── fix_suggestions_history.jsonl # fix suggestion LLM response history
|   └── fix_suggester_server.log      # fix suggestion server log
├── violation_assessments.json   # scored review output
├── validation_summary.json      # global rollup
└── final_report.html            # interactive HTML report
```

## Run Metrics Script

`MetricsCalculator` parses AutoUP telemetry files (``.jsonl``) produced by a LAFVT run and
aggregates token usage, cost, and timing data per function and across the codebase.
It is automatically run as **Stage 5** of the full LAFVT pipeline, but can also be
invoked standalone against any output directory.

### File discovery

The calculator uses a two-stage discovery strategy:

1. **LAFVT-structured** (preferred) — looks for `autoup_metrics.jsonl` in the
   standard two-level layout `<output_dir>/<file_slug>/<function_name>/autoup_metrics.jsonl`.
2. **Recursive fallback** — if no structured files are found, every `*.jsonl` file
   anywhere under the given directory is collected.  The function name is derived from
   the file stem after stripping common prefixes (`metrics-`, `autoup_`).  This handles
   ad-hoc AutoUP output directories that are not produced by a full LAFVT run.

### Usage

```bash
python src/metrics_calculator.py <output_dir> \
    [--model gpt-5.2] \
    [--source_dir <path/to/source>] \
    [--codebase_name <name>]
```

Examples:

```bash
# LAFVT output directory (structured)
python src/metrics_calculator.py RIOT/lafvt_output --model gpt-5.2

# Standalone AutoUP output directory (flat / any layout)
python src/metrics_calculator.py /path/to/autoup-output-2026-01-24 --model gpt-5.2 --source_dir RIOT/sys
```

### Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `output_dir` | Yes | — | Directory containing `.jsonl` metrics files (LAFVT or standalone AutoUP output) |
| `--model` | No | `gpt-5.2` | LLM model used during proof run; selects pricing row |
| `--source_dir` | No | — | C/C++ source tree root for per-function LOC (fallback — used only when no `analysis_manifest.csv` is present in `output_dir`) |
| `--codebase_name` | No | parent dir name | Override the codebase name in the output JSON |

### Output

Writes **`LAFVT_metrics.json`** into the `output_dir`.  Contains:

- Codebase-level summary
- Per-function breakdown with token usage, cost, timing, and per-agent stats
- Per-function LOC, resolved in priority order:
  1. **`analysis_manifest.csv`** in `output_dir` (written by Stage 1 — available automatically in all LAFVT pipeline runs, no extra flag needed)
  2. **`--source_dir` tree scan** using Lizard (fallback for non-LAFVT output directories)
  3. `null` if neither source is available

Example codebase-level summary:

```json
{
    "codebase_name": "RIOT",
    "model": "gpt-5.2",
    "total_functions_processed": 192,
    "metrics": {
        "real_execution_time_seconds": 106441.73,
        "serial_execution_time_seconds": 1059446.14,
        "total_lines_of_code": 21960,
        "token_usage": {
            "input_tokens": 159457227,
            "cached_tokens": 506417280,
            "output_tokens": 15623076,
            "total_tokens": 681497583
        },
        "cost": {
            "input_cost": 279.05,
            "cached_cost": 88.62,
            "output_cost": 218.72,
            "total_cost": 586.40,
            "cost_per_100_loc": 2.67
        }
    }
}
```

## Analyzer

The Analyzer is a standalone, pluggable component that scans a C/C++ codebase, scores every function for vulnerability risk, and produces two CSV files consumed by the rest of the LAFVT pipeline. For in-depth algorithm descriptions, dataflow diagrams, output column references, and the VCCFinder SVM model details, see the [Analyzer documentation](src/analyzer/algorithms.md).

### Quick reference

| Algorithm | Flag | Summary |
|---|---|---|
| Lizard | `--algorithm lizard` | Cyclomatic complexity, nesting, params, line count — normalised within quantile bins |
| LOC | `--algorithm loc` | Raw line count normalised to [0, 1] |
| LEOPARD | `--algorithm leopard` | libclang AST metrics (C1..C4, V1..V11) with complexity-bin prioritisation and in-bin vulnerability ranking |
| VCCFinder | `--algorithm vccfinder` | Git-history mining + LinearSVC classification (offline, no GPU) |

| Selector | Flag | Summary |
|---|---|---|
| Top N | `--selector top_N` | Top-N by descending `score` |
| Bottom N | `--selector bottom_N` | Bottom-N by ascending `score` |
| First | `--selector first` | First function in output order |
| Last | `--selector last` | Last function in output order |
| All | `--selector all` | Every function |

| Post-Selector | Flag | Summary |
|---|---|---|
| Root Func File | `--post-selector root_func_file` | Traces intra-file call graph backwards to root callers (functions with no in-file callers) |
| Root Func Codebase | `--post-selector root_func_codebase` | Same approach but traces callers across the entire codebase (BFS with targeted on-demand parsing) |

### Running standalone

```bash
cd src

# Defaults: lizard algorithm, top_N selector, threshold 10
python -m analyzer <path/to/source>

# Explicit options
python -m analyzer <path/to/source> \
    --algorithm vccfinder \
    --selector top_N \
    --threshold 5 \
    --output-dir ./output

# See all options
python -m analyzer --help
```

### Using the Analyzer from Python

```python
from pathlib import Path
from analyzer import Analyzer

analyzer = Analyzer(
    project_root=Path("./output"),
    algorithm="lizard",
    selector="top_N",
    post_selector="root_func_file",  # optional
)

# Phase 1 — writes lizard_analysis.csv to output/
analysis_csv = analyzer.analyze(Path("/path/to/source"))

# Phase 2 — writes selected_functions.csv to output/
#            returns list of dicts with all analysis columns + code
selected = analyzer.select(N=5)
for func in selected:
    print(func["function_name"], func["filepath"])
```

## Report Generator

The report generator turns `*violation_assessments.json` into an interactive HTML report for triage:

- `src/report_generator.py` renders a single-page report with threat-score charts, and per-violation expandable cards (metadata, reasoning, and relevant artifacts).
- `src/server.py` serves the report locally by generating HTML dynamically from the `violation_assessments.json` currently in `--output_dir` 
- The UI includes quick search plus a theme toggle; the server also exposes `/api/shutdown` (wired to the "Stop Server" button) and writes `server.pid` into the output directory for external shutdown.

#### Running the Server Standalone

  ```bash
    python src/server.py \
    --output_dir lafvt_output \
    --project_dir  <path/to/project> \
    [--llm_model gpt-5.2]
```

### On-demand Fix Suggestions

The interactive report contains a **Generate Code Fix** button on each violation. When clicked, it calls `/api/suggest_fix` which makes an API call to an LLM for a suggested code fix, streams the LLM result back into the card, and writes JSON to `lafvt_output/fix_suggestions/`.

- Requires `OPENAI_API_KEY` (from CLI flag, `.env`, or environment) plus read access to the original source tree via `--project_dir`.

#### Running Fix Suggestion Standalone

Run the same logic from the CLI if you want a single suggestion without the server:
  ```bash
  python src/fix_suggester.py \
      --output_dir lafvt_output \
      --project_dir <path/to/project> \
      --target_func <function_name> \
      --target_precon "<violated_precondition>" \
      [--llm_model gpt-5.2]
  ```

#### LLM Response Structure

Each entry in the output JSON contains:
- **Target Function**: The name of the function where the violation was found.
- **Source File**: The path to the original source file.
- **Violated Precondition**: The specific precondition or assertion that failed.
- **Fix Suggestion**: The LLM output parsed as JSON, including:
  - `is_fixable` (boolean): Whether the LLM determined the bug could be reasonably fixed given the context.
  - `explanation` (string): A brief explanation of why the proposed fix resolves the violation.
  - `suggested_code_diff` (string): The suggested code changes in standard diff format.
  - `extra_changes_required` (string): Specifies code changes that may need to be made in other functions/headers/file due to the suggested code changes in the C source code.
- **Token Usage**: Specifies token usage, split by the type of tokens.
    - `input_tokens`: Number of tokens in the input prompt
    - `cached_tokens`: Number of tokens retrieved from cache
    - `output_tokens`: Number of tokens generated in the model's response
    - `reasoning_tokens`: Number of tokens used during internal reasoning/thinking process
    - `total_tokens`: Sum of all tokens consumed in the API call

#### Output Files

| File | Description |
|---|---|
| `fix_suggestions.json` | Most recent fix suggestion LLM response |
| `fix_suggestions_history.jsonl` | Append-only history of all fix suggestion LLM responses |
| `fix_suggester_server.log` | Server log for fix suggestion requests |

## Run Metrics Script

See the [Metrics Script](#run-metrics-script) section above. 
