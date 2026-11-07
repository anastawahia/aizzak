<div dir="rtl">

# خطة الاختبار: F0، إصلاح `ele-parse` والتصنيف وتقرير التوصيف

- **الفرع:** `fix/ele-parse-curves`، الإيداعات `e9af6fb` و`eb9c0d8` و`bcab640` و`59e19c2` فوق `1ce3cc6`، وإيداع QA هو `791029a`.
- **المستودع عام:** أعداد فقط، والاختبارات على ملفات PDF اصطناعية.

## 1. مصفوفة التغطية

التحقق: لكل اختبار مهم أُزيل الإصلاح بشكل مؤقت (تعديل بـ`sed` ثم `git checkout -- <file>` وعادت الشجرة كما كانت) وتأكد أن الاختبار يفشل.

| AC | نوع الاختبار | الملف/الاختبار | يفشل بدون الإصلاح؟ | الحالة |
|---|---|---|---|---|
| F0‑AC‑1 | وحدة بعملية فرعية حقيقية | `tests/unit/test_ele_parse_service.py::test_f0_ac_1_a_real_bezier_page_is_read_and_only_the_small_curve_is_counted`، و`test_electrical_f0_qa.py::test_f0_qa_curve_extent_boundary_is_ten_points_inclusive` (الحد 10 pt شاملاً، و10.5 لا يُعدّ) | نعم (M1: العودة إلى `point.x`) | ✅ |
| F0‑AC‑2 | وحدة | `test_ele_parse_service.py::test_f0_ac_2_lines_rects_and_quads_do_not_break_the_page_or_count_as_curves` | نعم (M1) | ✅ |
| F0‑AC‑3 | وحدة وعقد | `test_electrical_profile_detection.py::test_f0_ac_3_a_page_with_no_classification_is_failed_and_an_empty_one_is_not`، `..._the_report_sums_the_failed_pages_of_its_documents`؛ `test_ops_electrical_characterize.py::test_f0_ac_3_the_json_and_the_markdown_show_the_failed_pages`، `..._a_dropped_page_reaches_the_json_and_the_markdown`؛ `test_api_electrical_router.py::test_f0_ac_3_failed_pages_reach_both_characterization_responses`، `..._a_document_with_every_page_read_has_no_failed_pages`؛ و`test_electrical_f0_qa.py::test_f0_qa_failed_pages_is_never_negative_and_zero_pages_is_zero` | نعم (M5: `failed_pages` يعيد 0؛ فشل 6 اختبارات) | ✅ |
| F0‑AC‑4 | وحدة | `test_ops_electrical_characterize.py::test_f0_ac_4_a_failed_or_left_out_page_is_counted_alike_by_the_tool_and_the_worker[parse_error]` و`[left_out]` (مقارنة `to_plain` بايتاً ببايت، وعدد الملفات المثبتة 5) | نعم (M5) | ✅ |
| F0‑AC‑5 | وحدة | `test_electrical_classification.py::test_f0_ac_5_many_vector_paths_with_little_text_are_outlined` (400 داخل، 399 خارج)، `..._sound_text_with_many_paths_stays_vector_text_and_other_kinds_hold`؛ وإضافات QA: `test_f0_qa_no_text_many_paths_and_a_small_image_is_outlined`، `test_f0_qa_a_big_image_beats_many_paths_and_an_image_only_page_is_not_empty`، `test_f0_qa_pipeline_version_is_1_1_0` | نعم (M2: إزالة شرط المسارات). **فجوة وُجدت:** إعادة `PIPELINE_VERSION` إلى `1.0.0` لم يفشل أي اختبار في كل `tests/unit`، فأضفتُ اختباراً يثبت `1.1.0` | ✅ (بعد إضافة QA) |
| F0‑AC‑6 | وحدة وواجهة | `test_electrical_profile_detection.py::test_f0_ac_6_without_an_observed_convention_there_is_no_symbol_claim`، `..._the_report_counts_unclaimed_documents_under_unknown`؛ `test_ops_electrical_characterize.py::test_f0_ac_6_the_markdown_claims_no_symbol_standard_without_evidence`؛ `test_api_electrical_router.py::test_f0_ac_6_a_document_without_a_convention_claims_no_symbol_standard`؛ وإضافات QA: ترتيب `unknown` بعد المعايير المعروفة، وتجميع فارغ | نعم (M3: التخمين دائماً غير فارغ؛ M4: الأساس يذكر `convention:` دائماً) | ✅ |
| F0‑AC‑7 | وحدة | `test_electrical_profile_detection.py::test_f0_ac_7_the_proposed_profile_and_its_confidence_do_not_change_without_symbols`؛ `test_electrical_adapters.py::test_f0_ac_7_a_row_stored_before_f0_reads_and_derives_its_failed_pages`، `..._a_row_with_a_null_guess_reads_and_round_trips`؛ وإضافة QA: أساس رموز فارغ مقبول في نموذج الواجهة | نعم (M5 للصف القديم) | ✅ |
| F0‑AC‑8 | يدوي بعد الدمج | معاينة فقط قبل الدمج (§3، الصف «معاينة F0‑AC‑8»). المعيار نفسه يحتاج صورة من `master` المدموج | لا ينطبق | ⏳ معاينة مطابقة، والمعيار بعد الدمج |

### اختبارات QA المضافة (إيداع `791029a`)

الملف `tests/unit/test_electrical_f0_qa.py`، 14 اختباراً، كلها خضراء:
- حدود `_paths`: امتداد 9.9 و10 و10.5، ومنحنيان في مسار واحد (2)، وصفحة بلا رسوم، ومنحنى متدهور (لا يرفع خطأ)، ومنحنى داخل Form XObject.
- القاعدة 4: صفحة بلا نص و5,000 مسار وصورة صغيرة تعطي `VECTOR_OUTLINED`؛ وصورة كبيرة تسبقها `RASTER`؛ وصفحة بلا شيء `EMPTY`.
- `PIPELINE_VERSION == "1.1.0"`.
- `failed_pages` عند `page_count` = 0، وعند زيادة `text_kinds` على `page_count`، وعند عدم تصنيف أي صفحة.
- `aggregate([])`، وترتيب مفاتيح `symbol_standards` (المعروفة ثم `unknown`).
- `openapi.yaml` يطابق نموذجي الواجهة: `failed_pages` إلزامي، و`symbol_standard_guess` إلزامي وقابل للقيمة الفارغة.

## 2. نتائج بوابات المشروع

```
$ .venv/bin/ruff format --check .
985 files already formatted                         exit=0
$ .venv/bin/ruff check .
All checks passed!                                  exit=0
$ .venv/bin/mypy src
Success: no issues found in 547 source files        exit=0
$ .venv/bin/lint-imports
WireTwin sections are independent and shared is the floor KEPT
Contracts: 9 kept, 0 broken.                        exit=0
$ .venv/bin/pytest tests/unit tests/architecture tests/eval -rs --junitxml=.../junit-qa.xml
8314 passed, 37 skipped, 7 warnings in 273.49s      exit=0
  junit-qa.xml: tests=8351 failures=0 errors=0 skipped=37
$ deploy/prometheus/test-rules.sh
SUCCESS: 31 rules found
test-rules: OK                                      exit=0
```

- **المتخطى (37):** كلها اختبارات حاويات اختيارية: `RUN_EDGE_FILES_TEST=1` (17) و`RUN_ELE_PARSE_CONTAINER_TEST=1` (14)، والباقي من الصنفين نفسيهما بعدّاد متعدد. لم تُضف مرشحات.
- لم يُشغَّل `tests/integration` بحسب القيد (المكدّس الحي قيد الاستعمال). الصف المخزّن مغطى بوحدة في `test_electrical_adapters.py`، ولا ترحيل في F0.
- ملاحظة: حاوية الاختبار ليست `pymupdf` 1.28.2 بل 1.28.0 في `.venv`. أُعيد فحص حدود `_paths` داخل الصورة (1.28.2) مع `inspect.py` المعدّل مركباً للقراءة فقط، والنتائج مطابقة تماماً للقيم أعلاه.

## 3. الاختبار الاستكشافي

| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| إعادة إنتاج العيب الأصلي | تشغيل `_paths` من `inspect.py` القديم في الصورة `p1-fe6dde9` على صفحة منحنى | `AttributeError` | `'tuple' object has no attribute 'x'` | ✅ العيب مؤكد، والإصلاح يزيله |
| حدود `_paths` على 1.28.0 مقابل 1.28.2 | امتداد 9.9 و10 و10.5، ومنحنيان في مسار، وXObject، ومنحنى متدهور | نتائج واحدة | متطابقة: (1,1) (1,1) (1,0) (1,2) (1,1) و(1,0) للمتدهور | ✅ |
| منحنى متدهور (كل النقاط متساوية) | `draw_bezier` بأربع نقاط واحدة | لا خطأ | PyMuPDF يخزّنه `l` فيُتخطى؛ لا خطأ | ✅ |
| صفحة بلا رسوم | صفحة فارغة | (0,0) | (0,0) | ✅ |
| `page_count` = 0 | `failed_pages` | 0 | 0 | ✅ |
| `text_kinds` أكبر من `page_count` | 3 مصنفة من 1 | غير سالب | 0 (مقصوص بـ`max(0, ...)`، فيُخفي عدم اتساق لو حصل) | ✅ مع ملاحظة (B‑2) |
| `aggregate([])` | قائمة فارغة | لا خطأ | `symbol_standards = {}` و`failed_pages = 0` | ✅ |
| مستهلكو `symbol_standard_guess` | بحث في `src` و`services` و`tests` و`tests/eval` و`docs/design` | لا مستهلك يفترض غير الفارغ | `m32_profile_detection.py` يقرأ `profile.symbol_standard` من المقترح (لا من السجل)، وهو لم يتغيّر. لا فرز يخلط `None` بنصوص (`Counter` على المفاتيح). `test_electrical_drawings_repository_rls.py` (حي) يكتب قيمة غير فارغة فقط | ✅ |
| `symbol_standard_basis` فارغاً | الكتابة والقراءة والواجهة | يعمل | يعبر `list(())` في المستودع ونموذج الواجهة (اختبار QA)، ولا مستهلك آخر | ✅ |
| أثر رفع `PIPELINE_VERSION` | بحث عن مقارنات | لا إعادة معالجة تلقائية | `knowledge.PIPELINE_VERSION` ثابت منفصل (عدد صحيح). في الكهرباء يُكتب فقط (`inspection.py`) ولا يُقارن | ✅ |
| القاعدة 4 مع `char_count`=0 ومسارات كثيرة وصورة | الاختبارات أعلاه | `RASTER` ثم `VECTOR_GARBLED` ثم الجديدة، و`EMPTY` فقط بلا مسارات ولا صور | كما هو متوقع | ✅ |
| `openapi.yaml` | تحليل YAML ومقارنة بنموذجي Pydantic | يُحلَّل، و`required` و`nullable` صحيحان | صحيح للمخططين؛ `nullable: true` في ملف 3.1.0 هو عرف الملف (89 استعمالاً) | ✅ (B‑3 ملاحظة) |
| معاينة F0‑AC‑8 (قبل الدمج، ليست المعيار) | `inspect` في حاوية مؤقتة `--network none --read-only --cap-drop ALL` والملفات `:ro`، مع `inspect.py` المعدّل مركباً، ثم `electrical_characterize` بلا `--with-names` | 3 صفحات، 0 فاشلة، 3 `vector_outlined`، النسبة 1.0، `unknown` 3، pipeline `1.1.0` | **3 صفحات (ملف واحدة لكل ملف)، `failed_pages` = 0، `vector_outlined` = 3، `ocr_needed_ratio` = 1.0، `symbol_standards` = `{unknown: 3}`، pipeline = `['1.1.0']`، ولا `iec_60617` في Markdown، وسطر `failed pages: 0 of 3`، والتخمين `null` والأساس `[]` في الوثائق الثلاث** | ✅ معاينة مطابقة؛ المعيار الفعلي بعد الدمج |
| ازدواج الإرسال وعزل المستأجر والصلاحيات | غير متأثرة: لا مسار ولا صلاحية ولا ترحيل جديد في F0 | لا تغيير | لم تُفحص على المكدّس (ممنوع) | غير منطبق |
| RTL/375px | لا واجهة في هذا المستودع | — | — | غير منطبق |

مخرجات المعاينة خارج المستودع في `/tmp/claude-1000/-home-AIZZAK/ea1330df-7a84-4d14-8a7a-9bc5fe55ade1/scratchpad/f0-preview/` (`report.json` و`report.md`). لم يدخل المستودع أي اسم ملف أو مشروع أو وسم حقيقي.

## 4. الأخطاء المكتشفة

لا خطأ يمنع الدمج ولا خطأ كبير. البنود التالية ملاحظات.

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| B‑1 | لم يكن أي اختبار يثبت `PIPELINE_VERSION == "1.1.0"` (جزء من F0‑AC‑5): تغيير الثابت إلى `1.0.0` يُبقي كل `tests/unit` أخضر | صغير (فجوة تغطية) | في `domain/pipeline.py:8` غيّر `"1.1.0"` إلى `"1.0.0"` ثم `pytest tests/unit` | عولج باختبار `test_f0_qa_pipeline_version_is_1_1_0` في `791029a` |
| B‑2 | `Characterization.failed_pages` يقصّ بـ`max(0, ...)`، فلو زاد عدد الصفحات المصنفة على `page_count` (مثلاً صفحة مكررة في ملف فحص) يظهر 0 بصمت | صغير | `Characterization(page_count=1, text_kinds={VECTOR_TEXT: 3}, ...).failed_pages` يعطي 0 | مقبول ومثبت باختبار؛ القرار موثق في التصميم، والملف المحمّل يتحقق من العقد |
| B‑3 | `openapi.yaml` يستعمل `nullable: true` ضمن `openapi: 3.1.0`، وهو كلمة 3.0 | صغير (توافق مولّدات) | `grep -n "nullable: true" docs/design/openapi.yaml` | موجود قبل F0 (89 استعمالاً)، فالإضافة تتبع العرف؛ لا إجراء |
| B‑4 | صورة `aizzak/ele-parse:p1-fe6dde9` الحالية فيها `inspect.py` القديم، فتبقى صفحات المنحنيات فاشلة حتى إعادة بناء الصورة | معلوم (نشر) | `docker run --entrypoint python aizzak/ele-parse:p1-fe6dde9 ...` على صفحة منحنى | خارج الفرع: قرار بشري بعد الدمج (تصميم §8) |

الوثائق القديمة المذكورة في تصميم §10 (`docs/lld-inc-1.md` و`docs/design/06-domain-models.md:317`) ما زالت قديمة، وهي متابعة مسجلة لا عيب.

## 5. الحكم

**PASS** لهذه المرحلة: البوابات الست خضراء (8314 ناجحاً و0 فاشلاً و0 أخطاء، و37 متخطى كلها اختيارية)، وكل `F0‑AC‑1..7` ✅ باختبار يفشل بدون الإصلاح، ومعاينة `F0‑AC‑8` على الملفات الثلاثة تطابق الأرقام المطلوبة. `F0‑AC‑8` نفسه ⏳ حتى بناء صورة من `master` المدموج. لا خطأ يمنع.

**بعد الإعادة 1 (`fa030c4`، إضافة المنسّق):** الأرقام أعلاه على `791029a`. على `fa030c4` البوابات الست برمز خروج 0، و`tests=8353 failures=0 errors=0 skipped=37`، وتغيّر اسم اختبار في `test_electrical_f0_qa.py` وزاد عدد اختباراته.

</div>
