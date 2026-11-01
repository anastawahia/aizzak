<div dir="rtl">

# خطة الاختبار: وسوم الملفات `PLT‑26` (`PR‑A` من `INC‑1`، ميزة `electrical-drawings`)

- الفرع: `feat/electrical-drawings-inc-1-pr-a` على `master@4bc6417`. الإيداعات المفحوصة: `c12504d` (S1) و`0cd5fe6` (S2) و`ce733d0` (S3) و`2e0b7e8` (S3 docs).
- المرجع الحاكم: `docs/delivery/electrical-drawings/docs/lld-inc-1.md` §3.1 و§3.3.1–§3.3.4 و§4.1 و§5.1–§5.2.
- النطاق: `INC1‑AC‑1` و`INC1‑AC‑2` و`INC1‑AC‑3` وحصة `PR‑A` من `INC1‑AC‑16`. ما سواها «ليس في هذا الـPR» (`PR‑B..D`).
- الاختبارات الجديدة التي كتبتُها: `tests/unit/test_files_tags_qa.py` (37 حالة اختبار بعد التوسيع بالمعاملات في الجولة الأولى، ثم 100 بعد جولة إعادة العمل §6؛ و0 تغيير في كود المنتج).

## 1. مصفوفة التغطية

### INC1‑AC‑1 — الوسوم على الملف

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑1.a وسوم اختيارية ثلاثة بطلب PUT واحد | واجهة | `tests/unit/test_api_files_media_router.py::test_register_with_tags_stores_them_and_every_read_returns_them` | ✅ |
| AC‑1.a ومجزأً (multipart) | واجهة + وحدة | `test_api_files_media_router.py::test_multipart_register_carries_the_tags_too`، `test_files_use_cases.py::test_the_multipart_registrar_and_the_transfer_service_pass_the_tags_down` | ✅ |
| AC‑1.b التطبيع NFC | وحدة | `tests/unit/test_files_domain.py::test_tags_are_nfc_normalised_so_composed_and_decomposed_alef_hamza_match`، `::test_from_mapping_normalises_like_the_constructor`؛ وجديد: `test_files_tags_qa.py::test_the_length_limit_counts_code_points_after_nfc` | ✅ |
| AC‑1.b قص المسافات | وحدة + واجهة | `test_files_domain.py::test_tags_are_stripped`، `test_files_tags_qa.py::test_a_non_breaking_space_is_whitespace_not_a_value`، `test_api_files_media_router.py::test_register_with_tags_stores_them_and_every_read_returns_them` (القيمة `"  sys-1  "`) | ✅ |
| AC‑1.c رفض الفارغ والمسافات وحدها | وحدة + واجهة | `test_files_domain.py::test_tags_refuse_an_empty_or_blank_value`، `::test_from_mapping_refuses_an_empty_value`، `test_api_files_media_router.py::test_a_bad_tag_at_registration_is_a_422_and_spends_nothing` (`""` و`"   "`) | ✅ |
| AC‑1.c رفض الأطول من 128 (128 مقبول، 129 مرفوض) | وحدة + واجهة | `test_files_domain.py::test_tags_accept_the_boundary_length_and_refuse_one_more`، `::test_the_length_limit_counts_the_stripped_value`؛ وجديد: `test_files_tags_qa.py::test_an_astral_character_counts_as_one` | ✅ |
| AC‑1.c رفض محرف التحكّم | وحدة + واجهة | `test_files_domain.py::test_tags_refuse_a_control_character` (`\x00` و`\n` و`\x7f` و`\t` داخل القيمة) | ✅ |
| AC‑1.c رفض المفتاح المجهول (ويشمل حالة الأحرف `System`) | وحدة + واجهة | `test_files_domain.py::test_from_mapping_refuses_an_unknown_key`؛ جديد: `test_files_tags_qa.py::test_the_keys_of_a_mapping_are_case_sensitive`، `::test_a_wire_bad_tag_is_a_422_before_the_quota_is_even_entered` | ✅ |
| AC‑1.d 422 قبل إنفاق أي حدّ (وحدة، داخل `RegisterUpload`) | وحدة | `test_files_use_cases.py::test_a_bad_tag_is_a_422_before_any_limit_is_spent` (الحدّ `files.too_many`، والحجم، والفضاء المجهول) | ✅ |
| AC‑1.d وقبل فتح رفع مجزأ | وحدة + واجهة | `test_files_use_cases.py::test_a_bad_tag_opens_no_multipart_upload`، `test_api_files_media_router.py::test_a_bad_tag_at_registration_is_a_422_and_spends_nothing` (`multipart_storage.uploads == {}`) | ✅ |
| AC‑1.d المسار الكامل عبر `SpaceQuotaService` (فضاء ممتلئ / مجهول) لا يُنفق شيئاً ولا يفتح رفعاً (انظر §3 «النقطة التي رفعها المطوّر») | واجهة | **جديد**: `test_files_tags_qa.py::test_a_domain_bad_tag_into_a_full_space_spends_nothing`، `::test_a_domain_bad_tag_into_an_unknown_space_spends_nothing`، `::test_a_wire_bad_tag_is_a_422_before_the_quota_is_even_entered` | ✅ |
| AC‑1.d الوسم الخاطئ يغلب النوع غير المدعوم والحجم الزائد | واجهة | `test_api_files_media_router.py::test_a_bad_tag_wins_over_an_unsupported_type_and_an_oversize_file` | ✅ |
| AC‑1.e تُحفظ وتُعاد في `FileOut` (الإتمام والقراءة والسرد) | واجهة | `test_api_files_media_router.py::test_register_with_tags_stores_them_and_every_read_returns_them` | ✅ |
| AC‑1.e الغائب `null` في الجواب (المفاتيح الثلاثة حاضرة دائماً) | واجهة | `test_api_files_media_router.py::test_an_untagged_file_reads_all_three_tags_as_null`، `::test_register_accepts_null_tags_and_an_empty_object_as_untagged` | ✅ |
| AC‑1.e ولا يُخزَّن فارغاً (NULL لا `''`) | تكامل `live_db` | `tests/integration/test_files_repository_rls.py::test_an_untagged_file_stores_and_reads_back_null_never_empty`، `::test_the_database_refuses_a_malformed_tag`، `::test_the_database_accepts_the_boundary_tags` | ✅ (الشغّل مهندس البيانات على قاعدة مؤقتة: 94 ناجحاً) |
| AC‑1.e ذهاب وإياب الوسوم الثلاثة في قاعدة حقيقية + عزل المستأجر | تكامل `live_db` | `test_files_repository_rls.py::test_the_three_tags_round_trip`، `::test_a_partial_tag_set_keeps_each_tag_in_its_own_column`، `::test_a_tag_keeps_its_exact_unicode_form`، `::test_save_writes_a_retag`، `::test_save_can_clear_every_tag`، `::test_a_retag_cannot_reach_another_tenants_file`، `::test_another_tenant_cannot_read_a_files_tags` | ✅ |
| AC‑1.e الوسوم في `FileView` (للمستهلكين) | وحدة | `test_files_use_cases.py::test_the_readable_view_carries_the_present_tags_only` | ✅ |
| AC‑1.f `PUT /files/{id}/tags` يستبدل كاملةً (غياب المفتاح و`null` يزيلان الوسم، و`{}` ينزع الكل) | واجهة + وحدة | `test_api_files_media_router.py::test_retag_replaces_all_three_tags_and_answers_the_whole_file`، `::test_retag_with_an_empty_object_untags_the_file`، `test_files_use_cases.py::test_retag_replaces_all_three_tags_and_saves`، `::test_an_empty_mapping_untags_the_file`، `test_files_domain.py::test_retag_replaces_all_three_and_stamps_updated_at`، `::test_retag_to_no_tags_clears_them` | ✅ |
| AC‑1.f الصلاحية `files:write` وبلا توكن 401 | عقد/واجهة | `tests/unit/test_api_rbac.py` (صف `("/api/v1/files/{}/tags","put")`)، `test_api_files_media_router.py::test_retag_requires_a_bearer` | ✅ |
| AC‑1.f الجسم الخاطئ 422 ولا تغيير | واجهة + وحدة | `test_api_files_media_router.py::test_retag_with_a_bad_tag_is_422_and_changes_nothing`، `test_files_use_cases.py::test_retag_with_a_bad_tag_is_a_422_and_writes_nothing` | ✅ |
| AC‑1.f الملف المجهول أو لمستأجر آخر 404 | واجهة + وحدة | `test_api_files_media_router.py::test_retag_of_a_deleted_file_is_409_and_of_an_unknown_one_404`، `test_files_use_cases.py::test_retag_of_an_unknown_or_foreign_file_is_not_found` | ✅ |
| AC‑1.g إعادة الوسوم نفسها لا تكتب ولا تغيّر `updated_at` | وحدة + واجهة | `test_files_domain.py::test_retag_to_the_same_tags_is_a_no_op_that_leaves_updated_at_alone`، `test_files_use_cases.py::test_re_tagging_with_the_tags_it_has_never_writes`، `test_api_files_media_router.py::test_re_tagging_with_the_tags_it_has_does_not_write`؛ جديد: `test_files_tags_qa.py::test_a_retag_that_only_differs_by_nfc_form_or_padding_does_not_write` | ✅ |
| AC‑1.h الملف المحذوف 409 | وحدة + واجهة | `test_files_domain.py::test_retag_refuses_a_deleted_file_and_changes_nothing`، `test_files_use_cases.py::test_retag_of_a_deleted_file_is_a_conflict`، `test_api_files_media_router.py::test_retag_of_a_deleted_file_is_409_and_of_an_unknown_one_404` | ✅ |
| AC‑1 بصمة مفتاح التكرار تشمل الوسوم | واجهة | `test_api_files_media_router.py::test_the_same_idempotency_key_with_other_tags_is_a_conflict`؛ جديد: `test_files_tags_qa.py::test_replaying_a_key_with_the_same_tags_is_the_same_file`، `::test_a_422_for_a_bad_tag_does_not_burn_the_idempotency_key` | ✅ |

### INC1‑AC‑2 — الاستبدال بالاسم والوسوم

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑2.a عند الإتمام يُحذف الأقدم الحيّ بالاسم **والوسوم نفسها** وحده | وحدة + واجهة | `tests/unit/test_file_replacement.py::test_completing_replaces_only_the_namesake_with_the_same_tags`، `test_api_files_media_router.py::test_completing_replaces_only_the_namesake_with_the_same_tags` | ✅ |
| AC‑2.a عند إعادة الوسم (على الأقدم يستبدل، على الأحدث لا شيء، والابتعاد عن التصادم لا شيء) | وحدة + واجهة | `test_file_replacement.py::test_re_tagging_onto_an_older_files_tags_replaces_it`، `::test_re_tagging_onto_a_newer_files_tags_replaces_nothing`، `::test_re_tagging_away_from_a_collision_replaces_nothing`، `test_api_files_media_router.py::test_retag_onto_an_older_files_name_and_tags_replaces_it` | ✅ |
| AC‑2.a عند إعادة التسمية (لا تراجع) | وحدة | اختبارات `rename` القائمة في `test_file_replacement.py` (لم تُعدَّل، انظر AC‑16) | ✅ |
| AC‑2.b الغائب يطابق الغائب وحده، والملفات بلا وسوم تتبع القاعدة كما كانت | وحدة + تكامل | `test_file_replacement.py::test_an_absent_tag_matches_only_an_absent_one`، `test_files_domain.py::test_absent_matches_only_absent`، `tests/integration/test_file_namesakes_live.py::test_two_untagged_files_still_replace_each_other`، `::test_a_tagged_and_an_untagged_file_never_replace_each_other` | ✅ |
| AC‑2.c وسمٌ واحد مختلف يُبقي الاثنين، والمطابقة حرفية بلا طيّ حالة | تكامل + وحدة | `test_file_namesakes_live.py::test_one_differing_tag_keeps_both_files`، `::test_the_same_three_tags_are_the_same_file`، `test_files_domain.py::test_tags_are_compared_literally_with_no_case_fold` | ✅ |
| AC‑2.d نوع الملف لا يدخل | وحدة + تكامل | `test_file_replacement.py::test_the_file_type_plays_no_part_in_the_match`، `test_file_namesakes_live.py::test_the_file_type_does_not_enter_the_match` | ✅ |
| AC‑2.e إعادة الوسم بلا تغيير تكنس أيضاً | وحدة | `test_file_replacement.py::test_a_no_op_re_tag_still_sweeps` | ✅ |
| AC‑2.f فشل الكنس لا يُفشل الطلب، وفشل إعادة الوسم لا يكنس | وحدة | `test_file_replacement.py::test_a_failed_purge_does_not_fail_the_re_tag`، `::test_a_failing_re_tag_sweeps_nothing` | ✅ |
| AC‑2.g المعاينة تقسم السميّين إلى `replaced` و`kept` ولا تكتب | وحدة + تكامل | `tests/unit/test_files_use_cases.py::test_preview_splits_the_namesakes_into_replaced_and_kept`، `::test_preview_writes_nothing`، `test_file_namesakes_live.py::test_a_conflict_is_a_same_name_file_with_different_tags`، `::test_a_conflict_is_found_when_only_one_side_is_null`، `::test_the_two_reads_partition_the_same_name_files` | ✅ |
| AC‑2.h الفضاء/الاسم/الحيوية/الاستقلال عن الحالة (مجال خالٍ من الفضاء، حذف، أحرف كبيرة/صغيرة) | تكامل | `test_file_namesakes_live.py::test_tags_decide_inside_a_spaceless_bucket_too`، `::test_a_conflict_is_scoped_to_name_space_workspace_and_liveness`، `::test_a_conflict_is_found_across_case_and_normalisation`، `::test_a_conflict_ignores_age_unlike_a_namesake` | ✅ |
| AC‑2.i `name_key` يبقى شرط فهرس (`Index Cond`) مع ملفات موسومة | تكامل `live_db` | `test_file_namesakes_live.py::test_the_tag_condition_is_a_filter_and_name_key_stays_an_index_condition` (معلَّم بـ`live_name_conflicts` و`live_namesakes`) | ✅ (من تشغيل مهندس البيانات) |
| AC‑2.j اختبارات الاستبدال القائمة لا تتغيّر وتبقى خضراء | مراجعة الفرق | `git diff 4bc6417..HEAD -- tests/` : الأسطر المحذوفة 14 كلها استيراد أو اتساع توقيع (`namesakes=…` صار `retag=…, namesakes=…`، وسطر `*, file_id…` يُعاد تنسيقه). لا تأكيد حُذف أو خُفّف | ✅ |

### INC1‑AC‑3 — العقود

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑3.a `files.file.uploaded.v1` يحمل `tags` حين يوجد وسم وحده | عقد | `tests/unit/test_files_event_mapping.py::test_uploaded_data_carries_the_present_tags_and_validates`، `::test_an_absent_tag_is_absent_from_the_object_and_never_null`؛ جديد من الطرف إلى الطرف (تسجيل ← إتمام ← صندوق الصادر): `test_files_tags_qa.py::test_the_event_carries_the_present_tags_and_an_untagged_file_has_no_key` | ✅ |
| AC‑3.b المفتاح الغائب يغيب، ولا `null` ولا `{}` | عقد | `test_files_event_mapping.py::test_an_untagged_file_publishes_no_tags_key_at_all`، `::test_the_schema_refuses_a_null_an_empty_object_and_an_empty_value`، `::test_tags_and_space_are_independent_optional_fields` | ✅ |
| AC‑3.c يُصادَق بالمخطط المنشور | عقد | `test_files_event_mapping.py::test_uploaded_data_carries_the_present_tags_and_validates` (`jsonschema.Draft202012Validator` على الملف المنشور) | ✅ |
| AC‑3.d `openapi.yaml` يعلن `retagFile` وحقلي `tags` ويطابق التطبيق في الاتجاهين | عقد | `tests/unit/test_api_conventions.py::test_the_application_publishes_exactly_the_operations_the_contract_declares` (العمليات في الاتجاهين)؛ جديد: `test_files_tags_qa.py::test_openapi_and_the_application_agree_on_the_tag_schemas`، `::test_retag_file_is_declared_by_both_documents_with_the_same_shape` | ✅ |
| AC‑3.d snake_case وصف `files:write` على المسار الجديد | عقد | `test_api_conventions.py::test_every_published_property_name_is_snake_case`، `tests/unit/test_api_rbac.py` | ✅ |
| AC‑3.e حد 128 والمفاتيح الثلاثة واحدة في المجال و`openapi` والمخطط والهجرة | عقد | **جديد**: `test_files_tags_qa.py::test_one_tag_limit_and_one_key_set_across_domain_openapi_event_and_migration` | ✅ |
| AC‑3.f الوثائق الحاكمة `01` §2.6 و`02` و`03` و`04` و`05` §4 و`06` §6 محدّثة | مراجعة يدوية | `git diff 4bc6417..HEAD --stat -- docs/design`: الملفات الستة حاضرة (`01` +10، `02` +28، `03` ±32، `04` ±4، `05` +1، `06` ±11) مع `openapi.yaml` والمخطط. فحصتُ نص `01` و`05` وطابقا الهجرة وصف الصلاحية | ✅ (يدوي) |
| AC‑3.g الهجرة `files/0006_file_tags` (توسيع فقط، قيد `ck_files_tags_shape`) | تكامل + معماري | الحارس القائم `test_zero_downtime_migrations.py` و`test_migration_revision_ids.py` ضمن البوابة 5 (أخضران)؛ و`test_files_repository_rls.py::test_the_database_refuses_a_malformed_tag` | ✅ |

### INC1‑AC‑16 — حصة `PR‑A`

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑16.a البوابات الخمس الأولى خضراء | بوابات | §2 | ✅ |
| AC‑16.b `deploy/prometheus/test-rules.sh` أخضر | بوابة | §2 | ✅ |
| AC‑16.c اختبارات الرفع والاستبدال والحصة القائمة بلا تعديل إلا اتساع التواقيع | مراجعة الفرق | `tests/unit/test_space_quota.py` (توسيع `_FakeRegistrar.register` بوسيط `tags`)، `tests/unit/test_composition_root.py` (استيراد + تأكيد جديد)، `support_files_media.py` (تمرير `retag=`). لا تأكيد قائم حُذف | ✅ |
| AC‑16.d لا سطر يتغيّر في `src/app/agents/rag_agent/` ولا في `knowledge` | فحص | `git diff 4bc6417..HEAD --stat -- src/app/agents/rag_agent src/app/modules/knowledge` ← فارغ (0 أسطر) | ✅ |
| AC‑16.e `deploy/supply-chain/scan.sh` | — | ليس في هذا الـPR: لم يُطلب في مهمتي، ومساره `PR‑D` (`INC1‑AC‑15`) | غير مطلوب هنا |

### ما ليس في هذا الـPR
`INC1‑AC‑4` إلى `INC1‑AC‑15` (النواة، والمشاريع، والتسجيل، والفحص، والمهام، وعزل `ele-parse`، ومشارك الحذف، والمعاينة عبر `electrical`، وربط المنصة، والتوصيف، والمراقبة، وسلسلة التوريد) هي `PR‑B..D`. ويظهر في `PR‑A` من `INC1‑AC‑11` شقّ `files` وحده (`FindNamesakes.preview` و`live_name_conflicts`) وقد غُطّي أعلاه تحت AC‑2.g.

## 2. نتائج بوابات المشروع

تشغيل من `/home/AIZZAK` بـ`.venv`، بعد إضافة ملف الاختبارات الجديد. لم يُشغَّل `tests/integration` على المكدّس الحيّ (قيد `CLAUDE.md`).

```
$ .venv/bin/ruff format --check .
869 files already formatted

$ .venv/bin/ruff check .
All checks passed!

$ .venv/bin/mypy src
Success: no issues found in 490 source files

$ .venv/bin/lint-imports
Agents never import API or Infrastructure KEPT
Infrastructure imported only by Composition Root KEPT
Framework does not import outer layers KEPT
WireTwin sections are independent and shared is the floor KEPT

Contracts: 9 kept, 0 broken.

$ .venv/bin/pytest -rs tests/unit tests/architecture tests/eval
6651 passed, 19 skipped, 7 warnings in 253.03s (0:04:13)

$ deploy/prometheus/test-rules.sh
Checking /etc/prometheus/alerts.yml
  SUCCESS: 29 rules found

test-rules: OK
```

ملاحظات:
- الـ19 المتخطّاة كلها في `tests/unit/test_public_file_links_containers.py` (حزمة حاويات اختيارية: `RUN_EDGE_FILES_TEST=1`). لا علاقة لها بالوسوم، ولم أضف أي مرشّح لإخفاء اختبار.
- اختبارات `E3`/`E4` الحيّة (`tests/integration/test_files_repository_rls.py` و`test_file_namesakes_live.py`) لم أُعد تشغيلها: شغّلها مهندس البيانات على قاعدة مؤقتة (94 ناجحاً). فحصتُ أنها تغطي كل بند في §5.2 (انظر المصفوفة). وأنشأتُ حاوية `postgres:16` مؤقتة (`--rm`، `tmpfs`، `127.0.0.1:25432`) لفحص واحد فقط (سلوك `asyncpg` مع surrogate، انظر B‑1) ثم أُزيلت وتحققتُ بـ`docker ps -a`.

## 3. الاختبار الاستكشافي

الطريقة: `TestClient` على `create_app` بالمكدّس الداخلي `support_files_media` (نفس موصل الإنتاج بلا Postgres/MinIO)، وسكربتات استكشاف مؤقتة خارج المستودع.

| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| غياب `tags` / `null` / `{}` / كل القيم `null` | `POST /files` و`/files/multipart` بكل شكل | 201، ملف بلا وسوم | 201 في الثمانية | ✅ |
| مسافات وحدها / NBSP وحده | `{"system":"   "}`، `" "` | 422 | 422 | ✅ |
| `U+200B` (مسافة صفرية العرض) وحده | `{"system":"​"}` | غير محدد في LLD (ليست محرف تحكّم ولا `str.strip()` تقصّها) | 201 ويُخزَّن وسماً غير مرئي | ملاحظة O‑1 (ليست خللاً حسب التصميم) |
| 128 / 129 محرفاً، و128 محرفاً مع مسافات حول | `"x"*128`، `"x"*129`، `" "+"x"*128+" "` | 201 / 422 / 201 (القياس بعد القص) | مطابق | ✅ |
| إيموجي 128 / 129 (نقطة ترميز واحدة بـ4 بايت) | `"😀"*128/129` | 201 / 422 | مطابق، و`char_length` في Postgres يعطي 128 (تحقق على حاوية مؤقتة) | ✅ |
| عربية NFD (`ا`+همزة) | وسم مفكّك | يُخزَّن NFC بطول 2 | مطابق | ✅ |
| محرف تحكّم `\x00` داخل القيمة | `"a\x00b"` | 422 | 422 | ✅ |
| تحكّم C1 `\x85` | `"a\x85b"` | غير محدد (النمط `[\x00-\x1f\x7f]` كما في `name`) | 201 | ملاحظة O‑2 (توافق مع `name`) |
| مفتاح مجهول `section`، وحالة أحرف `System`، قيمة غير نصية `1`، قائمة، نص | الجسم الخاطئ | 422 `common.validation_error` | 422 في الستة | ✅ |
| وسم بحجم 1 ميغابايت | `"x"*1_000_000` | 422 | 422 | ✅ |
| مفتاح تكرار: غياب ثم `null` ثم `{}` ثم كلها `null` | رفعان بالمفتاح نفسه بشكلين | الغياب = `null` (201,201)؛ الغياب ≠ `{}` | غياب→`null` 201,201؛ غياب→`{}` 201,409؛ `{}`→`{"system":null}` 201,201 | ✅ (O‑3: البصمة على الجسم الخام لا على المعنى) |
| مفتاح تكرار: NFC مقابل NFD، وقيمة بمسافات | الجسمان يعنيان الوسم نفسه | غير محدد | 409 | O‑3 |
| 422 لا يحرق المفتاح | وسم خاطئ ثم صحيح بالمفتاح نفسه | 422 ثم 201 | 422 ثم 201 | ✅ (اختبار جديد) |
| فضاء ممتلئ + وسم خاطئ (مجال) | حصة 100 بايت، 90 مستخدمة، رفع 50 | لا إنفاق | 409 `spaces.quota_exceeded`، لا صف ولا رفع ولا presigned | ✅ للإنفاق (انظر النقطة أدناه) |
| فضاء مجهول + وسم خاطئ (مجال) | `space_id` غير موجود | لا إنفاق | 404، لا صف ولا رفع | ✅ للإنفاق |
| فضاء ممتلئ/مجهول + وسم خاطئ (pydantic) | مفتاح مجهول أو قيمة غير نصية | 422 | 422 وقفل الحصة لم يؤخذ | ✅ |
| `PUT /files/{id}/tags` لملف محذوف | حذف ثم وسم | 409 `common.conflict` | 409 | ✅ |
| ملف محذوف + وسم خاطئ | — | 422 أو 409 | 422 (التحقق قبل الحالة) | ✅ مقبول |
| معرّف غير صالح / جسم فارغ / جسم `null` / بلا توكن | `PUT` | 404 / 422 / 422 / 401 | مطابق | ✅ |
| إعادة وسم بقيمة تختلف شكلاً فقط (NFD، مسافات، NBSP) | `PUT` | 200 بلا كتابة | 200، بلا `save`، `updated_at` ثابت | ✅ (اختبار جديد) |
| Surrogate مفرد في الوسم | JSON خام `"\ud800"` | 422 | **201 في الذاكرة**؛ وعلى قاعدة حقيقية `asyncpg.DataError: surrogates not allowed` ← `common.internal` 500 | الخلل B‑1 |
| Surrogate مفرد في `name` (مقارنة) | الشيء نفسه | — | 201 في الذاكرة؛ السلوك سابق للـPR | O‑4 (سابق لـPR‑A) |
| عرض 375px / RTL | — | — | لا واجهة في هذا المستودع (`CLAUDE.md`: backend only) | غير قابل للتطبيق |
| `openapi.json` الحيّ مقابل `openapi.yaml` | قارنتُ المخططات | نفس الخصائص | الخصائص والمطلوب متطابقة. الفرق: الحيّ بلا `maxLength: 128` على `FileTagsIn`، و`FileTagsOut` في `openapi.yaml` بلا `required` | ملاحظة O‑5 (تجميلي) |
| العزل بين المستأجرين | وسم/قراءة بمستأجر آخر | 404 / لا يُرى | 404 في الوحدة، وفي التكامل الحي `test_a_retag_cannot_reach_another_tenants_file` و`test_another_tenant_cannot_read_a_files_tags` | ✅ |

### الحكم على النقطة التي رفعها المطوّر (ترتيب `SpaceQuotaService` قبل تحليل الوسوم)

**الحكم: ليست خللاً مقابل `INC1‑AC‑1`. الترتيب سابق ومطابق لـ`name`، والمعيار يُفهم «إنفاق حدّ» لا «أسبقية رمز الخطأ». أوصي بتصحيح صياغة LLD فقط.**

الأدلة:
1. **ماذا يفعل المغلِّف قبل `RegisterUpload`** (`src/app/framework/di/space_quota.py:154-193`): داخل وحدة عمل واحدة يأخذ قفلاً استشارياً على سقف الملفات (`self._lock.hold`)، ثم `SELECT … FOR UPDATE` على صف الفضاء (`self._spaces.lock`)، ثم `SUM` على بايتات الفضاء. هذه كلها قراءة وقفل لا يكتب شيئاً. فيُلقي `NotFoundError` أو `ConflictError(spaces.quota_exceeded)` فتتراجع المعاملة وتُرفع الأقفال. **الإنفاق الفعلي** (صف الملف ← يرفع `bytes_in_space` و`count`) يقع في `RegisterUpload.execute` سطر `await self._files.add(...)` (`use_cases.py:230`) بعد تحليل الوسم.
2. **لا رفع مجزأ يُفتح**: `start_multipart` في `use_cases.py` يقع بعد كتلة التحقق الأولى التي فيها `FileTags.from_mapping`، وبعد فحص الحصة. اختباراتي الجديدة تثبت من الطرف إلى الطرف (فضاء ممتلئ، وفضاء مجهول، على المسارين `POST /files` و`POST /files/multipart`): `file_repository.rows` لم يتغيّر، `storage.presigned_puts` لم يتغيّر، `multipart_storage.uploads == {}`.
3. **pydantic يرد معظم الأشكال قبل المغلِّف**: `FileTagsIn` بـ`extra="forbid"` و`str | None` يرد المفتاح المجهول (بما فيه `System`) والقيمة غير النصية بـ422 قبل تشغيل المعالج، فلا يؤخذ قفل الحصة أصلاً (الاختبار `test_a_wire_bad_tag_is_a_422_before_the_quota_is_even_entered` يؤكد أن `RecordingQuotaLock.held` لم يتغيّر، وفضاء ممتلئ ومجهول لا يقنّعان الـ422). المتبقي لأمر المجال: الفارغ/المسافات والأطول من 128 ومحرف التحكّم.
4. **الأسبقية الفعلية المرصودة** للوسم الذي يرفضه المجال: فضاء مجهول ← 404 `common.not_found`، فضاء ممتلئ ← 409 `spaces.quota_exceeded`، وإلا 422. هذا هو الترتيب نفسه لـ`name` و`content_type` الخاطئين اليوم (الاثنان يُحلَّلان داخل `RegisterUpload` أيضاً)، فلا تراجع ولا سلوك جديد.
5. **لا حدّ يستهلكه الطلب**: لا محدِّد معدّل على `POST /files` (حارس الصلاحية وحده، `routers/files.py:117`)، وتأكّدتُ أن مفتاح التكرار لا يُحرق بالـ422 (اختبار جديد).

التوصية (غير ملزمة، بلا إعادة عمل): تعديل تعليق LLD §3.3.2 «a bad tag is a 422 before any limit is spent» ليقول «قبل أي كتابة أو رفع» بدل الإيحاء بأسبقية الرمز على الـ404/409. ولو أراد المالك أسبقية الـ422 فعلاً فالحلّ تحليل الوسوم في الموجّه أو في المغلِّف قبل فتح وحدة العمل، وهذا قرار تصميم لا خلل.

### ملاحظات غير ملزمة (ليست أخطاء)

- O‑1: `U+200B` وحروف التنسيق (`Cf`) غير مرفوضة. التصميم يرفض أحرف التحكّم فقط، فوسم «غير مرئي» ممكن ويطابق نفسه حرفياً. يُترك لقرار المالك إن أرادها مرفوضة.
- O‑2: تحكّم C1 (`U+0080..U+009F`) غير مرفوض، توافقاً مع `FileName`.
- O‑3: بصمة مفتاح التكرار على الجسم لا على معناه: الغياب ≠ `{}`، وNFC ≠ NFD. لا ضرر (409 يُنبّه العميل).
- O‑4: Surrogate المفرد في `name` يُقبل في المجال أيضاً (سابق للـPR، لكنه يجعل B‑1 نمطاً أعم يستحق إصلاحاً واحداً للاثنين).
- O‑5: `openapi.json` المولَّد من التطبيق يخلو من `maxLength: 128` الموجود في `openapi.yaml` (التحقق في المجال، فالسلوك مطابق). `FileTagsOut` في `openapi.yaml` بلا `required` والحيّ به. تجميلي، ولا يكسر `test_api_conventions`.

## 4. الأخطاء المكتشفة

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| B‑1 | **surrogate مفرد في الوسم يمرّ المجال ثم ينهار الحفظ بـ500.** `_clean_tag` لا يتحقق من قابلية الترميز UTF‑8. الجسم JSON صالح (`"\ud800"`) فيقبله pydantic، ثم يرفضه `asyncpg` عند الكتابة (`DataError: surrogates not allowed`) ويُترجَم إلى `common.internal` 500 بدل 422. المتوقع حسب `INC1‑AC‑1` بند «وسم غير صالح ← 422». الفعلي: 500 على `POST /files` و`/files/multipart` و`PUT /files/{id}/tags` (متوقَّع من المسار، لا شيء يُحفظ؛ انظر الملاحظة أدناه عن الرفع اليتيم) | minor | (1) `POST /api/v1/files` بجسم خام `{"space_id":"<sp>","name":"s.pdf","content_type":"application/pdf","size_bytes":10,"tags":{"system":"\ud800"}}` ← على المكدّس الداخلي 201 (المجال قبله)؛ (2) في قاعدة حقيقية: `await conn.fetchval("select $1::text", "\ud800")` ← `asyncpg.exceptions.DataError: invalid input for query argument $1 … surrogates not allowed` (تحققتُ على `postgres:16` مؤقت)؛ والمحوّل `sql_repository._translate` (`sql_repository.py:573-593`) يحوّل أي `DBAPIError` غير معروف إلى `common.internal`. المالك: **backend-developer**، الموضع المشتبه: `src/app/modules/files/domain/value_objects.py:213-226` (`_clean_tag`: أضف رفض ما لا يُرمَّز UTF‑8 كأن تجرّب `cleaned.encode("utf-8")` وتُلقي `InvalidFileInput`). نمط أعمّ يمسّ `FileName` (`value_objects.py:55-69`) أيضاً (سابق للـPR). لا يحجب الدمج. | ~~مفتوح~~ **مُغلق** في `f5c48e6` (راجع «الجولة الأولى من إعادة العمل» أدناه) |

لا أخطاء blocking أو major. ملاحظة لـB‑1: في المسار المجزأ يُفتح الرفع في التخزين (`start_multipart`) قبل الإدراج الفاشل، فيبقى رفع يتيم يكنسه التخزين بعد انتهاء المدة (التوثيق في `use_cases.py` سطر «A failed insert afterwards leaves an orphan upload»)؛ وهذا سلوك موثّق مسبقاً لأي فشل إدراج.

## 5. الحكم

**PASS** (حكم `ae13a38`؛ الحكم المحدَّث بعد إعادة العمل في القسم التالي) — البوابات الست خضراء، وكل بند من `INC1‑AC‑1/2/3` وحصة `PR‑A` من `INC1‑AC‑16` له اختبار ناجح أو فحص يدوي معلَّل، ولا أخطاء blocking أو major (خلل minor واحد B‑1 غير حاجب)، وترتيب الحصة قبل تحليل الوسم ليس خللاً مقابل `INC1‑AC‑1` لأن لا شيء يُنفق ولا رفع يُفتح.

---

## 6. الجولة الأولى من إعادة العمل (2026‑11‑01، `HEAD` = `080199a`)

- المفحوص: `f5c48e6` (الوسوم ترفض ما لا يُرمَّز UTF‑8 والفئات Cc وCf وCs وCo وCn وZl وZp؛ قرار المالك `S‑3`، الوسوم فقط و`name` كما هو) و`080199a` (قرار المالك `S‑1`: الكنس عند إعادة التسمية وإعادة الوسم لملف `ready` وحده، والإتمام يكنس دائماً؛ حقلا التدقيق `user_id` و`source`؛ تعديلات `openapi.yaml`). وثيقة `LLD` بإصدارها 0.3 (تعديلات غير مودَعة بعد) هي المرجع.
- `git diff ae13a38..HEAD --stat`: `src/` ملفات ثلاثة فقط (`routers/files.py` تعليقات، `file_replacement.py`، `value_objects.py`) و`docs/design/{03,06,openapi}`. **لا SQL ولا هجرة ولا مستودع** (`sql_repository.py` و`migrations/` خارج الفرق)، لذا اختبارات `E3`/`E4` الحيّة (`test_files_repository_rls.py`، `test_file_namesakes_live.py`) غير متأثرة ولم تُعد تشغيلها. لم يُشغَّل `tests/integration` على المكدّس الحيّ.
- كود المنتج: لم ألمس شيئاً. أضفتُ اختبارات في `tests/unit/test_files_tags_qa.py` فقط (+374 سطراً، وحُذف منه 4 أسطر هي نفسي: تعليق، وتأكيد `maxLength` على `FileTagsIn` الذي ألغاه قرار `S‑1`، فحلّ محلّه تأكيد غياب `maxLength` وحضور وصف الحدّ و`required` في `FileTagsOut`، وسطر استيراد). لم يُحذف أو يُخفَّف أي تأكيد للمطوّرين.

### 6.1 حالة `B‑1`

**مُغلق.** `_clean_tag` يجرّب `cleaned.encode("utf-8")` ويُلقي `InvalidFileInput` (`value_objects.py:236-242`) فيصير الـsurrogate المفرد 422 `common.validation_error` ولا يصل إلى `asyncpg`. الدليل (اختبارات تمرّ كلها):

| المسار | الاختبار |
|---|---|
| المجال | `test_files_domain.py::test_a_tag_that_cannot_be_encoded_as_utf8_is_refused_explicitly`، والمعامَلان `\ud800` و`\udfff` في `::test_tags_refuse_invisible_and_reordering_characters_by_unicode_category` |
| `PUT /files/{id}/tags` | `test_api_files_media_router.py::test_retag_with_a_lone_surrogate_is_422_and_never_saves` (422، لا `save`، الوسوم القديمة سليمة، القيمة لا تُرجَع في الجواب) |
| `POST /files/multipart` (لا رفع يُفتح) | `test_api_files_media_router.py::test_multipart_register_with_a_lone_surrogate_never_starts_an_upload` (`multipart_storage.uploads == {}` ولا صف) |
| `POST /files` و`/files/multipart` و`PUT …/tags` | **جديد** `test_files_tags_qa.py::test_a_forbidden_character_at_registration_is_a_422_that_never_echoes_it` و`::test_a_forbidden_character_in_a_retag_is_a_422_and_changes_nothing` (عشرة أشكال خطرة ومنها surrogate، بجسم JSON خام) |

الرفع اليتيم الذي ذكرتُه في الجولة السابقة لم يعد ممكناً لهذا السبب: الرفض في المجال قبل `start_multipart`.

### 6.2 إعادة تخطيط `INC1‑AC‑1` على صياغة 0.3

| البند (0.3) | الاختبار | الحالة |
|---|---|---|
| الرفض للفئات Cc وCf وCs وCo وCn وZl وZp | `test_files_domain.py::test_tags_refuse_invisible_and_reordering_characters_by_unicode_category` (17 محرفاً نموذجياً على الأنواع الثلاثة، والرسالة تسمّي النوع ولا تعيد القيمة)؛ **جديد** `test_files_tags_qa.py::test_every_code_point_is_judged_by_its_unicode_category` (كل نقاط الترميز الـ1,114,112 في وسط قيمة: مرفوضة إن وفقط إن كانت فئتها من السبع، وكل فئة ممنوعة لها أعضاء فعلاً) | ✅ |
| المسموح: Zs وحروف كل الكتابات والعلامات المركِّبة والإيموجي | `test_files_domain.py::test_tags_accept_ordinary_text_of_any_script`؛ **جديد** `test_combining_marks_pass_and_a_zwj_sequence_does_not`، `test_allowed_text_of_other_scripts_registers_and_reads_back_unchanged` (عربي وCJK وإيموجي وعلامة مركِّبة عبر `POST` ثم `GET`) | ✅ |
| قصّ المسافات والتحكّم الحافّي قبل الفحص | `test_files_domain.py::test_control_characters_at_the_edges_are_stripped_not_refused`؛ **جديد** `test_edge_whitespace_is_stripped_before_the_category_check` (`\t` و`\x85` و` ` و` ` و`\x1c`/`\x1f` وNBSP وIDEOGRAPHIC SPACE)، و`test_edge_characters_that_are_not_whitespace_are_refused_not_stripped` (NUL وZWSP وBOM وبديل الاتجاه على الحافة: يُرفض ولا يُقصّ)، و`test_a_value_that_is_only_invisible_is_refused` | ✅ |
| 422 قبل أي كتابة أو رفع وقبل رفع مجزأ، وقد يجيب الفضاء/الحصة أولاً بـ404/409 ولا يُنفق شيئاً | `test_files_tags_qa.py::test_a_domain_bad_tag_into_a_full_space_spends_nothing`، `::test_a_domain_bad_tag_into_an_unknown_space_spends_nothing` (أُضيف المحرف `U+200B` إلى مجموعة الأجسام)، `::test_a_wire_bad_tag_is_a_422_before_the_quota_is_even_entered`، والجديد أعلاه | ✅ |
| `error` لا يردّد القيمة | الوحدة (`char not in str(err.value)`) والواجهة (الجديد: لا المحرف ولا صيغة `\uXXXX` في الجواب) | ✅ |
| بقية البنود (التطبيع، الحدّ 128، التخزين `NULL`، الإرجاع في `FileOut`، `PUT` يستبدل كاملةً، لا كتابة عند التطابق، المحذوف 409) | لم تتغيّر، انظر §1 (كلها خضراء في التشغيل أدناه) | ✅ |

### 6.3 إعادة تخطيط `INC1‑AC‑2` على صياغة 0.3

| البند (0.3) | الاختبار | الحالة |
|---|---|---|
| الإتمام يكنس دائماً (بما فيه ملف سجّلته إعادة تسمية/وسم وهو وعدٌ فقط) | `test_file_replacement.py::test_re_tagging_a_file_with_no_proven_bytes_destroys_nothing_until_it_completes[uploaded/scanning]` و`::test_renaming_a_file_with_no_proven_bytes_destroys_nothing_until_it_completes` (ثم `complete` يحذف الأقدم)، `test_api_files_media_router.py::test_retag_and_rename_of_a_file_that_is_not_ready_destroy_nothing_until_it_completes` (على المسار) | ✅ |
| إعادة التسمية وإعادة الوسم لملف `ready` يكنسان | `test_file_replacement.py::test_re_tagging_onto_an_older_files_tags_replaces_it` وإخوته، `test_api…::test_retag_onto_an_older_files_name_and_tags_replaces_it`؛ **جديد** `test_a_no_op_rename_of_a_ready_file_still_sweeps` | ✅ |
| إعادة الوسم بلا تغيير لملف `ready` تكنس | `test_file_replacement.py::test_a_no_op_re_tag_still_sweeps` | ✅ |
| ملف غير `ready` (`uploaded`/`scanning`/`quarantined`) لا يكنس شيئاً عند إعادة التسمية أو الوسم | `test_file_replacement.py::test_a_quarantined_file_renamed_or_re_tagged_destroys_nothing`؛ **جديد** `test_files_tags_qa.py::test_a_no_op_rename_or_retag_of_a_file_that_is_not_ready_deletes_nothing[uploaded/scanning/quarantined]` (الحالة الأصعب: الملف يصادم أقدم فعلاً وكتابته no‑op، ومع ذلك لا حذف)، `::test_the_not_ready_statuses_are_exactly_the_three_the_contract_names` (يفشل إن أُضيفت حالة جديدة فتُعاد قراءة القاعدة)، `::test_a_retag_that_makes_a_file_not_ready_never_happens_and_status_is_untouched`، `::test_the_routes_sweep_for_a_ready_file_and_not_for_a_quarantined_one` (على المسار: 200 و`knowledge_purge.calls == []` والحالة تبقى `quarantined`، ثم ملف `ready` يكنس) | ✅ |
| فشل الكنس لا يُفشل الطلب | `test_a_failed_purge_does_not_fail_the_re_tag`؛ **جديد** `test_a_failed_lookup_is_logged_with_the_audit_fields_and_does_not_fail_the_call[complete/rename/retag]` | ✅ |
| التدقيق: `user_id` و`source` في سجلات الكنس، بلا اسم ولا وسم | `test_file_replacement.py::test_the_sweep_logs_who_acted_and_through_which_entry_point`، `::test_the_failed_sweep_logs_carry_the_audit_fields_and_no_name_or_tag`؛ **جديد** سجل `lookup_failed` (الاختبار السابق)، و`test_no_sweep_log_field_ever_carries_a_name_or_a_tag_value` (كل سجل يصدره الخدمة: الحقول الإضافية ⊆ `{file_id, workspace_id, user_id, source, replaced_id}`، وأي نص اسم أو وسم غير موجود في الرسالة ولا الحقول) | ✅ |
| `name_key` شرط فهرس؛ الغائب يطابق الغائب؛ نوع الملف لا يدخل؛ الاختبارات القائمة لم تتغيّر | غير متأثر (لا SQL في الدلتا)؛ §1؛ `git diff ae13a38..HEAD -- tests/` لا يحذف سطر تأكيد واحداً من اختبارات المطوّرين | ✅ |

### 6.4 العقود (`INC1‑AC‑3.d/e`) بعد الدلتا

`openapi.yaml`: `FileTagsIn` بلا `maxLength` ووصفه يذكر 128 «بعد التطبيع والقصّ» والفئات الممنوعة؛ و`FileTagsOut.required` للمفاتيح الثلاثة. قارنتُ `app.openapi()` الحيّ بالملف: الخصائص و`required` متطابقة الآن، فـ**الملاحظة O‑5 زالت**. `test_one_tag_limit_and_one_key_set_across_domain_openapi_event_and_migration` عُدّل ليؤكد ذلك (لا ضعف: يتحقق من غياب `maxLength` وحضور 128 في الوصف و`required`).

### 6.5 الاستكشافي (جديد)

| السيناريو | الفعلي | الحكم |
|---|---|---|
| كل نقطة ترميز (1,114,112) في وسط قيمة | مرفوضة: Cc 65، Cf 170، Cn 825,345، Zl 1، Zp 1، Cs 2,048، Co 137,468. مقبولة: Zs 17 وLo 131,612 وLl وLu وMn 1,985 وMc وNd وSo 6,634 … صفر مخالفة للقاعدة. المسح كله 3.7 ثانية | ✅ (صار اختباراً دائماً) |
| تحكّم/فاصل حافّي `\t \x85  ` + مسافة | يُقصّ إلى `x` | ✅ |
| `\x00x` و`x\x00` و`​x` و`x﻿` | مرفوض (ليست مسافات فلا تُقصّ) | ✅ |
| `U+200B` وحده / `" ​ "` / `​‍` | مرفوض (O‑1 السابقة صارت مرفوضة بقرار المالك) | ✅ |
| NBSP داخل القيمة وIDEOGRAPHIC SPACE حولها | `pump 7` يمرّ؛ حول القيمة يُقصّ؛ NBSP وحده يُرفض كفارغ | ✅ |
| تسلسل إيموجي بـZWJ (👨‍👩) | مرفوض (Cf داخلي). إيموجي مفرد وعلامات مركِّبة وعربي بحركات وCJK: مقبولة | ✅ ملاحظة O‑6 أدناه |
| surrogate مفرد في JSON خام على الطرق الثلاث | 422 في كلها، لا صف ولا رفع ولا `save`، والقيمة لا تُرجَع | ✅ (B‑1) |
| ملف `quarantined` عبر الواجهة: `PUT …/tags` ثم `PATCH` الاسم على اسم/وسوم ملف أقدم | 200 و200، `knowledge_purge.calls == []`، الحالة تبقى `quarantined`، الأقدم لم يُحذف | ✅ |
| `uploaded` و`scanning` بالطريقة نفسها (وكتابة no‑op تصادم أقدم) | لا حذف | ✅ |
| `ready` بإعادة وسم بلا تغيير مع أقدم مصادم | يكنس | ✅ |
| ملف `ready` وصل لاحقاً بالاسم والوسوم نفسها لملف `quarantined` أقدم | الـ`quarantined` الأقدم يُحذف أيضاً (هو «حيّ أقدم» بالتعريف) | ✅ يطابق LLD؛ O‑7 |
| فشل البحث عن السميّين (`live_namesakes` يرمي) | الطلب ينجح، السجل `file.replacement.lookup_failed` فيه `user_id` و`source` و`workspace_id` و`file_id` | ✅ |
| حقول سجلات الكنس | معرّفات فقط؛ لا اسم ولا قيمة وسم، حتى حين يحتوي نص الاستثناء الاسم (يذهب إلى `exc_info` لا إلى الحقول) | ✅ |
| `FileStatus` الحالية | أربع قيم فقط (لا `failed`)؛ غير الجاهزة ثلاث، وهي ما يذكره `openapi` | ✅ |
| 375px / RTL | لا واجهة في هذا المستودع | غير قابل للتطبيق |

ملاحظات غير ملزمة:
- O‑6: تسلسلات الإيموجي المركّبة بـZWJ (عائلة، أشخاص بمهنة، أعلام بعلامات `U+E00xx`) ترفضها القاعدة لأنها تحتوي Cf. هذا نتيجة مباشرة لقرار `S‑3` (يعدّه المالك صحيحاً لأن الوسم مرجع تقني) لكن يستحق جملة في وثيقة الواجهة لو ظهر عميل يرسل إيموجي مركّباً. إيموجي المفرد مقبول.
- O‑7: ملف `quarantined` الأقدم يُكنس حين يصل ملف `ready` بالاسم والوسوم نفسها. سلوك قائم قبل هذه الدلتا ومتّسق مع «الأقدم الحيّ»؛ أذكره فقط لأن المنع من الكنس مقصور على الملف الجديد لا على القديم.
- `C0` الفاصلة `\x1c..\x1f` و`\x85` تُقصّ على الحواف (لأن `str.strip` يعدّها مسافات) وتُرفض داخل القيمة؛ سلوك مقصود وفق الوثيقة («قصّ ثم فحص»).

### 6.6 بوابات الجولة (من `/home/AIZZAK` بـ`.venv`)

```
$ .venv/bin/ruff format --check .
870 files already formatted

$ .venv/bin/ruff check .
All checks passed!

$ .venv/bin/mypy src
Success: no issues found in 490 source files

$ .venv/bin/lint-imports
Layered architecture (inward only) KEPT
Domain is pure (no technical imports) KEPT
Application depends on domain + framework only KEPT
Business modules do not import each other KEPT
Agents do not import each other KEPT
Agents never import API or Infrastructure KEPT
Infrastructure imported only by Composition Root KEPT
Framework does not import outer layers KEPT
WireTwin sections are independent and shared is the floor KEPT

Contracts: 9 kept, 0 broken.

$ .venv/bin/pytest -rs tests/unit tests/architecture tests/eval
6753 passed, 19 skipped, 7 warnings in 362.90s (0:06:02)

$ deploy/prometheus/test-rules.sh
Checking /etc/prometheus/alerts.yml
  SUCCESS: 29 rules found

test-rules: OK
```

الـ19 المتخطّاة هي نفسها (`test_public_file_links_containers.py`، حزمة حاويات اختيارية `RUN_EDGE_FILES_TEST=1`) ولا علاقة لها بالوسوم. لم أضف أي مرشّح. (6651 ← 6753: +102 حالة اختبار، منها 63 لي في هذه الجولة و39 للمطوّرين.)

### 6.7 الأخطاء

لا أخطاء جديدة. `B‑1` مُغلق (§6.1). لا blocking ولا major ولا minor مفتوحة.

### 6.8 الحكم المحدَّث

**PASS** — البوابات الست خضراء، `INC1‑AC‑1` و`INC1‑AC‑2` (صياغة 0.3) و`INC1‑AC‑3` وحصة `PR‑A` من `INC1‑AC‑16` كلها ✅ بلا بند بلا تغطية، `B‑1` مُغلق ولا أخطاء مفتوحة، وكود المنتج لم يُلمس. قيد وحيد معلن: اختبارات `E3`/`E4` الحيّة لم تُعد (لا SQL في الدلتا، ولا تشغيل على المكدّس الحيّ).

</div>
