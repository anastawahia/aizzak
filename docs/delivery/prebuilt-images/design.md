<div dir="rtl">

# التصميم التقني: صورٌ جاهزة بدل البناء المحلّيّ (المرحلة 1 من خطّة خادم الكرت)

> **المراجع:** [`requirements.md`](requirements.md)، و[`stories.md`](stories.md) (US-1…US-5)، و[`status.md`](status.md) «قرارات وتصعيدات» (البوّابة ①، 2026‑10‑13)، و[`docs/server-gpu-plan.md`](../../server-gpu-plan.md) الخطوات 1.1–1.4 و§3 و§6.
>
> **الفرع:** `feat/prebuilt-images` من `master` (`de707f0`). **المنفّذ الوحيد:** `devops-engineer` (كلّ الملفّات في مستودعٍ واحد، ولا شيفرة في `src/`، فلا سبب للتقسيم).
>
> **دورة الإعادة 1 (2026‑10‑13):** حسم المالك بندَي ⚠️ في النسخة الأولى (`status.md` «قرارات وتصعيدات»):
> - **لا `pull_policy`** في `docker-compose.build.yml` (البديل (أ)).
> - **مستودعٌ خاصٌّ واحد** `anastawahia/aizzak`، والصور تُميَّز ببادئة الوسم: `app-<الإصدار>`، و`embedding-<الإصدار>`، و`embedding-cuda-<الإصدار>` محجوزٌ للخطوة 2.4. هذا يحلّ محلّ أسماء قرار 1 في البوّابة ①.
>
> عُدّل التصميم كلّه على هذا الأساس. المعايير التي تغيّرت صياغتها مجموعةٌ في §5.1.
>
> **المعرّفات:** `FR-n` و`AC-n.m` محلّيّةٌ لهذه الميزة (بشرطةٍ عاديّة، كما في `requirements.md`). معرّفات المشروع بالشرطة غير المنكسرة (`ث‑7`، `ن‑4`). قرارات هذا التصميم: `D-1`…`D-9`.

## 1. الملخص

الميزة **بنيةٌ تشغيليّة فقط**: لا تلمس `src/` ولا `.importlinter` ولا المخطّط. تقع في طبقة النشر (Compose وCI وسكربتات `deploy/`)، ولها أربعة أجزاء:

1. **فصل البناء عن التشغيل (US-1):**
   - `docker-compose.yml` يسمّي الصورتين فقط، بمتغيّرين لكلّ صورة: المستودع والوسم.
   - ملفٌّ إضافيٌّ جديد `docker-compose.build.yml` يحمل `build:` وحده لعشر خدمات.
   - جهاز المالك يضيف سطر `COMPOSE_FILE` واحداً في `.env`، فيبقى الناتج كما اليوم: الصورتان `aizzak/app:dev` و`aizzak/embedding:dev`.
2. **الأسماء والوسوم (US-2):**
   - في السجلّ: مستودعٌ خاصٌّ واحد `anastawahia/aizzak` (قرار المالك، دورة الإعادة 1).
   - رقم الإصدار `YYYY.M.N` واحدٌ للصور كلّها، ووسم كلّ صورةٍ هو بادئتها ثمّ الرقم: `anastawahia/aizzak:app-2026.10.1` و`anastawahia/aizzak:embedding-2026.10.1`.
   - البصمة تُطبع وتُحفظ.
   - `embedding-cuda-<الإصدار>` بادئةٌ محجوزة لا تُبنى (2.4).
3. **سكربت واحد للبناء والرفع (US-3):** `deploy/release/build-push.sh`. هو المنطق الوحيد للإصدار، ويستعمله المالك للطوارئ ويستعمله CI.
4. **مسار CI جديد (US-4):** `.github/workflows/release.yml`.
   - يعمل على دفع وسم `v*` فقط.
   - ترتيبه: فحصٌ أوّليّ رخيص، ثمّ البوّابات الخمس، ثمّ السكربت نفسه بالأسرار.
   - البصمات تُحفظ في artifact.

**الوثائق (US-5)** تذكر السطر الجديد حيث يلزم، وتذكر أوامر الإصدار.

**النمط المتّبع:**
- **ملفّات Compose الإضافيّة:** على نمط `docker-compose.test.yml` و`docker-compose.wsl-gpu.yml`. كلٌّ منها ملفٌّ يُطلب صراحةً، ولا يُدمَج تلقائيّاً (`stack-commands.md` §35 و§37).
- **السكربت:** على نمط `deploy/rolling-deploy.sh`، أي `bash` بـ`set -euo pipefail`، ورأسٌ يشرح السبب، وكلّ أمرٍ خارجيّ يمرّ عبر CLI.
- **اختبار السكربت:** على نمط `tests/unit/test_rolling_deploy.py:290-440`. بدائل `docker`/`gh`/`curl` مكتوبةٌ بـPython توضع أوّل `PATH`، وتسجّل كلّ نداءٍ في `calls.jsonl`. والتحقّق يكون على تسلسل النداءات.
- **اختبار ملفّات CI:** على نمط `tests/unit/test_ci_workflow_truthfulness.py`، أي `yaml.safe_load` مع التنبّه لأنّ `on` تُقرأ `True`، و`bash -n` على كلّ `run:`.

## 2. الملفات

| المسار | إنشاء/تعديل | المسؤولية | المنفّذ |
|---|---|---|---|
| `docker-compose.yml` | تعديل (محميّ، ضمن الموافقة) | حذف `build:` من `x-app-image` (`:52-54`) ومن `embedding` (`:1239-1241`). `image:` بصيغة D-1. تعليقٌ يشرح الفصل | devops-engineer |
| `docker-compose.build.yml` | إنشاء (محميّ، ضمن الموافقة) | `build:` لعشر خدمات، ولا شيء غيره (§3.2) | devops-engineer |
| `.github/workflows/ci.yml` | تعديل (محميّ، ضمن الموافقة) | `jobs.integration.env.COMPOSE_FILE` تصير `docker-compose.yml:docker-compose.test.yml:docker-compose.build.yml`، مع تعديل التعليق فوقها. لا تغيير آخر | devops-engineer |
| `.github/workflows/release.yml` | إنشاء (محميّ، ضمن الموافقة) | مسار الإصدار على `v*` (§3.4) | devops-engineer |
| `deploy/release/build-push.sh` | إنشاء، بصلاحيّة `100755` (محميّ، ضمن الموافقة) | البناء والرفع وطباعة البصمات (§3.3) | devops-engineer |
| `.env.example` | تعديل (قالبٌ مسموح) | كتلة تعليقٍ فقط: سطر `COMPOSE_FILE` معلَّق، ومتغيّرات الصور الأربعة معلَّقة مع شرح (§3.5) | devops-engineer |
| `tests/unit/test_ci_workflow_truthfulness.py` | تعديل | السطر `:348` ورسالته: القيمة الجديدة (AC-1.7). في الالتزام نفسه مع `ci.yml` | devops-engineer |
| `tests/unit/test_compose_build_overlay.py` | إنشاء | AC-1.1…1.4، وAC-1.6، ووكيلٌ آليّ لـAC-1.5 وAC-1.9، وAC-5.1 | devops-engineer |
| `tests/unit/test_release_build_push.py` | إنشاء | AC-2.1…2.3، وAC-3.1…3.8، وحوافّ السكربت | devops-engineer |
| `tests/unit/test_release_workflow.py` | إنشاء | AC-4.1…4.4، وحارس الانحراف بين `quality` في الملفّين | devops-engineer |
| `docs/quickstart.md` | تعديل | §2.1 خطوة السطر، و§2.2 ملاحظة البناء الأوّل، و§6 أمرا `-f` (`:230-231`)، و§9 صفّ «pull access denied» (§3.6) | devops-engineer |
| `docs/stack-commands.md` | تعديل | الصفّ 1، والصفّان 35 و37، والأقسام §35 و§36 و§37 (`:447,466-467,490,510`)، وقسمٌ جديد «و) الإصدار» بصفَّي 38 و39 (§3.6) | devops-engineer |
| `README.md` | تعديل | «البدء السريع» (`:152-156`)، وسطرا الشجرة (`:123-124`)، و`deploy/release/` في الشجرة | devops-engineer |
| `docs/server-gpu-summary.html` | تعديل **في النهاية**، يتولّاه المنسّق مع لوحة §4 | انظر §6 البند 6 | المنسّق |

**لا تُلمس** (لكلٍّ سببه):
- `Dockerfile` و`services/embedding/Dockerfile`: غير مشمولين بالموافقة، ولا حاجة لهما.
- `docker-compose.test.yml:5` و`docker-compose.wsl-gpu.yml:4`: تعليقات رأسٍ فيها `-f`. غير مشمولةٍ بالموافقة، وتبقى صحيحةً حين توجد الصور محلّيّاً.
- `deploy/load/run.sh:125` (يقترح `up -d --build`): يعمل مع السطر الجديد، وخارج الموافقة.
- `deploy/resource-budget.sh` و`deploy/rolling-deploy.sh`: لا يتأثّران. السطر في `.env` لا يُصدَّر إلى الصدفة، و`rolling-deploy.sh:69` يقرأ `docker compose config` الذي يحترم `.env`.
- `docs/design/08-local-runbook.md`: وثيقةٌ مُلزِمة، وأوامر `build` فيها تبقى صحيحةً مع السطر (قرار BA في US-5).
- `.importlinter` و`src/` و`CLAUDE.md`.

## 3. العقود

### الواجهات البرمجية

| الطريقة | المسار | الطلب | الاستجابة | الأخطاء | الصلاحية |
|---|---|---|---|---|---|
| — | — | — | — | — | — |

**لا واجهات HTTP جديدة، ولا تغيير في `docs/design/openapi.yaml`.** العقود هنا ثلاثة: شكل Compose، وواجهة السكربت، وشكل مسار CI.

### 3.1 قرارات التصميم

- **D-1: صيغة `image:` بمتغيّرٍ للمستودع ومتغيّرٍ للوسم لكلّ صورة:**

  ```yaml
  ${APP_IMAGE_REPO:-aizzak/app}:${APP_IMAGE_TAG:-dev}
  ${EMBEDDING_IMAGE_REPO:-aizzak/embedding}:${EMBEDDING_IMAGE_TAG:-dev}
  ```

  - **لماذا هذا الشكل مع المستودع الواحد؟** الاسم المحلّيّ والاسم على الخادم يختلفان في **الموضعين** معاً:

    | | المستودع | الوسم |
    |---|---|---|
    | محلّيّاً (القاعدة 1) | `aizzak/app` و`aizzak/embedding`، مستودعان | `dev` |
    | على الخادم | `anastawahia/aizzak`، مستودعٌ واحد | `app-<v>` و`embedding-<v>` |

    - **رُفض:** متغيّر مستودعٍ مشترك مع بادئةٍ ثابتةٍ في Compose، مثل `${AIZZAK_IMAGE_REPO:-…}:app-${AIZZAK_VERSION:-dev}`. يحوّل الاسم المحلّيّ إلى `aizzak/app:app-dev`، فيكسر القاعدة 1. ولا يوجد تعبيرٌ مشتركٌ واحد يعطي `aizzak/app` و`aizzak/embedding` محلّيّاً و`anastawahia/aizzak` على الخادم.
    - **فالبادئة تسكن قيمة الوسم على الخادم**، والمتغيّرات الأربعة تبقى. وتبقى منها `APP_IMAGE_TAG` و`EMBEDDING_IMAGE_TAG` الموجودتان اليوم (A-2).
  - **الأثر على معايير القبول:** AC-1.2 وAC-1.4 تُقرآن بهذه الأسماء بدل `AIZZAK_REGISTRY`، كما تسمح القصص نفسها: «الصيغة النهائيّة حسب Q-2». التفصيل في §5.1.
  - **فائدةٌ لاحقة:** 2.5/5.2 تبدّل صورة التضمين إلى CUDA بقيمة الوسم وحدها (`EMBEDDING_IMAGE_TAG=embedding-cuda-<v>`)، دون مفتاح `image:` جديد.
  - **على الخادم:** يكتب `.env` (لاحقاً بواسطة `install.sh` في 5.2) القيم التالية:

    ```
    APP_IMAGE_REPO=anastawahia/aizzak
    APP_IMAGE_TAG=app-<v>
    EMBEDDING_IMAGE_REPO=anastawahia/aizzak
    EMBEDDING_IMAGE_TAG=embedding-<v>
    ```

    مقيسٌ في النموذج التجريبيّ: `docker compose config --images` بهذه القيم يعطي `anastawahia/aizzak:app-2026.10.1` و`anastawahia/aizzak:embedding-2026.10.1`. وبلا قيم، يعطي `aizzak/app:dev` و`aizzak/embedding:dev`، و`services` متطابقة مع ناتج اليوم (§3.2).
  - **خطأ تشغيلٍ ممكن** (يُوثَّق، ويحرسه 5.2/7.1): وسمٌ بلا بادئة، مثل `APP_IMAGE_TAG=2026.10.1`، يفشل سحبه بـ`not found`، أي فشلٌ صاخب. ووسمٌ ببادئةٍ خاطئة، مثل `APP_IMAGE_TAG=embedding-…`، يشغّل الصورة الخطأ. لذلك يجب أن يكتب `install.sh` الوسمين من رقمٍ واحد، وأن يفحص 7.1 أنّ وسم كلّ صورةٍ يبدأ ببادئتها.
- **D-2: القيم الافتراضيّة تبقى أسماء اليوم** (`aizzak/app:dev`، و`aizzak/embedding:dev`)، تطبيقاً للقاعدة 1 والافتراض A-3. منع `dev` على الخادم مسؤوليّة 5.2/7.1.
  - ملاحظةٌ لـ7.1: فحص «لا `:dev` في ملفّات الحزمة» يجب أن يقرأ **الناتج المحلول** بـ`.env` الحزمة، لا نصّ `docker-compose.yml` الخام، لأنّ الخام سيبقى فيه `:-dev`.
- **D-3: لا `pull_policy` في الملفّ الإضافيّ** (قرار المالك، البديل (أ)). سلوك السحب يبقى كما اليوم، وR-3 **خطرٌ قائمٌ مقبول** لا تعالجه هذه الميزة.
  - **مقيس (2026‑10‑13، بـ`docker compose --dry-run` في مجلّدٍ مؤقّت):** خدمةٌ فيها `image:` و`build:` وصورتها غائبة يحاول Compose **سحبها من Docker Hub أوّلاً**، ثمّ يبني بعد `authorization failed`. هذا سلوك اليوم نفسه، وسيبقى مع الملفّ الإضافيّ.
  - **معنى ذلك:** الخطر قديمٌ لا يأتي مع الفصل. لو حجز أحدٌ المساحة `aizzak` ورفع `app:dev`، لشُغّلت صورته على نسخةٍ جديدة من المستودع، أو على جهاز المالك إن حُذفت صورته المحلّيّة.
  - **التخفيف بلا تغييرٍ في Compose:** الوثائق (§3.6) تطلب `docker compose build` **قبل** أوّل `up -d` على نسخةٍ جديدة. أمر `build` لا يسحب الصورة الهدف، فإذا وُجدت الصورة لم يحاول `up` السحب أصلاً.
  - **وما يبقى مفتوحاً:** المسار الذي ينسى الملفّ الإضافيّ يحاول السحب من `aizzak/…`، وكذلك أيّ `up` لصورةٍ غائبة. الحلّ الجذريّ، أي حجز المساحة `aizzak` على Docker Hub أو `pull_policy: never`، عُرض ولم يُختر.
- **D-4: سكربتٌ واحدٌ هو منطق الإصدار.** CI لا يحتوي `docker build` ولا `docker push` (AC-4.3). السكربت لا يسجّل الدخول إلى السجلّ (A-6). الدخول خطوةٌ قبله: في CI بـ`--password-stdin`، ومحلّيّاً بجلسة `docker login` قائمة.
- **D-5: الابتلاع ثمّ الرفع.** السكربت يبني **كلّ** الصور أوّلاً (`--load`)، ثمّ يرفعها واحدةً واحدة.
  - **السبب:** فشل البناء، كانقطاع Hugging Face (`services/embedding/Dockerfile:30-32`)، يقع قبل أيّ رفع.
  - **البصمة:** تُقرأ بعد الرفع من السجلّ نفسه، بـ`docker buildx imagetools inspect --format '{{.Manifest.Digest}}'`. هذه البصمة التي يحلّها `docker pull`، ويقارن بها AC-2.4.
- **D-6: نسخةٌ مرفوعةٌ جزئيّاً تُهجَر.**
  - إن رُفعت صورةٌ (مثل `app-<T>`) وفشلت التالية، فالإصدار `<T>` لا يُكمَل ولا يُعاد. يُصدَر `<T+1>` (`YYYY.M.(N+1)`).
  - **السبب:** فحص «الوسم موجود» (A-5) يرفض الإعادة أصلاً، والاستكمال اليدويّ من CI مستحيل لأنّ العدّاء زال.
  - السكربت يطبع هذا صراحةً عند الفشل، ورأسه يوثّقه. هذا هو جواب Q-5 لـ«الاستكمال».
- **D-7: حارس الخصوصيّة على المستودع الواحد.**
  - **السبب:** Docker Hub ينشئ المستودع تلقائيّاً عند أوّل رفع، **بخصوصيّة الحساب الافتراضيّة**، وهي عامّةٌ ما لم تُغيَّر. هكذا وُلدت `ث‑7`.
  - **قبل البناء:** السكربت يطلب `https://hub.docker.com/v2/repositories/anastawahia/aizzak/` **بلا اعتماد**، مرّةً واحدة:
    - `404`: خاصٌّ أو غير موجود، فيكمل.
    - `200`: عامّ، فيرفض بالرمز 7.
    - غير ذلك: «لا يمكن التحقّق»، فيرفض بالرمز 7.
  - **بعد كلّ رفع:** يكرّر الطلب نفسه، ويفشل مغلقاً: أيّ ردٍّ غير `404` بالضبط (200 أو 429 أو 5xx أو انتهاء المهلة) يخرج بالرمز 9 ورسالةٍ صاخبة، ويُهجر الإصدار (D-6) فيُصدر الرقم التالي.
  - **الشرط المسبق على المالك:** إنشاء المستودع `anastawahia/aizzak` **خاصّاً** قبل أوّل رفع (§6).
- **D-8: الأسرار في GitHub Environment اسمها `release`**، مقيّدة بوسوم `v*` (Deployment tag rule)، لا أسرار مستودع.
  - **السبب:** المستودع عامّ، وسرّ المستودع يصل إليه أيّ workflow يدفعه صاحب صلاحيّة كتابةٍ على أيّ فرع. سرّ البيئة يصل إلى وظيفة `environment: release` على وسم `v*` فقط.
  - **الأسماء:** `DOCKERHUB_USERNAME` و`DOCKERHUB_TOKEN`.
  - إن وضعهما المالك أسرارَ مستودعٍ بدل ذلك، فالمسار يعمل كما هو. لا تغيير في الملفّ.
  - **نطاق المفتاح:** Personal Access Token بصلاحيّة **Read & Write**، بلا Delete. **بحسب علمي** لا يقبل مفتاح الحساب الشخصيّ على Docker Hub التقييد بمستودعٍ واحد، فهو يكتب في كلّ مستودعات الحساب، ومنها `aizzak-runpod`. التقييد بمستودعٍ واحد متاحٌ في مفاتيح المنظّمات (Organization Access Tokens) فقط. خطرٌ متبقٍّ مقبول، مسجّلٌ في §7.
- **D-9: لا actions من طرفٍ ثالث.** الدخول بـ`docker login --password-stdin`، لا بـ`docker/login-action`، ولا اعتماد جديد. الـactions المستعملة كلّها رسميّةٌ من GitHub: `actions/checkout@v4` و`actions/setup-python@v5` (كما في `ci.yml`)، و`actions/upload-artifact@v4` جديدةٌ للبصمات.

### 3.2 شكل Compose بالضبط

**`docker-compose.yml`:** الأسطر `51-55` تصير كما يلي (والتعليق بالإنجليزيّة):

```yaml
# The ONE application image every app-side process runs. This file only NAMES
# it -- it never builds it (server-gpu-plan.md 1.1): a server pulls a released
# image and has no source tree to build from. HOW it is built lives in
# docker-compose.build.yml, which a development machine adds through
#   COMPOSE_FILE=docker-compose.yml:docker-compose.build.yml        (in .env)
# The defaults are this machine's own image names. A server pulls the ONE
# private repository, the image told apart by a tag prefix:
#   APP_IMAGE_REPO=anastawahia/aizzak  APP_IMAGE_TAG=app-<YYYY.M.N>
x-app-image: &app-image
  image: ${APP_IMAGE_REPO:-aizzak/app}:${APP_IMAGE_TAG:-dev}
```

**خدمة `embedding`:** الأسطر `1238-1241` تصير سطراً واحداً، مع تعليقٍ من سطرين يحيل إلى التعليق أعلاه:

```yaml
    image: ${EMBEDDING_IMAGE_REPO:-aizzak/embedding}:${EMBEDDING_IMAGE_TAG:-dev}
```

**`docker-compose.build.yml`** (جديد). مفتاحه الأعلى الوحيد `services`. المرساة داخل أوّل خدمة، فلا مفتاح `x-*`:

```yaml
# Development overlay: HOW the two platform images are built
# (server-gpu-plan.md 1.1). docker-compose.yml only names them; a server pulls
# released images and never reads this file (it is not in the 5.1 bundle).
#
# Enable it on a development machine with ONE line in .env:
#   COMPOSE_FILE=docker-compose.yml:docker-compose.build.yml
# (with the WSL GPU overlay: docker-compose.yml:docker-compose.wsl-gpu.yml:docker-compose.build.yml)
# A command that passes `-f` explicitly ignores COMPOSE_FILE and must add
# `-f docker-compose.build.yml` itself.
#
# Only `build` belongs here (tests/unit/test_compose_build_overlay.py).
services:
  migrate: &app-build
    build:
      context: .
      dockerfile: Dockerfile
  app: *app-build
  worker-memory: *app-build
  worker-knowledge: *app-build
  worker-media: *app-build
  outbox-relay: *app-build
  wal-shipper: *app-build
  backup: *app-build
  ops-scheduler: *app-build
  embedding:
    build:
      context: .
      dockerfile: services/embedding/Dockerfile
```

**مقيسٌ على نموذجٍ بهذا الشكل بالضبط** (أُعيد القياس في دورة الإعادة 1، في مجلّدٍ مؤقّت، بـ`--env-file .env.example`، دون لمس المستودع):

| الفحص | النتيجة |
|---|---|
| الأساسيّ وحده: `config -q` | رمز 0 |
| الأساسيّ وحده: عدد الخدمات التي فيها `build` | 0 (اليوم 9) |
| الأساسيّ مع الإضافيّ: `services` | متطابقة حرفيّاً مع ناتج اليوم، ومع `--profile backup` كذلك |
| الأساسيّ مع الإضافيّ: `diff` الكامل مع ناتج اليوم | 3 أسطر محذوفة فقط: `build:` و`context: .` و`dockerfile: Dockerfile` **داخل كتلة `x-app-image:` العليا** |
| الأساسيّ وحده، بقيم الخادم في البيئة: `config --images` | `anastawahia/aizzak:app-2026.10.1` و`anastawahia/aizzak:embedding-2026.10.1` |

**اكتشافٌ يغيّر صياغة AC-1.5:**
- `docker compose config` يطبع مفاتيح `x-*` العليا كما هي.
- لذلك حذف `build:` من المرساة، وهو ما يطلبه FR-1 نفسه، يُظهر حتماً 3 أسطر في `diff`. أي أنّ «`diff` فارغ» مستحيلٌ بأيّ تصميمٍ يحقّق FR-1.
- **الصياغة الصحيحة لـAC-1.5:**
  - `services` في `docker compose config --format json` متطابقةٌ قبل التعديل وبعده.
  - و`diff` الكامل لا يحتوي إلّا الأسطر الثلاثة تحت `x-app-image`.
- هذا ليس قراراً. هو تصحيحٌ لمعيارٍ بُني على افتراضٍ ثبت خطؤه بالقياس. الإجراء في §5، والصياغة في §5.1.

**سطر المالك المحلّيّ (Q-8):** يعمل في الحالتين. ينفّذه المالك بنفسه، لأنّ الوكلاء لا يقرؤون `.env`، و**فقط بعد** أن تحتوي الشجرة `docker-compose.build.yml`، أي بعد الدمج أو بعد التحوّل إلى الفرع:

```bash
cd /home/AIZZAK
env | grep -c '^COMPOSE_FILE='     # must print 0: an exported COMPOSE_FILE overrides .env
if grep -q '^COMPOSE_FILE=' .env; then
  grep -q '^COMPOSE_FILE=.*docker-compose\.build\.yml' .env \
    || sed -i '/^COMPOSE_FILE=/ s/$/:docker-compose.build.yml/' .env
else
  printf '\nCOMPOSE_FILE=docker-compose.yml:docker-compose.build.yml\n' >> .env
fi
grep '^COMPOSE_FILE=' .env           # not a secret; prints this one line only
```

| حالة `.env` قبل التعديل | النتيجة |
|---|---|
| لا سطر `COMPOSE_FILE` | `COMPOSE_FILE=docker-compose.yml:docker-compose.build.yml` |
| `COMPOSE_FILE=docker-compose.yml:docker-compose.wsl-gpu.yml` | `COMPOSE_FILE=docker-compose.yml:docker-compose.wsl-gpu.yml:docker-compose.build.yml` |
| القيمة بين علامتَي تنصيص | `sed` يُلحق بعد العلامة فيكسرها. **عدّل السطر يدويّاً** (تذكره الوثائق) |

> **مأزقٌ يُوثَّق:** بعد السطر، أيّ `git switch` إلى commit **قبل** هذه الميزة يُفشل كلّ أمر `docker compose` بـ«`docker-compose.build.yml`: no such file». الحلّ: عطّل السطر مؤقّتاً (`#`) عند العمل على فرعٍ قديم. يُذكر في `stack-commands.md` §37 والقسم الجديد.

### 3.3 السكربت `deploy/release/build-push.sh`: واجهة الاستعمال

```
deploy/release/build-push.sh [--gates-checked-by-workflow] [--digest-file PATH] <VERSION>
deploy/release/build-push.sh --preflight <VERSION>
deploy/release/build-push.sh --check-tag <VERSION>
deploy/release/build-push.sh --list-images
deploy/release/build-push.sh --help
```

**المستودع والوسوم:**
- المستودع واحدٌ ثابت: `anastawahia/aizzak`.
- الوسيط هو **رقم الإصدار** وحده. السكربت يشتقّ منه وسم كلّ صورة بالشكل `<prefix>-<VERSION>`:

  | الصورة | البادئة | ملفّ البناء | المرجع للإصدار `2026.10.1` |
  |---|---|---|---|
  | التطبيق | `app` | `Dockerfile` | `anastawahia/aizzak:app-2026.10.1` |
  | التضمين على المعالج | `embedding` | `services/embedding/Dockerfile` | `anastawahia/aizzak:embedding-2026.10.1` |
  | التضمين على الكرت | `embedding-cuda` | محجوز، لا يُبنى (2.4) | — |

- لا وسم بلا بادئة، ولا `dev` ولا `latest` ولا `<prefix>-latest`.

**الخيارات:**
- **`<VERSION>`:** رقم الإصدار **بلا** `v`. الصيغة: `^20[0-9]{2}\.(1[0-2]|[1-9])\.[1-9][0-9]*$`.
  - تقبل: `2026.10.1` و`2026.1.12`.
  - ترفض: `dev`، و`latest`، و`""`، و`v2026.10.1`، و`2026.10`، و`2026.10.1-rc`، و`2026.01.1`، وأيّ مسافة، **وأيّ رقمٍ يحمل بادئة** (`app-2026.10.1`). البادئة يضعها السكربت، لا المستعمل.
- **`--check-tag`:** يتحقّق من **صيغة الرقم** فقط، ويخرج بـ0 أو 2. لا git ولا docker. تستعمله الاختبارات AC-2.1 وAC-2.2.
  - **وجود الوسم في السجلّ** لا يفحصه هذا الخيار. يفحصه المسار الكامل (`require_tag_absent`) لكلّ وسمٍ ببادئته: `app-<V>` ثمّ `embedding-<V>`. أيّ وسمٍ منهما موجودٌ يرفض الإصدار كلّه.
- **`--list-images`:** يطبع قائمة الصور، ولا يحتاج شيئاً:

  ```
  app Dockerfile
  embedding services/embedding/Dockerfile
  embedding-cuda reserved:2.4
  ```

- **`--preflight`:** الوسم، وجذر المستودع، والشجرة النظيفة، والانتماء إلى `master`. لا `gh` ولا `docker`. تستعمله وظيفة `preflight` في CI، ليفشل وسمٌ خاطئ في ثوانٍ لا بعد البوّابات.
- **`--gates-checked-by-workflow`:** يتخطّى فحص `gh`، لأنّ `needs: quality` في المسار نفسه هو الفحص (Q-4 ج). **يُرفض بالرمز 2 ما لم يكن `GITHUB_ACTIONS=true`**، حتى لا يُستعمل محلّيّاً بالخطأ.
- **`--digest-file PATH`:** يُلحق بعد كلّ رفعٍ ناجح سطراً بالصيغة `<repo>:<prefix>-<VERSION> sha256:<64hex> <commit-sha>`. الكتابة **بعد كلّ رفع** لا في النهاية، فيبقى أثر الرفع الجزئيّ (NFR-7).

**الثوابت:** في رأس السكربت، ولا يغيّرها متغيّر بيئة.

```bash
readonly IMAGE_REPO="anastawahia/aizzak"         # ONE private repository (owner, 2026-10-13)
readonly GITHUB_REPO="anastawahia/aizzak"
readonly CI_WORKFLOW="ci.yml"
readonly PLATFORM="linux/amd64"
readonly VERSION_RE='^20[0-9]{2}\.(1[0-2]|[1-9])\.[1-9][0-9]*$'
readonly IMAGES=(                                 # "<tag prefix> <dockerfile>"
  "app Dockerfile"
  "embedding services/embedding/Dockerfile"
)
readonly RESERVED_IMAGES=(                        # "<tag prefix> <plan step>" -- NOT built here
  "embedding-cuda 2.4"                            # server-gpu-plan.md 2.4 adds requirements-cuda.txt
)
```

**الدوالّ:** أسماءٌ ملزِمة، لأنّ الاختبارات ورسائل الخطأ تعتمد عليها.

```bash
usage()                                  # usage text to stderr
die <exit-code> <message...>             # "release: ERROR: <message>" to stderr, then exit
valid_version <version>                  # return 0 iff [[ $version =~ $VERSION_RE ]]
image_tag <prefix> <version>             # prints "<prefix>-<version>"
list_images                              # prints the three lines shown above
require_repo_root                        # cd "$(dirname "${BASH_SOURCE[0]}")/../.."; must equal `git rev-parse --show-toplevel`
require_clean_tree                       # `git status --porcelain --untracked-files=normal` empty
require_on_master                        # `git fetch --quiet origin master` then `git merge-base --is-ancestor HEAD FETCH_HEAD`
require_ci_green <sha>                   # gh run list --repo "$GITHUB_REPO" --workflow "$CI_WORKFLOW" --commit "$sha"
                                         #   --limit 20 --json status,conclusion,createdAt
                                         #   --jq 'if length==0 then "none" else (sort_by(.createdAt)|last|"\(.status)/\(.conclusion)") end'
                                         #   only "completed/success" passes
require_buildx_amd64                     # `docker buildx version`; `docker buildx inspect` lists linux/amd64
require_repo_private                     # curl -sS -o /dev/null -w '%{http_code}' --max-time 20 \
                                         #   "https://hub.docker.com/v2/repositories/anastawahia/aizzak/"  -> must be 404
require_tag_absent <tag>                 # `docker buildx imagetools inspect "$IMAGE_REPO:<tag>"`, called once per prefixed tag:
                                         #   rc 0 -> exists (refuse); stderr ~ /not found/ -> ok; else refuse
build_image <prefix> <dockerfile> <version> <sha>
push_image <prefix> <version> <sha>      # docker push; digest via imagetools;
                                         #   prints "anastawahia/aizzak:<prefix>-<version>@sha256:<hex>"
```

**أمر البناء بالضبط**، من جذر المستودع:

```bash
docker buildx build --platform linux/amd64 --pull --load \
  --provenance=false --sbom=false \
  --file "<dockerfile>" --tag "anastawahia/aizzak:<prefix>-<VERSION>" \
  --label "org.opencontainers.image.revision=<sha>" \
  --label "org.opencontainers.image.version=<VERSION>" \
  --label "org.opencontainers.image.title=aizzak-<prefix>" \
  --label "org.opencontainers.image.source=https://github.com/anastawahia/aizzak" \
  .
```

- **`--pull`:** الإصدار لا يعتمد على صورة أساسٍ قديمة في ذاكرة الجهاز.
- **`--provenance=false --sbom=false`:** بيانٌ واحدٌ لمنصّةٍ واحدة. سلسلة التوريد هي الموجة 8، وهي مؤجّلة.
- **الوسوم الأربعة (labels):** تحقّق NFR-7 من الصورة نفسها، دون تعديل Dockerfile. و`title` يميّز الصورة حتى لو نُسخت بوسمٍ آخر.

**الترتيب ورموز الخروج:**

| الترتيب | الفحص | الرمز عند الفشل |
|---|---|---|
| 1 | وسيطٌ مفقود، أو رقم إصدارٍ غير صالح، أو خيارٌ مجهول، أو `--gates-checked-by-workflow` خارج Actions، أو مسار `--digest-file` لا يمكن الكتابة فيه (يُفحص قبل أيّ بناء) | **2** (يطبع الاستعمال) |
| 2 | ليس جذر مستودع git، أو الشجرة متّسخة (متتبَّعٌ معدَّل، أو غير متتبَّعٍ خارج `.gitignore`) | **3** |
| 3 | `HEAD` لا ينتمي إلى `origin/master`، أو فشل الجلب | **4** |
| 4 | CI ليس `completed/success`، أو لا تشغيل، أو فشل `gh` (يُتخطّى مع `--gates-checked-by-workflow`) | **5** |
| 5 | لا `docker`، أو لا `buildx`، أو لا `linux/amd64` | **6** |
| 6 | المستودع `anastawahia/aizzak` عامّ، أو أيّ وسمٍ ببادئته (`app-<V>`، `embedding-<V>`) موجود، أو تعذّر التحقّق | **7** |
| 7 | فشل بناء أيّ صورة، **قبل أيّ رفع** | **8** |
| 8 | فشل رفع أيّ صورة، أو (بعد نجاح `docker push`) تعذّرت قراءة البصمة أو لم تطابق `^sha256:[0-9a-f]{64}$`، أو تعذّرت الكتابة في `--digest-file` (احتياطيّ)، أو كان ردّ فحص الخصوصيّة بعد أيّ رفعٍ **غير `404` بالضبط** (200 أو 429 أو 5xx أو انتهاء المهلة) | **9** |
| — | نجاح | **0** |

**رموز الخروج** لم تتغيّر في دورة الإعادة. الرمز 7 صار يشمل المستودع الواحد وكلّ وسمٍ ببادئته.

**ما يطبعه عند النجاح** على stdout. سطرا الصورتين مجرّدان، لتطابق AC-3.5 بصيغتها المعدّلة (§5.1).
- **لماذا `<repo>:<tag>@sha256:<hex>`؟** الصورتان في مستودعٍ واحد، فسطر `<repo>@sha256:` وحده لا يقول أيّ صورةٍ هي.
- هذه الصيغة مرجعٌ صالحٌ مباشرةً لـ`docker pull` ولمفتاح `image:` في Compose. Docker يتحقّق من البصمة، والوسم للقارئ.

```
release: commit <40-hex>
release: version 2026.10.1
anastawahia/aizzak:app-2026.10.1@sha256:<64-hex>
anastawahia/aizzak:embedding-2026.10.1@sha256:<64-hex>
release: OK 2026.10.1 (2 images)
```

**ما يطبعه عند الفشل الجزئيّ** (AC-3.7) على stderr، ولا يطبع سطر `OK`:

```
release: ERROR: push failed: anastawahia/aizzak:embedding-2026.10.1
release: already pushed (version 2026.10.1 is ABANDONED -- release 2026.10.2):
anastawahia/aizzak:app-2026.10.1@sha256:<64-hex>
```

**قواعد النصّ** (يفحصها AC-3.8):
- السطر الأوّل `#!/usr/bin/env bash`، وأوّل سطرٍ غير تعليقٍ بعده `set -euo pipefail`.
- لا تظهر السلسلة `.env` في الملفّ إطلاقاً، ولا في التعليقات. يُكتب «environment file» بدلها.
- لا `docker login`.
- لا خيار يقبل كلمة سرّ أو مفتاحاً.

**رأس السكربت** يوثّق:
- الاستعمال، والشروط المسبقة: `docker login` قائم، و`gh auth status` سليم، والمستودع `anastawahia/aizzak` موجودٌ وخاصّ.
- جدول البادئات، وأنّ `embedding-cuda` محجوزةٌ للخطوة 2.4.
- رموز الخروج.
- قاعدة D-6 للرفع الجزئيّ.
- أنّ CI هو الطريق المعتاد، وأنّ السكربت للطوارئ: نحو 150 ك.ب/ث، أي قرابة 7 ساعات لصورة التضمين (R-1).

### 3.4 مسار CI: `.github/workflows/release.yml`

```yaml
name: Release images

# Builds and pushes ONE release's platform images (server-gpu-plan.md 1.4) by
# running deploy/release/build-push.sh -- the same script an operator runs, so
# no build logic lives here. Fires ONLY on a pushed `v*` tag: no pull_request,
# no branches, so a fork can never reach the registry secret. The repository is
# PUBLIC: no step may print a credential. Guarded by
# tests/unit/test_release_workflow.py.
on:
  push:
    tags: ["v*"]

permissions:
  contents: read

concurrency:
  group: release
  cancel-in-progress: false

jobs:
  # Seconds, not minutes: a malformed tag or a commit that is not on master
  # fails here, before the five gates spend a runner on it.
  preflight:
    if: github.repository == 'anastawahia/aizzak'
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
          persist-credentials: false
      - name: Tag format and master ancestry
        run: deploy/release/build-push.sh --preflight "${GITHUB_REF_NAME#v}"

  # The five gates + alert rules, step for step the `quality` job of ci.yml
  # (Q-4 (c): ci.yml does not run on tags). Drift between the two is a test failure.
  quality:
    needs: preflight
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      # ... exactly the `uses:`/`run:` steps of ci.yml jobs.quality, same order ...

  publish:
    needs: [preflight, quality]
    runs-on: ubuntu-latest
    timeout-minutes: 60
    environment: release
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
          persist-credentials: false

      - name: Registry secrets are set
        env:
          DOCKERHUB_USERNAME: ${{ secrets.DOCKERHUB_USERNAME }}
          DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}
        run: |
          missing=""
          [ -n "${DOCKERHUB_USERNAME}" ] || missing="${missing} DOCKERHUB_USERNAME"
          [ -n "${DOCKERHUB_TOKEN}" ] || missing="${missing} DOCKERHUB_TOKEN"
          if [ -n "${missing}" ]; then
            echo "::error::release secret(s) not set in environment 'release':${missing}"
            exit 1
          fi

      # Q-9: measured on the first run; frees space only when short.
      - name: Runner disk
        run: |
          df -h /
          if [ "$(df --output=avail -k / | tail -1)" -lt 26214400 ]; then
            sudo rm -rf /usr/local/lib/android /usr/share/dotnet /opt/ghc
            df -h /
          fi

      - name: Log in to Docker Hub (token on stdin)
        env:
          DOCKERHUB_USERNAME: ${{ secrets.DOCKERHUB_USERNAME }}
          DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}
        run: |
          printf '%s' "${DOCKERHUB_TOKEN}" |
            docker login docker.io --username "${DOCKERHUB_USERNAME}" --password-stdin

      - name: Build and push (deploy/release/build-push.sh)
        run: |
          deploy/release/build-push.sh --gates-checked-by-workflow \
            --digest-file "${RUNNER_TEMP}/release-digests.txt" "${GITHUB_REF_NAME#v}"

      - name: Digests to the job summary
        if: always()
        run: |
          if [ -f "${RUNNER_TEMP}/release-digests.txt" ]; then
            cat "${RUNNER_TEMP}/release-digests.txt" >> "${GITHUB_STEP_SUMMARY}"
          fi

      # Unmasked record (NFR-7): a secret's value is masked in logs, so if
      # DOCKERHUB_USERNAME equals the namespace the log shows
      # `***/aizzak:app-<v>@sha256:…` (the tag prefix still tells the images apart).
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: release-digests
          path: ${{ runner.temp }}/release-digests.txt
          if-no-files-found: ignore
          retention-days: 90

      - name: Runner disk after
        if: always()
        run: df -h /

      - name: Log out of Docker Hub
        if: always()
        run: docker logout docker.io
```

**قواعد ملزِمة** (يفحصها `test_release_workflow.py`):
- `${{ secrets.* }}` تظهر **فقط** قيماً تحت `env:` لخطوة، ولا تظهر أبداً داخل نصّ `run:`. الأسماء المسموحة اثنان فقط: `DOCKERHUB_USERNAME` و`DOCKERHUB_TOKEN`.
- الوسم يُقرأ من `GITHUB_REF_NAME` (متغيّر بيئة)، لا من `${{ github.ref_name }}` داخل `run:`. هذا يمنع حقن النصّ.
- كلّ `run:` تعدّيه `bash -n`.

**الوسوم في git وفي السجلّ:**
- وسم git هو `v<VERSION>`، مثل `v2026.10.1`. المسار يزيل `v` ويمرّر الرقم.
- السكربت يرفع `anastawahia/aizzak:app-<VERSION>` و`anastawahia/aizzak:embedding-<VERSION>`.
- البصمات تظهر في ثلاثة أماكن: سطر السجلّ، وملخّص الوظيفة، والـartifact `release-digests` بأسطر `<repo>:<prefix>-<VERSION> sha256:<hex> <commit>`.

### 3.5 `.env.example` (تعليقٌ فقط)

تُضاف كتلةٌ قرب أعلى الملفّ، بعد `LOG_LEVEL`. **كلّ سطرٍ فيها يبدأ بـ`#`:**

```bash
# ---------------------------------------------------------------------------
# Platform images (server-gpu-plan.md 1.1/1.2). docker-compose.yml only NAMES
# the two images; docker-compose.build.yml says how to BUILD them.
#
# Development machine: uncomment to build locally (add the WSL GPU overlay in
# the middle if you use it). Left commented on purpose: CI copies this file to
# its environment file, and a server must never build.
#COMPOSE_FILE=docker-compose.yml:docker-compose.build.yml
#
# Server: pull a released version instead, from the ONE private repository;
# the tag prefix says which image. One version for both; never `dev` or
# `latest`, never a tag without its prefix. Defaults: aizzak/app, aizzak/embedding, `dev`.
#APP_IMAGE_REPO=anastawahia/aizzak
#APP_IMAGE_TAG=app-2026.10.1
#EMBEDDING_IMAGE_REPO=anastawahia/aizzak
#EMBEDDING_IMAGE_TAG=embedding-2026.10.1
```

### 3.6 الوثائق (US-5)

كلّها عربيّةٌ داخل `<div dir="rtl">`، والأوامر والمسارات بالإنجليزيّة. لا قيمة سرّ في أيّ منها.

- **`docs/quickstart.md`:**
  - **§2.1، بعد `cp .env.example .env`:**
    - خطوة «فعّل سطر `COMPOSE_FILE`»، بحالتَي Q-8 كما في الجدول §3.2.
    - تحذير: بدون السطر، `docker compose up -d` يحاول سحب `aizzak/app:dev` من مساحةٍ على Docker Hub لا نملكها، ولا يبني.
  - **§2.2، قبل `docker compose up -d`:** خطوة `docker compose build` (D-3).
    - السبب: `build` لا يسحب الصورة الهدف. ومع الصور موجودةً، لا يحاول `up` السحب من المساحة `aizzak` غير المحجوزة (R-3).
    - جملةٌ تشرح أنّ `up -d` وحده على نسخةٍ جديدة يطبع أوّلاً `Pulling … authorization failed` ثمّ يبني. هذا سلوك Compose اليوم، وليس عطلاً.
  - **§6:** أمرا `-f` (`:230-231`) يُضاف إليهما `-f docker-compose.build.yml`، مع جملةٍ تقول إنّ `-f` الصريح يتجاهل `COMPOSE_FILE`.
  - **§9:** صفٌّ جديد:
    - العَرَض: `pull access denied for aizzak/app` (أو `aizzak/embedding`).
    - الفحص: سطر `COMPOSE_FILE` غائب، أو أمرٌ بـ`-f` صريحٍ نسي `docker-compose.build.yml`.
- **`docs/stack-commands.md`:**
  - **الصفّ 1:** لم يعد «الخطوة الوحيدة». تُذكر معه خطوة السطر.
  - **الصفّان 35 و37، والأقسام §35 و§36 و§37:** يُضاف `-f docker-compose.build.yml` حيث يلزم البناء.
  - **فقرة `:510`:** تعطي صيغة الملفّات الثلاثة `docker-compose.yml:docker-compose.wsl-gpu.yml:docker-compose.build.yml`.
  - **قسمٌ جديد «و) الإصدار»** بصفّين:
    - **38** `deploy/release/build-push.sh <YYYY.M.N>`: للطوارئ. يذكر الشروط ورموز الخروج وD-6.
    - **39** `git tag v<YYYY.M.N> <sha-on-master> && git push origin v<YYYY.M.N>`: الطريق المعتاد، عبر CI.
  - **شرح القسم الجديد** يذكر:
    - المستودع الخاصّ الواحد `anastawahia/aizzak` وجدول البادئات (§3.3)، وقيم `.env` على الخادم (D-1).
    - أسماء الأسرار والبيئة `release` (D-8).
    - مكان البصمات: سطر السجلّ، وملخّص الوظيفة، وartifact اسمه `release-digests`.
    - مأزق الفروع القديمة (§3.2).
  - **السطر «آخر مطابقةٍ مع الواقع»** يصير 2026‑10‑13.
- **`README.md`:**
  - «البدء السريع»: سطرٌ بين `cp` و`up` يحيل إلى خطوة `COMPOSE_FILE`.
  - الشجرة: `docker-compose.yml (+ .test · .wsl-gpu · .build)` و`.github/workflows/ci.yml · release.yml`، وسطر `release/` تحت `deploy/`.

## 4. البيانات

**لا تغيير.** لا جداول، ولا ترحيل Alembic، ولا RLS، ولا أدوار. الخطوة 5 (data-engineer) تُتخطّى.

## 5. خطة الاختبار المقترحة

اختبارات الوحدة كلّها تحت `tests/unit/`، وتجري ضمن `pytest tests/unit tests/architecture tests/eval` بلا مكدّسٍ حيّ.

- **الاختبارات التي تحتاج `docker` CLI** تستعمل `pytest.mark.skipif(shutil.which("docker") is None)`، كما في `test_monitoring_host_postgres_acceptance.py:197`.
  - تستدعي `docker compose config` فقط، وهو لا يتّصل بأيّ خدمة.
  - تمرّر `-f` و`--env-file .env.example` صراحةً، وتحذف `COMPOSE_FILE` من البيئة. فلا يدخل `.env` المالك أبداً.
  - تجري في وظيفة `quality` على CI، لأنّ Docker موجودٌ على العدّاء.
- **اختبار السكربت:**
  - ينسخ `build-push.sh` إلى مستودع git مؤقّت، فيه `Dockerfile` و`services/embedding/Dockerfile` وهميّان، وله `origin` مستودعٌ عارٍ (bare) محلّيّ عليه `master`.
  - يضع أوّل `PATH` بدائل Python لـ`docker` و`gh` و`curl`. كلّ بديلٍ يسجّل نداءاته في `calls.jsonl`، وتتحكّم في سلوكه متغيّراتٌ `FAKE_*`، منها `FAKE_DOCKER_EXISTING` و`FAKE_DOCKER_FAIL_PUSH` و`FAKE_DOCKER_FAIL_BUILD` و`FAKE_DOCKER_NO_BUILDX` و`FAKE_GH_OUTPUT` و`FAKE_GH_RC` و`FAKE_CURL_CODES`.
  - `git` حقيقيّ، ولا شبكة.

| AC | المستوى | المكان: الاختبار |
|---|---|---|
| AC-1.1 | وحدة | `test_compose_build_overlay.py::test_base_file_has_no_build_key_in_any_service_or_extension`. يمشي كلّ الخدمات وكلّ مفتاح `x-*` بعمق، ويستعمل `yaml.safe_load` الذي يحلّ `<<` |
| AC-1.2 | وحدة | `::test_every_platform_image_service_has_a_build_entry`. الخدمات التي تبدأ صورتها بـ`${APP_IMAGE_REPO:-aizzak/app}:` أو `${EMBEDDING_IMAGE_REPO:-aizzak/embedding}:` عددها 10، ومنها `backup`. لكلٍّ منها `context == "."` و`dockerfile` الصحيح. ولا خدمة في الإضافيّ غائبة عن الأساسيّ |
| AC-1.3 | وحدة | `::test_the_overlay_only_carries_build`. المفتاح الأعلى `{"services"}` وحده، ومفاتيح كلّ خدمة `== {"build"}`. أيّ `pull_policy` يُفشل الاختبار (قرار المالك، D-3) |
| AC-1.4 | وحدة | `::test_platform_image_defaults_resolve_to_todays_local_names`. حلّ `${VAR:-default}` بتعبيرٍ نمطيّ يعطي `aizzak/app:dev` و`aizzak/embedding:dev`. والنصّ الخام يحتوي `APP_IMAGE_REPO` و`APP_IMAGE_TAG` و`EMBEDDING_IMAGE_REPO` و`EMBEDDING_IMAGE_TAG` (بدل `AIZZAK_REGISTRY`، حسب D-1) |
| AC-1.5 | Docker، المالك، **بصيغةٍ مصحّحة** (§3.2) | يدويّ. الإجراء أدناه: يطبع بصمات sha256 فقط، فلا يظهر سرّ. **وكيلٌ آليّ:** `::test_overlay_renders_todays_build_and_image_names` (يحتاج docker). مع `--profile backup` و`.env.example`: 10 خدمات فيها `build`، والسياق جذر المستودع، والـdockerfile الصحيح، والصور `aizzak/app:dev` و`aizzak/embedding:dev`، ولا `pull_policy` في أيّ خدمة |
| AC-1.6 | وحدة (يحتاج docker) | `::test_base_file_alone_renders_and_builds_nothing`. مجلّد `tmp_path` فيه `docker-compose.yml` و`.env` منسوخٌ من `.env.example`: `config -q` برمز 0، و`--format json` بلا أيّ خدمةٍ فيها `build` |
| AC-1.7 | وحدة | تعديل `test_ci_workflow_truthfulness.py:348` إلى `"docker-compose.yml:docker-compose.test.yml:docker-compose.build.yml"`، ورسالته تذكر أنّ `--build migrate` يحتاج الملفّ الإضافيّ. في الالتزام نفسه مع `ci.yml` |
| AC-1.8 | CI | دفع الفرع، ثمّ `gh run watch`: وظيفتا `quality` و`integration` خضراوان، وسجلّ خطوة `Provision` يُظهر `Building` لـ`migrate` ولا يُظهر `Pulling aizzak/app` |
| AC-1.9 | Docker، المالك | بعد السطر: `docker compose config --images \| sort -u` (بلا أسرار) يحتوي `aizzak/app:dev` و`aizzak/embedding:dev`. ثمّ `docker compose build app embedding` |
| AC-1.10 | الفحوص الخمسة | `ruff format --check .`، و`ruff check .`، و`mypy src`، و`lint-imports`، و`pytest tests/unit tests/architecture tests/eval`. و`git diff master -- .importlinter` فارغ |
| AC-2.1 | وحدة | `test_release_build_push.py::test_valid_tags_are_accepted[2026.10.1, 2026.1.12]`، عبر `--check-tag`، والرمز 0 |
| AC-2.2 | وحدة | `::test_invalid_tags_are_refused_before_any_docker_call`. مُعلَّمٌ بالقيم: `dev`، و`latest`، و`""`، و`v2026.10.1`، و`2026.10`، و`2026.10.1-rc`، و`2026.01.1`، و`"2026.10.1 x"`، و`app-2026.10.1`. النتيجة: الرمز 2، والرسالة تذكر `YYYY.M.N`، و`calls.jsonl` فارغ. يُشغَّل بالوضع الكامل لا بـ`--check-tag` |
| AC-2.3 | وحدة | `::test_image_list_has_two_images_and_reserves_cuda_for_2_4`. `--list-images` يطبع الأسطر الثلاثة بالضبط: `app Dockerfile`، و`embedding services/embedding/Dockerfile`، و`embedding-cuda reserved:2.4` |
| AC-2.4، AC-2.5، AC-2.6 | يدويّ، يحتاج السجلّ | بعد أوّل إصدار، في المرحلة 13:<br>- `docker pull anastawahia/aizzak:app-<T>` و`docker pull anastawahia/aizzak:embedding-<T>`.<br>- `docker buildx imagetools inspect` يعطي البصمة نفسها المطبوعة.<br>- `DOCKER_CONFIG=$(mktemp -d) docker pull …` يفشل بـ`unauthorized` أو `denied`.<br>- السجلّ/الـartifact فيه الوسم والبصمة والـcommit |
| AC-3.1 | وحدة | `::test_dirty_tree_is_refused[modified_tracked, untracked_not_ignored]`: الرمز 3، والرسالة تذكر «dirty»، ولا نداء docker. **ومعه ضابطٌ موجب:** ملفٌّ غير متتبَّعٍ **داخل** `.gitignore` لا يمنع |
| AC-3.2 | وحدة | `::test_ci_not_green_is_refused`. مُعلَّمٌ بالقيم: `FAKE_GH_OUTPUT=completed/failure`، و`in_progress/`، و`none`، و`FAKE_GH_RC=1`. النتيجة: الرمز 5، ولا `buildx build` |
| AC-3.3 | وحدة | `::test_missing_tag_prints_usage` و`::test_bad_tag_prints_usage_before_any_other_check`. الرمز 2، ويطبع الاستعمال، ولا نداء `git fetch` ولا `gh` ولا `docker` |
| AC-3.4 | وحدة | `::test_build_calls_are_amd64_with_the_right_dockerfile_context_and_tag`. كلّ نداء `buildx build` فيه `--platform linux/amd64`، و`--file` الصحيح، والسياق `.`، و`--tag anastawahia/aizzak:app-2026.10.1` أو `:embedding-2026.10.1` حسب الملفّ، والـlabels الأربعة. ولا `dev` ولا `latest` في أيّ نداء. **وكلّ البناء قبل أوّل `push`** (D-5) |
| AC-3.5 | وحدة | `::test_success_prints_one_digest_line_per_image_and_the_commit`. سطران يطابقان `^anastawahia/aizzak:(app\|embedding)-2026\.10\.1@sha256:[0-9a-f]{64}$`، واحدٌ لكلّ بادئة، وسطر `release: commit <sha الـHEAD>`، والرمز 0. وملفّ `--digest-file` فيه سطران |
| AC-3.6 | وحدة | `::test_existing_tag_is_refused`. مُعلَّمٌ بالقيمتين `FAKE_DOCKER_EXISTING=anastawahia/aizzak:app-2026.10.1` و`…:embedding-2026.10.1`. أيّ وسمٍ ببادئته موجود: الرمز 7، ولا `build` ولا `push`. **وضابطٌ موجب:** وجود `anastawahia/aizzak:app-2026.10.0` لا يمنع `2026.10.1` |
| AC-3.7 | وحدة | `::test_second_push_failure_names_it_and_lists_what_was_pushed`. النتيجة: الرمز 9، ويُذكر `anastawahia/aizzak:embedding-2026.10.1`، ويظهر سطر `anastawahia/aizzak:app-2026.10.1@sha256:…`، وتظهر «ABANDONED» و`2026.10.2`، ولا `OK` |
| AC-3.8 | وحدة | `::test_script_text_hygiene`:<br>- `set -euo pipefail` أوّل سطرٍ غير تعليق.<br>- لا `.env`.<br>- لا `docker login`.<br>- لا `--password`.<br>- لا خيار `getopts`/`case` لكلمة سرٍّ أو مفتاح (`password\|token\|secret` غائبةٌ من فروع `case`) |
| AC-3.9 | يدويّ، يحتاج السجلّ والموافقة | تشغيلٌ حقيقيٌّ واحد: الوسمان `app-<T>` و`embedding-<T>` في `anastawahia/aizzak`، و`linux/amd64` لكلٍّ منهما. **المسار المعتاد CI** (AC-4.5)، والتشغيل المحلّيّ يُقبل باختبار الوحدة بسبب R-1 |
| حوافّ US-3 | وحدة | يُضاف إلى `test_release_build_push.py`:<br>- `::test_head_not_on_master_is_refused`: الرمز 4.<br>- `::test_workflow_flag_is_refused_outside_actions`: الرمز 2.<br>- `::test_runs_from_a_subdirectory_with_the_repo_root_as_context`.<br>- `::test_missing_buildx_is_refused_before_building`: الرمز 6.<br>- `::test_build_failure_pushes_nothing`: الرمز 8.<br>- `::test_public_repository_is_refused_before_building`: D-7، `200` على `anastawahia/aizzak`، الرمز 7. ونداء `curl` الأوّل يقع قبل أيّ `buildx build`.<br>- `::test_repository_turning_public_after_push_fails_loudly`: الرمز 9 |
| AC-4.1 | وحدة | `test_release_workflow.py::test_it_fires_only_on_pushed_v_tags`. `on` (أو `True`) = `{"push": {"tags": ["v*"]}}` بالضبط |
| AC-4.2 | وحدة | `::test_registry_credentials_come_only_from_two_secrets_via_env_and_stdin`. كلّ `${{ secrets.X }}` تحت `env:` فقط، و`X ∈ {DOCKERHUB_USERNAME, DOCKERHUB_TOKEN}`. لا `secrets.` داخل أيّ `run:`. سطر الدخول فيه `--password-stdin`، ولا `-p ` ولا `--password ` |
| AC-4.3 | وحدة | `::test_publishing_is_delegated_to_the_script`. خطوةٌ فيها `deploy/release/build-push.sh` و`"${GITHUB_REF_NAME#v}"`. ولا `docker build` ولا `docker push` ولا `buildx build` في أيّ `run:` |
| AC-4.4 | وحدة | `::test_publish_needs_quality_and_every_job_is_time_boxed`. `publish.needs ⊇ {"quality", "preflight"}`، و`timeout-minutes ≤ 60` لكلّ وظيفة، و`environment == "release"` |
| حارس الانحراف | وحدة | `::test_release_quality_is_ci_quality`. قائمة `(uses, run)` لخطوات `jobs.quality` في الملفّين متطابقة. وأيضاً `::test_every_run_step_is_valid_bash` و`::test_permissions_are_read_only_and_releases_never_cancel` |
| AC-4.5 | يدويّ، يحتاج الأسرار والموافقة | المرحلة 13: أوّل وسم `v2026.10.1` على commit من `master`. النتيجة: `anastawahia/aizzak:app-2026.10.1` و`…:embedding-2026.10.1` في السجلّ، وسطراهما في السجلّ والـartifact |
| AC-4.6، AC-4.7 | يدويّ | سرٌّ ناقص: يفشل عند «Registry secrets are set». وسم `v2026.10.1-test`: يفشل عند `preflight` في ثوانٍ. **ثمّ يُحذف الوسم التجريبيّ من GitHub** |
| AC-5.1 | وحدة | `test_compose_build_overlay.py::test_env_example_offers_the_overlay_line_commented_only`. سطرٌ معلَّقٌ يطابق `^#COMPOSE_FILE=docker-compose.yml:docker-compose.build.yml$`، ولا `^COMPOSE_FILE=` مفعّل، ولا `^(APP\|EMBEDDING)_IMAGE_(REPO\|TAG)=` مفعّل |
| AC-5.2…5.4 | يدويّ، فحص نصّ | المراجعة في المرحلة 9. `git grep -nE "DOCKERHUB_TOKEN=\|dckr_pat_"` لا يجد شيئاً |

**إجراء AC-1.5 المصحَّح.** ينفّذه المالك، أو وكيلٌ بموافقته، والمخرج بصماتٌ فقط:

```bash
cd /home/AIZZAK
svc() { docker compose "$@" config --format json | python3 -c 'import json,sys
print(json.dumps(json.load(sys.stdin)["services"],sort_keys=True))' | sha256sum; }
git switch master          ; svc ; svc --profile backup     # BEFORE: .env untouched
git switch feat/prebuilt-images                              # then add the COMPOSE_FILE line (§3.2)
svc ; svc --profile backup                                   # AFTER: both hashes must equal BEFORE
```

### 5.1 تعديلات معايير القبول

هذه صياغاتٌ جديدة لمعايير من `stories.md`، تفرضها قرارات المالك أو قياسٌ في هذا التصميم. **لا يُعدَّل `stories.md`.** هذا الجدول هو المرجع للمراحل 8 و9 و11.

| المعيار | الصياغة في `stories.md` | الصياغة المعتمدة | السبب |
|---|---|---|---|
| AC-1.2 | الخدمات التي تبدأ صورتها بـ`${AIZZAK_REGISTRY:-aizzak}/` | الخدمات التي تبدأ صورتها بـ`${APP_IMAGE_REPO:-aizzak/app}:` أو `${EMBEDDING_IMAGE_REPO:-aizzak/embedding}:`، وعددها 10 | D-1، ومستودعٌ واحد بوسومٍ ببادئة |
| AC-1.3 | لا مفتاح غير `build` | **بلا تغيير**: `build` وحده | قرار المالك (البديل (أ)) أبقى المعيار كما هو |
| AC-1.4 | النصّ الخام يحتوي `AIZZAK_REGISTRY` و`APP_IMAGE_TAG` و`EMBEDDING_IMAGE_TAG` | يحتوي `APP_IMAGE_REPO` و`APP_IMAGE_TAG` و`EMBEDDING_IMAGE_REPO` و`EMBEDDING_IMAGE_TAG`. والحلّ الافتراضيّ بلا تغيير: `aizzak/app:dev` و`aizzak/embedding:dev` | D-1 |
| AC-1.5 | `diff` بين `config` قبل وبعد فارغ | `services` في `config --format json` متطابقة (بصمة sha256)، و`diff` الكامل = الأسطر الثلاثة تحت `x-app-image` فقط | قياس §3.2: `config` يطبع `x-*` حرفيّاً، وFR-1 يحذف منها `build:` |
| AC-2.2 | قائمة الوسوم المرفوضة | تُضاف إليها `app-2026.10.1`: الرقم يُعطى بلا بادئة | البادئة يضعها السكربت |
| AC-2.3 | «صورتا التطبيق والتضمين… وخانة `embedding-cuda`» | القائمة بادئات وسومٍ في مستودعٍ واحد: `app` و`embedding`، و`embedding-cuda` محجوزة (2.4) | مستودعٌ واحد |
| AC-2.4 | `docker pull <registry>/<app>:<T>` و`<registry>/<embedding>:<T>` | `docker pull anastawahia/aizzak:app-<T>` و`docker pull anastawahia/aizzak:embedding-<T>`، والبصمة كما في السجلّ | مستودعٌ واحد |
| AC-2.5 | `docker pull` للصورتين يفشل بلا اعتماد | المرجعان نفسهما في AC-2.4، من مستودعٍ واحد خاصّ | مستودعٌ واحد |
| AC-2.6 | لكلّ صورة: الوسم والبصمة والـcommit | الوسم هو الوسم ببادئته (`app-<T>`)، ورقم الإصدار `<T>` يُطبع مرّةً واحدة | مستودعٌ واحد |
| AC-3.4 | كلّ نداء بناءٍ فيه الوسم `2026.10.1` | فيه `--tag anastawahia/aizzak:<prefix>-2026.10.1`، بالبادئة التي تطابق ملفّ البناء | مستودعٌ واحد |
| AC-3.5 | سطرٌ يطابق `^<repo>@sha256:[0-9a-f]{64}$` | سطرٌ لكلّ صورة يطابق `^anastawahia/aizzak:(app\|embedding)-<T>@sha256:[0-9a-f]{64}$` | `<repo>@sha256` لا يميّز صورتين في مستودعٍ واحد |
| AC-3.6 | `<repo>:<T>` موجود يُرفض | **أيّ** وسمٍ ببادئته (`app-<T>` أو `embedding-<T>`) موجودٌ في `anastawahia/aizzak` يرفض الإصدار كلّه | مستودعٌ واحد |
| AC-3.7 | يذكر اسم الصورة التي فشلت | يذكر مرجعها الكامل `anastawahia/aizzak:<prefix>-<T>`، والمرفوعة قبلها بصيغة AC-3.5 | مستودعٌ واحد |
| AC-3.9 | «الصورتان في السجلّ بالوسم `<T>`» | الوسمان `app-<T>` و`embedding-<T>` في `anastawahia/aizzak`، وكلٌّ منهما `linux/amd64` | مستودعٌ واحد |
| AC-4.5 | «الصورتان في السجلّ بالوسم `<T>`» | الوسمان `app-<T>` و`embedding-<T>` في `anastawahia/aizzak`، وسطر كلٍّ منهما بصيغة AC-3.5 في السجلّ والـartifact | مستودعٌ واحد |

**ملاحظةٌ على `requirements.md`:** FR-3 (`AIZZAK_REGISTRY`) وFR-6 (صورتان «في سجلٍّ واحد»، بأسماء مستقلّة) تُقرآن بـD-1 وبالمستودع الواحد. FR-7 بلا تغيير: «وسمٌ واحد» صار رقم إصدارٍ واحداً تُشتقّ منه وسوم الصور.

## 6. ترتيب التنفيذ والتوازي

المنفّذ واحد (`devops-engineer`) على `feat/prebuilt-images`. كلّ التزامٍ أخضر بالفحوص الخمسة، والتزامٌ واحد لكلّ وحدةٍ منطقيّة:

1. **الالتزام 1: `build(compose): name images only; move builds to docker-compose.build.yml`**
   - `docker-compose.yml`، و`docker-compose.build.yml`، و`ci.yml` (`COMPOSE_FILE`).
   - `tests/unit/test_ci_workflow_truthfulness.py:348`، و`tests/unit/test_compose_build_overlay.py`.
   - كتلة `.env.example`.
   - **كلّها في التزامٍ واحد** (القاعدة 3، وR-2).
2. **الالتزام 2: `feat(release): add deploy/release/build-push.sh`** مع `tests/unit/test_release_build_push.py`.
3. **الالتزام 3: `ci(release): build and push images on v* tags`** مع `.github/workflows/release.yml` و`tests/unit/test_release_workflow.py`. يعتمد على 2، لأنّ اسم السكربت وخياراته عقد.
4. **الالتزام 4: `docs: local COMPOSE_FILE line and the release commands`** في `quickstart.md` و`stack-commands.md` و`README.md`.
   - **يمكن كتابته بالتوازي** مع 2 و3، لأنّ العقود ثابتةٌ في §3. ويُلتزم بعدهما.
5. **دفع الفرع:**
   - AC-1.8: CI أخضر على الفرع.
   - `release.yml` لا يعمل على الفروع، فلا أثر له قبل أوّل وسم.
6. **بعد الدمج، على المالك** (خارج الوكلاء):
   - (أ) سطر `COMPOSE_FILE` (§3.2)، ثمّ AC-1.5 وAC-1.9.
   - (ب) إنشاء المستودع `anastawahia/aizzak` على Docker Hub **خاصّاً**، قبل أيّ رفع. وإلّا أنشأه أوّل رفعٍ بخصوصيّة الحساب الافتراضيّة، وهي عامّة. وD-7 يرفض الرفع إن وجده عامّاً.
   - (ج) Personal Access Token بصلاحيّة **Read & Write** فقط، بلا Delete. لا يمكن تقييده بالمستودع وحده (D-8).
   - (د) GitHub → Settings → Environments → `release`، بقاعدة نشرٍ لوسوم `v*`، وفيها السرّان `DOCKERHUB_USERNAME` و`DOCKERHUB_TOKEN`.
   - (هـ) أوّل وسم `v2026.10.1` في المرحلة 13، ثمّ AC-4.5 وAC-2.4…2.6، ثمّ AC-4.6 وAC-4.7.
7. **المنسّق في النهاية** (ليس من هذا التصميم):
   - لوحة §4 في `server-gpu-plan.md`.
   - **`docs/server-gpu-summary.html` يجب أن يتغيّر:**
     - وسوم حالة 1.1–1.4.
     - تُزال عبارتا «تنتظر قرار ١» و«تنتظر قرار ٦» (`:331,353`)، فالقراران اتُّخذا.
     - «ثلاث صور» و«الصور الثلاث» (`:333,344-345,356`) تصير: «صورتان الآن، البرنامج والتضمين على المعالج، في مستودعٍ خاصٍّ واحد، وكلٌّ منهما بوسمٍ يبدأ باسمها ثمّ رقم الإصدار. والثالثة، التضمين على الكرت، مكانٌ محجوز يُبنى في الخطوة 2.4».
     - «القبول» في 1.2 (`:334`): «الصورتان تُسحبان برقم الإصدار من جهازٍ مسجَّلٍ في السجلّ، ولا تُسحبان من غيره».
   - **ويُقترح تصحيح نصّ الخطّة §5 و§6.** قرارا المالك يعدّلانه، وقاعدة الخطّة: «لا تتغيّر إلّا بقرار». الصياغة المقترحة:
     - **1.1 البند 2:** «الملفّ الأساسيّ يحمل `image:` فقط: `${APP_IMAGE_REPO:-aizzak/app}:${APP_IMAGE_TAG:-dev}`، والمثل للتضمين بـ`EMBEDDING_IMAGE_REPO` و`EMBEDDING_IMAGE_TAG`. القيم الافتراضيّة تبقي اسمَي اليوم».
     - **1.2 «المطلوب»:** «مستودعٌ خاصٌّ واحد `anastawahia/aizzak` على Docker Hub (**قرار 1**، معدَّلٌ في 2026‑10‑13)، والصور تُميَّز ببادئة الوسم: `app-<الإصدار>` (التطبيق والعمّال و`migrate` و`ops-scheduler`)، و`embedding-<الإصدار>` (التضمين على المعالج)، و`embedding-cuda-<الإصدار>` (التضمين على الكرت، [2.4])».
     - **1.2 «الوسم»:** «رقم إصدارٍ واحد للصور كلّها، مثل `2026.10.1`، يُشتقّ منه وسم كلّ صورة ببادئتها. ومعه البصمة. لا `dev` ولا `latest` على خادم».
     - **1.3 و1.4 «القبول»:** «الصورتان (والثالثة بعد 2.4) في المستودع برقم الإصدار نفسه…».
     - **2.4 البند 2:** «… وتُبنى منه صورة `anastawahia/aizzak:embedding-cuda-<الإصدار>`».
     - **§6 قرار 1:** يُسجَّل الجواب: «مستودعٌ خاصٌّ واحد على Docker Hub، `anastawahia/aizzak`، بوسومٍ ببادئة».

**مراحل تُتخطّى:** UX (لا واجهة)، والبيانات (لا ترحيل)، والواجهة الأماميّة.

## 7. المخاطر والقرارات

**لا بنود ⚠️ مفتوحة.** بندا النسخة الأولى حسمهما المالك، ولم يظهر في دورة الإعادة مانعٌ جديد.

### قرارات المالك (2026‑10‑13، دورة الإعادة 1)
- **بلا `pull_policy` في `docker-compose.build.yml`** (كان ⚠️ 1، والقرار البديل (أ)):
  - الملفّ الإضافيّ يحمل `build:` وحده، وAC-1.3 كما في `stories.md`.
  - سلوك السحب كما اليوم: الصورة الغائبة يحاول Compose سحبها من Docker Hub أوّلاً، ثمّ يبنيها.
- **مستودعٌ خاصٌّ واحد `anastawahia/aizzak`، والصور تُميَّز ببادئة الوسم** (كان ⚠️ 2):
  - البادئات: `app-<v>` و`embedding-<v>`، و`embedding-cuda-<v>` محجوزة لـ2.4.
  - يحلّ محلّ أسماء قرار 1 في البوّابة ①.
  - يتّسع له حساب Docker المجّانيّ، إن صحّ أنّه يسمح بمستودعٍ خاصٍّ واحد، ولا كلفة.
  - أثره في التصميم: D-1 (قيم الخادم)، وD-7 (مستودعٌ واحد)، وواجهة السكربت §3.3، وصيغة البصمة، و§5.1.

### مخاطر مقبولة ومسجّلة (لا تعالجها الميزة)
- **R-3: المساحة `aizzak` على Docker Hub غير محجوزة (404). خطرٌ قائمٌ قبل الميزة، وقبله المالك.**
  - **مقيس:** مع `image:` و`build:` اليوم، تُسحب الصورة الغائبة من `aizzak/…` قبل البناء. من يحجز المساحة ويرفع `app:dev` تُشغَّل صورته على نسخةٍ جديدة من المستودع، أو على جهاز المالك إن حُذفت صورته المحلّيّة.
  - **التخفيف الوحيد في هذه الميزة:** الوثائق تطلب `docker compose build` قبل أوّل `up -d` (§3.6).
  - **الحلّ الجذريّ متاحٌ لاحقاً بقرار:** حجز المساحة `aizzak` على Docker Hub، أو `pull_policy: never` في الملفّ الإضافيّ (سطرٌ لكلّ خدمة).
- **نطاق مفتاح Docker Hub:** المفتاح الشخصيّ لا يُقيَّد، **بحسب علمي**، بمستودعٍ واحد، فيكتب أيضاً في `anastawahia/aizzak-runpod`. التخفيف: صلاحيّة Read & Write بلا Delete، وسرّ بيئةٍ مقيّدٌ بوسوم `v*` (D-8). التقييد الحقيقيّ يحتاج منظّمة Docker، ولم يُطلب.
- **حصّة المستودع الخاصّ الواحد وثغرة `ث‑7`:**
  - إن كانت الخطّة المجّانيّة تسمح بمستودعٍ خاصٍّ واحد، فقد أخذه `anastawahia/aizzak`.
  - إذن علاج `ث‑7` بجعل `aizzak-runpod` خاصّاً سيحتاج خطّةً مدفوعة، أو نقل صورة RunPod إلى المستودع نفسه ببادئة `runpod-<وسم>`.
  - هذا خارج هذه الميزة، ويُذكر هنا كي لا يفاجئ خطوة RunPod 3.2.
- **خطأ البادئة على الخادم** (D-1): وسمٌ بلا بادئة يفشل صاخباً، ووسمٌ ببادئةٍ خاطئة يشغّل الصورة الخطأ. الحارس في 5.2 (`install.sh` يكتب الوسمين من رقمٍ واحد) و7.1 (فحص البادئة).

### قراراتٌ وتصحيحات أخرى
- **AC-1.5 بصيغته الأصليّة («`diff` فارغ») مستحيل:** `config` يطبع `x-app-image` حرفيّاً، وFR-1 يحذف منها `build:`. صُحّح المعيار في §3.2 و§5.1 (مقارنة `services`). تصحيحٌ بالقياس، لا قرار.
- **أسماء المتغيّرات D-1 تخالف نصّ 1.1** (`AIZZAK_REGISTRY`). تفرضها أسماء اليوم المحلّيّة والمستودع الواحد معاً، وتُصحَّح الخطّة في النهاية (§6 البند 7).
- **إخفاء اسم المستخدم:** السرّ `DOCKERHUB_USERNAME`، إن كانت قيمته `anastawahia`، يُخفي المساحة في سجلّ CI، فيظهر `***/aizzak:app-<v>@sha256:…`. البادئة والبصمة تبقيان ظاهرتين، والـartifact `release-digests` غير مُخفى. إن أراد المالك سجلّاً مقروءاً كاملاً، فليجعل اسم المستخدم **متغيّراً** (`vars.`) لا سرّاً. اختيارٌ تجميليّ يغيّر سطرين.
- **الأسرار في Environment `release` مقيّدة بـ`v*`** (D-8)، لا أسرار مستودع. أضيق وصولٍ في مستودعٍ عامّ، ويعمل المسار بالشكلين.
- **لا اعتماد جديد:** لا actions من طرفٍ ثالث (D-9). `actions/upload-artifact@v4` رسميّة.
- **R-1 (سرعة الرفع):** CI هو الطريق المعتاد، والسكربت للطوارئ.
- **R-4 (أوزان النموذج غير مثبّتة المراجعة):** البصمة هي المرجع لا الوسم. التثبيت خارج النطاق، لأنّه تعديلٌ في Dockerfile.
- **Q-9 (قرص العدّاء):** يُقاس في أوّل تشغيل (`df -h /` قبل وبعد)، ويُفرَّغ تلقائيّاً تحت 25 ج.ب.
- **مأزق الفروع القديمة مع سطر `COMPOSE_FILE`:** موثَّق (§3.2). لا علاج آليّ له.
- **`docker-compose.test.yml:5` و`docker-compose.wsl-gpu.yml:4` و`deploy/load/run.sh:125`:** نصوصٌ فيها `-f` أو `--build`، وخارج الموافقة. تعمل كما هي حين توجد الصور أو يوجد السطر. تُترك.
- **`deploy/resource-budget.sh` مع `COMPOSE_FILE` مُصدَّرٍ في الصدفة:** عطلٌ قائم، لا تُصلحه الميزة.
- **اقتراحٌ للمالك، اختياريّ وخارج صلاحيّة الوكلاء:** سطرٌ في `CLAUDE.md` يقول إنّ `docker compose build` على هذا الجهاز يعتمد على سطر `COMPOSE_FILE` في `.env`.

</div>
