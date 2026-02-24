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

From Here you can run the LAFVT tool using the following command:

python src/lafvt.py --target_directory <target_directory> --output_dir <output_dir> --autoup_root <autoup_root>

Example:

```bash
python src/lafvt.py --target_directory test_functions
```

NOTE: The autoup_root is the root directory of the AutoUP tool. By default it is set to ./AutoUP
NOTE: The output_dir is the directory where the results will be stored. By default it is set to ./lafvt_output

Optional Arguments:
`--no-cache`: Disable Cache Checkpointing

## Run Metrics Script
This script leverages the log files produced by LAFVT and AutoUP to calculate metrics for the LAFVT Toolchain.

### Usage
```bash
python metrics_calculator.py "Absolute file path to AutoUP output directory that holds the log files and harnesses for a codebase"
```

Example:
```bash
python metrics_calculator.py "C:\Users\gaura\Desktop\LAFVT\LAFVT\AutoUp-output\output-2026-01-24_16-32-18-RIOT"
```

### Output
Currently implements the "Time taken to generate harness in seconds" and "Harness generation cost in tokens" metrics.

The output will be present in a directory named "LAFVT_metrics" (within the input directory), which should contain a second directory named "reports" (function-level metrics) and a "codebase_summary.json" file.

- Metrics for harness generation using AutoUP on a per-function basis
- Codebase level summary

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
This script leverages the log and metrics files produced by AutoUP and LAFVT to calculate metrics.

### Usage
This script takes the following arguments:

    --input_dir: The directory containing the log and metrics files.
    **Note**: Required

    --model: The model used to generate the metrics.
    **Note**: Optional. Default is gpt-5.2

    --source_dir: The directory containing the source code.
    **Note**: Optional. If left empty, it will not calculate lines of code or any other related metrics (e.g. cost per 100 LOC).

Run the script using the following command:
```bash
python src/metrics_calculator.py <input_dir> --model <model_name> --source_dir <path_to_source>
```

Example:
```bash
python metrics_calculator.py "C:\Users\gaura\Desktop\LAFVT\LAFVT\AutoUp-output\output-2026-01-24_16-32-18-RIOT" --model gpt-5.2 --source_dir "C:\Users\gaura\Desktop\LAFVT\LAFVT\RIOT\sys"
```

### Output
Currently implements the "Time taken to generate harness in seconds" and "Harness generation cost" metrics.

The output will be present in a directory named "LAFVT_metrics" (within the input directory), which should contain a second directory named "reports" (function-level metrics) and a "codebase_summary.json" file.

- Codebase-level Metrics Summary
    Example output:
    ```json
    {
        "codebase_name": "RIOT",
        "total_functions_processed": 192,
        "metrics": {
            "real_execution_time_seconds": 106441.72755432129,
            "serial_execution_time_seconds": 1059446.1432557106,
            "total_lines_of_code": 21960,
            "token_usage": {
                "input_tokens": 159457227.0,
                "cached_tokens": 506417280.0,
                "output_tokens": 15623076.0,
                "total_tokens": 681497583.0
            },
            "cost": {
                "input_cost": 279.05014725000007,
                "cached_cost": 88.623024,
                "output_cost": 218.72306400000025,
                "total_cost": 586.3962352499997,
                "cost_per_100_loc": 2.6702925102459
            }
        }
    }
    ```

- Metrics for harness generation using AutoUP on a per-function basis
    Example Output:
    ```json
   {
        "function_name": "bluetil_addr_from_str",
        "harness_path": "/home/jorgenel/work/RIOT/harnesses/bluetil_addr/bluetil_addr_from_str",
        "lines_of_code": 73,
        "serial_execution_time_seconds": 850.8417167663574,
        "token_usage": {
            "input_tokens": 180985,
            "cached_tokens": 196096,
            "output_tokens": 16452,
            "total_tokens": 393533
        },
        "cost": {
            "input_cost": 0.31672375,
            "cached_cost": 0.0343168,
            "output_cost": 0.230328,
            "total_cost": 0.58136855
        },
        "metrics_per_agent": {
            "InitialHarnessGenerator": {
                "input_tokens": 4145,
                "cached_tokens": 1664,
                "output_tokens": 1456,
                "total_tokens": 5601,
                "input_cost": 0.00434175,
                "cached_cost": 0.0002912,
                "output_cost": 0.020384,
                "total_cost": 0.02501695
            },
            "MakefileGenerator": {
                "input_tokens": 43810,
                "cached_tokens": 28800,
                "output_tokens": 2069,
                "total_tokens": 45879,
                "input_cost": 0.0262675,
                "cached_cost": 0.005039999999999999,
                "output_cost": 0.028966,
                "total_cost": 0.0602735
            },
            "CoverageDebugger": {
                "input_tokens": 265262,
                "cached_tokens": 141056,
                "output_tokens": 7781,
                "total_tokens": 273043,
                "input_cost": 0.2173605,
                "cached_cost": 0.0246848,
                "output_cost": 0.10893399999999999,
                "total_cost": 0.3509793
            },
            "PreconditionValidator": {
                "input_tokens": 39900,
                "cached_tokens": 14592,
                "output_tokens": 3153,
                "total_tokens": 43053,
                "input_cost": 0.044289,
                "cached_cost": 0.0025536,
                "output_cost": 0.044142,
                "total_cost": 0.0909846
            },
            "debugger": {
                "input_tokens": 23964,
                "cached_tokens": 9984,
                "output_tokens": 1993,
                "total_tokens": 25957,
                "input_cost": 0.024465,
                "cached_cost": 0.0017472,
                "output_cost": 0.027902,
                "total_cost": 0.0541142
            }
        }
    }
    ``` 