<div dir="rtl">

# مراجعة الشيفرة: المراقبة اختياريّة وأرقام الكرت (`monitoring-profile`)

- **النطاق المراجَع:** `git diff master...feat/monitoring-profile` في `/home/AIZZAK`: تسعة التزامات من `f2158a0` إلى `3880ff9`، و35 ملفّاً. قرأتُ كلّ ملفٍّ متغيّر كاملاً، لا مقاطع الفرق وحدها.
- **الحكم:** **PASS** في الجولات الأربع.
  - **الجولة 0:** لا ملاحظة حاجبة. فيها 4 ملاحظات مهمّة و7 تحسينات، وأُصلحت كلّها في الجولة 1.
  - **الجولة 1:** لا حاجب. فيها 3 ملاحظات مهمّة جديدة (#12…#14) و4 تحسينات (#15…#18). انظر القسم «الجولة 1» أسفل.
  - **الجولة 2:** لا حاجب. #12…#18 أُصلحت، و#16 جزئيّاً. فيها ملاحظة مهمّة جديدة واحدة (#19) و5 تحسينات (#20…#24). انظر القسم «الجولة 2» أسفل.
  - **الجولة 3:** لا حاجب. #19…#23 أُصلحت، واكتمل بها #16، و#24 يُذكر في وصف الـPR بقرار المالك. فيها ملاحظة مهمّة جديدة واحدة (#25) وتحسينان (#26، #27)، وكلّها متابعةٌ تُكتب في وصف الـPR. انظر القسم «الجولة 3» أسفل.
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

## الجولة 2 (إعادة العمل، `7bda359..08e4540`)

- **النطاق:** `git diff 7bda359..08e4540`: أربعة التزامات (`dc1ca50` للوثائق، و`9234a17` للفحص، و`3eab510` لحدود الكشط، و`08e4540` لرأس ملفّ الكرت)، و13 ملفّاً. و`7bda359` يحمل ملفّات مراجعة الجولة 1 وحدها، فلم أراجعه.
  - قرأتُ كاملاً: `deploy/gpu-preflight.sh` و`deploy/prometheus/scrape-gpu.yml`. وقرأتُ بقيّة الملفّات مع سياق كلّ تعديل.
  - ومعها: SR‑9…SR‑14 في `security-review.md` «الجولة 1»، وقرارات المالك في `status.md` (2026‑10‑18).
- **الحكم:** **PASS**. لا حاجب. الملاحظات #12…#18 أُصلحت، و#16 جزئيّاً (بقيّته في #23). فيها ملاحظة مهمّة جديدة واحدة (#19) و5 تحسينات (#20…#24).
- **قرارات المالك محترمة (2026‑10‑18)، ولا أطرح منها ملاحظة:**
  - مضيفٌ بلا `nvidia-ctk` يبقى 12 حتّى مع `--smoke`، وتغيّرت النصوص وحدها.
  - و`--web.enable-lifecycle` باقٍ، وخطره مسجّل في ث‑7.
  - وفحص `nvidia-container-cli` الاختياريّ لم يُضَف.

### حال الملاحظات #12…#18

| # | الحال | الدليل |
|---|---|---|
| 12 | ✅ | `deploy-server.md:94-104` جدولٌ واحد بلا سطرٍ فارغ، فث‑7…ث‑9 صفوف. وحارسٌ جديد: `test_every_security_row_sits_in_one_contiguous_table` (`test_gpu_preflight.py:663`). أعدتُ السطر الفارغ قبل ث‑7 في نسخة scratch، فأمسكه (M10). |
| 13 | ✅ | **(أ)** رأس السكربت `:25-29`، وسطر `ctk` الغائب `:240` («exits 12 on purpose, even with --smoke»)، و`deploy-server.md:57-58`، و`stack-commands.md:593`، وdocstring الاختبار `:400-401`. كلّها تقول إنّ `--smoke` يغلب على مضيف CDI وحده بشرط `ctk OK`، وإنّ مضيفاً بلا `nvidia-ctk` يخرج بـ12 عمداً. **(ب)** سطر WARN ‏`:223` لا يَعِد بأنّ `--smoke` يقرّر، والتلميح `:229` مشروطٌ بفحص `ctk`. **(ج)** `test_no_runtime_and_no_nvidia_ctk_is_exit_12_even_with_smoke` (`:410`): الرمز 12، ولا `docker run`، و`smoke SKIP`، والنصّ القديم غائب. أفسدتُ الشرط (`--smoke` يُشغَّل مع `absent`)، ثمّ أعدتُ النصّ القديم، فأمسك في الحالتين (M8، M9). |
| 14 | ✅ | `alerts.md:525` و`:527` بـ`,monitoring`، و`08-local-runbook.md:2656` صار «نسيانُ `wsl-ollama` في `COMPOSE_PROFILES`». والحارس `test_no_runbook_shows_a_compose_profiles_line_without_monitoring` (`test_monitoring_profile.py:347-357`) يغطّي الدليلين. أعدتُ السطر العاري في كلٍّ منهما فأمسك (M11، M12). ولا سطر `COMPOSE_PROFILES=` بلا `monitoring` باقٍ فيهما، ولا بصيغة `export` أو داخل أمر. |
| 15 | ✅ | `stack-commands.md:590`: «خمسة أشياء» ومنها `nvidia-ctk` ≥ 1.17.8، و12 «أو إصدارها أقدم من 1.17.8». وجدول المتطلّبات `deploy-server.md:21-22`، و§1.2 ‏`:31`. |
| 16 | ⚠️ جزئيّاً | سطرا `prometheus` والمصدّر في الرأس (`docker-compose.gpu.yml:8-11`) يذكران `gpu-metrics`. واسم الاختبار صار `test_prometheus_gets_one_mount_and_the_gpu_network` (`test_compose_gpu_overlay.py:165`)، والـdocstring في `:96` و`:286-288` محدَّث. **وبقي** سطرٌ للمفتاح الأعلى `networks:` في الرأس (#23). |
| 17 | ✅ | `docker-compose.gpu.yml:117`: «56.2 MiB with both flags below (54.1 without)». وث‑9 في `deploy-server.md:104`، والصفّ 4.2 في `server-gpu-plan.md:134`. و`server-gpu-summary.html` لا رقم ذروة فيه. |
| 18 | ✅ | ملاحظتان مؤرَّختان: `design.md:112-115` في آخر §2، و`:285-289` في آخر §3‑ج. فيهما SR‑1 وSR‑2 وSR‑4 والالتزامات، و`gpu-preflight.sh` داخل النطاق بموافقة المالك. لكنّ عدّ البنود في الثانية لا يطابق (#22). |

### الملاحظات الجديدة

| # | الشدة | الملف:السطر | الملاحظة | الإصلاح المطلوب |
|---|---|---|---|---|
| 19 | مهمة | `deploy/prometheus/scrape-gpu.yml:8-10` و`:17-18`؛ `tests/unit/test_observability_stack_wiring.py:233-234` | **أرقام تعليل الحدود لم تُقَس، وهي نحو نصف الحقيقة، والاختبار يحرس الرقم الخطأ.** طلب SR‑9 أن يكون `sample_limit` المقيسَ × 4. والتعليق يقدّر: «a few dozen series per GPU … plus ~80 Go/process series; 8 GPUs is ~400 samples»، و«the widest series carry < 10». **قستُ بالمصدّر نفسه** (البصمة والخياران نفسهما، في حاويةٍ مؤقّتة على منفذ loopback، ثمّ أُزيلت)، على كرتٍ واحد: 151 عيّنة في الكشط، منها 95 لكلّ كرت (بتسمية `uuid`) و56 ثابتة. والجسم 30.5 ك.ب. وأعرض سلسلة `nvidia_smi_gpu_info` بـ12 تسمية، و14 مع `job` و`instance`. فثمانية كروت ≈ 816 عيّنة، لا 400. وسقفها إن صارت حقول `nvidia-smi --help-query-gpu` الـ214 كلّها أرقاماً: 1768. **القيم الحاليّة سليمة:** 5000 ≥ 816 × 4، و2MB فوق الجسم المتوقّع لثمانية كروت (نحو 90 ك.ب) بأكثر من 20 ضعفاً، و30 تسمية فوق 14. **الخطر في التعليل والحارس:** الاختبار يقبل أيّ `sample_limit` فوق 400. **السيناريو (أفسدتُه، M14):** من يعيد ضبط الحدود في 7.3 يصدّق «~400 for 8 GPUs» فيكتب `sample_limit: 500`. يمرّ الاختبار. وعلى خادمٍ بستّة كروتٍ فأكثر (6 × 95 + 56 = 626 عيّنة) يسقط كلّ كشط: `up` صفرٌ دائماً، و`AizzakScrapeTargetDown` مشتعلٌ بلا توقّف، ولا أرقام كرت. | **التعليق:** الأرقام المقيسة، مثلاً: «measured on one RTX 4060 (2026‑10‑18): 151 samples (95 per GPU + 56), 30.5 KB, widest series 12 labels; 8 GPUs ≈ 816 samples; ceiling 8 × 214 fields + 56 = 1768». وكذلك تعليقا السطرين 17 و18. **الحارس:** حدٌّ أدنى من القياس لا من التقدير، مثل `assert job["sample_limit"] >= 4 * (8 * 95 + 56)`، أو السقف `>= 8 * 214 + 56`. |
| 20 | تحسين | `tests/unit/test_gpu_preflight.py:578-590` | **لا حالة لإصدار Engine تجريبيٍّ أحدث من الحدّ.** قائمة `ctk` فيها `later-rc` (‏`1.18.0-rc.1` ← 0)، وقائمة Engine ليس فيها مثلها. **أفسدتُ الشرط (M6):** حذفتُ `[[ $shown == "$MIN_DOCKER_ENGINE" ]] &&` من `gpu-preflight.sh:202`، فصار كلّ Engine تجريبيّ يُرفض بـ11، ومنه `29.0.0-rc.1`. ومرّت الاختبارات كلّها. | `pytest.param("29.0.0-rc.1", True, id="later-rc")` في قائمة `test_the_docker_engine_minimum_is_exit_11`. |
| 21 | تحسين | `deploy/gpu-preflight.sh:45-47`؛ `docs/deploy-server.md:73` | **تعليل الأسبقيّة لم يعد صحيحاً مع حدّ Engine.** يقول النصّان إنّ أعطال المنصّة تسبق أعطال الكرت «لأنّها تمنع التشغيل على المعالج أيضاً». لكنّ CVE‑2024‑29018 يخصّ شبكة `gpu-metrics`، وهي في `docker-compose.gpu.yml` وحده، و`docker-compose.yml` بلا شبكةٍ `internal`. **السيناريو (شغّلتُه بالمزيّفات):** خادمٌ بلا كرت وEngine ‏`25.0.4` يأخذ 11، وعمود التثبيت يقول «توقّف» (`deploy-server.md:67`)، مع أنّ المكدّس على المعالج كان سيعمل. **والسلوك نفسه مقبول:** المحرّك قديمٌ فعلاً، وجدول المتطلّبات (`:22`) يطلب 26.0.0 للخادم. فالخطأ في الجملة وحدها. | جملةٌ في الموضعين، مثل: «Engine أقدم من 26.0.0 يعطي 11 حتّى على مضيفٍ بلا كرت. هو لا يمنع التشغيل على المعالج بنفسه، لكنّه حدٌّ للخادم يُحدَّث قبل أيّ شيء». |
| 22 | تحسين | `docs/delivery/monitoring-profile/design.md:285` و`:112-115` | **الملاحظة المؤرَّخة في §3‑ج تقول «ثلاثة أمور» ثمّ تعدّ أربعة.** والنسخة في §2 بلا البند الرابع (SR‑9 وSR‑10)، مع أنّ SR‑10 غيّر `deploy/gpu-preflight.sh`، وهذا الملفّ يخصّ قائمة §2. | «أربعة أمور» في `:285`، وإضافة البند الرابع إلى `:112-115`. |
| 23 | تحسين | `docker-compose.gpu.yml:3-4` | **بقيّة #16.** الرأس يقول «Five entries, and only these keys»، ولا سطر فيه للمفتاح الأعلى `networks:` (‏`gpu-metrics` ‏`internal: true`، `:163-167`). والاختبار `test_the_overlay_has_only_services_and_exactly_five` يثبت `{"services", "networks"}`. | سطرٌ في الرأس، مثل: «plus one top-level network, `gpu-metrics` (internal), shared by the exporter and Prometheus». |
| 24 | تحسين | الالتزام `3eab510` | **الالتزام يحمل حارساً من غير موضوعه.** عنوانه «scrape limits and no redirects for the GPU exporter job»، لكنّه يضيف أيضاً `test_no_runbook_shows_a_compose_profiles_line_without_monitoring` (`test_monitoring_profile.py:347-357`). وهذا حارس SR‑14 لإصلاح الوثائق في `dc1ca50`. **السيناريو:** لو رُدّ `3eab510` وحده (حدٌّ يرفض كشط خادمٍ حقيقيّ مثلاً)، لسقط حارس الدليلين معه. | اختياريّ، والفرع لم يُرفع: نقل الحارس إلى `dc1ca50`، أو ذكره في جسم رسالة `3eab510`. |

### الشيفرة الجديدة

#### `deploy/gpu-preflight.sh`: حدّ Docker Engine
- **المقارنة:** `version_ge` (`:108-122`) عدديّة بـ`10#`، وتعمل على `shown` المطبَّع، فلا خانة فارغة ولا صفر بادئ. والنمط `^v?([0-9]{1,4})\.([0-9]{1,4})\.([0-9]{1,4})` (`:195`) مثبَّتٌ بالبداية.
- **لا حارس للخانة الخامسة هنا، وهذا سليم:**
  - المكوّنان الأوّل والثاني يلزمهما `\.` بعدهما، فرقمٌ فيهما أطول من 4 خانات لا يطابق، ويصير «unreadable» ← 11.
  - والقطع لا يقع إلّا في المكوّن الثالث، وهو يصغّره دائماً، فلا يصنع نجاحاً كاذباً. شغّلتُ `25.0.99999` ← 11.
- **الإصدار التجريبيّ** (`:201-202`): `is_prerelease` على ما بعد التطابق، ولا يُرفض إلّا تجريبيٌّ من 26.0.0 نفسه. شغّلتُ بمزيّفات الاختبار:
  - `26.0.0-rc1` و`26.0.0-beta.2` ← 11.
  - `29.0.0-rc.1` و`v26.0.0` و`26.1.3+azure-1` و`026.000.000` ← 0.
  - `26.0` و`dev` والقيمة الفارغة ← 11.
- **`BASH_REMATCH` في `:202`** ما زال من تطابق `:196`: ‏`version_ge` لا تستعمل `=~`، و`==` مطابقة نمطٍ لا تمسّه.
- **الرمز 11 لا يصيب مضيفاً بلا كرت خطأً:**
  - بلا `nvidia-smi` وEngine ‏`29.6.1` ← 10. و`nvidia-smi` يفشل ← 10. وبلا كرت وCompose قديم ← 13.
  - ولا 11 إلّا مع Engine قديمٍ أو غير مقروء فعلاً: `25.0.4` و`dev` ← 11. والتعليل وحده يحتاج جملة (#21).
- **Engine قديم يُبقي `DOCKER_OK=0`،** فيُتخطّى `toolkit` و`ctk` و`--smoke`، فلا نداء لـ`nvidia-ctk` ولا `docker run`. والرمز 11 يسبق 13 و12. شغّلتُ Engine قديماً مع Compose قديم ← 11، ومع وقت تشغيلٍ غائب و`--smoke` ← 11 بلا `docker run`.

#### `deploy/gpu-preflight.sh`: نمط `ctk`
- **التثبيت:** النمط يبدأ بكلمة `version ` (`:244`)، فسطرٌ بلا الكلمة ← 12. وكلّ مكوّنٍ من 1 إلى 4 خانات، فلا التفاف حسابيّ.
- **حارس الخانة الخامسة** (`:252`): إن بدأ ما بعد التطابق برقم فالإجابة غير مقروءة ← 12. وهو لازمٌ هنا دون Engine، لأنّ قطع `1.17.18446744073709551624` يعطي `1.17.1844`، وهذا فوق الحدّ.
- **التجريبيّ** (`:259-260`): `rc` أو `alpha` أو `beta` بعد فاصلٍ اختياريّ (`-` أو `~` أو `.` أو `+` أو `_`)، بلا حساسيّة للحالة. شغّلتُ:
  - `1.17.8rc1` و`1.17.8-RC.1` ← 12.
  - `1.17.8.1`، و`1.17.8` يليه سطر `commit:` ← 0.
  - `version v1.17.8` ← 12. وهذا اتّجاه الرفض، و`nvidia-ctk` الحقيقيّ لا يطبع `v`.
- **على هذا الجهاز** (بلا `--smoke`): `docker OK server 29.6.1 (>= 26.0.0)`، و`ctk OK 1.20.1 (>= 1.17.8)`، والرمز 0.

#### `set -euo pipefail` والأسبقيّة
- **لا مسار يكسر `set -e`:**
  - `is_prerelease` و`version_ge` لا تعيدان 1 إلّا داخل شرط `if`، أو بعد `!` أو `&&`.
  - و`read -a <<<` يعيد 0.
  - و`BASH_REMATCH` لا يُقرأ إلّا بعد تطابق.
  - ولا أنابيب جديدة.
- **`set -u`:** المصفوفتان في `version_ge` فيهما ثلاثة عناصر دائماً، لأنّ المدخلين x.y.z مطبَّعان أو ثابتان.
- **الأسبقيّة:** `verdict` (`:343-370`) لم يتغيّر: 11، ثمّ 13، ثمّ 10، ثمّ 12، ثمّ 14.
  - ورأس السكربت (`:36-41` و`:45-47`)، و`usage` (`:89`)، وجدول `deploy-server.md:64-70`، كلّها متّفقة.
  - واختبار الرموز ما زال يطابق: لا صفّ يبدأ برقم في `deploy-server.md` إلّا الأسطر السبعة في جدول الرموز.

#### `deploy/prometheus/scrape-gpu.yml`
- **promtool يقرأ المفاتيح فعلاً،** فهو يحمّل الملفّ عبر نمط `scrape_config_files`. أفسدتُ نسختين في scratch: `body_size_limit: 2 megabytes` ← «unknown unit»، و`follow_redirect` ← «field … not found». فما يقوله docstring الاختبار صحيح.
- **القيم على 8 كروت سليمةٌ بهامش،** والأرقام في #19.
  - `label_value_length_limit: 256`: أطول قيمةٍ مقيسة 40 حرفاً، وهي بصمة `revision`.
  - و`label_name_length_limit: 64`: أطول اسمٍ مقيس 20 حرفاً (`driver_model_current`).
- **`follow_redirects: false` لا يمسّ الكشط العاديّ:** المصدّر يجيب `/metrics` بـ200 مباشرة، دون تحويل.

#### الاختبارات الجديدة
- **أفسدتُ 16 حارساً على نسخةٍ من الشجرة في scratch** (`git archive 08e4540`). تأكّدتُ أوّلاً أنّها خضراء (163 passed في الوحدات الأربع). ثمّ طبّقتُ كلّ إفسادٍ وحده وأعدتُ الملفّ بعده:

| الإفساد | النتيجة |
|---|---|
| M1: حذف حارس الخانة الخامسة | يمسك (`wrap`) |
| M2: حذف قاعدة التجريبيّ في `ctk` | يمسك (4 حالات) |
| M3: النمط القديم غير المثبَّت | يمسك (`no-version-word` و`wrap`) |
| M4: فحص Engine ناجحٌ دائماً | يمسك (3 حالات) |
| M5: حذف شرط التجريبيّ في Engine | يمسك (`rc-of-the-minimum`) |
| M6: كلّ Engine تجريبيّ يُرفض | **لا يمسك** (#20) |
| M7: كلّ `ctk` تجريبيّ يُرفض | يمسك (`later-rc`) |
| M8: سطر WARN القديم | يمسك |
| M9: `--smoke` يُشغَّل مع `ctk` غائب | يمسك |
| M10: سطرٌ فارغ قبل ث‑7 | يمسك |
| M11 وM12: سطرٌ عارٍ في `alerts.md` وفي 08 | يمسك في الاثنين |
| M13 وM15: حذف `follow_redirects` وحذف `body_size_limit` | يمسك في الاثنين |
| M14: `sample_limit: 500` | **لا يمسك** (#19) |
| M16: الحدّ 25.0.0 | يمسك (4 حالات، منها حارس المتغيّر الواحد) |

- **العزل عن البيئة:** اختبارات `test_gpu_preflight` الجديدة تبني PATH والبيئة من الصفر كما قبل. و`FAKE_SERVER_VERSION` افتراضه `29.6.1`، فالحالات القديمة لا تتغيّر.

### الوثائق
- لا صفّ جدولٍ في `deploy-server.md` يبدأ برقم إلّا صفوف جدول الرموز (`:64-70`). وصفوف الأمان تبدأ بـ«ث».
- **`<div dir="rtl">`:** يلفّ العربيّة في `deploy-server.md` و`stack-commands.md` و`runbooks/alerts.md` و`server-gpu-plan.md` و`design.md`. و`08-local-runbook.md` من `docs/design/`، فلا يُلفّ، كما في القاعدة.
- **المعرّفات:** فحصتُ كلّ سطرٍ عربيّ مضاف خارج مقاطع الشيفرة. فـSR‑ وCVE‑ وث‑ وBUG‑ والتواريخ كلّها بـU+2011.
- لا وعد قديم باقٍ بأنّ `--smoke` يغلب على Docker Desktop، لا في `docs/` ولا في `deploy/`.
- ث‑7 يذكر `/-/reload` و`--web.enable-lifecycle` وحدّ Engine، كما قرّر المالك.

### رسائل الالتزامات
- الأربعة كلّها: عنوانٌ، ثمّ سطرٌ فارغ، ثمّ التذييلان. و`git log --format='%(trailers)'` يقرأ `Co-Authored-By` و`Claude-Session` في كلٍّ منها.
- الجسم في `9234a17` وحده. ولا قاعدة في المشروع تطلب الجسم (`10-code-standards.md` §11)، وكثيرٌ من التزامات `master` بلا جسم، فلا ملاحظة عليه.
- وما يحمله `3eab510` خارج عنوانه في #24.

### ما شغّلتُه (HEAD `08e4540`)
| الفحص | النتيجة |
|---|---|
| `ruff format --check .` و`ruff check .` | 821 ملفّاً منسَّقة، وAll checks passed |
| `lint-imports` | 8 kept، و0 broken |
| `mypy src` | لم يُشغَّل: `src/` لم يتغيّر في `7bda359..08e4540` |
| 29 وحدة تقرأ ملفّاً متغيّراً، و`tests/architecture`، ببيئةٍ نظيفة (بلا `COMPOSE_FILE` ولا `COMPOSE_PROFILES`) | `906 passed`، ‏rc 0 |
| الوحدات نفسها بـ`COMPOSE_FILE` الثلاثيّ كما في CI | `906 passed`، ‏rc 0 |
| `deploy/prometheus/test-rules.sh` | `test-rules: OK`، و29 قاعدة، و`check config` ناجحٌ بالوضعين، ‏rc 0 |
| `promtool check config` على نسختين مفسدتين من `scrape-gpu.yml` | يرفض الاثنتين، ‏rc 1 |
| 16 إفساداً على نسخة scratch | 14 تمسك، و2 لا تمسك (#19، #20) |
| 20 حالةً إضافيّة بمزيّفات `test_gpu_preflight` | كلّها كما وُصف في «الشيفرة الجديدة» |
| `deploy/gpu-preflight.sh` الحقيقيّ على هذا الجهاز، بلا `--smoke` | rc 0 |
| حاوية المصدّر المؤقّتة (البصمة نفسها، على `127.0.0.1` وحده) | 151 عيّنة و30.5 ك.ب، ثمّ أُزيلت |

- لم أشغّل `tests/integration`، ولم أقرأ `.env`.
- لم أشغّل حاويةً غير حاويات promtool وحاوية المصدّر المؤقّتة. وهذه شُغّلت بـ`--rm`، ثمّ أُوقفت، وتحقّقتُ أنّها زالت.
- نسخة الشجرة وملفّ الحالات وملفّ المقاييس كلّها في scratch، خارج المستودع، وحُذفت نسخة الشجرة.

## الجولة 3 (إعادة العمل، `fb4ccfd..16498e7`)

- **النطاق:** `git diff fb4ccfd..16498e7`: ثلاثة التزامات (`7aa1844` للفحص، و`c576193` لـPrometheus، و`16498e7` للوثائق)، و8 ملفّات. و`fb4ccfd` يحمل ملفّات مراجعة الجولة 2 وحدها، فلم أراجعه.
  - قرأتُ كاملاً: `deploy/gpu-preflight.sh` و`deploy/prometheus/scrape-gpu.yml`. وقرأتُ بقيّة الملفّات مع سياق كلّ تعديل.
  - ومعها: SR‑15…SR‑17 في `security-review.md` «الجولة 2»، وقرارات المالك في `status.md` (2026‑10‑18).
- **الحكم:** **PASS**. لا حاجب. #19…#23 أُصلحت كلّها، واكتمل #16 بـ#23. فيها ملاحظة مهمّة جديدة واحدة (#25) وتحسينان (#26، #27). وهذه الجولة الأخيرة، فالثلاث كلّها **متابعة** لا تمنع الدمج، وتُكتب في وصف الـPR مع #24.
- **قرارات المالك محترمة، ولا أطرح منها ملاحظة:**
  - `--web.enable-lifecycle` باقٍ، وخطره في ث‑7.
  - و#24 بلا إعادة كتابة للتاريخ.
  - ومضيفٌ بلا `nvidia-ctk` يبقى 12 حتّى مع `--smoke`، وEngine قديم يبقى 11.

### حال الملاحظات #19…#24

| # | الحال | الدليل |
|---|---|---|
| 19 | ✅ | **التعليق** (`scrape-gpu.yml:8-14`): الأرقام المقيسة. 151 عيّنة (95 لكلّ كرت و56 ثابتة)، و30.5 ك.ب، و12 تسمية (14 مع `job` و`instance`)، و816 لثمانية كروت، والسقف 1768. وتعليقات `:19` و`:20` و`:23` بالأرقام نفسها. **الحارس** (`test_observability_stack_wiring.py:223-225` و`:241`): الحدّ الأدنى `214 × 8 + 56 = 1768`. أعدتُ إفساد الجولة 2 (`sample_limit: 500`) فأمسك، وكذلك 1767. |
| 20 | ✅ | `test_gpu_preflight.py:609`: ‏`29.0.0-rc.1` ← 0. أعدتُ إفساد M6 (كلّ Engine تجريبيّ يُرفض) فأمسك (`later-rc`). |
| 21 | ✅ | `gpu-preflight.sh:45-50` و`deploy-server.md:72`: «غالباً»، ثمّ الاستثناء الواحد لحدّ Engine وسببه (CVE‑2024‑29018). والسلوك لم يتغيّر: `verdict` (`:348-375`) كما هو. |
| 22 | ✅ | `design.md:112` و`:286`: «أربعة أمور». والبند الرابع في الموضعين (`:116` و`:290`) فيه SR‑9 وSR‑10 وSR‑15. |
| 23 | ✅ | `docker-compose.gpu.yml:3` («Five service entries, plus one top-level network») و`:12-13` (سطر `gpu-metrics`). فاكتمل #16. والتعديل تعليقٌ وحده، فلا يتغيّر `config --hash`. |
| 24 | ⏭ بقرار المالك | لا إعادة كتابة للتاريخ، ويُذكر في وصف الـPR. |

### الملاحظات الجديدة

| # | الشدة | الملف:السطر | الملاحظة | الإصلاح المطلوب |
|---|---|---|---|---|
| 25 | مهمة | `tests/unit/test_observability_stack_wiring.py:257-260` | **متابعة لوصف الـPR.** **حارس قاعدة `keep` يفحص اسمين يمرّان واسمين يسقطان، فلا يرى تضييق التعبير ولا توسيعه.** القاعدة نفسها صحيحة اليوم (انظر «الشيفرة الجديدة»). **السيناريو 1 (R11، شغّلتُه على نسخة scratch):** يضيّق أحدٌ التعبير إلى ما يظنّه حاجة اللوحة: `nvidia_smi_memory_.*\|nvidia_smi_gpu_info\|…`. فيسقط `nvidia_smi_temperature_gpu` و`nvidia_smi_utilization_gpu_ratio`. فلا يشتعل `AizzakGpuTemperatureHigh` أبداً، وتفرغ لوحتا الحرارة والاستعمال. و`up` يبقى 1، فلا يشتعل `AizzakScrapeTargetDown`. **السيناريو 2 (R12):** يضيف أحدٌ `\|aizzak_http_.*`، فتنفتح SR‑15 من جديد. مصدّرٌ مخترَق يحقن `aizzak_http_requests_total`، فيميّع مقام `AizzakRateLimitedShareHigh` (`alerts.yml:962-963`، بلا مرشّح `job`). **مرّ الإفسادان على الوحدات الـ23 كلّها: 851 passed.** و`test-rules.sh` لا يراهما، لأنّ حالات promtool تحقن السلاسل مباشرةً ولا تمرّ بالكشط. | **الموجب:** `for m in GPU_EXPORTER_METRICS: assert keep.fullmatch(m)`. والقائمة معرَّفة أصلاً في `support_compose.py:39-47` لهذا الغرض. **السالب:** كلّ اسم مقياسٍ تقرؤه قواعد `alerts.yml` خارج المجموعة `aizzak-gpu` لا يطابق التعبير. وهذا يمسك أيضاً قاعدةً مقبلة على `process_*` أو `go_*` بلا `job`، وهما عائلتان تُبقيهما القاعدة. |
| 26 | تحسين | `tests/unit/test_gpu_preflight.py:592-600`؛ `deploy/gpu-preflight.sh:192` و`:343` و`:172-175` و`:322-325` | **متابعة لوصف الـPR.** **اسم الاختبار يَعِد بأكثر ممّا تغطّيه التنقية.** الاسم `…never_reach_the_terminal`، لكنّ التنقية في سطرَي الإصدار وحدهما (`:206` و`:269`)، كما طلب SR‑17. **شغّلتُ بمزيّفات الاختبار:** رسالة `docker info` الفاشلة وفيها ESC تعطي الرمز 11، ويصل ESC إلى الطرفيّة (`:192`). ورسالة Docker في `--smoke` الفاشل تعطي 14، ويصل (`:343`). واسم الكرت، من `nvidia-smi` أو من حاوية `--smoke`، يصل في سطر OK (`:172` و`:322`). وهذه المسارات أقدم من الفرق، وبلا أثرٍ أمنيّ (SR‑17). **والإفساد R3** (سطر `docker OK` يعود إلى `$engine`) لا يمسكه اختبار. | التنقية مرّةً واحدة في `row()`: ‏`printf '…' "$1" "$2" "${3//[^[:print:]]/?}"`، ثمّ حذف الموضعين المحلّيّين، وحالتان في الاختبار بـ`FAKE_INFO_ERR` و`FAKE_RUN_ERR`. أو تسمية الاختبار بحدوده: `…_in_the_version_rows`. |
| 27 | تحسين | الالتزامان `c576193` و`16498e7` | **متابعة لوصف الـPR.** **جسم `c576193` ينسب إليه تعديل ث‑7، والتعديل في `16498e7`.** نصّه: «ث‑7 and the 4.2 row name the new 7.3 check». لكنّ `c576193` لا يمسّ `docs/deploy-server.md`، وجملة SR‑15 في ث‑7 (`deploy-server.md:102`) دخلت مع `16498e7`، وجسم هذا لا يذكرها. وفي الرسالتين المعرّف العربيّ `ث‑7`، ولا رسالة في `master` فيها `ث‑` (CLAUDE.md: رسائل الالتزامات بالإنجليزيّة). | من صنف #24: بلا إعادة كتابة للتاريخ، ويُذكر في وصف الـPR. |

### الشيفرة الجديدة

#### قاعدة `keep` في `scrape-gpu.yml`
- **لا تُسقط شيئاً يحتاجه المكدّس:**
  - التنبيهان يقرآن `nvidia_smi_memory_used_bytes` و`nvidia_smi_memory_total_bytes` و`nvidia_smi_temperature_gpu` (`alerts.yml:1187-1191` و`:1215-1216`).
  - وصفّ الكرت في `aizzak-capacity.json` يقرأ هذه الثلاثة، ومعها `nvidia_smi_utilization_gpu_ratio` و`nvidia_smi_gpu_info`.
  - والأسماء الخمسة كلّها تطابق التعبير.
  - و`AizzakScrapeTargetDown` يقرأ `up{tier!="optional"}` (`alerts.yml:304`). و`up` و`scrape_*` يضيفهما Prometheus بعد إعادة الوسم، فلا تمسّهما القاعدة.
- **ولا تُسقط شيئاً يبثّه المصدّر:** طابقتُ بالتعبير ناتج 1.15.1 الحقيقيّ الذي قستُه في الجولة 2 (151 عيّنة، 144 اسماً)، فلم يسقط اسم. فعلى كرتٍ مثله يساوي `scrape_samples_post_metric_relabeling` ‏`scrape_samples_scraped`، وفحص 7.3 (`server-gpu-plan.md:134`) يثبت ذلك على الخادم.
- **التعبير مثبَّت الطرفين:** promtool ‏v3.13.2 يلفّه بـ`^(?s:…)$`، فـ`fullmatch` في الاختبار يطابق سلوكه. وأفسدتُ نسختين في scratch، فرفضهما `promtool check config` برمز 1: `action: kep` ← «unknown relabel action»، وقوسٌ غير مغلق ← «error parsing regexp». فالمفاتيح تُقرأ فعلاً.
- **ما تُبقيه القاعدة لا يقرؤه أحد:** لا قاعدة في `alerts.yml` ولا لوحة تقرأ `go_*` أو `process_*` أو `promhttp_*`. فهذه العائلات لا تُسكت تنبيهاً اليوم، والحارس المقترح في #25 يحرس ذلك غداً.

#### التنقية في `deploy/gpu-preflight.sh`
- **لا تكسر `set -euo pipefail`:** التنقية توسيعُ معاملٍ في `local clean=…` (`:206`) وفي إسنادٍ (`:269`)، وكلاهما يعيد 0. و`engine` و`tail` معرَّفان دائماً قبلها، فلا خطر من `set -u`. ولا أنبوب جديد، ولا `BASH_REMATCH` يُقرأ بعدها.
- **لا تغيّر رمز خروج:** التنقية تأتي بعد قرار الفحص، وتمسّ النصّ المطبوع وحده. شغّلتُ 13 حالة بمزيّفات الاختبار، ورموزها كما قبل:
  - Engine قديمٌ مع ESC أو حرفٍ غير ASCII أو tab ← 11.
  - و`ctk` قديمٌ مع حرفٍ غير ASCII ← 12.
  - و`--smoke` الفاشل ← 14، والسليم ← 0 بـ`--smoke` ودونه.
- **أسطر OK سليمة:**
  - `docker OK` يطبع الآن `$shown` المطبَّع: `server 29.6.1 (>= 26.0.0)`. و`26.1.3+azure-1` يُطبع `26.1.3`، و`v26.0.0` يُطبع `26.0.0`.
  - وEngine سليمٌ يليه ESC يطبع `server 29.6.1` بلا ESC.
  - وسطر `ctk OK` لم يتغيّر، ولا يطبع اللاحقة أصلاً: `1.20.1` يليه ESC ← `1.20.1 (>= 1.17.8)`.
- **الاستبدال:** في بيئة الاختبار (locale ‏C) يصير كلّ بايتٍ غير قابلٍ للطباعة `?`: ‏`25.0.4-ü` ← `25.0.4-??`، وtab ← `?`. وفي locale ‏`UTF-8` يبقى الحرف القابل للطباعة كما هو.
- **على هذا الجهاز** (بلا `--smoke`): `docker OK server 29.6.1 (>= 26.0.0)`، و`ctk OK 1.20.1 (>= 1.17.8)`، والرمز 0.
- **ما بقي خامّاً:** أربعة مسارات أقدم من الفرق (#26).

#### الاختبارات الجديدة: الإفساد
نسخة الشجرة من `git archive 16498e7` في scratch، وخضراء أوّلاً (116 passed في الوحدتين). ثمّ طبّقتُ كلّ إفسادٍ وحده، وأعدتُ الملفّ بعده:

| الإفساد | النتيجة |
|---|---|
| R1: حذف التنقية في `ctk` | يمسك (`ctk-suffix`) |
| R2: سطر `docker FAIL` يطبع `$engine` | يمسك (`engine` و`engine-rc`) |
| R3: سطر `docker OK` يطبع `$engine` | **لا يمسك** (#26) |
| R4: كلّ Engine تجريبيّ يُرفض (M6 في الجولة 2) | يمسك (`later-rc`) |
| R5 وR6: `sample_limit` ‏500 ثمّ 1767 | يمسك في الاثنين |
| R7: `sample_limit` ‏1768 | لا يمسك، وهذا مقصود: 1768 هو الحدّ الأدنى نفسه |
| R8: حذف `metric_relabel_configs` | يمسك |
| R9: التعبير `.*` | يمسك |
| R10: `action: drop` | يمسك |
| R11: تعبيرٌ يُسقط الحرارة والاستعمال | **لا يمسك**، ولا في الوحدات الـ23 (#25) |
| R12 وR13: التعبير يُدخل `aizzak_http_.*` ثمّ `aizzak_dlq_.*` | **لا يمسك**، وR12 لا في الوحدات الـ23 أيضاً (#25) |

### الوثائق
- **`deploy-server.md`:**
  - لا صفّ يبدأ برقم إلّا الأسطر السبعة في جدول الرموز (`:64-70`).
  - وجدول الأمان كتلةٌ واحدة. عرضتُه بـ`markdown-it` (commonmark مع الجداول)، فخرجت ث‑1…ث‑9 تسعةَ صفوفٍ في جدولٍ واحد، ولا فقرة تبدأ بـ`| ث`. والحارس `test_every_security_row_sits_in_one_contiguous_table` أخضر.
  - وث‑7 (`:102`) فيه: قرار المالك على `--web.enable-lifecycle`، وأثر `/-/quit` (SR‑16)، وعلاجان للموجة 8، وواجهة الاستعلام مفصولةٌ عن الخيار، وقاعدة `keep` مع فحص 7.3 (SR‑15).
- **`<div dir="rtl">`:** يلفّ `deploy-server.md` و`server-gpu-plan.md` و`design.md`، من أوّل سطرٍ إلى آخره.
- **المعرّفات:** كلّ ما أُضيف خارج الشيفرة بالشرطة U+2011: SR‑9 وSR‑10 وSR‑15 وSR‑16 وث‑7 وCVE‑2024‑29018 والتاريخ. ولا معرّف بشرطة ASCII.
- **اللوحة:** صفّ 4.2 (`server-gpu-plan.md:134`) يضيف فحص 7.3، وحاله لم تتغيّر (🟡)، فلا يتغيّر عدّ `server-gpu-summary.html`.

### رسائل الالتزامات
- الثلاثة: عنوانٌ، ثمّ سطرٌ فارغ، ثمّ جسمٌ، ثمّ سطرٌ فارغ، ثمّ التذييلان. و`git log --format='%(trailers:only,unfold)'` يقرأ `Co-Authored-By` و`Claude-Session` في كلٍّ منها.
- والنسبة في جسم `c576193` في #27.

### ما شغّلتُه (HEAD `16498e7`)
| الفحص | النتيجة |
|---|---|
| `ruff format --check .` و`ruff check .` | 821 ملفّاً منسَّقة، وAll checks passed |
| `lint-imports` | 8 kept، و0 broken |
| `mypy src` | لم يُشغَّل: `src/` لم يتغيّر في `fb4ccfd..16498e7` |
| 23 وحدة تقرأ ملفّاً متغيّراً، و`tests/architecture`، بلا `COMPOSE_FILE` ولا `COMPOSE_PROFILES` | `851 passed`، ‏rc 0 |
| الوحدات نفسها بـ`COMPOSE_FILE` الثلاثيّ كما في CI | `851 passed`، ‏rc 0 |
| `deploy/prometheus/test-rules.sh` | `test-rules: OK`، و29 قاعدة، و`check config` ناجحٌ بالوضعين، ‏rc 0 |
| `promtool check config` على نسختين مفسدتين من `scrape-gpu.yml` | يرفض الاثنتين، ‏rc 1 |
| تعبير `keep` على ناتج المصدّر 1.15.1 المقيس (144 اسماً)، وعلى أسماء التنبيهين واللوحة | لا اسم يسقط |
| 13 إفساداً على نسخة scratch | 8 تمسك، وR7 مقصود، و4 لا تمسك (R3، R11، R12، R13) |
| R11 وR12 على الوحدات الـ23 كلّها | `851 passed` في الاثنين، فلا وحدة أخرى تمسكهما |
| 13 حالةً إضافيّة بمزيّفات `test_gpu_preflight` | الرموز كما قبل، والتنقية كما وُصف |
| `deploy/gpu-preflight.sh` الحقيقيّ على هذا الجهاز، بلا `--smoke` | rc 0 |

- لم أشغّل `tests/integration`، ولم أقرأ `.env`.
- لم أشغّل حاويةً غير حاويات promtool (في `test-rules.sh` ونسختَي الإفساد)، وكلّها بـ`--rm`. وقائمة الحاويات بعدها مطابقةٌ لما قبلها.
- نسخة الشجرة وملفّات الحالات والإفساد في scratch، خارج المستودع، وحُذفت نسخة الشجرة.

</div>
