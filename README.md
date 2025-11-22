This is the LLM-Assisted Formal Verification Toolchain (LAFVT). It builds upon the AutoUP tool to formally verify C/C++ functions using CBMC.


To use LAFVT, create a virtual environment and install the requirements.txt file

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```
NOTE: This should incorporate the AutoUP requirements too.

From Here you can run the LAFVT tool using the following command: 

python src/lafvt.py --target_directory <target_directory> --output_dir <output_dir> --autoup_root <autoup_root> 

NOTE: The autoup_root is the root directory of the AutoUP tool. By default it is set to ./AutoUP
NOTE: The output_dir is the directory where the results will be stored. By default it is set to ./lafvt_output

