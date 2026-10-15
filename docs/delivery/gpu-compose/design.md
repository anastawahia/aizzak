<div dir="rtl">

# التصميم التقني: الكرت داخل Compose — Ollama والتضمين (المرحلة 2 من خطّة خادم الكرت)

> **المراجع:** [`requirements.md`](requirements.md)، و[`stories.md`](stories.md) (US‑1…US‑8، 64 معياراً، وهو مرجع معرّفات المعايير)، و[`status.md`](status.md) «قرارات وتصعيدات» (البوّابة ① وجواب Q‑9، 2026‑10‑14)، و[`docs/server-gpu-plan.md`](../../server-gpu-plan.md) الخطوات 2.1–2.6 و§3 و§7. العمق والشكل على نمط [`prebuilt-images/design.md`](../prebuilt-images/design.md)، ودروس [`retro.md`](../prebuilt-images/retro.md) مطبّقة (تقرير الإصلاح بـ`git show --stat`، والإعداد الخارجيّ بأمرٍ يختبر قبل الحفظ).
>
> **الفرع:** `feat/gpu-compose` من `master` (`1814998`). **المنفّذون:**
> - `backend-developer`: ‏`services/embedding/app.py` واختباره.
> - `devops-engineer`: ‏Compose والسكربتات والصورة والإصدار والوثائق، ومعها اختباراتها في الالتزام نفسه (القاعدة 3).
> - `qa-engineer`: اختبارات النظافة، وأداة قياس التطابق، و`test-plan.md`، والإثباتات المحلّيّة.
>
> مستودعٌ واحد وفرعٌ واحد، فالكتّاب يعملون بالتتابع (§6).
>
> **المعرّفات:** `FR‑n` و`NFR‑n` و`AC‑n.m` و`Q‑n` و`R‑n` و`A‑n` و`ش‑n` من `requirements.md` و`stories.md`. قرارات هذا التصميم `D‑1`…`D‑16`. وكلّ `ملفّ:سطر` هنا تحقّقتُ منه على `1814998` يوم 2026‑10‑14.
>
> **قياساتٌ أُجريت لهذا التصميم** (قراءةٌ فقط: لم تُشغَّل حاوية، ولم يُلمس المكدّس الحيّ، ولم يُقرأ `.env`):
> - وسوم الصور وبصماتها على Docker Hub، ومحتوى طبقات صورة Ollama الصغيرة.
> - وجود عجلة `torch-2.5.1+cu124` لـPython 3.12 وحجمها.
> - سلوك Compose ‏v5.3.1 بـ`docker compose config` وحده، في مجلّدٍ مؤقّت.
> - نوع Docker على هذا الجهاز، وآخر إصدارٍ مستقرّ من NVIDIA Container Toolkit في مستودعها.
> - بنية `gemma3:1b` من `/api/show` في Ollama الأصليّ (قراءة بيانات وصفيّة، لا تحميل نموذج).

## 1. الملخص

الميزة **بنيةٌ تشغيليّة**، ومعها تعديلٌ صغير في خدمة التضمين المستقلّة. لا تلمس `src/` ولا `.importlinter` ولا المخطّط ولا `deploy/runpod/`. أجزاؤها ستّة، لكلٍّ خطوةٌ من الخطّة:

1. **2.1 فحص المتطلّبات:** ‏`deploy/gpu-preflight.sh` يفحص ولا يثبّت، ولكلّ نقصٍ رمز خروج. ومنها رمز «لا كرت» يختار به `install.sh` (5.2) التشغيلَ على المعالج. ومعه `docs/deploy-server.md` بقسم المتطلّبات وحده وقائمة أمانٍ فارغة (Q‑4 أ).
2. **2.2 Ollama داخل المكدّس:** خدمة `ollama` (‏`ollama/ollama:0.13.5`) وخدمةٌ لمرّةٍ واحدة `ollama-pull`، كلتاهما تحت profile ‏`local-llm`، وبلا منافذ. القيمة الافتراضيّة لـ`OLLAMA_BASE_URL` تصير `http://ollama:11434`، و`.env.example` لا يكتب غيرها.
3. **2.3 الجسر تحت profile:** ‏`ollama-bridge` تحت `wsl-ollama`. وكلّ حافّةٍ إليه أو إلى `ollama` تصير `required: false`. سطر المالك الوحيد: `COMPOSE_PROFILES=wsl-ollama`.
4. **2.4 صورة CUDA:**
   - `requirements-cuda.txt` بالإصدارات نفسها وفهرس `cu124`، ووسيطٌ في Dockerfile، وصورةٌ ثالثة في `build-push.sh`.
   - `GET /health` في التضمين يذكر الجهاز والدقّة.
   - المتغيّر `EMB_REQUIRE_CUDA` يجعل الخدمة ترفض الإقلاع دون CUDA (Q‑10).
5. **2.5 ملفّ الكرت:** ‏`docker-compose.gpu.yml` لثلاث خدماتٍ فقط. لا يبني شيئاً، ولا يُركَّب مع ملفّ البناء.
6. **2.6 ذاكرة الكرت:**
   - جدولٌ جديد في `docs/design/08-local-runbook.md` §2‑ي.
   - `OLLAMA_MAX_LOADED_MODELS=1`، و`OLLAMA_NUM_PARALLEL=2` بعلاقةٍ مكتوبةٍ ومختبَرة مع `OLLAMA_MAX_CONCURRENCY`.

**النمط المتّبع، والشاهد عليه:**

| الجديد | يتبع | الشاهد |
|---|---|---|
| `ollama-pull` | صورة Python جاهزة تشغّل ملفّاً واحداً مربوطاً، للقراءة فقط وبلا امتياز | `alert-sink` (`docker-compose.yml:2654-2684`)، و`deploy/alertmanager/sink.py` |
| فحص صحّة `ollama` | `/dev/tcp` في bash لصورةٍ بلا `curl` ولا `wget` | `qdrant` (`docker-compose.yml:1176-1197`) |
| `docker-compose.gpu.yml` | ملفٌّ إضافيّ يُطلب صراحةً ولا يُدمَج تلقائيّاً | `docker-compose.build.yml`، و`docker-compose.wsl-gpu.yml` |
| `deploy/gpu-preflight.sh` واختباره | bash بـ`set -euo pipefail` ورموز خروجٍ في الرأس، وبدائل Python على `PATH` تسجّل النداءات | `deploy/release/build-push.sh`، و`tests/unit/test_release_build_push.py:29-109` |
| اختبارات `[config]` | `docker compose config` بـ`--env-file .env.example`، ويتخطّى إن غاب Docker | `tests/unit/test_compose_build_overlay.py:34-45,80-94` |
| اختبار `/health` | محمِّلٌ مزيَّف بدل torch | `tests/unit/test_embedding_service_app.py:84-97` |

## 2. الملفات

| المسار | إنشاء/تعديل | المسؤولية | المنفّذ |
|---|---|---|---|
| `services/embedding/app.py` | تعديل (محميّ، ضمن Q‑8) | `_Device` و`_choose_device` و`_require_cuda`. و`_load_model` يأخذ الجهاز. وحقلا `device`/`dtype` في `HealthResponse`. ورفض الإقلاع (§3.6) | backend-developer |
| `tests/unit/test_embedding_service_app.py` | تعديل | المحمِّل المزيَّف بثلاثة وسائط، وبديلٌ تلقائيّ لـ`_choose_device`، والردّ الحرفيّ في `:109-136`، واختبارات AC‑4.3 وQ‑10 (§5.1) | backend-developer |
| `deploy/gpu-preflight.sh` | إنشاء بصلاحيّة `100755` (محميّ، ضمن Q‑8) | فحص 2.1 (§3.9) | devops-engineer |
| `tests/unit/test_gpu_preflight.py` | إنشاء | AC‑1.1…AC‑1.6 وAC‑1.9 والحوافّ | devops-engineer |
| `docs/deploy-server.md` | إنشاء | قسم «المتطلّبات»، وجدول رموز الخروج، وقائمة أمانٍ فارغة (§3.12) | devops-engineer |
| `docker-compose.yml` | تعديل (محميّ، ضمن Q‑8) | `OLLAMA_BASE_URL` ومرساة `&provider-routing`، وخدمتا `ollama`/`ollama-pull` وحجم `ollama-models`، وprofile الجسر، وحواف `required: false`، وثلاثة تعليقات (§3.2، §3.3) | devops-engineer |
| `deploy/ollama/pull_models.py` | إنشاء (**محميّ، خارج Q‑8: ⚠️ 1**) | منطق `ollama-pull`، بمكتبة Python القياسيّة وحدها (§3.4) | devops-engineer |
| `tests/unit/test_ollama_pull_models.py` | إنشاء | AC‑2.3 وNFR‑10، بخادم HTTP مزيَّف | devops-engineer |
| `tests/unit/test_compose_local_llm.py` | إنشاء | AC‑2.1/2.2/2.4/2.5/2.6، وAC‑3.1/3.2/3.4/3.5، وAC‑6.2/6.3، وAC‑7.3، وجزءٌ من AC‑8.1 | devops-engineer |
| `tests/unit/test_healthcheck_coverage.py` | تعديل | `ollama-pull` في `_ONE_SHOT`، وحافّة الجسر `required is False` | devops-engineer |
| `tests/unit/test_resource_budget.py` | تعديل | العدّ 29/5/3 ← 29/5/5 (C3) ← 28/5/6 (C4)، وسطور السجلّ في التعليق | devops-engineer |
| `docs/design/08-local-runbook.md` | تعديل (مُلزِم، ضمن Q‑8) | جدول الخدمات `:30`، والدفتر `:476-477`، ونصّ §2‑ز، وجدول §5، و§3.0، وقسمٌ جديد §2‑ي (§3.10) | devops-engineer |
| `.env.example` | تعديل (قالبٌ مسموح) | كتلة Ollama بلا سطرٍ مفعّل، وسطور profiles والخادم معلَّقة (§3.11) | devops-engineer |
| `services/embedding/requirements-cuda.txt` | إنشاء (محميّ، ضمن Q‑8) | §3.5 | devops-engineer |
| `services/embedding/Dockerfile` | تعديل (محميّ، ضمن Q‑8) | `ARG EMBEDDING_REQUIREMENTS` (§3.5) | devops-engineer |
| `tests/unit/test_embedding_cuda_image.py` | إنشاء | AC‑4.1 وAC‑4.2 وAC‑4.5 | devops-engineer |
| `deploy/release/build-push.sh` | تعديل (محميّ، ضمن Q‑8) | الصورة الثالثة ووسيط البناء، وحذف `RESERVED_IMAGES` (§3.7) | devops-engineer |
| `tests/unit/test_release_build_push.py` | تعديل | AC‑4.4 (§5.1) | devops-engineer |
| `docker-compose.gpu.yml` | إنشاء (محميّ، ضمن Q‑8) | §3.8 | devops-engineer |
| `tests/unit/test_compose_gpu_overlay.py` | إنشاء | AC‑5.1…AC‑5.8، وجزءٌ من AC‑6.2 وAC‑8.1 | devops-engineer |
| `docs/stack-commands.md` | تعديل | سطر المالك، وقسمٌ جديد «ز) الكرت على خادم» بالصفوف 40–43، وقسم الإصدار بثلاث صور (§3.12) | devops-engineer |
| `docs/quickstart.md` | تعديل | بندٌ واحد في §2.1: سطر `COMPOSE_PROFILES` (§3.12) | devops-engineer |
| `docs/runbooks/alerts.md` | تعديل | الخطوة 3 في `AizzakLlmCircuitOpen` بالمسارين (`:508-514`) | devops-engineer |
| `deploy/prometheus/alerts.yml` | تعديل نصّ `response` فقط (محميّ، ضمن Q‑8 اختياريّاً) | `:1068-1073` بالمسارين، و`test-rules.sh` أخضر | devops-engineer |
| `docker-compose.wsl-gpu.yml`، `docker-compose.test.yml` | تعديل تعليق الرأس فقط (محميّ، ضمن Q‑8 اختياريّاً) | سطر الاستعمال بـ`-f docker-compose.build.yml` (`retro.md` §7)، وفي ملفّ WSL إشارةٌ إلى ملفّ الكرت | devops-engineer |
| `tests/unit/test_gpu_compose_hygiene.py` | إنشاء | AC‑7.5 وAC‑7.6، وفحصٌ نصّيّ خفيف لـAC‑8.2 وAC‑8.3 | qa-engineer |
| `tests/eval/vector_parity.py` | إنشاء | أداة AC‑4.9 (التطابق) وAC‑4.11 (حمل الذاكرة). لا يجمعها pytest (`tests/eval/README.md:14-16`) | qa-engineer |
| `docs/delivery/gpu-compose/test-plan.md` | إنشاء | خطّة الاختبار وسجلّ القياسات: الحجم والذاكرة والتطابق | qa-engineer |
| `docs/server-gpu-plan.md` (§4 والتصحيحات)، `docs/server-gpu-summary.html` | تعديل **في النهاية** | §6.4 | المنسّق |

**لا تُلمس، ولكلٍّ سببه:**
- **`docker-compose.build.yml`:** يبقى يبني صورة المعالج وحدها (D‑1). لذلك تخرج `.embedding.build.args` من القائمة البيضاء في AC‑7.1.
- **`.github/workflows/release.yml` و`ci.yml`:** السكربت يبني الصور الثلاث بلا تغييرٍ في المسار (D‑12). ووظيفة `integration` لا تقلع الجسر ولا `app` (`ci.yml:116-123`).
- **`services/embedding/requirements.txt`:** تعليق رأسه (`:10-14`) يصف صورة CUDA بأنّها «لاحقة»، فصار قديماً. لكنّه ليس في Q‑8، ولا يغيّر سلوكاً. يُصحَّح بموافقةٍ لاحقة أو في 7.4.
- **`deploy/resource-budget.sh`:** يصنّف الخدمات بالمفتاحين نفسيهما (`profiles` و`restart`)، فيتبع النقل وحده. تعليق رأسه (`:38-39`) يسمّي `backup` و`cadvisor` و`k6` أمثلة. ليس في Q‑8، و3.4 يعيد كتابته.
- **`docs/architecture.md:93,650` و`docs/deploy-runpod.md` §11:** صحيحان لجهاز المالك (الجسر باقٍ عنده)، وتحدّثهما 7.4 كما تقول الخطّة (`server-gpu-plan.md` §7.4).
- **`deploy/prometheus/alerts.test.yml`:** لا يطابق نصّ `response` (لا `exp_annotations`)، فلا يتغيّر.
- **`README.md`** (الشجرة): في 7.4.
- **`src/` و`deploy/runpod/` و`.importlinter` و`CLAUDE.md`** (AC‑7.7).

## 3. العقود

### الواجهات البرمجية

| الطريقة | المسار | الطلب | الاستجابة | الأخطاء | الصلاحية |
|---|---|---|---|---|---|
| GET | `/health` في **خدمة التضمين الداخليّة** (`embedding:8080`، لا عبر الحافّة) | — | 200: الحقول الحاليّة، ومعها `"device": "cpu"` أو `"cuda"`، و`"dtype": "float32"` أو `"float16"` | 503 قبل تحميل النموذج، كما اليوم. ومع `EMB_REQUIRE_CUDA=1` بلا CUDA لا ردّ أصلاً: العمليّة تخرج عند الإقلاع | داخليّ وبلا مصادقة، بحدود الثقة نفسها التي لـPostgres (`docker-compose.yml:1203-1206`) |

**لا واجهة HTTP جديدة في المنصّة، ولا تغيير في `docs/design/openapi.yaml`،** لأنّ خدمة التضمين ليست فيه. والحقلان إضافةٌ لا تكسر أحداً: المستهلكون يقرؤون مفاتيح بعينها (`tests/integration/test_embedding_service_live.py:92-94`).

### 3.1 قرارات التصميم

- **D‑1: Compose لا يبني صورة CUDA أبداً.**
  - **الواقع المقيس:** ملفّ الكرت لا يحمل `build` (AC‑5.5)، وملفّ البناء يضيف `build` لـ`embedding` أيّاً كان ترتيب الملفّات. إذن `yml:gpu:build` يعطي حتماً اسم CUDA مع بناء المعالج. قيس ذلك بـ`config`: الصورة `aizzak/embedding-cuda:dev`، و`build` بلا `args`.
  - **ولا مخرج داخل Compose:** الاستيفاء يقرأ البيئة وحدها لكلّ ملفٍّ على حدة، فلا يعرف ملفٌّ أنّ الآخر حاضر.
  - **العلاج في طبقتين:**
    1. **المصدر الوحيد لصورة CUDA** هو `build-push.sh` (الإصدار، يختبره AC‑4.4)، أو أمر `buildx` واحد موثَّق للإثبات المحلّيّ (§3.12، الصف 43).
    2. **ملفّ الكرت يضبط `EMB_REQUIRE_CUDA: "1"`** (Q‑10). فصورة معالجٍ تحمل اسم CUDA ترفض الإقلاع برسالةٍ تقول إنّ torch فيها مبنيٌّ للمعالج (`torch.version.cuda is None`)، ولا تعمل على المعالج بصمت. وهذا أثر R‑6 بالضبط.
  - **والوثائق ورأس ملفّ الكرت** تقول: لا يُركَّب مع ملفّ البناء.
  - **الأثر:** صياغةٌ جديدة لـAC‑5.7 (§5.4). و`docker-compose.build.yml` لا يتغيّر.
- **D‑2: صورة CUDA بمتغيّرين خاصّين بها.** ‏`${EMBEDDING_CUDA_IMAGE_REPO:-aizzak/embedding-cuda}:${EMBEDDING_CUDA_IMAGE_TAG:-dev}` في ملفّ الكرت.
  - `.env` الخادم يحمل متغيّرات الصورتين معاً، و`COMPOSE_FILE` وحده يختار بينهما. فالتبديل بين الكرت والمعالج سطرٌ واحد.
  - هذا يحلّ محلّ ملاحظة D‑1 في المرحلة 1: «التبديل بقيمة `EMBEDDING_IMAGE_TAG` وحدها». السبب أنّ AC‑5.3 يطلب أن يسمّي ملفُّ الكرت الصورةَ بنفسه.
  - الاسم المحلّيّ الافتراضيّ غير اسم صورة المعالج وغير `latest` (A‑3). فخادمٌ نسي المتغيّرين يفشل في السحب، ولا يشغّل torch المعالج.
- **D‑3: فحص صحّة `ollama` بـ`/dev/tcp` في bash، على `/api/version`.**
  - **مقيس:** صورة `ollama/ollama:0.13.5` هي `ubuntu:24.04` ومعها `ca-certificates` و`libvulkan1` وملفّ `ollama`. فيها `bash` و`sh`، وليس فيها `curl` ولا `wget`. سُرد ذلك من طبقاتها الثلاث الصغيرة (0 و1 و2).
  - فاتُّبع نمط `qdrant` حرفيّاً، وهو أيضاً ما تسمّيه الخطّة (`server-gpu-plan.md:203`).
  - **رُفض `ollama list`:** يعمل أيضاً، لكنّه يقرأ النماذج من القرص، ويفتح نمطاً ثانياً لصورٍ بلا أدوات.
  - **ورُفض `ollama -v`:** يخرج بـ0 والخادم ميّت.
- **D‑4: `ollama-pull` صورة `python:3.12-slim` تشغّل `deploy/ollama/pull_models.py`.**
  - صورة Ollama بلا Python ولا `jq`. وقراءة `PROVIDER_ROUTING` تحتاج JSON صارماً، لأنّ AC‑2.3 (هـ) يطلب رفض JSON غير صالح.
  - الوجود يُفحص بـ`POST /api/show`، والسحب لا يحدث إلّا للغائب. السبب أنّ `ollama pull` على نموذجٍ موجود يطلب البيان من السجلّ، فيفشل والشبكة مقطوعة (NFR‑10).
  - الصورة نفسها مستعملةٌ في `alert-sink`. والملفّ الجديد خارج Q‑8، فهو ⚠️ 1.
- **D‑5: لا سطر `OLLAMA_BASE_URL` مفعّل في `.env.example`.**
  - القيمة الافتراضيّة في Compose (`http://ollama:11434`) مصدرٌ واحد، وتطابق القيمة الافتراضيّة في التطبيق (`src/app/infrastructure/config/env_settings.py:163`).
  - سطرٌ مفعّل مكرَّر يحتاج حارساً لاتّساق القيمتين. وسطر الجسر لـWSL معلَّقٌ مع شرحه (AC‑2.4).
- **D‑6: كلّ حافّةٍ إلى `ollama` أو `ollama-bridge` تحمل `required: false`، ومنها حافّة `ollama-pull` إلى `ollama`.**
  - الثابت واحدٌ وبسيط: لا شيء يشترط Ollama. وهو يستوفي AC‑2.5 حرفيّاً.
  - **مقيس بـ`config`:** حافّةٌ بـ`required: false` إلى خدمةٍ معطَّلة بـprofile لا تُفشل `config`، وتبقى في المخرج المحلول.
  - **التبعة:** إن فشل `ollama` في الإقلاع، يحذّر Compose ويشغّل `ollama-pull`، فيخرج بالرمز 3 «Ollama unreachable». أي فشلٌ صاخب.
  - حواف `app` والعمّال إلى `ollama` بـ`service_healthy`: الحجّة التي أبقت حافّة الجسر `service_started` («خدمةٌ أصليّة خارج دورة حياة Compose») لا تنطبق على خدمةٍ داخله. و`required: false` يمنع أن يحبس عطلُها المكدّس.
- **D‑7: `OLLAMA_NUM_PARALLEL` يساوي `OLLAMA_MAX_CONCURRENCY` في القيمة الافتراضيّة (2)، ويحرس المساواةَ اختبار.**
  - الحارس لكلّ عمليّة، والعمّال بلا سقف، فالمكدّس قد يرسل أكثر من 2 بكثير (§5.6 في `requirements.md`). وOllama هو الطابور: الزائد ينتظر في `OLLAMA_MAX_QUEUE` (512 افتراضيّاً).
  - ذاكرة الكرت يحدّدها `NUM_PARALLEL × num_ctx`، والطابور يحدّد الزمن وحده.
  - المساواة اختيارٌ مكتوب: عمليّة API واحدة عند سقفها تملأ خانات Ollama بالضبط. وتغيير أحدهما يُسقط الاختبار، فيُتّخذ القرار عن وعي (§3.10).
- **D‑8: لا `count` ولا `device_ids` في الحجز.**
  - **مقيس:** Compose يحلّ الغياب إلى `count: -1`، أي كلّ الكروت.
  - الخادم المستهدف بكرتٍ واحد (قرار 2). وتثبيت الكروت على خادمٍ متعدّد يُقاس في 7.3، ويُكتب في §2‑ي.
- **D‑9: حدّ ذاكرة `embedding` في ملفّ الكرت `3g` قيمةٌ ابتدائيّة لا قياس (FR‑19).** القاعدة التي تستبدلها مكتوبةٌ في §3.8، وتطبَّق في AC‑4.11.
- **D‑10: `gpu-preflight.sh` يشغّل الفحوص الثابتة كلّها ويطبع سطراً لكلٍّ منها، ثمّ يخرج بأعلى الرموز أسبقيّة: 11 ثمّ 13 ثمّ 10 ثمّ 12.**
  - أعطال المنصّة (Docker وCompose) تسبق أعطال الكرت، لأنّها تمنع التشغيل على المعالج أيضاً. فالرمز 10 يعني بالضبط: «لا كرت، والتشغيل على المعالج ممكن». وهذا ما يحتاجه 5.2.
  - **حافّة CDI وDocker Desktop (سؤال القصّة US‑1):** نعم، نجاح `--smoke` يغلب غياب وقت التشغيل `nvidia`. السبب أنّ Docker يحلّ `--gpus` وحجز Compose بمسار الـhook أو CDI، لا بتسجيل وقت التشغيل.
  - دون `--smoke`: الرمز 12 مع نصيحة «جرّب `--smoke` إن كان المضيف بـCDI أو Docker Desktop».
- **D‑11: صورة الفحص الحيّ `nvidia/cuda:12.4.1-base-ubuntu22.04`.**
  - **مقيس:** الوسم منشور، وبصمة الفهرس `sha256:0f6bfcbf267e65123bcc2287e2153dedfc0f24772fb5ce84afe16ac4b2fada95`، و92 م.ب مضغوطة.
  - شرطها `NVIDIA_REQUIRE_CUDA=cuda>=12.4`، مع فروع 470 و525 و535. فنجاحها يعني أنّ التعريف يكفي عجلات `cu124`.
  - الوسم وحده في السكربت، والبصمة في تعليق رأسه.
- **D‑12: `release.yml` لا يتغيّر.**
  - السكربت هو منطق الإصدار الوحيد، ويقرأ قائمة صوره من ثوابته.
  - مهلة 60 دقيقة، والمساحة 86 ج.ب حرّة (NFR‑8). تقديري للصورة الثالثة: عجلات نحو 3 ج.ب مضغوطة، وصورة نحو 8–9 ج.ب محلّيّاً. تبقى مساحةٌ تفوق 20 ج.ب، ويُقاس ذلك في AC‑4.7.
  - خطوة «Runner disk» (`release.yml:113-119`) تفرّغ وحدها إن نقص الحرّ.
- **D‑13: أوامر ملفّ الكرت في `docs/` تُكتب بصيغة `COMPOSE_FILE=…` لا بـ`-f`.**
  - الاختبار `test_no_documented_explicit_f_up_build_or_run_omits_the_overlay` (`tests/unit/test_prebuilt_images_docs.py:87-101`) يرفض أيّ سطر `docker compose … -f docker-compose… up|build|run` في `docs/` لا يذكر `docker-compose.build.yml`.
  - وD‑1 يمنع تركيب ملفّ البناء مع ملفّ الكرت.
  - فالأوامر بـ`-f` (المشاريع المنفصلة في الإثباتات) تسكن `docs/delivery/` وحدها، وهي مستثناة من ذلك الاختبار.
- **D‑14: الجهاز يقرّره `_choose_device()` وحده، ولا يختاره متغيّر بيئة.**
  - المحمِّل يضع النموذج عليه، و`/health` يذكره. فلا يمكن أن يختلف المذكور عن المستعمل.
  - المتغيّر `EMB_REQUIRE_CUDA` **يرفض** جهازاً ولا **يختاره**. قيمه المقبولة: غائب، أو `""`، أو `"0"`، أو `"1"`. أيّ قيمةٍ أخرى ترفض الإقلاع، لأنّ خطأً إملائيّاً لا يجوز أن يطفئ حارساً بصمت.
- **D‑15: جدول ذاكرة الكرت في `08-local-runbook.md` §2‑ي** (A‑7)، بعد §2‑ط، ومعه سطر إحالةٍ في آخر §2‑ز. الحرف التالي في الترتيب هو ي.
- **D‑16: ملفّ البناء لا يتغيّر،** فاختباره `test_compose_build_overlay.py` لا يتغيّر أيضاً. صورتا `ollama` و`ollama-pull` ليستا صورتي المنصّة، فعدّ الخدمات المبنيّة يبقى 10.

### 3.2 `docker-compose.yml`: Ollama داخل المكدّس (2.2)

**`x-app-env`، السطر `:201`:**

```yaml
  # server-gpu-plan.md 2.2: the in-stack `ollama` service (profile
  # `local-llm`) -- the same default the app's own settings carry
  # (env_settings.py). This WSL machine's .env points at the bridge on :11435
  # (profile `wsl-ollama`); any Ollama outside Compose is an absolute URL in .env.
  OLLAMA_BASE_URL: ${OLLAMA_BASE_URL:-http://ollama:11434}
```

**`x-app-env`، السطر `:258`:** مرساةٌ على القيمة نفسها، دون تغييرٍ في النصّ:

```yaml
  PROVIDER_ROUTING: &provider-routing ${PROVIDER_ROUTING:-{"llm":{"default":{"provider":"ollama","model":"gemma3:1b"}},"embedding":{"default":{"provider":"embedding-local","model":"sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"}}}}
```

**تعليق `x-host-gateway` (`:303-312`):** الجملة الأولى تصير:

> `host.docker.internal` -- which this WSL machine's .env names for the bridge (:11435), as does any host whose Ollama lives outside Compose -- is a Docker DESKTOP convenience...

والباقي كما هو، لأنّ الحاجة باقية.

**الخدمتان الجديدتان:** بعد `ollama-bridge` في قسم «app plane»:

```yaml
  # ── Ollama INSIDE the stack (server-gpu-plan.md 2.2) ──────────────────────
  # The platform's local LLM as a Compose service, for every host without an
  # Ollama of its own: a server sets COMPOSE_PROFILES=local-llm, and
  # OLLAMA_BASE_URL's default (x-app-env) already names this service. Behind
  # a profile because THIS machine keeps its native WSL Ollama (ollama-bridge
  # above, profile wsl-ollama): a default `up -d` starts neither, and each host
  # opts into exactly one.
  #
  # INTERNAL ONLY -- `expose`, never `ports`: Ollama has no authentication, so
  # it gets the embedding service's trust boundary exactly.
  # CPU here; docker-compose.gpu.yml adds the GPU reservation.
  ollama:
    profiles: ["local-llm"]
    deploy:
      resources:
        limits:
          # ⚠️ ESTIMATES, measured by gpu-compose AC‑2.7 (CPU) and AC‑6.4 (GPU):
          # gemma3:1b is 0.8 GB of weights, plus the KV cache for
          # OLLAMA_NUM_PARALLEL x num_ctx 8192, plus compute buffers. Outside the
          # standing ledger (profiled, 08 §2‑ز); a server's budget is step 3.4.
          cpus: "4.0"
          memory: 4g
    # The native WSL Ollama's own version (Q‑5), so gemma3:1b behaves as measured
    # here. Index digest on 2026-10-14:
    # sha256:2c9595c555fd70a28363489ac03bd5bf9e7c5bdf2890373c3a830ffd7252ce6d
    image: ollama/ollama:0.13.5
    environment:
      # 2.6. ONE model resident: loading another evicts this one instead of
      # sitting beside it in VRAM (08 §2‑ي).
      OLLAMA_MAX_LOADED_MODELS: ${OLLAMA_MAX_LOADED_MODELS:-1}
      # Requests one loaded model serves at once; the rest wait in Ollama's own
      # queue (OLLAMA_MAX_QUEUE, 512). EQUAL to OLLAMA_MAX_CONCURRENCY's default
      # on purpose, and NOT because the stack sends at most this many: that
      # guard is per API process (twelve of them; the workers run unguarded), so
      # Ollama is the queue. VRAM grows with NUM_PARALLEL x num_ctx; the queue
      # costs latency, not memory. Move both together and re-measure 08 §2‑ي
      # (tests/unit/test_compose_local_llm.py).
      OLLAMA_NUM_PARALLEL: ${OLLAMA_NUM_PARALLEL:-2}
    volumes:
      - ollama-models:/root/.ollama
    expose:
      - "11434"
    # THE SHAPE IS FORCED BY THE IMAGE (measured): ubuntu:24.04 plus the ollama
    # binary -- no curl, no wget. bash's /dev/tcp, the qdrant precedent above.
    healthcheck:
      test:
        [
          "CMD",
          "bash",
          "-c",
          "exec 3<>/dev/tcp/127.0.0.1/11434 && printf 'GET /api/version HTTP/1.1\\r\\nHost: localhost\\r\\nConnection: close\\r\\n\\r\\n' >&3 && grep -q '^HTTP/1.1 200 OK' <&3",
        ]
      interval: 10s
      timeout: 5s
      retries: 6
      start_period: 30s
    restart: unless-stopped
    <<: *app-logging

  # One-shot: pull EXACTLY the Ollama models PROVIDER_ROUTING routes to, and
  # never one already present -- a second run contacts no registry and works
  # offline. A stock Python image running one standard-library file (the
  # alert-sink precedent): nothing to build, read-only, unprivileged. Nothing
  # waits for it: a chat before the first pull ends answers 502, it does not
  # hold the boot. Re-run with `docker compose run --rm ollama-pull`.
  ollama-pull:
    profiles: ["local-llm"]
    deploy:
      resources:
        limits:
          cpus: "0.25"
          memory: 64m
    image: python:3.12-slim
    user: "65534:65534"
    read_only: true
    depends_on:
      ollama:
        condition: service_healthy
        required: false
    environment:
      # ALWAYS the in-stack service, never the shared OLLAMA_BASE_URL: on a host
      # with an outside Ollama that one points somewhere this must not write.
      OLLAMA_BASE_URL: http://ollama:11434
      # The value `app` resolves (the anchor in x-app-env), so the models pulled
      # are the models routed -- LLM_FALLBACK_ROUTE included, since it may only
      # name a route of this same table.
      PROVIDER_ROUTING: *provider-routing
    volumes:
      - ./deploy/ollama/pull_models.py:/pull/pull_models.py:ro
    command: ["python", "-u", "/pull/pull_models.py"]
    restart: "no"
    <<: *app-logging
```

**لماذا لا يُدمج `*app-env` في `ollama-pull`:** فيه `VAULT_SECRET_ID` و`VAULT_TOKEN` وعناوين قواعد البيانات. وخدمةٌ تقرأ جدول توجيهٍ لا تحتاج شيئاً منها (مبدأ الامتياز الأدنى). وفوق ذلك، `test_connection_budget.py:340-352` يعدّ كلّ خدمةٍ تحمل `DATABASE_URL`.

**الحجم:** في `volumes:` العليا (`:3236`):

```yaml
  # Ollama's models (2.2): gemma3:1b is ~0.8 GB, pulled once by ollama-pull.
  # Losing it costs a re-download, never data.
  ollama-models:
```

**الحافّة إلى `ollama`** في `x-worker-service.depends_on` (`:340`) وفي `app.depends_on` (`:1669`)، بجوار حافّة الجسر (§3.3):

```yaml
    ollama:
      condition: service_healthy
      required: false      # 2.2: profile local-llm -- absent on this WSL machine
```

### 3.3 الجسر والحواف (2.3)

**`ollama-bridge` (`:1610`):** يُضاف `profiles: ["wsl-ollama"]` سطراً أوّل، وبقيّة الخدمة حرفيّاً كما هي (AC‑3.1). وفي تعليقها (`:1588-1609`) تصير الجملة الأخيرة:

> Behind profile `wsl-ollama` (server-gpu-plan.md 2.3): this machine's .env carries `COMPOSE_PROFILES=wsl-ollama`; a server never starts it, and its default is the in-stack `ollama` service (profile `local-llm`).

وفي فقرة «📌 NOTHING gates on this» تُضاف: «and `required: false`, so a host without the profile boots without it».

**الحواف الأربع** (`app` و`x-worker-service` الذي تدمجه `worker-memory` و`worker-knowledge` و`worker-media`):

```yaml
    ollama-bridge:
      condition: service_started
      required: false      # 2.3: profile wsl-ollama -- absent on a server
```

**الحساب الناتج** (عُدّ بدالّتَي `test_resource_budget.py` نفسيهما، `_classify` و`_totals`):

| | الدائمة | لمرّةٍ واحدة | بـprofile | `standing_cpus` | `standing_memory_gb` |
|---|---|---|---|---|---|
| `master` ‏`1814998` | 29 | 5 | 3 | 36.10 | 57.53 (57.5312) |
| بعد C3 (2.2) | 29 | 5 | 5 | 36.10 | 57.53 |
| بعد C4 (2.3) | **28** | 5 | **6** | **35.60** | **57.28** (57.2812) |

**مقيسٌ بـ`config`** في مجلّدٍ مؤقّت، بملفّاتٍ مصغّرة بالشكل نفسه:
- بلا profile: لا الجسر ولا `ollama`.
- بـ`COMPOSE_PROFILES=wsl-ollama` من `.env`: الجسر وحده.
- بـ`--profile local-llm`: ‏`ollama` و`ollama-pull`.
- **وأمرٌ بـ`--profile backup` ومعه `COMPOSE_PROFILES=wsl-ollama` في `.env`: الجسر غائب.** قيمة الخيار تحلّ محلّ المتغيّر، حتّى المقروء من `.env`. هذا يغيّر إجراء AC‑7.1 (§5.4)، وهو R‑5.
- وعلى `master` اليوم: 34 خدمة بلا profile، و35 بـ`--profile backup`، وحافّة الجسر تُطبع `"required": true`.

### 3.4 `deploy/ollama/pull_models.py` (⚠️ 1)

**مكتبة Python القياسيّة وحدها:** ‏`json` و`urllib.request` و`os` و`sys` و`time`، على نمط `sink.py`. ويمرّ بـ`ruff` كأيّ ملفّ Python في المستودع.

```python
"""ollama-pull (server-gpu-plan.md 2.2): pull every Ollama model PROVIDER_ROUTING
routes to into the in-stack `ollama` service -- and nothing already there, so a
re-run contacts no registry and works offline. Standard library only: it runs on
a stock python:3.12-slim image (docker-compose.yml, service `ollama-pull`).

Exit codes: 0 done (including "no route uses ollama") · 2 PROVIDER_ROUTING is
not usable · 3 Ollama unreachable · 4 a pull failed (the model is named).
"""

OLLAMA_PROVIDER = "ollama"

class RoutingError(ValueError): ...   # -> exit 2
class OllamaError(RuntimeError): ...  # -> exit 3 (unreachable) or 4 (pull failed)

def ollama_models(routing_json: str) -> list[str]:
    """Every distinct `model` whose `provider` is exactly "ollama", across every
    namespace and route, sorted. Invalid JSON, a non-object namespace or entry,
    or a non-string/empty provider or model raises RoutingError naming the
    offending path (e.g. `llm.summarize`). The value itself is never echoed."""

def is_present(base_url: str, model: str, *, timeout_s: float = 10.0) -> bool:
    """POST {base}/api/show {"model": model}: 200 -> True, 404 -> False,
    anything else -> OllamaError. Reads the local manifest only."""

def pull(base_url: str, model: str, *, read_timeout_s: float = 600.0) -> None:
    """POST {base}/api/pull {"model": model, "stream": true}. One output line
    per change of `status`; an `error` line, an HTTP error, or a stream that
    ends without `"status": "success"` raises OllamaError."""

def wait_reachable(base_url: str, *, within_s: float) -> None:
    """GET {base}/api/version until 200, once a second; OllamaError after
    `within_s` (OLLAMA_PULL_CONNECT_WAIT_S, default 30)."""

def main(environ: Mapping[str, str] = os.environ) -> int: ...
```

**التسلسل في `main`:**
1. `ollama_models(environ["PROVIDER_ROUTING"])`، وعند `RoutingError` الرمز 2 ولا نداء HTTP.
2. قائمةٌ فارغة: «no route uses ollama -- nothing to pull» والرمز 0، ولا نداء HTTP.
3. `wait_reachable`، وعند الفشل الرمز 3.
4. لكلّ نموذج:
   - إن كان `is_present`: سطر `ollama-pull: gemma3:1b already present -- not contacting the registry`. هذا السطر هو دليل AC‑2.7.
   - وإلّا: `pull` ثمّ `is_present` للتحقّق. وعند الفشل الرمز 4 باسم النموذج.

**أسطر السجلّ** كلّها بالبادئة `ollama-pull:`، ولا تطبع البيئة.

**اختيار النماذج:**
- **كلّ** مدخلٍ مزوّده `ollama` في **كلّ** فضاء: ‏`default` و`local` و`summarize` و`cloud`، وأيّ مفتاحٍ آخر.
- `LLM_FALLBACK_ROUTE` لا يُقرأ. التطبيق يرفض الإقلاع ما لم يسمِّ مساراً محلّيّاً موجوداً في الجدول نفسه (`docker-compose.yml:259-264`)، فنموذجه مسحوبٌ بالقاعدة نفسها. هذا حرف حالة الحافّة في US‑2.

### 3.5 صورة CUDA: `requirements-cuda.txt` ووسيط Dockerfile (2.4)

**`services/embedding/requirements-cuda.txt` (جديد):**

```text
# Pinned dependencies for the embedding SERVICE image, CUDA variant
# (server-gpu-plan.md 2.4). The SAME pins as requirements.txt -- the one
# difference is the index line (tests/unit/test_embedding_cuda_image.py compares
# the two). Built ONLY with --build-arg EMBEDDING_REQUIREMENTS=requirements-cuda.txt,
# by deploy/release/build-push.sh (tag prefix embedding-cuda-) or the one local
# buildx command in docs/stack-commands.md -- never by docker-compose.build.yml.
# torch's cu124 wheels carry their own CUDA 12.4 runtime: the host provides the
# NVIDIA driver and NVIDIA Container Toolkit (deploy/gpu-preflight.sh checks both).
--extra-index-url https://download.pytorch.org/whl/cu124

torch==2.5.1
sentence-transformers==3.3.1
fastapi==0.115.6
uvicorn[standard]==0.32.1
pydantic==2.10.4
```

- **مقيس:** `torch-2.5.1+cu124-cp312-cp312-linux_x86_64.whl` منشورٌ في الفهرس، وحجمه 908 م.ب. وعجلة المعالج 175 م.ب.
- pip يختار `2.5.1+cu124` لأنّ الإصدار المحلّيّ أعلى من `2.5.1` في PEP 440، كما يختار `+cpu` اليوم.
- التحقّق بالنتيجة لا بالنصّ: `torch.version.cuda` في AC‑4.6.

**`services/embedding/Dockerfile`:** السطران `:26-27` يصيران كما يلي. الوسيط بعد طبقة `apt`، فتبقى تلك الطبقة مشتركةً بين البناءين:

```dockerfile
# Which pinned dependency set this image installs (server-gpu-plan.md 2.4):
# requirements.txt -- CPU torch, the default, so a build without the argument is
# byte-for-byte today's image -- or requirements-cuda.txt for the
# embedding-cuda-<v> release image (deploy/release/build-push.sh).
ARG EMBEDDING_REQUIREMENTS=requirements.txt
COPY services/embedding/${EMBEDDING_REQUIREMENTS} ./${EMBEDDING_REQUIREMENTS}
RUN pip install --no-cache-dir -r "${EMBEDDING_REQUIREMENTS}"
```

وفي تعليق الرأس سطرٌ واحد: «CPU by default; the CUDA variant is the same file with one build argument».

- قيمةٌ خاطئة للوسيط تُفشل `COPY`، فيفشل البناء صاخباً قبل أيّ رفع (الرمز 8).
- البناء دون الوسيط يطابق ذاكرة طبقات اليوم: الملفّ نفسه إلى الوجهة نفسها.

### 3.6 `services/embedding/app.py`: الجهاز والدقّة ورفض الإقلاع (backend-developer)

```python
_DTYPE_BY_DEVICE: Final[dict[str, str]] = {"cuda": "float16", "cpu": "float32"}
_REQUIRE_CUDA_VALUES: Final[dict[str, bool]] = {"": False, "0": False, "1": True}


@dataclass(frozen=True)
class _Device:
    """What `_choose_device` decided -- the ONE value `_load_model` places the
    model on and `/health` reports, so the two cannot disagree (D‑14)."""

    name: str                # "cuda" | "cpu"
    torch_cuda: str | None   # torch.version.cuda -- None on a CPU wheel (R‑6's tell)

    @property
    def dtype(self) -> str:  # "float16" on cuda, "float32" on cpu -- the policy below
        return _DTYPE_BY_DEVICE[self.name]


def _choose_device() -> _Device:
    """`cuda` iff torch.cuda.is_available(), else `cpu`. Lazy torch import (module
    docstring); the unit suite monkeypatches THIS, never imports torch."""


def _require_cuda(environ: Mapping[str, str] = os.environ) -> bool:
    """EMB_REQUIRE_CUDA: unset / "" / "0" -> False, "1" -> True; any other value
    raises RuntimeError naming the accepted values (a typo must not switch a
    guard off in silence). Set ONLY by docker-compose.gpu.yml (Q‑10)."""


def _refusal(device: _Device) -> str:
    """The startup error. torch_cuda is None -> "this image's torch is a CPU build
    (torch.version.cuda is None): it was built from requirements.txt, not
    requirements-cuda.txt". Otherwise -> "torch is built for CUDA <x> but no GPU
    reached this container: check the NVIDIA driver and Container Toolkit
    (deploy/gpu-preflight.sh) and the GPU reservation in docker-compose.gpu.yml"."""


def _load_model(model_name: str, max_seq_length: int, device: _Device) -> Encoder:
    """SentenceTransformer(model_name, device=device.name); .half() iff
    device.dtype == "float16"; max_seq_length applied LAST (unchanged)."""
```

**التغييرات الأخرى:**
- **`_ModelState`:** ‏`device: str | None = None`، و`dtype: str | None = None`.
- **`HealthResponse`:** الحقلان نفسهما بعد `max_batch_texts`.
- **`health()`:** يمرّر `device=_state.device, dtype=_state.dtype`.
- **docstring الوحدة**، فقرة «Device policy»: الجهاز ما زال آليّاً لا يُضبط من البيئة، والبيئة تستطيع أن **ترفض** جهازاً ولا أن **تختاره**.

**ترتيب `_lifespan`:**
1. قراءات البيئة الحاليّة، ومعها `require_cuda = _require_cuda()`. القيمة الخاطئة تُرفض قبل أيّ استيراد لـtorch.
2. تثبيت الخيوط، كما اليوم.
3. `device = _choose_device()`.
4. `if require_cuda and device.name != "cuda": raise RuntimeError(_refusal(device))`. وهذا **قبل** تحميل النموذج.
5. `_state.model = _load_model(model_name, max_seq_length, device)`.
6. `_state.device, _state.dtype = device.name, device.dtype`. وفي `finally` يعودان `None`.

**السلوك في الحاوية:**
- uvicorn يطبع «Application startup failed» ويخرج بالرمز 3.
- `restart: unless-stopped` يعيده، فتظهر الحلقة في `docker compose ps`، ولا يصير سليماً أبداً.
- و`embedding-lb` ينتظره بـ`service_healthy`، فيفشل `up` صاخباً.

**صورة RunPod تشغّل الملفّ نفسه** (`deploy/runpod/supervisord.conf:211-212`). فالحقلان يصلانها عند بنائها التالي، والمتغيّر مطفأٌ هناك. لا يتغيّر ملفٌّ تحت `deploy/runpod/` (AC‑7.7).

### 3.7 `deploy/release/build-push.sh`: الصورة الثالثة (2.4)

```bash
readonly IMAGES=(                    # "<tag prefix> <dockerfile> [<build arg NAME=VALUE>]"
  "app Dockerfile"
  "embedding services/embedding/Dockerfile"
  "embedding-cuda services/embedding/Dockerfile EMBEDDING_REQUIREMENTS=requirements-cuda.txt"
)
```

**التغييرات:**
- يُحذف `RESERVED_IMAGES` وحلقته في `list_images`. فيطبع `--list-images` المدخلات الثلاثة كما هي، ثلاثة أسطر بلا `reserved` (AC‑4.4).
- كلّ حلقةٍ تقرأ المدخل بـ`read -r prefix dockerfile build_arg <<<"$entry"`.
- `build_image <prefix> <dockerfile> <version> <sha> [<build-arg>]` يضيف `--build-arg "<build-arg>"` قبل `--file` إن لم يكن فارغاً. نداء الصورة الثالثة بالضبط:

```bash
docker buildx build --platform linux/amd64 --pull --load \
  --provenance=false --sbom=false \
  --build-arg EMBEDDING_REQUIREMENTS=requirements-cuda.txt \
  --file services/embedding/Dockerfile --tag "anastawahia/aizzak:embedding-cuda-<VERSION>" \
  --label "org.opencontainers.image.revision=<sha>" \
  --label "org.opencontainers.image.version=<VERSION>" \
  --label "org.opencontainers.image.title=aizzak-embedding-cuda" \
  --label "org.opencontainers.image.source=https://github.com/anastawahia/aizzak" \
  .
```

**ما لا يتغيّر:**
- `require_tag_absent` يمرّ على الوسوم الثلاثة.
- D‑5 للمرحلة 1 (كلّ البناء قبل أوّل رفع) يجعل فشل بناء الثالثة بلا رفعٍ أصلاً (الرمز 8).
- سطر النجاح يصير `release: OK <V> (3 images)` وحده (`${#PUSHED[@]}`).
- رموز الخروج كما هي.

**رأس السكربت:**
- صفّ `embedding-cuda` في جدول البادئات يصير مبنيّاً بالوسيط.
- جملة الطوارئ تقول: الصور الثلاث من وصلةٍ منزليّة نحو 14 ساعة أو أكثر (صورة CUDA نحو 3–3.5 ج.ب مضغوطة، تقدير)، وCI هو الطريق.
- قواعد النصّ باقية (AC‑3.8 للمرحلة 1): لا `.env` في الملفّ، ولا `docker login`.

### 3.8 `docker-compose.gpu.yml` (جديد، 2.5)

```yaml
# GPU overlay (server-gpu-plan.md 2.5): everything that needs an NVIDIA GPU, in
# ONE file a GPU server adds to COMPOSE_FILE -- leave it out and the same
# docker-compose.yml runs on CPU. Three services, and only these keys
# (tests/unit/test_compose_gpu_overlay.py):
#   ollama     the LLM on the GPU (only with COMPOSE_PROFILES=local-llm)
#   embedding  the CUDA image, ONE replica, on the GPU
#   app        `utility` only: nvidia-smi for the System Monitor page, no compute
#
#   COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml
#   COMPOSE_PROFILES=local-llm
#   EMBEDDING_CUDA_IMAGE_REPO=anastawahia/aizzak
#   EMBEDDING_CUDA_IMAGE_TAG=embedding-cuda-<YYYY.M.N>
#
# Prerequisites -- NVIDIA driver, NVIDIA Container Toolkit configured for
# Docker, Compose >= 2.20 -- are what deploy/gpu-preflight.sh checks
# (docs/deploy-server.md). Without the toolkit every service below fails to
# start with "could not select device driver".
#
# ⚠️ NEVER together with docker-compose.build.yml: that overlay builds
# `embedding` from requirements.txt (CPU torch) and would tag the result with
# the CUDA name below. The CUDA image comes from deploy/release/build-push.sh
# (or the one buildx command in docs/stack-commands.md), and EMB_REQUIRE_CUDA
# makes a CPU build under this name refuse to start rather than run slowly.
#
# Not for this WSL development machine before the toolkit is installed: that
# is docker-compose.wsl-gpu.yml, and the two are never combined.
#
# No `count`/`device_ids`: every GPU on the host is visible to both services;
# the target server has one (decision 2). Pinning devices on a multi-GPU host
# is measured in 7.3 and recorded in 08-local-runbook §2‑ي.
services:
  ollama:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              capabilities: [gpu]

  embedding:
    # Named only, never built here (above). Its own local default -- never the
    # CPU image's name, never a floating tag -- so a GPU host that forgot the
    # two variables fails to pull instead of running CPU torch.
    image: ${EMBEDDING_CUDA_IMAGE_REPO:-aizzak/embedding-cuda}:${EMBEDDING_CUDA_IMAGE_TAG:-dev}
    deploy:
      # ONE process on the GPU, fed by the batcher (capacity 4.3); three would
      # hold three CUDA contexts and three copies of the model (08 §2‑ي).
      # embedding-lb stays in front of it unchanged.
      replicas: 1
      resources:
        limits:
          # ⚠️ A STARTING VALUE, NOT A MEASUREMENT (FR‑19). The base file's 1280m
          # was measured for a CPU process; a CUDA process maps the CUDA libraries
          # and stages the fp32 weights in RAM before .half(). gpu-compose AC‑4.11
          # (15 minutes of 8 concurrent /embed, batches of 8 Arabic chunks) keeps
          # it if peak x 1.5 <= 3g, else replaces it with peak x 1.79 rounded up
          # to 256m -- the base file's own margin.
          memory: 3g
        reservations:
          devices:
            - driver: nvidia
              capabilities: [gpu]
    environment:
      # Q‑10 (owner, 2026-10-14): refuse to start without CUDA. A missing toolkit,
      # an old driver or a CPU build under this name would otherwise run on the
      # CPU in silence -- the same vectors, far slower.
      EMB_REQUIRE_CUDA: "1"

  app:
    # `utility` and nothing else: the toolkit mounts nvidia-smi and NVML so
    # system_stats.py can read the card -- the server-side replacement for the
    # WSL overlay -- and no CUDA context is granted.
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              capabilities: [utility]
```

**مقيسٌ بـ`config`،** بملفٍّ بهذا الشكل فوق ملفٍّ أساسيٍّ مصغّر:
- `config -q` برمز 0 دون الأداة.
- `embedding`: ‏`replicas: 1`، و`limits: {cpus: 1, memory: 3221225472}`. فـ`cpus` الأساسيّ باقٍ، والذاكرة استُبدلت.
- البيئة مدموجة: `EMB_BATCH` الأساسيّ مع `EMB_REQUIRE_CUDA`.
- الحجز `count: -1`.
- مدخل `ollama` بلا أثرٍ دون `local-llm`، فلا تظهر الخدمة. هذه حافّة «خادمٌ بكرت وOllama خارجيّ».

**ما لا يحمله الملفّ:** نصّ `/dev/dxg` و`/usr/lib/wsl` و`LD_LIBRARY_PATH` و`:latest`، ولا حتّى في التعليقات، لأنّ AC‑5.4 وAC‑5.5 يبحثان في النصّ كلّه.

### 3.9 `deploy/gpu-preflight.sh` (2.1)

**الاستعمال:**

```
deploy/gpu-preflight.sh [--smoke]
deploy/gpu-preflight.sh --help
```

لا متغيّر بيئةٍ يُقرأ، ولا `.env`، ولا root.

**الثوابت:**

```bash
readonly MIN_COMPOSE="2.20.0"                                  # depends_on.required
readonly SMOKE_IMAGE="nvidia/cuda:12.4.1-base-ubuntu22.04"      # D‑11; index digest in the header
readonly PROBE_TIMEOUT_S=5                                      # per external call (NFR‑11)
```

**الفحوص بالترتيب:** كلّها تعمل ويُطبع لكلٍّ سطر. وكلّ نداءٍ خارجيّ داخل `timeout "$PROBE_TIMEOUT_S"`.

| # | الفحص | الأمر بالضبط (تطابقه بدائل الاختبار) | ينجح إن |
|---|---|---|---|
| 1 | التعريف | `nvidia-smi --query-gpu=name,driver_version --format=csv,noheader` | `nvidia-smi` على `PATH` (لا مسار مفترض: على WSL هو `/usr/lib/wsl/lib`)، والرمز 0، وسطرٌ واحد على الأقلّ. تُطبع **كلّ** الكروت |
| 2 | Docker | `docker info --format '{{.ServerVersion}}\|{{json .Runtimes}}'` | الرمز 0. وإن ذكر الخطأ `permission denied` فالرسالة تقول ذلك نصّاً (لا «الأداة غائبة») |
| 3 | الأداة | (من مخرج 2) | المفتاح `"nvidia"` في `Runtimes`. يُتخطّى إن فشل 1 أو 2 |
| 4 | Compose | `docker compose version --short` | المخرج يطابق `^v?([0-9]+)\.([0-9]+)\.([0-9]+)` (اللاحقة مثل `-desktop.1` مقبولة)، و(الرئيسيّ، الفرعيّ، الترقيع) ≥ (2، 20، 0) بمقارنة أعداد |
| 5 | `--smoke` فقط | `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi --query-gpu=name --format=csv,noheader` | الرمز 0. يعمل إن نجحت 1 و2 و4، سواء نجحت 3 أم لا (D‑10) |

**رموز الخروج:** في رأس السكربت، ويطابقها `docs/deploy-server.md` حرفيّاً (AC‑1.9):

| الرمز | المعنى | ما يفعله 5.2 |
|---|---|---|
| 0 | جاهز لـ`docker-compose.gpu.yml` | وضع الكرت |
| 2 | استعمالٌ خاطئ | — |
| 10 | لا كرت صالح (`nvidia-smi` غائب أو يفشل)، وDocker وCompose سليمان | وضع المعالج، دون ملفّ الكرت |
| 11 | Docker غير صالح: لا CLI، أو الخادم لا يجيب، أو `permission denied` على المقبس | توقّف |
| 12 | كرتٌ موجود، وNVIDIA Container Toolkit غير مضبوطة لـDocker (لا وقت تشغيل `nvidia`، أو فشل `--smoke` معها) | توقّف، أو وضع المعالج باختيار |
| 13 | Docker Compose غائب أو أقدم من 2.20.0 | توقّف |
| 14 | `--smoke`: الفحوص الثابتة نجحت، وحاوية الكرت فشلت (ومعها رسالة Docker، ومنها فشل السحب) | توقّف |

- **الأسبقيّة** عند أكثر من فشل: 11، ثمّ 13، ثمّ 10، ثمّ 12 (D‑10). والرمز 1 يعني خطأً غير متوقّع (`set -e`).
- **النصيحة للرمز 12** تُطبع ولا تُنفَّذ:
  `fix: sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker (docs/deploy-server.md)`.
  وبعدها: `on a CDI-only or Docker Desktop host, run again with --smoke`.

**المخرج المتوقّع على هذا الجهاز اليوم (AC‑1.7)،** والرمز 12:

```
gpu-preflight: driver   OK    NVIDIA GeForce RTX 4060 Laptop GPU, 592.82
gpu-preflight: docker   OK    server 29.6.1
gpu-preflight: toolkit  FAIL  Docker has no `nvidia` runtime
gpu-preflight:                fix: sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker (docs/deploy-server.md)
gpu-preflight:                on a CDI-only or Docker Desktop host, run again with --smoke
gpu-preflight: compose  OK    5.3.1 (>= 2.20.0)
gpu-preflight: FAIL 12 (NVIDIA Container Toolkit)
```

**قواعد النصّ** (يفحصها AC‑1.1 وAC‑1.6):
- السطر الأوّل `#!/usr/bin/env bash`، وأوّل سطرٍ غير تعليقٍ `set -euo pipefail`.
- لا تظهر السلسلة `.env` في الملفّ.
- لا `set -x`، ولا `env`، ولا `printenv`.
- أسماء `apt-get` و`yum` و`dnf` و`systemctl` و`nvidia-ctk` و`sudo` لا تظهر إلّا داخل نصّ النصيحة. والاختبار يثبت بالسلوك أنّها لا تُستدعى.

### 3.10 ميزانيّة ذاكرة الكرت: `08-local-runbook.md` §2‑ي (2.6)

**القسم الجديد** «2‑ي ميزانيّةُ ذاكرة الكرت — ما يسكن الكرت، وكم (gpu-compose · الخطوة 2.6)»، بعد §2‑ط. فيه:

1. **علاقة المتغيّرات (D‑7)،** بنصّ تعليق `OLLAMA_NUM_PARALLEL` نفسه.
2. **الجدول التالي،** بقيمٍ أوّليّة. كلّ «تقدير» يُستبدل بقياس ش‑3 (حدٌّ أدنى على 8 ج.ب) ثمّ ش‑5 (الكرت المستهدف):

| المستهلك | الصيغة | القيمة | المصدر | الحالة |
|---|---|---|---|---|
| أوزان `gemma3:1b` | ملفّ `Q4_K_M` | 815,319,791 بايت (777.5 م.ب ثنائيّة) | `/api/tags` (`requirements.md` §5.1) | مقيس |
| ذاكرة السياق | `OLLAMA_NUM_PARALLEL × num_ctx × 26 طبقة × 1 رأس KV × (256+256) × 2 بايت` = 2 × 8192 × 26,624 بايت | 436 م.ب (416 م.ب ثنائيّة)، **حدٌّ أعلى** | بنية النموذج من `/api/show` (‏26 كتلة، رأس KV واحد، طول 256، نافذة منزلقة 512)، و`num_ctx` من `ollama_llm.py:256` | تقدير محسوب. نافذة الطبقات المحلّيّة قد تجعله أصغر بكثير |
| ذاكرة الحساب في Ollama | مخازن ggml للدفعة | نحو 0.5 ج.ب | — | تقدير. يُقاس من `size` في `/api/ps` بعد محادثة، ناقصاً السطرين أعلاه |
| سياق CUDA لـOllama | عمليّة المشغّل | نحو 0.4 ج.ب | — | تقدير |
| أوزان التضمين بدقّة fp16 | نصف ملفّ الأوزان fp32 (نحو 470 م.ب) | نحو 0.23 ج.ب | — | تقدير |
| تنشيطات التضمين ومخصِّص torch | `EMB_BATCH=8` × 512 رمزاً | نحو 0.15 ج.ب | — | تقدير |
| سياق CUDA لـtorch | cuBLAS وcuDNN | نحو 0.5 ج.ب | — | تقدير |
| **مجموع الخادم** | | **نحو 2.9 ج.ب** | | تقدير |
| نسخةٌ ثانية مؤقّتة عند `rolling-deploy.sh --surge` (R‑10) | سياقٌ وأوزانٌ وتنشيطات | نحو +0.9 ج.ب مؤقّتاً | `deploy/rolling-deploy.sh:307-317` | تقدير |
| سطح مكتب Windows (هذا الجهاز وحده) | — | 866–926 م.ب | `nvidia-smi` في 2026‑10‑14 | مقيس |

**المجموع مقابل 90%:**

| الكرت | 90% من `memory.total` | الخادم | مع `--surge` | هذا الجهاز مع سطح المكتب |
|---|---|---|---|---|
| 8 ج.ب (8188 م.ب، هذا الجهاز) | 7369 م.ب | نحو 36% | نحو 47% | نحو 48%، ومع `--surge` نحو 58% |
| 16 ج.ب | نحو 14.7 ج.ب | نحو 18% | نحو 24% | — |
| 24 ج.ب | نحو 22.1 ج.ب | نحو 12% | نحو 16% | — |

3. **طريقة القياس** (ش‑3 وش‑5):
   - `nvidia-smi --query-gpu=memory.used` قبل أيّ خدمة، ثمّ بعد `embedding` سليماً، ثمّ بعد أوّل محادثة، ثمّ أعلى قيمة تحت حمل AC‑6.4.
   - و`size`/`size_vram` من `/api/ps`.
   - على WSL لا تظهر قائمة العمليّات، فالفروق هي القياس.
4. **ملاحظات:**
   - `OLLAMA_MAX_LOADED_MODELS=1` مع مسارين بنموذجين مختلفين يعني تبديلاً وإعادة تحميل.
   - `num_ctx` أكبر من 8192 شيفرة تطبيق، خارج النطاق.
   - نموذجٌ أكبر (قرار 3) يُعاد قياسه قبل اعتماده.
   - Ollama الأصليّ على هذا الجهاز يُفرَّغ من الكرت أثناء القياس (A‑6).
   - `/health` يذكر الآن `device`/`dtype`.

### 3.11 `.env.example`

**الكتلة العليا** (بعد `#EMBEDDING_IMAGE_TAG=…`، `:33`). كلّ سطرٍ يبدأ بـ`#`:

```bash
#
# GPU server (server-gpu-plan.md 2.5): the GPU overlay instead of the build one,
# and the CUDA embedding image of the SAME release. Never together with
# docker-compose.build.yml (the overlay's header says why).
#COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml
#EMBEDDING_CUDA_IMAGE_REPO=anastawahia/aizzak
#EMBEDDING_CUDA_IMAGE_TAG=embedding-cuda-2026.10.2

# ---------------------------------------------------------------------------
# Compose profiles (server-gpu-plan.md 2.2/2.3): where the LLM comes from.
# Left commented on purpose -- CI copies this file -- and note that a
# `--profile X` on a command line REPLACES this list instead of adding to it.
# This WSL machine: the bridge to the native Ollama (with OLLAMA_BASE_URL below).
#COMPOSE_PROFILES=wsl-ollama
# A server: Ollama in the stack, then `docker compose run --rm ollama-pull`.
#COMPOSE_PROFILES=local-llm
```

**كتلة Ollama (`:470-476`) تصير.** لا سطر مفعّل (D‑5):

```bash
# Ollama: the platform's sole local LLMProvider (DD-13, 2.8-a) -- no API key.
# Unset, it is http://ollama:11434: the in-stack `ollama` service behind the
# `local-llm` profile (server-gpu-plan.md 2.2). This WSL machine instead keeps
# its native Ollama and reaches it through the `ollama-bridge` forwarder
# (profile `wsl-ollama`), which listens on :11435:
#OLLAMA_BASE_URL=http://host.docker.internal:11435
# Any other Ollama (another host, a GPU Pod): its absolute URL here.
#
# 2.6 -- the in-stack service only. Defaults shown; NUM_PARALLEL equals
# OLLAMA_MAX_CONCURRENCY above on purpose (08 §2‑ي): move them together.
#OLLAMA_MAX_LOADED_MODELS=1
#OLLAMA_NUM_PARALLEL=2
```

**ما يبقى أخضر:**
- `test_env_example_offers_the_overlay_line_commented_only` (`test_compose_build_overlay.py:199-203`): لا `COMPOSE_FILE=` مفعّل، ولا `(APP|EMBEDDING)_IMAGE_(REPO|TAG)=` مفعّل.
- خطوة CI ‏`. ./.env` (`ci.yml:141-143`): الكتل تعليقٌ كلّها.

### 3.12 الوثائق

كلّها عربيّةٌ داخل `<div dir="rtl">`، والأوامر والمسارات بالإنجليزيّة، والمعرّفات بالشرطة غير المنكسرة، ولا قيمة سرّ. **وأوامر ملفّ الكرت بصيغة `COMPOSE_FILE=` لا `-f` (D‑13).**

- **`docs/deploy-server.md` (جديد):**
  - رأسٌ يقول إنّه هيكل يكمله 7.4.
  - **§1 المتطلّبات:**
    - تعريف NVIDIA: ‏`nvidia-smi` على المضيف، ورابط دليل NVIDIA لكلّ توزيعة.
    - NVIDIA Container Toolkit: رابط دليل التثبيت الرسميّ لـapt وdnf، ثمّ `sudo nvidia-ctk runtime configure --runtime=docker` ثمّ `sudo systemctl restart docker`.
    - Docker Engine وCompose plugin ≥ 2.20: روابطهما الرسميّة.
  - **§1.4 الفحص:** «السكربت يفحص ولا يثبّت»، و`deploy/gpu-preflight.sh [--smoke]`، وجدول رموز الخروج في §3.9 حرفيّاً.
  - **«قائمة الأمان المؤجّلة»:** جدولٌ برأسه وحده، فارغ (القاعدة 8).
- **`docs/stack-commands.md`:**
  - **الصفّ 1 والشرح §1:** سطرا المالك معاً: ‏`COMPOSE_FILE` من 1.1 كما هو، و`COMPOSE_PROFILES=wsl-ollama` على WSL مع Ollama الأصليّ.
    - و`--profile X` في أمرٍ يحلّ محلّ `COMPOSE_PROFILES`. فإمّا `--profile wsl-ollama` معه، أو `COMPOSE_PROFILES=wsl-ollama,X docker compose …`.
    - وبعد 4.1 يصير السطر قائمة: `wsl-ollama,monitoring`.
    - ونسيان السطر يُسقط الجسر، فيشتعل `AizzakLlmCircuitOpen` (R‑4).
  - **§37:** ملفّ الكرت هو النظير العامّ بعد تثبيت الأداة، ولا يُركَّب الملفّان معاً. والرابط المكسور `deploy-linux-server.md` يصير `deploy-server.md`.
  - **قسمٌ جديد «ز) الكرت على خادم»:**
    - **40:** `deploy/gpu-preflight.sh [--smoke]`.
    - **41:** `.env` الخادم: `COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml`، و`COMPOSE_PROFILES=local-llm`، ومتغيّرا صورة CUDA.
    - **42:** `docker compose run --rm ollama-pull`.
    - **43:** بناء صورة CUDA محلّيّاً للإثبات وحده: `docker buildx build --platform linux/amd64 --load --build-arg EMBEDDING_REQUIREMENTS=requirements-cuda.txt -f services/embedding/Dockerfile -t aizzak/embedding-cuda:dev .`
  - **§38–39:**
    - صفّ `embedding-cuda` في الجدول صار مبنيّاً.
    - و`.env` الخادم يضيف `EMBEDDING_CUDA_IMAGE_REPO`/`TAG`.
    - و«يبني الصور الثلاث قبل أوّل رفع».
    - وتقدير الطوارئ للصور الثلاث.
  - **سطر «آخر مطابقة»** يُحدَّث.
- **`docs/quickstart.md` §2.1:** بندٌ بعد خطوة `COMPOSE_FILE` وقبل أوّل `up -d`، ليبقى `test_quickstart_adds_compose_file_between_env_copy_and_first_up` أخضر:
  - على WSL مع Ollama الأصليّ: `COMPOSE_PROFILES=wsl-ollama` و`OLLAMA_BASE_URL=http://host.docker.internal:11435`.
  - وبلا Ollama أصليّ: `COMPOSE_PROFILES=local-llm`، ثمّ `docker compose run --rm ollama-pull` بعد الإقلاع.
- **`docs/runbooks/alerts.md`،** الخطوة 3 في `AizzakLlmCircuitOpen`:
  - «على جهاز WSL: `docker compose ps ollama-bridge` (غائبٌ إن نقص `COMPOSE_PROFILES=wsl-ollama` من `.env`)، ثمّ Ollama المضيف».
  - و«على خادم: `docker compose ps ollama` و`docker compose logs --since 15m ollama`، ثمّ `docker compose run --rm ollama-pull` إن ذكر السجلّ نموذجاً غائباً».
- **`deploy/prometheus/alerts.yml:1071-1073`:**
  `` `ollama`: on WSL `docker compose ps ollama-bridge` and the host's Ollama; on a server `docker compose ps ollama` and `docker compose logs ollama`. ``
- **`docs/design/08-local-runbook.md`:**
  - **الجدول `:30`:** صفّ `ollama-bridge` يذكر «profile `wsl-ollama` (جهاز المالك وحده)».
    - صفٌّ جديد `ollama` | `ollama/ollama:0.13.5` | 11434 داخليّاً | «النموذج اللغويّ داخل المكدّس، profile `local-llm`؛ وعلى الكرت بـ`docker-compose.gpu.yml`».
    - وتحت «خدمات لمرّةٍ واحدة»: `ollama-pull` | `python:3.12-slim` | — | «يسحب نماذج Ollama في `PROVIDER_ROUTING` ولا يعيد سحب موجود، profile `local-llm`».
  - **§2‑ز:**
    - الدفتر 35.60/57.28، مع «و36.10/57.53 قبل `gpu-compose` (2.3)».
    - والجملة عن الخدمات خلف `profiles:` تعدّ ستّاً.
    - وتعليق `--with-profiles` يعدّ ستّاً.
    - وفقرةٌ قصيرة: «⚠️ و`gpu-compose` (2.3) أخرجت الجسر من الدائمة. جهاز المالك ما زال يشغّله بسطر `COMPOSE_PROFILES`، لكنّه لا يُعدّ على المضيف المرجعيّ، لأنّ خادماً بلا WSL لا يشغّله».
    - وسطر إحالةٍ إلى §2‑ي.
  - **§3.0:** سطر تعليقٍ بعد `cp .env.example .env` يذكر `COMPOSE_PROFILES` في الحالتين.
  - **§5:**
    - صفٌّ لـ`ollama`: «`/dev/tcp` في bash إلى `/api/version`: الصورة بلا curl ولا wget».
    - وفقرة «📌 ولا شيء يعتمد على صحّة `ollama-bridge`» تضيف `required: false` والـprofile.
    - و«`ollama` يُنتظر بـ`service_healthy` و`required: false`».
  - **§2‑ي:** كما في §3.10.

### 3.13 سطر المالك والمقارنة بالبصمات (ش‑2، AC‑3.6 وAC‑7.1 وAC‑7.2)

**Q‑9 مجاب:**
- `OLLAMA_BASE_URL` مضبوطٌ على `:11435`.
- `COMPOSE_PROFILES` غائب.
- `COMPOSE_FILE` موجود (سطر 1.1).

فالسطر الوحيد الجديد: `COMPOSE_PROFILES=wsl-ollama`. ينفّذ المالك ما يلي بنفسه، والمخرج أسماءٌ وبصماتٌ فقط:

```bash
cd /home/AIZZAK
# The same function before and after. `--profile wsl-ollama` is harmless on master
# (no service has it) and REQUIRED after: a command-line --profile replaces .env's
# COMPOSE_PROFILES (measured), which would drop the bridge from the "after" side.
svc()  { docker compose --project-directory /home/AIZZAK "$@" config --services | sort | sha256sum; }
body() { docker compose --project-directory /home/AIZZAK "$@" config --format json | python3 -c '
import json, sys
s = json.load(sys.stdin)["services"]
s.get("ollama-bridge", {}).pop("profiles", None)
for n in ("app", "worker-memory", "worker-knowledge", "worker-media"):
    d = s[n].get("depends_on", {})
    d.get("ollama-bridge", {}).pop("required", None)
    d.pop("ollama", None)
print(json.dumps(s, sort_keys=True))' | sha256sum; }

git switch master                                  # BEFORE: .env untouched
svc; body; svc --profile backup --profile wsl-ollama; body --profile backup --profile wsl-ollama

git switch feat/gpu-compose                        # (or master after the merge)
env | grep -c '^COMPOSE_PROFILES='                 # must print 0: an exported value beats .env
grep -q '^COMPOSE_PROFILES=' .env || printf '\nCOMPOSE_PROFILES=wsl-ollama\n' >> .env
grep '^COMPOSE_PROFILES=' .env                     # not a secret; this one line only
svc; body; svc --profile backup --profile wsl-ollama; body --profile backup --profile wsl-ollama   # AFTER == BEFORE
```

**القائمة البيضاء W:** ما يحذفه `body`، وهي AC‑7.1 بلا `.embedding.build.args` (D‑16). وليس فيها مفتاح `environment` (AC‑7.2).

**بعد البصمات** (AC‑3.6):
1. `docker compose up -d`.
2. **أوّل `up -d` بعد الدمج يعيد إنشاء `app` بنسخه الثلاث، والعمّال الثلاثة، والجسر.** السبب أنّ إعدادها تغيّر (حواف `depends_on` وprofile). هذا انقطاعٌ قصيرٌ مرّةً واحدة، فيُختار له وقتٌ هادئ، أو يُستعمل `deploy/rolling-deploy.sh` لـ`app`.
3. `docker compose ps ollama-bridge` تقول `running (healthy)`.
4. محادثةٌ على المسار المحلّيّ تكتمل.

## 4. البيانات

**لا تغيير.** لا جداول، ولا ترحيل Alembic، ولا RLS، ولا أدوار. المرحلة 5 (data-engineer) تُتخطّى.

## 5. خطة الاختبار المقترحة

**الحزمة التي تُشغَّل:** اختبارات الوحدة كلّها تحت `tests/unit/`، وتجري ضمن `pytest tests/unit tests/architecture tests/eval` بلا مكدّسٍ حيّ (`CLAUDE.md`). لا اختبار تكامل.

**قواعد اختبارات `[config]`:**
- تستدعي `docker compose config` وحده، بـ`--env-file .env.example` و`-f` صريحة.
- تحذف `COMPOSE_FILE` **و`COMPOSE_PROFILES`** من البيئة (AC‑3.5).
- تتخطّى إن غاب Docker (`test_compose_build_overlay.py:34-45`).
- تجري في وظيفة `quality` على CI.

### 5.1 الاختبارات القائمة التي تتغيّر

| الملفّ | التغيير | الالتزام |
|---|---|---|
| `tests/unit/test_healthcheck_coverage.py` | `_ONE_SHOT` يضمّ `"ollama-pull"`، وتعليقه: «profile `local-llm`، عقده رمز الخروج». يبقى `test_one_shot_services_are_actually_one_shot` أخضر بـ`restart: "no"` | C3 |
| `tests/unit/test_healthcheck_coverage.py` | `test_the_bridge_is_probed_end_to_end_but_gates_nothing` (`:165-180`) يضيف `assert condition.get("required") is False`، وأنّ الحواف إلى الجسر أربعٌ بالضبط (`app` والعمّال)، ويحدّث نصّ الشرح | C4 |
| `tests/unit/test_resource_budget.py` | `test_the_patterns_actually_find_something` (`:358-360`): ‏`len(profiled) == 5` في C3. ثمّ في C4 ‏`len(standing) == 28` و`len(profiled) == 6`. ويُضاف إلى تعليق السجلّ: «28 since gpu-compose 2.3: `ollama-bridge` went behind profile `wsl-ollama` (0.5 vCPU / 256m) -> 35.60/57.28; profiled 3 -> 6 with `ollama` and `ollama-pull` (2.2) and the bridge». الأرقام محسوبةٌ بدالّتَي الوحدة نفسيهما (§3.3). ويبقى `test_the_script_and_this_module_agree` أخضر لأنّ السكربت يصنّف بالمفتاحين نفسيهما | C3، C4 |
| `docs/design/08-local-runbook.md:476-477` | `35.60` و`57.28`، يطابقهما `test_the_runbook_ledger_carries_the_numbers_this_module_computes` | C4 |
| `tests/unit/test_embedding_service_app.py` | (1) `_fake_loader` ‏(`:84-97`): الدالّة الداخليّة `_load(model_name, max_seq_length, device)` تسجّل الجهاز المستلَم. (2) fixture تلقائيّة تجعل `service_app._choose_device` تعيد `_Device("cpu", None)`، فلا يُستورد torch أبداً. (3) `_reset_state` يعيد `device`/`dtype` إلى `None`. (4) الردّ الحرفيّ في `test_health_is_200_with_the_loaded_models_identity_once_started` (`:109-136`) يضيف `"device": "cpu"` و`"dtype": "float32"`. (5) الاختبارات الجديدة في §5.3 (AC‑4.3) | C1 |
| `tests/unit/test_release_build_push.py` | (1) `_CUDA_REF = "anastawahia/aizzak:embedding-cuda-2026.10.1"`. (2) `test_image_list_has_two_images_and_reserves_cuda_for_2_4` يصير `test_image_list_has_three_images_and_no_reserved_slot`: الأسطر الثلاثة بالضبط، والثالث `embedding-cuda services/embedding/Dockerfile EMBEDDING_REQUIREMENTS=requirements-cuda.txt`. (3) `test_build_calls_…` (`:302-329`): ‏`len(builds) == 3`، والخريطة **بالوسم** لا بملفّ البناء (ملفٌّ واحد لصورتين): لكلّ وسم `(dockerfile, build_arg)`. و`--build-arg` يظهر في نداء CUDA وحده، ووسم `title` يساوي `aizzak-<prefix>`، و`kinds == ["buildx"] * 3 + ["push"] * 3`. (4) `test_success_prints_…` (`:332-354`): الصيغة `(app\|embedding\|embedding-cuda)`، واستخراج البادئة بمجموعةٍ في النمط لا بـ`split("-")` الذي يعطي `embedding` مرّتين، و`(3 images)`، وثلاثة صفوف. (5) `test_existing_tag_is_refused` يضيف `_CUDA_REF`. (6) `test_build_failure_pushes_nothing` معلَّمٌ بـ`embedding-cuda-` أيضاً: الرمز 8، وصفر رفع. (7) `test_visibility_after_push_fails_closed`: ‏`after ∈ [1, 2, 3]` | C5 |

**قائمةٌ لا تتغيّر، تحقّقتُ منها:**
- **`test_compose_build_overlay.py`** (D‑16): ‏`_platform_services()` يبقى 10، و`.env.example` بلا سطرٍ مفعّل.
- **`test_embedding_fleet.py`:** ‏`replicas: 3` في الأساسيّ (AC‑5.8).
- **`test_log_correlation.py:371-379`:** يفحص كلّ خدمة، فالخدمتان الجديدتان تدمجان `*app-logging`.
- **`test_observability_stack_wiring.py:281-289`:** ‏`ollama` لا يُكشط.
- **`test_connection_budget.py`:** ‏`ollama-pull` بلا `DATABASE_URL`.
- **`test_prebuilt_images_docs.py`:** ملزِمٌ لكاتب الوثائق (D‑13).
- **`test_prometheus_alert_rules.py`:** ‏`response` لـ`AizzakLlmCircuitOpen` غير مُطابَق.
- **`test_ci_workflow_truthfulness.py` و`test_release_workflow.py`:** لا يتغيّر `ci.yml` ولا `release.yml`.

### 5.2 ملفّات الاختبار الجديدة

- **`tests/unit/test_gpu_preflight.py`** (devops، C2):
  - على نمط `test_release_build_push.py`: بدائل Python لـ`nvidia-smi` و`docker` أوّلَ `PATH` تسجّل في `calls.jsonl`، وسلوكها بمتغيّرات `FAKE_*`.
  - ومعها بدائل مسجِّلة لـ`apt-get` و`yum` و`dnf` و`sudo` و`systemctl` و`nvidia-ctk`، يجب ألّا تُستدعى.
  - و`timeout` الحقيقيّ من coreutils.
- **`tests/unit/test_ollama_pull_models.py`** (devops، C3):
  - يحمّل `deploy/ollama/pull_models.py` بـ`importlib.util.spec_from_file_location`.
  - يختبر `ollama_models` نقيّةً.
  - ويختبر `main` مقابل `http.server` في خيطٍ على `127.0.0.1:0`، يسجّل المسارات المطلوبة، ويجيب `/api/show` و`/api/pull` و`/api/version` حسب الحالة.
- **`tests/unit/test_compose_local_llm.py`** (devops، C3 ثمّ C4): ‏YAML بـ`yaml.safe_load` (يحلّ `<<` والمراسي)، ومعه `[config]`.
- **`tests/unit/test_embedding_cuda_image.py`** (devops، C5).
- **`tests/unit/test_compose_gpu_overlay.py`** (devops، C6).
- **`tests/unit/test_gpu_compose_hygiene.py`** (qa، C8).
- **`tests/eval/vector_parity.py`** (qa، C8). أداة قياسٍ لا اختبار، لا يجمعها pytest، بثلاثة أوامر:
  - `build-set --out set.json`، في `.venv`: الأسئلة الـ42 من `tests/eval/hr_handbook_set.py` (‏15 سؤالاً بلغتين، و6 بلا جواب بلغتين)، ومقاطع `docs/hr-no-table.docx` بمحلّل المنصّة ومقطِّعها عند `embedding_max_input_tokens = 512`، وبصمة sha256 للنصوص بترتيبها.
  - `compare --cpu URL --gpu URL --set set.json`، بالمكتبة القياسيّة وحدها: يطبع JSON بأدنى جيب تمامٍ ومتوسّطه، وتداخل أعلى 10 لكلّ سؤال (ترتيب المقاطع بالضرب الداخليّ في فضاء كلّ صورةٍ على حدة)، وتطابق الأوّل من 42، و`pass`. ويخرج بغير صفر إن فشل شرطٌ من Q‑3 (أ) أو (ب).
  - `soak --url URL --minutes 15 --concurrency 8 --batch 8`، بالمكتبة القياسيّة وحدها: حمل AC‑4.11 بنصوصٍ عربيّة طولها نحو 350 كلمة، مبنيّةٍ حتميّاً من نصوص المجموعة العربيّة.

### 5.3 لكلّ معيار

المرحلة بلغة `stories.md`: ‏[وحدة] و[config] و[نصّ] تُثبَت قبل الدمج. و[محلّيّ] قبل الدمج بإذن تشغيل حاويات. و[مالك] = ش‑2. و[CI] = ش‑1 أو ش‑4. و[أداة] = ش‑3 (نافذة المالك بعد تثبيت الأداة). و[خادم] = ش‑5 (7.3).

| AC | المرحلة | الاختبار أو الأمر |
|---|---|---|
| AC‑1.1 | وحدة | `test_gpu_preflight.py::test_never_installs_anything[driver_missing,toolkit_missing,compose_old,all_ok,all_ok_smoke]`: صفر نداء لبدائل التثبيت الستّة. و`::test_script_text_hygiene`: الشبانغ، و`set -euo pipefail` أوّل سطر شيفرة، ولا `.env`، ولا `set -x`/`env`/`printenv`، والصلاحيّة 100755 |
| AC‑1.2 | وحدة | `::test_no_usable_driver_is_exit_10[missing,nonzero]`: الرمز 10، والرسالة تذكر «NVIDIA driver»، ولا `docker run` حتّى مع `--smoke` |
| AC‑1.3 | وحدة | `::test_missing_runtime_is_exit_12_with_the_advice_not_the_action`: ‏Runtimes ‏`{"io.containerd.runc.v2":…,"runc":…}`، فالرمز 12، والمخرج فيه `nvidia-ctk runtime configure --runtime=docker` و`restart`، و`nvidia-ctk`/`sudo`/`systemctl` لم تُستدعَ |
| AC‑1.4 | وحدة | `::test_compose_version_is_compared_semantically` معلَّمٌ بالقيم: ‏`v2.19.1`←13، و`2.19.0`←13، و`v2.20.0`←0، و`2.29.7`←0، و`v5.3.1`←0، و`2.29.7-desktop.1`←0، ونصٌّ غير مفهوم←13، ورمزٌ غير صفريّ←13 |
| AC‑1.5 | وحدة | `::test_all_green_without_smoke_exits_0_and_starts_no_container`: يطبع الكرت والتعريف وCompose. و`::test_smoke_runs_exactly_one_pinned_gpu_container`: نداءٌ واحد بالبادئة `["run","--rm","--gpus","all","nvidia/cuda:12.4.1-base-ubuntu22.04","nvidia-smi"]`، والوسم يطابق `\d+\.\d+\.\d+` لا `latest`. و`::test_smoke_failure_is_exit_14_and_relays_dockers_message` |
| AC‑1.6 | وحدة | `::test_no_environment_value_reaches_the_output`: ‏`CANARY_SECRET=<uuid>` في البيئة لكلّ السيناريوهات، وغيابه من stdout وstderr |
| (حوافّ US‑1) | وحدة | `::test_docker_permission_denied_is_exit_11_not_toolkit`، و`::test_smoke_evidence_overrides_a_missing_runtime` (D‑10)، و`::test_every_gpu_is_printed`، و`::test_no_gpu_with_an_old_compose_is_exit_13` (الأسبقيّة) |
| AC‑1.7 | محلّيّ، بلا أداة | `time deploy/gpu-preflight.sh; echo "rc=$?"`: الرمز 12، وسطرا driver وcompose بـ`OK`، والزمن ≤ 10 ث. أوامر قراءةٍ فقط (QA، المرحلة 8) |
| AC‑1.8 | أداة ثمّ خادم | `deploy/gpu-preflight.sh --smoke`: الرمز 0، واسم الكرت مطبوعٌ من داخل الحاوية (ش‑3 ثمّ ش‑5) |
| AC‑1.9 | نصّ ووحدة | `test_gpu_preflight.py::test_the_server_doc_lists_the_scripts_exit_codes`: يقرأ صفوف الجدول في `docs/deploy-server.md` وسطور الرموز في رأس السكربت، فتتطابق، ومعها `nvidia-ctk runtime configure --runtime=docker` و`<div dir="rtl">`. وقراءةٌ في المراجعة |
| AC‑2.1 | وحدة | `test_compose_local_llm.py::test_the_ollama_service_shape`: الصورة تطابق `^ollama/ollama:\d+\.\d+\.\d+$`، و`profiles == ["local-llm"]`، ولا `ports` ولا `container_name`، و`ollama-models:/root/.ollama` معرَّفٌ في `volumes` العليا، والفحص `CMD bash -c` إلى `/api/version`، والحدّان، و`restart: unless-stopped`، و`logging` |
| AC‑2.2 | وحدة | `::test_the_ollama_pull_service_shape`: ‏`profiles` و`restart: "no"`، والحافّة `service_healthy` و`required: false`، والحدّان، ولا `ports`، و`python:3.12-slim`، و`user`/`read_only`، وربط السكربت `:ro`، و`OLLAMA_BASE_URL == "http://ollama:11434"`، و`PROVIDER_ROUTING` يساوي قيمة `x-app-env`. ومعه `_ONE_SHOT` في `test_healthcheck_coverage.py` |
| AC‑2.3 | وحدة | `test_ollama_pull_models.py`: ‏`::test_the_compose_default_routing_pulls_exactly_gemma3_1b` (أ)، و`::test_the_env_example_routing_pulls_exactly_gemma3_1b_fallback_route_included` (ب)، و`::test_a_mixed_table_pulls_each_ollama_model_once_and_nothing_else` (ج)، و`::test_no_ollama_route_pulls_nothing_and_exits_0` (د)، و`::test_invalid_json_exits_2_with_no_http_call` (هـ). ومعها: ‏`::test_a_present_model_is_never_pulled` (صفر `/api/pull`، وسطر «already present»)، و`::test_an_absent_model_is_pulled_once_then_verified`، و`::test_a_pull_error_exits_4_naming_the_model`، و`::test_an_unreachable_ollama_exits_3`، و`::test_a_malformed_entry_exits_2_naming_its_path` |
| AC‑2.4 | وحدة | `test_compose_local_llm.py::test_ollama_base_url_defaults_to_the_in_stack_service` (النصّ الخام `${OLLAMA_BASE_URL:-http://ollama:11434}`)، و`::test_env_example_sets_no_other_ollama_base_url` (لا `^OLLAMA_BASE_URL=` مفعّل، والسطر المعلَّق `:11435` موجود) |
| AC‑2.5 | وحدة | `::test_app_and_the_three_workers_wait_for_ollama_optionally`، و`::test_no_edge_requires_ollama_or_ollama_pull` (كلّ حافّةٍ إلى أيٍّ منهما تحمل `required` بالقيمة `False`) |
| AC‑2.6 | config | `::test_local_llm_adds_exactly_ollama_and_ollama_pull`: ‏`config -q` و`--services` بلا profile وبـ`--profile local-llm`، والرمز 0 في الكلّ، والفرق بالضبط الخدمتان، و`app.depends_on.ollama.required is False` في المحلول |
| AC‑2.7 | محلّيّ، بلا أداة | الإجراء P‑1 (§5.5) |
| AC‑2.8 | خادم (ش‑5)، أو مالك بموافقةٍ صريحة | محادثةٌ على المسار المحلّيّ بـ`COMPOSE_PROFILES=local-llm`: إطار `final`، وصفّ `usage` على `ollama` |
| AC‑3.1 | وحدة | `test_compose_local_llm.py::test_the_bridge_is_profiled_and_otherwise_unchanged`: ‏`profiles == ["wsl-ollama"]`، وبقيّة المفاتيح مطابقةٌ لقاموسٍ مثبَّت (الصورة، و`network_mode: host`، والأمر، والفحص، والحدود) |
| AC‑3.2 | وحدة | `test_healthcheck_coverage.py::test_the_bridge_is_probed_end_to_end_but_gates_nothing` (معدَّل)، و`test_compose_local_llm.py::test_the_four_bridge_edges_are_optional` |
| AC‑3.3 | وحدة | `test_resource_budget.py`: العدّ 28/5/6، والدفتر 35.60/57.28، و`test_the_script_and_this_module_agree` |
| AC‑3.4 | config | `test_compose_local_llm.py::test_wsl_ollama_adds_exactly_the_bridge`: بلا profile وبـ`--profile wsl-ollama`، والرمز 0، ولا «depends on undefined service» |
| AC‑3.5 | وحدة | `::test_env_example_offers_both_profiles_commented_only`: السطران المعلَّقان، ولا `^COMPOSE_PROFILES=` مفعّل |
| AC‑3.6 | مالك (ش‑2) | §3.13، الخطوات بعد البصمات |
| AC‑3.7 | CI (ش‑1) | `gh pr checks`: ‏`quality` و`integration` خضراوان |
| AC‑3.8 | خادم (ش‑5) | `docker compose ps -a` بلا `ollama-bridge`، على خادمٍ بـ`COMPOSE_PROFILES=local-llm` وحده |
| AC‑4.1 | وحدة | `test_embedding_cuda_image.py::test_cuda_requirements_pin_the_same_versions_and_differ_only_in_the_index`: الأسطر غير التعليقيّة. مجموعة الإصدارات الخمسة واحدة، والفرق الوحيد سطر `--extra-index-url https://download.pytorch.org/whl/cu124`، ولا `cpu` فيه |
| AC‑4.2 | وحدة | `::test_one_arg_selects_the_requirements_and_defaults_to_cpu`: ‏`ARG EMBEDDING_REQUIREMENTS=requirements.txt` واحد، و`COPY` و`pip install` يذكرانه، و`FROM` بوسمٍ غير `latest` |
| AC‑4.3 | وحدة | `test_embedding_service_app.py`: ‏`::test_health_is_200_with_the_loaded_models_identity_once_started` (معدَّل: ‏`cpu`/`float32`)، و`::test_health_reports_cuda_and_float16_when_the_device_is_cuda`، و`::test_the_loader_places_the_model_on_the_chosen_device`، و`::test_the_device_is_not_read_from_the_environment` (متغيّراتٌ كـ`EMB_DEVICE=cuda` لا أثر لها) |
| (Q‑10) | وحدة | `::test_require_cuda_refuses_a_cpu_start_before_loading` (يرفع `RuntimeError` عند الدخول، والمحمِّل لم يُستدعَ، والرسالة تذكر `EMB_REQUIRE_CUDA` و`requirements-cuda.txt`)، و`::test_require_cuda_with_a_cuda_torch_but_no_gpu_names_the_toolkit`، و`::test_require_cuda_starts_on_cuda`، و`::test_require_cuda_off_by_default[unset,"","0"]`، و`::test_require_cuda_rejects_any_other_value[true,yes,2]` (قبل `_choose_device`) |
| AC‑4.4 | وحدة | `test_release_build_push.py` (§5.1) |
| AC‑4.5 | config | `test_embedding_cuda_image.py::test_the_build_overlay_builds_embedding_without_the_cuda_arg`: في `yml:build` المحلول، ‏`services.embedding.build` بلا `args` أو بلا `EMBEDDING_REQUIREMENTS` |
| AC‑4.6 | محلّيّ، بلا أداة | الإجراء P‑2 |
| AC‑4.7 | CI (ش‑4) | أوّل وسم `v2026.10.2` أو ما بعده: الوسوم الثلاثة في `anastawahia/aizzak`، وبصماتها في السجلّ والـartifact `release-digests`، و`publish` ≤ 60 د، و«Runner disk after» ≥ 20 ج.ب |
| AC‑4.8 | أداة ثمّ خادم | P‑3: ‏`/health` يقول `cuda`/`float16` |
| AC‑4.9 | أداة أو خادم | P‑4 بـ`tests/eval/vector_parity.py compare` |
| AC‑4.10 | خادم (ش‑5) | `tests/eval/run_calibration.py` على الفهرسين، بشرط Q‑3 (ج) |
| AC‑4.11 | أداة ثمّ خادم | P‑5 بـ`vector_parity.py soak` و`memory.peak` |
| AC‑5.1 | وحدة | `test_compose_gpu_overlay.py::test_the_overlay_has_only_services_and_exactly_three` |
| AC‑5.2 | وحدة | `::test_ollama_gets_exactly_one_gpu_reservation`: الخدمة تساوي `{"deploy": {"resources": {"reservations": {"devices": [{"driver": "nvidia", "capabilities": ["gpu"]}]}}}}` حرفيّاً |
| AC‑5.3 | وحدة | `::test_embedding_is_one_cuda_replica_with_a_written_memory_limit`: صيغة المتغيّرين، والحلّ الافتراضيّ `aizzak/embedding-cuda:dev` غير صورة المعالج وغير `latest`، والحجز `[gpu]`، و`replicas == 1`، و`limits == {"memory": "3g"}`، و`environment == {"EMB_REQUIRE_CUDA": "1"}`، والمفاتيح ⊆ `{image, deploy, environment}` |
| AC‑5.4 | وحدة | `::test_app_gets_utility_only_and_no_wsl_path`: ‏`["utility"]` بالضبط، ونصّ الملفّ بلا `/dev/dxg` ولا `/usr/lib/wsl` ولا `LD_LIBRARY_PATH` |
| AC‑5.5 | وحدة | `::test_the_overlay_builds_publishes_and_names_nothing`: لا `build` ولا `ports` ولا `container_name` ولا `network_mode` في أيّ عمق، ولا `:latest` |
| AC‑5.6 | config | `::test_the_overlay_resolves_one_cuda_replica_and_leaves_the_balancer_alone`: ‏`config -q`، ثمّ JSON بلا profile وبـ`local-llm`. ‏`embedding` بنسخةٍ واحدة وصورة CUDA وحجز. و`services["embedding-lb"]` مطابقٌ حرفيّاً لمخرج الأساسيّ وحده |
| AC‑5.7 | config، **بصياغة §5.4** | `::test_only_the_release_script_builds_cuda_and_a_cpu_build_under_its_name_refuses`: ‏`yml:build` يعطي صورة المعالج و`build` بلا وسيط. و`yml:gpu` يعطي صورة CUDA بلا `build`. و`yml:gpu:build` يعطي صورة CUDA و`EMB_REQUIRE_CUDA == "1"` في المحلول. والأساسيّ وحده بلا `EMB_REQUIRE_CUDA` |
| AC‑5.8 | وحدة | `::test_the_base_file_reserves_no_device` (صفر `devices`/`gpus` في أيّ عمق). و`test_embedding_fleet.py::test_the_fleet_is_three_replicas` كما هو |
| AC‑5.9 | أداة | P‑3 (‏`/api/ps` و`/health` و`memory.used` و`nvidia-smi` من `app`) |
| AC‑5.10 | خادم (ش‑5) | `nvidia-smi` على المضيف يعرض عمليّتين، و`GET /api/v1/admin/system/stats` بلا `gpu_error`، بعد محادثةٍ واحدة |
| AC‑6.1 | نصّ | قراءة §2‑ي في المراجعة: صفٌّ لكلّ مستهلك، والمصدر والعلامة، والمجموع مقابل 90% لـ8 و16 و24 ج.ب |
| AC‑6.2 | وحدة | `test_compose_local_llm.py::test_ollama_concurrency_knobs_and_their_relation`: ‏`OLLAMA_MAX_LOADED_MODELS` افتراضيّه `"1"`، وافتراضيّ `OLLAMA_NUM_PARALLEL` يساوي افتراضيّ `OLLAMA_MAX_CONCURRENCY` في `x-app-env`، والسطران في `.env.example` (إن وُجدا، مفعّلين أو معلَّقين) متساويان. و`test_compose_gpu_overlay.py::test_the_ollama_knobs_live_in_the_base_file_only` |
| AC‑6.3 | وحدة | `test_compose_local_llm.py::test_every_ollama_route_is_gemma3_1b`: في الافتراضيّ `:258` وفي `.env.example:629` |
| AC‑6.4 | أداة | P‑6 |
| AC‑6.5 | خادم (ش‑5) | مثل P‑6 عبر الـAPI، ومعه فهرسةٌ جارية، والأرقام في `docs/delivery/compose-gpu-server/` |
| AC‑7.1 | مالك (ش‑2) | §3.13 |
| AC‑7.2 | مالك (ش‑2) | §3.13: ‏W بلا `environment`، والبصمتان متطابقتان |
| AC‑7.3 | config | `test_compose_local_llm.py::test_the_default_stack_is_the_28_standing_and_5_one_shots`: ‏`config --services` بلا profile يساوي مجموعة الخدمات بلا `profiles` في YAML، وعددها 33، وليس فيها `ollama-bridge`. ومعه أمرٌ تنفّذه QA مرّةً واحدة يقارن بـ`git show master:docker-compose.yml`، والفرق بالضبط `ollama-bridge` |
| AC‑7.4 | البوّابات | الفحوص الخمسة عند كلّ commit وعند رأس الفرع، ومعها `deploy/prometheus/test-rules.sh` (‏`alerts.yml` يتغيّر في C7). و`git diff master -- .importlinter` فارغ |
| AC‑7.5 | وحدة | `test_gpu_compose_hygiene.py::test_every_image_and_from_is_pinned`: كلّ `image:` في الملفّين وكلّ `FROM` في ملفّي Dockerfile إمّا بمتغيّري المستودع والوسم بقيمٍ افتراضيّة، أو `اسم:وسم` بوسمٍ غير فارغ وغير `latest` |
| AC‑7.6 | وحدة | `::test_no_credential_and_no_environment_file_sourcing`: قائمةٌ صريحة بملفّات الفرع الجديدة والمعدَّلة، تُبحث عن `dckr_pat_` و`hf_[A-Za-z0-9]{20,}` و`sk-[A-Za-z0-9]{20,}`، وعن `source .env` و`. ./.env` في `deploy/gpu-preflight.sh` و`deploy/ollama/pull_models.py` |
| AC‑7.7 | أمر QA والمراجعة | `git diff --stat master...HEAD -- deploy/runpod src` فارغ |
| AC‑7.8 | إجراء QA | في worktree منفصل: `for c in $(git rev-list --reverse master..HEAD); do git checkout -q "$c" && .venv/bin/pytest -q tests/unit \|\| echo "RED $c"; done`، ومعه `git show --stat` لكلّ commit يلمس `docker-compose*.yml` أو `services/embedding/` أو `deploy/release/` |
| AC‑7.9 | CI (ش‑1) | كـAC‑3.7 |
| AC‑8.1 | وحدة | `test_compose_local_llm.py::test_env_example_offers_both_profiles_commented_only`، و`test_compose_gpu_overlay.py::test_env_example_offers_the_server_lines_commented_only`: ‏`#COMPOSE_PROFILES=local-llm`، و`#COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml`، وسطرٌ يطابق `^#EMBEDDING_CUDA_IMAGE_TAG=embedding-cuda-\d{4}\.\d+\.\d+$`، ولا سطر `COMPOSE_*=` مفعّل |
| AC‑8.2 | نصّ | المراجعة، ومعها `test_gpu_compose_hygiene.py::test_stack_commands_names_the_owner_line_the_overlay_and_the_preflight` (‏`COMPOSE_PROFILES=wsl-ollama`، و`docker-compose.gpu.yml`، و`gpu-preflight.sh`، وجملة `--profile`) |
| AC‑8.3 | نصّ | المراجعة، و`::test_the_circuit_runbook_names_both_ollama_paths` (‏`ollama-bridge` و`docker compose ps ollama` في القسم)، و`test-rules.sh` أخضر |
| AC‑8.4 | نصّ | قراءة `08-local-runbook.md`: ‏`:30` والصفّان الجديدان و§2‑ي |

### 5.4 تعديلات معايير القبول

صياغاتٌ تفرضها دلالات Compose أو قرارات المالك. **لا يُعدَّل `stories.md`.** هذا الجدول هو المرجع للمراحل 8 و9 و11.

| المعيار | في `stories.md` | المعتمد | السبب |
|---|---|---|---|
| AC‑5.7 | مع ملفّ الكرت: اسم CUDA **ووسيط** CUDA؛ ولا تركيبة تعطي اسم CUDA مع متطلّبات المعالج | (أ) ‏`yml:build`: صورة المعالج بلا وسيط، كما اليوم. (ب) ‏`yml:gpu`: صورة CUDA بلا `build`، وهذا شكل الخادم. (ج) ‏`yml:gpu:build`: تركيبةٌ غير مدعومة، تقولها الوثائق ورأس الملفّ. وفي المحلول `EMB_REQUIRE_CUDA: "1"`، فصورة معالجٍ بُنيت بهذا الاسم ترفض الإقلاع برسالةٍ تسمّي السبب (اختبارات Q‑10 في §5.3). (د) صورة CUDA لا تُبنى إلّا بـ`build-push.sh` أو الأمر 43 | D‑1. الحرف مستحيلٌ مع AC‑5.5 في دلالات Compose. هذا تصحيحٌ بالقياس لا قرار |
| AC‑7.1 | البصمة بـ`--profile backup` | بـ`--profile backup --profile wsl-ollama` في «قبل» و«بعد» معاً. وW بلا `.embedding.build.args` | §3.3 مقيس: الخيار يحلّ محلّ `COMPOSE_PROFILES` من `.env`. وD‑16 |
| AC‑2.1 | «فيها `healthcheck`» | الفحص `CMD bash -c` بـ`/dev/tcp` إلى `/api/version` | D‑3: الصورة بلا `curl`/`wget` |
| AC‑2.5 | لا خدمة تعتمد على `ollama` دون `required: false` | كما هو حرفيّاً، ويشمل حافّة `ollama-pull` نفسها | D‑6 |
| AC‑4.4 | «ثلاثة أسطر بلا `reserved`» | والسطر الثالث يحمل وسيط البناء: `embedding-cuda services/embedding/Dockerfile EMBEDDING_REQUIREMENTS=requirements-cuda.txt` | §3.7 |
| AC‑4.6 | «`device: "cpu"`، أو ترفض الإقلاع إن اختار Q‑10 ذلك» | الاثنان معاً: دون المتغيّر تقلع على المعالج ويقول `/health` ‏`cpu`؛ وبـ`-e EMB_REQUIRE_CUDA=1` ترفض | Q‑10 اختار متغيّراً لا سلوكاً ثابتاً في الصورة |
| AC‑1.5 | `docker run --rm --gpus all <صورة> nvidia-smi` | البادئة نفسها، وبعد `nvidia-smi` خياران: `--query-gpu=name --format=csv,noheader` | ليطبع اسم الكرت وحده (AC‑1.8) |
| AC‑5.2 | «ولا مفتاح آخر» | خدمة `ollama` في ملفّ الكرت لا تحمل إلّا هذا المسار، ومدخل الجهاز `{driver, capabilities}` بلا `count` | D‑8 |
| AC‑7.3 | «قبل الفرع وبعده» | اختبار وحدةٍ يثبّت المجموعة المتوقّعة (28 + 5)، ومقارنةٌ بـ`master` تنفّذها QA مرّةً | اختبار الوحدة لا يرى `master` في CI |

### 5.5 الإجراءات غير الآليّة

**القاعدة في كلّها:**
- مشروع Compose منفصل بـ`-p`، و`--env-file .env.example`، و`-f` صريحة. فلا يُقرأ `.env` الحقيقيّ، ولا يُلمس المكدّس الحيّ.
- بلا منافذ منشورة، إلّا `127.0.0.1` في P‑4.
- الأحجام التي تنشئها المشاريع المنفصلة تبقى، وحذفها يحتاج موافقة (`CLAUDE.md`).
- **قبل P‑1 وP‑2:** ‏`free -m` يجب أن يُظهر ≥ 5 ج.ب متاحة، لأنّ هذا الجهاز بـ13 ج.ب والمكدّس الحيّ مضبوطٌ لـ64. ويُختار وقتٌ هادئ بلا تشغيل حمل.

**P‑1، ‏AC‑2.7، محلّيّ على المعالج** (QA بإذن تشغيل حاويات):

```bash
cd /home/AIZZAK
P="docker compose -p aizzak-llm-proof --env-file .env.example -f docker-compose.yml --profile local-llm"
$P up -d ollama
$P run --rm ollama-pull                        # 1st: pulls gemma3:1b, rc 0
time $P run --rm ollama-pull                   # 2nd: "already present -- not contacting the registry", rc 0, <= 30 s
$P logs ollama | grep -c 'registry.ollama.ai'  # unchanged between the two runs
$P run --rm --no-deps --entrypoint python ollama-pull -c 'import json,urllib.request as u; r=u.urlopen(u.Request("http://ollama:11434/api/generate", json.dumps({"model":"gemma3:1b","prompt":"Say hi","stream":False}).encode(), {"Content-Type":"application/json"}), timeout=300); print(bool(json.load(r)["response"].strip()))'   # True
docker exec aizzak-llm-proof-ollama-1 cat /sys/fs/cgroup/memory.peak   # against the 4g estimate (§3.2)
$P stop ollama && $P rm -f ollama
```

**P‑2، ‏AC‑4.6، محلّيّ بلا كرت** (QA):

```bash
docker buildx build --platform linux/amd64 --load \
  --build-arg EMBEDDING_REQUIREMENTS=requirements-cuda.txt \
  -f services/embedding/Dockerfile -t aizzak/embedding-cuda:dev .
docker image inspect aizzak/embedding-cuda:dev --format '{{.Size}}'   # into test-plan.md; > 10 GB -> owner before the first release (NFR‑9)
docker run --rm --network none --entrypoint python aizzak/embedding-cuda:dev -c "import torch; print(torch.__version__, torch.version.cuda)"   # 2.5.1+cu124 12.4
docker run --rm --network none --entrypoint python aizzak/embedding:dev      -c "import torch; print(torch.__version__, torch.version.cuda)"   # 2.5.1+cpu None
docker run -d --name emb-cuda-proof --network none aizzak/embedding-cuda:dev
docker exec emb-cuda-proof curl -fsS --retry 30 --retry-delay 2 --retry-all-errors http://127.0.0.1:8080/health   # "device":"cpu","dtype":"float32"
docker rm -f emb-cuda-proof
docker run --rm --network none -e EMB_REQUIRE_CUDA=1 aizzak/embedding-cuda:dev; echo "rc=$?"   # non-zero, the message names requirements-cuda.txt
```

**ش‑3، نافذة الأداة.** بعد الملحق أ وP‑2. تُفرَّغ أوّلاً نماذج Ollama الأصليّ من الكرت: `curl -s 127.0.0.1:11434/api/generate -d '{"model":"gemma3:1b","keep_alive":0}'`.

```bash
G="docker compose -p aizzak-gpu-proof --env-file .env.example -f docker-compose.yml -f docker-compose.gpu.yml --profile local-llm"
nvidia-smi --query-gpu=memory.used --format=csv,noheader                 # baseline
```

- **P‑3، ‏AC‑1.8 وAC‑4.8 وAC‑5.9:**
  1. `deploy/gpu-preflight.sh --smoke`.
  2. `$G up -d embedding embedding-lb ollama`، ثمّ `$G run --rm ollama-pull`.
  3. `/health` في `embedding` يقول `cuda`/`float16`.
  4. طلب توليدٍ واحد كما في P‑1، ثمّ `/api/ps`: ‏`size_vram == size`.
  5. `memory.used` فوق خطّ الأساس.
  6. `$G run --rm --no-deps app nvidia-smi --query-gpu=name,memory.used --format=csv` يطبع الكرت.
- **P‑4، ‏AC‑4.9:**
  - حاويتان على شبكةٍ مؤقّتة `aizzak-parity`، منشورتان على `127.0.0.1:18081` و`127.0.0.1:18082` بعد التحقّق أنّ المنفذين حرّان: `aizzak/embedding:dev` و`aizzak/embedding-cuda:dev --gpus all -e EMB_REQUIRE_CUDA=1`. و`EMB_MAX_SEQ_LEN` على افتراضيّه 512.
  - ثمّ `.venv/bin/python tests/eval/vector_parity.py build-set --out <scratch>/set.json`، ثمّ `compare`.
  - يُكتب JSON النتيجة وبصمة المجموعة في `test-plan.md`.
- **P‑5، ‏AC‑4.11:**
  - `vector_parity.py soak --minutes 15` ضدّ `embedding` في المشروع، من حاويةٍ مؤقّتة على شبكته بصورة `python:3.12-slim`، و`tests/eval` مربوطاً للقراءة.
  - ثمّ `docker exec aizzak-gpu-proof-embedding-1 cat /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory.events`، و`docker inspect --format '{{.State.OOMKilled}}'`.
  - القاعدة في §3.8: إن تغيّر الحدّ، فـcommit لاحقٌ على `docker-compose.gpu.yml` (ضمن Q‑8) مع القياس.
- **P‑6، ‏AC‑6.4:**
  - `OLLAMA_NUM_PARALLEL + 2` = 4 طلبات توليدٍ متزامنة بسياقٍ نحو 3,000 رمز، ومعها P‑5، لمدّة ≥ 10 د.
  - و`nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader -l 1` في ملفّ.
  - و`/api/ps` كلّ 10 ث.
  - و`$G logs ollama | grep -iE 'offload|cpu'`.
  - الشروط: الأعلى < 0.90، و`size_vram == size`، وصفر OOM. والأرقام تملأ §2‑ي.
- **في الختام:** `$G down` (بلا `-v`). الأحجام تبقى.

## 6. ترتيب التنفيذ والتوازي

### 6.1 الالتزامات

على `feat/gpu-compose`. كلّ commit أخضرُ بالفحوص الخمسة: ‏`ruff format --check .` و`ruff check .` و`mypy src` و`lint-imports` و`pytest tests/unit tests/architecture tests/eval`. ومعها `deploy/prometheus/test-rules.sh` حيث يتغيّر `alerts.yml`. والاختبارات التي تقرأ ملفّاً تتغيّر في commit الملفّ نفسه (القاعدة 3، AC‑7.8).

1. **C1، backend-developer:** `feat(embedding): /health reports device and dtype; EMB_REQUIRE_CUDA refuses a CPU start`
   - `services/embedding/app.py`، و`tests/unit/test_embedding_service_app.py`.
   - لا يعتمد على شيء، والعقد ثابتٌ في §3.6.
2. **C2، devops-engineer:** `feat(deploy): gpu-preflight.sh and the server prerequisites (2.1)`
   - `deploy/gpu-preflight.sh`، و`tests/unit/test_gpu_preflight.py`، و`docs/deploy-server.md`.
3. **C3، devops-engineer:** `feat(compose): ollama and ollama-pull behind local-llm (2.2)`. **ينتظر حسم ⚠️ 1.**
   - `docker-compose.yml`: ‏`OLLAMA_BASE_URL` والمرساة، وتعليق `x-host-gateway`، والخدمتان والحجم، وحافّة `ollama` في `app` و`x-worker-service`.
   - `deploy/ollama/pull_models.py`.
   - `.env.example`: كتلة Ollama، و`#COMPOSE_PROFILES=local-llm`، ومتغيّرا 2.6 معلَّقين.
   - `test_healthcheck_coverage.py` (`_ONE_SHOT`)، و`test_resource_budget.py` (29/5/5).
   - الجديدان `test_compose_local_llm.py` (أجزاء 2.x و6.x) و`test_ollama_pull_models.py`.
   - `08-local-runbook.md`: صفّا الجدول، وصفّ §5، وقائمة الـprofiles في §2‑ز (خمسٌ هنا).
4. **C4، devops-engineer:** `feat(compose): ollama-bridge behind wsl-ollama; optional edges (2.3)`
   - `docker-compose.yml`: ‏profile الجسر وتعليقه، و`required: false` على الحواف الأربع.
   - `.env.example`: ‏`#COMPOSE_PROFILES=wsl-ollama`.
   - `test_healthcheck_coverage.py` (حافّة الجسر)، و`test_resource_budget.py` (28/5/6)، و`test_compose_local_llm.py` (أجزاء 3.x و7.3).
   - `08-local-runbook.md`: الدفتر 35.60/57.28، وصفّ الجسر، وفقرة §2‑ز، وملاحظة §5.
5. **C5، devops-engineer:** `feat(embedding): CUDA requirements, Dockerfile ARG, embedding-cuda release image (2.4)`
   - `services/embedding/requirements-cuda.txt`، و`services/embedding/Dockerfile`، و`deploy/release/build-push.sh`.
   - `tests/unit/test_release_build_push.py`، و`tests/unit/test_embedding_cuda_image.py`.
6. **C6، devops-engineer:** `feat(compose): docker-compose.gpu.yml and the VRAM budget (2.5, 2.6)`
   - `docker-compose.gpu.yml`، و`tests/unit/test_compose_gpu_overlay.py`.
   - `.env.example` (سطور الخادم).
   - `08-local-runbook.md` §2‑ي.
   - يعتمد على C1 (المتغيّر)، وC3 (`ollama`)، وC5 (الاسم والصورة).
7. **C7، devops-engineer:** `docs: the owner's profile line, the GPU overlay and both Ollama paths (US‑8)`
   - `docs/stack-commands.md`، و`docs/quickstart.md`، و`docs/runbooks/alerts.md`.
   - `deploy/prometheus/alerts.yml` (`response`)، ثمّ `test-rules.sh`.
   - تعليقا رأس `docker-compose.wsl-gpu.yml` و`docker-compose.test.yml`.
   - `08-local-runbook.md` §3.0.
8. **C8، qa-engineer:** `test: gpu-compose hygiene and the vector parity tool`
   - `tests/unit/test_gpu_compose_hygiene.py`، و`tests/eval/vector_parity.py`.
   - ومعه `docs/delivery/gpu-compose/test-plan.md`.

**التوازي:** الكتابة متتابعة لأنّ الفرع واحد. لكنّ العقود ثابتةٌ في §3، فيمكن كتابة C1 وC8 (الأداة) في worktree منفصل بالتوازي، ثمّ تُطبَّق بالترتيب أعلاه. الترتيب يقلّل تبديل الكتّاب إلى ثلاثة: backend ثمّ devops ثمّ qa.

### 6.2 قبل الدمج

1. دفع الفرع وفتح الـPR (ش‑1: ‏AC‑3.7 وAC‑7.9). ‏`release.yml` لا يعمل على الفروع.
2. QA في المرحلة 8، بإذن تشغيل حاويات: AC‑1.7 وP‑1 وP‑2. ويُكتب الحجم والذاكرة في `test-plan.md` (NFR‑9، FR‑14).
3. المراجعتان والقبول بشروط ش‑n، كما في المرحلة 1.

### 6.3 بعد الدمج، على المالك (خارج الوكلاء)

1. **ش‑2:** البصمات «قبل» على `master` قبل السحب، ثمّ السطر، ثمّ «بعد»، ثمّ أوّل `up -d` في وقتٍ هادئ، ثمّ فحص الجسر والمحادثة (§3.13).
2. **ش‑3، في الوقت الذي يختاره:** الملحق أ، ثمّ إجراءات ش‑3 (§5.5). ينفّذها وكيلٌ بإذنه.
3. **ش‑4:**
   - (أ) التحقّق من سعة خطّة Docker Hub لصورةٍ ثالثة بنحو 3–3.5 ج.ب مضغوطة في كلّ إصدار (Q‑7).
   - (ب) وسم `v2026.10.2` أو ما بعده: السكربت يرفض `2026.10.1` لأنّ `app-2026.10.1` موجود.
4. **ش‑5:** التجربة 7.3.

### 6.4 على المنسّق في النهاية

**لوحة §4 في `docs/server-gpu-plan.md` و`docs/server-gpu-summary.html` معاً.** تُحدَّث **بعد** القبول وحده، والحالات تبقى ⬜ أو 🟡 حتّى شروطها:

| الخطوة | بعد الدمج | ✅ متى |
|---|---|---|
| 2.1 | 🟡 | بعد AC‑1.8 (ش‑3 أو ش‑5) |
| 2.2 | 🟡 | بعد AC‑2.8 (ش‑5، أو محادثةٌ محلّيّة بموافقة المالك) |
| 2.3 | 🟡 | بعد ش‑2 وAC‑3.8 |
| 2.4 | 🟡 | بعد ش‑4 وAC‑4.8 وAC‑4.9، ويُكتب AC‑4.10 في وثيقة 7.3 |
| 2.5، 2.6 | 🟡 | بعد AC‑5.10 وAC‑6.5 (ش‑5) |

**نصّ الخطّة يُصحَّح** (§5 و§1.1)، لأنّ قاعدة الخطّة «لا تتغيّر إلّا بقرارٍ أو بدليل»، وهذه أدلّة:
1. **أرقام الأسطر:** ‏`:56` ‏`:52`←`:59-60`، و`:59` ‏`:196`←`:201` و`:1666`←`:1669-1671`، و2.3 «المشكلة» ‏`:1666`←`:1669-1671` (`requirements.md` §5.8 البند 1).
2. **2.3:** أربع حواف (`app` والعمّال الثلاثة عبر `x-worker-service`)، و`required: false` عليها كلّها (البند 2).
3. **2.2 البند 4:** «… و`.env.example` لا يكتب قيمةً غيرها» (البند 3).
4. **2.2 البند 2:** الفحص على `/api/version` بـ`/dev/tcp`، لأنّ الصورة بلا curl/wget. و**البند 3:** ‏`ollama-pull` صورة `python:3.12-slim` مع `deploy/ollama/pull_models.py`، يسحب كلّ نموذج `ollama` في الجدول، ويفحص الوجود قبل السحب.
5. **2.4:**
   - «الطريقة»: البند 2 يذكر `build-push.sh` (البند 10).
   - و«القبول» الأوّل يحتاج تعديل `services/embedding/app.py` (البند 4).
   - و«القبول» الثاني بهامش Q‑3 الثلاثيّ، لأنّ `tests/eval` أداةٌ حيّة لا مجموعة (البند 5).
   - و§1.2 «الناقص هو torch بنسخة CUDA فقط» يُضاف إليه: «وحقلا `/health` وحدّ الذاكرة».
6. **2.5:**
   - المتغيّران `EMBEDDING_CUDA_IMAGE_REPO`/`TAG`.
   - و«لا يُركَّب مع `docker-compose.build.yml`».
   - و«القبول»: قائمة العمليّات تُقاس على خادم، لا على WSL (البند 7).
7. **2.6 «القبول»:** الحارس لكلّ عمليّة، فالحمل `OLLAMA_NUM_PARALLEL + 2` مع فهرسة (البند 6). و«منسجم» تصير «يساوي افتراضيّاً، ويحرسه اختبار».
8. **2.1:**
   - «Compose ≥ 2.20 بمقارنةٍ دلاليّة» (البند 8).
   - و«الطريقة»: الخطوات في `docs/deploy-server.md` الآن (Q‑4 أ) والفحص `deploy/gpu-preflight.sh`، ويستدعيه `install.sh` في 5.2.
   - و«القبول»: صورة `nvidia/cuda:12.4.1-base-ubuntu22.04`.
9. **1.3 «⚠️ سرعة الرفع»:** المساحة على عدّاء CI ليست قيداً (86 ج.ب حرّة) (البند 11).
10. **§3 القاعدة 3:** يُذكر معها دفتر `08 §2‑ز`، لأنّ اختباراً يطابقه (البند 9).
11. **ملاحظةٌ لـ5.1 و5.2:**
    - الحزمة تضمّ `deploy/ollama/pull_models.py` و`docker-compose.gpu.yml`.
    - و`install.sh` يكتب متغيّري صورة CUDA و`COMPOSE_PROFILES=local-llm`، ويشغّل `docker compose run --rm ollama-pull` قبل `up -d --wait`.
    - ويُتحقَّق يومها من سلوك `--wait` مع خدمةٍ لمرّةٍ واحدة منتهية.

**مراحل تُتخطّى:** UX (لا واجهة)، والبيانات (لا ترحيل)، والواجهة الأماميّة.

## 7. المخاطر والقرارات

### ⚠️ بندٌ واحدٌ يوقف C3 حتّى يقرّر المالك

- **⚠️ 1: ملفٌّ محميٌّ جديد خارج قائمة Q‑8: `deploy/ollama/pull_models.py`.**
  - **لماذا يلزم:** منطق `ollama-pull` يحتاج Python: قراءة `PROVIDER_ROUTING` بـJSON صارم (AC‑2.3 هـ)، وفحص الوجود بـ`/api/show`، والسحب. وصورة Ollama بلا Python ولا `jq` (مقيس).
  - **الخيار (أ):** ملفٌّ جديد تحت `deploy/ollama/` تربطه الخدمة للقراءة فقط، على نمط `alert-sink` و`deploy/alertmanager/sink.py`.
    - يُختبر مباشرةً بخادم HTTP مزيَّف، ويمرّ بـ`ruff`.
    - ويدخل حزمة 5.1 كأيّ ملفٍّ مربوط تحت `deploy/`.
  - **الخيار (ب):** الشيفرة نفسها نصّاً داخل `docker-compose.yml`، بـ`command: ["python", "-c", …]`. لا تحتاج موافقةً جديدة لأنّ الملفّ ضمن Q‑8، لكنّ ثمنها:
    - نحو 60 سطر Python داخل YAML.
    - كلّ `$` فيها يُكتب `$$`.
    - `ruff` لا يراها.
    - الاختبار يستخرجها من YAML قبل تشغيلها.
  - **التوصية: (أ).**

### قرارات التصميم (محسومة، لا تحتاج قراراً بشريّاً)

D‑1…D‑16 في §3.1. أهمّها للمراجعين:
- **D‑1:** Compose لا يبني صورة CUDA. وAC‑5.7 بصياغة §5.4، تصحيحاً بالقياس لا قراراً، على سابقة AC‑1.5 في المرحلة 1.
- **D‑6:** ‏`required: false` على كلّ حافّةٍ إلى Ollama.
- **D‑10:** أسبقيّة رموز الفحص، وغلبة `--smoke` في حافّة CDI وDocker Desktop.
- **D‑12:** ‏`release.yml` بلا تغيير.

### مخاطر مقبولة ومسجّلة

- **`aizzak/embedding-cuda` اسمٌ محلّيّ افتراضيّ في مساحة `aizzak` على Docker Hub.** (تصحيح 2026‑10‑15: المراجعة الأمنيّة وجدت الحساب `aizzak` موجوداً، وأكّد المالك أنّه حسابه، فلا خطر انتحال.) هو R‑3 للمرحلة 1 نفسه، مقبولٌ هناك. والخادم يكتب `anastawahia/aizzak` (5.2).
- **`ollama` يعمل بمستخدم root، وواجهته بلا مصادقة، داخل الشبكة الداخليّة وحدها.** التصليب في الموجة 8، المؤجّلة بقرار المالك (2026‑10‑06). وقائمة الأمان في `deploy-server.md` تبدأ فارغة كما اختار المالك في Q‑4.
- **Ollama على المعالج لا يحسب خيوطه من حدّ الـcgroup،** كما كان torch قبل 4.1. لا أثر له على الكرت، ويُقاس في 7.3 لخوادم بلا كرت.
- **صورة RunPod تأخذ تعديل `app.py` عند بنائها التالي** (§3.6). الإضافة غير كاسرة، والمتغيّر مطفأ.
- **ذاكرة هذا الجهاز أثناء الإثباتات المحلّيّة** (13 ج.ب، والمكدّس الحيّ يعمل). شرط `free -m` في §5.5، والحدود (`4g`، و`3g`) سقفٌ احتياطيّ. وإذن تشغيل الحاويات يعطيه المنسّق.
- **أوّل `up -d` بعد الدمج يعيد إنشاء `app` والعمّال والجسر معاً** (§3.13). انقطاعٌ قصيرٌ مرّةً واحدة.
- **R‑4 وR‑5:** نسيان السطر، وأوامر `--profile X`. موثَّقان في `stack-commands.md` و`.env.example` ودليل التنبيه.
- **R‑7:** خلط متّجهات المعالج والكرت في مجموعةٍ واحدة. خارج النطاق، والخادم الجديد يبدأ من الصفر. يُكتب في 7.4.
- **R‑9 وR‑10:** قياس الجهاز حدٌّ أدنى. و`--surge` يعني عمليّتي CUDA لحظة التحديث، وهو صفٌّ في §2‑ي.
- **NFR‑9:** إن تجاوز حجم صورة CUDA المقيس في P‑2 عشرة ج.ب، يُعرض على المالك قبل أوّل إصدار. تقديري 8–9 ج.ب.
- **قائمة الأمان المؤجّلة** في `deploy-server.md` تبدأ فارغة، ويضيف إليها كلُّ من يجد ثغرةً على الخادم (القاعدة 8).
- **تعليقاتٌ قديمة تُركت عمداً،** لأنّها خارج Q‑8 أو مؤجّلة إلى 7.4: ‏`services/embedding/requirements.txt:10-14`، و`deploy/resource-budget.sh:38-39`، و`docs/architecture.md:93,650`، و`docs/deploy-runpod.md` §11.

---

## ملحق أ: NVIDIA Container Toolkit على هذا الجهاز (ينفّذه المالك، Q‑1)

**ما وجدتُه** (2026‑10‑14، قراءةٌ فقط):
- **Docker Engine داخل WSL، لا Docker Desktop:**
  - `docker info`: ‏`OperatingSystem=Ubuntu 24.04.4 LTS`، و`ServerVersion=29.6.1`.
  - الحزم `docker-ce` و`docker-ce-cli` و`containerd.io 2.2.6` من مستودع Docker.
  - `docker context ls`: سياقٌ واحد `default` على `unix:///var/run/docker.sock`.
- **systemd:** هو العمليّة الأولى، و`systemctl is-active docker` = `active`، و`is-enabled` = `enabled`.
- **Docker اليوم:** ‏`/etc/docker/daemon.json` **غير موجود**، والـRuntimes ‏`io.containerd.runc.v2 runc`. و`CDISpecDirs` معلَنة (`/etc/cdi` و`/var/run/cdi`) لكنّ المجلّدين غير موجودين.
- **NVIDIA:**
  - لا حزمة NVIDIA مثبّتة.
  - `/usr/lib/wsl/lib` فيه `libcuda.so.1` و`libnvidia-ml.so.1`.
  - `nvidia-smi`: ‏`RTX 4060 Laptop GPU`، والتعريف 592.82، وCUDA 13.1.
- **المستخدم** في مجموعة `docker`. و`ollama.service` الأصليّ يعمل.
- **آخر إصدارٍ مستقرّ للأداة في مستودع NVIDIA:** `1.20.1-1`، بحزمه الأربع.

**المسار إذن مسار لينكس العاديّ** (دليل NVIDIA الرسميّ لـapt). التعريف يأتي من Windows ولا يُثبَّت داخل WSL.

**قبل النافذة:**
- وقتٌ هادئ بلا تشغيل حمل.
- **`VAULT_SECRET_ID` لن ينتهي قريباً:** ‏`app` والعمّال الثلاثة و`outbox-relay` و`wal-shipper` و`ops-scheduler` تسجّل الدخول إلى Vault عند كلّ إقلاع. وإعادة تشغيل Docker تعيد إقلاعها كلّها.
- لقطةٌ للحالة، بلا أسرار: `docker compose ps --format '{{.Service}} {{.State}} {{.Health}}' > ~/ps-before.txt`.

**الأوامر:**

```bash
# 1) NVIDIA's apt repository and key
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update

# 2) the toolkit, pinned (verified on the repository 2026-10-14)
V=1.20.1-1
sudo apt-get install -y nvidia-container-toolkit=$V nvidia-container-toolkit-base=$V \
  libnvidia-container-tools=$V libnvidia-container1=$V

# 3) register the `nvidia` runtime: CREATES /etc/docker/daemon.json (absent today);
#    runc stays the default runtime
sudo nvidia-ctk runtime configure --runtime=docker
sudo cat /etc/docker/daemon.json

# 4) ⚠️ THE ONE DISRUPTIVE STEP: every container of the live stack stops and comes
#    back by `restart: unless-stopped` (no dependency order: `app` may restart a few
#    times until pgbouncer and vault are healthy; one-shots stay exited)
sudo systemctl restart docker
```

**التحقّق بعدها:**

```bash
docker info --format '{{range $k, $v := .Runtimes}}{{$k}} {{end}}'     # lists nvidia
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
#   the RTX 4060 table; on WSL "No running processes found" is normal
deploy/gpu-preflight.sh --smoke; echo "rc=$?"                          # 0, once the branch carries C2 (AC‑1.8)
docker compose ps --format '{{.Service}} {{.State}} {{.Health}}' | diff ~/ps-before.txt -
#   give Qdrant several minutes to load its collections (it swaps on this host)
curl -s http://localhost/health
```

**الرجوع** (إعادة تشغيلٍ ثانية للمكدّس):

```bash
sudo rm /etc/docker/daemon.json          # it did not exist before step 3
sudo systemctl restart docker
sudo apt-get purge -y nvidia-container-toolkit nvidia-container-toolkit-base \
  libnvidia-container-tools libnvidia-container1
sudo rm /etc/apt/sources.list.d/nvidia-container-toolkit.list \
  /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
```

**لا شيء في المكدّس الحيّ يعتمد على الأداة** ما لم يُستعمل ملفّ الكرت. فالرجوع لا يغيّر سلوك المكدّس، وكذلك التثبيت، باستثناء إعادة التشغيل نفسها.

</div>
