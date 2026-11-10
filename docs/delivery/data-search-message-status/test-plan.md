<div dir="rtl">

# خطة الاختبار: ث‑2 حقل حالة الرسالة (`status` و`fallback`)

الفرع `feat/data-search-message-status` على `0976bcb`. الاختبارات الحية (`tests/integration`) لم تُشغَّل هنا بقرار المالك (المكدس الحي مستعمل)، وتعمل في CI job `integration`.

اختصارات: `ORCH` = `tests/unit/test_orchestrator.py`، `ROUTER` = `tests/unit/test_api_conversations_router.py`، `SQL` = `tests/unit/test_conversations_sql_statements.py`، `UC` = `tests/unit/test_conversations_use_cases.py`، `DOM` = `tests/unit/test_conversations_domain.py`، `PAR` = `tests/unit/test_conversations_schema_parity.py`، `FB` = `tests/unit/test_llm_fallback.py`، `WS` = `tests/unit/test_websocket_endpoint.py`، `QA` = `tests/unit/test_message_status_qa.py`، `RLS` = `tests/integration/test_conversations_repository_rls.py`، `MIG` = `tests/integration/test_message_status_migration_live.py`.

## 1. مصفوفة التغطية

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑1.1 | وحدة | `ORCH::test_a_turn_that_reached_final_is_stored_complete_with_no_fallback`، `UC::test_the_service_round_trips_status_and_fallback`، `SQL::test_append_writes_status_and_fallback_in_the_same_insert` | ✅ |
| AC‑1.2 | وحدة + راوتر | `ORCH::test_a_timed_out_turn_is_stored_partial`، `ROUTER::test_a_turn_cut_by_the_time_cap_is_partial_in_history` | ✅ |
| AC‑1.3 | وحدة | `ORCH::test_a_turn_whose_agent_failed_after_tokens_is_stored_partial` | ✅ |
| AC‑1.4 | وحدة | `QA::test_a_machinery_failure_after_tokens_is_stored_partial` (فرع `except Exception`، أُضيف) | ✅ |
| AC‑1.5 | وحدة | `ORCH::test_an_abandoned_stream_is_stored_partial` | ✅ |
| AC‑1.6 | وحدة | `ORCH::test_the_status_is_never_inferred_from_the_text` | ✅ |
| AC‑1.7 | وحدة | `ORCH::test_a_clarifying_question_is_stored_complete` | ✅ |
| AC‑1.8 | وحدة | `QA::test_a_confidence_gate_apology_is_complete_and_not_a_fallback` (أُضيف) | ✅ |
| AC‑1.9 | وحدة | `ORCH::test_a_media_agents_queued_job_is_stored_complete_and_its_frame_keeps_its_status` | ✅ |
| AC‑1.10 | وحدة | `SQL::test_append_writes_status_and_fallback_in_the_same_insert`، `SQL::test_append_without_status_binds_none_for_both` (عبارتان: INSERT ثم UPDATE للمحادثة، بلا UPDATE على الرسائل) | ✅ |
| AC‑1.11 | وحدة | `test_message_status_qa.py::test_a_machinery_failure_after_tokens_is_stored_partial` (يثبت `[None, "partial"]`، أي كتابة جزئية واحدة رغم استدعاء `_settle_cut` ثانيةً في `finally`). الاختبار السابق `ORCH::test_a_partial_reply_states_its_status_once` حُذف في `812bf9f` (ملاحظة المراجعة 4) | ✅ |
| AC‑1.12 | وحدة | `ROUTER::test_a_client_sent_status_or_fallback_in_content_is_not_stored`، `ORCH::test_the_user_turn_states_neither_status_nor_fallback` | ✅ |
| AC‑1.13 | وحدة | `UC::test_an_unknown_status_is_a_validation_error_and_writes_no_row`، `UC::test_a_status_on_a_user_message_is_a_validation_error`، `UC::test_a_fallback_without_a_status_is_a_validation_error`، `DOM::test_inv_cv5_only_assistant_messages_carry_status`، `DOM::test_inv_cv5_a_fallback_needs_a_status` | ✅ |
| AC‑1.14 | وحدة | `tests/unit/test_workflow_orchestration.py::test_a_completed_step_output_is_stored_complete_with_no_fallback` | ✅ |
| حافة US‑1: كاتبا الملخص في `workers/bootstrap.py` | وحدة | `tests/unit/test_workers_bootstrap.py` (تأكيدان `append.statuses == ["complete"]` لتسليم الملخص ولتسليم الفشل). راجعت الفرق: `status=_STATUS_COMPLETE` في الموضعين | ✅ |
| AC‑2.1 | وحدة (مسار `_FallbackLLM` الحقيقي) | `FB::test_a_switched_turn_is_stored_complete_with_the_final_frames_fallback` (أُضيف). مقارنة عميقة بالقيم الأربع | ✅ |
| AC‑2.2 | وحدة | `FB::test_a_local_model_that_dies_mid_answer_is_stored_partial_and_switched` (أُضيف) | ✅ |
| AC‑2.3 | وحدة | `FB::test_a_healthy_cloud_turn_is_stored_complete_without_a_fallback`، `FB::test_a_turn_the_cloud_started_and_lost_is_partial_with_no_fallback` (أُضيفا، الدور المختلط) | ✅ |
| AC‑2.4 | وحدة | `FB::test_an_english_question_is_stored_under_the_english_notice` (أُضيف)، ويبقى `test_a_turn_answered_by_the_local_fallback_keeps_its_citations` أخضر | ✅ |
| AC‑2.5 | وحدة | `ORCH::test_an_agent_that_puts_a_fallback_key_on_its_final_does_not_make_it_a_fallback`، `QA::test_a_confidence_gate_apology_is_complete_and_not_a_fallback` | ✅ |
| AC‑2.6 | وحدة | `DOM::test_a_fallback_holds_exactly_four_names`، `SQL::test_no_credential_can_reach_the_stored_fallback`، `ORCH::test_the_stored_fallback_carries_no_credential`، `FB::test_the_stored_fallback_carries_four_names_and_no_credential` | ✅ |
| AC‑3.1 | راوتر | `ROUTER::test_a_streamed_answer_is_complete_in_history` | ✅ |
| AC‑3.2 | راوتر | `ROUTER::test_a_turn_cut_by_the_time_cap_is_partial_in_history` | ✅ |
| AC‑3.3 | وحدة + حي | `SQL::test_a_row_without_status_hydrates_to_none_none`، `ROUTER::test_a_fallback_is_served_with_the_from_key_and_rows_without_status_are_null` (صف قديم نصه `old`)، `ROUTER::test_a_row_without_status_that_ends_with_the_cut_off_sentence_stays_null` (صف قديم ينتهي نصه بجملة الانقطاع: 200 و`null`/`null`)، `RLS::test_a_row_inserted_without_status_or_fallback_reads_back_as_none` (CI)، `MIG::…old_row_survives` (CI) | ✅ (الجزء الحي في CI فقط) |
| AC‑3.4 | راوتر | `ROUTER::test_the_user_message_has_status_and_fallback_keys_with_null` | ✅ |
| AC‑3.5 | راوتر | `ROUTER::test_every_kind_of_message_keeps_content_to_text_and_attachments` (أُضيف: مستخدم، مكتمل، قديم، جزئي مع تحول، وكلها بمفاتيح `status`/`fallback`/`citations` و`content` بمفتاحين). ويكمّله `test_content_stays_text_and_attachments_only` من ث‑1 | ✅ |
| AC‑3.6 | راوتر | `ROUTER::test_a_partial_arabic_answer_is_sent_as_utf8_and_marked_partial` | ✅ |
| AC‑3.7 | راوتر | `ROUTER::test_a_fallback_is_served_with_the_from_key_and_rows_without_status_are_null` (ثلاث رسائل، `limit=1`) | ✅ |
| AC‑3.8 | وحدة | `SQL::test_list_messages_selects_status_and_fallback_from_messages_alone` | ✅ |
| AC‑3.9 | راوتر | `ROUTER::test_a_deleted_partial_message_leaves_history` | ✅ |
| AC‑4.1 | راوتر | `ROUTER::test_a_non_streamed_reply_has_the_same_status_and_fallback_as_history` | ✅ |
| AC‑4.2 | راوتر | `tests/unit/test_api_agents_router.py::test_invoke_without_stream_returns_the_stored_status_and_fallback` | ✅ |
| AC‑4.3 | راوتر | `ROUTER::test_a_non_streamed_cut_turn_is_a_problem_but_history_keeps_the_partial` | ✅ |
| AC‑4.4 | وحدة | `ORCH::test_the_final_frame_gains_no_status_key`، وفرع التحول: `FB::test_a_switched_turn_keeps_the_final_frames_keys_unchanged` (أُضيف) | ✅ |
| AC‑4.5 | وحدة | `ORCH::test_a_media_agents_queued_job_is_stored_complete_and_its_frame_keeps_its_status` | ✅ |
| AC‑4.6 | راوتر | `ROUTER::test_a_stream_error_frame_keeps_its_problem_keys` | ✅ |
| AC‑4.7 | وحدة (WebSocket) | `WS::test_a_fallback_turn_keeps_the_notice_and_final_shapes_of_the_contract` (أُضيف، مسار fallback حقيقي، مجموعة المفاتيح مساوية لـ `03 §3.2`) | ✅ |
| AC‑5.1 | عقد | `tests/unit/test_api_conventions.py::test_the_contract_publishes_message_status_and_fallback_as_the_app_serves_them` | ✅ |
| AC‑5.2 | مراجعة | `docs/design/03-api-spec.md` (في الفرق: §2 وَ§3.1). فحصٌ يدوي، لا اختبار آلي | ✅ (مراجعة) |
| AC‑5.3 | مراجعة | `docs/design/06-domain-models.md` §4 (في الفرق). فحصٌ يدوي | ✅ (مراجعة) |
| AC‑5.4 | وحدة + حي | `PAR::test_the_status_and_fallback_columns_are_spelled_the_same_everywhere`، `…nullable_without_a_default`، `…fallback_writes_sql_null_for_none`، `…check_is_added_not_valid_and_never_validated`، `…downgrade_drops_the_constraint_and_both_columns`، `…continues_the_chain`. والذهاب والإياب الحي `MIG::test_downgrade_then_upgrade_round_trips_and_an_old_row_survives` | ✅ (الذهاب والإياب في CI فقط) |
| AC‑5.5 | مراجعة | `docs/design/01-data-model.md` §2.4 (في الفرق) | ✅ (مراجعة) |
| AC‑5.6 | مراجعة | تعليق `orchestrator.py` حول ب‑10 (في الفرق: يقول إن ث‑2 نقض القرار) | ✅ (مراجعة) |
| AC‑5.7 | مراجعة | `docs/delivery/data-search-android/gaps.md` (في الفرق، 3 أسطر). فحصٌ يدوي | ✅ (مراجعة) |
| AC‑6.1 | راوتر | `ROUTER::test_another_workspaces_history_is_a_404_with_no_status_or_fallback` (أُضيف). يتحقق أولاً أن المساحة A ترى الرسالة (200) ثم أن B تتلقى 404 problem+json بلا نص ولا `status` ولا `fallback` | ✅ |
| AC‑6.2 | حي | `RLS::test_status_and_fallback_are_invisible_to_another_tenant` | ✅ (CI فقط، لم يُشغَّل هنا) |
| AC‑6.3 | وحدة | `tests/unit/test_api_rbac.py::test_the_message_history_needs_conversations_read_and_a_refusal_leaks_no_citations` (403 على مسار الرسائل نفسه بلا `data`). تأكدتُ أنه يغطي المسار | ✅ |
| AC‑6.4 | وحدة | `ROUTER::test_a_bad_token_is_401_on_the_messages_route`، `ROUTER::test_no_token_is_401_on_the_messages_route`، `ROUTER::test_routes_require_a_bearer_token` | ✅ |
| AC‑6.5 | معمارية | `lint-imports` (9 عقود سليمة، انظر §2) و`tests/architecture`. `git diff` لا يُظهر تعديلاً على `.importlinter` | ✅ |

الإجمالي: 48 معياراً، 48 ✅، 0 🔴. منها 5 مراجعة يدوية (AC‑5.2، 5.3، 5.5، 5.6، 5.7)، و3 تعتمد جزئياً على اختبارات حية تعمل في CI فقط (AC‑3.3، AC‑5.4، AC‑6.2). الباقي (40) آلي ويعمل محلياً.

اختبارات أضفتها (كلها اختبارات فقط): 8 في `FB`، 1 في `WS`، 2 في `ROUTER`، 3 في `QA` (ملف جديد).

## 2. نتائج بوابات المشروع

أُعيد تشغيلها على الشجرة النهائية بعد إضافة الاختبارات (من `/home/AIZZAK`):

```
$ .venv/bin/ruff format --check .
999 files already formatted
$ .venv/bin/ruff check .
All checks passed!
$ .venv/bin/mypy src
Success: no issues found in 551 source files
$ .venv/bin/lint-imports
Framework does not import outer layers KEPT
WireTwin sections are independent and shared is the floor KEPT
Contracts: 9 kept, 0 broken.
$ .venv/bin/pytest -rs tests/unit tests/architecture tests/eval
8928 passed, 37 skipped, 7 warnings in 354.63s (0:05:54)
$ deploy/prometheus/test-rules.sh
  SUCCESS: 31 rules found
test-rules: OK
```

ملاحظات:
- `CLAUDE.md` يقول 8 عقود في `lint-imports` والمخرج الفعلي 9 (لا علاقة لها بهذه الميزة، ولا `ignore_imports` جديد).
- تُخطّى اختبارات `test_public_file_links_containers.py` وأشباهها لأنها تحتاج `RUN_EDGE_FILES_TEST=1` (حاويات). لم أرشّح أي اختبار.
- `tests/integration` لم يُشغَّل بقرار المالك. الاختبارات الحية الخمسة لهذه الميزة (أربعة في `RLS` وواحد في `MIG`) تُفحص في CI فقط، وقرأتُها مراجعةً ولم أنفّذها.

## 3. الاختبار الاستكشافي

| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| حالة غير صالحة | `service.append(role="assistant", status="error")`، و`"COMPLETE"`، و`""` | `ValidationError` ولا صف | `ValidationError` للثلاث (سكربت مباشر على المخزن في الذاكرة) | ✅ |
| حالة على رسالة مستخدم | `UC::test_a_status_on_a_user_message_is_a_validation_error` | `ValidationError` | كما هو متوقع | ✅ |
| `fallback` مشوّه (اسم نموذج من 300 حرفاً، الحد 255) | `QA::test_a_name_the_module_refuses_to_record_leaves_null_and_one_clean_warning` عبر الخدمة الحقيقية والمنسّق الحقيقي | الرسالة تُحفظ `complete` بـ `fallback = null`، وجملة التحول باقية، وتحذير واحد، والإطار يحمل `fallback` | كما هو متوقع. التحذير بلا اسم نموذج ولا مفتاح (فُحص نص السجل كله) | ✅ |
| `fallback` يُسقطه المنفذ | `FB::test_a_module_that_drops_the_fallback_costs_one_warning_without_model_names` | تحذير واحد بلا أسماء | كما هو متوقع | ✅ |
| رسائل المستخدم | `GET …/messages` | `status` و`fallback` حاضران بـ `null` | كما هو متوقع (`ROUTER::…user_message_has_status_and_fallback_keys_with_null`) | ✅ |
| صفوف قديمة | رسالة بلا الحقلين، نصها ينتهي بجملة الانقطاع | 200 و`null`/`null`، لا تُسمّى partial | كما هو متوقع (`ROUTER::test_a_row_without_status_that_ends_with_the_cut_off_sentence_stays_null`) | ✅ |
| جزئي مع تحول معاً | المحلي يموت بعد أول chunk | `partial` و`fallback` غير null | كما هو متوقع (`FB::…dies_mid_answer…`) | ✅ |
| دور مختلط (السحابي بدأ ثم سقط) | `fails_after` على السحابي | `partial` و`fallback = null` | كما هو متوقع | ✅ |
| نص جزئي طويل جداً بالعربية | 100,000 حرف «ج» مع `partial` و`fallback` عبر الخدمة | يُحفظ كاملاً | 100000 حرف، `partial`، `fallback` سليم | ✅ |
| `from` المحجوزة في بايثون | قراءة رسالة بتحول | مفتاح `"from"` لا `"from_"` | كما هو متوقع (`ROUTER::…never_renders_a_from_underscore_key`) | ✅ |
| مساحة عمل أخرى | `GET …/messages` من B لمحادثة A فيها `partial`+`fallback` | 404 problem+json بلا تسريب | كما هو متوقع | ✅ |
| بلا صلاحية / بلا رمز | 403 و401 على مسار الرسائل | بلا بيانات | كما هو متوقع (اختبارات قائمة) | ✅ |
| فشل مسار آلية المنسّق بعد token | منفّذ يرفع `RuntimeError` | `partial` ثم يُعاد رفع الاستثناء | كما هو متوقع | ✅ |
| ضغط مزدوج (double submit) | رسالة مكررة بالمفتاح نفسه | غير خاص بهذه الميزة | لم يُجرَّب: لا مفتاح idempotency على `POST …/messages` ولا تغيير في هذا المسار | ⏭ |
| RTL/LTR عند 375px | | | لا واجهة في هذا المستودع | ⏭ |
| ترحيل `0008` على قاعدة حية | | | لم يُشغَّل (CI فقط) | ⏭ |

## 4. الأخطاء المكتشفة

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| لا شيء | لم أجد عيباً في شيفرة المنتج. ملاحظتان توثيقيتان غير مانعتين أدناه | | | |
| N‑1 | `CLAUDE.md` يقول 8 عقود لـ `lint-imports` والفعلي 9 | minor (توثيق، المالك: orchestrator/docs) | `.venv/bin/lint-imports` ثم `Contracts: 9 kept` | مفتوح، ليس من هذه الميزة |
| N‑2 | تحذير `orchestrator.fallback_not_recorded` يصدر من المنسّق وحده حين يُسقط المنفذ الحقل. المنفذ نفسه (`_recordable_fallback`) يُسقط الاسم الطويل بصمت بلا سجل. التصميم يقبل هذا (سطر واحد في السجل يكفي) والتحذير يصدر فعلاً من طرف المنسّق (مُثبت في `QA`). لا تدخّل | minor / ملاحظة | `QA::test_a_name_the_module_refuses_to_record…` | للعلم |

## 5. الحكم

**PASS** — كل المعايير الـ48 مغطاة (40 آلياً محلياً، 3 جزئياً في CI الحي، 5 مراجعة مستندات)، كل البوابات خضراء، ولا أخطاء مانعة أو رئيسية. شرط الدمج: أن يمرّ CI job `integration` (`RLS` وَ`MIG`) لأنه الدليل الوحيد على AC‑6.2 والذهاب والإياب في AC‑5.4.

</div>
