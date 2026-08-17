"""
core/env.py

Tiny local ".env" file loader -- no dependency on python-dotenv, just
enough to read KEY=VALUE lines out of a plain text file and drop them
into os.environ. As of Phase 8 the only thing on the client side that
reads from this file is core/api_client.py's SINULEAD_API_URL -- SMTP
settings used to live here too (core/otp.py), but that whole module
was moved server-side; see server/.env.example instead.

Looked for at sinulead/.env (i.e. next to main.py) by default. That path
is deliberately NOT inside a package -- it's a plain data file, same
folder as sinulead.db, so it's easy to find and easy to .gitignore.

Format (one KEY=VALUE per line):
    SINULEAD_API_URL=http://127.0.0.1:8000

Rules, deliberately simple (this is not a general .env parser -- it
covers exactly what this app's own settings need):
  - Blank lines and lines starting with '#' are skipped.
  - Everything up to the first '=' is the key; everything after is the
    value, as-is except for surrounding whitespace and one layer of
    matching "..." or '...' quotes (so a value with a leading/trailing
    space, or a '#' in it, can still be expressed).
  - A real environment variable of the same name, if already set (e.g.
    by your shell, or a process manager, or a CI system), always wins --
    this file only *fills in* what's missing, it never overrides. That
    way the file is a convenient local default, not a hidden override
    a deployed instance can't reason about.
  - Malformed lines (no '=' at all) are skipped rather than raising --
    a typo in this file should never be the reason the whole app won't
    start.
"""

import os
import sys
from pathlib import Path


def _default_env_path() -> Path:
    """Where to look for the .env file.

    Normal (unfrozen) run: next to main.py, i.e. sinulead/.env -- same
    as before.

    PyInstaller-frozen run: __file__ resolves inside the temp/bundle
    extraction dir (sys._MEIPASS), which is read-only and a different
    path every launch in --onefile mode -- a user could never actually
    edit that .env to point at a real server. Frozen builds instead
    look next to the .exe/binary itself (sys.executable's folder), so
    dropping a .env alongside the packaged app works the same way it
    does for the unfrozen dev version.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / ".env"
    return Path(__file__).resolve().parent.parent / ".env"


DEFAULT_ENV_PATH = _default_env_path()


def _parse_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    if not key:
        return None
    return key, value


def load_env_file(path: str | Path | None = None) -> int:
    """Reads KEY=VALUE pairs from `path` (defaults to DEFAULT_ENV_PATH)
    into os.environ, skipping any key that's already set. Returns how
    many new variables were actually set (0 if the file doesn't exist --
    that's the normal case for anyone using real `export`ed vars instead,
    not an error).
    """
    target = Path(path) if path is not None else DEFAULT_ENV_PATH
    if not target.is_file():
        return 0

    loaded = 0
    for raw_line in target.read_text(encoding="utf-8").splitlines():
        parsed = _parse_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        if key in os.environ:
            continue  # a real env var already set this -- don't clobber it
        os.environ[key] = value
        loaded += 1
    return loaded
