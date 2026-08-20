"""Driven ports — pure abstractions implemented by ``infrastructure`` adapters
and bound in the Composition Root (02-port-contracts §1, D-17)."""

from __future__ import annotations

from app.framework.ports.auth_provider import AuthProvider, Identity
from app.framework.ports.cache_provider import CacheProvider
from app.framework.ports.connector_provider import ConnectorProvider, OAuthTokens
from app.framework.ports.embedding_provider import EmbeddingProvider, EmbeddingResult
from app.framework.ports.event_publisher import EventPublisher, StreamEvent
from app.framework.ports.image_provider import ImageProvider, ImageRequest, ImageResult
from app.framework.ports.llm_provider import (
    LlmChunk,
    LlmMessage,
    LlmParams,
    LLMProvider,
    LlmResult,
)
from app.framework.ports.mcp_client import MCPClient, McpTarget, McpTool
from app.framework.ports.rerank_provider import RerankedDocument, RerankProvider
from app.framework.ports.secrets_provider import SecretsProvider
from app.framework.ports.storage_provider import StorageProvider
from app.framework.ports.vector_store import (
    HybridVectorStore,
    SparseVector,
    VectorHit,
    VectorPoint,
    VectorStore,
)
from app.framework.ports.video_provider import VideoProvider, VideoRequest, VideoResult
from app.framework.ports.web_search_provider import WebSearchHit, WebSearchProvider

__all__ = [
    "AuthProvider",
    "CacheProvider",
    "ConnectorProvider",
    "EmbeddingProvider",
    "EmbeddingResult",
    "EventPublisher",
    "HybridVectorStore",
    "Identity",
    "ImageProvider",
    "ImageRequest",
    "ImageResult",
    "LLMProvider",
    "LlmChunk",
    "LlmMessage",
    "LlmParams",
    "LlmResult",
    "MCPClient",
    "McpTarget",
    "McpTool",
    "OAuthTokens",
    "RerankProvider",
    "RerankedDocument",
    "SecretsProvider",
    "SparseVector",
    "StorageProvider",
    "StreamEvent",
    "VectorHit",
    "VectorPoint",
    "VectorStore",
    "VideoProvider",
    "VideoRequest",
    "VideoResult",
    "WebSearchHit",
    "WebSearchProvider",
]
