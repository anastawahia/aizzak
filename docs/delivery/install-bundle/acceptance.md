<div dir="rtl">

# قبول مالك المنتج: حزمة تثبيتٍ بدل المستودع (`install-bundle`، الخطوتان 5.1 و5.2)

- **الفرع:** `feat/install-bundle`، ‏HEAD ‏`c93ab6b`، والأساس على `master` ‏`5d00b45`.
- **النطاق:** US‑1…US‑8، و47 معيار قبول (بوابة ①، Q‑3 (ب)). أمّا US‑9 وUS‑10 (5.3، `upgrade.sh`) فخارج النطاق، ولا تُقبلان هنا.
- **المراجِع:** product-owner · 2026‑10‑20.
- **ما قرأتُه:** `stories.md`، و`requirements.md` (NFR‑3 وQ‑4…Q‑10)، و`test-plan.md` (الجولة 2)، و`review.md`، و`security-review.md`، و`follow-ups.md`، و`status.md` («قرارات وتصعيدات»).

## ما تحقّقتُ منه بنفسي

1. **الشيفرة لم تتغيّر منذ آخر تشغيلٍ للبوّابات.** ‏`git diff dce2750..HEAD` لا يمسّ إلّا `docs/delivery/install-bundle/`. فنتائج البوّابات على `dce2750` تصدق على HEAD.
2. **السكربت الذي أُثبت حيّاً هو السكربت الذي في HEAD.**
   - sha256 لـ`install.sh` داخل حزمة الإثبات `aizzak-2026.10.98.tar.gz` يساوي `deploy/release/install.sh` في HEAD (`b230e249…1dab`).
   - و`VERSION` في الحزمة يسمّي `commit dce2750…`.
3. **اختبارات الميزة.** شغّلتُ `pytest -rs` على عشرة ملفّات: `test_release_bundle.py`، و`test_release_bundle_guard.py`، و`test_install_sh.py`، و`test_install_sh_tty.py`، و`test_install_bundle_docs.py`، و`test_auto_size_edges.py`، و`test_host_tuning.py`، و`test_resource_budget.py`، و`test_gpu_preflight.py`، و`test_gpu_compose_hygiene.py`.
   - النتيجة: **515 ناجحاً** في 35.9 ث، ولا متخطّى.
   - **استثنيتُ ما يشغّل `docker` الحقيقيّ**، التزاماً بقيد «لا شيء على Docker». وهي `test_install_sh_config.py`، و`test_release_bundle_guard.py::test_the_yaml_reading_agrees_with_docker_compose_config`، و`test_auto_size.py` (ينادي `docker compose version` عند تحميله). ودليلها تشغيل البوّابة 5 المسجَّل.
4. **البوّابات 1–4 على HEAD، بلا ذاكرةٍ مؤقّتة:** ‏`ruff format --check` (829 ملفّاً)، و`ruff check`، و`lint-imports` (8 kept, 0 broken)، و`mypy src` (467 ملفّاً). كلّها خضراء.
   - **والبوّابتان 5 و6** من التشغيل المسجَّل على `dce2750`: 6133 ناجحاً، و`test-rules: OK` (status.md، دورة الإعادة 2).
5. **كلّ اختبارٍ سمّته مصفوفة `test-plan.md` موجود:** 127 اسماً.
   - غاب اسمٌ واحد هو `test_the_forbidden_sentences_are_found_when_present`، وقد حلّ محلّه `test_the_key_policy_guard_flags_each_forbidden_sentence` في `b23f3d0` (test-plan.md §7).
6. **الإثبات المحلّيّ.**
   - قرأتُ `proof2.log` و`proof3.log`.
   - وقارنتُ اللقطات الأربع `run2-{containers,env,hash,volumes}-{before,after}.txt` بـ`cmp`، فوجدتُها متطابقةً كلّها: 24 حاوية، وتجزئة `.env`، و24 بصمة، و8 أحجام.
7. **NFR‑1.** ‏`git diff 5d00b45..HEAD -- docker-compose.yml docker-compose.gpu.yml deploy/sizes deploy/postgres/sizes` فيه أربعة أسطر في `docker-compose.yml`، وكلّها تعليقات، ولا شيء في الباقي. وقد قاست الجودة تساوي البصمات (test-plan.md §1.2).

## المعايير

الرموز:
- ✅ مقبولٌ بدليل.
- ⏸ مؤجَّلٌ بقرار بوابة ①: المعيار [خادم] أو [مالك]، و`stories.md` نفسه يقول «لا يُثبَت هنا». فليس 🔴، لكنّه **شرطٌ مفتوح** على 7.3 أو على ما بعد الدمج.
- 🔴 مرفوض.

مسارات الاختبارات كلّها تحت `tests/unit/`. وحيث لا يُذكر الملفّ، فهو `test_install_sh.py`.

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑1.1 | `test_release_bundle.py::test_the_file_list_is_the_listed_paths_plus_install_sh_and_version`، و`::test_install_sh_is_executable_and_is_the_one_at_the_tag` (أعدتُهما). **وحزمة الإثبات:** مجلّدٌ واحد `aizzak-2026.10.98/` فيه 46 ملفّاً، و`install.sh` بـ0755، ولا `upgrade.sh` | ✅ مقبول |
| AC‑1.2 | `::test_no_trap_and_no_forbidden_path_is_in_the_archive` (أفخاخٌ متتبَّعة وغير متتبَّعة)، و`::test_the_forbidden_reference_can_actually_fail`، و`::test_the_content_is_the_tag_not_the_working_tree`. **وفي حزمة الإثبات:** لا `.env*` غير `.env.example` و`deploy/sizes/*.env`، ولا `src/` ولا `tests/` ولا `deploy/load/` ولا `certs` | ✅ مقبول |
| AC‑1.3 | `::test_version_is_five_lines_with_the_commit_and_three_pinned_images`. **و`VERSION` في حزمة الإثبات:** الإصدار، ثمّ `commit`، ثمّ ثلاث صورٍ بـ`@sha256:` من 64 خانة | ✅ مقبول |
| AC‑1.4 | `::test_each_refusal_has_its_code_and_writes_nothing` (11 حالة)، و`::test_a_bad_digests_file_is_refused_with_5`، و`::test_an_unwritable_output_directory_is_refused_with_6`، و`::test_an_existing_bundle_is_never_replaced`، و`::test_the_header_check_fails_in_both_directions` | ✅ مقبول |
| AC‑1.5 | `test_install_sh_config.py::test_every_combination_of_mode_monitoring_and_size_is_a_valid_bundle_project` (20 تركيبة). لم أعد تشغيله لأنّه يحتاج Docker. وقد جرى في البوّابة 5 على `dce2750`، وفي جولة الجودة 2 (158 ناجحاً) | ✅ مقبول |
| AC‑1.6 (Should) | `::test_two_builds_a_moment_apart_are_byte_identical`، و`::test_the_archive_mtime_is_the_commit_time_not_now` | ✅ مقبول |
| AC‑1.7 | [مالك] بعد الدمج: أوّل وسمٍ حقيقيّ `v2026.10.3` بعد ش‑4 (Q‑10). مسجَّلٌ في `stories.md` (المجموع)، وفي `test-plan.md` (⏸، ومعه آليّة `AIZZAK_BUNDLE`)، وفي صفّ 5.1 من لوحة الخطّة | ⏸ مؤجَّل |
| AC‑2.1 | `test_release_bundle_guard.py::test_every_deploy_bind_is_listed_or_excluded_with_a_reason`، و`::test_the_real_compose_files_have_the_27_binds_the_design_measured` (أعدتُهما). والتقاطع مع `config --format json` من البوّابة 5 | ✅ مقبول |
| AC‑2.2 | الحالات الأربع (أ)…(د) في `test_release_bundle_guard.py`، ورسالة كلٍّ منها تسمّي المسار | ✅ مقبول |
| AC‑2.3 | `::test_every_listed_path_is_tracked_by_git`، و`::test_the_list_has_34_distinct_paths` | ✅ مقبول |
| AC‑2.4 | `test_gpu_compose_hygiene.py` (السكربتان في `_SCRIPTS`)، و`::test_no_destructive_command_and_no_env_sourcing[install.sh/bundle.sh]`، و`::test_the_forbidden_patterns_find_what_they_are_for` | ✅ مقبول |
| AC‑3.1 | `::test_preflight_codes_choose_the_mode_or_stop_the_install` (13 مزيجاً)، و`::test_a_stopped_preflight_writes_nothing`. **وحيّاً:** أعطى `gpu-preflight` الرمز 10، فاختار التثبيت وضع المعالج (`proof2.log` السطر 7) | ✅ مقبول |
| AC‑3.2 | `::test_a_missing_python_or_pyyaml_stops_with_11_before_the_root_check` (3 حالات)، و`::test_too_little_free_space_stops_a_first_install_with_12_in_gb` | ✅ مقبول |
| AC‑3.3 | `::test_unauthorized_stops_with_13_and_tells_how_to_log_in`، و`::test_a_network_failure_stops_with_14_and_says_no_network`، و`::test_the_script_accepts_exactly_the_six_documented_options` (لا خيار يأخذ مفتاحاً)، و`::test_the_script_never_reads_a_credential_file` | ✅ مقبول |
| AC‑3.4 | `::test_running_it_from_a_checkout_exits_3_and_touches_nothing` (sha256 لكلّ ملفٍّ قبل التشغيل وبعده)، و`::test_a_damaged_version_file_exits_3` | ✅ مقبول |
| AC‑3.5 | `::test_the_kernel_step_writes_the_knob_and_one_persist_file_and_repeats_to_the_byte`، و`::test_a_non_root_run_stops_with_4_and_says_sudo`، و`test_host_tuning.py`. **وحيّاً:** `0 -> 1` وكتابة `90-aizzak.conf` في التشغيل 2، ثمّ `ok` و`0 changed` في التشغيل 3 | ✅ مقبول |
| AC‑3.6 | [خادم] في 7.3 (بوابة ①) | ⏸ مؤجَّل |
| AC‑4.1 | `::test_env_is_0644_under_umask_022_and_nothing_else_secret_appears`، و`::test_no_value_is_left_as_change_me_and_the_13_are_48_hex_and_distinct`، و`::test_the_13_are_derived_from_the_template_and_match_the_list`، و`::test_every_other_template_key_keeps_its_value`. والمعيار [وحدة]، فيكفيه الاختبار. أمّا مسار `created` على Docker حقيقيّ فيبقى لـ7.3 | ✅ مقبول |
| AC‑4.2 | `::test_no_secret_is_in_any_output_argument_or_variable`، و`::test_no_secret_is_printed_or_passed_on_a_failure_path` (3 مسارات فشل)، و`::test_the_metrics_exporter_password_crosses_stdin_and_nothing_else`. **وحيّاً:** 0 من 15 قيمةً سرّيّة في السجلّين (status.md الصفّ 8) | ✅ مقبول |
| AC‑4.3 | `::test_the_validators_accept_and_refuse_exactly_these`، و`::test_the_domain_is_lower_cased_into_the_three_keys`، و`::test_bad_usage_exits_2_before_anything_is_written` (ومعه `xn--`) | ✅ مقبول |
| AC‑4.4 | `::test_bad_usage_exits_2_before_anything_is_written` (الخيار غائب، و`demo proj`، و`a/b`) | ✅ مقبول |
| AC‑4.5 | `::test_the_four_combinations_write_compose_selectors_and_pinned_images` | ✅ مقبول |
| AC‑4.6 | `::test_a_repeat_keeps_every_secret_the_edit_the_bytes_and_the_mode`، و`::test_compose_unchanged_writes_nothing_not_even_the_mtime`. والمولِّد يعيد كتابة `.env` في التكرار بـ`mkstemp` (0600)، ثمّ `chmod` إلى صلاحيّة الهدف (`deploy/auto_size.py:647,666`)، فلا تصيب نافذة SR‑2 هذا المسار | ✅ مقبول |
| AC‑4.7 | `::test_changing_an_input_after_install_is_refused_naming_the_key` (5 مدخلات، والرمز 30، وبايتات `.env` كما هي) | ✅ مقبول |
| AC‑5.1 | `::test_the_generator_exit_codes_map_to_40_and_41_before_any_pull[1,2,3]`، و`::test_a_generator_error_leaves_env_as_step_3_wrote_it`، و`::test_a_passing_generator_lets_the_install_continue` | ✅ مقبول |
| AC‑5.2 | `::test_a_named_size_writes_its_block_and_no_auto_block`، و`::test_the_named_budget_is_called_with_the_size_and_without_monitoring`، و`::test_bad_usage_exits_2...[--size huge]` | ✅ مقبول |
| AC‑5.3 | `::test_a_shell_exported_compose_file_does_not_reach_the_budget_and_stops_before_pull` | ✅ مقبول |
| AC‑5.4 | `test_install_sh_config.py::test_the_real_generator_in_the_bundle_fits_three_hosts_and_refuses_the_small_one` (في الوضعين). من البوّابة 5، ولم أعده لأنّه يحتاج Docker | ✅ مقبول |
| AC‑5.5 | (أ) `test_auto_size_edges.py::test_as_root_*` (خمسة اختبارات).<br>(ب) `test_resource_budget.py::test_a_planted_yaml_module_in_the_working_directory_is_never_imported`.<br>(ج) `::test_an_unwritable_sizes_directory_names_auto_conf_not_env`.<br>(د) `::test_a_world_readable_env_gets_a_warning_with_the_file_name_only`، وحيّاً السطر 27 في `proof2.log`.<br>و`test_auto_size_edges.py` و`test_resource_budget.py` خضراوان عندي، و`test_auto_size.py` أخضر في البوّابة 5 | ✅ مقبول |
| AC‑5.6 | [خادم] في 7.3 | ⏸ مؤجَّل |
| AC‑6.1 | `::test_the_boot_runs_in_the_documented_order`، و`::test_nothing_is_created_before_the_key_exists_except_vault_and_its_bootstrap`، و`test_release_bundle_guard.py::test_vault_and_vault_bootstrap_do_not_pull_in_a_carrier`. **وحيّاً:** أسطر `boot:` في `proof2.log` بالترتيب المطلوب: `vault`، ثمّ `vault-bootstrap`، ثمّ المفتاح، ثمّ `minio-bootstrap`، ثمّ `migrate`، ثمّ `metrics_exporter`، ثمّ `ollama-pull`، ثمّ الباقي | ✅ مقبول |
| AC‑6.2 | `::test_the_key_is_minted_inside_vault_and_written_once`: نصّ `MINT_SCRIPT` حرفيّاً، و`VAULT_ADDR` و`init.json` داخل `sh -c`، وسطرٌ واحد، والصلاحيّة. ومعه `::test_a_key_that_is_not_a_uuid_stops_with_61_before_any_carrier`، و`::test_a_damaged_key_in_env_is_never_replaced` | ✅ مقبول |
| AC‑6.3 | `::test_a_digest_that_is_not_the_one_in_version_stops_with_51_and_no_up`: الرمز 51، واسم الصورة، والبصمتان. وجزء «لا `up`» يضمنه الترتيب الخطّيّ لـ`run_steps`، لا التأكيد (CR‑12) | ✅ مقبول (دليلٌ ضعيف في جزء) |
| AC‑6.4 | `::test_a_migrate_failure_is_reported_by_name_and_the_rerun_resumes`: الرمز 60 و`docker compose logs migrate`، ثمّ 0، ومفتاحٌ واحد. وجزء «لا كلمات سرٍّ جديدة» لا يستطيع تأكيده أن يفشل (CR‑11). فدليله غير مباشر: AC‑4.6، وAC‑7.3، و`env: unchanged` في `proof3.log` | ✅ مقبول (دليلٌ ضعيف في جزء) |
| AC‑6.5 | `proof2.log`: الخروج 0، و`install: READY`، و`/health 200, /health/ready 200, stack_smoke PASS`. والبنود ② إلى ⑨ فحوصٌ يدويّة مسجَّلة في status.md الصفّ 8. وخطّ الأساس في `baseline-aizzak-{containers,volumes}.txt` (40 حاوية و13 حجماً) | ✅ مقبول |
| AC‑6.6 | [خادم] في 7.3 | ⏸ مؤجَّل |
| AC‑7.1 | `::test_a_healthy_stack_is_ready_and_probes_the_port_compose_published`، و`::test_a_502_stops_with_70_and_the_report_says_not_ready`، و`::test_a_failing_smoke_stops_with_71` | ✅ مقبول |
| AC‑7.2 | ستّة اختبارات للتقرير (test-plan.md). **وحيّاً:** في تقرير `proof2.log` (الأسطر 167–175) كلّ البنود، ومنها تاريخ انتهاء المفتاح (Should)، وسطر `chmod 600 .env` مرّةً واحدة | ✅ مقبول |
| AC‑7.3 | `::test_a_repeat_on_a_converged_stack_touches_nothing`، و`::test_no_call_anywhere_recreates_or_initialises` | ✅ مقبول |
| AC‑7.4 | `proof3.log`: الخروج 0، و`boot: already converged: no container touched`، و`env: unchanged`. واللقطات الأربع متطابقة بـ`cmp`، وقد أعدتُ المقارنة بنفسي | ✅ مقبول |
| AC‑7.5 | `test_install_bundle_docs.py::test_the_header_and_the_document_list_the_same_exit_codes` (19 رمزاً)، و`::test_the_comparison_fails_when_either_side_loses_or_gains_a_code`، و`::test_every_exit_the_script_takes_is_in_the_table`. والاختبار يقارن الرموز لا معانيها، وقد قرأ مراجِع الشيفرة المعاني فوجدها متّسقة | ✅ مقبول (دليلٌ ضعيف في المعاني) |
| AC‑7.6 | [خادم] في 7.3 | ⏸ مؤجَّل |
| AC‑8.1 | `test_install_bundle_docs.py::test_the_broken_vault_commands_are_gone_and_the_working_one_is_there` (الوثائق الثلاث) | ✅ مقبول |
| AC‑8.2 | `::test_no_document_still_says_the_key_must_not_be_in_env`، و`::test_the_key_policy_guard_flags_each_forbidden_sentence`، و`::test_no_document_says_install_sh_sets_env_to_0600`، و`::test_the_shell_passing_examples_stay`. وفي Compose تغيّر التعليق وحده، وقد تحقّقتُ من الفرق | ✅ مقبول (انظر CR‑4) |
| AC‑8.3 | `::test_the_install_section_covers_requirements_options_and_the_env_mode`، و`::test_the_two_new_gaps_are_rows_of_the_one_security_table`. وفي `docs/deploy-server.md`: ث‑10 وث‑11 (ثغرتا Q‑4)، وث‑12 (الحزمة بلا توقيع) | ✅ مقبول |
| AC‑8.4 | `::test_the_plan_counts_27_paths_and_25_in_the_bundle`، و`::test_the_plan_names_what_was_built`، و`::test_the_status_rows_of_phase_5_are_not_not_started`، و`::test_the_summary_matches_the_plan_and_its_bar_matches_its_articles` | ✅ مقبول (عليه بندٌ توثيقيّ قبل الدمج، انظر «الحكم النهائي») |
| AC‑8.5 | البوّابات 1–4 على HEAD، وقد أعدتُها. والبوّابتان 5 و6 على `dce2750` (6133 ناجحاً، و`test-rules: OK`)، ولم تتغيّر الشيفرة بعدها. وNFR‑10: الزيادة بين +10.6 و+12.1 ث، والحدّ 15 (test-plan.md §1.3). وNFR‑1: الفرق تعليقاتٌ فقط | ✅ مقبول |

**العدد:** 47 معياراً:
- **42 ✅:** منها 40 آليّة، و2 محلّيّة (AC‑6.5 وAC‑7.4).
- **5 ⏸:** ‏AC‑1.7 [مالك]، وAC‑3.6 وAC‑5.6 وAC‑6.6 وAC‑7.6 [خادم].
- **0 🔴.**

## المعايير المؤجَّلة: أين سُجّلت

- **الأربعة [خادم]** (AC‑3.6 وAC‑5.6 وAC‑6.6 وAC‑7.6) مؤجَّلةٌ إلى 7.3 بقرار بوابة ① (2026‑10‑19). وهي مسجَّلةٌ في:
  - `stories.md`، في سطر المجموع: «4 مؤجّلة إلى 7.3».
  - `test-plan.md`، في المصفوفة: ⏸.
  - `status.md`، في الصفّ 8.
  - `review.md`، في قسم الاختبارات.
  - ولوحة الخطّة، في صفّ 5.2: «و7.3».
- **AC‑1.7 ليس من 7.3.** هو بيد المالك بعد الدمج، على أوّل وسمٍ حقيقيّ `v2026.10.3` (Q‑10). و`status.md` الصفّ 8 يجمعه مع معايير 7.3، أمّا `stories.md` ولوحة الخطّة (صفّ 5.1) فيسمّيانه باسمه.
  - وعند تنفيذه، الفحص يدويٌّ في جزأين: `diff` بين أسطر `VERSION` الثلاثة وسطور `release-digests`، ثمّ `AIZZAK_BUNDLE=<tar.gz> pytest tests/unit/test_install_sh_config.py -k bundle` (test-plan.md، صفّ AC‑1.7).
- **قبل 7.3 يُقرأ القسم (أ) من `follow-ups.md`:** ‏SR‑2، وSR‑1، وCR‑3، وCR‑4، وSR‑4.
  - **وCR‑3 يمسّ AC‑6.6 مباشرة:** خطوة 7.3 في `docs/server-gpu-plan.md:429` تقول `./install.sh --domain <النطاق>`، وهذا الأمر يخرج بالرمز 2. والأمر الصحيح هو الذي في نصّ AC‑6.6 نفسه.

## أدلّةٌ وجدتُها ضعيفة (لا تقلب حكماً)

1. **AC‑6.5:** البنود ② (`operator init` مرّةً واحدة)، و③ (طول المفتاح 36 في الحاويات الثماني)، و④ (مشروع `aizzak` كما كان) فحوصٌ يدويّة مسجَّلةٌ في status.md وحده.
   - ولم يُحفظ ملفّ «بعد» لمشروع `aizzak`، فالمحفوظ ملفّا خطّ الأساس وحدهما.
   - والإثبات يسلك مسار `adopted` وحده.
   - ونصّ المعيار يقول «الخدمات الثماني الدائمة»، وهي في وضع المعالج سبعٌ دائمة ومعها `migrate`. هذه دقّةٌ في صياغة المعيار، لا في المنتج.
2. **AC‑6.4 (CR‑11) وAC‑6.3 (CR‑12):** في كلٍّ منهما تأكيدٌ لا يستطيع أن يفشل، فالجزء الذي يحرسه ثابتٌ بأدلّةٍ أخرى سمّيتُها في الجدول.
3. **AC‑7.5:** الاختبار يحرس الرموز لا معانيها.
4. **AC‑1.5 وAC‑5.4، وأجزاءٌ من AC‑2.1 وAC‑6.1:** اختبارات [config]. دليلها تشغيل البوّابة 5 المسجَّل، ولم أعد تشغيلها بسبب قيد Docker. والشيفرة لم تتغيّر منذ ذلك التشغيل.
5. **AC‑7.3:** الاختبار يقيس sha256 والصلاحيّة لا `mtime`. والتكرار بحجم `auto` يعيد كتابة `.env` و`auto.conf` بالبايتات نفسها. وهذا يوافق نصّ المعيار.
6. **AC‑8.4:** صفّ 5.2 في `docs/server-gpu-plan.md:136`، ووسمه في `docs/server-gpu-summary.html:573`، يقولان «بانتظار … الإثبات المحلّيّ (AC‑6.5 وAC‑7.4)». وقد نجح الإثبات (status.md الصفّ 8). فاللوحة والملخّص متطابقان، لكنّ ما فيهما صار قديماً.
7. **AC‑8.2 مع CR‑4:** صفّ ث‑10 في `docs/deploy-runpod.md:465` ما زال يقول «و`install.sh` ينشئ `.env` بـ0600»، وهذا يخالف Q‑4.
   - والملفّ خارج قائمة الملفّات التي يسمّيها AC‑8.2، فالمعيار صادقٌ كما كُتب.
   - لكنّ في المستودع جملةً واحدة تناقض قرار المالك.

## ملاحظات غير حاجبة مقبولة

**أقبل بالاسم الملاحظات الأربع والعشرين في `follow-ups.md`**، بقرار المالك (2026‑10‑20): «لا تصلح شيئاً، وثّق هذه الملاحظات ليُعمل عليها لاحقاً». والمراجعتان PASS، ولا ملاحظة حاجبة في أيٍّ منهما.

- **(أ) قبل 7.3:** ‏SR‑2 (نافذة الملفّ المؤقّت)، وSR‑1 (حارس root ناقص)، وCR‑3 (أمر 7.3 في الخطّة)، وCR‑4 (ث‑10 وث‑11 في `deploy-runpod.md`)، وSR‑4 (متغيّرات المسار من بيئة المستدعي).
- **(ب) مع 5.3:** ‏CR‑1، وCR‑2، وCR‑5…CR‑13، وSR‑5…SR‑9. وCR‑8 هو SR‑7 نفسه.
- **(ج) ميزةٌ مستقلّة بموافقة المالك:** ‏SR‑3 (21 صورة طرفٍ ثالث بوسومٍ قابلةٍ للنقل).
- **(د) معروفة، أو قرار المالك:** ‏SR‑10 (Q‑4، `.env` بـ0644)، وSR‑11 (ث‑12، الحزمة بلا توقيع).

**هل تجعل إحداها معياراً كاذباً كما كُتب؟ لا.** وهذا وزني لما يقترب من معيار:

- **SR‑2 مقابل AC‑4.6 وAC‑6.2 وNFR‑3:**
  - المعياران يقيسان الصلاحيّة النهائيّة، وهي محفوظة.
  - ومسار المولِّد في AC‑4.6 آمن (`mkstemp` ثمّ `chmod`).
  - النافذة في كتابات `env_tool` وحدها: التبنّي، وكتلة الحجم، والسكّ. وهي تمسّ روح NFR‑3 «لا يغيّر صلاحيّةً ضبطها المالك»، لا نصّ معيار.
  - فإصلاحها شرطٌ قبل 7.3، وقبل أن يعيد `upgrade.sh` في 5.3 استعمال `atomic_write`.
- **SR‑1 مقابل AC‑5.5 (أ):** المعيار يسمّي الرابط الرمزيّ والملكيّة، وكلاهما مختبَر. وSR‑1 يطلب أوسع من ذلك: الكتابة للمجموعة، والآباء، والسكربتات.
- **CR‑3 مقابل AC‑8.4:** المعيار يخصّ قسمَي 5.1 و5.2، والأمر الخاطئ في 7.3. لكنّه يمسّ AC‑6.6 حين يُنفَّذ.
- **CR‑4 مقابل AC‑8.2:** صادقٌ كما كُتب (البند 7 أعلاه).
- **SR‑3 مقابل AC‑8.3:** «كلّ ثغرةٍ وُجدت أثناء البناء».
  - SR‑3 أقدم من الميزة، ووجدته المراجعة الأمنيّة لا البناء.
  - وث‑13 الذي اقترحته لم يُسجَّل في قائمة الأمان. وسجلّه الوحيد اليوم `follow-ups.md`.
- **CR‑11 وCR‑12 وCR‑13:** ضعفٌ في الاختبارات، والسلوك المقصود ثابتٌ بأدلّةٍ أخرى.
- **الباقي** لا يمسّ نصّ أيّ معيار.

**ملاحظتان جديدتان منّي، تجميليّتان وغير حاجبتين.** أقترح أن يضيفهما المنسّق إلى `follow-ups.md` (ب):

- **PO‑1:** الخطوة 4 (الميزانيّة) بلا عنوانٍ في مخرج التثبيت. فيرى المشغّل الخطوات 1 و2 و3 ثمّ 5 و6 و7 و8 (`proof2.log` و`proof3.log`)، مع أنّ `design.md:257` يسمّيها «step 4».
- **PO‑2:** في وسط مخرج التثبيت سطرا `auto_size.py`: «`next: docker compose up -d`» و«`docker compose restart postgres`». فيُطلب من المشغّل ما سيفعله `install.sh` بعد سطور.

## الحكم النهائي

**مقبول.**

**السبب:**
- كلّ معيارٍ يمكن إثباته في هذه المرحلة ✅ بدليلٍ مسمّى: 42 من 47.
- والخمسة الباقية مؤجَّلةٌ بقرار بوابة ① ومسجَّلة.
- ومراجعة الشيفرة PASS، والمراجعة الأمنيّة PASS، بلا ملاحظةٍ حاجبة.
- والملاحظات الأربع والعشرون أجّلها المالك بالاسم، ولا تجعل أيّ معيارٍ كاذباً كما كُتب.
- والسكربت الذي أُثبت حيّاً هو بايتاً ببايت السكربت الذي في HEAD.

**ما يبقى بعد القبول، وليس رفضاً:**
1. **قبل بوابة ② (على المنسّق، توثيق):**
   - تحديث صفّ 5.2 في `docs/server-gpu-plan.md`، ووسمه في `docs/server-gpu-summary.html`: الإثبات المحلّيّ نجح، فيبقى «بانتظار الدمج و7.3».
   - وإضافة PO‑1 وPO‑2 إلى `follow-ups.md`.
2. **بعد الدمج (المالك):** AC‑1.7 على `v2026.10.3` (Q‑10).
3. **قبل 7.3:** القسم (أ) من `follow-ups.md`، ثمّ AC‑3.6 وAC‑5.6 وAC‑6.6 وAC‑7.6 على الخادم.
4. **تنبيه:** هامش NFR‑10 نحو 3 ث وحدها، فأيّ اختبارٍ ثقيلٍ يُضاف بعد اليوم قد يخرقه.

</div>
