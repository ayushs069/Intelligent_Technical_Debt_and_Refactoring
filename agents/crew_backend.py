"""
CrewAI backend (optional) — runs the same three agents through CrewAI.

Install with ``pip install crewai``.  The native backend (agents/agents.py) is
the default because it is cheaper, deterministic to cache and guarantees valid
JSON through structured outputs; this backend exists to reproduce the plan's
CrewAI setup.  Outputs use the same fields as the native chain.
"""

from __future__ import annotations

from typing import Any
import os

from pydantic import BaseModel

from agents.agents import (SCORE_RUBRIC, _clamp_score, analysis_agent, priority_agent,
                           refactor_agent)


class _Analysis(BaseModel):
    summary: str
    debt_indicators: list[str]
    impact_factors: list[str]
    risk_assessment: str


class _Priority(BaseModel):
    priority: str
    priority_score: int
    reason: str


class _Recommendation(BaseModel):
    action: str
    refactoring_type: str
    expected_benefit: str
    risk: str


def _crew_agent(spec, llm):
    from crewai import Agent

    return Agent(role=spec.role, goal=spec.goal, backstory=spec.backstory,
                 llm=llm, verbose=False, allow_delegation=False)


def crew_model_name(model: str) -> str:
    """Preserve explicit provider routes rather than forcing every model to Anthropic."""
    if "/" in model:
        return model
    return f"anthropic/{model}" if model.startswith("claude-") else f"openai/{model}"


def run_crew_for_item(item_context: str, model: str) -> dict[str, Any]:
    from crewai import LLM, Crew, Process, Task

    routed_model = crew_model_name(model)
    if routed_model.startswith("groq/") and not os.environ.get("GROQ_API_KEY"):
        raise ValueError("GROQ_API_KEY is missing; configure it in the local .env file")
    llm = LLM(model=routed_model, temperature=0, timeout=120, max_tokens=2048)
    a_agent = _crew_agent(analysis_agent, llm)
    p_agent = _crew_agent(priority_agent, llm)
    r_agent = _crew_agent(refactor_agent, llm)

    analysis_task = Task(
        description=f"{analysis_agent.instructions}\n\nItem under review:\n\n{item_context}",
        expected_output="JSON with summary, debt_indicators, impact_factors, risk_assessment",
        agent=a_agent, output_pydantic=_Analysis)
    priority_task = Task(
        description=(f"{priority_agent.instructions}\n\nItem under review:\n\n{item_context}\n\n"
                     "Use the Code Analysis Expert's output from the previous task."),
        expected_output=f"JSON with priority (HIGH/MEDIUM/LOW), priority_score, reason. "
                        f"{SCORE_RUBRIC}",
        agent=p_agent, context=[analysis_task], output_pydantic=_Priority)
    refactor_task = Task(
        description=(f"{refactor_agent.instructions}\n\nItem under review:\n\n{item_context}"),
        expected_output="JSON with action, refactoring_type, expected_benefit, risk",
        agent=r_agent, context=[analysis_task, priority_task], output_pydantic=_Recommendation)

    crew = Crew(agents=[a_agent, p_agent, r_agent],
                tasks=[analysis_task, priority_task, refactor_task],
                process=Process.sequential, verbose=False)
    output = crew.kickoff()
    analysis, priority, rec = (t.pydantic for t in output.tasks_output)
    level = priority.priority.upper()
    return {
        "priority": level if level in ("HIGH", "MEDIUM", "LOW") else "MEDIUM",
        "priority_score": _clamp_score(priority.priority_score),
        "reason": priority.reason,
        "action": rec.action,
        "refactoring_type": rec.refactoring_type,
        "expected_benefit": rec.expected_benefit,
        "refactoring_risk": rec.risk,
        "analysis_summary": analysis.summary,
        "debt_indicators": analysis.debt_indicators,
        "impact_factors": analysis.impact_factors,
    }


def prioritise_with_crewai(items: list[dict], contexts: dict[str, str],
                           model: str) -> list[dict]:
    try:
        import crewai  # noqa: F401
    except ImportError as exc:
        raise SystemExit("[error] CrewAI is not installed: pip install crewai") from exc
    from agents.prioritise import rank_results

    results = []
    for n, item in enumerate(items, 1):
        base = {"id": item["id"], "file": item["file"], "function": item["function"],
                "line": item["line"]}
        try:
            results.append({**base, **run_crew_for_item(contexts[item["id"]], model)})
        except Exception as exc:  # CrewAI surfaces many error types
            results.append({**base, "error": f"CrewAI failed ({type(exc).__name__}); check provider access and quota."})
        print(f"  [{n:>3}/{len(items)}] {item['id']}")
    return rank_results(results)
