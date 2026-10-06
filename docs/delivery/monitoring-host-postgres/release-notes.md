<div dir="rtl">

# ملاحظات الإصدار: مراقبة الجهاز وPostgres (`monitoring-host-postgres`)

- **الإصدار/الوسم:** لا وسم. المرجع المنشور: `master` عند `9da4178` (Merge pull request #1)، دُمج بقرار المالك 2026‑10‑06 بطريقة merge commit.
- **الـ PRs:** [PR #1](https://github.com/anastawahia/aizzak/pull/1)
- **نطاق الالتزامات:** من `81fcda8` (feat(monitoring)) إلى `9da4178`. تضمّ التزامات الميزة (`81fcda8` … `304eaca`، ثمّ إصلاحات المراجعة `22442b2`، `97e4b12`، `a1c9571`، `e1c2868`)، وتوثيق القبول (`ce5d741`، `ee4e353`، `e128a5e`)، وإصلاحات CI وMinIO (`95f1875`، `be62d48`، `c44cc8e`، `23ac1a3`، `720e48a`، `5f72a85`)، ثمّ التراجع عن حدود الإصدارات (`de7d0af`).
- **البيئة:** لا يوجد هدف staging (CLAUDE.md). الحزمة المحلّيّة على هذا الجهاز هي بيئة التطوير والاختبار الحيّ، وهي تعمل بهذه الميزة فعلاً. **بوّابة ③ (الإنتاج) لا تنطبق**: الإنتاج (RunPod أو أيّ نشر عامّ) بيد البشر وحدهم، والموجة 8 مؤجّلة بقرار المالك. لم يُنشر شيء خارج هذا الجهاز.

## ما الجديد
- خدمتان جديدتان: `node-exporter` (مقاييس الجهاز، القرص المراقَب `/` فقط) و`postgres-exporter` (مقاييس Postgres بدور قراءة `metrics_exporter`).
- مهمّتا تجميع جديدتان في Prometheus: `node` و`postgres`.
- أربع قواعد تنبيه جديدة، فصار المجموع 27 قاعدة:
  - `AizzakHostDiskHigh` (القرص، `for: 10m`)
  - `AizzakPostgresDown`
  - `AizzakPostgresLockWaitHigh`
  - `AizzakPostgresArchiveStalled` (مستوى `critical`، 15 دقيقة)
- سكربت initdb جديد `deploy/postgres/initdb/15-metrics-exporter.sh` لإنشاء الدور، مُحصَّن بحيث لا تظهر كلمة السرّ في `pg_stat_statements` ولا في argv ولا في سجلّ الأوامر (إصلاح M‑1).
- اختبارات حارسة وأدلّة تشغيل (runbooks) ودفاتر سجلّ للميزة، وتحديث وثائق العمارة إلى 27 قاعدة.
- لا لوحات Grafana (US‑7 مؤجّلة إلى المرحلة 4 بقرار المالك).

## خطوات النشر
الميزة مُفعَّلة على الحزمة المحلّيّة. لا نشر إنتاج (انظر أعلاه). الخطوات التي نُفّذت على الحزمة المحلّيّة، بالترتيب:
1. إضافة `METRICS_EXPORTER_PASSWORD` إلى `.env` (قيمة عشوائيّة، دون قراءة الملفّ أو طباعتها).
2. إنشاء دور `metrics_exporter` يدويّاً في `postgres` الحيّة (دون إعادة إنشاء `postgres`، قرار Q‑6).
3. `docker compose up -d --no-deps` لـ `node-exporter` و`postgres-exporter` و`prometheus`.
4. تنظيف M‑1 الحيّ (حذف الصفّ المسرِّب من `pg_stat_statements` ثمّ تدوير كلمة السرّ) نفّذه المالك، وبعده أعاد بناء الصور وأنشأ الحاويات من جديد.
5. بعد ذلك استُبدلت صورة MinIO (انظر أدناه) وأعاد المالك إنشاء حاويتها.

إن احتاج أحد إلى تكرار التفعيل على جهاز آخر (تطوير فقط): ينفّذ الدور من `15-metrics-exporter.sh` أو يدويّاً وفق دليل التشغيل، ثمّ `docker compose up -d --no-deps node-exporter postgres-exporter prometheus`. أمّا الإنتاج فيُكتب له إجراء مستقلّ يعتمده الإنسان حين تُفتح الموجة 8.

## الترحيلات وتغييرات الإعدادات
- **ترحيلات Alembic:** لا يوجد. لا جداول جديدة. الدور يُنشأ بسكربت initdb (للتهيئة الجديدة) أو يدويّاً (للقاعدة القائمة)، لذلك لا ترتيب ولا قفل على الجداول.
- **متغيّرات بيئة جديدة (أسماء فقط):**
  - `METRICS_EXPORTER_PASSWORD` (في `.env`، واسمها فقط في `.env.example`)
  - `TEST_DATABASE_URL_METRICS_EXPORTER` (للاختبارات، في `.env.test.example` ويُكتب في CI)
  - في CI أُضيف كذلك كتابة `TEST_DATABASE_URL_PURGER` و`TEST_DATABASE_URL_BACKUP`.
- **`docker-compose.yml`:**
  - خدمتا `node-exporter` و`postgres-exporter` الجديدتان.
  - خُفّض حدّ ذاكرة `redis-cache` إلى 1792m لتوفير ميزانيّة الذاكرة (Q‑1).
  - استُبدلت صورة MinIO بـ `pgsty/minio:RELEASE.2026-08-04T00-00-00Z` وصورة mc بـ `pgsty/mc:RELEASE.2026-09-16T00-00-00Z`، لأنّ `minio/minio` و`minio/mc` لم تعودا تُسحبان (Docker Hub يردّ 404). التغيير نفسه في `deploy/runpod/Dockerfile` و`deploy/backup/restore_drill.sh`. الصيغة على القرص هي نفسها.
- **`docker-compose.test.yml`:** `cpus: "4.0"` لـ postgres (أجهزة GitHub فيها 4 أنوية). الحزمة الحيّة لا تستعمله.
- **`.github/workflows/ci.yml`:** تشغيل سكربت الدور وكتابة DSN الاختبار، وتصحيح اسم خدمة redis، وكتابة DSN الـ purger والـ backup. بُدّلت أيضاً أخطاء ruff 0.16 في اختبارين قديمين، وثُبّت متغيّر `COMPOSE_FILE` في اختبار `test_resource_budget.py`.
- **`CLAUDE.md`:** أكّد المالك تعديله في `09aa3f0` (صلاحيّات تشغيل الحزمة المحلّيّة).
- nginx وVault: لا تغيير.

## التحقق بعد النشر (smoke)
نُفّذت جميعها قراءةً فقط بتاريخ 2026‑10‑06 على الحزمة الحيّة (الأدلّة في «نتيجة staging»):
- [x] الحاويات الخمس (`node-exporter`، `postgres-exporter`، `prometheus`، `postgres`، `minio`) بحالة healthy.
- [x] `up{job=~"node|postgres"}` = 1 للمهمّتين.
- [x] `pg_up` = 1.
- [x] مقياس القرص للجذر `/` موجود.
- [x] 27 قاعدة محمّلة، والأربع الجديدة `inactive`.
- [x] `deploy/prometheus/test-rules.sh` ينتهي بـ `test-rules: OK`.
- [x] صحّة MinIO على الصورة الجديدة: `/minio/health/live` يردّ 200.
- [ ] (بيد المالك) قياسات الساعة AC‑3.6 وAC‑9.5 وفحص العشر دقائق AC‑9.4 كانت مقيسة في QA؛ يُعاد فحصها عند أيّ تفعيل جديد.

## خطة التراجع
1. **المُصدِّران والقواعد (لا أثر على البيانات):**
   - `docker compose stop node-exporter postgres-exporter` ثمّ `docker compose rm -f node-exporter postgres-exporter` (دون `-v`).
   - إزالة مهمّتي `node` و`postgres` من `deploy/prometheus/prometheus.yml` وقواعد التنبيه الأربع من `alerts.yml` عبر `git revert` لالتزامات الميزة على فرع جديد من `master`، ثمّ `docker compose up -d --no-deps prometheus`.
   - إسقاط الدور (اختياري، بيد المالك): `DROP ROLE metrics_exporter` بعد إيقاف المُصدِّر. لا بيانات تطبيق مرتبطة به.
2. **`redis-cache`:** إعادة الحدّ إلى قيمته السابقة بالتراجع عن هذا التعديل في `docker-compose.yml`، ثمّ `docker compose up -d --no-deps redis-cache`. (تحقّق قبلها من ميزانيّة الذاكرة، `deploy/resource-budget.sh`.)
3. **MinIO:** الرجوع إلى `minio/minio:RELEASE.2025-04-22T22-12-26Z` ممكن **فقط على جهاز تتوفّر فيه هذه الصورة محلّيّاً** (لا تُسحب من أيّ سجلّ). فُحصت الرحلة: قديم ثمّ pgsty ثمّ قديم على نسخة من البيانات (475 MB): 3 دلاء، 10980 كائناً، ونفس md5 لخمس عيّنات في الحالات الثلاث. أي لا فقدان بيانات. الأمر: إعادة سطر الصورة في `docker-compose.yml` ثمّ `docker compose up -d --no-deps minio minio-bootstrap`. إن لم تتوفّر الصورة القديمة فالتراجع غير ممكن، والمسار الوحيد للأمام هو pgsty أو بديل آخر.
4. لا تُحذف أيّ مجلّدات (`down -v` ممنوع).

## نتيجة staging
لا staging؛ الفحص على الحزمة المحلّيّة الحيّة، قراءةً فقط، بلا إعادة تشغيل ولا SQL كتابي ولا `tests/integration`.

**المرجع:** `master` = `9da4178`.

حالة الحاويات (`docker compose ps`):
```
SERVICE                 STATUS
minio                   Up 16 minutes (healthy)
node-exporter           Up 11 hours (healthy)
postgres                Up 17 hours (healthy)
postgres-exporter       Up 6 hours (healthy)
prometheus              Up 11 hours (healthy)
```
(`postgres` لم يُعَد إنشاؤه، ويعمل منذ 17 ساعة.)

استعلامات Prometheus:
```
up{job=~"node|postgres"}
  node     instance=node-exporter:9100       -> 1
  postgres instance=postgres-exporter:9187   -> 1
pg_up{job="postgres"}                         -> 1
node_filesystem_avail_bytes{mountpoint="/"}   -> 884320428032  (device=/dev/sdd, ext4)
/api/v1/rules: groups 9, rules 27
  aizzak-host     AizzakHostDiskHigh            inactive
  aizzak-postgres AizzakPostgresDown            inactive
  aizzak-postgres AizzakPostgresLockWaitHigh    inactive
  aizzak-postgres AizzakPostgresArchiveStalled  inactive
```

التنبيهات المشتعلة (`/api/v1/alerts`):
```
AizzakWatchdog   none     firing  2026-10-05T18:11:58Z   (متوقّع)
AizzakDlqNotEmpty warning firing  2026-10-06T05:06:28Z
```

`deploy/prometheus/test-rules.sh` (الخاتمة):
```
SUCCESS: /etc/prometheus/prometheus.yml is valid prometheus config file syntax
SUCCESS: /etc/prometheus/alerts.yml - 27 rules found
test-rules: OK
```

MinIO على الصورة الجديدة:
```
aizzak-minio-1  pgsty/minio  RELEASE.2026-08-04T00-00-00Z  linux/amd64
GET /minio/health/live (من داخل الحاوية) -> HTTP 200
```

## ملاحظات للمالك (مراقبة)
- **`AizzakDlqNotEmpty` (warning) مشتعل منذ 2026‑10‑06T05:06Z.** طابور الرسائل الميّتة (DLQ) ليس من هذه الميزة. سُجّلت الملاحظة قراءةً فقط ولم تُمسّ بيانات DLQ. القرار والفحص للمالك (انظر قسم التنبيه في `docs/runbooks/alerts.md`).

## مشكلات معروفة ومؤجّلة (ليست حاجبة، بقرار المالك)
- **job ‏`integration` في CI أحمر** بعطل قديم لا يخصّ الميزة: خلط حلقات asyncio في اختبارات `live_*` المعلَّمة `@pytest.mark.anyio` مع fixtures يديرها pytest‑asyncio (10 فشل، 36 خطأً، و5643 ناجح). تثبيت إصدارات المكتبات لم يُصلحه فتُرك. الإصلاح مهمّة مستقلّة على فرع جديد من `master`، وقد تظهر بعده أعطال أخرى. `quality` أخضر.
- شرط القبول 1 (نجاح `integration`) غير مستوفى، والمالك دمج مع علمه بذلك.
- `mc ilm rule add` في `deploy/minio/bootstrap.sh` يضيف قاعدة مكرّرة في كلّ تشغيل (في الحزمة الحيّة 5 قواعد متطابقة). غير ضارّ وقديم، ولم يُصلَح.
- بنود غير حاجبة من المراجعتين: L‑4 وI‑6 وI‑7 (أُغلق ما أمكن في `a1c9571`/`e1c2868`).
- تعديل `src/app/ops/backup.py` الذي ظهر في شجرة العمل من جلسة أخرى أثناء البناء لم يعد موجوداً: الشجرة نظيفة و`QDRANT_RETENTION` على 7 أيّام كما في `master`.

## بوّابة ③ (الإنتاج)
لا تنطبق: لا هدف staging ولا إنتاج مُعرَّف، والموجة 8 مؤجّلة. لا خطوات إنتاج بانتظار الإنسان الآن. عند فتح الموجة 8 يُكتب إجراء إنتاج مستقلّ، ويُشترط أن يمرّ عبر GitHub Environment `production` بمراجع إلزامي.

</div>
