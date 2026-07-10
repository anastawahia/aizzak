# عقود المنافذ (Ports & Contracts)

> المنافذ تُعرَّف في `framework/ports/` (تجريدات نقية)، وتُنفَّذ في `infrastructure/` (محوّلات)، وتُربط في Composition Root عبر **Manual DI**.
> **لا يستورد أحدٌ المحوّلات المحسوسة إلا Composition Root** (D‑17).
> النمط: `typing.Protocol` للمنافذ المُقادة (بنيوي، يسهّل الاختبار)، و`ABC` للعقود القابلة للوراثة (BaseAgent/BaseTool).
> كل عمليات الـI/O **async**. التوقيعات أدناه عقدٌ نهائي للطرق (أسماء/أنواع)، لا تنفيذ.

## 0) أنواع مشتركة (framework)
```python
Uuid = str                      # نص UUIDv7
Json = dict[str, Any]

@dataclass(frozen=True, slots=True)
class ExecutionContext:          # request/job-scoped، stateless، محقون
    workspace_id: Uuid
    user_id: Uuid | None
    correlation_id: Uuid
    roles: frozenset[str]
    request_id: Uuid | None = None
```

---

## 1) المنافذ المُقادة (Driven Ports)
> الأساس العشرة (1.1–1.10) + منفذا `integrations` الخارجيان (1.11–1.12) — كلها تُنفَّذ في `infrastructure/` وتُحقن في Composition Root.

### 1.1 `LLMProvider` — `framework/ports/llm_provider.py` (D‑15)
```python
@dataclass(frozen=True, slots=True)
class LlmMessage: role: str; content: str            # role ∈ {system,user,assistant,tool}
@dataclass(frozen=True, slots=True)
class LlmParams:  model: str; temperature: float = 0.7; max_tokens: int | None = None
                  top_p: float | None = None; stop: list[str] | None = None
                  tools: list[Json] | None = None      # مخططات أدوات محايدة
@dataclass(frozen=True, slots=True)
class LlmChunk:   delta: str; finish_reason: str | None = None
@dataclass(frozen=True, slots=True)
class LlmResult:  content: str; finish_reason: str; prompt_tokens: int; completion_tokens: int
                  tool_calls: list[Json] | None = None

class LLMProvider(Protocol):
    provider: str                                       # 'openai'|'gemini'|'claude'|'ollama'|'openrouter'
    async def complete(self, messages: Sequence[LlmMessage], params: LlmParams,
                       api_key: str) -> LlmResult: ...
    async def stream(self, messages: Sequence[LlmMessage], params: LlmParams,
                     api_key: str) -> AsyncIterator[LlmChunk]: ...
    def supports(self, capability: str) -> bool: ...    # 'tools'|'vision'|'streaming'
```

### 1.2 `EmbeddingProvider` — `embedding_provider.py` (D‑14)
```python
@dataclass(frozen=True, slots=True)
class EmbeddingResult: vectors: list[list[float]]; model: str; dimensions: int; tokens: int

class EmbeddingProvider(Protocol):
    provider: str
    async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult: ...
    def dimensions(self, model: str) -> int: ...
```

### 1.3 `ImageProvider` — `image_provider.py` (D‑02)
```python
@dataclass(frozen=True, slots=True)
class ImageRequest: prompt: str; width: int; height: int; model: str; extra: Json | None = None
@dataclass(frozen=True, slots=True)
class ImageResult:  content: bytes; content_type: str; model: str

class ImageProvider(Protocol):
    provider: str
    async def generate(self, req: ImageRequest, api_key: str) -> ImageResult: ...
```

### 1.4 `VideoProvider` — `video_provider.py` (D‑02)
```python
@dataclass(frozen=True, slots=True)
class VideoRequest: prompt: str; duration_s: int; model: str; extra: Json | None = None
@dataclass(frozen=True, slots=True)
class VideoResult:  content: bytes | None; remote_url: str | None; content_type: str; model: str

class VideoProvider(Protocol):
    provider: str
    async def generate(self, req: VideoRequest, api_key: str) -> VideoResult: ...
```

### 1.5 `VectorStore` — `vector_store.py` (D‑01 · Qdrant)
```python
@dataclass(frozen=True, slots=True)
class VectorPoint: id: Uuid; vector: list[float]; payload: Json
@dataclass(frozen=True, slots=True)
class VectorHit:   id: Uuid; score: float; payload: Json

class VectorStore(Protocol):
    async def ensure_collection(self, name: str, dim: int, distance: str = 'cosine') -> None: ...
    async def upsert(self, collection: str, points: Sequence[VectorPoint]) -> None: ...
    async def search(self, collection: str, vector: list[float], k: int,
                     flt: Json | None = None) -> list[VectorHit]: ...     # flt يحمل workspace_id دائماً
    async def delete(self, collection: str, ids: Sequence[Uuid]) -> None: ...
```

### 1.6 `StorageProvider` — `storage_provider.py` (MinIO)
```python
class StorageProvider(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def presign_get(self, key: str, ttl_s: int) -> str: ...
    async def presign_put(self, key: str, ttl_s: int, content_type: str) -> str: ...
```

### 1.7 `CacheProvider` — `cache_provider.py` (Redis)
```python
class CacheProvider(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes, ttl_s: int | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def incr(self, key: str, amount: int = 1) -> int: ...
    async def expire(self, key: str, ttl_s: int) -> None: ...
```

### 1.8 `EventPublisher` — `event_publisher.py` (Redis Streams · D‑18/20)
```python
@dataclass(frozen=True, slots=True)
class StreamEvent: stream: str; event: Json          # event = مظروف CloudEvents كامل

class EventPublisher(Protocol):
    async def publish(self, stream: str, event: Json) -> str: ...          # يعيد stream entry id
    async def publish_batch(self, items: Sequence[StreamEvent]) -> list[str]: ...
```
> يُستخدم من `outbox_relay` فقط للنشر؛ الوحدات لا تنشر مباشرة بل تكتب Outbox.

### 1.9 `SecretsProvider` — `secrets_provider.py` (Vault · D‑03/22)
```python
class SecretsProvider(Protocol):
    async def get_secret(self, path: str) -> Json: ...                      # Vault KV
    async def encrypt(self, key_name: str, plaintext: bytes) -> str: ...    # Transit → ciphertext
    async def decrypt(self, key_name: str, ciphertext: str) -> bytes: ...   # Transit
```

### 1.10 `AuthProvider` — `auth_provider.py` (Firebase · D‑25)
```python
@dataclass(frozen=True, slots=True)
class Identity: firebase_uid: str; email: str | None; email_verified: bool; claims: Json

class AuthProvider(Protocol):
    async def verify_token(self, id_token: str) -> Identity: ...            # تحقّق JWT محلي بمفاتيح مُخزّنة
```

### 1.11 `ConnectorProvider` — `connector_provider.py` (integrations · OAuth · FR‑120/124)
```python
@dataclass(frozen=True, slots=True)
class OAuthTokens: access_token: str; refresh_token: str | None; expires_in: int; scopes: tuple[str, ...]

class ConnectorProvider(Protocol):
    connector: str                                       # 'github'|'slack'|… من الكتالوج
    def authorize_url(self, redirect_uri: str, state: str, scopes: Sequence[str]) -> str: ...
    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens: ...
    async def refresh(self, refresh_token: str) -> OAuthTokens: ...          # تجديد كسول عند الطلب (FR‑124)
```

### 1.12 `MCPClient` — `mcp_client.py` (integrations · MCP بعيد HTTP/SSE حصراً · FR‑120)
```python
@dataclass(frozen=True, slots=True)
class McpTool:   name: str; description: str; parameters: Json           # مخطط أدوات محايد
@dataclass(frozen=True, slots=True)
class McpTarget: endpoint_url: str; transport: str; auth: Json | None    # transport ∈ {'http','sse'} فقط

class MCPClient(Protocol):
    async def list_tools(self, target: McpTarget) -> list[McpTool]: ...      # اكتشاف وقت التشغيل
    async def call_tool(self, target: McpTarget, name: str, args: Json) -> Json: ...
# نقل stdio المحلي خارج v1 (يتبع sandbox، ARC‑15) — لا تشغيل خوادم MCP كعمليات فرعية.
```

---

## 2) منافذ الوحدات (Inbound + Repository)

كل وحدة تعرّف في `modules/<m>/ports/`:
- **Repository Port** (Outbound): تجريد الاستمرار، ينفّذه `adapters/sql_repository.py`.
- **Inbound Port** (اختياري): واجهة تستدعيها الوكلاء/الوحدات الأخرى (بدل الاستيراد المباشر).

**عقد Repository عام (نمط، لا وراثة إلزامية):**
```python
class Repository(Protocol[TAggregate]):
    async def get(self, ctx: ExecutionContext, id: Uuid) -> TAggregate | None: ...
    async def add(self, ctx: ExecutionContext, entity: TAggregate) -> None: ...
    async def save(self, ctx: ExecutionContext, entity: TAggregate) -> None: ...   # قفل تفاؤلي على version
    async def list(self, ctx: ExecutionContext, *, limit: int, cursor: str | None) -> Page[TAggregate]: ...
```
> كل طريقة تتلقّى `ctx` لتمرير `workspace_id` إلى ضبط RLS والترشيح التطبيقي.
> **المؤشّر (`cursor`) مبهم (opaque) من نوع `str`** ومتّسق عبر كل منافذ Repository ومع `API‑03`. داخلياً يُرمّز **keyset على `id` (UUIDv7)** نصّاً (base64url)؛ لا يعتمد عليه المستدعي بنيوياً ولا يُفسّره — يُمرَّر كما هو من `meta.next_cursor`.

**أمثلة منافذ الوحدات (Inbound):**
```python
# files/ports/repository.py
class FileRepository(Protocol):
    async def get(self, ctx, file_id: Uuid) -> File | None: ...
    async def add(self, ctx, file: File) -> None: ...
    async def list(self, ctx, *, limit: int, cursor: str | None) -> Page[File]: ...
    async def mark_ready(self, ctx, file_id: Uuid, checksum: str) -> None: ...

# files/ports/inbound.py  (تستدعيه الوكلاء/knowledge بلا استيراد مباشر للوحدة)
class FilesQuery(Protocol):
    async def get_readable(self, ctx, file_id: Uuid) -> FileView | None: ...

# knowledge/ports/repository.py
class DocumentRepository(Protocol):
    async def get(self, ctx, doc_id: Uuid) -> Document | None: ...
    async def add(self, ctx, doc: Document) -> None: ...
    async def set_status(self, ctx, doc_id: Uuid, status: str, error: str | None = None) -> None: ...
    async def add_chunks(self, ctx, chunks: Sequence[Chunk]) -> None: ...   # idempotent عبر uq(document_id,seq)

# knowledge/ports/inbound.py
class KnowledgeRetrieval(Protocol):
    async def retrieve(self, ctx, query: str, k: int) -> list[RetrievedChunk]: ...

# conversations/ports/repository.py
class ConversationRepository(Protocol):
    async def get(self, ctx, conv_id: Uuid) -> Conversation | None: ...
    async def add(self, ctx, conv: Conversation) -> None: ...
    async def list_by_agent(self, ctx, agent_key: str, *, limit: int, cursor: str | None) -> Page[Conversation]: ...
    async def append_message(self, ctx, msg: Message) -> None: ...          # يزيد seq بقفل تفاؤلي

# access/ports/inbound.py
class AuthorizationService(Protocol):
    async def roles_of(self, ctx, user_id: Uuid) -> frozenset[str]: ...
    def is_allowed(self, roles: frozenset[str], permission: str) -> bool: ...  # نقي

# credentials/ports/inbound.py  (يستخدمه ProviderResolver — §3.5)
class CredentialResolver(Protocol):
    async def resolve(self, ctx, provider: str) -> ResolvedKey: ...          # user ثم platform؛ لا fallback بين مزوّدين

# ── usage — منافذ واردة تُستدعى من المُنسِّق (طبقة الوكلاء) حصراً — لا Redis Streams ──
# usage/ports/inbound.py
@dataclass(frozen=True, slots=True)
class UsageCharge:                          # ما يوفّره المُنسِّق (FR‑134)
    agent: str; provider: str; tokens: int; cost_micros: int; operation_id: Uuid
@dataclass(frozen=True, slots=True)
class LimitDecision:                        # كائن قرار — لا bool مجرّد (FR‑132)
    allowed: bool
    reason: str | None = None              # 'quota_exceeded'|'budget_exceeded'|None
    remaining: int | None = None
    reservation_id: Uuid | None = None     # يبقى None في v1؛ حقل تطوّر reserve/commit بلا كسر عقد

class UsageEnforcement(Protocol):          # قبل العملية
    async def check(self, ctx, agent: str, provider: str,
                    estimated_tokens: int | None = None) -> LimitDecision: ...
    # نقاط توسعة (غير مُفعّلة v1، تُضاف دون كسر): reserve(...) -> LimitDecision ثم commit(ctx, reservation_id, UsageCharge)

class UsageCapture(Protocol):              # بعد العملية — إلحاق متزامن idempotent
    async def record(self, ctx, charge: UsageCharge) -> None: ...            # تكرار operation_id ⇒ تجاهُل بهدوء

# usage/ports/repository.py
class UsageLedgerRepository(Protocol):
    async def append(self, ctx, charge: UsageCharge) -> bool: ...            # False عند تعارض UNIQUE(operation_id)
    async def rollup(self, ctx, agent: str, provider: str, period: str) -> UsageTotals: ...
    async def get_limits(self, ctx) -> list[UsageLimit]: ...

# ── integrations — منفذ وارد يستهلكه نظام الأدوات (FR‑52/122) + Repository ──
# integrations/ports/inbound.py
@dataclass(frozen=True, slots=True)
class DiscoveredTool: name: str; description: str; parameters: Json; source: str  # 'connector:<id>'|'mcp:<name>'

class ToolCatalog(Protocol):               # كتالوج ديناميكي لكل Workspace (اكتشاف MCP وقت التشغيل)
    async def list_tools(self, ctx) -> list[DiscoveredTool]: ...
    async def invoke_tool(self, ctx, name: str, args: Json) -> Json: ...     # يوجّه للموصّل/MCP الصحيح خلف العقد الموحّد

# integrations/ports/repository.py
class ConnectionRepository(Protocol):
    async def get(self, ctx, conn_id: Uuid) -> Connection | None: ...
    async def add(self, ctx, conn: Connection) -> None: ...
    async def list_connected(self, ctx) -> list[Connection]: ...
    async def update_tokens(self, ctx, conn_id: Uuid, token_ref: str, key_id: str, expires_at) -> None: ...  # تجديد كسول
```
> النمط نفسه يتكرّر لـ `memory`, `media`, `workspace`, `credentials`, `integrations`, `usage`. **لا وحدة تستورد Repository وحدة أخرى** — فقط Inbound Port محقون. منافذ `usage` الواردة **متزامنة بلا أحداث** (`FR‑131`, `EVT‑10`).

---

## 3) عقود الإطار (Framework Contracts)

### 3.1 `BaseTool` — `framework/tools/base_tool.py` (D‑08)
```python
@dataclass(frozen=True, slots=True)
class ToolSpec: name: str; description: str; parameters: Json   # JSON Schema للمعاملات

class BaseTool(ABC):
    spec: ClassVar[ToolSpec]
    @abstractmethod
    async def run(self, ctx: ExecutionContext, args: Json) -> Json: ...
# الأداة محوّل رفيع فوق Ports محقونة؛ الوكيل يستخدمها عبر الاسم دون معرفة تنفيذها.
```

### 3.2 `BaseAgent` + `AgentMetadata` — `framework/agent_runtime/` (D‑05/13)
```python
@dataclass(frozen=True, slots=True)
class AgentMetadata:                                # من manifest.py
    key: str; name: str; version: str; description: str
    capabilities: frozenset[str]; required_permissions: frozenset[str]
    default_tools: tuple[str, ...] = ()

class AgentLifecycle(str, Enum):
    CREATED='created'; INITIALIZED='initialized'; RUNNING='running'
    COMPLETED='completed'; FAILED='failed'; DISPOSED='disposed'

@dataclass(frozen=True, slots=True)
class AgentRequest:  conversation_id: Uuid | None; input: Json; stream: bool = False
@dataclass(frozen=True, slots=True)
class AgentEvent:    type: str; data: Json          # token|tool_call|final|error (للبثّ)

class BaseAgent(ABC):
    metadata: ClassVar[AgentMetadata]
    def __init__(self, ctx: ExecutionContext, deps: "AgentDependencies") -> None: ...  # stateless per request
    @abstractmethod
    async def initialize(self) -> None: ...          # تحميل السياق/الذاكرة/المحادثة
    @abstractmethod
    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]: ...
    async def dispose(self) -> None: ...             # تحرير الموارد
# AgentDependencies: حزمة Ports محقونة (llm, tools, conversations, memory, knowledge...) — بلا infrastructure.
```

### 3.3 `WorkflowEngine` — `framework/workflows/engine.py` (D‑04/09/12)
```python
@dataclass(frozen=True, slots=True)
class WorkflowStep:       agent_key: str; input_map: Json
@dataclass(frozen=True, slots=True)
class WorkflowDefinition: key: str; name: str; steps: tuple[WorkflowStep, ...]   # ثابت بالكود
@dataclass(frozen=True, slots=True)
class WorkflowResult:     workflow_key: str; conversation_id: Uuid; outputs: list[Json]

class WorkflowEngine(Protocol):
    async def run(self, ctx: ExecutionContext, definition: WorkflowDefinition,
                  initial_input: Json) -> AsyncIterator[AgentEvent]: ...
# خطوات خفيفة تُنفَّذ متزامنة؛ الخطوة الثقيلة تُحال إلى Streams ويتابعها الـWorker (D‑04).
```

### 3.4 Registries (Platform Components)
```python
class AgentRegistry(Protocol):
    def register(self, metadata: AgentMetadata, factory: Callable[..., BaseAgent]) -> None: ...
    def create(self, key: str, ctx: ExecutionContext, deps) -> BaseAgent: ...   # إنشاء لكل طلب
    def list(self) -> list[AgentMetadata]: ...

class ToolRegistry(Protocol):
    def register(self, tool_cls: type[BaseTool]) -> None: ...
    def get(self, name: str) -> type[BaseTool]: ...

class WorkflowRegistry(Protocol):
    def register(self, definition: WorkflowDefinition) -> None: ...
    def get(self, key: str) -> WorkflowDefinition: ...
```
> **الكتالوج الديناميكي (FR‑52):** `ToolRegistry` يحمل الأدوات الثابتة المُسجَّلة بالكود؛ أمّا أدوات الموصّلات المُفعّلة وأدوات MCP المُكتشَفة وقت التشغيل فتُحلّ **لكل Workspace** عبر منفذ `integrations.ToolCatalog` (2 أعلاه) خلف نفس عقد `BaseTool`. يحلّها الوكيل **بالاسم** دون معرفة مصدرها ودون اقتران مباشر بموصّل.

### 3.5 `ProviderResolver` — `framework/providers/resolver.py` (D‑16 · FR‑73)
> يختار المزوّد/الموديل من **جدول توجيه في الإعداد** (`Settings`) — **لا من كود صلب** — ويحلّ المفتاح عبر `CredentialResolver` (user ثم platform، **بلا fallback بين مزوّدين**، D‑16). تبديل المزوّد **إعدادٌ لا تعديل كود** (`FR‑73`, `OQ‑04`).
```python
@dataclass(frozen=True, slots=True)
class ResolvedProvider:
    provider: str                         # 'openai'|'gemini'|'claude'|'ollama'|'openrouter'
    model: str
    api_key: str                          # من CredentialResolver (user→platform)

class ProviderResolver(Protocol):         # كل الاختيار من الإعداد (FR‑73)
    async def resolve_llm(self, ctx: ExecutionContext, *, capability: str,
                          model: str | None = None) -> tuple[LLMProvider, ResolvedProvider]: ...
    async def resolve_embedding(self, ctx: ExecutionContext, *,
                                model: str | None = None) -> tuple[EmbeddingProvider, ResolvedProvider]: ...
# جدول التوجيه (capability/agent → provider+model) يُقرأ من Settings؛ المحوّل الملموس يُحقن في Composition Root.
```
