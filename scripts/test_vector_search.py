#!/usr/bin/env python3
"""
test_vector_search.py

Quick sanity check that vector search actually works end to end:
embeds a query with Bedrock Titan, runs it against
semantic_memory_vector_index with an active=true pre-filter, and prints
whatever comes back with its similarity score.

Usage:
  python scripts/test_vector_search.py "why is a customer worried about database performance"

  (with no argument, uses a default query that should match semantic_fact_456)

Requires the same env vars as embed_seed_data.py:
  CB_CONN_STR, CB_USERNAME, CB_PASSWORD, CB_BUCKET (optional), CB_CA_BUNDLE (optional)
  BEDROCK_REGION, EMBED_MODEL_ID
"""

import json
import os
import sys
import time
from datetime import timedelta

import boto3
from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions, SearchOptions
from couchbase.search import BooleanFieldQuery, MatchNoneQuery, SearchRequest
from couchbase.vector_search import VectorQuery, VectorSearch


def embed(text: str):
    bedrock = boto3.client("bedrock-runtime", region_name=os.environ["BEDROCK_REGION"])
    body = json.dumps({"inputText": text})
    response = bedrock.invoke_model(
        modelId=os.environ["EMBED_MODEL_ID"],
        body=body,
        accept="application/json",
        contentType="application/json",
    )
    return json.loads(response["body"].read())["embedding"]


def main():
    query_text = sys.argv[1] if len(sys.argv) > 1 else "why is a customer worried about database performance"

    print(f"Query: {query_text!r}")
    t0 = time.perf_counter()
    vector = embed(query_text)
    embed_ms = round((time.perf_counter() - t0) * 1000)
    print(f"Embedded to {len(vector)} dims. ({embed_ms}ms)\n")

    ca_bundle = os.environ.get("CB_CA_BUNDLE")
    if ca_bundle:
        auth = PasswordAuthenticator(os.environ["CB_USERNAME"], os.environ["CB_PASSWORD"], cert_path=ca_bundle)
    else:
        auth = PasswordAuthenticator(os.environ["CB_USERNAME"], os.environ["CB_PASSWORD"])

    t0 = time.perf_counter()
    cluster = Cluster(os.environ["CB_CONN_STR"], ClusterOptions(auth))
    cluster.wait_until_ready(timedelta(seconds=15))
    connect_ms = round((time.perf_counter() - t0) * 1000)
    print(f"Connected to cluster. ({connect_ms}ms)")

    bucket = cluster.bucket(os.environ.get("CB_BUCKET", "agent_memory"))
    scope = bucket.scope("knowledge")

    # Pre-filter on active=true — this is the governance rule from design
    # spec Section 10.4 in action: stale generations are excluded INSIDE
    # the vector search itself, not filtered out after the fact.
    prefilter = BooleanFieldQuery(True, field="active")
    vector_query = VectorQuery("embedding", vector, num_candidates=5, prefilter=prefilter)
    vector_search = VectorSearch.from_vector_query(vector_query)

    # Run the SAME search twice in this one process. Capella's own UI
    # reported this exact query completing server-side in <1ms -- so if
    # our client-measured "search" time is still seconds long, the cost
    # is happening client-side, not on the cluster. Running it twice
    # tests a specific theory: the SDK may lazily establish a
    # Search-service-specific connection on the FIRST search call, even
    # though cluster.wait_until_ready() already confirmed KV/bootstrap
    # connectivity. If call #2 is dramatically faster than call #1, that
    # confirms a one-time, per-process Search connection warmup cost --
    # the same category of problem as the earlier per-request Couchbase
    # connection issue, just specific to the Search service this time.
    search_times = []
    for attempt in (1, 2):
        t0 = time.perf_counter()
        request = SearchRequest.create(MatchNoneQuery()).with_vector_search(vector_search)
        result = scope.search(
            "semantic_memory_vector_index",
            request,
            SearchOptions(fields=["fact", "content", "active", "logical_id"], limit=5),
        )
        rows = list(result.rows())  # force evaluation -- the SDK streams lazily, so
                                     # the search hasn't actually finished until this
        elapsed_ms = round((time.perf_counter() - t0) * 1000)
        search_times.append(elapsed_ms)
        print(f"Search attempt {attempt} completed. ({elapsed_ms}ms)")

    print()
    if not rows:
        print("No results. Check that the index has finished building and the seed doc has a real embedding.")
        return

    for row in rows:
        print(f"score={row.score:.4f}  id={row.id}")
        print(f"  fields: {row.fields}\n")

    search_ms = search_times[0]  # first-call time, for the summary line below
    print(f"--- Summary: embed={embed_ms}ms  connect={connect_ms}ms  "
          f"search_attempt_1={search_times[0]}ms  search_attempt_2={search_times[1]}ms  "
          f"total={embed_ms+connect_ms+sum(search_times)}ms ---")


if __name__ == "__main__":
    main()
