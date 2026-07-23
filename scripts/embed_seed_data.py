#!/usr/bin/env python3
"""
embed_seed_data.py

Finds every document with an empty `embedding` array across the two
vector-backed collections (semantic_memory, memory_intent_patterns) and
fills it in with a real vector from AWS Bedrock Titan.

This replaces an earlier version of this script that only knew about two
hardcoded documents. As you seed more data over time (new customers,
events, facts, classifier patterns), this version finds all of them
automatically via a SQL++ query instead of needing a new hardcoded entry
for every document.

This is a one-time FILL, not a promotion. Every document processed here is
still at generation 1 / active = true with no usable vector yet — filling
that in is not the same as the immutable generation / promotion workflow
(design spec Section 10.3), which applies to FUTURE re-embeddings (e.g. a
model upgrade of documents that already have a real vector).

Embedding provider: AWS Bedrock, Amazon Titan Text Embeddings G1
(amazon.titan-embed-text-v1) — fixed 1536-dim output, matches the dims
already configured on both Capella vector search indexes.

Usage:
  export CB_CONN_STR="couchbases://cb.<your-cluster>.cloud.couchbase.com"
  export CB_USERNAME=...
  export CB_PASSWORD=...
  export CB_BUCKET=agent_memory        # optional, defaults to agent_memory
  export BEDROCK_REGION=us-east-1
  export EMBED_MODEL_ID=amazon.titan-embed-text-v1
  export CB_CA_BUNDLE=/path/to/vectorcluster-root-certificate.txt   # optional

  # AWS credentials: use whatever your environment already has configured
  # (aws configure, AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, or an assumed
  # role) — boto3 picks these up automatically, nothing to set here.

  python scripts/embed_seed_data.py            # finds + writes real vectors
  python scripts/embed_seed_data.py --dry-run  # finds + generates + prints,
                                                # writes nothing (still reads
                                                # from Couchbase, since
                                                # discovery needs a live query)

Requirements:
  pip install boto3 couchbase --break-system-packages
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone


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
# work; the model check mainly guards against config drift.
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
# The two vector-backed collections and how to build embeddable text from
# each document shape. Add a new entry here if a new vector-backed
# collection is introduced later — no per-document hardcoding needed.
# ---------------------------------------------------------------------------
VECTOR_BACKED_COLLECTIONS = [
    {
        "label": "semantic_memory",
        "scope": "knowledge",
        "collection": "semantic_memory",
        "text_fn": lambda doc: f"{doc['fact']}. {doc['content']}",
        "expected_dimension": 1536,
    },
    {
        "label": "memory_intent_patterns",
        "scope": "system_intelligence",
        "collection": "memory_intent_patterns",
        "text_fn": lambda doc: doc["question_text"],
        "expected_dimension": 1536,
    },
]


def find_docs_needing_embeddings(cluster, bucket_name, scope_name, collection_name):
    """Returns [(doc_id, doc_dict), ...] for every doc with an empty embedding."""
    query = f"""
        SELECT META(t).id AS __id, t.*
        FROM `{bucket_name}`.`{scope_name}`.`{collection_name}` AS t
        WHERE ARRAY_LENGTH(t.embedding) = 0
    """
    result = cluster.query(query)
    docs = []
    for row in result:
        doc_id = row.pop("__id")
        docs.append((doc_id, row))
    return docs


def get_couchbase_collection(cluster, bucket_name, scope_name, collection_name):
    bucket = cluster.bucket(bucket_name)
    return bucket.scope(scope_name).collection(collection_name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Find docs and generate embeddings, but do not write anything back to Capella.",
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

    # ---- Couchbase connection (required even in --dry-run, since finding
    # docs that need embeddings means querying live data) ----
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

    # cert_path is optional — the Python SDK bundles Capella's standard root
    # cert by default (SDK 4.1+), so couchbases:// alone often works without
    # this. Only needed for non-standard networking.
    if ca_bundle:
        auth = PasswordAuthenticator(username, password, cert_path=ca_bundle)
    else:
        auth = PasswordAuthenticator(username, password)

    cluster = Cluster(conn_str, ClusterOptions(auth))
    cluster.wait_until_ready(timedelta(seconds=15))

    total_written = 0

    for target in VECTOR_BACKED_COLLECTIONS:
        print(f"\n--- {target['label']} ---")

        pending = find_docs_needing_embeddings(cluster, bucket_name, target["scope"], target["collection"])
        if not pending:
            print("Nothing to do — no documents with an empty embedding.")
            continue

        print(f"Found {len(pending)} document(s) needing an embedding.")
        collection = get_couchbase_collection(cluster, bucket_name, target["scope"], target["collection"])

        for doc_id, doc in pending:
            text_to_embed = target["text_fn"](doc)
            print(f"\n  {doc_id}")
            print(f"  Embedding text: {text_to_embed!r}")

            vector, actual_model = generate_embedding(bedrock_client, embed_model_id, text_to_embed)

            validate_embedding_compatibility(
                vector,
                expected_model=embed_model_id,
                expected_dimension=target["expected_dimension"],
                actual_model=actual_model,
            )
            print(f"  Generated {len(vector)}-dim vector using {actual_model}. Compatibility check passed.")

            if args.dry_run:
                print("  (--dry-run: not writing to Capella)")
                continue

            doc["embedding"] = vector
            doc["embedding_metadata"]["model"] = actual_model
            doc["embedding_metadata"]["dimension"] = len(vector)
            doc["embedding_metadata"]["generated_at"] = datetime.now(timezone.utc).isoformat()
            collection.upsert(doc_id, doc)
            total_written += 1
            print(f"  Wrote embedding to {target['scope']}.{target['collection']}::{doc_id}")

    print(f"\nDone. {'(dry run -- nothing written)' if args.dry_run else f'{total_written} document(s) updated.'}")


if __name__ == "__main__":
    main()
