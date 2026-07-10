# مواصفة الـAPI

> `/api/v1` (`DD‑06`) · JSON `snake_case` · ترقيم بالمؤشّر · أخطاء **RFC 9457** (`DD‑05`).
> العقد الآلي الكامل: [`openapi.yaml`](openapi.yaml). هذا المستند يشرح الاصطلاحات وDTOs والبثّ ونموذج الأخطاء.

## 0) المبادئ العابرة
- **المصادقة:** `Authorization: Bearer <Firebase ID Token>` على كل المسارات عدا `/health`. يتحقّق منه Middleware محلياً (D‑25) ويبذر المستخدم JIT.
- **المستأجر:** يُشتقّ `workspace_id` من هوية المستخدم (لا يُمرَّر من العميل) ويُحقن في `ExecutionContext` ⇒ RLS.
- **التفويض:** حرّاس RBAC على مستوى المسار (`required_permission`).
- **الترابط:** رأس `X-Correlation-Id` (يُولَّد إن غاب) يُعاد في الرد وفي كل حدث/خطأ.
- **المثالية (Idempotency):** طلبات الإنشاء الثقيلة (`media`, `files`) تقبل رأس `Idempotency-Key` ⇒ إعادة المحاولة آمنة.
- **تغليف المجموعات (`API‑04`):** **كل** نقطة نهاية تُعيد مجموعة تُغلَّف بـ`{ data:[…], meta:{ next_cursor, limit } }` — **بلا استثناء**، بما فيها القوائم غير المرقّمة/المحدودة (تُعيد `next_cursor: null`). المورد المفرد يُعاد **مجرّداً** (بلا غلاف).
- **الترقيم:** `?limit=<=100&cursor=<opaque>` على المجموعات المرقّمة؛ ويُعاد المؤشّر التالي في `meta.next_cursor` (=`null` عند آخر صفحة أو عند مجموعة محدودة غير مرقّمة).

## 1) مخطط الموارد
| المورد | المسار الأساس | العمليات |
|--------|---------------|----------|
| Workspace | `/api/v1/workspace` | GET · PATCH |
| Agents | `/api/v1/agents` | GET(list) · GET(one) · POST `{key}/invoke` |
| Conversations | `/api/v1/conversations` | GET(list by agent) · POST · GET · DELETE · GET/POST `…/messages` |
| Files | `/api/v1/files` | POST(register) · POST `{id}/complete` · GET(list) · GET · DELETE |
| Knowledge | `/api/v1/knowledge` | POST `/search` · GET `/documents` · GET `/documents/{id}` |
| Media | `/api/v1/media` | POST `/jobs` · GET `/jobs/{id}` |
| Workflows | `/api/v1/workflows` | GET(list) · POST `{key}/run` · GET `/runs/{id}` |
| Credentials | `/api/v1/credentials` | GET · POST · DELETE |
| Integrations | `/api/v1/integrations` | GET `/connectors` · GET/POST `/connections` · POST `/connections/{id}/authorize` · GET `/connections/oauth/callback` · DELETE `/connections/{id}` · GET/POST/DELETE `/mcp-servers` · GET `/tools` |
| Usage | `/api/v1/usage` | GET (summary) · GET `/limits` · PUT `/limits` |
| Streaming | `/api/v1/ws` (WS) · `Accept: text/event-stream` (SSE) | interactive · single-response |
| Health | `/health` · `/health/ready` | GET (بلا مصادقة) |

> **حدود `usage`:** الفرض (قبل العملية) والالتقاط (بعد العملية) **منفذان واردان داخليّان** يستدعيهما المُنسِّق (طبقة الوكلاء) — **ليسا API عاماً** (`FR‑131/132`)؛ مسارات `/usage` أعلاه للعرض/الإعداد فقط.
> **MCP:** خوادم MCP **بعيدة (HTTP/SSE) حصراً** في v1؛ لا تسجيل نقل stdio محلي (§6.13).

## 2) نماذج DTO الأساسية (Pydantic v2)
```python
# استجابة موحّدة للمجموعات — تُطبَّق على **كل** نقطة نهاية تُعيد مجموعة (API‑04)،
# حتى المحدودة/غير المرقّمة (مثل listAgents/listWorkflows/listCredentials/listConnectors/
# listMcpServers/listDiscoveredTools/searchKnowledge/getUsageLimits/putUsageLimits):
# تُعاد ضمن Page[T] بـ meta.next_cursor=None. المورد المفرد فقط يُعاد مجرّداً.
class Page[T](BaseModel): data: list[T]; meta: PageMeta
class PageMeta(BaseModel): next_cursor: str | None; limit: int   # next_cursor=None ⇒ آخر صفحة / مجموعة محدودة

# Workspace
class WorkspaceOut(BaseModel): id: str; name: str; status: str; created_at: datetime
class WorkspacePatchIn(BaseModel): name: str = Field(min_length=1, max_length=80)

# Agents
class AgentOut(BaseModel):
    key: str; name: str; version: str; description: str
    capabilities: list[str]; required_permissions: list[str]
class AgentInvokeIn(BaseModel):
    conversation_id: str | None = None
    input: dict[str, Any]
    stream: bool = False
class AgentInvokeOut(BaseModel):        # عند stream=false
    conversation_id: str; message: MessageOut; usage: Usage
class Usage(BaseModel): prompt_tokens: int; completion_tokens: int

# Conversations
class ConversationOut(BaseModel):
    id: str; agent_key: str; kind: str; title: str | None; created_at: datetime
class ConversationCreateIn(BaseModel): agent_key: str; title: str | None = None
class MessageOut(BaseModel):
    id: str; role: str; content: dict[str, Any]; token_count: int | None; seq: int; created_at: datetime
class MessageCreateIn(BaseModel):
    content: dict[str, Any]; stream: bool = False        # يشغّل الوكيل ويعيد ردّه

# Files
class FileRegisterIn(BaseModel):
    name: str; content_type: str; size_bytes: int
class FileRegisterOut(BaseModel):
    file_id: str; upload_url: str; expires_in: int       # presigned PUT (MinIO)
class FileCompleteIn(BaseModel): checksum: str | None = None
class FileOut(BaseModel):
    id: str; name: str; content_type: str; size_bytes: int
    status: str; download_url: str | None; created_at: datetime   # presigned GET عند ready

# Knowledge
class KnowledgeSearchIn(BaseModel): query: str; k: int = Field(default=5, le=50)
class RetrievedChunkOut(BaseModel): document_id: str; chunk_id: str; text: str; score: float
class DocumentOut(BaseModel):
    id: str; file_id: str; status: str; chunk_count: int; created_at: datetime

# Media
class MediaJobCreateIn(BaseModel):
    kind: Literal['image','video']; prompt: str; agent_key: str; params: dict[str, Any] = {}
class MediaJobOut(BaseModel):
    id: str; kind: str; status: str; result_file_id: str | None; error: str | None; created_at: datetime

# Workflows
class WorkflowOut(BaseModel): key: str; name: str; steps: list[str]
class WorkflowRunIn(BaseModel): input: dict[str, Any]; stream: bool = False
class WorkflowRunOut(BaseModel): run_id: str; conversation_id: str; status: str

# Credentials
class CredentialCreateIn(BaseModel):
    provider: str; scope: Literal['platform','user']; label: str | None = None; secret: str
class CredentialOut(BaseModel):        # لا يُعاد السرّ أبداً
    id: str; provider: str; scope: str; label: str | None; status: str; created_at: datetime

# Integrations (FR‑120…124) — لا يُعاد أي سرّ/رمز OAuth أبداً
class ConnectorOut(BaseModel):         # عنصر كتالوج الموصّلات المتاحة
    key: str; name: str; scopes: list[str]; auth_type: Literal['oauth2']
class ConnectionOut(BaseModel):
    id: str; connector_key: str; display_name: str | None
    status: Literal['pending','connected','revoked','error']
    scopes: list[str]; expires_at: datetime | None; created_at: datetime
class ConnectionCreateIn(BaseModel):   # يبدأ ربطاً؛ الرد يحمل رابط التفويض
    connector_key: str; scopes: list[str] = []
class AuthorizeOut(BaseModel):
    authorize_url: str; state: str
class McpServerCreateIn(BaseModel):
    name: str; endpoint_url: str; transport: Literal['http','sse'] = 'http'   # بعيد حصراً
class McpServerOut(BaseModel):
    id: str; name: str; endpoint_url: str; transport: str; status: str; created_at: datetime
class DiscoveredToolOut(BaseModel):    # كتالوج ديناميكي لكل Workspace (FR‑52)
    name: str; description: str; source: str        # 'connector:<id>'|'mcp:<name>'

# Usage (FR‑130…134) — عرض/إعداد فقط (الفرض/الالتقاط منفذان داخليّان)
class UsageSummaryOut(BaseModel):
    workspace_id: str; period: Literal['day','month']
    tokens: int; cost_micros: int
    by_agent: list[dict[str, Any]]; by_provider: list[dict[str, Any]]
class UsageLimitOut(BaseModel):
    id: str; scope: Literal['workspace','agent','provider']; scope_key: str
    metric: Literal['tokens','cost_micros']; period: Literal['day','month']; limit_value: int
class UsageLimitsPutIn(BaseModel):
    limits: list[UsageLimitOut]        # استبدال طقم الحدود القابلة للإعداد (owner/admin)
```

## 3) بروتوكول البثّ (D‑10)

### 3.1 SSE — للردّ الواحد (`Accept: text/event-stream`)
يُستخدم على `POST /agents/{key}/invoke`, `POST /conversations/{id}/messages`, `POST /workflows/{key}/run`.
```
event: token
data: {"delta":"مرحب"}

event: tool_call
data: {"tool":"rag_search","args":{"query":"..."}}

event: final
data: {"message_id":"018f...","content":{...},"usage":{"prompt_tokens":812,"completion_tokens":140}}

event: error
data: {"type":"https://errors.platform/agent.failed","title":"Agent failed","status":502,"code":"agent.failed","correlation_id":"018f..."}
```
- ترميز UTF‑8، `Cache-Control: no-cache`، نبضة `:keep-alive` كل 15s.
- الإنهاء بحدث `final` أو `error` ثم إغلاق التدفّق.

### 3.2 WebSocket — التفاعلي `GET /api/v1/ws`
- المصادقة: `?token=<Firebase ID Token>` عند الـHandshake (يتحقّق قبل القبول)، أو أول رسالة `auth`.
- **رسائل العميل → الخادم:**
```json
{"type":"invoke","agent_key":"rag_agent","conversation_id":"018f...","input":{"text":"..."}}
{"type":"cancel","conversation_id":"018f..."}
{"type":"ping"}
```
- **رسائل الخادم → العميل:**
```json
{"type":"token","conversation_id":"018f...","delta":"..."}
{"type":"tool_call","conversation_id":"018f...","tool":"...","args":{...}}
{"type":"notification","event":"knowledge.document.indexed.v1","data":{"document_id":"018f..."}}
{"type":"final","conversation_id":"018f...","message_id":"018f...","usage":{...}}
{"type":"error","conversation_id":"018f...","problem":{...RFC9457...}}
{"type":"pong"}
```
- **الإشعارات غير المتزامنة** (نتائج العمّال: فهرسة/توليد) تُدفع كـ`notification` على اتصال الـWS الحيّ للمستأجر — الجسر: العامل ينشر حدثاً عالمياً ⇒ مُشترِك WS يوجّهه لجلسات الـ`workspace_id`.
- حدود: رسالة ≤ 64KB، اتصالات متزامنة/مستخدم محدودة (انظر `07`).

## 4) نموذج الأخطاء — RFC 9457 (`DD‑05`)
`Content-Type: application/problem+json`. الهيكل:
```json
{
  "type": "https://errors.platform/files.too_large",
  "title": "File exceeds maximum size",
  "status": 413,
  "detail": "size_bytes=73400320 exceeds limit 52428800",
  "instance": "/api/v1/files",
  "code": "files.too_large",
  "correlation_id": "018f2a...",
  "errors": [ {"field":"size_bytes","message":"must be <= 52428800"} ]
}
```

### كتالوج الأكواد (مستقرّة، آلية)
| code | HTTP | متى |
|------|------|-----|
| `auth.missing_token` | 401 | لا Bearer |
| `auth.invalid_token` | 401 | توقيع/انتهاء JWT |
| `authz.forbidden` | 403 | صلاحية RBAC غير كافية |
| `common.validation_error` | 422 | فشل تحقّق DTO (يملأ `errors[]`) |
| `common.not_found` | 404 | مورد غير موجود ضمن المستأجر |
| `common.conflict` | 409 | تعارض قفل تفاؤلي (`version`) |
| `common.rate_limited` | 429 | تجاوز حدّ المعدّل (`Retry-After`) |
| `files.too_large` | 413 | تجاوز حجم الرفع |
| `files.unsupported_type` | 415 | نوع MIME خارج القائمة البيضاء |
| `files.not_ready` | 409 | استخدام ملف قبل `ready` |
| `knowledge.not_indexed` | 409 | استرجاع قبل الفهرسة |
| `media.invalid_params` | 422 | معاملات توليد خارج الحدود |
| `credentials.provider_unknown` | 422 | مزوّد غير مدعوم |
| `credentials.none_available` | 409 | لا مفتاح مستخدم/منصّة للمزوّد (لا Fallback، D‑16) |
| `integrations.connector_unknown` | 422 | موصّل غير موجود في الكتالوج |
| `integrations.oauth_failed` | 502 | فشل تبادل/تجديد رمز OAuth مع الطرف الثالث |
| `integrations.not_connected` | 409 | استخدام موصّل غير مُتّصل/مُبطَل |
| `integrations.mcp_transport_unsupported` | 422 | نقل MCP غير مدعوم (مسموح `http`/`sse` فقط — v1) |
| `integrations.mcp_unreachable` | 502 | تعذّر الوصول لخادم MCP البعيد |
| `usage.quota_exceeded` | 429 | تجاوز **حصّة** الرموز/الطلبات (يعيده المُنسِّق عند رفض الفرض؛ `LimitDecision.reason='quota_exceeded'`) |
| `usage.budget_exceeded` | 429 | تجاوز **الميزانية** (التكلفة `cost_micros`) (يعيده المُنسِّق عند رفض الفرض؛ `LimitDecision.reason='budget_exceeded'`) |
| `agent.unknown` | 404 | `agent_key` غير مُسجّل |
| `agent.failed` | 502 | فشل تنفيذ الوكيل/المزوّد |
| `workflow.unknown` | 404 | `workflow_key` غير مُعرّف |
| `common.internal` | 500 | خطأ غير متوقّع (يُخفي التفاصيل) |

- كل الأخطاء تحمل `correlation_id` ⇒ نقطة توسعة تتبّع.
- `500` لا يسرّب داخلياً؛ التفاصيل في السجلّ فقط.
