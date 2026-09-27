"""
main.py

Local-dev launcher -- starts the FastAPI server (app/) bound to
127.0.0.1 so you can hit /docs and test endpoints by hand.

Production is now Render (see render.yaml at the repo root), which
runs `uvicorn app.main:app --host 0.0.0.0 --port $PORT` directly --
there is no tunnel/funnel step anymore, so this file no longer shells
out to Tailscale. Use run_all.py instead if you also want the desktop
client launched alongside the server.

Usage:
    python main.py
    python main.py --port 8000   (default is 8000)

Press Ctrl+C to stop.
"""

import argparse
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    print(f"Starting server on 127.0.0.1:{args.port} (local dev only) ...")
    try:
        subprocess.run(
            [
                sys.executable, "-m", "uvicorn", "app.main:app",
                "--reload", "--host", "127.0.0.1", "--port", str(args.port),
            ]
        )
    except KeyboardInterrupt:
        print("\nStopping ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())