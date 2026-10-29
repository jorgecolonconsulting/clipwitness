"""Create the plugin's Python environment from its lock on first use.

Installed plugins are copied into each host's cache (Claude Code and Codex alike), so environments live in
one per-user directory both share, one per lock: a host on another plugin version never deletes the
environment a running analysis uses. Runs on Python 3.9.
"""

import fcntl
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

CANDIDATES = ("python3.13", "python3.12", "python3.11", "python3.10", "python3")


def home() -> Path:
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(
        os.environ.get("VIDEO_ANALYSIS_HOME") or os.path.join(cache, "video-analysis")
    ).expanduser()


def base_python() -> str:
    check = "import sys; sys.exit(sys.version_info < (3, 10))"
    for name in CANDIDATES:
        path = shutil.which(name)
        if path and subprocess.run([path, "-c", check]).returncode == 0:
            return path
    sys.exit("analyze-video needs Python 3.10 or newer on PATH: brew install python@3.13")


def ensure_venv(lock: Path) -> Path:
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    # ponytail: old venv-* folders are never pruned (~90 MB each, one per lock change); delete them by hand.
    venv = home() / f"venv-{digest[:12]}"
    python = venv / "bin" / "python"
    marker = venv / ".lock-sha256"

    def ready():
        # A missing interpreter (e.g. Homebrew removed the base Python) also forces a rebuild.
        return python.exists() and marker.is_file() and marker.read_text().strip() == digest

    if ready():
        return python
    try:
        venv.parent.mkdir(parents=True, exist_ok=True)
        stream = open(venv.parent / "venv.lock", "a")
    except OSError as exc:
        # Codex's default sandbox can read the cache but not write it; an approved (unsandboxed) run can.
        sys.exit(
            f"Cannot write {venv.parent} ({exc.strerror}). In Codex, approve running this command "
            "outside the sandbox; the first run installs dependencies there."
        )
    with stream:
        fcntl.flock(stream, fcntl.LOCK_EX)  # one installer at a time; the rest wait, then reuse it
        if not ready():
            print(f"First run: installing dependencies into {venv} ...", file=sys.stderr)
            shutil.rmtree(venv, ignore_errors=True)
            try:
                subprocess.run([base_python(), "-m", "venv", str(venv)], check=True)
                subprocess.run(
                    [str(python), "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", str(lock)],
                    check=True,
                    stdout=sys.stderr,
                )
            except subprocess.CalledProcessError as exc:
                sys.exit(
                    f"Dependency setup failed (exit {exc.returncode}). It needs network access to PyPI "
                    f"and write access to {venv.parent}; retry once both are available."
                )
            marker.write_text(digest)
    return python
