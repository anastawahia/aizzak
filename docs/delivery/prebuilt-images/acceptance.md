<div dir="rtl">

# قبول مالك المنتج: صورٌ جاهزة بدل البناء المحلّيّ (المرحلة 1 من خطّة خادم الكرت، 1.1–1.4)

- **الفرع:** `feat/prebuilt-images`، وفيه 7 commits فوق `master` (`874bea9` … `9a29215`). 18 ملفّاً، ولا تغيير في `src/` ولا في `.importlinter` (`git diff master --stat -- src .importlinter` فارغ).
- **المرجع:** صياغة المعايير في `stories.md`، وما عدّله منها `design.md` §5.1 (له الأسبقيّة). وD-7 و§3.3 بصيغة الجولة 2.
- **التاريخ:** 2026-10-13.

**ما فحصتُه بنفسي** (للقراءة فقط: لا مكدّس حيّ، ولا `.env`، ولا شبكة):
- **أسماء الاختبارات:** كلّ اسمٍ ذُكر دليلاً في الجدول موجودٌ في `tests/unit/`. اسمٌ واحد تغيّر في `489dc2d`: صار `test_no_documented_explicit_f_up_build_or_run_omits_the_overlay`.
- **اختبارات الرجوع بعد جولتَي الإعادة:**
  - في `489dc2d`: ‏`test_failure_after_a_successful_push_still_reports_the_tag_as_pushed`، و`test_visibility_after_push_fails_closed`، و`test_builder_without_amd64_is_refused`.
  - في `9a29215`: ‏`test_unwritable_digest_file_is_a_usage_error_before_any_call`، و`test_digest_file_failing_late_hits_the_backstop`.
- **ملفّات اختبار الميزة الستّة:** شغّلتُها بالأمر
  `PATH=.venv/bin:$PATH .venv/bin/pytest -rs tests/unit/test_compose_build_overlay.py tests/unit/test_release_build_push.py tests/unit/test_release_build_push_edges.py tests/unit/test_release_workflow.py tests/unit/test_prebuilt_images_docs.py tests/unit/test_ci_workflow_truthfulness.py`
  والنتيجة **`105 passed`** بلا أيّ تخطٍّ. Docker موجودٌ على المضيف، فاختبارات `docker compose config` جرت فعلاً ولم تُتخطَّ.
- **البوّابات بعد `9a29215`** (شغّلها المنسّق):
  - `ruff format`: ‏805 ملفّاً منسَّقة.
  - `ruff check`: نظيف.
  - `mypy src`: ‏467 ملفّاً بلا خطأ.
  - `lint-imports`: العقود 8 كلّها سليمة.
  - `pytest tests/unit tests/architecture tests/eval`: ‏**5223 passed** بلا تخطٍّ.
  - البوّابة 6 (`test-rules.sh`) لا تنطبق، لأنّ الفرع لا يمسّ `deploy/prometheus/`.
- **سطورٌ قرأتُها في الملفّات:**
  - `ci.yml:95`: قيمة `COMPOSE_FILE` بالملفّات الثلاثة.
  - `release.yml`: المهل 5 و30 و60 دقيقة، و`needs: [preflight, quality]`، و`environment: release`، وخطوة «Registry secrets are set».

**مفتاح الأحكام:**
- ✅: الدليل موجودٌ ويغطّي المعيار.
- ⏳: **مؤجَّلٌ بشرط**، أي لا يمكن إنتاج الدليل قبل الدمج، أو يحتاج `.env` المالك أو السجلّ. الدليل الجزئيّ مذكور في الجدول، والخطوة وصاحبها في «الشروط». **هذا ليس ✅:** لا تُعلَّم خطوة الخطّة ✅ قبل تنفيذ شرطها.
- لا يوجد 🔴.

## US-1 فصل البناء عن التشغيل (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC-1.1 | `test_compose_build_overlay.py::test_base_file_has_no_build_key_in_any_service_or_extension` | ✅ مقبول |
| AC-1.2 (§5.1: `APP_IMAGE_REPO`/`EMBEDDING_IMAGE_REPO`، 10 خدمات) | `test_compose_build_overlay.py::test_every_platform_image_service_has_a_build_entry`. ومنها `backup` | ✅ مقبول |
| AC-1.3 | `test_compose_build_overlay.py::test_the_overlay_only_carries_build`. أيّ `pull_policy` يُفشله، كما قرّر المالك | ✅ مقبول |
| AC-1.4 (§5.1) | `test_compose_build_overlay.py::test_platform_image_defaults_resolve_to_todays_local_names` | ✅ مقبول |
| AC-1.5 (مصحَّح §5.1: مقارنة `services` فقط) | **دليلٌ جزئيّ:**<br>- `test_overlay_renders_todays_build_and_image_names` (جرى هنا، لم يُتخطَّ).<br>- مراجعة الشيفرة قاست `services` من `master` ومن الفرع مع الملفّ الإضافيّ، بـ`.env.example`، بلا profile ومع `--profile backup`، فجاءت متطابقة حرفيّاً (`review.md`).<br>**الناقص:** المقارنة نفسها على `.env` المالك الحقيقيّ<br>**تحديث 2026‑10‑14 (ش-2):** على `.env` المالك الحقيقيّ:<br>- «قبل»: `docker-compose.yml` من `de707f0` (رأس `master` قبل الدمج)، بـ`--project-directory /home/AIZZAK`. البصمتان: `6c26df36…` بلا profile، و`a7d3a7db…` مع `--profile backup`.<br>- كان في `.env` سطر `COMPOSE_FILE=` فارغ، فصار `COMPOSE_FILE=docker-compose.yml:docker-compose.build.yml`. الصيغة الثنائيّة لأنّ الحزمة كانت تعمل من `docker-compose.yml` وحده، بلا جسر WSL (يجيب Q-8).<br>- «بعد»: `svc` مرّتين و`svc --profile backup` مرّتين، فجاءت البصمتان نفسهما في كلّ مرّة | ✅ مقبول (2026‑10‑14) |
| AC-1.6 | `test_compose_build_overlay.py::test_base_file_alone_renders_and_builds_nothing` (جرى هنا). وكرّره QA يدويّاً: 35 خدمة، وما فيها `build` = 0 | ✅ مقبول |
| AC-1.7 | `ci.yml:95` = `docker-compose.yml:docker-compose.test.yml:docker-compose.build.yml`. ويثبّتها `test_ci_workflow_truthfulness.py::test_integration_job_selects_real_markers_and_requires_live_dependencies` | ✅ مقبول |
| AC-1.8 | **دليلٌ جزئيّ:** `docker compose config -q` لتركيبة CI (الأساسيّ + test + build) نجح محلّيّاً (`test-plan.md` §3).<br>**الناقص:** تشغيل CI على الفرع لم يحدث بعد.<br>**تحديث 2026‑10‑14 (ش-1):** دُفعت commits الميزة ضمن [PR #3](https://github.com/anastawahia/aizzak/pull/3). في [run 38470311343](https://github.com/anastawahia/aizzak/actions/runs/38470311343):<br>- `quality` و`integration` خضراوان.<br>- خطوة `Provision` بنت `aizzak/app:dev` داخل CI (`naming to docker.io/aizzak/app:dev done`)، ولا `Pulling aizzak/app` فيها. Compose هناك لا يطبع كلمة `Building`، فمخرجات BuildKit هي دليل البناء.<br>- `release.yml` لم يعمل على الـPR.<br>جرى التحقّق بعد الدمج المحلّيّ لا قبله، لأنّ البوّابة ② كانت دمجاً بلا دفع | ✅ مقبول (2026‑10‑14) |
| AC-1.9 | **دليلٌ جزئيّ:** الاختبار نفسه الذي في AC-1.5 يُظهر الصورتين `aizzak/app:dev` و`aizzak/embedding:dev` مع ملفّ البناء.<br>**الناقص:** البناء الفعليّ على `.env` المالك<br>**تحديث 2026‑10‑14 (ش-2):** على `.env` المالك:<br>- `docker compose config --images` يحوي `aizzak/app:dev` و`aizzak/embedding:dev`.<br>- `docker compose build app embedding` نجح برمز 0 في 11 دقيقة ونصف تقريباً، بلا `-f`، أي من `COMPOSE_FILE` وحده | ✅ مقبول (2026‑10‑14) |
| AC-1.10 | البوّابات الخمس خضراء بعد `9a29215` (أعلاه). و`git diff master -- .importlinter` فارغ | ✅ مقبول |

## US-2 أسماء الصور ووسومٌ ثابتة (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC-2.1 | `test_release_build_push.py::test_valid_tags_are_accepted[2026.10.1, 2026.1.12]` | ✅ مقبول |
| AC-2.2 (§5.1، ومعها `app-2026.10.1`) | - `test_release_build_push.py::test_invalid_tags_are_refused_before_any_docker_call` (9 قيم).<br>- `test_release_build_push_edges.py::test_malformed_versions_the_stories_did_not_list_are_refused` | ✅ مقبول |
| AC-2.3 (§5.1) | `test_release_build_push.py::test_image_list_has_two_images_and_reserves_cuda_for_2_4` | ✅ مقبول |
| AC-2.4 | **تحديث 2026‑10‑14 (ش-4 د):** على جهاز المالك المسجَّل:<br>- `docker pull` للوسمين `app-2026.10.1` و`embedding-2026.10.1` نجح، و`RepoDigests` للأوّل = `sha256:b09587ca718e71e89681bfee572f6d2b2ce4ba553a2d5939c9269b67962e155c`.<br>- `docker buildx imagetools inspect` للصورتين يعطي البصمتين المطبوعتين في CI حرفيّاً (`app`: `b09587ca…`، و`embedding`: `dd817bb3…`) | ✅ مقبول (2026‑10‑14) |
| AC-2.5 | **تحديث 2026‑10‑14 (ش-4 هـ):** بـ`DOCKER_CONFIG` فارغ، `docker pull` للوسمين يفشل بـ`pull access denied … may require 'docker login'`. والصفحة العامّة `hub.docker.com/v2/namespaces/anastawahia/repositories/aizzak` تعطي `404`، فتأكّد NOTE-1 | ✅ مقبول (2026‑10‑14) |
| AC-2.6 | **دليلٌ جزئيّ:** شكل السجلّ (الوسم ببادئته، والبصمة، والـcommit) مثبتٌ آليّاً في AC-3.5.<br>**تحديث 2026‑10‑14 (ش-4 ج):** في [run 38500790209](https://github.com/anastawahia/aizzak/actions/runs/38500790209)، خطوة «Build and push»:<br>- سطرٌ لكلّ صورة: `…/aizzak:app-2026.10.1@sha256:b09587ca718e71e89681bfee572f6d2b2ce4ba553a2d5939c9269b67962e155c` و`…:embedding-2026.10.1@sha256:dd817bb384edc95aa84c4412e3c56874f0deecaf769082b123858c31a6836127`، ومعهما `release: commit 759c82dd…`.<br>- الـartifact `release-digests` فيه السطران نفسهما مع الـcommit.<br>- اسم المستخدم يظهر `***` لأنّه سرّ | ✅ مقبول (2026‑10‑14) |

## US-3 سكربت البناء والرفع (Must)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC-3.1 | `test_release_build_push.py`:<br>- `::test_dirty_tree_is_refused[modified_tracked, untracked_not_ignored]`.<br>- `::test_ignored_untracked_file_does_not_block` (ضابطٌ موجب) | ✅ مقبول |
| AC-3.2 | `test_release_build_push.py::test_ci_not_green_is_refused` بأربع حالات: failure، وin_progress، وnone، وrc=1. وبديل `gh` صار يتحقّق من نصّ `--jq` حرفيّاً (مراجعة #6) | ✅ مقبول |
| AC-3.3 | - `test_release_build_push.py::test_missing_tag_prints_usage`.<br>- `test_release_build_push.py::test_bad_tag_prints_usage_before_any_other_check`.<br>- `test_release_build_push_edges.py::test_bad_options_are_usage_errors_with_no_calls`.<br>- `test_release_build_push.py::test_unwritable_digest_file_is_a_usage_error_before_any_call`: الرمز 2، ولا نداء. هذا إصلاح N-1 | ✅ مقبول |
| AC-3.4 (§5.1) | `test_release_build_push.py::test_build_calls_are_amd64_with_the_right_dockerfile_context_and_tag`. يؤكّد أيضاً `--load` و`--pull` و`--provenance=false` و`--sbom=false` (مراجعة #6). ومعه `test_builder_without_amd64_is_refused` | ✅ مقبول |
| AC-3.5 (§5.1) | `test_release_build_push.py::test_success_prints_one_digest_line_per_image_and_the_commit` | ✅ مقبول |
| AC-3.6 (§5.1) | - `test_release_build_push.py::test_existing_tag_is_refused[app, embedding]`.<br>- `test_release_build_push.py::test_an_older_version_does_not_block_the_next`.<br>- `test_release_build_push_edges.py::test_existing_tag_for_one_prefix_builds_and_pushes_nothing` | ✅ مقبول |
| AC-3.7 (§5.1) | - `test_release_build_push.py::test_second_push_failure_names_it_and_lists_what_was_pushed`.<br>- `test_release_build_push_edges.py::test_first_push_failure_reports_nothing_pushed_and_never_ok`.<br>- `test_release_build_push.py::test_failure_after_a_successful_push_still_reports_the_tag_as_pushed`: حالتان × صورتان، وهو إصلاح مراجعة #1.<br>- `test_release_build_push.py::test_digest_file_failing_late_hits_the_backstop`.<br>- `test_release_build_push.py::test_visibility_after_push_fails_closed`: هذا S-1 | ✅ مقبول |
| AC-3.8 | - `test_release_build_push.py::test_script_text_hygiene`.<br>- `test_prebuilt_images_docs.py::test_no_shell_tracing_in_release_script_or_workflow`.<br>- `test_prebuilt_images_docs.py::test_release_script_is_valid_bash` | ✅ مقبول |
| AC-3.9 | **دليلٌ جزئيّ:** سلوك البناء مثبتٌ ببدائل في AC-3.4.<br>**تحديث 2026‑10‑14 (ش-4 ج):** السكربت نفسه رفع الصورتين من CI (AC-4.5)، وانتهى بـ`release: OK 2026.10.1 (2 images)`. وبعد السحب: `docker image inspect --format '{{.Os}}/{{.Architecture}}'` = `linux/amd64` للصورتين | ✅ مقبول (2026‑10‑14) |

## US-4 البناء والرفع من CI (Should)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC-4.1 | `test_release_workflow.py::test_it_fires_only_on_pushed_v_tags` | ✅ مقبول |
| AC-4.2 | - `test_release_workflow.py::test_registry_credentials_come_only_from_two_secrets_via_env_and_stdin`.<br>- `test_prebuilt_images_docs.py::test_docs_and_workflows_carry_no_registry_credential` | ✅ مقبول |
| AC-4.3 | - `test_release_workflow.py::test_publishing_is_delegated_to_the_script`.<br>- `test_release_workflow.py::test_the_tag_is_read_from_the_environment_not_interpolated` | ✅ مقبول |
| AC-4.4 | `test_release_workflow.py::test_publish_needs_quality_and_every_job_is_time_boxed`. وحارس الانحراف `::test_release_quality_is_ci_quality` صار يقارن `env` أيضاً | ✅ مقبول |
| AC-4.5 | **تحديث 2026‑10‑14 (ش-4 ج):** [run 38500790209](https://github.com/anastawahia/aizzak/actions/runs/38500790209) أخضر:<br>- المهامّ: `preflight` (6 ث)، و`quality` (3.5 د)، و`publish` (3 د).<br>- `app-2026.10.1` و`embedding-2026.10.1` في `anastawahia/aizzak`، والبصمتان في السجلّ وفي الـartifact.<br>- لا مفتاح في السجلّ: `grep -ci dckr_pat` على سجلّ التشغيل كلّه = 0.<br>- المحاولتان 2 و3 فشلتا عند `docker login` بمفتاحٍ خاطئ، قبل أيّ بناء. صحّحه المالك، فنجحت المحاولة 4 | ✅ مقبول (2026‑10‑14) |
| AC-4.6 | **دليلٌ جزئيّ:** خطوة «Registry secrets are set» موجودة (`release.yml:99`)، ويفحصها `bash -n`.<br>**تحديث 2026‑10‑14 (ش-4 ب):** [run 38500790209](https://github.com/anastawahia/aizzak/actions/runs/38500790209)، المحاولة 1، و`DOCKERHUB_USERNAME` وحده في البيئة. فشل `publish` بعد 9 ثوانٍ عند «Registry secrets are set» بـ`release secret(s) not set in environment 'release': DOCKERHUB_TOKEN`، ولم يُبنَ شيء | ✅ مقبول (2026‑10‑14) |
| AC-4.7 | **دليلٌ جزئيّ:** الرفض نفسه مثبتٌ آليّاً في `test_release_build_push.py::test_preflight_refuses_a_bad_tag`.<br>**تحديث 2026‑10‑14 (ش-4 أ):** دُفع `v2026.10.1-test`. في [run 38500738096](https://github.com/anastawahia/aizzak/actions/runs/38500738096) فشل `preflight` بعد 5 ثوانٍ بـ`'2026.10.1-test' is not a version: expected YYYY.M.N`، وتُخطّيت `quality` و`publish`. ثمّ حُذف الوسم من GitHub | ✅ مقبول (2026‑10‑14) |

## US-5 وثائق الأوامر (Should)

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC-5.1 | `test_compose_build_overlay.py::test_env_example_offers_the_overlay_line_commented_only` | ✅ مقبول |
| AC-5.2 | - `test_prebuilt_images_docs.py::test_quickstart_adds_compose_file_between_env_copy_and_first_up`.<br>- `test_prebuilt_images_docs.py::test_readme_quick_start_mentions_the_overlay_before_up` | ✅ مقبول |
| AC-5.3 | - `test_prebuilt_images_docs.py::test_stack_commands_documents_overlay_three_file_form_and_release_rows`.<br>- `test_prebuilt_images_docs.py::test_no_documented_explicit_f_up_build_or_run_omits_the_overlay`: يمسح الآن `README.md` وكلّ `docs/` عدا `docs/delivery/`.<br>- وفي `stack-commands.md` سطرٌ عن قاعدة «غير `404` بعد الرفع يهجر الإصدار» (N-3) | ✅ مقبول |
| AC-5.4 | - `test_prebuilt_images_docs.py::test_modified_docs_stay_inside_an_rtl_div`.<br>- `test_prebuilt_images_docs.py::test_docs_and_workflows_carry_no_registry_credential`.<br>- قراءة QA للفروق (`test-plan.md`) | ✅ مقبول |

**العدّ (36 معياراً):**
- 36 ✅ (2026‑10‑14). المعايير العشرة المؤجّلة تحقّقت كلّها: AC-1.8 بـش-1، وAC-1.5 وAC-1.9 بـش-2، والسبعة الباقية بـش-4.
- 0 ⏳، و0 🔴.

## الشروط والمتابعات (بالخطوة وصاحبها)

**لماذا يُقبل الدمج قبل أن تصير هذه المعايير ✅؟** المعايير ش-3 وش-4 لا يمكن التحقّق منها قبل الدمج أصلاً: الوسم `v*` يجب أن يقع على commit من `master`، وCI عليه أخضر (Q-5، و`require_on_master`). فرفض الميزة بسببها لا يعيدها إلى مرحلةٍ تستطيع إنتاج الدليل. لذلك:
- **أقبل الدمج** بشرط ش-1، وش-2 يُنفَّذ عند الدمج.
- **أحجب** علامة ✅ عن خطوات الخطّة إلى أن تتحقّق شروطها. هذا استثناءٌ صريحٌ من قاعدة «كلّ Must ✅»، وأعرضه على المالك في البوّابة ②.

| # | متى | الخطوة بالضبط | صاحبها | يغلق |
|---|---|---|---|---|
| ش-1 | **قبل الدمج** (البوّابة ②) | يُدفع الفرع ويُفتح الـPR، ثمّ `gh pr checks <PR> --watch`:<br>- `quality` و`integration` خضراوان.<br>- سجلّ خطوة `Provision` في `integration` يُظهر `Building` لـ`migrate`، ولا يُظهر `Pulling aizzak/app`.<br>- `release.yml` لا يعمل على الـPR، وهذا متوقَّع. | المنسّق، بعد إذن المالك بالدفع | AC-1.8 |
| ش-2 | **عند الدمج**، قبل أوّل `docker compose build` على جهاز المالك بعده | 1. على `master` قبل الدمج: `svc ; svc --profile backup` (إجراء AC-1.5 في `design.md` §5). يطبع بصمات sha256 فقط.<br>2. بعد الدمج: يضيف المالك سطر `COMPOSE_FILE` إلى `.env`. مع جسر WSL تكون الصيغة الثلاثيّة `docker-compose.yml:docker-compose.wsl-gpu.yml:docker-compose.build.yml`، وهذا يجيب Q-8.<br>3. يعيد `svc` مرّتين، فتتطابق البصمتان مع «قبل».<br>4. `docker compose config --images \| sort -u` يحوي `aizzak/app:dev` و`aizzak/embedding:dev`.<br>5. `docker compose build app embedding` ينجح. | المالك، لأنّ `.env` لا يقرؤه وكيل. أو وكيلٌ بموافقته، على أن يطبع البصمات فقط | AC-1.5، AC-1.9 |
| ش-3 | **قبل أوّل وسم `v*`** | شروط `security-review.md` «قبل أوّل وسم»:<br>(1) في Docker Hub: *Default repository privacy = Private*، ثمّ إنشاء `anastawahia/aizzak` **خاصّاً**. هذا S-1.<br>(2) مفتاح PAT بصلاحيّة Read & Write بلا Delete، وتفعيل *Immutable tags* للنمطين `app-*` و`embedding-*` إن أتاحتها الخطّة. هذا S-4.<br>(3) في GitHub: البيئة `release` بقاعدة وسوم `v*`، والسرّان أسرارَ **بيئة**، وtag ruleset على `refs/tags/v*` للـadmin. هذا S-2.<br>(4) اختياريّ: حجز المساحة `aizzak` على Docker Hub. هذا R-3/S-5. | المالك (إعدادات Docker Hub وGitHub) | شرطٌ مسبق لـش-4 |
| ش-4 | **أوّل إصدار** (المرحلة 13) | **(أ) AC-4.7:** يُدفع `v2026.10.1-test`، فيفشل في `preflight` خلال ثوانٍ، ولا يُرفع شيء. ثمّ يُحذف الوسم من GitHub.<br>**(ب) AC-4.6:** تُترك البيئة `release` بـ`DOCKERHUB_USERNAME` وحده، ويُدفع `v2026.10.1` على commit من `master` أخضر. يفشل عند «Registry secrets are set» ويذكر `DOCKERHUB_TOKEN`، ولا يبني. لا يُرفع شيء، فرقم الإصدار لا يُحرق.<br>**(ج) AC-4.5:** يُضاف `DOCKERHUB_TOKEN`، ثمّ يُعاد تشغيل المسار (re-run). النتيجة المطلوبة:<br>&nbsp;&nbsp;- المسار أخضر.<br>&nbsp;&nbsp;- `app-2026.10.1` و`embedding-2026.10.1` في `anastawahia/aizzak`.<br>&nbsp;&nbsp;- سطرا البصمة وSHA في السجلّ والـartifact `release-digests`.<br>&nbsp;&nbsp;- لا مفتاح في السجلّ.<br>&nbsp;&nbsp;يتحقّق معه AC-2.6، وكذلك AC-3.9 إذ CI هو الطريق المعتاد، و`docker image inspect --format '{{.Os}}/{{.Architecture}}'` بعد السحب = `linux/amd64`.<br>**(د) AC-2.4:** على جهازٍ مسجّل: `docker pull anastawahia/aizzak:app-2026.10.1` و`…:embedding-2026.10.1`، و`docker buildx imagetools inspect` يعطي البصمة المطبوعة.<br>**(هـ) AC-2.5:** `DOCKER_CONFIG=$(mktemp -d) docker pull …` يفشل بـ`unauthorized` أو `denied`. وهذا يؤكّد أيضاً افتراض NOTE-1: الصفحة العامّة لمستودعٍ خاصّ تعطي `404`. **إن لم تعطِه**، رفض السكربت الإصدار بالرمز 7 بأمان، لكنّ الإصدار يتعطّل، فتعود الميزة إلى مرحلة البناء (devops-engineer) لتعديل `hub_status`. | تُنفَّذ (أ)–(ج) بدفع وسوم، فتحتاج إذن المالك أو تنفيذه. ويُنفّذ devops-engineer (د) و(هـ)، ويسجّل النتائج في `release-notes.md` | AC-2.4، AC-2.5، AC-2.6، AC-3.9، AC-4.5، AC-4.6، AC-4.7 |

**متى تُعلَّم خطوات الخطّة ✅** (في `server-gpu-plan.md` §4، وفي `server-gpu-summary.html`):
- **1.1:** بعد ش-1 وش-2.
- **1.2:** بعد ش-4 (د) و(هـ)، ومعه AC-2.6.
- **1.3:** بعد ش-4 (ج)، أي AC-3.9 عبر CI.
- **1.4:** بعد ش-4 (أ) و(ب) و(ج).
- **قبل ذلك:** تُعلَّم 1.1–1.4 «مدموجة، بانتظار التحقّق»، لا ✅.

## ملاحظات غير حاجبة مقبولة
- **تعليقا الرأس في ملفَّين محميَّين:** `docker-compose.test.yml:5` و`docker-compose.wsl-gpu.yml:4` ما زالا يذكران `-f …` بلا `-f docker-compose.build.yml`. لم يُعدَّلا لأنّ موافقة البوّابة ① لم تشملهما.
  - **أقبلهما** لأنّهما تعليقان لا أمران منفَّذان. والأمر نفسه يعمل ما دامت الصور موجودةً محلّيّاً.
  - الوثائق المرجعيّة صُحّحت كلّها، ويحرسها `test_no_documented_explicit_f_up_build_or_run_omits_the_overlay`.
  - **المتابعة:** المالك يوافق على تعديل سطرَي تعليقٍ في الملفّين، في ميزةٍ لاحقة أو commit صغير.
- **S-3 (المتبقّي):** الـactions مثبّتةٌ بوسمٍ لا بـSHA (`checkout@v4`، و`setup-python@v5`، و`upload-artifact@v4`).
  - **أقبله** لأنّ `docker logout` نُقل إلى ما بعد الرفع مباشرةً، فلا action خارجيّ يعمل والمفتاح على القرص. والخطر الباقي منخفض: سلامة الـartifact والاختبارات فقط.
  - **المتابعة:** التثبيت بالـSHA مع Dependabot لـ`github-actions` في `ci.yml` و`release.yml` معاً، لأنّ حارس الانحراف يربطهما.
- **S-1 (المتبقّي):** فحص الخصوصيّة قبل أوّل رفع لا يفرّق بين «خاصّ» و«غير موجود».
  - **أقبله** لأنّ الفحص بعد الرفع صار يفشل مغلقاً، والصورتان بلا أسرار.
  - **الشرط:** ش-3 (1) قبل أوّل وسم.
- **S-2:** الثقة كلّها في «من يدفع وسم `v*`».
  - **أقبله** لأنّ المالك هو المتعاون الوحيد.
  - **الشرط:** ش-3 (3) قبل أوّل وسم.
- **S-4:** الخادم يسحب بوسمٍ قابلٍ للتغيير.
  - **أقبله** لهذه الميزة، فالخادم لا يُثبَّت قبل الخطوة 5.2.
  - **المتابعة:** في 5.2 يكتب `install.sh` الوسم مع البصمة (`app-<v>@sha256:…`)، ومعه ش-3 (2): Immutable tags.
- **R-3 / S-5:** المساحة `aizzak` على Docker Hub غير محجوزة.
  - خطرٌ قائمٌ قبل الميزة، قبله المالك بقراره ألّا يُضاف `pull_policy`. أقبله كما هو.
  - تخفيفٌ رخيص اختياريّ: ش-3 (4).
- **N-2 لم يُصلَح، ورسالة الـcommit تقول غير ذلك:**
  - تعليق النوع في `tests/unit/test_release_workflow.py:116` ما زال `list[tuple[Any, Any, Any]]`، والدالّة تُرجع أربعة عناصر.
  - رسالة `9a29215` تقول «fixes a type comment in the workflow test»، لكنّ الملفّ ليس في ذلك الـcommit.
  - لا أثر وظيفيّ له: الاختبار ينجح، و`mypy` يفحص `src` وحده. أقبله تحسيناً مفتوحاً.
  - على المنسّق أن يصحّح ما في `status.md` (البناء دورة 2) عن N-1…N-3: ‏N-1 وN-3 أُصلحا، وN-2 لم يُصلَح.
- **NOTE-1:** افتراض أنّ مستودعاً خاصّاً يعطي `404` لصفحته العامّة. يتأكّد في ش-4 (هـ)، وإن خاب فالسكربت يفشل بأمان.
- **أدوات غير مثبّتة على المضيف:** `shellcheck` و`actionlint` و`gitleaks`. اكتُفي بـ`bash -n` وبفحص الأنماط. أقبل ذلك؛ إضافتها إلى CI تحسينٌ لاحق.

## الحكم النهائي
**مقبول للدمج، بشروط.**

**ما يبرّر القبول:**
- كلّ معيارٍ يمكن التحقّق منه قبل الدمج ✅ بدليلٍ آليٍّ موجود. وفيها كلّ معايير US-1 وUS-3 وUS-5 القابلة للأتمتة، وكلّ معايير US-2 وUS-4 غير المرتبطة بالسجلّ.
- `review.md` و`security-review.md`: كلاهما **PASS** بعد جولة التحقّق.
- ملاحظات المراجعة (#1–#9، وN-1، وN-3) أُصلحت، ولها اختبارات رجوعٍ موجودة وناجحة. وبقي N-2 (تعليق نوع) مفتوحاً ومقبولاً.
- البوّابات خضراء بلا تخطٍّ.

**الشروط:**
- **ش-1** (AC-1.8، أي CI الـPR أخضر) شرطٌ لازم **قبل** الدمج. **تحقّق في 2026‑10‑14** عبر PR #3 (انظر AC-1.8).
- **ش-2** يُنفَّذ عند الدمج على جهاز المالك. **تحقّق في 2026‑10‑14** على `.env` المالك، نفّذه وكيلٌ وطبع البصمات وحدها (انظر AC-1.5 وAC-1.9).

**تحديث 2026‑10‑14:** تحقّق ش-3 وش-4، فصارت المعايير الـ36 كلّها ✅، وتُعلَّم الخطوات 1.1–1.4 ✅ في الخطّة. أوّل إصدار: `v2026.10.1` (`release-notes.md`).

**ما لا يشمله هذا القبول (نصّ 2026‑10‑13):** الخطوات 1.1–1.4 في الخطّة لا تصير ✅ إلّا بعد ش-2 وش-4 كما في الجدول أعلاه. وإن فشل أيٌّ من ش-1 أو ش-4، تعود الميزة إلى **مرحلة البناء** (devops-engineer)، ثمّ تُعاد المراجعة والقبول للجزء المتغيّر.

</div>
