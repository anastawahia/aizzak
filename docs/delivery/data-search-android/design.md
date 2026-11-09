<div dir="rtl">

# التصميم التقني: ث‑1 حفظ الاستشهادات مع رسالة المساعد

- **المدخلات:** `requirements.md` و`stories.md` في هذا المجلد، وقرارات بوابة ① في `status.md` (ق‑1…ق‑4 والافتراضات المعتمدة). كلها ملزمة هنا.
- **الشيفرة التي فُحصت:** الفرع `fix/data-search-simple-gaps` على `69d4370`.
- **المرجع الأقرب الذي نتبعه:** ب‑9 (`pending_clarification`). فيه عمود `jsonb NULL` إضافي بترحيل `conversations/0005`، وفيه يقرأ المنسّق مفتاحاً من `final` قراءة دفاعية (`_pending_options`) ثم يكتبه عبر المنفذ. ونتبع فيه أيضاً اختبار التطابق `test_conversations_schema_parity.py`.

## 1. الملخص

الميزة تمر بأربع طبقات، ولا تمس غير وحدة `conversations` والمنسّق وطبقة `api`:

1. **`app.agents.orchestrator`:** يقرأ `_persist_reply` المفتاح `citations` من `final` قراءة دفاعية، ويحوّله إلى قيم مُنمَّطة `CitationRef`. ويمررها في **استدعاء `append` نفسه** الذي يكتب الرسالة. وإطار `final` نفسه لا يتغير (FR‑10).
2. **`app.modules.conversations`:**
   - **domain:** كائن قيمة جديد `Citation` هو مصدر قواعد الصحة الوحيد. وحقل جديد `Message.citations`. وثابت جديد INV‑CV4.
   - **ports:** `CitationRef` (مقبض مسطّح عبر الحد)، و`AppendedMessage.citations`، ومعامل `citations` على `ConversationThreads.append`.
   - **application:** `AppendMessage` يبني `Citation` من المراجع. فإن خالفت قاعدة، أو زاد العدد عن 50، **يُسقط الاستشهادات (`None`) ولا يُفشل الإدراج**.
   - **adapter:** عمود جديد `messages.citations` يُكتب في عبارة `INSERT` نفسها ويُقرأ في `SELECT` نفسه.
3. **`app.api`:** حقل مستقل `MessageOut.citations` بجانب `content`. وتملؤه المحوّلات الثلاثة: `GET …/messages`، والرد غير المتدفق على `POST /conversations/{id}/messages`، و`POST /agents/{key}/invoke`. ويبقى `content` بالشكل `{text, attachments}` بالضبط (ق‑2).
4. **البيانات:** ترحيل إضافي `conversations/0007_message_citations` يضيف `citations jsonb NULL`. التغيير metadata فقط، بلا backfill.

**توزيع الفحص:** المنسّق يسأل «هل هذا JSON بالشكل الصحيح؟» (أنواع JSON فقط). والوحدة تسأل «هل هذا استشهاد صحيح؟» (القيم: UUID، والرتبة، والطول، والعدد). بهذا لا تتكرر القواعد في مكانين، ويبقى قرار «الاستشهاد التالف لا يُفشل الدور» في مكان واحد (`AppendMessage`).

**دلالة القيم (معتمدة):**
- `null` = لم يُسجَّل. ويشمل: رسائل المستخدم، والصفوف القديمة، والوكلاء الذين لا يضعون المفتاح، والأجوبة المقطوعة، ومخرجات خطوات الـ workflow، والاستشهادات التالفة.
- `[]` = الوكيل صرّح بأنه لا مصادر.

**مساحة لـ ث‑2 (بلا بناء):** يضع هذا التصميم النمط الذي سيتبعه حقل الحالة لاحقاً:
- عمود مستقل على `conversations.messages` في ترحيل لاحق (`0008`)؛
- حقل مستقل على `MessageOut` لا داخل `content`؛
- حقل بقيمة افتراضية في آخر `AppendedMessage`؛
- معامل keyword بقيمة افتراضية على `append`.

لا شيء هنا يحجز اسماً أو قيمة لـ ث‑2.

## 2. الملفات

لا يعدّل منفّذان الملف نفسه.

| المسار | إنشاء/تعديل | المسؤولية | المنفّذ |
|---|---|---|---|
| `migrations/versions/conversations/0007_message_citations.py` | إنشاء | `ADD COLUMN citations jsonb NULL` على `conversations.messages`، و`downgrade` يُسقطه. docstring على نمط `0005` | data-engineer |
| `docs/design/01-data-model.md` (§2.4) | تعديل | سطر `citations` في `CREATE TABLE conversations.messages` مع تعليق. وتصحيح عبارة «`MessageContent` بلا نصفٍ مُهيكَل» في تعليق `pending_clarification` (§4 أدناه) | data-engineer |
| `tests/unit/test_conversations_schema_parity.py` | تعديل | تمديد التطابق إلى جدول `messages`، واختبارات `citations` (AC‑4.4) | data-engineer |
| `tests/integration/test_conversations_repository_rls.py` | تعديل | ذهاب وإياب حيّ، وعزل المستأجر للاستشهادات (AC‑5.2). يُشغَّل في CI `integration` فقط (ق‑4) | data-engineer |
| `src/app/modules/conversations/domain/value_objects.py` | تعديل | `Citation` و`MAX_CITATIONS` | backend-developer |
| `src/app/modules/conversations/domain/entities.py` | تعديل | `Message.citations`، و`Conversation.append_message(..., citations=)`، وINV‑CV4 | backend-developer |
| `src/app/modules/conversations/ports/inbound.py` | تعديل | `CitationRef`، و`AppendedMessage.citations`، و`ConversationThreads.append(..., citations=)` | backend-developer |
| `src/app/modules/conversations/application/use_cases.py` | تعديل | `AppendMessage.execute(..., citations=)` بالإسقاط الليّن، و`ConversationService.append` | backend-developer |
| `src/app/modules/conversations/adapters/sql_repository.py` | تعديل | عمود `citations` في الـ `Table`، والكتابة في `append_message`، والقراءة في `_hydrate_message` | backend-developer |
| `src/app/agents/orchestrator.py` | تعديل | `_declared_citations`، وتمريرها في `_persist_reply`، والتحذير بالعدد. وتحديث تعليقات ب‑10 و`_turn_content` بصدق (§3.4) | backend-developer |
| `src/app/api/v1/dto/conversations.py` | تعديل | DTO `Citation`، و`MessageOut.citations` | backend-developer |
| `src/app/api/v1/routers/conversations.py` | تعديل | `_to_message_out` و`appended_to_message_out` يملآن `citations` | backend-developer |
| `docs/design/openapi.yaml` | تعديل | مخطط `Citation`، و`MessageOut.citations` | backend-developer |
| `docs/design/03-api-spec.md` (§2، §3.1) | تعديل | `MessageOut.citations` و`Citation`، ودلالة `null`/`[]`، والمساواة مع `final` | backend-developer |
| `docs/design/06-domain-models.md` (§4) | تعديل | `Message.citations` و`VO Citation` وINV‑CV4، وملاحظة ب‑10 | backend-developer |
| `docs/delivery/data-search-android/gaps.md` (ث‑1) | تعديل | فقرة «الإصلاح (التاريخ)» بعد اخضرار البوابات (AC‑4.5) | backend-developer |
| `tests/unit/test_conversations_domain.py` | تعديل | قواعد `Citation` وINV‑CV4 | backend-developer |
| `tests/unit/test_conversations_use_cases.py` | تعديل | الإسقاط الليّن، ورفض الاستشهادات على رسالة غير المساعد، وتمرير الاستشهادات في الصفحات | backend-developer |
| `tests/unit/test_conversations_sql_statements.py` | إنشاء | العبارات المنفذة (AC‑1.9، AC‑2.7)، والترطيب (AC‑2.2)، والفرق بين SQL NULL و`[]` | backend-developer |
| `tests/unit/test_orchestrator.py` | تعديل | الـ fake `append` يقبل `citations`. واختبارات US‑1 على المنسّق | backend-developer |
| `tests/unit/test_workflow_orchestration.py` | تعديل | توقيع الـ fake `append` فقط، وأن مخرجات الخطوات `null` | backend-developer |
| `tests/unit/test_api_conversations_router.py` | تعديل | وكيل `_CitingAgent`، واختبارات US‑2 وUS‑3 وUS‑5 على السلك | backend-developer |
| `tests/unit/test_api_agents_router.py` | تعديل | AC‑3.2 | backend-developer |
| `tests/unit/test_api_conventions.py` | تعديل | اختبار مطابقة `MessageOut`/`Citation` بين `openapi.yaml` والتطبيق (AC‑4.1) | backend-developer |

**ملفات لا تتغير، وقد فُحص ذلك:**
- **`deploy/postgres/initdb/10-roles.sh` و`src/app/ops/provision.py`:** الصلاحيات على مستوى الجدول. `app_rw` يملك `SELECT, INSERT, UPDATE, DELETE` على `conversations.messages`، و`purge_operator` يملك `SELECT, DELETE` (`provision.py:407`). ولا توجد صلاحية على مستوى الأعمدة لهذا الجدول، فالعمود الجديد مغطى تلقائياً.
- **سياسة RLS:** `tenant_isolation` على مستوى الصف (`0001_conversations.py:106-115`)، فهي تحكم العمود الجديد دون تغيير.
- **`src/app/api/v1/routers/agents.py`:** يستعمل `appended_to_message_out` نفسه. و`AgentInvokeOut` يشير إلى `MessageOut` بـ `$ref`، فيرث الحقل.
- **ملفات أخرى:** `docs/design/events/schemas/` (لا حدث للرسائل)، و`rag_agent` (لا تغيير في ما يصدره)، و`.importlinter`.
- **الترحيلات المطبَّقة** (`0005_*` وغيره) تبقى كما هي، رغم أن docstring `0005` يذكر الفرضية القديمة. إنها تاريخ ما طُبِّق.

## 3. العقود

### الواجهات البرمجية

لا مسار جديداً. يتغير جسم الرد في ثلاثة مسارات قائمة.

| الطريقة | المسار | الطلب | الاستجابة | الأخطاء | الصلاحية |
|---|---|---|---|---|---|
| GET | `/api/v1/conversations/{id}/messages` | بلا تغيير (`limit`، `cursor`) | `MessagePage`. كل `MessageOut` فيه `citations: Citation[] \| null`، والمفتاح حاضر دائماً | بلا تغيير: 401، 403، 404 (محادثة غير موجودة أو من مساحة أخرى أو محذوفة)، 422 `common.validation_error` (cursor تالف) | `conversations:read` |
| POST | `/api/v1/conversations/{id}/messages` | بلا تغيير. أي `content.citations` يرسله العميل يُتجاهل (FR‑7) | `stream=false`: `MessageOut` لرسالة المساعد، و`citations` فيه مطابق لما يعيده السجل. `stream=true`: SSE، و`final` بلا تغيير (FR‑10) | بلا تغيير: 401، 403، 404، 409، 422، 429، وproblem+json من `_in_band_error` | `conversations:write` (+ صلاحيات الوكيل عبر المنسّق) |
| POST | `/api/v1/agents/{key}/invoke` | بلا تغيير | `stream=false`: `AgentInvokeOut.message.citations`. `stream=true`: بلا تغيير | بلا تغيير | `agents:invoke` |

**`MessageOut` على السلك (مثال):**
```json
{
  "id": "018f…",
  "role": "assistant",
  "content": {"text": "…", "attachments": []},
  "citations": [
    {"document_id": "018f…", "file_name": "دليل الصيانة ٢٠٢٦.pdf", "page": 12, "chunk_id": "6a1e…", "rank": 1}
  ],
  "token_count": 140,
  "seq": 2,
  "created_at": "2026-11-10T09:00:00Z"
}
```

**OpenAPI** (`docs/design/openapi.yaml`، بأسلوب الملف القائم `nullable: true`). يُدرج `Citation` بعد `MessageOut`:
```yaml
    MessageOut:
      type: object
      properties:
        id: {type: string, format: uuid}
        role: {type: string, enum: [user, assistant, system, tool]}
        content: {type: object, additionalProperties: true}
        citations:
          type: array
          nullable: true
          maxItems: 50
          items: { $ref: "#/components/schemas/Citation" }
          description: "Sources the assistant answered from, stored with the message (gap th-1) and equal to the `citations` of that turn's `final` frame. Always present. `null` = none recorded (user messages, messages saved before this field, agents that emit none, cut-off replies, workflow step outputs). `[]` = the agent stated there are no sources. A snapshot taken at answer time: a later rename or delete of the file does not change it."
        token_count: {type: integer, nullable: true}
        seq: {type: integer}
        created_at: {type: string, format: date-time, description: "UTC ISO-8601, trailing Z (DAT-06)", pattern: "Z$"}
    Citation:
      type: object
      required: [document_id, file_name, page, chunk_id, rank]
      description: "One retrieved source, in the shape `rag_agent` puts on `final` (03 §3.1). `file_name`/`page` are always present and `null` when the indexed chunk carried none."
      properties:
        document_id: {type: string, format: uuid}
        file_name: {type: string, nullable: true, maxLength: 255}
        page: {type: integer, nullable: true, minimum: 1}
        chunk_id: {type: string, format: uuid}
        rank: {type: integer, minimum: 1, description: "One-based retrieval rank, as emitted; never re-ordered."}
```
- `AgentInvokeOut` لا يتغير نصّه، لأنه يشير إلى `MessageOut`.
- لا يُضاف `required` إلى `MessageOut`، لأن المخطط القائم بلا قائمة `required` (أصغر فرق). وحضور المفتاح الدائم مذكور في `description`، ومُثبت باختبار على السلك (AC‑2.3).
- **العميل والأنواع:** لا واجهة في هذا المستودع. يقرأ Android `openapi.yaml`، أو `/openapi.json` الحي، ويحدّث محوّله. تحديث `backend-reply.md` في مستودع Android يأتي بعد الدمج وبقرار المالك (س‑9).

### التواقيع

#### 3.1 domain — `src/app/modules/conversations/domain/value_objects.py`
```python
# ث-1 — at most this many citations on one message; more is malformed (stored NULL).
MAX_CITATIONS = 50
_MAX_CITED_FILE_NAME_LEN = 255  # the files module's own bound (files/domain/value_objects.py:22)
# Canonical 8-4-4-4-12 text. Matched, never normalised: the stored string must
# equal the one on the `final` frame (AC-2.1 is a deep equality).
_UUID_TEXT_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass(frozen=True, slots=True)
class Citation:
    """One source an assistant reply was answered from (ث-1, 06 §4).

    A SNAPSHOT taken at answer time (ق-3): `file_name` is the name the
    chunk carried then, and is never refreshed from `files`. Values are
    stored verbatim -- nothing is stripped, lower-cased or re-ranked.
    """

    document_id: str
    file_name: str | None
    page: int | None
    chunk_id: str
    rank: int

    def __post_init__(self) -> None:
        # raises InvalidConversationInput when:
        #  - document_id / chunk_id is not a `str` matching _UUID_TEXT_RE
        #  - file_name is not None and (not a `str`, empty, or > 255 chars)
        #  - page is not None and (type is not exactly int, or page < 1)
        #  - rank's type is not exactly int, or rank < 1
        # "type is exactly int" rejects `bool` (True is an int in Python).
        ...
```

#### 3.2 domain — `src/app/modules/conversations/domain/entities.py`
```python
@dataclass(slots=True)
class Message:
    id: str
    conversation_id: str
    workspace_id: str
    role: MessageRole
    content: MessageContent
    token_count: int | None
    seq: int
    created_at: datetime
    deleted_at: datetime | None
    # ث-1 — LAST and defaulted, so every existing constructor call stays valid.
    # None = not recorded; () = the agent stated there are no sources.
    citations: tuple[Citation, ...] | None = None


class Conversation:
    def append_message(
        self,
        message_id: str,
        role: MessageRole,
        content: MessageContent,
        token_count: int | None,
        now: datetime,
        *,
        citations: tuple[Citation, ...] | None = None,
    ) -> Message:
        """... INV-CV4: citations are carried by ASSISTANT messages only, and
        at most MAX_CITATIONS of them; otherwise InvalidConversationInput."""
```

#### 3.3 port — `src/app/modules/conversations/ports/inbound.py`
```python
@dataclass(frozen=True, slots=True)
class CitationRef:
    """One citation as it crosses the port (ث-1) -- plain fields, no rules.

    A handle, for `AppendedMessage`'s reason: the caller should not import
    this module's domain. The rules live on `domain.Citation`, and the
    module applies them in `AppendMessage`.
    """

    document_id: str
    file_name: str | None
    page: int | None
    chunk_id: str
    rank: int


@dataclass(frozen=True, slots=True)
class AppendedMessage:
    id: Uuid
    conversation_id: Uuid
    role: str
    text: str
    attachments: tuple[str, ...]
    token_count: int | None
    seq: int
    created_at: datetime
    # ث-1 — what was STORED, which can be None even when citations were sent
    # (the module dropped them as malformed). Last and defaulted.
    citations: tuple[CitationRef, ...] | None = None


class ConversationThreads(Protocol):
    async def append(
        self,
        ctx: ExecutionContext,
        conversation_id: Uuid,
        *,
        role: str,
        text: str,
        attachments: tuple[str, ...] = (),
        token_count: int | None = None,
        citations: tuple[CitationRef, ...] | None = None,
    ) -> AppendedMessage: ...
```

#### 3.4 application — `src/app/modules/conversations/application/use_cases.py`
```python
class AppendMessage:
    async def execute(
        self,
        ctx: ExecutionContext,
        conversation_id: Uuid,
        *,
        role: str,
        text: str,
        attachments: tuple[str, ...] = (),
        token_count: int | None = None,
        citations: Sequence[CitationRef] | None = None,
    ) -> tuple[Message, tuple[ConversationEvent, ...]]: ...


def _recordable_citations(
    role: MessageRole, refs: Sequence[CitationRef] | None
) -> tuple[Citation, ...] | None:
    """ث-1 / FR-8 — the citations this message will store.

    - refs is None                      -> None
    - role is not ASSISTANT             -> raise ValidationError (a caller bug, loud: FR-7)
    - len(refs) > MAX_CITATIONS         -> None   (dropped, the append goes on)
    - any Citation(...) raises          -> None   (dropped, the append goes on)
    - otherwise                         -> tuple in the SAME order, values verbatim

    Dropping, not raising, is deliberate and is the ONLY lenient branch in
    this use case: the text is the turn, and the citations annotate it. A
    malformed annotation must never cost the user the answer (FR-8, NFR-6).
    It runs BEFORE any I/O, like MessageContent's validation.
    """


class ConversationService:
    async def append(
        self,
        ctx: ExecutionContext,
        conversation_id: Uuid,
        *,
        role: str,
        text: str,
        attachments: tuple[str, ...] = (),
        token_count: int | None = None,
        citations: tuple[CitationRef, ...] | None = None,
    ) -> AppendedMessage:
        # passes citations to AppendMessage, then maps message.citations
        # (domain Citation) back onto CitationRef for AppendedMessage.citations.
        ...
```
- لا تسجيل (logging) في طبقة application، كما هو اليوم. التحذير يصدره المنسّق (§3.5).
- `_recordable_citations` يُستدعى بعد التحقق من `role`، وقبل `self._conversations.get(...)`.

#### 3.5 agents — `src/app/agents/orchestrator.py`
```python
from app.modules.conversations.ports.inbound import (
    AppendedMessage,
    CitationRef,
    ConversationThreads,
)

_CITATIONS_KEY = "citations"
_CITATION_KEYS = ("document_id", "file_name", "page", "chunk_id", "rank")


@dataclass(frozen=True, slots=True)
class _DeclaredCitations:
    """What a `final` frame says about its sources, JSON-shape-checked (ث-1)."""

    refs: tuple[CitationRef, ...] | None  # None = nothing to record
    malformed: bool = False               # True => refs is None and a warning is owed
    count: int = 0                        # len of the declared list; 0 when not a list


def _declared_citations(data: Json) -> _DeclaredCitations:
    """Defensive in `_pending_options`' shape and for its reason: what arrives
    is an agent's JSON, not a typed value.

    - key absent, or value None          -> refs=None, malformed=False   (AC-1.4)
    - value not a list                   -> refs=None, malformed=True    (AC-1.6)
    - any item not a dict, missing one of _CITATION_KEYS, or with a JSON
      type that does not fit CitationRef (str / str|None / int|None / str /
      int; bool is NOT an int here)      -> refs=None, malformed=True    (AC-1.6)
    - otherwise                          -> one CitationRef per item, SAME
      order, extra keys dropped (AC-1.7); [] stays () (AC-1.3)

    VALUES (UUID form, rank >= 1, length, count <= 50) are NOT checked here:
    they are the module's rules (domain.Citation / MAX_CITATIONS).
    """
```
**تغيير `_persist_reply`** (لا يتغير غيره في مسار `final`):
```python
declared = _declared_citations(event.data)
if declared.malformed:
    _logger.warning(
        "orchestrator.citations_not_recorded",
        extra={"conversation_id": record.conversation_id, "count": declared.count,
               "reason": "shape"},
    )
message = await threads.append(
    ctx, record.conversation_id, role=_ROLE_ASSISTANT,
    text=_with_fallback_notice(text, record.fallback), attachments=attachments,
    token_count=completion or None,
    citations=declared.refs,
)
# the module dropped what was sent (a value rule, or more than MAX_CITATIONS)
if declared.refs is not None and message.citations is None:
    _logger.warning(
        "orchestrator.citations_not_recorded",
        extra={"conversation_id": record.conversation_id, "count": len(declared.refs),
               "reason": "rejected"},
    )
```
- **تحذير واحد على الأكثر في كل دور.** الحالتان متنافيتان: `malformed` يعني `refs=None`. ولا يحمل التحذير إلا العدد واسم سبب ثابت (`shape` أو `rejected`)، بلا `file_name` ولا نص (NFR‑8، ق‑5).
- **إطار `final` لا يتغير** (FR‑10). يبقى `citations` عليه كما أصدره الوكيل، و`content` المضاف يبقى `{text, attachments}`. أما `rag_agent` فيصدر المفاتيح الخمسة بقيم UUID، فتتساوى القيمتان المحفوظة والمبثوثة تماماً (AC‑2.1، AC‑3.3). ولا تختلفان إلا لوكيل يصدر قيمة تالفة أو مفاتيح زائدة، وذلك مقصود.
- **`_persist_partial`، و`_persist_step_output`، ورسائل المستخدم** (`orchestrator.py:1479`، `:1567`، `:1632`، `:2100`) لا تمرر `citations`، فتبقى القيمة الافتراضية `None` (AC‑1.5، A‑4، A‑6). لا تعديل عليها.
- **مسار `fallback`** (AC‑1.8): المفتاح يُضاف إلى `event.data` قبل `_persist_reply`، ولا يمس `citations`. لا تعديل.
- **قطع الاتصال بعد `final`:** الحفظ يسبق `yield event` (`orchestrator.py:1973-1974`). لا تعديل.

**تحديث التعليقات بصدق** (AC‑4.3، ت‑2). هذه نصوص تعليقات وdocstrings، لا سلوك:
- **`orchestrator.py:348-355` (ب‑10):** حين قُرر ب‑10، كانت الرسالة بلا نصف مُهيكَل. ث‑1 أعطاها واحداً: `messages.citations`، عموداً مستقلاً، لا مفتاحاً في `content`. فصار جزء «عمود وترحيل» من الكلفة سابقة مدفوعة لا كلفة جديدة. ويبقى جزء «قاعدة عرض في كل عميل» قائماً. وتحويل علامة الانقطاع إلى حقل هو ث‑2، ولا يُقرر هنا. الجملة باقية.
- **`orchestrator.py:362-370` (جملة التحول للنموذج المحلي):** التعليل نفسه. الجملة في رأس الرد باقية، وث‑1 لا يغيرها (خارج النطاق).
- **docstring `_turn_content` (`orchestrator.py:417-432`):** يبقى صحيحاً أن الرسالة بلا حقل `data` مُهيكَل **عام**. لكن صار لها حقل مُهيكَل **واحد** لشكل معروف واحد، هو `citations` (ث‑1). ويبقى ردّ وكيل الوسائط الخالي من النص يُسلسَل JSON كما هو.
- **docstring `_persist_reply`:** يضاف سطر يقول إن `citations` تُحفظ الآن مع الرسالة، وتبقى أيضاً على الإطار.

#### 3.6 adapter — `src/app/modules/conversations/adapters/sql_repository.py`
```python
messages = Table(
    "messages", _metadata,
    ...,
    Column("deleted_at", _timestamptz, nullable=True),
    # `migrations/versions/conversations/0007_message_citations.py` (ث-1).
    # none_as_null=True is LOAD-BEARING: plain JSONB() binds Python None as the
    # JSON literal 'null', not SQL NULL (checked on SQLAlchemy 2.0.51), and
    # "not recorded" must have exactly one spelling in the table: SQL NULL.
    Column("citations", JSONB(none_as_null=True), nullable=True),
    schema="conversations",
)

def _citations_to_json(citations: tuple[Citation, ...] | None) -> list[Json] | None:
    """None -> None (SQL NULL); () -> [] ; else the five keys per item, in order."""

def _citations_from_json(raw: object) -> tuple[Citation, ...] | None:
    """NULL -> None; a list -> tuple(Citation(...)) re-validated, like
    `_hydrate_message` rebuilds MessageContent rather than trusting it."""
```
- **`append_message`:** يضيف `citations=_citations_to_json(message.citations)` إلى `insert(messages).values(...)` نفسها. ولا عبارة جديدة: تبقى `INSERT` على `messages` ثم `UPDATE` على `conversations` (version)، في المعاملة نفسها، ولا `UPDATE` على `messages` (AC‑1.9).
- **`list_messages`:** لا تغيير في العبارة. `select(messages)` يُسقط كل أعمدة الـ `Table`، ومعها `citations`، في عبارة واحدة بلا ربط (AC‑2.7، NFR‑1).
- **`_hydrate_message`:** `citations=_citations_from_json(row["citations"])`.
- **`get_message` و`save_message`:** بلا تغيير. `save_message` يكتب `deleted_at` وحده، فالاستشهادات لا تتغير بعد الإنشاء (FR‑1).

#### 3.7 api — `src/app/api/v1/dto/conversations.py` و`routers/conversations.py`
```python
class Citation(BaseModel):
    """One stored source on a message (ث-1, 03 §2). The nested-object naming
    of `Usage` (no `Out` suffix): it only ever appears inside `MessageOut`."""

    document_id: str
    file_name: str | None
    page: int | None
    chunk_id: str
    rank: int


class MessageOut(BaseModel):
    id: str
    role: str
    content: dict[str, Any]
    citations: list[Citation] | None   # required-present, no default: the key is always on the wire
    token_count: int | None
    seq: int
    created_at: datetime
```
```python
# routers/conversations.py
def _citations_out(citations: Sequence[object] | None) -> list[Citation] | None:
    # Works for both sources -- domain `Citation` (reads) and port `CitationRef`
    # (the orchestrator's handle) -- because both carry the same five attributes.
    return None if citations is None else [
        Citation.model_validate(c, from_attributes=True) for c in citations
    ]

# _to_message_out:          citations=_citations_out(message.citations)
# appended_to_message_out:  citations=_citations_out(message.citations)
```
- `content` في المحوّلين يبقى `{"text": …, "attachments": […]}` حرفياً (AC‑2.6).
- العربية: Starlette `JSONResponse` يسلسل بـ `ensure_ascii=False`، وكذلك `sse.py:76`، فلا escape (AC‑2.4).

## 4. البيانات

**الترحيل:** `migrations/versions/conversations/0007_message_citations.py`
- `revision = "0007_message_citations"` (22 حرفاً، أقل من حد `alembic_version.version_num` البالغ 32؛ يفحصه `test_migration_revision_ids.py`).
- `down_revision = "0006_space_count_covering"` (رأس السلسلة الحالي).

```python
def upgrade() -> None:
    op.execute("ALTER TABLE conversations.messages ADD COLUMN citations jsonb NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE conversations.messages DROP COLUMN IF EXISTS citations;")
```
**ما يقوله docstring الترحيل** (على نمط `0005`):
- **الغرض:** ث‑1.
- **لماذا عمود لا مفتاح في `content`:** وُعد Android بأن `content` مغلق (ق‑2، ت‑4).
- **`jsonb`:** قائمة مرتبة يجب أن تنجو من الذهاب والإياب.
- **`NULL` = «لم يُسجَّل»:** هذا ما تعنيه كل الصفوف القديمة، فلا backfill ولا `DEFAULT`.
- **بلا فهرس:** العمود إسقاط لا شرط.
- **بلا CHECK:** القواعد في `domain.Citation`. وCHECK على `jsonb` يُعيد كتابة نصف تلك القواعد بلغة ثانية.
- **البيانات المنسوخة:** `file_name` لقطة من وحدة أخرى (ق‑3).

**`01-data-model.md` §2.4.** سطر جديد في `CREATE TABLE conversations.messages` بعد `deleted_at`:
```sql
  citations       jsonb NULL,                       -- ث‑1 · لقطة مصادر جواب المساعد {document_id, file_name, page, chunk_id, rank} بترتيبها؛ NULL ⇒ لم يُسجَّل، '[]' ⇒ صرّح الوكيل بلا مصادر
```
وتعليق تحته يذكر:
- مكان التخزين (عمود لا مفتاح في `content`)؛
- السقف 50؛
- أن `content` يبقى نصاً ومرفقات فقط؛
- أن فرضية «`MessageContent` بلا نصفٍ مُهيكَل» صارت «الرسالة لها جزء مُهيكَل واحد: `citations`»، مع الإشارة إلى ب‑10.

وفي تعليق `pending_clarification`، تُستبدل عبارة «`MessageContent` بلا نصفٍ مُهيكَل» بعبارة تبقي التعليل صحيحاً: «`content` نصٌّ ومرفقات فقط، والجزء المُهيكَل الوحيد للرسالة (`citations`، ث‑1) لا يحمل المرشّحين».

**الخصائص:**
- **الفهارس:** لا شيء.
- **القيود:** لا شيء جديد.
- **RLS:** بلا تغيير. سياسة `tenant_isolation` و`FORCE RLS` على مستوى الصف تغطي العمود.
- **الأدوار والصلاحيات:** بلا تغيير، كلها على مستوى الجدول (§2).
- **متوافق مع الإصدار السابق:** نعم.
  - الشيفرة القديمة على قاعدة مُرحَّلة تُدرج بلا العمود، فيصبح `NULL`، وتقرأ بـ `SELECT` صريح الأعمدة فتتجاهله.
  - الشيفرة الجديدة تتطلب العمود، لأن `select(messages)` و`insert` يسمّيانه. لذلك يجب أن يسبق `migrate` (`python -m app.ops.provision`) تشغيل الصورة الجديدة، وهو الترتيب القائم في `docker-compose.yml` و`deploy/rolling-deploy.sh`.
- **قفل الترحيل:** `ADD COLUMN` بلا `DEFAULT` تعديل في الـ catalog فقط. يأخذ `ACCESS EXCLUSIVE` لحظياً تحت `lock_timeout` الذي يضبطه `provision`. ويمر بقاعدة expand/contract في `test_zero_downtime_migrations.py` (لا `NOT NULL` بلا `DEFAULT`، ولا فهرس حاجب).
- **التراجع:** `downgrade` يُسقط العمود، فتضيع الاستشهادات المسجلة منذ الترقية، ولا يُمس النص. ويجب أولاً إرجاع الصورة إلى ما قبل ث‑1، ثم تشغيل التراجع عبر الأداة المعتمدة. `alembic downgrade` المباشر ممنوع مثل `upgrade` (`docs/stack-commands.md`).
- **الحجم:** نحو 170 بايت للاستشهاد، أي قرابة 1 KB لجواب RAG بالـ `k` الافتراضي (5). وأسوأ حالة 50 × ~430 بايت ≈ 21 KB، وتُضغط في TOAST فوق 2 KB.

## 5. خطة الاختبار المقترحة

**المستويات:**
- **unit:** تحت `tests/unit/`، وتُشغَّل محلياً: `pytest tests/unit tests/architecture tests/eval`.
- **integration:** تحت `tests/integration/` (`live_db`)، وتُكتب الآن وتُشغَّل في CI job `integration` فقط (ق‑4).
- **مراجعة:** فحص يدوي يقوم به code-reviewer.

| AC | المستوى | المكان | ما يُثبت |
|---|---|---|---|
| AC‑1.1 | unit + integration | `test_orchestrator.py`: fake يُصدر `final` باستشهادين (UUID حقيقية) → `threads.citations[-1]` يساويهما بالترتيب. `test_conversations_use_cases.py`: `ConversationService.append` يعيدهما في `AppendedMessage`. `test_conversations_repository_rls.py`: إدراج ثم `list_messages` يعيدهما | الحفظ في `append` نفسه، والذهاب والإياب على Postgres |
| AC‑1.2 | unit + integration | `test_conversations_domain.py` (`Citation` يقبل `None` لـ `file_name`/`page`). `test_conversations_sql_statements.py` (`_citations_to_json` يُبقي المفتاحين بقيمة `null`). RLS live | المفتاحان حاضران |
| AC‑1.3 | unit + integration | `test_orchestrator.py` (`citations: []` → `()` لا `None`). `test_conversations_sql_statements.py` (`()` → `[]`، و`None` → SQL NULL). RLS live (`[]` يعود `[]`) | التمييز بين `[]` و`null` |
| AC‑1.4 | unit | `test_orchestrator.py`: وكيل بلا المفتاح (`_JobAgent` القائم) → `None` | |
| AC‑1.5 | unit | `test_api_conversations_router.py`: `POST` بـ `content.citations` ثم `GET` → رسالة المستخدم `citations: null`، و`content == {text, attachments}`. `test_conversations_use_cases.py`: `citations` على `role="user"` → `ValidationError` | FR‑7 |
| AC‑1.6 | unit | `test_orchestrator.py`، ثلاث حالات مُعلمة (`"x"`، عنصر بلا `chunk_id`، `rank: "1"`): `final` يصل، والنص محفوظ، و`citations is None`، و`caplog` فيه سجل واحد `orchestrator.citations_not_recorded` بـ `count` و`reason`، ولا يظهر في السجل `file_name` ولا النص | FR‑8، NFR‑8 |
| AC‑1.7 | unit | `test_orchestrator.py`: عنصر فيه `score` → `CitationRef` بخمسة حقول. `test_conversations_sql_statements.py`: JSON المكتوب بخمسة مفاتيح فقط | |
| AC‑1.8 | unit | `test_orchestrator.py` (مسار `_Fallback` القائم): الاستشهادات محفوظة، والنص يبدأ بـ `_FALLBACK_NOTICE_*` | FR‑9 |
| AC‑1.9 | unit | `test_conversations_sql_statements.py` (جديد): `tenant_session` مزيّف يسجل العبارات. `append_message` يُنفذ `INSERT INTO conversations.messages` فيه `citations`، ثم `UPDATE conversations.conversations` فقط، ولا `UPDATE conversations.messages` | FR‑1 |
| AC‑1.10 | unit | `test_conversations_use_cases.py`: 51 مرجعاً → رسالة محفوظة بـ `citations=None`. `test_orchestrator.py`: fake يعيد `citations=None` لما أُرسل → تحذير `reason="rejected"` واحد. وحالة الحد: 50 تُحفظ | NFR‑2 |
| حواف US‑1 | unit | `test_orchestrator.py`: مهلة أو `error` بعد tokens → `_persist_partial` بـ `citations=None`. وقطع بعد `final` (`aclose()` بعد استلامه) → الاستشهادات محفوظة. وفشل `append` → `final` بلا `message_id`، والدور ليس `error` | A‑6، NFR‑6 |
| حافة 255 حرفاً | unit | `test_conversations_domain.py`: 255 تُقبل، و256 تُرفض. وعبر الطبقات، 256 → `None` | |
| AC‑2.1 | unit | `test_api_conversations_router.py` بوكيل `_CitingAgent` يصدر استشهادين: SSE ثم `GET` → الرسالة التي `id == final.message_id` فيها `citations == final.citations` (مقارنة عميقة) | معيار Android 6 |
| AC‑2.2 | unit + integration | `test_conversations_sql_statements.py`: `_hydrate_message` على صف بلا `citations` (`None`) → `Message.citations is None`. RLS live: صف يُدرَج بـ SQL خام بلا العمود → `list_messages` يعيده بـ `None`. وعلى السلك: `citations: null` و200 | NFR‑3 |
| AC‑2.3 | unit | `test_api_conversations_router.py`: رسالة `user` في `GET` فيها المفتاح `citations` بقيمة `null` (`"citations" in m`) | |
| AC‑2.4 | unit + integration | `test_api_conversations_router.py`: `file_name = "دليل الصيانة ٢٠٢٦.pdf"` → `response.content` يحتوي بايتات UTF‑8 للاسم، ولا `\u`. RLS live: الذهاب والإياب عبر `jsonb` يعيد الاسم مطابقاً | NFR‑7 |
| AC‑2.5 | unit | `test_api_conversations_router.py`: ثلاثة أدوار بثلاث قوائم، تُقرأ بـ `limit=1` حتى `next_cursor: null` → كل رسالة بقائمتها. (وتمديد `test_list_messages_pages_by_seq_cursor` الحي اختياري) | |
| AC‑2.6 | unit | `test_api_conversations_router.py` و`test_api_agents_router.py`: `set(content) == {"text", "attachments"}` لكل رسالة. والتأكيدات القائمة (`:487`، `:493-494`، `agents :374`) تبقى خضراء بلا تعديل | ق‑2 |
| AC‑2.7 | unit | `test_conversations_sql_statements.py`: `list_messages` ينفذ عبارة واحدة، وFROM فيها `conversations.messages` وحده، بلا `files.` ولا `knowledge.` | NFR‑1 |
| AC‑2.8 | unit + integration | `test_api_conversations_router.py`: `DELETE …/messages/{id}` لرسالة المساعد ثم `GET` → غائبة. واختبار RLS الحي القائم `test_a_soft_deleted_message_is_still_readable_but_no_longer_listed` يغطي الحذف | INV‑CV3 |
| AC‑3.1 | unit | `test_api_conversations_router.py`: `stream: false` → `reply["citations"]` يساوي ما في `GET` للـ `id` نفسه | FR‑5 |
| AC‑3.2 | unit | `test_api_agents_router.py`: `invoke` بـ `stream: false` لوكيل يصدر استشهادات → `message.citations` يساوي `GET` | FR‑5 |
| AC‑3.3 | unit | `test_api_conversations_router.py`: SSE → `final.citations` بشكله الحالي، ويساوي `GET`. واختبار `test_orchestrator.py:559` القائم يبقى أخضر | FR‑10 |
| AC‑3.4 | unit | `test_api_conversations_router.py` (`_EchoAgent` القائم بلا استشهادات) → `reply["citations"] is None` | |
| AC‑3.5 | unit | `test_api_conversations_router.py`: وكيل يصدر `citations: []` → `reply["citations"] == []` | |
| AC‑4.1 | unit (contract) | `test_api_conventions.py`، اختبار جديد على نمط `test_the_contract_publishes_every_field_of_the_job_a_client_polls`: خصائص `MessageOut` و`Citation` في `SPEC` و`LIVE` متساوية، و`citations` حاضر، و`SPEC.Citation.required` بالمفاتيح الخمسة، و`document_id`/`chunk_id` بـ `format: uuid`. والاختبارات القائمة في الملف خضراء | |
| AC‑4.2 | مراجعة | `03-api-spec.md` §2 و§3.1 | |
| AC‑4.3 | مراجعة | `06-domain-models.md` §4 و`01-data-model.md` §2.4 وتعليقات `orchestrator.py` | ت‑2 |
| AC‑4.4 | unit + integration | `test_conversations_schema_parity.py`: (أ) كل عمود في `messages` Table يُنشئه `CREATE TABLE conversations.messages` في `0001` أو `ALTER TABLE conversations.messages ADD COLUMN`. (ب) العكس. (ج) `ADD COLUMN citations jsonb NULL` في السلسلة، و`citations` مذكور في `01-data-model.md`. (د) العمود `nullable`، و`server_default is None`، و`type.none_as_null is True`. (هـ) `downgrade` في `0007` يحوي `DROP COLUMN IF EXISTS citations`. والتطبيق الفعلي عبر `provision` في CI `integration` | |
| AC‑4.5 | مراجعة | `gaps.md` §ث‑1 | |
| AC‑5.1 | unit | `test_api_conversations_router.py`: محادثة غير معروفة للمساحة → 404 problem+json، ولا `citations` في الجسم | |
| AC‑5.2 | integration (CI) | `test_conversations_repository_rls.py`، اختبار جديد: إدراج رسالة باستشهادات في A، ثم بـ `app.workspace_id = B`: `list_messages` صفر صفوف، و`SELECT citations FROM conversations.messages WHERE id = :id` صفر صفوف. وحالة SQL NULL: `SELECT citations IS NULL` لرسالة user → `true` (يثبت `none_as_null`) | NFR‑4 |
| AC‑5.3 | unit | `test_api_conversations_router.py`: رمز بلا `conversations:read` → 403. (إن وُجد اختبار قائم مكافئ، تُضاف إليه فقط تأكيد غياب البيانات) | |
| AC‑5.4 | unit | `test_api_conversations_router.py`: بلا رمز، وبرمز سيئ → 401 | |
| AC‑5.5 | architecture | `tests/architecture/test_import_contracts.py` + البوابة 4 (`lint-imports`)، بلا `ignore_imports` جديد. المنسّق يستورد `ports.inbound` فقط، كما اليوم | NFR‑5 |

**تعديلات لازمة على fakes قائمة:**
- **`test_orchestrator.py:1274` و`test_workflow_orchestration.py:191`:** يقبل الـ fake `append` المعامل `citations: tuple[CitationRef, ...] | None = None`، ويعيده في `AppendedMessage(citations=...)`. ويسجله في قائمة **منفصلة** `self.citations`. لا يُمد الـ tuple القائم `self.appended`، حتى لا تنكسر التأكيدات القائمة عليه.
- **لا يتغير** `support_conversations.py` (`InMemoryConversationRepository` يخزن `Message` كما هو)، ولا `AppendedMessage(...)` في الاختبارات، لأن للحقل الجديد قيمة افتراضية.

## 6. ترتيب التنفيذ والتوازي

1. **بالتوازي:**
   - **(أ) data-engineer:**
     - الترحيل `0007_message_citations.py`؛
     - و`01-data-model.md` §2.4؛
     - وتمديد `test_conversations_schema_parity.py`. لن يخضر قبل الخطوة 2‑هـ، لأنه يقرأ `messages` Table من المحوّل.
   - **(ب) backend-developer:** الخطوة 2 بالترتيب.
2. **backend-developer:**
   - (أ) domain: `Citation`، `MAX_CITATIONS`، `Message.citations`، `append_message` + INV‑CV4، واختبارات domain.
   - (ب) port: `CitationRef`، و`AppendedMessage.citations`، و`ConversationThreads.append`.
   - (ج) application: `AppendMessage` و`_recordable_citations` و`ConversationService.append`، واختبارات use cases.
   - (د) orchestrator: `_declared_citations` و`_persist_reply` والتعليقات. وتحديث fakes في `test_orchestrator.py`/`test_workflow_orchestration.py`، واختبارات US‑1.
   - (هـ) adapter: العمود (`none_as_null=True`) و`_citations_to_json`/`_citations_from_json`، و`test_conversations_sql_statements.py`. يتفق مع 1‑أ على اسم العمود `citations` ونوعه.
   - (و) api: DTO `Citation` و`MessageOut.citations` والمحوّلان، واختبارات routers.
   - (ز) العقد والوثائق: `openapi.yaml` واختبار `test_api_conventions.py` الجديد، ثم `03-api-spec.md` و`06-domain-models.md`.
3. **data-engineer**، بعد 2‑هـ: تمديد `test_conversations_repository_rls.py`. يُكتب، ولا يُشغَّل محلياً (ق‑4).
4. **البوابات 1–5 محلياً** (`pytest tests/unit tests/architecture tests/eval`)، ثم `deploy/prometheus/test-rules.sh` (لا قاعدة تنبيه جديدة، لكنه جزء من البوابات).
5. **backend-developer:** فقرة «الإصلاح» في `gaps.md` §ث‑1.
6. **CI job `integration`** على الـ PR يُثبت AC‑4.4 (التطبيق عبر `provision`) وAC‑5.2 والذهاب والإياب الحي.

**المراحل التي تُتخطى:** UX (3)، والواجهة (7)، وstaging (13)، وبوابة الإنتاج (14)، كما في `status.md`. ومرحلة البيانات (5) **لازمة** (ترحيل).

## 7. المخاطر والقرارات

**لا بنود ⚠️ جديدة.** كل ما يمس التخزين والعقد والخصوصية والسقف حسمته بوابة ① (ق‑1…ق‑4 والافتراضات المعتمدة). ما يلي قرارات تصميم داخل تلك الحدود، ومخاطر مُدارة.

**قرارات التصميم (بلا حاجة لقرار بشري):**
- **د‑ت1 — قواعد القيم في مكان واحد (`domain.Citation`)، والإسقاط الليّن في مكان واحد (`AppendMessage`).**
  - المنسّق يفحص أنواع JSON فقط، لأنه يحتاجها ليبني `CitationRef` مُنمَّطاً تحت `mypy --strict`.
  - البديل المرفوض الأول: تكرار القواعد في المنسّق، وفيه خطر انجراف بين نسختين.
  - البديل المرفوض الثاني: أن يرفع `AppendMessage` الخطأ، فيفشل الإدراج كله ويضيع النص (FR‑8).
  - يعرف المنسّق أن الوحدة أسقطت الاستشهادات بمقارنة ما أرسل بما عاد، فيصدر التحذير هو، ويبقى application بلا logging كما هو اليوم.
- **د‑ت2 — التحقق من صيغة UUID لـ `document_id` و`chunk_id`.**
  - العقد المنشور يعد بـ `format: uuid`.
  - المنتِج يصدر UUID دائماً: المستند UUIDv7، والمقطع uuid5 (`knowledge/domain/collections.py:72`).
  - تُطابَق القيمة ولا تُطبَّع، لتبقى مساوية للإطار.
  - الأثر: قيمة غير UUID تجعل القائمة كلها `null`، وهذا داخل «التالف = null» المعتمد.
  - ملاحظة: fixtures قائمة في `test_orchestrator.py:559` تستعمل `doc-1`/`chunk-a`. تبقى خضراء لأنها تفحص الإطار فقط، والاختبارات الجديدة تستعمل `new_uuid7()`.
- **د‑ت3 — `page ≥ 1` و`rank ≥ 1`، و`file_name` من 1 إلى 255 حرفاً.**
  - المحللات تصدر الصفحات من 1 (`pdf_text.py:275`، `image_ocr.py:463`).
  - حد 255 هو حد وحدة `files` نفسها.
- **د‑ت4 — إطار `final` لا يُعاد كتابته.**
  - المحفوظ قد يختلف عن المبثوث في حالتين فقط: وكيل يصدر مفاتيح زائدة (تُحذف من المحفوظ)، أو قيماً تالفة (المحفوظ `null`). ولا يقع أي منهما لـ `rag_agent` اليوم.
  - مواءمة الإطار مع المحفوظ تخالف FR‑10.
- **د‑ت5 — `JSONB(none_as_null=True)` على العمود الجديد.**
  - بدونه، تُكتب `None` على أنها JSON `'null'` لا SQL NULL. هذا مُتحقق منه محلياً على SQLAlchemy 2.0.51.
  - تثبته parity unit (`type.none_as_null`) وRLS live (`IS NULL`).
- **د‑ت6 — اسم DTO `Citation` بلا لاحقة `Out`.** على سابقة `Usage`، ولأن AC‑4.1 يسمي المخطط `Citation`. ولا تصادم مع أي نموذج Pydantic آخر في `src/app` (فُحص).
- **د‑ت7 — لا `required` جديد على `MessageOut` في `openapi.yaml`.** أصغر فرق. الحضور الدائم موصوف في `description`، ومُثبت على السلك.

**المخاطر المُدارة:**
- **ترتيب النشر:** صورة ث‑1 على قاعدة غير مُرحَّلة تُفشل كل إدراج لرسالة مساعد، وكل قراءة للسجل (`UndefinedColumn`). وهذا لا يمس بث `final`، لأن فشل الحفظ يتدهور إلى تحذير. التخفيف: `migrate` يسبق `app` في الترتيب القائم. ولا نشر تلقائياً (CLAUDE.md).
- **حجم الجدول:** نحو 1 KB لكل جواب RAG. على بذرة السعة (مليون رسالة، نحو نصفها للمساعد، إن كانت كلها RAG) يكون ذلك نحو 0.5 GB إضافية في الأسوأ، ونحو 50 KB إضافية لكل صفحة `list_messages` من 50 رسالة. هذا مقبول ضمن قرار ق‑2 والسقف 50، ولا يغيّر خطة الاستعلام (`explain_hot_paths.py` يقرأ `SELECT *` نفسه).
- **بيانات منسوخة من وحدة أخرى** (ت‑3): `file_name` يبقى بعد حذف الملف، حتى تُحذف الرسالة أو المحادثة أو تُطهَّر المساحة (`purge_space` يحذف الصفوف كلها). حُسم في ق‑3. ويُبلَّغ security-reviewer به للتوثيق فقط.
- **الاختبارات الحية لا تُشغَّل محلياً** (ق‑4): AC‑4.4 (التطبيق) وAC‑5.2 يثبتهما CI `integration` قبل بوابة ②.
- **ملاحظة خارج النطاق:** نتيجة د‑ت5 تعني أن `pending_clarification` (`JSONB()` بلا `none_as_null`) يكتب على الأرجح JSON `'null'` لا SQL NULL، خلافاً لما يقوله تعليقه في `01 §2.4`. القراءة لا تتأثر (`row[...] or ()`). لا يُصلح هنا، ويُقترح بنداً منفصلاً.

</div>
