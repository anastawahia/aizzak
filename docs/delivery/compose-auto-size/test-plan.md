<div dir="rtl">

# خطة الاختبار: حدودٌ تُحسب تلقائيّاً (`compose-auto-size`)

> الفرع `feat/compose-auto-size`، الالتزامات `a15672b` و`0521e1b`. المراجع: QA · 2026‑10‑16.

## 1. مصفوفة التغطية

ملفّ الاختبار الأساسيّ `tests/unit/test_auto_size.py` (يُختصر في الجدول بـ`::`). 34 معياراً: 31 ✅ مؤتمتة أو جزئيّاً، و2 مؤجَّلان بقرار (AC‑2.6 إلى 7.3، AC‑5.7 للمالك)، ولا معيار 🔴.

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑1.1 | وحدة | test_auto_size.py::test_the_block_carries_exactly_the_keys_of_large | ✅ |
| AC‑1.2 | وحدة | ::test_every_host_fits_and_stays_between_small_and_large (6 خوادم × وضعان؛ المجموع من Compose نفسه) | ✅ |
| AC‑1.3 | وحدة | ::test_the_reference_host_reproduces_large_exactly (وضعان) | ✅ |
| AC‑1.4 | وحدة | ::test_no_value_shrinks_as_the_host_grows (ذاكرة 16–128 ومعالجات 8–64) | ✅ |
| AC‑1.5 | وحدة | ::test_replicas_follow_the_cpus_up_to_three، ::test_the_gpu_embedding_row_is_one_replica…، ::test_a_many_core_small_memory_host_still_fits | ✅ |
| AC‑1.6 | وحدة | ::test_a_host_below_small_is_refused_naming_the_shortfall + (جديد) test_auto_size_edges.py::test_the_refusal_threshold_is_exact_in_gpu_mode (15.79 يُرفض، 15.8 ينجح) | ✅ |
| AC‑1.7 | وحدة | ::test_cpu_oversubscription_is_reported_not_refused | ✅ |
| AC‑1.8 | وحدة | ::test_nothing_runs_the_generator_on_its_own | ✅ |
| AC‑2.1 | وحدة | ::test_auto_conf_has_the_size_shape_and_fits | ✅ |
| AC‑2.2 | وحدة | ::test_auto_conf_has_the_size_shape_and_fits (يتضمن حالة 64GB الفاشلة) | ✅ |
| AC‑2.3 | وحدة | ::test_the_reference_host_writes_large_conf_values | ✅ |
| AC‑2.4 | وحدة | ::test_redis_keeps_its_container_headroom | ✅ |
| AC‑2.5 | وحدة | ::test_a_run_points_postgres_at_the_written_auto_conf + test_postgres_tuning (أخضر) | ✅ |
| AC‑2.6 | خادم | مؤجَّل إلى 7.3 بقرار البوّابة ① | ⏭ مؤجَّل |
| AC‑3.1 | وحدة | ::test_only_the_block_changes_mode_survives_and_nothing_leaks + (جديد) test_a_guarded_value_never_reaches_any_output و…malformed_block_error_does_not_echo_the_file | ✅ |
| AC‑3.2 | وحدة | ::test_a_second_run_changes_nothing | ✅ |
| AC‑3.3 | وحدة | ::test_a_rerun_on_a_bigger_host_replaces_only_the_block | ✅ |
| AC‑3.4 | وحدة | ::test_a_conflicting_key_outside_the_block_is_refused_by_name + (جديد) test_every_spelling_of_a_conflicting_key_is_refused (export/مسافات/اقتباس/تعليق لاحق) | ✅ |
| AC‑3.5 | وحدة | ::test_the_template_postgres_config_file_is_overridden_by_the_block | ✅ |
| AC‑3.6 | config | ::test_compose_resolves_the_block (Docker متاح، لم يُتخطَّ) | ✅ |
| AC‑3.7 | وحدة | ::test_dry_run_writes_nothing | ✅ |
| AC‑4.1 | وحدة | ::test_the_budget_script_agrees_with_the_generator | ✅ |
| AC‑4.2 | وحدة | ::test_the_budget_script_refuses_an_edited_block | ✅ |
| AC‑4.3 | وحدة+يدوي | ::test_without_a_block_the_budget_is_the_reference_ledger + مقارنة يدويّة (حُذف اختبار test_the_named_size_files_match_master؛ الدليل الآن مخرج `git diff --exit-code` في وصف الـPR، مع مقارنة لمخرج resource-budget.sh مع نسخة master §3) | ✅ |
| AC‑5.1 | وحدة | ::test_dev_role_on_the_development_host_writes_a_dev_block | ✅ |
| AC‑5.2 | وحدة | ::test_server_role_refuses_the_development_host | ✅ |
| AC‑5.3 | وحدة | ::test_dev_floors_hold_and_never_pass_large | ✅ |
| AC‑5.4 | وحدة | ::test_dev_overcommit_is_a_warning_with_the_ratio (يؤكّد 23.22 و1.83x كما في design؛ نصّ القصّة 22.97/1.81 قديم، انظر ملاحظة §4) | ✅ |
| AC‑5.5 | وحدة | ::test_dev_host_runs_one_replica_of_everything | ✅ |
| AC‑5.6 | وحدة | ::test_the_role_is_remembered_from_the_block + (جديد) test_no_block_and_no_role_means_server | ✅ |
| AC‑5.7 | مالك | يدويّ بعد الدمج على `.env` الحقيقيّ | ⏭ مؤجَّل |
| AC‑6.1 | وحدة | ::test_the_runbook_and_env_example_document_the_block + test_repo_hygiene | ✅ |
| AC‑6.2 | مراجعة | فحص آليّ جزئيّ (§3): خطوة 3.5 موجودة، عدّادات HTML مطابقة، لا معرّفات بشرطة عاديّة؛ المراجعة النهائيّة للمالك | ✅ (جزئيّ آليّاً) |
| AC‑6.3 | بوّابات | §2: كلّ البوّابات خضراء؛ زمن test_auto_size.py نحو 7.7 ث | ✅ |

الاختبارات المضافة: `tests/unit/test_auto_size_edges.py` (16 حالة): عتبة الرفض الدقيقة، أشكال المفتاح المتعارض، عدم تسرّب قيمة حارسة في مسارات الإخراج كلّها، عدم تغيّر `.env` عند فشل كتابة `auto.conf`، بقاء الرابط الرمزيّ، `.env` فارغ، تطابق ملفّات الأحجام مع `master`.

## 2. نتائج بوابات المشروع
```
1. ruff format --check .      -> 819 files already formatted                 EXIT=0
2. ruff check .               -> All checks passed!                          EXIT=0
3. mypy src                   -> Success: no issues found in 467 source files EXIT=0
4. lint-imports               -> Contracts: 8 kept, 0 broken.                EXIT=0
5. PATH=.venv/bin:$PATH pytest -rs tests/unit tests/architecture tests/eval
                              -> 5714 passed in 163.12s (0:02:43)            EXIT=0
   (0 skipped: التخطّي الوحيد عند المطوّر، test_import_contracts، اختفى بوجود .venv/bin في PATH.
    5714 = 5698 + 16 اختباراً أضافها QA)
6. deploy/prometheus/test-rules.sh -> SUCCESS: 27 rules found / test-rules: OK  EXIT=0
```
AC‑6.3 الزمن: `test_auto_size.py` و`test_auto_size_edges.py` معاً 128 اختباراً في 8.18 ث (الأبطأ 0.53 ث)؛ الأول وحده نحو 7.7 ث ≤ 15 ث. الإجمالي 2m43s مقابل 2m40.8s عند المطوّر (+2 ث، ضمن تقلّب الجهاز ويشمل 16 اختباراً جديداً). `tests/integration/` لم يُشغَّل (قيد المكدّس الحيّ).

## 3. الاختبار الاستكشافي
كلّه على نسخ مؤقّتة في scratchpad مع `.env` فيه قيمة حارسة `SENTINEL_ZZ_99`؛ لم يُكتب `.env` ولا `auto.conf` الحقيقيّان.

| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| عتبة الرفض | 8 معالجات، 15 ج.ب، gpu؛ ثمّ 15.8 | 15 يُرفض (ناقص 0.72)، 15.8 ينجح | كما متوقّع، رمز 1 ولا كتابة | ✅ |
| عدد المعالجات 1/7/9/65 | `--dry-run` على 32 ج.ب | نسخ 1/1/1/2، تحذير OVERSUBSCRIBED حين يلزم | كما متوقّع، رمز 0 | ✅ |
| gpu مقابل cpu | `COMPOSE_FILE` فيه الكرت | `mode gpu (COMPOSE_FILE in .env)` | كما متوقّع | ✅ |
| مفتاح متعارض خارج الكتلة | `QDRANT_MEM` بأشكال: عاديّ، `export`، مسافات، اقتباس، تعليق لاحق، `APP_REPLICAS` | رمز 3 واسم المفتاح دون قيمته، ولا كتابة | كما متوقّع؛ والمفتاح المعلَّق `#` لا يُعدّ تعارضاً | ✅ |
| علامات مكسورة | علامة نهاية وحدها؛ علامة بدايةٍ بمسافات لاحقة | رمز 3 `malformed` | كما متوقّع | ✅ |
| CRLF + إعادة التشغيل | `.env` بنهايات CRLF مرّتين | تبقى CRLF، sha256 ثابت | كما متوقّع | ✅ |
| بلا سطر أخير / `.env` فارغ | | الكتلة في سطرها | كما متوقّع | ✅ |
| إعادة التشغيل | تشغيلان متتاليان، sha256 | متطابق | متطابق، الصلاحيّات 600 و`auto.conf` 644 | ✅ |
| حفظ الدور | `--role dev` ثمّ بلا `--role` | يبقى `dev` | كما متوقّع | ✅ |
| دور تالف في الكتلة | `# role: banana` | خطأ واضح أو رجوع آمن | يرجع إلى `server` بصمت (قد يرفض جهاز التطوير برسالة لا تذكر الدور التالف) | ⚠️ BUG‑3 |
| القيمة الحارسة | stdout وstderr في الكتابة وdry‑run والرفض والتعارض | لا تظهر | لم تظهر قطّ | ✅ |
| وسائط سيّئة | `nan`, `inf`, `1e9`, `-3`, `--mode foo`, `--profiles ../../x` | رمز 2 | رمز 2 | ✅ |
| مسار وعربيّ | مجلد `مجلد/` ومحتوى عربيّ في `.env` | يعمل ويحفظ البايتات | كما متوقّع | ✅ |
| رابط رمزيّ | `.env` رابطٌ إلى ملف | الرابط يبقى والهدف يُحدَّث | كما متوقّع | ✅ |
| `.env` بصلاحيّة 0400 | | لا يُدمّر | يُستبدل ذرّيّاً مع حفظ الصلاحيّة 0400 (يتجاوز القراءة‑فقط لأنّ المجلد قابل للكتابة) | ℹ️ ملاحظة |
| مجلد غير قابل للكتابة | `chmod 500` على مجلد `.env` وعلى `sizes/` | رسالة خطأ، `.env` ثابت | `.env` ثابت، لكن **Traceback** خام ورمز 1 | ⚠️ BUG‑1 |
| بايتات غير UTF‑8 في `.env` | `BAD=\xff\xfe` | رسالة خطأ | **Traceback** خام ورمز 1 | ⚠️ BUG‑2 |
| ملفّات مؤقّتة | بعد التشغيل | لا بقايا `/tmp/auto-size-*` ولا `.env.bak*` | لا بقايا | ✅ |
| `resource-budget.sh` مقابل master | `git show master:…`، 7 تشغيلات (افتراضيّ، `--size` ×3 + خاطئ، هامش) | مخرج مطابق | مطابق بايتاً ببايت إلّا نصّ usage (مسار السكربت و`--block`) | ✅ |
| الوثائق | عدّ `<article>` في HTML؛ معرّفات بشرطة عاديّة | 8+7+14=29 تطابق الشريط والعنوان؛ لا معرّف بشرطة ASCII في الأسطر المضافة | مطابق؛ خطوة 3.5 في اللوحة §4 و§3 | ✅ |

## 4. الأخطاء المكتشفة
| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| BUG‑1 | عدم قابليّة الكتابة في مجلد `.env` أو `deploy/postgres/sizes/` يُخرج Traceback بايثون خاماً بدل رسالة `auto-size:` (رمز 1). `.env` لم يتغيّر في الحالتين، لكن في حالة مجلد `.env` يكون `auto.conf` قد كُتب قبله (يتيم، مُتجاهَل في git) | minor | انسخ `.env.example` إلى مجلد مؤقّت مع `deploy/postgres/sizes/`؛ `chmod 500` على المجلد؛ `python3 deploy/auto_size.py --env-file <tmp>/.env --host-cpus 16 --host-memory-gb 32`. المشتبه: `deploy/auto_size.py:609` (`mkstemp`) و`:741‑742` | مفتوح (تجميلي) |
| BUG‑2 | `.env` فيه بايتات غير UTF‑8 يُخرج Traceback (`UnicodeDecodeError`) بدل خطأ واضح؛ لا كتابة | minor | `printf 'BAD=\xff\xfe\n' >> .env` ثمّ شغّل المولِّد | مفتوح (تجميلي) |
| BUG‑3 | سطر `# role:` بقيمةٍ غير `dev`/`server` يُتجاهل بصمت فيصير الدور `server`؛ على جهاز تطوير يظهر رفضٌ لا يفسّر السبب | minor | استبدل `# role: dev` بـ`# role: banana` في الكتلة، ثمّ شغّل بلا `--role` على 10 معالجات و12.67 ج.ب | مفتوح (تجميلي) |

ملاحظات لا تُعدّ أخطاء: (أ) نصّ AC‑5.4 في stories.md (22.97 ج.ب و1.81×) أقدم من design.md/الشيفرة/الاختبار (23.22 و1.83×)، والفرق مسجَّل في status.md؛ يُستحسن تحديث stories. (ب) استبدال `.env` ذرّيّاً يتجاوز صلاحيّة القراءة‑فقط (يحفظها لكنه يكتب)؛ سلوك مقصود للكتابة الذرّيّة. لا أخطاء blocking أو major.

## 5. الحكم
**PASS** — البوّابات الستّ خضراء (5714 ناجحاً، 0 تخطٍّ)، كلّ معايير Must لها اختبار ناجح، AC‑2.6 وAC‑5.7 مؤجَّلان بقرار، وثلاثة أخطاء minor تجميلية فقط (كلّها backend، لا تمنع الدمج).

## الجولة 1 (إعادة الاختبار بعد الإصلاح 0322361)

**فحص الطفرة:** استُرجعت شيفرة `b59fb85` (`auto_size.py` و`resource-budget.sh`) وشُغّل الملفّان؛ فشل 25 اختباراً على القديم، ثمّ استُعيدت الشيفرة ولا تغيير في ملفّات المنتج (`git status`).
- فشلت على القديم (أي تلتقط الأخطاء فعلاً): #1 (تعليق لاحق يُخفي gpu/profiles + 4 أشكال تحليل)، #2 (فشل temp الـ`.env` يترك `auto.conf` ثابتاً، وتنظيف temp الأول)، BUG‑2 (غير UTF‑8 في المولِّد وفي السكربت)، BUG‑3 (3 أشكال دور تالف)، SR‑1 (رابط خارج المشروع)، مجموعة وسام الصلاحيّات/المجموعة، البادئة الثابتة، حدود `--host-*`، سلّم المعالجات، مسار الملخّص، و`test_a_failed_write_leaves_no_temp_file`.
- نجح على القديم وهو متوقّع: `test_a_symlinked_env_stays_a_symlink` وبقيّة اختبارات الخط الأساسي (لا تدّعي تغطية خطأ جديد). لا اختبار ينجح على القديم وهو يدّعي تغطية خطأ مُصلَح.
- `test_the_replaced_env_keeps_its_group_and_mode`: **نُفّذ ولم يُتخطَّ** (0 تخطٍّ).

**استكشافي (نسخ مؤقّتة، القيمة الحارسة لم تظهر في أي مخرج):** تعليق لاحق `# SENTINEL` على COMPOSE_FILE يُكتشف gpu؛ tab قبل `#` يُقتطع؛ `x#y` يبقى قيمة؛ `export` والقيمة المقتبسة بنص لاحق تُعدّان تعارضاً (رمز 3)؛ `sizes/` غير قابل للكتابة: رمز 3 برسالة `auto-size:` بلا Traceback و`.env` ثابت (sha) بلا بقايا؛ مجلد `.env` غير قابل للكتابة مع كتلة قائمة: رمز 3 و`auto.conf` ثابت (sha)؛ حدود المعالجات: 0→2، 1→0، 4096→0، 4097→2؛ الذاكرة: 0→2، 4096→OK، 4097→2، `32\n`→2 (و1 ج.ب يُرفض بالعتبة رمز 1 لا استخدام)؛ `.env` رابط رمزيّ يبقى رابطاً و`auto.conf` يقع في شجرة المشروع؛ بادئة `.auto-size-tmp-*` في `.gitignore` و`.dockerignore`.

**البوّابات:** ruff format (820 ملفّاً) 0؛ ruff check 0؛ mypy (467 ملفّاً) 0؛ lint-imports 8 kept 0؛ pytest unit+architecture+eval **5745 ناجحاً، 0 تخطٍّ** (رمز 0، 165 s)؛ test-rules.sh OK (27 قاعدة) رمز 0. اختبارات auto‑size الملفّان: 159 اختباراً أبطؤها 0.53 s (إجمالاً أقل بكثير من +15 s، AC‑6.3 ✅).

**الأخطاء:** BUG‑1 وBUG‑2 وBUG‑3 مُغلقة (مُتحقَّق منها). لا أخطاء جديدة.

**الحكم (الجولة 1): PASS.**

## الجولة 2 (إعادة اختبار بعد e05804a)

**استكشافي (نسخ مؤقّتة، قيم حارسة لم تظهر في أي مخرج):** مفتاح حدّ متعارض بالأشكال `KEY: v` و`KEY:v` و`KEY : v` و`export KEY: v` و`export KEY : v` يُرفض برمز 3 بالاسم (#13). `COMPOSE_FILE` بأربعة أشكال (`: ` و`:` بلا مسافة و` : ` و`export`) يكتشف gpu (#13). فشل الاستبدال الثاني (`os.replace` على `.env`) يعطي رمز 3 برسالة تسمّي `.env` الحقيقيّ وتقول «auto.conf was put back» بلا Traceback، والملفّان متطابقان بايتاً (sha) ولا بقايا `.auto-size-tmp-*` (#14، #16). إعادة كتابة `.env` تعطي inode جديداً ووقت تعديل جديداً (بعد ضبطه على 2001) مع بقاء الصلاحيّات 0640 والمجموعة (#15).

**التصحيح #18:** صفّ AC‑4.3 لم يعد يشير إلى الاختبار المحذوف؛ الدليل مخرج `git diff --exit-code` في وصف الـPR.

**البوّابات:** ruff format 820 ملفّاً 0؛ ruff check 0؛ mypy 467 ملفّاً 0؛ lint-imports 8 kept 0؛ pytest unit+architecture+eval **5758 ناجحاً، 0 تخطٍّ** (رمز 0، 163 s)؛ test-rules.sh OK (27 قاعدة) رمز 0.

**الأخطاء:** لا جديدة؛ #13 و#14 و#15 و#16 و#18 مُتحقَّق منها.

**الحكم (الجولة 2): PASS.**

</div>
