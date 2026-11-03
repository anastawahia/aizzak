<div dir="rtl">

# خطة الاختبار: electrical-drawings، INC‑1، `PR‑C` (قلب WireTwin، الخطوات S5..S10)

الفرع: `feat/electrical-drawings-inc-1-pr-c` (الأساس `master` = `dc4289f`، الرأس `60b75f2`، 17 إيداعاً). المرجع: `docs/lld-inc-1.md` 0.6 §5.1 و§5.2. مفحوص بالتشغيل لا بالقراءة وحدها: كل جدول أدناه أُعيد تشغيله على الرأس، وإيداع QA يضيف ملفي اختبار فقط (§6).

## 1. مصفوفة التغطية

«حيّ» = يعمل على Postgres/MinIO/Redis حقيقية، وقد شُغِّل في هذه الجولة على حاويات مؤقتة (§6)، وفي CI ضمن وظيفة `integration`. بقية الاختبارات تعمل محلياً في `pytest tests/unit tests/architecture tests/eval`.

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| INC1‑AC‑5: مشروع واحد لكل فضاء، 409 للثاني، 404 للفضاء المجهول، القراءة والسرد والتعديل | وحدة + واجهة + حيّ | `test_electrical_projects.py::test_the_second_project_of_a_space_is_a_conflict_and_leaves_nothing`، `::test_an_unknown_or_deleted_space_is_a_404`، `::test_get_list_and_update`؛ `test_api_electrical_router.py::test_a_second_project_in_the_space_is_a_409_and_an_unknown_space_a_404`، `::test_a_project_is_patched_and_changing_nothing_writes_nothing`؛ حيّ: `test_electrical_drawings_repository_rls.py::test_project_round_trips_and_one_project_per_space` | ✅ |
| INC1‑AC‑5: ملف الإعداد بإصدارات متتالية لا تُعدَّل وبصمة، والملف غير الصالح `422 electrical.invalid_profile` يسمّي الحقل | وحدة + واجهة + حيّ | `test_electrical_projects.py::test_the_project_profile_is_append_only_with_consecutive_versions`، `::test_the_key_order_of_a_body_does_not_change_its_fingerprint`، `::test_an_invalid_profile_is_a_422_naming_the_field_and_writes_nothing`؛ `test_electrical_drawings_domain.py` (BT15)؛ `test_api_electrical_router.py::test_the_project_profile_is_replaced_by_a_new_version`، `::test_an_invalid_profile_is_a_422_that_names_the_field_and_never_echoes_the_client`؛ حيّ: `::test_profile_versions_number_per_owner_from_one_and_round_trip`، `::test_a_lost_race_on_a_profile_version_is_a_conflict` | ✅ |
| INC1‑AC‑5: الصلاحيات (§3.3.16) | عقد | `test_api_rbac.py` (الصفوف العشرون)؛ `test_api_electrical_router.py::test_a_member_reads_and_previews_but_neither_registers_nor_changes_a_profile`، `::test_a_viewer_reads_and_nothing_more` | ✅ |
| INC1‑AC‑6: التسجيل، وثيقة ومهمة وحدث في وحدة عمل واحدة و202 | وحدة + واجهة | `test_electrical_registration.py::test_a_registration_creates_the_document_the_job_and_the_event_in_one_unit`، `::test_a_failing_outbox_leaves_no_document_and_no_job`؛ `test_api_electrical_router.py::test_registering_a_document_answers_202_with_the_document_and_its_queued_job` | ✅ |
| INC1‑AC‑6: كل فرع رفض بالترتيب ولا كتابة (مشروع، ملف، PDF، فضاء، `document_exists`، سميّ، مكرّر، طابور) | وحدة + واجهة | `test_electrical_registration.py` (فرع لكل اختبار، ثم `::test_the_first_failing_check_is_the_answer`، 22 حالة)؛ `test_api_electrical_router.py::test_every_refusal_is_its_own_code_and_writes_nothing`، `::test_a_full_queue_is_a_429_with_retry_after`، `::test_document_exists_says_an_excluded_document_still_holds_its_file` | ✅ |
| INC1‑AC‑6: مفتاح التكرار والضغط المزدوج | واجهة + حيّ + **QA** | `test_api_electrical_router.py::test_the_idempotency_key_replays_the_first_answer_and_creates_one_document`؛ حيّ: `test_electrical_drawings_repository_rls.py::test_two_concurrent_registrations_of_one_file_one_wins`؛ **جديد** `test_electrical_qa_pr_c.py::test_a_replayed_registration_that_excluded_an_orphan_counts_the_exclusion_once` | ✅ |
| INC1‑AC‑6: `heavy_job` يحرس المسارات الأربعة | عقد | `test_heavy_job_limit.py`؛ `test_api_electrical_router.py::test_the_heavy_job_guard_sits_in_front_of_the_202s_and_refuses_before_any_write` | ✅ |
| INC1‑AC‑6: استبعاد اليتيم عند التسجيل التالي وعدّه | وحدة + واجهة + **QA** | `test_electrical_registration.py::test_orphans_are_excluded_in_the_same_unit_and_reported`، `::test_a_failed_registration_does_not_keep_the_exclusions`؛ `test_api_electrical_router.py::test_an_orphaned_document_is_excluded_at_registration_and_counted`؛ **جديد** (العدّ لا يتكرر عند إعادة الجواب) | ✅ |
| INC1‑AC‑7: البصمة والتدفق والمحتوى المكرّر `duplicate_document` | وحدة + حيّ | `test_electrical_inspection.py::test_an_ingestion_fingerprints_classifies_and_proposes`، `::test_the_same_bytes_as_an_active_document_exclude_the_newcomer`، `::test_a_size_mismatch_fails_the_job_as_source_unreadable`؛ `test_minio_streaming_adapter.py`، `test_storage_handle.py`؛ حيّ: `test_minio_storage.py -k read_chunks` (3)، `test_electrical_ingestion_live.py` | ✅ |
| INC1‑AC‑7: `pdf_rejected`، وفشل صفحة وحدها وبقية الصفحات تكمل، والعتبة في الاتجاهين، والحتمية | وحدة | `test_electrical_inspection.py::test_a_rejected_pdf_fails_with_ele_parses_message_and_touches_nothing_else`، `::test_a_failing_page_is_recorded_and_the_rest_complete`، `::test_the_threshold_is_inclusive_in_both_directions`، `::test_a_confident_proposal_succeeds_and_a_doubtful_one_awaits_confirmation`؛ `test_electrical_profile_detection.py::test_the_same_inputs_give_the_same_bytes` | ✅ |
| INC1‑AC‑7: تصنيف الصفحة ولغتها وOCR، والكشف (IEC وGB/T وNFPA والاختلاط والعيّنة الصغيرة) | وحدة | `test_electrical_classification.py` (27 حالة)، `test_electrical_profile_detection.py` (29 حالة) | ✅ |
| INC1‑AC‑7: خدمة `ele-parse` على PDFs مولّدة (متجهي، صورة، فارغ، مشفّر، تالف) والمهلة والذاكرة | وحدة | `test_ele_parse_service.py` (BT26)؛ **QA** `test_electrical_qa_pr_c.py::test_a_summary_of_the_same_damaged_file_is_fine_which_is_why_the_defect_hides` | ✅ |
| INC1‑AC‑7: صفحة بمحتوى تالف جزئياً (تحذيرات MuPDF) تُفحص | **QA** (xfail) | `test_electrical_qa_pr_c.py::test_a_page_with_a_damaged_content_stream_is_still_inspected` | 🔴 **QA‑1** (ليس شرطاً حرفياً في AC‑7، انظر §4) |
| INC1‑AC‑7: من التسجيل إلى `succeeded` بالمخازن الحقيقية، وصف واحد في `processed_events` بعد تسليمين | حيّ | `test_electrical_ingestion_live.py::test_a_drawing_goes_from_registration_to_a_proposal_through_the_real_stores` (BT38) | ✅ |
| INC1‑AC‑8: آلة الحالة (كل انتقال) | وحدة + **QA** | `test_electrical_drawings_domain.py` (BT15)؛ **جديد** `test_electrical_qa_pr_c.py::test_every_transition_is_allowed_or_refused_exactly_as_the_table_says` (56 حالة: 7 حالات × 8 انتقالات، ورفض لا يغيّر شيئاً) | ✅ |
| INC1‑AC‑8: الإلغاء (فوري للمنتظرة وتعاوني للجارية)، الاستئناف، إعادة المعالجة، مهمة مفتوحة واحدة | وحدة + **QA** | `test_electrical_jobs.py` (BT20، 21 اختباراً)؛ **جديد** `::test_a_redelivered_message_for_a_cancelled_job_does_nothing_and_is_claimed`، `::test_cancelling_a_running_job_twice_keeps_the_first_request_and_writes_once`، `::test_a_cancel_request_on_a_job_whose_worker_is_gone_is_honoured_on_takeover`، `::test_cancelling_a_proposal_leaves_two_ways_out_and_resume_is_one_of_them`، `::test_reprocess_and_resume_cannot_both_win_for_one_document` | ✅ |
| INC1‑AC‑8: العقد الحيّ يردّ تشغيلاً ثانياً، والاستحواذ بعد انتهاء العقد | وحدة + **QA** | `test_electrical_inspection.py::test_a_live_lease_held_by_a_running_peer_turns_a_second_run_away_without_a_claim`، `::test_an_expired_lease_is_taken_over_and_the_attempt_counted`؛ **جديد** `test_electrical_qa_pr_c.py::test_a_live_lease_blocks_a_second_run_and_an_expired_one_is_taken_over` | ✅ |
| INC1‑AC‑8: `ux_drawings_jobs_open` ولا تشغيلان متزامنان | حيّ + **QA حيّ** | `test_electrical_drawings_repository_rls.py::test_one_open_job_per_document_and_closed_ones_do_not_count`؛ **جديد** `test_electrical_qa_live.py::test_a_cancel_and_a_lease_claim_racing_never_lose_the_cancellation` | ✅ |
| INC1‑AC‑8: التقدّم إشعاراً بـ`seq` متزايد ومقيّد، والعدّ المشتق والموضع والتقدير | وحدة | `test_electrical_event_mapping.py::test_the_progress_sequence_is_whatever_the_worker_numbered`، `test_electrical_worker.py`، `test_electrical_jobs.py::test_the_counts_are_derived_from_the_stage_rows`، `::test_queue_position_and_estimate_use_the_median_of_the_last_runs` | ✅ |
| INC1‑AC‑9: عزل `ele-parse` (Compose) | وحدة (ملف) | `test_compose_electrical_profile.py` (18 اختباراً: الملف، الشبكة الداخلية وحدها، بلا أسرار، قراءة فقط، مستخدم غير جذري، `cap_drop`، الحجم `:ro`، الحدود، و`app` ليس على الشبكة) | ✅ |
| INC1‑AC‑9: داخل الحاوية (لا شبكة خارجية، لا كتابة، `id -u` ≠ 0، المسار `../`) | حاويات (اختياري) | `test_ele_parse_containers.py` بـ`RUN_ELE_PARSE_CONTAINER_TEST=1` على الصورة `:dev` ثم على صورة مبنيّة من الرأس: «16 passed» في الحالتين | ✅ |
| INC1‑AC‑9: حدّ الذاكرة والمهلة والمسار والرابط الرمزي وBoundedSemaphore | وحدة + **QA** | `test_ele_parse_service.py`؛ **جديد** `test_electrical_qa_pr_c.py::test_a_hostile_body_is_a_400_with_a_code_never_a_dropped_connection` (15 مدخلاً عدائياً)، `::test_a_page_range_with_huge_or_odd_numbers_is_a_422_bad_range`، `::test_a_symlink_to_a_file_outside_the_root_and_a_directory_symlink_are_both_refused` | ✅ |
| INC1‑AC‑10: مرحلة 1 لكل مسار (حذف، استبدال بالاسم، تسمية، وسم) معاملة واحدة، واستبعاد `purging` من القراءات، والتكرار 0 | وحدة | `test_electrical_purge.py::test_a_file_purge_marks_its_documents_cancels_their_jobs_and_publishes_in_one_unit`، `::test_a_file_replaced_by_rename_or_retag_is_purged_the_same_way`، `::test_the_reads_stop_seeing_a_purging_document_at_once`، `::test_a_second_purge_returns_zero_and_writes_no_event`، `::test_a_failing_outbox_rolls_the_whole_phase_back` | ✅ |
| INC1‑AC‑10: وثيقة مستبعدة تُطهَّر ويزول سببها (إصلاح `mark_purging`) | وحدة + حيّ + **QA حيّ** | `test_electrical_drawings_domain.py::test_purging_an_excluded_document_clears_its_exclusion_reason`؛ `test_electrical_purge.py::test_an_excluded_document_is_purged_too_and_loses_its_reason`؛ حيّ `test_electrical_drawings_repository_rls.py::test_an_excluded_document_can_still_be_purged`؛ **جديد** `test_electrical_qa_live.py::test_a_file_purge_over_an_excluded_a_running_and_an_awaiting_document` (SQL حقيقي ومنفذ outbox حقيقي والكنس) | ✅ |
| INC1‑AC‑10: حذف الفضاء يطهّر وثائقه ومشروعه، وتطهير مساحة العمل يشمل `electrical` | وحدة + حيّ + **QA حيّ** | `test_electrical_purge.py::test_a_space_purge_takes_every_document_and_the_project`، `test_space_deletion.py`، `test_file_deletion.py`، `test_ops_purge.py`؛ حيّ: `test_space_cascade_live.py`، `test_file_cascade_live.py`؛ **جديد** `test_electrical_qa_live.py::test_a_space_purge_takes_every_status_and_the_project_in_one_unit` | ✅ |
| INC1‑AC‑10: الكنس (المرحلة 2) والتكرار المثالي | وحدة + حيّ | `test_electrical_purge.py::test_the_sweep_claims_then_deletes_everything_under_the_document`، `::test_a_duplicate_sweep_delivery_and_a_missing_document_delete_nothing`؛ حيّ `::test_sweep_document_deletes_in_order_only_while_purging_and_only_its_own` | ✅ |
| INC1‑AC‑11: المعاينة (`replaced` و`kept` و`content_identical`) و404 و422 ولا كتابة | وحدة + واجهة | `test_electrical_registration.py::test_the_preview_lists_replaced_files_their_documents_and_the_kept_namesakes`، `::test_content_identical_has_its_three_states`، `::test_the_preview_of_an_unknown_file_is_a_404_and_a_bad_checksum_a_422`؛ `test_api_electrical_router.py::test_the_preview_lists_what_would_be_replaced_and_kept`، `::test_the_preview_of_an_unknown_file_is_404_and_a_bad_checksum_422_without_echo` | ✅ |
| INC1‑AC‑12: `stream.electrical` والمجموعة بأنواعها الثلاثة والمجاري الخمسة ومعالجات العامل | عقد | `test_stream_topology.py` (10)، `test_electrical_event_mapping.py` (8)، `test_electrical_worker.py` (10)، `test_notifications.py` | ✅ |
| INC1‑AC‑12: المخطط والسلسلة الثالثة عشرة والمنح وRLS على الجداول السبعة | معماري + حيّ | `test_electrical_tables.py` (4)، `test_ops_provision.py`؛ حيّ: `test_electrical_drawings_repository_rls.py::test_another_tenant_sees_nothing_and_cannot_write_in_every_table[7 جداول]`، `::test_row_level_security_is_forced_even_for_the_table_owner`، `::test_a_forged_workspace_on_add_is_refused_by_the_with_check` | ✅ |
| INC1‑AC‑12: الرموز الخمسة عشر، والمفاتيح الأحد عشر و`.env.example` بلا سرّ، والعقود التسعة | عقد | `test_error_catalog.py` (آلي)، `test_electrical_settings.py` (38 حالة)، `test_compose_electrical_profile.py::test_every_electrical_setting_key_is_named_in_env_example`، `lint-imports` «9 kept» بلا `ignore_imports` جديد (`git diff dc4289f...HEAD -- .importlinter` فارغ) | ✅ |
| INC1‑AC‑12: `ELECTRICAL_ENABLED=false` ← 503 لكل مسار، قبل RBAC وبعد المصادقة | واجهة + **QA** | `test_api_electrical_router.py::test_every_operation_answers_503_while_the_flag_is_off_even_to_a_caller_without_permission`، `::test_an_anonymous_caller_is_refused_before_the_flag_is_mentioned`، `::test_an_unwired_bundle_fails_closed_not_empty`؛ `test_api_app_shell.py` | ✅ |
| INC1‑AC‑12: شكل RFC 9457 لكل خطأ | **QA** | `test_electrical_qa_pr_c.py::test_every_error_of_the_surface_is_a_rfc_9457_problem_with_its_own_status` (15 طلباً: 404/409/422 وغيرها) | ✅ |
| INC1‑AC‑13 (حصة PR‑C): سجل التوصيف بكل حقول FR‑EVAL‑14، والتجميع، والمستبعد خارجه | وحدة | `test_electrical_characterization.py` (5)، `test_electrical_profile_detection.py::test_a_document_is_characterized_from_what_stg_01_holds`، `::test_a_report_aggregates_documents_and_lists_every_version_seen` | ✅ |
| INC1‑AC‑13: الأداة `app.ops.electrical_characterize` = ما يحسبه العامل بايتاً ببايت، بلا شبكة ولا قاعدة | وحدة | `test_ops_electrical_characterize.py::test_the_tool_computes_the_record_the_worker_stores`، `::test_the_same_inputs_are_the_same_bytes_and_names_only_on_request`، `::test_the_tool_needs_no_network_and_no_database` | ✅ |
| INC1‑AC‑13: M‑32 على ملفات فحص اصطناعية = القيمة المحسوبة يدوياً (لكل حقل وللاثنين، وبحسب المورّد واللغة) | وحدة | `test_electrical_m32.py::test_m32_is_the_hand_counted_share_of_documents_right_on_both_fields`، `::test_each_field_is_measured_alone_and_by_vendor_and_language`، `::test_the_measure_runs_the_workers_detection_not_a_copy` | ✅ |
| INC1‑AC‑13: النص غير الموثوق في التقارير (R1‑4) | وحدة + **QA** | `test_untrusted_text.py`، `test_ops_electrical_characterize.py::test_what_a_pdf_says_about_itself_is_written_escaped`، `test_api_electrical_router.py::test_the_pdfs_own_metadata_arrives_escaped` | ✅ |
| INC1‑AC‑14: `AizzakElectricalQueueWaitHigh` و`AizzakElectricalDocumentsExcluded` وصمت القاعدة العامة عن `stream.electrical` | قواعد | `deploy/prometheus/test-rules.sh` («31 rules found»، `test-rules: OK`)، `test_prometheus_alert_rules.py::test_every_rule_opens_its_own_runbook_section`، `::test_every_rule_is_fired_and_held_silent_by_the_promtool_suite` | ✅ |
| INC1‑AC‑14: السجلات تحمل `job_id` و`document_id` و`page` | وحدة | `test_electrical_inspection.py` (`caplog` على `electrical.inspect.page_failed`) | ✅ |
| INC1‑AC‑16 (حصة PR‑C): البوابات الست خضراء؛ لا سطر في `rag_agent` ولا `knowledge`؛ الاختبارات القائمة بلا تعديل إلا اتساع التواقيع | بوابات | §2. `git diff dc4289f...HEAD -- src/app/agents/rag_agent src/app/modules/knowledge` = 0 سطر. وراجعتُ كل سطر محذوف من اختبار قائم (≈90 سطراً في 25 ملفاً): كلها اتساع (مشارك `electrical`، عدّاد خدمات ومجارٍ، `drawings=`) أو الاستبدال المقرّ لـQ‑6 وR2‑2 (`test_a_combining_mark_makes_no_identifier_documented_deviation`، و`+12<ZWSP>v` من `extra_voltage_labels`)، ولا اختبار حُذف دون بديل أقوى | ✅ |

لا معيار بلا تغطية. `INC1‑AC‑15` في `PR‑D`.

### 1.1 المتابعات المحمولة

| البند | الاختبار | الحالة |
|---|---|---|
| `INC1‑Q‑5` (تسمية الجهد من `+` تشترط `V`) | `test_electrical_shared_rework2.py::test_a_plus_label_requires_the_v_unit`؛ وأجريتُ `+1`/`-1`/`+24V`/`+5V`/`＋24Ｖ`: `+1` موقع (لا جهد)، `+24V` جهد | ✅ |
| `INC1‑Q‑6` (غير ASCII بعد التطبيع ← `valid=False` و`text.non_ascii`) | `test_electrical_shared_rework2.py::test_non_ascii_leftovers_are_flagged_per_kind`، `::test_a_flagged_non_ascii_token_is_no_evidence_for_a_convention`؛ أجريتُ `КМ1` و`ΚΜ1` و`ǸM1` ← `None` (التفسير المقرّ)، و`ḰM1` و`-É5` ← `text.non_ascii` | ✅ |
| R2‑2 (تسمية جهد من `+`/`-` وحدها أو فيها محرف خفي) | `::test_a_label_with_a_hidden_character_is_rejected_not_stripped`؛ `+` و`-` و`++` ← `None`، و`+<ZWSP>24V` ← `valid=False` `text.hidden_character` | ✅ |
| S‑8 (PR‑A): سطر `file.replacement.deferred` مع الفاعل | `test_file_replacement.py:848` و`:872` | ✅ |
| S‑8 (PR‑B): لا صدى في `invalid_profile` | `test_api_electrical_router.py::test_an_invalid_profile_is_a_422_that_names_the_field_and_never_echoes_the_client` (يشمل RLO وZWSP و`<script>` و50 حرفاً)؛ **QA** `::test_a_profile_of_a_megabyte_is_refused_without_being_echoed` | ✅ |
| R1‑4 (`escape_untrusted_text`) | `test_untrusted_text.py` (3)، وموضعا الاستعمال في الواجهة والأداة مختبران أعلاه | ✅ |
| إصلاح `mark_purging` | وحدة + حيّ + QA حيّ (أعلاه) | ✅ |
| عمود `detection` | حيّ `::test_detection_round_trips_and_its_size_check_rejects_an_oversized_value`؛ `test_api_electrical_router.py::test_after_stg01_the_job_awaits_confirmation_and_the_proposal_carries_its_evidence`، `::test_a_proposal_that_stored_no_detection_answers_empty_maps` | ✅ |
| `INC1‑R‑16` (لا قبول آلي عند 0.90 الافتراضي) | `test_electrical_profile_detection.py::test_inc1_r16_the_default_threshold_is_unreachable_until_the_samples_tune_it`؛ والمسار الآلي مختبر بعتبة 0.5 (`test_electrical_inspection.py::test_a_confident_proposal_succeeds_and_a_doubtful_one_awaits_confirmation`) | ✅ |

## 2. نتائج بوابات المشروع

على الرأس `60b75f2` مع اختباري QA غير المودَعين بعد (ملف الوحدة 91 حالة منها xfail واحد، وملف الحيّ 4 منها xfail واحد).

```
$ .venv/bin/ruff format --check .
981 files already formatted
$ .venv/bin/ruff check .
All checks passed!
$ .venv/bin/mypy src
Success: no issues found in 547 source files
$ .venv/bin/lint-imports
Infrastructure imported only by Composition Root KEPT
Framework does not import outer layers KEPT
WireTwin sections are independent and shared is the floor KEPT

Contracts: 9 kept, 0 broken.
$ .venv/bin/pytest -rs tests/unit tests/architecture tests/eval      # التشغيل 1
SKIPPED [1] tests/unit/test_public_file_links_containers.py:971: opt-in container harness: set RUN_EDGE_FILES_TEST=1 (about a minute)
8077 passed, 35 skipped, 1 xfailed, 7 warnings in 257.93s (0:04:17)
exit=0
$ .venv/bin/pytest -rs tests/unit tests/architecture tests/eval      # التشغيل 2
8077 passed, 35 skipped, 1 xfailed, 7 warnings in 256.42s (0:04:16)
exit=0
$ deploy/prometheus/test-rules.sh
Checking /etc/prometheus/alerts.yml
  SUCCESS: 31 rules found
test-rules: OK
```

ملاحظات: (1) التشغيل على الرأس نفسه قبل إضافة اختباراتي أعطى «7987 passed, 35 skipped» رمز 0، مطابقاً لما أبلغه المنسّق. (2) الـ35 تخطياً كلها اختيارية بعلم صريح: `RUN_EDGE_FILES_TEST=1` (17 سطراً) و`RUN_ELE_PARSE_CONTAINER_TEST=1` (12 سطراً)، وشغّلتُ الثانية يدوياً في §6 («16 passed»)؛ ولا مرشّح أضفتُه. الـxfail الوحيد هو QA‑1. التشغيلان متطابقان (8077 = 7987 + 90 جديدة + xfail واحد) بلا تذبذب. (3) `tests/integration/` لم تُشغَّل على المكدّس الحيّ؛ شُغّلت على حاويات مؤقتة (§6): RLS+الابتلاع «41 passed»، و`read_chunks` «3 passed»، والتتالي والرفع المجزأ وRLS الملفات «74 passed»، واختباراتي الحيّة «3 passed, 1 xfailed».

## 3. الاختبار الاستكشافي

| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| جسم HTTP عدائي لـ`ele-parse` | 15 جسماً: `[`×3000، كائنات متداخلة، بايتات غير UTF‑8، `NaN`، `[]`، `null`، فارغ، اسم بسطر جديد أو NUL أو UUID كبير أو أرقام عربية‑هندية أو `../` أو مطلق أو `file://` | 400 برمز، لا انقطاع اتصال | 400 `bad_request`/`bad_path` في الكل (اختبار بارامتري) | ✅ |
| أرقام صفحات عدائية | `(0,1)`، `(-1,5)`، `(1,10**9)`، `True`، `1.5`، `"1"` | 422 `bad_range` | كما المتوقع | ✅ |
| رابط رمزي | رابط ملف خارج الجذر، ورابط مجلد خارج الجذر | 400 `bad_path` | كما المتوقع | ✅ |
| PDF بمحتوى تالف جزئياً | صفحة بتدفق محتوى فيه عوامل سليمة ثم رموز عشوائية؛ `summary` ثم `pages` عبر الطفل الحقيقي وعبر صورة الحاوية (`python -m services.ele_parse inspect`) | الصفحة تُفحص (تحذير MuPDF لا يمنع) | `summary` ينجح؛ `pages` ← `500 parse_error: the child sent no answer` وفي الحاوية «parse_error: the child sent no answer». السبب: PyMuPDF يطبع «MuPDF error: …» على stdout الطفل فيفسد JSON | 🔴 QA‑1 |
| قنبلة ضغط (تدفق محتوى 400 MiB يُضغط إلى 0.4 MiB) تحت حدّ 256 MiB | `run_child` عبر `summary` و`pages` | 507 `memory_limit` للصفحة | `summary` ينجح (لا يفك الضغط)؛ `pages` ← `500 parse_error` (السبب نفسه QA‑1: ضجيج MuPDF)، فيُوسَم الفشل `parse_error` لا `memory_limit` | 🔴 يتبع QA‑1 |
| ملفات تالفة: مقتطعة، رأس فقط، أصفار، فارغ | `summary` عبر الطفل | 422 `corrupt`/`not_pdf`، أو نجاح للمقتطع | مقتطع ← يقرأ 3 صفحات، رأس فقط ← `corrupt`، أصفار وفارغ ← `not_pdf` | ✅ |
| اتصال `ele-parse` بطيء | 50 اتصالاً يرسل ترويسة `Content-Length: 100` ثم يصمت | يُغلق الخادم بعد مهلة | `_Handler.timeout = None`: 50 خيطاً معلّقاً بلا نهاية | ⚠️ QA‑2 (minor، الشبكة داخلية) |
| آلة الحالة كاملة | 7 حالات × 8 انتقالات، ومنها العقد المنتهي | كما جدول LLD 3.3.6؛ الرفض لا يغيّر الحالة ولا `attempt` | كما المتوقع | ✅ |
| إلغاء أثناء انتظار تأكيد | `awaiting_confirmation` ← cancel، ثم confirm وreprocess وresume ووضع ملف | مخرج واحد على الأقل | `confirm` ← 409 `job_conflict` («لا مهمة تنتظر»)، `reprocess` ← 409 `profile_unconfirmed` («تنتظر التأكيد»)، والمخرجان `resume` (يعيد الكشف) و`PUT profile` (يثبّت) | ⚠️ QA‑4 (minor: رسالتان متناقضتان) |
| إلغاء مهمة جارية انتهى عقدها | عقد منتهٍ ثم cancel ثم رسالة معاد تسليمها | الطلب يُحترم عند الاستحواذ | الحالة `running` مع طلب إلغاء، ثم تصير `cancelled` بلا قراءة PDF | ✅ |
| رسالة معاد تسليمها لمهمة ملغاة | cancel ثم `inspector.run` | لا ele‑parse ولا أحداث | كما المتوقع | ✅ |
| سباق الإلغاء والاستحواذ (SQL حقيقي، 20 جولة) | `gather(claim, cancel)` | لا إلغاء ضائع | في كل الجولات `running` مع `cancel_requested_at` (الاستحواذ يسبق عملياً) | ✅ (الحراسة سليمة؛ التداخل الفعلي نادر) |
| كنس الحذف على SQL حقيقي | وثيقة مستبعدة، وأخرى بمهمة جارية، وأخرى بمهمة تنتظر تأكيداً؛ تطهير الملف ثلاثاً ثم مرة ثانية ثم الكنس | الاستبعاد يزول سببه، المهمتان `cancelled` بـ`electrical.document_not_live`، 3+2 حدثاً، التكرار 0، الكنس يفرغ الجداول الخمسة | كما المتوقع | ✅ |
| حذف الفضاء | وثيقتان (نشطة ومستبعدة) ومشروع، ومشروع ثانٍ لفضاء آخر في المستأجر نفسه | يُطهَّر الأول وحده، والتكرار 0 | كما المتوقع | ✅ |
| مستأجر آخر (المزيّفات) | تسع عمليات بهوية مستأجر غريب (get/pages/profile/job/cancel/resume/reprocess/confirm/register) | 404 كلها، ولا تغيير | 404 كلها | ✅ |
| مستأجر آخر (SQL) | 7 جداول، وكنس مستأجر غريب | صفر صفوف و`42501` | «41 passed» حيّاً | ✅ |
| معرّف ليس UUID على SQL حقيقي | `repos.documents.get(ctx, "not-a-uuid")` (وكذلك المهام والمشاريع والملف) | `None` (404) | `AppError common.internal` (500) | 🔴 QA‑3 (minor) |
| Unicode وعربي | اسم مشروع «مشروع المصنع ٧ — لوحة التحكم» ووصف عربي | يُحفظ كما هو | ذهاب وإياب سليم | ✅ |
| محارف خفية وطويلة | اسم بـ10,000 محرف، `\x00`، RLO، ZWSP | 422 بشكل problem+json بلا صدى | كما المتوقع | ✅ |
| ملف إعداد بحجم ميغابايت | تعبير سلكي بمليون محرف | 413/422 بلا صدى | كما المتوقع | ✅ |
| وثيقة مستبعدة | reprocess وconfirm وresume | 409 برمز `electrical.*` | كما المتوقع | ✅ |
| شكل RFC 9457 | 15 خطأً من 404/409/422 | `type,title,status,detail,code`، و`status` = رمز الاستجابة | كما المتوقع | ✅ |
| `electrical.disabled` | العلم مطفأ: مستدعٍ بلا صلاحية، ومجهول | 503 للمصادَق عليه (قبل RBAC)، 401 للمجهول | كما المتوقع (اختبارات الموجّه) | ✅ |
| ضغط مزدوج | تسجيل الملف نفسه مرتين (مفتاح تكرار، ومتزامنان على SQL) | واحد ينجح | كما المتوقع | ✅ |
| حاوية `ele-parse` | صورة `:dev` الموجودة ثم صورة مبنيّة من الرأس (`:qa`) بحاويات `--internal` مؤقتة | 16 اختباراً | «16 passed» في الحالتين | ✅ |
| قواعد التنبيه | `test-rules.sh` | 31 قاعدة، حالتا كل واحدة | `test-rules: OK` | ✅ |
| RTL/375px | — | — | لا واجهة في هذا المستودع | غير منطبق |

## 4. الأخطاء المكتشفة

| # | الوصف | الشدة | خطوات إعادة الإنتاج | المالك | الحالة |
|---|---|---|---|---|---|
| **QA‑1** | PyMuPDF يطبع «MuPDF error: …» على **stdout** الطفل، وهو قناة الجواب JSON. فأي صفحة يصدر عنها تحذير بنية (تدفق محتوى فيه خلل، وهو شائع في ملفات CAD) تفشل بـ`500 parse_error` («the child sent no answer») ويُسجَّل سببها `parse_error` بدل أن تُفحص؛ وقنبلة الضغط تُوسَم `parse_error` لا `memory_limit`. الصفحة وحدها تفشل وبقية الوثيقة تكمل، لكن فقد الصفحة صامت وغير قابل للإصلاح من المستخدم. | major | `PYTHONPATH=. .venv/bin/pytest tests/unit/test_electrical_qa_pr_c.py -k damaged_content` (xfail strict). يدوياً: `doc.update_stream(xref, b"BT /helv 12 Tf 40 700 Td (hi) Tj ET\n)))) <<<< garbage ] ] 1 2 3 foo bar", compress=False)` ثم `echo '{"op":"pages","path":P,"first":1,"last":1,"token_sample":5}' \| python -m services.ele_parse.child --memory-mb 512 --cpu-seconds 20` ← stdout يبدأ بـ`MuPDF error: syntax error` لا بـ`{`. المتوقع: `{"ok":true,...}`. العلاج المقترح: أن يكتب الطفل جوابه على نسخة محفوظة من stdout الحقيقي ويحوّل `sys.stdout`/الواصف 1 إلى stderr (أو `pymupdf.TOOLS.mupdf_display_errors(False)`) قبل فتح الملف. `services/ele_parse/child.py:67-76` (الكتابة) و`sandbox.py:_answer` (يقرأ stdout كله). | backend-developer | مفتوح |
| QA‑2 | خادم `ele-parse` بلا مهلة مقبس: عميل يعلن `Content-Length` ولا يرسل الجسم يترك خيطاً معلّقاً للأبد (`daemon_threads` بلا سقف للخيوط قبل الإشارة `children`). الشبكة داخلية والمستدعي الوحيد العامل، فالأثر ضئيل. | minor | `PYTHONPATH=. python` يشغّل `make_server` ثم 50 اتصالاً بترويسة `Content-Length: 100` وجسم `{`: `_Handler.timeout is None` و50 خيطاً معلّقاً. `services/ele_parse/app.py` (`_Handler` بلا `timeout`). المقترح: `timeout = 30`. | backend-developer | مفتوح |
| QA‑3 | معرّف مسار ليس UUID (`GET /api/v1/electrical/documents/nope`، أو `file_id` في الجسم) يصل عمود `uuid` فيرفضه asyncpg ويحوّله المحوّل إلى `AppError(common.internal)` أي 500 بدل 404. يُطلق تنبيه معدّل الأخطاء بيد أي مستدعٍ مصادَق. النمط نفسه في وحدة `files` (سابق للفرع)، ولا يحمي `Id` المشترك في `openapi.yaml` من الشكل. | minor | تشغيل حيّ: `pytest tests/integration/test_electrical_qa_live.py::test_an_id_that_is_not_a_uuid_is_simply_not_found` (xfail strict) ← `repos.documents.get(ctx, "not-a-uuid")` يرفع `AppError`. `src/app/modules/electrical/drawings/adapters/sql_repository.py:303-308` (`_translate`). المقترح: إرجاع `None` من الجلب عند `22P02`/`DataError`، أو تحقق UUID في الموجّه. | backend-developer (والقرار قد يكون عاماً لا خاصاً بالفرع) | مفتوح |
| QA‑4 | بعد إلغاء مهمة `awaiting_confirmation` تبقى الوثيقة `proposed`، فيقول `reprocessing` «الملف الشخصي ينتظر التأكيد» (`profile_unconfirmed`) بينما يقول `profile-confirmation` «لا مهمة تنتظر التأكيد» (`job_conflict`). المخرجان موجودان (`resume` و`PUT profile`) لكن الرسالتين لا تدلان عليهما. | minor | `test_electrical_qa_pr_c.py::test_cancelling_a_proposal_leaves_two_ways_out_and_resume_is_one_of_them` (يثبّت السلوك الحالي ويمرّ). `application/documents.py:414-417`. المقترح: رسالة تذكر «استأنف المهمة أو ضع ملفاً شخصياً». | backend-developer | مفتوح |

لم أجد خطأً في: مصفوفة الانتقالات، والتتالي على SQL، وعزل المستأجر، وتدفقات التسجيل والإلغاء والاستئناف، وشكل RFC 9457، وبوابة `electrical.disabled`، وقواعد التنبيه، وعزل الحاوية. ملاحظة غير خطأ: حقول الـDTO الزائدة تُتجاهل (لا `extra=forbid` إلا في الوسوم)، وهو نمط المستودع.

## 5. الحكم

**FAIL** — البوابات الست خضراء (8077 ناجحاً في تشغيلين بلا تذبذب، و`test-rules: OK`) وكل معيار في `INC1‑AC‑5..14` وحصتا `AC‑13` و`AC‑16` له اختبار ناجح، لكن **QA‑1 (major)**: صفحة يطبع MuPDF تحذيراً عنها تفشل بـ`parse_error` لأن الطفل يخلط التحذيرات بجوابه JSON؛ علاجه سطور قليلة في `services/ele_parse/child.py`. أربعة أخطاء مفتوحة: واحد major (backend)، وثلاثة minor (backend). لا خطأ للبيانات (data-engineer) ولا للبنية (devops-engineer). عند إغلاق QA‑1 يُحذف `xfail(strict)` من اختبار `test_a_page_with_a_damaged_content_stream_is_still_inspected` (سيحمرّ بنفسه إشارةً لذلك) ويكفي تشغيل `tests/unit/test_electrical_qa_pr_c.py` وبوابات 1..5.

## 6. ما أضافته QA وكيف شُغِّل الحيّ

**الاختبارات (اختبارات فقط، لا شيفرة منتج):**
- `tests/unit/test_electrical_qa_pr_c.py` (91 حالة، xfail strict واحد لـQA‑1): مصفوفة الانتقالات، وتتابعات الإلغاء/العقد/إعادة التسليم، وجدار المستأجر في المزيّفات، وأجسام `ele-parse` العدائية، وPDF بمحتوى تالف عبر الطفل الحقيقي، وسطح HTTP (RFC 9457، عربي، محارف خفية، ملف إعداد ضخم، العدّاد عند الإعادة، وثيقة مستبعدة).
- `tests/integration/test_electrical_qa_live.py` (4 حالات، xfail strict واحد لـQA‑3): تطهير ملف فيه وثيقة مستبعدة وجارية وتنتظر، وتطهير فضاء، وسباق الإلغاء والاستحواذ، على SQL حقيقي ومنفذ outbox حقيقي.

**الحاويات المؤقتة** (لم يُلمس 15432 ولا 16379/16380 ولا 16333 ولا 19000 ولا 18200؛ المنافذ العشوائية على 127.0.0.1 كانت 44621 و44622 و44623؛ كلها `--rm` وبلا حجم مسمّى، وأُزيلت بعد الاختبار، وبقيت حاويات المكدّس الحيّ الثماني عشرة كما كانت):

```
docker run -d --rm --name qa-prc-pg --tmpfs /var/lib/postgresql/data:rw,size=512m -p 127.0.0.1::5432 \
  -e POSTGRES_USER=postgres -e POSTGRES_DB=aizzak -e POSTGRES_PASSWORD=<عشوائي> \
  -e AIZZAK_OWNER_PASSWORD=<عشوائي> -e APP_RW_PASSWORD=<عشوائي> ... (ثماني كلمات مرور عشوائية) \
  -v deploy/postgres/initdb/10-roles.sh:/docker-entrypoint-initdb.d/10-roles.sh:ro \
  -v deploy/postgres/testdb/20-test-database.sh:/docker-entrypoint-initdb.d/20-test-database.sh:ro postgres:16
docker run -d --rm --name qa-prc-redis -p 127.0.0.1::6379 redis:7
docker run -d --rm --name qa-prc-minio --tmpfs /data:rw,size=512m -p 127.0.0.1::9000 \
  -e MINIO_ROOT_USER=aizzak_test -e MINIO_ROOT_PASSWORD=<عشوائي> \
  minio/minio:RELEASE.2025-04-22T22-12-26Z server /data
# الحاوية الدلو aizzak-test بعميل minio (python) ثم:
REQUIRE_LIVE=1 TEST_DATABASE_URL*=... (سبعة DSN) TEST_REDIS_URL=... TEST_CACHE_REDIS_URL=... \
  TEST_MINIO_*=... TEST_QDRANT_URL/TEST_VAULT_ADDR/TEST_EMBEDDING_URL/TEST_OLLAMA_BASE_URL=http://127.0.0.1:9 \
  .venv/bin/pytest tests/integration/test_electrical_drawings_repository_rls.py \
     tests/integration/test_electrical_ingestion_live.py   # 41 passed
  .venv/bin/pytest tests/integration/test_minio_storage.py -k read_chunks   # 3 passed
  .venv/bin/pytest tests/integration/test_file_cascade_live.py test_space_cascade_live.py \
     test_files_multipart_live.py test_files_repository_rls.py   # 74 passed
  .venv/bin/pytest tests/integration/test_electrical_qa_live.py   # 3 passed, 1 xfailed
# ثم docker stop للثلاث (--rm يزيلها) وحذف ملف البيئة المؤقت
docker build -q -f services/ele_parse/Dockerfile -t aizzak/ele-parse:qa .
ELE_PARSE_TEST_IMAGE=aizzak/ele-parse:qa RUN_ELE_PARSE_CONTAINER_TEST=1 .venv/bin/pytest tests/unit/test_ele_parse_containers.py   # 16 passed
docker rmi aizzak/ele-parse:qa
```

ملاحظة أمان: مجموعة `tests/integration/conftest.py` تقتطع الجداول في كل اختبار (`truncate_tables` تلقائي) وتعيد بناء المخطط، وافتراضات DSN تشير إلى المكدّس الحيّ؛ فلم يُشغَّل شيء منها قبل ضبط كل المتغيّرات السبعة والـRedis والـMinIO على المنافذ المؤقتة، وفحصتُ قبل أول تشغيل أن `TEST_DATABASE_URL` لا يحوي 15432 ولا 16379 (والمنافذ المؤقتة عشوائية بالبناء).

---

## 7. إعادة 1 (بعد جولة الإصلاح الأولى، الرأس `f550e4a`)

### 7.1 إغلاق الأخطاء

| # | الإصلاح | دليل الإغلاق | الحالة |
|---|---|---|---|
| QA‑1 (major) | `196a151`: يحفظ الطفل الواصف 1 على واصف خاص ويوجّه 1 إلى `/dev/null` قبل استيراد PyMuPDF، و`mupdf_display_errors(False)`، وفشل التخصيص ⇒ `507 memory_limit` | اختبار التكرار (كان xfail) يمرّ. جرّبتُ خمسة أنواع تلف: تدفق محتوى فاسد، Flate مكسور، خط/موارد مفقودة، `Resources` يشير إلى كائن غير موجود، `Annots` بمرجع مفقود، كل واحد بالعمليتين `pages` و`summary` ونطاق 1..3 ثم 2..2 ثم 3..3: في كل الحالات 3 صفحات (أو صفحة واحدة للنطاق)، وstdout يبدأ بـ`{` وstderr فارغ. قنبلة الضغط في صفحة 2: النطاق «1..1» ينجح و«2..2» يعطي `507 memory_limit` (إضافة: `test_a_bomb_on_page_two_does_not_hurt_a_range_that_is_only_page_one`). قنبلة صورة (600 م.ب. Flate) لا تُفكّ أصلاً: `OK`. طفل يموت قبل الكتابة (JSON فاسد، `{}`): الخروج 4، stdout فارغ ⇒ `500 parse_error` نظيف لا جواب ناقص؛ stdout مغلق من الأب: الخروج 4 بلا تعليق. حاوية `--read-only --cap-drop ALL --user 10001`: `/dev/null` متاح، واختبار جديد في `test_ele_parse_containers.py` (PDF بتدفق تالف عبر HTTP) ⇒ 200 وصفحتان. «false positive» لـ`is_allocation_failure`: النمط يطابق `...alloc... failed` في أي `RuntimeError` (مثلاً اسم ملف فيه الكلمتان)، وأثره 507 بدل 500 فقط، فلا أراه خطأً. | مغلق |
| QA‑2 (minor) | `e70fcbb`: `_Handler.timeout = 30` | الاختبار يمرّ (الاتصال النصف‑مفتوح يُغلَق). ملاحظة: المهلة لكل عملية قراءة، فعميل يرسل بايتاً كل 20 ث يبقى (slow‑drip)؛ الشبكة داخلية والأثر ضئيل، لا خطأ جديد. | مغلق |
| QA‑3 (minor) | `865d3c1`: `_is_uuid` في محوّل SQL للكهرباء | حالة التكرار الأصلية (`"nope"`, `""`, `"not-a-uuid"`) تعيد `None` دون لمس القاعدة في المستودعات الأربعة (`get`, `lock_live`, `get_by_space`, `list_by_space`)، وكل مسارات الكهرباء تمرّ على `get` محروس قبل أي استعلام آخر (مراجعة الشيفرة). **لكن الحراسة ناقصة**: انظر QA‑5. `file_id` في الجسم و`space_id` في `POST /projects` يذهبان إلى `files`/`spaces` (قرار المنسّق: متابعة منصّة) | أُغلق للحالة المبلَّغة، وفُتح QA‑5 للباقي |
| QA‑4 (minor) | `f550e4a`: نصّا 409 يذكران `resume` و`PUT profile` | اختبار التثبيت الموجود يمرّ والنصان يحتويان `resume` و`PUT /electrical/documents/{id}/profile` | مغلق |

### 7.2 أخطاء جديدة

| # | الوصف | الشدة | إعادة الإنتاج | المالك | الحالة |
|---|---|---|---|---|---|
| **QA‑5** | `_is_uuid` يعتمد على `uuid.UUID()` وهو أكثر تسامحاً من مفسّر asyncpg: يقبل شرطة سفلية وإشارة `+` ومسافة بادئة ونهاية سطر وأرقاماً عربية‑هندية (`"\u0660"*32+"----"` طولها 36)، فيصل المعرّف إلى القاعدة ويرفضه asyncpg ⇒ `DBAPIError` ⇒ 500 كما في QA‑3. جرّبتُ على PostgreSQL مؤقت (127.0.0.1:44627، أُزيلت): underscore/plus/lead‑space/trail‑nl/arabic كلها `guard=True` ثم `DBAPIError`. النمط نفسه في `decode_id_cursor` (منصّة، يشترك فيه files وconversations والكهرباء): مؤشّر (cursor) = base64 لإحدى هذه الصيغ ⇒ 500 على `GET .../documents?cursor=` و`GET /projects?cursor=`. وكذلك مؤشّر الصفحات `decode_seq_cursor` يقبل `99999999999` فيتجاوز `int32` لعمود `page_no` ⇒ `DataError` (تحقّقتُ من سلوك asyncpg، لم أشغّل المسار كاملاً). الأثر: 500 يحرّك تنبيه معدل الأخطاء بيد مستدعٍ مصادَق، دون تسريب بيانات. | minor | `pytest tests/unit/test_electrical_qa_pr_c.py -k "driver_would_refuse or id_cursor_the_driver"` (xfail strict، عشرة حالات). المقترح: `_is_uuid` = `re.fullmatch(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value)` وإعادة استعمالها في `decode_id_cursor`، ورفض `abs(seq) > 2**31-1` في مؤشّر الصفحات. `sql_repository.py:_is_uuid` (~276‑289)، `framework/pagination.py:77-92` | backend‑developer (الجزء المشترك قرار منصّة) | مفتوح |
| QA‑6 | `POST /electrical/projects` بجسم `space_id` ليس UUID يصل إلى `spaces.get_active` ومنه إلى مستودع `spaces` (`spaces.c.id == space_id`) ⇒ 500؛ ومثله `file_id` في تسجيل الوثيقة. ليس من الفرع (من مراجعة الشيفرة `modules/spaces/adapters/sql_repository.py:91-99`؛ لم يُشغَّل)، ويقع تحت قرار «متابعة منصّة» نفسه. | minor | `POST /api/v1/electrical/projects {"space_id":"nope","name":"x"}` بمصادَق مدير. | backend‑developer (منصّة) | مفتوح، متابعة |

### 7.3 ما أضفته

- `tests/unit/test_electrical_qa_pr_c.py`: تحذيرات MuPDF الأخرى (8 حالات)، قنبلة صفحة 2 مقابل نطاق صفحة 1، طفل يموت قبل الكتابة، وxfail strict لـQA‑5 (10 حالات).
- `tests/unit/test_ele_parse_containers.py`: PDF بتدفق تالف داخل الحاوية `--read-only`.

### 7.4 البوابات (الرأس `f550e4a` مع اختباراتي)

```
ruff format --check .   981 files already formatted
ruff check .            All checks passed!
mypy src                Success: no issues found in 547 source files
lint-imports            Contracts: 9 kept, 0 broken.
pytest -rs (1)          8103 passed, 713 skipped, 10 xfailed, 7 warnings in 262.97s   (exit 0)
pytest -rs (2)          8103 passed, 714 skipped, 10 xfailed, 7 warnings in 259.25s   (exit 0)
test-rules.sh           31 rules found / test-rules: OK
```

المتخطّاة: 713 في التشغيل الأول و714 في الثاني (أضفتُ بينهما اختبار الحاوية الاختياري)، وكلها حيّ/opt‑in كما في الجولة السابقة. حاوية `ele-parse` على صورة مؤقتة `aizzak/ele-parse:qa2` (أُزيلت): «17 passed». لم يُلمس المكدّس الحيّ ولا الوسم `:dev`؛ PostgreSQL المؤقت للفحص أُزيل.

### 7.5 الحكم بعد الإعادة 1

**PASS** — لا خطأ blocking أو major مفتوح، والبوابات الست خضراء، وQA‑1 (major) أُغلق بدليل على أنواع تلف متعددة وعلى الحاوية المقيّدة. يبقى مفتوحاً minor: QA‑5 (حراسة المعرّف ناقصة، تُصلَح بتعبير نمطي صارم من سطر واحد) وQA‑6 (متابعة منصّة معروفة)، لا يمنعان الدمج بتقديري لكن الأفضل إغلاق QA‑5 قبله لأن إصلاح QA‑3 ادّعى معالجة «كل معرّف ليس UUID».

---

## 8. إعادة 2 (بعد جولة الإصلاح الثانية، الرأس `7190940`)

### 8.1 إغلاق QA‑5

| البند | الدليل | الحالة |
|---|---|---|
| حالات QA‑5 العشر | `test_electrical_qa_pr_c.py` تمرّ الآن دون `xfail` (لا علامة بقيت في الملف) | مغلق |
| متغيّرات إضافية (16 نصاً غير قانوني) | أحرف كبيرة مع شرطة سفلية، أقواس معقوفة، صيغة URN، 32 رقماً بلا شرطات، 35 و37 حرفاً، بايت NUL (في الآخر وفي الأول وبعد UUID كامل)، مسافة بادئة، سطر جديد في النهاية وبعد UUID كامل (فخّ `$`)، أرقام كاملة العرض وعربية‑هندية، حرف غير ست‑عشري، نص فارغ: كلها `is_uuid_text` و`_is_uuid` = False و`decode_id_cursor` يرفض (`test_noncanonical_uuid_text_is_refused_by_every_guard`) | مغلق |
| القانوني يمرّ | حروف صغيرة وكبيرة ومختلطة وأصفار؛ ومحلّل asyncpg نفسه يقبلها (`pgproto.UUID`)؛ غير النصوص (`None`, `int`, `bytes`, `list`) False؛ مؤشّرات تالفة (فارغ، `!!!`، 5000 حرف، بايتات غير UTF‑8) ترفضها كلتا الدالتين دون استثناء غير `AppError` | مغلق |
| مؤشّر الصفحات int32 | على `SqlPageRepository.list` بجلسة مسجِّلة: `±(2^31‑1)` و0 تصل القاعدة؛ `2^31` و`2^31+1` و`99999999999` و`‑2^31` و`2^63` ⇒ `ValidationError` والجلسة لم تُستدعَ | مغلق |
| مؤشّر المحادثات bigint | `decode_seq_cursor` دون `maximum` يقبل `2^31` و`2^40` و`2^63‑1`؛ ومحوّل المحادثات لا يمرّر `maximum` (اختبار على المصدر) | مغلق |
| files/conversations/spaces… | `test_files_repository_rls.py` و`test_conversations_repository_rls.py` على PostgreSQL مؤقت: 110 نجاح مع المسارات الأخرى؛ كل `decode_id_cursor` في الأجسام الأخرى يقبل كل UUID قانوني (اختبار الحدّ أعلاه) | مغلق |

### 8.2 الفحص الاستكشافي على سلوك الجولة 2

| الحالة | الطريقة | النتيجة |
|---|---|---|
| عطل عابر ⇒ تحرير العقد ⇒ إعادة التسليم | `InspectionUnavailable` في `pages`، ثم نفس `event_id` | الأول يرفع، الوظيفة `running` وعقدها منتهٍ والدفتر فارغ؛ الثاني يكمل ويطالب بـ`e1`؛ الثالث لا أثر (`test_a_transient_fault_releases_the_lease_and_the_redelivery_completes`). والمطوّرون: التحرير بحارس `attempt` |
| تشغيل متقادم (استُولي عليه) لا يكتب عبر أي مساعد | اختبارات المطوّرين (`_fail`, `_stop_not_live`, `_exclude_duplicate`, الصفحة‑بصفحة، `_keep_lease`) راجعتها وتحقّقتُ أنها تفشل على الشيفرة السابقة (الحارس `_owned` غائب فيها) | سليم |
| إلغاء أثناء الدفعة الوحيدة؛ إلغاء ثانٍ على `awaiting_confirmation` | `test_a_cancel_asked_during_the_only_batch…` و`…second_cancel…` | الأول `cancelled` مع حدث ودفتر؛ الثاني يلغي فعلاً. سليم |
| `seed` بـ5000 صفحة وإعادة البذر | وحدة: 10 جمل كلها دون 32767 معامل؛ حيّ: `test_electrical_drawings_repository_rls.py` (يبذر 5000 على PostgreSQL مؤقت) ويعيد | سليم |
| فشل `summary` | الأسباب الثلاثة ⇒ `electrical.pdf_rejected` بنص ثابت يحوي السبب | سليم |
| PUT profile | 422 لـ`+1` و`--` و`-K5` و`KM1` و`X1:14` وملصقات بأحرف مخفية و`=+` وقيم غير قابلة للتجزئة وأحرف مخفية في `vendor_library`؛ أضفتُ 10 أجسام عدائية بعلامة `ZQXHOSTILE`: لا تظهر في أي نص استجابة؛ 200 وقانوني لـ`+24V` و`‑48v`⇒`‑48V` | سليم |
| حدود ele‑parse | جداول خطوط ضخمة، جواب أكبر من الحدّ، فيض stderr، نصوص رفض ثابتة، `O_NOFOLLOW` وارتباط رمزي ومجلّد (اختبارات المطوّرين مع تشغيل الطفل الحقيقي) + حاوية مقيّدة 18 نجاحاً | سليم |
| المُحمِّل يتوقف عند أول بايت فوق `expected_size`؛ هروب تقرير Markdown | اختبارات المطوّرين تمرّ | سليم |

### 8.3 أخطاء جديدة

لا شيء. الملاحظة الوحيدة: QA‑6 (`space_id`/`file_id` ليس UUID في جسم `POST`) ما زال مفتوحاً كمتابعة منصّة minor كما في الإعادة 1، لم يتغيّر.

### 8.4 البوابات (الرأس `7190940` مع اختباراتي)

```
ruff format --check .   983 files already formatted
ruff check .            All checks passed!
mypy src                Success: no issues found in 547 source files
lint-imports            Contracts: 9 kept, 0 broken.
pytest -rs (1)          8214 passed, 716 skipped, 7 warnings in 272.34s (0:04:32)   (exit 0)
pytest -rs (2)          8214 passed, 716 skipped, 7 warnings in 269.72s (0:04:29)   (exit 0)
test-rules.sh           SUCCESS: 31 rules found / test-rules: OK
```

المتخطّى (716) هو الحيّ/opt‑in. الصورة المؤقتة `aizzak/ele-parse:qa3` (أُزيلت؛ بقي الوسم `dev` وحده): `RUN_ELE_PARSE_CONTAINER_TEST=1` ⇒ 18 passed. الاختبارات الحيّة على حاويات مؤقتة بمنافذ عشوائية (PostgreSQL 16 وRedis 7 وMinIO، `--rm`، أُوقفت وأُزيلت): `test_electrical_drawings_repository_rls.py` + `test_electrical_ingestion_live.py` + `test_electrical_qa_live.py` + `test_minio_storage.py` = 66 passed (دون xfail)، وcascades/files/conversations = 110 passed. ضُبطت المتغيّرات السبعة للـDSN والـRedis وMinIO قبل أول تشغيل مع فحص عدم وجود 15432/16379/16380، وQdrant/Vault/Ollama/embedding على منفذ ميّت. لم يُلمس المكدّس الحيّ.

### 8.5 الحكم بعد الإعادة 2

**PASS**. كل البوابات خضراء، QA‑5 مغلق، لا خطأ blocking أو major أو جديد. يبقى QA‑6 (minor، متابعة منصّة).

</div>
