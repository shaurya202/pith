"""Open the Pith window.

    python -m pith

starts a local server and opens it in a desktop window. If a desktop
window is not available, it opens your browser instead. Nothing here
listens on the network: the host is 127.0.0.1.
"""

from __future__ import annotations

import argparse
import atexit
import logging
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from pith import __version__
from pith.errors import UserError
from pith.export import stop_ffmpeg
from pith.paths import default_data_dir
from pith.server import create_app


def pick_port(preferred: int) -> int:
    start = preferred or 8734
    for port in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise UserError("Could not find a free local port for the Pith window.")


def _wait_until_up(url: str, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    health = url + "/api/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health, timeout=0.4) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.15)
    raise UserError("The Pith window server did not start.")


def _setup_logging(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(data_dir / "pith.log", encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Pith — cut the pauses, keep your style.")
    parser.add_argument("--port", type=int, default=0, help="Local port. 0 picks 8734 or the next free port.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address. Leave this as 127.0.0.1.")
    parser.add_argument("--data-dir", type=Path, default=None, help="Where projects are saved.")
    parser.add_argument("--no-window", action="store_true", help="Serve only. Do not open a window.")
    parser.add_argument("--browser", action="store_true", help="Open in the browser instead of a desktop window.")
    parser.add_argument("--self-test", action="store_true", help="Transcribe the sample, cut pauses, export, and exit.")
    parser.add_argument(
        "--download-model",
        choices=["tiny", "base", "small"],
        help="Download a Whisper model now, then exit.",
    )
    args = parser.parse_args(argv)

    if args.download_model:
        from pith.transcribe import load_model

        load_model(args.download_model)
        print(f"Whisper model {args.download_model!r} is ready.")
        return

    if args.self_test:
        from pith.selftest import run_self_test

        code = run_self_test()
        raise SystemExit(code)

    data_dir = args.data_dir or default_data_dir()
    _setup_logging(data_dir)
    atexit.register(stop_ffmpeg)
    app = create_app(data_dir / "projects")
    port = pick_port(args.port)
    url = f"http://{args.host}:{port}"

    def serve():
        uvicorn.run(app, host=args.host, port=port, log_level="warning")

    thread = threading.Thread(target=serve, name="pith-http", daemon=True)
    thread.start()
    _wait_until_up(url)
    print(f"Pith {__version__} is running at {url}")
    print("This stays on your computer. Close the window, or press Ctrl+C, to quit.")

    if args.no_window:
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            print("\nStopped.")
        return

    if not args.browser:
        try:
            import webview

            webview.create_window("Pith", url, width=1280, height=860, min_size=(960, 640))
            webview.start()
            return
        except Exception as exc:
            logging.getLogger("pith").warning("Desktop window unavailable (%s). Opening a browser.", exc)

    webbrowser.open(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
