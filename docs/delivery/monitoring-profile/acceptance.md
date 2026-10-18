<div dir="rtl">

# قبول مالك المنتج: المراقبة اختياريّة وأرقام الكرت (`monitoring-profile`، المرحلة 4 من خطّة خادم الكرت)

- **الفرع:** `feat/monitoring-profile` على `16498e7` (من `master` ‏`47225ff`). التاريخ 2026‑10‑18.
- **ما قرأتُه:**
  - `requirements.md`، ومعه أجوبة بوابة ① (Q‑1…Q‑11).
  - `stories.md`، و`design.md` (§3‑ي للأرقام، والإجراء ج).
  - `test-plan.md` (الجولتان 0 و1)، و`review.md` و`security-review.md` (الجولات 0…3)، و`status.md` «قرارات وتصعيدات».
- **ما شغّلتُه بنفسي:**
  - البوّابات الستّ.
  - `deploy/resource-budget.sh` بالحالات كلّها.
  - تعبير قاعدة `keep` على أسماء المقاييس.
  - فحص النطاق، وعدّ اللوحة والملخّص.
  - دمجٌ تجريبيّ للقراءة فقط مع `master` الحاليّ (`git merge-tree`، بلا تعديل مراجع).
- **ما لم أفعله:**
  - لم أشغّل `tests/integration`.
  - لم أقرأ `.env` ولا ملفّات الأسرار.
  - لم أعدّل غير هذا الملفّ.

## الخلاصة

| المجموعة | العدد | ✅ متحقّق | ⚠️ مقبولٌ باستثناء المالك | ⏸ مؤجَّل بطبيعة المعيار | 🔴 غير متحقّق |
|---|---|---|---|---|---|
| Must (US‑1…US‑3) | 21 | 18 | 0 | 3 (AC‑1.9، AC‑3.3، AC‑3.4) | 0 |
| Should (US‑4…US‑6) | 15 | 12 | 1 (AC‑4.6) | 2 (AC‑5.5، AC‑6.2) | 0 |
| **المجموع** | **36** | **30** | **1** | **5** | **0** |

**معنى «المؤجَّل» هنا:**
- المعايير الخمسة مكتوبةٌ في `stories.md` من نوع [مالك، بعد الدمج] أو [خادم، 7.3].
- ولا يمكن إثبات أيٍّ منها قبل الدمج أصلاً. مثلاً: AC‑3.3 يقارن `master` بـcommit الدمج نفسه.
- ووافق المالك على هذا التقسيم في بوابة ① (status.md الصفّ 2، وجواب Q‑9).

## البوّابات (شغّلتُها على `16498e7`، و`COMPOSE_FILE` و`COMPOSE_PROFILES` غير مضبوطين)

| # | الأمر | النتيجة |
|---|---|---|
| 1 | `.venv/bin/ruff format --check .` | `821 files already formatted`، ‏rc 0 |
| 2 | `.venv/bin/ruff check .` | `All checks passed!`، ‏rc 0 |
| 3 | `.venv/bin/mypy src` | `Success: no issues found in 467 source files`، ‏rc 0 |
| 4 | `.venv/bin/lint-imports` | `Contracts: 8 kept, 0 broken.`، ‏rc 0 |
| 5 | `pytest -rs tests/unit tests/architecture tests/eval` (و`.venv/bin` في PATH) | **5864 passed**، و0 skipped، و0 failed، و7 warnings، ‏rc 0 (كانت 5834 في الجولة 1، والزيادة اختبارات الجولتين 2 و3) |
| 6 | `deploy/prometheus/test-rules.sh` | `check config` بلا ملفّ الكرت ومعه، `29 rules found`، `test-rules: OK`، ‏rc 0 |

## جدول المعايير

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| **US‑1 (Must)** | | |
| AC‑1.1 | `tests/unit/test_monitoring_profile.py:176` `test_exactly_the_eleven_carry_the_monitoring_profile`، و`:189` `test_each_monitoring_service_is_unchanged_but_for_its_profile[×11]` (بصمة sha256 لكلّ خدمةٍ كما على `master` ‏`47225ff`، `:77-89`)، و`:198` `test_cadvisor_stays_behind_container_metrics_alone`. وفرق `docker-compose.yml` عن `master`: كتلة تعليق، و11 سطر `profiles: ["monitoring"]`، لا غير | ✅ مقبول |
| AC‑1.2 | `test_monitoring_profile.py:205` `test_no_service_outside_monitoring_depends_on_it`، و`:215` `test_the_monitoring_edges_are_the_known_three_inside_and_five_out` (الحوافّ في `:61-72`). والنموذج مدموجٌ بملفّ الكرت (`:117-122`، إصلاح review #2) | ✅ مقبول |
| AC‑1.3 | `test_monitoring_profile.py:228` `test_no_service_outside_monitoring_names_a_monitoring_host` (يفحص `environment` و`command` و`entrypoint` و`healthcheck`)، و`:248` `test_the_log_anchor_is_still_json_file`. و`git diff master...HEAD -- deploy/nginx src` فارغ، فـ`/metrics` لم يتغيّر (FR‑3) | ✅ مقبول |
| AC‑1.4 | `test_monitoring_profile.py:264` `test_without_the_profile_none_of_the_eleven_is_in_the_model` (22 = 17 + 5، و`config -q` ‏0)، و`:273` `test_the_profile_adds_exactly_the_eleven`. يمرّران `--env-file .env.example` ويحذفان `COMPOSE_*` (`:159`). وDocker موجود فلم يُتخطَّ أيٌّ منهما | ✅ مقبول |
| AC‑1.5 | `test_monitoring_profile.py:282` `test_the_profile_key_does_not_change_any_config_hash`: بصمات `config --hash` للإحدى عشرة متطابقةٌ مع نسخةٍ بلا `profiles` | ✅ مقبول |
| AC‑1.6 | `test_monitoring_host_postgres_acceptance.py:260-275` `test_compose_refuses_to_render_without_the_exporter_password[None, monitoring]` | ✅ مقبول |
| AC‑1.7 | `test_monitoring_host_postgres_acceptance.py:238,247` يرسم بـ`--profile monitoring` ويؤكّد `profiles == ["monitoring"]`. و`test_compose_local_llm.py:355-360` يؤكّد 22 = 17 + 5 و33 = 28 + 5. و`test_observability_stack_wiring.py:369` `test_the_optional_target_is_the_one_behind_a_compose_profile`: profile غير `monitoring`، فـ`cadvisor` وحده. و`pytest` كلّه أخضر (البوّابة 5) | ✅ مقبول |
| AC‑1.8 | `test-plan.md` §3.أ، حيٌّ بموافقة Q‑9: <br>• بعد `stop` الإحدى عشرة: 0 تعمل، و`/health` 200، و`/health/ready` 200، و`stack_smoke.py` ‏PASS برمز 0. <br>• `up -d --no-deps`: 11 healthy عند 05:51:25، ولم يُعَد إنشاء أيٍّ منها. <br>• `AizzakWatchdog` في `alert-sink` عند 05:55:13. <br>• المدّة نحو 5 دقائق (≤ 10). | ✅ مقبول |
| AC‑1.9 | [خادم] مؤجَّلٌ إلى 7.3 بنصّ المعيار، وبجواب المالك على Q‑9: «رفع ملفٍّ وفهرسته وسؤالٌ عنه» يُؤجَّل إلى 7.3. والسند الحاليّ AC‑1.2 وAC‑1.3 وAC‑1.4 وAC‑1.8: الصحّة و`stack_smoke` سليمان والمراقبة موقوفة. ومسجَّلٌ في صفّ 4.1 (`docs/server-gpu-plan.md:133`) | ⏸ مؤجَّل (7.3) |
| **US‑2 (Must)** | | |
| AC‑2.1 | `test_resource_budget.py:478` `test_the_budget_counts_monitoring_by_default_and_says_so[ref,large,medium,small]`. وأعدتُ التشغيل، والرمز 0 في الأربع: <br>• المرجعيّ 35.60/57.28. <br>• `--size large` على 32/64: 37.85/57.59. <br>• `medium` على 16/32: 19.60/28.47. <br>• `small` على 8/16: 11.75/14.28. <br>وفي الأربع السطر `monitoring: counted (on by default, decision 7)`. والأرقام أرقام ما بعد 4.2 كما في `stories.md:121` | ✅ مقبول |
| AC‑2.2 | `test_resource_budget.py:488` `test_without_monitoring_each_total_drops_by_the_elevens_limits[×4]`، و`:540` `test_without_monitoring_conflicts_are_usage_errors`. وأعدتُ التشغيل: <br>• المرجعيّ 32.00/52.62، أي ينقص 3.60/4.66. <br>• الأحجام 34.00/52.88 و17.00/26.00 و10.10/12.56، أي تنقص الإحدى عشرة ومعها المصدّر (AC‑4.5). <br>• السطر: «NOT counted (--without-monitoring). Without it there are no alerts at all». <br>• الجمع مع `--block` أو `--with-profiles` يخرج بالرمز 2. | ✅ مقبول |
| AC‑2.3 | `test_auto_size.py:820` `test_the_budget_block_follows_the_blocks_profiles`: السطران «counted» و«NOT counted»، والفرق 3.60 و4.66 | ✅ مقبول |
| AC‑2.4 | `test_auto_size.py:842` `test_monitoring_is_a_known_profile_and_counts_only_when_named`. و`_known_profiles()` يقرأ الـprofiles من Compose (`deploy/auto_size.py:205-210`)، فلم يتغيّر الملفّ. و`test_auto_size.py:877-883` يمرّر `wsl-ollama,monitoring` ويؤكّد 11.72/11.40/0.32. و`:905-916` يؤكّد 23.22 و1.83x | ✅ مقبول |
| AC‑2.5 | `test_resource_budget.py:532` `test_without_monitoring_the_standing_set_is_seventeen`، و`:366` `test_the_patterns_actually_find_something`، و`:292` `test_the_script_and_this_module_agree`. وسطرا الدفتر `docs/design/08-local-runbook.md:480-481` (35.60 و57.28) لم يتغيّرا | ✅ مقبول |
| AC‑2.6 | `test_monitoring_profile.py:314` `test_the_ledger_explains_monitoring_on_by_default`. والنصّ في `08-local-runbook.md:496`، و`:530`، و`:533` | ✅ مقبول |
| **US‑3 (Must)** | | |
| AC‑3.1 | `test_monitoring_profile.py:334` `test_both_guides_carry_the_monitoring_lines[stack-commands, quickstart]`: الأشياء الخمسة، ويرفض الأسطر القديمة. و`test_gpu_compose_hygiene.py:293` ما زال ينجح | ✅ مقبول |
| AC‑3.2 | `test_compose_local_llm.py:367` `test_env_example_offers_both_profiles_commented_only`، و`test_gpu_compose_hygiene.py:442` `test_env_example_activates_no_compose_selector` | ✅ مقبول |
| AC‑3.3 | [مالك، بعد الدمج] بالإجراء (ج) في `design.md`. وسنده قبل الدمج: <br>• AC‑1.5: البصمات لا تتغيّر بالـprofile. <br>• AC‑1.8: `up -d --no-deps` لم يُعِد إنشاء أيٍّ من الإحدى عشرة. <br>• `test-plan.md` §3.أ: `.env` المالك يفعّل `monitoring` أصلاً. | ⏸ مؤجَّل (المالك بعد الدمج) |
| AC‑3.4 | [مالك، بعد الدمج، شرطيّ] بالإجراء (ج)، الخطوة 6. وإن لم تكن في `.env` كتلة `auto-size` يُكتب «لا ينطبق» | ⏸ مؤجَّل (المالك بعد الدمج) |
| AC‑3.5 | `test_monitoring_profile.py:360` `test_no_monitoring_means_no_alerts_is_said_where_it_is_read` (`runbooks/alerts.md` §0، و`deploy-server.md` §2) | ✅ مقبول |
| AC‑3.6 | `test_monitoring_profile.py:376` `test_the_plan_names_the_third_exception`. وفي `docs/server-gpu-plan.md`: <br>• `:95` الاستثناء الثالث. <br>• `:133-134` الصفّان بشروطهما: AC‑1.8 ✅، وAC‑3.3 عند المالك، وAC‑1.9 في 7.3؛ وAC‑6.3 ✅، وAC‑4.6 باستثناء، وAC‑5.5 وفحص `keep` في 7.3. <br>• `:457` جواب قرار 7. <br>**الملخّص:** عددتُ المقالات (8 `done` و9 `partial` و12 `pending`)، وتطابق عدّ صفوف اللوحة (8/9/12 من 29) وشريط `server-gpu-summary.html` | ✅ مقبول |
| **US‑4 (Should)** | | |
| AC‑4.1 | `test_compose_gpu_overlay.py:173` `test_the_gpu_exporter_is_pinned_internal_unprivileged_and_bounded`. يطابق المواصفة كاملةً: <br>• profile `monitoring`، وصورةٌ بالبصمة. <br>• `expose` بلا `ports`، و`utility`، وحدّا 0.25 و64m. <br>• healthcheck و`unless-stopped`. <br>• `read_only` و`cap_drop` و`no-new-privileges`. <br>• لا `privileged` ولا `network_mode` ولا `volumes`. <br>ومعه `:231` `test_the_gpu_exporter_logs_like_every_other_service`، و`test_gpu_compose_hygiene.py:128` `test_every_compose_image_is_pinned` | ✅ مقبول |
| AC‑4.2 | في `test_compose_gpu_overlay.py`: <br>• `:95` `test_the_overlay_has_only_services_and_exactly_five`. <br>• `:165` `test_prometheus_gets_one_mount_and_the_gpu_network`. <br>• `:243` `test_the_exporter_exists_only_with_the_monitoring_profile[×4]`. <br>• `:284` `test_the_overlay_changes_only_the_services_it_names[×2]`. | ✅ مقبول |
| AC‑4.3 | `test-rules.sh` (البوّابة 6): `check config` بلا ملفّ الكرت ومعه. وحالات `alerts.test.yml`: <br>• جهازٌ بلا سلسلة كرت: `AizzakScrapeTargetDown` صامتٌ عند 45s. <br>• `up{job="nvidia-gpu"} 0`: يشتعل عند 45s. <br>ومعها `test_prometheus_alert_rules.py:746` `test_the_rule_script_checks_the_config_with_and_without_the_gpu_file` | ✅ مقبول |
| AC‑4.4 | في `test_observability_stack_wiring.py`: `:207` `test_every_scrape_target_names_a_service_that_exists`، و`:263` `test_every_gpu_scrape_target_names_a_service_the_gpu_file_defines`، و`:281`، و`:301`. ومعها `test_healthcheck_coverage.py`، وهو يقرأ ملفّ الكرت (`:37`) | ✅ مقبول |
| AC‑4.5 | `test_resource_budget.py:591` `test_every_size_fits_the_host_its_header_names[×3]`، و`:514` `test_the_monitoring_share_is_the_elevens_limits_plus_the_gpu_exporter[×3]`. وأعدتُ التشغيل، والرمز 0: <br>• `large`: ‏57.59 من 57.60. <br>• `medium`: ‏28.47 ≤ 28.80. <br>• `small`: ‏14.28 ≤ 14.40. <br>و`--without-monitoring` يُخرج المصدّر | ✅ مقبول |
| AC‑4.6 | `test-plan.md` §3.ب و§6.ب: المصدّر healthy طوال 10 دقائق، و`restarts=0`. <br>• **البند 1 ✅:** الهدف `up`. <br>• **البند 2 ✅:** السلاسل الأربع، و`gpu_info` بـ`uuid` و`name`. <br>• **البند 3 🔴:** `OOMKilled=false`، لكنّ `memory.peak` = ‏58,929,152 بايت (56.2 MiB)، و× 1.5 = 84.3 MiB > 64 MiB (BUG‑1). <br>قَبِل المالك الخطر (status.md، 2026‑10‑18)، ويُعاد القياس في 7.3 | ⚠️ مقبولٌ باستثناء المالك (Should، لا يحجب) |
| AC‑4.7 | `test_monitoring_profile.py:388` `test_the_security_list_records_the_gpu_exporter_image`. وفي `docs/deploy-server.md:101` صفّ ث‑6، ومعه ث‑7…ث‑9 (`:102-104`) | ✅ مقبول |
| **US‑5 (Should)** | | |
| AC‑5.1 | المجموعة `aizzak-gpu` في `deploy/prometheus/alerts.yml:1185-1233`: <br>• > 0.90 لـ5m، و> 85 لـ5m، و`warning`. <br>• الحقول الخمسة، و`total > 0`، وتسميةٌ لكلّ كرت. <br>ومعها `test_prometheus_alert_rules.py:761` `test_the_gpu_rules_read_one_card_at_a_time`. و`EXPECTED_ALERTS` 29 عنصراً، وسببه الخطوة 4.2 (`:165`، `:235`) | ✅ مقبول |
| AC‑5.2 | حالات `alerts.test.yml`: <br>• **الذاكرة:** 95٪ لكرتين تشتعل لكلّ `uuid`، و85٪ صامتة، و95٪ القصيرة صامتة، و`total` = 0 صامت. <br>• **الحرارة:** 88 تشتعل، و80 صامتة، وقفزة 90 ثمّ 70 صامتة. <br>• **بلا سلسلة كرت:** كلاهما صامت. <br>و`test-rules: OK` (البوّابة 6)، و`test_prometheus_alert_rules.py:601` | ✅ مقبول |
| AC‑5.3 | `test_prometheus_alert_rules.py:584` `test_every_rule_opens_its_own_runbook_section`، و`:801` `test_the_gpu_runbook_sections_start_with_reading`. وفي `docs/runbooks/alerts.md`: <br>• `:75-76` الفهرس. <br>• `:541` و`:573` المرساتان. <br>• في كلّ قسم: «ماذا يعني»، وخطواتٌ تبدأ بـ`nvidia-smi` ثمّ `ollama ps` ثمّ 08 §2‑ي، و«كيف تعرف أنّه زال». | ✅ مقبول |
| AC‑5.4 | `test_prometheus_alert_rules.py:816` `test_the_trial_log_lists_the_gpu_rules`. و`docs/runbooks/alerts.md:870-871`: «بانتظار التجربة في 7.3» | ✅ مقبول |
| AC‑5.5 | [خادم، 7.3، اختياريّ] مؤجَّلٌ بنصّ المعيار | ⏸ مؤجَّل (7.3) |
| **US‑6 (Should)** | | |
| AC‑6.1 | في `test_observability_stack_wiring.py`: `:648` `test_the_gpu_row_shows_memory_load_and_temperature`، و`:673` `test_every_gpu_metric_a_panel_or_rule_reads_is_one_the_exporter_emits`، و`:503` (uid)، و`:559` (description) | ✅ مقبول |
| AC‑6.2 | [مالك، بعد الدمج] بالإجراء (ج)، الخطوة 7: لوحة الكرت «No data» على جهاز المالك | ⏸ مؤجَّل (المالك بعد الدمج) |
| AC‑6.3 | `test-plan.md` §3.ب، حيٌّ بموافقة Q‑10: كلّ تعبيرٍ من الثلاثة عبر `/api/ds/query` أعاد إطاراً غير فارغ (0.1179، و0.06، و44). <br>**قاعدة `keep` (`c576193`) جاءت بعد هذا الفحص.** <br>• تعبيرها يُبقي الأسماء الخمسة: تحقّقتُ بـ`fullmatch` على `GPU_EXPORTER_METRICS` في `tests/unit/support_compose.py`. <br>• وطابقه المراجع على 144 اسماً من ناتج 1.15.1 الحقيقيّ، فلم يسقط اسم (`review.md:417`). | ✅ مقبول |

## ملاحظات غير حاجبة مقبولة

**أ) قبول المالك وقراراته.** أقبلها بأسمائها:
1. **BUG‑1 / AC‑4.6.** ذروة المصدّر 56.2 MiB، و× 1.5 > `64m`.
   - قرار المالك (2026‑10‑18): يبقى الحدّ `64m`، ويُعاد القياس في 7.3.
   - إن فشل القياس هناك، فالقرار حسب Q‑8: يُخفض حدٌّ آخر في `deploy/sizes/large.env`.
   - هامش `large` اليوم 0.01 ج.ب فقط (57.59 من 57.60).
2. **مضيفٌ بلا `nvidia-ctk`** يخرج بالرمز 12 حتّى مع `--smoke` (قرار #13).
3. **Docker Engine < 26.0.0** يخرج بالرمز 11 (SR‑10، CVE‑2024‑29018).
4. **`--web.enable-lifecycle` باقٍ.** وخطره (حلقة `/-/quit`) وعلاجه في الموجة 8 مسجَّلان في ث‑7.
5. **#24:** لا إعادة كتابة للتاريخ.
6. **SR‑13 (3):** فحص `nvidia-container-cli` الاختياريّ لم يُنفَّذ.

**ب) على المالك بعد الدمج، أو في 7.3:**
1. **`cosign verify` على المصدّر 1.15.1** (ث‑6، `deploy-server.md:101`). لم يُشغَّل بعد، ويُسجَّل ناتجه في الصفّ.
2. **الإجراء (ج):**
   - AC‑3.3: قائمة الخدمات وبصمات الإحدى عشرة متساوية، ومعرّفات الحاويات لم تتغيّر.
   - AC‑3.4: الشرطيّ.
   - AC‑6.2: «No data».
   - ثمّ `up -d --force-recreate --no-deps prometheus`.
3. **في 7.3:**
   - فحص `scrape_samples_post_metric_relabeling == scrape_samples_scraped` للمهمّة `nvidia-gpu`.
   - إعادة قياس ذاكرة المصدّر.
   - AC‑1.9 وAC‑5.5.
4. **أحجام scratch:** تسعة أحجام `aizzak-gpumon-proof_*` وصورة المصدّر باقية على الجهاز. حذف الأحجام يحتاج إنساناً.

**ج) متابعاتٌ لوصف الـPR (بلغ حدّ الإعادة، 3 جولات):**

| البند | ما هو | هل يحجب؟ |
|---|---|---|
| #24 و#27 | موضع النسبة في رسائل الالتزامات: حارس الأدلّة في commit حدود الكشط، وجسم `c576193` ينسب إليه تعديل ث‑7 الذي في `16498e7`، والمعرّف العربيّ في رسالةٍ إنجليزيّة | لا. شكليٌّ، ولا إعادة كتابة للتاريخ بقرار المالك |
| #25 / SR‑19 | حارس قاعدة `keep` يفحص اسمين يمرّان واسمين يسقطان. فلا يرى تضييقاً يُسقط `nvidia_smi_temperature_gpu`، ولا توسيعاً يُدخل `aizzak_http_.*` | لا. التفصيل في (د) |
| #26 / SR‑18 | التنقية في سطرَي الإصدار وحدهما، وخمسة صفوفٍ في `gpu-preflight.sh` تطبع ناتجاً خامّاً. الإصلاح التنقية داخل `row()` | لا. مصادرها جذريّةٌ على المضيف، فلا أثر أمنيّ، وأقصاه خداع العرض |
| SR‑16 | جملتا ث‑7 (أثر `/-/quit` وعلاجه) في عمود «الثغرة» لا «الأثر»/«العلاج» | لا. المضمون مسجَّل، والمسألة موضع |
| SR‑20 | محاولة الحقن صارت صامتة (`up=1`). والفخّ الاختياريّ تنبيه فرقٍ بين المقياسين | لا. لا ضرر، ولا شيء يُخزَّن. تنبيهٌ جديد قرارٌ للمالك، ويحتاج قسماً في `runbooks/alerts.md` |
| SR‑13 (3) | فحص `nvidia-container-cli` | لا. تركه المالك |
| `honor_timestamps: false` | تقسيةٌ اختياريّة (security-review.md الجولة 3) | لا |

**د) حكمي في #25.** لا يحجب القبول:
1. **السلوك المسلَّم صحيح اليوم:**
   - تعبير `keep` في `deploy/prometheus/scrape-gpu.yml:34-37` يُبقي الأسماء الخمسة التي يقرؤها التنبيهان واللوحات الثلاث، ومنها `nvidia_smi_temperature_gpu`. تحقّقتُ بنفسي بـ`fullmatch`.
   - ولا يمرّ من أسماء `alerts.yml` كلّها إلّا أسماء الكرت الثلاثة، وكلّها تُقرأ بـ`job="nvidia-gpu"`.
   - فـFR‑14 وFR‑15 وFR‑16، وAC‑5.1 وAC‑5.2 وAC‑6.1 وAC‑6.3، متحقّقة على HEAD.
2. **ما يقوله #25 أنّ الحارس لا يمنع تعديلاً مقبلاً خاطئاً.**
   - وهذه قوّة حارس انحدار، لا عطبٌ في ما سُلِّم.
   - والمتطلبات لا تطلب حارساً على القاعدة: القاعدة نفسها جاءت من SR‑15 في الجولة 2، فوق المتطلبات الأصليّة.
   - والقصّتان Should.
3. **سيناريو الخطر يحتاج تعديلاً متعمَّداً** في `scrape-gpu.yml`، وهذا يمرّ بالمراجعة. وفحص 7.3 (تساوي المقياسين) يكشف التضييق مرّةً على الخادم.
4. **التوصية:** أوّل متابعةٍ في وصف الـPR، والإصلاح صغير:
   - **الموجب:** `for m in GPU_EXPORTER_METRICS: assert keep.fullmatch(m)`.
   - **السالب:** كلّ اسمٍ تقرؤه قواعد `alerts.yml` خارج `aizzak-gpu` لا يطابق التعبير.
   - يُنفَّذ قبل 7.3، أو مع أوّل تعديلٍ يلمس `scrape-gpu.yml`.

**هـ) ملاحظاتي (لا تحجب):**
1. **الإثبات الحيّ لـAC‑4.6 وAC‑6.3 والعزل جرى على `3467ba3`،** قبل حدود الكشط (`3eab510`) وقاعدة `keep` (`c576193`).
   - منذ ذلك تغيّر `docker-compose.gpu.yml` في التعليقات وحدها (`git diff 3467ba3..HEAD`).
   - و`scrape-gpu.yml` يغطّيه promtool بالوضعين، وتشغيل المراجع الأمنيّ على Prometheus v3.13.2 حقيقيّ.
   - لكن لم يُعَد التشغيل بالمصدّر الحقيقيّ على HEAD، وفحص 7.3 في (ب) يغطّي ذلك.
2. **اسمٌ قديم في `test-plan.md`:** يذكر `test_prometheus_gets_one_mount_and_nothing_else`، واسمه الآن `test_prometheus_gets_one_mount_and_the_gpu_network` (`test_compose_gpu_overlay.py:165`).
3. **صفّ 4.2 في اللوحة** لا يذكر AC‑6.2 بعد الدمج. وAC‑3.6 لا يطلبه، والإجراء (ج) يغطّيه.

## النطاق

**الفرق:** `git diff --stat master...HEAD`: 40 ملفّاً، و4897 سطراً مضافاً، و143 محذوفاً.

**لم يُمسّ** (0 ملفّ في كلٍّ منها):
- `src/`، و`deploy/auto_size.py`، و`deploy/sizes/`.
- `deploy/runpod/`، و`.github/`، و`deploy/nginx/`.
- `migrations/`، و`Dockerfile`، و`pyproject.toml`، و`.importlinter`.
- `docker-compose.wsl-gpu.yml`، و`docker-compose.build.yml`، و`docker-compose.test.yml`.

**ما تغيّر:**
- **داخل قائمة Q‑3:**
  - `docker-compose.yml`: تعليقٌ و11 سطر profile فقط.
  - `docker-compose.gpu.yml`.
  - `deploy/prometheus/{prometheus.yml,scrape-gpu.yml,alerts.yml,alerts.test.yml,test-rules.sh}`.
  - `deploy/grafana/dashboards/aizzak-capacity.json`.
  - `deploy/resource-budget.sh`.
  - تعليقات `.env.example`.
- **خارج القائمة، بموافقةٍ مسجّلة:** `deploy/gpu-preflight.sh` (status.md 2026‑10‑18، SR‑2).
- **وثائق خارج قائمة design.md §2، ومبرَّرة:** `docs/architecture.md` و`docs/architecture.html` و`docs/monitoring-plan.md`. فيها عدد القواعد 27 ← 29 (review #4).
- **والباقي:** وثائق في نطاق requirements.md §2، واختبارات `tests/unit/`.

**الوثائق التي تتحرّك معاً تحرّكت:**
- **`docs/server-gpu-plan.md`:**
  - `:95` الاستثناء الثالث.
  - `:133-134` الصفّان 4.1 و4.2.
  - `:457` جواب قرار 7.
- **`docs/server-gpu-summary.html`:**
  - مقالا 4.1 و4.2 صارا `partial`.
  - الشريط 8/9/12، ويطابق عدّ اللوحة.
- **`docs/runbooks/alerts.md`:**
  - `:75-76` سطرا الفهرس.
  - `:541-600` القسمان.
  - `:870-871` سجلّ التجربة.
- **`docs/deploy-server.md`:**
  - `:101-104` ث‑6…ث‑9.
  - §2 «دون المراقبة لا تنبيهات».

**⚠️ للإنسان: التداخل مع PR ‏#13.**
- **حال الـPR:** لم يعد مفتوحاً. دُمج في `master` يوم 2026‑10‑17 الساعة 16:25 UTC (`893972e`)، أي بعد تفرّع هذا الفرع من `47225ff`. فالفرع خلف `master` بالتزامَي PR ‏#13.
- **`docs/server-gpu-summary.html`:** يندمج بلا تعارض (دمجٌ تجريبيّ بـ`git merge-tree`). وPR ‏#13 أبقى 3.5 `partial`، فيبقى الشريط 8/9/12 صحيحاً بعد الدمج.
- **`docs/server-gpu-plan.md`:** فيه **تعارضٌ نصّيٌّ واحد**، في صفوف اللوحة 3.5 و4.1 و4.2.
  - **الحلّ:** صفّ 3.5 من `master` («🟡 مدموجة 2026‑10‑16 (PR #11 …؛ وإصلاح اختبارَي CI في PR #12 …)»)، وصفّا 4.1 و4.2 من هذا الفرع.
  - والتعديلات الأخرى في الملفّ (الاستثناء الثالث، وجواب قرار 7، وسطر «الحالة» في رأسه) تندمج وحدها.
  - وبعد الحلّ يبقى `test_the_plan_names_the_third_exception` أخضر.

## الحكم النهائي

**مقبول**: كلّ معيارٍ يمكن إثباته قبل الدمج متحقّقٌ بدليل.
- **Must:** 18 من 21 ✅. والثلاثة الباقية (AC‑1.9، وAC‑3.3، وAC‑3.4) معاييرُ بعد الدمج أو في 7.3 بتعريفها، ووافق المالك على ذلك في بوابة ①.
  - لا مرحلة تستطيع إثباتها الآن، فلا يُرفض العمل بسببها.
  - لكنّ القبول مشروطٌ بها: إن فشل AC‑3.3 بعد الدمج (بصمةٌ تختلف، أو حاويةٌ أُعيد إنشاؤها) فهو انحدارٌ يعود إلى المرحلة 6 (الخلفيّة).
- **Should:** 12 من 15 ✅، وAC‑4.6 مقبولٌ باستثناء المالك (BUG‑1)، واثنان مؤجّلان.
- **المراجعتان:** PASS في الجولات الأربع، بلا حاجب. وما بقي متابعاتٌ معلوماتيّة أو تحسينات، قبلتُها بأسمائها في (ج)، وحكمتُ في #25 أنّه لا يحجب.
- **البوّابات الستّ:** خضراء على `16498e7`.

**قبل الدمج (بوابة ②):** يُحلّ تعارض `docs/server-gpu-plan.md` مع PR ‏#13 كما في «النطاق». ثمّ تُعاد البوّابات على نتيجة الدمج.

</div>
