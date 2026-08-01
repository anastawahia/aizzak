"""Surface tests for the driven-port contracts (02-port-contracts §1).

Ports are pure abstractions; structural conformance is enforced by mypy. These
tests just lock the public surface: the value objects construct as declared and
the Protocol methods exist under the expected names.
"""

from __future__ import annotations

from app.framework import ports


def test_port_value_objects_construct() -> None:
    msg = ports.LlmMessage(role="user", content="hi")
    params = ports.LlmParams(model="gpt-x")
    assert (msg.role, params.temperature) == ("user", 0.7)

    hit = ports.VectorHit(id="018f", score=0.9, payload={"workspace_id": "ws"})
    assert hit.payload["workspace_id"] == "ws"

    tokens = ports.OAuthTokens(
        access_token="a", refresh_token=None, expires_in=3600, scopes=("repo",)
    )
    assert tokens.scopes == ("repo",)

    sparse = ports.SparseVector(indices=[1], values=[0.5])
    assert (sparse.indices, sparse.values) == ([1], [0.5])


def test_port_protocols_expose_expected_methods() -> None:
    for proto, methods in (
        (ports.LLMProvider, ("complete", "stream", "supports")),
        (ports.EmbeddingProvider, ("embed", "dimensions")),
        (ports.VectorStore, ("ensure_collection", "upsert", "search", "delete")),
        (ports.StorageProvider, ("put", "get", "delete", "presign_get", "presign_put")),
        (ports.CacheProvider, ("get", "set", "delete", "incr", "expire")),
        (ports.SecretsProvider, ("get_secret", "encrypt", "decrypt")),
        (ports.AuthProvider, ("verify_token",)),
        (ports.ConnectorProvider, ("authorize_url", "exchange_code", "refresh")),
        (ports.MCPClient, ("list_tools", "call_tool")),
        # HybridVectorStore (3.k3) is a knowledge-only superset of VectorStore
        # (ISP) -- it must expose its own two methods PLUS everything it
        # inherits from VectorStore unchanged.
        (
            ports.HybridVectorStore,
            (
                "ensure_hybrid_collection",
                "search_sparse",
                "ensure_collection",
                "upsert",
                "search",
                "delete",
            ),
        ),
    ):
        for method in methods:
            assert hasattr(proto, method), f"{proto.__name__} missing {method}"
