-- ============================================================================
-- seed_additional_data.sql
--
-- A fuller dataset to actually test the logic, not just prove connectivity:
--
-- 1. A second, UNRELATED customer + event + semantic fact ("decoy") — lets
--    us confirm vector search discriminates by meaning instead of just
--    returning whatever's in the collection.
-- 2. Two more Acme events + semantic facts, so episodic/semantic retrieval
--    has a real timeline to work with instead of a single data point.
-- 3. A playbook specifically matched to Acme's situation (MongoDB ->
--    Couchbase competitive displacement), so procedural memory retrieval
--    has something meaningfully different to select between.
-- 4. Four more memory_intent_patterns, one per remaining memory type
--    (semantic, long_term, procedural, short_term) — previously only
--    episodic had a labeled example, which isn't a real classifier config.
--
-- All new semantic_memory and memory_intent_patterns docs are inserted
-- with an empty embedding array — run the updated
-- scripts/embed_seed_data.py after this to populate all of them at once
-- (it now auto-discovers any doc with an empty embedding, not just the
-- original two).
-- ============================================================================


-- ============================================================================
-- 1. DECOY CUSTOMER — unrelated industry, unrelated technical concern.
-- Purpose: prove that a query about database scalability does NOT surface
-- this customer's data, and a query about compliance does NOT surface Acme's.
-- ============================================================================

INSERT INTO `agent_memory`.`knowledge`.`customers` (KEY, VALUE)
VALUES ("northstar_health_002", {
  "customer_id": "northstar_health_002",
  "name": "NorthStar Health Partners",
  "industry": "Healthcare",
  "tier": "Mid-Market",
  "contract_status": "Active",
  "primary_contacts": [
    { "name": "Priya Nair", "role": "Director of Compliance" }
  ],
  "technology_context": {
    "current_database": "PostgreSQL",
    "workloads": ["patient records", "appointment scheduling"]
  }
});

INSERT INTO `agent_memory`.`knowledge`.`source_documents` (KEY, VALUE)
VALUES ("doc_200", {
  "document_id": "doc_200",
  "type": "customer_meeting_transcript",
  "customer_id": "northstar_health_002",
  "content": "Full meeting transcript: Priya raised concerns about HIPAA audit logging requirements and data residency for patient records...",
  "created_at": "2026-07-10T14:00:00Z"
});

INSERT INTO `agent_memory`.`knowledge`.`events` (KEY, VALUE)
VALUES ("event_200", {
  "event_id": "event_200",
  "timestamp": "2026-07-10T14:00:00Z",
  "participants": ["Priya Nair"],
  "account": "northstar_health_002",
  "event_type": "customer_meeting",
  "summary": "Discussed HIPAA audit logging requirements and data residency for patient records",
  "source_document_id": "doc_200"
});

INSERT INTO `agent_memory`.`knowledge`.`semantic_memory` (KEY, VALUE)
VALUES ("semantic_fact_500_v1", {
  "logical_id": "semantic_fact_500",
  "generation": 1,
  "fact": "NorthStar Health Partners has strict HIPAA compliance and data residency requirements",
  "content": "NorthStar requires detailed audit logging and guaranteed data residency for patient records to satisfy healthcare compliance obligations",
  "confidence": 0.90,
  "source_event_id": "event_200",
  "source_document_id": "doc_200",
  "embedding": [],
  "embedding_metadata": {
    "model": "amazon.titan-embed-text-v1",
    "dimension": 1536
  },
  "active": true,
  "created_at": "2026-07-10T15:00:00Z"
});


-- ============================================================================
-- 2. RICHER ACME TIMELINE — two more events + semantic facts, so episodic
-- retrieval has an actual timeline and semantic search has more than one
-- real candidate to rank against the decoy.
-- ============================================================================

INSERT INTO `agent_memory`.`knowledge`.`source_documents` (KEY, VALUE)
VALUES ("doc_124", {
  "document_id": "doc_124",
  "type": "customer_meeting_transcript",
  "customer_id": "acme_001",
  "content": "Full meeting transcript: follow-up call to discuss migration timeline, budget approval process, and required executive sign-off...",
  "created_at": "2026-07-17T16:00:00Z"
});

INSERT INTO `agent_memory`.`knowledge`.`events` (KEY, VALUE)
VALUES ("event_124", {
  "event_id": "event_124",
  "timestamp": "2026-07-17T16:00:00Z",
  "participants": ["Sarah Chen", "Mike Torres", "David Park"],
  "account": "acme_001",
  "event_type": "customer_meeting",
  "summary": "Follow-up call: migration timeline, budget approval process, executive sign-off required from David Park (CTO)",
  "source_document_id": "doc_124"
});

INSERT INTO `agent_memory`.`knowledge`.`semantic_memory` (KEY, VALUE)
VALUES ("semantic_fact_457_v1", {
  "logical_id": "semantic_fact_457",
  "generation": 1,
  "fact": "Acme's migration decision requires CTO-level executive sign-off",
  "content": "Any database migration at Acme needs budget approval and sign-off from David Park, the CTO, before proceeding",
  "confidence": 0.88,
  "source_event_id": "event_124",
  "source_document_id": "doc_124",
  "embedding": [],
  "embedding_metadata": {
    "model": "amazon.titan-embed-text-v1",
    "dimension": 1536
  },
  "active": true,
  "created_at": "2026-07-17T17:00:00Z"
});

INSERT INTO `agent_memory`.`knowledge`.`source_documents` (KEY, VALUE)
VALUES ("doc_125", {
  "document_id": "doc_125",
  "type": "customer_meeting_transcript",
  "customer_id": "acme_001",
  "content": "Full meeting transcript: technical deep dive on multi-region replication requirements for payment processing compliance...",
  "created_at": "2026-07-21T13:00:00Z"
});

INSERT INTO `agent_memory`.`knowledge`.`events` (KEY, VALUE)
VALUES ("event_125", {
  "event_id": "event_125",
  "timestamp": "2026-07-21T13:00:00Z",
  "participants": ["Sarah Chen", "Mike Torres"],
  "account": "acme_001",
  "event_type": "technical_deep_dive",
  "summary": "Technical deep dive: multi-region replication requirements for payment processing regulatory compliance",
  "source_document_id": "doc_125"
});

INSERT INTO `agent_memory`.`knowledge`.`semantic_memory` (KEY, VALUE)
VALUES ("semantic_fact_458_v1", {
  "logical_id": "semantic_fact_458",
  "generation": 1,
  "fact": "Acme requires multi-region replication for regulatory compliance",
  "content": "Acme's payment processing workload must replicate across multiple regions to satisfy financial services regulatory requirements",
  "confidence": 0.91,
  "source_event_id": "event_125",
  "source_document_id": "doc_125",
  "embedding": [],
  "embedding_metadata": {
    "model": "amazon.titan-embed-text-v1",
    "dimension": 1536
  },
  "active": true,
  "created_at": "2026-07-21T14:00:00Z"
});


-- ============================================================================
-- 3. PLAYBOOK MATCHED TO ACME'S SITUATION
-- Ties procedural memory to something concretely relevant: Acme is
-- currently on MongoDB (per customers.acme_001.technology_context) and is
-- financial services (regulatory + multi-region requirements just surfaced
-- above), so a generic "migration_assessment" playbook and this one should
-- rank differently depending on the question asked.
-- ============================================================================

INSERT INTO `agent_memory`.`knowledge`.`playbooks` (KEY, VALUE)
VALUES ("mongodb_competitive_displacement", {
  "playbook_id": "mongodb_competitive_displacement",
  "trigger_conditions": ["MongoDB", "competitive displacement", "NoSQL migration", "document database"],
  "steps": [
    "confirm current MongoDB version and deployment topology",
    "identify specific pain points (scaling, ops overhead, licensing cost)",
    "map MongoDB collections/indexes to Couchbase scopes/collections",
    "highlight built-in vector search and mobile sync as differentiators",
    "propose a phased migration plan with a low-risk pilot workload"
  ]
});


-- ============================================================================
-- 4. FULL CLASSIFIER PATTERN COVERAGE
-- One labeled example per remaining memory type. Previously only episodic
-- had a pattern, which isn't a usable classifier config — a real (even if
-- minimal) config needs at least one example per type it's meant to
-- distinguish between.
-- ============================================================================

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("semantic_pattern_001_v1", {
  "logical_id": "semantic_pattern_001",
  "generation": 1,
  "memory_type": "semantic",
  "question_text": "Why is this customer considering a migration?",
  "embedding": [],
  "embedding_metadata": {
    "model": "amazon.titan-embed-text-v1",
    "dimension": 1536
  },
  "active": true,
  "created_at": "2026-07-22T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("long_term_pattern_001_v1", {
  "logical_id": "long_term_pattern_001",
  "generation": 1,
  "memory_type": "long_term",
  "question_text": "Who is this account and what industry are they in?",
  "embedding": [],
  "embedding_metadata": {
    "model": "amazon.titan-embed-text-v1",
    "dimension": 1536
  },
  "active": true,
  "created_at": "2026-07-22T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("procedural_pattern_001_v1", {
  "logical_id": "procedural_pattern_001",
  "generation": 1,
  "memory_type": "procedural",
  "question_text": "How should we approach this discovery call?",
  "embedding": [],
  "embedding_metadata": {
    "model": "amazon.titan-embed-text-v1",
    "dimension": 1536
  },
  "active": true,
  "created_at": "2026-07-22T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("short_term_pattern_001_v1", {
  "logical_id": "short_term_pattern_001",
  "generation": 1,
  "memory_type": "short_term",
  "question_text": "What have we discussed so far in this conversation?",
  "embedding": [],
  "embedding_metadata": {
    "model": "amazon.titan-embed-text-v1",
    "dimension": 1536
  },
  "active": true,
  "created_at": "2026-07-22T09:00:00Z"
});


-- ============================================================================
-- 5. SANITY CHECKS
-- ============================================================================

-- Confirm the decoy customer and Acme are both present and distinct
SELECT customer_id, name, industry FROM `agent_memory`.`knowledge`.`customers`;

-- Confirm Acme now has a real timeline (should return 3 rows)
SELECT event_id, `timestamp`, summary
FROM `agent_memory`.`knowledge`.`events`
WHERE account = "acme_001"
ORDER BY `timestamp`;

-- Confirm every memory_type now has at least one classifier pattern
SELECT memory_type, COUNT(*) AS pattern_count
FROM `agent_memory`.`system_intelligence`.`memory_intent_patterns`
GROUP BY memory_type;

-- Confirm which docs still need embeddings (should be 3 semantic_memory +
-- 4 memory_intent_patterns before running the updated embedding script)
SELECT COUNT(*) AS pending
FROM `agent_memory`.`knowledge`.`semantic_memory`
WHERE ARRAY_LENGTH(embedding) = 0;

SELECT COUNT(*) AS pending
FROM `agent_memory`.`system_intelligence`.`memory_intent_patterns`
WHERE ARRAY_LENGTH(embedding) = 0;
