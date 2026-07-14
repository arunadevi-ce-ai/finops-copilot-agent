"""
Ingests the four FinOps policy docs in data/policies/ into an embedded
Chroma collection and exposes retrieve() for querying them.

Chunking splits on "## " section headers rather than a fixed word
window — each doc has real sections (When this applies / Required
action / Approved actions / Precedent), and a sliding window risks
cutting the precedent narrative across two chunks.

Uses Chroma's bundled default embedding model (dense, no API key). See
test_rag.py for how retrieval holds up under paraphrased queries.
"""

import glob
import os
import re

import chromadb

POLICIES_DIR = os.path.join(os.path.dirname(__file__), "data", "policies")
CHROMA_DIR = os.path.join(os.path.dirname(__file__), "data", "chroma")
COLLECTION_NAME = "finops_policies"

TITLE_RE = re.compile(r"^# (.+)$", re.MULTILINE)
SECTION_HEADER_RE = re.compile(r"^## (.+)$", re.MULTILINE)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def chunk_policy_doc(path: str) -> list[dict]:
    """One chunk per "## " section, heading plus body. The top-level
    "# POLICY-FIN-XX: Title" line becomes metadata on every chunk, not
    a section of its own."""
    with open(path, encoding="utf-8") as f:
        text = f.read()

    title_match = TITLE_RE.search(text)
    title = title_match.group(1).strip() if title_match else os.path.basename(path)
    policy_id = title.split(":")[0].strip()

    parts = re.split(r"\n(?=## )", text)
    chunks = []
    for part in parts:
        part = part.strip()
        if not part.startswith("## "):
            continue  # doc-title fragment before the first section header
        header_match = SECTION_HEADER_RE.match(part)
        section_title = header_match.group(1).strip()
        chunks.append({
            "text": part,
            "source_file": os.path.basename(path),
            "policy_id": policy_id,
            "policy_title": title,
            "section_title": section_title,
            "section_index": len(chunks),
        })
    return chunks


def get_client(persistent: bool = True):
    if persistent:
        os.makedirs(CHROMA_DIR, exist_ok=True)
        return chromadb.PersistentClient(path=CHROMA_DIR)
    return chromadb.EphemeralClient()


def ingest(client=None, policies_dir: str = POLICIES_DIR, collection_name: str = COLLECTION_NAME):
    """Chunk every *.md file in policies_dir and upsert into the
    collection. Ids are deterministic (file + section slug), so
    re-running this is idempotent instead of piling up duplicates."""
    client = client or get_client()
    collection = client.get_or_create_collection(collection_name)

    doc_paths = sorted(glob.glob(os.path.join(policies_dir, "*.md")))
    all_chunks = []
    for path in doc_paths:
        all_chunks.extend(chunk_policy_doc(path))

    if not all_chunks:
        return collection

    collection.upsert(
        ids=[f"{c['source_file']}::{_slugify(c['section_title'])}" for c in all_chunks],
        documents=[c["text"] for c in all_chunks],
        metadatas=[
            {
                "source_file": c["source_file"],
                "policy_id": c["policy_id"],
                "policy_title": c["policy_title"],
                "section_title": c["section_title"],
                "section_index": c["section_index"],
            }
            for c in all_chunks
        ],
    )
    return collection


def get_document_chunks(source_file: str, client=None, collection_name: str = COLLECTION_NAME) -> list[dict]:
    """All chunks for one policy doc, in section order. A doc's
    Precedent section is often not the closest semantic match to a
    query about the surface anomaly, so chunk-level top-k can miss it.
    Once retrieve() has found the right document, pull the whole thing
    with this instead of trusting top-k to have surfaced every section."""
    client = client or get_client()
    collection = client.get_collection(collection_name)
    results = collection.get(where={"source_file": source_file})

    chunks = [
        {
            "text": doc,
            "source_file": meta["source_file"],
            "policy_id": meta["policy_id"],
            "section_title": meta["section_title"],
            "section_index": meta["section_index"],
        }
        for doc, meta in zip(results["documents"], results["metadatas"])
    ]
    chunks.sort(key=lambda c: c["section_index"])
    return chunks


def retrieve(query: str, top_k: int = 3, client=None, collection_name: str = COLLECTION_NAME) -> list[dict]:
    client = client or get_client()
    collection = client.get_collection(collection_name)
    results = collection.query(query_texts=[query], n_results=top_k)

    retrieved = []
    for doc, meta, distance in zip(
        results["documents"][0], results["metadatas"][0], results["distances"][0]
    ):
        retrieved.append({
            "text": doc,
            "source_file": meta["source_file"],
            "policy_id": meta["policy_id"],
            "section_title": meta["section_title"],
            "distance": distance,
        })
    return retrieved


def main():
    client = get_client()
    collection = ingest(client=client)
    print(f"Ingested {collection.count()} chunks into '{COLLECTION_NAME}' at {CHROMA_DIR}")

    query = "orphaned disk that turns out to be a disaster recovery volume"
    print(f"\nQuery: {query!r}")
    for r in retrieve(query, top_k=3, client=client):
        print(f"  [{r['distance']:.4f}] {r['source_file']} :: {r['section_title']}")


if __name__ == "__main__":
    main()
