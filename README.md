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

### Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--project_dir` | Yes | — | Root directory of the C/C++ project to verify |
| `--algorithm` | No | `lizard` | Static analysis algorithm (`lizard`, `loc`) |
| `--selector` | No | `top_N` | Function selection strategy (`top_N`, `top_risk`, etc.) |
| `--llm_model` | No | `gpt-5.2` | LLM model forwarded to AutoUP agents |
| `--j` | No | `10` | Maximum number of parallel AutoUP prover workers |
| `--OPENAI_API_KEY` | No | reads `.env` | Override the OpenAI API key |
| `--target_directory` | No | `project_dir` | Restrict analysis to this subdirectory (must be inside `project_dir`) |
| `--skip-proof` | No | `False` | Skip Stage 2 (AutoUP); go straight to Review + Report assuming output dir is already populated |
| `--skip-review` | No | `False` | Skip Stage 3 (Review); go straight to Report assuming `violation_assessments.json` already exists |
| `--demo` | No | `False` | Pause after each stage and print a brief summary before continuing |

The `OPENAI_API_KEY` is resolved in priority order: `--OPENAI_API_KEY` flag → `.env` file at the repo root → shell environment variable.

### Output

```
<project_dir>/lafvt_output/
├── lafvt.log                    # combined run log
├── timing_data.json             # per-stage wall-clock timings
├── LAFVT_metrics.json           # token usage, cost, timing summary (Stage 5)
├── lizard_analysis.csv          # full analyzer output
├── analysis_manifest.csv        # selected functions passed to the proofer
├── <file_slug>/<function>/      # per-function AutoUP artifacts
│   ├── build/
│   ├── autoup_metrics.jsonl
│   ├── violation.json
│   └── execution.log
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

The Analyzer is a standalone, pluggable component that scans a C/C++ codebase, scores every function for vulnerability risk, and produces two CSV files consumed by the rest of the LAFVT pipeline.

### Output files

| File | Columns | Description |
|---|---|---|
| `<algorithm>_analysis.csv` | `filepath`, `function_name`, + algorithm metrics | Full per-function analysis results |
| `selected_functions.csv` | `filepath`, `function_name` | Functions chosen by the selector |

`filepath` values are always **absolute** paths so they can be handed directly to downstream tools regardless of the working directory.

### Running standalone

```bash
cd src

# Defaults: lizard algorithm, top_N selector, threshold 10
python -m analyzer <path/to/source>

# Explicit options
python -m analyzer <path/to/source> \
    --algorithm lizard \
    --selector top_N \
    --threshold 5 \
    --output-dir ./output

# See all options
python -m analyzer --help
```

### Algorithms

Currently implemented:

| Name | Flag | Description |
|---|---|---|
| Lizard | `--algorithm lizard` | Computes cyclomatic complexity, nesting depth, parameter count, and line count per function. Metrics are normalised within complexity bins; `score` is the sum of the three normalised values (higher = higher risk). |
| LOC | `--algorithm loc` | Scores functions by raw line count, normalised to [0, 1] across the codebase. The longest function scores 1.0. Simple and fast. |

### Selectors

All selectors operate on the canonical `score` column produced by every algorithm.  `N` accepts either an integer (e.g. `5`) or a percentage string (e.g. `10%`).

| Name | Flag | Description |
|---|---|---|
| Top N | `--selector top_N` | Top-N functions by descending `score` (use `--threshold` to set N) |
| Bottom N | `--selector bottom_N` | Bottom-N functions by ascending `score` (use `--threshold` to set N) |
| First | `--selector first` | First function in analysis output order |
| Last | `--selector last` | Last function in analysis output order |
| All | `--selector all` | Every function, no filtering |

### Adding a new algorithm

1. Copy `src/analyzer/algorithms/_template.py` to `src/analyzer/algorithms/my_algo.py`
2. Set `name = "my_algo"` and implement the `analyze(root_directory)` method — return a `DataFrame` with at minimum `filepath` and `function_name` columns
3. Add `from . import my_algo` to `src/analyzer/algorithms/__init__.py`

The new algorithm will be immediately available via `--algorithm my_algo` with no other changes required.

### Adding a new selector

Same process but inherit from `SelectorAlgorithm`, implement `select(df, N)`, use `@register_selector`, place the file under `src/analyzer/selectors/`, and add the import to `src/analyzer/selectors/__init__.py`.

### Using the Analyzer from Python

```python
from pathlib import Path
from analyzer import Analyzer

analyzer = Analyzer(
    project_root=Path("./output"),
    algorithm="lizard",
    selector="top_N",
)

# Phase 1 — writes lizard_analysis.csv to output/
analysis_csv = analyzer.analyze(Path("/path/to/source"))

# Phase 2 — writes selected_functions.csv to output/
#            returns list of dicts with all analysis columns + code
selected = analyzer.select(N=5)
for func in selected:
    print(func["function_name"], func["filepath"])
```


## Run Metrics Script

See the [Metrics Script](#run-metrics-script) section above. 