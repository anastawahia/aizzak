<div dir="rtl">

# المراجعة الأمنية: ث‑2 حقل حالة الرسالة (`status` و`fallback` على رسالة المساعد)

- **النطاق المراجَع:** `git diff 0976bcb..HEAD` على الفرع `feat/data-search-message-status` (حتى `2ade90b`). يشمل الترحيل `conversations/0008_message_status`، ووحدة `conversations` (domain وports وapplication وadapter)، والمنسّق، و`workers/bootstrap.py`، و`dto`/`routers` للمحادثات، و`openapi.yaml`، والاختبارات. التعديل غير المحفوظ على `status.md` خارج النطاق.
- **الحكم:** **PASS**. لا ملاحظة عالية أو حرجة، ولا فحص صلاحية أو عزل ناقص. الملاحظات الثلاث أدناه معلومات غير حاجبة.
- **طريقة المراجعة:**
  - قراءة ثابتة للفرق وللملفات المتغيرة، ومعها `requirements.md` و`stories.md` و`design.md` و`test-plan.md` و`status.md`، ومراجعة ث‑1 السابقة وقرارات المالك عليها (`data-search-android/gaps.md` §«سلوك معروف يبقى كما هو»).
  - اختبارات RLS والترحيل الحية (`tests/integration/test_conversations_repository_rls.py`، `test_message_status_migration_live.py`) رُوجعت قراءةً ولم تُشغَّل، لأن المكدس الحي مستخدم. تثبتها CI job `integration`.
  - شُغّلت اختبارات الوحدة ذات الصلة فقط (13 ملفاً): **606 passed**.
  - لم يُعدَّل أي ملف شيفرة أو إعداد.

## قائمة الفحص

- [x] **المصادقة والصلاحيات تُفحص في الخلفية لكل مسار ملموس.**
  - لا مسار جديداً ولا صلاحية جديدة. يتغير جسم الرد فقط في `GET /conversations/{id}/messages` (`conversations:read`)، و`POST …/messages` (`conversations:write`)، و`POST /agents/{key}/invoke` (`agents:invoke`). الحراسة لم تتغير.
  - 401 بلا ترويسة وبرمز تالف: `test_no_token_is_401_on_the_messages_route` و`test_a_bad_token_is_401_on_the_messages_route`.
  - 403 بلا `data`: `test_api_rbac.py` يربط المسار بـ `CONVERSATIONS_READ` (`:99`)، والاختبار القائم من ث‑1 يثبت أن الرفض لا يحمل بيانات.
  - محادثة من مساحة أخرى ترد 404 problem+json، ولا يظهر فيها `status` ولا `fallback` ولا أسماء النماذج (`test_another_workspaces_history_is_a_404_with_no_status_or_fallback`).
- [x] **عزل المستأجرين في الاستعلام وقاعدة البيانات.**
  - العمودان في الصف نفسه، فتغطيهما سياسة `tenant_isolation` مع `FORCE RLS` (`0001_conversations.py`). المنح على مستوى الجدول، فلا منح أعمدة يلزم تحديثه. `10-roles.sh` و`provision.py` بلا تغيير، ولا يلزمهما.
  - `list_messages` ما زال عبارة واحدة على `conversations.messages`، بشرط المساحة الصريح.
  - اختبار RLS الحي الجديد `test_status_and_fallback_are_invisible_to_another_tenant` يقرأ العمودين بـ SQL خام بدور `aizzak_owner` (NOSUPERUSER وتخضعه FORCE RLS) مع GUC المساحة B، ويتوقع صفر صفوف. فهو يثبت السياسة نفسها، لا الشرط الصريح وحده.
- [x] **التحقق من المدخلات وترميز المخرجات.**
  - **لا يصل مفتاح من الوكيل أو من العميل إلى التخزين.**
    - `status` ثابت في الشيفرة عند كل كاتب (`orchestrator.py:1766`، `:2246`، `:2409`، و`bootstrap.py` في عاملي الملخص).
    - `fallback` يُبنى من `record.fallback` (كائن `_Fallback` الذي يملكه المنسّق، `orchestrator.py:1503`) عبر `_stored_fallback` (`:936`)، بشرط `switched`. لا يُقرأ `final.data["fallback"]` في أي مسار حفظ.
    - رسالة المستخدم تُكتب بلا `status`/`fallback` (`orchestrator.py:1604`، `:1692`)، و`_turn_content` يأخذ `text` والمرفقات فقط. فـ `content.status` أو `content.fallback` من العميل لا يُحفظ (AC‑1.12).
    - INV‑CV5 يرفض الحقلين على غير المساعد، في الـ domain وفي `AppendMessage`.
  - **القيود:**
    - `status` مغلق في ثلاث طبقات: `MessageStatus` (StrEnum)، و`_recordable_status` (صارم، يرفع `ValidationError`)، و`ck_messages_status` في القاعدة (`NOT VALID` لكنه مفروض على كل INSERT/UPDATE جديد، ويثبته اختبار RLS الحي بـ SQLSTATE 23514).
    - `fallback` أربعة نصوص، كل منها من 1 إلى 255 حرفاً (`value_objects.py:29`). القيمة المخالفة تُسقط إلى `None` ولا تُفشل الإدراج (`use_cases.py:188`)، ويحذّر المنسّق مرة.
  - **المخرج يُعاد بناؤه ولا يُعكس.** القراءة تستخرج المفاتيح الأربعة فقط من jsonb (`sql_repository.py:685`)، و`_fallback_out` يبني `{from, to}` منها. فمفتاح زائد في الصف، لو وُجد، لا يصل إلى السلك.
  - الكتابة بمعاملات مربوطة عبر `insert(...).values(...)`. والترحيل نصوص ثابتة. لا SQL بتنسيق نصي، ولا shell، ولا قوالب. واجهة JSON فقط، فلا XSS في الخادم.
- [x] **لا أسرار في الشيفرة أو السجلات أو الرسائل.**
  - `FallbackRef` و`MessageFallback` لا يحملان إلا أربعة أسماء، فلا يمكن تمثيل credential فيهما. `ResolvedLLM.api_key` لا يُنسخ (`_Fallback.ref()`). وهناك اختبارات تثبت أن `repr(ref)` و`_fallback_to_json` لا يحتويان `sk-secret`.
  - السجل الجديد `orchestrator.fallback_not_recorded` يحمل `conversation_id` فقط (`orchestrator.py:946`). واختبارات `caplog` تثبت غياب أسماء النماذج و`sk-cloud` منه.
  - بحث بالأنماط في الأسطر المضافة (`api_key`، `secret`، `password`، `token`، `Bearer`، `sk-…`، `AKIA…`، `ghp_…`، `hvs.…`، `BEGIN … KEY`، روابط): لم يظهر إلا قيم اختبار وهمية (`sk-secret`، `sk-cloud`، `t-one`). لا روابط داخلية ولا مفاتيح.
  - لا متغير بيئة جديد، و`.env.example` بلا تغيير.
- [x] **رسائل الخطأ لا تكشف تفاصيل داخلية.**
  - `_status_out` (`routers/conversations.py:104`) و`_status_from_row`/`_fallback_from_json` يرفعون على قيمة تالفة. يلتقط ذلك المعالج العام (`api/main.py:598`)، فيرد `common.internal` بلا `exc`، والتفاصيل في السجل فقط.
  - رسالة `InvalidConversationInput` في `MessageFallback` تذكر اسم الحقل والحد، لا القيمة.
- [x] **التبعيات الجديدة:** لا تغيير في `pyproject.toml` ولا في ملفات القفل.
- [x] **رفع الملفات والروابط الخارجية:** لا جلب لروابط، ولا رفع، ولا مسارات ملفات في هذا الفرق. لا SSRF.
- [x] **حدود المعدل واستنزاف الموارد.**
  - لا مسار جديد. المسارات الملموسة تحت حدود المعدل القائمة (`api_req` في nginx و`middleware/rate_limit.py`).
  - الحجم محدود: `status` نحو 9 بايت. و`fallback` في أسوأ حالة نظرية 4 × 255 حرفاً (نحو 4 KB بـ UTF‑8)، والحالة الفعلية نحو 120 بايت. وصفحة السجل محدودة بـ `limit`.
- [x] **قفل الترحيل وسلامته.**
  - ثلاث عبارات `ALTER`، كلها تعديل catalog: `ADD COLUMN … NULL` بلا `DEFAULT`، و`ADD CONSTRAINT … NOT VALID`. تأخذ `ACCESS EXCLUSIVE` على `conversations.messages` لحظياً، تحت `lock_timeout` الذي يضبطه `migrations/env.py:91`. لا مسح ولا إعادة كتابة.
  - ترك القيد بلا `VALIDATE` آمن: العمود وُلد في المعاملة نفسها، فكل صف قائم `NULL` بالبناء. ولا يمكن أن يُكتب صف بين `ADD COLUMN` و`ADD CONSTRAINT`، لأنهما في معاملة واحدة.
  - `downgrade` يفقد البيانات عمداً، وهذا موثّق. هذه مسألة تشغيل، لا أمن.

**أدوات الفحص:**
- `gitleaks` و`trufflehog` و`detect-secrets` و`semgrep` و`bandit` غير مثبتة على المضيف، و`pip-audit` غير موجود في `.venv`. لم يُشغَّل أي منها. البديل بحث `grep` بالأنماط أعلاه، ولا حاجة إلى `pip-audit` لأن التبعيات لم تتغير.
- اختبارات الوحدة: 13 ملفاً (`test_api_conversations_router`، `test_api_agents_router`، `test_conversations_domain`، `test_conversations_use_cases`، `test_conversations_sql_statements`، `test_conversations_schema_parity`، `test_llm_fallback`، `test_message_status_qa`، `test_orchestrator`، `test_websocket_endpoint`، `test_workers_bootstrap`، `test_workflow_orchestration`، `test_api_conventions`)، والنتيجة `606 passed in 23.17s`.

## الملاحظات

| # | الشدة | الفئة (OWASP/CWE) | الملف:السطر | الوصف | الإصلاح |
|---|---|---|---|---|---|
| SR‑1 | معلومة | A01:2021 Broken Access Control · CWE‑200 (Exposure of Sensitive Information) | `src/app/agents/orchestrator.py:936`، `src/app/api/v1/routers/conversations.py:116`، `src/app/api/v1/dto/conversations.py` (`ProviderModel`) | **ما الجديد:** قبل ث‑2 كان `{from, to}` يصل في إطاري `notice`/`final` إلى من أرسل السؤال فقط. الآن يُحفظ مع الرسالة، ويراه كل عضو له `conversations:read` في المساحة، لأن المحادثات مشتركة على مستوى المساحة (SR‑8 في ث‑1). **لماذا لا تسرب:** القيم الأربع هي `provider.provider` و`model` من `ResolvedLLM`، ولا شيء غيرها: لا مفتاح، ولا `base_url`، ولا اسم مسار داخلي. ومسار الـ fallback يجب أن يكون مساراً مُعرَّفاً في `llm` (`resolver.py:304-323`)، و`GET /models` يعرض كل المسارات المُعرَّفة بـ `provider` و`model` (`resolver.py:436`) بصلاحية `agents:read`. وكل دور يملك `conversations:read` (member وviewer وowner وadmin) يملك `agents:read` أيضاً (`access/domain/value_objects.py:91-130`). فلا يعرف القارئ شيئاً لم يكن يعرفه. وهذه القيم لقطة وقت الجواب، فتبقى في السجل حتى لو حُذف المسار لاحقاً من الكتالوج. | غير حاجب. أبقِ `fallback` محصوراً في `describe()`/`ref()` كما هو. أعد المراجعة إن صار `GET /models` مصفّى لكل مستخدم، أو صار اسم `model` يحمل معرّفاً خاصاً بالمستأجر (مثل نموذج BYOK أو deployment خاص)، أو صارت المحادثات خاصة بكل مستخدم. |
| SR‑2 | معلومة | CWE‑754 (Improper Check for Unusual Conditions) | `src/app/modules/conversations/adapters/sql_repository.py:685`، `:700` | صف تالف في `fallback` (مفتاح ناقص، أو نوع خاطئ، أو اسم أطول من 255) يرفع `KeyError` أو `TypeError` أو `InvalidConversationInput`، فتصير قراءة صفحة السجل كلها 500. هذا نمط SR‑6 في ث‑1 نفسه. لا يصل إليه إلا من يكتب في القاعدة مباشرة بدور الوحدة أو المالك، لأن `status` عليه CHECK، والكتابة الوحيدة لـ `fallback` تمر بـ `MessageFallback`. والجسم لا يكشف شيئاً (`common.internal`). و`fallback` بلا CHECK في القاعدة، لكن القراءة تعيد بناء المفاتيح الأربعة فقط، فمحتوى زائد لا يصل إلى العميل. | اختياري: صف تالف يُقرأ `None` مع تحذير بلا قيم، بدل إسقاط الصفحة كلها. يُعالج مع SR‑6 في ث‑1 إن قرر المالك ذلك. |
| SR‑3 | معلومة | A04:2021 Insecure Design · CWE‑345 (Insufficient Verification of Data Authenticity) | `src/app/agents/orchestrator.py:2098` (سلوك سابق للفرق) | الحفظ صحيح: مصدر `fallback` المحفوظ هو المنسّق وحده، ويثبته AC‑2.5. لكن الإطار سلوكه سابق ولم يتغير: إن وضع وكيل مفتاح `fallback` على `final` ولم يحدث تحوّل، يبقى المفتاح على الإطار كما هو، بلا تحقق. أما عند التحول فيُستبدل بـ `describe()`. **اليوم لا استغلال:** لا وكيل في المستودع يضع هذا المفتاح خارج الاختبارات. **السيناريو المستقبلي:** وكيل يمرر JSON من النموذج أو من أداة MCP إلى `final`. عندها يستطيع حقن الأوامر (prompt injection) أن يجعل الإطار يقول «أجاب النموذج المحلي X»، والسجل المحفوظ يقول `null`. فيختلف ما يراه المستخدم في البث عما يراه بعد إعادة التحميل. | غير حاجب، وخارج نطاق ث‑2. وثّق للعملاء أن `MessageOut.fallback` هو المرجع. ويمكن لاحقاً أن يزيل المنسّق أي `fallback` يضعه وكيل على `final` حين لا يحدث تحوّل، كما يملك المنسّق `message_id` و`usage`. يُراجَع مع SR‑1 في ث‑1 (قائمة سماح لمفاتيح الوكيل) قبل إضافة أي وكيل يمرر مخرج نموذج إلى `final`. |

## الملاحظات الحاجبة

لا يوجد.

</div>
