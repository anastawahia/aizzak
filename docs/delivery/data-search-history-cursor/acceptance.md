<div dir="rtl">

# قبول مالك المنتج: ث‑5 قراءة السجل من الأحدث (cursor للخلف)

- **الفرع:** `feat/data-search-history-cursor`، HEAD `d68d5ec` (الأساس `master` `5d54b15`). الخلفية فقط.
- **ما قرأته:** `status.md` (القرارات ق‑1…ق‑3 ودورة الإعادة)، و`stories.md` (37 معياراً، كلها Must)، و`test-plan.md`، و`review.md` (PASS)، و`security-review.md` (PASS)، و`design.md` §5 و§6. وقرأت فروق الوثائق الملزمة: `openapi.yaml` (`listMessages`)، و`03-api-spec.md` §0، و`02-port-contracts.md` §2، و`06-domain-models.md` §4، وdocstrings `pagination.py` و`sql_repository.py`.
- **ما تحققت منه بنفسي:**
  - كل اختبار تسمّيه `test-plan.md` §1 موجود في `tests/` (50 اسماً). الاسم `test_the_owner_gets_the_same_transcript_plan_as_app_rw` مكتوب في الخطة مختصراً (`…owner_gets…`)، وهو موجود.
  - قرأت نص الاختبارات الممثلة: AC‑1.1، وAC‑1.6، وAC‑2.4، وAC‑2.6، وAC‑5.1 (العبارة الخلفية تُقارن كاملة بـ`_backward_statement` المستقل، `seq < n`)، وAC‑3.4 (`_master_forward_statement`)، وAC‑5.4 (RLS الحي).
  - شغّلت ملفات الوحدة السبعة المتأثرة (`R`، `SQL`، `F`، `UC`، `C`، `X`، و`test_electrical_qa_pr_c_round2.py`) على `d68d5ec`، والنتيجة **564 passed، exit 0**. وجمعت ملفي الحي بـ`--collect-only` (53 و25 اختباراً) بلا خطأ، ولم أشغّلهما.
  - `git diff master...HEAD` لا يمس `migrations/` ولا `.importlinter` ولا `deploy/` ولا `.github/`.
  - ملفا الحي يحملان `pytestmark = [pytest.mark.live_db]`، وCI job `integration` يشغّلهما (`REQUIRE_LIVE=1`، والمرشّح لا يستثني `live_db`).
  - مستودع Android: لم يتغير فيه ملف اليوم (`find -newermt 2026-11-17` فارغ). التعديلات غير المحفوظة فيه أقدم من الميزة.
  - إصلاحات دورة الإعادة موجودة في الشيفرة: العبارة الخلفية الدقيقة (المهمة 1)، و`==` بدل `is` (التحسين 2)، وdocstrings `pagination.py` (3) و`test_electrical_qa_pr_c_round2.py` (4)، وحذف الاختبار المكرر (5)، وتصحيح AC‑2.4 في `design.md` (7، N‑1)، و`_SEQ_TEXT` مع اختباري `…refuse_non_canonical_digits` (S‑4).
- **بوابات المشروع:** أعادها المنسّق على `7a66289`، وبعده تغيّر `status.md` وحده. النتائج: `ruff format --check` (1010 files already formatted)، و`ruff check` (All checks passed!)، و`mypy src` (552 ملفاً بلا مشكلة)، و`lint-imports` (9 kept، 0 broken)، و`pytest -rs tests/unit tests/architecture tests/eval` (9256 passed، 37 skipped، exit 0)، و`deploy/prometheus/test-rules.sh` (OK).
- **لم أشغّل** `tests/integration` (المكدس الحي مستعمل، وقرار المالك ق‑1/س‑9 أن الحي يُشغَّل في CI `integration` وحده).
- **الرموز:** ✅ يعني أن الدليل المحلي كامل. ✅ + ⏳ CI يعني أن الجزء المحلي أخضر والجزء الحي يُنتظر من CI. ⏳ CI يعني أن الدليل حي فقط ولم يُشغَّل بعد. ⏳ يعني خطوة مؤجلة بقرار التصميم. 🔴 يعني أن الدليل ناقص.

## الحكم لكل معيار

### US‑1: أفتح المحادثة على أحدث رسائلها (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑1.1 | `R::test_desc_first_page_is_the_newest_twenty_newest_first`: الـ`seq` = 45…26، و`meta.limit` = 20، و`next_cursor` نص غير فارغ (قرأته) | ✅ |
| AC‑1.2 | `R::test_desc_short_thread_is_one_page_with_a_null_cursor` | ✅ |
| AC‑1.3 | `R::test_desc_exactly_limit_messages_has_no_phantom_page`، و`LIMIT 21` في `SQL::test_list_messages_desc_is_one_select_below_the_cursor_newest_first` | ✅ |
| AC‑1.4 | `R::test_desc_of_a_fresh_thread_is_an_empty_page`، و`R::test_qa_a_thread_of_only_soft_deleted_messages_is_an_empty_page` | ✅ |
| AC‑1.5 | وحدة: `R::test_desc_first_page_skips_the_soft_deleted_newest`. حي: `RLS::test_list_messages_newest_first_pages_by_seq_cursor_and_excludes_deleted` | ✅ + ⏳ CI |
| AC‑1.6 | `R::test_desc_and_asc_walks_return_the_same_messages_byte_for_byte`: عربي بتشكيل وemoji، ونص 100,000 حرف، و`citations`/`partial`/`fallback`/`reply_to`، ومساواة كل `MessageOut` بنظيره، وبايتات UTF‑8 بلا `\u` (قرأته) | ✅ |

### US‑2: أحمّل الأقدم بلا تكرار، وأعرف متى انتهى السجل (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑2.1 | وحدة: `R::test_desc_full_walk_is_three_pages_of_20_20_5`. حي: اختبار `RLS` نفسه في AC‑1.5، بالـ cursor الحقيقي | ✅ + ⏳ CI |
| AC‑2.2 | `R::test_desc_walk_is_the_exact_reverse_of_the_asc_walk_across_gaps` | ✅ |
| AC‑2.3 | `R::test_messages_appended_mid_walk_never_enter_its_later_pages`، و`R::test_qa_appends_mid_backward_walk_with_a_tiny_limit_never_duplicate_or_skip` | ✅ |
| AC‑2.4 | `R::test_a_soft_delete_mid_walk_neither_shifts_nor_skips`: الصفحة الثانية 25…5 بلا 15 (20 عنصراً)، والثالثة 4…1 ثم `null` (قرأته). يطابق نص المعيار: 15 لا تظهر، ولا إزاحة، ولا تخطٍّ. وصُحّحت القيم في `design.md` (`7a66289`) | ✅ |
| AC‑2.5 | `R::test_a_non_null_desc_cursor_has_an_older_message_unless_all_were_deleted` | ✅ |
| AC‑2.6 | `R::test_the_catch_up_recipe_finds_every_new_message_once`: صفحتان، و46…70 مرة واحدة بالدمج بـ`id` (قرأته). والوصفة مكتوبة في `description` الخاص بـ`listMessages` في `openapi.yaml` | ✅ |
| AC‑2.7 | `R::test_desc_first_page_holds_the_turn_and_the_reply_to_it` | ✅ |

### US‑3: لا شيء يتغير للعميل القائم (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑3.1 | `R::test_the_default_walk_is_unchanged`. واختبارات `GET …/messages` القائمة لم تتغير: السطر الوحيد المحذوف من `R` هو استيراد `FallbackRef` | ✅ |
| AC‑3.2 | `R::test_no_order_and_order_asc_are_byte_identical` | ✅ |
| AC‑3.3 | `R::test_a_cursor_minted_before_the_change_still_reads_forward`، و`F::test_seq_cursor_payload_is_unchanged` | ✅ |
| AC‑3.4 | `SQL::test_list_messages_forward_sql_is_unchanged`، يقارن بـ`_master_forward_statement` المستقل (قرأته)، و`SQL::test_an_explicit_asc_order_is_the_forward_statement` | ✅ |
| AC‑3.5 | `R::test_page_keys_are_the_same_in_both_directions` | ✅ |
| AC‑3.6 | `C::test_the_order_parameter_is_declared_on_list_messages_alone` (العقد والمولَّد، في الاتجاهين) | ✅ |

### US‑4: المدخل الخاطئ يُرفض بوضوح (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑4.1 | `R::test_an_unknown_order_is_a_validation_error` (`foo`، `DESC`، فارغ)، و`R::test_qa_order_is_case_and_whitespace_strict` (9 قيم) | ✅ |
| AC‑4.2 | وحدة: `R::test_a_cursor_never_crosses_directions`، و`SQL::test_a_cursor_of_the_other_direction_runs_no_statement`، و`F::test_seq_cursors_refuse_the_other_direction`. حي: الخلط داخل اختبار `RLS` في AC‑1.5 | ✅ + ⏳ CI |
| AC‑4.3 | `R::test_desc_refuses_malformed_and_foreign_cursors`، و`R::test_qa_desc_refuses_every_malformed_cursor_with_422_and_never_500` (11 مدخلاً)، و`F::test_decode_desc_seq_cursor_rejects_garbage` | ✅ |
| AC‑4.4 | وحدة: `SQL::test_a_seq_past_int4_runs_no_statement_in_either_direction`، و`SQL::test_int4_edges_reach_the_database_in_either_direction`، و`R::test_a_cursor_past_int4_is_invalid_cursor_not_500`، و`R::test_qa_the_int4_boundary_is_exact_in_both_directions`. حي: `RLS::test_list_messages_refuses_a_seq_past_int4` | ✅ + ⏳ CI |
| AC‑4.5 | `R::test_desc_limit_keeps_its_bounds`، و`R::test_qa_desc_limit_bounds_on_a_long_thread` | ✅ |
| AC‑4.6 | `R::test_desc_guards_are_the_asc_guards` (401، 403، 404 لمحادثة مجهولة ومحذوفة ومن مساحة أخرى)، و`UC::test_list_messages_desc_on_a_missing_or_deleted_thread_never_reads_messages`، و`R::test_qa_an_unknown_thread_is_404_before_a_bad_cursor_is_looked_at` | ✅ |
| AC‑4.7 | `R::test_a_desc_cursor_at_or_below_the_oldest_is_an_empty_last_page` | ✅ |

### US‑5: الصفحة التنازلية سريعة ومعزولة (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑5.1 | `SQL::test_list_messages_desc_is_one_select_below_the_cursor_newest_first`: عبارة واحدة، تُقارن كاملة (نصاً ومعاملات) بـ`_backward_statement` المستقل (`seq < n`، `ORDER BY seq DESC`، `LIMIT limit+1`). ومعه `SQL::test_list_messages_desc_page_cursor_is_a_desc_cursor_of_the_last_row` | ✅ |
| AC‑5.2 | حي فقط: `PLAN::test_the_newest_first_transcript_page_scans_uq_msg_seq_backward`، و`…reads_a_page_plus_the_deleted_rows_it_passes`، و`…without_uq_msg_seq_the_newest_first_page_loses_its_backward_scan` (إثبات قدرته على الفشل)، و`…the_owner_gets_the_same_transcript_plan_as_app_rw`، و`…uq_msg_seq_comes_back_on_rollback`. توأم الوحدة: `X::test_the_backward_scan_reader_reads_a_canned_plan` (أخضر). مكتوب ويُجمع، ولم يُشغَّل | ⏳ CI |
| AC‑5.3 | `git diff master...HEAD --stat -- migrations` فارغ (تحققت) | ✅ |
| AC‑5.4 | حي فقط: `RLS::test_list_messages_newest_first_is_tenant_scoped`. المساحة B ترى `[]` و`next_cursor` = `null`، والمساحة A ترى `[2, 1]` (قرأته) | ⏳ CI |
| AC‑5.5 | `X` (المدخل `conversations.list_messages_newest_first` في `CATALOGUE`)، أخضر | ✅ |
| AC‑5.6 | `lint-imports`: 9 kept، 0 broken. و`.importlinter` بلا تغيير، فلا `ignore_imports` جديد. وعقد `application` يمنع `fastapi` و`sqlalchemy`. نص المعيار يقول «الثمانية»، والعدد الحقيقي 9 قبل الفرع (N‑3)، وغرض المعيار متحقق | ✅ |

### US‑6: العقد والوثائق تقول ما يفعله الخادم (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑6.1 | `openapi.yaml`: المعامل `order` (`required: false`، `enum: [asc, desc]`، `default: asc`)، و`description` العملية يذكر النقاط الخمس: الترتيب في كل اتجاه، و`next_cursor` في كل اتجاه، والخلط 422 `common.invalid_cursor`، و`null` نهاية السجل، ووصفة اللحاق (قرأته). والمولَّد يطابقه باختبار `C` | ✅ |
| AC‑6.2 | `03 §0` و`02 §2` (بند 6.3‑ب) يقولان: إلى الأمام افتراضياً، وللخلف بـ`order=desc`، والمعامل خاص بـ`listMessages`. و`06 §4` فيه `MessageOrder`. ولا تبقى صياغة الاتجاه الواحد في `pagination.py` ولا `sql_repository.py` ولا `ports/repository.py` (`grep` لـ«ONE exception» و«One direction» و«reads forward by» فارغ. والسطر `sql_repository.py:620` «in one direction» عن حذف المحادثة، لا عن القراءة) | ✅ |
| AC‑6.3 | `gaps.md` لم يُعدَّل بعد. السطر 11 ما زال يقول إن ث‑5 مفتوحة، والسطر 90 ما زال يقول «لا يوجد رمز `common.invalid_cursor`». التأجيل مقصود إلى ما بعد CI (`design.md` §6، الخطوة 9). وصحّة السطر 90 داخلة في قرار المالك ق‑1 (ت‑5، س‑8) | ⏳ (الشرط 2) |
| AC‑6.4 | لم يتغير في مستودع Android ملف اليوم. والتعديلات غير المحفوظة فيه سابقة ولا تخص الميزة (ق‑3) | ✅ |
| AC‑6.5 | البوابات 1–5 خضراء و`test-rules.sh` OK (أعادها المنسّق على `7a66289`). وCI job `integration` لم يجرِ بعد | ✅ + ⏳ CI |

**الحصيلة (37 معياراً، كلها Must):**
- 29 ✅ بدليل محلي كامل.
- 5 ✅ + ⏳ CI: جزؤها المحلي أخضر، وجزؤها الحي في CI (AC‑1.5، 2.1، 4.2، 4.4، 6.5).
- 2 ⏳ CI: دليلها حي فقط (AC‑5.2، 5.4).
- 1 ⏳: خطوة وثائق مؤجلة بقرار التصميم (AC‑6.3).
- لا 🔴.

## المراجعتان

- **`review.md`: PASS** بلا حاجب. المهمة 1 والتحسينات 2–5 و7 أُصلحت في دورة الإعادة (`3d83255`، `7a66289`)، وتحققت منها في الشيفرة. والتحسين 6 هو N‑2، أقبله أدناه.
- **`security-review.md`: PASS** بلا حاجب. S‑4 أُصلحت في `2dcf08d` (`_SEQ_TEXT`). أما S‑1 وS‑2 وS‑3 فأقبلها بالاسم أدناه.
- المراجعتان كانتا على `49f02c0`، ولم يُعِد المراجعان القراءة بعد دورة الإعادة. الفرق بعدها تشديد في الـ codec، واختبارات، ووثائق، وقد تحققت منه بنفسي (أعلاه). وأرى أن هذا لا يحتاج مراجعة ثانية.

## ملاحظات غير حاجبة مقبولة

- **S‑1** (منخفضة، سابقة للفرع): لا شيء في `live_db` يتحقق أن الـ DSN يشير إلى قاعدة `*_test`. أقبلها لأن الفرع لا يزيد الخطر، فـ`_rebuild_schema` القائم أشد من حذف القيد الجديد. لكني **أوصي ببند لاحق قريب**: الحارس رخيص، ومكدس هذا الجهاز حي ويشترك مع قاعدة الاختبار في الخادم نفسه (15432). القرار للمالك لأنه تعديل على حزمة الاختبار.
- **S‑2** (معلومة، سابقة): معرّف محادثة ليس UUID قد يعطي 500 بدل 404، في كل مسارات `/conversations/{id}`. أقبلها لأنها ليست من الفرع ولا تسرّب فيها. أوصي ببند لاحق صغير يُتحقق منه أولاً في CI `integration`، لأن Android يستفيد من 404 ثابت.
- **S‑3** (معلومة): المسح الخلفي يقرأ الرسائل المحذوفة المتتالية التي يمر بها. الاتجاه الأمامي مثله، والحد مثبت حياً. لا إجراء، ويُراقب كقرار سعة إن ظهر حذف كثيف.
- **N‑2** (بنية اختبارات): `X` يستورد `_backward_scans` من ملف integration. أقبلها لأن لهذا الاستيراد سابقة في ثلاثة ملفات وحدة، ولا أثر جانبياً له. التنظيف اختياري: نقل المساعد إلى وحدة تحت `tests/`.
- **N‑3** (وثائق، سابقة): `CLAUDE.md` يقول 8 عقود والعدد الحقيقي 9. ونص AC‑5.6 يقول «الثمانية» أيضاً. أقبلها، ويُصحَّح `CLAUDE.md` في commit وثائق مستقل بموافقة بشرية، لأنه ملف تعليمات.
- **انحرافات التصميم** د‑ت5 ود‑ت6 (حذف الاختبار الذي كان يثبّت غياب حد `int4`) مقبولة من جهة المنتج. والحد مقصود بقرار المالك (س‑5)، ويحرسه سلوكياً `SQL` و`R` و`RLS`.

## الحكم النهائي

**مقبول (مشروط)**: المعايير الـ 37 كلها Must. منها 29 ✅ بدليل محلي كامل، ولا معيار 🔴. القبول يصير نهائياً بشرطين قبل بوابة ② (الدمج)، كما في سابقتي ث‑1 وث‑3:

1. **CI job `integration` أخضر على الـ PR.** يغلق هذا AC‑5.2 وAC‑5.4، والأجزاء الحية من AC‑1.5 و2.1 و4.2 و4.4، ومعها AC‑6.5. إن فشل:
   - AC‑5.2 (المخطِّط لا يختار `uq_msg_seq` للخلف تحت RLS) يعود إلى **software-architect مع data-engineer**. ولا يُضاف فهرس بلا موافقة المالك (`stories.md` US‑5).
   - AC‑5.4، أو جزء حي من AC‑1.5 أو 2.1 أو 4.2 أو 4.4، يعود إلى **backend-developer**.
2. **تعديل `gaps.md` بعد أن يخضرّ CI** (AC‑6.3، `design.md` §6، الخطوة 9): فقرة «الإصلاح» في §ث‑5 بتاريخها، وسطر الحالة (السطر 11) يذكر ث‑5 مُصلحة، وتصحيح سطر `common.invalid_cursor` (السطر 90). يكتبه المنسّق، وأتحقق منه بقراءة الفرق قبل بوابة ②. إن لم يُكتب، يبقى AC‑6.3 🔴 ويعود إلى المنسّق (مرحلة الوثائق).

</div>
