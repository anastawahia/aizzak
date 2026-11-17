<div dir="rtl">

# خطة الاختبار: ث‑7 التثبيت على ملف محذوف (`data-search-deleted-pins`)

الفرع `feat/data-search-deleted-pins` على `master` `137874c`. الخلفية فقط. اختبارات `tests/integration/` لم تُشغَّل محلياً (الحزمة الحية مستعملة، CLAUDE.md)، وتُشغَّل في CI job `integration`.

المفاتيح: `UC` = `tests/unit/test_conversations_use_cases.py`، `R` = `tests/unit/test_api_conversations_router.py`، `O` = `tests/unit/test_orchestrator.py`، `CR` = `tests/unit/test_composition_root.py`، `LIVE` = `tests/integration/test_deleted_pins_live.py` (يُجمع: 5 اختبارات، لا يُشغَّل محلياً).

## 1. مصفوفة التغطية

كل اختبار مُقرأ والتأكد أنه يفحص المعيار فعلاً (لا ينجح بدون الميزة). ونُفِّذ فحص طفرات (§3).

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑1.1 | وحدة + واجهة + CI | `UC::test_a_deleted_file_drops_out_of_the_list_with_its_original_pinned_at`، `R::test_a_deleted_file_is_no_longer_listed_as_pinned` (الجسد والـ`meta` ومعرّف A غائب)، `LIVE` | ✅ |
| AC‑1.2 | واجهة | `R::test_all_pins_deleted_lists_an_empty_page` | ✅ |
| AC‑1.3 | وحدة | `UC::test_live_pins_keep_their_order_and_their_timestamps` | ✅ |
| AC‑1.4 | وحدة + واجهة | `UC::test_a_deleted_thread_lists_only_its_live_pins`، `R::test_a_soft_deleted_thread_lists_only_its_live_pins` | ✅ |
| AC‑1.5 | واجهة | `R::test_a_fully_live_list_is_byte_identical` (مقارنة بايتات) | ✅ |
| AC‑1.6 | واجهة | `R::test_the_list_guards_run_before_any_files_read` (401/404/404 مستأجر آخر/403 و`bulk_calls == []`) | ✅ |
| AC‑2.1 | وحدة (منسّق) | `O::test_a_deleted_pin_leaves_the_rest_as_the_turn_scope` | ✅ |
| AC‑2.2 | وحدة (منسّق) | `O::test_no_live_pin_left_widens_the_turn_to_the_threads_space` (يفحص نص الاعتذار الثابت لأن إطار `final` بلا مفتاح `fallback`: الملاحظة مُتحقَّق منها، مقبولة، والنموذج استُدعي) | ✅ |
| AC‑2.3 | وحدة (منسّق) | `O::test_pinning_again_after_the_widening_narrows_the_next_turn` | ✅ |
| AC‑2.4 | وحدة | `UC::test_the_list_and_the_turn_scope_are_one_answer` (243 حالة بالتعداد، وتتحقق من المساواة ومن المحتوى معاً) | ✅ |
| AC‑2.5 | وحدة (منسّق) | `O::test_the_widened_scope_never_leaves_the_threads_space` | ✅ |
| AC‑2.6 | وحدة | `UC::test_a_live_unindexed_pin_stays_listed_and_scoped`، `O::test_a_live_unindexed_pin_still_falls_back` | ✅ |
| AC‑2.7 | وحدة | `O::test_a_never_pinned_thread_is_unscoped_and_reads_no_files` + القائم `test_an_unpinned_thread_gives_the_run_an_empty_scope` | ✅ |
| AC‑3.1 | وحدة | `UC::test_a_directly_written_orphan_row_is_hidden_and_left_in_place` | ✅ |
| AC‑3.2 | وحدة + CI | `UC::test_a_pin_on_an_id_files_never_knew_is_hidden`، `LIVE::test_a_row_naming_no_file_at_all_is_not_listed` | ✅ |
| AC‑3.3 | واجهة + وحدة QA | `R::test_unpinning_a_deleted_files_pin_is_204_twice_and_drops_the_row`، و`UC::test_qa_unpinning_is_idempotent_for_live_orphan_and_unknown_ids` (جديد) | ✅ |
| AC‑3.4 | CI فقط | `LIVE::test_deleting_the_file_twice_keeps_the_list_at_the_rest` | ✅ (جُمع، لم يُشغَّل محلياً) |
| AC‑3.5 | وحدة + واجهة | `UC::test_pinning_a_deleted_file_with_an_orphan_row_is_still_422`، `R::test_pinning_a_deleted_file_with_an_orphan_row_is_422_on_the_wire` | ✅ |
| AC‑3.6 | وحدة | `UC::test_orphans_hold_no_place_under_the_scope_bound`، `test_the_bound_counts_and_reports_live_pins_only`، `test_below_the_bound_pinning_reads_no_bulk_files`، وجديد QA: `test_qa_the_scope_bound_at_49_50_51_stored_rows_with_orphans` (7 حالات حدّية)، `test_qa_repinning_a_live_pin_at_the_live_bound_still_passes_with_orphans`، `test_qa_a_failed_check_at_the_bound_refuses_the_pin_and_writes_nothing` | ✅ |
| AC‑3.7 | مراجعة + CI | `git diff --stat master -- migrations src/app/framework/di/file_deletion.py` فارغ (تحققتُ). و`LIVE::test_the_cascade_leaves_the_pin_row_and_the_list_hides_it` | ✅ (مراجعة محلية، والحي CI) |
| AC‑4.1 | وحدة (منسّق) | `O::test_a_file_deleted_mid_turn_never_widens_that_turn` | ✅ |
| AC‑4.2 | وحدة | `UC::test_a_file_deleted_between_the_check_and_the_write_never_shows` (الترتيبان) | ✅ |
| AC‑4.3 | واجهة + وحدة | `R::test_a_failed_files_read_is_a_500_that_names_no_file` (معامَل بـ`AppError` و`RuntimeError`)، `UC::test_a_not_found_from_the_files_seam_is_never_an_unknown_thread`، `test_another_failure_of_the_files_seam_passes_through_untouched`، وجديد QA: `test_qa_a_failed_check_never_turns_into_an_empty_unscoped_answer` | ✅ |
| AC‑4.4 | واجهة + منسّق | `R::test_a_failed_files_read_fails_the_turn_before_anything_is_written` (stream true/false، لا رسالة، `_RUNS == []`، وإعادة بالمفتاح نفسه تنجح = حُرّر)، `O::test_a_failed_files_read_reserves_nothing_and_never_reaches_knowledge` | ✅ |
| AC‑5.1 | وحدة | `UC::test_a_thread_with_no_pins_never_asks_the_files_module` | ✅ |
| AC‑5.2 | وحدة | `UC::test_one_bulk_read_per_list_and_per_turn_whatever_the_size` (1، 2، 50) | ✅ |
| AC‑5.3 | CI فقط (+ عزل بوحدة QA) | `LIVE::test_a_pin_on_a_file_this_tenant_deleted_is_gone_and_another_tenant_sees_nothing`. وجديد QA: `UC::test_qa_the_files_check_runs_under_the_callers_own_context` (يثبت تمرير سياق المنادي) | ✅ (الحي CI) |
| AC‑5.4 | بوابة + مراجعة | `lint-imports`: 9 عقود kept، 0 broken. `git diff master -- .importlinter` فارغ. `grep app.modules.files` في `conversations` يعيد تعليق docstring موجوداً على `master` (`ports/files.py:60`) وليس استيراداً | ✅ |
| AC‑5.5 | بوابة + وحدة | `mypy src` نظيف. `CR::test_the_pin_list_and_bound_share_one_files_seam` | ✅ |
| AC‑5.6 | مراجعة | `git diff --stat master -- src/app/modules/files` فارغ (تحققتُ) | ✅ |
| AC‑6.1 | مراجعة + عقد | `openapi.yaml` الأوصاف الثلاثة تحمل النقاط (قرأتها)، و`test_api_conventions` ضمن الحزمة الخضراء | ✅ |
| AC‑6.2 | مراجعة | `01`/`02`/`03` محدّثة (diff قُرئ)، وgrep العبارات القديمة في `src/app/modules/conversations` فارغ، و`0003` بلا تعديل | ✅ |
| AC‑6.3 | مؤجل بقرار | `gaps.md` بعد اخضرار CI (خطوة 7 من التصميم). ليس عيباً، لكنه **مفتوح** | ⏸ (ليس 🔴: مؤجل بخطة) |
| AC‑6.4 | مراجعة | مستودع Android فيه تعديلات غير محفوظة سابقة (مذكورة في `status.md`)، ولا commit فيه من هذه الميزة. غير مُتحقَّق من لمسه بدقة قبل/بعد لعدم وجود لقطة سابقة | ✅ (بتحفظ) |
| AC‑6.5 | بوابة | §2: 1–5 محلياً و`test-rules.sh` خضراء. CI يبقى للدمج | ✅ محلياً، CI معلّق |

**العدّ (35 معياراً):** محلي بوحدة/واجهة: 27، بوابة/مراجعة (محلي): 6 (3.7، 5.4، 5.6، 6.1، 6.2، 6.4)، CI فقط: AC‑3.4 و AC‑5.3 (مع تغطية محلية جزئية لـ5.3 وLIVE لـ3.2/3.7/1.1)، مؤجل بقرار: AC‑6.3. لا معيار 🔴 بلا اختبار.

## 2. نتائج بوابات المشروع

```
1. ruff format --check .        -> 1011 files already formatted            exit 0
2. ruff check .                 -> All checks passed!                      exit 0
   (أول مرة: RUF059 في اختباري الجديد -- صُحِّح قبل الـcommit)
3. mypy src                     -> Success: no issues found in 552 source files   exit 0
4. lint-imports                 -> Contracts: 9 kept, 0 broken.            exit 0
5. pytest -rs tests/unit tests/architecture tests/eval
                                -> انظر السطر الأخير أدناه                 exit 0
   pytest --collect-only tests/integration/test_deleted_pins_live.py -> 5 tests (لم يُشغَّل)
6. deploy/prometheus/test-rules.sh -> SUCCESS: 31 rules found; test-rules: OK   exit 0
```

pytest: 9308 passed, 37 skipped, 7 warnings in 290.37s, exit 0 (المطوّر: 9295، والفرق 13 اختباراً QA جديداً).

ما يُتخطى: اختبارات harness الحاويات (`RUN_EDGE_FILES_TEST` وغيرها) و`live_*` غير مشمولة بالتشغيل بقرار CLAUDE.md. لم تُضَف مرشِّحات.

## 3. الاختبار الاستكشافي

| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| طفرة 1: إسقاط الفلتر | `_live_pins` يعيد `list(pinned)` | فشل اختبارات | فشلت UC و8 اختبارات R/O | ✅ مُقتَل |
| طفرة 2: إزالة تحويل `NotFoundError` | `except ZeroDivisionError` | فشل | فشل `test_a_not_found_from_the_files_seam…` | ✅ |
| طفرة 3: الحد يعدّ المخزّن لا الحيّ | `len(pinned)` بدل `len(live)` | فشل | فشل `test_orphans_hold_no_place…` | ✅ |
| طفرة 4: قراءة مجمّعة دائماً | `>= 0` بدل `>= MAX` | فشل | فشل `test_below_the_bound…` | ✅ |
| طفرة 5: قراءة لمحادثة بلا تثبيت | حذف حارس `if not pinned` | فشل | فشل `test_a_thread_with_no_pins…` | ✅ |
| بعد الطفرات | `git diff -- src` | فارغ | فارغ (استُرجع الملف) | ✅ |
| 49/50/51 صفاً مع يتامى | `test_qa_the_scope_bound_at_49_50_51…` | يُحسب الحيّ فقط، وقراءة مجمّعة عند الحد فقط | كما المتوقع | ✅ |
| تثبيت مكرر عند الحد الحيّ مع يتامى | إعادة تثبيت `f0` | ينجح بالـ`created_at` الأصلي | كما المتوقع | ✅ |
| فشل القراءة المجمّعة عند الحد | `NotFoundError` أثناء التثبيت | `common.internal` ولا صف جديد | كما المتوقع | ✅ |
| حيّ/محذوف/مجهول مختلط | ترتيب المخزن، قراءة واحدة بكل المعرّفات | كما المتوقع | كما المتوقع | ✅ |
| فك التثبيت المكرر (حيّ، يتيم، غير موجود) | تكرار الحذف | بلا استثناء ولا قراءة `files` | كما المتوقع | ✅ |
| عزل المستأجر في المزيّف | `names_for_files` بسياق w1 ثم w2 | كل سياق يرى ملفاته فقط | كما المتوقع | ✅ |
| اسم عربي/emoji، دخل ضخم | القائمة تحمل `file_id` فقط، فلا أثر للاسم | لا تغيير | لم يُختبر آلياً (خارج النطاق بحسب stories) | — |
| RTL و375px | لا واجهة في هذا المستودع | — | غير منطبق | — |
| حالة quarantined/غير ready | غير قابلة للوصول في المزيّفات (الفرق «غير في `ready`» فقط)، والمنفذ يوثّق أن الغياب هو الجواب | — | تغطيها `LIVE` وتعريف `names_for_files` في وحدة `files` (القائمة `test_ready_names_…`) | ملاحظة |
| تكرار المعرّفات | PK `(conversation_id, file_id)` يمنعه | — | لا يُمكن إنتاجه | — |

## 4. الأخطاء المكتشفة

لا أخطاء في كود المنتج.

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| — | لا شيء | — | — | — |

ملاحظات غير معيبة:
- `mypy` على `tests/integration/test_deleted_pins_live.py` وحده يشتكي (قول المطوّر)؛ البوابة `mypy src` خضراء، وهو غير مشمول بالبوابة.
- التغطية الحية (AC‑3.4، AC‑5.3، LIVE) لم تُشغَّل هنا، وتبقى شرط اخضرار CI `integration` قبل الدمج.
- AC‑6.3 (`gaps.md`) مؤجل حتى اخضرار CI، ويجب إغلاقه قبل اعتبار الميزة منتهية.

## 5. الحكم

**PASS** — البوابات 1–4 و6 خضراء والوحدات/المعمارية/التقييم خضراء، وكل معيار Must مغطى باختبار يفحصه فعلاً (5 طفرات قُتلت)، ولا عيوب في كود المنتج. مشروط بـ: CI `integration` أخضر (AC‑3.4، 5.3) ثم إغلاق AC‑6.3.

</div>
