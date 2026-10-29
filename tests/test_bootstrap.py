import hashlib
from pathlib import Path

import bootstrap


def fake_installer(monkeypatch, calls):
    def run(command, check=False, **kwargs):
        calls.append(command)
        if command[1:3] == ["-m", "venv"]:
            python = Path(command[3]) / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("")

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    monkeypatch.setattr(bootstrap, "base_python", lambda: "python3")


def test_lock_change_builds_beside_and_matching_lock_reuses(tmp_path, monkeypatch):
    # A new lock must never run against stale dependencies, and must not delete the environment
    # another host (Claude Code vs Codex on an older plugin version) may be running right now.
    monkeypatch.setenv("VIDEO_ANALYSIS_HOME", str(tmp_path / "home"))
    lock = tmp_path / "requirements.lock"
    lock.write_text("pydantic==1\n")
    calls = []
    fake_installer(monkeypatch, calls)

    python = bootstrap.ensure_venv(lock)
    assert [c[1:3] for c in calls] == [["-m", "venv"], ["-m", "pip"]]
    assert (python.parents[1] / ".lock-sha256").read_text() == hashlib.sha256(
        lock.read_bytes()
    ).hexdigest()

    calls.clear()
    assert bootstrap.ensure_venv(lock) == python
    assert calls == []  # every later run starts without touching the network

    lock.write_text("pydantic==2\n")
    newer = bootstrap.ensure_venv(lock)
    assert [c[1:3] for c in calls] == [["-m", "venv"], ["-m", "pip"]]
    assert newer != python and python.exists()


def test_failed_install_is_retried_next_run(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_ANALYSIS_HOME", str(tmp_path / "home"))
    lock = tmp_path / "requirements.lock"
    lock.write_text("pydantic==2\n")
    calls = []
    fake_installer(monkeypatch, calls)
    real_run = bootstrap.subprocess.run

    def offline(command, check=False, **kwargs):
        real_run(command, check, **kwargs)
        if command[1:3] == ["-m", "pip"]:
            raise bootstrap.subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(bootstrap.subprocess, "run", offline)
    try:
        bootstrap.ensure_venv(lock)
    except SystemExit as exc:
        assert "network access" in str(exc)
    else:
        raise AssertionError("an offline install must fail loudly")

    fake_installer(monkeypatch, calls)
    calls.clear()
    bootstrap.ensure_venv(lock)
    assert [c[1:3] for c in calls] == [["-m", "venv"], ["-m", "pip"]]


def test_unwritable_cache_explains_how_to_fix_it(tmp_path, monkeypatch):
    # Codex's sandbox blocks writes to ~/.cache; the user must see what to approve, not a traceback.
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setenv("VIDEO_ANALYSIS_HOME", str(locked / "home"))
    lock = tmp_path / "requirements.lock"
    lock.write_text("pydantic==2\n")
    try:
        bootstrap.ensure_venv(lock)
    except SystemExit as exc:
        assert "outside the sandbox" in str(exc)
    else:
        raise AssertionError("an unwritable cache must fail with instructions")
    finally:
        locked.chmod(0o700)
