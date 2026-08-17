"""
serve_public.py

One-terminal launcher: starts the FastAPI server (server/) bound to
0.0.0.0 so it's reachable over the network, then starts
`sudo tailscale funnel <port>` to expose it publicly, and prints the
resulting public URL. Ctrl+C stops both cleanly -- no more juggling
two separate terminals.

Requirements:
    - tailscale installed, logged in, and key expiry disabled for this
      device (see the admin console -> Machines -> ... -> Disable key
      expiry) so the tunnel doesn't stop working after 180 days.
    - You'll be prompted for your sudo password once, since Funnel
      needs root to bind privileged ports.
    - Dependencies installed first (once): pip install -r requirements.txt
      inside server/.

Usage:
    python serve_public.py
    python serve_public.py --port 8000   (default is 8000)

Press Ctrl+C to stop the server and the tunnel together.
"""

import argparse
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.join(ROOT)

FUNNEL_URL_RE = re.compile(r"https://\S+\.ts\.net/?")


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


def stream_funnel_output(proc: subprocess.Popen, found_url: dict):
    """Reads tailscale funnel's stdout line by line, printing it live
    and grabbing the public URL the first time it appears."""
    for raw_line in iter(proc.stdout.readline, ""):
        if not raw_line:
            break
        line = raw_line.rstrip()
        print(f"[tailscale] {line}")
        if "url" not in found_url:
            match = FUNNEL_URL_RE.search(line)
            if match:
                found_url["url"] = match.group(0).rstrip("/")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    port = args.port

    health_url = f"http://127.0.0.1:{port}/health"

    print(f"Starting server on 0.0.0.0:{port} ...")
    server_proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "0.0.0.0", "--port", str(port),
        ],
        cwd=SERVER_DIR,
    )

    funnel_proc = None
    funnel_thread = None
    found_url: dict = {}

    try:
        if not wait_for_server(health_url):
            print(
                f"Server didn't come up within 20s (checked {health_url}). "
                "Check the output above for an error -- most likely a "
                "missing dependency (run `pip install -r requirements.txt` "
                "inside server/)."
            )
            return 1

        print(f"Server is up locally -- http://127.0.0.1:{port}")
        print("Starting Tailscale Funnel (you may be asked for your sudo password) ...")

        funnel_proc = subprocess.Popen(
            ["sudo", "tailscale", "funnel", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        funnel_thread = threading.Thread(
            target=stream_funnel_output, args=(funnel_proc, found_url), daemon=True,
        )
        funnel_thread.start()

        # Give Funnel a few seconds to print the public URL.
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and "url" not in found_url:
            time.sleep(0.3)

        print()
        if "url" in found_url:
            print(f"Public URL: {found_url['url']}")
            print(f"Docs:       {found_url['url']}/docs")
        else:
            print(
                "Didn't detect the public URL automatically yet -- check the "
                "[tailscale] lines above, or run `sudo tailscale funnel status` "
                "in another terminal."
            )
        print("\nBoth server and tunnel are running. Press Ctrl+C to stop.\n")

        server_proc.wait()
        return 0

    except KeyboardInterrupt:
        print("\nStopping ...")
        return 0

    finally:
        if funnel_proc is not None and funnel_proc.poll() is None:
            funnel_proc.terminate()
            try:
                funnel_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                funnel_proc.kill()
        if server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()
        print("Server and tunnel stopped.")


if __name__ == "__main__":
    sys.exit(main())