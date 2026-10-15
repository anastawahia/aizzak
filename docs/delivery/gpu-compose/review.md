<div dir="rtl">

# مراجعة الشيفرة: الكرت داخل Compose — Ollama والتضمين (المرحلة 2 من خطّة خادم الكرت)

- **النطاق المراجَع:** `git diff master...feat/gpu-compose` في `/home/AIZZAK` (HEAD ‏`8b325fc`؛ الشيفرة حتّى `8d13cb0`)، 36 ملفّاً
- **الحكم:** **PASS** (0 حاجبة · 1 مهمة · 4 تحسين)

## الملاحظات
| # | الشدة | الملف:السطر | الملاحظة | الإصلاح المطلوب |
|---|---|---|---|---|
| 1 | مهمة | `docs/stack-commands.md:522`، `docs/stack-commands.md:580`، `docker-compose.gpu.yml:25-26` | **جملةٌ شرطيّة صار شرطها واقعاً.** النصّ يقول «جهاز المالك يبقى على ملفّ WSL **ما لم تُثبَّت الأداة عليه**»، ورأس ملفّ الكرت يقول «Not for this WSL development machine **before the toolkit is installed**». لكنّ الأداة مثبّتة منذ 2026‑10‑14 (`status.md`، والسطر 591 من الملفّ نفسه يقولها). فمن يقرأ النصّ حرفيّاً يستنتج أنّ جهاز المالك ينتقل الآن إلى `docker-compose.gpu.yml`. ولم يقرّر أحدٌ ذلك، وصورة `embedding-cuda` لا تُنشر قبل ش‑4 | تُكتب الحقيقة بدل الشرط في المواضع الثلاثة. مثلاً: «الأداة مثبّتة على جهاز المالك منذ 2026‑10‑14، ويبقى على ملفّ WSL (الأمر 37) حتّى يقرّر المالك الانتقال، بعد ش‑3». وفي رأس ملفّ الكرت جملةٌ إنجليزيّة بالمعنى نفسه |
| 2 | تحسين | `deploy/ollama/pull_models.py:136-144`، `:279-280` | `is_present` يعامل **أيّ** ردٍّ غير 200 أو 404 من `/api/show` معاملة `unreachable=True`، و`main` يخرج فوراً بالرمز 3. لكنّ Ollama أجاب في هذه الحال، فهو ليس «غير قابل للوصول». **مثال:** جدولٌ فيه نموذجان، وأحدهما يُرتَّب أوّلاً ويردّ عليه `/api/show` بخطأ يخصّه (400 لاسمٍ غير صالح، أو 500 لملفٍّ تالف). النتيجة الرمز 3 «unreachable»، والنموذج السليم لا يُسحب. هذا يناقض انحراف devops المسجَّل نفسه: «فشل نموذجٍ لا يوقف البقيّة» | يُبقى `unreachable=True` لأخطاء النقل وحدها (`_TRANSPORT_ERRORS`). وردٌّ HTTP غير 200 أو 404 يصير فشلاً للنموذج: الرمز 4، وتكمل الحلقة. ويُعدَّل `test_an_ollama_that_errors_on_show_is_exit_3` (`tests/unit/test_ollama_pull_models.py:467`) ليطابق |
| 3 | تحسين | `docs/stack-commands.md:615` | رموز خروج `ollama-pull` تقول «**2** `PROVIDER_ROUTING` غير صالح» وحده. لكنّ الرمز 2 بعد `8d13cb0` يشمل أيضاً `OLLAMA_BASE_URL` و`OLLAMA_PULL_CONNECT_WAIT_S` (docstring ‏`pull_models.py:10-11`) | «**2** الإعداد غير صالح (`PROVIDER_ROUTING` أو `OLLAMA_BASE_URL` أو `OLLAMA_PULL_CONNECT_WAIT_S`)» |
| 4 | تحسين | `deploy/release/build-push.sh:25-26` | عمود «reference for 2026.10.1» يذكر `anastawahia/aizzak:embedding-cuda-2026.10.1`، وهذا الوسم غير موجود: الإصدار المنشور 2026.10.1 فيه صورتان فقط. `stack-commands.md` يوضّح ذلك، أمّا رأس السكربت فلا | يُكتب `2026.10.2` مثالاً للصفّ الثالث، أو يُضاف «(from 2026.10.2)» |
| 5 | تحسين | `tests/unit/test_vector_parity.py` (800 سطر)، `tests/eval/README.md` | خارج قائمة الملفّات في `design.md` §2 (فيها `tests/eval/vector_parity.py` وحده). لا ضرر فيه: اختبارات بخوادم مزيَّفة، وتمرّ في CI. لكنّه أكبر من الأداة اليدويّة التي يختبرها | لا تعديل في الشيفرة. يُسجَّل في `status.md` إضافةً من QA خارج §2 |

## مطابقة التصميم
- **`docker-compose.yml`:** يطابق §3.2 و§3.3:
  - `ollama` و`ollama-pull` خلف `local-llm`، بـ`expose` بلا `ports`.
  - فحص الصحّة بـ`bash` و`/dev/tcp` إلى `/api/version`.
  - الحدود: 4 vCPU و4g للأولى، و0.25 و64m للثانية.
  - حجم `ollama-models`.
  - المرساة `&provider-routing` تعطي `ollama-pull` القيمة نفسها التي يحلّها `app`.
  - `OLLAMA_BASE_URL` في `ollama-pull` ثابتٌ على الخدمة الداخليّة.
  - القيمة الافتراضيّة `http://ollama:11434` تطابق `env_settings.py:163`.
  - الحواف التسع بـ`required: false`: أربعٌ إلى الجسر، وأربعٌ إلى `ollama` (`app` والعمّال الثلاثة)، وحافّة `ollama-pull` إلى `ollama`.
  - الجسر خلف `wsl-ollama`، وبقيّة خدمته كما هي.
  - لا شيء يراقب الجسر في `deploy/prometheus` غير نصّ `response`.
- **`docker-compose.gpu.yml`:** يطابق §3.8 حرفيّاً: ثلاث خدمات، وحجزٌ بلا `count` (D‑8)، و`replicas: 1`، و`3g`، و`EMB_REQUIRE_CUDA: "1"`، و`app` بـ`utility` وحدها.
- **`services/embedding/app.py`:** يطابق §3.6 وD‑14:
  - الجهاز يقرّره `_choose_device` وحده.
  - `_require_cuda` يُقرأ قبل أيّ استيراد لـtorch، ويرفض أيّ قيمةٍ غير المقبولة.
  - الرفض قبل تحميل النموذج، ورسالته تميّز عجلة المعالج عن غياب الكرت.
  - `device` و`dtype` يُصفَّران في `finally`.
  - لا تغيير في `openapi.yaml` (الخدمة ليست فيه).
- **`Dockerfile` و`requirements-cuda.txt` و`build-push.sh`:**
  - `ARG` بعد طبقة `apt`، والقيمة الافتراضيّة صورة المعالج.
  - الإصدارات نفسها، والفرق سطر الفهرس.
  - `read -r prefix dockerfile build_arg`، و`${arg_flags[@]+…}` آمنٌ مع `set -u`.
  - حلقتا الرفع و`require_tag_absent` تقرآن البادئة بـ`${entry%% *}`، وهذا صحيحٌ مع المدخل ذي الحقول الثلاثة.
  - `release.yml` لم يتغيّر (D‑12).
- **صورة Ollama:**
  - الوسم `0.34.4` متّسقٌ في كلّ موضعٍ يثبّته: `docker-compose.yml:1684`، و`08-local-runbook.md:31`، والاختبارات الثلاثة.
  - البصمة في التعليق `sha256:8262851b…` تساوي معرّف الصورة المحلّيّة.
  - وسمها `org.opencontainers.image.version=24.04`، فتعليق «ubuntu:24.04» ما زال صحيحاً.
  - `0.13.5` باقٍ في `design.md` وحده، وهو سجلٌّ تاريخيّ يعدّله قرار المالك في `status.md`.
- **انحرافات devops:** كلّها سليمة.
  - **`gpu-preflight.sh`:**
    - مهلة `--smoke` ‏300 ث ضروريّة، لأنّ أوّل تشغيلٍ يسحب 92 م.ب، ومهلة 5 ث تجعله مستحيلاً.
    - WARN ثمّ حكم الفحص الحيّ يطبّق D‑10: النجاح 0، والفشل مع غياب وقت التشغيل 12، والفشل مع وجوده 14.
    - المخرج على stdout، ما عدا خطأ الوسيط.
  - **`pull_models.py`:**
    - فشل نموذجٍ يكمل البقيّة ثمّ يخرج بـ4، وعدم الوصول يخرج بـ3 فوراً، والإعداد الخاطئ يخرج بـ2.
    - الاستثناء الوحيد هو الملاحظة 2: ردّ HTTP من `/api/show` يُعامَل معاملة عدم الوصول.
  - **§2‑ي:** ‏14746 = 0.9 × 16384، و22118 = 0.9 × 24576، صحيحان. والنسب محسوبةٌ من `memory.total` (2.9 × 1024 / 8188 ≈ 36%)، وتُقارَن بشرط 0.90 في «طريقة القياس»، فهي متّسقة.
  - **الروابط:** `deploy-linux-server.md` → `deploy-server.md` صحيح، لأنّ الأوّل غير موجود. وما بقي منه في `deploy/nginx/nginx.conf:283,298` و`08-local-runbook.md:830` قديمٌ وخارج النطاق.
- **القواعد المُلزِمة:**
  - لا تغيير في `src` ولا `.importlinter` ولا `.github` ولا `deploy/runpod` ولا `pyproject.toml` ولا `migrations` ولا `CLAUDE.md`.
  - الملفّات المحميّة المعدَّلة كلّها في قائمة Q‑8 والخيار (أ).
  - `alerts.yml` تغيّر فيه نصّ `response` وحده. و`docker-compose.test.yml` و`docker-compose.wsl-gpu.yml` تغيّر فيهما تعليق الرأس وحده.
  - `docs/deploy-server.md` داخل `<div dir="rtl">`.

## الاختبارات
- **تختبر السلوك لا النصّ:**
  - `pull_models` بخادم HTTP مزيَّف، وبتشغيل السكربت نفسه.
  - `gpu-preflight.sh` بأوامر بديلة على `PATH`، ويثبت أنّ `sudo` و`apt` لا تُستدعى.
  - `app.py` ببدائل `_choose_device` و`torch`، ويغطّي الرفض والقيم المقبولة والمرفوضة وتصفير `finally`.
  - Compose بـ`docker compose config` بلا حاوية، ومع `--env-file .env.example`، وبعد حذف `COMPOSE_FILE` و`COMPOSE_PROFILES` من البيئة.
  - النصّيّ وحده هو `test_gpu_compose_hygiene.py`، وهذا مقصود (AC‑7.5 وAC‑7.6).
- **AC‑7.8:**
  - كلّ اختبارٍ يقرأ ملفّاً تغيّر في commit ذلك الملفّ: C3 وC4 وC5 وC6 و`8dcb24f` و`8d13cb0`، ومنها `test_release_build_push_edges.py` في C5.
  - ولم أُعِد البوّابات عند كلّ commit، وهو تحفّظ QA نفسه.
- **`tests/eval/vector_parity.py`:** لا يجمعه pytest. اسمه لا يطابق `test_*.py`، و`pyproject.toml` لا يغيّر `python_files`. ويُختبر من `tests/unit/test_vector_parity.py` بـ`from tests.eval import vector_parity`.
- **التغطية:** معايير US‑2…US‑5 الآليّة لكلٍّ منها اختبارٌ باسمه (AC‑2.1…2.6، 3.1/3.2/3.4/3.5، 4.1/4.2/4.5، 5.1…5.8، 6.2/6.3، 7.3). والمؤجَّل (ش‑1…ش‑5) مكتوبٌ بشروطه في `test-plan.md` §1.2.
- **البوّابات:** لم أُعِدها، فقد أعادها المنسّق على `8d13cb0` وكانت خضراء.

</div>
