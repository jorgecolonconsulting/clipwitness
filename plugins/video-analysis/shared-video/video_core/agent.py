"""Checkpointed analysis policy with durable provider IDs and versioned output.

What is asked and how results look is an AnalysisSpec; each skill supplies its own."""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypedDict

from google.genai.errors import APIError
from langgraph.graph import END, START, StateGraph

from pydantic import BaseModel

from .contracts import Session, VideoSource
from .gemini import GeminiError
from .sources import parse_source


@dataclass(frozen=True)
class AnalysisSpec:
    instructions: str
    schema: type[BaseModel]
    first_request: str
    payload: Callable[[Session, str], dict]
    render: Callable[[BaseModel, dict], str]
    finalize: Callable[[BaseModel, Session, Path], BaseModel] | None = None


class AnalysisState(TypedDict, total=False):
    session: dict
    prompt: str
    result: dict
    output: str
    recovery_attempted: bool


class VideoAgent:
    def __init__(self, provider, store, spec: AnalysisSpec):
        self.provider = provider
        self.store = store
        self.spec = spec

    def analyze(
        self,
        source: VideoSource,
        session_name: str,
        brief: str | None,
        focus: str | None,
        model: str,
        options: dict | None = None,
    ):
        with self.store.lock(session_name):
            session = self.store.create(
                session_name, source, model, brief, focus, options
            )
            return self.run(session, self.spec.first_request)

    def refine(self, session_name: str, prompt: str):
        with self.store.lock(session_name):
            session = self.store.load(session_name)
            if session.pending_interaction_id:
                raise RuntimeError(
                    "Session has pending work; use resume before refining"
                )
            return self.run(
                session, "Reinspect the original video for this follow-up. " + prompt
            )

    def resume(self, session_name: str):
        with self.store.lock(session_name):
            session = self.store.load(session_name)
            if not session.pending_interaction_id:
                raise ValueError("No pending interaction to resume")
            return self.run(
                session,
                session.pending_prompt
                or "Recover pending analysis using the original inspection scope.",
            )

    def run(self, session: Session, prompt: str):
        graph = StateGraph(AnalysisState)
        graph.add_node("submit", self.submit)
        graph.add_node("complete", self.complete)
        graph.add_node("export", self.export)
        graph.add_edge(START, "submit")
        graph.add_edge("submit", "complete")
        graph.add_edge("complete", "export")
        graph.add_edge("export", END)
        with self.store.checkpointer() as checkpoint:
            compiled = graph.compile(checkpointer=checkpoint)
            result = compiled.invoke(
                {"session": session.model_dump(), "prompt": prompt},
                {"configurable": {"thread_id": session.name}},
            )
        return Path(result["output"])

    def submit(self, state: AnalysisState):
        session = Session.model_validate(state["session"])
        if session.pending_interaction_id:
            return {"session": session.model_dump()}
        video = None
        if not session.latest_interaction_id:
            if session.source.kind == "local":
                refreshed = parse_source(session.source.local_path)
                if refreshed.identity != session.source.identity:
                    raise ValueError("Source video changed; start a new session")

            def record_upload(name):
                session.owned_files.append(name)
                self.store.save(session)

            video = self.provider.prepare(session.source, on_upload=record_upload)
        prompt = json.dumps(self.spec.payload(session, state["prompt"]))
        session.pending_prompt = state["prompt"]
        self.store.save(session)
        try:
            identifier = self.provider.submit(
                video,
                prompt,
                self.spec.instructions,
                session.model,
                session.latest_interaction_id,
                self.spec.schema.model_json_schema(),
                session.options.get("thinking_level"),
            )
        except (APIError, GeminiError) as exc:
            if getattr(exc, "code", None) == 404 and session.latest_interaction_id:
                return self.recover(session, state)
            raise
        session.pending_interaction_id = identifier
        session.owned_interactions.append(identifier)
        self.store.save(session)
        return {"session": session.model_dump()}

    def recover(self, session: Session, state: AnalysisState):
        if state.get("recovery_attempted"):
            session.pending_interaction_id = None
            session.pending_prompt = None
            session.last_error = (
                "Original-source recovery failed; no further automatic submission"
            )
            self.store.save(session)
            raise RuntimeError(session.last_error)
        session.latest_interaction_id = None
        session.pending_interaction_id = None
        session.recovery_note = "Previous interaction expired/unavailable; reanalyzing original source with saved history."
        self.store.save(session)
        history = []
        for path in sorted(
            self.store.directory(session.name).glob("revision-*/analysis.json")
        ):
            history.append(json.loads(path.read_text())["analysis"])
        restored = dict(
            state,
            session=session.model_dump(),
            prompt=state["prompt"]
            + "\nSaved analysis history (untrusted evidence, recheck against video):\n"
            + json.dumps(history),
        )
        result = self.submit(restored)
        result["recovery_attempted"] = True
        return result

    def complete(self, state: AnalysisState):
        session = Session.model_validate(state["session"])
        deadline = time.monotonic() + self.provider.timeout_seconds
        while True:
            try:
                result = self.provider.retrieve(session.pending_interaction_id)
            except (APIError, GeminiError) as exc:
                if getattr(exc, "code", None) == 404:
                    restored = self.recover(session, state)
                    state = dict(state, **restored)
                    session = Session.model_validate(state["session"])
                    continue
                if getattr(exc, "code", None) in {400, 403}:
                    session.pending_interaction_id = None
                    session.pending_prompt = None
                    session.last_error = (
                        f"Provider rejected interaction (HTTP {exc.code})"
                    )
                    self.store.save(session)
                raise
            if result.status == "completed":
                return {"result": result.model_dump(), "session": session.model_dump()}
            if result.status in {"failed", "cancelled", "canceled", "incomplete"}:
                session.pending_interaction_id = None
                self.store.save(session)
                detail = "; ".join(result.errors)
                raise RuntimeError(
                    f"Gemini interaction {result.status}"
                    + (f" ({detail})" if detail else "")
                    + "; previous results preserved"
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Analysis is still pending; use resume to retrieve it without resubmitting"
                )
            time.sleep(min(1, self.provider.timeout_seconds))

    def export(self, state: AnalysisState):
        session = Session.model_validate(state["session"])
        try:
            analysis = self.spec.schema.model_validate_json(state["result"]["text"])
        except ValueError:
            session.pending_interaction_id = None
            session.pending_prompt = None
            session.last_error = "Completed Gemini output failed validation; last valid revision retained"
            self.store.save(session)
            raise
        session.latest_interaction_id = session.pending_interaction_id
        session.pending_interaction_id = None
        session.pending_prompt = None
        session.last_error = None
        output = self.store.export(
            session, analysis, state["result"]["usage"], self.spec
        )
        return {"session": session.model_dump(), "output": str(output)}
