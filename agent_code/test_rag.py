"""
Tests rag.py's chunking and retrieve() against a fresh in-memory Chroma
collection (EphemeralClient — separate from the data/chroma/ store
rag.py's main() writes to).

Uses real dense embeddings. The paraphrase tests check whether
retrieval still finds the right doc when a query shares no vocabulary
with the policy text — printed either way, not just asserted.
"""

import chromadb
import pytest

from rag import POLICIES_DIR, chunk_policy_doc, ingest, retrieve


@pytest.fixture(scope="module")
def chroma_client():
    client = chromadb.EphemeralClient()
    ingest(client=client, policies_dir=POLICIES_DIR)
    return client


def _print_results(query: str, results: list[dict]) -> None:
    print(f"\nQuery: {query!r}")
    for rank, r in enumerate(results, start=1):
        preview = r["text"].splitlines()[0][:70]
        print(f"  #{rank} [distance={r['distance']:.4f}] {r['source_file']} :: "
              f"{r['section_title']}  ({preview}...)")


def test_chunking_splits_each_doc_on_section_headers():
    chunks = chunk_policy_doc(f"{POLICIES_DIR}/policy-fin-07-orphaned-resources.md")
    section_titles = [c["section_title"] for c in chunks]

    assert section_titles == [
        "When this applies",
        "Required action before proposing a change",
        "Approved actions",
        "Precedent",
    ]
    assert all(c["policy_id"] == "POLICY-FIN-07" for c in chunks)
    assert all(c["source_file"] == "policy-fin-07-orphaned-resources.md" for c in chunks)


def test_ingest_produces_four_chunks_per_doc(chroma_client):
    assert chroma_client.get_collection("finops_policies").count() == 16  # 4 docs x 4 sections


def test_dr_volume_query_retrieves_orphaned_resources_precedent(chroma_client):
    query = "orphaned disk that turns out to be a disaster recovery volume"
    results = retrieve(query, top_k=3, client=chroma_client)
    _print_results(query, results)

    top = results[0]
    assert top["source_file"] == "policy-fin-07-orphaned-resources.md"
    assert top["section_title"] == "Precedent"


def test_idle_gpu_query_retrieves_idle_gpu_policy(chroma_client):
    query = "GPU instance left running at low utilization after a training job finished"
    results = retrieve(query, top_k=3, client=chroma_client)
    _print_results(query, results)

    top = results[0]
    assert top["source_file"] == "policy-fin-11-idle-gpu.md"


def test_paraphrased_dr_volume_query(chroma_client):
    """Different wording, no shared vocabulary with the policy doc's own
    phrasing. Only asserts the right doc appears somewhere in top_k, not
    that it's still #1 — that's the open question."""
    query = "we unplugged a volume from its VM but it's actually kept around for quarterly failover drills"
    results = retrieve(query, top_k=4, client=chroma_client)
    _print_results(query, results)

    source_files = [r["source_file"] for r in results]
    assert "policy-fin-07-orphaned-resources.md" in source_files, (
        "paraphrased DR-volume query failed to retrieve policy-fin-07 at all "
        f"within top_k={len(results)}"
    )


def test_paraphrased_idle_gpu_query(chroma_client):
    query = "our accelerator hardware finished its job hours ago but nobody shut it down"
    results = retrieve(query, top_k=4, client=chroma_client)
    _print_results(query, results)

    source_files = [r["source_file"] for r in results]
    assert "policy-fin-11-idle-gpu.md" in source_files, (
        "paraphrased idle-GPU query failed to retrieve policy-fin-11 at all "
        f"within top_k={len(results)}"
    )
