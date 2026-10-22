<div dir="rtl">

# التصميم التقني: روابط الملفّات العامّة (الخطوة 6.2 من خطّة خادم الكرت)

> **المدخلات:** [`requirements.md`](requirements.md) و[`stories.md`](stories.md) بعد بوابة ① (2026‑10‑22)، و[`status.md`](status.md) «قرارات وتصعيدات». النطاق US‑1…US‑6 (33 معياراً) بالخيارات المختارة: Q‑1 (ب)، وQ‑2 (ب)، وQ‑3 (أ)، وQ‑4 (أ)، وQ‑5 (أ)، وQ‑6، وQ‑7 (أ).
>
> **ما قُرئ للتصميم (2026‑10‑22، قراءةٌ فقط):**
> - `deploy/nginx/nginx.conf` و`app-locations.conf`، و`deploy/runpod/nginx.conf:120-436`، و`deploy/smoke/stack_smoke.py`، و`docker-compose.yml` (`nginx` و`minio` و`MINIO_*`)، و`.env.example:214-224`.
> - `src/app/infrastructure/storage/minio_storage.py`، و`settings.py` (`MinioSettings` و`Limits`)، و`files/application/use_cases.py`، و`src/app/api/main.py:247-279`، و`docs/design/openapi.yaml` (`/files`).
> - كلّ اختبارٍ يقرأ ملفّي الحافّة أو `deploy-server.md` أو الخطّة (§5.1).
> - الصور المحلّيّة: `nginx:1.27` (`6784fb0834aa`) و`pgsty/minio:RELEASE.2026-08-04T00-00-00Z` (`b6bfe7239bfc`) موجودتان، فالعُدّة لا تسحب شيئاً.
>
> **ما لم يُقس، ولماذا لا يحجز التصميم:**
> - مسبارٌ للقراءة فقط لترويسات MinIO الحيّ على `127.0.0.1:19000` انتهت مهلته من جلسة التصميم، فلم يُكرَّر.
> - فترويسات MinIO في ردوده (`Access-Control-*` و`Strict-Transport-Security` و`X-Content-Type-Options`) مأخوذةٌ من تعليق RunPod (`deploy/runpod/nginx.conf:389-392`) ومن سلوك MinIO المعروف، لا من قياس.
> - المسار مصمَّمٌ ليصحّ في الحالين، أرسلها MinIO أم لا: يخفيها، والحافّة ترسل ترويساتها هي. والعُدّة تقيس النتيجة (AC‑1.6 وAC‑2.1).
> - لم يُشغَّل أيّ حاوية، ولم يُلمس المكدّس الحيّ، ولم يُتّصل بـCloudflare، ولم يُقرأ `.env` ولا `deploy/nginx/certs/*`.

## 1. الملخّص

الميزة كلّها **عند الحافّة وخارج `src/`**: مسارٌ في nginx، وفحص `[4]` في سكربت الفحص، واختبارات، ووثائق. لا مسار HTTP في التطبيق، ولا حدث، ولا مخطّط، ولا هجرة، ولا تُلمس طبقات `.importlinter`.

| المكوّن | التغيير | الدور |
|---|---|---|
| `deploy/nginx/app-locations.conf` | موقعان جديدان: `location = /workspace-files/` و`location ^~ /workspace-files/`، وتصحيح جملةٍ في تعليق الترويسات | FR‑1…FR‑11 (§3.2) |
| `deploy/nginx/nginx.conf` | تعليق `:140-142` وحده، ولا توجيه يتغيّر | FR‑17 (§3.3) |
| `deploy/smoke/stack_smoke.py` | شرط `[4]` في دالّةٍ مختبَرة، وتنزيلٌ عبر الرابط العامّ، ورسائل فشلٍ تسمّي الرمز، وتعليق الرأس | FR‑15، FR‑16، FR‑17 (§3.4) |
| `tests/unit/` | أربعة ملفّاتٍ جديدة، وتعديل حارسين في `test_edge_capacity.py` | §5 |
| الوثائق | `deploy-server.md` §5 جديد، وصفّا أمان، وتصحيح مهلتي Cloudflare، ونصّ 6.2 والاستثناء الرابع في الخطّة، وسطرٌ في `deploy-runpod.md`، وخليّةٌ في `08` | US‑6 (§3.6) |
| منطقة `aizzak.com` في Cloudflare | **قراءةٌ فقط** (AC‑2.4)، ولا تغيير | §3.5 |

**التدفّق على الخادم بعد 7.3:**
1. العميل يطلب `https://api.aizzak.com/workspace-files/<workspace_id>/<file_id>?X-Amz-…`.
2. Cloudflare يحفظ `Host: api.aizzak.com`، ولا يخزّن (قاعدة التجاوز).
3. الأصل على 443 (Full (strict))، فـnginx يطابق `location ^~ /workspace-files/`. ويقبل `GET` و`HEAD` و`PUT` وحدها، وجسماً حتّى 50 م.ب، بالبثّ وبحدّ المعدّل.
4. `minio:9000`، و`Host` هو ما أرسله العميل (`$http_host`).
5. MinIO يتحقّق من SigV4 على `api.aizzak.com`، فينجح الطلب.

**لا مسار قائم يُحجب.** التطبيق لا يعرّف شيئاً تحت `/workspace-files/`:
- جذره `/health*` و`/metrics` وحدها (`src/app/api/main.py:247-248`).
- والباقي تحت `api_prefix` = `/api/v1` (`:249-279`، و`servers: /api/v1` في `openapi.yaml:12-13`).
- فالمسار كان يقع في `location /` ويردّ التطبيق 404، وصار يصل MinIO. وهذا هو الفرق المحلّيّ الذي قبله المالك (Q‑7 (أ)).

**المكدّس المحلّيّ:**
- لا ملفّ Compose ولا `.env.example` ولا ملفّ أحجام يتغيّر، فلا تتغيّر بصمة `config --hash` لأيّ خدمة (AC‑3.1).
- والروابط المحلّيّة موقّعةٌ على `localhost:19000`، فتذهب إليه مباشرةً ولا تمرّ بالمسار.
- والفرق الوحيد هو Q‑7 (أ).

**RunPod:**
- لا يتغيّر فيه ملفّ (`deploy/runpod/` خارج الفرق).
- ويكسب `[4]` هناك خطوة تنزيل (Q‑4 (أ))، فحافّته تمرّر `GET` أصلاً (`deploy/runpod/nginx.conf:393`).

**النمط المتّبع:**
- المسار مأخوذٌ من نسخة RunPod (`deploy/runpod/nginx.conf:381-417`، الـcommit ‏`346a743`)، ومعه نمط الوجهة المتغيّرة في `location /` (`app-locations.conf:10-50`، و`:258-278`).
- والاختبارات على نمط 6.1: المحلِّل البنيويّ في `test_edge_hardening.py` يُعاد استعماله كما في `test_edge_cloudflare.py`. وفحوص النصّ تثبت أنّها تفشل على نسخةٍ معدّلة، كما في `test_cloudflare_tls_docs.py`. والعُدّة لا تعمل إلّا بطلبٍ صريح، كما في `live_edge` (`RUN_EDGE_CAPACITY_TEST=1`).

## 2. الملفّات

**المنفّذ:** `backend-developer` وحده لكلّ الملفّات، فلا يكتب في المستودع إلّا كاتبٌ واحد.

**المراحل المتخطّاة:**
- `data-engineer`: لا جدول ولا هجرة (§4).
- `frontend-developer` وUX: لا واجهة في المستودع.

**أعمال غير الكاتب:**
- `qa-engineer`: يكتب `test-plan.md`، ويشغّل العُدّة بطلبٍ صريح، وفحوص المكدّس المحلّيّ (§5.5).
- المنسّق: قراءات Cloudflare (§3.5).

| المسار | إنشاء/تعديل | المسؤوليّة | المنفّذ |
|---|---|---|---|
| `deploy/nginx/app-locations.conf` | تعديل | الموقعان (§3.2.2) بين `location /api/v1/ws` و`location /`، وتصحيح «none of the three locations» في تعليق الترويسات (§3.2.5) | backend-developer |
| `deploy/nginx/nginx.conf` | تعديل | التعليق فوق `client_max_body_size 2m;` وحده (§3.3) | backend-developer |
| `deploy/smoke/stack_smoke.py` | تعديل | `roundtrip_reason` و`public_roundtrip` و`check_public_roundtrip` و`failure_hint`، والبند 4 في التعليق (§3.4) | backend-developer |
| `tests/unit/test_public_file_links_edge.py` | إنشاء | AC‑1.4، AC‑1.8، AC‑3.2 (الفرق المقصود)، AC‑5.5، AC‑6.4، وحرّاس شكل المسار | backend-developer |
| `tests/unit/test_public_file_links_containers.py` | إنشاء | العُدّة المؤقّتة: 12 معياراً (§5.3)، ولا تعمل إلّا بـ`RUN_EDGE_FILES_TEST=1` | backend-developer |
| `tests/unit/test_stack_smoke_roundtrip.py` | إنشاء | AC‑4.1، AC‑4.2، AC‑4.3 | backend-developer |
| `tests/unit/test_public_file_links_docs.py` | إنشاء | AC‑5.6، AC‑6.1، AC‑6.2، AC‑6.3، ونصف AC‑6.4 الوثائقيّ | backend-developer |
| `tests/unit/test_edge_capacity.py` | تعديل | حارس الانجراف يقرأ دفعة `location /` بعينها، وحارس ترويسات WebSocket يستثني المسار الجديد (§5.1) | backend-developer |
| `docs/deploy-server.md` | تعديل | الرأس، و§3.8، و§4.7، و§4.8، وقسمٌ جديد `## 5)`، وصفّا ث‑16 وث‑17 (§3.6) | backend-developer |
| `docs/server-gpu-plan.md` | تعديل | §3 القاعدة 1 (استثناءٌ رابع)، و§5 نصّ 6.2. **وعند الإغلاق فقط:** صفّ اللوحة وسطر الحالة في الرأس | backend-developer |
| `docs/deploy-runpod.md` | تعديل | السطر 390: `[4]` ينزّل أيضاً، والسطر الجديد المتوقّع | backend-developer |
| `docs/design/08-local-runbook.md` | تعديل | خليّة «الدور» في صفّ `nginx` بجدول §2 (السطر 17) | backend-developer |
| `docs/server-gpu-summary.html` | تعديل **عند الإغلاق فقط** | بطاقة ٦٫٢ والشريط والمفتاح والتذييل | backend-developer |

**الملفّات المحميّة ثلاثة، وكلّها في قائمة Q‑6:**
- `deploy/nginx/app-locations.conf`.
- `deploy/nginx/nginx.conf`، والتعديل فيه تعليقٌ وحده.
- `deploy/smoke/stack_smoke.py`.

**ولا يُلمس:**
- `docker-compose*.yml` و`.env.example` و`.env.test.example`.
- `deploy/runpod/` و`deploy/release/` (ومنه `install.sh` و`bundle-files.txt`)، و`deploy/minio/`.
- `Dockerfile` و`.github/workflows/` و`src/` و`pyproject.toml`.

فلا بند ⚠️ لملفٍّ محميّ.

## 3. العقود

### 3.1 الواجهات البرمجية

لا تغيير:
- لا مسار جديد في التطبيق، و`docs/design/openapi.yaml` كما هو، ولا حدث.
- ولا عميل API يُعاد توليده (لا واجهة في المستودع).
- عقد `upload_url` و`download_url` (`FileRegisterOut` و`FileOut`) لا يتغيّر. يتغيّر فقط من يستقبل الرابط على الخادم.

### 3.2 `deploy/nginx/app-locations.conf`

#### 3.2.1 المكان

بعد كتلة `location /api/v1/ws { … }` (تنتهي في السطر 256 اليوم) وقبل `location / {` (السطر 258)، كما في RunPod (`/workspace-files/` بين WebSocket و`/`):
- الترتيب لا يغيّر المطابقة: الموقع المطابق التامّ يسبق كلّ بادئة، و`^~` يمنع أيّ regex، ولا regex في الملفّ.
- ويبقى أوّل ظهورٍ للنصّ `location /api/v1/ws` هو الموقع الحقيقيّ. فحارسا `test_log_correlation.py:299` و`test_edge_capacity.py:380` يأخذان أوّل ظهورٍ له.
- **ولا يكتب التعليق الجديد** `location /api/v1/ws` ولا `location / {` ولا أيّ قوس `{` أو `}`، لأنّ ثلاثة حرّاسٍ قائمين تبحث بالنصّ عن هذه الأشكال (§5.1).

#### 3.2.2 النصّ

التعليق بالإنجليزيّة كبقيّة الملفّ. ويجوز للمطوّر تهذيب صياغته دون معناه، ودون الأشكال الممنوعة في §3.2.1:

```nginx
# ── Presigned object URLs (server-gpu-plan.md 6.2) ──────────────────────────
# On a server, install.sh writes MINIO_PUBLIC_ENDPOINT=<the --domain> and
# MINIO_PUBLIC_SECURE=true, so the app signs every upload and download URL for
# the public name -- https://<domain>/workspace-files/<workspace>/<file>?X-Amz-...
# -- and those requests arrive HERE, through Cloudflare, and are passed to
# MinIO inside the Compose network. MinIO's own ports stay on 127.0.0.1.
#
# On the dev box the URLs name localhost:19000 and never come here. A request
# that does (localhost/workspace-files/...) now reaches MinIO instead of the
# app, and still needs a valid signature: the one local difference, a narrow
# exception to the plan's rule 1 (public-file-links Q-7).
#
# ⚠️ DIFFERENT FROM deploy/runpod/nginx.conf ON PURPOSE (public-file-links Q-1,
# Q-2). tests/unit/test_public_file_links_edge.py fails the build when either
# side is "unified" with the other:
#   * Host is the client's own. RunPod rebuilds it from X-Forwarded-Host
#     because RunPod's proxy replaces Host; Cloudflare keeps it, so there is
#     no map here, and a client's X-Forwarded-Host is dropped, never read.
#   * GET, HEAD and PUT only, and the bucket root is answered 404 right here.
#     The app presigns GetObject and PutObject and nothing else, so listing,
#     bucket settings, DELETE and multipart stay unreachable from the
#     internet even with a valid signature.
#   * MinIO's own Strict-Transport-Security and X-Content-Type-Options are
#     hidden, so the four headers at the top of this file are the only ones.
#     MinIO's HSTS carries includeSubDomains and would bypass the per-host
#     map in nginx.conf (6.1).
#   * The destination is a NAME resolved per request (the warning at the top
#     of this file); RunPod's is a literal loopback address.
location = /workspace-files/ {
    return 404;
}

location ^~ /workspace-files/ {
    # Per-request resolution, like $aizzak_app: the edge boots and answers
    # /health while MinIO is down, this path answers 502, and the first
    # request after MinIO is back succeeds within the resolver's valid=10s.
    set $aizzak_minio "minio:9000";
    # $request_uri, not $uri: SigV4 signed the path and the query exactly as
    # sent, so nothing may be decoded or normalised on the way. The location
    # match uses the normalised path, so a `..` cannot walk out of this
    # prefix into another bucket, and MinIO refuses any `..` it is sent.
    proxy_pass http://$aizzak_minio$request_uri;

    limit_except GET PUT {
        deny all;
    }

    # Repeated in full: a level that declares one proxy_set_header replaces
    # the whole inherited list (the trap the WebSocket location above
    # documents). $http_host, not $host: SigV4 signed the host and any
    # non-default port, and $host drops the port.
    proxy_set_header Host              $http_host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host  "";

    # CORS stays with the allow-list at the top of this file and its OPTIONS
    # 204. MinIO answers a request that carries Origin with an Allow-Origin of
    # its own, and a browser refuses a response that carries two. There is NO
    # add_header here: one would replace the whole inherited set
    # (tests/unit/test_edge_hardening.py).
    proxy_hide_header Access-Control-Allow-Origin;
    proxy_hide_header Access-Control-Allow-Credentials;
    proxy_hide_header Access-Control-Expose-Headers;
    proxy_hide_header Strict-Transport-Security;
    proxy_hide_header X-Content-Type-Options;

    # The registration endpoint refuses anything above max_upload_bytes
    # (50 MB, framework/settings/settings.py), but a presigned PUT carries no
    # size of its own and CompleteUpload does not read the object, so THIS is
    # the ceiling on the bytes. Raise both together, and never above
    # Cloudflare's 100 MB request limit on the free plan.
    client_max_body_size    50m;
    # Stream both ways: a 50 MB body is never spooled to /var/cache/nginx.
    proxy_request_buffering off;
    proxy_buffering         off;
    proxy_read_timeout      300s;
    proxy_send_timeout      300s;
    # The same zone and burst as `location /` below (P1-7, capacity 3.1).
    limit_req zone=api_req burst=400 nodelay;
}
```

#### 3.2.3 كلّ توجيه، وسببه، ومقارنته بـRunPod

| التوجيه | السبب | المتطلّب | RunPod |
|---|---|---|---|
| `location = /workspace-files/ { return 404; }` | المطابقة التامّة تسبق كلّ بادئة، فالسرد (`GET /workspace-files/?list-type=2`) وإعداد الدلو (`?policy`، `?versioning`) يردّان 404 من الحافّة. و`$uri` بلا استعلام، و`merge_slashes` يجعل `//workspace-files/` مثله | FR‑10، Q‑2 (ب) | غير موجود (فرقٌ مقصود) |
| `^~ /workspace-files/` | أطول بادئة، ويمنع أيّ regex لاحق | FR‑1 | نفسه |
| `set $aizzak_minio "minio:9000";` + `proxy_pass http://$aizzak_minio$request_uri;` | وجهةٌ متغيّرة، فيُحلّ الاسم مع كلّ طلب عبر `resolver 127.0.0.11 … valid=10s` (`nginx.conf:416`). لا `upstream` ولا `depends_on` (يحرسه `test_only_the_publisher_with_nothing_to_resolve_pools_upstream`). و`$request_uri` يمرّر المسار والاستعلام كما أُرسلا | FR‑3، FR‑4 | `proxy_pass http://127.0.0.1:9000` حرفيّاً (فرقٌ مقصود) |
| `limit_except GET PUT { deny all; }` | `GET` يسمح بـ`HEAD` ضمناً. وكلّ طريقةٍ أخرى 403 من الحافّة قبل MinIO: `POST` (الرفع المجزّأ) و`DELETE` و`PATCH` والطرق المجهولة. و**403 لا 405**، لأنّ 405 الصحيح يحتاج `add_header Allow`، وأيّ `add_header` في الموقع يمحو الترويسات الموروثة كلّها. و`OPTIONS` لا يصل الموقع: يجيبه `if` الخادم بـ204 (`app-locations.conf:119-121`) | FR‑10، Q‑2 (ب) | غير موجود (فرقٌ مقصود) |
| `Host $http_host` | الاسم والمنفذ كما وقّعهما minio‑py. `$host` يحذف المنفذ، وقد يأتي من `server_name`. وفي HTTP/2 يبني nginx ترويسة `Host` من `:authority` | FR‑2، Q‑1 (ب) | `$minio_signed_host` (فرقٌ مقصود) |
| `X-Real-IP` و`X-Forwarded-For` و`X-Forwarded-Proto $scheme` | القائمة نفسها في RunPod، و`$scheme` كبقيّة هذا الملفّ. لا يقرؤها التوقيع. و`$remote_addr` بعد `realip` (6.1) هو العميل | — | الأسماء نفسها، و`https` ثابتة لـProto كما في WebSocket |
| `X-Forwarded-Host ""` | القيمة الفارغة تعني ألّا تُرسل الترويسة، فلا تصل MinIO قيمةٌ من العميل. هذا تطبيق Q‑1 (ب) حرفيّاً: لا يقرؤها شيء، ولو قرأها إصدارٌ لاحق من MinIO | FR‑2 | لا يُعيَّن (فرقٌ مقصود) |
| `proxy_hide_header` لترويسات CORS الثلاث | ترويسة Allow-Origin واحدة يقبلها المتصفّح، وهي ترويسة الحافّة | FR‑8 | نفسها |
| `proxy_hide_header Strict-Transport-Security` | HSTS من MinIO (`max-age=31536000; includeSubDomains`) كان سيصل كلّ ردٍّ لهذا المسار، ومنه `localhost`. وهذا عكس بوّابة 6.1 لكلّ اسم (`nginx.conf:365-368`). وبعد الإخفاء يرسل الحافّة وحدها `max-age=31536000` لـ`api.aizzak.com`، ولا شيء لغيره | FR‑8، قرار 6.1 | غير موجود (فرقٌ مقصود، انظر §7) |
| `proxy_hide_header X-Content-Type-Options` | ترويسةٌ واحدة لا اثنتان: ترويسة الحافّة | FR‑8 | غير موجود (فرقٌ مقصود) |
| `client_max_body_size 50m` | `50m` = 52,428,800 = `Limits().max_upload_bytes` (`settings.py:992`)، وتحت 104,857,600 (Cloudflare المجّانيّ). ويبقى الحدّ العامّ 2 م.ب للـAPI (`nginx.conf:143`) | FR‑5 | نفسه |
| `proxy_request_buffering off` و`proxy_buffering off` | بثٌّ بلا قرص. ويعمل مع `proxy_http_version 1.1` الموروث من مستوى الخادم (`:52`)، وإلّا خُزّن الجسم المقسَّم (chunked) | FR‑6، NFR‑5 | نفسه |
| `proxy_read_timeout 300s` و`proxy_send_timeout 300s` | كما في RunPod و`location /`. خلف Cloudflare تسبقهما مهلتا Cloudflare (125 ث و30 ث). وهما تحدّان العميل المباشر (حتّى 6.3) وجهاز التطوير. فلا رقم جديد ولا انجراف | FR‑6 | نفسه |
| `limit_req zone=api_req burst=400 nodelay` | الدفعة نفسها في `/`. ولا `limit_conn` | FR‑7 | نفسه |

**ما لا يُضاف عمداً:**
- **لا `add_header`** في الموقعين، فالترويسات الثماني الموروثة تصل كما هي (§3.2.4).
- **لا `access_log` ولا `error_log` خاصّ.**
- **لا `X-Correlation-Id` ولا `X-Request-Id`** إلى MinIO، كما في RunPod: MinIO لا يقرؤهما، وسطر الحافّة يحملهما في السجلّ.
- **لا `if`** في موقع البادئة.

#### 3.2.4 تفاعله مع ما بُني قبله

| ما بُني | الأثر على المسار الجديد | الدليل |
|---|---|---|
| الترويسات الأمنيّة الأربع وCORS (`app-locations.conf:96-117`) | تُورَّث كاملةً، لأنّ الموقعين بلا `add_header`. و`always` يضعها على ردود MinIO 403/404 وعلى 404 و403 و413 و502 من الحافّة. والحارس البنيويّ `test_no_scope_declares_a_partial_add_header_set` يمرّ: لا `add_header` جزئيّة، ولا يحويها `limit_except` | `test_edge_hardening.py:377-411` |
| `OPTIONS` بـ204 (`:119-121`) | يُجاب في مرحلة rewrite على مستوى الخادم قبل اختيار الموقع، وبترويسات CORS. وتعلن `Allow-Methods` ‏`DELETE` و`POST` كما لكلّ مسار، والحافّة ترفضهما بعدها بـ403: إعلانٌ لا يفتح شيئاً | AC‑1.6 |
| خريطة HSTS (`nginx.conf:365-368`) | `api.aizzak.com` يأخذ `max-age=31536000` وحدها، والاسم الآخر لا شيء. وHSTS من MinIO مخفيّ | AC‑2.1 (فحص الحاوية الإضافيّ) |
| `realip` (6.1، `nginx.conf:179-234`) | `$remote_addr` هو العميل لطلبٍ من نطاقات Cloudflare، فمفتاح `limit_req` وسطر السجلّ و`X-Real-IP` إلى MinIO كلّها للعميل | `test_edge_cloudflare.py` كما هو |
| حدّ المعدّل (`api_req`، 200 ط/ث) | الموقع يستهلك المنطقة نفسها. ورفع 50 م.ب طلبٌ واحد | FR‑7 |
| سجلّ الوصول (`"path":"$uri"`) | بلا استعلام، فلا `X-Amz-Signature` ولا `X-Amz-Credential` فيه | `test_log_correlation.py:271-280`، وAC‑5.4 |
| سجلّ الأخطاء (`warn`) | سطر خطأ nginx لطلبٍ فشل (413، أو 502، أو انقضاء مهلة) يحمل سطر الطلب كاملاً بالاستعلام. وهذا سلوك nginx الثابت، ولا صيغة له تُضبط. يُسجَّل في ث‑16 (§3.6)، ولا يُطفأ السجلّ (§7، القرار 6) | AC‑5.4 يقيسه ولا يحجز عليه |
| `proxy_connect_timeout 2s` و`proxy_next_upstream … tries 2` (`nginx.conf:169-177`) | عنوانٌ واحد للاسم، فلا إعادة. واسمٌ لا يُحلّ (MinIO غائب) يعطي 502 فوراً | AC‑3.4 |
| `Expect: 100-continue` | nginx يجيبه بنفسه ولا يمرّره (ضمن ترويسات nginx الافتراضيّة المحذوفة). وcurl يرسله لجسمٍ فوق 1 ك.ب | العُدّة ترفع 1 م.ب و50 م.ب بـcurl |
| ترويسة `Server` من MinIO | nginx لا يمرّرها افتراضيّاً، فلا يظهر `Server: MinIO` | — |

#### 3.2.5 تصحيح التعليق القائم

في تعليق «Security headers + HSTS» (`app-locations.conf:93-95`): «none of the three locations in this file declares an `add_header` of its own» تصير «none of the locations in this file declares an `add_header` of its own».
- العدد خاطئٌ أصلاً، فالمواقع اليوم خمسة، وتصير سبعة.
- والادّعاء نفسه يبقى صحيحاً، ويحرسه `test_edge_hardening.py:377`.

#### 3.2.6 ما يحدث لكلّ طلب

| الطلب (عبر الحافّة) | يصل | الردّ |
|---|---|---|
| `PUT` موقّعٌ على `/workspace-files/<ws>/<file>` | MinIO | 200، أو 403 XML (توقيعٌ لاسمٍ آخر، أو رابطٌ منتهٍ) |
| `GET` أو `HEAD` موقّع | MinIO | 200 بالبايتات ونوع المحتوى المخزَّن، أو 403/404 XML |
| `DELETE` أو `POST …?uploads` أو `PATCH`، ولو موقّعاً | الحافّة | 403 (صفحة nginx) |
| `GET /workspace-files/` أو `PUT /workspace-files/?policy` | الحافّة | 404 (صفحة nginx) |
| `OPTIONS` على أيّ مسار | الحافّة (مستوى الخادم) | 204 بترويسات CORS |
| `/workspace-files` بلا شرطة أخيرة | التطبيق (`location /`) | 404 من التطبيق، كما اليوم |
| `/aizzak-backups/x`، `/aizzak-test/x`، `/minio/…` | التطبيق | 404 من التطبيق (FR‑9) |
| `/workspace-files/../aizzak-backups/x` و`/workspace-files/%2e%2e/aizzak-backups/x` | التطبيق: nginx يفكّ الترميز ويطبّع قبل المطابقة، فيصير `$uri` ‏`/aizzak-backups/x` | 404 من التطبيق |
| **خامٌ** `/aizzak-backups/../workspace-files/x` (`--path-as-is`) | MinIO، لأنّ المسار المطبَّع يبدأ بالبادئة، و`$request_uri` يمرّ خاماً | MinIO يرفض أيّ مكوّن `..` (400). ولا توقيع صالح لمسارٍ لم يوقّعه التطبيق. **تقيسه العُدّة** (AC‑5.1، الحالة الإضافيّة) |
| جسمٌ فوق 52,428,800 بايت | الحافّة | 413 (صفحة nginx)، ولا كائن |
| MinIO متوقّف (لا يُحلّ الاسم) | الحافّة | 502 فوراً، و`/health` يبقى 200 |

### 3.3 `deploy/nginx/nginx.conf`

تُستبدل الأسطر 140-142 (التعليق وحده)، ويبقى السطر 143 `client_max_body_size 2m;` كما هو:

```nginx
    # The API takes no request body of consequence, so a small ceiling is the
    # correct posture for it. Upload bytes DO pass through this edge on a
    # server (6.2): a URL signed for the public name arrives at
    # `location ^~ /workspace-files/` in app-locations.conf, which sets its own
    # ceiling equal to the app's max_upload_bytes. On the dev box the URLs name
    # localhost:19000 and go to MinIO directly.
```

لا خريطة جديدة (Q‑1 (ب))، ولا سطر آخر يتغيّر. فبصمة الملفّ لكلّ توجيه كما هي، والحرّاس القائمون على `nginx.conf` تمرّ دون تعديل.

### 3.4 `deploy/smoke/stack_smoke.py`

#### 3.4.1 التواقيع

```python
from collections.abc import Callable, Mapping
from typing import Protocol

_ROUNDTRIP_ENV = "SMOKE_PUBLIC_ROUNDTRIP"
# Byte-for-byte today's line (stack_smoke.py:141): AC-4.1, and AC-3.3 greps for it.
SKIPPED_LINE = "[4] presigned PUT round trip: skipped (not on RunPod)"
_PROBE_KEY = "smoke/presign-put-probe.txt"          # unchanged; Q-2 (b) lets it through
_PROBE_BODY = b"stack_smoke probe\n"                # unchanged
_PROBE_TTL_S = 300                                  # unchanged


class ProbeStorage(Protocol):
    """What [4] needs from MinioStorage (structural match, nothing new in src/)."""

    async def presign_put(self, key: str, ttl_s: int, content_type: str) -> str: ...
    async def presign_get(self, key: str, ttl_s: int) -> str: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


def roundtrip_reason(environ: Mapping[str, str]) -> str | None:
    """Why [4] runs here, or None to skip it.

    "RUNPOD_POD_ID" when RunPod set it (today's rule, unchanged);
    "SMOKE_PUBLIC_ROUNDTRIP=1" when the operator asked for it on a Compose server;
    None otherwise -- the dev box, and every install.sh run.
    Raises SystemExit, naming the variable, when it holds anything but "" or "1":
    an operator who typed `=true` must not read "skipped" as "passed".
    """


def failure_hint(response: httpx.Response) -> str:
    """One short reason for a non-200, in this order:
    the S3 `<Code>` from MinIO's XML body (SignatureDoesNotMatch, AccessDenied, ...);
    `cf-mitigated` present -> Cloudflare challenged this client;
    413 -> over the edge's client_max_body_size or Cloudflare's 100 MB;
    520-530 -> Cloudflare could not use the origin (docs/deploy-server.md §4.8);
    404 -> this edge does not route /workspace-files/ (a release without 6.2);
    403 -> refused by the edge (GET, HEAD and PUT only);
    502 -> the edge could not reach MinIO;
    anything else -> "unexpected answer"."""


async def public_roundtrip(storage: ProbeStorage, http: httpx.AsyncClient) -> None:
    """PUT the probe through the public URL, read it back inside, GET it through
    the public URL and compare. The probe object is deleted in every case
    (`finally`). Raises SystemExit on any failure. No printed line and no
    message carries a query string: URLs are shown up to '?'."""


async def check_public_roundtrip(
    environ: Mapping[str, str],
    storage: ProbeStorage,
    open_client: Callable[[], httpx.AsyncClient],
) -> None:
    """Check [4]: SKIPPED_LINE, or the reason and then public_roundtrip()."""
```

**`check_presign()`:**
- يبقى كما هو حتّى نهاية `[3]` (السطر 138).
- ثمّ يستبدل الأسطر 140-159 بسطرٍ واحد: `await check_public_roundtrip(os.environ, storage, lambda: httpx.AsyncClient(timeout=30))`.
- و`main()` وسطر `PASS: pgbouncer/OPS-02, RLS fail-closed, and presign addressing all verified` كما هما، فـ`deploy-runpod.md:390` يقتبس الأخير.

#### 3.4.2 السلوك والمخرج

| البيئة | `[4]` | المخرج |
|---|---|---|
| جهاز التطوير، و`install.sh` على أيّ خادم: لا `RUNPOD_POD_ID` ولا المتغيّر | يُتخطّى، ولا رابط يُوقَّع لـ`[4]` ولا طلب يُرسل | `SKIPPED_LINE` كما اليوم |
| RunPod (`RUNPOD_POD_ID`) | يعمل كما اليوم، **ويُضاف التنزيل** (Q‑4 (أ)) | `    [4] runs: RUNPOD_POD_ID`، ثمّ سطرا النجاح |
| خادم Compose، و`docker compose exec -T -e SMOKE_PUBLIC_ROUNDTRIP=1 app python /app/deploy/smoke/stack_smoke.py` | يعمل | `    [4] runs: SMOKE_PUBLIC_ROUNDTRIP=1`، ثمّ سطرا النجاح |
| `SMOKE_PUBLIC_ROUNDTRIP` بقيمةٍ غير `1` (ولا `RUNPOD_POD_ID`) | لا يعمل | `FAIL: SMOKE_PUBLIC_ROUNDTRIP must be 1 or unset, got …` والخروج 1 |

**خطوات `public_roundtrip`، بالترتيب:**
1. `put_url = await storage.presign_put(_PROBE_KEY, _PROBE_TTL_S, "text/plain")`، ويُطبع `    PUT -> <الرابط حتّى ?>`.
2. داخل `try`: `PUT` بـ`_PROBE_BODY` و`Content-Type: text/plain`.
   - غير 200: `SystemExit(f"FAIL: presigned PUT through {base} answered {status} ({failure_hint(r)}) -- browser uploads are broken")`.
   - `httpx.HTTPError`: `SystemExit(f"FAIL: presigned PUT through {base} got no answer: {type(exc).__name__}: {exc} -- browser uploads are broken")`.
3. `await storage.get(_PROBE_KEY)` يساوي `_PROBE_BODY`. وإلّا الرسالة القائمة: «answered 200 but the object is not in MinIO».
4. يُطبع السطر القائم بنصّه: `[4] presigned PUT through the public address stored the bytes  OK`.
5. `get_url = await storage.presign_get(_PROBE_KEY, _PROBE_TTL_S)`، ثمّ `GET`.
   - غير 200، أو خطأ نقل: الصيغة نفسها، والخاتمة «-- downloads are broken».
   - بايتاتٌ مختلفة: `FAIL: presigned GET through {base} returned {n} bytes that differ from the {m} uploaded -- downloads are broken`.
6. يُطبع السطر الجديد: `[4] presigned GET through the public address returned the same bytes  OK`.
7. `finally: await storage.delete(_PROBE_KEY)`، كما اليوم. وحذف كائنٍ غير موجود لا يفشل في S3.

**ما لا يتغيّر:**
- **رمز الخروج:** `SystemExit("…")` يعني 1، كما اليوم. و`install.sh` يحوّل أيّ رمزٍ غير الصفر إلى 71، ولا يرى هذا الفرع لأنّه لا يمرّر المتغيّر.
- **عدم المتابعة:** لا تحويل (`follow_redirects` الافتراضيّ في httpx هو `False`).

**`install.sh` لا يتغيّر** (Q‑4 (أ)):
- أمره `compose 300 exec -T app python /app/deploy/smoke/stack_smoke.py` (`install.sh:1324`).
- و`docker compose exec` لا يورث بيئة المضيف، فلا يصل المتغيّر الحاويةَ إلّا بـ`-e`.
- فأوّل تثبيتٍ قبل DNS يطبع `SKIPPED_LINE` وينتهي `READY`.

#### 3.4.3 تعليق الرأس، البند 4 (الأسطر 24-32)

```python
  4. **A presigned PUT and GET through the public address round-trip the
     bytes.** [3] reads the URL and never sends it, and on RunPod that was not
     enough: the first staging Pod passed [3] while every browser upload
     failed `403 SignatureDoesNotMatch`, because RunPod's proxy rewrites the
     Host that SigV4 signed (deploy/runpod/nginx.conf, `$minio_signed_host`).
     Only a real round trip proves it, and it runs only where the public
     address is reachable from inside this container:
       * on RunPod (`RUNPOD_POD_ID` is set), always;
       * on a Compose server, only when the operator asks for it after the
         name has a DNS record and the origin certificate is in place
         (docs/deploy-server.md §5.3):
             docker compose exec -T -e SMOKE_PUBLIC_ROUNDTRIP=1 app \
                 python /app/deploy/smoke/stack_smoke.py
         install.sh never sets it: the first install precedes both, and a
         failure here would end it with exit 71;
       * on the dev box it is skipped: the URLs name the operator's
         localhost:19000, which from in here is this container itself.
```

### 3.5 Cloudflare: قراءاتٌ للمنسّق (AC‑2.4)

**القيود:**
- بأداة Cloudflare API في الجلسة، بطلبات `GET` وحدها، لا بـcurl وتوكن.
- لا تغيير في المنطقة.
- تُنفَّذ الآن، بالتوازي مع البناء، وقبل سجلّ DNS في كلّ حال. وتُضمّ إلى صورة PO‑1 الكاملة (`docs/delivery/cloudflare-tls/follow-ups.md:29`) إن أُخذت معاً.
- تُكتب النتيجة في `status.md`. **وما يخالف المتوقّع يُعرض على المالك قبل DNS.**

| # | الطلب | المتوقّع |
|---|---|---|
| 1 | `GET /zones?name=aizzak.com` | `zone_id` |
| 2 | `GET /zones/{zone_id}/rulesets/phases/http_request_origin/entrypoint` | 404 (لا Origin Rules)، أو لا قاعدة يطابق تعبيرها `api.aizzak.com` وتغيّر `host_header` أو `origin` أو `sni` |
| 3 | `GET /zones/{zone_id}/rulesets/phases/http_request_transform/entrypoint` | لا قاعدة URL Rewrite تغيّر `uri.path` أو `uri.query` لهذا الاسم |
| 4 | `GET /zones/{zone_id}/rulesets/phases/http_request_late_transform/entrypoint` | لا قاعدة ترويسات طلبٍ لهذا الاسم تمسّ `Host` أو `X-Forwarded-Host` أو `Authorization` |
| 5 | `GET /zones/{zone_id}/url_normalization` | `scope` هو `incoming` لا `both`، فالتطبيع لا يمتدّ إلى الأصل |
| 6 | `GET /zones/{zone_id}/rulesets/phases/http_request_cache_settings/entrypoint` | قاعدة التجاوز: التعبير `(http.host eq "api.aizzak.com")` بلا شرط مسار، والإجراء `set_cache_settings` و`cache: false` |
| 7 | `GET /zones/{zone_id}/bot_management` | قيمة `fight_mode` تُسجَّل (R‑3) |
| 8 | `GET /zones/{zone_id}/settings/browser_check` | القيمة تُسجَّل (R‑3) |
| 9 | `GET /zones/{zone_id}/workers/routes` | لا نمط يغطّي `api.aizzak.com/*` (Worker يعيد كتابة الطلب) |
| 10 | `GET /zones/{zone_id}/rulesets/phases/http_request_firewall_custom/entrypoint` و`…/http_ratelimit/entrypoint` | معلومة: لا قاعدة تحجب `PUT` أو `/workspace-files/` لهذا الاسم |
| 11 | `GET /zones/{zone_id}/managed_headers` و`…/rulesets/phases/http_response_headers_transform/entrypoint` | معلومة: لا شيء يضيف `Access-Control-*` أو `Strict-Transport-Security` لهذا الاسم |

البنود 1-8 هي AC‑2.4 حرفيّاً، و9-11 معلوماتٌ تحسم A‑1 أوسع.

### 3.6 الوثائق (US‑6)

**قيودٌ من حرّاسٍ قائمين يلتزم بها كلّ نصّ:**
- **عناوين الأقسام:**
  - `section_four()` في `test_cloudflare_tls_docs.py:25-28` يقرأ من `## 4) ` إلى أوّل `\n## `، فالقسم الجديد يبدأ بـ`## 5) `.
  - و`section(doc, "3")` في `test_install_bundle_docs.py:72-78` كذلك.
  - و`plan_section()` في `test_cloudflare_tls_docs.py:48-50` ينتهي عند `#### 6.2 `، فالعنوان يبقى بنصّه.
- **§4.7:** يبقى فيه «100 م.ب» و`` `524` `` و`runWorkflow` و«SSE» (`_LIMITS` و`test_the_524_limit_and_the_sse_advice_are_written`).
- **صفوف الأمان:**
  - تبدأ بـ`| ث‑N |` وبأربعة أعمدة بالضبط (`count("|") == 5`)، فلا `|` داخل خليّة.
  - وتتّصل بالجدول بلا سطرٍ فارغ (`test_gpu_preflight.py:690`).
  - ولا صفّ آخر في الوثيقة يبدأ بـ`| ث‑`.
- **رموز جدول الأعطال** بين علامتي الكود (`` `413` ``) كما في §4.8.
- **القاعدة 1:** الاستثناء الرابع داخلها، بين «1. **المكدّس المحلّيّ لا يتغيّر» و«2. **الموافقة قبل التنفيذ» (`test_monitoring_profile.py:378`).
- **لا مثال مفتاحٍ PEM** في الدليل (`test_gpu_compose_hygiene.py`).

| الملفّ | الموضع | المحتوى |
|---|---|---|
| `docs/deploy-server.md` | الرأس (السطر 5) | «…وقسم الشهادة عبر Cloudflare (§4، الخطوة 6.1)، **وقسم روابط الملفّات العامّة (§5، الخطوة 6.2)**، وقائمة الأمان…» |
| | §3.8 (السطر 189) | تُحذف «روابط الملفّات العامّة (6.2)، » من قائمة ما لا يعالجه. ويُضاف: «أمّا روابط الملفّات (6.2) فيكتب لها `install.sh` النطاقَ في `MINIO_PUBLIC_ENDPOINT` و`MINIO_PUBLIC_SECURE=true`، وتمرّرها الحافّة (§5). لكنّه لا يفحص الرابط العامّ: فحص `[4]` يُشغَّل بيدك بعد سجلّ DNS والشهادة (§5.3).» |
| | §4.7 (السطر 323) | «Cloudflare ينتظر الأصل مهلتين، ونهاية أيٍّ منهما تصل العميلَ `524` والعمل جارٍ على الخادم: **125 ثانية** ليبدأ الردّ (Proxy Read Timeout)، و**30 ثانية** ليقبل الأصل ما يُرسَل إليه (Proxy Write Timeout، لا تتغيّر في الخطّة المجّانيّة). تحقّق المنسّق منهما في وثائق Cloudflare (2026‑10‑22). والجواب المجمَّع لسير عملٍ طويل (`runWorkflow`) معرّضٌ للأولى، فيُنصح بالبثّ SSE (`text/event-stream`). ورفع ملفٍّ كبير على اتّصالٍ بطيء يُقاس في 7.3 (§5.2).» وفي البند الأوّل يُلحق «(§5)» بعد «50 م.ب برابطٍ موقّع» |
| | §4.8 صفّ `` `524` `` (السطر 335) | المعنى: «الأصل لم يبدأ الردّ خلال 125 ثانية، أو لم يقبل ما يُرسَل إليه خلال 30 ثانية». العلاج: «بثّ SSE بدل الجواب المجمَّع. ولرفع ملف: §5.2». **فلا يبقى «100 ثانية» في §4** |
| | **قسمٌ جديد** `## 5) روابط الملفّات العامّة (6.2)` بعد §4.8 وقبل `---` و«قائمة الأمان المؤجّلة» | الأقسام الفرعيّة أدناه |
| | قائمة الأمان، بعد ث‑15 | ث‑16 وث‑17 (أدناه) |
| `docs/server-gpu-plan.md` | §3 القاعدة 1، بعد الاستثناء الثالث (بعد السطر 98) | الاستثناء الرابع (أدناه) |
| | §5، نصّ 6.2 (الأسطر 381-386) | (أدناه). والعنوان `#### 6.2 روابط الملفّات العامّة` كما هو |
| | **عند الإغلاق فقط:** صفّ **6.2** في اللوحة (السطر 139)، وسطر الحالة في الرأس (السطر 7) | عمود الموافقة: «موافقة `deploy/nginx/` و`deploy/smoke/`». والحالة: «🟡 مدموجة <التاريخ> (PR ‎#N، `<sha>`، `docs/delivery/public-file-links/`)، بانتظار 7.3: `[4]` على الخادم ورحلة `curl` من الخارج». ومعه جملةٌ في الرأس على نمط 6.1 |
| `docs/deploy-runpod.md` | السطر 390 | «…وقبله السطران `[4] presigned PUT through the public address stored the bytes  OK` و`[4] presigned GET through the public address returned the same bytes  OK`: الفحص يرفع ملفّاً صغيراً فعلاً عبر وكيل RunPod، ثمّ ينزّله عبر الرابط العامّ ويقارن البايتات، ثمّ يحذفه، لأنّ قراءة نصّ الرابط وحدها لم تكشف عطل `Host`.» |
| `docs/design/08-local-runbook.md` | §2، صفّ `nginx` (السطر 17)، عمود «الدور» | «Reverse proxy · TLS · WS upgrade · روابط الملفّات الموقّعة إلى MinIO على الخادم (`/workspace-files/`، `GET` و`HEAD` و`PUT`، 6.2)» |
| `docs/server-gpu-summary.html` | **عند الإغلاق فقط** | بطاقة ٦٫٢ `step partial` بوسم «مدموجة · تنتظر الخادم»، والشريط ١٣ جارية و٨ لم تبدأ، و`aria-label` والمفتاح والتذييل. يحرسه `test_the_summary_matches_the_plan_and_its_bar_matches_its_articles` |

**الاستثناء الرابع في القاعدة 1** (بنمط الثالث، بندٌ فرعيّ):

> - **استثناءٌ ضيّق رابع ([6.2](#62-روابط-الملفّات-العامّة)، قرار المالك 2026‑10‑22، Q‑7 في `docs/delivery/public-file-links/`):** مسار `/workspace-files/` في `deploy/nginx/app-locations.conf` يعمل على حافّة جهاز التطوير أيضاً، لأنّ الملفّ واحدٌ للخادم والجهاز. فطلبٌ إلى `localhost/workspace-files/…` على 80 أو 443 يصل MinIO لا التطبيق، ويبقى محتاجاً إلى توقيعٍ صالح. وحافّة الجهاز منشورةٌ على كلّ الواجهات، فيُبلَغ دلو التطبيق منها، وكان على `127.0.0.1:19000` وحده. ولا يتغيّر مسارٌ قائم: الروابط المحلّيّة موقّعةٌ على `localhost:19000` وتذهب إليه مباشرة، وبصمات `docker compose config --hash` لا تتغيّر.

**نصّ 6.2 في §5 من الخطّة:**

> - **المشكلة:** كما هي.
> - **الطريقة** (بوابة ① في `docs/delivery/public-file-links/`، 2026‑10‑22):
>   1. `location ^~ /workspace-files/` في `deploy/nginx/app-locations.conf` يمرّر إلى `minio:9000` باسمٍ يُحلّ مع كلّ طلب، **بلا خريطة**: `Host` الذي يصل MinIO هو `Host` العميل (`$http_host`)، و`X-Forwarded-Host` لا يُقرأ ولا يُمرَّر (Q‑1 (ب)). خريطة `$minio_signed_host` في RunPod سببها وكيلٌ يستبدل `Host`، وCloudflare يحفظه، و`map` لا يجوز داخل `server`. والمسار يقبل `GET` و`HEAD` و`PUT` وحدها، وجذر الدلو 404 من الحافّة (Q‑2 (ب)). وحدّ الجسم فيه 50 م.ب (`max_upload_bytes`)، والبثّ في الاتّجاهين. ويختلف عن مسار RunPod عمداً، واختبارٌ يحرس الفرق.
>   2. `MINIO_PUBLIC_ENDPOINT=<النطاق>` و`MINIO_PUBLIC_SECURE=true` يضبطهما `install.sh` منذ 5.2، فلا يتغيّر.
>   3. MinIO يبقى على `127.0.0.1`، واختبارٌ يحرس ذلك.
>   4. `[4]` في `stack_smoke.py` يرفع وينزّل عبر الرابط العامّ: على RunPod كما كان، وعلى خادم Compose بطلبٍ صريح `SMOKE_PUBLIC_ROUNDTRIP=1` بعد سجلّ DNS والشهادة. و`install.sh` لا يمرّره (Q‑4 (أ)).
> - **القبول** (Q‑5 (أ)، في 7.3): `[4]` على الخادم عبر الاسم العامّ، ورحلة `curl` من جهاز المالك: تسجيلٌ ثمّ رفعٌ ثمّ إتمامٌ ثمّ تنزيلٌ بالبصمة نفسها ([`deploy-server.md`](deploy-server.md) §5.3).

**أقسام `## 5)` في `docs/deploy-server.md`** (عربيّة، والأوامر بالإنجليزيّة):
- **مقدّمة (أربعة أسطر):** التطبيق يوقّع على `MINIO_PUBLIC_ENDPOINT` (= `--domain`، يكتبه `install.sh` منذ 5.2)، فالرابط `https://api.aizzak.com/workspace-files/<مساحة العمل>/<الملف>?X-Amz-…`. وCloudflare ثمّ nginx (`location ^~ /workspace-files/`) ثمّ `minio:9000`، و`Host` كما أرسله العميل. وMinIO على `127.0.0.1` وحده. وجهاز التطوير لا يمرّ بالمسار، والفرق الوحيد فيه Q‑7.
- **5.1 كيف يعمل الرابط:**
  - `GET` و`HEAD` و`PUT` وحدها. وجذر الدلو 404. ودلو النسخ و`/minio/…` لا مسار لها (404 من التطبيق).
  - لا تُرسل ترويسة `Authorization` (توكن الـAPI) إلى `upload_url` ولا `download_url`: التوقيع في الرابط هو الصلاحيّة، وMinIO يرفض الطلب الذي يحمل الاثنين. وA‑9 يؤكّده المالك في 7.3.
  - مدّتا الرابطين: رفع 900 ث، وتنزيل 300 ث (`MINIO_PRESIGN_PUT_TTL_S` و`MINIO_PRESIGN_GET_TTL_S`).
  - ونوع الملفّ يختاره الرافع (ث‑17).
- **5.2 الحدود:**
  - 50 م.ب = `max_upload_bytes` = `client_max_body_size 50m` في المسار، مقابل 100 م.ب حدّ Cloudflare المجّانيّ (413).
  - «يُرفعان معاً، ولا يتجاوزان 100 م.ب»، واختبارٌ يفشل إن تجاوزا.
  - مهلتا Cloudflare: 125 ث و30 ث (§4.7). ورفع 50 م.ب على اتّصالٍ بطيء يُقاس في 7.3، وعلاجه (حدٌّ أصغر أو رفعٌ مجزّأ) خارج 6.2.
  - **واسم الدلو:** المسار `/` + `MINIO_BUCKET` الافتراضيّ (`workspace-files`). **تغيير `MINIO_BUCKET` على خادمٍ يكسر كلّ رابطٍ بصمت**، لأنّ الحافّة لا تمرّر غير `/workspace-files/`. لا تغيّره، واختبارٌ يربط القيم الثلاث.
- **5.3 التحقّق من خارج الخادم** (بعد §4: سجلّ DNS وشهادة المنشأ). الأوامر في §5.6 من هذا التصميم بنصّها.
- **5.4 جدول الأعطال:** الأعمدة «الرمز | المعنى | الفحص | العلاج». الصفوف:
  - `` `403` `` وفي الجسم `SignatureDoesNotMatch`.
  - `` `403` `` وفي الجسم `AccessDenied` (`Request has expired`).
  - `` `403` `` من nginx (طريقةٌ غير المسموحة).
  - `` `403` `` وفيه ترويسة `cf-mitigated` (تحدّي Cloudflare).
  - `` `413` ``.
  - `` `404` `` من التطبيق (حافّةٌ بلا 6.2).
  - `` `404` `` من nginx (جذر الدلو، مقصود).
  - `` `502` `` (MinIO متوقّف، وأوّل طلبٍ بعد عودته ينجح خلال 10 ثوانٍ دون إعادة nginx).
  - `` `524` `` (§4.7).
  - `` `526` `` على رابط ملف (§4.2).
  - ونصّ الفحص والعلاج على نمط §4.8.
- **5.5 خادمٌ ثُبّت قبل 6.2:** الإصدار الذي فيه 6.2 لازم (R‑4):
  - ملفّا nginx في الحزمة، فيُنسخان من حزمة الإصدار الجديد، ثمّ `nginx -t` في حاويةٍ مؤقّتة كما في §4.5، ثمّ **`sudo docker compose restart nginx`**: الملفّان مربوطان ملفّاً، و`reload` يعيد النصّ القديم.
  - و`stack_smoke.py` في صورة التطبيق لا في الحزمة، فالتنزيل في `[4]` يأتي مع صورة ذلك الإصدار.
  - حتّى `upgrade.sh` (5.3) يُثبَّت 7.3 من إصدارٍ بعد 6.2.

**صفّا الأمان** (عمودٌ لكلٍّ من: # | الثغرة | الأثر | العلاج المقترح):

| # | الثغرة | الأثر | العلاج المقترح |
|---|---|---|---|
| ث‑16 | اعتماد MinIO يعمل من الإنترنت عبر الحافّة (6.2). قبل 6.2 كان MinIO لا يُبلَغ إلّا من الخادم نفسه (`127.0.0.1`). والآن يقبل مسار `/workspace-files/` أيّ طلبٍ موقّعٍ صالح بـ`GET` و`HEAD` و`PUT` على دلو التطبيق. وضيّقه Q‑2 (ب): لا حذف ولا سرد ولا إعداد للدلو ولا رفع مجزّأ، وجذر الدلو 404. والتطبيق يوقّع باعتماد جذر MinIO (`secret/data/minio`)، وكلمة سرّه في `.env` على الخادم (ث‑11). وسطر سجلّ الأخطاء في nginx لطلبٍ فشل على هذا المسار (413 أو 502 مثلاً) يحمل سطر الطلب كاملاً، ومنه `X-Amz-Signature` (صالحٌ حتّى 15 دقيقة) و`X-Amz-Credential` (اسم مفتاح الوصول) | من يملك الاعتماد يقرأ أيّ ملفٍّ لأيّ مستأجرٍ ويكتب فوقه من الإنترنت، لا من الخادم وحده. ومن يقرأ سجلّ الأخطاء يعيد استعمال رابطٍ لم تنقضِ مدّته | هويّة توقيعٍ مقيّدة لـMinIO بدل الجذر: حساب خدمةٍ بسياسة `s3:GetObject` و`s3:PutObject` على `workspace-files/*` وحده، في الموجة 8 (شيفرة تطبيق). وحتّى ذلك `chmod 600 .env` (ث‑11) |
| ث‑17 | نوع الملفّ يختاره الرافع ويُخدم من اسم الـAPI (Q‑3 (أ)، 6.2). رابط الرفع لا يوقّع `Content-Type` (حدٌّ في minio-py)، و`CompleteUpload` لا يقرأ الكائن المخزَّن. فعضوٌ في مساحة العمل يسجّل `text/plain` ويرفع `text/html`، ورابط التنزيل يخدمه من `https://api.aizzak.com`. قبله المالك لمرحلة staging (2026‑10‑22). والحال نفسه على RunPod | صفحةٌ يتحكّم بها مستخدمٌ تعمل على أصل الـAPI. والأثر محدود: الـAPI بلا كوكيز، والتوكن في أصل الواجهة لا في أصل الـAPI | أن يوقّع التطبيق `response-content-disposition` و`response-content-type` في رابط التنزيل، أو يتحقّق من الكائن (الحجم والنوع) عند `CompleteUpload`. أو اسمٌ منفصل للملفّات (Q‑3 (ج)) |

### 3.7 حسم الافتراضات

- **A‑1** (Cloudflare يحفظ `Host` والمسار والاستعلام): يُقرأ في §3.5 (AC‑2.4)، ويُقاس في 7.3 (AC‑4.4 وAC‑1.9).
- **A‑5** (المسار ASCII): يثبته AC‑1.8 بدالّة التطبيق نفسها.
- **A‑7** (CORS بلا أصلٍ ولا ترويسةٍ جديدة): يثبته AC‑1.6 في العُدّة.
- **A‑8** (الدلو خاصّ): AC‑5.1 وAC‑5.2، والطلب غير الموقّع إلى MinIO يردّ 403 في العُدّة.
- **A‑9** (ترويسات الواجهة): يبقى للمالك في 7.3. ويضيف التصميم إليه: «ولا `Authorization` مع الرابط الموقّع» (§3.6، §5.1 من الدليل).
- **A‑10** (`localhost:19000` محلّيّاً): يُقرأ من مخرج `[3]` في AC‑3.3.

## 4. البيانات

لا تغيير: لا جدول ولا عمود ولا فهرس، ولا هجرة Alembic، ولا RLS، ولا دلوٌ جديد ولا سياسة دلو. **مرحلة `data-engineer` تُتخطّى.**

## 5. خطّة الاختبار المقترحة

### 5.1 الحرّاس القائمون، وما يتغيّر منهم

| الاختبار | يقرأ | يتغيّر؟ |
|---|---|---|
| `test_edge_hardening.py` (كلّه) | الحافّتان | **لا.** `_locations()` (`:94-97`) لا يطابق موقعاً بمُعدِّل (`^~` و`=`)، فلا يرى المسار الجديد. والحارس البنيويّ يرى `limit_except` بلا `add_header` فيمرّ |
| `test_edge_capacity.py::test_the_two_edges_have_not_drifted` (`:278-306`) | الحافّتان | **نعم.** `burst()` يأخذ أوّل `limit_req … burst=` في الملفّ، وفي RunPod أوّله في `/workspace-files/`. فيُقرأ من كتلة `location /` بعينها في الملفّين. ويُضاف: دفعة `/workspace-files/` في Compose = دفعة `/` (FR‑7، AC‑3.2) |
| `test_edge_capacity.py::test_the_websocket_handshake_still_carries_the_shared_proxy_headers` (`:509-542`) | الحافّتان | **نعم.** «المشترك» يُحسب اليوم بالملفّ كلّه ناقصاً كتلة WebSocket، فيدخل فيه `x-forwarded-host` من المسار الجديد، وليس في كتلة WebSocket، فيفشل. فتُطرح كتلة `/workspace-files/` أيضاً في الملفّين، فيبقى «المشترك» ما على مستوى الخادم كما قصد الحارس |
| `test_edge_capacity.py` (البقيّة) | — | لا. `_ws_location` وحالاته السلبيّة كما هي |
| `test_log_correlation.py` (`:246-311`) | `nginx.conf` و`app-locations.conf` | لا. `$uri` في السجلّ، والمعرّفان في WebSocket، وأوّل `location /api/v1/ws` هو الحقيقيّ (§3.2.1) |
| `test_connection_timeouts.py:92` | `location / \{…proxy_read_timeout` | لا. التعليق الجديد لا يكتب `location / {` |
| `test_rolling_deploy.py:169,187-188` | `/health` و`/health/drain` | لا |
| `test_edge_cloudflare.py` | `realip` وHSTS | لا |
| `test_install_sh.py:1386,1399,1412` | أمر الفحص في `install.sh` | لا، و`install.sh` لا يتغيّر |
| `test_cloudflare_tls_docs.py` | `deploy-server.md` §4 والخطّة | لا، بقيود §3.6 |
| `test_install_bundle_docs.py` و`test_gpu_preflight.py:675,690` و`test_monitoring_profile.py:378` | الدليل والخطّة والملخّص | لا، بقيود §3.6. وعند الإغلاق يتغيّر الملخّص، ويحرسه `test_the_summary_matches_the_plan_and_its_bar_matches_its_articles` |

**الدالّة المساعدة في `test_edge_capacity.py`:**
- `_location_block(text: str, path: str) -> str`: أوّل موقعٍ يبدأ سطره بـ`location` (بمُعدِّلٍ اختياريّ `=` أو `^~`) ومساره `path`.
- مطابقة الأقواس فيه تتخطّى ما بعد `#` إلى آخر السطر.
- وتُعيد **نصّاً من الأصل كما هو**، فيبقى `text.replace(block, "")` صالحاً.
- و`_ws_location` يبقى كما هو، لأنّ حالاته السلبيّة تعتمد عليه.

### 5.2 الملفّات الجديدة وتواقيعها

```python
# tests/unit/test_public_file_links_edge.py -- no Docker, no network.
# Reuses the structural parser, as test_edge_cloudflare.py does.
from tests.unit.test_edge_hardening import Statement, _iter_blocks, _parse_config
# AC-1.8 reuses the file use-case fakes the same way.
from tests.unit.test_files_use_cases import _SPACE, _FakeFiles, _ctx, _transfers

_CLOUDFLARE_FREE_BODY_LIMIT = 104_857_600   # 100 MB; Cloudflare answers 413 above it

def location_body(tree: list[Statement], *args: str) -> list[Statement]: ...
#   exactly one `location <args...>` block anywhere in the tree, else AssertionError naming it
def direct(body: list[Statement], name: str) -> list[list[str]]: ...   # direct children only
def size_bytes(value: str) -> int: ...   # nginx sizes: "50m" -> 52_428_800 (k/m/g, any case)

def body_limit_problems(locations_text: str, max_upload_bytes: int) -> list[str]: ...
def bucket_binding_problems(
    locations_text: str, runpod_text: str, compose_text: str, env_example_text: str
) -> list[str]: ...
#   both Compose locations, the RunPod location, every ${MINIO_BUCKET:-X} default in Compose
#   (today :94 and :1538) and the single MINIO_BUCKET= line in .env.example name one bucket
def unbound_ports(service: Mapping[str, object]) -> list[str]: ...
#   short syntax must start "127.0.0.1:"; long syntax must carry host_ip "127.0.0.1"
def files_route_problems(locations_text: str, nginx_text: str) -> list[str]: ...
#   one `= /workspace-files/` whose body is exactly `return 404`; one `^~ /workspace-files/`;
#   set $aizzak_minio "minio:9000" + proxy_pass http://$aizzak_minio$request_uri; no add_header
#   in either; limit_except GET PUT { deny all; }; Host $http_host; X-Forwarded-Host "";
#   the five proxy_hide_header; no other proxy_pass names minio; nothing in deploy/nginx/
#   reads $http_x_forwarded_host or $minio_signed_host; nginx.conf keeps `resolver`
def files_drift_problems(compose_locations_text: str, runpod_text: str) -> list[str]: ...
#   identical on both: client_max_body_size, proxy_request_buffering, proxy_buffering,
#     proxy_read_timeout, proxy_send_timeout, limit_req
#   intended and only: Host ($http_host vs $minio_signed_host); proxy_set_header names
#     Compose - RunPod == {x-forwarded-host}, RunPod - Compose == {};
#     proxy_hide_header Compose - RunPod == {strict-transport-security, x-content-type-options},
#     RunPod - Compose == {}; limit_except and the exact root location on Compose only;
#     destination (variable + set vs literal 127.0.0.1:9000); X-Forwarded-Proto value
#   any other directive on one side only -> a problem naming it

def test_the_body_limit_is_the_app_s_upload_limit_and_under_cloudflare_s() -> None: ...      # AC-1.4
def test_the_body_limit_guard_fails_when_one_number_moves_alone() -> None: ...              # AC-1.4
#   edited copies: 51m vs 52_428_800; 50m vs 60 MiB; 200m vs 209_715_200 (over Cloudflare)
async def test_an_arabic_file_name_never_reaches_the_signed_path() -> None: ...              # AC-1.8
#   real MinioStorage + create_minio_signing_client(public_endpoint="api.aizzak.test",
#   public_secure=True), keys "ak"/"sk", no network (region pinned); register
#   "تقرير الربع الأوّل.pdf" through _transfers(); path == f"/workspace-files/{ws}/{file.id}",
#   url.isascii(), no "pdf" in the path, nothing Arabic after unquote, and the path starts
#   with the `^~` prefix parsed from app-locations.conf
def test_the_two_files_locations_differ_exactly_where_the_owner_decided() -> None: ...      # AC-3.2
def test_the_drift_guard_fails_when_either_side_is_unified() -> None: ...                   # AC-3.2
#   edited copies: Compose Host -> $minio_signed_host; Compose without X-Forwarded-Host;
#   RunPod with Compose's limit_except; Compose client_max_body_size 60m; Compose + add_header
def test_minio_is_published_on_loopback_only() -> None: ...                                  # AC-5.5
def test_the_loopback_guard_fails_on_a_port_published_everywhere() -> None: ...             # AC-5.5
#   "${HOST_PORT_MINIO:-19000}:9000", "0.0.0.0:19000:9000", {"target": 9000, "published": 19000}
def test_the_route_names_the_bucket_compose_and_env_example_default_to() -> None: ...       # AC-6.4
def test_the_bucket_guard_fails_when_one_name_moves_alone() -> None: ...                    # AC-6.4
def test_the_files_route_has_the_decided_shape() -> None: ...                               # FR-2,3,4,8,9,10
def test_the_files_route_guard_fails_on_each_regression() -> None: ...
#   one edited copy per rule in files_route_problems, each named by it
```

```python
# tests/unit/test_stack_smoke_roundtrip.py -- loads deploy/smoke/stack_smoke.py with
# importlib.util.spec_from_file_location (it is not a package); no network: httpx.MockTransport.
class _Probe:            # ProbeStorage fake: URLs end "?X-Amz-Signature=sekret-put|get",
    ...                  # `stored` is what the mock PUT wrote, `deleted` records every delete

def test_the_round_trip_runs_on_runpod_or_on_request_and_is_skipped_otherwise() -> None: ...  # AC-4.1
#   parametrized: {} -> None; {"RUNPOD_POD_ID": "x"} -> runs; {"SMOKE_PUBLIC_ROUNDTRIP": "1"}
#   -> runs; both -> runs; {"SMOKE_PUBLIC_ROUNDTRIP": ""} -> None
def test_an_opt_in_value_other_than_1_stops_with_a_message() -> None: ...                    # AC-4.1
async def test_a_skipped_round_trip_prints_today_s_line_and_sends_nothing() -> None: ...     # AC-4.1
#   SKIPPED_LINE == "[4] presigned PUT round trip: skipped (not on RunPod)"; no request, no presign
async def test_runpod_and_the_opt_in_both_upload_then_download() -> None: ...               # AC-4.1
#   check_public_roundtrip for each: the transport saw PUT then GET on the public URLs
def test_install_sh_runs_the_smoke_without_the_opt_in() -> None: ...                         # AC-4.2
#   install.sh holds "exec -T app python /app/deploy/smoke/stack_smoke.py" and never
#   "SMOKE_PUBLIC_ROUNDTRIP"
async def test_a_clean_round_trip_passes_and_deletes_the_probe() -> None: ...                # AC-4.3
async def test_different_bytes_back_fail() -> None: ...                                      # AC-4.3
@pytest.mark.parametrize(...)   # 403 + <Code>SignatureDoesNotMatch</Code>, 413 nginx page,
async def test_a_refused_put_names_its_status(...) -> None: ...                              # AC-4.3
#   526 Cloudflare page, 403 + cf-mitigated: challenge, 404 problem+json
async def test_a_refused_get_names_its_status() -> None: ...                                 # AC-4.3
async def test_an_unreachable_public_name_fails_with_the_error_class() -> None: ...         # AC-4.3
#   MockTransport raising httpx.ConnectError
async def test_no_line_or_message_carries_the_signature() -> None: ...                       # AC-4.3
#   every case: "sekret" absent from the SystemExit message and from captured stdout,
#   and _Probe.deleted == [_PROBE_KEY] exactly once
```

```python
# tests/unit/test_public_file_links_docs.py -- text checks in the style of
# test_cloudflare_tls_docs.py; each phrase list is also shown failing on an edited copy.
def section_five(doc: str) -> str: ...          # "## 5) " up to the next "\n## "
def subsection(text: str, number: str) -> str: ...
_FIVE = ["/workspace-files/", "MINIO_PUBLIC_ENDPOINT", "SignatureDoesNotMatch", "`413`",
         "`502`", "`526`", "50 م.ب", "100 م.ب", "MINIO_BUCKET", "upload_url", "download_url",
         "SMOKE_PUBLIC_ROUNDTRIP=1"]
_STALE_NGINX = ("Uploads go DIRECTLY to MinIO",
                "no request body of consequence passes through here")
_STALE_SMOKE = "On Compose the public address is the operator's"
_PLAN_62 = ["app-locations.conf", "$http_host", "بلا خريطة", "install.sh", "5.2"]
_RULE_1 = ["استثناءٌ ضيّق رابع", "/workspace-files/", "Q‑7"]

def test_the_two_new_gaps_are_rows_of_the_one_security_table() -> None: ...       # AC-5.6
#   ث‑16 and ث‑17 once each, count("|") == 5; ث‑16 has MinIO, GET, HEAD, PUT,
#   "الموجة 8"; ث‑17 has Content-Type, response-content-disposition, staging
def test_the_guide_has_a_6_2_section_with_its_checks_and_faults() -> None: ...     # AC-6.1, AC-6.4
#   _FIVE in section five, plus "يكسر" next to MINIO_BUCKET; the doc keeps its rtl wrapper
def test_section_3_8_no_longer_lists_6_2_as_not_handled() -> None: ...             # AC-6.1
def test_the_cloudflare_timeouts_are_125_and_30_seconds() -> None: ...             # AC-6.1
#   4.7 + 4.8 hold "125" and "30" and no "100 ثانية"
def test_the_two_stale_comments_are_gone() -> None: ...                            # AC-6.2
def test_the_plan_s_6_2_text_and_rule_1_say_what_was_built() -> None: ...          # AC-6.3
def test_each_phrase_check_fails_on_an_edited_copy() -> None: ...
```

### 5.3 العُدّة المؤقّتة (`tests/unit/test_public_file_links_containers.py`)

**لماذا ملفٌّ في المستودع لا نصٌّ في scratch كما في 6.1:**
- اثنا عشر معياراً تقوم عليها، وفي 6.1 كانت معيارين.
- ويُعاد تشغيلها قبل 7.3 وبعد أيّ تعديلٍ في المسار.
- **وهي لا تعمل إلّا بطلبٍ صريح** للسبب نفسه في `live_edge`: نحو دقيقة، فلو عملت مع `pytest tests/unit` لكسرت حدّ NFR‑7 (5 ثوانٍ).

**البوّابة:** `pytestmark = pytest.mark.skipif(...)`، ويُتخطّى الملفّ بسببٍ مكتوب إلّا باجتماع ثلاثة شروط:
- `RUN_EDGE_FILES_TEST=1`.
- و`docker` على `PATH`.
- والصورتان موجودتان محلّيّاً (`docker image inspect`): `nginx:1.27`، وصورة `minio` كما يقرؤها من `docker-compose.yml`.

فلا علامة جديدة في `pyproject.toml`. وفي CI يظهر الملفّ «skipped» بالسبب، ولا يكلّف وقتاً.

**الطوبولوجيا:** كلّ شيءٍ بـ`docker run --rm -d --pull never --label aizzak.harness=public-file-links --label aizzak.run=<id>`:
- **الشبكة:** `docker network create --internal pfl-<id>`. لا منفذ منشور، ولا خروج إلى الإنترنت، ولا صلة بـ`aizzak_default`، ولا حجمٌ مسمّى.
- **`store`:** صورة MinIO من Compose، و`MINIO_ROOT_USER` و`MINIO_ROOT_PASSWORD` عشوائيّان لهذا التشغيل، والاسم المستعار `minio-store`. وبعد `mc ready local`: `mc alias set` ثمّ `mc mb local/workspace-files`.
- **`recorder`:** الاسم المستعار `minio`، أي الاسم الذي تحلّه الحافّة. صورة `nginx:1.27` بإعدادٍ يكتبه الاختبار:
  - `listen 9000`.
  - و`proxy_pass http://minio-store:9000;` بلا URI، فيمرّ الطلب خاماً.
  - و`proxy_set_header Host $http_host`.
  - والبثّ في الاتّجاهين، و`client_max_body_size 0`.
  - و`add_header X-Harness-Upstream minio always`.
  - وسجلّ JSON على stdout بحقول `$request_method` و`$request_uri` و`$http_host` و`$http_x_forwarded_host`.
  - **هذا «سجلّ ما يصل MinIO» في AC‑5.1…AC‑5.3.**
- **`app`:** الاسم المستعار `app`. `nginx:1.27` على 8000: `location = /health` ‏200، والباقي ‏404 بـ`add_header X-Harness-Upstream app always`.
- **`edge`:** `nginx:1.27`:
  - بـ`--memory 512m` (`NGINX_MEM` الافتراضيّ).
  - و`deploy/nginx/nginx.conf` و`app-locations.conf` من المستودع **كما هما**، مربوطين للقراءة فقط كما في Compose.
  - ومجلّد شهادةٍ ذاتيّة في `tmp_path`، يولّدها `deploy/nginx/gen-cert.sh` (`AIZZAK_CERT_DIR`) في حاويةٍ مؤقّتة `--network none --user <uid>:<gid>`، أو `openssl` المضيف.
  - والأسماء المستعارة `api.aizzak.test` و`api.aizzak.com`، داخل الشبكة المعزولة وحدها.
- **`client`:** `nginx:1.27` (فيها `curl`، كما يستعمله فحص صحّة الحافّة في Compose)، يعمل بـ`sleep infinity`. وكلّ طلبٍ يُرسل بـ`docker exec client curl -sS -k …`، بقائمة وسائط لا بصدفة.
- **التوقيع:** في عمليّة الاختبار بدالّة التطبيق نفسها:
  - `MinioStorage(create_minio_client(...), "workspace-files", create_minio_signing_client(MinioSettings(endpoint="minio:9000", public_endpoint=<الاسم>, public_secure=True), ...))`.
  - و`get_presigned_url("HEAD"|"DELETE"|…)` من minio‑py للطرق الأخرى.
  - حسابٌ محلّيّ بلا شبكة (المنطقة مثبّتة).
- **القراءة من MinIO:** `docker exec store mc cat local/workspace-files/<key>`، وتُحسب البصمة في عمليّة الاختبار. وغياب الكائن = خروج `mc stat` بغير الصفر.
- **التنظيف (finalizer):** `docker rm -f` لكلّ حاويةٍ تحمل `aizzak.run=<id>`، ثمّ `docker network rm`. وفي البدء: كنسُ حاوياتٍ بقيت من تشغيلٍ مات بالوسم `aizzak.harness`. ولا `-v`، ولا `prune`، ولا `down -v`، فالأحجام المجهولة تُحذف مع `--rm`.

**الحالات:** واحدةٌ لكلّ معيار. كلّها في §5.4، والاختبار يحمل اسمه هناك.

### 5.4 لكلّ معيار

| AC | المستوى | المكان | الطريقة |
|---|---|---|---|
| AC‑1.1 | حاوية | `test_a_signed_put_through_the_edge_stores_the_same_bytes` | `PUT` بـ1 م.ب عشوائيّ على رابطٍ لـ`api.aizzak.test` ← 200، و`mc cat` بالبصمة نفسها، والمسجِّل رأى `PUT` بـ`Host: api.aizzak.test` |
| AC‑1.2 | حاوية | `test_a_url_signed_for_another_name_is_refused_by_minio` | رابطٌ لـ`other.aizzak.test`، يُبدَّل اسم مضيفه إلى `api.aizzak.test` ← 403، وفي الجسم `SignatureDoesNotMatch`، ولا كائن |
| AC‑1.3 | حاوية | `test_the_body_limit_is_exact_at_50_mb` | 0 و52,428,800 ← 200 بحجميهما. و52,428,801 ← 413، والجسم صفحة nginx لا `<Error>`، والمسجِّل لم يرَ الطلب، ولا كائن. بـHTTP/2 (الافتراضيّ مع ALPN) وبـ`--http1.1` |
| AC‑1.4 | وحدة | `test_public_file_links_edge.py` | `test_the_body_limit_is_…` وحالاته السلبيّة |
| AC‑1.5 | حاوية | `test_a_50_mb_upload_streams_without_temp_files` | رفع 50 م.ب بـ`--limit-rate 10M` (نحو 5 ث)، مرّةً بـHTTP/2 ومرّةً بـ`--http1.1`. وطوال الرفع: `find /var/cache/nginx/client_temp -type f \| wc -l` كلّ 100 م.ث = 0. وبعده: `memory.peak` من `/sys/fs/cgroup` تحت 512 Mi، و`memory.events` بلا `oom`، و`OOMKilled=false`. والحدّ لا يُتجاوز إلّا بالقتل، فالعدّادان هما الدليل، والذروة تُكتب |
| AC‑1.6 | حاوية | `test_cors_comes_from_the_edge_once` | `OPTIONS` بالأصل `https://aizzak-agent.web.app` ← 204، وترويسة `Access-Control-Allow-Origin` واحدة بقيمته. و`PUT` موقّعٌ بالأصل نفسه ← 200 وترويسةٌ واحدة. وبالأصل `https://evil.example`: لا ترويسة في الطلبين |
| AC‑1.7 | حاوية | `test_an_expired_upload_url_is_refused` | رابطٌ مدّته ثانية، يُرسل بعد 3 ث ← 403 من MinIO (`AccessDenied`)، ولا كائن |
| AC‑1.8 | وحدة | `test_public_file_links_edge.py` | `test_an_arabic_file_name_never_reaches_the_signed_path` |
| AC‑1.9 | 7.3 | `deploy-server.md` §5.3 | الخطوات 2-5 (§5.6) |
| AC‑1.10 | 7.3 | §5.3 | الخطوة 8، ومعها NFR‑8 |
| AC‑2.1 | حاوية | `test_a_signed_get_returns_the_bytes_type_and_edge_headers` | كائن AC‑1.1 مخزَّنٌ بـ`application/pdf`. `GET` موقّع ← 200، والبصمة نفسها، و`Content-Type: application/pdf`، والترويسات الأمنيّة الثلاث **مرّةً واحدة لكلٍّ منها**. **وفحصٌ إضافيّ (FR‑8، قرار 6.1):** لا `Strict-Transport-Security` لـ`api.aizzak.test`، وواحدةٌ قيمتها `max-age=31536000` بلا `includeSubDomains` لرابطٍ موقّعٍ لـ`api.aizzak.com`. وتُكتب في `test-plan.md` ترويسات MinIO كما رآها المسجِّل |
| AC‑2.2 | 7.3 | §5.3 | الخطوة 6 |
| AC‑2.3 | 7.3 | §5.3 | الخطوة 7 |
| AC‑2.4 | Cloudflare (المنسّق) | `status.md` | §3.5، البنود 1-8 |
| AC‑3.1 | المكدّس المحلّيّ | `test-plan.md` | §5.5 |
| AC‑3.2 | وحدة | `test_public_file_links_edge.py` و`test_edge_capacity.py` | `test_the_two_files_locations_differ_…` وحالاته السلبيّة، والحارسان المعدّلان (§5.1). و`git diff master -- deploy/runpod/` فارغ، يكتبه QA |
| AC‑3.3 | المكدّس المحلّيّ | `test-plan.md` | §5.5 |
| AC‑3.4 | حاوية | `test_the_edge_boots_and_recovers_without_minio` | لا يعتمد على ترتيب الاختبارات: يُزال المسجِّل، ثمّ `docker restart edge` ← تقلع، و`/health` بـ200، و`/workspace-files/<مفتاح>` بـ502 خلال 3 ث أو أقلّ. ثمّ يُنشأ المسجِّل من جديد بعنوانٍ آخر (`--ip` مختلف ضمن شبكة العُدّة) ← أوّل ردٍّ من MinIO (403 لطلبٍ غير موقّع) خلال 10 ث أو أقلّ، دون إعادة الحافّة |
| AC‑4.1 | وحدة | `test_stack_smoke_roundtrip.py` | الحالات الثلاث، وسطر التخطّي بنصّه، وPUT ثمّ GET في الحالتين العاملتين |
| AC‑4.2 | وحدة | `test_stack_smoke_roundtrip.py` + `test_install_sh.py` كما هو | `test_install_sh_runs_the_smoke_without_the_opt_in`، و`test_install_sh.py:1386` أخضر. و`git diff master -- deploy/release/` فارغ، يكتبه QA |
| AC‑4.3 | وحدة | `test_stack_smoke_roundtrip.py` | الحالات بـ`httpx.MockTransport`، والحذف مرّةً واحدة في كلّ حالة |
| AC‑4.4 | 7.3 | §5.3 | الخطوة 1 |
| AC‑5.1 | حاوية | `test_other_buckets_minio_paths_and_traversal_never_reach_minio` | الطلبات الستّ في المعيار (التمريرة بـ`--path-as-is`) ← ردٌّ يحمل `X-Harness-Upstream: app`، ولا سطر لها في المسجِّل. **وحالةٌ إضافيّة (§3.2.6):** الخام `/aizzak-backups/../workspace-files/x` و`/aizzak-backups%2F..%2Fworkspace-files/x` (بـ`--path-as-is`) ← لا 2xx، ولا شيء من دلوٍ آخر. ورمز MinIO يُكتب |
| AC‑5.2 | حاوية | `test_only_get_head_and_put_on_an_object_reach_minio` | `DELETE` و`POST …?uploads` **موقّعان بمفتاحٍ صالح** ← 403 من الحافّة، والكائن باقٍ. و`GET /workspace-files/` و`PUT /workspace-files/?policy` ← 404 من الحافّة. ولا شيء منها في المسجِّل. ثمّ `PUT` و`GET` و`HEAD` موقّعة على `smoke/presign-put-probe.txt` ← 200، والثلاثة في المسجِّل |
| AC‑5.3 | حاوية | `test_the_host_minio_checks_is_the_client_s_own` | الطلبان بـ`X-Forwarded-Host: evil.example`: الموقّع لـ`api.aizzak.test` ← 200، والموقّع لـ`evil.example` ← 403. وفي المسجِّل `Host` هو `api.aizzak.test`، و`X-Forwarded-Host` فارغ |
| AC‑5.4 | حاوية | `test_the_access_log_never_holds_a_signature` | 20 طلباً موقّعاً (10 `PUT` و10 `GET`)، ثمّ `docker logs edge`. أسطر سجلّ الوصول (stdout، `"logger":"nginx.access"`): صفرٌ فيها `X-Amz-Signature` أو `X-Amz-Credential`، ولا `?` في `path`. **وسجلّ الأخطاء (stderr):** عدد الأسطر التي فيها `X-Amz-Signature` بعد 413 AC‑1.3 و502 AC‑3.4 **يُطبع ولا يُشترط** (ث‑16)، ويكتبه QA |
| AC‑5.5 | وحدة | `test_public_file_links_edge.py` | `test_minio_is_published_on_loopback_only` وحالاته السلبيّة |
| AC‑5.6 | وحدة/نصّ | `test_public_file_links_docs.py` | صفّا ث‑16 وث‑17، والاتّصال يحرسه `test_gpu_preflight.py:690` |
| AC‑5.7 | 7.3 | §5.3 | الخطوة 9 |
| AC‑6.1 | نصّ | `test_public_file_links_docs.py` | §5 و§3.8 و§4.7/§4.8 |
| AC‑6.2 | نصّ | `test_public_file_links_docs.py` | `test_the_two_stale_comments_are_gone` |
| AC‑6.3 | نصّ | `test_public_file_links_docs.py` (+ حارس الملخّص القائم عند الإغلاق) | نصّ 6.2 والقاعدة 1 |
| AC‑6.4 | وحدة + نصّ | `test_public_file_links_edge.py` و`test_public_file_links_docs.py` | ربط الأسماء، وجملة «يكسر» في §5 |

**الحصيلة:** 33، وتطابق `stories.md`:

| أين يُثبت | العدد | المعايير |
|---|---|---|
| [مستودع] حاوية مؤقّتة | 12 | AC‑1.1، 1.2، 1.3، 1.5، 1.6، 1.7، 2.1، 3.4، 5.1، 5.2، 5.3، 5.4 |
| [مستودع] وحدة/نصّ | 12 | AC‑1.4، 1.8، 3.2، 4.1، 4.2، 4.3، 5.5، 5.6، 6.1، 6.2، 6.3، 6.4 |
| [مستودع] المكدّس المحلّيّ | 2 | AC‑3.1، 3.3 |
| [Cloudflare] قراءةٌ للمنسّق | 1 | AC‑2.4 |
| [خادم 7.3] | 6 | AC‑1.9، 1.10، 2.2، 2.3، 4.4، 5.7 |

والمجموع 12 + 12 + 2 + 1 + 6 = 33. ومعه NFR‑4 في AC‑3.4، وNFR‑5 في AC‑1.5، وNFR‑6 في AC‑5.4.

### 5.5 فحوص المكدّس المحلّيّ (QA، تُكتب في `test-plan.md`)

**AC‑3.1:**
- على `master` ثمّ على الفرع: `docker compose config --hash '*' > <scratch>/hash-<ref>.txt`، ثمّ `cmp` للملفّين.
- ثمّ `git diff master --stat -- 'docker-compose*.yml' .env.example .env.test.example deploy/release/` فارغ.
- `config --hash` يطبع البصمات وحدها، فلا يظهر سرّ.

**AC‑3.3:**
1. `docker compose restart nginx`، فالملفّان مربوطان ملفّاً، والحاوية الجارية ترى النصّ القديم بعد `git checkout`. ثمّ انتظار `healthy`.
2. `curl -fsS http://127.0.0.1/health` و`curl -fsSk https://127.0.0.1/health` ← 200. و`/metrics` و`/health/drain` على 80 و443 ← 404.
3. `docker compose exec -T app python /app/deploy/smoke/stack_smoke.py`، أي نسخة الصورة (`master`)، ثمّ **نسخة الفرع:** `docker compose exec -T app python - < deploy/smoke/stack_smoke.py`. وفي المخرجين: `[3] … public=localhost:19000`، و`[4] presigned PUT round trip: skipped (not on RunPod)`، و`PASS`.
   - **ما يلمسه التشغيلان من المكدّس الحيّ للقراءة فقط:** `SELECT` عبر PgBouncer، وقراءة `secret/data/minio` من Vault، وتوقيعٌ محلّيّ.
   - و`[4]` يُتخطّى، فلا كتابة.
4. **Q‑7، للمعلومة:**
   - `curl -sk -o /dev/null -w '%{http_code}\n' https://127.0.0.1/workspace-files/x` ← 403 (XML من MinIO).
   - و`-X DELETE` ← 403 (nginx).
   - و`https://127.0.0.1/workspace-files/` ← 404 (nginx).
   - كلّها بلا توقيع، فلا أثر.

### 5.6 خطوات 7.3 (تُكتب في `deploy-server.md` §5.3 بهذا الترتيب)

**الخطوة 1 (AC‑4.4)، على الخادم من مجلّد الحزمة:**

```bash
sudo docker compose exec -T -e SMOKE_PUBLIC_ROUNDTRIP=1 app python /app/deploy/smoke/stack_smoke.py
```

المتوقّع:
- `[3] … public=api.aizzak.com`.
- ثمّ `    [4] runs: SMOKE_PUBLIC_ROUNDTRIP=1`.
- ثمّ سطرا `[4] … OK`.
- ثمّ `PASS: …`.
- ولا يبقى `smoke/presign-put-probe.txt` في الدلو.

**الخطوات 2-10، من جهاز المالك** (bash، و`curl` و`jq` و`sha256sum` و`nc`، وتوكن Firebase لمستخدمٍ على مشروع الخادم، صالحٌ ساعة):

```bash
API=https://api.aizzak.com
HDR=$(mktemp) && chmod 600 "$HDR"
read -rs TOKEN && printf 'Authorization: Bearer %s\n' "$TOKEN" > "$HDR" && unset TOKEN   # never in argv (§4.1)
SPACE=$(curl -fsS -H @"$HDR" "$API/api/v1/spaces" | jq -r '.data[0].id')
# 2) register
head -c 1048576 /dev/urandom > probe.bin && SUM=$(sha256sum probe.bin | cut -d' ' -f1)
REG=$(curl -fsS -H @"$HDR" -H 'Content-Type: application/json' \
  -d "{\"space_id\":\"$SPACE\",\"name\":\"probe-6.2.txt\",\"content_type\":\"text/plain\",\"size_bytes\":1048576}" \
  "$API/api/v1/files")
ID=$(jq -r .file_id <<<"$REG"); UP=$(jq -r .upload_url <<<"$REG")
echo "${UP%%\?*}"                                    # must start https://api.aizzak.com/workspace-files/
# 3) upload -- the signed URL is the credential: no Authorization header here
curl -sS -o /dev/null -w '%{http_code}\n' -X PUT -H 'Content-Type: text/plain' --data-binary @probe.bin "$UP"   # 200
# 4) complete
curl -fsS -H @"$HDR" -H 'Content-Type: application/json' -d "{\"checksum\":\"$SUM\"}" \
  "$API/api/v1/files/$ID/complete" | jq -r .status
# 5) read
DL=$(curl -fsS -H @"$HDR" "$API/api/v1/files/$ID" | jq -r 'select(.status=="ready") | .download_url')
# 6) download twice: same sha256, cf-cache-status never HIT
for i in 1 2; do curl -sS -D "h$i.txt" -o "back$i.bin" "$DL"; sha256sum "back$i.bin"; grep -i '^cf-cache-status' "h$i.txt"; done
# 7) after the 300 s lifetime: 403 with an XML body from MinIO, not a cached 200
sleep 300; curl -sS -w '\n%{http_code}\n' "$DL"
```

- **الخطوة 8 (AC‑1.10، NFR‑8):**
  - ملفٌّ بـ52,428,800 بايت بالتسلسل نفسه، و`-w '%{http_code} %{time_total}\n'` على الـ`PUT`: 200 لا 413 ولا `524`، والزمن يُكتب.
  - ثمّ 20 رحلة بملفّ 1 م.ب، ووسيط `time_total` للرفع وللتنزيل.
- **الخطوة 9 (AC‑5.7):**
  - `nc -zv -w 5 <عنوان الخادم> 19000` و`… 19001`: يفشلان.
  - و`curl -sS -i "$API/aizzak-backups/x" | head -5`: ‏404 بـ`application/problem+json` من التطبيق، لا XML.
- **الخطوة 10 (التنظيف):**
  - `curl -fsS -X DELETE -H @"$HDR" "$API/api/v1/files/<id>"` لكلّ ملف مسبار.
  - ثمّ `rm -f "$HDR" probe.bin big.bin back*.bin h*.txt`.
- **وA‑9:** يؤكّد المالك في الخطوة نفسها أنّ الواجهة لا ترسل في الرفع غير `Content-Type`، ولا `Authorization`.

### 5.7 الزمن (NFR‑7: ≤ 5 ث زيادة)

**تقدير** (لم يُقَس، ويقيسه QA بـ`pytest tests/unit --durations=0` قبل الفرع وبعده):

| ما يُضاف | الزمن التقديريّ |
|---|---|
| تحليل الحافّتين وCompose (`test_public_file_links_edge.py` دون AC‑1.8) | نحو 0.3 ث |
| AC‑1.8: HMAC محلّيّ وتسجيلٌ على الأشباه | أقلّ من 0.2 ث |
| تحميل `stack_smoke.py` وأحد عشر اختباراً بـ`MockTransport` | نحو 0.5 ث، ومعظم الوحدات المستوردة محمّلةٌ أصلاً في الجلسة |
| الوثائق | أقلّ من 0.2 ث |
| العُدّة بلا طلبٍ صريح | نحو 0، لأنّها تُتخطّى |
| **المجموع** | **نحو 1.2 ث** |

والعُدّة بطلبٍ صريح نحو دقيقة، خارج الحدّ، كما `live_edge`.

## 6. ترتيب التنفيذ والتوازي

الفرع `feat/public-file-links` من `master` (`f1c8bda`). كاتبٌ واحد، فالتنفيذ متتابع.

**بوّابات كلّ commit:** `ruff format --check .`، و`ruff check .`، و`mypy src`، و`lint-imports`، و`pytest -rs tests/unit tests/architecture tests/eval`:
- لا `tests/integration` بلا موافقة بشريّة (`CLAUDE.md`).
- و`deploy/prometheus/test-rules.sh` لا يلزم، فلا تنبيه.
- وفي ذيل كلّ رسالة سطور الإسناد التي تحدّدها الجلسة.

| # | Commit | الملفّات | يعتمد على |
|---|---|---|---|
| C1 | `feat(edge): route presigned file URLs to MinIO on the Compose edge` | `app-locations.conf` (§3.2)، و`nginx.conf` (§3.3)، و`test_public_file_links_edge.py`، و`test_edge_capacity.py` (§5.1) | — |
| C2 | `test(edge): opt-in container harness for the public file route` | `test_public_file_links_containers.py` | C1 |
| C3 | `feat(smoke): opt-in public round trip with a download in check [4]` | `stack_smoke.py` (§3.4)، و`test_stack_smoke_roundtrip.py` | — |
| C4 | `docs(public-file-links): guide section 5, two security rows, Cloudflare timeouts and the 6.2 plan text` | `deploy-server.md`، والخطّة (§3 و§5 دون اللوحة)، و`deploy-runpod.md:390`، و`08` السطر 17، و`test_public_file_links_docs.py` | C1…C3 (النصوص المختبرة) |

**بعد C2:** يشغّل المطوّر العُدّة مرّةً بـ`RUN_EDGE_FILES_TEST=1`، ويصحّح ما تجده، كتعليقٍ يصف ترويسة MinIO كما قيست.

**بالتوازي مع C1…C4:** المنسّق ينفّذ قراءات §3.5 (AC‑2.4) ويكتبها في `status.md`. ولا تحجز البناء إلّا إن خالفت المتوقّع، فتُعرض على المالك قبل DNS.

**بعد C4:**
1. QA: البوّابات، و`--durations`، والعُدّة (§5.3)، وفحوص المكدّس المحلّيّ (§5.5)، و`git diff` للمعايير AC‑3.1 وAC‑3.2 وAC‑4.2.
2. ثمّ مراجعة الشيفرة والمراجعة الأمنيّة.
3. ثمّ PR وبوابة ②.
4. **commit الإغلاق بعد الدمج** (`docs(server-gpu-plan): mark step 6.2 merged`): صفّ 6.2 وسطر الرأس في الخطّة، و`server-gpu-summary.html`، كما في 6.1.
5. ثمّ وسم إصدارٍ (حزمةٌ وصورة)، وبعده 7.3 (§5.6).

## 7. المخاطر والقرارات

### بحاجةٍ إلى قرارٍ بشريّ قبل البناء

**لا بند ⚠️:**
- الملفّات المحميّة الثلاثة كلّها في Q‑6، ولا شيء خارجها.
- وكلّ ما يمسّ الأمن أو حدّ الثقة إمّا حُسم في بوابة ①: Q‑1 وQ‑2 وQ‑3 وQ‑7.
- وإمّا يُسجَّل في قائمة الأمان بقاعدة الخطّة 8 وقرار المالك (2026‑10‑06): «سجّل الثغرة ولا تحجز staging»، وهو ث‑16.

### قراراتٌ اتّخذها التصميم (تُراجَع في المراجعة)

1. **`X-Forwarded-Host ""`:** الحافّة لا تقرأ الترويسة ولا تمرّرها، فـQ‑1 (ب) مفروضٌ في الإعداد لا في سلوك MinIO. والثمن سطرٌ وفرقٌ آخر عن RunPod، يحرسه اختبار الانجراف.
2. **إخفاء `Strict-Transport-Security` و`X-Content-Type-Options` من MinIO.** هذا يحفظ قرار 6.1: HSTS لاسم الـAPI وحده، بلا `includeSubDomains`، ولا شيء لـ`localhost`. ويجعل كلّ ترويسةٍ مرّةً واحدة. وهو صحيحٌ أرسلها MinIO أم لا.
3. **403 لا 405 للطرق المرفوضة** (`limit_except`)، لأنّ 405 الصحيح يحتاج `add_header Allow`، فيمحو الترويسات الموروثة. والمعيار يقبل الاثنين.
4. **جذر الدلو موقعٌ مطابقٌ تامّ** (`= /workspace-files/`) لا `if` داخل البادئة.
5. **المهلتان 300 ث كما في RunPod:** خلف Cloudflare تسبقهما مهلتاه، وتغييرهما فرقٌ بلا قياس.
6. **سجلّ الأخطاء لا يُكتم.** سطر nginx لطلبٍ فاشل يحمل الرابط الموقّع. فـ`error_log … crit` داخل الموقع يُخفي 502 و413 والمهل، وهي ما يحتاجه من يحقّق في الأعطال. فيُكتب في ث‑16، ويقيس AC‑5.4 عدد الأسطر ولا يحجز عليه.
7. **العُدّة في المستودع، ولا تعمل إلّا بطلبٍ صريح** (`RUN_EDGE_FILES_TEST=1`). ومعها مسجِّلٌ بين الحافّة وMinIO يثبت ما يصل MinIO فعلاً: الطريقة، والمسار الخام، و`Host`، و`X-Forwarded-Host`. والملفّا nginx كما هما في المستودع، لا يُعدَّلان للعُدّة.
8. **`SMOKE_PUBLIC_ROUNDTRIP` يقبل `1` وحده،** وأيّ قيمةٍ أخرى غير فارغة تُخرج بـ1 ورسالة. فلا يقرأ مشغّلٌ كتب `=true` سطر التخطّي نجاحاً.
9. **`[4]` يُبقي القراءة الداخليّة قبل التنزيل العامّ،** فيفرّق «لم يُخزَّن شيء» من «التنزيل مكسور». ويُبقي سطر نجاح الرفع بنصّه، لأنّ `deploy-runpod.md:390` يقتبسه، ويضيف سطراً للتنزيل.
10. **سطرٌ في `08-local-runbook.md` §2:** الوثيقة المُلزِمة تقول ما تفعله الحافّة الآن.

### مخاطر

- **R‑4، خادمٌ بلا إصدارٍ جديد:**
  - ملفّا nginx يصلان بالحزمة، و`stack_smoke.py` بالصورة.
  - فخادمٌ ثُبّت قبل 6.2 يردّ على الرابط بـ404 من التطبيق، و`failure_hint` يقول ذلك.
  - ويُثبَّت 7.3 من إصدارٍ بعد الدمج (§5.6). والدليل §5.5 يقول كيف.
- **ترويسات MinIO لم تُقس هنا** (المسبار انتهت مهلته). المسار صحيحٌ في الحالين، والعُدّة تكتب ما رأته.
  - **وإن ثبت أنّ MinIO يرسل HSTS بـ`includeSubDomains`، فحافّة RunPod تمرّره اليوم** إلى `<POD_ID>-80.proxy.runpod.net/workspace-files/…`، خلاف تعليقها نفسه (`deploy/runpod/nginx.conf:241-249`).
  - لا يُصلح هنا، فـ`deploy/runpod/` خارج الخطّة (§2). ويُسجّله المنسّق ملاحظةً لـRunPod.
- **فرقٌ بين المسار المطبَّع والمسار الخام** (`$uri` للمطابقة و`$request_uri` لـMinIO):
  - يغلقه رفض MinIO لأيّ `..`، وأنّ كلّ طلبٍ يحتاج توقيعاً على مساره بالضبط.
  - والعُدّة تجرّب حالتين خامّتين (AC‑5.1). وإن ردّ MinIO بغير الرفض، فالعلاج سطر `if ($request_uri !~ "^/workspace-files/")` يُعرض قبل الدمج.
- **R‑2 وR‑3:** مهلتا Cloudflare (125 ث و30 ث)، وBot Fight Mode وBrowser Integrity Check على `PUT` من httpx وcurl. تُقرأ إعداداتها في §3.5، وتُقاس في 7.3. و`failure_hint` يسمّي `cf-mitigated`.
- **R‑7 وR‑8:** سطح اعتماد MinIO ومحتوى المستخدم على أصل الـAPI: ث‑16 وث‑17، والموجة 8.
- **R‑9:** الأصل يُبلَغ مباشرةً حتّى 6.3 (ث‑14)، ومعه هذا المسار. ويبقى حدّ الجسم والمعدّل في nginx.
- **A‑9:**
  - إن أرسلت الواجهة في الرفع ترويسةً غير `Content-Type`، رفض المتصفّح الطلب التمهيديّ.
  - وإن أرسلت `Authorization` مع الرابط، رفضه MinIO.
  - يؤكّده المالك في 7.3. والعلاج تعديلٌ في الواجهة، أو في `Access-Control-Allow-Headers` (`SEC‑08`)، وهو قرارٌ منفصل.
- **CI لا يشغّل العُدّة.** فانحرافٌ في سلوك nginx لا تمسكه في CI إلّا الحرّاس البنيويّة (§5.2). والعُدّة تُعاد بيدٍ قبل 7.3 وبعد أيّ تعديلٍ في المسار.
- **جهاز التطوير (Q‑7):** دلو التطبيق صار يُبلَغ عبر 80 و443 على كلّ الواجهات، بتوقيعٍ صالح وحده. مقبولٌ ومكتوب في القاعدة 1.

</div>
