<div dir="rtl">

# المراجعة الأمنية: المراقبة اختياريّة وأرقام الكرت (`monitoring-profile`، المرحلة 4 من خطّة خادم الكرت)

- **النطاق المراجَع:** `git diff master...feat/monitoring-profile` (`47225ff…3880ff9`، 9 التزامات، 35 ملفّاً).
  - قُرئت كاملةً: `docker-compose.gpu.yml`، و`deploy/prometheus/{prometheus.yml,scrape-gpu.yml,alerts.yml,test-rules.sh}`، و`deploy/resource-budget.sh`، و`tests/unit/test_compose_gpu_overlay.py`.
  - وقُرئ الفرق مع سياقه: `docker-compose.yml` (أسطر `profiles` فقط، مع تعريفات الخدمات الإحدى عشرة)، و`.env.example` (تعليقات فقط)، وصفّ GPU في `deploy/grafana/dashboards/aizzak-capacity.json`، والوثائق المعدّلة.
- **المرجع:** `requirements.md` (NFR‑10)، و`design.md` §3‑ج و§3‑د، و`test-plan.md`، و`status.md` (Q‑4، Q‑8، قرار 7)، و`CLAUDE.md` (قاعدة الـstaging)، و`docs/server-gpu-plan.md` §3.
- **الحكم:** **PASS**، بشرط: تُسجَّل ث‑7 وث‑8 وث‑9 المقترحة، ويُعدَّل علاج ث‑6 (أسفل الوثيقة)، قبل الدمج أو معه.

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

</div>
