<div dir="rtl">

# قبول مالك المنتج: روابط الملفّات العامّة (`public-file-links`، الخطوة 6.2)

- **الفرع:** `feat/public-file-links`، ‏HEAD ‏`c02c4c9`، والأساس `f1c8bda` (هو `origin/master`). ستّة commits: `9154fc4`، و`bf5e97f`، و`61db874`، و`6181706`، و`4da254e`، و`c02c4c9`.
- **النطاق:** US‑1…US‑6، و33 معيار قبول. **ويُقبل هنا ما يسلّمه هذا الـPR قبل 7.3**، بقرار بوابة ① ‏Q‑5 (أ): قبول الخطّة لـ6.2 («من جهازٍ خارج الخادم») لا يقع إلّا على الخادم. فتصير 6.2 «🟡 مدموجة، بانتظار 7.3» كما 6.1.
- **المراجِع:** product-owner · 2026‑10‑22.
- **ما قرأتُه:**
  - من مجلّد التسليم: `status.md` كلّه (الصفوف، و«دورات الإعادة»، و«قرارات وتصعيدات» ومنها أجوبة بوابة ① وقرارات المالك بعد المراجعتين)، و`requirements.md` (A‑9)، و`stories.md`، و`design.md` (§3.2.6 عند CR‑1)، و`test-plan.md` (§1–§7)، و`review.md`، و`security-review.md` (حتّى «إعادة المراجعة بعد الجولة 1»)، و`follow-ups.md`.
  - ومن المستودع: فرق الفرع كلّه، و`docs/deploy-server.md` §5 وقائمة الأمان، و`docs/deploy-runpod.md` §8.4، وصفّ 6.2 ونصّها في الخطّة، وبطاقة ٦٫٢ في `docs/server-gpu-summary.html`.
- **لم أقرأ** `.env` ولا `deploy/nginx/certs/*` ولا ملفّات `deploy/load/`. **ولم ألمس** المكدّس المحلّيّ (متوقّفٌ عمداً)، ولم أشغّل `tests/integration`.

## ما تحقّقتُ منه بنفسي

1. **الشجرة:** HEAD هو `c02c4c9`، و`git status --short` لا يُظهر إلّا `?? docs/delivery/public-file-links/`. ولم أكتب خارج هذا الملفّ.
2. **`master` المحلّيّ متقدّمٌ على `origin/master` بـcommit واحد:** `44769d3` («docs(README): update platform description…»، المالك، 2026‑10‑22 الساعة 08:40 +03، غير مدفوع).
   - فـ`git diff --name-only master..HEAD` يُظهر 14 ملفّاً، منها `README.md`. ولم يلمس `README.md` أيّ commit في الفرع.
   - **وفرق الفرع نفسه** (`git diff --name-only master...HEAD`، من نقطة التفرّع `f1c8bda`) **13 ملفّاً**.
   - وفي لقطةٍ من `master` المحلّيّ (`git archive` في scratch) يفشل اختبارٌ قائم: `tests/unit/test_prebuilt_images_docs.py::test_readme_quick_start_mentions_the_overlay_before_up`، لأنّ `README.md` الجديد لم يعد فيه `cp .env.example .env`. وعلى الفرع ينجح.
   - ليس من 6.2، لكنّه يمسّ بوابة ② (الشرط 3).
3. **الملفّات المحميّة.** من الـ13:
   - المحميّ بالقاعدة 2 في الخطّة (`deploy/`) ثلاثةٌ وحدها، وهي قائمة Q‑6: `deploy/nginx/app-locations.conf`، و`deploy/nginx/nginx.conf`، و`deploy/smoke/stack_smoke.py`.
   - و`nginx.conf` تعليقٌ وحده (`:140-145`)، و`client_max_body_size 2m` كما هو.
   - و`src/` ملفٌّ واحد بموافقة المالك بعد المراجعتين: `src/app/infrastructure/storage/minio_storage.py`، ‏+6/−3. **وكلّ سطرٍ مضافٍ أو محذوفٍ فيه تعليق:** فحصتُه فوجدتُ صفر سطرٍ غير تعليق.
   - والفرق فارغ على: `docker-compose*.yml`، و`.env.example` و`.env.test.example`، و`deploy/release/`، و`deploy/runpod/`، و`deploy/minio/`، و`Dockerfile`، و`.github/`، و`pyproject.toml`، و`.importlinter`، و`migrations/`.
   - والباقي: أربع وثائق (`docs/deploy-server.md`، و`docs/deploy-runpod.md`، و`docs/server-gpu-plan.md`، و`docs/design/08-local-runbook.md`)، وأربعة ملفّات اختبار جديدة، وتعديل `tests/unit/test_edge_capacity.py`.
4. **التعديل في اختبارٍ قائم** (`test_edge_capacity.py`) يقوّيه ولا يرخيه:
   - حارس الدفعة صار يقرأ كتلة `location /` بعينها (`_location_block`)، وأُضيف شرط «دفعة مسار الملفّات = دفعة `/`».
   - وحارس WebSocket يطرح كتلة الملفّات من المجموعة المشتركة، وسببه مكتوب: القائمة مكرّرة عمداً، وفيها `X-Forwarded-Host ""`.
5. **البوّابات على `c02c4c9`:**
   - أعدتُ: `ruff format --check .` ‏837 ملفّاً، و`ruff check .` «All checks passed!»، و`mypy src` ‏467 ملفّاً بلا خطأ، و`lint-imports` ‏8 kept و0 broken.
   - ولم أُعِد `pytest` الكامل ولا `test-rules.sh`. شغّلهما المنسّق على الرأس نفسه: ‏6255 ناجحاً و15 متخطّى (كلّها العُدّة)، والخروج 0 في 329.14 ث، وtest-rules OK. وأعادتهما الجودة: ‏6255 و15 في 321.52 ث (test-plan §7.1).
6. **اختبارات الميزة، أعدتُها على `c02c4c9`:**
   - الملفّات الأربعة المطلوبة (`test_public_file_links_edge.py`، و`test_public_file_links_docs.py`، و`test_stack_smoke_roundtrip.py`، و`test_edge_capacity.py`): **58 ناجحاً في 1.68 ث، ولا متخطّى.**
   - ومعها `test_edge_hardening.py` و`test_install_sh.py` وملفّ العُدّة دون تفعيل: **247 ناجحاً و15 متخطّى** في 19.43 ث. والمتخطّى العُدّة وحدها، وسببها مكتوب: `opt-in container harness: set RUN_EDGE_FILES_TEST=1`.
7. **العُدّة، مرّةً واحدة** (`RUN_EDGE_FILES_TEST=1 pytest tests/unit/test_public_file_links_containers.py`): **15 ناجحاً في 47.37 ث**، ومنها `test_a_copy_source_header_never_reaches_minio`.
   - وDocker قبلها وبعدها متطابقٌ سطراً بسطر: 40 حاوية (لا واحدة تعمل)، و4 شبكات، و40 حجماً، و21 معلّقاً، وصفر حاوية بوسم `aizzak.harness`.
   - لم أحذف شيئاً.
8. **حارس النسخ في `app-locations.conf`:**
   - أربع كتل `if ($http_… != "") { return 403; }` داخل `location ^~ /workspace-files/`، بلا `add_header`: لـ`x-amz-copy-source`، و`x-amz-copy-source-range`، و`x-amz-meta-snowball-auto-extract`، و`x-minio-extract`.
   - حالة العُدّة تجرّب الثلاث الأولى بثلاثة مصادر. و`x-minio-extract` يحرسه الفحص البنيويّ وحده (GAP‑1).
   - وقرأتُ فرق الاختبارات في `4da254e`: القاعدة `_copy_guard_problems` تطلب الكتل الأربع بشكلها، وحالتان سلبيّتان تُفشلانها.
9. **خطوات 7.3:** قرأتُ `docs/deploy-server.md` §5.3 خطوةً خطوة. ولكلٍّ من المعايير الستّة خطوةٌ فيها أمرٌ ونتيجةٌ متوقّعة (الجدول أدناه).
10. **قائمتا الأمان:**
    - ث‑16 وث‑17 في `docs/deploy-server.md`.
    - وث‑14 («**حرجة.** … سُجّل بقرار المالك 2026‑10‑22، لم يُصلح») وث‑15 في `docs/deploy-runpod.md` §8.4.

## المعايير

الرموز:
- ✅ مُثبَتٌ بدليلٍ مسمّى.
- ✅ (المنسّق) قراءةٌ في Cloudflare سجّلها المنسّق في `status.md`.
- 🕒 ينتظر 7.3 على الخادم، بقرار بوابة ① ‏Q‑5 (أ). ولكلٍّ منها خطوةٌ في `docs/deploy-server.md` §5.3. وليس 🔴.
- 🔴 مرفوض.

الاختبارات تحت `tests/unit/`. و«العُدّة» هي `test_public_file_links_containers.py` (حاوياتٌ مؤقّتة)، وأعدتُها كلّها (البند 7). والأولويّة Must إلّا US‑6 (Should).

| AC | الدليل | الحكم |
|---|---|---|
| AC‑1.1 | العُدّة `::test_a_signed_put_through_the_edge_stores_the_same_bytes` | ✅ |
| AC‑1.2 | العُدّة `::test_a_url_signed_for_another_name_is_refused_by_minio` | ✅ |
| AC‑1.3 | العُدّة `::test_the_body_limit_is_exact_at_50_mb` (h2 وhttp1.1) | ✅ |
| AC‑1.4 | `test_public_file_links_edge.py::test_the_body_limit_is_the_app_s_upload_limit_and_under_cloudflare_s`، و`::test_the_body_limit_guard_fails_when_one_number_moves_alone` | ✅ |
| AC‑1.5 | العُدّة `::test_a_50_mb_upload_streams_without_temp_files` (h2 وhttp1.1). وقياس الجودة: `client_temp` صفر، والذروة 83 م.ب من 512 (test-plan §1) | ✅ |
| AC‑1.6 | العُدّة `::test_cors_comes_from_the_edge_once` | ✅ |
| AC‑1.7 | العُدّة `::test_an_expired_upload_url_is_refused` | ✅ |
| AC‑1.8 | `test_public_file_links_edge.py::test_an_arabic_file_name_never_reaches_the_signed_path` | ✅ |
| AC‑1.9 | §5.3 الخطوات 2–5: `upload_url` يبدأ بـ`https://api.aizzak.com/workspace-files/`، و`PUT` ‏200، ثمّ الإتمام، ثمّ `ready` | 🕒 |
| AC‑1.10 | §5.3 الخطوة 8: ملفٌّ بـ52,428,800 بايت بـ200، لا `413` ولا `524`، ومعه الزمن. ثمّ 20 رحلة بـ1 م.ب ووسيطها | 🕒 |
| AC‑2.1 | العُدّة `::test_a_signed_get_returns_the_bytes_type_and_edge_headers` | ✅ |
| AC‑2.2 | §5.3 الخطوة 6: تنزيلان بالبصمة نفسها، ولا `HIT` في `cf-cache-status` | 🕒 |
| AC‑2.3 | §5.3 الخطوة 7: بعد 300 ث ‏403 بجسم XML من MinIO | 🕒 |
| AC‑2.4 | `status.md` «قرارات وتصعيدات» (2026‑10‑22، أداة Cloudflare API، `GET` وحدها، ولا تغيير في المنطقة): لا Origin Rule ولا Transform Rule (404 ‏`10003`). و`url_normalization` ‏`scope=incoming`. وقاعدة التخزين `(http.host eq "api.aizzak.com")` بلا شرط مسار، و`cache: false`. و`fight_mode=false` و`browser_check=on`. ولا Worker، ولا قواعد جدار، ولا ترويسات مُدارة | ✅ (المنسّق؛ انظر «أدلّة ضعيفة» 3) |
| AC‑3.1 | test-plan §2.3: `docker compose config --hash '*'` على `master` والفرع متطابقٌ بايتاً ببايت (34 سطراً، على `6181706`). والفرق على Compose و`.env*.example` و`deploy/release/` فارغ حتّى `c02c4c9` (البند 3) | ✅ (انظر «أدلّة ضعيفة» 2) |
| AC‑3.2 | `git diff -- deploy/runpod/` فارغ (البند 3). و`test_public_file_links_edge.py::test_the_two_files_locations_differ_exactly_where_the_owner_decided` و`::test_the_drift_guard_fails_when_either_side_is_unified`. و`test_edge_capacity.py::test_the_two_edges_have_not_drifted`: يقرأ دفعة `location /`، ويشترط الدفعة نفسها في المسار (البند 4). و`test_edge_hardening.py` أخضر | ✅ |
| AC‑3.3 | `status.md` الصفّ 8: المنسّق، بموافقة المالك «شغّله أنت ثم أوقفه»، على `6181706`. ‏`up -d` ثمّ `restart nginx` ← `healthy`. و`/health` ‏200 على 80 و443، و`/metrics` و`/health/drain` ‏404 على الاثنين. و`stack_smoke.py` من الصورة ومن الفرع: `[3] endpoint=minio:9000  public=localhost:19000`، و`[4] presigned PUT round trip: skipped (not on RunPod)`، و`PASS`، والخروج 0. ثمّ `stop`، فعادت الحاويات الأربعون `exited` كما كانت | ✅ (انظر «أدلّة ضعيفة» 1) |
| AC‑3.4 | العُدّة `::test_the_edge_boots_and_recovers_without_minio` | ✅ |
| AC‑4.1 | `test_stack_smoke_roundtrip.py::test_the_round_trip_runs_on_runpod_or_on_request_and_is_skipped_otherwise`، و`::test_a_skipped_round_trip_prints_today_s_line_and_sends_nothing` (السطر حرفاً بحرف)، و`::test_runpod_and_the_opt_in_both_upload_then_download` | ✅ |
| AC‑4.2 | `git diff -- deploy/release/` فارغ. و`test_stack_smoke_roundtrip.py::test_install_sh_runs_the_smoke_without_the_opt_in`، و`test_install_sh.py` كلّه أخضر (البند 6) | ✅ |
| AC‑4.3 | `test_stack_smoke_roundtrip.py::test_a_clean_round_trip_passes_and_deletes_the_probe`، و`::test_different_bytes_back_fail`، و`::test_a_refused_put_names_its_status` (ومنها 403 و413 و526)، و`::test_a_refused_get_names_its_status`، و`::test_a_put_that_answers_200_without_storing_is_caught`، و`::test_no_line_or_message_carries_the_signature` | ✅ |
| AC‑4.4 | §5.3 الخطوة 1: `docker compose exec -T -e SMOKE_PUBLIC_ROUNDTRIP=1 app python /app/deploy/smoke/stack_smoke.py`. والمتوقّع سطرا `[4] … OK` ثمّ `PASS`، ولا يبقى المسبار | 🕒 (انظر «أدلّة ضعيفة» 5) |
| AC‑5.1 | العُدّة `::test_other_buckets_minio_paths_and_traversal_never_reach_minio` | ✅ |
| AC‑5.2 | العُدّة `::test_only_get_head_and_put_on_an_object_reach_minio`.<br>**وما زاده إصلاح SR‑1 على نصّ المعيار** (رفض ترويسات النسخ والاستخراج بـ403 قبل MinIO): العُدّة `::test_a_copy_source_header_never_reaches_minio`، والقاعدة `_copy_guard_problems` داخل `test_public_file_links_edge.py::test_the_files_route_has_the_decided_shape` و`::test_the_files_route_guard_fails_on_each_regression`. وأكّده المراجع الأمنيّ حيّاً بإحدى عشرة صيغة | ✅ |
| AC‑5.3 | العُدّة `::test_the_host_minio_checks_is_the_client_s_own` | ✅ |
| AC‑5.4 | العُدّة `::test_the_access_log_never_holds_a_signature`. وسجلّ الأخطاء خارج المعيار (ث‑16 وSR‑3) | ✅ |
| AC‑5.5 | `test_public_file_links_edge.py::test_minio_is_published_on_loopback_only`، و`::test_the_loopback_guard_fails_on_a_port_published_everywhere` | ✅ |
| AC‑5.6 | `test_public_file_links_docs.py::test_the_two_new_gaps_are_rows_of_the_one_security_table`. وقرأتُ ث‑16 وث‑17 (البند 10) | ✅ |
| AC‑5.7 | §5.3 الخطوة 9: `nc -zv` على 19000 و19001 يفشلان، و`/aizzak-backups/x` ‏404 بـ`application/problem+json` | 🕒 |
| AC‑6.1 (Should) | `test_public_file_links_docs.py::test_the_guide_has_a_6_2_section_with_its_checks_and_faults`، و`::test_section_3_8_no_longer_lists_6_2_as_not_handled`، و`::test_the_cloudflare_timeouts_are_125_and_30_seconds`، و`::test_each_phrase_check_fails_on_an_edited_copy` | ✅ |
| AC‑6.2 (Should) | `test_public_file_links_docs.py::test_the_two_stale_comments_are_gone`. وقرأتُ التعليق الجديد في `nginx.conf` | ✅ |
| AC‑6.3 (Should) | `test_public_file_links_docs.py::test_the_plan_s_6_2_text_and_rule_1_say_what_was_built`. وقرأتُ الفرق: الطريقة (1) بلا خريطة وبـ`$http_host`، والطريقة (2) «منذ 5.2»، والاستثناء الرابع في القاعدة 1. **والبند الأخير («عند الإغلاق») لـPR الإغلاق** (الشرط 4) | ✅ (البند الأخير بعد الدمج) |
| AC‑6.4 (Should) | `test_public_file_links_edge.py::test_the_route_names_the_bucket_compose_and_env_example_default_to`، و`::test_the_bucket_guard_fails_when_one_name_moves_alone`، وجملة «يكسر كلّ رابطٍ بصمت» في §5.2 | ✅ |

**العدد:** 33 معياراً.
- **27 ✅:**
  - ستّةٌ وعشرون في المستودع: 12 بالعُدّة، و12 وحدة/نصّ، وAC‑3.1 وAC‑3.3 على المكدّس المحلّيّ.
  - وAC‑2.4 في Cloudflare.
- **6 🕒:** AC‑1.9، وAC‑1.10، وAC‑2.2، وAC‑2.3، وAC‑4.4، وAC‑5.7. وكلّها Must.
- **0 🔴.**
- **بالأولويّة:** Must ‏29 (‏23 ✅ و6 🕒)، وShould ‏4 (‏4 ✅).

## تعريف الإنجاز لهذا الـPR (نموذج التشغيل §5)

| البند | الحال | الدليل |
|---|---|---|
| 1. البوّابات خضراء بمخرجٍ فعليّ | ✅ | البند 5 أعلاه، و`status.md` «دورات الإعادة»، وtest-plan §7.1 |
| 2. لكلّ معيارٍ اختبارٌ آليّ أو تبريرٌ مكتوب | ✅ | 24 باختبار. وAC‑3.1 وAC‑3.3 يدويّان على المكدّس المحلّيّ (test-plan §2.3، و`status.md` الصفّ 8). وAC‑2.4 قراءةٌ في Cloudflare. والستّة بخطواتها في §5.3 (test-plan §1) |
| 3. المراجعتان PASS | ✅ | `review.md` على `6181706`: **PASS** (حاجبة 0، ومهمّة 1 هي CR‑1، وتحسين 7).<br>و`security-review.md`: FAIL على `6181706` بسبب SR‑1، ثمّ **PASS** على `c02c4c9`، وSR‑1 مغلقة، ولا ملاحظة جديدة.<br>وما بقي مفتوحاً غير حاجب، وأقبله بالاسم أدناه |
| 4. `acceptance.md` بحكم «مقبول» | ✅ بشرط | الحكم أدناه |
| 5. `status.md` محدّث | 🟡 | يحدّث المنسّق الصفّ 11 بهذا الحكم (الشرط 1) |
| لا ملفّ محميّ خارج Q‑6 وموافقة التعليق | ✅ | البند 3 |
| المؤجَّلات مسجَّلةٌ بمنفّذ | 🟡 | `follow-ups.md` يسمّي المنفّذ لكلّ صفّ. لكنّ المجلّد غير متتبَّع بعد (الشرط 1) |

## أدلّةٌ وجدتُها ضعيفة (لا تقلب حكماً)

1. **AC‑3.3 جرى مرّةً على `6181706`، لا على `c02c4c9`.** والمكدّس متوقّفٌ عمداً، فلم أُعِده.
   - الفرق بين الرأسين في الإعداد أربع كتل `if` داخل موقع الملفّات وحده. فلا يمسّ `/health` ولا `/metrics` ولا `/health/drain`، ولا يتغيّر `stack_smoke.py`.
   - والعُدّة تُقلع الحافّة بالملفّين النهائيّين (AC‑3.4 أخضر على `c02c4c9`).
   - فأقبل الدليل كما هو.
2. **AC‑3.1 قيس على `6181706`.** وCompose و`.env*.example` مطابقةٌ للأساس بايتاً ببايت حتّى `c02c4c9`، فالبصمات متطابقة بالبناء.
3. **AC‑2.4 صورةٌ ليوم 2026‑10‑22**، وقد تتغيّر المنطقة قبل سجلّ DNS.
   - و`browser_check=on` (القيمة الافتراضيّة) قد يتحدّى `PUT` من `curl` أو httpx (R‑3). وهذا يمسّ AC‑1.9 وAC‑4.4، ويُقاس في 7.3 (صفّ `cf-mitigated` في §5.4).
   - فتُعاد القراءة قبل سجلّ DNS، مع PO‑1 في 6.1.
4. **حارس `x-minio-extract` بنيويٌّ وحده**، لا في العُدّة (GAP‑1). والحرّاس الثلاثة الأخرى في العُدّة وفي الفحص البنيويّ معاً.
5. **AC‑4.4، «لا يبقى كائن المسبار»:** §5.3 لا يعطي أمراً يتحقّق منه، ويعتمد على `finally` في `[4]`. وإن فشل الحذف نفسه دُفن سطر `FAIL` (CR‑2). وأقترح علاجه في PO‑1 أدناه.
6. **«الفرق الوحيد» غير دقيقٍ في ثلاثة نصوص** (CR‑1 = BUG‑1): `/workspace-files` بلا شرطة يردّ 301 إلى الجذر ثمّ 404، ولا يصل MinIO.
   - ونصّ AC‑6.3 يطلب وجود الاستثناء الرابع، وهو موجود. فالمعيار صادق، والجملة تُصحَّح مع CR‑1.
7. **لا قياس عبر Cloudflare قبل 7.3** (R‑1). فحدّ 100 م.ب والمهلتان (125 ث و30 ث) مأخوذةٌ من وثائق Cloudflare، والعُدّة تقيس الحافّة وMinIO وحدهما.
8. **مراجعة الشيفرة على `6181706`، ولم تُعَد على فرق الجولة 1** (`4da254e` و`c02c4c9`، +121/−7).
   - راجعه بعدها المراجع الأمنيّ حيّاً، والجودة بإحدى وعشرين طفرة بلا ناجية.
   - وقرأتُ فرق الاختبارات فيه بنفسي (البند 8).
   - فرقٌ صغيرٌ ومحروس، فلا أطلب جولة مراجعةٍ ثانية.

## ملاحظات غير حاجبة مقبولة

**أقبل بالاسم كلّ ما في `follow-ups.md` مؤجَّلاً** (نموذج التشغيل §5، البند 3)، بقرار المالك 2026‑10‑22: «لا تصلح شيء، قم بتوثيق كل شيء ليتم الإصلاح لاحقاً»، وفي RunPod «تسجيل فقط». والمراجعتان PASS:
- **(أ) RunPod:**
  - SR‑1 على RunPod = ث‑14 في `docs/deploy-runpod.md`. **حرجة**، وقائمةٌ منذ `346a743` قبل 6.2، وكشفتها مراجعة 6.2.
  - SR‑4 = ث‑15 (منخفضة).
- **(ب) حافّة Compose:** CR‑1 = BUG‑1 (مهمّة)، وSR‑2، وSR‑3، وSR‑7.
- **(ج) الفحص والاختبارات والوثائق:** CR‑2، وCR‑3، وCR‑4، وCR‑5، وCR‑6، وCR‑7، وCR‑8، وGAP‑1، وملاحظة AC‑5.2. **وملاحظة AC‑5.2 يكفيها هذا الملفّ:** صفّ AC‑5.2 أعلاه يسمّي دليلَي الرفض. فيُغلقها المنسّق بإحالةٍ إليه، أو بسطرٍ في `stories.md`، والعدد يبقى 33.
- **(د) مقبولٌ ومسجَّل:** SR‑5 = ث‑17 (Q‑3 (أ))، وA‑9 (يؤكّده المالك في 7.3).

**هل تجعل إحداها معياراً كاذباً كما كُتب؟ لا.**
- **ث‑14 وث‑15:** في `deploy/runpod/`، وAC‑3.2 يشترط ألّا يُلمس. ولا معيار في 6.2 يقيس حافّة RunPod.
- **CR‑1 مقابل AC‑5.2:** المعيار يطلب `GET /workspace-files/` ‏404 من الحافّة، وهو كذلك. والصيغة بلا شرطة تنتهي إليه ولا تصل MinIO. وعن AC‑6.3 انظر «أدلّة ضعيفة» 6.
- **SR‑2 مقابل AC‑2.1:** المعيار يطلب الترويسات الأمنيّة الثلاث، وهي موجودة. و`Server: MinIO` زيادةٌ لا نقص.
- **SR‑3 مقابل AC‑5.4:** المعيار عن سجلّ الوصول، وهو نظيف. وسجلّ الأخطاء في ث‑16.
- **CR‑2 مقابل AC‑4.3:** حالات المعيار كلّها تحذف المسبار. وفشل الحذف نفسه ليس من حالاته.
- **CR‑3:** طفرةٌ ناجية في سلوكٍ زائدٍ على AC‑4.1 (قيمةٌ خاطئة مع `RUNPOD_POD_ID`). والحالات الثلاث في المعيار محروسة.
- **CR‑4 وCR‑5:** نصّ تلميحٍ، وجملةٌ في جدول الأعطال. وكلمات AC‑6.1 كلّها موجودة.
- **CR‑6 وCR‑7 وCR‑8:** في الاختبارات لا في المنتج. ولم يتسرّب حجمٌ في تشغيلي ولا في تشغيل الجودة.
- **GAP‑1:** الحارس قائمٌ ومحروسٌ بنيويّاً (البند 8).
- **SR‑5 = ث‑17:** هو نصّ AC‑5.6 نفسه.

**ث‑14 عاجلة، وإن كانت خارج هذا الـPR.**
- `CLAUDE.md` يسمح للوكلاء بنشر RunPod دون إنسان.
- وث‑14 تعني أنّ أيّ Pod منشورٍ اليوم يكشف لأيّ مستخدمٍ مصادَق ملفّاتِ كلّ المستأجرين ونسخَ قاعدة البيانات.
- أُبلغ المالك فاختار «تسجيل فقط»، وأحترم قراره. وأوصي بأن يُعرض عليه PO‑2 أدناه، وأن يكون إصلاح ث‑14 أوّل عملٍ تالٍ على الحافّة.

**ملاحظتان منّي، أقترح أن يضيفهما المنسّق إلى `follow-ups.md`** (وهما غير PO‑1 في 6.1):
- **PO‑1 (قبل 7.3، backend-developer):** أمرٌ في §5.3 الخطوة 1 يثبت أنّ `smoke/presign-put-probe.txt` لم يبقَ في الدلو (AC‑4.4). ويُصلح معه CR‑2.
- **PO‑2 (الآن، المنسّق، ويقرّره المالك):** سطرٌ في القسم (أ) من `follow-ups.md`: لا يُنشر Pod منشورٌ ولا يُعاد تشغيله قبل إصلاح ث‑14 إلّا بموافقة المالك.

## الحكم النهائي

**مقبول، بشرط.** يُقبل ما في الفرع على `c02c4c9`. وبوابة ② بعد الشرطين 1 و2، وبعد قرار المالك في الشرط 3.

**السبب:**
- معايير المستودع الستّة والعشرون ✅ بدليلٍ مسمّى، وأعدتُ اختباراتها والعُدّة على `c02c4c9`.
- AC‑2.4 قرأه المنسّق في Cloudflare، وجاء كلّه كالمتوقّع.
- البوّابات الستّ خضراء على `c02c4c9`.
- مراجعة الشيفرة **PASS**، والمراجعة الأمنيّة **PASS** بعد الجولة 1 (SR‑1 مغلقة ومؤكَّدة حيّاً)، والجودة **PASS**.
- الملفّات المحميّة هي قائمة Q‑6 وحدها، ومعها تعليق `src/` بموافقة المالك.
- قرارات بوابة ① كلّها منفّذة، من Q‑1 (ب) إلى Q‑7 (أ)، كما تحقّقت مراجعة الشيفرة.
- **الستّة 🕒 من Must، ومؤجَّلةٌ بقرار بوابة ① ‏Q‑5 (أ)**، ولكلٍّ منها خطوةٌ في §5.3. فلا تُقبل 6.2 قبولاً تامّاً إلّا بعدها في 7.3. ولهذا «بشرط».

**الشروط:**

أ. **قبل فتح الـPR (بوابة ②)، على المنسّق:**
1. **commit توثيقيّ على الفرع** فيه `docs/delivery/public-file-links/` كلّه، كما فعلت 6.1 (`b0f5657`):
   - هذا الملفّ.
   - و`follow-ups.md`، ومعه PO‑1 وPO‑2.
   - و`status.md` بالصفّ 11.
   - ثمّ البوّابات خضراء بعده، والـsha في `status.md`.
2. **الـPR على `origin/master` (`f1c8bda`)، لا على `master` المحلّيّ.** ووصفه يذكر أنّ المراجعتين PASS، ويحيل إلى `follow-ups.md`، ويسمّي ث‑14 صراحةً. وCI أخضر.
3. **`44769d3` (README) على `master` المحلّيّ ليس من 6.2،** ويُفشل `test_prebuilt_images_docs.py::test_readme_quick_start_mentions_the_overlay_before_up`.
   - فإن دُفع إلى `origin/master` قبل الدمج، احمرّ CI هذا الـPR بسببٍ من خارجه.
   - يقرّر المالك: يُصلح README (أو الاختبار) قبل دفعه، أو يُدمج 6.2 أوّلاً.
   - ولا يُعاد بناء الفرع عليه.

ب. **بعد الدمج، في PR الإغلاق** (كما 6.1، `0f34dba`):

4. صفّ 6.2 وبطاقتها:
   - صفّ 6.2 في `docs/server-gpu-plan.md` يصير «🟡 مدموجة ‹التاريخ› (PR #‹N›، `‹sha›`، `docs/delivery/public-file-links/`)، بانتظار 7.3». وتُضاف 6.2 إلى سطر الحالة (السطر 7).
   - وفي `docs/server-gpu-summary.html`، بطاقة ٦٫٢: الوسم من «لم تبدأ» إلى «مدموجة … تنتظر الخادم» (`partial`).
   - **ونصّ «المطلوب» في البطاقة يُعاد:** «الإصلاح مكتوبٌ ومجرَّبٌ في نسخة RunPod، ويُنقل كما هو» لم يعد صحيحاً. فلا خريطة بقرار Q‑1 (ب)، والطرق ثلاث، وفي المسار حارس نسخ، وفي RunPod ث‑14.
   - وهذا يُغلق البند الأخير من AC‑6.3. ثمّ `status.md` والـretro.

ج. **قبل 7.3 وفيها:**

5. **وسم إصدارٍ بعد الدمج** (§5.5): ملفّا nginx في الحزمة، و`stack_smoke.py` في صورة التطبيق. فحزمةٌ أقدم لا تحمل المسار ولا التنزيل في `[4]`.
6. القسمان (ب) و(ج) من `follow-ups.md` قبل 7.3، أو يقرّر المالك تأجيلهما.
7. **A‑9:** يؤكّد المالك أنّ الواجهة لا ترسل في الرفع غير `Content-Type`، ولا ترسل `Authorization` مع الرابط.
8. إعادة قراءة Cloudflare قبل سجلّ DNS («أدلّة ضعيفة» 3، ومعها PO‑1 في 6.1).
9. المعايير الستّة بخطوات §5.3 على الخادم.

**إن فشل شرط:**
- **CI أحمر بسبب الفرع، أو بوّابةٌ حمراء بعد commit التوثيق:**
  - إن كان السبب في الشيفرة أو الاختبارات، يعود العمل إلى **المرحلة 6 (الخلفيّة)**.
  - وإن كان في وثيقة التسليم، يصلحه المنسّق.
- **معيارٌ من الستّة يفشل في 7.3:**
  - `SignatureDoesNotMatch`، أو 502، أو 413 تحت 50 م.ب، أو بقاء المسبار: يعود العمل إلى **المرحلة 6**.
  - تحدّي Cloudflare (`cf-mitigated`)، أو `524` على الرفع الكبير: قرارٌ للمالك (استثناءٌ في Cloudflare، أو حدٌّ أصغر، أو رفعٌ مجزّأ). ثمّ **المرحلة 4 (التصميم)** إن تغيّر المسار.

</div>
