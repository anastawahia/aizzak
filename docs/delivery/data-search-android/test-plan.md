<div dir="rtl">

# خطة الاختبار: ث‑1 حفظ استشهادات RAG مع رسالة المساعد

الفرع `fix/data-search-simple-gaps`، الأساس `0be830e`. لم يُشغَّل أي شيء على الحزمة الحية (ق‑4): اختبارات `tests/integration` رُوجعت ساكناً فقط (تُجمَع 40 اختباراً بلا أخطاء) وتعمل في CI.

## 1. مصفوفة التغطية
اختصارات: ORCH = `tests/unit/test_orchestrator.py`، ROUTER = `test_api_conversations_router.py`، SQL = `test_conversations_sql_statements.py`، UC = `test_conversations_use_cases.py`، DOM = `test_conversations_domain.py`، RLS = `tests/integration/test_conversations_repository_rls.py` (CI فقط).

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑1.1 | وحدة + تكامل | ORCH `test_citations_reach_the_append_that_writes_the_reply_in_order`؛ UC `test_the_service_stores_and_returns_citations_in_order`؛ RLS `test_citations_round_trip_in_order_with_nulls_and_arabic_name` | ✅ |
| AC‑1.2 | وحدة + تكامل | DOM `test_citation_accepts_null_file_name_and_page`؛ SQL `test_each_item_is_exactly_the_five_keys_in_order_keeping_null_values`؛ ORCH (`file_name=None,page=None` في الاختبار الأول) | ✅ |
| AC‑1.3 | وحدة + تكامل | ORCH `test_an_empty_citation_list_is_passed_as_empty_not_none`؛ SQL `test_python_none_is_bound_as_sql_null_and_an_empty_list_as_json`؛ RLS `test_an_empty_citation_list_and_a_missing_one_stay_distinct` | ✅ |
| AC‑1.4 | وحدة | ORCH `test_an_agent_that_emits_no_citations_key_records_none`، `test_a_null_citations_value_records_none` | ✅ |
| AC‑1.5 | وحدة | ROUTER `test_a_client_sent_content_citations_key_is_ignored`؛ UC `test_citations_on_a_user_message_are_a_loud_validation_error` | ✅ |
| AC‑1.6 | وحدة | ORCH `test_a_malformed_list_stores_none_keeps_the_turn_and_warns_once` (6 حالات: نص، بلا `chunk_id`، `rank` نصي، `page` bool، عنصر غير dict، `file_name` رقم؛ تحذير واحد بالعدد فقط وبلا `file_name`)؛ ROUTER `test_a_malformed_citation_list_never_fails_the_turn` | ✅ |
| AC‑1.7 | وحدة | ORCH `test_unknown_item_keys_are_dropped_and_the_frame_keeps_them`؛ SQL (خمسة مفاتيح فقط) | ✅ |
| AC‑1.8 | وحدة | ORCH `test_a_fallback_turn_keeps_its_citations_and_still_gets_the_notice_key` (إطار اصطناعي)؛ **أُضيف:** `test_llm_fallback.py::test_a_turn_answered_by_the_local_fallback_keeps_its_citations` عبر المسار الحقيقي (resolver + سقوط السحابة)، مع التحقق أن النص يبدأ بجملة التحول | ✅ (سدّ ملاحظة المطوّر 1) |
| AC‑1.9 | وحدة | SQL `test_append_writes_citations_in_the_same_insert_and_never_updates_messages` | ✅ |
| AC‑1.10 | وحدة | UC `test_more_than_the_maximum_is_dropped_and_exactly_the_maximum_is_kept` (50 تُحفظ و51 تُسقط)؛ ORCH `test_citations_the_module_dropped_warn_once_with_the_count_only`، `test_value_rules_are_the_modules_so_more_than_fifty_are_handed_over`؛ DOM `test_inv_cv4_caps_citations_at_the_maximum` | ✅ |
| حواف US‑1 | وحدة | ORCH `test_a_cut_off_reply_records_no_citations`، `test_a_failed_turn_after_tokens_records_no_citations`، `test_citations_are_saved_before_the_final_frame_is_delivered`، `test_a_failed_reply_write_delivers_final_without_message_id_and_does_not_error`؛ UC/DOM لحدّ 255/256 | ✅ |
| AC‑2.1 | وحدة (سلك) | ROUTER `test_a_streamed_answer_returns_the_same_citations_in_history` | ✅ |
| AC‑2.2 | وحدة + تكامل | SQL `test_a_row_with_null_citations_hydrates_to_none`؛ RLS `test_a_row_inserted_without_the_column_reads_back_as_none` | ✅ |
| AC‑2.3 | وحدة (سلك) | ROUTER `test_the_user_message_has_the_citations_key_with_null` | ✅ |
| AC‑2.4 | وحدة + تكامل | ROUTER `test_the_arabic_file_name_is_sent_as_utf8_not_escaped`؛ RLS (الذهاب والإياب بالاسم العربي) | ✅ |
| AC‑2.5 | وحدة (سلك) | ROUTER `test_each_message_keeps_its_own_citations_across_pages` | ✅ |
| AC‑2.6 | وحدة | ROUTER `test_content_stays_text_and_attachments_only`؛ `test_api_agents_router.py::test_invoke_without_stream_returns_the_stored_citations` | ✅ |
| AC‑2.7 | وحدة | SQL `test_list_messages_is_one_select_on_messages_alone_that_includes_citations` | ✅ |
| AC‑2.8 | وحدة | ROUTER `test_a_deleted_assistant_message_leaves_history_and_takes_its_citations_with_it` (**أُعيدت تسميته وشُدِّد**: يتحقق الآن أن الاسم العربي لا يظهر في أي مكان بعد الحذف، وأن الباقي رسالة المستخدم فقط) | ✅ (سدّ ملاحظة المطوّر 3) |
| AC‑3.1 | وحدة (سلك) | ROUTER `test_a_non_streamed_reply_has_the_same_citations_as_history` | ✅ |
| AC‑3.2 | وحدة (سلك) | `test_api_agents_router.py::test_invoke_without_stream_returns_the_stored_citations` | ✅ |
| AC‑3.3 | وحدة (سلك) | ROUTER `test_a_streamed_answer_returns_the_same_citations_in_history` (`assistant["citations"] == final["citations"]`)؛ ORCH `final.data["citations"] == declared` | ✅ |
| AC‑3.4 | وحدة (سلك) | ROUTER `test_an_agent_without_citations_replies_with_null`؛ agents `test_invoke_of_an_agent_without_citations_returns_null` | ✅ |
| AC‑3.5 | وحدة (سلك) | ROUTER `test_an_agent_that_states_no_sources_replies_with_an_empty_list` | ✅ |
| AC‑4.1 | عقد | `test_api_conventions.py` (اختبار مطابقة `MessageOut`/`Citation` مع `openapi.yaml`) | ✅ |
| AC‑4.2 | مراجعة وثيقة | `docs/design/03-api-spec.md` §2 و§3.1 (في الفرق: 12 سطراً)؛ لا يوجد اختبار آلي | ✅ (يدوي) |
| AC‑4.3 | مراجعة وثيقة | `06-domain-models.md` §4 و`01-data-model.md` §2.4؛ اختبار parity يتحقق أن `citations` مذكور في `01` | ✅ (يدوي + جزئي آلي) |
| AC‑4.4 | وحدة + CI | `test_conversations_schema_parity.py` (6 اختبارات جديدة: الاتجاهان، التهجئة، `nullable` بلا default و`none_as_null`، `DROP COLUMN` في downgrade). التطبيق عبر `provision` في CI `integration` فقط | ✅ (التطبيق الحي معلّق على CI) |
| AC‑4.5 | مراجعة وثيقة | `gaps.md` (+3 أسطر، فقرة «الإصلاح») | ✅ (يدوي) |
| AC‑5.1 | وحدة (سلك) | ROUTER `test_an_unknown_thread_is_a_404_problem_without_citations` | ✅ |
| AC‑5.2 | تكامل (CI) | RLS `test_citations_are_invisible_to_another_tenant`، رُوجع ساكناً فقط ولم يُشغَّل | ✅ ساكناً، 🟡 التشغيل في CI |
| AC‑5.3 | وحدة (سلك) | **أُضيف:** `test_api_rbac.py::test_the_message_history_needs_conversations_read_and_a_refusal_leaks_no_citations`. الموجود سابقاً (`EXPECTED[...messages, get] == CONVERSATIONS_READ`) يثبت الحارس في الخريطة فقط، ولا يوجد اختبار سلك لهذا المسار. الجديد: `platform_admin` يتلقى 403 `missing permission: conversations:read` بلا بيانات، و`viewer`/`member` يصلان إلى البحث (404) | ✅ (سدّ ملاحظة المطوّر 2) |
| AC‑5.4 | وحدة (سلك) | ROUTER `test_a_bad_token_is_401_on_the_messages_route`. لا اختبار لغياب الرمز كلياً على هذا المسار تحديداً، لكن 401 العام مغطى في `test_api_conversations_router.py` للمسارات الشقيقة | ✅ (جزئي، انظر الملاحظة 3 في القسم 4) |
| AC‑5.5 | معمارية | `lint-imports` (9 عقود سليمة، لا `ignore_imports` جديد) + `tests/architecture` | ✅ |

لا AC بلا تغطية. ملاحظة المطوّر 4 (`_CONSTRAINT_WORDS` في parity): راجعتها، الإصلاح معقول (يستثني أسطر القيود من أعمدة `CREATE TABLE`)؛ الاستخراج بالتعبير النمطي هش بطبيعته لكنه يفشل بصوت مرتفع إن تغيّر شكل `0001` (`assert match is not None`).

## 2. نتائج بوابات المشروع (من `/home/AIZZAK`)
```
$ .venv/bin/ruff format --check .
996 files already formatted
$ .venv/bin/ruff check .
All checks passed!
$ .venv/bin/mypy src
Success: no issues found in 551 source files
$ .venv/bin/lint-imports
... Infrastructure imported only by Composition Root KEPT
Framework does not import outer layers KEPT
WireTwin sections are independent and shared is the floor KEPT
Contracts: 9 kept, 0 broken.
$ .venv/bin/pytest -q -p no:warnings tests/unit tests/architecture tests/eval
... [100%]
exit=0
$ deploy/prometheus/test-rules.sh
SUCCESS: 31 rules found
test-rules: OK
```
ملاحظات: لم يُشغَّل `tests/integration` (قرار المالك ق‑4)، فاختبارات RLS وتطبيق الترحيل غير مُنفَّذة هنا. التعبير `pytest -q` لا يطبع عدد الاختبارات الإجمالي في الذيل المحفوظ.

## 3. الاختبار الاستكشافي
على مستوى الوحدة وTestClient فقط (لا تشغيل حي).

| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| اسم ملف عربي | `Citation(file_name="ا"*255)` و`"ا"*256` | 255 تُقبل و256 تُرفض | كما هو متوقع | ✅ |
| `file_name` فارغ `""` | `Citation(file_name="")` | رفض (1..255) | رُفض، فتسقط قائمة الاستشهادات كلها إلى `null` | ✅ سلوك مصمَّم، انظر الملاحظة 1 |
| `page=0`، `page=True`، `rank=True` | بناء `Citation` | رفض | رُفضت الثلاثة | ✅ |
| `document_id` بأحرف كبيرة | `Citation(document_id=UUID.upper())` | يُحفظ حرفياً | قُبل بلا تطبيع | ✅ |
| 50 مقابل 51 استشهاداً | UC و DOM | 50 تُحفظ و51 `null` | كما هو متوقع (اختبارات قائمة) | ✅ |
| عناصر تالفة، bool، غير UUID | ORCH (6 حالات) وROUTER (`chunk_id="chunk-a"`) | الدور ينجح، `citations=null`، تحذير واحد بلا PII | كما هو متوقع | ✅ |
| `null` مقابل `[]` على السلك | ROUTER/agents | المفتاح حاضر دائماً | كما هو متوقع | ✅ |
| `stream=false` على المسارين | `POST …/messages` و`POST /agents/{k}/invoke` | `citations` مساوية للسجل | كما هو متوقع | ✅ |
| رسالة مستخدم وصف قديم | ROUTER، SQL hydrate | `null` والرد 200 | كما هو متوقع | ✅ |
| ترقيم `limit=1` | ROUTER | كل رسالة باستشهاداتها | `[None, first, None, [], None, None]` | ✅ |
| `content` دون تغيير | ROUTER و agents | المفتاحان `text`,`attachments` | كما هو متوقع | ✅ |
| مساحة أخرى / 403 / 401 | RBAC وROUTER | 404 / 403 / 401 بلا استشهادات | كما هو متوقع | ✅ |
| قطع الاتصال بعد `final` | ORCH (`aclose()` بعد `final`) | الاستشهادات محفوظة | محفوظة | ✅ |
| RTL وعرض 375px | غير قابل للتطبيق (خلفية فقط) | | | لا ينطبق |

## 4. الأخطاء المكتشفة
لا أخطاء blocking أو major أو minor مؤكدة. ملاحظات للمتابعة (ليست أخطاء):

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| 1 | قائمة الاستشهادات تُسقط كلها (`null`) إذا فشل عنصر واحد في قاعدة قيمة، مثل `file_name=""` أو `page=0`، فيضيع الاستشهاد الصحيح معه. مصمَّم (إسقاط ليّن بالكامل، `use_cases.py:_recordable_citations`) ويطابق AC‑1.6 | ملاحظة | `Citation(file_name="")` ثم `AppendMessage` بقائمة فيها عنصر سليم وعنصر بهذا الاسم | مقبول، يُوثَّق لـ Android إن لزم |
| 2 | `_citations_from_json` (`sql_repository.py`) يعيد التحقق عند القراءة؛ صف مخزَّن لا يطابق قواعد أشد مستقبلاً يرفع استثناء ويكسر قراءة السجل كلها (500) بدل تخفيف الصف. غير قابل للحدوث اليوم لأن الكتابة تمر بالقواعد نفسها | ملاحظة | غير قابل للإعادة حالياً | للمراجعة إذا شُدِّدت القواعد لاحقاً |
| 3 | AC‑5.4: لا اختبار 401 بلا رمز كلياً على مسار الرسائل تحديداً (يوجد برمز سيئ) | ملاحظة | | التغطية العامة كافية |
| 4 | وثيقة `CLAUDE.md` تقول 8 عقود لـ `lint-imports` بينما الناتج 9 (عقد WireTwin) | ملاحظة وثائقية، خارج نطاق الميزة | `lint-imports` | للمالك |

## 5. الاختبارات المضافة (في هذا الالتزام)
- `tests/unit/test_llm_fallback.py`: `test_a_turn_answered_by_the_local_fallback_keeps_its_citations`، `test_a_healthy_cloud_turn_keeps_its_citations_without_a_notice` (مع وكيل `_CitingChatAgent`).
- `tests/unit/test_api_rbac.py`: `test_the_message_history_needs_conversations_read_and_a_refusal_leaks_no_citations`.
- `tests/unit/test_api_conversations_router.py`: إعادة تسمية اختبار الحذف وتشديده (لا إضعاف).

## 6. الحكم
**PASS** — البوابات الخمس خضراء، كل AC من نوع Must له اختبار ناجح أو مراجعة وثيقة مكتوبة، ولا أخطاء blocking أو major. تبقى خارج هذا الحكم اختبارات `tests/integration` (RLS وتطبيق الترحيل) التي تُشغَّل في CI قبل بوابة ②.

</div>
