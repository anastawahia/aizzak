<div dir="rtl">

# قبول مالك المنتج: F0، إصلاح `ele-parse` والتصنيف وتقرير التوصيف

- **الفرع والنطاق:** `fix/ele-parse-curves`، و`HEAD` = `fa030c4`، والتغيير `git diff 1ce3cc6..HEAD`.
- **المستودع عام:** أعداد فقط هنا، ولا اسم ملف ولا وسم.
- **معنى الحكم:** `F0‑AC‑1..7` محكومة الآن. و`F0‑AC‑8` لا يُحكم قبل الدمج (انظر قسمه أدناه).

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| F0‑AC‑1 | `test_ele_parse_service.py::test_f0_ac_1_a_real_bezier_page_is_read_and_only_the_small_curve_is_counted`: ملف PDF حقيقي فيه منحنى 6 pt وآخر 200 pt. عبر `child.handle` النتيجة `ok`، و`vector_path_count == 2`، و`short_curve_path_count == 1`. وعبر `inspect_file` بعملية فرعية حقيقية: لا مفتاح `failed_pages`. والحد في `test_f0_qa_curve_extent_boundary_is_ten_points_inclusive`: 9.9 و10 يُعدّان، و10.5 لا يُعدّ. | ✅ مقبول |
| F0‑AC‑2 | `test_f0_ac_2_lines_rects_and_quads_do_not_break_the_page_or_count_as_curves`: `ok`، والعدّاد 1. طبعتُ عناصر هذا الملف: `l` 2، و`re` 1، و`qu` 1، و`c` 1، كلها tuples. وحذف فحص `item[0] != "c"` أو السماح بـ`l` يُفشل الاختبارات. ونتيجة الفحص في `design.md` §3.3، وأكدتُها بالبحث: استعمال إنتاج واحد في `inspect.py:155`، ولا قراءة `.x` أو `.y` في `services/ele_parse`. | ✅ مقبول |
| F0‑AC‑3 | السجل والمجموع: `test_f0_ac_3_a_page_with_no_classification_is_failed_and_an_empty_one_is_not` (1، ثم 0، و`EMPTY` 2 تعطي 0)، و`..._the_report_sums_the_failed_pages_of_its_documents`. JSON وMarkdown: `test_f0_ac_3_the_json_and_the_markdown_show_the_failed_pages`، و`test_f0_ac_3_a_dropped_page_reaches_the_json_and_the_markdown` (السطر، وخلية `failed` = 1، بعد الإعادة 1). الواجهة: `test_f0_ac_3_failed_pages_reach_both_characterization_responses`، و`..._a_document_with_every_page_read_has_no_failed_pages`. المخطط: `test_f0_qa_the_api_schemas_and_openapi_agree_on_nullable_and_required`. **المسار الحقيقي:** مرشح `evidence` في SQL مراجَع بالقراءة، وتحققه الوظيفة `integration` (الشرط 3). | ✅ مقبول |
| F0‑AC‑4 | `test_ops_electrical_characterize.py::test_f0_ac_4_a_failed_or_left_out_page_is_counted_alike_by_the_tool_and_the_worker[parse_error]` و`[left_out]`: مقارنة JSON لـ`to_plain` من الطرفين بالمساواة التامة، و`ours.failed_pages == stored.failed_pages == 1`، و`len(PINNED) == 5`. والفحص الثاني لازم لأن `to_plain` لا يمر على الخاصية. | ✅ مقبول |
| F0‑AC‑5 | `test_f0_ac_5_many_vector_paths_with_little_text_are_outlined`: 400 داخل، و399 خارج، و5,000 بلا نص تعطي `VECTOR_OUTLINED` و`needs_ocr`. `test_f0_ac_5_the_paths_arm_uses_the_configured_threshold`: 50 و49 بعتبة مخصصة. `test_f0_ac_5_sound_text_with_many_paths_stays_vector_text_and_other_kinds_hold`: `RASTER` و`VECTOR_GARBLED` قبل القاعدة، وليس `MIXED`. واختبار `MIXED` القديم لم يتغيّر. `test_f0_qa_pipeline_version_is_1_1_0`. | ✅ مقبول |
| F0‑AC‑6 | `test_f0_ac_6_without_an_observed_convention_there_is_no_symbol_claim` (`None`، والأساس بلا `convention:`، ومع منتج له تلميح أيضاً)، و`test_f0_ac_6_the_report_counts_unclaimed_documents_under_unknown` (المجموع = `documents`)، و`test_f0_ac_6_the_markdown_claims_no_symbol_standard_without_evidence` (`iec_60617` غائب، والخلية `unknown`)، و`test_f0_ac_6_a_document_without_a_convention_claims_no_symbol_standard` على الواجهة. | ✅ مقبول |
| F0‑AC‑7 | `test_f0_ac_7_the_proposed_profile_and_its_confidence_do_not_change_without_symbols`، و`test_f0_ac_7_a_row_stored_before_f0_reads_and_derives_its_failed_pages` (صف بلا `mixed_standards`: 5 صفحات و2 مصنفة تعطي 3)، و`test_f0_ac_7_a_row_with_a_null_guess_reads_and_round_trips`. **وتحقق مستقل:** شغّلتُ `detect_profile` من `1ce3cc6` ومن `HEAD` على 120 حالة، فتطابق المعيار والثقة وثقة الحقول والبصمة تماماً. | ✅ مقبول |
| F0‑AC‑8 | لا دليل على المعيار نفسه، لأنه لا يتحقق إلا من `master` المدموج. والموجود معاينة فقط: `test-plan.md` §3، وهي مطابقة لكل الأرقام. أعدتُ تشغيل الأداة على مخرجات الفحص من `HEAD`، فخرج التقرير مطابقاً بايتاً ببايت للتقرير الخام، وبلا `iec_60617` في Markdown. | ⏳ لم يُحكم، وليس ✅ |

## ما تحققتُ منه بنفسي

- **الاختبارات:** فتحتُ كل اختبار مذكور في الجدول، وأشرت إلى تأكيداته لا إلى اسمه. وشغّلتُ ملفات F0 السبعة على `fa030c4`: `tests=249 failures=0 errors=0 skipped=0`، برمز خروج 0.
- **الطفرات:** 11 طفرة على نسخة خارج المستودع، كلها كُشفت. منها حذف فحص `c`، والعودة إلى `point.x`، و`>` بدل `>=` عند العتبة، وتطبيق المسارات على `MIXED`، والتخمين الدائم، والأساس الدائم، وأصفار في الواجهة وMarkdown، وقراءة `null`.
- **البوابات الست:** أعتمد فيها رقم المنسّق على `fa030c4` (`tests=8353 failures=0 errors=0 skipped=37`). ولم أشغّل `tests/integration`.
- **الإيداع `fa030c4`:** ثلاثة ملفات اختبار فقط، فلا يحتاج إعادة مراجعة، وهو يغلق ملاحظات المراجعة #2 و#3 و#4 و#5.
- **`design.md` §7 و§8 مقابل ملاحظة المراجعة #1:** متطابقان. ق‑5 يقول إن `_conclude` يختم الإصدار الحالي على صفوف مخزّنة، فلا يكشفها سطر `versions`. و§8 يوجب `reprocess` كاملاً ولو أُكّدت الوثيقة بعد F0. وتحققتُ من الشيفرة في `inspection.py:710`، ولم يبقَ ادعاء قديم في الملف.

## مطابقة نطاق F0 (`p1-fix-path.md` §6)

| البند | ما طلبه المالك | الحال |
|---|---|---|
| 1 | `_paths` بالفهرس، واختبار بمنحنى حقيقي | مُسلَّم: `F0‑AC‑1` |
| 2 | فحص كل استعمال آخر لمخرجات `get_cdrawings()` | مُسلَّم: `F0‑AC‑2` و`design.md` §3.3 |
| 3 | إظهار الصفحات الفاشلة في التقرير | مُسلَّم: `F0‑AC‑3` و`F0‑AC‑4` |
| 4 | صفحة قليلة النص كثيرة المسارات لا تكون `VECTOR_TEXT` | مُسلَّم: `F0‑AC‑5`، ودون 400 مسار تبقى `VECTOR_TEXT` بقرار ق‑2 |
| 5 | عدم عرض `iec_60617` نتيجةً بلا دليل | مُسلَّم في التقرير كله: `F0‑AC‑6`. والملف المقترح يبقى كما هو بثقة 0.0 (`F0‑AC‑7`)، وأقبل هذا التفسير لأنه مقترح ينتظر تأكيد إنسان، لا نتيجة |
| 6 | إعادة التوصيف على الملفات الثلاثة بشيفرة `master` | مؤجَّل صراحة إلى الخطوة 11و: `F0‑AC‑8` |

**لا تسرّب من F1 أو F2 أو F3.** 17 ملفاً فقط: قائمة `design.md` §2 ومعها ملف QA. ولا مساس بـ`shared` ولا ملفات القواعد ولا `contract.py` ولا الترحيلات ولا `pyproject.toml` ولا Compose ولا CI. ولا ذكر لـKKS أو ANSI أو RapidOCR أو أي مرحلة OCR في أسطر الإنتاج المتغيّرة.

## `F0‑AC‑8`: هل يصح القبول وهو معلّق؟

**نعم، بشرط.**

- المعيار يقول بنصه «بعد الدمج فقط»، فاشتراط ✅ قبل الدمج يجعل الدمج مستحيلاً.
- والقاعدة العامة «كل معيار ✅» لا تنطبق عليه حرفياً، فأسجّل هذا استثناءً صريحاً، ولا أعدّ المعاينة دليلاً عليه.
- الخطر المتبقي صغير: المعاينة شغّلت `inspect.py` المعدّل نفسه في صورة PyMuPDF 1.28.2، والفرق أن الصورة لم تُبنَ من `master` بعد.
- **الشرط:** لا يُغلق F0 حتى تُنفَّذ الخطوة 11و (`design.md` §9) وتُسجَّل أعدادها. فإن خالفت الأرقام `F0‑AC‑8`، سقط هذا القبول، ويعود العمل إلى البناء (6و) إن كان العيب في الشيفرة، أو إلى التصميم (4و) إن كان في القاعدة أو العتبة.

## الشروط

**قبل الدمج:**

1. **قرار المالك في S‑1، قبل أول رفع.** الفرع غير مرفوع، و`1ce3cc6` ليس على `origin/master`. فبعد الرفع تضيع فرصة حذف النسخة الإضافية من التاريخ. أما نسخة `master` فباقية في التاريخ العام أياً كان القرار.
2. **إيداع وثائق الدورة** (`inc-1/f0/` و`status.md`) مع الـPR، فهي غير مودَعة.
3. **كل وظائف CI خضراء على الـPR، وخصوصاً `integration`.**
   - لم يُشغَّل `tests/integration` هنا، ومرشح `evidence` الحقيقي مراجَع بالقراءة.
   - الاختبار الذي يحسمه: `test_evidence_reads_back_finished_pages_with_their_tally_and_reset_reopens_them`، وهو يؤكد أن صفحة `EMPTY` الفاشلة ليست دليلاً، وعلامته `live_db` غير مستثناة في CI.
   - إن فشل، يعود العمل إلى البناء (6و).
4. **بوابة ② الدمج:** قرار بشري، وهذا القبول لا يحل محله.

**بعد الدمج، وقبل عدّ F0 منتهياً:**

5. **`F0‑AC‑8`:** الخطوة 11و بصورة بوسم مستقل، في حاوية بلا شبكة، فلا تمس المكدّس.

**عند النشر، وهو قرار بشري:**

6. **بناء الصور.** حتى ذلك الحين تبقى صفحات المنحنيات فاشلة في الصورة الحالية (B‑4).
7. **ترتيب النشر:** `app` قبل `worker-electrical` أو معه، لأن `app` القديم لا يقرأ صف `null`، وصف واحد يكفي لإسقاط تقرير المشروع.
8. **`reprocess` كامل لكل وثيقة من قبل F0، بعد نشر الصور الجديدة.** ولم يُعَدّ ما على المكدّس منها.
   - **تنبيه غير مذكور في `design.md`:** `reprocess` يرد 409 `electrical.profile_unconfirmed` ما دام الملف مقترحاً، وهذه حال الوثائق بلا رموز، لأن ثقتها 0.
   - فالترتيب لهذه الوثائق: تأكيد الملف أو استبداله، ثم `reprocess` كامل. وبينهما يحمل السجل `1.1.0` بتصنيف قديم (الملاحظة #1).

## قرارات المالك، ولا تحجب الدمج

- **المراجعة #1، الحل الأقوى:** ختم السجل بأقدم `pipeline_version` في صفوف صفحاته، في إيداع منفصل. ولأجل ذلك لا يُعتمد سطر `versions` دليلاً على إعادة التصنيف. والأثر في وصف السجل فقط، ولا شيء يوجّه على `needs_ocr` في INC‑1.
- **S‑1:** استبدال الاسم بعبارة عامة في الموضعين، وإعادة كتابة التاريخ إن أرادها المالك. والموعد في الشرط 1.

## متابعات

- **S‑2، سابقة لـF0:** انهيار `get_cdrawings()` عند نفاد الذاكرة داخل الطفل، وينتهي بأمان `507 memory_limit` ثم `failed`. المتابعة: إبلاغ PyMuPDF، وقرار تقوية `PR_SET_DUMPABLE=0`.
- **وثائق قديمة في `design.md` §10:** `docs/lld-inc-1.md` و`docs/design/06-domain-models.md:317`، و`inc-1/characterization.md` §5 في الخطوة 11و.
- **`test-plan.md`:** كُتب قبل `fa030c4`. فما زال يذكر الاسم القديم لاختبار `an_image_only_page_is_not_empty` (صار `a_blank_page_is_empty`)، و14 اختباراً في ملف QA (صارت 15)، و8351 (صارت 8353). الأرقام الصحيحة أعلاه، ويُحدَّث عند إيداع الوثائق.

## ملاحظات غير حاجبة مقبولة

- **المراجعة #1** بالحد الأدنى المنفذ، والحل الأقوى قرار المالك.
- **S‑1** و**S‑2** (منخفضتان)، و**S‑3** و**S‑4** (إعلاميتان). وS‑4 يلزم تذكّره عند تصميم أي توجيه على `needs_ocr`.
- **B‑2:** القص بـ`max(0, ...)` يخفي زيادة التصنيف، ولا يصل إليه إلا ملف فحص فيه صفحة مكررة. **B‑3:** `nullable: true` عرف قائم في الملف. **B‑4:** شرط النشر 6.
- **ق‑2:** العتبة 400 قيمة بداية لا قيمة مقيسة، و`INC1‑R‑3` مفتوح. وإن نقص كشف النص المرسوم عند مورد آخر، فالأمر يُراجع في F1.
- **حد باقٍ في الاختبار:** كتابة `null` في Postgres الحقيقي غير مغطاة حتى في `integration`، فاختبار RLS الحي يكتب قيمة غير فارغة. وأقبله لأن الدالتين مغطاتان بوحدة، ولا CHECK على `report` غير الحجم، والعمود `jsonb`.
- **`design.md` §3.3** يقول لا `get_drawings` في `tests`، وقد أضاف `fa030c4` استعمالاً واحداً في اختبار، وهذا لا يمس النتيجة.

## الحكم النهائي

**مقبول** — بشروط، وللدمج فقط. `F0‑AC‑1..7` كلها ✅ بدليل مفتوح ومُختبر، و`review.md` و`security-review.md` كلاهما PASS، وبنود §6 مسلَّمة أو مؤجلة صراحة، ولا شيء من F1 أو F2 أو F3 دخل. والشروط 1 إلى 4 لازمة قبل الدمج. ويبقى F0 مفتوحاً حتى يتحقق `F0‑AC‑8` بعد الدمج، فإن فشل سقط هذا القبول.

</div>
