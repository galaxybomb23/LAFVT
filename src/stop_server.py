#!/usr/bin/env python3
"""
StopServer
==========
Stop the LAFVT local server by reading its PID file.

Reads the ``server.pid`` written by :mod:`server` and sends a termination
signal to the corresponding process.  Works on both Windows (``ctypes``)
and Unix (``SIGTERM``).

Usage
-----
    python src/stop_server.py --output_dir <path>
"""
import argparse
import os
import sys
import signal
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Stop the LAFVT local server.")
    parser.add_argument("--output_dir", required=True, help="Same output_dir used when starting the server.")
    args = parser.parse_args()

    pid_file = Path(args.output_dir) / "server.pid"

    if not pid_file.exists():
        print(f"No server.pid found in {args.output_dir} — server may not be running.")
        return 1

    pid = int(pid_file.read_text().strip())

    try:
        if sys.platform == "win32":
            import ctypes
            PROCESS_TERMINATE = 1
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if handle:
                ctypes.windll.kernel32.TerminateProcess(handle, -1)
                ctypes.windll.kernel32.CloseHandle(handle)
                print(f"Server (PID {pid}) stopped.")
            else:
                print(f"Could not find process with PID {pid}. Already stopped?")
        else:
            os.kill(pid, signal.SIGTERM)
            print(f"Server (PID {pid}) stopped.")
    except (ProcessLookupError, OSError):
        print(f"Process {pid} not found — server may have already exited.")
    finally:
        pid_file.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
