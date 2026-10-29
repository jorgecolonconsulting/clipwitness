"""Atomic session manifests, locked operations and revision directories."""

import fcntl
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from pydantic import BaseModel

from .contracts import Session, VideoSource


class SessionStore:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, name: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name):
            raise ValueError(
                "Session name must contain letters, digits, underscores or hyphens"
            )
        path = self.root / name
        if path.is_symlink():
            raise ValueError("Session directory cannot be a symlink")
        return path

    @contextmanager
    def lock(self, name: str):
        self.directory(name)
        with (self.root / f"{name}.lock").open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    "Session is busy; retry after the current operation finishes"
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def create(
        self,
        name: str,
        source: VideoSource,
        model: str,
        brief: str | None,
        focus: str | None,
        options: dict | None = None,
    ) -> Session:
        self.directory(name).mkdir()
        session = Session(
            name=name,
            source=source,
            model=model,
            brief=brief,
            focus=focus,
            options=options or {},
        )
        self.save(session)
        return session

    def load(self, name: str) -> Session:
        return Session.model_validate_json(
            (self.directory(name) / "session.json").read_text()
        )

    def save(self, session: Session) -> None:
        directory = self.directory(session.name)
        descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".manifest-")
        try:
            with os.fdopen(descriptor, "w") as stream:
                stream.write(session.model_dump_json(indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, directory / "session.json")
        finally:
            Path(temporary).unlink(missing_ok=True)

    def checkpointer(self):
        return SqliteSaver.from_conn_string(str(self.root / "checkpoints.sqlite"))

    def export(self, session: Session, analysis: BaseModel, usage: dict, spec) -> Path:
        directory = self.directory(session.name)
        revision = session.revision + 1
        target = directory / f"revision-{revision:04d}"
        if target.exists():
            published = json.loads((target / "analysis.json").read_text())
            interaction = session.latest_interaction_id
            if (
                interaction
                and published["metadata"]["latest_interaction_id"] == interaction
            ):
                session.revision = revision
                self.save(session)
                return target / "analysis.md"
            raise FileExistsError(
                "Revision already exists; reload the session before exporting"
            )
        staging = Path(tempfile.mkdtemp(dir=directory, prefix=".revision-"))
        metadata = session.model_dump()
        metadata.update(revision=revision, processing="agentic", usage=usage)
        try:
            if spec.finalize:
                analysis = spec.finalize(analysis, session, staging)
            (staging / "analysis.json").write_text(
                json.dumps(
                    {"metadata": metadata, "analysis": analysis.model_dump()}, indent=2
                )
            )
            (staging / "analysis.md").write_text(spec.render(analysis, metadata))
            os.rename(staging, target)
            session.revision = revision
            self.save(session)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return target / "analysis.md"
