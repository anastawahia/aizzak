<div dir="rtl">

# ملاحظات مؤجَّلة: روابط الملفّات العامّة (`public-file-links`، الخطوة 6.2)

**قرار المالك (2026‑10‑22)، بعد المراجعتين:**
- البنود غير الحاجبة: «لا تصلح شيء، قم بتوثيق كل شيء ليتم الإصلاح لاحقاً».
- RunPod: «تسجيل فقط». سُجّل البندان في قائمة الأمان في `docs/deploy-runpod.md` §8.4، وهما ث‑14 وث‑15.
- يُصلَح في هذا الـPR الحاجب وحده:
  - SR‑1: رفض ترويسة النسخ على الحافّة.
  - تعليق `minio_storage.py:200-203`، بموافقة المالك.

**المراجع:**
- التفصيل والدليل والإصلاح المقترح في `review.md` (`CR‑1`…`CR‑8`) و`security-review.md` (`SR‑1`…`SR‑7`) و`test-plan.md` (`BUG‑1`).
- أرقام الأسطر من `6181706`، وقد تنزاح بعد إصلاح SR‑1.

**على من يبدأ 7.3، أو يمسّ حافّة `/workspace-files/` أو نسخة RunPod، أن يقرأ هذا الملفّ أوّلاً.**

## أ. RunPod (منشورٌ للعموم، وبياناتٌ حقيقيّة مسموحة)

**PO‑2، قاعدة المالك (2026‑10‑22):** لا يُنشر Pod على RunPod ولا يُعاد تشغيله حتّى يُصلح SR‑1 على RunPod (ث‑14)، إلّا بموافقة المالك. وهي تعلو على إذن النشر الآليّ لـRunPod في `CLAUDE.md` («Staging & deploy») إلى أن يُصلح البند.

| # | الشدّة | المنفّذ | المكان | المشكلة | الإصلاح المقترح |
|---|---|---|---|---|---|
| SR‑1 على RunPod (ث‑14 في `deploy-runpod.md`) | **حرجة** | backend-developer، وموافقة المالك على `deploy/runpod/` | `deploy/runpod/nginx.conf:393-417` | موقع `/workspace-files/` بلا تصفية طرق ولا رفضٍ لترويسة النسخ. فسطح S3 للدلو كلّه مفتوحٌ بتوقيعٍ صالح، ومنه `CopyObject` بـ`x-amz-copy-source` غير الموقّعة. **فأيّ مستخدمٍ مصادَق يقرأ ملفّات كلّ مساحات العمل، ونسخ قاعدة البيانات في `aizzak-backups`.** وهو موجودٌ قبل 6.2 (`346a743`)، وكشفته مراجعة 6.2 | حارس ترويسة النسخ نفسه الذي في `deploy/nginx/app-locations.conf`، و`limit_except GET PUT`، و404 لجذر الدلو، واختبار. **قبل أيّ تشغيلٍ لـPod منشور** |
| SR‑4 (ث‑15 في `deploy-runpod.md`) | منخفضة | backend-developer | `deploy/runpod/nginx.conf:393-417` | الموقع يخفي ترويسات CORS الثلاث وحدها، فيمرّ `Strict-Transport-Security: max-age=31536000; includeSubDomains` من MinIO إلى `<POD_ID>-80.proxy.runpod.net`، خلاف تعليقه `:241-249` | `proxy_hide_header Strict-Transport-Security;` |

## ب. حافّة Compose (`deploy/nginx/app-locations.conf`)

| # | الشدّة | المنفّذ | المكان | المشكلة | الإصلاح المقترح |
|---|---|---|---|---|---|
| CR‑1 = BUG‑1 | مهمّة | backend-developer | `app-locations.conf:291`، ومعه `:267` و`design.md:259` و`docs/deploy-server.md:346` و`docs/server-gpu-plan.md:99` | `GET /workspace-files` بلا شرطة يردّ 301 (إعادة توجيه nginx التلقائيّة) لا 404 من التطبيق كما يقول التصميم. فجملة «الفرق المحلّيّ الوحيد» في ثلاثة نصوصٍ غير دقيقة. لا أثر أمنيّ (يقع على 404 الجذر). وأكّده المنسّق على المكدّس المحلّيّ | `location = /workspace-files { return 404; }`، وقاعدةٌ في `files_route_problems` بحالةٍ سلبيّة، وحالةٌ في العُدّة، وتصحيح النصوص الأربعة |
| SR‑2 | منخفضة (CWE‑200) | backend-developer | `app-locations.conf:323-327` | `Server: MinIO` و`x-amz-request-id` و`x-amz-id-2` تمرّ إلى الإنترنت، فتكشف أنّ الخلفيّة MinIO/S3. وجسم XML للخطأ لا يكشف جديداً: اسم الدلو والمفتاح في الرابط أصلاً | `proxy_hide_header` للثلاث، وصفٌّ في قائمة الأمان |
| SR‑3 | منخفضة (CWE‑532) | backend-developer | ث‑16 في `docs/deploy-server.md`، و`deploy/alloy/config.alloy:139`، و`loki.yml:92` | سطور سجلّ الأخطاء التي تحمل `X-Amz-Signature` تصل Loki وتبقى 14 يوماً، وتُقرأ في Grafana، لا ملفّاً على الخادم وحده. ونافذة الإعادة قصيرة (5–15 دقيقة) | جملةٌ في ث‑16، أو `stage.replace` في Alloy يمحو التوقيع |
| SR‑7 | منخفضة/معلومة (CWE‑770) | backend-developer | `app-locations.conf` (موقع الملفّات) | `limit_req` بلا `limit_conn` لكلّ عنوان، كمسار الـAPI العامّ | `limit_conn` اختياريّ لكلّ عنوان |

## ج. فحص `[4]` والاختبارات والوثائق

| # | الشدّة | المنفّذ | المكان | المشكلة | الإصلاح المقترح |
|---|---|---|---|---|---|
| CR‑2 | تحسين | backend-developer | `deploy/smoke/stack_smoke.py:281-282` | إن فشل حذف المسبار في `finally` (MinIO متوقّف، حالة 502 نفسها)، حلّ `AppError` محلّ `SystemExit`، فيُدفن سطر `FAIL … 502` وسط التتبّع | التقاط `AppError` من الحذف وطباعة `WARN`، وترك `SystemExit` الأصليّ يمرّ. واختبار |
| CR‑3 | تحسين | backend-developer | `tests/unit/test_stack_smoke_roundtrip.py:122-127` | لا اختبار لقيمةٍ خاطئة في `SMOKE_PUBLIC_ROUNDTRIP` مع `RUNPOD_POD_ID`. والطفرة نجت | حالة `{"RUNPOD_POD_ID":"x","SMOKE_PUBLIC_ROUNDTRIP":"true"}` |
| CR‑4 | تحسين | backend-developer | `deploy/smoke/stack_smoke.py:211` | تلميح 403 «GET, HEAD and PUT only» مضلّل، فطلبات `[4]` نفسها `PUT` و`GET` | «refused before MinIO, no S3 error body» |
| CR‑5 | تحسين | backend-developer | `docs/deploy-server.md:412` | صفّ `SignatureDoesNotMatch` يقول إنّ `[3]` يطبع `Host` الذي يصل MinIO، وهو يطبع الاسم الذي وُقّع له الرابط (`public=`) | تصحيح الجملة |
| CR‑6 | تحسين | backend-developer | `tests/unit/test_public_file_links_containers.py:199-200` | `docker rm -f` بعد `docker stop` مباشرةً يسابق حذف `--rm` لحجم MinIO المجهول. ولم يظهر تسرّبٌ في قياس الجودة | انتظار اختفاء الحاويات قبل `rm -f` |
| CR‑7 | تحسين | backend-developer | `tests/unit/test_public_file_links_containers.py:739-742` | توكيدٌ ميّت يتضمّنه الذي بعده | التوكيد على `status` غير 2xx في سطور المسجِّل |
| CR‑8 | تحسين | backend-developer | `tests/unit/test_public_file_links_edge.py:445-450` و`:152-157` | حارس اسم الدلو يقرأ كلّ موقع `=` و`^~` في الملفّ، فيفشل برسالةٍ مضلّلة إن أُضيف موقعٌ لا صلة له بالدلو | حصر الحارس في مواقع `/workspace-files` |
| GAP‑1 (الجودة، بعد الجولة 1) | minor | backend-developer | `tests/unit/test_public_file_links_containers.py` (`test_a_copy_source_header_never_reaches_minio`)، و`test_the_files_route_guard_fails_on_each_regression` | حالة العُدّة لا ترسل `x-minio-extract`، فحذف حارسه الرابع لا يُفشلها، ولا يحرسه إلّا الفحص البنيويّ. والاختبار السلبيّ يغطّي متغيّري `copy_source` وحدهما | إضافة `x-minio-extract: true` إلى ترويسات حالة العُدّة، وحالتين سلبيّتين للحارسين الآخرين |
| PO‑1 (مسؤول المنتج، قبل 7.3) | لازم قبل 7.3 | backend-developer | `docs/deploy-server.md` §5.3 الخطوة 1 | AC‑4.4 («لا يبقى كائن المسبار») بلا أمرٍ يتحقّق منه، ويعتمد على `finally` في `[4]`. وإن فشل الحذف دُفن سطر `FAIL` (CR‑2) | أمرٌ في الخطوة 1 يثبت أنّ `smoke/presign-put-probe.txt` لم يبقَ في الدلو، ويُصلح معه CR‑2 |
| AC‑5.2 (ملاحظة الجودة) | وثيقة | المنسّق | `stories.md` (AC‑5.2) | نصّ المعيار يذكر الطرق وجذر الدلو، ولا يذكر رفض ترويسة النسخ (SR‑1) | ملاحظةٌ تحت AC‑5.2 تسمّي دليلَي الرفض. والعدد يبقى 33 |

## د. مقبولٌ ومسجَّل (لا عمل الآن)

- **SR‑5 = ث‑17 (متوسّطة):** نوع الملفّ يختاره الرافع ويُخدم من `api.aizzak.com`. قبله المالك لمرحلة staging (Q‑3 (أ))، وتقييم ث‑17 دقيق. يُراجع في الموجة 8.
- **A‑9 (يؤكّده المالك في 7.3):** الواجهة لا ترسل في الرفع ترويسةً غير `Content-Type`، ولا `Authorization` مع الرابط الموقّع.

</div>
