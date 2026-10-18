<div dir="rtl">

# خطة الاختبار: المراقبة اختياريّة وأرقام الكرت (`monitoring-profile`)

الفرع `feat/monitoring-profile` على `81fc8e5`. لم يُضَف اختبارٌ جديد: كلّ معيارٍ آليٍّ له اختبارٌ قائم قرأته وتحقّقت أنّه يؤكّد المعيار فعلاً (لا اسمه فقط)، وجرّبت إفساد الحرّاس الرئيسيّة (القسم 3).

## 1. مصفوفة التغطية

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑1.1 | وحدة | `test_monitoring_profile.py::test_exactly_the_eleven_carry_the_monitoring_profile`، `::test_each_monitoring_service_is_unchanged_but_for_its_profile[×11]`، `::test_cadvisor_stays_behind_container_metrics_alone` | ✅ |
| AC‑1.2 | وحدة | `::test_no_service_outside_monitoring_depends_on_it`، `::test_the_monitoring_edges_are_the_known_three_inside_and_five_out` | ✅ |
| AC‑1.3 | وحدة | `::test_no_service_outside_monitoring_names_a_monitoring_host`، `::test_the_host_pattern_matches…`، `::test_the_log_anchor_is_still_json_file`، و`/metrics` في `test_edge_hardening.py` القائم (`git diff master -- deploy/nginx src` فارغ) | ✅ |
| AC‑1.4 | config | `::test_without_the_profile_none_of_the_eleven_is_in_the_model` (22)، `::test_the_profile_adds_exactly_the_eleven` (33). وتحقّقتُ يدويّاً: 22 و33 | ✅ |
| AC‑1.5 | config | `::test_the_profile_key_does_not_change_any_config_hash` | ✅ |
| AC‑1.6 | config | `test_monitoring_host_postgres_acceptance.py::test_compose_refuses_to_render_without_the_exporter_password` (معامَل بالـprofile) | ✅ |
| AC‑1.7 | وحدة | الملفّات الستّة المعدَّلة (`test_monitoring_host_postgres_acceptance`، `test_compose_local_llm`، `test_observability_stack_wiring`، …) وكلّ `pytest` أخضر | ✅ |
| AC‑1.8 | محلّيّ حيّ | الإجراء (أ)، القسم 3.أ. المراقبة مفعّلةٌ في `.env` المالك، فأُوقفت وأُعيدت | ✅ |
| AC‑1.9 | خادم | مؤجَّل إلى 7.3 | ⏭ مؤجَّل |
| AC‑2.1 | وحدة | `test_resource_budget.py::test_the_budget_counts_monitoring_by_default_and_says_so[ref,large,medium,small]`. المجاميع الحاليّة تتضمّن مصدّر 4.2: ref 35.60/57.28، large 37.85/57.59، medium 19.60/28.47، small 11.75/14.28 (الأرقام في القصّة قبل 4.2). وراجعتُ السكربت يدويّاً | ✅ |
| AC‑2.2 | وحدة | `::test_without_monitoring_each_total_drops_by_the_elevens_limits[4]`، `::test_without_monitoring_conflicts_are_usage_errors`. يدويّاً: ref 32.00/52.62، والرفض برمز 2 مع `--block` و`--with-profiles` | ✅ |
| AC‑2.3 | وحدة | `test_auto_size.py::test_the_budget_block_follows_the_blocks_profiles` | ✅ |
| AC‑2.4 | وحدة | `test_auto_size.py::test_monitoring_is_a_known_profile_and_counts_only_when_named` + الاختباران القائمان (11.72/11.40/0.32، 23.22/1.83x) | ✅ |
| AC‑2.5 | وحدة | `test_resource_budget.py::test_without_monitoring_the_standing_set_is_seventeen`، `::test_the_patterns_actually_find_something`، `::test_the_script_and_this_module_agree`، واتّفاق السكربت والوحدة بلا مراقبة داخل `test_without_monitoring_each_total…` (لا اختبار منفصل بالاسم الذي ذكره التصميم، والتأكيد موجود) | ✅ |
| AC‑2.6 | نصّ | `test_monitoring_profile.py::test_the_ledger_explains_monitoring_on_by_default` | ✅ |
| AC‑3.1 | نصّ | `::test_both_guides_carry_the_monitoring_lines[stack-commands, quickstart]` + `test_gpu_compose_hygiene.py` القائم | ✅ |
| AC‑3.2 | وحدة | `test_compose_local_llm.py::test_env_example_offers_both_profiles_commented_only`، `test_gpu_compose_hygiene.py::test_env_example_activates_no_compose_selector` | ✅ |
| AC‑3.3 | مالك بعد الدمج | الإجراء (ج) | ⏭ المالك |
| AC‑3.4 | مالك بعد الدمج، شرطيّ | الإجراء (ج) خطوة 6 | ⏭ المالك |
| AC‑3.5 | نصّ | `::test_no_monitoring_means_no_alerts_is_said_where_it_is_read` | ✅ |
| AC‑3.6 | نصّ | `::test_the_plan_names_the_third_exception`. والملخّص HTML بالمراجعة: الشريط 8+9+12 = 29 يطابق اللوحة، والبطاقتان `partial` | ✅ |
| AC‑4.1 | وحدة | `test_compose_gpu_overlay.py::test_the_gpu_exporter_is_pinned_internal_unprivileged_and_bounded`، `::test_the_gpu_exporter_logs_like_every_other_service`، `test_every_compose_image_is_pinned` | ✅ |
| AC‑4.2 | وحدة + config | `test_compose_gpu_overlay.py::test_the_overlay_has_only_services…`، `::test_prometheus_gets_one_mount_and_nothing_else`، `::test_the_exporter_exists_only_with_the_monitoring_profile[×4]`، `::test_the_overlay_changes_only_the_services_it_names[×2]`. وتحقّقتُ يدويّاً: بالملفّين بلا profile عدد الخدمات 22 ولا مصدّر | ✅ |
| AC‑4.3 | قاعدة | `test-rules.sh` (فحصا `check config` بلا ملفّ الكرت ومعه، وحالتا promtool)، و`test_prometheus_alert_rules.py::test_the_rule_script_checks_the_config_with_and_without_the_gpu_file` | ✅ |
| AC‑4.4 | وحدة | `test_observability_stack_wiring.py::test_every_scrape_target_names_a_service_that_exists`، `::test_the_gpu_scrape_file_is_mounted_where_prometheus_looks`، `::test_the_gpu_target_is_ordinary_and_its_job_unique`، `test_healthcheck_coverage.py` | ✅ |
| AC‑4.5 | وحدة | `test_resource_budget.py::test_every_size_fits_the_host_its_header_names[×3]` (large 57.59 ≤ 57.60 — هامش 0.01 فقط، medium 28.47 ≤ 28.80، small 14.28 ≤ 14.40)، `::test_the_monitoring_share_is_the_elevens_limits_plus_the_gpu_exporter[×3]` | ✅ |
| AC‑4.6 | محلّيّ حيّ | الإجراء (ب)، القسم 3.ب. الهدف `up`، والسلاسل موجودة، `read_only` يعمل، لا OOM، **لكنّ `memory.peak` × 1.5 يتجاوز `64m`** (BUG‑1) | 🔴 |
| AC‑4.7 | نصّ | `::test_the_security_list_records_the_gpu_exporter_image` (صفّ `ث‑6`) | ✅ |
| AC‑5.1 | وحدة | `test_prometheus_alert_rules.py::test_the_gpu_rules_read_one_card_at_a_time`، `EXPECTED_ALERTS` = 29. قارنتُ العتبات والمدد بـQ‑5 يدويّاً: > 0.90 لـ5m، > 85 لـ5m، `warning` | ✅ |
| AC‑5.2 | قاعدة | حالات promtool في `alerts.test.yml`، و`test_every_rule_is_fired_and_held_silent_by_the_promtool_suite`. `test-rules: OK` | ✅ |
| AC‑5.3 | وحدة | `test_every_rule_opens_its_own_runbook_section`، `::test_the_gpu_runbook_sections_start_with_reading` | ✅ |
| AC‑5.4 | نصّ | `::test_the_trial_log_lists_the_gpu_rules` | ✅ |
| AC‑5.5 | خادم | مؤجَّل إلى 7.3 | ⏭ مؤجَّل |
| AC‑6.1 | وحدة | `test_observability_stack_wiring.py::test_the_gpu_row_shows_memory_load_and_temperature`، `::test_every_gpu_metric_a_panel_or_rule_reads_is_one_the_exporter_emits` + اختبارات `description`/`uid` القائمة | ✅ |
| AC‑6.2 | مالك بعد الدمج | الإجراء (ج) خطوة 7 | ⏭ المالك |
| AC‑6.3 | محلّيّ حيّ | الإجراء (ب) خطوة 6، القسم 3.ب. كلّ تعبير من الثلاثة عبر `/api/ds/query` أعاد إطاراً غير فارغ | ✅ |

**العدّ (36):** آليّ 28 ✅ (كلّها خضراء). حيّ منجز: AC‑1.8 ✅ وAC‑6.3 ✅ وAC‑4.6 🔴 (لا مصدّر ذاكرة ضمن الحدّ). للمالك بعد الدمج: 3 (AC‑3.3، AC‑3.4، AC‑6.2). مؤجَّل إلى 7.3: 2 (AC‑1.9، AC‑5.5).

## 2. نتائج بوابات المشروع

على `81fc8e5`، و`.venv/bin` في المسار:
```
ruff format --check .   -> 821 files already formatted                 rc 0
ruff check .            -> All checks passed!                           rc 0
mypy src                -> Success: no issues found in 467 source files rc 0
lint-imports            -> Contracts: 8 kept, 0 broken.                 rc 0
pytest -rs tests/unit tests/architecture tests/eval
                        -> 5818 passed, 7 warnings in 223.22s (0:03:43) rc 0 (لا تخطٍّ)
COMPOSE_FILE=docker-compose.yml:docker-compose.build.yml:docker-compose.test.yml pytest -rs tests/unit
                        -> 5817 passed, 7 warnings in 222.69s (0:03:42) rc 0
deploy/prometheus/test-rules.sh -> ... SUCCESS: 29 rules found / test-rules: OK  rc 0
```
(شغّلتُ `pytest` الأوّل مع إلغاء `COMPOSE_FILE` و`COMPOSE_PROFILES` من بيئة الصدفة. لم تُشغَّل `tests/integration`.)

## 3. الاختبار الاستكشافي

### أ) AC‑1.8: إيقاف الإحدى عشرة في المكدّس الحيّ (Procedure A)
`.env` المالك يفعّل `monitoring` أصلاً (الإحدى عشرة قامت بـ`docker compose up -d`، ولم يُقرأ `.env`).
| الخطوة (UTC) | الناتج |
|---|---|
| 05:46:43 `docker compose up -d` | 34 حاوية قامت وصحّت (التطبيق ×3 healthy) خلال ~2.5 دقيقة، ولا مشكلة في Vault أو Ollama |
| 05:50:24 قبل الإيقاف | الإحدى عشرة تعمل وhealthy |
| `docker compose stop <11>` | تعمل منها: 0 |
| `/health` | 200 |
| `/health/ready` | 200 |
| `stack_smoke.py` (داخل `app`) | PASS، rc=0 (pgbouncer/OPS‑02، RLS يفشل مغلقاً، عنوان presign) |
| 05:50:39 `up -d --no-deps <11>` | كلّها «Started» ولم يُعَد إنشاء أيٍّ منها، وصارت 11 healthy عند 05:51:25 |
| `scrape_config_files` في إعداد Prometheus | 1 |
| قاعدتا الكرت في `/api/v1/rules` | حاضرتان وغير نشطتين |
| هدف `nvidia-gpu` | 0 (لا ملفّ كرت هنا) |
| `AizzakWatchdog` في `alert-sink` بعد الإعادة | سطر عند 05:55:13 (قبل الإيقاف 05:49:14)، أي في أقلّ من 10 دقائق. **المدّة الكلّيّة ≈ 5 دقائق** |
حالة النهاية: أُوقفت 34 حاوية بالاسم (`docker stop`)، و`aizzak-cadvisor-1` ما زالت `Up 14 hours (healthy)`. لا `down` ولا `-v`.

### ب) AC‑4.6 وAC‑6.3 (Procedure B) في مشروع `aizzak-gpumon-proof`
قبل البدء: متاح 10.5 ج.ب. الكرت `RTX 4060 Laptop 8188 MiB`. الصورة سُحبت بالبصمة `7aee2d42…`.
| البند | الناتج |
|---|---|
| `config -q` | ok |
| الخدمات الثلاث (`up -d --no-deps nvidia-gpu-exporter prometheus grafana`) | قامت وصارت healthy |
| `read_only: true` مع أداة NVIDIA | **يعمل**: `ReadonlyRootfs=true`، الصحّة healthy، `restarts=0` |
| هدف `nvidia-gpu` | `"health":"up"`، `lastError:""` |
| المقاييس | `memory_used_bytes` 1011875840، `memory_total_bytes` 8585740288، `utilization_gpu_ratio` 0.06، `temperature_gpu` 44، و`gpu_info` فيه `name="NVIDIA GeForce RTX 4060 Laptop GPU"`; كلّها بـ`uuid` |
| `OOMKilled` | **false** |
| `memory.peak` (cgroup v2، بايت) | 37339136 بعد دقيقة، 42303488 عند 05:40، 43700224 عند 05:43، **56692736 عند 05:45:50** (آخر قراءة، 54.1 MiB). الحدّ `memory.max` = 67108864 |
| تقسيم الذاكرة الحاليّة | `anon` 7.5 م.ب و`file` (ذاكرة تخزين مؤقّت للصفحات، قابلة للاسترداد) 16.6 م.ب؛ `docker stats` ≈ 19.6 MiB |
| حكم Q‑8 | **56692736 × 1.5 = 85 م.ب > 64 MiB (67108864)**. والعتبة 44739242 بايت. **تجاوز**، فتوقّف الجزء الحيّ للمصدّر هنا ولم أغيّر `large.env` ولا أيّ حدّ |
| AC‑6.3 | كلّ تعبير من اللوحات الثلاث عبر `/api/ds/query` على `aizzak-prometheus` أعاد إطاراً واحداً غير فارغ: الذاكرة 0.1179، الحمل 0.06، الحرارة 44 |
| `down` بلا `--profile` | **تبقى الحاويات الثلاث تعمل** (الشبكة «still in use»). يؤكّد حذر §5.6 |
| `down --profile monitoring` (بلا `-v`) | أُزيلت الحاويات والشبكة |
**أحجام scratch المتبقّية** (تركتُها، `down -v` محجوب): `aizzak-gpumon-proof_grafana-data`، `_minio-data`، `_postgres-data`، `_prometheus-data`، `_qdrant-data`، `_redis-data`، `_vault-data`، `_vault-init`، `_wal-archive`. كلّها فارغة تقريباً وتخصّ المشروع التجريبيّ وحده. وصورة المصدّر ما زالت في الجهاز.

### ج) فحوص config وغيرها
| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| خدمات بلا profile | `config --services` بلا `COMPOSE_*` و`--env-file .env.example` | 22، بلا الإحدى عشرة | 22 | ✅ |
| `--profile monitoring` | نفسه | 33 | 33 | ✅ |
| ملفّا الكرت بلا profile / مع | `-f … -f gpu.yml` | 22 بلا مصدّر / المصدّر حاضر | 22، 0 مصدّر / `nvidia-gpu-exporter` | ✅ |
| `--profile X` يحلّ محلّ `COMPOSE_PROFILES` | `COMPOSE_PROFILES=wsl-ollama,monitoring` + `--profile backup` | بلا مراقبة | 0 prometheus/grafana، 23 خدمة | ✅ |
| `resource-budget.sh --without-monitoring` | الأرقام ورموز الخروج | 32.00/52.62، رفض برمز 2 | 32.00/52.62؛ `--block` رمز 2؛ `--with-profiles` رمز 2 | ✅ |
| أحجام الميزانيّة | `--size large/medium/small` | ضمن 90٪ | 57.59/57.60، 28.47/28.80، 14.28/14.40 | ✅ (هامش large ضيّق جدّاً) |
| تنبيها الكرت | قراءة `alerts.yml` | Q‑5 | 0.90/5m و85/5m و`warning`، بلا قسمة على صفر (`total > 0`) | ✅ |
| لوحة Grafana | تحميل JSON | صفّ + 3 لوحات، أسماء مقاييس المصدّر | صحيح؛ وعلى المصدّر الحقيّ كلّها وُجدت | ✅ |
| دليل التشغيل | مرساتان وسطرا فهرس §1 وصفّا §6 | موجودة | `alerts.md:75-76, 541, 573, 870-871` | ✅ |
| `deploy-server.md` | صفوف تبدأ برقم | فقط جدول رموز الخروج القائم | الصفوف الرقميّة كلّها في جدول `gpu-preflight` (0، 2، 10–14) | ✅ |
| `test-rules.sh` الوضعان | تشغيل | OK | `check config` بلا ملفّ الكرت ومعه، 29 قاعدة، OK | ✅ |
| إفساد الحرّاس (نسخة scratch من المستودع، لا الملفّ الحقيقيّ) | M1 حذف `profiles` من `loki`؛ M2 `depends_on: prometheus` في `nginx`؛ M3 حذف profile من المصدّر؛ M4 وسم المصدّر `latest`؛ M5 ذكر `loki:3100` في `app`؛ M6 `alerts.yml` ← 0.95؛ M7 `for: 1m` للحرارة | الفشل | كلّها فشلت: `test_exactly_the_eleven…`، `test_without_the_profile_none…`، `test_exactly_the_eleven…`، `test_the_gpu_exporter_is_pinned…`، `test_no_service_outside_monitoring_names…`، وفي M6 وM7 فشل `test_the_gpu_rules_read_one_card…` وpromtool | ✅ |

## 4. الأخطاء المكتشفة

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| BUG‑1 | **ذروة ذاكرة المصدّر تتجاوز حدّ Q‑8.** `memory.peak` = 56692736 بايت (54.1 MiB)، فـ× 1.5 = 85 م.ب > 64 MiB (عتبة 44739242 بايت). السبب الغالب ذاكرة تخزين صفحات (`file` 16.6 م.ب) تُقرأ من مكتبات أداة NVIDIA ومن `nvidia-smi`، لا ذاكرة العملية (`anon` 7.5 م.ب، `docker stats` ≈ 19.6 MiB). لم تحدث OOM ولا إعادة تشغيل، والذروة تقفز تدريجيّاً (37.3 ← 42.3 ← 43.7 ← 56.7 م.ب خلال 10 دقائق). **AC‑4.6 لا يتحقّق بالنصّ الحاليّ.** القرار للمالك (رفع حدّ المصدّر أو خفض حدٍّ آخر في `deploy/sizes/large.env` وهامشه 0.01 ج.ب فقط، أو قبول القاعدة على `anon` لا على الذروة). لم أغيّر شيئاً | major (يحجب AC‑4.6 فقط؛ لا أثر على 4.1 ولا على بقيّة 4.2) | `cd /home/AIZZAK; G="docker compose -p scratch --env-file .env.example -f docker-compose.yml -f docker-compose.gpu.yml --profile monitoring"; $G up -d --no-deps nvidia-gpu-exporter prometheus grafana`؛ دَع 10 دقائق؛ اقرأ `/sys/fs/cgroup/system.slice/docker-<id>.scope/memory.peak`. المتوقّع ≤ 44739242، الفعليّ 56692736. الموضع: `docker-compose.gpu.yml` (حدّ `memory: 64m` للمصدّر) و`deploy/sizes/large.env` | مفتوح، بانتظار قرار المالك |
| BUG‑2 | ملاحظة بلا خلل: نصّ AC‑2.1 في `stories.md` يذكر 37.60/57.53 قبل 4.2، والسكربت الآن 37.85/57.59 لأنّ المصدّر (0.25 vCPU، 64m) محسوبٌ في وضع الكرت. الاختبارات والتصميم يعرفان ذلك | minor (توثيق) | `deploy/resource-budget.sh --size large --host-cpus 32 --host-memory-gb 64` | لا إجراء |

## 5. الحكم

**FAIL** — بوابات الجودة الست خضراء وكلّ معيار آليٍّ مغطّى باختبارٍ يؤكّده (وفسدت الحرّاس عند الإفساد)، وAC‑1.8 وAC‑6.3 أُثبتا حيّاً، لكنّ AC‑4.6 فشل في شرط Q‑8 (ذروة المصدّر × 1.5 أكبر من `64m`، BUG‑1)، وهو قرارٌ للمالك لا تعديلٌ لي.

## 6. الجولة 1 (بعد المراجعتين، HEAD `3467ba3`)

**ما أُعيد فحصه:** الإصلاحات الستّة منذ `3880ff9`: الشبكة الداخليّة `gpu-metrics`، وعلَمَا `--collect.interval=15s` و`--web.max-requests=2`، وفحص `ctk` في `deploy/gpu-preflight.sh` (≥ 1.17.8، الخروج 12)، وسطر «monitoring:» في `resource-budget.sh --block`، وحارس الملفّ المدموج في `test_monitoring_profile.py`، وحارس الأدلّة لـ`COMPOSE_PROFILES` العارية.

### أ) إفساد الحرّاس (نسخة scratch خارج المستودع، حُذفت بعدها)

| الإفساد | الاختبار الذي فشل | النتيجة |
|---|---|---|
| المصدّر على `default` أيضاً | `test_the_gpu_exporter_is_pinned_internal_unprivileged_and_bounded` | ✅ |
| `internal: true` ← `false` | `test_the_exporter_joins_no_network_but_an_internal_gpu_metrics` | ✅ |
| `prometheus.networks` بلا `default` | `test_prometheus_gets_one_mount_and_nothing_else` | ✅ |
| `--collect.interval=15s` ← `1s` | `test_the_gpu_exporter_is_pinned_internal_unprivileged_and_bounded` | ✅ |
| مقارنة `ctk`: `>=` ← `>` على رقم الإصدار الأخير | `…compared_semantically[exactly-the-minimum]` وغيره | ✅ |
| مقارنة نصّيّة (lexical) بدل الرقميّة | `…compared_semantically[numeric-not-text]` و`[numeric-minor]` | ✅ |
| الحالات المتوقّعة بلا إفساد | 1.17.8 ينجح، 1.20.1 ينجح، 2.0.0 ينجح، 1.17.10 ينجح، 1.17.7 يفشل (12)، 1.16.2 يفشل، 1.9.30 يفشل؛ غياب `nvidia-ctk` أو ناتج غير مقروء أو خروج ≠ 0 ← 12 | ✅ |

لا حاجة إلى اختبارٍ جديد: كلّ إصلاح له اختبارٌ يفشل عند إفساده.

### ب) القياس الحيّ (Procedure B)، المشروع `aizzak-gpumon-proof` بملفّ `docker-compose.gpu.yml` الجديد

`free` المتاح 10829 م.ب قبل البدء. المصدّر `healthy` طوال 10 دقائق، `restarts=0`، `OOMKilled=false`.

| القياس | الجولة السابقة | الآن | قاعدة Q‑8 (الذروة × 1.5 ≤ 64 MiB، أي ≤ 42.7 MiB) |
|---|---|---|---|
| `memory.peak` | 56692736 (54.1 MiB) | **58929152 (56.2 MiB)** | ❌ × 1.5 = 84.3 MiB |
| `memory.current` عند 10 دقائق | — | 41816064 (39.9 MiB) | — |
| `anon` / `file` | 7.5 / 16.6 م.ب | **8.2 MiB / 30.5 MiB** | الذروة في معظمها ذاكرة تخزين صفحات |
| `docker stats` | ≈ 19.6 MiB | 21.6 MiB / 64 MiB، المعالج 0.10% | — |

الخلاصة: العلَمان لم يخفضا الذروة (ارتفعت 2 م.ب، وهو ضمن التذبذب). لا OOM. الحدّ `64m` باقٍ بقرار المالك. **BUG‑1 يبقى «مقبولٌ من المالك، يُعاد قياسه في 7.3»** ولا يجعل الجولة FAIL.

### ج) العزل والجمع

| الفحص | الطريقة | النتيجة |
|---|---|---|
| الشبكة `gpu-metrics` | `docker network inspect` | `Internal=true`، عضواها: المصدّر وPrometheus فقط ✅ |
| Prometheus يجمع `nvidia-gpu` عبرها | `/api/v1/targets` | `up` ✅؛ استعلامات `memory_used/total` و`utilization` و`temperature` ≥ 1، ونتيجة `gpu_info` فيها `name` و`uuid` ✅ |
| المصدّر لا يصل إلى الإنترنت | حاوية `prom/prometheus` المحلّيّة (`--network container:<exporter>`) تتصل بـ`1.1.1.1:443` | فشل ✅ (الشاهد: من شبكة Prometheus نفسها يتّصل) |
| المصدّر لا يرى خدمات `default` | حلّ الاسم والاتصال بـ`redis-stream:6379` | `bad address` ✅ (الشاهد: من Prometheus يتّصل) |
| المصدّر يصل إلى نظيره فقط | `prometheus:9090` | يتّصل (متوقّع، هو العضو الآخر في `gpu-metrics`) ✅ |
| Prometheus ما زال يجمع أهداف `default` | شغّلتُ `redis-stream` و`redis-stream-exporter` في المشروع scratch | `redis-stream` = `up` ✅ (بقيّة الأهداف `down` لأنّها لم تُشغَّل، وهذا متوقّع) |

### د) فحص التجهيز على هذا المضيف

`deploy/gpu-preflight.sh` (بلا `--smoke`: يقرأ الإصدارات فقط ولا يبدأ حاويات): driver OK (RTX 4060 Laptop, 592.82)، docker OK (29.6.1)، toolkit OK، **ctk OK 1.20.1 (>= 1.17.8)**، compose OK (5.3.1)، `OK (ready for docker-compose.gpu.yml)`، الخروج 0.

### هـ) البوّابات على HEAD `3467ba3`

| البوّابة | النتيجة |
|---|---|
| `ruff format --check .` | 821 files already formatted، 0 |
| `ruff check .` | All checks passed، 0 |
| `mypy src` | no issues found in 467 source files، 0 |
| `lint-imports` | 8 kept, 0 broken، 0 |
| `pytest -rs tests/unit tests/architecture tests/eval` | **5834 passed**, 7 warnings، 0 (لا skips) |
| `tests/unit` مع `COMPOSE_FILE=docker-compose.yml:docker-compose.build.yml:docker-compose.test.yml` | **5833 passed**, 7 warnings، 0 |
| `deploy/prometheus/test-rules.sh` | 29 rules found, `test-rules: OK`، 0 |

لم يُشغَّل `tests/integration`.

### و) التنظيف

`down --profile monitoring` على المشروع scratch أزال الحاويات والشبكتين. محاولة حذف الأحجام بخيار `-v` منعها حارس الأحجام (ت‑4)، فلم أحذف شيئاً. أحجام scratch الباقية للمالك (تُحذف بالاسم عبر `docker volume rm`): `aizzak-gpumon-proof_{grafana-data,minio-data,postgres-data,prometheus-data,qdrant-data,redis-data,vault-data,vault-init,wal-archive}`. مكدّس `aizzak` لم يُمسّ (يعمل `aizzak-cadvisor-1` وحده كما كان).

### ز) الحكم

**PASS** — كلّ إصلاحٍ مثبتٌ باختبارٍ يفشل عند إفساده، والعزل مثبتٌ حيّاً، والبوّابات خضراء. BUG‑1 (الذروة 56.2 MiB، × 1.5 > 64 MiB) مقبولٌ من المالك حتى إعادة القياس في 7.3، ولا أخطاء جديدة.

</div>
