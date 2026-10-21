<div dir="rtl">

# خطة الاختبار: شهادةٌ حقيقيّة عبر Cloudflare (الخطوة 6.1)

الفرع `feat/cloudflare-tls` على `9b3e3f7`. لم أضف أيّ commit: التغطية كافية، وكلّ اختبارٍ يفشل فعلاً عند كسر السلوك (انظر §3، الطفرات). الأعداد تطابق stories.md: 20 مستودع + 3 منسّق + 15 في 7.3 + 1 غير لازم = 39.

## 1. مصفوفة التغطية
| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC-1.1 | وحدة | `tests/unit/test_edge_cloudflare.py::test_cloudflare_s_header_is_the_only_real_ip_source_and_sits_in_http`، `::test_every_trusted_range_is_a_strict_cidr_and_both_families_are_there`، `::test_no_trusted_range_is_private_loopback_docker_or_too_wide`، `::test_the_list_names_its_source_and_the_day_it_was_copied`، `::test_the_real_ip_guard_fails_on_each_shape_it_exists_for` | ✅ |
| AC-1.2 | منسّق (Cloudflare) | acceptance.md، يوم الدمج | منسّق |
| AC-1.3 | وحدة + مراجعة | `git diff master...HEAD -- 'docker-compose*.yml' deploy/runpod` فارغ (0 ملف) وحارسا `test_edge_hardening.py` تنجح | ✅ |
| AC-1.4 | حاوية | §3 (فحص nginx:1.27، `--network none`) | ✅ |
| AC-1.5 / 1.6 | 7.3 | — | 7.3 |
| AC-2.1 | نصّ | `test_cloudflare_tls_docs.py::test_the_certificate_steps_are_in_order`، `::test_each_phrase_check_fails_on_an_edited_copy` | ✅ |
| AC-2.2 | وحدة | `test_gen_cert.py::test_both_files_present_are_left_alone_and_openssl_is_never_called` | ✅ |
| AC-2.3 | وحدة | `test_gen_cert.py::test_one_file_alone_stops_naming_its_partner_and_writes_nothing` (+ `test_a_dangling_symlink_counts_as_present`) | ✅ |
| AC-2.4 / 2.5 / 2.7 | 7.3 | — | 7.3 |
| AC-2.6 | وحدة | `test_gen_cert.py::test_no_tls_material_is_tracked_and_the_directory_stays_ignored` | ✅ |
| AC-3.1 / 3.7 | منسّق (Cloudflare) | acceptance.md | منسّق |
| AC-3.2 | غير لازم (Q-3 أ) | — | غير لازم |
| AC-3.3 / 3.4 / 3.5 / 3.6 / 3.8 | 7.3 | — | 7.3 |
| AC-4.1 | وحدة | `test_edge_cloudflare.py::test_hsts_names_the_api_host_alone_without_subdomains_or_preload`، `::test_the_hsts_guard_fails_on_a_second_host_includesubdomains_or_preload` | ✅ |
| AC-4.2 | حاوية | §3 (HSTS لكلّ Host) | ✅ |
| AC-4.3 | 7.3 | — | 7.3 |
| AC-4.4 | نصّ | `test_cloudflare_tls_docs.py::test_hsts_timing_and_rollback_are_said_in_the_guide_and_the_plan` | ✅ |
| AC-5.1 | وحدة | `test_install_sh_tls.py::test_a_full_run_with_the_development_certificate_reports_it_and_exits_0` | ✅ |
| AC-5.2 | وحدة | `test_install_sh_tls.py::test_a_certificate_for_the_domain_names_issuer_and_expiry`، `::test_a_certificate_for_another_name_warns_and_names_the_domain`، `::test_an_expired_certificate_is_a_warning`، `::test_the_origin_ca_issuer_gets_the_proxied_hint` | ✅ |
| AC-5.3 | وحدة | `test_install_sh_tls.py::test_openssl_never_sees_the_key_and_the_marker_never_reaches_the_output` | ✅ |
| AC-5.4 | وحدة | `test_install_sh.py`، `test_install_sh_config.py`، `test_install_bundle_docs.py` (القائمة، تنجح) | ✅ |
| AC-6.1 | نصّ | `test_cloudflare_tls_docs.py::test_the_plan_row_step_and_decision_4_say_cloudflare` | ✅ |
| AC-6.2 | نصّ | `::test_the_guide_section_has_the_zone_table_limits_and_error_codes` | ✅ |
| AC-6.3 | نصّ | `::test_the_three_new_gaps_are_rows_of_the_one_security_table` | ✅ |
| AC-6.4 | نصّ | `::test_no_reference_to_the_missing_linux_server_guide_remains` | ✅ |
| AC-6.5 | نصّ | `::test_the_two_binding_documents_name_the_origin_certificate_and_its_key` | ✅ |
| AC-7.1 / 7.2 / 7.4 / 7.5 | 7.3 | — | 7.3 |
| AC-7.3 | نصّ | `::test_the_524_limit_and_the_sse_advice_are_written` | ✅ |

الإجمالي: 20 ✅ في المستودع، 3 منسّق، 15 في 7.3، 1 غير لازم. لا AC من الـ20 بلا تغطية.

## 2. نتائج بوابات المشروع (على `9b3e3f7`)
```
1 ruff format --check .   -> 833 files already formatted
2 ruff check .            -> All checks passed!
3 mypy src                -> Success: no issues found in 467 source files
4 lint-imports            -> Contracts: 8 kept, 0 broken.
5 pytest -rs tests/unit tests/architecture tests/eval
                          -> 6168 passed, 7 warnings in 189.56s (0:03:09)   (لا تخطٍّ، ولا تكامل: المكدّس الحيّ مشترك)
6 deploy/prometheus/test-rules.sh -> SUCCESS: 29 rules found / test-rules: OK
```
`git status` بعد كلّ شيء: لا يظهر إلا `docs/delivery/cloudflare-tls/` غير متعقَّب.

**NFR-6:** الملفّات الأربعة الجديدة (35 اختباراً): 3.01 ث إجمالاً (أبطؤها 1.50 ث: التشغيل الكامل لـinstall.sh). الحدّ 5 ث، ✅.

**NFR-1 (المكدّس المحلّيّ لم يتغيّر):** لا ملف `docker-compose*.yml` ولا `deploy/runpod` في الفرق. وبـ`docker compose --env-file <قيم وهميّة> config --hash '*'` من المسار نفسه (master ثمّ الفرع، فالمسار جزء من البصمة): 22 خدمة، **البصمات متطابقة**، وكذلك مع `docker-compose.gpu.yml`. ✅

## 3. الاختبار الاستكشافي
| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| nginx -t للفرع وmaster | حاوية `nginx:1.27` مؤقّتة، `--network none` | ناجح | ناجح للاثنين | ✅ |
| انتحال `CF-Connecting-IP` و`X-Forwarded-For` من نظير غير Cloudflare (AC-1.4) | طلب من 127.0.0.1 بالرأسين | العنوان الحقيقيّ في السجلّ وفي ما يُرسَل للتطبيق | `remote_addr=127.0.0.1`، والتطبيق رأى `xri=127.0.0.1` | ✅ |
| نظير موثوق (نسخة اختبار فيها 127.0.0.1 موثوق) | قيم: IPv4، IPv6 `2001:db8::1`، `garbage`، فارغ، قيمتان، XFF وحده | IPv4/IPv6 صالحة تُقبل، والتالف يُتجاهل، وXFF لا يُقرأ | كما هو متوقّع؛ القيمة الفاسدة والفارغة تُهمَل؛ XFF وحده لا يغيّر شيئاً. (ملاحظة: مع قيمتين يؤخذ الأخير، ولا يحدث من Cloudflare) | ✅ |
| HSTS لكلّ Host (AC-4.2) على :80 و:443 | `localhost`، `aizzak.com`، `api.aizzak.com`، `API.AIZZAK.COM`، `api.aizzak.com:443`، `api.aizzak.com.` | غياب الترويسة عدا api.aizzak.com بـ`max-age=31536000` مرّة واحدة | `localhost` و`aizzak.com`: 0. الأربعة الباقية: ترويسة واحدة بالقيمة الصحيحة | ✅ |
| نظير Cloudflare بعنوان IPv6 حقيقيّ | لا يمكن محاكاته بلا شبكة | — | تغطّيه الاختبارات الوحدويّة (نطاقات v6 حاضرة، حذفها يُفشل الاختبار) وقراءة IPv6 من الرأس أُثبتت أعلاه؛ والتحقّق الفعليّ في 7.3 | 7.3 |
| gen-cert: لا شيء / crt وحده / key وحده / الاثنان / مجلّد للقراءة فقط | `AIZZAK_CERT_DIR` على مجلّدات مؤقّتة | توليد 0600/0644؛ خطأ 1 بلا كتابة؛ لا مساس؛ فشل بلا ملفّات | كما هو متوقّع في الأربع الأولى؛ للقراءة فقط: خروج 1 بلا أيّ ملف متبقٍّ، لكن **بلا رسالة سبب** (BUG-1) | ✅ مع ملاحظة |
| أوامر §4.2 (مفتاح EC بـumask 077، SAN، بصمتا المفتاح العامّ، -checkend) | نفّذتها بمفتاح وشهادة تجريبيّين | المفتاح 0600، البصمتان متساويتان | 600، والبصمتان متطابقتان، `-checkend` يخرج 0 | ✅ |
| سطر TLS في install.sh: بلا openssl / منتهية / ذاتيّة / اسم آخر / ملف مفقود / رابط رمزيّ / غير قابلة للقراءة | الاختبارات الوحدويّة بأدوات مزيّفة | تحذير أو معلومة، الخروج لا يتغيّر، ولا مادّة مفتاح | كلّها مغطّاة وناجحة، وعلامة المفتاح المزروعة لا تظهر في المخرج ولا تُمرَّر إلى openssl | ✅ |
| ترتيب `-checkend` بعد `reload` في §4.2 (الانحراف المعلن) | قراءة الخطوات 4 إلى 6 والتنفيذ | آمن وصحيح | آمن: الخطوة 4 تطبع `-enddate` قبل التفعيل ويراها المشغّل، وإن فشل `-checkend` بعد التفعيل فالرجوع §4.4 قائم ولا حركة قبل 7.3. الأفضل نقله إلى الخطوة 4، لكنه ليس خطأً | ✅ (BUG-2 ملاحظة) |

**اختبارات الطفرات (تغيير ملف ملتزَم، ثمّ استرجاعه بـ`git checkout`؛ `git status` نظيف بعدها):**
- `nginx.conf` (10 طفرات): حذف نطاق، نطاق Docker `172.16.0.0/12`، `127.0.0.1`، `real_ip_recursive on`، الرأس `X-Forwarded-For`، حذف نطاقات IPv6، حذف `real_ip_header`، `includeSubDomains`، HSTS في `default`، نمط عامّ لـHSTS. كلّها تُفشل اختباراً.
- `gen-cert.sh`: إعادة التوليد عند ملفّ واحد، الكتابة فوق الزوج، `chmod 644` للمفتاح، تجاهل الرابط المعلّق: تُفشل. **ناجيتان:** استبدال `mv` بـ`cp` وحذف `umask 077`، ونتيجتهما النهائيّة مطابقة (ذرّيّة النقل ونافذة الأذونات لا تُقاسان من الخارج)؛ والحالة الأهمّ (فشل التوليد لا يترك ملفّاً) مغطّاة. مقبول.
- `install.sh` (7 طفرات): حذف سطر TLS، الرابط الرمزيّ، الانتهاء، تطابق الاسم، الذاتيّة، قراءة المفتاح، تلميح Origin CA: كلّها تُفشل.
- الوثائق (10 طفرات): 524، `max-age=0`، `-checkend`، «أضعف»، ترتيب reload، «Origin CA» في الخطّة وفي 08، `server.key` في 05، ث‑15، `deploy-linux-server`: كلّها تُفشل.

## 4. الأخطاء المكتشفة
| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| BUG-1 | `gen-cert.sh` يخرج بـ1 بصمتٍ حين لا يُكتب في المجلّد (للقراءة فقط): `openssl ... >/dev/null 2>&1` يبتلع السبب، فلا يعرف المشغّل لماذا فشلت `nginx-certs`. سلوكٌ قائم قبل الفرع (لم يُدخله) | minor | `mkdir d; chmod 555 d; AIZZAK_CERT_DIR=$PWD/d sh deploy/nginx/gen-cert.sh; echo $?` المتوقّع: رسالة تسمّي المجلّد؛ الفعليّ: سطر «generating» ثمّ خروج 1. الموضع: `deploy/nginx/gen-cert.sh` (استدعاء `openssl req` قرابة السطر 60) | مفتوح، لا يمنع |
| BUG-2 | `docs/deploy-server.md` §4.2: `-checkend 31536000` (خطوة 6) بعد `reload` (خطوة 5). آمنٌ كما شُرح، لكنّ الفحص قبل التفعيل أوضح للمشغّل | minor (ملاحظة) | قراءة §4.2 الخطوات 4 إلى 6. المقترح: نقله إلى نهاية الخطوة 4، دون كسر `test_the_certificate_steps_are_in_order` إلا بتعديل الترتيب فيه وفي AC-2.1 | مفتوح، لا يمنع |

لا أخطاء blocking ولا major.

## 5. الحكم
**PASS** — البوابات الستّ خضراء، وكلّ AC من الـ20 في المستودع له اختبارٌ يفشل عند كسر السلوك، وفحوص الحاوية وNFR-1 (بصمات Compose متطابقة) وNFR-6 (3.01 ث) سليمة؛ المتبقّي: 3 للمنسّق و15 في 7.3 وAC-3.2 غير لازم، وخطآن minor لا يمنعان.

## 6. إعادة الفحص بعد جولة العمل الأولى (الرأس `8f296ba`)

**البوّابات:** أعاد المنسّق تشغيل الستّ على `8f296ba` وكلّها خضراء (pytest 6172، بلا تخطٍّ). أنا شغّلت الملفّات الأربعة المعنيّة: 39 ناجحاً، ومعمارية `lint-imports` لم تتخطَّ (المسار أولاً في PATH). `git status` في النهاية: غير المتعقَّب `docs/delivery/cloudflare-tls/` وحده.

**الطفرات على الفرق (كلّ طفرة بـ`sed` ثمّ `git checkout --` للملف):**
| AC | الطفرة | النتيجة |
|---|---|---|
| gen-cert (SR‑5) | العودة إلى اسم ثابت `.server.key.new` بدل `mktemp` | 3 اختبارات تفشل ✅ |
| gen-cert | نشر المفتاح بـ`mv -f` بدل `ln` | يفشل اختبار ✅ |
| gen-cert | نشر الشهادة بـ`mv -f` بدل `ln` | يفشل اختبار ✅ |
| gen-cert | حذف سحب المفتاح المنشور عند فشل الشهادة (لا نصف زوج) | يفشل اختبار ✅ |
| gen-cert | حذف `trap cleanup EXIT` | 4 اختبارات تفشل ✅ |
| gen-cert (BUG‑1) | تغيير نصّ رسالة المجلّد للقراءة فقط | يفشل `test_a_read_only_directory_says_so…` ✅ |
| install (المراجعة #3) | `"Hostname $DOMAIN does match certificate"` ← `"does match certificate"` | يفشل اختبار «اسم المُصدِر يقول does match» ✅ |
| AC‑2.1 / BUG‑2 | `-checkend` ← `-enddate` في الخطوة 4 | 2 تفشلان ✅ |
| AC‑2.1 / المراجعة #4 | `server.crt.prev` ← اسم آخر في سلسلة `cp` | **ينجو** (9 ناجحة) 🔴 |
| المراجعة #4 | حذف عبارة «`*.prev` قبل» من نصّ الخطوة 5 | **ينجو** 🔴 |
| AC‑6.4 | `--exclude-dir=certs` | لا يوجد في `docs/` أصلاً (هو في نصّ المعيار في `stories.md` فقط) فلا طفرة؛ المعيار نفسه مضبوط ✅ |

**الثغرة GAP‑1 (minor، لا تمنع):** لا اختبار يحرس نسخ `*.prev` في §4.2 الخطوة 5 ولا جملة الاستعادة منها. تُحذف السطران `cp -p … .prev` أو تُغيَّر أسماؤهما فتبقى الاختبارات خضراء. أُضيف اختبارٌ يتحقّق أنّ الخطوة 5 تحوي `cp -p` لكلٍّ من `server.crt` و`server.key` إلى `.prev` قبل أوّل `mv`، وأنّ جملة الاستعادة تذكر `mv -f …server.crt.prev`. (لم أضفه: ممنوع الالتزام في هذه الجولة.) المالك: الاختبارات (backend).

**gen-cert في حاوية `nginx:1.27` مؤقّتة `--network none` (السكربت يُربط للقراءة فقط؛ الفحص بعد كلّ تشغيل لقائمة المجلّد بـ`ls -A`):**
| الحالة | الناتج | الحكم |
|---|---|---|
| مجلّد جديد | خروج 0؛ `server.crt` 644 و`server.key` 600؛ لا ملفّات مؤقّتة | ✅ |
| تشغيل ثانٍ | «already present, leaving it alone»، خروج 0، لا تغيير | ✅ |
| مجلّد `:ro` | «ERROR: cannot create files in /certs (is deploy/nginx/certs read-only or not writable?); nothing was written»، خروج 1، المجلّد فارغ | ✅ (BUG‑1 أُصلح) |
| رابط رمزيّ مزروع على `.server.key.new` يشير إلى ملف غير موجود | خروج 0، الرابط لم يُتبع (الهدف لم يُنشأ)، والرابط نفسه بقي كما هو، لا ملفّ مؤقّت عشوائيّ | ✅ |
| `server.key` وحده من المشغّل | خروج 1 باسم الناقص، المحتوى لم يُلمس، لا كتابة | ✅ |
| زوج وضعه المشغّل | يُترك كما هو، خروج 0، لا ملفّات إضافيّة | ✅ |

**قراءة §4.2 و§4.3 كما يفعل المشغّل (نسخٌ بالترتيب في صدفة واحدة):**
- الخطوة 4: `( cd deploy/nginx/certs && … )` المسار نسبيّ من جذر المستودع، والصدفة الفرعيّة تحفظ مجلّد المشغّل. السلسلة بـ`&&` فيقف `-checkend` الفاشل (خروج 1) قبل البصمتين، و`-checkend` يسبق التفعيل (الخطوة 5). ✅
- الخطوة 5: `cp -p` للزوج العاملة إلى `*.prev` ثمّ `mv` ثمّ `nginx -t && reload` في سلسلة واحدة، فلا `reload` بعد `cp` أو `mv` فاشل. الاستعادة تذكر `mv -f server.crt.prev server.crt` «ومثله للمفتاح» وهي كافية. ملاحظة: أوّل تفعيل يجد `server.crt` ذاتيّ التوقيع من `nginx-certs` فينجح `cp`؛ ولو غاب (خادم بلا `nginx-certs`) لتوقّفت السلسلة قبل أيّ `mv`، وهذا الاتّجاه آمن.
- §4.3 الخطوة 3 تحيل إلى الخطوتين 4 و5، وخطوة 5 فيها تحيل إلى معرّف الشهادة المسجَّل في §4.2 الخطوة 3. ✅
- الصفوف الجديدة في جدول TLS (رابط رمزيّ، غير مقروءة) متّسقة مع `install.sh`؛ وصفّ 522 يذكر 443 وحدها، متّسق مع ث‑14.

**حالة الأخطاء:**
| # | الحالة |
|---|---|
| BUG‑1 | **أُصلح**: رسالة تسمّي المجلّد، وخروج 1، ولا ملفّ متبقٍّ (حاوية + اختبار يفشل بالطفرة) |
| BUG‑2 | **أُصلح**: `-checkend` في الخطوة 4 قبل التفعيل، والترتيب في `_ORDER` وAC‑2.1 محدَّثان (طفرة تُفشل اختبارين) |
| GAP‑1 | جديد، minor، لا يمنع: لا اختبار لنسخ `*.prev` ولا لجملة الاستعادة (أعلاه) |

لا أخطاء جديدة في السلوك، ولا blocking ولا major.

**الحكم بعد إعادة الفحص: PASS** — الإصلاحان BUG‑1/BUG‑2 ثابتان بطفرات وحاويات، والتغييرات الأخرى (install.sh، SR‑5، نصوص الوثيقة) محروسة باختبارات تفشل عند كسرها؛ المتبقّي ثغرة اختبار واحدة minor (GAP‑1).

## 7. إعادة الفحص بعد الجولة الثانية (الرأس `9d838cd`)

النطاق: `git diff 8f296ba..9d838cd` (ثلاث إيداعات). بوّابات المنسّق الستّ خضراء (pytest: 6183 نجح، لا تخطّي). الطفرات على `tests/unit/test_gen_cert.py` و`test_install_sh_tls.py` و`test_cloudflare_tls_docs.py`، وكلّ طفرة رُدّت بـ`git checkout --` والشجرة نظيفة بعدها.

| الطفرة | النتيجة |
|---|---|
| `ln -T` إلى `ln` للمفتاح | يفشل `test_a_directory_that_appears_as_the_target_is_refused_not_filled[server.key-dir]` |
| `ln -T` إلى `ln` للشهادة | يفشل نفس الاختبار `[server.crt-dir]` |
| حذف `trap 'exit 1' HUP INT TERM` | يفشل `test_a_termination_signal_removes_the_temporary_files` |
| الفخّ بلا `TERM` | يفشل الاختبار نفسه |
| الرسالة دائماً «appeared» | يفشل `test_a_failing_ln_is_not_blamed_on_a_file_that_appeared` |
| الرسالة دائماً «could not publish» | يفشل اختبار الدليل `[server.key-dir]` |
| حذف `rm -f "$KEY"` عند فشل الشهادة | يفشل `test_a_file_that_appears_while_generating_is_never_overwritten[server.crt]` |
| `-checkhost` عائد إلى النداء المشترك (نسخة `8f296ba`) | يفشل `test_an_issuer_name_carrying_the_phrase_does_not_vouch_for_the_name[full-phrase-cn]` |
| `-checkhost` بمطابقة جزء من النصّ | **تنجو**: طفرة مكافئة، لأنّ النداء المنفرد لا يطبع إلا سطر `-checkhost` (وعند عدم المطابقة يطبع «does NOT match»). الحماية الفعليّة هي فصل النداء، وهي محروسة بالطفرة السابقة |
| `-checkhost` بشرط «غير فارغ» فقط | يفشل `test_a_certificate_for_another_name_warns_and_names_the_domain` |
| الاستعادة بلا نقل `server.crt` إلى `origin.crt` | يفشل `test_activation_keeps_the_working_pair_and_restores_it_without_…` |
| الاستعادة بلا نقل `server.key` إلى `origin.key` | يفشل الاختبار نفسه |
| الاستعادة بلا إرجاع `server.crt.prev` | يفشل الاختبار نفسه |
| الاستعادة بلا إرجاع `server.key.prev` | يفشل الاختبار نفسه |
| حذف سطر `cp -p` للمفتاح | يفشل الاختبار نفسه |
| حذف `rm -f *.prev` (§4.3 الخطوة 7، ثمّ الاثنتان معاً) | يفشل `test_the_previous_pair_is_deleted_after_the_check_and_after_the_revocation` |
| `rm -f` لـ`server.crt.prev` وحده | يفشل الاختبار نفسه |

**الحاوية** (`nginx:1.27`، `--network none`، `openssl` مغلَّف لإحداث الحالة):

| الحالة | النتيجة |
|---|---|
| ظهور `server.key` كدليل أثناء التوليد | رُفض بـ`server.key appeared while ... nothing was written`، الخروج 1، الدليل فارغ، لا ملفّات مؤقّتة |
| `SIGTERM` أثناء التوليد (وُجد ملفّان مؤقّتان قبله) | الخروج 1، وبعده 0 ملفّات متبقّية في المجلّد |

**GAP‑1: مُغلقة.** اختبار الوثيقة صار يفشل عند حذف نسخ `*.prev` أو إرجاعها أو حذفها.

أخطاء جديدة: لا شيء blocking أو major. متابعة minor: لا شيء يستحقّ التسجيل سوى أنّ طفرة المطابقة الجزئيّة لـ`-checkhost` مكافئة ولا يمكن قتلها باختبار.

**الحكم: PASS** على `9d838cd`.

</div>
