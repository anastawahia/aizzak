<div dir="rtl">

# خطة الاختبار: F2 البند 1، وسوم KKS وأرقام أجهزة ANSI وأطراف `1X-25` وحقول ملف الإعداد

- **الفرع:** `feat/electrical-drawings-f2-rules`، من `32f6b0a` إلى `a688b6a` (6 إيداعات فوق `master` عند `de82b80`).
- **النطاق:** US‑1..4 وUS‑10 (`F2‑AC‑1..20` و`41..43`) وNFR‑F2‑1..7. أما US‑5..8 فمن INC‑2، وUS‑9 تحقق منها في جولة الوثائق.
- **الأمثلة كلها مخترعة.** لم يُفتح مجلد العيّنات ولم يُنسخ منه وسم.
- **اختبارات المراجعة المضافة:** `tests/unit/test_electrical_f2_qa.py` (فقط، بلا مساس بالشيفرة).

## 1. مصفوفة التغطية

قرأتُ كل اختبار من اختبارات المطوّر، وتأكدتُ أنه يؤكد شرط المعيار نفسه (قيم الأجزاء والنوع والصلاحية والأسباب والبدائل)، لا أنه يعمل فحسب. وما يلي يضيف اختبارات المراجعة حيث وجدتُ حافة غير مثبّتة.

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| F2‑AC‑1 | وحدة | `test_electrical_shared_kks.py::test_f2_ac_1_an_equipment_tag_has_its_levels_and_parts` | ✅ |
| F2‑AC‑2 | وحدة | `…::test_f2_ac_2_each_packaged_form_has_its_class` (5 صيغ بأصنافها وأجزائها). ومراجعة: `test_electrical_f2_qa.py::test_a_four_digit_cable_suffix_is_valid_after_a_system_or_an_equipment_tag` | ✅ |
| F2‑AC‑3 | وحدة | `…::test_f2_ac_3_spaces_case_and_full_width_meet_in_one_canonical_form`. ومراجعة: `test_kks_spelling_variants_meet_in_one_canonical_form` (NBSP وtab وسطر جديد وأرقام كاملة العرض ومختلطة وعلامات الاتجاه) | ✅ |
| F2‑AC‑4 | وحدة | `…::test_f2_ac_4_a_near_miss_is_flagged_malformed_never_dropped`. ومراجعة: `test_a_third_level_with_a_wrong_count_is_malformed_not_dropped`، و`test_a_cable_suffix_of_the_wrong_length_is_never_a_valid_tag` | ✅ |
| F2‑AC‑5 | وحدة | `…::test_f2_ac_5_a_letter_o_in_a_digit_position_is_named_not_changed` (+ الصفر في موضع الحرف) | ✅ |
| F2‑AC‑6 | وحدة | `…::test_f2_ac_6_a_prefixed_tag_is_kks_only_when_enabled_and_complete`. ومراجعة: `test_prefixed_tags_are_never_kks_while_the_feature_is_off`، و`test_a_doubled_or_unknown_prefix_is_no_kks_tag` | ✅ |
| F2‑AC‑7 | وحدة وتقييم | `test_electrical_shared_parser.py::test_f2_ac_7_kks_on_or_off_changes_no_existing_case` و`…ansi_on_changes_only_the_listed_ambiguities`. وتحققتُ أن الحالات الـ192 القائمة لم تُمسّ في `identifiers.toml` (الفرق إضافات فقط). ومراجعة: `test_enabling_kks_does_not_touch_a_plain_iec_terminal_or_designation`، و`test_ansi_off_leaves_every_slash_text_a_cross_reference_as_before` | ✅ |
| F2‑AC‑8 | وحدة | `test_electrical_shared_ansi.py::test_f2_ac_8_numbers_suffixes_instances_and_classes`. ومراجعة: لواحق من 1..3 حروف، والمثيل 1..99 دون صفر بادئ، والحروف الكبيرة والمسافة | ✅ |
| F2‑AC‑9 | وحدة | `…::test_f2_ac_9_a_bare_number_is_a_wire_unless_the_caller_has_context`. ومراجعة: `test_ansi_bounds_are_inclusive_with_context` (1 و9 و10 و52 و99 بسياق وبلاه) | ✅ |
| F2‑AC‑10 | وحدة | `…::test_f2_ac_10_a_compound_against_a_cross_reference` (الصيغ الثلاث). ومراجعة: `test_the_pinned_cross_reference_format_decides_50_slash_51` (auto وpage_slash_column وpage_dot_column)، و`test_a_caller_with_context_reads_the_compound_even_when_the_format_is_pinned`، و`test_a_compound_has_two_or_three_functions_…` | ✅ |
| F2‑AC‑11 | وحدة | `…::test_f2_ac_11_unknown_suffix_profile_suffix_and_out_of_range`. ومراجعة: `test_ansi_function_numbers_outside_1_to_99_are_never_a_device` (0 و00 و01 و100 و100T، بسياق وبلاه)، و`test_a_bad_ansi_instance_is_not_a_device` | ✅ |
| F2‑AC‑12 | وحدة | `…::test_f2_ac_12_every_ansi_class_is_a_class_name`، و`test_electrical_shared_rules.py::test_the_kks_and_ansi_tables_refuse_unknown_keys` | ✅ |
| F2‑AC‑13 | وحدة | `test_electrical_f2_profile.py::test_f2_ac_13_one_profile_reads_iec_kks_and_ansi_together`. ومراجعة: `test_the_selection_adds_kks_and_ansi_beside_exactly_one_device_convention` (الاصطلاحات الأربعة) | ✅ |
| F2‑AC‑14 | وحدة | `…::test_f2_ac_14_kks_and_ansi_are_proposed_and_not_mixed` و`…iec_and_chinese_stay_mixed`. ومراجعة بالعدّ الحقيقي (لا بقاموس): `test_iec_with_chinese_letter_codes_stays_mixed_through_the_real_tally`، و`test_kks_and_ansi_in_bulk_neither_create_nor_hide_the_mix` | ✅ |
| F2‑AC‑15 | وحدة | `…::test_f2_ac_15_under_twenty_tags_kks_has_zero_confidence` | ✅ |
| F2‑AC‑16 | وحدة على الموجّه | `test_api_electrical_router.py::test_f2_ac_16_the_new_fields_are_saved_validated_and_defaulted` (حفظ، و9 حالات 422 باسم الحقل بلا صدى، ولا إصدار يُخزَّن عند الرفض). ومراجعة: أنواع خاطئة، وحروف تحكم وبديلة وعربية وصينية، و15 محرف تعبير نمطي، و64/65 مدخلاً، وتكرار، وطول مفرط، وأن اللاحقة عضوية لا نمط | ✅ |
| F2‑AC‑17 | وحدة | `test_api_electrical_router.py::test_a_member_reads_and_previews_but_neither_registers_nor_changes_a_profile` و`…test_a_viewer_reads_and_nothing_more` (PUT بـ`plant_designation` يعود 403 ولا إصدار جديد) | ✅ |
| F2‑AC‑18 | وحدة وعقد | `test_the_profile_body_description_names_every_profile_field`، واختبار العقد القائم، و`test_f2_ac_18_the_release_fingerprint_moves_with_the_versions`. ومراجعة: `test_an_existing_profile_keeps_its_body_and_its_fingerprint` ببصمة ملف غير افتراضي حُسبت على `master` (`69a75c7d…`) وتطابقت، و`test_only_the_fields_that_differ_from_their_defaults_reach_the_body` | ✅ |
| F2‑AC‑19 | وحدة وتقييم | حالات `appendix-a` (87 حالة) في `cases/identifiers.toml`، و`test_every_appendix_a_example_is_in_the_case_set`. وشغّلتُ الأداة: `M-04 = 1.0000 over 235 cases` (192 + 43) | ✅ |
| F2‑AC‑20 | يدوي | فحصتُ الفرق بنمط الوسوم: لا نتيجة إلا الأنماط المجرّدة (`XYZ`، `AB`، `99AAA99AA999`). أما البحث بالقائمة الحقيقية خارج المستودع فخطوة المراجع (لم أفتح المجلد) | ✅ (يدوي: القائمة الحقيقية للمراجع) |
| F2‑AC‑41 | وحدة | `test_electrical_shared_terminals_wires.py::test_f2_ac_41_a_digits_first_x_strip_reads_under_iec_and_gbt`. ومراجعة بالتشغيل: كاملة العرض والأحرف الصغيرة والمسافات تعطي `1X-25` نفسه | ✅ |
| F2‑AC‑42 | وحدة | `…::test_f2_ac_42_the_chinese_reading_and_the_xt_rule_stay` | ✅ |
| F2‑AC‑43 | وحدة | `test_electrical_shared_rules.py::test_f2_ac_43_digits_first_conventions_are_data` (بلا الجدول، ورمز مجهول، واصطلاح مجهول، وقائمة فارغة، وتعديل الجدول وحده) | ✅ |

**المتطلبات غير الوظيفية**

| NFR | الاختبار | الحالة |
|---|---|---|
| NFR‑F2‑1 الحتمية | `test_electrical_shared_kks.py::test_nfr_f2_1_every_f2_case_parses_twice_the_same`. ومراجعة: `test_parsing_twice_and_with_a_fresh_parser_gives_the_same_result` (15 نصاً × 3 تلميحات)، و`test_the_tally_does_not_depend_on_token_order`، و`test_detection_is_deterministic` | ✅ |
| NFR‑F2‑2 القواعد بيانات | `test_electrical_shared_rules.py::test_nfr_f2_2_a_new_kks_form_and_a_new_suffix_need_no_code` + `F2‑AC‑43` | ✅ |
| NFR‑F2‑3 لا نص مستخدم في تعبير | حالات `ansi_suffixes` (مراجعة: `test_a_metacharacter_or_a_hidden_character_is_refused_and_never_echoed` × 25، و`test_a_suffix_is_membership_only_never_a_pattern`). والنماذج في `kks.py` تُبنى من أقنعة الملف وحدها (قرأتُ الشيفرة) | ✅ |
| NFR‑F2‑4 الأداء | قياس يدوي (القسم 3، وحكمي في §5) | ✅ |
| NFR‑F2‑5 حدود الأسطورة | INC‑2. وفي البند 1 حدّ 64 للّواحق: `test_the_suffix_limit_counts_what_was_sent_and_it_is_64_entries_at_most` | ✅ (في النطاق) |
| NFR‑F2‑6 التراخيص | `F2‑AC‑12` | ✅ |
| NFR‑F2‑7 الخصوصية | `F2‑AC‑20` | ✅ (يدوي) |

لا معيار بلا تغطية.

## 2. نتائج بوابات المشروع

من `/home/AIZZAK`، بالترتيب، بعد إضافة اختبارات المراجعة. لم تُشغَّل اختبارات `tests/integration` (المكدّس الحي مستعمل).

```
1) ruff format --check .   -> 993 files already formatted
2) ruff check .            -> All checks passed!
3) mypy src                -> Success: no issues found in 551 source files
4) lint-imports            -> Contracts: 9 kept, 0 broken.
5) pytest tests/unit tests/architecture tests/eval -q -p no:warnings
   [100%] خرج بالرمز 0 (8,671 اختباراً مجموعاً: unit وarchitecture وeval)، بلا فشل. السطر الختامي مخفي بإعداد -q في المشروع، فالدليل رمز الخروج 0 وعدّ الجمع
6) deploy/prometheus/test-rules.sh -> SUCCESS: 31 rules found ... test-rules: OK
```

ملاحظات:
- البوابة 5 بمسارات `unit` و`architecture` و`eval` فقط، كما قيّد المالك. اختبارات `live_*` لم تُشغَّل ولم تُتخطَّ بصمت، فهي خارج المسار المطلوب.
- لم تُضَف أي مرشحات تُخفي اختباراً.

## 3. الاختبار الاستكشافي

شُغّل على المحلّل مباشرة وعلى الموجّه (عبر اختبارات المراجعة)، بلا خدمات حية. والواجهة الأمامية خارج هذا المستودع، فلا RTL/LTR عند 375px هنا.

| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| KKS بأحرف صغيرة وفراغات وأرقام كاملة العرض | `20xyz10ab123`، `20 XYZ 10 AB 123`، NBSP، tab، سطر جديد، كاملة العرض | قانوني واحد `20XYZ10AB123` والأصل محفوظ | كما المتوقع | ✅ |
| KKS شبه صحيح | `20XY10AB123`، `20XYZ10AB12`، `…CD4`، `…CD45E` | غير صالح `kks.malformed` لا يُسقط | كما المتوقع | ✅ |
| كابل بطول لاحقة خاطئ | `20XYZ10456` (3)، `…1045678` (5)، `20XYZ10AB12345`، `…123456` | ألا يعود صالحاً أبداً | يعود `None` (لا معرّف) أو غير صالح، ولا صالح قط. و4 أرقام صالحة | ✅ (انظر الملاحظة 1) |
| KKS ببادئة والميزة معطّلة | `=…`، `+…`، `++…` بقواعد IEC وحدها أو الكاملة | ليس KKS | مرجع IEC كما اليوم | ✅ |
| بادئات مزدوجة | `=+…`، `+++…`، `-…` | ليس وسم KKS | كما المتوقع | ✅ |
| وسم أطول من 200 | `20XYZ10AB123`×17، 201 رقماً، مسافات | لا معرّف | `None` | ✅ |
| حرف مخفي / علامة اتجاه / عربي أو صيني ملتصق / سيريلي | `20{ZWSP}XYZ10AB123`، RLO، `…ن`، `…中`، `А` سيريلي | المخفي غير صالح، والاتجاه صالح بوسم، والباقي لا يصير صالحاً | كما المتوقع | ✅ |
| ANSI المدى | `0`، `00`، `01`، `100`، `100T`، `0T` (بسياق وبلاه) | ليس جهازاً | كما المتوقع. و1..99 بسياق جهاز | ✅ |
| ANSI لواحق متعددة | `87TN`، `87TTT`، `87TTTT`، `87QZ` | صالح، غير صالح بسبب، لا معرّف، غير صالح | كما المتوقع | ✅ |
| ANSI المثيل | `27-0`، `27-01`، `27-100`، `27-2-3`، `27-99` | الأولى لا، `27-99` نعم | كما المتوقع | ✅ |
| `50/51` مقابل المرجع المتقاطع | auto، `page_slash_column`، `page_dot_column`، بسياق، ANSI معطّل | كما F2‑AC‑10 | كما المتوقع (مركّب ثلاثي أقصى؛ والرابع مرفوض) | ✅ |
| رقم مجرد بسياق وبلاه | `52` | سلك ببديل ANSI، ثم جهاز بسياق | كما المتوقع | ✅ |
| حقول الملف بأنواع خاطئة | أرقام، `null`، bool، قائمة، قاموس، `"KKS"` | 422 باسم الحقل | كما المتوقع | ✅ |
| لواحق بمحارف تعبير نمطي | 15 محرفاً: `.*`، `(`، `[A-Z]`، `\w`، `A|B` … | رفض بلا صدى | كما المتوقع، ولا صدى للقيمة | ✅ |
| لواحق بمحارف مخفية | ZWSP وRLM وRLO وحرف الوصل الناعم وZWJ وWord joiner وtab وNUL | رفض بلا صدى | رُفضت كلها. ومحدّد التنويع U+FE0F يُحذف بلا رفض فيُخزَّن `QZ` نظيفاً | ✅ (الملاحظة 2) |
| 64 / 65 لاحقة وتكرار | 64 مقبول؛ 65؛ `["A"]×65` | 65 يُرفض | كما المتوقع. و`["a","A","a"]` تنهار إلى `("A",)` | ✅ |
| الصدى وبصمة الملفات القديمة | ملف `gbt_5094` قديم بلا الحقول | الجسد والبصمة كما على `master` | `69a75c7d…` على الفرعين | ✅ |
| الكشف: IEC + حروف صينية | 60 IEC + 30 `nKM` (+ KKS وANSI بالمئات) | مختلط | `mixed_standards = True` | ✅ |
| الكشف: KKS وANSI بكثرة | 60 IEC + 120 KKS + 120 ANSI | غير مختلط | غير مختلط، والاصطلاح IEC | ✅ |
| أدلة ملتبسة | `1..99` و`50/51` و`12/3` و`87QZ` و`87TTT` ونصوص بعلامة مدمجة/مخفية | لا تُعدّ دليلاً | لا KKS ولا ANSI في العدّ | ✅ |
| ترتيب الرموز في `tally` | خلط عشوائي | العدّ نفسه | متساوٍ | ✅ |
| بدون صلاحية / مستأجر آخر / ضغط مزدوج | `viewer` و`member` يرسلان PUT | 403 ولا إصدار | كما المتوقع (F2‑AC‑17). ولا تغيّر في العزل بين المستأجرين (لا مسار جديد) | ✅ |
| أخطاء الشبكة/API | لا مسار جديد ولا نداء خارجي في هذا البند | — | لا ينطبق | — |
| الأداء NFR‑F2‑4 | 192 حالة M‑04 من `master`، 20 تكراراً × 30 جولة، على المضيف نفسه، بالتناوب مرتين | ≤ 1.2× | `parse`: `master` 13.27..13.74 µs للحالة؛ الفرع بقواعد افتراضية 14.02..14.52 (**1.06×**)؛ وبـKKS وANSI مفعّلين 15.27..15.31 (**1.15×**). و`tally` على 1,000 رمز: `master` 14.5..14.7 ms، الفرع 22.3..22.5 (**1.53×**)، ومع التفعيل 17.2..17.6 (1.19×) | ✅ |

## 4. الأخطاء المكتشفة

لا خطأ مانع ولا كبير. ملاحظتان صغريان (لا تمنعان القبول، ولا تحتاجان إصلاحاً قبل الدمج):

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| 1 | وسم كابل بطول لاحقة خاطئ (`20XYZ10456`، `20XYZ1045678`، `20XYZ10AB12345`) يعود **بلا معرّف** لا «غير صالح بسبب `kks.malformed`»، لأن الشكل الخارجي `_OUTER` لا يغطيه. وهذا ما نصّه التصميم (`design.md` §3.5 الخطوة 6)، لكن `F2‑AC‑4` يقول «عدد أحد أجزائه خاطئ» ولا يذكر الكابل صراحة. ولا يُسقط وسم صالح، ولا يُنتج صالحاً خاطئاً | minor | قواعد IEC+KKS: `parse("20XYZ10456")` تعيد `None` (و`20XYZ10AB1234` تعيد `kks.malformed`). مثبّت بـ`test_a_cable_suffix_of_the_wrong_length_is_never_a_valid_tag` | مفتوح: قرار للمالك إن أراد توسيع `_OUTER` (`kks.py:46`) |
| 2 | `ansi_suffixes` تقبل محدّد التنويع (U+FE0F) والمسافة الداخلية (`"Q Z"`) وتطبّعها إلى `QZ`، بينما تُرفض حروف التحكم والتنسيق الأخرى. القيمة المخزَّنة نظيفة ASCII دائماً، فلا أثر أمني | minor | `DrawingProfile.from_body({"schema":1,"ansi_suffixes":["Q️Z"]})` تعيد `("QZ",)`. مثبّت بـ`test_a_variation_selector_in_a_suffix_is_dropped_and_the_stored_value_is_clean` | مفتوح: `profile.py` (`_ansi_suffixes`، `_FORBIDDEN_TEXT_CATEGORIES`) |

**ملاحظات (ليست أخطاء):**
- `9A-99` يُقرأ جهاز ANSI (`9` + لاحقة `A` + مثيل 99) فقط حين تكون كل الاصطلاحات مفعّلة مع ANSI؛ ويسبقه الطرف حيث ينطبق (`1X-25`). وهذا من ترتيب `_ORDER` المصمَّم.
- `20XYZ10AB123CDE45` صالح (المعدّة `AB123C` والمكوّن `DE45`) وفق الأقنعة، وهو التباس بنيوي معروف في الأقنعة، لا خلل.
- الانحرافات الخمسة التي أبلغ عنها المطوّر: (1) `00` و`05` أرقام أسلاك في c233/c234 ✅ مقبول، يوافق F2‑AC‑11 (لا جهاز)؛ (2) أسباب شبه الصحيح تجارب منفصلة ✅ موثّق ومختبَر؛ (3) المسبار يقرأ الرموز التي تبدأ برقم أو `=`/`+` فقط ✅ ولا يفقد دليلاً (KKS وANSI لا يبدآن إلا بها)؛ (4) تعديل ثلاثة اختبارات قائمة ✅ راجعتُها: تعديل الحدّ (300 إلى 400) وإضافة مفتاحين وتحريك الإصدارين، ولا تضعيف لتأكيد؛ (5) NFR‑F2‑4 انظر §5.

## 5. الحكم

**NFR‑F2‑4:** الحدّ في التصميم (`design.md:702`، `:766`) على `parse`، و`tally` «يُقاس ويُسجَّل». قياسي أعلاه يطابق قياس المطوّر: `parse` **1.06×** بالقواعد الافتراضية و**1.15×** مع التفعيل (دون 1.2)، و`tally` **1.53×** بالقواعد المعبّأة. أراه **مقبولاً**: الحدّ المكتوب لـ`parse` محقَّق؛ و`tally` يعمل مرة واحدة لكل وثيقة عند الاستيعاب لا في المسار الساخن، والفرق المطلق نحو 8 ms لكل 1,000 رمز (نحو 8 µs للرمز)، والتصميم نفسه (خ‑3) توقّع المضاعفة وجعل التضييق إلى الرموز التي تبدأ برقم/`=`/`+` علاجها، وقد طُبّق. ويُوصى بتسجيل الأرقام في جسد الـPR.

**PASS** — البوابات الست خضراء، وكل معيار Must (`F2‑AC‑1..19` و`41..43`) له اختبار يؤكد شرطه، و`F2‑AC‑20` يدوي بيد المراجع (فحص الفرق بالأنماط نظيف)، ولا خطأ مانع أو كبير؛ ملاحظتان صغريان فقط.

## 6. الإعادة 1 (2026‑11‑09): فحص ما بعد المراجعة

- **النطاق:** `bd05ee8..HEAD`، أي الإيداعان `568bfea` (الإصلاح) و`1216021` (وصف العقد)، ومعهما إيداع اختباراتي.
- **المراجع:** ح‑1 (ضجيج التيار والأقطاب يُسقط ثقة اقتراح IEC) وح‑2 (تسمية جهد مثل `24V` تعطي 422).
- **الأمثلة كلها مخترعة**، والاستثناء الوحيد `1X-25` (§11.5). فحصتُ الأنماط في الأسطر المضافة فلم أجد غيرها.
- **الاختبارات المضافة** في `tests/unit/test_electrical_f2_rework1_qa.py` (50 حالة، بلا مساس بالشيفرة).

### 6.1 المعايير المعدَّلة وبنود §11.7

| البند / المعيار | الاختبار | الحالة |
|---|---|---|
| `F2‑AC‑15` (معدَّل: لا يُقترح KKS تحت 20) | `test_electrical_f2_profile.py::test_f2_ac_15_under_twenty_tags_kks_is_not_proposed` (19: الحقل `none` بثقة 1.0، والثقة العامة كالأساس، ولا `systems`؛ و20: يُقترح بثقة 0.2). ومراجعة: `test_the_thresholds_are_per_field_and_exact` (19 ANSI مع 20 KKS وعكسه) | ✅ |
| `F2‑AC‑14` (الكيس الجديد) | `test_f2_ac_14_*` تدور على `87T` و`50BF` و`51N` و`27-1` والتأكيدات كما هي | ✅ |
| `F2‑AC‑20` (استثناء `1X-25`) | يدوي: فحص الأنماط في فرق الإعادة نظيف. و`1X-25` تسمية جهد ترفضها كل التوليفات (لقطة §6.3) | ✅ |
| `F2‑AC‑16` (تطبيع `ansi_suffixes`) | `test_electrical_f2_qa.py::test_whitespace_and_full_width_letters_fold_to_the_clean_suffix`. ومراجعة: `test_every_spelling_of_one_suffix_folds_to_one` (مسافة وtab وسطر جديد وعرض كامل وحروف صغيرة وNBSP)، و`test_unfoldable_suffixes_are_refused_and_never_echoed` | ✅ |
| `F2‑AC‑17`، `F2‑AC‑5` | `test_f2_ac_5_both_confusions_together_name_both_reasons` (6 حالات)، وحالة M‑04 `c236` | ✅ |
| `F2‑AC‑43` / NFR‑F2‑2 | `test_electrical_shared_rules.py::test_the_evidence_rules_are_data` (تعطيل اللواحق ورفع المستوى بالبيانات، و9 طفرات `RuleError`) | ✅ |
| §11.7 بند 1 (بيانات القاعدة وS‑1) | `test_the_evidence_rules_are_data`، و`test_s1_optional_positions_are_capped` | ✅ |
| بند 2 (دالتا الدليل وS‑4) | `test_the_ansi_evidence_rule` (15)، و`test_the_kks_evidence_rule`، و`test_the_probe_filter_checks_the_length_first` | ✅ |
| بند 3 (الحدّ في الاكتشاف) | `test_rating_and_pole_tokens_…` (×1 و×30)، و`test_system_level_shapes_are_no_kks_evidence`، و`test_under_twenty_evidence_identifiers_nothing_is_proposed` | ✅ |
| بند 4 (تسميات الجهد) | `test_voltage_labels_with_ansi_off_…`، و`…hides_an_ansi_device_is_refused`، و`…supply_label_is_accepted_and_read_as_a_voltage`، و`…kks_shaped_label_is_refused`، و`…detection_can_enable_ansi_over_a_stored_label…`، و`test_api_electrical_router.py::test_a_supply_voltage_label_is_saved` (200 والإصدار 2) | ✅ |
| بند 5 (الالتباسان معاً) | `test_f2_ac_5_both_confusions_…` وحالة `c236` | ✅ |
| بند 6 (`checked_ansi_suffixes`) | `test_electrical_f2_qa.py` المعدَّل (422 للمنسّقات غير القابلة للطيّ) | ✅ |
| بنود 7 و8 (وثائق نصية وOpenAPI) | اختبار العقد القائم `openapi.yaml` أخضر | ✅ |
| بند 9 (S‑5) | `test_api_electrical_router.py`: الإصدار 1 بعد الرفض | ✅ |
| بند 10 (NFR‑F2‑4) | §6.4 | ✅ |
| بند 12 (لا تغيير) | `git diff bd05ee8..HEAD --stat`: لا مساس بـ`.importlinter` ولا `tests/architecture/` ولا `inspection.py` ولا `documents.py` ولا `sql_repository.py` ولا النشر | ✅ |

### 6.2 اختبارات الحواف المضافة

| الاختبار | ما يثبّته |
|---|---|
| `test_tally_counts_each_edge_token_as_the_rule_says` (13) | اللاحقة `A` وحدها مستثناة: `50N` و`50T` و`50BF` دليل، و`50A` و`32A-1` لا (المثيل لا ينقذ لاحقة التيار)؛ `27-1` و`27-12` دليل، و`27` و`50` العاريان لا؛ `16A` و`1N` بلا فئة لا؛ `=20XYZ10` (المستوى 1) لا، و`=20XYZ10AB123` (المستوى 2) نعم |
| `test_the_thresholds_are_per_field_and_exact` | الحدّ 19/20 لكل حقل على حدة، ولا يؤثر أحدهما في الآخر |
| `test_noise_in_a_mixed_iec_document_…` | وثيقة IEC فيها 70 رمز ضجيج (`16A` و`24V` و`32A` و`4P` و`1N` و`20XYZ10`): عدّ ANSI يبقى 20 بالضبط والاقتراح لا يتغيّر؛ ومع 19 دليلاً لا يُقترح شيء ولا يتغيّر الاقتراح بالضجيج |
| `test_supply_labels_are_saved_…` (12 توليفة) | `24V` و`48V` و`12VDC` مقبولة مع KKS وANSI مفعّلين ومعطّلين، ومخزَّنة قانونية |
| `test_a_label_that_hides_evidence_is_refused_only_where_that_system_is_on` (10) | `87T` و`50N` مرفوضتان مع ANSI فقط (وحده أو مع KKS)، ومقبولتان مع KKS وحده ودون تفعيل؛ ووسم KKS كامل مرفوض مع KKS فقط؛ و`16A` مقبولة؛ والرسالة تبدأ بـ`voltage_labels` ولا تعيد القيمة |
| `test_a_saved_non_evidence_label_is_read_as_a_voltage_…` | `24V` و`16A` المقبولتان تُقرآن `VOLTAGE` تحت التفعيلات الأربع |
| `test_the_limit_of_64_counts_the_list_…` | 64 مقبولة، و65 مرفوضة برسالة ثابتة |

### 6.3 الاستكشاف (على الشيفرة الحالية)

| الحالة | النتيجة |
|---|---|
| 19 و20 دليلاً (ANSI وKKS) فوق `iec(120)` | 19: يطابق `propose(iec(120))` تماماً (ثقة 0.8)؛ 20: يُقترح بثقة الحقل 0.2 و`basis["systems"] == ("ansi:20",)` ✅ |
| 19 دليلاً + 50 × `16A` | لا اقتراح، والثقة كالأساس ✅ |
| 20 دليلاً + `16A` و`24V` | العدّ 20 تماماً ✅ |
| KKS مستوى 1 (`20XYZ10`، `20XYZ104567`، `=20XYZ10`) مقابل مستوى 2 و3 | الأول لا يُعدّ، والثاني والثالث يُعدّان ✅ |
| `20XYZ10AB1234567` | مستوى 2 صالح، دليل ✅ |
| `12VDC` | يُقرأ ANSI غير صالح (لاحقة `VDC`)، فهو غير دليل، فتسمية الجهد تُقبل ✅ |
| تسمية `24V` / `48V` / `12VDC` | مقبولة في الحالات الأربع (بلا تفعيل، ANSI، KKS، كلاهما) ✅ |
| تسمية `87T` | مقبولة بلا تفعيل ومع KKS، مرفوضة مع ANSI ✅ (يوافق §11.2) |
| تسمية `1X-25` و`KM1` | مرفوضتان دائماً ✅ (استثناء `1X-25` المكتوب لا يتعلق بالتسميات) |
| تسمية `16A` و`50A` و`32A` و`4P` | مقبولة تحت ANSI، وتُقرأ جهداً. ملاحظة تصميمية: `50A` له فئة لكن اللاحقة `A` مستثناة، فلا يُحجب (قرار §11.1) |
| `ansi_suffixes`: `Q Z`, `Q<tab>Z`, `QZ<nl>`, `ＱＺ`, `qz`, NBSP | كلها `("QZ",)` ✅؛ و`Q<zwsp>Z` و`QZZZ` و`Q1` والفارغة مرفوضة برسالة ثابتة لا تعيد القيمة ✅ |
| `ansi_suffixes` 64 مقابل 65 | 64 تُقبل، و65 تُرفض ✅ |
| نص بطول 70 مسافة داخل لاحقة | مرفوض (حدّ 64 قبل التطبيع) ✅ |

**ملاحظة:** إضافة رمز مثل `52` (رقم سلك حقيقي) إلى الضجيج ترفع عدّ المعرّفات فتغيّر ثقة العيّنة، وهذا سلوك قائم وليس أثراً للقاعدة (الرموز الستة الأخرى لا تغيّرها).

### 6.4 NFR‑F2‑4 (إعادة القياس)

- **الطريقة:** سكربت خارج المستودع في المجلد المؤقت، بـ`python -I`، على نسخة `master` (`git archive`) ثم الفرع، على المضيف نفسه.
- **parse:** متوسط أقل زمن من 7 جولات على نصوص الحالات الـ192 غير المُحدِّدة (`select`) في `identifiers.toml`.
- **tally:** 1,000 رمز اصطناعي عشوائي بذرته ثابتة.
- **مرتان على الأقل لكل جهة، والأرقام ثابتة:**

| القياس | master | الفرع | النسبة |
|---|---|---|---|
| `parse`، القواعد المعبّأة | 14.4 – 14.5 µs | 15.1 – 15.5 µs | **1.05×** |
| `parse`، KKS وANSI مفعّلان | غير موجود | 15.4 – 15.8 µs | **1.08×** (نسبة إلى master المعبّأ) |
| `tally`، 1,000 رمز | 13.5 – 13.9 ms | 28.9 – 29.1 ms | **2.1×** (للتسجيل فقط) |

- حدّ `parse` 1.2× محقَّق بفارق واسع.
- أبلغ المطوّر `parse` 1.09 و1.14 مع التفعيل و`tally` 1.59. أرقامي أدنى في `parse` وأعلى في `tally`، لأن بذرة رموزي اصطناعية فيها حصة أكبر من الرموز التي تبدأ برقم (يشغّلها المسبار). فالمضاعفة المتوقعة (§7 خ‑3) ما زالت قائمة ومسجَّلة، وهي لا تتجاوز نحو 15 ms لكل 1,000 رمز عند الاستيعاب، ولا حدّ مكتوب عليها.

### 6.5 البوابات

| البوابة | النتيجة |
|---|---|
| `ruff format --check .` | 994 ملفاً منسَّقاً ✅ |
| `ruff check .` | All checks passed ✅ |
| `mypy src` | no issues في 551 ملفاً ✅ |
| `lint-imports` | 9 عقود محفوظة، 0 مكسور ✅ |
| `pytest tests/unit tests/architecture tests/eval -rs` | 8724 passed, 37 skipped (رُجّع بعد إضافة اختباراتي) ✅ |
| `deploy/prometheus/test-rules.sh` | SUCCESS: 31 rules، `test-rules: OK` ✅ |

- الاختبارات المتجاوَزة: حزمة الحاويات الاختيارية (`RUN_EDGE_FILES_TEST=1`) وما شابهها، وكلها اختيارية. ولم أشغّل `tests/integration` لأن المكدّس الحيّ قيد الاستعمال (CLAUDE.md).
- لم تُضَف مرشِّحات تخفي اختباراً.

### 6.6 الأخطاء

**لا أخطاء مانعة ولا كبيرة.** ولا أخطاء صغيرة جديدة.

الخطآن الصغيران في §4 السابق:
1. **وسم الكابل بطول لاحقة خاطئ يعود بلا معرّف:** ما زال مفتوحاً كما هو، قرار للمالك (`kks.py:46`)، ولا أثر له على الإعادة.
2. **محدّد التنويع (U+FE0F) في `ansi_suffixes`:** **مُغلق**. الإعادة جعلته مرفوضاً بدل أن يُطوى (بند 6)، وبقيت الحالة في `test_electrical_f2_qa.py` تؤكد الرفض.

### 6.7 الحكم

**PASS (الإعادة 1).**
- ح‑1 مُغلق: لا يؤثر `16A` ولا `32A` ولا `4P` ولا `2P` ولا `1N` (مرة أو 30 مرة) في اقتراح IEC، والحدّ 20 دقيق (19 لا يُقترح، و20 يُقترح) لكل حقل على حدة.
- ح‑2 مُغلق: `24V` و`48V` و`12VDC` تُحفظ في كل توليفات التفعيل، و`87T` و`27-1` و`50BF` تبقى مرفوضة حين يكون ANSI مفعّلاً.
- البوابات خضراء، وNFR‑F2‑4 ضمن الحدّ.

</div>
