<div dir="rtl">

# تشغيل AIZZAK على خادمٍ بكرت NVIDIA

> **ما هذه الوثيقة؟** هيكلٌ أوّل لدليل تشغيل الخادم. فيه الآن **قسم المتطلّبات** (الخطّة [`server-gpu-plan.md`](server-gpu-plan.md) الخطوة 2.1)، وقسم المراقبة والتنبيهات (4.1)، وقائمة الأمان المؤجّلة (بدأت فارغة). يكمله دليل التشغيل الكامل في الخطوة 7.4 بعد تجربة الخادم الحقيقيّ (7.3)، ويستدعي `install.sh` (5.2) سكربت الفحص أدناه.
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
| NVIDIA Container Toolkit، مضبوطةً لـDocker | `docker info` يعرض وقت التشغيل `nvidia` | [دليل التثبيت الرسميّ](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) (فيه `apt` و`dnf`) |
| Docker Engine وCompose plugin بإصدار 2.20 فما فوق | `docker compose version` | [Docker Engine](https://docs.docker.com/engine/install/)، و[Compose plugin](https://docs.docker.com/compose/install/linux/) |

### 1.1 تعريف NVIDIA

- يُثبَّت على المضيف نفسه (لا داخل حاوية)، بإصدارٍ يدعم CUDA 12.4 فما فوق، لأنّ صورة التضمين على الكرت تحمل مكتبات CUDA 12.4 بنفسها، والمضيف يعطي التعريف وحده.
- على WSL يأتي التعريف من Windows ولا يُثبَّت داخل لينكس، و`nvidia-smi` يظهر تحت `/usr/lib/wsl/lib`.

### 1.2 NVIDIA Container Toolkit

بعد تثبيت الحزم من الدليل الرسميّ أعلاه، يُسجَّل وقت التشغيل في Docker ثمّ يُعاد تشغيل Docker:

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
- **مضيفٌ بـCDI أو Docker Desktop:** قد لا يظهر وقت التشغيل `nvidia` في `docker info` والكرت يعمل. نجاح `--smoke` **يغلب** غياب وقت التشغيل في هذه الحال (يخرج بـ0)، لأنّ Docker يحلّ `--gpus` وحجز Compose بمسار الـhook أو CDI لا بتسجيل وقت التشغيل. ودون `--smoke` يخرج بـ12 مع النصيحة نفسها وتلميح «جرّب `--smoke`».

**رموز الخروج** (تطابق رأس السكربت، ويفحص ذلك اختبار):

| الرمز | المعنى | ما يفعله التثبيت (5.2) |
|---|---|---|
| 0 | جاهز لـ`docker-compose.gpu.yml` | وضع الكرت |
| 2 | استعمالٌ خاطئ (وسيطٌ غير معروف) | — |
| 10 | لا كرت صالح (`nvidia-smi` غائب أو يفشل)، وDocker وCompose سليمان | وضع المعالج، دون ملفّ الكرت |
| 11 | Docker غير صالح: لا CLI، أو الخادم لا يجيب، أو `permission denied` على المقبس | توقّف |
| 12 | كرتٌ موجود، وNVIDIA Container Toolkit غير مضبوطة لـDocker (لا وقت تشغيل `nvidia`، ولا `--smoke` ناجح) | توقّف، أو وضع المعالج باختيار |
| 13 | Docker Compose غائب أو أقدم من 2.20.0 | توقّف |
| 14 | `--smoke`: الفحوص الثابتة نجحت، وحاوية الكرت فشلت (ومعها رسالة Docker، ومنها فشل السحب) | توقّف |

- **الأسبقيّة** عند أكثر من فشل: 11، ثمّ 13، ثمّ 10، ثمّ 12. أعطال المنصّة (Docker وCompose) تسبق أعطال الكرت لأنّها تمنع التشغيل على المعالج أيضاً. فالرمز 10 يعني بالضبط: «لا كرت، والتشغيل على المعالج ممكن».
- الرمز 1 خطأٌ غير متوقّع داخل السكربت نفسه.

---

## 2) المراقبة والتنبيهات

1. **مفعّلةٌ افتراضيّاً** (قرار 7): `COMPOSE_PROFILES=local-llm,monitoring`. خدمات المراقبة الإحدى عشرة (Prometheus وGrafana وAlertmanager و`alert-sink` والمصدّرات الخمسة وLoki وAlloy) خلف profile `monitoring`.
2. **الخادم الصغير وحده يحذفها.** ⚠️ **دونها لا تنبيهات أصلاً:**
   - افحص `/health` **من خارج الخادم**: خدمة رصدٍ خارجيّة تطلب `https://<النطاق>/health` كلّ دقيقة، وتنبّه على غير 200. والاختيار في 7.4.
   - و`install.sh` (5.2) سيطبع ذلك.
3. **حكم الميزانيّة دونها:** `deploy/resource-budget.sh --without-monitoring --size <الحجم>`.
4. **مع ملفّ الكرت والمراقبة:** مصدّر الكرت `nvidia-gpu-exporter`، وهدف `nvidia-gpu` في Prometheus، وتنبيها الكرت، وصفّ GPU في لوحة capacity (4.2).

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
| ث‑6 | صورة طرفٍ ثالث لمصدّر الكرت: `utkuozdemir/nvidia_gpu_exporter:1.15.1`، مثبّتةً بالبصمة `sha256:7aee2d42…3d087` (مشرفٌ فرد، 14 إصداراً بين 2026‑06‑22 و2026‑09‑02)، بحجز `utility` (عُقد NVIDIA ومكتبة NVML داخلها). لم يُتحقَّق من توقيع cosign (monitoring-profile، Q‑4) | البصمة تمنع تغيّر ما يعمل بلا تعديلٍ في المستودع، لكنّ كلّ تحديثٍ لها ثقةٌ بما نشره المشرف. والمنفذ الداخليّ يكشف هويّة الكرت (UUID، الرقم التسلسليّ، عنوان PCI) لكلّ خدمةٍ على الشبكة | التحقّق من توقيع cosign قبل كلّ تحديثٍ للبصمة، أو نسخ الصورة إلى `anastawahia/aizzak` بوسمٍ ببادئة. أو `dcgm-exporter` الرسميّ بعد 7.3 إن كان كرت الخادم من فئة مراكز البيانات |

</div>
