"""Create/reuse .venv, install dependencies, and reproduce training in one command."""

import argparse
import os
from pathlib import Path
import subprocess
import sys


def run(collect=False):
    root = Path(__file__).resolve().parent
    environment = root / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def execute(arguments):
        subprocess.run([str(argument) for argument in arguments], cwd=root, check=True)

    if not python.is_file():
        print("Creating .venv...", flush=True)
        execute([sys.executable, "-m", "venv", environment])
    print("Installing pinned dependencies...", flush=True)
    execute([python, "-m", "pip", "--disable-pip-version-check", "install", "-r", root / "requirements.txt"])
    if collect:
        print("Collecting tournament data (cached responses are reused)...", flush=True)
        execute([python, root / "collect_data.py"])
    print("Running walk-forward validation and fitting the final model...", flush=True)
    execute([python, root / "train.py"])
    print(f"Done. Active model and validation results: {root / 'results' / 'current'}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect", action="store_true", help="Collect/rebuild the dataset before training")
    args = parser.parse_args()
    try:
        run(collect=args.collect)
    except subprocess.CalledProcessError as error:
        print(f"Reproduction stopped: a command exited with code {error.returncode}.", file=sys.stderr)
        sys.exit(error.returncode)
