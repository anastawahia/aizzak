<div dir="rtl">

# تشغيل AIZZAK على خادمٍ بكرت NVIDIA

> **ما هذه الوثيقة؟** هيكلٌ أوّل لدليل تشغيل الخادم. فيه الآن **قسم المتطلّبات** (الخطّة [`server-gpu-plan.md`](server-gpu-plan.md) الخطوة 2.1)، وقسم المراقبة والتنبيهات (4.1)، وقسم التثبيت (§3، الخطوتان 5.1 و5.2: حزمة الإصدار و`install.sh`)، وقسم الشهادة عبر Cloudflare (§4، الخطوة 6.1)، وقائمة الأمان المؤجّلة (بدأت فارغة). يكمله دليل التشغيل الكامل في الخطوة 7.4 بعد تجربة الخادم الحقيقيّ (7.3). ويستدعي `install.sh` سكربت الفحص أدناه.
>
> **الوثائق المرتبطة:**
> - أوامر المكدّس: [`stack-commands.md`](stack-commands.md).
> - التشغيل المحلّيّ: [`quickstart.md`](quickstart.md).
> - المرجع المُلزِم للقرارات التشغيليّة: [`design/08-local-runbook.md`](design/08-local-runbook.md).

---

## 1) المتطلّبات

ثلاثة أشياء على **المضيف** قبل أيّ أمر Compose. الثلاثة تُثبَّت يدويّاً، لأنّ تثبيتها يختلف بين التوزيعات، والسكربت في [§1.4](#14-الفحص) **يفحصها ولا يثبّتها**.

| المتطلّب | كيف تعرف أنّه موجود | الدليل الرسميّ |
|---|---|---|
| تعريف NVIDIA | `nvidia-smi` يعمل على المضيف ويعرض الكرت | [تحميل التعريف](https://www.nvidia.com/Download/index.aspx)، و[دليل تثبيت التعريف على لينكس](https://docs.nvidia.com/datacenter/tesla/driver-installation-guide/index.html) |
| NVIDIA Container Toolkit بإصدار 1.17.8 فما فوق، مضبوطةً لـDocker | `docker info` يعرض وقت التشغيل `nvidia`، و`nvidia-ctk --version` يعرض 1.17.8 فما فوق | [دليل التثبيت الرسميّ](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) (فيه `apt` و`dnf`) |
| Docker Engine بإصدار 26.0.0 فما فوق، وCompose plugin بإصدار 2.20 فما فوق | `docker version` و`docker compose version` | [Docker Engine](https://docs.docker.com/engine/install/)، و[Compose plugin](https://docs.docker.com/compose/install/linux/) |

### 1.1 تعريف NVIDIA

- يُثبَّت على المضيف نفسه (لا داخل حاوية)، بإصدارٍ يدعم CUDA 12.4 فما فوق، لأنّ صورة التضمين على الكرت تحمل مكتبات CUDA 12.4 بنفسها، والمضيف يعطي التعريف وحده.
- على WSL يأتي التعريف من Windows ولا يُثبَّت داخل لينكس، و`nvidia-smi` يظهر تحت `/usr/lib/wsl/lib`.

### 1.2 NVIDIA Container Toolkit

بعد تثبيت الحزم من الدليل الرسميّ أعلاه (NVIDIA Container Toolkit بإصدار ≥ 1.17.8، وتُعرف بـ`nvidia-ctk --version`)، يُسجَّل وقت التشغيل في Docker ثمّ يُعاد تشغيل Docker:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

- ⚠️ **إعادة تشغيل Docker توقف كلّ حاويات المضيف لحظةً** ثمّ تعيدها سياسة `restart: unless-stopped`. فاختر لها وقتاً هادئاً على خادمٍ يعمل.
- الأمر الأوّل **يعدّل** `/etc/docker/daemon.json` (وينشئه إن لم يوجد). السكربت في [§1.4](#14-الفحص) لا ينفّذه، ويطبعه نصيحةً حين يغيب وقت التشغيل.

### 1.3 Docker Engine وCompose

- Compose v2 بإصدار **2.20.0 أو أحدث**، لأنّ ملفّ المكدّس يستعمل `depends_on.required`. الإصدار يُقارَن عدداً لا نصّاً (`v2.9.0` أقدم من `v2.20.0`)، واللاحقة مثل `-desktop.1` مقبولة.
- المستخدم الذي يشغّل المكدّس يصل إلى مقبس Docker (عضو في مجموعة `docker`). إن لم يصل فالسكربت يقول «permission denied» لا «الأداة غائبة».

### 1.4 الفحص

```bash
deploy/gpu-preflight.sh            # static checks; starts no container, takes a few seconds
deploy/gpu-preflight.sh --smoke    # plus ONE throwaway container: the GPU's name from inside it
```

- **يفحص ولا يثبّت.** لا يستدعي `apt` ولا `dnf` ولا `sudo` ولا `systemctl`، ولا يغيّر إعداداً. لا يقرأ متغيّرات البيئة ولا ملفّاً سرّيّاً، ولا يحتاج root.
- يشغّل الفحوص كلّها ويطبع سطراً لكلٍّ منها، ويطبع **كلّ** الكروت إن تعدّدت. وكلّ نداءٍ خارجيّ بمهلة قصيرة، فلا يعلّق تعريفٌ أو Docker ميّت السكربت.
- **`--smoke`** يضيف نداءً حيّاً واحداً: `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi --query-gpu=name --format=csv,noheader`. أوّل تشغيلٍ يسحب الصورة (92 م.ب مضغوطة)، فيحتاج الشبكة.
- **حدٌّ أدنى لإصدار الأداة:** يقرأ السكربت `nvidia-ctk --version` ويفشل بـ12 تحت **1.17.8** (المتغيّر `MIN_NVIDIA_CTK`). خطّافات الأداة تعمل root على المضيف وتقرأ جذر الصورة وبيئتها قبل أن يسري `cap_drop` أو `no-new-privileges`، والإصدارات الأقدم فيها CVE‑2024‑0132 وCVE‑2025‑23359 وCVE‑2025‑23266 (هروبٌ من حاوية مصنوعة عمداً). فنجاح `--smoke` لا يغلب هذا الفشل، ولا يُشغَّل `--smoke` معه. يُراجَع الحدّ مع نشرات NVIDIA الأمنيّة في 7.3.
- **مضيفٌ بـCDI:** قد لا يظهر وقت التشغيل `nvidia` في `docker info` والكرت يعمل. نجاح `--smoke` **يغلب** غياب وقت التشغيل فقط في هذه الحال، **وبشرط أن يكون فحص `nvidia-ctk` سليماً** (الأداة على `PATH` وإصدارها ≥ 1.17.8): يخرج بـ0، لأنّ Docker يحلّ `--gpus` وحجز Compose بمسار الـhook أو CDI لا بتسجيل وقت التشغيل. ودون `--smoke` يخرج بـ12 مع النصيحة نفسها وتلميح «جرّب `--smoke`».
- **مضيفٌ بلا `nvidia-ctk` على `PATH` (Docker Desktop مثلاً) يخرج بـ12 عمداً، حتّى مع `--smoke`**، لأنّ إصدار الأداة لا يمكن قراءته، ولا يُشغَّل `--smoke` معه (قرار المالك 2026‑10‑18).

**رموز الخروج** (تطابق رأس السكربت، ويفحص ذلك اختبار):

| الرمز | المعنى | ما يفعله التثبيت (5.2) |
|---|---|---|
| 0 | جاهز لـ`docker-compose.gpu.yml` | وضع الكرت |
| 2 | استعمالٌ خاطئ (وسيطٌ غير معروف) | — |
| 10 | لا كرت صالح (`nvidia-smi` غائب أو يفشل)، وDocker وCompose سليمان | وضع المعالج، دون ملفّ الكرت |
| 11 | Docker غير صالح: لا CLI، أو الخادم لا يجيب، أو `permission denied` على المقبس، أو Engine أقدم من 26.0.0 أو إصداره غير مقروء (CVE‑2024‑29018) | توقّف |
| 12 | كرتٌ موجود، وNVIDIA Container Toolkit غير مضبوطة لـDocker (لا وقت تشغيل `nvidia`، ولا `--smoke` ناجح)، **أو** إصدارها (`nvidia-ctk --version`) أقدم من 1.17.8 (والإصدار التجريبيّ `rc` أو `alpha` أو `beta` منه أقدم) أو الأداة غائبة عن `PATH` أو إجابتها غير مقروءة (`--smoke` لا يغلب هذا) | توقّف، أو وضع المعالج باختيار |
| 13 | Docker Compose غائب أو أقدم من 2.20.0 | توقّف |
| 14 | `--smoke`: الفحوص الثابتة نجحت، وحاوية الكرت فشلت (ومعها رسالة Docker، ومنها فشل السحب) | توقّف |

- **الأسبقيّة** عند أكثر من فشل: 11، ثمّ 13، ثمّ 10، ثمّ 12. أعطال المنصّة (Docker وCompose) تسبق أعطال الكرت لأنّها تمنع التشغيل على المعالج غالباً أيضاً. فالرمز 10 يعني: «لا كرت، والتشغيل على المعالج ممكن». **واستثناءٌ واحد:** حدّ Docker Engine الأدنى يحمي الشبكة الداخليّة في ملحق الكرت (CVE‑2024‑29018)، فمضيفٌ بلا كرت وEngine أقدم من 26.0.0 يعطي 11 مع أنّ المكدّس على المعالج كان سيعمل. هو حدٌّ للخادم يُحدَّث قبل أيّ شيء.
- الرمز 1 خطأٌ غير متوقّع داخل السكربت نفسه.

---

## 2) المراقبة والتنبيهات

1. **مفعّلةٌ افتراضيّاً** (قرار 7): `COMPOSE_PROFILES=local-llm,monitoring`. خدمات المراقبة الإحدى عشرة (Prometheus وGrafana وAlertmanager و`alert-sink` والمصدّرات الخمسة وLoki وAlloy) خلف profile `monitoring`.
2. **الخادم الصغير وحده يحذفها.** ⚠️ **دونها لا تنبيهات أصلاً:**
   - افحص `/health` **من خارج الخادم**: خدمة رصدٍ خارجيّة تطلب `https://<النطاق>/health` كلّ دقيقة، وتنبّه على غير 200. والاختيار في 7.4.
   - **وما يغيب:** التنبيهات الأمنيّة `AizzakVaultAuthFailing` و`AizzakRateLimitedShareHigh` و`AizzakRedisStreamEvicted`، وتجميع السجلّات (Loki): تبقى السجلّات في `json-file` وحده، 10m × 3 لكلّ حاوية، أي نافذة تحقيقٍ من ساعاتٍ على خدمةٍ مزدحمة.
   - و`/health` الخارجيّ فحصُ حيويّةٍ فقط (لا يلمس Vault ولا قاعدة البيانات)، فلا يرى فشل الاعتمادات ولا إساءة الاستعمال، ولا يعوّض هذه التنبيهات.
   - و`install.sh` (§3) يطبع ذلك في تقريره حين يُشغَّل بـ`--no-monitoring`.
3. **حكم الميزانيّة دونها:** `deploy/resource-budget.sh --without-monitoring --size <الحجم>`.
4. **مع ملفّ الكرت والمراقبة:** مصدّر الكرت `nvidia-gpu-exporter`، وهدف `nvidia-gpu` في Prometheus، وتنبيها الكرت، وصفّ GPU في لوحة capacity (4.2).

---

## 3) التثبيت (`install.sh`)

لا مستودع على الخادم. حزمة إصدارٍ واحدة، `aizzak-<الإصدار>.tar.gz`، تصنعها `deploy/release/bundle.sh` من وسم الإصدار (5.1) وفيها ما يحتاجه Compose وحده: ملفّا Compose، و`.env.example`، وأحجام `deploy/sizes/`، والسكربتات، وملفّات الإعداد المربوطة، وملفّ `VERSION` ببصمات الصور الثلاث. ثمّ تُفكّ في مجلّدٍ ويُشغَّل منها `sudo ./install.sh` (5.2). والصور تُسحب من Docker Hub بالبصمة الواردة في `VERSION`.

### 3.1 المتطلّبات

- **`sudo`:** السكربت يعمل root، لأنّه يضبط النواة (`/etc/sysctl.d/`) ويكتب ملفّاً فيه أسرار. وبغير root يخرج بالرمز 4 ويقول `sudo ./install.sh ...`.
- **`docker login`:** السجلّ خاصّ. سجّل الدخول مرّةً واحدة بحساب root، بمفتاح وصولٍ **قرائيٍّ منفصلٍ لهذا الخادم وحده** (لا مفتاح الرفع الذي في CI): `sudo docker login docker.io -u <المستخدم>`. السكربت لا يأخذ مفتاحاً في أيّ خيار، ولا يقرأ ملفّ اعتماداتك، ولا يحفظ شيئاً.
- **Python 3.10 فما فوق مع PyYAML** (`python3 -c 'import yaml'`، ويثبَّت بـ`sudo apt-get install python3-yaml`)، و`curl` و`flock` و`timeout` و`df`. وbash 4 فما فوق.
- **متطلّبات §1** (Docker وCompose، والتعريف والأداة للكرت). يفحصها `deploy/gpu-preflight.sh` من داخل `install.sh`.
- **مساحة حرّة** في مجلّد بيانات Docker: 40 ج.ب لوضع الكرت و30 للمعالج (تقدير: الصور قِيست 27 و19.5 ج.ب). تُفحص في التثبيت الأوّل وحده.

### 3.2 التحقّق والفكّ

```bash
sha256sum aizzak-2026.10.3.tar.gz           # قارنه بالقيمة التي طبعها bundle.sh
sudo tar -xzf aizzak-2026.10.3.tar.gz -C /opt
cd /opt/aizzak-2026.10.3
```

الملكيّة لـroot شرط، لا نصيحة: يرفض `install.sh` (الرمز 3) أن يعمل إن كان المجلّد أو `deploy` أو `deploy/postgres/sizes` أو `.env` رابطاً رمزيّاً أو يملكه غير root. والحزمة بلا توقيع اليوم (ث‑12)، فمقارنة sha256 باليد هي كلّ الضمان.

### 3.3 الأمر والخيارات

```bash
sudo docker login docker.io -u <المستخدم>
sudo ./install.sh --domain example.com --firebase-project aizzak-agent
```

| الخيار | المعنى |
|---|---|
| `--domain NAME` | إلزاميّ. النطاق العامّ بحروف ASCII (النطاق العربيّ بصيغة `xn--`)، بلا مخطّط ولا مسار ولا منفذ |
| `--firebase-project ID` | إلزاميّ. معرّف مشروع Firebase (مثل `aizzak-agent`). التطبيق لا يقلع دونه |
| `--size S` | `auto` افتراضيّاً: تُحسب الحدود من هذا الخادم. أو `small` أو `medium` أو `large` |
| `--cpu` | وضع المعالج صراحةً، ولو وُجد كرت |
| `--no-monitoring` | دون خدمات المراقبة. **لا تنبيهات** حينها (§2) |
| `--gpu-smoke` | يضيف `--smoke` إلى فحص الكرت (مضيف CDI، §1.4) |

لا خيار يأخذ كلمة سرّ أو مفتاحاً أو توكناً، ولا أسئلة تفاعليّة. وكلّ خيارٍ يُعطى مرّةً واحدة.

### 3.4 الخطوات

1. **الفحص، ولا شيء يُكتب:** `gpu-preflight.sh` يحدّد وضع الكرت أو المعالج (جدول §1.4)، ثمّ المساحة، ثمّ وصول السجلّ.
2. **النواة:** `host-tuning.sh --apply --persist` يضبط `vm.overcommit_memory=1` ويحفظه في `/etc/sysctl.d/90-aizzak.conf`.
3. **`.env`:** يُنسخ من `.env.example` وتُولَّد كلمات السرّ الثلاث عشرة (48 خانة سداسيّة لكلّ منها) ويُكتب النطاق ومشروع Firebase و`VAULT_ROLE_ID` عشوائيّاً لهذا الخادم، وكتلة الإصدار (`COMPOSE_FILE` و`COMPOSE_PROFILES` ووسوم الصور مثبّتةً بالبصمة). لا شيء من هذا يُطبع.
4. **الحجم والميزانية:** `auto_size.py` أو الحجم المسمّى، ثمّ `resource-budget.sh`. يُرفض الخادم الصغير هنا، **قبل** أيّ سحب.
5. **الصور:** يُسحب الغائب وحده (لا `docker compose pull`، فلا يتحرّك وسمٌ لطرفٍ ثالث بين تثبيتين)، ثمّ يُتحقَّق أنّ ما سُحب هو ما في `VERSION`.
6. **أوّل إقلاع بترتيبٍ ثابت:** `vault` ثمّ `vault-bootstrap`، ثمّ **سكّ `secret_id` من داخل حاوية `vault`** وكتابته في `.env`، ثمّ `minio-bootstrap` و`migrate`، ثمّ كلمة سرّ دور `metrics_exporter` في Postgres، ثمّ `ollama-pull` (فشله يوقف التثبيت: دونه تردّ المحادثة 502)، ثمّ الباقي. لا تُنشأ خدمةٌ تحمل المفتاح قبل أن يوجد.
7. **الفحوص:** `/health` و`/health/ready` على المنفذ الذي نشره Compose فعلاً، ثمّ `stack_smoke.py`.
8. **التقرير** على الشاشة فقط: الرابط، والحجم، والكرت، والمراقبة، وانتهاء `secret_id`، وتذكير بنسخ حجم `vault-init` خارج الخادم (6.4)، وتذكير بصلاحيّة `.env`، وحال الشهادة (§4).

### 3.5 رموز الخروج

تطابق رأس `install.sh`، ويفحص ذلك اختبار.

| الرمز | المعنى |
|---|---|
| 0 | `READY`: `/health` و`/health/ready` بـ200 و`stack_smoke.py` ناجح |
| 2 | استعمالٌ خاطئ: خيارٌ غير معروف أو مكرّر، أو `--domain` أو `--firebase-project` ناقص أو بصيغةٍ خاطئة، أو `--size` غير معروف |
| 3 | ليست حزمةً مفكوكة (لا `VERSION`، أو نسخةٌ من المستودع، أو `VERSION` تالف)، أو رابطٌ رمزيّ أو ملكيّةٌ غير root، أو `install.sh` آخر يعمل في المجلّد |
| 4 | ليس root: شغّل `sudo ./install.sh ...` |
| 10 | `gpu-preflight.sh` أوقف التثبيت (يُطبع رمزه) |
| 11 | أداةٌ ناقصة: bash 4 أو Python 3.10 أو PyYAML أو `curl` أو `timeout` أو `flock` أو `df` |
| 12 | مساحة Docker الحرّة أقلّ من الحدّ (التثبيت الأوّل) |
| 13 | السجلّ رفض الصور: لا `docker login`، أو مفتاحٌ بلا صلاحية |
| 14 | السجلّ لا يُبلَغ: مهلة أو DNS أو TLS أو 5xx |
| 20 | ضبط النواة فشل |
| 30 | `.env` مرفوض: مفتاحٌ يخالف المدخلات، أو كلمة سرّ ما زالت `change-me`، أو كتلةٌ تالفة |
| 40 | الحجم مرفوض: الخادم أصغر من `small` أو فوق الميزانية |
| 41 | أداة الحجم فشلت بخطأ |
| 50 | سحب صورة فشل أو انقضت مهلته |
| 51 | صورة لا تطابق `VERSION` (القائمة أو البصمة)، أو وسم `dev` أو `latest` |
| 60 | خطوة إقلاعٍ فشلت أو انقضت مهلتها (الرسالة تسمّي الخطوة والخدمة وأمر `docker compose logs <service>`) |
| 61 | `VAULT_SECRET_ID`: فشل السكّ، أو الناتج ليس UUID، أو القيمة في `.env` تالفة |
| 70 | `/health` أو `/health/ready` لم يردّ 200 |
| 71 | `stack_smoke.py` فشل |

والرمز 1 خطأٌ غير متوقّع داخل السكربت نفسه.

### 3.6 صلاحيّة `.env`

`install.sh` **لا يضبط صلاحيّة `.env`** (قرار المالك، Q‑4). يُنشأ الملفّ بصلاحيّة `umask` (0644 تحت `umask 022`)، ويُترك ملفٌّ قائمٌ على صلاحيّته ومالكه. فيه كلمات السرّ الثلاث عشرة و`VAULT_SECRET_ID`، فاضبطه بيدك بعد كلّ تثبيت:

```bash
chmod 600 .env
```

يذكّر به سطرٌ واحد في تقرير كلّ تشغيل، ويحذّر منه `auto_size.py` (SR‑11). وهو ثغرة معروفة (ث‑10 وث‑11).

### 3.7 التكرار وما يرفضه

التشغيل ثانيةً بالمدخلات نفسها آمن: لا كلمة سرّ جديدة، ولا `secret_id` ثانٍ، ولا حاويةٌ تُعاد، وإن كان المكدّس سليماً فلا يُنادى `up` أصلاً. وإن توقّف تثبيتٌ في منتصفه (فشل `migrate` مثلاً) فالتشغيل التالي يكمل من حيث وقف.

ويرفض `install.sh` (الرمز 30، ويسمّي المفتاح، ولا يغيّر بايتاً) أن يُشغَّل بمدخلاتٍ تخالف ما ثُبِّت: نطاقٌ آخر، أو مشروع Firebase آخر، أو وضعٌ آخر (`--cpu`)، أو المراقبة، أو حجمٌ آخر، أو كلمة سرّ صارت فارغة أو `change-me`. أمّا الانتقال إلى إصدارٍ آخر فليس من عمله: هو `upgrade.sh` (5.3).

**لا تشغّل `docker compose up` بيدك قبل أن ينتهي `install.sh`**: خدمةٌ تحمل المفتاح تُنشأ قبل أن يُكتب تدخل حلقة انهيارٍ تقفل دور Vault (`08-local-runbook.md` §3.1). وبعد التثبيت شغّل كلّ أوامر Compose بـ`sudo` ومن هذا المجلّد.

### 3.8 ما لا يفعله

لا يثبّت Docker ولا التعريف (§1: يفحصهما فقط). ولا يعالج ما تتكفّل به خطوات المرحلة 6: روابط الملفّات العامّة (6.2)، والمنافذ وجدار الحماية (6.3)، ونسخ حجم `vault-init` خارج الخادم (6.4)، وتجديد `secret_id` قبل انقضائه (6.5: ينتهي بعد 720 ساعة من سكّه، والتقرير يطبع التاريخ). ولا يصنع الشهادة الحقيقيّة، فتضعها بخطوات §4 (والتقرير يقول حالها). ولا يأخذ مفتاح السجلّ، ولا يضبط صلاحيّة `.env`، ولا يحدّث ولا يرجع (5.3)، ولا يحذف ملفّاً ولا حجماً.

---

## 4) الشهادة عبر Cloudflare (6.1)

الطريقة في أربعة أسطر:
- اسم الـAPI `api.aizzak.com` يمرّ عبر Cloudflare (سجلّ DNS مُمرَّر)، فيرى الزائر شهادة Universal SSL العامّة.
- Cloudflare يصل الأصل على 443 بوضع **Full (strict)** لهذا الاسم وحده، فيتحقّق من شهادة الأصل.
- شهادة الأصل من **Cloudflare Origin CA** لهذا الاسم وحده، ومدّتها 15 سنة، فلا تجديد. ومفتاحها ECDSA P‑256 يولَّد على الخادم ولا يخرج منه.
- nginx يأخذ عنوان العميل من `CF-Connecting-IP` لنطاقات Cloudflare وحدها (§4.5).

التثبيت بـ`--domain api.aizzak.com`. وجهاز التطوير لا يتغيّر: يبقى على الشهادة الذاتيّة و`localhost`. واسمٌ أعمق من المستوى الأوّل (مثل `api.prod.aizzak.com`) لا تغطّيه Universal SSL المجّانيّة.

### 4.1 إعدادات المنطقة

| الإعداد | القيمة | النطاق | من ومتى |
|---|---|---|---|
| قاعدة Cache Rules: تجاوز التخزين | Bypass cache | `api.aizzak.com` وحده | لم يُطبَّق بعد |
| قاعدة Single Redirect: HTTP إلى HTTPS | 308، مع الاستعلام | `api.aizzak.com` وحده | لم يُطبَّق بعد |
| قاعدة Configuration Rules: SSL | Full (strict) | `api.aizzak.com` وحده | لم يُطبَّق بعد |
| أدنى إصدار TLS | 1.2 | المنطقة كلّها | لم يُطبَّق بعد |
| سجلّ DNS من النوع A، مُمرَّر | عنوان الخادم | `api.aizzak.com` | 7.3 |

كلّ تغييرٍ في Cloudflare بموافقة المالك عليه بعينه، وقبله صورة وبعده صورة.

**توكن Cloudflare API** (لمن ينفّذ التغييرات بالـAPI لا باللوحة):
- Custom Token لا Global API Key، لمنطقة `aizzak.com` وحدها، وبالصلاحيّات التي يحتاجها هذا الدليل وحدها: قراءة المنطقة، وتعديل DNS، وتعديل إعدادات المنطقة، وتعديل قواعد Configuration وRedirect وCache، و«SSL and Certificates» لإصدار شهادة المنشأ وإبطالها، وقراءة Bot Management.
- له تاريخ انتهاء بعد 7.3، ويُقيَّد بتصفية عنوان العميل (Client IP Address Filtering) على جهاز المشغّل.
- توكنٌ آخر للقراءة فقط لأخذ اللقطات (`snap`).
- يُبطَل التوكنان بعد 7.3.
- لا يظهر التوكن في سطر أوامر `curl` (فيُرى في `ps`)؛ يُمرَّر من ملفٍّ بصلاحيّة 0600 بالخيار `-H @<ملف>`، أو من stdin بالخيار `--config -`. ولا يُكتب في ملفّ داخل المستودع.

**لا يُلمس:** وضع `ssl` للمنطقة (يبقى `full`)، و`always_use_https` (مطفأ)، وHSTS المنطقة (مطفأ)، وUniversal SSL (مفعّل)، والجذر `aizzak.com` و`chat.` و`notify.` والبريد. وإن غيّر أحدٌ `ssl` إلى `flexible` فيرجع الاسم يتكلّم نصّاً صريحاً مع الأصل: افحصه بـ`GET /zones/<id>/settings/ssl` وأعده إلى `full`.

### 4.2 شهادة المنشأ

الأوامر من مجلّد الحزمة المفكوكة وبـ`sudo`. لا يُطبع المفتاح ولا يخرج من الخادم:

1. المفتاح وطلب التوقيع على الخادم:
   ```bash
   sudo sh -c 'umask 077 && openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
     -keyout deploy/nginx/certs/origin.key -out deploy/nginx/certs/origin.csr -subj "/CN=api.aizzak.com"'
   sudo cat deploy/nginx/certs/origin.csr
   ```
   وإن غاب `openssl` عن المضيف: الأمر نفسه داخل `sudo docker run --rm --network none` على صورة `nginx:1.27` بمجلّد الشهادات مربوطاً.
2. في لوحة Cloudflare: SSL/TLS ← Origin Server ← Create Certificate ← «Use my private key and CSR» ← الصق الطلب ← احذف `*.aizzak.com` و`aizzak.com` من الأسماء واترك `api.aizzak.com` وحده ← 15 years ← Create. الطريق البديل (مفتاحٌ يولّده Cloudflare) أضعف، لأنّ المفتاح يمرّ بالمتصفّح.
3. ضع الشهادة (PEM عامّ) في `deploy/nginx/certs/origin.crt` ثمّ `sudo chmod 644` لها. **سجّل معرّف الشهادة** الظاهر في اللوحة عند الإصدار (أو `.result.id` إن صدرت بالـAPI): خطوة الإبطال في §4.3 تحتاجه.
4. تحقّق قبل التفعيل (تُطبع البصمات وحدها). الأمر في صدفةٍ فرعيّة حتّى لا تبقى صدفتك داخل مجلّد الشهادات:
   ```bash
   ( cd deploy/nginx/certs &&
     sudo openssl x509 -in origin.crt -noout -ext subjectAltName -issuer -enddate &&
     sudo openssl x509 -in origin.crt -noout -checkend 31536000 &&
     sudo openssl x509 -in origin.crt -noout -pubkey | openssl pkey -pubin -outform DER | sha256sum &&
     sudo openssl pkey -in origin.key -pubout -outform DER | sha256sum )
   ```
   البصمتان الأخيرتان متساويتان، وSAN فيه `api.aizzak.com` وحده، و`-checkend` يخرج بـ0 (الصلاحيّة سنةً على الأقلّ). وإن فشل أيٌّ منها فلا تفعّل.
5. التفعيل: بالاسمين `server.crt` و`server.key` يرى nginx الملفّين الجديدين بلا إعادة إنشاء الحاوية (المجلّد مربوطٌ مجلّداً). الأسطر مسلسلةٌ بـ`&&` فلا `reload` بعد `mv` فاشل، وتُنسخ الشهادة العاملة إلى `*.prev` قبل أن تُستبدل:
   ```bash
   sudo cp -p deploy/nginx/certs/server.crt deploy/nginx/certs/server.crt.prev &&
     sudo cp -p deploy/nginx/certs/server.key deploy/nginx/certs/server.key.prev &&
     sudo mv -f deploy/nginx/certs/origin.key deploy/nginx/certs/server.key &&
     sudo mv -f deploy/nginx/certs/origin.crt deploy/nginx/certs/server.crt &&
     sudo docker compose exec nginx nginx -t && sudo docker compose exec nginx nginx -s reload
   ```
   وإن فشل `nginx -t` فلا `reload`: أعد الزوج العاملة (`sudo mv -f deploy/nginx/certs/server.crt.prev deploy/nginx/certs/server.crt` ومثله للمفتاح) ثمّ طبّق الرجوع (§4.4) إن لزم.
6. التحقّق المحلّيّ: `openssl s_client -connect 127.0.0.1:443 -servername api.aizzak.com -CAfile <جذر Origin CA ECC العامّ من وثائق Cloudflare> </dev/null 2>/dev/null | grep 'Verify return code'` يطبع `0 (ok)`. ثمّ من جهازٍ خارج الخادم: `curl -fsS https://api.aizzak.com/health`.

التقرير يطبع سطر `TLS:` في كلّ تثبيت:

| السطر | المعنى |
|---|---|
| `self-signed development certificate` | ما زالت الشهادة الذاتيّة: اتّبع الخطوات أعلاه |
| `certificate for <النطاق>, issuer …, expires …` | شهادةٌ تغطّي النطاق. ومعها «trusted by Cloudflare only» إن كانت من Origin CA: أبقِ سجلّ DNS مُمرَّراً |
| `WARNING: the certificate does not cover …` | الشهادة لاسمٍ آخر |
| `WARNING: the certificate expired …` | انتهت صلاحيّتها |
| `WARNING: deploy/nginx/certs/server.crt is a symbolic link; not read` | `server.crt` رابطٌ رمزيّ: لا يقرؤه التقرير (احتراز). ضع الملفّ نفسه |
| `WARNING: deploy/nginx/certs/server.crt could not be read as a certificate` | الملفّ ليس شهادة PEM مقروءة: أعد نسخ الشهادة |
| `no certificate yet` / `not checked` | لا شهادة بعد، أو لا `openssl` على المضيف |

لا يتغيّر رمز الخروج في أيّ حالة. وإن وُجد `server.crt` وحده أو `server.key` وحده فخدمة `nginx-certs` تخرج بـ1 ولا تكتب شيئاً (§4.8).

### 4.3 التدوير

1. سبرٌ خارجيّ كلّ ثانية: `while :; do curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 https://api.aizzak.com/health; sleep 1; done | tee probe.log`.
2. سجّل معرّف الحاوية ووقت بدئها قبل التمرين: `docker inspect -f '{{.Id}} {{.State.StartedAt}}' $(sudo docker compose ps -q nginx)`.
3. مفتاحٌ وطلبٌ ثانيان (`origin.key` و`origin.csr` من جديد)، وشهادةٌ ثانية، ثمّ الخطوتان 4 و5 من §4.2 (والخطوة 5 تنسخ الزوج العاملة إلى `*.prev` قبل الاستبدال، فاحتفظ بها إلى أن يثبت التدوير).
4. البصمة الجديدة في `s_client`، و`docker inspect` بعد التمرين بالقيمتين نفسيهما (لم تُعَد الحاوية).
5. أبطل الشهادة الأولى من لوحة Cloudflare (Revoke) أو بـ`DELETE /certificates/<id>` بمعرّفها الذي سجّلته عند إصدارها (§4.2 الخطوة 3). وبالـAPI يلزم التوكن صلاحيّة «SSL and Certificates» (§4.1).
6. `grep -vc '^200$' probe.log` يطبع 0.

### 4.4 الرجوع

```bash
sudo mkdir -p deploy/nginx/certs/aside
sudo mv deploy/nginx/certs/server.crt deploy/nginx/certs/server.key deploy/nginx/certs/aside/
sudo docker compose up -d nginx-certs
sudo docker compose exec nginx nginx -t && sudo docker compose exec nginx nginx -s reload
```

فيعود الاسم يردّ **526** بقصد (strict يرفض الشهادة الذاتيّة). وإخراج الاسم من الخدمة كلّيّاً بحذف سجلّ DNS.

### 4.5 عنوان العميل وقائمة نطاقات Cloudflare

كتلة `realip` في `nginx.conf` تضع في `$remote_addr` قيمة `CF-Connecting-IP` لطلبٍ نظيره من نطاقات Cloudflare المنشورة وحدها. فيصير مفتاح حدّ المعدّل وحدّ الاتّصالات والسجلّ و`X-Real-IP` هو العميل لا حافّة Cloudflare. أيّ نظيرٍ آخر (الجهاز المحلّيّ، وفحوص `install.sh`، وعميلٌ يصل الأصل مباشرة) يبقى بعنوانه. ولا يُقرأ `X-Forwarded-For`. وطلبات Cloudflare Workers تصل بعنوان خروج الـWorker فتتقاسم حدّاً واحداً.

حدّ الثقة هنا **كلّ عملاء Cloudflare** لا عميلنا وحده: طلبٌ من Worker في منطقةٍ أخرى يصل من نطاقات Cloudflare فيُقبل منه `CF-Connecting-IP` الذي يختاره. أثر ذلك على مفتاح حدّ المعدّل لكلّ عنوان وعلى سجلّ الوصول. **فلا يُبنى قبل 6.3 قرارُ `allow` أو `deny` أو تفويضٍ على `$remote_addr`.**

القائمة **نسخة** من `https://api.cloudflare.com/client/v4/ips` بتاريخها و`etag` في تعليق `nginx.conf`. المقارنة يوم الدمج:

```bash
curl -sS https://api.cloudflare.com/client/v4/ips | jq -r '.result.ipv4_cidrs[], .result.ipv6_cidrs[]' | sort > ips-api.txt
sed -n 's/^[[:space:]]*set_real_ip_from[[:space:]]\+\([^;]*\);.*/\1/p' deploy/nginx/nginx.conf | sort > ips-conf.txt
diff ips-api.txt ips-conf.txt && echo SAME
```

التحديث: في المستودع أوّلاً (والتاريخ في التعليق). ثمّ على الخادم حتّى يوجد `upgrade.sh`: انسخ `nginx.conf`، وافحصه في حاويةٍ مؤقّتة (`sudo docker run --rm --network none -v <المسار>/nginx.conf:/etc/nginx/nginx.conf:ro -v <المسار>/app-locations.conf:/etc/nginx/app-locations.conf:ro -v <المسار>/certs:/etc/nginx/certs:ro nginx:1.27 nginx -t`)، ثمّ **`sudo docker compose restart nginx`**. فـ`nginx.conf` مربوطٌ ملفّاً، و`nginx -s reload` بعد استبدال الملفّ يعيد تحميل النصّ القديم. ونطاقٌ جديد غير مدرج يجعل عملاءه يتقاسمون حدّاً واحداً حتّى التحديث.

### 4.6 HSTS

السطر في خريطة `nginx.conf`: `"api.aizzak.com" "max-age=31536000"`. **بلا `includeSubDomains` ولا `preload`:** منطقة `aizzak.com` تحمل موقع المالك في الجذر و`chat.` و`notify.` والبريد، وأيّ من الخيارين يفرض HTTPS على أسماءٍ لا يخدمها هذا المكدّس. والترويسة لا تبلغ متصفّحاً قبل أن يوجد سجلّ DNS للاسم (7.3، بعد ثبوت الشهادة)، وهذا معنى «بعد ثبوت الشهادة». والرجوع عنها `max-age=0`، وأثرها باقٍ في كلّ متصفّحٍ رآها حتّى يعود إلى الموقع. وخادمٌ مثبّتٌ بـ`--domain` آخر لا يأخذ HSTS من هذه الخريطة.

### 4.7 حدود Cloudflare

- جسم الطلب 100 م.ب كحدٍّ أقصى (والـAPI نفسه 2 م.ب، والملفّات حتّى 50 م.ب برابطٍ موقّع).
- ردٌّ غير مبثوث يتجاوز 100 ثانية يصل العميلَ بالرمز `524` والعمل جارٍ على الخادم. والجواب المجمَّع لسير عملٍ طويل (`runWorkflow`) معرّضٌ لذلك، فيُنصح بالبثّ SSE (`text/event-stream`).
- WebSocket: على العميل إرسال `ping` كلّ 30 ثانية حتّى يُقاس الحدّ في 7.3.
- التخزين بحسب الامتداد، وقاعدة التجاوز (§4.1) تحسمه لاسم الـAPI.
- Bot Fight Mode وBrowser Integrity Check و«Under Attack» قد تتحدّى عميل API.
- أصلٌ للمتصفّح غير مدرجٍ في قائمة CORS يُرفض، وإضافة أصلٍ جديد تعديلٌ منفصل.

### 4.8 جدول الأعطال

| الرمز | المعنى | الفحص | العلاج |
|---|---|---|---|
| `521` | الأصل لا يجيب على 443، أو nginx متوقّف | `sudo docker compose ps nginx` ثمّ `curl -k https://127.0.0.1/health` على الخادم | شغّل nginx، وافتح 443 للخادم |
| `522` | مهلة الاتّصال بالأصل | جدار حماية الخادم أو مزوّده | اسمح لنطاقات Cloudflare بالوصول إلى المنفذ 443 |
| `524` | الردّ تجاوز 100 ثانية | زمن الطلب في سجلّ nginx | بثّ SSE بدل الجواب المجمَّع |
| `525` | فشل مصافحة TLS مع الأصل | `sudo docker compose logs nginx` | تحقّق من الشهادة والمفتاح (§4.2) |
| `526` | شهادة الأصل ذاتيّة أو لاسمٍ آخر أو منتهية | سطر `TLS:` في تقرير `install.sh` | ضع شهادة Origin CA صالحة (§4.2) |
| `nginx-certs` خرج بـ1 | نصف زوجٍ في `deploy/nginx/certs` | رسالته على stderr تسمّي الناقص | ضع الملفّ الناقص، أو انقل الموجود جانباً |

---

## قائمة الأمان المؤجّلة

ثغراتٌ معروفة تُقبل مؤقّتاً وتُعالَج لاحقاً. القائمة **فارغة** عند البدء (قرار المالك). أضف إليها كلّ ثغرةٍ تجدها على الخادم، برقمٍ جديد (`ث‑N`، ث = ثغرة) لا يُعاد استعماله.

| # | الثغرة | الأثر | العلاج المقترح |
|---|---|---|---|
| ث‑1 | فهرسا حزمٍ بلا بصمات: `services/embedding/requirements-cuda.txt` (ومثله `requirements.txt`) يستعمل `--extra-index-url` بلا `--require-hashes`، والحزم التابعة غير مثبّتة (المراجعة الأمنيّة لـ`gpu-compose`، S‑2) | خلط الاعتماديّات: اسمٌ موجود في فهرسٍ واحد قد يُسجَّل في الآخر بنسخةٍ أعلى (CWE‑494، CWE‑1357) | ملفّ قفلٍ ببصمات (`pip-compile --generate-hashes`) يُثبَّت بـ`--require-hashes`، للملفّين معاً |
| ث‑2 | `torch==2.5.1` متأثّرة بـCVE‑2025‑32434 (‏`torch.load`، أُصلحت في 2.6) (S‑3) | صعب الاستغلال هنا: النموذج مخبوزٌ في الصورة عند البناء، والتشغيل بلا شبكة | `torch` ‏≥ 2.6، ثمّ إعادة `tests/eval/vector_parity.py` |
| ث‑3 | صورٌ بوسومٍ قابلةٍ للتغيّر: بصمة `ollama/ollama:0.34.4` تعليقٌ فقط في `docker-compose.yml`، وكذلك `python:3.12-slim` لـ`ollama-pull` و`SMOKE_IMAGE` في `deploy/gpu-preflight.sh` (S‑4) | وسمٌ يُعاد نشره يغيّر ما يعمل دون تغييرٍ في المستودع | إلحاق `@sha256:…` بكلٍّ منها |
| ث‑4 | `ollama` يعمل بصلاحيّات root، وواجهته بلا مصادقة على شبكة `default` المشتركة، وبلا `cap_drop`؛ و`ollama-pull` بلا `cap_drop: ["ALL"]` و`no-new-privileges` (S‑5، S‑6 ب) | أيّ خدمةٍ مخترَقة على الشبكة تصل إلى Ollama | شبكة `llm` خاصّة، و`cap_drop`، و`no-new-privileges` (الموجة 8) |
| ث‑5 | حجز `utility` في `docker-compose.gpu.yml` يُظهر عُقد أجهزة NVIDIA داخل `app` المكشوف للإنترنت (S‑6 أ) | سطحٌ إضافيّ داخل الخدمة الأكثر تعرّضاً | إزالته حين لا يُحتاج فحص الكرت من داخل `app` |
| ث‑6 | صورة طرفٍ ثالث لمصدّر الكرت: `utkuozdemir/nvidia_gpu_exporter:1.15.1`، مثبّتةً بالبصمة `sha256:7aee2d42…3d087` (مشرفٌ فرد، 14 إصداراً بين 2026‑06‑22 و2026‑09‑02)، بحجز `utility` (عُقد NVIDIA ومكتبة NVML داخلها). لم يُتحقَّق من توقيع cosign (monitoring-profile، Q‑4) | البصمة تمنع تغيّر ما يعمل بلا تعديلٍ في المستودع، لكنّ كلّ تحديثٍ لها ثقةٌ بما نشره المشرف. والمنفذ الداخليّ يكشف هويّة الكرت (UUID، الرقم التسلسليّ، عنوان PCI) لـPrometheus وحده، جاره الوحيد على `gpu-metrics` | **قبل كلّ تحديثٍ للبصمة:** (1) `docker buildx imagetools inspect` للوسم الجديد على `docker.io/utkuozdemir/nvidia_gpu_exporter` وعلى `ghcr.io/utkuozdemir/nvidia_gpu_exporter`، والبصمة واحدة في الاثنين. (2) `cosign verify ghcr.io/utkuozdemir/nvidia_gpu_exporter@sha256:<بصمة الفهرس> --certificate-identity 'https://github.com/utkuozdemir/nvidia_gpu_exporter/.github/workflows/release.yml@refs/tags/v<X.Y.Z>' --certificate-oidc-issuer https://token.actions.githubusercontent.com` (التوقيع على GHCR وحده، keyless من workflow الإصدار لدى المنبع؛ Docker Hub بلا توقيع، فلا يُفحص هناك. بـcosign v3، وإن لم يجد التوقيع فبـ`--new-bundle-format`). ⚠️ **لم يُشغَّل هذا الأمر بعد** (cosign غير مثبّت على هذا الجهاز): يشغّله المالك أو devops مرّةً على 1.15.1 ويسجّل نتيجته هنا. (3) إعداد الصورة: `User=65534:65534`، و**رفض أيّ صورةٍ يضبط إعدادها `LD_PRELOAD` أو `LD_LIBRARY_PATH`** (CVE‑2025‑23266). أو نسخ الصورة إلى `anastawahia/aizzak` بوسمٍ ببادئة. أو `dcgm-exporter` الرسميّ بعد 7.3 إن كان كرت الخادم من فئة مراكز البيانات |
| ث‑7 | شبكة `default` واحدة مسطّحة وغير `internal` لكلّ خدمات المكدّس، وعليها بلا مصادقة: خادما Redis، وQdrant، وLoki، وAlertmanager، و`/-/quit` في Prometheus، و`/health/drain` في `app`، وOllama (SR‑1، monitoring-profile). **عُولج جزئيّاً:** مصدّر الكرت (صورة طرفٍ ثالث) صار على شبكة `gpu-metrics` ‏`internal: true` وحده، مع Prometheus. **وما بقي للمصدّر:** يصل إلى `prometheus:9090`: واجهة الاستعلام مفتوحة له دائماً (لا تتعلّق بالـlifecycle)، و`/-/quit` و`/-/reload` متاحان لأنّ Prometheus يشغَّل بـ`--web.enable-lifecycle` (قرار المالك 2026‑10‑18: يبقى، لأنّ إجراء تعديل العتبات في `docker-compose.yml` يعتمد عليه). **الأثر من المصدّر:** حلقة `POST /-/quit` توقف تقييم كلّ التنبيهات، والتسليم محلّيّ فلا يُلاحَظ غياب `AizzakWatchdog` (SR‑16). **العلاج (الموجة 8):** `docker compose kill -s SIGHUP prometheus` بدل `POST /-/reload` ثمّ إزالة `--web.enable-lifecycle`؛ أو `--web.config.file` بمصادقة، ويحتاج حينها مصدر بيانات Grafana والفحص الصحّيّ اعتماداً. وحدود الكشط في `scrape-gpu.yml` تحدّ الإغراق (SR‑9)، وقاعدة `keep` على اسم المقياس تمنع المصدّر من حقن سلاسل `aizzak_*` و`pgbouncer_*` التي تقرؤها القواعد بلا مرشّح `job` (SR‑15؛ في 7.3: `scrape_samples_post_metric_relabeling` يساوي `scrape_samples_scraped` للمهمّة `nvidia-gpu`). **وعزل `gpu-metrics` يتطلّب Docker Engine ≥ 26.0.0** (CVE‑2024‑29018: خروج DNS من الشبكات الداخليّة)، ويفرضه `gpu-preflight.sh` بالرمز 11 (SR‑10) | ما بقي: أيّ خدمةٍ أخرى مخترَقة على `default` تقرأ بيانات كلّ المستأجرين من Redis وQdrant، وتُسكت التنبيهات، وتُخرج البيانات | الموجة 8: شبكةٌ لكلّ طبقة، و`requirepass` لـRedis، ومفتاح API لـQdrant |
| ث‑8 | حدّ `nvidia-ctk` الأدنى (1.17.8) في `deploy/gpu-preflight.sh` يلزمه أن يُراجَع: خطّافات الأداة تعمل root على المضيف بجذر الصورة وبيئتها، وصورتا طرفٍ ثالث تحجزان الكرت: `ollama/ollama` والمصدّر (SR‑2). **عُولج:** السكربت يفشل بـ12 تحت الحدّ | حدٌّ قديم يفوّت ثغراتٍ لاحقة في الأداة (CVE‑2024‑0132، CVE‑2025‑23359، CVE‑2025‑23266 وأمثالها) | مراجعة `MIN_NVIDIA_CTK` مع نشرات NVIDIA الأمنيّة في 7.3، ورفض أيّ صورةٍ يضبط إعدادها `LD_PRELOAD` عند تحديث البصمة (ث‑6). **ويُعاد `deploy/gpu-preflight.sh` بعد كلّ تحديثٍ لحزم NVIDIA**، فالفحص يجري مرّةً عند التجهيز ولا شيء في `up` يعيده (SR‑13) |
| ث‑9 | المصدّر يشغّل `nvidia-smi` مع كلّ طلب، حتّى 40 بالتوازي افتراضيّاً، ولأيّ جارٍ على شبكته (SR‑4). **عُولج:** `--collect.interval=15s` و`--web.max-requests=2`، والشبكة `gpu-metrics` (ث‑7) تحصر الجيران في Prometheus | ذروة ذاكرة المصدّر أُعيد قياسها محلّيّاً بالخيارين: 56.2 م.ب (54.1 دونهما)، و× 1.5 فوق الحدّ `64m`، فهو خطرٌ قبله المالك (BUG‑1، SR‑12) | إعادة قياس الذروة على الخادم في 7.3 |
| ث‑10 | `VAULT_SECRET_ID` محفوظٌ في `.env` على الخادم: `install.sh` يسكّه من داخل حاوية `vault` ويكتبه فيه (قرار المالك 2026‑10‑19، Q‑4). كان `05 §3.3` يمنع ذلك، وعُدّل النصّ | من يقرأ `.env` يملك `secret_id` صالحاً 720 ساعة، فيصادق على Vault بدور `app` (قراءة `secret/data/*` وتشفير Transit) إلى أن ينقضي أو يُبطَل | ضبط صلاحيّة `.env` (ث‑11). وبعد 6.5 يُجدَّد المفتاح قبل انقضائه فتقصر نافذة الأثر. ولا يُطبع المفتاح ولا يُسجَّل |
| ث‑11 | `.env` على الخادم يبقى على صلاحيّة `umask` (0644 غالباً): فيه كلمات السرّ الثلاث عشرة (Postgres وأدواره الثمانية، و`metrics_exporter`، وMinIO، وGrafana) و`VAULT_SECRET_ID`. قرار المالك (2026‑10‑19): `install.sh` لا يضبط الصلاحيّة، ويضبطها المالك بيده | أيّ مستخدمٍ على الخادم يقرأ الملفّ كلّه | `chmod 600 .env` بعد كلّ تثبيت. يذكّر به التقرير في كلّ تشغيل، ويحذّر منه `auto_size.py` (SR‑11) |
| ث‑12 | حزمة التثبيت بلا توقيع: يُطبع sha256 عند صنعها ويقارنه المشغّل باليد | من يبدّل الحزمة في الطريق، أو يخدع المشغّل بحزمةٍ أخرى، يشغّل ما يشاء بصلاحيّة root عبر `sudo ./install.sh` | توقيع الحزمة (cosign أو GPG) ونشر المفتاح العامّ، في الموجة 8 |
| ث‑13 | Cloudflare ينهي TLS ويرى النصّ الصريح: ترويسة `Authorization`، وتوكن WebSocket في الاستعلام (`?token=`، `03-api-spec.md:289`)، والأسئلة والأجوبة، ومحتوى الملفّات في 6.2. قبله المالك لمرحلة staging (Q‑9، 2026‑10‑20) | طرفٌ ثالث يرى كلّ حركة الـAPI | يُعاد النظر في الموجة 8 |
| ث‑14 | الأصل يُبلَغ مباشرةً على 80 و443 متجاوزاً Cloudflare، حتّى 6.3 أو Authenticated Origin Pulls | تجاوز حماية Cloudflare وحدوده. وحدّ الثقة في `realip` هو كلّ عملاء Cloudflare: طلبٌ من Worker في منطقةٍ أخرى يختار `CF-Connecting-IP`، فيتأثّر مفتاح حدّ المعدّل لكلّ عنوان وسجلّ الوصول (لا قرار تفويض مبنيّ على العنوان، §4.5) | 6.3: حصر 80 و443 بنطاقات Cloudflare. وAuthenticated Origin Pulls معه (Q‑1) |
| ث‑15 | مفتاح المنشأ `deploy/nginx/certs/server.key` ملفٌّ على القرص خارج Vault، صلاحيّته 0600 لـroot (Q‑8) | من يملك root أو مجموعة `docker` على الخادم يقرؤه، فينتحل الأصل أمام Cloudflare | إبطال الشهادة في Cloudflare وإصدار أخرى (§4.3). وتشفير القرص ومراجعة الموجة 8 |

</div>
