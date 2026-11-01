<div dir="rtl">

# خطة الاختبار: electrical-drawings، INC‑1، `PR‑B` (النواة `shared`، CMP‑06، الخطوة S4)

الفرع: `feat/electrical-drawings-inc-1-pr-b` (الأساس `master` = `eaf888e`). الإيداعات المفحوصة: `9078ed3` و`f741d4c`.

## 1. مصفوفة التغطية
| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| INC1‑AC‑4: كل صف في ملحق أ (النوع، الاصطلاح، الجوانب، نقطة الاتصال، الأشكال الثلاثة) | وحدة | `tests/unit/test_electrical_shared_parser.py::test_every_appendix_a_example_parses_to_its_kind` (جدول ملحق أ مكتوب حرفياً، مستقل عن ملف الحالات) + `test_the_parser_returns_exactly_what_each_case_says[c001..c171]` | ✅ |
| INC1‑AC‑4: IEC 81346 وجوانبه (`=`/`+`/`-`/`==`/`++`) والهرمية | وحدة | `test_electrical_shared_reference_designation.py::test_the_aspect_chain_is_split_in_order`، `test_gbt_5094_is_chosen_when_only_it_is_enabled` | ✅ |
| INC1‑AC‑4: نقطة الاتصال ودور القطب (FR‑ID‑07) | وحدة | `test_electrical_shared_reference_designation.py::test_the_connection_point_and_its_pole_role`، `test_electrical_shared_terminals_wires.py::test_connection_point_labels_and_pole_roles` | ✅ |
| INC1‑AC‑4: الأطراف والجسور (FR‑ID‑03) | وحدة | `test_electrical_shared_terminals_wires.py::test_every_terminal_form_of_the_srs`، `::test_bridges` | ✅ |
| INC1‑AC‑4: أرقام الأسلاك بأنماط قابلة للضبط (FR‑ID‑04) | وحدة | `test_electrical_shared_terminals_wires.py::test_wire_numbers_by_configured_pattern`، `::test_the_page_column_wire_needs_a_hint_because_12_3_is_a_cross_reference` | ✅ |
| INC1‑AC‑4: كل عائلات S7 (EN/DE/TIA/DB/S7‑200) | وحدة | `test_electrical_shared_plc_siemens.py::test_every_siemens_form_in_the_srs_appendix`، `::test_the_same_address_is_findable_in_every_s7_spelling` | ✅ |
| INC1‑AC‑4: أكواد الحروف الصينية وNFPA/JIC (FR‑ID‑13) | وحدة | `test_electrical_shared_letter_codes.py` (كل الاختبارات الثمانية) | ✅ |
| INC1‑AC‑4: المرجع المتقاطع والصيغة السائدة (FR‑ID‑06) | وحدة | `test_electrical_shared_cross_references.py::test_each_format_of_the_srs`، `::test_the_tally_counts_formats_and_the_dominant_one_wins`، `::test_a_tie_goes_to_the_alphabetically_first_format` | ✅ |
| INC1‑AC‑4: الجهود (FR‑ID‑08) | وحدة | `test_electrical_shared_terminals_wires.py::test_voltage_labels`، `::test_voltage_wins_over_the_aspect_reading_of_plus_24v` | ✅ |
| INC1‑AC‑4: التطبيع كامل العرض وأشكاله الثلاثة (FR‑ID‑09) | وحدة | `test_electrical_shared_normalization.py` (7 اختبارات)، `test_electrical_shared_letter_codes.py::test_a_full_width_code_parses_and_keeps_its_original`، **جديد** `test_electrical_shared_robustness.py::test_the_full_width_spelling_of_every_ascii_designation_equals_the_ascii_parse` | ✅ |
| INC1‑AC‑4: غير الصالح يُعلَّم ولا يُسقط (FR‑ID‑10) | وحدة | `test_electrical_shared_parser.py::test_invalid_input_is_flagged_never_dropped`، **جديد** `test_electrical_shared_robustness.py::test_a_malformed_form_of_an_appendix_a_example_is_flagged_not_dropped` | ✅ |
| INC1‑AC‑4: القواعد من ملف بإصدار، و`RuleSelection` يضيّق، والاسم المجهول `RuleError` | وحدة | `test_electrical_shared_rules.py` (كل الاختبارات)، **جديد** `test_electrical_shared_robustness.py::test_a_document_profile_cannot_make_the_parser_slow_or_compile_user_input` | ✅ |
| INC1‑AC‑4: `shared` بالمكتبة القياسية وبلا I/O؛ العقدان `domain-purity` و`electrical-contexts` محفوظان | وحدة + معمارية | `test_electrical_shared_parser.py::test_the_kernel_imports_only_the_standard_library_and_itself`، `lint-imports` (9 kept)، `tests/architecture/test_import_contracts.py::test_the_skeleton_invariants_hold` | ✅ |
| INC1‑AC‑4: M‑04 على مجموعة الحالات = 1.0 | وحدة | `test_electrical_shared_parser.py::test_m04_on_the_case_set_is_one` | ✅ |
| INC1‑AC‑4: الحتمية (FR‑ID‑12) والمتانة | وحدة | `test_electrical_shared_parser.py::test_the_parse_is_deterministic`، **جديد** `test_electrical_shared_robustness.py::test_random_unicode_never_raises_and_is_deterministic`، `::test_adversarial_input_is_fast_and_never_raises` | ✅ |
| INC1‑AC‑13 (حصة PR‑B): هيكل التقرير JSON بالالتزام والإصدارات وبصمة المدخلات | وحدة | `test_electrical_eval_framework.py::test_the_report_is_written_as_sorted_json_with_commit_versions_and_fingerprint`، `::test_build_report_records_the_versions_of_what_was_measured`، `::test_the_fingerprint_ignores_input_order_and_changes_with_content` | ✅ |
| INC1‑AC‑13 (حصة PR‑B): مقارنة تشغيلين | وحدة | `test_electrical_eval_framework.py::test_two_runs_are_compared_by_delta`، `::test_a_metric_only_in_one_run_is_not_compared` | ✅ |
| INC1‑AC‑13 (حصة PR‑B): حساب M‑04 | وحدة | `test_electrical_eval_framework.py::test_m04_is_the_exact_match_rate_with_a_breakdown`، `::test_m04_breakdowns_cover_standard_script_and_source`، `::test_the_dataset_fingerprint_changes_when_a_case_changes` | ✅ |
| INC1‑AC‑13 (الحصص الأخرى: التوصيف، `electrical_characterize`، M‑32) | — | خارج PR‑B (PR‑C/PR‑D) | غير مطلوب هنا |
| INC1‑AC‑16: لا تغيير في `rag_agent` ولا `knowledge`؛ البوابات خضراء | بوابات | `git diff master --stat -- src/app/agents/rag_agent src/app/modules/knowledge` فارغ؛ البوابات في §2. (`scan.sh` غير مشغّل: يحتاج Docker/الشبكة ولا يخص هذا الفرع، وهو شرط عند الدمج) | ✅ (بالقيد المذكور) |

مراجعة الأوراكل (الطلب الخاص): قارنتُ كل أمثلة ملحق أ في `cases/identifiers.toml` بنص SRS يدوياً، لا بمخرجات المحلل. كل مثال موجود، وأنواعها واصطلاحاتها وأدوار الأقطاب (`A1`/`A2` ملف، `13` فرعي NO، `2` قطب رئيسي) وعائلات S7 (`E0.0`/`A4.7`/`PEW256` ألمانية، `V100.0`/`AIW16` S7‑200) تطابق SRS. تطابق القيمة المتوقعة المنصوص عليها في SRS؛ الاستثناء الوحيد ملاحظة B‑2 (النمط الفرعي لـ`W11`، وهو خيار تصميم لا ينص عليه SRS). كذلك أُجري اختبار طفرات في نسخة معزولة (خارج المستودع): تغيير `KM`، وتغيير دور `95`، وإزالة NFKC، وجعل `valid` دائماً صحيحاً، أسقطت كلها اختبارات موجودة (أي أن الأوراكل ليس ذاتي التحقق).

## 2. نتائج بوابات المشروع
```
$ .venv/bin/ruff format --check .
897 files already formatted
$ .venv/bin/ruff check .
All checks passed!
$ .venv/bin/mypy src
Success: no issues found in 503 source files
$ .venv/bin/lint-imports
Framework does not import outer layers KEPT
WireTwin sections are independent and shared is the floor KEPT
Contracts: 9 kept, 0 broken.
$ .venv/bin/pytest -rs tests/unit tests/architecture tests/eval
...
SKIPPED [1] tests/unit/test_public_file_links_containers.py:971: opt-in container harness: set RUN_EDGE_FILES_TEST=1 (about a minute)
7219 passed, 19 skipped, 7 warnings in 210.97s (0:03:30)
$ deploy/prometheus/test-rules.sh
  SUCCESS: 29 rules found
test-rules: OK
```
ملاحظات: (1) لم تُشغَّل `tests/integration` (المكدّس الحيّ مستعمل، وCLAUDE.md يمنعها دون موافقة)؛ لا شيء منها يخص `shared`. (2) الـ19 تخطياً كلها «opt-in container harness» (`RUN_EDGE_FILES_TEST=1`) لا تخص هذا الفرع. (3) لم تُضف أي مرشحات. (4) اختبارات QA الجديدة: 26 حالة في `tests/unit/test_electrical_shared_robustness.py`.

## 3. الاختبار الاستكشافي
مُشغَّل على المحلل مباشرة (لا تطبيق ولا واجهة في هذا الفرع: نواة بلا I/O).

| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| فارغ وفراغات | `parse("")`، `"   "`، `"\n"` | `None` | `None` | ✅ |
| نص طويل (>200) | `"1"*201`، `"=" + "A"*199` | `None` (نثر) والحد 200 مقبول | كما المتوقع | ✅ |
| ReDoS | 19 مدخلاً عدائياً (تكرار `=A1`، `1~`، `1.1`، `1/`، `L1-`، أرقام 5000…) على أنماط TOML | أقل من ثانية، بلا استثناء | أقل من 0.1 ms لكل مدخل؛ أكبر مدخل 9000 محرف يرجع فوراً بالحد | ✅ |
| كامل العرض/NFC | `ＫＭ１`، `XT1：５`، `Ｌ１－５`، `％Ｉ０．０`، `L1–5`، `L1−5` | مطابقة الشكل ASCII | مطابق، والأصل محفوظ | ✅ |
| محارف غير مرئية | ZWSP، NBSP، BOM، tab/newline داخل المعرّف | تُسقط في الشكل المطبّع | `KM1` | ✅ |
| NUL داخل النص | `-K5\x00` | معرّف مُعلَّم أو غير معرّف | `None` (يسقط دون علم) | ⚠️ ملاحظة B‑4 |
| أحرف مشابهة من سكربت آخر | سيريلي/يوناني `КМ1`، `ΚΜ1`، `-К5` | لا يُقرأ لاتينياً | `None` | ✅ |
| أرقام عربية‑هندية | `X١:١٤`، `-X1:١٤`، `KM١`، `I٠.٠`، `W١١` | سلوك موحّد (طيّها إلى ASCII أو رفضها كلها) | غير متسق: `X١:١٤` → `None`؛ `KM١` ينجح بمفتاح بحث `١KM`؛ `I٠.٠` يضيف مفتاح `I0.0`؛ `W١١` سلك | 🔴 B‑1 (minor) |
| غير الصالح لا يُسقط | `-K5:`، `KM1:`، `X1.`، `1XT-`، `I0.8`، `DB10.DBX2.8`، `-K5:13:` | `valid=False` مع سبب | كما المتوقع | ✅ |
| الحتمية وعدم تغير النتيجة | تشغيل مرتين، ومحلل جديد، وترتيب عشوائي لـ171 حالة، و3000 نص عشوائي Unicode | نتائج متطابقة، وتحليل الشكل المطبّع يعطي النتيجة نفسها | متطابقة؛ لا استثناء | ✅ |
| غموض موثَّق | `12.3`، `13`، `95`، `M`، `M1`، `M10.1` بلا تلميح وبتلميح | ترتيب ثابت وتلميح يبدّل | كما في الحالات c128..c137 | ✅ |
| أسماء قواعد مجهولة | `RuleSelection(wire_number_patterns=("(a+)+$",))` | `RuleError`، دون ترجمة regex من المستخدم | `RuleError` | ✅ |
| مفتاح بحث للجهد `L-` | `parse("L-")` | مفتاح غير مفرط العمومية | `search_keys = ("L-", "L")` | ⚠️ B‑3 (minor) |
| رموز الأعطال/البارامترات | `F07801`، `F 0002`، `E-024`، `P1204` | FR‑ID‑11 هو S وخارج `INC1‑AC‑4` | `None` (لا مُحلِّل) و`21.03` يُقرأ مرجعاً متقاطعاً | ملاحظة: خارج النطاق، لا حكم |
| RTL/375px/شبكة/صلاحيات/مستأجر آخر/ضغط مزدوج | — | لا ينطبق | النواة نقية بلا واجهة ولا I/O ولا هوية | غير منطبق |

## 4. الأخطاء المكتشفة
لا blocking ولا major. أخطاء minor يُقترح توجيهها إلى **backend** (المالك: مطوّر `shared`)، ويجوز إرجاؤها إلى ما قبل PR‑C/المؤشّر.

| # | الوصف | الشدة | خطوات إعادة الإنتاج (مدخل، متوقع، فعلي، `ملف:سطر`) | الحالة |
|---|---|---|---|---|
| B‑1 | الأرقام العربية‑الهندية غير مطوية في التطبيع، والأنماط تستعمل `\d` (يقبل Unicode) فتتصرف الأنواع بشكل غير متسق. | minor | `parse("X١:١٤")` يعطي `None` (متوقع: معرّف طرف مطبّع `X1:14`، أو علَم)، بينما `parse("KM١")` يعطي `letter_code` بـ`canonical="KM١"` ولا يطابق `KM1` في البحث؛ `parse("I٠.٠")` plc بمفتاح `I0.0`. السبب: `normalization.py` `canonical_form` (حوالي السطر 27) لا يطوي الأرقام، و`letter_codes.py:~57` وسائر الأنماط تستعمل `\d` بدل `[0-9]`. أهمية أعلى مع الأسئلة العربية لاحقاً (FR‑NLU)؛ ولا يخص SRS ملحق أ الحالي. | مفتوح |
| B‑2 | `W11` (من ملحق أ مع `U11`/`V11` كجهد‑مبني) يُصنَّف `sequential` لا `voltage_based` لأن `sequential` تسبقه في الملف. النوع `wire_number` صحيح ولا خطأ في الكشف. | minor | `parse("W11").variant` متوقع (قراءة ملحق أ) `voltage_based` أو توثيق الغموض؛ فعلي `sequential` (الحالة c040 تثبّت ذلك). `rules/identifiers.toml` `[wire_numbers]` الترتيب. يُقبل إن كان `variant` للعرض فقط؛ يُوثَّق. | مفتوح (قرار تصميم) |
| B‑3 | مفتاح البحث للجهد `L-` هو `L` (إزالة الفواصل تحذف `-` دون `+`)، فيصير مفتاحاً عاماً جداً قد يطابق أي `L`. | minor | `parse("L-").forms.search_keys == ("L-", "L")` بينما `parse("L+")` → `("L+",)`. `normalization.py:~58` `_SEPARATORS`/`compact`. | مفتوح |
| B‑4 | نص فيه محرف تحكم (`\x00`) داخل معرّف صالح يسقط بصمت (`None`) بدل أن يُعلَّم؛ هامشي لأن المصدر النصي من PDF. | minor | `parse("-K5\x00")` → `None`. متوقع: إما تنظيف المحرف أو `valid=False`. `normalization.py` `canonical_form` (يزيل `Cf` فقط لا `Cc`). | مفتوح |

ملاحظات على الانحرافات التي أعلنها المطوّر (§ المهمة):
1. `rules.py` مقابل حزمة `rules/`: مقبول. الـLLD نفسه متناقض (B15 يذكر `rules` ملفاً وB16 يذكر الحزمة `rules/__init__.py`)، ولا يتعايش الاثنان؛ الحزمة هي الصحيحة وتحمل ملف TOML بجوارها.
2. الحقول الإضافية (`variant`، `conventions`، `plc_families`، `connection_point_separator`): مقبولة، إضافية وبقيم افتراضية فلا تكسر البناء بحسب LLD.
3. دلالات `RuleSelection` (فارغ = بلا تقييد، الجهود إضافية، الاسم المجهول `RuleError`): مقبولة ومختبرة.
4. توسيع `voltage_based`: مقبول بل ضروري. نمط LLD الحرفي `^(L[1-3]|[UVW])\d{1,3}(-\d{1,3})?$` لا يطابق `L1-5` من ملحق أ (يطلب رقماً بعد `L1`)؛ مصدر الحقيقة هو SRS. `L1` وحده يبقى جهداً.
5. لا محمّل في `shared`: مقبول وهو ما ينص عليه LLD (`rule_files.py` في المحوّل لاحقاً، السطر 1531)؛ وهناك اختبار يمنع `open`.
6. تدوين الجسر `X1:1~X1:2`: مقبول كافتراض لأن SRS (FR‑ID‑03) يذكر الجسور دون صيغة؛ يُطلب تأكيد المالك عند أول عيّنات حقيقية (يُسجَّل في `follow-ups`).
7. حل الغموض بترتيب ثابت وتلميح: مقبول، موثّق في ترويسة `parser.py` ومثبت بحالات c128..c137.
8. صيغ المورّدين الآخرين (`C`): مقبول لأنها FR‑ID‑14 (C) وغير مطلوبة؛ ملاحظة: `%IX0.0` يُعلَّم `valid=False` رغم أنه عنوان IEC 61131‑3 صحيح، وهذا اختيار حذر لا خطأ.
9. مفاتيح البحث الإضافية: مقبولة، ومنها `STRIP:POINT` و`KM1`+`1KM` وتهجئات PLC. إلا أن FR‑IDX‑01 (يُسترجع `=A1+P2-K5:13` بـ`-K5` و`=A1` و`+P2`) يحتاج مفاتيح الجوانب منفصلة، وهي غير موجودة في `search_keys` (يوجد `K5` و`K5:13` فقط)؛ يمكن للمفهرس بناؤها من `aspects`، فيُراقَب عند INC‑2، ولا يُدخل في `INC1‑AC‑4`.

## 5. الحكم
**PASS** — البوابات الست خضراء (7219 ناجحاً، 19 تخطياً opt‑in لا يخص الفرع، `lint-imports` = 9 kept)، وكل ما في `INC1‑AC‑4` وحصة PR‑B من `INC1‑AC‑13` و`INC1‑AC‑16` له اختبار ناجح، والأوراكل مطابق لملحق أ في SRS، ولا أخطاء blocking أو major (أربعة minor مفتوحة).

## 6. إعادة 1 (بعد جولة المراجعة الأولى: `0cf9e41` و`6268d38`)

الإيداعات المفحوصة: `3b9ca75..6268d38`. `SHARED_VERSION` = 1.1.0، و188 حالة، وM‑04 = 1.0.

### 6.1 إغلاق أخطائي السابقة (بالمدخلات الأصلية)
| الخطأ | المدخل | الآن | الحالة |
|---|---|---|---|
| B‑1 | `X١:١٤`، `KM١`، `I٠.٠`، `X१:१४` (ديفاناغري)، `Ｘ１：１４` | `terminal X1:14`، `letter_code KM1`، `plc I0.0`؛ مفاتيح البحث مطابقة لنظيرتها ASCII | ✅ مغلق |
| B‑3 | `parse("L-")` | `search_keys == ("L-",)` (لا مفتاح `L`) | ✅ مغلق |
| B‑4 | `-K5\x00`، `-K5\x00:13`، `KM1\u200b` | `valid=False` و`text.hidden_character`، والأصل محفوظ؛ لا إسقاط صامت | ✅ مغلق |
| B‑2 | `W11` ← `sequential` | لم يُعدَّل عمداً (قرار تصميم، موثّق) | يبقى minor مفتوحاً |

اختبارات المتانة السابقة (`test_electrical_shared_robustness.py`، 26 حالة) خضراء بلا تعديل. اختبارات جديدة: `/home/AIZZAK/tests/unit/test_electrical_shared_rework1.py` (7 اختبارات خضراء + 3 `xfail(strict)` للخطأ N‑1).

### 6.2 الاستكشاف على الشيفرة الجديدة
| السيناريو | الفعلي | الحكم |
|---|---|---|
| توليد ~166 ألف مدخل من قواعد لغوية (كل الأنواع: أطراف، جسور، مراجع متقاطعة، أسلاك، جهود، PLC بكل التهجئات، تعيينات، أصفار بادئة) وفحص أي مفتاح بحث مشترك بين هويتين مختلفتين | بين الأطراف والجسور والمراجع المتقاطعة والأسلاك والجهود لا تصادم دلالي (بعد طيّ الأصفار البادئة والفاصل `.`/`-`/`:` في الطرف، وهما مقصودان). المفاتيح المشتركة المتبقية هي المقصودة فقط: `-K5`/`K5`، `KM1`/`1KM`، `%I0.0`/`I0.0`/`E0.0`، ومفتاح الجهاز لـ`-K5:13` | ✅ |
| هل يعيد مفتاح `_TAG` فتح التصادم؟ | لا: لا يُنتج إلا لحرف+فاصل+أرقام كاملة، وأي نوع من الأنواع التسعة لا يحلّل هذا الشكل (`F-0001`، `K-5`، `X-1`، `KM_1` كلها `None`)، فالمفتاح خامل عبر `parse` ويعمل عبر `normalize` فقط (T‑21 مغطى في الاختبار على `normalize`). لا خطر تصادم، لكن لا اختبار على مستوى المحلل لـT‑21 إلى حين وجود مُحلِّل رموز الأعطال (S) | ✅ (ملاحظة) |
| طيّ الأرقام لكل Nd | مسح كل نقاط Nd غير ASCII (أكثر من 400) على `KM`+d و`X1:`+d و`12.`+d، وعيّنة موزعة منها على PLC: تتطابق مع ASCII | ✅ |
| أشباه الأرقام (`²`، `①`، `⅕`، `一`) | لا تُطوى إلى أرقام Nd؛ لا استثناء | ✅ |
| محارف مخفية/ثنائية الاتجاه | `U+2800`، `U+FE0F`، وسوم `E0041`، `U+180E`، `U+00AD`، `U+202E`/`202B`، مع الجمع بينهما: التعليم صحيح (مخفي = غير صالح، bidi = يبقى صالحاً) | ✅ |
| علامات الدمج (`KM1\u0301`، `K\u0301M1`) | `None` (لا معرّف) دون علم؛ انحراف أعلنه المطوّر؛ مثبّت باختبار | ⚠️ ملاحظة (minor) |
| `I0.9999` ← `None` | `I0.8` و`I0.99` و`I0.999` تُعلَّم `plc.bit_out_of_range`، و`I0.9999`/`DB1.DBX0.9999`/`M0.9999` لا شيء. الحكم: مقبول لا يخالف FR‑ID‑10 في روحه (نص لا يتبع شكل العنوان ولا يشبه عنواناً مبتوراً؛ الحد ثلاث خانات يمنع `int()` الضخم، وهو مغطى باختبار S‑4)، لكن الحد اعتباطي: سلوك الخانة الرابعة يختلف عن الثالثة. يُقترح توثيقه | ⚠️ ملاحظة (minor، مقبول) |
| فحص يدوي لـ46 مدخلاً أعيد اشتقاقه | قارنتُ فرق `identifiers.toml` بيدي: كل التغييرات هي حذف المفاتيح بلا فاصل (`X114`، `K513`، `123`، `XT15`، `L15`، `=A1K5`…) وإبقاء المفاتيح بفواصلها؛ لا قيمة أخرى تغيّرت (النوع، الاصطلاح، الجوانب، `canonical`)، وأمثلة ملحق أ (SRS) كما هي: `X1:14`، `-K5:13`، `12.3`، `L1-5`… والإضافات c172..c188 تطابق ما يلزم من SRS (NFR‑I18N‑03: الأصل محفوظ) | ✅ |

### 6.3 أخطاء جديدة
| # | الوصف | الشدة | إعادة الإنتاج | المالك |
|---|---|---|---|---|
| N‑1 | مفتاح الجهاز في سلسلة جوانب فيها أكثر من `-` يؤخذ من آخر جانب `-`، فيصير رقماً عارياً يتصادم مع رقم سلك. | minor (استرجاع فقط، ولا تتغير الهوية `(kind, canonical)`) | `parse("-K5-13").forms.search_keys` = `('-K5-13','K5-13','13')` و`parse("13")` سلك بمفتاح `'13'`؛ كذلك `-X1-5` ← `'5'`، `-Q1-3` ← `'3'`، `=A1-K5-3` ← `'3'` (ويفقد `K5`). المتوقع: مفتاح الجهاز `K5` لا الرقم. `reference_designation.py:~88` (`product = next(... reversed(chain.aspects) ...)`). اختبار `xfail(strict)`: `test_qa_n1_a_hyphen_chain_does_not_leak_a_bare_number_key` (يتحول إلى فشل مُنبِّه عند الإصلاح فيُزال `xfail`). | backend |
| N‑2 | `-X1.14` (نقطة) يصنَّف تعيين مرجع بمفاتيح `X1.14` و`X1`، بينما `X1.14` و`-X1:14` طرفان بمفتاح `X1:14`؛ فلا يلتقي `-X1.14` مع `X1:14` في البحث (غياب لا تصادم). | minor | `parse("-X1.14")` ← `reference_designation`، `search_keys=('-X1.14','X1.14','X1')`، لا `X1:14`. `terminals.py` يسبق التعيين لكن `-X1.14` لا يطابق نمط الطرف؛ يُقرّر المالك إن كان مقصوداً. | backend |

### 6.4 البوابات (إعادة 1)
```
$ .venv/bin/ruff format --check .
900 files already formatted
$ .venv/bin/ruff check .
All checks passed!
$ .venv/bin/mypy src
Success: no issues found in 504 source files
$ .venv/bin/lint-imports
Framework does not import outer layers KEPT
WireTwin sections are independent and shared is the floor KEPT
Contracts: 9 kept, 0 broken.
$ .venv/bin/pytest -rs tests/unit tests/architecture tests/eval     (مرتان)
التشغيل 1: 7345 passed, 19 skipped, 3 xfailed, 7 warnings in 222.15s (0:03:42)
التشغيل 2: 7345 passed, 19 skipped, 3 xfailed, 7 warnings in 216.36s (0:03:36)
$ deploy/prometheus/test-rules.sh
  SUCCESS: 29 rules found
test-rules: OK
```
لا فشل متقطع في التشغيلين (لا FAILED ولا ERROR). الـ19 تخطياً هي نفسها (`RUN_EDGE_FILES_TEST=1`). الـ3 `xfailed` هي N‑1. ملاحظة: اشتُغل التشغيلان قبل تحويل المحارف غير ASCII في ملف الاختبار الجديد إلى تسلسلات `\u` (لتجاوز RUF001)؛ أُعيد تشغيل الملف وحده بعدها (7 passed, 3 xfailed) والبوابات الثلاث الثابتة (format/check/mypy) أعيدت بعده.

### 6.5 الحكم بعد إعادة 1
**PASS** — البوابات الست خضراء مرتين بلا تقطع، B‑1 وB‑3 وB‑4 مغلقة بمدخلاتها الأصلية، وكل أمثلة ملحق أ ما زالت مطابقة للـSRS يدوياً. لا blocking ولا major؛ minor مفتوحة: B‑2 (قرار)، N‑1، N‑2، وملاحظتا علامات الدمج و`I0.9999`.

</div>
