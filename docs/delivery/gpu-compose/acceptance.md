<div dir="rtl">

# قبول مالك المنتج: الكرت داخل Compose — Ollama والتضمين (المرحلة 2 من خطّة خادم الكرت، 2.1–2.6)

- **الفرع:** `feat/gpu-compose` عند `a6075a2`، من `master` ‏`1814998`. الشيفرة حتّى `8d13cb0`، وما بعده وثائق تسليمٍ فقط. 36 ملفّاً في الفرق، ولا تغيير في `src/` ولا `deploy/runpod/` ولا `.importlinter` (`git diff master...HEAD -- deploy/runpod src .importlinter` ‏0 بايت).
- **المرجع:** صياغة `stories.md` (64 معياراً)، وما عدّله منها `design.md` §5.4 وله الأسبقيّة: AC‑1.5، وAC‑2.1، وAC‑2.5، وAC‑4.4، وAC‑4.6، وAC‑5.2، وAC‑5.7، وAC‑7.1، وAC‑7.3. ثمّ قرارات المالك في `status.md`، ومنها صورة `ollama/ollama:0.34.4` بدل 0.13.5 (تعدّل Q‑5، لأنّ 0.13.5 لا يسحب `gemma3:1b`).
- **التاريخ:** 2026‑10‑15.

**ما فحصتُه بنفسي** (للقراءة فقط: لا حاوية، ولا `.env`، ولا ملفّ سرّ):
- **أسماء الاختبارات:** بحثتُ بـ`grep "def <الاسم>"` عن 47 اسماً من المذكورة أدلّةً في الجدول، فوجدتُها كلّها في `tests/unit/`.
- **ملفّات اختبار الميزة الأربعة عشر عند HEAD:** `test_gpu_preflight` و`test_compose_local_llm` و`test_ollama_pull_models` و`test_compose_gpu_overlay` و`test_embedding_cuda_image` و`test_embedding_service_app` و`test_release_build_push` و`test_release_build_push_edges` و`test_gpu_compose_hygiene` و`test_vector_parity` و`test_resource_budget` و`test_healthcheck_coverage` و`test_embedding_fleet` و`test_compose_build_overlay`. النتيجة **`432 passed`** بلا أيّ تخطٍّ. Docker موجود، فاختبارات `docker compose config` جرت فعلاً.
- **AC‑7.8 عند `8dcb24f`:** هو الـcommit الوحيد بين `3fe51ea` و`8d13cb0` الذي يغيّر شيفرةً ولم يفحصه QA وحده. أخرجتُ شجرته بـ`git archive` إلى مجلّدٍ مؤقّت، وشغّلتُ عليها الملفّات الستّة التي تقرأ Compose و`pull_models.py`. النتيجة **`153 passed`**.
- **`git diff ed899de 8d13cb0 -- deploy/gpu-preflight.sh`:** تغيّر سطرٌ واحد في نصّ الاستعمال (أُضيف الرمز 2). فتشغيل المالك `--smoke` على `ed899de` جرى على منطق السكربت الحاليّ نفسه (AC‑1.8).
- **البوّابات** (شغّلها المنسّق على `8d13cb0`، ونقلها `test-plan.md` §2): 815 ملفّاً منسَّقاً · `ruff check` نظيف · `mypy` ‏467 ملفّاً بلا خطأ · العقود 8 سليمة · **5515 passed** بلا تخطٍّ. البوّابة 6 (`test-rules.sh`) ‏OK بـ27 قاعدة على `ed899de`، و`alerts.yml` لم يتغيّر بعده.
- **المراجعتان:** `review.md` **PASS** (0 حاجبة، 1 مهمّة، 4 تحسين). `security-review.md` **PASS** (0 حرجة، 0 عالية، 2 متوسّطة، 4 منخفضة، 2 معلومات).

**مفتاح الأحكام:**
- ✅: الدليل موجودٌ ويغطّي المعيار.
- ⏳ ش‑n: **مؤجَّلٌ بشرط**. الدليل لا يُنتَج قبل الدمج، أو أجّله المالك (ش‑3). الدليل الجزئيّ مذكور في الصفّ، والخطوة وصاحبها في «الشروط». **هذا ليس ✅.**
- لا يوجد 🔴: كلّ معيارٍ له اختبارٌ أو إجراءٌ مكتوب.

## US‑1: فحص متطلّبات خادم الكرت، 2.1 (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑1.1 | `test_gpu_preflight.py::test_never_installs_anything` بخمسة سيناريوهات: صفر نداءٍ لبدائل التثبيت الستّة. ومعه `::test_script_text_hygiene` و`::test_install_and_service_commands_appear_only_in_the_advice_text` | ✅ مقبول |
| AC‑1.2 | `::test_no_usable_driver_is_exit_10[missing,nonzero,empty-list]`: الرمز 10، ورسالةٌ تذكر NVIDIA driver، ولا `docker run` حتّى مع `--smoke` | ✅ مقبول |
| AC‑1.3 | `::test_missing_runtime_is_exit_12_with_the_advice_not_the_action` و`::test_a_runtime_named_nvidia_something_is_not_the_nvidia_runtime` | ✅ مقبول |
| AC‑1.4 | `::test_compose_version_is_compared_semantically` باثنتي عشرة قيمة، منها `v2.19.1`←13، و`v2.20.0` و`2.29.7` و`v5.3.1`←0، ونصٌّ غير مفهوم أو رمزٌ غير صفريّ←13 | ✅ مقبول |
| AC‑1.5 (§5.4) | `::test_all_green_without_smoke_exits_0_and_starts_no_container`، و`::test_smoke_runs_exactly_one_pinned_gpu_container`، و`::test_the_smoke_image_in_the_script_is_the_pinned_one`، و`::test_smoke_failure_is_exit_14_and_relays_dockers_message`، و`::test_a_failed_pull_is_also_exit_14` | ✅ مقبول |
| AC‑1.6 | `::test_no_environment_value_reaches_the_output` بخمسة سيناريوهات: `CANARY_SECRET` لا يظهر في stdout ولا stderr | ✅ مقبول |
| AC‑1.7 | تشغيلٌ يدويٌّ على هذا الجهاز (QA، الجولة 3): الرمز **0** في 0.3 ث، وأربعة أسطر OK (التعريف 592.82، وDocker 29.6.1، ووقت التشغيل `nvidia`، وCompose 5.3.1). **شرط المعيار تجاوزه الواقع:** التوقّع الأصليّ (الرمز 12، «بلا أداة») سقط لأنّ المالك ثبّت Toolkit 1.20.1 في 2026‑10‑14. أقبله لأنّ غاية المعيار تحقّقت: قراءةٌ صحيحة للجهاز الحقيقيّ في ≤ 10 ث (NFR‑11). ومسار الرمز 12 مثبتٌ آليّاً في AC‑1.3 | ✅ مقبول (يدويّ) |
| AC‑1.8 | P‑3 الخطوة 1 (2026‑10‑15): `deploy/gpu-preflight.sh --smoke` ← خمسة أسطر OK، وsmoke يطبع `NVIDIA GeForce RTX 4060 Laptop GPU` من داخل `nvidia/cuda:12.4.1-base-ubuntu22.04`، و**`rc=0`** (`test-plan.md` §1.3) | ✅ مقبول |
| AC‑1.9 | `::test_the_server_doc_lists_the_scripts_exit_codes`: الرموز {0,2,10,11,12,13,14} في جدول `docs/deploy-server.md` تطابق رأس السكربت، ومعها الأمر و`<div dir="rtl">` وغياب أيّ سرّ. وقرأ QA الوثيقة، والروابط الخمسة أجابت HTTP 200 | ✅ مقبول |

## US‑2: خدمة Ollama داخل Compose، 2.2 (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑2.1 (§5.4) | `test_compose_local_llm.py::test_the_ollama_service_shape` (فحص الصحّة `bash` و`/dev/tcp`، والحدود، والحجم، ولا `ports`) و`::test_the_ollama_image_is_the_pinned_version` (`0.34.4` بقرار المالك) | ✅ مقبول |
| AC‑2.2 | `::test_the_ollama_pull_service_shape` و`test_healthcheck_coverage.py::test_one_shot_services_are_actually_one_shot`، و`ollama-pull` في `_ONE_SHOT` | ✅ مقبول |
| AC‑2.3 | `test_ollama_pull_models.py`: (أ) `::test_the_compose_default_routing_pulls_exactly_gemma3_1b`، (ب) `::test_the_env_example_routing_pulls_exactly_gemma3_1b_fallback_route_included`، (ج) `::test_a_mixed_table_pulls_each_ollama_model_once_and_nothing_else`، (د) `::test_no_ollama_route_pulls_nothing_and_exits_0`، (هـ) `::test_invalid_json_exits_2_with_no_http_call` بسبع قيم | ✅ مقبول |
| AC‑2.4 | `::test_ollama_base_url_defaults_to_the_in_stack_service` و`::test_env_example_sets_no_other_ollama_base_url` | ✅ مقبول |
| AC‑2.5 (§5.4) | `::test_app_and_the_three_workers_wait_for_ollama_optionally[4]` و`::test_no_edge_requires_ollama_or_ollama_pull`، ويشمل حافّة `ollama-pull` | ✅ مقبول |
| AC‑2.6 | `::test_local_llm_adds_exactly_ollama_and_ollama_pull`، ولم يُتخطَّ. وتحقّق QA يدويّاً: 33 خدمة بلا profile، و35 مع `local-llm`، و`app.depends_on.ollama.required=false` | ✅ مقبول |
| AC‑2.7 | P‑1 نُفِّذ في الجولة 4 (2026‑10‑15، بطلب المالك؛ المكدّس الحيّ متوقّف فالمتاح 11 ج.ب) بـ`0.34.4`: التنزيل الأوّل rc 0، والثاني «already present -- not contacting the registry» rc 0 في 2 ث، و`"/api/pull"` ‏1 → 1، والتوليد `True`، و`memory.peak` ‏1.86 من 4 ج.ب (`test-plan.md` §1.3). وبهذا أُغلق المعيار الوحيد من المؤجَّلة الذي طلبت `stories.md` إثباته قبل الدمج | ✅ مقبول |
| AC‑2.8 | ش‑5، التجربة 7.3: محادثةٌ على المسار المحلّيّ بـ`COMPOSE_PROFILES=local-llm`، وصفّ `usage` على `ollama` | ⏳ ش‑5 |

## US‑3: إخراج جسر WSL من المسار العامّ، 2.3 (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑3.1 | `test_compose_local_llm.py::test_the_bridge_is_profiled_and_otherwise_unchanged`: قاموسٌ مثبَّت من `master` ‏`1814998` | ✅ مقبول |
| AC‑3.2 | `::test_the_four_bridge_edges_are_optional[4]`، و`::test_no_edge_requires_the_bridge`، و`test_healthcheck_coverage.py::test_the_bridge_is_probed_end_to_end_but_gates_nothing` بعد تعديله ليفحص `required is False` أيضاً | ✅ مقبول |
| AC‑3.3 | `test_resource_budget.py::test_the_patterns_actually_find_something` (28/5/6)، و`::test_the_runbook_ledger_carries_the_numbers_this_module_computes` (35.60 و57.28)، و`::test_the_script_and_this_module_agree` | ✅ مقبول |
| AC‑3.4 | `test_compose_local_llm.py::test_wsl_ollama_adds_exactly_the_bridge`، بلا خطأ «depends on undefined service» | ✅ مقبول |
| AC‑3.5 | `::test_env_example_offers_both_profiles_commented_only` و`test_gpu_compose_hygiene.py::test_env_example_activates_no_compose_selector` | ✅ مقبول |
| AC‑3.6 | ش‑2 على `.env` المالك. **تحليلٌ اصطناعيٌّ مسبق** من QA: على `.env.example` مع سطر `OLLAMA_BASE_URL=…:11435` اصطناعيّ، تتطابق البصمات قبل وبعد | ⏳ ش‑2 |
| AC‑3.7 | ش‑1: `quality` و`integration` على الـPR | ⏳ ش‑1 |
| AC‑3.8 | ش‑5: `docker compose ps -a` على الخادم بلا `ollama-bridge`. نصفه مثبتٌ بـAC‑3.4 | ⏳ ش‑5 |

## US‑4: صورة تضمينٍ على CUDA، 2.4 (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑4.1 | `test_embedding_cuda_image.py::test_cuda_requirements_pin_the_same_versions_and_differ_only_in_the_index` و`::test_the_cuda_index_matches_the_driver_the_preflight_smoke_checks` | ✅ مقبول |
| AC‑4.2 | `::test_one_arg_selects_the_requirements_and_defaults_to_cpu`، و`::test_the_arg_sits_after_the_apt_layer_so_that_layer_stays_shared`، و`test_gpu_compose_hygiene.py::test_every_dockerfile_base_is_pinned` | ✅ مقبول |
| AC‑4.3 | `test_embedding_service_app.py::test_health_is_200_with_the_loaded_models_identity_once_started` (cpu/float32)، و`::test_health_reports_cuda_and_float16_when_the_device_is_cuda`، و`::test_the_loader_places_the_model_on_the_chosen_device`، و`::test_the_device_is_not_read_from_the_environment` | ✅ مقبول |
| AC‑4.4 (§5.4) | `test_release_build_push.py::test_image_list_has_three_images_and_no_reserved_slot`، و`::test_build_calls_are_amd64_with_the_right_dockerfile_context_and_tag`، و`::test_the_cuda_build_call_is_the_embedding_one_plus_the_build_argument`، و`::test_build_failure_pushes_nothing[embedding-,embedding-cuda-]` | ✅ مقبول |
| AC‑4.5 | `test_embedding_cuda_image.py::test_the_build_overlay_builds_embedding_without_the_cuda_arg`، ومعه (أ) في `test_compose_gpu_overlay.py::test_only_the_release_script_builds_cuda_and_a_cpu_build_under_its_name_refuses` | ✅ مقبول |
| AC‑4.6 (§5.4) | P‑2، نفّذه QA في الجولة 2: البناء rc 0. `torch` في صورة CUDA ‏`2.5.1+cu124 12.4`، وفي صورة المعالج `2.5.1+cpu None`. بلا كرت: `/health` يقول `device=cpu` و`dtype=float32`، ومع `EMB_REQUIRE_CUDA=1` ترفض الإقلاع بالرمز 3. الحجم مكتوب: `.Size` ‏4.13 ج.ب (انظر O‑5) | ✅ مقبول (يدويّ) |
| AC‑4.7 | ش‑4: أوّل وسم `v2026.10.2` فما بعده | ⏳ ش‑4 |
| AC‑4.8 | P‑3 الخطوة 3 (2026‑10‑15): `/health` في التضمين ← `"device":"cuda","dtype":"float16"`، بـ`EMB_REQUIRE_CUDA=1` | ✅ مقبول |
| AC‑4.9 | P‑4 (2026‑10‑15) بـ`tests/eval/vector_parity.py compare` على **10 أسئلة من 42** (بطلب المالك) و174 مقطعاً: أدنى cosine ‏0.9999990 (المتوسّط 0.9999995) على النصوص الـ184 كلّها، والتداخل في أعلى 10 ‏**10/10** لكلّ سؤال، وأعلى مقطع واحدٌ في 10/10، و`compare` ‏rc 0 (`test-plan.md` §1.3). **مجموعة مخفّضة بقرار المالك:** 10 أسئلة من 42 موزّعة بالتساوي، والمقاطع كلّها، والعتبات كما هي | ✅ مقبول (10 من 42 بقرار المالك) |
| AC‑4.10 | ش‑5: `tests/eval/run_calibration.py` على الفهرسين | ⏳ ش‑5 |
| AC‑4.11 | ش‑3 (P‑5) بـ`vector_parity.py soak`. الأداة مختبَرة في `::test_soak_sends_the_ac_4_11_load_and_reports_it` | ⏳ ش‑3 |

## US‑5: ملفّ الكرت `docker-compose.gpu.yml`، 2.5 (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑5.1 | `test_compose_gpu_overlay.py::test_the_overlay_has_only_services_and_exactly_three` | ✅ مقبول |
| AC‑5.2 (§5.4) | `::test_ollama_gets_exactly_one_gpu_reservation`: مساواةٌ حرفيّة للقاموس، بلا `count` | ✅ مقبول |
| AC‑5.3 | `::test_embedding_is_one_cuda_replica_with_a_written_memory_limit`، والحدّ `3g`. أمّا كفايته فتثبتها AC‑4.11 | ✅ مقبول |
| AC‑5.4 | `::test_app_gets_utility_only_and_no_wsl_path` | ✅ مقبول |
| AC‑5.5 | `::test_the_overlay_builds_publishes_and_names_nothing` | ✅ مقبول |
| AC‑5.6 | `::test_the_overlay_resolves_one_cuda_replica_and_leaves_the_balancer_alone[None,local-llm]` و`::test_the_overlay_changes_only_the_three_services_it_names` | ✅ مقبول |
| AC‑5.7 (§5.4) | `::test_only_the_release_script_builds_cuda_and_a_cpu_build_under_its_name_refuses` بحالاته (أ)–(د)، و`::test_the_overlay_header_says_never_with_the_build_overlay`. ورفض صورة معالجٍ تحت اسم CUDA مثبتٌ فعليّاً في P‑2 (الرمز 3) | ✅ مقبول |
| AC‑5.8 | `::test_the_base_file_reserves_no_device`، و`test_embedding_fleet.py::test_the_fleet_is_three_replicas` دون تغيير | ✅ مقبول |
| AC‑5.9 | P‑3 الخطوات 4–6 (2026‑10‑15): `/api/ps` ← `size_vram == size` ‏(978,929,582)، و`offloaded 27/27 layers to GPU`، و`memory.used` ‏1188 → 2960 MiB، و`app nvidia-smi` يطبع الكرت (rc 0) | ✅ مقبول |
| AC‑5.10 | ش‑5: عمليّتان على الكرت، و`/admin/system/stats` بلا `gpu_error` | ⏳ ش‑5 |

## US‑6: ميزانيّة ذاكرة الكرت والنموذج، 2.6 (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑6.1 | `test_gpu_compose_hygiene.py::test_the_vram_budget_names_every_consumer_with_a_label`، و`::test_the_vram_budget_arithmetic_is_right`، و`::test_the_vram_budget_states_its_measurement_method_and_the_conditions`. وقرأ QA الجدول، وتحقّق المراجع من 14746 و22118 | ✅ مقبول |
| AC‑6.2 | `test_compose_local_llm.py::test_ollama_concurrency_knobs_and_their_relation` و`test_compose_gpu_overlay.py::test_the_ollama_knobs_live_in_the_base_file_only` | ✅ مقبول |
| AC‑6.3 | `test_compose_local_llm.py::test_every_ollama_route_is_gemma3_1b` | ✅ مقبول |
| AC‑6.4 | ش‑3 (P‑6). السكربتات الثلاثة جاهزة وجُرّبت على خوادم مزيَّفة | ⏳ ش‑3 |
| AC‑6.5 | ش‑5: كـP‑6 عبر الـAPI مع فهرسةٍ جارية | ⏳ ش‑5 |

## US‑7: المكدّس المحلّيّ لا يتغيّر، والبوّابات والوسوم والأسرار (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑7.1 (§5.4) | ش‑2 بـ`--profile backup --profile wsl-ollama` في «قبل» و«بعد» معاً، وW بلا `.embedding.build.args`. **تحليلٌ اصطناعيٌّ مسبق:** بصمتا «services» و«body» متطابقتان في الحالتين | ⏳ ش‑2 |
| AC‑7.2 | ش‑2. مثبتٌ اصطناعيّاً، ومعه ضابطٌ سلبيّ: حذف `OLLAMA_BASE_URL` يغيّر البصمة | ⏳ ش‑2 |
| AC‑7.3 (§5.4) | `test_compose_local_llm.py::test_the_default_stack_is_the_28_standing_and_5_one_shots`. وقارن QA مع `git show master:docker-compose.yml`: من 34 خدمة إلى 33، والفرق `ollama-bridge` وحده | ✅ مقبول |
| AC‑7.4 | البوّابات 1–5 خضراء على `8d13cb0` (الأرقام أعلاه)، والبوّابة 6 OK. `.importlinter` بلا فرق (تحقّقتُ: 0 بايت)، ولا `ignore_imports` جديد | ✅ مقبول |
| AC‑7.5 | `test_gpu_compose_hygiene.py::test_every_compose_image_is_pinned[2]`، و`::test_every_dockerfile_base_is_pinned[2]`، واختبارا المصنِّف والمحلّل | ✅ مقبول |
| AC‑7.6 | `::test_no_credential_in_what_the_branch_added_or_changed` على 36 ملفّاً، و`::test_no_script_sources_an_environment_file[3]`، و`::test_env_example_carries_placeholders_for_secrets_and_nothing_else`، و`::test_the_credential_scan_finds_what_it_is_for_and_only_that`. ومسح المراجعة الأمنيّة بالأنماط: 0 اعتماد | ✅ مقبول |
| AC‑7.7 | `git diff --stat master...HEAD -- deploy/runpod src` فارغ. أعدتُه بنفسي مع `.importlinter`: 0 بايت | ✅ مقبول (يدويّ) |
| AC‑7.8 | QA شغّل `pytest tests/unit` عند كلّ commit من الأحد عشر حتّى `3fe51ea`، وكلّها رمز 0. وكلّ commit يغيّر `docker-compose*.yml` أو `services/embedding/` أو `deploy/release/` يغيّر اختباراته معه، ما عدا C7 (تعليقات رأسٍ فقط)، وأكّد ذلك المراجع. **تحفّظ QA ضيّقتُه:** من الـcommits الخمسة بعد `3fe51ea`، اثنان وثائق فقط، و`4e02905` يضيف اختباراتٍ فقط، و`8d13cb0` هو HEAD الأخضر. وشغّلتُ على شجرة `8dcb24f` ملفّاتِ الاختبار الستّة التي تقرأ ما غيّره: **153 passed**. ما لم يُشغَّل هو `tests/unit` كاملاً عند `8dcb24f`، وخطره ضئيل لأنّ `src` لم يتغيّر | ✅ مقبول (يدويّ) |
| AC‑7.9 | ش‑1، كـAC‑3.7 | ⏳ ش‑1 |

## US‑8: الوثائق وسطر المالك (Should)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑8.1 | `test_compose_local_llm.py::test_env_example_offers_both_profiles_commented_only`، و`test_compose_gpu_overlay.py::test_env_example_offers_the_server_lines_commented_only`، و`test_compose_build_overlay.py::test_env_example_offers_the_overlay_line_commented_only` دون تغيير | ✅ مقبول |
| AC‑8.2 | `test_gpu_compose_hygiene.py::test_stack_commands_names_the_owner_line_the_overlay_and_the_preflight`، وقراءة QA. فيه جملةٌ شرطيّة قديمة (ملاحظة المراجعة المهمّة، أدناه)، ولا تمسّ ما يطلبه المعيار | ✅ مقبول |
| AC‑8.3 | `::test_the_circuit_runbook_names_both_ollama_paths` و`::test_the_alert_response_text_names_both_ollama_paths`، و`test-rules.sh` أخضر | ✅ مقبول |
| AC‑8.4 | `::test_the_runbook_service_table_places_the_bridge_and_the_new_services`، واختبارات AC‑6.1 | ✅ مقبول |

## الحصيلة

| الحالة | العدد | المعايير |
|---|---|---|
| ✅ آليّ | 42 | AC‑1.1…1.6، و1.9 · AC‑2.1…2.6 · AC‑3.1…3.5 · AC‑4.1…4.5 · AC‑5.1…5.8 · AC‑6.1…6.3 · AC‑7.3…7.6 · AC‑8.1…8.4 |
| ✅ يدويّ نُفِّذ | 9 | AC‑1.7، وAC‑1.8، وAC‑2.7، وAC‑4.6، وAC‑4.8، وAC‑4.9 (10 أسئلة)، وAC‑5.9، وAC‑7.7، وAC‑7.8 |
| ⏳ مؤجَّل بشرط | 13 | كلّها Must. ش‑1: 2 · ش‑2: 3 · ش‑3: 2 · ش‑4: 1 · ش‑5: 5 |
| 🔴 | 0 | — |

## الشروط والمتابعات (بالخطوة وصاحبها)

**لماذا يُقبل الدمج قبل أن تصير هذه المعايير ✅؟**
- **أربعة شروطٍ لا تُنتَج قبل الدمج أصلاً:**
  - ش‑1 يحتاج الـPR.
  - ش‑2 يحتاج `.env` المالك عند الدمج.
  - ش‑4 يحتاج وسماً على commit من `master`.
  - ش‑5 يحتاج خادماً مستأجراً.
  - رفض الميزة بسببها لا يعيدها إلى مرحلةٍ تستطيع إنتاج الدليل.
- **ش‑3 هو الاستثناء:** يمكن تقنيّاً قبل الدمج، فالأداة مثبّتة. لكنّ المالك أجّله (2026‑10‑15) لسببين: الاستعمال، والذاكرة (نحو 4.9–5.0 ج.ب متاحة مع المكدّس الحيّ، والـswap ممتلئ). **أقبل التأجيل** لثلاثة أسباب:
  1. **الدمج لا يغيّر مسار التشغيل على جهاز المالك.** `local-llm` وملفّ الكرت كلاهما مطفأٌ افتراضيّاً (AC‑2.6، وAC‑5.8، وAC‑7.3)، وش‑2 يثبت بالبصمات أنّ مكدّسه لم يتغيّر.
  2. **لا نشر تلقائيّ،** وRunPod لم يُلمس (AC‑7.7).
  3. **الجزء الثابت من كلّ معيار كرتٍ مثبتٌ آليّاً،** ومعه أدلّةٌ جزئيّة حقيقيّة: Ollama يرى `CUDA0`، و`app` يطبع الكرت، وصورة CUDA تُبنى وترفض الإقلاع بلا كرت، و`--smoke` بالرمز 0.
- **الخطر الذي أقبله صراحةً:** عيبٌ في مسار الكرت قد يظهر بعد الدمج. حينها يعود إلى البناء في فرعٍ جديد من `master`، وتبقى الخطوات 🟡.
- **هذا استثناءٌ صريحٌ من قاعدة «كلّ Must ✅»،** على سابقة المرحلة 1 (`docs/delivery/prebuilt-images/acceptance.md`). أعرضه على المالك في البوّابة ②.

| # | متى | الخطوة بالضبط | صاحبها | يغلق | إن فشل |
|---|---|---|---|---|---|
| ش‑1 | **قبل الدمج** (البوّابة ②) | يُدفع الفرع ويُفتح الـPR، ثمّ `gh pr checks <PR> --watch`: `quality` و`integration` خضراوان. `integration` لا تقلع الجسر أصلاً | المنسّق، بعد إذن المالك بالدفع | AC‑3.7، AC‑7.9 | يعود إلى البناء (devops-engineer)، ثمّ رجوعٌ آليّ عند QA |
| ش‑2 | **عند الدمج**، قبل أوّل `up` بعده | 1. على `master` قبل الدمج: بصمتا `config --services` و`config --format json \| jq -S '.services \| del(W)'`، بلا profile ثمّ بـ`--profile backup --profile wsl-ollama` (`design.md` §3.13 و§5.4). تُطبع البصمات وحدها.<br>2. بعد الدمج: يضيف المالك `COMPOSE_PROFILES=wsl-ollama` إلى `.env`، ويبقى سطر `COMPOSE_FILE` من 1.1 كما هو.<br>3. تُعاد البصمات، فتتطابق مع «قبل».<br>4. `docker compose up -d`، ثمّ `docker compose ps ollama-bridge` يعطي `running (healthy)`، ثمّ تكتمل محادثةٌ على المسار المحلّيّ.<br>**تنبيه R‑4:** إن نُسي السطر، لا يقلع الجسر بعد أوّل إعادة إنشاء، فتنقطع المحادثات المحلّيّة | المالك، لأنّ `.env` لا يقرؤه وكيل. أو وكيلٌ بموافقته يطبع البصمات وحدها | AC‑3.6، AC‑7.1، AC‑7.2 | فرقٌ خارج W، أو `environment` داخلها: يعود إلى التصميم (Q‑9 (1)، سطرٌ ثالث في `.env`). جسرٌ غير سليم: يعود إلى البناء |
| ش‑3 | **بعد الدمج**، في وقتٍ يحدّده المالك | الشروط (أ)–(د) في `test-plan.md` §1.3: `available ≥ 5120`، ولا حمل شغّال، والأحجام تبقى، ولا `-v`. ويُفرَّغ Ollama الأصليّ من الكرت. ثمّ الأوامر الجاهزة: **P‑5 وP‑6** (وP‑1 وP‑3 وP‑4 ✅ في 2026‑10‑15)، في مشروعين منفصلين بـ`.env.example`. إن لم تكفِ الذاكرة، يوقف المالك المكدّس الحيّ كما فعل في 2026‑10‑14. تُلصق النتائج في `test-plan.md` §1.3، ويُحدَّث هذا الملفّ | qa-engineer، أو المنسّق بإذن تشغيل الحاويات | AC‑4.11، AC‑6.4 | P‑1 بخطأ شبكة: يُعاد حتّى مرّتين. P‑1 بغير الشبكة، أو P‑3/P‑5/P‑6 خارج الحدود: يعود إلى البناء (devops-engineer)، ومنه تعديل حدّ `3g` بالقاعدة المكتوبة في `docker-compose.gpu.yml`. P‑4 خارج هامش Q‑3: يعود إلى التصميم |
| ش‑4 | **أوّل إصدارٍ بثلاث صور** (`v2026.10.2` فما بعده) | **قبل الوسم:** يُعرض على المالك حجم صورة CUDA بمقاييسه الثلاثة (O‑5): `.Size` ‏4.13 ج.ب، ومجموع الطبقات نحو 6.9 ج.ب، و`docker images` ‏11.1 ج.ب. حدّ NFR‑9 هو 10 ج.ب. ويتحقّق المالك من سعة Docker Hub (Q‑7).<br>**بعده:** `app-<v>` و`embedding-<v>` و`embedding-cuda-<v>` في `anastawahia/aizzak`، وبصماتها في السجلّ والـartifact. و`publish` ينتهي في ≤ 60 د، و«Runner disk after» ≥ 20 ج.ب | المالك يدفع الوسم أو يأذن به. ويسجّل devops-engineer النتائج في `release-notes.md` | AC‑4.7 | يعود إلى البناء (`build-push.sh` أو `release.yml`). وإن رأى المالك الحجم مرفوضاً: قرارٌ بشريّ قبل الوسم |
| ش‑5 | **التجربة 7.3** على خادمٍ مستأجر | كلّ ما بقي، بالمكدّس الكامل وملفّ الكرت و`local-llm`. تُكتب الأرقام في `docs/delivery/compose-gpu-server/` | devops-engineer. استئجار الخادم قرارٌ بشريّ وميزانيّة (قرار 2) | AC‑2.8، AC‑3.8، AC‑4.10، AC‑5.10، AC‑6.5 | يعود إلى البناء للخطوة المعنيّة، في فرع 7.3 |

## لوحة الخطّة بعد الدمج

في `docs/server-gpu-plan.md` §4 وفي `docs/server-gpu-summary.html`:
- **عند الدمج** (بعد ش‑1): تصير الخطوات 2.1–2.6 كلّها 🟡 «مدموجة، بانتظار ش‑n»، لا ✅.
- **قاعدة ✅:** لا تُعلَّم خطوةٌ ✅ قبل أن يتحقّق كلّ شرطٍ في صفّها أدناه.

| الخطوة | بعد الدمج | تصير ✅ بعد | المعايير |
|---|---|---|---|
| 2.1 | ✅ عند الدمج | لا شيء | AC‑1.8 ✅ (P‑3، 2026‑10‑15)، وهو معيار قبول 2.1 حرفيّاً (`server-gpu-plan.md:197`) |
| 2.2 | 🟡 | ش‑5 | AC‑2.7 ✅ (P‑1، 2026‑10‑15)، وAC‑2.8 (ش‑5). يمكن أن يُغلق AC‑2.8 قبل 7.3 بمحادثةٍ محلّيّة على `local-llm`، بموافقةٍ صريحة من المالك |
| 2.3 | 🟡 | ش‑2 وش‑5 | AC‑3.6 وAC‑7.1 وAC‑7.2 (ش‑2)، وAC‑3.8 (ش‑5) |
| 2.4 | 🟡 | ش‑3 وش‑4 وش‑5 | AC‑4.8 ✅ (P‑3)، وAC‑4.9 ✅ (P‑4)، وAC‑4.11 (ش‑3)، وAC‑4.7 (ش‑4)، وAC‑4.10 (ش‑5). اقترحت `stories.md` ألّا يحجب AC‑4.10 هذه الخطوة. لا أتبنّى ذلك، لأنّه معيار قبول 2.4 الثاني في الخطّة نفسها |
| 2.5 | 🟡 | ش‑5 | AC‑5.9 ✅ (P‑3، 2026‑10‑15)، وAC‑5.10 (ش‑5) |
| 2.6 | 🟡 | ش‑3 وش‑5 | AC‑6.4 (ش‑3)، وAC‑6.5 (ش‑5) |

## ملاحظات غير حاجبة مقبولة

كلّها مؤجّلة بقرار المالك (2026‑10‑15)، بلا جولة إصلاحٍ الآن. أقبلها بأسمائها:
- **ملاحظة المراجعة المهمّة #1 (CR‑مهمّة): جملٌ شرطيّة صار شرطها واقعاً.** في `docs/stack-commands.md:522` و`:580`، ورأس `docker-compose.gpu.yml:25-26`: «ما لم تُثبَّت الأداة».
  - **أقبلها** لأنّها لا تغيّر شيئاً يعمل: `.env` المالك على ملفّ WSL، وصورة `embedding-cuda` لا تُنشر قبل ش‑4.
  - **المتابعة:** تُصحَّح على الأكثر مع ش‑3، لأنّ ش‑3 هو ما يسبق قرار المالك بالانتقال.
- **CR‑1: `pull_models.py:136-144` و`:279-280`.** أيّ ردّ HTTP من `/api/show` غير 200 و404 يُعامَل معاملة «غير قابل للوصول» (الرمز 3)، فلا يُسحب نموذجٌ سليمٌ بعده.
  - **أقبله** لأنّ الجدول اليوم فيه نموذجٌ واحد (`gemma3:1b`، AC‑6.3)، والمكدّس يقلع على أيّ حال (`required: false`).
  - **المتابعة:** يُصلَح قبل إضافة نموذج Ollama ثانٍ إلى `PROVIDER_ROUTING`، ويُعدَّل معه `test_ollama_pull_models.py:467`.
- **CR‑2، وCR‑3، وCR‑4:** نصوص وثائق ومثالٌ في رأس `build-push.sh`، واختبارات QA خارج قائمة §2. لا أثر وظيفيّ لها.
- **S‑1:** أغلقه المالك، فحساب `aizzak` على Docker Hub حسابه.
- **S‑2 (ث‑1)، وS‑3 (ث‑2)، وS‑4 (ث‑3)، وS‑5 مع S‑6 ب (ث‑4)، وS‑6 أ (ث‑5):** مسجَّلةٌ في قائمة الأمان المؤجّلة `docs/deploy-server.md`.
  - **أقبلها** بقرار المالك (2026‑10‑06): مرحلة staging، الثغرات تُسجَّل ولا توقف شيئاً.
  - **تنبيه:** ترقية torch في ث‑2 تستلزم إعادة P‑4.
- **S‑7 وS‑8 (معلومات):** طباعة `base_url` في رسالة «unreachable»، واسم نموذجٍ قد يسمّي أيّ سجلّ. العنوان ثابتٌ في Compose، والجدول إعدادُ مشغِّل.
- **O‑5:** تفاوت مقاييس حجم صورة CUDA. `.Size` دون حدّ 10 ج.ب، و`docker images` فوقه. **الشرط:** يُعرض على المالك في ش‑4 قبل الوسم.
- **مجموعة التطابق 174 مقطعاً لا 221:** `docs/hr-no-table.docx` حُذف من الشجرة، فتقرؤه الأداة من `e105e47` وتسجّل بصمته. شرط Q‑3 (ب) يبقى قابلاً للقياس.
- **`design.md` ما زال يذكر `0.13.5`:** سجلٌّ تاريخيّ، وقرار المالك في `status.md` يعدّله.
- **الماسحات غير مثبّتة على المضيف** (gitleaks، وpip‑audit، وtrivy وغيرها): اكتُفي بمسح الأنماط واختبار AC‑7.6. إضافتها إلى CI تحسينٌ لاحق.
- **أحجامٌ وصورٌ باقية من مشاريع الإثبات:** `aizzak-llm-proof_*` و`aizzak-gpu-proof_*`، ومعها `aizzak/embedding-cuda:dev` و`aizzak/embedding:gpu-compose-proof`. يستعملها ش‑3 (استئناف السحب)، وحذفها يحتاج موافقة المالك.

## الحكم النهائي
**مقبول للدمج، بشروط.** وليس «مقبولاً» نهائيّاً بعد: 13 معياراً من Must ⏳ (كانت 18، ثمّ أُثبتت AC‑2.7 وAC‑1.8 وAC‑4.8 وAC‑5.9 وAC‑4.9 في 2026‑10‑15)، والحكم الكامل يصدر حين تتحقّق ش‑1…ش‑5.

**ما يبرّر القبول:**
- كلّ معيارٍ يمكن إثباته قبل الدمج دون حاوية كرت ✅: 46 من 64 (ثمّ 47 بـP‑1؛ ومع حاويات الكرت 50 بـP‑3 و51 بـP‑4)، ومنها معايير US‑8 كلّها.
- البوّابات الستّ خضراء بلا تخطٍّ. وتحقّقتُ بنفسي: ملفّات الميزة 432 passed عند HEAD، و153 عند `8dcb24f`.
- `review.md` و`security-review.md` كلاهما **PASS**، بلا ملاحظةٍ حاجبة ولا عالية. والملاحظات المفتوحة مقبولةٌ بأسمائها أعلاه.
- `test-plan.md` §5: PASS مع تأجيلٍ مصرَّح، و0 معيارٍ بلا اختبار أو إجراء.

**الشروط:**
- **ش‑1** لازمٌ **قبل** الدمج.
- **ش‑2** يُنفَّذ عند الدمج، قبل أوّل `up`.
- **ش‑3 وش‑4 وش‑5** بعده، وكلٌّ منها يحرّك خطواته في اللوحة إلى ✅.
- إن فشل شرط، فالمرحلة التي يعود إليها مذكورة في صفّه.

</div>
