<div dir="rtl">

# قبول مالك المنتج: ث‑7 التثبيت على ملف محذوف (`data-search-deleted-pins`)

- **الفرع:** `feat/data-search-deleted-pins` على `1bb9f8e` (من `master` `137874c`).
- **المراجِع:** product-owner، 2026‑11‑17. قراءة فقط، ولم أغيّر شيفرة ولا اختباراً.
- **ما قرأتُه:** `status.md` (ق‑1 وق‑2)، و`requirements.md`، و`stories.md` (35 معياراً، كلها Must)، و`design.md`، و`test-plan.md`، و`review.md`، و`security-review.md`، و`git diff master..HEAD` على `src/` و`docs/design/`، وفرق دورة التنظيف `dcd2a8c` و`4006868`، وأقرأها بنفسي لأن المراجعَين لم يعيدا قراءتها.
- **ما شغّلتُه بنفسي:**
  - `pytest` على `test_conversations_use_cases` و`test_api_conversations_router` و`test_orchestrator` و`test_composition_root` و`test_workflow_orchestration` و`test_api_conventions`: ‏`598 passed, 1 warning in 32.04s`، exit 0.
  - `pytest tests/unit/test_file_deletion.py::test_deleting_an_already_deleted_file_runs_the_whole_cascade_again`: ناجح.
  - `pytest --collect-only tests/integration/test_deleted_pins_live.py`: ‏5 اختبارات. لم يُشغَّل، لأن المكدس الحي مستعمل (CLAUDE.md).
  - `mypy tests/integration/test_deleted_pins_live.py`: الملف الجديد نظيف بعد التنظيف، والخطآن الباقيان في `tests/integration/conftest.py:965,967`، وهما سابقان للفرع.
  - `git diff --stat master..HEAD` على `migrations` و`file_deletion.py` و`src/app/modules/files` و`src/app/agents` و`.importlinter` و`deploy` و`.github` و`pyproject.toml` و`.env.example`: فارغ.
- **بوابات المنسّق بعد التنظيف:** ‏ruff format/check خضراء، و`mypy src` بلا أخطاء في 552 ملفاً، و`lint-imports` ‏9 kept و0 broken، و`pytest tests/unit tests/architecture tests/eval` ‏`9308 passed, 37 skipped, 7 warnings in 376.82s` exit 0. و`test-rules.sh` OK (31 قاعدة) من QA.

المفاتيح: `UC` = `tests/unit/test_conversations_use_cases.py`، و`R` = `tests/unit/test_api_conversations_router.py`، و`O` = `tests/unit/test_orchestrator.py`، و`CR` = `tests/unit/test_composition_root.py`، و`LIVE` = `tests/integration/test_deleted_pins_live.py` (يعمل في CI `integration` فقط).

قاعدة الحكم: المعيار الذي لا دليل منفَّذاً له بعد يأخذ 🔴، حتى لو لم يكن فيه عيب. لذلك تحمل الأربعة المعلّقة على CI أو على `gaps.md` العلامة 🔴 «معلّق»، لا «معيب».

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑1.1 | `UC::test_a_deleted_file_drops_out_of_the_list_with_its_original_pinned_at`. و`R::test_a_deleted_file_is_no_longer_listed_as_pinned`: يقارن الجسد كاملاً بـ`{"data":[{B, pinned_at الأصلي}], "meta":{"next_cursor":null,"limit":1}}`، ويؤكد غياب A من النص. ناجحان (598) | ✅ مقبول |
| AC‑1.2 | `R::test_all_pins_deleted_lists_an_empty_page`: الجسد `{"data":[],"meta":{"next_cursor":null,"limit":0}}`. ومعه `LIVE::test_no_live_pin_left_reads_as_an_unscoped_thread` في CI | ✅ مقبول |
| AC‑1.3 | `UC::test_live_pins_keep_their_order_and_their_timestamps` | ✅ مقبول |
| AC‑1.4 | `UC::test_a_deleted_thread_lists_only_its_live_pins`، و`R::test_a_soft_deleted_thread_lists_only_its_live_pins` (DELETE المحادثة 204، ثم القائمة `["file-BB"]`) | ✅ مقبول |
| AC‑1.5 | `R::test_a_fully_live_list_is_byte_identical`: يقارن `response.content` بايتاً ببايت بترميز `Page[ConversationFileOut]` كما على `master`، ويؤكد أن المفاتيح `{data, meta}` و`{next_cursor, limit}` و`{file_id, pinned_at}` فقط | ✅ مقبول |
| AC‑1.6 | `R::test_the_list_guards_run_before_any_files_read`: ‏401 بلا توكن، و404 `common.not_found` لمعرّف مجهول، و404 لمستأجر آخر، و403 `authz.forbidden` بلا صلاحية، و`bulk_calls == []` | ✅ مقبول |
| AC‑2.1 | `O::test_a_deleted_pin_leaves_the_rest_as_the_turn_scope`: ‏`scopes == [("B",)]`، و`spaces[0] == S`، والاستشهاد `doc-B` | ✅ مقبول |
| AC‑2.2 | `O::test_no_live_pin_left_widens_the_turn_to_the_threads_space`: ‏`scopes == [None]`، و`spaces == {S}`، والاستشهاد `doc-C`، والنموذج استُدعي (`streams == 1`). وبعد `dcd2a8c` صار التأكيد إيجابياً (`text == "answer"`) بدل النفي `not startswith`. والفحص بالنص لا بمفتاح `path`، لأن إطار `final` لا يحمل `fallback` (ملاحظة QA) | ✅ مقبول |
| AC‑2.3 | `O::test_pinning_again_after_the_widening_narrows_the_next_turn`: النطاق الأخير `("C",)`، والقائمة `["C"]` | ✅ مقبول |
| AC‑2.4 | `UC::test_the_list_and_the_turn_scope_are_one_answer`: تعداد كامل لـ243 حالة (5 ملفات × حي/محذوف/يتيم مكتوب مباشرة). يؤكد المساواة بالترتيب، ويؤكد أن المحتوى هو الأحياء وحدها | ✅ مقبول |
| AC‑2.5 | `O::test_the_widened_scope_never_leaves_the_threads_space`: ‏`scopes == [None]`، و`spaces == {S}`، و`doc-X` من المساحة الأخرى لا يُستشهد به | ✅ مقبول |
| AC‑2.6 | `UC::test_a_live_unindexed_pin_stays_listed_and_scoped`، و`O::test_a_live_unindexed_pin_still_falls_back`: ‏`scopes == [("F",)]`، والنص مساوٍ تماماً للاعتذار الثابت. والاعتذار يحمل لاحقة «There are no files in this space yet.» (`4006868`)، وتأتي من ترويسة المساحة القائمة في `rag_agent/agent.py:373` حين لا مستندات مفهرسة. وهذا ما يقوله الإنتاج أيضاً لملف لم يُفهرس، و`agents/` بلا تعديل | ✅ مقبول |
| AC‑2.7 | `O::test_a_never_pinned_thread_is_unscoped_and_reads_no_files` (`scopes == [None]`، ولا قراءة ملفات)، والاختبار القائم `test_an_unpinned_thread_gives_the_run_an_empty_scope` | ✅ مقبول |
| AC‑3.1 | `UC::test_a_directly_written_orphan_row_is_hidden_and_left_in_place`: القائمة `["B"]`، والنطاق `("B",)`، والصف اليتيم باقٍ بـ`created_at` نفسه | ✅ مقبول |
| AC‑3.2 | `UC::test_a_pin_on_an_id_files_never_knew_is_hidden`، ومعه `LIVE::test_a_row_naming_no_file_at_all_is_not_listed` في CI | ✅ مقبول |
| AC‑3.3 | `R::test_unpinning_a_deleted_files_pin_is_204_twice_and_drops_the_row`: ‏`(204, 204)`، والمخزن `["file-BB"]` بعد الأولى، والقائمة `["file-BB"]`. ومعه `UC::test_qa_unpinning_is_idempotent_for_live_orphan_and_unknown_ids` | ✅ مقبول |
| AC‑3.4 | ثلاثة أجزاء. (1) ‏204 مرتين يثبتها القائم `test_api_files_media_router.py::test_delete_is_204_idempotent_and_the_file_then_reads_404`. (2) الخطوات الثلاث في كل مرة يثبتها القائم `test_file_deletion.py::test_deleting_an_already_deleted_file_runs_the_whole_cascade_again` (`trace == ["mark","knowledge","electrical"]`)، شغّلتُه فنجح، و`file_deletion.py` بلا تعديل. (3) القائمة `[B]` بعد كل حذف هي الجزء الجديد، ودليلها الوحيد `LIVE::test_deleting_the_file_twice_keeps_the_list_at_the_rest`. قرأتُه فوجدته يفحص ذلك، لكنه **لم يُشغَّل بعد** (CI `integration`) | 🔴 معلّق: ينتظر CI |
| AC‑3.5 | `UC::test_pinning_a_deleted_file_with_an_orphan_row_is_still_422` (لا يتغير صف، و`get_readable` سُئل)، و`R::test_pinning_a_deleted_file_with_an_orphan_row_is_422_on_the_wire` (422 `common.validation_error`، والصفوف كما هي) | ✅ مقبول |
| AC‑3.6 | 40 حياً و10 يتامى ⇒ 201: `UC::test_orphans_hold_no_place_under_the_scope_bound`. و50 حياً مع يتامى ⇒ 422 بالرقم 50: `UC::test_the_bound_counts_and_reports_live_pins_only`، وبعد `dcd2a8c` يطابق الرسالة كاملة `^a conversation may pin at most 50 files \(currently 50\)$`. ومعهما QA: `test_qa_the_scope_bound_at_49_50_51_stored_rows_with_orphans` (7 حالات، مطابقة كاملة أيضاً) | ✅ مقبول |
| AC‑3.7 | `git diff --stat master..HEAD -- migrations src/app/framework/di/file_deletion.py` فارغ (تحققتُ). و`LIVE::test_the_cascade_leaves_the_pin_row_and_the_list_hides_it` يزيد دليلاً حياً في CI | ✅ مقبول |
| AC‑4.1 | `O::test_a_file_deleted_mid_turn_never_widens_that_turn`: الحذف داخل الاسترجاع، و`scopes == [("A",)]` لا `None`، ولا استشهاد، والنص مساوٍ للاعتذار، و`llm.streams == 0`. والدور التالي `None` | ✅ مقبول |
| AC‑4.2 | `UC::test_a_file_deleted_between_the_check_and_the_write_never_shows`: الترتيبان، وبعدهما القائمة `[]` والنطاق `()` | ✅ مقبول |
| AC‑4.3 | `R::test_a_failed_files_read_is_a_500_that_names_no_file` (مرة بـ`AppError` ومرة بـ`RuntimeError`): ‏500، و`problem+json`، و`common.internal`، ولا `file_id` في الجسد. ومعه `UC::test_a_not_found_from_the_files_seam_is_never_an_unknown_thread`، وبعد `dcd2a8c` يطابق نص الرسالة كاملاً | ✅ مقبول |
| AC‑4.4 | `R::test_a_failed_files_read_fails_the_turn_before_anything_is_written` (`stream` ‏true وfalse): ‏500 `problem+json`، ولا `event: accepted`، ولا رسالة مستخدم، و`_RUNS == []`، وإعادة الطلب بالمفتاح نفسه تنجح. ومعه `O::test_a_failed_files_read_reserves_nothing_and_never_reaches_knowledge`: ‏`reserved == []`، و`knowledge.scopes == []` | ✅ مقبول |
| AC‑5.1 | `UC::test_a_thread_with_no_pins_never_asks_the_files_module`: ‏`bulk_calls == []` و`calls == []` | ✅ مقبول |
| AC‑5.2 | `UC::test_one_bulk_read_per_list_and_per_turn_whatever_the_size` (1، 2، 50، ونصفها محذوف): قراءة واحدة لكل طلب بكل المعرّفات، و`get_readable` صفر | ✅ مقبول |
| AC‑5.3 | `LIVE::test_a_pin_on_a_file_this_tenant_deleted_is_gone_and_another_tenant_sees_nothing`. قرأتُه: يثبت أن القائمة بسياق W1 هي `[kept]`، وأن `names_for_files` بسياق W2 فارغة، وأن القائمة بسياق W2 ‏`NotFoundError`، والجلسة على دور `app_rw` تحت RLS. **لم يُشغَّل بعد.** والاختبار المحلي `UC::test_qa_the_files_check_runs_under_the_callers_own_context` يثبت تمرير السياق فقط، ولا يُحسب دليل عزل (صُحِّح في `dcd2a8c`) | 🔴 معلّق: ينتظر CI |
| AC‑5.4 | `lint-imports` ‏9 kept و0 broken. وعدد `ignore_imports` في `.importlinter` هو 5 على الفرع و5 على `master`، والملف بلا تعديل. و`grep app.modules.files` في `conversations` يعيد سطر docstring فقط (`ports/files.py:64`). والمعيار يقول «العقود الثمانية»، والعدد الفعلي 9، وهذا فرق سابق للفرع (`review.md`) | ✅ مقبول |
| AC‑5.5 | `mypy src` نظيف. و`composition_root.py:1342` ‏`ListConversationFiles(repository, files)`. و`CR::test_the_pin_list_and_bound_share_one_files_seam`: النسخة نفسها في القائمة والتثبيت، وهي `FilesQueryService`، والمنسّق يقرأ `ListConversationFiles` نفسه | ✅ مقبول |
| AC‑5.6 | `git diff --stat master..HEAD -- src/app/modules/files` فارغ (تحققتُ) | ✅ مقبول |
| AC‑6.1 | `openapi.yaml`: وصف `listConversationFiles` يحمل النقطتين (1) و(2). و`pinConversationFile` يحمل (3)، أي 422 لملف محذوف، ويضيف أن الصف لا يُحسب في الخمسين. و`unpinConversationFile` يحمل (4)، أي 204. وعبارة «retrieval simply finds nothing for it» غائبة (grep فارغ). و`test_api_conventions` ناجح ضمن الـ598 | ✅ مقبول |
| AC‑6.2 | `03-api-spec.md` §1 و`01-data-model.md` §2.4 محدَّثان، و`02-port-contracts.md` §2 فيه `ReadableFiles.names_for_files`. وgrep عبارات ما قبل ث‑7 («not re-checked on read»، «deliberately NOT repeated on read»، «leaves the pin standing»، «simply finds nothing») في `conversations` و`api` و`openapi.yaml` فارغ. وفرق `migrations` فارغ، فملف `0003` بلا تعديل | ✅ مقبول |
| AC‑6.3 | `git diff master..HEAD -- docs/delivery/data-search-android/gaps.md` فارغ. فقرة الإصلاح وتنبيه `backend-reply.md:146` وسطر الحالة لم تُكتب بعد، وهي مؤجلة إلى ما بعد CI (`design.md` §6 الخطوة 7). وبحسب ق‑2 يجب أن يحمل الملف أيضاً SR‑1 بنداً مفتوحاً | 🔴 معلّق: مؤجل بخطة |
| AC‑6.4 | في مستودع Android لا ملف معدَّل منذ 2026‑11‑17 00:00 (`find -newermt` فارغ)، و`backend-reply.md` آخر تعديل له 2026‑11‑09. والتعديلات غير المحفوظة فيه سابقة لهذه الميزة | ✅ مقبول |
| AC‑6.5 | البوابات 1–5 خضراء محلياً (تشغيل المنسّق بعد التنظيف)، و`test-rules.sh` OK (QA). أما CI job `integration`، ومنه AC‑5.3، فلم يُشغَّل بعد | 🔴 معلّق: ينتظر CI |

**العدّ:** 35 معياراً، كلها Must. ‏31 ✅ بدليل منفَّذ أو مراجعة قابلة للإعادة. و4 🔴 معلّقة بلا عيب: AC‑3.4 وAC‑5.3 وAC‑6.5 تنتظر CI `integration`، وAC‑6.3 تنتظر كتابة `gaps.md`. ولا معيار مرفوض لعيب في الشيفرة أو الاختبار.

## مراجعة دورة التنظيف (`dcd2a8c`، `4006868`)

قرأتُ الفرق كاملاً:
- **لا تغيير سلوك في `src`.** التعديل الوحيد هناك حذف «Missing thread ⇒ ``()``» المكرر من docstring `ConversationService.pinned_files` (التحسين 5).
- **المهمة 1:** صُحِّح الاختبار. صار `test_qa_the_files_check_runs_under_the_callers_own_context` يقرأ محادثة W1 بسياق W1 ويؤكد `seen == ["w1"]`، وdocstring يقول «caller's own ctx» فقط. وسطر AC‑5.3 في `test-plan.md` صُحِّح أيضاً.
- **التحسينات 2 و3 و4:** طُبِّقت كلها. رسالة الحد تُطابَق كاملة، ورسالة الفشل تُطابَق كاملة، والاعتذار يُطابَق بـ`==`، وAC‑2.2 يؤكد إيجاباً `text == "answer"`.
- **التحسين 6:** طُبِّق. ‏`_RecordingVectors` صار `_NoopVectors`، وحُذف `type: ignore` الزائد. ونُقل `type: ignore[arg-type]` من سطري `_NoopOutbox`/`_NoopUnitOfWork` إلى سطر `PurgeFileKnowledge`، والملف الجديد صار نظيفاً تحت mypy وحده. وهذا لا أثر له في التشغيل.
- **`4006868`:** أصلح اختبارين فشلا بعد `dcd2a8c`، لأن الاعتذار الحقيقي يحمل لاحقة المساحة الفارغة. تحققتُ أن اللاحقة من الشيفرة القائمة (`agent.py:373`)، لا تغيير في السلوك.

## ملاحظات غير حاجبة مقبولة

أقبل هذه بالاسم، ولا تمنع القبول:
- **SR‑1** (منخفضة، CWE‑770): لا سقف لعدد الصفوف المخزنة، لأن الأيتام لا تُنظَّف والحد يعدّ الأحياء فقط. قرار المالك ق‑2: يوثَّق فقط ولا يُصلح الآن. **شرط:** يُكتب بنداً مفتوحاً في `gaps.md`، مع أن افتراض NFR‑3 (N ≤ 50) لم يعد صحيحاً للصفوف المخزنة، ومع اقتراح الإصلاح المؤجَّل (حذف أيتام المحادثة عند التثبيت على الحد). وهذا جزء من AC‑6.3.
- **SR‑2** (معلومة): الجواب الفارغ الصامت من `files` يوسّع النطاق داخل المساحة نفسها. ولا يمكن أن يحدث ذلك اليوم إلا بخطأ في إعداد RLS. والعدّاد المقترح اختياري ومؤجل.
- **SR‑3** (معلومة، سابقة للفرع): نص `_translate` يقول «persisting» في عملية قراءة، والسجل يحمل معرّفات فقط. مؤجلة.
- **SR‑4** (معلومة، قرار س‑1): التوسيع الصامت حين يُحذف آخر ملف مثبت سلوكٌ اختاره المالك، والعقد يوثّقه. إظهار ذلك للمستخدم مسؤولية عميل Android.
- **`review.md`:** docstring `_pinned_scope` في `src/app/agents/orchestrator.py` ما زال يقول «the whole workspace corpus». هذا سابق للفرع (س‑32)، والتصميم منع لمس `agents/`. يُصحَّح في commit مستقل إن رأى المالك ذلك.
- **`review.md`:** ‏`lint-imports` يعدّ 9 عقود وCLAUDE.md وAC‑5.4 يقولان 8. فرق سابق للفرع.
- **AC‑2.2 وAC‑2.6 تُفحصان بنص الاعتذار** لا بمفتاح `path`، لأن إطار `final` لا يحمل هذا المفتاح. التحقق بالنص الكامل كافٍ.
- **عملية:** ‏`dcd2a8c` سُجِّل وفيه اختباران أحمران، وأصلحهما `4006868` بعد تسع دقائق. هذا يخالف «Commit only when all gates are green» في CLAUDE.md، لكن رأس الفرع أخضر. لا أطلب إعادة كتابة التاريخ، ويُستحسن الدمج squash إن رأى المالك ذلك.

## الحكم النهائي

**مقبول بشروط:** ليس «مقبولاً» نهائياً بعد. لا عيب في الشيفرة ولا في الاختبارات، و31 من 35 معياراً Must عليها دليل. لكن 4 معايير Must لا دليل منفَّذاً لها بعد، والقبول النهائي يشترط أن تكون كل معايير Must ✅. لذلك تبقى الشروط التالية قبل بوابة ② (الدمج):

1. **CI job `integration` أخضر على PR هذا الفرع.** تمر فيه الاختبارات الخمسة في `tests/integration/test_deleted_pins_live.py` فعلاً، لا تخطياً (`REQUIRE_LIVE=1`). هذا يغلق AC‑3.4 وAC‑5.3 وAC‑6.5.
   - إن فشل أحدها: يعود العمل إلى **المرحلة 6 (الخلفية)**، ثم الجودة (8) قبل القبول من جديد.
2. **`docs/delivery/data-search-android/gaps.md` محدَّث** في commit وثائق على الفرع نفسه، بعد اخضرار CI (`design.md` §6 الخطوة 7). يحمل:
   - فقرة «الإصلاح» في §ث‑7 بتاريخها؛
   - تنبيه العميل أن `backend-reply.md:146` لم يعد صحيحاً؛
   - سطر الحالة (`gaps.md:11`) يذكر ث‑7 مُصلحة ولا يقول إنها تغيّر قاعدة البيانات؛
   - **SR‑1 بنداً مفتوحاً** كما في ق‑2.

   هذا يغلق AC‑6.3. وإن نقص: يعود إلى **المرحلة 6 (الخلفية)**، لخطوة الوثائق وحدها.
3. يعيد المنسّق فحص المعايير الأربعة (AC‑3.4، AC‑5.3، AC‑6.3، AC‑6.5) بدليلها، ويثبته في `status.md`. أو يستدعيني لتحويلها إلى ✅. وبعدها يصير الحكم **مقبول**.

</div>
