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
