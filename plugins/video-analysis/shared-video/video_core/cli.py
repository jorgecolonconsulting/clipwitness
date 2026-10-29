"""Command line shared by the video skills: analyze, refine, resume, status, cleanup."""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from google import genai
from google.genai.errors import APIError
from pydantic import ValidationError
from .agent import AnalysisSpec, VideoAgent
from .gemini import GeminiVideoClient
from .sessions import SessionStore
from .sources import parse_source

# Quality/cost tiers: (model, thinking level). Agentic navigation runs on thinking.
DEPTHS = {
    "deep": ("gemini-3.8-flash", "medium"),
    "lite": ("gemini-3.5-flash-lite", "high"),
}


def parser(description: str, default_root: str):
    """The full parser plus its analyze subparser, for skill-specific options."""
    result = argparse.ArgumentParser(description=description)
    result.add_argument("--session-root", type=Path, default=Path(default_root))
    result.add_argument(
        "--timeout", type=float, default=600, help="Upload/poll deadline in seconds"
    )
    commands = result.add_subparsers(dest="command", required=True)
    analyze = commands.add_parser(
        "analyze", help="Inspect a local video or public YouTube URL"
    )
    analyze.add_argument("source")
    analyze.add_argument("--session", required=True)
    analyze.add_argument("--focus", help="Initial reference inspection priorities")
    analyze.add_argument(
        "--depth",
        choices=tuple(DEPTHS),
        default="lite",
        help="deep: best visuals and timecodes; lite: about 5x cheaper, text-focused",
    )
    analyze.add_argument("--model", help="Overrides the depth's model")
    analyze.add_argument(
        "--thinking-level",
        choices=("minimal", "low", "medium", "high"),
        help="Overrides the depth's Gemini thinking level",
    )
    analyze.add_argument(
        "--local-input",
        choices=("auto", "files", "inline"),
        default="files",
        help="files: upload via the Files API (default); inline: send in the request, at most 14 MiB; "
        "auto: inline up to 14 MiB, files above",
    )
    refine = commands.add_parser("refine", help="Reinspect the original reference")
    refine.add_argument("--session", required=True)
    refine.add_argument("--prompt", required=True)
    for name in ("resume", "status", "cleanup"):
        command = commands.add_parser(name)
        command.add_argument("--session", required=True)
    return result, analyze


def create_provider(timeout: float) -> GeminiVideoClient:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise ValueError("Set GEMINI_API_KEY (or GOOGLE_API_KEY) before calling Gemini")
    return GeminiVideoClient(genai.Client(api_key=key), timeout)


def model_and_thinking(args) -> tuple[str, dict]:
    # The level is saved in the session so refine and resume think at the same level.
    model, level = DEPTHS[args.depth]
    return args.model or model, {"thinking_level": args.thinking_level or level}


def dispatch(args, spec: AnalysisSpec, options: dict) -> None:
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")
    store = SessionStore(args.session_root)
    if args.command == "status":
        print(store.load(args.session).model_dump_json(indent=2))
        return
    provider = create_provider(args.timeout)
    agent = VideoAgent(provider, store, spec)
    if args.command == "analyze":
        model, thinking = model_and_thinking(args)
        print(
            agent.analyze(
                parse_source(args.source).model_copy(
                    update={"transport": args.local_input}
                ),
                args.session,
                getattr(args, "brief", None),
                args.focus,
                model,
                {**options, **thinking},
            )
        )
    elif args.command == "refine":
        print(agent.refine(args.session, args.prompt))
    elif args.command == "resume":
        print(agent.resume(args.session))
    else:
        with store.lock(args.session):
            session = store.load(args.session)
            results = provider.delete_owned(session)
            session.owned_files = [
                name for name in session.owned_files if results[name] != "deleted"
            ]
            session.owned_interactions = [
                name
                for name in session.owned_interactions
                if results[name] != "deleted"
            ]
            if session.latest_interaction_id not in session.owned_interactions:
                session.latest_interaction_id = None
            if session.pending_interaction_id not in session.owned_interactions:
                session.pending_interaction_id = None
            store.save(session)
            print(json.dumps(results, indent=2))
            if "failed" in results.values():
                raise RuntimeError(
                    "Some remote deletions failed; retained ownership for retry"
                )


def main(argv, built, spec: AnalysisSpec, options=lambda args: {}) -> int:
    """Run one command. `built` is parser()'s result after the skill added its options;
    `options(args)` returns skill settings saved in the session (e.g. a timecode frame rate).
    """
    args = built[0].parse_args(argv)
    try:
        dispatch(args, spec, options(args))
        return 0
    except APIError as exc:
        # Avoid printing provider request bodies, which may contain sensitive inputs.
        print(
            f"Gemini API request failed (HTTP {exc.code}); check model access, source availability and quota.",
            file=sys.stderr,
        )
    except ValidationError:
        print(
            "Gemini output did not satisfy the analysis contract; no new revision published.",
            file=sys.stderr,
        )
    except (ValueError, OSError, RuntimeError, httpx.HTTPError) as exc:
        message = str(exc)
        for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            key = os.environ.get(name)
            if key:
                message = message.replace(key, "[redacted]")
        print(f"Error: {message}", file=sys.stderr)
    return 1
