<div dir="rtl">

# خطة الاختبار: حزمة تثبيتٍ بدل المستودع (`install-bundle`، المرحلة 5 من خطّة خادم GPU، الخطوتان 5.1 و5.2)

> **الفرع:** `feat/install-bundle` بعد جولة الإصلاح الأولى (`0122db4` و`b23f3d0` من الباني، وفوقهما `0b48a98` منّي)؛ الجولة الأولى لـQA كانت عند `f187d1e`. **النطاق:** US‑1…US‑8، 47 معياراً (US‑9 وUS‑10 مؤجّلتان ولم تُختبرا).
> **الطريقة:** اختبارات `tests/unit` وحدها. لا حاوية ولا مكدّس حيّ ولا `tests/integration`. كلّ أمر Compose جرى في نسخةٍ مؤقّتة تحت `/tmp/claude-1000/` و`.env` مبنيّ من `.env.example`، و`DOCKER_CONFIG` فارغ، وأوامر `config` وحدها (لا تتّصل بالـdaemon). لم يُقرأ `.env` الحقيقيّ ولا `config.json` ولا الملفّات السرّيّة.
> **الحدّ:** الإثبات المحلّيّ (AC‑6.5 وAC‑7.4) **لم يُشغَّل**، وهو بيد المنسّق مع المالك (§6).

## 1. مصفوفة التغطية

الحالات: ✅ اختبار آليّ ناجح يثبت المعيار · 🟡 بانتظار الإثبات المحلّيّ (§6) · ⏸ ليس من اختصاص هذه المرحلة ([خادم] في 7.3، و[مالك] بعد الدمج) · 🔴 غير مغطّى. **لا 🔴 في المصفوفة.** وكلّ المسارات تحت `tests/unit/`.

### US‑1: حزمة الإصدار

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑1.1 | وحدة | `test_release_bundle.py::test_the_file_list_is_the_listed_paths_plus_install_sh_and_version` (قائمة الأرشيف = `git ls-tree` للمسارات + `install.sh` + `VERSION`)، و`::test_install_sh_is_executable_and_is_the_one_at_the_tag` (0755، يساوي نسخة الوسم بايتاً ببايت، ولا `upgrade.sh`)، و`::test_a_clean_build_exits_0_and_prints_the_path_sha_and_count` | ✅ |
| AC‑1.2 | وحدة | `::test_no_trap_and_no_forbidden_path_is_in_the_archive` (5 أفخاخ متتبَّعة و5 غير متتبَّعة)، و`::test_the_forbidden_reference_can_actually_fail` (المرجع مستقلّ عن `bundle.sh`)، و`::test_the_content_is_the_tag_not_the_working_tree` | ✅ |
| AC‑1.3 | وحدة | `::test_version_is_five_lines_with_the_commit_and_three_pinned_images` | ✅ |
| AC‑1.4 | وحدة | `::test_each_refusal_has_its_code_and_writes_nothing` (11 حالة: 2/4/5/6)، و`::test_a_bad_digests_file_is_refused_with_5` (7 تحويرات منها commit مخالف)، و`::test_an_unwritable_output_directory_is_refused_with_6`، و`::test_an_existing_bundle_is_never_replaced`، و`::test_a_listed_path_missing_at_the_tag...` (7)، وجدول رموز الرأس في الاتّجاهين (`::test_the_header_check_fails_in_both_directions`). في كلّ حالة قائمة مجلّد الإخراج فارغة | ✅ |
| AC‑1.5 | config | `test_install_sh_config.py::test_every_combination_of_mode_monitoring_and_size_is_a_valid_bundle_project` (20 تركيبة: `config -q` بـ0، و36/24/35/24 خدمة، ولا `:dev`/`:latest`). **جرى هنا فعلاً** (Docker موجود)، ولم يتخطَّ | ✅ |
| AC‑1.6 | وحدة | `::test_two_builds_a_moment_apart_are_byte_identical` و`::test_the_archive_mtime_is_the_commit_time_not_now` | ✅ |
| AC‑1.7 | مالك | بعد `v2026.10.3`. **تحقّقتُ من الآليّة:** بنيتُ حزمةً بـ`bundle.sh` في مستودعٍ مؤقّت وشغّلتُ `AIZZAK_BUNDLE=<tar.gz> pytest tests/unit/test_install_sh_config.py -k bundle` فاختار اختبارين ونجحا (2 passed, 1 deselected). **انتبه:** الأمر لا يقارن `VERSION` بالأثر (هذا فحصٌ يدويّ ثانٍ: `diff` بين أسطر `VERSION` الثلاثة وسطور `release-digests`) | ⏸ |

### US‑2: الحارس الأدنى

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑2.1 | وحدة | `test_release_bundle_guard.py::test_every_deploy_bind_is_listed_or_excluded_with_a_reason` و`::test_the_real_compose_files_have_the_27_binds_the_design_measured` و`::test_the_yaml_reading_agrees_with_docker_compose_config` (تقاطع مع `config --format json`، جرى فعلاً) | ✅ |
| AC‑2.2 | وحدة | الحالات الأربع كلّها بالرسالة المسمّية للمسار: `::test_a_new_bind_nobody_listed...` (أ)، و`::test_a_path_deleted_from_the_list...` (ب، ثلاثة مسارات)، و`::test_listing_an_excluded_path_fails...` (ج، لكلٍّ من المسارين)، و`::test_an_exclusion_that_is_not_a_bind...` (د)، و`::test_an_extra_listed_path_nothing_needs...`. الاتّجاهان مغطّيان | ✅ |
| AC‑2.3 | وحدة | `::test_every_listed_path_is_tracked_by_git` و`::test_the_list_has_34_distinct_paths` و`::test_the_bundle_never_names_a_secret_or_a_dev_file` | ✅ |
| AC‑2.4 | وحدة | `test_gpu_compose_hygiene.py` (`_SCRIPTS` يحوي السكربتين)، و`test_install_sh.py::test_no_destructive_command_and_no_env_sourcing[install.sh/bundle.sh]` (`volume rm`، `prune`، `down`، `--force-recreate`، `operator init`، `source .env`، `compose pull`)، و`::test_the_forbidden_patterns_find_what_they_are_for`، و`::test_a_bare_config_is_never_called` | ✅ |

### US‑3: الفحص قبل أيّ شيء

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑3.1 | وحدة | `test_install_sh.py::test_preflight_codes_choose_the_mode_or_stop_the_install` (13 مزيجاً من رمز الفحص و`--cpu`)، و`::test_a_stopped_preflight_writes_nothing[11,12,13,14,2]` (لا `.env`، ولا `compose`، ولا ملف جديد، و`sysctl.d` فارغ) | ✅ |
| AC‑3.2 | وحدة | `::test_a_missing_python_or_pyyaml_stops_with_11_before_the_root_check` (3 حالات)، و`::test_too_little_free_space_stops_a_first_install_with_12_in_gb`، و`::test_the_same_shortage_does_not_refuse_a_repeat` | ✅ |
| AC‑3.3 | وحدة | `::test_unauthorized_stops_with_13_and_tells_how_to_log_in`، و`::test_a_network_failure_stops_with_14_and_says_no_network`، و`::test_the_script_accepts_exactly_the_six_documented_options`، و`::test_the_script_never_reads_a_credential_file` | ✅ |
| AC‑3.4 | وحدة | `::test_running_it_from_a_checkout_exits_3_and_touches_nothing` (sha لكلّ الملفّات قبل/بعد)، و`::test_a_version_file_with_a_git_directory_is_still_not_a_bundle`، و`::test_a_damaged_version_file_exits_3` (6 تلفات) | ✅ |
| AC‑3.5 | وحدة | `::test_the_kernel_step_writes_the_knob_and_one_persist_file_and_repeats_to_the_byte` (sha وmtime)، و`::test_a_kernel_failure_stops_with_20`، و`::test_a_non_root_run_stops_with_4_and_says_sudo`، و`::test_require_root_refuses_a_normal_user`، و`test_host_tuning.py` (`--persist` و`--check --persist`). **ملاحظة:** الفرع root يحتاج مستخدماً غير root لاختباره، وقد جرى | ✅ |
| AC‑3.6 | خادم | 7.3 | ⏸ |

### US‑4: `.env` والأسرار

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑4.1 | وحدة | `::test_env_is_0644_under_umask_022_and_nothing_else_secret_appears` (0644، والملفّات الجديدة `.env` و`auto.conf` فقط، ولا `.install-tmp-*`)، و`::test_no_value_is_left_as_change_me_and_the_13_are_48_hex_and_distinct`، و`::test_the_13_are_derived_from_the_template_and_match_the_list` (يفشل في الاتّجاهين: 12 و14)، و`::test_every_other_template_key_keeps_its_value` | ✅ |
| AC‑4.2 | وحدة | `::test_no_secret_is_in_any_output_argument_or_variable` (الـ13 والتوكن والمفتاح في stdout وstderr وargv وبيئة كلّ نداء)، و`::test_the_metrics_exporter_password_crosses_stdin_and_nothing_else` (sha256 لـstdin فقط)، و`::test_a_mint_failure_stops_with_61_hides_the_token_and_leaves_no_file`. **أضفتُ:** `::test_no_secret_is_printed_or_passed_on_a_failure_path` (فشل `migrate` بعد إصدار المفتاح، وفشل خطوة `metrics_exporter`، وفشل الإصدار): الاختبار الأصليّ كان يفحص التشغيلين الناجحين وحدهما. (وفشل `ollama-pull` و502 و`smoke` فحصتها استكشافيّاً دون تسرّب، §3.) **جرّبتُ طفرةً** (سطر يطبع كلمة السرّ عند فشل `metrics_exporter`) فالكاشف يمسكها | ✅ |
| AC‑4.3 | وحدة | `::test_the_validators_accept_and_refuse_exactly_these` (14 نطاقاً)، و`::test_the_domain_is_lower_cased_into_the_three_keys`، و`::test_bad_usage_exits_2_before_anything_is_written` (الرمز 2 ومعه `xn--`) | ✅ |
| AC‑4.4 | وحدة | `::test_bad_usage_exits_2...` (غائب، `demo proj`، `a/b`: «does not start without it») | ✅ |
| AC‑4.5 | وحدة | `::test_the_four_combinations_write_compose_selectors_and_pinned_images` (4 تركيبات: `COMPOSE_*`، والصور الستّ = `VERSION`، و`VAULT_TOKEN` فارغ، و`VAULT_ROLE_ID` UUID v4) | ✅ |
| AC‑4.6 | وحدة | `::test_a_repeat_keeps_every_secret_the_edit_the_bytes_and_the_mode` (`LOG_LEVEL=DEBUG` و`chmod 600`: الأسرار والملفّ كلّه كما كانت، والصلاحيّة 0600؛ والمولِّد الحقيقيّ بالغلاف يعمل في التكرار)، و`::test_compose_unchanged_writes_nothing_not_even_the_mtime`، و`::test_a_failed_write_leaves_env_and_no_temp_file`. **نافذة الصلاحيّة (QA‑2) أُصلحت، ويحرسها اختبار التجسّس على `fsync`** | ✅ |
| AC‑4.7 | وحدة | `::test_changing_an_input_after_install_is_refused_naming_the_key` (النطاق، وFirebase، والحجم، والكرت بعد المعالج، والمراقبة: الرمز 30 والمفتاح في الرسالة وبايتات `.env` كما هي)، و`::test_a_repeat_that_contradicts_the_install_is_refused_and_writes_nothing` و`::test_the_size_rules_of_a_repeat` و`::test_compose_refuses_a_key_the_install_block_owns_or_a_vault_token` | ✅ |

### US‑5: الحجم والميزانيّة

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑5.1 | وحدة | `::test_the_generator_exit_codes_map_to_40_and_41_before_any_pull[1,2,3]` (argv و`isolated==1` والبيئة المثبّتة)، و`::test_a_generator_error_leaves_env_as_step_3_wrote_it`، و`::test_a_passing_generator_lets_the_install_continue` | ✅ |
| AC‑5.2 | وحدة | `::test_a_named_size_writes_its_block_and_no_auto_block`، و`::test_the_named_budget_is_called_with_the_size_and_without_monitoring`، و`::test_with_monitoring_the_budget_gets_no_without_flag`، و`::test_bad_usage_exits_2...[--size huge]` | ✅ |
| AC‑5.3 | وحدة | `::test_a_shell_exported_compose_file_does_not_reach_the_budget_and_stops_before_pull` (قيمة CI مصدَّرة، والميزانيّة بـ1: الرمز 40، ولا `pull`، والتحذير يسمّي `COMPOSE_FILE`) | ✅ |
| AC‑5.4 | config | `test_install_sh_config.py::test_the_real_generator_in_the_bundle_fits_three_hosts_and_refuses_the_small_one` (8/16، 16/32، 32/64 بـ0 ثمّ `--block` و`config -q` بـ0؛ و8/12 بـ1 ولا كتابة؛ في الوضعين). جرى فعلاً | ✅ |
| AC‑5.5 | وحدة | (أ) `test_auto_size_edges.py::test_as_root_a_symlinked_env_is_refused...` و`::test_as_root_a_symlinked_directory_on_the_path_is_refused[3]` و`::test_as_root_an_existing_auto_conf_that_is_a_link...` و`::test_as_root_a_file_owned_by_someone_else...`. **أضفتُ:** `::test_as_root_a_symlinked_project_directory_is_refused`، لأنّ الدليل الذي فيه `.env` نفسه (`base`) في `check_trusted` لم يكن مغطّى. (ب) `test_resource_budget.py::test_a_planted_yaml_module_in_the_working_directory_is_never_imported`. (ج) `::test_an_unwritable_sizes_directory_names_auto_conf_not_env`. (د) `::test_a_world_readable_env_gets_a_warning_with_the_file_name_only` | ✅ |
| AC‑5.6 | خادم | 7.3 | ⏸ |

### US‑6: السحب وأوّل إقلاع

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑6.1 | وحدة + ثابت | `test_install_sh.py::test_the_boot_runs_in_the_documented_order`، و`::test_nothing_is_created_before_the_key_exists_except_vault_and_its_bootstrap`، و`test_release_bundle_guard.py::test_vault_and_vault_bootstrap_do_not_pull_in_a_carrier` (+ `::test_a_dependency_from_vault_bootstrap_to_a_carrier_would_be_caught`)، و`::test_the_nine_carriers_are_exactly_the_services_that_name_the_secret`، و`test_install_sh_config.py::test_writing_the_key_changes_the_hash_of_the_carriers_and_of_nothing_else` (قياسٌ على Compose الحقيقيّ) | ✅ |
| AC‑6.2 | وحدة | `::test_the_key_is_minted_inside_vault_and_written_once` (نصّ `MINT_SCRIPT` حرفيّاً، وسطر واحد، والصلاحيّة)، و`::test_a_key_that_is_not_a_uuid_stops_with_61_before_any_carrier[ ""، not-a-uuid]`، و`::test_a_damaged_key_in_env_is_never_replaced` | ✅ |
| AC‑6.3 | وحدة | `::test_a_digest_that_is_not_the_one_in_version_stops_with_51_and_no_up`، و`::test_only_missing_images_are_pulled_and_never_a_compose_level_pull`، و`::test_a_failed_pull_stops_with_50` | ✅ |
| AC‑6.4 | وحدة | `::test_a_migrate_failure_is_reported_by_name_and_the_rerun_resumes` (الرمز 60 و`docker compose logs migrate`؛ والثاني 0؛ الأسرار كما هي؛ إصدار واحد للمفتاح)، و`::test_an_ollama_pull_failure_stops_the_install...`، و`::test_the_metrics_exporter_step_failing_stops_with_60`، و`::test_a_service_that_never_becomes_healthy_is_named` | ✅ |
| AC‑6.5 | محلّيّ | §6.1 | 🟡 |
| AC‑6.6 | خادم | 7.3 | ⏸ |

### US‑7: الفحوص والتقرير والتكرار

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑7.1 | وحدة | `::test_a_healthy_stack_is_ready_and_probes_the_port_compose_published` (منفذ 20080 من `port`)، و`::test_a_502_stops_with_70_and_the_report_says_not_ready`، و`::test_a_failing_smoke_stops_with_71` | ✅ |
| AC‑7.2 | وحدة | `::test_the_report_with_monitoring_and_a_gpu` و`::test_the_report_without_monitoring_or_a_gpu` و`::test_the_forced_cpu_is_reported_as_the_choice` و`::test_the_expiry_is_the_minting_time_plus_720_hours` و`::test_a_0600_env_is_reported_as_done_with_one_mention` و`::test_the_report_is_plain_text_on_stdout_and_writes_no_file` | ✅ |
| AC‑7.3 | وحدة | `::test_a_repeat_on_a_converged_stack_touches_nothing` (لا `up`، ولا `exec vault`، ولا `pull`، و«already converged»، وsha وصلاحيّة `.env`)، و`::test_no_call_anywhere_recreates_or_initialises`، و`::test_the_ttl_constant_equals_the_one_vault_bootstrap_sets`. **انظر §7:** الاختبار يقيس sha والصلاحيّة لا mtime، والمولِّد يعيد كتابة `.env` و`auto.conf` في كلّ تكرارٍ بحجم `auto` (البايتات نفسها) | ✅ |
| AC‑7.4 | محلّيّ | §6.2 | 🟡 |
| AC‑7.5 | وحدة | `test_install_bundle_docs.py::test_the_header_and_the_document_list_the_same_exit_codes` (19 رمزاً في الاتّجاهين) و`::test_the_comparison_fails_when_either_side_loses_or_gains_a_code` و`::test_every_exit_the_script_takes_is_in_the_table` و`test_gpu_preflight.py::test_the_server_doc_lists_the_scripts_exit_codes`. **يقارن الرموز لا المعاني** (§7) | ✅ |
| AC‑7.6 | خادم | 7.3 | ⏸ |

### US‑8: الوثائق

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑8.1 | نصّ | `test_install_bundle_docs.py::test_the_broken_vault_commands_are_gone_and_the_working_one_is_there[3 وثائق]` (و`MINT_SCRIPT` المستخرَج من السكربت في كلٍّ منها، وأمر `role_id` أيضاً) | ✅ |
| AC‑8.2 | نصّ | `::test_no_document_still_says_the_key_must_not_be_in_env[6 ملفّات]`، و`::test_no_document_says_install_sh_sets_env_to_0600`، و`::test_the_shell_passing_examples_stay`. **تحقّقتُ بنفسي** أنّ الجمل الخمس الممنوعة كانت كلّها موجودةً في `13733ce` (فالحارس غير فارغ) وغائبةٌ الآن. **ضعف:** `::test_the_forbidden_sentences_are_found_when_present` تحصيل حاصل (§7) | ✅ (مع ضعف) |
| AC‑8.3 | نصّ | `::test_the_install_section_covers_requirements_options_and_the_env_mode` و`::test_the_two_new_gaps_are_rows_of_the_one_security_table` و`::test_the_document_still_opens_and_closes_its_rtl_wrapper` | ✅ |
| AC‑8.4 | نصّ | `::test_the_plan_counts_27_paths_and_25_in_the_bundle` و`::test_the_plan_names_what_was_built` و`::test_the_status_rows_of_phase_5_are_not_not_started` و`::test_the_summary_matches_the_plan_and_its_bar_matches_its_articles` و`::test_the_bar_check_fails_when_a_count_drifts` | ✅ |
| AC‑8.5 | بوّابات + قياس | §2 (الستّ خضراء)، وNFR‑1 مقيسٌ بنفسي (§1.2: بصمات متطابقة)، وNFR‑10 مقيس بعد الإصلاح: **+10.6 إلى +12.1 ث** من 15 (§1.3) | ✅ |

الجملة: 47 معياراً: **40 ✅**، و**2 🟡** (AC‑6.5 وAC‑7.4)، و**5 ⏸** ([خادم] ×4، [مالك] ×1)، ولا معيار بلا اختبار.

### 1.1 ما أضفتُه (commit `3bffde6`، `test:`)

| الملف | الاختبار | يغطّي |
|---|---|---|
| `test_install_sh.py` | `test_no_secret_is_printed_or_passed_on_a_failure_path` ×3 (فشل `migrate`، وفشل `metrics_exporter`، وفشل الإصدار) | AC‑4.2 على مسارات الفشل |
| `test_install_sh.py` | `test_a_crlf_env_still_counts_as_an_installed_server` (كان `xfail`؛ العلامة رُفعت مع الإصلاح، ويجتاز) | QA‑1 |
| `test_install_sh.py` | `test_a_0600_env_is_never_rewritten_through_a_more_open_temp_file` (كان `xfail`؛ يجتاز بعد الإصلاح) | QA‑2، AC‑4.6 |
| `test_auto_size_edges.py` | `test_as_root_a_symlinked_project_directory_is_refused` | AC‑5.5 (أ) |

الاختباران كانا `xfail` صارمين؛ رفع الباني العلامتين مع الإصلاح كما هو مطلوب. **جولة 1:** أعدتُ تأكيدين أسقطهما التسريع (عدم تغيّر كلمات السرّ عبر تشغيلَي AC‑6.4، وبقاء `.env` التالف بايتاً ببايت في AC‑6.2)، في commit `0b48a98`.

### 1.2 NFR‑1 (البصمات)

قياسٌ لمرّةٍ واحدة بيدي، في مجلّدٍ واحد ثابت الاسم (البصمة تتضمّن المسارات المطلقة، فمجلّدان باسمين مختلفين يعطيان بصماتٍ مختلفة): `docker compose config --hash '*'` على ملفّي Compose عند `13733ce` وعند `HEAD`، و`.env.example` نفسه، لكلّ تركيبة. **النتيجة:** متطابقة في الأربع (معالج/كرت × مع/دون مراقبة) بـ24 و35 و24 و36 خدمة. و`git diff 13733ce..HEAD -- docker-compose.yml docker-compose.gpu.yml deploy/sizes deploy/postgres/sizes`: 8 أسطر في `docker-compose.yml`، تعليقاتٌ كلّها، ولا شيء في الباقي.

### 1.3 NFR‑10 (زمن الاختبارات)

الحدّ: ≤ 15 ث زيادةً في `pytest tests/unit` عن الأساس `13733ce`. **بعد جولة الإصلاح الأولى:** ثلاثة أزواجٍ متتابعة بالتناوب (الأساس ثمّ الفرع)، على نسختي `git clone` نظيفتين عند `13733ce` وعند `0b48a98`:

| الزوج | الأساس | الفرع | الزيادة |
|---|---|---|---|
| 1 | 169.95 ث | 181.67 ث | **+11.7 ث** |
| 2 | 170.55 ث | 182.68 ث | **+12.1 ث** |
| 3 | 170.51 ث | 181.07 ث | **+10.6 ث** |

الزيادة 10.6 إلى 12.1 ث، تحت الحدّ بنحو 3 ث. (الباني قاس +6.5 و+9.6 و+8.4؛ فرق القياس من طريقتي: نسخٌ نظيفة وجهازٌ آخر الحالات. وقبل الإصلاح كانت قياساتي +29 إلى +58 ث.) والهامش ضيّق، وتقلّب الجهاز كبير (بوّابة 5 نفسها 253 ث ثمّ 193 ث ثمّ 181 ث)، فأيّ إضافةٍ ثقيلة بعد اليوم تعيد الخرق.

## 2. نتائج بوابات المشروع

(من `/home/AIZZAK`، بالترتيب. البوّابة 5 بـ`PATH=/home/AIZZAK/.venv/bin:$PATH` فلا يتخطّى `test_import_contracts.py`. ولا `-q` ثانية.)

```
$ .venv/bin/ruff format --check .
828 files already formatted

$ .venv/bin/ruff check .
All checks passed!

$ .venv/bin/mypy src
Success: no issues found in 467 source files

$ .venv/bin/lint-imports
Framework does not import outer layers KEPT

Contracts: 8 kept, 0 broken.

$ PATH=/home/AIZZAK/.venv/bin:$PATH .venv/bin/pytest -rs tests/unit tests/architecture tests/eval
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
6131 passed, 7 warnings in 180.99s (0:03:00)

$ deploy/prometheus/test-rules.sh
Checking /etc/prometheus/alerts.yml
  SUCCESS: 29 rules found

test-rules: OK
```

- **لا اختبار متخطّى** (`-rs` لا يطبع سطر SKIPPED): `test_import_contracts.py` عمل لأنّ `lint-imports` في `PATH`، واختبارات [config] عملت لأنّ Docker موجود (تشغّل `docker compose config` وحده في نسخٍ مؤقّتة).
- لا `xfail` بعد الآن (رُفعت علامتا QA‑1 وQA‑2 مع إصلاحهما). على `f187d1e`: 6116 ناجحاً وواحد متخطّى؛ الآن 6131 ناجحاً ولا متخطّى.
- البوّابة 6 تشغّل `docker run --rm` لـ`promtool` و`amtool` (حاويات مؤقّتة تقرأ القواعد)، ولا تمسّ المكدّس الحيّ.

## 3. الاختبار الاستكشافي

كلّها بـ`docker` مزيّف أو دون Docker، في مجلّداتٍ مؤقّتة تحت `/tmp/claude-1000/`. «غير root» لأنّ الجهاز لا يمنح `sudo` دون كلمة سرّ؛ مسارات root وحدها (ملكيّة `.env` لمستخدمٍ آخر) لم تُجرَّب إلّا بالمعامل `uid` في `require_trusted`.

| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| نطاقاتٌ غريبة | `valid_domain` على: `EXAMPLE.COM`، `Ex.Com\nFOO=bar`، `example.com ` (مسافة)، `example.com.`، `xn--80ak6aa92e.com`، `ex_ample.com`، `xn--.com`، `例え.jp`، 300 حرف، `-a.com`، `a-.com`، `0.0`، `a.123`، `\tfoo.com` | الحروف الكبيرة تُصغَّر وتُقبل؛ نقطة النهاية والسطر الجديد والمسافة والتبويب والشرطة الطرفيّة والعربيّ ومعرّف IP مرفوضة | كما هو متوقَّع. السطر الجديد مرفوضٌ (فلا حقن في `.env`) | ✅ |
| مدخلات ضخمة وعربيّة | `--domain` بـ100 ألف حرف، و`--firebase-project` بـ5000 حرفٍ عربيّ | الرمز 2 بلا انتظار | الرمز 2 فوراً | ✅ |
| مسافاتٌ في المسار | تثبيتٌ كاملٌ في `.../my bundle dir/...` | يعمل | `READY`، الرمز 0 | ✅ |
| مجلّدٌ رمزيّ | `ln -s <bundle> link; cd link; ./install.sh` | يعمل (يُحلّ المسار الفيزيائيّ) | يصل إلى فحص root (4) | ✅ |
| رابطٌ رمزيّ إلى `install.sh` نفسه | `ln -s <bundle>/install.sh inst; bash inst ...` | رفضٌ آمن | الرمز 3 «not an unpacked release bundle» (لا يخمّن) | ✅ |
| `VERSION` مفقود/تالف | غياب الملفّ، سطرٌ ناقص، commit خاطئ، بصمة 63 حرفاً، ترتيبٌ معكوس، `:latest` | 3 | 3 في الستّ (اختبارات قائمة) | ✅ |
| `.env` مبتور (قتلٌ في الوسط) | قصّ الملفّ عند `# <<< aizzak install <<<` | رفضٌ دون تغيير | الرمز 30 «the install block is malformed»، وsha كما هو | ✅ |
| ملفٌّ مؤقّتٌ مخلَّف | `.install-tmp-12345` قديمٌ في الحزمة | يمرّ | يمرّ، **ويبقى الملفّ**، ولا أحد ينظّفه | ✅ (QA‑3 أُصلح) |
| `.env` فارغ (0 بايت) | `: > .env` ثمّ تثبيت | يتبنّاه كملفٍّ بيد المشغّل | `adopted`: يُضاف الأسرار والنطاق فقط، **لا تُملأ بقيّة مفاتيح القالب** (هذا تصميمٌ: «غير المُدار لا يُلمس») | ملاحظة |
| `.env` بنهايات CRLF | تثبيتٌ ثمّ `\n`→`\r\n` ثمّ تكرار | يُعامَل كتثبيتٍ قائم | التكرار ينجح، **لكن** إن كانت المساحة قليلة يُرفض بـ12 كأنّه أوّل تثبيت | ✅ (QA‑1 أُصلح) |
| `.env` بصلاحيّة 0600 يدويّ ثمّ تبنٍّ | `chmod 600` ثمّ `env_tool compose` مع تجسّسٍ على `fsync` | الملفّ المؤقّت ليس أوسع من الهدف | المؤقّت 0644 وفيه كلّ الأسرار حتّى `chmod` | ✅ (QA‑2 أُصلح) |
| `--size` مع `--cpu` | `--size medium --cpu`، `--size auto --cpu` | يمرّ | الرمز 0 | ✅ |
| خيارٌ غير معروف | `--gpu`، `--firebase-project=x` (بعلامة =)، `""`، `--size medium --size small`، `--size` بلا قيمة، `--domain --cpu` | 2 | 2 بالرسالة الصحيحة في الستّة | ✅ |
| فشلٌ في كلّ خطوة: الأسرار | `FAKE_FAULTS` على `migrate` و`ollama-pull`، وخطوة `metrics_exporter`، والإصدار (فشلٌ وUUID غير صالح)، و502، و`smoke`، وبصمةٌ خاطئة | لا سرّ في المخرج ولا في argv ولا في البيئة | لا تسرّب في التسع | ✅ (واختباراً دائماً §1.1) |
| تكرارٌ بعد نجاحٍ | تشغيلان متتاليان بحجم `auto` | لا `up`، ولا إصدار، و`.env` كما هو | صحيحٌ، **لكنّ mtime لـ`.env` و`deploy/postgres/sizes/auto.conf` يتغيّر** (المولِّد يعيد الكتابة بالبايتات نفسها) | ملاحظة (§7) |
| تبنٍّ كالإثبات المحلّيّ | `.env` = `.env.example` + أحد عشر `HOST_PORT_*` بـ+20000 + `COMPOSE_PROJECT_NAME=aizzak-install-proof`، `--cpu --no-monitoring`، النطاق `install-proof.invalid` | `adopted` ثمّ `READY`، والمشروع باسمه، والتكرار «already converged» | كما هو. **وبـCompose الحقيقيّ** (`config` وحده): الاسم `aizzak-install-proof`، و24 خدمة، والمنافذ المنشورة كلّها في 2xxxx/3xxxx (20080، 20443، 35432، 36333، 36379، 36380، 36432، 38200، 39000، 39001)، ولا `container_name` ثابت | ✅ |
| ميزانيّة الإثبات على هذا الجهاز | `auto_size.py --mode cpu --profiles local-llm` ثمّ `resource-budget.sh --block` في نسخةٍ مؤقّتة | 0 بـ11.39 من 11.40 | 0 بـ11.39 من 11.40 ج.ب (هامش 0.01). ومع المراقبة 1 (12.47)، والكرت 1 (14.28) | ✅ (يطابق التصميم) |
| `bundle.sh`: شجرة متّسخة | تعديلٌ غير مُلتزَم عليه، وآخر في الفهرس (`git add`) | لا يدخلان | الأرشيف واحد (sha نفسه) | ✅ |
| `bundle.sh`: وسمٌ غائب، وcommit مخالف | وسم 2026.10.98 غير موجود؛ ملفّ بصماتٍ بـcommit آخر | 4 ثمّ 5 | 4 و5 | ✅ |
| `bundle.sh`: وسمٌ موسوم (annotated) | `git tag -a` | يمرّ | يمرّ | ✅ |
| `bundle.sh`: مسارٌ فيه مسافة، وتشغيلٌ من مجلّدٍ آخر | `--out "out dir"`، وcwd خارج المستودع | يعمل | يعمل | ✅ |
| `bundle.sh`: ثلاثة بناءاتٍ متزامنة إلى المجلّد نفسه | ثلاثة خيوط | واحدٌ ينجح والباقي 6 | `[0, 6, 6]` ولا ملفٌّ مؤقّت متبقٍّ | ✅ |
| `bundle.sh`: ملفّ بصماتٍ بنهايات CRLF | `\n`→`\r\n` | 5 برسالةٍ مفهومة | 5، لكنّ الرسالة تُظهر commitين متطابقين ظاهراً (الفرق `\r`) | ✅ (QA‑5: صار يُقبل) |
| `bundle.sh --help` خارج المستودع | نسخةٌ من السكربت في `/tmp` | يطبع الاستعمال | الرمز 3 «not inside a git repository» | ✅ (QA‑4 أُصلح) |
| اتّساق الوثائق | الجمل الخمس الممنوعة في `13733ce` ثمّ الآن | موجودةٌ ثمّ غائبة | صحيح، في الملفّات الستّة | ✅ |

## 4. الأخطاء المكتشفة

بعد جولة الإصلاح الأولى: **لا خطأ مفتوح** عدا ملاحظة QA‑7. الإصلاحات تحقّقتُ منها بالشيفرة والاختبار (التفاصيل في عمود الحالة).

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| QA‑1 | **`.env` بنهايات CRLF يُعدّ أوّل تثبيت في التكرار.** `step_checks_host` يبحث عن سطر العلامة بـ`grep -qxF '# >>> aizzak install >>>'`، و`-x` لا يطابق `...>>>\r`. فيُفعَّل فحص المساحة (الرمز 12) على تكرارٍ مشروع، وتُتخطّى حالة «التكرار». الباقي (`env_tool`) يقرأ CRLF صحيحاً. `install.sh:917`. العلاج: `grep -qxF` بعد إزالة `\r`، أو البحث بـ`env_tool`. | minor | 1) ثبّت (أو شغّل `_step_env` في الاختبار). 2) حوّل `.env` إلى CRLF (`sed -i 's/$/\r/'`). 3) شغّل `step_checks_host` بـ`FAKE_DF_KB=5242880` (5 ج.ب). **المتوقَّع:** `FIRST_INSTALL=0` ونجاح. **الفعلي:** `FIRST_INSTALL=1` والرمز 12 «only 5 GB free». | **مُصلَح** (`0122db4`): `tr -d '\r' <.env | grep -qxF`. تحقّقتُ من الشيفرة؛ الاختبار `test_a_crlf_env_still_counts_as_an_installed_server` يجتاز بلا `xfail`. |
| QA‑2 | **نافذة صلاحيّةٍ في الكتابة الذرّيّة.** `atomic_write` ينشئ الملفّ المؤقّت بـ`os.open(..., 0o666)` فيأخذ 0644 من `umask 022`، ويكتب كلّ المحتوى ويعمل `fsync`، **ثمّ فقط** ينسخ صلاحيّة الهدف بـ`chmod`. فمن ضبط `.env` على 0600 (ما يوصي به التقرير، وهو سيناريو AC‑4.6) وأعاد التثبيت بمسارٍ يكتب (التبنّي، و`size-block` لحجمٍ جديد) يُكتب نسخةٌ كاملة بكلمات السرّ بصلاحيّة 0644 في الحزمة، ولو للحظة. ولو قُتلت العمليّة في تلك النافذة تبقى النسخة (QA‑3). التصميم نفسه (§3.3.5) يرتّب الخطوات هكذا. `install.sh:461-470`. العلاج: `os.fchmod(fd, S_IMODE(existing.st_mode))` قبل أوّل `write`. **أعرضه على `security-reviewer`**: أنزلتُه إلى minor لأنّ القرار Q‑4 يترك `.env` نفسه 0644 على أيّ حال. | minor (أمنيّ) | شغّل `env_tool compose` على ملفّ 0600 مع تجسّسٍ على `os.fsync` يسجّل صلاحيّة `.install-tmp-*`. **المتوقَّع:** `0600`. **الفعلي:** `0644`. | **مُصلَح** (`0122db4`): `os.fchmod`/`os.fchown` على الواصف قبل أوّل بايت. تحقّقتُ من الشيفرة؛ اختبار التجسّس على `fsync` (0600 أثناء الحمل) يجتاز بلا `xfail`. |
| QA‑3 | **`.install-tmp-<pid>` المخلَّف لا يُنظَّف.** `atomic_write` يحذف مؤقّتَه هو فقط (`install.sh:458-459`)، وفي التشغيل التالي pid آخر. قتلٌ بـ`kill -9` في النافذة يترك ملفّاً مقروءاً للجميع فيه أسرار تشغيلٍ ميّت (أو نسخةً من `.env` كامل مع QA‑2). | minor | ضع `.install-tmp-12345` في الحزمة وشغّل التثبيت: يمرّ ويبقى الملفّ. | **مُصلَح** (`0122db4`): `atomic_write` يحذف كلّ `.install-tmp-*` أولاً. اختبار جديد `test_a_stale_temp_file_from_a_killed_run_is_removed_by_the_next_write` يجتاز. (ملاحظة: الحذف يشمل مؤقّت أيّ pid؛ يحرسه `lock_bundle` من تشغيلين معاً.) |
| QA‑4 | `bundle.sh --help` لا يعمل إلّا داخل المستودع (الرمز 3 قبل قراءة الوسائط). `main` ينادي `require_tools` و`require_repo_root` قبل `parse_args` (`bundle.sh:296-298`). `install.sh --help` يعمل في أيّ مكان. | minor | انسخ `bundle.sh` إلى `/tmp` وشغّله بـ`--help`. | **مُصلَح** (`0122db4`): فحص `--help`/`-h` في أوّل `main`. اختبار `test_help_works_outside_a_repository` يجتاز. |
| QA‑5 | ملفّ بصماتٍ بنهايات CRLF يُرفض (5) برسالةٍ تبدو متناقضة: «built from commit X\r, but the tag is X». المتوقَّع رسالةٌ تسمّي النهايات. `bundle.sh:171-186`. | minor | حوّل `release-digests.txt` إلى CRLF وشغّل `bundle.sh`. | **مُصلَح بتغيير سلوك** (`0122db4`): ملفّ CRLF صار **يُقبل** وينتج أرشيفاً مطابقاً بايتاً ببايت. اختبار `test_a_digests_file_with_crlf_line_endings_builds_the_same_bundle` يجتاز. (يخالف نصّ كتبته «يُرفض»، وهو أنفع للمشغّل.) |
| QA‑6 | **زمن الاختبارات يتجاوز NFR‑10 / AC‑8.5.** الزيادة في `pytest tests/unit` المقيسة 29 إلى 58 ث (أربعة أزواجٍ متتابعة، أدناها +15 ث في أهدأ لحظة) مقابل حدّ 15 ث، لا +11.4 ث التي ادّعاها الباني. أغلبها `test_install_sh.py` (17.9 ث) و`test_install_sh_config.py` (4.6 ث). التفاصيل والاقتراح في §1.3. | **major** | على شجرتين نظيفتين عند `13733ce` و`f187d1e`: `time pytest -p no:cacheprovider tests/unit` على كلٍّ بالتناوب مرّتين. **المتوقَّع:** الفرق ≤ 15 ث. **الفعلي:** +33 و+29 ث (بلا `.git`)، و+58 و+33 ث (مع `.git`). | **مُصلَح**: +10.6 إلى +12.1 ث في ثلاثة أزواج (§1.3). الهامش ضيّق. |
| QA‑7 | **ملاحظة، لا خطأ:** يشترط `install.sh` bash ≥ 4 لكنّه يعمل بـ`set -u` ويوسّع مصفوفاتٍ فارغة (`"${args[@]}"` في `step_preflight`). في bash 4.0–4.3 هذا «unbound variable». الخوادم المستهدفة (Python ≥ 3.10، Compose ≥ 2.20) أحدث من ذلك عمليّاً، فلا أثر. الأنسب رفع الحدّ إلى 4.4 أو حراسة التوسعة. | info | — | للعلم |

## 5. الحكم

**PASS** — بشرطٍ واحد باقٍ خارج يدي: نجاح الإثبات المحلّيّ (§6) لـAC‑6.5 وAC‑7.4. البوّابات الستّ خضراء (6131 ناجحاً، لا متخطّى، لا `xfail`)؛ 40 معياراً آليّاً ناجحة بتأكيداتٍ تطابق نصّها (وقد راجعتُ فرق الاختبارات مع `514c34e`: لم يُحذف اختبارٌ، وأعدتُ تأكيدين أسقطهما التسريع، §1.1)؛ NFR‑10 تحقّق (+10.6 إلى +12.1 ث من 15)؛ QA‑1…QA‑6 أُصلحت وتحقّقتُ منها؛ و5 معايير [خادم]/[مالك] خارج هذه المرحلة. لا خطأ مفتوح. الباقي: الإثبات المحلّيّ، وهامش الزمن الضيّق (3 ث).

## 6. قائمة الإثبات المحلّيّ (AC‑6.5 وAC‑7.4)

لم أشغّل شيئاً منها. تشغّلها أنت مع المالك بعد مرحلتي. **أثبتُّ بيدي (دون Docker daemon) أنّ الأرضيّة سليمة:** الميزانيّة تمرّ اليوم بـ11.39 من 11.40 ج.ب (هامش 0.01 فقط، فأيّ تغيير في ذاكرة WSL أو أيّ عمليّةٍ ترفع «المجموع» تجعله [خادم])؛ والقرص حرّ 786 ج.ب؛ والمنافذ المنشورة كلّها منقولة؛ و`config --images` يقبل `repo:tag@sha256`.

### 6.1 ما يجب أن يظهر في AC‑6.5

1. `sudo SYSCTL_ROOT=… SYSCTL_D_DIR=… DOCKER_CONFIG=/home/anas/.docker ./install.sh --domain install-proof.invalid --firebase-project aizzak-agent --cpu --no-monitoring` يخرج **0**، ويطبع `install: READY` و`/health 200, /health/ready 200, stack_smoke PASS`. والمخرج كلّه إلى ملفٍّ (`2>&1 | tee proof.log`).
2. `docker compose logs vault | grep -c 'initialized; unseal key'` يعطي **1** (النصّ موجودٌ في `deploy/vault/start.sh:140`).
3. في كلّ حاويةٍ تحمل المفتاح (**ثمانٍ في وضع المعالج: سبعٌ دائمة هي `app` و`ops-scheduler` و`outbox-relay` و`wal-shipper` والعمّال الثلاثة، و`migrate` المنتهية بـ0**؛ و`backup` ليست في الملفّ الشخصيّ): طول `VAULT_SECRET_ID` في `docker inspect` = **36**. الطول وحده، لا القيمة.
4. مشروع `aizzak`: قائمتا `docker ps -a --filter label=com.docker.compose.project=aizzak --format '{{.ID}} {{.Names}} {{.CreatedAt}}'` و`docker volume ls --filter label=…=aizzak` **متطابقتان** قبل وبعد (الحالة تتغيّر بالإيقاف، والمعرّفات لا).
5. **لا سرّ في `proof.log`:** افحصه بأمرٍ لا يطبع القيم (يقرأ `.env` بصلاحيّة root، ويطبع «found 0» أو عدداً فقط) للـ13 وللمفتاح ولتوكن Vault الجذر.
6. `.env` باقٍ 0644 ومملوكٌ لـroot (مسار «adopted» يحفظ صلاحيّة الملفّ القائم)، والتقرير فيه `mode 0644 -- run: chmod 600 .env`.
7. صورتا الإصدار (`app` و`embedding`) وحدهما سُحبتا بالبصمة، ولم يظهر `Pulling` ثانيةً أثناء `up` (إثباتٌ أنّ Compose يجد صورةً سُحبت بـ`repo@sha256` وهي مسمّاةٌ `repo:tag@sha256`، وهذا **غير مقيس** في التصميم §7). وإن سحب، فالبديل `docker tag` المذكور.
8. أنّ MinIO قبلت اسم المستخدم بـ48 حرفاً (A‑7): `minio-bootstrap` انتهت بـ0.
9. **لا ملفّات مملوكة لـroot في `/home/anas/.docker`** بعد التشغيل: `find /home/anas/.docker -user root`. فإن وُجدت فـ`chown` لها (لأنّ عمليّة root عملت بـ`DOCKER_CONFIG` الخاصّ بالمالك).

### 6.2 ما يجب أن يظهر في AC‑7.4

قبل التشغيل الثاني وبعده، في مجلّد الإثبات:
1. `docker compose config --hash '*'` (بصماتٌ فقط): **متطابقة** لكلّ خدمة.
2. معرّف كلّ حاوية و`State.StartedAt` (و`Created`): **متطابقة**. **والمنتهيّة (`migrate` و`minio-bootstrap` و`vault-bootstrap` و`nginx-certs` و`wal-archive-init` و`ollama-pull`) بالذات:** لم يُعَد تشغيلها. هذا هو الجزء غير المقيس (design §3.3.6)، والمعيار هنا يحسمه.
3. التشغيل الثاني بالوسائط والبيئة نفسها يخرج **0**، ويحوي «`boot: already converged: no container touched`»، ولا «`boot: starting`».
4. (إضافة) `docker events --since <t0> --until <t1> --filter label=com.docker.compose.project=aizzak-install-proof` فارغ خلال التشغيل الثاني: لا `create` ولا `start` ولا `die`.
5. sha256 لـ`.env` كما كان (لا mtime: المولِّد يعيد الكتابة بالبايتات نفسها، §7).

### 6.3 الإجراء المصحَّح (يحلّ محلّ خطوات design.md §5.4 حيث يخالفها)

الترتيب هو ما يُشغَّل. ما تغيّر عن §5.4 معلَّم بـ**(مصحَّح)** أو **(جديد)**. لا `sudo docker login` ولا `docker logout` في أيّ خطوة (قرار ⚠️‑1 (ب)).

1. **(جديد) فحصٌ مسبق للسجلّ قبل إيقاف أيّ شيء.** يشغّله المالك: `sudo DOCKER_CONFIG=/home/anas/.docker docker manifest inspect anastawahia/aizzak@sha256:<بصمة app الحقيقيّة لـ2026.10.1> >/dev/null; echo $?` ويجب **0**. إن كان `config.json` يستعمل `credsStore` (مساعدٌ خارجيّ، شائع مع Docker Desktop في WSL) فقد لا يجده root بـ`PATH` الذي يحدّده `sudo`، فيقف التثبيت عند الرمز 13 **بعد** إيقاف المكدّس الحيّ. لا يقرأ وكيلٌ الملفّ.
2. **الحزمة.** وسمٌ محلّيّ خفيف `v2026.10.97` على `HEAD` النهائيّ للفرع (لا يُدفع أبداً: `release.yml` يعمل عند دفع `v*`). ثمّ ملفّ البصمات بثلاثة أسطر:
   - `anastawahia/aizzak:app-2026.10.97 sha256:<بصمة app الحقيقيّة لـ2026.10.1> <COMMIT>`
   - `anastawahia/aizzak:embedding-2026.10.97 sha256:<بصمة embedding الحقيقيّة> <COMMIT>`
   - `anastawahia/aizzak:embedding-cuda-2026.10.97 sha256:` متبوعةً بـ64 صفراً `<COMMIT>`
   - **(مصحَّح) العمود الثالث `<COMMIT>` = `git rev-parse v2026.10.97^{commit}` (commit الوسم المحلّيّ)** في الأسطر الثلاثة، لا commit إصدار 2026.10.1، وإلّا يخرج `bundle.sh` بـ5.
   - ثمّ `deploy/release/bundle.sh --digests <الملفّ> --out /tmp/claude-1000/ib-proof 2026.10.97`، ويُقارَن sha256 المطبوع.
3. **خطّ الأساس** لمشروع `aizzak`: `docker ps -a --filter label=com.docker.compose.project=aizzak --format '{{.ID}} {{.Names}} {{.CreatedAt}}'` و`docker volume ls --filter label=com.docker.compose.project=aizzak`. ثمّ يوقف المالك المكدّس الحيّ بالخدمات المسمّاة (قرار 2026‑10‑05). السبب: ذاكرة الإثبات 11.39 من 11.40 ج.ب، لا المنافذ (منقولة).
4. **الفكّ:** `sudo tar -xzf aizzak-2026.10.97.tar.gz -C /tmp/claude-1000/ib-proof` (الملكيّة لـroot شرط). ثمّ في مجلّد الحزمة: `sudo cp .env.example .env`، و`sudo sed -i` لـ`HOST_PORT_*` الأحد عشر بإضافة 20000 (20080 و20443 و35432 و36432 و36379 و36380 و39000 و39001 و36333 و38200 و33000)، وإضافة سطر `COMPOSE_PROJECT_NAME=aizzak-install-proof`. يُتحقَّق قبلها بـ`ss -ltn` أنّ المنافذ حرّة. (هذا مسار `adopted`.)
5. **التشغيل (مصحَّح: `DOCKER_CONFIG` على الأمر نفسه):** من مجلّد الحزمة:
   `sudo SYSCTL_ROOT=<scratch>/sysctl SYSCTL_D_DIR=<scratch>/sysctl.d DOCKER_CONFIG=/home/anas/.docker ./install.sh --domain install-proof.invalid --firebase-project aizzak-agent --cpu --no-monitoring 2>&1 | tee proof.log`
   (`<scratch>/sysctl/vm/overcommit_memory` يُنشأ مسبقاً بالقيمة الحاليّة.) هذه التركيبة **الوحيدة** التي تمرّ على هذا الجهاز (تحقّقتُ: 11.39 من 11.40). ثمّ تُنفَّذ بنود §6.1.
6. **AC‑7.4:** قبل التشغيل الثاني: بصمات `config --hash '*'`، ومعرّفات الحاويات و`StartedAt` و`Created`، وsha256 لـ`.env`. ثمّ **الأمر نفسه حرفاً بحرف**، ثمّ بنود §6.2.
7. **(جديد) بعد التشغيل:** `find /home/anas/.docker -user root` فارغ (وإلّا `chown`)، لأنّ عمليّة root عملت بـ`DOCKER_CONFIG` المالك.
8. **التنظيف (مصحَّح):** `sudo docker compose down` في مجلّد الإثبات **بلا `-v`**، ثمّ `docker compose start` للمكدّس الحيّ وفحص صحّته، ثمّ `git tag -d v2026.10.97`. **لا `docker logout`** (يمحو دخول المالك في ملفّه). أحجام `aizzak-install-proof_*` ومجلّد scratch (ملكيّة root) يحذفها المالك.

**ما لا يثبته الإثبات:** مساره `adopted` وحده؛ المسار `created` (0644 بـ`umask` على Docker حقيقيّ) يبقى لـ7.3، فلا يُحتسب دليلاً على AC‑4.1. وتحذير: `ollama-pull` يسحب نموذجاً بالجيجابايتات وحدّه 900 ث (الرمز 60 «chat would answer 502»)؛ التكرار يستأنف، فيُسجَّل الزمن ولا يُعدّ فشلاً.

## 7. معايير اختبارها أضعف من نصّها

| AC | الضعف | الأثر |
|---|---|---|
| AC‑8.2 | (معالَج في `b23f3d0`) الحارس صار دالّةً `key_policy_problems` يشغّلها اختبارٌ على وثيقةٍ سليمة ثمّ على وثيقةٍ فيها كلّ جملةٍ ممنوعة وحدها، وعلى وثيقةٍ لا تذكر `install.sh` | معالَج |
| AC‑7.5 | يقارن **الرموز** لا **المعاني** (الاختبار كما في `test_gpu_preflight.py` السابق) | صغير. معنى الرمز في الوثيقة يمكن أن ينحرف عن الرأس |
| AC‑7.3 | النصّ «لا شيء يُلمس»، والاختبار يقيس الحاويات (لا `up`) وsha و`mode` لـ`.env`، لا الملفّات الأخرى ولا mtime. وقد وجدتُ أنّ التكرار بحجم `auto` يعيد كتابة `.env` و`deploy/postgres/sizes/auto.conf` بالبايتات نفسها (يتغيّر mtime، وتتغيّر inode لأنّ الكتابة `os.replace`) | لا أثر على Compose (البصمة من المحتوى). أثرٌ نظريّ: ربطٌ لملفٍّ مفرد قد يرى inode قديماً. ربط `deploy/postgres/sizes` مجلّداً، فلا |
| `test_help_prints_the_usage...` | (معالَج) لقطة قبل التشغيل وبعده | معالَج |
| AC‑4.2 | كان يُفحص على التشغيلين الناجحين وحدهما (عولج بالاختبار المضاف §1.1) | معالَج |

## 8. ملاحظاتٌ للمنسّق

- **ما لم يُشغَّل بحكم القيود:** الإثبات المحلّيّ، و`tests/integration`، وأيّ `docker compose up/run/exec/pull/down/stop`. الاختبارات التي تشغّل `docker compose config` على نسخٍ مؤقّتة جرت (بوّابة 5 تتضمّنها)، ولم تلمس daemon ولا المكدّس.
- **`test-rules.sh`** يشغّل `docker run --rm` لـ`promtool`/`amtool` وحدهما (حاويات مؤقّتة)، لا المكدّس الحيّ.

</div>
