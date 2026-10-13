<div dir="rtl">

# خطة الاختبار: صورٌ جاهزة بدل البناء المحلّيّ (`prebuilt-images`)

الفرع `feat/prebuilt-images` · 4 التزامات للمطوّر (`874bea9`…`a478d56`) + التزام QA واحد `43c34a9` (`test:`، اختباراتٌ فقط). الصياغات المعتمدة للمعايير هي §5.1 من `design.md`.
لم يُلمس أيّ ملفّ منتَج. لم يُشغَّل بناء Docker ولا المكدّس الحيّ ولا `docker login`/push. لم تُقرأ ملفّات الأسرار.

## 1. مصفوفة التغطية

اختصارات الملفّات: **CO** = `tests/unit/test_compose_build_overlay.py` · **RB** = `tests/unit/test_release_build_push.py` · **RE** = `tests/unit/test_release_build_push_edges.py` (جديد) · **RW** = `tests/unit/test_release_workflow.py` · **CT** = `tests/unit/test_ci_workflow_truthfulness.py` · **PD** = `tests/unit/test_prebuilt_images_docs.py` (جديد).

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC-1.1 | وحدة | `CO::test_base_file_has_no_build_key_in_any_service_or_extension` | ✅ |
| AC-1.2 (§5.1: `APP_IMAGE_REPO`/`EMBEDDING_IMAGE_REPO`، 10 خدمات) | وحدة | `CO::test_every_platform_image_service_has_a_build_entry` | ✅ |
| AC-1.3 | وحدة | `CO::test_the_overlay_only_carries_build` | ✅ |
| AC-1.4 (§5.1) | وحدة | `CO::test_platform_image_defaults_resolve_to_todays_local_names` | ✅ |
| AC-1.5 (مصحَّح: `services` فقط) | وكيلٌ آليّ + يدويّ للمالك | الوكيل: `CO::test_overlay_renders_todays_build_and_image_names` ✅. ومقارنة البصمة قبل/بعد على `master`: **مؤجَّلة للمالك**. الخطوة: إجراء AC-1.5 في `design.md` §5 (`git switch master; svc; svc --profile backup` ثمّ الفرع مع سطر `COMPOSE_FILE`)، فتتطابق البصمتان. تحتاج `.env` الحقيقيّ الذي لا يُقرأ | ✅ وكيل / ⏳ مؤجَّل |
| AC-1.6 | وحدة (docker) | `CO::test_base_file_alone_renders_and_builds_nothing`. وأعدتُ الفحص يدويّاً في مجلّد مؤقّت: 35 خدمة، 0 فيها `build` | ✅ |
| AC-1.7 | وحدة | `CT::test_integration_job_selects_real_markers_and_requires_live_dependencies` (يطابق `COMPOSE_FILE` ثلاثيّ الملفّات) | ✅ |
| AC-1.8 | CI | **مؤجَّل: يحتاج دفع الفرع وتشغيل CI**. الخطوة: بعد دفع المالك، `gh run watch`: `quality` و`integration` خضراوان، وفي سجلّ `Provision` تظهر `Building` لـ`migrate` ولا `Pulling aizzak/app`. وكيلٌ محلّيّ: `docker compose config -q` للتركيبة (قاعدة+test+build) ينجح (§3) | ⏳ مؤجَّل |
| AC-1.9 | Docker، المالك | **مؤجَّل للمالك** (يحتاج `.env`). الخطوة: بعد إضافة السطر، `docker compose config --images \| sort -u` يحوي `aizzak/app:dev` و`aizzak/embedding:dev`، ثمّ `docker compose build app embedding`. وكيل آليّ: نفس اختبار AC-1.5 | ⏳ مؤجَّل (وكيل ✅) |
| AC-1.10 | البوّابات | §2 أدناه: كلّها خضراء، و`git diff master -- .importlinter` فارغ | ✅ |
| AC-2.1 | وحدة | `RB::test_valid_tags_are_accepted[2026.10.1, 2026.1.12]` | ✅ |
| AC-2.2 (§5.1 + `app-2026.10.1`) | وحدة | `RB::test_invalid_tags_are_refused_before_any_docker_call` (9 قيم)، و`RE::test_malformed_versions_the_stories_did_not_list_are_refused` (`2026.13.1`، `2026.10.0`، `2026.10.01`، `1999.10.1`، سطر جديد لاحق، `;`) | ✅ |
| AC-2.3 (§5.1) | وحدة | `RB::test_image_list_has_two_images_and_reserves_cuda_for_2_4` | ✅ |
| AC-2.4 | يدويّ، السجلّ | **مؤجَّل لأوّل إصدار (المرحلة 13)**. الخطوة: `docker pull anastawahia/aizzak:app-<T>` و`…:embedding-<T>`، و`docker buildx imagetools inspect` يطابق البصمة المطبوعة | ⏳ مؤجَّل |
| AC-2.5 | يدويّ، السجلّ | **مؤجَّل لأوّل إصدار**. الخطوة: `DOCKER_CONFIG=$(mktemp -d) docker pull anastawahia/aizzak:app-<T>` يفشل بـ`unauthorized`/`denied`. ويتحقّق منه أيضاً افتراض السكربت أنّ الصفحة العامّة تعطي 404 للمستودع الخاصّ (`hub_status`): تأكيده على الواقع في الإصدار الأوّل | ⏳ مؤجَّل |
| AC-2.6 | يدويّ | **مؤجَّل لأوّل إصدار**: سجلّ الوظيفة/الـartifact `release-digests` فيه الوسم والبصمة وSHA. الصيغة نفسها مثبتة آليّاً بـAC-3.5 | ⏳ مؤجَّل |
| AC-3.1 | وحدة | `RB::test_dirty_tree_is_refused[modified_tracked, untracked_not_ignored]` + `RB::test_ignored_untracked_file_does_not_block`، و`RE::test_workflow_flag_inside_actions_still_requires_the_other_checks` | ✅ |
| AC-3.2 | وحدة | `RB::test_ci_not_green_is_refused` (failure / in_progress / none / rc=1) | ✅ |
| AC-3.3 | وحدة | `RB::test_missing_tag_prints_usage`، `RB::test_bad_tag_prints_usage_before_any_other_check`، `RE::test_bad_options_are_usage_errors_with_no_calls`، `RE::test_help_exits_zero_without_touching_anything` | ✅ |
| AC-3.4 (§5.1) | وحدة | `RB::test_build_calls_are_amd64_with_the_right_dockerfile_context_and_tag` | ✅ |
| AC-3.5 (§5.1) | وحدة | `RB::test_success_prints_one_digest_line_per_image_and_the_commit` | ✅ |
| AC-3.6 (§5.1) | وحدة | `RB::test_existing_tag_is_refused[app, embedding]`، `RB::test_an_older_version_does_not_block_the_next`، `RE::test_existing_tag_for_one_prefix_builds_and_pushes_nothing` | ✅ |
| AC-3.7 (§5.1) | وحدة | `RB::test_second_push_failure_names_it_and_lists_what_was_pushed`، `RE::test_first_push_failure_reports_nothing_pushed_and_never_ok`، `RE::test_digest_file_keeps_only_the_image_pushed_before_a_failure` | ✅ |
| AC-3.8 | وحدة | `RB::test_script_text_hygiene`، `PD::test_no_shell_tracing_in_release_script_or_workflow`، `PD::test_release_script_is_valid_bash` | ✅ |
| AC-3.9 (§5.1) | يدويّ، السجلّ | **مؤجَّل لأوّل إصدار**. الطريق المعتاد CI (AC-4.5). الخطوة (إن نُفّذ محلّيّاً): `build-push.sh <T>` ثمّ `docker image inspect --format '{{.Os}}/{{.Architecture}}'` = `linux/amd64` لكلّ صورة. السلوك مثبت ببدائل الوحدة (AC-3.4) | ⏳ مؤجَّل |
| AC-4.1 | وحدة | `RW::test_it_fires_only_on_pushed_v_tags` | ✅ |
| AC-4.2 | وحدة | `RW::test_registry_credentials_come_only_from_two_secrets_via_env_and_stdin`، `PD::test_docs_and_workflows_carry_no_registry_credential` | ✅ |
| AC-4.3 | وحدة | `RW::test_publishing_is_delegated_to_the_script`، `RW::test_the_tag_is_read_from_the_environment_not_interpolated` | ✅ |
| AC-4.4 | وحدة | `RW::test_publish_needs_quality_and_every_job_is_time_boxed`، مع الحرّاس `RW::test_release_quality_is_ci_quality` و`RW::test_every_run_step_is_valid_bash` و`RW::test_permissions_are_read_only_and_releases_never_cancel` | ✅ |
| AC-4.5 | يدويّ، الأسرار | **مؤجَّل لأوّل وسم `v<T>` (المرحلة 13) وبعد أن يضبط المالك البيئة `release` والسرّين**. الخطوة: دفع الوسم على commit من `master`؛ المسار أخضر؛ الصورتان `app-<T>` و`embedding-<T>` في السجلّ؛ سطرا البصمة والـSHA في السجلّ والـartifact؛ لا مفتاح في السجلّ | ⏳ مؤجَّل |
| AC-4.6 | يدويّ | **مؤجَّل**: بسرٍّ ناقص يفشل عند الخطوة «Registry secrets are set» (النصّ موجود في `release.yml` ومفحوص بـ`bash -n`) ولا يصل إلى البناء | ⏳ مؤجَّل |
| AC-4.7 | يدويّ | **مؤجَّل**: وسم `v2026.10.1-test` يفشل عند `preflight` (الرفض نفسه مثبت آليّاً بـ`RB::test_preflight_refuses_a_bad_tag`). ثمّ يُحذف الوسم التجريبيّ | ⏳ مؤجَّل |
| AC-5.1 | وحدة | `CO::test_env_example_offers_the_overlay_line_commented_only` | ✅ |
| AC-5.2 | فحص نصّ (آليّ الآن) | `PD::test_quickstart_adds_compose_file_between_env_copy_and_first_up`، `PD::test_readme_quick_start_mentions_the_overlay_before_up` | ✅ |
| AC-5.3 | فحص نصّ (آليّ الآن) | `PD::test_stack_commands_documents_overlay_three_file_form_and_release_rows`، `PD::test_no_documented_explicit_f_up_or_build_omits_the_overlay` | ✅ |
| AC-5.4 | فحص نصّ (آليّ الآن) | `PD::test_modified_docs_stay_inside_an_rtl_div`، `PD::test_docs_and_workflows_carry_no_registry_credential` (العربيّة داخل `rtl` والأوامر بالإنجليزيّة: قراءة يدويّة للفروق، سليمة) | ✅ |

**العدّاد (36 معياراً):** آليّ بالكامل 26 · آليّ كوكيلٍ + مؤجَّل للمالك 2 (AC-1.5، AC-1.9) · مؤجَّل مع تبرير وخطوة محدّدة 8 (AC-1.8، AC-2.4، AC-2.5، AC-2.6، AC-3.9، AC-4.5، AC-4.6، AC-4.7). لا معيار بلا تغطية. كلّ المؤجَّلات تحتاج السجلّ أو الأسرار أو `.env` أو دفعاً إلى GitHub: لا يمكن لوكيلٍ تنفيذها هنا. معايير Must كلّها (US-1…US-3) مغطّاة آليّاً عدا ما يلزمه السجلّ/`.env` (AC-1.5/1.8/1.9/2.4–2.6/3.9) وهي مؤجَّلة بتبريرها.

## 2. نتائج بوابات المشروع

من `/home/AIZZAK`، بعد التزام الاختبارات `43c34a9` (البوّابة 5 مقتصرة على `tests/unit tests/architecture tests/eval` حسب `CLAUDE.md`، لأنّ التكامل يمسّ المكدّس الحيّ). البوّابة 6 (`test-rules.sh`) خارج التكليف.

```
$ .venv/bin/ruff format --check .
805 files already formatted
$ .venv/bin/ruff check .
All checks passed!
$ .venv/bin/mypy src
Success: no issues found in 467 source files
$ .venv/bin/lint-imports
Infrastructure imported only by Composition Root KEPT
Framework does not import outer layers KEPT
Contracts: 8 kept, 0 broken.
$ PATH=$PWD/.venv/bin:$PATH .venv/bin/pytest -rs tests/unit tests/architecture tests/eval
5204 passed, 7 warnings in 95.87s (0:01:34)
```

ملاحظات صدق:
- تشغيل `.venv/bin/pytest` **بلا** `.venv/bin` على `PATH` يعطي `5179 passed, 1 skipped`؛ السكيب الوحيد `tests/architecture/test_import_contracts.py:21: import-linter (lint-imports) is not installed` (يبحث بـ`shutil.which`). سلوكٌ سابق للميزة، والبوّابة 4 تُشغَّل مباشرةً خضراء. مع `PATH` الصحيح لا سكيب.
- الاختبارات الجديدة: 24 اختباراً (حالات المعاملات مشمولة) في ملفّين PD وRE. كلّها خضراء ولا تحتاج شبكة. اختبارات `docker compose config` لم تُتخطَّ لأنّ Docker موجود على هذا المضيف.
- `shellcheck` **غير مثبّت** على المضيف، ولم تُستعمل بدائل. اكتُفي بـ`bash -n` على السكربت وعلى كلّ `run:` (الاختباران `test_release_script_is_valid_bash` و`test_every_run_step_is_valid_bash`).

## 3. الاختبار الاستكشافي

كلّها بلا Docker حقيقيّ ولا شبكة: بدائل `docker`/`gh`/`curl` أوّل `PATH` (حزام `RB::_run`) على مستودع مؤقّت بـ`origin` محلّيّ عارٍ، وكلّ ما في نطاق scratchpad. وCompose على نسخة مؤقّتة من الملفّات مع `--env-file .env.example` و`COMPOSE_FILE` غير مضبوط، لا على `/home/AIZZAK`.

| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| `--help` / خيار مجهول / وسمان / `--digest-file` بلا مسار | تشغيل بكلّ منها | 0 للأوّل، و2 مع الاستعمال للباقي، بلا نداءات | مطابق | ✅ |
| صيغ وسم سيّئة | `2026.13.1`، `2026.10.0`، `2026.10.01`، `1999.10.1`، `2026.10.1\n`، `2026.10.1;touch x`، `-2026.10.1`، `v…`، `app-…`، `latest` | رمز 2، صفر نداءات | مطابق | ✅ |
| `--check-tag` مع `--list-images` | معاً | رمز 2 | مطابق | ✅ |
| شجرة متّسخة (معدَّل / غير متتبَّع) | تعديل `Dockerfile` أو ملف جديد | رمز 3 بلا docker | مطابق (وملفّ ضمن `.gitignore` لا يمنع) | ✅ |
| ليس على master | commit محلّيّ فقط | رمز 4 | مطابق | ✅ |
| CI أحمر / معلَّق / لا تشغيل / `gh` يفشل | `completed/failure`، `in_progress/`، `none`، rc=1 | رمز 5، بلا بناء | مطابق | ✅ |
| وسم موجود لبادئة واحدة فقط (`embedding-` ثمّ `app-`) | `FAKE_DOCKER_EXISTING` | رمز 7، لا بناء ولا رفع | مطابق في الحالتين | ✅ |
| المستودع عامّ (200) / غير قابل للتحقّق (403) | `FAKE_CURL_CODES` | رمز 7 قبل البناء | مطابق | ✅ |
| فشل رفع الصورة الأولى / الثانية | `FAKE_DOCKER_FAIL_PUSH` | رمز 9، ذكر الفاشلة، وما رُفع قبلها ببصمته، وABANDONED + الرقم التالي، بلا `OK` | مطابق، و`--digest-file` يحمل ما رُفع فقط | ✅ |
| المستودع صار عامّاً بعد أوّل رفع | `FAKE_CURL_CODES=404,200` | رمز 9 | مطابق | ✅ |
| فشل بناء الثانية | `FAKE_DOCKER_FAIL_BUILD` | رمز 8 ولا رفع | مطابق (البناء كلّه قبل أوّل push) | ✅ |
| `--gates-checked-by-workflow` | خارج Actions / داخلها مع CI أحمر | 2 / يتخطّى فحص CI | مطابق | ✅ |
| `--digest-file` نسبيّ من مجلّد فرعيّ | `cwd=deploy` | الملفّ في المجلّد الحاليّ | مطابق | ✅ |
| سرّية السكربت | `git grep dckr_pat_`، `set -x`، `.env`، `--password` | لا شيء | لا شيء. الدخول في المسار فقط عبر `--password-stdin` وبمتغيّرات `env:` | ✅ |
| `release.yml` | قراءة + اختبارات RW | `on: push tags v*` فقط؛ `publish.needs=[preflight, quality]`؛ `permissions: contents: read`؛ لا `${{ }}` داخل `run:`؛ `timeout-minutes` ≤ 60؛ `concurrency` دون إلغاء؛ `preflight` يفشل في ثوانٍ لوسم أو commit خاطئ | مطابق. ملاحظة: خطوة `checkout` في `quality` بلا `persist-credentials: false` (تطابقٌ مقصود مع `ci.yml` يفرضه اختبار الانحراف؛ الصلاحية قراءة فقط ولا يصلها سرّ) | ✅ |
| `docker compose config -q` | قاعدة وحدها | 0، و0 خدمة فيها `build` (من 35) | مطابق | ✅ |
| قاعدة + build (مع `--profile backup`) | | 0، و10 خدمات فيها `build`، والصور `aizzak/app:dev` و`aizzak/embedding:dev` | مطابق | ✅ |
| قاعدة + wsl-gpu + build | | 0 | مطابق | ✅ |
| قاعدة + test + build (تركيبة CI `integration`) | | 0 | مطابق | ✅ |
| قيم الخادم | `APP_IMAGE_REPO=anastawahia/aizzak APP_IMAGE_TAG=app-2026.10.1` + التضمين | `config --images` يحمل الاسمين | `anastawahia/aizzak:app-2026.10.1` و`…:embedding-2026.10.1` | ✅ |
| وثائق: أوامر تنكسر | `grep` على README و`quickstart` و`stack-commands` و`deploy/` و`.github/` عن `-f docker-compose…` و`--build` بلا الملفّ الإضافيّ | لا أمر | لا أمر في الملفّات المعدَّلة. البقية في §4 (BUG-1) | ⚠️ |
| وثائق: RTL وU+2011 | رأس `<div dir="rtl">` وذيله `</div>` في الثلاثة. سطر «آخر مطابقة» يبقي U+2011 | سليم | سليم. المعرّفات الجديدة في الوثائق لا تحمل IDs من هذا النوع غير ذلك | ✅ |
| واجهة 375px / RTL واجهة / عربيّ في الإدخال | | لا ينطبق: لا واجهة | | ⏭ |

## 4. الأخطاء المكتشفة

لا أخطاء blocking ولا major.

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| BUG-1 | أمرا `docker compose -f docker-compose.yml -f docker-compose.test.yml up -d` في `docs/data/db-profile.md:188` وفي `docs/design/08-local-runbook.md:659-660` يسميان `-f` صراحةً بلا `-f docker-compose.build.yml`. `-f` يتجاهل `COMPOSE_FILE`، فعلى نسخةٍ جديدة بلا الصورتين محلّيّاً يفشل بـ`pull access denied for aizzak/app` (متوقّع: الأمر يعمل كما في `quickstart.md` و`stack-commands.md` المصحَّحين). `08-local-runbook.md` وثيقة تصميمٍ مُلزِمة تركها `stories.md` عمداً (حافّة US-5)، أمّا `db-profile.md` فلم تُذكر وقد تُنسى | minor (وثائق) | 1) `grep -n "docker-compose.test.yml up -d" docs/data/db-profile.md` 2) انسخ الأمر على نسخةٍ جديدة بلا صور محليّة فيفشل السحب | مفتوح، للمنفّذ `devops-engineer` (إضافة `-f docker-compose.build.yml` في `docs/data/db-profile.md:188`، وتنبيه أو تعديل في `08-local-runbook.md:659`) |
| NOTE-1 | `hub_status` يفترض أنّ الصفحة العامة لمستودعٍ خاصّ على Docker Hub تعطي 404 بلا اعتماد. إن أعطت غيره رفض السكربت الإصدار (7) بأمان، ولا يرفع شيئاً. لا يمكن التحقّق بلا شبكة | ملاحظة (ليس خطأ) | ينفَّذ مع AC-2.5 في أوّل إصدار | مؤجَّل |
| NOTE-2 | `tests/architecture/test_import_contracts.py` يتخطّى إن لم يكن `.venv/bin` على `PATH` (قديم، ليس من الميزة) | ملاحظة | `.venv/bin/pytest -rs …` بلا تعديل `PATH` | لا إجراء |

## 5. الحكم

**PASS** — البوّابات الخمس خضراء (5204 اختباراً ناجحاً بلا سكيب مع `PATH` صحيح)، ولكلّ معيار من الـ36 اختبارٌ آليّ أو خطوة تحقّق مؤجَّلة محدّدة بتبرير (السجلّ، `.env`، CI، الأسرار)، ولا خطأ blocking أو major؛ الخطأ الوحيد minor وثائقيّ (BUG-1). الشروط على المالك قبل الدمج النهائيّ/الإصدار: AC-1.5 وAC-1.9 على `.env`، وAC-1.8 بعد دفع الفرع، وAC-2.4…2.6 وAC-3.9 وAC-4.5…4.7 مع أوّل وسم.

</div>
