<div dir="rtl">

# قبول مالك المنتج: الحجم من ملفّ بدل أرقامٍ ثابتة (compose-sizes)

- **النطاق:** الفرع `feat/compose-sizes`، الرأس `129e39b`. الاختبارات تحت `tests/unit/`، والخريطة في `test-plan.md` §1.
- **البوّابات على `129e39b` (أعادها المنسّق، `status.md`):** ruff format/check خضراء، mypy خضراء، lint-imports 8 kept، pytest `5582 passed, 1 skipped` exit 0. التخطّي سابقٌ للفرع ويتعلّق بـPATH (N‑1).
- **تحقّقتُ بنفسي:** وجدتُ بـgrep 13 اسم اختبارٍ من الخريطة، وكلّها موجودة. وراجعتُ `git show 129e39b`، فوجدتُ إصلاحاته تطابق CR‑1…CR‑6 وأمن م‑1.

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑1.1 | `test_compose_sizes.py::test_every_limit_and_replica_count_is_a_named_variable` | ✅ مقبول |
| AC‑1.2 | `test_compose_sizes.py::test_resolve_absent_given_and_empty` و`…ignores_the_process_environment` و`…refuses_every_other_form` | ✅ مقبول |
| AC‑1.3 | الاختبارات المنقولة، و`test_resource_budget.py::test_the_runbook_ledger_carries_the_numbers_this_module_computes` | ✅ مقبول |
| AC‑1.4 | `test_resource_budget.py::test_the_script_and_this_module_agree`، ومقارنة المراجع القديم بالجديد (`review.md`) | ✅ مقبول |
| AC‑1.5 | `test_compose_sizes.py::test_compose_resolves_limits_as_the_helper_does` | ✅ مقبول |
| AC‑1.6 | يدويّ: البصمتان متطابقتان، وسجّلها المنسّق (`status.md` الصفّ 6) | ✅ مقبول |
| AC‑1.7 | `test_rolling_deploy.py::test_the_target_count_comes_from_running_containers` | ✅ مقبول |
| AC‑2.1 | `test_postgres_tuning.py::test_each_size_file_includes_the_base_first_and_sets_only_memory` | ✅ مقبول |
| AC‑2.2 | `test_postgres_tuning.py::test_the_sizes_directory_is_mounted_read_only` | ✅ مقبول |
| AC‑2.3 | `test_postgres_tuning.py::test_each_size_fits_its_postgres_container`، ومعه حالة سالبة | ✅ مقبول |
| AC‑2.4 | `test_postgres_tuning.py::test_each_size_stays_inside_its_host_and_shm` | ✅ مقبول |
| AC‑2.5 | `test_redis_split.py::test_maxmemory_is_a_variable_with_todays_default` و`…test_each_size_keeps_container_headroom`، وأصلح CR‑5 الفحص في `129e39b` | ✅ مقبول |
| AC‑2.6 | `test_connection_budget.py::test_every_size_stays_inside_the_connection_ceilings` | ✅ مقبول |
| AC‑2.7 | مؤجَّل إلى 7.3 بقرار المالك Q‑6 (`status.md`)، لأنّ المالك لا يريد تشغيل أيّ حاوية هنا | ⏭ مؤجَّل، مقبول |
| AC‑3.1 | `test_compose_sizes.py::test_a_size_file_has_a_header_and_exactly_the_derived_keys` | ✅ مقبول |
| AC‑3.2 | الاختبار نفسه، و`test_the_postgres_config_path_names_a_file_that_exists` | ✅ مقبول |
| AC‑3.3 | `test_compose_sizes.py::test_every_value_has_its_grammar` | ✅ مقبول |
| AC‑3.4 | `test_compose_sizes.py::test_large_is_the_default_except_what_gpu_mode_needs` | ✅ مقبول |
| AC‑3.5 | `test_compose_sizes.py::test_sizes_never_grow_downwards` | ✅ مقبول |
| AC‑3.6 | `test_compose_resolves_limits_as_the_helper_does` و`test_the_gpu_overlay_pins_embedding_at_any_size`، و`compose config -q` نجح بالرمز 0 للأحجام الثلاثة (`test-plan.md` §3) | ✅ مقبول |
| AC‑3.7 | مؤجَّل إلى 7.3 في `stories.md` نفسه («لا يُثبَت هنا»)، فهو يحتاج خادماً بالحجم، والمالك وضع قيد «المكدّس لا يعمل» | ⏭ مؤجَّل، مقبول |
| AC‑4.1 | `test_resource_budget.py::test_the_script_and_this_module_agree_per_size` | ✅ مقبول |
| AC‑4.2 | `test_resource_budget.py::test_the_script_refuses_an_unknown_or_missing_size`، و`test_size_names_are_validated_before_any_path_is_built` (أُضيف في `129e39b`) | ✅ مقبول |
| AC‑4.3 | `test_resource_budget.py::test_every_size_fits_the_host_its_header_names` | ✅ مقبول |
| AC‑4.4 | `test_resource_budget.py::test_a_size_over_its_host_fails_naming_it` | ✅ مقبول |
| AC‑4.5 | `test_resource_budget.py::test_the_runbook_size_table_carries_the_numbers_this_module_computes` | ✅ مقبول |

## ملاحظات غير حاجبة مقبولة
- **AC‑2.7 وAC‑3.7:** مؤجّلان إلى الخطوة 7.3 بقرار المالك. ليسا ناقصين.
- **م‑2 (أمن، معلومة):** `small` قد يُقتل بـOOM تحت الحمل، والمالك قبل ذلك (ق‑0). شرطه ألّا يُستعمل `small` على Pod مكشوفٍ ببيانات حقيقيّة دون تسجيله في قائمة أمن staging.
- **N‑1:** اختبار `test_import_contracts.py` يتخطّى حين لا يكون `.venv/bin` في PATH. هذا سابقٌ للفرع وخارج النطاق.
- **N‑2:** `--size` دون `--host-*` يقيس المضيف المكتشَف، وهذا حسب التصميم.
- **إصلاحات `129e39b`:** لم تُعَد المراجعتان بعدها. أقبل ذلك لأنّ كلّ النتائج كانت غير حاجبة، ولأنّ الفرق يطابقها:
  - CR‑1: الأمر الكامل `--env-file .env --env-file …` في 08 وفي رأس ملفّات الأحجام.
  - CR‑2: العدّاد صار ٥/٩/١٤.
  - CR‑3: بطاقة 3.3 لم تعد تقول «بلا مراقبة».
  - CR‑4: `ValueError` يخرج الآن بالرمز 2.
  - CR‑5: الفحص يمرّ على `resolve()`.
  - CR‑6 وم‑1: يُتحقَّق من الاسم بـ`^[a-z0-9-]+$` وبوجوده ضمن `available`.

## الحكم النهائي
**مقبول.** كلّ معايير القصص الأربع من نوع Must. فيها 24 معياراً ✅ بدليل، ومعياران مؤجّلان بقرار المالك المسجَّل. البوّابات خضراء على `129e39b`، والمراجعتان PASS دون أيّ نتيجةٍ مفتوحة حاجبة.

</div>
