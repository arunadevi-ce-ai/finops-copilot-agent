"""
MCP tool server wrapping detector.py, rag.py, and orchestrator.py —
get_cost_anomalies, search_finops_policy, propose_rightsizing. Runs
over stdio using the low-level mcp.server.Server pattern (explicit
list_tools/call_tool handlers, explicit JSON Schema).

Each tool is a thin wrapper over an already-tested function.
"""

import asyncio
import os
import uuid

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

import detector
import orchestrator
import rag

server = Server("finops-copilot")

TOOLS = [
    types.Tool(
        name="get_cost_anomalies",
        description=(
            "Runs detector.py's rule-based anomaly detector against the mock "
            "billing/utilization/metadata data and returns current findings, "
            "optionally filtered to one team. Mechanical/naive by design — "
            "no policy nuance (see propose_rightsizing for that)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "team": {
                    "type": "string",
                    "description": "Optional team name to filter findings to (e.g. 'payments', 'ml-training'). Omit to return findings for every team.",
                }
            },
            "required": [],
        },
    ),
    types.Tool(
        name="search_finops_policy",
        description=(
            "Semantic search over the four ingested FinOps policy docs in "
            "Chroma (rag.py's retrieve()). Returns the top_k closest chunks "
            "— each one structural section of a policy doc (When this "
            "applies / Required action before proposing a change / Approved "
            "actions / Precedent)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language query describing the situation to find FinOps policy precedent for.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of policy chunks to return.",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 12,
                },
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="propose_rightsizing",
        description=(
            "Runs the full LangGraph orchestrator (investigation_agent -> "
            "retrieval_agent -> recommendation_agent) for one currently- "
            "anomalous resource, up to the human_approval interrupt() point, "
            "and returns the pending proposal. Nothing is executed — this "
            "only produces a proposal awaiting a separate human-approval step."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "resource_name": {
                    "type": "string",
                    "description": "Resource name to propose an action for (e.g. 'vm-ml-training-gpu-01'). Must be a resource detector.py currently flags as an anomaly.",
                }
            },
            "required": ["resource_name"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return TOOLS


def _get_cost_anomalies(team: str | None) -> list[dict]:
    findings = detector.run_detector()
    metadata = detector.load_metadata(os.path.join(detector.DATA_DIR, "resource_metadata.json"))

    results = []
    for f in findings:
        f_team = metadata.get(f.resource_name, {}).get("team", "unknown")
        if team and f_team != team:
            continue
        results.append({
            "resource_name": f.resource_name,
            "category": f.category,
            "detail": f.detail,
            "team": f_team,
        })
    return results


def _search_finops_policy(query: str, top_k: int) -> list[dict]:
    return rag.retrieve(query, top_k=top_k)


def _propose_rightsizing(resource_name: str) -> dict:
    findings = detector.run_detector()
    finding = next((f for f in findings if f.resource_name == resource_name), None)
    if finding is None:
        known = sorted(f.resource_name for f in findings)
        raise ValueError(
            f"No current anomaly finding for resource_name={resource_name!r}. "
            f"Currently anomalous resources: {known}"
        )

    thread_id = f"mcp-propose-{resource_name}-{uuid.uuid4().hex[:8]}"
    _, result, _ = orchestrator.run_for_finding(finding, thread_id=thread_id)

    return {
        "resource_name": resource_name,
        "finding": result["finding"],
        "investigation": result["investigation"],
        "recommendation": result["recommendation"],
        "status": "pending_human_approval",
        "thread_id": thread_id,
    }


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> dict:
    if name == "get_cost_anomalies":
        return {"anomalies": _get_cost_anomalies(arguments.get("team"))}
    elif name == "search_finops_policy":
        return {"results": _search_finops_policy(arguments["query"], arguments.get("top_k", 3))}
    elif name == "propose_rightsizing":
        return _propose_rightsizing(arguments["resource_name"])
    else:
        raise ValueError(f"Unknown tool: {name}")


async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
