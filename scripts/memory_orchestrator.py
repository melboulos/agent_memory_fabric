#!/usr/bin/env python3
"""
memory_orchestrator.py

Implements the full pipeline from the design spec (Sections 9-11):

    classify (evidence)  ->  route (LLM decision)  ->  retrieve (per memory
    type)  ->  fuse (normalize + provenance-link + rank + threshold)  ->
    reason (LLM answer, grounded only in the fused evidence)

Semantic importance is computed from a TRUE cosine similarity against the
raw embedding vectors, not Couchbase's blended FTS relevance score --
this keeps it comparable to other memory types' importance values instead
of being structurally capped low by an unrelated scoring scale. Evidence
below MIN_IMPORTANCE_THRESHOLD is dropped before Stage 5 ever sees it, so
irrelevant retrieved evidence (e.g. a different customer's fact that
still matched a vector search) can't reach the reasoning LLM regardless
of how careful that LLM's own prompt discipline is.

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
import time
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

# Below this, evidence is dropped before it ever reaches the reasoning LLM
# (design spec Section 11 intends Context Fusion to hand the LLM curated
# evidence, not everything retrieval happened to return). This is a second,
# structural safeguard -- it does not rely on the reasoning LLM's own
# judgment as the only thing keeping irrelevant evidence out of an answer.
MIN_IMPORTANCE_THRESHOLD = 0.15


def cosine_similarity(vec_a, vec_b):
    """True cosine similarity computed directly from raw embedding vectors,
    genuinely 0-1-ish for these models -- deliberately NOT the raw FTS
    relevance score, which is a blended score that empirically tops out
    around 0.3-0.4 even for a strong match and isn't comparable to the
    fixed-constant importance values used by other memory types."""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# Empirically observed range for amazon.titan-embed-text-v1 cosine similarity
# on short business-sentence pairs in this domain: ~0.1 for unrelated content,
# ~0.4 for a genuinely strong match (see test_vector_search.py's very first
# real result: 0.309 for a clearly correct match). Raw cosine similarity does
# NOT naturally span 0-1 for this model/domain -- without rescaling, semantic
# evidence is structurally capped around 0.3-0.4 raw, which can never compete
# against other memory types' fixed-constant importance values (long_term=1.0,
# short_term=0.7). This rescales it onto a comparable 0-1 scale before combining
# with confidence.
SIMILARITY_FLOOR = 0.1   # ~unrelated content
SIMILARITY_CEILING = 0.4  # ~genuinely strong match


def rescale_similarity(raw_similarity, floor=SIMILARITY_FLOOR, ceiling=SIMILARITY_CEILING):
    """Rescales raw cosine similarity onto a 0-1 scale comparable to other
    memory types' importance values, clamped at both ends. This is an
    empirical calibration for this embedding model and domain, not a
    universal constant -- worth re-checking if the embedding model changes
    or as more real semantic memory data accumulates."""
    if ceiling <= floor:
        return raw_similarity
    scaled = (raw_similarity - floor) / (ceiling - floor)
    return max(0.0, min(1.0, scaled))


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
def classify_intent(system_scope, question_vector):
    prefilter = BooleanFieldQuery(True, field="active")
    # 30 covers headroom above the current 25 patterns (5 per memory type);
    # bump this if the pattern set grows further.
    vector_query = VectorQuery("embedding", question_vector, num_candidates=30, prefilter=prefilter)
    vector_search = VectorSearch.from_vector_query(vector_query)
    request = SearchRequest.create(MatchNoneQuery()).with_vector_search(vector_search)

    result = system_scope.search(
        "memory_intent_patterns_vector_index",
        request,
        SearchOptions(fields=["memory_type", "logical_id"], limit=30),
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


def retrieve_semantic(cluster, scope, question_vector, bucket_name, k=5):
    prefilter = BooleanFieldQuery(True, field="active")
    vector_query = VectorQuery("embedding", question_vector, num_candidates=k, prefilter=prefilter)
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
            # Compute TRUE cosine similarity from the raw embedding vectors,
            # rather than using row.score (Couchbase's blended FTS relevance
            # score, which is not a clean 0-1 cosine similarity and isn't
            # comparable to other memory types' fixed-constant importance
            # values). See cosine_similarity() docstring for why this matters.
            similarity = cosine_similarity(question_vector, doc["embedding"])
            hits.append({"similarity_score": similarity, "fts_score": row.score, **doc})
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
            raw_similarity = fact.get("similarity_score", 0.0)
            rescaled_similarity = rescale_similarity(raw_similarity)
            confidence = fact.get("confidence", 0.5)
            importance = rescaled_similarity * confidence
            evidence.append({
                "evidence_id": f"ev_semantic_{fact.get('logical_id')}",
                "memory_type": "semantic",
                "content": fact.get("fact"),
                "evidence_type": "extracted_knowledge",
                "source_metadata": {
                    "logical_id": fact.get("logical_id"),
                    "raw_cosine_similarity": round(raw_similarity, 3),
                    "rescaled_similarity": round(rescaled_similarity, 3),
                    "fts_score": round(fact.get("fts_score", 0.0), 3),
                },
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
    """retrieval_results: dict of memory_type -> raw retriever output.
    Returns (kept_evidence, dropped_evidence) -- dropped items are anything
    below MIN_IMPORTANCE_THRESHOLD, filtered out here so Stage 5 never sees
    them, rather than relying on the reasoning LLM's own judgment as the
    only thing keeping low-relevance evidence out of an answer."""
    all_evidence = []
    for memory_type, raw in retrieval_results.items():
        all_evidence.extend(normalize_evidence(memory_type, raw))

    all_evidence = link_provenance(all_evidence)
    all_evidence.sort(key=lambda e: e["importance"], reverse=True)

    kept = [e for e in all_evidence if e["importance"] >= MIN_IMPORTANCE_THRESHOLD]
    dropped = [e for e in all_evidence if e["importance"] < MIN_IMPORTANCE_THRESHOLD]
    return kept, dropped


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

The evidence is listed in order of importance, highest first, with an
internal importance score next to each item -- that score is for YOUR
reasoning only. Never mention the words "importance" or any numeric score
in your answer; the person reading it should never see this internal
bookkeeping.

Synthesize across ALL of it into ONE cohesive narrative, not a separate
paragraph per evidence item. Give more weight to higher-importance
evidence, and connect related items into a single explanation rather than
listing them one after another (e.g. tie a compliance requirement and an
executive sign-off requirement together as "this is a strategic,
compliance-driven decision" rather than describing them in two disconnected
sentences).

Evidence (importance-ordered; scores are internal only, never repeat them):
{evidence_text}

Question: "{question}"

Answer:"""
    return invoke_llm(bedrock_client, llm_model_id, prompt, max_gen_len=400, temperature=0.3)


# Common words excluded when checking whether an evidence item's distinctive
# language shows up in the final answer -- without this, a word like "Acme"
# (present in almost every piece of evidence) would falsely count as
# "referenced" no matter which fact the answer actually used.
_COVERAGE_STOPWORDS = {
    "acme", "customer", "customers", "their", "with", "requires", "require",
    "concerns", "concern", "about", "current", "considering", "consideration",
    "because", "which", "would", "there", "these", "those",
}


def score_answer_coverage(evidence_list, answer_text):
    """Mechanical, non-LLM check of whether the final answer actually used
    the evidence it was given -- not a self-reported score from the model,
    which would be unreliable. For each evidence item, checks what fraction
    of its distinctive words (>4 chars, not in the stopword list above)
    appear in the answer text. An item is 'referenced' if at least 20% of
    its distinctive words show up.

    Returns both a simple coverage ratio (fraction of items referenced) and
    an importance-weighted coverage ratio (fraction of TOTAL IMPORTANCE that
    was actually referenced) -- the weighted version is the one that
    actually catches 'ignored the top-ranked fact', since missing a
    high-importance item should count for more than missing a low one."""
    answer_lower = answer_text.lower()
    per_item = []

    for e in evidence_list:
        words = [w.strip(".,;:\"'()") for w in e["content"].lower().split()]
        distinctive_words = [w for w in words if len(w) > 4 and w not in _COVERAGE_STOPWORDS]
        if not distinctive_words:
            per_item.append({"evidence_id": e["evidence_id"], "importance": e["importance"], "referenced": False, "match_ratio": 0.0})
            continue
        matches = sum(1 for w in distinctive_words if w in answer_lower)
        match_ratio = matches / len(distinctive_words)
        per_item.append({
            "evidence_id": e["evidence_id"],
            "importance": e["importance"],
            "referenced": match_ratio >= 0.2,
            "match_ratio": round(match_ratio, 3),
        })

    total_importance = sum(e["importance"] for e in evidence_list) or 1.0
    referenced_importance = sum(i["importance"] for i in per_item if i["referenced"])
    total_items = len(per_item) or 1

    return {
        "simple_coverage": round(sum(1 for i in per_item if i["referenced"]) / total_items, 3),
        "importance_weighted_coverage": round(referenced_importance / total_importance, 3),
        "per_item": per_item,
    }


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
def build_bedrock_client():
    """Creates a fresh Bedrock runtime client. Called once at CLI invocation,
    or once at FastAPI server startup -- never per-request."""
    bedrock_region = os.environ.get("BEDROCK_REGION")
    if not bedrock_region:
        sys.exit("Set BEDROCK_REGION before running.")
    return boto3.client("bedrock-runtime", region_name=bedrock_region)


def build_couchbase_cluster():
    """Connects to Couchbase and waits until ready. Called once at CLI
    invocation, or once at FastAPI server startup -- never per-request.
    Opening a new connection per request is both slow (every request pays
    the connection setup cost) and fragile (a cold cluster can time out on
    the very first attempt, exactly like the wait_until_ready timeout seen
    earlier when running this script standalone)."""
    conn_str = os.environ.get("CB_CONN_STR")
    username = os.environ.get("CB_USERNAME")
    password = os.environ.get("CB_PASSWORD")
    ca_bundle = os.environ.get("CB_CA_BUNDLE")

    missing = [n for n, v in [("CB_CONN_STR", conn_str), ("CB_USERNAME", username), ("CB_PASSWORD", password)] if not v]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    auth = PasswordAuthenticator(username, password, cert_path=ca_bundle) if ca_bundle else PasswordAuthenticator(username, password)
    cluster = Cluster(conn_str, ClusterOptions(auth))
    cluster.wait_until_ready(timedelta(seconds=15))
    return cluster


def run(question, customer_id, session_id, cluster=None, bedrock_client=None):
    """cluster and bedrock_client are optional so the CLI (scripts/memory_orchestrator.py,
    invoked once per run) can keep building them fresh each time, while the
    FastAPI app builds them ONCE at server startup and passes the same
    connections into every request instead of reconnecting every time."""
    embed_model_id = os.environ.get("EMBED_MODEL_ID")
    llm_model_id = os.environ.get("LLM_MODEL_ID")
    if not all([embed_model_id, llm_model_id]):
        sys.exit("Set EMBED_MODEL_ID and LLM_MODEL_ID before running.")

    if bedrock_client is None:
        bedrock_client = build_bedrock_client()
    if cluster is None:
        cluster = build_couchbase_cluster()

    bucket_name = os.environ.get("CB_BUCKET", "agent_memory")
    knowledge_scope = cluster.bucket(bucket_name).scope("knowledge")
    system_scope = cluster.bucket(bucket_name).scope("system_intelligence")

    timings = {}
    t_total_start = time.perf_counter()

    print(f"\n{'='*70}\nQUESTION: {question}\n{'='*70}")

    # Embed the question ONCE and reuse it for both the classifier (Stage 1)
    # and semantic retrieval (Stage 3) -- these used to each call Bedrock
    # separately for the identical text, which was a real, measurable waste
    # (one full embedding round-trip, ~200-400ms, for nothing).
    t0 = time.perf_counter()
    question_vector = embed_text(bedrock_client, embed_model_id, question)
    timings["embed_question_ms"] = round((time.perf_counter() - t0) * 1000)

    # Stage 1: classify (evidence, not decision)
    print("\n--- Stage 1: Memory Attention Layer (classifier evidence) ---")
    t0 = time.perf_counter()
    classifier_candidates = classify_intent(system_scope, question_vector)
    timings["classify_ms"] = round((time.perf_counter() - t0) * 1000)
    for memory_type, score in classifier_candidates.items():
        print(f"  {memory_type:12s} {score:.3f}")

    # Stage 2: route (LLM decision)
    print("\n--- Stage 2: LLM Final Routing ---")
    t0 = time.perf_counter()
    llm_decision = route_memories(bedrock_client, llm_model_id, question, classifier_candidates)
    timings["route_ms"] = round((time.perf_counter() - t0) * 1000)
    for d in llm_decision:
        flag = "SELECTED" if d["selected"] else "skipped "
        print(f"  [{flag}] {d['memory']:12s} {d['reason']}")

    # Stage 3: retrieve only what was selected
    print("\n--- Stage 3: Retrieval ---")
    t0 = time.perf_counter()
    retrieval_results = {}
    selected_types = {d["memory"] for d in llm_decision if d["selected"]}

    if "long_term" in selected_types:
        retrieval_results["long_term"] = retrieve_long_term(cluster, bucket_name, customer_id)
        print(f"  long_term:  {'found' if retrieval_results['long_term'] else 'not found'}")
    if "episodic" in selected_types:
        retrieval_results["episodic"] = retrieve_episodic(cluster, bucket_name, customer_id)
        print(f"  episodic:   {len(retrieval_results['episodic'])} event(s)")
    if "semantic" in selected_types:
        retrieval_results["semantic"] = retrieve_semantic(cluster, knowledge_scope, question_vector, bucket_name)
        print(f"  semantic:   {len(retrieval_results['semantic'])} fact(s)")
    if "procedural" in selected_types:
        retrieval_results["procedural"] = retrieve_procedural(cluster, bucket_name, question)
        print(f"  procedural: {len(retrieval_results['procedural'])} playbook(s)")
    if "short_term" in selected_types:
        retrieval_results["short_term"] = retrieve_short_term(cluster, bucket_name, session_id)
        print(f"  short_term: {'found' if retrieval_results['short_term'] else 'not found'}")
    timings["retrieve_ms"] = round((time.perf_counter() - t0) * 1000)

    # Stage 4: fuse
    print("\n--- Stage 4: Context Fusion ---")
    t0 = time.perf_counter()
    evidence_list, dropped_evidence = fuse_context(retrieval_results)
    timings["fuse_ms"] = round((time.perf_counter() - t0) * 1000)
    for e in evidence_list:
        print(f"  [{e['memory_type']:10s} importance={e['importance']:.3f}] {e['content']}")
    if dropped_evidence:
        print(f"\n  Dropped ({len(dropped_evidence)}, below importance {MIN_IMPORTANCE_THRESHOLD}):")
        for e in dropped_evidence:
            print(f"    [{e['memory_type']:10s} importance={e['importance']:.3f}] {e['content']}")

    # Persist routing trace
    t0 = time.perf_counter()
    trace_id = write_routing_trace(cluster, bucket_name, session_id, question, classifier_candidates, llm_decision)
    timings["save_trace_ms"] = round((time.perf_counter() - t0) * 1000)
    print(f"\n  (routing trace saved: {trace_id})")

    # Stage 5: reason
    print("\n--- Stage 5: Reasoning ---")
    t0 = time.perf_counter()
    answer = generate_answer(bedrock_client, llm_model_id, question, evidence_list)
    timings["reason_ms"] = round((time.perf_counter() - t0) * 1000)
    print(f"\n{answer}\n")

    coverage = score_answer_coverage(evidence_list, answer)
    print(f"  Coverage: {coverage['simple_coverage']:.0%} of evidence items referenced, "
          f"{coverage['importance_weighted_coverage']:.0%} of total importance covered")
    for item in coverage["per_item"]:
        flag = "used" if item["referenced"] else "IGNORED"
        print(f"    [{flag:7s} importance={item['importance']:.3f}] {item['evidence_id']}")

    timings["total_ms"] = round((time.perf_counter() - t_total_start) * 1000)
    print(f"\n  Timings (ms): " + ", ".join(f"{k}={v}" for k, v in timings.items()))

    return {
        "classifier_candidates": classifier_candidates,
        "llm_decision": llm_decision,
        "evidence": evidence_list,
        "dropped_evidence": dropped_evidence,
        "answer": answer,
        "coverage": coverage,
        "trace_id": trace_id,
        "timings": timings,
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
