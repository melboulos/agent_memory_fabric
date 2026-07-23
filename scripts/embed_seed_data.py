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

Embedding provider: AWS Bedrock, Amazon Titan Text Embeddings G1
(amazon.titan-embed-text-v1) — fixed 1536-dim output, matches the dims
already configured on both Capella vector search indexes. No index rebuild
needed when switching to this provider.

Targets:
  - agent_memory.knowledge.semantic_memory                 -> semantic_fact_456_v1
  - agent_memory.system_intelligence.memory_intent_patterns -> episodic_pattern_001_v1

Usage:
  export CB_CONN_STR="couchbases://cb.<your-cluster>.cloud.couchbase.com"
  export CB_USERNAME=...
  export CB_PASSWORD=...
  export CB_BUCKET=agent_memory        # optional, defaults to agent_memory
  export BEDROCK_REGION=us-east-1
  export EMBED_MODEL_ID=amazon.titan-embed-text-v1
  export CB_CA_BUNDLE=/path/to/vectorcluster-root-certificate.txt   # optional

  # AWS credentials: use whatever your environment already has configured
  # (aws configure, AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY env vars, or an
  # assumed role) — boto3 picks these up automatically, nothing to set here.

  python embed_seed_data.py            # generates + writes
  python embed_seed_data.py --dry-run  # generates + prints, writes nothing

Requirements:
  pip install boto3 couchbase --break-system-packages
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

EMBEDDING_DIMENSION = 1536  # fixed for amazon.titan-embed-text-v1; must match
                             # embedding_metadata.dimension already recorded
                             # on both seed documents


# ---------------------------------------------------------------------------
# Embedding compatibility validation
# Mirrors the shared utility described in design spec Section 10.5:
# validate_embedding_compatibility(query_embedding, target_collection).
#
# Note on Titan specifically: unlike some providers, the Bedrock Titan
# embeddings response does not echo back a model identifier — it only
# returns the vector and a token count. So `actual_model` here is the model
# ID we explicitly requested (EMBED_MODEL_ID), not something independently
# confirmed by the API response. The dimension check is the part doing real
# work; the model check mainly guards against config drift (e.g. someone
# changes EMBED_MODEL_ID without updating the document's expected_model).
# ---------------------------------------------------------------------------
def validate_embedding_compatibility(vector, expected_model, expected_dimension, actual_model):
    if actual_model != expected_model:
        raise ValueError(
            f"EMBEDDING_COMPATIBILITY_ERROR: expected model '{expected_model}', "
            f"requested '{actual_model}'. Refusing to write a mismatched embedding."
        )
    if len(vector) != expected_dimension:
        raise ValueError(
            f"EMBEDDING_COMPATIBILITY_ERROR: expected dimension {expected_dimension}, "
            f"got {len(vector)}. Refusing to write a mismatched embedding."
        )


def generate_embedding(bedrock_client, model_id: str, text: str):
    """Calls Bedrock Titan Text Embeddings and returns (vector, model_id)."""
    body = json.dumps({"inputText": text})
    response = bedrock_client.invoke_model(
        modelId=model_id,
        body=body,
        accept="application/json",
        contentType="application/json",
    )
    response_body = json.loads(response["body"].read())
    vector = response_body["embedding"]
    return vector, model_id


# ---------------------------------------------------------------------------
# The two seed targets. Each entry says which collection/key to update, what
# text to embed, and the model/dimension already recorded in that document's
# embedding_metadata (so we can validate against it, not just assume it).
#
# NOTE: the live seed documents currently have embedding_metadata.model set
# to "text-embedding-3-small" (a placeholder from before the provider was
# decided). This script's expected_model below is the CORRECT value
# (amazon.titan-embed-text-v1) — it will overwrite embedding_metadata.model
# to match reality when it writes the real vector. See the companion
# sql/fix_embedding_metadata.sql for a standalone correction if you want to
# fix the metadata without waiting on this script.
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

    # ---- Bedrock client ----
    try:
        import boto3
    except ImportError:
        sys.exit("Missing dependency: pip install boto3 --break-system-packages")

    bedrock_region = os.environ.get("BEDROCK_REGION")
    embed_model_id = os.environ.get("EMBED_MODEL_ID")
    if not bedrock_region or not embed_model_id:
        sys.exit("Set BEDROCK_REGION and EMBED_MODEL_ID before running.")

    bedrock_client = boto3.client("bedrock-runtime", region_name=bedrock_region)

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
        ca_bundle = os.environ.get("CB_CA_BUNDLE")  # optional

        missing = [
            name
            for name, val in [("CB_CONN_STR", conn_str), ("CB_USERNAME", username), ("CB_PASSWORD", password)]
            if not val
        ]
        if missing:
            sys.exit(f"Missing required environment variables: {', '.join(missing)}")

        # cert_path is optional — the Python SDK bundles Capella's standard
        # root cert by default (SDK 4.1+), so couchbases:// alone often works
        # without this. Only needed for non-standard networking (VPC
        # peering, private endpoints, custom-issued certs).
        if ca_bundle:
            auth = PasswordAuthenticator(username, password, cert_path=ca_bundle)
        else:
            auth = PasswordAuthenticator(username, password)

        cluster = Cluster(conn_str, ClusterOptions(auth))
        from datetime import timedelta
        cluster.wait_until_ready(timedelta(seconds=15))

    # ---- Process each seed target ----
    for target in SEED_TARGETS:
        print(f"\n--- {target['label']} ---")

        if args.dry_run:
            text_to_embed = target["dry_run_text"]
            print("(dry-run: using known seed text, no Couchbase read)")
            print(f"Embedding text: {text_to_embed!r}")

            vector, actual_model = generate_embedding(bedrock_client, embed_model_id, text_to_embed)
            validate_embedding_compatibility(
                vector,
                expected_model=embed_model_id,
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

        vector, actual_model = generate_embedding(bedrock_client, embed_model_id, text_to_embed)

        validate_embedding_compatibility(
            vector,
            expected_model=embed_model_id,
            expected_dimension=target["expected_dimension"],
            actual_model=actual_model,
        )

        print(f"Generated {len(vector)}-dim vector using {actual_model}. Compatibility check passed.")

        doc["embedding"] = vector
        doc["embedding_metadata"]["model"] = actual_model
        doc["embedding_metadata"]["dimension"] = len(vector)
        doc["embedding_metadata"]["generated_at"] = datetime.now(timezone.utc).isoformat()
        collection.upsert(target["key"], doc)
        print(f"Wrote embedding to {target['scope']}.{target['collection']}::{target['key']}")

    print("\nDone.")
    if args.dry_run:
        print("This was a dry run — nothing was written to Capella. Re-run without --dry-run to persist.")


if __name__ == "__main__":
    main()
