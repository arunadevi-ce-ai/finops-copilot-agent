"""
Hand-rolled stdio JSON-RPC client for mcp_server.py — proves tools/list
and tools/call work against real data, not mocks. Talks raw JSON-RPC
over the subprocess's stdin/stdout directly, rather than using the
SDK's ClientSession, so the wire protocol stays visible.
"""

import json
import os
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER_CMD = [sys.executable, "mcp_server.py"]


class StdioJsonRpcClient:
    def __init__(self):
        self.proc = subprocess.Popen(
            SERVER_CMD,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=PROJECT_ROOT,
        )
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _send(self, message: dict) -> None:
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def _recv(self) -> dict:
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError(f"server closed stdout unexpectedly. stderr:\n{self.proc.stderr.read()}")
        return json.loads(line)

    def request(self, method: str, params: dict | None = None) -> dict:
        req_id = self._next_id()
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        response = self._recv()
        assert response.get("id") == req_id, f"response id mismatch: {response}"
        return response

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self) -> dict:
        response = self.request("initialize", {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "raw-jsonrpc-test-client", "version": "0.1"},
        })
        self.notify("notifications/initialized")
        return response

    def close(self) -> None:
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture
def client():
    c = StdioJsonRpcClient()
    init_response = c.initialize()
    assert "result" in init_response, f"initialize failed: {init_response}"
    yield c
    c.close()


def test_tools_list_returns_all_three_tools_with_explicit_schemas(client):
    response = client.request("tools/list")
    assert "result" in response, response

    tools = {t["name"]: t for t in response["result"]["tools"]}
    print(f"\ntools/list -> {sorted(tools.keys())}")
    assert set(tools.keys()) == {"get_cost_anomalies", "search_finops_policy", "propose_rightsizing"}

    assert tools["get_cost_anomalies"]["inputSchema"]["properties"].keys() == {"team"}
    assert tools["search_finops_policy"]["inputSchema"]["required"] == ["query"]
    assert tools["propose_rightsizing"]["inputSchema"]["required"] == ["resource_name"]


def test_get_cost_anomalies_against_real_detector(client):
    response = client.request("tools/call", {"name": "get_cost_anomalies", "arguments": {}})
    assert "result" in response, response
    assert response["result"].get("isError", False) is False

    anomalies = response["result"]["structuredContent"]["anomalies"]
    print(f"\nget_cost_anomalies() -> {len(anomalies)} anomalies")
    for a in anomalies:
        print(f"  {a['resource_name']:28s} {a['category']:28s} team={a['team']}")

    # Matches detector.py's documented ground truth: 7 anomalies, including
    # the pd-payments-dr-volume-01 policy trap (naive detector flags it).
    resource_names = {a["resource_name"] for a in anomalies}
    assert len(anomalies) == 7
    assert "pd-payments-dr-volume-01" in resource_names


def test_get_cost_anomalies_filters_by_team(client):
    response = client.request("tools/call", {"name": "get_cost_anomalies", "arguments": {"team": "payments"}})
    anomalies = response["result"]["structuredContent"]["anomalies"]
    print(f"\nget_cost_anomalies(team='payments') -> {anomalies}")

    assert len(anomalies) == 1
    assert anomalies[0]["resource_name"] == "pd-payments-dr-volume-01"
    assert anomalies[0]["team"] == "payments"


def test_search_finops_policy_against_real_chroma(client):
    response = client.request("tools/call", {
        "name": "search_finops_policy",
        "arguments": {"query": "orphaned disk that turns out to be a disaster recovery volume", "top_k": 3},
    })
    assert "result" in response, response
    assert response["result"].get("isError", False) is False

    results = response["result"]["structuredContent"]["results"]
    print(f"\nsearch_finops_policy(...) top result: {results[0]['source_file']} :: {results[0]['section_title']}")

    assert results[0]["source_file"] == "policy-fin-07-orphaned-resources.md"
    assert results[0]["section_title"] == "Precedent"


def test_propose_rightsizing_runs_real_graph_to_interrupt(client):
    response = client.request("tools/call", {
        "name": "propose_rightsizing",
        "arguments": {"resource_name": "pd-payments-dr-volume-01"},
    })
    assert "result" in response, response
    assert response["result"].get("isError", False) is False

    proposal = response["result"]["structuredContent"]
    print(f"\npropose_rightsizing('pd-payments-dr-volume-01') -> {proposal['recommendation']}")

    assert proposal["status"] == "pending_human_approval"
    # Same trap case as test_orchestrator.py's real-graph test: proves the
    # MCP plumbing carries the policy-grounded outcome through unchanged,
    # not just that some response came back.
    assert proposal["recommendation"]["requires_manual_review"] is True
    assert "delet" not in proposal["recommendation"]["proposed_action"].lower()


def test_propose_rightsizing_errors_on_unknown_resource(client):
    response = client.request("tools/call", {
        "name": "propose_rightsizing",
        "arguments": {"resource_name": "does-not-exist"},
    })
    assert "result" in response, response
    print(f"\npropose_rightsizing('does-not-exist') -> {response['result']}")

    assert response["result"]["isError"] is True
