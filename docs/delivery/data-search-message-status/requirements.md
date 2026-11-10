<div dir="rtl">

# المتطلبات: ث‑2 حقل حالة الرسالة

- **المعرّف:** `data-search-message-status` (البند ث‑2 من `docs/delivery/data-search-android/gaps.md:23-27`).
- **الشيفرة التي فُحصت:** `master` على `0976bcb` (2026‑11‑10). أرقام الأسطر أدناه منه.
- **المصدر:** `gaps.md` §ث‑2، ومعيارا القبول 5 و6 في طلب Android (`aizzak-android/docs/delivery/tasks/data-search-agent/backend-change-request.md:122-123`)، وسؤال الطلب الأصلي (`backend-change-request.md:68`)، وطلب المتابعة `BE-DS-F01` بند 2 و4 ومعيار التحقق (`backend-follow-up-request.md:22,25,27`).
- **الميزة السابقة التي نبني عليها:** ث‑1 (`docs/delivery/data-search-android/`)، وقرارات المالك ق‑1…ق‑4 في `status.md` هناك.

## 1. الخلفية والهدف

اليوم لا تحمل رسالة المساعد أي حقل يقول كيف انتهى الدور:

1. الجواب المقطوع يُحفظ بالنص الذي وصل، وفي آخره جملة `⚠️ انقطعت الإجابةُ قبل اكتمالها.` (أو الإنجليزية) (`src/app/agents/orchestrator.py:438-439`، `orchestrator.py:487-496`).
2. جواب النموذج المحلي البديل (خطة السعة 6.4) يبدأ بجملة `⚠️ تعذّر الوصولُ إلى النموذج السحابيّ…` (`orchestrator.py:453-456`، `orchestrator.py:908-914`).
3. لا يعرف العميل أن الجواب جزئي، أو أن النموذج المحلي كتبه، إلا بمطابقة النص. وهذا ما قاله رد الخلفية لـ Android صراحة (`backend-reply.md:111`).

طلب Android: «سؤال قطع الاتصال بعد token وقبل `final`، وفشل المزود قبل أول token، والانتهاء الطبيعي، تنتج نتائج مستعادة قابلة للتمييز **دون البحث عن نص التحذير داخل الجواب**. جواب يتضمن عمداً جملة التحذير لا يغير حالته» (`backend-follow-up-request.md:25`). وطلب أيضاً ألا تُسمّى الرسائل القديمة «مكتملة» افتراضياً (`backend-follow-up-request.md:23`).

**المستفيد:** مستخدم تطبيق Android (وأي عميل آخر) يعيد فتح محادثة Data Search، أو يصالح بعد انقطاع.

**الهدف:** تحمل كل رسالة مساعد جديدة حالة منظَّمة محفوظة (`complete` أو `partial`) ومؤشراً منفصلاً للتحول إلى النموذج المحلي. تُعاد من السجل ومن الرد غير المتدفق. تُحدَّد الحالة من مسار الشيفرة الذي كتب الرسالة، لا من نصها.

## 2. النطاق

**داخل النطاق:**
- حالة الرسالة `status` لكل رسالة مساعد يكتبها المنسّق: `complete` من `_persist_reply`، و`partial` من `_persist_partial`. تُكتب في عبارة الإدراج نفسها.
- مؤشر منفصل `fallback` لرسالة كتبها النموذج المحلي البديل، مكتملة كانت أو جزئية. قيمته نفس قيمة `final.fallback` (`{from, to}`) أو `null`.
- إعادة الحقلين لكل رسالة في `GET /conversations/{id}/messages`.
- إعادتهما في الرد غير المتدفق على المسارين اللذين يستعملان `appended_to_message_out` (`src/app/api/v1/routers/conversations.py:120-138`):
  - `POST /conversations/{id}/messages` (`MessageOut`)؛
  - `POST /agents/{key}/invoke` (`AgentInvokeOut.message`، `src/app/api/v1/routers/agents.py:141-144`).
- قيمة محددة (`null`) لرسائل المستخدم وللصفوف المحفوظة قبل التغيير، بلا backfill.
- تحديث العقد والوثائق الملزمة: `openapi.yaml`، و`03-api-spec.md` §2 و§3.1، و`06-domain-models.md` §4، و`01-data-model.md` §2.4. وترحيل إضافي في سلسلة `conversations` إن اختار التصميم عموداً (المتوقع `0008`).
- اختبارات unit، واختبار parity للأعمدة، وتمديد اختبار RLS الحي (يُشغَّل في CI job `integration`، على قاعدة ق‑4).
- فقرة «الإصلاح» لـ ث‑2 في `docs/delivery/data-search-android/gaps.md`.

**خارج النطاق:**
- ث‑3: معرّف رسالة المستخدم في الأحداث، و`Idempotency-Key` للإرسال، وأي سجل «محاولة/دور» يحمل حالة **بلا رسالة مساعد** (مثل فشل المزود قبل أول token). انظر ت‑5 وس‑2.
- ث‑5 (الـ cursor للخلف) وث‑7 (التثبيت على ملف محذوف).
- قيمة `error` لرسالة محفوظة. لا يوجد اليوم مسار يكتب رسالة فاشلة (س‑2).
- تغيير جملتي التحذير أو حذفهما من النص (س‑5).
- تغيير إطارات البث `final` و`error` و`notice` في SSE وWebSocket (س‑6). ومنه: إضافة `message_id` للرسالة الجزئية إلى إطار `error` (تخص المصالحة، أي ث‑3).
- تخزين سبب الانقطاع (`timeout`/`error`/`abandoned`) على الرسالة (س‑9). يبقى في السجل `orchestrator.partial_reply_persisted` (`orchestrator.py:2206-2209`).
- حالة مهمة الوسائط. `status` في `final` لوكلاء الوسائط هو حالة المهمة (`src/app/agents/_shared.py:50-53`)، لا حالة الرسالة.
- جعل الحفظ في حالة التخلّي (`abandoned`) مضموناً. يبقى «أفضل جهد» كما قرر ب‑10 (`orchestrator.py:2059-2069`).
- تحديث `backend-reply.md` في مستودع Android (س‑11).
- تشغيل اختبارات القبول الحية الاثني عشر.

## 3. المتطلبات الوظيفية

| المعرّف | المتطلب | المصدر |
|---|---|---|
| FR‑1 | كل رسالة مساعد يكتبها `_persist_reply` (الدور انتهى بـ `final`) تُحفظ بحالة `complete`. | الطلب (ث‑2)؛ `gaps.md:25`؛ `backend-change-request.md:68`؛ `orchestrator.py:2044-2057`، `orchestrator.py:2339-2348` |
| FR‑2 | كل رسالة مساعد يكتبها `_persist_partial` (المهلة، أو حدث `error` بعد tokens، أو استثناء في المنسّق، أو تخلّي القارئ) تُحفظ بحالة `partial`. | `gaps.md:25`؛ `orchestrator.py:2001-2010`، `orchestrator.py:2011-2021`، `orchestrator.py:2029-2040`، `orchestrator.py:2059-2069`، `orchestrator.py:2183-2194` |
| FR‑3 | الحالة يحددها **مسار الشيفرة** الذي كتب الرسالة، لا نصها. جواب مكتمل يحتوي جملة التحذير حرفياً يبقى `complete`. | `backend-follow-up-request.md:25` |
| FR‑4 | تُكتب الحالة في عبارة الإدراج نفسها، ولا تتغير بعد ذلك. لا `UPDATE` لأجلها. | ثبات الرسالة: `06-domain-models.md:101` (INV‑CV3)، والسابقة AC‑1.9 في ث‑1 |
| FR‑5 | يُحفظ مؤشر منفصل `fallback` لكل رسالة مساعد أجاب عنها النموذج المحلي البديل (`record.fallback.switched`). قيمته نفس `fallback.describe()` الذي يضعه المنسّق على `final`، أي `{from:{provider,model}, to:{provider,model}}`. وفي غير ذلك `null`. ينطبق على `complete` و`partial` معاً. | `orchestrator.py:729-735`، `orchestrator.py:2046-2050`؛ `03-api-spec.md:383`؛ الرسالة الجزئية تحمل جملة التحول أيضاً: `orchestrator.py:2188` |
| FR‑6 | رسائل المستخدم، ورسائل الأدوار الأخرى (`system`/`tool`)، والصفوف المحفوظة قبل التغيير تُعاد بـ `status: null` و`fallback: null`. لا backfill، ولا تُسمّى القديمة `complete`. | `backend-follow-up-request.md:23`؛ `orchestrator.py:1562-1565`؛ سابقة ث‑1 (`01-data-model.md:208-209`) |
| FR‑7 | يعيد `GET /conversations/{id}/messages` المفتاحين `status` و`fallback` لكل رسالة. المفتاحان حاضران دائماً، ولا يُحذفان. | معيار القبول 5 و6 (`backend-change-request.md:122-123`)؛ عُرف ث‑1 «المفتاح حاضر دائماً» (`03-api-spec.md:105`) |
| FR‑8 | يحمل الرد غير المتدفق الحقلين نفسيهما: `MessageOut` من `POST /conversations/{id}/messages`، و`AgentInvokeOut.message` من `POST /agents/{key}/invoke`. وهي تساوي ما يعيده السجل للرسالة نفسها. | `BE-DS-F01` بند 1 (الاتساق بين السجل وJSON)؛ `routers/conversations.py:358`، `routers/agents.py:141-144` |
| FR‑9 | إطار `final` وإطار `error` وإطار `notice` في SSE وWebSocket لا تتغير. | `03-api-spec.md:375-384`، `03-api-spec.md:402-412`؛ ت‑4 |
| FR‑10 | جملتا التحذير تبقيان في النص كما هما اليوم. الحقلان إضافة، لا بديل. | `03-api-spec.md:383`؛ `backend-reply.md:111`؛ س‑5 |
| FR‑11 | العميل لا يستطيع ضبط `status` أو `fallback`. أي مفتاح بهذا الاسم داخل `content` في `MessageCreateIn` لا يُحفظ على رسالة المستخدم. | `MessageCreateIn.content` يُمرَّر كما هو (`src/app/api/v1/dto/conversations.py:124-135`)؛ سابقة FR‑7 في ث‑1 |
| FR‑12 | مخرجات خطوات الـ workflow (`_persist_step_output`) تُحفظ بحالة `complete`، لأن الخطوة لا تُكتب إلا إذا اكتملت. (يتوقف على س‑7.) | `orchestrator.py:1692-1697`، `orchestrator.py:1247-1257` |
| FR‑13 | تُحدَّث الوثائق الملزمة لتصف الحقلين وقيمهما ودلالة `null`: `openapi.yaml` (`MessageOut`)، و`03-api-spec.md` §2 و§3.1، و`06-domain-models.md` §4 (ثابت جديد بنمط INV‑CV4)، و`01-data-model.md` §2.4. | CLAUDE.md «Architecture rules»؛ `tests/unit/test_api_conventions.py:188`؛ `tests/unit/test_conversations_schema_parity.py:133-165` |

## 4. المتطلبات غير الوظيفية

| المعرّف | المتطلب | معيار القياس |
|---|---|---|
| NFR‑1 | لا كلفة إضافية على الدور. | عدد العبارات التي ينفذها `append_message` لكل رسالة لا يتغير (إدراج واحد + تحديث `version` كما اليوم، `sql_repository.py:354-370`). يثبته اختبار unit على العبارات المنفذة. |
| NFR‑2 | لا كلفة إضافية على قراءة السجل. | `list_messages` تنفذ عبارة SQL واحدة لكل صفحة، بلا ربط مع جدول آخر (`sql_repository.py:443-460`). اختبار unit على العبارة. |
| NFR‑3 | التوافق مع البيانات القائمة. | 100% من صفوف `conversations.messages` الحالية تُقرأ بلا خطأ بعد التغيير، وتعطي `status: null` و`fallback: null`. الترحيل إضافي، metadata فقط (بلا إعادة كتابة الجدول)، له `downgrade` يحذف ما أضافه، ويُطبَّق عبر `python -m app.ops.provision` وحده. |
| NFR‑4 | مجموعة قيم مغلقة. | `status` في القاعدة وفي الـ domain وفي OpenAPI يقبل `complete` و`partial` و`null` فقط. قيمة أخرى من المنسّق تُرفض في الوحدة (حالة الاستخدام أو VO)، ولا تصل القاعدة. |
| NFR‑5 | فشل الحفظ لا يُسقط الجواب. | إذا فشلت كتابة الرسالة، يبقى السلوك كما اليوم: `final` يصل بلا `message_id` (`orchestrator.py:2349-2355`)، والجزئي يسجّل تحذيراً (`orchestrator.py:2195-2201`). لا يتحول الدور إلى `error` بسبب الحقلين. |
| NFR‑6 | عزل المستأجر. | الحقلان في صف `conversations.messages` نفسه، فتغطيهما RLS (`SET LOCAL app.workspace_id`) وصلاحيات الجدول. يُمدَّد `tests/integration/test_conversations_repository_rls.py` ليثبت أن مساحة أخرى تقرأ صفر صفوف. يُشغَّل في CI job `integration` (ق‑4). |
| NFR‑7 | حدود الطبقات. | `lint-imports` يمر بالعقود الثمانية دون أي `ignore_imports` جديد. `conversations.domain` يستورد stdlib فقط. الوحدة لا تعرف `_Fallback` ولا أي نوع من `app.agents`؛ ما يعبر المنفذ قيم بسيطة. |
| NFR‑8 | الخصوصية. | لا يظهر نص الرسالة في أي سجل جديد. أي تحذير جديد يحمل أسماء ثابتة وأعداداً فقط (قاعدة ق‑5، `orchestrator.py:2203-2209`). `fallback` يحمل مزوداً ونموذجاً فقط، بلا credential (`orchestrator.py:729-735`). |
| NFR‑9 | النص العربي/RTL. | رسالة جزئية عربية تعود من السجل بنصها كاملاً، ومعه جملة `⚠️ انقطعت الإجابةُ قبل اكتمالها.` مطابقة بايتاً ببايت (UTF‑8، بلا escape)، و`status: "partial"`. يثبته اختبار. |
| NFR‑10 | بوابات الجودة. | البوابات 1–5 خضراء محلياً (`ruff format --check`، `ruff check`، `mypy src`، `lint-imports`، `pytest tests/unit tests/architecture tests/eval`)، و`deploy/prometheus/test-rules.sh`. والاختبارات الحية في CI job `integration` فقط (ق‑4). |

## 5. الوضع الحالي في الشيفرة

### 5.1 كل مسار يكتب (أو لا يكتب) رسالة مساعد

| # | المسار | أين في الشيفرة | ما يُحفظ اليوم | العلامة في النص | الحالة المقترحة | `fallback` |
|---|---|---|---|---|---|---|
| 1 | دور انتهى بـ `final` | `_metered` `orchestrator.py:2044-2057` ← `_persist_reply` `orchestrator.py:2295-2378` | نص `final` (`_turn_content`، `orchestrator.py:499-523`) والمرفقات والاستشهادات | لا شيء | `complete` | `null` |
| 2 | `final` بعد أن أجاب النموذج المحلي كل الدور (6.4) | كما 1، مع `orchestrator.py:2046-2050` و`orchestrator.py:2344` | كما 1 | جملة التحول **في أول** النص (`orchestrator.py:908-914`) | `complete` | `{from, to}` |
| 3 | دور سؤال توضيح («أي ملف تقصد؟») | `_settle_clarification` `orchestrator.py:2211-2249` ثم `_persist_reply` | نص السؤال؛ المفتاح `pending_clarification` يُنزع ويُكتب على المحادثة | لا شيء | `complete` | حسب 6.4 |
| 4 | جواب ثابت أو اعتذار من `rag_agent` (`citations: []`)، ومنه «بوابة الثقة» التي يسميها الوكيل `fallback` في سجلاته | `rag_agent/agent.py:739-769`، `agent.py:816-840` | النص الثابت | لا شيء | `complete` | `null` (ليس تحول 6.4) |
| 5 | وكيل وسائط (`image_agent`/`video_agent`) | `_shared.py:50-53` | `final` كله JSON نصاً (`{job_id,status,kind}`)، `orchestrator.py:523` | لا شيء | `complete` (الدور انتهى؛ المهمة ما زالت `queued`) | `null` |
| 6 | المهلة الكلية بعد tokens | `orchestrator.py:1992-2010` ← `_settle_cut(reason=timeout)` ← `_persist_partial` `orchestrator.py:2129-2209` | ما بُثّ، بلا مرفقات | جملة الانقطاع **في آخر** النص (`orchestrator.py:487-496`)؛ وجملة التحول في أوله إن تحوّل | `partial` | حسب 6.4 |
| 7 | حدث `error` من الوكيل بعد tokens (فشل المزود أثناء الجواب، ومنه موت النموذج المحلي أثناء الجواب `orchestrator.py:882-883`) | `orchestrator.py:2029-2040` | كما 6 | كما 6 | `partial` | حسب 6.4 |
| 8 | استثناء في آلية المنسّق نفسها | `orchestrator.py:2011-2021` | كما 6 | كما 6 | `partial` | حسب 6.4 |
| 9 | تخلّي القارئ: قطع SSE (`api/v1/sse.py:127-135`)، أو `cancel` أو إغلاق WebSocket (`api/v1/websocket/streaming.py:222-235`، `streaming.py:305-309`) | `finally` في `orchestrator.py:2059-2069` (`reason=abandoned`) | كما 6، **بأفضل جهد**: قد لا يكتمل الحفظ داخل `GeneratorExit` | كما 6 | `partial` | حسب 6.4 |
| 10 | أي قطع (6–9) قبل أول token، أو tokens فراغ فقط | `orchestrator.py:2176-2178` | **لا شيء** | — | لا رسالة | — |
| 11 | فشل المزود السحابي ثم فشل النموذج المحلي قبل أن يقول شيئاً | `orchestrator.py:757-768`، `orchestrator.py:819-824`، `orchestrator.py:884-885` | لا شيء (حدث `error`، ويطبق 10) | — | لا رسالة | — |
| 12 | فشل ما قبل البث (403/404/409/422/429، `credentials.none_available`) | `_begin_turn` `orchestrator.py:1416-1506` | لا رسالة مساعد. رسالة المستخدم تُكتب فقط إذا وصل الدور إلى `_open_turn` (`orchestrator.py:1562-1565`) | — | لا رسالة | — |
| 13 | وصل `final` لكن فشلت كتابته | `orchestrator.py:2349-2355`، والحارس `terminal` (`orchestrator.py:1979-1985`) يمنع حفظه جزئياً | لا شيء | — | لا رسالة | — |
| 14 | منفذ المحادثات غير موصول | `orchestrator.py:2324-2326`، `orchestrator.py:2179-2181` | لا شيء | — | لا رسالة | — |
| 15 | مخرج خطوة workflow مكتملة | `WorkflowRun.events` `orchestrator.py:1247-1257` ← `_persist_step_output` `orchestrator.py:1687-1724` | نص `final` الخطوة | لا شيء. الخطوة الفاشلة أو المقطوعة بالمهلة لا تُكتب (`orchestrator.py:1237-1259`) | `complete` (س‑7) | `null` (لا تحول 6.4 في الخطوات: `begin_step` يبني الحزمة بلا `fallback`، `orchestrator.py:1763`) |
| 16 | رسالة المستخدم (دور وكيل أو بداية workflow) | `orchestrator.py:1562-1565`، `orchestrator.py:1650-1653` | النص والمرفقات | — | `null` | `null` |

**الخلاصة:**
- **لا تُحفظ رسالة «فاشلة» أبداً اليوم.** إما رسالة مكتملة، وإما جزئية فيها نص، وإما لا شيء. لذلك القيمتان `complete` و`partial` تغطيان كل ما يُكتب. وقيمة `error` لا يكتبها أي مسار (س‑2).
- **`fallback` متعامد مع الحالة.** رسالة واحدة قد تكون `partial` ومن النموذج المحلي معاً (المسار 7 بعد تحول: `orchestrator.py:2188`). لذلك هو حقل مستقل، لا قيمة ثالثة للحالة (س‑3).
- **المسار غير المتدفق** (`invoke_once`) يرفع `AppError` عند أي حدث `error` (`orchestrator.py:1396-1398`)، فيعيد problem+json لا `MessageOut`، مع أن الجزء الذي بُثّ داخلياً قد حُفظ `partial` قبل ذلك (`orchestrator.py:2040`). إذن `MessageOut` في الرد غير المتدفق يحمل دائماً `status: "complete"`، والرسالة الجزئية لا تُرى إلا من السجل.

### 5.2 أين تُبنى الرسالة وتُقرأ

- **المنفذ:** `ConversationThreads.append` يقبل `role, text, attachments, token_count, citations` فقط (`src/app/modules/conversations/ports/inbound.py:123-133`). ونتيجته `AppendedMessage` لا تحمل حالة (`inbound.py:64-84`).
- **حالة الاستخدام:** `AppendMessage.execute` (`src/app/modules/conversations/application/use_cases.py:166-207`). والتنفيذ الوحيد للمنفذ `ConversationService` (`use_cases.py:718-750`).
- **الـ domain:** `Message` بلا حالة (`src/app/modules/conversations/domain/entities.py:221-236`). و`Conversation.append_message` يطبق INV‑CV4 على `citations` (`entities.py:178-214`)، وهو النمط الذي يُعاد استعماله لحقل الحالة.
- **`MessageContent`** يرفض رسالة بلا نص ولا مرفق (`domain/value_objects.py:84-90`). لذلك لا يمكن حفظ «رسالة فاشلة» فارغة دون تغيير هذا الثابت (ت‑5).
- **التخزين:** جدول `messages` في المحوّل (`adapters/sql_repository.py:142-160`): لا عمود للحالة. آخر ترحيل في السلسلة `0007_message_citations` (`migrations/versions/conversations/`). وعمود `citations` يستعمل `JSONB(none_as_null=True)` لأن `JSONB()` العادي يكتب `None` على شكل JSON `'null'` (`sql_repository.py:154-158`)؛ أي عمود JSONB جديد (مثل `fallback`) يحتاج الشيء نفسه.
- **الإدراج:** `append_message` (`sql_repository.py:354-370`). **القراءة:** `list_messages` (`sql_repository.py:443-460`) و`_hydrate_message` (`sql_repository.py:724-738`).
- **API:** `MessageOut` (`src/app/api/v1/dto/conversations.py:111-121`) بلا حالة. يبنيه `_to_message_out` للسجل و`appended_to_message_out` للرد غير المتدفق (`routers/conversations.py:101-138`).
- **العقد المنشور:** `MessageOut` في `docs/design/openapi.yaml:1345-1359` بلا حالة. و`03-api-spec.md:113-115` كذلك.
- **مخطط الأحداث:** لا يوجد مخطط لأحداث الرسائل في `docs/design/events/schemas/`. و`MessageAppended` حدث في الذاكرة فقط (`06-domain-models.md:93`). لا أثر على الأحداث.
- **اختبارات قائمة تثبت النص:** `test_the_partial_reply_is_marked_as_incomplete` و`test_a_completed_stream_persists_no_trailing_marker` و`test_a_stream_cut_before_it_said_anything_stores_nothing` و`test_a_partial_reply_is_persisted_once_not_twice` (`tests/unit/test_orchestrator.py:2419-2515`)، و`test_a_fallback_turn_keeps_its_citations_and_still_gets_the_notice_key` (`test_orchestrator.py:3002`). يجب أن تبقى خضراء دون تعديل في توقعات النص (FR‑10)، وتُمدَّد بتوقعات الحالة.

### 5.3 ما وُعد به Android

- `backend-reply.md:111`: «لا يوجد حقل حالة… إذا لم يصل أي token لا يُحفظ شيء… وعند التحول يُضاف في أول النص…».
- `backend-reply.md:75`: «`content`… يكتب دائماً `text` و`attachments` ولا شيء غيرهما». الحقلان الجديدان خارج `content`، فيبقى هذا صحيحاً (قرار ق‑2 في ث‑1).
- `backend-reply.md:93-94`: شكل `final` و`error`. لا يتغير (FR‑9).
- `backend-reply.md:98`: الحفظ عند قطع الاتصال «أفضل جهد، غير مضمون». يبقى كذلك.

## 6. التعارض مع الوثائق الملزمة والقرارات السابقة (للتصعيد)

| # | التعارض | المرجع | الأثر |
|---|---|---|---|
| ت‑1 | الوثائق الملزمة تقول إن علامة الانقطاع **تبقى جملة في النص**، وإن «تحويلها إلى حقل قرار لاحق». و`MessageOut` و`Message` في الوثائق بلا حالة. | `06-domain-models.md:98`؛ `01-data-model.md:205-209`؛ `03-api-spec.md:113-115`؛ `openapi.yaml:1345-1359` | ث‑2 هو هذا «القرار اللاحق». **يحتاج موافقة بشرية** على تعديل `06 §4` و`01 §2.4` و`03 §2/§3.1` و`openapi.yaml` (س‑1). |
| ت‑2 | قرار ب‑10 رقم 1: «جملة في الذيل لا حقل جديد على الرسالة… إضافة حقل لهذا وحده تغيير في نموذج البيانات لا يستحقه». | `docs/rag-agent-scenarios-implementation-plan.md` (حُذف في `5ecdc61`، يُقرأ من `5ecdc61^`)؛ تعليق الشيفرة `orchestrator.py:426-437` يقول صراحة إن تحويل العلامة إلى حقل هو ث‑2 | ليس وثيقة ملزمة، وفرضيته («لا نصف مُهيكَل للرسالة») سقطت بـ ث‑1. لكن ث‑2 **ينقض قراره نفسه**. يجب أن يذكر التصميم ذلك، ويُحدَّث تعليق `orchestrator.py:426-437`. والجملة تبقى (FR‑10)، فلا يخسر أي قارئ قائم. |
| ت‑3 | `03 §3.1` الملزم يقول: «ويبدأ الردُّ المحفوظ (`content.text`) بالجملة نفسِها» لجواب النموذج المحلي. وأُبلغ Android بالشيء نفسه للجملتين. | `03-api-spec.md:383`؛ `backend-reply.md:111` | **لا تعارض** إذا بقيت الجملتان (FR‑10، س‑5). أي قرار بحذفهما يتعارض مع الوثيقة الملزمة ومع ما وُعد به Android، ويُكسر العملاء الذين لا يعرفون الحقل. |
| ت‑4 | إضافة مفتاح `status` إلى إطار `final` تصطدم بمفتاح الوكيل نفسه: `final` وكلاء الوسائط يحمل `status` (حالة المهمة، `queued`)، والقاعدة الملزمة تقول إن مفاتيح الوكيل الطرفية تبقى على `final` ولا يُسقطها المنسّق. وفي إطار `error`، `status` هو رمز HTTP (RFC 9457). | `_shared.py:50-53`؛ `03-api-spec.md:384`؛ `api/errors.py:114-131` | لذلك FR‑9 لا يغيّر الإطارات. `final` نفسه يعني `complete` بنوعه، و`error` يعني أن الرسالة (إن حُفظت) `partial`. وإن أراد المالك الحالة على الإطار، فباسم آخر (مثل `message_status`) (س‑6). |
| ت‑5 | طلب Android: «ربط الحالة بمحاولة/دور **حتى إن لم يصل أي token ولم تُنشأ رسالة مساعد**». هذا يتعارض مع قرار ب‑10 «لا شيء بُثّ، لا شيء يُحفظ» ومع ثابت `MessageContent` (نص أو مرفق إلزامي). | `backend-follow-up-request.md:22`؛ `orchestrator.py:2157-2161`، `orchestrator.py:2176-2178`؛ `domain/value_objects.py:89-90` | حقل على الرسالة **لا يستطيع** تلبية هذا الجزء. يحتاج سجل محاولة/دور، وهو ث‑3. القراءة الأضيق (الافتراض A‑1): ث‑2 يغطي الرسائل المحفوظة فقط. ويجب أن يقول الرد لـ Android ذلك صراحة (س‑2، س‑11). |
| ت‑6 | مبدأ الكتالوج في `03 §4`: «صف في الكتالوج وعد بالإصدار»، وحُذفت رموز لا موضع يصدرها. | `03-api-spec.md:441`، `03-api-spec.md:514` | المبدأ عن رموز الأخطاء، لكن روحه تنطبق على قيمة `error` في enum لا يكتبها أي مسار. لذلك التوصية في س‑2 قيمتان فقط. |
| ت‑7 | تسمية القيم: سابقة المنصة في `WorkflowRunOut.status` هي `running|completed|failed`. وطلب Android والفجوة يقولان `complete|partial|error`. | `orchestrator.py:186-188`؛ `gaps.md:25`؛ `backend-change-request.md:68` | ليس تعارضاً ملزماً. التوصية: كلمات Android (`complete`/`partial`)، لأن العميل هو من طلبها، ولأن `completed` في الـ workflow صفة لتشغيل لا لرسالة (س‑12). |

## 7. الافتراضات والأسئلة المفتوحة

**الافتراضات (أضيق قراءة معقولة):**

| # | الافتراض | يحتاج قرار من |
|---|---|---|
| A‑1 | ث‑2 = حالة **الرسالة المحفوظة** فقط. حالة محاولة لم تُنتج رسالة (فشل قبل أول token، أو فشل ما قبل البث) هي ث‑3. | المالك (س‑2) |
| A‑2 | الحقلان على `MessageOut` بجانب `content` و`citations`، و`content` يبقى `{text, attachments}` (على قاعدة ق‑2). | المصمم |
| A‑3 | المنسّق يمرر الحالة والمؤشر صراحة إلى المنفذ. الوحدة تتحقق من القيمة ومن أن الدور `assistant` (ثابت جديد بنمط INV‑CV4)، ولا تستنتج شيئاً من النص. | المصمم |
| A‑4 | `complete` لوكيل الوسائط = «انتهى الدور وأُدرجت المهمة»، لا «نجحت المهمة». حالة المهمة تبقى في `GET /media/jobs/{id}`. | — |
| A‑5 | اعتذار `rag_agent` (بوابة الثقة، التي يسميها الوكيل `fallback` في سجلاته `agent.py:723-728`) رسالة `complete` و`fallback: null`. كلمة `fallback` في الحقل الجديد تعني تحول 6.4 فقط. | — |
| A‑6 | الحفظ في حالة التخلّي يبقى أفضل جهد. إن لم يكتمل، فلا رسالة ولا حالة. | — |
| A‑7 | `fallback` المحفوظ لقطة وقت الجواب، كـ `citations` (ق‑3). لا يتغير إن تغيّر إعداد `LLM_FALLBACK_ROUTE` لاحقاً. | — |
| A‑8 | أي قيمة غير معروفة للحالة في المستقبل يجب أن يعاملها العميل كـ «غير مكتملة». يُكتب ذلك في وصف OpenAPI. | المصمم |

**الأسئلة المفتوحة لبوابة ①:**

| # | السؤال | التوصية | يحتاج قرار من |
|---|---|---|---|
| س‑1 | هل توافق على تعديل الوثائق الملزمة `06 §4` و`01 §2.4` و`03 §2/§3.1` و`openapi.yaml`، ونقض قرار ب‑10 رقم 1 (ت‑1، ت‑2)؟ | نعم. الوثائق نفسها تركت القرار «لاحقاً»، والجملة تبقى فلا يخسر قارئ قائم. | المالك |
| س‑2 | ما القيم؟ `complete|partial` فقط، أم `complete|partial|error`؟ | **قيمتان فقط.** لا مسار يكتب رسالة فاشلة اليوم (§5.1، المسارات 10–14). كتابة رسالة `error` فارغة تكسر ثابت `MessageContent` وقرار ب‑10. والفشل بلا رسالة يُغطى في ث‑3 بسجل المحاولة (ت‑5، ت‑6). | المالك |
| س‑3 | التحول للنموذج المحلي: حقل منفصل أم قيمة للحالة؟ وما شكله؟ | **حقل منفصل** `fallback`، لأنه متعامد مع الحالة (`partial` + محلي ممكن). وشكله **نفس `final.fallback`** `{from, to}` أو `null`، على مبدأ ث‑1 «يساوي ما في `final`». البديل الأبسط: `fallback: bool`. | المالك |
| س‑4 | الصفوف القديمة: `null`، أم backfill من نص العلامة؟ | **`null` بلا backfill.** طلب Android صراحة ألا تُسمّى القديمة مكتملة (`backend-follow-up-request.md:23`). والـ backfill من النص هو بالضبط مطابقة النص التي نزيلها، وقد يخطئ مع جواب يقتبس الجملة. | المالك |
| س‑5 | هل تبقى جملتا التحذير في النص؟ | **نعم، تبقيان.** `03 §3.1` ملزم بها، ووُعد بها Android، وهي الإشارة الوحيدة في الصفوف القديمة وللعملاء الذين لا يعرفون الحقل. حذفها قرار منفصل لاحق. | المالك |
| س‑6 | هل تحمل إطارات `final`/`error` الحالة، وهل يحمل `error` معرّف الرسالة الجزئية؟ | **لا تغيير على الإطارات.** نوع الإطار هو الحالة. و`status` يصطدم بمفتاح وكلاء الوسائط وبرمز HTTP (ت‑4). أما `message_id` للجزئي في `error` فمفيد للمصالحة، لكنه من ث‑3؛ يُنقل إليه. | المالك |
| س‑7 | مخرجات خطوات الـ workflow: `complete` أم `null` (كما في ث‑1 للاستشهادات)؟ | **`complete`.** الخطوة لا تُكتب إلا مكتملة (`orchestrator.py:1692-1697`)، والكلفة معامل واحد في الاستدعاء نفسه. و`null` لرسالة جديدة يعني «غير معروف» بلا سبب. الكتالوج فارغ اليوم، فالأثر نظري. | المالك |
| س‑8 | رسائل المستخدم: `null` أم `complete`؟ | **`null`.** الحالة صفة لجواب المساعد. وهذا ما فعله ث‑1 مع `citations`. | المالك |
| س‑9 | هل يُحفظ سبب الانقطاع (`timeout`/`error`/`abandoned`)؟ | **لا.** يبقى في السجل `orchestrator.partial_reply_persisted`. لا حاجة لدى Android إليه، وهو يكشف تفاصيل تشغيلية. | المالك |
| س‑10 | تشغيل الاختبارات الحية (RLS، والتجربة الكاملة) في CI job `integration` فقط، كما ق‑4؟ | **نعم.** محلياً `pytest tests/unit tests/architecture tests/eval` فقط. | المالك |
| س‑11 | هل يُحدَّث `backend-reply.md` في مستودع Android، أو يُرسل ملحق؟ | **كقرار ث‑1: لا يُحدَّث.** يُسجَّل الإصلاح في `gaps.md`، والعقد المرجعي هو `openapi.yaml`. ويُذكر في `gaps.md` أن الفشل بلا رسالة ما زال مفتوحاً في ث‑3. | المالك |
| س‑12 | أسماء القيم: `complete|partial` (كلمات Android) أم `completed|partial` (قريب من سابقة الـ workflow)؟ | **`complete|partial`** (ت‑7). | المالك |

## 8. التقدير

| البند | الحجم (S/M/L/XL) | ملاحظات |
|---|---|---|
| US‑1 حالة الرسالة عند الكتابة | M | المنفذ، و`AppendedMessage`، وحالة الاستخدام، والـ domain (ثابت جديد)، والمستودع، والمسارات الخمسة في المنسّق، ومخرج الخطوة. ومعه تحديث المنفذ المزيّف في `tests/unit/support_conversations.py`. |
| US‑2 مؤشر النموذج المحلي | S | قيمة من `record.fallback.describe()` تمر في الاستدعاء نفسه. عمود JSONB ثانٍ أو بنية واحدة، حسب التصميم. |
| US‑3 الإعادة من السجل | S | `_hydrate_message` و`_to_message_out` و`MessageOut`، والتوافق مع الصفوف القديمة. |
| US‑4 الرد غير المتدفق والإطارات | S | `appended_to_message_out` يخدم المسارين. الإطارات لا تتغير؛ اختبارات تثبت ذلك. |
| US‑5 العقد والوثائق والترحيل | S | `openapi.yaml`، و`03`، و`06`، و`01`، وترحيل `0008`، واختبار parity، و`gaps.md`. |
| US‑6 العزل والصلاحيات | S | تمديد اختبار RLS (حي، في CI). |
| **الإجمالي** | **M** | نحو 1–2 جلسة تنفيذ بعد حسم س‑1 وس‑2 وس‑3. |

**المخاطر الرئيسية:**
1. انتظار موافقة تعديل الوثائق الملزمة ونقض ب‑10 (ت‑1، ت‑2).
2. توقع Android لحالة «فشل بلا رسالة» (ت‑5). إن لم يُقل صراحة إنها في ث‑3، فقد يُعدّ ث‑2 ناقصاً عند القبول.
3. المسار 9 (التخلّي) أفضل جهد: اختبار unit يثبت القيمة إن اكتمل الحفظ، ولا يثبت أن الحفظ يكتمل دائماً.
4. اختبارات قائمة تبني `AppendedMessage` أو `MessageOut` أو منفذاً مزيّفاً بمعاملات محددة. إن لم تكن للحقول الجديدة قيم افتراضية، فستنكسر.
5. CHECK على عمود في جدول قائم قد يفحص الجدول كله عند الإضافة. يحتاج قراراً من مهندس البيانات (مثلاً `NOT VALID` ثم `VALIDATE`) لئلا يطول القفل.
6. اختبارات RLS الحية لا تُشغَّل إلا في CI.

</div>
