<div dir="rtl">

# قبول مالك المنتج: F3، قواعد المعرّفات (المجموعات، وقيم التقدير، وANSI بلا سياق، والتقطيع، وKKS، وعيّنة الاكتشاف)

- **الفرع والنطاق:** `feat/electrical-drawings-f3-rules`، و`HEAD` = `44dfdb5` فوق `master` = `281a227`. والتغيير `git diff 281a227..HEAD`: 15 إيداعاً، و42 ملفاً (+4253/−144). وفيها البناء `50c212e`..`51b9024`، وQA `0dfd88e`، والإعادة 1 `86b0cfc` و`75db4eb`، وإعادة الاختبار 1 `9c87910`، والإعادة 2 `7653020`، وإعادة الاختبار 2 `44dfdb5`.
- **المدخلات:**
  - `design.md` 1.0 §5.1 (`F3‑AC‑1..30` وNFR‑F3‑1..6)، و§5.4، و§8.
  - `test-plan.md` §1..§7: PASS ثلاث مرات.
  - `review.md`: FAIL، ثم PASS في «إعادة المراجعة 1» و«إعادة المراجعة 2».
  - `security-review.md`: PASS، ثم PASS في «إعادة الفحص 1» و«إعادة الفحص 2».
  - `status.md`: الصفوف 4ل..9م′، وقرارات المالك في 2026-11-18 و2026-11-20.
- **الأولوية:** لا يكتب `design.md` أولوية لكل معيار، فعاملتُ المعايير الثلاثين كلها معاملة Must. فهي شرط قبول البناء، ومتطلباها الجديدان FR‑ID‑19 وFR‑ID‑20 من صنف M في SRS 0.21.
- **المستودع عام:** كل مثال هنا مخترع من `design.md`، أو داخل استثناءات `F2‑AC‑20` المكتوبة. ولم أنقل من الحقيقة المرجعية أي نص.

## ما تحققتُ منه بنفسي (2026-11-20، على `44dfdb5`)

1. **وجود كل اختبار تسمّيه المصفوفة:** بحثتُ بـ`grep -n "def <الاسم>"` عن 62 اسماً من `test-plan.md` §1..§7، فوجدتُها كلها. وللأسماء ذات النجمة وجدتُ العدد المكتوب: `test_f3_ac_11_*` و`_12_*` و`_13_*` ثلاثة لكلٍّ، و`_14_*` أربعة، و`test_electrical_shared_kks.py::test_f3_ac_21_*` خمسة.
2. **اختبارات F3:** شغّلتُ 16 ملفاً:
   - `test_electrical_f3_rules.py` و`_f3_groups.py` و`_f3_tokens.py` و`_f3_qa.py`.
   - و`test_electrical_shared_ansi.py` و`_parser.py` و`_kks.py` و`_terminals_wires.py` و`_rules.py`.
   - و`test_electrical_profile_detection.py`، و`test_api_electrical_router.py`، و`test_ele_parse_service.py`.
   - و`test_electrical_f2_qa.py` و`_f2_profile.py` و`test_electrical_characterization.py` و`test_api_conventions.py`.
   - **النتيجة: 1446 نجحت، و0 فشل، و0 تخطٍّ، و0 `xfail`، ورمز الخروج 0.** ولا وسم `xfail` في ملف QA.
3. **`tests/architecture`:** رمز الخروج 0.
4. **M‑04:** `python -m tests.eval.electrical.m04_identifiers --out <المجلد المؤقت>` يعطي `M-04 = 1.0000 over 286 cases`.
5. **البوابات الثابتة على شجرة العمل:** كلها خضراء:
   - `ruff format --check`: 1018 ملفاً.
   - `ruff check`.
   - `mypy src`: 555 ملفاً.
   - `lint-imports`: «9 kept, 0 broken».
6. **ما لا يمسّه الفرق:**
   - الفرق خارج `src/` و`services/` و`tests/` و`docs/delivery/` سطر وصف واحد في `docs/design/openapi.yaml`.
   - ولا يمسّ `deploy/` ولا `.importlinter` ولا `.github/` ولا `docker-compose.yml` ولا `.env.example` ولا `pyproject.toml` ولا `migrations/`.
   - ولا يمسّ `drawings/domain/profile.py`، فالبصمة لا تتغيّر.
7. **الإصدارات في الشيفرة:**
   - `SHARED_VERSION` = `1.4.0`.
   - و`identifiers.toml` `version` = `1.2.0`.
   - و`PIPELINE_VERSION` = `1.2.0`.
   - و`SERVICE_VERSION` = `1.1.0`.
8. **NFR‑F3‑1:** قِسته بنفسي بعد الإعادة 2، والتفصيل في قسمه أدناه.
9. **F3‑AC‑28 على ما لم يشمله بحث المراجع:** بحث المراجع في إعادة المراجعة 2 شمل `9c87910..7653020` وحده. فبحثتُ أنا في ثلاثة مواضع:
   - الأسطر المضافة في `7653020..44dfdb5` (162 سطراً).
   - وتعديل `status.md` غير المودَع.
   - و`review.md` و`security-review.md` غير المودَعين.
   - **الطريقة:** سكربت في المجلد المؤقت خارج المستودع، بـ`python -I`. يقارن كل كلمة في هذه الأسطر بكل نص في `/home/ele-data/labels/` (17 ملف JSON و5 ملفات Markdown)، ويطبع العدد والشكل المجرد وحدهما. وقراءة المجلد للمقارنة مسموحة بقرار المالك (SRS D‑34، 2026-11-07).
   - **النتيجة: 0 تطابق خارج الاستثناءات المكتوبة.**
     - في `44dfdb5`: لا تطابق فيه رقم. والتطابقات كلمات إنجليزية عامة ومفاتيح مخطط البيانات، مثل «electrical» و«terminal» و«identifiers».
     - في الوثائق غير المودَعة: التطابقات التي فيها رقم كلها داخل الاستثناء: الطرف `1X-25`، والتيار المقنّن والأقطاب، وأسماء أجهزة عامة من حرف ورقم. والباقي معرّفات القرارات (`F3`، `F3‑Q3`)، وتواريخ.
10. **F3‑AC‑30:** فحصتُ إيداع وثائق التصميم `f61599b` على `master` (PR #47)، والتفصيل في صفّه.

## المعايير مقابل الدليل

| AC | الأولوية | الدليل (اختبار، أو مخرج، أو قسم مراجعة) | الحكم |
|---|---|---|---|
| F3‑AC‑1 | Must | `tests/unit/test_electrical_f3_rules.py:42` `test_f3_ac_1_ansi_without_a_class_needs_context`، و`:49` `…_a_compound_of_numbers_outside_the_table_is_a_cross_reference` (`23/4`)، و`:60` `…_16f_is_invalid_with_context_for_its_suffix`. ومعها اختبارات QA: `test_electrical_f3_qa.py:746` `test_ansi_without_a_class_needs_a_hint`، و`test_ansi_with_a_class_reads_with_and_without_a_hint`، و`test_ansi_that_is_no_device_even_with_a_hint` | ✅ |
| F3‑AC‑2 | Must | `test_electrical_f3_rules.py:66` `test_f3_ac_2_classed_ansi_reads_as_in_f2`، و`:75` `test_f3_ac_2_the_m04_cases_change_only_at_c226`، و`test_electrical_shared_parser.py:204` `test_f2_ac_7_ansi_on_changes_only_the_listed_ambiguities`. والمراجع تحقق أن فرق ملف الحالات لا يحذف إلا سطور `c226` وتعليق الرأس (`review.md`، «حالات M‑04») | ✅ |
| F3‑AC‑3 | Must | `test_electrical_f3_rules.py:101` `test_f3_ac_3_a_named_vendor_wire_pattern_wins_over_ansi`، و`:124` `…_letter_digits_is_read_only_when_named`، و`:131` `test_opt_in_patterns_never_read_unnarrowed` (كل حالة M‑04 بلا `select`). ومعها اختباران في ملف QA | ✅ |
| F3‑AC‑4 | Must | `tests/unit/test_api_electrical_router.py:478` `test_f3_ac_4_opt_in_wire_pattern_names_are_accepted`: الحفظ إصداراً، والاسم المجهول و`.*` يعطيان 422 `electrical.invalid_profile` بلا صدى، ولا يُكتب إصدار (`security-review.md`، قائمة الفحص) | ✅ |
| F3‑AC‑5 | Must | `test_electrical_f3_rules.py:146` `test_f3_ac_5_a_digits_first_terminal_is_no_ansi_under_nfpa` | ✅ |
| F3‑AC‑6 | Must | `tests/unit/test_electrical_shared_ansi.py:181` `test_f3_ac_6_a_suffix_with_a_trailing_digit`. ومعه صفّا `14B6` و`59G2-1` في اختبارَي QA | ✅ |
| F3‑AC‑7 | Must | `test_electrical_f3_rules.py:169` `test_f3_ac_7_ratings_are_no_identifiers`، و`:174` `…_a_unit_that_is_a_letter_code_matches_as_written`، و`:185` `…_a_rating_is_a_value_of_its_own`. **وداخل القوائم والنطاقات** (QA‑F3‑1 والحاجبة #1): `test_electrical_f3_qa.py:683` `test_qa_f3_1_a_rating_inside_a_list_is_no_identifier` (صار اختباراً عادياً)، و`:1038` `test_rework_a_rating_in_a_list_is_unreadable_for_every_unit` وخمسة غيره، و`test_electrical_f3_groups.py::test_rework_1_*`. ومع حرف تحكّم في الآخر (#2): `test_rework_2_the_fast_path_agrees_with_the_slow_one` | ✅ |
| F3‑AC‑8 | Must | `test_electrical_f3_rules.py:194` `test_f3_ac_8_voltage_labels_win`، و`:199` `…_a_voltage_label_the_profile_added_stays_a_voltage` | ✅ |
| F3‑AC‑9 | Must | `tests/unit/test_electrical_shared_rules.py:354` `test_f3_ac_9_units_are_data_and_checked`، و`:460` `test_f3_ac_9_a_unit_the_file_lacks_is_no_barrier` | ✅ |
| F3‑AC‑10 | Must | `test_electrical_f3_rules.py:216` `test_f3_ac_10_page_zero_is_no_page`، و`:230` `…_other_pages_read_as_before`. ومعها ثلاثة اختبارات QA للصفحة 0 في كل صيغة. وحالات §5.3 في M‑04 | ✅ |
| F3‑AC‑11 | Must | `tests/unit/test_electrical_f3_groups.py:48` `test_f3_ac_11_*` (3)، ومعها اختبارات QA للفواصل `，` و`、` و`﹑` و`､` والعرض الكامل | ✅ |
| F3‑AC‑12 | Must | `test_electrical_f3_groups.py:81` `test_f3_ac_12_*` (3). **وشرط «ولا يعود طرفاً» بعد نفاد الميزانية** (ر‑1، S‑6): `:355` `test_rework_2_s6_a_range_the_budget_refused_never_reads_as_a_terminal`، و`test_electrical_f3_qa.py:1255` `test_rework2_a_refused_group_falls_through_to_no_kind` (63 حالة). وQA يذكر أن 25 حالة منها تفشل على `9c87910` (`test-plan.md` §7) | ✅ |
| F3‑AC‑13 | Must | `test_electrical_f3_groups.py:117` `test_f3_ac_13_*` (3)، ومعها `test_a_malformed_suffix_list_is_no_group` و`test_a_malformed_backslash_list_is_no_group` | ✅ |
| F3‑AC‑14 | Must | `test_electrical_f3_groups.py:149` `test_f3_ac_14_*` (4). ومعها في ملف QA: `:105` `test_a_range_over_the_cap_generates_nothing`، و`:112` `test_the_cap_is_checked_before_a_member_is_read` (بالتجسيس على `_member`)، و`:181` `test_hostile_texts_inside_the_length_bound_are_cheap`، و`test_a_flood_of_hostile_texts_is_linear_enough` | ✅ |
| F3‑AC‑15 | Must | `test_electrical_f3_groups.py:198` `test_f3_ac_15_the_readings_of_today_are_no_group`. وM‑04 = 1.0000، و`c226` وحدها تغيّرت (`F3‑AC‑2`) | ✅ |
| F3‑AC‑16 | Must | `test_electrical_f3_groups.py:205` `test_f3_ac_16_the_tally_counts_the_members_of_a_group`، و`:211` `…_the_characterization_carries_the_members`. ومعها `test_tally_counts_each_member_and_the_tokens_it_saw` وحدود الميزانية في ملف QA | ✅ |
| F3‑AC‑17 | Must | `tests/unit/test_electrical_f3_tokens.py:39` `test_f3_ac_17_split_candidates`، و`:43` `…_a_list_is_one_candidate_and_marks_at_the_end_are_dropped`، و`:58` `…_the_split_is_linear`. ومعها حالات QA لعلامات CJK والكلمات العربية | ✅ |
| F3‑AC‑18 | Must | `test_electrical_f3_tokens.py:72` `test_f3_ac_18_an_enclosed_number_is_no_wire`، و`:79` `…_a_full_width_digit_is_still_a_digit` | ✅ |
| F3‑AC‑19 | Must | `tests/unit/test_electrical_shared_terminals_wires.py:224` `test_f3_ac_19_a_prime_after_the_terminal_number_is_part_of_it`. ومعها ثلاثة اختبارات `test_deviation_2_*` في ملف QA | ✅ |
| F3‑AC‑20 | Must | `test_electrical_shared_terminals_wires.py:176` `test_f3_ac_20_letters_first_conventions_are_data`، و`:214` `…_a_bad_letters_first_entry_is_a_rule_error` | ✅ |
| F3‑AC‑21 | Must | `tests/unit/test_electrical_shared_kks.py:216` `test_f3_ac_21_*` (5)، و`test_electrical_shared_rules.py:423` `test_the_kks_f3_keys_are_required_and_checked`. ومعها `test_evidence_*` في ملف QA: `20XYZ` بالمستوى 1 ليس دليلاً | ✅ |
| F3‑AC‑22 | Must | `test_electrical_f3_rules.py:242` `test_f3_ac_22_location_references`، و`tests/unit/test_electrical_profile_detection.py:195` `test_location_formats_keep_auto` | ✅ |
| F3‑AC‑23 | Must | `tests/unit/test_ele_parse_service.py:187` `test_f3_ac_23_digit_tokens_fill_the_sample_first`، ومعها خمسة اختبارات `test_tokens_*` في ملف QA | ✅ |
| F3‑AC‑24 | Must | `test_ele_parse_service.py:203` `test_f3_ac_24_a_long_word_is_split_before_the_bound`. ومعها `test_rework_a_long_word_never_overfills_the_sample` (S‑3) واختبارات `test_tokens_*` للكلمة الطويلة | ✅ |
| F3‑AC‑25 | Must | `test_electrical_shared_rules.py:36` `test_the_rules_file_is_packaged_and_versioned` (`1.2.0` و`1.4.0` والأنواع الاثنا عشر)، واختبارا الإصدار في `test_ele_parse_service.py` و`test_electrical_f2_profile.py`، و`test_electrical_f2_qa.py:418` `test_an_existing_profile_keeps_its_body_and_its_fingerprint` (البصمة). والإصدارات الأربعة في الشيفرة كما في البند 7 أعلاه | ✅ |
| F3‑AC‑26 | Must | `test_api_electrical_router.py:1082` `test_every_electrical_schema_has_the_published_properties_and_required_set`، و`:1065` `test_the_profile_body_description_names_every_profile_field`. وفرق `openapi.yaml` سطر وصف واحد بالنص الذي يطلبه §3.1، و`profile.py` لم يتغيّر، فـ`_TOP_KEYS` كما هو (البند 6 أعلاه) | ✅ |
| F3‑AC‑27 | Must | `test_electrical_shared_parser.py:71` `test_f3_ac_27_every_f3_appendix_example_has_an_appendix_a_case`. وتشغيلي للأداة: M‑04 = 1.0000 على 286 حالة، أي 236 قائمة و50 جديدة (`c237`..`c286`). والحالة الفاشلة تُفشل الاختبار في CI | ✅ |
| F3‑AC‑28 | Must (يدوي) | **بحث المراجع** بالقائمة الحقيقية ثلاث مرات (`review.md`: «F3‑AC‑28: البحث عن الخصوصية»، ثم القسم نفسه في الإعادتين). شمل `281a227..7653020`، والنتيجة 0 تطابق خارج الاستثناءات المكتوبة. **والأمن** قارن الأسطر المضافة حتى `9c87910` (`security-review.md` §7 و«إعادة الفحص 1»)، فلم يجد وسم KKS حقيقياً ولا اسم ملف. **وبحثي أنا** على `44dfdb5` والوثائق غير المودَعة (البند 9 أعلاه): 0 تطابق خارج الاستثناءات | ✅ |
| F3‑AC‑29 | Must (قياس) | **لم يُجرَ بعد، وهذا بالتصميم لا فشل.** يقول `design.md` §5.4: «يجريها المنسّق بعد الـPR وقبل الدمج»، والتقرير `inc-1/f3/measure-4.md` غير موجود الآن. فلا دليل له اليوم، ولا يُحكم عليه بـ✅. **وهو الشرط 1 قبل الدمج أدناه**، كما كانت شروط F2 قبل الدمج | ⏳ شرط قبل الدمج |
| F3‑AC‑30 | Must (مراجعة) | إيداع `f61599b` على `master` (PR #47) يغيّر `docs/requirements.md` و`docs/hld.md`، ومعهما `design.md` و`status.md`. **SRS 0.21:** الرأس 0.21، و«D-01..D-37 كلها محسومة» (S‑1). وصفّ 0.21 في سجل الإصدارات (S‑2). وصفّ D‑37 واحد (S‑11). وFR‑ID‑19 وFR‑ID‑20 جديدان (S‑9، S‑10). وصفّ «قائمة أو نطاق» في الملحق أ، ومعه أمثلة D‑37 في صفوف الطرف والمرجع المتقاطع وKKS وANSI (S‑12). و«FR-ID-01..20» لـI‑04 (S‑13). **والأسطر المتغيّرة في الفرق** صفوف FR‑ID‑03 و04 و06 و09 و15 و16، وهي S‑3..S‑8، والصفّان الجديدان. فلا يمسّ FR‑ID‑01..02 ولا 05 ولا 07..08 ولا 10..14 ولا 17..18. **HLD 0.20:** الرأس وسجل الإصدارات (H‑1)، و§5.4 (H‑2..H‑4)، وصفّ STG‑01 (H‑5)، وصفّ INC‑2 (H‑6)، وRSK‑28 وRSK‑29 (H‑7). **RTL:** `hld.md` و`design.md` داخل `<div dir="rtl">`. أما `requirements.md` فلم يكن داخلها منذ أُنشئ قبل F3 (`7d16970`)، ولم يغيّر F3 ذلك، وقبله F2 كذلك (الملاحظة 7 أدناه). **وفرع البناء لا يمسّ `docs/delivery/electrical-drawings/docs/`** (0 ملف) | ✅ |

## المتطلبات غير الوظيفية

| NFR | الدليل | الحكم |
|---|---|---|
| NFR‑F3‑1 الأداء | القسم التالي. ويتحقق الحدّ 1.2 ضعف حيث أبقاه المالك: `parse`، و`tally` بلا قوائم. ويُسجَّل `tally` بقوائم و`_tokens` قياساً فقط بقرارَي المالك في 2026-11-20 | ✅ بقرار المالك |
| NFR‑F3‑2 الحتمية | `test_electrical_f3_rules.py:251` `test_nfr_f3_2_every_f3_case_parses_twice_the_same`، و`test_electrical_f3_qa.py:894` `test_determinism_independent_parsers_agree_on_every_shape`، و`test_determinism_the_order_of_parsing_changes_nothing`، و`test_determinism_the_tally_does_not_depend_on_token_order`. والحدّ المعروف: بعد نفاد الميزانية يتبع العدّ ترتيب الرموز، وقبله المراجع (`review.md`، «الانحراف: `TALLY_MEMBER_BUDGET` ثابت») | ✅ |
| NFR‑F3‑3 القواعد بيانات | `test_electrical_f3_rules.py:261` `test_nfr_f3_3_f3_rules_need_no_code`، و`test_electrical_shared_kks.py:274` `test_f3_nfr_3_a_part_separator_is_data`، وطفرات `units` و`max_members` و`letters_first` و`near_miss_min_length` (`review.md`، «التغطية») | ✅ |
| NFR‑F3‑4 لا نص مستخدم في تعبير | `F3‑AC‑4` (`.*` يعطي 422)، و`security-review.md` §1: الأسماء تُختار بالاسم، والوحدات والفواصل تُقارن بالعضوية | ✅ |
| NFR‑F3‑5 الحدود | `test_a_range_over_the_cap_generates_nothing`، و`test_the_cap_is_checked_before_a_member_is_read`، و`:192` `test_a_text_over_the_length_bound_is_no_identifier`، و`:380` `test_split_candidates_is_linear_on_a_huge_word`. و`security-review.md` §1 و§2: لا ReDoS، وأسوأ نمط 1.2 ms على 100 ألف حرف. وميزانية 2,000 عضو لكل نداء `tally` (S‑2، مغلقة) | ✅ |
| NFR‑F3‑6 الخصوصية | `F3‑AC‑28` | ✅ |

## NFR‑F3‑1: الأداء

- **الحدّ المكتوب** (`design.md` §5.1): 1.2 ضعف `master` لـ`parse` على حالات M‑04، ولـ`tally` على 1,000 رمز فيها 10% قوائم ونطاقات، ولـ`_tokens` على صفحة من 5,000 كلمة.
- **قرارا المالك في 2026-11-20 («اقبل وسجّل»):**
  - يبقى الحدّ لـ`parse` ولـ`tally` بلا قوائم.
  - ويُسجَّل `tally` على خليط فيه 10% قوائم قياساً فقط.
  - ويُسجَّل `_tokens` (4.3 ضعف) قياساً فقط، لأنه أثر F3‑Q4 (ج) ويجري داخل الابن المحدود.
- **الأرقام المسجَّلة قبلي:**

| القائس | `parse` | `tally` بلا قوائم | `tally` بـ10% قوائم | `_tokens` |
|---|---|---|---|---|
| المطوّر، البناء (7ل) | 0.9× و0.95× | 1.0× | 1.8× | نحو 4× |
| المطوّر، الإعادة 1 (9ل′) | 0.97× .. 1.0× | 1.03× | 1.9× | 4.3× |
| المراجع (`review.md` #3) | — | — | — | 4.3× (19 إلى 81 µs) |

- **قياسي على `44dfdb5`، بعد الإعادة 2:**
  - **لماذا قِسته:** الإعادة 2 غيّرت `parser.py` بعد آخر أرقام مسجَّلة.
  - **الطريقة:**
    - سكربت في المجلد المؤقت خارج المستودع، بـ`python -I`.
    - على نسختين بـ`git archive`، من `281a227` ومن `44dfdb5`، بالتناوب مرتين على المضيف نفسه.
    - `parse`: أقل متوسط من 7 جولات × 20 تكراراً، على نصوص الحالات الـ236 من `master`.
    - `tally`: على 1,000 رمز اصطناعي ببذرة ثابتة، بالقواعد المعبّأة.
    - والخليط بالقوائم فيه 10% قوائم أطراف ونطاقات أكواد وقوائم KKS لاحقة.

| القياس | `master` | الفرع | النسبة |
|---|---|---|---|
| `parse`، القواعد المعبّأة | 14.47 / 14.74 µs | 14.79 / 14.60 µs | **1.02× / 0.99×** |
| `parse`، كل الاصطلاحات مع KKS وANSI | 17.99 / 18.12 µs | 18.78 / 18.52 µs | **1.04× / 1.02×** |
| `tally`، 1,000 رمز بلا قوائم | 27.5 / 27.4 ms | 23.9 / 25.1 ms | **0.87× / 0.91×** |
| `tally`، 1,000 رمز فيها 10% قوائم | 26.4 / 26.4 ms | 31.3 / 32.0 ms | 1.18× / 1.21× (للتسجيل فقط) |

- **الحكم: محقَّق حيث أبقاه المالك.** أعلى نسبة لـ`parse` عند أي قائس 1.04×، ولـ`tally` بلا قوائم 1.03×، والحدّ 1.2.
- **`tally` بقوائم:** نسبته تتبع الخليط، من 1.2× في خليطي الأخفّ إلى 1.9× في خليط المطوّر. والسبب أن الفرع يقرأ أعضاءً لا يقرؤها `master`. ولا حدّ عليه بقرار المالك.
- **`_tokens`:** لم أقسه. وأعتمد رقم المراجع (4.3×، والفرق المطلق نحو 62 µs لكل 5,000 كلمة)، وهو مقبول بقرار المالك.
- **جسد الـPR:** تُنسخ إليه الأرقام مع وصف الخليط (الشرط 3).

## المراجعات

- **مراجعة الشيفرة (`review.md`):**
  - **الجولة الأولى: FAIL.** فيها حاجبة #1 (QA‑F3‑1 أوسع)، ومهمتان #2 و#3، وتحسينات #4..#6.
  - **إعادة المراجعة 1: PASS.** أُغلقت #1 و#2 و#4..#6، وقُبلت #3 بقرار المالك. وأُضيفت ر‑1 (مهمة) وت‑1 (تحسين).
  - **إعادة المراجعة 2: PASS.** أُغلقت ر‑1 وت‑1، ولا حاجبة ولا مهمة مفتوحة.
  - **F3‑AC‑28:** ناجح في الجولات الثلاث.
- **المراجعة الأمنية (`security-review.md`):**
  - **الجولة الأولى: PASS**، بلا حرجة ولا عالية ولا متوسطة.
  - **إعادة الفحص 1: PASS.** أُغلقت S‑1..S‑5، وأُضيفت S‑6 (إعلامية).
  - **إعادة الفحص 2: PASS.** أُغلقت S‑6، ولا ملاحظة مفتوحة.
  - **وما لم يُفحص:** لم يُعَد الفحص على `44dfdb5`، وأقبل ذلك بالاسم (الملاحظة 6 أدناه).
- **QA (`test-plan.md`):** PASS ثلاث مرات (§5، §6، §7). وآخر تشغيل كامل: 9888 نجحت و37 تخطّياً، والتخطّي للحاويات الاختيارية وحدها. وM‑04 = 1.0000، و`test-rules.sh` OK.

## الشروط قبل الدمج

1. **F3‑AC‑29، إعادة القياس 4.**
   - **من يجريها ومتى:** المنسّق، بعد فتح الـPR وقبل الدمج، كما في `design.md` §5.4.
   - **الطريقة:** ملفا الإعداد الثلاثة في §5.4، والبرامج والمخرجات خارج المستودع. والتقرير `inc-1/f3/measure-4.md` بالأعداد وبأنماط مجردة.
   - **شرط النجاح:** يتحقق كل بند من البنود السبعة في `F3‑AC‑29`.
   - **إن لم يتحقق بند:**
     - يسقط هذا القبول، ويعود العمل قبل الدمج.
     - **إلى البناء** (`backend-developer`، ثم QA والمراجعتين): إن كانت القاعدة في التصميم والشيفرة لا تحققها.
     - **أو إلى التصميم** (`software-architect`، بقرار المالك): إن كانت القاعدة نفسها لا تكفي. مثلاً نسبة ANSI الزائفة فوق 10%، أو عناصر مراجع الأطراف بقائمة دون 95%.
2. **إيداع وثائق الجودة على الفرع:** `review.md` و`security-review.md` وهذه الوثيقة، ومعها تعديل `status.md`. فالثلاثة الأولى غير مودَعة الآن، و`status.md` معدَّل في شجرة العمل. ويلحق بها `measure-4.md` بعد الشرط 1.
3. **جسد الـPR** يحمل:
   - أرقام NFR‑F3‑1 بوصف الخليط، وقرارَي المالك في 2026-11-20.
   - قائمة الاختبارات القديمة التي تغيّرت، كما يطلب `design.md` §3.4.
   - إجراء الرجوع والنشر المتدرّج في `design.md` §4.1 كما هو.
   - سطراً يقول إن إعادة بناء صورتَي `aizzak/ele-parse:dev` و`app` قرار المالك وخارج هذا الـPR.
4. **CI أخضر على الـPR:** الوظيفتان `quality` و`integration` (`docker-compose.test.yml`). فالتكامل لم يُشغَّل على هذا المضيف بالتعليمات، والفرق لا يمسّ مستودعاً ولا ترحيلاً.

## ملاحظات غير حاجبة أقبلها بالاسم

1. **`tally` بقوائم فوق 1.2×** (1.18× إلى 1.9× بحسب الخليط): مقبول تسجيلاً بقرار المالك في 2026-11-20.
2. **`_tokens` بـ4.3× (#3 في المراجعة):** مقبول تسجيلاً بقرار المالك في 2026-11-20. والكلفة المطلقة صغيرة (نحو 62 µs للصفحة)، وتجري داخل الابن المحدود.
3. **M‑1:** `1X-37',38'` مجموعة غير صالحة بـ`group.member_unreadable`، و`1X-37',38` صالحة. فشل آمن، وثمن الانحراف 2 المقبول.
4. **M‑3:** بعد قائمة أطراف قد يُقرأ نثر قصير نقطة اتصال (`X7:41,abc`)، وكذلك `X7:37kV` طرف صالح. مخاطرة إيجابيات كاذبة ضئيلة تُسجَّل لـINC‑2. وأطلب أن يعدّ القياس 4 هذا الشكل إن صادفه.
5. **الميزانية الثابتة (2,000 عضو لكل نداء `tally`):**
   - بعد نفادها يتبع العدّ ترتيب الرموز، ولا تُقرأ المجموعة المرفوضة بنوع آخر (ر‑1 مغلقة). وقبلها المراجع.
   - الصفحة العادية (عيّنة 200 رمز) لا تبلغها.
6. **لم تُعَد المراجعة الأمنية على `44dfdb5`:**
   - الإيداع اختبارات في `tests/unit/test_electrical_f3_qa.py` وسطور في `test-plan.md` وحدها، بلا `src/` ولا `services/`.
   - وقد شمله بحثي في الخصوصية (البند 9).
7. **`docs/requirements.md` بلا `<div dir="rtl">`:** هكذا منذ أُنشئ (`7d16970`)، ولم يغيّره F3، وقبله F2 في `F2‑AC‑21`. ويُصلح في جولة وثائق مستقلة إن رغب المالك.
8. **بقية S‑5 في مسار غير ASCII:** يُقرأ العضو `37KV` رمزاً، لكنه يحمل `text.non_ascii` و`valid=False`، فلا يُعدّ (`security-review.md`، «إعادة الفحص 1»).
9. **الانحرافات التسعة عن التصميم:** قبلها المراجع وQA، ومنها رفض الصفحة 0 في `page_column` والوسيط `primed` ووراثة رأس القائمة. وكذلك تعديل ستة ملفات اختبار قائمة خارج قائمة §2.2 لسبب يفرضه التصميم (`review.md`، «النطاق»).

## متابعات

| # | المتابعة | إلى | المرجع |
|---|---|---|---|
| 1 | الفجوات الثماني عشرة المؤجَّلة، ومنها نسبة المحوّل (فج‑9) والاسم من الموضع (فج‑11، فج‑28) واسترجاع النص المرسوم (فج‑26) | LLD الزيادة INC‑2 | `design.md` §2.1، وHLD 0.20 §16 |
| 2 | M‑3: النثر بعد قائمة أطراف يُقرأ نقطة | INC‑2 (سياق STG‑05) | `test-plan.md` §3 |
| 3 | ضبط `MIN_IDENTIFIERS` (20) مع M‑32 | `INC1‑R‑15` | F3‑Q5، فج‑41 |
| 4 | الأرقام بلاحقة خارج جدول الفئات (13 من 46 في جداول الدليل) تُقرأ بالسياق أو بإضافة فئتها بالبيانات | INC‑2، أو تغيير بيانات | `design.md` §1، خ‑1 |
| 5 | نسخ `design.md` §4.1 إلى `docs/deploy-server.md` أو دليل التشغيل | أول نشر خادم (قرار إنسان) | سابقة F2، المتابعة 8 |

## قرارات المالك، ولا تحجب الدمج

- **إعادة بناء الصورتين `aizzak/ele-parse:dev` و`app`، وترتيب النشر في §4.1:** قرار المالك بعد الدمج، وليست جزءاً من هذا الـPR (F3‑Q4 (ج)، و`status.md` 2026-11-18). وحتى تُبنى الصورة، تعمل `ele-parse` بالعيّنة القديمة، فلا ينكسر شيء.
- **حُسم قبل هذا القبول:**
  - F3‑Q1..F3‑Q6 (2026-11-18).
  - و«اقبل وسجّل» لـ`tally` بقوائم ثم لـ`_tokens` (2026-11-20).
  - ونطاق جولة الإصلاح 1 (2026-11-20).

## الحكم النهائي

**مقبول**، بالشروط الأربعة قبل الدمج، وأولها `F3‑AC‑29`.

- **ما تحقق:**
  - 29 من 30 معياراً ✅ بدليل مفتوح شغّلتُه أو فحصتُه: `F3‑AC‑1..28` و`F3‑AC‑30`.
  - وNFR‑F3‑1..6 ✅، وNFR‑F3‑1 محقَّق حيث أبقاه المالك.
  - و`review.md` PASS بعد إعادتين، و`security-review.md` PASS في الجولات الثلاث، وQA PASS ثلاث مرات.
  - ولا معيار مرفوض، ولا ملاحظة حاجبة أو مهمة مفتوحة.
- **ما بقي:** `F3‑AC‑29` لم يُقس بعد، لأن التصميم جعل موعده بعد الـPR وقبل الدمج، فهو ⏳ لا 🔴. ولا يُدمج الفرع قبل `measure-4.md` ناجحاً في بنوده السبعة. فإن فشل بند، يسقط هذا القبول، ويعود العمل إلى البناء أو إلى التصميم كما في الشرط 1.

</div>
