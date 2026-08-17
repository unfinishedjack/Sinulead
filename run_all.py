"""
run_all.py

Convenience launcher for local testing -- starts the FastAPI server
(server/) as a background subprocess, waits until it's actually
answering requests, then runs the desktop client (client/main.py) in
the foreground. Closing the desktop app (or Ctrl+C-ing this script)
shuts the server back down automatically, so you don't end up with an
orphaned uvicorn process still holding port 8000.

This does NOT install dependencies -- run these once first, in each of
server/ and client/ (ideally inside a venv):

    pip install -r requirements.txt

Usage:

    python run_all.py

Optional flags:

    --port 8000        Port for the server (default 8000; must match
                        client/core/api_client.py's default, or set
                        SINULEAD_API_URL yourself before running).
    --server-only       Just start the server and wait (Ctrl+C to stop)
                        -- useful for hitting /docs by hand without 
                        also launching the desktop app.
"""

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.join(ROOT, "server")
CLIENT_DIR = os.path.join(ROOT, "client")


def wait_for_server(url: str, timeout: float = 20.0) -> bool:
    """Polls `url` until it responds or `timeout` seconds pass."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.3)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--server-only", action="store_true")
    args = parser.parse_args()

    health_url = f"http://127.0.0.1:{args.port}/health"
    
    print(f"Starting server on port {args.port} ...")
    server_proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(args.port),
        ],
        cwd=SERVER_DIR,
    )

    try:
        if not wait_for_server(health_url):
            print(
                f"Server didn't come up within 20s (checked {health_url}). "
                "Check the output above for an error -- most likely a "
                "missing dependency (run `pip install -r requirements.txt` "
                "inside server/)."
            )
            return 1

        print(f"Server is up -- http://127.0.0.1:{args.port} (docs at /docs)")

        if args.server_only:
            print("--server-only: server is running. Press Ctrl+C to stop.")
            server_proc.wait()
            return 0

        print("Starting desktop client ...")
        env = dict(os.environ)
        env.setdefault("SINULEAD_API_URL", f"http://127.0.0.1:{args.port}")
        client_proc = subprocess.run(
            [sys.executable, "main.py"], cwd=CLIENT_DIR, env=env,
        )
        return client_proc.returncode

    except KeyboardInterrupt:
        print("\nStopping ...")
        return 0

    finally:
        if server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()
        print("Server stopped.")


if __name__ == "__main__":
    sys.exit(main())
