<div dir="rtl">

# قبول مالك المنتج: وسوم الملفات `PLT‑26` (`PR‑A` من `INC‑1`، ميزة `electrical-drawings`)

- **الفرع:** `feat/electrical-drawings-inc-1-pr-a` عند `8a5dfc6`، على `master@4bc6417`. تسعة إيداعات: `c12504d` و`0cd5fe6` و`ce733d0` و`2e0b7e8` و`ae13a38` و`f5c48e6` و`080199a` و`d864eb7` و`8a5dfc6`.
- **المرجع:** `docs/delivery/electrical-drawings/docs/lld-inc-1.md` 0.3 §5.1، وفيه قرارا المالك في 2026-11-01: S‑1 (الكنس عند إعادة التسمية وإعادة الوسم لملف `ready` وحده) وS‑3 (الوسوم ترفض الفئات Cc وCf وCs وCo وCn وZl وZp).
- **النطاق:** `INC1‑AC‑1` و`INC1‑AC‑2` و`INC1‑AC‑3`، وحصة `PR‑A` من `INC1‑AC‑16`. كلها بأولوية Must، لأن الـLLD لا يرتّب معاييره، فكل معيار في النطاق شرط للقبول.
- **الأدلة المقروءة:** `inc-1/pr-a/test-plan.md` (PASS، ثم PASS بعد الإعادة 1)، و`review.md` (PASS، ثم PASS في إعادة المراجعة 1)، و`security-review.md` (PASS، ثم PASS في إعادة المراجعة 1)، و`status.md` (6د..10د، وصفّا «دورات الإعادة» 1 و2).
- **ما تحقّقتُ منه بنفسي على `8a5dfc6`** (2026-11-01، بلا أي تعديل):
  - `ruff format --check .`: «870 files already formatted».
  - `ruff check .`: «All checks passed!».
  - `mypy src`: «Success: no issues found in 490 source files».
  - `lint-imports`: «Contracts: 9 kept, 0 broken.».
  - `pytest tests/unit tests/architecture tests/eval`: خرج بالرمز 0. جُمع 6,774 اختباراً، تخطّى منها 19، كلها في حزمة الحاويات الاختيارية `test_public_file_links_containers.py` (`RUN_EDGE_FILES_TEST=1`). فالناجح 6,755، كما في صف «دورات الإعادة» 2.
  - `deploy/prometheus/test-rules.sh`: «SUCCESS: 29 rules found»، ثم «test-rules: OK».
  - `git diff 4bc6417..HEAD --stat -- src/app/agents/rag_agent src/app/modules/knowledge`: فارغ.
  - `git diff 4bc6417..HEAD --stat -- deploy .github pyproject.toml`: فارغ.
  - كل سطر محذوف من الاختبارات القائمة (`git diff 4bc6417..HEAD -- tests/`، تسعة أسطر) سطرُ استيراد، أو اتساع توقيع (`retag=file_retag` في `support_files_media.py`)، أو إعادة تنسيق توقيع المساعد `_uploaded` في `test_files_event_mapping.py` لإضافة وسيط الوسوم. ولم يُحذف تأكيد واحد.
  - وجدتُ بالاسم كل اختبار مذكور في الجدول أدناه في ملفه (`grep "def <name>"`).
- **ما لم أشغّله:** `tests/integration`، لأن المكدّس الحيّ مستعمل (`CLAUDE.md`). فأدلة E3 وE4 الحيّة منقولة من صف 7د في `status.md`: شغّلها مهندس البيانات على حاوية `postgres:16` مؤقتة، و«94 passed». ولا تغيير في SQL ولا في الهجرة بعد `0cd5fe6` (`test-plan.md` §6). ووظيفة CI `integration` تعيدها على طلب الدمج.

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| `INC1‑AC‑1` (الوسوم على الملف) | **التسجيل بطلب PUT واحد ومجزأً:** `tests/unit/test_api_files_media_router.py::test_register_with_tags_stores_them_and_every_read_returns_them` و`::test_multipart_register_carries_the_tags_too`. **NFC وقصّ المسافات:** `tests/unit/test_files_domain.py::test_tags_are_nfc_normalised_so_composed_and_decomposed_alef_hamza_match` و`::test_tags_are_stripped`. **رفض الفارغ:** `::test_tags_refuse_an_empty_or_blank_value`. **حدّ الـ128 (128 يُقبل و129 يُرفض):** `::test_tags_accept_the_boundary_length_and_refuse_one_more`. **الفئات السبع (0.3، S‑3):** `::test_tags_refuse_invisible_and_reordering_characters_by_unicode_category`، و`tests/unit/test_files_tags_qa.py::test_every_code_point_is_judged_by_its_unicode_category` (يمرّ على كل نقاط الترميز، وعددها 1,114,112، ولا مخالفة واحدة)، والشيفرة في `value_objects.py:220` و`:239` و`:242`. **البديل المنفرد 422 على المسارات الثلاثة:** `test_api_files_media_router.py::test_retag_with_a_lone_surrogate_is_422_and_never_saves`، و`::test_multipart_register_with_a_lone_surrogate_never_starts_an_upload`، و`::test_single_register_with_a_lone_surrogate_tag_is_422_and_writes_no_row` (`POST /files`، `8a5dfc6`). **المفتاح المجهول:** `test_files_domain.py::test_from_mapping_refuses_an_unknown_key`. **422 قبل أي كتابة أو رفع وقبل فتح رفع مجزأ:** `tests/unit/test_files_use_cases.py::test_a_bad_tag_is_a_422_before_any_limit_is_spent` و`::test_a_bad_tag_opens_no_multipart_upload`. **404 أو 409 قد يسبقان ولا يُنفق شيء:** `test_files_tags_qa.py::test_a_domain_bad_tag_into_a_full_space_spends_nothing` و`::test_a_domain_bad_tag_into_an_unknown_space_spends_nothing`. **الإعادة في `FileOut` و`null` للغائب:** `test_api_files_media_router.py::test_an_untagged_file_reads_all_three_tags_as_null`. **لا يُخزَّن فارغاً:** `tests/integration/test_files_repository_rls.py::test_an_untagged_file_stores_and_reads_back_null_never_empty` و`::test_the_three_tags_round_trip` (E3، 94 passed على قاعدة مؤقتة). **`PUT /files/{id}/tags` يستبدل كاملة:** `test_api_files_media_router.py::test_retag_replaces_all_three_tags_and_answers_the_whole_file`. **الإعادة بالوسوم نفسها لا تكتب ولا تغيّر `updated_at`:** `test_files_domain.py::test_retag_to_the_same_tags_is_a_no_op_that_leaves_updated_at_alone` و`test_api_files_media_router.py::test_re_tagging_with_the_tags_it_has_does_not_write`. **الملف المحذوف 409:** `::test_retag_of_a_deleted_file_is_409_and_of_an_unknown_one_404`. والتغطية كاملة في `test-plan.md` §1 و§6.2 | ✅ مقبول |
| `INC1‑AC‑2` (الاستبدال بالاسم والوسوم) | **الإتمام يحذف الأقدم بالاسم والوسوم نفسها وحده:** `tests/unit/test_file_replacement.py::test_completing_replaces_only_the_namesake_with_the_same_tags`، ومثله على المسار في `test_api_files_media_router.py`. **إعادة الوسم لملف `ready`:** `test_file_replacement.py::test_re_tagging_onto_an_older_files_tags_replaces_it`. **إعادة الوسم بلا تغيير لملف `ready` تكنس:** `::test_a_no_op_re_tag_still_sweeps`، و`test_files_tags_qa.py::test_a_no_op_rename_of_a_ready_file_still_sweeps`. **إعادة التسمية أو الوسم لملف غير `ready` لا تحذف شيئاً (0.3، S‑1):** `test_file_replacement.py::test_a_quarantined_file_renamed_or_re_tagged_destroys_nothing` و`::test_re_tagging_a_file_with_no_proven_bytes_destroys_nothing_until_it_completes`، و`test_files_tags_qa.py::test_a_no_op_rename_or_retag_of_a_file_that_is_not_ready_deletes_nothing` (على `uploaded` و`scanning` و`quarantined`) و`::test_the_routes_sweep_for_a_ready_file_and_not_for_a_quarantined_one`، والشيفرة في `file_replacement.py:227-242`. **الغائب يطابق الغائب وحده:** `test_file_replacement.py::test_an_absent_tag_matches_only_an_absent_one`، و`tests/integration/test_file_namesakes_live.py::test_two_untagged_files_still_replace_each_other` و`::test_one_differing_tag_keeps_both_files`. **نوع الملف لا يدخل:** `test_file_replacement.py::test_the_file_type_plays_no_part_in_the_match`. **`name_key` شرط فهرس:** `test_file_namesakes_live.py::test_the_tag_condition_is_a_filter_and_name_key_stays_an_index_condition` (E4، 94 passed على قاعدة مؤقتة). **اختبارات الاستبدال القائمة لم تتغيّر:** تحقّقتُ من الفرق، فلا سطر محذوف فيها إلا اتساع توقيع | ✅ مقبول |
| `INC1‑AC‑3` (العقود) | **الحدث يحمل `tags` حين يوجد وسم وحده، ويُصادَق بالمخطط:** `tests/unit/test_files_event_mapping.py::test_uploaded_data_carries_the_present_tags_and_validates` (`Draft202012Validator` على `docs/design/events/schemas/files.file.uploaded.v1.json`). **لا مفتاح ولا `null`:** `::test_an_untagged_file_publishes_no_tags_key_at_all`. **`openapi.yaml` يعلن `retagFile` (السطر 425) ويطابق التطبيق في الاتجاهين:** `tests/unit/test_api_conventions.py::test_the_application_publishes_exactly_the_operations_the_contract_declares`، و`test_files_tags_qa.py::test_retag_file_is_declared_by_both_documents_with_the_same_shape` و`::test_one_tag_limit_and_one_key_set_across_domain_openapi_event_and_migration`. **الصلاحية:** الصف `("/api/v1/files/{}/tags", "put"): Permission.FILES_WRITE` في `tests/unit/test_api_rbac.py:139`. **الوثائق الحاكمة:** `git diff 4bc6417..HEAD --stat -- docs/design` يذكر `01` و`02` و`03` و`04` و`05` و`06` و`openapi.yaml` ومخطط الحدث، وقد طابقها المراجع بالشيفرة (`review.md` «مطابقة التصميم») | ✅ مقبول |
| `INC1‑AC‑16`، حصة `PR‑A` (لا تراجع) | **البوابات الست خضراء** على `8a5dfc6` بتشغيلي (المخرجات أعلاه). **اختبارات الرفع والاستبدال والحصة القائمة خضراء، ولم يتغيّر فيها إلا اتساع التواقيع:** `tests/unit/test_space_quota.py` (أُضيف `tags` إلى `_FakeRegistrar.register` واختباران جديدان، ولم يُحذف تأكيد)، و`test_composition_root.py` (استيراد `RetagFile` وتأكيد هوية جديد)، و`support_files_media.py` (`retag=file_retag`). **لا سطر في `src/app/agents/rag_agent/` ولا في `src/app/modules/knowledge/`:** الفرق فارغ | ✅ مقبول |
| `INC1‑AC‑16`، شقّ `deploy/supply-chain/scan.sh` | ليس في هذا الـPR: يتبع `PR‑D` (`INC1‑AC‑15`). ولم يمسّ `PR‑A` `pyproject.toml` ولا `deploy/` | ليس في هذا الـPR |
| `INC1‑AC‑4` .. `INC1‑AC‑15` | ليست في هذا الـPR: النواة، والمشاريع، والتسجيل، والفحص، والمهام، وعزل `ele-parse`، ومشارك الحذف، والمعاينة عبر `electrical`، وربط المنصة، والتوصيف، والمراقبة، وسلسلة التوريد (`PR‑B..D`). وشقّ `files` من `INC1‑AC‑11` (`FindNamesakes.preview` و`live_name_conflicts`) مغطّى تحت `INC1‑AC‑2` في `test-plan.md` (AC‑2.g) | ليس في هذا الـPR |

## المراجعتان

- **`review.md`:** PASS، ثم PASS في إعادة المراجعة 1. أُغلقت الملاحظات 1 إلى 4. وأُغلق التحسينان 6 و7 في `8a5dfc6`: ثلاثة تعليقات وثائقية، واختبار `POST /files` بالبديل المنفرد، وحالة `scanning` في اختبار التسمية. وبقيت الملاحظة 5 (المستودع المزيّف يعيد الكائن نفسه) بلا عمل مطلوب.
- **`security-review.md`:** PASS، ثم PASS في إعادة المراجعة 1. أُغلقت S‑1 وS‑2 (للوسوم) وS‑3 وS‑4. وبقيت S‑8 (منخفضة) وS‑9 (إعلامية)، وصنّفهما المراجع «غير حاجب». وS‑5 وS‑6 وS‑7 إعلامية.
- لا FAIL مفتوح في أيّ منهما، ولا ملاحظة حاجبة، ولا ملاحظة عالية أو حرجة.

## ملاحظات غير حاجبة مقبولة

أقبل كل بند أدناه باسمه. وأيّ منها لا يمسّ معياراً في النطاق، فلا سبب فيها للرفض:

- **S‑8** (منخفضة، `security-review.md`، فجوة تدقيق): عضو X يعيد تسمية رفع غير مكتمل لزميله Y أو يعيد وسمه، فلا يُكتب ولا يُحذف ولا يُسجَّل شيء. ثم يتمّ Y الرفع، فيُحذف الملف الأقدم، ويُنسب الحذف في سطر `replaced` إلى `user_id=Y` و`source=complete`. **سبب القبول:** لا تصعيد صلاحية جديد، لأن X يملك الحذف بالاستبدال أصلاً بـ`INC1‑R‑2`. والسلوك هو ما قرّره المالك في S‑1 («الإتمام يكنس دائماً»). **المتابعة:** قرار للمالك قبل `PR‑C`: (أ) سطر `info` عند إعادة التسمية أو الوسم لملف غير `ready`، أو (ب) قصر ذلك على المسجِّل ما دام الملف غير `ready`، كقاعدة SR‑1.
- **S‑9** (إعلامية): محارف خفية قليلة من خاصية `Default_Ignorable_Code_Point` تقع خارج الفئات السبع، فتُقبل: U+034F، ومحدّدات التنويع U+FE00..FE0F وU+E0100..E01EF، وحشوات الهانغل، وفراغ برايل U+2800. وفئة `Cn` مرتبطة بإصدار Unicode في المفسّر. **سبب القبول:** هذا تطبيق حرفي لقرار المالك S‑3. **المتابعة:** إن أراد المالك سدّها، فقائمة صريحة صغيرة تضاف إلى الفحص، والحلّ الجذري كتالوج `assets` المُدار (م‑2 في المساعد الذكي).
- **O‑4** (سابقة لـ`PR‑A`): بديل منفرد في `name` الملف يمرّ المجال، فيصير 500 على Postgres الحقيقي، ويترك في المسار المجزأ رفعاً يتيماً في MinIO. **سبب القبول:** `INC1‑AC‑1` يخصّ الوسوم. والوسوم أُصلحت، وقرّر المالك في S‑3 أن يبقى `name` كما هو. **المتابعة:** بند منفصل لـ`FileName` (`value_objects.py:55-69`) يرفض ما لا يُرمَّز UTF‑8، مع اختبار على المسارين.
- **ملاحظة صياغة ترتيب 422 في الـLLD:** أُغلقت في 0.3 (§3.3.2 و`INC1‑AC‑1`: «قبل أي كتابة أو رفع… وقد يجيب فحص وجود الفضاء وحصته أولاً بـ404 أو 409»)، وفي وصف `FileRegisterIn` في `openapi.yaml:1196` وفي `03`. لا عمل باقٍ عليها إلا إيداع الـLLD (الشرط أدناه).
- **ملاحظات إعلامية أقبلها كما هي، بلا عمل:**
  - S‑5 / O‑5: لا حدّ للطول على السلك، والحدّ 128 يُفرض في المجال بعد التطبيع والقصّ.
  - S‑6: `downgrade` الهجرة يمحو الوسوم. لا يُطبَّق على بيانات حقيقية.
  - S‑7 / O‑3: بصمة مفتاح التكرار تُحسب على الجسم الخام، فالغائب و`{}` يختلفان، وNFC وNFD يختلفان. النتيجة 409 لا ضرر.
  - O‑6: تسلسلات الإيموجي المركّبة بـZWJ مرفوضة (Cf)، وهذه نتيجة مباشرة لقرار S‑3.
  - O‑7: ملف `quarantined` الأقدم يُكنس حين يصل ملف `ready` بالاسم والوسوم نفسها، لأنه «حيّ أقدم».
  - الملاحظة 5 في `review.md`: المستودع المزيّف يعيد الكائن المخزَّن نفسه.
- **متابعة لـ`PR‑C`:** يتبع `INC1‑AC‑10` قاعدة S‑1 نفسها، أي أن مشارك حذف `electrical` يُستدعى عند إعادة التسمية أو الوسم لملف `ready` وحده (قرار المالك 2026-11-01 في `status.md`).

## شروط قبل الدمج (ليست إعادة عمل)

- **إيداع الملفات غير المودَعة مع هذا الـPR:** `lld-inc-1.md` 0.3 (مرجع معياري `INC1‑AC‑1` و`INC1‑AC‑2` في هذا القبول)، و`status.md`، و`review.md`، و`security-review.md`، وهذا الملف.
- **وظيفة CI `integration` خضراء على طلب الدمج:** هي التشغيل الرسمي لـE3 وE4 (`test_files_repository_rls.py` و`test_file_namesakes_live.py`) في بيئة معزولة.
- **الهجرة `files/0006_file_tags` لم تُطبَّق على المكدّس الحيّ.** تطبيقها قرار بشري لاحق عبر `migrate` (`python -m app.ops.provision`)، والهجرة قبل الشيفرة الجديدة. والتراجع يفقد الوسوم كلها.

## الحكم النهائي

**مقبول.** كل معيار Must في النطاق ✅: `INC1‑AC‑1` و`INC1‑AC‑2` و`INC1‑AC‑3` بصياغة 0.3، وحصة `PR‑A` من `INC1‑AC‑16`. وكل بند له اختبار مسمّى موجود وناجح، والبوابات الست خضراء بتشغيلي على `8a5dfc6`. ومراجعة الشيفرة والمراجعة الأمنية PASS بلا حاجبة مفتوحة. ولا سطر تغيّر في `rag_agent` ولا في `knowledge`. أما `INC1‑AC‑4..15` وشقّ `scan.sh` من `INC1‑AC‑16` فليست في هذا الـPR، وتُقبل في `PR‑B..D`.

</div>
