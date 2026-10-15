<div dir="rtl">

# خطة الاختبار: compose-sizes (الفرع `feat/compose-sizes`، الرأس `b673e5e`)

## 1. مصفوفة التغطية
الملفات تحت `tests/unit/`. لم يلزم كتابة اختبارٍ جديد: كلّ معيار مغطّى.

| AC | الملف::الاختبار | الحالة |
|---|---|---|
| 1.1 | `test_compose_sizes.py::test_every_limit_and_replica_count_is_a_named_variable` | ✅ |
| 1.2 | `test_compose_sizes.py::test_resolve_absent_given_and_empty`، `…ignores_the_process_environment`، `…refuses_every_other_form` | ✅ |
| 1.3 | الملفات المنقولة + `test_connection_budget.py`/`test_resource_budget.py::test_the_runbook_ledger_carries_the_numbers_this_module_computes` | ✅ |
| 1.4 | `test_resource_budget.py::test_the_script_and_this_module_agree` | ✅ |
| 1.5 | `test_compose_sizes.py::test_compose_resolves_limits_as_the_helper_does` (حجم × cpu/gpu) | ✅ |
| 1.6 | مالك: بصمتان متطابقتان (`status.md`، المنسّق) | ✅ يدوي |
| 1.7 | `test_rolling_deploy.py::test_the_target_count_comes_from_running_containers` | ✅ |
| حوافّ US‑1 | `test_compose_sizes.py::test_empty_falls_back_malformed_is_refused` | ✅ |
| 2.1 | `test_postgres_tuning.py::test_each_size_file_includes_the_base_first_and_sets_only_memory` | ✅ |
| 2.2 | `test_postgres_tuning.py::test_the_sizes_directory_is_mounted_read_only` | ✅ |
| 2.3 | `test_postgres_tuning.py::test_each_size_fits_its_postgres_container` (+ حالة سالبة) | ✅ |
| 2.4 | `test_postgres_tuning.py::test_each_size_stays_inside_its_host_and_shm` | ✅ |
| 2.5 | `test_redis_split.py::test_maxmemory_is_a_variable_with_todays_default`، `…test_each_size_keeps_container_headroom` | ✅ |
| 2.6 | `test_connection_budget.py::test_every_size_stays_inside_the_connection_ceilings` | ✅ |
| 2.7 | مؤجَّل إلى 7.3 (قرار المالك Q‑6) | ⏭ مؤجَّل |
| 3.1 | `test_compose_sizes.py::test_a_size_file_has_a_header_and_exactly_the_derived_keys` | ✅ |
| 3.2 | نفسه + `test_the_postgres_config_path_names_a_file_that_exists` | ✅ |
| 3.3 | `test_compose_sizes.py::test_every_value_has_its_grammar` | ✅ |
| 3.4 | `test_compose_sizes.py::test_large_is_the_default_except_what_gpu_mode_needs` | ✅ |
| 3.5 | `test_compose_sizes.py::test_sizes_never_grow_downwards` | ✅ |
| 3.6 | `test_compose_sizes.py::test_compose_resolves_limits_as_the_helper_does`، `test_the_gpu_overlay_pins_embedding_at_any_size` | ✅ |
| 3.7 | مؤجَّل إلى 7.3 (خادم) | ⏭ مؤجَّل |
| 4.1 | `test_resource_budget.py::test_the_script_and_this_module_agree_per_size` | ✅ |
| 4.2 | `test_resource_budget.py::test_the_script_refuses_an_unknown_or_missing_size` | ✅ |
| 4.3 | `test_resource_budget.py::test_every_size_fits_the_host_its_header_names` | ✅ |
| 4.4 | `test_resource_budget.py::test_a_size_over_its_host_fails_naming_it` | ✅ |
| 4.5 | `test_resource_budget.py::test_the_runbook_size_table_carries_the_numbers_this_module_computes` | ✅ |

**NFR‑3:** كلّ ملفات الاختبار المعدّلة/الجديدة (10 ملفات، 180 اختباراً) تعمل في 21.8 ث، و`test_compose_sizes.py` وحده 2.6 ث. الزيادة أقلّ بكثير من حدّ 15 ث ✅ (قياسٌ علويّ: لم يُشغَّل `master` للمقارنة).

## 2. نتائج بوابات المشروع
```
ruff format --check .   -> 817 files already formatted            (rc 0)
ruff check .            -> All checks passed!
mypy src                -> Success: no issues found in 467 source files
lint-imports            -> Contracts: 8 kept, 0 broken.
pytest -rs tests/unit tests/architecture tests/eval
                        -> 5576 passed, 1 skipped, 7 warnings in 168.85s  (rc 0)
SKIPPED tests/architecture/test_import_contracts.py:21: lint-imports is not installed
```
لم تُشغَّل اختبارات التكامل ولا `test-rules.sh` (قيد المالك، والتنبيهات لم تتغيّر).

**التخطّي:** شرطه `shutil.which("lint-imports")` (السطر 19)، أي PATH فقط. والملف غير مُعدَّل في الفرع. وعند وضع `.venv/bin` في PATH ينجح الاختبار. إذن سابقٌ للفرع وناتج طريقة التشغيل (`.venv/bin/pytest` بلا تفعيل venv)، وليس عيباً فيه. البوّابة الفعليّة `lint-imports` خضراء.

## 3. الاختبار الاستكشافي
| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| `docker compose config -q` لكلّ حجم | `env -i`، `--env-file .env.example --env-file deploy/sizes/<s>.env`، بلا ملفّ الكرت ومعه | rc 0 | rc 0 للأحجام الثلاثة × الحالتين | ✅ |
| حجم مجهول | `resource-budget.sh --size huge` | rc 2 ويسمّي الأحجام | rc 2: `available: large medium small` | ✅ |
| تجاوز السقف | نسخة `medium.env` في scratchpad بـ`QDRANT_MEM=40g`، `SIZES_DIR=…` | يفشل ويسمّي الملفّ | rc 1، `65.41 GB … 36.61 GB over`، يسمّي الملفّ | ✅ |
| `medium` سليم على 16/32 | `--size medium --host-cpus 16 --host-memory-gb 32` | OK | `28.41 of 32.00 GB (89%)`، rc 0 | ✅ |
| `--size` بلا `--host-*` | على هذا الجهاز (10 vCPU، 12.67 GB) | يقيس المضيف المكتشف | يُبلّغ OVER للأحجام الثلاثة (متوقّع: الجهاز أصغر منها) | ✅ ملاحظة |
| مفتاح زائد/ناقص في ملفّ حجم | يغطّيه اختبار المجموعة المشتقّة المطابقة تماماً (لم يُجرَّب يدويّاً) | يفشل | — | ✅ بالقراءة |
| الشجرة | `git status` بعد التجارب | نظيفة | الملفّ المعدّل مسبقاً `status.md` فقط | ✅ |

## 4. الأخطاء المكتشفة
لا أخطاء (لا blocking ولا major). ملاحظتان غير عائقتين:
| # | الوصف | الشدة | إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| N‑1 | اختبار `test_import_contracts.py` يتخطّى إن لم يكن `.venv/bin` في PATH | minor، سابق للفرع | `.venv/bin/pytest tests/architecture` بلا تفعيل venv | مفتوح، خارج النطاق |
| N‑2 | `--size` بلا `--host-*` يستخدم المضيف المكتشف لا ما في رأس الملفّ، فيُبلّغ OVER على جهاز التطوير | minor، حسب التصميم | `deploy/resource-budget.sh --size small` | للتوثيق فقط |

## 5. الحكم
**PASS** — البوّابات الخمس خضراء، 24 معياراً مغطّى و AC‑2.7 وAC‑3.7 مؤجَّلان لـ7.3 بقرار المالك، ولا أخطاء blocking/major. لم تُضَف اختبارات ولا commits.

</div>
