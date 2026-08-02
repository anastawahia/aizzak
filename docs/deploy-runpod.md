<div dir="rtl">

# نشر AIZZAK على RunPod — دليلٌ خطوةً بخطوة

> **لمن هذه الوثيقة؟** لمن لم يسبق له نشر تطبيقٍ على خادمٍ سحابيّ. كلّ أمرٍ هنا مكتوبٌ كاملاً وجاهزٌ للّصق، وكلّ حقلٍ في واجهة RunPod مذكورٌ باسمه وقيمته.
>
> **علاقتها بباقي الوثائق:** [`quickstart.md`](quickstart.md) يشرح التشغيل **المحلّيّ** بـDocker Compose، و[`design/08-local-runbook.md`](design/08-local-runbook.md) هو المرجع المُلزِم للقرارات التشغيليّة. هذه الوثيقة لا تنقض أيّاً منهما؛ هي تترجمهما إلى منصّةٍ **لا تدعم Docker Compose**، وتذكر صراحةً كلّ موضعٍ اضطُرّت فيه للانحراف ولماذا.
>
> **آخر مطابقةٍ مع الواقع: 2026‑07‑24.**

---

## 0) اقرأ هذا أوّلاً — القيد الذي يحكم كلّ ما بعده

مشروعك اليوم يُقلَع بأمرٍ واحد: `docker compose up -d`، فيُشغّل **اثنتي عشرة حاوية** مرتّبةً بـ`depends_on` وفحوص الصحّة.

**هذا الأمر لن يعمل على RunPod.** وثائق RunPod تقول ذلك حرفيّاً:

> *"Docker Compose is not supported: Runpod runs Docker for you, so you cannot spin up your own Docker instance or use Docker Compose on Pods."*

والـ Docker‑in‑Docker (تشغيل Docker داخل Docker) كان متاحاً على أجيال أقدم من الـPods وقد **سُحِب**:

> *"While our previous Kata-based pods supported Docker-in-Docker (nested containerization), this capability is no longer available."*

**السبب بلغةٍ مبسّطة:** الـ«Pod» في RunPod **هو نفسه حاوية**. أنت لا تستأجر خادماً تثبّت عليه ما تشاء، بل تستأجر حاويةً واحدةً تعمل من صورةٍ واحدة. ولأنّ لا خادم Docker بداخلها، فلا `docker` ولا `docker compose` ولا `docker build`.

### ماذا يعني ذلك عمليّاً

| ما تفعله محلّيّاً | ما يقابله على RunPod |
|---|---|
| `git clone` ثمّ `docker compose up -d` على الخادم | ⛔ مستحيل |
| اثنتا عشرة حاوية، كلٌّ بصورتها | ✅ **صورةٌ واحدة** تحوي كلّ العمليّات |
| `depends_on` + `condition: service_healthy` | ✅ **supervisord** + سكربت إقلاعٍ يفرض الترتيب |
| شبكة Docker الداخليّة (`minio:9000`) | ✅ **loopback** (`127.0.0.1:9000`) داخل الحاوية |
| `docker compose logs -f app` | ✅ سجلّ الـPod الموحَّد في واجهة RunPod |
| تخزينٌ في أحجام Docker | ✅ قرصٌ دائم على المسار `/workspace` |

### الخيارات الثلاثة، والتوصية

| # | الخيار | يعمل؟ | الكلفة | الجهد | متى تختاره |
|---|---|---|---|---|---|
| **أ** | **صورةٌ واحدة شاملة على Pod واحد** | ✅ | GPU واحد | متوسّط (الملفّات جاهزةٌ في هذا المستودع) | تريد كلّ شيءٍ على RunPod، وتريد GPU لـOllama |
| ب | تقسيم الخدمات على عدّة Pods مع Global Networking | ✅ تقنيّاً | 4–8 أضعاف | مرتفع جدّاً | لا تختره لهذا المشروع |
| ج | مكدّس Compose على خادمٍ عاديّ + Pod على RunPod لـOllama وحده | ✅ | الأرخص | الأدنى | لا تحتاج GPU للتطبيق نفسه، وتريد `docker compose` كما هو |

> **التوصية:** إن كان هدفك من RunPod هو **الـGPU** — أي تسريع نموذج Ollama — فالخيار **(ج)** أرخص وأبسط وأقلّ انحرافاً عن تصميم المشروع، وهو موصوفٌ في [§11](#11-المسار-البديل-ج--خادمٌ-عاديّ--runpod-للـgpu-وحده).
>
> أمّا إن كان المطلوب حرفيّاً **«التطبيق يعمل على RunPod»**، فالخيار **(أ)** هو الطريق الوحيد، وهو موضوع الأقسام 1–10. وقد **جُهِّزت ملفّاته كاملةً** في `deploy/runpod/` ضمن هذا المستودع.

---

## 1) ما ستحصل عليه في نهاية هذا الدليل

Pod واحدٌ على RunPod، بداخله صورةٌ واحدة تشغّل كلّ هذا:

```
┌─────────────────── RunPod Pod (حاوية واحدة) ───────────────────┐
│                                                                │
│  supervisord (المُشرِف — يبدأ كلّ شيءٍ ويعيد تشغيل ما يسقط)      │
│                                                                │
│  ┌── منشورٌ للعالم ────────────────────────────────────────┐    │
│  │  nginx        :80    ← https://<POD_ID>-80.proxy…      │    │
│  │  MinIO API    :9000  ← https://<POD_ID>-9000.proxy…    │    │
│  │  MinIO console:9001                                    │    │
│  │  sshd         :22    (منفذ TCP — للصيانة)               │    │
│  └────────────────────────────────────────────────────────┘    │
│                                                                │
│  ┌── داخليٌّ فقط، على 127.0.0.1 لا يصله أحدٌ من الخارج ────┐    │
│  │  gunicorn/app  :8000    PostgreSQL 16  :5432           │    │
│  │  Redis         :6379    Qdrant         :6333           │    │
│  │  Vault         :8200    خدمة التضمين    :8080           │    │
│  │  Ollama        :11434   ← يستخدم الـGPU                │    │
│  │  عامل memory + مُرحّل Outbox (بلا منافذ)                │    │
│  └────────────────────────────────────────────────────────┘    │
│                                                                │
│  /workspace  ← القرص الدائم: postgres · redis · minio ·        │
│                qdrant · نماذج ollama                           │
└────────────────────────────────────────────────────────────────┘
```

### الانحرافات المقصودة عن مكدّس Compose

| الانحراف | السبب |
|---|---|
| **لا PgBouncer** | تجميع المعاملات (`D‑21`) يخدم **عدّة نُسخٍ** من التطبيق على اتّصالاتٍ قليلة؛ Pod واحدٌ فيه تطبيقٌ واحدٌ بتجمّعٍ واحد، فالمُجمِّع هنا يضيف عطباً محتملاً ولا يعطي شيئاً. والنصف الآخر من `OPS‑02` — `statement_cache_size=0` — مفروضٌ في `create_engine` لكلّ محرّكٍ على أيّ حال، فلا شيء في `src/` يتغيّر |
| **لا TLS داخل الـPod** | وكيل RunPod ينهي TLS عند `https://<POD_ID>-80.proxy.runpod.net` ويمرّر HTTP عادياً. شهادةٌ ذاتيّة التوقيع بالداخل ستكون عقبةً بلا فائدة |
| **Ollama داخل الصورة** | في Compose هو على المضيف (`host.docker.internal:11434`). هنا هو **سبب استئجار الـGPU أصلاً**، فمكانه داخل الـPod |
| **بيئتان افتراضيّتان** | `/opt/venv` للتطبيق و`/opt/venv-emb` لـtorch. يحفظ الفصل الذي أُنشئ `services/embedding/requirements.txt` من أجله: torch لا يدخل رسم اعتماديّات التطبيق أبداً |
| **عامل `memory` افتراضاً** | العمّال الثلاثة صاروا موصولين بالكامل — `knowledge` في [§3.100](log/3.100.md) و`media` في [§3.104](log/3.104.md) — فلا قيمةَ تنهار لنقصِ محوّل. الافتراضيّ يبقى `memory` لأنّه **المُثبَت حاويّاً** وحده ([§3.83](log/3.83.md))، والآخران «جاهزان للتجربة» لا «معروفان جيّدان» |

---

## 2) المتطلّبات المسبقة

### 2.1 على جهازك

| المتطلّب | كيف تتحقّق | ملاحظة |
|---|---|---|
| **WSL Ubuntu‑24.04** | `wsl -d Ubuntu-24.04 -- echo ok` | نفّذ **كلّ** أوامر هذا الدليل داخل WSL. من Git‑Bash ستحصل على `Exec format error` |
| **Docker** | `docker --version` | مقيس: `29.6.1` |
| **مساحة قرصٍ حرّة ≥ 40 ج.ب** | `df -h /` | الصورة النهائيّة ≈ 9–11 ج.ب، والبناء يحتاج ضعفها مؤقّتاً |
| **إنترنت مستقرّ للرفع** | — | ستدفع ≈ 4–5 ج.ب مضغوطة إلى Docker Hub. على وصلةٍ منزليّة قد يستغرق ذلك ساعة أو أكثر |

### 2.2 حساباتٌ تحتاج إنشاءها

1. **RunPod** — [runpod.io](https://www.runpod.io) → Sign Up → اشحن رصيداً (‏10 دولارات تكفي للتجربة).
2. **Docker Hub** — [hub.docker.com](https://hub.docker.com) → Sign Up. احفظ **اسم المستخدم**؛ سيدخل في اسم الصورة.
   - أنشئ **Access Token** بدل كلمة السرّ: `Account Settings → Personal access tokens → Generate new token`، الصلاحيّة `Read & Write`. **انسخه الآن؛ لن يُعرَض ثانية.**
3. **Firebase** — [console.firebase.google.com](https://console.firebase.google.com) → مشروعٌ جديد. ما تحتاجه منه هو **Project ID** فقط (مثل `aizzak-prod-12345`)، وتجده في `⚙️ Project settings → General`.

> ⚠️ `FIREBASE_PROJECT_ID` **إلزاميّ ولا بديل عنه**: `FirebaseAuth` يفشل فشلاً سريعاً عند الإنشاء على قيمةٍ فارغة، فالعَرَض ليس «مصادقةٌ لا تعمل» بل **تطبيقٌ لا يقلع إطلاقاً**.

### 2.3 مفتاح SSH (لتشخيص الأعطال لاحقاً)

على جهازك، داخل WSL:

```bash
ls ~/.ssh/id_ed25519.pub 2>/dev/null || ssh-keygen -t ed25519 -C "aizzak-runpod" -N "" -f ~/.ssh/id_ed25519
```

ثمّ اطبع المفتاح العامّ وانسخه:

```bash
cat ~/.ssh/id_ed25519.pub
```

في RunPod: `Settings → SSH Public Keys → Add`، والصق السطر. سيحقنه RunPod في كلّ Pod عبر المتغيّر `PUBLIC_KEY`، وسكربت الإقلاع في صورتنا يضعه في `authorized_keys`.

---

## 3) الخطوة 1 — توليد كلمات السرّ

سبع كلمات سرّ. **لا تخترعها يدويّاً ولا تعِد استخدام كلمةٍ قديمة.** ولّدها بأمرٍ واحدٍ داخل WSL:

```bash
cd /home/AIZZAK && for k in POSTGRES_SUPERUSER_PASSWORD AIZZAK_OWNER_PASSWORD APP_RW_PASSWORD OUTBOX_RELAY_PASSWORD RETENTION_SWEEPER_PASSWORD METRICS_READER_PASSWORD MINIO_ROOT_PASSWORD; do echo "$k=$(openssl rand -hex 24)"; done; echo "MINIO_ROOT_USER=aizzak-minio"
```

انسخ المخرجات كاملةً إلى ملفٍّ نصّيٍّ على **جهازك** (لا في المستودع). ستلصقها في واجهة RunPod في [§5.5](#55-متغيّرات-البيئة).

> ⚠️ **لا تستخدم رمز `%` في أيّ كلمة سرّ.** `supervisord` يفسّر `%(...)s` داخل ملفّ إعداده، ورمز `%` في قيمةٍ تمرّ عبره سببٌ معروفٌ لأعطالٍ غامضة. أمر `openssl rand -hex` أعلاه يولّد أحرفاً سُداسيّةً عشريّة فقط، فهو آمنٌ بالبناء.
>
> ⚠️ **لا `VAULT_DEV_ROOT_TOKEN` بعد الآن.** Vault صار دائماً (‏[§8.1](#81--vault-دائمٌ-الآن--واقرأ-هذا-قبل-أوّل-اعتمادٍ-حقيقيّ) أدناه): لا توكن جذرٍ تختاره أنت — `start.sh` يولّد توكناً حقيقيّاً بنفسه عند أوّل إقلاعٍ (`operator init`) ويسجّله على القرص الدائم. أيّ متغيّرٍ بهذا الاسم في قالبك القديم صار **بلا أثر**؛ احذفه.

**لماذا سبع كلمات وليست واحدة؟** لأنّ الفصل بين الأدوار هو ضمانة عزل المستأجرين نفسها:

| الدور | ما يملكه |
|---|---|
| `postgres` (المستخدم الفائق) | تهيئة العنقود وإنشاء الأدوار فقط |
| `aizzak_owner` | المُهاجِر ومالك الجداول. **لا يخدم أيّ طلب** |
| `app_rw` | ما يتّصل به التطبيق والعمّال. `NOINHERIT`، بلا `BYPASSRLS`، ليس مالكاً ⇒ **سياسة RLS هي التي تحصره** في مساحة عملٍ واحدة |
| `outbox_relay` | `SELECT/UPDATE` على `platform.outbox` ولا شيء غيرها |
| `retention_sweeper` | `SELECT/DELETE` فقط على ثلاثة جداولٍ غير محدودة (`platform.outbox`/`platform.processed_events`/`platform.idempotency_keys`) — مكنسة احتفاظٍ تُشغَّل يدويّاً (`python -m app.ops.retention`)، لا خدمةً دائمة |
| `metrics_reader` | `SELECT` وحيدة على `platform.outbox` — قراءة `/metrics` (‏P1‑3)، خدمةٌ دائمة داخل `app` |
| `MinIO root` | تخزين الكائنات |

> **Vault root token:** لم يعد يُولَّد يدويّاً هنا. `deploy/vault/start.sh` يصنعه بنفسه عند أوّل إقلاعٍ (`vault operator init`) ويحفظه — مع مفتاح فكّ الختم — في `$DATA/vault-init/init.json` (‏`chmod 600`). راجع [§8.1](#81--vault-دائمٌ-الآن--واقرأ-هذا-قبل-أوّل-اعتمادٍ-حقيقيّ).

---

## 4) الخطوة 2 — بناء الصورة ورفعها

### 4.1 تحقّق أنّ الملفّات موجودة

```bash
cd /home/AIZZAK && ls -1 deploy/runpod/
```

يجب أن ترى خمسة ملفّات: `Dockerfile` · `entrypoint.sh` · `bootstrap.sh` · `supervisord.conf` · `nginx.conf`.

### 4.2 ابنِ الصورة

استبدل `YOURNAME` باسم مستخدمك في Docker Hub في كلّ أمرٍ تالٍ:

```bash
cd /home/AIZZAK && docker build --platform linux/amd64 -f deploy/runpod/Dockerfile -t YOURNAME/aizzak-runpod:v1 .
```

**تفكيك الأمر — كلّ جزءٍ منه ضروريّ:**

| الجزء | لماذا |
|---|---|
| `--platform linux/amd64` | كلّ خوادم RunPod x86‑64. لو بنيت على معالج ARM بدونه، ستُنشئ صورةً **لن تعمل**، والعطل سيظهر كـ`exec format error` بعد رفعٍ طويل |
| `-f deploy/runpod/Dockerfile` | ملفّ البناء ليس في الجذر |
| `.` الأخيرة | **سياق البناء = جذر المستودع**، لأنّ الـDockerfile ينسخ `pyproject.toml` و`src/` و`services/` و`deploy/`. لا تجعله `deploy/runpod` |

> ⏱️ **البناء الأوّل بطيء: 20–45 دقيقة.** أثقل خطوتين: تثبيت `torch` (‏≈ 900 م.ب) وتنزيل أوزان نموذج التضمين (‏≈ 470 م.ب) و**خبزها داخل الصورة**. مقابل ذلك: الحاوية بعدها لا تنادي `huggingface.co` أبداً (`HF_HUB_OFFLINE=1`)، فإقلاعها لا يعتمد على شبكةٍ خارجيّة.
>
> ✅ أُضيف `.dockerignore` في جذر المستودع خصّيصاً لهذا: بدونه يُرفَع `.venv` (‏460 م.ب) و`.git` وكلّ مخابئ الأدوات إلى محرّك Docker في **كلّ** بناء.

تحقّق من الحجم:

```bash
docker images YOURNAME/aizzak-runpod:v1
```

توقَّع `9–11GB`. هذا طبيعيّ: PostgreSQL + Redis + MinIO + Qdrant + Vault + Ollama + torch + نموذج التضمين في صورةٍ واحدة.

### 4.3 ادفع الصورة إلى Docker Hub

```bash
docker login -u YOURNAME
```

الصق **الـAccess Token** عند طلب كلمة السرّ (لا كلمة سرّ الحساب).

```bash
docker push YOURNAME/aizzak-runpod:v1
```

> ⏱️ الرفع يعادل ≈ 4–5 ج.ب مضغوطة. على وصلةٍ منزليّة قد يستغرق ساعةً أو أكثر. إن انقطع، أعد الأمر نفسه — Docker يستأنف الطبقات المرفوعة.

### 4.4 (خيارٌ أفضل إن كانت وصلتك بطيئة) ابنِ على GitHub Actions

بدل البناء والرفع من منزلك، دع خوادم GitHub تفعلها. أنشئ `.github/workflows/runpod-image.yml`:

```yaml
name: build runpod image
on: { workflow_dispatch: {} }
jobs:
  build:
    runs-on: ubuntu-latest
    permissions: { contents: read, packages: write }
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: .
          file: deploy/runpod/Dockerfile
          platforms: linux/amd64
          push: true
          tags: ghcr.io/${{ github.repository_owner }}/aizzak-runpod:v1
```

ثمّ `Actions → build runpod image → Run workflow`. اسم الصورة يصير `ghcr.io/YOURNAME/aizzak-runpod:v1`.

> ⚠️ صور GHCR **خاصّة افتراضيّاً**. إمّا تجعلها عامّة (`Packages → Package settings → Change visibility → Public`)، وإمّا تعطي RunPod بيانات اعتماد السجلّ في حقل *Registry Credentials* عند إنشاء القالب.

---

## 5) الخطوة 3 — إنشاء الـPod

### 5.1 اختيار العتاد

من لوحة RunPod: `Pods → Deploy`.

| البند | القيمة الموصى بها | لماذا |
|---|---|---|
| النوع | **GPU Pod** | Ollama هو المستفيد. (‏CPU Pod يعمل أيضاً، لكنّ توليد النصّ سيكون بطيئاً جدّاً) |
| البطاقة | **RTX A4000 / RTX 4000 Ada (‏16 ج.ب)** | `gemma3:1b` نموذجٌ صغير؛ لا داعي لبطاقةٍ أغلى. ارفع الفئة فقط إن غيّرت إلى نموذجٍ أكبر |
| السحابة | **Secure Cloud** | Community Cloud أرخص لكنّ عنوان الـIP العامّ قد يتغيّر عند إعادة التشغيل |
| المنطقة | أيّ مركزٍ يدعم Network Volumes | يلزم لثبات البيانات |

### 5.2 القالب (Template)

اضغط **Edit Template** (أو `Templates → New Template`) واملأ:

| الحقل | القيمة | ملاحظة |
|---|---|---|
| **Container Image** | `YOURNAME/aizzak-runpod:v1` | أو `ghcr.io/YOURNAME/aizzak-runpod:v1` |
| **Container Registry Credentials** | فارغ إن كانت الصورة عامّة | املأه للصور الخاصّة |
| **Container Start Command** | **اتركه فارغاً** | الصورة تعرّف `CMD`. أيّ قيمةٍ هنا تلغيه ويصير الـPod حاويةً فارغةً حيّة |
| **Container Disk** | **40 GB** | الصورة وحدها ≈ 10 ج.ب. أقلّ من 30 سيفشل السحب |
| **Volume Disk** | **50 GB** | البيانات الدائمة |
| **Volume Mount Path** | `/workspace` | ⚠️ **بالضبط هكذا.** كلّ مسارات البيانات في `entrypoint.sh` تحت `/workspace/data` |

### 5.3 المنافذ

| الحقل | القيمة |
|---|---|
| **Expose HTTP Ports** | `80,9000,9001` |
| **Expose TCP Ports** | `22` |

**ماذا تعني كلّ قيمة:**

- `80` → واجهة التطبيق. ستصلها على `https://<POD_ID>-80.proxy.runpod.net`.
- `9000` → **واجهة MinIO، وهي إلزاميّة لا اختياريّة.** الروابط المُوقَّعة مسبقاً (presigned) تُوقَّع بـSigV4 **على اسم المضيف**، فرابطٌ وُقِّع على `127.0.0.1:9000` لا يصلح لمتصفّحٍ ولا يمكن تصحيحه لاحقاً — لا بـnginx ولا بجراحة نصوص. `entrypoint.sh` يشتقّ `MINIO_PUBLIC_ENDPOINT` تلقائيّاً من `RUNPOD_POD_ID` ليطابق هذا المنفذ.
- `9001` → كونسول MinIO في المتصفّح (اختياريّ لكنّه مريح).
- `22` → SSH، **كمنفذ TCP لا HTTP** (‏SSH ليس بروتوكول HTTP).

> ⚠️ **حدّ المئة ثانية.** وكيل RunPod الـHTTP خلف Cloudflare، ووثائقهم تنصّ على *"a maximum connection time of 100 seconds"* وتوصي بأنّ *"WebSocket applications may work better with TCP exposure"*. وهذا التطبيق **يبثّ** عبر SSE و WebSocket.
>
> إن رأيت اتّصالات البثّ تُقطَع عند ≈ 100 ثانية: أضف `80` إلى **Expose TCP Ports** أيضاً، ثمّ استعمل العنوان المباشر من `Connect → Direct TCP Ports` (بصيغة `http://<IP>:<PORT>`) بدل رابط الوكيل. لا يمكن نشر المنفذ نفسه على HTTP وTCP معاً، فإمّا هذا وإمّا ذاك.

### 5.4 التخزين — أهمّ فقرةٍ في هذا الدليل

| النوع | يُمحى متى؟ |
|---|---|
| **Container Disk** | عند **كلّ** إيقافٍ للـPod. كلّ ما هو خارج `/workspace` يُكتَب ليُفقَد |
| **Volume Disk** | يبقى ما دام الـPod موجوداً؛ يُمحى عند حذفه |
| **Network Volume** | **يبقى مستقلّاً عن الـPod كلّيّاً** |

> ✅ **استخدم Network Volume.** أنشئه أوّلاً: `Storage → Network Volume → New`، بحجم 50 ج.ب، ثمّ اربطه عند إنشاء الـPod.
>
> ⚠️ **لا يمكن ربط Network Volume بـPod قائم.** يجب اختياره **وقت الإنشاء**؛ وإلّا فالسبيل الوحيد هو حذف الـPod وإنشاؤه من جديد.
>
> إن لم تربط أيّ حجم، سينبّهك سجلّ الإقلاع: `⚠️ /workspace is NOT a mounted volume. All data will be LOST`.

### 5.5 متغيّرات البيئة

في قسم **Environment Variables**، أضف هذه بالضبط (`Key` / `Value`):

| المفتاح | القيمة | إلزاميّ؟ |
|---|---|---|
| `POSTGRES_SUPERUSER_PASSWORD` | من §3 | ✅ |
| `AIZZAK_OWNER_PASSWORD` | من §3 | ✅ |
| `APP_RW_PASSWORD` | من §3 | ✅ |
| `OUTBOX_RELAY_PASSWORD` | من §3 | ✅ |
| `RETENTION_SWEEPER_PASSWORD` | من §3 | ✅ |
| `METRICS_READER_PASSWORD` | من §3 | ✅ |
| `MINIO_ROOT_USER` | `aizzak-minio` | ✅ |
| `MINIO_ROOT_PASSWORD` | من §3 | ✅ |
| `FIREBASE_PROJECT_ID` | مثل `aizzak-prod-12345` | ✅ |
| `OLLAMA_MODEL` | `gemma3:1b` | اختياريّ (هذه هي القيمة الافتراضيّة) |
| `WEB_CONCURRENCY` | `2` | اختياريّ |
| `LOG_LEVEL` | `INFO` | اختياريّ |
| `APP_ENV` | `production` | اختياريّ |

**ما لا تحتاج ضبطه — وهذا مقصود:** `DATABASE_URL` · `REDIS_URL` · `MINIO_ENDPOINT` · `QDRANT_URL` · `VAULT_ADDR` · `EMBEDDING_SERVICE_URL` · `OLLAMA_BASE_URL` · `PROVIDER_ROUTING` · `MINIO_PUBLIC_ENDPOINT` · `OAUTH_REDIRECT_BASE_URL`. كلّها يشتقّها `entrypoint.sh`؛ الثلاثة الأخيرة تحديداً تُبنى من `RUNPOD_POD_ID` الذي يحقنه RunPod في كلّ Pod — لأنّك لا تعرف معرّف الـPod قبل إنشائه، ولا يصحّ أن يكون أوّل إقلاعٍ ناقصاً بالضرورة.

> 💡 **بديلٌ أنظف للأسرار:** أنشئ `/workspace/.env` عبر SSH بعد أوّل إقلاع، وضع فيه المتغيّرات بصيغة `KEY=value`. `entrypoint.sh` يقرأه في كلّ إقلاع، مع قاعدةٍ صريحة: **ما هو مضبوطٌ في البيئة يفوز على الملفّ** (متغيّرات القالب مصدرٌ أوضح، وملفٌّ قديمٌ يطغى عليها فخّ).

اضغط **Deploy**.

---

## 6) الخطوة 4 — أوّل إقلاع

افتح `Pods → <podك> → Logs`. أوّل إقلاعٍ يمرّ بهذه المراحل:

| # | ما تراه | المدّة | ماذا يجري |
|---|---|---|---|
| 1 | `Downloading image…` | 5–15 د | RunPod يسحب الصورة (مرّةً واحدةً لكلّ خادم) |
| 2 | `[entrypoint] initialising a new PostgreSQL 16 cluster` | ‏< 1 د | `initdb` على `/workspace/data/postgres`. **أوّل مرّةٍ فقط** |
| 2.5 | `vault-start: sealed with no init material on record -- attempting first-time initialization` ثمّ `vault-start: unsealed and ready` | ثوانٍ | `deploy/vault/start.sh` (‏`[program:vault]`، يُقلع مبكّراً بالتوازي مع Postgres): `operator init` + `operator unseal`، ومفتاح فكّ الختم + التوكن الجذر يُسجَّلان في `$DATA/vault-init/init.json`. **أوّل مرّةٍ فقط** — الإقلاعات التالية تقرأ الملفّ وتفكّ الختم به فوراً |
| 3 | `[bootstrap] creating roles aizzak_owner / app_rw / outbox_relay` | ثوانٍ | نفس سكربت المشروع `10-roles.sh` |
| 4 | `[bootstrap] seeding Vault (KV v2 + Transit + AppRole)` | ثوانٍ | نفس `deploy/vault/bootstrap.sh` — يقرأ التوكن الجذر من `$DATA/vault-init/init.json` (لا توكن dev بعد الآن) |
| 5 | `[bootstrap] creating bucket workspace-files` | ثوانٍ | نفس `deploy/minio/bootstrap.sh` |
| 6 | `[bootstrap] running migrations + grants (app.ops.provision)` | 1–2 د | **إحدى عشرة سلسلة هجراتٍ ثمّ المنح** |
| 7 | `[bootstrap] pulling gemma3:1b` | 2–10 د | تنزيل النموذج إلى `/workspace/data/ollama`. **أوّل مرّةٍ فقط** |
| 8 | `[bootstrap] starting workers` → `starting the API` → `starting nginx` | ‏< 1 د | طبقة التطبيق، **بالترتيب الملزم** |
| 9 | `[bootstrap] ✅ boot complete` | — | جاهز |

**الإجماليّ: 10–30 دقيقة لأوّل إقلاع، ودقيقةٌ إلى ثلاثٍ لكلّ إقلاعٍ بعده** (العنقود موجود، والنموذج منزَّل).

### لماذا هذا الترتيب بالذات

- **الهجرات قبل التطبيق:** `app.ops.provision` ليس هجراتٍ فحسب بل **المنح** معها. حاويةٌ تشغّل الهجرات وحدها تُقلع بدور `app_rw` لا يملك أيّ صلاحيّةٍ على أيّ جدول، فتجيب `permission denied` على أوّل طلبٍ يلمس جدولاً.
- **العمّال قبل المُرحّل:** مجموعات مستهلكي Redis تُنشأ عند `$` (ذيل المجرى). مجموعةٌ تُنشأ **بعد** أن ينشر المُرحّل على مجرًى **جديد** لن ترى تلك المدخلات أبداً — وصفّ الـoutbox موسومٌ `published` سلفاً، أي **فقدٌ حقيقيّ لا إعادة تسليم**. على مجرًى قائمٍ الترتيب غير مؤثّر؛ عند أوّل إقلاعٍ هو الفارق بين خطٍّ يعمل وأحداثٍ تختفي بصمت.
- **nginx أخيراً:** لا تُفتَح الحافّة إلّا بعد أن يجيب `/health` فعلاً، فلا يقف وكيل RunPod أمام تطبيقٍ ميّت.

### ⚠️ ليس عطلاً

| ما تراه في السجلّ | التفسير |
|---|---|
| `PUBLIC_KEY unset -- sshd will accept no logins` | لم تضف مفتاح SSH في §2.3 |
| `vault-start: already unsealed (unexpected for a freshly started process, harmless)` | يظهر فقط لو أُعيد تشغيل `[program:vault]` وحده (‏`supervisorctl restart vault`) بينما السيرفر لم يُعِد التشغيل فعليّاً؛ لا أثر عمليّ |
| `Qdrant … telemetry disabled` | مقصود |

---

## 7) الخطوة 5 — التحقّق

اجلب `POD_ID` من واجهة RunPod (السطر تحت اسم الـPod)، ثمّ على **جهازك**:

```bash
curl -s https://<POD_ID>-80.proxy.runpod.net/health
```

توقَّع رمز 200 وجسماً يقول إنّ الخدمة حيّة. ثمّ:

```bash
curl -s https://<POD_ID>-80.proxy.runpod.net/health/ready
```

> ⚠️ `/health/ready` **لا يلمس أيّ تبعيّة عمداً**: الجاهزيّة تعني «انتهى الإقلاع»، لا «التبعيّات حيّة». فكونه أخضر ليس دليلاً على أنّ مسار البيانات يعمل — الدليل هو ما يلي.

### الدخول عبر SSH

من `Connect → Direct TCP Ports` انسخ الـIP والمنفذ المقابل لـ22:

```bash
ssh root@<IP> -p <PORT> -i ~/.ssh/id_ed25519
```

### حالة كلّ عمليّة

```bash
supervisorctl -c /etc/supervisor/supervisord.conf status
```

الحالة السليمة: كلّ شيءٍ `RUNNING` ما عدا `bootstrap` الذي يجب أن يكون **`EXITED`** (فقد أنهى عمله بنجاح — وهذا هو الصحيح، لا عطل).

### الإثباتات الحيّة (من داخل الـPod)

```bash
cd /app && /opt/venv/bin/python deploy/smoke/stack_smoke.py
```

يثبت `OPS-02` وعزل RLS والتوقيع المسبق. ثمّ فحص خدمة التضمين والنموذج:

```bash
curl -s 127.0.0.1:8080/health && curl -s 127.0.0.1:11434/api/tags
```

---

## 8) الأمان — قبل أيّ استخدامٍ حقيقيّ

### 8.1 ⚠️ Vault دائم الآن — واقرأ هذا قبل أوّل اعتمادٍ حقيقيّ

**حُلّت مشكلة `-dev` (‏release-blockers-plan.md §3 خطوة 1)، وبقي قيدٌ آخر يجب أن تعرفه قبل أن تثق بهذا المسار.** الصورة لم تعد تشغّل `vault server -dev` (‏في الذاكرة بالكامل، تُمحى KV والTransit وAppRole عند كلّ إعادة تشغيل). بدلاً منه: `deploy/vault/start.sh` (‏`[program:vault]`) يشغّل Vault بتخزين `file` دائم تحت `$DATA/vault`، ويقود السيرفر بنفسه خلال `operator init` (أوّل إقلاعٍ فقط) أو `operator unseal` (كلّ إقلاعٍ بعده) **قبل** أن يستطيع `aizzak-bootstrap.sh` رؤيته صحّيّاً.

**ما الذي تغيّر عمليّاً:**

- KV والTransit وAppRole **يبقَون عبر إعادة التشغيل** — لا حاجة لـ`bootstrap.sh` لإعادة بذرها من الصفر بعد كلّ إقلاع.
- مفتاح Transit **لا يُعاد توليده أبداً** بعد أوّل إقلاع (مُثبَتٌ حيّاً، انظر [`release-blockers-plan.md`](release-blockers-plan.md) §4) — فالخطر الذي حذّر منه هذا القسم سابقاً (بيانات مستأجرٍ تصير غير قابلةٍ لفكّ التشفير إلى الأبد) **زال بانتقال التخزين**، لا بإصلاح مؤقّتٍ في `bootstrap.sh`.

**⚠️ لكنّ هذا لا يكفي وحده لإنتاجٍ حقيقيّ، وهذا مقصودٌ لا سهو.** لا يوجد KMS داخل Pod واحد يُفكّ الختم عبره تلقائيّاً بأمان، فـ`start.sh` يكتب **مفتاح فكّ الختم والتوكن الجذر في ملفٍّ نصّيٍّ عاديّ** على القرص الدائم نفسه: `$DATA/vault-init/init.json`، بصلاحيّة `chmod 600`. أيّ من يقرأ ذلك الملفّ — عمليّةً أخرى على نفس الـPod، أو نسخةً احتياطيّةً لـ`/workspace` لم تُشفَّر، أو من يصل SSH بمفتاح مسروق — يستطيع فكّ ختم Vault وقراءة **كلّ** سرٍّ يحرسه. هذه ليست ثغرةً غير مقصودة؛ إنّها **الثمن الصريح** لتشغيل Vault بلا خدمة KMS خارجيّة، وهي مقبولةٌ **لمضيفٍ واحدٍ محلّيّ فقط** (تجربة، عرضٌ داخليّ، بيئة تطوير) — **وغير مقبولةٍ إطلاقاً لإنتاجٍ حقيقيّ.**

| استخدامك | ماذا تفعل |
|---|---|
| تجربة/عرض، لا اعتمادات مستخدمين حقيقيّة | اتركه كما هو — التخزين دائمٌ والمفتاح لا يتجدّد |
| **أيّ اعتمادٍ حقيقيّ لمستخدم** | ⛔ لا تكتفِ بهذا الإعداد. راجع مسار الترقية أدناه قبل تخزين أوّل بيانةٍ حقيقيّة |

#### نسخٌ احتياطيّ واستعادة `vault-init`

مادّة التهيئة (‏`$DATA/vault-init/init.json`) هي **المفتاح الوحيد** لفكّ ختم Vault على هذا القرص. فقدانها بينما `$DATA/vault` لا يزال قائماً يعني بياناتٍ **غير قابلةٍ للفكّ إلى الأبد** — تماماً كفقدان مفتاح Transit نفسه، لأنّ مفتاح فكّ الختم هو ما يحمي كلّ ما تحته.

```bash
# على الـPod، بعد كلّ تغييرٍ حقيقيّ (نادراً — الملفّ يُكتَب مرّةً واحدة فقط
# عند أوّل إقلاع؛ لا حاجة لتكرار هذا إلّا للتأكّد أنّ نسخةً موجودة أصلاً)
cat /workspace/data/vault-init/init.json    # يحتوي unseal key + root token

# اسحبه إلى جهازك فوراً بعد أوّل إقلاع، واحفظه في مكانٍ مُشفَّرٍ منفصل
# (مدير كلمات سرّ، خزنة أسرارٍ أخرى — ليس مجلّد Downloads):
runpodctl send /workspace/data/vault-init/init.json
```

**الاستعادة** (نفس القرص فُقد ملفّه بطريقةٍ ما، وبيانات `$DATA/vault` ما زالت سليمة): انسخ `init.json` المحفوظ إلى `$DATA/vault-init/init.json` بنفس المسار والصلاحيّة (`chmod 600`) قبل أن يُقلع `[program:vault]` من جديد؛ `start.sh` يجده ويفكّ الختم به بدل محاولة تهيئةٍ جديدة (وهو أصلاً **يرفض** إعادة التهيئة إن وجد Vault مُهيَّأً بلا ملفٍّ محلّيّ يطابقه — راجع رسالة الخطأ في سجلّه، تشرح بالضبط هذه الحالة).

#### مسار الترقية لإنتاجٍ حقيقيّ (وصفٌ لا تنفيذ)

المسار الأهمّ هو **auto-unseal عبر KMS خارجيّ**: Vault يدعم `seal "awskms"`/`"gcpckms"`/`"azurekeyvault"` في ملفّ الإعداد، وعندها **لا يُخزَّن مفتاح فكّ ختمٍ على القرص إطلاقاً** — Vault يطلب من خدمة الـKMS فكّ تعمية مفتاحه الرئيسيّ في كلّ إقلاع، والوصول محكومٌ بصلاحيّات IAM لا بملفٍّ يمكن نسخه. البديل الآخر: **Transit auto-unseal** عبر مثيل Vault **آخر** (خارج هذا الـPod، بتخزينٍ ومصادقةٍ خاصّين به) يُستعمَل بصفته موفّر فكّ ختمٍ فقط لهذا الواحد. كلاهما يحتاج تلك الخدمة (KMS أو Vault الخارجيّ) **موجودةً مسبقاً** — لا يمكن تفعيلها بتعديل ملفّ إعدادٍ محلّيٍّ وحده، ولذلك لم تُنفَّذ هنا. من اختار المسار (ج) في [§11](#11-المسار-البديل-ج--خادمٌ-عاديّ--runpod-للـgpu-وحده) لا يواجه هذا القيد إطلاقاً: Vault هناك يعمل على خادمٍ عاديّ ضمن مكدّس Compose، وبإمكانه استعمال أيّ من الخيارين أعلاه بلا قيد «Pod واحد».

هذا **القيد الأهمّ المتبقّي** في نشرٍ من Pod واحد، ولا يمكن التحايل عليه بتعديل سكربتٍ محلّيّ.

### 8.2 السطح المكشوف

| المنفذ | مكشوف؟ | التقييم |
|---|---|---|
| 80 (nginx) | ✅ عامّ | مقصود. المصادقة عبر Firebase وحرّاس RBAC |
| 9000 (MinIO API) | ✅ عامّ | **إلزاميّ** للروابط المُوقَّعة. الدلو خاصّ؛ الوصول بتوقيعٍ مؤقّتٍ فقط |
| 9001 (كونسول MinIO) | ✅ عامّ | **أزله في الإنتاج.** لوحة إداريّة بكلمة سرّ على الإنترنت المفتوح |
| 22 (SSH) | ✅ عامّ | بمفتاحٍ فقط؛ كلمات السرّ غير مفعّلة |
| البقيّة | ⛔ `127.0.0.1` | لا يصلها شيءٌ من خارج الـPod |

للإنتاج: احذف `9001` من **Expose HTTP Ports** وصِل بالكونسول عبر نفق SSH عند الحاجة:

```bash
ssh -N -L 9001:127.0.0.1:9001 root@<IP> -p <PORT> -i ~/.ssh/id_ed25519
```

### 8.3 اسمٌ خاصٌّ بك بدل رابط الوكيل

`https://<POD_ID>-80.proxy.runpod.net` **يتغيّر مع كلّ Pod جديد**. لنطاقٍ ثابت، ضع Cloudflare Tunnel أو وكيلاً عكسيّاً أمامه، وحدِّث عندها `OAUTH_REDIRECT_BASE_URL` و`MINIO_PUBLIC_ENDPOINT` يدويّاً في متغيّرات البيئة.

---

## 9) التشغيل اليوميّ

### إيقافٌ وتشغيل

| العمليّة | الأثر | التكلفة |
|---|---|---|
| **Stop** | قرص الحاوية يُمحى؛ `/workspace` يبقى | تدفع ثمن التخزين فقط |
| **Start** | الصورة تُسحب ثانيةً؛ `entrypoint` يجد العنقود قائماً فلا يعيد التهيئة | — |
| **Terminate** | يُحذف كلّ شيء، ويبقى Network Volume إن استعملته | — |

### تحديث الصورة بعد تعديل الشيفرة

```bash
cd /home/AIZZAK && docker build --platform linux/amd64 -f deploy/runpod/Dockerfile -t YOURNAME/aizzak-runpod:v2 . && docker push YOURNAME/aizzak-runpod:v2
```

ثمّ في RunPod: `Edit Pod → Container Image → :v2 → Save`. سيعيد الـPod الإقلاع بالصورة الجديدة، وهجراتٌ جديدةٌ ستُشغَّل تلقائيّاً عبر `bootstrap.sh`.

> ⚠️ **لا تعِد استخدام الوسم `v1`.** RunPod قد يستعمل نسخةً مخزّنةً محلّيّاً على الخادم فتحدّث الصورة ولا يتغيّر شيء، وتقضي ساعةً تطارد عطلاً غير موجود. زِد الرقم في كلّ مرّة.

### نسخٌ احتياطيّ

```bash
# على الـPod
PGPASSWORD="$POSTGRES_SUPERUSER_PASSWORD" pg_dump -h 127.0.0.1 -U postgres -Fc aizzak > /workspace/backup-$(date +%F).dump
```

ثمّ اسحبه إلى جهازك (‏`runpodctl` مثبَّتٌ في كلّ Pod):

```bash
runpodctl send /workspace/backup-2026-07-24.dump
```

وعلى جهازك: `runpodctl receive <الرمز>`.

---

## 10) جدول الأعطال

| العَرَض | الفحص والعلاج |
|---|---|
| الـPod يقلع ثمّ يموت فوراً | السجلّ يبدأ بـ`⛔ <VAR> is not set` — متغيّرٌ إلزاميّ ناقص (§5.5) |
| `exec format error` | بُنيت الصورة على ARM. أعد البناء بـ`--platform linux/amd64` |
| `Downloading image` عالقٌ ثمّ يفشل | **Container Disk** أصغر من الصورة. ارفعه إلى 40 ج.ب |
| `[bootstrap] ⛔ PostgreSQL did not come up` | راجع سطور `program:postgres`. غالباً `/workspace` بلا حجمٍ مربوط أو الصلاحيّات خاطئة |
| `provisioning failed` | اقرأ التتبّع فوق السطر. عادةً كلمة سرّ `AIZZAK_OWNER_PASSWORD` لا تطابق ما وُضع عند تهيئة العنقود — **حجمٌ قديمٌ بكلمة سرٍّ جديدة**. إمّا تعيد كلمة السرّ القديمة، وإمّا تبدأ بحجمٍ نظيف |
| التطبيق لا يقلع إطلاقاً | `FIREBASE_PROJECT_ID` فارغ — فشلٌ سريعٌ عند الإنشاء |
| 502 من رابط الوكيل | `supervisorctl status app`. إن كان `RUNNING` فراجع `nginx`؛ إن كان `FATAL` فالسجلّ يحمل السبب |
| البثّ يُقطَع عند ≈ 100 ثانية | حدّ وكيل RunPod. انشر `80` كمنفذ **TCP** واستعمل العنوان المباشر (§5.3) |
| `POST /search` يعيد 503 | `supervisorctl status embedding` — النموذج يحتاج ≈ 30 ثانيةً للتحميل بعد الإقلاع |
| نداءات LLM تفشل بـ404 | النموذج غير مسحوب: `ollama list`، ثمّ `ollama pull gemma3:1b` |
| `secret does not exist in Vault` | Vault دائمٌ الآن (§8.1) فهذا لم يعد سببه فقدان الذاكرة عادةً — الأرجح أنّ `[bootstrap]` لم يكتمل بعد (طبيعيّ في أوّل دقيقةٍ من أوّل إقلاع). راجع `supervisorctl status bootstrap`؛ إن كان `EXITED` بغير 0 اقرأ سجلّه |
| `vault-start: ⛔ 'operator init' failed, and .../init.json does not exist` | بيانات `$DATA/vault` مُهيَّأةٌ من إقلاعٍ سابق لكنّ `$DATA/vault-init/init.json` مفقود — **لا يمكن استرجاع الوصول بلا ذلك الملفّ**. استعِد نسخةً احتياطيّةً منه (§8.1) أو اقبل أنّ تلك البيانات صارت غير قابلةٍ للفكّ |
| `vault-start: unseal FAILED with the recorded key` | `$DATA/vault-init/init.json` لا يطابق `$DATA/vault` الحاليّة (مثلاً حجمٌ استُبدل جزئيّاً). لا حلّ آليّ — إمّا استعادة زوجٍ متطابقٍ من نسخةٍ احتياطيّة، وإمّا قبول فقد تلك البيانات |
| Vault يعمل ساعةً ثمّ يفشل كلّ سرّ | انتهاء `token_ttl=1h` لتوكن AppRole الذي يستعمله التطبيق (لا علاقة له بتوكن Vault الجذر). العلاج منفَّذٌ في الشيفرة (إعادة مصادقةٍ واحدة عند 401/403) — تحقّق أنّ `relogin` موصولٌ في جذر التركيب |
| صفر صفوفٍ رغم وجود بيانات | لم يُضبط `app.workspace_id` (‏RLS) — راجع `ExecutionContext`/`rls.py` |
| نموّ ذاكرة Redis | `redis-cli XLEN stream.<وحدة>` — المجاري إلحاقيّة و`XACK` **لا يحذف**. السقف `STREAM_MAXLEN` علاجٌ لا حلّ |
| العامل ينهار عند الإقلاع | **عطلُ بيئةٍ لا نقصُ كود، أيّاً كانت قيمة `WORKER`**: Vault · سرّ MinIO · `PROVIDER_ROUTING` (‏و`media` يضيف: مدخلةَ `image` واعتمادَ `image:openai`) — الرسالة تسمّيه |
| فقدت كلّ البيانات بعد إعادة تشغيل | لم يكن هناك حجمٌ مربوطٌ على `/workspace` (§5.4) |

**أوامرٌ تشخيصيّةٌ سريعة** (من داخل الـPod عبر SSH):

```bash
supervisorctl -c /etc/supervisor/supervisord.conf status
```

```bash
supervisorctl -c /etc/supervisor/supervisord.conf tail -1000 app stderr
```

```bash
supervisorctl -c /etc/supervisor/supervisord.conf restart app
```

---

## 11) المسار البديل (ج) — خادمٌ عاديّ + RunPod للـGPU وحده

**متى تختاره؟** حين يكون سبب ذهابك إلى RunPod هو تسريع نموذج اللغة فقط. عندها لا داعي لنقل المكدّس أصلاً.

**الملاحظة الجوهريّة:** كلّ خدمات هذا المشروع تعمل على المعالج فقط. `services/embedding/requirements.txt` يسحب torch من **فهرس عجلات المعالج** صراحةً. المكوّن الوحيد الذي يستفيد من GPU هو **Ollama**، وهو أصلاً **خارج** مكدّس Compose (‏`OLLAMA_BASE_URL` يشير إلى `host.docker.internal:11434`).

### الخطوات

**1. استأجر خادماً لينكسيّاً عاديّاً** (‏Hetzner CX42 أو ما يعادله — ‏8 أنوية / 16 ج.ب / 160 ج.ب، ≈ 20–30 دولاراً شهريّاً):

```bash
curl -fsSL https://get.docker.com | sh
```

**2. انقل المشروع وشغّله كما هو — بلا أيّ تعديل:**

```bash
git clone <مستودعك> /opt/aizzak && cd /opt/aizzak && cp .env.example .env
```

عدّل `.env`: الكلمات الستّ من §3، و`FIREBASE_PROJECT_ID`، و`MINIO_PUBLIC_ENDPOINT=<نطاقك>:19000`.

```bash
docker compose up -d
```

**3. أنشئ Pod على RunPod لـOllama وحده** — استخدم القالب الرسميّ `ollama/ollama`، وانشر منفذ HTTP‏ `11434`. الصورة الرسميّة تخدم على `0.0.0.0:11434` افتراضيّاً.

**4. صِل الاثنين** — في `.env` على خادمك:

```
OLLAMA_BASE_URL=https://<POD_ID>-11434.proxy.runpod.net
```

ثمّ `docker compose up -d app`.

> ⚠️ **وكيل RunPod عامٌّ ومفتوح.** أيّ من يعرف الرابط يستطيع استعمال نموذجك. عالج ذلك بربطٍ خاصٍّ عبر **Global Networking** (يعطي الـPod عنواناً داخليّاً `<POD_ID>.runpod.internal` لا يُنشر للإنترنت أبداً)، أو بنفق SSH من خادمك إلى الـPod.

### المقارنة

| | المسار (أ): Pod واحدٌ شامل | المسار (ج): خادم + Pod لأولاما |
|---|---|---|
| التغيير في المستودع | صورةٌ جديدة (‏جاهزة) | **لا شيء** |
| `docker compose` يعمل كما هو | ⛔ | ✅ |
| كلفة شهريّة تقريبيّة | ≈ 0.30 دولار/ساعة ⇒ ≈ 220 دولاراً إن عمل دائماً | ≈ 25 دولاراً للخادم + الـGPU بالساعة عند الحاجة |
| ثبات البيانات | Network Volume | أقراص الخادم + نسخٌ احتياطيّ عاديّ |
| Vault دائم | ✅ نفس `deploy/vault/start.sh`، لكن بلا KMS داخل Pod واحد (§8.1) | ✅ نفس الحلّ، ونفس القيد إن بقيت داخل Pod واحد أيضاً (§8.1) |
| مطابقة تصميم المشروع | جزئيّة (‏§1) | **كاملة** |

---

## 12) القواعد الذهبيّة لهذا النشر

1. **RunPod لا يشغّل `docker compose`.** صورةٌ واحدة، لا اثنتا عشرة.
2. **كلّ ما هو خارج `/workspace` يُكتَب ليُفقَد.**
3. **`--platform linux/amd64` في كلّ بناء.**
4. **وسمٌ جديدٌ لكلّ تحديث** — لا تعِد استخدام `v1`.
5. **`MINIO_PUBLIC_ENDPOINT` يجب أن يطابق منفذاً منشوراً فعلاً**، وإلّا فكلّ رابطٍ مُوقَّعٍ مسبقاً مكسور، ولا يمكن إصلاحه بعد التوقيع.
6. **العمّال قبل المُرحّل** على مجرًى جديد.
7. **`WORKER=memory` هي القيمة الوحيدة العاملة اليوم.**
8. **Vault دائمٌ الآن، لكنّ `$DATA/vault-init/init.json` (المفتاح + التوكن) عاديٌّ غير مُشفَّر على القرص** — انسخه احتياطيّاً (§8.1) قبل أيّ اعتمادٍ حقيقيّ، وراجع مسار الترقية إلى KMS قبل إنتاجٍ فعليّ.
9. **`FIREBASE_PROJECT_ID` الفارغ = تطبيقٌ لا يقلع**، لا مجرّد مصادقةٍ معطّلة.
10. **Pod ‏`healthy` ليس دليلاً على مسار بيانات** — الدليل في `deploy/smoke/`.

---

## المراجع

- [Runpod Pods — Overview](https://docs.runpod.io/pods/overview) — «‏Docker Compose is not supported»
- [Enhanced CPU Pods Now Support Docker and Network Volumes](https://www.runpod.io/blog/enhanced-cpu-pods-docker-network) — سحب دعم Docker‑in‑Docker
- [Expose ports](https://docs.runpod.io/pods/configuration/expose-ports) — وكيل HTTP، منافذ TCP، حدّ المئة ثانية
- [Environment variables](https://docs.runpod.io/pods/templates/environment-variables) — ‏`RUNPOD_POD_ID` وأخواته
- [Manage Pod templates](https://docs.runpod.io/pods/templates/manage-templates) — حقول القالب واعتمادات السجلّ
- [Network volumes](https://docs.runpod.io/storage/network-volumes) — الثبات والربط وقت الإنشاء
- [Connect to a Pod with SSH](https://docs.runpod.io/pods/configuration/use-ssh) — ‏sshd في صورةٍ مخصّصة
- [Global networking](https://docs.runpod.io/pods/networking) — ‏`<POD_ID>.runpod.internal`

</div>
