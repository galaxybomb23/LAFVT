import subprocess
import os
import sys
from pathlib import Path
from typing import Dict, Any, Tuple

class AutoUPWrapper:
    def __init__(self, autoup_root: Path):
        self.autoup_root = Path(autoup_root)
        self.run_script = self.autoup_root / "src" / "run.py"

    def run(self, function_data: Dict[str, Any], output_dir: Path) -> Tuple[bool, str]:
        """
        Runs AutoUP on the specified function.
        Returns (success, message).
        """
        func_name = function_data['name']
        file_path = Path(function_data['file'])
        file_name = file_path.stem
        
        # Assuming its cwd as user should execute from root (maybe add a flag later)
        project_root = os.getcwd() 
        
        harness_path = output_dir / file_name / func_name
        harness_path.mkdir(parents=True, exist_ok=True)
        
        log_file =output_dir / file_name / func_name / "autoup_log.log"
        metrics_file =output_dir / file_name /func_name / "autoup_metrics.jsonl"
        
        # Construct command
        # python src/run.py all ...
        cmd = [
            str(sys.executable), str(self.run_script),
            "all",
            "--target_function_name", func_name,
            "--root_dir", str(project_root),
            "--harness_path", str(harness_path),
            "--target_file_path", str(file_path),
            "--log_file", str(log_file),
            "--metrics_file", str(metrics_file)
        ]
        
        print(f"Running AutoUP for {func_name}...")
        # print(f"Command: {' '.join(cmd)}")
        
        try:
            # Capture output to log file in harness dir
            with open(harness_path / "execution.log", "w") as f:
                result = subprocess.run(
                    cmd, 
                    cwd=self.autoup_root, #not sure if this is needed
                    stdout=f, 
                    stderr=subprocess.STDOUT,
                    text=True
                )
            
            if result.returncode == 0:
                return True, f"AutoUP completed successfully for {func_name}"
            else:
                return False, f"AutoUP failed for {func_name} with return code {result.returncode}"
                
        except Exception as e:
            return False, f"AutoUP execution error: {e}"
    
    def review(self, output_dir: Path, project_root: Path) -> Tuple[bool, str]:
        """
        Run AutoUP in review mode on the specified output directory.
        """
        log_file = output_dir / "review_log.log"
        cmd = [
            str(sys.executable), str(self.run_script),
            "review",
            "--harness_path", str(output_dir),
            "--log_file", str(log_file),
            "--target_function_name", "none",  # Not needed for review, but run.py requires it
            "--root_dir", str(project_root),
            "--target_file_path", "none",  # Not needed for review, but run
            "--metrics_file", str(output_dir / "review_metrics.jsonl")  # Not needed for review, but run.py requires it
        ]
        print(f"Running command: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd, 
                cwd=self.autoup_root,
                text=True
            )
            
            if result.returncode == 0:
                return True, f"AutoUP review completed successfully for {output_dir}"
            else:
                return False, f"AutoUP review failed for {output_dir} with return code {result.returncode}"
                
        except Exception as e:
            return False, f"AutoUP review error: {e}"