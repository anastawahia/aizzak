<div dir="rtl">

# مراجعة الشيفرة: المراقبة اختياريّة وأرقام الكرت (`monitoring-profile`)

- **النطاق المراجَع:** `git diff master...feat/monitoring-profile` في `/home/AIZZAK`: تسعة التزامات من `f2158a0` إلى `3880ff9`، و35 ملفّاً. قرأتُ كلّ ملفٍّ متغيّر كاملاً، لا مقاطع الفرق وحدها.
- **الحكم:** **PASS** في الجولتين.
  - **الجولة 0:** لا ملاحظة حاجبة. فيها 4 ملاحظات مهمّة و7 تحسينات، وأُصلحت كلّها في الجولة 1.
  - **الجولة 1:** لا حاجب. فيها 3 ملاحظات مهمّة جديدة (#12…#14) و4 تحسينات (#15…#18). انظر القسم «الجولة 1» أسفل.
- **خارج الحكم:** BUG‑1 (ذروة ذاكرة المصدّر، AC‑4.6). قبله المالك خطراً حتّى 7.3 في 2026‑10‑18، فلا يُطرح هنا حاجباً. لكنّ الملاحظة 3 تطلب تسجيل هذا القبول حيث يُقرأ.

## الملاحظات

| # | الشدة | الملف:السطر | الملاحظة | الإصلاح المطلوب |
|---|---|---|---|---|
| 1 | مهمة | `docs/stack-commands.md:21`، و`docs/stack-commands.md:123`، و`docs/design/08-local-runbook.md:755-756` (و`:30`) | **بقيت أسطر profile قديمة بلا `monitoring`**، وهي تناقض ما أضافه الفرع في الوثيقتين نفسيهما. الصفّ 1 في الجدول يقول: «وعلى WSL سطر `COMPOSE_PROFILES=wsl-ollama`». والسطر 123: «سطره `COMPOSE_PROFILES=local-llm`». والإقلاع السريع في 08 §3.0 يكتب السطرين القديمين ثمّ يقول «هذا وحده يكفي». **السيناريو:** مشغّلُ خادمٍ يتبع السطر 123 أو §3.0 ثمّ يشغّل `docker compose up -d`. فلا يقلع Prometheus ولا Alertmanager ولا `alert-sink`، ولا تنبيهات ولا نبض Watchdog، بصمت. وهذا هو العطب الذي تحذّر منه الميزة نفسها. أمّا سؤال `down`/`stop`: لا وثيقة في الفرع تأمر بـ`down` أو `stop` بلا profile. فحصتُ `stack-commands.md` و`quickstart.md` و08 و`runbooks/` و`deploy/rolling-deploy.sh` و`deploy/load/run.sh`. | السطر 21: `COMPOSE_PROFILES=wsl-ollama,monitoring`. والسطر 123: `COMPOSE_PROFILES=local-llm,monitoring`. وفي 08: السطران 755-756 بـ`,monitoring`، والسطر 30 يذكر `wsl-ollama,monitoring`. |
| 2 | مهمة | `tests/unit/test_monitoring_profile.py:117-122` | **حارسا AC‑1.2 وAC‑1.3 لا يريان مداخل ملفّ الكرت للخدمات الأساسيّة.** `_services()` يبني النموذج بـ`setdefault`، فيُهمَل `environment` الذي يضيفه الملفّ إلى `ollama` (`docker-compose.gpu.yml:36`) وإلى `embedding` (`:73`). وهذا انحرافٌ عن design.md §3‑ط، وفيه: `load_compose(files=(COMPOSE, GPU_OVERLAY))["services"]`. **تحقّقتُ بالإفساد:** أضفتُ `PROBE_URL=http://prometheus:9090/-/ready` إلى `environment` في `embedding` داخل ملفّ الكرت، فنجح `test_no_service_outside_monitoring_names_a_monitoring_host`. وعلى خادم كرتٍ بلا `monitoring` يصير هذا مضيفاً غير موجود. | `_services()` يعيد `load_compose(files=(COMPOSE, GPU_OVERLAY))["services"]` كما في التصميم. و`environment` قاموسٌ في الملفّين، فيُدمَج ولا يُستبدَل. أو يُفحص كلّ مدخلٍ في ملفّ الكرت لخدمةٍ خارج المراقبة. |
| 3 | مهمة | `docker-compose.gpu.yml:108-109`، و`docs/server-gpu-plan.md:133-134` | **قبول المالك لـBUG‑1 غير مسجَّل خارج مجلّد التسليم، والتعليق يقول العكس.** في التعليق: «64m … held against measured peak x 1.5 (AC‑4.6)». والمقيس 56,692,736 بايت، ومضاعفه × 1.5 نحو 81.1 MiB، أي فوق `64m`. وصفّ 4.2 ما زال يقول «AC‑4.6 وAC‑6.3 محلّيّاً (Q‑10) أو في 7.3»، والواقع أنّ AC‑6.3 نجح محلّيّاً، وAC‑4.6 فشل ثمّ قُبل باستثناء. **السيناريو:** من يقرأ ملفّ الكرت أو اللوحة في 7.3 يظنّ أنّ الحدّ ثبت أمام شرطه، فلا يعيد القياس. | **التعليق:** «64m stays by owner decision 2026‑10‑18, an accepted risk: WSL peak 54.1 MiB, and peak x 1.5 > 64m. Re-measure in 7.3». تعديل التعليق لا يغيّر `config --hash`. **الصفّ 4.2:** «AC‑6.3 ✅ محلّيّاً، وAC‑4.6 مقبولٌ باستثناء (BUG‑1) ويُعاد قياسه في 7.3». **الصفّ 4.1:** يذكر AC‑1.8 ✅. |
| 4 | مهمة | `docs/architecture.html:231`، و`:763` | **عدد القواعد في الملفّ ما زال 27**، بينما `architecture.md:88` و`:767` صارا 29 في هذا الفرع. الالتزام `97e4b12` كان يحدّث الملفّين معاً. و`architecture.html` هو مصدر سجلّ القرارات الملزم (`Requirements-v1.md:417,468`). ملاحظة نطاق: `architecture.md` و`monitoring-plan.md` ليسا في قائمة design.md §2، لكنّ تعديلهما صحيح ومبرَّر. | 27 ← 29 في السطرين. |
| 5 | تحسين | `deploy/resource-budget.sh:309-316` | **مع `--block` و`--with-profiles` معاً يطبع السكربت «monitoring: NOT counted (not in the block's profiles)»**، مع أنّ `TOTAL` يعدّ الإحدى عشرة. قستُ ذلك بكتلةٍ فيها `# profiles: wsl-ollama` على 32/64: دون `--with-profiles` النتيجة 32.50/52.88 برمز 0، ومعه 43.85/65.09 برمز 1، والسطر نفسه يقول «NOT counted». | إن كان `with_profiles` و`monitoring` ليس في `enabled`، يُطبع سطرٌ مثل «monitoring: not in the block's profiles, counted by --with-profiles». أو يُرفض الجمع برمز 2 كما رُفض `--without-monitoring --with-profiles`. ويُضاف اختبارٌ للحالة. |
| 6 | تحسين | `docs/delivery/monitoring-profile/design.md:942-943` (الإجراء ج، الخطوة 7) | **يقترح الإجراء `/-/reload` بعد `git pull`، وهذا لا يكفي.** `prometheus.yml` و`alerts.yml` مربوطان ملفّاً ملفّاً، فتبقى الحاوية على الـinode القديم بعد git. و`08-local-runbook.md:992` يقول صراحةً «إعادة إنشاء لا reload». فيقرأ الـreload الملفّين القديمين: 27 قاعدة، وبلا `scrape_config_files`، ويبدو مع ذلك ناجحاً. | يُستبدل بـ`docker compose up -d --force-recreate --no-deps prometheus` بعد فحص معرّفات الحاويات في AC‑3.3، كما في 08:992. |
| 7 | تحسين | `tests/unit/test_prometheus_alert_rules.py:762`، و`tests/unit/test_observability_stack_wiring.py:105` | **قائمة مقاييس المصدّر الخمسة مكرّرةٌ حرفيّاً في وحدتَين.** فتحديث إصدار المصدّر يتطلّب تعديلين، ونسيان أحدهما يُبقي حارساً على أسماءٍ قديمة. | تُعرَّف القائمة مرّةً واحدة، في `tests/unit/support_compose.py` بجانب `DEFAULT_PROFILES` مثلاً، وتُستورد في الوحدتين. |
| 8 | تحسين | `docs/design/08-local-runbook.md:32` | **جدول الخدمات بلا صفّ `nvidia-gpu-exporter`**، وdesign.md §3‑ح يطلب صفّين. | يُضاف الصفّ الثاني كما كُتب في design.md §3‑ح. |
| 9 | تحسين | `docs/stack-commands.md:604` | **الجملة تناقض نفسها:** «`docker-compose.gpu.yml` يمسّ ثلاث خدماتٍ وحدها»، ثمّ تذكر خدمتين أخريين. ورأس ملفّ الكرت صار يقول «Five entries». | «يمسّ ثلاث خدماتٍ دائماً، ومع `monitoring` اثنتين أخريين: …». |
| 10 | تحسين | `docs/server-gpu-plan.md:457` | **صفّ «قرار 7» بلا جواب المالك**، مع أنّ القرارين 1 و6 في الجدول نفسه يحملان «**الجواب (التاريخ):**». والشيفرة تستشهد بهذا القرار: `deploy/resource-budget.sh:184` و`tests/unit/support_compose.py`. | يُضاف «**الجواب (2026‑10‑17):** مفعّلة افتراضيّاً، وتُطفأ على الخادم الصغير وحده»، مع الإحالة إلى Q‑1 في `status.md`. |
| 11 | تحسين | `docs/delivery/monitoring-profile/stories.md:118` | **BUG‑2 المعروف:** أرقام AC‑2.1 من قبل 4.2 (37.60/57.53 و19.35/28.41 و11.50/14.22). والاختبار يؤكّد أرقام ما بعدها: 37.85/57.59 و19.60/28.47 و11.75/14.28. | يُضاف تحت AC‑2.1 سطرٌ بأرقام ما بعد 4.2، أو إحالةٌ إلى design.md §3‑ي. |

## مطابقة التصميم

يطابق التنفيذ design.md في كلّ عقد، إلّا الانحرافين في الملاحظتين 2 و8.

### `docker-compose.yml` (4.1)
- **السطور:** أحد عشر سطر `profiles: ["monitoring"]`، كلٌّ أوّلَ مفتاحٍ تحت اسم خدمته، ومعها فقرة التعليق. ولا تغيير آخر في الملفّ.
- **`cadvisor`:** باقٍ على `["container-metrics"]` وحده.
- **`METRICS_EXPORTER_PASSWORD`:** ما زال إلزاميّاً بالـprofile ودونه.
- **ما لا يعتمد على المراقبة:**
  - لا حافّة `depends_on` من خارج الإحدى عشرة إليها.
  - لا ذكر لمضيفٍ منها بصيغة `host:port` في `src/` ولا `deploy/nginx` ولا السكربتات (بحثتُ بـgrep).
  - وظيفة `integration` في CI لا تُقلع غير البنية التحتيّة.
  - صورة RunPod لا تحمل المراقبة أصلاً (`deploy/runpod/supervisord.conf`).

### دمج ملفّ الكرت في `prometheus`
- **الدمج إلحاقٌ لا استبدال.** قيس بـ`docker compose -f docker-compose.yml -f docker-compose.gpu.yml --env-file .env.example --profile monitoring config --format json`، بعد حذف `COMPOSE_FILE` و`COMPOSE_PROFILES` من البيئة. فصارت مداخل `volumes` أربعة: الثلاثة الأصليّة، ثمّ `scrape-gpu.yml` على `/etc/prometheus/scrape.d/gpu.yml` للقراءة فقط.
- **دوالّ `merge()` في السكربتين و`support_compose._merge`** تستبدل القائمة، لكنّها لا تقرأ غير الحدود والـprofiles و`restart`، كما يقول التصميم.

### المصدّر
- **الصورة:** مثبّتة بالوسم والبصمة (`1.15.1@sha256:7aee2d42…`).
- **التقييد:** `user` 65534، و`read_only`، و`cap_drop: ALL`، و`no-new-privileges`. ولا `ports` ولا `volumes`.
- **الكرت:** حجز `utility` بلا `count`، فيحلّه Compose إلى `-1`.
- **الفحص الصحّيّ:** `nvidia-smi`، وهو ممكنٌ في صورة distroless بلا shell، وأثبته QA (healthy، و`read_only` يعمل).
- **السجلّ:** `logging` يطابق `x-app-logging`، فيصل `attrs` الذي يحتاجه Alloy. ويحرس ذلك اختبار.

### هدف Prometheus
- **الآليّة:** `scrape_config_files` بنمط.
- **`test-rules.sh`:** يفحص الإعداد مرّتين، بلا ملفّ الكرت ومعه.
- **الفحص الثاني يقرأ الملفّ المربوط فعلاً.** تحقّقتُ بالإفساد: مهمّةٌ مكرّرة الاسم، أو مفتاحٌ غير `scrape_configs`، يُفشلانه برمز 1.

### `deploy/resource-budget.sh`
- **الرسائل:** حرفيّاً كما في §3‑ب.
- **الجمع المتعارض:** رمز 2 مع `--block` ومع `--with-profiles`.
- **`monitoring`:** مفعّلٌ افتراضيّاً، ومع `--size` أيضاً. و`--block` يتبع سطر `# profiles:`.
- **الثغرة الوحيدة:** الجمع `--block --with-profiles` (الملاحظة 5).

### `deploy/auto_size.py`
- **لم يتغيّر، وما زال صحيحاً.** `_known_profiles()` يرى `monitoring` من ملفّات Compose، و`compute()` يعدّ ما في `COMPOSE_PROFILES` وحده، كما يفعل Compose.

### التنبيهان واللوحة
- **التعبيران:** يقرآن `nvidia_smi_memory_used_bytes` و`nvidia_smi_memory_total_bytes` و`nvidia_smi_temperature_gpu` وحدها.
- **لكلّ كرت تنبيهٌ** بتسمية `uuid`، ولا تجميع.
- **القسمة على صفر:** `total > 0` يمنعها، فلا NaN يُشعل التنبيه.
- **لا ربط بـ`nvidia_smi_gpu_info` في التنبيهين.** والربط في اللوحات وحدها: `on (uuid) group_left (name, index)`.
- **الحدود:** 0.90 لـ5m و85 لـ5m بخطورة `warning`، حسب Q‑5.
- **حالات promtool السبع:** تغطّي كرتين، والقفزة، والقراءة الفاشلة، وجهازاً بلا كرت، والمصدّر الميّت.
- **أقسام الدليل:** مرساتان، وسطرا فهرس، وسطرا §6، وسطرٌ في `AizzakScrapeTargetDown` وآخر في `AizzakWatchdog`.

### الوثائق
- **عدّ اللوحة:** 8 مكتملة، و9 جارية، و12 لم تبدأ. يطابق شريط `server-gpu-summary.html` عدد المقالات (29) وصفوف اللوحة.
- **`deploy-server.md`:** لا صفّ جدولٍ جديد يبدأ برقم.
- **IDs:** الشرطة U+2011 في كلّ ما أُضيف.

### النطاق
- **خارج قائمة design.md §2:** `architecture.md` و`monitoring-plan.md` فقط.
- **الفرق فارغ** في `src` و`deploy/auto_size.py` و`deploy/sizes` و`deploy/nginx` و`deploy/runpod` و`deploy/alloy` و`deploy/alertmanager` و`.github` و`migrations`.

## الاختبارات

### هل تؤكّد السلوك؟
نعم، ولا تكتفي بالأسماء:
- **AC‑1.1:** لقطة sha256 للمواصفة.
- **AC‑1.5:** بصمات `config --hash` على نسخةٍ حُذف منها السطر.
- **AC‑1.4 وAC‑4.2:** قوائم الخدمات المرسومة.
- **AC‑2.1 وAC‑2.2 وAC‑4.5:** مجاميع السكربت مقابل الوحدة، والفرق باسم حدود الإحدى عشرة والمصدّر.
- **AC‑5.1:** قراءة التعبيرين.
- **AC‑6.1:** أسماء المقاييس محصورةٌ في قائمة المصدّر.

ولكلّ معيار Must آليّ اختبارٌ يغطّيه. الثغرة الوحيدة في الحارس هي الملاحظة 2.

### العزل عن البيئة
- **اختبارات الرسم الجديدة والمعدَّلة** تحذف `COMPOSE_FILE` و`COMPOSE_PROFILES`، وتمرّر `--env-file .env.example`: `test_monitoring_profile`، و`test_compose_gpu_overlay`، و`test_compose_local_llm`، و`test_monitoring_host_postgres_acceptance`. والأخير صار يمرّر `-f docker-compose.yml` أيضاً.
- **مشغِّلا الميزانيّة** يثبّتان `COMPOSE_FILE`.
- **`test_compose_sizes`** يرسم بـ`--profile *` وبيئةٍ مصغّرة.

### ما شغّلتُه
| الفحص | النتيجة |
|---|---|
| `ruff format --check` و`ruff check` | أخضر |
| `lint-imports` | 8 عقود محفوظة |
| `mypy src` | لم يُشغَّل: `src/` لم يتغيّر، وأعاده المنسّق أخضر على `81fc8e5`. والالتزام `3880ff9` وثيقةٌ فقط |
| الوحدات الاثنتا عشرة المتأثّرة، ببيئةٍ نظيفة | خضراء. والعيّنة المعدودة: `86 passed`، rc 0 |
| سبع وحدات Compose والميزانيّة، بـ`COMPOSE_FILE` الثلاثيّ كما في CI | خضراء |
| `deploy/prometheus/test-rules.sh` | `test-rules: OK`، و29 قاعدة، والفحصان كلاهما ناجح |

### الإفساد
- **الحارس الذي يمسك:** فحص `check config` الثاني. يفشل على ملفّ أهدافٍ معطوب، فهو يقرأ الملفّ المربوط فعلاً.
- **الحارس الذي يفوّت:** حارس AC‑1.3 لا يرى `environment` في ملفّ الكرت (الملاحظة 2).

---

## الجولة 1 (إعادة العمل، `278defa..f5591c2`)

- **النطاق:** `git diff 278defa..HEAD`: ستّة التزامات (`6d3b574` و`3802c25` و`88ea496` و`9ebf5a7` و`3467ba3`، ثمّ `f5591c2` لـQA)، و19 ملفّاً.
  - قرأتُ كاملةً: `deploy/gpu-preflight.sh`، و`docker-compose.gpu.yml`، و`tests/unit/test_gpu_preflight.py`، و`tests/unit/test_compose_gpu_overlay.py`.
  - وقرأتُ بقيّة الملفّات المعدّلة مع سياق كلّ تعديل.
  - ومعها: `security-review.md` (SR‑1…SR‑8)، و`test-plan.md` §6، و`status.md` «قرارات وتصعيدات».
- **الحكم:** **PASS**. لا حاجب. الملاحظات #1…#11 كلّها أُصلحت، ولا تراجع. فيها 3 ملاحظات مهمّة جديدة و4 تحسينات.
- **نطاقٌ موسَّع بموافقة:** `deploy/gpu-preflight.sh` واختباره خارج قائمة design.md §2. وتعديلهما لـSR‑2 بموافقة المالك الصريحة (`status.md`، 2026‑10‑18).

### حال الملاحظات #1…#11

| # | الحال | الدليل |
|---|---|---|
| 1 | ✅ | `stack-commands.md:21` و`:123`، و`08-local-runbook.md:30` و`:756-757`، كلّها بـ`,monitoring`. وحارسٌ جديد يرفض السطر العاري في `stack-commands.md` و`quickstart.md` (`test_monitoring_profile.py:339-341`). |
| 2 | ✅ | `_services()` يعيد `load_compose(files=(COMPOSE, GPU_OVERLAY))["services"]` (`test_monitoring_profile.py:117-122`). **أعدتُ الإفساد نفسه** على نسخةٍ في scratch: `PROBE_URL=http://prometheus:9090/-/ready` في `environment` لـ`embedding` داخل ملفّ الكرت. ففشل `test_no_service_outside_monitoring_names_a_monitoring_host`: ‏1 failed و26 passed. |
| 3 | ✅ | **التعليق** (`docker-compose.gpu.yml:114-117`): «accepted risk … peak x 1.5 > 64m. Re-measure in 7.3». **الصفّان 4.1 و4.2** (`server-gpu-plan.md:133-134`): AC‑1.8 ✅، وAC‑6.3 ✅، وAC‑4.6 مقبولٌ باستثناء. ورقم الذروة فيهما صار قديماً بعد قياس QA (#17). |
| 4 | ✅ | `architecture.html:231` و`:763`: 29. ولا «27 قاعدة» باقية في `docs/`. |
| 5 | ✅ | `resource-budget.sh:312-313` يطبع «not in the block's profiles, counted by --with-profiles». و`test_block_with_profiles_does_not_claim_monitoring_is_uncounted` يثبت السطرين، مع `--with-profiles` ودونه. |
| 6 | ✅ | `design.md:942-945`: ‏`up -d --force-recreate --no-deps prometheus`، بعد فحص AC‑3.3. |
| 7 | ✅ | `GPU_EXPORTER_METRICS` معرَّفة مرّةً في `support_compose.py:36-47`، وتستوردها الوحدتان. |
| 8 | ✅ | `08-local-runbook.md:33`، ويذكر شبكة `gpu-metrics` أيضاً. |
| 9 | ✅ | `stack-commands.md:604`: «ثلاث خدماتٍ دائماً … ومع `monitoring` اثنتين أخريين». |
| 10 | ✅ | `server-gpu-plan.md:457`: «الجواب (2026‑10‑17)» مع رابط Q‑1، والرابط يصل. |
| 11 | ✅ | `stories.md:121`: أرقام ما بعد 4.2، وتطابق `test_resource_budget.py:450-453`. |

### الملاحظات الجديدة

| # | الشدة | الملف:السطر | الملاحظة | الإصلاح المطلوب |
|---|---|---|---|---|
| 12 | مهمة | `docs/deploy-server.md:101` | **سطرٌ فارغ يقطع جدول قائمة الأمان، فلا تظهر ث‑7 وث‑8 وث‑9 صفوفاً.** في Markdown ينتهي الجدول عند أوّل سطرٍ فارغ. والأسطر 102-104 بلا رأس جدول، فتصير فقرةً واحدة من نصٍّ فيه خطوطٌ عموديّة. **تحقّقتُ بالعرض:** ‏`markdown-it` (commonmark مع الجداول) يضع ث‑1…ث‑6 في `<tr>`، ثمّ `</table>` ثمّ `<p>\| ث‑7 …`. وتسجيل ث‑7…ث‑9 كان شرط PASS في المراجعة الأمنيّة. | حذف السطر 101. |
| 13 | مهمة | `deploy/gpu-preflight.sh:25-27` و`:178` و`:184`؛ `docs/deploy-server.md:57`؛ `docs/stack-commands.md:593`؛ `tests/unit/test_gpu_preflight.py:400` | **`--smoke` لم يعد يغلب غياب وقت التشغيل على مضيفٍ بلا `nvidia-ctk`، والنصوص ما زالت تعد بالخروج 0.** حالة `absent` تُفشل `ctk`، فيُتخطّى `--smoke` (`:261`)، والخروج 12 دائماً. وهذه حال Docker Desktop المعتادة، فأداة NVIDIA فيه داخل آلته الافتراضيّة لا في التوزيعة. **السيناريو، شغّلتُه بمزيّفات الاختبار:** لا وقت تشغيل `nvidia`، و`--smoke`، ولا `nvidia-ctk` على PATH. فيقول سطر toolkit: «WARN … the live check (--smoke) below decides». ثمّ `ctk FAIL … not on PATH`، ثمّ `smoke SKIP`، ثمّ `FAIL 12 (NVIDIA Container Toolkit)`، ولا `docker run`. فمن يتبع التلميح «on a CDI-only or Docker Desktop host, run again with --smoke» يعود إلى 12 نفسه. **القرار نفسه صحيحٌ أمنيّاً** (SR‑2)، والعطب في النصوص. ومضيف CDI عليه `nvidia-ctk` عادةً، لأنّه يولّد ملفّ CDI، فيبقى `--smoke` صالحاً له. | **(أ)** تُصحَّح النصوص الخمسة: Docker Desktop، وأيّ مضيفٍ بلا `nvidia-ctk` على PATH، يخرج بـ12 عمداً، لأنّ إصدار الأداة لا يُقرأ. و«`--smoke` يغلب» يبقى لمضيف CDI وحده، وبشرط `ctk OK`. **(ب)** سطر WARN في `:178` لا يَعِد بأنّ `--smoke` «يقرّر» إن كان `ctk` سيمنعه. **(ج)** اختبارٌ للحالة: لا وقت تشغيل، و`--smoke`، و`ctk=False`، والنتيجة 12 بلا `docker run`. وإن أراد المالك إبقاء Docker Desktop مدعوماً فهذا قراره، مثلاً WARN بدل FAIL في `absent` مع `--smoke` ناجح. |
| 14 | مهمة | `docs/runbooks/alerts.md:525` و`:527` | **خطوة إجراءٍ في دليل `AizzakLlmCircuitOpen` ما زالت بالسطر العاري.** وهي من صنف #1 وSR‑5. النصّ: «فالسبب غالباً نقصُ `COMPOSE_PROFILES=wsl-ollama` من `.env`». **السيناريو:** يرى المالك الجسر غائباً، فيضيف السطر إلى `.env` كما كُتب: `COMPOSE_PROFILES=wsl-ollama`. وعند أوّل إعادة إنشاء تسقط المراقبة كلّها بصمت، وهو العطب الذي تحرسه الميزة. والسطر 527 يصف الخادم بـ`COMPOSE_PROFILES=local-llm`. والحارس الجديد لا يرى الموضعين، فهو يفحص `stack-commands.md` و`quickstart.md` وحدهما. **أمّا `08-local-runbook.md:2656`** («ونسيانُ `COMPOSE_PROFILES=wsl-ollama` … يُسقط الجسر») فمقبول: سردٌ لا سطرٌ يُنسخ، ومعناه صحيح. | **السطر 525:** «نقصُ `wsl-ollama` من سطر `COMPOSE_PROFILES` في `.env` (والسطر كاملاً: `COMPOSE_PROFILES=wsl-ollama,monitoring`)». **السطر 527:** `COMPOSE_PROFILES=local-llm,monitoring`. **الحارس:** يُمدّ نمط `test_both_guides_carry_the_monitoring_lines` إلى `runbooks/alerts.md` و`design/08-local-runbook.md`، وفي 08 كانت أسطر §3.0 في #1. ومعه يُعاد صوغ `08:2656` إلى «نسيانُ `wsl-ollama` في `COMPOSE_PROFILES`»، كما في `stack-commands.md:120`. |
| 15 | تحسين | `docs/stack-commands.md:590`؛ `docs/deploy-server.md:21` و§1.2 (`:29`) | **وصف الفحص لم يتبع فحص `ctk`.** الأمر 40 يقول «يفحص أربعة أشياء»، وهي الآن خمسة، ويعرّف 12 بـ«الأداة غير مضبوطة» وحده. وجدول المتطلّبات و§1.2 لا يذكران الحدّ 1.17.8، فمن يثبّت الأداة لا يعرفه حتّى يفشل الفحص. | **الأمر 40:** «خمسة أشياء … وإصدار `nvidia-ctk` ≥ 1.17.8»، و12: «… أو أقدم من 1.17.8». **جدول المتطلّبات:** «≥ 1.17.8 (`nvidia-ctk --version`)». |
| 16 | تحسين | `docker-compose.gpu.yml:3-4` و`:8`؛ `tests/unit/test_compose_gpu_overlay.py:95-96` و`:165` | **رأس ملفّ الكرت وأسماء الاختبارات أضيق من المحتوى الآن.** الرأس يقول «Five entries, and only these keys» و«prometheus ONE mount». لكنّ الملفّ صار فيه مفتاح `networks:` أعلى، ويضمّ `prometheus` إلى شبكة. والاختباران `test_prometheus_gets_one_mount_and_nothing_else` و`…has_only_services_and_exactly_five` (في docstring: «prometheus (one mount)») يؤكّدان الشبكة أيضاً. | **الرأس:** «prometheus ONE mount and the `gpu-metrics` network»، وسطرٌ للشبكة العليا. **اسم الاختبار:** مثل `test_prometheus_gets_one_mount_and_the_gpu_network`. |
| 17 | تحسين | `docker-compose.gpu.yml:115-117`؛ `docs/deploy-server.md:104` (ث‑9، عمود الأثر)؛ `docs/server-gpu-plan.md:134` | **أرقام الذروة كُتبت قبل قياس QA في الجولة 1، ولم تُحدَّث بعده.** التعليق: «WSL peak 54.1 MiB … (the peak may drop)». وث‑9: «لم يُعَد قياسها بهذين الخيارين». والصفّ 4.2: «ذروة 54.1 م.ب». **والمقيس بالخيارين** (`test-plan.md` §6‑ب): 56.2 MiB، فلم ينخفض. | **التعليق:** «WSL peak 56.2 MiB with both flags (54.1 without); x 1.5 > 64m; re-measure in 7.3». **ث‑9:** «أُعيد محلّيّاً: 56.2 م.ب بالخيارين، والحدّ خطرٌ مقبول». **الصفّ 4.2:** 56.2. |
| 18 | تحسين | `docs/delivery/monitoring-profile/design.md:241` و`:270-272`، و§2 (`:52`) | **التصميم لا يذكر تغييرات المراجعة الأمنيّة.** مواصفة المصدّر في §3‑ج ما زالت بـ`command` واحد، وبلا `networks`، وفيها «والجمع متزامنٌ مع كلّ كشط». وقائمة الملفّات في §2 بلا `deploy/gpu-preflight.sh`. فمن يقبل الميزة (المرحلة 11)، أو يعيد القياس في 7.3، يقارن بعقدٍ قديم. | ملاحظةٌ قصيرة في §3‑ج و§2: «بعد المراجعة الأمنيّة (SR‑1 وSR‑2 وSR‑4، موافقة المالك 2026‑10‑18): …»، مع الإحالة إلى الالتزامات. |

### الشيفرة الجديدة

#### `deploy/gpu-preflight.sh`: فحص `ctk`
- **قراءة الإصدار:**
  - النمط `([0-9]+)\.([0-9]+)\.([0-9]+)` بلا تثبيت بداية، ويُطبَّق على السطر الأوّل وحده، ثمّ يُقارَن عدداً بـ`10#`.
  - **شغّلتُ حالاتٍ إضافيّة بمزيّفات الاختبار نفسها** (ملفٌّ في scratch يستورد `_run`). وكلّها كما يُتوقّع:
    - `…version 1.17.8` يليه سطر `commit:` ← 0. وكذلك `1.17.8-1` و`v1.17.8` و`1.17.08` ← 0.
    - `1.18.0-rc.1` ← 0، فهو يُقرأ 1.18.0. و`1.17.7-rc.1` ← 12.
    - ناتجٌ سطره الأوّل فارغ ← 12 «no usable answer». والخطأ هنا في اتّجاه الرفض، فهو آمن.
    - `nvidia-ctk` معلَّق ← 12 «timed out after 5s»، في أقلّ من 20 ث.
  - وعلى هذا الجهاز يطبع `nvidia-ctk --version` على stdout: ‏`NVIDIA Container Toolkit CLI version 1.20.1`، ثمّ `commit: …`، برمز 0.
- **`set -euo pipefail`:** لا مسار يكسره.
  - `run` يلتقط الرمز بـ`|| RC=$?`.
  - والنمط داخل `if`.
  - و`read <<<` يعيد 0.
  - و`BASH_REMATCH` لا يُقرأ إلّا بعد تطابق.
- **الرمز 12 والأسبقيّة:**
  - لا يكون `CTK_STATE=skipped` إلّا مع فشل الكرت أو Docker، وهذان يخرجان قبله بـ10 أو 11.
  - فشل الإصدار يأتي بعد 11 و13 و10، وقبل 14.
  - رأس السكربت (`:35-37`) وجدول `deploy-server.md:67` متّفقان.
  - وتحليل الاختبار للأسطر الرقميّة ما زال يطابق: `_script_exit_codes`، والنمط `^\|\s*(\d{1,2})\s*\|`. فأسطر التتمّة في الرأس لا تبدأ برقم، وصفوف ث‑7…ث‑9 تبدأ بـ«ث».
- **`--smoke` لا يُشغَّل مع `ctk` غير OK** (`:261-264`). ويحرسه `test_an_old_toolkit_is_not_overridden_by_a_passing_smoke`: لا `docker run`. والثغرة الوحيدة في النصوص حوله (#13).
- **لا يثبّت شيئاً:**
  - المزيّف يرفض أيّ وسيطٍ غير `--version` برمز 64.
  - و`test_never_installs_anything` يؤكّد أنّ كلّ نداءٍ هو `["--version"]`.

#### `docker-compose.gpu.yml`: الشبكات والخيارات
- **مضيفٌ بالملفّ الأساسيّ وحده لا يتغيّر:**
  - `docker-compose.yml` مطابقٌ لـ`278defa` بايتاً ببايت، والشبكة الجديدة معرّفة في ملفّ الكرت وحده.
  - وقستُ دون ملفّ الكرت: كلّ الخدمات على `default` وحده، ولا `gpu-metrics`.
- **مع ملفّ الكرت و`monitoring` و`local-llm`** (قرأتُ مفاتيح الشبكات وحدها من `config --format json`):
  - `prometheus` على `[default, gpu-metrics]`، والمصدّر على `[gpu-metrics]` وحده.
  - `embedding` و`ollama` و`app` و`grafana` باقيةٌ على `default`، فلا تفقد خدمةٌ شبكتها.
  - و`gpu-metrics` ‏`internal: true`.
- **ودون `monitoring`** لا تظهر `gpu-metrics` في النموذج أصلاً.
- **لا خدمة أخرى تحتاج الشبكة:**
  - لا يذكر المصدّرَ غيرُ `scrape-gpu.yml` وحالات promtool.
  - Grafana تسأل Prometheus على `default`.
  - وAlloy يقرأ السجلّات من Docker، لا من الشبكة.
- **الخياران `--collect.interval=15s` و`--web.max-requests=2`:**
  - لم أتحقّق من مصدر v1.15.1 بنفسي.
  - لكنّ QA شغّل المصدّر بهما 10 دقائق: `healthy`، و`restarts=0`، وPrometheus يكشطه. ولو كان الخيار مجهولاً لسقط الإقلاع.
  - و15s تساوي `scrape_interval` العامّ.

#### `deploy/resource-budget.sh`
الفرع الجديد صحيح:
- `with_profiles` قيمةٌ منطقيّة (`:140`).
- و`counted` يضمّ الخدمات المؤجّلة معه (`:289`)، فالسطر يطابق المجموع.

#### الاختبارات الجديدة والمعدّلة
- **تؤكّد السلوك، لا الأسماء:**
  - مقارنة الإصدار بسبع حالات، منها `1.17.10` و`1.9.30` ضدّ المقارنة النصّيّة.
  - و`--smoke` لا يغلب الإصدار القديم.
  - وغياب الأداة، والإجابة غير المقروءة بثلاث صور.
  - وعزل الشبكة في النصّ وفي النموذج المدمج (`test_the_overlay_changes_only_the_services_it_names`، عبر `docker compose config`).
  - وأثبت QA أنّ كلّ حارسٍ يفشل بإفساده.
- **العزل عن البيئة:**
  - `test_compose_gpu_overlay` و`test_monitoring_profile` يحذفان `COMPOSE_FILE` و`COMPOSE_PROFILES`، كما في الجولة 0.
  - و`load_compose` لا يقرأ البيئة أصلاً.
  - واختبارات `test_gpu_preflight` الجديدة تبني PATH والبيئة من الصفر.

### الوثائق
- **`deploy-server.md`:**
  - §1.4 فيه الحدّ الأدنى، وجدول الرموز محدَّث.
  - §2 فيه ما يغيب دون المراقبة (SR‑6)، ولا صفّ جدولٍ فيه يبدأ برقم.
  - ث‑6 بنصّ العلاج الجديد.
  - ث‑7…ث‑9 مكتوبة، لكنّها لا تظهر صفوفاً (#12).
- **`stack-commands.md` و08:**
  - الأسطر مصحَّحة (#1).
  - وسطر «آخر مطابقة» (`stack-commands.md:11`) صار «سطر المالك الأوّل (profile الجسر)». هو تاريخٌ أُعيدت صياغته ليمرّ الحارس، وهذا مقبول.
- **اللوحة:**
  - الصفّان 4.1 و4.2 محدَّثان، وجواب قرار 7 مضاف.
  - عدّ `server-gpu-summary.html` كما هو: 8 مكتملة، و9 جارية، و12 لم تبدأ، من 29 مقالة. والسطر المضاف «الحالة» تحت 4.2 صحيح.
- **`architecture.html`:** ‏29 في الموضعين.
- **المعرّفات:** النصّ العربيّ المضاف كلّه بالشرطة U+2011. والاستثناء الوحيد `AC-3.3` داخل تعليق bash في design.md، وهو كود.

### ما شغّلتُه (HEAD `f5591c2`)
| الفحص | النتيجة |
|---|---|
| `ruff format --check .` و`ruff check .` | 821 ملفّاً منسَّقة، وAll checks passed |
| `lint-imports` | 8 kept، و0 broken |
| `mypy src` | لم يُشغَّل: `src/` لم يتغيّر في `278defa..HEAD` |
| 13 وحدة متأثّرة و`tests/architecture`، ببيئةٍ نظيفة (بلا `COMPOSE_FILE` ولا `COMPOSE_PROFILES`) | `530 passed`، ‏rc 0 |
| الوحدات الـ13 نفسها، بـ`COMPOSE_FILE` الثلاثيّ كما في CI | `529 passed` |
| `deploy/prometheus/test-rules.sh` | `test-rules: OK`، و29 قاعدة، و`check config` ناجحٌ بالوضعين، ‏rc 0 |
| إفساد #2 على نسخةٍ في scratch | الحارس يمسك: 1 failed |
| حالات `ctk` الإضافيّة وحالة #13، بمزيّفات `test_gpu_preflight` | 12 passed، كما وُصف أعلاه |

- لم أشغّل `tests/integration`، ولا حاويةً غير حاويات promtool المؤقّتة في `test-rules.sh`.
- نسخة scratch وملفّ الحالات خارج المستودع.

</div>
