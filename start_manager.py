from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".manager-venv"
VENV_PYTHON = VENV / "Scripts" / "python.exe"
READY = VENV / ".dependencies-ready"
REQUIRED_IMPORTS = ("fastapi", "uvicorn", "dotenv", "pydantic", "psycopg", "cryptography")


def dependencies_healthy() -> bool:
    probe = "import " + ",".join(REQUIRED_IMPORTS)
    return subprocess.run(
        [str(VENV_PYTHON), "-c", probe],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Install and start VTAB Reporting Manager")
    parser.add_argument("--reinstall", action="store_true", help="Reinstall Python dependencies")
    args, manager_args = parser.parse_known_args()

    if not VENV_PYTHON.exists():
        print("Creating the isolated VTAB Reporting Manager environment...")
        venv.EnvBuilder(with_pip=True).create(VENV)

    if args.reinstall or not READY.exists() or not dependencies_healthy():
        print("Installing VTAB Reporting Manager dependencies...")
        try:
            subprocess.run(
                [str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "-r", str(ROOT / "requirements.txt")],
                check=True,
            )
        except subprocess.CalledProcessError as error:
            print("Dependency installation failed.")
            print(f'Run manually: "{VENV_PYTHON}" -m pip install --upgrade -r "{ROOT / "requirements.txt"}"')
            raise SystemExit(error.returncode) from error
        if not dependencies_healthy():
            print("The environment is still incomplete after installation. Delete .manager-venv and run start_manager.py again.")
            raise SystemExit(1)
        READY.write_text("ready\n", encoding="utf-8")

    command = [str(VENV_PYTHON), str(ROOT / "run_manager.py"), *manager_args]
    raise SystemExit(subprocess.call(command, cwd=ROOT))


if __name__ == "__main__":
    main()

