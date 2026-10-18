<div dir="rtl">

# المراجعة الأمنية: المراقبة اختياريّة وأرقام الكرت (`monitoring-profile`، المرحلة 4 من خطّة خادم الكرت)

- **النطاق المراجَع:** `git diff master...feat/monitoring-profile` (`47225ff…3880ff9`، 9 التزامات، 35 ملفّاً).
  - قُرئت كاملةً: `docker-compose.gpu.yml`، و`deploy/prometheus/{prometheus.yml,scrape-gpu.yml,alerts.yml,test-rules.sh}`، و`deploy/resource-budget.sh`، و`tests/unit/test_compose_gpu_overlay.py`.
  - وقُرئ الفرق مع سياقه: `docker-compose.yml` (أسطر `profiles` فقط، مع تعريفات الخدمات الإحدى عشرة)، و`.env.example` (تعليقات فقط)، وصفّ GPU في `deploy/grafana/dashboards/aizzak-capacity.json`، والوثائق المعدّلة.
- **المرجع:** `requirements.md` (NFR‑10)، و`design.md` §3‑ج و§3‑د، و`test-plan.md`، و`status.md` (Q‑4، Q‑8، قرار 7)، و`CLAUDE.md` (قاعدة الـstaging)، و`docs/server-gpu-plan.md` §3.
- **الحكم:** **الجولة 3 (`fb4ccfd..16498e7`): PASS.** عولج SR‑15، وقاعدة `keep` مُثبتةٌ حيّاً على Prometheus v3.13.2 بالمهمّة الحقيقيّة. وعولج SR‑17 في أسطره الثلاثة، وSR‑16 في مضمونه. لا حرج ولا عالٍ ولا متوسّط ولا منخفض. ملاحظاتٌ جديدة: 3 معلوماتيّة (SR‑18…SR‑20، في قسم «الجولة 3» أسفل الوثيقة)، تُسجَّل متابعةً في وصف الـPR ولا تحجب.
  - الجولة 2 (`7bda359..08e4540`): PASS. عولجت SR‑9…SR‑14 كلّها، إلّا SR‑13 (3) الذي تركه المالك. 1 منخفضة و2 معلوماتيّة (SR‑15…SR‑17).
  - الجولة 1 (`278defa..f5591c2`): PASS، وتحقّق فيها شرط الجولة 0 (ث‑7…ث‑9، وعلاج ث‑6). 3 منخفضة و3 معلوماتيّة (SR‑9…SR‑14)، وطلب المالك إصلاحها كلّها قبل الدمج.
  - الجولة 0 (`3880ff9`): PASS بشرط تسجيل ث‑7 وث‑8 وث‑9 المقترحة وتعديل علاج ث‑6، قبل الدمج أو معه.

### لماذا PASS
- لا ملاحظة حرجة ولا عالية.
- لا مسار تطبيقيّ جديد، فلا فحص صلاحيّات ناقص. ولا استعلام ولا مخطّط جديد، فلا فحص عزلٍ ناقص في الشيفرة.
- الملاحظتان المتوسّطتان (SR‑1 وSR‑2) ثغرتان **قائمتان قبل الميزة**: شبكة `default` المسطّحة، وفحص أداة NVIDIA بلا إصدار. والميزة توسّعهما بصورة طرفٍ ثالث جديدة. وقاعدة المرحلة (`CLAUDE.md`، قرار المالك 2026‑10‑06): تُسجَّل في قائمة الأمان ولا تحجب.
- ⚠️ **للمالك:** إن عُدّ SR‑1 «عزل مستأجرين ناقصاً» صار الحكم FAIL. وإصلاحه رخيص: شبكةٌ داخليّة واحدة في `docker-compose.gpu.yml`.

## قائمة الفحص
- [x] **المصادقة والصلاحيات:** لا مسار تطبيقيّ جديد.
  - منفذ المصدّر `9835` بلا مصادقة، مثل المصدّرات الخمسة القائمة، و`expose` فقط بلا `ports`. تحقّقتُ من النموذج المدمج: `ports=[]`.
  - nginx لم يتغيّر، ولا `location` جديد. ومصادقة Grafana لم تتغيّر (`GF_USERS_ALLOW_SIGN_UP=false`، بلا anonymous).
- [~] **عزل المستأجرين:** لا استعلام ولا RLS جديد. لكنّ المصدّر ينضمّ إلى شبكة `default` الوحيدة، فيصل بلا مصادقة إلى Redis وQdrant، وفيهما بيانات كلّ المستأجرين ← SR‑1.
- [x] **المدخلات والمخرجات:**
  - `resource-budget.sh` يمرّر الخيارات عبر argv إلى heredoc مقتبس (`<<'PY'`). و`--without-monitoring` علمٌ بلا قيمة، والقراءة بـ`yaml.safe_load`، ولا ملفّات مؤقّتة.
  - تعابير اللوحة ثابتة، وأسطورتها `{{index}}: {{name}}` فقط. لا SQL ولا HTML.
- [x] **الأسرار:**
  - لا سرّ في الأسطر المضافة: بحثتُ بأنماط المفاتيح والرموز، وبالسلاسل الطويلة عالية العشوائيّة.
  - إثبات Grafana في `design.md:886` يولّد كلمته بـ`openssl rand` ولا يطبعها.
  - `METRICS_EXPORTER_PASSWORD` ما زال `:?` إلزاميّاً (`docker-compose.yml:3013`).
- [x] **رسائل الخطأ:** لا مسار جديد، ورسائل `resource-budget.sh` لا تكشف شيئاً.
- [~] **التبعيات الجديدة:** صورة طرفٍ ثالث واحدة، مثبّتة بالبصمة وموقَّعة من المنبع (تحقّقٌ من البيانات الوصفيّة فقط) ← SR‑2 وSR‑3.
- [x] **الروابط الخارجية وSSRF:** هدف الكشط ثابت (`nvidia-gpu-exporter:9835`). والـglob في `scrape_config_files` ← SR‑7 (معلوماتيّ).
- [~] **حدود المعدّل:** كلّ طلب `/metrics` يشغّل `nvidia-smi`، حتّى 40 طلباً بالتوازي ← SR‑4.
- [x] **NFR‑10:** المصدّر بلا `ports` ولا `docker.sock` ولا ربط جذر المضيف، وصورته مسجّلة في القائمة (ث‑6). ولا صلاحيّة فوق `utility`.

## ما تحقّقتُ منه (قراءةً فقط)
| البند | النتيجة |
|---|---|
| مفاتيح الخدمة | `user: "65534:65534"`، و`read_only: true` (أثبته QA حيّاً: `ReadonlyRootfs=true` والصحّة healthy)، و`cap_drop: ["ALL"]`، و`no-new-privileges:true`. ولا `volumes` ولا `devices` ولا `privileged` ولا `pid` ولا `network_mode`. اختبار مساواةٍ تامّة يحرسها (`tests/unit/test_compose_gpu_overlay.py:169-205`) |
| حجز الكرت | `capabilities: [utility]` وحدها، و`count: -1` (كلّ الكروت، وهو المقصود). في الوضع التقليديّ للأداة، `utility` يركّب `nvidia-smi` وNVML وحدهما، بلا مكتبات CUDA ولا `/dev/nvidia-uvm`. وعمليّات الكتابة في NVML (القدرة والتردّد وإعادة الضبط) تحتاج root، فتُرفض لمستخدمٍ 65534 بلا صلاحيّات |
| ما يتركه المصدّر على افتراضيّه | `--collect.compute-apps` مطفأ، فلا `pid: host`. و`--web.enable-pprof` مطفأ افتراضيّاً (المنبع `internal/app/app.go:183-186` في `v1.15.1`، قُرئ ولم يُشغَّل) |
| الصورة | الفهرس `sha256:7aee2d42…3d087` هو اليوم ما يشير إليه الوسم `1.15.1` على **Docker Hub وGHCR معاً** (البصمة نفسها من سجلّين). إعداد `linux/amd64`: `User=65534:65534`، و`Entrypoint=/usr/bin/nvidia_gpu_exporter`، و`Env` = ‏`PATH` و`SSL_CERT_FILE` فقط (لا `LD_PRELOAD`)، و`WorkingDir=/`، ولا `Volumes` ولا `OnBuild`. الأساس طبقات distroless debian13، ثمّ `COPY` واحد للثنائيّ. ولا وسوم OCI ‏`source`/`revision` |
| التوقيع | على GHCR وحده: OCI referrer من نوع `application/vnd.dev.sigstore.bundle.v0.3+json`، موضوعه بصمة الفهرس نفسها. شهادة Fulcio: الهويّة `https://github.com/utkuozdemir/nvidia_gpu_exporter/.github/workflows/release.yml@refs/tags/v1.15.1`، والمُصدِر `https://token.actions.githubusercontent.com`، والـcommit ‏`87afc069c5ce6a37b13db58989ec0f30d9b87217`، وrunner من GitHub، وRekor ‏`logIndex 2691430426`. وُقّع 2026‑09‑02 22:03 UTC، بعد بناء الصورة بتسع دقائق. **Docker Hub بلا أيّ أثر توقيع** (`.sig` و`.att` ووسم referrers كلّها: not found). ⚠️ **لم يُتحقَّق منه تشفيريّاً** (cosign غير مثبّت): فُكّت الحزمة وقُرئت الشهادة فقط، ولم تُفحص السلسلة ولا سجلّ الشفافيّة |
| الشبكة | النموذج المدمج (`--env-file .env.example`، ملفّ الكرت، profile ‏`monitoring`، مفلتراً إلى `networks`/`ports`): كلّ الخدمات على شبكة `default` واحدة، وهي ليست `internal`. والمصدّر `networks=['default']` |
| الكشف للخارج | nginx، وRunPod، وprovisioning ‏Grafana، وAlertmanager، وCI: بلا تغيير. وربط Prometheus الجديد `:ro`، ولا منفذ منشور جديد |
| `--profile` في السكربتات | `deploy/load/run.sh` و`deploy/backup/restore_drill.sh` يستعملان `run --rm --no-deps`، فلا يعيدان إنشاء حاويات المراقبة ولا يُسقطانها |
| سجلّ التدقيق | `platform.admin_audit_log` في Postgres، فلا يتأثّر بإطفاء Loki |

## الماسحات
- **لم يُشغَّل شيءٌ منها:** gitleaks وtrufflehog وtrivy وcosign وcrane وskopeo وhadolint غير مثبّتة.
- **pip-audit وnpm audit:** لا ينطبقان، فلا حزمة Python ولا npm جديدة (`pyproject.toml` بلا تغيير).
- **بدلها:**
  - `grep` على الأسطر المضافة.
  - `docker buildx imagetools inspect`: الفهرس، وإعداد `amd64`، ووسوم التوقيع على السجلّين.
  - واجهة GHCR للقراءة فقط: قائمة الوسوم، ووسم referrers الاحتياطيّ، وmanifest حزمة sigstore وملفّها، فُكّت بـ`python3 -I` في scratch.
  - `docker compose config --format json` مفلتراً إلى `networks` و`ports`، بـ`.env.example` لا `.env`.
  - قراءة `internal/app/app.go` من المنبع (`v1.15.1`) دون تشغيل.
- لم تُسحب صورة ولم تُشغَّل حاوية.

## الملاحظات
| # | الشدة | الفئة (OWASP/CWE) | الملف:السطر | الوصف | الإصلاح |
|---|---|---|---|---|---|
| SR‑1 | متوسّط | A05:2021 Security Misconfiguration؛ CWE‑653، CWE‑306 | `docker-compose.gpu.yml:112-144` (لا مفتاح `networks:`، فالشبكة `default`). الجيران: `docker-compose.yml:1000,1056` (Redis بلا `requirepass`)، و`:1165` (Qdrant بلا مفتاح API)، و`deploy/loki/loki.yml:24` (`auth_enabled: false`)، و`docker-compose.yml:2669` (`--web.enable-lifecycle`)، و`src/app/api/health.py:94` (`POST /health/drain` بلا مصادقة) | المصدّر صورة طرفٍ ثالث على الشبكة الوحيدة `default`، وهي ليست `internal` (لها مخرجٌ إلى الإنترنت). ومنها يصل بلا أيّ اعتماد إلى: `redis-stream` و`redis-cache` (التدفّقات، ونوافذ الحدّ، وقائمة `auth:revoked:`)، وQdrant (مجموعات `kn-<workspace_id>` لكلّ المستأجرين بحمولاتها)، وAlertmanager (إنشاء silence)، وPrometheus (`POST /-/quit`)، وLoki (دفع سجلّاتٍ مزوّرة)، و`app:8000/health/drain`، وOllama. **السيناريو:** تحديث بصمةٍ لاحق بلا تحقّق، أو حساب المشرف الفرد مخترَق، يأتي بثنائيٍّ يقرأ `SCAN`/`XRANGE` من Redis و`scroll` من كلّ مجموعة Qdrant، ثمّ يضع silence على كلّ التنبيهات، ويرسل البيانات إلى الخارج. وحاجته الحقيقيّة اتّصالٌ واحد: Prometheus يطلبه على 9835 | في `docker-compose.gpu.yml`: شبكةٌ عليا `gpu-metrics: {internal: true}`، و`nvidia-gpu-exporter.networks: [gpu-metrics]` وحدها، و`prometheus.networks: [default, gpu-metrics]`. ⚠️ اذكر `default` صراحةً، وإلّا حلّت القائمة محلّ الشبكة الضمنيّة وفقد Prometheus كلّ أهدافه. ثمّ حدّث الاختبارات `test_the_gpu_exporter_is_pinned_internal_unprivileged_and_bounded` و`test_prometheus_gets_one_mount_and_nothing_else` و`test_the_overlay_changes_only_the_services_it_names`. وإن أُجّل الإصلاح: الصفّ ث‑7 |
| SR‑2 | متوسّط | A06:2021 Vulnerable and Outdated Components؛ CWE‑1395، CWE‑367 | `deploy/gpu-preflight.sh:156-172` (يفحص وجود وقت التشغيل `nvidia` لا إصداره)؛ `docker-compose.gpu.yml:126-129` | خطافات NVIDIA Container Toolkit تعمل **بصلاحيّة root على المضيف**، وتقرأ جذر الصورة وبيئتها قبل أن يسري `user` و`cap_drop` و`no-new-privileges`. وثغراتٌ معروفة جعلت صورةً مصنوعةً عمداً تهرب إلى المضيف: CVE‑2024‑0132 وCVE‑2025‑23359 (TOCTOU)، وCVE‑2025‑23266 «NVIDIAScape» (`ENV LD_PRELOAD` في الصورة)، وأُصلحت حتّى 1.17.8. الميزة تضيف ثانيَ صورة طرفٍ ثالث تحجز الكرت (بعد `ollama/ollama`، ث‑3 وث‑4)، وإصدار الأداة على الخادم لم يُعرف بعد. **السيناريو:** بصمةٌ جديدة غير متحقَّق منها، على خادمٍ أداته أقدم من 1.17.8، تعطي root على المضيف عند `up -d` (مادّة Vault و`.env` وكلّ الأحجام)، قبل أن يعمل سطرٌ واحد من الثنائيّ. والبصمة الحاليّة نظيفة من هذا النمط (`Env` = ‏`PATH` و`SSL_CERT_FILE`، و`WorkingDir=/`). والجهاز المحلّيّ عليه 1.20.1 | `gpu-preflight.sh`: يقرأ `nvidia-ctk --version`، ويخرج بـ12 (أو WARN) تحت حدٍّ أدنى: 1.17.8 اليوم، ويُراجَع مع نشرات NVIDIA الأمنيّة في 7.3. وتضاف إلى إجراء تحديث البصمة (SR‑3) خطوة: رفض أيّ صورةٍ يضبط إعدادها `LD_PRELOAD` أو `LD_LIBRARY_PATH`. الصفّ ث‑8 |
| SR‑3 | منخفض | A08:2021 Software and Data Integrity Failures؛ CWE‑345 | `docs/deploy-server.md:97` (ث‑6)؛ `docker-compose.gpu.yml:104-106` (التعليق «verified … with `docker buildx imagetools inspect`») | `imagetools inspect` يثبت أنّ البصمة موجودة، لا أنّ مصدرها صحيح. وث‑6 يقول «لم يُتحقَّق من توقيع cosign»، ويقترح التحقّق قبل كلّ تحديث، لكنّه لا يقول أين التوقيع. **وDocker Hub، السجلّ الذي يسحب منه Compose، بلا أيّ أثر توقيع.** فمن يفحص `docker.io` يخلص إلى «غير موقَّع» ويتجاوز الخطوة، مع أنّ التوقيع موجود على GHCR (keyless، من workflow الإصدار) | يُعاد صياغة علاج ث‑6 بأمرٍ محدّد (النصّ المقترح أدناه)، ويُشغَّل مرّةً الآن على 1.15.1 وتُسجَّل نتيجته. يحتاج cosign: عند المالك أو devops. ويُصحَّح التعليق إلى: «same index digest on docker.io and ghcr.io; signature verification: docs/deploy-server.md ث‑6» |
| SR‑4 | منخفض | API4:2023 Unrestricted Resource Consumption؛ CWE‑770 | `docker-compose.gpu.yml:119-120` (`command` بلا `--collect.interval` ولا `--web.max-requests`) | افتراض المصدّر: الجمع متزامنٌ مع كلّ طلب (`--collect.interval=0`)، وحتّى 40 طلباً بالتوازي (`--web.max-requests=40`، المنبع `app.go:107-111`). فأيّ حاويةٍ على `default` ترسل 40 طلباً متوازياً تشغّل 40 `nvidia-smi` داخل 0.25 vCPU و64m: OOM للمصدّر (فينطلق `AizzakScrapeTargetDown`)، وتزاحمٌ على NVML. والأثر محصورٌ في المصدّر | `--collect.interval=15s` (جمعٌ في الخلفيّة، والطلب يقرأ آخر نتيجة: لا تضخيم) مع `--web.max-requests=2`. يحتاج تحديث اختبار المساواة، وإعادة قياس الذاكرة مع Q‑8 في 7.3، وقد يخفض الذروة نفسها (BUG‑1). أو يكفي SR‑1 وحده. الصفّ ث‑9 |
| SR‑5 | منخفض | A09:2021 Security Logging and Monitoring Failures؛ CWE‑778 | `docs/stack-commands.md:123`؛ `docs/design/08-local-runbook.md:755-756` | سطران قديمان بلا `monitoring`: «على خادمٍ … سطره `COMPOSE_PROFILES=local-llm`»، و«الإقلاع السريع: `COMPOSE_PROFILES=wsl-ollama` … أو `local-llm`». من يتبعهما يُقلع بلا Prometheus ولا Alertmanager، فتغيب **بصمت** `AizzakVaultAuthFailing` و`AizzakRateLimitedShareHigh` و`AizzakRedisStreamEvicted` (ومن أسبابه طرد مداخل `auth:revoked:`) | `local-llm,monitoring` و`wsl-ollama,monitoring` في الموضعين. واختبارٌ يرفض في الأدلّة `COMPOSE_PROFILES=local-llm` أو `COMPOSE_PROFILES=wsl-ollama` إذا لم تتبعه فاصلة: `test_both_guides_carry_the_monitoring_lines` يفحص حضور الجديد، لا غياب القديم |
| SR‑6 | منخفض | A09:2021؛ CWE‑778 | `docs/deploy-server.md:78-79` | البديل المقترح للخادم بلا مراقبة فحص `/health` من الخارج. لكنّ `/health` فحص حيويّة صِرف لا يلمس أيّ اعتماد (`src/app/api/health.py:69-71`)، فلا يرى فشل Vault ولا قاعدة البيانات ولا إساءة الاستعمال. ومع المراقبة يغيب Loki أيضاً، فتبقى السجلّات في `json-file` وحده (10m × 3 لكلّ حاوية): نافذة التحقيق ساعاتٌ على خدمةٍ مزدحمة | جملةٌ في §2 تقول: ما يغيب (التنبيهات الأمنيّة الثلاثة، وتجميع السجلّات)، وأنّ `/health` لا يعوّضه. ويطبعها `install.sh` (5.2) |
| SR‑7 | معلوماتيّ | CWE‑15 | `deploy/prometheus/prometheus.yml:47-48` | الـglob في `scrape_config_files` يجعل كلّ ملفّ `*.yml` تحت `/etc/prometheus/scrape.d/` إعدادَ كشط. اليوم لا يُربط هناك إلّا ملفٌّ واحد `:ro`، ولا واجهة كتابة عن بعد (`--web.enable-admin-api` غير مفعّل)، فالاستغلال يحتاج تنفيذاً داخل Prometheus أصلاً | أبقِ ما يُربط هناك ملفّاتٍ مفردة `:ro`، لا مجلّداً من المضيف |
| SR‑8 | معلوماتيّ | CWE‑200 | `deploy/prometheus/alerts.yml` (`summary` يحمل `{{ $labels.uuid }}`)؛ مقاييس المصدّر | UUID الكرت (ومعه الرقم التسلسليّ وعنوان PCI، حسب ث‑6) يدخل Prometheus وسطر `alert-sink` وLoki. معرّف عتادٍ منخفض الحساسيّة: يبصم الجهاز ولا يمنح وصولاً. والتسليم محلّيّ فقط | لا إجراء الآن. وإن صار للتنبيه قناةٌ خارجيّة، فـ`index` أو `name` بدل `uuid` في `summary` |

**العدد:** حرج 0 · عالٍ 0 · متوسّط 2 · منخفض 4 · معلوماتيّ 2.

## صفوفٌ مقترحة لقائمة الأمان في `docs/deploy-server.md` (لم أعدّل الملف)

**صفوفٌ جديدة:**

| # | الثغرة | الأثر | العلاج المقترح |
|---|---|---|---|
| ث‑7 | مصدّر الكرت، وكلّ خدمات المكدّس، على شبكة `default` واحدة غير `internal`. ومنها بلا مصادقة: خادما Redis، وQdrant، وLoki، وAlertmanager، و`/-/quit` في Prometheus، و`/health/drain` في `app`، وOllama (SR‑1، monitoring-profile) | صورة طرفٍ ثالث مخترَقة تقرأ بيانات كلّ المستأجرين من Redis وQdrant، وتُسكت التنبيهات، وتُخرج البيانات إلى الإنترنت | شبكة `gpu-metrics` بـ`internal: true` في `docker-compose.gpu.yml`، للمصدّر وPrometheus وحدهما. ثمّ في الموجة 8: شبكةٌ لكلّ طبقة، و`requirepass` لـRedis، ومفتاح API لـQdrant |
| ث‑8 | `deploy/gpu-preflight.sh` يفحص وجود أداة NVIDIA لا إصدارها. وخطافاتها تعمل root على المضيف بجذر الصورة وبيئتها، وصورتا طرفٍ ثالث تحجزان الكرت: `ollama/ollama` والمصدّر (SR‑2) | أداةٌ أقدم من 1.17.8 مع صورةٍ مصنوعة تعطي root على المضيف (CVE‑2024‑0132، CVE‑2025‑23359، CVE‑2025‑23266) | حدٌّ أدنى لـ`nvidia-ctk --version` في الفحص (رمز 12)، يُراجَع مع نشرات NVIDIA. ورفض أيّ صورةٍ يضبط إعدادها `LD_PRELOAD` عند تحديث البصمة |
| ث‑9 | المصدّر يشغّل `nvidia-smi` مع كلّ طلب، حتّى 40 بالتوازي، ولأيّ جارٍ على الشبكة (SR‑4) | أيّ حاويةٍ مخترَقة تُسقط المصدّر بـOOM | `--collect.interval=15s` و`--web.max-requests=2`، أو يكفي ث‑7 |

**تعديل عمود «العلاج المقترح» في ث‑6 (SR‑3):**

> قبل كلّ تحديثٍ للبصمة:
> 1. `docker buildx imagetools inspect` للوسم الجديد على `docker.io/utkuozdemir/nvidia_gpu_exporter` وعلى `ghcr.io/utkuozdemir/nvidia_gpu_exporter`، والبصمة واحدة في الاثنين.
> 2. `cosign verify ghcr.io/utkuozdemir/nvidia_gpu_exporter@sha256:<بصمة الفهرس> --certificate-identity 'https://github.com/utkuozdemir/nvidia_gpu_exporter/.github/workflows/release.yml@refs/tags/v<X.Y.Z>' --certificate-oidc-issuer https://token.actions.githubusercontent.com`
>    - التوقيع على GHCR وحده، حزمة sigstore مرفقة كـOCI referrer. وDocker Hub بلا توقيع.
>    - بـcosign v3؛ وإن لم يجد التوقيع فبـ`--new-bundle-format`.
> 3. إعداد الصورة: `User=65534:65534`، ولا `LD_PRELOAD` ولا `LD_LIBRARY_PATH`.
>
> أو تُنسخ الصورة إلى `anastawahia/aizzak` بوسمٍ ببادئة. أو يُستعمل `dcgm-exporter` الرسميّ بعد 7.3، إن كان كرت الخادم من فئة مراكز البيانات.

## خارج نطاق الفرق (قائمٌ قبله، للعلم)
- **كلمة مدير Grafana:** `GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-admin}` (`docker-compose.yml:2710`)، وقيمتها في القالب placeholder ثابت. Grafana على `127.0.0.1` فقط. لكنّ المراقبة صارت مفعّلةً افتراضيّاً على الخوادم (قرار 7)، فليولّد `install.sh` (5.2) هذه الكلمة كما يولّد `METRICS_EXPORTER_PASSWORD`.
- **حدّ العمليّات:** لا `pids_limit` على أيّ خدمةٍ من الـ34.
- **RunPod:** صورة الـPod لا تستعمل الـprofile ولا ملفّ الكرت، فلا أثر للميزة عليها. وقائمتها في `docs/deploy-runpod.md` §8.4 (ث‑6 هناك: لا مراقبة داخل الـPod) لا تحتاج تعديلاً.

---

## الجولة 1 (إعادة العمل: `git diff 278defa..HEAD`، HEAD ‏`f5591c2`، 6 التزامات، 19 ملفّاً)

- **قُرئت كاملةً:** `deploy/gpu-preflight.sh`، و`docker-compose.gpu.yml`، وقائمة الأمان و§2 في `docs/deploy-server.md`، وفرق الاختبارات الستّة، و`test-plan.md` §6، و`status.md` («قرارات وتصعيدات»: المالك يطلب إصلاح كلّ الملاحظات، ويوافق على تعديل `gpu-preflight.sh`، ويقبل BUG‑1).
- **الحكم:** **PASS**. **وشرط الجولة 0 تحقّق:** ث‑7 وث‑8 وث‑9 مسجّلة، وعلاج ث‑6 أُعيدت صياغته بأمر cosign صحيح، وهو معلَّمٌ «لم يُشغَّل بعد». لكنّ الصفوف الثلاثة الجديدة لا تظهر جدولاً (SR‑11).
- **لماذا PASS:**
  - لا حرج ولا عالٍ ولا متوسّط.
  - المتوسّطان SR‑1 وSR‑2 عولجا، وتحقّقتُ من ذلك بالنموذج المدمج وبالاختبارات، وأثبت QA العزل حيّاً.
  - لا مسار تطبيقيّ جديد، ولا فحص صلاحيّات أو عزل مستأجرين ناقص.
  - المصدّر لم يعد يصل إلى Redis ولا Qdrant، فأُغلق الطريق الذي كان يقترب من «عزل مستأجرين ناقص» (⚠️ الجولة 0).

### حالة ملاحظات الجولة 0
| # | الحالة | الدليل |
|---|---|---|
| SR‑1 | ✅ عولج | `docker-compose.gpu.yml:100,145,162-166`. النموذج المدمج (`--env-file .env.example`، ملفّ الكرت، `--profile monitoring --profile local-llm`، مفلتراً إلى `networks`/`ports`): المصدّر `networks=['gpu-metrics']`، و`ports=[]`، وPrometheus ‏`['default','gpu-metrics']`، و`gpu-metrics` ‏`internal: true`. **ولا خدمة غيرهما من الـ36 على `gpu-metrics`.** حارسه `test_the_exporter_joins_no_network_but_an_internal_gpu_metrics`، وQA أفسده فرآه يفشل. وحيّاً (QA §6 ج): `redis-stream` ‏`bad address`، و`1.1.1.1:443` يفشل، وPrometheus يكشط المصدّر. ما بقي ← SR‑9 وSR‑10 |
| SR‑2 | ✅ عولج | `deploy/gpu-preflight.sh:52,188-222,261-264,315-318`، وتفصيله في «فحص `gpu-preflight.sh`» أدناه. على هذا الجهاز: ctk ‏1.20.1 OK (QA §6 د). بقايا معلوماتيّة ← SR‑13 |
| SR‑3 | ✅ عولج نصّاً؛ ⏳ التحقّق نفسه معلّق | علاج ث‑6 أُعيدت صياغته (التحليل أدناه)، وصُحّح تعليق `docker-compose.gpu.yml:109-111`. أمر `cosign verify` لم يُشغَّل بعد، ولا cosign على هذا الجهاز: يشغّله المالك أو devops، والصفّ يقول ذلك صراحةً |
| SR‑4 | ✅ عولج | `docker-compose.gpu.yml:134-135`: ‏`--collect.interval=15s` (الطلب يقرأ آخر نتيجة، فلا `nvidia-smi` لكلّ طلب)، و`--web.max-requests=2` (ما زاد يُرَدّ بـ503). والعلَمان مقبولان في v1.15.1: المصدّر بقي `healthy` عشر دقائق والهدف `up` (QA §6 ب، ج). ولم يخفضا الذروة (56.2 م.ب)، فـBUG‑1 باقٍ خطراً قبله المالك |
| SR‑5 | ✅ عولج في الموضعين | `stack-commands.md:123` و`08-local-runbook.md:756-757` صارا `,monitoring`. والحارس الجديد يغطّي `stack-commands.md` و`quickstart.md` فقط. بقايا من الصنف نفسه ← SR‑14 |
| SR‑6 | ✅ عولج | `docs/deploy-server.md:81-82`: ما يغيب (التنبيهات الأمنيّة الثلاثة وLoki ونافذة `json-file`)، وأنّ `/health` لا يعوّضه |
| SR‑7 | ✅ (معلوماتيّ) | تعليق القاعدة في `docker-compose.gpu.yml:93-94`. والربط ما زال ملفّاً مفرداً `:ro` |
| SR‑8 | — (معلوماتيّ) | لا إجراء مطلوب، ولا تغيير |

### صفوف قائمة الأمان (`docs/deploy-server.md:93-104`)
| الصفّ | الحكم |
|---|---|
| ث‑6 | ✅ **أمر cosign صحيح:** `--certificate-identity` بالقيمة التامّة `https://github.com/utkuozdemir/nvidia_gpu_exporter/.github/workflows/release.yml@refs/tags/v<X.Y.Z>`، و`--certificate-oidc-issuer https://token.actions.githubusercontent.com`. وهما ما قرأتُه من شهادة Fulcio في الجولة 0 (وسم git ‏`v1.15.1` ووسم الصورة `1.15.1`، فالـ`v` في مكانها). التحقّق بالبصمة على GHCR، وCompose يسحب البصمة نفسها من Docker Hub. والبصمة عنوان محتوى، فالخطوة (1) هي ما يربط السجلّين. وخطوة `LD_PRELOAD`/`LD_LIBRARY_PATH` موجودة. و«لم يُشغَّل بعد» بارز (⚠️). عمود «الأثر» قديم ← SR‑12 |
| ث‑7 | ✅ مسجّل: «عولج جزئيّاً»، والخطر الباقي مذكور (أيّ خدمة مخترَقة على `default`)، وبنود الموجة 8 مسمّاة (شبكة لكلّ طبقة، و`requirepass` لـRedis، ومفتاح API لـQdrant). لكنّه لا يذكر ما يبقى في متناول المصدّر نفسه ← SR‑9 وSR‑10. ولا يظهر في الجدول ← SR‑11 |
| ث‑8 | ✅ مسجّل: الحدّ 1.17.8 ورمز 12، والمراجعة مع نشرات NVIDIA في 7.3، ورفض `LD_PRELOAD` عند تحديث البصمة. والخطر الباقي مذكور (حدٌّ يتقادم). ولا يظهر في الجدول ← SR‑11 |
| ث‑9 | ✅ مسجّل. لكنّ «الأثر» يقول إنّ الذروة لم يُعَد قياسها بالعلَمين، وQA أعاد قياسها بعد كتابة الصفّ: 56.2 م.ب ← SR‑12. ولا يظهر في الجدول ← SR‑11 |

### فحص `gpu-preflight.sh` (الكود الجديد)
- **التنفيذ:**
  - النداء الوحيد `run "$PROBE_TIMEOUT_S" nvidia-ctk --version` (`:198`): وسائط مقتبسة، ولا `eval`، ولا `sh -c`، ولا ملفّ مؤقّت جديد. ويُعثر على الأداة بـ`command -v` في PATH المشغِّل، كبقيّة الفحوص.
  - المهلة 5 ثوانٍ: ثنائيٌّ يعلق 8 ثوانٍ ← `timed out after 5s` ورمز 12.
  - الحارس الثابت يقصر ظهور `nvidia-ctk` على `--version`، والأداة المزيّفة في الاختبار ترفض أيّ وسيطٍ غيره.
- **ناتج الأداة غير موثوق، وجرّبتُه بأدواتٍ مزيّفة في scratch (لا Docker ولا حاويات):**
  - نصّ `$(touch …)`، وعلامات `` ` ``، و`a[$(…)]`: لم يُنفَّذ شيء، فلا ملفّ أُنشئ.
  - الالتقاطات أرقامٌ فقط بحكم التعبير المنتظم، فلا حقن في الحساب `$((10#…))`.
  - تسلسلات ANSI لا تُطبع: السطر يطبع الأرقام الثلاثة وحدها، ومسار «غير مقروء» لا يطبع الناتج أصلاً.
- **الإغلاق عند الشكّ:**
  - غياب الأداة ← 12 (`absent`).
  - خروج غير صفريّ، أو نصّ بلا إصدار، أو إصدار في السطر الثاني وحده، أو مهلة منتهية ← 12 (`unreadable`).
  - إصدار أقدم ← 12 (`old`).
  - و`--smoke` لا يغلب ذلك، بل لا يُشغَّل على أداةٍ قديمة (`:261-264`)، وهذا أحسن من المطلوب.
  - وحين يُتخطّى الفحص (لا تعريف أو لا Docker) يكون الخروج 10 أو 11، فلا يُستعمل ملفّ الكرت.
- **الأسرار:** لا يقرأ بيئةً ولا ملفّاً، ويطبع رقم الإصدار وحده.
- **بقايا معلوماتيّة ← SR‑13:**
  - `1.17.8-rc.1` يُقبل على أنّه 1.17.8.
  - رقمٌ من 20 خانة يلتفّ ويمرّ. ولا أثر أمنيّ لذلك: من يتحكّم بناتج الثنائيّ يتحكّم بالثنائيّ نفسه.
  - الفحص يقرأ `nvidia-ctk` الذي في PATH، لا الثنائيّ الذي يستدعيه Docker.

### الشبكة: هل يصير Prometheus جسراً بين الشبكتين؟
- **على مستوى الشبكة: لا.** المصدّر بلا `NET_ADMIN` (`cap_drop: ["ALL"]`)، فلا يضيف مساراً عبر Prometheus. وحاوية Prometheus لا تمرّر الحزم، وDocker يعزل الجسور عن بعضها. وPrometheus كان يصل إلى كلّ `default` أصلاً، والشبكة الجديدة لا تضيف له إلّا نظيراً واحداً.
- **على مستوى التطبيق: نعم، بقدرٍ محدود.** المصدّر يصل إلى `prometheus:9090` (أثبته QA)، وPrometheus يحلّل ناتج المصدّر ويقف على `default`. ← SR‑9.
- **منفذٌ جديد من خارج المضيف: لا.** المنافذ المنشورة في النموذج المدمج هي هي: `nginx` على 80 و443، والبقيّة على `127.0.0.1`. والمصدّر وPrometheus بلا `ports`، و`docker-compose.yml` لم يتغيّر في هذه الجولة.
- **العلَمان:** مناسبان. Prometheus العميل الوحيد، فطلبان متوازيان يكفيان. والجمع كلّ 15 ثانية يساوي فاصل الكشط تقريباً، فلا تأخّر يُذكر في التنبيهين (`for: 5m`).

### الأسرار
- لا سرّ في الأسطر المضافة (365 سطراً): بحثتُ بأنماط AWS وGitHub وSlack و`sk-` وVault ‏(`hvs.`) وJWT والمفاتيح الخاصّة، وبأنماط `password/secret/token=` وDSN بكلمة مرور. ثمّ راجعتُ يدويّاً السلاسل الطويلة (≥ 32 حرفاً): كلّها مسارات وأسماء اختبارات.
- `FAKE_CTK_OUT` في الاختبار نصُّ إصدار. والـcommit ‏`0123abc` وهميّ.

### الماسحات وما شُغّل
- **غير مثبّتة، كما في الجولة 0:** gitleaks، وtrufflehog، وtrivy، وgrype، وsyft، وcosign، وcrane، وskopeo، وhadolint، وshellcheck، وpip-audit.
- **pip-audit وnpm audit:** لا ينطبقان (لا تبعيّة Python ولا npm جديدة).
- **ما شُغّل:**
  - `docker compose … config --format json` مفلتراً إلى `networks`/`ports` بـ`python3 -I`، بـ`.env.example` لا `.env`.
  - `docker version`: المحرّك هنا 29.6.1.
  - `pytest` على `test_gpu_preflight.py` و`test_compose_gpu_overlay.py` و`test_monitoring_profile.py`: ‏113 passed، rc 0 (بلا cache وبلا bytecode).
  - `gpu-preflight.sh` مع أدوات مزيّفة على PATH في scratch.
  - `grep` على الفرق.
- لم تُسحب صورة، ولم تُشغَّل حاوية ولا حُذفت، ولم يُقرأ `.env`.

### ملاحظات الجولة 1
| # | الشدة | الفئة (OWASP/CWE) | الملف:السطر | الوصف | الإصلاح |
|---|---|---|---|---|---|
| SR‑9 | منخفض | API4:2023 Unrestricted Resource Consumption؛ CWE‑770، CWE‑918 | `deploy/prometheus/scrape-gpu.yml:5-8` (لا حدود ولا `follow_redirects`)؛ `docker-compose.yml:2669` (`--web.enable-lifecycle`)؛ `docker-compose.gpu.yml:100`؛ `docs/deploy-server.md:102` (ث‑7) | جار المصدّر الوحيد Prometheus، وهو يثق بناتجه ويقف على `default`. **السيناريو:** تحديث بصمةٍ مخترَق يأتي بثنائيٍّ يفعل أحد ثلاثة. (أ) يرسل `POST prometheus:9090/-/quit` في حلقة: Prometheus يعيد التشغيل (`unless-stopped`) ثمّ يُطفأ ثانية، فلا تقييم للقواعد ولا تنبيه. والتسليم محلّيّ فقط، فلا أحد خارجيّ يلاحظ غياب `AizzakWatchdog`. (ب) يعرض ملايين السلاسل في `/metrics`: لا `sample_limit`، فيمتلئ الـTSDB حتّى OOM عند `2g`. والـWAL يحفظها، فيتكرّر الـOOM عند كلّ إقلاع. (ج) يردّ بـ302 إلى خدمةٍ على `default`: ‏`follow_redirects` افتراضه `true`، فيطلبها Prometheus بـGET. والأثر اليوم ضئيل: Prometheus 3 يرفض نوع المحتوى غير المعروف، ولا GET بأثرٍ جانبيّ معروف. **النتيجة:** المصدّر المخترَق لم يعد يقرأ بيانات المستأجرين، لكنّه يستطيع إعماء المراقبة. ولا بيانات مستأجرين في المقاييس (لا تسمية `workspace` في `src/app/framework/observability/metrics.py`)، فقراءة واجهة الاستعلام لا تكشف شيئاً منها | في `scrape-gpu.yml`: `sample_limit` (عدد `scrape_samples_scraped{job="nvidia-gpu"}` المقيس × 4)، و`label_limit`، و`label_value_length_limit`، و`body_size_limit`، و`follow_redirects: false`. تجاوز الحدّ يُسقط الكشط كلّه، فينطلق `AizzakScrapeTargetDown`، وهي إشارة مفيدة. وفي ث‑7 يُضاف سطر: «المصدّر ما زال يصل إلى `prometheus:9090` (`/-/quit` و`/-/reload` وواجهة الاستعلام)». ثمّ في الموجة 8: `--web.config.file` بمصادقة، أو إطفاء الـlifecycle |
| SR‑10 | منخفض | A06:2021 Vulnerable and Outdated Components؛ CWE‑1395 | `deploy/gpu-preflight.sh:151-162` (يقرأ `ServerVersion` ولا يفرض حدّاً أدنى)؛ `docker-compose.gpu.yml:162-166` | `internal: true` لا يمنع الخروج عبر DNS على Docker Engine أقدم من 25.0.5 (أو 23.0.11 على فرع 23؛ والإصلاح في 26.0.0): ‏CVE‑2024‑29018. خادم DNS المدمج يمرّر الأسماء الخارجيّة من شبكة المضيف حتّى لحاويةٍ على شبكةٍ داخليّة وحدها. والفحص يفرض Compose ≥ 2.20 لا المحرّك. واختبار QA للخروج (`1.1.1.1:443`) كان على 29.6.1 وعلى TCP، لا على DNS. **السيناريو:** على خادمٍ بمحرّكٍ قديم، يُخرج مصدّرٌ مخترَق بياناته في أسماء نطاقات (`<data>.attacker.example`). وبعد SR‑1 لا يصل المصدّر إلى بيانات المستأجرين، فما يُخرج قليل: هويّة الكرت، ومقاييس Prometheus (SR‑9) | `readonly MIN_DOCKER_ENGINE="26.0.0"` في `gpu-preflight.sh`، يُقارن بـ`ServerVersion` كما يُقارن Compose، ويخرج بـ11 تحته. أو، أقلّ من ذلك، سطرٌ في ث‑7: «عزل `gpu-metrics` يتطلّب محرّكاً ≥ 26.0.0 (CVE‑2024‑29018)» |
| SR‑11 | منخفض | CWE‑1059 Insufficient Technical Documentation | `docs/deploy-server.md:101` | سطرٌ فارغ بين ث‑6 وث‑7 يُنهي الجدول. فالصفوف ث‑7 وث‑8 وث‑9 تُعرض فقرةً واحدة فيها `|` حرفيّة، لا صفوف جدول. وهذه قائمة المخاطر المقبولة التي يقرؤها المالك (قاعدة `CLAUDE.md`)، وثلاثة صفوفٍ منها يسهل أن تفوته. والاختبار يقرأ الأسطر واحداً واحداً، فلا يلاحظ | احذف السطر 101. ويمكن أن يتحقّق اختبارٌ من أنّ كلّ سطرٍ يبدأ بـ`| ث‑` يقع في كتلة الجدول نفسها |
| SR‑12 | معلوماتيّ | CWE‑1059 | `docs/deploy-server.md:100` (ث‑6 «الأثر»)؛ `:104` (ث‑9 «الأثر»)؛ `docker-compose.gpu.yml:115-117` | ثلاثة نصوصٍ تقادمت بعد إعادة العمل: (1) ث‑6 يقول إنّ المنفذ يكشف هويّة الكرت «لكلّ خدمةٍ على الشبكة»، وصار لـPrometheus وحده. (2) ث‑9 يقول إنّ الذروة «لم يُعَد قياسها بهذين الخيارين»، وQA أعاد قياسها: 56.2 م.ب، والعلَمان لم يخفضاها. (3) تعليق الملفّ يقول «the peak may drop»، وهذا ما نفاه القياس | (1) «لـPrometheus، جاره الوحيد على `gpu-metrics`». (2) «أُعيد القياس محلّيّاً بالعلَمين: 56.2 م.ب، ‏× 1.5 > 64m، خطرٌ قبله المالك، ويُعاد على الخادم في 7.3». (3) احذف «(the peak may drop)» أو اكتب «measured 56.2 MiB with both flags» |
| SR‑13 | معلوماتيّ | CWE‑1104؛ CWE‑697 | `deploy/gpu-preflight.sh:193-211` | (1) التعبير `([0-9]+)\.([0-9]+)\.([0-9]+)` غير مثبّتٍ ويتجاهل اللاحقة، فـ`1.17.8-rc.1` يمرّ على أنّه 1.17.8، والإصدار التجريبيّ يسبق النهائيّ. (2) رقمٌ من 20 خانة يلتفّ في الحساب ويمرّ (بلا أثرٍ أمنيّ). (3) يُفحص `nvidia-ctk` الذي في PATH المشغِّل. أمّا ما يستدعيه Docker فهو `nvidia-container-runtime` (مساره في `docker info`) أو مسار الخطّاف في مواصفة CDI، ومعه `nvidia-container-cli` (libnvidia-container، وفيها جزءٌ من CVE‑2024‑0132). وتبعيّات الحزم تبقيها في إصدارٍ واحد عادةً، فالفحص تقريبٌ جيّد لا ضمان. (4) الفحص يُشغَّل مرّةً عند التجهيز، ولا شيء في `up` يعيده | (1)(2) `version ([0-9]{1,4})\.([0-9]{1,4})\.([0-9]{1,4})`، ولاحقة `-rc`/`~rc` تُعدّ أقلّ من الحدّ. (3) اختياريّ: فحص `nvidia-container-cli --version` بالحدّ نفسه. (4) جملة في ث‑8: «يُعاد `gpu-preflight.sh` بعد كلّ تحديثٍ لحزم NVIDIA» |
| SR‑14 | معلوماتيّ | A09:2021؛ CWE‑778 | `docs/runbooks/alerts.md:525,527`؛ `tests/unit/test_monitoring_profile.py:333` | صنف SR‑5 نفسه خارج الدليلين: إجراء `AizzakLlmCircuitOpen` يقول إنّ سبب غياب الجسر «نقصُ `COMPOSE_PROFILES=wsl-ollama` من `.env`»، ويصف الخادم بـ`COMPOSE_PROFILES=local-llm`. فمن يتبعه حرفيّاً قد يكتب السطر بلا `monitoring`، فتُطفأ المراقبة بصمت عند أوّل إعادة إنشاء. والحارس الجديد يغطّي `stack-commands.md` و`quickstart.md` فقط، لا `08-local-runbook.md` (موضع SR‑5 الثاني) ولا `runbooks/alerts.md`. أمّا `server-gpu-plan.md:124,213,218` فتاريخيّة، تبقى كما هي | `,monitoring` في `alerts.md:525,527`. وأضف `design/08-local-runbook.md` و`runbooks/alerts.md` إلى الحارس، بنمطٍ يستثني الوصف التاريخيّ، أو اكتفِ بحارسٍ على الأسطر التي تبدأ بـ`COMPOSE_PROFILES=` داخل كتل الشيفرة |

**العدد (الجولة 1):** حرج 0 · عالٍ 0 · متوسّط 0 · منخفض 3 (SR‑9، SR‑10، SR‑11) · معلوماتيّ 3 (SR‑12، SR‑13، SR‑14).

**المعلّق خارج الشيفرة (لا يحجب):** أمر `cosign verify` على 1.15.1، يشغّله المالك أو devops ويسجّل نتيجته في ث‑6. وإعادة قياس ذاكرة المصدّر على الخادم في 7.3 (BUG‑1).

---

## الجولة 2 (إعادة العمل، `7bda359..08e4540`)

- **النطاق:** `git diff 7bda359..08e4540`، وفيه أربعة التزامات: `dc1ca50` (وثائق)، و`9234a17` (الفحص)، و`3eab510` (حدود الكشط)، و`08e4540` (رأس ملفّ الكرت). 13 ملفّاً، و241 سطراً مضافاً.
- **قُرئت كاملةً:**
  - `deploy/gpu-preflight.sh`، و`deploy/prometheus/scrape-gpu.yml`، و`docker-compose.gpu.yml` (المصدّر والشبكة).
  - قائمة الأمان في `docs/deploy-server.md`، وفروق الاختبارات الخمسة.
  - `status.md`: قرارات 2026‑10‑18، ودورة الإعادة 2.
- **الحكم:** **PASS.**
- **لماذا PASS:**
  - عولجت SR‑9…SR‑14 كلّها. وما لم يُنفَّذ (SR‑13 (3)) تركه المالك صراحةً.
  - حدود الكشط ليست مكتوبةً فقط. أثبتُّ على ثنائيّ v3.13.2 نفسه أنّ كلّ حدٍّ يُسقط الكشط، وأنّ `follow_redirects: false` هو ما يمنع التحويل.
  - لا حرج ولا عالٍ ولا متوسّط. ولا مسار تطبيقيّ جديد، فلا فحص صلاحيّات ناقص ولا فحص عزل مستأجرين ناقص.
  - الجديد ملاحظةٌ منخفضة واحدة (SR‑15). وهي قائمةٌ منذ الجولة 0، لا من هذه الالتزامات، وشرطها شرط SR‑9 نفسه: صورة مصدّرٍ مخترَقة.

### حالة ملاحظات الجولة 1
| # | الحالة | الدليل |
|---|---|---|
| SR‑9 | ✅ عولج، ومُثبَت حيّاً | `scrape-gpu.yml:17-24`: الحدود الخمسة و`follow_redirects: false`. **القبول:** `promtool check config` من `prom/prometheus:v3.13.2` يقبل الملف (`test-rules.sh`، الوضع ب). والتحليل صارم: نسخةٌ فيها مفتاحٌ مخطوء (`follow_redirect`) تُرفض بـ`field follow_redirect not found in type config.ScrapeConfig`، فالمفاتيح تُقرأ ولا تُتجاهَل. **الفعاليّة:** شغّلتُ ثنائيّ v3.13.2 مستخرجاً من الصورة على 127.0.0.1 في scratch، أمام مصدّرٍ مزيّف. والقيم منسوخة آليّاً من الملف الحقيقيّ، ولم يتغيّر غير الهدف والمسار. النتائج: 5001 عيّنة ← `sample limit exceeded`. و43 تسمية ← `label_limit exceeded`. واسمٌ من 65 حرفاً ← `label_name_length_limit exceeded`. وقيمةٌ من 257 حرفاً ← `label_value_length_limit exceeded`. وجسمٌ من 3 م.ب ← `body size limit exceeded`، **وكذلك الجسم نفسه مضغوطاً بـgzip**، فالحدّ يُطبَّق بعد فكّ الضغط، ولا قنبلة gzip. و302 ← `server returned HTTP status 302 Found`، والخادم «الضحيّة» لم يُطلب مرّةً واحدة، بينما طلبته المهمّة الضابطة بلا `follow_redirects` خمس مرّات. في كلّ حالةٍ `up=0`، ولم تُخزَّن عيّنةٌ واحدة، والمهمّة السليمة (400 عيّنة) `up=1`. حارسه `test_observability_stack_wiring.py:223`. وسطر ث‑7 المطلوب موجود. **ما لا تغطّيه الحدود ← SR‑15، وتقييم سطر الـlifecycle ← SR‑16** |
| SR‑10 | ✅ عولج | `gpu-preflight.sh:55`: ‏`readonly MIN_DOCKER_ENGINE="26.0.0"`، يُقارَن بـ`ServerVersion` بمقارنة أعداد (`:193-207`)، ويخرج بـ11. والإصدار غير المقروء 11 أيضاً، وكذلك الإصدار التجريبيّ من 26.0.0 نفسه. وجرّبتُ بأدواتٍ مزيّفة حالاتٍ غير التي في الاختبار، فكلّها خرجت بـ11: `26.0.0~rc1`، و`26.0.0-beta.2`، و`25.0.99999`، و`20.10.24+dfsg1`، و` 26.0.0` (بمسافةٍ أولى). ولا حارس للخانة الخامسة في المحرّك، لكنّ كلّ التقاطٍ ≤ 4 خانات، والجزآن الأوّلان محصوران بالنقاط، فلا يمرّ محرّكٌ أقدم: `25.0.99999` يُقرأ 25.0.9999. وث‑7 يحمل السطر («يتطلّب Docker Engine ≥ 26.0.0 … ويفرضه `gpu-preflight.sh` بالرمز 11»)، ومعه جدول المتطلّبات (`deploy-server.md:22`) وجدول الرموز (`:67`) و`stack-commands.md:590`. والحدّ يرفض 25.0.5 و23.0.11 وفيهما الإصلاح أيضاً، وهذا تشدّدٌ مقبول. حارسه `test_gpu_preflight.py:592,601` |
| SR‑11 | ✅ عولج، ومُثبَت بالعرض | `deploy-server.md:101-104` متّصلة. عرضتُ الملف بـ`markdown-it-py` 4.2.0 (commonmark مع الجداول)، فظهر جدولٌ واحد فيه الصفوف التسعة ث‑1…ث‑9 في `<tr>`، ولا فقرة تبدأ بـ`\| ث‑`. حارسه `test_gpu_preflight.py:663`، ويفحص الكتلة كلّها من الرأس إلى آخر صفّ |
| SR‑12 | ✅ عولج في المواضع الثلاثة | ث‑6 (`:101`): «لـPrometheus وحده، جاره الوحيد على `gpu-metrics`». وث‑9 (`:104`): «56.2 م.ب (54.1 دونهما)، و× 1.5 فوق الحدّ». وتعليق `docker-compose.gpu.yml:116-118`: «56.2 MiB with both flags below (54.1 without)». ولا أثر لـ«may drop» ولا لـ«لم يُعَد قياسها» في الملفّات الأربعة. وصُحّح `server-gpu-plan.md:134` معها |
| SR‑13 | ✅ عولج (1) و(2) و(4)؛ وترك المالك (3) | **(1)** `:244`: التعبير يشترط كلمة `version ` قبل الأرقام، وكلّ جزءٍ ≤ 4 خانات. ولاحقة `rc` أو `alpha` أو `beta` تُعدّ أقلّ من الحدّ نفسه، لا من إصدارٍ أحدث منه (`:259-260`، و`is_prerelease` في `:126-130`). ويُقبل قبلها فاصلٌ من `-~._+` أو لا فاصل، بأيّ حالة أحرف. وجرّبتُ حالاتٍ غير التي في الاختبار، فخرجت بـ12: `1.17.8.rc1`، و`1.17.8~rc.1-1`، و`1.17.8-RC2`. **(2)** `:252`: خانةٌ خامسة في الرقم الأخير ← `unreadable`، فـ12. و`1.17.00008` ← 12. **(4)** جملة ث‑8 موجودة: «ويُعاد `deploy/gpu-preflight.sh` بعد كلّ تحديثٍ لحزم NVIDIA…». **بقايا بلا إجراء:** `1.17.8 rc1` (بمسافة) و`1.17.8-pre1` تُقبلان، وNVIDIA لا تستعمل هذين الشكلين. **(3)** فحص `nvidia-container-cli` تركه المالك (status.md، دورة الإعادة 2). فيبقى الفحص تقريباً جيّداً لا ضماناً، كما في الجولة 1 |
| SR‑14 | ✅ عولج | `runbooks/alerts.md:525` («السطر الكامل `COMPOSE_PROFILES=wsl-ollama,monitoring`») و`:527` (`local-llm,monitoring`). والحارس `test_monitoring_profile.py:350` يغطّي `runbooks/alerts.md` و`design/08-local-runbook.md`. يفحص النصّ الحرفيّ بين علامتي `` ` ``، والأسطر التي تبدأ بـ`COMPOSE_PROFILES=`. أفسدتُ نسخاً في الذاكرة، فالتُقط إرجاع `:527` إلى `local-llm`، والتُقط سطرٌ عارٍ في كتلة شيفرة. ولا يُلتقط شكل `export COMPOSE_PROFILES=…`، ولا سطر منه اليوم، فلا إجراء. وما بقي في المستودع بلا `monitoring` هو `server-gpu-plan.md:124,213,218`، وهي أسطر تاريخيّة قُبلت في الجولة 1 |

### قرار `--web.enable-lifecycle` وسجلّه في ث‑7
- **القرار للمالك، ولا أعترض عليه.** الخطر الباقي منخفض: يحتاج أوّلاً صورة مصدّرٍ مخترَقة، والبصمة مثبّتة، وإجراء تحديثها مكتوب (ث‑6).
- **التسجيل كافٍ في نصفه.** ث‑7 يسمّي ما يصل إليه المصدّر (`/-/quit` و`/-/reload` وواجهة الاستعلام)، وسببه (إجراء تعديل العتبات)، وما يخفّفه (حدود الكشط). لكنّ فيه أربعة نواقص ← SR‑16:
  1. **عمود «الأثر»** يصف خطر «أيّ خدمةٍ أخرى مخترَقة على `default`» وحده، ولا يقول ما يفعله المصدّر نفسه. حلقة `POST /-/quit` توقف تقييم القواعد الـ29 كلّها، ومنها الأمنيّة. والتسليم محلّيّ فقط، فلا أحد في الخارج يلاحظ غياب `AizzakWatchdog`.
  2. **عمود «العلاج»** بلا بندٍ لهذا الخطر. بنود الموجة 8 (شبكةٌ لكلّ طبقة، و`requirepass`، ومفتاح Qdrant) لا تغلق `/-/quit`، لأنّ Prometheus يبقى على `gpu-metrics` في كلّ حال.
  3. **السبب أضعف ممّا يبدو.**
     - الدليل نفسه يقول «إعادةُ إنشاء لا reload» بعد git (`08-local-runbook.md:993`)، لأنّ الملفّين مربوطان ملفّاً ملفّاً. فلا يخدم `/-/reload` إلّا تعديلاً في مكان الملف أثناء تجربة حمل.
     - وهذا يؤدّيه `docker compose kill -s SIGHUP prometheus` أيضاً: Prometheus يعيد قراءة إعداده عند SIGHUP، بالقيد نفسه على الـinode، وبلا منفذ HTTP.
  4. **الصياغة:** واجهة الاستعلام متاحةٌ بلا `--web.enable-lifecycle`، والجملة تنسبها إليه.

### فحص الشيفرة الجديدة
- **`gpu-preflight.sh`:**
  - لا نداء خارجيّ جديد. و`version_ge` و`is_prerelease` دالّتان صرفتان.
  - الحساب `$((10#…))` لا يرى إلّا أرقاماً التقطها تعبيرٌ منتظم، فلا حقن حسابيّ (`a[$(…)]`).
  - المقارنة بلا `sort -V`، فلا اعتماد على أداةٍ إضافيّة في PATH.
  - الإغلاق عند الشكّ باقٍ:
    - محرّكٌ غير مقروء ← 11.
    - `nvidia-ctk` غائب ← 12 حتّى مع `--smoke` (قرار المالك؛ حارسه `test_gpu_preflight.py:410`).
    - `--smoke` لا يُشغَّل إلّا مع `ctk OK` (`:306-309`).
  - **تراجعٌ صغير ← SR‑17:** سطر FAIL لـ`ctk` صار يطبع بقيّة سطر الأداة كما هي (`:265`)، ومعها تسلسلات التحكّم. فما سجّلتُه في الجولة 1 («تسلسلات ANSI لا تُطبع») لم يعد صحيحاً في هذا المسار.
  - **ليس أمنيّاً، للمراجع والمالك:** مضيفٌ بلا كرت ومحرّكه أقدم من 26.0.0 يخرج الآن بـ11 («توقّف»)، لا بـ10 («المعالج ممكن»)، مع أنّ الملفّ الأساسيّ بلا شبكة `internal`.
- **`scrape-gpu.yml`:** مفاتيح إعدادٍ فقط، بلا مسار ولا سرّ. ولم يُضَف `honor_labels`، وافتراضه `false`، فلا يستطيع المصدّر انتحال التسميتين `job` و`instance`.
- **`docker-compose.gpu.yml`:** تعليقاتٌ فقط. والمفاتيح كما في الجولة 1. والتعديلات في الاختبارات القائمة أسماءٌ ونصوص توثيق.
- **الوثائق:** لا تعليمات تُضعف الأمان، وث‑7 وث‑8 وث‑9 صحيحة المضمون.

### الأسرار
- لا سرّ في الأسطر المضافة (241 سطراً). بحثتُ بأنماط AWS وGitHub وSlack و`sk-` و`hvs.` وJWT والمفاتيح الخاصّة و`dckr_pat_` و`hf_`، وبأنماط `password/secret/token/api_key=`، وDSN بكلمة مرور.
- والسلاسل الطويلة (≥ 32 حرفاً) كلّها مساراتٌ وروابط وأسماء اختبارات.

### الماسحات وما شُغّل
- **غير مثبّتة:** gitleaks وtrufflehog وcosign، كما في الجولتين السابقتين. وpip-audit وnpm audit لا ينطبقان، فلا تبعيّة جديدة.
- **ما شُغّل:**
  - `pytest` على `test_gpu_preflight.py` و`test_compose_gpu_overlay.py` و`test_monitoring_profile.py` و`test_observability_stack_wiring.py`، بلا `COMPOSE_FILE` ولا `COMPOSE_PROFILES`، وبلا cache ولا bytecode: ‏163 passed، ‏rc 0.
  - `deploy/prometheus/test-rules.sh`: ‏rc 0 (29 قاعدة، و`check config` بالوضعين).
  - `promtool check config` على نسخةٍ في scratch فيها مفتاحٌ مخطوء، في حاويةٍ بـ`--network none`.
  - ثنائيّ Prometheus v3.13.2 مستخرجاً من الصورة المثبّتة (`docker create` ثمّ `docker cp`)، على `127.0.0.1:29090` وبياناته في scratch. ومعه مصدّرٌ مزيّف وخادمٌ «ضحيّة» على 127.0.0.1. لم يلمس المكدّس الحيّ ولا شبكاته، وأُوقف كلّه بعد الفحص.
  - `gpu-preflight.sh` بأدواتٍ مزيّفة على PATH في scratch (`env -i`).
  - `markdown-it-py` على `deploy-server.md`.
  - قراءة `internal/app/app.go` و`internal/exporter/exporter.go` من المنبع (`v1.15.1`) لأسماء المقاييس، دون تشغيل.
- لم تُسحب صورة، ولم يُقرأ `.env`، ولم تُشغَّل `tests/integration`.

### ملاحظات الجولة 2
| # | الشدة | الفئة (OWASP/CWE) | الملف:السطر | الوصف | الإصلاح |
|---|---|---|---|---|---|
| SR‑15 | منخفض | A09:2021 Security Logging and Monitoring Failures؛ CWE‑345، CWE‑348 | `deploy/prometheus/scrape-gpu.yml:13-24` (لا `metric_relabel_configs`). والقواعد في `deploy/prometheus/alerts.yml`: ‏`:140` (DlqNotEmpty)، و`:542` (StreamBackstopHigh)، و`:634` (OpsTaskOverdue)، و`:679` (OpsTaskNeverArmed)، و`:749` و`:787` (ErrorBudgetBurn)، و`:861` (PgbouncerClientsWaiting)، و`:960` (RateLimitedShareHigh) | **الحدود تحدّ الحجم لا الأسماء.** يستطيع المصدّر أن يعرض سلاسل بأسماء التطبيق (`aizzak_*` و`pgbouncer_*`)، فيخزّنها Prometheus ولا يميّزها إلّا `job="nvidia-gpu"`. و15 قاعدةً من 29 تقرأ `aizzak_*` بلا مرشّح `job`. **السيناريو** (شرطه شرط SR‑9: صورة مصدّرٍ مخترَقة): (أ) يعرض `aizzak_ops_task_last_success_timestamp_seconds{task="backup"} 9e15`، فيأخذها `max by (task)`، ولا يشتعل `AizzakOpsTaskOverdue` والنسخ الاحتياطيّ متوقّف. (ب) عدّادٌ ضخمٌ متزايد لـ`aizzak_http_requests_total` يميّع مقام `AizzakRateLimitedShareHigh`، وهو أحد التنبيهات الأمنيّة الثلاثة (SR‑6)، ومقام تنبيهَي ميزانيّة الأخطاء. (ج) قيمةٌ سالبة لـ`aizzak_dlq_depth` أو `pgbouncer_pools_client_waiting_connections` تُلغي المجموع. (د) قيمةٌ ضخمة لـ`aizzak_stream_maxlen` تُخفي `AizzakStreamBackstopHigh`. وكلّ ذلك داخل الحدود ومع `up=1`، فلا يشتعل `AizzakScrapeTargetDown`، وهو أخفى من `/-/quit`. وبقيّة القواعد تقبل إنذاراتٍ كاذبة بالطريقة نفسها. **أثبتُّه حيّاً** على النسخة المحلّيّة نفسها، وبحدود الملفّ الحقيقيّة. خُزّنت السلسلة المحقونة. وعاد فارغاً تعبيرٌ بشكل `OpsTaskOverdue` (`time() - max by (task)(…) > 86400`)، بينما عاد التعبير نفسه، دون المهمّة المحقونة، بـ`backup` (864001 ث). **قائمٌ منذ الجولة 0، لا من هذه الالتزامات، وفاتني في الجولتين** | في `scrape-gpu.yml` بعد الحدود: `metric_relabel_configs: [{source_labels: [__name__], regex: "nvidia_smi_.*\|nvidia_gpu_exporter_.*\|go_.*\|process_.*\|promhttp_.*", action: keep}]`. هذه كلّ عائلات المصدّر في v1.15.1: ‏`exporter.DefaultPrefix = "nvidia_smi"`، و`clientversion.NewCollector("nvidia_gpu_exporter")`، وGo وprocess وpromhttp. **أثبتُّه على النسخة المحلّيّة:** أُسقطت `aizzak_*`، وبقيت `nvidia_smi_*`، و`up=1`. والقاعدة لا تمسّ `up` ولا `scrape_*`، لأنّهما لا يمرّان بإعادة الوسم. و`sample_limit` يُحسب بعد الإسقاط، و`body_size_limit` يبقى سقف المُسقَط. **ثمّ:** (1) يتحقّق الاختبار في `test_observability_stack_wiring.py:223` من وجود قاعدة `keep` على `__name__`، ومن أنّ تعبيرها لا يطابق `aizzak_` ولا `pgbouncer_`. (2) على الخادم في 7.3: يساوي `scrape_samples_post_metric_relabeling` ‏`scrape_samples_scraped` للمهمّة `nvidia-gpu`، فلم يسقط مقياسٌ حقيقيّ. (3) سطرٌ في ث‑7. وعلى المدى الأبعد (الموجة 8): مرشّح `job` في القواعد التي تجمع `aizzak_*` |
| SR‑16 | معلوماتيّ | A05:2021 Security Misconfiguration؛ CWE‑1059 | `docs/deploy-server.md:102` (ث‑7)؛ `docker-compose.yml:2666-2669` | سجلّ خطر الـlifecycle في ث‑7 ناقص. التفصيل في «قرار `--web.enable-lifecycle`» أعلاه: أثر المصدّر نفسه غير مذكور، ولا علاج مسمّى، والسبب يخدمه SIGHUP أيضاً | **(أ) نصّ:** يُضاف إلى «الأثر»: «ومن المصدّر: حلقة `POST /-/quit` توقف تقييم كلّ التنبيهات، والتسليم محلّيّ فلا يُلاحَظ غياب `AizzakWatchdog`». ويُضاف إلى «العلاج»: «`docker compose kill -s SIGHUP prometheus` بدل `POST /-/reload` ثمّ إزالة `--web.enable-lifecycle`؛ أو `--web.config.file` بمصادقة، ويحتاج حينها مصدر بيانات Grafana والفحص الصحّيّ اعتماداً». وتُصحَّح نسبة واجهة الاستعلام. **(ب) اختياريّ، بقرار المالك:** تطبيق SIGHUP الآن، وتعديل تعليق `docker-compose.yml:2666-2668` معه |
| SR‑17 | معلوماتيّ | CWE‑150 Improper Neutralization of Escape, Meta, or Control Sequences | `deploy/gpu-preflight.sh:265` (جديد: `${tail:+ ($tail)}`)؛ و`:203` و`:206` (`$engine`، قائمٌ قبل الفرق في `:160`) | سطرا FAIL لـ`ctk` و`docker` يطبعان نصّاً من ناتج الأداة كما هو. **جرّبتُه:** `1.17.8-rc1` تتبعه `\033[2J\033]0;title\007\033[32mOK`، فوصلت التسلسلات كلّها إلى الطرفيّة: مسح الشاشة، وعنوان النافذة، ولونٌ أخضر. **بلا أثرٍ أمنيّ:** المصدر ثنائيٌّ على PATH المشغِّل أو خادم Docker الذي يعمل root، ومن يتحكّم بهما يتحكّم بالمضيف. لكنّ الجولة 1 سجّلت «تسلسلات ANSI لا تُطبع»، ولم يعد هذا صحيحاً | `${tail//[^[:print:]]/?}` قبل الطباعة، أو «(pre-release)» بدل اللاحقة. وفي سطرَي `docker`: `$shown` بدل `$engine`، أو التنقية نفسها |

**العدد (الجولة 2):** حرج 0 · عالٍ 0 · متوسّط 0 · منخفض 1 (SR‑15) · معلوماتيّ 2 (SR‑16، SR‑17).

**المعلّق خارج الشيفرة (لا يحجب، كما في الجولة 1):** أمر `cosign verify` على 1.15.1 (المالك أو devops)، وإعادة قياس ذاكرة المصدّر على الخادم في 7.3 (BUG‑1).

---

## الجولة 3 (إعادة العمل، `fb4ccfd..16498e7`)

- **النطاق:** `git diff fb4ccfd..16498e7`، وفيه ثلاثة التزامات، و8 ملفّات، و89 سطراً مضافاً:
  - `7aa1844`: الفحص. تنقية تسلسلات التحكّم، ونصّ استثناء Engine، واختبار الإصدار التجريبيّ الأحدث.
  - `c576193`: حدود الكشط المقيسة، وقاعدة `keep` على اسم المقياس، واختباراتهما.
  - `16498e7`: وثائق: ث‑7 وملاحظات التصميم.
- **قُرئت كاملةً:**
  - `deploy/gpu-preflight.sh`، و`deploy/prometheus/scrape-gpu.yml` و`prometheus.yml`، وتعريف Prometheus في `docker-compose.yml:2641-2679`.
  - تعابير القواعد الـ29 في `alerts.yml` (استُخرجت أسماؤها آليّاً).
  - صفّ ث‑7 معروضاً، وفروق الاختبارين.
- **الحكم:** **PASS.**
- **لماذا PASS:**
  - SR‑15 عولج، ومُثبَت حيّاً بالمهمّة الحقيقيّة. ولا تصادم عبر بادئةٍ مُبقاة، ولا انتحال لـ`job` أو `instance`، ولا تجميد للاستيعاب بطابع وقتٍ مستقبليّ.
  - SR‑17 عولج في الأسطر الثلاثة المسمّاة. وبقيت خمسة صفوف أخرى تطبع ناتج الأدوات كما هو (SR‑18). ومصادرها كلّها جذريّة على المضيف، أو حاويةٌ تحجز الكرت أصلاً، فلا أثر أمنيّ.
  - SR‑16 عولج في المضمون، وبقيت فيه مسألة موضع (تفصيلها أدناه).
  - لا حرج ولا عالٍ ولا متوسّط ولا منخفض. ولا مسار تطبيقيّ جديد، فلا فحص صلاحيّات ناقص ولا فحص عزل مستأجرين ناقص.
  - الجديد ثلاث ملاحظاتٍ معلوماتيّة (SR‑18…SR‑20)، تُسجَّل متابعةً في وصف الـPR.

### حالة ملاحظات الجولة 2
| # | الحالة | الدليل |
|---|---|---|
| SR‑15 | ✅ عولج، ومُثبَت حيّاً | `scrape-gpu.yml:34-37`. شغّلتُ ثنائيّ v3.13.2 نفسه على 127.0.0.1، وبيانات جديدة لكلّ حالة. والإعداد مبنيّ آليّاً من مهمّة الملفّ الحقيقيّة: نُسخت المفاتيح السبعة، ولم يتغيّر غير اسم المهمّة والهدف والمسار. **(أ) هجومٌ مختلط:** خُزّنت `nvidia_smi_*`، و`nvidia_gpu_exporter_build_info`، و`go_*`، و`process_*`، و`promhttp_*`. وأُسقطت `aizzak_ops_task_last_success_timestamp_seconds{task="backup"} 9e15`، و`pgbouncer_pools_client_waiting_connections -1000`، و`up 0`، و`ALERTS{alertname="AizzakWatchdog"}`، و`scrape_samples_scraped 999`. وبقي `up` التركيبيّ 1. و`scrape_samples_scraped` كان 11 مقابل `scrape_samples_post_metric_relabeling` 6، فيكشف فحص 7.3 أيّ حقن. **(ب) حيل الاسم:** الاسم المقتبس بصيغة UTF‑8 `{"aizzak_dlq_depth",stream="q"}` أُسقط. وتسمية `__name__` ثانية، عاديّةً أو مقتبسة، تُرفض بالكشط كلّه: `label name "__name__" is not unique`، و`up=0`، ولا سلسلة مخزّنة. **(ج) التصادم عبر بادئةٍ مُبقاة:** `grep` على `go_` و`process_` في `alerts.yml` لا يجد شيئاً. واستخرجتُ آليّاً كلّ اسم مقياسٍ في القواعد الـ29 ولوحات Grafana. لا يطابق التعبيرَ إلّا ثلاثة أسماء: `nvidia_smi_memory_used_bytes` و`nvidia_smi_memory_total_bytes` و`nvidia_smi_temperature_gpu` (`alerts.yml:1189-1190,1216`)، وكلّها تُقرأ بـ`job="nvidia-gpu"`. ولا قاعدة ولا لوحة تقرأ `go_*` أو `process_*` أو `promhttp_*`، ولا مُحدِّد `__name__=~`. **(د) `honor_labels`:** غير مضبوط في أيّ ملفّ، وافتراضه `false`. أثبتُّه: `go_threads{job="aizzak-app",instance="app:8000"}` خُزّن بـ`exported_job` و`exported_instance`، وبقي `job="nvidia-gpu"` الحقيقيّ. **(هـ) `honor_timestamps`:** غير مضبوط، وافتراضه `true`، فالمصدّر يختار طابع الوقت لسلاسله المُبقاة. فحصتُ هل يجمّد طابعٌ مستقبليّ استيعاب كلّ المهامّ. طابع +600 ث قُبل، وتقدّم `prometheus_tsdb_head_max_time` عشر دقائق فقط، وظلّت المهمّة الجارة تُستوعب. وطوابع +3540 ث و+7200 ث و+يوم رُفضت (`too far into the future`، مع زيادة `prometheus_target_scrapes_sample_out_of_bounds_total`). فلا تجميد، ولا ملاحظة. و`honor_timestamps: false` تقسيةٌ اختياريّة. **حارسه** `test_observability_stack_wiring.py:250-260`، وهو ضيّق (← SR‑19). وث‑7 و`server-gpu-plan.md:134` يحملان فحص 7.3 |
| SR‑16 | ✅ عولج في المضمون؛ وبقيت مسألة موضع | `deploy-server.md:102`. عرضتُ الملفّ بـ`markdown-it-py`: الجدول سليم، 9 صفوف في كلٍّ منها 4 خانات. والمطلوب موجود: (1) «**الأثر من المصدّر:** حلقة `POST /-/quit` توقف تقييم كلّ التنبيهات…». (2) «**العلاج (الموجة 8):** `docker compose kill -s SIGHUP prometheus`… ثمّ إزالة `--web.enable-lifecycle`؛ أو `--web.config.file` بمصادقة…». (3) «واجهة الاستعلام مفتوحة له دائماً (لا تتعلّق بالـlifecycle)». وقرار المالك (2026‑10‑18) مسجّل. وكلمة «حلقة» دقيقة: `restart: unless-stopped` (`docker-compose.yml:2678`) يعيد Prometheus بعد `/-/quit`، فطلبٌ واحد انقطاعٌ قصير، والحلقة هي ما يُبقيه متوقّفاً. **مسألة الموضع:** النصّان في خانة «الثغرة» تحت وسمين عريضين، لا في خانتَي «الأثر» و«العلاج المقترح». وهاتان الخانتان ما زالتا تذكران بنود الموجة 8 وحدها، وهي لا تغلق `/-/quit`، فمن يقرأ عمود العلاج وحده يفوته البند. **متابعة اختياريّة:** نقل الجملتين إلى خانتيهما |
| SR‑17 | ✅ عولج في الأسطر الثلاثة المسمّاة؛ وبقيت صفوفٌ غيرها (← SR‑18) | الأسطر الثلاثة: `gpu-preflight.sh:206-207` (`clean`)، و`:210` (`$shown`، وفيه أرقامٌ فقط)، و`:269-270` (`tail` منقّى). جرّبتُها بأدوات الاختبار المزيّفة وبالتسلسل `ESC[2J ESC]0;… BEL ESC[32m`، فخرجت نظيفة، وكذلك سطرا OK لـ`docker` و`ctk` حين تتبع الرقمَ لاحقة. والتنقية تستبدل محارف التحكّم C0 وC1 (ومنها U+009B) في المحلّيّتين C وC.UTF-8. ولا يمرّ في UTF‑8 إلّا U+202E (عكس الاتّجاه)، ولا أثر له في الطرفيّة، فلا إجراء. حارسه `test_gpu_preflight.py:592` (3 حالات). **لكنّ خمسة صفوفٍ أخرى ما زالت تطبع الناتج كما هو ← SR‑18** |

### فحص الالتزامات الثلاثة (هل من ضعفٍ جديد؟)
- **`7aa1844`:**
  - التنقية توسيعٌ داخل bash، بلا أداةٍ خارجيّة ولا `eval`. ولا تتغيّر رموز الخروج (167 اختباراً خضراء).
  - `29.0.0-rc.1` يُقبل (`test_gpu_preflight.py:609`). هو إصدارٌ تجريبيّ لما بعد الحدّ، وفيه إصلاح CVE‑2024‑29018 (أُصلح في 26.0.0). سليم.
  - نصّ استثناء Engine (`gpu-preflight.sh:47-50`، `deploy-server.md:72`) يطابق الشيفرة: إن لم يُضبط `DOCKER_OK` كان الرمز 11 أوّلاً.
- **`c576193`:**
  - **قاعدة `keep` تسبق `sample_limit` وحدود التسميات.** أثبتُّه: 6001 سلسلة `aizzak_junk` ← `up=1`، و`scraped` 6001، و`post` 1، ولا شيء مخزّن. فلا ضرر، لأنّ شيئاً لا يُخزَّن، و`body_size_limit` (2MB، مُثبَت في الجولة 2) يحدّ كلفة التحليل. لكنّ محاولة الحقن صارت صامتة ← SR‑20.
  - `sample_limit` (5000) فوق السقف المحسوب 1768. وبقيّة الحدود لم تتغيّر قيمها، والتغيير فيها تعليقات.
  - اختبار الحارس ← SR‑19.
- **`16498e7`:** وثائق وتعليق. ورأس `docker-compose.gpu.yml:12-13` يطابق `:165-169` (`internal: true`). ولا تعليمة تُضعف الأمان.

### الأسرار
- لا سرّ في الأسطر المضافة (89 سطراً). بحثتُ بالأنماط نفسها التي في الجولة 2: AWS، وGitHub، وSlack، و`sk-`، و`hvs.`، وJWT، والمفاتيح الخاصّة، و`dckr_pat_`، و`hf_`، و`password/secret/token/api_key=`، وDSN بكلمة مرور.
- والسلاسل الطويلة (≥ 32 حرفاً) أسماء مقاييس واختبار ومسار.

### الماسحات وما شُغّل
- **غير مثبّتة:** gitleaks، وtrufflehog، وcosign، وpip-audit. ولا تبعيّة جديدة، فلا ينطبق `pip-audit` ولا `npm audit`.
- **ما شُغّل:**
  - `pytest` على `test_gpu_preflight.py` و`test_observability_stack_wiring.py` و`test_compose_gpu_overlay.py` و`test_monitoring_profile.py`، بلا `COMPOSE_FILE` ولا `COMPOSE_PROFILES`، وبلا cache ولا bytecode: ‏167 passed، ‏rc 0.
  - `deploy/prometheus/test-rules.sh`: ‏rc 0 (29 قاعدة، و`check config` بالوضعين).
  - ثنائيّ Prometheus v3.13.2 نفسه من الجولة 2 (في scratch)، على `127.0.0.1:29190`، ومعه مصدّرٌ مزيّف على `127.0.0.1:29191`. الحالات: الهجوم المختلط، و`__name__` المكرّر، والاسم المقتبس، و6001 سلسلة تُسقطها القاعدة، وطوابع الوقت +600 و+3540 و+7200 و+86400 ث. أُوقف كلّه، وتحقّقتُ أنّ المنفذين مغلقان. لم يُلمس المكدّس الحيّ ولا شبكاته.
  - `gpu-preflight.sh` بأدوات الاختبار المزيّفة (`_run` من `test_gpu_preflight.py`، وPATH معزول)، والتسلسلات في عشرة صفوف.
  - استخراج أسماء المقاييس من `alerts.yml` واللوحات، ومحاكاة تعبيرٍ مُرخًى على تأكيدات الحارس.
  - `markdown-it-py` على `deploy-server.md`.
- لم تُسحب صورة، ولم يُقرأ `.env`، ولم تُشغَّل `tests/integration`.

### ملاحظات الجولة 3
| # | الشدة | الفئة (OWASP/CWE) | الملف:السطر | الوصف | الإصلاح |
|---|---|---|---|---|---|
| SR‑18 | معلوماتيّ | CWE‑150 Improper Neutralization of Escape, Meta, or Control Sequences | `deploy/gpu-preflight.sh:172` و`:175` (اسم الكرت من `nvidia-smi`)؛ و`:192` (`first_line "$ERR"` من `docker info`)؛ و`:322` و`:325` (ناتج حاوية `--smoke`)؛ و`:343` (رسالة `docker run`، ومنها أخطاء السحب) | **بقيّة SR‑17:** نقّى العلاج الأسطر الثلاثة المسمّاة وحدها. **جرّبتُه** بأدوات الاختبار المزيّفة وبالتسلسل `ESC[2J ESC]0;… BEL ESC[32m`. فوصل ESC وBEL إلى الطرفيّة من الصفوف الخمسة (برموز الخروج 0 و11 و0 و14)، وخرجت الصفوف الأخرى نظيفة. **بلا أثرٍ أمنيّ:** المصادر ثنائيّاتٌ على PATH المشغِّل، أو خادم Docker الجذريّ، أو صورة `--smoke` التي تعمل أصلاً بـ`--gpus all`. ووسم تلك الصورة غير مثبّتٍ بالبصمة (`:66`، والبصمة تعليق)، وهذا قائمٌ قبل الفرق. **أقصى الأثر خداع العرض.** مثلاً `ESC[8m` (الإخفاء) يبقى سارياً على ما بعده، ومنه سطر الحكم، لأنّ السكربت لا يعيد ضبط الطرفيّة. ورمز الخروج يبقى صحيحاً. ولم يعد اسم الاختبار «never reach the terminal» صادقاً إلّا للصفوف الثلاثة | **نقطةٌ واحدة** تغطّي الصفوف الحاليّة والقادمة: التنقية داخل `row()` (`:102-104`)، أي `printf 'gpu-preflight: %-8s %-5s %s\n' "$1" "$2" "${3//[^[:print:]]/?}"`. ويُوسَّع `test_gpu_preflight.py:592` بأربع حالات: `FAKE_SMI_GPUS`، و`FAKE_INFO_ERR` مع `FAKE_INFO_RC=1`، و`FAKE_RUN_OUT`، و`FAKE_RUN_ERR` مع `FAKE_RUN_RC` (الأخيرتان مع `--smoke`) |
| SR‑19 | معلوماتيّ | A09:2021 Security Logging and Monitoring Failures؛ CWE‑693 Protection Mechanism Failure (حارس SR‑15) | `tests/unit/test_observability_stack_wiring.py:250-260` | **الحارس يفحص اسمين بعينهما فقط.** يتأكّد أنّ تعبير `keep` لا يطابق `aizzak_ops_task_last_success_timestamp_seconds` ولا `pgbouncer_pools_client_waiting_connections`، ولا شيء غيرهما. **جرّبتُه:** التعبير المُرخى `…\|promhttp_.*\|.*_total` يجتاز التأكيدات الأربع، ويعيد قابليّة الحقن لستّ قواعد: `AizzakRateLimitedShareHigh` (`aizzak_http_requests_total` و`aizzak_rate_limit_rejections_total`)، و`AizzakApiErrorBudgetBurnFast` و`…Slow`، و`AizzakRedisStreamEvicted` (`redis_evicted_keys_total`)، و`AizzakVectorShadowWrites`. ولا يلتقط أيضاً قاعدةً قادمة تقرأ `go_*` أو `process_*` بلا `job` | يستخرج الاختبار أسماء المقاييس من تعابير `alerts.yml` (واللوحات) آليّاً، ويتأكّد أنّ كلّ اسمٍ يطابق تعبير `keep` لا يُقرأ إلّا بـ`job="nvidia-gpu"`. وهذا يلتقط القواعد القادمة أيضاً. **أو أبسط منه:** تثبيت التعبير حرفيّاً في الاختبار، فيمرّ كلّ إرخاءٍ بالمراجعة |
| SR‑20 | معلوماتيّ | A09:2021 Security Logging and Monitoring Failures؛ CWE‑778 Insufficient Logging | `deploy/prometheus/scrape-gpu.yml:13-14` و`:19` و`:34-37`؛ ولا قاعدة في `alerts.yml` | **محاولة الحقن صارت صامتة.** `sample_limit` وحدود التسميات تُحسب بعد قاعدة `keep`. أثبتُّه: 6001 سلسلة `aizzak_junk` (فوق 5000) ← `up=1`، و`scrape_samples_scraped` 6001، و`scrape_samples_post_metric_relabeling` 1، ولا شيء مخزّن. فلا ضرر. لكن قبل القاعدة كانت 5001 سلسلة بأيّ اسم تُسقط الكشط، فيصير `up=0` ويشتعل `AizzakScrapeTargetDown`. أمّا الآن فلا يكشف المصدّرَ المخترَق الذي يجرّب الحقن إلّا الفرقُ بين المقياسين، ولا قاعدة تقرؤه، وفحص 7.3 يجري مرّةً واحدة. وتعليق `:13-14` («Going over a limit drops the WHOLE scrape») لا يصدق إلّا على العيّنات المُبقاة | (1) تعليقٌ في `:13-14`: «the limits count only the samples the keep rule passes». (2) **اختياريّ، فخٌّ دائم:** قاعدة `scrape_samples_scraped{job="nvidia-gpu"} - scrape_samples_post_metric_relabeling{job="nvidia-gpu"} > 0` بـ`for: 5m`، مع قسمٍ لها في `docs/runbooks/alerts.md` (`CLAUDE.md`) واختبارٍ في `alerts.test.yml`. وهي تشتعل أيضاً إن أضاف إصدارٌ جديدٌ للمصدّر عائلةً خارج القائمة، وهذا مفيد: يعني أنّ قائمة `keep` تحتاج مراجعة |

**العدد (الجولة 3):** حرج 0 · عالٍ 0 · متوسّط 0 · منخفض 0 · معلوماتيّ 3 (SR‑18، SR‑19، SR‑20). وفي SR‑16 مسألة موضعٍ باقية.

**متابعةٌ لوصف الـPR (لا تحجب):**
- SR‑18 وSR‑19 وSR‑20.
- نقل الأثر والعلاج في ث‑7 إلى خانتيهما (SR‑16).
- `honor_timestamps: false` في `scrape-gpu.yml`، تقسيةً اختياريّة.

**المعلّق خارج الشيفرة (كما في الجولة 2):**
- أمر `cosign verify` على 1.15.1 (المالك أو devops).
- إعادة قياس ذاكرة المصدّر على الخادم في 7.3 (BUG‑1).
- فحص `scrape_samples_post_metric_relabeling == scrape_samples_scraped` للمهمّة `nvidia-gpu` على الخادم في 7.3.

</div>
