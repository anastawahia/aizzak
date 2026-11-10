<div dir="rtl">

# قبول مالك المنتج: ث‑1 حفظ استشهادات RAG مع رسالة المساعد (ومعها ث‑4 وث‑6 وث‑8 وث‑9)

- **الفرع:** `fix/data-search-simple-gaps` على `36640e2` (2026‑11‑10). لم يُدفع بعد إلى GitHub، فلم تعمل CI عليه.
- **المراجَع:** `requirements.md`، `stories.md` (33 معياراً)، `design.md`، `test-plan.md` (PASS)، `review.md` (PASS)، `security-review.md` (PASS)، `status.md` (ق‑1…ق‑4)، `gaps.md`، ومعيار القبول 6 في `aizzak-android/docs/delivery/tasks/data-search-agent/backend-change-request.md:123`.
- **ما تحققت منه بنفسي (قراءة فقط، بلا حزمة حية):**
  - كل اختبار في مصفوفة `test-plan.md` §1 موجود في المستودع (44 اسماً، بحث بـ `grep "def <name>"`)، وقرأت جسم كل اختبار يثبت معياراً من نوع Must.
  - شغّلت الاختبارات المسمّاة وحدها، مع ملف `test_conversations_schema_parity.py` كاملاً: 56 حالة (مع الحالات المُعلمة) نجحت، exit 0. وشغّلت `test_no_token_is_401_on_the_messages_route` (أُضيف في `36640e2`): نجح.
  - على `36640e2`: `ruff format --check` (996 ملفاً منسقاً)، و`ruff check` سليم، و`lint-imports` (9 عقود سليمة، لا مكسور). لم أُعد `mypy` ولا الحزمة كاملة: `36640e2` يغيّر وثائق وتعليقات واختباراً واحداً، والمنسّق سجّل البوابات خضراء بعده (`status.md`، دورة الإعادة 1).
  - لم أشغّل `tests/integration` (ق‑4). قرأت اختبارات RLS الجديدة فقط، وهي تحت `live_db`، ومهمة `integration` في CI تشغّلها (`ci.yml:188` لا يستثني `live_db`).

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑1.1 | `test_orchestrator.py::test_citations_reach_the_append_that_writes_the_reply_in_order`: استشهادان (`rank` 1 و2) يصلان إلى `append` رسالة المساعد بالقيم والترتيب، وإطار `final` بلا تغيير. و`test_conversations_use_cases.py::test_the_service_stores_and_returns_citations_in_order`. الذهاب والإياب الحي `test_citations_round_trip_in_order_with_nulls_and_arabic_name` في CI | ✅ مقبول |
| AC‑1.2 | `test_conversations_sql_statements.py::test_each_item_is_exactly_the_five_keys_in_order_keeping_null_values` (المفتاحان حاضران بقيمة `None`)، و`test_conversations_domain.py::test_citation_accepts_null_file_name_and_page`، والعنصر الثاني في اختبار AC‑1.1 | ✅ مقبول |
| AC‑1.3 | `test_orchestrator.py::test_an_empty_citation_list_is_passed_as_empty_not_none` (يصل `()`)، و`test_python_none_is_bound_as_sql_null_and_an_empty_list_as_json` (`[]` يُربط `"[]"`، و`None` يُربط SQL NULL). والتمييز الحي `test_an_empty_citation_list_and_a_missing_one_stay_distinct` في CI | ✅ مقبول |
| AC‑1.4 | `test_orchestrator.py::test_an_agent_that_emits_no_citations_key_records_none` (`[None, None]`)، و`test_a_null_citations_value_records_none` | ✅ مقبول |
| AC‑1.5 | `test_api_conversations_router.py::test_a_client_sent_content_citations_key_is_ignored`: العميل يرسل `content.citations`، فتعود رسالة المستخدم `citations: null` و`content == {text, attachments:[]}`. ومعه `test_citations_on_a_user_message_are_a_loud_validation_error` (INV‑CV4) | ✅ مقبول |
| AC‑1.6 | `test_orchestrator.py::test_a_malformed_list_stores_none_keeps_the_turn_and_warns_once`، بست حالات مُعلمة، منها الثلاث المطلوبة (`"x"`، وعنصر بلا `chunk_id`، و`rank="1"`). يثبت: `final` وصل ومعه `message_id`، والنص محفوظ، والاستشهادات `None`، وسجل تحذير واحد بالعدد، ولا `file_name` ولا نص في السجل. وعلى السلك `test_a_malformed_citation_list_never_fails_the_turn` (200، `citations: null`) | ✅ مقبول |
| AC‑1.7 | `test_orchestrator.py::test_unknown_item_keys_are_dropped_and_the_frame_keeps_them`: `score` و`snippet` لا يُحفظان، والإطار يبقيهما | ✅ مقبول |
| AC‑1.8 | `test_llm_fallback.py::test_a_turn_answered_by_the_local_fallback_keeps_its_citations` عبر مسار التحول الحقيقي: `final` يحمل `fallback`، والنص المحفوظ يبدأ بجملة التحول، والاستشهادات محفوظة. ومعه الاختبار الاصطناعي `test_a_fallback_turn_keeps_its_citations_and_still_gets_the_notice_key` | ✅ مقبول |
| AC‑1.9 | `test_conversations_sql_statements.py::test_append_writes_citations_in_the_same_insert_and_never_updates_messages`: عبارتان فقط، `INSERT INTO conversations.messages` تحمل `citations`، والثانية `UPDATE conversations.conversations`، ولا `UPDATE conversations.messages` | ✅ مقبول |
| AC‑1.10 | `test_conversations_use_cases.py::test_more_than_the_maximum_is_dropped_and_exactly_the_maximum_is_kept` (51 تصير `None`، و50 تُحفظ)، و`test_inv_cv4_caps_citations_at_the_maximum`، و`test_citations_the_module_dropped_warn_once_with_the_count_only`. السقف 50 اعتمدته بوابة ① (س‑7) | ✅ مقبول |
| حواف US‑1 | `test_a_cut_off_reply_records_no_citations`، و`test_a_failed_turn_after_tokens_records_no_citations`، و`test_citations_are_saved_before_the_final_frame_is_delivered` (قطع بعد `final` ثم `aclose()`، والاستشهادات محفوظة)، و`test_a_failed_reply_write_delivers_final_without_message_id_and_does_not_error` | ✅ مقبول |
| AC‑2.1 | `test_api_conversations_router.py::test_a_streamed_answer_returns_the_same_citations_in_history`: `assistant.id == final.message_id`، و`assistant.citations == final.citations` بمقارنة عميقة. **هذا معيار Android 6** على مستوى السلك (TestClient). أما التجربة الحية من طرف إلى طرف فخارج النطاق (`requirements.md` §2) | ✅ مقبول |
| AC‑2.2 | `test_conversations_sql_statements.py::test_a_row_with_null_citations_hydrates_to_none`. والحي `test_a_row_inserted_without_the_column_reads_back_as_none` في CI | ✅ مقبول |
| AC‑2.3 | `test_api_conversations_router.py::test_the_user_message_has_the_citations_key_with_null` (المفتاح حاضر والقيمة `None`) | ✅ مقبول |
| AC‑2.4 | `test_api_conversations_router.py::test_the_arabic_file_name_is_sent_as_utf8_not_escaped`: بايتات UTF‑8 للاسم `دليل الصيانة ٢٠٢٦.pdf` في جسم الرد، ولا `\u` | ✅ مقبول |
| AC‑2.5 | `test_api_conversations_router.py::test_each_message_keeps_its_own_citations_across_pages`: ثلاثة أدوار بثلاث قيم مختلفة (عنصر واحد، و`[]`، وبلا مفتاح)، تُقرأ بـ `limit=1` حتى `next_cursor: null`، والنتيجة `[None, first, None, [], None, None]` | ✅ مقبول |
| AC‑2.6 | `test_api_conversations_router.py::test_content_stays_text_and_attachments_only` (الرد وكل رسالة في السجل)، و`test_api_agents_router.py::test_invoke_without_stream_returns_the_stored_citations` | ✅ مقبول |
| AC‑2.7 | `test_conversations_sql_statements.py::test_list_messages_is_one_select_on_messages_alone_that_includes_citations`: عبارة واحدة، و`FROM conversations.messages` وحده، بلا `JOIN` ولا `files.` ولا `knowledge.` | ✅ مقبول |
| AC‑2.8 | `test_api_conversations_router.py::test_a_deleted_assistant_message_leaves_history_and_takes_its_citations_with_it`: 204، والرسالة غائبة، والاسم العربي غائب من الجسم الخام | ✅ مقبول |
| AC‑3.1 | `test_api_conversations_router.py::test_a_non_streamed_reply_has_the_same_citations_as_history` (`_ask` يرسل `stream: false` بلا `Accept: text/event-stream`) | ✅ مقبول |
| AC‑3.2 | `test_api_agents_router.py::test_invoke_without_stream_returns_the_stored_citations`: `message.citations` يساوي ما يعيده السجل لـ `message.id` | ✅ مقبول |
| AC‑3.3 | اختبار AC‑2.1 نفسه (`final.citations == history == _TWO_CITATIONS`)، و`final.data["citations"] == declared` في اختبار AC‑1.1 (الإطار بلا تغيير) | ✅ مقبول |
| AC‑3.4 | `test_api_conversations_router.py::test_an_agent_without_citations_replies_with_null`، و`test_api_agents_router.py::test_invoke_of_an_agent_without_citations_returns_null` | ✅ مقبول |
| AC‑3.5 | `test_api_conversations_router.py::test_an_agent_that_states_no_sources_replies_with_an_empty_list` | ✅ مقبول |
| AC‑4.1 | `docs/design/openapi.yaml:1351-1369`: `MessageOut.citations` مصفوفة nullable من `Citation` (`maxItems: 50`)، و`Citation` بالمفاتيح الخمسة `required`، و`document_id`/`chunk_id` بصيغة uuid، و`file_name`/`page` nullable، و`rank` بحد أدنى 1. واختبار العقد `test_api_conventions.py::test_the_contract_publishes_message_citations_as_the_app_serves_them` ناجح | ✅ مقبول |
| AC‑4.2 | مراجعة وثيقة: `03-api-spec.md:105-114` (§2): `MessageOut.citations` ودلالة `null` مقابل `[]` والمواضع الثلاثة. و`03-api-spec.md:391` (§3.1): القائمة نفسها تُحفظ وتُعاد في `MessageOut.citations` بالقيمة والترتيب. دلالة `null`/`[]` مكتوبة في §2 لا في §3.1، و§3.1 يحيل إليها، فالمعنى مكتمل | ✅ مقبول (يدوي) |
| AC‑4.3 | مراجعة وثيقة: `06-domain-models.md:86-102` (VO `Citation`، والحقل، وINV‑CV4، وتصحيح فرضية ب‑10 صراحة)، و`01-data-model.md:184` و`:201-207` (العمود وتعليقه وتصحيح تعليق `pending_clarification`). موافقة المالك ق‑1 مسجلة | ✅ مقبول (يدوي) |
| AC‑4.4 | `migrations/versions/conversations/0007_message_citations.py`: `ADD COLUMN citations jsonb NULL` بلا `DEFAULT`، و`downgrade` يحوي `DROP COLUMN IF EXISTS`. واختبارات `test_conversations_schema_parity.py` الستة الجديدة ناجحة (الاتجاهان، والتهجئة، و`nullable` بلا default مع `none_as_null`، والإسقاط في downgrade). **تطبيق الترحيل عبر `provision` لم يُنفَّذ بعد**، ويُنفَّذ في مهمة `integration` (ق‑4) | ✅ مقبول ساكناً، 🟡 التطبيق معلّق على CI |
| AC‑4.5 | `gaps.md:21`: فقرة «الإصلاح (2026‑11‑10)» تحت ث‑1 | ✅ مقبول (يدوي) |
| AC‑5.1 | `test_api_conversations_router.py::test_an_unknown_thread_is_a_404_problem_without_citations` (404، و`application/problem+json`، ولا مفتاح `citations`). العزل الفعلي بين مساحتين يثبته AC‑5.2 | ✅ مقبول |
| AC‑5.2 | `tests/integration/test_conversations_repository_rls.py::test_citations_are_invisible_to_another_tenant`. قرأته: يكتب في المساحة A، ثم يتوقع من المساحة B صفحة فارغة من المستودع، وصفر صفوف من SELECT خام بدور `aizzak_owner` وGUC المساحة B. ولم يُشغَّل محلياً (ق‑4)، فلا دليل تنفيذ بعد | 🟡 معلّق على CI (ليس فشلاً) |
| AC‑5.3 | `test_api_rbac.py::test_the_message_history_needs_conversations_read_and_a_refusal_leaks_no_citations`: 403 `authz.forbidden` مع `missing permission: conversations:read`، بلا `data` ولا `citations`. والأدوار التي تملك الصلاحية تصل إلى 404 | ✅ مقبول |
| AC‑5.4 | `test_a_bad_token_is_401_on_the_messages_route`، ومعه `test_no_token_is_401_on_the_messages_route` (أُضيف في `36640e2`، وسدّ ملاحظة المراجعة 7 وملاحظة QA 3). كلاهما ناجح | ✅ مقبول |
| AC‑5.5 | `lint-imports`: 9 عقود سليمة، ولا مكسور. `git diff 0be830e..HEAD -- .importlinter` فارغ، فلا `ignore_imports` جديد. ولا استيراد لـ `app.modules.knowledge` أو `app.modules.files` في `modules/conversations`: الذكر الوحيد في docstring قديم (`ports/files.py:60`) | ✅ مقبول |

**الخلاصة:** 33 معياراً، كلها من نوع Must. 31 مقبولة بدليل مُنفَّذ أو بمراجعة وثيقة مكتوبة. واثنان معلّقان على CI: AC‑5.2 كله، والجزء الحي من AC‑4.4 (تطبيق الترحيل). لا معيار مرفوض.

## ملاحظات غير حاجبة مقبولة

أقبلها بالاسم. لا شيء منها يمس معياراً من نوع Must:

- **ملاحظة QA 1:** عنصر واحد تالف يُسقط القائمة كلها إلى `null`. هذا مقصود (AC‑1.6، والقرار د‑ت1)، ويحمي المقارنة العميقة في AC‑2.1. يُذكر لـ Android في `backend-reply.md`.
- **ملاحظة QA 2 = المراجعة 2 = SR‑6:** `_citations_from_json` يعيد التحقق عند القراءة، فصف تالف يعطي 500 للسجل كله. لا يحدث اليوم. شرط مقبول: أي تشديد لاحق لقواعد `Citation` يحتاج تنظيف البيانات المخزنة أولاً، أو تخفيف القراءة.
- **ملاحظة QA 4:** `CLAUDE.md` يقول 8 عقود، والناتج 9 (عقد WireTwin). انحراف وثائقي خارج الميزة.
- **المراجعة 6 (ث‑8):** `text/plain;` (معامل فارغ) و`boundary="a;b"` (`;` داخل قيمة مقتبسة) يُرفضان بـ 422. الرفض مقصود ومُختبر، ولا يمس Android.
- **SR‑1:** المنسّق يحفظ `citations` من أي وكيل بلا قائمة سماح. لا استغلال اليوم، فـ `rag_agent` وحده يصدرها من استرجاع داخل المساحة.
- **SR‑2:** اسم الملف المحذوف يبقى في الاستشهادات القديمة. هذا نتيجة ق‑3 (لقطة)، داخل المساحة نفسها فقط.
- **SR‑3:** رابط PUT يبقى صالحاً بعد `complete` (TOCTOU). سابق لهذا الفرق (ح‑17).
- **SR‑4:** `complete` لرفع PUT مفرد لا يفحص `uploaded_by`. داخل المساحة فقط.
- **SR‑5:** `content_type` بلا `max_length` في الـ DTO.
- **SR‑7:** على Android عرض `file_name` نصاً عادياً، لا HTML ولا Markdown.
- **SR‑8:** نطاق `Idempotency-Key` لا يشمل المستخدم. لا تسرب ما دامت المحادثات مشتركة على مستوى المساحة.
- **SR‑9:** معلومة، لا إجراء.
- **ملاحظة التصميم (`design.md:592`):** `pending_clarification` (‏`JSONB()` بلا `none_as_null`) يكتب على الأرجح JSON `'null'` لا SQL NULL. خارج النطاق، والقراءة لا تتأثر.
- **ملاحظات المراجعة 1 و3 و4 و5 و7 و8:** أُغلقت في `36640e2`. تحققت من 1 (INV‑F5 في `06:151`)، ومن 3 (`01:207` ينسب السقف إلى INV‑CV4)، ومن 7 (اختبار 401 بلا رمز).

## متابعات تحتاج قرار المالك

| # | البند | متى | المصدر |
|---|---|---|---|
| م‑1 | **شرط القبول:** ادفع الفرع وافتح PR، ثم انتظر نجاح مهمة `integration` في CI. ستشغّل `test_conversations_repository_rls.py` (AC‑5.2، ومعها الذهاب والإياب في AC‑1.1 وAC‑1.3 وAC‑2.2 وAC‑2.4) وتطبّق `0007` عبر `provision` (AC‑4.4) | قبل بوابة ② | ق‑4 |
| م‑2 | تحديث `backend-reply.md` في مستودع Android: السطر 75 يبقى صحيحاً (`content` مغلق). أما السطور 107–109 فما زالت تقول «❌ غير محفوظة» و«بلا استشهادات» في JSON، وهذا لم يعد صحيحاً. يُضاف الحقل `MessageOut.citations`، ودلالة `null`/`[]`، والسقف 50، والإسقاط الكلي للقائمة التالفة (QA 1)، وتوصية SR‑7. من يحدّثه ومتى؟ | بعد الدمج | س‑9 |
| م‑3 | SR‑2: هل يُوثَّق في سياسة الاحتفاظ أن حذف الملف لا يمحو اسمه من الاستشهادات، أم يُبنى محو `file_name` عند `FileDeleted`؟ | قرار منفصل | ق‑3، SR‑2 |
| م‑4 | SR‑4: إكمال رفع PUT مفرد، هل هو عمل عضو (يُفحص `uploaded_by` كما في multipart) أم عمل مساحة (يُوثَّق)؟ | قرار منفصل | SR‑4 |
| م‑5 | SR‑1: قائمة سماح (أو قدرة في الـ manifest) للوكلاء الذين يُحفظ منهم `citations`. هذا شرط قبل إضافة أي وكيل جديد يمرر JSON من النموذج أو من MCP إلى `final` | قبل أي وكيل جديد يصدر استشهادات | SR‑1 |
| م‑6 | المراجعة 6: هل يقبل الخادم المعامل الفارغ بعد `;`، أم يبقى الرفض ويُوثَّق في `openapi.yaml`؟ | اختياري | review.md 6 |
| م‑7 | بند منفصل لـ `pending_clarification` (JSON `'null'` مقابل SQL NULL) | اختياري | `design.md:592` |
| م‑8 | تحسينات صغيرة: SR‑5 (`max_length=255`)، وSR‑3 (تقصير `put_ttl_s` أو إعادة الفحص عند الفهرسة، وتحديث تعليق `minio_storage.py`)، وتصحيح عدد العقود في `CLAUDE.md` (QA 4) | اختياري | security-review.md، test-plan.md |

## الحكم النهائي

**مقبول**، بشرط واحد: نجاح مهمة `integration` في CI على الـ PR قبل بوابة ② (م‑1).

- كل معايير Must الثلاثة والثلاثين لها دليل.
- 31 منها بدليل مُنفَّذ تحققت منه، أو بمراجعة وثيقة مكتوبة.
- الاثنان الباقيان (AC‑5.2، والجزء الحي من AC‑4.4) اختبارهما مكتوب ومقروء، ونقلهما المالك عمداً إلى CI (ق‑4).
- `review.md` و`security-review.md` كلاهما PASS، بلا ملاحظة حاجبة. وما بقي مفتوحاً مقبول بالاسم أعلاه.
- معيار Android 6 (`backend-change-request.md:123`) مُثبت على مستوى السلك بـ AC‑2.1 وAC‑3.1 وAC‑3.2: الاستشهادات من السجل تساوي `final` مقارنةً عميقة، و`page`/`file_name` الغائبان `null` صراحة، ولا `score`.

**إن فشلت مهمة `integration`:**
- فشل اختبار RLS أو قراءة العمود: يعود العمل إلى مرحلة **الخلفية (6)**.
- فشل تطبيق الترحيل `0007` أو parity على قاعدة حقيقية: يعود إلى مرحلة **البيانات (5)**.
- وفي الحالتين يُعاد القبول بعد الإصلاح.

</div>
