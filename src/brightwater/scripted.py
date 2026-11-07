"""A scripted chat model, for running the whole agent loop with no model API.

It plays a competent analyst with a fixed plan: read the application, the
figures, the related facilities, the connections and the policy; decide with
the policy engine; write a cited rationale; file. Its first filing leaves one
figure uncited on purpose, so every scripted run exercises the linter's
reject-and-retry path.

Use it to check the plumbing (`brightwater agent run APP-H1 --model scripted`)
and in CI. It is not a baseline: its numbers never go in the results table.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

PLAN = ["get_application", "get_figures", "get_related_facilities", "find_connections", "get_policy"]


class ScriptedAnalyst(BaseChatModel):
    application_id: str = ""

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kw: Any) -> "ScriptedAnalyst":
        return self

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any) -> ChatResult:
        done = [m for m in messages if isinstance(m, ToolMessage)]
        results = {m.name: json.loads(m.content) for m in done if m.content.startswith("{")}
        filings = [json.loads(m.content) for m in done if m.name == "file_decision"]
        app = self.application_id
        usage = {"input_tokens": 1000 + 200 * len(done), "output_tokens": 120, "total_tokens": 1120 + 200 * len(done)}

        def call(name: str, args: dict[str, Any]) -> ChatResult:
            msg = AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": uuid.uuid4().hex[:12]}],
                            usage_metadata=usage)
            return ChatResult(generations=[ChatGeneration(message=msg)])

        for name in PLAN:
            if name not in results:
                return call(name, {} if name == "get_policy" else {"application_id": app})
        if filings and filings[-1].get("filed"):
            msg = AIMessage(content=f"Filed decision {filings[-1]['decision_id']}.", usage_metadata=usage)
            return ChatResult(generations=[ChatGeneration(message=msg)])

        from . import metrics_cube, policy
        d = policy.evaluate(metrics_cube.facts_for(app))
        f = results["get_figures"]["figures"]
        lines = [f"Outcome: {d.outcome.value}"]
        dscr = f"DSCR is {f['dscr']['value']:.2f}"
        lines.append(dscr + ("." if not filings else f" [{f['dscr']['record']}]."))  # first filing: uncited
        lines.append(f"Group exposure is ${f['group_exposure']['value']:,.0f} [{f['group_exposure']['record']}].")
        for fired in d.fired:
            lines.append(f"Rule {fired.rule_id} applies [policy:{fired.rule_id}v1].")
        assessment = None
        if d.outcome is policy.Outcome.REVIEW:
            assessment = {"risk_tier": "moderate", "cash_flow_stable": True, "recommendation": "approve_with_conditions"}
        return call("file_decision", {"application_id": app, "outcome": d.outcome.value, "rationale": "\n".join(lines),
                                      "requires_person": d.requires_person, "assessment": assessment})
