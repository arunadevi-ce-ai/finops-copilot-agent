"""
LangGraph orchestrator: investigation_agent -> retrieval_agent ->
recommendation_agent -> human_approval (interrupt()).

Takes one of detector.py's findings, grounds it against rag.py's policy
precedent, and proposes an action — downgrading to manual review if the
resource's metadata matches a documented policy exception. Nothing is
final until a human resumes past interrupt().

LLM is OpenAI, key from .env. Checkpointer is env-var-driven:
InMemorySaver by default, or a Redis-backed one when REDIS_URL is set
— see _make_checkpointer().
"""

import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

import detector
import metrics
import rag
from events.watcher import load_resource_billing_facts

load_dotenv()

MODEL_NAME = "gpt-4o-mini"


def _make_llm(temperature: float = 0.0) -> ChatOpenAI:
    return ChatOpenAI(model=MODEL_NAME, temperature=temperature)


class InvestigationResult(BaseModel):
    worth_pursuing: bool = Field(
        description="Whether this anomaly's evidence actually justifies proposing an action"
    )
    reasoning: str = Field(
        description="Why, referencing the specific evidence in the finding and resource facts"
    )
    policy_search_query: str = Field(
        description="A natural-language query to retrieve the FinOps policy precedent most relevant to this specific situation"
    )


class Recommendation(BaseModel):
    proposed_action: str = Field(
        description="Specific action, e.g. 'propose deletion', 'flag for manual review', 'no action'"
    )
    dollar_estimate: float = Field(
        description="Best-effort dollar impact — savings if approved, or current cost at risk"
    )
    requires_manual_review: bool = Field(
        description="True if the resource's metadata matches a policy-documented exception/precedent, "
                     "meaning this must NOT be auto-proposed and instead needs the owning team's sign-off"
    )
    citation: str = Field(
        description="The specific policy file and section this recommendation is grounded in"
    )
    reasoning: str = Field(
        description="Why, explicitly connecting the resource's metadata to the cited policy text"
    )


class OrchestratorState(TypedDict):
    finding: dict
    resource_facts: dict
    investigation: dict
    retrieved_policy: list
    recommendation: dict
    human_decision: dict


def build_initial_state(finding: detector.Finding, data_dir: str = detector.DATA_DIR) -> OrchestratorState:
    billing_facts = load_resource_billing_facts(os.path.join(data_dir, "billing_export.csv"))
    metadata = detector.load_metadata(os.path.join(data_dir, "resource_metadata.json"))

    resource_facts = {
        **billing_facts.get(finding.resource_name, {}),
        "metadata": metadata.get(finding.resource_name, {}),
    }
    return {
        "finding": {
            "resource_name": finding.resource_name,
            "category": finding.category,
            "detail": finding.detail,
        },
        "resource_facts": resource_facts,
        "investigation": {},
        "retrieved_policy": [],
        "recommendation": {},
        "human_decision": {},
    }


def investigation_agent(state: OrchestratorState) -> dict:
    llm = _make_llm().with_structured_output(InvestigationResult)
    result: InvestigationResult = llm.invoke([
        ("system",
         "You are a FinOps investigation agent. You receive one anomaly finding "
         "from a rule-based detector, plus the resource's raw billing and "
         "inventory-metadata facts. Reason about whether the evidence actually "
         "justifies pursuing an action — don't just restate the finding. Then "
         "write a natural-language search query that would retrieve the FinOps "
         "policy precedent most relevant to this specific situation."),
        ("human", f"Finding: {state['finding']}\nResource facts: {state['resource_facts']}"),
    ])
    return {"investigation": result.model_dump()}


def retrieval_agent(state: OrchestratorState) -> dict:
    """Finds the right policy document, then pulls the whole thing
    (see rag.get_document_chunks — chunk-level top-k alone can miss
    the Precedent section)."""
    query = state["investigation"]["policy_search_query"]
    top_hits = rag.retrieve(query, top_k=1)
    if not top_hits:
        return {"retrieved_policy": []}

    best_source_file = top_hits[0]["source_file"]
    return {"retrieved_policy": rag.get_document_chunks(best_source_file)}


def recommendation_agent(state: OrchestratorState) -> dict:
    llm = _make_llm().with_structured_output(Recommendation)
    policy_context = "\n\n".join(
        f"[{r['source_file']} :: {r['section_title']}]\n{r['text']}"
        for r in state["retrieved_policy"]
    )
    result: Recommendation = llm.invoke([
        ("system",
         "You are a FinOps recommendation agent. Propose a specific action for "
         "this anomaly, grounded in the retrieved policy text below — cite the "
         "exact file and section. Before proposing deletion or any other "
         "destructive action, cross-check the resource's metadata (tags, "
         "labels) against any exception or precedent described in the policy "
         "text — pay particular attention to the Precedent section, which "
         "documents exceptions that the surface-level evidence alone would "
         "miss (that's the entire reason it's there). If the resource's "
         "metadata matches a documented exception (e.g. a tag the policy says "
         "requires the owning team's sign-off), you MUST set "
         "requires_manual_review=true and must NOT propose deletion. Always "
         "include a dollar estimate."),
        ("human",
         f"Finding: {state['finding']}\n"
         f"Resource facts: {state['resource_facts']}\n"
         f"Investigation: {state['investigation']}\n"
         f"Retrieved policy:\n{policy_context}"),
    ])

    resource_name = state["finding"]["resource_name"]
    anomaly_type = state["finding"]["category"]
    team = state["resource_facts"].get("team", "unknown")

    print(f"recommendation proposed: {resource_name} -> {result.proposed_action} "
          f"(${result.dollar_estimate:.2f}, manual_review={result.requires_manual_review})")
    metrics.RECOMMENDATIONS.labels(status="proposed", anomaly_type=anomaly_type).inc()
    metrics.PROJECTED_SAVINGS_DOLLARS.labels(anomaly_type=anomaly_type, team=team).inc(result.dollar_estimate)
    metrics.OPEN_RECOMMENDATIONS.labels(anomaly_type=anomaly_type, team=team).inc()

    if result.requires_manual_review:
        # Downgrades count as "rejected" — see metrics.py for why there's
        # no separate manual_review status.
        print(f"recommendation downgraded to manual review: {resource_name} "
              f"(citation={result.citation})")
        metrics.RECOMMENDATIONS.labels(status="rejected", anomaly_type=anomaly_type).inc()
        metrics.OPEN_RECOMMENDATIONS.labels(anomaly_type=anomaly_type, team=team).dec()

    return {"recommendation": result.model_dump()}


def human_approval(state: OrchestratorState) -> dict:
    decision = interrupt({
        "resource_name": state["finding"]["resource_name"],
        "recommendation": state["recommendation"],
    })

    # Already resolved by recommendation_agent's auto-reject path —
    # don't double-count those here.
    if not state["recommendation"].get("requires_manual_review", False):
        anomaly_type = state["finding"]["category"]
        team = state["resource_facts"].get("team", "unknown")
        status = "approved" if decision.get("approved") else "rejected"
        print(f"recommendation {status}: {state['finding']['resource_name']}")
        metrics.RECOMMENDATIONS.labels(status=status, anomaly_type=anomaly_type).inc()
        metrics.OPEN_RECOMMENDATIONS.labels(anomaly_type=anomaly_type, team=team).dec()

    return {"human_decision": decision}


def _make_checkpointer():
    """InMemorySaver by default. If REDIS_URL is set, uses a Redis-backed
    checkpointer instead, so a different process can resume a run this
    one paused."""
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        return InMemorySaver()

    from langgraph.checkpoint.redis import RedisSaver

    saver = RedisSaver(redis_url=redis_url)
    saver.setup()  # idempotent
    return saver


def build_graph():
    graph = StateGraph(OrchestratorState)
    graph.add_node("investigation_agent", investigation_agent)
    graph.add_node("retrieval_agent", retrieval_agent)
    graph.add_node("recommendation_agent", recommendation_agent)
    graph.add_node("human_approval", human_approval)

    graph.add_edge(START, "investigation_agent")
    graph.add_edge("investigation_agent", "retrieval_agent")
    graph.add_edge("retrieval_agent", "recommendation_agent")
    graph.add_edge("recommendation_agent", "human_approval")
    graph.add_edge("human_approval", END)

    return graph.compile(checkpointer=_make_checkpointer())


def run_for_finding(finding: detector.Finding, thread_id: str):
    """Builds a fresh graph and runs it up to the human_approval
    interrupt. Returns (app, state, config) so the caller can resume
    with app.invoke(Command(resume=...), config=config), or via
    resume_decision() below."""
    app = build_graph()
    config = {"configurable": {"thread_id": thread_id}}
    result = app.invoke(build_initial_state(finding), config=config)
    return app, result, config


def resume_decision(thread_id: str, decision: dict) -> dict:
    """Resumes a paused run from just its thread_id — a fresh graph
    built against the same checkpointer backend finds the existing
    state, so this doesn't need the original app/config objects."""
    app = build_graph()
    config = {"configurable": {"thread_id": thread_id}}
    return app.invoke(Command(resume=decision), config=config)


def main():
    metrics.start_metrics_server(port=8000)
    print("Prometheus metrics on http://localhost:8000/metrics")

    findings_by_resource = {f.resource_name: f for f in detector.run_detector()}
    finding = findings_by_resource["pd-payments-dr-volume-01"]

    _, result, _ = run_for_finding(finding, thread_id="demo-dr-volume")
    print(f"Investigation: {result['investigation']}")
    print(f"Recommendation: {result['recommendation']}")
    print(f"Awaiting human approval: {result['__interrupt__']}")


if __name__ == "__main__":
    main()
