"""OpenTelemetry spans for every model and tool call (Ch 3 onward).

Spans follow the OpenTelemetry GenAI semantic conventions (`gen_ai.*`
attributes; span names `invoke_agent`, `chat <model>`, `execute_tool <name>`),
so any OTLP backend can read them. Two exporters run side by side:

* a JSON Lines file under `results/spans/`, which the results table is
  computed from, so measurements never depend on a UI being up; and
* OTLP over HTTP to Langfuse, when `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY` and
  `LANGFUSE_SECRET_KEY` are set. Langfuse ingests OTLP at
  `/api/public/otel` with HTTP basic auth.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

ROOT = Path(__file__).resolve().parents[2]
_provider: TracerProvider | None = None


def spans_file() -> Path:
    return Path(os.getenv("BRIGHTWATER_RESULTS", ROOT / "results")) / "spans" / "spans.jsonl"


class JsonlExporter(SpanExporter):
    """Appends spans to a JSON Lines file; with no path, to `spans_file()` as of each export."""

    def __init__(self, path: Path | None = None):
        self.path = path

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        path = self.path or spans_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for s in spans:
                f.write(json.dumps({
                    "name": s.name, "trace_id": f"{s.context.trace_id:032x}", "span_id": f"{s.context.span_id:016x}",
                    "parent_id": f"{s.parent.span_id:016x}" if s.parent else None,
                    "start_ns": s.start_time, "end_ns": s.end_time,
                    "attributes": dict(s.attributes or {}), "status": s.status.status_code.name,
                }, default=str) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


def setup(path: Path | None = None) -> trace.Tracer:
    global _provider
    if _provider is None:
        _provider = TracerProvider(resource=Resource.create({"service.name": "brightwater-agent"}))
        _provider.add_span_processor(SimpleSpanProcessor(JsonlExporter(path)))
        host = os.getenv("LANGFUSE_HOST")
        if host and os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            auth = base64.b64encode(f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()).decode()
            _provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(
                endpoint=f"{host.rstrip('/')}/api/public/otel/v1/traces", headers={"Authorization": f"Basic {auth}"})))
    # The provider is used directly rather than installed globally, so other
    # OpenTelemetry users in the same process keep theirs.
    return _provider.get_tracer("brightwater")


def provider_of(model: str) -> str:
    return "anthropic" if model.startswith("claude") else "openai"


try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:  # pragma: no cover
    BaseCallbackHandler = object  # type: ignore[misc,assignment]


class GenAISpans(BaseCallbackHandler):
    """A LangChain callback handler that turns model and tool calls into GenAI spans under one root."""

    def __init__(self, tracer: trace.Tracer, root: trace.Span, model: str, conversation_id: str):
        self.tracer, self.root, self.model, self.conversation_id = tracer, root, model, conversation_id
        self.open: dict[UUID, trace.Span] = {}
        self.parent_ctx = trace.set_span_in_context(root)

    def _start(self, run_id: UUID, name: str, attrs: dict[str, Any]) -> None:
        span = self.tracer.start_span(name, context=self.parent_ctx, attributes=attrs)
        self.open[run_id] = span

    def on_chat_model_start(self, serialized: dict, messages: list, *, run_id: UUID, **kw: Any) -> None:
        self._start(run_id, f"chat {self.model}", {
            "gen_ai.operation.name": "chat", "gen_ai.provider.name": provider_of(self.model),
            "gen_ai.system": provider_of(self.model), "gen_ai.request.model": self.model,
            "gen_ai.conversation.id": self.conversation_id})

    def on_llm_end(self, response: Any, *, run_id: UUID, **kw: Any) -> None:
        span = self.open.pop(run_id, None)
        if not span:
            return
        usage: dict[str, Any] = {}
        try:
            msg = response.generations[0][0].message
            usage = dict(getattr(msg, "usage_metadata", None) or {})
            model = (getattr(msg, "response_metadata", None) or {}).get("model_name") or self.model
            span.set_attribute("gen_ai.response.model", model)
        except Exception:  # noqa: BLE001
            pass
        span.set_attribute("gen_ai.usage.input_tokens", int(usage.get("input_tokens", 0)))
        span.set_attribute("gen_ai.usage.output_tokens", int(usage.get("output_tokens", 0)))
        details = usage.get("input_token_details") or {}
        span.set_attribute("gen_ai.usage.cache_read_input_tokens", int(details.get("cache_read", 0) or 0))
        span.end()

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kw: Any) -> None:
        span = self.open.pop(run_id, None)
        if span:
            span.record_exception(error)
            span.set_status(trace.Status(trace.StatusCode.ERROR))
            span.end()

    def on_tool_start(self, serialized: dict, input_str: str, *, run_id: UUID, **kw: Any) -> None:
        name = (serialized or {}).get("name") or kw.get("name") or "tool"
        self._start(run_id, f"execute_tool {name}", {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name,
                                                     "gen_ai.conversation.id": self.conversation_id})

    def on_tool_end(self, output: Any, *, run_id: UUID, **kw: Any) -> None:
        span = self.open.pop(run_id, None)
        if span:
            span.end()

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kw: Any) -> None:
        self.on_llm_error(error, run_id=run_id)


def read_spans(path: Path, trace_id: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [s for s in map(json.loads, path.read_text().splitlines()) if s["trace_id"] == trace_id]


def now_ns() -> int:
    return time.time_ns()
