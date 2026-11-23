"""Launch a just-built Pith and confirm it actually starts.

    python scripts/smoke_frozen.py

Runs the packaged binary's --check-bundle, then starts it with
--no-window and requests /api/health. Exits 0 only if both succeed.
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def binary() -> Path:
    pointer = ROOT / "dist" / "pith-binary.path"
    if pointer.exists():
        path = Path(pointer.read_text(encoding="utf-8").strip())
        if path.exists():
            return path
    candidates = [
        ROOT / "dist" / "Pith" / "Pith.exe",
        ROOT / "dist" / "Pith" / "Pith",
        ROOT / "dist" / "Pith.app" / "Contents" / "MacOS" / "Pith",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    sys.exit("No packaged binary found. Run python scripts/package.py first.")


def main() -> None:
    program = binary()
    print(f"checking {program}")
    check = subprocess.run([str(program), "--check-bundle"], timeout=120)
    if check.returncode != 0:
        sys.exit(f"--check-bundle exited {check.returncode}")

    data = ROOT / "dist" / "smoke-data"
    port = "8741"
    process = subprocess.Popen(
        [
            str(program),
            "--no-window",
            "--port",
            port,
            "--data-dir",
            str(data),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        deadline = time.time() + 40
        body = ""
        while time.time() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                sys.exit(f"Pith exited early ({process.returncode}):\n{output}")
            try:
                with urllib.request.urlopen(url, timeout=0.5) as response:
                    body = response.read().decode()
                if response.status == 200 and '"ok"' in body:
                    print(f"health: {body.strip()}")
                    return
            except Exception:
                time.sleep(0.3)
        output = ""
        if process.stdout:
            process.kill()
            output = process.stdout.read()
        sys.exit(f"health check did not succeed:\n{output}")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
