#!/usr/bin/env python3
import os
import subprocess
from pathlib import Path
import argparse
import shutil


def run(cmd, cwd, env):
    subprocess.run(cmd, cwd=str(cwd), env=env, check=True)


def main():
    parser = argparse.ArgumentParser(
        description="Generate compile_commands.json for a RIOT app directory using bear."
    )
    parser.add_argument("app_dir", help="Path to RIOT app directory")
    parser.add_argument("--riotbase", required=True, help="Path to RIOT base directory")
    parser.add_argument("--board", required=True, help="RIOT board (e.g., native)")
    parser.add_argument("-j", "--jobs", type=int, default=8, help="Parallel jobs for make")
    parser.add_argument("--clean", action="store_true", help="Run make clean before building")
    parser.add_argument(
        "--bear",
        default="bear",
        help="Path to bear binary (or name on PATH)",
    )
    args = parser.parse_args()

    app_dir = Path(args.app_dir).resolve()
    riotbase = Path(args.riotbase).resolve()
    bear_path = args.bear

    if not app_dir.is_dir():
        raise SystemExit(f"app_dir not found: {app_dir}")
    if not riotbase.is_dir():
        raise SystemExit(f"riotbase not found: {riotbase}")
    if Path(bear_path).is_file():
        bear_bin = str(Path(bear_path))
    else:
        bear_bin = shutil.which(bear_path)
        if not bear_bin:
            raise SystemExit(f"bear not found: {bear_path}")

    env = os.environ.copy()
    env["RIOTBASE"] = str(riotbase)
    env["BOARD"] = args.board

    if args.clean:
        run(["make", "clean"], cwd=app_dir, env=env)

    build_cmd = ["make", f"-j{args.jobs}"]
    cmd = [bear_bin, "--", *build_cmd]

    run(cmd, cwd=app_dir, env=env)

    cc = app_dir / "compile_commands.json"
    if cc.exists():
        print(f"Generated: {cc}")
    else:
        raise SystemExit(
            "compile_commands.json was not generated. Try --clean or ensure a full rebuild."
        )


if __name__ == "__main__":
    main()
