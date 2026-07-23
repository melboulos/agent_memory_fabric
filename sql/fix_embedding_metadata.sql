-- ============================================================================
-- fix_embedding_metadata.sql
--
-- Corrects embedding_metadata.model on the two vector-backed seed documents.
-- These were originally inserted with a placeholder value
-- ("text-embedding-3-small") before the embedding provider was decided.
-- The actual provider is AWS Bedrock, Amazon Titan Text Embeddings G1
-- (amazon.titan-embed-text-v1), which is a fixed 1536-dim model — dimension
-- is already correct and does not need to change.
--
-- Run this BEFORE running scripts/embed_seed_data.py if you want the
-- metadata correct ahead of time, or skip it — the script itself now
-- also corrects embedding_metadata.model/dimension as part of writing the
-- real vector. This file exists so the metadata can be fixed independently
-- of running the embedding script, e.g. for review purposes.
-- ============================================================================

UPDATE `agent_memory`.`knowledge`.`semantic_memory`
SET embedding_metadata.model = "amazon.titan-embed-text-v1"
WHERE META().id = "semantic_fact_456_v1";

UPDATE `agent_memory`.`system_intelligence`.`memory_intent_patterns`
SET embedding_metadata.model = "amazon.titan-embed-text-v1"
WHERE META().id = "episodic_pattern_001_v1";

-- Confirm the fix
SELECT META().id, embedding_metadata
FROM `agent_memory`.`knowledge`.`semantic_memory`
WHERE META().id = "semantic_fact_456_v1";

SELECT META().id, embedding_metadata
FROM `agent_memory`.`system_intelligence`.`memory_intent_patterns`
WHERE META().id = "episodic_pattern_001_v1";
