<div dir="rtl">

# التصميم التقني: ث‑3 مصالحة الإرسال الغامض

- **المدخلات الملزمة:**
  - `requirements.md` و`stories.md` في هذا المجلد (7 قصص، 66 معياراً).
  - قرارات بوابة ① في `status.md` (2026‑11‑10): ق‑1…ق‑4، وتوصيات المحلل في س‑2 وس‑3 وس‑5…س‑7 وس‑9…س‑14.
- **الشيفرة التي فُحصت:** `master` على `b104878`. أرقام الأسطر أدناه منه.
- **المرجع الأقرب الذي نتبعه:**
  - **ث‑4** (`routers/conversations.py:216-255`): `Idempotency-Key` بسجل المفاتيح القائم، والفحص قبل `idempotent()`، والبصمة بـ sha256.
  - **ث‑1 وث‑2** (`docs/delivery/data-search-android/design.md`، `docs/delivery/data-search-message-status/design.md`): عمود مستقل على `conversations.messages`، وحقل أخير بقيمة افتراضية على `Message` و`AppendedMessage`، ومعامل keyword على `ConversationThreads.append`، ومفتاح حاضر دائماً على `MessageOut`، واختبار parity، واختبار RLS حي، واختبار ترحيل حي ذهاباً وإياباً.
  - **`_settle_cut`** (`orchestrator.py:2133-2177`): ما يكتبه الدور عند نهايته يُكتب **قبل** أن يخرج الإطار الطرفي، من إطار ما زال يعمل. نتيجة المفتاح تتبع القاعدة نفسها.
- **دروس retro ث‑2 التي يلتزم بها هذا التصميم:**
  - الاختبار الحي للترحيل يُكتب بلا قوالب `str.format`، وله توأم unit يشغّل `upgrade()`/`downgrade()` محلياً على `op` مزيّف (§5).
  - كل منطق مساعد في اختبار حي له اختبار unit رخيص.
  - كل اختبار يُجمِّع SQL يسمّي لهجة `asyncpg` صراحة (`_PG`).

## 1. الملخص

الميزة تمر بأربع طبقات. لا تمس إلا وحدة `conversations`، وسجل المفاتيح (`framework.ports.idempotency_store` ومحوّله)، و`AppError`، والمنسّق، وطبقة `api`.

1. **`app.agents.orchestrator`:**
   - `_open_turn` يعيد رسالة المستخدم المكتوبة بدل أن يهملها، فيحفظها `_TurnRecord.user_message`.
   - **`_metered` يبث إطار `accepted` أولاً** (`conversation_id`، `user_message_id`، `seq`)، قبل أن يسحب أي حدث من الوكيل (NFR‑4). لا يُبث إن لم تُكتب رسالة المستخدم (FR‑3).
   - `final` يحمل `user_message_id`. و`error` (حدث الوكيل والمهلة) يحمل `user_message_id`، و`message_id` للجزء المحفوظ إن وُجد (FR‑16).
   - `_persist_reply` و`_persist_partial` يمرران `reply_to=record.user_message.id` في `append` نفسه (FR‑4).
   - **منفذ جديد صغير `TurnLedger`** يعرّفه المنسّق، ويمرره المستدعي اختيارياً إلى `invoke`/`invoke_once`. المنسّق يناديه مرتين: `accepted` بعد كتابة رسالة المستخدم، و`settled` مرة واحدة على المسار الطرفي، **قبل** خروج الإطار الطرفي. المنسّق لا يعرف شيئاً عن `Idempotency-Key` (د‑ت1).
2. **`app.modules.conversations`:**
   - **domain:** `Message.reply_to` (حقل أخير)، و`append_message(..., reply_to=)`، والثابت **INV‑CV6**: `reply_to` لرسائل `assistant` وحدها.
   - **ports:** `AppendedMessage.reply_to`، و`ConversationThreads.append(..., reply_to=)`.
   - **application:** `_recordable_reply_to` صارم قبل أي I/O، وحالة استخدام جديدة `GetMessage` لقراءة الإعادة.
   - **adapter:** عمود `reply_to uuid NULL`. حين يُمرَّر `reply_to` يصير الإدراج `INSERT … SELECT … WHERE EXISTS (رسالة user في المحادثة نفسها) RETURNING id`، في **العبارة نفسها**. فعدد العبارات لا يتغير، ولا FK ولا فهرس (د‑ت5).
3. **سجل المفاتيح** (`framework/ports/idempotency_store.py` و`infrastructure/persistence/idempotency.py`):
   - عملية جديدة `checkpoint`: جسد مؤقت على مطالبة لم تكتمل.
   - `claim` يعيد مع `IN_PROGRESS` الجسد المؤقت وعمر المطالبة `age_s` بساعة Postgres.
   - `release` لا يحذف مطالبة لها جسد مؤقت.
   - العمليات الأخرى ومستعملوها الخمسة القائمون بلا تغيير في السلوك (د‑ت2).
4. **`app.api`:**
   - **وحدة جديدة `api/v1/idempotent_turn.py`:** البصمة على `content` وحده، والنطاق `POST /conversations/{conversation.id}/messages`، و`TurnHandle` (المقبض المخزَّن، بلا نص)، و`IdempotentTurnLedger` (يطبّق `TurnLedger` فوق `IdempotencyStore`)، و`claim_turn`، وبناة الإعادة.
   - **`post_message`** يقبل `Idempotency-Key` في الفرعين. يفحص المفتاح، ثم يقرأ المحادثة (404)، ثم يطالب، ثم يشغّل الدور أو يعيد نتيجته.
   - `MessageOut.reply_to` (حاضر دائماً).
   - أعضاء امتداد RFC 9457 `user_message_id`/`message_id` في `api/errors.py`، من مجموعة مغلقة، يحملها `AppError.extensions` الجديد.
   - WebSocket يمرر `accepted` كما يمرر أي إطار، مع إصلاح واحد في الغلاف (د‑ت11).
5. **البيانات:** ترحيل إضافي واحد `conversations/0009_message_reply_to`: عمود `uuid NULL`، metadata فقط، بلا قيد ولا فهرس. لا تغيير على `platform.idempotency_keys`.

**آلة حالات صف المفتاح لهذا المسار** (مفتاح الصف: المساحة، و`endpoint = "POST /conversations/{conversation.id}/messages"`، والمفتاح):

| الحالة | `response_body` | `completed_at` | ما يعطيه `claim` لبصمة مطابقة | ما يعطيه الطلب المكرر |
|---|---|---|---|---|
| غائب | — | — | `CLAIMED` | دور جديد |
| S1 مُطالَب | `NULL` | `NULL` | `IN_PROGRESS` (`age_s`، بلا جسد) | عمر < T: 409 `common.conflict`. عمر ≥ T: problem 502 `agent.failed` بلا معرّفات، وبلا بث (د‑ت4) |
| S2 مقبول | مقبض `outcome: "accepted"` | `NULL` | `IN_PROGRESS` (`age_s`، المقبض) | عمر < T: 409. عمر ≥ T: «الدور ضاع» = `accepted` (`replayed: true`) ثم `error` `agent.failed`، أو problem 502 ومعه `user_message_id` (FR‑14) |
| S3أ أُجيب | مقبض `outcome: "replied"` | مضبوط | `REPLAY` | JSON: `MessageOut` مقروء من الصف الحالي (محذوف ⇒ 404). البث: `accepted` (`replayed`) ثم `final` |
| S3ب فشل | مقبض `outcome: "failed"` | مضبوط | `REPLAY` | JSON: problem بالرمز والحالة المخزنين ومعه المعرّفات. البث: `accepted` (`replayed`) ثم `error` |
| مكنوس | — | — | `CLAIMED` | دور جديد (FR‑19) |

- **بصمة مختلفة في أي حالة ⇒ 409 `common.conflict`** ولا يُكتب شيء (`MISMATCH`، كما اليوم).
- **T = `settings.limits.stream_max_duration_s` + `TURN_STALE_MARGIN_S`** = 600 + 120 = 720 ثانية، على ساعة Postgres (`now() - created_at`) (§7.1، د‑ت3).
- **الانتقالات:**

| من → إلى | السبب | العبارة |
|---|---|---|
| غائب → S1 | `claim` | 1: `INSERT` |
| S1 → غائب | فشل قبل القبول، أو منفذ المحادثات غير موصول | `release` (`DELETE … WHERE completed_at IS NULL AND response_body IS NULL`) |
| S1 → S2 | `TurnLedger.accepted` | 2: `checkpoint` (`UPDATE`) |
| S2 → S3 | `TurnLedger.settled` | 3: `complete` (`UPDATE`) |
| S1 → S3 | `checkpoint` فشل والدور أكمل | 3: `complete` (المقبض الكامل يحمل المعرّفات أيضاً) |
| أي حالة → غائب | `sweep_idempotency_keys` بعد يومين من `created_at`، يومياً | خارج الطلب |

- **ما يُخزَّن (FR‑17، NFR‑9):** معرّفات، و`seq`، والنتيجة، والرمز والحالة، وعدّادا الاستهلاك. **لا** `text` ولا `content` ولا `citations` ولا `detail`. الطول ثابت تقريباً (~300 بايت)، لا يتبع طول الرسالة (NFR‑11).

## 2. الملفات

لا يعدّل منفّذان الملف نفسه.

| المسار | إنشاء/تعديل | المسؤولية | المنفّذ |
|---|---|---|---|
| `migrations/versions/conversations/0009_message_reply_to.py` | إنشاء | `ADD COLUMN reply_to uuid NULL`، و`downgrade` يحذفه. docstring بنمط `0008` (§4) | data-engineer |
| `docs/design/01-data-model.md` (§2.4، §4.2‑ب) | تعديل | سطر `reply_to` وتعليقه في `CREATE TABLE conversations.messages`. وفي §4.2‑ب: معنى `response_body` مع `checkpoint`، وشكل المقبض لـ `postMessage`، وتصحيح «لا سياسة احتفاظ» (FR‑19، ت‑7) | data-engineer |
| `tests/unit/test_conversations_schema_parity.py` | تعديل | تهجئة `reply_to` في الأماكن الثلاثة، و`nullable` بلا `server_default`، و`downgrade`، والسلسلة | data-engineer |
| `tests/unit/test_message_reply_to_migration.py` | إنشاء | يحمّل ملف الترحيل بـ `importlib`، ويستبدل `op` بمسجّل، ويشغّل `upgrade()` و`downgrade()` محلياً، ويقارن العبارات حرفياً. ويستورد ثوابت SQL من الاختبار الحي ويفحص أنها بلا أقواس قالب (درس retro) | data-engineer |
| `tests/integration/test_message_reply_to_migration_live.py` | إنشاء | تنزيل `conversations` إلى `0008_message_status` ثم رفعها إلى `0009`، مع صف قديم يعبر الرحلة. معاملات مربوطة فقط، بلا `str.format`. CI `integration` فقط | data-engineer |
| `tests/integration/test_conversations_repository_rls.py` | تعديل | ذهاب وإياب `reply_to`، والصف القديم `NULL`، والعزل، ورفض `reply_to` لمحادثة أخرى ولمساحة أخرى. CI فقط | data-engineer |
| `src/app/modules/conversations/domain/entities.py` | تعديل | `Message.reply_to`، و`append_message(..., reply_to=)`، وINV‑CV6 | backend-developer |
| `src/app/modules/conversations/ports/inbound.py` | تعديل | `AppendedMessage.reply_to`، و`ConversationThreads.append(..., reply_to=)` | backend-developer |
| `src/app/modules/conversations/application/use_cases.py` | تعديل | `_recordable_reply_to`، و`AppendMessage.execute(..., reply_to=)`، و`GetMessage`، و`ConversationUseCases.get_message`، و`ConversationService.append` | backend-developer |
| `src/app/modules/conversations/adapters/sql_repository.py` | تعديل | العمود، و`_message_values`، و`_insert_message` (العادي أو المحروس)، و`append_message`، و`_hydrate_message` | backend-developer |
| `src/app/framework/errors.py` | تعديل | `AppError.extensions` (و`RateLimitedError` يمرّره) | backend-developer |
| `src/app/framework/ports/idempotency_store.py` | تعديل | `IdempotencyStore.checkpoint`، و`IdempotencyClaim.age_s`، والجسد المؤقت مع `IN_PROGRESS`، وdocstrings بلا «بالضبط ثلاث عمليات» (AC‑6.9) | backend-developer |
| `src/app/infrastructure/persistence/idempotency.py` | تعديل | `checkpoint`، و`_inspect` يقرأ العمر والجسد المؤقت، وشرط `release` | backend-developer |
| `src/app/framework/di/composition_root.py` | تعديل | سطر واحد: `get_message=GetMessage(repository)` في `ConversationUseCases` (`:1341`) | backend-developer |
| `src/app/agents/orchestrator.py` | تعديل | `AcceptedTurn` و`TurnOutcome` و`TurnLedger`، وحقول `_TurnRecord`، و`_open_turn`، وإطار `accepted`، والمعرّفات على `final`/`error`، و`reply_to`، ونداءات الدفتر، و`invoke(..., ledger=)`، و`invoke_once(..., ledger=)`، و`_in_band_error` بالامتداد (§3.5) | backend-developer |
| `src/app/api/errors.py` | تعديل | `PROBLEM_EXTENSION_KEYS`، و`problem(..., extensions=)`، و`problem_from_error` و`problem_from_event` يمرران الامتداد | backend-developer |
| `src/app/api/v1/idempotency.py` | تعديل | `_guard_key` يصير `guard_key` (عام، يستعمله `idempotent_turn`)، ووصف الرأس، وdocstring الوحدة (AC‑6.9) | backend-developer |
| `src/app/api/v1/idempotent_turn.py` | إنشاء | §3.8 | backend-developer |
| `src/app/api/v1/routers/conversations.py` | تعديل | `post_message` بالمفتاح، و`_run_turn` و`_replay_turn`، و`reply_to` في المحوّلين | backend-developer |
| `src/app/api/v1/dto/conversations.py` | تعديل | `MessageOut.reply_to` | backend-developer |
| `src/app/api/v1/websocket/streaming.py` | تعديل | غلاف `conversation_id` لإطار `accepted` حين لم يسمِّ الطلب محادثة (د‑ت11) | backend-developer |
| `docs/design/openapi.yaml` | تعديل | `postMessage` (المفتاح والوصف)، ووصف SSE في `postMessage` و`invokeAgent`، و`MessageOut.reply_to`، وأعضاء `Problem` الاختيارية، ووصف `IdempotencyKey`. **في commit واحد مع DTO والراوتر** (`test_api_conventions.py:313-326` و`:495`) | backend-developer |
| `docs/design/03-api-spec.md` (§0، §2، §3.1، §3.2، §4) | تعديل | §3.10 | backend-developer |
| `docs/design/06-domain-models.md` (§4) | تعديل | `Message.reply_to`، وINV‑CV6، وتصحيح «سجل المحاولة في ث‑3» (`:102`)، و`GetMessage` في قائمة حالات الاستخدام | backend-developer |
| `docs/delivery/data-search-android/gaps.md` (ث‑3، ث‑2، `:83`) | تعديل | فقرة «الإصلاح (التاريخ)» بعد اخضرار البوابات (AC‑6.8) | backend-developer |
| `tests/unit/support_idempotency.py` | تعديل | `checkpoint`، وعمر لكل صف يضبطه الاختبار (`set_age`)، وسجل لكل عملية (`calls`)، وشرط `release` الجديد | backend-developer |
| `tests/unit/support_conversations.py` | تعديل | `append_message` يرفض `reply_to` لا يسمّي رسالة `user` في المحادثة نفسها والمساحة نفسها، بنمط المحوّل. و`get_message=GetMessage(...)` في `ConversationUseCases(` (`:314`) | backend-developer |
| `tests/unit/test_conversations_domain.py` | تعديل | INV‑CV6 | backend-developer |
| `tests/unit/test_conversations_use_cases.py` | تعديل | `_recordable_reply_to`، و`GetMessage`، والذهاب والإياب عبر `ConversationService.append` | backend-developer |
| `tests/unit/test_conversations_sql_statements.py` | تعديل | الإدراج المحروس في عبارة واحدة، وعدد العبارات، و`RETURNING`، والرفض بلا `UPDATE`، والترطيب | backend-developer |
| `tests/unit/test_idempotency_store_sql.py` | إنشاء | تجميع عبارات `SqlIdempotencyStore` بلهجة `asyncpg`: `checkpoint`، وعمود العمر في `_inspect`، وشرط `release` | backend-developer |
| `tests/integration/test_idempotency_live.py` | تعديل | `checkpoint` يبقي `IN_PROGRESS` بجسده، و`age_s` من ساعة القاعدة (صف بـ `created_at` مُرجَع إلى الوراء)، و`release` لا يحذف صفاً مقبولاً، و`complete` بعد `checkpoint` يعطي `REPLAY`. CI فقط | backend-developer |
| `tests/unit/test_api_idempotency.py` | تعديل | `idempotent()` يتجاهل الجسد المؤقت مع `IN_PROGRESS` (لا تغيير في سلوكه)، واسم `guard_key` | backend-developer |
| `tests/unit/test_api_idempotent_turn.py` | إنشاء | البصمة، والنطاق، والمقبض، وآلة الحالات، والعتبة، والدفتر، و«الحماية من الإلغاء» (§5) | backend-developer |
| `tests/unit/test_api_errors.py` | تعديل | أعضاء الامتداد المغلقة، ولا تطغى على العضو الأساسي | backend-developer |
| `tests/unit/test_orchestrator.py` | تعديل | `_FakeThreads.append` يقبل `reply_to` ويسجله في `self.reply_tos` (قائمة منفصلة). وإطار `accepted`، والمعرّفات، و`reply_to`، ونداءات الدفتر. **وتحديث كل توقع قائم لترتيب الإطارات** | backend-developer |
| `tests/unit/test_workflow_orchestration.py` | تعديل | توقيع `append` المزيّف، ومخرج الخطوة بـ `reply_to is None` | backend-developer |
| `tests/unit/test_llm_fallback.py` | تعديل | فقط توقعات ترتيب الإطارات التي يكسرها `accepted` إن وُجدت (لا اختبارات جديدة) | backend-developer |
| `tests/unit/test_api_conversations_router.py` | تعديل | US‑1 وUS‑2 وUS‑3 وUS‑4 وUS‑5 وUS‑7 على السلك. وتحديث توقعات الترتيب القائمة | backend-developer |
| `tests/unit/test_api_agents_router.py` | تعديل | AC‑1.6، و`AgentInvokeOut.message.reply_to` | backend-developer |
| `tests/unit/test_api_conventions.py` | تعديل | `MessageOut.reply_to` في الاتجاهين، و`postMessage` يعلن المفتاح (الاختبار القائم) | backend-developer |
| `tests/unit/test_websocket_endpoint.py` | تعديل | تحديث توقعات الترتيب القائمة، وغلاف `accepted` بلا `conversation_id` في الطلب | backend-developer |
| `tests/unit/test_asgi_disconnect_semantics.py` | إنشاء | يثبت على النسخ المثبتة (uvicorn 0.51، Starlette 1.3.1) أن معالج JSON لا يُلغى عند قطع العميل، وأن بث `StreamingResponse` يُلغى (§7.1) | qa-engineer |
| `tests/unit/test_send_reconcile_frames.py` | إنشاء | NFR‑5 (مقارنة الإطارات قبل التغيير وبعده)، وAC‑1.2، وAC‑1.3، وAC‑1.9، وAC‑1.10 | qa-engineer |
| `tests/unit/test_websocket_accepted_frames.py` | إنشاء | AC‑1.5: دوران متزامنان على مقبس واحد | qa-engineer |
| `docs/delivery/data-search-send-reconcile/test-plan.md` | إنشاء | مصفوفة AC ← اختبار، وفجوات التغطية | qa-engineer |

**ملفات لا تتغير، وقد فُحص ذلك:**
- **`platform.idempotency_keys` وترحيلاتها:** الأعمدة كافية (`response_body` jsonb، و`created_at` بساعة القاعدة، و`completed_at`). و`app_rw` يملك `UPDATE` أصلاً (`01 §4.2‑ب`)، و`checkpoint` تحديث.
- **`deploy/postgres/initdb/10-roles.sh` و`src/app/ops/provision.py`:** صلاحيات `conversations.messages` على مستوى الجدول، و`INSERT … SELECT` يحتاج `SELECT` الممنوح أصلاً للقراءة.
- **سياسة RLS** (`0001_conversations.py:106-115`): على مستوى الصف، فتغطي العمود، وتحجب صفوف المساحات الأخرى عن الاستعلام الفرعي في `EXISTS`.
- **`src/app/ops/retention.py`:** الكنس بعد يومين لا يتغير (خارج النطاق). يُوثَّق فقط.
- **`src/app/api/v1/sse.py`:** يرمّز أي نوع إطار، ويحوّل `error` عبر `problem_from_event` الذي يحمل الامتداد الآن.
- **`src/app/api/v1/routers/agents.py`:** يستدعي `invoke`/`invoke_once` بلا `ledger`، و`appended_to_message_out` يضيف `reply_to`. لا مفتاح على `invokeAgent` (س‑11).
- **`routers/workflows.py` و`WorkflowRun`:** لا يمران بـ `_metered`، فلا `accepted` (AC‑1.10). و`_persist_step_output` لا يمرر `reply_to` (A‑5).
- **`src/app/workers/bootstrap.py`:** رسالتا الملخص تبقيان `reply_to: null` (A‑5)، بلا تعديل، لأن المعامل الجديد بقيمة افتراضية.
- **`src/app/ops/load_seed.py` و`purge.py` و`explain_hot_paths.py`:** `INSERT` بأعمدة مسماة يترك العمود `NULL`، والحذف يحذف صفوفاً كاملة ولا FK يعيقه.
- **`docs/design/events/schemas/`:** إطارات SSE/WS ليست أحداث ناقل (ت‑9).
- **`.importlinter`:** لا `ignore_imports` جديد (AC‑7.7). المنفذ `TurnLedger` في `app.agents.orchestrator`، والـ API يستورده (اتجاه مسموح).
- **`deploy/`، و`.github/workflows/`، و`.env.example`:** لا إعداد جديد ولا سر. الهامش 120 ثانية ثابت في الشيفرة (د‑ت3).

## 3. العقود

### الواجهات البرمجية

لا مسار جديد، ولا رمز خطأ جديد، ولا صلاحية جديدة.

| الطريقة | المسار | الطلب | الاستجابة | الأخطاء | الصلاحية |
|---|---|---|---|---|---|
| POST | `/api/v1/conversations/{id}/messages` | الجسد بلا تغيير (`content`، `stream`). **جديد:** رأس `Idempotency-Key` اختياري، 1…255 حرفاً، ليس مسافات فقط. البصمة على `content` وحده | **JSON:** `MessageOut` لرسالة المساعد، وفيه `reply_to` = معرّف رسالة المستخدم. **البث:** `accepted` أولاً، ثم `notice`?، ثم `token`*، ثم `final` (ومعه `user_message_id`) أو `error` (ومعه `user_message_id` و`message_id`?). **الإعادة:** JSON = `MessageOut` نفسه أو الـ problem نفسه. البث = `accepted` (`replayed: true`) ثم `final` أو `error` فقط | كما اليوم: 401، و403، و404 (محادثة مجهولة أو محذوفة أو من مساحة أخرى، **قبل** أي قراءة من السجل)، و409، و422، و429، وproblem+json من `_in_band_error`. **جديد:** 422 `common.validation_error` لمفتاح غير صالح. 409 `common.conflict` لمفتاح جارٍ أو لمحتوى مختلف. 404 `common.not_found` لإعادة جواب حُذف. 502 `agent.failed` لإعادة دور ضاع أو فشل بعد القبول | `conversations:write` (+ صلاحيات الوكيل عبر المنسّق) |
| POST | `/api/v1/agents/{key}/invoke` | بلا تغيير، بلا مفتاح (س‑11) | **JSON:** `AgentInvokeOut.message.reply_to`. **البث:** `accepted` أولاً، و`final.user_message_id`، والمعرّفات على `error` | بلا تغيير، وproblem الفشل بعد القبول يحمل الامتداد | `agents:invoke` |
| GET | `/api/v1/conversations/{id}/messages` | بلا تغيير | كل `MessageOut` فيه `reply_to: uuid \| null` | بلا تغيير | `conversations:read` |
| WS | `GET /api/v1/ws` (`invoke`) | بلا تغيير، بلا مفتاح | `{"type":"accepted","conversation_id":…,"user_message_id":…,"seq":…}` أولاً لكل دور، و`final.user_message_id`، و`error.problem` بالامتداد | بلا تغيير | `agents:invoke` |

**الإطارات على السلك:**
```text
event: accepted
data: {"conversation_id":"018f…c1","user_message_id":"018f…u7","seq":7}

event: final
data: {"message_id":"018f…a8","content":{"text":"…","attachments":[]},"usage":{"prompt_tokens":812,"completion_tokens":140},"citations":[…],"user_message_id":"018f…u7"}

event: error
data: {"type":"https://errors.platform/agent.failed","title":"Agent execution failed","status":502,"code":"agent.failed","detail":"…","correlation_id":"018f…","user_message_id":"018f…u7","message_id":"018f…a8"}
```
الإعادة بالبث (S3أ):
```text
event: accepted
data: {"conversation_id":"018f…c1","user_message_id":"018f…u7","seq":7,"replayed":true}

event: final
data: {"message_id":"018f…a8","content":{"text":"…","attachments":[]},"citations":[…],"usage":{"prompt_tokens":812,"completion_tokens":140},"user_message_id":"018f…u7"}
```
- **إطار `accepted` الحي** مفاتيحه بالضبط الثلاثة (AC‑1.9). و`replayed: true` لا يظهر إلا في الإعادة.
- **`final` المعاد يُبنى من الرسالة المحفوظة ومن المقبض** (د‑ت8): `message_id`، و`content`، و`citations` (إن لم تكن `null`)، و`fallback` (إن لم تكن `null`)، و`usage` (من المقبض)، و`user_message_id`. مفاتيح الوكيل الخاصة (`job_id`…) لا تُعاد.
- **`error` المعاد:** الرمز والحالة المخزّنان، و`detail` جملة ثابتة («the earlier attempt with this Idempotency-Key ended with this error») (د‑ت9).
- **ترتيب الإطارات المعلن:** `accepted` → `notice`? → `token`* → `final` | `error`. وعلى العميل تجاهل أي نوع لا يعرفه (ت‑4).

**OpenAPI** (`docs/design/openapi.yaml`، بأسلوب الملف القائم):

`postMessage`:
```yaml
      parameters: [ { $ref: "#/components/parameters/Id" }, { $ref: "#/components/parameters/CorrelationId" }, { $ref: "#/components/parameters/IdempotencyKey" } ]
```
وصف `postMessage` (يُستبدل): ‏"Append a user message and run the conversation's agent. SSE when Accept: text/event-stream (or `stream: true`). The SSE body starts with an `accepted` frame `{conversation_id, user_message_id, seq}` as soon as the user message is stored, then `notice`?, `token`*, and `final` or `error`; clients must ignore any frame type they do not know. `final` and an in-flight `error` carry `user_message_id`; an `error` after a saved partial also carries its `message_id`. **Idempotency-Key (gap th-3):** scoped to (workspace, this conversation, key); the request is identified by `content` alone, so `stream`/`Accept` may differ on a retry and choose the shape of the answer. A repeat after the turn ended does not store a second question, does not run the agent and is not billed: JSON returns the same `MessageOut` (read from the stored row; 404 if it was deleted since) or the same problem; SSE returns two frames, `accepted` with `replayed: true` and then `final` or `error`. A repeat while the first attempt runs, or with a different `content`, is 409 `common.conflict`. A failure before the message is stored frees the key; a failure after it does not. An attempt whose server died is reported as `agent.failed` once `stream_max_duration_s` plus 120 s have passed since the key was first used. Keys are kept for 2 to 3 days (swept daily after 2); after that the same key starts a new turn."

`invokeAgent` (وصف `text/event-stream` فقط، بلا مفتاح):
```yaml
            text/event-stream: { schema: { type: string, description: "SSE: accepted (first, once the user message is stored), then token|tool_call|notice, then final|error (03 §3.1). Ignore unknown frame types." } }
```

`IdempotencyKey` (المكوّن المشترك):
```yaml
    IdempotencyKey: { name: Idempotency-Key, in: header, required: false, description: "Makes a retry safe (03 §0). 1..255 characters. Kept for at least 2 days: the daily retention sweep removes keys older than 2 days, so a key lives 2 to 3 days; after that it is a new key.", schema: { type: string, minLength: 1, maxLength: 255 } }
```

`MessageOut` (بعد `fallback`):
```yaml
        reply_to:
          type: string
          format: uuid
          nullable: true
          description: "The id of the user message this assistant message answers (gap th-3), stored in the same write by the turn that wrote both, so two identical questions from two devices are never confused. Always present. `null` = not recorded: user messages, messages saved before this field, workflow step outputs and summaries delivered by a worker. A question soft-deleted later keeps its id here (the question itself leaves every read)."
```

`Problem` (خاصيتان اختياريتان بعد `correlation_id`):
```yaml
        user_message_id: { type: string, format: uuid, description: "th-3 extension member (RFC 9457 §3.2): the user message a turn had already stored when it failed. Absent for any failure before that." }
        message_id: { type: string, format: uuid, description: "th-3 extension member: the partial assistant message (`status: partial`) saved before the failure. Absent when nothing was saved." }
```
- لا يُضاف `required` إلى `MessageOut`، كما في ث‑1 د‑ت7 وث‑2 د‑ت9. الحضور الدائم في `description`، وفي `LIVE.required`.
- **العميل والأنواع:** لا واجهة في هذا المستودع. يقرأ Android `openapi.yaml` أو `/openapi.json` الحي. و`backend-reply.md` لا يُحدَّث (س‑13)، والإصلاح يُسجَّل في `gaps.md`.

### التواقيع

#### 3.1 domain — `src/app/modules/conversations/domain/entities.py`
```python
@dataclass(slots=True)
class Message:
    ...
    fallback: MessageFallback | None = None
    # th-3 -- LAST and defaulted, for citations' reason. The id of the USER
    # message this assistant message answers; None = not recorded (user
    # messages, rows that predate the column, workflow steps, summaries).
    reply_to: str | None = None


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
        reply_to: str | None = None,
    ) -> Message:
        """... INV-CV6 (th-3): `reply_to` is carried by ASSISTANT messages only;
        otherwise InvalidConversationInput. That it names a USER message of THIS
        conversation is enforced by the repository's write (the aggregate does
        not hold its messages)."""
```
- INV‑CV6 يُفحص بعد INV‑CV5 وقبل زيادة `message_count`.

#### 3.2 port — `src/app/modules/conversations/ports/inbound.py`
```python
@dataclass(frozen=True, slots=True)
class AppendedMessage:
    ...
    fallback: FallbackRef | None = None
    # th-3 -- what was STORED: the user message this one answers, or None.
    reply_to: Uuid | None = None


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
        reply_to: Uuid | None = None,
    ) -> AppendedMessage: ...
```

#### 3.3 application — `src/app/modules/conversations/application/use_cases.py`
```python
def _recordable_reply_to(role: MessageRole, reply_to: str | None) -> str | None:
    """th-3 -- STRICT, like _recordable_status: one writer passes it, from its own
    record, so a bad value is a caller bug.

    - reply_to is None               -> None
    - role is not ASSISTANT          -> raise ValidationError("only assistant messages reply to a message")
    - uuid.UUID(reply_to) fails      -> raise ValidationError("reply_to must be a message id")
    - otherwise                      -> str(uuid.UUID(reply_to))  (canonical lower-case)
    """


class AppendMessage:
    async def execute(
        self, ctx, conversation_id, *, role, text, attachments=(), token_count=None,
        citations=None, status=None, fallback=None,
        reply_to: str | None = None,
    ) -> tuple[Message, tuple[ConversationEvent, ...]]: ...
    # check order: role, content, citations, status, fallback, reply_to -- all
    # before `self._conversations.get(...)` (no read, no row on a refusal).


class GetMessage:
    """One LIVE message of one thread -- the read behind an Idempotency-Key replay
    (th-3). Not a route: `03 §1` publishes none (q-10)."""

    def __init__(self, conversations: ConversationRepository) -> None: ...

    async def execute(self, ctx: ExecutionContext, conversation_id: Uuid, message_id: Uuid) -> Message:
        """NotFoundError("message not found") when the repository returns None
        (unknown, another thread's, another tenant's) OR the row is soft-deleted:
        a replay must never hand back text the user removed (FR-17, AC-3.11)."""


@dataclass(frozen=True, slots=True)
class ConversationUseCases:
    ...
    space_counts: CountConversationsBySpace
    # th-3 -- the replay read of `POST …/messages` with an Idempotency-Key.
    get_message: GetMessage
```
- `ConversationService.append` يمرر `reply_to` ويعيد `AppendedMessage(reply_to=message.reply_to)`.
- رفض المحوّل (`reply_to` لا يسمّي رسالة `user` في المحادثة) يصل `ValidationError` كما هو.

#### 3.4 adapter — `src/app/modules/conversations/adapters/sql_repository.py`
```python
messages = Table(
    "messages", _metadata,
    ...,
    Column("fallback", JSONB(none_as_null=True), nullable=True),
    # `migrations/versions/conversations/0009_message_reply_to.py` (th-3).
    # No FK and no index: integrity is checked by the guarded INSERT below.
    Column("reply_to", _uuid_col, nullable=True),
    schema="conversations",
)


def _message_values(message: Message) -> dict[str, object]:
    """The column -> value map append_message writes (today's twelve + reply_to)."""


def _insert_message(message: Message) -> Insert:
    """reply_to is None -> insert(messages).values(**_message_values(m)), exactly
    today's statement (the user message, steps, summaries).

    reply_to set -> ONE guarded statement, so the write count does not change
    (NFR-1, AC-2.5):

        INSERT INTO conversations.messages (<cols>)
        SELECT <one typed bind per col>
        WHERE EXISTS (SELECT 1 FROM conversations.messages
                      WHERE id = :reply_to AND conversation_id = :conversation_id
                        AND workspace_id = :workspace_id AND role = 'user')
        RETURNING conversations.messages.id

    built with insert(messages).from_select(names, select(*(literal(v, messages.c[n].type)
    for n, v in values)).where(exists(...))).returning(messages.c.id).
    RLS also applies to the sub-select, so another tenant's row is invisible."""
```
- **`append_message`:** ينفذ `_insert_message(message)`. إن كان `reply_to` مضبوطاً و`RETURNING` بلا صف، يرفع `ValidationError("reply_to names no user message of this conversation")` **قبل** `UPDATE` على `conversations`، فتُلغى المعاملة ولا يُكتب شيء. يبقى الطريق السليم عبارتين كما اليوم: `INSERT` ثم `UPDATE` (version).
- **`_hydrate_message`:** `reply_to=row["reply_to"]`.
- **`list_messages` و`get_message`:** `select(messages)` نفسها، فتُسقط العمود معها.

#### 3.5 agents — `src/app/agents/orchestrator.py`
```python
# th-3 -- the first frame of a single-agent stream, and the reconciliation ids.
_ACCEPTED_EVENT = "accepted"
_USER_MESSAGE_ID_KEY = "user_message_id"
_MESSAGE_ID_KEY = "message_id"


@dataclass(frozen=True, slots=True)
class AcceptedTurn:
    """The user message a single-agent turn stored (th-3) -- ids and seq, no text."""

    conversation_id: Uuid
    user_message_id: Uuid
    seq: int


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """How one ACCEPTED turn ended (th-3). Ids, codes and counts, never text.

    replied=True  <=> `final` was reached AND `_persist_reply` stored it;
                      message_id is that complete message, usage is filled.
    replied=False <=> any other ending; message_id is the saved PARTIAL or None;
                      code/status are the problem the caller was given
                      (agent.failed/502 for a reader who left and for a `final`
                      whose write failed).
    """

    accepted: AcceptedTurn
    replied: bool
    message_id: Uuid | None
    code: str | None = None
    status: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class TurnLedger(Protocol):
    """A caller that wants to hear how one turn went (th-3: the Idempotency-Key
    ledger of `POST …/messages`). The orchestrator calls `accepted` once, right
    after the user message is stored (still pre-flight), and `settled` at most
    once, BEFORE the terminal frame is yielded -- or from the `finally` when the
    reader left. A raise from either is logged and swallowed: the ledger
    annotates the turn, it never fails it."""

    async def accepted(self, turn: AcceptedTurn) -> None: ...

    async def settled(self, outcome: TurnOutcome) -> None: ...


@dataclass(slots=True)
class _TurnRecord:
    ...
    fallback: _Fallback | None = None
    # th-3 -- the user message `_open_turn` stored (None: seam unwired), the
    # caller's ledger, and whether it has been told the ending.
    user_message: AppendedMessage | None = None
    ledger: TurnLedger | None = None
    settled: bool = False


def _accepted_event(message: AppendedMessage) -> AgentEvent:
    """{"conversation_id", "user_message_id", "seq"} -- exactly three keys (AC-1.9)."""


def _with_user_message_id(event: AgentEvent, record: _TurnRecord) -> AgentEvent:
    """`final` + user_message_id, when a user message was stored; else unchanged."""


def _with_turn_ids(event: AgentEvent, record: _TurnRecord) -> AgentEvent:
    """`error` + user_message_id (when stored) + message_id (when a partial was
    stored). B1's code/status/detail untouched."""


def _turn_ids(data: Json) -> dict[str, str]:
    """The two id keys of an event payload that are non-empty strings -- what
    `_in_band_error` hands to AppError(extensions=...)."""


class AgentOrchestrator:
    async def invoke(
        self, ctx: ExecutionContext, agent_key: str, req: AgentRequest,
        *, ledger: TurnLedger | None = None,
    ) -> AsyncIterator[AgentEvent]: ...

    async def invoke_once(
        self, ctx: ExecutionContext, agent_key: str, req: AgentRequest,
        *, ledger: TurnLedger | None = None,
    ) -> AgentTurn: ...

    async def _begin_turn(
        self, ctx: ExecutionContext, agent_key: str, req: AgentRequest,
        ledger: TurnLedger | None,
    ) -> tuple[_TurnRecord, AsyncIterator[AgentEvent]]: ...

    async def _open_turn(
        self, ctx: ExecutionContext, agent_key: str, req: AgentRequest
    ) -> AppendedMessage | None:
        """... now RETURNS the stored user message (it used to drop it, `:1605`)."""

    async def _note_accepted(self, record: _TurnRecord) -> None:
        """ledger.accepted(AcceptedTurn(...)) when both exist; a raise -> one
        warning `orchestrator.turn_ledger_failed` {conversation_id, phase} only."""

    async def _settle_ledger(
        self, record: _TurnRecord, *, replied: bool,
        code: str | None = None, status: int | None = None,
    ) -> None:
        """At most once (record.settled). No-op without a ledger or a user
        message. message_id = record.message.id if record.message else None;
        usage from record.meter when replied. Same warning on a raise."""
```

**التغييرات في المنسّق** (لا يتغير غيرها):

| الموضع | بعد التغيير |
|---|---|
| `_begin_turn` (`:1537-1542`) | `opened = await self._open_turn(...)`. إن لم يكن `None`: `record.conversation_id = opened.conversation_id`، و`record.user_message = opened`، و`bind_turn(...)`، ثم `await self._note_accepted(record)`. و`record.ledger = ledger` في أول الدالة |
| `_open_turn` (`:1605-1608`) | يعيد `AppendedMessage` من `threads.append(...)`، أو `None` إن كان المنفذ غير موصول |
| `_metered`، قبل `while True` | `if record.user_message is not None: yield _accepted_event(record.user_message)` داخل `try` نفسها. فالتخلّي عند هذا الإطار يمر بـ `finally` |
| المهلة (`:2042-2060`) | `terminal = True`، ثم `_settle_cut`، ثم `event = _with_turn_ids(_timeout_event(cap_s), record)`، ثم `_settle_ledger(replied=False, code="agent.failed", status=502)`، ثم `yield event` |
| `except Exception` (`:2061-2071`) | `_settle_cut`، ثم `_settle_ledger(replied=False, code=exc.code if isinstance(exc, AppError) else "common.internal", status=… أو 500)`، ثم `raise` |
| حدث `error` (`:2079-2090`) | بعد `_settle_cut`: `event = _with_turn_ids(event, record)`، و`err = _in_band_error(event)`، ثم `_settle_ledger(replied=False, code=err.code, status=err.status)`. فالمخزَّن هو ما يتلقاه عميل JSON حرفياً |
| `final` (`:2094-2107`) | بعد `_persist_reply`: `event = _with_user_message_id(event, record)`، ثم `_settle_ledger(replied=record.message is not None, code=None if replied else "agent.failed", status=None if replied else 502)` (AC‑5.3) |
| `finally` (`:2118-2119`) | `if not terminal:` → `try: await self._settle_cut(...)` و`finally: await self._settle_ledger(record, replied=False, code="agent.failed", status=502)`. فالدفتر يُبلَّغ حتى إن أُلغي الحفظ الجزئي. و`_capture` بعدهما كما هو |
| `_persist_reply` و`_persist_partial` | `reply_to=None if record.user_message is None else record.user_message.id` في `threads.append(...)` |
| `_in_band_error` | `AppError(..., extensions=_turn_ids(event.data))` و`RateLimitedError(..., extensions=...)` |
| `invoke_once` (`:1445-1449`) | إن كان `record.user_message` مضبوطاً و`record.message is None`: `AppError("the reply was produced but could not be saved", code="agent.failed", extensions={"user_message_id": ...})` (د‑ت6). وإلا `common.internal` كما اليوم (المنفذ غير موصول) |

- **لا يتغير:** `invoke_workflow`، و`begin_step`/`finish_step`، و`_persist_step_output`، و`_capture`، والحارس `terminal`، والإطار `notice`.
- **السجلات (ق‑5، AC‑7.5):** تحذير واحد جديد `orchestrator.turn_ledger_failed` بـ `{"conversation_id", "phase": "accepted" | "settled"}`. لا مفتاح ولا نص.

#### 3.6 framework — `src/app/framework/errors.py`
```python
class AppError(Exception):
    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        status: int | None = None,
        extensions: Mapping[str, str] | None = None,
    ) -> None:
        ...
        # th-3 -- RFC 9457 extension members (§3.2), ids only. The API renders a
        # CLOSED subset of names (`api/errors.PROBLEM_EXTENSION_KEYS`); empty is
        # the norm. Read-only so a handler cannot mutate a shared error.
        self.extensions: Mapping[str, str] = MappingProxyType(dict(extensions or {}))


class RateLimitedError(AppError):
    def __init__(self, detail=None, *, code=None, status=None, retry_after_s=None,
                 extensions: Mapping[str, str] | None = None) -> None: ...
```

#### 3.7 port — `src/app/framework/ports/idempotency_store.py` ومحوّله
```python
@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    """REPLAY: response_body is the completed body. IN_PROGRESS: response_body is
    the provisional body `checkpoint` stored (None when there is none) and age_s
    is the seconds since the claim, on the DATABASE clock (None when the row
    vanished under us). Every other outcome: both None."""

    outcome: ClaimOutcome
    response_body: Json | None = None
    age_s: float | None = None


class IdempotencyStore(Protocol):
    async def claim(self, ctx, *, endpoint: str, key: str, request_hash: str) -> IdempotencyClaim: ...

    async def checkpoint(
        self, ctx: ExecutionContext, *, endpoint: str, key: str, response_body: Json
    ) -> None:
        """Store a PROVISIONAL body on an UNFINISHED claim (th-3). completed_at
        stays NULL, so a repeat still meets IN_PROGRESS; `complete` replaces it;
        `release` no longer removes the row -- an operation that has had an
        effect must not free its key."""

    async def complete(self, ctx, *, endpoint: str, key: str, response_body: Json) -> None: ...

    async def release(self, ctx, *, endpoint: str, key: str) -> None:
        """Drop a claim that is neither completed NOR checkpointed."""
```
`SqlIdempotencyStore`:
- **`checkpoint`:** `UPDATE platform.idempotency_keys SET response_body = :body WHERE workspace_id = :ws AND endpoint = :ep AND idempotency_key = :key AND completed_at IS NULL`.
- **`_inspect`:** يضيف إلى `select(...)` العمود `func.extract("epoch", func.now() - idempotency_keys.c.created_at).label("age_s")`. ومع `IN_PROGRESS` يعيد `IdempotencyClaim(IN_PROGRESS, response_body=row["response_body"], age_s=float(row["age_s"]))`. ما زالت عبارة واحدة على مسار التعارض.
- **`release`:** يضيف `idempotency_keys.c.response_body.is_(None)` إلى الشرط.
- **`idempotent()`** (ث‑4 والمسارات الأربعة الأخرى) لا يستعمل `checkpoint`، فجسده المؤقت `NULL` دائماً، ولا يتغير سلوكه.

#### 3.8 api — `src/app/api/v1/idempotent_turn.py` (جديد)
```python
"""The Idempotency-Key flow of `POST /conversations/{id}/messages` (gap th-3).

Not `idempotent()`: a turn has an effect (a stored question, a billed run)
BEFORE it has an answer, the streaming branch has no body to store, and the
replay must be rebuilt from the rows as they are now. See design.md §1 for the
state machine this module implements."""

TURN_STALE_MARGIN_S: Final[float] = 120.0
_HANDLE_KIND = "agent_turn"
_HANDLE_VERSION = 1
_DETACHED: set[asyncio.Task[None]] = set()  # strong refs for shielded writes


def turn_endpoint(conversation_id: Uuid) -> str:
    """f"POST /conversations/{conversation_id}/messages" -- the id is the
    conversation's CANONICAL id (`conversation.id`), never the path text (d-t10)."""


def content_hash(content: Json) -> str:
    """sha256 over json.dumps(content, sort_keys=True, separators=(",", ":"),
    ensure_ascii=False).encode("utf-8"). `content` ONLY: `stream` and `Accept`
    choose how the answer is delivered, not which question it is (q-5)."""


def stale_after_s(settings: Settings) -> float:
    """settings.limits.stream_max_duration_s + TURN_STALE_MARGIN_S (FR-14)."""


@dataclass(frozen=True, slots=True)
class TurnHandle:
    """What `response_body` holds for this endpoint. No text, ever (FR-17)."""

    conversation_id: Uuid
    user_message_id: Uuid
    seq: int
    outcome: Literal["accepted", "replied", "failed"]
    message_id: Uuid | None = None
    code: str | None = None
    status: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @classmethod
    def of_accepted(cls, turn: AcceptedTurn) -> TurnHandle: ...
    @classmethod
    def of_outcome(cls, outcome: TurnOutcome) -> TurnHandle: ...
    def lost(self) -> TurnHandle:
        """An accepted turn whose server died: outcome="failed",
        code="agent.failed", status=502, message_id=None (FR-14)."""
    def to_json(self) -> Json:
        """{"kind": "agent_turn", "v": 1, "conversation_id", "user_message_id",
        "seq", "outcome", "message_id", "code", "status",
        "usage": {"prompt_tokens", "completion_tokens"} | null}"""
    @classmethod
    def from_json(cls, raw: Json | None) -> TurnHandle | None:
        """None for a missing body or one that is not a v1 turn handle."""


class IdempotentTurnLedger:
    """`TurnLedger` over `IdempotencyStore` for ONE claimed key."""

    def __init__(self, store: IdempotencyStore, ctx: ExecutionContext, *,
                 endpoint: str, key: str) -> None: ...

    accepted_turn: AcceptedTurn | None  # set BEFORE the checkpoint write

    async def accepted(self, turn: AcceptedTurn) -> None:
        """self.accepted_turn = turn; then store.checkpoint(TurnHandle.of_accepted(turn))
        via _shielded."""

    async def settled(self, outcome: TurnOutcome) -> None:
        """store.complete(TurnHandle.of_outcome(outcome)) via _shielded."""

    async def release_unless_accepted(self) -> None:
        """store.release(...) only while accepted_turn is None (FR-11)."""


async def _shielded(write: Awaitable[None]) -> None:
    """Run the store write as its own task and await it through asyncio.shield.
    The normal path awaits it to the end. When the AWAITER is cancelled (Starlette
    cancels an SSE body on disconnect, and anyio re-cancels every await of that
    unwind), the write keeps running to completion in the background, held in
    _DETACHED until done (d-t3b)."""


async def claim_turn(
    store: IdempotencyStore,
    ctx: ExecutionContext,
    *,
    conversation_id: Uuid,
    key: str,
    content: Json,
    stale_after_s: float,
) -> IdempotentTurnLedger | TurnHandle:
    """CLAIMED     -> IdempotentTurnLedger (the caller runs the turn)
    REPLAY      -> TurnHandle.from_json(body); a body that is not a turn
                   handle -> AppError(code="common.internal")
    MISMATCH    -> ConflictError("Idempotency-Key was already used on this
                   conversation with different content")
    IN_PROGRESS -> age_s is None or age_s < stale_after_s
                     -> ConflictError("an earlier attempt with this Idempotency-Key
                        is still running; retry shortly")
                   else, handle present -> handle.lost()
                   else -> AppError("the earlier attempt with this Idempotency-Key
                        was lost before its message was stored", code="agent.failed")
    The key value is never in a log line, nor in these details (d-t13)."""


def replay_error(handle: TurnHandle) -> AppError:
    """outcome != "replied" -> AppError(_REPLAYED_DETAIL, code=handle.code,
    status=handle.status, extensions={"user_message_id": ..., "message_id"?: ...})."""


async def replay_events(handle: TurnHandle, reply: MessageOut | None) -> AsyncIterator[AgentEvent]:
    """Exactly two events: accepted {conversation_id, user_message_id, seq,
    replayed: True}; then final (built from `reply` + handle.usage +
    user_message_id) when outcome == "replied", else error {code, status,
    detail, user_message_id, message_id?}. No token, no notice."""
```

#### 3.9 api — `routers/conversations.py` و`dto/conversations.py` و`errors.py` و`streaming.py`
```python
# dto/conversations.py
class MessageOut(BaseModel):
    ...
    fallback: MessageFallback | None
    # th-3 -- required-present, no default: the key is always on the wire.
    reply_to: str | None
    token_count: int | None
    seq: int
    created_at: datetime
```
```python
# routers/conversations.py
async def post_message(
    conversation_id: str,
    body: MessageCreateIn,
    request: Request,
    services: Services,
    ctx: Context,
    idempotency_key: IdempotencyKey = None,
) -> MessageOut | StreamingResponse:
    if idempotency_key is not None:
        guard_key(idempotency_key)                                   # 422 before any I/O (AC-3.9)
    conversation = await services.conversations.get.execute(ctx, conversation_id)   # 404 (FR-18)
    req = AgentRequest(conversation_id=conversation_id, input=body.content, stream=body.stream)
    agent_key = conversation.agent_key.value
    sse = wants_sse(request, body.stream)
    if idempotency_key is None:
        return await _run_turn(services, ctx, agent_key, req, sse=sse, ledger=None)  # today, byte for byte
    claim = await claim_turn(
        services.idempotency, ctx, conversation_id=conversation.id, key=idempotency_key,
        content=body.content, stale_after_s=stale_after_s(services.settings),
    )
    if isinstance(claim, TurnHandle):
        return await _replay_turn(services, ctx, claim, sse=sse)
    try:
        return await _run_turn(services, ctx, agent_key, req, sse=sse, ledger=claim)
    except BaseException:
        await claim.release_unless_accepted()                        # FR-11
        raise


async def _run_turn(services, ctx, agent_key, req, *, sse: bool,
                    ledger: IdempotentTurnLedger | None) -> MessageOut | StreamingResponse:
    """Today's two branches with `ledger=` passed through. SSE: after `invoke`
    returns, a ledger with no accepted_turn (seam unwired) is released at once
    (d-t12)."""


async def _replay_turn(services, ctx, handle: TurnHandle, *, sse: bool) -> MessageOut | StreamingResponse:
    """outcome == "replied" -> get_message.execute(ctx, handle.conversation_id,
    handle.message_id) (404 if gone), mapped by _to_message_out. Then SSE ->
    StreamingResponse(sse_stream(replay_events(handle, reply), ...)); JSON ->
    reply, or raise replay_error(handle)."""

# _to_message_out:         reply_to=message.reply_to
# appended_to_message_out: reply_to=message.reply_to
```
```python
# api/errors.py
# th-3 -- the RFC 9457 extension members (§3.2) this API may add. CLOSED: a
# name outside this tuple is never rendered, whatever an error carries.
PROBLEM_EXTENSION_KEYS: Final[tuple[str, ...]] = ("user_message_id", "message_id")

def problem(code, status, *, detail=None, correlation_id=None, instance=None,
            errors=None, extensions: Mapping[str, object] | None = None) -> Json:
    """... then, for each name in PROBLEM_EXTENSION_KEYS whose value is a
    non-empty str: body[name] = value. Never overrides a core member."""

# problem_from_error(exc, ...)  -> problem(..., extensions=exc.extensions)
# problem_from_event(data, ...) -> problem(..., extensions=data)
```
```python
# websocket/streaming.py, _pump
body = {**event.data, "type": event.type,
        "conversation_id": _envelope_conversation_id(event, conversation_id)}

def _envelope_conversation_id(event: AgentEvent, requested: str | None) -> object:
    """The request's id wins, as today -- except an `accepted` frame of an invoke
    that named no thread, whose data carries the thread just opened (d-t11)."""
```
- **التسلسل:** `MessageOut` يسلسل كما اليوم. و`ensure_ascii=False` في SSE وJSON يحفظ العربية بلا escape (NFR‑10).

#### 3.10 الوثائق الملزمة (backend-developer، نصاً عربياً بنمط الملفات)
- **`03 §0` فقرة المثالية:**
  - `postMessage` بين العمليات، والقائمة الملزمة هي ما يعلنه `openapi.yaml`.
  - نطاقه (المساحة + المحادثة + المفتاح)، والبصمة على `content` وحده.
  - **استثناء صريح لـ `postMessage`:** الإعادة تعيد **النتيجة نفسها** لا الرد الأول المخزَّن. JSON: `MessageOut` من الصف الحالي (محذوف ⇒ 404)، أو الـ problem نفسه. البث: إطاران. والفشل **بعد** كتابة رسالة المستخدم لا يحرّر المفتاح.
  - بث `runWorkflow` يبقى خارج المخزن كما هو.
  - المفتاح الجاري 409، والدور الذي ضاع خادمه يُعاد `agent.failed` بعد `stream_max_duration_s` + 120 ثانية.
  - الاحتفاظ: يُكنس بعد يومين من أول استعمال بجولة يومية، فيعيش بين يومين وثلاثة، وبعده مفتاح جديد.
  - المفتاح لا يفصل المستخدمين داخل المساحة (SR‑8)، فعلى العميل UUID جديد لكل محاولة يقصدها المستخدم.
  - إعادة الجسد بمسافات زائدة بصمة أخرى ⇒ 409.
- **`03 §2`:** سطر `reply_to` في `MessageOut` وتعليقه (معنى `null`)، وتعليق `MessageCreateIn` يذكر الرأس.
- **`03 §3.1`:** إطار `accepted` بمثاله، والترتيب الجديد، وقاعدة «تجاهل النوع المجهول»، و`final.user_message_id`، والعضوان على `error`، وإطارا الإعادة.
- **`03 §3.2`:** سطر `{"type":"accepted",…}` في رسائل الخادم، و`final` يحمل `user_message_id`، و`error.problem` قد يحمل العضوين.
- **`03 §4`:** عضوا الامتداد الاختياريان، مجموعة مغلقة، معرّفات فقط.
- **`06 §4`:**
  - `reply_to` في سطر `E Message`.
  - فقرة `reply_to` (من يكتبه، ومعنى `None`، والحذف الناعم A‑6).
  - INV‑CV6.
  - جملة `:102` تصير: «فشل بلا رسالة لا يُكتب رسالةً (ق‑3)، بل يُسجَّل في سجل المفتاح `platform.idempotency_keys` للطلب الذي أرسل `Idempotency-Key` فقط، ويعيش مثله (ث‑3)».
  - `GetMessage` في قائمة حالات الاستخدام.

## 4. البيانات

**الترحيل:** `migrations/versions/conversations/0009_message_reply_to.py`
- `revision = "0009_message_reply_to"`: 21 حرفاً، أقل من حد 32 (`test_migration_revision_ids.py`).
- `down_revision = "0008_message_status"`: رأس السلسلة الحالي.

```python
def upgrade() -> None:
    # One ALTER per column: test_conversations_schema_parity.py reads
    # "ALTER TABLE conversations.messages ADD COLUMN <name>".
    op.execute("ALTER TABLE conversations.messages ADD COLUMN reply_to uuid NULL;")


def downgrade() -> None:
    # DATA LOSS by design: every stored question-answer link is discarded.
    op.execute("ALTER TABLE conversations.messages DROP COLUMN IF EXISTS reply_to;")
```

**ما يقوله docstring الترحيل** (بنمط `0008`):
- **الغرض:** ث‑3. يربط رسالة المساعد بسؤالها، لأن الترتيب `seq` لا يكفي حين يتداخل دوران (WebSocket، جهازان).
- **`uuid` لا `text`:** بنوع `id` نفسه.
- **`NULL` يعني «لم يُسجَّل»:** رسائل المستخدم، وكل صف قبل الترحيل، ومخرجات الخطوات، والملخصات، وصفوف `load_seed`. فلا backfill ولا `DEFAULT`.
- **لماذا لا FK:**
  - فحص FK في Postgres يتجاوز RLS، فلا يثبت أن السؤال من المساحة نفسها.
  - ولا يثبت أنه من المحادثة نفسها ولا أنه رسالة `user`.
  - ويقيّد ترتيب الحذف في `purge.py`.
  - السلامة يفرضها الإدراج المحروس في المحوّل (`INSERT … SELECT … WHERE EXISTS`)، في العبارة نفسها.
- **بلا فهرس:** العمود إسقاط لا شرط. لا مسار يرشّح به (س‑10). و`EXISTS` في الإدراج يمر بالمفتاح الأساسي `id`.
- **RLS والصلاحيات:** بلا تغيير.
- **التوافق:** الشيفرة القديمة تُدرج بلا العمود (`NULL`) وتقرأ بـ `Table` القديم. والجديدة تسمّيه، فيجب أن يسبق `migrate` الصورة الجديدة، وهو الترتيب القائم في `docker-compose.yml` و`deploy/rolling-deploy.sh`.
- **التراجع:** `downgrade` يحذف العمود وكل الروابط المسجلة. الأفضل في التراجع تركه، فهو خامل للشيفرة القديمة.

**`01-data-model.md` §2.4.** سطر بعد `fallback`:
```sql
  reply_to        uuid NULL,                        -- ث‑3 · معرّف رسالة المستخدم التي يجيبها هذا المساعد، يُكتب في الإدراج نفسه؛ NULL ⇒ لم يُسجَّل
```
وتعليق تحت الجدول يذكر: من يكتبه (`_persist_reply` و`_persist_partial` فقط)، ومعنى `NULL`، وINV‑CV6، والإدراج المحروس بدل FK وسببه، وبقاء المعرّف بعد حذف السؤال ناعماً (A‑6)، وأن لا فهرس.

**`01-data-model.md` §4.2‑ب:**
- تعليق `response_body`: «NULL ⇒ مُطالَبٌ به وقيد التنفيذ. وجسدٌ مع `completed_at IS NULL` ⇒ جسدٌ مؤقت (`checkpoint`، ث‑3): العملية أحدثت أثرها ولم تنتهِ، فلا يُحرَّر المفتاح».
- فقرة جديدة: لـ `postMessage` يحمل `response_body` مقبض الدور (`{kind, v, conversation_id, user_message_id, seq, outcome, message_id, code, status, usage}`)، بلا نص، ~300 بايت.
- **الاحتفاظ** (بدل «لا سياسة احتفاظٍ في v1»): «تكنس `ops-scheduler` يومياً الصفوف التي مضى على `created_at` فيها يومان (`IDEMPOTENCY_KEYS_RETENTION`، `src/app/ops/retention.py:140`)، فيعيش المفتاح بين يومين وثلاثة. والمفتاح بعد كنسه جديد».

**الخصائص:**

| الخاصية | القيمة |
|---|---|
| الفهارس | لا شيء |
| القيود | لا شيء (السلامة في المحوّل، د‑ت5) |
| RLS | بلا تغيير |
| الأدوار والصلاحيات | بلا تغيير |
| متوافق مع الإصدار السابق | نعم (expand فقط) |
| يمر بـ `test_zero_downtime_migrations.py` | نعم: `ADD COLUMN … NULL` بلا `DEFAULT`، ولا `DROP` في `upgrade()` |
| القفل | عبارة `ALTER` واحدة، تعديل catalog، `ACCESS EXCLUSIVE` لحظي تحت `lock_timeout` الذي يضبطه `env.py` |
| الحجم | 16 بايت لكل رسالة مساعد جديدة |
| `platform.idempotency_keys` | لا تغيير في المخطط. صف واحد لكل إرسال بمفتاح، ~300 بايت للجسد و~700 بايت بالفهارس. يُكنس بعد يومين (§7، الخطر 5) |

**التراجع:** يُرجَع `app` أولاً إلى صورة ما قبل ث‑3، ثم يُشغَّل التراجع عبر الأداة المعتمدة. `alembic downgrade` المباشر على المكدس ممنوع (`docs/stack-commands.md`). أما الاختبار الحي في CI فيستعمل `alembic.command` على قاعدة الاختبار المعزولة، بنمط `test_message_status_migration_live.py` (`_move`). ومقابض المفاتيح المخزنة خاملة للشيفرة القديمة: إعادة بمفتاح على صورة قديمة تجد صفاً مكتملاً لـ endpoint لا تعرفه الصورة القديمة، لأن الصورة القديمة لا تقرأ هذا الـ endpoint أصلاً.

## 5. خطة الاختبار المقترحة

**المستويات:**
- **unit:** تحت `tests/unit/`، وتُشغَّل محلياً: `pytest tests/unit tests/architecture tests/eval`.
- **integration:** تحت `tests/integration/` (`live_db`). تُكتب الآن، وتُشغَّل في CI job `integration` فقط (س‑14).
- **مراجعة:** فحص يدوي يقوم به code-reviewer.

**مفاتيح المساعدة في الجدول:**
- `O` = `test_orchestrator.py`، وفيه `_FakeThreads` ومعه القائمة الجديدة `reply_tos`. و`L` = دفتر مزيّف في الملف نفسه يسجل `accepted`/`settled`.
- `R` = `test_api_conversations_router.py` (منسّق حقيقي، و`InMemoryConversationRepository`، و`InMemoryIdempotencyStore` باسم `S`، و`S.calls`).
- `IT` = `test_api_idempotent_turn.py`. و`ST` = `test_idempotency_store_sql.py`. و`IL` = `test_idempotency_live.py`.
- `SQL` = `test_conversations_sql_statements.py` (`_PG`). و`RLS` = `test_conversations_repository_rls.py`.
- `MIG` = `test_message_reply_to_migration_live.py`. و`MIGU` = `test_message_reply_to_migration.py`.
- `FR` = `test_send_reconcile_frames.py`. و`DIS` = `test_asgi_disconnect_semantics.py`. و`C` = `test_api_conventions.py`.
- «لا تشغيل» = `S.calls` بلا `checkpoint`/`complete`/`release`، وصفر `agents.create`، وصفر `_admit`/`_capture` على منفذ الاستهلاك المزيّف، ولا رسالة جديدة في `GET …/messages`.

| AC | المستوى | المكان | ما يُثبت |
|---|---|---|---|
| AC‑1.1 | unit | `R`: SSE، أول إطار `accepted`. `user_message_id` = `id` رسالة `user` في `GET`، و`seq` و`conversation_id` مطابقان | FR‑1 |
| AC‑1.2 | unit | `FR`: وكيل يمر بـ `_FallbackLLM` (فشل قبل أول chunk)، فالترتيب `accepted`، `notice`، `token`+، `final`، و`accepted` مرة واحدة | |
| AC‑1.3 | unit | `FR`: وكيل لا يُنتج حدثاً، و`sse_stream` بـ `keepalive_interval_s` صغير. أول بايتات الجسم هي `event: accepted`، قبل `:keep-alive` | NFR‑4 |
| AC‑1.4 | unit | `O`: `final.data["user_message_id"]` = قيمة `accepted`. `FR`: الإطار بعد حذف `user_message_id` يساوي إطار اليوم لـ `_StreamingAgent` و`_CitingAgent` ودور متحوّل | NFR‑5 |
| AC‑1.5 | unit | `test_websocket_accepted_frames.py` (qa): `invoke` على محادثتين بلا انتظار. لكل دور `accepted` واحد قبل أول `token` له، بمعرّفاته | |
| AC‑1.6 | unit | `test_api_agents_router.py`: `stream: true` بلا `conversation_id` ومعه `space_id`. `accepted.conversation_id` = الخيط الجديد | |
| AC‑1.7 | unit | `R`: 403 (viewer)، و404 (مجهولة)، و409 (محذوفة بين القراءة والإلحاق، بمنفذ مزيّف)، و422، و429 (منفذ استهلاك يرفض). problem+json، لا `event: accepted`، ولا رسالة `user` | FR‑3 |
| AC‑1.8 | unit | `O`: `conversations=None`، فلا `accepted`، ولا `user_message_id` على `final`، والباقي كما اليوم | |
| AC‑1.9 | unit | `FR`: نص عربي طويل. `set(accepted.data) == {"conversation_id", "user_message_id", "seq"}`، ولا جزء من النص في الإطار | NFR‑9 |
| AC‑1.10 | unit | `FR`: `runWorkflow` بالبث، فلا `event: accepted` | |
| AC‑2.1 | unit + integration | `O`: `reply_tos[-1]` = معرّف رسالة المستخدم. `R`: `GET` وJSON `POST` بالقيمة نفسها. `test_api_agents_router.py`: `message.reply_to`. `RLS`: ذهاب وإياب | FR‑4، FR‑5 |
| AC‑2.2 | unit | `O`: المهلة، و`error` بعد tokens، واستثناء المنسّق، و`aclose()` بعد token. `reply_to` صحيح مع `status: "partial"` | |
| AC‑2.3 | unit + integration | `R`: رسالة `user` بـ `"reply_to" in m` و`None`. `test_workflow_orchestration.py`: الخطوة `None`. `test_workers_bootstrap.py` الحالي يبقى أخضر. `SQL`: `_hydrate_message` لصف `reply_to=None`. `RLS`/`MIG`: صف قديم ⇒ `NULL` | |
| AC‑2.4 | unit | `O`: دوران متزامنان بالنص نفسه عبر `_FakeThreads` يتحكم في الترتيب (`u1, u2, a2, a1`)، فـ `a1.reply_to = u1`، و`a2.reply_to = u2` | |
| AC‑2.5 | unit | `SQL`: الإدراج بـ `reply_to` عبارتان بالضبط: `INSERT … SELECT … WHERE EXISTS … RETURNING` ثم `UPDATE conversations.conversations`، ولا `UPDATE conversations.messages`. والإدراج بلا `reply_to` مطابق لعبارة اليوم | NFR‑1 |
| AC‑2.6 | unit | `R`: جسد فيه `"reply_to"` في أعلاه وداخل `content`. رسالة `user` بـ `None`، والمساعد بالمعرّف الحقيقي | FR‑6 |
| AC‑2.7 | unit + integration | `UC`: `reply_to` على `role="user"`، أو نص ليس UUID ⇒ `ValidationError` ولا قراءة. `D`: INV‑CV6. `UC` مع المستودع المزيّف: `reply_to` لرسالة محادثة أخرى، أو لرسالة `assistant` ⇒ `ValidationError` ولا صف. `SQL`: لا صف من `RETURNING` ⇒ `ValidationError` ولا `UPDATE`. `RLS`: رسالة محادثة أخرى، ورسالة مساحة أخرى ⇒ `ValidationError`، وصفر صفوف جديدة | |
| AC‑2.8 | unit | `R`: `DELETE` لرسالة المستخدم بعد الجواب، ثم `GET`: السؤال غائب، و`reply_to` باقٍ | A‑6 |
| AC‑2.9 | unit | `R`: `stream: false`، و`reply["reply_to"]` = `id` رسالة `user` في `GET` | |
| AC‑2.10 | unit + integration | `MIGU`: `upgrade()` ينفذ `ADD COLUMN reply_to uuid NULL` وحده، و`downgrade()` ينفذ `DROP COLUMN IF EXISTS reply_to` وحده. parity: الاسم في الأماكن الثلاثة، و`nullable` بلا `server_default`. `MIG` (CI): `0009 → 0008` فيغيب العمود والصف القديم سليم، ثم `0008 → 0009` فيعود `is_nullable = YES` والصف `NULL`. والرفع في `finally` | NFR‑6 |
| AC‑3.1 | unit | `R`: JSON وSSE بلا رأس، و`S.calls == []` | NFR‑1 |
| AC‑3.2 | unit | `R`: بمفتاح، ثم الإعادة. 200 و`MessageOut` مساوٍ حقلاً حقلاً، و«لا تشغيل»، وعدد الرسائل ثابت | FR‑9 |
| AC‑3.3 | unit | `R`: المفتاح نفسه بمحتوى آخر ⇒ 409 `common.conflict`، و«لا تشغيل» | |
| AC‑3.4 | unit | `R`: وكيل بطيء (`_SlowAgent`) وطلبان متزامنان. الثاني 409، والأول يكتمل، ورسالة `user` واحدة | |
| AC‑3.5 | unit | `R`: `K1` و`K2` بالنص نفسه ⇒ سؤالان وجوابان | |
| AC‑3.6 | unit | `R`: `K` على `C1` ثم على `C2` ⇒ دور جديد في `C2`. `IT`: `turn_endpoint` يختلف، ويستعمل `conversation.id` القانوني (طلب بمعرّف بحروف كبيرة يقع على الصف نفسه) | FR‑8 |
| AC‑3.7 | unit | `R`: منفذ استهلاك يرفض (429) ثم يقبل. والإعادة بـ `K` تشغّل وتكتمل، و`S.calls` فيه `release`. ومثله 422 و409 | FR‑11 |
| AC‑3.8 | unit | `R`: `_HalfAgent` (`error` بعد tokens)، و`stream: false`. 502 ومعه `user_message_id` و`message_id`. والإعادة: الـ problem نفسه (الرمز، والحالة، والعضوان)، و«لا تشغيل» | FR‑16 |
| AC‑3.9 | unit | `R` مُعلَم: `""` و`"   "` و256 حرفاً ⇒ 422، و`S.calls == []`، وصفر `agents.create`، وصفر قراءة محادثة | |
| AC‑3.10 | unit | `R`: الأول بالبث، والإعادة بـ JSON ⇒ 200 `MessageOut`، لا 409 | FR‑13 |
| AC‑3.11 | unit | `R`: اكتمل، ثم `DELETE …/messages/{id}` للجواب، ثم الإعادة ⇒ 404 `common.not_found`، والنص غائب من `response.content`، و«لا تشغيل». ومثله بالبث (404 قبل أي إطار) | FR‑17 |
| AC‑3.12 | unit | `R`: اكتمل، ثم `DELETE /conversations/{id}`، ثم الإعادة ⇒ 404، و`S.calls` لم يزد | FR‑18 |
| AC‑3.13 | unit | `R`: اكتمل، ثم يُحذف الصف من `S.rows` (محاكاة الكنس)، ثم الإعادة ⇒ دور جديد وسؤال ثانٍ | FR‑19 |
| AC‑3.14 | unit | `R`: نص عربي بتشكيل وemoji، ونص 100,000 حرف. الإعادة replay. `GET` يعيد النص مطابقاً بايتاً ببايت. `S.rows[pk].response_body` بلا `text`/`content`/`citations`، و`len(json.dumps(...))` متقارب للحالتين (< 512). `IT`: `content_hash` ثابت للعربي | NFR‑10، NFR‑11 |
| AC‑4.1 | unit | `R`: بالبث، ثم الإعادة بالبث. إطاران بالترتيب: `accepted` (`replayed: true` والقيم الأولى) ثم `final` (`message_id` و`content` و`citations` و`user_message_id` مساوية للأصل). لا `token` ولا `notice`، و«لا تشغيل» | FR‑12ب |
| AC‑4.2 | unit | `R`: `_HalfAgent` بالبث، ثم الإعادة. `accepted` (`replayed`)، ثم `error` بالرمز الأصلي ومعه العضوان | |
| AC‑4.3 | unit | `O`: `aclose()` بعد token، و`L.settled[-1]` = `replied=False`، و`agent.failed`، و`message_id` = الجزء. `IT`: `IdempotentTurnLedger.settled` بمخزن تنتظر كتابته `Event`، ثم يُلغى المنتظِر، ثم يُفتح `Event`: الكتابة تكتمل (`_shielded`). `R`: الإعادة بعدها ⇒ `accepted` ثم `error` `agent.failed` ومعه `message_id` | §7.1 (3) |
| AC‑4.4 | unit | `O`: `aclose()` مباشرة بعد `accepted`. `L.settled[-1]` بلا `message_id`. `R`: الإعادة بـ `user_message_id` نفسه، ورسالة `user` واحدة | |
| AC‑4.5 | unit | `R`: صف `S` في S2 بعمر 10 ثوانٍ ⇒ 409 problem+json (لا `text/event-stream`)، والبث الأول لا يتأثر (وكيل بطيء يكمل) | |
| AC‑4.6 | unit + integration | `IT`: عمر `T − 1` ⇒ `ConflictError`، وعمر `T` ⇒ `handle.lost()`. `R`: `S.set_age(pk, 720)` على صف S2 ⇒ `accepted` (`replayed`، والمعرّف المسجل) ثم `error` `agent.failed`، وJSON ⇒ 502 ومعه `user_message_id`. وصف S1 قديم ⇒ 502 بلا معرّفات وبلا بث. واختبار أن `stale_after_s(settings)` = `stream_max_duration_s + 120`. `ST`: عمود العمر في `_inspect`. `IL`: `created_at` مُرجَع 800 ثانية ⇒ `age_s ≥ 800` | FR‑14 |
| AC‑4.7 | unit | `IT`: مفاتيح `TurnHandle.to_json()` هي القائمة المغلقة بالضبط. `R`: جسد كل صف بعد كل مسار (`replied`، و`failed`، و`accepted`) بلا `text`/`content`/`citations`/`detail` | FR‑17 |
| AC‑4.8 | unit | `R`: بث بلا مفتاح. `S.calls == []`، و`accepted` بلا `replayed` | |
| AC‑4.9 | unit | `R`: الأول JSON، والإعادة بالبث ⇒ `accepted` (`replayed`) ثم `final` للجواب نفسه | FR‑13 |
| AC‑4.10 | unit + integration | `R`: الدور الأول بالبث `S.calls == ["claim", "checkpoint", "complete"]`، والإعادة `["claim"]` فقط. `ST`: `checkpoint` و`complete` عبارة `UPDATE` واحدة لكل منهما | NFR‑2 |
| AC‑5.1 | unit | `R`: وكيل يصدر `error` قبل أي token، بالبث. `error` فيه `user_message_id` بلا `message_id`. والإعادة: `accepted` ثم `error` بالرمز نفسه، ولا رسالة مساعد `reply_to` فيها المعرّف | FR‑15 |
| AC‑5.2 | unit | `R`: الحالة نفسها بـ JSON ⇒ 502 ومعه `user_message_id`. والإعادة: الـ problem نفسه، و«لا تشغيل» | |
| AC‑5.3 | unit | `O`: `_FakeThreads` يفشل لـ `assistant` (`_fail_on`). `final` بلا `message_id` ومعه `user_message_id`، و`L.settled[-1]` = `agent.failed` بلا `message_id`. `R` بالبث: الإعادة ⇒ `accepted` ثم `error` `agent.failed`. و`R` بـ JSON: 502 ومعه `user_message_id` (لا 500)، والإعادة مثله (د‑ت6) | |
| AC‑5.4 | unit | `O`: `aclose()` بعد `accepted` وقبل أي token. `R`: الإعادة ⇒ `accepted` ثم `error` `agent.failed` بلا `message_id` | |
| AC‑5.5 | unit | `R`: بعد كل حالة فشل في US‑5، `GET`: لا رسالة مساعد جديدة، وكل `status` في `{"complete", "partial", None}` | ق‑3 |
| AC‑5.6 | unit | `R`: بلا مفتاح، فشل قبل أول token. `S.calls == []`، والسجل فيه رسالة `user` وحدها | |
| AC‑6.1 | unit (contract) | `C`: الاختبار القائم `test_the_idempotency_header_is_declared_on_exactly_the_contracts_operations` أخضر، و`postMessage` في الطرفين | |
| AC‑6.2 | unit (contract) | `C`: خصائص `MessageOut` متساوية في الطرفين، و`reply_to` في `LIVE.MessageOut.required`، و`SPEC.reply_to` بـ `format: uuid` و`nullable` | |
| AC‑6.3 | مراجعة | وصف SSE في `postMessage` و`invokeAgent` | |
| AC‑6.4 | مراجعة | `03 §0` | |
| AC‑6.5 | مراجعة | `03 §2` و§3.1 و§3.2 و§4 | |
| AC‑6.6 | مراجعة | `06 §4` | |
| AC‑6.7 | unit + مراجعة | parity (AC‑2.10). ومراجعة `01 §2.4` و§4.2‑ب | |
| AC‑6.8 | مراجعة | `gaps.md` | |
| AC‑6.9 | مراجعة | docstrings `idempotency.py` و`idempotency_store.py` | |
| AC‑7.1 | unit + integration | `R`: مساحتان. `K` نفسه في B على محادثتها ⇒ دور مستقل، و`K` من B إلى محادثة A ⇒ 404، و`S.calls` لم يزد. `IL`: اختبار RLS القائم للمفاتيح | |
| AC‑7.2 | unit | `R`: viewer بمفتاح ⇒ 403 `authz.forbidden`، و`S.rows == {}` | |
| AC‑7.3 | integration (CI) | `RLS`: رسائل بـ `reply_to` في A. جلسة B: `list_messages` صفر صفوف، و`SELECT reply_to FROM conversations.messages WHERE id = :id` صفر صفوف | NFR‑7 |
| AC‑7.4 | unit | `R`: مستخدمان في مساحة واحدة، المفتاح والمحتوى نفسيهما ⇒ الثاني replay (السلوك الموثّق، س‑12) | |
| AC‑7.5 | unit | `R` مع `caplog`: كل سجل جديد (`orchestrator.turn_ledger_failed`، وأي سطر للمسار) بلا قيمة المفتاح ولا النص. و`accepted` والامتداد ومقبض الصف معرّفات فقط. `test_api_errors.py`: اسم خارج `PROBLEM_EXTENSION_KEYS` لا يُعرض، والامتداد لا يطغى على `code`/`status` | |
| AC‑7.6 | unit | `R`: بلا `Authorization` وبرمز تالف، بمفتاح ⇒ 401، و`S.calls == []` | |
| AC‑7.7 | architecture | البوابة 4 (`lint-imports`) و`tests/architecture/` | NFR‑8 |

**تحقق إضافي (qa-engineer):**
- **`DIS`:** يشغّل `uvicorn.Server` داخل العملية على `127.0.0.1` بمنفذ عشوائي، وتطبيقاً فيه `@app.middleware("http")` (كـ `correlation_middleware`). عميل socket خام يرسل الطلب ثم يغلق.
  - مسار JSON ينتظر `Event`: يكمل بعد القطع ولا يُلغى.
  - مسار `StreamingResponse`: يُلغى مولّده ويمر بـ `finally`.
  - يثبت §7.1 على النسخ المثبتة. إن غيّر ترقية uvicorn أو Starlette السلوك، يفشل الاختبار وينبه.
- **NFR‑3:** في كل إعادة (`R`)، صفر استدعاء لمزوّد LLM المزيّف ولـ `agents.create` ولـ `_admit`/`_capture`.

**تعديلات لازمة على fakes وتوقعات قائمة:**
- **`test_orchestrator.py:1296`:** `append` يقبل `reply_to: str | None = None`، ويسجله في `self.reply_tos` منفصلة، ولا يمد tuple `self.appended`، ويعيده في `AppendedMessage`.
- **`test_workflow_orchestration.py`:** التوقيع نفسه.
- **`support_idempotency.py`:** `checkpoint`، و`set_age(pk, seconds)` (افتراضياً 0)، و`calls: list[str]` بأسماء العمليات، و`release` لا يحذف صفاً له جسد. وتبقى `claims` كما هي لمستعمليها القائمين.
- **`support_conversations.py`:** فحص `reply_to` في `append_message` بنمط المحوّل، و`get_message` في `ConversationUseCases(`.
- **كل اختبار يقارن تسلسل الإطارات بالضبط** في الملفات التي تبني منسّقاً بمنفذ محادثات موصول (المتوقع: `test_orchestrator.py`، و`test_llm_fallback.py`، و`test_api_conversations_router.py`، و`test_api_agents_router.py`، و`test_websocket_endpoint.py`). يُحدَّث ليبدأ بـ `accepted`، أو يُرشَّح منه `accepted` صراحة. هذا تعديل مقصود (المخاطرة 3 في `requirements.md`). ولا يُخفَّف أي تأكيد آخر.

## 6. ترتيب التنفيذ والتوازي

**قاعدة كل commit أخضر:**
- **parity:** ترحيل `0009` وعمود `reply_to` في `Table` المحوّل يدخلان **commit واحداً**. data-engineer يكتب ملفاته في شجرة العمل ولا يعمل لها commit، وbackend-developer يضمها إلى commit المحوّل، وتذكر رسالة الـ commit الاثنين.
- **العقد:** DTO (`MessageOut.reply_to`)، ورأس `post_message`، و`openapi.yaml` في commit واحد (`test_api_conventions.py:313-326`، `:495`).
- **المنفذ:** `IdempotencyStore.checkpoint` وتنفيذاه (`SqlIdempotencyStore` و`InMemoryIdempotencyStore`) في commit واحد.
- **الإطار:** `accepted` في `_metered` وتحديث كل توقع قائم لترتيب الإطارات في commit واحد.

1. **الفرع:** `feat/data-search-send-reconcile` من `master` على `b104878`.
2. **بالتوازي:**
   - **(أ) data-engineer**، يكتب ولا يعمل commit:
     - `0009_message_reply_to.py`؛
     - و`01-data-model.md` §2.4 و§4.2‑ب (نص §4 أعلاه)؛
     - وتمديد `test_conversations_schema_parity.py`؛
     - و`test_message_reply_to_migration.py`؛
     - و`test_message_reply_to_migration_live.py`؛
     - وتمديد `test_conversations_repository_rls.py`.

     يتفق مع backend-developer على: الاسم `reply_to`، والنوع `uuid`، ورسالة الرفض `ValidationError`.
   - **(ب) backend-developer، بالترتيب:**
     1. **framework والأخطاء:** `AppError.extensions`، و`api/errors.py`، و`test_api_errors.py`.
     2. **سجل المفاتيح:** المنفذ، والمحوّل، و`support_idempotency.py`، و`ST`، و`IL`، و`test_api_idempotency.py`، و`guard_key`.
     3. **conversations:** domain، وport، وapplication (`GetMessage`)، وadapter، وcomposition root، و`support_conversations.py`، واختبارات `D` و`UC` و`SQL`. **commit مشترك** مع ملفات 2‑أ.
     4. **المنسّق:** §3.5، وتمديد `_FakeThreads` وfake الـ workflow، واختبارات `O`، وتحديث توقعات الترتيب.
     5. **api والعقد** في commit واحد: `idempotent_turn.py`، والراوتر، وDTO، و`streaming.py`، و`openapi.yaml`، و`C`. ثم `IT` و`R` و`test_api_agents_router.py` و`test_websocket_endpoint.py`.
     6. **الوثائق:** `03-api-spec.md` و`06-domain-models.md`.
3. **البوابات 1–5 محلياً:** `ruff format --check`، و`ruff check`، و`mypy src`، و`lint-imports`، و`pytest tests/unit tests/architecture tests/eval` (المخرج كله في ملف ثم `grep -E "passed|failed"`، retro ث‑2). ثم `deploy/prometheus/test-rules.sh` (بلا قاعدة جديدة). **لا تشغيل لـ `tests/integration` محلياً.**
4. **qa-engineer**، بعد 2‑ب‑5: `DIS` و`FR` و`test_websocket_accepted_frames.py` و`test-plan.md`، ومراجعة التغطية مقابل §5.
5. **backend-developer:** فقرة «الإصلاح» في `gaps.md`، بعد اخضرار البوابات.
6. **CI job `integration`** على الـ PR يثبت تطبيق `0009` عبر `provision`، ورحلة `MIG`، وAC‑2.7 وAC‑7.3 في `RLS`، وامتدادات `IL`. يجب أن تكون نتيجته معروفة قبل بوابة ②.

**المراحل التي تُتخطى:** UX (3)، والواجهة (7)، وstaging (13)، وبوابة الإنتاج (14)، كما في `status.md`. مرحلة البيانات (5) **لازمة**، لأن هناك ترحيلاً.

## 7. المخاطر والقرارات

**لا بنود ⚠️ جديدة.** ما يمس العقد والتخزين والأمن حسمته بوابة ①:
- الإطار (ق‑2)؛
- والسجل (ق‑3)؛
- ونتيجة الفشل (ق‑4)؛
- والنطاق بلا المستخدم (س‑12)؛
- والامتداد (س‑2)؛
- و409 للجاري (س‑9)؛
- وحد FR‑14 للمفتاح الذي مات دوره.

لا سر جديد، ولا إعداد، ولا تبعية، ولا تغيير على deploy أو CI. ما يلي قرارات تصميم داخل تلك الحدود، ونتيجتا البندين اللذين لم يتحقق منهما المحلل، ومخاطر مُدارة.

### 7.1 ما لم يُتحقق منه في `requirements.md` §5.4 — النتيجة

**(1) فرع JSON عند قطع العميل: لا يُلغى المعالج.**
- **الدليل** (على النسخ المثبتة: uvicorn 0.51.0، Starlette 1.3.1، FastAPI 0.139.0، وgunicorn بعمّال uvicorn في `Dockerfile:277`):
  - `connection_lost` في `uvicorn/protocols/http/httptools_impl.py:124-126` يضبط `cycle.disconnected` و`message_event` فقط، ولا يلغي مهمة التطبيق.
  - `BaseHTTPMiddleware` (`correlation_middleware`، `api/main.py:423`) يلغي مجموعة مهامه بعد إرسال الرد فقط (`starlette/middleware/base.py:117-128`).
  - FastAPI يقرأ الجسد قبل المعالج، فلا `receive()` بعده.
- **النتيجة:** الدور يكمل، ويُحفظ، ويُسجَّل `replied` أو `failed`، والإعادة تعيده. وهذا ما يقوله حافة US‑3.
- **التصميم صحيح في الحالتين.** لو أُلغي المعالج بعد القبول: `CancelledError` يمر بـ `invoke_once` إلى `finally` الـ `_metered`، فتُسجَّل نتيجة التخلّي (`agent.failed`) عبر `_shielded`. والراوتر لا يحرّر المفتاح، لأن `accepted_turn` مضبوط.
- **الإثبات:** `DIS` (مسار JSON)، و`R` (إلغاء مهمة الطلب بعد `accepted`: لا `release`، و`settled` مسجَّل).

**(2) نافذة 409 بعد موت البث: T = 600 + 120 = 720 ثانية على ساعة Postgres.**
- **الافتراض:** دور حي لا يتجاوز 600 ثانية + 60 ثانية + زمن ما قبل البث + زمن الحفظ. التفصيل:
  - المهلة الكلية 600 ثانية تبدأ عند أول سحب من `_metered` (`_deadline`، `orchestrator.py:2023`). ويدخل فيها الوقت المحجوز في `yield`.
  - إن امتلأ مخزن الإرسال (عميل ميت أو بطيء)، يقف `yield`. وnginx بلا `send_timeout` صريح في `deploy/nginx/`، فقيمته الافتراضية 60 ثانية: يغلق الاتصال، فيصل uvicorn انقطاع، فيُلغى البث، فتُسجَّل النتيجة.
  - و`proxy_read_timeout` (300 ثانية) لا يُستنفد، لأن نبضة `:keep-alive` كل 15 ثانية.
  - ما قبل البث وما بعده عبارات قاعدة بيانات قليلة، والهامش 120 ثانية يغطيها.
- **الساعة:** `now() - created_at`، وكلاهما من Postgres. فلا انحراف بين نسخ `app`.
- **ما يمد النافذة فعلاً:** موت العامل. مثلاً `deploy/rolling-deploy.sh` و`--graceful-timeout 30` يقتلان البث الجاري. فتبقى الإعادة 409 حتى 720 ثانية من أول طلب، ثم تُعطى نتيجة قاطعة (FR‑14).
- **الإثبات:** `IT` (العتبة عند `T − 1` و`T`)، و`R` (S1 وS2 قديمان، والمعامل المحسوب من الإعدادات)، و`ST` و`IL` (العمر بساعة القاعدة).

**(3) قطع SSE (لم يُسأل عنه، لكنه يقرر الحل).**
- uvicorn يعلن ASGI `spec_version` 2.3 (`httptools_impl.py:228`). فـ `StreamingResponse` يستمع للانقطاع ويلغي البث بمجال إلغاء anyio (`starlette/responses.py:270-281`).
- وفي ذلك المجال يُعاد إلغاء كل `await` في مسار الفك. لذلك الحفظ الجزئي عند التخلّي «أفضل جهد» كما يقول الكود.
- وكتابة النتيجة هنا تُفصل في مهمة محمية (`_shielded`، د‑ت3ب)، حتى لا يبقى أشيع سيناريو لـ Android (إغلاق التطبيق أثناء البث) 409 اثنتي عشرة دقيقة.

### 7.2 قرارات التصميم (بلا حاجة إلى قرار بشري)

- **د‑ت1 — دفتر يناديه المنسّق (`TurnLedger`)، لا غلاف في الـ API حول الأحداث.**
  - الغلاف لا يعرف معرّف الجزء المحفوظ عند التخلّي، ولا يستطيع أن يسجل قبل خروج الإطار الطرفي.
  - المنسّق يعرف الحقائق، ويملك المسارات الطرفية، ويحفظ فيها قبل `yield` (قاعدة `_settle_cut`).
  - المنفذ صغير، اختياري، ولا يعرف المفاتيح. و`invokeAgent` وWebSocket لا يمررانه.
- **د‑ت2 — توسيع `IdempotencyStore` لا منفذ ثانٍ.** يفرض NFR‑8 منفذاً واحداً للسجل. الإضافات ثلاث:
  - `checkpoint`؛
  - و`age_s` والجسد المؤقت مع `IN_PROGRESS`؛
  - وشرط `release` الأضيق.

  لا تغيّر سلوك المستعملين الخمسة القائمين، لأن جسدهم المؤقت `NULL` دائماً. وبديل «نتيجة خامسة `STALE` في `claim`» رُفض: يغيّر المفردات المغلقة لكل مستعمل، ويضع سياسة مسار واحد في المخزن.
- **د‑ت3 — العتبة T = `stream_max_duration_s` + 120 ثانية، ثابت في الشيفرة (`TURN_STALE_MARGIN_S`)، والإعادة بعد العتبة لا تكتب شيئاً (A‑4).**
  - البديل «عقد إيجار بنبضة دورية» (`UPDATE` كل 30 ثانية لكل دور جارٍ) يقصّر النافذة إلى دقيقة تقريباً. لكنه يضيف كتابة دورية لكل بث، ومهمة خلفية لكل دور. رُفض.
  - والإعادة التي تكتب حكم «ضاع» تجعل الإعادة كتابة. رُفض أيضاً.
- **د‑ت3ب — كتابة الدفتر محمية من الإلغاء** (`_shielded`، مع مرجع قوي في `_DETACHED`).
  - المسار العادي ينتظرها حتى النهاية. والمسار الملغى يتركها تكمل في الخلفية.
  - **لا** يغيّر هذا الحفظ الجزئي عند التخلّي، فهو باقٍ أفضل جهد (خارج النطاق).
  - في `finally` تُنادى `settled` في `finally` داخلية حول `_settle_cut`، فتصل حتى لو أُلغي الحفظ الجزئي. و`message_id` فيها هو ما حُفظ فعلاً.
- **د‑ت4 — صف S1 قديم** (مات العامل بين المطالبة وتسجيل القبول) **⇒ 502 `agent.failed` بلا معرّفات، وبلا بث، ولا يُحرَّر المفتاح.**
  - لا نعرف هل كُتبت رسالة المستخدم. والتحرير قد يكرر السؤال، وإطار `accepted` بلا معرّف كذب.
  - النافذة بين `append` و`checkpoint` أجزاء من الثانية. ويُوثَّق ذلك في `03 §0`.
- **د‑ت5 — سلامة `reply_to` بإدراج محروس** (`INSERT … SELECT … WHERE EXISTS (… role = 'user') RETURNING`)، **لا FK ولا CHECK ولا فهرس.**
  - يحقق AC‑2.7 (المحادثة نفسها) وAC‑2.5 (عبارة واحدة، بلا `UPDATE`).
  - والاستعلام الفرعي تحت RLS. وFK لا يثبت المحادثة ولا الدور ولا المساحة، ويعيق `purge.py`.
  - و«رسالة `user`» شرط مجاني يضيّق الثابت.
- **د‑ت6 — `invoke_once`: دور مقبول وصل `final` وفشل حفظ جوابه ⇒ 502 `agent.failed` ومعه `user_message_id`، بدل 500 `common.internal` اليوم** («persistence is not wired»، وهي جملة خاطئة لهذه الحالة).
  - بدونه تعطي الإعادة (502، AC‑5.3) غير ما أُعطي أول مرة (500)، فينكسر FR‑12أ.
  - الـ 500 يبقى للمنفذ غير الموصول وحده. يُبلَّغ product-owner به.
- **د‑ت7 — `AppError.extensions` عام، والعرض مغلق** (`PROBLEM_EXTENSION_KEYS`، اسمان). لا يطغى عضو امتداد على عضو أساسي. فـ «المجموعة الدقيقة» في `problem()` تصير الأساسية + اثنين اختياريين معلنين.
- **د‑ت8 — `final` المعاد مفاتيح المنصة فقط:** `message_id`، و`content`، و`citations`?، و`fallback`?، و`usage`، و`user_message_id`. مفاتيح الوكيل الخاصة (`job_id`…) لا تُحفظ فلا تُعاد. و`citations` المعادة هي المحفوظة (المفاتيح الخمسة، ث‑1)، وتساوي إطار `rag_agent` الأصلي.
- **د‑ت9 — `detail` لا يُخزَّن.** الإعادة تحمل جملة ثابتة إنجليزية، والرمز والحالة والمعرّفات هي العقد. و`detail` الأصلي قد يحمل نصاً من مزوّد.
- **د‑ت10 — النطاق بمعرّف المحادثة القانوني** (`conversation.id` من القاعدة) لا نص المسار. فطلبان بحالتي حروف مختلفتين للمعرّف يقعان على الصف نفسه.
- **د‑ت11 — غلاف WebSocket:** `_pump` يجعل `conversation_id` المرسَل في الطلب يغلب. فإطار `accepted` لـ `invoke` بلا محادثة كان سيخرج بـ `conversation_id: null`. الاستثناء لهذا الإطار وحده، والإطارات الأخرى كما هي.
- **د‑ت12 — مفتاح مع منفذ محادثات غير موصول:** يُحرَّر فور عودة `invoke`، ويُبث الدور بلا حماية. لا رسالة تُكتب، فلا أثر يُحمى. وفرع JSON يرفع 500 كما اليوم فيُحرَّر.
- **د‑ت13 — تفاصيل 409 لهذا المسار لا تكرر قيمة المفتاح**، بخلاف سابقة `idempotent()`. لا يحتاجها العميل، وتقل بها المواضع التي قد تصل سجلاً.
- **د‑ت14 — `GetMessage` حالة استخدام جديدة، لا مسار** (س‑10). تستعمل `repository.get_message` القائم، وترفض المحذوف ناعماً.

### 7.3 المخاطر المُدارة

1. **تغيير دلالات ملزمة في `03 §0`** (ت‑1…ت‑3): موافق عليه (ق‑1). وAndroid وُعد بالقديمة (`backend-reply.md:216-222`)، فتذكر فقرة `gaps.md` أن أسطره 96–97 و220 لم تعد صحيحة.
2. **إطار جديد يكسر عميلاً صارماً** (ت‑4): Android يتجاهل المجهول (`backend-reply.md:92`). والقاعدة تُكتب في `03 §3.1` وفي `openapi.yaml`. والواجهة الأمامية خارج المستودع لم تُفحص.
3. **حكم «ضاع» قد يسبق نتيجة متأخرة.** إن تأخرت كتابة `settled` أكثر من 720 ثانية (توقف القاعدة)، تعطي إعادة بعد العتبة `agent.failed`، ثم تكتب الكتابة المتأخرة النتيجة الحقيقية، فتعطي الإعادة التالية غيرها. غير قابل للحدوث إلا بتوقف القاعدة أكثر من دقيقتين بعد مهلة الدور. والسجل (`reply_to`) يبقى الحقيقة.
4. **الحفظ الجزئي عند التخلّي أفضل جهد** (خارج النطاق). قد تقول الإعادة `agent.failed` بلا `message_id` بينما حُفظ جزء لاحقاً، أو العكس. يجده العميل في السجل بـ `reply_to` (FR‑20).
5. **حجم السجل.** صف لكل إرسال بمفتاح، ~700 بايت بالفهارس، يعيش يومين إلى ثلاثة.
   - مليون إرسال في اليوم ≈ 1.4–2.1 GB قائمة. هذا على سقف السعة 30/ث متواصلاً ≈ 2.6 مليون يومياً.
   - وعلى حجم Android الحالي ضئيل. والكنس اليومي يحذف ما يُدرج، وجدول السجل عليه ضبط autovacuum (`platform/0005_ledger_autovacuum`).
   - تغيير مدة الاحتفاظ خارج النطاق.
6. **كلفة كل إرسال بمفتاح:** 3 عبارات على السجل، كل منها في جلسة مستأجر مستقلة (NFR‑2 معتمد). على 30/ث ≈ 90 عبارة/ث إضافية، أولية بالمفتاح الأساسي.
7. **ترتيب النشر:** صورة ث‑3 على قاعدة غير مُرحَّلة تُفشل كل إدراج وقراءة رسالة (`UndefinedColumn`). التخفيف: `migrate` يسبق `app`، ولا نشر تلقائي. ونافذة النشر المتدرج تترك رسائل replica قديم `reply_to: null`، وهذا مطابق للدلالة.
8. **اختبارات ترتيب الإطارات القائمة** تتغير عمداً. يراجع code-reviewer أن التعديل يضيف `accepted` ولا يحذف تأكيداً آخر.
9. **الاختبارات الحية لا تُشغَّل محلياً** (س‑14): `MIG` و`RLS` و`IL` في CI قبل بوابة ②. وكل منطق مساعد فيها له توأم unit (`MIGU`، `ST`).
10. **تجربة الإعادة كاملة ضد Postgres حقيقي غير مضافة:** المحوّلان يُثبتان حياً كلٌّ وحده (`IL`، `RLS`)، والتركيب يُثبت بـ unit (`R`). يقرر qa-engineer في `test-plan.md` إن كانت الفجوة مقبولة.
11. **ملاحظة خارج النطاق:** `CLAUDE.md` يذكر 8 عقود لـ `lint-imports`، والناتج 9 (retro ث‑1 وث‑2). لا يُصلح هنا.

</div>
