-- ============================================================================
-- Capella AIDP Agent Memory Demo — Data Model Setup
-- Paste sections into Capella Query Workbench, in order, against the
-- `agent_memory` bucket. Create the bucket itself first via the Capella UI
-- (Query Workbench cannot create buckets) — a small "Free Tier"-style bucket
-- is sufficient for demo purposes.
--
-- Run each numbered section top to bottom. Sections 1–3 are pure SQL++.
-- Section 6 (vector search indexes) is REFERENCE ONLY — Capella vector
-- indexes are created via the Search Service UI or REST API, not SQL++.
-- The JSON is included here so it lives next to everything else it depends on.
-- ============================================================================


-- ============================================================================
-- 1. SCOPES
-- Three scopes, locked per design spec Section 7:
--   agent                -> ephemeral cognition / active conversation state
--   knowledge             -> persistent, accumulated organizational knowledge
--   system_intelligence   -> configuration, routing, observability (NOT memory)
-- ============================================================================

CREATE SCOPE `agent_memory`.`agent`               IF NOT EXISTS;
CREATE SCOPE `agent_memory`.`knowledge`           IF NOT EXISTS;
CREATE SCOPE `agent_memory`.`system_intelligence` IF NOT EXISTS;


-- ============================================================================
-- 2. COLLECTIONS
-- ============================================================================

-- Agent scope: working state, no long-term significance
CREATE COLLECTION `agent_memory`.`agent`.`working_memory`     IF NOT EXISTS;
CREATE COLLECTION `agent_memory`.`agent`.`short_term_memory`  IF NOT EXISTS;

-- Knowledge scope: source_documents is the ingestion layer; the rest are
-- the four accumulated-knowledge memory types (long-term / episodic /
-- semantic / procedural)
CREATE COLLECTION `agent_memory`.`knowledge`.`source_documents` IF NOT EXISTS;
CREATE COLLECTION `agent_memory`.`knowledge`.`customers`        IF NOT EXISTS;
CREATE COLLECTION `agent_memory`.`knowledge`.`events`           IF NOT EXISTS;
CREATE COLLECTION `agent_memory`.`knowledge`.`semantic_memory`  IF NOT EXISTS;
CREATE COLLECTION `agent_memory`.`knowledge`.`playbooks`        IF NOT EXISTS;

-- System Intelligence scope: configuration + observability, explicitly not memory
CREATE COLLECTION `agent_memory`.`system_intelligence`.`memory_intent_patterns` IF NOT EXISTS;
CREATE COLLECTION `agent_memory`.`system_intelligence`.`routing_traces`         IF NOT EXISTS;
CREATE COLLECTION `agent_memory`.`system_intelligence`.`memory_audit`          IF NOT EXISTS;


-- ============================================================================
-- 3. PRIMARY INDEXES
-- Needed for ad-hoc querying/debugging during the build. Fine for demo scale;
-- would drop or restrict in a production deployment.
-- ============================================================================

CREATE PRIMARY INDEX IF NOT EXISTS `idx_primary_working_memory`
  ON `agent_memory`.`agent`.`working_memory`;

CREATE PRIMARY INDEX IF NOT EXISTS `idx_primary_short_term_memory`
  ON `agent_memory`.`agent`.`short_term_memory`;

CREATE PRIMARY INDEX IF NOT EXISTS `idx_primary_source_documents`
  ON `agent_memory`.`knowledge`.`source_documents`;

CREATE PRIMARY INDEX IF NOT EXISTS `idx_primary_customers`
  ON `agent_memory`.`knowledge`.`customers`;

CREATE PRIMARY INDEX IF NOT EXISTS `idx_primary_events`
  ON `agent_memory`.`knowledge`.`events`;

CREATE PRIMARY INDEX IF NOT EXISTS `idx_primary_semantic_memory`
  ON `agent_memory`.`knowledge`.`semantic_memory`;

CREATE PRIMARY INDEX IF NOT EXISTS `idx_primary_playbooks`
  ON `agent_memory`.`knowledge`.`playbooks`;

CREATE PRIMARY INDEX IF NOT EXISTS `idx_primary_memory_intent_patterns`
  ON `agent_memory`.`system_intelligence`.`memory_intent_patterns`;

CREATE PRIMARY INDEX IF NOT EXISTS `idx_primary_routing_traces`
  ON `agent_memory`.`system_intelligence`.`routing_traces`;

CREATE PRIMARY INDEX IF NOT EXISTS `idx_primary_memory_audit`
  ON `agent_memory`.`system_intelligence`.`memory_audit`;


-- ============================================================================
-- 4. SECONDARY (GSI) INDEXES
-- Support the actual retrieval patterns from the design spec (Section 7 & 10).
-- Note on vector-backed collections (semantic_memory, memory_intent_patterns):
-- these GSIs support NON-vector lookups (e.g. debug queries, admin views).
-- The `active` pre-filter for vector search itself is enforced at query time
-- by combining a `knn` clause with a `query` filter in the same search
-- request against the vector index (Section 6.3), not by these GSIs.
-- ============================================================================

-- customers: direct lookup by customer_id (long-term memory retrieval)
CREATE INDEX IF NOT EXISTS `idx_customers_customer_id`
  ON `agent_memory`.`knowledge`.`customers`(customer_id);

-- events: entity + timeline retrieval (episodic memory)
CREATE INDEX IF NOT EXISTS `idx_events_account_timestamp`
  ON `agent_memory`.`knowledge`.`events`(account, `timestamp`);

CREATE INDEX IF NOT EXISTS `idx_events_participants`
  ON `agent_memory`.`knowledge`.`events`(DISTINCT ARRAY p FOR p IN participants END);

CREATE INDEX IF NOT EXISTS `idx_events_event_type`
  ON `agent_memory`.`knowledge`.`events`(event_type, account);

-- semantic_memory: active-generation filtering + provenance lookups
CREATE INDEX IF NOT EXISTS `idx_semantic_active_logical`
  ON `agent_memory`.`knowledge`.`semantic_memory`(logical_id, active, generation);

CREATE INDEX IF NOT EXISTS `idx_semantic_source_event`
  ON `agent_memory`.`knowledge`.`semantic_memory`(source_event_id)
  WHERE source_event_id IS NOT MISSING;

CREATE INDEX IF NOT EXISTS `idx_semantic_source_document`
  ON `agent_memory`.`knowledge`.`semantic_memory`(source_document_id)
  WHERE source_document_id IS NOT MISSING;

-- playbooks: intent/trigger-condition matching (procedural memory)
CREATE INDEX IF NOT EXISTS `idx_playbooks_trigger_conditions`
  ON `agent_memory`.`knowledge`.`playbooks`(DISTINCT ARRAY t FOR t IN trigger_conditions END);

-- source_documents: lookup by customer and type (ingestion layer)
CREATE INDEX IF NOT EXISTS `idx_source_documents_customer_type`
  ON `agent_memory`.`knowledge`.`source_documents`(customer_id, type);

-- short_term_memory: session lookup + expiry housekeeping
CREATE INDEX IF NOT EXISTS `idx_short_term_session_id`
  ON `agent_memory`.`agent`.`short_term_memory`(session_id);

CREATE INDEX IF NOT EXISTS `idx_short_term_expires_at`
  ON `agent_memory`.`agent`.`short_term_memory`(expires_at);

-- memory_intent_patterns: active-generation filtering (classifier config)
CREATE INDEX IF NOT EXISTS `idx_patterns_active_logical`
  ON `agent_memory`.`system_intelligence`.`memory_intent_patterns`(logical_id, active, generation);

CREATE INDEX IF NOT EXISTS `idx_patterns_memory_type`
  ON `agent_memory`.`system_intelligence`.`memory_intent_patterns`(memory_type, active);

-- routing_traces: per-session / per-question debugging
CREATE INDEX IF NOT EXISTS `idx_routing_traces_session_created`
  ON `agent_memory`.`system_intelligence`.`routing_traces`(session_id, created_at);

-- memory_audit: lifecycle queries by collection/operation/time
CREATE INDEX IF NOT EXISTS `idx_memory_audit_collection_ts`
  ON `agent_memory`.`system_intelligence`.`memory_audit`(`collection`, operation, `timestamp`);


-- ============================================================================
-- 5. SEED DATA
-- One representative document per collection, matching the schemas locked in
-- the design spec. Enough to exercise every demo scenario (Section 12) and
-- every retrieval path in the Retrieval Matrix (Section 6) without a real
-- ingestion pipeline.
-- ============================================================================

-- 5.1 customers (long-term memory)
INSERT INTO `agent_memory`.`knowledge`.`customers` (KEY, VALUE)
VALUES ("acme_001", {
  "customer_id": "acme_001",
  "name": "Acme Corporation",
  "industry": "Financial Services",
  "tier": "Enterprise",
  "contract_status": "Active",
  "primary_contacts": [
    { "name": "Sarah Chen", "role": "VP Engineering" }
  ],
  "technology_context": {
    "current_database": "MongoDB",
    "workloads": ["payments", "customer accounts"]
  }
});

-- 5.2 source_documents (knowledge ingestion layer)
INSERT INTO `agent_memory`.`knowledge`.`source_documents` (KEY, VALUE)
VALUES ("doc_123", {
  "document_id": "doc_123",
  "type": "customer_meeting_transcript",
  "customer_id": "acme_001",
  "content": "Full meeting transcript: Sarah described scalability and latency concerns with the current MongoDB deployment under peak payment load...",
  "created_at": "2026-07-14T15:00:00Z"
});

-- 5.3 events (episodic memory)
INSERT INTO `agent_memory`.`knowledge`.`events` (KEY, VALUE)
VALUES ("event_123", {
  "event_id": "event_123",
  "timestamp": "2026-07-14T15:00:00Z",
  "participants": ["Sarah Chen", "Mike Torres"],
  "account": "acme_001",
  "event_type": "customer_meeting",
  "summary": "Discussed migration concerns: scalability and latency under peak load",
  "source_document_id": "doc_123"
});

-- 5.4 semantic_memory (vector-backed; generation 1, active)
-- NOTE: replace the embedding array with a real 1536-dim vector before use —
-- this is a placeholder shape, not a usable embedding.
INSERT INTO `agent_memory`.`knowledge`.`semantic_memory` (KEY, VALUE)
VALUES ("semantic_fact_456_v1", {
  "logical_id": "semantic_fact_456",
  "generation": 1,
  "fact": "Acme has scalability concerns with their current architecture",
  "content": "Acme requires higher throughput and predictable latency for payment processing",
  "confidence": 0.86,
  "source_event_id": "event_123",
  "source_document_id": "doc_123",
  "embedding": [],
  "embedding_metadata": {
    "model": "text-embedding-3-small",
    "dimension": 1536
  },
  "active": true,
  "created_at": "2026-07-14T16:00:00Z"
});

-- 5.5 playbooks (procedural memory — structured, not vector-backed in v1)
INSERT INTO `agent_memory`.`knowledge`.`playbooks` (KEY, VALUE)
VALUES ("migration_assessment", {
  "playbook_id": "migration_assessment",
  "trigger_conditions": ["database migration", "modernization", "scalability concern"],
  "steps": [
    "identify current workload characteristics",
    "evaluate latency and throughput requirements",
    "assess operational requirements and constraints",
    "propose target architecture options"
  ]
});

-- 5.6 short_term_memory (session continuity)
INSERT INTO `agent_memory`.`agent`.`short_term_memory` (KEY, VALUE)
VALUES ("session_123", {
  "session_id": "session_123",
  "conversation_summary": "User is evaluating Acme migration opportunity",
  "active_entities": ["Acme Corporation"],
  "recent_topics": ["database modernization", "scalability"],
  "created_at": "2026-07-22T10:00:00Z",
  "expires_at": "2026-07-29T10:00:00Z"
});

-- 5.7 working_memory (ephemeral reasoning scratchpad — illustrative only;
-- in practice this may live purely in-process and never be persisted)
INSERT INTO `agent_memory`.`agent`.`working_memory` (KEY, VALUE)
VALUES ("working_session_123", {
  "session_id": "session_123",
  "scratchpad": {
    "current_question": "Why are they considering migration?",
    "candidate_memories_in_progress": ["customers", "semantic_memory", "events"]
  },
  "updated_at": "2026-07-22T10:05:00Z"
});

-- 5.8 memory_intent_patterns (classifier config — vector-backed; NOT memory)
-- NOTE: replace embedding with a real vector before use.
INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("episodic_pattern_001_v1", {
  "logical_id": "episodic_pattern_001",
  "generation": 1,
  "memory_type": "episodic",
  "question_text": "What happened in our last meeting with Acme?",
  "embedding": [],
  "embedding_metadata": {
    "model": "text-embedding-3-small",
    "dimension": 1536
  },
  "active": true,
  "created_at": "2026-07-22T09:00:00Z"
});

-- 5.9 routing_traces (per-query routing explanation)
INSERT INTO `agent_memory`.`system_intelligence`.`routing_traces` (KEY, VALUE)
VALUES ("trace_001", {
  "trace_id": "trace_001",
  "session_id": "session_123",
  "question": "Why are they considering migration?",
  "classifier_candidates": [
    { "memory": "semantic", "score": 0.80 },
    { "memory": "episodic", "score": 0.64 },
    { "memory": "long_term", "score": 0.30 }
  ],
  "llm_decision": [
    { "memory": "customers", "selected": true, "reason": "Establish account identity" },
    { "memory": "semantic_memory", "selected": true, "reason": "Conceptual match on scalability concern" },
    { "memory": "events", "selected": true, "reason": "Historical grounding for the concern" }
  ],
  "created_at": "2026-07-22T10:05:03Z"
});

-- 5.10 memory_audit (lifecycle audit entry)
INSERT INTO `agent_memory`.`system_intelligence`.`memory_audit` (KEY, VALUE)
VALUES ("audit_001", {
  "operation": "READ",
  "collection": "events",
  "document_id": "event_123",
  "actor": "memory_orchestrator",
  "timestamp": "2026-07-22T10:05:03Z"
});


-- ============================================================================
-- 6. VECTOR SEARCH INDEXES — REFERENCE ONLY (VALIDATED, LIVE IN CAPELLA)
-- Create/import these via Capella UI (Data Tools > Search > Create Search
-- Index > Advanced Mode > Import) or the Search Service REST API — NOT via
-- Query Workbench / SQL++.
--
-- Correction from an earlier draft of this file: Couchbase Search indexes
-- have no "filter_fields" property — that was shorthand invented before
-- validating against a real cluster, and would fail to import as-is.
-- Pre-filtering on active/logical_id/generation/memory_type is achieved by
-- indexing them as ordinary typed fields (boolean/text/number) alongside the
-- vector field, then combining a `knn` clause with a `query` filter in the
-- same search request at query time (see 6.3 below) — not by any special
-- index-level setting.
--
-- Both definitions below were built, corrected, and confirmed working
-- directly against the agent_memory cluster.
-- ============================================================================

-- 6.1 semantic_memory_vector_index (scope: knowledge, collection: semantic_memory)
-- {
--   "type": "fulltext-index",
--   "name": "agent_memory.knowledge.semantic_memory_vector_index",
--   "sourceType": "gocbcore",
--   "sourceName": "agent_memory",
--   "planParams": { "maxPartitionsPerPIndex": 128, "indexPartitions": 1, "numReplicas": 1 },
--   "params": {
--     "doc_config": {
--       "docid_prefix_delim": "", "docid_regexp": "",
--       "mode": "scope.collection.type_field", "type_field": "type"
--     },
--     "mapping": {
--       "analysis": {}, "default_analyzer": "standard",
--       "default_datetime_parser": "dateTimeOptional", "default_field": "_all",
--       "default_mapping": { "dynamic": false, "enabled": false },
--       "default_type": "_default", "docvalues_dynamic": false,
--       "index_dynamic": true, "store_dynamic": false, "type_field": "_type",
--       "types": {
--         "knowledge.semantic_memory": {
--           "dynamic": false, "enabled": true,
--           "properties": {
--             "embedding": {
--               "dynamic": false, "enabled": true,
--               "fields": [{ "dims": 1536, "index": true, "name": "embedding",
--                 "similarity": "cosine", "type": "vector", "vector_index_optimized_for": "recall" }]
--             },
--             "active": {
--               "dynamic": false, "enabled": true,
--               "fields": [{ "index": true, "name": "active", "store": true, "type": "boolean" }]
--             },
--             "generation": {
--               "dynamic": false, "enabled": true,
--               "fields": [{ "index": true, "name": "generation", "store": true, "type": "number" }]
--             },
--             "logical_id": {
--               "dynamic": false, "enabled": true,
--               "fields": [{ "analyzer": "en", "index": true, "name": "logical_id", "store": true, "type": "text" }]
--             }
--           }
--         }
--       }
--     },
--     "store": { "indexType": "scorch", "segmentVersion": 16 }
--   },
--   "sourceParams": {}
-- }

-- 6.2 memory_intent_patterns_vector_index (scope: system_intelligence, collection: memory_intent_patterns)
-- {
--   "type": "fulltext-index",
--   "name": "agent_memory.system_intelligence.memory_intent_patterns_vector_index",
--   "sourceType": "gocbcore",
--   "sourceName": "agent_memory",
--   "planParams": { "maxPartitionsPerPIndex": 128, "indexPartitions": 1, "numReplicas": 1 },
--   "params": {
--     "doc_config": {
--       "docid_prefix_delim": "", "docid_regexp": "",
--       "mode": "scope.collection.type_field", "type_field": "type"
--     },
--     "mapping": {
--       "analysis": {}, "default_analyzer": "standard",
--       "default_datetime_parser": "dateTimeOptional", "default_field": "_all",
--       "default_mapping": { "dynamic": false, "enabled": false },
--       "default_type": "_default", "docvalues_dynamic": false,
--       "index_dynamic": true, "store_dynamic": false, "type_field": "_type",
--       "types": {
--         "system_intelligence.memory_intent_patterns": {
--           "dynamic": false, "enabled": true,
--           "properties": {
--             "embedding": {
--               "dynamic": false, "enabled": true,
--               "fields": [{ "dims": 1536, "index": true, "name": "embedding",
--                 "similarity": "cosine", "type": "vector", "vector_index_optimized_for": "recall" }]
--             },
--             "active": {
--               "dynamic": false, "enabled": true,
--               "fields": [{ "index": true, "name": "active", "store": true, "type": "boolean" }]
--             },
--             "logical_id": {
--               "dynamic": false, "enabled": true,
--               "fields": [{ "analyzer": "en", "index": true, "name": "logical_id", "store": true, "type": "text" }]
--             },
--             "memory_type": {
--               "dynamic": false, "enabled": true,
--               "fields": [{ "analyzer": "en", "index": true, "name": "memory_type", "store": true, "type": "text" }]
--             }
--           }
--         }
--       }
--     },
--     "store": { "indexType": "scorch", "segmentVersion": 16 }
--   },
--   "sourceParams": {}
-- }

-- 6.3 Query-time pre-filtering pattern (enforces Section 10.4's governance rule)
-- Combine a `knn` clause with a `query` filter in the SAME search request —
-- this is what actually keeps stale embedding generations out of the top-k
-- results, not any index-level setting.
-- {
--   "knn": [{ "field": "embedding", "vector": [ /* query embedding */ ], "k": 5 }],
--   "query": { "field": "active", "bool": true }
-- }


-- ============================================================================
-- 7. SANITY CHECKS
-- Run after setup to confirm the model is wired correctly.
-- ============================================================================

-- Confirm exactly one active generation per logical semantic memory object
-- (should return zero rows once real data volume grows — any row here is a
-- governance violation per Section 10.2's invariant)
SELECT logical_id, COUNT(*) AS active_count
FROM `agent_memory`.`knowledge`.`semantic_memory`
WHERE active = true
GROUP BY logical_id
HAVING COUNT(*) != 1;

-- Confirm the long-term memory retrieval path works end to end
SELECT * FROM `agent_memory`.`knowledge`.`customers` WHERE customer_id = "acme_001";

-- Confirm episodic retrieval by account + time
SELECT * FROM `agent_memory`.`knowledge`.`events`
WHERE account = "acme_001"
ORDER BY `timestamp` DESC;
