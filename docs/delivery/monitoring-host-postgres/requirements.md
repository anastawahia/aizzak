<div dir="rtl">

# المتطلبات: مراقبة الجهاز وPostgres (خطّة المراقبة، المرحلة 2، الصفّان 1 و2)

> **المعرّف:** `monitoring-host-postgres` · **الفرع:** `monitoring` · **التاريخ:** 2026‑10‑05
> **المصدر:** [`docs/monitoring-plan.md`](../../monitoring-plan.md) §1 «المرحلة 2 — سدّ فجوات التغطية»، الصفّان 1 و2 فقط (`docs/monitoring-plan.md:56-57`).
> **قرارات المالك (2026‑10‑05):** النطاق هو الجهاز وPostgres فقط. تعديل `deploy/` و`docker-compose.yml` و`deploy/postgres/initdb/10-roles.sh` مسموحٌ **على الفرع `monitoring` وحده**. إعادة إنشاء الحزمة الحيّة أو إعادة تشغيلها **بيد البشري وحده**.

## 1. الخلفية والهدف

**المشكلة:** Prometheus اليوم يقرأ من 9 وظائف (`deploy/prometheus/prometheus.yml:46-195`)، وليس بينها الجهاز ولا Postgres نفسه. لذلك تبقى ثلاثة أعطالٍ صامتة:

1. **امتلاء القرص.** لا رقم له في Prometheus. وخطّة المراقبة تعدّه، مع قاعدة البيانات، «أكثر أسباب السقوط» (`docs/monitoring-plan.md:183`). ومسار WAL يملأ القرص حين يتوقّف الشحن (`src/app/ops/backup.py:108-118`).
2. **Postgres متوقّف وPgBouncer حيّ.** `AizzakPgbouncerDown` يقرأ `pgbouncer_up` (`deploy/prometheus/alerts.yml:356-358`)، وهذا رقمٌ عن المُجمِّع لا عن الخادم خلفه.
3. **أرشفة WAL وانتفاخ الجداول بلا مقياس.** هذان دَينان مسجَّلان ينتظران `postgres_exporter`: `د‑12` (`docs/capacity-status.md:3012`) و`د‑15` (`docs/capacity-status.md:3023`).

**الهدف في سطر:** أن يقرأ Prometheus أرقام الجهاز وأرقام Postgres من مُصدِّرَين جديدين، وأن يشتعل تنبيهٌ مختبَرٌ وله إجراءٌ مكتوب عند امتلاء القرص وعند تعطّل Postgres أو تعطّل أرشفته، وأن يغطّي `AizzakScrapeTargetDown` المُصدِّرَين.

**المستفيد:** مشغّل المنصّة (المالك). هو يقرأ التنبيهات من `alert-sink` في Loki (`docs/runbooks/alerts.md:13-24`).

## 2. النطاق

**داخل النطاق:**
- خدمة `node-exporter` في `docker-compose.yml` لها سقف موارد، ووظيفة كشطٍ لها في `prometheus.yml`.
- خدمة `postgres-exporter` جانبيّة تتّصل **بدورٍ جديدٍ للقراءة فقط**، لا بـ`app_rw` ولا بالمستخدم الخارق. يُنشأ الدور في `deploy/postgres/initdb/10-roles.sh`، ويُضاف اسم كلمة مروره إلى `.env.example` و`.env.test.example`. للخدمة سقف موارد ووظيفة كشط.
- قواعد تنبيهٍ جديدة في `deploy/prometheus/alerts.yml`. لكلّ قاعدة قسمٌ في `docs/runbooks/alerts.md` وحالتا promtool في `deploy/prometheus/alerts.test.yml` (حالةٌ تُشعلها وحالةٌ تُبقيها صامتة). القواعد هي:
  - الجهاز: القرص فوق 80%.
  - Postgres: الخادم لا يردّ، وقفلٌ ينتظر أكثر من دقيقة، والأرشفة متوقّفة.
- إثباتٌ مختبَرٌ أنّ `AizzakScrapeTargetDown` يشمل الوظيفتين الجديدتين.
- تحديث الاختبارات والدفاتر التي تعدّ الخدمات والقواعد والميزانيّة لتطابق الواقع الجديد:
  - `tests/unit/test_resource_budget.py`
  - `tests/unit/test_prometheus_alert_rules.py`
  - `tests/unit/test_observability_stack_wiring.py`
  - `tests/unit/test_connection_budget.py` إن لزم.
  - دفتر الموارد في `docs/design/08-local-runbook.md:475-476`.
- إجراءٌ مكتوب يتّبعه البشري لتفعيل الميزة على **العنقود القائم**، لأنّ `10-roles.sh` لا يعمل إلّا على حجمٍ جديد (`deploy/postgres/initdb/10-roles.sh:7-8`).
- تحديث وثائق الحالة بما تغيّر: `docs/monitoring-plan.md:15` و`:18`، و`د‑12` و`د‑15` في `docs/capacity-status.md` مع `docs/capacity-summary.html` إن تغيّرت حالة دَين.

**خارج النطاق:**
- صفوف المرحلة 2 من 3 إلى 8: Qdrant والعمّال وembedding وnginx وVault وMinIO.
- قناة تنبيهٍ خارجيّة (تيليغرام أو بريد). أجّلها المالك، وهي `GAP‑21`.
- لوحات Grafana. هي المرحلة 4، و**اختياريّة** هنا (`US‑7`، أولويّة Could) إلّا إن ضمّها المالك عند البوابة ①.
- تصدير `pg_stat_statements` لكلّ استعلام (تسمية `queryid` أو نصّ الاستعلام). هذا هو فخّ تعدّد القيم الذي يرفضه `src/app/ops/slow_queries.py:26-30`. تبقى أداة `python -m app.ops.slow_queries` مصدر «أيّ استعلامٍ بطيء».
- مقياس عمق مِبْولة WAL، أي ما بين المِبْولة وMinIO. هذا في `wal-shipper` لا في Postgres، والجزء الذي يلي الخادم من `د‑12` يبقى مفتوحاً (السؤال س‑4).
- تنبيهٌ جديد لعمر آخر نسخة احتياطيّة. `AizzakOpsTaskOverdue{task="backup"}` يغطّيه اليوم (`deploy/prometheus/alerts.yml:631-642`)، انظر الافتراض ف‑5 والسؤال س‑5.
- RunPod (`deploy/runpod/`). لا Prometheus فيه أصلاً.
- إعادة إنشاء أيّ خدمة في الحزمة الحيّة أو إعادة تشغيلها، وإنشاء الدور على العنقود الحيّ. هذا كلّه بيد البشري بعد الدمج.
- تغيير أيّ حدٍّ في `docs/design/07-nfr-slo.md`. الخطّة تقيس ولا تغيّر (`docs/monitoring-plan.md:7`).
- المرحلة 8، أي مراقبة قرص Prometheus وLoki نفسيهما. يغطّيها جزئيّاً تنبيه القرص العامّ (‏FR‑5)، لكنّ قاعدةً خاصّةً بهما خارج النطاق.

## 3. المتطلبات الوظيفية

| المعرّف | المتطلب | المصدر |
|---|---|---|
| FR‑1 | تُضاف خدمة `node-exporter` إلى `docker-compose.yml`. تكون `expose` فقط بلا منفذٍ منشور، بصورةٍ مثبّتة الإصدار، ولها `deploy.resources.limits` (‏`cpus` و`memory`)، وليست خلف `profiles:`. | `docs/monitoring-plan.md:56` و`:69` · `tests/unit/test_resource_budget.py:128-142` · `tests/unit/test_observability_stack_wiring.py:295-323` · `docker-compose.yml:11-13` |
| FR‑2 | تُكشط `node-exporter` في وظيفةٍ مستقلّة في `prometheus.yml` بلا تسمية `tier: optional`. | `docs/monitoring-plan.md:66` و`:71` · `deploy/prometheus/alerts.yml:301` |
| FR‑3 | يكشف `node-exporter` أرقام القرص (المساحة الكلّيّة والمتاحة لكلّ نظام ملفّات) والمعالج والذاكرة والشبكة **للجهاز** لا للحاوية. وتحدّد وثيقة التصميم أيّ أنظمة الملفّات تُعدّ «القرص» على مضيف WSL2 (س‑2). | `docs/monitoring-plan.md:56` |
| FR‑4 | يتجاهل كشطُ القرص أنظمة الملفّات المؤقّتة والافتراضيّة (`tmpfs` و`overlay` و`proc` و`sysfs` وما شابهها) حتى لا تُشعل التنبيه أو تُخفيه. | استنتاجٌ من FR‑5 (تنبيهٌ لا يشتعل خطأً) · `deploy/prometheus/alerts.yml:43-49` |
| FR‑5 | قاعدة تنبيه على الجهاز: **نظام ملفّاتٍ مُراقَب استُعمل فوق 80% لمدّةٍ محدّدة** (`severity: warning`). | `docs/monitoring-plan.md:67` |
| FR‑6 | يُنشأ دور Postgres جديد للمُصدِّر في `10-roles.sh` (الاسم مقترح: `metrics_exporter`). يكون `LOGIN` وعضواً في `pg_monitor` بـ`WITH INHERIT TRUE`، **بلا** `SUPERUSER` ولا `BYPASSRLS` ولا `CREATEROLE` ولا `REPLICATION`، وبلا أيّ `INSERT/UPDATE/DELETE`، وبلا `SELECT` على جداول المستأجرين. ويُمنح `CONNECT` على قاعدة البيانات فقط. | `docs/monitoring-plan.md:57` («بدور قراءة فقط، **وليس** بدور `app_rw`») · `deploy/postgres/initdb/10-roles.sh:100-118` و`:191-201` (درس `NOINHERIT` و`WITH INHERIT TRUE` على PG16) |
| FR‑7 | كلمة مرور الدور الجديد متغيّرٌ اسمه على نمط `<ROLE>_PASSWORD`. يُضاف **اسمه** بقيمة `change-me-*` إلى `.env.example`، ويُمرَّر إلى خدمة `postgres` بحارس `:?`. ويضع البشري القيمة الحقيقيّة. | `CLAUDE.md` «Secrets» · `.env.example:53-60` و`:87` · `docker-compose.yml:651-704` · `tests/unit/test_role_provisioning_wiring.py:62-66` |
| FR‑8 | تُضاف خدمة `postgres-exporter` جانبيّة. تتّصل **مباشرةً** بـ`postgres:5432` بالدور الجديد، وتكون `expose` فقط بصورةٍ مثبّتة، ولها سقف موارد، ولا تتلقّى كلمة مرور المستخدم الخارق ولا `app_rw`. | `docs/monitoring-plan.md:57` · `deploy/postgres/pg_hba.conf:53` (`host all all all scram-sha-256` يقبلها) · `tests/unit/test_observability_stack_wiring.py:295-323` |
| FR‑9 | تُكشط `postgres-exporter` في وظيفةٍ مستقلّة في `prometheus.yml` بلا `tier: optional`. | `docs/monitoring-plan.md:66` و`:71` |
| FR‑10 | يكشف `postgres-exporter` على الأقلّ: حياة الخادم (مثل `pg_up`)، وعدد الأقفال حسب النوع، و**أطول انتظارٍ على قفل**، وأطول معاملةٍ أو استعلامٍ نشط (هذا معنى «الاستعلامات البطيئة» هنا)، وحجم قاعدة البيانات، وحجم كلّ جدول، وحالة الأرشفة. | `docs/monitoring-plan.md:57` |
| FR‑11 | **حالة الأرشفة لا تُبنى على `pg_stat_archiver.failed_count`**، لأنّه مقيسٌ أنّه يبقى صفراً حين يفشل `archive_command` في التنفيذ. تُبنى على تقدّم الأرشفة: المقاطع المكتملة غير المؤرشفة (ملفّات `.ready`)، أو آخر مقطعٍ مؤرشف مقارنةً بالمقطع الحاليّ، أو زمن آخر أرشفة. | `src/app/ops/backup.py:463-475` · `docker-compose.yml:620-642` (‏`archive_command` عبر `sh`) · `docs/capacity-status.md:3012` |
| FR‑12 | قاعدة تنبيه: **Postgres لا يردّ** حسب حكم المُصدِّر (`pg_up == 0` أو ما يكافئه) لا حسب `up` (`severity: critical`). | النمط القائم في `deploy/prometheus/alerts.yml:356-358` و`:992-994` · `tests/unit/test_prometheus_alert_rules.py:267` و`:654` |
| FR‑13 | قاعدة تنبيه: **جلسةٌ تنتظر قفلاً أكثر من دقيقة** (`severity: warning`). | `docs/monitoring-plan.md:67` («Postgres فيه أقفال أطول من دقيقة») |
| FR‑14 | قاعدة تنبيه: **أرشفة WAL متوقّفة**. يتأخّر مقطعٌ مكتمل عن الأرشفة أكثر من حدٍّ يُشتقّ من `archive_timeout=300` (`severity` تُحدَّد في التصميم). ولا تشتعل القاعدة على عنقودٍ هادئ لا يكتب WAL. | `docs/monitoring-plan.md:57` · `docker-compose.yml:643-650` · `docs/capacity-status.md:3012` |
| FR‑15 | لكلّ قاعدةٍ جديدة الحقول التي يفرضها `test_every_rule_carries_the_minimum_operator_fields`، أي `summary` و`description` و`reason` و`response` و`runbook_url`. ويشير `runbook_url` إلى قسمٍ خاصّ بها في `docs/runbooks/alerts.md`، ويُضاف لها صفٌّ في فهرس الوثيقة (`docs/runbooks/alerts.md:46-72`). | `deploy/prometheus/alerts.yml:30-40` · `tests/unit/test_prometheus_alert_rules.py:495` و`:553` |
| FR‑16 | لكلّ قاعدةٍ جديدة حالةٌ في `alerts.test.yml` تُشعلها وحالةٌ تُبقيها صامتة. | `deploy/prometheus/alerts.test.yml:5-8` · `tests/unit/test_prometheus_alert_rules.py:570` |
| FR‑17 | `AizzakScrapeTargetDown` يشتعل لكلٍّ من الوظيفتين الجديدتين حين تتوقّف، ويُثبت ذلك بحالة promtool. | `docs/monitoring-plan.md:71` · `deploy/prometheus/alerts.yml:300-302` |
| FR‑18 | `EXPECTED_ALERTS` في `tests/unit/test_prometheus_alert_rules.py:88` يُحدَّث بالقواعد الجديدة، ويُسجَّل سبب النموّ في docstring الاختبار مع الإشارة إلى هذه الوثيقة. | `tests/unit/test_prometheus_alert_rules.py:209-214` («a TWENTY-FOURTH entry needs its own written reason») |
| FR‑19 | يُحدَّث دفتر الموارد ليشمل الخدمتين: عدد الخدمات الدائمة في `test_the_patterns_actually_find_something` (`tests/unit/test_resource_budget.py:349`)، وأرقام `docs/design/08-local-runbook.md:475-476`. ويبقى `test_the_standing_budget_fits_the_reference_host` أخضر، وهذا معلّقٌ على قرار س‑1. | `docs/monitoring-plan.md:69` · `tests/unit/test_resource_budget.py:145-160` و`:310-331` |
| FR‑20 | تُضاف الخدمتان إلى قائمة «لا منفذ منشور» في `test_the_scraper_and_exporters_publish_no_host_port`. | `tests/unit/test_observability_stack_wiring.py:295-323` |
| FR‑21 | إجراءٌ مكتوب لترقية **عنقودٍ قائم** على نمط `§3.3‑ب`، مع تحذير `--no-deps` وإعادة إنشاء `postgres`. يشمل: إنشاء الدور بيد المستخدم الخارق، وضبط كلمة المرور في `.env`، والأمر الذي يُنشئ الحاويتين الجديدتين دون إعادة إنشاء `postgres`، وإعادة تحميل Prometheus، ثمّ التحقّق. | `deploy/postgres/initdb/10-roles.sh:7-8` · `docs/design/08-local-runbook.md:786-834` |
| FR‑22 | تُحدَّث وثائق الحالة: سطرا «أين نحن اليوم» في `docs/monitoring-plan.md:15` و`:18` (عدد القواعد 23)، وحالة `د‑12` و`د‑15` في `docs/capacity-status.md`، ومعها `docs/capacity-summary.html` إن تغيّرت حالة دَين. | `CLAUDE.md` «Docs conventions» · `docs/capacity-status.md:3012` و`:3023` |

## 4. المتطلبات غير الوظيفية

| المعرّف | المتطلب | معيار القياس |
|---|---|---|
| NFR‑1 | الذاكرة | سقف ذاكرة `node-exporter` ≤ 64 MiB وسقف `postgres-exporter` ≤ 128 MiB، بمثل سقف `pgbouncer-exporter` (`docker-compose.yml:2685-2690`). ويُقرأ المجموع الدائم بـ`deploy/resource-budget.sh --host-cpus 32 --host-memory-gb 64`، الذي يخرج بـ0، وفي حدود `57.60 GB` (`tests/unit/test_resource_budget.py:67-74`)، أو بالحدّ الذي يقرّه المالك في س‑1. |
| NFR‑2 | المعالج | سقف كلّ مُصدِّر ≤ 0.25 vCPU، مثل المُصدِّرات القائمة. |
| NFR‑3 | حدود الصلاحيّات | استعلامٌ في التصميم والاختبار يُثبت أنّ دور المُصدِّر: `rolsuper=f` و`rolbypassrls=f` و`rolcreaterole=f` و`rolreplication=f`؛ وأنّ `SELECT` على جدول مستأجرٍ (مثل `workspace.users`) يُرفض بـ`permission denied`؛ وأنّ `INSERT` على أيّ جدولٍ يُرفض. |
| NFR‑4 | حماية قاعدة البيانات من المُصدِّر | المُصدِّر يفتح ≤ 2 اتّصالٍ متزامنٍ بـPostgres. ويُضبط `statement_timeout` على الدور (≤ 5 s مقترح) حتى لا يتراكم كشطٌ بطيء. والمجموع يبقى تحت `max_connections = 300` (`deploy/postgres/postgresql.conf:56`) مع طلبٍ مقيسٍ عند 161 خلفيّة. |
| NFR‑5 | تعدّد قيم التسميات | لا تسمية `queryid` ولا نصّ استعلام ولا `workspace_id` ولا `user_id`. تسمية الجدول مقبولة لأنّ الجداول بعدد الوحدات (عشرات) لا بعدد المستأجرين. المعيار بعد التفعيل: `scrape_samples_scraped{job="postgres"}` < 3000 و`scrape_samples_scraped{job="node"}` < 3000. |
| NFR‑6 | كلفة الكشط | `scrape_duration_seconds` لكلّ وظيفةٍ جديدة < 2 s في p95 على ساعةٍ من التشغيل الحيّ، بفاصل كشطٍ 15 s لا يتغيّر (`tests/unit/test_observability_stack_wiring.py:255-267`). |
| NFR‑7 | زمن الاكتشاف | توقّف أيّ مُصدِّر يُشعل `AizzakScrapeTargetDown` خلال ≤ 60 s (15 s كشط + `for: 30s` + 15 s تقييم، `deploy/prometheus/prometheus.yml:21-28`). ويُثبَت بـpromtool دون اتّصال، وحيّاً بيد البشري. |
| NFR‑8 | الحدّ الأدنى من الوصول إلى المضيف | `node-exporter` يعمل بمستخدمٍ غير جذر، وبتركيباتٍ للقراءة فقط (`:ro`)، و**بلا** `/var/run/docker.sock`. وأيّ تركيبٍ لجذر المضيف `/` يُبرَّر في التصميم ويُراجَع أمنيّاً، لأنّه يكشف ملفّات المضيف، ومنها `/home/AIZZAK/.env`، لعمليّة الحاوية. |
| NFR‑9 | البوّابات | البوّابات الستّ في `CLAUDE.md` خضراء. يُشغَّل من `pytest` الجزء `tests/unit tests/architecture tests/eval` فقط ما لم يأذن البشري. |
| NFR‑10 | لغة الوثائق | أقسام الدليل بالعربيّة داخل `<div dir="rtl">` على نمط `docs/runbooks/alerts.md`، والقواعد والتعليقات بالإنجليزيّة. |

## 5. الوضع الحالي في الشيفرة

**Prometheus وقواعده:**
- 9 وظائف كشط: `prometheus` و`aizzak-app` و`pgbouncer` و`redis-stream` و`redis-cache` و`alloy` و`loki` و`alertmanager` و`cadvisor` (`deploy/prometheus/prometheus.yml:46-195`). لا وظيفة للجهاز ولا لـPostgres.
- 23 قاعدة (`tests/unit/test_prometheus_alert_rules.py:88`، و`docs/capacity-status.md:2595`).
- `AizzakScrapeTargetDown` هو `up{tier!="optional"} == 0` بـ`for: 30s` (`deploy/prometheus/alerts.yml:300-302`). فأيّ وظيفةٍ جديدةٍ بلا `tier: optional` تُغطّى **تلقائيّاً**، ويبقى إثبات ذلك بحالة promtool.
- `test-rules.sh` يفحص `alerts.yml` وحده بـpromtool بصورة Compose المثبّتة، ويختبر `alerts.test.yml`، ويفحص `alertmanager.yml` (`deploy/prometheus/test-rules.sh:62-72`). **لا يفحص `prometheus.yml`** بـ`promtool check config`.
- `prometheus.yml` و`alerts.yml` مركّبان كملفّين مفردين (`docker-compose.yml:2515-2516`)، و`--web.enable-lifecycle` مفعّل. وقد احتاجت `7.3` إلى **إعادة إنشاء** Prometheus لتحميل القواعد (`docs/capacity-status.md:2595`).

**قاعدة البيانات والأدوار:**
- ثمانية أدوار في `10-roles.sh`، وليس بينها دورٌ للمراقبة. أقربها `metrics_reader`: `SELECT` على `platform.outbox` وحده (`deploy/postgres/initdb/10-roles.sh:43-51`)، ولا يكفي المُصدِّر دون توسيعه.
- السكربت يعمل مرّةً واحدةً على حجمٍ جديد (`:7-8`). والإضافة إلى عنقودٍ قائم يدويّة (`docs/design/08-local-runbook.md:786`).
- كلمات مرور الأدوار عناصر في **بيئة خدمة `postgres`** (`docker-compose.yml:651-704`)، وتغييرها يغيّر بصمة إعداد الخدمة فيُعيد `docker compose up` إنشاء `postgres` (`docs/design/08-local-runbook.md:834`).
- `pg_hba.conf` يقبل أيّ دورٍ عبر الشبكة بـ`scram-sha-256` (`deploy/postgres/pg_hba.conf:53`).
- `pg_stat_statements` محمَّل (`docker-compose.yml:588`)، وأرشفة WAL إلى مِبْولة بـ`archive_timeout=300` (`:620-650`).
- `failed_count` لا يصلح مصدراً لحالة الأرشفة، وهذا مقيس (`src/app/ops/backup.py:463-475`).

**النسخ الاحتياطيّ وعمره:**
- `ops-scheduler` يشغّل `backup` ليلاً ويكتب آخر نجاحٍ في دفتر المهامّ (`docker-compose.yml:2265-2333` و`src/app/framework/observability/scheduled_tasks.py:109-113`).
- `/metrics` يكشفه بـ`aizzak_ops_task_last_success_timestamp_seconds` (`src/app/api/metrics.py:123`).
- `AizzakOpsTaskOverdue` ينبّه بعد دورتين، أي 48 ساعة زائد زمن التشغيل (`deploy/prometheus/alerts.yml:631-642`).
- **فعمر آخر نسخةٍ مقيسٌ ومنبَّهٌ عليه اليوم.** وPostgres لا يعرف ما في MinIO، فالمُصدِّر لا يستطيع قياسه.

**المِبْولة:**
- `wal-shipper` لا يكشف مقياساً، وفحص صحّته نبضٌ فقط (`docker-compose.yml:2162-2212`).
- عمق المِبْولة يقرؤه `python -m app.ops.backup status` ولا يكشطه أحد (`د‑12`، `docs/capacity-status.md:3012`).

**ميزانيّة الذاكرة (حاجبٌ محتمل):**
- `deploy/resource-budget.sh` يقرأ السقوف من `docker-compose.yml` تلقائيّاً (`deploy/resource-budget.sh:93-111`). فـ«إضافة الحدّ إلى `resource-budget.sh`» في الخطّة (`docs/monitoring-plan.md:69`) تعني عمليّاً **وضع `deploy.resources.limits` في Compose**، وتحديث العدّ والدفتر.
- المجموع الدائم اليوم **57.59 GB = 58,976 MiB**، والحدّ `64 − 6.4 = 57.60 GB = 58,982.4 MiB` (`tests/unit/test_resource_budget.py:67-74` و`:155`). **الهامش المتبقّي 6.4 MiB فقط.** وقد قالت `7.3` إنّها «sized to fit the last tenth» (`tests/unit/test_resource_budget.py:347-348`).
- ⇒ أيّ مُصدِّرٍ دائمٍ بأيّ سقفٍ واقعيّ **يُفشل `test_the_standing_budget_fits_the_reference_host`**. هذا هو السؤال س‑1.
- وعلى هذا المضيف بالذات (10 vCPU و12.67 GB) المجموع يتجاوز الذاكرة أصلاً: `MEMORY OVER` ومخرج 1، وهو `د‑25`. وانهار المضيف تحت الحمل بسبب ضغط الذاكرة (`د‑34`، `docs/capacity-status.md:366`).

**المضيف (WSL2):**
- Docker Engine يعمل داخل توزيعة WSL2 مباشرةً، لا عبر Docker Desktop (`docker info`: ‏`Ubuntu 24.04.4 LTS · 6.6.87.2-microsoft-standard-WSL2`).
- `/` و`/var/lib/docker` كلاهما `/dev/sdd ext4` بحجمٍ افتراضيٍّ 1007G، **مستعمَلٌ منه 14%**.
- `C:\` مركّب على `/mnt/c` بنظام `9p`، **مستعمَلٌ منه 83%** ومتاحٌ 70G (`df -h`، 2026‑10‑05).
- القرص الافتراضيّ ملفٌّ `ext4.vhdx` ينمو على قرص Windows. **مكانه لم يُتحقَّق منه (يحتاج تأكيداً)**. إن كان على `C:` فالقرص الافتراضيّ يقول 14% بينما ما ينفد فعلاً هو `C:` عند 83%. هذا هو السؤال س‑2.

**الاختبارات التي ستلمسها الميزة:**
- `tests/unit/test_resource_budget.py`: العدّ `27` في `:349`، والهامش في `:145-160`، والدفتر في `:310-331`.
- `tests/unit/test_prometheus_alert_rules.py`: ‏`EXPECTED_ALERTS` في `:88`، والحقول والروابط وpromtool في `:495-597`.
- `tests/unit/test_observability_stack_wiring.py`: الأهداف موجودة في `:201`، والطبقة الاختياريّة في `:270`، و«لا منفذ» في `:295`.
- `tests/unit/test_connection_budget.py:95-108`: جلسات الإدارة بالأرقام الحرفيّة. اتّصال المُصدِّر المباشر بـPostgres لا يمرّ بالمُجمِّع، فلا يدخل `_pooler_clients`. يحسم التصميم هل يُعدّ.
- `tests/unit/test_metrics_topology.py`: يحكم استعلامات `aizzak-app` وحدها (`:111`). المقاييس الجديدة ليست منها، لكنّ الخطّة تطلب ضمّ أيّ استعلام لوحةٍ جديد إليه (`docs/monitoring-plan.md:104`).

## 6. الافتراضات والأسئلة المفتوحة

**الافتراضات (أضيق قراءةٍ معقولة، وتُعدَّل عند البوابة ①):**

| # | الافتراض | يحتاج قرار من |
|---|---|---|
| ف‑1 | اسما وظيفتي الكشط `node` و`postgres`، واسما الخدمتين `node-exporter` و`postgres-exporter`. معايير القبول تستعمل هذه الأسماء. | المعماريّ (يجوز تغييرها مع تحديث AC) |
| ف‑2 | «تنبيهٌ واحد على الأقلّ» لكلّ خدمة يُقرأ هكذا: **قاعدةٌ واحدة للجهاز** (القرص)، و**ثلاث لـPostgres**: لا يردّ، وقفلٌ أطول من دقيقة، والأرشفة متوقّفة. المعالج والذاكرة والشبكة وحجم الجداول والاستعلامات البطيئة **تُكشط بلا تنبيه**. | المالك |
| ف‑3 | «الاستعلامات البطيئة» تعني أطول معاملةٍ أو استعلامٍ نشطٍ الآن (رقمٌ واحدٌ لكلّ حالة)، لا ترتيب `pg_stat_statements` لكلّ استعلام. | المالك |
| ف‑4 | «تأخّر أرشفة WAL» يعني المرحلة **من الخادم إلى المِبْولة** فقط (`.ready` وآخر أرشفة). مرحلة المِبْولة إلى MinIO خارج النطاق. | المالك |
| ف‑5 | «عمر آخر نسخةٍ احتياطيّة» **مغطّى** بالمقياس والتنبيه القائمين (`aizzak_ops_task_last_success_timestamp_seconds{task="backup"}` و`AizzakOpsTaskOverdue`)، فلا قاعدةَ جديدة. المطلوب فقط سطرٌ في الدليل يربط السؤال بهما. | المالك |
| ف‑6 | كلمة مرور الدور الجديد تتبع نمط أدوار Postgres القائمة: `.env` بحارس `:?`، والاسم في `.env.example`. لا تُقرأ من Vault، لأنّ `postgres-exporter` لا يقرأ Vault، والأدوار الثمانية كلّها اليوم في `.env`. | المالك (س‑3) |
| ف‑7 | لوحات Grafana خارج النطاق ما لم يضمّها المالك (`US‑7` بأولويّة Could). | المالك |
| ف‑8 | لا تغيير على RunPod. | — |

**الأسئلة المفتوحة (للبوابة ①):**

| # | السؤال | يحتاج قرار من |
|---|---|---|
| س‑1 | **🔴 حاجب: لا مكان في ميزانيّة الذاكرة.** الهامش 6.4 MiB، والمُصدِّران يحتاجان نحو 192 MiB (64 + 128). الخيارات: (أ) **خفض سقف خدمةٍ قائمة** بالمقدار نفسه، مثل `prometheus` من 2g، أو `minio`، أو `redis-cache` من 2g. (ب) **خفض الهامش** `_MEMORY_HEADROOM_GB` من 6.4، وهذا تغيير قاعدةٍ في دفترٍ مُحكَم. (ج) **وضعهما خلف profile** مع `tier: optional`، وهذا يُسقط معيار القبول لأنّ `AizzakScrapeTargetDown` لن يغطّيهما. أيّها؟ | المالك |
| س‑2 | **ما معنى «القرص» على WSL2؟** القرص الافتراضيّ `/` عند 14% من 1007G، و`C:` عند 83%، ومكان `ext4.vhdx` غير مؤكَّد. هل يُراقَب `/` وحده، أم `/mnt/c` أيضاً، أم يُراقَب `C:` من Windows خارج هذه الميزة؟ ⚠️ إن دخل `/mnt/c` **فتنبيه 80% سيشتعل من أوّل يوم** (83% اليوم). | المالك |
| س‑3 | سرّ الدور الجديد: `.env` على نمط الأدوار الثمانية (ف‑6)، أم Vault كما يقول `CLAUDE.md` للأسرار الجديدة؟ Vault يحتاج آليّةً تكتب السرّ في ملفٍّ يقرؤه المُصدِّر (`DATA_SOURCE_PASS_FILE`)، وهذا ليس موجوداً اليوم. | المالك |
| س‑4 | هل يُضمّ عمق المِبْولة (من المِبْولة إلى MinIO، الجزء الباقي من `د‑12`)؟ قياسه من Postgres يحتاج `pg_read_server_files` أو `pg_ls_dir` على مسارٍ خارج `PGDATA`، وكلاهما يوسّع صلاحيّة الدور كثيراً. والبديل مقياسٌ يكشفه `wal-shipper`، وهذا تغييرٌ في `src/` خارج النطاق المقترح. | المالك |
| س‑5 | هل يريد المالك تنبيهاً **أضيق** لعمر النسخة الاحتياطيّة (مثلاً > 26 ساعة)، بدل حدّ `AizzakOpsTaskOverdue` البالغ 48 ساعة زائد زمن التشغيل؟ | المالك |
| س‑6 | **إعادة إنشاء `postgres` عند التفعيل.** إضافة متغيّر كلمة المرور إلى بيئة `postgres` (FR‑7) تغيّر بصمة إعداده، فأوّل `docker compose up -d` بعد الدمج **يُعيد إنشاء قاعدة البيانات** (`docs/design/08-local-runbook.md:834`). هل يقبل المالك نافذة صيانةٍ لذلك، أم يُفضَّل تصميمٌ يتجنّبه؟ مثال ذلك: تفعيلٌ بـ`--no-deps` للخدمتين وPrometheus فقط، مع إنشاء الدور يدويّاً. | المالك + المعماريّ |
| س‑7 | حدود القواعد الجديدة. مدّة `for:` لتنبيه القرص (مقترح 10m)، وهل يُضاف حدٌّ حرجٌ عند 90%؟ وحدّ الأرشفة (مقترح: مقطعٌ مكتملٌ غير مؤرشف > 15 دقيقة، أي ثلاثة أضعاف `archive_timeout`)؟ وخطورتها (مقترح `critical` لأنّ الأرشفة المتوقّفة تملأ القرص وتُبطل الاسترجاع إلى نقطة)؟ | المالك |
| س‑8 | هل يُضاف تنبيه **ضغط ذاكرة المضيف** (مثلاً `MemAvailable` < 10% لمدّة 5m)؟ `د‑34` يقول إنّ المضيف انهار من ضغط الذاكرة. لكنّ تشغيلات الحمل على هذا المضيف ستُشعله دائماً. | المالك |
| س‑9 | الشبكة على WSL2. أرقام الشبكة لا تكون للجهاز إلّا إن شارك `node-exporter` شبكة المضيف (`network_mode: host`)، وهذا يُخرجه من شبكة Compose فلا يصل إليه Prometheus باسمه. هل تكفي أرقام الشبكة كما تراها الحاوية، أم تُقبل `network_mode: host` مع هدفٍ بعنوان المضيف؟ (يحتاج تأكيداً في التصميم.) | المعماريّ |
| س‑10 | لوحات Grafana (`aizzak-host.json` و`aizzak-postgres.json`): داخل النطاق أم تؤجَّل إلى المرحلة 4؟ | المالك |

## 7. التقدير

| البند | الحجم (S/M/L/XL) | ملاحظات |
|---|---|---|
| US‑1 `node-exporter` وكشطه | M | التركيبات على WSL2، وأنظمة الملفّات (س‑2)، وشبكة المضيف (س‑9) |
| US‑2 دور القراءة و`postgres-exporter` وكشطه | L | دورٌ جديد بمواضعه الأربعة، و`pg_monitor` على PG16، والاستعلامات الإضافيّة للأقفال والأرشفة، ودفتر الاتّصالات |
| US‑3 تنبيه القرص ودليله واختباره | S | |
| US‑4 تنبيهات Postgres الثلاثة وأدلّتها واختباراتها | M | قاعدة الأرشفة تحتاج تمييز العنقود الهادئ |
| US‑5 تغطية `AizzakScrapeTargetDown` | S | حالتا promtool |
| US‑6 الميزانيّة والاختبارات والدفاتر | S | **معلّق على س‑1** |
| US‑7 لوحتان دنيا (Could) | M | خارج الإجمالي ما لم يضمّها المالك |
| US‑8 إجراء التفعيل على العنقود القائم وتحديث الحالة | S | التنفيذ الحيّ نفسه بيد البشري |
| **الإجمالي** | **L** | دون US‑7: **نحو 3 جلسات عمل**، يوم إلى يومين، مقابل «يوم» في الخطّة (`docs/monitoring-plan.md:183`). ومع US‑7: يُضاف يومٌ تقريباً. |

**المخاطر الرئيسيّة:**
1. **ميزانيّة الذاكرة (س‑1).** دون قرارٍ يفشل `pytest`، فلا commit.
2. **إعادة إنشاء `postgres` غير مقصودة عند التفعيل (س‑6).** بيد البشري، لكنّها تحتاج إجراءً صريحاً (FR‑21).
3. **تعريف القرص على WSL2 (س‑2).** تنبيهٌ يشتعل دائماً، أو لا يشتعل أبداً.
4. **تحميل Prometheus للإعداد الجديد.** الملفّات المركّبة مفردةً، وقد احتاجت `7.3` إعادة إنشاء، فـ`/-/reload` وحده قد لا يكفي.
5. **الاستعلامات الإضافيّة في `postgres-exporter`.** آليّة `--extend.query-path` موسومةٌ بالإهمال في الإصدارات الحديثة من المُصدِّر (يحتاج تأكيداً في التصميم). وقياس انتظار القفل والأرشفة قد يعتمد عليها.
6. **ذاكرة المضيف الحيّ مستنفدة أصلاً (`د‑34`).** حاويتان إضافيّتان صغيرتان، لكنّهما على مضيفٍ بلا هامش. يُقاس RSS الفعليّ بعد التفعيل.

</div>
