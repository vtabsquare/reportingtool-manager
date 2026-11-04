from __future__ import annotations

import argparse
import subprocess
import sys
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".manager-venv"
VENV_PYTHON = VENV / "Scripts" / "python.exe"
READY = VENV / ".dependencies-ready"


def main() -> None:
    parser = argparse.ArgumentParser(description="Install and start VTAB Reporting Manager")
    parser.add_argument("--reinstall", action="store_true", help="Reinstall Python dependencies")
    args, manager_args = parser.parse_known_args()

    if not VENV_PYTHON.exists():
        print("Creating the isolated VTAB Reporting Manager environment...")
        venv.EnvBuilder(with_pip=True).create(VENV)

    if args.reinstall or not READY.exists():
        print("Installing VTAB Reporting Manager dependencies...")
        subprocess.run(
            [str(VENV_PYTHON), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")],
            check=True,
        )
        READY.write_text("ready\n", encoding="utf-8")

    command = [str(VENV_PYTHON), str(ROOT / "run_manager.py"), *manager_args]
    raise SystemExit(subprocess.call(command, cwd=ROOT))


if __name__ == "__main__":
    main()

