<div dir="rtl">

# المتطلبات: حزمة تثبيتٍ بدل المستودع (المرحلة 5 من خطّة خادم الكرت)

> **المصدر:** طلب المالك (2026‑10‑18): «`server-gpu-plan.md` ابدأ المرحلة الخامسة». والخطّة [`docs/server-gpu-plan.md`](../../server-gpu-plan.md):
> - §5 «المرحلة 5»: 5.1 (الأسطر 324–333)، و5.2 (335–352)، و5.3 (354–363).
> - اللوحة §4 (135–137)، والقواعد §3 (93–106)، وقرارات §6 (449–459).
> - صفّا «الإعداد من المستودع» و«دخول Vault يدويّ» في §1.1 (57، 65).
>
> والقصص ومعايير القبول في [`stories.md`](stories.md).
>
> **كلّ رقمٍ هنا مقيسٌ ومعه طريقة قياسه، أو معلَّمٌ «تقدير»** (درس `monitoring-profile/retro.md:86`).
>
> **بوابة ① اكتملت (2026‑10‑19).** نصّ المالك: «تم الموافقة على النطاق مع التوصيات، اكمل العمل». والقرارات في §6، ومصدرها [`status.md`](status.md) «قرارات وتصعيدات». فالنطاق 5.1 و5.2، و5.3 ميزةٌ تالية (Q‑3 (ب)). و`VAULT_SECRET_ID` يُكتب في `.env`، و`install.sh` لا يضبط صلاحيّة `.env` (Q‑4، قرار المالك).

## 1. الخلفية والهدف

اليوم لا يُثبَّت المكدّس على خادمٍ إلّا من نسخةٍ كاملة من المستودع. والخطوات يدويّة، وإحداها مكسورة:
- **الإعداد من المستودع:** Compose يربط 27 مساراً تحت `./deploy/` (§5.1). فالخادم يحتاج المستودع كلّه.
- **كلمات السرّ يدويّة:** 13 قيمةً في `.env.example` تبدأ بـ`change-me` (§5.2). و`10-roles.sh` يكتبها في Postgres مرّةً واحدة عند أوّل تهيئة (`deploy/postgres/initdb/10-roles.sh:125-169`)، فكلمةٌ ضعيفة تبقى مع الدور.
- **دخول Vault مكسور:** الأمر الموثَّق لإصدار `VAULT_SECRET_ID` يفشل منذ خرج Vault من وضع dev (`server-gpu-plan.md:65`).
- **التحديث بلا أداة:** لا شيء يأخذ نسخةً احتياطيّة ثمّ يسحب ويهاجر ويبدّل الخدمات بترتيب. وأداته (5.3) ميزةٌ تالية (Q‑3 (ب)).

**الهدف** (`server-gpu-plan.md:31-35`): خادم لينكس جديد يصير منصّةً بثلاثة أوامر:
1. تنزيل الحزمة وفكّها.
2. `sudo ./install.sh` مع النطاق ومعرّف مشروع Firebase (Q‑5 (أ)، Q‑8 (أ)).
3. انتظار «جاهز» ومعه نتيجة `/health`.

**المستفيدون:**
- مشغّل الخادم: يثبّت دون المستودع ودون خطوةٍ يدويّة. والتحديث في الميزة التالية (5.3).
- المالك: جهازه لا يتغيّر (القاعدة 1).
- لاحقاً: 6.1–6.5 و7.1–7.4، وكلّها تعتمد على 5.2 (`server-gpu-plan.md:138-146`).

## 2. النطاق

**داخل النطاق:**
- **5.1 الحزمة:**
  - `deploy/release/bundle.sh <VERSION>` يصنع `aizzak-<VERSION>.tar.gz`، وفيه ملفّ `VERSION` (الوسم وبصمات الصور الثلاث).
  - **حارسٌ أدنى** في `tests/unit/` (توصية Q‑2): كلّ ربطٍ تحت `./deploy/` في الملفّين موجودٌ في قائمة الحزمة أو في قائمة استثناءٍ مسمّاة، ولا ملفّ ممنوعاً في الحزمة.
- **5.2 التثبيت:** `install.sh` بخطواته الثماني (`server-gpu-plan.md:338-350`)، وتكراره آمن (`:351`). ويشمل:
  - إصلاح أمر إصدار `VAULT_SECRET_ID` في الوثائق الثلاث (`:348`).
  - حفظ المفتاح في `.env`، دون أن يضبط `install.sh` صلاحيّة `.env` (Q‑4، قرار المالك). ويذكّر التقرير بـ`chmod 600 .env`.
  - شرط القبول 3 من `compose-auto-size`، قبل أن يستدعي `install.sh` المولِّدَ بصلاحيّة root: review #20، وSR‑6/ث‑11، وتحذير SR‑11 (`docs/delivery/compose-auto-size/acceptance.md:168-174`).
- **الوثائق:**
  - قسم التثبيت في `docs/deploy-server.md`، وقائمة الأمان فيه (القاعدة 8، `server-gpu-plan.md:105`)، ومعها ثغرتا Q‑4 (FR‑33).
  - تعديل الوثائق المُلزِمة التي تمنع `VAULT_SECRET_ID` في `.env`: `05 §3.3`، و`08 §3.1`، و`quickstart.md`، و`stack-commands.md`، وتعليق `docker-compose.yml:148-151` (FR‑25، Q‑4).
  - لوحة الخطّة §4 (الصفوف 5.1–5.3، و5.3 بحالة «ميزةٌ تالية»)، و`docs/server-gpu-summary.html`.
  - تصحيح نصّ الخطّة حيث خالف الشيفرة (§5.10).

**خارج النطاق:**
- **5.3 التحديث (`upgrade.sh`) والرجوع:** ميزةٌ تالية تبدأ بعد الدمج (Q‑3 (ب)، 2026‑10‑19). ونصّها باقٍ هنا لتنسخه الميزة التالية كما هو: FR‑27…FR‑32 في §3.3، وUS‑9 وUS‑10 في `stories.md` قسم «مؤجَّل إلى الميزة التالية (5.3)». فلا يُبنى `upgrade.sh` ولا يدخل الحزمة.
- **ضبط صلاحيّة `.env` على 0600:** بيد المالك بعد التثبيت (Q‑4). و`install.sh` يذكّر به فقط (FR‑22).
- **6.1:** شهادةٌ حقيقيّة وHSTS. تبقى الشهادة الذاتيّة من `nginx-certs` (`docker-compose.yml:2468-2477`).
- **6.2:** مسار `/workspace-files/` في nginx. `install.sh` يكتب قيم `MINIO_PUBLIC_*` فقط (`server-gpu-plan.md:342`، `:381`).
- **6.3:** جدار الحماية وSSH.
- **6.4:** النسخ خارج الخادم، ونسخ `init.json`. `install.sh` **يذكّر** بذلك فقط (`:350`).
- **6.5:** تجديد `secret_id` آليّاً. `install.sh` يصدر واحداً عند أوّل تثبيت، ويطبع تاريخ انتهائه (FR‑22).
- **7.1:** بقيّة اختبارات الحزمة (`:413-417`): مطابقة مفاتيح ملفّات الأحجام، وتغطية `.env.example` لكلّ متغيّرٍ بلا قيمةٍ افتراضيّة، وفحص `build:` و`:dev`. يبقى هنا الحارس الأدنى وحده (Q‑2).
- **7.2:** وظيفة CI للحزمة. لا تعديل في `.github/workflows/`.
- **7.3:** التجربة على خادمٍ حقيقيّ.
- **7.4:** الدليل الكامل. يُكتب هنا قسم التثبيت وحده.
- **تثبيت Docker أو تعريف NVIDIA أو أداة الحاويات.** `install.sh` يفحصها ولا يثبّتها (`:202`).
- شيفرة التطبيق `src/` (`:466`)، ونسخة RunPod (`deploy/runpod/`)، وتصليب الإنتاج (الموجة 8).
- بناء الصور: CI يبنيها عند وسم `v*` (`.github/workflows/release.yml`).
- أكثر من خادم، وجهاز WSL هدفاً للتثبيت.

**ما لا يُثبَت إلّا على خادمٍ حقيقيّ (مؤجّلٌ إلى 7.3 صراحةً):**
- قبول 5.2: «على خادمٍ جديد … ينتهي بـ`/health` 200 دون خطوةٍ يدويّة» (`:352`).
- قبول 5.3: «تحديثٌ بين وسمين … والرجوع مجرَّبٌ مرّةً» (`:363`). مؤجَّلٌ مع 5.3 إلى الميزة التالية (Q‑3 (ب)).
- وضع الكرت، والإقلاع بعد إعادة تشغيل الخادم (`:433`).
- وما يُثبت محلّيّاً قبلها مكتوبٌ في `stories.md` بوسوم [config] و[وحدة] و[محلّيّ].

## 3. المتطلبات الوظيفية

### 3.1 الحزمة (5.1)

| المعرّف | المتطلب | المصدر |
|---|---|---|
| FR‑1 | `deploy/release/bundle.sh <VERSION>` يصنع `aizzak-<VERSION>.tar.gz`. صيغة `<VERSION>` كما في `build-push.sh`: `YYYY.M.N`، بلا `v` وبلا بادئة صورة | `server-gpu-plan.md:326`، `deploy/release/build-push.sh:15-17` |
| FR‑2 | في الحزمة فقط:<br>• `docker-compose.yml` و`docker-compose.gpu.yml`.<br>• المسارات الخمسة والعشرون المربوطة تحت `./deploy/` في الملفّين، وهي السبعة والعشرون في §5.1 ناقص `deploy/load` و`deploy/nginx/certs`. ومنها `deploy/postgres/sizes/` بملفّاته الثلاثة المتتبَّعة.<br>• `deploy/sizes/`.<br>• `.env.example`.<br>• `install.sh` وحده. و`upgrade.sh` مع 5.3 في الميزة التالية (Q‑3 (ب)).<br>• `deploy/host-tuning.sh` و`deploy/resource-budget.sh` و`deploy/rolling-deploy.sh`.<br>• **و`deploy/auto_size.py` و`deploy/gpu-preflight.sh`**، وقائمة الخطّة تنساهما (§5.10 البند 3).<br>• ملفّ `VERSION` | `:327-330`، `:344`، `docs/deploy-server.md:5` |
| FR‑3 | لا يدخل الحزمة:<br>• `docker-compose.build.yml` و`docker-compose.test.yml` و`docker-compose.wsl-gpu.yml`.<br>• `deploy/load/` و`deploy/runpod/` و`deploy/postgres/testdb/` و`deploy/backup/results/`.<br>• `src/` و`tests/`.<br>• أيّ `.env*` غير `.env.example`، وأيّ `init.json`، وأيّ ملفٍّ تحت `deploy/nginx/certs/`.<br>• `deploy/postgres/sizes/auto.conf*` و`.auto-size-tmp-*` و`__pycache__` | `:331`، القاعدة 6 (`:103`)، `.gitignore:12-28,58-61,73-91`، سابقة ث‑12 (`docs/deploy-runpod.md:467`) |
| FR‑4 | **محتوى الحزمة من الشجرة المثبّتة عند الوسم `v<VERSION>`**، لا من مجلّد العمل. فلا يدخلها ملفٌّ متجاهَل في git مهما كان في مجلّد البانية. والوسم الغائب رفض | FR‑3؛ `build-push.sh` يرفض الشجرة المتّسخة (`:41,139`) |
| FR‑5 | **`VERSION`** فيه رقم الإصدار، والـcommit، وسطرٌ لكلّ صورةٍ من الثلاث بصيغة `anastawahia/aizzak:<prefix>-<VERSION>@sha256:<64 hex>` للبادئات `app` و`embedding` و`embedding-cuda`. بصمةٌ غائبة أو تالفة أو `digest-unknown` تمنع صنع الحزمة | `:330`، `:175`، `build-push.sh:238`، `release.yml:132,153-155` |
| FR‑6 | الحزمة تُفكّ في مجلّدٍ فارغ خارج المستودع، و`docker compose config -q` ينجح فيه لكلّ تركيبةٍ من: (مع الكرت، دونه) × (مع المراقبة، دونها) × (بلا حجم، `small`، `medium`، `large`، `auto`) | `:333`، `:422` |
| FR‑7 | **حارسٌ أدنى في `tests/unit/`:**<br>• كلّ مسارٍ يربطه أحد الملفّين تحت `./deploy/` إمّا في قائمة الحزمة، وإمّا في قائمة استثناءٍ مسمّاة لها سبب: `deploy/load` (profile `load`)، و`deploy/nginx/certs` (يُولَّد على الخادم).<br>• ولا ملفّ من FR‑3 في الحزمة | `:414`، `:418` (درس RunPod حتّى `staging-4`)، Q‑2 |

### 3.2 التثبيت (5.2)

| المعرّف | المتطلب | المصدر |
|---|---|---|
| FR‑8 | **يفحص ولا يثبّت:**<br>• Docker وCompose وأداة NVIDIA: عبر `deploy/gpu-preflight.sh`، ويتبع رموزه كما في جدول `deploy-server.md` (0 = وضع الكرت، 10 = وضع المعالج، 11/13/14 = توقّف، 12 = توقّف ما لم يُطلب وضع المعالج صراحةً).<br>• المساحة الحرّة في مجلّد Docker (NFR‑8).<br>• `python3` ≥ 3.10 ومعه PyYAML، لأنّ `auto_size.py` و`resource-budget.sh` يحتاجانهما على المضيف.<br>• الوصول إلى المستودع الخاصّ `anastawahia/aizzak`. والدخول بمفتاح Docker Hub قرائيٍّ منفصل لكلّ خادم، لا مفتاح الرفع الذي في CI، يُدخله المشغّل بـ`docker login` قبل التثبيت. و`install.sh` لا يأخذه ولا يطبعه ولا يحفظه (Q‑9) | `:338`، `:202`، `docs/deploy-server.md:62-71`، `deploy/auto_size.py:21,45`، `deploy/resource-budget.sh:123,130` |
| FR‑9 | يرفض العمل في نسخةٍ من المستودع، أي حيث لا `VERSION` بجانبه، ولا يكتب فيها شيئاً. فلا يغيّر `.env` المالك ولا أسطر `COMPOSE_*` فيه | القاعدة 1 (`:93`) |
| FR‑10 | **`install.sh` كلّه يعمل بصلاحيّة root** (`sudo ./install.sh`)، ودونها يتوقّف قبل أيّ كتابة ويقترح `sudo` (Q‑5 (أ)). ويضبط النواة بـ`deploy/host-tuning.sh`، وفشله بالرمز 3 يوقف التثبيت ومعه رسالة. **ويبقى الضبط بعد إعادة تشغيل الخادم:** `host-tuning.sh` يحفظه في ملفٍّ تحت `/etc/sysctl.d/` (Q‑5 (أ)) | `:339`، `deploy/host-tuning.sh:85-88,110-112`، Q‑5 |
| FR‑11 | **ينشئ `.env`** من `.env.example` إن لم يوجد، ولا يستبدل ملفّاً موجوداً. **ولا يضبط صلاحيّته** (Q‑4، قرار المالك 2026‑10‑19): تبقى صلاحيّة الإنشاء الافتراضيّة بحسب `umask`، ولا `chmod` عليه. ويُكتب فيه `VAULT_SECRET_ID` (FR‑19). والتقرير يذكّر بـ`chmod 600 .env` (FR‑22) | `:340`، Q‑4 (`status.md` «قرارات وتصعيدات»). وتوصية 0600 في ث‑10/SR‑11 (`docs/deploy-runpod.md:465`، `docs/delivery/compose-auto-size/security-review.md:51`) يتقدّم عليها قرار المالك |
| FR‑12 | **يولّد القيم الثلاث عشرة** التي تبدأ بـ`change-me` في `.env.example`، ولا يطبعها:<br>• **الإحدى عشرة بلا قيمةٍ افتراضيّة في Compose:** `POSTGRES_SUPERUSER_PASSWORD`، `AIZZAK_OWNER_PASSWORD`، `APP_RW_PASSWORD`، `OUTBOX_RELAY_PASSWORD`، `RETENTION_SWEEPER_PASSWORD`، `METRICS_READER_PASSWORD`، `TRANSIT_ROTATOR_PASSWORD`، `WORKSPACE_PURGER_PASSWORD`، `BACKUP_OPERATOR_PASSWORD`، `METRICS_EXPORTER_PASSWORD`، `MINIO_ROOT_PASSWORD`.<br>• **ومعها:** `MINIO_ROOT_USER`، وليس له قيمةٌ افتراضيّة أيضاً. و`GRAFANA_ADMIN_PASSWORD`، وقيمته الافتراضيّة `admin`.<br>• **الأبجديّة** آمنةٌ في الروابط، لأنّ كلمات السرّ تدخل روابط الاتّصال حرفيّاً. والطول ≥ 32، لأنّ `15-metrics-exporter.sh` يرفض الأقصر (A‑7).<br>• **لا تبقى بعد التثبيت أيّ قيمةٍ تبدأ بـ`change-me`** | `:341`، القاعدة 6، §5.2، `docker-compose.yml:399,1581,1826,2710`، `deploy/postgres/initdb/15-metrics-exporter.sh:34-38` |
| FR‑13 | **النطاق** يُعطى بخيار (`--domain`)، ويُكتب منه: `MINIO_PUBLIC_ENDPOINT=<النطاق>`، و`MINIO_PUBLIC_SECURE=true`، و`OAUTH_REDIRECT_BASE_URL=https://<النطاق>`. نطاقٌ بمخطّطٍ أو مسارٍ أو منفذ، أو فارغ، يُرفض. والنطاق بحروفٍ عربيّة لا يُقبل إلّا بصيغة `xn--` (A‑12) | `:342` |
| FR‑14 | **`FIREBASE_PROJECT_ID` إلزاميّ** (Q‑8 (أ)): يُعطى بخيار `--firebase-project <id>`، ودونه يتوقّف التثبيت قبل كتابة `.env`. التطبيق لا يقلع بقيمةٍ فارغة، والقالب فارغ. ومثال الدليل `aizzak-agent` | `src/app/framework/di/composition_root.py:64-68`، `src/app/infrastructure/auth/firebase_auth.py:239`، `.env.example:437`، `docs/quickstart.md:101` |
| FR‑15 | **يكتب الاختيار:**<br>• `COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml` في وضع الكرت، و`docker-compose.yml` في وضع المعالج.<br>• `COMPOSE_PROFILES=local-llm,monitoring` افتراضيّاً، و`local-llm` بخيار «دون المراقبة».<br>• لا `wsl-ollama` ولا ملفّ `build` أو `test` أبداً.<br>• المستودعات الثلاثة والوسوم الثلاثة من `VERSION`، ولا `dev` ولا `latest` (القاعدة 5).<br>• `VAULT_TOKEN` يبقى فارغاً: فراغه هو ما يختار AppRole | `:343`، قرار 7 (`:457`)، `docker-compose.yml:59-60,139-147`، `docker-compose.gpu.yml:57`، `.env.example:25-49,355` |
| FR‑16 | **الحجم:**<br>• `auto` افتراضيّاً: يستدعي `deploy/auto_size.py --role server`.<br>• `--size small\|medium\|large` يكتب متغيّرات الحجم في كتلةٍ معلَّمة في `.env`.<br>• **رموز المولِّد:** 0 يكمل. 1 يتوقّف: الخادم أصغر من `small` أو رفضته الميزانيّة. 2 خطأٌ داخليّ. 3 خطأ إعداد، و`.env` لم يُلمس، وليس خطأ حجم | `:344`، `:285`، `deploy/auto_size.py:17-19` |
| FR‑17 | **يفحص الميزانيّة قبل السحب:**<br>• `resource-budget.sh --size <الحجم>` لحجمٍ مسمّى، و`--block .env` لـ`auto`. ومعه `--without-monitoring` إن أُطفئت المراقبة.<br>• `COMPOSE_FILE` و`GPU_COMPOSE_FILE` مثبّتان عند الاستدعاء، فلا يغيّرهما ما في الصدفة.<br>• الرفض يوقف التثبيت قبل أيّ سحب | `:345`، `:293`، `docs/delivery/compose-auto-size/retro.md:73`، `deploy/resource-budget.sh:55-56` |
| FR‑18 | **يسحب ويتحقّق:** `docker compose pull`، ثمّ تُقارن بصمات الصور الثلاث المسحوبة بما في `VERSION`. اختلافٌ واحد يوقف التثبيت قبل أيّ `up` | `:346`، القاعدة 5 |
| FR‑19 | **أوّل إقلاع بالترتيب** (§5.3):<br>1. `vault` سليم.<br>2. `vault-bootstrap` انتهى بنجاح.<br>3. **يصدر `VAULT_SECRET_ID`** بالصيغة العاملة: العنوان والتوكن الجذر يُضبطان داخل حاوية `vault` نفسها، فلا يخرج التوكن منها. والناتج يُلتقط عبر `exec` لا خدمة، فلا يدخل سجلّ `json-file`. ويُكتب في `.env` سطراً `VAULT_SECRET_ID=` (Q‑4)، ولا تتغيّر صلاحيّة الملفّ (FR‑11).<br>4. `minio-bootstrap`.<br>5. `migrate`.<br>6. **كلمة سرّ `metrics_exporter`** (الخطوة ① من `08 §3.3‑ج`)، وتنساها الخطّة (§5.10 البند 4).<br>7. `ollama-pull`.<br>8. `docker compose up -d --wait` بمهلةٍ محدودة | `:347-348`، `docs/design/08-local-runbook.md:810-812,1025`، `docs/quickstart.md:125`، `deploy/postgres/initdb/15-metrics-exporter.sh:13-18` |
| FR‑20 | **لا تُنشأ ولا تبدأ خدمةٌ تحمل `VAULT_SECRET_ID` وقيمته فارغة أو تالفة.** والخدمات هي: `app`، والعمّال الثلاثة، و`outbox-relay`، و`wal-shipper`، و`ops-scheduler`، و`migrate`، و`backup` (§5.4). فحلقة دخولٍ فاشلة تقفل الدور في Vault | `docs/design/08-local-runbook.md:829-843` |
| FR‑21 | **يفحص:**<br>• `/health` و`/health/ready` برمز 200 عبر `http://127.0.0.1`. المنفذ 80 يخدم التطبيق بلا تحويل.<br>• ثمّ `docker compose exec -T app python /app/deploy/smoke/stack_smoke.py` ينتهي بـ0. والسكربت داخل الصورة، فلا يحتاج الحزمة | `:349`، `deploy/nginx/nginx.conf:368-373`، `Dockerfile:143`، `deploy/smoke/stack_smoke.py:1-32` |
| FR‑22 | **يطبع التقرير:**<br>• الرابط، ونتيجة `/health`، والحجم (المسمّى، أو `auto` بمعالجات الخادم وذاكرته)، وهل الكرت مستعمل، وهل المراقبة مفعّلة.<br>• إن أُطفئت المراقبة: «لا تنبيهات»، ونصيحة فحص `/health` من الخارج.<br>• تذكيرٌ بحفظ مادّة Vault خارج الخادم (6.4)، دون أيّ محتوى.<br>• **سطرٌ واحد يذكّر بـ`chmod 600 .env`**، لأنّ `install.sh` لا يضبطها و`.env` فيه كلمات السرّ والمفتاح (Q‑4).<br>• (Should) تاريخ انتهاء `secret_id`، بلا السرّ نفسه | `:350`، `:314`، `docs/deploy-server.md:80-84`، Q‑4 |
| FR‑23 | **التكرار آمن.** تشغيلٌ ثانٍ بالمدخلات نفسها:<br>• لا يغيّر كلمة سرّ، ولا يعيد تهيئة Vault، ولا يصدر `secret_id` جديداً إن وُجد واحد محفوظ.<br>• `.env` يبقى بايتاً ببايت.<br>• لا يعيد إنشاء أيّ حاوية.<br>وتشغيلٌ ثانٍ بمدخلاتٍ مختلفة (نطاق، حجم مسمّى، وضع) يتوقّف ويسمّي المفتاح، ولا يغيّر شيئاً (A‑13) | `:351`، `:352`، §5.4 (قياس الإصدار الجديد) |
| FR‑24 | **رموز خروج `install.sh` موثّقة** في `docs/deploy-server.md`، واختبارٌ يقارنها برأس السكربت، كما يفعل `gpu-preflight.sh` | `docs/deploy-server.md:58-71` |
| FR‑25 | **تصحيح أمر إصدار `secret_id`** في `08 §3.1` و`quickstart.md` و`stack-commands.md`، بالصيغة العاملة.<br>**وتعديل الوثائق المُلزِمة التي تمنع المفتاح في `.env`** (Q‑4): تقول كلّها إنّه يُحفظ في `.env`، وإنّ `install.sh` يكتبه هناك على الخادم. والمواضع:<br>• `05 §3` و`§3.3` (`05-rbac-config-secrets.md:128,148`).<br>• `08 §3.1` (`08-local-runbook.md:800`).<br>• `quickstart.md:204,353`، و`stack-commands.md:259,277,285,635`.<br>• وتعليق `docker-compose.yml:148-151`: تعليقٌ وحده، لا يغيّر البصمة (NFR‑1).<br>وأمثلة التمرير من الصدفة تبقى، لأنّها ما زالت تعمل: الصدفة تفوز على `.env` (`docker-compose.yml:149-150`، A‑15) | `:348`، Q‑4، `docs/design/08-local-runbook.md:800,812`، `docs/design/05-rbac-config-secrets.md:128,148`، `docs/quickstart.md:204,210,353`، `docs/stack-commands.md:55,259,274,277,285,635` |
| FR‑26 | **قبل أن يستدعي `install.sh` المولِّدَ بصلاحيّة root** (شرط القبول 3 لـ`compose-auto-size`):<br>• review #20: رسالة الفشل تسمّي الملفّ الحقيقيّ.<br>• SR‑6/ث‑11: حين `euid == 0`، يُرفض `.env` أو مجلّده أو `deploy/postgres/sizes` إن كان رابطاً رمزيّاً أو غير مملوكٍ لـroot. و`resource-budget.sh` يشغّل `python3 -I`.<br>• SR‑11: تحذيرٌ بالاسم وحده حين يقرأ `.env` غيرُ مالكه | `docs/delivery/compose-auto-size/acceptance.md:168-174`، `security-review.md:46,95` |

### 3.3 التحديث والرجوع (5.3): مؤجَّلٌ إلى الميزة التالية

> **خارج النطاق بقرار المالك** في Q‑3 (ب) (2026‑10‑19). تبقى المعرّفات FR‑27…FR‑32 ونصّها كما هي لتنسخها الميزة التالية، ولا تُبنى ولا تُختبر هنا. وعند النسخ: Q‑4 حُسم (المفتاح في `.env`)، وQ‑6 يُعرض على المالك من جديد.

| المعرّف | المتطلب | المصدر |
|---|---|---|
| FR‑27 | **`upgrade.sh` يعمل من الحزمة الجديدة بعد فكّها، على مجلّد التثبيت القائم.** فالوسم وحده لا يكفي: ملفّات Compose والإعداد تتغيّر بين الإصدارات (§5.10 البند 9). ويرفض إصداراً أقدم أو مساوياً ما لم يُطلب الرجوع، ويرفض حزمةً بلا بصمات | `:356`، FR‑5 |
| FR‑28 | **نسخةٌ احتياطيّة كاملة أوّلاً:** `docker compose --profile backup run --rm backup python -m app.ops.backup full`. فشلها يوقف التحديث، ومجلّد التثبيت لم يتغيّر | `:357`، `src/app/ops/backup.py:7-8` |
| FR‑29 | **يبدّل ملفّات الحزمة:**<br>• ولا يلمس `.env` القائم ولا `auto.conf` ولا الشهادات.<br>• يضيف المفاتيح الجديدة من `.env.example` الجديد، ويولّد ما كان منها `change-me`، ولا يغيّر قيمةً موجودة.<br>• يكتب الوسوم الجديدة، ثمّ يسحب، ثمّ يتحقّق من البصمات (FR‑18).<br>• ويُبقي نسخةً من ملفّات الإصدار السابق للرجوع | `:358` |
| FR‑30 | **الترتيب:**<br>1. `migrate`، والتوسيع أوّلاً (08 §(٣)).<br>2. إعادة تشغيل كلّ خدمةٍ تغيّر ملفٌّ تربطه، أو إعادة تشغيلها لمرّةٍ واحدة إن كانت كذلك (مثل `vault-bootstrap` إن تغيّر `app-policy.hcl`)، لأنّ بصمة Compose لا ترى محتوى الملفّات المربوطة (§5.8، مقيس).<br>3. `rolling-deploy.sh` لـ`app` (بالفائض)، وللعمّال و`outbox-relay` و`ops-scheduler` و`wal-shipper` (في المكان).<br>4. `up -d` للباقي.<br>وكلّ خدمةٍ تُعاد تحمل `VAULT_SECRET_ID` (FR‑20) | `:359-360`، `docs/design/08-local-runbook.md:423-431`، `deploy/rolling-deploy.sh:48-59,206` |
| FR‑31 | **الفحوص نفسها** في FR‑21، ويطبع مدّة التوقّف: الثواني التي لم يُرجع فيها `/health` 200، مقيسةً بسبرٍ كلّ ثانية | `:361` |
| FR‑32 | **الرجوع إلى الإصدار السابق:**<br>• يرجع بملفّاته ووسومه.<br>• لا يسترجع قاعدة بيانات ولا يحذف حجماً دون تأكيدٍ بشريٍّ صريح (القاعدة 9).<br>• إن كانت في الإصدار الجديد هجرةٌ لا يقرؤها `migrate` القديم، يتوقّف الرجوع الآليّ ويطبع الطريق اليدويّ (Q‑6) | `:362`، القاعدة 9 (`:106`)، §5.10 البند 10 |

### 3.4 الوثائق واللوحة

| المعرّف | المتطلب | المصدر |
|---|---|---|
| FR‑33 | **`docs/deploy-server.md`، قسم التثبيت:** المدخلات، والخيارات، والخطوات بترتيبها، ورموز الخروج، وما لا يفعله (6.x). ومعها `sudo` (Q‑5 (أ))، و`docker login` بمفتاحٍ قرائيٍّ منفصل لكلّ خادم (Q‑9)، ومثال `--firebase-project aizzak-agent` (Q‑8).<br>**وتُضاف إلى قائمة الأمان فيه** كلّ ثغرةٍ تظهر، برقمٍ جديد بعد ث‑9. **ومنها ثغرتا Q‑4:**<br>(أ) `VAULT_SECRET_ID` محفوظٌ في `.env` على الخادم.<br>(ب) `.env`، وفيه كلمات السرّ الثلاث عشرة والمفتاح، يبقى دون 0600 بقرار المالك (2026‑10‑19) حتّى يضبطه بيده | القاعدة 8 (`:105`)، `docs/deploy-server.md:90-104`، Q‑4، قرار staging (2026‑10‑06) |
| FR‑34 | **لوحة الخطّة** §4 (الصفوف 5.1–5.3، و5.3 بحالة «ميزةٌ تالية»، Q‑3 (ب)) و`docs/server-gpu-summary.html` تُحدَّثان. ونصّ الخطّة يُصحَّح حيث خالف الشيفرة (§5.10 البنود 1–4 و6–8) | `:23`، `CLAUDE.md` (Docs conventions) |
| FR‑35 | **السكربتات الجديدة في حارس النظافة:** لا `source .env`، ولا سرّ حرفيّ. ويقرأ كلٌّ منها `.env` قراءةً، لأنّ قيم JSON تفسد بالتعيين في الصدفة | `tests/unit/test_gpu_compose_hygiene.py:213-217`، `deploy/backup/restore_drill.sh` (تعليق قراءة `.env`) |

## 4. المتطلبات غير الوظيفية

| المعرّف | المتطلب | معيار القياس |
|---|---|---|
| NFR‑1 | **المكدّس المحلّيّ لا يتغيّر** (القاعدة 1) | • `git diff master -- docker-compose.yml docker-compose.gpu.yml deploy/sizes deploy/postgres/sizes` فارغ.<br>• أو تعليقاتٌ فقط، ومعها بصمة `config --hash` لكلّ خدمةٍ متساويةٌ بين `master` والفرع، بـ`.env.example`. والمتوقَّع تعليق `docker-compose.yml:148-151` وحده (Q‑4).<br>• و`install.sh` يرفض العمل في نسخة المستودع (FR‑9). و`upgrade.sh` مثله في الميزة التالية |
| NFR‑2 | **لا سرّ في مخرج أو سجلّ أو وسيط** (القاعدة 6) | في تشغيلٍ كامل بـ`docker` مزيّف:<br>• لا تظهر في stdout ولا stderr أيّ من القيم الثلاث عشرة المولَّدة، ولا توكن جذرٍ مزروع، ولا `secret_id`.<br>• ولا تظهر في وسائط أيّ عمليّةٍ فرعيّة. يُمرَّر السرّ بـ`-e NAME` من البيئة، لا بـ`NAME=value` في سطر الأمر |
| NFR‑3 | **صلاحيّة `.env`** (Q‑4، قرار المالك 2026‑10‑19) | • **`install.sh` لا يضبط صلاحيّة `.env`:** بعد أوّل تثبيت تبقى صلاحيّة الإنشاء الافتراضيّة بحسب `umask`، أي **0644 بـ`umask 022`**، ولا `chmod` عليه.<br>• **ولا يغيّر صلاحيّةً ضبطها المالك بيده** (0600 مثلاً): لا التشغيل الثاني، ولا إعادة كتابة كتلة الحجم. والمولِّد يحفظ الصلاحيّة اليوم (`deploy/auto_size.py:654-666,852`).<br>• **لا ملفّ أسرارٍ غير `.env`**، فالمفتاح فيه (FR‑19).<br>• **التقرير** فيه سطرٌ واحد يذكّر بـ`chmod 600 .env` (FR‑22).<br>• **والأمران ثغرتان** في قائمة أمان `docs/deploy-server.md` (FR‑33) |
| NFR‑4 | **حجم الحزمة** | مقيس: قائمةٌ مرشّحة بحسب FR‑2 من `git archive HEAD`: **45 ملفّاً، و804 ك.ب، و209 ك.ب مضغوطة** (§5.8). الحدّ: ≤ 1 م.ب مضغوطة |
| NFR‑5 | **الحزمة قابلةٌ للإعادة** (Should) | صنعها مرّتين من الوسم نفسه وملفّ البصمات نفسه يعطي sha256 واحداً |
| NFR‑6 | **صورٌ ثابتة** (القاعدة 5) | • `docker compose config --images` بعد التثبيت بلا `:dev` ولا `:latest`. مقيس: صفرٌ في التركيبات الست عشرة حين تُضبط الوسوم الثلاثة (§5.8).<br>• والبصمات المسحوبة تساوي `VERSION` (FR‑18) |
| NFR‑7 | **لا انتظار بلا نهاية** | لكلّ انتظارٍ في `install.sh` مهلة، ورسالةٌ عند انقضائها تسمّي الخدمة. والمهلة الكلّيّة مكتوبةٌ في رأس السكربت (تقدير: ≤ 30 دقيقة للإقلاع الأوّل دون السحب)، ويُقاس الزمن الحقيقيّ في 7.3 (`:428`) |
| NFR‑8 | **المساحة الحرّة** | يرفض التثبيت إن قلّت المساحة الحرّة في `docker info --format '{{.DockerRootDir}}'` عن حدٍّ مكتوب. **أساسه مقيس:** صور وضع الكرت تأخذ نحو **27 ج.ب** على هذا الجهاز (عمود SIZE في `docker images`: `embedding-cuda` ‏11.1، و`ollama` ‏9.28، و`app` ‏1.4، والباقي نحو 5)، ووضع المعالج نحو 19.5. **الحدّ «تقدير»:** 40 ج.ب لوضع الكرت و30 للمعالج، ويُقاس في 7.3 |
| NFR‑9 | **المضيف** | يعمل على bash ≥ 4 وPython ≥ 3.10 مع PyYAML، كما يقول `auto_size.py:21` («a server may be Ubuntu 22.04»). وغياب أيٍّ منها رسالةٌ تسمّيه ورمزٌ في الجدول (FR‑24) |
| NFR‑10 | **البوّابات** | البوّابات الخمس خضراء، ومعها `deploy/prometheus/test-rules.sh` (لا تنبيه جديد). والاختبارات الجديدة تزيد زمن `pytest tests/unit` ≤ 15 ث، بـ`--durations` قبل وبعد (سابقة `monitoring-profile` NFR‑7) |
| NFR‑11 | **الاختبار بلا مكدّس** | لا معيار Must يحتاج المكدّس الحيّ:<br>• معايير [config] تمحو `COMPOSE_FILE` و`COMPOSE_PROFILES` من بيئتها، وتعمل في نسخةٍ مؤقّتة، وتتخطّى إن غاب Docker. فوظيفة `integration` تصدّر `COMPOSE_FILE` بثلاثة ملفّات (`.github/workflows/ci.yml:95`).<br>• ومعايير [وحدة] تستعمل `docker` و`nvidia-smi` مزيّفين على `PATH`، كما يفعل `test_rolling_deploy.py:297-424` |

## 5. الوضع الحالي في الشيفرة

### 5.1 المسارات المربوطة تحت `./deploy/`: سبعةٌ وعشرون لا أربعةٌ وعشرون

**طريقة القياس:**
- نُسخ `docker-compose.yml` و`docker-compose.gpu.yml` و`.env.example` وحدها إلى مجلّد scratch، فلا يُقرأ `.env` الحقيقيّ.
- ثمّ `docker compose --env-file .env.example -f docker-compose.yml -f docker-compose.gpu.yml --profile '*' config --format json`، وعُدّت مصادر `type: bind`.
- **النتيجة:** 32 مصدراً. خمسةٌ منها من المضيف لا من المستودع (`/` و`/sys` و`/var/lib/docker/` و`/var/run` لـ`cadvisor`، و`/var/lib/docker/containers` لـ`alloy`). والباقي **27 مساراً تحت `./deploy/`**:

| المجموعة | المسار | الخدمة | في الحزمة؟ |
|---|---|---|---|
| Postgres | `deploy/postgres/initdb/` (مجلّد) | `postgres` (`docker-compose.yml:721`) | نعم |
| | `deploy/postgres/pg_hba.conf` | `postgres` (`:724`) | نعم |
| | `deploy/postgres/postgresql.conf` | `postgres` (`:729`) | نعم |
| | `deploy/postgres/sizes/` (مجلّد، أُضيف في 3.2) | `postgres` (`:731`) | نعم، بلا `auto.conf` |
| | `deploy/postgres/archive_wal.sh` | `postgres` (`:732`) | نعم |
| التضمين | `deploy/nginx/embedding-lb.conf` | `embedding-lb` (`:1389`) | نعم |
| Vault | `deploy/vault/server.hcl` و`start.sh` | `vault` (`:1449-1450`) | نعم |
| | `deploy/vault/bootstrap.sh` و`app-policy.hcl` | `vault-bootstrap` (`:1516-1517`) | نعم |
| MinIO | `deploy/minio/bootstrap.sh` | `minio-bootstrap` (`:1551`) | نعم |
| Ollama | `deploy/ollama/pull_models.py` (أُضيف في 2.2) | `ollama-pull` (`:1749`) | نعم |
| الحافّة | `deploy/nginx/gen-cert.sh` | `nginx-certs` (`:2476`) | نعم |
| | `deploy/nginx/certs/` (قراءةٌ وكتابة) | `nginx-certs` (`:2477`) و`nginx` (`:2497`) | **لا**: يُولَّد على الخادم (FR‑3) |
| | `deploy/nginx/nginx.conf` و`app-locations.conf` | `nginx` (`:2495-2496`) | نعم |
| المراقبة | `deploy/prometheus/prometheus.yml` و`alerts.yml` | `prometheus` (`:2653-2654`) | نعم |
| | `deploy/prometheus/scrape-gpu.yml` (أُضيف في 4.2) | `prometheus` (`docker-compose.gpu.yml:102`) | نعم |
| | `deploy/grafana/provisioning/` و`dashboards/` (مجلّدان) | `grafana` (`:2720-2721`) | نعم |
| | `deploy/alertmanager/alertmanager.yml` و`sink.py` | `alertmanager` و`alert-sink` (`:2760`، `:2796`) | نعم |
| | `deploy/postgres-exporter/queries.yaml` | `postgres-exporter` (`:3015`) | نعم |
| | `deploy/loki/loki.yml` | `loki` (`:3100`) | نعم |
| | `deploy/alloy/config.alloy` | `alloy` (`:3138`) | نعم |
| الحمل | `deploy/load/` (قراءةٌ وكتابة، فيه `accounts.json` و`tokens.json`) | `k6`، profile `load` (`:3266`) | **لا** (`server-gpu-plan.md:331`) |

- **لماذا قالت الخطّة 24؟** عند `874bea9` (2026‑10‑13، يوم كُتبت الخطّة) كان في `docker-compose.yml` ‏25 سطر ربطٍ تحت `./deploy/`، و`nginx/certs` مربوطٌ مرّتين، فهي 24 مساراً. ثمّ أُضيفت ثلاثة: `deploy/postgres/sizes/` (3.2)، و`deploy/ollama/pull_models.py` (2.2)، و`deploy/prometheus/scrape-gpu.yml` (4.2).
- **في الحزمة إذن 25 مساراً.**
- **ولا ربط لملفٍّ خارج `./deploy/`.** لا `env_file` ولا `configs` ولا `secrets`، فُحص بـ`grep` على الملفّين.

### 5.2 المتغيّرات بلا قيمةٍ افتراضيّة، و`.env.example`

- **في `docker-compose.yml` اثنا عشر متغيّراً بصيغة `${VAR:?…}` أو `${VAR}` بلا قيمةٍ افتراضيّة.** وفي `docker-compose.gpu.yml` لا شيء. مقيسٌ بـ`grep` على الصيغتين:
  - **الإحدى عشرة كلمة سرّ** التي تذكرها الخطّة:

    | المتغيّر | أسطر الاستعمال |
    |---|---|
    | `POSTGRES_SUPERUSER_PASSWORD` | `:667`، `:941`، `:2851` |
    | `AIZZAK_OWNER_PASSWORD` | `:673`، `:1498`، `:1581` |
    | `APP_RW_PASSWORD` | `:399`، `:674`، `:1499`، `:1826` |
    | `OUTBOX_RELAY_PASSWORD` | `:675`، `:2244` |
    | `RETENTION_SWEEPER_PASSWORD` | `:683`، `:2429` |
    | `METRICS_READER_PASSWORD` | `:690`، `:1846` |
    | `TRANSIT_ROTATOR_PASSWORD` | `:699`، `:2431` |
    | `WORKSPACE_PURGER_PASSWORD` | `:709`، `:2430` |
    | `BACKUP_OPERATOR_PASSWORD` | `:718`، `:2357`، `:2432` |
    | `METRICS_EXPORTER_PASSWORD` | `:3013` |
    | `MINIO_ROOT_PASSWORD` | `:1114`، `:1501`، `:1537` |

  - **والثاني عشر `MINIO_ROOT_USER`** (`:1113`): اسم مستخدمٍ لا كلمة سرّ، لكنّه بلا قيمةٍ افتراضيّة أيضاً.
- **في `.env.example` ثلاث عشرة قيمةً تبدأ بـ`change-me`:**
  - الاثنا عشر أعلاه (`.env.example:89-96,127,134,223-224`).
  - ومعها `GRAFANA_ADMIN_PASSWORD=change-me-grafana` (`:370`). وقيمته الافتراضيّة في Compose `admin` (`docker-compose.yml:2710`)، وGrafana منشورٌ على `127.0.0.1` وحده (`:2724`).
- **قيمٌ أخرى يحتاجها الخادم:**

  | المفتاح | قيمته في القالب | ما يلزم على الخادم |
  |---|---|---|
  | `FIREBASE_PROJECT_ID` | فارغ (`:437`) | إلزاميّ، وإلّا لا يقلع التطبيق (FR‑14) |
  | `OAUTH_REDIRECT_BASE_URL` | فارغ (`:679`) | `https://<النطاق>` |
  | `MINIO_PUBLIC_ENDPOINT` و`MINIO_PUBLIC_SECURE` | `localhost:19000` و`false` (`:221-222`) | النطاق و`true` |
  | `VAULT_TOKEN` | فارغ (`:355`) | يبقى فارغاً |
  | `VAULT_ROLE_ID` | `aizzak-local-app` (`:361`) | A‑5 |
  | `MINIO_TEST_ACCOUNT_ENABLED` | `false` (`:277`) | يبقى `false` (A‑6) |

- **أسطر `COMPOSE_FILE` و`COMPOSE_PROFILES` ووسوم الصور معلَّقةٌ عمداً** (`.env.example:25-49`)، ويحرس ذلك `test_env_example_activates_no_compose_selector` (`tests/unit/test_gpu_compose_hygiene.py:442`). فـ`install.sh` يضيفها، ولا يغيّر القالب.
- **`APP_ENV` اسمٌ في السجلّات فقط.** يُقرأ في `env_settings.py:53`، ولا يُستعمل إلّا في حقول `extra` للسجلّات (`grep` على `src/`). فلا يغيّر سلوكاً (A‑4).
- **كلمات السرّ تُكتب في Postgres مرّةً واحدة**، عند تهيئة الحجم الأوّل (`deploy/postgres/initdb/10-roles.sh:125-169`). و`vault-bootstrap` يعيد بذرها في Vault من `.env` عند **كلّ** تشغيل (`deploy/vault/bootstrap.sh:92-97`). فتغيير كلمةٍ في `.env` بعد التثبيت يفرّق بين Postgres وVault. وهذا سبب FR‑23.
- **دور `metrics_exporter` يولد بلا كلمة سرّ** على حجمٍ جديد. يُضبط بأمرٍ يدويّ واحد بعد أوّل `up`: `docker compose exec -T -e METRICS_EXPORTER_PASSWORD postgres bash /docker-entrypoint-initdb.d/15-metrics-exporter.sh` (`15-metrics-exporter.sh:13-18`، `docs/quickstart.md:125`، `08-local-runbook.md:1025`). ودونه يشتعل `AizzakPostgresDown`.

### 5.3 الخدمات لمرّةٍ واحدة وترتيب الإقلاع

مقيسٌ من `config --format json` بكلّ الـprofiles (§5.1):

| الخدمة لمرّة | تعتمد على | ينتظرها |
|---|---|---|
| `wal-archive-init` (`:436`) | — | `postgres`، و`wal-shipper`، و`ops-scheduler` |
| `vault-bootstrap` (`:1479`) | `vault` سليم | `app`، والعمّال الثلاثة، و`ops-scheduler` |
| `minio-bootstrap` (`:1525`) | `minio` سليم | `wal-shipper`، و`ops-scheduler` |
| `migrate` (`:1569`) | `postgres` سليم | `app`، والعمّال، و`outbox-relay`، و`ops-scheduler` |
| `nginx-certs` (`:2468`) | — | `nginx` |
| `ollama-pull` (`:1726`، profile `local-llm`) | `ollama` سليم (اختياريّ) | **لا شيء**: «Nothing waits for it» (`:1723-1725`) |
| `backup` (`:2341`، profile `backup`) | `postgres`، `minio` | أمرٌ يشغّله المشغّل، لا خدمة |

- **Compose يرتّب كلّ ما لمرّةٍ واحدة بنفسه.** فـ`docker compose up -d` وحده يقلع البيانات، ثمّ الخدمات لمرّة، ثمّ التطبيق والحافّة (`docs/quickstart.md:127`).
- **الخطوة الوحيدة خارج Compose هي إصدار `secret_id`.** يجب أن تأتي بعد `vault-bootstrap`، لأنّه ينشئ الدور (`bootstrap.sh:110-149`). ويجب أن تأتي قبل إنشاء أيّ خدمةٍ تحمل المفتاح، لأنّ الحاوية تأخذ بيئتها عند إنشائها.
- **فترتيب الخطّة صحيحٌ في ما يهمّ، وناقصٌ في ثلاثة:**
  - لا يذكر `wal-archive-init` و`nginx-certs`، ويرتّبهما Compose وحده.
  - ينسى خطوة `metrics_exporter` (§5.2).
  - و`ollama-pull` لا ينتظره شيء. فإن لم ينتظره `install.sh`، ردّت أوّل محادثةٍ 502 حتّى يكتمل السحب.
- **سلوك `up -d --wait` مع خدماتٍ لمرّةٍ انتهت بـ0 يحتاج تأكيداً** على أدنى Compose يقبله `gpu-preflight.sh` (2.20.0، `deploy/gpu-preflight.sh:54`). لم يُقَس هنا (A‑11).

### 5.4 Vault و`VAULT_SECRET_ID`

- **الأمر الموثَّق يفشل.** المكتوب هو `docker compose exec vault vault write -f -field=secret_id auth/approle/role/app/secret-id`، في ثلاثة مواضع: `08-local-runbook.md:812`، و`quickstart.md:210`، و`stack-commands.md:55,274`.
  - **السبب:** حاوية `vault` بلا `VAULT_ADDR` ولا توكن. بيئتها فيها `VAULT_LISTEN_ADDR` وحده (`docker-compose.yml:1443-1447`).
  - **والتوكن الجذر** في `/vault/init/init.json` داخل حجم `vault-init`، يكتبه `start.sh` عند أوّل تهيئة (`deploy/vault/start.sh:126-141`).
  - **الصيغة العاملة** (جُرّبت 2026‑09‑29، كما في طلب المنسّق): تضبط `VAULT_ADDR=http://127.0.0.1:8200` و`VAULT_TOKEN` من `init.json` **داخل** `sh -c` في الحاوية، ثمّ تشغّل `vault write -f -field=secret_id …`.
- **`vault-bootstrap` لا يصدر `secret_id` عمداً** (`bootstrap.sh:167-170`). ومدّته 720 ساعة (`:144-149`)، ولا شيء يجدّده (6.5).
- **`start.sh` يرفض إعادة التهيئة وحده.** إن وُجدت بياناتٌ بلا `init.json` رفض `operator init` (`start.sh:126-141`). فتكرار التثبيت لا يعيد تهيئة Vault ما دام الحجمان قائمين.
- **الخدمات التي تحمل `VAULT_SECRET_ID`:**
  - **من يحمله:** كلّ خدمةٍ تدمج `*app-env` (`docker-compose.yml:146-151`). مقيسٌ من `config --format json`: `app`، و`worker-memory`، و`worker-knowledge`، و`worker-media`، و`outbox-relay`، و`wal-shipper`، و`ops-scheduler`، و`migrate`، و`backup` (profile).
  - **فهي تسع.** ثمانٍ تحت الإقلاع العاديّ، وتاسعتها `backup`.
  - **ولا تحمله:** `vault-bootstrap` ولا `ollama-pull` ولا أيّ خدمةٍ بصورة طرفٍ ثالث.
- **مكان حفظه: تعارضٌ مع الوثائق المُلزِمة** (Q‑4، حُسم في بوابة ①: في `.env`، وتُعدَّل الوثائق بـFR‑25):

  | الموضع | ما يقوله |
  |---|---|
  | `docker-compose.yml:148-151` | «NEVER put this in .env (05 §3.3) … read from the invoking shell» |
  | `08-local-runbook.md:800` | «يُحقن خارج الملفّ» |
  | `quickstart.md:204،353` و`stack-commands.md:259،277،285،635` | «لا يُكتب في `.env` أبداً؛ يُمرَّر من الصدفة» |
  | `05-rbac-config-secrets.md:128` | «لا سرّ في `.env` أو الصورة» |
  | `05-rbac-config-secrets.md:148` | «يُحقن كـsecret آمن (لا يُطبع، لا يُسجَّل)» |
  | **بالمقابل** خطوة 6.5 (`server-gpu-plan.md:406`) | تجديده «يكتبه في `.env`» |
  | **بالمقابل** `.env` المالك | فيه المفتاح منذ 2026‑09‑28. هذا من ملاحظة ذاكرةٍ للمالك، **غير مُتحقَّق**، لأنّ `.env` لا يُقرأ |

  - **وعلى خادمٍ جديد لا صدفة تبقى.** إعادة تشغيل الخادم تُبقي الحاويات ببيئتها (`restart: unless-stopped`). لكنّ أيّ إعادة إنشاء تحتاج المفتاح حاضراً عند كلّ `docker compose up`: ومنها `upgrade.sh`، و`rolling-deploy.sh` (`:308-324`)، وتعديل `.env`.
  - **ونسيانه مكلف:** يُقلع `app` بمفتاحٍ فارغ، فتدور حلقة دخولٍ فاشلة، فيقفل Vault الدور (`08-local-runbook.md:829-843`).
  - **قياس:** إضافة `VAULT_SECRET_ID=<قيمة>` إلى `.env` في مجلّد scratch غيّرت بصمة `config --hash` لثماني خدمات: `app`، و`migrate`، و`ops-scheduler`، و`outbox-relay`، و`wal-shipper`، والعمّال الثلاثة. فـ**إصدار مفتاحٍ جديد في كلّ تكرار يعيد إنشاءها كلّها**، وهذا يكسر FR‑23.

### 5.5 الأدوات الموجودة، وما يعيد `install.sh` و`upgrade.sh` استعماله

| الأداة | ما تقدّمه اليوم | ما يُعاد استعماله | حدودها |
|---|---|---|---|
| `deploy/gpu-preflight.sh` | فحوص Docker ≥ 26.0.0، وCompose ≥ 2.20.0، والتعريف، وأداة NVIDIA ≥ 1.17.8. ورموزه 0/2/10–14 موثّقةٌ مع «ما يفعله التثبيت» (`:31-45`، `docs/deploy-server.md:62-71`). لا يحتاج root ولا يقرأ `.env` | الخطوة 1 كما هي | لا يفحص المساحة ولا Python ولا السجلّ |
| `deploy/host-tuning.sh` | يضبط `vm.overcommit_memory=1` وحده (`:75`). فيه `--check`. ويخرج بـ3 إن لم يكن root (`:110-112`) | الخطوة 2 | **يكتب في `/proc/sys` فقط** (`:85-88`)، فيضيع الضبط بعد إعادة تشغيل الخادم (Q‑5) |
| `deploy/auto_size.py` | يكتب كتلة `aizzak auto-size` و`auto.conf`. ورموزه 0/1/2/3 (`:17-19`). يقبل قيم `.env.example` غير المعدَّلة، فلا يتعارض مع `.env` جديد (`:556-576`). ويثبّت `COMPOSE_FILE` حين يستدعي الميزانيّة (`:754-755`) | الخطوة 3 (`--role server`) | يحتاج Python ≥ 3.10 وPyYAML (`:21,45`). وSR‑6 وreview #20 مفتوحان (FR‑26) |
| `deploy/resource-budget.sh` | `--size` و`--block` و`--without-monitoring` و`--host-*`. ورموزه 0/1/2 (`:6-18`) | الخطوة 4 | يأخذ `COMPOSE_FILE` من البيئة مساراً واحداً (`:55`). ويشغّل `python3 -` و`import yaml` بلا `-I` (`:123,130`) |
| `deploy/rolling-deploy.sh` | يبدّل خدمةً مسمّاةً نسخةً نسخة: بالفائض لخدمة HTTP، وفي المكان لعاملٍ بفحص نبض (`:40-59`). يأخذ العدد من النسخ العاملة (`:199-200`)، ويرفض خدمةً بلا فحص صحّة (`:206`) | 5.3 الخطوة 4 | خدمةٌ واحدة لكلّ تشغيل. وكلّ نسخةٍ جديدة تأخذ بيئة الصدفة و`.env` في تلك اللحظة (`up -d --no-deps --no-recreate --scale`، `:308-324`) |
| `deploy/smoke/stack_smoke.py` | داخل صورة التطبيق (`Dockerfile:143`): PgBouncer، وRLS، والروابط الموقّعة على العنوان العامّ، والرفع على RunPod وحده (`:1-32`) | الخطوة 7 | لا يرفع ملفّاً على Compose ([4] لـRunPod وحده) |
| `app.ops.backup` | `full` و`status` و`base` و`dump` و`wal` و`qdrant` و`prune`، عبر `--profile backup run --rm backup` (`src/app/ops/backup.py:7-8,1331-1370`) | 5.3 الخطوة 1 | النسخ في MinIO على الخادم نفسه (6.4) |
| `deploy/backup/restore_drill.sh` | **تمرين:** يسترجع إلى حاويةٍ جانبيّة وحجمٍ جديد، ويقارن العلامات وعدد الصفوف، ويكتب صفّين في القاعدة الحيّة (`:1-36`) | — | **ليس أداة استرجاعٍ في المكان.** فـ«استرجاع النسخة» في رجوع 5.3 لا أداة له (§5.10 البند 10) |
| `deploy/release/build-push.sh` و`release.yml` | يبنيان الصور الثلاث ويرفعانها عند وسم `v*`. ويكتب السكربت سطراً لكلّ صورة: `<repo>:<tag> <sha256> <commit>` (`:238`)، ويرفعه CI أثراً `release-digests` يبقى 90 يوماً (`release.yml:132,153-155`) | مصدر البصمات لـ`VERSION` (FR‑5) | الأثر في GitHub. والبديل `docker buildx imagetools inspect`، ويحتاج دخول السجلّ الخاصّ |

### 5.6 الوسوم والإصدارات

- **وسما الإصدار في git:** `v2026.10.1` و`v2026.10.2`.
- **لا إصدار يحمل ملفّات Compose الحاليّة ولا صورة CUDA:**
  - `v2026.10.2` يشير إلى `1814998` (2026‑10‑14 ‏06:57)، **قبل** PR ‏#7.
  - شجرته بلا `docker-compose.gpu.yml`.
  - و`build-push.sh` فيها يقول عن `embedding-cuda`: «RESERVED for plan step 2.4, NOT built here» (`git show v2026.10.2:deploy/release/build-push.sh`، السطر 25).
  - **ومع ذلك يذكر `.env.example:40` الوسم `embedding-cuda-2026.10.2`.** الأرجح أنّه غير موجود. لم يُتحقَّق من السجلّ، فهو خاصّ.
  - وأوّل إصدارٍ بالصور الثلاث ينتظر ش‑4 (`docs/delivery/gpu-compose/acceptance.md:157`): عرض حجم صورة CUDA على المالك (4.13 / 6.9 / 11.1 ج.ب)، وسعة Docker Hub.
- **صورة التطبيق لم تتغيّر منذ `v2026.10.1`.** `git log v2026.10.1..master -- src migrations Dockerfile` فارغ. والتغيير بعده في `services/embedding/` وحده (`3032cd9` و`7212bf3`). فـ`app-2026.10.1` و`embedding-2026.10.1` صالحتان لإثباتٍ محلّيّ بوضع المعالج (Q‑7).
- **الوسم يصل إلى Compose من ستّة متغيّرات:** `APP_IMAGE_REPO`/`TAG` (`docker-compose.yml:60`)، و`EMBEDDING_IMAGE_REPO`/`TAG` (`:1256`)، و`EMBEDDING_CUDA_IMAGE_REPO`/`TAG` (`docker-compose.gpu.yml:57`).
  - قيمها الافتراضيّة `aizzak/app:dev` و`aizzak/embedding:dev` و`aizzak/embedding-cuda:dev`. فـ`.env` بلا هذه الأسطر يطلب `:dev`، وهو ممنوعٌ على الخادم.
  - وقيمة `TAG` تقبل `app-<V>@sha256:<digest>`، فيُثبَّت الوسم والبصمة معاً دون تعديل Compose. هذا للمصمّم.

### 5.7 الكرت والمراقبة: `COMPOSE_FILE` و`COMPOSE_PROFILES`

| الخادم | `COMPOSE_FILE` | `COMPOSE_PROFILES` | الخدمات (مقيسة) |
|---|---|---|---|
| كرت + مراقبة (الافتراضيّ) | `docker-compose.yml:docker-compose.gpu.yml` | `local-llm,monitoring` | 36 |
| كرت دون مراقبة | كذلك | `local-llm` | 24 |
| معالج + مراقبة | `docker-compose.yml` | `local-llm,monitoring` | 35 |
| معالج دون مراقبة | كذلك | `local-llm` | 24 |

- **التعيين نظيف.**
  - `wsl-ollama` لجهاز المالك وحده (`docker-compose.yml:1621-1625`).
  - `container-metrics` و`load` و`backup` لا تُفعَّل.
  - ملفّ الكرت يضيف المصدّر تحت `monitoring`، ويضيف لـPrometheus ملفّ أهداف. ويبقى `config -q` سليماً دون المراقبة (§5.8).
- **`METRICS_EXPORTER_PASSWORD` يبقى إلزاميّاً حتّى دون المراقبة.** Compose يستبدل متغيّرات الخدمات المطفأة أيضاً (`monitoring-profile/requirements.md` §5.6). فـ`install.sh` يولّده في الحالين.

### 5.8 قياساتٌ في مجلّد scratch (بلا أيّ حاوية)

1. **حزمةٌ مرشّحة:**
   - `git archive HEAD` للمسارات في FR‑2، مفكوكةً في مجلّدٍ فارغ: 45 ملفّاً، و804 ك.ب، و213,710 بايتاً مضغوطة.
   - فيها `auto_size.py` و`gpu-preflight.sh`، وليس فيها `install.sh` بعد.
   - ولا `.env` ولا شهادة ولا `auto.conf`، لأنّ `git archive` يأخذ الملفّات المتتبَّعة وحدها.
2. **`config -q`:**
   - أُضيفت إلى نسخة `.env.example` أسطر `COMPOSE_FILE` و`COMPOSE_PROFILES` والوسوم الستّة، ومُحي `COMPOSE_*` من البيئة.
   - **النتيجة: 16 من 16 تركيبةً تنجح**، وهي (كرت، معالج) × (مراقبة، دونها) × (بلا حجم، `small`، `medium`، `large`).
   - وعدد الخدمات كما في §5.7. و`config --images` بلا `:dev` ولا `:latest` في كلّها.
3. **`auto` من الحزمة المفكوكة** (`python3 -I deploy/auto_size.py --role server --host-cpus N --host-memory-gb G`، وضع الكرت):
   - 8/16 و16/32 و32/64: الرمز 0.
   - 8/12: الرمز 1، و«the smallest limits need 14.28 GB, 10.80 GB allowed».
   - **بعد الكتابة على 16/32:**
     - `resource-budget.sh --block .env`: الذاكرة 28.79 من 32.00 ج.ب (90%)، والمعالج 20.07 من 16 (1.25×)، والرمز 0.
     - و`config -q` ينجح.
     - و`auto.conf` كُتب في `deploy/postgres/sizes/` داخل الحزمة. فـ`upgrade.sh` يجب أن يحفظه.
4. **بصمة Compose لا ترى محتوى الملفّات المربوطة.**
   - عُدّل `deploy/vault/app-policy.hcl` و`deploy/nginx/app-locations.conf` و`deploy/postgres/postgresql.conf`.
   - فبقيت `config --hash` لـ`vault-bootstrap` و`nginx` و`postgres` كما هي.
   - **فالنتيجة:** `up -d` بعد تبديل الملفّات لا يعيد تشغيل شيء (FR‑30).
5. **`VAULT_SECRET_ID` في `.env` يغيّر بصمة ثماني خدمات** (§5.4).
6. **الذاكرة على هذا الجهاز:** 12 ج.ب يراها لينكس، و11 متاحة لحظة القياس، ولا حاوية تعمل (`docker ps` فارغ). والمكدّس الحيّ قد يقلع في أيّ وقت (`CLAUDE.md`).
   - مكدّسٌ كامل ثانٍ بجانبه لا يتّسع: حدود `small` وحدها 14.22 ج.ب (`monitoring-profile/requirements.md` NFR‑2).
   - **فالإثبات المحلّيّ الكامل لا يكون إلّا والمكدّس الحيّ متوقّف** (Q‑7).
   - لا `container_name` في الملفّين، فمشروعٌ ثانٍ بـ`-p` لا يتصادم بالأسماء. لكنّه يتصادم بالمنافذ: 80 و443 و`HOST_PORT_*` التسعة (`docker-compose.yml`)، فتُنقل كلّها.

### 5.9 القاعدة 1 (المكدّس المحلّيّ لا يتغيّر)

- **المرحلة 5 لا تلمس `docker-compose*.yml` ولا ملفّات الأحجام**، إلّا تعليق `docker-compose.yml:148-151`، وقد قرّر Q‑4 تعديله (FR‑25). والتعليق لا يدخل البصمة.
  - الملفّات الجديدة سكربتاتٌ في `deploy/release/` واختبارات.
  - وتعديل `auto_size.py` و`resource-budget.sh` (FR‑26) يغيّر سلوكهما تحت root وحده.
- **خطران يُغلقان بمتطلّب:**
  1. **`install.sh` في نسخة المستودع** يكتب `.env` وأسطر `COMPOSE_*`، فيغيّر جهاز المالك. ويغلقه FR‑9.
  2. **حفظ `VAULT_SECRET_ID` بـ`env_file:` في Compose** يغيّر بصمة ثماني خدمات، فيعيد إنشاءها على جهاز المالك. لذلك هو مستبعدٌ في Q‑4.

### 5.10 تعارضاتٌ بين الخطّة والشيفرة

1. **«24 مساراً» صارت 27** (§5.1). أُضيفت ثلاثةٌ بعد كتابة الخطّة. وفي الحزمة 25. ← FR‑2، FR‑34.
2. **«كلمات السرّ الإحدى عشرة» صحيحة، لكنّها ناقصة** (§5.2). فمعها `MINIO_ROOT_USER` بلا قيمةٍ افتراضيّة، و`GRAFANA_ADMIN_PASSWORD` بقيمةٍ افتراضيّة `admin`. فيولّد `install.sh` ثلاث عشرة. ← FR‑12.
3. **قائمة 5.1 تنسى `deploy/auto_size.py`**، و5.2 الخطوة 3 تستدعيه (`:344`). **وتنسى `deploy/gpu-preflight.sh`**، و`docs/deploy-server.md:5` يقول إنّ `install.sh` يستدعيه. ولا يلزمها `stack_smoke.py`، فهو داخل الصورة. ← FR‑2.
4. **ترتيب 5.2 الخطوة 6 ينقصه شيئان:**
   - كلمة سرّ `metrics_exporter` بعد أوّل `up`.
   - وكلمة «يفحص ثمّ يُقلع» لـ`ollama-pull`، لأنّ لا شيء ينتظره.
   وما بقي يرتّبه Compose بنفسه، إلّا إصدار `secret_id` (§5.3). ← FR‑19.
5. **مكان حفظ `VAULT_SECRET_ID`** يتعارض بين الوثائق المُلزِمة وخطوة 6.5 وممارسة المالك (§5.4). ← **Q‑4، حُسم:** في `.env`، وبلا 0600 (FR‑11، FR‑19، FR‑25، NFR‑3).
6. **قبول 5.2 (`./install.sh --domain <النطاق>`) لا يصل إلى `/health` 200 وحده**، لأنّ `FIREBASE_PROJECT_ID` إلزاميّ وفارغٌ في القالب. ← FR‑14، Q‑8 (أ).
7. **السجلّ خاصّ** (قرار 1، `:451`)، والقاعدة 6 تمنع مفتاحه في الحزمة. فـ`docker compose pull` (`:346`) يفشل دون `docker login` يسبقه، والخطّة لا تذكره. ← FR‑8، Q‑9 (مفتاحٌ قرائيٌّ لكلّ خادم).
8. **لا إصدار يحمل Compose اليوم ولا صورة CUDA** (§5.6). فقبول 5.1 على وسمٍ حقيقيّ (`:333`) ينتظر `v2026.10.3` فما بعده، بعد الدمج وبعد ش‑4. ← Q‑10.
9. **5.3 «يكتب الوسم في `.env` ثمّ يسحب» لا يكفي** (`:358`). فالإصدار الجديد قد يغيّر `docker-compose.yml` وملفّات الإعداد. والبصمة لا ترى محتوى الملفّات المربوطة (§5.8 البند 4). فلا يطبّق `up -d` سياسة Vault الجديدة ولا إعداد nginx الجديد. ← FR‑27، FR‑30 (مؤجّلان مع 5.3، Q‑3 (ب)).
10. **رجوع 5.3 «باسترجاع النسخة» (`:362`) بلا أداة:**
    - `restore_drill.sh` تمرينٌ على حاويةٍ جانبيّة (§5.5).
    - والاسترجاع في المكان يمحو ما كُتب بعد النسخة، فيحتاج إنساناً (القاعدة 9).
    - **وبالمقابل:** سياسة التوسيع ثمّ التضييق المُلزِمة (`08-local-runbook.md:423-431`) تجعل الرجوع بالصورة وحدها آمناً بعد هجرة توسيع.
    - **لكنّ** `migrate` القديم سيقابل مراجعاتٍ لا يعرفها، و`command.upgrade` يرفضها على الأرجح (`src/app/ops/provision.py:432-435`؛ سلوك Alembic، **لم يُقَس**). فيمنع `up` لـ`app` عبر `depends_on`.
    ← FR‑32، Q‑6 (مؤجّلان مع 5.3، Q‑3 (ب)).
11. **`host-tuning.sh` لا يبقى بعد إعادة التشغيل** (§5.5)، و7.3 تطلب «كلّ شيءٍ يعود وحده» (`:433`). ← FR‑10، Q‑5 (أ): الحفظ في `/etc/sysctl.d/`.
12. **3.1 تقول إنّ `rolling-deploy.sh` يقرأ عدد النسخ من Compose المحلول** (`:260`). وهو يعدّ النسخ العاملة (`:199-200`). لا ضرر على التثبيت، لكنّ `upgrade.sh` يحتاج الخدمة عاملةً قبل أن يبدّلها.
13. **الخطّة لا تذكر Python ≥ 3.10 مع PyYAML** متطلّباً للمضيف (2.1، `:198-201`). و`auto_size.py` و`resource-budget.sh` يحتاجانه. ← FR‑8، NFR‑9.
14. **5.2 تشغّل المولِّد بصلاحيّة root دون ذكر شرط القبول 3 من 3.5** (SR‑6، #20، SR‑11). ← FR‑26.
15. **§1.2 تعدّ `restore_drill.sh` أداةً جاهزة** (`:74`). وهي تمرينٌ لا استرجاع (البند 10).

## 6. الافتراضات والأسئلة (حُسمت في بوابة ①)

> **بوابة ① اكتملت في 2026‑10‑19 على مرحلتين** (`status.md` «قرارات وتصعيدات»):
> - **أوّلاً** أجاب المالك عن Q‑3 وQ‑4 وQ‑7 بنصّه.
> - **ثمّ** قال: «تم الموافقة على النطاق مع التوصيات، اكمل العمل». فالباقي بالمقترح.
>
> **وتفسير «بدون 0600» في Q‑4** لم يُجب عنه صراحةً. فالمعتمد: الصلاحيّة صفةٌ للملفّ كلّه، فلا يغيّر `install.sh` صلاحيّة `.env`، وفيه كلمات السرّ الثلاث عشرة مع المفتاح. ويُعرض على المالك عند المراجعة، وإن أراد غيره عُدّل FR‑11 وNFR‑3 وAC‑4.1.
>
> **لا سؤال مفتوح يحجز التصميم.**

| # | السؤال/الافتراض | يحتاج قرار من | القرار (بوابة ①، 2026‑10‑19) |
|---|---|---|---|
| Q‑1 | **موافقةٌ دفعةً واحدة على الملفّات المحميّة** (القاعدة 2، `:99`):<br>• **جديدة:** `deploy/release/bundle.sh`، و`deploy/release/install.sh`، و`deploy/release/upgrade.sh` (إن اختير Q‑3 (أ))، وربّما ملفّ قائمة الحزمة تحت `deploy/release/`.<br>• **معدَّلة:** `deploy/auto_size.py` (FR‑26)، و`deploy/resource-budget.sh` (`python3 -I`)، و`deploy/host-tuning.sh` (إن اختير الحفظ الدائم في Q‑5).<br>• **ربّما:** تعليق `docker-compose.yml:148-151` وحده (Q‑4). تعليقٌ لا يغيّر البصمة.<br>• **لا يُلمس:** `.github/workflows/`، و`Dockerfile`، و`docker-compose.gpu.yml`، و`deploy/runpod/`، و`deploy/nginx/`.<br>• **وخارج القاعدة 2:** الاختبارات، و`.env.example` (تعليقات فقط)، و`.gitignore`، والوثائق ومنها `08` و`05` المُلزِمتان.<br>**المقترح:** موافقةٌ على هذه القائمة. وما خرج عنها يُعرض قبل تعديله (سابقة `monitoring-profile` Q‑3) | المالك | **محسوم: موافَقٌ على القائمة دفعةً واحدة، دون `deploy/release/upgrade.sh`** (سقط مع Q‑3 (ب)).<br>وبقراري Q‑4 وQ‑5 صار المشروط فيها لازماً: تعليق `docker-compose.yml:148-151`، وتعديل `deploy/host-tuning.sh`.<br>وما خرج عن القائمة يُعرض على المالك قبل تعديله |
| Q‑2 | **أين تعيش السكربتات؟ وأين الحارس؟**<br>(أ) في المستودع تحت `deploy/release/`، بجانب `build-push.sh`. ويضع `bundle.sh` نسختين من `install.sh` و`upgrade.sh` في جذر الحزمة. وكلّ سكربتٍ يجد جذره بالمجلّد الذي فيه `docker-compose.yml` و`VERSION`، ويرفض العمل دون `VERSION` (FR‑9).<br>(ب) في جذر المستودع: يظهر `./install.sh` بجانب `docker-compose.yml` على جهاز المالك، ويُغري بتشغيله هناك.<br>**والحارس:** الأدنى في FR‑7 هنا، وبقيّة 7.1 في 7.1. وسبب الأدنى أنّ الحزمة بلا حارسٍ تفسد بصمت عند أوّل ربطٍ جديد، كما في RunPod حتّى `staging-4`.<br>**المقترح:** (أ)، والحارس الأدنى هنا | المالك | **محسوم: (أ).** السكربتات تحت `deploy/release/`، ويضع `bundle.sh` نسخةً من `install.sh` وحده في جذر الحزمة (لا `upgrade.sh`، Q‑3 (ب)).<br>والحارس الأدنى (FR‑7) هنا، وبقيّة 7.1 في 7.1 |
| Q‑3 | **`upgrade.sh` في هذه الميزة أم في ميزةٍ تالية؟**<br>(أ) الثلاث معاً: PR واحد، والحجم XL.<br>(ب) 5.1 و5.2 الآن، ثمّ 5.3 ميزةً تالية تبدأ بعد الدمج. **وأسبابها:**<br>&nbsp;&nbsp;• 5.3 «مهمّ» لا «يجب» (`:137`).<br>&nbsp;&nbsp;• قبولها يحتاج وسمين حقيقيّين فيهما `install.sh`، ولا يوجد أيٌّ منهما قبل الدمج (§5.6).<br>&nbsp;&nbsp;• وفيها مجهولان: تبديل الملفّات المربوطة (§5.8)، والرجوع عبر هجرة (Q‑6).<br>**المقترح:** (ب). القصّتان US‑9 وUS‑10 مكتوبتان، فتنتقلان كما هما | المالك | **محسوم: (ب)، بنصّ المالك.** 5.1 و5.2 في هذه الميزة، و5.3 ميزةٌ تالية بعد الدمج.<br>فـ§3.3 (FR‑27…FR‑32) وUS‑9 وUS‑10 مؤجّلةٌ بنصّها.<br>والنطاق 8 قصص و47 معيار قبول، والتقدير L |
| Q‑4 | **⚠️ تصعيد: أين يُحفظ `VAULT_SECRET_ID` على الخادم؟** الوثائق المُلزِمة تقول «لا في `.env`، من الصدفة» (§5.4). والخادم لا صدفة تبقى له. وتكرار الإصدار يعيد إنشاء ثماني خدمات (مقيس). **الخيارات:**<br>(أ) في `.env` بصلاحيّة 0600 على الخادم. تُعدَّل الوثائق: `05 §3.3` و`08 §3.1` و`quickstart` و`stack-commands`، وتعليق Compose. هو ما تفترضه 6.5 (`:406`)، وما يفعله المالك محلّيّاً (غير مُتحقَّق). ولا يضيف كشفاً جديداً: `.env` فيه أصلاً كلمات سرّ قاعدة البيانات وMinIO، و`init.json` على القرص نفسه. وتُسجَّل ثغرةً في قائمة أمان الخادم (القاعدة 8).<br>(ب) ملفٌّ منفصل بصلاحيّة 0600 يُمرَّر مع كلّ أمر: `--env-file .env --env-file vault.env`. لكنّ `--env-file` يعطّل قراءة `.env` التلقائيّة (`08-local-runbook.md:552`). فأيّ `docker compose up` يدويّ بدونه يقلع بمفتاحٍ فارغ، ثمّ يقفل Vault الدور.<br>(ج) في الصدفة وحدها: يحتاج كلّ تحديثٍ وكلّ إعادة إنشاء أن يُصدَر مفتاحٌ جديد يدويّاً، والخطر نفسه.<br>(د) `env_file:` في Compose: يغيّر بصمة ثماني خدمات على جهاز المالك (القاعدة 1). **مرفوض**.<br>**المقترح: (أ)** | المالك | **محسوم: (أ) في `.env`، بنصّ المالك**، وجزء 0600 منه مرفوض: «ضعه الآن في ملف `.env` بدون صلاحية 0600، وسأقوم أنا لاحقاً بتعديل الصلاحية».<br>**التفسير المعتمد:** `install.sh` لا يغيّر صلاحيّة `.env`، فتبقى على `umask`. ويطبع في تقريره سطراً يذكّر بـ`chmod 600 .env`.<br>**ويلزم منه:**<br>• تعديل الوثائق المُلزِمة وتعليق Compose (FR‑25).<br>• تسجيل ثغرتين في قائمة أمان `docs/deploy-server.md`: المفتاح في `.env`، و`.env` بلا 0600 (FR‑33).<br>← FR‑11، FR‑19، FR‑22، NFR‑3 |
| Q‑5 | **صلاحيّة root:**<br>(أ) `sudo ./install.sh` كاملاً بصلاحيّة root. يُنشأ `.env` لـroot بصلاحيّة 0600. وFR‑26 شرط. و`host-tuning.sh` يحفظ الضبط في `/etc/sysctl.d/` ليبقى بعد إعادة التشغيل. وكلّ أمر Compose بعدها بـ`sudo`.<br>(ب) مستخدمٌ في مجموعة `docker` و`sudo` لخطوة النواة وحدها. لكنّ مجموعة `docker` تساوي root أصلاً، و`.env` يُقرأ بصلاحيّة المستخدم.<br>**المقترح: (أ)**، مع الحفظ الدائم | المالك | **محسوم: (أ).** `sudo ./install.sh` كاملاً بصلاحيّة root، و`host-tuning.sh` يحفظ الضبط في `/etc/sysctl.d/` (FR‑10).<br>**إلّا** «يُنشأ `.env` بصلاحيّة 0600»: جواب المالك في Q‑4 يتقدّم |
| Q‑6 | **(إن اختير Q‑3 (أ)) الرجوع عبر هجرة:**<br>(أ) رجوعٌ بالصورة والملفّات وحدها، لأنّ سياسة التوسيع ثمّ التضييق تجعله آمناً بعد هجرة توسيع. وإن كان `migrate` القديم لا يقرأ المراجعات الجديدة، توقّف `upgrade.sh` وطبع الطريق اليدويّ. والاسترجاع في المكان يبقى إجراءً بشريّاً موثَّقاً لا آليّاً (القاعدة 9).<br>(ب) استرجاعٌ آليّ للنسخة، كما في الخطّة. لكن لا أداة له، ويمحو ما كُتب بعد النسخة.<br>**المقترح: (أ)** | المالك | **سقط** مع Q‑3 (ب). يُعرض على المالك من جديد في ميزة 5.3 |
| Q‑7 | **الإثبات المحلّيّ:**<br>• **دون موافقة:** الحزمة مفكوكةً و`config -q` لكلّ تركيبة، و`auto` على أجهزةٍ مفترضة، والسكربتات بـ`docker` مزيّف. كلّه [وحدة] و[config].<br>• **بموافقة:** تثبيتٌ كامل مرّةً واحدة في مشروعٍ منفصل `-p aizzak-install-proof` على هذا الجهاز:<br>&nbsp;&nbsp;– بوضع المعالج وصور `2026.10.1`، فشيفرة التطبيق لم تتغيّر منذها (§5.6).<br>&nbsp;&nbsp;– بمنافذ منقولة، **والمكدّس الحيّ متوقّف** (§5.8 البند 6).<br>&nbsp;&nbsp;– يثبت: `/health` 200، وتهيئة Vault مرّةً واحدة، والمفتاح حاضرٌ في الخدمات التسع (بالحضور لا بالقيمة)، وأنّ التكرار لا يعيد إنشاء حاوية.<br>&nbsp;&nbsp;– **ويترك أحجاماً بأسماء المشروع** لا يحذفها إلّا المالك، لأنّ الحارس يسأل عند `docker volume rm`.<br>&nbsp;&nbsp;– ووضع الكرت يبقى لـ7.3.<br>**المقترح:** نعم، مرّةً واحدة بعد البناء. وإن رُفض صار AC‑6.5 وAC‑7.4 [خادم] | المالك | **محسوم: نعم، بنصّ المالك.** تثبيتٌ كامل مرّةً واحدة بعد البناء:<br>• في مشروعٍ منفصل `-p`، بوضع المعالج، ومنافذ منقولة.<br>• والمكدّس الحيّ متوقّف أثناءها.<br>• والأحجام التي يتركها يحذفها المالك.<br>فـAC‑6.5 وAC‑7.4 يبقيان [محلّيّ] |
| Q‑8 | **`FIREBASE_PROJECT_ID`** (§5.10 البند 6):<br>(أ) خيارٌ إلزاميّ `--firebase-project <id>`، ويتوقّف التثبيت دونه. وتُصحَّح جملة القبول في الخطّة.<br>(ب) قيمةٌ افتراضيّة `aizzak-agent`، وهي قيمة RunPod التي أكّدها المالك (`docs/deploy-runpod.md:102`).<br>**المقترح: (أ)**، والمثال في الدليل يذكر `aizzak-agent` | المالك | **محسوم: (أ).** `--firebase-project <id>` إلزاميّ، والمثال في الدليل `aizzak-agent` (FR‑14، FR‑33) |
| Q‑9 | **دخول السجلّ الخاصّ على الخادم.** `install.sh` يتحقّق من الوصول، ويتوقّف ومعه تعليمة `docker login` إن فشل. ولا يأخذ المفتاح ولا يطبعه ولا يحفظه.<br>**من أين المفتاح؟** المقترح: مفتاحٌ قرائيّ منفصل (Docker Hub access token بصلاحيّة القراءة وحدها) لكلّ خادم، لا مفتاح الرفع الذي في CI (قرار 6، `:456`) | المالك | **محسوم: المقترح.** مفتاح Docker Hub قرائيٌّ منفصل لكلّ خادم، لا مفتاح الرفع الذي في CI. و`install.sh` لا يأخذه ولا يطبعه ولا يحفظه (FR‑8، AC‑3.3) |
| Q‑10 | **أوّل حزمةٍ حقيقيّة.** لا وسم اليوم فيه `install.sh` ولا صورة CUDA (§5.6). **المقترح:** بعد الدمج يدفع المالك `v2026.10.3`، بعد قرار ش‑4 في حجم صورة CUDA. ثمّ تُصنع حزمته ويُثبت AC‑1.7. وقبل ذلك يُثبت `bundle.sh` على وسمٍ تجريبيّ محلّيّ وملفّ بصماتٍ مزيّف | المالك | **محسوم: المقترح.** أوّل حزمةٍ حقيقيّة بعد الدمج: يدفع المالك `v2026.10.3` بعد قرار ش‑4 (AC‑1.7).<br>وقبل ذلك يُثبت `bundle.sh` على وسمٍ تجريبيّ محلّيّ وملفّ بصماتٍ مزيّف (AC‑1.1…AC‑1.6) |
| A‑1 | الإصدار بصيغة `YYYY.M.N` كما في `build-push.sh`. والحزمة `aizzak-<V>.tar.gz`، وفيها مجلّدٌ واحد `aizzak-<V>/` | — | — |
| A‑2 | الحزمة من الشجرة المثبّتة عند `v<V>` (FR‑4). ومصدر البصمات أثر `release-digests`، أو `imagetools inspect` بدخول السجلّ. يحسم المصمّم | المصمّم | — |
| A‑3 | `install.sh` بلا أسئلة تفاعليّة: خياراتٌ فقط، فيصلح للأتمتة. ولا استثناء في هذه الميزة: تأكيد الاسترجاع في US‑10 مؤجّلٌ مع 5.3 (Q‑3 (ب)) | — | — |
| A‑4 | `APP_ENV` اسمٌ في السجلّات لا يغيّر سلوكاً (§5.2). فلا يغيّره `install.sh` ما لم يقرّر المصمّم غير ذلك | — | — |
| A‑5 | `VAULT_ROLE_ID`: يولّد `install.sh` معرّفاً عشوائيّاً لكلّ خادم، بدل `aizzak-local-app` المشترك. ثمّ يثبّته `vault-bootstrap` بالآليّة الموجودة (`bootstrap.sh:151-165`)، فيبقى صالحاً إن فُقد حجم `vault-data`. والمعرّف غير سرّيّ (`05-rbac-config-secrets.md:109`) | المصمّم | — |
| A‑6 | حساب MinIO التجريبيّ يبقى مطفأً (`.env.example:277`)، فلا يُولَّد `MINIO_TEST_SECRET_KEY` | — | — |
| A‑7 | كلمات السرّ 48 حرفاً ستّ‑عشريّاً، أي `openssl rand -hex 24`، كما في RunPod (`docs/deploy-runpod.md:121`) و`quickstart.md:99` | المصمّم | — |
| A‑8 | المراقبة مفعّلةٌ افتراضيّاً، وتُطفأ بخيار (قرار 7) | — | — |
| A‑9 | لا وكيل يشغّل اختبارات `tests/integration/` ولا يلمس المكدّس الحيّ، إلّا في AC [محلّيّ] (AC‑6.5 وAC‑7.4)، وقد وافق عليهما المالك (Q‑7) | — | — |
| A‑10 | ثغرات الخادم تُكتب في `docs/deploy-server.md` بأرقامٍ بعد ث‑9. وقائمة RunPod تحمل ث‑10…ث‑12 أيضاً، لكنّها قائمةٌ مستقلّة | — | — |
| A‑11 | سلوك `up -d --wait` مع خدمات المرّة الواحدة يقيسه المصمّم في scratch، أو يتجنّبه بانتظار كلّ خطوةٍ بنفسه (§5.3) | المصمّم | — |
| A‑12 | النطاق العربيّ لا يُقبل إلّا بصيغة `xn--`، والتحويل خارج النطاق. والنطاق يُكتب بحروفٍ صغيرة | — | — |
| A‑13 | تغيير النطاق أو الحجم المسمّى أو الوضع بعد التثبيت ليس «تكراراً»: يتوقّف `install.sh` ويسمّي المفتاح. وإعادة الإعداد خارج النطاق | — | — |
| A‑14 | `ollama-pull` يحتاج الإنترنت على الخادم. والتثبيت بلا شبكة خارج النطاق | — | — |
| A‑15 | **تعديل الوثائق المُلزِمة لـQ‑4 بأضيق قراءة** (FR‑25): تُستبدل جمل المنع وحدها، ومنها «لا يُكتب في `.env` أبداً» و«NEVER put this in .env»، بأنّ المفتاح يُحفظ في `.env`، وأنّ `install.sh` يكتبه هناك على الخادم. وتبقى أمثلة التمرير من الصدفة، لأنّها ما زالت تعمل: الصدفة تفوز (`docker-compose.yml:149-150`) | — | أُضيف بعد بوابة ① |

## 7. التقدير

| البند | الحجم (S/M/L/XL) | ملاحظات |
|---|---|---|
| US‑1 الحزمة و`VERSION` | M | `git archive` للقائمة، وتحقّقٌ من البصمات، واختبارٌ على مستودعٍ مؤقّت بوسمٍ تجريبيّ |
| US‑2 الحارس الأدنى | S | قراءة الربط من الملفّين ومقارنته بالقائمة، وإفسادٌ في الاتّجاهين |
| US‑3 الفحص والوضع | M | خريطة رموز `gpu-preflight.sh`، والمساحة، وPython، والسجلّ، ورفض نسخة المستودع |
| US‑4 `.env` والأسرار | M | التوليد والكتابة دون طباعة، والتكرار بايتاً ببايت |
| US‑5 الحجم والميزانيّة والتقوية | M | استدعاء المولِّد والميزانيّة، ومعه FR‑26 في `auto_size.py` و`resource-budget.sh` |
| US‑6 السحب وأوّل إقلاع | L | الترتيب، وإصدار المفتاح دون كشف التوكن، وخطوة `metrics_exporter`، وبيئة `docker` مزيّفة تحفظ الحالة |
| US‑7 الفحوص والتقرير والتكرار | M | |
| US‑8 الوثائق واللوحة | S | ثلاث وثائق للأمر، وجمل المنع في خمسة مواضع مع تعليق Compose (Q‑4، A‑15)، وقسمٌ في `deploy-server.md` ومعه ثغرتا Q‑4، واللوحة والملخّص |
| US‑9 التحديث: **مؤجّل** إلى الميزة التالية (Q‑3 (ب)) | (L) | لا يُحتسب هنا. تبديل الملفّات وأثرها على الخدمات، والدمج مع `rolling-deploy.sh` |
| US‑10 الرجوع: **مؤجّل** إلى الميزة التالية (Q‑3 (ب)) | (M) | لا يُحتسب هنا |
| **الإجمالي** | **L** (US‑1…US‑8، Q‑3 (ب)). وتقدير 5.3 الأوّليّ ميزةً تالية: L | **التقدير:** جلستان إلى ثلاث، ومعها المراجعة.<br>**المخاطر:**<br>• **Q‑4 حُسم بقرار المالك، وبقي أثره:** `.env` على الخادم فيه كلمات السرّ الثلاث عشرة والمفتاح، ويقرؤه كلّ حسابٍ محلّيّ حتّى يضبط المالك `chmod 600`. ويخفّفه سطر التذكير (FR‑22)، والثغرتان في `deploy-server.md` (FR‑33).<br>• تحذير SR‑11 من المولِّد يظهر في كلّ تثبيتٍ ما دام `.env` مقروءاً للآخرين (AC‑5.5 (د)). هذا متوقَّع، ولا يوقف التثبيت.<br>• تعديل خمس وثائق مُلزِمة (`05` و`08` و`quickstart` و`stack-commands` وتعليق Compose) قد يفوته موضع. ويحرسه AC‑8.2.<br>• لا إصدار يحمل Compose اليوم، فقبول 5.1 الحقيقيّ بعد الدمج (Q‑10، ش‑4).<br>• سكربت bash طويل بلا إثباتٍ حيّ قبل الإثبات المحلّيّ (Q‑7، بعد البناء) أو 7.3. والتكلفة في المراجعات: 3.2 مليون رمز في `monitoring-profile` (`retro.md:74`).<br>• `up --wait` غير مُتحقَّق (A‑11).<br>• Python وPyYAML على مضيفٍ مجهول.<br>• صلاحيّة 30 يوماً لـ`secret_id` قبل 6.5: خادمٌ يُثبَّت اليوم يفقد Vault بعد شهر.<br>• الإثبات المحلّيّ يحتاج إيقاف المكدّس الحيّ، ويترك أحجاماً يحذفها المالك |

</div>
