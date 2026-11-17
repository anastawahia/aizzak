<div dir="rtl">

# التصميم التقني: ث‑5 قراءة السجل من الأحدث (cursor للخلف)

- **المدخلات الملزمة:**
  - `requirements.md` و`stories.md` في هذا المجلد (6 قصص، 37 معياراً).
  - قرارات بوابة ① في `status.md` (2026‑11‑17): ق‑1 (كل توصيات ت‑1…ت‑7 وس‑1…س‑10)، وق‑2 (الـ cursor الخلفي يحمل اتجاهه، والخلط 422 `common.invalid_cursor`، وشكل الـ cursor الأمامي لا يتغير)، وق‑3 (`backend-reply.md` لا يُحدَّث).
- **الشيفرة التي فُحصت:** `master` على `5d54b15`. أرقام الأسطر أدناه منه.
- **المرجع الأقرب الذي نتبعه:**
  - **صفحات `electrical`** (`src/app/modules/electrical/drawings/adapters/sql_repository.py:279`، `:1285`): حدّ `int4` بـ`decode_seq_cursor(cursor, maximum=_INT4_MAX)` قبل أي عبارة، واختبار الحافتين بلا قاعدة (`tests/unit/test_electrical_qa_pr_c_round2.py:129-140`).
  - **معامل الاستعلام المغلق:** `Annotated[Literal[...], Query()]` بقيمة افتراضية (`routers/usage.py:59`، `:103`)، ثم تحويله إلى `StrEnum` من domain الوحدة (`routers/electrical.py:474-482`، `DocumentStatus(status)`).
  - **الترقيم المطبوع 6.3‑أ/6.3‑ب** (`src/app/framework/pagination.py`): codec لكل keyset، وفكّ كلّي يخرج بـ`common.invalid_cursor`، والشرط والترتيب باتجاه واحد.
  - **اختبار الخطة الحي** (`tests/integration/test_hot_path_plans_live.py`): الفهرس بالاسم في الخطة، وإثبات القدرة على الفشل بالحذف داخل معاملة تُلغى، والمالك يرى خطة `app_rw` نفسها.
  - **تصميم ث‑3** (`docs/delivery/data-search-send-reconcile/design.md`): جدول الملفات بمنفّذ واحد لكل ملف، واختبار عبارات بلهجة `asyncpg` (`_PG`)، والاختبارات الحية في CI فقط.

## 1. الملخص

الميزة **قراءة فقط**، داخل وحدة `conversations` وحدها، ومعها codec في `app.framework.pagination`. لا ترحيل، ولا حدث، ولا إعداد، ولا سر.

1. **`app.framework.pagination`:** زوج codec ثالث `encode_desc_seq_cursor`/`decode_desc_seq_cursor` للاتجاه الخلفي على keyset `seq` نفسه. حمولته موسومة بالحرف `<` (`"<26"`). أما حمولة الاتجاه الأمامي (`"26"`) فلا تتغير بايتاً واحداً. كل فاكّ يرفض شكل الآخر، فالخلط 422 `common.invalid_cursor` (ق‑2، د‑ت1).
2. **`modules.conversations.domain`:** `MessageOrder(StrEnum)` بقيمتين `asc` و`desc` (د‑ت2).
3. **المنفذ وحالة الاستخدام:** `list_messages(..., order: MessageOrder = MessageOrder.ASC)`، بقيمة افتراضية، فلا يتغير أي مستدعٍ قائم. `ListMessages.execute` يمرر `order` كما هو، بعد الحراسة نفسها (404 قبل أي قراءة للرسائل).
4. **المحوّل SQL:** فرعان في الطريقة نفسها:
   - `ASC`: `seq > :cursor` و`ORDER BY seq`. النص المترجم مطابق لليوم.
   - `DESC`: `seq < :cursor` و`ORDER BY seq DESC`.
   - الشروط الثلاثة نفسها في الاتجاهين (`conversation_id`، و`workspace_id`، و`deleted_at IS NULL`)، و`LIMIT limit+1` نفسه. والفكّ في الاتجاهين بحدّ `int4` قبل فتح الجلسة (س‑5).
5. **`app.api`:** معامل `order: Literal["asc", "desc"] = "asc"` على `GET /conversations/{id}/messages` وحده. والقيمة الخاطئة 422 `common.validation_error` قبل حالة الاستخدام.
6. **العقد والوثائق:** `openapi.yaml` (المعامل ووصف للعملية)، و`03 §0` (بندا 6.3‑ب و6.3‑أ)، و`02 §2` (تعديلا 6.3‑أ و6.3‑ب)، وسطر VO في `06 §4`، والـ docstrings التي تقول «اتجاه واحد»، و`gaps.md` (ث‑5، وسطر الحالة، وتصحيح السطر 90).
7. **الأداء:** مدخل جديد في `app.ops.explain_hot_paths` للعبارة الخلفية، واختبار حي في CI يثبت `Index Scan Backward` على `uq_msg_seq`، بلا `Sort` ولا `Seq Scan`، وبالحذف المُلغى للقيد.

**الدلالة على السلك:**

| الطلب | ترتيب `data` | الشرط | `meta.next_cursor` |
|---|---|---|---|
| بلا `order`، أو `order=asc`، بلا cursor | `seq` تصاعدياً من أقدم رسالة ظاهرة | — | cursor أمامي لآخر عنصر (أكبر `seq`)، أو `null` |
| `order=asc` مع cursor أمامي | تصاعدياً | `seq > n` | كما فوق |
| `order=desc` بلا cursor | `seq` تنازلياً من أحدث رسالة ظاهرة | — | cursor خلفي لآخر عنصر (أصغر `seq`)، أو `null` |
| `order=desc` مع cursor خلفي | تنازلياً | `seq < n` | كما فوق |
| cursor خلفي بلا `order` أو مع `asc`، أو cursor أمامي مع `desc` | — | — | 422 `common.invalid_cursor`، بلا عبارة على `messages` |

## 2. الملفات

منفّذ واحد لكل ملف. لا ملف يعدّله اثنان.

| المسار | إنشاء/تعديل | المسؤولية | المنفّذ |
|---|---|---|---|
| `src/app/framework/pagination.py` | تعديل | `encode_desc_seq_cursor`، و`decode_desc_seq_cursor`، و`_DESC_TAG`، والمساعد الخاص `_seq_value` الذي يتشاركه الفاكّان (سلوك `decode_seq_cursor` لا يتغير). ونص الـ docstring في §3.6 | backend-developer |
| `src/app/modules/conversations/domain/value_objects.py` | تعديل | `MessageOrder(StrEnum)` (§3.2) | backend-developer |
| `src/app/modules/conversations/ports/repository.py` | تعديل | `order` في توقيع `list_messages`، وفقرة `list_messages` في docstring الصنف (`:35-41`) | backend-developer |
| `src/app/modules/conversations/application/use_cases.py` | تعديل | `ListMessages.execute(..., order=)`، والـ docstring (`:390-423`) | backend-developer |
| `src/app/modules/conversations/adapters/sql_repository.py` | تعديل | `_INT4_MAX`، والفرعان في `list_messages` (`:456-474`)، و`_desc_seq_cursor_of` بجانب `_seq_cursor_of` (`:729-730`)، وفقرة `list_messages` في docstring الوحدة (`:25-35`) | backend-developer |
| `src/app/api/v1/routers/conversations.py` | تعديل | المعامل `order` في `list_messages` (`:346-363`)، والـ docstring الذي يصير `description` في `/openapi.json`، وسطر المسار في docstring الوحدة (`:13`) | backend-developer |
| `src/app/ops/explain_hot_paths.py` | تعديل | مدخل `conversations.list_messages_newest_first` بعد `conversations.list_messages` (`:330-344`) | backend-developer |
| `docs/design/openapi.yaml` | تعديل | `listMessages` (`:273-280`): المعامل `order` و`description` (§3.1) | backend-developer |
| `docs/design/03-api-spec.md` (§0) | تعديل | بندا «اتّجاه الترتيب» و«المؤشّر معتِم» (`:26-27`)، نصاً في §3.7 | backend-developer |
| `docs/design/02-port-contracts.md` (§2) | تعديل | تعديلا 6.3‑أ و6.3‑ب (`:239-240`)، نصاً في §3.7 | backend-developer |
| `docs/design/06-domain-models.md` (§4) | تعديل | سطر `VO MessageOrder` بعد `VO MessageFallback` (`:95`) | backend-developer |
| `docs/delivery/data-search-android/gaps.md` | تعديل | سطر الحالة (`:11`)، وفقرة «الإصلاح» لـ ث‑5 (بعد `:55`)، وتصحيح `:90`. **بعد اخضرار البوابات وCI** (نمط ث‑3) | backend-developer |
| `tests/unit/support_conversations.py` | تعديل | `InMemoryConversationRepository.list_messages(..., order=)` يطابق المحوّل: الفاكّ نفسه بالحد نفسه، والاتجاهان، والـ cursor الخلفي. والـ docstring (`:9-14`) | backend-developer |
| `tests/unit/test_conversations_use_cases.py` | تعديل | `_FakeConversations.list_messages(..., order=)` يسجّل الاتجاه في قائمة منفصلة `list_messages_orders` (فلا يتغير `list_messages_calls` ولا الاختبار القائم `:568`). واختبارات التمرير | backend-developer |
| `tests/unit/test_workflow_orchestration.py` | **لا تعديل** | المزيّف (`:857-860`) يرفع `AssertionError` ولا يُستدعى، و`mypy` لا يفحص `tests/` | — |
| `tests/unit/test_foundation.py` | تعديل | اختبارات الـ codec الخلفي بجانب `test_seq_cursor_round_trips` (`:52-106`) | backend-developer |
| `tests/unit/test_conversations_sql_statements.py` | تعديل | عبارة الاتجاهين، ومطابقة النص الأمامي، وصفر عبارات عند الرفض، وحافتا `int4` | backend-developer |
| `tests/unit/test_electrical_qa_pr_c_round2.py` | تعديل | حذف `test_the_conversations_and_files_adapters_still_call_the_unbounded_forms` (`:148-150`) وحده، لأنه يثبّت غياب `maximum` من محوّل `conversations`، وهذا ما قرر المالك تغييره (س‑5، د‑ت6). ويبقى `test_conversation_style_seq_cursors_keep_the_bigint_range` (`:143-145`) كما هو، لأن `decode_seq_cursor` بلا `maximum` لا يتغير | backend-developer |
| `tests/unit/test_api_conversations_router.py` | تعديل | قسم جديد «ث‑5» في آخر الملف: US‑1 وUS‑2 وUS‑3 وUS‑4 على السلك. الاختبارات القائمة لا تُعدَّل (AC‑3.1) | backend-developer |
| `tests/unit/test_api_conventions.py` | تعديل | `test_the_order_parameter_is_declared_on_list_messages_alone` (AC‑3.6، AC‑6.1) | backend-developer |
| `tests/unit/test_ops_explain_hot_paths.py` | تعديل | التوأم الرخيص لقارئ اتجاه المسح في الاختبار الحي (§5). الاختبارات المعلمة القائمة تغطي المدخل الجديد تلقائياً | backend-developer |
| `tests/integration/test_conversations_repository_rls.py` | تعديل | قسم «ث‑5» بعد (10d) (`:523-535`): الصفحات الخلفية والحذف الناعم، والعزل، ورفض الخلط وحدّ `int4` حياً. CI فقط | backend-developer |
| `tests/integration/test_hot_path_plans_live.py` | تعديل | fixture سجل بـ 10,000 رسالة، وأربعة اختبارات للخطة الخلفية (§5). CI فقط | backend-developer |

**ما لا يتغير، عمداً:**
- **`migrations/`:** لا ملف جديد ولا معدّل (NFR‑1، AC‑5.3). فلا مرحلة لـ data-engineer.
- **`src/app/framework/di/composition_root.py`:** `ListMessages(repository)` (`:1346`) بلا تغيير، لأن المُنشئ لا يتغير.
- **`src/app/api/v1/dto/pagination.py`:** `Limit` و`Cursor` و`Page` و`PageMeta` كما هي. `order` معامل خاص بمسار واحد، فيُعلن في الراوتر لا في الوحدة المشتركة (ت‑2).
- **`MessageOut` و`_to_message_out`:** بلا تغيير (FR‑7).
- **`tests/integration/test_replay_ops_live.py:110`** والمستدعون الآخرون لـ`list_messages`: القيمة الافتراضية `ASC` تحفظ سلوكهم.
- **`docs/Requirements-v1.md` (`API‑03`):** بلا تعديل. المعامل يُذكر في `03 §0` على أنه خاص بـ`listMessages` (ت‑2).
- **`docs/design/01-data-model.md`، و`events/schemas/`، و`.importlinter`، و`deploy/`، و`.github/workflows/`، و`.env.example`:** بلا تغيير.
- **مستودع Android:** لا يُمس (ق‑3، AC‑6.4).

## 3. العقود

### الواجهات البرمجية

| الطريقة | المسار | الطلب | الاستجابة | الأخطاء | الصلاحية |
|---|---|---|---|---|---|
| GET | `/api/v1/conversations/{id}/messages` | استعلام: `limit` (1..100، الافتراضي 20، بلا تغيير)، و`cursor` (نص معتم، بلا تغيير)، و**`order` جديد**: `asc` \| `desc`، اختياري، الافتراضي `asc` | 200 `MessagePage` = `{data: MessageOut[], meta: {next_cursor, limit}}` بلا تغيير في الشكل. ترتيب `data` ومعنى `next_cursor` حسب `order` (جدول §1) | 401 بلا توكن. 403 `authz.forbidden` بلا `conversations:read`. 404 `common.not_found` لمحادثة مجهولة أو محذوفة ناعماً أو من مساحة أخرى. 422 `common.validation_error` لـ`order` غير `asc`/`desc` (ومنه `DESC` و`""`) أو `limit` خارج المدى. 422 `common.invalid_cursor` لـ cursor مشوّه، أو من مجموعة أخرى، أو من الاتجاه الآخر، أو بـ`seq` خارج `int4` | `conversations:read` (بلا تغيير) |

**ترتيب الحراسات (بلا تغيير عن اليوم):** المصادقة، ثم الصلاحية، ثم تحقق FastAPI من `limit` و`order` (422 `common.validation_error`)، ثم قراءة المحادثة (404)، ثم فكّ الـ cursor في المحوّل (422 `common.invalid_cursor`) قبل فتح الجلسة، ثم عبارة واحدة. فمحادثة مجهولة مع cursor تالف تبقى 404 كما اليوم (د‑ت5).

#### 3.1 `docs/design/openapi.yaml` — `listMessages` (`:273-280`)

يُستبدل سطر `parameters` الواحد بقائمة، ويضاف `description`. الباقي بلا تغيير:

```yaml
  /conversations/{id}/messages:
    get:
      tags: [conversations]
      operationId: listMessages
      description: "The thread's messages, soft-deleted ones excluded (a soft-deleted message keeps its `seq`, so `seq` may have gaps). `order=asc` (the default) reads forward: oldest first, and `meta.next_cursor` continues after the last (newest) message of the page. `order=desc` reads backward: without a cursor the page is the newest `limit` messages, newest first, and `meta.next_cursor` continues before the last (oldest) message of the page. `meta.next_cursor` is `null` only when no message remains in that direction at the time of the read; a page of exactly `limit` messages is followed by `null`, never by an empty page. A cursor belongs to the direction that issued it: sending it with the other `order` (no `order` means `asc`) is 422 `common.invalid_cursor`, as is a malformed cursor or one from another collection. Cursors issued before `order` existed are `asc` cursors and stay valid. Messages added after a backward read began do not appear on its later pages. To catch up, read `order=desc` without a cursor, follow `meta.next_cursor` until a page holds a `seq` you already have, and merge by `id`. Permission `conversations:read`."
      parameters:
        - { $ref: "#/components/parameters/Id" }
        - { $ref: "#/components/parameters/Limit" }
        - { $ref: "#/components/parameters/Cursor" }
        - { name: order, in: query, required: false, description: "Reading direction by `seq`: `asc` oldest first, `desc` newest first. Lower case only; any other value is 422 `common.validation_error`.", schema: { type: string, enum: [asc, desc], default: asc } }
      responses:
        "200": { description: OK, content: { application/json: { schema: { $ref: "#/components/schemas/MessagePage" } } } }
        default: { $ref: "#/components/responses/Problem" }
```

- المعامل inline، لا مكوّن في `components/parameters`، لأنه لعملية واحدة (سابقة `period` في `:768`).
- `PageMeta.next_cursor` (`:1060-1062`) يبقى «null on the last (or unbounded) page». وصف العملية يحدد «الأخيرة» بكل اتجاه.

#### 3.2 `/openapi.json` المولَّد وعميل Android

- يولِّد FastAPI المعامل من `Annotated[Literal["asc", "desc"], Query()] = "asc"`: `{name: order, in: query, required: false, schema: {type: string, enum: [asc, desc], default: asc}}`. يقارنه اختبار الاتفاقية بالعقد (§5، AC‑3.6).
- لا frontend في هذا المستودع. عميل Android يعيد التوليد من `/openapi.json` الحي أو من `openapi.yaml` متى قرر. والإضافة غير كاسرة (`API‑05`): المعامل اختياري وافتراضيه سلوك اليوم.

### التواقيع

#### 3.3 framework — `src/app/framework/pagination.py`

```python
#: The backward ``seq`` codec's payload prefix (gap th-5). Never a digit, a sign or
#: whitespace, so ``int()`` refuses a tagged payload and a forward payload can never
#: carry it.
_DESC_TAG: Final = "<"


def encode_seq_cursor(last_seq: int) -> str:                      # UNCHANGED
    """Encode a ``seq`` keyset position (a message's ordinal) as a cursor."""
    return _encode(str(last_seq))


def decode_seq_cursor(cursor: str, *, maximum: int | None = None) -> int:   # behaviour UNCHANGED
    raw = _decode(cursor)
    return _seq_value(raw, maximum)


def encode_desc_seq_cursor(last_seq: int) -> str:
    """Encode a ``seq`` position for a NEWEST-FIRST read: the next page is ``seq < last_seq``."""
    return _encode(_DESC_TAG + str(last_seq))


def decode_desc_seq_cursor(cursor: str, *, maximum: int | None = None) -> int:
    """Decode a newest-first ``seq`` cursor, or reject it -- including a forward one."""
    raw = _decode(cursor)
    if not raw.startswith(_DESC_TAG):
        _reject()
    return _seq_value(raw.removeprefix(_DESC_TAG), maximum)


def _seq_value(raw: str, maximum: int | None) -> int:
    """The ordinal a ``seq`` payload carries, bounded to ``maximum`` in magnitude."""
    try:
        value = int(raw)
    except ValueError:
        _reject()
    if maximum is not None and abs(value) > maximum:
        _reject()
    return value
```

**الترميز بالضبط (ق‑2):**

| الاتجاه | الحمولة قبل base64url | مثال `seq = 26` | مثال `seq = 20` |
|---|---|---|---|
| أمامي (بلا تغيير) | `str(seq)` | `"26"` ⇒ `MjY` | `"20"` ⇒ `MjA` |
| خلفي (جديد) | `"<" + str(seq)` | `"<26"` ⇒ `PDI2` | `"<20"` ⇒ `PDIw` |

**جدول الفكّ:**

| المدخل | `decode_seq_cursor` (أمامي) | `decode_desc_seq_cursor` (خلفي) |
|---|---|---|
| base64 غير صالح، أو خارج الأبجدية، أو ليس UTF‑8 (`!!!!`) | `invalid_cursor` من `_decode` (اليوم) | `invalid_cursor` من `_decode` |
| حمولة أمامية `"26"` | 26 | `invalid_cursor` (بلا الوسم) |
| حمولة خلفية `"<26"` | `invalid_cursor` (`int("<26")` يفشل) | 26 |
| cursor `id` (UUID) من `list_by_agent` | `invalid_cursor` (اليوم) | `invalid_cursor` (بلا الوسم) |
| `"<"` وحده، أو `"<<26"`، أو `"<abc"` | `invalid_cursor` | `invalid_cursor` (`int("")`، `int("<26")` يفشلان) |
| `|seq| > maximum` (`2^31` أو `-(2^31)` أو `2^63`) | `invalid_cursor` حين يمرَّر `maximum` | `invalid_cursor` حين يمرَّر `maximum` |

- **`int()` نفسها في الاتجاهين.** ما يقبله الفاكّ الأمامي اليوم (مثل `" 5"` أو `"+5"`) يقبله الخلفي بعد الوسم. هذا مقصود: تشديد الأمامي يكسر FR‑2، والقيمة محدودة المدى ومربوطة معاملاً في كل حال.
- **`decode_seq_cursor` بلا `maximum`** يبقى بمدى bigint كما يثبته `test_conversation_style_seq_cursors_keep_the_bigint_range`. الحدّ يأتي من المستدعي (المحوّل)، كسابقة `electrical`.

#### 3.4 domain — `src/app/modules/conversations/domain/value_objects.py`

بعد `MessageRole` (`:44-51`):

```python
class MessageOrder(StrEnum):
    """Which way a transcript is read by ``seq`` (gap th-5): ``ASC`` oldest first -- the
    default, and the only direction before th-5 -- or ``DESC`` newest first. A read
    concern only: it never touches the aggregate or INV-CV1."""

    ASC = "asc"
    DESC = "desc"
```

#### 3.5 port وapplication وadapter

**`ports/repository.py`** (`:118-120`). يستورد `MessageOrder` من `app.modules.conversations.domain.value_objects`:

```python
    async def list_messages(
        self,
        ctx: ExecutionContext,
        conversation_id: Uuid,
        *,
        limit: int,
        cursor: str | None,
        order: MessageOrder = MessageOrder.ASC,
    ) -> Page[Message]: ...
```

**`application/use_cases.py`** (`ListMessages.execute`، `:409-423`):

```python
    async def execute(
        self,
        ctx: ExecutionContext,
        conversation_id: Uuid,
        *,
        limit: int,
        cursor: str | None = None,
        order: MessageOrder = MessageOrder.ASC,
    ) -> Page[Message]:
        conversation = await self._conversations.get(ctx, conversation_id)
        if conversation is None or conversation.deleted_at is not None:
            raise NotFoundError("conversation not found")
        return await self._conversations.list_messages(
            ctx, conversation_id, limit=limit, cursor=cursor, order=order
        )
```

`application` يستورد من domain وحده، فلا `fastapi` ولا `sqlalchemy` (NFR‑7، AC‑5.6).

**`adapters/sql_repository.py`** (`:456-474`، و`:729-730`):

```python
_INT4_MAX: Final = 2**31 - 1  # messages.seq is an int4 (0001_conversations)


    async def list_messages(
        self,
        ctx: ExecutionContext,
        conversation_id: UuidStr,
        *,
        limit: int,
        cursor: str | None,
        order: MessageOrder = MessageOrder.ASC,
    ) -> Page[Message]:
        # Soft-deleted messages are excluded from retrieval (INV-CV3); their
        # `seq` stays reserved, so a page can legitimately show a gap -- in
        # either direction.
        backward = order is MessageOrder.DESC
        conditions = [
            messages.c.conversation_id == conversation_id,
            messages.c.workspace_id == ctx.workspace_id,
            messages.c.deleted_at.is_(None),
        ]
        # Decoded BEFORE the session opens: a refused cursor runs no statement.
        # The predicate and the ORDER BY point the same way (6.3-b), per branch.
        if cursor is not None:
            if backward:
                conditions.append(
                    messages.c.seq < decode_desc_seq_cursor(cursor, maximum=_INT4_MAX)
                )
            else:
                conditions.append(messages.c.seq > decode_seq_cursor(cursor, maximum=_INT4_MAX))
        ordering = messages.c.seq.desc() if backward else messages.c.seq
        stmt = select(messages).where(*conditions).order_by(ordering).limit(limit + 1)
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return _paginate(
            rows,
            limit,
            _hydrate_message,
            next_cursor_of=_desc_seq_cursor_of if backward else _seq_cursor_of,
        )


def _desc_seq_cursor_of(row: RowMapping) -> str:
    return encode_desc_seq_cursor(row["seq"])
```

- `_paginate` (`:733-744`) بلا تغيير: يطلب `limit + 1`، ويرمّز آخر صف في الصفحة. في الاتجاه الخلفي آخر صف هو أصغر `seq` (FR‑5).
- **`ordering` في الفرع الأمامي هو `messages.c.seq` نفسه** لا `.asc()`، لأن `.asc()` يضيف `ASC` إلى النص المترجم فيكسر AC‑3.4.

**العبارتان كما تُترجمان (منطقياً):**

```sql
-- order=asc (UNCHANGED, AC-3.4)
SELECT conversations.messages.* FROM conversations.messages
WHERE conversations.messages.conversation_id = :conversation_id
  AND conversations.messages.workspace_id = :workspace_id
  AND conversations.messages.deleted_at IS NULL
  [AND conversations.messages.seq > :cursor_seq]
ORDER BY conversations.messages.seq
LIMIT :limit_plus_one

-- order=desc (NEW, AC-5.1)
SELECT conversations.messages.* FROM conversations.messages
WHERE conversations.messages.conversation_id = :conversation_id
  AND conversations.messages.workspace_id = :workspace_id
  AND conversations.messages.deleted_at IS NULL
  [AND conversations.messages.seq < :cursor_seq]
ORDER BY conversations.messages.seq DESC
LIMIT :limit_plus_one
```

**الحذف الناعم في الاتجاهين** (FR‑8): `deleted_at IS NULL` شرط على الصف في العبارتين، والـ cursor موضع `seq` لا عدّاد صفوف. فرسالة حُذفت بعد إصدار الـ cursor تغيب عن الصفحة التالية بلا إزاحة، ورسالة حُذفت وقد قُرئت لا أثر لها (AC‑2.4). وهذا سلوك الاتجاه الأمامي اليوم نفسه.

**المدخل الجديد في `src/app/ops/explain_hot_paths.py`** (بعد `:344`):

```python
    HotPath(
        name="conversations.list_messages_newest_first",
        kind="page",
        table="conversations.messages",
        source="modules/conversations/adapters/sql_repository.py"
        "::SqlConversationRepository.list_messages",
        backs="§0 `browse` -- a long thread opened on its latest turns (th-5, `order=desc`)",
        needs=("workspace_id", "conversation_id"),
        sql="""
        SELECT * FROM conversations.messages
        WHERE conversation_id = :conversation_id
          AND workspace_id = :workspace_id
          AND deleted_at IS NULL
        ORDER BY seq DESC LIMIT :page
        """,
    ),
```

#### 3.6 api — `src/app/api/v1/routers/conversations.py` (`:346-363`)

```python
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query, Request
from app.modules.conversations.domain.value_objects import MessageOrder


async def list_messages(
    services: Services,
    ctx: Context,
    conversation_id: str,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    order: Annotated[Literal["asc", "desc"], Query()] = "asc",
) -> Page[MessageOut]:
    """The thread's turns, soft-deleted ones excluded: oldest first by ``seq``, or newest
    first with ``order=desc``. A cursor is spent in the direction that issued it."""
    page = await services.conversations.list_messages.execute(
        ctx, conversation_id, limit=limit, cursor=cursor, order=MessageOrder(order)
    )
    return Page(
        data=[_to_message_out(message) for message in page.data],
        meta=PageMeta(next_cursor=page.next_cursor, limit=page.limit),
    )
```

- `Literal` عند الحد، و`StrEnum` من domain بعده، كسابقة `electrical` (`:474`، `:480`). فـ`MessageOrder` لا يُنشر مكوّناً في `/openapi.json`.
- `Literal` حساس لحالة الأحرف، و`?order=` يصل نصاً فارغاً. فـ`DESC` و`""` و`foo` كلها 422 `common.validation_error` قبل حالة الاستخدام (FR‑10).

#### 3.7 نصوص الوثائق والـ docstrings (backend-developer)

**`src/app/framework/pagination.py`، الفقرة `:16-21`.** تُستبدل جملتها الأخيرة:
- **قبل:** «A discriminator in the envelope would buy a better error message for a case the types already close.»
- **بعد:** «Across keysets a discriminator in the envelope would buy a better error message for a case the types already close. WITHIN one keyset the types close nothing, and that is the next paragraph.»

ثم تضاف بعدها فقرة جديدة:

> **A ``seq`` cursor carries its direction** (gap th-5). ``list_messages`` reads ``seq`` both ways, so a forward and a backward cursor share one keyset, and a forward cursor replayed backward would decode cleanly into a position that means something else: the client asks for older messages and gets newer ones, duplicates and all, with nothing anywhere reporting a fault. So the backward codec tags its payload (``"<42"``) and each decoder refuses the other's form: ``int("<42")`` fails in ``decode_seq_cursor``, and ``decode_desc_seq_cursor`` refuses a payload without the tag. The forward payload is unchanged byte for byte, so every forward cursor a client already holds still decodes to the same position.

**الفقرة `:23-33`.** تُستبدل جملتاها الأخيرتان:
- **قبل:** «The ONE exception is ``list_messages``: a transcript is a narrative, not an inbox, so it reads forward by ``seq``.»
- **بعد:** «The exception is ``list_messages``: a transcript is a narrative, not an inbox, so it reads FORWARD by ``seq`` by default (``seq > cursor``, ``ORDER BY seq``). Since gap th-5 it also reads BACKWARD on request (``order=desc``: ``seq < cursor``, ``ORDER BY seq DESC``), so a client can open a long thread on its latest turns in one request. Each direction obeys the rule above on its own.»

ويتغير عنوان الفقرة من «**One direction, and it is NEWEST FIRST** (6.3-ب)» إلى «**NEWEST FIRST, with one collection that also reads forward** (6.3-ب)».

**`adapters/sql_repository.py`، docstring الوحدة `:25-35`.** يصير مطلع الفقرة: «``list_messages`` reads that same child table back by ``seq`` -- oldest first by default, newest first with ``MessageOrder.DESC`` (gap th-5) -- and DOES filter ``deleted_at IS NULL`` in both directions: …». وتنتهي الفقرة بإضافة: «The backward direction has its own codec, ``encode_desc_seq_cursor``/``decode_desc_seq_cursor``, whose tag makes a cursor unspendable in the other direction, and both decoders are bounded to the ``int4`` column (``_INT4_MAX``), so an out-of-range position is a 422 before any statement, as in ``electrical``.»

**`ports/repository.py`، `:35-37`.** تصير الجملة الأولى: «``list_messages`` (6.1-ج-1) is the read side of that append-only child: one conversation's messages by ``seq``, oldest first or -- with ``order=MessageOrder.DESC`` (gap th-5) -- newest first, soft-deleted ones excluded (INV-CV3).»

**`use_cases.py`، `ListMessages` (`:391`).** يصير السطر الأول: «List one conversation's messages by ``seq`` (INV-CV1), oldest first or newest first (``order``, gap th-5).»

**`docs/design/03-api-spec.md` §0، السطر `:26`، يُستبدل كله بـ:**

> - **اتّجاه الترتيب (تعديل 6.3‑ب، وث‑5):** كلّ مجموعةٍ مرقّمة تُعاد **الأحدث أوّلاً** (‏keyset تنازليّ على `id` الذي هو UUIDv7 زمنيّ الترتيب)، إلا `listMessages`: يُقرأ نصّ المحادثة **إلى الأمام** بـ`seq` افتراضياً، و**للخلف** (الأحدث أوّلاً) بـ`?order=desc`. المعامل `order` (‏`asc|desc`، الافتراضي `asc`) خاصٌّ بـ`listMessages`، وليس جزءاً من `API‑03` في غيره. و`meta.next_cursor` في الاتجاه الخلفي يكمل بما قبل أقدم رسالة في الصفحة، و`null` يعني أنه لا رسائل أقدم.

**والسطر `:27`، يُستبدل كله بـ:**

> - **المؤشّر معتِم وكلّيّ الفكّ (تعديل 6.3‑أ، وث‑5):** أيّ مؤشّرٍ مشوَّه — base64 غير صالح، أو خارج الأبجديّة، أو صالحٌ يحمل مفتاحاً من مجموعةٍ أخرى، أو موضعاً خارج مدى العمود الذي يُقارَن به، أو مؤشّرٌ من اتّجاهٍ غير المطلوب في `listMessages` — يُرَدّ **422 `common.invalid_cursor`**، ولا يبلغ أيّ استعلام.

**`docs/design/02-port-contracts.md` §2، السطر `:239`.** تضاف في آخره جملة:

> ولأنّ اتّجاهَي `list_messages` يتشاركان المفتاح `seq`، فالنوع لا يكفي وسماً بينهما (ث‑5): `encode_desc_seq_cursor`/`decode_desc_seq_cursor` للاتّجاه الخلفي، وحمولتهما موسومة بـ`<`، فمؤشّرُ اتّجاهٍ مرفوضٌ في الآخر بـ`common.invalid_cursor`. وحمولة الاتّجاه الأمامي لم تتغيّر، فمؤشّراته القديمة صالحة.

**والسطر `:240`، يُستبدل مطلعه:**
- **قبل:** «الاستثناء الوحيد `list_messages` (‏`seq` إلى الأمام).»
- **بعد:** «إلا `list_messages`: `seq` إلى الأمام افتراضياً (‏`seq > cursor`، `ORDER BY seq`)، وللخلف بـ`order=MessageOrder.DESC` (‏`seq < cursor`، `ORDER BY seq DESC`، ث‑5).»
- وبقية السطر («الشرط والترتيب يجب أن يشيرا الاتّجاه نفسه. و`DocumentRepository.list`…») بلا تغيير.

**`docs/design/06-domain-models.md` §4، بعد `:95`:**

```text
VO  MessageOrder     (asc | desc) — اتجاه قراءة السجل بـ`seq` في `list_messages`؛ قراءةٌ فقط، لا تمسّ الـaggregate (ث‑5)
```

**`docs/delivery/data-search-android/gaps.md`** (بعد اخضرار CI، والتاريخ ورقم الـ PR يُملآن عند الكتابة):

- **السطر `:11`** يصير: «الحالة (2026‑11‑ي ي): … **وأُصلحت ث‑3** (PR #41، دُمج في `master`). **وأُصلحت ث‑5** (PR #NN، دُمج في `master`). **الباقية ث‑7 مفتوحة**، وتحتاج خطة منفصلة لأنها تغيّر قاعدة البيانات.»
- **بعد `:55`** يضاف:

> - **الإصلاح (2026‑11‑ي ي):** يقبل `GET /conversations/{id}/messages` معاملاً اختيارياً `order` بقيمتين: `asc` (الافتراضي، سلوك اليوم حرفاً بحرف) و`desc`. مع `order=desc` تبدأ الصفحة الأولى بلا cursor بأحدث `limit` رسالة ظاهرة، من الأحدث إلى الأقدم. و`meta.next_cursor` يكمل بما قبل أقدم رسالة في الصفحة، و`null` يعني أنه لا رسائل أقدم وقت القراءة. الـ cursor يحمل اتجاهه: إرساله مع الاتجاه الآخر (وغياب `order` يعني `asc`) يعيد 422 `common.invalid_cursor`. وشكل الـ cursor الأمامي لم يتغير، فالـ cursors القديمة صالحة. وصار cursor يحمل `seq` خارج مدى العمود 422 `common.invalid_cursor` في الاتجاهين، لا خطأ قاعدة بيانات. لا ترحيل: الفهرس القائم `uq_msg_seq` يخدم الاتجاهين، وتثبته خطة `EXPLAIN` في CI. ولا `prev_cursor`: للحاق بالجديد يقرأ العميل `order=desc` بلا cursor حتى أول صفحة فيها `seq` يملكه، ويدمج بالمعرّف `id`.
> - ⚠️ **للعميل:** السطر 83 من `backend-reply.md` («لا يوجد cursor للخلف ولا طريقة لبدء الصفحة من الأحدث») لم يعد صحيحاً. ولم يُعدَّل `backend-reply.md` عمداً (ق‑3). العقد في `openapi.yaml` و`/openapi.json` الحي.

- **السطر `:90`** يُستبدل كله بـ:

> - **(صُحّح في ث‑5):** الـ cursor التالف، أو من مجموعة أخرى، أو من الاتجاه الآخر في السجل، يعيد 422 `common.invalid_cursor`. الرمز موجود منذ 6.3 (`src/app/framework/errors.py:80`، `03 §0`). والسطر 198 من `backend-reply.md` الذي يقول غير ذلك لم يُعدَّل (ق‑3).

## 4. البيانات

**لا تغيير.** لا ترحيل، ولا عمود، ولا فهرس، ولا قيد، ولا سياسة RLS، ولا دور.

- **الفهرس الذي يخدم الاتجاهين:** `uq_msg_seq UNIQUE (conversation_id, seq)` (`migrations/versions/conversations/0001_conversations.py:79`). القيد الفريد فهرس btree على العمودين. `conversation_id = :id` يثبّت العمود الأول، فيأتي `seq` مرتباً. ويُمسح الفهرس للخلف لـ`ORDER BY seq DESC` (`Index Scan Backward`)، ويقف عند `LIMIT`. و`workspace_id` و`deleted_at IS NULL` وقيد RLS مرشّحات على الصف، كالاتجاه الأمامي اليوم.
- **التوافق والرجوع:** لا ترحيل، فلا شيء يُرجَع في القاعدة. والرجوع عن الميزة revert للـ commit. الـ cursors الخلفية التي أُصدرت تصير عندها 422 `common.invalid_cursor` (الفاكّ الأمامي يرفض الوسم)، لا صفحة خاطئة. والـ cursors الأمامية لا تتأثر في الاتجاهين.
- **عزل المستأجر:** بلا تغيير. طبقتان كما اليوم: `SET LOCAL app.workspace_id` من `tenant_session`، و`WHERE workspace_id` صريح في العبارتين. ويثبت ذلك اختبار RLS الحي (AC‑5.4).
- **إثبات استعمال الفهرس** (NFR‑3، NFR‑4، AC‑5.2)، في `tests/integration/test_hot_path_plans_live.py`، CI job `integration` فقط:
  - **fixture `seeded_transcript`:** مساحة واحدة. محادثة هدف فيها **10,000** رسالة (`seq` 1..10,000)، أحدث **5** منها محذوفة ناعماً (`deleted_at` في الإدراج نفسه). ومحادثة أخرى في المساحة نفسها بـ 2,000 رسالة، حتى يكون للعمود الأول في الفهرس معنى. الكتابة بـ`executemany` بصفة `app_rw` تحت GUC المساحة (نمط `_write`، `:140-173`). ثم `VACUUM ANALYZE conversations.conversations, conversations.messages` بصفة المالك في AUTOCOMMIT.
  - **الخطة:** `EXPLAIN (ANALYZE, BUFFERS, VERBOSE, FORMAT JSON)` لمدخل `conversations.list_messages_newest_first` بـ`page = 21`، داخل معاملة تُلغى دائماً، تحت `SET LOCAL app.workspace_id`.
  - **التأكيدات:**
    - في الخطة عقدة `Node Type = "Index Scan"` و`Index Name = "uq_msg_seq"` و`Scan Direction = "Backward"`.
    - لا عقدة `Sort`.
    - `summarise(...).seq_scans` لا يحوي `conversations.messages`.
    - `"current_setting"` في الخطة (أخذت تحت قيد RLS).
    - `rows_returned == 21` و`rows_read <= 21 + 5` (NFR‑4: الصفحة وما مرّت عليه من المحذوفة، لا طول المحادثة).
  - **القدرة على الفشل:** بصفة المالك، داخل المعاملة نفسها التي تُلغى، `ALTER TABLE conversations.messages DROP CONSTRAINT uq_msg_seq`. **لا `DROP INDEX`**، لأن Postgres يرفض حذف فهرس يملكه قيد. وبعدها:
    - تختفي عقدة المسح الخلفي على `uq_msg_seq`.
    - وتظهر `Sort` أو `Seq Scan` على `conversations.messages`.
    - ثم اختبار مستقل يثبت أن القيد عاد بعد الإلغاء (`pg_constraint`).
  - **ولا يصح الحذف إلا إن رأى المالك خطة `app_rw` نفسها:** يُقارن `_backward_scans` للاثنين بلا حذف (نمط `test_the_owner_gets_the_same_plan_as_app_rw`، `:425-435`).
- **إن فشل AC‑5.2 في CI:** لا يُضاف فهرس ولا ترحيل بلا قرار بشري. يعود الأمر إلى المصمم ومهندس البيانات (`stories.md` US‑5).

## 5. خطة الاختبار المقترحة

**المستويات:**
- **unit:** تحت `tests/unit/`، وتُشغَّل محلياً: `pytest tests/unit tests/architecture tests/eval`.
- **integration:** تحت `tests/integration/` (`live_db`). تُكتب الآن، وتُشغَّل في CI job `integration` فقط (س‑9).
- **مراجعة:** فحص يدوي يقوم به code-reviewer.

**مفاتيح:**
- `F` = `test_foundation.py`. و`SQL` = `test_conversations_sql_statements.py` (لهجة `_PG`، و`_RecordingSession` الذي يسجّل كل عبارة). و`UC` = `test_conversations_use_cases.py`.
- `R` = `test_api_conversations_router.py`، قسم «ث‑5»:
  - مساعد جديد `_seed(stack, n, *, deleted=())` يكتب `n` رسالة عبر `stack.repository.append_message` بـ`Message` كامل الحقول، ثم يضبط `deleted_at` للمذكورة.
  - ومساعد `_walk(client, conversation_id, *, order, limit)` يتابع `next_cursor` حتى `null` ويعيد الصفحات.
  - و`_messages` القائم (`:1157`) للطلب الواحد.
- `C` = `test_api_conventions.py`. و`X` = `test_ops_explain_hot_paths.py`.
- `RLS` = `tests/integration/test_conversations_repository_rls.py`. و`PLAN` = `tests/integration/test_hot_path_plans_live.py`.
- «بلا عبارة» = `SQL`: `statements == []` بعد `pytest.raises(ValidationError)` برمز `common.invalid_cursor`، على المحوّل الحقيقي.

**اختبارات الـ codec (`F`)، أساس US‑3 وUS‑4:**
- `test_seq_cursor_payload_is_unchanged`: `encode_seq_cursor(20) == "MjA"` و`encode_seq_cursor(26) == "MjY"` (حرفياً، يثبت FR‑2).
- `test_desc_seq_cursor_round_trips`: لـ`0` و`1` و`4_211` و`2**31 - 1`.
- `test_desc_seq_cursor_is_tagged`: `encode_desc_seq_cursor(26) == "PDI2"`.
- `test_seq_cursors_refuse_the_other_direction`: كل فاكّ يرفض شكل الآخر برمز `common.invalid_cursor`.
- `test_decode_desc_seq_cursor_rejects_garbage`: `"!!!!"`، و`""`، وcursor `id`، و`"<"`، و`"<<26"`، و`"<abc"`.
- `test_desc_seq_cursor_honours_maximum`: `2**31` و`-(2**31)` مرفوضان مع `maximum=2**31 - 1`، و`2**31 - 1` و`-(2**31 - 1)` مقبولان.

| AC | المستوى | المكان والاسم | ما يُثبت |
|---|---|---|---|
| AC‑1.1 | unit | `R` `test_desc_first_page_is_the_newest_twenty_newest_first`: محادثة 45، `?order=desc`. `seq` = 45..26، و`meta.limit == 20`، و`next_cursor` نص غير فارغ | FR‑3، FR‑6 |
| AC‑1.2 | unit | `R` `test_desc_short_thread_is_one_page_with_a_null_cursor`: 5 رسائل، `limit=20` ⇒ 5..1 و`null` | FR‑5 |
| AC‑1.3 | unit + integration | `R` `test_desc_exactly_limit_messages_has_no_phantom_page`: 20 رسالة، `limit=20` ⇒ عشرون و`null`. و`SQL`: `LIMIT` = 21 | FR‑5 |
| AC‑1.4 | unit | `R` `test_desc_of_a_fresh_thread_is_an_empty_page`: الجسد = `{"data": [], "meta": {"next_cursor": None, "limit": 20}}` | |
| AC‑1.5 | unit + integration | `R` `test_desc_first_page_skips_the_soft_deleted_newest`: 45 حُذفت منها 44 و45، `limit=3` ⇒ 43، 42، 41. و`RLS` `test_list_messages_newest_first_pages_by_seq_cursor_and_excludes_deleted` | FR‑8 |
| AC‑1.6 | unit | `R` `test_desc_and_asc_walks_return_the_same_messages_byte_for_byte`: رسالة عربية بتشكيل وemoji، ومساعد بـ`citations` و`status: "partial"` و`fallback` و`reply_to`، ورسالة 100,000 حرف. عبوران كاملان. كل عنصر في `desc` يساوي نظيره بالمعرّف في `asc` (`dict ==`)، والنص العربي بايتات UTF‑8 في `response.content` بلا `\u` | FR‑7، NFR‑9 |
| AC‑2.1 | unit + integration | `R` `test_desc_full_walk_is_three_pages_of_20_20_5`. و`RLS`: الصفحات الخلفية بالـ cursor الحقيقي | FR‑4، FR‑5 |
| AC‑2.2 | unit | `R` `test_desc_walk_is_the_exact_reverse_of_the_asc_walk_across_gaps`: 60 رسالة، حُذفت 10 و20 و21 و30 و60، `limit=7`. كل ظاهرة مرة واحدة في كل عبور، ولا محذوفة، و`ids(desc) == list(reversed(ids(asc)))` | FR‑8 |
| AC‑2.3 | unit | `R` `test_messages_appended_mid_walk_never_enter_its_later_pages`: صفحة أولى، ثم 3 رسائل جديدة، ثم المتابعة ⇒ 25..6 ثم 5..1. وصفحة جديدة بلا cursor تبدأ بـ 48 | FR‑13 |
| AC‑2.4 | unit | `R` `test_a_soft_delete_mid_walk_neither_shifts_nor_skips`: بعد الصفحة الأولى تُحذف 15 و40. الصفحة الثانية = 25..5 بلا 15 (20 عنصراً)، والثالثة 4..1، ولا تكرار | FR‑8 |
| AC‑2.5 | unit | `R` `test_a_non_null_desc_cursor_has_an_older_message_unless_all_were_deleted`: (أ) الصفحة التالية فيها رسالة أقدم. (ب) تُحذف كل الأقدم بين الطلبين ⇒ `data: []` و`null` | FR‑5 |
| AC‑2.6 | unit | `R` `test_the_catch_up_recipe_finds_every_new_message_once`: عميل حتى 45، ثم 46..70. صفحات خلفية `limit=20` حتى أول صفحة فيها `seq ≤ 45` ⇒ صفحتان، و46..70 كلها مرة واحدة بعد الدمج بالمعرّف | س‑6 |
| AC‑2.7 | unit | `R` `test_desc_first_page_holds_the_turn_and_the_reply_to_it`: `_ask` بالبث (منسّق حقيقي)، ثم `?order=desc&limit=20`. فيها رسالة `user` بـ`accepted.user_message_id`، ورسالة مساعد `reply_to` فيها المعرّف نفسه | س‑10 |
| AC‑3.1 | unit | `R`: كل الاختبارات القائمة في الملف بلا تعديل. و`test_the_default_walk_is_unchanged`: محادثة 45 بلا `order` ⇒ 1..20، 21..40، 41..45 | FR‑2 |
| AC‑3.2 | unit | `R` `test_no_order_and_order_asc_are_byte_identical`: بلا cursor ومع cursor، `response.content` متطابق | FR‑2 |
| AC‑3.3 | unit | `R` `test_a_cursor_minted_before_the_change_still_reads_forward`: `encode_seq_cursor(20)` بلا `order` ومع `asc` ⇒ 21.. تصاعدياً. و`F` `test_seq_cursor_payload_is_unchanged` | NFR‑5 |
| AC‑3.4 | unit | `SQL` `test_list_messages_forward_sql_is_unchanged`: بلا cursor ومع cursor. النص المترجم يساوي ترجمة عبارة يبنيها الاختبار كما يبنيها `master` (`select(messages).where(3 شروط[, seq > n]).order_by(messages.c.seq).limit(n + 1)`)، ولا `DESC` فيه، وعبارة واحدة. يُكتب الاختبار ويمرّ **قبل** تعديل المحوّل | NFR‑5 |
| AC‑3.5 | unit | `R` `test_page_keys_are_the_same_in_both_directions`: `set(body) == {"data", "meta"}`، و`set(meta) == {"next_cursor", "limit"}`، ومفاتيح كل `MessageOut` متساوية | API‑04 |
| AC‑3.6 | unit (contract) | `C` `test_the_order_parameter_is_declared_on_list_messages_alone`: `_declaring(LIVE, "order", spec=False) == _declaring(SPEC, "order", spec=True) == {("/api/v1/conversations/{}/messages", "get")}`. وفي الطرفين `enum == ["asc", "desc"]`، و`default == "asc"`، و`required` غير صحيح | FR‑14 |
| AC‑4.1 | unit | `R` مُعلَم `test_an_unknown_order_is_a_validation_error` بـ`foo` و`DESC` و`""`: 422 problem+json `common.validation_error`. و`monkeypatch` على `stack.repository.list_messages` يثبت أنه لم يُستدعَ | FR‑10 |
| AC‑4.2 | unit + integration | `R` `test_a_cursor_never_crosses_directions`: الخلفي بلا `order` وبـ`asc`، والأمامي بـ`desc` ⇒ 422 `common.invalid_cursor`. و`SQL` `test_a_cursor_of_the_other_direction_runs_no_statement` (بلا عبارة، في الاتجاهين). و`F`. و`RLS`: الخلط حياً | FR‑9، ق‑2 |
| AC‑4.3 | unit | `R` `test_desc_refuses_malformed_and_foreign_cursors`: `!!!!` و`encode_id_cursor(new_uuid7())` ⇒ 422 `common.invalid_cursor`، ونص الـ cursor ليس في `detail` ولا في `response.text`. و`SQL`: بلا عبارة | FR‑11 |
| AC‑4.4 | unit + integration | `SQL` مُعلَم `test_a_seq_past_int4_runs_no_statement_in_either_direction` (`2**31`، `-(2**31)`، `2**63`، بصيغة كل اتجاه) ⇒ بلا عبارة. و`test_int4_edges_reach_the_database_in_either_direction` (`2**31 - 1`، `0`، `-(2**31 - 1)`) ⇒ عبارة واحدة. و`R` `test_a_cursor_past_int4_is_invalid_cursor_not_500`. و`RLS` `test_list_messages_refuses_a_seq_past_int4` | FR‑11، س‑5 |
| AC‑4.5 | unit | `R` مُعلَم `test_desc_limit_keeps_its_bounds`: `0` و`101` و`-1` و`abc` ⇒ 422 `common.validation_error`. و`1` و`100` ⇒ 200 | FR‑6 |
| AC‑4.6 | unit | `R` `test_desc_guards_are_the_asc_guards`: بلا توكن 401. و`Principal` بـ`roles=frozenset()` (بنمط `monkeypatch` على `_FakeAuth.authenticate`، `:2421-2429`) ⇒ 403 `authz.forbidden`. ومجهولة ومحذوفة ومن مساحة أخرى ⇒ 404 `common.not_found`. و`UC` `test_list_messages_desc_on_a_missing_or_deleted_thread_never_reads_messages`: `list_messages_calls == []` | FR‑12 |
| AC‑4.7 | unit | `R` `test_a_desc_cursor_at_or_below_the_oldest_is_an_empty_last_page`: `encode_desc_seq_cursor(1)` و`(0)` ⇒ 200، `data: []`، `null` | |
| AC‑5.1 | unit | `SQL` `test_list_messages_desc_is_one_select_below_the_cursor_newest_first`: بلا cursor ومع cursor. عبارة واحدة، `FROM conversations.messages` وحده، والشروط الثلاثة، و`seq <` مع الـ cursor فقط، و`ORDER BY conversations.messages.seq DESC`، ومعامل `LIMIT` = `limit + 1`، ولا `JOIN` | NFR‑2 |
| AC‑5.2 | integration | `PLAN`، الاختبارات الخمسة في §4: `test_the_newest_first_transcript_page_scans_uq_msg_seq_backward`، و`test_the_newest_first_page_reads_a_page_plus_the_deleted_rows_it_passes`، و`test_without_uq_msg_seq_the_newest_first_page_loses_its_backward_scan`، و`test_the_owner_gets_the_same_transcript_plan_as_app_rw`، و`test_uq_msg_seq_comes_back_on_rollback`. ومساعد خالص `_backward_scans(explained) -> set[tuple[str, str, str]]` (نوع العقدة، والفهرس، والاتجاه)، وله توأم unit في `X` `test_the_backward_scan_reader_reads_a_canned_plan` على خطة JSON مكتوبة باليد | NFR‑3، NFR‑4 |
| AC‑5.3 | مراجعة | `git diff --stat master -- migrations/` فارغ | NFR‑1 |
| AC‑5.4 | integration | `RLS` `test_list_messages_newest_first_is_tenant_scoped`: المساحة B بالاتجاه الخلفي لمعرّف محادثة A ⇒ `data == []` و`null`، وA ترى رسائلها | NFR‑6 |
| AC‑5.5 | unit | `X`: الاختبارات المعلمة القائمة على المدخل الجديد (طريقة موجودة، وجدول مستأجر، وقراءة، و`:page`، والاحتياجات من `TenantProbes`، واسم فريد) | NFR‑8 |
| AC‑5.6 | بوابة | `lint-imports` (8 عقود)، و`git diff master -- .importlinter` فارغ | NFR‑7 |
| AC‑6.1 | unit (contract) + مراجعة | `C` في AC‑3.6. والمراجعة: `description` يذكر النقاط الخمس (الترتيب في كل اتجاه، و`next_cursor` في كل اتجاه، والخلط 422، و`null` نهاية، ووصفة اللحاق) | FR‑15 |
| AC‑6.2 | مراجعة | نصوص §3.7 في `03 §0` و`02 §2`. و`grep -n "ONE exception\|One direction\|reads forward by" src/app/framework/pagination.py src/app/modules/conversations/` لا يجد الصياغة القديمة | FR‑15 |
| AC‑6.3 | مراجعة | `gaps.md`: فقرة الإصلاح بتاريخها، وسطر الحالة، وتصحيح `:90` | FR‑16 |
| AC‑6.4 | مراجعة | `git -C /mnt/c/Users/anast/Desktop/aizzak-android status --short` قبل الفرع وبعده: لا ملف جديد من هذه الميزة | ق‑3 |
| AC‑6.5 | بوابة | الخمس محلياً، و`deploy/prometheus/test-rules.sh`، وCI `quality` و`integration` أخضران | NFR‑10 |

**اختبارات `RLS` الجديدة** (قسم «ث‑5» بعد `:535`، بالمستودع الحقيقي و`append_message`):
- `test_list_messages_newest_first_pages_by_seq_cursor_and_excludes_deleted`:
  - 5 رسائل، وتُحذف 5 و2 بـ`_soft_delete_message_as_owner`.
  - `DESC limit=2` ⇒ `[4, 3]`، ثم بالـ cursor ⇒ `[1]` و`None`.
  - والـ cursor الخلفي مع `ASC` ⇒ `ValidationError` برمز `common.invalid_cursor`.
- `test_list_messages_newest_first_is_tenant_scoped` (AC‑5.4).
- `test_list_messages_refuses_a_seq_past_int4`: `encode_seq_cursor(2**31)` مع `ASC`، و`encode_desc_seq_cursor(2**31)` مع `DESC`، كلاهما `common.invalid_cursor` لا `common.internal`. هذا يحسم §5.7 في `requirements.md` بالسلوك الجديد، بلا حاجة لمعرفة سلوك اليوم.

## 6. ترتيب التنفيذ والتوازي

منفّذ واحد (backend-developer) على فرع `feat/data-search-history-cursor` من `master`. الخطوات المرقّمة بالحرف نفسه يمكن أن تُنفَّذ بأي ترتيب بينها.

1. **أولاً، على `master` بلا تعديل في `src`:** `SQL` `test_list_messages_forward_sql_is_unchanged` و`F` `test_seq_cursor_payload_is_unchanged`، ويمرّان. هذا يثبت أن المرجع هو سلوك اليوم لا سلوك الفرع (AC‑3.4، AC‑3.3).
2. **(أ) framework:** `pagination.py` (الـ codec و`_seq_value` والـ docstring)، واختبارات `F`.
   **(أ) domain:** `MessageOrder`.
3. **المنفذ، ثم المحوّل:** `ports/repository.py`، ثم `sql_repository.py` (`_INT4_MAX`، والفرعان، و`_desc_seq_cursor_of`، والـ docstring). ثم اختبارات `SQL`، وحذف اختبار `electrical` الواحد (د‑ت6) **في الـ commit نفسه** الذي يضيف `maximum`، حتى لا يحمر `pytest` بينهما.
4. **حالة الاستخدام والمزيّفات:** `use_cases.py`، و`support_conversations.py`، و`_FakeConversations`، واختبارات `UC`.
5. **الراوتر:** `routers/conversations.py`، واختبارات `R`.
6. **(ب) العقد:** `openapi.yaml`، واختبار `C`.
   **(ب) الأداء:** مدخل `explain_hot_paths.py`، وتوأم `X`، و`PLAN`.
   **(ب) العزل:** اختبارات `RLS`.
7. **الوثائق الملزمة:** `03 §0`، و`02 §2`، و`06 §4`. يمكن موازاتها بالخطوات 3‑6 بعد تثبيت التواقيع في §3.
8. **البوابات 1–5 و`test-rules.sh`**، ثم PR، ثم CI `quality` و`integration`.
9. **بعد اخضرار CI:** `gaps.md` (ث‑5، وسطر الحالة، و`:90`) في commit وثائق على الفرع نفسه (نمط ث‑3).

**المراحل التي تُتخطّى:** UX وfrontend (لا واجهة في هذا المستودع)، والبيانات/data-engineer (لا ترحيل)، وstaging وبوابة ③ (لا هدف staging، والموجة 8 مؤجلة).

## 7. المخاطر والقرارات

**لا بنود ⚠️ جديدة.** كل ما يمس العقد الملزم حسمه المالك في بوابة ① (ق‑1…ق‑3): المعامل واسمه وقيمه، والترتيب داخل الصفحة، وحمل الاتجاه في الـ cursor، وحدّ `int4`، وتعديل `03 §0` و`02 §2`، وتصحيح `gaps.md`، والاختبارات الحية في CI، وعدم تحديث Android. وما بقي قرارات تصميم لا تمس الأمن ولا المستأجر ولا الكلفة، ولا تضيف تبعية.

### 7.1 قرارات التصميم (بلا حاجة إلى قرار بشري)

- **د‑ت1، وسم `<` في الحمولة لا في الغلاف.**
  - البديل الأول: بادئة خارج base64 (مثل `d.` + base64). يكسر «المؤشّر base64url واحد» في `_decode`، ويحتاج مساراً ثانياً للفكّ الصارم.
  - البديل الثاني: علامة سالبة لـ`seq`. القيم السالبة مقبولة اليوم في الاتجاه الأمامي (سلوك قائم، `stories.md` US‑4)، فلا تفصل.
  - `<` ليس رقماً ولا إشارة ولا فراغاً، فيرفضه `int()` في الفاكّ الأمامي بلا سطر جديد. والحمولة الأمامية لا تتغير بايتاً (ق‑2).
- **د‑ت2، `MessageOrder` في domain الوحدة، لا في `app.framework`.**
  - الاتجاه الاختياري خاص بالسجل (ت‑2، A‑1). نوع في framework يدعو إلى تعميمه.
  - والنمط قائم: `Literal` عند الحد، ثم `StrEnum` من domain (`electrical`).
  - كلفته سطر في `06 §4`، وهو إضافة تصف الشيفرة لا تعارض.
- **د‑ت3، codec باسم صريح لا معامل `direction` على `decode_seq_cursor`.** يتبع مبدأ 6.3‑أ «تسمية الـ keyset في الدالة تجعله مرئياً». ولا يتغير توقيع يستعمله `electrical`.
- **د‑ت4، `order` بقيمة افتراضية في المنفذ وحالة الاستخدام.** لا يتغير أي مستدعٍ قائم (`test_replay_ops_live.py:110`، والمزيّفات)، وهذا هو FR‑2 على مستوى المنفذ.
- **د‑ت5، الفكّ يبقى في المحوّل، بعد قراءة المحادثة.**
  - يحفظ ترتيب الحراسات اليوم (404 قبل 422) وموضعاً واحداً للفكّ مع صاحب الـ keyset (`dto/pagination.py:39-43`).
  - **انحراف صغير عن تعريف «بلا استعلام» في `stories.md`:** التعريف يقبل «صفر عبارات SQL في المستودع» أو «صفر استدعاء لـ`list_messages` في المزيّف». نثبت الأول على المحوّل الحقيقي (`SQL`، «بلا عبارة»). أما في اختبارات `R` لرفض الـ cursor فيُستدعى `list_messages` في المزيّف، ويرفض قبل أن يقرأ شيئاً، بالـ codec نفسه.
  - وقراءة المحادثة (`get`) تسبق كما اليوم. فـ«لا يبلغ أيّ استعلام» في `03 §0` يعني ما يعنيه اليوم: لا عبارة تحمل الـ cursor.
  - ولـ AC‑4.1 وAC‑4.6 لا يُستدعى `list_messages` أصلاً.
- **د‑ت6، حذف اختبار `electrical` الذي يثبّت غياب `maximum` من محوّل `conversations`** (`test_electrical_qa_pr_c_round2.py:148-150`). كان يحرس قرار نطاق PR‑C («bigint range of the conversation cursor untouched»)، وقرار المالك س‑5 يحل محله. والحدّ الجديد يحرسه سلوكاً `SQL` (AC‑4.4)، وهو أقوى من فحص نص المصدر.
- **د‑ت7، fixture الخطة بـ 10,000 رسالة وخمس محذوفة.** يغطي NFR‑3 (≥ 2,000) وNFR‑4 (10,000، والقراءة ≤ الصفحة + المحذوفة) بـ fixture واحد. والإدراج بـ`executemany` رخيص (السابقة قاست 0.7 ثانية لـ 24,800 صف، `:123-124`).
- **د‑ت8، حذف القيد لا الفهرس في إثبات الفشل.** `uq_msg_seq` قيد، فـ`DROP INDEX` يفشل. و`ALTER TABLE … DROP CONSTRAINT` داخل معاملة تُلغى يعيده، واختبار مستقل يثبت عودته.

### 7.2 المخاطر المُدارة

- **المخطِّط لا يختار المسح الخلفي تحت RLS.** الاحتمال منخفض: قيد السياسة مقارنة `uuid` بـ`current_setting` (لا دالة غير leakproof كما في `files/0003`)، والاتجاه الأمامي على الفهرس نفسه في الكتالوج منذ 2.4. إن حدث يفشل AC‑5.2 في CI، فيعود الأمر إلى المصمم ومهندس البيانات، ولا فهرس بلا موافقة (US‑5).
- **قفل `ACCESS EXCLUSIVE` على `conversations.messages`** أثناء اختبار الحذف. في قاعدة CI المعزولة فقط، ولا يُشغَّل على المكدس الحي (CLAUDE.md).
- **عميل ينسى `order` في الصفحة الثانية:** 422 `common.invalid_cursor` لا رسائل مكررة. هذا مقصود (ق‑2)، ووصف العملية يقوله.
- **تغيير سلوك أمامي ضيق:** cursor أمامي بـ`|seq| > 2^31 - 1` يصير 422 بدل خطأ القاعدة. لا عميل يحمل مثله شرعاً (العمود `int4`)، وقرره المالك (س‑5).
- **الـ cursor ليس سراً:** الوسم والموضع يُقرآن بفكّ base64، كما اليوم. لا يكشف إلا `seq`، وهو في `MessageOut` أصلاً. وأي موضع يصنعه العميل يُقرأ تحت RLS و`workspace_id`، فلا قراءة خارج المساحة (AC‑5.4).
- **ما قيل لـ Android يصير ناقصاً** (ت‑7): لا يُكسر شيء، لأن الافتراضي باقٍ. والتنبيه في `gaps.md` (§3.7).

</div>
