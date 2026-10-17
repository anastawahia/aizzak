<div dir="rtl">

# المتطلبات: المراقبة اختياريّة وأرقام الكرت (المرحلة 4 من خطّة خادم الكرت)

> **المصدر:** طلب المالك (2026‑10‑17) «ابدأ تنفيذ المرحلة الرابعة»، و[`docs/server-gpu-plan.md`](../../server-gpu-plan.md):
> - §5 «المرحلة 4» (الأسطر 301–316).
> - اللوحة §4 (129–130).
> - القواعد §3 (91–102).
> - **قرار 7** في §6 (453).
> - صفّ «المراقبة إجباريّة» في §1.1 (61).
>
> والقصص ومعايير القبول في [`stories.md`](stories.md).

## 1. الخلفية والهدف

اليوم تعمل إحدى عشرة خدمة مراقبة مع كلّ `docker compose up -d`، بلا profile:
- Prometheus وGrafana وAlertmanager و`alert-sink` وLoki وAlloy.
- وخمسة مصدّرات.

حدودها **3.60 معالج و4.66 ج.ب** بأرقام اليوم، أي نحو 8٪ من ذاكرة المكدّس (`server-gpu-plan.md:61`). على خادمٍ صغير هذا كثير. والخطّة تريدها **اختياريّة ومفعّلة افتراضيّاً**، ولا تُطفأ إلّا على الخادم الصغير (قرار 7، `:453`).

وعلى خادمٍ فيه كرت، لا يرى أحدٌ ذاكرة الكرت ولا حرارته. إن امتلأت ذاكرة الكرت رفض Ollama تحميل النموذج، أو نقله إلى المعالج فأبطأ كثيراً (`:240`)، ولا تنبيه يقول ذلك.

**المستفيدون:**
- مشغّل الخادم: يطفئ المراقبة على خادمٍ صغير، ويرى الكرت على خادمٍ كبير.
- المالك: جهازه لا يتغيّر.
- لاحقاً `install.sh` (5.2) وفحص الحزمة في CI (7.2)، وكلاهما يبني على الـprofile.

## 2. النطاق

**داخل النطاق (الخطوتان 4.1 و4.2):**
- **4.1:**
  - `profiles: ["monitoring"]` على الخدمات الإحدى عشرة في `docker-compose.yml`، ولا تغيير آخر فيها.
  - حارسٌ يمنع أيّ خدمةٍ أساسيّة من الاعتماد عليها.
- **الاختبارات التي تفترض أنّ المراقبة دائمة** تتعلّم الـprofile في الالتزام نفسه (القاعدة 3).
- **ميزانيّة الموارد** تعدّ المراقبة مفعّلةً افتراضيّاً، وتستطيع الحكم بدونها:
  - `deploy/resource-budget.sh`.
  - `deploy/auto_size.py` واختباراته.
  - `test_resource_budget.py`.
  - دفتر `08 §2‑ز`.
- **انتقال جهاز المالك دون تغيّرٍ في سلوكه** (القاعدة 1)، وتوثيق السطر:
  - `stack-commands.md` و`quickstart.md`.
  - تعليقات `.env.example`.
  - `runbooks/alerts.md` §0.
  - `deploy-server.md`.
- **4.2:**
  - مصدّر أرقام NVIDIA بوسمٍ ثابت في `docker-compose.gpu.yml` تحت profile `monitoring`.
  - هدفٌ له في Prometheus لا يُشعل `AizzakScrapeTargetDown` على جهازٍ بلا ملفّ الكرت.
  - تنبيهان: امتلاء ذاكرة الكرت، وحرارته.
  - لكلّ تنبيه: حالاته في `alerts.test.yml`، وقسمٌ في `docs/runbooks/alerts.md`.
  - لوحة Grafana لذاكرة الكرت وحمله.
- **تحديث لوحة الخطّة** (§4، الصفّان 4.1 و4.2) و`docs/server-gpu-summary.html`.

**خارج النطاق:**
- نسخة RunPod (`deploy/runpod/`).
- شيفرة التطبيق (`src/`)، ومنها `GET /metrics` (`:307`).
- **المراحل 5–7:**
  - `install.sh` وطباعته «المراقبة مطفأة» (`:310`، `:346`).
  - الحزمة (5.1).
  - مصفوفة CI «مع المراقبة/دونها» (7.2، `:418`).
  - التجربة على خادم (7.3).
  - الدليل الكامل (7.4).
- الموجة 8 (تصليب الإنتاج) وقرار الإطلاق.
- قناة تنبيهٍ خارجيّة. التوصيل محلّيٌّ بقرار المالك (2026‑10‑04، `docs/runbooks/alerts.md:13`).
- مصدّر الكرت في ملفّ WSL (`docker-compose.wsl-gpu.yml`). جهاز المالك يبقى على ملفّ WSL (`docs/delivery/gpu-compose/status.md:99`).
- `cadvisor`: يبقى خلف `container-metrics` وحده (`docker-compose.yml:3035`).
- **إعادة اشتقاق أرقام `small` و`medium`**، ومنها «تخفيف `small` بعد 4.1» (`docs/delivery/compose-sizes/status.md:38`). انظر Q‑6.
- ميزانيّة الاتّصالات (`test_connection_budget.py`): تعدّ جلسات المصدّرات ثوابت، فعدّها والمراقبة مطفأة زيادةٌ آمنة (A‑2).
- أيّ تعديلٍ في حجم احتفاظ Prometheus أو Loki.

## 3. المتطلبات الوظيفية

| المعرّف | المتطلب | المصدر |
|---|---|---|
| FR‑1 | الخدمات الإحدى عشرة تحمل `profiles: ["monitoring"]`، ولا يتغيّر فيها شيءٌ آخر. وهي: `prometheus` و`grafana` و`alertmanager` و`alert-sink` و`loki` و`alloy` و`pgbouncer-exporter` و`redis-stream-exporter` و`redis-cache-exporter` و`node-exporter` و`postgres-exporter`. ولا تحمل أيّ خدمةٍ أخرى هذا الـprofile | `server-gpu-plan.md:306` |
| FR‑2 | لا خدمة خارج الإحدى عشرة تعتمد (`depends_on`) على واحدةٍ منها، ولا تذكر اسم مضيفها في بيئتها أو أمرها. وحارسٌ آليّ يمنع ذلك مستقبلاً | `:307` |
| FR‑3 | `GET /metrics` في التطبيق، وحجب nginx له، يبقيان كما هما | `:307`، `deploy/nginx/app-locations.conf:158-169` |
| FR‑4 | كلّ اختبارٍ يفترض أنّ المراقبة دائمة يتعلّم الـprofile، في الالتزام نفسه | `:309`، القاعدة 3 (`:96`) |
| FR‑5 | ميزانيّة الموارد تعدّ `monitoring` مفعّلاً افتراضيّاً (قرار 7)، فلا يتغيّر بـ4.1 أيّ رقم في الدفتر ولا في جدول الأحجام | `:453`، `:308`، `docs/design/08-local-runbook.md:478-479,543-547` |
| FR‑6 | طريقةٌ صريحة لحكم الميزانيّة **دون** المراقبة، للخادم الصغير. وتقول في ناتجها إنّ المراقبة مطفأة | `:453`، `docs/delivery/compose-sizes/status.md:38` (Q‑3) |
| FR‑7 | `auto_size.py` و`resource-budget.sh --block` يتبعان الـprofiles كما يراها Compose، كما اليوم. و`monitoring` صار profile معروفاً | `08-local-runbook.md:566`، `deploy/auto_size.py:604-633`، `deploy/resource-budget.sh:222-239` |
| FR‑8 | جهاز المالك يُبقي الخدمات الإحدى عشرة تعمل بعد الدمج، دون إعادة إنشاء أيّ حاوية (Q‑2) | القاعدة 1 (`:93`)، `:308` |
| FR‑9 | الوثائق تقول ثلاثة أشياء: سطر المالك وسطر الخادم، وأنّ `--profile X` يحلّ محلّ `COMPOSE_PROFILES`، وأنّ **دون المراقبة لا تنبيهات**، مع فحص `/health` من الخارج | `:310`، `docs/stack-commands.md:117-122` |
| FR‑10 | لوحة الخطّة §4 و`server-gpu-summary.html` تُحدَّثان | `server-gpu-plan.md:23` |
| FR‑11 | مصدّر أرقام NVIDIA في `docker-compose.gpu.yml` تحت profile `monitoring`: وسمٌ ثابت، و`expose` فقط، وحدود، وhealthcheck | `:315`، القاعدة 5 (`:98`) |
| FR‑12 | هدفٌ للمصدّر في Prometheus: على جهازٍ **بلا** ملفّ الكرت لا يُشعل أيّ تنبيه، وعلى جهازٍ **به** يغطّيه `AizzakScrapeTargetDown` كأيّ هدفٍ عاديّ | `:315`، `deploy/prometheus/alerts.yml:303-305` |
| FR‑13 | ميزانيّة وضع الكرت تعدّ المصدّر، ويبقى كلّ حجمٍ تحت 90٪ من خادمه | `08-local-runbook.md:541-547`، `tests/unit/test_resource_budget.py:429-432` |
| FR‑14 | تنبيهان: امتلاء ذاكرة الكرت، وحرارته، بحدودٍ وأزمنة `for:` مكتوبة (Q‑5) | `:315` |
| FR‑15 | لكلّ تنبيهٍ ثلاثة أشياء: الحقول الخمسة، وحالةٌ تُشعله وحالةٌ تُسكته في `alerts.test.yml`، وقسمٌ في `docs/runbooks/alerts.md` وسطرٌ في فهرسه. ثمّ `test-rules.sh` ينجح | `:316`، القاعدة 7 (`:100`)، `deploy/prometheus/alerts.yml:33-43` |
| FR‑16 | لوحة Grafana تعرض ذاكرة الكرت وحمله (وحرارته) | `:316` |

## 4. المتطلبات غير الوظيفية

| المعرّف | المتطلب | معيار القياس |
|---|---|---|
| NFR‑1 | جهاز المالك لا يتغيّر سلوكه (القاعدة 1) | بالسطر الجديد في `.env` (Q‑2)، تتساوى بين `master` والدمج: قائمة `docker compose config --services`، وبصمة `config --hash` لكلٍّ من الإحدى عشرة. و`up -d` لا يعيد إنشاء أيٍّ منها. **ثبت** على Compose v5.3.1 أنّ إضافة `profiles:` لا تغيّر البصمة (§5.6) |
| NFR‑2 | الأرقام لا تتحرّك بـ4.1 | `resource-budget.sh --host-cpus 32 --host-memory-gb 64` يطبع **35.60 vCPU و57.28 GB**. و`--size large\|medium\|small` يطبع 37.60/57.53 و19.35/28.41 و11.50/14.22، كما على `master`. وبعد 4.2 لا يتغيّر إلّا وضع الكرت، بحدّ المصدّر وحده |
| NFR‑3 | لا مكدّس حيّ لأيّ معيار Must | كلّ معيار Must يعمل دون تشغيل حاوية للمكدّس. والمعايير [config]: <br>• تمرّر `--env-file .env.example`. <br>• تحذف `COMPOSE_FILE` و`COMPOSE_PROFILES` من بيئتها، لأنّ وظيفة `integration` تصدّر `COMPOSE_FILE` بثلاثة ملفّات (`.github/workflows/ci.yml:95`)، وهو الدرس الذي سجّلته `compose-auto-size` (`status.md:41`). <br>• تتخطّى إن غاب Docker |
| NFR‑4 | لا أسرار | لا معيار يطبع ناتج `docker compose config` على `.env` الحقيقيّ. معايير [مالك] تطبع بصماتٍ وأعداداً فقط |
| NFR‑5 | البوّابات الستّ | البوّابات الخمس خضراء، ومعها `deploy/prometheus/test-rules.sh`، لأنّ التنبيهات تتغيّر |
| NFR‑6 | صورٌ بوسومٍ ثابتة | صورة المصدّر بلا `latest` ولا `dev`، ووسمها موجودٌ في سجلّه فعلاً. `test_gpu_compose_hygiene.py` يفحص الملفّين (`:24`) |
| NFR‑7 | كلفة الاختبارات | الاختبارات الجديدة والمعدّلة تزيد زمن `pytest tests/unit` ≤ 15 ث على هذا الجهاز (سابقة `compose-sizes`)، بالقياس بـ`--durations` قبل وبعد |
| NFR‑8 | ذاكرة المصدّر تتّسع في `large` | هامش `large` بوضع الكرت **70.4 م.ب** (57.53 من 57.60 ج.ب، §5.3). فحدّ المصدّر ≤ `64m`، أو يُخفض حدٌّ آخر في `large.env` بقرار (Q‑8). وذروته المقيسة × 1.5 ≤ حدّه، ويُقاس محلّيّاً (Q‑10) أو في 7.3 |
| NFR‑9 | زمن التنبيه | كلّ تنبيهٍ يشتعل خلال `for:` + 30 ث من تجاوز الحدّ (فترتا كشطٍ وتقييم بـ15 ث، `deploy/prometheus/prometheus.yml:29-31`)، ويُثبت ذلك بـ`eval_time` في `alerts.test.yml` |
| NFR‑10 | سطح الأمان | المصدّر بلا `ports`، وبلا `docker.sock`، وبلا ربط جذر المضيف. وأيّ صلاحيةٍ فوق `utility`، أو صورة طرفٍ ثالث، تُسجَّل في قائمة الأمان في `docs/deploy-server.md` (القاعدة 8، `:101`) ولا تحجب |

## 5. الوضع الحالي في الشيفرة

### 5.1 الخدمات الإحدى عشرة واعتماداتها

**الإحدى عشرة كلّها بلا `profiles:`.** وأسطرها في `docker-compose.yml`:

| الخدمة | السطر |
|---|---|
| `prometheus` | `:2631` |
| `grafana` | `:2683` |
| `alertmanager` | `:2732` |
| `alert-sink` | `:2772` |
| `pgbouncer-exporter` | `:2812` |
| `redis-stream-exporter` | `:2866` |
| `redis-cache-exporter` | `:2889` |
| `node-exporter` | `:2925` |
| `postgres-exporter` | `:2977` |
| `loki` | `:3070` |
| `alloy` | `:3101` |

- **مجموع حدودها بأرقام اليوم 3.60 vCPU و4768 م.ب (4.66 ج.ب)**، وهو رقم الخطّة (`:61`). حُسب من الملفّ ومن `deploy/sizes/*.env`:

  | الحجم | المعالج | الذاكرة |
  |---|---|---|
  | `large` | 3.60 | 4768 م.ب |
  | `medium` | 2.35 | 2464 م.ب |
  | `small` | 1.40 | 1696 م.ب |

- **`cadvisor`** خدمة مراقبةٍ ثانية عشرة، لكنّها خلف `container-metrics` أصلاً (`:3035`)، وليست من الإحدى عشرة.
- **دعوى الخطّة «الاعتمادات بينها فقط» (`:307`):**
  - **صحيحةٌ في ما يهمّ:** لا خدمة أساسيّة تعتمد على أيٍّ من الإحدى عشرة. فُحصت حوافّ `depends_on` كلّها.
  - **والحوافّ الداخليّة الثلاث كما قالت:**
    - `grafana` ← `prometheus` (`:2690-2692`).
    - `alertmanager` ← `alert-sink` (`:2743-2745`).
    - `alloy` ← `loki` (`:3108-3110`).
  - **لكنّها ناقصة.** فهناك **خمس حوافّ خارجة** من المراقبة إلى الأساس، لم تذكرها:
    - `prometheus` ← `app` (`:2638-2640`).
    - `pgbouncer-exporter` ← `pgbouncer` (`:2819-2821`).
    - `redis-stream-exporter` ← `redis-stream` (`:2873-2875`).
    - `redis-cache-exporter` ← `redis-cache` (`:2896-2898`).
    - `postgres-exporter` ← `postgres` (`:2988-2990`).
  - هذه الحوافّ جائزة: Compose يقبل خدمةً بـprofile تعتمد على خدمةٍ بلا profile. وأثرها أنّ `--profile monitoring up -d prometheus` يُقلع `app` أيضاً.
- **لا اعتماد وقت التشغيل:**
  - nginx لا يمرّر Grafana ولا Prometheus، وفيه `/metrics` وحده محجوباً (`deploy/nginx/app-locations.conf:158-169`).
  - لا ذكر لـ`prometheus:9090` أو `loki:3100` أو `alertmanager:9093` أو `grafana:3000` في `src/` ولا في `deploy/nginx/*.conf`.
  - السجلّات بمشغّل `json-file` لا بمشغّل Loki (`docker-compose.yml:280-283`)، فغياب Loki لا يمنع حاويةً من الإقلاع.
- **سكربتات بلا اعتماد على المراقبة:**
  - `deploy/rolling-deploy.sh` يعمل على خدمةٍ مسمّاة بـ`--no-deps` (`:238`، `:308`).
  - `deploy/smoke/` و`deploy/load/*.sh` لا يقرآن Prometheus.
  - وظيفة `integration` في CI لا تُقلع إلّا البنية المسمّاة (`.github/workflows/ci.yml:114-117`).

### 5.2 ما يفترض اليوم أنّ المراقبة دائمة (يتغيّر في الالتزام نفسه، القاعدة 3)

| الملفّ:السطر | الافتراض | يتأثّر بـ |
|---|---|---|
| `tests/unit/test_observability_stack_wiring.py:270-289` | `tier: optional` = **كلّ** خدمةٍ مكشوطة خلف أيّ profile. بعد 4.1 تصير ثمانية مصدّراتٍ عاديّة «خلف profile»، فيفشل. والقاعدة الصحيحة: «خلف profile غير `monitoring`»، لأنّ Prometheus نفسه في الـprofile ذاته | 4.1 |
| `tests/unit/test_monitoring_host_postgres_acceptance.py:216-236` | يرسم `config` **دون** `--profile`، ثمّ يقرأ `services["node-exporter"]`، ويؤكّد `"profiles" not in svc` (`:227`). بعد 4.1: `KeyError`، ثمّ تعارضٌ مباشر | 4.1 |
| `tests/unit/test_compose_local_llm.py:352-358` | المجموعة الافتراضيّة 33 = «28 دائمة + 5 لمرّة». بعد 4.1 تصير 22 = 17 + 5 | 4.1 |
| `tests/unit/test_compose_local_llm.py:364-367` | `.env.example` فيه حرفيّاً `#COMPOSE_PROFILES=wsl-ollama` و`#COMPOSE_PROFILES=local-llm` | 4.1 (إن تغيّرت التعليقات) |
| `tests/unit/test_resource_budget.py:338-359`، `:389-391` | الدفتر 35.60/57.28، والعدد 28 دائمة و5 لمرّة و6 بـprofile. `_classify` يعدّ أيّ profile غير مفعّل خارج المجموع (`:99-116`) | 4.1 |
| `tests/unit/test_resource_budget.py:402-475` | جداول الأحجام، و`small` ≤ 90٪ | 4.1، 4.2 |
| `tests/unit/test_auto_size.py:846-852`، `:866-881` | أرقام 11.72/11.40/0.32 و23.22/1.83x محسوبةٌ بـ`profiles="wsl-ollama"` والمراقبة دائمة. بعد 4.1 دون `monitoring` تنقص | 4.1 |
| `tests/unit/test_observability_stack_wiring.py:201-214` | كلّ هدف كشطٍ اسمُ خدمةٍ في `docker-compose.yml` وحده | 4.2 |
| `tests/unit/test_compose_gpu_overlay.py:89-92`، `:182-188` | ملفّ الكرت فيه ثلاث خدماتٍ بالضبط، ولا يغيّر غيرها | 4.2 |
| `tests/unit/test_prometheus_alert_rules.py:88`، `:169-255` | `EXPECTED_ALERTS` سبعٌ وعشرون، والثامنة والعشرون تحتاج سبباً مكتوباً: «`docs/log/` أو خطوةٌ مسمّاة في `capacity-plan.md`» | 4.2 |
| `tests/unit/test_healthcheck_coverage.py:36`، `:99-111` | **لا يتأثّر بـ4.1:** لا يعرف الـprofiles. لكنّه يقرأ الملفّ الأساسيّ وحده، فلا يرى healthcheck خدمةٍ جديدة في ملفّ الكرت | 4.2 |
| `deploy/resource-budget.sh:264-265` | كلّ profile غير مفعّل يُخرج الخدمة من المجموع. و`--size` لا يفعّل إلّا `local-llm` في وضع الكرت (`:190-192`) | 4.1 |
| `deploy/auto_size.py:392-393`، `:604-633` | يتبع `COMPOSE_PROFILES`. بعد 4.1 تُعدّ المراقبة فقط إن كان `monitoring` فيه، وهذا سلوكٌ صحيح يحاكي Compose | 4.1 |
| `docs/stack-commands.md:117-122`، `docs/quickstart.md:80-83` | سطر المالك `COMPOSE_PROFILES=wsl-ollama`. والسطر 120 يتنبّأ أصلاً: «بعد المرحلة 4.1 يصير `wsl-ollama,monitoring`» | 4.1 |
| `docs/runbooks/alerts.md:13-25` | «أين تصل التنبيهات»: تفترض Prometheus وAlertmanager و`alert-sink` | 4.1 |

**ما لا يتأثّر** (يقرأ YAML الخام، والـprofile لا يمسّ الحقول التي يفحصها):
- `test_log_correlation.py:343`.
- `test_metrics_exporter_role.py:214-252`.
- `test_observability_stack_wiring.py:295-326`.
- `test_connection_budget.py:618-643`.
- `test_deploy_worker_default.py:270-293` (العمّال وحدهم).
- `test_compose_build_overlay.py`.
- `test_compose_sizes.py:221` (يرسم بـ`--profile "*"`).

و`tests/architecture` لا يذكر أيّ خدمة مراقبة.

### 5.3 الميزانيّة والأحجام

- **المجاميع:**
  - الدفتر (وضع المعالج): **35.60 vCPU و57.28 GB** (`08-local-runbook.md:478-479`).
  - جدول الأحجام بوضع الكرت (`:543-547`): `large` 37.60/57.53، و`medium` 19.35/28.41، و`small` 11.50/14.22.
  - المسموح = ذاكرة الخادم × 0.9 (`deploy/resource-budget.sh:319`).
- **الهامش المتبقّي، محسوباً من الملفّات:**

  | الحجم | الهامش |
  |---|---|
  | `large` | **70.4 م.ب** |
  | `medium` | 403.2 م.ب |
  | `small` | 185.6 م.ب |
  | الدفتر المرجعيّ | 326.4 م.ب |

  فمصدّر الكرت بحدٍّ فوق `64m` يُسقط `test_every_size_fits_the_host_its_header_names` لـ`large`.
- **ملفّات الأحجام:**
  - تعرّف حدود الإحدى عشرة (`deploy/sizes/large.env:67-90`).
  - رؤوسها كلّها `mode=gpu` (`large.env:2`، `small.env:2`).
  - `small` «والمراقبة محسوبة» (`server-gpu-plan.md:278`). وقرار البوّابة ① لـ`compose-sizes` Q‑3: «تُحسب الآن، وتُخفَّف بعد 4.1» (`docs/delivery/compose-sizes/status.md:38`).
- **حدود الملفّات الإضافيّة أرقامٌ ثابتة** لا متغيّرات حجم. مثالها `embedding` بـ`3g` في ملفّ الكرت (`docker-compose.gpu.yml:65`، و`compose-sizes` A‑3). وعدد مفاتيح الحجم 85 (`tests/unit/test_compose_sizes.py:95`).
- **`resource-budget.sh` يرفض خدمةً بلا حدود** («NO LIMIT»، `:251-253`، `:370-376`). فمصدّر الكرت يحتاج `cpus` و`memory`.

### 5.4 Prometheus والتنبيهات وGrafana

- **الأهداف كلّها `static_configs`**، إلّا `app` فبـ`dns_sd_configs` (`deploy/prometheus/prometheus.yml:107-113`).
- **`AizzakScrapeTargetDown`** = `up{tier!="optional"} == 0` لـ30 ث (`alerts.yml:303-305`). وحده `cadvisor` يحمل `tier: optional` (`prometheus.yml:207-211`).
- **فهدفٌ ثابت لمصدّر الكرت يُشعل التنبيه بلا توقّف** على كلّ جهازٍ بلا ملفّ الكرت، ومنه جهاز المالك. والمضيف غير موجود فـ`up == 0` دائماً.
- **ثلاث طرقٍ ممكنة** (للمصمّم، Q‑7):
  1. **`tier: optional`.** لكنّه يُسكت مصدّراً ميّتاً على خادم الكرت.
  2. **ملفّ الكرت يضيف لـPrometheus ملفّ أهدافٍ ثانياً.** مثلاً بـ`scrape_config_files` في Prometheus، والصورة `v3.13.2` (`docker-compose.yml:2637`). فلا يوجد الهدف إلّا في وضع الكرت، ويبقى عاديّاً.
  3. **`dns_sd_configs`.** اسمٌ غير موجود لا ينتج هدفاً، لكنّ حاويةً متوقّفة تختفي من DNS أيضاً.
- **`test-rules.sh`:**
  - يأخذ صورتَي `prom/prometheus` و`prom/alertmanager` من `docker-compose.yml` بـ`grep` (`:35-44`).
  - ثمّ يشغّل أربعة فحوص: `check rules`، و`test rules` على نسخةٍ بلا annotations، و`amtool check-config`، و`check config` على `prometheus.yml` (`:66-82`).
  - لا يرى ملفّ أهدافٍ ثانياً ما لم يُضَف إليه.
- **التنبيهات:**
  - 27 تنبيهاً.
  - كلّ قاعدةٍ تحمل الحقول الخمسة: `summary` و`description` و`reason` و`response` و`runbook_url` (`tests/unit/test_prometheus_alert_rules.py:510-521`).
  - ولها مرساةٌ `<a id>` في `docs/runbooks/alerts.md` (`:568-583`).
  - الخطورة واحدةٌ من `critical` و`warning` و`none` (`:614-621`).
  - وفي الدليل فهرسٌ في §1 (`docs/runbooks/alerts.md:46-78`).
- **Grafana:**
  - لوحتان: `deploy/grafana/dashboards/aizzak-capacity.json` (30 لوحة فرعيّة) و`aizzak-logs.json`.
  - ثلاثة مصادر: `prometheus.yml` و`loki.yml` و`alertmanager.yml` تحت `deploy/grafana/provisioning/datasources/`.
  - الاختبارات تفرض شيئين لكلّ لوحةٍ فرعيّة: `description`، و`uid` مصدرٍ مُجهَّز (`test_observability_stack_wiring.py:395-475`). ولا تفحص أسماء مقاييس غير `aizzak_*`.

### 5.5 ملفّ الكرت وجهاز المالك

- **`docker-compose.gpu.yml`:**
  - ثلاث خدمات فقط: `ollama` و`embedding` و`app` (`:3-7`، `:31-85`).
  - **خدمةٌ جديدة فيه لا ترث مرساة السجلّات `*app-logging`**، لأنّ مراسي YAML لا تعبر الملفّات.
  - ودون خيار `labels` (`docker-compose.yml:305`) تخرج أسطرها بلا `attrs`، فيُسقطها Alloy بسبب `not_this_project` (`deploy/alloy/config.alloy:115`).
- **جهاز المالك:**
  - NVIDIA Container Toolkit ‏1.20.1 مثبّتة منذ 2026‑10‑14، و`nvidia-smi` يعمل داخل حاويةٍ بـ`--gpus all` (`docs/delivery/gpu-compose/status.md:64-66`).
  - لكنّ الجهاز يبقى على ملفّ WSL لا ملفّ الكرت (`:99`). **فمصدّرٌ في `docker-compose.gpu.yml` لا يعمل على جهاز المالك أصلاً.**
- **التحقّق المحلّيّ:**
  - لا يكون إلّا في مشروع Compose منفصل (`-p`)، كما فعلت `gpu-compose` في P‑3 (`status.md:87`)، وبذاكرةٍ متاحة ≥ 5 ج.ب.
  - `nvidia-smi` على WSL يقرأ الحرارة والذاكرة (`docker-compose.wsl-gpu.yml:37-40`).
  - **دعم DCGM على WSL2 يحتاج تأكيداً**، والمرجّح أنّه غير مدعوم.
- **ذاكرة الكرت المقيسة:** 0.35–0.37 من 8 ج.ب تحت حمل AC‑6.4 (`status.md:91`، `:93`). ومعيار 2.6: «تبقى تحت 90٪» (`server-gpu-plan.md:245`).

### 5.6 سلوك Compose (مقيسٌ هنا على v5.3.1، في مشروعٍ تجريبيّ في مجلّد scratch، بلا أيّ حاوية)

- **خدمةٌ بـprofile غير مفعّل:**
  - تختفي من `config` و`config --services`.
  - **لكنّ متغيّراتها تُستبدل:** `${VAR:?…}` فيها يُفشل `config` حتّى والـprofile مطفأ.
  - فـ`METRICS_EXPORTER_PASSWORD` (`docker-compose.yml:2994`) يبقى إلزاميّاً على خادمٍ بلا مراقبة، ويبقى `test_compose_refuses_to_render_without_the_exporter_password` صحيحاً.
- **خدمةٌ أساسيّة تعتمد على خدمةٍ بـprofile غير مفعّل** (`required` افتراضيّاً): `config` يرفض المشروع («depends on undefined service»). فحارس FR‑2 يحمي الإقلاع نفسه.
- **إضافة `profiles:` لا تغيّر بصمة `config --hash`.** فالحاويات الإحدى عشرة لا يُعاد إنشاؤها بعد الدمج إن بقي الـprofile مفعّلاً.
- **ملفٌّ إضافيّ يستطيع إضافة `profiles:` إلى خدمةٍ في الملفّ الأساسيّ**، وهذا بديلٌ في Q‑2.
- **تحتاج تأكيداً، ولم تُقَس:**
  - `docker compose down` مع حاوياتٍ لخدماتٍ بـprofile غير مفعّل.
  - `up -d grafana` مسمّاةً صراحةً ومعها اعتمادٌ بـprofile غير مفعّل.

### 5.7 تعارضاتٌ بين الخطّة والشيفرة

1. **القاعدة 1 والـprofile.** Compose لا يعرف profile افتراضيّاً. فوضع الإحدى عشرة تحت `monitoring` يُطفئها على جهاز المالك، إلّا إن أضاف `monitoring` إلى `COMPOSE_PROFILES` في `.env`.
   - والقاعدة 1 لا تسمح إلّا بسطرَي 1.1 و2.3، وباستثناء 3.5 (`server-gpu-plan.md:93-94`).
   - و`stack-commands.md:120` يفترض السطر مسبقاً.
   - وهل في `.env` المالك اليوم `COMPOSE_PROFILES=wsl-ollama`؟ غير معروف: اللوحة تقول «بانتظار ش‑2» (`server-gpu-plan.md:120`). ← Q‑2.
2. **الميزانيّة تُسقط المراقبة بصمت.** السكربت والاختبار و`auto_size.py` تعدّ كلّ profile غير مفعّل خارج المجموع.
   - فبعد 4.1 ينزل الدفتر 3.60 vCPU و4.66 GB دون أن يقرّر أحد.
   - وهذا يخالف قرار 7 (المراقبة مفعّلة) والقاعدة 1 (الأرقام لا تتغيّر). ← FR‑5، Q‑6.
3. **الخطّة تسمّي اختبارَين غير دقيقين** (`:309`).
   - `test_healthcheck_coverage.py` لا يتأثّر بـ4.1.
   - والذي ينكسر فعلاً خمسة ملفّات، في §5.2: `test_monitoring_host_postgres_acceptance.py` و`test_resource_budget.py` و`test_auto_size.py` و`test_compose_local_llm.py`، و`test_observability_stack_wiring.py` الذي تسمّيه الخطّة.
4. **الخطّة لا تقول ما يفعله «هدفٌ له في Prometheus» على جهازٍ بلا كرت (`:315`).** هدفٌ ثابت يُشعل `AizzakScrapeTargetDown` بلا توقّف. ونمط `tier: optional` الموجود يتعارض مع حارسه نفسه (§5.2). ← Q‑7.
5. **هامش `large` 70.4 م.ب.** الخطّة تقترح `dcgm-exporter` أوّلاً، وحدّه المتوقَّع أكبر من ذلك (يحتاج قياساً). ← Q‑4، Q‑8.
6. **تناقضٌ داخل الخطّة حول `small`.** جدول 3.3 يقول «المراقبة محسوبة» (`:278`)، وقرار 7 يقول «تُطفأ على الخادم الصغير» (`:453`). ← Q‑6.
7. **قبول 4.1 فيه «رفع ملفٍّ وفهرسته وسؤالٌ عنه» (`:311`).** لا أداة في المستودع تفعل ذلك آليّاً. `stack_smoke.py` يفحص OPS‑02 وRLS والتوقيع المسبق فقط (`deploy/smoke/stack_smoke.py:11-32`). ← Q‑9، والجزء الكامل في 7.3.
8. **حارس التنبيهات يطلب سبب كلّ تنبيهٍ جديد** «في `docs/log/` أو خطوةٍ في `capacity-plan.md`» (`test_prometheus_alert_rules.py:226-230`)، و4.2 خطوةٌ في `server-gpu-plan.md`. فيُكتب السبب في docstring الاختبار مع الإشارة إلى الخطوة.
9. **PR ‏#13 مفتوح**، ويلمس `server-gpu-plan.md` و`server-gpu-summary.html` (`status.md:35`). وFR‑10 يلمسهما أيضاً.

## 6. الافتراضات والأسئلة المفتوحة

| # | السؤال/الافتراض | يحتاج قرار من |
|---|---|---|
| Q‑1 | **قرار 7:** المراقبة مفعّلةٌ افتراضيّاً على جهاز المالك وعلى كلّ خادم، ولا تُطفأ إلّا على الخادم الصغير (`server-gpu-plan.md:453`)؟ **المقترح:** نعم، كما في الخطّة | المالك |
| Q‑2 | **استثناء القاعدة 1** (§5.7 البند 1). الخيارات:<br>(أ) يعدّل المالك سطره بنفسه إلى `COMPOSE_PROFILES=wsl-ollama,monitoring`، ويُكتب استثناءً ضيّقاً ثالثاً في القاعدة 1. وهو ما تفترضه `stack-commands.md:120`، و2.3 سابقته.<br>(ب) يبقى الملفّ الأساسيّ بلا profile، ويضيف ملفٌّ إضافيّ (مثل `docker-compose.no-monitoring.yml`) `profiles: ["monitoring"]` للإحدى عشرة. يذكره الخادم الصغير وحده في `COMPOSE_FILE`. فلا يلمس المالك `.env`، لكنّه يخالف نصّ الخطوة (`:306`، `:308`)، ونصّ 4.2 «تحت profile `monitoring`».<br>**المقترح: (أ)**، بشرطين: يثبت المالك بعد الدمج أنّ البصمات متطابقة (AC‑3.3)، ويُعلَن الخطر صراحةً. والخطر: نسيان السطر يُطفئ مسار التنبيه كلّه بصمت، فيتوقّف نبض `AizzakWatchdog` ولا ينبّه أحد. وسؤالٌ للمالك: هل أضاف سطر ش‑2 (`COMPOSE_PROFILES=wsl-ollama`) بعد؟ | المالك |
| Q‑3 | **الموافقة على الملفّات المحميّة (القاعدة 2):**<br>• `docker-compose.yml` و`docker-compose.gpu.yml`.<br>• `deploy/prometheus/prometheus.yml` و`alerts.yml` و`alerts.test.yml`، وربّما `test-rules.sh` (ليفحص ملفّ الأهداف الثاني، Q‑7)، وربّما ملفّ أهدافٍ جديد تحت `deploy/prometheus/`.<br>• `deploy/grafana/dashboards/*.json`.<br>• `deploy/resource-budget.sh`، وربّما `deploy/auto_size.py`.<br>• ربّما `deploy/sizes/large.env` (Q‑8).<br>• وأسطر التعليق في `.env.example`.<br>لا `.github/workflows/` ولا `Dockerfile` ولا `deploy/runpod/`. **المقترح:** موافقةٌ دفعةً واحدة على هذه القائمة، وما خرج عنها يُعرض قبل تعديله (سابقة `gpu-compose` Q‑8) | المالك |
| Q‑4 | **المصدّر ووسمه:**<br>(أ) `dcgm-exporter` الرسميّ من NVIDIA: أغنى (أخطاء XID وأسباب الخنق). لكنّ صورته وذاكرته أكبر، ودعمه لكروت GeForce وWSL2 يحتاج تأكيداً. ولا يُتحقَّق منه إلّا في 7.3، ويحتاج غالباً خفضاً في `large` (Q‑8).<br>(ب) مصدّرٌ يقرأ `nvidia-smi` (مثل `utkuozdemir/nvidia_gpu_exporter`): صغير، ويعمل حيث يعمل `nvidia-smi`، ومنه كرت المالك تحت الأداة. ومصدره هو مصدر `system_stats.py`. لكنّه صورة طرفٍ ثالث تُسجَّل في قائمة الأمان.<br>**المقترح: (ب)** بوسمٍ وبصمةٍ يتحقّق المصمّم من وجودهما في السجلّ (درس `0.13.5` في `gpu-compose`)، لأنّه يتّسع في هامش `large` ويمكن إثباته محلّيّاً. ويبقى (أ) خياراً بعد 7.3 إن كان كرت الخادم من فئة مراكز البيانات | المالك |
| Q‑5 | **الحدود:**<br>• **الذاكرة:** `used/total > 0.90` لـ`for: 5m`، بخطورة `warning`. هو حدّ قبول 2.6 نفسه (`:245`)، والمقيس محلّيّاً 0.35–0.37.<br>• **الحرارة:** `> 85°C` لـ`for: 5m`، بخطورة `warning`. هي قرابة حدّ الخنق الشائع، ويُضبط بعد 7.3 إن قرأ المصدّر حدّ الكرت نفسه.<br>• **الاسمان:** `AizzakGpuMemoryHigh` و`AizzakGpuTemperatureHigh`.<br>**المقترح:** كما كُتب | المالك |
| Q‑6 | **الميزانيّة والمراقبة مطفأة.** المقترح أربعة أجزاء:<br>(1) السكربت والاختبار والدفتر يعدّون `monitoring` مفعّلاً افتراضيّاً، فلا يتحرّك رقم (FR‑5).<br>(2) خيارٌ صريح، مثل `--without-monitoring`، يحكم دونها (FR‑6).<br>(3) `--block` و`auto_size.py` يتبعان الـprofiles كما اليوم، ويعيد المالك تشغيل `auto_size.py` بعد تعديل سطره.<br>(4) **ملفّات الأحجام لا تتغيّر**، و`small` يبقى يعدّ المراقبة. و«تخفيفه بعد 4.1» (قرار Q‑3 في `compose-sizes`) يُؤجَّل إلى قياس 7.3.<br>هل يُقبل التأجيل في (4)، أم يُحكم على `small` دون المراقبة الآن؟ | المالك |
| Q‑7 | **هدف المصدّر على جهازٍ بلا كرت** (§5.4). **المقترح:** شرطٌ لا طريقة: لا هدف ولا تنبيه بلا ملفّ الكرت، وهدفٌ عاديّ معه. والطريقة المرجّحة: ملفّ الكرت يضيف ملفّ أهدافٍ لـPrometheus (`scrape_config_files`)، ويفحصه `test-rules.sh` في الوضعين. والمصمّم يحسم | المصمّم |
| Q‑8 | **حدّ ذاكرة المصدّر** و`large` (هامش 70.4 م.ب). **المقترح:** حدٌّ ≤ `64m` إن كانت ذروته × 1.5 تحته. وإلّا يُخفض حدٌّ في `large.env` بقرار المالك، كما فعل Q‑2 في `compose-sizes` بـ`POSTGRES_MEM` | المالك |
| Q‑9 | **الإثبات الحيّ لـ4.1 (`:311`).** هل يُسمح بإيقاف الإحدى عشرة في المكدّس المحلّيّ ≤ 10 دقائق، بأسمائها وبـ`stop` لا `down`؟ ثمّ تُفحص `/health` و`/health/ready` و`stack_smoke.py`، ثمّ تُعاد وتُفحص صحّتها (AC‑1.8). **المقترح:** نعم. و«رفع ملفٍّ وفهرسته وسؤالٌ عنه» يُؤجَّل إلى 7.3 (AC‑1.9)، لأنّ لا أداة آليّة له هنا | المالك |
| Q‑10 | **إثباتٌ محلّيّ لـ4.2** في مشروع Compose منفصل (`-p`) بملفّ الكرت، كـP‑3 في `gpu-compose`، بذاكرةٍ متاحة ≥ 5 ج.ب. يقيس ثلاثة أشياء: ذروة المصدّر، ومقاييسه، ولوحة Grafana (AC‑4.6، AC‑6.2). **المقترح:** نعم إن اختير (ب) في Q‑4، وإلّا يُؤجَّل إلى 7.3 | المالك |
| Q‑11 | PR ‏#13 يلمس ملفَّي اللوحة. **المقترح:** يُدمج أوّلاً، ثمّ يتفرّع `feat/monitoring-profile` من `master` بعده | المالك |
| A‑1 | «الإحدى عشرة» هي القائمة في FR‑1 بالضبط. و`cadvisor` لا يأخذ `monitoring` | — |
| A‑2 | ميزانيّة الاتّصالات لا تتغيّر. تعدّ جلسات المصدّرين ثوابت (`test_connection_budget.py:618-643`)، فعدّها والمراقبة مطفأة زيادةٌ آمنة | — |
| A‑3 | `METRICS_EXPORTER_PASSWORD` يبقى إلزاميّاً حتّى والمراقبة مطفأة (§5.6)، و`install.sh` يولّده أصلاً (`:337`) | — |
| A‑4 | حدود مصدّر الكرت أرقامٌ ثابتة في ملفّ الكرت، كـ`embedding` بـ`3g`، لا متغيّرات حجم. فمفاتيح الحجم تبقى 85 | — |
| A‑5 | التنبيهان في `alerts.yml` الذي يُحمَّل دائماً. على جهازٍ بلا مقاييس كرت لا سلسلة لهما، فيبقيان غير نشطين ولا يشتعلان | — |
| A‑6 | `.env.example` يبقى بلا سطر `COMPOSE_*` فعّال (`test_gpu_compose_hygiene.py:442-446`)، وتتغيّر التعليقات وحدها. ولم يُقرأ الملفّ هنا: صلاحيّات هذه الجلسة تمنع قراءة `.env*`، ومحتواه مأخوذٌ من الاختبارات | — |
| A‑7 | مصدّر الكرت يعيد ذكر خيارات السجلّ (`json-file` و`labels`)، فتصل أسطره إلى Loki (§5.5) | — |

## 7. التقدير

| البند | الحجم (S/M/L/XL) | ملاحظات |
|---|---|---|
| US‑1 profile المراقبة (4.1) | M | تعديلٌ آليّ لإحدى عشرة خدمة، وحارسان، ونقل ثلاثة اختبارات |
| US‑2 الميزانيّة تتبع الـprofile | M | خيارٌ في السكربت، ومنطق «مفعّل افتراضيّاً» في السكربت والاختبار، وأرقام `test_auto_size.py`، والدفتر |
| US‑3 جهاز المالك والوثائق | S | نصوص، وتعليقات `.env.example`، ولوحة الخطّة، ومعياران على جهاز المالك بعد الدمج |
| US‑4 مصدّر الكرت وهدفه (4.2) | M | خدمةٌ في ملفّ الكرت، وآليّة الهدف (Q‑7)، و`test-rules.sh`، والميزانيّة، واختبارات ملفّ الكرت |
| US‑5 التنبيهان (4.2) | S | قاعدتان، وست حالات promtool، وقسمان في الدليل |
| US‑6 لوحة Grafana (4.2) | S | صفٌّ بثلاث لوحاتٍ فرعيّة |
| **الإجمالي** | **L** | جلستا تنفيذٍ إلى ثلاث، ومعهما المراجعة. **المخاطر:**<br>• Q‑2: نسيان سطر المالك يُطفئ التنبيهات بصمت.<br>• هامش `large` 70.4 م.ب (Q‑8).<br>• آليّة الهدف (Q‑7) تلمس `prometheus.yml` و`test-rules.sh` وحارسين.<br>• وسم المصدّر ودعمه لـWSL غير مُتحقَّق.<br>• القاعدة 3: نحو ثمانية ملفّات اختبار تتغيّر، وبعضها يرسم `config` في بيئة CI (`COMPOSE_FILE` مصدَّر).<br>• التعارض النصّيّ مع PR ‏#13.<br>• الإثبات الحيّ لـ4.2 لا يكتمل قبل 7.3. |

</div>
