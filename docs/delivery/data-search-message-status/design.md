<div dir="rtl">

# التصميم التقني: ث‑2 حقل حالة الرسالة

- **المدخلات الملزمة:**
  - `requirements.md` و`stories.md` في هذا المجلد.
  - قرارات بوابة ① في `status.md` (2026‑11‑10): ق‑1…ق‑4، وتوصيات المحلل في س‑4…س‑12.
- **الشيفرة التي فُحصت:** `master` على `0976bcb`. أرقام الأسطر أدناه منه.
- **المرجع الأقرب الذي نتبعه:**
  - **ث‑1** (`docs/delivery/data-search-android/design.md`). منه نأخذ:
    - عموداً مستقلاً على `conversations.messages` بترتيب `conversations/0007_message_citations.py`؛
    - و`JSONB(none_as_null=True)`؛
    - وحقلاً أخيراً بقيمة افتراضية على `Message` و`AppendedMessage`؛
    - ومعاملاً keyword على `ConversationThreads.append`؛
    - وحقلاً مستقلاً على `MessageOut` حاضر المفتاح دائماً؛
    - واختبار parity واختبار RLS الحي.
  - **`files/0006_file_tags.py`:** سابقة CHECK بـ `NOT VALID` على جدول قائم.
  - **`tests/integration/test_space_columns_live.py`:** سابقة اختبار حي يُنزل سلسلة ترحيل ثم يرفعها (`_move`).
- **دروس `retro.md` في ث‑1 التي يلتزم بها هذا التصميم:**
  - كل اختبار يُجمِّع SQL يسمّي لهجة `asyncpg` صراحة (`sqlalchemy.dialects.postgresql.asyncpg.dialect()`). CI يثبّت SQLAlchemy 2.1.x، والبيئة المحلية 2.0.51.
  - `downgrade` يُختبر هذه المرة. يُضاف اختبار حي ينزل الترحيل ويرفعه (§5، AC‑5.4).

## 1. الملخص

الميزة تمر بالطبقات الأربع نفسها التي مر بها ث‑1. لا تمس إلا وحدة `conversations`، والمنسّق، وعامل `knowledge` (موضعان)، وطبقة `api`.

1. **`app.agents.orchestrator`:** كل كاتب لرسالة مساعد يمرر الحالة **صراحة** في استدعاء `append` نفسه:
   - `_persist_reply` يمرر `complete`.
   - `_persist_partial` يمرر `partial`.
   - `_persist_step_output` يمرر `complete`.
   - المساران الأولان يمرران أيضاً `fallback`. يُبنى من `record.fallback` (الكائن `_Fallback`) بالشرط نفسه الذي يضع جملة التحول في النص (`fallback.switched`).
   - لا شيء يُستنتج من النص (FR‑3).
   - إطارات `final` و`error` و`notice` لا تتغير (FR‑9).
2. **`app.modules.conversations`:**
   - **domain:**
     - `MessageStatus` (StrEnum مغلق: `complete`، `partial`).
     - VO `MessageFallback` (أربعة نصوص).
     - حقلان جديدان على `Message`.
     - ثابت جديد INV‑CV5 في `Conversation.append_message`.
   - **ports:**
     - `FallbackRef`، مقبض مسطّح عبر الحد، بنمط `CitationRef`.
     - `AppendedMessage.status` و`.fallback`.
     - معاملان keyword على `append`.
   - **application:** `AppendMessage` يتحقق قبل أي I/O:
     - الحالة **صارمة**: قيمة غير معروفة، أو حالة على رسالة غير المساعد، ترفع `ValidationError` (AC‑1.13).
     - `fallback` **ليّن** في قيمه فقط: VO يرفض القيمة، فيُسقط إلى `None` ولا يُفشل الإدراج. وهذه قاعدة ث‑1 د‑ت1: «التعليق لا يكلّف الجواب».
   - **adapter:**
     - عمودان يُكتبان في `INSERT` نفسه ويُقرآن في `SELECT` نفسه.
     - `status text NULL`، وعليه CHECK بقيمتين.
     - `fallback jsonb NULL` بـ `none_as_null=True`.
3. **`app.workers.bootstrap`:** هذا **كاتب لم يذكره جدول `requirements.md` §5.1.** عاملا تسليم الملخص يكتبان رسالة مساعد عبر `AppendMessage` مباشرة:
   - `build_knowledge_summary_delivery_handler`، `bootstrap.py:1079`؛
   - و`build_knowledge_summary_failure_handler`، `bootstrap.py:1174`.

   كلاهما يمرر الآن `status="complete"` (د‑ت6).
4. **`app.api`:**
   - `MessageOut.status` و`MessageOut.fallback` بجانب `content` و`citations`.
   - تملؤهما المحوّلات نفسها التي ملأت `citations`: `_to_message_out` للسجل، و`appended_to_message_out` للمسارين غير المتدفقين.
   - `content` يبقى `{text, attachments}` بالضبط.
5. **البيانات:** ترحيل إضافي واحد `conversations/0008_message_status` يضيف العمودين والقيد:
   - التغيير metadata فقط، بلا backfill.
   - القيد `NOT VALID`، بلا مسح للجدول (د‑ت3).

**دلالة القيم (معتمدة في بوابة ①):**

| `status` | `fallback` | المعنى |
|---|---|---|
| `"complete"` | `null` | الدور وصل إلى `final`، والنموذج الأساسي أجاب. يشمل: سؤال التوضيح، والاعتذار الثابت، ومهمة وسائط أُدرجت، ومخرج خطوة workflow، وملخصاً سلّمه العامل (ناجحاً أو فاشلاً) |
| `"complete"` | `{from, to}` | الدور وصل إلى `final`، وأجاب النموذج المحلي (6.4) |
| `"partial"` | `null` أو `{from, to}` | الدور قُطع بعد نص (المهلة، أو `error`، أو استثناء المنسّق، أو التخلّي). `fallback` حسب من كان يجيب لحظة القطع |
| `null` | `null` | لم يُسجَّل: رسائل المستخدم، وكل صف قبل الترحيل، وصفوف `load_seed` |

- الدور الذي لم يبث شيئاً لا يكتب رسالة، فلا حالة له (ث‑3).
- `fallback` غير `null` يستلزم `status` غير `null` (INV‑CV5).
- **مصدر `fallback` هو المنسّق وحده.** وكيل يضع مفتاحاً اسمه `fallback` على `final` لا يجعل الرسالة «من النموذج المحلي» (د‑ت5، AC‑2.5).

## 2. الملفات

لا يعدّل منفّذان الملف نفسه.

| المسار | إنشاء/تعديل | المسؤولية | المنفّذ |
|---|---|---|---|
| `migrations/versions/conversations/0008_message_status.py` | إنشاء | `ADD COLUMN status text NULL`، و`ADD COLUMN fallback jsonb NULL`، وقيد `ck_messages_status` بـ `NOT VALID`. و`downgrade` يحذف الثلاثة. docstring بنمط `0007` و`files/0006` (§4) | data-engineer |
| `docs/design/01-data-model.md` (§2.4) | تعديل | سطران في `CREATE TABLE conversations.messages`، والقيد، وتعليق تحته. وتصحيح عبارة «جزءٌ مُهيكَل واحد: `citations`» في تعليق `citations` وفي تعليق `pending_clarification` (§4) | data-engineer |
| `tests/unit/test_conversations_schema_parity.py` | تعديل | تهجئة العمودين في الأماكن الثلاثة، و`nullable`، و`none_as_null` لـ `fallback`، والقيد في الترحيل، و`downgrade` | data-engineer |
| `tests/integration/test_conversations_repository_rls.py` | تعديل | ذهاب وإياب حي للحقلين، وصف قديم بـ SQL خام، والعزل، و`IS NULL`، ورفض القيد. يُشغَّل في CI `integration` فقط | data-engineer |
| `tests/integration/test_message_status_migration_live.py` | إنشاء | تنزيل `conversations` إلى `0007_message_citations` ثم رفعها إلى `0008`، مع صف قديم يعبر الرحلة. يُشغَّل في CI `integration` فقط | data-engineer |
| `src/app/modules/conversations/domain/value_objects.py` | تعديل | `MessageStatus`، و`MessageFallback`، و`_MAX_FALLBACK_NAME_LEN` | backend-developer |
| `src/app/modules/conversations/domain/entities.py` | تعديل | `Message.status` و`Message.fallback`، و`append_message(..., status=, fallback=)`، وINV‑CV5 | backend-developer |
| `src/app/modules/conversations/ports/inbound.py` | تعديل | `FallbackRef`، و`AppendedMessage.status` و`.fallback`، و`ConversationThreads.append(..., status=, fallback=)` | backend-developer |
| `src/app/modules/conversations/application/use_cases.py` | تعديل | `_recordable_status`، و`_recordable_fallback`، و`AppendMessage.execute(..., status=, fallback=)`، و`ConversationService.append` | backend-developer |
| `src/app/modules/conversations/adapters/sql_repository.py` | تعديل | عمودا الـ `Table`، والكتابة في `append_message`، والقراءة في `_hydrate_message`، و`_fallback_to_json` و`_fallback_from_json` و`_status_from_row` | backend-developer |
| `src/app/agents/orchestrator.py` | تعديل | `_STATUS_COMPLETE` و`_STATUS_PARTIAL`، و`_Fallback.ref()`، و`_stored_fallback`، و`_warn_if_fallback_dropped`. وتمرير الحقلين في `_persist_reply` و`_persist_partial` و`_persist_step_output`. وتحديث التعليقات (§3.5، AC‑5.6) | backend-developer |
| `src/app/workers/bootstrap.py` | تعديل | `status="complete"` في استدعاءي `conversation_messages.execute` (`:1079`، `:1174`)، وثابت `_STATUS_COMPLETE` بجانب `_ROLE_ASSISTANT` (`:782`) | backend-developer |
| `src/app/api/v1/dto/conversations.py` | تعديل | DTO `ProviderModel` و`MessageFallback`، و`MessageOut.status` و`.fallback` | backend-developer |
| `src/app/api/v1/routers/conversations.py` | تعديل | `_status_out` و`_fallback_out`، والمحوّلان `_to_message_out` و`appended_to_message_out` | backend-developer |
| `docs/design/openapi.yaml` | تعديل | `MessageOut.status` و`.fallback`، ومخططا `MessageFallback` و`ProviderModel`. **في commit واحد مع DTO**، لأن `test_api_conventions.py:495` يقارن خصائص `MessageOut` في الاتجاهين | backend-developer |
| `docs/design/03-api-spec.md` (§2، §3.1) | تعديل | سطر `MessageOut` وتعليقه، وفقرة الحالة في §3.1، وجملة `fallback` في فقرة `notice` | backend-developer |
| `docs/design/06-domain-models.md` (§4) | تعديل | حقلا `Message`، وVO `MessageStatus` و`MessageFallback`، وINV‑CV5، ونقض ب‑10 رقم 1 (AC‑5.3) | backend-developer |
| `docs/delivery/data-search-android/gaps.md` (ث‑2) | تعديل | فقرة «الإصلاح (التاريخ)» بعد اخضرار البوابات (AC‑5.7) | backend-developer |
| `tests/unit/test_conversations_domain.py` | تعديل | قواعد `MessageFallback`، و`MessageStatus`، وINV‑CV5 | backend-developer |
| `tests/unit/test_conversations_use_cases.py` | تعديل | الصرامة في الحالة، والليونة في `fallback`، والرفض على رسالة غير المساعد، وأن الصف لا يُكتب، والذهاب والإياب عبر `ConversationService.append` | backend-developer |
| `tests/unit/test_conversations_sql_statements.py` | تعديل | الربط في `INSERT` نفسه، وعدد العبارات، و`list_messages` عبارة واحدة، والترطيب، و`None` يُربط SQL NULL. بلهجة `asyncpg` القائمة (`_PG`) | backend-developer |
| `tests/unit/test_orchestrator.py` | تعديل | `_FakeThreads.append` يقبل `status`/`fallback` ويسجلهما في قائمتين **منفصلتين** (`self.statuses`، `self.fallbacks`). واختبارات US‑1، وAC‑2.5، وAC‑4.4، وAC‑4.5 | backend-developer |
| `tests/unit/test_workflow_orchestration.py` | تعديل | توقيع الـ fake `append`، ومخرج الخطوة `complete`/`null` (AC‑1.14) | backend-developer |
| `tests/unit/test_workers_bootstrap.py` | تعديل | `_FakeAppendMessage` يسجّل `status` في قائمة منفصلة، والرسالتان `complete` | backend-developer |
| `tests/unit/test_api_conversations_router.py` | تعديل | US‑3، وAC‑1.12، وAC‑4.1، وAC‑4.3، وAC‑4.6، وAC‑6.1، على السلك | backend-developer |
| `tests/unit/test_api_agents_router.py` | تعديل | AC‑4.2 | backend-developer |
| `tests/unit/test_api_conventions.py` | تعديل | اختبار مطابقة `MessageOut.status`/`fallback` و`MessageFallback`/`ProviderModel` بين `openapi.yaml` والتطبيق (AC‑5.1) | backend-developer |
| `tests/unit/test_llm_fallback.py` | تعديل | مسار 6.4 الحقيقي (`_FallbackLLM`): AC‑2.1 وAC‑2.2 وAC‑2.3 وAC‑2.4. يستعمل `_FakeThreads` من `test_orchestrator.py` بعد أن يمدّه backend-developer | qa-engineer |
| `tests/unit/test_websocket_endpoint.py` | تعديل | AC‑4.7: `notice` ثم `final` عبر WebSocket بلا مفتاح جديد | qa-engineer |
| `docs/delivery/data-search-message-status/test-plan.md` | إنشاء | مصفوفة AC ← اختبار، وفجوات التغطية | qa-engineer |

**ملفات لا تتغير، وقد فُحص ذلك:**
- **`deploy/postgres/initdb/10-roles.sh` و`src/app/ops/provision.py`:** الصلاحيات على `conversations.messages` على مستوى الجدول، فالعمودان مغطيان تلقائياً. فُحص ذلك في ث‑1 ولم يتغير.
- **سياسة RLS** `tenant_isolation` و`FORCE RLS` (`0001_conversations.py:106-115`) على مستوى الصف.
- **`src/app/api/v1/routers/agents.py`:** يستعمل `appended_to_message_out` (`:144`). و`AgentInvokeOut.message` يشير إلى `MessageOut` بـ `$ref`.
- **`src/app/api/v1/sse.py` و`websocket/streaming.py`:** الإطارات لا تتغير (FR‑9).
- **`src/app/framework/di/composition_root.py:1361` و`bootstrap.py:1666`:** يبنيان `AppendMessage(repository)` كما هو، والمعاملان الجديدان بقيم افتراضية.
- **`src/app/ops/load_seed.py:892`:** `INSERT` بأعمدة مسماة، فتبقى صفوف البذرة `NULL`، وهذا يعني «لم يُسجَّل» كالصفوف القديمة. و`explain_hot_paths.py:339` يقرأ `SELECT *`، فلا تتغير خطة الاستعلام. و`purge.py` يحذف صفوفاً كاملة.
- **`rag_agent` و`image_agent`/`video_agent`:** ما تصدره لا يتغير (AC‑1.8، AC‑1.9، AC‑4.5).
- **`docs/design/events/schemas/`:** لا مخطط لأحداث الرسائل. و`MessageAppended` في الذاكرة فقط، ولا يحمل الحالة (`06 §4`).
- **`.importlinter`:** لا `ignore_imports` جديد (AC‑6.5).
- **`tests/unit/support_conversations.py`:** `InMemoryConversationRepository` يخزن `Message` كما هو. والحقلان بقيم افتراضية، فلا تتغير أي `AppendedMessage(...)` أو `Message(...)` قائمة في الاختبارات.
- **`0007_message_citations.py`:** docstring يقول «a message now has exactly one [structured half]». هذا تاريخ ما طُبِّق، ولا يُعدَّل، على قاعدة ث‑1 مع `0005`.

## 3. العقود

### الواجهات البرمجية

لا مسار جديداً، ولا رمز خطأ جديداً، ولا صلاحية جديدة. يتغير جسم الرد في ثلاثة مسارات قائمة.

| الطريقة | المسار | الطلب | الاستجابة | الأخطاء | الصلاحية |
|---|---|---|---|---|---|
| GET | `/api/v1/conversations/{id}/messages` | بلا تغيير (`limit`، `cursor`) | `MessagePage`. كل `MessageOut` فيه `status: "complete" \| "partial" \| null` و`fallback: MessageFallback \| null`، والمفتاحان حاضران دائماً | بلا تغيير: 401، و403، و404 (محادثة غير موجودة أو من مساحة أخرى أو محذوفة)، و422 `common.validation_error` (cursor تالف) | `conversations:read` |
| POST | `/api/v1/conversations/{id}/messages` | بلا تغيير. أي `content.status` أو `content.fallback` يرسله العميل لا يُحفظ (FR‑11)، لأن `_turn_content` يأخذ `text` والمرفقات فقط | `stream=false`: `MessageOut` لرسالة المساعد، وفيه دائماً `status: "complete"`، و`fallback` مطابق للسجل. `stream=true`: SSE، والإطارات بلا تغيير | بلا تغيير: 401، و403، و404، و409، و422، و429، وproblem+json من `_in_band_error`. الدور المقطوع في `stream=false` يبقى problem+json (AC‑4.3) | `conversations:write` (+ صلاحيات الوكيل عبر المنسّق) |
| POST | `/api/v1/agents/{key}/invoke` | بلا تغيير | `stream=false`: `AgentInvokeOut.message.status` و`.fallback`. `stream=true`: بلا تغيير | بلا تغيير | `agents:invoke` |

**`MessageOut` على السلك (أمثلة):**
```json
{"id": "018f…", "role": "assistant",
 "content": {"text": "⚠️ تعذّر الوصولُ إلى النموذج السحابيّ، فأجاب النموذجُ المحلّيّ عن هذا السؤال.\n\nالجواب هو…\n\n⚠️ انقطعت الإجابةُ قبل اكتمالها.", "attachments": []},
 "citations": null,
 "status": "partial",
 "fallback": {"from": {"provider": "openai", "model": "gpt-4.1-mini"},
              "to": {"provider": "ollama", "model": "gemma3:1b"}},
 "token_count": 57, "seq": 4, "created_at": "2026-11-10T09:00:00Z"}
```
```json
{"id": "018f…", "role": "user", "content": {"text": "…", "attachments": []},
 "citations": null, "status": null, "fallback": null,
 "token_count": null, "seq": 3, "created_at": "2026-11-10T08:59:58Z"}
```

**OpenAPI** (`docs/design/openapi.yaml`، 3.1.0، بأسلوب الملف القائم: `nullable: true`، و`{allOf: [$ref], nullable: true}` كما في `:1176`، و`nullable` مع `enum` كما في `:1803`):

يُدرج الحقلان بعد `citations` في `MessageOut`، ويُدرج المخططان بعد `Citation`:
```yaml
        status:
          type: string
          nullable: true
          enum: [complete, partial]
          description: "How the assistant turn that wrote this message ended (gap th-2). Set by the code path that wrote the message, never inferred from its text: an answer that quotes the cut-off sentence is still `complete`. `complete` = the turn reached `final` (this includes a clarifying question, a fixed apology, a media agent's queued job, a workflow step output and a delivered document summary). `partial` = the turn was cut after it had streamed text (total-time cap, provider failure mid-answer, platform fault, or the reader left); the stored text still ends with the cut-off sentence. Always present. `null` = not recorded: user messages, and every message saved before this field. A turn cut before its first token stores no message, so it never appears here (th-3). Clients must treat a value they do not know as not complete."
        fallback:
          allOf: [{ $ref: "#/components/schemas/MessageFallback" }]
          nullable: true
          description: "Set when the local model answered instead of the conversation's cloud model (capacity-plan 6.4), complete or partial; equal to the `fallback` key of that turn's `final` frame. The stored text still starts with the fallback sentence. Always present. `null` = the primary model answered, or not recorded (user messages, messages saved before this field). A turn the primary model started and then lost is not a fallback (`null`). Route-level names only, never a credential."
```
```yaml
    MessageFallback:
      type: object
      required: [from, to]
      description: "The model the conversation asked for (`from`) and the local model that answered (`to`) -- the shape of `final.fallback` and of the `notice` frame's `from`/`to` (03 §3.1)."
      properties:
        from: { $ref: "#/components/schemas/ProviderModel" }
        to: { $ref: "#/components/schemas/ProviderModel" }
    ProviderModel:
      type: object
      required: [provider, model]
      properties:
        provider: {type: string, minLength: 1, maxLength: 255}
        model: {type: string, minLength: 1, maxLength: 255}
```
- لا يُضاف `required` إلى `MessageOut` نفسه، كما في ث‑1 د‑ت7. الحضور الدائم موصوف في `description`، ومثبت في `LIVE.required` وعلى السلك.
- **العميل والأنواع:** لا واجهة في هذا المستودع. يقرأ Android `openapi.yaml` أو `/openapi.json` الحي، ويحدّث محوّله. و`backend-reply.md` لا يُحدَّث (س‑11). الإصلاح يُسجَّل في `gaps.md` §ث‑2.

### التواقيع

#### 3.1 domain — `src/app/modules/conversations/domain/value_objects.py`
```python
# th-2 -- bound on each provider/model name a fallback stores. Route-level
# names from the model catalogue are far shorter; the bound keeps the jsonb
# small and mirrors _MAX_CITED_FILE_NAME_LEN.
_MAX_FALLBACK_NAME_LEN = 255


class MessageStatus(StrEnum):
    """How the assistant turn that wrote a message ended (th-2, 06 section 4).

    Closed on purpose: there is no `error` -- no code path stores a message
    for a turn that produced nothing (owner decision q-3). A failure with no
    message is th-3's attempt record, not a value here.
    """

    COMPLETE = "complete"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class MessageFallback:
    """The 6.4 switch an assistant message was answered under (th-2).

    Four route-level names -- what `final.fallback` carries -- and nothing
    else: no credential can be represented. A snapshot at answer time, like
    `Citation`: a later change of LLM_FALLBACK_ROUTE does not rewrite it.
    Stored verbatim (not stripped, not lower-cased) so it equals the frame.
    """

    from_provider: str
    from_model: str
    to_provider: str
    to_model: str

    def __post_init__(self) -> None:
        # raises InvalidConversationInput when any of the four is not a `str`,
        # is empty, or is longer than _MAX_FALLBACK_NAME_LEN characters.
        ...
```

#### 3.2 domain — `src/app/modules/conversations/domain/entities.py`
```python
@dataclass(slots=True)
class Message:
    ...
    citations: tuple[Citation, ...] | None = None
    # th-2 -- LAST and defaulted, for citations' reason. None = not recorded
    # (user messages, rows that predate the column).
    status: MessageStatus | None = None
    # th-2 -- None = the primary model answered, or not recorded.
    fallback: MessageFallback | None = None


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
        status: MessageStatus | None = None,
        fallback: MessageFallback | None = None,
    ) -> Message:
        """... INV-CV5: `status` and `fallback` are carried by ASSISTANT
        messages only, and a `fallback` needs a `status`; otherwise
        InvalidConversationInput."""
```
- INV‑CV5 يُفحص بعد `_guard_not_deleted()` وبعد INV‑CV4، وقبل زيادة `message_count`، بنمط INV‑CV4 نفسه.

#### 3.3 port — `src/app/modules/conversations/ports/inbound.py`
```python
@dataclass(frozen=True, slots=True)
class FallbackRef:
    """A 6.4 fallback as it crosses the port (th-2) -- plain fields, no rules.

    Flat for CitationRef's reason (the caller must not import this module's
    domain), and flat rather than {from, to} because `from` is a Python
    keyword. The rules live on `domain.MessageFallback`; the API renders the
    `{from: {provider, model}, to: {...}}` shape `final.fallback` has.
    """

    from_provider: str
    from_model: str
    to_provider: str
    to_model: str


@dataclass(frozen=True, slots=True)
class AppendedMessage:
    ...
    citations: tuple[CitationRef, ...] | None = None
    # th-2 -- what was STORED. `status` crosses as a plain str, like `role`
    # ("complete" | "partial" | None). `fallback` can be None even when one
    # was sent (the module dropped it as malformed). Last and defaulted.
    status: str | None = None
    fallback: FallbackRef | None = None


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
        status: str | None = None,
        fallback: FallbackRef | None = None,
    ) -> AppendedMessage: ...
```

#### 3.4 application — `src/app/modules/conversations/application/use_cases.py`
```python
def _recordable_status(role: MessageRole, status: str | None) -> MessageStatus | None:
    """th-2 / NFR-4 -- the status this message will store. STRICT, unlike
    citations: every writer passes a constant, so a bad value is a caller bug.

    - status is None                 -> None
    - role is not ASSISTANT          -> raise ValidationError
    - MessageStatus(status) fails    -> raise ValidationError("invalid message status: ...")
    - otherwise                      -> the enum member
    """


def _recordable_fallback(
    role: MessageRole, status: MessageStatus | None, ref: FallbackRef | None
) -> MessageFallback | None:
    """th-2 -- the fallback this message will store.

    - ref is None                    -> None
    - role is not ASSISTANT          -> raise ValidationError   (a caller bug, loud)
    - status is None                 -> raise ValidationError   (a caller bug, loud)
    - MessageFallback(...) raises    -> None  (dropped, the append goes on)
    - otherwise                      -> MessageFallback with the four values verbatim

    Dropping a bad VALUE and not raising is _recordable_citations' rule: the
    text is the turn, and the fallback annotates it -- the stored text still
    starts with the fallback sentence either way.
    """


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
        status: str | None = None,
        fallback: FallbackRef | None = None,
    ) -> tuple[Message, tuple[ConversationEvent, ...]]: ...
```
- **ترتيب الفحص:**
  1. `MessageRole(role)`؛
  2. ثم `MessageContent`؛
  3. ثم `_recordable_citations`؛
  4. ثم `_recordable_status`؛
  5. ثم `_recordable_fallback`.

  كلها قبل `self._conversations.get(...)`، فالرفض لا يكتب صفاً ولا يقرأ شيئاً (AC‑1.13).
- **نقل الخطأ:** `conversation.append_message(...)` يمرر `status=` و`fallback=`. و`InvalidConversationInput` من INV‑CV5 لا يصل عملياً، لأن الفحص المسبق يسبقه. وإن وصل، يُحوَّل إلى `ValidationError` في الكتلة نفسها التي تحوّل `ConversationDeletedError` إلى `ConflictError`.
- **`ConversationService.append`:**
  - يمرر الحقلين إلى `AppendMessage`.
  - يعيد `AppendedMessage(status=message.status.value if message.status else None, fallback=FallbackRef(...) if message.fallback else None)`.
- لا تسجيل (logging) في طبقة application، كما هو اليوم.

#### 3.5 agents — `src/app/agents/orchestrator.py`
```python
from app.modules.conversations.ports.inbound import (
    AppendedMessage,
    CitationRef,
    ConversationThreads,
    FallbackRef,
)

# th-2 -- the message status each writer states. Constants, never derived
# from the text (FR-3): the code path that writes the message is the fact.
_STATUS_COMPLETE = "complete"
_STATUS_PARTIAL = "partial"


@dataclass(slots=True)
class _Fallback:
    ...
    def describe(self) -> Json:  # unchanged
        ...

    def ref(self) -> FallbackRef:
        """describe()'s four values as the conversations port takes them (th-2).
        Same four attributes, so the stored fallback equals `final.fallback`."""


def _stored_fallback(fallback: _Fallback | None) -> FallbackRef | None:
    """The fallback a message records: the SAME condition as
    _with_fallback_notice (`fallback.switched`), so "the text starts with the
    notice" and "fallback is not null" cannot disagree. A turn the primary
    started and then lost never switched, and records None."""


def _warn_if_fallback_dropped(
    conversation_id: Uuid, sent: FallbackRef | None, stored: AppendedMessage
) -> None:
    """One warning when the module dropped a fallback it was handed:
    `orchestrator.fallback_not_recorded`, extra={"conversation_id": ...} only
    -- no provider or model name in the log (NFR-8, q-5)."""
```

**التغيير في الكتّاب الخمسة** (لا يتغير غيرها):

| الموضع | الاستدعاء بعد التغيير |
|---|---|
| `_persist_reply` (`:2340`) | `fallback = _stored_fallback(record.fallback)`، ثم `threads.append(..., citations=declared.refs, status=_STATUS_COMPLETE, fallback=fallback)`، ثم `_warn_if_fallback_dropped(...)` بعد نجاح الإدراج، بجانب تحذير الاستشهادات |
| `_persist_partial` (`:2184`) | `threads.append(..., status=_STATUS_PARTIAL, fallback=fallback)`، ثم `_warn_if_fallback_dropped(...)`. يبقى التحذير `partial_reply_persist_failed` كما هو، ولا يُضاف `reason` إلى الرسالة (س‑9) |
| `_persist_step_output` (`:1716`) | `threads.append(..., status=_STATUS_COMPLETE)`. لا `fallback`، لأن الخطوات لا تتحول (`begin_step` بلا `_Fallback`، `:1763`) |
| رسالتا المستخدم (`:1563`، `:1651`) | **بلا تغيير.** القيمة الافتراضية `None` (FR‑6) |

- **إطار `final` لا يتغير** (FR‑9، AC‑4.4):
  - لا يُضاف مفتاح `status` ولا `message_status`.
  - يبقى `final.status` لوكلاء الوسائط حالة المهمة (ت‑4، AC‑4.5).
  - يبقى `final.fallback` كما يضعه `_metered` (`:2046-2050`).
- **الحارس `terminal` و`record.message`** (`:1979-1985`، `:2174`): بلا تغيير. الرسالة تُكتب مرة واحدة بحالة واحدة (AC‑1.11). والدور المكتمل الذي فشلت كتابته لا يُحفظ `partial` بعده.
- **`invoke_once`:** بلا تغيير. يعيد `record.message`، ومعه `status` و`fallback` من `AppendedMessage`.

**تحديث التعليقات بصدق** (AC‑5.6، ت‑2). هذه نصوص تعليقات وdocstrings بالإنجليزية، لا سلوك:
- **تعليق ب‑10 (`:426-437`):**
  - القرار 1 (جملة لا حقل) **نقضه ث‑2** بقرار المالك (2026‑11‑10).
  - الرسالة تحمل الآن `messages.status` (`partial` لكل ما يكتبه `_persist_partial`).
  - الجملة **باقية** في النص لكل قارئ لا يعرف الحقل، وللصفوف القديمة التي لا حالة لها.
  - تُحذف عبارة «turning this marker into a field is ث-2, not decided here».
- **تعليق جملة التحول (`:445-452`):**
  - الجملة باقية في رأس الرد.
  - صار معها `messages.fallback` بقيمة `describe()` نفسها.
- **docstring `_turn_content` (`:499-523`):** «exactly one structured field … `citations`» يصبح «ثلاثة حقول مُهيكَلة لأشكال معروفة: `citations` (ث‑1)، و`status` و`fallback` (ث‑2)». ويبقى صحيحاً أنه لا حقل `data` عاماً.
- **docstring `_persist_reply`:** سطر عن `status=complete` و`fallback`.
- **docstring `_persist_partial`:** سطر يقول إن «marked as incomplete» صار حقلاً أيضاً (`status=partial`)، والجملة باقية.
- **docstring `_persist_step_output`:** سطر عن `status=complete`.

#### 3.6 workers — `src/app/workers/bootstrap.py`
```python
_ROLE_ASSISTANT = "assistant"
# th-2 -- a delivered summary (or its failure notice) is a whole message: the
# JOB's outcome is told in the text, as a media agent's job status is.
_STATUS_COMPLETE = "complete"
```
- `conversation_messages.execute(..., role=_ROLE_ASSISTANT, text=..., status=_STATUS_COMPLETE)` في الموضعين `:1079` و`:1174`.
- لا `fallback`. التلخيص لا يمر بـ `_FallbackLLM`.

#### 3.7 adapter — `src/app/modules/conversations/adapters/sql_repository.py`
```python
messages = Table(
    "messages", _metadata,
    ...,
    Column("citations", JSONB(none_as_null=True), nullable=True),
    # `migrations/versions/conversations/0008_message_status.py` (th-2).
    # Text, not an enum type: the closed set is ck_messages_status plus
    # domain.MessageStatus, and an ALTER TYPE is a heavier migration later.
    Column("status", Text, nullable=True),
    # none_as_null=True is LOAD-BEARING, for citations' reason exactly.
    Column("fallback", JSONB(none_as_null=True), nullable=True),
    schema="conversations",
)


def _fallback_to_json(fallback: MessageFallback | None) -> Json | None:
    """None -> None (SQL NULL); else exactly
    {"from": {"provider", "model"}, "to": {"provider", "model"}} -- final.fallback's shape."""


def _fallback_from_json(raw: object) -> MessageFallback | None:
    """NULL -> None; an object -> MessageFallback(...) re-validated, like
    _citations_from_json. A malformed row raises (an explicit read error)."""


def _status_from_row(raw: object) -> MessageStatus | None:
    """NULL -> None; else MessageStatus(raw). An unknown value raises
    ValueError -- an explicit read error, never an invented value (US-3 edge).
    ck_messages_status makes it unreachable for every row written since 0008."""
```
- **`append_message`:** يضيف `status=None if message.status is None else message.status.value` و`fallback=_fallback_to_json(message.fallback)` إلى `insert(messages).values(...)` نفسها. تبقى عبارتان كما اليوم (AC‑1.10، NFR‑1):
  - `INSERT` على `messages`؛
  - ثم `UPDATE` على `conversations` (version).
  - ولا `UPDATE` على `messages`.
- **`list_messages` و`get_message`:** العبارة نفسها، `select(messages)`، فتُسقط العمودين معها (AC‑3.8، NFR‑2).
- **`_hydrate_message`:** `status=_status_from_row(row["status"])` و`fallback=_fallback_from_json(row["fallback"])`.
- **`save_message`:** بلا تغيير. يكتب `deleted_at` وحده، فالحقلان لا يتغيران بعد الإنشاء (FR‑4).

#### 3.8 api — `src/app/api/v1/dto/conversations.py` و`routers/conversations.py`
```python
class ProviderModel(BaseModel):
    """One side of a 6.4 fallback (th-2): route-level names, no credential."""

    provider: str
    model: str


class MessageFallback(BaseModel):
    """`final.fallback`'s shape, stored with the message (th-2). The wire key
    is `from`, a Python keyword, hence the alias; build it with
    `model_validate({"from": ..., "to": ...})` (pydantic.mypy is on)."""

    from_: ProviderModel = Field(alias="from")
    to: ProviderModel


class MessageOut(BaseModel):
    id: str
    role: str
    content: dict[str, Any]
    citations: list[Citation] | None
    # th-2 -- required-present, no default: both keys are always on the wire.
    status: Literal["complete", "partial"] | None
    fallback: MessageFallback | None
    token_count: int | None
    seq: int
    created_at: datetime
```
```python
# routers/conversations.py
def _status_out(raw: str | None) -> Literal["complete", "partial"] | None:
    # Explicit, not a cast: a value outside the closed set is a loud 500, never
    # silently rendered (the port's `status` is a plain str).
    ...

def _fallback_out(fallback: object | None) -> MessageFallback | None:
    # Works for both sources -- domain `MessageFallback` (reads) and port
    # `FallbackRef` -- because both carry the same four attributes.
    # None -> None; else MessageFallback.model_validate(
    #     {"from": {"provider": f.from_provider, "model": f.from_model},
    #      "to":   {"provider": f.to_provider,   "model": f.to_model}})
    ...

# _to_message_out:         status=_status_out(None if m.status is None else m.status.value),
#                          fallback=_fallback_out(m.fallback)
# appended_to_message_out: status=_status_out(m.status), fallback=_fallback_out(m.fallback)
```
- **التسلسل بالاسم البديل:**
  - `GET` له `response_model`، والمساران الآخران `response_model=None` ويعيدان نموذج Pydantic. FastAPI يمرره في الحالتين إلى `jsonable_encoder` بـ `by_alias=True`، فيخرج المفتاح `from` لا `from_`.
  - يثبت ذلك اختبار على السلك (AC‑2.1، AC‑4.1) واختبار المخطط الحي (AC‑5.1).
- **العربية:** Starlette `JSONResponse` يسلسل بـ `ensure_ascii=False` (AC‑3.6)، كما في ث‑1.

## 4. البيانات

**الترحيل:** `migrations/versions/conversations/0008_message_status.py`
- `revision = "0008_message_status"`: 19 حرفاً، أقل من حد 32 الذي يفحصه `test_migration_revision_ids.py`.
- `down_revision = "0007_message_citations"`: رأس السلسلة الحالي.

```python
def upgrade() -> None:
    # One ALTER per column: test_conversations_schema_parity.py reads
    # "ALTER TABLE conversations.messages ADD COLUMN <name>".
    op.execute("ALTER TABLE conversations.messages ADD COLUMN status text NULL;")
    op.execute("ALTER TABLE conversations.messages ADD COLUMN fallback jsonb NULL;")
    op.execute(
        """
        ALTER TABLE conversations.messages ADD CONSTRAINT ck_messages_status
          CHECK (status IN ('complete', 'partial')) NOT VALID;
        """
    )
    # Deliberately NO `VALIDATE CONSTRAINT` (module docstring).


def downgrade() -> None:
    # DATA LOSS by design: every stored status and fallback is discarded.
    op.execute("ALTER TABLE conversations.messages DROP CONSTRAINT IF EXISTS ck_messages_status;")
    op.execute(
        "ALTER TABLE conversations.messages DROP COLUMN IF EXISTS fallback, "
        "DROP COLUMN IF EXISTS status;"
    )
```

**ما يقوله docstring الترحيل** (بنمط `0007` و`files/0006`):
- **الغرض:** ث‑2. ينقض قرار ب‑10 رقم 1 بقرار المالك (2026‑11‑10)، وتبقى الجملتان في النص.
- **لماذا عمودان لا مفتاحان في `content`:** `content` مغلق (ق‑2 في ث‑1).
- **لماذا عمودان لا عمود واحد:** الحالة والتحول متعامدان (`partial` + محلي ممكن)، فلكل منهما عمود.
- **`text` لا نوع enum:** المجموعة المغلقة يفرضها القيد و`domain.MessageStatus`. ونوع enum يحتاج `ALTER TYPE` لاحقاً.
- **`NULL` يعني «لم يُسجَّل»:** هذا معنى كل الصفوف القديمة، فلا backfill ولا `DEFAULT`. وطلب Android صراحة ألا تُسمّى القديمة مكتملة.
- **`fallback jsonb`:** بشكل `final.fallback` نفسه. بلا CHECK، لأن القواعد في `domain.MessageFallback` (قاعدة ث‑1).
- **القيد `NOT VALID` بلا `VALIDATE`:**
  - `NOT VALID` يجعل `ADD CONSTRAINT` تعديلاً في الـ catalog فقط. وPostgres **يفرض** القيد على كل `INSERT`/`UPDATE` بعده.
  - كل صف قائم قيمته `NULL` بالبناء، لأن العمود وُلد في الترحيل نفسه، و`NULL` يمر بالـ CHECK.
  - فالـ `VALIDATE` مسح كامل لأكبر جدول في المنصة، تحت `ACCESS EXCLUSIVE` نفسه (Alembic يشغّل كل الترحيلات في معاملة واحدة، `env.py:95-102`)، **ولا يثبت شيئاً**.
  - هذا خلاف مقصود لسابقة `files/0005` و`0006`. هناك الجدول محدود بـ `max_files_per_workspace`. أما هنا فعلى بذرة السعة نحو مليون رسالة (د‑ت3).
- **بلا فهرس:** العمودان إسقاط لا شرط. لا مسار يرشّح بالحالة.
- **RLS والصلاحيات:** بلا تغيير، على مستوى الصف والجدول.
- **التراجع:** `downgrade` يحذف القيد والعمودين، فتضيع كل الحالات والمؤشرات المسجلة منذ الترقية. ولا يُمس النص، والجملتان فيه. الأفضل في التراجع ترك العمودين، فهما خاملان للشيفرة القديمة.

**`01-data-model.md` §2.4.** سطران جديدان في `CREATE TABLE conversations.messages` بعد `citations`، وقيد قبل `fk_msg_conv`:
```sql
  status          text NULL,                        -- ث‑2 · كيف انتهى الدور الذي كتب رسالة المساعد: 'complete' | 'partial'؛ NULL ⇒ لم يُسجَّل
  fallback        jsonb NULL,                       -- ث‑2 · {from:{provider,model}, to:{provider,model}} حين أجاب النموذج المحلي (6.4)؛ NULL ⇒ أجاب الأساسي أو لم يُسجَّل
  CONSTRAINT ck_messages_status CHECK (status IN ('complete','partial')),   -- أُضيف NOT VALID في 0008
```
وتعليق تحته يذكر:
- أن الحالة يكتبها مسار الشيفرة لا النص، وأن الجملتين باقيتان.
- أن `NULL` يعني رسائل المستخدم والصفوف القديمة وصفوف `load_seed`، بلا backfill.
- أن `fallback` يستلزم `status` (INV‑CV5، في الـ domain لا في القاعدة).
- لماذا القيد `NOT VALID`.
- وأن «للرسالة جزء مُهيكَل واحد: `citations`» صار «ثلاثة أجزاء مُهيكَلة: `citations` و`status` و`fallback`». ويُصحَّح تعليق `citations` (`:205-209`) وتعليق `pending_clarification` (`:184`) بالمعنى نفسه، بعبارة لا تُبطل تعليل `pending_clarification`: لا شيء من هذه الأجزاء يحمل المرشّحين.

**الخصائص:**

| الخاصية | القيمة |
|---|---|
| الفهارس | لا شيء |
| القيود | `ck_messages_status` (`NOT VALID`، مفروض على الصفوف الجديدة) |
| RLS | بلا تغيير |
| الأدوار والصلاحيات | بلا تغيير |
| متوافق مع الإصدار السابق | نعم (expand فقط) |
| يمر بـ `test_zero_downtime_migrations.py` | نعم. لا `DROP`/`RENAME`/`SET NOT NULL` في `upgrade()`، ولا `NOT NULL` بلا `DEFAULT`، ولا فهرس حاجب |

**التوافق في الاتجاهين:**
- **الشيفرة القديمة على قاعدة مُرحَّلة:** تُدرج بلا العمودين، فيصيران `NULL`، والقيد يقبل ذلك. وتقرأ بـ `select(messages)` بأعمدة الـ `Table` القديم، فتتجاهلهما. خلال نافذة النشر المتدرج، رسائل يكتبها replica قديم تبقى `NULL` إلى الأبد، أي «لم يُسجَّل». هذا مقبول ومطابق لدلالة `NULL`.
- **الشيفرة الجديدة تتطلب العمودين.** `insert` و`select(messages)` يسمّيانهما. لذلك يجب أن يسبق `migrate` (`python -m app.ops.provision`) الصورة الجديدة، وهو الترتيب القائم في `docker-compose.yml` و`deploy/rolling-deploy.sh`. وفشل الحفظ لا يمس بث `final` (NFR‑5).

**قفل الترحيل:**
- ثلاث عبارات `ALTER` في معاملة واحدة، كلها تعديل catalog.
- تأخذ `ACCESS EXCLUSIVE` على `conversations.messages` لحظياً، تحت `lock_timeout` الذي يضبطه `env.py` في startup packet.
- لا مسح ولا إعادة كتابة.

**التراجع:** يُرجَع أولاً `app` إلى صورة ما قبل ث‑2، ثم يُشغَّل التراجع عبر الأداة المعتمدة. `alembic downgrade` المباشر على المكدس ممنوع مثل `upgrade` (`docs/stack-commands.md`). أما الاختبار الحي في CI (§5) فيستعمل `alembic.command` على قاعدة الاختبار المعزولة، بنمط `test_space_columns_live.py:59-64`.

**الحجم:**
- `status` نحو 9 بايت لكل رسالة مساعد جديدة.
- `fallback` نحو 120 بايت، وعلى أدوار التحول فقط.
- لا أثر ملموس على الجدول ولا على صفحة `list_messages`.

## 5. خطة الاختبار المقترحة

**المستويات:**
- **unit:** تحت `tests/unit/`، وتُشغَّل محلياً: `pytest tests/unit tests/architecture tests/eval`.
- **integration:** تحت `tests/integration/` (`live_db`). تُكتب الآن، وتُشغَّل في CI job `integration` فقط (س‑10).
- **مراجعة:** فحص يدوي يقوم به code-reviewer.

**مفاتيح المساعدة في الجدول:**
- `T` = `_FakeThreads` في `test_orchestrator.py`، ومعه القائمتان الجديدتان `T.statuses` و`T.fallbacks`.
- `SQL` = `test_conversations_sql_statements.py` (لهجة `asyncpg`).
- `RLS` = `test_conversations_repository_rls.py`.
- `MIG` = `test_message_status_migration_live.py`.

| AC | المستوى | المكان | ما يُثبت |
|---|---|---|---|
| AC‑1.1 | unit + integration | `test_orchestrator.py`: `_StreamingAgent` (أو وكيل نصي قائم مماثل)، `T.statuses[-1] == "complete"`. `test_conversations_use_cases.py`: `ConversationService.append(status="complete")` يعيد `AppendedMessage.status == "complete"`. `SQL`: `params["status"] == "complete"`. `RLS`: إدراج ثم `list_messages` | مسار `final`، والذهاب والإياب |
| AC‑1.2 | unit | `test_orchestrator.py`: تمديد `test_the_partial_reply_is_marked_as_incomplete` بـ `T.statuses == [None, "partial"]` (المستخدم ثم المساعد). توقعات النص القائمة لا تتغير | مسار المهلة |
| AC‑1.3 | unit | `test_orchestrator.py`: `_HalfAnswerThenFailAgent` يعطي `"partial"` | حدث `error` بعد tokens |
| AC‑1.4 | unit | `test_orchestrator.py`: تمديد `test_the_record_says_which_cut_produced_the_save` (`:2588`) بحيث يكون `"partial"` لكل قيمة من `reason`، ومنها `error` من فرع `except Exception` | استثناء المنسّق |
| AC‑1.5 | unit | `test_orchestrator.py`: تمديد `test_an_abandoned_stream_keeps_what_it_streamed` (`:2459`، `aclose()` بعد token) بـ `"partial"` | التخلّي |
| AC‑1.6 | unit | `test_orchestrator.py`: وكيل يصدر `final` نصه يحتوي `_INCOMPLETE_MARKER_AR` و`_INCOMPLETE_MARKER_EN` حرفياً. النتيجة `"complete"`، والنص المحفوظ مطابق للمُصدَر | FR‑3 |
| AC‑1.7 | unit | `test_orchestrator.py`: `_AskingAgent` القائم (يصدر `pending_clarification`)، فيكون `"complete"`، و`"pending_clarification" not in final.data` | |
| AC‑1.8 | unit | `test_orchestrator.py`: وكيل يصدر نصاً ثابتاً و`citations: []`، فيكون `"complete"` و`T.fallbacks[-1] is None` | |
| AC‑1.9 | unit | `test_orchestrator.py`: `_JobAgent`، فيكون `"complete"`، والنص المحفوظ `json.loads(...)["status"] == "queued"` | |
| AC‑1.10 | unit | `SQL`: `append_message` ينفذ عبارتين بالضبط. الأولى `INSERT INTO conversations.messages` وفيها `status` و`fallback`، والثانية `UPDATE conversations.conversations`، ولا `UPDATE conversations.messages`. لحالتي `complete` و`partial` مع `fallback` | NFR‑1، FR‑4 |
| AC‑1.11 | unit | `test_orchestrator.py`: تمديد `test_a_partial_reply_is_persisted_once_not_twice`: رسالة مساعد واحدة، و`T.statuses` فيه `"partial"` مرة واحدة | |
| AC‑1.12 | unit | `test_api_conversations_router.py`: `POST` بـ `content.status`/`content.fallback` ثم `GET`، فتكون رسالة المستخدم `status is None` و`fallback is None`، و`set(content) == {"text", "attachments"}`. `test_orchestrator.py`: إدخال `append` للمستخدم بـ `status=None` | FR‑11 |
| AC‑1.13 | unit | `test_conversations_use_cases.py`، حالات مُعلمة: `status` من `"error"` و`"done"` و`"COMPLETE"` و`""`، فيكون `ValidationError` ولا صف في `InMemoryConversationRepository`. `status="complete"` على `role="user"` يعطي `ValidationError`. و`fallback` بلا `status` يعطي `ValidationError`. `test_conversations_domain.py`: INV‑CV5 على `append_message` مباشرة | NFR‑4 |
| AC‑1.14 | unit | `test_workflow_orchestration.py`: خطوة مكتملة تعطي `"complete"` و`None`. والخطوة الفاشلة لا تُكتب (تأكيد قائم) | س‑7 |
| حواف US‑1 | unit | `test_orchestrator.py`: الاختبارات القائمة `…cut_before_it_said_anything_stores_nothing` و`…completed_turn_whose_write_failed_is_not_saved_as_a_partial` و`…unwired_conversations_seam…` تبقى خضراء، ولا قيمة في `T.statuses` للمساعد. `test_workers_bootstrap.py`: تسليم الملخص وتسليم الفشل يعطيان `"complete"` (د‑ت6) | NFR‑5 |
| AC‑2.1 | unit | `test_llm_fallback.py` (qa): مسار `_FallbackLLM` الحقيقي (فشل قبل أول chunk ثم جواب محلي). `T.statuses[-1] == "complete"`، والقيمة `{from:{provider,model}, to:{…}}` المبنية من `T.fallbacks[-1]` تساوي `final.data["fallback"]` مقارنة عميقة. `SQL`: `_fallback_to_json` يعطي الشكل نفسه. `test_api_conversations_router.py`: `appended_to_message_out` لـ `FallbackRef` يعطي `body["fallback"]["from"]`، ولا `from_` | معيار Android 6 |
| AC‑2.2 | unit | `test_llm_fallback.py` (qa): المحلي يموت بعد أول chunk. `"partial"`، و`T.fallbacks[-1] is not None`، والنص يبدأ بـ `_FALLBACK_NOTICE_*` وينتهي بجملة الانقطاع | |
| AC‑2.3 | unit | `test_llm_fallback.py` (qa): تمديد `test_a_healthy_cloud_turn_is_untouched` بـ `T.fallbacks[-1] is None`. وتمديد `test_a_failure_after_the_first_chunk_is_cut_not_answered_twice` (الدور المختلط) بـ `"partial"` و`None` | |
| AC‑2.4 | unit | `test_llm_fallback.py` (qa): النص المحفوظ يبدأ بالجملة بلغة السؤال. و`test_a_fallback_turn_keeps_its_citations_and_still_gets_the_notice_key` يبقى أخضر | FR‑10 |
| AC‑2.5 | unit | `test_orchestrator.py`: تمديد `:3002`. وكيل يضع مفتاح `fallback` بنفسه بلا تحول، فيكون `T.fallbacks[-1] is None`، ويبقى `final.data["fallback"]` كما وضعه الوكيل. ووكيل يحاكي بوابة الثقة (اعتذار)، فيكون `None` | A‑5، د‑ت5 |
| AC‑2.6 | unit | `test_conversations_domain.py`: `{f.name for f in fields(MessageFallback)}` هي الأربعة فقط. `SQL`: مفاتيح `_fallback_to_json` هي `{"from", "to"}`، وكلٌّ منهما `{"provider", "model"}`. `test_orchestrator.py`: `_Fallback` بـ `api_key="sk-secret"`، و`repr(ref)` و`_fallback_to_json` لا يحتويان السر | NFR‑8 |
| AC‑3.1 | unit | `test_api_conversations_router.py`: SSE ثم `GET`. الرسالة التي `id == final.message_id` فيها `status == "complete"` و`fallback is None` | |
| AC‑3.2 | unit | `test_api_conversations_router.py`: orchestrator بـ `stream_max_duration_s` صغير ووكيل يصدر token ثم ينام، ثم `GET`. آخر رسالة `assistant` بعد رسالة المستخدم `status == "partial"` | معيار Android 5 |
| AC‑3.3 | unit + integration | `SQL`: `_hydrate_message` على صف `status=None, fallback=None` يعطي `None` و`None`. `test_api_conversations_router.py`: `Message(...)` بلا الحقلين في المستودع المزيّف، نصه ينتهي بجملة الانقطاع، فيكون 200 و`null`/`null`. `RLS`: صف بـ SQL خام بلا العمودين يعيده `list_messages` بـ `None`/`None`. `MIG`: الصف القديم بعد الرفع `IS NULL` | NFR‑3 |
| AC‑3.4 | unit | `test_api_conversations_router.py`: رسالة `user` فيها `"status" in m` و`"fallback" in m`، وقيمتاهما `None` | |
| AC‑3.5 | unit | `test_api_conversations_router.py` و`test_api_agents_router.py`: `set(content) == {"text", "attachments"}` لكل رسالة. وتأكيدات `citations` من ث‑1 تبقى خضراء | |
| AC‑3.6 | unit + integration | `test_api_conversations_router.py`: جزئي عربي «الجواب هو»، و`response.content` يحتوي بايتات UTF‑8 لـ `انقطعت الإجابةُ قبل اكتمالها.`، ولا `\u`، و`"partial"`. `RLS`: النص يعود مطابقاً | NFR‑9 |
| AC‑3.7 | unit | `test_api_conversations_router.py`: ثلاثة أدوار (`complete`، ثم `partial`، ثم `complete` مع `fallback` عبر fake threads أو seed)، تُقرأ بـ `limit=1` حتى `next_cursor: null`، فكل رسالة بقيمتيها | |
| AC‑3.8 | unit | `SQL`: `list_messages` عبارة واحدة، وFROM فيها `conversations.messages` وحده، وقائمة SELECT تحوي `status` و`fallback` | NFR‑2 |
| AC‑3.9 | unit | `test_api_conversations_router.py`: `DELETE …/messages/{id}` للرسالة الجزئية ثم `GET`، فهي غائبة | INV‑CV3 |
| AC‑4.1 | unit | `test_api_conversations_router.py`: `stream: false`، فيكون `reply["status"] == "complete"`، و`reply["fallback"]` يساوي `GET` للـ `id` نفسه | FR‑8 |
| AC‑4.2 | unit | `test_api_agents_router.py`: `invoke` بـ `stream: false`، و`message.status`/`fallback` يساويان `GET` | FR‑8 |
| AC‑4.3 | unit | `test_api_conversations_router.py`: `stream: false` ووكيل يفشل بعد tokens، فيكون 502 problem+json بلا `status` من `MessageOut`، ثم `GET` يعيد رسالة مساعد جديدة `"partial"` | |
| AC‑4.4 | unit | `test_orchestrator.py`: `set(final.data)` يساوي مجموعة اليوم بالضبط لوكيل نصي (`_StreamingAgent`: مفاتيحه هو + `{"message_id", "content", "usage"}`)، ولـ `_CitingAgent` (+`citations`)، ولدور متحوّل (+`fallback`). و`"message_status" not in` | FR‑9 |
| AC‑4.5 | unit | `test_orchestrator.py`: `_JobAgent`، فيكون `final.data["status"] == "queued"` و`T.statuses[-1] == "complete"` | ت‑4 |
| AC‑4.6 | unit | `test_api_conversations_router.py`: SSE مقطوع، و`set(error) == {"type", "title", "status", "code", "detail", "correlation_id"}`، و`isinstance(error["status"], int)` | |
| AC‑4.7 | unit | `test_websocket_endpoint.py` (qa): دور متحوّل عبر WS، و`notice` و`final` بمفاتيح `03 §3.2` نفسها، بلا مفتاح جديد | FR‑9 |
| AC‑5.1 | unit (contract) | `test_api_conventions.py`، اختبار جديد بنمط `:489`: خصائص `MessageOut` في `SPEC` و`LIVE` متساوية (التأكيد القائم `:495`). و`status` و`fallback` في `LIVE.MessageOut.required`. و`SPEC.status.enum == ["complete", "partial"]` و`nullable`. و`SPEC.MessageFallback.required == ["from", "to"]`، و`set(LIVE.MessageFallback.properties) == {"from", "to"}` (يثبت الاسم البديل). و`ProviderModel.required == {"provider", "model"}` في الاثنين | |
| AC‑5.2 | مراجعة | `03-api-spec.md` §2 و§3.1 | |
| AC‑5.3 | مراجعة | `06-domain-models.md` §4: الحقلان، وINV‑CV5، ونقض ب‑10 رقم 1، وحذف «تحويلها إلى حقل قرارٌ لاحق» | |
| AC‑5.4 | unit + integration | `test_conversations_schema_parity.py`: (أ) و(ب) الاختباران القائمان على أعمدة `messages` في الاتجاهين يغطيان العمودين تلقائياً. (ج) `ADD COLUMN status text NULL` و`ADD COLUMN fallback jsonb NULL` في السلسلة، والاسمان في `01-data-model.md`. (د) العمودان `nullable` و`server_default is None`، و`fallback.type.none_as_null is True`. (هـ) `ck_messages_status` و`NOT VALID` في `upgrade()` و`VALIDATE` غائب. (و) `downgrade` يحوي `DROP CONSTRAINT IF EXISTS ck_messages_status` و`DROP COLUMN IF EXISTS` للعمودين. `MIG` (CI): تنزيل إلى `0007_message_citations`، فيغيب العمودان والقيد (`information_schema`، `pg_constraint`)، والصف القديم سليم. ثم رفع إلى `0008`، فيعود العمودان `is_nullable = YES` والقيد `convalidated = false`. ثم `INSERT` بـ `status='error'` يُرفض بـ SQLSTATE `23514`، و`'partial'` يُقبل. والرفع في `finally` | NFR‑3، درس retro |
| AC‑5.5 | مراجعة | `01-data-model.md` §2.4 | |
| AC‑5.6 | مراجعة | تعليقات `orchestrator.py` (§3.5) | ت‑2 |
| AC‑5.7 | مراجعة | `gaps.md` §ث‑2: الإصلاح، وأن الفشل بلا رسالة باقٍ في ث‑3 | ت‑5 |
| AC‑6.1 | unit | `test_api_conversations_router.py`: تمديد اختبار ث‑1 للمساحة الأخرى، فيكون 404 problem+json، ولا `status`/`fallback` في الجسم | |
| AC‑6.2 | integration (CI) | `RLS`: رسالة `partial` مع `fallback` في A. ثم بـ `app.workspace_id = B`: `list_messages` صفر صفوف، و`SELECT status, fallback FROM conversations.messages WHERE id = :id` صفر صفوف. ورسالة user بـ `SELECT fallback IS NULL, status IS NULL` تعطي `true, true` (يثبت `none_as_null`) | NFR‑6 |
| AC‑6.3 | unit | اختبار 403 القائم من ث‑1 (`test_api_conversations_router.py`/`test_api_rbac.py`) يبقى أخضر. تأكيد غياب البيانات موجود | |
| AC‑6.4 | unit | اختبار 401 القائم يبقى أخضر. وتُضاف حالة «بلا ترويسة `Authorization`» إن لم تُضف بعد ملاحظة المراجعة 7 في ث‑1 | |
| AC‑6.5 | architecture | البوابة 4 (`lint-imports`) و`tests/architecture/`. المنسّق يستورد `ports.inbound` وحده، والـ domain يستورد `enum` و`dataclasses` فقط | NFR‑7 |

**تعديلات لازمة على fakes قائمة:**
- **`test_orchestrator.py:1284` و`test_workflow_orchestration.py:197`:** الـ fake `append` يقبل `status: str | None = None` و`fallback: FallbackRef | None = None`.
  - يسجلهما في `self.statuses` و`self.fallbacks`، **منفصلتين**. لا يُمد الـ tuple `self.appended`، حتى لا تنكسر تأكيداته القائمة.
  - يعيدهما في `AppendedMessage`.
  - علَم `drop_fallback` بنمط `_drop_citations`، ليختبر `_warn_if_fallback_dropped`.
- **`test_workers_bootstrap.py` (`_FakeAppendMessage`):** يقبل `status` ويسجله في `self.statuses`. وتبقى `self.appended` ثلاثية كما هي.

## 6. ترتيب التنفيذ والتوازي

**قاعدة كل commit أخضر:**
- اختبارا parity القائمان على أعمدة `messages` (`test_conversations_schema_parity.py:133-145`) يفشلان إن وصل الترحيل بلا عمود الـ `Table`، أو العكس.
- لذلك يدخل ترحيل `0008` وعمودا المحوّل في **commit واحد**:
  - data-engineer يكتب ملفاته في شجرة العمل على الفرع ولا يعمل لها commit؛
  - وbackend-developer يضمها إلى commit المحوّل (الخطوة 2‑د). وتذكر رسالة الـ commit الاثنين.
- كذلك DTO و`openapi.yaml` في commit واحد (`test_api_conventions.py:495`). وكلاهما لـ backend-developer.

1. **الفرع:** `feat/data-search-message-status` من `master` على `0976bcb`.
2. **بالتوازي:**
   - **(أ) data-engineer:** يكتب ولا يعمل commit:
     - `0008_message_status.py`؛
     - و`01-data-model.md` §2.4؛
     - وتمديد `test_conversations_schema_parity.py`؛
     - و`test_message_status_migration_live.py`؛
     - وتمديد `test_conversations_repository_rls.py`.

     يتفق مع backend-developer على الأسماء والأنواع: `status` من نوع `text`، و`fallback` من نوع `jsonb`، والقيد `ck_messages_status`.
   - **(ب) backend-developer، بالترتيب:**
     1. **domain:** `MessageStatus`، و`MessageFallback`، والحقلان، وINV‑CV5، واختبارات domain.
     2. **port:** `FallbackRef`، و`AppendedMessage`، و`append`.
     3. **application:** `_recordable_status` و`_recordable_fallback`، و`AppendMessage`، و`ConversationService.append`، واختبارات use cases.
     4. **adapter:** العمودان والمحوّلات، واختبارات `SQL`. **commit مشترك** مع ملفات 2‑أ.
     5. **orchestrator:** الكتّاب الثلاثة، والتعليقات، وتمديد `_FakeThreads` وfake الـ workflow، واختبارات US‑1 وAC‑2.5 وAC‑2.6 وAC‑4.4 وAC‑4.5.
     6. **workers:** `bootstrap.py` و`test_workers_bootstrap.py`.
     7. **api + العقد** في commit واحد: DTO والمحوّلان و`openapi.yaml` واختبار `test_api_conventions.py`، ثم اختبارات routers.
     8. **الوثائق:** `03-api-spec.md` و`06-domain-models.md`.
3. **البوابات 1–5 محلياً:** `ruff format --check`، و`ruff check`، و`mypy src`، و`lint-imports`، و`pytest tests/unit tests/architecture tests/eval`. ثم `deploy/prometheus/test-rules.sh`، وهو بلا قاعدة تنبيه جديدة، لكنه من البوابات. **لا تشغيل لـ `tests/integration` محلياً.**
4. **qa-engineer**، بعد 2‑ب‑5 على الأقل: `test_llm_fallback.py` و`test_websocket_endpoint.py` و`test-plan.md`، ومراجعة التغطية مقابل §5.
5. **backend-developer:** فقرة «الإصلاح» في `gaps.md` §ث‑2، بعد اخضرار البوابات.
6. **CI job `integration`** على الـ PR يثبت:
   - تطبيق `0008` عبر `provision`؛
   - ورحلة `downgrade`/`upgrade` (`MIG`)؛
   - وAC‑6.2، والذهاب والإياب الحي.

   يجب أن تكون نتيجته معروفة قبل بوابة ②.

**المراحل التي تُتخطى:**
- UX (3)، والواجهة (7)، وstaging (13)، وبوابة الإنتاج (14)، كما في `status.md`.
- مرحلة البيانات (5) **لازمة**، لأن هناك ترحيلاً.

## 7. المخاطر والقرارات

**لا بنود ⚠️ جديدة.** ما يمس العقد والتخزين والقيم حسمته بوابة ① (ق‑1…ق‑4 وس‑4…س‑12). والقرار الوحيد الذي يمس الأداء (قفل القيد، د‑ت3) يُحسم هنا بالخيار الذي لا كلفة له، فلا حاجة إلى قرار بشري. ما يلي قرارات تصميم داخل تلك الحدود، ومخاطر مُدارة.

**قرارات التصميم (بلا حاجة إلى قرار بشري):**
- **د‑ت1 — الحالة صارمة، و`fallback` ليّن في قيمه.**
  - الحالة ثابت في الشيفرة عند كل كاتب، والقيمة الخاطئة خطأ مبرمج. لذلك ترفع `ValidationError`، وهذا ما يطلبه AC‑1.13 وNFR‑4.
  - قيم `fallback` تأتي من إعداد المسارات. إن خالفت قاعدة الـ VO (فارغة، أو أطول من 255)، تُسقط إلى `None` مع تحذير واحد من المنسّق، ولا يضيع الجواب. وهذه قاعدة ث‑1 د‑ت1 نفسها، والجملة في النص تبقى شاهداً.
  - أما `fallback` بلا حالة، أو على رسالة غير المساعد، فخطأ مبرمج، ويرفع.
- **د‑ت2 — عمودان، وترحيل واحد `0008`.**
  - الحالة والتحول متعامدان.
  - عمود jsonb واحد يجمعهما يجعل الحالة المغلقة غير قابلة لـ CHECK بسيط.
  - وترحيلان لميزة واحدة يضاعفان نافذة القفل بلا فائدة.
- **د‑ت3 — `status text NULL` وعليه CHECK بـ `NOT VALID` بلا `VALIDATE`.** الخيارات:
  - **(أ) `NOT VALID` فقط** (الموصى به):
    - catalog فقط؛
    - والقيد مفروض على كل صف جديد؛
    - وكل صف قائم `NULL` بالبناء، فالتحقق مسح لا يثبت شيئاً.
  - **(ب) `NOT VALID` ثم `VALIDATE`**، كما في `files/0005` و`0006`: المسح يجري تحت `ACCESS EXCLUSIVE` نفسه، لأن Alembic يشغّل كل الترحيلات في معاملة واحدة. فيتجمد `conversations.messages`، أكبر جدول في المنصة (نحو مليون صف على بذرة السعة)، طوال المسح.
  - **(ج) بلا CHECK** (سابقة `citations`): يخالف NFR‑4، الذي يطلب «في القاعدة».
  - الأثر الوحيد للخيار (أ): `\d` يعرض القيد `NOT VALID`. ويحفظه `pg_dump` كما هو. ويُذكر ذلك في docstring الترحيل وفي `01 §2.4`.
- **د‑ت4 — `fallback` يُخزَّن بشكل `final.fallback` نفسه** (`{from:{provider,model}, to:{…}}`).
  - على الحد يعبر مسطّحاً (`FallbackRef`)، لأن `from` كلمة محجوزة.
  - التحويل إلى الشكل المتداخل في موضعين فقط: المحوّل (`_fallback_to_json`) والـ API (`_fallback_out`). وكلاهما يثبته اختبار مقابل `describe()`.
- **د‑ت5 — مصدر `fallback` المحفوظ هو `record.fallback` في المنسّق، لا مفتاح `fallback` على `final`.**
  - وكيل يضع المفتاح بنفسه (كما يفعل اختبار `test_orchestrator.py:3002`) يبقى على الإطار، فمفاتيح الوكيل لا تُسقط (`03 §3.1`). لكن الرسالة تُحفظ بـ `fallback: null`.
  - الحقل يقرر حقيقة تعرفها المنصة، لا ادعاء وكيل. وهذا يغطي أيضاً «بوابة الثقة» في `rag_agent` (A‑5).
  - الأثر: في هذه الحالة النادرة وحدها يختلف المحفوظ عن الإطار. ولا يقع ذلك لأي وكيل في المستودع اليوم خارج الاختبارات.
- **د‑ت6 — رسائل عامل التلخيص تُحفظ `complete`.**
  - `requirements.md` §5.1 لم يذكر هذين الكاتبين (`bootstrap.py:1079`، `:1174`).
  - الهدف المعتمد يقول: «تحمل كل رسالة مساعد جديدة حالة». و`null` على رسالة جديدة يعني «لم يُسجَّل» بلا سبب، كما قال المحلل في س‑7 عن خطوات الـ workflow.
  - رسالة الفشل («تعذّر بناء الملخص…») رسالة كاملة تخبر بفشل **المهمة**، كحالة مهمة الوسائط في A‑4، فهي `complete` لا `partial`.
  - الكلفة معامل واحد في كل استدعاء. يُبلَّغ product-owner به في القبول.
- **د‑ت7 — `fallback` يُشتق بالشرط نفسه الذي يضع جملة التحول** (`fallback.switched` في `_with_fallback_notice`)، عبر `_stored_fallback`. فلا يمكن أن يبدأ النص بالجملة و`fallback` قيمته `null`، أو العكس. والدور المختلط (`:789-792`) يعطي `null` في الاثنين.
- **د‑ت8 — `Literal["complete", "partial"] | None` على DTO،** ومعه محوّل صريح `_status_out`.
  - يولّد `enum` في المخطط الحي.
  - ويجعل قيمة خارج المجموعة 500 صريحاً لا نصاً مخترعاً (حافة US‑3).
  - وفي القراءة، `_status_from_row` يرفع على قيمة مجهولة، والقيد يجعلها غير قابلة للحدوث.
- **د‑ت9 — بلا `required` جديد على `MessageOut` في `openapi.yaml`،** كما في ث‑1 د‑ت7.

**المخاطر المُدارة:**
- **ترتيب النشر:** صورة ث‑2 على قاعدة غير مُرحَّلة تُفشل كل إدراج لرسالة، وكل قراءة للسجل (`UndefinedColumn`). وبث `final` لا يتأثر (NFR‑5). التخفيف: `migrate` يسبق `app` في الترتيب القائم، ولا نشر تلقائياً.
- **نافذة النشر المتدرج:** رسائل يكتبها replica قديم بعد الترحيل تبقى `NULL`، أي «لم يُسجَّل». هذا مطابق للدلالة، ولا backfill لها.
- **التخلّي أفضل جهد** (A‑6): AC‑1.5 يثبت القيمة إن اكتمل الحفظ، لا أن الحفظ يكتمل.
- **توقع Android لحالة «فشل بلا رسالة»** (ت‑5): ليس في ث‑2. تذكره فقرة `gaps.md` صراحة (AC‑5.7)، ويذكره وصف `status` في `openapi.yaml`.
- **قراءة صف تالف ترد 500 للصفحة كلها:** النمط نفسه في ملاحظة المراجعة 2 في ث‑1. غير قابل للحدوث، لأن القيد على `status`، والكتابة الوحيدة لـ `fallback` تمر بـ `MessageFallback`. ولا يُعالج هنا.
- **الاختبارات الحية لا تُشغَّل محلياً** (س‑10): AC‑5.4 (التطبيق والتراجع) وAC‑6.2 يثبتهما CI `integration` قبل بوابة ②.
- **انحراف SQLAlchemy** (retro ث‑1): `JSONB(none_as_null=True)` و`Text` يعملان على 2.0 و2.1. وكل اختبار يُجمِّع SQL يستعمل `_PG = asyncpg.dialect()` القائم في `SQL`، ولا يستعمل `postgresql.dialect()`.
- **ملاحظة خارج النطاق:** `CLAUDE.md` يذكر 8 عقود لـ `lint-imports`، والناتج 9 (retro ث‑1 بند 5). لا يُصلح هنا.

</div>
