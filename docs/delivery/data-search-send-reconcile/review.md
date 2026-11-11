<div dir="rtl">

# مراجعة الشيفرة: ث‑3 مصالحة الإرسال الغامض

- **النطاق المراجَع:** `git diff b104878..26411ea` في `/home/AIZZAK` (الفرع `feat/data-search-send-reconcile`، 11 commit، 52 ملفاً). قرأتُ الملفات المتغيرة كاملة، لا الفروق وحدها. تجاهلتُ تعديل `status.md` غير المحفوظ.
- **الحكم:** **PASS** (لا ملاحظة حاجبة). الملاحظات المهمة الثلاث يُستحسن إصلاحها قبل بوابة ②، وشرط CI `integration` قائم كما في `test-plan.md`.
- **البوابات (أعدتُ تشغيلها محلياً):** `ruff format --check` (1010 ملفاً منسّقة)، و`ruff check` (سليم)، و`mypy src` (552 ملفاً، بلا أخطاء)، و`lint-imports` (9 عقود سليمة، وليس في `.importlinter` أي تغيير)، و`pytest tests/unit tests/architecture tests/eval`: النتيجة في آخر الملف. لم أشغّل `tests/integration` ولا أي ترحيل.

## الملاحظات

| # | الشدة | الملف:السطر | الملاحظة | الإصلاح المطلوب |
|---|---|---|---|---|
| 1 | مهمة | `src/app/api/v1/idempotent_turn.py:207-212` | **فشل كتابة الدفتر بعد مغادرة المنتظِر يُبتلع بلا أي سجل.** `_reap` يقرأ `task.exception()` ليمنع تحذير «never retrieved» ثم يتركه. أما في المسار العادي فيصل الفشل إلى `_settle_ledger`، وهو يسجّل `orchestrator.turn_ledger_failed`. **السيناريو:** يغلق مستخدم Android التطبيق أثناء البث، فيُلغى الجسم، وتنفصل `complete` في مهمة خلفية، ثم تفشل (القاعدة مشغولة، أو `_translate` يرفع). يبقى الصف في S2، وتعطي كل إعادة 409 حتى 720 ثانية ثم `agent.failed`، والجواب قد يكون محفوظاً بـ`reply_to`. ولا يترك ذلك أثراً في السجلات. هذا أشيع سيناريو تحميه الميزة (§7.1‑3). | في `_reap`: إن لم تُلغَ المهمة وكان لها استثناء، يُكتب تحذير واحد (مثلاً `idempotent_turn.detached_write_failed`) بلا المفتاح ولا النص، بالمعرّفات والمرحلة فقط (ق‑5، AC‑7.5)، مع `exc_info`. ويكفيه اختبار unit واحد بـ`caplog` يمدّد `test_a_failure_after_the_awaiter_left_is_reaped_not_left_unretrieved`. |
| 2 | مهمة | `docs/design/03-api-spec.md:12-13` (§0 فقرة ث‑3) و`:476-481` (§4) | **409 و429 المُعادان لا يميزهما العقد عن «جارٍ، أعد قريباً».** الإعادة تعيد الرمز والحالة المخزّنين كما هما. وهذا صحيح بحسب FR‑12أ، والاختبار `test_a_failure_after_the_question_is_stored_is_a_502_with_ids_and_replays_the_same` يعيد `409 common.conflict` فعلاً. لكن `409 common.conflict` هو أيضاً رد «المحاولة الأولى ما زالت تجري؛ أعد قريباً». و429 في أول مرة يحمل `Retry-After` (من `RateLimitedError`)، أما المُعاد فـ`AppError` عادي (`idempotent_turn.py:331-336`)، فلا يحمل الرأس (`api/main.py:542`). **السيناريو:** حارس المزوّد مشبع بعد قبول الرسالة، فيصل 429 داخل البث. عميل يطبّق «429/409 ⇒ أعد بالمفتاح نفسه» يدور يومين إلى ثلاثة على الإجابة نفسها، ولا يُشغَّل دور أبداً. الإشارة الوحيدة التي تميز الحالتين هي وجود `user_message_id`، والعقد لا يقولها. | جملة في `03 §0` (فقرة ث‑3) وفي وصف `postMessage` في `openapi.yaml`: «problem يحمل `user_message_id` نتيجةٌ نهائية لهذا المفتاح، أياً كان رمزه، وإعادته بالمفتاح نفسه تعيده كما هو. ولسؤال جديد مفتاح جديد. و409 بلا `user_message_id` هو المفتاح الجاري». ويُذكر أن `Retry-After` لا يُعاد. ثم يُنقل التنبيه إلى `gaps.md` لعميل Android. لا تغيير في الشيفرة. |
| 3 | مهمة | `src/app/api/v1/idempotent_turn.py:355-363`؛ `docs/design/03-api-spec.md:423` | **(N‑1) `final` المعاد بلا المفتاح `text`، وAndroid وُعد به صراحة.** قيل لـ Android إن `final` هو `{text, citations, message_id, content:{text,attachments}, usage}` (`aizzak-android/.../backend-reply.md:93`). و`final` الحي لوكلاء المحادثة يحمل `text` فعلاً، وقد تحقق QA من ذلك في `test-plan.md` §3. أما المعاد فيُبنى بمفاتيح المنصة وحدها (د‑ت8). و`03 §3.1:423` يقول «مفاتيح الوكيل الخاصة (`job_id`…) لا تُعاد»، ولا يسمّي `text`، مع أن `text` ليس من نوع `job_id` في نظر العميل. **السيناريو:** Android يقرأ `final.text` ليعرض الجواب، فيعرض فراغاً في الإعادة وحدها. ولا أوصي بإضافة `text` إلى المعاد: `final.text` الأصلي نص الوكيل الخام، و`content.text` المحفوظ قد يبدأ بجملة التحول (6.4)، فلا يتساويان. | توثيق صريح: في `03 §3.1:423` تُضاف `text` إلى ما لا يُعاد، مع جملة «اقرأ `content.text`». ويُضاف تنبيه في فقرة ث‑3 من `gaps.md` لعميل Android. |
| 4 | تحسين | `src/app/agents/orchestrator.py:1559-1561` | docstring `invoke_once` ما زال يقول إن الدور الذي لم يحفظ رسالة «يحدث فقط حين لا يكون منفذ المحادثات موصولاً… (500)». بعد د‑ت6 صار هناك فرع ثانٍ: سؤال محفوظ وجواب لم يُحفظ ⇒ 502 `agent.failed` (`:1573-1581`). وهذا يشمل `invokeAgent` بلا مفتاح أيضاً. | تحديث الفقرة لتذكر الفرعين: 502 مع `user_message_id` حين حُفظ السؤال، و500 للمنفذ غير الموصول. |
| 5 | تحسين | `src/app/api/errors.py:48-58` | `PROBLEM_EXTENSION_KEYS` أُدرج بين `_FALLBACK_CODE` و`_FALLBACK_STATUS`. ففصل الزوج الذي يشرحه تعليق واحد فوقهما («What a malformed in-band error payload degrades to»)، وصار `_FALLBACK_STATUS` تحت تعليق ث‑3. | نقل الثابت وتعليقه إلى ما بعد `_FALLBACK_STATUS`. |
| 6 | تحسين | `tests/unit/test_api_conversations_router.py:1889-1894`؛ `tests/unit/test_api_idempotent_turn.py:86-88` | **اختبارا د‑ت10 (المعرّف القانوني) فارغان من جهة ما يسمّيانه.** الأول يرسل المعرّف القانوني نفسه، فيمر حتى لو مرّر الراوتر نص المسار بدل `conversation.id`. والثاني يختبر تنسيق السلسلة فقط. الشيفرة صحيحة (`routers/conversations.py:411`، `conversation_id=conversation.id`)، لكن لا شيء يثبتها. | مستودع مزيّف يعيد من `get` معرّفاً بحروف صغيرة لطلب بحروف كبيرة (أو monkeypatch على `conversations.get.execute`)، ثم تأكيد أن مفتاح الصف هو المعرّف القانوني. أو تغيير الاسم ليطابق ما يُثبت فعلاً. |
| 7 | تحسين | `tests/unit/test_api_conversations_router.py:2037` | اسم الاختبار `…is_a_502_with_ids…`، وهو يؤكد 409 (`:2049`) لأن `_HalfAgent` يرمي `ConflictError`. التعليق يشرح ذلك، لكن الاسم مضلل عند قراءة تقرير الفشل. | تسمية مثل `…keeps_its_code_with_both_ids_and_replays_the_same`. |
| 8 | تحسين | `src/app/api/v1/websocket/streaming.py:102-109` | **(N‑5)** فرع `requested is None and event.type == "accepted"` في `_envelope_conversation_id` لا يُبلَغ من السلك. WebSocket يبني `AgentRequest` بلا `space_id` (`:400`)، فطلب `invoke` بلا محادثة يُرفض 422 قبل أي إطار (`orchestrator.py:1744`). والـ docstring يقول «without this the first frame of such a turn would announce null»، ومثل هذا الدور لا يحدث. | جملة في الـ docstring بأن الفرع لا يُبلَغ إلى أن يحمل بروتوكول WS `space_id`. أو حذف الفرع (د‑ت11 بُني على افتراض خاطئ). لا ضرر في بقائه. |
| 9 | تحسين | `src/app/agents/orchestrator.py:2317-2328` | في مسار «استثناء الآلة حول الوكيل» يسجّل الدفتر الرمز، لكن الرد الحي لا يحمل `user_message_id`. في JSON الاستثناء بلا `extensions`. وفي SSE ينقطع البث بلا إطار، كما كان قبل الميزة. أما الإعادة فتحمل المعرّف. فالرد الأول والإعادة يختلفان في العضوين، وFR‑16 يطلبهما «حين يكون الفشل بعد قبول الرسالة». مسار نادر، والتصميم (§3.5) لم يطلب المعرّفات فيه. | اختياري: في `invoke_once` يُغلَّف الاستثناء بعد القبول بالامتداد، أو يُكتب في `03 §4` أن هذا المسار قد يصل بلا العضوين. |

## مطابقة التصميم

**آلة الحالات** (`idempotent_turn.py`، `idempotency_store.py`، `infrastructure/persistence/idempotency.py`) تطابق جدولَي §1:
- `claim` → S1. ثم `checkpoint` (`UPDATE … WHERE completed_at IS NULL`، فلا يكتب checkpoint متأخر فوق جسد مكتمل) → S2. ثم `complete` → S3.
- `release` صار بشرط `completed_at IS NULL AND response_body IS NULL`. فلا يتغير شيء للمستعملين الخمسة القائمين (لا يستعملون `checkpoint`)، والمزيّف يطابق الشرط.
- العمر `extract(epoch, now() - created_at)` في عبارة `_inspect` نفسها، فكلا الطرفين من ساعة Postgres.
- العتبة `age_s < T` ⇒ 409 و`age_s ≥ T` ⇒ ضائع: حصرية تحت الحد وشاملة عنده، كما في AC‑4.6. و`age_s is None` (الصف اختفى) ⇒ 409.
- S1 القديم ⇒ 502 بلا معرّفات ولا بث، ولا يُحرَّر المفتاح (د‑ت4). وS2 القديم ⇒ `lost()`.

**التحرير قبل القبول فقط** (`routers/conversations.py:418-422`، و`release_unless_accepted`): `accepted_turn` يُضبط **قبل** كتابة `checkpoint`، فلا يُحرَّر المفتاح حتى إن فشلت الكتابة. وفي فرع البث يُحرَّر فور عودة `invoke` إن لم يُكتب سؤال (د‑ت12).

**الإدراج المحروس:** جمّعتُ `_insert_message` بلهجة `asyncpg` فخرج:
- `INSERT … SELECT $1::UUID, …, $13::UUID WHERE EXISTS (SELECT 1 FROM conversations.messages WHERE id=$14::UUID AND conversation_id=… AND workspace_id=… AND role=…) RETURNING id`.
- كل bind مصبوب بنوعه، فلا خطر `unknown → text`.
- الرفض يقع قبل `UPDATE` الإصدار، داخل `session.begin()`، فتُلغى المعاملة.
- عدد العبارات لم يتغير (NFR‑1).

**الحماية من الإلغاء (`_shielded`):** صحيحة.
- `ensure_future` يبدأ قبل أي نقطة تعليق، و`asyncio.shield` يمنع إلغاء المنتظِر من الوصول إلى الكتابة.
- `_DETACHED` يمسك المرجع القوي.
- يثبت ذلك `DIS` بخادم uvicorn حقيقي، و`IT::test_a_settled_write_survives_the_cancellation_of_its_awaiter`.
- الثغرة الوحيدة هي الصمت عند الفشل (الملاحظة 1).

**`accepted` أولاً:** يُبث داخل `try` في `_metered` قبل أول `_next_before`، فالتخلّي عنده يمر بـ `finally`. وفي WebSocket يمر كأي إطار. ولا يظهر في `runWorkflow` (لا يمر بـ `_metered`).

**الأدوار المتزامنة و`reply_to`:** القيمة تُقرأ من `record.user_message` الخاص بكل دور، لا من آخر رسالة. ويثبت ذلك `O::test_two_concurrent_turns_with_the_same_text_each_reply_to_their_own_question` و`WS::test_two_concurrent_turns_…`.

**الطبقات:** `TurnLedger` و`AcceptedTurn` و`TurnOutcome` معرّفة في `app.agents.orchestrator`، والـ API يستوردها (اتجاه مسموح). `conversations.domain` يبقى stdlib. و`application` يستورد `uuid` فقط. ولا `ignore_imports`.

**الأخطاء:**
- `AppError.extensions` جديد، و`MappingProxyType` يجعله للقراءة فقط.
- `problem()` يعرض اسمين فقط من `PROBLEM_EXTENSION_KEYS`، بقيمة نصية غير فارغة، ولا يطغى على عضو أساسي (`name not in body`).
- المجموعة المغلقة مطابقة لـ `03 §4` ولمخطط `Problem`.

**الترحيل `0009`:**
- `revision` من 21 حرفاً، و`down_revision = "0008_message_status"` (رأس السلسلة).
- `ADD COLUMN reply_to uuid NULL` بلا `DEFAULT`، و`DROP COLUMN IF EXISTS` في `downgrade`.
- docstring بنمط `0008`.

**العقود:** `openapi.yaml` (`postMessage` يعلن المفتاح، ووصف SSE لـ `postMessage` و`invokeAgent`، و`MessageOut.reply_to` بـ `format: uuid` و`nullable`، وعضوا `Problem`، ووصف `IdempotencyKey`) يطابق `03 §0/§2/§3.1/§3.2/§4` و`06 §4` و`01 §2.4/§4.2‑ب`.

**النطاق:** لا تغيير في `deploy/` ولا `.github/` ولا `.importlinter` ولا `pyproject.toml` ولا `.env.example`. كل ملف متغير مذكور في جدول §2.

**الأحكام المطلوبة:**

1. **الموضع الرابع `code=` في `test_error_catalog.py` (`handle.code or _AGENT_FAILED`): مقبول.**
   - هو صدى بحت لرمز لا يكتبه في المقبض إلا هذا المسار. ومصادر الرمز ثلاثة: `_in_band_error(event).code` (وهو صدى الموضع الأول القائم)، و`exc.code` لـ `AppError` مرفوع، والحرفيّان `agent.failed` و`common.internal`.
   - والرمز المجهول يتحول إلى `common.internal` في `problem_from_event` لفرع البث.
   - تصحيح صغير اختياري: تعليق `test_error_catalog.py:94-97` يقول «from a B1 event» فقط، ومسار `_settle_raised` مصدر آخر. ويمكن تضييق الحد بأن يرفض `TurnHandle.from_json` رمزاً لا يعرفه `spec_for`.
2. **تقسيم `_metered` إلى `_settle_failed` و`_settle_raised` و`_settle_abandoned` و`_settle_replied` (و`_with_fallback_key`): السلوك مطابق لجدول §3.5 سطراً سطراً.**
   - المهلة: `terminal` ثم `_settle_cut` ثم المعرّفات والدفتر (`agent.failed`/502 من `_in_band_error` على `_timeout_event`) ثم `yield`.
   - الاستثناء: `_settle_cut` ثم الدفتر بـ `exc.code` أو `common.internal`/500 ثم `raise`.
   - حدث `error`: `_settle_cut` ثم `_with_turn_ids` ثم `_in_band_error` ثم الدفتر.
   - `final`: `_persist_reply` ثم `user_message_id` ثم الدفتر، وقيمة `replied` هي `record.message is not None`.
   - `finally`: `try _settle_cut / finally _settle_ledger` ثم `_capture`.
   - `_with_fallback_key` نقل حرفي للشرط القديم. ولا تتغير الإطارات بلا دفتر، لأن `_settle_ledger` يعود قبل أي `await`.
   - الدفتر يُبلَّغ قبل إطار `notice` حين يكون الحدث `error`، وهذا مقبول لأن `notice` ليس طرفياً.
3. **انحرافات التصميم (د‑ت4، د‑ت6، د‑ت8، د‑ت9، د‑ت11، د‑ت13): مقبولة كلها**، مع ملاحظتين: د‑ت8 تحتاج التوثيق في الملاحظة 3، ود‑ت11 في الملاحظة 8.
   - **د‑ت6 (JSON «جواب لم يُحفظ» = 502 `agent.failed` بدل 500):** مقبول، وهو ما يجعل الإعادة تساوي الرد الأول. والـ 500 باقٍ للمنفذ غير الموصول (`:1582`). لكن الأثر يشمل `invokeAgent` بلا مفتاح أيضاً، وdocstring `invoke_once` لم يُحدَّث (الملاحظة 4). يُبلَّغ product-owner كما قرر التصميم.
   - **د‑ت8 (`final` المعاد بمفاتيح المنصة):** مقبول تقنياً، لأن مفاتيح الوكيل لا تُحفظ. ويحتاج التوثيق في الملاحظة 3.
   - **د‑ت9 (`detail` ثابت):** مقبول. يمنع إعادة نص من مزوّد، والرمز والحالة والمعرّفات هي العقد.
   - **د‑ت4 (S1 القديم 502 بلا معرّفات ولا تحرير):** مقبول وموثّق في `03 §0`.
   - **د‑ت13 (409 بلا المفتاح):** مقبول. يثبته `IT::test_other_content_under_the_same_key_is_a_conflict_without_the_key_in_it`، و`R` (`_KEY not in response.text`).
   - **د‑ت11 (غلاف WS):** صحيح، لكنه غير قابل للوصول اليوم (الملاحظة 8).
4. **ملاحظات QA:**
   - **N‑1:** لا تذكرها `03 §3.1` صراحة. السطر 423 يقول «مفاتيح الوكيل الخاصة (`job_id`…) لا تُعاد» ولا يسمّي `text`، وAndroid وُعد بـ `final.text`. ⇒ الملاحظة 3 (مهمة، توثيق).
   - **N‑3:** فجوة مقبولة. مسار الاستثناء يستدعي `_settle_cut` ← `_persist_partial` نفسها، وهي تمرر `reply_to` من السجل بلا شرط، وحالتا المهلة وحدث `error` مختبرتان. اختبار ثالث تحسين لا أكثر.
   - **N‑4:** مقبولة كما وثّقها التصميم (§7.3، المخاطرتان 3 و4). الجواب محفوظ وبه `reply_to`، فالمصالحة من السجل ممكنة (FR‑20). لكن صمت الفشل في المسار المنفصل (الملاحظة 1) يجعل N‑4 غير مرئية تشغيلياً، ولهذا الملاحظة 1 مهمة.

## الاختبارات

- **تغطية معايير القبول:** كل AC له اختبار آلي أو مراجعة، كما في مصفوفة `test-plan.md`. وتحققتُ من عينة تمثيلية:
  - AC‑3.2 وAC‑4.10: `S.calls` بالتسلسل الكامل.
  - AC‑3.7: `claim, release` ثم الإعادة تنجح.
  - AC‑4.3: الإلغاء الحقيقي، و`_shielded`.
  - AC‑4.6: العتبة عند `T − 1` و`T`، وS1 وS2.
  - AC‑2.5: العبارتان، والإدراج المحروس.
  - AC‑3.11: 404 بالشكلين، بلا نص.
  - AC‑7.1: 404 بلا لمس المخزن.
  الاختبارات الحية (`RLS` و`MIG` و`IL`) مكتوبة ولم تُشغَّل هنا (س‑14)، ونجاح CI `integration` شرط قبل بوابة ②.
- **اختبارات ترتيب الإطارات القائمة لم تُضعَف.** راجعتُ كل سطر محذوف في `test_orchestrator.py` و`test_llm_fallback.py` و`test_websocket_endpoint.py` و`test_api_conversations_router.py`. كل تعديل إضافة صرف: `"accepted"` في أول القائمة، أو إزاحة الفهرس بواحد (`events[0]` ← `events[1]`)، أو `__anext__()` إضافي معلَّق، أو `user_message_id` في مجموعة مفاتيح `final` المغلقة. ولم يُحذف أي تأكيد آخر ولم يُرخَّ. وفي `test_websocket_endpoint` أُضيف تأكيد كامل على قيمة `accepted`.
- **اختبار NFR‑5:** يقارن بقيم «قديمة» التقطها QA من `b104878` لا من الشيفرة الجديدة. هذا ما يجعله ذا معنى.
- **اختبارات فارغة أو مضللة:** اختبارا د‑ت10 (الملاحظة 6)، واسم اختبار AC‑3.8 (الملاحظة 7). ولا أرى اختباراً آخر يمر بلا التغيير. تحقق QA من أن `DIS` يفشل حين يُستبدل `_shielded` بـ `await` عادي.
- **أنماط المشروع متبعة:**
  - مزيّفات في الذاكرة: `InMemoryIdempotencyStore.calls`/`set_age`، والفحص المحروس في `InMemoryConversationRepository.append_message`.
  - لهجة `_PG` صريحة في اختبارات SQL.
  - توأم unit لكل اختبار حي (`MIGU` و`ST`).
  - علامات `live_*` على ملفات `tests/integration/`.

## نتيجة الاختبارات المحلية

`pytest tests/unit tests/architecture tests/eval` على `26411ea`: **exit 0**، و9123 ناجحاً، و37 متخطّى (كلها opt-in container harness، كما في `test-plan.md` §2)، وصفر فشل. والنتيجة مطابقة لعدّ QA.

</div>
