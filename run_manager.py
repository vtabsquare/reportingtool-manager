from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run VTAB Reporting Manager")
    parser.add_argument("--host", default=os.getenv("HOST") or os.getenv("VTAB_MANAGER_HOST", "0.0.0.0" if os.getenv("PORT") else "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT") or os.getenv("VTAB_MANAGER_PORT", "8840")))
    parser.add_argument("--reload", action="store_true", help="Restart automatically when source files change")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the manager in the default browser")
    parser.add_argument("--init-only", action="store_true", help="Create the database schema and exit")
    return parser.parse_args()


def main() -> None:
    try:
        import uvicorn
    except ImportError:
        bootstrap = ROOT / "start_manager.py"
        if bootstrap.exists():
            print("Required packages are not installed yet.")
            print("Starting the automatic VTAB Reporting Manager setup...")
            raise SystemExit(subprocess.call([sys.executable, str(bootstrap), *sys.argv[1:]], cwd=ROOT))
        print("Required packages are missing and start_manager.py was not found.")
        print(f'Run: "{sys.executable}" -m pip install -r "{ROOT / "requirements.txt"}"')
        raise SystemExit(1)

    args = parse_args()
    from manager.config import settings
    from manager.database import Database

    Database(settings.database_path, settings.database_url).initialize()
    if args.init_only:
        print(f"VTAB Reporting Manager database initialized: {settings.database_path}")
        return

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser and settings.open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    print("VTAB Reporting Manager")
    print(f"Open: {url}")
    print(f"Environment: {settings.environment}")
    print(f"Storage: {settings.storage_backend}")
    if settings.storage_backend == "sqlite":
        print(f"Database: {settings.database_path}")
    uvicorn.run("manager.main:app", host=args.host, port=args.port, reload=args.reload, app_dir=str(ROOT))


if __name__ == "__main__":
    main()

