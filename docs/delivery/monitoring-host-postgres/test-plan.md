<div dir="rtl">

# خطة الاختبار: مراقبة الجهاز وPostgres (المرحلة 2، الصفّان 1 و2)

- **الفرع:** `monitoring` (ستّ commits من الميزة + commit الجودة). **التاريخ:** 2026‑10‑05.
- **النطاق:** US‑1…US‑6 وUS‑8 وUS‑9. **US‑7 (اللوحات) مؤجّلة ولا تُختبر.**
- **ملاحظة عن شجرة العمل:** `src/app/ops/backup.py` و`tests/unit/test_backup_wiring.py` و`tests/unit/test_ops_backup.py` فيها تعديلاتٌ غير مُودَعة من جلسةٍ أخرى (تغيير `QDRANT_RETENTION`). بوّابة pytest أدناه جرت على الشجرة كما هي، أي معها تلك التعديلات. لم تُلمس.
- **الرموز في عمود النوع:** وحدة = `pytest tests/unit`؛ promtool = `deploy/prometheus/test-rules.sh`؛ Compose = `docker compose config` على `.env.example`؛ CI‑تكامل = `tests/integration` (لم يُشغَّل هنا: ممنوع دون إذن، يشغّله job ‏`integration`)؛ حيّ = فحصٌ مُسجَّل بتاريخه على الحزمة المحلّيّة.
- **اختبارات أضافها QA:** `tests/unit/test_monitoring_host_postgres_acceptance.py` (20 اختباراً: كلّها ناجحة بعد إصلاح BUG‑1 وإزالة `xfail`).

## 1. مصفوفة التغطية

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑1.1 | Compose + وحدة | `tests/unit/test_monitoring_host_postgres_acceptance.py::test_compose_renders_clean_from_the_example_env` و`::test_compose_renders_both_exporters_as_standing_services_with_limits` (صورةٌ بإصدار، `cpus` و`memory`، لا `profiles` ولا `ports`)؛ و`test_observability_stack_wiring.py::test_the_scraper_and_exporters_publish_no_host_port`؛ و`test_metrics_exporter_role.py::test_both_new_images_are_pinned_to_a_version`. حيّ: `config --quiet` خرج 0 | ✅ |
| AC‑1.2 | promtool + وحدة | `test-rules.sh` الخطوة الرابعة (`promtool check config`: SUCCESS، 27 قاعدة)؛ `test_observability_stack_wiring.py::test_every_scrape_target_names_a_service_that_exists` و`::test_the_host_and_postgres_jobs_scrape_their_exporters` | ✅ |
| AC‑1.3 | وحدة + Compose | `test_observability_stack_wiring.py::test_the_host_exporter_reads_no_host_filesystem`؛ وفي اختبار QA أعلاه: لا `docker.sock`، وكلّ تركيبٍ bind للقراءة فقط، و`user` = `65534:65534` | ✅ |
| AC‑1.4 | حيّ | `up{job="node"}` = 1 (§3، الفحص ح‑1) | ✅ |
| AC‑1.5 | حيّ | `node_memory_MemTotal_bytes` = 13,599,727,616 مقابل `/proc/meminfo` ‏13,280,984 kB × 1024 = 13,599,727,616 (تطابق تامّ)؛ عدد المعالجات 10 = `nproc` | ✅ |
| AC‑1.6 | حيّ | `node_filesystem_size_bytes` سلسلةٌ واحدة: `mountpoint="/"`, `fstype="ext4"`؛ لا tmpfs ولا overlay ولا 9p | ✅ |
| AC‑2.1 | وحدة | `test_metrics_exporter_role.py`: `test_the_role_is_created_inside_an_existence_check`، `test_the_password_is_a_psql_variable_never_shell_interpolated`، `test_pg_monitor_with_inherit_is_the_only_grant` (الموضع: `15-metrics-exporter.sh` لا `10-roles.sh`، قرار ق‑2 في التصميم) | ✅ |
| AC‑2.2 | وحدة | `test_metrics_exporter_role.py::test_the_exporter_alone_requires_the_password` و`::test_the_password_never_enters_the_postgres_service` و`::test_the_role_script_sorts_between_the_roles_and_the_extensions`؛ وسطر `change-me-*` في `.env.example` و`.env.test.example` | ✅ |
| AC‑2.3 | Compose + وحدة | `test_monitoring_host_postgres_acceptance.py::test_compose_refuses_to_render_without_the_exporter_password` (ملفّ بيئةٍ مؤقّت دون المتغيّر: خروج ≠ 0 والرسالة تسمّيه؛ قيس يدويّاً أيضاً: `required variable METRICS_EXPORTER_PASSWORD is missing a value`) | ✅ |
| AC‑2.4 | CI‑تكامل + حيّ (كتالوج) | `tests/integration/test_metrics_exporter_role_live.py`: `test_the_role_holds_no_dangerous_attribute`، `test_it_can_read_no_table_and_write_none`، `test_it_is_refused_every_tenant_table`، `test_it_sees_other_sessions_through_pg_stat_activity`. **لم يُشغَّل هنا** (ممنوع). فحصٌ كتالوجيّ للقراءة فقط على العنقود الحيّ: `f|f|f|f` للصفات الأربع، وعضويّة `pg_monitor` بـ`inherit_option=t`، و`has_table_privilege` على `workspace.users` SELECT = f وعلى `platform.outbox` INSERT = f، ولا منح جداول (0) | ✅ (التكامل ينتظر CI) |
| AC‑2.5 | CI‑تكامل + حيّ (كتالوج) | `test_the_role_is_bounded_by_its_own_settings`. حيّ: `rolconfig={statement_timeout=5s,lock_timeout=1s,default_transaction_read_only=on}` و`rolconnlimit=2` | ✅ (التكامل ينتظر CI) |
| AC‑2.6 | وحدة (شكلاً) + حيّ | `test_metrics_exporter_role.py::test_every_attribute_is_reasserted_so_a_rerun_converges`. **لا اختبار تكاملٍ ينفّذ السكربت مرّتين** (CI يشغّله مرّةً واحدة). الدليل التنفيذيّ: إجراء §3.3‑ج يشغّله مرّتين ويقارن السطر، وحالة الدور الحيّة الآن تطابق المتوقَّع حرفيّاً (§3 الفحص ح‑6). لم أُعد تنفيذه على العنقود الحيّ (DDL) | ✅ مبرَّر (انظر الملاحظة 1 في §4) |
| AC‑3.1 | وحدة + Compose | `test_metrics_exporter_role.py::test_the_exporter_connects_directly_as_its_own_role` و`::test_the_exporter_alone_requires_the_password`؛ اختبار QA: `depends_on postgres: service_healthy`، ولا `POSTGRES_SUPERUSER_PASSWORD` ولا `APP_RW_PASSWORD` في بيئة المُصدِّر؛ `expose` بلا `ports`؛ `DATA_SOURCE_URI` يبدأ بـ`postgres:5432/` | ✅ |
| AC‑3.2 | وحدة | كلّ `tests/unit/test_observability_stack_wiring.py` ناجح؛ منها `test_the_host_and_postgres_jobs_scrape_their_exporters` و`test_the_scraper_and_exporters_publish_no_host_port` | ✅ |
| AC‑3.3 | حيّ | `up{job="postgres"}` = 1 و`pg_up` = 1 (ح‑1) | ✅ |
| AC‑3.4 | حيّ | وُجدت سلاسل لـ: `pg_up`، `pg_locks_count` (45 سلسلة)، `pg_lock_wait_longest_seconds`، `pg_stat_activity_max_tx_duration`، `pg_database_size_bytes`، `pg_stat_user_tables_table_size_bytes` (43 جدولاً)، `pg_archive_ready_segments`، `pg_archive_ready_oldest_age_seconds`، `pg_stat_archiver_last_archive_age` | ✅ |
| AC‑3.5 | حيّ | `pg_stat_activity` للدور: 1 (≤ 2)؛ والسقف من الخادم `CONNECTION LIMIT 2` | ✅ |
| AC‑3.6 | حيّ (أُكمل بعد الدورة 1) | قياس ساعةٍ كاملة عند 20:48 UTC (المُصدِّران يعملان منذ 18:11، أي أكثر من ساعتين): `max_over_time(scrape_samples_scraped[1h])` = 1535 (postgres) و357 (node) (< 3000)؛ P95 زمن الكشط خلال الساعة = 0.034 ث (postgres) و0.012 ث (node) (< 2)؛ لا سلسلة بتسمية `queryid` | ✅ |
| AC‑3.7 | وحدة | `test_metrics_exporter_role.py::test_no_per_query_series_can_be_exported` (+ `queries.yaml`: كلّ الأعمدة GAUGE، لا تسمية) | ✅ |
| AC‑4.1 | promtool | `alerts.test.yml`: «the root filesystem at 85% past ten minutes fires once, with its mountpoint» (صامتٌ عند 9m، يشتعل عند 11m بـ`severity: warning` و`mountpoint: /`) | ✅ |
| AC‑4.2 | promtool + استكشاف | «79 percent, the Windows drive, an overlay and a zero-size mount are silent» (79% و9p و`overlay`)؛ وأضفتُ استكشافيّاً حالة tmpfs وقرصٍ آخر (§3). حالة «85% لمدّةٍ أقصر من `for`» تغطّيها حالة 9m في الاختبار الأول | ✅ |
| AC‑4.3 | promtool | الحالة نفسها: حجم 0 وavail 0 لا يشتعل (قسمة على صفر = NaN) | ✅ |
| AC‑4.4 | وحدة | الاختبارات الأربعة المسمّاة كلّها ضمن 5102 ناجحاً: `test_every_rule_carries_the_minimum_operator_fields`، `test_every_rule_opens_its_own_runbook_section`، `test_every_rule_is_fired_and_held_silent_by_the_promtool_suite`، `test_the_file_declares_exactly_the_expected_alerts` | ✅ |
| AC‑4.5 | وحدة (جديد) | `test_monitoring_host_postgres_acceptance.py::test_each_new_runbook_section_is_complete[AizzakHostDiskHigh]` (ماذا يعني، خطوات مرقّمة، أوّل أمر `df -h /` للقراءة فقط، «كيف تعرف أنّه زال»، صفّ الفهرس)؛ `::test_the_disk_runbook_forbids_the_three_data_destroying_commands`؛ `::test_the_disk_runbook_admits_the_windows_drive_is_not_watched` | ✅ |
| AC‑5.1 | promtool | «a dead server behind a live exporter fires within the minute» (`pg_up==0` ← `critical`) و«a server that answers is silent» | ✅ |
| AC‑5.2 | promtool + وحدة | «a dead exporter is a scrape failure, not a dead server» (يشتعل `AizzakScrapeTargetDown`، ولا `AizzakPostgresDown`)؛ و`test_prometheus_alert_rules.py::test_the_postgres_liveness_rule_reads_the_exporters_verdict` | ✅ |
| AC‑5.3 | promtool | «a lock wait past a minute that persists fires» (`warning`) و«a 50-second wait and a one-sample 70 are silent»؛ استكشافيّاً: الحدّ 60 تماماً صامت و61 يشتعل (§3) | ✅ |
| AC‑5.4 | promtool | «a segment stuck past fifteen minutes fires, whatever failed_count says» و«quiet and healthy clusters are silent however old the last archive is» | ✅ |
| AC‑5.5 | وحدة | `test_prometheus_alert_rules.py::test_the_archive_rule_reads_progress_not_the_failure_counter` | ✅ |
| AC‑5.6 | وحدة | `test_the_file_declares_exactly_the_expected_alerts` (فيه الأسماء الأربعة وفقرة السبب)، و`test_the_postgres_liveness_rule_reads_the_exporters_verdict`، و`test_the_lock_rule_thresholds_one_minute_of_waiting`، و`test_the_disk_rule_watches_root_only` | ✅ |
| AC‑5.7 | وحدة (جديد) | `test_each_new_runbook_section_is_complete[AizzakPostgresDown\|AizzakPostgresLockWaitHigh\|AizzakPostgresArchiveStalled]` (أوّل أمر: `docker compose ps postgres` / `pg_blocking_pids` / `pg_stat_archiver`، ولا فعل تعديل)؛ `::test_no_new_runbook_step_restarts_postgres_without_saying_it_is_human[…]` | ✅ |
| AC‑5.8 | وحدة | `test_prometheus_alert_rules.py::test_the_backup_age_question_points_at_the_ops_task_rule` | ✅ |
| AC‑6.1 | promtool | «a dead node exporter fires, a 15-second blip on the postgres one does not» (عند 45s يشتعل لـ`job="node"`) و«a dead exporter is a scrape failure…» (لـ`job="postgres"`) | ✅ |
| AC‑6.2 | promtool | الحالة نفسها: ومضة 15 ثانية لا تشتعل | ✅ |
| AC‑6.3 | وحدة | `test_observability_stack_wiring.py::test_the_optional_target_is_the_one_behind_a_compose_profile` | ✅ |
| AC‑6.4 | حيّ (نُفِّذ) | `docker compose stop postgres-exporter` ← `AizzakScrapeTargetDown{job="postgres"}` معلَّقٌ (pending) بعد 31 ث ومشتعلٌ (firing) بعد 61 ث؛ `alert-sink`: سطر `firing` بـ`starts_at` 18:19:06.935Z (بعد الإيقاف 18:18:17 بـ49 ث)، ثمّ بعد `up -d --no-deps` سطر `resolved` بـ`ends_at` 18:20:21.935Z وعاد `up=1` خلال 12 ث وانطفأ التنبيه بعد 24 ث (§3 ح‑2) | ✅ |
| AC‑8.1 | حيّ دون اتّصال | `deploy/resource-budget.sh --host-cpus 32 --host-memory-gb 64` خرج 0: `MEMORY OK 57.53 of 64.00 GB`؛ `node-exporter` 0.25 vCPU / 0.06 GB، و`postgres-exporter` 0.25 / 0.12 GB. (CPU يُحذَّر `OVERSUBSCRIBED` 36.10/32 وكان 35.60 قبل الميزة: وضعٌ سابق، لا يغيّر الخروج) | ✅ |
| AC‑8.2 | وحدة | `tests/unit/test_resource_budget.py` كلّه (العدّ 29، 36.10 و57.53) | ✅ |
| AC‑8.3 | وحدة | `tests/unit/test_connection_budget.py` (164 = 162 + 2، وحارس `CONNECTION LIMIT 2` و`postgres:5432/`) | ✅ |
| AC‑8.4 | بوّابات | §2: الستّ كلّها 0 | ✅ |
| AC‑8.5 | وحدة (جديد) + مراجعة | `test_monitoring_host_postgres_acceptance.py::test_the_monitoring_plan_counts_the_real_number_of_rules` (27 = عدد القواعد الحقيقيّ، والجهاز وPostgres انتقلا من «الناقص»)؛ `::test_the_capacity_ledger_says_what_the_exporter_closed_and_left_open` (د‑12 ود‑15). `capacity-summary.html`: مراجعة يدويّة للفرق (سطر «متبقٍّ» حُدِّث؛ لا تغيّر في العدّادات لأنّ حالة الدَّين لم تتغيّر) | ✅ |
| AC‑9.1 | وحدة (جديد) + مراجعة | `::test_the_activation_steps_come_in_the_required_order`، `::test_the_activation_warns_that_a_plain_up_recreates_things`، `::test_the_activation_explains_recreate_over_reload_and_checks_postgres_is_untouched`، `::test_the_activation_prints_no_secret`. **BUG‑1 أُغلق في الدورة 1** (فحص الطول `len=` موجود في ⓪ ويرفض الفارغ و`change-me*`؛ الاختبار `::test_the_activation_checks_the_password_length_without_printing_it` يمرّ فعلاً دون `xfail`) | ✅ |
| AC‑9.2 | حيّ | `up{job=~"node\|postgres"}` سلسلتان = 1 (ح‑1) | ✅ |
| AC‑9.3 | حيّ | `Created` لـ`aizzak-postgres-1` = `2026-09-28T06:16:02Z` والمعرّف `6137b203eb18` = خطّ الأساس في `status.md` (لم يُعَد إنشاؤه). `StartedAt` = 12:07:47Z اليوم (إعادة تشغيلٍ سابقة لا إعادة إنشاء) | ✅ |
| AC‑9.4 | حيّ | عند 18:24 UTC (Prometheus يعمل منذ 18:11:53، المُصدِّران منذ 18:11:20): `ALERTS{alertname=~"AizzakScrapeTargetDown\|AizzakPostgres.*\|AizzakHost.*", alertstate="firing"}` = لا نتيجة. الاشتعالان الوحيدان في المكدّس: `AizzakWatchdog` (بالتصميم) و`AizzakDlqNotEmpty` (قديمٌ لا علاقة له بالميزة). أُعيدت المراجعة في آخر الجلسة (§3 ح‑3) | ✅ |
| AC‑9.5 | حيّ (أُكمل بعد الدورة 1) | `docker stats` عند 20:48 UTC: `node-exporter` 17.6 MiB من 64 (27.6%)؛ `postgres-exporter` 14.6 MiB من 128 (11.4%) — كلاهما < 50% بعد أكثر من ساعتين | ✅ |

**ملخّص (بعد الدورة 1):** 46 معياراً داخل النطاق: 46 ✅. يبقى معياران ينتظران طرفاً آخر: AC‑2.4/2.5 (job ‏`integration` في CI، لم يُشغَّل هنا). AC‑3.6/AC‑9.5 أُكملا (قياس الساعة).

## 2. نتائج بوابات المشروع
على `/home/AIZZAK`، الفرع `monitoring`، `PATH` يبدأ بـ`.venv/bin` (دونه يتخطّى اختبار `tests/architecture/test_import_contracts.py` لأنّ `lint-imports` غير موجود على PATH).

```
$ .venv/bin/ruff format --check .
798 files already formatted                                    EXIT 0
$ .venv/bin/ruff check .
All checks passed!                                             EXIT 0
$ .venv/bin/mypy src
Success: no issues found in 467 source files                   EXIT 0
$ .venv/bin/lint-imports
Infrastructure imported only by Composition Root KEPT
Framework does not import outer layers KEPT
Contracts: 8 kept, 0 broken.                                   EXIT 0
$ .venv/bin/pytest -rs tests/unit tests/architecture tests/eval
5102 passed, 1 xfailed, 7 warnings in 86.55s (0:01:26)         EXIT 0
$ deploy/prometheus/test-rules.sh
  SUCCESS: /etc/prometheus/prometheus.yml is valid prometheus config file syntax
Checking /etc/prometheus/alerts.yml
  SUCCESS: 27 rules found
test-rules: OK                                                 EXIT 0
```

- الـ`xfailed` الوحيد هو BUG‑1 (صارم: يصير فشلاً إن أُصلح التوثيق دون إزالة العلامة).
- **لم يُشغَّل:** `tests/integration` (ممنوع دون إذن؛ فيه `test_metrics_exporter_role_live.py` ويشغّله CI).
- لا اختبارٌ حُذف ولا أُضعف ولا أُضيف له فلتر. لا تخطّيات في البوّابة بعد إضافة `.venv/bin` إلى PATH.

## 3. الاختبار الاستكشافي

الفحوص الحيّة على الحزمة المحلّيّة (بيئة تطوير)، بالتوقيت UTC. الاستعلامات عبر `docker compose exec -T prometheus wget …/api/v1/query`.

| # | السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|---|
| ح‑1 | الكشط يعمل (AC‑1.4، 3.3، 9.2) | `up{job=~"node\|postgres"}`، `pg_up` | 1 للثلاثة | 1 · 1 · 1 | ✅ |
| ح‑2 | إيقاف المُصدِّر (AC‑6.4) | 18:18:17 `docker compose stop postgres-exporter`؛ استطلاع كلّ 5 ث؛ ثمّ 18:20:10 `up -d --no-deps postgres-exporter` | pending ثمّ firing (`for: 30s`) ثمّ resolved | `up=0` عند +14 ث؛ pending عند +31 ث؛ firing عند +61 ث؛ sink: firing `starts_at` 18:19:06.935Z ثمّ resolved `ends_at` 18:20:21.935Z؛ بعد البدء `up=1` عند +12 ث وانطفأ التنبيه عند +24 ث. الحزمة بعده: كلّ الخدمات `healthy` | ✅ |
| ح‑3 | حالة الاشتعال بعد 10 دقائق (AC‑9.4) | `ALERTS{…firing}` للأسماء الأربعة + `AizzakScrapeTargetDown` | لا نتيجة | لا نتيجة (18:24)؛ وأُعيد في الختام | ✅ |
| ح‑4 | قياسات AC‑3.6/9.5 الجزئيّة | `scrape_samples_scraped` و`scrape_duration_seconds` و`docker stats` | < 3000، < 2 ث، < 50% | 1535، 0.198 ث، 26% و10%؛ و`node` 357 عيّنة | ✅ (الساعة للبشري) |
| ح‑5 | عنقودٌ هادئ بلا `.ready` | promtool مؤقّت (خارج المستودع): `oldest_age=0`، `segments=0`، `last_archive_age=999999` لـ45 دقيقة | لا اشتعال | لا اشتعال | ✅ |
| ح‑6 | دورٌ بلا امتياز زائد (حيّ، كتالوج فقط) | `pg_roles` و`pg_auth_members` و`has_table_privilege`/`role_table_grants` بالمستخدم المالك داخل الحاوية | الصفات `f`، عضويّة واحدة، لا SELECT/INSERT | `f|f|f|f` (+`createdb=f`، `replication=f`)، `pg_monitor` وحدها `inherit=t`، 0 منح جداول، SELECT على `workspace.users` = f و INSERT على `platform.outbox` = f، `rolconnlimit=2`، `rolinherit=f` (مقصود، والتوريث على العضويّة نفسها)، جلسة حيّة واحدة | ✅ |
| ح‑7 | سلاسل ناقصة | `pg_up` فقط بلا سلاسل الأرشفة/القفل/القرص (promtool) | لا اشتعال كاذب | صامتة الثلاث | ✅ (انظر الملاحظة 2) |
| ح‑8 | حدودٌ مضبوطة | الأرشفة 900 ثانية، القفل 60، القرص 80.0% بالضبط ثمّ 901/61/80.1% | الأولى صامتة، والثانية تشتعل بالتسميات الصحيحة | كما توقّعنا (`critical` و`warning` و`warning`+`mountpoint="/"`) | ✅ |
| ح‑9 | نقاط تركيب لا يجب أن تُرى | tmpfs على `/run`، 9p على `/mnt/c`، ext4 على `/var/lib/docker`، كلّها 99% | صامت (المراقَب `/` وحده) | صامت | ✅ |
| ح‑10 | `pg_up=0` من وظيفةٍ أخرى | سلسلة `pg_up{job="other"}` = 0 | لا `AizzakPostgresDown` | صامت | ✅ |
| ح‑11 | المنفذ العامّ وسرّ الوصول | `docker compose config`: `expose` فقط للمُصدِّرَين؛ بيئة `postgres` لا تحوي `METRICS_EXPORTER_PASSWORD` | لا منافذ، ولا سرّ في `postgres` | كما توقّعنا (الاسم الوحيد المطابق `METRICS_READER_PASSWORD` القائم) | ✅ |
| ح‑12 | سرّ الإجراء | قراءة §3.3‑ج: كلمة السرّ عبر stdin، لا `echo` | لا طباعة | لا طباعة (محروس باختبار) | ✅ |
| ح‑13 | إعادة تنفيذ سكربت الدور على العنقود الحيّ | لم يُنفَّذ (DDL، خارج حدود المهمّة) | — | غير مُختبَر حيّاً؛ يغطّيه الاختبار الساكن وإجراء US‑9 | ⚠️ |
| ح‑14 | كلمة سرّ مفقودة | `docker compose --env-file <بدون المتغيّر> config` | فشل باسم المتغيّر | `required variable METRICS_EXPORTER_PASSWORD is missing a value`, exit 1 | ✅ |

لا ينطبق هنا: RTL/375px (لا واجهة)، ومدخلات المستخدم (لا API جديدة)، ومستأجرون متعدّدون (المُصدِّر مقصودٌ ألّا يقرأ جداول المستأجرين: ح‑6).

## 4. الأخطاء المكتشفة

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| BUG‑1 | **إجراء التفعيل (§3.3‑ج) ينقصه فحص طول كلمة السرّ** (AC‑9.1 البند ⓪، وتصميمه ⓪‑ب `len=32`). الخطوة ⓪ تلحق قيمةً عشوائيّة فقط إن غاب الاسم (`grep -q`)، فإن كان في `.env` قيمة `change-me-*` منسوخة من `.env.example` (والـ`:?` لا يرفضها: `08-local-runbook.md:665`) فتمرّ بصمت ويصير للدور كلمة سرّ معروفة. | minor (توثيقيّ؛ الحزمة المحلّيّة منشورةٌ بقيمةٍ عشوائيّة بحسب `status.md`، والمخاطرة على من ينفّذ الإجراء لاحقاً) | `sed -n '/### 3.3‑ج/,/^### /p' docs/design/08-local-runbook.md` وابحث عن فحص طول (`${#…}` أو `wc -c` أو `len=`): لا شيء. أو `pytest tests/unit/test_monitoring_host_postgres_acceptance.py::test_the_activation_checks_the_password_length_without_printing_it` (xfail صارم). **المتوقَّع:** سطرٌ بعد ⓪ يطبع الطول فقط (على نمط `08-local-runbook.md:665-675`) ويفشل إن < 24 أو بدأ بـ`change-me`. **الفعلي:** غائب. الملف: `docs/design/08-local-runbook.md` (قسم §3.3‑ج، كتلة ⓪). **المالك: backend (توثيق)**. بعد الإصلاح تُزال علامة `xfail` من الاختبار | **مُغلق** (الدورة 1، commit `22442b2`؛ انظر §6) |

**ملاحظات (ليست أخطاء، للعلم):**
1. **AC‑2.6:** لا اختبار تكاملٍ ينفّذ سكربت الدور مرّتين؛ يقتصر الأمر على الاختبار الساكن وإجراء US‑9. يُنصح (غير حاجب) بإضافة حالةٍ إلى `test_metrics_exporter_role_live.py` تشغّل السكربت ثانيةً وتقارن `pg_roles`.
2. **عمىً صامت:** إن فشل أحد استعلامَي `queries.yaml` (مثلاً فقد الدور صلاحية `pg_ls_archive_statusdir`) فتغيب سلسلة `pg_archive_ready_*` أو `pg_lock_wait_*` وتصمت قاعدتاهما، بينما يبقى `pg_up=1`. ما من قاعدةٍ على غياب السلسلة. التغطية الحاليّة: اختبار CI‑التكامل `test_every_custom_exporter_query_runs_under_the_role` يحرس ذلك عند البناء فقط. خطرٌ مقبول لهذه المرحلة؛ يُقترح `absent(…)` في المرحلة 4.
3. **AC‑6.4:** الاشتعال بعد 61 ث من الاستطلاع المحلّيّ (الكشط 15 ث + `for: 30s` + دورة التقييم 15 ث)؛ وصل `starts_at` إلى الـsink بعد 49 ث من الإيقاف. المعيار «≤ 60 ثانية» محقَّقٌ بحساب `starts_at` لا بوصول السطر، فهو حدّيّ. لا تغيير مطلوب.
4. **الحزمة:** إعادة إنشاء الحاويتين وPrometheus جرت 18:11 UTC (غير جلستي)، فقياسات الساعة تبدأ من 18:11، لا من التفعيل الأوّل (~12:00).
5. **د‑25/CPU:** `OVERSUBSCRIBED 36.10/32` في `resource-budget.sh` (كان 35.60)؛ سابقٌ للميزة ولا يغيّر خروج 0.

## 5. الحكم
**PASS** (بعد الدورة 1) — البوّابات الستّ خضراء، لا أخطاء مفتوحة، BUG‑1 مُغلق وM‑1/L‑1 مُتحقَّق منهما على حاوية مؤقّتة. ما يبقى لغير QA: نتيجة job ‏`integration` في CI (AC‑2.4/2.5)، والتنظيف الحيّ لمرّة واحدة (إجراء بشريّ حصراً، `08 §3.3‑ج`) لإزالة أي أثر قديم في `pg_stat_statements` للحزمة الحيّة.


## 6. إعادة بعد الدورة 1 (commits ‏`22442b2` و`97e4b12`)

**البوّابات (على شجرة فيها تعديلات الجلسة الأخرى غير المُودَعة، لم تُلمس):**

| البوّابة | النتيجة | EXIT |
|---|---|---|
| `ruff format --check .` | 798 files already formatted | 0 |
| `ruff check .` | All checks passed! | 0 |
| `mypy src` | Success: no issues found in 467 source files | 0 |
| `lint-imports` | Contracts: 8 kept, 0 broken | 0 |
| `pytest -rs tests/unit tests/architecture tests/eval` | 5107 passed, 7 warnings in 90.71s (لا xfail ولا skip) | 0 |
| `deploy/prometheus/test-rules.sh` | 27 rules found، `test-rules: OK` | 0 |

لم يُشغَّل `tests/integration` (ممنوع دون إذن)، ولم يُقرأ `.env`.

**1) BUG‑1 مُغلق.**
- `test_the_activation_checks_the_password_length_without_printing_it` يمرّ فعلاً (لا `xfail`).
- السكربت المُودَع (`git show HEAD:deploy/postgres/initdb/15-metrics-exporter.sh`) يرفض الفارغ و`change-me*` بخروج 1 ما لم يكن `METRICS_EXPORTER_ALLOW_PLACEHOLDER=1`؛ وهذا المتغيّر لا يرد إلا في `ci.yml` والسكربت واختبار الوحدة (`grep` على الشجرة).
- فحص الطول `len=${#l}` في ⓪ من `08 §3.3‑ج`، وحلقتا الأسرار في `08` وquickstart تضمّان `METRICS_EXPORTER_PASSWORD`.
- على الحاوية المؤقّتة: الفارغ → `REFUSED ... empty` خروج 1؛ `change-me-abc` → `REFUSED: placeholder` خروج 1؛ مع `ALLOW_PLACEHOLDER=1` → تحذير وخروج 0.

**2) M‑1/L‑1 على `postgres:16` مؤقّتة** (شبكة مؤقّتة، `shared_preload_libraries=pg_stat_statements`، `track_utility=on`، `track=all`؛ أُزيلت الحاوية والشبكة بعدها، والتحقّق: 0 متبقٍّ):
- شُغّل السكربت المُودَع مرّتين بكلمة سرّ عشوائية (48 hex) بطريقة stdin: الخروج 0 في المرّتين (متقاربٌ ومتكرّر).
- عدد صفوف `pg_stat_statements` التي تحوي القيمة = **0**. الشاهد: `ALTER ROLE ... PASSWORD 'qa-control-literal'` بجلسة psql عاديّة → **1** (فالقياس صالح ويكشف التسرّب).
- سجلّ الحاوية لا يحوي القيمة (0 تطابق). والدخول بالقيمة عبر TCP نجح (الكلمة فعلاً مضبوطة).
- `ps`: السكربت لا يستعمل `--set`/`-v` بالقيمة؛ الوحيد `--set db_name=` (اسم قاعدة، لا سرّ) ويقرأ القيمة بـ`\getenv` فلا تظهر في argv.
- لم يُشغَّل شيء على `postgres` الحيّ.

**3) الحزمة الحيّة عند 20:48 UTC:** كلّ الخدمات `healthy`؛ `up{job=~"node|postgres"}` = 1 للاثنين. قياسات الساعة في AC‑3.6/AC‑9.5 أعلاه.

**الأخطاء المفتوحة:** لا شيء. لم تُضَف اختبارات جديدة (لا فجوة).

</div>
