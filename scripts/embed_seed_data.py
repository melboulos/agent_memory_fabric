#!/usr/bin/env python3
"""
embed_seed_data.py

Generates real embeddings for the two seed documents created in
capella_agent_memory_setup.sql (Section 5) and writes them back into
Capella, replacing the placeholder empty `embedding` arrays.

This is a one-time FILL, not a promotion. Both seed docs were inserted as
generation 1 / active = true with no usable vector yet — this script
completes generation 1, it does not create generation 2. The immutable
generation / promotion workflow (design spec Section 10.3) applies to
FUTURE re-embeddings (e.g. a model upgrade), not to this initial fill.

Targets:
  - agent_memory.knowledge.semantic_memory            -> semantic_fact_456_v1
  - agent_memory.system_intelligence.memory_intent_patterns -> episodic_pattern_001_v1

Usage:
  export OPENAI_API_KEY=...
  export CB_CONN_STR="couchbases://<your-cluster>.cloud.couchbase.com"
  export CB_USERNAME=...
  export CB_PASSWORD=...
  export CB_BUCKET=agent_memory

  python embed_seed_data.py            # generates + writes
  python embed_seed_data.py --dry-run  # generates + prints, writes nothing

Requirements:
  pip install openai couchbase --break-system-packages
"""

import argparse
import os
import sys
from datetime import datetime, timezone

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536  # must match embedding_metadata.dimension already
                             # recorded on both seed documents


# ---------------------------------------------------------------------------
# Embedding compatibility validation
# Mirrors the shared utility described in design spec Section 10.5:
# validate_embedding_compatibility(query_embedding, target_collection).
# Here it's applied to a freshly generated embedding against the dimension
# the target document already declares in embedding_metadata, so a model
# mismatch fails loudly instead of writing a silently incompatible vector.
# ---------------------------------------------------------------------------
def validate_embedding_compatibility(vector, expected_model, expected_dimension, actual_model):
    if actual_model != expected_model:
        raise ValueError(
            f"EMBEDDING_COMPATIBILITY_ERROR: expected model '{expected_model}', "
            f"got '{actual_model}'. Refusing to write a mismatched embedding."
        )
    if len(vector) != expected_dimension:
        raise ValueError(
            f"EMBEDDING_COMPATIBILITY_ERROR: expected dimension {expected_dimension}, "
            f"got {len(vector)}. Refusing to write a mismatched embedding."
        )


def generate_embedding(client, text: str):
    """Calls the embedding model and returns (vector, model_name)."""
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    vector = response.data[0].embedding
    return vector, response.model


# ---------------------------------------------------------------------------
# The two seed targets. Each entry says which collection/key to update, what
# text to embed, and the model/dimension already recorded in that document's
# embedding_metadata (so we can validate against it, not just assume it).
# ---------------------------------------------------------------------------
SEED_TARGETS = [
    {
        "label": "semantic_memory: semantic_fact_456_v1",
        "scope": "knowledge",
        "collection": "semantic_memory",
        "key": "semantic_fact_456_v1",
        "text_fn": lambda doc: f"{doc['fact']}. {doc['content']}",
        # Known seed text (matches capella_agent_memory_setup.sql Section 5.4)
        # used only in --dry-run mode, where we don't read the live document.
        "dry_run_text": (
            "Acme has scalability concerns with their current architecture. "
            "Acme requires higher throughput and predictable latency for payment processing"
        ),
        "expected_model": "text-embedding-3-small",
        "expected_dimension": 1536,
    },
    {
        "label": "memory_intent_patterns: episodic_pattern_001_v1",
        "scope": "system_intelligence",
        "collection": "memory_intent_patterns",
        "key": "episodic_pattern_001_v1",
        "text_fn": lambda doc: doc["question_text"],
        # Known seed text (matches capella_agent_memory_setup.sql Section 5.8)
        "dry_run_text": "What happened in our last meeting with Acme?",
        "expected_model": "text-embedding-3-small",
        "expected_dimension": 1536,
    },
]


def get_couchbase_collection(cluster, bucket_name, scope_name, collection_name):
    bucket = cluster.bucket(bucket_name)
    return bucket.scope(scope_name).collection(collection_name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate embeddings and print vector stats, but do not write to Capella.",
    )
    args = parser.parse_args()

    # ---- Embedding client ----
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("Missing dependency: pip install openai --break-system-packages")

    openai_api_key = os.environ.get("OPENAI_API_KEY")
    if not openai_api_key:
        sys.exit("Set OPENAI_API_KEY before running.")
    openai_client = OpenAI(api_key=openai_api_key)

    # ---- Couchbase connection (skipped entirely in --dry-run) ----
    cluster = None
    if not args.dry_run:
        try:
            from couchbase.cluster import Cluster
            from couchbase.options import ClusterOptions
            from couchbase.auth import PasswordAuthenticator
        except ImportError:
            sys.exit("Missing dependency: pip install couchbase --break-system-packages")

        conn_str = os.environ.get("CB_CONN_STR")
        username = os.environ.get("CB_USERNAME")
        password = os.environ.get("CB_PASSWORD")
        bucket_name = os.environ.get("CB_BUCKET", "agent_memory")

        missing = [
            name
            for name, val in [("CB_CONN_STR", conn_str), ("CB_USERNAME", username), ("CB_PASSWORD", password)]
            if not val
        ]
        if missing:
            sys.exit(f"Missing required environment variables: {', '.join(missing)}")

        cluster = Cluster(conn_str, ClusterOptions(PasswordAuthenticator(username, password)))
        cluster.wait_until_ready(timeout=None)

    # ---- Process each seed target ----
    for target in SEED_TARGETS:
        print(f"\n--- {target['label']} ---")

        if args.dry_run:
            text_to_embed = target["dry_run_text"]
            print(f"(dry-run: using known seed text, no Couchbase read)")
            print(f"Embedding text: {text_to_embed!r}")

            vector, actual_model = generate_embedding(openai_client, text_to_embed)
            validate_embedding_compatibility(
                vector,
                expected_model=target["expected_model"],
                expected_dimension=target["expected_dimension"],
                actual_model=actual_model,
            )
            print(f"Generated {len(vector)}-dim vector using {actual_model}. Compatibility check passed.")
            print("(--dry-run: not writing to Capella)")
            continue

        collection = get_couchbase_collection(
            cluster, os.environ.get("CB_BUCKET", "agent_memory"), target["scope"], target["collection"]
        )

        get_result = collection.get(target["key"])
        doc = get_result.content_as[dict]

        text_to_embed = target["text_fn"](doc)
        print(f"Embedding text: {text_to_embed!r}")

        vector, actual_model = generate_embedding(openai_client, text_to_embed)

        validate_embedding_compatibility(
            vector,
            expected_model=target["expected_model"],
            expected_dimension=target["expected_dimension"],
            actual_model=actual_model,
        )

        print(f"Generated {len(vector)}-dim vector using {actual_model}. Compatibility check passed.")

        doc["embedding"] = vector
        doc["embedding_metadata"]["generated_at"] = datetime.now(timezone.utc).isoformat()
        collection.upsert(target["key"], doc)
        print(f"Wrote embedding to {target['scope']}.{target['collection']}::{target['key']}")

    print("\nDone.")
    if args.dry_run:
        print("This was a dry run — nothing was written to Capella. Re-run without --dry-run to persist.")


if __name__ == "__main__":
    main()
