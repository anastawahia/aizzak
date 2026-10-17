<div dir="rtl">

# التصميم التقني: المراقبة اختياريّة وأرقام الكرت (`monitoring-profile`، المرحلة 4 من خطّة خادم الكرت)

> **المرجع:** [`requirements.md`](requirements.md) و[`stories.md`](stories.md)، وقرارات البوّابة ① في [`status.md`](status.md): Q‑1 نعم، وQ‑2 (أ)، وQ‑3 دفعةً واحدة، وQ‑4 (ب)، وQ‑5 وQ‑6 كما كُتبا، وQ‑8 ≤ `64m`، وQ‑9 وQ‑10 مسموحان، وQ‑11 التفرّع من `master` الحاليّ. لا يكرّر هذا الملفّ المتطلبات.
>
> **كيف تحقّق المعماريّ:**
> - **نموذجٌ أوّليّ في مجلّد مؤقّت.** طُبّق التصميم على نسخةٍ من المستودع بلا `.git` ولا `.env` ولا أيّ ملفّ سرّ (`/tmp/claude-1000/-home-AIZZAK/26c33c59-4c28-4bda-b837-8a670f1e625c/scratchpad/repo`)، ومنه أُخذ كلّ رقمٍ وكلّ قائمة اختباراتٍ أدناه:
>   - **4.1 وحده:** 25 اختباراً تنكسر في 6 ملفّات.
>   - **بعد إصلاحات §2:** `5736 passed`، ولا فشل.
>   - **ثمّ 4.2:** 10 اختباراتٍ أخرى تنكسر، كلّها متوقَّعة (§2).
> - **Compose v5.3.1:**
>   - بصمات `config --hash` للإحدى عشرة **متطابقة** مع `profiles:` وبدونه.
>   - `COMPOSE_PROFILES=wsl-ollama,monitoring` على `master` اليوم لا يغيّر شيئاً: 34 خدمة في الحالتين.
> - **صورة المصدّر:** تحقّقٌ للقراءة فقط من السجلّ، بلا سحبٍ ولا تشغيل (§3‑ج).
> - **Prometheus ‏`v3.13.2`:** قُرئ مصدره عند الوسم نفسه (§3‑د).

## 1. الملخص

الميزة **طبقة نشرٍ ومراقبة فقط**: ملفّا Compose و`deploy/prometheus/` و`deploy/grafana/` و`deploy/resource-budget.sh` والاختبارات والوثائق. **لا تلمس `src/`** ولا المخطّط ولا `deploy/auto_size.py` ولا ملفّات الأحجام.

- **4.1، المراقبة خلف profile:**
  - **الملفّ:** سطر `profiles: ["monitoring"]` أوّلَ مفتاحٍ في كلٍّ من الخدمات الإحدى عشرة، ولا تغيير آخر فيها. البصمة لا تتغيّر، فلا حاوية يُعاد إنشاؤها.
  - **حارسٌ آليّ:** يمنع أيّ خدمةٍ خارجها من الاعتماد عليها أو ذكر مضيفها.
  - **الميزانيّة:** `deploy/resource-budget.sh` ودفتر الاختبار يعدّان `monitoring` **مفعّلاً افتراضيّاً** (قرار 7)، فلا يتحرّك رقم.
  - **الحكم بدونها:** خيارٌ جديد `--without-monitoring`.
  - **ما يتبع Compose كما اليوم:** `--block` و`deploy/auto_size.py`. و`auto_size.py` **لا يتغيّر سطرٌ فيه**، لأنّ `_known_profiles()` يقرأ الـprofiles من ملفّات Compose.
- **4.2، أرقام الكرت:**
  - **المصدّر:** خدمة `nvidia-gpu-exporter` (`utkuozdemir/nvidia_gpu_exporter:1.15.1` مثبّتةً بالبصمة) في `docker-compose.gpu.yml` وحده، تحت `monitoring`.
  - **هدفه في Prometheus:**
    - `prometheus.yml` يقرأ ملفّات أهدافٍ بنمط: `scrape_config_files: [/etc/prometheus/scrape.d/*.yml]`.
    - وملفّ الكرت وحده يربط فيه `deploy/prometheus/scrape-gpu.yml`.
    - فجهازٌ بلا ملفّ الكرت لا هدف له ولا سلسلة `up` ولا تنبيه، وجهازٌ به هدفٌ عاديّ يغطّيه `AizzakScrapeTargetDown`.
  - **التنبيهان:** `AizzakGpuMemoryHigh` و`AizzakGpuTemperatureHigh`.
  - **اللوحة:** صفّ GPU في `aizzak-capacity.json`.
- **الأنماط المتّبعة:**
  - **profile على خدمةٍ قائمة:** `ollama-bridge`، والـprofile أوّل مفتاح (`docker-compose.yml:1624-1625`).
  - **لقطة «لم يتغيّر غير الـprofile»:** `test_the_bridge_is_profiled_and_otherwise_unchanged` (`tests/unit/test_compose_local_llm.py:307-311`).
  - **اختبارات [config] ببيئةٍ نظيفة:** `tests/unit/test_compose_gpu_overlay.py:32,55-70`، و`--env-file .env.example` وحذف `COMPOSE_FILE` و`COMPOSE_PROFILES`.
  - **مصدّرٌ مقيَّد الصلاحيّات:** `node-exporter` (`docker-compose.yml:2925-2975`)، و`user` و`read_only` و`cap_drop` و`no-new-privileges`.
  - **البصمة بجانب الصورة:** `ollama` (`docker-compose.yml:1681-1686`). وهنا تُلحق بالمرجع نفسه، وهو علاج `ث‑3` المقترح في `docs/deploy-server.md:83`.
  - **حالات promtool:** «تشتعل/تصمت» كما في `deploy/prometheus/alerts.test.yml:95-110`.
  - **قسم الدليل:** `AizzakHostDiskHigh` (`docs/runbooks/alerts.md:534-561`).

## 2. الملفات

**المنفّذ لكلّ الملفّات: `backend-developer`.** المستودع واحد، والعمل متسلسل. لا `frontend-developer` ولا `data-engineer`.

| المسار | إنشاء/تعديل | المسؤولية | المنفّذ |
|---|---|---|---|
| `docker-compose.yml` | تعديل | 11 سطراً `profiles: ["monitoring"]`، أوّلَ مفتاحٍ تحت اسم كلّ خدمة (§3‑أ). وفقرة تعليقٍ بعد رأس كتلة «Observability» (`:2617`). لا شيء غيرهما | backend-developer |
| `docker-compose.gpu.yml` | تعديل | مدخل `prometheus` بربطٍ واحد (§3‑د)، وخدمة `nvidia-gpu-exporter` (§3‑ج)، وتحديث تعليق الرأس («Three services» ← خمسة مداخل) | backend-developer |
| `deploy/prometheus/scrape-gpu.yml` | إنشاء | مهمّة `nvidia-gpu` بهدفٍ ثابت (§3‑د) | backend-developer |
| `deploy/prometheus/prometheus.yml` | تعديل | `scrape_config_files` بنمط، ومعه تعليقه (§3‑د) | backend-developer |
| `deploy/prometheus/test-rules.sh` | تعديل | فحص `check config` مرّتين: بلا ملفّ الكرت ومعه (§3‑د) | backend-developer |
| `deploy/prometheus/alerts.yml` | تعديل | مجموعة `aizzak-gpu` بقاعدتين (§3‑هـ) | backend-developer |
| `deploy/prometheus/alerts.test.yml` | تعديل | سبع حالات promtool (§3‑هـ) | backend-developer |
| `deploy/grafana/dashboards/aizzak-capacity.json` | تعديل | صفٌّ (id 31) وثلاث لوحاتٍ فرعيّة (32–34) عند `y=68`، فلا يتحرّك `gridPos` قائم (§3‑ز) | backend-developer |
| `deploy/resource-budget.sh` | تعديل | `monitoring` مفعّلٌ افتراضيّاً، و`--without-monitoring`، وسطر `monitoring:` (§3‑ب) | backend-developer |
| `.env.example` | تعديل | سطرا التعليق يصيران `#COMPOSE_PROFILES=wsl-ollama,monitoring` و`#COMPOSE_PROFILES=local-llm,monitoring`، وفوقهما سطر تعليقٍ واحد (§3‑ح). لا سطر `COMPOSE_*` فعّال | backend-developer |
| `tests/unit/support_compose.py` | تعديل | `DEFAULT_PROFILES` (§3‑ط) | backend-developer |
| `tests/unit/test_monitoring_profile.py` | إنشاء | AC‑1.1…1.5، وAC‑2.6، وAC‑3.1، وAC‑3.5، وAC‑3.6، وAC‑4.7 (§5) | backend-developer |
| `tests/unit/test_resource_budget.py` | تعديل | `_classify` و`_size_totals` بـ`DEFAULT_PROFILES`، وAC‑2.1، وAC‑2.2، وAC‑2.5، وAC‑4.5 | backend-developer |
| `tests/unit/test_auto_size.py` | تعديل | `_plan(..., profiles="monitoring")`، والاختبارات الأربعة التي تمرّر `monitoring` صراحةً، وAC‑2.3، وAC‑2.4. وفي 4.2 صفوف `gpu` الستّة وأرقام الرفض | backend-developer |
| `tests/unit/test_auto_size_edges.py` | تعديل | `--profiles monitoring` في اختبارين، وفي 4.2 عتبة الرفض 15.86/15.87 | backend-developer |
| `tests/unit/test_compose_local_llm.py` | تعديل | `33` ← `22` = 17 + 5 (`:352-358`)، وسطرا `.env.example` (`:364-367`) | backend-developer |
| `tests/unit/test_monitoring_host_postgres_acceptance.py` | تعديل | `_compose(..., profile=)` يحذف `COMPOSE_FILE` و`COMPOSE_PROFILES` ويمرّر `-f docker-compose.yml`. و`profiles == ["monitoring"]` (`:227`)، وAC‑1.6 بالوضعين | backend-developer |
| `tests/unit/test_observability_stack_wiring.py` | تعديل | قاعدة `tier: optional` (`:270-289`)، وأهداف ملفّ الكرت (`:201-214`)، والمنافذ، وآليّة الربط، وAC‑6.1 | backend-developer |
| `tests/unit/test_compose_gpu_overlay.py` | تعديل | المداخل خمسة (`:89-92`)، و`_config(profiles=…)`، وAC‑4.1، وAC‑4.2، والسجلّ | backend-developer |
| `tests/unit/test_healthcheck_coverage.py` | تعديل | يضيف الخدمات التي **يعرّفها** ملفّ الكرت (`:88-96`) | backend-developer |
| `tests/unit/test_prometheus_alert_rules.py` | تعديل | `EXPECTED_ALERTS` 29 وسببه المكتوب، وAC‑5.1، وAC‑5.3، وAC‑5.4، ومسار الربط في `test-rules.sh` | backend-developer |
| `docs/runbooks/alerts.md` | تعديل | فقرة §0، وسطرا الفهرس، وقسمان في §3، وسطرٌ في `AizzakScrapeTargetDown`، وخطوة Watchdog، وسطرا §6 (§3‑و) | backend-developer |
| `docs/stack-commands.md` | تعديل | الأمر 1 (`:117-122`)، والأمر 41 (`:594-607`)، وخليّة الجدول 41 (`:97`)، وسطر «آخر مطابقة» (`:11`) | backend-developer |
| `docs/quickstart.md` | تعديل | الأسطر 80–83 | backend-developer |
| `docs/deploy-server.md` | تعديل | جملة الرأس، و«## 2) المراقبة والتنبيهات»، وصفّ `ث‑6` في قائمة الأمان. **الملفّ موجود** (87 سطراً). ⚠️ لا صفّ جدولٍ يبدأ برقم: `test_gpu_preflight.py:481-484` يعدّ كلّ `\| <رقم> \|` رمزَ خروج | backend-developer |
| `docs/design/08-local-runbook.md` | تعديل | جدول الخدمات (صفّان)، و§2‑ز (فقرة، وأمر، وجملة في «ما لا يُحسَب»، وجدول الأحجام بعد 4.2)، وصفٌّ في جدول §5 | backend-developer |
| `docs/server-gpu-plan.md` | تعديل | §3 القاعدة 1 (الاستثناء الثالث)، و§4 صفّا 4.1 و4.2 | backend-developer |
| `docs/server-gpu-summary.html` | تعديل | مقالا ٤٫١ و٤٫٢، وشريط التقدّم ومفتاحه ووصفه | backend-developer |

**لا تُلمس:** `deploy/auto_size.py`، و`deploy/sizes/*`، و`deploy/postgres/*`، و`deploy/nginx/*`، و`deploy/alloy/*`، و`deploy/alertmanager/*`، و`deploy/runpod/*`، و`.github/workflows/*`، وكلّ `Dockerfile`، و`src/`، و`migrations/`، و`docker-compose.{wsl-gpu,test,build}.yml`.
- **كلّ ملفٍّ محميٍّ في الجدول من قائمة Q‑3.** ولا ملفّ محميّ خارجها.
- **ما لا يتغيّر من القائمة:** `deploy/auto_size.py` و`deploy/sizes/large.env`. والثاني لا يتغيّر إلّا إن تحقّق ⚠️ في §7.

**الاختبارات التي تنكسر، بالقياس على النموذج لا بالتقدير:**

- **بـ4.1 (25 اختباراً):**
  - **`test_auto_size.py`:**
    - صفوف `_REFERENCE` الـ13.
    - و`test_a_host_below_small_is_refused_naming_the_shortfall`.
    - و`test_dev_overcommit_is_a_warning_with_the_ratio`.
    - و`test_server_role_refuses_the_development_host`.
    - و`test_without_a_block_the_budget_is_the_reference_ledger`.
  - **`test_auto_size_edges.py`:** `test_the_refusal_threshold_is_exact_in_gpu_mode` و`test_no_block_and_no_role_means_server`.
    - **⚠️ هذا الملفّ ليس في قائمة المحلّل** (`requirements.md` §5.2).
  - **`test_compose_local_llm.py`:** `test_the_default_stack_is_the_28_standing_and_5_one_shots`.
  - **`test_monitoring_host_postgres_acceptance.py`:** `test_compose_renders_both_exporters_as_standing_services_with_limits`.
  - **`test_observability_stack_wiring.py`:** `test_the_optional_target_is_the_one_behind_a_compose_profile`.
  - **`test_resource_budget.py`:** `test_the_patterns_actually_find_something`، و`test_the_runbook_ledger_carries_the_numbers_this_module_computes`، و`test_the_runbook_size_table_carries_the_numbers_this_module_computes`.
  - **إصلاح الثلاثة الأخيرة:** `_classify` بـ`DEFAULT_PROFILES`، و`--without-monitoring`، فلا يتغيّر رقمٌ مكتوب.
  - **وبعد إصلاحات هذا الجدول:** `test_the_budget_script_agrees_with_the_generator[3]` يحتاج `--profiles monitoring` أيضاً.
- **بـ4.2 (10 اختبارات):**
  - **`test_auto_size.py`:** صفوف `gpu` الستّة في `_REFERENCE`، و`test_a_host_below_small_is_refused_naming_the_shortfall`.
  - **`test_auto_size_edges.py`:** `test_the_refusal_threshold_is_exact_in_gpu_mode`.
  - **`test_compose_gpu_overlay.py`:** `test_the_overlay_has_only_services_and_exactly_three`.
  - **`test_resource_budget.py`:** `test_the_runbook_size_table_carries_the_numbers_this_module_computes`.
  - والأرقام الجديدة في §3‑ي.
- **لا تتأثّر** (خضراء في النموذج):
  - `test_healthcheck_coverage` قبل توسيعه، و`test_log_correlation`، و`test_metrics_exporter_role`، و`test_connection_budget`.
  - و`test_compose_sizes`، و`test_deploy_worker_default`، و`test_compose_build_overlay`، و`test_edge_hardening`.
  - و`test_gpu_compose_hygiene` و`test_gpu_preflight`. الثاني يبقى أخضر ما دام لا صفّ رقميّ جديد في `deploy-server.md`.

## 3. العقود

### الواجهات البرمجية

لا شيء: لا مسار HTTP، ولا تغيير في `openapi.yaml`. و`GET /metrics` وحجبه في nginx كما هما (FR‑3). يحرسهما `tests/unit/test_edge_hardening.py:333-354` القائم.

### 3‑أ) `docker-compose.yml`

- **11 سطراً، سطرٌ لكلّ خدمة، وكلٌّ أوّلَ مفتاحٍ تحت اسمها** (`:2631` و`:2683` و`:2732` و`:2772` و`:2812` و`:2866` و`:2889` و`:2925` و`:2977` و`:3070` و`:3101`):
  ```yaml
    prometheus:
      profiles: ["monitoring"]
      deploy:
  ```
- **ثمّ فقرة تعليقٍ** بعد كتلة التعليق «Observability» (`:2617-2629`) وقبل `prometheus:`. التعليق لا يغيّر الـYAML ولا البصمة:
  ```yaml
    # ── Profile `monitoring` (server-gpu-plan.md 4.1, decision 7) ────────────
    # The eleven services of this block and of the log block below --
    # prometheus, grafana, alertmanager, alert-sink, the five exporters, loki,
    # alloy -- run only with `monitoring` in COMPOSE_PROFILES. On by default:
    # the owner's line is COMPOSE_PROFILES=wsl-ollama,monitoring, a server's
    # local-llm,monitoring. A small server may drop it and then has NO alert
    # path at all. Nothing outside the eleven depends on them or names their
    # hosts (tests/unit/test_monitoring_profile.py); their own edges out to
    # app, pgbouncer, postgres and the two Redis are allowed. cadvisor keeps
    # its own `container-metrics` profile.
  ```
- **وما لا يتغيّر:**
  - `cadvisor` يبقى `["container-metrics"]` وحده.
  - `${METRICS_EXPORTER_PASSWORD:?…}` يبقى إلزاميّاً حتّى والمراقبة مطفأة (A‑3).

### 3‑ب) `deploy/resource-budget.sh`: المراقبة مفعّلةٌ افتراضيّاً، و`--without-monitoring`

**سطر الأمر** (سطر `usage:` ونصّ `--help`، و`sed -n '2,18p'` بدل `'2,15p'`):
```
deploy/resource-budget.sh [--host-cpus N] [--host-memory-gb G] [--with-profiles | --without-monitoring] [--size NAME | --block FILE]
#   deploy/resource-budget.sh --without-monitoring [--size NAME]
#                                      judge a host that does not run profile `monitoring`
#                                      (counted by default, decision 7). No alerts without it
```

**القواعد:**

| الحالة | `monitoring` | السبب |
|---|---|---|
| بلا `--size` وبلا `--block`، أو بـ`--size` | **مفعّل**، ويُضاف إلى `enabled` قبل التصنيف | قرار 7، FR‑5 |
| `--without-monitoring` (وحده أو مع `--size`) | غير مفعّل، فتنزل الإحدى عشرة ومصدّر الكرت إلى قسم «profiled» | FR‑6 |
| `--block FILE` | من سطر `# profiles:` في الكتلة وحده، كما اليوم | FR‑7: الكتلة تكتب ما يراه Compose |
| `--without-monitoring` مع `--block`، أو مع `--with-profiles` | **رمز 2**، والرسالة على stderr | الجمع متناقض، فيُرفض ولا يُختار أحدهما بصمت |

**الرسائل بالحرف.** التعارض برمز 2:
```
--without-monitoring and --block are mutually exclusive: the block's '# profiles:' line already says whether monitoring runs
--without-monitoring and --with-profiles are mutually exclusive: one removes a profile, the other counts them all
```

**سطرٌ جديد واحد على stdout،** بعد `size_line` إن وُجد وقبل قسم «standing». هو التغيير الوحيد في مخرج المسار الافتراضيّ ومسار `--size`، وسطر `TOTAL` لا يتغيّر:
```
monitoring: counted (on by default, decision 7)
monitoring: counted (in the block's profiles)
monitoring: NOT counted (--without-monitoring). Without it there are no alerts at all: check /health from outside the host.
monitoring: NOT counted (not in the block's profiles). Without it there are no alerts at all: check /health from outside the host.
```

**التعديل داخل الـheredoc:**
- **وسيطٌ تاسع:** `"$without_monitoring"`.
- **ثابتٌ مسمّى:**
  ```python
  without_monitoring = sys.argv[9] == "1"
  MONITORING = "monitoring"  # decision 7 (server-gpu-plan.md §6): on by default
  enabled: set = set() if (block or without_monitoring) else {MONITORING}
  ```
- **ما يبقى كما هو:**
  - `--size` بوضع الكرت يضيف `local-llm` كما اليوم.
  - `--block` يضيف `block_profiles`.
  - التصنيف `set(profiles) - enabled` لا يتغيّر.
- **ويُحدَّث رأس التعليق** (`:46-47`): «Profiled services … `--with-profiles` counts them. `monitoring` is the exception: counted by default (decision 7); `--without-monitoring` judges a host without it; `--block` follows the block's `# profiles:` line, as Compose does».

**`deploy/auto_size.py` لا يتغيّر، لثلاثة أسباب:**
- `_known_profiles()` (`:204-210`) يقرأ الـprofiles من الملفّين، فيصير `monitoring` معروفاً وحده.
- `compute()` (`:392-393`) يعدّ ما في `COMPOSE_PROFILES` فقط، وهذا سلوك Compose (FR‑7).
- فخادمٌ يكتب `install.sh` (5.2) سطره `local-llm,monitoring` يُحسب بالمراقبة، وخادمٌ صغير بلا `monitoring` يُحسب بدونها.

### 3‑ج) `docker-compose.gpu.yml`: خدمة `nvidia-gpu-exporter`

**ما تحقّق منه المعماريّ في السجلّ (2026‑10‑17)، قراءةً فقط:**

| ما فُحص | كيف | النتيجة |
|---|---|---|
| الوسوم ونشرها | `curl https://hub.docker.com/v2/repositories/utkuozdemir/nvidia_gpu_exporter/tags` | `1.15.1` منشورٌ 2026‑09‑02، ويساوي `latest` اليوم. و14 إصداراً بين 2026‑06‑22 و2026‑09‑02 |
| بصمة الفهرس متعدّد المنصّات | `docker buildx imagetools inspect utkuozdemir/nvidia_gpu_exporter:1.15.1` | `sha256:7aee2d42836ad29d4adb2722b7cfdc806ec9cc8d45fd0b8971ebbad2d9f3d087`، ويطابق ما في Docker Hub API |
| بصمة `linux/amd64` | الأمر نفسه | `sha256:93b45e335c8286379e03ecd542354fa425e5ffb8e0e5761e60c1fb241212fda4`، و11.5 م.ب مضغوطة |
| إعداد الصورة | `imagetools inspect --format '{{json .Image}}'` | `User=65534:65534`، و`Entrypoint=/usr/bin/nvidia_gpu_exporter`، و`ExposedPorts=9835/tcp`، و`Healthcheck=None` |
| أساس الصورة | `Dockerfile` في الوسم `v1.15.1` على GitHub | `gcr.io/distroless/base-nossl-debian13` مثبّتٌ بالبصمة: **لا shell ولا `wget` ولا `curl`**. مسار التحوّل: `1.14.0`: «shell-less distroless base»، و`1.15.0`: «non-root user»، و«sign the release checksums keyless with cosign» |
| أسماء المقاييس | `internal/integration/testdata/*rtx-4080-super*load.metrics` و`*h100*` في الوسم نفسه، و`docs/METRICS.md` | `nvidia_smi_memory_used_bytes{uuid}`، و`nvidia_smi_memory_total_bytes{uuid}`، و`nvidia_smi_utilization_gpu_ratio{uuid}` (0–1)، و`nvidia_smi_temperature_gpu{uuid}` (°C)، و`nvidia_smi_gpu_info{uuid,name,index,…}` = 1، و`nvidia_smi_last_collect_success` |
| نقاط الصحّة | `docs/CONFIGURE.md` و`internal/app/app.go:799-800` | `/-/healthy` و`/-/ready` على مستوى العمليّة. لكن لا عميل HTTP في الصورة ليستدعيهما |

**الخدمة** (بعد `app:` في الملفّ):
```yaml
  # 4.2 (server-gpu-plan.md): the exporter's scrape job, found by
  # prometheus.yml's `scrape_config_files` glob. Without this mount the glob
  # matches nothing and Prometheus has no `nvidia-gpu` target at all.
  prometheus:
    volumes:
      - ./deploy/prometheus/scrape-gpu.yml:/etc/prometheus/scrape.d/gpu.yml:ro

  # ── GPU numbers for Prometheus (server-gpu-plan.md 4.2) ─────────────────
  # nvidia-smi read by utkuozdemir/nvidia_gpu_exporter (owner decision Q-4 (b),
  # docs/delivery/monitoring-profile/). Here only, and only with profile
  # `monitoring`: no GPU file, no exporter, no target, no alert.
  # 1.15.1 (2026-09-02) is distroless and uid 65534: no shell and no wget, so
  # the healthcheck runs the one binary the toolkit mounts -- nvidia-smi --
  # and proves the card is readable from HERE; the HTTP side is
  # AizzakScrapeTargetDown's (an ordinary target). Pinned by the multi-arch
  # index digest, verified 2026-10-17 with `docker buildx imagetools inspect`
  # (linux/amd64 manifest sha256:93b45e335c8286379e03ecd542354fa425e5ffb8e0e5761e60c1fb241212fda4).
  # `utility` only, as `app` above: nvidia-smi + NVML, no CUDA context.
  # Limits are fixed numbers, not size variables (A-4): 64m is `large`'s
  # GPU-mode headroom (Q-8), held against measured peak x 1.5 (AC-4.6).
  # YAML anchors do not cross files, so `logging` restates x-app-logging;
  # tests/unit/test_compose_gpu_overlay.py holds the two equal.
  nvidia-gpu-exporter:
    profiles: ["monitoring"]
    image: utkuozdemir/nvidia_gpu_exporter:1.15.1@sha256:7aee2d42836ad29d4adb2722b7cfdc806ec9cc8d45fd0b8971ebbad2d9f3d087
    user: "65534:65534"
    read_only: true
    cap_drop: ["ALL"]
    security_opt: ["no-new-privileges:true"]
    command:
      - "--web.listen-address=:9835"
    deploy:
      resources:
        limits:
          cpus: "0.25"
          memory: 64m
        reservations:
          devices:
            - driver: nvidia
              capabilities: [utility]
    expose:
      - "9835"
    healthcheck:
      test: ["CMD", "nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 20s
    restart: unless-stopped
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
        labels: "com.docker.compose.service,com.docker.compose.project"
```

**تفاصيل:**
- **ما يتركه المصدّر على افتراضيّه:**
  - `--query-field-names` على `AUTO`.
  - والجمع متزامنٌ مع كلّ كشط (15 ث).
  - و`--collect.timeout=10s`.
  - ولا `--collect.compute-apps`، فلا `pid=host`.
- **الحجز بلا `count`:** Compose يحلّه إلى `count: -1`، أي كلّ الكروت، كما `app` (رأس الملفّ `:28-30`).
- **`capabilities: [utility]`:** هو ما يأخذه `app` في الملفّ نفسه (`:79-85`)، وأثبت P‑3 في `gpu-compose` أنّ `nvidia-smi` يعمل به على هذا الجهاز (`status.md:87`).
- **إن رفضت أداة NVIDIA `read_only: true` في الإثبات المحلّيّ:** يُحذف هذا المفتاح وحده. السبب: `ldcache` تكتبه أداة الكرت داخل الجذر، والمتوقَّع أنّ ذلك يحدث قبل جعل الجذر للقراءة. ويُسجَّل في `status.md`، ولا قرار جديد.
- **`test_every_compose_image_is_pinned`** يقبل `tag@sha256` (`tests/unit/test_gpu_compose_hygiene.py:41,48-49`).
- **Compose يدمج `volumes`** في `prometheus` إلحاقاً بالمسار الهدف (قيس: 4 مداخل). و`merge()` في `resource-budget.sh` و`support_compose._merge` و`auto_size.py` يستبدل القائمة، لكنّها لا تقرأ غير الحدود والـprofiles و`restart`.

### 3‑د) هدف المصدّر في Prometheus (Q‑7)

**الآلية المختارة: `scrape_config_files` بنمطٍ في `prometheus.yml`، وملفّ الكرت وحده يربط ملفّ الأهداف.**

| الطريقة | جهازٌ بلا ملفّ الكرت | جهازٌ به | الحكم |
|---|---|---|---|
| `tier: optional` | صامت | **مصدّرٌ ميّت صامت** | يُرفض: يُسقط FR‑12 |
| `dns_sd_configs` | لا هدف | حاويةٌ متوقّفة تختفي من DNS، فلا `up` | يُرفض: يُسقط FR‑12 |
| **`scrape_config_files` بنمط** | النمط لا يطابق شيئاً، فلا مهمّة ولا `up` ولا تنبيه | هدفٌ ثابتٌ عاديّ، و`up == 0` يُشعل `AizzakScrapeTargetDown` | **المختار** |

**ما قُرئ في مصدر Prometheus عند الوسم المثبّت `v3.13.2`:**
- **`config.GetScrapeConfigs()`** (`config/config.go:346-391`) يمرّ على كلّ نمطٍ بـ`filepath.Glob`.
- **النمط الذي لا يطابق شيئاً** (والمجلّد غير موجود) لا ملفّ له ولا خطأ.
- **كلّ ملفٍّ مطابق:**
  - يُقرأ بـ`yaml.UnmarshalStrict`، فلا مفتاح فيه غير `scrape_configs`.
  - ويُتحقَّق منه مع `global`.
  - وتكرار اسم مهمّةٍ خطأ.
- **`promtool check config`** يستدعي `GetScrapeConfigs()` في الوضع العاديّ (`cmd/promtool/main.go:696-705`)، فيفحص الملفّ المربوط فعلاً.

**`deploy/prometheus/prometheus.yml`** (بعد `rule_files:`). تغيير محتوى ملفٍّ مربوط لا يغيّر بصمة `config --hash`:
```yaml
# ── Scrape files an overlay adds (server-gpu-plan.md 4.2) ──────────────────
# A glob, not a path. Prometheus reads every file it matches and NOTHING when
# it matches none (config.GetScrapeConfigs, filepath.Glob -- v3.13.2). The
# base Compose file mounts nothing under scrape.d/, so a host without
# docker-compose.gpu.yml has no `nvidia-gpu` job, no `up` series for it and
# therefore no AizzakScrapeTargetDown. docker-compose.gpu.yml mounts
# scrape-gpu.yml here, and the exporter becomes an ordinary target that rule
# covers. A matched file may hold `scrape_configs:` only (parsed strictly),
# and its job names must not repeat this file's.
scrape_config_files:
  - /etc/prometheus/scrape.d/*.yml
```

**`deploy/prometheus/scrape-gpu.yml`** (جديد):
```yaml
# Mounted into `prometheus` by docker-compose.gpu.yml only (server-gpu-plan.md
# 4.2); prometheus.yml's `scrape_config_files` glob finds it. An ORDINARY
# target -- no `tier: optional` -- so AizzakScrapeTargetDown fires the moment
# the exporter dies on a GPU host.
scrape_configs:
  - job_name: nvidia-gpu
    static_configs:
      - targets: ["nvidia-gpu-exporter:9835"]
```

**`deploy/prometheus/test-rules.sh`:**
- **الفحص 4 يصير فحصَين،** والرأس يقول «Five checks».
- **صورة promtool** هي ما يثبّته `docker-compose.yml`، كما اليوم.
- **مسار الربط في 4ب** هو حرفيّاً مسار `docker-compose.gpu.yml`، ويحرس ذلك اختبار وحدة (§5، AC‑4.3).

```bash
echo "== promtool check config, no GPU overlay (${prom_image})"
docker run --rm \
  -v "$PWD/deploy/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
  -v "$PWD/deploy/prometheus/alerts.yml:/etc/prometheus/alerts.yml:ro" \
  --entrypoint promtool "$prom_image" check config /etc/prometheus/prometheus.yml

echo "== promtool check config, with docker-compose.gpu.yml's scrape file"
docker run --rm \
  -v "$PWD/deploy/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
  -v "$PWD/deploy/prometheus/alerts.yml:/etc/prometheus/alerts.yml:ro" \
  -v "$PWD/deploy/prometheus/scrape-gpu.yml:/etc/prometheus/scrape.d/gpu.yml:ro" \
  --entrypoint promtool "$prom_image" check config /etc/prometheus/prometheus.yml
```

**`tier: optional`** يبقى لـ`cadvisor` وحده، والقاعدة الجديدة في `test_observability_stack_wiring.py:270-289`:
```python
profiled = {
    name
    for name, service in compose["services"].items()
    if set(service.get("profiles") or ()) - {"monitoring"}
}
```
والمعنى: الأهداف ذات `tier: optional` هي بالضبط الخدمات المكشوطة خلف profile **غير** `monitoring`. فالخدمة خلف `monitoring` وحده لا تغيب ما دام Prometheus، الذي في الـprofile نفسه، يعمل.

### 3‑هـ) التنبيهان وحالات promtool

**`deploy/prometheus/alerts.yml`.** مجموعةٌ جديدة قبل `aizzak-meta`:
```yaml
  # ── The GPU (server-gpu-plan.md 4.2, docs/delivery/monitoring-profile/) ──
  # Both read nvidia_gpu_exporter (docker-compose.gpu.yml, profile
  # `monitoring`). On a host without the GPU overlay neither has a series, so
  # both stay inactive -- an absent card is not an alert. A dead exporter on a
  # GPU host is AizzakScrapeTargetDown{job="nvidia-gpu"}. One alert per card:
  # the series carry `uuid`, and nothing here aggregates them. No join on
  # nvidia_smi_gpu_info: a missing info series must not disarm the rule.
  - name: aizzak-gpu
    rules:
      - alert: AizzakGpuMemoryHigh
        expr: >-
          nvidia_smi_memory_used_bytes{job="nvidia-gpu"}
            / (nvidia_smi_memory_total_bytes{job="nvidia-gpu"} > 0)
          > 0.90
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "GPU memory is {{ $value | humanizePercentage }} used (card {{ $labels.uuid }})"
          runbook_url: "https://github.com/anastawahia/aizzak/blob/master/docs/runbooks/alerts.md#aizzakgpumemoryhigh"
          description: >-
            The LLM and the embedding service share this card's memory. Past
            the ceiling Ollama refuses to load the model or moves layers to
            the CPU, and every chat turn slows several-fold with no error.
            Ollama keeps a loaded model resident (OLLAMA_KEEP_ALIVE), so a
            high steady level UNDER the threshold is normal, not a leak.
          reason: >-
            0.90 is step 2.6's own acceptance bound (server-gpu-plan.md 2.6:
            GPU memory stays under 90%); measured 0.35-0.37 of 8 GB under that
            step's load (gpu-compose P-6). `for: 5m` ignores a model swap's
            transient. `total > 0` drops a failed reading instead of dividing
            by zero, so NaN never fires it.
          response: >-
            `nvidia-smi` on the host (which processes hold the memory), then
            `docker compose exec ollama ollama ps` (model, size, share on the
            GPU), then the table in docs/design/08-local-runbook.md 2-ي.
            Changing the model or OLLAMA_NUM_PARALLEL is a human decision.
      - alert: AizzakGpuTemperatureHigh
        expr: nvidia_smi_temperature_gpu{job="nvidia-gpu"} > 85
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "GPU at {{ $value }} C for five minutes (card {{ $labels.uuid }})"
          runbook_url: "https://github.com/anastawahia/aizzak/blob/master/docs/runbooks/alerts.md#aizzakgputemperaturehigh"
          description: >-
            The card has stayed above 85 C for five minutes. Near its limit
            the driver lowers the clocks, and generation slows with no error
            anywhere in the platform.
          reason: >-
            85 C sits near the common slowdown point of NVIDIA cards (owner
            decision Q-5). Where the driver reports it, the exporter also
            reads the card's own margin (nvidia_smi_temperature_gpu_tlimit);
            the threshold is re-tuned from it after step 7.3. `for: 5m`
            ignores a burst of load.
          response: >-
            `nvidia-smi -q -d TEMPERATURE,PERFORMANCE` on the host (the
            temperature, the slowdown thresholds, the active throttle
            reasons), then `docker compose exec ollama ollama ps` for what is
            running. Cooling is the provider's; reducing load is a human
            decision.
```

**`deploy/prometheus/alerts.test.yml`.** سبع حالات بعد قسم `aizzak-postgres`:
- **الحالة 1** تثبت NFR‑9: الاشتعال عند 5m30s، أي `for:` + 30 ث، والسلاسل فوق الحدّ من الثانية 0.
- **والحالتان 6 و7** هما AC‑4.3: شكل جهازٍ بلا كرت، وشكل جهازٍ بكرت.

```yaml
  # ── aizzak-gpu ──────────────────────────────────────────────────────────
  - name: two cards at 95 percent of their memory fire once each within for + 30s
    interval: 15s
    input_series:
      - series: 'nvidia_smi_memory_used_bytes{job="nvidia-gpu",instance="nvidia-gpu-exporter:9835",uuid="aaaa"}'
        values: "7.6e9x40"
      - series: 'nvidia_smi_memory_total_bytes{job="nvidia-gpu",instance="nvidia-gpu-exporter:9835",uuid="aaaa"}'
        values: "8e9x40"
      - series: 'nvidia_smi_memory_used_bytes{job="nvidia-gpu",instance="nvidia-gpu-exporter:9835",uuid="bbbb"}'
        values: "22.8e9x40"
      - series: 'nvidia_smi_memory_total_bytes{job="nvidia-gpu",instance="nvidia-gpu-exporter:9835",uuid="bbbb"}'
        values: "24e9x40"
    alert_rule_test:
      - eval_time: 4m
        alertname: AizzakGpuMemoryHigh
        exp_alerts: []
      - eval_time: 5m30s
        alertname: AizzakGpuMemoryHigh
        exp_alerts:
          - exp_labels: {severity: warning, job: nvidia-gpu, instance: "nvidia-gpu-exporter:9835", uuid: aaaa}
          - exp_labels: {severity: warning, job: nvidia-gpu, instance: "nvidia-gpu-exporter:9835", uuid: bbbb}

  - name: 85 percent for ten minutes, a four-minute burst and a failed reading are silent
    interval: 15s
    input_series:
      - series: 'nvidia_smi_memory_used_bytes{job="nvidia-gpu",instance="nvidia-gpu-exporter:9835",uuid="aaaa"}'
        values: "6.8e9x40"
      - series: 'nvidia_smi_memory_total_bytes{job="nvidia-gpu",instance="nvidia-gpu-exporter:9835",uuid="aaaa"}'
        values: "8e9x40"
      - series: 'nvidia_smi_memory_used_bytes{job="nvidia-gpu",instance="nvidia-gpu-exporter:9835",uuid="bbbb"}'
        values: "7.6e9x15 4e9x24"
      - series: 'nvidia_smi_memory_total_bytes{job="nvidia-gpu",instance="nvidia-gpu-exporter:9835",uuid="bbbb"}'
        values: "8e9x40"
      - series: 'nvidia_smi_memory_used_bytes{job="nvidia-gpu",instance="nvidia-gpu-exporter:9835",uuid="cccc"}'
        values: "1e9x40"
      - series: 'nvidia_smi_memory_total_bytes{job="nvidia-gpu",instance="nvidia-gpu-exporter:9835",uuid="cccc"}'
        values: "0x40"
    alert_rule_test:
      - eval_time: 6m
        alertname: AizzakGpuMemoryHigh
        exp_alerts: []
      - eval_time: 10m
        alertname: AizzakGpuMemoryHigh
        exp_alerts: []

  - name: a card at 88 C for six minutes fires, one at 80 C does not
    interval: 15s
    input_series:
      - series: 'nvidia_smi_temperature_gpu{job="nvidia-gpu",instance="nvidia-gpu-exporter:9835",uuid="aaaa"}'
        values: "88x40"
      - series: 'nvidia_smi_temperature_gpu{job="nvidia-gpu",instance="nvidia-gpu-exporter:9835",uuid="bbbb"}'
        values: "80x40"
    alert_rule_test:
      - eval_time: 4m
        alertname: AizzakGpuTemperatureHigh
        exp_alerts: []
      - eval_time: 5m30s
        alertname: AizzakGpuTemperatureHigh
        exp_alerts:
          - exp_labels: {severity: warning, job: nvidia-gpu, instance: "nvidia-gpu-exporter:9835", uuid: aaaa}

  - name: a three-minute burst to 90 C is silent
    interval: 15s
    input_series:
      - series: 'nvidia_smi_temperature_gpu{job="nvidia-gpu",instance="nvidia-gpu-exporter:9835",uuid="aaaa"}'
        values: "90x11 70x29"
    alert_rule_test:
      - eval_time: 6m
        alertname: AizzakGpuTemperatureHigh
        exp_alerts: []

  - name: a card that reports no temperature is silent
    interval: 15s
    input_series:
      - series: 'nvidia_smi_memory_total_bytes{job="nvidia-gpu",instance="nvidia-gpu-exporter:9835",uuid="aaaa"}'
        values: "8e9x40"
    alert_rule_test:
      - eval_time: 10m
        alertname: AizzakGpuTemperatureHigh
        exp_alerts: []

  - name: a host without the GPU overlay has no GPU series, no GPU alert and no GPU target down
    interval: 15s
    input_series:
      - series: 'up{job="node",instance="node-exporter:9100"}'
        values: "1x40"
    alert_rule_test:
      - eval_time: 10m
        alertname: AizzakGpuMemoryHigh
        exp_alerts: []
      - eval_time: 10m
        alertname: AizzakGpuTemperatureHigh
        exp_alerts: []
      - eval_time: 45s
        alertname: AizzakScrapeTargetDown
        exp_alerts: []

  - name: a dead GPU exporter on a GPU host is an ordinary target down
    interval: 15s
    input_series:
      - series: 'up{job="nvidia-gpu",instance="nvidia-gpu-exporter:9835"}'
        values: "0x10"
    alert_rule_test:
      - eval_time: 45s
        alertname: AizzakScrapeTargetDown
        exp_alerts:
          - exp_labels: {severity: warning, job: nvidia-gpu, instance: "nvidia-gpu-exporter:9835"}
      - eval_time: 45s
        alertname: AizzakGpuMemoryHigh
        exp_alerts: []
```

### 3‑و) `docs/runbooks/alerts.md`

**§0** (بعد «أين تصل التنبيهات»، قبل جدول الخطورة):
> **⚠️ ومسارُ التنبيه نفسُه خلف profile `monitoring`** (خطّة خادم الكرت 4.1).
> - Prometheus وAlertmanager و`alert-sink` (ومعها Grafana وLoki وAlloy والمصدّرات) لا تقلع إلّا و`monitoring` في `COMPOSE_PROFILES`: على جهاز المالك `COMPOSE_PROFILES=wsl-ollama,monitoring`، وعلى الخادم `local-llm,monitoring`.
> - **دونه لا مسار تنبيهٍ أصلاً،** ونبض `AizzakWatchdog` يغيب، ولا تنبيه يستطيع قول ذلك.
> - تحقّق: `docker compose ps prometheus alertmanager alert-sink`.
> - والخادم الصغير الذي يطفئ المراقبة عمداً يفحص `/health` من خارجه ([`deploy-server.md`](../deploy-server.md) §2).

**الفهرس §1.** سطران بعد `AizzakLlmCircuitOpen`:
```
| [`AizzakGpuMemoryHigh`](#aizzakgpumemoryhigh) | تحذير | ذاكرة الكرت ممتلئةٌ أكثر من 90% منذ 5 دقائق |
| [`AizzakGpuTemperatureHigh`](#aizzakgputemperaturehigh) | تحذير | حرارة الكرت فوق 85°م منذ 5 دقائق |
```

**§3، قسمان بعد `AizzakLlmCircuitOpen`،** على شكل `AizzakHostDiskHigh`.

**القسم الأوّل:** المرساة `<a id="aizzakgpumemoryhigh"></a>`، والعنوان `### \`AizzakGpuMemoryHigh\` — ذاكرة الكرت ممتلئةٌ أكثر من 90% منذ 5 دقائق`.
- **ماذا يعني:**
  - النموذج اللغويّ والتضمين يتقاسمان ذاكرة الكرت. فوق الحدّ يرفض Ollama تحميل النموذج، أو ينقل طبقاتٍ إلى المعالج، فتبطؤ المحادثة كثيراً دون خطأ.
  - الحدّ هو حدّ قبول 2.6، والمقيس على كرت 8 ج.ب 0.35–0.37.
  - ⚠️ Ollama يُبقي النموذج محمَّلاً (`OLLAMA_KEEP_ALIVE`)، فاستعمالٌ ثابتٌ مرتفعٌ **تحت** الحدّ طبيعيّ.
  - التنبيه لكلّ كرت، بتسمية `uuid`.
- **الخطوات (قراءةٌ فقط أوّلاً):**
  1. على المضيف، ```nvidia-smi```.
  2. ```docker compose exec ollama ollama ps```.
  3. جدول ما يسكن الكرت في `docs/design/08-local-runbook.md` §2‑ي: نموذجٌ أكبر من المحسوب؟ أو `OLLAMA_NUM_PARALLEL` أعلى؟ أو نسخة تضمينٍ ثانية؟
  4. تغيير النموذج أو `OLLAMA_NUM_PARALLEL` قرارٌ بشريّ (قرار 3).
- **كيف تعرف أنّه زال:** تحت 90% خمس دقائق، وسطر `resolved`، وصفّ GPU في لوحة capacity تحت الخطّ الأحمر.

**القسم الثاني:** المرساة `<a id="aizzakgputemperaturehigh"></a>`، والعنوان `### \`AizzakGpuTemperatureHigh\` — حرارة الكرت فوق 85°م منذ 5 دقائق`.
- **ماذا يعني:** قرب حدّه يخفض التعريف السرعة، فيبطؤ التوليد بلا خطأ. والحدّ يُضبط بعد 7.3 من حدّ الكرت نفسه.
- **الخطوات:**
  1. ```nvidia-smi -q -d TEMPERATURE,PERFORMANCE``` على المضيف.
  2. ```docker compose exec ollama ollama ps```.
  3. جدول §2‑ي في `08-local-runbook.md`.
  4. التبريد عند المزوّد، وخفض الحمل قرارٌ بشريّ.
- **كيف تعرف أنّه زال:** تحت 85°م خمس دقائق، وسطر `resolved`.

**ثلاثة تعديلاتٍ صغيرة:**
- **`AizzakScrapeTargetDown`، الخطوة 2.** سطرٌ يقول: إن كان `job` هو `nvidia-gpu` فـ`docker compose ps nvidia-gpu-exporter` و`docker compose logs --since 15m nvidia-gpu-exporter`. ورسالة «could not select device driver» تعني أداة NVIDIA (`deploy/gpu-preflight.sh`).
- **`AizzakWatchdog`، الخطوة 2.** تضيف: إن لم يكن `monitoring` في سطر `COMPOSE_PROFILES` في `.env` فلن يُقلعها `up -d` التالي.
- **§6.** سطران قبل «(البقيّة)»: `| \`AizzakGpuMemoryHigh\` | — | — | بانتظار التجربة في 7.3 | — |`، ومثله لـ`AizzakGpuTemperatureHigh`.

### 3‑ز) لوحة Grafana: `deploy/grafana/dashboards/aizzak-capacity.json`

**الصفّ:** `{"id": 31, "type": "row", "title": "GPU — memory, load, temperature (server-gpu-plan 4.2; GPU hosts only)", "collapsed": false, "gridPos": {"h": 1, "w": 24, "x": 0, "y": 68}, "panels": []}`.

**الشكل:** ثلاث لوحاتٍ فرعيّة `timeseries` بشكل لوحة 22 (`Redis memory`):
- `datasource {"type":"prometheus","uid":"aizzak-prometheus"}` في اللوحة وفي الهدف.
- و`editorMode: code`، و`range: true`.
- ومفتاح الإيضاح جدولٌ بـ`lastNotNull` و`max`.

**الربط بـ`nvidia_smi_gpu_info`:** في اللوحة وحدها لاسم الكرت، ولا ربط في التنبيهات.

| id | العنوان | `expr` | الوحدة والحدود | `gridPos` | `description` (مختصرة) |
|---|---|---|---|---|---|
| 32 | `GPU memory used (of total)` | `nvidia_smi_memory_used_bytes{job="nvidia-gpu"} / (nvidia_smi_memory_total_bytes{job="nvidia-gpu"} > 0) * on (uuid) group_left (name, index) nvidia_smi_gpu_info{job="nvidia-gpu"}` | `percentunit`، min 0، max 1، عتبة حمراء 0.9 بـ`thresholdsStyle: {"mode": "line"}` | `{h:8,w:8,x:0,y:69}` | Share of each card's memory in use (nvidia-smi via nvidia_gpu_exporter). The red line is AizzakGpuMemoryHigh's 90%, step 2.6's own bound. Ollama keeps its model resident, so a flat line under it is normal. "No data" on a host without docker-compose.gpu.yml (this WSL machine) is expected |
| 33 | `GPU load` | `nvidia_smi_utilization_gpu_ratio{job="nvidia-gpu"} * on (uuid) group_left (name, index) nvidia_smi_gpu_info{job="nvidia-gpu"}` | `percentunit`، min 0، max 1 | `{h:8,w:8,x:8,y:69}` | Share of time the card was busy (utilization.gpu). It rises with generation and embedding batches; it is not a memory measure. "No data" without the GPU overlay |
| 34 | `GPU temperature` | `nvidia_smi_temperature_gpu{job="nvidia-gpu"} * on (uuid) group_left (name, index) nvidia_smi_gpu_info{job="nvidia-gpu"}` | `celsius`، عتبة حمراء 85 خطّاً | `{h:8,w:8,x:16,y:69}` | The red line is AizzakGpuTemperatureHigh's 85 C. Some virtual cards report no temperature: then only this panel says "No data" |

- **`legendFormat`:** `{{index}}: {{name}}` في الثلاث.
- **الإضافة في آخر المصفوفة،** فلا يتحرّك `gridPos` قائم ولا معرّف.

### 3‑ح) الوثائق والقالب

**`.env.example`** (لا يقرؤه المعماريّ: الصلاحيّات تمنعه، والمحتوى مأخوذٌ من `test_compose_local_llm.py:364-367`):
- السطر `#COMPOSE_PROFILES=wsl-ollama` يصير `#COMPOSE_PROFILES=wsl-ollama,monitoring`.
- والسطر `#COMPOSE_PROFILES=local-llm` يصير `#COMPOSE_PROFILES=local-llm,monitoring`.
- وفوقهما سطر تعليقٍ واحد:
  ```
  # `monitoring` = the eleven monitoring services (on by default, decision 7); a small server may drop it -- then there are no alerts at all.
  ```

**`docs/stack-commands.md`:**
- **الأمر 1 (`:117-122`):**
  - **سطر المالك:** `COMPOSE_PROFILES=wsl-ollama,monitoring`، بدل جملة «وبعد المرحلة 4.1 يصير…».
  - **تحذير النسيان،** على نمط تحذير الجسر: نسيان `monitoring` يُطفئ Prometheus وAlertmanager و`alert-sink` بصمت عند أوّل إعادة إنشاء، فيغيب نبض `AizzakWatchdog`.
  - **جملة `--profile X`** تبقى، ومثالها `--profile wsl-ollama --profile monitoring` أو `COMPOSE_PROFILES=wsl-ollama,monitoring,X docker compose …`.
  - **ومن له كتلة `aizzak auto-size`** يعدّل السطر **أوّلاً**، ثمّ يعيد `deploy/auto_size.py --role dev`.
- **الأمر 41 وخليّة الجدول 41:**
  - `COMPOSE_PROFILES=local-llm,monitoring`.
  - بندٌ: «**الخادم الصغير وحده يحذف `monitoring`**: يستردّ حصّتها (§2‑ز)، ولا تنبيهات بعدها، فافحص `/health` من الخارج. وحكم الميزانيّة دونها: `deploy/resource-budget.sh --without-monitoring --size small`».
  - وبند «ملفّ الكرت يمسّ ثلاث خدمات» يصير: «… ومع `monitoring` يضيف `nvidia-gpu-exporter` ويربط لـ`prometheus` ملفّ أهدافه».
- **سطر «آخر مطابقة» (`:11`):** «2026‑10‑1x (`monitoring-profile`): سطر المالك `COMPOSE_PROFILES=wsl-ollama,monitoring`، و`--without-monitoring`، ومصدّر الكرت».

**`docs/quickstart.md:80-83`:**
- السطران يحملان `,monitoring`.
- وتحذير «ونسيان سطر `COMPOSE_PROFILES`…» يذكر أيضاً أنّ نسيان `monitoring` يُسقط مسار التنبيه.
- ثمّ ثلاث جمل:
  - الخادم الصغير وحده يحذفها فلا تنبيهات (`--without-monitoring`).
  - وجملة `--profile X` القائمة.
  - و«من له كتلة `aizzak auto-size` يعيد `deploy/auto_size.py --role dev` بعد تعديل السطر».

**`docs/deploy-server.md`:**
- **جملة الرأس:** «فيه الآن قسم المتطلّبات، وقسم المراقبة، وقائمة الأمان».
- **«## 2) المراقبة والتنبيهات»** قبل `---` و«قائمة الأمان المؤجّلة». **بلا صفّ جدولٍ يبدأ برقم،** وفيه أربع نقاط:
  1. **مفعّلةٌ افتراضيّاً** (قرار 7): `COMPOSE_PROFILES=local-llm,monitoring`.
  2. **الخادم الصغير وحده يحذفها.** ⚠️ **دونها لا تنبيهات أصلاً:**
     - افحص `/health` **من خارج الخادم**: خدمة رصدٍ خارجيّة تطلب `https://<النطاق>/health` كلّ دقيقة، وتنبّه على غير 200. والاختيار في 7.4.
     - و`install.sh` (5.2) سيطبع ذلك.
  3. **حكم الميزانيّة دونها:** `deploy/resource-budget.sh --without-monitoring --size <الحجم>`.
  4. **مع ملفّ الكرت والمراقبة:** `nvidia-gpu-exporter`، وهدف `nvidia-gpu`، وتنبيها الكرت، وصفّ GPU في لوحة capacity.
- **صفّ `ث‑6`:**
  ```
  | ث‑6 | صورة طرفٍ ثالث لمصدّر الكرت: `utkuozdemir/nvidia_gpu_exporter:1.15.1`، مثبّتةً بالبصمة `sha256:7aee2d42…3d087` (مشرفٌ فرد، 14 إصداراً بين 2026‑06‑22 و2026‑09‑02)، بحجز `utility` (عُقد NVIDIA ومكتبة NVML داخلها). لم يُتحقَّق من توقيع cosign (monitoring-profile، Q‑4) | البصمة تمنع تغيّر ما يعمل بلا تعديلٍ في المستودع، لكنّ كلّ تحديثٍ لها ثقةٌ بما نشره المشرف. والمنفذ الداخليّ يكشف هويّة الكرت (UUID، الرقم التسلسليّ، عنوان PCI) لكلّ خدمةٍ على الشبكة | التحقّق من توقيع cosign قبل كلّ تحديثٍ للبصمة، أو نسخ الصورة إلى `anastawahia/aizzak` بوسمٍ ببادئة. أو `dcgm-exporter` الرسميّ بعد 7.3 إن كان كرت الخادم من فئة مراكز البيانات |
  ```

**`docs/design/08-local-runbook.md`:**

- **جدول الخدمات** (قبل صفّ «خدمات لمرّةٍ واحدة»):
  ```
  | `prometheus` · `grafana` · `alertmanager` · `alert-sink` · `loki` · `alloy` · المصدّرات الخمسة | صورٌ مثبّتة في `docker-compose.yml` | Grafana على `127.0.0.1:13000`، والبقيّة داخليّة | المراقبة والتنبيه والسجلّات — profile `monitoring` (مفعّل افتراضيّاً، قرار 7: `wsl-ollama,monitoring` على جهاز المالك، و`local-llm,monitoring` على الخادم) |
  | `nvidia-gpu-exporter` | `utkuozdemir/nvidia_gpu_exporter:1.15.1` (بالبصمة) | 9835 داخليّاً | أرقام الكرت من `nvidia-smi` — في `docker-compose.gpu.yml` وحده، profile `monitoring` |
  ```
- **§2‑ز، فقرةٌ** بعد فقرة «`gpu-compose` (2.3)» (`:488`):
  > **⚠️ و`monitoring-profile` (4.1 من خطّة خادم الكرت) وضعت خدمات المراقبة الإحدى عشرة خلف profile `monitoring`، ولم يتحرّك رقم.**
  > - المراقبة **مفعّلةٌ افتراضيّاً** (قرار 7)، فالدفتر وجدول الأحجام و`deploy/resource-budget.sh` يعدّونها كما كانوا.
  > - حصّتها بحدود اليوم: `large` **3.60 vCPU و4.66 GB** (4768m)، و`medium` **2.35 و2.41** (2464m)، و`small` **1.40 و1.66** (1696m). ويضيف إليها وضعُ الكرت `nvidia-gpu-exporter` (4.2) بـ0.25 vCPU و64m.
  > - والحكم على خادمٍ بلاها صريح: `deploy/resource-budget.sh --without-monitoring [--size …]` يطبع 32.00/52.62 للمرجعيّ، و34.00/52.88 و17.00/26.00 و10.10/12.56 للأحجام، ويقول إنّه لا تنبيهات دونها.
  > - و`--block` يتبع سطر `# profiles:` في الكتلة كما يتبعه Compose.
  > - وملفّات الأحجام لا تتغيّر، و`small` يبقى يعدّ المراقبة حتّى قياس 7.3 (Q‑6).
- **كتلة الأوامر:** `deploy/resource-budget.sh --without-monitoring --size small   # خادمٌ صغيرٌ بلا مراقبة (ولا تنبيهات)`.
- **«ما لا يُحسَب»** (`:522`)، جملةٌ تُضاف: «**والمراقبة (`monitoring`) ليست منها:** خلف profile منذ 4.1، لكنّها مفعّلةٌ افتراضيّاً فتُعدّ، ولا يُخرجها إلّا `--without-monitoring` أو كتلةٌ لا يحملها سطر `# profiles:` فيها». وسطر `--with-profiles` في الأوامر يبقى بقائمته الستّ.
- **جدول الأحجام بعد 4.2:** `large` 37.85 | 57.59، و`medium` 19.60 | 28.47، و`small` 11.75 | 14.28. ومعه جملة: «4.2: ‏`nvidia-gpu-exporter` بحدَّين ثابتين في ملفّ الكرت (0.25 vCPU و64m)، فهامش `large` صار 6.4 م.ب».
- **جدول §5:** `| \`nvidia-gpu-exporter\` | **الكرت من داخل الحاوية**: \`nvidia-smi --query-gpu=uuid\`، لأنّ الصورة distroless بلا shell ولا \`wget\`؛ ومنفذ المصدّر يغطّيه \`AizzakScrapeTargetDown\` |`.

**`docs/server-gpu-plan.md`:**
- **§3 القاعدة 1، بندٌ ثالث** بعد بند 3.5:
  > - **استثناءٌ ضيّق ثالث ([4.1](#41-profile-للمراقبة)، قرار المالك 2026‑10‑17، Q‑2 في `docs/delivery/monitoring-profile/`):**
  >   - خدمات المراقبة الإحدى عشرة خلف profile `monitoring`، فجهاز المالك يحتاج `monitoring` في سطر `COMPOSE_PROFILES`: **`COMPOSE_PROFILES=wsl-ollama,monitoring`**.
  >   - يعدّله المالك بنفسه، ويثبت بعد الدمج أنّ قائمة الخدمات وبصمات `config --hash` للإحدى عشرة لم تتغيّر، وأنّ `up -d` لم يُعد إنشاء أيٍّ منها.
  >   - **ونسيانه يُطفئ مسار التنبيه كلّه بصمت.**
- **§4 الصفّ 4.1:** «🟡 2026‑10‑1x (الفرع `feat/monitoring-profile`): الـprofile والحارس والميزانيّة بالاختبارات. بانتظار المراجعة والدمج؛ ثمّ AC‑3.3 (المالك يعدّل سطره ويثبت تطابق البصمات)، وAC‑1.9 (7.3)».
- **§4 الصفّ 4.2:** «🟡 … المصدّر والهدف والتنبيهان واللوحة بالاختبارات و`test-rules.sh`. بانتظار المراجعة والدمج؛ وAC‑4.6 وAC‑6.3 محلّيّاً (Q‑10) أو في 7.3، وAC‑5.5 في 7.3».

**`docs/server-gpu-summary.html`:**
- **مقالا ٤٫١ و٤٫٢:** `class="step partial"`، ووسم «على الفرع · تنتظر الدمج».
- **الشريط:** `flex:8/9/12`، والنصوص «٨ مكتملة / ٩ جارية / ١٢ لم تبدأ».
- **`aria-label`:** «تسعٌ وعشرون خطوة: ثمانٍ مكتملة وتسعٌ جارية واثنتا عشرة لم تبدأ»، والمفتاح بالأرقام نفسها.
- **إن دُمج PR ‏#13 أوّلاً،** تُعاد الأعداد عند حلّ التعارض (Q‑11).

### 3‑ط) التواقيع

```python
# tests/unit/support_compose.py
DEFAULT_PROFILES: Final[frozenset[str]] = frozenset({"monitoring"})   # decision 7: on by default
# MODE_PROFILES is NOT changed: test_auto_size._independent_totals unions it with the plan's own
# profiles, and auto_size.py follows COMPOSE_PROFILES -- folding monitoring into it would make
# the test count what Compose does not run.

# tests/unit/test_resource_budget.py
def _classify(compose: dict | None = None,
              profiles: frozenset[str] = DEFAULT_PROFILES) -> tuple[dict, dict, dict]
def _size_totals(size: str,
                 profiles: frozenset[str] = DEFAULT_PROFILES) -> tuple[float, float, float]
    # classifies with MODE_PROFILES[parsed.mode] | profiles

# tests/unit/test_auto_size.py
def _plan(cpus: float, gb: float, role: str = "server", mode: str = "cpu",
          profiles: str = "monitoring") -> Any      # decision 7: a server runs monitoring

# tests/unit/test_monitoring_profile.py (new)
MONITORING: Final[frozenset[str]]          # the eleven of FR-1
GPU_MONITORING: Final[frozenset[str]] = frozenset({"nvidia-gpu-exporter"})
INTERNAL_EDGES: Final[frozenset[tuple[str, str]]] = frozenset(
    {("grafana", "prometheus"), ("alertmanager", "alert-sink"), ("alloy", "loki")})
OUTBOUND_EDGES: Final[frozenset[tuple[str, str]]] = frozenset(
    {("prometheus", "app"), ("pgbouncer-exporter", "pgbouncer"),
     ("redis-stream-exporter", "redis-stream"), ("redis-cache-exporter", "redis-cache"),
     ("postgres-exporter", "postgres")})
_SPEC_ON_MASTER_SHA256: Final[dict[str, str]]   # AC-1.1, values in §5
_HOST_REF: Final[re.Pattern[str]]               # (?<![\w.-])(<the 12 names, longest first>)(?=:)
def _spec_digest(spec: Mapping[str, Any]) -> str
    # sha256 of json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
def _services() -> dict[str, Any]               # load_compose(files=(COMPOSE, GPU_OVERLAY))["services"]
def _edges(services: Mapping[str, Any]) -> set[tuple[str, str]]   # depends_on, list or dict form
def _compose(*args: str, files: Sequence[Path] = (COMPOSE,), profiles: Sequence[str] = (),
             env_file: Path = ENV_EXAMPLE,
             project_directory: Path | None = None) -> subprocess.CompletedProcess[str]
    # env = os.environ minus COMPOSE_FILE and COMPOSE_PROFILES; cwd = REPO_ROOT; timeout 120

# tests/unit/test_compose_gpu_overlay.py
_EXPORTER_IMAGE: Final = ("utkuozdemir/nvidia_gpu_exporter:1.15.1"
                          "@sha256:7aee2d42836ad29d4adb2722b7cfdc806ec9cc8d45fd0b8971ebbad2d9f3d087")
_SCRAPE_MOUNT: Final = "./deploy/prometheus/scrape-gpu.yml:/etc/prometheus/scrape.d/gpu.yml:ro"
def _config(*files: Path, profiles: Sequence[str] = ()) -> dict[str, Any]   # replaces `profile: str | None`

# tests/unit/test_observability_stack_wiring.py
_GPU_COMPOSE: Final[Path]; _GPU_SCRAPE: Final[Path]   # docker-compose.gpu.yml, deploy/prometheus/scrape-gpu.yml
GPU_EXPORTER_METRICS: Final = frozenset({
    "nvidia_smi_memory_used_bytes", "nvidia_smi_memory_total_bytes",
    "nvidia_smi_utilization_gpu_ratio", "nvidia_smi_temperature_gpu", "nvidia_smi_gpu_info"})
    # names as nvidia_gpu_exporter v1.15.1 emits them (internal/integration/testdata)
```

### 3‑ي) الأرقام قبل وبعد (مقيسةٌ بالسكربت على `master` وعلى النموذج، والخروج 0 في كلّها)

**المجاميع (vCPU / GB):**

| الحالة | `master` | بعد 4.1 | بعد 4.2 | `--without-monitoring` (بعد 4.1 و4.2) |
|---|---|---|---|---|
| المرجعيّ `--host-cpus 32 --host-memory-gb 64` | 35.60 / 57.28 | **35.60 / 57.28** | 35.60 / 57.28 | 32.00 / 52.62 |
| `--size large` | 37.60 / 57.53 | **37.60 / 57.53** | 37.85 / 57.59 (هامش 6.4 م.ب) | 34.00 / 52.88 |
| `--size medium` | 19.35 / 28.41 | **19.35 / 28.41** | 19.60 / 28.47 | 17.00 / 26.00 |
| `--size small` | 11.50 / 14.22 | **11.50 / 14.22** | 11.75 / 14.28 | 10.10 / 12.56 |

**التصنيف:**
- **الافتراضيّ:** 28 دائمة، و5 لمرّة، و6 بـprofile.
- **ودون المراقبة:** 17، و5، و17.

**`auto_size.py` بعد 4.2** (الدور `server`، والمراقبة مفعّلة):

| الجهاز | الوضع | `k*` | vCPU | GB |
|---|---|---|---|---|
| 8/16 | gpu | 3 | 11.75 | 14.38 |
| 12/24 | gpu | 196 | 15.15 | 21.58 |
| 16/32 | gpu | 353 | 20.07 | 28.79 |
| 24/48 | gpu | 685 | 28.26 | 43.20 |
| 32/64 | gpu | 1000 | 37.85 | 57.59 |
| 64/24 | gpu | 196 | 32.85 | 21.58 |

- **صفوف `cpu`:** لا تتغيّر.
- **الرفض بوضع الكرت:**
  - 8/15: المطلوب 14.28، والمسموح 13.50، والناقص 0.78.
  - والعتبة: 15.86 مرفوض، و15.87 مقبول. كانتا 15.79 و15.8.
- **دون المراقبة على 32/64:** `cpu` ‏32.00/49.62، و`gpu` ‏34.00/52.88. ومعها `cpu` ‏35.60/54.28.
- **دور `dev` على 10/12.67** بـ`wsl-ollama,monitoring`: ‏12.44 و23.22 و1.83x، كما على `master`.

## 4. البيانات

**لا تغيير:** لا جداول، ولا Alembic، ولا RLS، ولا Qdrant. **مرحلة `data-engineer` تُتخطّى.**

- **ما يتغيّر هو إعداد التشغيل وحده.**
- **وهو متوافقٌ مع ما قبله** على جهازٍ يضيف `monitoring` إلى سطره، لأنّ البصمات متطابقة.
- **الرجوع:** `git revert`. والسطر `COMPOSE_PROFILES=wsl-ollama,monitoring` غير ضارٍّ على `master` القديم؛ قيس: 34 خدمة في الحالتين.

## 5. خطة الاختبار المقترحة

**القواعد العامّة:**
- **كلّ معيار Must آليّ ولا يحتاج المكدّس الحيّ** (NFR‑3).
- **اختبارات [config]:**
  - تمرّر `--env-file .env.example`.
  - تحذف `COMPOSE_FILE` و`COMPOSE_PROFILES` من البيئة.
  - تُتخطّى إن غاب Docker.
- **ووظيفة `integration` في CI تشغّل `tests/unit` بـ`COMPOSE_FILE` من ثلاثة ملفّات** (`.github/workflows/ci.yml:95`). لذلك:
  - يُصلَح `_compose` في `test_monitoring_host_postgres_acceptance.py`، وهو اليوم لا ينظّف البيئة.
  - و`_run`/`_budget` في اختبارَي الميزانيّة يثبّتان `COMPOSE_FILE` كما اليوم.
- **زمن الرسم ~0.1 ث** (قيس). الاختبارات الجديدة نحو 15 رسماً، فالزيادة ≪ 15 ث (NFR‑7).

**لقطة AC‑1.1** (محسوبة على `master` `47225ff`، ومطابقة للنموذج بعد حذف `profiles`):
```python
_SPEC_ON_MASTER_SHA256 = {
    "prometheus": "5d8e624a46134d5b404406af9df1ab2cb2201674090b3f13dfc7117ae5910ffa",
    "grafana": "db448ba6b1be7232de501060ac3c8bd35f2588c47454266662e37ccee0d1b0d7",
    "alertmanager": "601887332e1edbfba1a9fab3cd306b4e99500add73f9c6cd92ffb6b40b863eb5",
    "alert-sink": "7ac34e2075124fed611f6fcf6464fa53d8ae5630c286c29485dc4a4c593d44f8",
    "pgbouncer-exporter": "130db28021dd78f3613029c4c3bfe1fe93ac85046591002dbd3c82b1adf24201",
    "redis-stream-exporter": "ac153e76b101a1c4c75101aa96b7c5d86a5d03c902a2a7d6d0ea8355d075b271",
    "redis-cache-exporter": "14c103a58f45485ff51135a9f5b41ac1f28a8a5b6190bef0d2787beef44bc97a",
    "node-exporter": "2371996456b840307ed43ddf3542323a911fc10adb6db43987ff4493088f1321",
    "postgres-exporter": "f36c986fac631ac63b556a5625e0835c0e8529baef6d3a78f32954e1fff86316",
    "loki": "120b387f19d4ebbc2ba32936b2b530341874b3774fd442692fd131acdb40b1dd",
    "alloy": "cc447e3de99857b188ec1ec94102f7bc6f232a8ab040a090d39a1cdc41d0f603",
}
```
**وشرط هذه اللقطة:** تعديلٌ لاحق لإحدى الخدمات يحدّث بصمتها في الالتزام نفسه. وdocstring الاختبار يقول ذلك، على نمط `_BRIDGE_ON_MASTER`.

| AC | المستوى | المكان والاختبار |
|---|---|---|
| 1.1 | وحدة | `test_monitoring_profile.py`: `test_exactly_the_eleven_carry_the_monitoring_profile` (في الملفّين معاً: الإحدى عشرة + `nvidia-gpu-exporter` في ملفّ الكرت، ولا غيرها). و`test_each_monitoring_service_is_unchanged_but_for_its_profile` (`_spec_digest` بعد حذف `profiles` = اللقطة). و`test_cadvisor_stays_behind_container_metrics_alone` |
| 1.2 | وحدة | `test_no_service_outside_monitoring_depends_on_it`: الرسالة تسمّي الحافّة. و`test_the_monitoring_edges_are_the_known_three_inside_and_five_out` (`INTERNAL_EDGES` و`OUTBOUND_EDGES` بالضبط، بالملفّين) |
| 1.3 | وحدة | `test_no_service_outside_monitoring_names_a_monitoring_host`: ‏`_HOST_REF` على `environment` و`command` و`entrypoint` و`healthcheck`. قيس على `master`: صفر. و`PROMETHEUS_MULTIPROC_DIR` لا يطابق لأنّ النمط حسّاسٌ لحالة الأحرف. و`test_the_log_anchor_is_still_json_file`. و`location /metrics`: `test_edge_hardening.py:333-354` القائم، ودليل الـPR: `git diff --exit-code master -- deploy/nginx src` |
| 1.4 | config | `test_without_the_profile_none_of_the_eleven_is_in_the_model`: ‏`config --services` = الخدمات بلا profile، وعددها 22. و`test_the_profile_adds_exactly_the_eleven`. و`config -q` يخرج 0 في الحالتين |
| 1.5 | config | `test_the_profile_key_does_not_change_any_config_hash`: نسخةٌ في `tmp_path` يُحذف منها السطر `    profiles: ["monitoring"]\n` (11 مرّة بالضبط)، وتُرسم بـ`--project-directory <repo>` حتّى تُحلّ مسارات الربط نفسها. ثمّ `config --hash <الإحدى عشرة>` بـ`--profile monitoring` على الأصل، وبلا profile على النسخة: متطابقة (قيس) |
| 1.6 | config | `test_monitoring_host_postgres_acceptance.py::test_compose_refuses_to_render_without_the_exporter_password`، مُعامَلاً بـ`profile ∈ {None, "monitoring"}` |
| 1.7 | وحدة | تعديلات §2 في الملفّات الستّة، وفيها `test_observability_stack_wiring.py`: مصدّرٌ من الإحدى عشرة يحمل `tier: optional` يُسقطه. ثمّ `pytest -rs` أخضر |
| 1.8 | محلّيّ (Q‑9) | QA، الإجراء (أ) أدناه |
| 1.9 | خادم | مؤجَّلٌ إلى 7.3 |
| 2.1 | وحدة | `test_resource_budget.py::test_the_budget_counts_monitoring_by_default_and_says_so[ref,large,medium,small]`: المجاميع في §3‑ي، والخروج 0، وسطر `monitoring: counted (on by default, decision 7)` |
| 2.2 | وحدة | `test_without_monitoring_each_total_drops_by_the_elevens_limits[4]`: السكربت = `_classify(profiles=frozenset())`. والفرق 3.60/4768m، و2.35/2464m، و1.40/1696m، ومعه في وضع الكرت بعد 4.2: 0.25/64m للمصدّر. وسطر `NOT counted (--without-monitoring)` و`no alerts`. و`test_without_monitoring_conflicts_are_usage_errors` (رمز 2 مع `--block` ومع `--with-profiles`) |
| 2.3 | وحدة | `test_auto_size.py::test_the_budget_block_follows_the_blocks_profiles`: المولِّد بـ`--profiles monitoring,wsl-ollama` ثمّ بـ`--profiles wsl-ollama` (32/64، الدور `server`). في الأولى سطر `# profiles: monitoring,wsl-ollama` و`monitoring: counted (in the block's profiles)`. وفي الثانية `NOT counted (not in the block's profiles)`، وسطر `TOTAL` أقلّ بـ3.60 vCPU و4.66 GB |
| 2.4 | وحدة | `test_monitoring_is_a_known_profile_and_counts_only_when_named`: ‏`"monitoring" in az._known_profiles()`. و`_plan(32, 64, mode="cpu", profiles="")` = 32.00/49.62، وبـ`"monitoring"` = 35.60/54.28. ثمّ الاختبارات القائمة بـ`wsl-ollama,monitoring`: ‏11.72/11.40/0.32، و23.22/1.83x |
| 2.5 | وحدة | `test_the_patterns_actually_find_something` القائم (28/5/6 بـ`DEFAULT_PROFILES`). و`test_without_monitoring_the_standing_set_is_seventeen` (17/5/17). وسطرا `resources.standing_*` (35.60، و57.28) القائمان. و`test_the_script_and_this_module_agree_without_monitoring` |
| 2.6 | نصّ (وحدة) | `test_monitoring_profile.py::test_the_ledger_explains_monitoring_on_by_default`: مقطع `### 2‑ز`…`### 2‑ح` فيه «قرار 7»، و`--without-monitoring`، و`3.60` و`4.66`، و`2.35` و`2.41`، و`1.40` و`1.66`. وفقرة «ما لا يُحسَب» تذكر `monitoring` |
| 3.1 | نصّ (وحدة) | `test_both_guides_carry_the_monitoring_lines[stack-commands, quickstart]`. في كلٍّ: `COMPOSE_PROFILES=wsl-ollama,monitoring`، و`COMPOSE_PROFILES=local-llm,monitoring`، و`--without-monitoring` مع «لا تنبيهات»، والنمط ``--profile X`[^\n]*يحلّ محلّ(\*\*)? `COMPOSE_PROFILES` ``، و`deploy/auto_size.py --role dev`. و`test_gpu_compose_hygiene.py:293-308` القائم أخضر |
| 3.2 | وحدة | `test_compose_local_llm.py::test_env_example_offers_both_profiles_commented_only`: النمطان `^#COMPOSE_PROFILES=wsl-ollama,monitoring$` و`^#COMPOSE_PROFILES=local-llm,monitoring$`. و`test_env_example_activates_no_compose_selector` القائم |
| 3.3 | مالك | الإجراء (ج) أدناه |
| 3.4 | مالك، شرطيّ | الإجراء (ج)، الخطوة 6 |
| 3.5 | نصّ (وحدة) | `test_no_monitoring_means_no_alerts_is_said_where_it_is_read`. في §0 من `alerts.md`: «profile `monitoring`»، و`AizzakWatchdog`، و`docker compose ps prometheus alertmanager alert-sink`. وفي `deploy-server.md` «## 2) المراقبة والتنبيهات»: `/health`، و«من خارج الخادم»، و«لا تنبيهات» |
| 3.6 | نصّ (وحدة + مراجعة) | `test_the_plan_names_the_third_exception`: §3 فيه «استثناءٌ ضيّق ثالث» و`COMPOSE_PROFILES=wsl-ollama,monitoring`، وصفّا 4.1 و4.2 لم يعودا `⬜`. والملخّص HTML بالمراجعة |
| 4.1 | وحدة | `test_compose_gpu_overlay.py::test_the_gpu_exporter_is_pinned_internal_unprivileged_and_bounded`: القاموس كلّه، مفتاحاً مفتاحاً، كما في §3‑ج. ولا `ports` ولا `privileged` ولا `network_mode` ولا `pid` ولا `cap_add` ولا `devices` ولا `volumes`. و`test_the_gpu_exporter_logs_like_every_other_service`: ‏`logging` = `x-app-logging.logging` في الملفّ الأساسيّ. و`test_every_compose_image_is_pinned` القائم |
| 4.2 | وحدة + config | `test_the_overlay_has_only_services_and_exactly_five`. و`test_prometheus_gets_one_mount_and_nothing_else` (المدخل = `{"volumes": [_SCRAPE_MOUNT]}`). و`test_the_exporter_exists_only_with_the_monitoring_profile[(), (local-llm), (monitoring), (local-llm, monitoring)]`. و`test_the_overlay_changes_only_the_services_it_names[local-llm \| local-llm+monitoring]`: المتغيّر `{ollama, embedding, app}`، أو هو و`prometheus`، والمضاف `{nvidia-gpu-exporter}`. وفرق `prometheus` مدخلٌ واحد في `volumes` هدفه `/etc/prometheus/scrape.d/gpu.yml` و`read_only` |
| 4.3 | قاعدة + وحدة | `test-rules.sh`: الفحصان 4أ و4ب، وحالتا promtool 6 و7، ثمّ `test-rules: OK`. و`test_prometheus_alert_rules.py::test_the_rule_script_checks_the_config_with_and_without_the_gpu_file`: السكربت يربط `deploy/prometheus/scrape-gpu.yml` على الهدف نفسه الذي في `_SCRAPE_MOUNT`، و`check config` مرّتان |
| 4.4 | وحدة | `test_observability_stack_wiring.py`: `test_every_scrape_target_names_a_service_that_exists` (مهامّ `prometheus.yml` ← خدمات الملفّ الأساسيّ، ومهامّ `scrape-gpu.yml` ← خدمات **يعرّفها** ملفّ الكرت). و`test_the_gpu_scrape_file_is_mounted_where_prometheus_looks`: ‏`scrape_config_files == ["/etc/prometheus/scrape.d/*.yml"]`، و`fnmatch` لهدف الربط، والملفّ الأساسيّ لا يربط تحت `scrape.d/`، و`scrape-gpu.yml` مفتاحه `scrape_configs` وحده. و`test_the_gpu_target_is_ordinary_and_its_job_unique` (بلا `labels`، واسمٌ لا يتكرّر، والمنفذ في `expose`). و`test_healthcheck_coverage.py` يرى `nvidia-gpu-exporter` |
| 4.5 | وحدة | `test_every_size_fits_the_host_its_header_names` و`test_the_runbook_size_table_carries_…` القائمان، بأرقام §3‑ي، و`large` ≤ 57.60. و`test_the_monitoring_share_is_the_elevens_limits_plus_the_gpu_exporter`. وAC‑2.2 يُخرج المصدّر أيضاً |
| 4.6 | محلّيّ (Q‑10) | QA، الإجراء (ب) أدناه |
| 4.7 | نصّ (وحدة) | `test_the_security_list_records_the_gpu_exporter_image`: صفّ `\| ث‑6 \|` فيه `utkuozdemir/nvidia_gpu_exporter` و`7aee2d42`. ولا صفّ رقميّ جديد (`test_gpu_preflight.py:481-484`) |
| 5.1 | وحدة | `test_prometheus_alert_rules.py`: ‏`EXPECTED_ALERTS` + الاثنان، وتعليقهما «server-gpu-plan.md step 4.2 (docs/delivery/monitoring-profile/)»، وفقرةٌ في docstring. و«TWENTY-EIGHTH» ← «THIRTIETH … or a named step in `docs/capacity-plan.md` or `docs/server-gpu-plan.md`»، ورسالة التأكيد. ثمّ `test_the_gpu_rules_read_one_card_at_a_time` على ستّة أشياء: (أ) المجموعة `aizzak-gpu`. (ب) التعبيران من `GPU_EXPORTER_METRICS` وحدها، بلا `sum`/`max`/`avg`/`by`/`on`. (ج) `> 0.90` و`(… > 0)`، و`> 85`. (د) `for: 5m`، و`warning`. (هـ) `reason` الذاكرة فيه `2.6` و`90%`، و`reason` الحرارة فيه `Q-5`. (و) الحقول الخمسة |
| 5.2 | قاعدة | حالات promtool 1–6 (§3‑هـ). و`test_every_rule_is_fired_and_held_silent_by_the_promtool_suite` القائم يفرض الوجهين لكلّ قاعدة |
| 5.3 | وحدة | `test_every_rule_opens_its_own_runbook_section` القائم (المرساتان)، و`test_the_gpu_runbook_sections_start_with_reading`: في كلّ قسم `nvidia-smi` قبل `docker compose exec ollama ollama ps`، و`2‑ي`، و«كيف تعرف أنّه زال»، وسطرٌ في فهرس §1 |
| 5.4 | نصّ (وحدة) | `test_the_trial_log_lists_the_gpu_rules`: سطران في §6 فيهما «بانتظار التجربة في 7.3» |
| 5.5 | خادم | 7.3 |
| 6.1 | وحدة | `test_observability_stack_wiring.py::test_the_gpu_row_shows_memory_load_and_temperature` (صفٌّ عنوانه يبدأ بـ`GPU` وتحته ثلاث لوحاتٍ فرعيّة، تعابيرها تقرأ `memory_used/total` و`utilization_gpu_ratio` و`temperature_gpu`). و`test_every_gpu_metric_a_panel_or_rule_reads_is_one_the_exporter_emits` (`nvidia_smi_[a-z_]+` في اللوحات وفي `alerts.yml` ⊆ `GPU_EXPORTER_METRICS`). واختبارا `description` و`uid` القائمان |
| 6.2 | مالك | الإجراء (ج)، الخطوة 7 |
| 6.3 | محلّيّ (Q‑10) | QA، الإجراء (ب)، الخطوة 6 |
| NFR‑5 | بوّابات | الخمس، و`deploy/prometheus/test-rules.sh` بعد الالتزامين 3 و4 |
| NFR‑7 | قياس | `pytest tests/unit --durations=0` على `master` ثمّ على الفرع، والفرق ≤ 15 ث |

**(أ) AC‑1.8: إيقاف الإحدى عشرة في المكدّس الحيّ.** يجريه QA، بموافقة Q‑9، ≤ 10 دقائق.
- **الشرط:**
  - المكدّس الحيّ يعمل و`app` ‏`healthy`.
  - ⚠️ **حالته عند كتابة هذا التصميم: متوقّف**، ولا يعمل إلّا `cadvisor`. فإن بقي متوقّفاً يسأل المنسّق المالكَ سطراً واحداً قبل تشغيله، كما في `gpu-compose` (`status.md:85`). وإلّا صار AC‑1.8 [خادم].
- **الفرع:** `feat/monitoring-profile` هو المسحوب، و`.env` المالك كما هو.
- **`--profile` صريحٌ في كلّ أمر،** لأنّه يحلّ محلّ `COMPOSE_PROFILES`. ولا `down` ولا `-v`.

```bash
cd /home/AIZZAK
M="prometheus grafana alertmanager alert-sink pgbouncer-exporter redis-stream-exporter redis-cache-exporter node-exporter postgres-exporter loki alloy"
P="docker compose --profile wsl-ollama --profile monitoring"
date -u +%T; $P ps --format '{{.Service}} {{.State}} {{.Health}}' $M     # 1. before: 11 running/healthy
$P stop $M                                                                # 2. stop by name
$P ps --status running --format '{{.Service}}' $M | wc -l                 # 3. -> 0
curl -sk -o /dev/null -w '%{http_code}\n' https://localhost/health        # 4. -> 200
curl -sk -o /dev/null -w '%{http_code}\n' https://localhost/health/ready  #    -> 200
docker compose exec -T app python /app/deploy/smoke/stack_smoke.py; echo "rc=$?"   # 5. -> rc=0
$P up -d --no-deps $M                                                     # 6. same hash: started, not recreated
$P ps --format '{{.Service}} {{.Health}}' $M                              # 7. 11 healthy (wait up to ~2 min)
$P exec -T prometheus wget -qO- http://127.0.0.1:9090/api/v1/status/config | grep -c scrape_config_files   # 8. >= 1 (the branch's prometheus.yml loaded)
$P exec -T prometheus wget -qO- http://127.0.0.1:9090/api/v1/rules | grep -o AizzakGpu[A-Za-z]* | sort -u   # 9. both rules, inactive
$P exec -T prometheus wget -qO- http://127.0.0.1:9090/api/v1/targets | grep -c '"job":"nvidia-gpu"'         # 10. -> 0 (no GPU file here)
date -u +%T                                                               # total <= 10 min
$P logs --since 10m alert-sink | grep -c AizzakWatchdog                   # 11. >= 1 within 10 min
```
- **يُسجَّل في `test-plan.md` و`status.md`:**
  - الأوقات (UTC)، والأعداد، ورموز HTTP وrc.
  - ولا شيء من `.env`.
- **وإن أعادت الخطوة 6 إنشاء أيّ حاوية:**
  - **علامتها:** `$P ps` يُظهر «Created» لا «Started».
  - **هذا فشلٌ لـNFR‑1:** يتوقّف QA ويبلّغ.

**(ب) AC‑4.6 وAC‑6.3: المصدّر واللوحة في مشروعٍ منفصل.** يجريه QA، بموافقة Q‑10، ولا يلمس مشروع `aizzak` إطلاقاً. النمط نمط P‑3 في `gpu-compose` (`test-plan.md:241,287`).
```bash
cd /home/AIZZAK
free -m | sed -n 2p                       # 0. column `available` >= 5120, else STOP and record it
G="docker compose -p aizzak-gpumon-proof --env-file .env.example -f docker-compose.yml -f docker-compose.gpu.yml --profile monitoring"
GF_PW=$(openssl rand -hex 12)             #    never printed; Grafana is on 127.0.0.1 only
$G config -q && echo config-ok            # 1.
HOST_PORT_GRAFANA=13001 GRAFANA_ADMIN_PASSWORD="$GF_PW" \
  $G up -d --no-deps nvidia-gpu-exporter prometheus grafana       # 2. three services, no app, no other port
sleep 600                                                         # 3. ten minutes
$G ps --format '{{.Service}} {{.State}} {{.Health}}'              # 4. exporter healthy (read_only works)
$G exec -T prometheus wget -qO- 'http://127.0.0.1:9090/api/v1/targets?scrapePool=nvidia-gpu' | grep -o '"health":"[a-z]*"'   # -> "up"
for q in nvidia_smi_memory_used_bytes nvidia_smi_memory_total_bytes nvidia_smi_utilization_gpu_ratio nvidia_smi_temperature_gpu nvidia_smi_gpu_info; do
  printf '%s ' "$q"; $G exec -T prometheus wget -qO- "http://127.0.0.1:9090/api/v1/query?query=$q" | grep -o '"result":\[[^]]*' | grep -c uuid
done                                                              #    each >= 1; gpu_info carries `name`
ID=$(docker inspect -f '{{.Id}}' "$($G ps -q nvidia-gpu-exporter)")
cat "/sys/fs/cgroup/system.slice/docker-$ID.scope/memory.peak"    # 5. bytes; x 1.5 <= 67108864 (64m) -> peak <= 44739242
docker inspect -f '{{.State.OOMKilled}}' "$ID"                    #    -> false
# 6. AC-6.3: each panel expression through Grafana (the provisioned uid aizzak-prometheus)
curl -s -u "admin:$GF_PW" -H 'Content-Type: application/json' http://127.0.0.1:13001/api/ds/query \
  -d '{"from":"now-5m","to":"now","queries":[{"refId":"A","datasource":{"uid":"aizzak-prometheus"},"expr":"<panel expr>","instant":true}]}' \
  | grep -c '"values"'                                            #    non-empty frame for each of the three
# 7. one measurement requirements.md §5.6 left open, in THIS project only:
docker compose -p aizzak-gpumon-proof --env-file .env.example -f docker-compose.yml -f docker-compose.gpu.yml down   # no --profile
docker ps --filter label=com.docker.compose.project=aizzak-gpumon-proof --format '{{.Names}}' | wc -l   # record: do the 3 survive?
$G down                                   # 8. with the profile, WITHOUT -v; the project's volumes stay (deleting them needs approval)
free -m | sed -n 2p
```
- **قيود الجهاز** (قيست هنا):
  - cgroup v2 بمشغّل `systemd`، و`memory.peak` يُقرأ من المضيف بلا `sudo`. والصورة بلا `cat`، فلا قراءة من داخلها.
  - وAlloy الحيّ يُسقط أسطر المشروع المنفصل (`project!="aizzak"`، `deploy/alloy/config.alloy:115`)، فلا يتلوّث المخزن الحيّ.
- **حكم Q‑8:**
  - إن تجاوزت `memory.peak` × 1.5 ‏`64m`: يتوقّف QA عند هذا البند، ويُعرض ⚠️ في §7 على المالك.
  - والقيمة تُكتب بالبايت كما هي.

**(ج) على جهاز المالك بعد الدمج: AC‑3.3 وAC‑3.4 وAC‑6.2.** يطبع بصماتٍ وأعداداً فقط (NFR‑4).
- **الترتيب مقصود:** السطر يُعدَّل **قبل** سحب الدمج. هو غير ضارٍّ على `master` (قيس: 34 خدمة)، فلا لحظة تكون فيها المراقبة مطفأة.
- **الشرط:** المكدّس يعمل.

```bash
cd /home/AIZZAK
M="prometheus grafana alertmanager alert-sink pgbouncer-exporter redis-stream-exporter redis-cache-exporter node-exporter postgres-exporter loki alloy"
snap() {
  docker compose config --services | sort | sha256sum
  docker compose config --services | wc -l
  docker compose config --hash "${M// /,}" | sort | sha256sum
  docker compose ps -q $M | sort | sha256sum
}
snap > /tmp/mp-0.txt                       # 1. on master as it is, BEFORE anything
# 2. edit .env by hand:  COMPOSE_PROFILES=wsl-ollama  ->  COMPOSE_PROFILES=wsl-ollama,monitoring
snap > /tmp/mp-1.txt; diff /tmp/mp-0.txt /tmp/mp-1.txt && echo "step 2: the line alone changes nothing"
git pull --ff-only                         # 3. the merge commit
snap > /tmp/mp-2.txt; diff <(sed -n 1,3p /tmp/mp-0.txt) <(sed -n 1,3p /tmp/mp-2.txt) && echo "AC-3.3: services and 11 hashes SAME"
docker compose up -d                       # 4.
docker compose ps -q $M | sort | sha256sum | diff - <(sed -n 4p /tmp/mp-0.txt) && echo "AC-3.3: no container recreated"
docker compose ps --format '{{.Health}}' $M | sort | uniq -c        # 5. -> 11 healthy
# 6. AC-3.4, only if .env holds an "aizzak auto-size" block (otherwise write "not applicable"):
deploy/auto_size.py --role dev --dry-run | grep '^# profiles:'      #    -> contains monitoring
deploy/auto_size.py --role dev && deploy/resource-budget.sh --block .env | grep '^monitoring:'   # -> counted (in the block's profiles)
docker compose up -d && docker compose ps -q $M | sort | sha256sum | diff - <(sed -n 4p /tmp/mp-0.txt) && echo "still not recreated"
# 7. AC-6.2: http://127.0.0.1:13000 -> capacity dashboard: the GPU row says "No data", no error; the rest as before.
#    (Prometheus keeps its old rules until a reload; optional:
#     docker compose exec -T prometheus wget -qO- --post-data= http://127.0.0.1:9090/-/reload )
```

## 6. ترتيب التنفيذ والتوازي

وكيلٌ واحد ومستودعٌ واحد، فلا توازي مفيد.
- **بعد كلّ التزام:** البوّابات الخمس، ومنها `pytest tests/unit tests/architecture tests/eval` فقط، لأنّ المكدّس الحيّ مستعمَل (`CLAUDE.md`).
- **وبعد 3 و4:** `deploy/prometheus/test-rules.sh`.
- **في كلّ رسالة التزام** سطرا النسب من التعليمات.

0. **على `master` `47225ff`:** ‏`pytest tests/unit --durations=0`، ويُحفظ الزمن الكلّيّ (NFR‑7). ثمّ `git switch -c feat/monitoring-profile`.
1. **`feat(compose): put the eleven monitoring services behind profile "monitoring"` (4.1 كاملاً، القاعدة 3):**
   - `docker-compose.yml`، و`deploy/resource-budget.sh`، و`tests/unit/support_compose.py`.
   - و`test_monitoring_profile.py` بمعايير US‑1، وتعديلات §2 لـ4.1 في `test_resource_budget` و`test_auto_size` و`test_auto_size_edges` و`test_compose_local_llm` و`test_monitoring_host_postgres_acceptance` و`test_observability_stack_wiring`.
   - **لا يُقسَم:** دون السكربت تنزل الأرقام، ودون الاختبارات تنكسر البوّابات.
2. **`docs: the monitoring profile line, the third exception, no monitoring means no alerts`:**
   - `.env.example` مع `test_compose_local_llm` (القالب مثبّتٌ باختبار).
   - و`stack-commands.md` و`quickstart.md` و`runbooks/alerts.md` §0 وخطوة Watchdog، و`deploy-server.md` §2.
   - و`08` (جدول الخدمات، و§2‑ز بلا جدول الأحجام).
   - و`server-gpu-plan.md` §3.
   - واختبارات AC‑2.6 وAC‑3.1 وAC‑3.5.
3. **`feat(gpu): nvidia_gpu_exporter under "monitoring", scraped only with the GPU file` (4.2، القاعدة 3):**
   - `docker-compose.gpu.yml`، و`scrape-gpu.yml`، و`prometheus.yml`، و`test-rules.sh`.
   - و`test_compose_gpu_overlay` و`test_observability_stack_wiring` (AC‑4.4) و`test_healthcheck_coverage`.
   - و`test_resource_budget` مع جدول الأحجام في `08` (AC‑4.5).
   - و`test_auto_size` و`test_auto_size_edges` بأرقام §3‑ي.
   - ومسار الربط في `test_prometheus_alert_rules` (AC‑4.3).
4. **`feat(alerts): AizzakGpuMemoryHigh and AizzakGpuTemperatureHigh`:**
   - `alerts.yml` و`alerts.test.yml`.
   - وأقسام الدليل وفهرسه وسطرا §6، وسطر `AizzakScrapeTargetDown`.
   - و`test_prometheus_alert_rules` (AC‑5.1، 5.3، 5.4).
5. **`feat(grafana): GPU memory, load and temperature row`:** `aizzak-capacity.json`، وAC‑6.1.
6. **`docs: GPU exporter in the security list and the runbook; board rows 4.1 and 4.2`:**
   - `deploy-server.md` `ث‑6`، و`stack-commands.md` الأمر 41، و`08` §5.
   - و`server-gpu-plan.md` §4، و`server-gpu-summary.html`.
   - وAC‑3.6 وAC‑4.7.
7. **البوّابات الستّ كاملة، وقياس NFR‑7، ودليل الـPR:**
   - `git diff --exit-code master -- src deploy/auto_size.py deploy/sizes deploy/nginx deploy/runpod .github migrations`.
   - **ثمّ QA:** الإجراءان (أ) و(ب).
   - **وبعد الدمج، المالك:** الإجراء (ج).

## 7. المخاطر والقرارات

**قرارات التصميم (محلّيّة لهذا الملفّ):**
- **ق‑1: هدف المصدّر بـ`scrape_config_files`،** وملفّ الكرت وحده يربط ملفّ الأهداف (§3‑د).
  - **لماذا:** الطريقة الوحيدة من الثلاث التي تحقّق FR‑12 في الاتّجاهين.
  - **الدليل:** مصدر `v3.13.2` نفسه.
  - **أثرها على الملفّ الأساسيّ:** محتوى `prometheus.yml` وحده، فبصمة `prometheus` لا تتغيّر.
- **ق‑2: الميزانيّة.**
  - `monitoring` مفعّلٌ افتراضيّاً في السكربت وفي `support_compose.DEFAULT_PROFILES`.
  - و`--without-monitoring` يرفض الجمع مع `--block` ومع `--with-profiles`.
  - و`MODE_PROFILES` لا يتغيّر، و`auto_size.py` لا يتغيّر.
- **ق‑3: الصورة `1.15.1` بالوسم والبصمة معاً،** بصمة الفهرس متعدّد المنصّات.
  - **لماذا هذا الإصدار:**
    - أحدث إصدار.
    - distroless، وبلا root، وموقَّع.
    - بدل `1.13.1` الأخيرة على Ubuntu، التي فيها `bash` لكنّها تعمل بـroot.
  - **وثمن الاختيار:** الفحص الصحّيّ بـ`nvidia-smi` لا بـHTTP. وهو يقيس «الكرت مقروءٌ من الحاوية»، ومنفذ المصدّر يغطّيه `AizzakScrapeTargetDown`.
  - **والبصمة في المرجع لا في تعليق:** هي علاج `ث‑3` المقترح، و«الوسم والبصمة» في Q‑4.
- **ق‑4: تقييد الحاوية بنمط `node-exporter`،** وبديلٌ مكتوب إن رفضت أداة NVIDIA `read_only` (§3‑ج).
- **ق‑5: السجلّ يُعاد كتابته في ملفّ الكرت،** واختبارٌ يُبقيه مساوياً لـ`x-app-logging`. والسبب: Alloy يُسقط ما لا يحمل `attrs`.
- **ق‑6: صفّ اللوحة في آخرها (`y=68`)،** و`group_left` على `nvidia_smi_gpu_info` للاسم في اللوحة وحدها. والتنبيهان بلا ربط، حتّى لا يُعطّلهما غياب سلسلة info.
- **ق‑7: لقطة AC‑1.1 بصمات sha256 للمواصفة المحلولة،** لا قاموسٌ حرفيّ، لأنّ الخدمات طويلة.
- **ق‑8: المالك يعدّل سطره قبل سحب الدمج،** وهو غير ضارٍّ على `master`.

**⚠️ يحتاج قراراً بشريّاً إن تحقّق شرطه فقط:**
- ⚠️ **ذاكرة المصدّر (Q‑8)، شرطيّ.** إن أظهر الإجراء (ب) `memory.peak` × 1.5 > `64m` (أي ذروة > 42.7 م.ب)، يتوقّف التنفيذ عند هذا البند.
  - **السبب المحتمل:** المصدّر عمليّة Go، ومعها `nvidia-smi` في المجموعة نفسها عند كلّ كشطٍ وكلّ فحصٍ صحّيّ. ومسار NVML على WSL يمرّ عبر `libdxcore`، وقد يكون أثقل منه على خادم لينكس.
  - **الخيارات:**
    - **(أ)** يبقى `64m` حتّى قياس 7.3 على الخادم، مع خطرٍ مقبول: `OOMKilled` يقتل المصدّر وحده، فيشتعل `AizzakScrapeTargetDown{job="nvidia-gpu"}` ولا تتأثّر المنصّة.
    - **(ب)** `PROMETHEUS_MEM` في `deploy/sizes/large.env` من `2g` إلى `1920m`، وذروته المقيسة 214.8 م.ب في 15 يوماً (`monitoring-host-postgres/design.md:360-380`). ثمّ حدّ المصدّر `128m`. يلمس ملفّاً محميّاً من قائمة Q‑3 («ربّما `large.env`»)، ولا يمسّ جهاز المالك ولا قيم Compose الافتراضيّة.
  - **التوصية:**
    - **(أ)** إن كانت الذروة على WSL وحده بين 42.7 و64 م.ب، لأنّ القياس الملزِم على الخادم.
    - **و(ب)** إن تجاوزت الذروة `64m` نفسها، أو `OOMKilled=true`.

**لا بنود ⚠️ أخرى:**
- لا ملفّ محميّ خارج قائمة Q‑3: لا `.github/workflows/`، ولا `Dockerfile`، ولا `deploy/runpod/`، ولا غيرها من `deploy/`.
- وصورة الطرف الثالث قرّرها المالك في Q‑4، وتُسجَّل `ث‑6`.
- ولا تغيير في الأمن أو التوثيق أو العزل بين مساحات العمل.

**مخاطر غير حاجبة:**
- **هامش `large` بعد 4.2 صار 6.4 م.ب.** أيّ خدمةٍ دائمة تُضاف بعدها في وضع الكرت تُسقط `test_every_size_fits_the_host_its_header_names`، وذلك مقصود (الدفتر يقول بصوتٍ عالٍ إنّ الخادم امتلأ).
- **نسيان المالك السطر:**
  - **أثره:** يُطفئ مسار التنبيه بصمت عند أوّل إعادة إنشاء، والحاويات القائمة تبقى حتّى ذلك الحين.
  - **ما يخفّفه:**
    - الإجراء (ج) يجعل السطر الخطوة 2 قبل الدمج.
    - والتحذير مكتوبٌ في `stack-commands.md` و`quickstart.md` و`alerts.md` §0.
- **كتلة `auto-size` قديمة** على جهاز المالك (`# profiles: wsl-ollama`): `--block` يقول «NOT counted» حتّى يُعاد المولِّد (AC‑3.4). والقيم المكتوبة لا تتغيّر.
- **إصدارات المصدّر متلاحقة** (14 في عشرة أسابيع): البصمة تثبّت ما يعمل، وتحديثها قرارٌ مسجّل (`ث‑6`).
- **PR ‏#13** يلمس `server-gpu-plan.md` و`server-gpu-summary.html`: يُحلّ التعارض عند الدمج (Q‑11)، وتُعاد أعداد الشريط إن تغيّرت.
- **حزمة الإصدار (5.1)، ملاحظةٌ خارج النطاق:** يجب أن تحمل `deploy/prometheus/scrape-gpu.yml`، لأنّ ملفّ الكرت يربطه. واختبار 7.1 («كلّ مسارٍ يربطه Compose») سيلتقطه.

**ملاحظات للمنسّق:**
- **`docs/deploy-server.md` موجود** (هيكل `gpu-compose`، 87 سطراً)، والمكان المقترح للتحذير هو قسمٌ جديد فيه.
- **المكدّس الحيّ متوقّفٌ الآن** (2026‑10‑17، `cadvisor` وحده يعمل)، وAC‑1.8 يحتاجه يعمل.
- **`test_auto_size_edges.py`** يتغيّر، ولم يكن في قائمة المحلّل.

</div>
