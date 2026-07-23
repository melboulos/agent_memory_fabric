-- ============================================================================
-- expand_classifier_patterns.sql
--
-- Brings memory_intent_patterns from 1 example per type (5 total) up to 5
-- examples per type (25 total) -- matching the design spec's Section 9.1
-- recommendation of 5-10 labeled examples per type.
--
-- Deliberate design choice: "Acme" (or another customer name) appears in
-- examples across MULTIPLE memory types below, not concentrated in one.
-- This directly targets a real bug found in testing: with only one example
-- per type, episodic's sole example happened to be the only one mentioning
-- "Acme" by name, which appeared to cause the classifier to associate the
-- word "Acme" itself with episodic intent -- scoring episodic 0.791 vs.
-- long_term's 0.277 for the question "Who is Acme?", which should have
-- gone the other way. Spreading customer-name mentions across types
-- should prevent this specific lexical shortcut from re-forming.
--
-- All new documents are inserted with an empty embedding array --
-- scripts/embed_seed_data.py already auto-discovers any document with an
-- empty embedding, so no code change is needed there. Just run this SQL,
-- then re-run the embedding script.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- long_term (identity / profile questions)
-- ---------------------------------------------------------------------------
INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("long_term_pattern_002_v1", {
  "logical_id": "long_term_pattern_002", "generation": 1, "memory_type": "long_term",
  "question_text": "What's Acme's current tech stack and industry?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("long_term_pattern_003_v1", {
  "logical_id": "long_term_pattern_003", "generation": 1, "memory_type": "long_term",
  "question_text": "What tier is this customer on and what's their contract status?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("long_term_pattern_004_v1", {
  "logical_id": "long_term_pattern_004", "generation": 1, "memory_type": "long_term",
  "question_text": "Who are the primary contacts at this account?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("long_term_pattern_005_v1", {
  "logical_id": "long_term_pattern_005", "generation": 1, "memory_type": "long_term",
  "question_text": "Tell me about Acme's business profile.",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

-- ---------------------------------------------------------------------------
-- episodic (event / history questions)
-- ---------------------------------------------------------------------------
INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("episodic_pattern_002_v1", {
  "logical_id": "episodic_pattern_002", "generation": 1, "memory_type": "episodic",
  "question_text": "What did we discuss in the most recent call?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("episodic_pattern_003_v1", {
  "logical_id": "episodic_pattern_003", "generation": 1, "memory_type": "episodic",
  "question_text": "Walk me through the timeline of events with this customer.",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("episodic_pattern_004_v1", {
  "logical_id": "episodic_pattern_004", "generation": 1, "memory_type": "episodic",
  "question_text": "Who attended the last technical review?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("episodic_pattern_005_v1", {
  "logical_id": "episodic_pattern_005", "generation": 1, "memory_type": "episodic",
  "question_text": "What was covered in the discovery call?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

-- ---------------------------------------------------------------------------
-- semantic (conceptual / why questions)
-- ---------------------------------------------------------------------------
INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("semantic_pattern_002_v1", {
  "logical_id": "semantic_pattern_002", "generation": 1, "memory_type": "semantic",
  "question_text": "What are Acme's technical challenges?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("semantic_pattern_003_v1", {
  "logical_id": "semantic_pattern_003", "generation": 1, "memory_type": "semantic",
  "question_text": "Why might they need multi-region support?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("semantic_pattern_004_v1", {
  "logical_id": "semantic_pattern_004", "generation": 1, "memory_type": "semantic",
  "question_text": "What's driving their interest in this solution?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("semantic_pattern_005_v1", {
  "logical_id": "semantic_pattern_005", "generation": 1, "memory_type": "semantic",
  "question_text": "What do we know about their pain points?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

-- ---------------------------------------------------------------------------
-- procedural (how-to / playbook questions)
-- ---------------------------------------------------------------------------
INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("procedural_pattern_002_v1", {
  "logical_id": "procedural_pattern_002", "generation": 1, "memory_type": "procedural",
  "question_text": "What's our playbook for a MongoDB migration?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("procedural_pattern_003_v1", {
  "logical_id": "procedural_pattern_003", "generation": 1, "memory_type": "procedural",
  "question_text": "How do we handle competitive displacement deals?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("procedural_pattern_004_v1", {
  "logical_id": "procedural_pattern_004", "generation": 1, "memory_type": "procedural",
  "question_text": "What steps should we take for this technical evaluation?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("procedural_pattern_005_v1", {
  "logical_id": "procedural_pattern_005", "generation": 1, "memory_type": "procedural",
  "question_text": "How should we structure the proof of concept for Acme?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

-- ---------------------------------------------------------------------------
-- short_term (current conversation / session questions)
-- ---------------------------------------------------------------------------
INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("short_term_pattern_002_v1", {
  "logical_id": "short_term_pattern_002", "generation": 1, "memory_type": "short_term",
  "question_text": "What did I just ask about?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("short_term_pattern_003_v1", {
  "logical_id": "short_term_pattern_003", "generation": 1, "memory_type": "short_term",
  "question_text": "Can you summarize this session?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("short_term_pattern_004_v1", {
  "logical_id": "short_term_pattern_004", "generation": 1, "memory_type": "short_term",
  "question_text": "What are we currently talking about regarding Acme?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

INSERT INTO `agent_memory`.`system_intelligence`.`memory_intent_patterns` (KEY, VALUE)
VALUES ("short_term_pattern_005_v1", {
  "logical_id": "short_term_pattern_005", "generation": 1, "memory_type": "short_term",
  "question_text": "What's the context of this conversation so far?",
  "embedding": [], "embedding_metadata": {"model": "amazon.titan-embed-text-v1", "dimension": 1536},
  "active": true, "created_at": "2026-07-23T09:00:00Z"
});

-- ---------------------------------------------------------------------------
-- Sanity checks
-- ---------------------------------------------------------------------------

-- Should show 5 patterns per type, 25 total
SELECT memory_type, COUNT(*) AS pattern_count
FROM `agent_memory`.`system_intelligence`.`memory_intent_patterns`
GROUP BY memory_type;

-- Should show 20 pending (the 20 new ones inserted above)
SELECT COUNT(*) AS pending
FROM `agent_memory`.`system_intelligence`.`memory_intent_patterns`
WHERE ARRAY_LENGTH(embedding) = 0;
