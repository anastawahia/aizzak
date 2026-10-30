<div dir="rtl">

# خطة الاختبار: electrical‑drawings، الزيادة INC‑0 (تهيئة المنصة لـWireTwin)

الفرع `feat/electrical-drawings-inc-0` فوق `master` (`8236e7b`)، تسع إيداعات. المرجع الملزم: `lld-inc-0.md` v0.2 (§5.1 المعايير، §5.2 الاختبارات المخططة). المراجِع: مهندس الجودة، 2026‑10‑30.

## 1. مصفوفة التغطية

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| INC0‑AC‑1 | وحدة | `tests/unit/test_foundation.py::test_limits_defaults_for_the_multipart_upload`، `::test_limits_reject_a_part_size_that_cannot_be_signed`، `::test_limits_accept_the_part_size_bounds` | ✅ |
| INC0‑AC‑1 | وحدة | `tests/unit/test_files_use_cases.py::test_a_single_put_above_50_mib_needs_multipart`، `::test_above_one_gib_is_too_large_in_both_modes`، `::test_one_gib_exactly_registers_as_a_64_part_multipart_upload`، `::test_a_multipart_registration_of_zero_bytes_is_a_validation_error` | ✅ |
| INC0‑AC‑1 | وحدة (موجّه) | `tests/unit/test_api_files_media_router.py` (ملاحظة 219..261 للرمزين `files.too_large` و`files.multipart_required` على السلك)، `tests/unit/test_api_spaces_router.py` (سقف الفضاء عبر `POST /files/multipart`) | ✅ |
| INC0‑AC‑1 | وحدة (حافة) | `tests/unit/test_public_file_links_edge.py::test_the_files_route_has_the_decided_shape`، `::test_the_files_route_guard_fails_on_each_regression` | ✅ |
| INC0‑AC‑2 | وحدة | `tests/unit/test_files_domain.py` (`MultipartUpload`، `part_count`، `expected_size`، `check_parts`) | ✅ |
| INC0‑AC‑2 | وحدة | `tests/unit/test_files_use_cases.py::test_sign_parts_signs_exactly_the_requested_numbers`، `::test_sign_parts_rejects_a_number_out_of_range_or_repeated`، `::test_progress_lists_the_held_parts_and_reports_expiry`، `::test_assemble_with_a_missing_or_wrong_sized_part_never_completes`، `::test_assemble_joins_the_parts_in_order_and_ignores_strays`، `::test_assemble_again_after_a_join_is_safe_and_a_sweep_is_expired`، `::test_complete_assembles_before_the_unit_of_work_and_a_single_file_skips_it` | ✅ |
| INC0‑AC‑2 | وحدة (موجّه) | `tests/unit/test_api_files_media_router.py::test_multipart_register_replays_on_the_same_idempotency_key`، `::test_multipart_register_refuses_oversize_zero_bytes_and_an_unknown_space` (سُمّي بـ`b47979c`؛ وسقف الملفات في `test_multipart_register_at_the_workspace_file_cap_opens_no_upload`، `::test_complete_with_a_missing_part_is_409_and_changes_nothing_then_succeeds`، `::test_complete_of_a_swept_upload_is_409_expired`، `::test_complete_after_an_assembly_whose_commit_was_lost_still_succeeds`، `::test_complete_of_a_multipart_file_replaces_the_older_namesake` | ✅ |
| INC0‑AC‑2 | وحدة | `tests/unit/test_storage_handle.py` (الطرق الخمس)، `tests/unit/test_minio_multipart_api.py` (توقيعات minio‑py) | ✅ |
| INC0‑AC‑2 | وحدة (جديد من الجودة) | `tests/unit/test_minio_multipart_adapter.py` (ترقيم صفحات `list_parts`، وترجمة الأخطاء، وترتيب `complete_multipart`، و`object_size`) | ✅ |
| INC0‑AC‑2 | تكامل (`live_minio`، `live_db`) | `tests/integration/test_minio_storage.py` (BT14)، `tests/integration/test_files_multipart_live.py` (BT15) | ✅ (على حاويات معزولة، §2) |
| INC0‑AC‑3 | تكامل (`live_db`) | `tests/integration/test_files_repository_rls.py` (E3) | ✅ (على حاويات معزولة) |
| INC0‑AC‑3 | وحدة | `tests/unit/test_files_use_cases.py::test_every_method_guards_unknown_foreign_deleted_single_and_finished_files`، `tests/unit/test_api_files_media_router.py::test_part_urls_and_progress_are_404_for_an_unknown_file_and_409_for_a_single_put` | ✅ |
| INC0‑AC‑3 | حاويات (اختياري، موافق عليه) | `tests/unit/test_public_file_links_containers.py` (19 اختباراً، منها PUT الجزء عبر الحافة و`POST ?uploads` و`?uploadId` و`x-amz-copy-source`) | ✅ |
| INC0‑AC‑4 | وحدة | `tests/unit/test_content_resolver.py::test_a_file_above_the_indexing_cap_fails_as_too_large_without_reading_it`، `::test_a_file_exactly_at_the_indexing_cap_is_read`، `::test_resolve_defaults_its_size_cap_to_the_setting`، و`tests/unit/test_error_catalog.py` | ✅ |
| INC0‑AC‑5 | عقد | `tests/unit/test_api_conventions.py` (OpenAPI في الاتجاهين)، `tests/unit/test_api_rbac.py` (BT9)، `tests/unit/test_error_catalog.py` | ✅ (الوثائق `02`/`03`/`06`/`07`: مراجعة الفرق، لا اختبار آلي لنصوصها) |
| INC0‑AC‑6 | وحدة | `tests/unit/test_access_domain.py::test_permission_matrix` (20 حالة)، `::test_electrical_permissions_are_the_four_and_no_role_was_added`، `tests/unit/test_api_rbac.py` | ✅ |
| INC0‑AC‑7 | معماري | `tests/architecture/test_import_contracts.py::test_import_contracts`، `::test_the_contract_set_is_the_nine_documented`، `::test_ignore_imports_are_exactly_the_sanctioned_ones`، `::test_electrical_contexts_keeps_its_shape`، `::test_the_widened_contracts_reach_the_electrical_sections`، `::test_the_skeleton_is_22_empty_packages` | ✅ |
| INC0‑AC‑7 | معماري (العقد يعضّ فعلاً) | `tests/architecture/test_electrical_contracts.py` (ثمانية اختبارات: نظيف، وقسم يستورد قسماً، و`shared` يستورد قسماً، وابن غير معلن، و`pydantic` في `domain` وفي `shared`، و`fastapi` في `application`، و`electrical` يستورد `files`) | ✅ |
| INC0‑AC‑8 | معماري | `tests/architecture/test_import_contracts.py::test_the_live_docs_state_nine_contracts_and_no_stale_count` (موسَّع على الوثائق الحيّة) | ✅ جزئياً (يدوي) |
| INC0‑AC‑9 | وحدة | `tests/unit/test_ci_supply_chain_job.py` (11 اختباراً)، `tests/unit/test_supply_chain_check.py` (تقرير AGPL وترخيص الجذر) | ✅ |
| INC0‑AC‑9 | قبول | وجود المرفق `sbom.cdx.json` في تشغيل CI على الـPR | مؤجل (يدوي، بوابة المالك ②) |
| INC0‑AC‑10 | وحدة + تشغيل | `tests/unit/test_supply_chain_check.py` (54 اختباراً)، و`deploy/supply-chain/scan.sh` محلياً: 90 مكوّناً، 0 مخالفة ترخيص | ✅ |
| INC0‑AC‑11 | وحدة + تشغيل | `tests/unit/test_supply_chain_check.py::test_an_expired_exception_does_not_cover`، `::test_exit_1_on_a_vulnerability`، `::test_exit_2_when_the_audit_file_is_absent`، `::test_exit_2_when_the_audit_has_fewer_packages_than_the_freeze`، `::test_exit_2_when_the_sbom_has_no_components`؛ و`test_ci_supply_chain_job.py::test_the_job_never_runs_the_quality_evaluation`؛ و`scan.sh`: 89 حزمة مفحوصة، 0 ثغرة | ✅ |
| INC0‑AC‑12 | مراجعة | `pyproject.toml:11` = `license = { text = "Proprietary" }` (تحققتُ)، ولا ملف `LICENSE` في الجذر (تحققتُ)، وحارس `about/source` في `test_api_conventions.py` | ✅ |
| INC0‑AC‑13 | كل البوابات | البوابات 1..6 خضراء (§2)، واختبارات الرفع الأحادي والاستبدال والحصة القائمة خضراء بلا تعديل | ✅ |

**المبرَّر يدوياً أو لاحقاً (لا اختبار آلي ممكن الآن)**

- **INC0‑AC‑8 (`CLAUDE.md:24`):** تعديل المالك (`INC0‑Q‑7`) عند الدمج؛ الاختبار يغطي الوثائق الحيّة الأخرى عمداً. لم يُمسّ `CLAUDE.md` ولا السجلات التاريخية. عند كتابة التقرير ما زال `CLAUDE.md` يقول «8 contracts»، وهذا متوقَّع وليس عيباً في الفرع.
- **INC0‑AC‑9 (SBOM مرفقاً بالتشغيل):** يحتاج تشغيل CI بعد الدفع. ويغطي الاختبار الآلي بنية الوظيفة، وسكربت `scan.sh` نفسه شُغّل محلياً وأنتج SBOM وتقريراً (§2).
- **INC0‑AC‑5 (نصوص `02` §1.6 و`03` و`06` §6 و`07` §4):** وثائق نثرية تُراجع في الفرق؛ العقد الآلي (OpenAPI، الكتالوج، RBAC) مغطّى.

**ثغرة وجدتُها وسددتُها:** حلقة ترقيم الصفحات في `MinioStorage.list_parts` (رفع بأكثر من 1000 جزء) وترجمة أخطاء `complete_multipart` و`object_size` لم يكن لها اختبار يشغّلها (التكامل الحي لا يرى أكثر من صفحة، واختبار minio‑py استبطاني فقط). كتبتُ `tests/unit/test_minio_multipart_adapter.py` (13 اختباراً بعميل مزيّف). الإيداع `5a8d027`.

## 2. نتائج بوابات المشروع

```
1. ruff format --check .   -> 868 files already formatted
2. ruff check .            -> All checks passed!
3. mypy src                -> Success: no issues found in 490 source files
4. lint-imports            -> WireTwin sections are independent and shared is the floor KEPT
                              Contracts: 9 kept, 0 broken.
5. pytest -rs tests/unit tests/architecture tests/eval
                           -> 6435 passed, 19 skipped, 7 warnings in 199.38s   (قبل إضافة ملفي)
                           -> الملف الجديد وحده: 13 passed
   (الـ19 المتخطّاة كلها في test_public_file_links_containers.py: "opt-in container harness: set RUN_EDGE_FILES_TEST=1")
6. deploy/prometheus/test-rules.sh
                           -> SUCCESS: 29 rules found
                              test-rules: OK
7. deploy/supply-chain/scan.sh   (exit=0)
                           -> 89 packages frozen; No known vulnerabilities found
                              supply-chain: 90 components, 0 licence violation(s); 89 packages audited,
                              0 vulnerability finding(s) -> PASS
```

بعد الإيداع الجديد أعدتُ `ruff format --check` (868 ملفاً) و`ruff check` فكانا أخضرين. لم أُعد تشغيل الحزمة الكاملة بعد إضافة الملف الجديد (ملف مستقل لا يعدّل شيئاً قائماً).

**عُدّة الحافة (موافق عليها 2026‑10‑30):** `RUN_EDGE_FILES_TEST=1 .venv/bin/pytest -rs tests/unit/test_public_file_links_containers.py` -> `19 passed in 38.74s`. تشغيل واحد بلا إعادة. التنظيف: `docker ps -a` لا يُظهر إلا حاويات المكدّس `aizzak-*` (عدد غيرها 0)، و`docker network ls` فيها `aizzak_default` و`bridge` و`host` و`none` فقط. لم تنقص صورة.

**الاختبارات الحيّة:** لم تُشغَّل على المكدّس الحيّ. أُطلقت على حاويات مؤقتة معزولة: `postgres:16` على `127.0.0.1:25432` (الأدوار من `deploy/postgres/initdb/10-roles.sh` بكلمات مرور مؤقتة، وقاعدة `aizzak_test` يعيد بناءها `live_db` ويهجّرها)، و`pgsty/minio` على `127.0.0.1:29000` (الدلو `aizzak-test` أنشأته بنداء minio‑py مؤقت)، كلٌّ بـ`--rm` وtmpfs وحد ذاكرة 512 MB، تحت `env -i` و`REQUIRE_LIVE=1` وإعدادات `TEST_DATABASE_URL*` و`TEST_MINIO_*` فقط. النتيجة: `60 passed in 17.23s` (`test_files_repository_rls.py` و`test_minio_storage.py` و`test_files_multipart_live.py`). المحاولة الأولى أعطت 19 خطأ إعداد لأن الدلو لم يكن موجوداً (خطأ في عُدّتي، لا في المنتج). حُذفت الحاويتان بعدها، ولم يبق أي `qa-scratch`.

## 3. الاختبار الاستكشافي

الواجهة غير موجودة (خلفية فقط): RTL و375px لا ينطبقان. فحصتُ بالاختبارات الموجودة وبقراءة الشيفرة؛ كل بند أدناه له اختبار آلي أو حاميه.

| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| حدود حجم الجزء | `Limits(multipart_part_bytes=…)` دون 5 MiB، وفوق 50 MiB، وحد يحتاج >10,000 جزء | رفض عند الإقلاع | `test_limits_reject_a_part_size_that_cannot_be_signed` و`..._accept_the_part_size_bounds` أخضران | ✅ |
| 50 MiB و50 MiB+1 | تسجيل أحادي | يمرّ / `413 files.multipart_required` | `test_a_single_put_above_50_mib_needs_multipart` | ✅ |
| 1 GiB و1 GiB+1 | الوضعان | 64 جزءاً / `413 files.too_large` في الوضعين | `test_one_gib_exactly_...64_part...`، `test_above_one_gib_is_too_large_in_both_modes` | ✅ |
| مجزأ بصفر بايت | `POST /files/multipart` | 422 | `test_a_multipart_registration_of_zero_bytes_is_a_validation_error` | ✅ |
| أرقام أجزاء خارج المدى أو مكرّرة أو أكثر من 100 أو فارغة | `POST …/parts` | 422 | DTO `min_length=1,max_length=100,1..10000` + `test_part_urls_refuse_out_of_range_repeated_and_oversized_requests` | ✅ |
| إتمام قبل وصول كل الأجزاء | complete مع جزء ناقص أو بحجم خاطئ | `409 files.upload_incomplete` ولا تغيّر | `test_complete_with_a_missing_part_is_409_and_changes_nothing_then_succeeds`، `..._wrong_sized_part_is_409` | ✅ |
| رفع مكنوس / ملغى | complete / progress بعد زوال الرفع | `409 files.upload_expired` | `test_complete_of_a_swept_upload_is_409_expired`، `test_progress_lists_...reports_expiry`، حي: `test_a_swept_upload_is_reported_expired` | ✅ |
| إعادة الإتمام بعد فقد الالتزام | الجمع نجح والمعاملة فشلت | تنجح | `test_complete_after_an_assembly_whose_commit_was_lost_still_succeeds` | ✅ |
| استئناف بعد انقطاع | رفع جزء، `GET /files/{id}/upload`، إكمال الناقص | يذكر ما وصل | حي: `test_register_resume_and_complete_a_multipart_file` | ✅ |
| تسجيل متكرر بمفتاح التكرار نفسه | `POST /files/multipart` مرتين | الجواب نفسه، رفع واحد | `test_multipart_register_replays_on_the_same_idempotency_key` | ✅ |
| الحصة وسقف عدد الملفات مع المجزأ | تسجيل فوق حصة الفضاء أو سقف الملفات | 413 / رفض ولا رفع يتيم | `test_multipart_register_refuses_oversize_zero_bytes_and_an_unknown_space` (سُمّي بـ`b47979c`؛ وسقف الملفات في `test_multipart_register_at_the_workspace_file_cap_opens_no_upload`، `test_a_multipart_registration_opens_no_upload_when_the_space_is_unknown` | ✅ |
| مساحة عمل أجنبية | توقيع / تقدّم / إتمام لمعرّف مستأجر آخر | 404 | `test_every_method_guards_unknown_foreign_...`، حي: `test_another_workspace_can_neither_read_sign_nor_complete_the_upload` | ✅ |
| RBAC للمسارات الثلاثة والصلاحيات الأربع | كل دور | `files:write` للمسارات، وجدول الأدوار الأربع × الخمسة | `test_api_rbac.py`، `test_permission_matrix` | ✅ |
| سقف الفهرسة 100 MiB | حجم = السقف، السقف+1 | يقرأ / `knowledge.too_large` ولا `storage.get` | `test_a_file_exactly_at_the_indexing_cap_is_read`، `test_a_file_above_the_indexing_cap_fails_as_too_large_without_reading_it` | ✅ |
| الأخطاء problem+json والرموز الجديدة | `files.upload_incomplete`، `files.upload_expired`، `files.multipart_required` | `application/problem+json` وتزامن `openapi.yaml` | `test_api_files_media_router.py:390`، `test_api_conventions.py`، `test_error_catalog.py` | ✅ |
| فاحص سلسلة التوريد: ترخيص خارج السياسة، واستثناء منتهٍ، وتدقيق ناقص أو غائب | مدخلات اصطناعية | exit 1 / 2 | `test_exit_1_on_a_licence_violation`، `test_an_expired_exception_does_not_cover`، `test_exit_2_when_the_audit_file_is_absent`، `..._has_fewer_packages_than_the_freeze` | ✅ |
| كل عقد استيراد جديد/موسَّع يعضّ | شجرة مؤقتة تخرق كل عقد | يفشل `lint-imports` | `tests/architecture/test_electrical_contracts.py` (8) | ✅ |
| الحافة | PUT جزء موقّع، `POST ?uploads`، `?uploadId`، `x-amz-copy-source` | 200 / 403‑405 / 403 | عُدّة الحاويات 19/19 | ✅ |
| أكثر من 1000 جزء في `list_parts` | عميل مزيّف بثلاث صفحات | كل الصفحات بالعلامة | لم يكن مغطّى؛ أضفتُ `test_list_parts_follows_every_page_by_its_marker` | ✅ بعد الإضافة |
| تسجيل ينشئ رفعاً في MinIO ثم يفشل الإدراج، أو حذف ملف قيد الرفع | قراءة الشيفرة | رفع يتيم | يبقى يتيماً حتى كنس MinIO (24 ساعة)، ولا `abort` في المنفذ | ملاحظة، لا عيب: مقبول في التصميم (`INC0‑D‑8`، `INC0‑R‑6`) |

## 4. الأخطاء المكتشفة

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| — | لا عيوب مانعة أو رئيسية | — | — | — |
| N‑1 | ثغرة تغطية (لا عيب في المنتج): ترقيم صفحات `list_parts` وترجمة أخطاء الإتمام بلا اختبار وحدة | صغيرة | راجع `src/app/infrastructure/storage/minio_storage.py:262-281` | سُدّت بـ`tests/unit/test_minio_multipart_adapter.py` (`5a8d027`) |
| N‑2 | ملاحظة: يخرج `list_parts` من الحلقة بصمت إن كانت الصفحة `is_truncated` ولا علامة تالية (`minio_storage.py:274`)، فيبدو الرفع ناقصاً (409 آمن، لا فساد) | صغيرة | نظرية؛ لا يحدث مع MinIO الحقيقي | أُصلح في `15122fb` (CR #5): يرفع الآن `common.internal`، ويحرسه `test_list_parts_of_a_truncated_page_without_a_marker_is_internal` (§6) |
| N‑3 | ملاحظة: `CLAUDE.md:24` ما زال «8 contracts» حتى يعدّله المالك (`INC0‑Q‑7`) | صغيرة | `grep -n contracts CLAUDE.md` | بيد المالك عند الدمج |

## 5. الحكم

**PASS** — كل INC0‑AC‑1..13 مغطّاة باختبار يعمل أو بتبرير مكتوب (AC‑8 في `CLAUDE.md` للمالك، وAC‑9 مرفق SBOM بعد الدفع)، والبوابات السبع خضراء، وعُدّة الحافة 19/19، والاختبارات الحيّة 60/60 على حاويات معزولة، ولا عيوب مانعة أو رئيسية.

(هذا حكم الجولة الأولى على `5a8d027`. والحكم الساري هو حكم §6.)

## 6. الانحدار بعد إعادة العمل الأولى للمراجعة (2026‑10‑30)

أُعيد التحقق على HEAD `79347eb`، بعد 13 إيداعاً منذ `5a8d027` (`git log 5a8d027..79347eb`).

### 6.1 ما تغيّر، وما يثبته

| الإصلاح | المعيار | الاختبار | الحالة |
|---|---|---|---|
| SR‑1: الموقِّع وحده يوقّع الأجزاء ويقرأ التقدّم ويُتمّ (404 لغيره، ولو كان مالكاً في المساحة نفسها) | INC0‑AC‑3 | `tests/unit/test_files_use_cases.py::test_another_member_of_the_workspace_cannot_sign_list_or_assemble_the_upload`، `::test_another_member_cannot_complete_the_upload_and_nothing_changes`، `::test_a_missing_identity_on_either_side_matches_nothing`؛ `tests/unit/test_api_files_media_router.py::test_another_member_of_the_workspace_gets_404_on_part_urls_progress_and_complete` (الزميل بدور `owner`: 404 `common.not_found` على المسارات الثلاثة، ولا توقيع ولا سرد ولا تجميع ولا حدث)؛ حيّ: `tests/integration/test_files_multipart_live.py::test_another_member_of_the_same_workspace_gets_404_and_writes_nothing` (على الصف الحقيقي و`uploaded_by` كما يقرؤه المستودع) | ✅ |
| SR‑2: بعد التجميع يجب أن يساوي حجم الكائن `size_bytes`، وإلا `409 files.upload_incomplete` | INC0‑AC‑2 | `tests/unit/test_files_use_cases.py::test_a_join_that_produced_no_object_of_the_declared_size_is_incomplete` (حالتان: لا كائن، وكائن بحجم آخر؛ الملف يبقى `uploaded`، ولا حدث، والرفع قابل للاستئناف) | ✅ (وحدة؛ لا يُستحثّ في MinIO الحقيقي، فلا اختبار حيّ له) |
| CR #5 (كان N‑2): صفحة `ListParts` مبتورة بلا علامة تجيب `common.internal` | INC0‑AC‑2 | `tests/unit/test_minio_multipart_adapter.py::test_list_parts_of_a_truncated_page_without_a_marker_is_internal` | ✅ |
| CR #1: تأكيدات هوية ربط الإنتاج | INC0‑AC‑2، INC0‑AC‑13 | `tests/unit/test_composition_root.py`، `tests/unit/test_api_app_shell.py` | ✅ |
| CR #6: إعادة تسمية، وحالة `files.too_many` للمجزأ | INC0‑AC‑2 | `tests/unit/test_api_files_media_router.py::test_multipart_register_at_the_workspace_file_cap_opens_no_upload` (409، ولا صف ولا رفع يتيم) | ✅ |
| سلسلة التوريد: قرار الحزمة مربوط بالترخيص المعلن وبمدى نسخ مغلق | INC0‑AC‑10 | `tests/unit/test_supply_chain_check.py::test_a_package_decision_is_stale_when_the_declared_licence_changes`، `::test_a_package_decision_is_stale_outside_its_version_range`، `::test_an_open_or_unreadable_version_range_makes_the_policy_invalid`، `::test_the_pdfium_decision_is_bound_to_the_one_reviewed_release` | ✅ |
| سلسلة التوريد: معرّف القرار `SRS D-<n>` أو `INC<n>-Q-<n>` وحدهما | INC0‑AC‑10، INC0‑AC‑11 | `::test_a_free_text_decision_makes_the_policy_invalid` (نص حرّ، وحروف صغيرة، وU+2011، وأرقام عربية هندية)، `::test_owner_decision_ids_are_accepted` | ✅ |
| سلسلة التوريد: تاريخ `added` في المستقبل مرفوض | INC0‑AC‑11 | `::test_an_exception_added_in_the_future_makes_the_policy_invalid`، `::test_exit_2_on_an_exception_added_in_the_future` | ✅ |
| سلسلة التوريد: `pip freeze --all`، وستّ ثغرات `pip` مستثناة بـ`INC0‑Q‑10` حتى 2027‑01‑28 | INC0‑AC‑11 | `::test_the_shipped_exceptions_are_the_pip_advisories_of_inc0_q_10_alone`، `::test_an_exception_of_exactly_90_days_covers`، وتشغيل `scan.sh` (§6.2) | ✅ |
| سلسلة التوريد: مكوّن في SBOM لم يُدقَّق مخالفة | INC0‑AC‑11 | `::test_a_component_left_out_of_the_freeze_is_named_and_is_a_violation`، `::test_exit_1_when_an_sbom_component_was_not_audited` | ✅ |
| الوثائق، وتعليقات SR‑8 في `deploy/nginx/app-locations.conf` | INC0‑AC‑5، INC0‑AC‑8 | `test_api_conventions.py`، `test_import_contracts.py` (خضراء)، ومراجعة الفرق | ✅ |

`INC0‑Q‑10` مسجَّل قراراً للمالك في `lld-inc-0.md:1082` و`docs/stack-commands.md:425`.

### 6.2 البوابات (أعدتُ تشغيلها بنفسي على `79347eb`)

```
1. .venv/bin/ruff format --check .   -> 868 files already formatted
2. .venv/bin/ruff check .            -> All checks passed!
3. .venv/bin/mypy src                -> Success: no issues found in 490 source files
4. .venv/bin/lint-imports            -> Contracts: 9 kept, 0 broken.
5. .venv/bin/pytest -rs tests/unit tests/architecture tests/eval
                                     -> 6521 passed, 19 skipped, 7 warnings in 213.02s (0:03:33)   exit=0
   (الـ19 المتخطّاة كلها في test_public_file_links_containers.py: opt-in container harness)
6. deploy/prometheus/test-rules.sh   -> SUCCESS: 29 rules found
                                        test-rules: OK
7. PYTHON=python3.12 deploy/supply-chain/scan.sh   (exit 0)
                                     -> 90 packages frozen
                                        Found 12 known vulnerabilities in 1 package
                                        pip-audit exit code: 1
                                        supply-chain: 90 frozen; 90 components, 0 licence violation(s),
                                        0 not audited; 90 packages audited, 0 vulnerability finding(s),
                                        6 excepted -> PASS
```

«12» عند pip-audit هي ستّ ثغرات مميّزة لـ`pip` 24.0، كل منها مكرّرة مرتين في `build/supply-chain/audit.json` (عددتُها: 12 مدخلاً، 6 معرّفات). والستّ كلها `excepted` و`applied` في التقرير، فلا ثغرة بلا غطاء. وخروج pip-audit بـ1 متوقَّع، والحكم للفاحص.

### 6.3 عُدّة الحافة

لم تُعَد. التغيّر تحت `deploy/nginx/` بين `5a8d027` و`79347eb` هو `app-locations.conf` وحده (14 سطراً)، وكل الأسطر المضافة والمحذوفة تعليقات `#` (فحصتُ ذلك: صفر سطر غير تعليق). فتبقى نتيجة §2 (19/19) سارية، ولا حاجة لموافقة جديدة.

### 6.4 الاختبارات الحيّة (حاويات مؤقتة فقط)

- `postgres:16` على `127.0.0.1:25432` (الأدوار من `10-roles.sh` بكلمات مرور مؤقتة)، و`pgsty/minio:RELEASE.2026-08-04T00-00-00Z` على `127.0.0.1:29000` (الدلو `aizzak-test` أُنشئ قبل التشغيل هذه المرة). كلٌّ بـ`--rm` وtmpfs و`--memory 512m`، بلا وحدة تخزين مسمّاة.
- `env -i` مع `REQUIRE_LIVE=1` و`TEST_DATABASE_URL*` و`TEST_MINIO_*` وحدها. لا `.env` ولا Vault ولا المكدّس الحيّ.
- `tests/integration/test_files_repository_rls.py` و`test_minio_storage.py` و`test_files_multipart_live.py` -> `61 passed in 22.50s` (كانت 60؛ الزائد هو اختبار SR‑1 الحيّ).
- بعدها: `docker rm -f qa-scratch-pg qa-scratch-minio`. و`docker ps -a` لا يُظهر حاوية `qa-scratch` (0)، ولا حاوية خارج `aizzak-*` (0). و`docker network ls`: `aizzak_default` و`bridge` و`host` و`none`.

### 6.5 استكشاف إضافي

| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| ترتيب حارس SR‑1 في `assemble` | قراءة `use_cases.py:745-754` | ملف أحادي أو مكتمل يعود بصمت كما كان، والمجزأ لغير الموقِّع 404 قبل أي نداء تخزين | كذلك؛ وتحرسه اختبارات §6.1 (`listed == 0`، `completed == []`) | ✅ |
| `parse_versions` بمدخلات غريبة | سكربت يحمّل `supply_chain_check.py` | المفتوح والمجهول مرفوضان | `>=0.18` و`<1` و`!=1.0` و`==5.14.0rc1` و`==٥.١٤` مرفوضة؛ `5.14.0.post1` و`5.14.0+local` خارج `==5.14.0` | ✅ |
| `_DECISION_ID` | المدخلات نفسها | ASCII بالضبط | `INC0‑Q‑10` (U+2011) و`srs d-33` و`SRS D-33 ` (مسافة) و`INC0-Q-10; extra` و`D-33` مرفوضة | ✅ |
| مدى نسخ فارغ | `parse_versions(">=5,<=4")` | رفض أو فشل مغلق | يُقبل، لكن القرار لا يطابق أي نسخة فيصير «قراراً قديماً» (مخالفة) | ✅ فشل مغلق (R1‑2) |
| «اليوم» في فحص `added` المستقبلي | قراءة `supply_chain_check.py:929` | تاريخ ثابت المرجع | `date.today()` بالتوقيت المحلي؛ المضيف +03 وCI على UTC | ملاحظة (R1‑1) |

### 6.6 الأخطاء

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| — | لا عيوب مانعة أو رئيسية | — | — | — |
| R1‑1 | فحص «`added` في المستقبل» يقارن بـ`date.today()` المحلي (`deploy/supply-chain/supply_chain_check.py:929`). فاستثناء يضيفه المالك بتاريخ يومه المحلي بين 00:00 و03:00 (+03) يرفضه CI (UTC) بخروج 2 لمدة تصل إلى ثلاث ساعات. لا يمسّ الاستثناءات الحالية (2026‑10‑30 قد بلغ في UTC). | صغيرة | أضف `[[vulnerability]]` بـ`added` = تاريخ الغد في UTC، وشغّل الفاحص بـ`--today` = تاريخ اليوم في UTC: خروج 2 | للعلم؛ يفشل مغلقاً ويزول وحده. اقتراح للمالك: `date.today()` بتوقيت UTC، أو تدوين القاعدة في `stack-commands.md` |
| R1‑2 | `parse_versions` يقبل مدى فارغاً (`>=5,<=4`) (`supply_chain_check.py:197-213`) | صغيرة | `parse_versions(">=5,<=4")` يعيد مدى ولا يرفع | للعلم؛ يفشل مغلقاً (القرار لا يطابق شيئاً فيصير مخالفة) |
| N‑3 | `CLAUDE.md:24` ما زال «8 contracts» | صغيرة | كما في §4 | بيد المالك (`INC0‑Q‑7`) |

### 6.7 الحكم الساري

**PASS** — على `79347eb`: البوابات السبع خضراء (6521 ناجحاً، و19 متخطّى كلها عُدّة الحافة الاختيارية؛ و`scan.sh` بلا مخالفة ولا ثغرة بلا غطاء)، والاختبارات الحيّة 61/61 على حاويات مؤقتة حُذفت، وعُدّة الحافة لم تُعَد لأن التغيّر في nginx تعليقات فقط. ولكل إصلاح (SR‑1 وSR‑2 وCR #5 وفحوص سلسلة التوريد الجديدة) اختبار يعمل. وكل INC0‑AC‑1..13 ما زالت مغطّاة أو مبرَّرة كما في §1 (AC‑8 في `CLAUDE.md` للمالك، وAC‑9 مرفق SBOM بعد الدفع). ولا عيوب مانعة أو رئيسية، والملاحظتان R1‑1 وR1‑2 صغيرتان وتفشلان مغلقتين.

</div>
