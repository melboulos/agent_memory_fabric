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

Once the base data model is in, run `sql/seed_additional_data.sql` for a
fuller dataset: a second, unrelated customer (a discrimination test for
vector search), a richer Acme event timeline, a playbook matched to Acme's
actual situation, and full classifier pattern coverage across every memory
type.

### 2. Generate real embeddings for the seed data

`scripts/embed_seed_data.py` auto-discovers every document with an empty
`embedding` array across both vector-backed collections and fills them in
using AWS Bedrock (Amazon Titan Text Embeddings G1 — fixed 1536-dim
output, matches the vector index dimensions already configured). Re-run it
any time you add new documents; it only touches ones that still need
embedding:

```bash
pip install -r requirements.txt

export CB_CONN_STR="couchbases://cb.<your-cluster>.cloud.couchbase.com"
export CB_USERNAME=...
export CB_PASSWORD=...
export CB_BUCKET=agent_memory
export BEDROCK_REGION=us-east-1
export EMBED_MODEL_ID=amazon.titan-embed-text-v1
export CB_CA_BUNDLE=/path/to/vectorcluster-root-certificate.txt   # optional —
  # only needed for non-standard networking (VPC peering, private endpoints);
  # the SDK bundles Capella's standard root cert by default

# AWS credentials picked up automatically from your environment
# (aws configure / AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY / assumed role)

python scripts/embed_seed_data.py --dry-run   # finds + generates, writes nothing
python scripts/embed_seed_data.py             # finds + writes real vectors
```

If you'd rather correct the seed documents' `embedding_metadata.model`
(currently a leftover placeholder value) independently of running the
script, see `sql/fix_embedding_metadata.sql`.

### 3. Confirm vector search actually discriminates

`scripts/test_vector_search.py` embeds a query and runs it against
`semantic_memory_vector_index` with an `active=true` pre-filter, printing
similarity scores. With the additional seed data in place, try a query
that should match Acme (e.g. about database scalability) and one that
should match the decoy customer instead (e.g. about HIPAA compliance) to
confirm results are being ranked by actual meaning, not just returned
indiscriminately:

```bash
python scripts/test_vector_search.py "why is a customer worried about database performance"
python scripts/test_vector_search.py "what are this customer's compliance requirements"
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
