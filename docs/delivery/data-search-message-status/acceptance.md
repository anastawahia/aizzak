<div dir="rtl">

# قبول مالك المنتج: ث‑2 حقل حالة الرسالة (`status` و`fallback`)

- **الفرع:** `feat/data-search-message-status` على `e47c37e` (آخر commit للشيفرة `812bf9f`)، والأساس `master` على `0976bcb`.
- **المصادر:** `stories.md` (6 قصص و48 معياراً، كلها Must)، و`test-plan.md` (QA PASS)، و`review.md` (PASS)، و`security-review.md` (PASS)، و`status.md` (ق‑1…ق‑4)، و`git diff 0976bcb..HEAD`.
- **ما تحققت منه بنفسي:**
  - بحثت بـ `grep` عن كل اختبار يذكره `test-plan.md`، فوجدتها كلها على الفرع إلا `test_orchestrator.py::test_a_partial_reply_states_its_status_once`. حُذف هذا الاختبار في `812bf9f` (ملاحظة المراجعة 4)، ودليل AC‑1.11 البديل في الجدول.
  - شغّلت ملفات الوحدة الأربعة عشر الخاصة بالميزة (`test_api_conversations_router`، `test_api_agents_router`، `test_conversations_domain`، `test_conversations_use_cases`، `test_conversations_sql_statements`، `test_conversations_schema_parity`، `test_llm_fallback`، `test_message_status_qa`، `test_orchestrator`، `test_websocket_endpoint`، `test_workers_bootstrap`، `test_workflow_orchestration`، `test_api_conventions`، `test_api_rbac`). النتيجة `626 passed, 1 warning in 16.57s`.
  - قرأت فرق الوثائق الملزمة: `03-api-spec.md`، و`06-domain-models.md`، و`01-data-model.md`، و`openapi.yaml`، و`gaps.md`، وتعليق ب‑10 في `orchestrator.py`.
- **بوابات المنسّق على `812bf9f`:** ruff format وruff check وmypy (551 ملفاً) خضراء. `lint-imports` يعطي 9 kept و0 broken. `pytest tests/unit tests/architecture tests/eval` يعطي 8929 passed و37 skipped، exit 0. و`test-rules.sh` OK (31 قاعدة).
- **لم يُشغَّل:** `tests/integration`، بقرار المالك (ق‑4، س‑10). يشغّله CI job `integration`.

اختصارات (كما في `test-plan.md`):

| الاختصار | الملف |
|---|---|
| `ORCH` | `tests/unit/test_orchestrator.py` |
| `ROUTER` | `tests/unit/test_api_conversations_router.py` |
| `SQL` | `tests/unit/test_conversations_sql_statements.py` |
| `UC` | `tests/unit/test_conversations_use_cases.py` |
| `DOM` | `tests/unit/test_conversations_domain.py` |
| `PAR` | `tests/unit/test_conversations_schema_parity.py` |
| `FB` | `tests/unit/test_llm_fallback.py` |
| `WS` | `tests/unit/test_websocket_endpoint.py` |
| `QA` | `tests/unit/test_message_status_qa.py` |
| `RLS` | `tests/integration/test_conversations_repository_rls.py` |
| `MIG` | `tests/integration/test_message_status_migration_live.py` |

## US‑1: حالة الرسالة تُحفظ عند كتابتها (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑1.1 | `ORCH::test_a_turn_that_reached_final_is_stored_complete_with_no_fallback`، و`UC::test_the_service_round_trips_status_and_fallback`، و`SQL::test_append_writes_status_and_fallback_in_the_same_insert` | ✅ مقبول |
| AC‑1.2 | `ORCH::test_a_timed_out_turn_is_stored_partial`، و`ROUTER::test_a_turn_cut_by_the_time_cap_is_partial_in_history` | ✅ مقبول |
| AC‑1.3 | `ORCH::test_a_turn_whose_agent_failed_after_tokens_is_stored_partial` | ✅ مقبول |
| AC‑1.4 | `QA::test_a_machinery_failure_after_tokens_is_stored_partial` (فرع `except Exception`) | ✅ مقبول |
| AC‑1.5 | `ORCH::test_an_abandoned_stream_is_stored_partial` | ✅ مقبول |
| AC‑1.6 | `ORCH::test_the_status_is_never_inferred_from_the_text` | ✅ مقبول |
| AC‑1.7 | `ORCH::test_a_clarifying_question_is_stored_complete` | ✅ مقبول |
| AC‑1.8 | `QA::test_a_confidence_gate_apology_is_complete_and_not_a_fallback` | ✅ مقبول |
| AC‑1.9 | `ORCH::test_a_media_agents_queued_job_is_stored_complete_and_its_frame_keeps_its_status` | ✅ مقبول |
| AC‑1.10 | `SQL::test_append_writes_status_and_fallback_in_the_same_insert` يؤكد: عبارتان (`INSERT` على الرسائل ثم `UPDATE` على المحادثة)، ولا `UPDATE conversations.messages`. والعدد 2 هو نفسه على `master` (`len(statements) == 2` في الملف على `0976bcb`). ومعه `SQL::test_append_without_status_binds_none_for_both` | ✅ مقبول |
| AC‑1.11 | الاختبار الذي يذكره `test-plan.md` حُذف في `812bf9f`. الدليل البديل `QA::test_a_machinery_failure_after_tokens_is_stored_partial`، ويؤكد `statuses == [None, "partial"]`، أي كتابة جزئية واحدة. قرأت `orchestrator.py:2061-2111`: في فرع `except Exception` لا يُضبط `terminal`، فيستدعي الـ `finally` الدالة `_settle_cut` مرة ثانية، والحارس `record.message` (`:2226`) يمنع الكتابة الثانية. فالاختبار يمر بالمسار المقطوع وبالـ `finally` معاً، كما يطلب المعيار | ✅ مقبول |
| AC‑1.12 | `ROUTER::test_a_client_sent_status_or_fallback_in_content_is_not_stored`، و`ORCH::test_the_user_turn_states_neither_status_nor_fallback` | ✅ مقبول |
| AC‑1.13 | `UC::test_an_unknown_status_is_a_validation_error_and_writes_no_row`، و`UC::test_a_status_on_a_user_message_is_a_validation_error`، و`UC::test_a_fallback_without_a_status_is_a_validation_error`، و`DOM::test_inv_cv5_only_assistant_messages_carry_status`، و`DOM::test_inv_cv5_a_fallback_needs_a_status` | ✅ مقبول |
| AC‑1.14 | `tests/unit/test_workflow_orchestration.py::test_a_completed_step_output_is_stored_complete_with_no_fallback` | ✅ مقبول |

حواف US‑1: `ORCH::test_a_stream_cut_before_it_said_anything_stores_nothing` و`ORCH::test_a_completed_turn_whose_write_failed_is_not_saved_as_a_partial` موجودان وأخضران. (`stories.md` يذكر الأول دون ملف. وهو في `ORCH` لا في `ROUTER`.)

## US‑2: مؤشر منفصل لجواب النموذج المحلي (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑2.1 | `FB::test_a_switched_turn_is_stored_complete_with_the_final_frames_fallback`: مسار `_FallbackLLM` الحقيقي، مع مقارنة عميقة للقيم الأربع | ✅ مقبول |
| AC‑2.2 | `FB::test_a_local_model_that_dies_mid_answer_is_stored_partial_and_switched` | ✅ مقبول |
| AC‑2.3 | `FB::test_a_healthy_cloud_turn_is_stored_complete_without_a_fallback`، و`FB::test_a_turn_the_cloud_started_and_lost_is_partial_with_no_fallback` (الدور المختلط) | ✅ مقبول |
| AC‑2.4 | `FB::test_an_english_question_is_stored_under_the_english_notice`، و`FB::test_a_turn_answered_by_the_local_fallback_keeps_its_citations`، و`ORCH::test_a_fallback_turn_keeps_its_citations_and_still_gets_the_notice_key` (أخضر كما يطلب المعيار) | ✅ مقبول |
| AC‑2.5 | `ORCH::test_an_agent_that_puts_a_fallback_key_on_its_final_does_not_make_it_a_fallback`، و`QA::test_a_confidence_gate_apology_is_complete_and_not_a_fallback` (الوكيل يضع `fallback: True`، والمحفوظ `None`) | ✅ مقبول |
| AC‑2.6 | `DOM::test_a_fallback_holds_exactly_four_names`، و`ORCH::test_the_stored_fallback_carries_no_credential`، و`FB::test_the_stored_fallback_carries_four_names_and_no_credential` (`api_key` على `_Fallback` لا يصل إلى المحفوظ) | ✅ مقبول |

## US‑3: الحالة والمؤشر من السجل (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑3.1 | `ROUTER::test_a_streamed_answer_is_complete_in_history` | ✅ مقبول |
| AC‑3.2 | `ROUTER::test_a_turn_cut_by_the_time_cap_is_partial_in_history` | ✅ مقبول |
| AC‑3.3 | `ROUTER::test_a_row_without_status_that_ends_with_the_cut_off_sentence_stays_null` (أُضيف في `812bf9f`، ويغلق ملاحظة المراجعة 1): صف بلا حالة ينتهي بجملة الانقطاع، والنتيجة 200 و`null`/`null`. ومعه `SQL::test_a_row_without_status_hydrates_to_none_none`. والجزء الحي (`RLS::test_a_row_inserted_without_status_or_fallback_reads_back_as_none`، و`MIG::test_downgrade_then_upgrade_round_trips_and_an_old_row_survives`) تأكيد إضافي في CI | ✅ مقبول |
| AC‑3.4 | `ROUTER::test_the_user_message_has_status_and_fallback_keys_with_null` | ✅ مقبول |
| AC‑3.5 | `ROUTER::test_every_kind_of_message_keeps_content_to_text_and_attachments`، و`ROUTER::test_content_stays_text_and_attachments_only` (ث‑1) | ✅ مقبول |
| AC‑3.6 | `ROUTER::test_a_partial_arabic_answer_is_sent_as_utf8_and_marked_partial` | ✅ مقبول |
| AC‑3.7 | `ROUTER::test_a_fallback_is_served_with_the_from_key_and_rows_without_status_are_null`: ثلاث رسائل مساعد بـ `limit=1` حتى `next_cursor: null`. الخليط فيه قديم `null`، و`partial` مع `fallback`، و`complete`، بدل الخليط الذي يذكره المعيار (`complete`، و`partial`، و`complete` مع `fallback`). المقصود ثابت: كل رسالة تحمل قيمتها هي عبر الصفحات، والقيم الثلاث مختلفة، وفيها `fallback` غير `null`. أقبله | ✅ مقبول |
| AC‑3.8 | `SQL::test_list_messages_selects_status_and_fallback_from_messages_alone` | ✅ مقبول |
| AC‑3.9 | `ROUTER::test_a_deleted_partial_message_leaves_history` | ✅ مقبول |

## US‑4: الرد غير المتدفق، والإطارات بلا تغيير (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑4.1 | `ROUTER::test_a_non_streamed_reply_has_the_same_status_and_fallback_as_history` | ✅ مقبول |
| AC‑4.2 | `tests/unit/test_api_agents_router.py::test_invoke_without_stream_returns_the_stored_status_and_fallback` | ✅ مقبول |
| AC‑4.3 | `ROUTER::test_a_non_streamed_cut_turn_is_a_problem_but_history_keeps_the_partial` | ✅ مقبول |
| AC‑4.4 | `ORCH::test_the_final_frame_gains_no_status_key`، وفرع التحول `FB::test_a_switched_turn_keeps_the_final_frames_keys_unchanged` | ✅ مقبول |
| AC‑4.5 | `ORCH::test_a_media_agents_queued_job_is_stored_complete_and_its_frame_keeps_its_status` | ✅ مقبول |
| AC‑4.6 | `ROUTER::test_a_stream_error_frame_keeps_its_problem_keys` | ✅ مقبول |
| AC‑4.7 | `WS::test_a_fallback_turn_keeps_the_notice_and_final_shapes_of_the_contract` | ✅ مقبول |

## US‑5: العقد والوثائق الملزمة والترحيل (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑5.1 | `tests/unit/test_api_conventions.py::test_the_contract_publishes_message_status_and_fallback_as_the_app_serves_them`. وقرأت فرق `openapi.yaml`، وفيه: `status` من نوع string، وnullable، و`enum: [complete, partial]`، ودلالة `null`، و«never inferred from its text». و`fallback` nullable يشير إلى `MessageFallback` (`from`/`to` مطلوبان، وفي كل منهما `provider`/`model` مطلوبان من نوع string)، ووصفه «equal to the `fallback` key of that turn's `final` frame». وصياغة «primary lost» صُححت في `812bf9f` (ملاحظة المراجعة 2) | ✅ مقبول |
| AC‑5.2 | **ما تحقق:** سطر `MessageOut` في §2 يذكر الحقلين (`03-api-spec.md:128`). و§3.1 يقول إن جواب النموذج المحلي يُحفظ معه `fallback` والجملة باقية (`:397`: «ويبدأ الردُّ المحفوظ بالجملة نفسِها… وتُحفظ القيمة نفسها مع الرسالة»). ويقول إن الدور المقطوع يُحفظ `partial` (`:406`). **ما ينقص:** لا يقول §3.1، ولا أي موضع في `03-api-spec.md`، إن نص الرسالة الجزئية **ما زال ينتهي بجملة الانقطاع**. وهذا هو الجزء «(والجملة تبقى)» من المعيار للحالة المقطوعة. بحثت عن «اكتمالها» و«cut off» في الملف فلم أجد شيئاً. والذكر الوحيد للجملة في `:117` يخص جواباً **يقتبسها**. أما `openapi.yaml` و`01 §2.4` و`06 §4` فتقول ذلك صراحة | 🔴 مرفوض |
| AC‑5.3 | فرق `06-domain-models.md` §4: `Message` يحمل `status` و`fallback`، والثابت INV‑CV5 جديد. وعبارة «تحويلها إلى حقل قرارٌ لاحق» حُذفت. ويذكر الفرق صراحة «ينقض قرار ب‑10 رقم 1… الجملتان باقيتان في النص». وموافقة المالك على س‑1 هي ق‑2 | ✅ مقبول |
| AC‑5.4 | `PAR::test_the_status_and_fallback_columns_are_spelled_the_same_everywhere`، و`PAR::test_status_and_fallback_are_nullable_without_a_default`، و`PAR::test_fallback_writes_sql_null_for_none`، و`PAR::test_the_status_check_is_added_not_valid_and_never_validated` (لا إعادة كتابة للجدول)، و`PAR::test_the_status_migration_downgrade_drops_the_constraint_and_both_columns`، و`PAR::test_the_status_migration_continues_the_chain`. كلها خضراء. وتطبيق الترحيل فعلاً (`MIG::test_downgrade_then_upgrade_round_trips_and_an_old_row_survives`) يثبت في CI `integration`، وهذا شرط الدمج أدناه | ✅ مقبول (بشرط CI) |
| AC‑5.5 | فرق `01-data-model.md` §2.4: العمودان، ودلالة `NULL` بلا backfill ولا DEFAULT، والقيد `ck_messages_status` بـ `NOT VALID` | ✅ مقبول |
| AC‑5.6 | `orchestrator.py:427-437` يقول «ث-2 overturned it by owner decision (2026-11-10)… The sentence stays». وعبارة «not decided here» حُذفت (أكدتها المراجعة) | ✅ مقبول |
| AC‑5.7 | فرق `gaps.md`: فقرة «الإصلاح (2026‑11‑10)» تحت ث‑2، وتذكر صراحة أن «فشل بلا رسالة» (سجل المحاولة) باقٍ في ث‑3 | ✅ مقبول |

## US‑6: العزل والصلاحيات (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑6.1 | `ROUTER::test_another_workspaces_history_is_a_404_with_no_status_or_fallback`: المساحة A ترى الرسالة (200)، والمساحة B تتلقى 404 بلا `status` ولا `fallback` | ✅ مقبول |
| AC‑6.2 | `RLS::test_status_and_fallback_are_invisible_to_another_tenant` موجود على الفرع، وقرأته المراجعة الأمنية. **لكنه لم يُنفَّذ بعد.** المعيار نفسه يحدد أن دليله تشغيل CI `integration` (ق‑4)، والفرع لم يُدفع. فلا دليل منفَّذ حتى الآن. يصير ✅ حين يخضرّ CI `integration` على الـ PR | 🔴 مرفوض (بانتظار الدليل) |
| AC‑6.3 | `tests/unit/test_api_rbac.py::test_the_message_history_needs_conversations_read_and_a_refusal_leaks_no_citations` | ✅ مقبول |
| AC‑6.4 | `ROUTER::test_a_bad_token_is_401_on_the_messages_route`، و`ROUTER::test_no_token_is_401_on_the_messages_route`، و`ROUTER::test_routes_require_a_bearer_token` | ✅ مقبول |
| AC‑6.5 | `lint-imports` يعطي «Contracts: 9 kept, 0 broken» (المعيار يقول ثمانية والفعلي تسعة، انظر N‑1). وملف `.importlinter` بلا تغيير في الفرق. والمراجعة أكدت أن `conversations.domain` يستورد stdlib فقط (`enum`، `dataclasses`) | ✅ مقبول |

**العدد:** 48 معياراً، كلها Must. **46 ✅** (منها 5 بمراجعة الوثائق: AC‑5.1 جزئياً، و5.3، و5.5، و5.6، و5.7، وواحد بشرط CI: AC‑5.4). و**2 🔴**: AC‑5.2 وAC‑6.2.

## الكاتبان الإضافيان في `workers/bootstrap.py` (حكم النطاق)

**داخل النطاق المعتمد، وأقبلهما.** السبب:

- هدف `requirements.md:22` الذي وافق عليه المالك (ق‑1): «تحمل **كل رسالة مساعد جديدة** حالة منظَّمة محفوظة». عاملا تسليم الملخص (`bootstrap.py:1090` و`:1183`) يكتبان رسائل مساعد جديدة. لو بقيا بلا حالة لخرجت رسائل جديدة بـ `null`، والعميل لا يستطيع تمييزها عن الصفوف القديمة. وهذا عكس دلالة `null` التي اعتمدها المالك («لم يُسجَّل»).
- `complete` هي القيمة الصحيحة: الملخص (أو إشعار فشله) يصل رسالة كاملة، ونتيجة المهمة في النص. وهذه سابقة AC‑1.9 نفسها (مهمة وسائط `queued` تُحفظ `complete`).
- لا تغيير في العقد أو الصلاحيات أو القاعدة غير ما اعتُمد. التغيير ثابت وسطران. الاختباران في `tests/unit/test_workers_bootstrap.py` يؤكدان `append.statuses == ["complete"]` للتسليم وللفشل. وهو موثّق في `03 §2` و`openapi.yaml` و`06 §4` («ملخصاً سلّمه العامل»).
- المراجعة تقول إنه لا يوجد كاتب آخر لرسالة مساعد في `src/` (خمسة مواضع فقط).

## ملاحظات غير حاجبة مقبولة

- **SR‑1 (معلومة):** أسماء المزوّد والنموذج في `fallback` مرئية لكل عضو في المساحة له `conversations:read`. أقبله، لأنها الأسماء نفسها التي يعرضها `GET /models` للأدوار نفسها. يُعاد النظر إن صارت المحادثات خاصة بكل مستخدم أو صار اسم النموذج معرّفاً خاصاً بالمستأجر.
- **SR‑2 (معلومة):** صف `fallback` تالف (لا يحدث إلا بكتابة يدوية في القاعدة) يجعل صفحة السجل كلها 500، كنمط SR‑6 في ث‑1. أقبله الآن، ويُعالج مع SR‑6 إن قرر المالك.
- **SR‑3 (معلومة، سلوك سابق):** مفتاح `fallback` يضعه وكيل على `final` بلا تحوّل يبقى على الإطار، والمحفوظ `null`. المرجع للعميل هو `MessageOut.fallback`. أقبله، وخارج نطاق ث‑2.
- **ملاحظة المراجعة 3:** `SQL::test_no_credential_can_reach_the_stored_fallback` لا يمكن أن يفشل. أقبل بقاءه، لأن AC‑2.6 مثبت بثلاثة اختبارات أخرى ذات معنى (انظر الجدول). ولم أعتمده دليلاً.
- **N‑1:** `CLAUDE.md` يقول 8 عقود لـ `lint-imports`، والفعلي 9. توثيق خارج الميزة.
- **N‑2:** المنفذ يُسقط اسم النموذج الطويل بصمت، والتحذير يصدر من المنسّق. مقبول في التصميم.
- **`test-plan.md` سطر AC‑1.11:** ما زال يذكر الاختبار المحذوف `ORCH::test_a_partial_reply_states_its_status_once`. يُصحَّح في الدورة نفسها إلى `QA::test_a_machinery_failure_after_tokens_is_stored_partial`. لا يمنع القبول، لأن الدليل البديل موجود ومفحوص.
- **AC‑3.7:** الخليط في الاختبار يختلف عن نص المعيار، والمقصود ثابت (انظر الجدول).

## الحكم النهائي

**مرفوض.** السبب: معياران من نوع Must ليسا ✅ بعد. لا خلل في الشيفرة: كل سلوك المنتج مثبت، و46 من 48 معياراً مقبولة.

1. **AC‑5.2: يعود إلى المرحلة 6 (backend-developer)، تعديل وثيقة فقط.**
   - **المطلوب:** في `docs/design/03-api-spec.md` §3.1، في فقرة «(ث‑2) حالة الرسالة» (`:406`)، تُضاف جملة واحدة: نص الرسالة الجزئية ما زال ينتهي بجملة الانقطاع (`⚠️ انقطعت الإجابةُ قبل اكتمالها.` أو `⚠️ This answer was cut off before it finished.`) كما كان، والحقل يُضاف إلى الجملة ولا يحل محلها.
   - **وفي الدورة نفسها:** يُصحَّح سطر AC‑1.11 في `test-plan.md`.
   - **بعد التعديل:** تُعاد البوابات 1–5. وتكفي مراجعة code-reviewer للسطر وحده. ثم أعيد أنا فحص AC‑5.2 وحده.
2. **AC‑6.2: لا يعود إلى مرحلة.** يحتاج دليلاً منفَّذاً فقط:
   - يخضرّ CI job `integration` على الـ PR، ومعه `RLS` الأربعة و`MIG`.
   - عندها يصير AC‑6.2 ✅، ويُستوفى شرط AC‑5.4 (الذهاب والإياب للترحيل) والجزء الحي من AC‑3.3.

**شرط القبول النهائي:** يُصلَح AC‑5.2، ويخضرّ CI `integration`. عندها تصير المعايير الـ48 كلها ✅ وتنتقل الميزة إلى بوابة ② (الدمج، قرار بشري).

</div>
