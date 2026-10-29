#!/usr/bin/env python3
"""Release a new plugin version: set it in both manifests, commit, and tag vX.Y.Z on that commit.

Non-interactive and deterministic, so coding agents can run it. Checks everything before changing
anything; every failure exits non-zero with what to do next. Never moves or reuses a tag.

Usage: python3 scripts/release.py X.Y.Z [--dry-run] [--push]
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = [
    ROOT / "plugins/video-analysis/.claude-plugin/plugin.json",
    ROOT / "plugins/video-analysis/.codex-plugin/plugin.json",
]
# ponytail: plain X.Y.Z only; add pre-release (-rc.1) parsing if you ever ship one.
SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
VERSION_FIELD = re.compile(r'("version"\s*:\s*")([^"]*)(")')
# Never let git wait on a prompt an agent can't answer.
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": ""}


def fail(message: str, code: int = 1):
    print(f"release: {message}", file=sys.stderr)
    sys.exit(code)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, env=ENV, stdin=subprocess.DEVNULL, capture_output=True, text=True
    )
    if check and result.returncode:
        fail(f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def current_versions() -> list:
    versions = []
    for path in MANIFESTS:
        found = VERSION_FIELD.findall(path.read_text())
        if len(found) != 1:
            fail(f"{path.relative_to(ROOT)} must have exactly one \"version\" field, found {len(found)}")
        versions.append(found[0][1])
    return versions


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="New version, X.Y.Z (no leading v)")
    parser.add_argument("--dry-run", action="store_true", help="Run every check and print the plan; change nothing")
    parser.add_argument("--push", action="store_true", help="Push the release commit and tag to origin")
    args = parser.parse_args(argv)
    new, tag = args.version, f"v{args.version}"

    if not SEMVER.fullmatch(new):
        fail(f"'{new}' is not a version like 1.2.3 (no leading v, no pre-release suffix)", 2)
    if git("rev-parse", "--is-inside-work-tree", check=False).returncode:
        fail(f"{ROOT} is not a git repository; run git init and make a first commit", 2)
    if git("rev-parse", "--verify", "-q", "HEAD", check=False).returncode:
        fail("the repository has no commits yet; commit first, then release", 2)
    if git("status", "--porcelain").stdout.strip():
        fail("the working tree has uncommitted changes; commit or stash them so the release holds only the version change", 3)

    versions = current_versions()
    if len(set(versions)) != 1:
        listed = ", ".join(f"{p.relative_to(ROOT)}={v}" for p, v in zip(MANIFESTS, versions))
        fail(f"the manifests disagree ({listed}); make them equal in a normal commit first", 3)
    old = versions[0]
    if not SEMVER.fullmatch(old):
        fail(f"current version '{old}' is not X.Y.Z; fix it by hand first", 3)
    if tuple(map(int, new.split("."))) <= tuple(map(int, old.split("."))):
        fail(f"{new} is not higher than the current {old}; pick a higher version", 4)

    if git("rev-parse", "--verify", "-q", f"refs/tags/{tag}", check=False).returncode == 0:
        fail(
            f"tag {tag} already exists locally. Tags are never moved; release the next version instead. "
            f"If {tag} was created by mistake and never pushed, a person can delete it with: git tag -d {tag}",
            5,
        )
    has_origin = git("remote", "get-url", "origin", check=False).returncode == 0
    if has_origin:
        remote = git("ls-remote", "--tags", "origin", f"refs/tags/{tag}", check=False)
        if remote.returncode:
            fail(f"could not check origin for {tag} (offline or no access): {remote.stderr.strip()}", 6)
        if remote.stdout.strip():
            fail(f"tag {tag} already exists on origin. Tags are never moved; release the next version instead", 5)
    elif args.push:
        fail("--push needs a remote named origin", 2)

    print(f"{old} -> {new}")
    for path in MANIFESTS:
        print(f"  set version in {path.relative_to(ROOT)}")
    print(f"  commit 'Release {tag}' and tag {tag} (annotated) on it")
    print(f"  {'push the commit and tag to origin' if args.push else 'no push (pass --push to publish)'}")
    if not has_origin:
        print("  note: no origin remote, so the tag was only checked locally")
    if args.dry_run:
        print("dry run: nothing changed")
        return 0

    for path in MANIFESTS:
        text = path.read_text()
        path.write_text(VERSION_FIELD.sub(lambda m: m.group(1) + new + m.group(3), text, count=1))
    files = [str(p.relative_to(ROOT)) for p in MANIFESTS]
    commit = git("commit", "-m", f"Release {tag}", "--", *files, check=False)
    if commit.returncode:
        git("checkout", "--", *files, check=False)  # leave the tree as we found it
        fail(
            f"commit failed, version change reverted: {commit.stderr.strip() or commit.stdout.strip()}. "
            "If commit signing needs a passphrase, unlock your key first",
            7,
        )
    tagged = git("tag", "-a", tag, "-m", f"Release {tag}", check=False)
    if tagged.returncode:
        fail(f"committed 'Release {tag}' but tagging failed: {tagged.stderr.strip()}. Fix it, then run: git tag -a {tag} -m 'Release {tag}'", 7)
    print(f"released {tag} at {git('rev-parse', '--short', 'HEAD').stdout.strip()}")

    if args.push:
        pushed = git("push", "--atomic", "origin", "HEAD", f"refs/tags/{tag}", check=False)
        if pushed.returncode:
            fail(f"release is local only; push failed: {pushed.stderr.strip()}. Retry with: git push --atomic origin HEAD refs/tags/{tag}", 8)
        print(f"pushed {tag} to origin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
