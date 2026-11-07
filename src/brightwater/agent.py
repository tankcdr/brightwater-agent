"""Chapter 3: the free-form agent, fully recorded.

A LangChain/LangGraph agent with the SOP as its instructions and the
`brightwater` tools. It decides for itself which tools to call and in what
order. Every model and tool call becomes an OpenTelemetry span; every run
becomes a reasoning trace in the memory graph; every filed decision is a row
in `agent.decision`. That record is what Ch 4 compiles a procedure from.

The model is configurable:

* `BRIGHTWATER_DRIVER_MODEL` (default `claude-opus-5-5`), via the Anthropic
  API with `ANTHROPIC_API_KEY`; or
* any OpenAI-compatible server when `BRIGHTWATER_DRIVER_BASE_URL` is set
  (Ch 7: a local model).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from . import tools as T
from . import tracing

ROOT = Path(__file__).resolve().parents[2]
SOP = ROOT / "skills" / "source" / "assessment-sop.md"

INSTRUCTIONS = """You are a credit analyst at Brightwater Community Bank assessing one small-business loan
application. Follow the SOP below exactly. Use the tools for every fact; do not invent figures, records or
names. When you are done, call file_decision once with the outcome, the rationale and whether a person must
confirm (P6). If the filing is rejected, fix every error it lists and file again.

When the application goes to review, pass your assessment to file_decision as
{"risk_tier": "low|moderate|elevated|high", "cash_flow_stable": true|false,
 "recommendation": "approve|approve_with_conditions|decline"}.
"""


def default_model() -> str:
    return os.getenv("BRIGHTWATER_DRIVER_MODEL", "claude-opus-5-5")


def chat_model(name: str | None = None) -> Any:
    from langchain.chat_models import init_chat_model

    name = name or default_model()
    base = os.getenv("BRIGHTWATER_DRIVER_BASE_URL")
    if base:
        return init_chat_model(name, model_provider="openai", base_url=base,
                               api_key=os.getenv("BRIGHTWATER_DRIVER_API_KEY", "local"), temperature=0)
    return init_chat_model(name, model_provider="anthropic", max_tokens=4096)


def langchain_tools(ctx: T.RunContext, backends: T.Backends = T.BACKENDS) -> list[Any]:
    """Wrap each tool as a LangChain tool bound to this run's context."""
    from langchain_core.tools import StructuredTool

    def wrap(name: str, fn, doc: str):  # noqa: ANN001
        def call(**kwargs: Any) -> str:
            if name != "get_policy":
                kwargs.setdefault("application_id", ctx.application_id)
            # A model can ask for several tools in one turn, and they then run at the same time.
            # The event is added when the call starts, not when it ends, so the reasoning trace
            # keeps the order the calls were made in, the same order as ctx.tool_calls.
            event = {"tool": name, "args": kwargs, "records": [], "ok": False, "ms": 0}
            ctx.tool_calls.append(name)
            ctx.events.append(event)
            t0 = time.perf_counter()
            result = fn(ctx, **kwargs, b=backends) if name != "get_policy" else fn(ctx)
            event.update(records=result.get("records", []),
                         ok="error" not in result and result.get("filed", True) is not False,
                         ms=int((time.perf_counter() - t0) * 1000))
            return json.dumps(result, default=str)
        call.__doc__ = doc
        return call

    def get_application(application_id: str) -> str: ...
    def get_figures(application_id: str) -> str: ...
    def get_related_facilities(application_id: str) -> str: ...
    def find_connections(application_id: str) -> str: ...
    def get_notes(application_id: str) -> str: ...
    def get_policy() -> str: ...
    def file_decision(application_id: str, outcome: str, rationale: str, requires_person: bool,
                      assessment: dict | None = None) -> str: ...

    out = []
    for proto in (get_application, get_figures, get_related_facilities, find_connections, get_notes, get_policy,
                  file_decision):
        name = proto.__name__
        doc = (T.TOOLS[name].__doc__ or name).strip().splitlines()[0]
        out.append(StructuredTool.from_function(func=wrap(name, T.TOOLS[name], doc), name=name,
                                                description=doc, args_schema=_schema(proto)))
    return out


def _schema(proto) -> type:  # noqa: ANN001
    """A pydantic args model from a prototype's signature."""
    import inspect

    from pydantic import create_model

    fields = {}
    for p in inspect.signature(proto).parameters.values():
        default = ... if p.default is inspect.Parameter.empty else p.default
        fields[p.name] = (p.annotation, default)
    return create_model(f"{proto.__name__}_args", **fields)


async def _run_agent(ctx: T.RunContext, model: Any, backends: T.Backends, use_memory: bool) -> None:
    from langchain.agents import create_agent

    tracer = tracing.setup()
    with tracer.start_as_current_span("invoke_agent brightwater-assessor", attributes={
            "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "brightwater-assessor",
            "gen_ai.request.model": ctx.model, "brightwater.application_id": ctx.application_id,
            "brightwater.configuration": ctx.configuration, "brightwater.run_id": ctx.run_id}) as root:
        ctx.trace_id = f"{root.get_span_context().trace_id:032x}"
        handler = tracing.GenAISpans(tracer, root, ctx.model, ctx.run_id)
        system = INSTRUCTIONS + "\n\n" + SOP.read_text()
        prompt = f"Assess application {ctx.application_id}."
        # The memory middleware embeds every message it stores and runs a vector search before each
        # model call, so it needs the local embedder Ch 7 configures (BRIGHTWATER_EMBED_URL). Until
        # then the run goes without it; run() still writes the reasoning trace, which needs no vectors.
        if use_memory and os.getenv("BRIGHTWATER_EMBED_URL"):
            from neo4j_agent_memory import MemoryClient
            from neo4j_agent_memory.integrations.langchain import Neo4jMemoryMiddleware

            from . import graph
            async with MemoryClient(graph.settings()) as memory:
                agent = create_agent(model, langchain_tools(ctx, backends), system_prompt=system,
                                     middleware=[Neo4jMemoryMiddleware(memory, session_id=ctx.run_id,
                                                                       extract_entities=False)])
                await agent.ainvoke({"messages": [("user", prompt)]},
                                    config={"callbacks": [handler], "recursion_limit": 60})
        else:
            agent = create_agent(model, langchain_tools(ctx, backends), system_prompt=system)
            await agent.ainvoke({"messages": [("user", prompt)]},
                                config={"callbacks": [handler], "recursion_limit": 60})
        root.set_attribute("brightwater.outcome", (ctx.decision or {}).get("outcome", "none"))


def run(application_id: str, configuration: str = "ch3-freeform", model: Any = None, model_name: str | None = None,
        backends: T.Backends = T.BACKENDS, use_memory: bool | None = None) -> T.RunContext:
    model_name = model_name or default_model()
    ctx = T.RunContext(application_id=application_id, configuration=configuration, model=model_name)
    if use_memory is None:
        use_memory = os.getenv("BRIGHTWATER_MEMORY", "on") == "on"
    t0 = time.perf_counter()
    asyncio.run(_run_agent(ctx, model or chat_model(model_name), backends, use_memory))
    ctx.wall_s = time.perf_counter() - t0  # type: ignore[attr-defined]
    if use_memory:
        from . import graph
        asyncio.run(graph.record_run(ctx))
    return ctx
