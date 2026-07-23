#!/usr/bin/env python3
"""
memory_orchestrator.py

Implements the full pipeline from the design spec (Sections 9-11):

    classify (evidence)  ->  route (LLM decision)  ->  retrieve (per memory
    type)  ->  fuse (normalize + provenance-link + rank)  ->  reason (LLM
    answer, grounded only in the fused evidence)

Every stage prints its output, so running this from the command line is a
text-mode version of "watch the agent think" -- the same data a Memory
Inspector UI would render, just as stdout instead of a widget.

This does NOT do document extraction/ingestion (see design spec's explicit
v1 scope: "Documents are ingested and processed into memory representations
... the demo focuses on retrieval, orchestration, and reasoning"). It reads
existing customers/events/semantic_memory/playbooks/short_term_memory --
it does not create them.

Usage:
  python scripts/memory_orchestrator.py \\
      --question "Why are they considering migration?" \\
      --customer-id acme_001 \\
      --session-id session_123

Env vars: same as embed_seed_data.py / test_vector_search.py --
  CB_CONN_STR, CB_USERNAME, CB_PASSWORD, CB_BUCKET (optional), CB_CA_BUNDLE (optional)
  BEDROCK_REGION, EMBED_MODEL_ID, LLM_MODEL_ID

Requirements:
  pip install boto3 couchbase --break-system-packages
"""

import argparse
import json
import math
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.exceptions import DocumentNotFoundException
from couchbase.options import ClusterOptions, SearchOptions
from couchbase.search import BooleanFieldQuery, MatchNoneQuery, SearchRequest
from couchbase.vector_search import VectorQuery, VectorSearch

MEMORY_TYPES = ["long_term", "episodic", "semantic", "procedural", "short_term"]


# ===========================================================================
# Bedrock helpers
# ===========================================================================
def embed_text(bedrock_client, embed_model_id: str, text: str):
    body = json.dumps({"inputText": text})
    response = bedrock_client.invoke_model(
        modelId=embed_model_id, body=body, accept="application/json", contentType="application/json"
    )
    return json.loads(response["body"].read())["embedding"]


def invoke_llm(bedrock_client, llm_model_id: str, prompt: str, max_gen_len=800, temperature=0.2):
    """Calls a Bedrock Llama 3 model with the required prompt formatting."""
    formatted_prompt = (
        f"<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n"
        f"{prompt}\n<|eot_id|>\n<|start_header_id|>assistant<|end_header_id|>\n"
    )
    body = json.dumps({
        "prompt": formatted_prompt,
        "max_gen_len": max_gen_len,
        "temperature": temperature,
        "top_p": 0.9,
    })
    response = bedrock_client.invoke_model(
        modelId=llm_model_id, body=body, accept="application/json", contentType="application/json"
    )
    return json.loads(response["body"].read())["generation"].strip()


def parse_json_from_llm(raw_text: str):
    """LLMs love wrapping JSON in prose or markdown fences -- extract the
    first {...} or [...] block and parse it, rather than assuming raw_text
    is pure JSON."""
    text = raw_text.strip()
    for fence in ("```json", "```"):
        if text.startswith(fence):
            text = text[len(fence):]
        if text.endswith("```"):
            text = text[: -len("```")]
    text = text.strip()

    start_candidates = [i for i in (text.find("["), text.find("{")) if i != -1]
    if not start_candidates:
        raise ValueError(f"No JSON found in LLM output: {raw_text!r}")
    start = min(start_candidates)
    end = max(text.rfind("]"), text.rfind("}")) + 1
    return json.loads(text[start:end])


# ===========================================================================
# Stage 1: Memory Attention Layer -- classifier generates EVIDENCE, not a
# decision (design spec Section 9.1). Embeds the question, vector-searches
# memory_intent_patterns with an active=true pre-filter, and aggregates
# similarity scores per memory_type (max score per type, since a type may
# have multiple labeled examples).
# ===========================================================================
def classify_intent(scope, bedrock_client, embed_model_id, question):
    vector = embed_text(bedrock_client, embed_model_id, question)

    prefilter = BooleanFieldQuery(True, field="active")
    vector_query = VectorQuery("embedding", vector, num_candidates=20, prefilter=prefilter)
    vector_search = VectorSearch.from_vector_query(vector_query)
    request = SearchRequest.create(MatchNoneQuery()).with_vector_search(vector_search)

    result = scope.search(
        "memory_intent_patterns_vector_index",
        request,
        SearchOptions(fields=["memory_type", "logical_id"], limit=20),
    )

    candidates = {memory_type: 0.0 for memory_type in MEMORY_TYPES}
    for row in result.rows():
        memory_type = row.fields.get("memory_type")
        if memory_type in candidates:
            candidates[memory_type] = max(candidates[memory_type], row.score)

    return candidates


# ===========================================================================
# Stage 2: LLM final routing (design spec Section 9.2). The classifier's
# scores are evidence, not a decision -- the LLM decides which memory
# systems to actually query, and can select more than one.
# ===========================================================================
def route_memories(bedrock_client, llm_model_id, question, classifier_candidates):
    scores_text = "\n".join(f"  - {k}: {v:.3f}" for k, v in classifier_candidates.items())
    prompt = f"""You are the routing layer of an AI agent's memory system. You do not
answer the user's question -- you only decide which memory systems should
be queried to gather evidence for it.

Memory types and what they hold:
  - long_term: stable customer/account identity (who is this customer, industry, tier)
  - episodic: historical events and meetings (what happened, when, who was there)
  - semantic: learned facts and concepts extracted from conversations (why something is true)
  - procedural: playbooks and workflows (how to approach a situation)
  - short_term: current conversation/session context

A classifier has produced similarity scores between the question and
labeled examples of each memory type (higher = more similar, 0 to 1 range,
not a probability):
{scores_text}

Question: "{question}"

Decide which memory type(s) should be queried. You may select more than
one if the question genuinely needs multiple kinds of evidence. Respond
with ONLY a JSON array, no other text, in this exact shape:
[
  {{"memory": "long_term", "selected": true, "reason": "..."}},
  {{"memory": "episodic", "selected": false, "reason": "..."}},
  ...
]
Include an entry for every memory type listed above."""

    raw = invoke_llm(bedrock_client, llm_model_id, prompt, max_gen_len=500, temperature=0.1)
    decision = parse_json_from_llm(raw)

    # Defensive normalization: ensure every memory type has an entry, in
    # case the LLM dropped one -- default to not-selected rather than crash.
    decision_by_type = {d["memory"]: d for d in decision if "memory" in d}
    for memory_type in MEMORY_TYPES:
        if memory_type not in decision_by_type:
            decision_by_type[memory_type] = {
                "memory": memory_type,
                "selected": False,
                "reason": "(no decision returned by LLM; defaulted to not selected)",
            }
    return [decision_by_type[t] for t in MEMORY_TYPES]


# ===========================================================================
# Stage 3: Per-memory-type retrieval
# ===========================================================================
def retrieve_long_term(cluster, bucket_name, customer_id):
    if not customer_id:
        return None
    try:
        collection = cluster.bucket(bucket_name).scope("knowledge").collection("customers")
        return collection.get(customer_id).content_as[dict]
    except DocumentNotFoundException:
        return None


def retrieve_episodic(cluster, bucket_name, customer_id, limit=5):
    if not customer_id:
        return []
    query = f"""
        SELECT e.*
        FROM `{bucket_name}`.`knowledge`.`events` AS e
        WHERE e.account = $customer_id
        ORDER BY e.`timestamp` DESC
        LIMIT {limit}
    """
    result = cluster.query(query, customer_id=customer_id)
    return list(result)


def retrieve_semantic(cluster, scope, bedrock_client, embed_model_id, bucket_name, question, k=5):
    vector = embed_text(bedrock_client, embed_model_id, question)
    prefilter = BooleanFieldQuery(True, field="active")
    vector_query = VectorQuery("embedding", vector, num_candidates=k, prefilter=prefilter)
    vector_search = VectorSearch.from_vector_query(vector_query)
    request = SearchRequest.create(MatchNoneQuery()).with_vector_search(vector_search)
    result = scope.search(
        "semantic_memory_vector_index",
        request,
        SearchOptions(fields=["logical_id"], limit=k),
    )

    # Search index only stores the fields we mapped (logical_id, active,
    # generation) -- fetch the full document via KV for fact/content/confidence.
    collection = cluster.bucket(bucket_name).scope("knowledge").collection("semantic_memory")
    hits = []
    for row in result.rows():
        try:
            doc = collection.get(row.id).content_as[dict]
            hits.append({"similarity_score": row.score, **doc})
        except DocumentNotFoundException:
            continue
    return hits


def retrieve_procedural(cluster, bucket_name, question):
    query = f"SELECT * FROM `{bucket_name}`.`knowledge`.`playbooks`"
    result = cluster.query(query)
    question_lower = question.lower()

    matches = []
    for row in result:
        playbook = row.get("playbooks", row)
        trigger_conditions = playbook.get("trigger_conditions", [])
        matched = [t for t in trigger_conditions if t.lower() in question_lower]
        if matched:
            match_ratio = len(matched) / len(trigger_conditions) if trigger_conditions else 0
            matches.append({"match_ratio": match_ratio, "matched_conditions": matched, **playbook})
    return matches


def retrieve_short_term(cluster, bucket_name, session_id):
    if not session_id:
        return None
    try:
        collection = cluster.bucket(bucket_name).scope("agent").collection("short_term_memory")
        return collection.get(session_id).content_as[dict]
    except DocumentNotFoundException:
        return None


# ===========================================================================
# Stage 4: Context Fusion (design spec Section 11) -- normalize each
# memory type's native shape into a common Evidence Object, link
# provenance ONLY via explicit shared source pointers (v1 dedup scope),
# score importance per-type, and rank.
# ===========================================================================
def normalize_evidence(memory_type, raw_results):
    evidence = []

    if memory_type == "long_term" and raw_results:
        doc = raw_results
        evidence.append({
            "evidence_id": f"ev_long_term_{doc.get('customer_id')}",
            "memory_type": "long_term",
            "content": f"{doc.get('name')} -- {doc.get('tier')} account, {doc.get('industry')}",
            "evidence_type": "customer_profile",
            "source_metadata": {"customer_id": doc.get("customer_id")},
            "importance": 1.0,  # authoritative, per design spec Section 11.4 v1 default
            "reason_selected": "Direct customer identity lookup",
            "source_event_id": None,
            "source_document_id": None,
        })

    elif memory_type == "episodic":
        for event in raw_results:
            days_old = _days_old(event.get("timestamp"))
            recency = math.exp(-days_old / 30.0) if days_old is not None else 0.5
            relevance = 0.85  # v1 heuristic placeholder -- see note below
            importance = 0.6 * relevance + 0.4 * recency
            evidence.append({
                "evidence_id": f"ev_episodic_{event.get('event_id')}",
                "memory_type": "episodic",
                "content": event.get("summary"),
                "evidence_type": "historical_event",
                "source_metadata": {"event_id": event.get("event_id"), "timestamp": event.get("timestamp")},
                "importance": round(importance, 3),
                "reason_selected": "Matches account timeline",
                "source_event_id": event.get("event_id"),
                "source_document_id": event.get("source_document_id"),
            })

    elif memory_type == "semantic":
        for fact in raw_results:
            similarity = fact.get("similarity_score", 0.0)
            confidence = fact.get("confidence", 0.5)
            importance = similarity * confidence
            evidence.append({
                "evidence_id": f"ev_semantic_{fact.get('logical_id')}",
                "memory_type": "semantic",
                "content": fact.get("fact"),
                "evidence_type": "extracted_knowledge",
                "source_metadata": {"logical_id": fact.get("logical_id"), "similarity": round(similarity, 3)},
                "importance": round(importance, 3),
                "reason_selected": "Conceptual similarity match",
                "source_event_id": fact.get("source_event_id"),
                "source_document_id": fact.get("source_document_id"),
            })

    elif memory_type == "procedural":
        for playbook in raw_results:
            evidence.append({
                "evidence_id": f"ev_procedural_{playbook.get('playbook_id')}",
                "memory_type": "procedural",
                "content": f"{playbook.get('playbook_id')}: {', '.join(playbook.get('steps', []))}",
                "evidence_type": "playbook",
                "source_metadata": {"matched_conditions": playbook.get("matched_conditions")},
                "importance": round(playbook.get("match_ratio", 0.0), 3),
                "reason_selected": f"Trigger conditions matched: {playbook.get('matched_conditions')}",
                "source_event_id": None,
                "source_document_id": None,
            })

    elif memory_type == "short_term" and raw_results:
        doc = raw_results
        evidence.append({
            "evidence_id": f"ev_short_term_{doc.get('session_id')}",
            "memory_type": "short_term",
            "content": doc.get("conversation_summary"),
            "evidence_type": "session_context",
            "source_metadata": {"session_id": doc.get("session_id")},
            "importance": 0.7,  # v1 heuristic -- recent conversation context, moderate weight
            "reason_selected": "Active session context",
            "source_event_id": None,
            "source_document_id": None,
        })

    return evidence


def _days_old(timestamp_str):
    if not timestamp_str:
        return None
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        return max((datetime.now(timezone.utc) - ts).days, 0)
    except ValueError:
        return None


def link_provenance(evidence_list):
    """v1 dedup rule (design spec Section 11.3): merge evidence ONLY when
    an explicit, shared source pointer exists (same source_event_id).
    Content-based similarity clustering is explicitly out of scope for v1."""
    by_source_event = {}
    standalone = []

    for item in evidence_list:
        key = item.get("source_event_id")
        if key:
            by_source_event.setdefault(key, []).append(item)
        else:
            standalone.append(item)

    merged = []
    for source_event_id, items in by_source_event.items():
        if len(items) == 1:
            merged.append(items[0])
            continue
        # Multiple evidence items share the same source event -- merge
        # provenance, keep the highest importance, note both contributions.
        best = max(items, key=lambda i: i["importance"])
        merged.append({
            **best,
            "evidence_type": "merged_provenance",
            "content": " | ".join(dict.fromkeys(i["content"] for i in items if i.get("content"))),
            "source_metadata": {"merged_from": [i["evidence_id"] for i in items], "source_event_id": source_event_id},
            "reason_selected": f"Merged {len(items)} evidence items sharing source_event_id={source_event_id}",
        })

    return merged + standalone


def fuse_context(retrieval_results):
    """retrieval_results: dict of memory_type -> raw retriever output."""
    all_evidence = []
    for memory_type, raw in retrieval_results.items():
        all_evidence.extend(normalize_evidence(memory_type, raw))

    all_evidence = link_provenance(all_evidence)
    all_evidence.sort(key=lambda e: e["importance"], reverse=True)
    return all_evidence


# ===========================================================================
# Stage 5: Reasoning -- final LLM call, grounded ONLY in the fused evidence
# ===========================================================================
def generate_answer(bedrock_client, llm_model_id, question, evidence_list):
    if not evidence_list:
        evidence_text = "(no evidence retrieved)"
    else:
        evidence_text = "\n".join(
            f"  [{e['memory_type']}, importance={e['importance']}] {e['content']}" for e in evidence_list
        )

    prompt = f"""You are a sales engineering assistant. Answer the question using ONLY
the evidence below. Do not invent facts not present in the evidence. If the
evidence is insufficient, say so explicitly.

Evidence:
{evidence_text}

Question: "{question}"

Answer:"""
    return invoke_llm(bedrock_client, llm_model_id, prompt, max_gen_len=400, temperature=0.3)


# ===========================================================================
# Persistence: routing_traces (per design spec Section 9.3)
# ===========================================================================
def write_routing_trace(cluster, bucket_name, session_id, question, classifier_candidates, llm_decision):
    collection = cluster.bucket(bucket_name).scope("system_intelligence").collection("routing_traces")
    trace_id = f"trace_{uuid.uuid4().hex[:12]}"
    doc = {
        "trace_id": trace_id,
        "session_id": session_id,
        "question": question,
        "classifier_candidates": [{"memory": k, "score": round(v, 3)} for k, v in classifier_candidates.items()],
        "llm_decision": llm_decision,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    collection.upsert(trace_id, doc)
    return trace_id


# ===========================================================================
# Orchestration entry point
# ===========================================================================
def run(question, customer_id, session_id):
    bedrock_region = os.environ.get("BEDROCK_REGION")
    embed_model_id = os.environ.get("EMBED_MODEL_ID")
    llm_model_id = os.environ.get("LLM_MODEL_ID")
    if not all([bedrock_region, embed_model_id, llm_model_id]):
        sys.exit("Set BEDROCK_REGION, EMBED_MODEL_ID, and LLM_MODEL_ID before running.")

    bedrock_client = boto3.client("bedrock-runtime", region_name=bedrock_region)

    conn_str = os.environ.get("CB_CONN_STR")
    username = os.environ.get("CB_USERNAME")
    password = os.environ.get("CB_PASSWORD")
    bucket_name = os.environ.get("CB_BUCKET", "agent_memory")
    ca_bundle = os.environ.get("CB_CA_BUNDLE")

    missing = [n for n, v in [("CB_CONN_STR", conn_str), ("CB_USERNAME", username), ("CB_PASSWORD", password)] if not v]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    auth = PasswordAuthenticator(username, password, cert_path=ca_bundle) if ca_bundle else PasswordAuthenticator(username, password)
    cluster = Cluster(conn_str, ClusterOptions(auth))
    cluster.wait_until_ready(timedelta(seconds=15))
    knowledge_scope = cluster.bucket(bucket_name).scope("knowledge")

    print(f"\n{'='*70}\nQUESTION: {question}\n{'='*70}")

    # Stage 1: classify (evidence, not decision)
    print("\n--- Stage 1: Memory Attention Layer (classifier evidence) ---")
    classifier_candidates = classify_intent(knowledge_scope, bedrock_client, embed_model_id, question)
    for memory_type, score in classifier_candidates.items():
        print(f"  {memory_type:12s} {score:.3f}")

    # Stage 2: route (LLM decision)
    print("\n--- Stage 2: LLM Final Routing ---")
    llm_decision = route_memories(bedrock_client, llm_model_id, question, classifier_candidates)
    for d in llm_decision:
        flag = "SELECTED" if d["selected"] else "skipped "
        print(f"  [{flag}] {d['memory']:12s} {d['reason']}")

    # Stage 3: retrieve only what was selected
    print("\n--- Stage 3: Retrieval ---")
    retrieval_results = {}
    selected_types = {d["memory"] for d in llm_decision if d["selected"]}

    if "long_term" in selected_types:
        retrieval_results["long_term"] = retrieve_long_term(cluster, bucket_name, customer_id)
        print(f"  long_term:  {'found' if retrieval_results['long_term'] else 'not found'}")
    if "episodic" in selected_types:
        retrieval_results["episodic"] = retrieve_episodic(cluster, bucket_name, customer_id)
        print(f"  episodic:   {len(retrieval_results['episodic'])} event(s)")
    if "semantic" in selected_types:
        retrieval_results["semantic"] = retrieve_semantic(
            cluster, knowledge_scope, bedrock_client, embed_model_id, bucket_name, question
        )
        print(f"  semantic:   {len(retrieval_results['semantic'])} fact(s)")
    if "procedural" in selected_types:
        retrieval_results["procedural"] = retrieve_procedural(cluster, bucket_name, question)
        print(f"  procedural: {len(retrieval_results['procedural'])} playbook(s)")
    if "short_term" in selected_types:
        retrieval_results["short_term"] = retrieve_short_term(cluster, bucket_name, session_id)
        print(f"  short_term: {'found' if retrieval_results['short_term'] else 'not found'}")

    # Stage 4: fuse
    print("\n--- Stage 4: Context Fusion ---")
    evidence_list = fuse_context(retrieval_results)
    for e in evidence_list:
        print(f"  [{e['memory_type']:10s} importance={e['importance']:.3f}] {e['content']}")

    # Persist routing trace
    trace_id = write_routing_trace(cluster, bucket_name, session_id, question, classifier_candidates, llm_decision)
    print(f"\n  (routing trace saved: {trace_id})")

    # Stage 5: reason
    print("\n--- Stage 5: Reasoning ---")
    answer = generate_answer(bedrock_client, llm_model_id, question, evidence_list)
    print(f"\n{answer}\n")

    return {
        "classifier_candidates": classifier_candidates,
        "llm_decision": llm_decision,
        "evidence": evidence_list,
        "answer": answer,
        "trace_id": trace_id,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", required=True)
    parser.add_argument("--customer-id", default=None, help="e.g. acme_001")
    parser.add_argument("--session-id", default=None, help="e.g. session_123")
    args = parser.parse_args()
    run(args.question, args.customer_id, args.session_id)


if __name__ == "__main__":
    main()
