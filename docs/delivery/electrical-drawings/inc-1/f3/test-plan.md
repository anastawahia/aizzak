<div dir="rtl">

# خطة الاختبار: F3، قواعد المعرّفات (فرع `feat/electrical-drawings-f3-rules`، 50c212e..51b9024)

**المختبِر:** qa-engineer، 2026-11-20. **المرجع:** `design.md` 1.0 §5.1 و§5.2 و§5.3. **اختباراتي:** `tests/unit/test_electrical_f3_qa.py` (258 حالة، منها حالة `xfail` واحدة تسجّل الخطأ QA‑F3‑1). كل الأمثلة مخترعة، وفحصتُ أنها لا تطابق أي نص في `/home/ele-data/labels/` (العدّ 0 لكل مثال، بلا طباعة محتوى).

## 1. مصفوفة التغطية

الاختصارات: `R` = `tests/unit/test_electrical_f3_rules.py`، `G` = `…_f3_groups.py`، `T` = `…_f3_tokens.py`، `Q` = `…_f3_qa.py` (جديد)، `SR` = `…_shared_rules.py`.

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| F3‑AC‑1 | وحدة | `R::test_f3_ac_1_ansi_without_a_class_needs_context`، `R::test_f3_ac_1_a_compound_of_numbers_outside_the_table_is_a_cross_reference`، `R::test_f3_ac_1_16f_is_invalid_with_context_for_its_suffix`. وQA: `Q::test_ansi_without_a_class_needs_a_hint`، `Q::test_ansi_with_a_class_reads_with_and_without_a_hint`، `Q::test_ansi_an_unknown_suffix_with_a_class_is_invalid_but_kept`، `Q::test_ansi_that_is_no_device_even_with_a_hint` | ✅ |
| F3‑AC‑2 | وحدة وتقييم | `R::test_f3_ac_2_classed_ansi_reads_as_in_f2`، `R::test_f3_ac_2_the_m04_cases_change_only_at_c226`، `test_electrical_shared_parser.py::test_f2_ac_7_ansi_on_changes_only_the_listed_ambiguities`. وQA: `Q::test_ansi_a_compound_reads_as_a_reference_with_the_device_alternative_only_in_context` | ✅ |
| F3‑AC‑3 | وحدة | `R::test_f3_ac_3_a_named_vendor_wire_pattern_wins_over_ansi`، `R::test_f3_ac_3_letter_digits_is_read_only_when_named`، `R::test_opt_in_patterns_never_read_unnarrowed`. وQA: `Q::test_a_named_wire_pattern_wins_over_ansi_without_context_only_when_named`، `Q::test_unnarrowed_rules_never_read_an_opt_in_wire_pattern` | ✅ |
| F3‑AC‑4 | وحدة على الموجّه | `tests/unit/test_api_electrical_router.py::test_f3_ac_4_opt_in_wire_pattern_names_are_accepted` (يشمل `.*` والاسم المجهول، 422) | ✅ |
| F3‑AC‑5 | وحدة | `R::test_f3_ac_5_a_digits_first_terminal_is_no_ansi_under_nfpa` | ✅ |
| F3‑AC‑6 | وحدة | `test_electrical_shared_ansi.py::test_f3_ac_6_a_suffix_with_a_trailing_digit`. وQA: `Q::test_ansi_without_a_class_needs_a_hint[14B6]`، `Q::test_ansi_that_is_no_device_even_with_a_hint[59G2-1]` | ✅ |
| F3‑AC‑7 | وحدة | `R::test_f3_ac_7_ratings_are_no_identifiers`، `R::test_f3_ac_7_a_unit_that_is_a_letter_code_matches_as_written`، `R::test_f3_ac_7_a_rating_is_a_value_of_its_own`. وQA: `Q::test_ratings_are_no_identifiers_under_any_rules`، `Q::test_deviation_7_*`، `Q::test_a_rating_beside_an_identifier_leaves_the_identifier_alone` | ✅ |
| F3‑AC‑8 | وحدة | `R::test_f3_ac_8_voltage_labels_win`، `R::test_f3_ac_8_a_voltage_label_the_profile_added_stays_a_voltage` | ✅ |
| F3‑AC‑9 | وحدة | `SR::test_f3_ac_9_units_are_data_and_checked`، `SR::test_f3_ac_9_a_unit_the_file_lacks_is_no_barrier`. وQA: `Q::test_deviation_7_the_units_table_is_data` | ✅ |
| F3‑AC‑10 | وحدة وتقييم | `R::test_f3_ac_10_page_zero_is_no_page`، `R::test_f3_ac_10_other_pages_read_as_before`. وQA: `Q::test_page_zero_is_no_reference_in_any_format`، `Q::test_pages_from_one_up_still_read_as_references`، `Q::test_a_prefixed_page_zero_is_no_cross_reference` | ✅ |
| F3‑AC‑11 | وحدة | `G::test_f3_ac_11_*` (3 حالات). وQA: `Q::test_every_comma_spelling_reads_one_list`، `Q::test_a_fully_wide_list_range_and_suffix_list_read_like_the_ascii_ones`، `Q::test_a_doubled_wide_comma_is_an_empty_member` | ✅ |
| F3‑AC‑12 | وحدة | `G::test_f3_ac_12_*` (3 حالات). وQA: `Q::test_deviation_5_a_wire_range_needs_the_named_pattern`، `Q::test_deviation_5_the_letter_first_wire_range_is_named_too` | ✅ |
| F3‑AC‑13 | وحدة | `G::test_f3_ac_13_*` (3 حالات). وQA: `Q::test_a_malformed_suffix_list_is_no_group`، `Q::test_a_malformed_backslash_list_is_no_group` | ✅ |
| F3‑AC‑14 | وحدة | `G::test_f3_ac_14_*` (4 حالات). وQA: `Q::test_a_range_at_the_cap_is_expanded`، `Q::test_a_range_over_the_cap_generates_nothing`، `Q::test_the_cap_is_checked_before_a_member_is_read`، `Q::test_a_list_and_a_suffix_list_over_the_cap_are_flagged_and_empty`، `Q::test_a_list_at_the_cap_is_expanded`، `Q::test_the_cap_is_the_rules_own_value`، `Q::test_hostile_texts_inside_the_length_bound_are_cheap`، `Q::test_a_flood_of_hostile_texts_is_linear_enough`، `Q::test_a_text_that_is_no_ascending_range_is_no_group`، `Q::test_range_members_edges`، `Q::test_list_edges` | ✅ |
| F3‑AC‑15 | وحدة وتقييم | `G::test_f3_ac_15_the_readings_of_today_are_no_group`، وحالات §5.3 | ✅ |
| F3‑AC‑16 | وحدة | `G::test_f3_ac_16_the_tally_counts_the_members_of_a_group`، `G::test_f3_ac_16_the_characterization_carries_the_members`. وQA: `Q::test_tally_counts_each_member_and_the_tokens_it_saw`، `Q::test_tally_over_the_cap_or_unreadable_counts_no_member` | ✅ |
| F3‑AC‑17 | وحدة | `T::test_f3_ac_17_split_candidates`، `T::test_f3_ac_17_a_list_is_one_candidate_and_marks_at_the_end_are_dropped`، `T::test_f3_ac_17_the_split_is_linear`. وQA: `Q::test_split_candidates_cjk_edges`، `Q::test_split_candidates_arabic_words_pass_through`، `Q::test_split_candidates_pieces_are_stable_and_clean`، `Q::test_split_candidates_is_linear_on_a_huge_word`، `Q::test_deviation_3_quotes_and_primes` | ✅ |
| F3‑AC‑18 | وحدة | `T::test_f3_ac_18_an_enclosed_number_is_no_wire`، `T::test_f3_ac_18_a_full_width_digit_is_still_a_digit`. وQA: `Q::test_an_enclosed_number_is_never_a_wire` | ✅ |
| F3‑AC‑19 | وحدة | `test_electrical_shared_terminals_wires.py::test_f3_ac_19_a_prime_after_the_terminal_number_is_part_of_it`. وQA: `Q::test_deviation_2_the_terminal_prime`، `Q::test_deviation_2_the_prime_is_for_terminals_and_bridges_only`، `Q::test_deviation_2_a_prime_elsewhere_is_no_identifier` | ✅ |
| F3‑AC‑20 | وحدة | `test_electrical_shared_terminals_wires.py::test_f3_ac_20_letters_first_conventions_are_data`، `…::test_f3_ac_20_a_bad_letters_first_entry_is_a_rule_error` | ✅ |
| F3‑AC‑21 | وحدة | `test_electrical_shared_kks.py::test_f3_ac_21_*` (5 حالات)، `SR::test_the_kks_f3_keys_are_required_and_checked`. وQA: `Q::test_deviation_8_the_kks_part_bound_stays_eight`، `Q::test_deviation_9_the_near_miss_gate_reads_its_own_key_not_shortest`، `Q::test_evidence_*` | ✅ |
| F3‑AC‑22 | وحدة | `R::test_f3_ac_22_location_references`، `test_electrical_profile_detection.py::test_location_formats_keep_auto` | ✅ |
| F3‑AC‑23 | وحدة | `tests/unit/test_ele_parse_service.py::test_f3_ac_23_digit_tokens_fill_the_sample_first`. وQA: `Q::test_tokens_digit_tokens_come_first_whatever_their_place`، `Q::test_tokens_the_sample_bound_cuts_the_others_not_the_digit_tokens`، `Q::test_tokens_are_distinct_across_words_and_pieces`، `Q::test_tokens_a_page_of_five_thousand_words_stays_in_reading_order`، `Q::test_tokens_unicode_and_arabic_words` | ✅ |
| F3‑AC‑24 | وحدة | `test_ele_parse_service.py::test_f3_ac_24_a_long_word_is_split_before_the_bound`. وQA: `Q::test_tokens_every_long_token_separator_splits`، `Q::test_tokens_a_long_word_keeps_its_fitting_pieces_only`، `Q::test_tokens_split_pieces_with_a_digit_join_the_digit_group`، `Q::test_tokens_a_short_word_keeps_its_list_whole`، `Q::test_tokens_a_huge_word_is_bounded_in_time_and_size` | ✅ |
| F3‑AC‑25 | وحدة | `SR::test_the_rules_file_is_packaged_and_versioned` (1.2.0 و1.4.0 والأنواع الاثنا عشر)، `test_ele_parse_service.py:220` (SERVICE 1.1.0 وPIPELINE 1.2.0)، `test_electrical_f2_profile.py` (سجلّ التوصيف 1.4.0/1.2.0)، `test_electrical_f2_qa.py::test_an_existing_profile_keeps_its_body_and_its_fingerprint` (البصمة)، `test_electrical_f0_qa.py:103`. وQA: `Q::test_tokens_the_service_version_moved_with_the_fill_order` | ✅ |
| F3‑AC‑26 | وحدة وعقد | `test_api_electrical_router.py::test_every_electrical_schema_has_the_published_properties_and_required_set`، `…::test_the_profile_body_description_names_every_profile_field`. فرق `docs/` و`deploy/` = سطر واحد في `openapi.yaml` (وصف) | ✅ |
| F3‑AC‑27 | تقييم | `test_electrical_shared_parser.py::test_f3_ac_27_every_f3_appendix_example_has_an_appendix_a_case`، و`python -m tests.eval.electrical.m04_identifiers` = **1.0000 على 286 حالة** (§2) | ✅ |
| F3‑AC‑28 | يدوي | لا اختبار آلي: البحث في الفرق بقائمة الوسوم الحقيقية يجريه المراجع، ولا يجوز أن يُخزَّن شيء منها في المستودع (يعطي اختبار آلي بقائمتها تسريباً). فحصتُ أمثلتي أنا بالبحث `grep -F` في `/home/ele-data/labels`: 0 تطابق | ⏳ يدوي (المراجع) |
| F3‑AC‑29 | قياس | لا اختبار آلي: يعيد المنسّق القياسات الثلاثة على التسميات خارج المستودع، ولا يجوز أن تدخل المستودع | ⏳ يدوي (المنسّق) |
| F3‑AC‑30 | مراجعة | لا اختبار آلي: مراجعة فرق وثائق (SRS 0.21) يجريها المنسّق | ⏳ يدوي (المنسّق) |
| NFR‑F3‑1 | قياس | لا اختبار آلي (توقيت بين `master` والفرع). **قرار المالك 2026-11-20: «اقبل وسجّل».** أرقام المطوّر، لا أعدتُ قياسها: `parse` 0.9× و0.95× (الحدّ 1.2× محقَّق)، و`tally` بلا قوائم 1.0× (محقَّق)، و`tally` على خليط فيه 10% قوائم ونطاقات **1.8×** (فوق الحدّ، مسجَّل فقط بقرار المالك)، و`_tokens` 4× بالميكروثانية. وأضفتُ حاجزاً مطلقاً للأسوأ: `Q::test_hostile_texts_inside_the_length_bound_are_cheap` و`Q::test_a_flood_of_hostile_texts_is_linear_enough` | ✅ بقرار المالك |
| NFR‑F3‑2 | وحدة | `R::test_nfr_f3_2_every_f3_case_parses_twice_the_same`. وQA: `Q::test_determinism_independent_parsers_agree_on_every_shape`، `Q::test_determinism_the_order_of_parsing_changes_nothing`، `Q::test_determinism_the_tally_does_not_depend_on_token_order` | ✅ |
| NFR‑F3‑3 | وحدة | `R::test_nfr_f3_3_f3_rules_need_no_code`، `test_electrical_shared_kks.py::test_f3_nfr_3_a_part_separator_is_data`. وQA: `Q::test_the_cap_is_the_rules_own_value`، `Q::test_evidence_the_level_is_the_rules_own_value`، `Q::test_evidence_the_non_evidence_suffixes_are_the_rules_own_value` | ✅ |
| NFR‑F3‑4 | وحدة | `test_api_electrical_router.py::test_f3_ac_4_opt_in_wire_pattern_names_are_accepted` (`.*` مرفوض 422)، `test_electrical_shared_rules.py:410` (`wire_number_patterns=(".*",)` مرفوض)، `test_electrical_f2_qa.py:335`. وQA: `Q::test_deviation_7_*` (الوحدات تُقارن بالعضوية) | ✅ |
| NFR‑F3‑5 | وحدة (QA) | `Q::test_a_range_over_the_cap_generates_nothing`، `Q::test_the_cap_is_checked_before_a_member_is_read`، `Q::test_a_text_over_the_length_bound_is_no_identifier`، `Q::test_hostile_texts_inside_the_length_bound_are_cheap`، `Q::test_split_candidates_is_linear_on_a_huge_word` | ✅ |
| NFR‑F3‑6 | يدوي | = F3‑AC‑28 | ⏳ يدوي (المراجع) |

**الانحرافات التسعة التي ذكرها المطوّر عن التصميم:** كلها سليمة ومغطاة.

| # | الانحراف | الحكم | اختبار |
|---|---|---|---|
| 1 | `page_column` يرفض الصفحة 0 أيضاً | سليم: نفس منطق gap 9 (`0.7` درجة دقة لا سلك) | `Q::test_deviation_1_page_zero_is_no_page_wire`، `Q::test_deviation_1_other_pages_stay_wires_when_named` |
| 2 | `primed` وسيط مفتاحي: الشرطة العليا للأطراف والجسور فقط | سليم: `KM1:13'` غير صالح، و`-K5:13'` و`37'` و`A1'` لا معرّف | `Q::test_deviation_2_*` (3 اختبارات) |
| 3 | علامة اقتباس افتتاحية تُغلق القطعة، و`1X-37'` تُبقي الشرطة | سليم | `Q::test_deviation_3_quotes_and_primes` (15 صفاً) |
| 4 | بند لاحق مجرد يرث رأس الأول إن كان للأول نقطة اتصال | سليم: `QS7,8` لا يرث (لا نقطة) | `Q::test_deviation_4_*` |
| 5 | `38Y-43Y` بلا النمط المسمّى مجموعة غير صالحة `group.member_unreadable` | سليم (فشل آمن: تُبلَّغ ولا تُخمَّن) | `Q::test_deviation_5_*` |
| 6 | مفاتيح البحث للمجموعة = مفاتيح أعضائها | سليم، مع ملاحظة M‑2 أدناه | `Q::test_deviation_6_*` |
| 7 | وحدات `[ratings]` من 1 إلى 5 أحرف ASCII، و`read_rating` يعيد تهجئة القواعد | سليم | `Q::test_deviation_7_*` (5 اختبارات) |
| 8 | `MAX_KKS_PARTS` يبقى 8 | سليم | `Q::test_deviation_8_the_kks_part_bound_stays_eight` |
| 9 | `KksRules.shortest` باقٍ بلا استعمال | سليم | `Q::test_deviation_9_the_near_miss_gate_reads_its_own_key_not_shortest` |

**الحالات الحدّية المطلوبة (كلها في `Q`):** سقف 64 وفحصه قبل التوليد (مع تجسيس `_member` لإثبات الترتيب)، وقوائم ونطاقات عدائية بطول 199 حرفاً، ونطاقات معكوسة وصفرية الطول، وعلامات CJK (`，`، `、`، `﹑`، `､`، `。`، `；`، الأقواس والاقتباسات الكاملة العرض)، والطيّ كامل العرض، وقيم التقدير بجوار المعرّفات، وANSI بفئة وبلا فئة وبتلميح وبلا تلميح، ومراجع الصفحة 0، وقاعدة الدليل من F2 (`non_evidence_suffixes` و`evidence_min_level` بقيمهما من القواعد)، والحتمية، وترتيب `_tokens` وتقطيع الكلمة الطويلة.

## 2. نتائج بوابات المشروع

كلها من `/home/AIZZAK` على الفرع `feat/electrical-drawings-f3-rules`، قبل الإيداع.

```
$ .venv/bin/ruff format --check .
1018 files already formatted

$ .venv/bin/ruff check .
All checks passed!

$ .venv/bin/mypy src
Success: no issues found in 555 source files

$ .venv/bin/lint-imports
Agents never import API or Infrastructure KEPT
Infrastructure imported only by Composition Root KEPT
Framework does not import outer layers KEPT
WireTwin sections are independent and shared is the floor KEPT
Contracts: 9 kept, 0 broken.

$ .venv/bin/pytest -rs tests/unit tests/architecture tests/eval
9735 passed, 37 skipped, 1 xfailed, 7 warnings in 290.17s (0:04:50)
   (الـ37 تخطٍّ: 19 بسبب `RUN_EDGE_FILES_TEST=1` و18 بسبب `RUN_ELE_PARSE_CONTAINER_TEST=1`، وكلها اختيارية بتصميمها وتحتاج حاويات.)
   (الـ xfailed الواحد هو QA‑F3‑1 أدناه، `strict=True`: يفشل البناء إن صُلح الخطأ، فيُنزَع الوسم عندها.)

$ .venv/bin/python -m tests.eval.electrical.m04_identifiers --out <scratch>/m04.json
M-04 = 1.0000 over 286 cases

$ deploy/prometheus/test-rules.sh
لم يُشغَّل: يحتاج Docker وكُلّفتُ بعدم لمس Docker ولا الصور. فرق الفرع لا يمسّ `deploy/` ولا `alerts.yml` (فرق `docs/` و`deploy/` سطر واحد في `docs/design/openapi.yaml`)، فلا أثر محتمل. تشغّله CI.
```

لم أشغّل `tests/integration` (المنصّة الحيّة مستعملة). لا مستودع جديد ولا ترحيل في الفرع (`git diff --stat` بلا `migrations/`)، فلا اختبار RLS جديد.

## 3. الاختبار الاستكشافي

الأمثلة مخترعة؛ «iec» = IEC 81346 وحده، و«cn» = أكواد الحروف الصينية.

| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| سقف المجموعة بحدّه | `QS1-QS64` و`QS0-QS63` و`X7:1`+`,1`×63 و`…AB131`+`/2`×63 | صالحة، 64 عنصراً | كما المتوقع | ✅ |
| فوق السقف | `QS1-QS65`، `QS0-QS9999`، `X7:1`+`,1`×97، `…AB131`+`/2`×70 | `group.too_many_members` وبلا عناصر | كما المتوقع، ولا قراءة لعضو مولَّد (تجسيس) | ✅ |
| نص أطول من 200 حرف | قائمة بـ100 بند، ونطاق مركّب | لا معرّف | `None` | ✅ |
| نطاق معكوس وصفري | `QS9-QS7`، `QS7-QS7`، `QS7-`، `-QS9`، `QS7-QS9-QS11` | لا مجموعة | لا مجموعة | ✅ |
| نطاق بحشو أصفار | `QS07-QS10`، `QS007-QS010` | حشو بعرض أول رقم | كما المتوقع | ✅ |
| فواصل CJK | `X7:41、42`، `，`، `﹑`، `､`، وكامل العرض `Ｘ７：４１，４２` | شكل قانوني واحد والأصل محفوظ | كما المتوقع | ✅ |
| فاصل مضاعف | `X7:41、、42` | `group.empty_member` | كما المتوقع | ✅ |
| كلمات عربية | `مفتاح`، `١٢٣`، `(١٢٣)` | لا معرّف / تمرّ كما هي | `١٢٣` تُطوى إلى سلك `123` (سلوك قبل F3 ولم يتغيّر) | ✅ |
| كلمة من مليون حرف | `_tokens` على `A`×10⁶، `,`×5×10⁵، `K1,K2`×10⁵ | عيّنة ≤ 200 وكل رمز ≤ 32، في أقل من 10 ث | كما المتوقع | ✅ |
| قيمة تقدير بجوار معرّف | `QS7 37kV` (مسافة) / `QS7,37kV` (قائمة) | القيمة ليست معرّفاً في الحالتين | الأولى ✅. **الثانية: `37kV` تُقرأ `letter_code` صالحاً** | 🔴 QA‑F3‑1 |
| وحدة بحالة غير المكتوبة | `37kv`، `37KV`، `3ka` | رمز حروف (حسب التصميم §3.5) | `letter_code` | ✅ |
| ANSI بلا فئة، مع/بلا تلميح | `8P`، `16F`، `14B6`، `38Y` | بلا تلميح: لا معرّف. بتلميح: `ansi_device` بلا فئة | كما المتوقع | ✅ |
| نمط سلك مسمّى مع ANSI | `38Y`، `59Y`، `87T`، `51N` بـ`digits_letter` | `wire_number` ببديل `ansi_device`؛ و`50BF` و`59G2` يبقيان جهازاً | كما المتوقع | ✅ |
| الصفحة 0 | `0.7`، `/0.7`، `0/3`، `0.6E`، `=A1/0.7`، `0.7-1` (مع `page_column`) | لا مرجع ولا سلك | كما المتوقع | ✅ |
| الدليل (D‑36) | `32A`، `32A,33A`، `20XYZ`×30، `87T,50BF,51N`، `…AB131/2/3`، حرف كيريلي مشابه | `A` ولا KKS مستوى 1 ولا مشابه: لا دليل. والباقي دليل | كما المتوقع، وقيمتا `evidence_min_level` و`non_evidence_suffixes` من القواعد | ✅ |
| حرف كامل العرض في وسم KKS | `ＸＹＺ…` مطوياً | يُطوى NFKC فيُعدّ دليلاً | يُعدّ (سلوك F2) | ✅ |
| حتمية | 4 محلّلين مستقلين، وترتيب مبعثر، و`tally` معكوس | تساوٍ حرفي | كما المتوقع | ✅ |
| قائمة بأوّلها نقطة وبند مجرد غير تسمية | `X7:41,4$`، `X7:41,1234567` | `group.member_unreadable` | كما المتوقع | ✅ |
| قائمة بنقاط ذات شرطة عليا | `1X-37',38'` | قراءة العضو | `group.member_unreadable` (فشل آمن، لا تخمين) | ⚠ ملاحظة M‑1 |
| مفاتيح مجموعة بـ64 عضواً | `QS1-QS64` | مفتاح الشكل القانوني ثم الأعضاء | القطع عند 64 (`MAX_GROUP_KEYS`) يُسقط مفتاح `QS64` | ⚠ ملاحظة M‑2 |

**ملاحظات ليست أخطاء (قرار تصميم، للمنسّق):**
- **M‑1:** `1X-37',38'` مجموعة غير صالحة بينما `1X-37',38` صالحة: بند لاحق بشرطة عليا ليس تسمية عادية (الانحراف 2 يحصر `primed` في الأطراف والجسور). فشل آمن، ولا إصلاح مطلوب إلا إن رغب المالك بقراءته.
- **M‑2:** مجموعة بـ64 عضواً متمايزاً تفقد مفتاح عضوها الأخير لأن الشكل القانوني يستهلك واحداً من حدّ الـ64 (`parser.py:94` و`:296`). الاسترجاع بالمجموعة ما زال ممكناً، لكن البحث بـ`QS64` وحده لا يجدها. أثره ضئيل، والحدّ مقصود.
- **M‑3:** `X7:37kV` طرف صالح (نقطته `37KV`)، و`X7:41,abc` تقرأ `abc` نقطة بوصفها تسمية صالحة من 1 إلى 6 أحرف. هذا حرفياً ما يقوله §3.6 (كل «تسمية صالحة» ترث)، لكن النثر بعد قائمة أطراف قد يُقرأ نقطة. مخاطرة إيجابيات كاذبة ضئيلة (يلزم نص `X7:41,` ثم كلمة قصيرة)، وتسجَّل لـ INC‑2.

## 4. الأخطاء المكتشفة

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| QA‑F3‑1 | **حاجز قيم التقدير يضيع داخل القوائم.** `37kV` وحدها لا معرّف (`None`)، لكن حين تكون بنداً في قائمة تُقرأ `letter_code` صالحاً (`37KV`، مرحّل جهد) وتدخل مفاتيح البحث والعدّ والدليل. السبب: `_list_group` و`_suffix_group` تقسم الشكل القانوني *المحوَّل إلى حروف كبيرة*، فيصل `_member` النص `37KV`، و`read_rating` (`ratings.py`) يطابق الوحدات التي تقابل رمز حروف بحالتها المكتوبة وحدها (`kV`)، فيُقرأ `37KV` رمز حروف بدل أن يُحجب. يصيب `kV` و`kA` وحدهما (غيرهما يطابق بأي حالة). المخالف: FR‑ID‑20 («قيمة التقدير ليست معرّفاً أبداً»)، وF3‑AC‑7. | **minor** (يلزم قائمة بفاصلة تخلط تقديراً بمعرّف، وهذا نادر؛ وأثره قراءة زائفة واحدة صالحة لا فقد بيانات). وأرفعها إلى major إن أظهر القياس 4 (F3‑AC‑29) تكراراً لهذا الشكل في رسم حقيقي. | في CN أو الكل: `parser_for(Convention.CN_LETTER_CODES).parse("QS7,37kV")`. المتوقع: لا بند `37KV` معرّفاً (`None` للعضو، فتصير المجموعة غير صالحة بـ`group.member_unreadable`)، أو لا مجموعة. الفعلي: `identifier_group` صالحة، أعضاؤها `QS7` و`37KV` (`letter_code`). وكذلك `37kV,QS7` و`QS7,QS8,37kV`. الاشتباه: `src/app/modules/electrical/shared/parser.py:231-256` (`_list_group`، البند يصل `_member(item)` من `canonical` المكبَّر) و`:258-274` (`_suffix_group`) و`:210-212` (`_member` يمرّر `text` المكبَّر إلى `_is_rating`). الإصلاح الممكن: تجزئة البنود من الأصل بعد الطيّ دون تكبير (`folded`) أو فحص `read_rating` على النص المطوي من الأصل لكل بند. **المالك: backend.** مسجَّل باختبار `xfail(strict=True)`: `Q::test_qa_f3_1_a_rating_inside_a_list_is_no_identifier`. |

لا أخطاء blocking ولا major.

## 5. الحكم

**PASS** — كل بوابات `CLAUDE.md` الممكن تشغيلها خضراء (ruff وmypy وlint-imports و9735 اختباراً و M‑04 = 1.0000 على 286 حالة)، وكل AC آلي ✅ بتغطية اختبار مسمّى، والثلاثة اليدوية (F3‑AC‑28 للمراجع، وF3‑AC‑29 وF3‑AC‑30 للمنسّق) والبوابة 6 (Docker) خارج نطاقي بالتعليمات وبسبب مكتوب، وNFR‑F3‑1 مقبول بقرار المالك (tally مع قوائم 1.8× مسجَّل). خطأ واحد minor (QA‑F3‑1) ولا blocking ولا major.

</div>
