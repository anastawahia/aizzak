<div dir="rtl">

# قبول مالك المنتج: F2 البند 1، وسوم KKS وأرقام أجهزة ANSI وأطراف `1X-25` وحقول ملف الإعداد

- **الفرع والنطاق:** `feat/electrical-drawings-f2-rules`، و`HEAD` = `d859733`، والتغيير `git diff master...HEAD`: 10 إيداعات، و32 ملفاً (+4022/−83).
- **المدخلات:** `stories.md` (`F2‑AC‑1..43`، ومنها `F2‑AC‑15` و`F2‑AC‑20` بعد تعديلهما في الإعادة 1)، و`design.md` §5.1 و§11 و§4.1، و`test-plan.md` (PASS، ومعه §6 للإعادة 1)، و`review.md` (إعادة المراجعة 1: PASS)، و`security-review.md` (PASS)، و`status.md` (الصفوف 7ي..9ي‴ وسجل القرارات).
- **المستودع عام:** لا وسم حقيقي ولا اسم ملف من العيّنات هنا. والأمثلة المذكورة كلها مخترعة من `stories.md`، أو داخلة في استثناء `F2‑AC‑20`. ولم أفتح مجلد العيّنات.
- **معنى الحكم:** يُحكم هنا على ما يخصّ البند 1 في التصميم: US‑1..4 وUS‑10 وUS‑9، أي `F2‑AC‑1..22` و`41..43`، وNFR‑F2‑1..7. أما `F2‑AC‑23..40` (US‑5..8) فخارج النطاق بنص `stories.md` الجزء الثاني و`design.md` §8، فلا تُرفض.

## ما تحققتُ منه بنفسي

1. **اختبارات F2 على شجرة العمل الحالية**، بأوامر قراءة فقط ودون خدمات حية. شغّلتُ 11 ملفاً: `test_electrical_shared_kks.py` و`_ansi.py` و`_parser.py` و`_rules.py` و`_terminals_wires.py`، و`test_electrical_f2_profile.py` و`test_electrical_f2_qa.py` و`test_electrical_f2_rework1_qa.py`، و`test_api_electrical_router.py` و`test_api_rbac.py` و`test_api_conventions.py` (اختبار العقد). **النتيجة: 852 اختباراً، و0 فشل، و0 خطأ، و0 تخطٍّ، ورمز الخروج 0.**
2. **`tests/architecture`:** رمز الخروج 0. و`tests/eval` لا يجمع اختبارات (رمز 5)، وعمله في الأداة أدناه.
3. **M‑04:** `python -m tests.eval.electrical.m04_identifiers --out <المجلد المؤقت>` يعطي `M-04 = 1.0000 over 236 cases`، أي 192 حالة قائمة و44 حالة F2، ومنها `c236` من الإعادة 1. وفيها 87 حالة `appendix-a`. وفرق `cases/identifiers.toml` عن `master` إضافات فقط، بلا سطر محذوف.
4. **البوابات السريعة على شجرة العمل:** `ruff format --check` (994 ملفاً) ✅، و`ruff check` ✅، و`lint-imports` («9 kept, 0 broken») ✅. ولم يمسّ الفرق `.importlinter` ولا `tests/architecture/` ولا `test_api_rbac.py`.
5. **NFR‑F2‑4:** قِسته بنفسي، والتفصيل في قسمه أدناه.
6. **`F2‑AC‑21` و`F2‑AC‑22`:** فحصتُ إيداع جولة الوثائق `29126d3` على `master`.
7. **ملاحظة على شجرة العمل:** أثناء المراجعة ظهر تعديلان غير مودَعين لم أكتبهما، في `src/app/modules/electrical/drawings/domain/profile_detection.py:84` (تعليق) و`tests/unit/test_electrical_shared_rules.py` (حذف سطرين ميتين). وهما إصلاحا ر‑6 ور‑5 من `review.md` حرفاً، ولا يغيّران سلوكاً. وشغّلتُ الاختبارات والبوابات أعلاه بعد ظهورهما، فكانت كلها خضراء.

## المعايير مقابل الدليل

| AC | الأولوية | الدليل (اختبار، أو مخرج، أو قسم مراجعة) | الحكم |
|---|---|---|---|
| F2‑AC‑1 | Must | `tests/unit/test_electrical_shared_kks.py:40` `test_f2_ac_1_an_equipment_tag_has_its_levels_and_parts`، ويؤكد النوع والصنف والصلاحية والجزأين بمستوييهما والشكل القانوني (`test-plan.md` §1) | ✅ |
| F2‑AC‑2 | Must | `test_electrical_shared_kks.py:67` `test_f2_ac_2_each_packaged_form_has_its_class` (الصيغ الخمس)، ومعه `test_electrical_f2_qa.py::test_a_four_digit_cable_suffix_is_valid_after_a_system_or_an_equipment_tag` | ✅ |
| F2‑AC‑3 | Must | `test_electrical_shared_kks.py:77` `test_f2_ac_3_spaces_case_and_full_width_meet_in_one_canonical_form`، ومعه حالات QA للمسافة الصلبة والجدولة والأرقام كاملة العرض وعلامات الاتجاه | ✅ |
| F2‑AC‑4 | Must | `test_electrical_shared_kks.py:87` `test_f2_ac_4_a_near_miss_is_flagged_malformed_never_dropped`، ويؤكد `kks.malformed` على `20XY10AB123` و`20XYZ10AB12`. وحالة الكابل بطول لاحقة خاطئ تعود بلا معرّف (خطأ QA الصغير 1)، ولا تخالف نص المعيار: المثالان المذكوران فيه يُوسمان، ولا وسم صالح يُسقط ولا يُنتج صالح خاطئ (`test_electrical_f2_qa.py:126`) | ✅ |
| F2‑AC‑5 | Must | `test_electrical_shared_kks.py:94` `test_f2_ac_5_a_letter_o_in_a_digit_position_is_named_not_changed`، ومعه `:190` `test_f2_ac_5_both_confusions_together_name_both_reasons` (6 حالات) وحالة M‑04 `c236` (الملاحظة 4 في المراجعة، مغلقة) | ✅ |
| F2‑AC‑6 | Must | `test_electrical_shared_kks.py:108` `test_f2_ac_6_a_prefixed_tag_is_kks_only_when_enabled_and_complete`، ومعه `test_prefixed_tags_are_never_kks_while_the_feature_is_off` في ملف QA | ✅ |
| F2‑AC‑7 | Must | `tests/unit/test_electrical_shared_parser.py:179` `test_f2_ac_7_kks_on_or_off_changes_no_existing_case`، و`:191` `test_f2_ac_7_ansi_on_changes_only_the_listed_ambiguities`. والحالات الـ192 لم تُمسّ (الفرق إضافات فقط، البند 3 أعلاه) | ✅ |
| F2‑AC‑8 | Must | `tests/unit/test_electrical_shared_ansi.py:43` `test_f2_ac_8_numbers_suffixes_instances_and_classes` (`87T` و`50BF` و`51N` و`27-1` بالرقم واللاحقة والمثيل والفئة) | ✅ |
| F2‑AC‑9 | Must | `test_electrical_shared_ansi.py:66` `test_f2_ac_9_a_bare_number_is_a_wire_unless_the_caller_has_context`: `52` رقم سلك ببديل، ومع التلميح جهاز، و`needs_context` صادق في الحالتين (`design.md` §5.1) | ✅ |
| F2‑AC‑10 | Must | `test_electrical_shared_ansi.py:80` `test_f2_ac_10_a_compound_against_a_cross_reference` (الصيغ الثلاث)، ومعه `test_the_pinned_cross_reference_format_decides_50_slash_51` في ملف QA | ✅ |
| F2‑AC‑11 | Must | `test_electrical_shared_ansi.py:118` `test_f2_ac_11_unknown_suffix_profile_suffix_and_out_of_range`: `87QZ` غير صالح ثم صالح بلاحقة الملف، و`00` و`100T` ليسا جهازاً. وانحراف `00`/`05` رقم سلك مقبول في المراجعة (الانحراف 1) | ✅ |
| F2‑AC‑12 | Must | `test_electrical_shared_ansi.py:148` و`tests/unit/test_electrical_shared_rules.py:167` `test_f2_ac_12_every_ansi_class_is_a_class_name`، و`:210` `test_the_kks_and_ansi_tables_refuse_unknown_keys`. وراجع المراجع الترخيص (`review.md`، «الترخيص») | ✅ |
| F2‑AC‑13 | Must | `tests/unit/test_electrical_f2_profile.py:130` `test_f2_ac_13_one_profile_reads_iec_kks_and_ansi_together`، ومعه `test_the_selection_adds_kks_and_ansi_beside_exactly_one_device_convention` في ملف QA | ✅ |
| F2‑AC‑14 | Must | `test_electrical_f2_profile.py:200` `test_f2_ac_14_kks_and_ansi_are_proposed_and_not_mixed`: 25 وسم KKS و22 رقم ANSI بلاحقة و30 طرف IEC، فيُقترح الحقلان بثقة 0.25 و0.22، ولا خلط. و`:218` `test_f2_ac_14_iec_and_chinese_stay_mixed`. والكيس الجديد بعد الإعادة في `test-plan.md` §6.1 | ✅ |
| F2‑AC‑15 (معدَّل) | Must | `test_electrical_f2_profile.py:227` `test_f2_ac_15_under_twenty_tags_kks_is_not_proposed`: مع 19 وسماً يبقى الحقل `none` بثقة 1.0، والثقة العامة كالأساس، ولا `systems`. ومع 20 يُقترح بثقة 0.2. ومعه `test_electrical_f2_rework1_qa.py:57` `test_the_thresholds_are_per_field_and_exact`، و`test_electrical_f2_profile.py:261` لـANSI | ✅ |
| F2‑AC‑16 | Must | `tests/unit/test_api_electrical_router.py:433` `test_f2_ac_16_the_new_fields_are_saved_validated_and_defaulted`: الحفظ إصداراً، و422 `electrical.invalid_profile` باسم الحقل بلا صدى، والجسد القديم بالافتراض `none`. ومعه `test_electrical_f2_qa.py:607` و`test_electrical_f2_rework1_qa.py:127` و`:132` لطيّ `ansi_suffixes` ورفض ما لا يُطوى | ✅ |
| F2‑AC‑17 | Must | `test_api_electrical_router.py:991` (`member`) و`:1015` (`viewer`): PUT بـ`plant_designation` يعطي 403 `authz.forbidden`، والإصدار يبقى 1 في الاختبارين (S‑5 مغلقة). وصفوف `EXPECTED` في `test_api_rbac.py` لم تتغيّر | ✅ |
| F2‑AC‑18 | Must | `test_api_electrical_router.py:1039` `test_the_profile_body_description_names_every_profile_field`، واختبار العقد `test_api_conventions.py:288` (ناجح)، و`test_electrical_shared_rules.py:36` (الإصداران `1.1.0` و`1.3.0`)، و`test_electrical_f2_profile.py:304` `test_f2_ac_18_the_release_fingerprint_moves_with_the_versions`. وبصمة الملف القديم كما على `master` (`test_electrical_f2_qa.py:415`) | ✅ |
| F2‑AC‑19 | Must | 87 حالة `appendix-a` في `tests/eval/electrical/cases/identifiers.toml`، و`test_electrical_shared_parser.py:80` `test_every_appendix_a_example_is_in_the_case_set`، و`:57`. وتشغيلي للأداة: M‑04 = 1.0000 على 236 حالة. والحالة الفاشلة تُفشل هذا الاختبار في CI. **صفّ FR‑ID‑17 (التسمية المكدّسة) خارج النطاق** بنص المعيار نفسه، ويأتي مع US‑6 في INC‑2 | ✅ |
| F2‑AC‑20 (معدَّل) | Must (يدوي) | فحص المراجع بالقائمة الحقيقية مرتين (`review.md`، «F2‑AC‑20» في الجولة الأولى و«F2‑AC‑20 على فرق الإعادة»): لا تطابق بجزء من نص، والتطابقات بكلمة كاملة كلها داخل الاستثناء المكتوب: `1X-25`، ورمز التيار والأقطاب، واختصار ثلاثي الطور، وتسمية الجهاز من حرف ورقم، ومثال طرف `F2‑AC‑13`. **وتسميات الجهد:** صنف في الاستثناء، ولم يُسجَّل لها تطابق. والاستثناء بقرارَي المالك في 2026-11-09 (`status.md`، سجل القرارات). وما أُضيف بعد الفحص (`d859733` والتعديلان غير المودَعين) شمله فحص الإعادة، أو هو حذف سطور وتعليق بلا وسم. ولم أُدخل رمزاً جديداً في هذه الوثيقة | ✅ |
| F2‑AC‑21 | Must | جولة الوثائق `29126d3` على `master`: SRS 0.20 في `docs/delivery/electrical-drawings/docs/requirements.md` (الرأس، و«`D-01..D-36`» بشرطة SRS العادية)، وصفّ `D-36` واحد (`:1188`). وفي فرق `29126d3` لا سطر محذوف من صفوف FR‑ID‑01..02 و04..12 ولا FR‑VEC‑04 ولا FR‑SYM‑12 (0)، وصفّ FR‑ID‑03 تغيّر مرة واحدة بـA‑5. ولا يمسّ هذا الفرع `docs/delivery/electrical-drawings/docs/`. ومعه `design.md` §10.4 | ✅ |
| F2‑AC‑22 | Must | `git show --stat 29126d3`: ستة ملفات، كلها داخل `docs/delivery/electrical-drawings/`. وفحص RTL والأمثلة المخترعة في `design.md` §10.4 | ✅ |
| F2‑AC‑41 | Must | `tests/unit/test_electrical_shared_terminals_wires.py:133` `test_f2_ac_41_a_digits_first_x_strip_reads_under_iec_and_gbt` | ✅ |
| F2‑AC‑42 | Must | `test_electrical_shared_terminals_wires.py:145` `test_f2_ac_42_the_chinese_reading_and_the_xt_rule_stay` (دون تضييق، وIEC وحده، وNFPA وحده، و`1XT-5`) | ✅ |
| F2‑AC‑43 | Must | `test_electrical_shared_rules.py:216` `test_f2_ac_43_digits_first_conventions_are_data` (بلا الجدول، والرمز المجهول، والاصطلاح المجهول، والقائمة الفارغة، وتعديل الجدول وحده) | ✅ |

### خارج نطاق البند 1، وليست مرفوضة

| AC | القصة | المرجع |
|---|---|---|
| F2‑AC‑23..26 | US‑5، تصحيح `0`/`O` | `stories.md` الجزء الثاني (INC‑2)، و`design.md` §8 البند 3. وفي البند 1 تُسمّى أسباب عدم الصلاحية فقط (`F2‑AC‑5`) |
| F2‑AC‑27..31 | US‑6، التسمية المكدّسة (حالات هندسية) | `design.md` §8 البند 2، و`F2‑AC‑19` نفسه يحيل صفّ FR‑ID‑17 إلى INC‑2 |
| F2‑AC‑32..37 | US‑7، الأسطورة | `design.md` §8 البند 4. وفي البند 1 حدّ `ansi_suffixes` (64) وحده |
| F2‑AC‑38..40 | US‑8، القياس على F3 | `design.md` §8 البند 6، وينتظر الحقيقة المرجعية من F3 |

## المتطلبات غير الوظيفية

| NFR | الدليل | الحكم |
|---|---|---|
| NFR‑F2‑1 الحتمية | `test_electrical_shared_kks.py:170` `test_nfr_f2_1_every_f2_case_parses_twice_the_same`، ومعه اختبارات QA للمحلّل الجديد ولترتيب الرموز في `tally` | ✅ |
| NFR‑F2‑2 القواعد بيانات | `test_electrical_shared_rules.py:263` و`:290` `test_the_evidence_rules_are_data`، و`F2‑AC‑43`. والحدّ المعروف أن `_OUTER` ثابت في الشيفرة (الملاحظة 8، مؤجَّلة) | ✅ |
| NFR‑F2‑3 لا نص مستخدم في تعبير | `review.md` «NFR‑F2‑3»، و`security-review.md` §2، واختبارات رفض المحارف الخاصة في ملفَي QA | ✅ |
| NFR‑F2‑4 الأداء | القسم التالي | ✅ |
| NFR‑F2‑5 حدود الأسطورة | الأسطورة في INC‑2. وحدّ 64 لاحقة في البند 1: `test_electrical_f2_rework1_qa.py:138` | ✅ (في النطاق) |
| NFR‑F2‑6 التراخيص | `F2‑AC‑12`، ومراجعة الترخيص في `review.md` | ✅ |
| NFR‑F2‑7 الخصوصية | `F2‑AC‑20` | ✅ |

## NFR‑F2‑4: الأداء

- **الحدّ المكتوب:** متوسط زمن `parse` على حالات M‑04 لا يتجاوز 1.2 ضعف `master` (`requirements.md` NFR‑F2‑4، و`design.md` §5.1). أما `tally` فـ«يُقاس ويُسجَّل» فقط (§7 خ‑3).
- **الأرقام المسجَّلة قبلي:**

| القائس | `parse` بالقواعد المعبّأة | `parse` بتفعيل KKS وANSI | `tally` (للتسجيل) |
|---|---|---|---|
| المطوّر (9ي″) | 1.09× | 1.14× | 1.59× |
| QA (`test-plan.md` §6.4) | 1.05× | 1.08× | 2.1× |
| المراجع (`review.md`) | 1.07× | — | نحو 1.9× |

- **قياسي (2026-11-09):** سكربت في المجلد المؤقت خارج المستودع، بـ`python -I`، على نسخة من `master` (`git archive`) ثم على الفرع، بالتناوب مرتين، على المضيف نفسه. و`parse` أقل متوسط من 7 جولات × 20 تكراراً على نصوص الحالات الـ192 من `master`. و`tally` على 1,000 رمز اصطناعي ببذرة ثابتة، ثلاثة أرباعها تبدأ برقم أو `=` أو `+`، فيمرّ أكثرها بالمسبار:

| القياس | `master` | الفرع | النسبة |
|---|---|---|---|
| `parse`، القواعد المعبّأة | 14.61 / 14.62 µs | 15.50 / 15.70 µs | **1.06× / 1.07×** |
| `parse`، كل الاصطلاحات مع KKS وANSI | — | 16.26 / 16.61 µs | **1.11× / 1.14×** (نسبة إلى `master` المعبّأ) |
| `tally`، 1,000 رمز | 12.9 / 13.5 ms | 31.1 .. 32.5 ms | نحو **2.4×** (للتسجيل فقط) |

- **الحكم: محقَّق.** أعلى نسبة لـ`parse` عند أي قائس 1.14×، وهي دون الحدّ 1.2.
- **و`tally`** تتبع نسبته حصة الرموز التي تبدأ برقم في الخليط: من 1.59× إلى 2.4×. والفرق المطلق نحو 18 ms لكل 1,000 رمز، مرة لكل وثيقة عند الاستيعاب. وقد توقّعه التصميم (خ‑3)، وطُبّق علاجه (قصر المسبار). ولا حدّ مكتوب عليه، فلا يحجب. ويُسجَّل في جسد الـPR مع وصف الخليط (متابعة 4).

## المراجعات

- **مراجعة الشيفرة (`review.md`):** FAIL في الجولة الأولى (ح‑1 وح‑2)، ثم **PASS في إعادة المراجعة 1**. أُغلقت الملاحظات 1 و2 و4 و5 و6 و7 و9 و10. وأُغلقت 3 جزئياً، والباقي منها نسخ §4.1 إلى جسد الـPR. وأُجّلت 8 و11 بالتصميم (§11.8). وملاحظات الإعادة: ر‑1 حسمه المالك، ور‑3 أُصلح قبل `d859733`، ور‑4 في `design.md` §11.6 البند 7، ور‑5 ور‑6 في شجرة العمل غير مودَعين (البند 7 أعلاه). **ويبقى ر‑2:** إيداع الوثائق قبل الـPR.
- **المراجعة الأمنية (`security-review.md`):** **PASS**، بلا حرجة ولا عالية ولا متوسطة. وقد أُجريت على `bd05ee8`، قبل الإعادة. ثم تحقق مراجع الشيفرة في إعادة المراجعة من إصلاح S‑1 وS‑4 وS‑5 في الشيفرة وباختبارات (`review.md`، «ملاحظات الأمن»). وحُسمت S‑3 بقاعدة واحدة، وS‑2 بإجراء §4.1. ولم تُعَد المراجعة الأمنية على `568bfea`، وأقبل ذلك بالاسم: الإعادة لم تُضف مساراً ولا استعلاماً ولا اعتمادية، وتغييرها في `_other_kind` قاعدة صحة تسمية لا ضابط أمن (`design.md` §11.9).

## الشروط قبل الدمج

1. **ر‑2:** إيداع وثائق `inc-1/f2/` على الفرع (`design.md` و`stories.md` و`review.md` و`security-review.md` وهذه الوثيقة) ومعها `status.md`. فـ`F2‑AC‑15` و`F2‑AC‑20` المعدَّلان و§11 و§4.1 غير مودَعة الآن.
2. **التعديلان غير المودَعين (ر‑5 ور‑6)** يُودَعان في إيداع واحد بعد البوابات الست، أو يُتركان، بقرار المنسّق. ولا يدخل `CLAUDE.md` ولا `docs/delivery/data-search-android/` في إيداعات هذا الفرع.
3. **جسد الـPR** كما في `design.md` §11.7 البند 11: §4.1 كما هو، وأرقام NFR‑F2‑4 بوصف خليط `tally`، وسطر عن استثناء `F2‑AC‑20` (قراراً المالك في 2026-11-09)، وسطر عن أن `F2‑AC‑15` صار «لا يُقترح KKS».

## ملاحظات غير حاجبة أقبلها بالاسم

- **خطأ QA الصغير 1 (`kks.py:46`، الشكل `_OUTER`):** وسم كابل بطول لاحقة خاطئ يعود بلا معرّف، لا `kks.malformed`. وهذا نص التصميم (§3.5 الخطوة 6، و§7 خ‑13). ولا يُسقط وسماً صالحاً ولا يصنع صالحاً خاطئاً. والقرار للمالك (المتابعة 3).
- **الملاحظة 8 (`_OUTER` ثابت):** مؤجَّلة إلى INC‑2 (خ‑13).
- **الملاحظة 11 (ضجيج ANSI غير الصالح حين يُفعَّل مع IEC):** مؤجَّلة إلى F3 مع M‑30 (خ‑12).
- **الانحرافات الثلاثة في الإعادة** (طيّ الجدولة وفاصل السطر في `ansi_suffixes`، واستبدال اختبار تسمية الجهد، والإيداع الواحد): قبلها المراجع.
- **S‑1..S‑5:** أُصلحت أو حُسمت، والمتبقي منها في المتابعات.
- **تناقض صغير في الوثائق:** `design.md` §11.5 ما زال يقول «حدّه: هذا الوسم وحده»، و§5.1 و`test-plan.md` §1 يذكران الاسم القديم لاختبار `F2‑AC‑15`. والنص الملزم هو `stories.md` وسجل القرارات، وهما محدَّثان (المتابعة 7).

## متابعات

| # | المتابعة | إلى | المرجع |
|---|---|---|---|
| 1 | الملاحظة 8: اشتقاق الشكل الخارجي لـKKS من الأقنعة مع أول صيغة مورد خارجه | INC‑2 | `design.md` §7 خ‑13، §11.8 |
| 2 | الملاحظة 11: قياس ضجيج ANSI غير الصالح على صفحات المورد الأول | F3، مع M‑30 | خ‑12، §8 البند 7 |
| 3 | خطأ QA الصغير 1 (`kks.py:46`): هل يُوسَّع `_OUTER` ليوسم لاحقة الكابل الخاطئة `kks.malformed`؟ | **قرار المالك** | `test-plan.md` §4 و§6.6 |
| 4 | ضبط قاعدة الدليل (`evidence_min_level`، و`non_evidence_suffixes`، وجدول الفئات) وحدّ 20 | F3، بعد M‑32 | خ‑4، خ‑11، §11.8 |
| 5 | كلفة `tally` (من 1.59× إلى 2.4× بحسب الخليط): تُسجَّل في جسد الـPR، وتُراجَع إن صار `tally` على مسار ساخن | INC‑2 | §7 خ‑3، والقسم أعلاه |
| 6 | القارئ المتسامح للجسد المخزَّن (S‑2)، والتحقق من الاقتراح عند التأكيد بلا جسد والقبول الآلي (خ‑14) | LLD الزيادة INC‑2 | §4.1، §8 البند 7 |
| 7 | تحديث `design.md` §11.5 بتوسيع الاستثناء (ر‑1)، واسم اختبار `F2‑AC‑15` في §5.1 و`test-plan.md` §1 | المنسّق، في إيداع الوثائق | `stories.md` `F2‑AC‑20` |
| 8 | نسخ §4.1 إلى `docs/deploy-server.md` أو دليل التشغيل | أول نشر خادم (قرار إنسان) | §11.8 |

## قرارات المالك، ولا تحجب الدمج

- المتابعة 3 وحدها: سلوك `_OUTER` للاحقة الكابل الخاطئة. والتصميم الحالي يعيد «لا معرّف»، وهو آمن.
- حُسم قبل هذا القبول: استثناء `1X-25` (م‑5)، وتوسيعه بالأصناف الخمسة (ر‑1)، وكلاهما في 2026-11-09.

## الحكم النهائي

**مقبول** — بالشروط الثلاثة قبل الدمج. كل معيار Must في نطاق البند 1 (`F2‑AC‑1..22` و`41..43`) ✅ بدليل مفتوح شغّلتُه أو فحصتُه، وNFR‑F2‑1..7 ✅، وNFR‑F2‑4 محقَّق (`parse` 1.14× أعلى ما قيس، والحدّ 1.2). و`review.md` PASS بعد الإعادة، و`security-review.md` PASS. ولا معيار مرفوض. و`F2‑AC‑23..40` خارج النطاق بمرجعها، لا مرفوضة.

</div>
