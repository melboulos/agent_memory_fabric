# Agent Memory Fabric

A Couchbase Capella AIDP demo showcasing **agent memory** — not just RAG. The
application visualizes how six distinct memory types (working, short-term,
long-term, semantic, episodic, procedural) collaborate to answer questions
and execute tasks, with a live "Memory Inspector" that shows exactly which
memory system contributed which evidence, and why.

## Repository layout

```
agent_memory_fabric/
├── docs/
│   └── Capella_AIDP_Agent_Memory_Design_Spec_v2.docx   # Full architecture spec
├── sql/
│   └── capella_agent_memory_setup.sql                  # Scopes, collections,
│                                                        # GSIs, seed data,
│                                                        # vector index reference
├── scripts/
│   └── embed_seed_data.py                              # Generates real
│                                                        # embeddings for the
│                                                        # seed documents
└── requirements.txt
```

## Architecture at a glance

Three Capella scopes:

- **agent** — ephemeral cognition (`working_memory`, `short_term_memory`)
- **knowledge** — accumulated organizational knowledge (`source_documents`,
  `customers`, `events`, `semantic_memory`, `playbooks`)
- **system_intelligence** — configuration, routing, and observability, not
  memory (`memory_intent_patterns`, `routing_traces`, `memory_audit`)

Routing is two-stage: a fast embedding-based classifier generates *evidence*
(candidate memory types + confidence scores), and the reasoning LLM performs
*final routing* over that evidence — the classifier never makes the decision
itself. Retrieved memories are normalized into a common Evidence Object by a
Context Fusion layer before reaching the LLM, with explainable per-type
importance scoring.

Vector-backed collections (`semantic_memory`, `memory_intent_patterns`) use
immutable embedding generations with an `active` flag — never overwritten in
place — and Capella Search indexes pre-filter on `active` at query time so
stale generations never crowd out current results.

Full details, including the failure modes each of these decisions were
made to avoid, are in `docs/Capella_AIDP_Agent_Memory_Design_Spec_v2.docx`.

## Setup

### 1. Provision the data model

Create the `agent_memory` bucket via the Capella UI (Query Workbench can't
create buckets), then paste `sql/capella_agent_memory_setup.sql` into
Query Workbench, section by section, in order.

Section 6 of that file is JSON reference for the two Search Vector Indexes
(`semantic_memory_vector_index`, `memory_intent_patterns_vector_index`) —
these must be created via Capella UI → Data Tools → Search, or the Search
Service REST API, not SQL++.

### 2. Generate real embeddings for the seed data

The SQL script inserts two vector-backed seed documents with placeholder
empty embeddings. Populate them with real vectors:

```bash
pip install -r requirements.txt

export OPENAI_API_KEY=...
export CB_CONN_STR="couchbases://<your-cluster>.cloud.couchbase.com"
export CB_USERNAME=...
export CB_PASSWORD=...
export CB_BUCKET=agent_memory

python scripts/embed_seed_data.py --dry-run   # sanity check, writes nothing
python scripts/embed_seed_data.py             # writes real vectors
```

### 3. Application layer (in progress)

Not yet in this repo: the Memory Orchestrator (classifier + LLM routing +
Context Fusion) and the Memory Inspector / Agent Brain visualization UI.
Tracked as the next milestones — see design spec Sections 9–11 for the
locked architecture these will implement.

## Status

- [x] Design spec (v2, locked)
- [x] Capella data model (scopes, collections, GSIs, seed data)
- [x] Vector search indexes (validated against a live cluster)
- [x] Seed embedding generation script
- [ ] Memory Orchestrator (classifier, routing, fusion)
- [ ] Memory Inspector / Agent Brain visualization UI
