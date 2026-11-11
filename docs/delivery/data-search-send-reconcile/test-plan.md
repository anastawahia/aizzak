<div dir="rtl">

# خطة الاختبار: ث‑3 مصالحة الإرسال الغامض

- **الفرع:** `feat/data-search-send-reconcile` (من `b104878`)، الخلفية فقط.
- **الاختصارات** (الملفات تحت `tests/unit/` ما لم يُذكر غير ذلك):
  - `R` = `test_api_conversations_router.py`، `O` = `test_orchestrator.py`، `IT` = `test_api_idempotent_turn.py`.
  - `ST` = `test_idempotency_store_sql.py`، `SQL` = `test_conversations_sql_statements.py`، `UC` = `test_conversations_use_cases.py`.
  - `MIGU` = `test_message_reply_to_migration.py`، `PAR` = `test_conversations_schema_parity.py`، `C` = `test_api_conventions.py`.
  - `FR` = `test_send_reconcile_frames.py`، `WS` = `test_websocket_accepted_frames.py`، `DIS` = `test_asgi_disconnect_semantics.py`، `ERR` = `test_send_reconcile_errors.py` (الأربعة كتبها QA).
  - **CI** = اختبار حي تحت `tests/integration/`: لا يُشغَّل محلياً بقرار المالك س‑14 (المكدس الحي مستعمل)، ويُشغَّل في CI job `integration`. **لم يُشغَّل هنا** ولم يُجرَّب، وحالته «⏳ CI» لا ✅.
- ✅ = اختبار آلي محلي يمر، ⏳ CI = اختبار حي في CI فقط (مكتوب ومفحوص ستاتيكياً: ruff وmypy، ولم يُنفَّذ)، 📖 = مراجعة وثائق قام بها QA بقراءة الفرق `git diff b104878..HEAD`.

## 1. مصفوفة التغطية

| AC | النوع | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑1.1 | وحدة (ASGI) | `R::test_sse_starts_with_accepted_naming_the_stored_user_message` | ✅ |
| AC‑1.2 | وحدة | `FR::test_a_fallback_turn_orders_accepted_notice_tokens_final_with_one_accepted`، و`FR::test_a_turn_that_ends_in_error_after_the_notice_still_has_one_accepted_first` | ✅ |
| AC‑1.3 | وحدة | `FR::test_accepted_reaches_the_client_before_the_first_keepalive` (منسّق حقيقي ووكيل صامت و`sse_stream` بنبضة 20ms: أول بايتات `event: accepted` ثم `:keep-alive`) | ✅ |
| AC‑1.4 | وحدة | `O::test_final_carries_the_user_message_id_that_accepted_announced`، و`FR::test_a_streamed_turn_is_the_legacy_frames_plus_accepted_and_one_final_key`، و`FR::test_a_citing_turn_is_the_legacy_frames_plus_accepted_and_one_final_key`، و`FR::test_a_turn_that_fell_back_to_the_local_model_keeps_its_legacy_frames`. القيم «القديمة» حرفية، التُقطت بتشغيل الوكلاء نفسهم على `b104878` (worktree منفصل) لا نُسخت من الشيفرة الجديدة | ✅ |
| AC‑1.5 | وحدة | `WS::test_two_concurrent_turns_each_get_one_accepted_before_their_first_token` (دوران بالنص نفسه على مقبس واحد: لكل محادثة `accepted, token, final`، والمعرّفان مختلفان ومطابقان للمخزَّن، و`reply_to` لكل جواب هو سؤاله)، و`WS::test_accepted_is_the_very_first_frame_of_a_turn`، و`test_websocket_endpoint::test_the_requests_conversation_id_wins_the_envelope_except_for_accepted_without_one` | ✅ |
| AC‑1.6 | وحدة | `test_api_agents_router::test_a_streamed_invoke_that_opens_a_thread_announces_it_in_the_first_frame` | ✅ |
| AC‑1.7 | وحدة | 404 و422: `R::test_a_refused_request_is_a_problem_with_no_accepted_frame_and_no_user_message[unknown,invalid]`. 429: `R::test_a_quota_refusal_is_a_429_with_no_accepted_frame_and_no_user_message`. **409 (محذوفة بين القراءة والإلحاق): `ERR::test_a_thread_deleted_between_the_read_and_the_append_is_a_409_with_no_frame_or_question[True,False]` (جديد، QA)**. **403 على فرع SSE بلا مفتاح: `ERR::test_a_viewer_is_refused_with_403_before_any_stream_and_without_a_key[3 حالات]` (جديد، QA)**. المفتاح يتحرر بعد 409: `ERR::test_that_409_frees_the_key_so_the_retry_after_the_cause_is_gone_can_run` | ✅ |
| AC‑1.8 | وحدة | `O::test_without_a_conversations_seam_there_is_no_accepted_and_no_id`، و`FR::test_without_a_conversations_seam_the_frames_are_exactly_the_legacy_ones` | ✅ |
| AC‑1.9 | وحدة | `O::test_accepted_is_the_first_frame_with_exactly_three_keys`، و`O::test_accepted_carries_no_part_of_the_text`، و`FR::test_accepted_has_exactly_three_keys_and_none_of_a_long_arabic_text` (10,000 حرف عربي، على السلك بعد الترميز)، و`FR::test_the_encoded_accepted_frame_is_utf8_and_one_line` | ✅ |
| AC‑1.10 | وحدة (ASGI) | `FR::test_a_streamed_workflow_run_has_no_accepted_frame` (الإطارات مساوية لما على `b104878`: `final` فقط)، و`test_workflow_orchestration` (`reply_tos == [None, None, None]`) | ✅ |
| AC‑2.1 | وحدة + CI | `O::test_the_reply_is_stored_with_reply_to_the_user_message`، و`R::test_the_reply_records_the_question_it_answers_in_json_and_history`، و`test_api_agents_router::test_invoke_without_stream_returns_the_persisted_turn` (`message.reply_to`). حي: `RLS::test_reply_to_round_trips_and_a_user_message_holds_null` | ✅ + ⏳ CI |
| AC‑2.2 | وحدة | `O::test_a_stored_partial_replies_to_the_user_message_too[المهلة، error بعد tokens]`، و`O::test_an_abandoned_partial_replies_to_the_user_message_too` (aclose بعد token). مسار «استثناء المنسّق» يمر بالكود نفسه (`_settle_cut` ← `_persist_partial`) ولا اختبار مستقل له يؤكد `reply_to` عليه: فجوة صغيرة مقبولة، انظر §4 (ملاحظة N‑3) | ✅ |
| AC‑2.3 | وحدة + CI | `R::test_the_reply_records_the_question_it_answers_in_json_and_history` (رسالة user: المفتاح حاضر و`None`)، و`test_workflow_orchestration` (الخطوة `None`)، و`SQL::test_a_row_hydrates_reply_to`، و`UC::test_no_reply_to_stays_none`. ملخص العامل: لا اختبار مباشر (الشيفرة لا تمرر `reply_to`، والقيمة الافتراضية `None`، و`test_workers_bootstrap.py` أخضر). حي: `RLS::test_a_message_written_without_reply_to_reads_back_null`، و`MIG::test_downgrade_then_upgrade_round_trips_and_an_old_row_survives` | ✅ + ⏳ CI |
| AC‑2.4 | وحدة | `O::test_two_concurrent_turns_with_the_same_text_each_reply_to_their_own_question` | ✅ |
| AC‑2.5 | وحدة | `SQL::test_append_with_reply_to_is_one_guarded_insert_then_the_version_update`، و`SQL::test_append_without_reply_to_is_todays_plain_insert_then_update`، و`SQL::test_a_guarded_insert_keeps_citations_status_and_fallback_binds` | ✅ |
| AC‑2.6 | وحدة | `R::test_a_client_sent_reply_to_is_ignored` | ✅ |
| AC‑2.7 | وحدة + CI | وحدة: `UC::test_a_reply_to_that_is_not_an_id_is_a_validation_error_before_any_read`، و`UC::test_a_reply_to_on_a_user_message_is_a_validation_error_before_any_read`، و`UC::test_a_reply_to_naming_another_threads_or_an_assistant_message_stores_no_row`، و`test_conversations_domain::test_inv_cv6_only_assistant_messages_reply_to_a_message`، و`SQL::test_a_refused_reply_to_raises_before_any_update`. **حي (CI فقط، س‑14):** `RLS::test_reply_to_naming_another_conversations_question_is_refused`، و`RLS::test_reply_to_naming_an_assistant_message_is_refused`، و`RLS::test_reply_to_naming_another_tenants_question_is_refused` | ✅ + ⏳ CI |
| AC‑2.8 | وحدة | `R::test_deleting_the_question_keeps_the_id_on_the_answer` | ✅ |
| AC‑2.9 | وحدة | `R::test_the_reply_records_the_question_it_answers_in_json_and_history` (`reply["reply_to"] == user["id"]`) | ✅ |
| AC‑2.10 | وحدة + CI | وحدة: `MIGU::test_upgrade_adds_the_nullable_uuid_column_and_nothing_else`، و`MIGU::test_downgrade_drops_the_column_and_nothing_else`، و`MIGU::test_upgrade_is_catalog_only`، و`PAR::test_the_reply_to_column_is_spelled_the_same_everywhere`، و`PAR::test_reply_to_is_nullable_without_a_default`، و`PAR::test_the_reply_to_migration_downgrade_drops_the_column`. **حي (CI فقط، س‑14):** `MIG::test_downgrade_then_upgrade_round_trips_and_an_old_row_survives`. ترحيل `provision` نفسه لم يُشغَّل هنا (ممنوع) | ✅ + ⏳ CI |
| AC‑3.1 | وحدة | `R::test_without_a_key_the_key_store_is_never_touched[False,True]` | ✅ |
| AC‑3.2 | وحدة | `R::test_a_repeat_returns_the_same_message_and_runs_nothing` | ✅ |
| AC‑3.3 | وحدة | `R::test_the_same_key_with_other_content_is_a_conflict_and_runs_nothing` | ✅ |
| AC‑3.4 | وحدة | `R::test_a_running_turn_makes_the_repeat_a_409_and_the_first_still_completes` | ✅ |
| AC‑3.5 | وحدة | `R::test_two_keys_with_the_same_text_are_two_turns` | ✅ |
| AC‑3.6 | وحدة | `R::test_the_same_key_on_another_conversation_is_a_new_turn`، و`R::test_the_scope_uses_the_canonical_conversation_id_not_the_path_text`، و`IT::test_the_scope_is_the_endpoint_of_the_canonical_conversation_id`، و`IT::test_the_scope_is_per_conversation_and_per_workspace` | ✅ |
| AC‑3.7 | وحدة | `R::test_a_failure_before_the_question_is_stored_frees_the_key` (429)، و`R::test_an_unknown_agent_frees_the_key_too`، و`ERR::test_that_409_frees_the_key_so_the_retry_after_the_cause_is_gone_can_run` (409)، و`R::test_an_unusable_key_is_a_422_before_any_read_or_run` (422: لا مطالبة أصلاً) | ✅ |
| AC‑3.8 | وحدة | `R::test_a_failure_after_the_question_is_stored_is_a_502_with_ids_and_replays_the_same` (الرمز الفعلي في الاختبار 409 لأن الوكيل المزيّف يرمي `ConflictError`: الإعادة تعيد ما أُعطي أولاً، وهو المطلوب) | ✅ |
| AC‑3.9 | وحدة | `R::test_an_unusable_key_is_a_422_before_any_read_or_run[""، "   "، 256 حرفاً]` | ✅ |
| AC‑3.10 | وحدة | `R::test_a_stream_then_a_json_repeat_returns_the_message_not_a_conflict` | ✅ |
| AC‑3.11 | وحدة | `R::test_a_replay_after_the_answer_was_deleted_is_a_404_in_both_shapes` | ✅ |
| AC‑3.12 | وحدة | `R::test_a_replay_after_the_conversation_was_deleted_is_a_404_and_touches_nothing` | ✅ |
| AC‑3.13 | وحدة | `R::test_a_swept_key_starts_a_new_turn` | ✅ |
| AC‑3.14 | وحدة | `R::test_arabic_and_very_long_content_replay_byte_for_byte`، و`IT::test_the_hash_is_stable_for_arabic_and_never_escapes_it`، و`IT::test_the_hash_is_over_the_content_only_and_ignores_key_order` | ✅ |
| AC‑4.1 | وحدة | `R::test_a_streamed_repeat_replays_the_original_frames_without_tokens_or_calls`، و`R::test_a_replayed_final_carries_the_stored_citations`، و`IT::test_a_replayed_turn_is_exactly_accepted_then_final` | ✅ |
| AC‑4.2 | وحدة | `R::test_a_streamed_failure_replays_accepted_then_the_same_error` | ✅ |
| AC‑4.3 | وحدة | `O::test_an_abandoned_turn_settles_with_the_partial_it_managed_to_save`، و`IT::test_a_settled_write_survives_the_cancellation_of_its_awaiter`، و`R::test_an_abandoned_stream_is_replayed_as_agent_failed_with_the_question_id`، و`DIS::test_a_shielded_write_completes_after_a_disconnect_while_a_bare_await_is_cancelled[h11,httptools]` (خادم uvicorn حقيقي: الكتابة المحمية تكتمل بعد القطع، والمجردة تُلغى). فحصتُ أن الاختبار ليس فارغاً: باستبدال `_shielded` بـ`await` عادي يفشل | ✅ |
| AC‑4.4 | وحدة | `O::test_abandoning_right_after_accepted_settles_with_no_message_id`، و**`ERR::test_a_reader_who_left_right_after_accepted_gets_agent_failed_for_the_same_question[stream,json]` (جديد، QA: الإعادة بـ`user_message_id` نفسه ورسالة user واحدة)** | ✅ |
| AC‑4.5 | وحدة | `R::test_a_young_unfinished_claim_is_a_409_problem_not_a_stream`، و`R::test_a_running_turn_makes_the_repeat_a_409_and_the_first_still_completes` | ✅ |
| AC‑4.6 | وحدة + CI | `IT::test_the_threshold_is_exclusive_below_and_inclusive_at`، و`IT::test_the_stale_threshold_is_the_stream_cap_plus_120_seconds` (=720)، و`R::test_an_old_accepted_claim_is_reported_lost_with_the_question_id`، و`R::test_one_second_before_the_threshold_it_is_still_a_409`، و`R::test_an_old_claim_with_no_recorded_question_is_a_502_without_ids_or_stream`، و`ST::test_inspect_selects_the_age_on_the_database_clock`. حي (CI): `IL::test_the_claim_age_comes_from_the_database_clock` | ✅ + ⏳ CI |
| AC‑4.7 | وحدة | `IT::test_the_stored_keys_are_exactly_the_closed_list_with_no_text`، و`R::test_the_stored_handle_carries_ids_and_counts_only_after_every_path` | ✅ |
| AC‑4.8 | وحدة | `R::test_without_a_key_the_key_store_is_never_touched[True]` | ✅ |
| AC‑4.9 | وحدة | `R::test_a_json_then_a_stream_repeat_replays_two_frames` | ✅ |
| AC‑4.10 | وحدة + CI | `R::test_a_repeat_returns_the_same_message_and_runs_nothing` (`S.calls == [claim, checkpoint, complete, claim]`)، و`ST::test_checkpoint_is_one_update_that_leaves_completed_at_alone`، و`ST::test_complete_is_one_update_that_sets_completed_at`. حي (CI): `IL::test_checkpoint_keeps_the_claim_in_progress_with_its_body`، و`IL::test_a_late_checkpoint_cannot_overwrite_a_completed_body`، و`IL::test_release_does_not_delete_a_checkpointed_claim` | ✅ + ⏳ CI |
| AC‑5.1 | وحدة | `R::test_a_failure_before_any_token_has_the_question_id_and_no_message_id` (بث، مع الإعادة) | ✅ |
| AC‑5.2 | وحدة | **`ERR::test_a_json_failure_before_any_token_has_the_question_id_and_replays_the_same_problem` (جديد، QA: JSON، فشل قبل أول token، الـ problem ومعه `user_message_id` بلا `message_id`، والإعادة هي نفسها بلا تشغيل)**. وجانب «بعد token»: `R::test_a_failure_after_the_question_is_stored_is_a_502_with_ids_and_replays_the_same` | ✅ |
| AC‑5.3 | وحدة | `O::test_a_reply_that_failed_to_save_settles_agent_failed_without_message_id`، و`R::test_a_reply_that_could_not_be_saved_is_a_502_with_the_question_id_not_a_500` (JSON، مع الإعادة). جانب البث (إعادة ← `accepted` ثم `error`) تغطيه `R::test_a_streamed_failure_replays_accepted_then_the_same_error` بالآلية نفسها (`outcome=failed`) لا بحالة فشل الحفظ بعينها | ✅ |
| AC‑5.4 | وحدة | `O::test_abandoning_right_after_accepted_settles_with_no_message_id`، و`ERR::test_a_reader_who_left_right_after_accepted_gets_agent_failed_for_the_same_question[stream]` | ✅ |
| AC‑5.5 | وحدة | `R::test_a_failure_leaves_no_assistant_message_that_is_not_complete_or_partial`، و`ERR::…before_any_token…` (رسالة user وحدها) | ✅ |
| AC‑5.6 | وحدة | `R::test_a_failure_without_a_key_touches_no_store_and_leaves_the_question` | ✅ |
| AC‑6.1 | وحدة (عقد) | `C::test_the_idempotency_header_is_declared_on_exactly_the_contracts_operations` (القائم)، و`C::test_the_contract_publishes_message_reply_to_and_the_problem_extensions` (`postMessage` في الطرفين) | ✅ |
| AC‑6.2 | وحدة (عقد) | `C::test_the_contract_publishes_message_reply_to_and_the_problem_extensions` (`reply_to` بـ`format: uuid` و`nullable`، وفي `LIVE.required`، والعضوان على `Problem`) | ✅ |
| AC‑6.3 | مراجعة | 📖 `openapi.yaml`: وصف `postMessage` و`invokeAgent` يذكران `accepted` والترتيب و«Ignore unknown frame types» | ✅ |
| AC‑6.4 | مراجعة | 📖 `03 §0`: النطاق والبصمة، واستثناء «النتيجة لا الردّ الأول»، والفشل بعد القبول، والجاري 409 والضائع 720 ث، والاحتفاظ 2–3 أيام، و`runWorkflow` خارج المخزن | ✅ |
| AC‑6.5 | مراجعة | 📖 `03 §2` (`reply_to`)، و§3.1 (مثال `accepted`، و`final.user_message_id`، والعضوان، وإطارا الإعادة)، و§3.2 (WebSocket)، و§4 (الامتداد المغلق) | ✅ |
| AC‑6.6 | مراجعة | 📖 `06 §4`: `Message.reply_to` وINV‑CV6 و`GetMessage`، وجملة «فشل بلا رسالة» صُحّحت | ✅ |
| AC‑6.7 | وحدة + مراجعة | `PAR` (الأربعة)، و📖 `01 §2.4` (عمود `reply_to`) و§4.2‑ب (مقبض الدور، والاحتفاظ بعد يومين بدل «لا سياسة») | ✅ |
| AC‑6.8 | مراجعة | 📖 `gaps.md`: فقرة «الإصلاح (2026‑11‑11)» في §ث‑3، وجملة §ث‑2 أُحيلت إلى ما قُرّر، وسطر الاحتفاظ صُحّح، وتنبيه سطور `backend-reply.md` 96–97 و220 | ✅ |
| AC‑6.9 | مراجعة | 📖 `idempotency.py`: ✅ (يذكر أن `postMessage` له مسار آخر). `idempotency_store.py`: **🔴 جزئي** — السطر 6 ما زال يقول «declares the header on exactly three operations» (انظر الخطأ B‑1). المعدود فعلياً أكثر من ثلاث | 🔴 |
| AC‑7.1 | وحدة + CI | `R::test_another_workspace_cannot_reach_or_replay_the_key`، و`IT::test_the_scope_is_per_conversation_and_per_workspace`. حي (CI): اختبار RLS القائم للمفاتيح في `IL` | ✅ + ⏳ CI |
| AC‑7.2 | وحدة | `R::test_a_viewer_is_refused_and_no_row_is_created`، و`ERR::test_a_viewer_is_refused_with_403_before_any_stream_and_without_a_key` | ✅ |
| AC‑7.3 | CI فقط | `RLS::test_reply_to_is_invisible_to_another_tenant` (حي، س‑14). لا مكافئ محلي ممكن: RLS خاصية Postgres | ⏳ CI |
| AC‑7.4 | وحدة | `R::test_a_second_user_of_the_same_workspace_replays_the_first_users_turn` | ✅ |
| AC‑7.5 | وحدة | `R::test_the_new_log_lines_carry_neither_the_key_nor_the_text`، و`O::test_a_ledger_that_raises_never_fails_the_turn_and_logs_no_text`، و`test_api_errors::test_a_name_outside_the_closed_set_is_never_rendered`، و`test_api_errors::test_an_extension_never_overrides_a_core_member_and_empty_values_are_dropped`، و`IT::test_other_content_under_the_same_key_is_a_conflict_without_the_key_in_it` | ✅ |
| AC‑7.6 | وحدة | `R::test_no_or_a_bad_token_is_401_and_the_store_is_untouched` | ✅ |
| AC‑7.7 | معمارية | بوابة `lint-imports`: 9 عقود سليمة، بلا `ignore_imports` جديد (`git diff b104878..HEAD -- .importlinter` فارغ) | ✅ |

**العدّ (66 معياراً):**
- **آلي محلي فقط (✅):** 50 (US‑1: 10، US‑2: 6، US‑3: 14، US‑4: 8، US‑5: 6، AC‑6.1 وAC‑6.2، وAC‑7.2 وAC‑7.4 وAC‑7.5 وAC‑7.6).
- **آلي محلي + حي في CI (✅ + ⏳):** 7 (AC‑2.1، 2.3، 2.7، 2.10، 4.6، 4.10، 7.1).
- **CI فقط (⏳):** 1 (AC‑7.3).
- **مراجعة وثائق (📖):** 7 (AC‑6.3 إلى 6.9، وAC‑6.7 معه وحدة PAR). ستة ✅ وواحد 🔴 جزئي (AC‑6.9).
- **بوابة معمارية:** 1 (AC‑7.7، `lint-imports`).
- المجموع 50 + 7 + 1 + 7 + 1 = 66.
- **بلا تغطية: لا شيء.** كل AC له اختبار أو مراجعة. الوحيد 🔴 هو AC‑6.9 (docstring في الشيفرة، B‑1).

**الاختبارات الحية (CI فقط بقرار س‑14)، بأسمائها:**
- `tests/integration/test_conversations_repository_rls.py`: `test_reply_to_round_trips_and_a_user_message_holds_null`، `test_a_message_written_without_reply_to_reads_back_null`، `test_reply_to_naming_another_conversations_question_is_refused`، `test_reply_to_naming_an_assistant_message_is_refused`، `test_reply_to_naming_another_tenants_question_is_refused`، `test_reply_to_is_invisible_to_another_tenant` (AC‑2.1، 2.3، 2.7، 7.3).
- `tests/integration/test_message_reply_to_migration_live.py`: `test_downgrade_then_upgrade_round_trips_and_an_old_row_survives` (AC‑2.10).
- `tests/integration/test_idempotency_live.py`: `test_checkpoint_keeps_the_claim_in_progress_with_its_body`، `test_a_late_checkpoint_cannot_overwrite_a_completed_body`، `test_release_does_not_delete_a_checkpointed_claim`، `test_the_claim_age_comes_from_the_database_clock` (AC‑4.6، 4.10).

## 2. نتائج بوابات المشروع

من `/home/AIZZAK` بـ`.venv`، على الفرع بعد إضافة اختباراتي (`tests/integration` لم يُشغَّل، ولا migrations).

```
$ .venv/bin/ruff format --check .
1010 files already formatted
$ .venv/bin/ruff check .
All checks passed!
$ .venv/bin/mypy src
Success: no issues found in 552 source files
$ .venv/bin/lint-imports
Agents never import API or Infrastructure KEPT
Infrastructure imported only by Composition Root KEPT
Framework does not import outer layers KEPT
WireTwin sections are independent and shared is the floor KEPT

Contracts: 9 kept, 0 broken.
$ .venv/bin/pytest -rs tests/unit tests/architecture tests/eval      (addopts بلا تغيير)
SKIPPED [1] tests/unit/test_public_file_links_containers.py:971: opt-in container harness: set RUN_EDGE_FILES_TEST=1 (about a minute)
9123 passed, 37 skipped, 7 warnings in 293.18s (0:04:53)
exit 0
$ deploy/prometheus/test-rules.sh
  SUCCESS: 31 rules found
test-rules: OK
```

**ما تخطّته الاختبارات (`-rs`) ولماذا:** كلها 37 اختبارات «opt-in container harness» بلا خدمة حية: 17 تحتاج `RUN_EDGE_FILES_TEST=1` (`test_public_file_links_containers.py`)، والباقية تحتاج `RUN_ELE_PARSE_CONTAINER_TEST=1`. لا تخطٍّ يخص ث‑3. (الأساس عند المطوّر 9093 passed؛ الفرق 30 = اختباراتي الجديدة.) المرشِّحات: لم تُضَف أي مرشِّحات.

## 3. الاختبار الاستكشافي

أُجريت على `TestClient` فوق تطبيق حقيقي (`create_app`) ومنسّق حقيقي ومخزن مفاتيح في الذاكرة (`InMemoryIdempotencyStore` الذي ينفّذ `claim` بنتائجه الأربع)، عبر سكربتات مؤقتة خارج المستودع. لم يُشغَّل Postgres، فسلوك الأعمدة والعمر بساعة القاعدة غير مفحوص هنا (CI).

| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| إعادة أثناء الجريان (409) | `claim` مزوَّر بعمر 10 ث وبلا جواب، ثم الإرسال بالمفتاح نفسه | 409 `common.conflict` بصيغة problem، لا بث | كما المتوقع (`R::test_a_young_unfinished_claim_is_a_409_problem_not_a_stream`) وأكّدتها بقراءة الشيفرة | ✅ |
| إعادة بعد الاكتمال، JSON ثم بث | مفتاح K بلا بث ثم بالبث | `accepted` (`replayed`) ثم `final`، ولا تشغيل | إطاران؛ `final` المعاد بلا `text` وبلا `token` (مقصود، د‑ت8) | ✅ |
| إعادة بعد الاكتمال، بث ثم بث | K بالبث مرتين | `['accepted','token','final']` ثم `['accepted','final']` و`replayed: true` | كما المتوقع. ملاحظة: `final` المعاد لا يحمل المفتاح `text` الذي كان في الأصلي (انظر N‑1) | ✅ |
| نفس المفتاح على محادثة أخرى | K على C1 ثم C2 | دور جديد في C2 | دور جديد (مغطى بـ`R`) | ✅ |
| نفس المفتاح بمحتوى مختلف | K بنص ثم بنص آخر | 409 ولا شيء يُكتب | 409، ولا تشغيل | ✅ |
| محتوى بترتيب مفاتيح مختلف | `{"text","attachments"}` ثم بالعكس | replay (البصمة مرتبة) | replay بالمعرّف نفسه | ✅ |
| فشل بلا رسالة، إعادة | وكيل يفشل قبل أول token، JSON ثم إعادة | problem ومعه `user_message_id` بلا `message_id`، والإعادة مثله بلا تشغيل | كما المتوقع (`ERR` الجديد) | ✅ |
| حدّ 720 ث | عمر 719 و720 و`stale_after_s(Settings())` | 409 تحت 720، `agent.failed` عند 720 فما فوق | `stale_after_s(Settings()) == 720.0`؛ وسلوك الحدّ مغطى بـ`IT` و`R` | ✅ |
| viewer / مساحة أخرى | دور viewer؛ مساحة B بمفتاح A على محادثة A | 403 بلا صف؛ 404 بلا لمس المخزن | كما المتوقع، وفي SSE بلا مفتاح أيضاً (`ERR`) | ✅ |
| مجموعة الامتداد المغلقة | `AppError(extensions={user_message_id:"u1", message_id:"", code:"hack", status:"200", title, evil, correlation_id})` | يظهر `user_message_id` فقط، ولا يطغى شيء على الأعضاء الأساسية، و`extensions` للقراءة فقط | كما المتوقع (`problem_from_error` و`problem_from_event` و`mappingproxy`) | ✅ |
| مفتاح 255 و256 و`" "` | إرسال بكل منها | 200 و422 و422 | كما المتوقع | ✅ |
| مفتاح بمسافات حوله وبعربي | `" pad "` ثم `"pad"`؛ ترويسة بايتات UTF‑8 | مفتاحان مختلفان؛ العربي يعمل ويعيد | `" pad "` و`"pad"` صفّان منفصلان (الخادم لا يقصّ)، والعربي يُخزَّن بترميز latin‑1 للترويسة ويعيد replay بثبات | ✅ (N‑2) |
| `content` ليس كائناً / `{}` | بمفتاح | 422 / سلوك اليوم | 422 بلا صف في المخزن / 200 بجواب «echo:» (قائم قبل الميزة) | ✅ |
| صف مكتمل بجسد فاسد أو `NULL` | تلف `response_body` يدوياً ثم إعادة | 500 `common.internal` بجملة لا تسرّب شيئاً | 500 بـ«the stored outcome of this Idempotency-Key could not be read» | ✅ |
| فشل `checkpoint` | مخزن يرمي عند `checkpoint` | الدور يكمل، وتُسجَّل `turn_ledger_failed` | 200 بجواب كامل، والإعادة replay (لأن `complete` نجح) | ✅ |
| فشل `complete` بعد الاكتمال | مخزن يرمي عند `complete` (بث) | الدور يكمل؛ الإعادة 409 حتى 720 ث ثم `agent.failed` | كما وُثِّق في المخاطرة 3/4 (الرد حُفظ في السجل بـ`reply_to` لكن المفتاح يقول «جارٍ») | ⚠️ موثّق (N‑4) |
| محادثة مجهولة بمفتاح | `POST` على معرّف غير موجود | 404 قبل أي قراءة للسجل | 404، `store.calls == []` | ✅ |
| محادثة تُحذف بين القراءة والإلحاق | حذف ناعم داخل `_admit`، بث وJSON، بمفتاح وبلا | 409 problem، بلا `accepted`، بلا رسالة، والمفتاح يتحرر | كما المتوقع (`ERR` جديد) | ✅ |
| قطع العميل أثناء JSON | خادم uvicorn حقيقي، مقبس خام، إغلاق أثناء المعالج | المعالج لا يُلغى (7.1‑1) | لا يُلغى ويكتمل، على h11 وhttptools (uvicorn 0.51 / Starlette 1.3.1) | ✅ |
| قطع العميل أثناء SSE | الشيء نفسه بجسم بث | المولّد يُلغى، والكتابة المحمية تكتمل | كما المتوقع؛ والكتابة العارية تُلغى (فيثبت سبب `_shielded`) | ✅ |
| WebSocket بلا `conversation_id` | `invoke` بلا محادثة | (الغلاف يعرض `conversation_id` المفتوحة، د‑ت11) | الخادم يردّ 422 «space_id is required when no conversation_id is given» كما كان قبل الميزة: بروتوكول WS لا يحمل `space_id`، فالفرع الجديد في `_envelope_conversation_id` غير قابل للوصول من السلك اليوم (N‑5) | ⚠️ ملاحظة |
| RTL / 375px | — | — | لا واجهة في هذا المستودع (خلفية فقط). الاختبار العربي على السلك: UTF‑8 بلا escape في SSE وJSON، في `FR` وفي `R` | غير منطبق |

## 4. الأخطاء المكتشفة

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| B‑1 | **AC‑6.9 غير مكتمل:** docstring الوحدة `src/app/framework/ports/idempotency_store.py:5-6` ما زال يقول «`openapi.yaml` declares the header on exactly three operations» (والتصميم §2 يطلب إزالة «بالضبط ثلاث»). والعدد غير صحيح أصلاً اليوم (`registerFile` و`createMediaJob` و`runWorkflow` و`createConversation` و`postMessage` وغيرها) | minor | `sed -n 1,8p src/app/framework/ports/idempotency_store.py` | مفتوح، المالك backend. يُصلح بحذف العدد |
| N‑1 | **ملاحظة وثائقية:** `final` المعاد في إعادة بث لا يحمل المفتاح `text` الذي كان في `final` الأصلي لوكلاء مثل `echo` (يحمل `content.text` فقط). مقصود في د‑ت8 («مفاتيح المنصة فقط»)، ولا تذكره `03 §3.1` صراحة ولا يكسر AC‑4.1. على عميل Android أن يقرأ `content.text` لا `text` | minor / ملاحظة | `_post(client, cid, key="s2", stream=True)` مرتين ثم قارن `final` | للعلم (product-owner) |
| N‑2 | مفتاح بمسافات حوله (`" pad "`) صفٌّ مستقل عن `"pad"`، ومفتاح بعربي يُخزَّن بترميز الترويسة (latin‑1). متسق ومستقر ولا يضر، لكنه غير مذكور في `03 §0` (يقول «1…255 حرفاً، ليس مسافات فقط») | ملاحظة | انظر السيناريو في §3 | للعلم |
| N‑3 | لا اختبار مستقل يؤكد `reply_to` على الجزء المحفوظ في مسار «استثناء المنسّق» (AC‑2.2). المسار نفسه الذي تجتازه حالتا المهلة وaclose (`_settle_cut`)، فالخطر منخفض | ملاحظة تغطية | — | مقبول |
| N‑4 | إن فشلت كتابة `settled` (توقف قاعدة لحظي) بعد إكمال الدور وحفظ الجواب، تبقى الإعادة 409 حتى 720 ث ثم `agent.failed` رغم أن الجواب محفوظ ويصل بـ`reply_to`. هذه هي المخاطرة 3 و4 في التصميم §7.3، مقبولة صراحة | ملاحظة (موثّق) | محاكاة بمخزن يرمي عند `complete` | مقبول |
| N‑5 | في WebSocket، `invoke` بلا `conversation_id` يُرفض دائماً بـ422 (لا `space_id` في البروتوكول)، فمنطق «`accepted` يعلن المحادثة المفتوحة» (د‑ت11) لا يُبلَغ من السلك اليوم. يُختبر على مستوى الدالة فقط (`test_websocket_endpoint`)، ولا ضرر | ملاحظة | `ws.send_json({"type":"invoke","agent_key":"…","conversation_id":null,"input":{}})` | للعلم |
| N‑6 | نافذة نظرية: إن قُطع الاتصال بعد أن أعادت `invoke` وسُجّل `checkpoint` لكن قبل أن يبدأ Starlette بسحب جسم البث (ميكروثواني)، فمولّد `_metered` لم يبدأ فلا `finally` فيه فلا `settled`. يبقى الصف S2 فتعطي الإعادة 409 حتى 720 ث ثم `agent.failed` (والجزء لم يُحفظ لأن لا شيء جرى). لم أستطع إنتاجه (نافذة لا تُقاس)، وأثره كأثر N‑4 | ملاحظة نظرية | — | مقبول |

لا أخطاء blocking ولا major.

## 5. الحكم

**FAIL (ضيّق، تصليح من سطر واحد)** — البوابات الست خضراء (9123 passed)، ولا خطأ blocking أو major، وكل AC له اختبار أو مراجعة. لكن AC‑6.9 (Must، US‑6) غير مستوفى: docstring `src/app/framework/ports/idempotency_store.py:6` ما زال يعدّ العمليات بعدد ثابت («exactly three operations») وهو ما يمنعه المعيار (B‑1). بعد حذف العدد من ذلك السطر (backend، بلا اختبار جديد) يصير الحكم **PASS**. الاختبارات الحية (7 معايير مع CI، وAC‑7.3) لم تُنفَّذ هنا بقرار س‑14، ونتيجة CI job `integration` شرط قبل بوابة ②.

</div>
