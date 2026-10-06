<div dir="rtl">

# قبول مالك المنتج: مراقبة الجهاز وPostgres (خطّة المراقبة، المرحلة 2، الصفّان 1 و2)

- **الفرع:** `monitoring`، الرأس `e1c2868` (13 commit فوق `master`). **التاريخ:** 2026‑10‑06.
- **النطاق المقبول عند البوابة ①:** US‑1…US‑6 وUS‑8 وUS‑9، وكلّها **Must**. ‏**US‑7** (لوحتا Grafana، Could) أجّلها المالك إلى المرحلة 4، فلا تدخل هذا القبول، ومعاييرها AC‑7.1…AC‑7.4 خارج العدّ.
- **ما قرأته:** `requirements.md`، `stories.md`، `design.md` (ومعه تعديل §4 اللاحق للمراجعة)، `test-plan.md` (PASS بعد الدورة 1)، `review.md` (PASS بعد الدورة 1)، `security-review.md` (PASS بعد الدورة 1)، `status.md`.
- **ما تحقّقتُ منه بنفسي (قراءة فقط، 2026‑10‑06 نحو 22:03 UTC):**
  - `pytest` على ستّة ملفّات الميزة: `test_metrics_exporter_role.py` و`test_monitoring_host_postgres_acceptance.py` و`test_observability_stack_wiring.py` و`test_prometheus_alert_rules.py` و`test_resource_budget.py` و`test_connection_budget.py`. النتيجة: 114 اختباراً جُمع، و114 نجح، والخروج 0.
  - `bash -n deploy/postgres/initdb/15-metrics-exporter.sh` ← سليم نحويّاً.
  - Prometheus الحيّ:
    - `up{job=~"node|postgres"}` ← سلسلتان، قيمة كلٍّ منهما 1. و`pg_up` = 1.
    - الاشتعال الحاليّ: `AizzakWatchdog` (موجودٌ بالتصميم) و`AizzakDlqNotEmpty` (قديم، لا علاقة له بالميزة)، ولا شيء من تنبيهات الميزة.
    - لا سلسلة بتسمية `queryid` في وظيفة `postgres`.
    - `max_over_time(scrape_samples_scraped[1h])` = 1535 لـpostgres و357 لـnode.
  - `docker inspect aizzak-postgres-1`: المعرّف `6137b203eb18`، وتاريخ الإنشاء `Created` = `2026-09-28T06:16:02Z`، فلم يُعَد إنشاؤه. وحالته `healthy`.
  - `docker stats`: ‏`node-exporter` يستعمل 17.4 MiB من 64 (27.2%)، و`postgres-exporter` يستعمل 14.9 MiB من 128 (11.7%).
  - `git show a1c9571 e1c2868`: الدورة 2 (راجع §«الدورة 2 دون إعادة مراجعة» أدناه).
- **بوّابات المنسّق (2026‑10‑06، مسجّلة في `status.md`):** ruff format وruff check نظيفان، mypy نظيف، lint-imports حفظ العقود الثمانية، `pytest tests/unit tests/architecture tests/eval` = 5115 passed، `test-rules.sh` = OK، و`compose config` خرج 0.

## مصفوفة القبول

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑1.1 | `test_monitoring_host_postgres_acceptance.py::test_compose_renders_clean_from_the_example_env` و`::test_compose_renders_both_exporters_as_standing_services_with_limits`، و`test_observability_stack_wiring.py::test_the_scraper_and_exporters_publish_no_host_port`، و`test_metrics_exporter_role.py::test_both_new_images_are_pinned_to_a_version`. أعدتُ تشغيلها، فنجحت | ✅ مقبول |
| AC‑1.2 | `test-rules.sh` (`promtool check config` SUCCESS، 27 قاعدة)، و`test_observability_stack_wiring.py::test_every_scrape_target_names_a_service_that_exists` و`::test_the_host_and_postgres_jobs_scrape_their_exporters` | ✅ مقبول |
| AC‑1.3 | `test_observability_stack_wiring.py::test_the_host_exporter_reads_no_host_filesystem`، واختبار QA: لا `docker.sock`، والتركيبات للقراءة فقط، والمستخدم `65534:65534` | ✅ مقبول |
| AC‑1.4 | حيّ: `up{job="node"}` = 1 (test-plan ح‑1، وأعدتُ الاستعلام اليوم) | ✅ مقبول |
| AC‑1.5 | حيّ: `node_memory_MemTotal_bytes` = 13,599,727,616، ويطابق `/proc/meminfo` تماماً. وعدد المعالجات 10 = `nproc` (test-plan AC‑1.5) | ✅ مقبول |
| AC‑1.6 | حيّ: سلسلة `node_filesystem_size_bytes` واحدة، `mountpoint="/"` و`ext4`. لا tmpfs ولا overlay ولا 9p | ✅ مقبول |
| AC‑2.1 | `test_metrics_exporter_role.py::test_the_role_is_created_inside_an_existence_check` و`::test_the_password_is_a_psql_variable_never_shell_interpolated` و`::test_pg_monitor_with_inherit_is_the_only_grant`. الموضع `15-metrics-exporter.sh` بدل `10-roles.sh`، وهذا قرار التصميم ق‑2 | ✅ مقبول |
| AC‑2.2 | `::test_the_exporter_alone_requires_the_password` و`::test_the_password_never_enters_the_postgres_service` و`::test_the_role_script_sorts_between_the_roles_and_the_extensions`، وسطر `change-me-*` في القالبين | ✅ مقبول |
| AC‑2.3 | `test_monitoring_host_postgres_acceptance.py::test_compose_refuses_to_render_without_the_exporter_password`. ويدويّاً: `required variable METRICS_EXPORTER_PASSWORD is missing a value`، والخروج 1 (ح‑14) | ✅ مقبول |
| AC‑2.4 | حيّ، فحص كتالوج للقراءة فقط (ح‑6): الصفات `f\|f\|f\|f`، و`pg_monitor` بـ`inherit=t` وحدها، و`has_table_privilege` على `workspace.users` SELECT = f وعلى `platform.outbox` INSERT = f، و0 منح جداول. وأنّ الدور يرى الجلسات الأخرى تشهد عليه السلاسل الحيّة `pg_locks_count` و`pg_stat_activity_max_tx_duration`. **أمّا التنفيذ الفعليّ** في `tests/integration/test_metrics_exporter_role_live.py` (أربعة اختبارات) فلم يجرِ بعد، وتجريه job ‏`integration` في CI. والمثبِّت صار يفشل تحت `REQUIRE_LIVE=1` (ملاحظة المراجعة 3، أُغلقت) | ✅ مقبول **بشرط CI** |
| AC‑2.5 | حيّ: `rolconfig={statement_timeout=5s,lock_timeout=1s,default_transaction_read_only=on}` و`rolconnlimit=2`. والاختبار `test_the_role_is_bounded_by_its_own_settings` تشغّله CI | ✅ مقبول **بشرط CI** |
| AC‑2.6 | `::test_every_attribute_is_reasserted_so_a_rerun_converges`. وشغّلت QA السكربت مرّتين على `postgres:16` مؤقّتة، فخرج 0 في المرّتين (test-plan §6). لا اختبار تكاملٍ يعيد التشغيل، وقبلتُ ذلك بالاسم أدناه | ✅ مقبول |
| AC‑3.1 | `::test_the_exporter_connects_directly_as_its_own_role` و`::test_the_exporter_alone_requires_the_password`، واختبار QA: `service_healthy`، ولا أسرار أخرى، و`expose` بلا `ports`، والاتّصال بـ`postgres:5432/` | ✅ مقبول |
| AC‑3.2 | `tests/unit/test_observability_stack_wiring.py` كلّه نجح، وأعدتُ تشغيله | ✅ مقبول |
| AC‑3.3 | حيّ: `up{job="postgres"}` = 1 و`pg_up` = 1، وأعدتُ الاستعلام اليوم | ✅ مقبول |
| AC‑3.4 | حيّ: السلاسل التسع موجودة، ومنها `pg_locks_count` (45 سلسلة) و`pg_stat_user_tables_table_size_bytes` (43 جدولاً) و`pg_archive_ready_*` و`pg_stat_archiver_last_archive_age` | ✅ مقبول |
| AC‑3.5 | حيّ: جلسات الدور 1، والحدّ ≤ 2. والسقف `CONNECTION LIMIT 2` من الخادم | ✅ مقبول |
| AC‑3.6 | حيّ، قياس ساعةٍ كاملة: العيّنات 1535 و357 (< 3000)، وP95 لزمن الكشط 0.034 ث و0.012 ث (< 2)، ولا `queryid`. وأعدتُ قياس العيّنات ونفي `queryid` اليوم | ✅ مقبول |
| AC‑3.7 | `test_metrics_exporter_role.py::test_no_per_query_series_can_be_exported` | ✅ مقبول |
| AC‑4.1 | promtool `alerts.test.yml`: «the root filesystem at 85% past ten minutes fires once…» (صامت عند 9m، ويشتعل عند 11m) | ✅ مقبول |
| AC‑4.2 | promtool: «79 percent, the Windows drive, an overlay and a zero-size mount are silent»، وحالة 9m، وفحوص QA الاستكشافيّة ح‑9 | ✅ مقبول |
| AC‑4.3 | promtool: حالة الحجم 0 صامتة | ✅ مقبول |
| AC‑4.4 | الاختبارات الأربعة المسمّاة في `test_prometheus_alert_rules.py`، وأعدتُ تشغيلها | ✅ مقبول |
| AC‑4.5 | `::test_each_new_runbook_section_is_complete[AizzakHostDiskHigh]` و`::test_the_disk_runbook_forbids_the_three_data_destroying_commands` و`::test_the_disk_runbook_admits_the_windows_drive_is_not_watched` | ✅ مقبول |
| AC‑5.1 | promtool: «a dead server behind a live exporter fires within the minute» و«a server that answers is silent» | ✅ مقبول |
| AC‑5.2 | promtool: «a dead exporter is a scrape failure, not a dead server»، و`::test_the_postgres_liveness_rule_reads_the_exporters_verdict` | ✅ مقبول |
| AC‑5.3 | promtool: «a lock wait past a minute that persists fires» و«a 50-second wait and a one-sample 70 are silent». واستكشافيّاً: الحدّ 60 صامت و61 يشتعل (ح‑8) | ✅ مقبول |
| AC‑5.4 | promtool: «a segment stuck past fifteen minutes fires…» و«quiet and healthy clusters are silent…» (ومعها ح‑5) | ✅ مقبول |
| AC‑5.5 | `::test_the_archive_rule_reads_progress_not_the_failure_counter` | ✅ مقبول |
| AC‑5.6 | `::test_the_file_declares_exactly_the_expected_alerts` و`::test_the_postgres_liveness_rule_reads_the_exporters_verdict` و`::test_the_lock_rule_thresholds_one_minute_of_waiting` و`::test_the_disk_rule_watches_root_only` | ✅ مقبول |
| AC‑5.7 | `::test_each_new_runbook_section_is_complete[…]` للتنبيهات الثلاثة، و`::test_no_new_runbook_step_restarts_postgres_without_saying_it_is_human[…]` | ✅ مقبول |
| AC‑5.8 | `::test_the_backup_age_question_points_at_the_ops_task_rule` | ✅ مقبول |
| AC‑6.1 | promtool: «a dead node exporter fires, a 15-second blip on the postgres one does not» | ✅ مقبول |
| AC‑6.2 | الحالة نفسها: ومضة 15 ثانية لا تشتعل | ✅ مقبول |
| AC‑6.3 | `test_observability_stack_wiring.py::test_the_optional_target_is_the_one_behind_a_compose_profile` | ✅ مقبول |
| AC‑6.4 | حيّ (ح‑2): أُوقف المُصدِّر، فوصل إلى `alert-sink` سطر `firing` بـ`starts_at` بعد 49 ث (≤ 60). وبعد إعادة التشغيل وصل `resolved` | ✅ مقبول (حدّيّ، انظر الملاحظات) |
| AC‑8.1 | `resource-budget.sh --host-cpus 32 --host-memory-gb 64` خرج 0، و`MEMORY OK 57.53 of 64.00`. المُصدِّران 0.25 vCPU، و0.06 GB و0.12 GB | ✅ مقبول |
| AC‑8.2 | `tests/unit/test_resource_budget.py` (العدّ 29، و36.10، و57.53)، وأعدتُ تشغيله | ✅ مقبول |
| AC‑8.3 | `tests/unit/test_connection_budget.py` (164 = 162 + 2)، وأعدتُ تشغيله | ✅ مقبول |
| AC‑8.4 | البوّابات الستّ خرجت 0 عند QA (5107) وعند المنسّق بعد الدورة 2 (5115) | ✅ مقبول |
| AC‑8.5 | `::test_the_monitoring_plan_counts_the_real_number_of_rules` (27) و`::test_the_capacity_ledger_says_what_the_exporter_closed_and_left_open` (د‑12 ود‑15). و`capacity-summary.html` لم تتغيّر عدّاداته لأنّ حالة الدَّين لم تتغيّر. وفي `monitoring-plan.md:18` العدد 27 | ✅ مقبول |
| AC‑9.1 | `::test_the_activation_steps_come_in_the_required_order` و`::test_the_activation_warns_that_a_plain_up_recreates_things` و`::test_the_activation_explains_recreate_over_reload_and_checks_postgres_is_untouched` و`::test_the_activation_prints_no_secret` و`::test_the_activation_checks_the_password_length_without_printing_it`. أُغلق BUG‑1، والخطوة ⓪ في `08:861` تقف على الفارغ و`change-me*` (بأيّ حالة أحرف) وعلى ما هو أقصر من 32. الخطوة ① تشغّل السكربت بدل نسخ SQL، وهذا بقرار التصميم ق‑2 | ✅ مقبول |
| AC‑9.2 | حيّ: `up{job=~"node\|postgres"}` سلسلتان = 1، وأعدتُ الاستعلام اليوم | ✅ مقبول |
| AC‑9.3 | حيّ: `Created` = `2026-09-28T06:16:02Z` والمعرّف `6137b203eb18` نفسه، وأعدتُ الفحص اليوم | ✅ مقبول |
| AC‑9.4 | حيّ: لا اشتعال لتنبيهات الميزة بعد 10 دقائق (ح‑3). واليوم لا يشتعل إلّا `AizzakWatchdog` و`AizzakDlqNotEmpty` القديم | ✅ مقبول |
| AC‑9.5 | حيّ: النسب 27.6% و11.4% بعد أكثر من ساعتين، واليوم 27.2% و11.7%، وكلّها < 50% | ✅ مقبول |

**الملخّص:** 46 معياراً في النطاق، كلّها Must. النتيجة 46 ✅ و0 🔴.

## الدورة 2 دون إعادة مراجعة (`a1c9571`، `e1c2868`): مقبولة

1. كلّ تغييرٍ فيها يعالج ملاحظةً مسمّاة، ولم يُضَف شيءٌ خارجها:
   - L‑4: سطور `SET` إضافيّة للسجلّ.
   - I‑7: رفض `change-me*` بأيّ حالة أحرف، وحدٌّ أدنى للطول 32.
   - R1‑3: الرفض قبل أوّل `psql`.
   - R1‑1: تعديل design.md §4 بعد المراجعة، وهو معلَّمٌ بذلك صراحةً في `design.md:461`.
   - R1‑2 وR1‑4 وI‑6: نصوص.
2. الفرق صغير: 98+ و25− في السكربت والاختبارات، والباقي وثائق. وهو يقوّي ولا يغيّر السلوك المقبول.
3. مسار الرفض **يُنفَّذ فعلاً**: `test_weak_passwords_are_refused_before_psql_and_never_printed` و`test_a_strong_password_passes_the_gate_and_ci_opt_in_still_works` يشغّلان السكربت الحقيقيّ مع `psql` وهميّ. وقد نجحا عندي.
4. **ما بقي غير منفَّذ:** سطور `SET` الخمسة الجديدة لم تجرِ بعدُ على Postgres 16 حقيقيّ. أوّل تنفيذٍ لها سيكون في job ‏`integration` في CI، فـCI تشغّل السكربت مع `ON_ERROR_STOP=1`، وأيّ إعدادٍ غير صالح يُفشلها. لذلك يدخل هذا في شرط CI أدناه.
5. السكربت لن يُنفَّذ على العنقود الحيّ إلّا في تنظيف المالك (08 §3.3‑ج)، وهذا يجري بعد CI.

## ما يبقى معلَّقاً على أحداثٍ بعد هذه المرحلة (شروط القبول)

1. **job ‏`integration` في CI عند فتح الـPR (شرطٌ قبل الدمج).** يعتمد عليها:
   - AC‑2.4 وAC‑2.5: الاختبارات الأربعة واختبار `rolconfig` في `test_metrics_exporter_role_live.py`، ومعها `test_every_custom_exporter_query_runs_under_the_role`.
   - أوّل تنفيذٍ حقيقيّ لسكربت الدورة 2 (سطور `SET` الجديدة).

   إن فشلت الـjob، يسقط القبول لـAC‑2.4 وAC‑2.5، ويعود العمل إلى **المرحلة 6 (الخلفية)**.
2. **تنظيف المالك الحيّ لمرّة واحدة وتدوير كلمة السرّ (08 §3.3‑ج، «تنظيفٌ لمرّةٍ واحدة»).**
   - أُغلقت M‑1 في الشيفرة، لكنّها **ليست مغلقةً على الحزمة الحيّة**. قاست المراجعة الأمنيّة صفّاً مسرِّباً واحداً (`leaking_rows_alter_metrics_exporter = 1`) في `pg_stat_statements` الحيّة، فقيمة كلمة السرّ الحاليّة تُعدّ مكشوفة.
   - الخطوة (ب)، أي التدوير، **إلزاميّة**: بعد التصفير يبقى النصّ يتيماً على القرص حتّى إعادة تشغيل postgres (I‑6).
   - هذا لا يمسّ أيّ معيار قبول. لكنّي أطلبه **بعد نجاح CI وقبل الدمج أو مع الدمج**، وفي كلّ حال قبل أيّ تعريضٍ للحزمة خارج المضيف.
3. **تأكيد المالك عند البوابة ② لتغيير سياسة `CLAUDE.md` (commit `09aa3f0`)**، وهو أنّ للوكلاء أن يعيدوا إنشاء حاويات الحزمة المحلّيّة وتشغيلها.
   - دوّن القرارَ وكيلٌ في `status.md`، وليس عندي دليلٌ مباشر من البشري (ملاحظة المراجعة 5).
   - هذا التغيير ليس جزءاً من أيّ معيار قبول. لكنّ دليل AC‑6.4 وAC‑9.2…9.5 جُمع بموجبه، بإيقاف `postgres-exporter` وإعادة إنشاء المُصدِّرين وPrometheus.
   - إن لم يؤكّده المالك، **يُفصل hunk `CLAUDE.md` من الفرع** (يُعاد أو يُنقل إلى PR مستقلّ) قبل الدمج. لا تُرفض الأدلّة الحيّة بأثرٍ رجعيّ، لأنّه لم يُعَد إنشاء `postgres` (AC‑9.3)، ولم تُمسّ الأحجام.

## ملاحظات غير حاجبة مقبولة
- **AC‑2.6 بلا اختبار تكاملٍ يعيد التشغيل** (QA الملاحظة 1). أقبل الدليل الساكن مع التشغيل المزدوج على الحاوية المؤقّتة. ويُقترح إضافة حالةٍ في `test_metrics_exporter_role_live.py` لاحقاً.
- **عمىً صامت إن غابت سلسلة مخصّصة** (QA الملاحظة 2). إن فشل استعلامٌ في `queries.yaml` تصمت قاعدته و`pg_up=1`. أقبله لهذه المرحلة، ويُضاف `absent(…)` في المرحلة 4.
- **AC‑6.4 حدّيّ** (QA الملاحظة 3). وصل `starts_at` بعد 49 ث، لكنّ السطر نفسه وصل قرب 61 ث. أقبل القياس بـ`starts_at` كما ينصّ عليه المعيار.
- **`OVERSUBSCRIBED 36.10/32` في CPU** (QA الملاحظة 5 ود‑25). الحالة سابقةٌ للميزة (35.60 قبلها)، ولا تغيّر خروج 0.
- **L‑2** (`DATA_SOURCE_PASS` متغيّر بيئة): هو نمط كلّ DSN في المستودع، ويُعالَج لاحقاً بـ`_FILE`.
- **L‑3** (`.env` بوضع `0644`): قائمٌ قبل الميزة. أوصي المالك بـ`chmod 600`.
- **I‑2** (`pg_monitor` يكشف للدور نصّ استعلامات الجلسات): متبقٍّ مقبول. لا يُصدَّر النصّ تسميةً (AC‑3.7)، والدور على الشبكة الداخليّة فقط.
- **I‑6**: عولج بالوثيقة، فصارت (ب) إلزاميّة. وما يبقى منه داخلٌ في الشرط 2.
- **R1‑1 وR1‑2 وR1‑3 وR1‑4 وL‑4 وI‑7**: أُغلقت في الدورة 2 دون إعادة مراجعة (انظر القسم أعلاه).
- **`AizzakDlqNotEmpty` المشتعل**: قديمٌ ولا علاقة له بالميزة، ولا يمسّ AC‑9.4.
- **تعديلات شجرة العمل غير المُودَعة** (`QDRANT_RETENTION` وغيرها، من جلسةٍ أخرى): ليست من هذه الميزة ولا من هذا القبول، ويجب ألّا تدخل الـPR.

## الحكم النهائي
**مقبول** (بشروط). معايير Must الستّة والأربعون في النطاق كلّها ✅، والمراجعتان (الشيفرة والأمن) PASS بلا ملاحظةٍ حاجبة مفتوحة. والقبول مشروطٌ بثلاثة أمور:
1. نجاح job ‏`integration` في CI على الـPR. وإلّا عاد AC‑2.4 وAC‑2.5 إلى 🔴، ورجع العمل إلى **المرحلة 6**.
2. تنفيذ المالك التنظيفَ الحيّ وتدويرَ كلمة السرّ (08 §3.3‑ج) حتّى تُغلق M‑1 على الحزمة الحيّة.
3. تأكيد المالك صراحةً عند البوابة ② لتغيير `CLAUDE.md` في `09aa3f0`، أو فصله عن الفرع.

US‑7 خارج هذا القبول، ومؤجّلة إلى المرحلة 4.

</div>
