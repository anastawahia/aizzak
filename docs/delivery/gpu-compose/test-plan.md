<div dir="rtl">

# خطّة الاختبار: الكرت داخل Compose — Ollama والتضمين (المرحلة 2 من خطّة خادم الكرت)

> **الجولة 1 من الجودة، بلا حاويات** · 2026‑10‑14 · الفرع `feat/gpu-compose` عند `3fe51ea` (قبل C8).
> المراجع: [`stories.md`](stories.md) (64 معياراً)، و[`design.md`](design.md) §5 (خطّة الاختبار §5.3، وصياغات §5.4، والإجراءات §5.5).
> **لم تُشغَّل أيّ حاوية في هذه الجولة**: لا `up` ولا `run` ولا `build` ولا سحب صور ولا اختبارات تكامل. كلّ أمر `docker compose` هنا بـ`-p qa-config --env-file .env.example` و`config` وحده. ولم يُقرأ `.env` ولا `deploy/load/*` ولا شهادات `nginx` ولا حجم `vault-init`.

**الحكم في سطرين** (التفصيل في §5):
- **هذه الجولة (الآليّ والاستكشافيّ): PASS.** البوّابات الخمس خضراء، ولا عيب مانع ولا كبير، وكلّ معيار له اختبار أو إجراء أو شرط مؤجّل.
- **الإثباتات اليدويّة P‑1…P‑6: ⏸ الجولة التالية** (وافق المالك 2026‑10‑14؛ الشرط المسبق: ذاكرة متاحة ≥ 5 ج.ب). الحكم النهائيّ للجودة بعدها.

## 1. مصفوفة التغطية

**المسارات تحت `tests/unit/` ما لم يُذكر غيره.** الاختبارات المذكورة تحقّقتُ أنّها موجودة وتؤكّد ما يقوله المعيار (قرأتُ كلّ ملفّ منها؛ وكلّ اختبارٍ كتبتُه أنا جُرِّب بتحويراتٍ للشيفرة، §3 الصفّ 20). المعايير التي تغيّرت صياغتها بـ§5.4 (تسعة) معلَّمة، والصياغة المعتمدة هي صياغة §5.4.

| AC | نوع الاختبار | الملف/الاختبار أو الإجراء | الحالة |
|---|---|---|---|
| AC‑1.1 | وحدة | `test_gpu_preflight.py::test_never_installs_anything[driver_missing,toolkit_missing,compose_old,all_ok,all_ok_smoke]` (صفر نداء لبدائل التثبيت الستّة) · `::test_script_text_hygiene` · `::test_install_and_service_commands_appear_only_in_the_advice_text` | ✅ |
| AC‑1.2 | وحدة | `test_gpu_preflight.py::test_no_usable_driver_is_exit_10[missing,nonzero,empty-list]` (الرمز 10، «NVIDIA driver»، ولا `docker run` حتّى مع `--smoke`) | ✅ |
| AC‑1.3 | وحدة | `test_gpu_preflight.py::test_missing_runtime_is_exit_12_with_the_advice_not_the_action` · `::test_a_runtime_named_nvidia_something_is_not_the_nvidia_runtime` | ✅ |
| AC‑1.4 | وحدة | `test_gpu_preflight.py::test_compose_version_is_compared_semantically[12 قيمة: v2.19.1←13، v2.20.0←0، 2.29.7←0، v5.3.1←0، 2.9.0←13، 2.100.0←0، نصّ غير مفهوم←13، رمز غير صفريّ←13 …]` | ✅ |
| AC‑1.5 | وحدة | `test_gpu_preflight.py::test_all_green_without_smoke_exits_0_and_starts_no_container` · `::test_smoke_runs_exactly_one_pinned_gpu_container` · `::test_the_smoke_image_in_the_script_is_the_pinned_one` · `::test_smoke_failure_is_exit_14_and_relays_dockers_message` · `::test_a_failed_pull_is_also_exit_14` (بصياغة §5.4: خياران بعد `nvidia-smi`) | ✅ |
| AC‑1.6 | وحدة | `test_gpu_preflight.py::test_no_environment_value_reaches_the_output[5 سيناريوهات]` (`CANARY_SECRET` لا يظهر في stdout ولا stderr) | ✅ |
| AC‑1.7 | محلّيّ | تشغيل QA اليوم: `deploy/gpu-preflight.sh` ← الرمز **0** في 0.3 ث، والأسطر الأربعة `OK` (تعريف 592.82 · Docker 29.6.1 · وقت التشغيل `nvidia` · Compose 5.3.1). **التوقّع الأصليّ (الرمز 12) سقط** لأنّ المالك ثبّت الأداة في ملحق أ؛ والمسار السلبيّ مغطّىً بـAC‑1.3 (وبما في §3 بند B‑1) | ✅ يدويّ (نُفِّذ) |
| AC‑1.8 | أداة | P‑3 الخطوة 1 (`--smoke`). دليلٌ مسبق من المالك في `status.md`: `--smoke` على `ed899de` ← الأسطر الخمسة OK، `rc=0` | ⏸ P‑3 |
| AC‑1.9 | نصّ+وحدة | `test_gpu_preflight.py::test_the_server_doc_lists_the_scripts_exit_codes` (مجموعة الرموز {0,2,10,11,12,13,14} في الجدول = في رأس السكربت، والأمر، و`<div dir="rtl">`، ولا سرّ) · قراءة QA للوثيقة · روابطها الخمسة تُجيب HTTP 200 (فُحصت اليوم، §3) | ✅ |
| AC‑2.1 | وحدة | `test_compose_local_llm.py::test_the_ollama_service_shape` · `::test_the_ollama_image_is_the_pinned_native_version` | ✅ |
| AC‑2.2 | وحدة | `test_compose_local_llm.py::test_the_ollama_pull_service_shape` · `test_healthcheck_coverage.py::test_one_shot_services_are_actually_one_shot` (و`ollama-pull` في `_ONE_SHOT`) | ✅ |
| AC‑2.3 | وحدة | `test_ollama_pull_models.py`: (أ) `::test_the_compose_default_routing_pulls_exactly_gemma3_1b` · (ب) `::test_the_env_example_routing_pulls_exactly_gemma3_1b_fallback_route_included` · (ج) `::test_a_mixed_table_pulls_each_ollama_model_once_and_nothing_else` · (د) `::test_no_ollama_route_pulls_nothing_and_exits_0` · (هـ) `::test_invalid_json_exits_2_with_no_http_call[7 قيم]` | ✅ |
| AC‑2.4 | وحدة | `test_compose_local_llm.py::test_ollama_base_url_defaults_to_the_in_stack_service` · `::test_env_example_sets_no_other_ollama_base_url` | ✅ |
| AC‑2.5 | وحدة | `test_compose_local_llm.py::test_app_and_the_three_workers_wait_for_ollama_optionally[4]` · `::test_no_edge_requires_ollama_or_ollama_pull` (بصياغة §5.4: يشمل حافّة `ollama-pull`) | ✅ |
| AC‑2.6 | config | `test_compose_local_llm.py::test_local_llm_adds_exactly_ollama_and_ollama_pull` (لم يُتخطَّ: Docker CLI موجود) · وتشغيل QA اليدويّ (§3): بلا profile 33 خدمة، ومع `local-llm` 35، و`app.depends_on.ollama.required=false` | ✅ |
| AC‑2.7 | محلّيّ | P‑1 (§4) | ⏸ P‑1 |
| AC‑2.8 | خادم | ش‑5 (التجربة 7.3): محادثةٌ على المسار المحلّيّ بـ`COMPOSE_PROFILES=local-llm` | ⏳ ش‑5 |
| AC‑3.1 | وحدة | `test_compose_local_llm.py::test_the_bridge_is_profiled_and_otherwise_unchanged` (قاموسٌ مثبَّت من `master` ‏`1814998`) | ✅ |
| AC‑3.2 | وحدة | `test_compose_local_llm.py::test_the_four_bridge_edges_are_optional[4]` · `::test_no_edge_requires_the_bridge` · `test_healthcheck_coverage.py::test_the_bridge_is_probed_end_to_end_but_gates_nothing` (معدَّل: `required is False` والحواف الأربع بالضبط) | ✅ |
| AC‑3.3 | وحدة | `test_resource_budget.py::test_the_patterns_actually_find_something` (28/5/6) · `::test_the_runbook_ledger_carries_the_numbers_this_module_computes` (35.60 / 57.28) · `::test_the_script_and_this_module_agree` · `test_gpu_compose_hygiene.py::test_the_runbook_service_table_places_the_bridge_and_the_new_services` (الدفتر) | ✅ |
| AC‑3.4 | config | `test_compose_local_llm.py::test_wsl_ollama_adds_exactly_the_bridge` (لا «depends on undefined service») | ✅ |
| AC‑3.5 | وحدة | `test_compose_local_llm.py::test_env_example_offers_both_profiles_commented_only` · `test_gpu_compose_hygiene.py::test_env_example_activates_no_compose_selector` | ✅ |
| AC‑3.6 | مالك | ش‑2: على `.env` المالك الحقيقيّ. **تحليلٌ اصطناعيّ مسبق** من QA (§3، لا بديل عنه): على `.env.example` + `OLLAMA_BASE_URL=…:11435` اصطناعيّاً تتطابق البصمات قبل/بعد | ⏳ ش‑2 |
| AC‑3.7 | CI | ش‑1: `gh pr checks` ← `quality` و`integration` خضراوان | ⏳ ش‑1 |
| AC‑3.8 | خادم | ش‑5: `docker compose ps -a` بلا `ollama-bridge` (نصفه مثبتٌ بـAC‑3.4) | ⏳ ش‑5 |
| AC‑4.1 | وحدة | `test_embedding_cuda_image.py::test_cuda_requirements_pin_the_same_versions_and_differ_only_in_the_index` · `::test_the_cuda_index_matches_the_driver_the_preflight_smoke_checks` | ✅ |
| AC‑4.2 | وحدة | `test_embedding_cuda_image.py::test_one_arg_selects_the_requirements_and_defaults_to_cpu` · `::test_the_arg_sits_after_the_apt_layer_so_that_layer_stays_shared` · `test_gpu_compose_hygiene.py::test_every_dockerfile_base_is_pinned[2]` | ✅ |
| AC‑4.3 | وحدة | `test_embedding_service_app.py::test_health_is_200_with_the_loaded_models_identity_once_started` (cpu/float32) · `::test_health_reports_cuda_and_float16_when_the_device_is_cuda` · `::test_the_loader_places_the_model_on_the_chosen_device` · `::test_the_device_is_not_read_from_the_environment`. ومعها Q‑10: `::test_require_cuda_refuses_a_cpu_start_before_loading` وأخواتها | ✅ |
| AC‑4.4 | وحدة | `test_release_build_push.py::test_image_list_has_three_images_and_no_reserved_slot` · `::test_build_calls_are_amd64_with_the_right_dockerfile_context_and_tag` · `::test_the_cuda_build_call_is_the_embedding_one_plus_the_build_argument` · `::test_build_failure_pushes_nothing[embedding-,embedding-cuda-]` · `::test_existing_tag_is_refused[3]` · `::test_success_prints_one_digest_line_per_image_and_the_commit` · `test_embedding_cuda_image.py::test_the_release_script_and_the_dockerfile_agree_on_the_cuda_argument` · و`--list-images` يدويّاً (§3) | ✅ |
| AC‑4.5 | config | `test_embedding_cuda_image.py::test_the_build_overlay_builds_embedding_without_the_cuda_arg` · `test_compose_gpu_overlay.py::test_only_the_release_script_builds_cuda_and_a_cpu_build_under_its_name_refuses` (أ) | ✅ |
| AC‑4.6 | محلّيّ | P‑2 (§4) (بصياغة §5.4: دون المتغيّر `cpu`، وبـ`EMB_REQUIRE_CUDA=1` ترفض) | ⏸ P‑2 |
| AC‑4.7 | CI | ش‑4: أوّل وسم `v2026.10.2` فما بعد: الوسوم الثلاثة والبصمات و`publish` ≤ 60 د و«Runner disk after» ≥ 20 ج.ب | ⏳ ش‑4 |
| AC‑4.8 | أداة | P‑3 الخطوة 3: `/health` يقول `cuda`/`float16` | ⏸ P‑3 |
| AC‑4.9 | أداة | P‑4 بـ`tests/eval/vector_parity.py compare` (الأداة مختبَرة هنا: `test_vector_parity.py`، §5) | ⏸ P‑4 |
| AC‑4.10 | خادم | ش‑5: `tests/eval/run_calibration.py` على الفهرسين (Q‑3 ج) | ⏳ ش‑5 |
| AC‑4.11 | أداة | P‑5 بـ`vector_parity.py soak` و`memory.peak` (الأداة مختبَرة: `test_vector_parity.py::test_soak_sends_the_ac_4_11_load_and_reports_it` · `::test_soak_exits_nonzero_when_the_service_errors`) | ⏸ P‑5 |
| AC‑5.1 | وحدة | `test_compose_gpu_overlay.py::test_the_overlay_has_only_services_and_exactly_three` | ✅ |
| AC‑5.2 | وحدة | `test_compose_gpu_overlay.py::test_ollama_gets_exactly_one_gpu_reservation` (المساواة الحرفيّة للقاموس) | ✅ |
| AC‑5.3 | وحدة | `test_compose_gpu_overlay.py::test_embedding_is_one_cuda_replica_with_a_written_memory_limit` | ✅ |
| AC‑5.4 | وحدة | `test_compose_gpu_overlay.py::test_app_gets_utility_only_and_no_wsl_path` | ✅ |
| AC‑5.5 | وحدة | `test_compose_gpu_overlay.py::test_the_overlay_builds_publishes_and_names_nothing` | ✅ |
| AC‑5.6 | config | `test_compose_gpu_overlay.py::test_the_overlay_resolves_one_cuda_replica_and_leaves_the_balancer_alone[None,local-llm]` · `::test_the_overlay_changes_only_the_three_services_it_names` · وتحقّق QA يدويّ (§3) | ✅ |
| AC‑5.7 | config (§5.4) | `test_compose_gpu_overlay.py::test_only_the_release_script_builds_cuda_and_a_cpu_build_under_its_name_refuses` (أ) `yml:build` ← صورة المعالج بلا وسيط (ب) `yml:gpu` ← CUDA بلا `build` (ج) `yml:gpu:build` ← `EMB_REQUIRE_CUDA=1` (د) الأساسيّ بلا الحارس · `::test_the_overlay_header_says_never_with_the_build_overlay`. وتحقّق QA يدويّ للتركيبتين غير المدعومتين (§3) | ✅ |
| AC‑5.8 | وحدة | `test_compose_gpu_overlay.py::test_the_base_file_reserves_no_device` · `test_embedding_fleet.py::test_the_fleet_is_three_replicas` (دون تغيير) | ✅ |
| AC‑5.9 | أداة | P‑3 الخطوات 4–6 (`/api/ps` و`memory.used` و`nvidia-smi` من `app`) | ⏸ P‑3 |
| AC‑5.10 | خادم | ش‑5: عمليّتان على الكرت و`/admin/system/stats` بلا `gpu_error` | ⏳ ش‑5 |
| AC‑6.1 | نصّ+وحدة | `test_gpu_compose_hygiene.py::test_the_vram_budget_names_every_consumer_with_a_label` · `::test_the_vram_budget_arithmetic_is_right` (436,207,616 بايت، 777.5 م.ب، 7369/14746/22118، والنسب) · `::test_the_vram_budget_states_its_measurement_method_and_the_conditions` · قراءة QA (§3) | ✅ |
| AC‑6.2 | وحدة | `test_compose_local_llm.py::test_ollama_concurrency_knobs_and_their_relation` · `test_compose_gpu_overlay.py::test_the_ollama_knobs_live_in_the_base_file_only` | ✅ |
| AC‑6.3 | وحدة | `test_compose_local_llm.py::test_every_ollama_route_is_gemma3_1b` | ✅ |
| AC‑6.4 | أداة | P‑6 (§4) | ⏸ P‑6 |
| AC‑6.5 | خادم | ش‑5: مثل P‑6 عبر الـAPI مع فهرسة جارية | ⏳ ش‑5 |
| AC‑7.1 | مالك | ش‑2: بصمات `.env` المالك الحقيقيّ. **تحليلٌ اصطناعيّ مسبق** (§3): على `.env.example` + سطر المالك الاصطناعيّ، بصمتا «services» و«body» متطابقتان قبل وبعد، في الحالتين (بلا profile، ومع `--profile backup --profile wsl-ollama`) | ⏳ ش‑2 |
| AC‑7.2 | مالك | ش‑2: القائمة البيضاء بلا `environment` (مثبَت اصطناعيّاً، ومعه ضابط سلبيّ: حذف `OLLAMA_BASE_URL` من البيئة يُغيّر البصمة) | ⏳ ش‑2 |
| AC‑7.3 | config | `test_compose_local_llm.py::test_the_default_stack_is_the_28_standing_and_5_one_shots` · ومقارنة QA مع `git show master:docker-compose.yml`: 34 ← 33 خدمة، **والفرق `ollama-bridge` وحده** | ✅ |
| AC‑7.4 | بوّابات | §2: الفحوص 1–5 خضراء. الفحص 6 (`test-rules.sh`) يحتاج حاوية فلم يُشغَّل هذه الجولة: شغّله المنسّق على `ed899de` ← OK (27 قاعدة)، و`alerts.yml` لم يتغيّر بعده. `git diff master -- .importlinter` فارغ، ولا `ignore_imports` جديد | ✅ |
| AC‑7.5 | وحدة | `test_gpu_compose_hygiene.py::test_every_compose_image_is_pinned[2]` · `::test_every_dockerfile_base_is_pinned[2]` · `::test_the_pin_classifier_tells_a_pin_from_a_floating_reference` · `::test_the_from_parser_skips_stage_references_and_resolves_arg_defaults` · `::test_the_compose_files_name_both_kinds_of_image` | ✅ |
| AC‑7.6 | وحدة | `test_gpu_compose_hygiene.py::test_no_credential_in_what_the_branch_added_or_changed[36 ملفّاً]` · `::test_no_script_sources_an_environment_file[3]` · `::test_env_example_carries_placeholders_for_secrets_and_nothing_else` · `::test_the_new_machine_read_files_hold_no_secret_assignment[4]` · `::test_the_credential_scan_finds_what_it_is_for_and_only_that`. أمّا `test_gpu_preflight.py:489` فلا يُستثنى ولا يُعدَّل: أنماط الفحص تشترط جسماً للاعتماد (`dckr_pat_` + 16 حرفاً فأكثر) فلا يطابقها النصّ الحرفيّ، ويُثبت ذلك اختبارٌ (قيمٌ تُبنى وقت التشغيل تُرصد، والنمط الحرفيّ لا) | ✅ |
| AC‑7.7 | أمر QA | `git diff --stat master...HEAD -- deploy/runpod src` ← فارغ (0 بايت). ولا فرق في `.importlinter` ولا `pyproject.toml` ولا `.github` ولا `migrations` | ✅ يدويّ (نُفِّذ) |
| AC‑7.8 | إجراء QA | worktree منفصل (الفرع الرئيسيّ لم يُبدَّل): `pytest tests/unit` عند كلّ commit من الـ11، **كلّها بالرمز 0** (5225 إلى 5391 اختباراً، §2). وكلّ commit يلمس `docker-compose*.yml` أو `services/embedding/` أو `deploy/release/` يغيّر اختباراته في الـcommit نفسه، عدا C7 (تعليقات رأس فقط) | ✅ (يدويّ، نُفِّذ) |
| AC‑7.9 | CI | ش‑1 كـAC‑3.7 | ⏳ ش‑1 |
| AC‑8.1 | وحدة | `test_compose_local_llm.py::test_env_example_offers_both_profiles_commented_only` · `test_compose_gpu_overlay.py::test_env_example_offers_the_server_lines_commented_only` · `test_compose_build_overlay.py::test_env_example_offers_the_overlay_line_commented_only` (دون تغيير) | ✅ |
| AC‑8.2 | نصّ+وحدة | `test_gpu_compose_hygiene.py::test_stack_commands_names_the_owner_line_the_overlay_and_the_preflight` (سطر المالك، الجملة `--profile X` ... «يحلّ محلّ» مرّتان، `docker-compose.gpu.yml`، `gpu-preflight.sh`، الأوامر 40–43) · قراءة QA | ✅ |
| AC‑8.3 | نصّ+وحدة | `test_gpu_compose_hygiene.py::test_the_circuit_runbook_names_both_ollama_paths` · `::test_the_alert_response_text_names_both_ollama_paths` · و`test-rules.sh` أخضر (المنسّق) | ✅ |
| AC‑8.4 | نصّ+وحدة | `test_gpu_compose_hygiene.py::test_the_runbook_service_table_places_the_bridge_and_the_new_services` (صفّ الجسر `wsl-ollama`، وصفّا `ollama` و`ollama-pull` تحت `local-llm`، و§2‑ي بجوار الدفتر) · اختبارات AC‑6.1 · قراءة QA | ✅ |

**الحصيلة (64 معياراً):**

| الحالة | العدد | المعايير |
|---|---|---|
| ✅ آليّ يمرّ اليوم | **42** | AC‑1.1…1.6، 1.9 · AC‑2.1…2.6 · AC‑3.1…3.5 · AC‑4.1…4.5 · AC‑5.1…5.8 · AC‑6.1…6.3 · AC‑7.3…7.6 · AC‑8.1…8.4 |
| ✅ يدويّ نفّذتُه اليوم بلا حاوية | **3** | AC‑1.7، AC‑7.7، AC‑7.8 |
| ⏸ إثباتٌ يدويّ في الجولة التالية (P‑1…P‑6) | **8** | AC‑1.8 (P‑3) · AC‑2.7 (P‑1) · AC‑4.6 (P‑2) · AC‑4.8 (P‑3) · AC‑4.9 (P‑4) · AC‑4.11 (P‑5) · AC‑5.9 (P‑3) · AC‑6.4 (P‑6) |
| ⏳ مؤجَّل بشرط ش‑n | **11** | ش‑1: AC‑3.7، AC‑7.9 · ش‑2: AC‑3.6، AC‑7.1، AC‑7.2 · ش‑4: AC‑4.7 · ش‑5: AC‑2.8، AC‑3.8، AC‑4.10، AC‑5.10، AC‑6.5 |
| 🔴 ناقص (بلا اختبار ولا إجراء) | **0** | — |

ملاحظتان على الحصيلة: (1) ستّةٌ من الثمانية (AC‑1.8، 4.8، 4.9، 4.11، 5.9، 6.4) هي معايير ش‑3 [أداة] وقد صارت الأداة مثبّتةً (ملحق أ)، واثنان [محلّيّ] بلا كرت (AC‑2.7، AC‑4.6). (2) معايير ش‑5 الخمسة تحتاج خادماً حقيقيّاً (7.3) ولا يغني عنها شيءٌ هنا.

### 1.1 ما كتبتُه في C8

| الملفّ | ما فيه |
|---|---|
| `tests/unit/test_gpu_compose_hygiene.py` | AC‑7.5 (كلّ `image:` وكلّ `FROM` مثبَّت، مع اختبارٍ للمصنِّف نفسه) · AC‑7.6 (فحص الاعتماد على 36 ملفّاً صريحاً، وعدم `source .env`، وقيم الأسرار في القالب) · AC‑8.2 وAC‑8.3 وAC‑8.4 · AC‑6.1 (صفّ لكلّ مستهلك بعلامة «مقيس/تقدير»، والحساب: 436,207,616 بايت، 777.5 م.ب، 7369/14746/22118، والنسب) |
| `tests/eval/vector_parity.py` | أداة قياس، لا اختبار (لا يجمعها pytest، ويثبت ذلك اختبار): `build-set` و`compare` و`soak` كما في design §5.2. `compare` و`soak` بالمكتبة القياسيّة وحدها (يثبت ذلك اختبار AST) |
| `tests/unit/test_vector_parity.py` | 48 اختباراً لدوالّ الأداة النقيّة (جيب التمام، ترتيب أعلى k، التداخل، البصمة، المئين، قاعدة النجاح Q‑3 بحدودها)، وللمجموعة (الأسئلة الـ42، ومقاطع `.docx` بالمحلّل والمقطِّع والجداول)، ولـ`compare` و`soak` طرفاً لطرف على خدمة تضمين مزيَّفة على `127.0.0.1:0` |
| `tests/eval/README.md` | قسمٌ قصير عن الأداة |
| `docs/delivery/gpu-compose/test-plan.md` | هذا الملفّ |

**اختبار الاختبارات (تحوير):** حوّرتُ أداة القياس بستّة تحويرات (جيب التمام `<=` بدل `<`، وتخفيف حدّ التداخل، وتشديد الأوّل، وحذف حارس الجهاز، وعكس كسر التعادل، وإسقاط شرط الأوّل من الحكم) فسقطت كلّها. وحوّرتُ المستندات والملفّات التي تقرؤها اختبارات النظافة بتسعة تحويرات (وسم عائم في الملفّ الإضافيّ، وصورة بلا وسم، و`FROM … latest`، وحذف جملة `--profile`، وحذف مسار الخادم من دليل التنبيه، وإزالة علامة «تقدير» من صفّ، ونمط اعتماد في سكربت، و`. ./.env` في سكربت، ونسبة 90% خاطئة)، فسقطت كلّها وأُعيدت الملفّات (`git status` نظيف عدا ملفّاتي).

**أين حدث التفاوت مع التصميم:**
- **المقاطع 174 لا 221.** `docs/hr-no-table.docx` **غير موجود في الشجرة** (أُضيف في `e105e47` وحُذف في `15a60b7`)، فالأداة تقرؤه من `e105e47` وتسجّل مصدره وبصمته. و221 في `tests/eval/README.md` هي مقاطع المستند بعد المسار الكامل (ومنه التقسيم الدلاليّ الذي يحتاج خدمة التضمين نفسها، أي دائريّ). فالمجموعة بديلٌ بالنصّ والجداول وعرض المقطع نفسها. ويبقى الشرط (ب) قابلاً للقياس (174 ≥ 10). قيمٌ فعليّة:
- بصمة المجموعة (sha256 للنصوص بترتيبها): `b3c5a7aecf98166ae33da5a468c14f4e1080c2a93c1c7f0d0bdeff9dce85d437`
- المستند: `git:e105e47:docs/hr-no-table.docx` (47148 بايت)، ‏sha256 ‏`113e8e245b8f9a05fcef694c34e04452d8ae7898d7002838eecfc7985d93548c`
- 42 نصّ سؤال و174 مقطعاً. التقطيع: نافذة 192 كلمة وتداخل 19 عند `embedding_max_input_tokens = 512`؛ 93 كتلة نصّ، و11 جداول فُجِّرت صفوفاً بـ`explode_table` (الدالّة التي يستدعيها المفهرس)، و168 قطعة قبل التقطيع؛ بلا تقسيم دلاليّ.
- **فحص AC‑7.6 لا يستثني `test_gpu_preflight.py:489` ولا يعدّله.** النمط الحرفيّ هناك (`dckr_pat_|hf_…|sk-…`) لا يطابق أنماط الفحص لأنّها تشترط جسماً للاعتماد، كما تفعل `hf_` و`sk-` أصلاً (`dckr_pat_` + 16 حرفاً فأكثر)؛ والاختبار `test_the_credential_scan_finds_what_it_is_for_and_only_that` يثبت أنّ القيم الحقيقيّة الشكل (تُبنى وقت التشغيل) تُرصَد وأنّ النمط الحرفيّ لا. وبهذا تُفحص ملفّات الاختبار ووثائق التسليم التي تذكر النمط وصفاً.

### 1.2 الشروط المؤجّلة

| الشرط | متى | ماذا | المعايير |
|---|---|---|---|
| ش‑1 | قبل الدمج (البوّابة ②) | الـPR مدفوع و`quality` و`integration` خضراوان | AC‑3.7، AC‑7.9 |
| ش‑2 | عند الدمج، قبل أوّل `up` بعده | بصمات «قبل» على `master`، ثمّ يضيف المالك `COMPOSE_PROFILES=wsl-ollama`، ثمّ «بعد»، وفحص الجسر والمحادثة (design §3.13) | AC‑3.6، AC‑7.1، AC‑7.2 |
| ش‑3 | الأداة مثبّتة (تمّ) | مشروعٌ منفصل بخدمات الكرت وحدها: **هي الإجراءات P‑3…P‑6 أدناه** | AC‑1.8، AC‑4.8، AC‑4.9، AC‑4.11، AC‑5.9، AC‑6.4 |
| ش‑4 | أوّل إصدارٍ بعد الدمج (وسم `v2026.10.2` فما بعد) | الصور الثلاث برقمٍ واحد | AC‑4.7 |
| ش‑5 | التجربة 7.3 على خادمٍ مستأجر | كلّ ما بقي | AC‑2.8، AC‑3.8، AC‑4.10، AC‑5.10، AC‑6.5 |

### 1.3 الإثباتات P‑1…P‑6: ⏸ الجولة التالية

**الحالة لكلٍّ منها: ⏸ الجولة التالية (وافق المالك 2026‑10‑14؛ الشرط المسبق: ذاكرة متاحة ≥ 5 ج.ب).** لم تُنفَّذ في هذه الجولة. ما يلي هو الأوامر جاهزةً للتنفيذ، ولا يحتاج الأمر إلى `.env` ولا يمسّ المكدّس الحيّ.

| الإجراء | المعيار | الحالة |
|---|---|---|
| P‑1 | AC‑2.7: `ollama` و`ollama-pull` على المعالج، مرّتان، وتوليدٌ واحد | ⏸ الجولة التالية |
| P‑2 | AC‑4.6: بناء صورة CUDA محلّيّاً، وحجمها، و`torch.version.cuda`، و`/health` بلا كرت، وحارس `EMB_REQUIRE_CUDA` | ⏸ الجولة التالية |
| P‑3 | AC‑1.8 وAC‑4.8 وAC‑5.9: `--smoke`، و`/health` يقول `cuda`، و`size_vram == size`، و`nvidia-smi` من `app` | ⏸ الجولة التالية |
| P‑4 | AC‑4.9: تطابق المتّجهات (`vector_parity.py compare`) | ⏸ الجولة التالية |
| P‑5 | AC‑4.11: حمل 15 دقيقة على `embedding` وذاكرته القصوى (`vector_parity.py soak`) | ⏸ الجولة التالية |
| P‑6 | AC‑6.4: حمل Ollama المتزامن مع حمل التضمين، وأعلى استعمال لذاكرة الكرت | ⏸ الجولة التالية |

**قبل البدء، وبعد كلّ إجراء:** (أ) `free -m | sed -n 2p` ← عمود `available` **≥ 5120**. (ب) لا حمل شغّال (`k6` ولا `deploy/load`)، والمكدّس الحيّ يعمل كما هو. (ج) أحجام المشاريع المنفصلة **تبقى** وحذفها يحتاج موافقة (`CLAUDE.md`). (د) لا منفذ منشور إلّا `127.0.0.1` في P‑4. ولا `-v` في أيّ `down`.

**ما غيّرتُ في أوامر design §5.5** (لا شيء في المعنى؛ هذه تتمّات، والأوامر المعتمدة هنا هي التالية):
- **P‑1:** عدّادا السجلّ يُؤخذان **بعد التشغيل الأوّل وبعد الثاني** ويُقارَنان (الأصل يعدّ مرّةً واحدة ويقول «unchanged»)، ومعه عدّاد `"/api/pull"` في سجلّ Ollama.
- **P‑4:** أُضيفت تفاصيل الحاويتين وأمر `compare` بوسائطه (`--cpu --gpu --set`). و`build-set` بلا تغيير (يقرأ المستند من `e105e47` لأنّه غير موجود في الشجرة).
- **P‑5:** صار أمر `docker run` الكامل (الشبكة `aizzak-gpu-proof_default`، وربط `tests/eval` للقراءة، والوسائط الثلاث صراحةً) بدل «من حاويةٍ مؤقّتة».
- **P‑6:** كان نثراً في التصميم، فصار سكربتاً: مولّد توليدٍ (`gen_load.py`) ومُعاين `/api/ps` (`ps_sampler.py`) ومحلّل الحكم (`analyze.py`) جُرّبت كلّها اليوم على خوادم مزيَّفة محلّيّة (بلا حاوية).

**P‑1، ‏AC‑2.7** (على المعالج، بلا كرت):

```bash
cd /home/AIZZAK
free -m | sed -n 2p                              # available >= 5120
P="docker compose -p aizzak-llm-proof --env-file .env.example -f docker-compose.yml --profile local-llm"
$P up -d ollama
$P run --rm ollama-pull                          # 1st: pulls gemma3:1b, rc 0
reg1=$($P logs ollama | grep -c 'registry.ollama.ai'); pull1=$($P logs ollama | grep -c '"/api/pull"')
time $P run --rm ollama-pull                     # 2nd: "already present -- not contacting the registry", rc 0, <= 30 s
reg2=$($P logs ollama | grep -c 'registry.ollama.ai'); pull2=$($P logs ollama | grep -c '"/api/pull"')
echo "registry lines $reg1 -> $reg2 ; /api/pull calls $pull1 -> $pull2"   # both unchanged
$P run --rm --no-deps --entrypoint python ollama-pull -c 'import json,urllib.request as u; r=u.urlopen(u.Request("http://ollama:11434/api/generate", json.dumps({"model":"gemma3:1b","prompt":"Say hi","stream":False}).encode(), {"Content-Type":"application/json"}), timeout=300); print(bool(json.load(r)["response"].strip()))'   # True
docker exec aizzak-llm-proof-ollama-1 cat /sys/fs/cgroup/memory.peak   # against the 4g estimate (design §3.2)
$P stop ollama && $P rm -f ollama
```

**P‑2، ‏AC‑4.6** (بلا كرت؛ الحجم يُكتب هنا، وإن جاوز 10 ج.ب يُعرض على المالك قبل أوّل إصدار، NFR‑9):

```bash
cd /home/AIZZAK
free -m | sed -n 2p                              # available >= 5120
docker buildx build --platform linux/amd64 --load \
  --build-arg EMBEDDING_REQUIREMENTS=requirements-cuda.txt \
  -f services/embedding/Dockerfile -t aizzak/embedding-cuda:dev .
docker image inspect aizzak/embedding-cuda:dev --format '{{.Size}}'   # into test-plan.md; > 10 GB -> owner before the first release
docker run --rm --network none --entrypoint python aizzak/embedding-cuda:dev -c "import torch; print(torch.__version__, torch.version.cuda)"   # 2.5.1+cu124 12.4
docker run --rm --network none --entrypoint python aizzak/embedding:dev      -c "import torch; print(torch.__version__, torch.version.cuda)"   # 2.5.1+cpu None
docker run -d --name emb-cuda-proof --network none aizzak/embedding-cuda:dev
docker exec emb-cuda-proof curl -fsS --retry 30 --retry-delay 2 --retry-all-errors http://127.0.0.1:8080/health   # "device":"cpu","dtype":"float32"
docker rm -f emb-cuda-proof
docker run --rm --network none -e EMB_REQUIRE_CUDA=1 aizzak/embedding-cuda:dev; echo "rc=$?"   # non-zero, the message names requirements-cuda.txt
```

**ش‑3، قبل P‑3…P‑6.** يُفرَّغ Ollama الأصليّ من الكرت أوّلاً:

```bash
curl -s 127.0.0.1:11434/api/generate -d '{"model":"gemma3:1b","keep_alive":0}'
G="docker compose -p aizzak-gpu-proof --env-file .env.example -f docker-compose.yml -f docker-compose.gpu.yml --profile local-llm"
nvidia-smi --query-gpu=memory.used --format=csv,noheader                 # baseline
```

**P‑3، ‏AC‑1.8 وAC‑4.8 وAC‑5.9:**

```bash
deploy/gpu-preflight.sh --smoke                                          # 1. rc 0, the card's name from inside the container
$G up -d embedding embedding-lb ollama && $G run --rm ollama-pull        # 2.
$G exec -T embedding curl -fsS http://127.0.0.1:8080/health              # 3. "device":"cuda","dtype":"float16"
# 4. one generation as in P-1 (from a python:3.12-slim container on the project network), then /api/ps: size_vram == size
# 5. memory.used above the baseline
$G run --rm --no-deps app nvidia-smi --query-gpu=name,memory.used --format=csv   # 6. prints the card
```

**P‑4، ‏AC‑4.9** (حاويتان على شبكةٍ مؤقّتة، `127.0.0.1` وحده):

```bash
cd /home/AIZZAK
free -m | sed -n 2p                              # available >= 5120 (two model processes)
SCRATCH=/tmp/aizzak-parity; mkdir -p "$SCRATCH"
ss -ltn '( sport = :18081 or sport = :18082 )' | tail -n +2    # must print nothing: both ports free
docker network create aizzak-parity
docker run -d --name parity-cpu  --network aizzak-parity -p 127.0.0.1:18081:8080 aizzak/embedding:dev
docker run -d --name parity-cuda --network aizzak-parity -p 127.0.0.1:18082:8080 --gpus all -e EMB_REQUIRE_CUDA=1 aizzak/embedding-cuda:dev
until curl -fsS 127.0.0.1:18081/health && curl -fsS 127.0.0.1:18082/health; do sleep 5; done   # device cpu / cuda, same max_seq_length 512
.venv/bin/python tests/eval/vector_parity.py build-set --out "$SCRATCH/set.json"
python3 tests/eval/vector_parity.py compare --cpu http://127.0.0.1:18081 --gpu http://127.0.0.1:18082 \
  --set "$SCRATCH/set.json" | tee "$SCRATCH/parity.json"; echo "rc=${PIPESTATUS[0]}"   # rc 0 only if Q-3 (a) and (b) hold
docker rm -f parity-cpu parity-cuda && docker network rm aizzak-parity
```
يُلصَق JSON النتيجة وبصمة المجموعة (`b3c5a7ae…` إن لم تتغيّر الأوعية) في هذا الملفّ. `compare` يرفض (الرمز 3) مقارنةً ليست CPU مقابل CUDA.

**P‑5، ‏AC‑4.11** (على مشروع ش‑3 القائم، `embedding` مباشرةً لا عبر الموازِن):

```bash
docker network ls --format '{{.Name}}' | grep aizzak-gpu-proof          # the project's network name
docker run --rm --network aizzak-gpu-proof_default -v /home/AIZZAK/tests/eval:/eval:ro python:3.12-slim \
  python /eval/vector_parity.py soak --url http://embedding:8080 --minutes 15 --concurrency 8 --batch 8 | tee "$SCRATCH/soak.json"
docker exec aizzak-gpu-proof-embedding-1 cat /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory.events
docker inspect --format '{{.State.OOMKilled}}' aizzak-gpu-proof-embedding-1     # false
# the limit is 3g = 3221225472: it stays if peak x 1.5 <= 3221225472 (peak <= 2147483648), else the rule in
# docker-compose.gpu.yml replaces it with peak x 1.79 rounded up to 256m -- a later commit on that file, with the measurement
```

**P‑6، ‏AC‑6.4** (على مشروع ش‑3 القائم؛ 4 = `OLLAMA_NUM_PARALLEL + 2`):

```bash
OUT=/tmp/aizzak-p6; mkdir -p "$OUT"; NET=aizzak-gpu-proof_default
cat > "$OUT/gen_load.py" <<'EOF'
"""P-6 load: N concurrent /api/generate calls (~3,000-token prompts, the shape of
08-local-runbook.md:2465) against one Ollama, for M minutes. Standard library only.
usage: python gen_load.py http://ollama:11434 <concurrency> <minutes>"""
import json, sys, threading, time, urllib.request

base, concurrency, minutes = sys.argv[1].rstrip("/"), int(sys.argv[2]), float(sys.argv[3])
WORDS = ("hours leave overtime payroll insurance contract employee manager policy "
         "benefit holiday medical report approval deduction grace period weekly daily").split()
deadline = time.monotonic() + minutes * 60
ok = failed = 0
lock = threading.Lock()
errors: dict[str, int] = {}


def prompt(n: int) -> str:  # ~2,300 words ~ 3,000 tokens; the first words differ per request
    body = " ".join(WORDS[(n + i) % len(WORDS)] for i in range(2300))
    return f"Request {n}. Summarize in one sentence:\n{body}"


def worker(k: int) -> None:
    global ok, failed
    n = k * 100000
    while time.monotonic() < deadline:
        n += 1
        req = urllib.request.Request(
            base + "/api/generate",
            json.dumps({"model": "gemma3:1b", "prompt": prompt(n), "stream": False,
                        "keep_alive": "30m", "options": {"num_ctx": 8192, "num_predict": 64}}).encode(),
            {"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                text = json.load(r).get("response", "")
            with lock:
                ok += 1 if text.strip() else 0
                failed += 0 if text.strip() else 1
        except Exception as err:  # noqa: BLE001
            with lock:
                failed += 1
                errors[repr(err)[:120]] = errors.get(repr(err)[:120], 0) + 1


threads = [threading.Thread(target=worker, args=(k,), daemon=True) for k in range(concurrency)]
[t.start() for t in threads]
[t.join() for t in threads]
print(json.dumps({"concurrency": concurrency, "minutes": minutes, "ok": ok, "failed": failed, "errors": errors}))
sys.exit(0 if failed == 0 and ok > 0 else 1)
EOF
cat > "$OUT/ps_sampler.py" <<'EOF'
"""P-6: sample Ollama's /api/ps every <interval> seconds for <seconds>; one JSON line each."""
import json, sys, time, urllib.request

base, seconds, interval = sys.argv[1].rstrip("/"), float(sys.argv[2]), float(sys.argv[3])
end = time.monotonic() + seconds
while time.monotonic() < end:
    row = {"t": round(time.time(), 1)}
    try:
        with urllib.request.urlopen(base + "/api/ps", timeout=10) as r:
            row["models"] = [{k: m.get(k) for k in ("name", "size", "size_vram")}
                             for m in json.load(r).get("models", [])]
    except Exception as err:  # noqa: BLE001
        row["error"] = repr(err)[:120]
    print(json.dumps(row), flush=True)
    time.sleep(interval)
EOF
cat > "$OUT/analyze.py" <<'EOF'
"""P-6 verdict: python3 analyze.py <dir> (vram.csv ps.jsonl gen.json soak.json offload.txt)."""
import json, pathlib, re, sys

d = pathlib.Path(sys.argv[1])
ratios = []
for line in (d / "vram.csv").read_text().splitlines():
    m = re.match(r"\s*(\d+)\s*MiB,\s*(\d+)\s*MiB", line)
    if m:
        ratios.append(int(m[1]) / int(m[2]))
ps = [json.loads(x) for x in (d / "ps.jsonl").read_text().splitlines() if x.strip()]
loaded = [m for row in ps for m in row.get("models", [])]
mismatch = [m for m in loaded if m["size_vram"] != m["size"]]
gen = json.loads((d / "gen.json").read_text())
soak = json.loads((d / "soak.json").read_text())
offload = [x for x in (d / "offload.txt").read_text().splitlines() if x.strip()]
out = {
    "vram_samples": len(ratios),
    "vram_max_ratio": round(max(ratios), 4) if ratios else None,
    "ps_samples": len(ps), "ps_model_rows": len(loaded), "ps_size_vram_ne_size": len(mismatch),
    "ps_errors": sum("error" in r for r in ps),
    "gen_ok": gen["ok"], "gen_failed": gen["failed"],
    "soak_ok": soak["requests_ok"], "soak_failed": soak["requests_failed"],
    "ollama_offload_lines": len(offload),
}
out["pass"] = bool(ratios and max(ratios) < 0.90 and loaded and not mismatch and not offload
                   and gen["failed"] == 0 and gen["ok"] > 0 and soak["requests_failed"] == 0)
print(json.dumps(out, indent=1))
sys.exit(0 if out["pass"] else 1)
EOF
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader -l 1 > "$OUT/vram.csv" & SMI=$!
docker run --rm --network $NET -v "$OUT:/out:ro" python:3.12-slim python /out/ps_sampler.py http://ollama:11434 630 10 > "$OUT/ps.jsonl" & PS=$!
docker run --rm --network $NET -v /home/AIZZAK/tests/eval:/eval:ro python:3.12-slim \
  python /eval/vector_parity.py soak --url http://embedding:8080 --minutes 10 > "$OUT/soak.json" & SOAK=$!
docker run --rm --network $NET -v "$OUT:/out:ro" python:3.12-slim python /out/gen_load.py http://ollama:11434 4 10 > "$OUT/gen.json"
wait $SOAK $PS; kill $SMI
$G logs ollama | grep -iE 'offload|cpu' > "$OUT/offload.txt"; true
docker inspect --format '{{.Name}} OOMKilled={{.State.OOMKilled}}' aizzak-gpu-proof-ollama-1 aizzak-gpu-proof-embedding-1   # both false
python3 "$OUT/analyze.py" "$OUT"; echo "rc=$?"     # max memory.used/total < 0.90, size_vram == size, no offload line, no failed request
```
(سطر `offload` الذي يطابق `cpu` في سجلّ Ollama عند الإقلاع قد يكون عاديّاً: يُراجع النصّ قبل الحكم، ولا يُعدّل `analyze.py` ليخفيه.) **في الختام:** `$G down` وكذلك `docker compose -p aizzak-llm-proof … down` (بلا `-v`)؛ الأحجام تبقى. وتُكتب الأرقام المقيسة في §2‑ي من `08-local-runbook.md` بدل «تقدير».

## 2. نتائج بوابات المشروع

على `feat/gpu-compose` ومعه ملفّات C8 (غير مُودَعة وقت التشغيل)، بترتيب `CLAUDE.md`، و`PATH=/home/AIZZAK/.venv/bin:$PATH`.

```
$ ruff format --check .
815 files already formatted
rc=0

$ ruff check .
All checks passed!
rc=0

$ mypy src
Success: no issues found in 467 source files
rc=0

$ lint-imports
--------------------------------------

Layered architecture (inward only) KEPT
Domain is pure (no technical imports) KEPT
Application depends on domain + framework only KEPT
Business modules do not import each other KEPT
Agents do not import each other KEPT
Agents never import API or Infrastructure KEPT
Infrastructure imported only by Composition Root KEPT
Framework does not import outer layers KEPT

Contracts: 8 kept, 0 broken.
rc=0

$ pytest -rs tests/unit tests/architecture tests/eval
# (-o addopts="--strict-markers -p no:anyio": the project addopts minus -q, so that the summary line prints)
(no SKIPPED, no FAILED, no ERROR lines)
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
================= 5502 passed, 7 warnings in 142.62s (0:02:22) =================
exit=0

# for the record: 5392 passed at ed899de (orchestrator) + 48 test_vector_parity + 62 test_gpu_compose_hygiene = 5502
```

**البوّابة 6 (`deploy/prometheus/test-rules.sh`):** لم تُشغَّل هذه الجولة لأنّها تحتاج Docker (حاوية `promtool`) وهذه الجولة بلا حاويات. شغّلها المنسّق على `ed899de`: OK (27 قاعدة). و`deploy/prometheus/alerts.yml` لم يتغيّر بعد `ed899de` (`git diff ed899de HEAD -- deploy/prometheus` فارغ). **تُعاد في الجولة التالية** قبل الحكم النهائيّ.

**الاختبارات التي تتخطّى:** لا شيء في `tests/unit tests/architecture tests/eval`: اختبارات `[config]` تعمل (Docker CLI موجود هنا)، وهي تتخطّى فقط إن غاب `docker compose`. `tests/integration` لم يُشغَّل (قرار `CLAUDE.md`: المكدّس الحيّ مستعمَل، وكلّ ما فيها `live_*`).

**AC‑7.8، `pytest tests/unit` عند كلّ commit** (`git worktree` في مجلّد QA المؤقّت، `git checkout --detach` داخله لا في الفرع، ثمّ أُزيل):

```
858f3ce5de66bb0b226d2418bb5c06f28985bafa | docs(delivery): gpu-compose requirements, stories and design (server-gpu phase 2) | touches: none | rc=0 | ================= 5225 passed, 7 warnings in 123.18s (0:02:03) =================
3032cd9d42a40a7187595af0ce1db820b3ca0178 | feat(embedding): /health reports device and dtype; EMB_REQUIRE_CUDA refuses a CPU start | touches: services/embedding/app.py, | rc=0 | ================= 5252 passed, 7 warnings in 102.36s (0:01:42) =================
92d94e804439bccd15b1fb819fc99497183c7d4e | docs(delivery): gpu-compose status after C1 | touches: none | rc=0 | ================= 5252 passed, 7 warnings in 101.22s (0:01:41) =================
7a08169ce4ba207689658eed75e14556d06ff06b | feat(deploy): gpu-preflight.sh and the server prerequisites (2.1) | touches: none | rc=0 | ================= 5302 passed, 7 warnings in 103.66s (0:01:43) =================
0109a3baefa350fb9063a3f1b50a725e59f89917 | feat(compose): ollama and ollama-pull behind local-llm (2.2) | touches: docker-compose.yml, | rc=0 | ================= 5354 passed, 7 warnings in 123.20s (0:02:03) =================
402e3317bf873189c1cc865cc93274f3bf5c9850 | feat(compose): ollama-bridge behind wsl-ollama; optional edges (2.3) | touches: docker-compose.yml, | rc=0 | ================= 5362 passed, 7 warnings in 125.85s (0:02:05) =================
7212bf3fc3255d6100978ff8c60eb7cb97f4b9bd | feat(embedding): CUDA requirements, Dockerfile ARG, embedding-cuda release image (2.4) | touches: deploy/release/build-push.sh,services/embedding/Dockerfile,services/embedding/requirements-cuda.txt, | rc=0 | ================= 5378 passed, 7 warnings in 130.24s (0:02:10) =================
f537e4c42d09a1aead99f5eacfc8ff2a2ce93667 | feat(compose): docker-compose.gpu.yml and the VRAM budget (2.5, 2.6) | touches: docker-compose.gpu.yml, | rc=0 | ================= 5391 passed, 7 warnings in 131.43s (0:02:11) =================
ed899dec5107a97a03dcdb1e2d1e1b960d672ac9 | docs: the owner's profile line, the GPU overlay and both Ollama paths (US-8) | touches: docker-compose.test.yml,docker-compose.wsl-gpu.yml, | rc=0 | ================= 5391 passed, 7 warnings in 130.85s (0:02:10) =================
a0b6fd7c0222430eeacd3942ac931d77d27c9234 | docs(delivery): gpu-compose status after C2-C7 | touches: none | rc=0 | ================= 5391 passed, 7 warnings in 130.83s (0:02:10) =================
3fe51ea392014bd4abe8b7e352db876aff130954 | docs(delivery): gpu-compose status, toolkit installed | touches: none | rc=0 | ================= 5391 passed, 7 warnings in 131.02s (0:02:11) =================
DONE (worktree removed)
```

## 3. الاختبار الاستكشافي

كلّه قراءةٌ أو `config` أو سكربتات على بدائل: بلا حاوية. ما لا ينطبق يُقال صراحةً.

| # | السيناريو | الخطوات | المتوقّع | الفعليّ | الحكم |
|---|---|---|---|---|---|
| 1 | المكدّس الافتراضيّ | `docker compose -p qa-config --env-file .env.example -f docker-compose.yml config --format json` | 33 خدمة؛ بلا `ollama` ولا الجسر؛ حواف `app` إليهما `required: false`؛ `OLLAMA_BASE_URL` = `http://ollama:11434`؛ صفر `devices` | كما هو؛ `app.depends_on`: `ollama` (`service_healthy`) و`ollama-bridge` (`service_started`) كلاهما `required: false` | ✅ |
| 2 | `--profile local-llm` | نفسه + `--profile local-llm` | 35: تُضاف `ollama` و`ollama-pull` | 35؛ `ollama/ollama:0.13.5` بحدّ 4 vCPU/4 ج.ب؛ `ollama-pull` بـ0.25/64م | ✅ |
| 3 | `--profile wsl-ollama` | نفسه | 34: يُضاف الجسر وحده | 34؛ `alpine/socat:1.8.0.3`، `profiles=['wsl-ollama']` | ✅ |
| 4 | الـprofile معاً | `--profile local-llm --profile wsl-ollama` | 36 | 36 | ✅ |
| 5 | ملفّ الكرت | `-f docker-compose.yml -f docker-compose.gpu.yml`، بلا profile ومع `local-llm` | `embedding`: صورة CUDA ونسخة واحدة وحجز `gpu`، وحدّ 3 ج.ب مع بقاء `cpus: 1`، و`EMB_REQUIRE_CUDA=1`؛ `app`: `utility`؛ `embedding-lb` كما هو؛ `ollama` بحجز `gpu` مع الـprofile ولا يظهر بدونه | كما هو: `aizzak/embedding-cuda:dev` · `replicas=1` · `limits={cpus:1, memory:3221225472}` · `EMB_BATCH`/`EMB_TORCH_THREADS`/`OMP_NUM_THREADS` باقية · الفحص الصحّيّ باقٍ | ✅ |
| 6 | متغيّرات الإصدار | `EMBEDDING_CUDA_IMAGE_REPO=anastawahia/aizzak EMBEDDING_CUDA_IMAGE_TAG=embedding-cuda-2026.10.2` (+ متغيّرا المعالج) | الصورتان بوسميهما، وبلا ملفّ الكرت المعالج بـ3 نسخ | `anastawahia/aizzak:embedding-cuda-2026.10.2`؛ وبلا الملفّ `anastawahia/aizzak:embedding-2026.10.1` بـ3 نسخ. وسمٌ **فارغ** يعود إلى `aizzak/embedding-cuda:dev` (سلوك `:-`): يفشل السحب ولا يشغّل torch المعالج، كما في D‑2 | ✅ |
| 7 | ملفّ WSL | `-f docker-compose.yml -f docker-compose.wsl-gpu.yml` | `app` بربط `/dev/dxg` و`/usr/lib/wsl`؛ التضمين على المعالج بلا حجز `nvidia` | كما هو | ✅ |
| 8 | تركيبتان **غير مدعومتان** | `gpu+build`، و`gpu+wsl-gpu` | الأولى محميّة بـ`EMB_REQUIRE_CUDA=1`؛ الثانية موثَّقة «لا تُركَّب معاً» | `gpu+build`: يُحلّ اسم CUDA مع `build` والحارس `1` (يرفض الإقلاع بصورة معالج، كما في D‑1). `gpu+wsl-gpu`: **يُحلّ بصمت** و`app` يحمل حجز `utility` **وربط WSL معاً**؛ لا شيء يمنعها ولا اختبار | ✅ كما صُمِّم، ومعه ملاحظة O‑1 |
| 9 | الفرق مع `master` (AC‑7.3) | `config --services` لـ`git show master:docker-compose.yml` مقابل الفرع | الفرق `ollama-bridge` وحده | 34 ← 33؛ `15d14 < ollama-bridge` | ✅ |
| 10 | البصمات الاصطناعيّة (تمهيد ش‑2) | دالّتا `svc` و`body` من design §3.13 على `master` ثمّ الفرع، بـ`.env.example` + `OLLAMA_BASE_URL=…:11435` اصطناعيّاً (+ `COMPOSE_PROFILES=wsl-ollama` بعدها)، بلا profile ومع `--profile backup --profile wsl-ollama` | تتطابق البصمات | تتطابق في الحالتين (بلا profile: خدمات `7ef9fe86af75` وbody `78bca42067fe`؛ ومع `backup`: `78dc6b6c81dc` و`69f7526e1d3e`)؛ **ضابط سلبيّ**: حذف `OLLAMA_BASE_URL` من البيئة يغيّر البصمة (`adbf270125ec`). ليست بديلاً عن ش‑2 (بيئة اصطناعيّة لا `.env` المالك) | ✅ (معلومة) |
| 11 | `gpu-preflight.sh` بلا `--smoke` | `deploy/gpu-preflight.sh` · `--help` · `--install` · `""` · `-h --bogus` · `--bogus -h` | ≤ 10 ث، لا يطبع البيئة؛ الاستعمال الخاطئ ← 2؛ `--help` ← 0 بلا أيّ نداء | rc 0 في **0.3 ث** والأسطر الأربعة `OK` (الأداة مثبّتة الآن)؛ `--help` rc 0؛ `--install` rc 2؛ `""` rc 2؛ `-h --bogus` rc 0؛ `--bogus -h` rc 2 (الأوّل يحسم). `--smoke` لم يُشغَّل (حاوية) | ✅ (ومعه ملاحظة O‑3) |
| 12 | مسارات حافّة السكربت | قراءة `gpu-preflight.sh` سطراً سطراً + اختباراته | المهلة، `permission denied` ← 11 لا «الأداة غائبة»، الأسبقيّة 11>13>10>12، CDI/Desktop يغلبه `--smoke`، تعدّد الكروت، `mktemp` وتنظيف `trap` | كلّها مغطّاة باختباراتٍ تشغّل السكربت حقيقيّاً ببدائل `PATH`؛ لا ثغرة وجدتُها (المهلة 5 ث لكلّ نداء، `--smoke` 300 ث) | ✅ |
| 13 | `build-push.sh` | `--list-images` · بلا وسائط · `2026.13.1` · `--list-images extra` | ثلاثة أسطر بلا `reserved`؛ الأخطاء ← 2 | 3 أسطر والثالث بوسيط CUDA؛ بلا وسائط rc 2 مع الاستعمال؛ الإصدار الخاطئ rc 2؛ `--list-images extra` يتجاهل الزائد (rc 0) وهذا سلوكٌ سابق للمرحلة | ✅ (ومعه O‑4) |
| 14 | حافّة `pull_models.py` | `PROVIDER_ROUTING` غائب · `OLLAMA_BASE_URL=ollama:11434` (بلا مخطّط) · `OLLAMA_PULL_CONNECT_WAIT_S=inf` · 5000 مساراً · اسم نموذج عربيّ · اسم بمسافات | رفض واضح أو سلوك مفهوم | غائب ← rc 2 «empty or not set»؛ بلا مخطّط ← rc 3 بعد **30 ث كاملة** وبرسالة «unknown url type» (B‑2)؛ `inf` يُقبل وينتظر للأبد (B‑3)؛ 5000 مسار ← 5000 نموذجاً دون مشكلة؛ العربيّ يُحفظ؛ المسافات لا تُقصّ (النداء سيفشل بـ4، سلوكٌ مقبول) | ✅ مع B‑2 وB‑3 (طفيفان) |
| 15 | أخطاء الشبكة والواجهة | اختبارات `pull_models` (Ollama يخطئ بـ500، لا يُبلَغ، خطأ في تيّار السحب، تيّارٌ بلا `success`) و`vector_parity` (خدمة ميّتة، `/embed` بـ500، مجموعة معدَّلة) | رموز خروج مميَّزة ورسالة تسمّي السبب بلا تسريب نصّ | 3/4 في السحب؛ 3 للمقارنة، و1 في `soak` مع تسمية الخطأ؛ لا تظهر قيمة البيئة ولا نصوص المجموعة في الرسائل | ✅ |
| 16 | مدخلات فارغة وضخمة وعربيّة | `vector_parity`: مجموعة فارغة/ناقصة/معدَّلة/بلا JSON؛ متّجه صفريّ؛ نصوص عربيّة | رفضٌ مسمّى لا نجاحٌ زائف | `load_set` يرفض الخمسة؛ متّجه صفريّ يرفع «no cosine»؛ مجموعة بـ6 مقاطع (أقلّ من top‑k) ← رفض «too small»؛ النصوص العربيّة تمرّ بـ`ensure_ascii=False` ويثبّتها اختبار البصمة بـUTF‑8؛ بناء 42+174 في 0.5 ث | ✅ |
| 17 | صلاحيّات ناقصة، مستأجر آخر، إرسال مزدوج | — | — | **لا ينطبق**: لا نقطة HTTP جديدة، ولا جدول، ولا RLS، ولا مستأجرون (design §4). والتشغيل المزدوج الوحيد المعنيّ هو `ollama-pull` مرّتين: مغطّى بالخادم المزيَّف (`test_a_second_run_after_a_pull_contacts_no_registry`) ويُثبَت حقيقيّاً في P‑1 | ✅ / لا ينطبق |
| 18 | RTL وLTR عند 375px | — | — | **لا ينطبق**: لا واجهة. وثائق C2/C7 داخل `<div dir="rtl">` (يفحصه اختبار لـ`deploy-server.md`، وهذا الملفّ كذلك) | لا ينطبق |
| 19 | روابط الوثيقة والأوسمة | `curl -L` على الروابط الخمسة في `docs/deploy-server.md`؛ واجهة Docker Hub للأوسمة | الروابط تُجيب (status.md: «مكتوبة من الذاكرة، يتحقّق منها المراجع»)؛ الأوسمة والبصمات كما في التعليقات | الروابط الخمسة **HTTP 200** (اثنان بعد إعادة توجيه إلى `…/latest/index.html` و`…/drivers/`). `ollama/ollama:0.13.5` بصمته `sha256:2c9595c555fd…` = تعليق `docker-compose.yml`؛ و`nvidia/cuda:12.4.1-base-ubuntu22.04` ‏`sha256:0f6bfcbf267e…` = تعليق `gpu-preflight.sh`؛ و`python:3.12-slim` موجود | ✅ |
| 20 | هل تفشل الاختبارات الجديدة فعلاً؟ | تحوير 6 في الأداة و9 في ملفّات اختبارات النظافة (§1.1) | كلّ تحوير يُسقط اختباراً | سقطت التحويرات الخمس عشرة، وأُعيدت الملفّات | ✅ |
| 21 | `build-set` على المستند الحقيقيّ | `.venv/bin/python tests/eval/vector_parity.py build-set --out …` مرّتين | يعمل رغم غياب `docs/hr-no-table.docx` من الشجرة، وحتميّ | قرأه من `e105e47`؛ الملفّان **متطابقان بايتاً** (`cmp`)؛ 42 سؤالاً و174 مقطعاً (§1.1) | ✅ (ومعه O‑2) |
| 22 | الذاكرة قبل الإثباتات | `free -m` | ≥ 5 ج.ب متاحة للجولة التالية | فحص المالك بعد ملحق أ: 4064 م.ب والـswap 4056/4096. وفي هذه الجولة قرأتُ 10.6 ثمّ 10.3 ج.ب متاحة (متغيّر بين قراءةٍ وأخرى)؛ **يُعاد الفحص عند بدء الجولة التالية** | ⏸ |

## 4. الأخطاء المكتشفة

لا عيب مانع ولا كبير. ثلاثة عيوب طفيفة، وأربع ملاحظات. **لم أعدّل أيّ ملفّ منتَج** (القاعدة).

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| B‑1 | **عبارات «الأداة غير مثبّتة» صارت خاطئة بعد ملحق أ** (أثبت المالك الأداة 2026‑10‑14). `docs/stack-commands.md:591`: «على هذا الجهاز اليوم الرمز **12** … والأداة غير مثبّتة»؛ و`docs/stack-commands.md:524` («غير مثبَّتة هنا… لا وجود لـ`/etc/docker/daemon.json`»)؛ وتعليق `docker-compose.wsl-gpu.yml:21-22` («NOT installed here»). **المتوقّع:** نصٌّ يصدق أو يقيّد نفسه بـ«قبل تثبيت الأداة». **الفعليّ:** السكربت اليوم يخرج بـ0، فالسطر 591 يناقضه. **المالك:** devops (وثائق) | طفيف | `deploy/gpu-preflight.sh; echo rc=$?` ← `rc=0` وسطر `toolkit OK`؛ ثمّ `grep -n "على هذا الجهاز اليوم" docs/stack-commands.md` | مفتوح (لم يُعدَّل) |
| B‑2 | **`OLLAMA_BASE_URL` بلا مخطّط يُعامَل كخادمٍ لا يُجيب.** `deploy/ollama/pull_models.py:186-203` (`wait_reachable`) و`:246`: العنوان `ollama:11434` خطأ إعدادٍ لا ينفع معه الانتظار، لكنّ السكربت ينتظر نافذة الاتّصال كاملةً (30 ث) ثمّ يخرج بـ**3** «unreachable» («unknown url type: ollama») بدل **2** «الإعداد غير صالح». **الأثر:** يقع في التشغيل اليدويّ وحده (الخدمة تثبّت العنوان `http://ollama:11434`). **المتوقّع:** رفض فوريّ بالرمز 2 يسمّي المتغيّر. **المالك:** devops | طفيف | `PROVIDER_ROUTING='{"llm":{"d":{"provider":"ollama","model":"gemma3:1b"}}}' OLLAMA_BASE_URL=ollama:11434 python -I deploy/ollama/pull_models.py; echo rc=$?` ← بعد 30 ث: `unreachable … unknown url type`، rc 3 | مفتوح |
| B‑3 | **`OLLAMA_PULL_CONNECT_WAIT_S=inf` مقبول وينتظر للأبد.** `deploy/ollama/pull_models.py:206-216` (`_connect_wait_s`): `float("inf")` يعبر شرطَي `< 0` وNaN. **المتوقّع:** رفضٌ بالرمز 2 (`math.isfinite`). **المالك:** devops | طفيف | `… OLLAMA_PULL_CONNECT_WAIT_S=inf OLLAMA_BASE_URL=http://127.0.0.1:9 timeout 4 python -I deploy/ollama/pull_models.py; echo rc=$?` ← 124 (ما زال ينتظر) | مفتوح |

**ملاحظات (ليست عيوباً، للمراجعين):**
- **O‑1.** `-f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.wsl-gpu.yml` يُحَلّ بلا خطأ ولا تحذير، و`app` يحمل حجز `utility` وربط WSL معاً. الوثائق ورأس الملفّ يقولان «لا يُركَّبان معاً»، ولا اختبار يحرس ذلك (على خلاف تركيبة ملفّ البناء المحميّة بـ`EMB_REQUIRE_CUDA`). أُرجّح أنّه مقبول لأنّ الجهاز الوحيد المعرَّض هو جهاز المالك، وقد ثبّت الأداة.
- **O‑2.** `docs/hr-no-table.docx` **ليس في الشجرة** (حُذف في `15a60b7`) بينما يذكره `tests/eval/README.md` وQ‑3 كأنّه موجود، وعدد مقاطعه المسجَّل (221) لا يُعاد إنتاجه دون التقسيم الدلاليّ. عالجته الأداة (§1.1) ولا يحتاج إجراءً.
- **O‑3.** نصّ `--help` في `gpu-preflight.sh` يعدّد «0, 10, 11, 12, 13, 14» ولا يذكر 2، وهو في الرأس والوثيقة. لا أثر.
- **O‑4.** `build-push.sh --list-images extra` يتجاهل الوسيط الزائد (سلوك المرحلة 1، خارج هذا النطاق).

## 5. الحكم

**PASS لهذه الجولة (الآليّ والاستكشافيّ)** — البوّابات 1–5 خضراء (البوّابة 6 للمنسّق على `ed899de`)، و42 معياراً آليّاً يمرّ، و3 يدويّة نُفِّذت، ولا معيار بلا اختبار أو إجراء، ولا عيب مانع أو كبير؛ والعيوب الثلاثة طفيفة (B‑1 في وثيقةٍ تخصّ تثبيت الأداة، وB‑2 وB‑3 في التشغيل اليدويّ لسكربتٍ لا تمسّه الخدمة).

**الإثباتات اليدويّة P‑1…P‑6: ⏸ الجولة التالية** (وافق المالك 2026‑10‑14؛ الشرط المسبق: ذاكرة متاحة ≥ 5 ج.ب ولا حمل شغّال). **الحكم النهائيّ للجودة يصدر بعدها**، وتبقى المعايير المؤجّلة الإحدى عشر بشروطها ش‑1…ش‑5.

</div>
