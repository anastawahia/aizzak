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
                  tool_call_id: str | None = None      # على role="tool": أي نداء يُجيب
                  tool_calls: list[Json] | None = None  # على role="assistant": النداءات التي أطلقها
@dataclass(frozen=True, slots=True)
class LlmParams:  model: str; temperature: float = 0.7; max_tokens: int | None = None
                  top_p: float | None = None; stop: list[str] | None = None
                  tools: list[Json] | None = None      # مخططات أدوات محايدة
@dataclass(frozen=True, slots=True)
class LlmChunk:   delta: str; finish_reason: str | None = None
                  tool_calls: list[Json] | None = None      # الدفعة الأخيرة فقط، مُجمَّعة
                  prompt_tokens: int | None = None          # الدفعة الأخيرة فقط
                  completion_tokens: int | None = None      # None ⇒ لم يُبلّغ المزوّد ⇒ قدِّر
@dataclass(frozen=True, slots=True)
class LlmResult:  content: str; finish_reason: str; prompt_tokens: int; completion_tokens: int
                  tool_calls: list[Json] | None = None

class LLMProvider(Protocol):
    provider: str                                       # 'openai'|'gemini'|'claude'|'ollama'|'openrouter'
    async def complete(self, messages: Sequence[LlmMessage], params: LlmParams,
                       api_key: str) -> LlmResult: ...
    def stream(self, messages: Sequence[LlmMessage], params: LlmParams,
               api_key: str) -> AsyncIterator[LlmChunk]: ...
    def supports(self, capability: str) -> bool: ...    # تلميح توجيه لا ضمان — انظر (ج)
```

> **تعديل 4.7‑أ (رفع حاجب المُنسِّق).** رصدت 2.8‑أ و2.8‑ب‑1 أربعة قيود في هذا المنفذ ومُنعتا صراحةً من تعديله (`D‑17`)؛ حُسمت كلها هنا، وكل تغيير **إضافي بقيمة `None` افتراضية** ⇒ صفر كسر للمحوّلَين الشاحنَين:
> - **(أ) `LlmChunk` بلا عدّادات** ⇒ نداء مبثوث يعطي **صفر** بيانات رموز للمحوّلَين معاً، فيشحن `UsageCapture` (`FR‑134`) **تقديراً** دائماً. الحلّ: حقلا `prompt_tokens`/`completion_tokens`. *(مُثبَت حيّاً على خادم Ollama حقيقي: `tests/integration/test_ollama_llm.py::test_stream_terminal_chunk_reports_real_token_counters`.)*
> - **(ب) `LlmChunk` بلا أدوات** ⇒ البثّ+الأدوات غير قابل للتعبير. الحلّ: حقل `tool_calls`، **مُجمَّعاً على الدفعة الأخيرة** لا شظايا (OpenAI يبثّ `arguments` قطعاً نصّية جزئية؛ تجميعها شغل المحوّل، وإلا تسرّبت دلالات المزوّد إلى المنفذ).
> - **(ج) `supports('tools')` تلميح لا ضمان** (يعتمد على النموذج في Ollama) ⇒ **مكان الحقيقة جدول التوجيه `D‑16`**، وعلى المُنسِّق أن يعامل رفض القدرة وقت النداء كفشل مترجَم عادي.
> - **(د) جولة الأدوات غير قابلة للتعبير** — أشدّها: كان المنفذ يُعلن دور `tool` ولا يستطيع أي محوّل ربط النتيجة بالنداء الذي أجابته (`LlmResult.tool_calls` يُسقط `id`، و`LlmMessage` بلا `tool_call_id` ولا وسيلة لإعادة تشغيل دور المساعد). الحلّ: حقلا `tool_call_id`/`tool_calls` على `LlmMessage`.
>
> **الشكل المحايد لنداء الأداة** = `{"id": str, "name": str, "arguments": Json}`. `id` من المزوّد حيث يوجد (OpenAI)، و**مُصنَّع في المحوّل** حيث لا يوجد (Ollama لا يُصدر أيّاً) ⇒ يربط المستدعي النتيجة بنداءها دون معرفة أيّ مزوّد أجاب.
>
> **`def stream` لا `async def`** — الوثيقة كانت تخالف المنفذ المبني منذ 2.1 (بند مزامنة مُسجَّل)؛ صُحّح هنا: الدالة **تُعيد** مولّداً غير متزامن ولا تنتظره، فتقع حرّاس `ValidationError` وقت النداء بلا I/O.

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
class SparseVector: indices: list[int]; values: list[float]        # مصطلحات متفرّقة (IDF عند الخادم)
@dataclass(frozen=True, slots=True)
class VectorPoint: id: Uuid; vector: list[float]; payload: Json; sparse: SparseVector | None = None
@dataclass(frozen=True, slots=True)
class VectorHit:   id: Uuid; score: float; payload: Json

class VectorStore(Protocol):
    async def ensure_collection(self, name: str, dim: int, distance: str = 'cosine') -> None: ...
    async def upsert(self, collection: str, points: Sequence[VectorPoint]) -> None: ...
    async def search(self, collection: str, vector: list[float], k: int,
                     flt: Json | None = None) -> list[VectorHit]: ...     # flt يحمل workspace_id دائماً
    async def delete(self, collection: str, ids: Sequence[Uuid]) -> None: ...

# المنفذ الهجين — يرثه المِحوَل نفسه، ويُعلَن منفصلاً لأن `memory` لا تطلب شيئاً منه:
class HybridVectorStore(VectorStore, Protocol):
    async def ensure_hybrid_collection(self, name: str, dim: int, *, distance: str = 'cosine') -> None: ...
    async def search_sparse(self, collection: str, sparse: SparseVector, k: int,
                            flt: Json | None = None) -> list[VectorHit]: ...
    async def ensure_payload_index(self, collection: str, field: str, *, tenant: bool = False) -> None: ...
```
> **`ensure_payload_index` على الهجين وحده** (خطّة الوحدات، §3.4): `memory` تكتب وتبحث ولا تطلب فهرس بطاقة، وقاعدة **فصل الواجهات** تمنع تحميلها منفذاً لا تستعمله. `tenant=True` ⇒ `is_tenant` — إعادةُ ترتيبٍ فيزيائيّ للنقاط على القرص بمفتاح الوحدة، لا تسريعُ مطابقةٍ فقط. تُستدعى من `ensure_hybrid_collection` لثلاثة مفاتيح: `workspace_id` و`document_id` عاديّين و`space` بـ`is_tenant`.
> **والفهرس `KEYWORD` لا `uuid`** رغم أنّ المفاتيح الثلاثة UUIDv7: فهرسُ uuid **يرفض** قيمةً لا تُحلَّل، فيحوّل حمولةً شاذّةً من قراءةٍ أبطأ إلى **كتابةٍ فاشلة**؛ و`KEYWORD` هو ما تُجاب منه `MatchValue`/`MatchAny` — الشرطان الوحيدان اللذان يُصدرهما بانِي المرشِّح.
> ⚠️ **الصناديق القائمة لا تكسبها**: `ensure_hybrid_collection` تخرج فوراً إن كان الصندوق موجوداً، فالفهارس تُنشأ للجديد وحده — والقديم يحتاج مهمّة تشغيليّة لمرّة واحدة (خطّة الوحدات §5‑ب).

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
> **تعديل 6.3‑أ (‏`framework/pagination.py`):** الترميز **مطبوعٌ بالمفتاح** — `encode_id_cursor`/`decode_id_cursor` للحالة العامّة، و`encode_seq_cursor`/`decode_seq_cursor` لاستثناء `list_messages` وحده. الفكّ **كلّيّ**: مِظروف base64url صارم (لا إسقاط لأحرفٍ خارج الأبجديّة) + تحقّقٌ من شكل المفتاح ⇒ كلّ مُشوَّهٍ يخرج بـ`common.invalid_cursor` قبل أن يبلغ أيّ `WHERE`. ولأنّ الزوجين مطبوعان، **النوع هو الوسم**: مؤشّرُ مجموعةٍ أُنفق على أخرى مرفوضٌ بلا مُميِّزٍ في الحمولة.
> **تعديل 6.3‑ب:** الاتّجاه **الأحدث أوّلاً** (‏`ORDER BY id DESC` مع شرط `id < cursor`) في كلّ مستودعٍ مرقّم؛ الاستثناء الوحيد `list_messages` (‏`seq` إلى الأمام). الشرط والترتيب يجب أن يشيرا الاتّجاه نفسه. و`DocumentRepository.list` انضمّ إلى التوقيع العامّ (`limit`/`cursor` ⇒ `Page[Document]`).

**أمثلة منافذ الوحدات (Inbound):**
```python
# ── spaces — محور الملكيّة داخل المستأجر (01 §2.11) ──
# spaces/ports/repository.py
class SpaceRepository(Protocol):
    async def get(self, ctx, space_id: Uuid) -> Space | None: ...     # يشمل المحذوفة ناعماً (سابقة files)
    async def lock(self, ctx, space_id: Uuid) -> bool: ...            # قفل صفٍّ على وحدة حيّة — مرساة الحصّة
    async def add(self, ctx, space: Space) -> None: ...
    async def save(self, ctx, space: Space) -> None: ...              # قفل تفاؤلي على version
    async def list(self, ctx, *, limit: int, cursor: str | None) -> Page[Space]: ...   # الحيّة وحدها
# لا `find_by_name`: التفرّد فهرسٌ جزئيّ يُنفَّذ في جملة الكتابة نفسها (01 §2.11)، وزوج
# «اقرأ ثمّ أدرج» يجيب السؤال نفسه دورةً أبكر ويخطئ بالضبط حين يتسابق طلبان.
# ولا `count`: لا سقف على عدد الوحدات — الحصّة **بايتات** على `files` لا صفوفٌ هنا.
# و`lock` يعيد `bool` لا كياناً: مُستدعيه خدمةُ تنسيقٍ عند جذر التركيب، ولا شأن لها
# بقراءة `Space`. وهو قفلٌ **داخل وحدة عمل فقط** — قفل الصفّ يموت بنهاية معاملته،
# ونداؤه خارج `UnitOfWork.begin(ctx)` يأخذ القفل ويطلقه بعد جملةٍ واحدة بصمت.

# spaces/ports/inbound.py — الوجه القرائيّ الذي يربطه غيرُك (سابقة FilesQuery)
@dataclass(frozen=True, slots=True)
class SpaceView: space_id: str; name: str       # إسقاط أنحف من التجميعة: بلا version/deleted_at
class SpacesQuery(Protocol):
    async def get_active(self, ctx, space_id: Uuid) -> SpaceView | None: ...
# `None` تغطّي «مجهولة» و«محذوفة» معاً بلا تمييز: كلتاهما «لا يُودَع هنا شيء»،
# وإخبارُ مستدعٍ بأن وحدةً لا يستطيع الكتابة فيها موجودةٌ رغم ذلك إفشاءٌ لا تشخيص.

# files/ports/spaces.py · conversations/ports/spaces.py — **المستهلك يعلن شكله**
class ActiveSpace(Protocol):
    @property
    def space_id(self) -> str: ...
class ActiveSpaces(Protocol):
    async def get_active(self, ctx, space_id: Uuid) -> ActiveSpace | None: ...
# النمط عينه في `conversations/ports/files.py`، والسبب هنا عقد الاستقلال (`.importlinter`):
# `files`/`conversations` **لا تستوردان** `app.modules.spaces`، فتُعلن كلٌّ منهما الشكل
# الضيّق الذي تحتاجه ويربطه جذر التركيب بنسخة `SpacesQueryService` **واحدة** — بنيويّاً،
# وmypy يفحص موضع الربط. الإعلان يتكرّر، والجواب لا (سابقة `AgentKey`).
# `@property` لا حقل: البروتوكول البنيويّ يطابق الحقل بسمةٍ **قابلة للتعديل**، وهو ما لا
# يقدّمه `SpaceView` المجمَّد — فالخاصيّة هي ما يجعل الطرفين يلتقيان تحت mypy.

# files/ports/repository.py
class FileRepository(Protocol):
    async def get(self, ctx, file_id: Uuid) -> File | None: ...
    async def add(self, ctx, file: File) -> None: ...
    async def save(self, ctx, file: File) -> None: ...                          # قفل تفاؤلي على version
    async def list(self, ctx, *, space_id: Uuid | None, limit: int, cursor: str | None) -> Page[File]: ...
    # ── ما كسبته من خطّة الوحدات: أربع دوالّ، ثلاثٌ منها **جماعيّة عمداً** ──
    async def bytes_in_space(self, ctx, space_id: Uuid) -> int: ...              # مجموع الحيّ — بسط الحصّة
    async def totals_by_space(self, ctx, space_ids: Sequence[Uuid]) -> Mapping[Uuid, SpaceFileTotals]: ...
    async def storage_keys_in_space(self, ctx, space_id: Uuid) -> Sequence[str]: ...  # قبل حذف الصفوف
    async def purge_space(self, ctx, space_id: Uuid) -> int: ...                 # حذفٌ صلب، يعيد العدد
    # ── وما كسبته من مراجعة فرع الاسترجاع §2: قراءةٌ جماعيّةٌ خامسة، على مسارٍ أحرّ ──
    async def ready_names(self, ctx, file_ids: Sequence[Uuid]) -> Mapping[Uuid, str]: ...
    # ── وما كسبته من س-29 القاعدة ١ (قرار المستخدم 2026‑08‑25): قراءةٌ سادسة ──
    async def live_namesakes(self, ctx, file: File) -> Sequence[Uuid]: ...       # ما يستبدله هذا الملفّ
    # قاعدةُ `get`+`is_ready` نفسها مدفوعةً إلى SQL (`status='ready'` و`deleted_at IS NULL`،
    # INV-F2/F3): المجهولُ والمحذوفُ والمحجورُ ونصفُ المرفوع **غائبون** من الخريطة لا
    # حاضرين بنصٍّ فارغ — قاعدةُ `totals_by_space` عينها (الاستعلام يعيد ما وُجد)، وهي ما
    # يُبقي «لا ملفَّ مقروءاً» متمايزاً عن «ملفٌّ مقروءٌ بلا اسم». والمعرّفات المكرّرة تنطوي،
    # و`file_ids` فارغةٌ ⇒ خريطةٌ فارغةٌ بلا استعلام. مشيُ الكوربوس في `knowledge` كان يدفع
    # رحلةً لكلّ معرّف قبل أن يُوجَّه سؤالٌ أصلاً؛ `WHERE id IN (…)` واحدةٌ تكلّف واحدة.
    # `totals_by_space` بالجمع لا بالمفرد: `GET /spaces` ينشر `bytes_used`/`file_count`،
    # ونداءٌ لكلّ صفٍّ يحوّل صفحةً من عشرين وحدة إلى أربعين ذهاباً وإياباً لعمودَي عرض.
    # و`storage_keys_in_space` تسبق `purge_space` دائماً — بعد حذف الصفوف لا يبقى ما
    # يدلّ على كائنات MinIO التي يجب حذفها. والمحذوف ناعماً **لا يُتخطّى** هنا خلافاً
    # لكلّ قراءةٍ أخرى: بايتاته عادت إلى الحصّة وكائنُه ما زال في التخزين.
    # `save` (قفل تفاؤلي على `version`) يكتب `name` أيضاً منذ BE‑RAG‑006 — الحقل
    # الوحيد القابل للتعديل بعد التسجيل؛ أمّا content_type/size_bytes/storage_key
    # فلا مُبدِّل لها في المجال، فبقاؤها خارج جملة UPDATE هو ما يحرسها. وهو أيضاً
    # مَن يكتب انتقال الحالة إلى `ready` (‏`File.complete` في المجال) — لا مُبدِّلَ حالةٍ
    # مستقلّ على المنفذ، فالانتقال قرارُ التجميعة والمستودعُ يحفظه كما يحفظ غيره.
    # و`space_id` على `list` **مفتاحيّةٌ إلزاميّةٌ بلا افتراضيّ** (خطّة الوحدات، الخطوة ١٢):
    # «كلّ الوحدات» قرارٌ يُكتب في موضع النداء، لا حالةٌ يقع فيها مَن نسي. والقاعدة عينها
    # على `ConversationRepository.list_by_agent` و`DocumentRepository.list`.
    #
    # ── `live_namesakes`: معرّفات الملفّات الحيّة التي **يستبدلها** هذا الملفّ ──
    # التجميعةُ كلّها وسيطٌ لا أربع قيمٍ مفكّكة (سببُ `add`/`save`): السؤال عن ملفّ،
    # وكلّ جزءٍ من الشرط — وحدتُه واسمُه ومتى وصل وأيُّ صفٍّ هو — يُقرأ من الصفّ نفسه،
    # فلا يستطيع مستدعٍ أن يقرن اسمَ ملفٍّ بوحدةِ آخر.
    # • «الاسم نفسه» = `lower(normalize(name, NFC))`، ونصفاه معاً لازمان: `lower`
    #   قاعدةُ `ux_spaces_ws_name` («‏Report» و«‏report» اسمٌ واحدٌ لإنسان)، و`normalize`
    #   هو ما يجعلها صحيحةً **للعربيّة** — الاسمُ نفسه على لوحتَي مفاتيح يختلف بعلامات
    #   التركيب وحدها، و`lower` لا تفعل له شيئاً. والتعبير مخزَّنٌ في عمودٍ **مولَّد**
    #   (`files.name_key`) لا في تعبير فهرس، وذلك قياسٌ لا تفضيل: الدالّتان ليستا
    #   `LEAKPROOF`، فتحت `FORCE ROW LEVEL SECURITY` لا يصير تعبيرُ الفهرس مفتاحَ بحثٍ
    #   أبداً (01 §2.6). والمُهايئ يقارن العمود بالوسيط **مُطبَّعاً في SQL كذلك**، فلا
    #   اتّفاقَ بين بايثون وSQL يمكن أن ينحرف بلا أن يُبلِّغ أحد.
    # • **الوحدة نفسها**، والنطاقُ من النموذج لا من تفضيل (س-32): الفضاءات معزولةٌ
    #   عزلاً تامّاً، فـ`report.pdf` في وحدةٍ لا يستبدل `report.pdf` في أخرى. والمقارنة
    #   آمنةٌ تجاه `NULL`: «بلا وحدة» دلوٌ واحدٌ لا حرفُ بدل.
    # • **الأقدمُ فقط** — أسبقُ تماماً في `(created_at, id)`. وهذا اللاتماثل هو حجّةُ
    #   التزامن لا تفصيلاً: مسارُ الإتمام **لا يمسك قفلاً** (قفلُ صفّ الوحدة يُؤخذ عند
    #   التسجيل)، و«أقدمُ من» ترتيبٌ صارم، فمن رفعَين لاسمٍ واحدٍ يُتمّان معاً لا يمكن أن
    #   يحذف كلٌّ الآخر. وهو أيضاً دلالةُ القرار نفسها: الجديدُ يَجُبُّ القديم ولا عكس.
    # • **المحذوفُ ناعماً مستثنى** (قاعدةُ `bytes_in_space`)، و**الحالةُ ليست شرطاً**:
    #   صفٌّ أقدمُ ما يزال `uploaded` يحجز الاسم والحصّة كما يحجزهما `ready`.

# files/ports/inbound.py  (تستدعيه الوكلاء/knowledge بلا استيراد مباشر للوحدة)
@dataclass(frozen=True, slots=True)
class FileView: file_id: str; space_id: str | None; name: str; content_type: str
                size_bytes: int; storage_key: str; status: str
class FilesQuery(Protocol):
    async def get_readable(self, ctx, file_id: Uuid) -> FileView | None: ...
    async def names_for_files(self, ctx, file_ids: Sequence[Uuid]) -> Mapping[Uuid, str]: ...
# `space_id` هنا `str | None` لا `str`: العمود ما زال يقبل `NULL` حتّى الصفّ ٨‑ب،
# ومسربٌ يَعِد بـ`str` ويسلّم `None` يعبر mypy أخضر ثمّ يكذب على كلّ قارئ.
# **والمنفذ لا يُرشِّح بالوحدة**: مستهلكاه يطبّقان سياستين مختلفتين على الجواب نفسه —
# قراءةُ وكيلٍ ترفض بـ`404` **بنصّ الملفّ غير الموجود حرفاً بحرف** (تمييزُ «في وحدةٍ أخرى»
# يُعلن أنّ الملفّ موجود، فيصير المعرّف عرّافَ وجود — وهو أخطر هنا لأنّ `file_id` يصل من
# مخرَج نموذجٍ لا من سردٍ رآه إنسان)، وتثبيتُ محادثةٍ يرفض بـ`409 spaces.cross_space_pin`.
# ترشيحٌ داخل `get_readable` كان يبتلع القاعدة الثانية ويحوّل رفضها الصريح إلى صمت.
# `names_for_files` (مراجعة فرع الاسترجاع §2) هي السؤالُ نفسُه مطروحاً بحجمٍ آخر: مَشْيُ
# كوربوسٍ يمسك **صفحةَ** معرّفات ويريد الحقل الوحيد الذي يعرضه، و`get_readable` لكلّ
# ملفٍّ كانت `D + 50` رحلةً متسلسلةً تُدفَع قبل أن يبدأ الاسترجاع. **الحضورُ يعني ما
# يعنيه `FileView` غيرُ الـ`None`** — جاهزٌ، غيرُ محذوفٍ ولا محجورٍ ولا نصفَ مرفوع
# (INV-F2/F3) — وما عدا ذلك **غائبٌ**، لا حاضرٌ بنصٍّ فارغ: `""` تعني «مقروءٌ بلا اسم»
# وحدها، والتمييز هو ما يُبقي المستهلك يتخطّى ما كان يتخطّاه بالضبط. والاسمُ وحده
# يُسقَط: مَن احتاج وحدةَ ملفٍّ أو حجمه يقرّر عن **ملفٍّ واحد** وله `get_readable`.
# ويُعلن `knowledge` الشكلَ نفسه في `knowledge/ports/files.py` (المستهلك يعلن شكله، أعلاه)،
# ويربطه جذر التركيب بنسخة `FilesQueryService` نفسها — إعلانٌ يتكرّر وجوابٌ لا يتكرّر.

# knowledge/ports/repository.py
class DocumentRepository(Protocol):
    async def get(self, ctx, doc_id: Uuid) -> Document | None: ...
    async def add(self, ctx, doc: Document) -> None: ...
    async def set_status(self, ctx, doc_id: Uuid, status: str, error: str | None = None) -> None: ...
    async def add_chunks(self, ctx, chunks: Sequence[Chunk]) -> None: ...   # idempotent عبر uq(document_id,seq)
    # BE-RAG-007: قراءةُ نقاط المستند ثمّ إتلافه. الأولى تسبق الثانية دائماً — بعد
    # حذف الصفوف لا يبقى ما يدلّ على النقاط التي يجب حذفها من Qdrant.
    async def vector_refs(self, ctx, doc_id: Uuid) -> Sequence[VectorRef]: ...
    async def purge(self, ctx, doc_id: Uuid) -> None: ...                   # chunks ثمّ الصفّ
    # نظيرا الاثنين أعلاه على نطاق الوحدة (الحذف المتسلسل، 01 §2.11):
    async def vector_refs_in_space(self, ctx, space_id: Uuid) -> Sequence[VectorRef]: ...
    async def purge_space(self, ctx, space_id: Uuid) -> int: ...
    # الترتيب داخل `purge_space` **شرطُ نجاح لا ذوق**: `fk_reindex_item_doc` بلا `ON DELETE`،
    # فحذف المستندات قبل بنود مهامّ إعادة الفهرسة يردّ `23503` على السلسلة كلّها. وصفّ
    # `reindex_jobs` نفسه يُحذف **فارغاً فقط**: المهمّة طلبٌ لا محتوى، وطلبٌ واحد قد يسمّي
    # مستنداتٍ من وحدتين، فحذفه مع أولاهما يمحو عرضَ تقدّم الأخرى.

# knowledge/ports/repository.py — BE-RAG-007/008
# `get` يقرأ حالةَ كلّ وثيقةٍ من جدول المستندات نفسه (لا نسخةَ حالةٍ على صفّ المهمّة):
# التقدّم مُشتقٌّ بحكم البناء، فلا عدّاد يهجر الحقيقة (INV-K5).
class ReindexJobRepository(Protocol):
    async def add(self, ctx, job: ReindexJob) -> None: ...                  # الصفّ وبنودُه معاً
    async def get(self, ctx, job_id: Uuid) -> ReindexJob | None: ...        # بضمّ البنود إلى المستندات
    async def mark_cancelled(self, ctx, job_id: Uuid, at: datetime) -> None: ...

# knowledge/ports/retrieval.py — شكلُ القطعة المسترجَعة، 1:1 مع `RetrievedChunkOut` (03 §2)
@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    document_id: str; chunk_id: str; text: str; score: float
    file_name: str | None = None; page_number: int | None = None; section: str | None = None
# الثلاثةُ الأخيرة حقولُ استشهاد (خطّة الاسترجاع §3.1/§3.9، س-19، `P-18`) تكتبها الفهرسةُ
# أصلاً على حمولة كلّ نقطة في Qdrant. حقولٌ صريحةٌ لا `metadata: Mapping`: المشروع على
# `mypy --strict` والمنفذ لا يخمّن مفاتيح، والثمنُ المُعلَن أنّ كلّ مفتاح استشهادٍ جديد
# يكلّف تعديلَ عقد. وكلُّها `| None`: نقطةٌ فُهرِست قبل وجود الحقل، أو محلِّلٌ لم يُصدره
# (‏DOCX بلا `page_number`)، تنحدر إلى «مجهول» لا إلى انهيار.

# knowledge/ports/inbound.py
# `file_ids` نطاقُ استرجاع لا مُرشِّحُ ملفات: الوحدة تحوّله داخلياً إلى `document_id`
# لأن الحمولة في Qdrant تحمل `document_id` لا `file_id` (BE‑RAG‑005). فارغ/None ⇒ النطاق الشامل.
# يعبر الحدّ كـ`file_id` لأن ما يعرفه المُثبِّت (conversations، والواجهة) هو الملف؛ ترجمةُ
# «ملف ⇒ مستند» ملكُ knowledge وحدها، فلا يضطر أحدٌ خارجها لمعرفة أنّ للمستند وجوداً.
# `space_id` **بلا قيمة افتراضيّة** (وسيطة مفتاحيّة إلزاميّة): كلّ مُستدعٍ يقول وحدتَه
# أو يقول `None` صراحةً، فلا يمرّ مسارٌ نسي نطاقَه متخفّياً في هيئة مسارٍ اختار الشمول.
# ⚠️ ويقولها `None` اليوم كلُّ وكيلٍ ووكلاءُ البحث المعرفيّ: `AgentDependencies` لا تحمل
# وحدةً بعد ⇒ **خيطٌ داخل وحدةٍ يسترجع من كلّ الوحدات** (القرار ١ غير مُنفَّذٍ على مسار
# القراءة، مسجَّلٌ في §7 من خطّة الوحدات). الآليّة هنا جاهزة، والناقص وسيطةٌ لا منفذ.
@dataclass(frozen=True, slots=True)
class DocumentNames: names: tuple[str, ...]; total: int   # الأسماء مسقوفةٌ عند المُنتِج · total عدد المستودع كلّه
@dataclass(frozen=True, slots=True)
class RoutedAnswer:
    intent: Intent                            # 'content'|'summarize_doc' (‏`knowledge/domain/intent.py`)
    chunks: tuple[RetrievedChunk, ...]        # مسار CONTENT — عين ما تعيده `retrieve`
    summary_job_id: Uuid | None               # مسار SUMMARIZE_DOC — إيصالُ مهمّةٍ لا ملخّص
    clarification_options: tuple[str, ...]    # أسماءُ ملفّاتٍ يُسأل عنها المستخدم · `()` ⇒ لا سؤال

class KnowledgeRetrieval(Protocol):
    async def retrieve(self, ctx, query: str, k: int | None = None,
                       file_ids: Sequence[Uuid] | None = None,
                       *, space_id: Uuid) -> list[RetrievedChunk]: ...
    async def answer(self, ctx, question: str, k: int | None = None,
                     file_ids: Sequence[Uuid] | None = None,
                     *, space_id: Uuid,
                     conversation_id: Uuid | None = None) -> RoutedAnswer: ...
    async def list_document_names(self, ctx, *, space_id: Uuid,
                                  limit: int | None = None) -> DocumentNames: ...
# ثلاثةُ مناهجَ على **بذرةٍ واحدة** لا ثلاثةُ منافذَ محقونة: `rag_agent` يستدعي شيئاً واحداً
# (ح-11)، والتوجيهُ شأنُ الوحدة لا شأنُ الوكيل (خطّة الاسترجاع §3.4، `P-21`، س-16 = أ).
# `k` **اختياريّةٌ** منذ الصفّ ١٨ (‏`P-40`، س-24 = أ): حذفُها يطلب ما تُعِدّه النشرةُ لا رقماً
# يحمله المستدعي (‏`Settings.retrieval.default_k`، يُحَلّ داخل الوحدة في `RetrieveContext`) —
# وهو الطريقُ الوحيد الذي يبلغ به رقمُ إعدادٍ وكيلاً لا يقرأ إعداداً ولا يستورد شيئاً، وبه
# سقطت `_TOP_K = 5` منه. وتسميتُها ما تزال جائزةً وتعني ما كانت تعنيه: `POST /knowledge/search`
# يسمّي `k` لأن حجم النتيجة جزءٌ من عقده المنشور (03 §2) لا مقبضُ معايرةٍ حصرته س-24 في
# `Settings`. و`limit` على `list_document_names` نفسُ الشكل حرفاً بحرف
# و`conversation_id` (‏`F-7`) هي الوسيطةُ الوحيدةُ هنا التي ليست عن الاسترجاع، ولذلك هي على `answer` وحدها: مسارُ التلخيص يُنتِج نصَّه بعد انتهاء الدورة بدقائق، فيلزم أن تُخبَر الوحدةُ أين يُسلَّم — والوكيلُ وحده يعرف المحادثة التي يجيب فيها (‏`AgentRequest.conversation_id`). ومسارُ المحتوى يتجاهلها: جوابُه يُكتَب داخل الدورة نفسها. افتراضُها `None` قيمةٌ حقيقيّة لا منسيّة — `POST /knowledge/search` بلا محادثةٍ أصلاً
# (‏`Settings.retrieval.max_corpus_names`)، وبها سقطت `_MAX_CORPUS_NAMES = 50` من الوكيل.
# `answer` تُصنّف ثمّ تُرسل: `SUMMARIZE_DOC` إلى `RequestSummary` فيعود `summary_job_id`،
# و`CONTENT` إلى `RetrieveContext` فتعود `chunks`. يُملأ أحدُهما لا كلاهما، وهما حقلان لا
# اتّحادٌ لأن المستدعي يعرضهما مختلفَين. و`intent` تُعلَن **بصدق** حتّى حين لم يجرِ مسارُها:
# سؤالُ تلخيصٍ لم يُعرَف مستندُه يعود `SUMMARIZE_DOC` بـ`summary_job_id = None` وبقطعِ CONTENT
# في اليد، وهو ما يجعل السؤال التوضيحيّ ممكناً أصلاً (س-18 = أ) — وجوابٌ يقول `CONTENT` كان
# سيمحو دليلَه. و`clarification_options` **أسماءُ ملفّاتٍ** لا كائناتِ مرشّحين ولا جملةً
# مُصاغة: س-18 = أ جعلت التوضيح **نصَّ إجابةٍ عاديّاً** على البثّ القائم، فالصياغةُ ولغتُها
# للمستدعي، والوحدةُ تدين له بالحقائق وحدها (حدثُ `clarification` مُهيكَلاً مسجَّلٌ في §7 من
# خطّة الاسترجاع خارج النطاق: يمسّ عقد البثّ، وهذا الحقلُ لا يمسّه).
# و`answer` وحدها تُقيّد النطاق بملفٍّ يسمّيه السؤال، و**صارمةً** (الصفّ ١٥، `P-25`): ملفٌّ
# مسمّى لا يحمل شيئاً يعود بـ`chunks` فارغة، ولا يُعاد البحثُ على بقيّة الكوربوس — إجابةٌ
# من ملفٍّ لم يسأل عنه المستخدم أسوأ من «ليس في ذلك الملفّ». و`retrieve` تبقى بلا تصنيفٍ
# ولا تقييد: هي البحث الحرفيّ الذي يعنيه `POST /knowledge/search`، وتوجيهُ بحثٍ REST عبر
# مُصنِّفٍ كان سيُدرِج مهمّةَ تلخيصٍ لم يطلبها أحد.
# ⚠️ **`space_id` إلزاميّةٌ وغيرُ قابلةٍ للعدم على المناهج الثلاثة** (‏س-32، قرار المالك
# 2026‑08‑26): الفضاءات معزولةٌ عزلاً تامّاً — ملفّاتُها وفهرسُها وصفوفُها — فالبحثُ يقع في
# فضاءٍ واحدٍ أو لا يقع، ولا قيمةَ على هذا المحور تعني «كلّ الفضاءات». كانت `Uuid | None`
# ومرّرها مستدعيان بـ`None` عمداً (`POST /knowledge/search` والوكيل)، وذانك بالضبط هما
# الثقب. والحارسُ الذي ينفّذها `retrieval.require_space_scope` — يرفض قبل أن يُحسَب تضمينٌ
# واحد. و`list_document_names` أخذتها معها: ترويسةٌ تسمّي ملفّات فضاءٍ لا يستطيع السائل
# فتحَه هي التسريبُ نفسُه بلسانٍ لطيف — وهذا تعديلٌ صريحٌ لـ«س-23 = ج» التي جعلت الترويسة
# على مستوى مساحة العمل.

# conversations/ports/repository.py
class ConversationRepository(Protocol):
    async def get(self, ctx, conv_id: Uuid) -> Conversation | None: ...
    async def add(self, ctx, conv: Conversation) -> None: ...
    async def list_by_agent(self, ctx, agent_key: str, *, space_id: Uuid | None,
                            limit: int, cursor: str | None) -> Page[Conversation]: ...
    async def append_message(self, ctx, msg: Message) -> None: ...          # يزيد seq بقفل تفاؤلي
    async def list_files(self, ctx, conv_id: Uuid) -> list[PinnedFile]: ...          # نطاق الاسترجاع المثبَّت
    async def pin_file(self, ctx, conv_id: Uuid, file_id: Uuid, now: datetime) -> PinnedFile: ...  # مثاليّ: يعيد الأصلي
    async def unpin_file(self, ctx, conv_id: Uuid, file_id: Uuid) -> None: ...       # مثاليّ: غيابه ليس خطأ
    async def counts_by_space(self, ctx, space_ids: Sequence[Uuid]) -> Mapping[Uuid, int]: ...  # عمود `GET /spaces`
    async def purge_space(self, ctx, space_id: Uuid) -> int: ...   # messages + conversation_files + conversations

# conversations/ports/files.py — الملفّ كما تراه المحادثة (لا استيراد لـ`app.modules.files`)
class ReadableFile(Protocol):
    @property
    def file_id(self) -> str: ...
    @property
    def space_id(self) -> str | None: ...
class ReadableFiles(Protocol):
    async def get_readable(self, ctx, file_id: Uuid) -> ReadableFile | None: ...
# قاعدة التكامل التي فرضها بقاء التثبيت: **الملفّ المثبَّت في وحدة المحادثة نفسها**،
# والمقارنة `!=` بسيطةٌ عمداً — `None` في الطرفين يمرّ (الحالة القائمة قبل الصفّ ٨‑ب)،
# وبعده تختفي الحالة وحدها بلا تعديل سطر. والرفض `409 spaces.cross_space_pin` لا `422`:
# الطلب سليم والملفّ سليم، والحالة هي المانع. ⚠️ ولو أُهمل الفحص لظلّ الفشل آمناً —
# مرشِّح الاسترجاع يجمع `space` و`document_id` بـAND فيسقط الغريب — لكنّ السقوط الصامت
# ليس رفضاً، والمستخدم يستحقّ خطأً لا نتيجةً فارغة.

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

#### 3.5.1 `ModelCatalog` — العرض القرائيّ لجدول التوجيه (`framework/providers/catalog.py`)
> **منفذ ضيّق** يُشتَقّ من **نفس** الجدول المُفكَّك في `SettingsProviderResolver`، لا من قراءةٍ ثانيةٍ لـ`Settings`: قراءتان للجدول تنحرفان، فتُعلن الواجهةُ موديلاً لا يستطيع `resolve_llm` توجيهه. ويُفصَل عن `ProviderResolver` لأنّ `ResolvedProvider` يحمل `api_key` **مفكوك التعمية** (`INV‑C2`) — فطبقةُ الـAPI تُمسِك هذا المنفذ الضيّق وحده، ولا تستطيع بناءً على البنية الوصول إلى `resolve_llm` أصلاً.
```python
@dataclass(frozen=True, slots=True)
class ModelChoice:
    capability: str                       # مفتاح التوجيه نفسه (قدرة أو مفتاح وكيل)
    provider: str
    model: str
    available: bool                       # مزوّدٌ بلا مفتاح، أو مفتاحٌ يُحَلّ فعلاً لهذا المستخدم

class ModelCatalog(Protocol):             # قراءة فقط — لا يُعيد مفتاحاً أبداً
    async def list_llm_models(self, ctx: ExecutionContext) -> list[ModelChoice]: ...
```
> `available` يُحسَب عبر **نفس** `CredentialResolver` الذي سيستعمله التنفيذ (user ثمّ platform، بلا fallback)، ويُحَلّ **مرّةً لكلّ مزوّد** لا مرّةً لكلّ مسار. غياب المفتاح ⇒ `available: false` **لا خطأ**: كتالوجٌ ينهار لأنّ أحد مزوّديه غير مُعتمَد يُخفي المزوّدات المُعتمَدة كلّها.
