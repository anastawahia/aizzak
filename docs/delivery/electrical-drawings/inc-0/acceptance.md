<div dir="rtl">

# قبول مالك المنتج: electrical‑drawings، الزيادة INC‑0 (تمهيد المنصة لـWireTwin)

- **الفرع:** `feat/electrical-drawings-inc-0`، و`HEAD` هو `79347eb`، وفيه 23 إيداعاً فوق `master` (`8236e7b`).
- **المرجع الملزم:** `docs/lld-inc-0.md` §5.1 (المعايير INC0‑AC‑1..13، وكلها Must)، و§5.2 (الاختبارات لكل معيار)، و§7.1 (قرارات المالك INC0‑Q‑1..10).
- **المراجِع:** مالك المنتج، 2026‑10‑30. قرأتُ الأدلة وتحقّقتُ منها، ولم أعدّل شيفرة ولا اختباراً.
- **ما شغّلتُه بنفسي:**
  - `.venv/bin/lint-imports`: النتيجة «Contracts: 9 kept, 0 broken.».
  - `pytest` على `tests/architecture` و22 ملف اختبار وحدة يخصّ INC‑0: النتيجة `678 passed`، ورمز الخروج 0.
  - فحوص قراءة فقط: `grep` و`git diff` و`git log`، وقراءة `build/supply-chain/` (مخرج `scan.sh` على `79347eb`).
- **ما لم أشغّله:** لم أشغّل `tests/integration/` ولا عُدّة الحافة، لأن المكدّس الحيّ مستعمل. وأستند فيهما إلى ما سجّلته الجودة:
  - الاختبارات الحيّة: 61/61 على حاويات مؤقتة (`test-plan.md` §6.4).
  - عُدّة الحافة: 19/19 في الجولة الأولى بموافقة المالك (`test-plan.md` §2 و§6.3).

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| INC0‑AC‑1 | **القيم** في `src/app/framework/settings/settings.py`: `max_upload_bytes` = 1,073,741,824 (`:1000`)، و`max_single_put_bytes` = 52,428,800 (`:1005`)، و`multipart_part_bytes` = 16,777,216 (`:1006`)، و`knowledge_max_index_bytes` = 104,857,600 (`:1009`، INC0‑Q‑2)، و`max_space_bytes` = 10,737,418,240 (`:1049`، INC0‑Q‑1). والمُدقّق الذي يوقف الإقلاع في `:1232-1241`.<br>**وحدة:** `tests/unit/test_foundation.py::test_limits_defaults_for_the_multipart_upload`، و`::test_limits_reject_a_part_size_that_cannot_be_signed`، و`::test_limits_accept_the_part_size_bounds`. ومعها `tests/unit/test_files_use_cases.py::test_a_single_put_above_50_mib_needs_multipart`، و`::test_above_one_gib_is_too_large_in_both_modes`، و`::test_one_gib_exactly_registers_as_a_64_part_multipart_upload`.<br>**على السلك:** `test_api_files_media_router.py` و`test_api_spaces_router.py`.<br>**الحافة:** `test_public_file_links_edge.py::test_the_files_route_has_the_decided_shape` و`::test_the_files_route_guard_fails_on_each_regression`. والقيمة `client_max_body_size 50m` في `app-locations.conf:371`، وفرق `deploy/nginx/` كله تعليقات (صفر سطر غير تعليق، تحقّقتُ).<br>**PUT الواحد كما هو:** `test_file_replacement.py` بلا فرق، و`test_files_use_cases.py` إضافات فقط | ✅ مقبول |
| INC0‑AC‑2 | **النطاق:** `tests/unit/test_files_domain.py` (BT1).<br>**الخدمة:** في `test_files_use_cases.py`: `::test_sign_parts_signs_exactly_the_requested_numbers`، و`::test_sign_parts_rejects_a_number_out_of_range_or_repeated`، و`::test_progress_lists_the_held_parts_and_reports_expiry`، و`::test_assemble_with_a_missing_or_wrong_sized_part_never_completes`، و`::test_assemble_joins_the_parts_in_order_and_ignores_strays`، و`::test_assemble_again_after_a_join_is_safe_and_a_sweep_is_expired`، و`::test_complete_assembles_before_the_unit_of_work_and_a_single_file_skips_it`، و`::test_a_join_that_produced_no_object_of_the_declared_size_is_incomplete` (SR‑2).<br>**الموجّه:** في `test_api_files_media_router.py`: `::test_multipart_register_replays_on_the_same_idempotency_key`، و`::test_multipart_register_at_the_workspace_file_cap_opens_no_upload`، و`::test_complete_with_a_missing_part_is_409_and_changes_nothing_then_succeeds`، و`::test_complete_of_a_swept_upload_is_409_expired`، و`::test_complete_after_an_assembly_whose_commit_was_lost_still_succeeds`، و`::test_complete_of_a_multipart_file_replaces_the_older_namesake`. وحصة الفضاء عبر `POST /files/multipart` في `test_api_spaces_router.py:556-570`.<br>**المحوّل:** `test_storage_handle.py`، و`test_minio_multipart_api.py`، و`test_minio_multipart_adapter.py`.<br>**ربط الإنتاج:** `test_composition_root.py` و`test_api_app_shell.py` (إصلاح CR #1).<br>**حيّ (سجلّ الجودة، 61/61):** `tests/integration/test_minio_storage.py` (BT14)، و`test_files_multipart_live.py::test_register_resume_and_complete_a_multipart_file`، و`::test_a_swept_upload_is_reported_expired` | ✅ مقبول |
| INC0‑AC‑3 | **RLS:** `tests/integration/test_files_repository_rls.py` (E3)، من سجلّ الجودة (61/61).<br>**مساحة عمل أخرى تأخذ 404:** `test_files_use_cases.py::test_every_method_guards_unknown_foreign_deleted_single_and_finished_files`، وحيّاً `test_files_multipart_live.py::test_another_workspace_can_neither_read_sign_nor_complete_the_upload`.<br>**عضو آخر في المساحة نفسها يأخذ 404 (SR‑1):** الحارس `_registered_by_caller` في `use_cases.py:694` و`:753` و`:802-810`، ويفشل مغلقاً على هوية فارغة. وتثبته الاختبارات `test_files_use_cases.py::test_another_member_of_the_workspace_cannot_sign_list_or_assemble_the_upload`، و`::test_another_member_cannot_complete_the_upload_and_nothing_changes`، و`::test_a_missing_identity_on_either_side_matches_nothing`، و`test_api_files_media_router.py::test_another_member_of_the_workspace_gets_404_on_part_urls_progress_and_complete`، وحيّاً `::test_another_member_of_the_same_workspace_gets_404_and_writes_nothing`.<br>**الحافة:** عُدّة `test_public_file_links_containers.py` نجحت 19/19 في تشغيل الجولة الأولى الموافق عليه، ومنها `test_a_copy_source_on_a_part_never_reaches_minio`. ولم يتغيّر بعدها في nginx إلا تعليقات | ✅ مقبول |
| INC0‑AC‑4 | `tests/unit/test_content_resolver.py::test_a_file_above_the_indexing_cap_fails_as_too_large_without_reading_it`، و`::test_a_file_exactly_at_the_indexing_cap_is_read`، و`::test_resolve_defaults_its_size_cap_to_the_setting`.<br>الرمز `knowledge.too_large` في `ERROR_CATALOG` (`framework/errors.py:138`)، ويثبته `test_error_catalog.py`.<br>`test_media_generation.py` و`test_content_agents.py` خضراء | ✅ مقبول |
| INC0‑AC‑5 | **`openapi.yaml`:** `registerMultipartFile` (`:386`)، و`getFileUpload` (`:396`)، و`/files/{id}/upload/part-urls` (`:402`)، ووصف `registerFile` (`:377`).<br>**المطابقة في الاتجاهين:** `test_api_conventions.py::test_the_application_publishes_exactly_the_operations_the_contract_declares`، و`::test_the_idempotency_header_is_declared_on_exactly_the_contracts_operations`.<br>**الصلاحيات:** `test_api_rbac.py`، والصفوف الثلاثة في `EXPECTED` (`:139-141`) بـ`files:write`.<br>**الرموز الأربعة:** في `framework/errors.py:117-119` و`:138`.<br>**الوثائق:** قرأتُ فرق `02-port-contracts.md` (`MultipartStorage`)، و`03-api-spec.md`، و`06-domain-models.md` (INV‑F5)، و`07-nfr-slo.md` §4 | ✅ مقبول |
| INC0‑AC‑6 | **الشيفرة:** `access/domain/value_objects.py:63-67`، أربع قيم `electrical:*`، و`electrical:review` غائب.<br>**الاختبارات:** `test_access_domain.py::test_permission_matrix` (20 حالة)، و`::test_electrical_permissions_are_the_four_and_no_role_was_added`، و`test_api_rbac.py::test_every_role_can_reach_something_and_no_role_can_reach_everything` (`:539`).<br>**الوثيقة:** صفوف `05-rbac-config-secrets.md` في §1.2 (`:31`) و§1.3 (`:65-68`) تطابق جدول LLD §3.3.8، وقد قارنتُها | ✅ مقبول |
| INC0‑AC‑7 | **شغّلتُ `lint-imports`:** «Contracts: 9 kept, 0 broken.». وفي `.importlinter` تسعة أقسام `[importlinter:contract:*]`، وفرقه بلا أي سطر `ignore_imports` (تحقّقتُ).<br>**الشكل:** `tests/architecture/test_import_contracts.py::test_import_contracts`، و`::test_the_contract_set_is_the_nine_documented`، و`::test_ignore_imports_are_exactly_the_sanctioned_ones`، و`::test_electrical_contexts_keeps_its_shape`، و`::test_the_widened_contracts_reach_the_electrical_sections`، و`::test_the_skeleton_is_22_empty_packages`.<br>**العقد يفشل حين يُخرق:** `tests/architecture/test_electrical_contracts.py`، ثمانية اختبارات تكسر كل عقد جديد أو موسَّع فعلاً | ✅ مقبول |
| INC0‑AC‑8 | **الاختبار:** `test_import_contracts.py::test_the_live_docs_state_nine_contracts_and_no_stale_count`.<br>**بحثي:** لا «8 عقود» ولا «ثمانية عقود» ولا «العقود الثمانية» في `architecture.md` و`architecture.html` و`quickstart.md` و`stack-commands.md` و`09-testing-strategy.md` و`README.md`.<br>**السجلّات المؤرّخة:** `baseline.md` و`ROADMAP.md` و`capacity-status.md` بلا فرق.<br>**`CLAUDE.md:24`:** ما زال «(8 contracts)»، والمعيار نفسه يتركه لإنسان (INC0‑Q‑7) | ✅ مقبول |
| INC0‑AC‑9 | **الوظيفة:** `ci.yml:201-231`: `supply-chain` تعمل على `push: [master]` و`pull_request`، بصلاحية `contents: read` و`timeout-minutes: 15`. تشغّل `scan.sh`، وترفع المرفق بـ`upload-artifact@v4` مع `if: always()`. ويثبت ذلك `test_ci_supply_chain_job.py` (11 اختباراً).<br>**صيغة الـSBOM:** `scan.sh:56-59` يشغّل `cyclonedx-py --sv 1.6 --of JSON`.<br>**المخرج المحلي على `79347eb`:** في `build/supply-chain/sbom.cdx.json` القيم `bomFormat` = CycloneDX و`specVersion` = 1.6 و90 مكوّناً. و`report.md` يعلّم `pymupdf` 1.28.2 (AGPL) ويذكر ترخيص الجذر `Proprietary`، ويثبته `test_supply_chain_check.py::test_the_report_names_agpl_components_and_the_root_licence`.<br>**ما بقي:** رؤية المرفق في أول تشغيل CI على الـPR، وهو للبوابة ② | ✅ مقبول |
| INC0‑AC‑10 | **`test_supply_chain_check.py`**، ومنه: `::test_or_needs_one_allowed_branch_and_and_needs_all`، و`::test_with_needs_an_allowed_base_and_a_listed_exception`، و`::test_ofl_is_refused_for_a_library_and_accepted_for_a_font_file`، و`::test_exit_1_on_a_licence_violation`، و`::test_an_approved_entry_without_a_decision_is_refused`، و`::test_a_free_text_decision_makes_the_policy_invalid`، و`::test_a_package_decision_is_stale_when_the_declared_licence_changes`، و`::test_a_package_decision_is_stale_outside_its_version_range`، و`::test_allowed_list_is_nfr_lic_01_and_the_approved_list_is_srs_d_32`.<br>**السياسة:** كل مدخل `approved` و`[[package]]` يحمل `decision` (`SRS D-32` أو `SRS D-33`).<br>**أدوات التطوير خارج الفحص:** الفحص على `'.[parsers]'` كما الصورة (`scan.sh:44`، `Dockerfile:43`)، و`[dev]` خارجه.<br>**`scan.sh`:** PASS، 90 مكوّناً و0 مخالفة ترخيص | ✅ مقبول |
| INC0‑AC‑11 | **`test_supply_chain_check.py`:** `::test_exit_1_on_a_vulnerability`، و`::test_an_expired_exception_does_not_cover`، و`::test_an_exception_added_in_the_future_makes_the_policy_invalid`، و`::test_exit_2_when_the_audit_file_is_absent`، و`::test_exit_2_when_the_audit_has_fewer_packages_than_the_freeze`، و`::test_exit_2_when_the_sbom_has_no_components`، و`::test_exit_1_when_an_sbom_component_was_not_audited`، و`::test_the_shipped_exceptions_are_the_pip_advisories_of_inc0_q_10_alone`.<br>**لا تقييم جودة في CI:** `test_ci_supply_chain_job.py::test_the_job_never_runs_the_quality_evaluation`.<br>**`report.md:109-111`:** 90 حزمة مدقَّقة، و«Not audited: none»، وست ثغرات لـ`pip` 24.0 كلها `excepted` بـ`decision = "INC0-Q-10"` حتى 2027‑01‑28، ولا ثغرة بلا غطاء | ✅ مقبول |
| INC0‑AC‑12 | **`pyproject.toml:11`:** `license = { text = "Proprietary" }`. وفرق `pyproject.toml` سطر `pymupdf>=1.24,<2` وحده (INC0‑Q‑9).<br>**الجذر:** لا `LICENSE` ولا `COPYING`.<br>**`openapi.yaml`:** لا مسار `about` أو `source`، و`test_api_conventions.py:288` يحرس الاتجاهين | ✅ مقبول |
| INC0‑AC‑13 | **البوابات على `79347eb`** (أعادها المنسّق والجودة، `test-plan.md` §6.2): 868 ملفاً منسّقاً، وruff check نظيف، وmypy بلا مشكلة في 490 ملفاً، و«9 kept, 0 broken»، وpytest «6521 passed, 19 skipped» (الـ19 كلها عُدّة الحافة الاختيارية)، و`test-rules.sh` OK.<br>**ما أعدتُه أنا:** 678 اختباراً ناجحاً.<br>**الاختبارات القائمة:** `test_file_replacement.py` بلا فرق. و`test_files_use_cases.py` إضافات ومعها سطر توقيع واحد. و`test_space_quota.py` لم يتغيّر فيه إلا الحدّ الافتراضي من 1 GiB إلى 10 GiB (INC0‑Q‑1)، والتأكيدات كما هي.<br>**ملف الحمل:** فرق `deploy/load/lib/profile.js` تعليق وحده (`:231-233`).<br>**الهجرات:** لا سلسلة جديدة. في `migrations/versions/` 12 مجلداً قبل الفرع وبعده، والجديد `files/0005_file_multipart.py` داخل سلسلة `files` | ✅ مقبول |

## إصلاحات مراجعة الشيفرة #1 و#2 و#3 (تحقّقتُ منها بنفسي)

لم يُعِد مراجع الشيفرة مراجعة هذه الإصلاحات، فتحقّقتُ منها في الشيفرة والاختبارات:

- **#1، ربط الإنتاج (`2786b46`):** أُضيفت التأكيدات الخمسة التي طلبها المراجع حرفياً إلى `tests/unit/test_composition_root.py`:
  - `complete._assembler is files.multipart`
  - `multipart._storage is storage`
  - `transfers._register._multipart is storage`
  - `multipart_quota._registrar is files.multipart_registrar`
  - `multipart_quota._spaces is store`

  وأُضيف إلى `tests/unit/test_api_app_shell.py` التأكيد `services.multipart_quota is not None`. شغّلتُها فنجحت.
- **#2، `pip` في التدقيق (`c8651c9`، INC0‑Q‑10):**
  - `scan.sh:46` يشغّل `pip freeze --all`.
  - في `license-policy.toml` ستة مداخل `[[vulnerability]]` لـ`pip` وحده، بالقيم `decision = "INC0-Q-10"` و`added = 2026-10-30` و`expires = 2027-01-28`.
  - الفاحص يعدّ كل مكوّن في الـSBOM لم يُدقَّق مخالفة (`check_coverage`)، ويذكر ذلك في `report.md` («Not audited: none»).
  - يثبته `::test_a_component_left_out_of_the_freeze_is_named_and_is_a_violation` و`::test_exit_1_when_an_sbom_component_was_not_audited`.
- **#3، قرار الحزمة مربوط بما رُوجع (`c206859`):**
  - `_decided` في `supply_chain_check.py:529-559` يعدّ القرار قديماً، ويحتاج المالك، إذا تغيّر الترخيص المعلن (`declared`) أو خرج الإصدار عن المدى (`versions`).
  - المدى في السياسة: `pyphen >=0.18.1,<0.19`، و`pycryptodome >=4.2.0,<4.3`، و`pypdfium2 ==5.14.0`.
  - حُذف الاختبار القديم `test_a_package_decision_overrides_the_metadata` الذي كان يثبّت التجاوز. وحلّ محلّه `::test_a_package_decision_is_stale_when_the_declared_licence_changes`، ومعه `::test_a_package_decision_is_stale_outside_its_version_range`.

## ملاحظات غير حاجبة مقبولة

**مراجعة الشيفرة** (ما بقي من التحسينات؛ و#5 و#6 و#8 و#9 و#10 أُصلحت كلها):
- **#4:** ما زال `multipart_registrar` حقلاً في `FileUseCases`. أُضيف التعليق وحده (`d61c81b`)، ولم يُنقل الحقل. والمراجع قبله بهذا الشرط.
- **#7:** لم يُعَد رسم الشكل 4 في `architecture.html`. وصُحّح عنوانه ليطابق الشكل ذا الاثنتي عشرة وحدة (`2ee847f`)، وهو البديل الذي اقترحه المراجع.

**المراجعة الأمنية:** الحكم الأصلي FAIL كان بسبب SR‑1، وقد أُصلح (`bc6291d`) وتحقّقتُ منه في AC‑3. والحكم الساري PASS على `79347eb`. وما بقي مفتوحاً، وأقبله بالاسم:
- **SR‑3** (منخفضة، مؤجَّلة إلى متابعة): أجزاء الرفع المهجور تبقى حتى يكنسها MinIO. ولا إلغاء عند الحذف أو التطهير، ولا ضبط صريح لـ`MINIO_API_STALE_UPLOADS_EXPIRY`. والمالك قبل الخطر في INC0‑Q‑3 (INC0‑R‑6).
- **SR‑4** (منخفضة، مفتوحة): سقف الفهرسة يُقارَن بالحجم المعلن لا بحجم الكائن. وهذا يصحّ على الخادم بحدّ الحافة 50 MiB وبفحص SR‑2، ويبقى أثره في بيئة التطوير وحدها.
- **SR‑7** (منخفضة، مؤجَّلة إلى متابعة): إجراءات CI مثبّتة بوسم متحرك، واعتماديات أداتي الفحص المتعدّية بلا بصمات.
- **SR‑9** (معلومة، مقبولة): معرّف الرفع ظاهر في رابط الجزء، ولا يضيف خطراً على ث‑16.
- **SR‑10** (معلومة، مقبولة): مرفقات `supply-chain-<sha>` مقروءة 90 يوماً لأن المستودع عام.
- **SR‑11** (معلومة، مؤجَّلة إلى المالك مع `PLT‑17`): تنبيهات في `.venv` المحلية وفي صورة `app` المبنية، منها `pillow` و`pypdf` و`pyjwt` و`urllib3`.
- **SR‑12** (معلومة، مقبولة): قفل الحصة يمتدّ رحلةً إلى MinIO (INC0‑R‑7).
- **SR‑13** (معلومة، مقبولة): في بيئة التطوير تذهب الروابط إلى MinIO مباشرة بلا حرّاس الحافة.
- **SR‑14** (منخفضة، قائمة قبل INC‑0 وخارج نطاقها): أي عضو يملك `files:write` يُتمّ ملف PUT الواحد. وهو قرار منفصل للمالك (انظر البوابة ②).
- **SR‑15** (معلومة): معرّف قرار صحيح الصيغة لكنه غير مسجّل، مثل `INC0-Q-99`، يُقبل. والحماية في الاختبار الذي يثبّت معرّفات السياسة المشحونة.
- **SR‑16** (معلومة): استثناءات `pip` تنتهي في 2027‑01‑28. وحتى ذلك لا يُشغَّل `pip install` داخل حاوية `app`.

**الجودة:**
- **R1‑1** (صغيرة): فحص «`added` في المستقبل» يقارن بـ`date.today()` المحلي (`supply_chain_check.py:929`). فقد يُرفض استثناء أُضيف بين منتصف الليل والثالثة فجراً (+03) حتى ثلاث ساعات. وهو يفشل مغلقاً.
- **R1‑2** (صغيرة): `parse_versions` يقبل مدى فارغاً (`>=5,<=4`). وهو يفشل مغلقاً لأن القرار لا يطابق أي إصدار.
- **N‑3** (صغيرة): `CLAUDE.md:24` ما زال «8 contracts». يعدّله المالك عند الدمج (INC0‑Q‑7).

**ملاحظاتي (مالك المنتج):**
- **PO‑1** (صغيرة، تعليق قديم):
  - التعليق في `settings.py:1077-1080` يبرّر `parser_max_uncompressed_mb` = 512 بأن `max_upload_bytes` = «50 MB».
  - بعد INC‑0 صار ملف Office بحجم 51 إلى 100 MiB يدخل الفهرسة. وبنسبة ضغط فوق 5:1 تقريباً قد يتجاوز 512 MB غير مضغوطة، فتصير وثيقته `failed` بخطأ صريح، لا بصمت.
  - يُراجَع مع INC0‑R‑10 و`PLT‑03`.
- **PO‑2** (انحراف في وثائق الأدلة، لا في الدليل نفسه):
  - صفّ INC0‑AC‑2 في `test-plan.md` §1، وجدول §3، يذكران `test_multipart_register_is_gated_by_the_space_quota_and_the_file_limit`. وهذا الاسم غير موجود، لأنه أُعيدت تسميته في `b47979c` (CR #6) إلى `test_multipart_register_refuses_oversize_zero_bytes_and_an_unknown_space`. وتغطية سقف الملفات صارت في `::test_multipart_register_at_the_workspace_file_cap_opens_no_upload`.
  - LLD §5.2 (BT12) يقول إن `ignore_imports` ستة أسطر، والقائم سبعة. والاختبار يثبّت السبعة، ومراجعة الشيفرة قبلت ذلك على أنه خطأ عدّ في LLD.
- **PO‑3** (للدمج): وثائق التسليم غير مُلتزمة:
  - `docs/requirements.md` معدّل غير ملتزم، وفيه SRS 0.16 و0.17 بالقرارين SRS D-32 وSRS D-33.
  - `docs/lld-inc-0.md` وفيه INC0‑Q‑8..10، و`status.md`، و`inc-0/*` كلها غير متتبَّعة أو غير ملتزمة.
  - و`license-policy.toml` يستشهد بـ`SRS D-32` و`SRS D-33`، وهما غير موجودين في `requirements.md` على `HEAD` (`git grep` فارغ).

**ما بقي مفتوحاً من التصميم (للعلم، لا يحجب):**
- **INC0‑R‑10:** سقف الفهرسة 100 MiB لم يُقَس، ويُقاس مع `PLT‑03`.
- **INC0‑R‑5:** الرفع المهجور يُعدّ في الحصة حتى يُحذف.
- **INC0‑F‑1..16:** التعارضات لم تُصحَّح في `hld.md` بعد، وتصحيحها بقرار المالك.
- **INC0‑F‑13:** `app.modules.admin` خارج العقد `modules-independent`.
- **INC0‑F‑16:** `torch==2.5.1` في صورة التضمين.

## للمالك عند البوابة ②

1. **`CLAUDE.md:24`:** يعدّله المالك بنفسه من «(8 contracts)» إلى «(9 contracts)» (INC0‑Q‑7، N‑3). ويقرّر معه هل يُضاف سطر للوظيفة `supply-chain` إلى قائمة البوابات.
2. **الترحيل `files/0005_file_multipart`:**
   - يُطبَّق على المكدّس الحيّ عبر خدمة `migrate` (`python -m app.ops.provision`) **قبل** نشر الشيفرة الجديدة، ولا يُشغَّل `alembic upgrade head` مباشرة.
   - التراجع عنه يُسقط القيد والعمودين، فيفقد `upload_id` لكل رفع مجزأ جارٍ.
3. **CI سيحمرّ عمداً** حتى يراجعه المالك في هذه الحالات:
   - أي إصدار جديد لـ`pypdfium2` (غير 5.14.0).
   - `pyphen` 0.19 أو أحدث.
   - `pycryptodome` 4.3 أو أحدث.
   - يوم 2027‑01‑29، إن لم يُرقَّ `pip` في صورة `app` إلى 26.2 أو أحدث مع `PLT‑17` (SR‑16). ويلزم جدولة `PLT‑17` قبل ذلك.
4. **SR‑14:** إتمام ملف PUT الواحد متاح لأي عضو يملك `files:write`، وهذا قائم منذ 6.1 قبل INC‑0. قرار منفصل للمالك: يبقى كما هو، أو يُقصر على من سجّل الملف.
5. **المتابعات المؤجّلة:**
   - SR‑3: إلغاء الرفع عند الحذف والتطهير، وضبط `MINIO_API_STALE_UPLOADS_EXPIRY` صراحةً.
   - SR‑7: تثبيت الإجراءات ببصمة الالتزام، وبصمات لأدوات الفحص.
   - SR‑11: إعادة بناء الصورة وتحديث `.venv`، مع `PLT‑17`.
6. **INC0‑AC‑9:** في أول تشغيل CI على الـPR، يُتأكَّد أن المرفق `supply-chain-<sha>` موجود وفيه `sbom.cdx.json`.
7. **PO‑3:** تُلتزم وثائق التسليم مع الدمج حتى يجد قارئ السياسة قراريها: `requirements.md` (SRS D-32 وD-33)، و`lld-inc-0.md`، و`status.md`، و`inc-0/`.
8. **اختياري (R1‑1):** يقارن الفاحص بتاريخ اليوم بتوقيت UTC، أو تُدوَّن القاعدة في `stack-commands.md`.

## الحكم النهائي

**مقبول** — المعايير INC0‑AC‑1..13 كلها ✅، ولكلٍّ منها دليل موجود في المستودع ويعمل:
- أعدتُ تشغيل 678 اختباراً و`lint-imports` بنفسي، وكلها خضراء.
- البوابات الست و`scan.sh` خضراء على `79347eb`.
- الحيّ 61/61 والحافة 19/19 من سجلّ الجودة.

المراجعات الثلاث PASS على `79347eb`. ونقطة FAIL الأمنية الوحيدة (SR‑1) أُصلحت وتحقّقتُ منها، والملاحظات المهمة الثلاث في مراجعة الشيفرة أُصلحت وتحقّقتُ منها. وما بقي منخفض أو معلومة، وقبلتُه بالاسم أعلاه. ويبقى الدمج قرار المالك عند البوابة ②، ومعه البنود الثمانية أعلاه.

</div>
