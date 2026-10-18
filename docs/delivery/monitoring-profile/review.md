<div dir="rtl">

# مراجعة الشيفرة: المراقبة اختياريّة وأرقام الكرت (`monitoring-profile`)

- **النطاق المراجَع:** `git diff master...feat/monitoring-profile` في `/home/AIZZAK`: تسعة التزامات من `f2158a0` إلى `3880ff9`، و35 ملفّاً. قرأتُ كلّ ملفٍّ متغيّر كاملاً، لا مقاطع الفرق وحدها.
- **الحكم:** **PASS**. لا ملاحظة حاجبة. فيه 4 ملاحظات مهمّة و7 تحسينات.
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

</div>
