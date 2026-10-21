<div dir="rtl">

# التصميم التقني: حزمة تثبيتٍ بدل المستودع (المرحلة 5 من خطّة خادم GPU، الخطوتان 5.1 و5.2)

> **المدخلات:** [`requirements.md`](requirements.md) و[`stories.md`](stories.md) بعد بوابة ① (2026‑10‑19)، و[`status.md`](status.md) «قرارات وتصعيدات». النطاق US‑1…US‑8 (47 معياراً). US‑9 وUS‑10 (5.3) خارج هذا التصميم، ولا شيء هنا يمنعهما (§3.3.9).
>
> **كلّ رقمٍ هنا مقيسٌ ومعه طريقة قياسه، أو معلَّمٌ «تقدير».** القياسات كلّها في 2026‑10‑19 على هذا الجهاز (10 معالجات، 12.67 ج.ب)، في مجلّد scratch فيه نسخٌ من ملفّي Compose و`.env.example` وحدها. لم تُشغَّل حاوية، ولم يُقرأ `.env` الحقيقيّ.

## 1. الملخّص

الميزة كلّها **خارج `src/`**: سكربتا نشرٍ جديدان تحت `deploy/release/`، وتعديلاتٌ صغيرة في ثلاثة سكربتات قائمة، واختباراتٌ في `tests/unit/`، ووثائق. لا مسار HTTP ولا مخطّط ولا هجرة، ولا تُلمس طبقات `.importlinter`.

| المكوّن | الشكل | الدور |
|---|---|---|
| `deploy/release/bundle.sh` | bash | يصنع `aizzak-<V>.tar.gz` من **الشجرة عند الوسم `v<V>`**، ومعه `VERSION` من أثر `release-digests` (5.1) |
| `deploy/release/bundle-files.txt` | قائمة بيانات | المصدر الوحيد لمحتوى الحزمة. يقرؤها `bundle.sh` من الوسم، ويقرؤها الحارس من الشجرة (FR‑7) |
| `deploy/release/install.sh` | bash، ومعه برنامج Python واحد مضمَّن (`python3 -I -`) | التثبيت بخطواته الثماني (5.2). نسخةٌ منه في جذر الحزمة |
| `deploy/auto_size.py` و`deploy/resource-budget.sh` | تعديل | شرط القبول 3 لـ`compose-auto-size` (FR‑26) |
| `deploy/host-tuning.sh` | تعديل | خيار `--persist` يكتب `/etc/sysctl.d/90-aizzak.conf` (Q‑5) |

**المبدأ الحاكم للتكرار والإقلاع الأوّل:** `.env` يُكتب **كاملاً** قبل أوّل `up`، إلّا سطر `VAULT_SECRET_ID`. والخدمتان الوحيدتان اللتان تُنشآن قبل الإصدار (`vault` و`vault-bootstrap`) لا تحملان المفتاح، فلا تتغيّر بصمتهما حين يُكتب. **مقيس:** إضافة `VAULT_SECRET_ID` إلى `.env` غيّرت `config --hash` لثماني خدماتٍ بالضبط (`app` و`migrate` و`ops-scheduler` و`outbox-relay` و`wal-shipper` والعمّال الثلاثة)، ولم تغيّر `vault` ولا `vault-bootstrap`. فلا تُنشأ حاويةٌ ثمّ يُعاد إنشاؤها. وفي التكرار لا يُنادى `up` أصلاً إن كان المكدّس متقارباً (§3.3.6).

**ما لا تفعله الميزة:** لا تثبّت Docker ولا تعريفاً (`gpu-preflight.sh` يفحص)، ولا تأخذ مفتاح السجلّ (Q‑9)، ولا تضبط صلاحيّة `.env` (Q‑4)، ولا تحدّث ولا ترجع (5.3)، ولا تمسّ `.github/workflows/` ولا `Dockerfile` ولا `docker-compose.gpu.yml` ولا `deploy/runpod/` ولا `deploy/nginx/`.

## 2. الملفّات

المنفّذ واحد: **`backend-developer`** لكلّ الملفّات، فلا يكتب في المستودع إلّا كاتبٌ واحد. **مرحلة `data-engineer` تُتخطّى:** لا جدول ولا فهرس ولا هجرة (§4). و**`frontend-developer` وUX يُتخطّيان:** لا واجهة في المستودع.

| المسار | إنشاء/تعديل | المسؤوليّة | المنفّذ |
|---|---|---|---|
| `deploy/release/bundle-files.txt` | إنشاء | 34 مساراً: ملفّا Compose، و`.env.example`، و`deploy/sizes/`، والسكربتات الخمسة، والمسارات الخمسة والعشرون المربوطة (§3.2.3) | backend-developer |
| `deploy/release/bundle.sh` | إنشاء | صنع الحزمة (§3.2) | backend-developer |
| `deploy/release/install.sh` | إنشاء | التثبيت (§3.3) | backend-developer |
| `deploy/auto_size.py` | تعديل | review #20، وSR‑6 (رفض الروابط وغير المملوك لـroot)، وتحذير SR‑11 (§3.4.1) | backend-developer |
| `deploy/resource-budget.sh` | تعديل | `exec python3 -I -` بدل `python3 -` (السطر 123)، وتعليقٌ بالسبب (§3.4.2) | backend-developer |
| `deploy/host-tuning.sh` | تعديل | `--persist` و`SYSCTL_D_DIR` (§3.4.3) | backend-developer |
| `docker-compose.yml` | تعديل (تعليقٌ فقط) | الأسطر 148‑150 وحدها، **بالعدد نفسه من الأسطر** فلا تنزاح أرقام الأسطر المذكورة في الوثائق (FR‑25، A‑15) | backend-developer |
| `.env.example` | تعديل (تعليقاتٌ فقط) | تعليق Vault (الأسطر 336‑341) وتعليق `VAULT_ROLE_ID` (356‑360)، بالعدد نفسه من الأسطر. ولا تتغيّر قيمة (§3.5) | backend-developer |
| `.gitignore` | تعديل | `/aizzak-*.tar.gz` و`/.aizzak-*.tar.gz.tmp.*` (مخرج `bundle.sh` الافتراضيّ في جذر المستودع) | backend-developer |
| `docs/design/05-rbac-config-secrets.md` | تعديل | السطران 128 و148 (FR‑25) | backend-developer |
| `docs/design/08-local-runbook.md` | تعديل | §3.1: الأسطر 800 و808 و812 و825 (FR‑25) | backend-developer |
| `docs/quickstart.md` | تعديل | الأسطر 204 و209‑210 و353 | backend-developer |
| `docs/stack-commands.md` | تعديل | الأسطر 54‑55 و259 و266 و274 و277 و285 و635 | backend-developer |
| `docs/deploy-server.md` | تعديل | قسمٌ جديد «## 3) التثبيت» قبل قائمة الأمان، وثغراتٌ جديدة من ث‑10 (§3.5) | backend-developer |
| `docs/server-gpu-plan.md` | تعديل | §1.1 (27 مساراً، 25 في الحزمة)، وقائمة 5.1، وخطوات 5.2، وصفوف اللوحة 5.1–5.3 (FR‑34) | backend-developer |
| `docs/server-gpu-summary.html` | تعديل | يطابق اللوحة | backend-developer |
| `tests/unit/support_install.py` | إنشاء | الأدوات المزيّفة وبناء شجرة الحزمة للاختبار (§5.1). على نمط `support_compose.py` | backend-developer |
| `tests/unit/test_release_bundle.py` | إنشاء | US‑1 [وحدة] | backend-developer |
| `tests/unit/test_release_bundle_guard.py` | إنشاء | US‑2 (الحارس الأدنى) | backend-developer |
| `tests/unit/test_install_sh.py` | إنشاء | US‑3…US‑7 [وحدة] | backend-developer |
| `tests/unit/test_install_sh_config.py` | إنشاء | معايير [config]: AC‑1.5 وAC‑5.4، وحارس «لا إعادة إنشاء بعد الإصدار». تتخطّى إن غاب Docker | backend-developer |
| `tests/unit/test_install_bundle_docs.py` | إنشاء | AC‑7.5 وAC‑8.1…AC‑8.4 | backend-developer |
| `tests/unit/test_auto_size_edges.py` | تعديل | AC‑5.5 (أ) و(ج) و(د) | backend-developer |
| `tests/unit/test_resource_budget.py` | تعديل | AC‑5.5 (ب) | backend-developer |
| `tests/unit/test_host_tuning.py` | تعديل | `--persist` | backend-developer |
| `tests/unit/test_gpu_preflight.py` | تعديل | `test_the_server_doc_lists_the_scripts_exit_codes` يقرأ صفوف الجدول من **§1 وحده** (§3.5) | backend-developer |
| `tests/unit/test_gpu_compose_hygiene.py` | تعديل | `deploy/release/bundle.sh` و`deploy/release/install.sh` في `_SCRIPTS` (AC‑2.4) | backend-developer |

**كلّها داخل موافقة Q‑1** أو خارج القاعدة 2 (الاختبارات، وتعليقات `.env.example`، و`.gitignore`، والوثائق). لا ملفّ محميّ خارج القائمة: البرنامج المضمَّن في `install.sh` يُغني عن ملف Python جديد تحت `deploy/release/`، على سابقة `resource-budget.sh:123`.

## 3. العقود

### 3.1 الواجهات البرمجية

لا تغيير. لا مسار جديد، و`docs/design/openapi.yaml` كما هو، ولا حدث في `docs/design/events/schemas/`.

### 3.2 `deploy/release/bundle.sh`

#### 3.2.1 الاستعمال ورموز الخروج

```
deploy/release/bundle.sh --digests FILE [--out DIR] <VERSION>
deploy/release/bundle.sh --help
```

- `<VERSION>` بصيغة `build-push.sh` نفسها: `^20[0-9]{2}\.(1[0-2]|[1-9])\.[1-9][0-9]*$` (`deploy/release/build-push.sh:70`). بلا `v` وبلا بادئة.
- `--digests FILE` **إلزاميّ**: أثر `release-digests` كما يكتبه `build-push.sh:238`، أي سطرٌ لكلّ صورة: `anastawahia/aizzak:<prefix>-<V> sha256:<64 hex> <commit>` (حسم A‑2).
- `--out DIR` افتراضيّاً المجلّد الحاليّ. والمخرج `DIR/aizzak-<V>.tar.gz`.

رأس السكربت يحمل الجدول بصيغة `gpu-preflight.sh` (`#   N  meaning`)، واختبارٌ يقارنه بالحالات:

| الرمز | المعنى |
|---|---|
| 0 | كُتبت الحزمة. يطبع المسار وsha256 وعدد الملفّات |
| 2 | استعمالٌ خاطئ: إصدارٌ بصيغةٍ خاطئة أو غائب، وخيارٌ غير معروف، و`--digests` غائب |
| 3 | البيئة: ليس جذر المستودع (`git rev-parse --show-toplevel` من مجلّد السكربت، كـ`build-push.sh:125`)، أو غياب `git` أو `gzip` أو `sha256sum`، أو `tar` ليس GNU tar ≥ 1.28 |
| 4 | الوسم `v<V>` غير موجود |
| 5 | ملفّ البصمات: غير مقروء، أو ليس ثلاثة أسطر، أو بادئةٌ ناقصة أو مكرّرة، أو `digest-unknown`، أو بصمةٌ ليست `sha256:` و64 حرفاً، أو مستودعٌ أو وسمٌ غير `anastawahia/aizzak:<prefix>-<V>`، أو commit يخالف commit الوسم |
| 6 | المخرج: المجلّد غير موجود أو غير قابلٍ للكتابة، أو `aizzak-<V>.tar.gz` موجودٌ فيه |
| 7 | المحتوى: مسارٌ في القائمة غائبٌ عند الوسم، أو رابطٌ رمزيّ أو submodule تحته، أو مسارٌ ممنوع (FR‑3) في المرحلة الوسيطة، أو الوسم أقدم من `bundle-files.txt` |
| 1 | خطأٌ غير متوقّع (`set -e`) |

**كلّ فحصٍ يسبق أيّ كتابةٍ في `DIR`** (AC‑1.4). والعمل الوسيط في `mktemp -d` يُحذف بـ`trap`.

#### 3.2.2 الخطوات

1. `umask 022` و`LC_ALL=C`، ثمّ فحص الأدوات والجذر (3)، ثمّ الوسائط (2).
2. `commit=$(git rev-parse -q --verify "refs/tags/v$V^{commit}")` (4)، و`epoch=$(git log -1 --format=%ct "$commit")`.
3. ملفّ البصمات (5). **ربط الصور بالمصدر:** commit كلّ سطرٍ يساوي commit الوسم. و`build-push.sh` يرفض أصلاً بناء commit ليس على `origin/master` (`:143`)، فأثرٌ حقيقيّ يضمن الأصل من `master` بالتعدّي. **فلا يفحص `bundle.sh` نسب `master` بنفسه**، وهذا ما يسمح بإثبات Q‑10 على وسمٍ تجريبيّ محلّيّ (حافّة US‑1 الأولى تتحقّق هكذا).
4. القائمة **من الوسم:** `git show "$commit:deploy/release/bundle-files.txt"`. ولكلّ مسار: `git cat-file -e "$commit:$p"`، ثمّ `git ls-tree -r "$commit" -- "$p"` بلا نمط `120000` ولا `160000` (7).
5. المرحلة الوسيطة: `git archive --format=tar "$commit" -- <المسارات> | tar -x -C "$stage/aizzak-$V"`. ثمّ `git show "$commit:deploy/release/install.sh" > "$stage/aizzak-$V/install.sh"` و`chmod 0755`. ثمّ `VERSION` (§3.2.4) بصلاحيّة 0644.
6. **فحص المنع (FR‑3)** على قائمة ملفّات المرحلة، بتعابير ERE في السكربت: ملفّات Compose `build` و`test` و`wsl-gpu`، و`deploy/(load|runpod|postgres/testdb|backup/results)/`، و`src/` و`tests/`، وأيّ `.env*` غير `.env.example`، و`init.json`، و`deploy/nginx/certs/`، و`deploy/postgres/sizes/auto.conf*`، و`.auto-size-tmp-*`، و`__pycache__` (7). المحتوى من الوسم أصلاً، فهذا دفاعٌ ثانٍ.
7. أرشيفٌ حتميّ (NFR‑5): `tar --sort=name --format=gnu --owner=0 --group=0 --numeric-owner --mtime="@$epoch" --mode='u+rwX,go+rX,go-w' -C "$stage" -cf - "aizzak-$V" | gzip -n -9 > "$DIR/.aizzak-$V.tar.gz.tmp.$$"`.
8. فحص الأرشيف بـ`tar -tvzf`: كلّ مدخلٍ تحت `aizzak-$V/`، ولا مسار مطلق ولا `..`، ولا نوع `l` ولا `h` (7).
9. `ln` من المؤقّت إلى `aizzak-$V.tar.gz` (ذرّيّ ويرفض الموجود)، ثمّ حذف المؤقّت. ويطبع `sha256sum` وعدد الملفّات والحجم.

**مقيس (`git archive HEAD` على `5d00b45` للمسارات الـ34، ثمّ `gzip -n -9`):** 44 ملفّاً متتبَّعاً (38 بصلاحيّة 100644 و6 بـ100755، لا روابط)، و706,560 بايتاً غير مضغوطة، و213,770 مضغوطة. فالحزمة 46 ملفّاً مع `install.sh` و`VERSION`، و≤ 1 م.ب بهامشٍ واسع (NFR‑4). الحجم بعد إضافة `install.sh` «تقدير»: أقلّ من 250 ك.ب.

#### 3.2.3 `deploy/release/bundle-files.txt`

مسارٌ لكلّ سطر، نسبةً إلى جذر المستودع كما هو عند الوسم. `/` في آخره يعني مجلّداً (كلّ الملفّات المتتبَّعة تحته). و`#` تعليق. **لا يُكتب فيه `deploy/release/install.sh`:** يضيفه `bundle.sh` في الجذر بنفسه.

```
docker-compose.yml
docker-compose.gpu.yml
.env.example
deploy/sizes/
deploy/auto_size.py
deploy/gpu-preflight.sh
deploy/host-tuning.sh
deploy/resource-budget.sh
deploy/rolling-deploy.sh
# the 25 ./deploy/ bind mounts of the two Compose files (deploy/load and deploy/nginx/certs are
# left out on purpose; tests/unit/test_release_bundle_guard.py names them and checks the rest)
deploy/alertmanager/alertmanager.yml
deploy/alertmanager/sink.py
deploy/alloy/config.alloy
deploy/grafana/dashboards/
deploy/grafana/provisioning/
deploy/loki/loki.yml
deploy/minio/bootstrap.sh
deploy/nginx/app-locations.conf
deploy/nginx/embedding-lb.conf
deploy/nginx/gen-cert.sh
deploy/nginx/nginx.conf
deploy/ollama/pull_models.py
deploy/postgres-exporter/queries.yaml
deploy/postgres/archive_wal.sh
deploy/postgres/initdb/
deploy/postgres/pg_hba.conf
deploy/postgres/postgresql.conf
deploy/postgres/sizes/
deploy/prometheus/alerts.yml
deploy/prometheus/prometheus.yml
deploy/prometheus/scrape-gpu.yml
deploy/vault/app-policy.hcl
deploy/vault/bootstrap.sh
deploy/vault/server.hcl
deploy/vault/start.sh
```

**مقيس:** القائمة الـ27 نفسها بطريقتين: `docker compose --profile '*' -f docker-compose.yml -f docker-compose.gpu.yml config --format json` (مصادر `type: bind`)، وقراءة YAML للملفّين بـPyYAML (الصيغة القصيرة كلّها، ولا صيغة طويلة ولا متغيّر في مسار). والخمسة الباقية من 32 مسارات مضيف (`/` و`/sys` و`/var/lib/docker/` و`/var/run` و`/var/lib/docker/containers`). ومجلّد الشهادات يُنشئه Docker عند أوّل `up`، لأنّ الربط بالصيغة القصيرة (`docker-compose.yml:2477`).

#### 3.2.4 صيغة `VERSION`

خمسة أسطر بالضبط، بنهاية `LF`، بلا تعليق:

```
2026.10.3
commit 0123456789abcdef0123456789abcdef01234567
anastawahia/aizzak:app-2026.10.3@sha256:<64 hex>
anastawahia/aizzak:embedding-2026.10.3@sha256:<64 hex>
anastawahia/aizzak:embedding-cuda-2026.10.3@sha256:<64 hex>
```

السطر الأوّل الإصدار وحده، فيكفي `head -1 VERSION`. والأسطر 3–5 بالترتيب الثابت `app` ثمّ `embedding` ثمّ `embedding-cuda`، وتطابق تعبير AC‑1.3.

### 3.3 `deploy/release/install.sh`

#### 3.3.1 الاستعمال

```
sudo ./install.sh --domain NAME --firebase-project ID [--size auto|small|medium|large]
                  [--cpu] [--no-monitoring] [--gpu-smoke]
./install.sh --help
```

| الخيار | المعنى |
|---|---|
| `--domain NAME` | إلزاميّ. يُحوَّل إلى حروفٍ صغيرة. ASCII فقط، ولا مخطّط ولا مسار ولا منفذ. والطول ≤ 253، وكلّ جزءٍ `[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?`، وجزآن على الأقلّ، وآخرها حروفٌ أو `xn--…` (فلا عنوان IP). النطاق العربيّ يُرفض ومعه تلميح `xn--` (A‑12) |
| `--firebase-project ID` | إلزاميّ (Q‑8). قاعدة معرّف مشروع Google: `^[a-z][a-z0-9-]{4,28}[a-z0-9]$`. فالمسافة و`/` مرفوضان (AC‑4.4). المثال في الدليل `aizzak-agent` |
| `--size S` | `auto` افتراضيّاً، أو اسم ملفٍّ في `deploy/sizes/` (`small` و`medium` و`large` اليوم). غيرها رمز 2 |
| `--cpu` | وضع المعالج صراحةً، ولو أعطى الفحص 0. وهو «الاختيار الصريح» الذي يجعل رمز الفحص 12 وضعَ معالج (FR‑8) |
| `--no-monitoring` | `COMPOSE_PROFILES=local-llm` (قرار 7) |
| `--gpu-smoke` | يمرّر `--smoke` إلى `gpu-preflight.sh`، لمضيف CDI (`docs/deploy-server.md` §1.4) |

**لا خيار يأخذ مفتاحاً أو كلمة سرّ أو توكناً** (AC‑3.3)، ولا تكرار لخيار، ولا وسيط موضعيّ (2). ولا أسئلة تفاعليّة (A‑3).

**المخرج بالإنجليزيّة**، كبقيّة سكربتات `deploy/`، نصٌّ بلا رموز تحكّم. والعبارات التي تختبرها المعايير: `READY` و`NOT READY`، و`GPU: yes|no`، و`monitoring: on|off`، و`NO ALERTS`، و`run it from the unpacked bundle`، و`sudo ./install.sh`، و`docker login`، و`chmod 600 .env`.

#### 3.3.2 رموز الخروج (FR‑24)

في رأس السكربت بين سطر `# Exit codes` و`set -euo pipefail`، بصيغة `gpu-preflight.sh` (`#   NN  meaning`)، وفي جدول §3 من `docs/deploy-server.md`. ويقارنهما AC‑7.5.

| الرمز | المعنى | الخطوة |
|---|---|---|
| 0 | `READY`: `/health` و`/health/ready` بـ200، و`stack_smoke.py` بـ0 | 8 |
| 2 | استعمالٌ خاطئ: خيارٌ غير معروف أو مكرّر، و`--domain` أو `--firebase-project` غائبٌ أو بصيغةٍ خاطئة، و`--size` غير معروف | 0 |
| 3 | ليست حزمةً مفكوكة: لا `VERSION` بجانب `docker-compose.yml`، أو نسخةٌ من المستودع (`.git` أو `src/`)، أو `VERSION` تالف. **أو** (root) الحزمة أو `deploy` أو `deploy/postgres/sizes` أو `.env` رابطٌ رمزيّ أو غير مملوكٍ لـroot. أو `install.sh` آخر يعمل في المجلّد نفسه | 0 |
| 4 | ليس root: `sudo ./install.sh …` | 0 |
| 10 | `gpu-preflight.sh` أوقف التثبيت، ويُطبع رمزه: 11 أو 13 أو 14، أو 12 دون `--cpu` | 1 |
| 11 | أداةٌ ناقصة على المضيف: bash < 4، أو Python < 3.10، أو PyYAML، أو `curl`، أو `timeout`، أو `flock`، أو `df` | 0 |
| 12 | المساحة الحرّة في `DockerRootDir` تحت الحدّ (تثبيتٌ أوّل فقط، §3.3.4) | 1 |
| 13 | السجلّ رفض صورة الإصدار: لا `docker login`، أو مفتاحٌ بلا صلاحيّة، أو بصمةٌ ليست فيه | 1 |
| 14 | السجلّ لا يُبلَغ: مهلة، أو DNS، أو TLS، أو 5xx | 1 |
| 20 | ضبط النواة فشل (`host-tuning.sh --apply --persist`) | 2 |
| 30 | `.env` مرفوض: مفتاحٌ يخالف المدخلات (النطاق، ومشروع Firebase، والوضع، والمراقبة، والحجم، والإصدار)، أو كلمة سرٍّ فارغة أو `change-me` على خادمٍ مثبَّت، أو مفتاحٌ تملكه الكتلة معرَّفٌ خارجها، أو كتلةٌ تالفة | 3 |
| 40 | الحجم مرفوض: الخادم أصغر من `small`، أو رفضته الميزانيّة (رمز 1 من المولِّد أو من `resource-budget.sh`) | 3–4 |
| 41 | خطأٌ في أداة الحجم: المولِّد بـ2 أو 3، أو `resource-budget.sh` بـ2 | 3–4 |
| 50 | سحب صورةٍ فشل أو انقضت مهلته | 5 |
| 51 | صورة إصدارٍ لا تطابق `VERSION` (قائمة Compose أو البصمة المسحوبة)، أو صورةٌ بوسم `dev` أو `latest` | 5 |
| 60 | خطوة إقلاعٍ فشلت أو انقضت مهلتها، والرسالة تسمّي الخطوة والخدمة وأمر `docker compose logs <service>` | 6 |
| 61 | `VAULT_SECRET_ID`: الإصدار فشل، أو الناتج ليس UUID، أو القيمة في `.env` تالفة | 6 |
| 70 | `/health` أو `/health/ready` لم يردّ 200 | 7 |
| 71 | `stack_smoke.py` فشل | 7 |
| 1 | خطأٌ غير متوقّع (`set -e`) | — |

#### 3.3.3 البنية: دوالّ صغيرة، و`main` محروس

السكربت يُشغَّل، ويُستورَد في الاختبار: `[[ ${BASH_SOURCE[0]} == "$0" ]] && main "$@"`. فالاختبار يستدعي `prepare` ثمّ `run_steps` بعد `source`، دون `require_root` ودون متغيّر بيئةٍ يتجاوز root. وتُختبر `require_root` و`require_trusted` و`lock_bundle` وحدها.

```bash
# ── constants (header) ─────────────────────────────────────────────────────
readonly IMAGE_REPO="anastawahia/aizzak"
readonly MIN_FREE_GB_GPU=40 MIN_FREE_GB_CPU=30   # تقدير (NFR-8): images measured 27 / 19.5 GB on this host
readonly SECRET_ID_TTL_H=720                     # == deploy/vault/bootstrap.sh secret_id_ttl (a test compares)
readonly PROBE_TIMEOUT_S=30 PULL_TIMEOUT_S=1800 PULL_DEADLINE_S=3600 BOOT_DEADLINE_S=1800   # تقدير
readonly MINT_SCRIPT='export VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN="$(sed -n "s/.*\"root_token\": *\"\([^\"]*\)\".*/\1/p" /vault/init/init.json)"; vault write -f -field=secret_id auth/approle/role/app/secret-id'

main "$@"            # prepare "$@"; require_root; require_trusted 0 "$ROOT" "$ROOT/deploy" \
                     #   "$ROOT/deploy/postgres/sizes" ["$ROOT/.env"]; lock_bundle; run_steps
prepare "$@"         # --help -> usage, exit 0; bash >= 4 (11); locate_root (3); read_version (3);
                     # parse_args (2); require_host_tools (11); scrub_environment
run_steps            # step_checks_host; step_kernel; step_env; step_size; step_budget;
                     # step_pull; step_verify_images; step_boot; step_checks; report READY

locate_root          # ROOT = dir of this file; if it has no docker-compose.yml, try ../.. (repo layout).
                     # exit 3 unless ROOT has docker-compose.yml AND VERSION AND neither .git nor src/
read_version         # VERSION, COMMIT, REF_APP, REF_EMBEDDING, REF_CUDA from the 5 lines (exit 3)
parse_args "$@"      # DOMAIN FIREBASE_PROJECT SIZE FORCE_CPU MONITORING GPU_SMOKE (exit 2)
valid_domain S | valid_firebase S | valid_size S        # 0/1, no output: unit-tested directly
require_host_tools   # python3 -I: version >= 3.10, `import yaml`; command -v curl timeout flock df
require_root         # [[ $EUID -eq 0 ]] || die 4 "... run: sudo ./install.sh ..."
require_trusted UID PATH...   # each: not a symlink (-L) and `stat -c %u` == UID, else die 3
lock_bundle          # exec 9<"$ROOT"; flock -n 9 || die 3   (a lock on the directory: no new file)
scrub_environment    # unset every name assigned or commented-assigned in .env.example, and COMPOSE_*;
                     # prints the names it removed (Compose gives the shell priority over .env)
die CODE MSG... | say MSG... | relay   # relay = tool output with control characters stripped
redact               # relay + hv[sbr].<…> and UUIDs replaced, for Vault's stderr
dk SECS ARGS...      # timeout -k 10 SECS docker ARGS...
compose SECS ARGS... # dk SECS compose --project-directory "$ROOT" ARGS...   (cwd = ROOT)

step_checks_host     # step 1: step_preflight (sets MODE, 10); step_disk (12); step_registry (13/14)
step_kernel          # step 2: bash "$ROOT/deploy/host-tuning.sh" --apply --persist  (20)
step_env             # step 3: env_tool compose ...  (30)
step_size            # step 3: named -> budget --size, then env_tool size-block; auto -> auto_size.py
step_budget          # step 4: auto only: resource-budget.sh --block "$ROOT/.env"
step_pull            # step 5: pull every image of `compose config --images` that is not present (50)
step_verify_images   # step 5: release images == VERSION; pulled digests == VERSION; no dev/latest (51)
step_boot            # step 6: B1..B8 (§3.3.6)  (60/61)
step_checks          # step 7: /health, /health/ready, stack_smoke.py  (70/71)
report STATUS        # step 8: stdout only (§3.3.8)

stack_state          # one batch: compose config --services, --hash '*', ps -a -q, docker inspect
service_done SVC     # 0 when converged (rules in §3.3.6)
ensure SVC SECS      # service_done || { compose up -d SVC; wait_for SVC SECS; }
wait_for SVC SECS    # poll every 2 s; fail fast on a one-shot exit != 0 (60)
mint_secret_id       # B3 (61)
set_metrics_exporter_password   # B6 (60)
env_tool SUB ARGS... # python3 -I - SUB ARGS... <<'PY' ... PY   (§3.3.5)
```

**قواعد مشتركة:**
- **كلّ نداءٍ خارجيّ تحت `timeout`** (NFR‑7). وكلّ انتظارٍ يأخذ `min(مهلة الخطوة، ما بقي من BOOT_DEADLINE_S)`، ورسالة الانقضاء تسمّي الخدمة.
- **لا `docker compose config` عارٍ أبداً:** ناتجه فيه كلّ كلمات السرّ. المسموح `--images` و`--services` و`--hash '*'` و`-q` وحدها، ويحرس ذلك اختبار.
- لا `source .env`، ولا `--force-recreate`، ولا `down`، ولا `volume rm`، ولا `prune`، ولا `operator init` (AC‑2.4، AC‑7.3).
- **`.env` يُقرأ ويُكتب بـ`env_tool` وحده**، لا بتعيين الصدفة (FR‑35).
- **لا سرّ في وسيط ولا في مخرج** (NFR‑2): الأسرار تُولَّد داخل `env_tool`. وكلمة سرّ `metrics_exporter` تعبر **stdin** إلى `docker compose exec`. و`secret_id` يُلتقط في متغيّر، ويصل إلى `env_tool` عبر متغيّر بيئةٍ مصدَّرٍ لحظة النداء وحده، ثمّ `unset`. وهذا أضيق من «`-e NAME`» في AC‑4.2: لا يحمل سطر أوامر `docker` سرّاً، ولا بيئته، وهي صيغة `08 §3.3‑ج` الخطوة ① نفسها.

#### 3.3.4 الخطوات 1–5

**الخطوة 0** (`prepare` ثمّ `main`): الترتيب 3 ثمّ 2 ثمّ 11 ثمّ 4. فرفض نسخة المستودع (AC‑3.4) يسبق كلّ شيء، وفحص Python يُختبر دون root (AC‑3.2). ولا كتابة قبل الخطوة 2.

**الخطوة 1 — الفحص، ولا كتابة فيه، ولا نداء `docker compose` من `install.sh` نفسه (AC‑3.1):**

| الفحص | الأمر | النتيجة |
|---|---|---|
| الكرت وDocker وCompose | `bash "$ROOT/deploy/gpu-preflight.sh" [--smoke]` | 0 ← GPU، إلّا مع `--cpu`. 10 ← CPU. 12 مع `--cpu` ← CPU، ودونه 10. و11 و13 و14 ← 10. ويُطبع مخرج الفحص بعد `relay` |
| المساحة | `dk 30 info --format '{{.DockerRootDir}}'` ثمّ `df -Pk <dir>` | **في التثبيت الأوّل وحده** (لا كتلة `aizzak install` في `.env`): أقلّ من 40 ج.ب للكرت أو 30 للمعالج ← 12، والرسالة بالـGB. وفي التكرار يُطبع الرقم فقط، لأنّ الصور المسحوبة أخذت المساحة أصلاً، فلا يُرفض استئنافٌ مشروع |
| السجلّ | `dk 60 manifest inspect anastawahia/aizzak@sha256:<بصمة app>` | 0 ← يكمل. stderr فيه `unauthorized` أو `denied` أو `authentication required` أو `no basic auth` أو `manifest unknown` أو `not found` ← 13، ومعه: `sudo docker login docker.io -u <user>` بمفتاح Docker Hub **قرائيّ لهذا الخادم وحده** (Q‑9). وغير ذلك (124، `dial tcp`، `TLS`، `i/o timeout`، `5xx`) ← 14 «no network» |

**الخطوة 2 — النواة:** `bash "$ROOT/deploy/host-tuning.sh" --apply --persist`. غير 0 ← 20. وتمرّ `SYSCTL_ROOT` و`SYSCTL_D_DIR` من البيئة، فهما ليسا في `.env.example`.

**الخطوة 3 — `.env`:** `env_tool compose …` (§3.3.5) ← 30 عند الرفض. ثمّ الحجم:
- **مسمّى:** أوّلاً `resource-budget.sh --size S [--without-monitoring]`، ثمّ `env_tool size-block`. فالرفض لا يترك كتلةً يصطدم بها تكرارٌ بحجمٍ آخر. والرمز 1 ← 40، و2 ← 41.
- **`auto`:** `python3 -I "$ROOT/deploy/auto_size.py" --role server --mode <cpu|gpu> --profiles <local-llm,monitoring|local-llm> --env-file "$ROOT/.env"`. والرموز: 0 يكمل، و1 ← 40 ومعه مخرج المولِّد (أرقام GB)، و2 و3 ← 41. ومن مخرجه يُؤخذ سطر `host: N vCPU / G GB` للتقرير. وتحذير SR‑11 فيه متوقَّع ولا يغيّر الرمز (AC‑5.5 (د)).
- **كلاهما** بـ`cwd=$ROOT`، و`COMPOSE_FILE=docker-compose.yml` و`GPU_COMPOSE_FILE=docker-compose.gpu.yml` و`SIZES_DIR=deploy/sizes` مثبّتة في بيئة النداء (FR‑17، AC‑5.3). و`--mode`/`--profiles` صريحان، فلا يرى المولِّد صدفةً ولا `.env`.

**الخطوة 4 — الميزانيّة:** لـ`auto` وحده: `bash deploy/resource-budget.sh --block "$ROOT/.env"` (يتبع سطر `# profiles:` في الكتلة). والرموز كما أعلاه. و**لا سحب قبل نجاحها** (AC‑5.3).

**الخطوة 5 — السحب والتحقّق:**
1. `images=$(compose 30 config --images | sort -u)`. وأيّ صورةٍ بلا وسم، أو بوسم `dev` أو `latest` ← 51 (NFR‑6).
2. **يُسحب الغائب وحده.** لكلّ صورة: `ref_by_digest` (صيغة `repo:tag@sha256:D` تصير `repo@sha256:D`)، ثمّ `dk 30 image inspect <ref>`، فإن غابت: `dk "$PULL_TIMEOUT_S" pull <ref>`، تحت `PULL_DEADLINE_S` للكلّ ← 50.
   - **لماذا لا `docker compose pull`** (انحرافٌ مقصود عن نصّ FR‑18، يحفظ قصده): في التكرار يعيد `compose pull` سحب الوسوم المتحرّكة (`postgres:16` و`redis:7` و`hashicorp/vault:1.18` و`nginx:1.27` و`python:3.12-slim`)، فإن نُشرت نسخةٌ جديدة من أحدها أعاد `up -d` التالي إنشاء حاويته. وهذا يكسر FR‑23، وفي إثبات Q‑7 يغيّر وسوم صور المكدّس الحيّ على جهاز المالك (القاعدة 1).
   - و`--policy missing` غير مضمونٍ في Compose 2.20.0، الحدّ الأدنى في `gpu-preflight.sh:54`: لم يُتحقَّق متى أُضيف. أمّا `docker image inspect` و`docker pull` فلا يتعلّقان بالإصدار.
3. **صور الإصدار مثبّتةٌ بالبصمة** في `.env` (`APP_IMAGE_TAG=app-<V>@sha256:<D>`)، فالسحب نفسه يتحقّق من المحتوى. **مقيس:** Compose يقبل `repo:tag@sha256:…` (`config -q` ينجح، و`config --images` يطبعها كما هي). ويتحقّق السكربت صراحةً أيضاً (FR‑18):
   - مجموعة صور `anastawahia/aizzak` في `config --images` تساوي بالضبط سطور `VERSION` للوضع: `app` و`embedding` للمعالج، و`app` و`embedding-cuda` للكرت. **مقيس:** وضع الكرت لا يسمّي صورة `embedding`.
   - ولكلّ واحدة: `dk 30 image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' anastawahia/aizzak@sha256:<D>` فيها `anastawahia/aizzak@sha256:<D>`، أو الصيغة ببادئة `docker.io/`.
   - والمخالفة ← 51، والرسالة تسمّي الصورة والبصمتين (AC‑6.3). البصمات ليست أسراراً.

#### 3.3.5 `.env`: التخطيط وبرنامج `env_tool`

**`env_tool`** دالّة bash تشغّل برنامج Python واحداً مضمَّناً (`python3 -I - "$@" <<'PY'`). مكتبةٌ قياسيّة فقط، ومتوافقٌ مع 3.10، ويقرأ `.env` بقواعد `auto_size.py` نفسها: `_ASSIGN` و`_unquote` و`_lines` (`deploy/auto_size.py:95,486-544`)، منسوخةً مع ذكر المصدر. **ولا يطبع قيمةً قرأها أبداً، إلّا `get`.** ورسائله أسماء مفاتيح وأرقام أسطر فقط.

| الأمر الفرعيّ | الوسائط (كلّها غير سرّيّة) | الخروج |
|---|---|---|
| `compose ENV TEMPLATE` | `--release V --commit C --mode cpu\|gpu --monitoring on\|off --size S --domain D --firebase F --image-app REF --image-embedding REF --image-cuda REF` | 0 ويطبع `created` أو `adopted` أو `unchanged`. و3 رفضٌ يسمّي المفتاح والسطر، ولا يكتب شيئاً |
| `size-block ENV SIZE_FILE NAME` | — | 0 (`written` أو `unchanged`)، و3 |
| `secret-id-state ENV` | — | 0 UUID صالح، و1 غائب أو فارغ، و2 تالف. بلا مخرج |
| `set-secret-id ENV` | القيمة من `AIZZAK_INSTALL_SECRET_ID` في البيئة | 0، و3 (ليس UUID، أو موجودٌ أصلاً، أو لا كتلة) |
| `get ENV KEY` | — | القيمة على stdout، **إلى أنبوبٍ فقط** (B6)، و1 إن غاب |
| `minted-at ENV` | — | وقت الإصدار ISO‑8601 من تعليق الكتلة، أو لا شيء |

**تخطيط `.env` الناتج:**
1. **الجسم:** نسخةٌ من `.env.example` بايتاً ببايت، **إلّا أسطراً بعينها تُبدَّل في مكانها:**
   - كلّ سطرٍ فعّال `KEY=change-me…` في القالب: 13 سطراً، **تُشتقّ من القالب نفسه**، واختبارٌ يقارنها بقائمة FR‑12.
   - `MINIO_PUBLIC_ENDPOINT=<domain>`، و`MINIO_PUBLIC_SECURE=true`، و`OAUTH_REDIRECT_BASE_URL=https://<domain>`، و`FIREBASE_PROJECT_ID=<id>`.
   - `VAULT_ROLE_ID=<uuid4>` بدل `aizzak-local-app` (حسم A‑5).
2. **كتلة التثبيت** في آخر الملفّ، يملكها `install.sh`:

```
# >>> aizzak install >>>
# release 2026.10.3 (commit 0123456789ab) -- written by install.sh; re-install to change it, do not edit
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml
COMPOSE_PROFILES=local-llm,monitoring
APP_IMAGE_REPO=anastawahia/aizzak
APP_IMAGE_TAG=app-2026.10.3@sha256:<64 hex>
EMBEDDING_IMAGE_REPO=anastawahia/aizzak
EMBEDDING_IMAGE_TAG=embedding-2026.10.3@sha256:<64 hex>
EMBEDDING_CUDA_IMAGE_REPO=anastawahia/aizzak
EMBEDDING_CUDA_IMAGE_TAG=embedding-cuda-2026.10.3@sha256:<64 hex>
# VAULT_SECRET_ID minted 2026-10-19T12:34:56Z; secret_id_ttl 720h (deploy/vault/bootstrap.sh); renewal is server-gpu-plan.md 6.5
VAULT_SECRET_ID=<uuid>
# <<< aizzak install <<<
```

   - السطران الأخيران يضيفهما `set-secret-id` قبل سطر النهاية، بعد B3.
   - وللمعالج `COMPOSE_FILE=docker-compose.yml`، ودون المراقبة `COMPOSE_PROFILES=local-llm`. ولا `wsl-ollama` ولا `build` ولا `test` أبداً.
3. **كتلة الحجم**، واحدةٌ منهما:
   - كتلة `aizzak auto-size` يكتبها المولِّد كما اليوم.
   - أو كتلة `# >>> aizzak size >>>`، وفيها سطر `# size: <name> (deploy/sizes/<name>.env)` ثمّ أسطر `KEY=VALUE` من الملفّ بترتيبها. ويكتبها `size-block`، بقاعدة التعارض التي في `splice()` (`auto_size.py:556-583`): لا مفتاح من الكتلة معرَّفٌ خارجها، إلّا قيمة القالب غير المعدَّلة قبلها. **مقيس:** المفتاح المشترك الوحيد بين ملفّات الأحجام و`.env.example` هو `POSTGRES_CONFIG_FILE`.

**قواعد `compose`:** ثلاث حالات، تُعرف بوجود كتلة التثبيت.

| الحالة | السلوك |
|---|---|
| **لا `.env`** (`created`) | الجسم والكتلة كما أعلاه. **والأسرار:** `secrets.token_hex(24)` لكلٍّ من الـ13، أي `^[0-9a-f]{48}$` (حسم A‑7، مصدرٌ قويّ دون `openssl`). وتُعاد القرعة إن تكرّرت قيمة |
| **`.env` بلا كتلة** (`adopted`، كالإثبات المحلّيّ أو نسخةٍ يدويّة) | المفتاح المُدار الذي قيمته **قيمة القالب** يُبدَّل. وكلمة السرّ التي ضبطها المشغّل (لا تبدأ بـ`change-me` ولا فارغة) تبقى. والمفتاح غير المُدار لا يُلمس، ومنه `HOST_PORT_*` و`COMPOSE_PROJECT_NAME`. **ويُرفض (3):** مفتاحٌ مُدار قيمته لا القالب ولا المطلوب، ومفتاح كتلةٍ (`COMPOSE_*` والصور الستّ و`VAULT_SECRET_ID`) فعّالٌ خارجها، و`METRICS_EXPORTER_PASSWORD` أقصر من 32 لأنّ `15-metrics-exporter.sh:69` سيرفضه، و`VAULT_TOKEN` غير فارغ |
| **كتلةٌ موجودة** (تكرار) | **لا توليد إطلاقاً.** والنتيجة `unchanged` لا تُكتب، فلا يتغيّر بايتٌ ولا وقت التعديل. **ويُرفض (3) باسم المفتاح:** أيّ مفتاحٍ مُدار أو مفتاحٍ في الكتلة يخالف المطلوب (A‑13). وكلمة سرٍّ صارت فارغة أو `change-me` (وإلّا افترق Postgres عن Vault، `10-roles.sh:125-169`). و`VAULT_ROLE_ID` فارغ أو `aizzak-local-app`. وإصدارٌ آخر في الكتلة، ورسالته: «upgrading is upgrade.sh (5.3), not install.sh». و**الحجم:** كتلة `auto` و`--size` مسمّى، أو كتلةٌ مسمّاة باسمٍ آخر أو دون `--size`، أو الكتلتان معاً |

**الكتابة الذرّيّة دون المساس بالصلاحيّة** (FR‑11، NFR‑3، AC‑4.1، AC‑4.6):
- ملفٌّ مؤقّت `.install-tmp-<pid>` في المجلّد نفسه بـ`os.open(…, O_CREAT|O_EXCL|O_WRONLY, 0o666)`، فيأخذ صلاحيّة `umask`: **0644 بـ`umask 022`**. وليس `mkstemp` الذي يفرض 0600.
- **إن وُجد `.env`** تُنسخ صلاحيّته ومالكه إلى المؤقّت (`os.chmod` و`os.chown`)، كـ`auto_size.py:654-666`.
- ثمّ `fsync`، و`os.replace`، و`fsync` للمجلّد. والفشل يحذف المؤقّت ولا يمسّ `.env`.
- **لا `chmod` على `.env` في أيّ مسار.**
- **تحديث (2026‑10‑21، SR‑2):** الشكل أعلاه استُبدل. الملفّ القائم يُعاد كتابته عبر مؤقّتٍ يُنشأ بـ0600 و`O_NOFOLLOW`، ثمّ يأخذ المالك، ثمّ ACL الهدف، ثمّ الصلاحيّة، قبل أوّل بايت. والملفّ الجديد يُنشأ بـ0o666 بلا `fchmod`. التفصيل في [`../install-bundle-follow-ups/design.md`](../install-bundle-follow-ups/design.md) §SR‑2، والـcommits ‏`10d02f6` و`1b18fce` و`0aa13c9`. **و`upgrade.sh` (5.3) يبني على الشكل الجديد.**

#### 3.3.6 الخطوة 6 — أوّل إقلاع، والتكرار

**«منجَز» (`service_done`):** حاويةٌ واحدة على الأقلّ، ولكلّ حاوياتها وسم `com.docker.compose.config-hash` يساوي `config --hash` للخدمة (المقارنة التي يستعملها `08 §3.3‑ج`). والحاويات لمرّة (`RestartPolicy.Name == "no"`) بحالة `exited` ورمز 0. والدائمة `running` وصحّتها `healthy` أو بلا فحص. **وكلّ ذلك من نداءٍ واحد:**

```
docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}|{{index .Config.Labels "com.docker.compose.config-hash"}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.State.ExitCode}}|{{.HostConfig.RestartPolicy.Name}}' <ids of compose ps -a -q>
```

وهذا يتجنّب `up --wait` و`ps --format json` كليهما، فلا تعلّق بإصدار Compose (حسم A‑11).

| # | الخطوة | الأمر | المهلة (تقدير) |
|---|---|---|---|
| B1 | `vault` سليم | `ensure vault` | 300 ث. فحصه `start_period` 30 ث، و20 محاولة كلّ 5 ث (`docker-compose.yml:1465-1470`) |
| B2 | `vault-bootstrap` انتهى بـ0 | `ensure vault-bootstrap` | 300 ث |
| B3 | **إصدار `VAULT_SECRET_ID`** | `env_tool secret-id-state`: 0 تخطٍّ، و2 ← 61 (لا يُستبدل مفتاحٌ تالف بصمت)، و1 ← `sid=$(compose 60 exec -T vault sh -c "$MINT_SCRIPT" 2>"$err")`. ثمّ UUID أو 61 (يُطبع الطول وعدد الأسطر فقط، والخطأ بعد `redact`). ثمّ `export AIZZAK_INSTALL_SECRET_ID="$sid"; env_tool set-secret-id; unset …` | 60 ث |
| — | حارس FR‑20 | `env_tool secret-id-state` يساوي 0، وإلّا 61 قبل أيّ خدمةٍ تحمل المفتاح | — |
| B4 | `minio-bootstrap` | `ensure minio-bootstrap` (يقلع `minio` بالاعتماد) | 300 ث |
| B5 | `migrate` | `ensure migrate` (يقلع `wal-archive-init` و`postgres`) | 900 ث، وهي `PROVISION_LOCK_WAIT_MS` (`docker-compose.yml:1594`) |
| B6 | كلمة سرّ `metrics_exporter` | `env_tool get .env METRICS_EXPORTER_PASSWORD \| compose 120 exec -T postgres bash -c 'read -r METRICS_EXPORTER_PASSWORD; export METRICS_EXPORTER_PASSWORD; bash /docker-entrypoint-initdb.d/15-metrics-exporter.sh'`، حرفيّاً كـ`08 §3.3‑ج` ①. يُشغَّل في كلّ تشغيل: السكربت متساوي القوى (`15-metrics-exporter.sh:79-99`)، و`exec` لا يغيّر حاوية | 120 ث |
| B7 | `ollama-pull` | `ensure ollama-pull`. **الفشل يوقف التثبيت (60)** ومعه «the model was not pulled; chat would answer 502; re-run sudo ./install.sh». لا «READY» بمسار محادثةٍ مكسور، والتكرار يستأنف (حسم حافّة US‑6) | 900 ث |
| B8 | الباقي | `pending` = خدمات `config --services` غير المنجَزة. **فارغة:** «already converged: no container touched» **ولا نداء `up`**. **وإلّا:** `compose up -d`، ثمّ انتظار كلّ الخدمات حتّى «منجَز». والفشل يسمّي الخدمات الناقصة وحالتها | 900 ث |

- **`ensure SVC`** = `service_done SVC` أو (`compose up -d SVC` ثمّ `wait_for SVC`). و`up -d` بلا `--no-deps`، فيرتّب Compose الاعتمادات بنفسه.
- **مقيس من Compose المحلول:** اعتمادات `vault` لا شيء، و`vault-bootstrap` ← `vault`. فلا يُنشأ قبل B3 شيءٌ يحمل المفتاح، لا بالاسم ولا بالاعتماد (AC‑6.1). وحارسٌ ثابت يحفظ ذلك (§5.2).
- **`wait_for`:** كلّ 2 ث. حاويةٌ لمرّة انتهت بغير 0 فشلٌ فوريّ. والدائمة التي ليست `healthy` تُنتظر حتّى المهلة. ولا يُطبع سجلّ خدمة، بل الأمر الذي يقرؤه.
- **التكرار على مكدّسٍ متقارب:** B1 وB2 وB4 وB5 وB7 منجَزة فتُتخطّى، وB3 يُتخطّى، وB8 بلا `up`. فلا تتغيّر حاويةٌ ولا `StartedAt`، وAC‑7.4 حرفيّاً.
- **وغير المتقارب:** `up -d` في B8 يعيد تشغيل الخدمات لمرّة المنتهية، وكلّها متساوية القوى (`bootstrap.sh:5-12`، وقفل `provision`). وهذا يحدث في التثبيت الأوّل أيضاً (B5 ثمّ B8). وهو **تشغيلٌ لا إعادة إنشاء**، فالبصمة لم تتغيّر. ولم يُقَس هل يعيد Compose تشغيل الحاويات المنتهية في `up` أم لا، والتصميم صحيحٌ في الحالين.
- **الاستئناف (AC‑6.4):** فشل `migrate` يترك كتلةً فيها المفتاح. والتكرار يرى الكتلة (لا توليد)، ويرى المفتاح (لا إصدار)، ويكمل من B5.

#### 3.3.7 الخطوة 7 — الفحوص

- المنفذ من Compose نفسه: `compose 30 port nginx 80`، فـ`0.0.0.0:20080` يصير `20080`. فـ`HOST_PORT_HTTP` المنقول يُحترم (حافّة US‑7).
- `curl -sS -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:<port>/health` ثمّ `/health/ready`، بإعادةٍ كلّ ثانيتين حتّى 120 ث (تقدير). وغير 200 ← 70.
- `compose 300 exec -T app python /app/deploy/smoke/stack_smoke.py`، ومخرجه بعد `relay`. **مقروء:** السكربت لا يطبع سرّاً، فالرابط الموقّع يُقصّ عند `?` (`stack_smoke.py:129`). وغير 0 ← 71.

#### 3.3.8 الخطوة 8 — التقرير (stdout وحده، ولا ملفّ)

```
install: READY -- AIZZAK 2026.10.3 at https://example.com
  checks      /health 200, /health/ready 200, stack_smoke PASS
  size        auto (16 vCPU, 31.27 GB)            | medium (deploy/sizes/medium.env)
  GPU: yes    docker-compose.gpu.yml               | GPU: no (gpu-preflight exit 10 | --cpu)
  monitoring: on                                    | monitoring: off -- NO ALERTS: watch https://example.com/health from outside this server (docs/deploy-server.md §2)
  vault       secret_id expires 2026-11-18T12:34:56Z (720h after minting); renewing it is server-gpu-plan.md 6.5
  backup      copy the Vault volume aizzak_vault-init off this server (6.4); its contents are not shown
  .env        mode 0644 -- run: chmod 600 .env  (it holds every password and VAULT_SECRET_ID)
  next        run every docker compose command with sudo, from this directory
```

- **الحجم `auto`** من سطر `host:` في مخرج المولِّد.
- **انتهاء `secret_id`** = `minted-at` + `SECRET_ID_TTL_H`.
- **اسم حجم Vault** = `<project>_vault-init`. والمشروع `COMPOSE_PROJECT_NAME` من `.env` إن وُجد، وإلّا `name:` من `docker-compose.yml`. ولا `config` عارٍ.
- **سطر `.env`** يظهر مرّةً واحدة في كلّ تشغيل (AC‑7.2). وإن كانت الصلاحيّة 0600: `.env  mode 0600 (chmod 600 .env: done)`.
- **ورمزا 70 و71** يطبعان التقرير نفسه بـ`NOT READY` وسطر `failed: <check>`.

#### 3.3.9 ما يتركه لـ5.3

- كتلة التثبيت تحمل الإصدار، و`install.sh` يرفض إصداراً آخر برسالةٍ تسمّي `upgrade.sh`.
- `env_tool` يُعاد استعماله في `upgrade.sh`: إضافة المفاتيح الجديدة وتوليد `change-me` منها.
- ولا شيء هنا يحذف ملفّاً أو حجماً.

### 3.4 تعديل السكربتات القائمة

#### 3.4.1 `deploy/auto_size.py` (FR‑26، AC‑5.5)

```python
def check_trusted(env_path: Path, euid: int) -> None:
    """When euid == 0 only: EnvRefused (exit 3) naming the path if .env, its directory,
    <dir>/deploy, <dir>/deploy/postgres, <dir>/deploy/postgres/sizes, or an existing
    auto.conf is a symlink (os.lstat) or not owned by uid 0. A no-op for any other euid."""

def warn_if_shared(env_path: Path) -> None:
    """SR-11: if st_mode & 0o077, print to stderr
    'auto-size: warning: .env can be read by users other than its owner (chmod 600 .env)'
    -- the file name only, never a value. The exit code does not change."""

def _run(args, env_path, environ, euid: int) -> int: ...      # calls both before reading .env
def main(argv=None, environ=None, euid: int | None = None) -> int: ...   # None -> os.geteuid()
```

- **review #20:** داخل `write_pair` يُلتقط `OSError` حول كلّ `stage(path, …)`، ويُرفع `EnvRefused(f"cannot write {_real(exc, path)} …")` باسم الملفّ الذي كان يُجهَّز. ويُحذف `except OSError` حول `write_pair` في `_run` (`:854-858`، كما يسمّيه review #20).
- و`euid` وسيطٌ صريح، فيختبر الحالتين دون root ودون متغيّر بيئة.
- و`.env` الرابط الرمزيّ يبقى مقبولاً لغير root، كاليوم (`:848`).

#### 3.4.2 `deploy/resource-budget.sh`

`exec python3 -I - "$COMPOSE_FILE" …` بدل `exec python3 - …` (`:123`)، ومعه تعليقٌ: `-I` يُسقط مجلّد العمل من `sys.path` ويتجاهل `PYTHON*`، فلا يُستورد `yaml.py` مزروع (SR‑6 (ب)). **مقروء:** `PyYAML` على هذا الجهاز في حزم النظام (`python3 -c 'import yaml'` ← 6.0.1)، و`-I` لا يمسّها. و`test_resource_budget.py:332` يشغّله بـ`PATH=/usr/bin:/bin`، فيبقى أخضر.

#### 3.4.3 `deploy/host-tuning.sh` (Q‑5، FR‑10)

```
deploy/host-tuning.sh [--check|--apply] [--persist]
env: SYSCTL_ROOT (default /proc/sys), SYSCTL_D_DIR (default /etc/sysctl.d)
exit: 0 ok · 1 --check found drift · 2 usage · 3 a write failed (need root)
```

- `--persist` مع التطبيق: يولّد `"$SYSCTL_D_DIR/90-aizzak.conf"` من جدول `KNOBS` حتميّاً:

```
# Written by deploy/host-tuning.sh --persist (server-gpu-plan.md 5.2). Do not edit: re-run it.
vm.overcommit_memory = 1
```

  - ويكتبه (مؤقّتٌ ثمّ `mv`، صلاحيّة 0644) **فقط إن اختلف** المحتوى. فالتكرار لا يغيّر بايتاً ولا وقت تعديل (AC‑3.5). ويطبع `persist  <path>  ok|written`.
- `--check --persist`: ملفٌّ غائب أو مختلف انحراف، والرمز 1 (AC‑7.6).
- **دون `--persist` لا يتغيّر السلوك**، فاختبارات `test_host_tuning.py` القائمة تبقى. والوسيطان بأيّ ترتيب، وغيرهما 2.

### 3.5 الوثائق (FR‑25، FR‑33، FR‑34، A‑15)

**أمر الإصدار العامل** في `08 §3.1` و`quickstart.md` و`stack-commands.md`: `docker compose exec -T vault sh -c '<MINT_SCRIPT>'`، والنصّ **حرفيّاً** كثابت `MINT_SCRIPT` في `install.sh`. واختبارٌ يستخرجه من السكربت ويبحث عنه في الوثائق الثلاث. وأمر قراءة `role_id` المجاور مكسورٌ بالسبب نفسه (`08:808`، و`quickstart.md:209`، و`stack-commands.md:54,266`)، فيُصلَح بالغلاف نفسه في الكتل نفسها. أمّا أمرا Transit (`08:1337,1347`) فخارج النطاق، ويُسجَّلان لـ7.4.

**جمل المنع** تُستبدل وحدها (A‑15)، وأمثلة التمرير من الصدفة باقية:

| الموضع | اليوم | بعد |
|---|---|---|
| `docker-compose.yml:148-150` | «NEVER put this in .env (05 §3.3 …) … read from the invoking shell» | ثلاثة أسطر: يُحفظ في `.env`، و`deploy/release/install.sh` يكتبه هناك على الخادم (Q‑4، 2026‑10‑19). وقيمةٌ مصدَّرة في الصدفة ما زالت تفوز على `.env` |
| `05:128` | «لا سرّ في `.env` أو الصورة» | لا سرّ **تطبيقٍ** في `.env` أو الصورة. والاستثناءان: كلمات سرّ الإقلاع، و`VAULT_SECRET_ID` (§3.3) |
| `05:148` | «يُحقن كـsecret آمن» | يُحفظ في `.env`، ويكتبه `install.sh` على الخادم، ولا يُطبع ولا يُسجَّل |
| `08:800`، و`quickstart.md:204`، و`stack-commands.md:259` (عمود `VAULT_SECRET_ID`) | «يُحقن خارج الملفّ» / «من الصَّدَفة، لا من `.env`» | في `.env` (`install.sh` على الخادم)، أو من الصدفة وهي تفوز |
| `stack-commands.md:277` | «لا يُكتب في `.env` أبداً» | يُحفظ في `.env` (Q‑4) |
| `quickstart.md:353` و`stack-commands.md:635` | «لا يُكتب في `.env` أبداً؛ يُمرَّر من الصَّدَفة» | يُحفظ في `.env`، والصدفة تفوز إن مُرِّر منها |
| `.env.example:336-341` (تعليق) | «must NEVER appear here … pass it in the shell» | install.sh writes it here on a server; an exported shell value still wins. **والعدد نفسه من الأسطر** |
| `.env.example:356-360` (تعليق) | «In a real deployment, leave the pin out» | a server gets a random per-server role_id from install.sh, pinned the same way |

**`docs/deploy-server.md`:**
- يُحدَّث سطر «ما هذه الوثيقة؟».
- **قسمٌ جديد «## 3) التثبيت (`install.sh`)»** قبل «قائمة الأمان المؤجّلة»، وفيه:
  - **المتطلّبات:** `sudo`، و`docker login` بمفتاحٍ قرائيٍّ لكلّ خادم، وPython ≥ 3.10 مع PyYAML، و`curl`.
  - **التحقّق والفكّ:** `sha256sum` للحزمة مقابل ما طبعه `bundle.sh`، ثمّ `sudo tar -xzf … -C /opt`، والملكيّة لـroot شرط (SR‑6).
  - **الخيارات** بمثال `--firebase-project aizzak-agent`، و**الخطوات** بترتيبها، و**جدول الرموز** (§3.3.2).
  - **`.env`:** يُترك على صلاحيّة `umask`، ثمّ `chmod 600 .env`.
  - **التكرار وما يرفضه** (A‑13)، و«لا `docker compose up` بيدك قبل أن ينتهي»، و**ما لا يفعله** (6.1–6.5).
- **رموز الجدول تتداخل مع جدول الفحص** (0 و2 و10–14)، فيُضيَّق اختبار الفحص إلى §1، ويقرأ الاختبار الجديد §3 وحده.
- **ثغراتٌ جديدة** في الجدول المتّصل نفسه:
  - **ث‑10:** `VAULT_SECRET_ID` محفوظٌ في `.env` على الخادم (Q‑4).
  - **ث‑11:** `.env`، وفيه الأسرار الثلاث عشرة والمفتاح، يبقى دون 0600 حتّى يضبطه المالك (Q‑4، 2026‑10‑19). يخفّفه سطر التقرير.
  - **ث‑12:** الحزمة بلا توقيع. يُطبع sha256 وحده ويُقارن باليد، والتوقيع في الموجة 8.
  - وكلّ ما يجده البناء بعدها.

**`docs/server-gpu-plan.md` و`docs/server-gpu-summary.html` (FR‑34، AC‑8.4):**
- §1.1 «27 مساراً تحت `deploy/`، 25 منها في الحزمة».
- قائمة 5.1 فيها `auto_size.py` و`gpu-preflight.sh`.
- 5.2: `sudo`، و`--firebase-project`، و`docker login`، وخطوة `metrics_exporter`، وانتظار `ollama-pull`.
- اللوحة: 5.1 و5.2 «🟡 مبنيّة في PR، بانتظار الدمج وAC‑1.7 و7.3»، و5.3 «ميزةٌ تالية (Q‑3 (ب))».
- وحالة ما بعد الدمج يكتبها المنسّق في PR الإغلاق، كسابقة PR #15.

### 3.6 حسم الافتراضات وتعارضات الخطّة (requirements.md §5.10 و§6)

| البند | الحسم |
|---|---|
| A‑2 مصدر البصمات | أثر `release-digests` بـ`--digests` إلزاميّ. لا سجلّ في `bundle.sh`، والتثبيت يتحقّق بالسحب المثبّت بالبصمة (§3.3.4) |
| A‑5 `VAULT_ROLE_ID` | `uuid4` لكلّ خادمٍ قبل أوّل `up`. يثبّته `vault-bootstrap` عبر `AIZZAK_APPROLE_ROLE_ID` (`docker-compose.yml:1508`). **مقيس:** تغييره يغيّر بصمة تسع خدمات (الثماني و`vault-bootstrap`)، فكتابته بعد B2، كما يقترح تعليق «Unset in a real deployment»، كانت ستعيد إنشاء `vault-bootstrap` |
| A‑7 كلمات السرّ | `secrets.token_hex(24)` للـ13 كلّها (`^[0-9a-f]{48}$`). وقبول MinIO لاسم مستخدمٍ بـ48 حرفاً **لم يُقَس**: يُثبته الإثبات المحلّيّ (`minio-bootstrap` في B4) |
| A‑11 `up --wait` | لا يُستعمل. انتظارٌ لكلّ خطوةٍ بـ`docker inspect` (§3.3.6) |
| A‑12 / A‑13 | §3.3.1 / §3.3.5 |
| §5.10 البند 1 (24 ← 27) | القائمة 25 والحارس، وتصحيح الخطّة |
| 2 (11 ← 13) | تُشتقّ من القالب، ويحرسها اختبار |
| 3 (`auto_size.py` و`gpu-preflight.sh`) | في القائمة |
| 4 (`metrics_exporter` و`ollama-pull`) | B6 وB7 |
| 5 (مكان المفتاح) | Q‑4 ← كتلة التثبيت، والوثائق في §3.5 |
| 6 (Firebase) | `--firebase-project` إلزاميّ |
| 7 (`docker login`) | فحص السجلّ في الخطوة 1 (13/14) |
| 8 (لا إصدار بـCompose) | Q‑10: مستودعٌ مؤقّت بوسمٍ تجريبيّ في الاختبار، وAC‑1.7 بعد `v2026.10.3` |
| 9 و10 و12 و15 (5.3) | مؤجّلة. التثبيت لا يستدعي `rolling-deploy.sh` |
| 11 (`host-tuning`) | `--persist` |
| 13 (Python) | فحص الخطوة 0 (11) |
| 14 (المولِّد بصلاحيّة root) | §3.4.1–§3.4.2 |

### 3.7 الثقة بين المكوّنات (درس `monitoring-profile/retro.md`)

| من ← إلى | ما يُصدَّق، وكيف يُقيَّد |
|---|---|
| المشغّل ← الحزمة | تُنفَّذ بصلاحيّة root، فلا ثقة إلّا بحزمةٍ يملكها root وتطابق sha256 الذي طبعه `bundle.sh` (§3.5، ث‑12). و`install.sh` يرفض حزمةً أو مساراً غير مملوكٍ لـroot (3). و`python3 -I` في كلّ نداء |
| `install.sh` ← حاوية `vault` | الناتج يجب أن يكون UUID، وإلّا 61. والتوكن لا يغادر الحاوية: يُقرأ داخل `sh -c`، ولا يقرأ `install.sh` ملفّ `init.json` |
| `install.sh` ← السجلّ | البصمة في `VERSION` هي المرجع. ووسمٌ نُقل في Docker Hub لا يغيّر ما يُسحب، لأنّ `.env` يثبّت `@sha256` |
| `install.sh` ← مخرج الأدوات | `relay` يحذف رموز التحكّم. و`port` يُقبل رقماً فقط. ورموز الفحص تُطابَق بجدول، وما سواها 10 |
| الصدفة ← Compose | `scrub_environment` يحذف كلّ مفتاحٍ من القالب ومن `COMPOSE_*`، فلا يغلب تصديرٌ قديم ما في `.env` (والمولِّد يحذّر من الشيء نفسه، `auto_size.py:827-833`) |

## 4. البيانات

**لا تغيير.** لا جدول ولا عمود ولا فهرس ولا هجرة Alembic ولا سياسة RLS ولا دور قاعدة بيانات. ودور `metrics_exporter` موجود (`15-metrics-exporter.sh`)، و`install.sh` يضبط كلمة سرّه بالسكربت القائم كما في `08 §3.3‑ج` ①. **فمرحلة `data-engineer` تُتخطّى.**

## 5. خطّة الاختبار المقترحة

كلّ الآليّ داخل `pytest tests/unit tests/architecture tests/eval`. لا مكدّس حيّ ولا شبكة. ومعايير [config] تمحو `COMPOSE_FILE` و`COMPOSE_PROFILES` و`COMPOSE_PROJECT_NAME`، وتعمل في نسخٍ مؤقّتة، وتتخطّى بسببٍ مكتوب إن غاب Docker (NFR‑11).

### 5.1 العُدّة (`tests/unit/support_install.py`)

- **`bundle_tree(tmp_path, version="2026.10.99", digests=None) -> Path`:** شجرة حزمةٍ مفكوكة دون `bundle.sh`، للسرعة. تُنسخ مسارات `bundle-files.txt` من شجرة العمل، و`deploy/release/install.sh` إلى الجذر، و`VERSION` ببصماتٍ مزيّفة.
- **`release_repo(tmp_path) -> Path`:** مستودع git مؤقّت (`git init`) فيه المسارات الـ34 و`deploy/release/` كاملاً من شجرة العمل.
  - **ومعها أفخاخٌ متتبَّعة:** `src/app/x.py`، و`tests/x.py`، و`deploy/runpod/x`، و`deploy/load/tokens.json`، و`docker-compose.build.yml`.
  - ثمّ commit ووسم `v2026.10.99`.
  - **وأفخاخٌ غير متتبَّعة** (AC‑1.2): `.env`، و`deploy/nginx/certs/server.key`، و`deploy/postgres/sizes/auto.conf`، و`init.json`، و`.auto-size-tmp-x`، وملفٌّ متتبَّع معدَّلٌ دون commit.
- **أدواتٌ مزيّفة** على رأس `PATH`، بـ`#!<sys.executable> -IS`:
  - **`docker`:** آلة حالةٍ في JSON، على نمط `test_rolling_deploy.py:297-424`. تفهم `info` و`manifest inspect` و`image inspect` و`pull`، و`compose … config --images|--services|--hash '*'` و`up -d [svc]` و`ps -a -q [svc]` و`exec` و`port`، و`inspect --format`.
    - **نموذج البصمة:** `sha256(service + .env)`، ويُحذف منه سطر `VAULT_SECRET_ID` لغير الحاملات التسع. وهذا نموذج القياس في §1.
    - **ولكلّ نداءٍ سطرٌ في `calls.jsonl`:** `argv` و`cwd` والبيئة كاملةً و`sha256` لما جاء على stdin. **لا يُكتب stdin نفسه أبداً.**
  - **`curl`:** يردّ رمزاً من الحالة.
  - **`df`:** يردّ المساحة من الحالة، فلا تتعلّق النتيجة بقرص مشغّل CI.
  - **`python3`:** رابطٌ إلى `sys.executable`، فيه PyYAML.
- **بدائل داخل الشجرة عند الطلب:**
  - `deploy/gpu-preflight.sh` يخرج برمزٍ مختار.
  - `deploy/auto_size.py` يسجّل `argv` و`sys.flags.isolated` ثمّ يخرج برمز. **أو** غلافٌ يستدعي المولِّد الحقيقيّ و`--host-cpus 16 --host-memory-gb 32`، فالنتيجة لا تتعلّق بالمشغّل.
  - `deploy/resource-budget.sh` يسجّل بيئته ويخرج برمز.
- **`host-tuning.sh` حقيقيّ** بـ`SYSCTL_ROOT` و`SYSCTL_D_DIR` مؤقّتين.
- **`run_steps(tree, *args, umask=0o022)`:** `bash -c 'umask 022; source "$1"; shift; prepare "$@" && run_steps' _ tree/install.sh …`، بـ`stdin=DEVNULL`. و**`run_entry(...)`** يشغّل `./install.sh` نفسه، لمسارات ما قبل root.
- **تشغيلاتٌ مشتركة** (`scope="module"`) تخدم معايير كثيرة، لحدّ NFR‑10 (§5.3).

### 5.2 لكلّ معيار

| AC | المستوى | المكان | الطريقة |
|---|---|---|---|
| AC‑1.1 | وحدة | `test_release_bundle.py` | `bundle.sh` في `release_repo`. قائمة `tar -tzf` (ملفّات) = توسيع `git ls-files` لـ`bundle-files.txt` عند الوسم، و`install.sh`، و`VERSION`، تحت `aizzak-2026.10.99/`. و`install.sh` بصلاحيّة 0755 ويساوي `deploy/release/install.sh` بايتاً ببايت. ولا `upgrade.sh` |
| AC‑1.2 | وحدة | نفسه | لا فخّ من المزروعة ولا المتتبَّعة في الأرشيف. وقائمة FR‑3 في الاختبار **مرجعٌ مستقلّ**، لا منسوخةٌ من `bundle.sh`. والملفّ المعدَّل بنسخة الوسم |
| AC‑1.3 | وحدة | نفسه | `VERSION` خمسة أسطر، وتعبير AC‑1.3، وcommit = `git rev-parse v2026.10.99^{commit}`، ولا `dev` ولا `latest` ولا `digest-unknown` |
| AC‑1.4 | وحدة | نفسه | مُعلَّمة بالحالات: `v2026.10.99` و`app-2026.10.99` والفارغ ← 2. ووسمٌ غائب ← 4. وسطرٌ ناقص و`digest-unknown` وبصمةٌ بـ63 حرفاً وcommit مخالف ← 5. ومجلّد 0555 ← 6 (يُتخطّى تحت root). وحزمةٌ موجودة ← 6 وsha256 لها كما كان. **وفي كلّ حالة: قائمة مجلّد الإخراج قبل وبعد واحدة** |
| AC‑1.5 | config | `test_install_sh_config.py` | تُفكّ الحزمة المبنيّة في مجلّدٍ مؤقّت، و`.env` من القالب مع أسطر الاختيار والصور. 20 تركيبة بـ`ThreadPoolExecutor(4)`. و`auto` بـ`python3 -I deploy/auto_size.py --role server --host-cpus 16 --host-memory-gb 32 --mode … --profiles …` داخل النسخة. والرمز 0، و36/24/35/24 خدمة (§5.7)، ولا `:dev` ولا `:latest`. **ومتغيّر الاختبار `AIZZAK_BUNDLE=<path>`** يشغّله على حزمةٍ حقيقيّة لـAC‑1.7 |
| AC‑1.6 | وحدة | `test_release_bundle.py` | بناءان في مجلّدين، وبينهما `touch` على شجرة العمل، والـsha256 واحد |
| AC‑1.7 | مالك | — | بعد `v2026.10.3` وش‑4: `gh run download <run> -n release-digests`، ثمّ `deploy/release/bundle.sh --digests release-digests.txt 2026.10.3`، ثمّ `AIZZAK_BUNDLE=… pytest tests/unit/test_install_sh_config.py -k bundle`. والوسم وsha256 في `status.md` |
| AC‑2.1 | وحدة (+config) | `test_release_bundle_guard.py` | `deploy_binds()` من YAML الملفّين، بكلّ الخدمات وكلّ الصيغ. ومتغيّرٌ في مسارٍ تحت `./deploy/` خطأ. ثمّ `check(binds, listed, EXCLUDED, REQUIRED_EXTRA) -> list[str]` فارغة، وشرطها `set(listed) == (binds − EXCLUDED) ∪ REQUIRED_EXTRA`. و`EXCLUDED = {"deploy/load": "...", "deploy/nginx/certs": "..."}` بأسبابهما. وفحصٌ متقاطع: إن وُجد Docker، `config --format json --profile '*'` في نسخةٍ مؤقّتة يعطي المجموعة نفسها |
| AC‑2.2 | وحدة | نفسه | الإفساد في نسخٍ داخل الذاكرة، في الاتّجاهين: (أ) ربطٌ جديد `./deploy/new.conf` في YAML. (ب) حذف مسار. (ج) إضافة `deploy/load` أو `deploy/nginx/certs` إلى القائمة. (د) استثناءٌ ليس ربطاً. والرسالة في كلّها تسمّي المسار |
| AC‑2.3 | وحدة | نفسه | `git ls-files -- <p>` غير فارغ لكلّ مدخل، وكلّ مدخلٍ موجود |
| AC‑2.4 | وحدة | `test_gpu_compose_hygiene.py` (`_SCRIPTS`) و`test_install_sh.py` | لا `source .env`، ولا بيانات اعتماد (الفحص القائم). وفي `install.sh` و`bundle.sh`: لا `docker volume rm` ولا `prune` ولا `down` ولا `--force-recreate` ولا `operator init` ولا `config` عارٍ (تعبيرٌ على النصّ) |
| AC‑3.1 | وحدة | `test_install_sh.py` | بديل الفحص بالرموز 0 و10 و11 و12 و13 و14، و12 مع `--cpu`، و0 مع `--cpu`. والوضع من `COMPOSE_FILE` الناتج. **وعند كلّ توقّف (10):** لا `.env`، ولا نداء `compose` في `calls.jsonl`، وقائمة الملفّات قبل وبعد واحدة |
| AC‑3.2 | وحدة | نفسه | `run_entry` بـ`python3` مزيّف يقول 3.9، ثمّ بلا `yaml` ← 11 قبل فحص root. و`df` مزيّف تحت 40 ج.ب في تثبيتٍ أوّل ← 12، والرسالة بالـGB. ولا كتابة |
| AC‑3.3 | وحدة | نفسه | `manifest inspect` مزيّف يقول «unauthorized» ← 13، ومعه `docker login` وعبارة «read-only». و«dial tcp … i/o timeout» ← 14. ولا كتابة. **وثابت:** `parse_args` لا يقبل خياراً غير الستّة، والسكربت لا يذكر `config.json` |
| AC‑3.4 | وحدة | نفسه | `run_entry` من تخطيط مستودع: `docker-compose.yml` و`.env` و`deploy/release/install.sh`، بلا `VERSION` ← 3 و«run it from the unpacked bundle». sha256 لـ`.env` وقائمة الملفّات كما هما. والمتغيّر: `VERSION` موجود مع `.git/` ← 3 |
| AC‑3.5 | وحدة | `test_install_sh.py` و`test_host_tuning.py` | **root:** `step_kernel` بالمجلّدين المؤقّتين يكتب `vm/overcommit_memory` وملفّاً واحداً في `sysctl.d`، والتكرار لا يغيّر sha256 ولا `mtime`. **غير root:** `run_entry` ← 4، ومعه `sudo ./install.sh`، ولا كتابة. ومعهما `--persist` و`--check --persist` في `test_host_tuning.py` |
| AC‑3.6 | خادم | 7.3 | — |
| AC‑4.1 | وحدة | `test_install_sh.py` (التشغيل الأساسيّ المشترك) | `umask 022`، فـ`.env` بصلاحيّة 0644، ولا ملفٌّ جديد غيره. ولا قيمة فعّالة تبدأ بـ`change-me`. والـ13 بتعبير `^[0-9a-f]{48}$` وبلا تكرار. وبقيّة مفاتيح القالب بقيمها |
| AC‑4.2 | وحدة | نفسه | لا شيء من القيم الـ13 ولا التوكن المزروع في `init.json` المزيّف ولا `secret_id` المزيّف في stdout وstderr و`calls.jsonl` (`argv` والبيئة). وكلمة سرّ `metrics_exporter` تظهر فقط sha256 لـstdin نداء `exec -T postgres`، وتساوي sha256 القيمة |
| AC‑4.3 | وحدة | نفسه | `valid_domain` بجدولٍ للحالات الثماني، ثمّ `env_tool compose` لـ`Example.COM`: الأسطر الثلاثة بحروفٍ صغيرة. والمرفوض ← 2، ومعه الصيغة، و`xn--` للعربيّ |
| AC‑4.4 | وحدة | نفسه | `--firebase-project` غائب، أو `demo proj`، أو `a/b` ← 2 قبل `.env`، والرسالة «the app does not start without it» |
| AC‑4.5 | وحدة | نفسه | `env_tool compose` للتركيبات الأربع: `COMPOSE_*` كما في §5.7، والصور الستّ = `VERSION`، و`VAULT_TOKEN` فارغ، و`VAULT_ROLE_ID` UUID ليس `aizzak-local-app` |
| AC‑4.6 | وحدة | نفسه | بعد التشغيل الأساسيّ: `LOG_LEVEL=DEBUG` و`chmod 600`، ثمّ تكرار. sha256 لكلٍّ من الـ13 وللملفّ كلّه كما كان، والصلاحيّة 0600. **ومعه المولِّد الحقيقيّ بالغلاف**، فتمرّ إعادة كتابة كتلته أيضاً |
| AC‑4.7 | وحدة | نفسه | تكرارٌ بـ`--domain other.com`، ثمّ `--size medium` بعد `auto`، ثمّ `--cpu` بعد الكرت ← 30، والرسالة تسمّي `MINIO_PUBLIC_ENDPOINT` و`size` و`COMPOSE_FILE`، وبايتات `.env` كما هي |
| AC‑5.1 | وحدة | نفسه | بديل المولِّد بالرموز 0 و1 و2 و3 ← يكمل، و40 (ولا `pull`)، و41، و41 (و`.env` كما هو). `argv` فيه `--role server --mode gpu --profiles local-llm,monitoring`، و`sys.flags.isolated == 1` |
| AC‑5.2 | وحدة | نفسه | `--size medium`: كتلة `aizzak size` = أسطر `medium.env`، ولا كتلة `auto`. والميزانيّة مزيّفة وتُستدعى بـ`--size medium`، ثمّ بـ`--without-monitoring` مع `--no-monitoring`. و`--size huge` ← 2 ولا كتابة |
| AC‑5.3 | وحدة | نفسه | `COMPOSE_FILE` بقيمة CI مصدَّراً في الصدفة، والميزانيّة مزيّفة تخرج بـ1. في بيئتها `COMPOSE_FILE=docker-compose.yml` و`GPU_COMPOSE_FILE=docker-compose.gpu.yml`، والرمز 40، ولا `pull` في `calls.jsonl`، والتحذير يسمّي `COMPOSE_FILE` |
| AC‑5.4 | config | `test_install_sh_config.py` | المولِّد الحقيقيّ في الحزمة المفكوكة على 8/16 و16/32 و32/64 و8/12، لكلّ وضع، بالتوازي. الثلاثة الأولى بـ0، ثمّ `--block` بـ0 و`config -q` بـ0. و8/12 بـ1 ولا كتابة |
| AC‑5.5 | وحدة | `test_auto_size_edges.py` و`test_resource_budget.py` | (أ) `main(..., euid=0)` على رابطٍ و`.env` يملكه المستخدم ← 3 ولا كتابة، و`euid=os.geteuid()` ← يمرّ. (ب) `yaml.py` مزروع في `cwd` يكتب علامةً إن استُورد: لا علامة. (ج) `deploy/postgres/sizes` بصلاحيّة 0555 (لغير root) ← stderr فيه `auto.conf` وليس فيه `.env (`. (د) 0644 ← التحذير باسم الملفّ وحده، والرمز 0. **والملفّات القائمة خضراء** |
| AC‑5.6 | خادم | 7.3 | — |
| AC‑6.1 | وحدة + ثابت | `test_install_sh.py` و`test_release_bundle_guard.py` | ترتيب النداءات في التشغيل الأساسيّ: `pull` ثمّ `image inspect` للبصمات ثمّ `up -d vault` ثمّ `up -d vault-bootstrap` ثمّ `exec -T vault` ثمّ `up -d minio-bootstrap` ثمّ `up -d migrate` ثمّ `exec -T postgres` ثمّ `up -d ollama-pull` ثمّ `up -d`. ولا `up` ولا `run` ولا `create` قبل `exec -T vault` يسمّي حاملاً. **وثابت من YAML:** الاعتمادات المتعدّية لـ`vault` و`vault-bootstrap` لا تحوي أيّاً من الحاملات التسع |
| AC‑6.2 | وحدة | `test_install_sh.py` | نداء الإصدار `compose … exec -T vault sh -c <MINT_SCRIPT>`، وفيه `VAULT_ADDR=http://127.0.0.1:8200` و`/vault/init/init.json`. وفي `.env` سطر `VAULT_SECRET_ID=` واحد، والصلاحيّة قبل وبعد واحدة. وناتجٌ فارغ أو `not-a-uuid` ← 61، ولا `up` لـ`minio-bootstrap` ولا لـ`migrate` |
| AC‑6.3 | وحدة | نفسه | `RepoDigests` مزيّف يخالف `VERSION` ← 51، والرسالة تسمّي الصورة والبصمتين، ولا `up` |
| AC‑6.4 | وحدة | نفسه | `migrate` ينتهي بـ1 مرّةً ثمّ بـ0. الأوّل ← 60 ومعه `docker compose logs migrate`. والثاني ← 0، وsha256 الـ13 كما هو، و`exec -T vault` واحدٌ في التشغيلين معاً |
| AC‑6.5 | محلّيّ | §5.4 | — |
| AC‑6.6 | خادم | 7.3 | — |
| AC‑7.1 | وحدة | `test_install_sh.py` (من حالةٍ متقاربة، فالتشغيل رخيص) | `curl` بـ200 ← 0 و`READY`. و502 ← 70 و`NOT READY` و`failed: /health`. و`stack_smoke` بـ1 ← 71. والرابط `http://127.0.0.1:20080/…` من `port` المزيّف |
| AC‑7.2 | وحدة | نفسه | التقرير مع المراقبة ودونها: الرابط، و`/health`، والحجم بـ`vCPU` وGB، و`GPU: yes|no`، و`monitoring: on|off`، و`NO ALERTS`، واسم `aizzak_vault-init`، و`chmod 600 .env` مرّةً واحدة، والانتهاء = وقت الإصدار + 720 ساعة. ولا سرّ (فحص AC‑4.2 على المخرج) |
| AC‑7.3 | وحدة | نفسه | تكرارٌ على الحالة المتقاربة: لا `exec -T vault`، ولا `up` إطلاقاً، ولا `--force-recreate` ولا `operator init` في أيّ `argv`، وsha256 لـ`.env` وصلاحيّته كما كانا. **ومعه:** `SECRET_ID_TTL_H` = `secret_id_ttl=720h` في `bootstrap.sh` |
| AC‑7.4 | محلّيّ | §5.4 | — |
| AC‑7.5 | وحدة | `test_install_bundle_docs.py` | رموز رأس `install.sh` ومعانيها = صفوف جدول §3 في `deploy-server.md`. و`test_gpu_preflight.py` يقرأ §1 وحده |
| AC‑7.6 | خادم | 7.3 | — |
| AC‑8.1 | نصّ | نفسه | لا `exec vault vault write -f -field=secret_id` ولا `exec vault vault read -field=role_id` في الوثائق الثلاث. و`MINT_SCRIPT`، مستخرجاً من `install.sh`، في كلٍّ منها |
| AC‑8.2 | نصّ | نفسه | في `05` و`08` و`quickstart` و`stack-commands` و`docker-compose.yml` و`.env.example`: لا «لا يُكتب في `.env` أبداً»، ولا «لا من `.env`»، ولا «يُحقن خارج الملفّ»، ولا «NEVER put this in .env»، ولا «must NEVER appear here». وكلٌّ منها يذكر `install.sh` مع `VAULT_SECRET_ID`. ولا `install.sh` يتبعه `0600` في السطر نفسه. وأمثلة `VAULT_SECRET_ID='<` باقية في `08` و`quickstart` و`stack-commands`. وتساوي البصمات في AC‑8.5 |
| AC‑8.3 | نصّ | نفسه | §3 من `deploy-server.md` فيه `sudo` و`docker login` و`python3` و`--firebase-project` و`aizzak-agent` و`umask` و`chmod 600 .env` و`sha256sum`، وجدول الرموز. وصفّا ث‑10 وث‑11 في الجدول المتّصل (الاختبار القائم). ولا أنماط أسرار |
| AC‑8.4 | نصّ | نفسه | الخطّة: §1.1 فيها 27 و25، و5.1 فيها `auto_size.py` و`gpu-preflight.sh`، و5.2 فيها `metrics_exporter` و`--firebase-project` و`docker login`. وصفوف 5.1–5.3 ليست ⬜، و5.3 «ميزةٌ تالية». والملخّص يذكر الحالات نفسها |
| AC‑8.5 | بوّابات | `test-plan.md` (QA) | البوّابات الستّ بمخرجها. و`pytest tests/unit --durations=0` قبل وبعد. و`git diff master -- docker-compose.yml docker-compose.gpu.yml deploy/sizes deploy/postgres/sizes` فيه الأسطر 148‑150 وحدها. **وقياسٌ لمرّةٍ واحدة لا اختبارٌ دائم:** `config --hash '*'` لملفّ `master` ولملفّ الفرع، بـ`.env.example` في scratch، لكلّ تركيبة، ومتساوية |

**حارسٌ إضافيّ** في `test_install_sh_config.py` يحفظ مبدأ §1، ويتخطّى دون Docker: `.env` من `env_tool compose` قبل `set-secret-id` وبعده. وفرق `config --hash '*'` = الحاملات الثماني بالضبط، و`vault` و`vault-bootstrap` لم يتغيّرا.

### 5.3 الزمن (NFR‑10: ≤ 15 ث زيادة)

**مقيس:** أداةٌ مزيّفة بـ`-IS` تأخذ نحو 15 م.ث للنداء (20 نداءً في 0.30 ث)، و`docker compose config -q` نحو 0.24 ث، وأربعةٌ بالتوازي 0.14 ث.

**التوزيع (تقدير):**

| البند | الزمن |
|---|---|
| مستودع الحزمة وبناءان | 1.5 ث |
| الحارس | 0.6 ث |
| `install.sh`: ثلاثة تشغيلاتٍ كاملة مشتركة (الأساسيّ، والمعالج دون مراقبة، وAC‑6.4)، ونحو 30 تشغيلاً قصيراً أو من حالةٍ متقاربة | 5.5 ث |
| [config] بالتوازي | 4 ث |
| الباقي | 1 ث |
| **المجموع** | **≈ 12.6 ث** |

والباني يقيس بـ`--durations`. وإن تجاوز الحدّ، فالقطع من تشغيلات [config] المكرّرة قبل غيرها.

### 5.4 الإثبات المحلّيّ (AC‑6.5 وAC‑7.4، Q‑7، مرّةً واحدة بعد البناء)

**كيف تُنقل المنافذ دون تغيير بصمات جهاز المالك (القاعدة 1):**
- القيم في **`.env` الإثبات وحده**، داخل الحزمة المفكوكة في scratch. **مقيس:** `COMPOSE_PROJECT_NAME` في `.env` يغلب `name: aizzak`.
- `install.sh` لا يلمس مفتاحاً غير مُدار (§3.3.5).
- ولا يُقرأ `/home/AIZZAK/.env` ولا يُعدَّل، ولا ملفّا Compose. فبصمات مشروع `aizzak` تُحسب من ملفّاته كما هي.
- **مقيس:** نقل `HOST_PORT_HTTP` لا يغيّر في مشروع الإثبات إلّا بصمة `nginx`.
- ولا اسم ثابت لحاويةٍ أو حجمٍ أو شبكة في الملفّين، فكلّها بادئة المشروع.
- **و«سحب الغائب وحده» (§3.3.4)** لا يغيّر وسماً تقرؤه حاويات المالك.

**الخطوات:**
1. **الحزمة:** وسمٌ محلّيّ خفيف `v2026.10.97` على commit الفرع. **لا يُدفع أبداً:** `release.yml` يعمل عند دفع `v*`.
   - ملفّ البصمات فيه **بصمتا `app-2026.10.1` و`embedding-2026.10.1` الحقيقيّتان**، بوسمَي `app-2026.10.97` و`embedding-2026.10.97`.
   - **مصدرهما بالترتيب:** أثر `release-digests` لـ`v2026.10.1` (باقٍ 90 يوماً من 2026‑10‑14، ويحتاج `gh auth login`)، ثمّ `RepoDigests` إن كانت الصورتان على الجهاز، ثمّ `docker buildx imagetools inspect` بعد الدخول.
   - وبصمة CUDA مزيّفة (64 صفراً): وضع المعالج لا يسحبها ولا يتحقّق منها.
   - ثمّ `deploy/release/bundle.sh --digests … --out /tmp/claude-1000/ib-proof 2026.10.97`.
2. **خطّ الأساس** لمشروع `aizzak`: `docker ps -a --filter label=com.docker.compose.project=aizzak --format '{{.ID}} {{.Names}} {{.CreatedAt}}'`، و`docker volume ls --filter label=com.docker.compose.project=aizzak`. ثمّ إيقاف المكدّس الحيّ: `docker compose stop` في `/home/AIZZAK` بالخدمات المسمّاة من القائمة (`CLAUDE.md`، قرار 2026‑10‑05).
3. **الفكّ:** `sudo tar -xzf aizzak-2026.10.97.tar.gz -C /tmp/claude-1000/ib-proof`، فالملكيّة لـroot (SR‑6). ثمّ `.env` الإثبات:
   - `sudo cp .env.example .env`.
   - ثمّ `sudo sed -i` لمنافذ `HOST_PORT_*` الأحد عشر بإضافة 20000: 20080 و20443 و35432 و36432 و36379 و36380 و39000 و39001 و36333 و38200 و33000. ويُتحقَّق قبلها بـ`ss -ltn` أنّها حرّة.
   - وسطر `COMPOSE_PROJECT_NAME=aizzak-install-proof`.
   - هذا مسار «`adopted`»، فيُثبَت حيّاً.
4. **الدخول:** المالك يشغّل `sudo docker login` بنفسه، في طرفيّته (⚠️‑1).
5. **التشغيل:** `sudo SYSCTL_ROOT=<scratch>/sysctl SYSCTL_D_DIR=<scratch>/sysctl.d ./install.sh --domain install-proof.invalid --firebase-project aizzak-agent --cpu --no-monitoring`.
   - المجلّدان المؤقّتان لا يغيّران نواة WSL ولا `/etc`. ويُنشأ `<scratch>/sysctl/vm/overcommit_memory` مسبقاً بالقيمة الحاليّة.
   - **مقيس:** `auto_size.py --role server --mode cpu --profiles local-llm` على هذا الجهاز (10 / 12.67) ← 0، بـ**11.39 من 11.40 ج.ب** مسموحة. ومع المراقبة 1 (12.47 مطلوبة)، والكرت 1، و`resource-budget.sh --size small` 1 بالمراقبة ودونها. **فهذه التركيبة الوحيدة التي تمرّ هنا.**
6. **AC‑6.5:**
   - الرمز 0 و`READY`.
   - `docker compose logs vault | grep -c 'initialized; unseal key'` = 1.
   - لكلّ حاملٍ دائم: `docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' <id> | awk -F= '$1=="VAULT_SECRET_ID"{print length($2)}'` = 36. **الطول وحده.**
   - وقائمتا مشروع `aizzak` كخطّ الأساس.
7. **AC‑7.4:** قبل التشغيل الثاني وبعده: `config --hash '*'` في مجلّد الإثبات (ناتجه بصماتٌ فقط)، ومعرّفات الحاويات و`StartedAt`. **والمتوقَّع: سطر «already converged» ولا نداء `up`.**
8. **التنظيف:**
   - `sudo docker compose down` في مجلّد الإثبات، **بلا `-v`**.
   - `docker compose start` للمكدّس الحيّ، ثمّ فحص صحّته.
   - `git tag -d v2026.10.97`، و`sudo docker logout`.
   - أحجام `aizzak-install-proof_*` ومجلّد scratch يحذفها المالك.
   - والصور المسحوبة (نحو 5 ج.ب، تقدير) باقية حتّى يقرّر.

**إن رُفض الحجم يومها (40):** النتيجة صادقة، ويصير AC‑6.5 وAC‑7.4 [خادم]. ولا تجاوز للميزانيّة في السكربت.

## 6. ترتيب التنفيذ والتوازي

كاتبٌ واحد في مستودعٍ واحد، فالتنفيذ متتابع، ولكلّ خطوةٍ commit. والمستقلّ بعضه عن بعض يجوز تبديل ترتيبه.

1. **مستقلّة:**
   - (أ) FR‑26: `auto_size.py` و`resource-budget.sh` واختباراتهما (AC‑5.5).
   - (ب) `host-tuning.sh --persist` واختباره.
   - (ج) `bundle-files.txt` والحارس (US‑2).
2. `bundle.sh` واختباراته (US‑1 [وحدة])، بعد 1(ج).
3. `install.sh`، بعد 1 كلّه:
   - الهيكل: الثوابت، والرأس، والرموز، و`prepare`، و`locate_root`، و`read_version`، و`parse_args`، والفحوص، و`require_*`.
   - ثمّ `env_tool` (US‑4).
   - ثمّ الحجم والميزانيّة (US‑5).
   - ثمّ السحب والتحقّق.
   - ثمّ الإقلاع (US‑6).
   - ثمّ الفحوص والتقرير (US‑7).
   - و`support_install.py` ينمو معه.
4. اختبارات [config]: AC‑1.5، وAC‑5.4، وحارس البصمة. بعد 2 و3.
5. **الوثائق** (US‑8): FR‑25، وتعليقا Compose و`.env.example`، و`deploy-server.md` §3 والثغرات، وتضييق `test_gpu_preflight.py`، والخطّة والملخّص، و`test_install_bundle_docs.py`. **يجوز بالتوازي مع 3**، إلّا جدول الرموز فبعد تثبيت رأس `install.sh`.
6. البوّابات الستّ، و`--durations`، وقياس NFR‑1 لمرّة (AC‑8.5).
7. بعد البناء وقبل البوابة ②: الإثبات المحلّيّ (§5.4، qa-engineer)، بعد حسم ⚠️‑1. ثمّ المراجعتان.

## 7. المخاطر والقرارات

### بحاجةٍ إلى قرارٍ بشريّ

- ⚠️‑1 **اعتماد السجلّ في الإثبات المحلّيّ (Q‑7) على جهاز المالك.** يحجب الإثبات وحده، لا البناء.
  - **السبب:** `install.sh` يعمل root، فيقرأ اعتماد Docker لـroot (`/root/.docker/config.json`)، لا اعتماد المالك. ودونه يقف التثبيت بالرمز 13.
  - **(أ) المقترح:** يصنع المالك مفتاح Docker Hub **قرائيّاً** لهذا الجهاز بوصفه «خادم إثبات»، على قاعدة Q‑9. ويشغّل `sudo docker login` بنفسه قبل الإثبات و`sudo docker logout` بعده. ولا يرى الوكيل المفتاح. فيمرّ الإثبات بمسار الخادم الحقيقيّ نفسه.
  - **(ب)** `sudo DOCKER_CONFIG=/home/anas/.docker ./install.sh …`: يستعمل دخول المالك القائم، ولا يحفظ اعتماداً جديداً. لكنّ عمليّة root تستعمل مفتاحاً لا نعرف صلاحيّته (قد يكون للرفع)، ولا يمرّ بمسار الخادم.
  - **(ج)** سحب الصور مسبقاً بحساب المالك: **لا يكفي**. فحص السجلّ في الخطوة 1 يحتاج الاعتماد على أيّ حال.
  - **قرار المالك (2026‑10‑19): (ب)** «دخولي الحاليّ». ولا يقرأ وكيلٌ `/home/anas/.docker/config.json`. و`sudo` يطلب كلمة سرّ، فأوامر root في الإثبات يشغّلها المالك (`status.md`).

### قراراتٌ اتّخذها التصميم (لا تحتاج حسماً، وتُراجَع في المراجعة)

1. **bash مع برنامج Python واحد مضمَّن، لا ملف Python جديد.** سابقته `resource-budget.sh:123`، فيبقى العمل داخل قائمة Q‑1. والثمن أنّ البرنامج المضمَّن خارج `ruff`، فتحرسه الاختبارات وحدها.
2. **«سحب الغائب وحده» بدل `docker compose pull`** (§3.3.4). يحفظ FR‑23 والقاعدة 1. وثمنه أنّ إعادة التثبيت لا تحدّث صور الطرف الثالث، فتحديثها 5.3.
3. **`VAULT_ROLE_ID` عشوائيّ لكلّ خادم ومثبَّت** (A‑5)، مع أنّ تعليقين في ملفّين محميّين يقولان «Unset in a real deployment»: `docker-compose.yml:1503-1508` و`deploy/vault/bootstrap.sh:151-160`. **لا يُعدَّلان هنا**، لأنّهما خارج قائمة Q‑1، ولا يغيّران سلوكاً. ويقول §3 من `deploy-server.md` و`08 §3.1` ما يحدث على الخادم. وإن وسّع المالك Q‑1 بهما، فتعديلهما تعليقٌ وحده. **قرار المالك (2026‑10‑19): «نعم، صحّحهما».** فيُعدَّل نصّ التعليقين وحده، ليقولا ما يحدث فعلاً: `install.sh` يضع لكلّ خادم معرّفاً عشوائيّاً ويثبّته `vault-bootstrap`. ولا يتغيّر سطرٌ غير تعليق. وعلى جهاز المالك يبقى `aizzak-local-app` الافتراضيّ. والانحرافان في القرارين 2 و5 وافق عليهما المالك أيضاً.
4. **المخرج بالإنجليزيّة** (§3.3.1)، كبقيّة سكربتات `deploy/`. والعبارات المختبرة مكتوبة هناك.
5. **`bundle.sh` لا يفحص نسب `master`**، والربط بالمصدر عبر commit البصمات (§3.2.2).
6. **فشل `ollama-pull` يوقف التثبيت (60)**، ولا «READY» بمسار محادثةٍ مكسور.
7. **فحص المساحة في التثبيت الأوّل وحده** (§3.3.4).
8. **تفسير «بدون 0600»** للملفّ كلّه (Q‑4): ينفّذه التصميم كما في `status.md`، ويبقى للتأكيد عند المراجعة كما سجّل المنسّق.

### مخاطر

- **الثغرة الأمنيّة المقبولة (Q‑4):** `.env` بصلاحيّة 0644 فيه كلّ الأسرار والمفتاح، حتّى يضبطه المالك (ث‑10 وث‑11). ويخفّفه سطر التقرير وتحذير SR‑11 في كلّ تثبيت.
- **`secret_id` يعيش 720 ساعة ولا يُجدَّد قبل 6.5:** خادمٌ يُثبَّت اليوم يفقد Vault بعد 30 يوماً. والتقرير يطبع التاريخ.
- **هامش الإثبات المحلّيّ 0.01 ج.ب** (§5.4). أيّ تغيّرٍ في ذاكرة WSL يجعله [خادم].
- **ما لم يُقَس، ويُثبته الإثبات المحلّيّ أو 7.3:**
  - قبول MinIO لاسم مستخدمٍ بـ48 حرفاً.
  - `docker manifest inspect` بمفتاحٍ قرائيّ.
  - عثور Compose على صورةٍ سُحبت بالبصمة وحدها وتُسمّى `repo:tag@digest`. وإن لم يجدها: `docker tag` محلّيّ قبل التشغيل، في الإثبات وحده.
- **زمن الاختبارات قريبٌ من حدّ NFR‑10** (§5.3، تقدير).
- **ميزانيّة الحجم المسمّى تحكم بوضع ملفّ الحجم (`mode=gpu`)** في `resource-budget.sh --size`، ولو كان التثبيت بالمعالج. هذا سلوكٌ قائم من 3.4، لا تغيّره الميزة، ويُسجَّل لـ7.3.
- **أوامر Vault المكسورة الأخرى** (`08:1337,1347`، Transit) تبقى كما هي (A‑15)، وتُسجَّل لـ7.4.

</div>
