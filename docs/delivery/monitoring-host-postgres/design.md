<div dir="rtl">

# التصميم التقني: مراقبة الجهاز وPostgres (خطّة المراقبة، المرحلة 2، الصفّان 1 و2)

> **المعرّف:** `monitoring-host-postgres` · **الفرع:** `monitoring` · **التاريخ:** 2026‑10‑05 · **المصمّم:** software-architect
> **المدخلات:** [`requirements.md`](requirements.md) · [`stories.md`](stories.md) · قرارات البوابة ① في [`status.md`](status.md) (النطاق US‑1…US‑6 وUS‑8 وUS‑9؛ US‑7 مؤجّلة).
> **ما تحقّقتُ منه بيدي قبل الكتابة** (لا شيء منه لمس الحزمة الحيّة إلّا بالقراءة):
> - `docker compose config --hash` للخدمات قبل التغيير المقترح وبعده (§3‑ز).
> - `promtool check rules` و`promtool test rules` على القواعد والحالات المقترحة أدناه، و`promtool check config` على `prometheus.yml` المقترح. كلّها `SUCCESS` بصورة `prom/prometheus:v3.13.2` المثبّتة.
> - حاوية `node-exporter` مؤقّتة بالأعلام والتركيب المقترحين (§3‑ب).
> - عنقود `postgres:16` مؤقّت على شبكةٍ مؤقّتة: سكربت الدور المقترح نُفّذ عند `initdb` ثمّ مرّتين يدويّاً، وتحته `postgres-exporter:v0.20.1` بملفّ الاستعلامات المقترح (§3‑ج، §4).
> - استعلاما الملفّ نُفِّذا للقراءة فقط على Postgres 16.14 الحيّ.
> - استعلامات Prometheus للقراءة فقط (cAdvisor وredis_exporter، 15 يوماً) لاختيار الحدّ المخفوض (§3‑هـ).

## 1. الملخص

الميزة **إعدادٌ وتشغيلٌ فقط**. لا شيفرة في `src/`، ولا ترحيل Alembic، ولا طبقة من طبقات `.importlinter`. تُضاف خدمتان جانبيّتان دائمتان إلى `docker-compose.yml` بجوار المُصدِّرات القائمة (`pgbouncer-exporter` و`redis-*-exporter`)، وتسير كلٌّ منهما على نمطها: صورة مثبّتة، و`expose` فقط، و`deploy.resources.limits`، و`<<: *app-logging`، وفحص صحّة يطرق المُصدِّر لا ما خلفه.

1. **`node-exporter`**: يقرأ أرقام الجهاز. **لا يركّب جذر المضيف.**
   - يعدّ القرص من **مجلّدٍ مُسمّى فارغ** (`node-exporter-probe`) مركّبٍ على `/host`، مع `--path.rootfs=/host`. فيُبلَّغ عن نظام الملفّات الذي يحمل جذر بيانات Docker بـ`mountpoint="/"`، وهو `/` على هذا المضيف (`/dev/sdd`، مقيس).
   - المعالج والذاكرة من `/proc` الحاوية، وهي أرقام المضيف لأنّ `meminfo` و`stat` غير معزولين.
   - الشبكة من نطاق الحاوية (Q‑9).
2. **`postgres-exporter`**: يتّصل **مباشرةً** بـ`postgres:5432` بدورٍ جديد `metrics_exporter`. الدور `pg_monitor` وحده، `NOINHERIT` مع عضويّةٍ `WITH INHERIT TRUE`، و`CONNECTION LIMIT 2`، و`statement_timeout=5s` و`lock_timeout=1s` و`default_transaction_read_only=on`. ويضيف المُصدِّر استعلامين صغيرين عبر `--extend.query-path`:
   - أطول انتظار قفل، من `pg_locks.waitstart`.
   - عمر أقدم مقطع WAL ينتظر الأرشفة (ملفّات `.ready`)، من `pg_ls_archive_statusdir()`.
3. **أربع قواعد جديدة** في `alerts.yml`، بمجموعتين `aizzak-host` و`aizzak-postgres`:
   - `AizzakHostDiskHigh`
   - `AizzakPostgresDown`
   - `AizzakPostgresLockWaitHigh`
   - `AizzakPostgresArchiveStalled`

   ووظيفتا كشط `node` و`postgres` بلا `tier: optional`، فيغطّيهما `AizzakScrapeTargetDown` تلقائيّاً.
4. **التفعيل لا يُعيد إنشاء `postgres` ولا يترك له إعادة إنشاءٍ معلّقة.** كتلة خدمة `postgres` لا تتغيّر حرفاً، فبصمتها لا تتغيّر (مُثبَت في §3‑ز). كلمة سرّ الدور **لا تدخل بيئة `postgres`**:
   - الدور يُولد بلا كلمة سرّ من سكربت `initdb` جديد، `deploy/postgres/initdb/15-metrics-exporter.sh`.
   - كلمة السرّ يضعها **السكربت نفسه** حين يشغّله البشريّ بـ`docker compose exec -e METRICS_EXPORTER_PASSWORD`. هذا هو قرار Q‑6: «الدور يُنشأ يدويّاً».
5. **ميزانيّة الذاكرة (Q‑1):** يُخفَض سقف `redis-cache` من `2g` إلى `1792m`. الدليل في §3‑هـ: ذروة 15 يوماً 21.0 MiB، وسقف Redis نفسه `--maxmemory 1gb`. المجموع الدائم يصير **57.53 GB** من 57.60، أي بهامش **70.4 MiB**. الهامش `_MEMORY_HEADROOM_GB` و`prometheus` لا يُمسّان.

**المراحل:** UX ⏭ · **البيانات (data-engineer) ⏭**، لأنّ الـDDL أدناه محدّدٌ حرفيّاً ومُجرَّب، ولا Alembic ولا RLS · الواجهة ⏭. **المنفّذ الوحيد: backend-developer.**

## 2. الملفات

كلّ الملفّات لمنفّذٍ واحد، فلا تداخل بين مالكين.

| المسار | إنشاء/تعديل | المسؤولية | المنفّذ |
|---|---|---|---|
| `deploy/postgres/initdb/15-metrics-exporter.sh` | إنشاء (وضع **0644**، كـ`10-roles.sh`) | ينشئ الدور `metrics_exporter` ومنحه وإعداداته؛ ويضع كلمة السرّ **فقط** إن وُجد `METRICS_EXPORTER_PASSWORD` في بيئته. يُنفَّذ تلقائيّاً عند `initdb` (يُستورَد `source`)، ويدويّاً على أيّ عنقود. نصّه في §4 | backend-developer |
| `deploy/postgres-exporter/queries.yaml` | إنشاء | استعلاما `pg_lock_wait` و`pg_archive_ready` (§3‑ج) | backend-developer |
| `docker-compose.yml` | تعديل | (1) خدمتا `node-exporter` و`postgres-exporter` (§3‑أ)، وتوضعان قبل كتلة `cadvisor`. (2) مجلّد `node-exporter-probe:` تحت `volumes:`. (3) `redis-cache`: ‏`memory: 2g` ← `memory: 1792m` مع تعليق الدليل (§3‑هـ). **كتلة `postgres` لا تُلمس، ولا تعليقاتها.** | backend-developer |
| `deploy/prometheus/prometheus.yml` | تعديل | وظيفتا `node` ← `node-exporter:9100` و`postgres` ← `postgres-exporter:9187`، قبل قسم الهدف الاختياريّ، بلا `labels` (§3‑ج) | backend-developer |
| `deploy/prometheus/alerts.yml` | تعديل | مجموعتا `aizzak-host` و`aizzak-postgres` قبل `aizzak-meta`، وفيهما القواعد الأربع بحقولها الخمسة (§3‑د)؛ وجملةٌ في رأس الملفّ (قسم Scope) تسجّل نموّ المرحلة 2 | backend-developer |
| `deploy/prometheus/alerts.test.yml` | تعديل | حالات promtool في §5‑أ، وهي مُجرَّبة | backend-developer |
| `deploy/prometheus/test-rules.sh` | تعديل | فحصٌ رابع: `promtool check config` على `prometheus.yml` مع تركيب `alerts.yml` في `/etc/prometheus/` (‏AC‑1.2). يعمل في CI بالخطوة القائمة، فلا تعديل على `ci.yml` | backend-developer |
| `.env.example` | تعديل | `METRICS_EXPORTER_PASSWORD=change-me-metrics-exporter`، مع تعليقٍ يقول لماذا **لا** يدخل بيئة `postgres`. وتُحدَّث جملة «Roles are created at cluster init (…10-roles.sh)» لتذكر `15-metrics-exporter.sh` | backend-developer |
| `.env.test.example` | تعديل | `TEST_DATABASE_URL_METRICS_EXPORTER=postgresql+asyncpg://metrics_exporter:change-me-metrics-exporter@127.0.0.1:15432/aizzak_test`، مع تعليق «يقرؤه `test_metrics_exporter_role_live.py` وحده، ويتخطّى وحده إن غاب» (نمط `TEST_DATABASE_URL_BACKUP`) | backend-developer |
| `tests/unit/test_metrics_exporter_role.py` | إنشاء | حرّاس الدور والسكربت والمتغيّر وحياد البصمة وعدم تصدير `queryid` (§5‑ب) | backend-developer |
| `tests/integration/test_metrics_exporter_role_live.py` | إنشاء | ‏`pytestmark = pytest.mark.live_db`: صلاحيّات الدور من الفهرس (تعمل في CI)، ثمّ اتّصالٌ بالدور نفسه يتخطّى وحده إن تعذّر، على نمط `test_backup_live.py` (§5‑ج) | backend-developer |
| `tests/unit/test_prometheus_alert_rules.py` | تعديل | ‏`EXPECTED_ALERTS` من 23 إلى 27 مع سبب النموّ (§5‑د)، وفقرة في docstring، و«TWENTY-FOURTH» ← «TWENTY-EIGHTH»، ونصّ رسالة الفشل. وحرّاسٌ جدد: حياة Postgres تقرأ `pg_up`، والأرشفة لا تذكر `failed_count`، والقفل > 60، والقرص `/` وحده، و`test-rules.sh` يفحص `check config`، وقسم `AizzakOpsTaskOverdue` يذكر مقياس عمر النسخة الاحتياطيّة (AC‑5.8) | backend-developer |
| `tests/unit/test_observability_stack_wiring.py` | تعديل | `node-exporter` و`postgres-exporter` في قائمة `test_the_scraper_and_exporters_publish_no_host_port`. وحارسٌ جديد للمُصدِّر المضيفيّ (AC‑1.3)، وآخر للوظيفتين: الاسم والمنفذ وغياب `tier` | backend-developer |
| `tests/unit/test_resource_budget.py` | تعديل | `len(standing) == 29`، مع تعليقٍ على نمط `:338-348` يسجّل الأرقام 36.10/57.53 ومَن دفع ثمنها | backend-developer |
| `tests/unit/test_connection_budget.py` | تعديل | `_POSTGRES_EXPORTER_BACKENDS = 2` يُضاف إلى مجموع خلفيّات Postgres، ويُحرَس بربطه بـ`DATA_SOURCE_URI` وبـ`CONNECTION LIMIT 2` (§3‑و) | backend-developer |
| `docs/runbooks/alerts.md` | تعديل | أربعة صفوف في فهرس §1، وأربعة أقسام بمراسٍ `<a id="…">`. التوزيع: `aizzakpostgresdown` و`aizzakpostgresarchivestalled` في §2، و`aizzakpostgreslockwaithigh` في §3، و`aizzakhostdiskhigh` في §4. وسطرٌ في قسم `AizzakOpsTaskOverdue` (AC‑5.8). المحتوى في §3‑د | backend-developer |
| `docs/design/08-local-runbook.md` | تعديل | (1) §2‑ب: صفّ `postgres-exporter` في جدول المستهلكين، و`compose.postgres_backends = 164`. (2) §2‑ز: `standing_cpus = 36.10` و`standing_memory_gb = 57.53`، وفقرة «⚠️ و`monitoring-host-postgres`…» بدليل §3‑هـ. (3) **قسم جديد `### 3.3‑ج`** بإجراء التفعيل (§6‑ب) بعد §3.3‑ب. (4) سطر إحالة في §4.28 | backend-developer |
| `docs/monitoring-plan.md` | تعديل | السطر 15: نقل «Postgres نفسه» و«الجهاز» من الناقص إلى الموجود. السطر 18: 27 قاعدة | backend-developer |
| `docs/capacity-status.md` | تعديل | `د‑12` و`د‑15`: «ضاقا» بما يُغلقه المُصدِّر، وما يبقى (عمق المِبْولة، Q‑4، واللوح في المرحلة 4) | backend-developer |
| `docs/capacity-summary.html` | تعديل | الخطوة ٢٫٨ «متبقٍّ»: المؤشّر صار يُكشَط ولا لوحَ له بعد. **أعداد الشريط لا تتغيّر** | backend-developer |
| `docs/quickstart.md` | تعديل | سطرٌ واحد بعد أوّل `docker compose up -d` في §2.2: على حجمٍ جديد، شغّل خطوة ② من 08 §3.3‑ج مرّةً، وإلّا اشتعل `AizzakPostgresDown` | backend-developer |

**ما لا يُلمس عمداً:**
- `deploy/postgres/initdb/10-roles.sh`: الإذن قائم لكنّه غير لازم. ولو أُضيف الدور إليه لاحتاج متغيّراً في بيئة `postgres`، وهذا هو ما يُعيد إنشاء القاعدة.
- كتلة `postgres` في Compose.
- `deploy/postgres/testdb/20-test-database.sh`: إضافة الدور إلى `GRANT CONNECT` فيه تُفشل السكربت على عنقودٍ لم يُفعَّل فيه الدور. و`CONNECT` لـ`PUBLIC` افتراضيّ يكفي.
- `.github/workflows/ci.yml` (انظر ⚠️‑1).
- `src/`.
- `deploy/grafana/` (US‑7 مؤجّلة).
- `app.ops.provision`: الدور ليس في `PROVISION_ROLES`، لأنّ أيّ هجرةٍ لا تسمّيه.

## 3. العقود

### الواجهات البرمجية

لا واجهة HTTP جديدة في التطبيق، و`docs/design/openapi.yaml` لا يتغيّر. نقطتا كشطٍ داخليّتان فقط، `expose` بلا `ports` وبلا مصادقة، على حدّ الثقة نفسه الذي يقف عليه `pgbouncer-exporter`:

| الطريقة | المسار | الطلب | الاستجابة | الأخطاء | الصلاحية |
|---|---|---|---|---|---|
| GET | `http://node-exporter:9100/metrics` | — | نصّ Prometheus، نحو 360 عيّنة (مقيس) | — | شبكة Compose الداخليّة فقط |
| GET | `http://postgres-exporter:9187/metrics` | — | نصّ Prometheus، نحو 1,600 عيّنة متوقّعة على 43 جدولاً (629 مقيسة على عنقودٍ تجريبيّ بـ3 جداول) | `pg_up 0` إن تعذّر الدخول؛ والنقطة تبقى 200 | شبكة Compose الداخليّة فقط |
| GET | `/` على المنفذين | — | صفحة الهبوط، 200 | — | فحص الصحّة وحده. **لا** `/metrics`، كي لا يفتح كلّ فحصٍ جولةً من استعلامات Postgres |

### 3‑أ. خدمتا Compose (نصٌّ مُلزِم، وقد مرّ عبر `docker compose config --quiet`)

```yaml
  # Monitoring plan phase 2, row 1 (docs/delivery/monitoring-host-postgres/design.md).
  # NO host rootfs mount, deliberately: `/:/host` would hand uid 65534 every
  # world-readable host file -- measured: /home/AIZZAK/.env is mode 0644 -- and
  # the whole Windows drive through /mnt/c. statfs() on an empty NAMED volume
  # answers for the filesystem holding Docker's data root, which on this host
  # IS `/` (/dev/sdd, measured equal to `df -B1 /`); --path.rootfs strips the
  # /host prefix so the series reads mountpoint="/". On a host whose
  # /var/lib/docker is a separate disk this measures THAT disk -- the one every
  # volume (Postgres, WAL spool, TSDB, Loki) lives on.
  # CPU/memory come from the container's own /proc, which is host-wide (no
  # lxcfs). Network numbers are the CONTAINER's namespace (Q-9), not the host's.
  # `node_filesystem_readonly{mountpoint="/"}` reads 1 because THIS mount is
  # :ro -- it says nothing about the host disk; no rule reads it.
  node-exporter:
    deploy:
      resources:
        limits:
          cpus: "0.25"
          memory: 64m
    image: prom/node-exporter:v1.12.1
    user: "65534:65534"
    read_only: true
    cap_drop: ["ALL"]
    security_opt: ["no-new-privileges:true"]
    volumes:
      - node-exporter-probe:/host:ro
    command:
      - "--path.rootfs=/host"
      - "--collector.disable-defaults"
      - "--collector.cpu"
      - "--collector.meminfo"
      - "--collector.loadavg"
      - "--collector.vmstat"
      - "--collector.stat"
      - "--collector.diskstats"
      - "--collector.filesystem"
      - "--collector.netdev"
      - "--collector.uname"
      - "--collector.time"
      - "--collector.filesystem.mount-points-exclude=^/(dev|proc|sys|etc|run)($$|/)"
      - "--collector.filesystem.fs-types-exclude=^(9p|autofs|binfmt_misc|bpf|cgroup2?|configfs|debugfs|devpts|devtmpfs|drvfs|erofs|fuse\\..*|fusectl|hugetlbfs|iso9660|mqueue|nsfs|overlay|proc|procfs|pstore|rpc_pipefs|securityfs|selinuxfs|squashfs|sysfs|tmpfs|tracefs|virtiofs)$$"
    expose:
      - "9100"
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://127.0.0.1:9100/"]
      interval: 15s
      timeout: 5s
      retries: 4
      start_period: 10s
    restart: unless-stopped
    <<: *app-logging

  # Monitoring plan phase 2, row 2. DIRECT to postgres:5432, never the pooler:
  # `AizzakPostgresDown` exists for the server dying BEHIND a live pgbouncer.
  # Holds ONE credential, its own: metrics_exporter is pg_monitor and nothing
  # else, CONNECTION LIMIT 2 (= the exporter's two MaxOpenConns(1) pools),
  # statement_timeout 5s, lock_timeout 1s, read-only by default -- all set by
  # deploy/postgres/initdb/15-metrics-exporter.sh, which is also what sets its
  # password (08 §3.3-ج). The password is NOT in the `postgres` service's
  # environment on purpose: a new key there changes that service's config hash,
  # and the next plain `docker compose up -d` would recreate the database.
  # Probes the EXPORTER (`/`), not the server and not /metrics -- the
  # pgbouncer-exporter reasoning: it must keep answering `pg_up 0` mid-outage.
  # queries.yaml is a single-file bind: an edit needs
  # `up -d --force-recreate --no-deps postgres-exporter`.
  postgres-exporter:
    deploy:
      resources:
        limits:
          cpus: "0.25"
          memory: 128m
    image: prometheuscommunity/postgres-exporter:v0.20.1
    user: "65534:65534"
    read_only: true
    cap_drop: ["ALL"]
    security_opt: ["no-new-privileges:true"]
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      DATA_SOURCE_URI: postgres:5432/${POSTGRES_DB:-aizzak}?sslmode=disable&connect_timeout=5
      DATA_SOURCE_USER: metrics_exporter
      DATA_SOURCE_PASS: ${METRICS_EXPORTER_PASSWORD:?set METRICS_EXPORTER_PASSWORD in .env}
    volumes:
      - ./deploy/postgres-exporter/queries.yaml:/etc/postgres-exporter/queries.yaml:ro
    command:
      - "--web.listen-address=:9187"
      - "--extend.query-path=/etc/postgres-exporter/queries.yaml"
      - "--collection-timeout=8s"
      - "--no-collector.stat_statements"
      - "--no-collector.statio_user_tables"
    expose:
      - "9187"
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://127.0.0.1:9187/"]
      interval: 15s
      timeout: 5s
      retries: 4
      start_period: 10s
    restart: unless-stopped
    <<: *app-logging
```

وتحت `volumes:` في آخر الملفّ: `node-exporter-probe:` (مجلّدٌ فارغ يملكه الجذر بوضع 0755، فلا تكتب فيه العمليّة `65534` شيئاً).

**الصور المثبّتة ولماذا هي:**
- `prom/node-exporter:v1.12.1`: آخر إصدار، 2026‑07‑14، وتصحيحٌ على 1.12.0. لا تغييرات كاسرة منذ 1.10؛ آخر `[CHANGE]` هو إضافة صورة distroless في 1.11.0. الصورة الافتراضيّة busybox فيها `wget` لفحص الصحّة، و`USER nobody`، ومن المؤسّسة نفسها التي تُصدر `prom/prometheus` و`prom/alertmanager` المثبّتتين.
- `prometheuscommunity/postgres-exporter:v0.20.1`: آخر إصدار، 2026‑07‑08. من المستودع والسجلّ نفسيهما للمثبَّت `pgbouncer-exporter:v0.12.1`. **0.20.0 كسرت أسماء أعلام** (`--disable-settings-metrics` أُزيل، و`replication_slot` ← `replication_slots`) ونقلت `stat_activity` و`stat_archiver` و`settings` إلى جامعاتٍ مستقلّة. فالأعلام وأسماء المقاييس في هذا التصميم مكتوبةٌ على 0.20، والتثبيت على 0.19 كان سيعني كتابتها على ما أُهمل. و0.20.1 تصحيحٌ وحيد (`stat_replication`). الصورة busybox فيها `wget`، و`USER nobody`.
- ⚠️ ليس قراراً، بل خطرٌ مسجّل: `--extend.query-path` **موسومٌ بالإهمال** ويطبع سطر `WARN` عند الإقلاع، لكنّه يعمل في 0.20.1 (مقيس: `pg_exporter_user_queries_load_error 0`). التثبيت يحمي منه. وترقية المُصدِّر لاحقاً تفحص هذا العلم أوّلاً.

**أسطر سجلٍّ متوقَّعة لا تعني عطلاً** (تُذكر في قسم الدليل):
- `postgres-exporter`: ‏`WARN … Error loading config … postgres_exporter.yml` (ملفّ الوحدات المتعدّدة غير مستعمل)، و`WARN … The extended queries.yaml config is DEPRECATED`.
- `node-exporter`: ‏`ERROR … diskstats … /run/udev/data` مرّةً عند الإقلاع، لأنّ خصائص udev غير مركّبة عمداً. الجامع نفسه ينجح: `node_scrape_collector_success{collector="diskstats"} 1`.

### 3‑ب. ما يكشفه `node-exporter` (مقيسٌ بحاويةٍ مؤقّتة بالأعلام أعلاه، على هذا المضيف)

| المقياس | القيمة المقيسة | مرجع المقارنة |
|---|---|---|
| `node_filesystem_size_bytes{device="/dev/sdd",fstype="ext4",mountpoint="/"}` | 1,081,101,176,832 | `df -B1 /` = 1,081,101,176,832 ✓ |
| `node_filesystem_avail_bytes{…,mountpoint="/"}` | 890,882,105,344 | `df` = 890,882,170,880 (فارق ثوانٍ) ✓ |
| سلاسل `node_filesystem_*` الأخرى | **لا شيء**. لا `/mnt/c` (9p)، ولا overlay، ولا tmpfs، ولا `/etc/hosts` | Q‑2 ✓ |
| `node_memory_MemTotal_bytes` | 13,599,727,616 | `MemTotal: 13280984 kB` × 1024 = 13,599,727,616 ✓ (AC‑1.5) |
| `count(count by (cpu) (node_cpu_seconds_total))` | 10 | `nproc` = 10 ✓ |
| `node_network_receive_bytes_total` | `device="eth0"` و`lo` فقط | شبكة الحاوية (Q‑9) |
| عدد العيّنات | 357 | < 3000 (NFR‑5) ✓ |
| `process_resident_memory_bytes` | 15.75 MiB | السقف 64m = 4.1× |

### 3‑ج. وظيفتا الكشط، والاستعلامات الإضافيّة، وأسماء المقاييس المثبّتة (AC‑3.4)

```yaml
  # Monitoring plan phase 2 (docs/delivery/monitoring-host-postgres/design.md).
  # Ordinary targets -- no `tier: optional` -- so AizzakScrapeTargetDown
  # covers both the moment either exporter dies.
  # `node`: disk/CPU/memory are the HOST's; network is the exporter
  # container's own namespace (Q-9) -- not host traffic.
  - job_name: node
    static_configs:
      - targets: ["node-exporter:9100"]

  # `postgres`: Postgres ITSELF, as opposed to `pgbouncer` above, whose
  # `pgbouncer_up` is the pooler's admin console and stays 1 when the server
  # behind it is gone.
  - job_name: postgres
    static_configs:
      - targets: ["postgres-exporter:9187"]
```

**`deploy/postgres-exporter/queries.yaml`**: نُفِّذ استعلاماه على Postgres 16.14 الحيّ للقراءة، وحمّلهما المُصدِّر التجريبيّ بلا خطأ.

```yaml
# Two numbers no built-in collector of postgres_exporter v0.20.1 has
# (docs/delivery/monitoring-host-postgres/design.md §3-ج). Every column is a
# GAUGE; NO column is a LABEL -- no query text, no queryid, no user (NFR-5).
# `--extend.query-path` is deprecated upstream and still works in the pinned
# version; check it first on any exporter upgrade.
pg_lock_wait:
  query: |
    SELECT COALESCE(max(EXTRACT(EPOCH FROM clock_timestamp() - l.waitstart)), 0)::float8 AS longest_seconds,
           count(DISTINCT l.pid)::float8 AS sessions
    FROM pg_catalog.pg_locks l
    JOIN pg_catalog.pg_stat_activity a ON a.pid = l.pid
    WHERE NOT l.granted AND l.waitstart IS NOT NULL AND a.datname = current_database()
  master: true
  metrics:
    - longest_seconds:
        usage: "GAUGE"
        description: "Seconds the longest-waiting lock request in this database has waited (pg_locks.waitstart, PG14+); 0 when none"
    - sessions:
        usage: "GAUGE"
        description: "Sessions in this database currently waiting on a lock"
pg_archive_ready:
  query: |
    SELECT count(*)::float8 AS segments,
           COALESCE(EXTRACT(EPOCH FROM clock_timestamp() - min(modification)), 0)::float8 AS oldest_age_seconds
    FROM pg_catalog.pg_ls_archive_statusdir()
    WHERE name LIKE '%.ready'
  master: true
  metrics:
    - segments:
        usage: "GAUGE"
        description: "WAL files completed and not yet archived (.ready in archive_status)"
    - oldest_age_seconds:
        usage: "GAUGE"
        description: "Age of the oldest .ready file; 0 when none -- a quiet cluster is not a stalled one"
```

لماذا `a.datname = current_database()`: انتظار القفل على `aizzak_test` (اختبارات التكامل على العنقود نفسه) لا يُحسب على المنصّة. واقتران أقفال `transactionid` (`database IS NULL`) بالقاعدة يأتي من `pg_stat_activity`.

**الأسماء المثبّتة لـAC‑3.4.** المُصدِّر القديم يضيف تسمية `server="postgres:5432"` لمقاييس `queries.yaml`، ولا يضيفها لـ`pg_up`. وهذا مقيس.

| AC‑3.4 | المقياس | المصدر |
|---|---|---|
| 1 حياة الخادم | `pg_up` | المُصدِّر نفسه |
| 2 الأقفال حسب النوع | `pg_locks_count{datname,mode}` | جامع `locks` (افتراضيّ) |
| 3 أطول انتظار قفل | `pg_lock_wait_longest_seconds` (و`pg_lock_wait_sessions`) | `queries.yaml` |
| 4 أطول معاملة أو استعلامٍ نشط | `pg_stat_activity_max_tx_duration{datname,state,…}`، ويُقرأ بـ`max(…{state="active"})` | جامع `stat_activity` |
| 5 حجم القاعدة | `pg_database_size_bytes{datname}` | جامع `database` |
| 6 حجم كلّ جدول | `pg_stat_user_tables_table_size_bytes{datname,schemaname,relname}` (و`_index_size_bytes`) | جامع `stat_user_tables` |
| 7 حالة الأرشفة | `pg_archive_ready_segments` و`pg_archive_ready_oldest_age_seconds` (`queries.yaml`)، و`pg_stat_archiver_archived_count` و`pg_stat_archiver_last_archive_age` (جامع `stat_archiver`) | — |
| `د‑15` (الانتفاخ) | `pg_stat_user_tables_n_dead_tup` و`n_live_tup` و`last_autovacuum` | جامع `stat_user_tables` |

**تقدير العيّنات (NFR‑5):** ‏21 مقياساً × 43 جدولاً (مقيس: `count(*) FROM pg_stat_user_tables` = 43) = 903، ثمّ:
- `settings` ≈ 300
- `stat_activity` ≈ 200
- `stat_database` ≈ 125
- `locks` 40
- `roles` ≈ 25
- الباقي ≈ 60

المجموع ≈ **1,650 < 3,000**. و`statio_user_tables` (‏8 × 43 = 344) مطفأٌ عمداً لأنّ FR‑10 لا يطلبه. و`stat_statements` مطفأ (AC‑3.7)، وهو افتراضاً مطفأ، والعلم صريحٌ ليحرسه اختبار.

### 3‑د. القواعد الأربع (PromQL نهائيّ ومُجرَّب بـpromtool)

```yaml
  # ── The host (monitoring plan phase 2, row 1) ──────────────────────────
  - name: aizzak-host
    rules:
      - alert: AizzakHostDiskHigh
        expr: >-
          1 - node_filesystem_avail_bytes{job="node", mountpoint="/", fstype!~"tmpfs|overlay|9p"}
            / node_filesystem_size_bytes{job="node", mountpoint="/", fstype!~"tmpfs|overlay|9p"}
          > 0.80
        for: 10m
        labels:
          severity: warning
        annotations: {…}   # below

  # ── Postgres itself (monitoring plan phase 2, row 2) ───────────────────
  - name: aizzak-postgres
    rules:
      - alert: AizzakPostgresDown
        expr: pg_up{job="postgres"} == 0
        for: 30s
        labels:
          severity: critical
      - alert: AizzakPostgresLockWaitHigh
        expr: pg_lock_wait_longest_seconds{job="postgres"} > 60
        for: 30s
        labels:
          severity: warning
      - alert: AizzakPostgresArchiveStalled
        expr: pg_archive_ready_oldest_age_seconds{job="postgres"} > 900
        for: 1m
        labels:
          severity: critical
```

**الحقول الخمسة لكلّ قاعدة.** المضمون مُلزِم؛ والصياغة الإنجليزيّة للمنفّذ، على نمط ما حولها. و`runbook_url` = `https://github.com/anastawahia/aizzak/blob/master/docs/runbooks/alerts.md#<الاسم بحروفٍ صغيرة>`.

| القاعدة | `summary` | `description` | `reason` (لماذا الرقم و`for`) | `response` (أوّله أمرٌ للقراءة) |
|---|---|---|---|---|
| `AizzakHostDiskHigh` | `The host's root filesystem is {{ $value \| humanizePercentage }} full` | يشمل `/` جذرَ بيانات Docker كلّه: Postgres ومِبْولة WAL وPrometheus وLoki. Postgres يتوقّف عن الكتابة عند 100%. **قرص Windows (`C:`، `/mnt/c`) غير مراقَب (Q‑2)، وملفّ القرص الافتراضيّ عليه قد يمتلئ أوّلاً.** | 80% رقم الخطّة. ‏`1 - avail/size`: الكتل المحجوزة للجذر تُعدّ مستعملة لأنّ Postgres يعمل بـuid 999 ولا يكتب فيها. ‏`for: 10m` (Q‑7): القرص يمتلئ بالساعات، والعشر دقائق تتجاهل قفزة مِبْولة نسخةٍ أساسيّة. واستبعاد `tmpfs\|overlay\|9p` في القاعدة حزامٌ فوق استبعاد المُصدِّر. | `df -h /`، ثمّ `docker system df`، ثمّ `docker compose exec -T ops-scheduler python -m app.ops.backup status` لعمق المِبْولة. **لا `docker volume prune` ولا `docker system prune` ولا `docker compose down -v`.** |
| `AizzakPostgresDown` | `postgres-exporter cannot reach Postgres -- every data path is down` | المُصدِّر حيّ ولا يدخل الخادم منذ 30 ث. `AizzakPgbouncerDown` **لا يرى هذا**، لأنّ لوحة الإدارة يجيبها المُجمِّع نفسه. | ‏`== 0` على حكم المُصدِّر لا على `up` (منطق `AizzakPgbouncerDown`). ‏30 ث: إعادة تشغيلٍ مقصودة لعنقودٍ سليم تنتهي داخلها، وهي `for` الخاصّة بـ`AizzakRedisStreamDown`. | `docker compose ps postgres`. إن كان يعمل: `docker compose logs --since 10m postgres-exporter`. سطر `password authentication failed for user "metrics_exporter"` يعني الدور أو كلمة السرّ لا الخادم، والعلاج 08 §3.3‑ج الخطوة ②. **إعادة تشغيل `postgres` بيد البشري وحده.** |
| `AizzakPostgresLockWaitHigh` | `A session has waited on a lock for over a minute` | جلسةٌ تنتظر قفلاً أكثر من دقيقة في قاعدة المنصّة. السبب عادةً معاملةٌ مفتوحة أو `ALTER`/`VACUUM FULL` يدويّ. | ‏> 60 ث رقم الخطّة. ‏`pg_locks.waitstart` يقيس الانتظار نفسه، لا عمر المعاملة. ‏`for: 30s` يجعل الحدّ الفعليّ 60–90 ث، فلا تُشعله عيّنة واحدة. | `SELECT pid, pg_blocking_pids(pid), wait_event, now()-xact_start, left(query,120) FROM pg_stat_activity WHERE wait_event_type = 'Lock';`. ‏`pg_cancel_backend` قرار بشريّ. والإسكات لعملٍ مخطّط من Grafana، لا تعديل الحدّ. |
| `AizzakPostgresArchiveStalled` | `A finished WAL segment has waited over 15 minutes to be archived` | مقاطع `.ready` تتراكم: الخادم لا ينسخ WAL إلى المِبْولة. القرص يمتلئ، والاسترجاع إلى نقطةٍ يقف عند آخر مقطعٍ مؤرشف. | **مبنيٌّ على `.ready` لا على `failed_count`**، فهذا مقيسٌ أنّه يبقى 0 حين يتعذّر تنفيذ `archive_command` (`src/app/ops/backup.py:463-475`). **ولا على `last_archive_age`**، فعنقودٌ هادئ لا يُكمل مقاطع (مقيس 2026‑10‑05: آخر أرشفة 12:13 قبل ست ساعات، و`.ready` = 0). ‏900 ث = 3 × `archive_timeout` (Q‑7). `critical` (Q‑7). ‏`for: 1m` يمتصّ قفزة ساعة WSL2. | `SELECT * FROM pg_stat_archiver;`، ثمّ `docker compose logs --since 30m postgres \| grep -i "archive command failed"`، ثمّ `docker compose exec -T postgres ls -ld /var/lib/postgresql/wal-archive`، ثمّ `df -h /`. |

**وفي الدليل (`docs/runbooks/alerts.md`):**
- لكلّ قسمٍ «ماذا يعني»، وخطواتٌ مرقّمة أوّلها أمرٌ للقراءة (كما في الجدول)، و«كيف تعرف أنّه زال»، وصفٌّ في الفهرس.
- قسم القرص يحمل تحذيراً مؤطّراً من الأوامر الثلاثة.
- قسم الحياة يذكر أسطر السجلّ المتوقَّعة (§3‑أ).
- وسطرٌ في `AizzakOpsTaskOverdue`: «عمر آخر نسخةٍ احتياطيّة هو `aizzak_ops_task_last_success_timestamp_seconds{task="backup"}`، وهذا التنبيه يغطّيه (Q‑5)».

### 3‑هـ. ميزانيّة الذاكرة: أيّ سقفٍ يُخفض، وبأيّ دليل (Q‑1)

**المطلوب:**
- المجموع اليوم 58,976 MiB، والسقف (64 − 6.4) = 58,982.4 MiB، فالهامش 6.4 MiB.
- المُصدِّران يحتاجان 64 + 128 = 192 MiB.
- فلا بدّ من تحرير ≥ 185.6 MiB، مع هامشٍ معلن.

**الاستعلام** (Prometheus الحيّ، للقراءة؛ تغطية cAdvisor = `sum_over_time(up{job="cadvisor"}[15d]) * 15 / 3600` = **93.3 ساعة** في 15 يوماً، وتشمل تشغيلات k6، إذ ذروة `k6` 2,035.8 MiB):

```promql
max by (container_label_com_docker_compose_service) (
  max_over_time(container_memory_working_set_bytes{container_label_com_docker_compose_project="aizzak"}[15d]))
```

| الخدمة | السقف (MiB) | ذروة 15 يوماً (MiB) | الحكم |
|---|---|---|---|
| **`redis-cache`** | 2048 | **21.0** (cAdvisor). ومن `redis_exporter` الدائم، 15 يوماً كاملة: `max_over_time(redis_memory_used_rss_bytes{job="redis-cache"}[15d])` = **19.6**، و`used` = 17.3، و`redis_memory_max_bytes` = **1024**. ‏`docker stats` الآن 3.97 | **يُخفض إلى 1792.** حدّه الأعلى يرسمه `--maxmemory 1gb` (إعداد، لا حمل). و`--save ""` و`--appendonly no` يعنيان لا `fork` ولا نسخ‑عند‑الكتابة. يبقى 768 MiB (75%) فوق `maxmemory` للتجزّؤ ومخازن العملاء، أي 91× الذروة المقيسة. وتعليق الخدمة نفسه يقول «2 GB would buy nothing measurable» |
| `prometheus` | 2048 | 214.8 | ممنوع بقرار المالك (سيزيد سلاسل) |
| `loki` | 1024 | **1020.8** | عند السقف، لا |
| `grafana` | 512 | **511.7** | عند السقف، لا |
| `alert-sink` | 32 | **31.1** | عند السقف، لا (ملاحظة في §7) |
| `qdrant` | 8192 | 7519.2 | لا (`ح‑3`، مبادلة Qdrant) |
| `postgres` | 18432 | 2877.2 | سقفٌ مشتقّ من مقابض 2.1، لا يُمسّ |
| `minio` | 2048 | 797.7 | يتبع الرفع المتزامن، أقلّ وضوحاً |
| `ops-scheduler` | 1024 | 183.2 | ذروة `backup full` تكبر مع القاعدة («لم يُقَس» في 08 §2‑ز) |
| `ollama-bridge` | 256 | 5.9 | يحرّر 192 على الأكثر (إلى 64m)، فيبقى الهامش 6.4 وحده: لا يكفي |
| `app` ×3 | 2048×3 | 1033.5 | خفضٌ لكلّ نسخةٍ بنسبة ×3، وذروته نصف سقفه |

**النتيجة** (`deploy/resource-budget.sh --host-cpus 32 --host-memory-gb 64` يجب أن يطبعها):

| | قبل | بعد |
|---|---|---|
| الخدمات الدائمة | 27 | **29** |
| المعالج | 35.60 | **36.10** (1.13×، `OVERSUBSCRIBED` مُبلَّغ لا فاشل) |
| الذاكرة (MiB) | 58,976 | **58,912** = 58,976 − 256 + 64 + 128 |
| الذاكرة (GB، كما في الدفتر) | 57.59 | **57.53** |
| الهامش تحت 57.60 | 6.4 MiB | **70.4 MiB** |

السقفان مشتقّان من القياس:
- `node-exporter` RSS 15.75 MiB ← 64m = 4.1×.
- `postgres-exporter` RSS 17.0 MiB على العنقود التجريبيّ ← 128m = 7.5×. ‏128 لا 64، لأنّ عيّنات العنقود الحيّ أكثر بـ2.6× (1,650 مقابل 629)، ولأنّه سقف أخيه `pgbouncer-exporter` (ذروته 17.0). وAC‑9.5 يقيسه حيّاً بعد ساعة.

### 3‑و. دفتر الاتّصالات (AC‑8.3)

- المُصدِّر يفتح **مسبحين بـ`SetMaxOpenConns(1)`**: المُصدِّر القديم (`exporter/server.go:79`) وجامعات `collector/instance.go:58`. هذا مقروءٌ في مصدر v0.20.1.
- و`CONNECTION LIMIT 2` على الدور يجعل الحدّ مفروضاً من الخادم لا وعداً.
- القرار: **يُعدّ.** `_POSTGRES_EXPORTER_BACKENDS = 2` يُضاف إلى مجموع الخلفيّات المباشرة في `test_connection_budget.py`. المنفّذ يضيف دالّةً صغيرة `_postgres_backends(topology) = _server_backends(topology, _pool_ceiling()) + _POSTGRES_EXPORTER_BACKENDS` ويستعملها في `test_the_pooler_cannot_ask_postgres_for_more_backends_than_it_has` وفي حساب الدفتر. أمّا `_server_backends` فتبقى «ما يفتحه المُجمِّع» كما يقول اسمها.
- الدفتر: ‏`compose.postgres_backends = 164` من 297 (كانت 162).
- الحارس يُمدَّد في `test_the_admin_sessions_this_ledger_counts_still_exist` بشرطين:
  - `DATA_SOURCE_URI` يبدأ بـ`postgres:5432/`.
  - `15-metrics-exporter.sh` يحوي `CONNECTION LIMIT 2`.
- المُصدِّر لا يمرّ بالمُجمِّع، فـ`pooler_clients` لا يتغيّر (433).

### 3‑ز. حياد بصمة `postgres` (Q‑6): البرهان

- نسختان من `docker-compose.yml`: الأصل، والمعدَّل بالكتل أعلاه حرفيّاً.
- الأمر `docker compose -p aizzak -f <file> --project-directory /home/AIZZAK --env-file <.env.example + المتغيّر> config --hash '*'`.
- الفرق بين المخرجَين **ثلاثة أسطر فقط**:

```text
> node-exporter      ad2b6b28…   (جديد)
> postgres-exporter  658b2f6a…   (جديد)
< redis-cache        7417427d…
> redis-cache        3a94a25f…   (السقف 1792m)
postgres             81ccbe60…   = 81ccbe60…   (لم تتغيّر)
prometheus           لم تتغيّر    (تغيُّر محتوى ملفٍّ مربوط لا يدخل البصمة)
```

- وعلى الحزمة الحيّة اليوم: `docker inspect … com.docker.compose.config-hash` لـ`postgres` = `9458ab5d…` = `docker compose config --hash postgres`. **لا انحراف قائم**، فالميزة لا تُدخل انحرافاً جديداً. أي أنّ `docker compose up -d` عامّاً بعد الدمج **لا يُعيد إنشاء `postgres`**. ويُعيد إنشاء `redis-cache` إن لم تُنفَّذ الخطوة ④ في §6‑ب، وهذا بلا ضرر: تعود فارغة كما صُمّمت.

### التواقيع

```text
# Compose
service node-exporter        image prom/node-exporter:v1.12.1                  expose 9100   limits 0.25 / 64m
service postgres-exporter    image prometheuscommunity/postgres-exporter:v0.20.1  expose 9187   limits 0.25 / 128m
volume  node-exporter-probe
env     METRICS_EXPORTER_PASSWORD        (.env; consumed ONLY by postgres-exporter and by the role script run by hand)
env     TEST_DATABASE_URL_METRICS_EXPORTER   (.env.test; the live test's own DSN, skip-alone)

# Prometheus
job node      -> node-exporter:9100        job postgres -> postgres-exporter:9187
rules: AizzakHostDiskHigh(warning,10m) AizzakPostgresDown(critical,30s)
       AizzakPostgresLockWaitHigh(warning,30s) AizzakPostgresArchiveStalled(critical,1m)
custom metrics: pg_lock_wait_longest_seconds, pg_lock_wait_sessions,
                pg_archive_ready_segments, pg_archive_ready_oldest_age_seconds   (label: server)

# Role script  deploy/postgres/initdb/15-metrics-exporter.sh
inputs : POSTGRES_USER (default postgres), POSTGRES_DB (default postgres),
         METRICS_EXPORTER_PASSWORD (optional; empty -> role without password, warning on stderr)
effect : idempotent; exit 0 on success, non-zero on SQL error (ON_ERROR_STOP)
contract: sourced by docker-entrypoint at initdb  -> NO top-level `exit`
          executed by hand:  docker compose exec -T -e METRICS_EXPORTER_PASSWORD postgres \
                               bash /docker-entrypoint-initdb.d/15-metrics-exporter.sh

# Tests (new names)
tests/unit/test_metrics_exporter_role.py
tests/integration/test_metrics_exporter_role_live.py      (pytestmark = pytest.mark.live_db)
tests/unit/test_connection_budget.py::_POSTGRES_EXPORTER_BACKENDS = 2
```

لا عميل API يُعاد توليده، فلا واجهة أماميّة.

## 4. البيانات

**لا جداول ولا أعمدة ولا فهارس ولا ترحيل Alembic ولا تغيير RLS.** التغيير الوحيد دورُ عنقودٍ جديد، يُنشئه سكربت `initdb` على حجمٍ جديد، وأمرٌ بشريّ على العنقود القائم. السكربت مُجرَّب على `postgres:16` مؤقّت: نجح عند `initdb` بلا كلمة سرّ، ثمّ مرّتين يدويّاً بكلمة سرّ، بمخرجٍ 0 وخصائص لم تتغيّر. والنصّ التالي مُلزِم، ويُكتب فوقه رأس تعليقٍ إنجليزيّ على نمط `10-roles.sh` يشرح ثلاثة أشياء: لماذا ملفٌّ مستقلّ، ولماذا لا كلمة سرّ في بيئة `postgres`، ولماذا لا `exit`.

```bash
#!/bin/bash
# (header comment -- see above)
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
     --username "${POSTGRES_USER:-postgres}" \
     --dbname "${POSTGRES_DB:-postgres}" \
     --set db_name="${POSTGRES_DB:-postgres}" <<-'EOSQL'
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'metrics_exporter') THEN
            CREATE ROLE metrics_exporter LOGIN NOINHERIT CONNECTION LIMIT 2;
        END IF;
    END
    $$;

    -- Re-asserted on every run: a role an operator made by hand may lack any of these.
    ALTER ROLE metrics_exporter LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
        NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
    ALTER ROLE metrics_exporter SET statement_timeout = '5s';
    ALTER ROLE metrics_exporter SET lock_timeout = '1s';
    ALTER ROLE metrics_exporter SET default_transaction_read_only = on;

    GRANT CONNECT ON DATABASE :"db_name" TO metrics_exporter;
    -- PG16: the inherit option is recorded PER MEMBERSHIP (10-roles.sh's
    -- backup_operator lesson). NOINHERIT on the role keeps every FUTURE grant
    -- inert; this one membership is explicitly live.
    GRANT pg_monitor TO metrics_exporter WITH INHERIT TRUE;
EOSQL

if [ -n "${METRICS_EXPORTER_PASSWORD:-}" ]; then
    case "${METRICS_EXPORTER_PASSWORD}" in
        change-me*) echo "15-metrics-exporter: WARNING: placeholder password (change-me-*)" >&2 ;;
    esac
    psql -v ON_ERROR_STOP=1 \
         --username "${POSTGRES_USER:-postgres}" \
         --dbname "${POSTGRES_DB:-postgres}" \
         --set exporter_password="${METRICS_EXPORTER_PASSWORD}" <<-'EOSQL'
        ALTER ROLE metrics_exporter PASSWORD :'exporter_password';
EOSQL
    echo "15-metrics-exporter: metrics_exporter ready (password set)"
else
    echo "15-metrics-exporter: metrics_exporter created WITHOUT a password -- set it with 08 §3.3-ج step ②" >&2
fi
```

**ملاحظات تنفيذ مُلزِمة:**
- **الملفّ يُستورَد (`source`) عند `initdb` لأنّ وضعه 0644**، كما مع `10-roles.sh` (مقيس في `docker-entrypoint.sh:188`). فأيّ `exit` في مستواه الأعلى يُسقط تهيئة العنقود.
- محدِّد الـheredoc الداخليّ `EOSQL` في العمود الأوّل (أو بعد tab).
- القيم الافتراضيّة `:-postgres` ضروريّة. التجربة كشفت أنّ `docker exec` على حاويةٍ بلا `POSTGRES_USER` في بيئتها يُفشل `set -u`. وفي حاويتنا `POSTGRES_USER` و`POSTGRES_DB` مضبوطان، والقيمة الافتراضيّة حزام.

**ما يثبته عنقودُ التجربة** (`postgres:16`، شبكة مؤقّتة):

```text
rolsuper|rolbypassrls|rolcreaterole|rolreplication|rolcreatedb|rolinherit|rolconnlimit
f|f|f|f|f|f|2          rolconfig = {statement_timeout=5s,lock_timeout=1s,default_transaction_read_only=on}
pg_monitor | inherit_option = t           pg_has_role(…,'pg_read_all_stats','USAGE') = t
SELECT 1 FROM workspace.users   -> ERROR: permission denied for schema workspace
INSERT INTO platform.outbox     -> ERROR: permission denied for schema platform
SHOW transaction_read_only      -> on
pg_ls_archive_statusdir()       -> يعمل (عضويّة pg_monitor فعّالة)
كلمة سرٍّ خاطئة                 -> FATAL: password authentication failed for user "metrics_exporter"
التشغيل الثاني                  -> NOTICE: role … already been granted membership … ; exit 0
```

- **المستأجرون/RLS:** الدور لا يملك `USAGE` على أيّ مخطّط مستأجر، ولا `SELECT` على أيّ جدول. وهو ليس `BYPASSRLS`. ويرى **نصّ استعلامات الجلسات الأخرى** عبر `pg_read_all_stats`، وهذا جزءٌ من `pg_monitor` كما نصّ عليه FR‑6. والمُصدِّر لا يصدّر هذا النصّ تسميةً: `stat_statements` مطفأ، و`stat_activity` لا يختار عمود `query`، و`queries.yaml` بلا `LABEL`. للمراجعة الأمنيّة، وليس صلاحيّةً فوق `pg_monitor`.
- **التوافق:** إضافيّ بالكامل. لا شيء قائم يقرأ الدور أو يعتمد عليه.
- **التراجع:** `docker compose stop node-exporter postgres-exporter`، ثمّ `DROP ROLE metrics_exporter;` بصلاحيّة المستخدم الخارق. هذا اختياريّ، فالدور قراءةٌ فقط ولا يملك شيئاً، و`DROP ROLE` ينجح بلا `REASSIGN`.

## 5. خطة الاختبار المقترحة

### 5‑أ. حالات promtool المضافة إلى `alerts.test.yml`

هذه الحالات مُجرَّبة كما هي، وكلّها `SUCCESS`. وتُكتب بأسلوب الملفّ: اسمٌ يصف الشكل، وتعليق القسم `# ── aizzak-host ──` و`# ── aizzak-postgres ──`.

```yaml
  # ── aizzak-host ──────────────────────────────────────────────────────────
  - name: the root filesystem at 85% past ten minutes fires once, with its mountpoint
    interval: 15s
    input_series:
      - series: 'node_filesystem_avail_bytes{job="node",instance="node-exporter:9100",device="/dev/sdd",fstype="ext4",mountpoint="/"}'
        values: "15e9x60"
      - series: 'node_filesystem_size_bytes{job="node",instance="node-exporter:9100",device="/dev/sdd",fstype="ext4",mountpoint="/"}'
        values: "100e9x60"
    alert_rule_test:
      - eval_time: 9m
        alertname: AizzakHostDiskHigh
        exp_alerts: []
      - eval_time: 11m
        alertname: AizzakHostDiskHigh
        exp_alerts:
          - exp_labels: {severity: warning, job: node, instance: "node-exporter:9100", device: /dev/sdd, fstype: ext4, mountpoint: /}

  - name: 79 percent, the Windows drive, an overlay and a zero-size mount are silent
    interval: 15s
    input_series:
      - series: 'node_filesystem_avail_bytes{job="node",instance="node-exporter:9100",device="/dev/sdd",fstype="ext4",mountpoint="/"}'
        values: "21e9x60"
      - series: 'node_filesystem_size_bytes{job="node",instance="node-exporter:9100",device="/dev/sdd",fstype="ext4",mountpoint="/"}'
        values: "100e9x60"
      - series: 'node_filesystem_avail_bytes{job="node",instance="node-exporter:9100",device="C:",fstype="9p",mountpoint="/mnt/c"}'
        values: "1e9x60"
      - series: 'node_filesystem_size_bytes{job="node",instance="node-exporter:9100",device="C:",fstype="9p",mountpoint="/mnt/c"}'
        values: "100e9x60"
      - series: 'node_filesystem_avail_bytes{job="node",instance="node-exporter:9100",device="overlay",fstype="overlay",mountpoint="/"}'
        values: "1e9x60"
      - series: 'node_filesystem_size_bytes{job="node",instance="node-exporter:9100",device="overlay",fstype="overlay",mountpoint="/"}'
        values: "100e9x60"
      - series: 'node_filesystem_avail_bytes{job="node",instance="node-exporter:9100",device="none",fstype="ext4",mountpoint="/"}'
        values: "0x60"
      - series: 'node_filesystem_size_bytes{job="node",instance="node-exporter:9100",device="none",fstype="ext4",mountpoint="/"}'
        values: "0x60"
    alert_rule_test:
      - eval_time: 15m
        alertname: AizzakHostDiskHigh
        exp_alerts: []

  # ── aizzak-postgres ──────────────────────────────────────────────────────
  - name: a dead server behind a live exporter fires within the minute
    interval: 15s
    input_series:
      - series: 'pg_up{job="postgres",instance="postgres-exporter:9187"}'
        values: "0x10"
      - series: 'up{job="postgres",instance="postgres-exporter:9187"}'
        values: "1x10"
    alert_rule_test:
      - eval_time: 15s
        alertname: AizzakPostgresDown
        exp_alerts: []
      - eval_time: 45s
        alertname: AizzakPostgresDown
        exp_alerts:
          - exp_labels: {severity: critical, job: postgres, instance: "postgres-exporter:9187"}
      - eval_time: 45s
        alertname: AizzakScrapeTargetDown
        exp_alerts: []

  - name: a dead exporter is a scrape failure, not a dead server
    interval: 15s
    input_series:
      - series: 'up{job="postgres",instance="postgres-exporter:9187"}'
        values: "0x10"
    alert_rule_test:
      - eval_time: 45s
        alertname: AizzakScrapeTargetDown
        exp_alerts:
          - exp_labels: {severity: warning, job: postgres, instance: "postgres-exporter:9187"}
      - eval_time: 45s
        alertname: AizzakPostgresDown
        exp_alerts: []

  - name: a dead node exporter fires, a 15-second blip on the postgres one does not
    interval: 15s
    input_series:
      - series: 'up{job="node",instance="node-exporter:9100"}'
        values: "0x10"
      - series: 'up{job="postgres",instance="postgres-exporter:9187"}'
        values: "0 0 1x8"
    alert_rule_test:
      - eval_time: 45s
        alertname: AizzakScrapeTargetDown
        exp_alerts:
          - exp_labels: {severity: warning, job: node, instance: "node-exporter:9100"}

  - name: a server that answers is silent
    interval: 15s
    input_series:
      - series: 'pg_up{job="postgres",instance="postgres-exporter:9187"}'
        values: "1x10"
    alert_rule_test:
      - eval_time: 2m
        alertname: AizzakPostgresDown
        exp_alerts: []

  - name: a lock wait past a minute that persists fires
    interval: 15s
    input_series:
      - series: 'pg_lock_wait_longest_seconds{job="postgres",instance="postgres-exporter:9187",server="postgres:5432"}'
        values: "70+15x10"
    alert_rule_test:
      - eval_time: 15s
        alertname: AizzakPostgresLockWaitHigh
        exp_alerts: []
      - eval_time: 45s
        alertname: AizzakPostgresLockWaitHigh
        exp_alerts:
          - exp_labels: {severity: warning, job: postgres, instance: "postgres-exporter:9187", server: "postgres:5432"}

  - name: a 50-second wait and a one-sample 70 are silent
    interval: 15s
    input_series:
      - series: 'pg_lock_wait_longest_seconds{job="postgres",instance="postgres-exporter:9187",server="postgres:5432"}'
        values: "50x10"
      - series: 'pg_lock_wait_longest_seconds{job="postgres",instance="postgres-exporter:9187",server="other:5432"}'
        values: "0 0 70 0 0 0 0 0"
    alert_rule_test:
      - eval_time: 45s
        alertname: AizzakPostgresLockWaitHigh
        exp_alerts: []
      - eval_time: 2m
        alertname: AizzakPostgresLockWaitHigh
        exp_alerts: []

  - name: a segment stuck past fifteen minutes fires, whatever failed_count says
    interval: 15s
    input_series:
      - series: 'pg_archive_ready_oldest_age_seconds{job="postgres",instance="postgres-exporter:9187",server="postgres:5432"}'
        values: "880+15x20"
      - series: 'pg_archive_ready_segments{job="postgres",instance="postgres-exporter:9187",server="postgres:5432"}'
        values: "3x20"
      - series: 'pg_stat_archiver_failed_count{job="postgres",instance="postgres-exporter:9187"}'
        values: "0x20"
    alert_rule_test:
      - eval_time: 1m
        alertname: AizzakPostgresArchiveStalled
        exp_alerts: []
      - eval_time: 2m
        alertname: AizzakPostgresArchiveStalled
        exp_alerts:
          - exp_labels: {severity: critical, job: postgres, instance: "postgres-exporter:9187", server: "postgres:5432"}

  - name: quiet and healthy clusters are silent however old the last archive is
    interval: 15s
    input_series:
      - series: 'pg_archive_ready_oldest_age_seconds{job="postgres",instance="postgres-exporter:9187",server="postgres:5432"}'
        values: "0x80"
      - series: 'pg_stat_archiver_last_archive_age{job="postgres",instance="postgres-exporter:9187"}'
        values: "21600+15x80"
      - series: 'pg_archive_ready_oldest_age_seconds{job="postgres",instance="postgres-exporter:9187",server="other:5432"}'
        values: "0 5 12 0 3 9 0 2 0 0 4 0 1 0 0 0 0 0 0 0"
    alert_rule_test:
      - eval_time: 20m
        alertname: AizzakPostgresArchiveStalled
        exp_alerts: []
```

### 5‑ب. `tests/unit/test_metrics_exporter_role.py` (جديد، وحدة)

| الاختبار | يحرس |
|---|---|
| `test_the_role_script_sorts_between_the_roles_and_the_extensions` | الاسم بين `10-roles.sh` و`20-extensions.sh`، والوضع غير تنفيذيّ (0644) كأخيه |
| `test_the_role_script_is_safe_to_source` | لا سطر `exit` خارج الدوالّ (يُستورَد عند `initdb`) |
| `test_the_role_is_created_inside_an_existence_check` | `IF NOT EXISTS (… rolname = 'metrics_exporter')` و`CREATE ROLE metrics_exporter LOGIN NOINHERIT CONNECTION LIMIT 2` (AC‑2.1) |
| `test_the_password_is_a_psql_variable_never_shell_interpolated` | `--set exporter_password=` و`PASSWORD :'exporter_password'`، ولا `PASSWORD '$` (AC‑2.1) |
| `test_pg_monitor_with_inherit_is_the_only_grant` | `GRANT pg_monitor TO metrics_exporter WITH INHERIT TRUE`؛ لا `GRANT SELECT\|INSERT\|UPDATE\|DELETE`؛ ولا ظهور لـ`pg_read_all_data` و`pg_write_all_data` و`pg_read_server_files` و`pg_write_server_files` و`pg_execute_server_program`؛ وكلمات `SUPERUSER\|BYPASSRLS\|CREATEROLE\|REPLICATION\|CREATEDB` لا تظهر إلّا مسبوقةً بـ`NO` (AC‑2.1) |
| `test_the_role_is_bounded_server_side` | `statement_timeout = '5s'` و`lock_timeout = '1s'` و`default_transaction_read_only = on` و`CONNECTION LIMIT 2` (AC‑2.5 نصّاً) |
| `test_every_attribute_is_reasserted_so_a_rerun_converges` | `ALTER ROLE metrics_exporter LOGIN NOINHERIT NOSUPERUSER … CONNECTION LIMIT 2` خارج كتلة `DO` (AC‑2.6 شكلاً) |
| `test_the_password_never_enters_the_postgres_service` | `METRICS_EXPORTER_PASSWORD` **غائب** عن `services.postgres.environment`. وdocstring يشرح Q‑6 وبرهان §3‑ز |
| `test_the_exporter_alone_requires_the_password` | `postgres-exporter.environment.DATA_SOURCE_PASS` = `${METRICS_EXPORTER_PASSWORD:?…}`، و`.env.example` فيه `METRICS_EXPORTER_PASSWORD=change-me-` (AC‑2.2/2.3) |
| `test_the_exporter_connects_directly_as_its_own_role` | `DATA_SOURCE_URI` يبدأ بـ`postgres:5432/` لا `pgbouncer`؛ `DATA_SOURCE_USER == "metrics_exporter"`؛ ولا مرجع `*_PASSWORD}` غير متغيّره؛ و`depends_on.postgres.condition == service_healthy` (AC‑3.1) |
| `test_no_per_query_series_can_be_exported` | `--no-collector.stat_statements` في `command`، ولا `--collector.stat_statements`؛ وفي `queries.yaml` كلّ `usage` ∈ {GAUGE, COUNTER} ولا `LABEL`، ولا ذكر لـ`pg_stat_statements` (AC‑3.7) |
| `test_both_new_images_are_pinned_to_a_version` | الوسم يبدأ بـ`v` وليس `latest` (AC‑1.1/3.1) |

### 5‑ج. `tests/integration/test_metrics_exporter_role_live.py` (جديد، `live_db`)

- **من الفهرس، عبر `owner_engine` محلّيّ (نمط `test_backup_live.py:71`). تعمل في job ‏`integration` على حجمٍ جديد**، والدور فيه بلا كلمة سرّ:
  1. `test_the_role_holds_no_dangerous_attribute`: ‏`rolsuper` و`rolbypassrls` و`rolcreaterole` و`rolreplication` و`rolcreatedb` كلّها `false`، و`rolcanlogin`، و`rolconnlimit = 2` (AC‑2.4 رابعاً). إن غاب الدور، فالرسالة تحيل إلى 08 §3.3‑ج.
  2. `test_the_role_is_bounded_by_its_own_settings`: ‏`rolconfig` ⊇ ‏{`statement_timeout=5s`، `lock_timeout=1s`، `default_transaction_read_only=on`} (AC‑2.5).
  3. `test_pg_monitor_is_its_only_membership_and_it_inherits`: عضويّاته = {`pg_monitor`} و`inherit_option = t`، و`pg_has_role('metrics_exporter','pg_read_all_stats','USAGE')`. وهذا بالضبط شرط أن ترى `pg_stat_activity` جلساتِ غيره (AC‑2.4 ثالثاً، فخّ PG16).
  4. `test_it_can_read_no_table_and_write_none`: صفرُ علاقاتٍ خارج `pg_catalog` و`information_schema` و`pg_toast*` يحمل عليها `has_table_privilege('metrics_exporter', c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')`؛ و`has_schema_privilege('metrics_exporter','workspace','USAGE') = f` (AC‑2.4 أوّلاً وثانياً، كتالوجيّاً).
- **بالاتّصال بالدور نفسه** (`TEST_DATABASE_URL_METRICS_EXPORTER`، و`pytest.skip` وحدها إن تعذّر الدخول، كما في `test_the_backup_role_sees_the_rows_a_dump_has_to_carry`):
  5. `SELECT 1 FROM workspace.users` ← `InsufficientPrivilegeError`، و`INSERT INTO platform.outbox …` ← `InsufficientPrivilegeError` (AC‑2.4 حرفيّاً).
  6. مع اتّصال مالكٍ مفتوح: ‏`query <> '<insufficient privilege>'` لجلسة المالك (AC‑2.4 ثالثاً حرفيّاً).
  7. كلّ استعلامٍ في `deploy/postgres-exporter/queries.yaml` يُنفَّذ بالدور وينجح تحت `statement_timeout`.

### 5‑د. تعديلات الاختبارات القائمة

- `test_prometheus_alert_rules.py`:
  - `EXPECTED_ALERTS` يُضاف إليه، بعد `AizzakLlmCircuitOpen`:
    ```python
    # Monitoring plan phase 2, rows 1-2 (docs/monitoring-plan.md §1,
    # docs/delivery/monitoring-host-postgres/) -- the first rules about the
    # HOST and about Postgres ITSELF rather than the pooler in front of it.
    # Until then a full disk, a dead server behind a live pgbouncer
    # (`pgbouncer_up` is the admin console's verdict) and a stalled WAL
    # archiver were all silent: none of them had a series to threshold.
    "AizzakHostDiskHigh",
    "AizzakPostgresDown",
    "AizzakPostgresLockWaitHigh",
    "AizzakPostgresArchiveStalled",
    ```
  - وفقرة docstring بالمعنى نفسه، و«a TWENTY-EIGHTH entry»، ونصّ الرسالة «…and the four of monitoring-plan phase 2».
  - حرّاسٌ جدد:
    - `test_the_postgres_liveness_rule_reads_the_exporters_verdict`: ‏`expr.strip() == 'pg_up{job="postgres"} == 0'`، و`critical`، و`30s` (AC‑5.6).
    - `test_the_archive_rule_reads_progress_not_the_failure_counter`: لا `failed_count` ولا `last_archive_age`، وفيه `pg_archive_ready_oldest_age_seconds` و`> 900`، و`critical` (AC‑5.5).
    - `test_the_lock_rule_thresholds_one_minute_of_waiting`.
    - `test_the_disk_rule_watches_root_only`: ‏`mountpoint="/"`، وليس فيه `/mnt/c`، و`> 0.80`، و`10m`، و`warning`.
    - `test_the_rule_script_also_checks_the_scrape_config`: `check config` في `test-rules.sh`.
    - `test_the_backup_age_question_points_at_the_ops_task_rule`: قسم `aizzakopstaskoverdue` في الدليل يحوي `aizzak_ops_task_last_success_timestamp_seconds{task="backup"}` (AC‑5.8).
- `test_observability_stack_wiring.py`:
  - الاسمان في قائمة «لا منفذ».
  - `test_the_host_exporter_reads_no_host_filesystem` (AC‑1.3)، ويحرس:
    - التركيب الوحيد `node-exporter-probe:/host:ro`، فلا مصدر يبدأ بـ`/` ولا `docker.sock`، وكلّ تركيبٍ ينتهي بـ`:ro`.
    - `user == "65534:65534"` و`read_only`.
    - لا `network_mode` ولا `pid` ولا `privileged`.
    - `--path.rootfs=/host` و`--collector.disable-defaults`.
  - `test_the_host_and_postgres_jobs_scrape_their_exporters`: ‏`node` ← `node-exporter:9100`، و`postgres` ← `postgres-exporter:9187`، بلا `labels`.
- `test_resource_budget.py`: ‏`len(standing) == 29` بتعليقٍ: «29 since monitoring-host-postgres: node-exporter (0.25/64m) + postgres-exporter (0.25/128m), paid for by redis-cache 2g -> 1792m (its 15-day peak is 21 MiB under a 1 GiB maxmemory) -> 36.10/57.53, 70 MiB under the ceiling».
- `test_connection_budget.py`: §3‑و.

### 5‑هـ. ربط كلّ معيار قبول داخل النطاق

| المعيار | المستوى | أين / كيف |
|---|---|---|
| AC‑1.1 | وحدة + بوّابة يدويّة دون اتّصال | `test_resource_budget` (الحدود)، و`test_observability_stack_wiring` (لا منفذ ولا profile)، و`test_both_new_images_are_pinned_to_a_version`، و`docker compose --env-file .env.example config --quiet` بيد المنفّذ |
| AC‑1.2 | CI (`test-rules.sh`) + وحدة | `promtool check config` الجديد (مُجرَّب)، و`test_every_scrape_target_names_a_service_that_exists`، و`test_the_host_and_postgres_jobs_scrape_their_exporters` |
| AC‑1.3 | وحدة | `test_the_host_exporter_reads_no_host_filesystem` |
| AC‑1.4 | حيّ: البشري | §6‑ب ⑥ |
| AC‑1.5 | حيّ: البشري | §6‑ب ⑥. وقيس في التصميم: تطابقٌ تامّ (§3‑ب) |
| AC‑1.6 | حيّ: البشري | §6‑ب ⑥. وقيس في التصميم: `/` وحده (§3‑ب) |
| AC‑2.1 | وحدة | §5‑ب. *القراءة:* الدور في `15-metrics-exporter.sh` لا في `10-roles.sh` (القرار ق‑2 في §7) |
| AC‑2.2 | وحدة | §5‑ب. *القراءة:* المتغيّر في `.env.example` و`.env.test.example` وبيئة المُصدِّر و`--set` في السكربت، و**غيابه** عن بيئة `postgres` محروس |
| AC‑2.3 | وحدة + دون اتّصال | الحارس الساكن `:?`. والرسالة مقيسة: `required variable METRICS_EXPORTER_PASSWORD is missing a value: set METRICS_EXPORTER_PASSWORD in .env`، والمنفّذ يعيدها بملفّ بيئةٍ مؤقّت |
| AC‑2.4 | تكامل CI (كتالوجيّ) + تكامل محلّيّ/حيّ (بالاتّصال) | §5‑ج 1–4 في CI، و5–6 يتخطّيان في CI ← **⚠️‑1** |
| AC‑2.5 | تكامل CI | §5‑ج 2 |
| AC‑2.6 | وحدة (شكلاً) + حيّ: البشري | §5‑ب `…reasserted…`، و§6‑ب ② يُشغَّل مرّتين ويُقارن السطر ← **⚠️‑1** |
| AC‑3.1 | وحدة | §5‑ب `…connects_directly…` و`…alone_requires…` |
| AC‑3.2 | وحدة | `test_observability_stack_wiring` (الوظيفة، ولا `tier`، و«لا منفذ») |
| AC‑3.3 | حيّ: البشري | §6‑ب ⑥ |
| AC‑3.4 | حيّ: البشري | §6‑ب ⑥، بأسماء جدول §3‑ج |
| AC‑3.5 | حيّ: البشري | §6‑ب ⑥ (وسقفه `CONNECTION LIMIT 2` من الخادم) |
| AC‑3.6 | حيّ: البشري | §6‑ب ⑧ |
| AC‑3.7 | وحدة | §5‑ب `…per_query_series…` |
| AC‑4.1–4.3 | CI (promtool) | §5‑أ حالتا القرص |
| AC‑4.4 | وحدة | الاختبارات الأربعة القائمة |
| AC‑4.5 | وحدة (المرساة) + مراجعة | `test_every_rule_opens_its_own_runbook_section`؛ والمحتوى يراجعه code-reviewer |
| AC‑5.1–5.4 | CI (promtool) | §5‑أ حالات Postgres |
| AC‑5.5 | وحدة | `test_the_archive_rule_reads_progress_not_the_failure_counter` |
| AC‑5.6 | وحدة | `EXPECTED_ALERTS` وdocstring و`test_the_postgres_liveness_rule_reads_the_exporters_verdict` |
| AC‑5.7 | وحدة (المرساة) + مراجعة | كـAC‑4.5 |
| AC‑5.8 | وحدة | `test_the_backup_age_question_points_at_the_ops_task_rule` |
| AC‑6.1–6.2 | CI (promtool) | §5‑أ «dead exporter» و«dead node exporter / 15‑second blip» |
| AC‑6.3 | وحدة | `test_the_optional_target_is_the_one_behind_a_compose_profile` كما هو |
| AC‑6.4 | حيّ: البشري (اختياريّ) | §6‑ب ⑨ |
| AC‑8.1 | دون اتّصال + وحدة | `deploy/resource-budget.sh --host-cpus 32 --host-memory-gb 64` ← `MEMORY OK 57.53`، و`test_the_script_and_this_module_agree` |
| AC‑8.2 | وحدة | `test_resource_budget` (29، والدفتر 36.10/57.53) |
| AC‑8.3 | وحدة | `test_connection_budget` (164) |
| AC‑8.4 | البوّابات الستّ | `pytest -rs tests/unit tests/architecture tests/eval` فقط على هذا المضيف |
| AC‑8.5 | مراجعة | code-reviewer |
| AC‑9.1 | مراجعة | 08 §3.3‑ج = §6‑ب حرفيّاً |
| AC‑9.2–9.5 | حيّ: البشري | §6‑ب ⑥–⑧ |

## 6. ترتيب التنفيذ والتوازي

### 6‑أ. للمنفّذ (منفّذٌ واحد، والمجموعات المتوازية مُعلَّمة)

0. **قبل أيّ commit يمسّ `docker-compose.yml`:** حسم ⚠️‑2.
1. **∥ أ (الإعداد):**
   - `deploy/postgres/initdb/15-metrics-exporter.sh` و`deploy/postgres-exporter/queries.yaml`.
   - خدمتا Compose، والمجلّد، و`redis-cache`.
   - وظيفتا `prometheus.yml`.
   - `.env.example` و`.env.test.example`.

   ثمّ: `docker compose --env-file .env.example config --quiet` (تخرج بغير 0 حتى يُضاف المتغيّر إلى `.env.example`)، و`deploy/resource-budget.sh --host-cpus 32 --host-memory-gb 64`.
2. **∥ ب (القواعد):** `alerts.yml` و`alerts.test.yml` و`test-rules.sh` ← `deploy/prometheus/test-rules.sh`.
3. **الاختبارات** (بعد 1 و2): الملفّان الجديدان وتعديلات §5‑د، ثمّ `pytest tests/unit tests/architecture tests/eval`.
4. **∥ ج (الوثائق، بعد 1–3):** `alerts.md`، و`08-local-runbook.md` (§2‑ب و§2‑ز و§3.3‑ج و§4.28)، و`monitoring-plan.md`، و`capacity-status.md` مع `capacity-summary.html` معاً، و`quickstart.md`.
5. البوّابات الستّ بالترتيب، ثمّ commit. **لا** `docker compose up` ولا `restart`. ولا `pytest tests/integration` على هذا المضيف دون إذن.

### 6‑ب. إجراء التفعيل (US‑9) بيد البشري بعد الدمج

هذا النصّ يُنسخ إلى 08 §3.3‑ج كما هو. والتنفيذ كلّه من `/home/AIZZAK`.

```bash
cd /home/AIZZAK

# ⓪-أ  المتغيّر في .env (قيمة عشوائيّة 32 حرفاً، لا تُطبع). إن نُفّذ هذا قبل الدمج (⚠️-2) فتخطَّ.
grep -q '^METRICS_EXPORTER_PASSWORD=' .env || \
  printf 'METRICS_EXPORTER_PASSWORD=%s\n' "$(python3 -c 'import secrets; print(secrets.token_hex(16))')" >> .env
# ⓪-ب  الطول وحده (المتوقَّع: len=32)
l=$(grep '^METRICS_EXPORTER_PASSWORD=' .env); l=${l#*=}
case "$l" in change-me*) echo "PLACEHOLDER -- fix .env first";; *) echo "len=${#l}";; esac
# ⓪-ج  خطّ الأساس لـAC-9.3: تاريخ إنشاء postgres وبصمته، والمحسوبة تساويها
docker inspect -f '{{.Created}} {{index .Config.Labels "com.docker.compose.config-hash"}}' aizzak-postgres-1
docker compose config --hash postgres

# ①  الشجرة على master بعد الدمج
git switch master && git pull --ff-only
docker compose config --hash postgres     # ⇐ يجب أن تبقى مساويةً للبصمة في ⓪-ج

# ②  الدور وكلمة سرّه: السكربت نفسه الذي يشغّله initdb (المجلّد مربوطٌ مجلّداً فالملفّ ظاهرٌ بلا إعادة إنشاء)
METRICS_EXPORTER_PASSWORD="$(sed -n 's/^METRICS_EXPORTER_PASSWORD=//p' .env)" \
  docker compose exec -T -e METRICS_EXPORTER_PASSWORD postgres \
  bash /docker-entrypoint-initdb.d/15-metrics-exporter.sh
#    مرّةً ثانيةً بالأمر نفسه (AC-2.6): يخرج بـ0، ثمّ يُقارَن هذا السطر بعد كلّ مرّة:
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
  "SELECT rolsuper, rolbypassrls, rolcreaterole, rolreplication, rolconnlimit, rolconfig FROM pg_roles WHERE rolname = '\''metrics_exporter'\''"'
#    المتوقَّع: f|f|f|f|2|{statement_timeout=5s,lock_timeout=1s,default_transaction_read_only=on}

# ③  المُصدِّران: صورٌ جاهزة لا تُبنى، فلا "docker compose build" (فخّ ن-4 لا ينطبق)
docker compose up -d --no-deps node-exporter postgres-exporter
docker compose ps node-exporter postgres-exporter         # healthy

# ④  (اختياريّ، في النافذة نفسها) redis-cache بسقفه الجديد: يعود فارغاً، وهذا تصميمه (--save "")
docker compose up -d --no-deps redis-cache

# ⑤  Prometheus: إعادة إنشاء لا reload. prometheus.yml وalerts.yml مربوطان ملفّاً ملفّاً
#    (docker-compose.yml:2515-2516)، والحاوية تمسك inode الملفّ القديم بعد git، فـPOST /-/reload
#    يعيد قراءة النسخة البائتة. كتلة prometheus لم تتغيّر، فالبصمة ثابتة و--force-recreate لازمة.
#    هكذا فعلت 7.3 (08 §4.28).
docker compose up -d --force-recreate --no-deps prometheus

# ⑥  التحقّق بعد دقيقة
Q() { docker compose exec -T prometheus promtool query instant http://127.0.0.1:9090 "$1"; }
Q 'up{job=~"node|postgres"}'                                   # سلسلتان = 1   (AC-9.2 · 1.4 · 3.3)
Q 'pg_up'                                                      # 1             (AC-3.3)
Q 'node_memory_MemTotal_bytes'; grep MemTotal /proc/meminfo     # ×1024 ≤ 1%    (AC-1.5)
Q 'count(count by (cpu) (node_cpu_seconds_total))'; nproc       # متساويان
Q 'node_filesystem_size_bytes'; df -B1 /                       # سلسلةٌ واحدة mountpoint="/" ext4 = حجم df (AC-1.6)
Q 'count by (__name__) ({job="postgres", __name__=~"pg_up|pg_locks_count|pg_lock_wait_longest_seconds|pg_stat_activity_max_tx_duration|pg_database_size_bytes|pg_stat_user_tables_table_size_bytes|pg_archive_ready_segments|pg_archive_ready_oldest_age_seconds|pg_stat_archiver_archived_count"})'   # تسعة أسماء (AC-3.4)
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
  "SELECT count(*) FROM pg_stat_activity WHERE usename = '\''metrics_exporter'\''"'      # ≤ 2 (AC-3.5)
docker inspect -f '{{.Created}}' aizzak-postgres-1             # = قيمة ⓪-ج (AC-9.3)
docker compose config --hash postgres                          # = البصمة في ⓪-ج

# ⑦  بعد 10 دقائق (AC-9.4): لا نتيجة، أو حالةٌ حقيقيّة موثَّقة
Q 'ALERTS{alertname=~"AizzakScrapeTargetDown|AizzakPostgres.*|AizzakHost.*", alertstate="firing"}'

# ⑧  بعد ساعة (AC-3.6 · AC-9.5): تُسجَّل الأرقام في status.md
Q 'count({job="postgres", queryid=~".+"}) or count({job="postgres", query=~".+"}) or vector(0)'   # 0
Q 'max_over_time(scrape_samples_scraped{job=~"node|postgres"}[1h])'                               # < 3000 لكلٍّ
Q 'quantile_over_time(0.95, scrape_duration_seconds{job=~"node|postgres"}[1h])'                   # < 2
docker stats --no-stream aizzak-node-exporter-1 aizzak-postgres-exporter-1                        # < 50% من السقف

# ⑨  (اختياريّ، AC-6.4: يوقف حاويةً حيّة)
docker compose stop postgres-exporter; sleep 75; docker compose logs --since 2m alert-sink | grep AizzakScrapeTargetDown
docker compose start postgres-exporter
```

**تحذيرات مؤطّرة في §3.3‑ج:**
- `docker compose up -d` العامّ **لا** يُعيد إنشاء `postgres` بسبب هذه الميزة (§3‑ز). لكنّه يُعيد إنشاء `redis-cache` إن تُخطّيت ④، وكلّ خدمةٍ أخرى انحرفت لسببٍ آخر. فالأوامر أعلاه بأسماء الخدمات و`--no-deps`.
- **حجمٌ جديد** (مضيفٌ جديد، أو بعد `down -v` بإذن): `initdb` يُنشئ الدور **بلا** كلمة سرّ، فتلزم الخطوة ② مرّةً بعد أوّل `up -d`. وقبلها يشتعل `AizzakPostgresDown`، وسجلّ المُصدِّر يقول `password authentication failed for user "metrics_exporter"`.
- **تدوير كلمة السرّ:** غيّرها في `.env`، ثمّ ②، ثمّ `docker compose up -d --no-deps postgres-exporter` (البيئة تغيّرت، فيُعاد إنشاؤه وحده).

**التراجع:**

```bash
docker compose stop node-exporter postgres-exporter && docker compose rm -f node-exporter postgres-exporter
git revert <merge-commit>          # يعيد prometheus.yml وalerts.yml وسقف redis-cache
docker compose up -d --force-recreate --no-deps prometheus
# اختياريّ، بصلاحيّة المستخدم الخارق:  DROP ROLE metrics_exporter;
# المجلّد aizzak_node-exporter-probe فارغ؛ حذفه (docker volume rm) قرار بشريّ ولا يلزم
```

## 7. المخاطر والقرارات

### بنودٌ تحتاج قراراً بشريّاً

- ⚠️‑1 **AC‑2.4 (نصفه بالاتّصال) وAC‑2.6 لا يتحقّقان في job ‏`integration` كما كُتبا.** على حجمٍ جديد يولد الدور بلا كلمة سرّ (ثمن حياد البصمة، Q‑6)، وCI لا يشغّل خطوة ②، و`.github/workflows/ci.yml` ممنوعٌ بلا إذن.
  - **(أ) — التوصية:** نقبل التحقّق كما في §5‑ج. الحقائق الأربع تُثبَت في CI من الفهرس بالدوالّ التي يستعملها Postgres نفسه للحكم: `has_table_privilege` و`has_schema_privilege` و`pg_has_role(…,'USAGE')` و`inherit_option` و`rolconfig`. وجزء «الاتّصال بالدور» يتخطّى وحده في CI، ويعمل محلّيّاً على الحاضنة، ويُثبته حيّاً AC‑3.3 (`pg_up = 1`) و§6‑ب ⑥. ويُعاد وسم AC‑2.6 «[حيّ — البشري]»: ② تُشغَّل مرّتين وتُقارن. وقد جرّبتُ السكربت مرّتين والاستعلامين على عنقودٍ مؤقّت وعلى PG 16.14 الحيّ، فالخطر المتبقّي صغير.
  - **(ب):** إذنٌ بأربعة أسطر في `ci.yml`. بعد خطوة «Provision»، يُشغَّل `docker compose exec -T -e METRICS_EXPORTER_PASSWORD=change-me-metrics-exporter postgres bash /docker-entrypoint-initdb.d/15-metrics-exporter.sh` مرّتين، ويُضاف `TEST_DATABASE_URL_METRICS_EXPORTER=…${METRICS_EXPORTER_PASSWORD}…` إلى `.env.test` المولَّد. فيتحقّق الثلاثة في CI حرفيّاً، ومعها صحّة `queries.yaml` تحت الدور.

- ⚠️‑2 **شجرة العمل هي مشروع Compose الحيّ.** الفرع `monitoring` مسحوبٌ في `/home/AIZZAK`، وهو المجلّد الذي تقرأ منه الحزمة الحيّة ملفّاتها. والأثران التاليان **مقيسان**:
  - لحظة يُثبَّت تعديل `docker-compose.yml` على الفرع، **يفشل كلّ أمر `docker compose` على هذا المضيف** حتى يحوي `.env` المتغيّر `METRICS_EXPORTER_PASSWORD`. يشمل ذلك `ps` و`logs` و`exec`، والرسالة: `required variable METRICS_EXPORTER_PASSWORD is missing a value`. هذا فشلٌ مغلق لا يضرّ البيانات، لكنّه يعطّل تشغيل البشري. والوكلاء لا يقرؤون `.env` ولا يكتبونه.
  - إعادة تشغيل حاوية `prometheus` قبل التفعيل (إعادة إقلاع WSL مثلاً) تُحمّل الوظيفتين والقواعد الجديدة من الشجرة، فيشتعل `AizzakScrapeTargetDown{job="node"|"postgres"}` (`warning`) حتى التفعيل. هذا بلا ضرر.
  - **(أ) — التوصية:** ينفّذ البشري الخطوة ⓪‑أ من §6‑ب **الآن**، قبل مرحلة الخلفيّة. سطرٌ واحد في `.env` لا تقرؤه أيّ خدمة قائمة، ولا يغيّر أيّ بصمة (§3‑ز)، ويرفع الأثر الأوّل. ويُقبل الأثر الثاني كما هو.
  - **(ب):** يعمل المنفّذ في `git worktree` منفصلة، وتعود `/home/AIZZAK` إلى `master` حتى الدمج.
  - **(ج):** يُقبل تعطّل أوامر Compose حتى التفعيل.

### قرارات اتّخذها التصميم (لا تحتاج قراراً جديداً)

- **ق‑1: السقف المخفوض `redis-cache` 2g ← 1792m.** الدليل في §3‑هـ واضح: حدّه يرسمه `maxmemory` لا الحمل، وذروته 21 MiB. والهامش بعده 70.4 MiB معلن. البدائل مرفوضة بالأرقام في الجدول نفسه.
- **ق‑2: الدور في `15-metrics-exporter.sh` لا في `10-roles.sh`، وكلمة السرّ خارج بيئة `postgres`.** وضعه في `10-roles.sh` يحتاج متغيّراً في بيئة `postgres`، أي بصمةً جديدة وإعادة إنشاء القاعدة عند أوّل `up -d` عامّ (Q‑6 و§3‑ز). والملفّ المستقلّ هو نفسه ما يشغّله `initdb` وما يشغّله البشري، فلا نسخ يدويّاً للـSQL. وقد حمل النسخ اليدويّ في §3.3‑ب خمسة أخطاء.
  - **الثمن:** خطوةٌ يدويّة واحدة على كلّ حجمٍ جديد، مذكورة في `quickstart.md`، وإن نُسيت اشتعل `AizzakPostgresDown` بسببٍ مكتوبٍ في دليله.
  - **البديل المرفوض:** خدمةٌ لمرّةٍ واحدة تحمل كلمة سرّ المستخدم الخارق وتضع كلمة السرّ آليّاً. هذا يوسّع حاملي الاعتماد الخارق، ويخالف نصّ Q‑6 «يُنشأ يدويّاً».
- **ق‑3: لا تركيب لجذر المضيف في `node-exporter`.** مجلّدٌ مُسمّى فارغ بدلاً منه (§3‑أ و§3‑ب). المقيس: `/home/AIZZAK/.env` بوضع **0644**، فـ`/:/host` كان سيضعه في متناول العمليّة، ومعه قرص Windows كلّه. ملاحظةٌ للمراجعة الأمنيّة، خارج النطاق: وضع `.env` نفسه.
- **ق‑4: الشبكة من نطاق الحاوية (Q‑9)**، وتُكتب كذلك في تعليق الخدمة ووظيفة الكشط والدليل.
- **ق‑5: `cap_drop: [ALL]` و`no-new-privileges`** على المُصدِّرَين. هذا جديدٌ في الملفّ، وجُرِّب أنّ كليهما يعمل به.
- **ق‑6: دفتر الاتّصالات يعدّ المُصدِّر** (§3‑و)، وحدّه مفروضٌ بـ`CONNECTION LIMIT 2`.

### مخاطر مسجّلة

- **`--extend.query-path` مهمَل upstream** (§3‑أ). التثبيت يحميه، والترقية تفحصه أوّلاً. وإن تعطّل الاستعلام صمتت قاعدتا القفل والأرشفة صمتَ «لا سلسلة». يكشفه AC‑3.4 حيّاً، ثمّ `pg_exporter_user_queries_load_error` و`pg_exporter_last_scrape_error` في السجلّ والكشط. ولا قاعدة عليهما في هذا النطاق.
- **ساعة WSL2 قد تقفز** بعد نوم المضيف. `for: 1m` على قاعدة الأرشفة يمتصّها، وقاعدة القفل تقيس فرق ساعةٍ واحدة داخل Postgres.
- **ملاحظة قياسٍ خارج النطاق:** ثلاث خدمات بلغت سقفها في 15 يوماً: `alert-sink` ‏31.1/32 MiB، و`loki` ‏1020.8/1024، و`grafana` ‏511.7/512. لم تُمسّ، وتستحقّ نظرةً في إعادة تركيب الدفتر (`د‑26`).
- **ما فعله التصميم على المضيف** (لا شيء منه يمسّ الحزمة):
  - سُحبت صورتا `prom/node-exporter:v1.12.1` و`prometheuscommunity/postgres-exporter:v0.20.1`.
  - شُغّلت حاوياتٌ وشبكةٌ مؤقّتة بأسماء `*-design-probe`، وأُزيلت كلّها مع مجلّداتها المجهولة.
  - نُفّذت استعلامات `SELECT` للقراءة على Postgres الحيّ.

</div>
