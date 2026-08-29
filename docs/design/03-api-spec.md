# مواصفة الـAPI

> `/api/v1` (`DD‑06`) · JSON `snake_case` · ترقيم بالمؤشّر · أخطاء **RFC 9457** (`DD‑05`).
> العقد الآلي الكامل: [`openapi.yaml`](openapi.yaml). هذا المستند يشرح الاصطلاحات وDTOs والبثّ ونموذج الأخطاء.

## 0) المبادئ العابرة
- **المصادقة:** `Authorization: Bearer <Firebase ID Token>` على كل المسارات عدا `/health`. يتحقّق منه Middleware محلياً (D‑25) ويبذر المستخدم JIT.
- **المستأجر:** يُشتقّ `workspace_id` من هوية المستخدم (لا يُمرَّر من العميل) ويُحقن في `ExecutionContext` ⇒ RLS.
- **التفويض:** حرّاس RBAC على مستوى المسار (`required_permission`).
- **الترابط:** رأس `X-Correlation-Id` (يُولَّد إن غاب) يُعاد في الرد وفي كل حدث/خطأ.
- **المثالية (Idempotency):** طلبات الإنشاء الثقيلة تقبل رأس `Idempotency-Key` (اختياريّ) ⇒ إعادة المحاولة آمنة. المواضع الثلاثة التي يعلنها `openapi.yaml` بالضبط: `registerFile` · `createMediaJob` · `runWorkflow`. **محقَّق فعليّاً منذ 3.79** بمخزنٍ على PostgreSQL (`platform.idempotency_keys`، بسياسة RLS بصيغة `NULLIF`) لا على Redis — لأنّ إخلاء Redis يعيد فتح نافذة التكرار صامتاً على مسارٍ يُحاسَب. الدلالة: المفتاح مُنطاقٌ بـ(المساحة + نقطة النهاية)؛ تكرارٌ بنفس المفتاح ونفس الجسد ⇒ **الردّ الأوّل نفسه** بلا إنشاء مورد ثانٍ؛ تكرارٌ بنفس المفتاح وجسدٍ مختلف ⇒ `common.conflict`/409 (رمزٌ من الكتالوج القائم، لا رمزٌ مُخترع)؛ تكرارٌ متزامنٌ قبل اكتمال الأوّل ⇒ 409 كذلك؛ وغياب الرأس ⇒ **السلوك السابق حرفاً بحرف**. وعمليّةٌ فشلت تُحرِّر مفتاحها كي لا يتحوّل عطبٌ عابر إلى مفتاحٍ معطوبٍ للأبد. **حدٌّ مُعلَن:** جواب `runWorkflow` **المبثوث (SSE)** خارج المخزن — مجرى الأحداث يُستهلَك ولا يُخزَّن، فلا جسدَ رَدٍّ يُعاد؛ عميلٌ يريد تشغيلاً مثاليّاً يطلب الجواب المجمَّع.
- **تغليف المجموعات (`API‑04`):** **كل** نقطة نهاية تُعيد مجموعة تُغلَّف بـ`{ data:[…], meta:{ next_cursor, limit } }` — **بلا استثناء**، بما فيها القوائم غير المرقّمة/المحدودة (تُعيد `next_cursor: null`). المورد المفرد يُعاد **مجرّداً** (بلا غلاف).
- **الترقيم:** `?limit=<=100&cursor=<opaque>` على المجموعات المرقّمة؛ ويُعاد المؤشّر التالي في `meta.next_cursor` (=`null` عند آخر صفحة أو عند مجموعة محدودة غير مرقّمة). و`meta.limit` هو الحجم **المطلوب** لا عدد الصفوف المُعاد.
- **اتّجاه الترتيب (تعديل 6.3‑ب):** كلّ مجموعةٍ مرقّمة تُعاد **الأحدث أوّلاً** (‏keyset تنازليّ على `id` الذي هو UUIDv7 زمنيّ الترتيب) — الاستثناء الوحيد `listMessages`، إذ يُقرأ نصّ المحادثة **إلى الأمام** بـ`seq`.
- **المؤشّر معتِم وكلّيّ الفكّ (تعديل 6.3‑أ):** أيّ مؤشّرٍ مشوَّه — base64 غير صالح، أو خارج الأبجديّة، أو صالحٌ يحمل مفتاحاً من مجموعةٍ أخرى — يُرَدّ **422 `common.invalid_cursor`**، ولا يبلغ أيّ استعلام.

## 1) مخطط الموارد
| المورد | المسار الأساس | العمليات |
|--------|---------------|----------|
| Workspace | `/api/v1/workspace` | GET · PATCH |
| Spaces | `/api/v1/spaces` | GET(list) · POST · PATCH `{id}` · DELETE `{id}` |
| Agents | `/api/v1/agents` | GET(list) · GET(one) · POST `{key}/invoke` |
| Models | `/api/v1/models` | GET(list) |
| Conversations | `/api/v1/conversations` | GET(list by agent) · POST · GET · PATCH · PUT `{id}/model` · DELETE · GET/POST `…/messages` · DELETE `…/messages/{message_id}` · GET/POST `…/files` · DELETE `…/files/{file_id}` |
| Files | `/api/v1/files` | POST(register) · POST `{id}/complete` · GET(list) · GET · PATCH · DELETE |
| Knowledge | `/api/v1/knowledge` | POST `/search` · GET `/documents` · **POST `/documents`** · GET `/documents/{id}` · POST `/reindex` · GET `/reindex/{id}` · POST `/reindex/{id}/cancel` · POST `/documents/{id}/summary` · GET `/documents/{id}/summary` · DELETE `/documents/{id}/summary` · GET `/documents/{id}/summary/export` · GET `/summary-jobs/{id}` · POST `/summary-jobs/{id}/cancel` |
| Media | `/api/v1/media` | POST `/jobs` · GET `/jobs/{id}` |
| Workflows | `/api/v1/workflows` | GET(list) · POST `{key}/run` · GET `/runs/{id}` |
| Credentials | `/api/v1/credentials` | GET · POST · DELETE |
| Integrations | `/api/v1/integrations` | GET `/connectors` · GET/POST `/connections` · POST `/connections/{id}/authorize` · GET `/connections/oauth/callback` · DELETE `/connections/{id}` · GET/POST/DELETE `/mcp-servers` · GET `/tools` |
| Usage | `/api/v1/usage` | GET (summary) · GET `/limits` · PUT `/limits` |
| Streaming | `/api/v1/ws` (WS) · `Accept: text/event-stream` (SSE) | interactive · single-response |
| Health | `/health` · `/health/ready` | GET (بلا مصادقة) |

> **الوحدة (space) على السلك — ثلاثة مواضع لا مسارٌ واحد** (‏[`01 §2.11`](01-data-model.md)):
> ① `?space_id=` **إلزاميّ** على السرود الثلاثة `GET /files` · `GET /conversations` · `GET /knowledge/documents` (بارامتر `SpaceId` في [`openapi.yaml`](openapi.yaml)، `required: true`). ومعرّفٌ لا يسمّي وحدةً يعطي **صفحةً فارغة** لا `404`: الترشيح لا يُثبت، وسردٌ يميّز «غير موجودة» من «فارغة» يصير عرّافَ وجودٍ لمعرّفاتٍ لم يُعطها أحد.
> ② `space_id` في **جسد** الكتابة: `POST /files` · `POST /conversations` (إلزاميّ) · `POST /agents/{key}/invoke` (اختياريّ) · `POST /workflows/{key}/run` (إلزاميّ). وهذه **تُثبت** الوحدة قبل الكتابة، لأنّ صفًّا يُكتب تحت وحدةٍ غير موجودة لا يراه أيّ سردٍ إلى الأبد.
> ③ `DELETE /spaces/{id}` **بلا `Idempotency-Key`** خلافاً لبقيّة العمليّات الثقيلة: سلسلة الحذف عديمةُ الأثر عند التكرار **بالبناء**، بالضبط كي تُصلَح سلسلةٌ ماتت في منتصفها بإعادة تشغيلها — ودفترٌ أمامها يعيد الجواب المخزَّن و**يتخطّى** ما بقي، فتبقى الوحدة موسومةً وملفّاتها ونقاطها في مكانها إلى الأبد. وعدُ `§0` يفي به الطريق نفسه، وهو صورته الأقوى.
> **حدود `usage`:** الفرض (قبل العملية) والالتقاط (بعد العملية) **منفذان واردان داخليّان** يستدعيهما المُنسِّق (طبقة الوكلاء) — **ليسا API عاماً** (`FR‑131/132`)؛ مسارات `/usage` أعلاه للعرض/الإعداد فقط.
> **MCP:** خوادم MCP **بعيدة (HTTP/SSE) حصراً** في v1؛ لا تسجيل نقل stdio محلي (§6.13).

## 2) نماذج DTO الأساسية (Pydantic v2)
```python
# استجابة موحّدة للمجموعات — تُطبَّق على **كل** نقطة نهاية تُعيد مجموعة (API‑04)،
# حتى المحدودة/غير المرقّمة (مثل listAgents/listWorkflows/listCredentials/listConnectors/
# listMcpServers/listDiscoveredTools/searchKnowledge/getUsageLimits/putUsageLimits):
# تُعاد ضمن Page[T] بـ meta.next_cursor=None. المورد المفرد فقط يُعاد مجرّداً.
# تعديل 6.3‑ب: listDocuments خرج من قائمة «المحدودة» وصار مرقّماً — الكوربوس ينمو
# بصفٍّ لكلّ رفعٍ مكتمل بلا سقفٍ في التصميم، بخلاف البواقي المحدودة بالبناء.
class Page[T](BaseModel): data: list[T]; meta: PageMeta
class PageMeta(BaseModel): next_cursor: str | None; limit: int   # next_cursor=None ⇒ آخر صفحة / مجموعة محدودة

# Workspace
class WorkspaceOut(BaseModel): id: str; name: str; status: str; created_at: datetime
class WorkspacePatchIn(BaseModel): name: str = Field(min_length=1, max_length=80)

# Agents
class AgentOut(BaseModel):
    key: str; name: str; version: str; description: str
    capabilities: list[str]; required_permissions: list[str]
# `space_id` **اختياريّ هنا وحده** بين كتّاب الوحدة، وليس تراخياً: طلبٌ يسمّي
# `conversation_id` **يرث وحدة ذلك الخيط** ولا يجوز أن يُعيد ذكرها، وإلّا ادّعى عميلٌ
# وحدةً لخيطٍ لا يملك محورَه. والتركيبة الوحيدة التي تفلت — لا خيط ولا وحدة — ترتدّ
# `422` **قبل الإقلاع**: قبل إنشاء الوكيل وقبل كتابة صفّ.
class AgentInvokeIn(BaseModel):
    conversation_id: str | None = None
    space_id: str | None = None
    input: dict[str, Any]
    stream: bool = False
class AgentInvokeOut(BaseModel):        # عند stream=false
    conversation_id: str; message: MessageOut; usage: Usage
class Usage(BaseModel): prompt_tokens: int; completion_tokens: int

# Spaces — محور الملكيّة (01 §2.11). الثلاثة أعمدةٍ في `SpaceOut` **مجاميعُ وحداتٍ أخرى**
# (`files` و`conversations`)، تُقرأ بالجمع لصفحةٍ كاملة لا بنداءٍ لكلّ صفّ؛ ولا `version`
# ولا `deleted_at` على السلك: حالةُ القفل التفاؤليّ ليست شأن العميل.
class SpaceCreateIn(BaseModel): name: str            # لا id ولا حصّة: الأوّل للخادم والثانية ثابتٌ منصّة
class SpaceRenameIn(BaseModel): name: str            # مطلوب الحضور (سابقة FileRenameIn)
class SpaceOut(BaseModel):
    id: str; name: str
    bytes_used: int; file_count: int; conversation_count: int
    created_at: datetime

# Models — كتالوج التوجيه المُعَدّ (02 §3.5.1)، لا كتالوج المزوّد البعيد: ما يُعرَض هنا
# هو بالضبط ما يستطيع `resolve_llm` توجيهه (D‑16/FR‑73). `capability` هو المُعرِّف —
# مفتاح التوجيه ذاته — و`available=false` تعني «مُعَدّ لكنّ مفتاحه لا يُحَلّ لك» لا «غير موجود».
class ModelOut(BaseModel):
    capability: str; provider: str; model: str; available: bool

# Conversations
class ConversationOut(BaseModel):
    id: str; agent_key: str; kind: str; title: str | None
    model_route: str | None                                  # مفتاح توجيه مثبَّت، أو null ⇒ يُحلّ بمفتاح الوكيل
    created_at: datetime
class ConversationCreateIn(BaseModel): agent_key: str; space_id: str; title: str | None = None
# PATCH /conversations/{id} — العنوان وحده قابل للتعديل؛ `title` **مطلوب** الحضور
# (لا حقل اختياري يجعل «الحذف» و«عدم الذكر» متطابقين): قيمة نصّية تُسمّي، و`null` يمسح.
class ConversationPatchIn(BaseModel): title: str | None
# PUT /conversations/{id}/model — مسارٌ فرعيّ مستقلّ لا حقلٌ في الـPATCH: `title` مطلوب الحضور
# هناك، فحقلٌ ثانٍ مطلوب كان سيُلزم من يُعيد التسمية بإعادة ذكر المسار (والعكس). و`route`
# **مفتاح توجيه** من `ModelOut.capability` لا اسم موديل (D‑16/FR‑73): يُتحقَّق منه مقابل الجدول
# الحيّ (مجهول ⇒ 422)، و`null` يفكّ التثبيت. مزوّدٌ بلا مفتاح **يُقبَل**: الإتاحة صفةُ اللحظة لا صفةُ المسار.
class ConversationModelIn(BaseModel): route: str | None
class MessageOut(BaseModel):
    id: str; role: str; content: dict[str, Any]; token_count: int | None; seq: int; created_at: datetime
class MessageCreateIn(BaseModel):
    content: dict[str, Any]; stream: bool = False        # يشغّل الوكيل ويعيد ردّه
# DELETE /conversations/{id}/messages/{message_id} — حذفٌ ناعم لدورٍ واحد: يخرج من كلّ قراءة
# ويبقى `seq` محجوزاً (INV‑CV3)، فالنصّ يُظهر فجوة ولا يُعاد ترقيمه. بلا جسدٍ وبلا رَدّ (204).
# **مُعشَّش تحت خيطه** لأن الرسالة كيانٌ ابن لا جذرَ تجميعة: المسار نفسه هو تحقّق الملكية،
# فرسالةٌ مقرونة بخيطٍ آخر ⇒ 404 لا حذفٌ عابرٌ للخيوط. مثاليّ (تكرارٌ ⇒ 204)، وخيطٌ محذوف
# ناعماً ⇒ 409 لا 404 (نفس تباين القراءة/الكتابة في `PATCH`). الصلاحية `conversations:delete`
# لا `conversations:write`: محوُ نصٍّ دوراً بدور يجب ألّا يُتاح حيث حذفُ الخيط نفسه لا يُتاح.

# `…/files` — **تثبيت نطاق الاسترجاع، لا امتلاك الملف.** الملفّ يبقى على مستوى مساحة العمل
# (يُرفَع مرّة ويُفهرَس مرّة ويُعاد استخدامه): مجموعة Qdrant واحدة لكل مساحة عمل و`knowledge.documents`
# لا يعرف المحادثات (01 §2.7). فالتثبيت يقول «أجب من هذه المستندات وحدها»: مع تثبيتٍ يُرشَّح
# الاسترجاع بـ`document_id` المشتقّ من هذه الملفات، وبلا تثبيت يبقى النطاق الشامل — سلوكُ كلّ
# خيطٍ قائم اليوم، وهو الافتراضي. لهذا العدّ على بطاقة المحادثة **رقمٌ صادق**: ليس عدد ملفات
# مساحة العمل مكرَّراً على كل بطاقة، بل ما تُجيب منه هذه المحادثة فعلاً.
# المُخرَج مرجعٌ (`file_id`) لا وصفٌ للملف: الاسم والحجم والحالة تملكها `GET /files`، وضمّها هنا
# كان سيجعل `conversations` يُسقِط أعمدة وحدةٍ أخرى. العميل يدمج بالمعرّف كما يدمج حالة المعرفة.
# التثبيت يتحقّق من أن الملف **قابل للقراءة** (`ready`، INV‑F2): مجهولٌ أو محجورٌ أو نصفُ مرفوع ⇒ 422
# لا مرجعٌ مخزَّن لا يبلغه الاسترجاع أبداً. مثاليّ: إعادة التثبيت تُعيد `pinned_at` الأصلي لا صفّاً ثانياً.
# فكّ التثبيت لا يحذف ملفاً ولا فهرساً، فصلاحيته `conversations:write` لا `conversations:delete`؛
# وكلاهما `write` لأن التثبيت — كتثبيت النموذج — يُعِدّ كيف يُجيب الخيط. خيطٌ محذوف ⇒ 409.
class ConversationFileIn(BaseModel): file_id: str
class ConversationFileOut(BaseModel): file_id: str; pinned_at: datetime

# Files
# `space_id` إلزاميّ: هو الوحدة التي تُحمَّل عليها الحصّة، ويُفحص السقف **قبل توقيع رابط
# الرفع** لا بعده — تحت قفل صفّ الوحدة، وإلّا مرّ رفعان متزامنان بـ600 MiB لكلٍّ منهما
# على المجموع نفسه فحملت الوحدة 1.2 GiB. تجاوزٌ ⇒ `spaces.quota_exceeded` (409).
class FileRegisterIn(BaseModel):
    name: str; content_type: str; size_bytes: int; space_id: str
class FileRegisterOut(BaseModel):
    file_id: str; upload_url: str; expires_in: int       # presigned PUT (MinIO)
class FileCompleteIn(BaseModel): checksum: str | None = None
# ⚠️ **إتمامُ الرفع باسمٍ تحمله الوحدة أصلاً يستبدل صاحبَه** (‏س-29 القاعدة ١، قرار
# المالك 2026‑08‑25): الملفّ الأقدم يُحذف **مع فهرسه**. لا يتغيّر شيءٌ في الجسد ولا في
# الحالات — الاستبدال يجري **بعد** نجاح الإتمام ولا يستطيع أن يحوّله إلى فشل — لكنّ
# `GET /files` بعده يعيد صفًّا واحداً حيث كان صفّان. والاتّجاه ملزم: **الأحدث يَجُبّ
# الأقدم ولا عكس**، والمقارنة على `lower(normalize(name, NFC))` داخل الوحدة وحدها.
# ولا شيء يقع عند **التسجيل**: رابط رفعٍ موقَّعٌ وعدٌ، ووعدٌ لا يُقايَض بملفٍّ قائم.
# الاسمُ هو الحقل الوحيد القابل للتعديل في الملف؛ والامتدادُ **غير قابل للتعديل**:
# مطابقٌ (بلا حساسيةٍ لحالة الأحرف) ⇒ يُخزَّن كما أُرسل · بلا امتداد ⇒ يرث الحاليّ ·
# مختلفٌ ⇒ 422. الامتداد ادّعاءٌ عن البايتات، والبايتات لا تتغيّر؛ فتغييره يجعل
# الاسم المعروض كذبةً ويُسلّم التنزيل إلى مُشغّل نظامٍ خاطئ. ولا حدث ولا إعادة فهرسة:
# `knowledge.documents` يفهرس بـ`file_id` وحمولة Qdrant لا تحمل اسماً أصلاً.
# ⚠️ **لكنّ `PATCH` يستبدل كما يستبدل الإتمام** (‏س-29 القاعدة ١): إعادةُ التسمية
# تصنع التصادم بعد الرفع، فتسميةُ ملفٍّ باسم ملفٍّ **أقدم** في وحدته تحذف ذاك مع فهرسه.
# فالجملة «لا إعادة فهرسة» تبقى صحيحةً عن `RenameFile` نفسه، لا عن المسار.
class FileRenameIn(BaseModel): name: str
class FileOut(BaseModel):
    id: str; name: str; content_type: str; size_bytes: int
    status: str; download_url: str | None; created_at: datetime   # presigned GET عند ready

# Knowledge
class KnowledgeSearchIn(BaseModel):
    query: str; k: int = Field(default=5, le=50); space_id: str = Field(min_length=1)
# `space_id` **إلزاميّة** (‏س-32، قرار المالك 2026‑08‑26): الفضاءات معزولةٌ عزلاً تامّاً —
# ملفّاتُها وفهرسُها وصفوفُها — فالبحث يقع في فضاءٍ واحدٍ أو لا يقع. كانت غائبةً عن هذا الجسد،
# والمسار يمرّر `space_id=None` صراحةً، فكان **المسلكَ الوحيد في النظام** الذي يسترجع من كلّ
# فضاءات مساحة العمل معاً؛ وقياسٌ في تدقيق الأمانة أُجري عبره ونُسب إلى سلوك المنتَج ثمّ
# سُحب. وحذفُها (أو إرسالُها فارغةً) ⇒ **422** قبل أن يُحسَب تضمينٌ واحد. وهي على **الجسد**
# لا كمُعامل استعلامٍ كما في الثلاثة القوائم: هذا `POST` كلُّ مُدخَله كائنٌ واحد، وشطرُ أحد
# تضييقاته الثلاثة إلى المسار كان سيجعل العميل يذكر بحثاً واحداً في موضعين.
# والتباين مع `k` هو القرارُ نفسُه: `k` غائبةً تطلب رقمَ النشرة، والفضاءُ غائباً كان يطلب
# كوربوسَ الجميع.
# `file_name`/`page_number`/`section` (retrieval plan §3.1/§3.9, س-19, `P-18`) — الحقول التي
# يستشهد بها العميل بلا رحلة ثانية. حقول صريحة لا `metadata: Mapping` (mypy --strict لا يخمّن
# مفاتيح). الثلاثة `| None` وتُنشَر دائماً على السلك (لا تُحذف): نقطة سبقت هذا الحقل، أو محلِّلٌ
# لم يُصدر إحداها، تنحدر إلى `null` — نفس عُرف `FileOut.download_url`.
class RetrievedChunkOut(BaseModel):
    document_id: str; chunk_id: str; text: str; score: float
    file_name: str | None = None; page_number: int | None = None; section: str | None = None
class DocumentOut(BaseModel):
    id: str; file_id: str; status: str; chunk_count: int; created_at: datetime
# الفهرسة **يدويّة**: إتمام الرفع لم يعد يسجّل مستنداً ولا يبدأ خطَّ الأنابيب. الملف يبقى
# بايتاتٍ في التخزين لا يجيب عن بحث حتى يُطلب `POST /knowledge/documents` له مرّةً واحدة.
# الملف يجب أن يكون `ready`: نصفُ مرفوعٍ أو محجورٌ أو محذوفٌ أو مجهولٌ أو لمستأجرٍ آخر ⇒ 404
# واحدة لا تميّز بينها — وهي نفسها شرط «انتظر اكتمال الرفع» مفروضاً لا موصى به. وملفٌّ له
# مستندٌ أصلاً ⇒ 409: لا لأن مستنداً ثانياً مستحيل (INV-K3 يجيزه — إعادة الرفع تسكّ واحداً)
# بل لأن مستندين حيّين على ملفٍ واحد يجعلان كلَّ بحثٍ يجيب منه مرّتين؛ إعادةُ البناء وظيفة
# `POST /knowledge/reindex` وحده، الوجهُ الذي يُتلف ما يستبدله. الوحدة (`space_id`) تُقرأ من
# **الملف** لا تُقبل من المتصل، وإلّا أُودع محتوىً في وحدةٍ لا ينتمي إليها ملفُّه. الردّ 202
# بالمستند `pending`: التضمين عملُ عاملٍ، و201 كان سيَعِد بمدخلِ فهرسٍ لا وجود له بعد.
# `Idempotency-Key` مُكرَّم — إعادةُ إرسالٍ كانت ستشتري مستنداً ثانياً وتضميناً مدفوعاً مرّتين.
# الصلاحية `knowledge:manage` لا `files:write` التي رفعت الملف: الصلاحية تتبع ما تُنفقه
# المكالمة، وهذه تنفق حصّة تضمين على فهرسٍ مشترك.
class IndexFileIn(BaseModel): file_id: str
# إعادة الفهرسة (BE-RAG-007/008): كل هدفٍ تحلّ محلّه وثيقةٌ جديدةٌ على الملف نفسه
# (INV-K3) بعد **إتلاف** القديمة — نقاط Qdrant ثمّ المقاطع ثمّ الصفّ (INV-K4)، وإلا
# أجاب الملفُّ كلَّ بحثٍ مرّتين. الطرفيّة وحدها قابلة (`pending`/`indexing` ⇒ 409)،
# والمجهولة 404، والعدد 1..50. الردّ 202 لأن العمل عاملٌ لا طلب. والتقدّم **مُشتقّ**
# من حالات الوثائق (INV-K5) لا مخزَّن؛ و`current_file_id` مرجعٌ يدمجه العميل مع
# `GET /files` كما يفعل مع التثبيتات. الصلاحية `knowledge:manage` للكتابتين لا
# `knowledge:read`: هذا يحذف فهرساً عاملاً وينفق حصّة تضمين.
class ReindexIn(BaseModel): document_ids: list[str] = Field(min_length=1, max_length=50)
class ReindexItemOut(BaseModel):
    document_id: str; file_id: str; source_document_id: str; status: str
class ReindexJobOut(BaseModel):
    id: str; status: str; total: int; finished: int; percent: int
    current_file_id: str | None; items: list[ReindexItemOut]
    created_at: datetime; cancelled_at: datetime | None

# Media
class MediaJobCreateIn(BaseModel):
    kind: Literal['image','video']; prompt: str; agent_key: str; params: dict[str, Any] = {}
class MediaJobOut(BaseModel):
    id: str; kind: str; status: str; result_file_id: str | None; error: str | None; created_at: datetime

# Workflows
class WorkflowOut(BaseModel): key: str; name: str; steps: list[str]
# `space_id` **إلزاميّ** هنا خلافاً لـ`AgentInvokeIn`: الوظيفة تفتح خيطها **دائماً**
# (وهو ما يجعل الجرية قابلة للتعريف)، فلا خيطَ سابقاً ترث وحدتَه.
class WorkflowRunIn(BaseModel): space_id: str; input: dict[str, Any]; stream: bool = False
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
- **مفاتيح الوكيل الطرفيّة تبقى على `final` إلى جانب ما تضيفه المنصّة** (`message_id`/`content`/`usage`): لكلّ وكيل مفاتيحه الخاصّة (`job_id`, …)، ولا يُسقِطها المنسِّق. `rag_agent` يضيف `citations` — قائمة استشهادات مفهومة (خطّة الاسترجاع §3.2/§4 صفّ ٣، `P-32`) لا UUID عارٍ:
  ```json
  "citations": [
    {"document_id": "018f...", "file_name": "maintenance.pdf", "page": 12, "chunk_id": "018f...", "rank": 1}
  ]
  ```
  `document_id` و`chunk_id` حاضران دائماً؛ `file_name`/`page` **يُعادان دائماً بالمفتاح** ويُصبحان `null` صراحة حين لا يحملهما الشذر المسترجَع نفسه (نقطة سبقت هذا الحقل، أو محلِّلٌ لم يُصدره) — نفس عُرف `RetrievedChunkOut` أعلاه، لا مفتاحٌ يُحذَف.
  و`rank` (خطّة السيناريوهات §٨، ب‑١٢، الفجوة ف‑١٢) **رتبةُ المصدر، مبدوءةً بـ١** — عددٌ يقرؤه إنسان لا فهرسٌ يفكّه برنامج. القائمةُ تصل مرتّبةً تنازليّاً بالصلة أصلاً ولا يعيد الوكيلُ ترتيبَها (مُثبَّتٌ باختبار)، فالحقلُ **لا يغيّر ترتيباً**؛ ما يغيّره أنّ الترتيبَ يكفّ عن أن يكون الموضعَ الوحيدَ الذي يسكنه المعنى: مَن أراد تمييزَ المصدر الأوّل بصريّاً يقرأ رقماً بدل أن يثق بأنّ التسلسلَ نجا من التسلسل والتخزين وإعادةِ العرض ومكوّنِ القائمة عنده. ⚠️ وما **لا** يقوله هذا الحقل: أيَّ المصادر استعمل النموذجُ فعلاً — ذلك لا يُعرف إلّا بسؤاله، ونموذجٌ يُسأل يُجيب بقائمةٍ واثقةٍ على أيّ حال، فتُلبَس التلفيقَ هيئةَ الاستشهاد.

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
- `final` هنا يحمل مفاتيح الوكيل الطرفيّة نفسها التي يحملها إطار SSE (§3.1) — بما فيها `citations` لـ`rag_agent` بشكلها المفهوم `{document_id, file_name, page, chunk_id, rank}` — لا مجرّد ما في المثال أعلاه.
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

> **مصدر الحقيقة الوحيد منذ 6.2:** `ERROR_CATALOG` في `src/app/framework/errors.py` — الجدول أدناه صورته. لا يُضاف رمز هنا دون موضع يُصدره في الكود، ولا يُصدر الكود رمزاً غائباً عن الكتالوج؛ يفرض الاتّجاهين **`tests/unit/test_error_catalog.py`** بمسحٍ ثنائيّ لكامل `src/app`. الحالة (`status`) خاصيّةُ **الرمز** لا خاصيّةُ صنف الاستثناء: تمرير `code=` وحده يكفي.

| code | HTTP | متى |
|------|------|-----|
| `auth.missing_token` | 401 | لا Bearer |
| `auth.invalid_token` | 401 | توقيع/انتهاء JWT |
| `authz.forbidden` | 403 | صلاحية RBAC غير كافية |
| `common.validation_error` | 422 | فشل تحقّق DTO أو قاعدة عمل (يملأ `errors[]` في الحالة الأولى) |
| `common.not_found` | 404 | مورد غير موجود ضمن المستأجر |
| `common.conflict` | 409 | تعارض قفل تفاؤلي (`version`) أو تفرّد |
| `common.invalid_cursor` | 422 | مؤشّر ترقيم مشوّه (`API‑02`) |
| `common.rate_limited` | 429 | تجاوز حدّ المعدّل (‏`Retry-After` يُصدَر متى وُجد مُنتِج حقيقيّ — انظر أدناه) |
| `common.too_large` | 413 | تجاوز حدٍّ عدديّ **غير الرفع** (مثال: ملف يُقرأ داخل ميزانية موجِّه) |
| `common.unsupported_type` | 415 | نوع محتوى غير مدعوم خارج سياق `files` |
| `common.method_not_allowed` | 405 | المسار موجود والفعل غير مُعرَّف عليه (موجّه Starlette) |
| `common.internal` | 500 | خطأ غير متوقّع (يُخفي التفاصيل) |
| `agent.unknown` | 404 | `agent_key` غير مُسجّل |
| `agent.failed` | 502 | فشل تنفيذ الوكيل/المزوّد |
| `workflow.unknown` | 404 | `workflow_key` غير مُعرّف |
| `files.too_large` | 413 | تجاوز حجم الرفع |
| `files.unsupported_type` | 415 | نوع MIME خارج القائمة البيضاء |
| `files.too_many` | 409 | بلوغ سقف ملفات المساحة |
| `spaces.duplicate_name` | 409 | اسم وحدةٍ مستعمَل في المساحة (من `23505` على `ux_spaces_ws_name`، لا من قراءةٍ مسبقة) |
| `spaces.quota_exceeded` | 409 | بلوغ حصّة بايتات الوحدة (1 GiB) عند تسجيل رفع — **لا 413**: الحمولة مشروعة وحالةُ الوحدة هي المانع، والعلاج حذفُ شيء. ولا `usage.quota_exceeded` (429): تلك ميزانيّة فترةٍ تنقضي، وهذه ستبقى متجاوَزةً غداً |
| `spaces.cross_space_pin` | 409 | تثبيت ملفٍّ من وحدةٍ أخرى في خيطٍ (‏`02 §2`) — **لا 422**: الطلب سليم والملفّ مقروء، والعلاج «ثبِّته في خيطٍ من وحدته أو ارفع **نسخة**» (لا نقل) |
| `knowledge.unsupported_type` | 415 | امتداد مستند لا مُحلِّل له |
| `knowledge.empty_content` | 422 | مستند بلا محتوى للتحليل |
| `knowledge.parse_failed` | 422 | تعذّر تحليل المستند |
| `knowledge.search_unavailable` | 503 | البحث المعرفيّ غير منشور (لا مِحوَل تضمين) |
| `media.invalid_params` | 422 | معاملات توليد خارج الحدود |
| `media.unsupported_kind` | 422 | نوع وسائط لا مولّد له بعد (الفيديو) — يظهر في `error` المهمّة لا كردّ مشكلة |
| `credentials.provider_unknown` | 422 | مزوّد غير مدعوم |
| `credentials.none_available` | 409 | لا مفتاح مستخدم/منصّة للمزوّد (لا Fallback، D‑16) |
| `integrations.connector_unknown` | 422 | موصّل غير موجود في الكتالوج |
| `integrations.oauth_failed` | 502 | فشل تبادل/تجديد رمز OAuth مع الطرف الثالث |
| `integrations.oauth_state_invalid` | 422 | لافتة `state` مزوّرة/منتهية/مُعادة على ردّ النداء العموميّ |
| `integrations.not_connected` | 409 | استخدام موصّل غير مُتّصل/مُبطَل |
| `integrations.too_many` | 409 | بلوغ سقف موصّلات/خوادم MCP للمساحة |
| `integrations.mcp_transport_unsupported` | 422 | نقل MCP غير مدعوم (مسموح `http`/`sse` فقط — v1) |
| `integrations.mcp_unreachable` | 502 | تعذّر الوصول لخادم MCP البعيد |
| `integrations.tools_unavailable` | 503 | كتالوج الأدوات غير منشور (لا مِحوَل موصّلات/MCP) |
| `usage.quota_exceeded` | 429 | تجاوز **حصّة** الرموز/الطلبات (يعيده المُنسِّق عند رفض الفرض؛ `LimitDecision.reason='quota_exceeded'`) |
| `usage.budget_exceeded` | 429 | تجاوز **الميزانية** (التكلفة `cost_micros`) (يعيده المُنسِّق عند رفض الفرض؛ `LimitDecision.reason='budget_exceeded'`) |

**تعديلات 6.2 على الكتالوج (وأسبابها):**
- **حُذف `files.not_ready` (409):** لا موضع له في v1 — `FilesQuery.get_readable` يطوي «غير موجود» و«غير جاهز» في `None` واحدة عمداً (وجه القراءة لا يُخبر مستدعياً بأنّ ملفاً يعجز عن قراءته موجودٌ رغم ذلك)، ومسارات الملفّات تجيب عن غير الجاهز بجسدٍ (`download_url: null`) لا بمشكلة. رمزٌ لا يُصدره خادمٌ وعدٌ لا يستطيع عميلٌ الاتّكاء عليه.
- **حُذف `knowledge.not_indexed` (409):** للسبب نفسه — لا مسار استرجاعٍ لمستندٍ بعينه، وإلصاق الرمز بـ«البحث لم يجد شيئاً» يحوّل نتيجةً فارغةً مشروعة إلى فشل.
- **حُذف `common.validation` (لم يكن هنا أصلاً وكان في الكود):** توحيد مع `common.validation_error` — مشكلةٌ واحدة كانت تجيب باسمين يفرّق بينهما ما لا يراه العميل ولا يتوقّعه.
- **أُضيفت المواضع الغائبة:** `agent.unknown` (سجلّ الوكلاء + `GET /agents/{key}`) و`credentials.none_available` (‏`ResolveCredential`، وتغيّرت حالته من 404 إلى 409: المزوّد موجود والمساحة موجودة، والناقص مفتاحٌ يستطيع المستدعي إضافته).
- **أُضيفت الأكواد التي سكّها التنفيذ:** `common.invalid_cursor` · `common.too_large` · `common.unsupported_type` · `files.too_many` · `integrations.too_many` · `integrations.oauth_state_invalid` · `knowledge.*` الثلاثة · فتحتا الـ503 (`knowledge.search_unavailable` · `integrations.tools_unavailable`).

**تعديلات 6.2‑ب:**
- **أُضيف `common.method_not_allowed` (405):** ولا يرفعه أيّ صنف استثناء — موجّه Starlette وحده يُنتجه (لا شيء في `src/app` يرفع `HTTPException` أصلاً). وقبل 6.2‑ب كان مسارٌ مجهول (404) أو فعلٌ خاطئ (405) يجيب `{"detail": …}` بـ`application/json`: **أوّل ردٍّ يراه عميلٌ من عنوانٍ مطبوعٍ خطأً كان الردَّ الوحيد الذي ليس مشكلة**، ولا اختبار موجّهٍ كان يمكن أن يمسكه لأنّ لا موجّه يشارك فيه. وحالةٌ غير معروفة في الخريطة تتدهور إلى `common.internal`/500 (مسارُ إطارٍ لا نُمثّله) بدل رمزٍ يُخترع من الحالة لحظتَها.
- **`Retry-After`: كان مؤجَّلاً لانعدام المُنتِج، وقد صار له مُنتِج (‏3.79).** التأجيل لم يكن سهواً: `LimitDecision` (‏`02 §2`) كان يحمل `remaining` ولا يحمل وقت إعادة التعيين، فأيّ قيمةٍ كنّا سنضعها **مخترَعة**، والرأس المخترَع أسوأ من غيابه (عميلٌ يتراجع لعددٍ لا يعني شيئاً). والامتداد الذي نصّت عليه هذه الفقرة نفسها — «حقل إعادة تعيين على `LimitDecision`» — نُفِّذ: `LimitDecision.retry_after_s` (إضافيّ، افتراضه `None`) يملؤه مِحوَل الفرض من حدّ فترة القاعدة المُلزِمة (`usage/domain/periods.py::period_reset_at`: أوّل الشهر التالي بـUTC للفترة `month`، ومنتصف الليل التالي للفترة `day`)، ويحمله المُنسِّق كما هو إلى `RateLimitedError`، فيصيّره مُعالِج `AppError` رأس `Retry-After` بصيغة delay-seconds على الـ429 وحده. `None` ⇒ **لا رأس إطلاقاً**، وهو الجواب الصادق لرفضٍ لا فترةَ له. و429 المزوّد لا يصير 429 لنا أصلاً (‏`openai_llm` يحوّله `agent.failed` عمداً كي لا يُخبر المستأجر أنّه بلغ حدَّنا، ولئلّا تتسرّب رؤوس حساب المنصّة) — ولذلك لا `provider.rate_limited` في الكتالوج (انظر البند التالي).
- **`provider.rate_limited`: مرشّحٌ **مرفوض**، لا مؤجَّل (‏3.79).** كان `openai_llm` يحمل ملاحظة «مرشّح مزامنة توثيقيّة» تقترح إضافة الرمز إلى `03 §4`، فحُسم الأمر في الاتّجاه المعاكس وحُذفت الملاحظة. السبب مبدئيّ لا ذوقيّ: **صفٌّ في الكتالوج وعدٌ بالإصدار** (‏`tests/unit/test_error_catalog.py` يفرض الاتّجاهين: لا رمز يُصدَر خارج الكتالوج، ولا صفَّ في الكتالوج بلا موضع إصدار)، والقرار القائم أنّ 429 المزوّد **لا يصل المستأجر كحدِّ معدّلٍ إطلاقاً** ⇒ فلا موضع له، وإضافته تشحن صفّاً غير قابل للبلوغ — وهو بعينه ما حُذف من أجله `files.not_ready` و`knowledge.not_indexed` في 6.2. الكلفة تبقى مسجَّلة هنا لا في الكود: طيّ 429 يمحو الإشارة الوحيدة التي كان يصحّ لمُنسِّقٍ أن يعيد المحاولة عليها.
- **العقد المولَّد يعلن الخطأ:** كلّ عمليّة في `/openapi.json` تحمل `default` بمخطّط `ProblemDetails` تحت `application/problem+json` — مطابقةً لما يعلنه هذا الملفّ (`components.responses.Problem`)، بعد أن كان المولَّد لا يذكر الأخطاء إطلاقاً فيخرج عميلٌ مولَّدٌ منه بلا نوع خطأ.
- **كلّ مشكلةٍ تحمل `correlation_id` فعلاً:** مشكلات الـWebSocket على مستوى البروتوكول (إطارٌ غير قابل للتحليل، نوع رسالةٍ مجهول) تقع **قبل** وجود أيّ `ExecutionContext`، فكانت تُرسَل بلا الحقل الذي يعلنه `components.schemas.Problem` **مطلوباً**؛ صارت تسكّ واحداً لكلّ مشكلة (والغرض من الحقل أن يجد المشغّل **هذا** الحدث في السجلّ).

- كل الأخطاء تحمل `correlation_id` ⇒ نقطة توسعة تتبّع.
- `500` لا يسرّب داخلياً؛ التفاصيل في السجلّ فقط.

## 5) CORS — قائمة حافة صريحة (P1‑7)

واجهة Firebase Hosting أصلٌ مختلف عن الـAPI، لذا تملك حافّة nginx سياسة CORS، لا تطبيق FastAPI. تُسمح هذه الأصول فقط: `https://aizzak-agent.web.app` و`https://aizzak-agent.firebaseapp.com` و`http://localhost:5173` للتطوير. أي أصل آخر يجعل `$cors_allow_origin` فارغاً، فلا تصدر الترويسة أصلاً.

- لا `CORSMiddleware` في `src/app`.
- تعيد الحافة `Access-Control-Allow-Origin` من القائمة فقط، مع `Access-Control-Allow-Headers: Authorization, Content-Type` وطرق `GET, POST, PATCH, PUT, DELETE, OPTIONS`، وتجيب عن preflight بـ204.
- لا `*` مطلقاً، ولا انعكاس لأصلٍ غير موثوق.
