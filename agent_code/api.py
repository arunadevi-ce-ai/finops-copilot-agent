"""
FastAPI wrapper around detector.py and orchestrator.py.

Requires REDIS_URL to be set (unlike orchestrator.py, where it's
optional). The /propose -> /confirm flow stores its one-time approval
token in Redis with no in-memory fallback, so a different request
(potentially a different pod) can complete the approval. Token storage
is shared with events/consumer_orchestrator.py via approvals.py, so a
proposal made through Kafka can be confirmed through this same
/confirm endpoint.
"""

import json
import os
import uuid

from fastapi import FastAPI, HTTPException
from prometheus_client import make_asgi_app
from pydantic import BaseModel

import approvals
import detector
import orchestrator

app = FastAPI(title="FinOps Copilot API")
app.mount("/metrics", make_asgi_app())  # one process, one port — see metrics.py


class ProposeRequest(BaseModel):
    resource_name: str


class ConfirmRequest(BaseModel):
    token: str
    approved: bool
    approver: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/anomalies")
def get_anomalies(team: str | None = None):
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
    return {"anomalies": results}


@app.post("/propose")
def propose(body: ProposeRequest):
    findings = detector.run_detector()
    finding = next((f for f in findings if f.resource_name == body.resource_name), None)
    if finding is None:
        known = sorted(f.resource_name for f in findings)
        raise HTTPException(
            status_code=404,
            detail=f"No current anomaly finding for resource_name={body.resource_name!r}. "
                   f"Currently anomalous resources: {known}",
        )

    thread_id = f"api-propose-{body.resource_name}-{uuid.uuid4().hex[:8]}"
    _, result, _ = orchestrator.run_for_finding(finding, thread_id=thread_id)

    # The token, not the thread_id, is what a client gets back — the
    # thread_id is an internal LangGraph identifier, not something to
    # expose or let a caller pick.
    token = approvals.create_token(body.resource_name, thread_id, result["recommendation"])

    return {
        "resource_name": body.resource_name,
        "finding": result["finding"],
        "investigation": result["investigation"],
        "recommendation": result["recommendation"],
        "approval_token": token,
        "expires_in_seconds": approvals.TOKEN_TTL_SECONDS,
    }


@app.post("/confirm")
def confirm(body: ConfirmRequest):
    # GETDEL: atomic read-and-delete, so a token can't be used twice
    # even under a race between two concurrent /confirm calls.
    raw = approvals.redis_client().getdel(f"finops:token:{body.token}")
    if raw is None:
        raise HTTPException(status_code=410, detail="Approval token not found, already used, or expired")

    payload = json.loads(raw)
    final_state = orchestrator.resume_decision(
        payload["thread_id"],
        {"approved": body.approved, "approver": body.approver},
    )

    return {
        "resource_name": payload["resource_name"],
        "human_decision": final_state.get("human_decision"),
        "recommendation": final_state.get("recommendation"),
        "status": "resumed",
    }
