<div dir="rtl">

# قبول مالك المنتج: ث‑3 مصالحة الإرسال الغامض

- **الفرع:** `feat/data-search-send-reconcile`، HEAD `c3e8014` (الأساس `b104878`). الخلفية فقط.
- **ما قرأته:** `stories.md` (66 معياراً)، و`test-plan.md`، و`review.md` (PASS)، و`security-review.md` (PASS)، و`status.md` (قرارات بوابة ① وما بعد المراجعتين، ودورتا الإعادة)، و`design.md` §7، والوثائق الملزمة `03-api-spec.md` §0/§3.1/§3.2/§4، و`06-domain-models.md` §4، و`01-data-model.md` §2.4/§4.2‑ب، و`openapi.yaml` (`postMessage`، `invokeAgent`، `IdempotencyKey`)، و`docs/delivery/data-search-android/gaps.md` §ث‑2 و§ث‑3 و«ملاحظات أخرى».
- **ما تحققت منه بنفسي:**
  - كل اختبار تسمّيه `test-plan.md` موجود في `tests/` (138 اسماً). اسمان تغيّرا في دورة الإعادة وما زالا موجودين: `test_a_failure_after_the_question_is_stored_replays_the_same_problem_with_its_ids` (كان `…is_a_502_with_ids…`)، و`test_the_scope_is_the_endpoint_of_the_conversation_id` (كان `…canonical…`).
  - شغّلت ملفات الوحدة الخاصة بالميزة على `c3e8014`: 15 ملفاً (منها ملفات QA الأربعة، و`R`، و`IT`، و`ST`، و`SQL`، و`UC`، و`MIGU`، و`PAR`، و`C`، و`test_access_domain.py`، و`test_api_errors.py`، و`test_conversations_domain.py`) ⇒ **505 passed، exit 0**. ثم `test_orchestrator.py` و`test_api_agents_router.py` و`test_websocket_endpoint.py` و`test_workflow_orchestration.py` و`test_llm_fallback.py` و`test_error_catalog.py` ⇒ **290 passed، exit 0**. و`lint-imports` ⇒ 9 عقود سليمة.
  - قرأت نص الاختبارات الممثلة لكل قصة (AC‑3.2، 3.8، 4.1، 4.6، 5.3، 7.3، وSR‑1، وSR‑2).
  - `git diff b104878..HEAD` لا يمس `.importlinter` ولا `pyproject.toml` ولا `deploy/` ولا `.github/` ولا `.env.example`.
  - ملفات `tests/integration` الثلاثة تحمل `live_db`، وCI job `integration` يشغّلها (`REQUIRE_LIVE=1`، والمرشّح لا يستثني `live_db`).
- **لم أشغّل** `tests/integration` ولا أي ترحيل (س‑14).
- **المراجعتان كانتا على `26411ea`**، وجاءت بعدهما ثلاثة commits (`743a646`، و`ba21e7a`، و`c3e8014`). لم يُعِد المراجعان القراءة. تحققت بنفسي من أن الإصلاحات في الشيفرة:
  - **SR‑1:** `raise _translate(exc) from None` في المواضع الأربعة، و`error_type` بدل `exc_info`.
  - **SR‑5:** السطر `idempotent_turn.detached_write_failed` موجود.
  - **SR‑2:** اختبار `RoleCatalog` موجود.
  - **SR‑4، والملاحظتان المهمتان 2 و3:** جملها في `03 §0` و`§3.1` و`§4`، وفي `openapi.yaml`، وفي `gaps.md`.
- **الرموز:** ✅ = دليل محلي كامل. ✅ + ⏳ CI = جزء محلي أخضر، والجزء الحي يُنتظر من CI. ⏳ CI = الدليل حي فقط. 🔴 = الدليل ناقص.

## الحكم لكل معيار

### US‑1: معرّف رسالتي يصل فور قبولها (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑1.1 | `R::test_sse_starts_with_accepted_naming_the_stored_user_message`: منسّق حقيقي، وأول إطار `accepted` بمعرّف رسالة user المخزّنة و`seq` و`conversation_id` | ✅ مقبول |
| AC‑1.2 | `FR::test_a_fallback_turn_orders_accepted_notice_tokens_final_with_one_accepted`، و`FR::test_a_turn_that_ends_in_error_after_the_notice_still_has_one_accepted_first` | ✅ مقبول |
| AC‑1.3 | `FR::test_accepted_reaches_the_client_before_the_first_keepalive`: وكيل صامت، ونبضة 20ms، وأول البايتات `event: accepted` | ✅ مقبول |
| AC‑1.4 | `O::test_final_carries_the_user_message_id_that_accepted_announced`، وثلاثة اختبارات `FR::…legacy_frames…` تقارن بإطارات حرفية التُقطت من `b104878` (NFR‑5). أكّد المراجع أن هذه المقارنة ذات معنى | ✅ مقبول |
| AC‑1.5 | `WS::test_two_concurrent_turns_each_get_one_accepted_before_their_first_token`، و`WS::test_accepted_is_the_very_first_frame_of_a_turn` | ✅ مقبول |
| AC‑1.6 | `test_api_agents_router::test_a_streamed_invoke_that_opens_a_thread_announces_it_in_the_first_frame` | ✅ مقبول |
| AC‑1.7 | الحالات الخمس مختبرة: 404 و422 (`R::…refused_request…[unknown,invalid]`)، و429 (`R::test_a_quota_refusal_…`)، و409 لمحادثة حُذفت بين القراءة والإلحاق (`ERR::…deleted_between_the_read_and_the_append…`)، و403 (`ERR::test_a_viewer_is_refused_with_403_before_any_stream…`) | ✅ مقبول |
| AC‑1.8 | `O::test_without_a_conversations_seam_there_is_no_accepted_and_no_id`، و`FR::test_without_a_conversations_seam_the_frames_are_exactly_the_legacy_ones` | ✅ مقبول |
| AC‑1.9 | `O::test_accepted_is_the_first_frame_with_exactly_three_keys`، و`O::test_accepted_carries_no_part_of_the_text`، و`FR::…none_of_a_long_arabic_text` (10,000 حرف عربي على السلك) | ✅ مقبول |
| AC‑1.10 | `FR::test_a_streamed_workflow_run_has_no_accepted_frame` (الإطارات مساوية لـ`b104878`)، و`test_workflow_orchestration` | ✅ مقبول |

### US‑2: كل جواب يشير إلى سؤاله (Must)

| AC | الدليل | الحكم |
|---|---|---|
| AC‑2.1 | وحدة: `O::test_the_reply_is_stored_with_reply_to_the_user_message`، و`R::test_the_reply_records_the_question_it_answers_in_json_and_history`، و`test_api_agents_router::test_invoke_without_stream_returns_the_persisted_turn`. حي: `RLS::test_reply_to_round_trips_and_a_user_message_holds_null` | ✅ + ⏳ CI |
| AC‑2.2 | `O::test_a_stored_partial_replies_to_the_user_message_too[المهلة، error بعد tokens]`، و`O::test_an_abandoned_partial_replies_to_the_user_message_too`. مسار «استثناء المنسّق» بلا اختبار مستقل (N‑3)، لكنه يمر بالدالة نفسها `_settle_cut` ← `_persist_partial`. أكّد ذلك المراجع | ✅ مقبول |
| AC‑2.3 | وحدة: `R::…in_json_and_history` (رسالة user: المفتاح حاضر و`None`)، و`test_workflow_orchestration`، و`SQL::test_a_row_hydrates_reply_to`، و`UC::test_no_reply_to_stays_none`. ملخص العامل لا يمرر `reply_to`، والقيمة الافتراضية `None`. حي: `RLS::test_a_message_written_without_reply_to_reads_back_null`، و`MIG::…round_trips_and_an_old_row_survives` | ✅ + ⏳ CI |
| AC‑2.4 | `O::test_two_concurrent_turns_with_the_same_text_each_reply_to_their_own_question` (الترتيب `u1, u2, a2, a1`) | ✅ مقبول |
| AC‑2.5 | `SQL::test_append_with_reply_to_is_one_guarded_insert_then_the_version_update`، و`SQL::…without_reply_to_is_todays_plain_insert_then_update`. لا `UPDATE` على الرسائل، وعدد العبارات لم يتغير | ✅ مقبول |
| AC‑2.6 | `R::test_a_client_sent_reply_to_is_ignored` | ✅ مقبول |
| AC‑2.7 | وحدة: ثلاثة اختبارات `UC::…validation_error…`، و`test_conversations_domain::test_inv_cv6_only_assistant_messages_reply_to_a_message`، و`SQL::test_a_refused_reply_to_raises_before_any_update`. حي: ثلاثة `RLS::test_reply_to_naming_…_is_refused` | ✅ + ⏳ CI |
| AC‑2.8 | `R::test_deleting_the_question_keeps_the_id_on_the_answer` | ✅ مقبول |
| AC‑2.9 | `R::test_the_reply_records_the_question_it_answers_in_json_and_history` (`reply["reply_to"] == user["id"]`) | ✅ مقبول |
| AC‑2.10 | وحدة: ثلاثة `MIGU` (العمود وحده، و`downgrade` يحذفه وحده، وتغيير في الكتالوج فقط)، وثلاثة `PAR`. حي: `MIG::test_downgrade_then_upgrade_round_trips_and_an_old_row_survives` | ✅ + ⏳ CI |

### US‑3: إعادة الإرسال بالمفتاح نفسه، فرع JSON (Must)

| AC | الدليل | الحكم |
|---|---|---|
| AC‑3.1 | `R::test_without_a_key_the_key_store_is_never_touched[False,True]` | ✅ مقبول |
| AC‑3.2 | `R::test_a_repeat_returns_the_same_message_and_runs_nothing`. قرأته: الجسمان متساويان، و`_RUNS` واحد، ورسالتان في السجل، و`store.calls == [claim, checkpoint, complete, claim]` | ✅ مقبول |
| AC‑3.3 | `R::test_the_same_key_with_other_content_is_a_conflict_and_runs_nothing` | ✅ مقبول |
| AC‑3.4 | `R::test_a_running_turn_makes_the_repeat_a_409_and_the_first_still_completes` | ✅ مقبول |
| AC‑3.5 | `R::test_two_keys_with_the_same_text_are_two_turns` | ✅ مقبول |
| AC‑3.6 | `R::test_the_same_key_on_another_conversation_is_a_new_turn`، و`R::test_the_scope_uses_the_canonical_conversation_id_not_the_path_text`، و`IT::test_the_scope_is_per_conversation_and_per_workspace` | ✅ مقبول |
| AC‑3.7 | 429: `R::test_a_failure_before_the_question_is_stored_frees_the_key`. 409: `ERR::test_that_409_frees_the_key…`. 422: لا مطالبة أصلاً (`R::test_an_unusable_key_is_a_422…`) | ✅ مقبول |
| AC‑3.8 | `R::test_a_failure_after_the_question_is_stored_replays_the_same_problem_with_its_ids`. الإعادة تعيد `code` و`status` و`title` والمعرّفين كما هي، بلا تشغيل ولا سؤال ثانٍ. لكن الوكيل المزيّف يرمي `ConflictError`، فالرمز المختبر 409 لا 502. حالة 502 `agent.failed` بالمعرّفات في JSON تغطيها اختبارات AC‑5.2 وAC‑5.3. أقبل هذا الدليل لأن الآلية لا تتعلق بالرمز | ✅ مقبول |
| AC‑3.9 | `R::test_an_unusable_key_is_a_422_before_any_read_or_run["", "   ", 256]` | ✅ مقبول |
| AC‑3.10 | `R::test_a_stream_then_a_json_repeat_returns_the_message_not_a_conflict` | ✅ مقبول |
| AC‑3.11 | `R::test_a_replay_after_the_answer_was_deleted_is_a_404_in_both_shapes` | ✅ مقبول |
| AC‑3.12 | `R::test_a_replay_after_the_conversation_was_deleted_is_a_404_and_touches_nothing` | ✅ مقبول |
| AC‑3.13 | `R::test_a_swept_key_starts_a_new_turn` | ✅ مقبول |
| AC‑3.14 | `R::test_arabic_and_very_long_content_replay_byte_for_byte`، و`IT::test_the_hash_is_stable_for_arabic_and_never_escapes_it`، و`IT::test_the_stored_keys_are_exactly_the_closed_list_with_no_text` | ✅ مقبول |

### US‑4: المفتاح نفسه على البث (Must)

| AC | الدليل | الحكم |
|---|---|---|
| AC‑4.1 | `R::test_a_streamed_repeat_replays_the_original_frames_without_tokens_or_calls`. قرأته: الإطاران `["accepted","final"]`، و`accepted` يساوي الأصل مع `replayed: true`، و`message_id` و`content` و`user_message_id` و`usage` مساوية، ولا تشغيل. و`R::test_a_replayed_final_carries_the_stored_citations`، و`IT::test_a_replayed_turn_is_exactly_accepted_then_final` | ✅ مقبول |
| AC‑4.2 | `R::test_a_streamed_failure_replays_accepted_then_the_same_error` | ✅ مقبول |
| AC‑4.3 | `R::test_an_abandoned_stream_is_replayed_as_agent_failed_with_the_question_id`، و`O::test_an_abandoned_turn_settles_with_the_partial_it_managed_to_save`، و`DIS::…[h11,httptools]` على خادم uvicorn حقيقي. تحقق QA أن هذا الاختبار يفشل بلا `_shielded` | ✅ مقبول |
| AC‑4.4 | `O::test_abandoning_right_after_accepted_settles_with_no_message_id`، و`ERR::test_a_reader_who_left_right_after_accepted_gets_agent_failed_for_the_same_question[stream,json]` | ✅ مقبول |
| AC‑4.5 | `R::test_a_young_unfinished_claim_is_a_409_problem_not_a_stream` | ✅ مقبول |
| AC‑4.6 | وحدة: `R::test_one_second_before_the_threshold_it_is_still_a_409`، و`R::test_an_old_accepted_claim_is_reported_lost_with_the_question_id` (قرأته: 502 بـ`user_message_id` في JSON، و`accepted` (`replayed`) ثم `error` في البث، ولا تشغيل)، و`IT::…exclusive_below_and_inclusive_at`، و`IT::…stream_cap_plus_120_seconds`. حي: `IL::test_the_claim_age_comes_from_the_database_clock` | ✅ + ⏳ CI |
| AC‑4.7 | `IT::test_the_stored_keys_are_exactly_the_closed_list_with_no_text`، و`R::test_the_stored_handle_carries_ids_and_counts_only_after_every_path` | ✅ مقبول |
| AC‑4.8 | `R::test_without_a_key_the_key_store_is_never_touched[True]` | ✅ مقبول |
| AC‑4.9 | `R::test_a_json_then_a_stream_repeat_replays_two_frames` | ✅ مقبول |
| AC‑4.10 | وحدة: `S.calls` بثلاثة استدعاءات للدور الأول واستدعاء `claim` واحد للإعادة، و`ST::…checkpoint_is_one_update…`، و`ST::…complete_is_one_update…`. حي: ثلاثة `IL`. (عدّ NFR‑2 على مستوى المنفذ كما نص عليه المتطلب. انظر الملاحظة 8 أدناه) | ✅ + ⏳ CI |

### US‑5: نتيجة المحاولة التي لم تكتب جواباً (Should)

| AC | الدليل | الحكم |
|---|---|---|
| AC‑5.1 | `R::test_a_failure_before_any_token_has_the_question_id_and_no_message_id` (بث، مع الإعادة) | ✅ مقبول |
| AC‑5.2 | `ERR::test_a_json_failure_before_any_token_has_the_question_id_and_replays_the_same_problem` | ✅ مقبول |
| AC‑5.3 | **السلوك:** `O::test_a_reply_that_failed_to_save_settles_agent_failed_without_message_id`، و`R::test_a_reply_that_could_not_be_saved_is_a_502_with_the_question_id_not_a_500` (قرأته: 502 `agent.failed` بـ`user_message_id` وبلا `message_id`، والإعادة مثله). جانب البث تغطيه آلية `outcome=failed` العامة. **التوثيق: غائب.** المعيار يطلب «ويُوثَّق أن العميل الذي استلم `final` الأصلي بلا `message_id` يملك نصاً لم يُحفظ». بحثت في `03 §3.1` و`§4` و`openapi.yaml` و`gaps.md` §ث‑3 فلم أجد هذه الجملة. وفي `03 §4:487` فقط أن `message_id` على `error` «غائب حين لم يُحفظ شيء»، وهذا عن `error` لا `final` | 🔴 مرفوض (جزء التوثيق، Should) |
| AC‑5.4 | `O::test_abandoning_right_after_accepted_settles_with_no_message_id`، و`ERR::…left_right_after_accepted…[stream]` | ✅ مقبول |
| AC‑5.5 | `R::test_a_failure_leaves_no_assistant_message_that_is_not_complete_or_partial` | ✅ مقبول |
| AC‑5.6 | `R::test_a_failure_without_a_key_touches_no_store_and_leaves_the_question` | ✅ مقبول |

### US‑6: العقد والوثائق الملزمة (Must)

| AC | الدليل | الحكم |
|---|---|---|
| AC‑6.1 | `C::test_the_idempotency_header_is_declared_on_exactly_the_contracts_operations`، و`C::test_the_contract_publishes_message_reply_to_and_the_problem_extensions`. كلاهما أخضر في تشغيلي | ✅ مقبول |
| AC‑6.2 | الاختبار نفسه (`reply_to` بـ`format: uuid` و`nullable`، وفي `LIVE.required`) | ✅ مقبول |
| AC‑6.3 | قرأت `openapi.yaml`: وصف `postMessage` ووصف SSE في `invokeAgent` يذكران `accepted` أولاً، والترتيب، و«Ignore unknown frame types» | ✅ مقبول |
| AC‑6.4 | قرأت `03 §0`: `postMessage` ونطاقه (المساحة + المحادثة + المفتاح)، والإعادة تعيد «نتيجة» لا بثاً، والفشل بعد القبول لا يحرّر المفتاح، والاحتفاظ «يومياً بعد يومين، فيعيش بين يومين وثلاثة» وما بعده، و`runWorkflow` باقٍ خارج المخزن | ✅ مقبول |
| AC‑6.5 | قرأت `03 §2` (`reply_to`)، و§3.1 (مثال `accepted`، و`final.user_message_id`، وإطارا الإعادة)، و§3.2 (WebSocket)، و§4 (العضوان، مجموعة مغلقة). س‑2 على التوصية | ✅ مقبول |
| AC‑6.6 | قرأت `06 §4`: `Message.reply_to`، وINV‑CV6، وجملة «فشل بلا رسالة» صارت تقول ما قرره ق‑4 | ✅ مقبول |
| AC‑6.7 | `PAR` (أخضر)، و`01 §2.4:204,223` (عمود `reply_to`)، و`01 §4.2‑ب:768` (الكنس بعد يومين، `retention.py:140`) | ✅ مقبول |
| AC‑6.8 | `gaps.md`: «الإصلاح (2026‑11‑11)» في §ث‑3، وجملة §ث‑2 تحيل إلى ما قُرّر، وسطر الاحتفاظ صُحّح («تحقّق منها ث‑3»)، وتنبيه أسطر `backend-reply.md` 96–97 و220 | ✅ مقبول |
| AC‑6.9 | `framework/ports/idempotency_store.py:5-7` صار «on the operations that opt in (the contract lists them)» بعد `26411ea` (أُغلق B‑1). و`api/v1/idempotency.py` يذكر أن `postMessage` له مسار آخر | ✅ مقبول |

### US‑7: المفتاح والمعرّفات لا تعبر حدود المساحة أو الصلاحية (Must)

| AC | الدليل | الحكم |
|---|---|---|
| AC‑7.1 | وحدة: `R::test_another_workspace_cannot_reach_or_replay_the_key` (404 بلا لمس المخزن)، و`IT::test_the_scope_is_per_conversation_and_per_workspace`. حي: اختبار RLS القائم للمفاتيح في `IL` | ✅ + ⏳ CI |
| AC‑7.2 | `R::test_a_viewer_is_refused_and_no_row_is_created`، و`ERR::test_a_viewer_is_refused_with_403…` | ✅ مقبول |
| AC‑7.3 | `RLS::test_reply_to_is_invisible_to_another_tenant`. قرأته: جلسة B تقرأ `[]`، والقراءة المباشرة للمساحة B صفر صفوف، والمساحة A ترى `reply_to`. لا مكافئ محلياً، لأن RLS خاصية في Postgres | ⏳ CI |
| AC‑7.4 | `R::test_a_second_user_of_the_same_workspace_replays_the_first_users_turn` (س‑12، وموثّق في `03 §0` و`gaps.md`) | ✅ مقبول |
| AC‑7.5 | `R::test_the_new_log_lines_carry_neither_the_key_nor_the_text`، و`O::test_a_ledger_that_raises_never_fails_the_turn_and_logs_no_text`، و`test_api_errors::…closed_set…`. **وبعد SR‑1:** `ST::test_a_database_error_is_translated_without_the_key_or_workspace_in_its_chain`، وهو يرمي `DBAPIError` بالمفتاح في معاملاته، ويؤكد أن سلسلة الاستثناء المطبوعة خالية من المفتاح ومن معرّف المساحة. واختبارا `IT` للكاتب المنفصل (SR‑5) | ✅ مقبول |
| AC‑7.6 | `R::test_no_or_a_bad_token_is_401_and_the_store_is_untouched` | ✅ مقبول |
| AC‑7.7 | `lint-imports`: 9 عقود سليمة (أعدت تشغيله)، و`git diff b104878..HEAD -- .importlinter` فارغ. (المعيار يقول «الثمانية»، والعدد اليوم 9 لأن عقد WireTwin كان قبل الميزة) | ✅ مقبول |

## العدّ

| الفئة | العدد | المعايير |
|---|---|---|
| ✅ دليل محلي كامل | 57 | كل ما لم يُذكر في الصفوف التالية |
| ✅ + ⏳ CI (الجزء المحلي أخضر، والجزء الحي يُنتظر) | 7 | AC‑2.1، 2.3، 2.7، 2.10، 4.6، 4.10، 7.1 (كلها Must) |
| ⏳ CI فقط | 1 | AC‑7.3 (Must) |
| 🔴 | 1 | AC‑5.3: جزء التوثيق فقط (Should) |
| **المجموع** | **66** | |

**كل معايير Must ✅، إلا ثمانية تنتظر CI job `integration`.** المعيار الوحيد 🔴 من قصة Should (US‑5)، والسلوك فيه مختبر وأخضر، والناقص جملة في الوثائق.

## الشروط

1. **CI job `integration` أخضر على الـ PR** (س‑14)، وفيه هذه الاختبارات الحية بأسمائها:
   - `tests/integration/test_conversations_repository_rls.py`: الاختبارات الستة الجديدة لـ`reply_to`.
   - `tests/integration/test_message_reply_to_migration_live.py::test_downgrade_then_upgrade_round_trips_and_an_old_row_survives`.
   - `tests/integration/test_idempotency_live.py`: الاختبارات الأربعة الجديدة، والقائمة.

   إن فشل أي منها، يسقط القبول لمعياره، ويعود العمل إلى:
   - **data-engineer**، إن كان الفشل في الترحيل `0009` أو RLS أو الإدراج المحروس (AC‑2.3، 2.7، 2.10، 7.3).
   - **backend-developer**، إن كان في `SqlIdempotencyStore` (AC‑4.6، 4.10، 7.1) أو في القراءة (AC‑2.1).
2. **قبل بوابة ② (موصى به، غير حاجب):** يعود AC‑5.3 إلى **backend-developer** لإضافة جملة توثيق، بلا شيفرة. تذهب الجملة إلى `03 §3.1`، وإلى وصف `postMessage` في `openapi.yaml`، وإلى §ث‑3 في `gaps.md`، ونصها تقريباً: «`final` بلا `message_id` = الجواب وصل لكنه لم يُحفظ؛ النص الذي في يد العميل غير موجود في السجل، وإعادة المحاولة بالمفتاح نفسه تعطي `error` `agent.failed` (502) لا `final`». إن أُضيفت، يصير AC‑5.3 ✅. وإن لم تُضف، يبقى القبول قائماً، لأن US‑5 Should.

## أحكام الانحرافات عن التصميم (من جهة المنتج)

- **د‑ت6 (جواب أُنتج ولم يُحفظ ⇒ 502 `agent.failed` ومعه `user_message_id`، بدل 500 `common.internal`): مقبول.**
  - 502 أصدق للعميل: السؤال محفوظ، والفشل في الدور لا في الخادم كله. ومع `user_message_id` يصالح العميل سؤاله.
  - وبدونه لا تساوي الإعادة الرد الأول (FR‑12أ).
  - الأثر على `invokeAgent` بلا مفتاح مقبول أيضاً. المسار نادر، و500 لم يكن موعوداً به في العقد («persistence is not wired» جملة خاطئة لهذه الحالة).
  - شرطه التوثيق في الشرط 2 (AC‑5.3).
- **د‑ت4 (مات العامل بين المطالبة وحفظ السؤال ⇒ الإعادة 502 بلا معرّفات وبلا بث، ولا يُحرَّر المفتاح): مقبول.**
  - الخادم لا يعرف هل كُتب السؤال، فلا يجوز أن يختلق معرّفاً، ولا أن يحرّر المفتاح فيسمح بسؤال ثانٍ ربما مكرر.
  - النافذة ضيقة: كتابة رسالة واحدة. وعلى العميل أن يقرأ السجل ليتأكد (FR‑20).
  - موثّق في `03 §0`.
- **د‑ت8 (`final` المعاد بمفاتيح المنصة فقط، بلا `text`): مقبول، لأنه وُثّق.**
  - مفاتيح الوكيل لا تُحفظ، و`content.text` المحفوظ قد يبدأ بجملة التحول، فلا يساوي `text` الخام.
  - الوثائق تقول الآن صراحة «`final` المعاد لا يحمل `text`؛ اقرأ `content.text`»، في `03 §3.1` و`openapi.yaml` و`gaps.md`.
  - **على Android أن يقرأ `content.text` في كل `final`**، لا في المعاد وحده. هذا يوحّد المسار.
- **د‑ت9 (`detail` ثابت في الإعادة): مقبول.** يمنع إعادة نص من مزوّد. والعقد هو الرمز والحالة والمعرّفات، والعميل لا يعرض `detail` الإعادة نصاً للمستخدم.
- **د‑ت13 (409 هذا المسار لا يكرر قيمة المفتاح): مقبول.** العميل يعرف مفتاحه، وحذفه يقلل ما قد يصل السجلات (AC‑7.5).
- **د‑ت11 (غلاف WebSocket لإطار `accepted` بلا محادثة): مقبول.** لا يُبلَغ من السلك اليوم (N‑5)، ولا ضرر منه.

## قرارات المالك بعد المراجعتين (تحققت من تنفيذها)

- **SR‑2:** وُثّق في `03 §0` وفي `openapi.yaml` أن الإعادة تعمل تحت `conversations:write` وحدها. والاختبار `test_access_domain.py::test_every_role_that_may_write_conversations_may_also_read_them_and_invoke_agents` موجود وأخضر.
- **SR‑4:** قبول الخطر موثّق في `03 §0` («البصمة المخزَّنة…»، SHA‑256 بلا ملح)، وفي `openapi.yaml`. لا سر جديد.
- **SR‑1:** أُصلح داخل الميزة (انظر AC‑7.5). و`hide_parameters` مسجّل بنداً **مفتوحاً** في `gaps.md` («ملاحظات أخرى»).
- **SR‑3:** مكتوب في `gaps.md` لـ Android: «UUIDv4 عشوائي لكل محاولة يقصدها المستخدم، لا متسلسل ولا مشتق». وفي `03 §0` أيضاً.

## ملاحظات غير حاجبة مقبولة

أقبل كلاً منها بالاسم:

1. **N‑1** (`final` المعاد بلا `text`): وُثّقت، انظر د‑ت8.
2. **N‑2** (المفتاح يُقارن حرفياً بلا قص، والعربي بترميز الترويسة): مقبول. لا يمس Android ما دام يرسل UUIDv4. لم يُوثَّق في `03 §0`، وهذا تحسين لاحق.
3. **N‑3** (لا اختبار مستقل لـ`reply_to` في مسار «استثناء المنسّق»): مقبول، لأن المسار يمر بالدالة نفسها المختبرة.
4. **N‑4 وN‑6** (فشل كتابة `settled` أو قطع قبل بدء سحب الجسم ⇒ الإعادة 409 حتى 720 ث ثم `agent.failed`، والجواب قد يكون محفوظاً بـ`reply_to`): مقبول، وهو الخطران 3 و4 في `design.md` §7.3. والمصالحة من السجل ممكنة (FR‑20). وصار الفشل المنفصل يترك سطراً في السجل (SR‑5).
5. **N‑5** (فرع WebSocket غير قابل للوصول): مقبول.
6. **ملاحظات المراجع التي لم تُنفَّذ:**
   - #5 (موضع `PROBLEM_EXTENSION_KEYS` بين `_FALLBACK_CODE` و`_FALLBACK_STATUS`، تجميلي).
   - #8 (docstring الفرع غير القابل للوصول في `streaming.py`).
   - #9 (مسار «استثناء الآلة حول الوكيل» بلا `user_message_id` في الرد الأول، والإعادة تحمله. مسار نادر لم يطلبه التصميم).
   - التعليق الاختياري في `test_error_catalog.py`.

   كلها مقبولة.
7. **AC‑3.8:** الدليل يختبر الرمز 409 لا 502 (يتبع الوكيل المزيّف). مقبول لأن آلية الإعادة لا تتعلق بالرمز، و502 مختبر في AC‑5.2 وAC‑5.3.
8. **AC‑4.10/NFR‑2:** العدّ على مستوى المنفذ: `claim` واحد للإعادة. لكن على مستوى SQL، إعادة `claim` هي `INSERT` يتعارض ثم `SELECT`، وكلاهما بالمفتاح الأساسي، بنمط ث‑4 القائم. مقبول، لأن المتطلب نص على العدّ بمنافذ مزيّفة، والكلفة ثابتة ورخيصة.
9. **AC‑7.7:** المعيار يقول «العقود الثمانية» والعدد 9. الفرق لا يخص الميزة، لأن العقد التاسع (WireTwin) سابق لها.
10. **المراجعتان على `26411ea`:** تحققت بنفسي من إصلاحات دورة الإعادة (`743a646`، و`ba21e7a`، و`c3e8014`)، ولم يُعِد المراجعان القراءة. أقبل ذلك، لأن التغيير محصور في السجلات والتوثيق، وفي `from None`، وفي 422 لحرف surrogate منفرد (SR‑6)، والبوابات خضراء على `c3e8014`.

## ما يجب أن يعرفه المالك قبل بوابة ②

- القبول **مشروط** بنجاح CI job `integration` على الـ PR. ثمانية معايير Must لها جزء حي لم يُشغَّل قط (الترحيل `0009` ذهاباً وإياباً، وRLS لـ`reply_to`، وعمر المطالبة بساعة القاعدة).
- **تغيير سلوك يراه العميل خارج المفتاح:** `invokeAgent` و`postMessage` بلا مفتاح يعطيان الآن 502 `agent.failed` (بدل 500) حين يُنتج الجواب ولا يُحفظ (د‑ت6). ويبدأ كل بث وكيل بإطار `accepted` جديد. والعميل القديم يتجاهله بحسب `backend-reply.md:92`.
- **`backend-reply.md` في مستودع Android لم يُحدَّث** (س‑13). أسطره 96–97 و220 لم تعد صحيحة، والمرجع الآن `openapi.yaml` و`gaps.md` §ث‑3. على فريق Android أن يعرف ثلاثة أشياء: UUIDv4 لكل محاولة، و`content.text` لا `text`، وأن «problem فيه `user_message_id` نهائي لا يُعاد بالمفتاح نفسه».
- **بندان مفتوحان بقرارك:** `hide_parameters` (في `gaps.md`)، وHMAC للبصمة (SR‑4، خطر مقبول).
- **AC‑5.3:** جملة توثيق واحدة ناقصة (الشرط 2). أوصي بإضافتها قبل الدمج لأنها رخيصة ويحتاجها Android.

## الحكم النهائي

**مقبول (مشروط)**: معايير Must ستون. 52 منها ✅ بدليل محلي كامل، وثمانية لها جزء حي (AC‑2.1، 2.3، 2.7، 2.10، 4.6، 4.10، 7.1، 7.3). هذه يصير قبولها نهائياً حين يخضرّ CI job `integration` على الـ PR. إن فشل، يعود المعيار المعني إلى data-engineer (الترحيل/RLS) أو backend-developer (سجل المفاتيح).
- المراجعتان PASS، وأُصلح ما أُرسل إلى دورة الإعادة (SR‑1، SR‑5، SR‑6، المهمتان 2 و3، وSR‑2 وSR‑4 بالتوثيق).
- المعيار الوحيد المرفوض، AC‑5.3 (Should)، ناقصه جملة توثيق لا سلوك، ويعود إلى backend-developer، ولا يحجب القبول.
- انحرافات التصميم د‑ت4 ود‑ت6 ود‑ت8 ود‑ت9 ود‑ت11 ود‑ت13 مقبولة من جهة المنتج.

</div>
