<div dir="rtl">

# قبول مالك المنتج: حدودٌ تُحسب تلقائيّاً من موارد الجهاز (`compose-auto-size`)

- **النطاق:** الفرع `feat/compose-auto-size`، الرأس `1d53e86` (فوق `master` ‏`447923f`). ثمانية التزامات، من `a15672b` إلى `1d53e86`. الاختبارات في `tests/unit/test_auto_size.py` (يُختصر في الجدول بـ`::`) و`tests/unit/test_auto_size_edges.py` (يُختصر بـ`edges::`).
- **المراجع:** product-owner · 2026‑10‑16. قرارات البوّابة ① في `status.md` («قرارات وتصعيدات») ملزمة.
- **ما تحقّقتُ منه بنفسي** (للقراءة فقط، دون `.env` الحقيقيّ ولا حاوية ولا `tests/integration/`):
  - كلّ اسم اختبارٍ في الجدول وجدتُه بـ`grep "def …"` في ملفّه.
  - `pytest -rs tests/unit/test_auto_size.py tests/unit/test_auto_size_edges.py`: ‏`172 passed in 10.05s`، والرمز 0.
  - `pytest -rs tests/unit/test_compose_sizes.py tests/unit/test_postgres_tuning.py tests/unit/test_resource_budget.py tests/unit/test_repo_hygiene.py`: ‏`98 passed in 6.07s`، والرمز 0.
  - البوّابات 1–4 على `1d53e86`. شجرة العمل لا تختلف عن الرأس إلّا بثلاثة ملفّات وثائق غير متتبَّعة:
    ```
    ruff format --check .  -> 820 files already formatted                  EXIT=0
    ruff check .           -> All checks passed!                           EXIT=0
    mypy src               -> Success: no issues found in 467 source files EXIT=0
    lint-imports           -> Contracts: 8 kept, 0 broken.                 EXIT=0
    ```
  - البوّابة 5 كاملةً (`5758 passed, 0 skipped`، الرمز 0) والبوّابة 6 (`test-rules.sh` OK، الرمز 0) من `test-plan.md` الجولة 2. لم أُعِدهما.
  - دليل AC‑4.3 (`git diff`)، والمراجعة اليدويّة لـAC‑6.2، وإعادةٌ حرفيّة لـAC‑3.3. والثلاثة أدناه.

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑1.1 | `::test_the_block_carries_exactly_the_keys_of_large`: مفاتيح `large.env` الـ85 بترتيبها | ✅ مقبول |
| AC‑1.2 | `::test_every_host_fits_and_stays_between_small_and_large`: ستّة خوادم في وضعين. المجموع من Compose نفسه ≤ 90%، وكلّ قيمةٍ بين `small` و`large` | ✅ مقبول |
| AC‑1.3 | `::test_the_reference_host_reproduces_large_exactly` في الوضعين. لا يختلف عن `large` إلّا `POSTGRES_CONFIG_FILE` | ✅ مقبول |
| AC‑1.4 | `::test_no_value_shrinks_as_the_host_grows`: الذاكرة من 16 إلى 128 بخطوة 2، ثمّ المعالجات من 8 إلى 64، في الوضعين | ✅ مقبول |
| AC‑1.5 | • `::test_replicas_follow_the_cpus_up_to_three`: ‏1/2/3/3/3<br>• `::test_the_gpu_embedding_row_is_one_replica_whatever_the_key_says`<br>• `::test_a_many_core_small_memory_host_still_fits`: ‏64/24، ≤ 90% | ✅ مقبول |
| AC‑1.6 | • `::test_a_host_below_small_is_refused_naming_the_shortfall`: الرمز 1، وفي الرسالة `gpu` و14.22 و13.50 و0.72، وsha256 كما هو، ولا `auto.conf`<br>• `edges::test_the_refusal_threshold_is_exact_in_gpu_mode` | ✅ مقبول |
| AC‑1.7 | `::test_cpu_oversubscription_is_reported_not_refused`: ‏4/32، الرمز 0، و`OVERSUBSCRIBED` | ✅ مقبول |
| AC‑1.8 | • `::test_nothing_runs_the_generator_on_its_own`<br>• وتحقّقتُ بـ`git grep -l auto_size -- ':!docs'`: لا يذكره إلّا `.dockerignore` و`.env.example` و`.gitignore`، والمولِّد، و`resource-budget.sh`، والاختباران. ولا يستدعيه أيّ سكربتٍ آخر في `deploy/**/*.sh`، فهذا يسدّ فجوة review #9 اليوم | ✅ مقبول |
| AC‑2.1 | `::test_auto_conf_has_the_size_shape_and_fits`: اثنتا عشرة حالة خادم، ومعها مدخلات AC‑5.1. سطر `include` أوّلاً، ثمّ المفاتيح الأربعة وحدها، و`effective_cache_size` = ⌊37.5%⌋ | ✅ مقبول |
| AC‑2.2 | الاختبار نفسه:<br>• `sb + 4×mwm < POSTGRES_MEM`<br>• `mwm ≤ 1GB`<br>• `ecs` أقلّ من ذاكرة الجهاز<br>• رفع `shared_buffers` إلى 64GB يُفشل الفحص، والرسالة تذكر `auto` | ✅ مقبول |
| AC‑2.3 | `::test_the_reference_host_writes_large_conf_values` | ✅ مقبول |
| AC‑2.4 | `::test_redis_keeps_its_container_headroom`: الدوران، والوضعان، وسبعة أجهزة | ✅ مقبول |
| AC‑2.5 | • `::test_a_run_points_postgres_at_the_written_auto_conf`<br>• `test_postgres_tuning.py::test_the_tuning_never_appears_as_a_command_line_argument`: أخضر في تشغيلي | ✅ مقبول |
| AC‑2.6 | ‏[خادم]: مؤجَّلٌ إلى 7.3. النصّ في `stories.md` نفسه («لا يُثبَت هنا»)، و`requirements.md` §2 يضعه خارج النطاق، ووافق المالك على ذلك في البوّابة ① | ⏭ مؤجَّل، مقبول |
| AC‑3.1 | • `::test_only_the_block_changes_mode_survives_and_nothing_leaks`: عمليّةٌ فرعيّة حقيقيّة، وصلاحيّة 0600، وقيمةٌ حارسة<br>• `edges::test_a_guarded_value_never_reaches_any_output`<br>• `edges::test_a_malformed_block_error_does_not_echo_the_file` | ✅ مقبول |
| AC‑3.2 | `::test_a_second_run_changes_nothing`: ‏sha256 | ✅ مقبول |
| AC‑3.3 | • `::test_a_rerun_on_a_bigger_host_replaces_only_the_block` (64 ج.ب ثمّ 128).<br>• وأعدتُه بأرقام المعيار نفسها (16 معالجاً، 32 ج.ب ثمّ 64) على نسخةٍ من `.env.example` في scratchpad. بعد التشغيلين بقي كلّ ما خارج الكتلة كالأصل بايتاً ببايت، وتغيّرت الكتلة، وبقيت الصلاحيّة 600، ولم تظهر القيمة الحارسة | ✅ مقبول |
| AC‑3.4 | • `::test_a_conflicting_key_outside_the_block_is_refused_by_name`: ‏`pgdata-conf` و`qdrant-mem` و`template-after-block`<br>• `::test_every_spelling_of_a_conflicting_key_is_refused`: خمس صيغ، منها `KEY: value`، ونظيره في `edges::`<br>• في كلّها: الرمز 3، والاسم مذكور، والقيمة غائبة، وsha256 كما هو | ✅ مقبول |
| AC‑3.5 | `::test_the_template_postgres_config_file_is_overridden_by_the_block` | ✅ مقبول |
| AC‑3.6 | `::test_compose_resolves_the_block` ‏[config]. ‏Docker متاح فلم يُتخطَّ: ‏0 skipped عند QA وفي تشغيلي | ✅ مقبول |
| AC‑3.7 | `::test_dry_run_writes_nothing` | ✅ مقبول |
| AC‑4.1 | `::test_the_budget_script_agrees_with_the_generator`: ثلاثة أجهزة، الرمز 0، وسطر المجموع نفسه، والقيمة الحارسة غائبة | ✅ مقبول |
| AC‑4.2 | `::test_the_budget_script_refuses_an_edited_block`: الرمز 1 و`OVER`. ونصّ الرسالة (`deploy/resource-budget.sh:335-337`) يذكر مقدار التجاوز («… GB over the … GB allowed») | ✅ مقبول |
| AC‑4.3 | • `git diff --exit-code master -- …`: لا فرق، والرمز 0 (المخرج أدناه)<br>• `test_compose_sizes.py::test_the_size_directory_holds_exactly_the_three_sizes`: أخضر<br>• `::test_without_a_block_the_budget_is_the_reference_ledger`: ‏35.60 vCPU و57.28 GB | ✅ مقبول |
| AC‑5.1 | `::test_dev_role_on_the_development_host_writes_a_dev_block`: ‏10/12.67، الرمز 0، `# role: dev` | ✅ مقبول |
| AC‑5.2 | `::test_server_role_refuses_the_development_host`: الرمز 1، وفي الرسالة 11.72 و11.40 و0.32 | ✅ مقبول |
| AC‑5.3 | `::test_dev_floors_hold_and_never_pass_large`: الأرضيّات الستّ ومعها `PROMETHEUS_MEM`. لا قيمة فوق `large`، والمصدر `monitoring-host-postgres/design.md` مذكورٌ في نصّ السكربت | ✅ مقبول |
| AC‑5.4 | `::test_dev_overcommit_is_a_warning_with_the_ratio`: الرمز 0، و23.22 و12.67 و`1.83x` و`Warning only`. رقم 23.22 GB و1.83× تطبيقٌ لقاعدة Q‑1 (design ق‑3: أرضيّة Prometheus)، **وليس تغييراً في النطاق**. و`stories.md:202` محدَّث | ✅ مقبول |
| AC‑5.5 | `::test_dev_host_runs_one_replica_of_everything` | ✅ مقبول |
| AC‑5.6 | • `::test_the_role_is_remembered_from_the_block`<br>• `edges::test_no_block_and_no_role_means_server` | ✅ مقبول |
| AC‑5.7 | ‏[مالك، يدويّ]: يجريه المالك بعد الدمج على `.env` الحقيقيّ (design §6، الخطوة 6). لا يُؤتمت، لأنّ الوكلاء لا يقرؤون `.env`، والمكدّس الحيّ مستعمَل | ⏭ مؤجَّل، مقبول (الشرط 1) |
| AC‑6.1 | • `::test_the_runbook_and_env_example_document_the_block`، و`test_repo_hygiene.py`<br>• قرأتُ `08 §2‑ز`: فيه الأمر، والدوران وقاعدة كلٍّ منهما، و«الرجوع: احذف الكتلة بعلامتيها»<br>• `.env.example`: سطرا تعليقٍ فقط | ✅ مقبول |
| AC‑6.2 | مراجعتي اليدويّة (القسم التالي) | ✅ مقبول |
| AC‑6.3 | • `test-plan.md`، الجولة 2: البوّابات الستّ خضراء، و`5758 passed, 0 skipped` بالرمز 0<br>• أعدتُ البوّابات 1–4 على الرأس<br>• زمن الملفّين: 172 اختباراً في 10.05 ث، أي ≤ 15 ث | ✅ مقبول |

**العدّ:** 34 معياراً.
- **32 ✅:**
  - 30 باختبارٍ آليّ: 29 ‏[وحدة] وواحد ‏[config].
  - واحدٌ بمخرج البوّابات (AC‑6.3).
  - واحدٌ بمراجعتي اليدويّة (AC‑6.2).
- **2 ⏭ مؤجَّلان** بقرار البوّابة ①: ‏AC‑2.6 وAC‑5.7.
- **0 🔴.**

ومن معايير Must الـ31 (US‑1…US‑5): ‏29 ✅ و2 ⏭. ومعايير Should الثلاثة (US‑6) كلّها ✅.

## دليل AC‑4.3
```
$ git rev-parse --short HEAD; git rev-parse --short master
1d53e86
447923f
$ git diff --exit-code master -- deploy/sizes deploy/postgres/sizes docker-compose.yml docker-compose.gpu.yml docker-compose.test.yml
EXIT=0
```
- الملفّات المتتبَّعة في المجلّدين ستّة: `deploy/sizes/{small,medium,large}.env` و`deploy/postgres/sizes/{small,medium,large}.conf`.
- `git diff --stat master -- 'docker-compose*.yml'` فارغٌ أيضاً.

## AC‑6.2: المراجعة اليدويّة
- **`docs/server-gpu-plan.md` §4، صفّ 3.5:**
  - العنوان «حجم `auto`: حدودٌ تُحسب من الجهاز»، والأولويّة «يجب».
  - الموافقة «موافقة `deploy/`، استثناء القاعدة 1».
  - يعتمد على 3.4، والحالة 🟡.
  - و5.2 صارت تعتمد على 3.5. هذا يطابق Q‑6.
- **§3، القاعدة 1:** «استثناءٌ ضيّق ثانٍ (3.5، قرار المالك 2026‑10‑16)». ويقول:
  - قيم المستودع لا تتغيّر.
  - الجهاز لا يتغيّر إلّا حين يشغّل المالك الأمر بنفسه.
  - لا يستدعيه `entrypoint` ولا `healthcheck` ولا `rolling-deploy.sh`.
  - الرجوع بحذف الكتلة.

  هذا هو نصّ Q‑5 كما وافق عليه المالك.
- **§5، تفصيل 3.5 و5.2:** يطابقان قرارات البوّابة ①:
  - الدوران، و`server` هو الافتراضيّ (Q‑2).
  - أرضيّة × 1.25، والتجاوز تحذيرٌ لا رفض (Q‑1، الخيار ج).
  - أمرٌ صريح فقط (Q‑4).
  - الاستيفاء الخطّيّ (Q‑8).
  - لا تعديل في `docker-compose*.yml` (Q‑7).
  - `auto` افتراضيٌّ في `install.sh` (Q‑9).
- **فصل الخطّة عن الحالة:**
  - الحالة في موضعين فقط: عمود اللوحة في §4، وسطر «الحالة» في رأس الوثيقة (🟡 على الفرع).
  - تعديلات الأقسام الثابتة (§3 و§5) كلّها مسنودةٌ بقرار البوّابة ①، كما تشترط الوثيقة نفسها («لا تتغيّر إلّا بقرارٍ أو بدليلٍ جديد»).
  - سطر «القبول» في §5 يذكر ما يبقى خارج الخطوة (AC‑5.7 وAC‑2.6). هذا نطاق، لا حالة.
- **`docs/server-gpu-summary.html`:**
  - ‏29 `<article>`: ‏8 مكتملة و7 جارية و14 لم تبدأ.
  - تطابق الشريط (`flex` ‏8/7/14)، و`aria-label` («تسعٌ وعشرون… ثمانٍ… سبعٌ… أربع عشرة»)، والمفتاح، والعنوان «٢٩ خطوة».
  - المرحلة ٣ «٣ من ٥»: ثلاث مكتملة، و3.3 و3.5 جاريتان.
  - مجموع المراحل: 4+6+5+2+3+5+4 = 29.
  - لوحة الخطّة 29 صفّاً أيضاً: 8 ✅ و7 🟡 و14 ⬜. فتتطابق مع HTML.
  - بطاقة «يحجب: ٢٤ خطوة من ٢٩» بقيت على 24، وهذا صحيح: موافقة 3.5 أُعطيت في البوّابة ①.
- **الرابط والمعرّفات:**
  - الرابط `#35-حجم-auto-يحسب-الحدود-من-الجهاز` يطابق عنوان 3.5.
  - لا معرّف بشرطةٍ عاديّة (ASCII) في الأسطر المضافة.

## المراجعات
- **`review.md`: ‏PASS بعد الجولة 2.**
  - لا ملاحظة حاجبة مفتوحة.
  - مفتوحٌ غير حاجب: #20 (مهمة) و#21 (تحسين).
  - مؤجَّلٌ بقرارٍ منذ الجولة 1: #9 و#10، والجزء الاختياريّ من #14.
- **`security-review.md`: ‏PASS.**
  - لا ملاحظة حرجة ولا عالية ولا متوسّطة.
  - أُصلحت في الجولة 1: ‏SR‑1…SR‑4 وSR‑7 وSR‑8، وتحقّق منها مراجع الشيفرة (`review.md`، جدول «بنود المراجعة الأمنيّة»). وأُغلقت SR‑9 وSR‑10 مع BUG‑1 وBUG‑3.
  - بقيت: ‏SR‑5 (معلومة)، وSR‑6 (مؤجَّلة إلى 5.2)، وSR‑11 (قرارٌ بشريّ).
  - أُضيفت ث‑10…ث‑12 إلى `docs/deploy-runpod.md`.

## ملاحظات غير حاجبة مقبولة
أقبل كلّ بندٍ هنا باسمه للدمج. وما يحتاج عملاً لاحقاً له شرطٌ في القسم التالي.

- **review #20 (مهمة): فشل تجهيز `auto.conf` يُنسب في الرسالة إلى `.env`.** أعدتُه على نسخة scratchpad: ‏`chmod 555` على `deploy/postgres/sizes`.
  - **النتيجة:** الرمز 3، والرسالة `cannot write <tmp>/.env (Permission denied). Nothing changed`.
  - **لا ضرر:** sha256 ‏`.env` و`auto.conf` لم يتغيّر، ولا بقايا `.auto-size-tmp-*`.
  - **لماذا أقبله:** العيب أنّ الرسالة تسمّي الملفّ الخطأ فقط. وجملة «Nothing changed» صحيحة، والرمز صحيح.
  - **المتابعة:** الشرط 3.
- **review #21 (تحسين): سطران متقادمان في `design.md`.**
  - في §5: الحارس الآليّ لا يذكر `st_ino`.
  - في §3‑ج: إعادة المجموعة موصوفةٌ لـroot وحده.
  - الأثر على الوثائق وحدها. المتابعة: الشرط 4.
- **review #9 و#10 (تحسين، مؤجَّلان بقرار):**
  - **#9:** سدّه اليوم تحقّقي بـ`git grep` في AC‑1.8.
  - **#10:** مراجع الشيفرة فحص يدويّاً أنّ قيم `auto.conf` لا تنقص من 16 إلى 128 ج.ب في الوضعين. وAC‑1.4 لا يشترط قيم `auto.conf`.
- **الجزء الاختياريّ من review #14:** مقارنة رأس `auto.conf` بالكتلة في `--block`. تُرك بقرار. والجزء المطلوب والمستحسن من #14 أُصلح (التراجع الآليّ).
- **SR‑5 (معلومة، TOCTOU):** المشغّل يشغّل الأمر بنفسه، ونافذة السباق نحو ثانية. فأقبله دون عمل.
- **SR‑6 (منخفضة، مستقبليّة):** يُسجّلها ث‑11، وتؤجَّل إلى 5.2. لا يشغّل المولِّدَ أحدٌ بصلاحيّة root اليوم، فلا يستدعيه شيءٌ تلقائيّاً (AC‑1.8). المتابعة: الشرط 3.
- **SR‑11 (منخفضة، سابقة للميزة):**
  - **الثغرة:** ‏`.env` ونسخه بصلاحيّة 0644 على جهاز التطوير، وتُسجّلها ث‑10.
  - **المطلوب:** قرارٌ بشريّ. الميزة لم تُنشئ الثغرة، فالمولِّد يحفظ الصلاحيّة كما هي.
  - **توصيتي للمالك:** ‏`chmod 600` قبل AC‑5.7 (الشرط 1). ولا يفعلها وكيل.
- **المراجعة الأمنيّة لم تُعَد بعد الجولتين 1 و2.** أقبل ذلك لثلاثة أسباب:
  - التعديلات بعدها إصلاحاتٌ لملاحظاتها هي، ولملاحظات المراجعة.
  - مراجع الشيفرة فحص مسار الكتابة الجديد أمنيّاً في الجولتين. لم تظهر القيمة الحارسة في أيّ مخرج، والمؤقّتات بـ0600، ولا بقايا.
  - لا سطح هجومٍ جديد: لا مسار HTTP، ولا صلاحيّات، ولا مستأجرين.
- **BUG‑1…BUG‑3:** مُغلقة ومُتحقَّقٌ منها (`test-plan.md`، الجولة 1).

## شروطٌ ومتابعات
1. **AC‑5.7، يجريه المالك بعد الدمج.**
   - **التسلسل:**
     1. `deploy/auto_size.py --role dev --dry-run`.
     2. الأمر نفسه دون `--dry-run`.
     3. `docker compose up -d`، ثمّ `docker compose restart postgres`.
     4. نصف ساعةٍ من الاستعمال: فهرسة ملفٍّ، ومحادثة.
     5. `OOMKilled=false` لكلّ الحاويات، و`/health` يعطي 200.
   - **قبل ذلك:** يُستحسن `chmod 600` على `.env` ونسخه (ث‑10).
   - **حالة 3.5:** تبقى 🟡 في اللوحة حتّى ينجح AC‑5.7.
   - **إن قُتلت خدمة:**
     - يرجع المالك فوراً بحذف الكتلة.
     - ويعود العمل إلى **software-architect** ليعدّل جدول الأرضيّة `DEV_PEAKS_MIB`. هذا تعديل بياناتٍ فقط (design §7).
     - ثمّ إلى backend-developer.
2. **AC‑2.6:** في الخطوة 7.3 على خادمٍ حقيقيّ، مع AC‑3.7 من `compose-sizes`.
3. **قبل أن يستدعي `install.sh` المولِّد (الخطوة 5.2)، أو في إصلاحٍ صغيرٍ قبلها:**
   - **review #20:** التقاط `OSError` حول كلّ `stage()` باسم الملفّ الحقيقيّ، مع الاختبار الموصوف في `review.md`.
   - **ث‑11 / SR‑6:**
     - رفض الروابط والمسارات غير المملوكة لـroot حين يكون `euid == 0`.
     - `python3 -I`.
   - **تحذيرٌ يذكر الاسم وحده** حين يكون `.env` مقروءاً لغير مالكه (SR‑11).
   - **المسار:** backend-developer، ثمّ qa-engineer، ثمّ code-reviewer.
4. **review #21:** تصحيح سطرَي `design.md`. هذا تعديل وثائق فقط، ويجوز أن يكون في هذا الـPR قبل الدمج. مستحسنٌ، وليس شرطاً.
5. **قبل فتح الـPR:**
   - التزام ملفّات التسليم غير المتتبَّعة: ‏`review.md` و`security-review.md` و`status.md`، وهذا الملفّ.
   - تحديث صفّ 11 في `status.md` (تعريف «تم» §5، البند 5).
   - لصق مخرج AC‑4.3 أعلاه في وصف الـPR، ومعه زمن NFR‑4. قستُه: ‏`--dry-run` على 16/32 في 0.23 ث، والحدّ ≤ 5 ث.

## الحكم النهائي
**مقبول** للبوّابة ② (الدمج).

- **الأدلّة:** ‏32 من 34 معياراً ✅ بدليل، ولا معيار 🔴. البوّابات الستّ خضراء، والمراجعتان PASS، ولا ملاحظة حاجبة مفتوحة.
- **المعياران المؤجَّلان (AC‑2.6 وAC‑5.7) من نوع Must، وليسا ✅.** قبلتُهما مؤجَّلَين، وهذا استثناءٌ صريح من قاعدة «كلّ Must ✅»، لثلاثة أسباب:
  - نصّهما في `stories.md`، الذي وافق عليه المالك في البوّابة ①، يضع إثباتهما خارج هذا التسليم.
  - لا مرحلة تستطيع أن تنتج دليلهما الآن: لا خادم قبل 7.3، والوكلاء لا يقرؤون `.env` ولا يشغّلون المكدّس الحيّ.
  - الدمج وحده لا يغيّر أيّ جهاز: المولِّد لا يعمل إلّا بأمرٍ صريح (FR‑13، AC‑1.8)، والرجوع بحذف الكتلة.
- **حدّ القبول:** الميزة لا تُعدّ منتهيةً على جهاز التطوير حتّى ينجح AC‑5.7 (الشرط 1)، ولا على الخادم حتّى ينجح AC‑2.6 في 7.3.

</div>
