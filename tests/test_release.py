import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SOURCES = Path(__file__).resolve().parents[1]
MANIFESTS = [
    "plugins/video-analysis/.claude-plugin/plugin.json",
    "plugins/video-analysis/.codex-plugin/plugin.json",
]


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # A throwaway copy with a local bare "origin", isolated from the user's git config (signing, hooks).
    config = tmp_path / "gitconfig"
    config.write_text("[user]\n\tname = Test\n\temail = test@example.com\n[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    work = tmp_path / "work"
    for name in [*MANIFESTS, "scripts/release.py"]:
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(SOURCES / name, work / name)
    for name in MANIFESTS:  # start from a known version, whatever the real one is
        path = work / name
        path.write_text(path.read_text().replace(release_version(SOURCES / name), "0.1.0", 1))
    git(work, "init", "-q", "-b", "main")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "init")
    git(tmp_path, "init", "-q", "--bare", "origin.git")
    git(work, "remote", "add", "origin", str(tmp_path / "origin.git"))
    git(work, "push", "-q", "origin", "main")
    return work


def release_version(path):
    import re

    return re.search(r'"version"\s*:\s*"([^"]*)"', path.read_text()).group(1)


def release(repo, *args):
    return subprocess.run(
        [sys.executable, str(repo / "scripts/release.py"), *args],
        cwd=repo, capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )


def test_release_bumps_both_manifests_and_tags_that_commit(repo):
    # Hosts update users only when the manifest version changes; the tag must name the commit that carries it.
    run = release(repo, "0.2.0", "--push")
    assert run.returncode == 0, run.stderr
    assert [release_version(repo / m) for m in MANIFESTS] == ["0.2.0", "0.2.0"]
    assert git(repo, "rev-parse", "v0.2.0^{commit}") == git(repo, "rev-parse", "HEAD")
    assert git(repo, "show", "--name-only", "--format=%s", "HEAD").splitlines() == ["Release v0.2.0", "", *MANIFESTS]
    assert git(repo, "cat-file", "-t", "v0.2.0") == "tag"  # annotated
    assert "v0.2.0" in git(repo, "ls-remote", "--tags", "origin")


@pytest.mark.parametrize("where", ["locally", "on origin"])
def test_existing_tag_is_never_moved(repo, where):
    # Users pinned to a tag must keep getting the same code, so an existing tag stops the release.
    git(repo, "tag", "v0.2.0")
    if where == "on origin":  # e.g. pushed from another clone; this one never saw it
        git(repo, "push", "-q", "origin", "v0.2.0")
        git(repo, "tag", "-d", "v0.2.0")
    before = git(repo, "rev-parse", "HEAD")
    run = release(repo, "0.2.0")
    assert run.returncode == 5 and f"already exists {where}" in run.stderr
    assert git(repo, "rev-parse", "HEAD") == before and not git(repo, "status", "--porcelain")


@pytest.mark.parametrize(
    "args,code,message",
    [
        (["0.1.0"], 4, "not higher"),
        (["v0.2.0"], 2, "not a version"),
        (["0.2.0-rc.1"], 2, "not a version"),
    ],
)
def test_bad_versions_change_nothing(repo, args, code, message):
    run = release(repo, *args)
    assert run.returncode == code and message in run.stderr
    assert not git(repo, "status", "--porcelain") and not git(repo, "tag")


def test_uncommitted_changes_block_the_release(repo):
    # Otherwise unrelated edits ride along in the release commit or stay out of the tag.
    (repo / "scripts/release.py").write_text((repo / "scripts/release.py").read_text() + "\n")
    run = release(repo, "0.2.0")
    assert run.returncode == 3 and "uncommitted" in run.stderr
    assert not git(repo, "tag")


def test_dry_run_changes_nothing(repo):
    run = release(repo, "0.2.0", "--dry-run")
    assert run.returncode == 0 and "0.1.0 -> 0.2.0" in run.stdout
    assert not git(repo, "status", "--porcelain") and not git(repo, "tag")
