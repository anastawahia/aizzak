<div dir="rtl">

# خطة الاختبار: روابط الملفّات العامّة (الخطوة 6.2)

- **الفرع:** `feat/public-file-links` على `6181706` (4 commits فوق `master` ‏`f1c8bda`). التاريخ: 2026‑10‑22.
- **لم يُعدَّل شيء في المستودع** (لا اختبارات جديدة ولا commits). كلّ معيار له إثباتٌ قائم.
- **الحكم المختصر:** **PASS للشيفرة والعُدّة والوثائق. ومعيارٌ واحد (AC‑3.3) لم يُنفَّذ لأنّ المكدّس المحلّيّ كان متوقّفاً قبل البدء** (انظر §2.4). لا أخطاء blocking ولا major.

## 1. مصفوفة التغطية (33)

الحصيلة: ✅ **26 − 1 = 25** مثبتة الآن (12 حاوية + 12 وحدة/نصّ + AC‑3.1)، و**1 معلّق** (AC‑3.3)، و**1 Cloudflare** (AC‑2.4 قراءة المنسّق، مرجعها `status.md` «قرارات»)، و**6 🕒 في 7.3** (خطواتها مكتوبةٌ في `docs/deploy-server.md` §5.3، وتحقّقتُ من كلّ خطوة).

| AC | النوع | الاختبار / الدليل | الحالة |
|---|---|---|---|
| AC‑1.1 | حاوية | `test_public_file_links_containers.py::test_a_signed_put_through_the_edge_stores_the_same_bytes` | ✅ |
| AC‑1.2 | حاوية | `…::test_a_url_signed_for_another_name_is_refused_by_minio` | ✅ |
| AC‑1.3 | حاوية | `…::test_the_body_limit_is_exact_at_50_mb[h2, http1.1]` | ✅ |
| AC‑1.4 | وحدة | `test_public_file_links_edge.py::test_the_body_limit_is_the_app_s_upload_limit_and_under_cloudflare_s` + `…guard_fails_when_one_number_moves_alone` | ✅ |
| AC‑1.5 | حاوية | `…::test_a_50_mb_upload_streams_without_temp_files[h2, http1.1]` (31 عيّنة، `client_temp`=0 في الحالتين، الذروة 83 م.ب من 512) | ✅ |
| AC‑1.6 | حاوية | `…::test_cors_comes_from_the_edge_once` | ✅ |
| AC‑1.7 | حاوية | `…::test_an_expired_upload_url_is_refused` | ✅ |
| AC‑1.8 | وحدة | `test_public_file_links_edge.py::test_an_arabic_file_name_never_reaches_the_signed_path` | ✅ |
| AC‑1.9 | 7.3 | `deploy-server.md` §5.3 الخطوات 2‑5 | 🕒 |
| AC‑1.10 | 7.3 | §5.3 الخطوة 8 | 🕒 |
| AC‑2.1 | حاوية | `…::test_a_signed_get_returns_the_bytes_type_and_edge_headers` | ✅ |
| AC‑2.2 | 7.3 | §5.3 الخطوة 6 | 🕒 |
| AC‑2.3 | 7.3 | §5.3 الخطوة 7 | 🕒 |
| AC‑2.4 | Cloudflare | `status.md` «قرارات»: البنود 1‑11 كلّها كالمتوقّع (لا Origin/Transform Rules، `scope=incoming`، قاعدة التجاوز بلا شرط مسار، `fight_mode=false`، `browser_check=on`) | ✅ (المنسّق) |
| AC‑3.1 | المكدّس المحلّيّ | §2.3: `config --hash '*'` متطابق بايتاً ببايت (34 خدمة)، والفرق فارغ للملفّات المحميّة | ✅ |
| AC‑3.2 | وحدة | `test_public_file_links_edge.py::test_the_two_files_locations_differ_exactly_where_the_owner_decided` + `…drift_guard_fails_when_either_side_is_unified`؛ `test_edge_capacity.py::test_the_two_edges_have_not_drifted`؛ `git diff master -- deploy/runpod/` فارغ | ✅ |
| AC‑3.3 | المكدّس المحلّيّ | **لم يُنفَّذ** (§2.4): `app` و`minio` متوقّفتان منذ 3 أيام، فلا `stack_smoke` ولا `/health` | 🔴 معلّق (بيئة، لا عيب) |
| AC‑3.4 | حاوية | `…::test_the_edge_boots_and_recovers_without_minio` | ✅ |
| AC‑4.1 | وحدة | `test_stack_smoke_roundtrip.py::test_the_round_trip_runs_on_runpod_or_on_request_and_is_skipped_otherwise` وثلاثة بعده | ✅ |
| AC‑4.2 | وحدة | `…::test_install_sh_runs_the_smoke_without_the_opt_in` + `test_install_sh.py`؛ `git diff master -- deploy/release/` فارغ | ✅ |
| AC‑4.3 | وحدة | `…::test_a_clean_round_trip_passes_and_deletes_the_probe`، `test_different_bytes_back_fail`، `test_a_refused_put_names_its_status[…]`، `test_a_refused_get_names_its_status`، `test_no_line_or_message_carries_the_signature` | ✅ |
| AC‑4.4 | 7.3 | §5.3 الخطوة 1 | 🕒 |
| AC‑5.1 | حاوية | `…::test_other_buckets_minio_paths_and_traversal_never_reach_minio` | ✅ |
| AC‑5.2 | حاوية | `…::test_only_get_head_and_put_on_an_object_reach_minio` | ✅ |
| AC‑5.3 | حاوية | `…::test_the_host_minio_checks_is_the_client_s_own` | ✅ |
| AC‑5.4 | حاوية | `…::test_the_access_log_never_holds_a_signature`؛ سطر سجلّ الأخطاء بعد 413: **1** سطر فيه `X-Amz-Signature` (يُطبع ولا يُشترط، ث‑16) | ✅ |
| AC‑5.5 | وحدة | `test_public_file_links_edge.py::test_minio_is_published_on_loopback_only` + سلبيّه | ✅ |
| AC‑5.6 | نصّ | `test_public_file_links_docs.py::test_the_two_new_gaps_are_rows_of_the_one_security_table` | ✅ |
| AC‑5.7 | 7.3 | §5.3 الخطوة 9 | 🕒 |
| AC‑6.1 | نصّ | `…::test_the_guide_has_a_6_2_section_…`، `test_section_3_8_…`، `test_the_cloudflare_timeouts_are_125_and_30_seconds` | ✅ |
| AC‑6.2 | نصّ | `…::test_the_two_stale_comments_are_gone` | ✅ |
| AC‑6.3 | نصّ | `…::test_the_plan_s_6_2_text_and_rule_1_say_what_was_built` (وجزء «عند الإغلاق» يبقى لـcommit الإغلاق) | ✅ |
| AC‑6.4 | وحدة+نصّ | `test_public_file_links_edge.py::test_the_route_names_the_bucket_compose_and_env_example_default_to` + سلبيّه + جملة «يكسر» في §5 | ✅ |

## 2. نتائج البوّابات

### 2.1 البوّابات (من `/home/AIZZAK` بـ`.venv/bin` في `PATH`)
```
ruff format --check .   -> 837 files already formatted
ruff check .           -> All checks passed!
mypy src               -> Success: no issues found in 467 source files
lint-imports           -> Contracts: 8 kept, 0 broken.
pytest -rs tests/unit tests/architecture tests/eval
                       -> 6255 passed, 14 skipped, 7 warnings in 189.44s (0:03:09)
deploy/prometheus/test-rules.sh -> SUCCESS: 29 rules found / test-rules: OK
```
- **المتخطَّى 14 وكلّه العُدّة** (`opt-in container harness: set RUN_EDGE_FILES_TEST=1`)، ولا تخطٍّ غيرها (اختبار `test_import_contracts.py` يعمل). لم أضف مرشّحاً.
- لم يُشغَّل `tests/integration` (يمسّ المكدّس الحيّ، ويحتاج موافقة بشريّة).
- **تنبيه تشغيل:** تشغيلٌ مبكّر بـ`-o addopts=""` فشل في اختبارٍ واحد (`has_plugin('anyio')`) بسببي لا بسبب الفرع، لأنّ `addopts` تحوي `-p no:anyio`. أُعيد بالإعداد الافتراضيّ فنجح.

### 2.2 الزمن (NFR‑7)
- `--durations=0`: ما ظهر من اختبارات الملفّات الأربعة الجديدة (فوق 5 م.ث) مجموعه 0.11 ث؛ الباقي تحت 5 م.ث. الأبطأ `test_minio_is_published_on_loopback_only` ‏0.07 ث. **التقدير ≤ 1.2 ث محقَّق، وحدّ NFR‑7 (5 ث) محفوظ.**
- عدد الأسطر: العُدّة تُتخطّى بلا كلفة.

### 2.3 العُدّة، وAC‑3.1، وAC‑4.2
- `RUN_EDGE_FILES_TEST=1 pytest tests/unit/test_public_file_links_containers.py`: **14 passed in 32.92s**. أبطأ حالة 5.99 ث (رفع 50 م.ب بـ10 م.ب/ث). وأعدتُ خمس حالاتٍ منها مع `-s` للقياسات: ترويسات MinIO كما كتبها المطوّر (HSTS بـ`includeSubDomains` وCORS بصدى الأصل و`Allow-Credentials: true`)، وذروة الذاكرة 83 م.ب، و`client_temp`=0.
- **Docker قبل/بعد العُدّة:**

| | قبل | بعد |
|---|---|---|
| `docker ps -a` | 40 | 40 (القائمة متطابقة اسماً باسم) |
| `docker network ls` | 4 | 4 (متطابقة) |
| `docker volume ls` | 40 | 40 (متطابقة) |
| `docker volume ls -qf dangling=true` | 21 | 21 |
| حاويات بوسم `aizzak.harness` | — | 0 |

  لم أحذف أيّ حجم. وبعد الجولات الإضافية (استكشاف + إعادة حالات) بقيت الأعداد نفسها: 0 حاويات موسومة، 40 حجماً، 21 معلّقاً، 4 شبكات.
- **AC‑3.1:** `docker compose config --hash '*'` على الفرع (الملفّ الحيّ) وعلى نسخةٍ من `master` (`git show master:docker-compose.yml` مع `--project-directory /home/AIZZAK`، والبيئة نفسها، دون worktree لأنّ الملفّ نفسه وغير ذي أثرٍ بالمسارات): `cmp` ← **متطابقان، 34 سطراً**. و`docker-compose.yml` متطابق بايتاً ببايت بين `master` و`HEAD`. و`git diff master --stat -- 'docker-compose*.yml' .env.example .env.test.example deploy/release/ deploy/runpod/ Dockerfile .github/ src/ pyproject.toml deploy/minio/` **فارغ**. الملفّات المتغيّرة 12: `deploy/nginx/{app-locations,nginx}.conf` و`deploy/smoke/stack_smoke.py` و4 وثائق و5 اختبارات.
- **AC‑4.2:** `git diff master -- deploy/release/` فارغ، و`test_install_sh.py` أخضر ضمن الـ6255.

### 2.4 AC‑3.3 على المكدّس المحلّيّ: لم يكتمل
- قبل أيّ أمر: كلّ حاويات `aizzak-*` في `docker ps -a` ‏**Exited (0) منذ 3 أيام**، ومنها `aizzak-app-31/32/33` و`aizzak-minio-1` و`aizzak-nginx-1`. (لقطتي الأولى لـ`ps` تسجّل ذلك.)
- نفّذتُ الأمر المأذون به فقط: `docker compose restart nginx`. ولمّا كانت الحاوية متوقّفة **بدأت** (لا إعادة تشغيل)، وبعد نحو 3 دقائق: `Up 3 minutes (unhealthy)`. السبب في سجلّها: `app could not be resolved (3: Host not found)` على `GET /health`، لأنّ لا حاوية `app` على الشبكة.
- `docker compose exec -T nginx nginx -t` ← `syntax is ok` / `test is successful` (إعداد الفرع مقبول).
- **وفق التعليمات توقّفتُ**: لم أشغّل `stack_smoke` ولا أيّ خدمة أخرى. **حال nginx الآن: يعمل وغير صحّيّ، وكان متوقّفاً قبلي.** إن أراد المالك إعادته كما كان: `docker compose stop nginx`. ولإكمال AC‑3.3 يلزم تشغيل `app` و`minio` والتبعيّات (قرار المالك)، ثمّ يُعاد الفحص §5.5.
- ما يغطّي النقص جزئيّاً: العُدّة ترى الحافّة تقلع بالملفّين ويجيب `/health` (AC‑3.4)، و`nginx -t` أخضر على المكدّس المحلّيّ نفسه. وبقي غير مُثبَت: `[3] … public=localhost:19000` و`[4] … skipped` على المكدّس الحيّ، و`/metrics` و`/health/drain` بـ404 على 80 و443. ويغطّي `[4] skipped` نصّاً وحدةُ `test_a_skipped_round_trip_prints_today_s_line_and_sends_nothing`.

## 3. الاختبار الاستكشافي

أُجري في العُدّة نفسها بسكربتٍ مؤقّت في scratchpad (لا يُلتزم). `up` = ترويسة `X-Harness-Upstream` أو ما رآه المسجِّل.

| السيناريو | المتوقّع | الفعلي | الحكم |
|---|---|---|---|
| `GET /workspace-files/` ، `//workspace-files/` ، `/workspace-files/./` ، `/workspace-files/x/../` ، `/workspace-files%2F` ، `/workspace-files/?list-type=2` | 404 من الحافّة، لا يصل MinIO | كلّها 404 `text/html` بلا upstream | ✅ |
| `/workspace-files` بلا شرطة | 404 من التطبيق (design §3.2.6) | **301** من nginx إلى `https://api.aizzak.test/workspace-files/` (ثمّ 404). وبالاستعلام `?policy` كذلك | ⚠️ BUG‑1 (minor) |
| `//workspace-files/<key>` غير موقّع | يصل MinIO ويُرفض | 400 من MinIO (XML)؛ المسجِّل رأى `//workspace-files/…` | ✅ (لا تسرّب) |
| `/workspace-files%2Fk…` غير موقّع | يُرفض | 403 من MinIO | ✅ |
| `/workspace-files/%2e%2e/aizzak-backups/x` (`--path-as-is`) | التطبيق | 404 `up=app` | ✅ |
| خام `/aizzak-backups/../workspace-files/x` و`%2F` | لا 2xx | مرفوض (يقيسه AC‑5.1) | ✅ |
| `DELETE` / `PATCH` / `POST ?uploads` غير موقّعة | 403 من الحافّة | 403 `text/html`، لا upstream | ✅ |
| `TRACE` | يُرفض | 405 من nginx | ✅ |
| أسماء الطرق بحروف صغيرة (`delete` ، `put`) | لا تتجاوز التقييد | **400** من nginx قبل التوجيه، لا upstream | ✅ |
| `OPTIONS` بأصلٍ غير مسموح | 204 بلا `Allow-Origin` | 204 (ترويسات الأصل غير المسموح تغيب، يقيسه AC‑1.6) | ✅ |
| `PUT` بـ`Content-Length: 99999999999` وجسم 1 بايت | 413 فوراً | 413 `text/html`، لا upstream | ✅ |
| `PUT` بـ`Content-Length: 52428801` وجسم صغير | 413 | 413 | ✅ |
| `PUT` مجزّأ (chunked) صغير غير موقّع | يصل MinIO ويُرفض | 403 XML | ✅ |
| `PUT` موقّع لمفتاح عربيّ `t/تقرير-…` | 200 ويُخزَّن | 200، مخزَّن، المسار مرمَّز UTF‑8 | ✅ |
| `PUT` لمفتاحين مرفوضين من MinIO (`t//x` ، 900 حرف) | رفض من MinIO | 400 من MinIO، لا كائن | ✅ (سلوك MinIO) |
| رابط `GET` موقّع يُستعمل بـ`PUT` أو `HEAD` | 403 | 403 | ✅ |
| رابط موقّع لاسمٍ آخر + `X-Forwarded-Host` | 403 | 403 | ✅ |
| رابط موقّع لـ`other.aizzak.test` والعميل يُجبر `Host: other.aizzak.test` | الاسم الذي يرسله العميل هو المعتمد | 200 | ✅ (مقصود Q‑1 (ب): الرابط هو الاعتماد، والحافّة لا تُلزم `server_name`، كما `location /`) |
| ترويسات ردّ 403 من MinIO ومن الحافّة | الأمنيّة مرّةً واحدة، لا `Server: MinIO`، لا CORS | `x-content-type-options` و`referrer-policy` و`x-frame-options` فقط (+ `server: nginx`) | ✅ |
| ترويسات MinIO الأصليّة | تُخفى | HSTS بـ`includeSubDomains` وCORS بصدى الأصل موجودة عند المسجِّل ومخفيّة عند العميل | ✅ |
| 375px وRTL | — | لا واجهة في المستودع | غير منطبق |
| ازدواج الإرسال / عزل مستأجرين | — | لا مسار تطبيق جديد ولا جدول؛ العزل في توقيع التطبيق (خارج 6.2) | غير منطبق |

## 4. الأخطاء المكتشفة

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| BUG‑1 | `GET https://<edge>/workspace-files` (بلا شرطة أخيرة) يردّ **301** من nginx إلى `/workspace-files/` لا **404 من التطبيق** كما يقول `design.md` §3.2.6 ("كما اليوم"). السبب إعادة التوجيه الضمنيّة في nginx لموقعٍ بادئته تنتهي بشرطة مع `proxy_pass`. لا أثر أمنيّ: الوجهة جذر الدلو وتردّ 404، والاستعلام يُحفَظ دون وصول MinIO. لا اختبار يقيس هذه الحالة، ولا وثيقة تقول غير ذلك | minor (انجراف عن التصميم) | في العُدّة: `h.curl("https://api.aizzak.test/workspace-files", "--path-as-is")` ← `301`، `Location: https://api.aizzak.test/workspace-files/`، أو على أيّ حافّة: `curl -sk -i https://127.0.0.1/workspace-files` | مفتوح. الاقتراح: تصحيح سطر الجدول في التصميم، أو إضافة `location = /workspace-files { return 404; }` (قرار المعماريّ). المالك: backend |
| ENV‑1 | (ليس عيباً في الفرع) المكدّس المحلّيّ متوقّف (`app` و`minio` وغيرها Exited منذ 3 أيام)، فلا يمكن إكمال AC‑3.3، ونتيجة `restart nginx` أن صار nginx يعمل **غير صحّيّ** | — | `docker ps -a --format '{{.Names}} {{.Status}}' \| grep aizzak` | مفتوح للمنسّق/المالك |

لا أخطاء blocking ولا major.

## 5. تجارب التطفير (Mutation)

كلّ تجربة: تعديل مؤقّت، ثمّ تشغيل ثمانية ملفّات اختبار (`test_public_file_links_{edge,docs}` و`test_stack_smoke_roundtrip` و`test_edge_{capacity,hardening,cloudflare}` و`test_log_correlation` و`test_install_sh`)، ثمّ `git checkout -- <file>`. **33 تعديلاً، فشل اختبارٌ في كلّ واحد، لا ناجٍ.**

| السلوك | التعديل | الاختبار الذي فشل |
|---|---|---|
| تقييد الطرق | `limit_except GET PUT POST` ؛ حذف الكتلة كلّها | `test_the_files_route_has_the_decided_shape` ، `…guard_fails_on_each_regression` ، `…differ_exactly_where_the_owner_decided` |
| جذر الدلو 404 | `return 200` ؛ `location = /workspace-filez/` | الثلاثة نفسها |
| `Host` | `$http_host` → `$host` | الثلاثة نفسها |
| `X-Forwarded-Host` | يُمرَّر من العميل ؛ حذف السطر | الثلاثة + `test_the_drift_guard_fails_when_either_side_is_unified` |
| إخفاء ترويسات MinIO | حذف `proxy_hide_header` لـHSTS ، nosniff ، `Allow-Origin` ، `Allow-Credentials` (أربع تجارب) | `…has_the_decided_shape` و`…differ_exactly…` |
| `client_max_body_size` | `51m` ؛ `200m` | `test_the_body_limit_is_the_app_s_upload_limit_and_under_cloudflare_s` + سلبيّه |
| البثّ | `proxy_request_buffering on` ؛ `proxy_buffering on` ؛ `proxy_read_timeout 60s` | `…differ_exactly…` (+ الحارسان) |
| `limit_req` | `burst=100` في المسار الجديد | `…differ_exactly…` ، `test_edge_capacity.py::test_the_two_edges_have_not_drifted` |
| `add_header` داخل المسار | إضافة سطر | `…has_the_decided_shape` ، `test_edge_hardening.py::test_no_scope_declares_a_partial_add_header_set` |
| `proxy_pass` بلا `$request_uri` | تعديل | `…has_the_decided_shape` ، `…differ_exactly…` |
| تعليق `nginx.conf` القديم | إعادة «Uploads go DIRECTLY to MinIO» | `test_the_two_stale_comments_are_gone` |
| قيمة التفعيل في `stack_smoke` | قبول `true` | `test_an_opt_in_value_other_than_1_stops_with_a_message[true]` |
| سطر التخطّي | حذف «(not on RunPod)» | `test_a_skipped_round_trip_prints_today_s_line_and_sends_nothing` |
| خطوة التنزيل | حذف مقارنة البايتات | `test_different_bytes_back_fail` |
| حذف المسبار | `storage.delete` → `pass` | `test_a_clean_round_trip_passes_and_deletes_the_probe` وغيره |
| فحص حالة `PUT` | تعطيل | `test_a_refused_put_names_its_status[signature, 413, 526, …]` |
| وثائق | «125 ثانية» → «100 ثانية» ؛ حذف صفّ ث‑17 ؛ حذف «يكسر» ؛ حذف `` `526` `` | `…cloudflare_timeouts_are_125_and_30_seconds` ، `…two_new_gaps_are_rows…` ، `…guide_has_a_6_2_section…` (مرّتان) |
| Compose | نشر منفذ MinIO على كلّ الواجهات ؛ تغيير اسم الدلو الافتراضيّ | `test_minio_is_published_on_loopback_only` ، `test_the_route_names_the_bucket_compose_…` |
| RunPod | "توحيد" بإضافة `limit_except` | `…differ_exactly…` |

محاولةٌ واحدة (`limit_req` أوّل تعبير) لم تُطبَّق لخطأ في نمطي، فأعدتها بنمطٍ صحيح (الصفّ ما قبل الأخير في الجدول). **في النهاية `git status --short` لا يُظهر غير `?? docs/delivery/public-file-links/`.**

## 6. الحكم

**PASS مشروط** — البوّابات الستّ خضراء (6255 ناجحاً، 14 متخطّى هي العُدّة وحدها)، والعُدّة 14/14، و33 تجربة تطفير بلا ناجٍ، وAC‑3.1 متطابق، وخطوات 7.3 الستّ مكتوبة؛ لا خطأ blocking أو major. **الشرط:** AC‑3.3 لم يُنفَّذ لأنّ المكدّس المحلّيّ متوقّف (ENV‑1)، فيلزم إعادة تشغيل `app` و`minio` بقرار المالك ثمّ فحص §5.5، أو قبول المالك بتأجيله إلى 7.3. و BUG‑1 (minor) يُصحَّح في التصميم أو يُقبل.

---

## 7. إعادة الفحص بعد الجولة 1

- **الفرع:** `feat/public-file-links` على `c02c4c9`، فوق `6181706` بـcommitين: `4da254e` (حرّاس ترويسات النسخ في `app-locations.conf`: أربعة `if ($http_… != "") { return 403; }` لـ`x-amz-copy-source` و`x-amz-copy-source-range` و`x-amz-meta-snowball-auto-extract` و`x-minio-extract`، و`_copy_guard_problems`، وحالة عُدّة جديدة) و`c02c4c9` (وثائق وتعليق في `minio_storage.py`). السبب SR‑1 في `security-review.md`. لم أُعدّل شيئاً ولم أُلتزم بشيء، ولم أشغّل المكدّس المحلّيّ.

### 7.1 البوّابات
```
ruff format --check .  -> 837 files already formatted
ruff check .           -> All checks passed!
mypy src               -> Success: no issues found in 467 source files
lint-imports           -> Contracts: 8 kept, 0 broken.
pytest -rs tests/unit tests/architecture tests/eval
                       -> 6255 passed, 15 skipped, 7 warnings in 321.52s (0:05:21)
deploy/prometheus/test-rules.sh -> test-rules: OK
```
- المتخطَّى 15، وكلّها العُدّة (14 السابقة + `test_a_copy_source_header_never_reaches_minio`)؛ عددُ المتخطّى غير العُدّة = 0.
- الزمن الأطول (321 ث مقابل 189 ث) من حِمل الجهاز لا من الاختبارات الجديدة، وهي من أجزاء الألف من الثانية.

### 7.2 العُدّة (`RUN_EDGE_FILES_TEST=1`) وDocker
- **15 passed in 48.33s** (يشمل الحالة الجديدة، وأبطأ حالة رفع 50 م.ب بـ6.8 ث).

| | قبل | بعد |
|---|---|---|
| حاويات `docker ps -a` | 40 | 40 (الأسماء متطابقة) |
| شبكات | 4 | 4 |
| أحجام | 40 | 40 |
| أحجام معلّقة (dangling) | 21 | 21 |
| حاويات بوسم `aizzak.harness` | 0 | 0 |

لم أحذف أيّ حجم. (الفرق الوحيد في `ps` نصّ «Exited … N minutes ago» يتقدّم بالزمن.)

### 7.3 التطفير على الحرّاس الجديدة
اختبارات: `test_public_file_links_edge.py` و`test_edge_hardening.py` و`test_edge_capacity.py`. لكلٍّ من المتغيّرات الأربعة خمس تجارب، فتكون 20، وسادسة، = **21 تجربة، كلّها قتلت اختباراً، ولا ناجٍ**، ثمّ `git checkout`، و`git status` نظيف.

| التجربة | النتيجة (الاختبار الفاشل) |
|---|---|
| حذف كتلة `if` (لكلّ من الأربعة) | `test_the_files_route_has_the_decided_shape` (+ `test_the_files_route_guard_fails_on_each_regression` لمتغيّري `copy_source`) |
| `403` → `404` (لكلّ من الأربعة) | `…has_the_decided_shape` |
| `403` → `200` (لكلّ من الأربعة) | `…has_the_decided_shape` |
| الشكل المجرّد `if ($var)` (لكلّ من الأربعة) | `…has_the_decided_shape` (+ السلبيّ للمتغيّرين الأولين) |
| إعادة تسمية المتغيّر `$http_…_x` (لكلّ من الأربعة) | `…has_the_decided_shape` |
| إضافة `add_header` بعد الحرّاس | `…has_the_decided_shape` ، `…differ_exactly_where_the_owner_decided` ، `test_edge_hardening.py::test_no_scope_declares_a_partial_add_header_set` |

**على مستوى العُدّة:** حذف حرّاس `x-amz-copy-source` ← يفشل `test_a_copy_source_header_never_reaches_minio` (الاختبار يرى نسخةً فعليّة تمرّ). أمّا حذف حرّاس `x-minio-extract` فـ**لا يفشل** في العُدّة، إذ الحالة تجرّب ثلاث ترويسات فقط (`x-amz-copy-source` و`-range` و`x-amz-meta-snowball-auto-extract`) ولا تجرّب `x-minio-extract`؛ يمسكه الفحص البنيويّ وحده.

### 7.4 تعيين المعايير: لا تراجع
- **33 معياراً كما كانت**: 25 ✅ (12 حاوية + 12 وحدة/نصّ + AC‑3.1)، و AC‑3.3 ✅ بحسب `status.md` صفّ 8 (نُفِّذ على المكدّس الحيّ، فلم أُعدْه)، و AC‑2.4 (Cloudflare)، و6 🕒 في 7.3. أعاد الفحص نفسه ما أعدتُه فحصاً: كلّ ما في الجدول §1 أخضر دون تغيير.
- **ملاحظة تعيين لـAC‑5.2:** نصّه يخصّ الطرق (`GET`/`HEAD`/`PUT`) وجذر الدلو. حرّاس النسخ **توسّع** ما يرفضه المسار إلى «`PUT` موقّعٌ بترويسة نسخٍ أو استخراج» وهو ليس في نصّ المعيار، فيُستحسن إضافة ملاحظة لـAC‑5.2 (أو معيار AC‑5.8) تقول إنّ `test_a_copy_source_header_never_reaches_minio` ، و`_copy_guard_problems` ضمن `test_the_files_route_has_the_decided_shape` ، هما الإثبات. لا تغيير في العدّ.
- **src/:** تغيّر `minio_storage.py` بتعليقٍ وحده (9 أسطر: +6/−3 كلّها `#`)، و mypy/lint‑imports/pytest خضراء. والفرق مقابل `master` لا يمسّ `docker-compose*.yml` ولا `deploy/runpod/` ولا `deploy/release/` ولا `Dockerfile` ولا `.github/` ولا `.env*` (الإخراج فارغ).

### 7.5 الأخطاء الجديدة
| # | الوصف | الشدة | الحالة |
|---|---|---|---|
| GAP‑1 | حالة العُدّة لا تجرّب `x-minio-extract` (الحرّاس الرابعة)؛ تُمسك بنيويّاً فقط. الحالة السلبيّة `test_the_files_route_guard_fails_on_each_regression` تغطّي أيضاً متغيّري `copy_source` وحدهما | minor (فجوة اختبار، لا عيب منتج) | مفتوح، أضِف `x-minio-extract: true` إلى قائمة الترويسات في الحالة (backend) |
| BUG‑1 | (من الجولة الأولى) `/workspace-files` بلا شرطة ← 301 | minor | لم يتغيّر، لم يُعالَج في الجولة 1 |

لا أخطاء blocking أو major جديدة.

### 7.6 الحكم بعد الجولة 1
**PASS** — البوّابات خضراء (6255 ناجحاً، 15 متخطّى كلّها العُدّة)، والعُدّة 15/15 دون تسرّب في Docker، و21 تجربة تطفير على الحرّاس الجديدة بلا ناجٍ، ولا تراجع في أيّ معيار، و AC‑3.3 ناجح بحسب `status.md`. بقيت ملاحظتان minor (GAP‑1 وBUG‑1).

</div>
