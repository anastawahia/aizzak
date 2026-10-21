<div dir="rtl">

# التصميم التقني: شهادةٌ حقيقيّة عبر Cloudflare (الخطوة 6.1 من خطّة خادم GPU)

> **المدخلات:** [`requirements.md`](requirements.md) و[`stories.md`](stories.md) بعد بوابة ① (2026‑10‑20)، و[`status.md`](status.md) «قرارات وتصعيدات». النطاق US‑1…US‑7 (39 معياراً)، بالطريقة (أ) والاسم `api.aizzak.com` (Q‑1، Q‑2)، وبلا تجربة قبل 7.3 (Q‑5 (أ)).
>
> **ما قيس هنا، وكيف (2026‑10‑20/21، على هذا الجهاز، ولم يُلمس المكدّس الحيّ):**
> - حاوية `nginx:1.27` مؤقّتة (`docker run --rm --network none`، الصورة `sha256:6784fb08…`) بنسختين من `nginx.conf` في scratch: نسخة `master`، ونسخة الفرع المقترحة في §3.2. وشهادةٌ ذاتيّة ولّدتها الحاوية نفسها، وخلفيّةٌ صغيرة على `127.0.0.1:8000` تعيد `X-Real-IP` و`X-Forwarded-For`.
> - `openssl` 3.0.13 على المضيف و3.0.16 في الصورة، على شهاداتٍ تجريبيّة في scratch (CA تجريبيّ يحاكي اسم مُصدِر Origin CA).
> - الاختبارات القائمة التي تقرأ `nginx.conf` شُغّلت في الذاكرة على نسخة الفرع (§5.1).
> - **لم يُقرأ** `deploy/nginx/certs/*` ولا `.env`، ولم يُتّصل بـCloudflare (لا يصلها غير المنسّق).

## 1. الملخّص

الميزة كلّها **عند الحافّة وخارج `src/`**: nginx (`deploy/nginx/`)، وسطرٌ في تقرير `install.sh`، وإعدادات منطقة Cloudflare، ووثائق. لا مسار HTTP ولا حدث ولا مخطّط ولا هجرة، ولا تُلمس طبقات `.importlinter`. والتطبيق لا يقرأ عنوان العميل ولا `X-Forwarded-*` (`requirements.md` §5.2، A‑7)، فلا يتغيّر فيه شيء.

| المكوّن | التغيير | الدور |
|---|---|---|
| `deploy/nginx/nginx.conf` | كتلة `realip` في سياق `http`، وسطرٌ في خريطة HSTS، وتعليقات | عنوان العميل الحقيقيّ خلف Cloudflare لنطاقاته وحدها (FR‑1…FR‑3)، وHSTS لـ`api.aizzak.com` وحده (FR‑16، FR‑17)، وإصلاح المرجع المعلّق (FR‑22) |
| `deploy/nginx/gen-cert.sh` | قاعدة «الوجود لا المحتوى» | لا يكتب فوق نصف زوجٍ وضعه المشغّل (FR‑7) |
| `deploy/release/install.sh` | دالّةٌ وسطرٌ في التقرير | حال الشهادة في التقرير، بلا خيارٍ ولا رمز خروجٍ جديد (FR‑18، Q‑7) |
| منطقة `aizzak.com` في Cloudflare | ثلاث قواعد لاسم الـAPI وأدنى TLS 1.2 (الآن)، وسجلّ DNS وشهادة منشأ (7.3) | §3.5. ينفّذها المنسّق، وكلّ بندٍ بموافقة المالك |
| الوثائق | قسمٌ جديد `## 4)` في `docs/deploy-server.md`، وثلاث ثغرات، والخطّة والملخّص، وجملتان مُلزِمتان | US‑6 |

**التدفّق على الخادم بعد 7.3:** الزائر ← Cloudflare (شهادة Universal SSL العامّة، وأدنى TLS 1.2، وتحويل HTTP، ولا تخزين) ← الأصل على 443 بوضع Full (strict) ← nginx بشهادة المنشأ، ويأخذ عنوان العميل من `CF-Connecting-IP` لأنّ النظير من نطاقات Cloudflare ← `app`.

**المكدّس المحلّيّ لا يتغيّر (القاعدة 1)،** لأربعة أسباب مقيسة (§3.2.5): لا ملفّ Compose ولا `.env.example` ولا ملفّ أحجام يتغيّر، فلا تتغيّر بصمة `config --hash` لأيّ خدمة. والثقة بـ`CF-Connecting-IP` لا تعمل إلّا لنظيرٍ من نطاقات Cloudflare، ولا يصل جهاز التطوير نظيرٌ منها. وسطر HSTS لا يطابق إلّا `Host: api.aizzak.com`. و`gen-cert.sh` يسلك المسار نفسه حين يجد الملفّين معاً.

## 2. الملفّات

المنفّذ واحد: **`backend-developer`** لكلّ الملفّات، فلا يكتب في المستودع إلّا كاتبٌ واحد. **تُتخطّى:** مرحلة `data-engineer` (لا جدول ولا هجرة، §4)، و`frontend-developer` وUX (لا واجهة في المستودع). ويكتب `qa-engineer` ملفّ `test-plan.md` وفحوص الحاوية (§5.3).

| المسار | إنشاء/تعديل | المسؤوليّة | المنفّذ |
|---|---|---|---|
| `deploy/nginx/nginx.conf` | تعديل | كتلة `realip` قبل قسم «Rate limiting» (§3.2.1)، وجملةٌ في تعليق حدّ المعدّل، وتعليق HSTS وسطر `api.aizzak.com` (§3.2.3)، وتعليق `:80` (§3.2.4) | backend-developer |
| `deploy/nginx/gen-cert.sh` | تعديل | قاعدة الوجود، و`AIZZAK_CERT_DIR`، والكتابة بأسماءٍ مؤقّتة (§3.3) | backend-developer |
| `deploy/release/install.sh` | تعديل | `tls_state()` وسطرٌ واحد في `report()` (§3.4) | backend-developer |
| `tests/unit/test_edge_cloudflare.py` | إنشاء | AC‑1.1، AC‑4.1، ونصف NFR‑8 الوحديّ، وحرّاسٌ تثبت أنّها تفشل | backend-developer |
| `tests/unit/test_gen_cert.py` | إنشاء | AC‑2.2، AC‑2.3، AC‑2.6، وسلوك التوليد اليوم | backend-developer |
| `tests/unit/test_install_sh_tls.py` | إنشاء | AC‑5.1…AC‑5.3، وحالات الحافّة | backend-developer |
| `tests/unit/test_cloudflare_tls_docs.py` | إنشاء | AC‑2.1، AC‑4.4، AC‑6.1…AC‑6.5، AC‑7.3 | backend-developer |
| `tests/unit/test_install_bundle_docs.py` | تعديل | `test_the_bar_check_fails_when_a_count_drifts` وحده: يُفسد الرقم الحاليّ لا الرقم 11 الثابت (§5.1) | backend-developer |
| `docs/deploy-server.md` | تعديل | قسم `## 4) الشهادة عبر Cloudflare (6.1)` قبل «قائمة الأمان المؤجّلة»، وصفوف ث‑13…ث‑15، ورأس الوثيقة، و§3.4 الخطوة 8، و§3.8 (§3.6) | backend-developer |
| `docs/server-gpu-plan.md` | تعديل | صفّ 6.1 في اللوحة، ونصّ 6.1 وقبوله، وجواب قرار 4، وسطر الحالة في الرأس | backend-developer |
| `docs/server-gpu-summary.html` | تعديل | الشريط والمفتاح، وبطاقة 6.1، وبطاقة القرارات، والتذييل | backend-developer |
| `docs/design/08-local-runbook.md` | تعديل | §3.2: جملةٌ في آخر الفقرة الأولى (السطر 876)، واستبدال المرجع في السطر 882 | backend-developer |
| `docs/design/05-rbac-config-secrets.md` | تعديل | §3: سطرٌ بعد السطر 127 | backend-developer |

**الملفّات المحميّة ثلاثة، وكلّها في قائمة Q‑12:** `deploy/nginx/nginx.conf` و`deploy/nginx/gen-cert.sh` و`deploy/release/install.sh`. **لا يُلمس:** `deploy/nginx/app-locations.conf` (جرده في §3.2.2: لا سطر يتغيّر)، و`docker-compose*.yml`، و`deploy/runpod/`، و`deploy/sizes/`، و`.env.example`، و`.gitignore` (يتجاهل `deploy/nginx/certs/` كلّه، ومنه الأسماء المؤقّتة في §3.3 و§3.5.4، مقيسٌ بـ`git check-ignore`)، و`.github/workflows/`، و`Dockerfile`.

## 3. العقود

### 3.1 الواجهات البرمجية

لا تغيير. لا مسار جديد، و`docs/design/openapi.yaml` كما هو، ولا حدث. ولا عميل API يُعاد توليده (لا واجهة في المستودع).

### 3.2 `deploy/nginx/nginx.conf`

#### 3.2.1 عنوان العميل خلف Cloudflare (FR‑1…FR‑3)

**المكان:** قسمٌ جديد في سياق `http`، **قبل السطر** `    # ── Rate limiting (P1-7, p1-hardening-plan.md §3 step 9) ───────────────` (السطر 179 اليوم)، وبعد `proxy_next_upstream_tries 2;` وسطره الفارغ. فيقرأ القارئ سبب المفتاح قبل المنطقتين اللتين تستعملانه. والتوجيهات في `http` وحده، فيرثها `:80` و`:443` معاً، ولا يعرّفها `server` ولا `location` (يحرسه AC‑1.1).

**النصّ** (التعليق بالإنجليزيّة كبقيّة الملفّ، ويجوز للمطوّر تهذيب صياغته دون معناه):

```nginx
    # ── The client's address behind Cloudflare (server-gpu-plan.md 6.1) ─────
    # On a server published through Cloudflare's proxy every TCP peer is a
    # Cloudflare edge, so `$binary_remote_addr` -- the key of both limit zones
    # below -- would be Cloudflare's address: one 200 r/s bucket and one
    # 500-socket WebSocket ceiling shared by every user behind the same edge,
    # and an access log that names Cloudflare instead of the client. The realip
    # module puts the client back: for a peer INSIDE one of the ranges below,
    # `$remote_addr` (and with it `$binary_remote_addr`, the log's
    # `remote_addr`, and the X-Real-IP / X-Forwarded-For that
    # app-locations.conf sends upstream) becomes the value of CF-Connecting-IP.
    # The TCP peer itself stays in `$realip_remote_addr`.
    #
    # ⚠️ NARROW ON PURPOSE. Only Cloudflare's PUBLISHED ranges are trusted:
    # no private, loopback or Docker range -- a same-host connection arrives
    # as the bridge gateway (172.18.0.1, see "Rate limiting" below), so
    # trusting Docker's ranges would let any container or local process pick
    # its own address. X-Forwarded-For is never read: the header is
    # CF-Connecting-IP, which Cloudflare sets to the one address it saw, and
    # `real_ip_recursive` stays off. Every other peer -- the dev box's
    # 127.0.0.1 and LAN clients, install.sh's checks, a client reaching the
    # origin directly before 6.3 -- keeps its own address whatever it sends.
    # Measured on nginx:1.27: a non-Cloudflare peer sending
    # `CF-Connecting-IP: 203.0.113.9` is logged and proxied with its own
    # address, and a malformed value from a trusted peer is ignored.
    #
    # A COPY, not a fetch at boot (cloudflare-tls Q-11). Source:
    # https://api.cloudflare.com/client/v4/ips (the list behind
    # https://www.cloudflare.com/ips/), copied 2026-10-20, etag
    # 38f79d050aa027e3be3865e495dcc9bc. A range Cloudflare adds later is not
    # trusted until this list is refreshed (docs/deploy-server.md §4), and its
    # users share one bucket until then. tests/unit/test_edge_cloudflare.py
    # checks the shape; the merge-day comparison with the API checks the content.
    set_real_ip_from 173.245.48.0/20;
    set_real_ip_from 103.21.244.0/22;
    set_real_ip_from 103.22.200.0/22;
    set_real_ip_from 103.31.4.0/22;
    set_real_ip_from 141.101.64.0/18;
    set_real_ip_from 108.162.192.0/18;
    set_real_ip_from 190.93.240.0/20;
    set_real_ip_from 188.114.96.0/20;
    set_real_ip_from 197.234.240.0/22;
    set_real_ip_from 198.41.128.0/17;
    set_real_ip_from 162.158.0.0/15;
    set_real_ip_from 104.16.0.0/13;
    set_real_ip_from 104.24.0.0/14;
    set_real_ip_from 172.64.0.0/13;
    set_real_ip_from 131.0.72.0/22;
    set_real_ip_from 2400:cb00::/32;
    set_real_ip_from 2606:4700::/32;
    set_real_ip_from 2803:f800::/32;
    set_real_ip_from 2405:b500::/32;
    set_real_ip_from 2405:8100::/32;
    set_real_ip_from 2a06:98c0::/29;
    set_real_ip_from 2c0f:f248::/32;
    real_ip_header    CF-Connecting-IP;
    real_ip_recursive off;
```

- **مصدر القائمة:** ملفّ المنسّق `/tmp/claude-1000/-home-AIZZAK/81328ec9-6c6d-4155-a4e6-26b05fb3b1f3/scratchpad/cloudflare-ips-2026-10-20.json` (15 IPv4 و7 IPv6). الأسطر أعلاه منسوخةٌ منه بترتيبه. **مقيس:** الـ22 تقبلها `ipaddress.ip_network(strict=True)`، ولا يتقاطع أيٌّ منها مع `10/8` و`172.16/12` و`192.168/16` و`127/8` و`169.254/16` و`100.64/10` و`::1/128` و`fc00::/7` و`fe80::/10`. و`172.64.0.0/13` خارج `172.16.0.0/12`.
- **`real_ip_recursive off`** مكتوبٌ صراحةً مع أنّه الافتراضيّ: يقرأ القارئ القرار ولا يستنتجه، وAC‑1.1 يمنع `on`.
- **وفي تعليق «Rate limiting» القائم** (الأسطر 191‑203) جملةٌ واحدة بعد «…regardless of hairpinning -- see app-locations.conf.»: `Behind Cloudflare the key is the client's address from CF-Connecting-IP, put back by the realip block above for Cloudflare's ranges only (6.1).`

**ما قيس في الحاوية المؤقّتة** (`nginx -t` ناجحٌ على النسختين):

| الطلب | `remote_addr` في السجلّ | `X-Real-IP` عند الخلفيّة |
|---|---|---|
| من `127.0.0.1` (ليس من Cloudflare) ومعه `CF-Connecting-IP: 203.0.113.9` و`X-Forwarded-For: 198.51.100.7` | `127.0.0.1` | `127.0.0.1` (و`X-Forwarded-For` = `198.51.100.7, 127.0.0.1`: وسمٌ لا يُقرأ) |
| **نسخة اختبارٍ تثق بـ`127.0.0.1` وحدها**، ومعه `CF-Connecting-IP: 203.0.113.9` | `203.0.113.9` | `203.0.113.9` |
| النسخة نفسها، و`2001:db8::1` | `2001:db8::1` | `2001:db8::1` |
| النسخة نفسها، و`garbage` أو قيمةٌ فارغة | `127.0.0.1` | `127.0.0.1` |
| النسخة نفسها، و`X-Forwarded-For: 203.0.113.50` وحده | `127.0.0.1` | `127.0.0.1` |
| النسخة نفسها، و`CF-Connecting-IP: 203.0.113.9, 198.51.100.7` | `198.51.100.7` (آخر عنصر) | `198.51.100.7` |

السطر الأخير سلوك nginx مع أيّ ترويسةٍ فيها قائمة، ولا يصل من Cloudflare: Cloudflare يضع في `CF-Connecting-IP` عنواناً واحداً. وهو من نظيرٍ موثوقٍ وحده، فلا يفتح تزويراً من خارج نطاقات Cloudflare.

#### 3.2.2 جرد كلّ موضعٍ يقرأ العنوان أو يمرّره

| الموضع | التوجيه | بعد الميزة | تغييرٌ في النصّ؟ |
|---|---|---|---|
| `nginx.conf:109` | `"remote_addr":"$remote_addr"` في `log_format aizzak_json` | عنوان العميل لطلبٍ من نطاقات Cloudflare، وعنوان النظير لغيره | لا |
| `nginx.conf:119` | `"forwarded_for":"$http_x_forwarded_for"` | الترويسة الواردة كما هي (Cloudflare يُلحق عنوان العميل بما أرسله العميل). وسمٌ للتحقيق، ولا يقرؤه شيءٌ ليقرّر | لا |
| `nginx.conf:191-203` | تعليق «Keyed by $binary_remote_addr» | يبقى صحيحاً، وتُضاف الجملة في §3.2.1 | جملةٌ في التعليق |
| `nginx.conf:232` | `limit_req_zone $binary_remote_addr zone=api_req:10m rate=200r/s;` | المفتاح عنوان العميل بعد `realip` (مرحلة post-read قبل preaccess) | لا. و`_LIMIT_REQ_ZONE_RE` في `test_edge_hardening.py:75` يطابقه كما هو |
| `nginx.conf:246` | `limit_conn_zone $binary_remote_addr zone=ws_conn:10m;` | كذلك | لا |
| `app-locations.conf:54` و`:229` | `proxy_set_header X-Real-IP $remote_addr;` | عنوان العميل | لا |
| `app-locations.conf:55` و`:230` | `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` | الوارد ثمّ `$remote_addr` بعد `realip`. خلف Cloudflare يحمل العنوان مرّتين (A‑10). والتطبيق لا يقرؤه | لا |
| `app-locations.conf:60` و`:231` | `proxy_set_header X-Forwarded-Proto $scheme;` | `https`: في Full (strict) يصل Cloudflare الأصل على 443 دائماً | لا |
| `app-locations.conf:255` و`:277` | `limit_conn ws_conn 500;` و`limit_req zone=api_req burst=400 nodelay;` | الحدّ لكلّ عميل لا لكلّ عنوانٍ من Cloudflare | لا |
| `app-locations.conf:108` | `add_header Strict-Transport-Security $hsts_header always;` | يقرأ الخريطة (§3.2.3) | لا |

**لا موضع آخر:** `grep` على الملفّين لـ`remote_addr` و`x_forwarded` و`X-Forwarded` و`limit_req_zone` و`limit_conn_zone` و`log_format` لا يجد غير ما في الجدول. و`CF-Connecting-IP` يمرّ إلى التطبيق كما يمرّ أيّ ترويسة، والتطبيق لا يقرؤه.

#### 3.2.3 HSTS (FR‑16، FR‑17، FR‑22)

**الخريطة** (الأسطر 303‑305):

```nginx
    map $host $hsts_header {
        default "";
        "api.aizzak.com" "max-age=31536000";
    }
```

- `default "";` يبقى أوّل مدخل، فيبقى `_HSTS_MAP_RE` (`test_edge_hardening.py:79`) يطابق. ولا `add_header … "max-age` حرفيّة في أيّ ملفّ.
- **مقيس في الحاوية، على 80 و443:** لا ترويسة لـ`Host` ‏`localhost` و`aizzak.com`. وترويسةٌ واحدة `max-age=31536000` لـ`api.aizzak.com` و`API.AIZZAK.COM` و`api.aizzak.com:443` و`api.aizzak.com.`: `$host` يصغّر الحروف ويحذف المنفذ والنقطة الأخيرة.

**التعليق فوق الخريطة** (الأسطر 279‑302) يُعاد بهذا المعنى:
1. الفقرة الأولى كما هي، إلّا الإحالة: «This exact nginx.conf runs, UNMODIFIED, on both this WSL dev box's `localhost` and a real server: the release bundle installs this same file (docs/deploy-server.md §3).» بدل «(deploy-linux-server.md §0: …)».
2. فقرة «`add_header` omits a header ENTIRELY…» كما هي.
3. **فقرة «A real deployment adds ONE line here…» تُستبدل**، ومعها مثالها `"aizzak.example.com" "max-age=63072000; includeSubDomains";` وفقرة «Nothing is listed by default…» (لم تعد صحيحة):

```nginx
    # One line per real, STABLE hostname that has a real certificate
    # (docs/deploy-server.md §4). `api.aizzak.com` is the first (6.1): the
    # API's name behind Cloudflare, one year, and DELIBERATELY without
    # includeSubDomains or preload -- the aizzak.com zone also carries the
    # owner's site at the apex, chat., notify. and mail, and either flag would
    # force HTTPS on names this stack does not serve. The header reaches no
    # browser before the name has a DNS record (7.3). Taking it back is
    # `max-age=0`, and it lingers in every browser that saw it until that
    # browser comes back. A server installed under another --domain gets no
    # HSTS from this map.
```

فلا يبقى في المستودع ذكرٌ لـ`deploy-linux-server.md` (AC‑6.4): السطران 283 و298 هنا، والسطر 882 في `08` (§3.6).

#### 3.2.4 تعليق `:80` (الأسطر 355‑367)

يُضاف بعد «…make a healthy backend look unhealthy.» ولا يتغيّر التوجيه:

```nginx
    # Behind Cloudflare (6.1) the public HTTP -> HTTPS redirect is a Cloudflare
    # Single Redirect for the API's name, at Cloudflare's edge: in SSL Full
    # (strict) Cloudflare always reaches this origin on :443, so a request
    # that arrives here on :80 is local (install.sh, a healthcheck) or a
    # client bypassing Cloudflare (closed by 6.3). This block stays as it is.
```

#### 3.2.5 لماذا لا يتغيّر المكدّس المحلّيّ

| السلوك | الدليل |
|---|---|
| لا خدمة يُعاد إنشاؤها | لا ملفّ Compose ولا `.env.example` ولا أحجام في الفرق. و`config --hash` لا يحسب محتوى ملفٍّ مربوط |
| `realip` لا يعمل محلّيّاً | النظراء محلّيّاً: `127.0.0.1` داخل الحاوية، و`172.18.0.1` (اتّصال المضيف بعد hairpin)، وعناوين الشبكة المحلّيّة. لا شيء منها في نطاقات Cloudflare. **مقيس:** طلبٌ من غير Cloudflare بترويسةٍ مزوّرة بقي بعنوانه |
| لا HSTS محلّيّاً | `localhost` و`127.0.0.1` وعنوان الشبكة المحلّيّة يأخذون `default ""`. **مقيس** |
| `nginx-certs` كما هو | الملفّان موجودان معاً على جهاز المالك، فيخرج بـ0 بالرسالة نفسها، ولا ينادي `openssl` (AC‑2.2) |
| ملاحظة تشغيل | `nginx.conf` مربوطٌ ملفّاً لا مجلّداً. فبعد `git checkout` تبقى الحاوية الجارية على النصّ القديم حتّى `docker compose restart nginx`. وبعدها السلوك مساوٍ لـ`master` (فحص NFR‑1 في §5.3) |

### 3.3 `deploy/nginx/gen-cert.sh` (FR‑7)

**القاعدة: الوجود لا المحتوى.** لا يفتح السكربت ملفّاً موجوداً، ولا يقرأ شهادةً حقيقيّة ولا مفتاحاً.
- المسار «موجود» إن صدق `[ -e "$p" ] || [ -L "$p" ]`. والرابط الرمزيّ المعلّق موجود: وضعه المشغّل.
- **الاثنان موجودان:** خروجٌ بـ0 ورسالة اليوم نفسها. هكذا يضع الخادم شهادة المنشأ، وهكذا يبقى جهاز التطوير.
- **واحدٌ وحده:** خروجٌ بـ1، ورسالةٌ على stderr تسمّي الموجود والناقص، ولا كتابة. فيبقى `nginx` غير مُقلَع (`depends_on: service_completed_successfully`)، ويقف `install.sh` بالرمز 60 مسمّياً الخدمة: فشلٌ صاخب بدل شهادةٍ ممحوّة.
- **لا شيء:** زوجٌ ذاتيّ كاليوم، يُكتب بأسماءٍ مؤقّتةٍ عشوائيّة (`mktemp -p`، O_EXCL) ثمّ يُنشر بـ`ln -T` الذي لا يستبدل ما هو موجود (ملفّاً أو رابطاً أو مجلّداً ظهر أثناء التوليد). والإشارات `HUP` و`INT` و`TERM` تتحوّل إلى خروج فيُنظَّف المؤقّت. وفشل النشر يُسمّى بحسب الحال: «appeared» إن وُجد الهدف فعلاً، وإلّا «could not publish» (نظام ملفّاتٍ بلا روابط صلبة). ومجلّدٌ للقراءة فقط يطبع خطأً صريحاً (BUG‑1). **فالتوليد الفاشل لا يترك نصف زوج**، ولولا ذلك لصار فشلٌ عابر في `openssl` سبباً لرفض القاعدة السابقة في التشغيل التالي.

**النصّ الكامل** (كما في `deploy/nginx/gen-cert.sh` بعد جولة SR‑5/SR‑9/SR‑10):

```sh
#!/bin/sh
# TLS material for the edge (7.3 · 08-local-runbook §3.2 · server-gpu-plan.md 6.1).
#
# Runs as a one-shot Compose service on the SAME pinned nginx image that
# consumes the output -- the vault-bootstrap/minio-bootstrap pattern. That is
# deliberate: `openssl` ships in nginx:1.27, so generating here needs nothing
# installed on the host and pins the tool to a version the stack already
# declares, instead of depending on whatever `openssl` a developer happens to
# have (or, on Windows hosts, does not).
#
# PRESENCE, NEVER CONTENT: what is in the directory decides, and nothing here
# opens a file that is already there.
#   both server.crt and server.key -> left alone. This is how a real server
#     supplies its own pair (a Cloudflare origin certificate, 6.1:
#     docs/deploy-server.md §4), and how the dev box keeps its own.
#   exactly one -> stop, name the missing one, write nothing. The old rule
#     regenerated BOTH here, which silently replaced an operator's
#     certificate whose key had not been copied in yet.
#   neither -> a self-signed development pair, written under random temporary
#     names and published with `ln -T` (which never replaces), so a failed run
#     leaves neither file instead of the half pair the rule above would then
#     refuse, and a pair that appears meanwhile is never overwritten.
# A dangling symbolic link counts as present: it is the operator's, not ours.
set -eu

# /certs is where docker-compose.yml mounts deploy/nginx/certs. The variable
# exists for tests/unit/test_gen_cert.py; Compose does not set it.
CERT_DIR="${AIZZAK_CERT_DIR:-/certs}"
CERT="$CERT_DIR/server.crt"
KEY="$CERT_DIR/server.key"

present() { [ -e "$1" ] || [ -L "$1" ]; }

if present "$CERT" && present "$KEY"; then
    echo "nginx-certs: certificate already present, leaving it alone"
    exit 0
fi
if present "$CERT" || present "$KEY"; then
    if present "$CERT"; then
        have=server.crt missing=server.key
    else
        have=server.key missing=server.crt
    fi
    echo "nginx-certs: ERROR: $have is present but $missing is missing (deploy/nginx/certs); nothing was written" >&2
    echo "nginx-certs: put $missing beside it, or move $have aside to get a development certificate" >&2
    exit 1
fi

echo "nginx-certs: generating a self-signed certificate for local TLS"
umask 077

# The temporary files get random names made with O_EXCL (mktemp), never a fixed
# name: a fixed `.server.key.new` that someone planted as a symbolic link in the
# folder would be written through. They are published with `ln -T`, which
# fails when the target already exists (a file, a symlink, even a dangling
# one) and, with -T, also when it is a directory or a symlink to one (without
# it `ln` would put the link INSIDE that directory and succeed), where `mv`
# would silently replace a pair that appeared while we generated.
TMP_KEY="" TMP_CERT=""
cleanup() {
    for f in "$TMP_KEY" "$TMP_CERT"; do
        [ -z "$f" ] || rm -f -- "$f"
    done
}
trap cleanup EXIT
# dash does not run the EXIT trap when a signal kills it (docker stop sends
# TERM): turn the signals into an exit so the temporary files are removed. The
# trap runs once the foreground command (openssl) returns.
trap 'exit 1' HUP INT TERM

fail() {
    echo "nginx-certs: ERROR: $1" >&2
    exit 1
}

TMP_KEY="$(mktemp -p "$CERT_DIR" .server.key.XXXXXX 2>/dev/null)" ||
    fail "cannot create files in $CERT_DIR (is deploy/nginx/certs read-only or not writable?); nothing was written"
TMP_CERT="$(mktemp -p "$CERT_DIR" .server.crt.XXXXXX 2>/dev/null)" ||
    fail "cannot create files in $CERT_DIR (is deploy/nginx/certs read-only or not writable?); nothing was written"

# SAN is not optional cosmetics: every current client ignores the legacy CN
# for hostname verification and reads subjectAltName only. Without it the
# certificate is rejected by name even when explicitly trusted. Both
# `localhost` and 127.0.0.1 are listed because the healthcheck probes the
# literal address from inside the container while a developer's browser uses
# the name.
openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes \
    -keyout "$TMP_KEY" -out "$TMP_CERT" \
    -subj "/CN=localhost/O=AIZZAK local development" \
    -addext "subjectAltName=DNS:localhost,DNS:app,IP:127.0.0.1" >/dev/null 2>&1 ||
    fail "openssl could not generate the development certificate; nothing was written"

# The key is readable only by its owner. nginx reads it as root in the master
# process before dropping to the `nginx` user, so 600 is sufficient and is
# the tightest mode that still works. (A hard link shares the mode.)
chmod 600 "$TMP_KEY"
chmod 644 "$TMP_CERT"
# `ln` can fail for another reason than a file that appeared (a filesystem
# without hard links, a permission): say which one it was.
publish_failed() {
    if present "$1"; then
        fail "$2 appeared while the certificate was being generated; left untouched, nothing was written"
    fi
    fail "could not publish $2 in $CERT_DIR (a filesystem without hard links?); nothing was written"
}

ln -T "$TMP_KEY" "$KEY" 2>/dev/null || publish_failed "$KEY" server.key
if ! ln -T "$TMP_CERT" "$CERT" 2>/dev/null; then
    # take back the key this run just published, so no half pair is left
    rm -f -- "$KEY"
    publish_failed "$CERT" server.crt
fi

echo "nginx-certs: wrote server.crt / server.key (self-signed, 825 days)"
echo "nginx-certs: ⚠️  DEVELOPMENT ONLY -- a real deployment mounts its own"
```

**مقيس** على نموذجٍ بهذا النصّ في scratch (`sh` على المضيف، ثمّ داخل `nginx:1.27` بـ`/certs`): المجلّد الفارغ ← زوجٌ بصلاحيّتي 0644 و0600، والتكرار ← «already present». و`server.crt` وحده، ثمّ `server.key` وحده، ثمّ رابطٌ معلّق باسم `server.key` ← الرمز 1 والرسالتان، ولا ملفّ جديد.

### 3.4 `deploy/release/install.sh` (FR‑18، Q‑7)

**المكان:**
- دالّةٌ جديدة `tls_state()` في قسم «step 8: the report»، بعد `vault_volume()` (السطر 1296 اليوم) وقبل `report()`.
- وفي `report()` سطرٌ واحد بعد كتلة `if [[ $status == READY ]] … fi` (أي بعد سطر `checks` أو `failed:`) وقبل `say "  size …"`: `say "  TLS: $(tls_state)"`. فيأتي سطر TLS بعد سطر `READY … at https://<النطاق>` مباشرة، في الحالتين READY وNOT READY.
- **لا يتغيّر:** `require_host_tools` (لا أداة إلزاميّة جديدة)، وجدول الرموز في الرأس، و`parse_args`، والخيارات الستّة.

**قاعدة الحكم** (بالترتيب، وأوّل ما يصدق يُطبع). تقرأ `deploy/nginx/certs/server.crt` وحده بنداء `openssl` واحد:

| الشرط | السطر بعد `  TLS: ` |
|---|---|
| `server.crt` رابطٌ رمزيّ | `WARNING: deploy/nginx/certs/server.crt is a symbolic link; not read` |
| غير موجود | `no certificate yet (deploy/nginx/certs/server.crt is missing)` |
| لا `openssl` على المضيف | `not checked: openssl is not installed on this host` |
| `openssl` لم يقرأه، أو لا `notAfter` | `WARNING: deploy/nginx/certs/server.crt could not be read as a certificate` |
| **ذاتيّ التوقيع:** `-subject_hash` = `-issuer_hash` | `self-signed development certificate -- browsers will not trust https://<domain>; a real one: docs/deploy-server.md §4` |
| `notAfter` مضى | `WARNING: the certificate expired on <YYYY-MM-DD> (issuer <label>); docs/deploy-server.md §4` |
| `-checkhost <domain>` يقول «does NOT match» | `WARNING: the certificate does not cover <domain> (issuer <label>, expires <YYYY-MM-DD>); docs/deploy-server.md §4` |
| غير ذلك | `certificate for <domain>, issuer <label>, expires <YYYY-MM-DD>`، ويُلحق به ` -- trusted by Cloudflare only: keep the DNS record proxied` إن كان `<label>` يحوي `CloudFlare Origin` |

- **`<label>`:** أوّل `commonName` في مُصدِر الشهادة، وإلّا `organizationalUnitName`، وإلّا `organizationName`. فمُصدِر Origin CA بلا `CN`، و`<label>` له `CloudFlare Origin SSL ECC Certificate Authority` (أو `… SSL Certificate Authority` لـRSA). **مقيس** على مُصدِرٍ تجريبيّ بالاسم نفسه.
- **الرمز لا يتغيّر في أيّ حالة:** الدالّة ترجع 0 دائماً. فقبول 5.2 كما هو (Q‑7).
- **لا مادّة سرّيّة:** لا يُذكر `server.key` في نصّ السكربت كلّه (حارسٌ ثابت في AC‑5.3، فالتعليق يقول «the key file»)، ولا يُطبع PEM، وstderr الخاصّ بـ`openssl` يُرمى، والناتج يمرّ بـ`relay` (يحذف محارف التحكّم من اسم المُصدِر).

**المرجع** (جُرّب في scratch على الحالات الثماني تحت `set -euo pipefail`، بـ`openssl` 3.0.13):

```bash
# One line for the report: the certificate nginx serves on :443 (cloudflare-tls FR-18). Reads
# server.crt only, never the key file, and prints no certificate material. Always returns 0.
tls_state() {
  local crt="$ROOT/deploy/nginx/certs/server.crt" out not_after end_s day label hint=""
  local -a hashes=()
  if [[ -L $crt ]]; then
    printf 'WARNING: deploy/nginx/certs/server.crt is a symbolic link; not read'
    return 0
  fi
  if [[ ! -e $crt ]]; then
    printf 'no certificate yet (deploy/nginx/certs/server.crt is missing)'
    return 0
  fi
  if ! command -v openssl >/dev/null 2>&1; then
    printf 'not checked: openssl is not installed on this host'
    return 0
  fi
  out="$(timeout 10 openssl x509 -in "$crt" -noout -subject_hash -issuer_hash -enddate \
    -issuer -nameopt sep_multiline,lname,utf8 2>/dev/null | relay)" || out=""
  not_after="$(sed -n 's/^notAfter=//p' <<<"$out" | head -n 1)"
  end_s="$(date -u -d "$not_after" +%s 2>/dev/null)" || end_s=""
  if [[ -z $not_after || -z $end_s ]]; then
    printf 'WARNING: deploy/nginx/certs/server.crt could not be read as a certificate'
    return 0
  fi
  day="$(date -u -d "@$end_s" +%Y-%m-%d)"
  mapfile -t hashes < <(head -n 2 <<<"$out")
  if [[ ${#hashes[@]} -eq 2 && ${hashes[0]} == "${hashes[1]}" ]]; then
    printf 'self-signed development certificate -- browsers will not trust https://%s; a real one: docs/deploy-server.md §4' "$DOMAIN"
    return 0
  fi
  label="$(sed -n 's/^ *commonName=//p' <<<"$out" | head -n 1)"
  [[ -n $label ]] || label="$(sed -n 's/^ *organizationalUnitName=//p' <<<"$out" | head -n 1)"
  [[ -n $label ]] || label="$(sed -n 's/^ *organizationName=//p' <<<"$out" | head -n 1)"
  label="${label:-unknown}"
  if ((end_s <= $(date -u +%s))); then
    printf 'WARNING: the certificate expired on %s (issuer %s); docs/deploy-server.md §4' "$day" "$label"
    return 0
  fi
  # -checkhost runs alone and its whole output is compared: the issuer name is the certificate's own
  # text and could carry the phrase (SR-8)
  chk="$(timeout 10 openssl x509 -in "$crt" -noout -checkhost "$DOMAIN" 2>/dev/null)" || chk=""
  if [[ $chk != "Hostname $DOMAIN does match certificate" ]]; then
    printf 'WARNING: the certificate does not cover %s (issuer %s, expires %s); docs/deploy-server.md §4' \
      "$DOMAIN" "$label" "$day"
    return 0
  fi
  [[ $label != *"CloudFlare Origin"* ]] || hint=' -- trusted by Cloudflare only: keep the DNS record proxied'
  printf 'certificate for %s, issuer %s, expires %s%s' "$DOMAIN" "$label" "$day" "$hint"
}
```

**حقائق مقيسة اعتمد عليها:** `-checkhost` يخرج بـ0 في الحالين، فالحكم من نصّه («does match» أو «does NOT match»). و`-checkend` في النداء نفسه يقطع المخرج قبل `-checkhost`، فلا يُستعمل، والانتهاء يُحسب بـ`date`. و`openssl` موجودٌ افتراضيّاً على Ubuntu Server، وإن غاب فالسطر يقول ذلك ولا يقف التثبيت.

### 3.5 دفتر المنسّق: تغييرات منطقة Cloudflare (US‑3)

**قواعد عامّة:**
- كلّ بندٍ أدناه **بموافقة المالك عليه بعينه** (Q‑3، Q‑12)، وقبله صورة وبعده صورة (AC‑3.1، AC‑3.7). ولا يمسّ بندٌ الجذر ولا `chat.` ولا نفق `myapp` ولا `notify.` ولا البريد (FR‑15). وإعدادات المنطقة `ssl` و`always_use_https` و`security_header` وUniversal SSL تبقى كما هي.
- `CF_API_TOKEN` و`ZONE_ID` (و`CF_API_TOKEN_RO` للقراءة فقط) قيمٌ في الجلسة، ولا تُطبع ولا تظهر في سطر أوامر. والتوكن لا يُكتب إلّا في ملفّ ترويسةٍ بصلاحيّة 0600 داخل scratch خارج المستودع (`CF_HDR`، `CF_HDR_RO`)، يُحذف في الخطوة الأخيرة من الدفتر. والصور في scratch خارج المستودع. وما يُنقل إلى `acceptance.md` أسماءٌ وقيمٌ غير سرّيّة، بلا توكن ولا معرّف حساب ولا معرّف منطقة.
- **نطاق التوكن (SR‑4):** Custom Token لمنطقة `aizzak.com` وحدها، وبالصلاحيّات التي يحتاجها الدليل وحدها: قراءة المنطقة، وتعديل DNS، وتعديل إعدادات المنطقة، وتعديل قواعد Configuration وRedirect وCache، و«SSL and Certificates» لإصدار شهادة المنشأ وإبطالها، وقراءة Bot Management. له تاريخ انتهاء بعد 7.3، وتصفية عنوان العميل على جهاز المشغّل. وتوكنٌ آخر للقراءة فقط لـ`snap`. ويُبطَل التوكنان بعد 7.3.
- إن لم يكن للتوكن صلاحيّة التعديل، فالقيم نفسها من لوحة Cloudflare (المسار مكتوبٌ مع كلّ بند).

**الأدوات** (bash، و`jq` موجودٌ على هذا الجهاز):

```bash
CF=https://api.cloudflare.com/client/v4
SNAP=/tmp/claude-1000/-home-AIZZAK/81328ec9-6c6d-4155-a4e6-26b05fb3b1f3/scratchpad/cf-snap
mkdir -p "$SNAP"
# the token never reaches argv (visible in ps): the header comes from a 0600 file in scratch
CF_HDR=$(umask 077; mktemp "$SNAP/.cfhdr.XXXXXX"); printf 'Authorization: Bearer %s\n' "$CF_API_TOKEN" >"$CF_HDR"
cf() { curl -sS -H @"$CF_HDR" -H "Content-Type: application/json" "$@"; }   # rm -f "$CF_HDR" at the end
# the read-only token (for snap) has its own header file: snap never holds an edit token
CF_HDR_RO=$(umask 077; mktemp "$SNAP/.cfhdr.XXXXXX"); printf 'Authorization: Bearer %s\n' "$CF_API_TOKEN_RO" >"$CF_HDR_RO"
cf_ro() { curl -sS -H @"$CF_HDR_RO" -H "Content-Type: application/json" "$@"; }   # rm -f "$CF_HDR_RO" at the end

probe() {  # AC-3.7: four answers that must not move
  local u
  for u in http://aizzak.com/ https://aizzak.com/ http://notify.aizzak.com/ https://notify.aizzak.com/; do
    printf '%s %s\n' "$u" "$(curl -s -o /dev/null --max-time 20 -w '%{http_code} %{redirect_url}' "$u")"
  done
}

snap() {  # snap <label>: the AC-3.1 picture, without ids or timestamps
  local d="$SNAP/$1" s p
  mkdir -p "$d"
  cf_ro "$CF/zones/$ZONE_ID/dns_records?per_page=100" \
    | jq -S '[.result[] | {type, name, content, proxied, ttl}] | sort_by(.name, .type, .content)' >"$d/dns.json"
  for s in ssl always_use_https min_tls_version tls_1_3 security_header browser_check security_level; do
    cf_ro "$CF/zones/$ZONE_ID/settings/$s" | jq -S '{id: .result.id, value: .result.value}'
  done | jq -s -S . >"$d/settings.json"
  cf_ro "$CF/zones/$ZONE_ID/bot_management" | jq -S '{fight_mode: .result.fight_mode}' >"$d/bots.json"
  for p in http_config_settings http_request_dynamic_redirect http_request_cache_settings; do
    cf_ro "$CF/zones/$ZONE_ID/rulesets/phases/$p/entrypoint" | jq -S --arg p "$p" \
      '{phase: $p, rules: [(.result.rules // [])[] | {description, expression, action, action_parameters, enabled}]}'
  done | jq -s -S . >"$d/rules.json"
  probe >"$d/probe.txt"
}

add_rule() {  # add_rule <phase> <rule json>: prints "<ruleset id> <rule id>" -- keep both for the rollback
  local rs
  rs="$(cf "$CF/zones/$ZONE_ID/rulesets/phases/$1/entrypoint" | jq -r '.result.id // empty')"
  if [[ -z $rs ]]; then  # no entrypoint yet (today, per the orchestrator's read): create it with this one rule
    cf -X PUT "$CF/zones/$ZONE_ID/rulesets/phases/$1/entrypoint" --data "$(jq -n --argjson r "$2" '{rules: [$r]}')"
  else                   # never PUT over an existing entrypoint: that would replace its other rules
    cf -X POST "$CF/zones/$ZONE_ID/rulesets/$rs/rules" --data "$2"
  fi | jq -r 'if .success then .result.id + " " + .result.rules[-1].id else error(.errors | tostring) end'
}
```

**لكلّ بند:** `snap before-N`، ثمّ التغيير، ثمّ `snap after-N`، ثمّ `diff -ru "$SNAP/before-N" "$SNAP/after-N"`. والمقبول: الفرق هو البند وحده، و`probe.txt` متساوٍ بلا استثناء (AC‑3.7). وإلّا فالرجوع فوراً.

#### 3.5.1 الآن (قبل الدمج، بالتوازي مع البناء)

**لماذا الآن:**
- القواعد الثلاث **خاملة** حتّى يوجد سجلّ DNS لـ`api.aizzak.com`، فلا طلب يطابقها. فأثرها على المنطقة صفر.
- **ويجب أن تسبق السجلّ:** فأوّل طلبٍ مُمرَّر في 7.3 يكون strict ومحوَّلاً وبلا تخزين. ولا تمرّ لحظةٌ يُخدم فيها الاسم بوضع `full` على شهادةٍ ذاتيّة، وهي الحال (أ0) المرفوضة.
- وأدنى TLS على المنطقة لا يتعلّق بالخادم، وتطبيقه الآن يفصل أثره عن ضجيج 7.3، ويُقاس الآن على `aizzak.com`.

**الترتيب من الأضيق أثراً إلى الأوسع:**

| # | البند | الطلب | التحقّق بعده | الرجوع |
|---|---|---|---|---|
| 1 | **تجاوز التخزين لاسم الـAPI** (FR‑14). اللوحة: Caching ← Cache Rules ← Create: Hostname equals `api.aizzak.com` ← Bypass cache | `add_rule http_request_cache_settings "$R1"` | الفرق في `rules.json` القاعدة وحدها | `cf -X DELETE "$CF/zones/$ZONE_ID/rulesets/<rs>/rules/<rule>"` |
| 2 | **HTTP ← HTTPS لاسم الـAPI** (FR‑12). اللوحة: Rules ← Redirect Rules ← Single Redirect: Hostname equals `api.aizzak.com` وSSL/HTTPS off ← Dynamic ← التعبير أدناه ← 308 ← Preserve query string | `add_rule http_request_dynamic_redirect "$R2"` | كذلك. و`always_use_https` ما يزال `off` | كذلك |
| 3 | **Full (strict) لاسم الـAPI** (FR‑11). اللوحة: Rules ← Configuration Rules ← Create: Hostname equals `api.aizzak.com` ← SSL: Strict | `add_rule http_config_settings "$R3"` | كذلك. و`ssl` على المنطقة ما يزال `full` | كذلك |
| 4 | **أدنى TLS 1.2 للمنطقة** (FR‑13). اللوحة: SSL/TLS ← Edge Certificates ← Minimum TLS Version ← TLS 1.2 | `cf -X PATCH "$CF/zones/$ZONE_ID/settings/min_tls_version" --data '{"value":"1.2"}'` | الفرق `min_tls_version` وحده. **ومسبار 1.1 على `aizzak.com`** قبل التغيير وبعده (أدناه) | `cf -X PATCH "$CF/zones/$ZONE_ID/settings/min_tls_version" --data '{"value":"1.0"}'` |

```bash
R1='{"description":"cloudflare-tls 6.1: never cache api.aizzak.com",
     "expression":"(http.host eq \"api.aizzak.com\")",
     "action":"set_cache_settings","action_parameters":{"cache":false},"enabled":true}'
R2='{"description":"cloudflare-tls 6.1: HTTP to HTTPS for api.aizzak.com",
     "expression":"(http.host eq \"api.aizzak.com\" and not ssl)",
     "action":"redirect",
     "action_parameters":{"from_value":{"status_code":308,
       "target_url":{"expression":"concat(\"https://\", http.host, http.request.uri.path)"},
       "preserve_query_string":true}},
     "enabled":true}'
R3='{"description":"cloudflare-tls 6.1: SSL Full (strict) for api.aizzak.com only",
     "expression":"(http.host eq \"api.aizzak.com\")",
     "action":"set_config","action_parameters":{"ssl":"strict"},"enabled":true}'
```

- **مسبار TLS 1.1 (البند 4):** `curl -sS -o /dev/null -w '%{http_code}\n' --tlsv1.1 --tls-max 1.1 --ciphers 'DEFAULT@SECLEVEL=0' https://aizzak.com/`. قبل التغيير رمزُ HTTP، وبعده فشل المصافحة (`curl` يخرج بـ35). `SECLEVEL=0` لازم: OpenSSL 3 يرفض 1.1 من جهته دونه، فيفشل المسبار قبل التغيير أيضاً ولا يثبت شيئاً. **وإن فشل قبل التغيير رغم ذلك،** فالمسبار باطل: `openssl s_client -connect aizzak.com:443 -servername aizzak.com -tls1_1 -cipher 'DEFAULT@SECLEVEL=0' </dev/null`. هذا نصف AC‑3.6 الخاصّ بالمنطقة، ونصفه على `api.aizzak.com` في 7.3.
- **R2 (حقيقةٌ من وثائق Cloudflare، غير مقيسةٍ هنا):** التعبير الديناميكيّ هو مثال Cloudflare لتحويل HTTP إلى HTTPS في Single Redirects. وإن رفضه الـAPI فلا يتغيّر شيء. وعندها البديل الموثّق: `(http.request.full_uri wildcard r"http://api.aizzak.com/*")` مع `wildcard_replace(http.request.full_uri, r"http://api.aizzak.com/*", r"https://api.aizzak.com/${1}")` و`preserve_query_string:false`. **308 لا 301:** يحفظ الطريقة والجسم، وهو ما يقترحه تعليق `:80` في `nginx.conf:364`، وAC‑3.5 يقبل الاثنين.
- **الأدلّة إلى `acceptance.md`:** صورة `before-1` (AC‑3.1)، ونصّ كلّ `diff`، و`probe.txt` قبل كلّ بندٍ وبعده (AC‑3.7)، ومسبار 1.1، والتاريخ. ويكتب المطوّر عمود «من ومتى» في جدول §4.1 من `deploy-server.md` بهذه التواريخ (§6).

#### 3.5.2 يوم الدمج (AC‑1.2)

```bash
curl -sS https://api.cloudflare.com/client/v4/ips | jq -r '.result.ipv4_cidrs[], .result.ipv6_cidrs[]' | sort >"$SNAP/ips-api.txt"
sed -n 's/^[[:space:]]*set_real_ip_from[[:space:]]\+\([^;]*\);.*/\1/p' /home/AIZZAK/deploy/nginx/nginx.conf | sort >"$SNAP/ips-conf.txt"
diff "$SNAP/ips-api.txt" "$SNAP/ips-conf.txt" && echo "SAME $(date -u +%F)"
```

الـAPI عامّ بلا توكن. والمقبول `SAME`، ويُكتب الناتج وتاريخه و`etag` في `acceptance.md`. **وإن اختلف:** تُحدَّث القائمة في الفرع قبل الدمج بالطريقة في §4.5 من الدليل، وتاريخها في التعليق.

#### 3.5.3 في 7.3 (على الخادم، بالترتيب)

**يجب قبل 7.3:** وسم إصدارٍ بعد دمج هذه الميزة، فحزمةٌ أقدم (منها `v2026.10.3` المنتظرة لـ5.1) لا تحمل `realip` ولا سطر HSTS ولا سطر TLS.

المشغّل: المالك، أو وكيلٌ على الخادم بإذنه. والأوامر من مجلّد الحزمة المفكوكة (`/opt/aizzak-<V>`) وبـ`sudo`. وأيّ خطوةٍ في Cloudflare للمنسّق، بموافقة المالك عليها بعينها.

1. **التثبيت:** `sudo ./install.sh --domain api.aizzak.com --firebase-project aizzak-agent`. فيصير منه `OAUTH_REDIRECT_BASE_URL` و`MINIO_PUBLIC_ENDPOINT`. والتقرير يقول `TLS: self-signed development certificate …`.
2. **المفتاح وطلب التوقيع على الخادم** (ECDSA P‑256، §7). لا يُطبع المفتاح ولا يخرج من الخادم:
   ```bash
   sudo sh -c 'umask 077 && openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
     -keyout deploy/nginx/certs/origin.key -out deploy/nginx/certs/origin.csr -subj "/CN=api.aizzak.com"'
   sudo cat deploy/nginx/certs/origin.csr     # public: the only thing that leaves the server
   ```
   وإن غاب `openssl` عن المضيف: الأمر نفسه داخل `sudo docker run --rm --network none -v "$PWD/deploy/nginx/certs:/c" --entrypoint sh nginx:1.27 -c '…'` بالمسار `/c/`.
3. **إصدار الشهادة** لاسم `api.aizzak.com` **وحده**، 15 سنة. إمّا:
   - **اللوحة (المالك):** SSL/TLS ← Origin Server ← Create Certificate ← «Use my private key and CSR» ← لصق الطلب ← حذف `*.aizzak.com` و`aizzak.com` من الأسماء وكتابة `api.aizzak.com` ← 15 years ← Create.
   - **أو الـAPI (المنسّق، والطلب عامّ):**
     ```bash
     jq -n --rawfile csr origin.csr \
       '{hostnames: ["api.aizzak.com"], requested_validity: 5475, request_type: "origin-ecc", csr: $csr}' \
       | cf -X POST "$CF/certificates" --data @- >"$SNAP/origin-1.json"
     jq -r '.result.id, .result.expires_on' "$SNAP/origin-1.json"     # keep the id: rotation revokes it
     jq -r '.result.certificate' "$SNAP/origin-1.json" >"$SNAP/origin-1.crt"   # public
     ```
   ثمّ تُكتب الشهادة (PEM عامّ) في `deploy/nginx/certs/origin.crt` على الخادم، و`sudo chmod 644` لها. **ومن اللوحة يُسجَّل معرّف الشهادة عند الإصدار:** خطوة الإبطال في التدوير تحتاجه.
4. **التحقّق قبل التفعيل (AC‑2.4، تُطبع البصمات وحدها):**
   ```bash
   ( cd deploy/nginx/certs &&
     sudo openssl x509 -in origin.crt -noout -ext subjectAltName -issuer -enddate &&
     sudo openssl x509 -in origin.crt -noout -checkend 31536000 &&
     sudo openssl x509 -in origin.crt -noout -pubkey | openssl pkey -pubin -outform DER | sha256sum &&
     sudo openssl pkey -in origin.key -pubout -outform DER | sha256sum )
   ```
5. **سجلّ DNS (المنسّق، AC‑3.3)،** والأصل ما يزال بالشهادة الذاتيّة: `snap before-dns`، ثمّ:
   ```bash
   cf -X POST "$CF/zones/$ZONE_ID/dns_records" --data \
     '{"type":"A","name":"api.aizzak.com","content":"<server IPv4>","proxied":true,"ttl":1,"comment":"cloudflare-tls 6.1: AIZZAK API origin"}' \
     | jq -r '.result.id'                                    # rollback: cf -X DELETE "$CF/zones/$ZONE_ID/dns_records/<id>"
   ```
   ثمّ `snap after-dns`، والفرق السجلّ وحده. و`curl -s -o /dev/null -w '%{http_code}\n' https://api.aizzak.com/health` = **526**: هذا نصف AC‑3.4 الأوّل، ودليل أنّ Cloudflare يتحقّق من الأصل.
6. **التفعيل:**
   ```bash
   sudo cp -p deploy/nginx/certs/server.crt deploy/nginx/certs/server.crt.prev &&
     sudo cp -p deploy/nginx/certs/server.key deploy/nginx/certs/server.key.prev &&
     sudo mv -f deploy/nginx/certs/origin.key deploy/nginx/certs/server.key &&
     sudo mv -f deploy/nginx/certs/origin.crt deploy/nginx/certs/server.crt &&
     sudo docker compose exec nginx nginx -t && sudo docker compose exec nginx nginx -s reload
   ```
   المجلّد مربوطٌ مجلّداً، فيرى nginx الملفّين الجديدين بلا إعادة إنشاء. والأسطر مسلسلةٌ بـ`&&` فلا `reload` بعد `mv` فاشل، والزوج العاملة منسوخةٌ إلى `*.prev`. وإن فشل `nginx -t`، فلا `reload`: يُنقل `server.crt`/`server.key` الجديدان إلى `origin.crt`/`origin.key` **أوّلاً** (حتّى لا يُمحى مفتاحهما)، ثمّ تُعاد `*.prev` إلى `server.crt`/`server.key`، ويُطبَّق الرجوع (الخطوة 10) إن لزم.
7. **التحقّق المحلّيّ:** `openssl s_client -connect 127.0.0.1:443 -servername api.aizzak.com -CAfile <جذر Origin CA ECC العامّ من وثائق Cloudflare> </dev/null 2>/dev/null | grep 'Verify return code'` ← `0 (ok)`.
8. **الدفعة الخارجيّة** من جهازٍ خارج الخادم:
   - AC‑2.5: `curl -fsS https://api.aizzak.com/health`.
   - AC‑3.4 (نصفه الثاني): 200، و`ssl` على المنطقة ما يزال `full`.
   - AC‑3.5: `curl -sI http://api.aizzak.com/health`.
   - AC‑3.6: مسبار 1.1 بـ`SECLEVEL=0`، ثمّ 1.2، ثمّ 1.3.
   - AC‑3.8: `cf-cache-status` على `/health` مرّتين و`/x.pdf` و`/x.png`.
   - AC‑4.3: HSTS على `api.aizzak.com` وحده.
   - AC‑1.5 و AC‑1.6.
   - بعد نجاح هذه الدفعة: `sudo rm -f deploy/nginx/certs/server.crt.prev deploy/nginx/certs/server.key.prev` (فالأولى مفتاح الشهادة السابقة).
   - وتكرار `install.sh` بالمدخلات نفسها يقول `TLS: certificate for api.aizzak.com, issuer CloudFlare Origin SSL ECC Certificate Authority, …` ولا يمسّ حاوية.
9. **تمرين التدوير (AC‑2.7):**
   - سبرٌ خارجيّ كلّ ثانية: `while :; do curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 https://api.aizzak.com/health; sleep 1; done | tee probe.log`.
   - `docker inspect -f '{{.Id}} {{.State.StartedAt}}' $(sudo docker compose ps -q nginx)` قبل التمرين.
   - مفتاحٌ وطلبٌ ثانيان (`origin.key`/`origin.csr` من جديد)، وشهادةٌ ثانية، والخطوتان 4 و6 (وتنسخ 6 الزوج العاملة إلى `*.prev`).
   - البصمة الجديدة في `s_client`، و`docker inspect` بعد التمرين بالقيمتين نفسيهما.
   - إبطال الشهادة الأولى: `cf -X DELETE "$CF/certificates/<id-1>"`، أو Revoke من اللوحة.
   - `grep -vc '^200$' probe.log` = 0.
   - بعد تأكيد الإبطال وخلوّ المسبار: `sudo rm -f deploy/nginx/certs/server.crt.prev deploy/nginx/certs/server.key.prev`.
10. **الرجوع (FR‑8):**
    ```bash
    sudo mkdir -p deploy/nginx/certs/aside
    sudo mv deploy/nginx/certs/server.crt deploy/nginx/certs/server.key deploy/nginx/certs/aside/
    sudo docker compose up -d nginx-certs
    sudo docker compose exec nginx nginx -t && sudo docker compose exec nginx nginx -s reload
    ```
    فيعود الاسم يردّ 526 بقصد (strict). وإخراجه من الخدمة كلّيّاً حذف سجلّ DNS.
11. **التنظيف في آخر الدفتر:** `rm -f "$CF_HDR" "$CF_HDR_RO"` (ملفّا ترويسة التوكنين)، ثمّ إبطال التوكنين من اللوحة بعد 7.3 (SR‑4).
11. **US‑7** (AC‑7.1، AC‑7.2، AC‑7.4، AC‑7.5) بحساب الاختبار على الخادم، وتُكتب الأرقام في وثيقة 7.3. ويُصحَّح إيقاع `ping` في §4.7 من الدليل بما قيس.

### 3.6 الوثائق (US‑6)

**قيدان من اختباراتٍ قائمة يلتزم بهما كلّ نصٍّ أدناه:**
- `test_no_document_says_install_sh_sets_env_to_0600`: لا سطر في `05` و`08` و`deploy-server.md` والخطّة فيه `install.sh` ثمّ `0600`. فجملة صلاحيّة المفتاح لا تذكر `install.sh` في سطرها.
- `test_gpu_compose_hygiene.py` يمسح `docs/deploy-server.md` بحثاً عن ترويسة مفتاحٍ خاصّ (`-----BEGIN … PRIVATE KEY-----`): لا مثال PEM في الدليل. وصفوف الأمان متّصلةٌ بلا سطرٍ فارغ (`test_every_security_row_sits_in_one_contiguous_table`)، وبأربعة أعمدة.

| الملفّ | الموضع | المحتوى |
|---|---|---|
| `docs/deploy-server.md` | الرأس (السطر 5) | ذكر القسم الجديد: «وقسم الشهادة عبر Cloudflare (§4، الخطوة 6.1)» |
| | §3.4 الخطوة 8 | «… وتذكير بصلاحيّة `.env`، **وحال الشهادة** (§4)» |
| | §3.8 (السطر 189) | تُحذف «الشهادة الحقيقيّة (6.1: يبقى ما يولّده `nginx-certs` ذاتيّ التوقيع)». ويُكتب: لا يصنع الشهادة الحقيقيّة، فتضعها بخطوات §4، والتقرير يقول حالها |
| | **قسمٌ جديد `## 4) الشهادة عبر Cloudflare (6.1)`** قبل `---` و«قائمة الأمان المؤجّلة» | الأقسام الفرعيّة أدناه |
| | قائمة الأمان، بعد ث‑12 | ث‑13 وث‑14 وث‑15 (أدناه) |
| `docs/server-gpu-plan.md` | الرأس (السطر 5) | جملة: «**6.1** (`docs/delivery/cloudflare-tls/`) مبنيّة 🟡، تنتظر 7.3» |
| | اللوحة، صفّ **6.1** | عمود الحالة: «🟡 مبنيّة في PR ‎#N (`docs/delivery/cloudflare-tls/`)، بانتظار 7.3: سجلّ DNS وشهادة المنشأ على الخادم، ثمّ `curl` دون `-k` وتمرين التدوير». ويصير «🟡 مدموجة <التاريخ> (PR ‎#N، `<sha>`)، بانتظار 7.3 …» في commit الإغلاق بعد الدمج، كما في 5.1 و5.2 |
| | §5، نصّ 6.1 (الأسطر 368‑375) | «الطريقة»: قرار 4 أُجيب (2026‑10‑20): Cloudflare. اسم `api.aizzak.com` مُمرَّر، وUniversal SSL للزائر، وFull (strict) لهذا الاسم. وشهادة منشأ من Origin CA لهذا الاسم وحده، 15 سنة، ومفتاحها يولَّد على الخادم بطلب توقيع، في المسارين الحاليّين، ثمّ `nginx -s reload`، بلا تغيير في Compose. وnginx يأخذ العنوان من `CF-Connecting-IP` لنطاقات Cloudflare وحدها. و`nginx-certs` يبقى للتطوير وللإقلاع الأوّل، ولا يكتب فوق شهادةٍ موجودة. وHSTS لـ`api.aizzak.com` وحده بلا `includeSubDomains`، ولا يبلغ متصفّحاً قبل سجلّ DNS في 7.3. وLet's Encrypt لم يُختَر، والتفاصيل في `deploy-server.md` §4. **و«القبول»:** `curl https://api.aizzak.com/health` دون `-k`، وشهادة المنشأ صالحةٌ سنةً على الأقلّ (`openssl x509 -checkend 31536000`)، وتمرين تدويرٍ واحد بلا ردٍّ غير 200. وذلك بدل «`--dry-run`»: لا تجديد لشهادةٍ مدّتها 15 سنة |
| | §6، صفّ **قرار 4** | يُلحق بعمود «المقترح»: «**الجواب (2026‑10‑20):** Cloudflare لا Let's Encrypt: شهادة Origin CA على nginx ووضع Full (strict) لاسم الـAPI `api.aizzak.com` (Q‑1 وQ‑2 في `docs/delivery/cloudflare-tls/`)» |
| `docs/server-gpu-summary.html` | الشريط (258‑266) | `aria-label`: «تسعٌ وعشرون خطوة: ثمانٍ مكتملة واثنتا عشرة جارية وتسعٌ لم تبدأ». و`flex:8` / `flex:12` / `flex:9` ونصوصها. والمفتاح «— ٨» و«— ١٢» و«— ٩» |
| | بطاقة ٦٫١ (605‑614) | `<article class="step partial">`. الوسم `<span class="tag partial">مبنيّة · تنتظر الخادم</span>`. «المطلوب»: اسم الـAPI يمرّ عبر Cloudflare، بشهادته العامّة للزائر، وشهادة منشأٍ منه على الخادم مدّتها ١٥ سنة فلا تجديد. وقرار ٤ أُجيب: Cloudflare بدل Let's Encrypt. «القبول»: الموقع يفتح بلا تحذير، والشهادة صالحةٌ سنةً على الأقلّ، وتبديلها لا يقطع الخدمة. **و«٠ من ٥» للمرحلة ٦ لا يتغيّر:** العدّ للمكتمل وحده |
| | بطاقة القرارات (737) | «النطاق والشهادة (أُجيب: Cloudflare)» |
| | التذييل (743) | تاريخ الالتزام، و«(٦٫١ مبنيّة، وتنتظر تجربة الخادم)» |
| `docs/design/08-local-runbook.md` | §3.2، آخر الفقرة الأولى (876) | جملةٌ واحدة: «وعلى الخادم خلف Cloudflare (6.1): شهادة منشأ من Cloudflare Origin CA لاسم الـAPI وحده في المسارين نفسيهما، ومفتاحها يولَّد على الخادم، ويأخذ nginx عنوان العميل من `CF-Connecting-IP` لنطاقات Cloudflare المنشورة وحدها (`docs/deploy-server.md` §4).» |
| | §3.2، السطر 882 | «(`deploy-linux-server.md §10.3`)» تصير «(`docs/deploy-server.md` §4)؛ وفيها اليوم سطر `api.aizzak.com` وحده، بلا `includeSubDomains`» |
| `docs/design/05-rbac-config-secrets.md` | §3، سطرٌ في الاقتباس بعد السطر 127 | «> ومفتاح TLS للحافّة (`deploy/nginx/certs/server.key`) ليس سرّ تطبيقٍ ولا يمرّ بـVault: ملفٌّ على قرص الخادم، صلاحيّته 0600 لـroot، يولَّد هناك بطلب توقيع، ولا يُلتزم ولا يدخل الحزمة (`docs/deploy-server.md` §4، ث‑15).» |

**أقسام `## 4)` في `docs/deploy-server.md`** (عربيّة، والأوامر بالإنجليزيّة):
- **مقدّمة:** الطريقة في أربعة أسطر، و`--domain api.aizzak.com`، وأنّ جهاز التطوير لا يتغيّر. واسمٌ أعمق من المستوى الأوّل (`api.prod.aizzak.com`) لا تغطّيه Universal SSL المجّانيّة.
- **4.1 إعدادات المنطقة:** جدول «الإعداد | القيمة | النطاق | من ومتى» للبنود الأربعة في §3.5.1 وسجلّ DNS (7.3). وصفٌّ لما لا يُلمس: `ssl` للمنطقة `full`، و`always_use_https` مطفأ، وHSTS المنطقة مطفأ، وUniversal SSL مفعّل، والجذر و`chat.` و`notify.` والبريد. وأمر الفحص إن غيّر أحدٌ `ssl` إلى `flexible`.
- **4.2 شهادة المنشأ:** الخطوات 2‑7 من §3.5.3 **بهذا الترتيب** (AC‑2.1): المفتاح وطلب التوقيع (`openssl req -new`)، ثمّ «Use my private key and CSR»، ثمّ `server.crt`، ثمّ `nginx -t` و`nginx -s reload`، على أن يسبقهما التحقّق قبل التفعيل ومنه `-checkend 31536000` (BUG‑2). ثمّ الرجوع `sudo docker compose up -d nginx-certs`. وجملةٌ أنّ الطريق البديل (مفتاحٌ يولّده Cloudflare) أضعف، لأنّ المفتاح يمرّ بالمتصفّح. وجدولٌ صغير لسطر `TLS:` في التقرير.
- **4.3 التدوير:** الخطوة 9.
- **4.4 الرجوع:** الخطوة 10، ومعنى 526 بعده، وحذف السجلّ لإخراج الاسم.
- **4.5 عنوان العميل وقائمة نطاقات Cloudflare:** ما يفعله `realip` ولمن. والمقارنة في §3.5.2. والتحديث: في المستودع أوّلاً (التعليق بالمصدر والتاريخ). ثمّ على الخادم حتّى يوجد `upgrade.sh`: نسخ `nginx.conf`، ثمّ فحصه في حاويةٍ مؤقّتة (`sudo docker run --rm --network none -v …nginx.conf:/etc/nginx/nginx.conf:ro -v …app-locations.conf:/etc/nginx/app-locations.conf:ro -v …certs:/etc/nginx/certs:ro nginx:1.27 nginx -t`)، ثمّ **`sudo docker compose restart nginx`**. فـ`nginx.conf` مربوطٌ ملفّاً، و`nginx -s reload` بعد استبدال الملفّ يعيد تحميل النصّ القديم.
- **4.6 HSTS (AC‑4.4):** السطر، ولا `includeSubDomains` ولا `preload` وسببه. والترويسة لا تبلغ متصفّحاً قبل سجلّ DNS (7.3، بعد AC‑2.5)، وهذا معنى «بعد ثبوت الشهادة». والرجوع `max-age=0`، وأثره باقٍ في المتصفّحات حتّى تزورها. واسم `--domain` آخر لا يأخذ HSTS.
- **4.7 حدود Cloudflare (FR‑23، AC‑7.3):**
  - جسم الطلب 100 م.ب (والـAPI ‏2 م.ب، والملفّات حتّى 50 م.ب برابطٍ موقّع).
  - ردٌّ غير مبثوث فوق 100 ثانية يصل العميل `524` والعمل جارٍ على الخادم. والجواب المجمَّع لسير عملٍ طويل (`runWorkflow`) معرّضٌ له، فيُنصح بـSSE (`text/event-stream`).
  - WebSocket: `ping` من العميل كلّ 30 ثانية حتّى يُقاس الحدّ في 7.3 (AC‑7.2).
  - التخزين بحسب الامتداد، وقاعدة التجاوز تحسمه لاسم الـAPI.
  - Bot Fight Mode وBrowser Integrity Check و«Under Attack» قد تتحدّى عميل API.
  - أصلٌ للمتصفّح غير مدرجٍ في CORS يُرفض (Q‑10).
- **4.8 جدول الأعطال:** الأعمدة «الرمز | المعنى | الفحص | العلاج»، والرموز بين علامتي الكود (`` `521` ``):
  - `521`: الأصل لا يجيب على 443، أو nginx متوقّف.
  - `522`: مهلة الاتّصال، أو جدار حمايةٍ يحجب Cloudflare.
  - `524`: الردّ تجاوز 100 ثانية.
  - `525`: فشل مصافحة TLS مع الأصل.
  - `526`: شهادة الأصل ذاتيّة أو لاسمٍ آخر أو منتهية.
  - `nginx-certs` خرج بـ1: نصف زوج.

**صفوف الأمان** (عمودٌ لكلٍّ من: # | الثغرة | الأثر | العلاج المقترح):

| # | الثغرة | الأثر | العلاج المقترح |
|---|---|---|---|
| ث‑13 | Cloudflare ينهي TLS ويرى النصّ الصريح: ترويسة `Authorization`، وتوكن WebSocket في الاستعلام (`?token=`، `03-api-spec.md:289`)، والأسئلة والأجوبة، ومحتوى الملفّات في 6.2. قبله المالك لمرحلة staging (Q‑9، 2026‑10‑20) | طرفٌ ثالث يرى كلّ حركة الـAPI | يُعاد النظر في الموجة 8 |
| ث‑14 | الأصل يُبلَغ مباشرةً على 80 و443 متجاوزاً Cloudflare، حتّى 6.3 أو Authenticated Origin Pulls | تجاوز حماية Cloudflare وحدوده. وحدّ الثقة في `realip` هو كلّ عملاء Cloudflare: طلبٌ من Worker في منطقةٍ أخرى يختار `CF-Connecting-IP`، فيتأثّر مفتاح حدّ المعدّل لكلّ عنوان وسجلّ الوصول (لا قرار تفويض مبنيّ على العنوان قبل 6.3) | 6.3: Authenticated Origin Pulls بشهادةٍ **خاصّة بالمنطقة** يرفعها المالك (شهادة Cloudflare المشتركة لا تُغلق الثغرة)، و`realip` في خادم 443 وحده، والمنفذ 443 وحده مفتوحٌ لنطاقات Cloudflare (لا 80) (Q‑1) |
| ث‑15 | مفتاح المنشأ `deploy/nginx/certs/server.key` ملفٌّ على القرص خارج Vault، صلاحيّته 0600 لـroot (Q‑8) | من يملك root أو مجموعة `docker` على الخادم يقرؤه، فينتحل الأصل أمام Cloudflare | إبطال الشهادة في Cloudflare وإصدار أخرى (§4.3). وتشفير القرص ومراجعة الموجة 8 |

### 3.7 حسم الافتراضات

- **A‑3:** مفتاح المنشأ **ECDSA P‑256** (`request_type: origin-ecc`). مصافحةٌ أخفّ، وCloudflare يقبله من الأصل. وRSA 2048 بديلٌ موثّق (`-newkey rsa:2048` و`origin-rsa`) إن تعذّر. والشهادة للاسم بالضبط، لا wildcard.
- **A‑4:** ✅ تحقّق المنسّق (`--with-http_realip_module`)، وأكّده تشغيل `nginx -t` والسلوك في §3.2.1.
- **A‑6:** `:80` يبقى يخدم بلا تحويل، والتعليق يقول السبب (§3.2.4).
- **A‑10:** `X-Forwarded-For` مكرّر العنوان خلف Cloudflare. أثرٌ شكليّ، ولا يُعالج.

## 4. البيانات

لا تغيير: لا جدول ولا عمود ولا فهرس، ولا هجرة Alembic، ولا RLS. **مرحلة `data-engineer` تُتخطّى.**

## 5. خطّة الاختبار المقترحة

### 5.1 الحرّاس القائمون، وما يتغيّر منهم

**شُغّلت في الذاكرة على نسخة الفرع من `nginx.conf`** (§3.2.1 + §3.2.3)، بتبديل ثابت المسار في كلّ وحدة، فنجحت الاختبارات بلا وسائط كلّها: `test_edge_hardening.py` (10)، و`test_edge_capacity.py` (12)، و`test_log_correlation.py` (28)، و`test_rolling_deploy.py` (13)، و`test_host_tuning.py` (7).

| الاختبار | يقرأ | يتغيّر؟ |
|---|---|---|
| `tests/unit/test_edge_hardening.py` (`:251` المنطقتان، `:284`، `:299`، `:414` HSTS، المحلّل `:96-140`) | `nginx.conf` و`app-locations.conf` و`deploy/runpod/nginx.conf` | **لا** (AC‑1.3 يشترط ذلك) |
| `tests/unit/test_edge_capacity.py` (`:278` الانجراف بين الحافّتين) | الثلاثة و`docker-compose.yml` | **لا** |
| `tests/unit/test_log_correlation.py` (`:246-311`) | `nginx.conf` و`app-locations.conf` | **لا** |
| `tests/unit/test_release_bundle_guard.py` (`:35-36`، `:280`) | `bundle-files.txt` وCompose | **لا** |
| `tests/unit/test_rolling_deploy.py` و`test_host_tuning.py` و`test_connection_timeouts.py` | `nginx.conf`/`app-locations.conf` | **لا** |
| `tests/unit/test_install_sh.py` و`test_install_sh_config.py` و`test_install_sh_tty.py` | `install.sh` | **لا**. التقرير يزيد سطراً، والاختبارات تبحث بـ`in`. وتشغيلاتها بلا `server.crt`، فلا يُنادى `openssl` |
| `tests/unit/test_install_bundle_docs.py::test_the_bar_check_fails_when_a_count_drifts` | الملخّص | **نعم، في commit الوثائق:** يستبدل `flex:11` الثابت. بعد التغيير يصير `flex:12`، فلا يجد ما يستبدله، ويفشل الحارس الذي يثبت أنّ المقارنة تفشل. والإصلاح: `current = _flex(html, "partial")`، ثمّ إفساد `flex:{current}` إلى `flex:{current + 1}` |
| `tests/unit/test_gpu_preflight.py` و`test_gpu_compose_hygiene.py` و`test_monitoring_profile.py` | `deploy-server.md` والخطّة | **لا**، بشرط قيود §3.6 |

### 5.2 الملفّات الجديدة وتواقيعها

```python
# tests/unit/test_edge_cloudflare.py -- reuses the structural parser (precedent: test_rolling_deploy
# imports from test_api_app_shell); test_edge_hardening.py itself stays untouched (AC-1.3).
from tests.unit.test_edge_hardening import _parse_config

_NGINX: Path          # deploy/nginx/nginx.conf
_LOCATIONS: Path      # deploy/nginx/app-locations.conf
_FORBIDDEN_OVERLAPS: tuple[IPv4Network | IPv6Network, ...]
#   10/8, 172.16/12, 192.168/16, 127/8, 169.254/16, 100.64/10, ::1/128, fc00::/7, fe80::/10
_WIDEST = {4: 12, 6: 28}   # no trusted range wider than /12 (v4) or /28 (v6); Cloudflare's widest
                           # today are /13 and /29 -- a wider one fails the build and gets a human look

def _http_directives(text: str, name: str) -> list[list[str]]: ...   # direct children of `http` only
def _anywhere(name: str) -> int: ...   # count across both files, comments stripped (parser)
def realip_problems(text: str) -> list[str]: ...   # empty == good; the guard the tests share
def hsts_entries(text: str) -> list[tuple[str, str]]: ...   # (host, value) in map order

def test_cloudflare_s_header_is_the_only_real_ip_source_and_sits_in_http() -> None: ...       # AC-1.1
def test_every_trusted_range_is_a_strict_cidr_and_both_families_are_there() -> None: ...       # AC-1.1, NFR-8
def test_no_trusted_range_is_private_loopback_docker_or_too_wide() -> None: ...               # AC-1.1, FR-2
def test_the_list_names_its_source_and_the_day_it_was_copied() -> None: ...                    # FR-3
def test_the_real_ip_guard_fails_on_each_shape_it_exists_for() -> None: ...
#   0.0.0.0/0, ::/0, 172.18.0.0/16, 127.0.0.1, "real_ip_recursive on", "real_ip_header
#   X-Forwarded-For", an empty list, a non-strict CIDR, a set_real_ip_from inside `server` --
#   each on an edited copy, each named by realip_problems()
def test_hsts_names_the_api_host_alone_without_subdomains_or_preload() -> None: ...            # AC-4.1
def test_the_hsts_guard_fails_on_a_second_host_includesubdomains_or_preload() -> None: ...
```

```python
# tests/unit/test_gen_cert.py -- runs deploy/nginx/gen-cert.sh with `sh`, AIZZAK_CERT_DIR=tmp_path,
# and a fake `openssl` first on PATH that records its argv (and, where a test says so, fails).
def _run(cert_dir: Path, *, fake_openssl: Literal["record", "fail-after-key", None]) -> CompletedProcess[str]: ...

def test_both_files_present_are_left_alone_and_openssl_is_never_called() -> None: ...          # AC-2.2: rc 0, sha256 + st_mtime_ns same
@pytest.mark.parametrize("alone", ["server.crt", "server.key"])
def test_one_file_alone_stops_naming_its_partner_and_writes_nothing(alone: str) -> None: ...    # AC-2.3: rc 1, listing unchanged
def test_a_dangling_symlink_counts_as_present() -> None: ...
def test_an_empty_directory_gets_the_development_pair_as_today() -> None: ...                   # real openssl; skip if absent
#   key 0600, cert 0644, SAN DNS:localhost, DNS:app, IP Address:127.0.0.1, no .new leftovers,
#   a second run says "already present"
def test_a_failed_generation_leaves_neither_file() -> None: ...                                 # fake writes .server.key.new, exits 1
def test_the_default_directory_is_the_one_compose_mounts() -> None: ...                         # nginx-certs volume target == /certs
def test_no_tls_material_is_tracked_and_the_directory_stays_ignored() -> None: ...              # AC-2.6
#   git ls-files deploy/nginx/certs '*.key' '*.csr' '*.pem' empty; git check-ignore for
#   server.key, origin.key, origin.csr, .server.key.new; EXCLUDED in test_release_bundle_guard
#   still names deploy/nginx/certs
```

```python
# tests/unit/test_install_sh_tls.py -- the support_install harness; certificates made by the test
# with the host's openssl in tmp_path (a test CA "AIZZAK Test CA" and leaves); skip if no openssl.
def _ca(tmp: Path) -> tuple[Path, Path]: ...                       # ca.crt, ca.key
def _leaf(tmp: Path, ca: tuple[Path, Path], san: str) -> Path: ...  # server.crt for `san`
def _plant(h: Harness, crt: Path | None, *, key_marker: bool = True) -> None: ...
#   copies into h.root/deploy/nginx/certs/; server.key holds a planted marker string, not a key
def _state(h: Harness, *, path: str | None = None) -> str: ...      # run_function: prepare; tls_state

def test_a_full_run_with_the_development_certificate_reports_it_and_exits_0() -> None: ...      # AC-5.1
#   one full run_steps (cpu, --no-monitoring, FAKE_ALL_IMAGES=1, preflight 10); exactly one line,
#   after "step 8/8: report", whose strip() starts "TLS: self-signed development certificate" and
#   names "docs/deploy-server.md §4"; it is the line right after "checks"
def test_a_certificate_for_the_domain_names_issuer_and_expiry() -> None: ...                    # AC-5.2 (a)
def test_a_certificate_for_another_name_warns_and_names_the_domain() -> None: ...               # AC-5.2 (b), report READY rc 0
def test_openssl_never_sees_the_key_and_the_marker_never_reaches_the_output() -> None: ...      # AC-5.3
#   fake openssl on PATH logs argv, then execs the real one; no argv contains "server.key";
#   marker absent from stdout and stderr; and "server.key" not in install.sh's text at all
def test_no_certificate_yet() -> None: ...
def test_an_unreadable_certificate_is_a_warning_not_an_exit() -> None: ...
def test_an_expired_certificate_is_a_warning() -> None: ...    # fake openssl rewrites notAfter to a past date
def test_a_symlinked_certificate_is_not_read() -> None: ...
def test_without_openssl_the_state_is_not_checked() -> None: ...
def test_the_origin_ca_issuer_gets_the_proxied_hint() -> None: ...   # issuer OU "CloudFlare Origin SSL ECC Certificate Authority"
```

```python
# tests/unit/test_cloudflare_tls_docs.py -- text checks in the style of test_install_bundle_docs.py,
# each phrase list also shown failing on an edited copy.
def section_four(doc: str) -> str: ...     # "## 4) " up to the next "## "
def test_the_certificate_steps_are_in_order() -> None: ...                     # AC-2.1
def test_hsts_timing_and_rollback_are_said_in_the_guide_and_the_plan() -> None: ...   # AC-4.4
def test_the_plan_row_step_and_decision_4_say_cloudflare() -> None: ...        # AC-6.1 (+ summary card 6.1 partial)
def test_the_guide_section_has_the_zone_table_limits_and_error_codes() -> None: ...   # AC-6.2 (+ §3.8 no longer says 6.1 is out)
def test_the_three_new_gaps_are_rows_of_the_one_security_table() -> None: ...  # AC-6.3
def test_no_reference_to_the_missing_linux_server_guide_remains() -> None: ... # AC-6.4: deploy/**, docs/design/*.md, docs/*.md
def test_the_two_binding_documents_name_the_origin_certificate_and_its_key() -> None: ...   # AC-6.5
def test_the_524_limit_and_the_sse_advice_are_written() -> None: ...           # AC-7.3
def test_each_phrase_check_fails_on_an_edited_copy() -> None: ...
```

### 5.3 لكلّ معيار

| AC | المستوى | المكان | الطريقة |
|---|---|---|---|
| AC‑1.1 | وحدة | `test_edge_cloudflare.py` | `real_ip_header CF-Connecting-IP` مرّةً واحدة في الملفّين معاً، ابناً مباشراً لـ`http`. وكلّ `set_real_ip_from` ابنٌ لـ`http` وحده: CIDR صارم، والعائلتان حاضرتان، ولا تكرار، ولا تقاطع مع `_FORBIDDEN_OVERLAPS`، ولا أوسع من `_WIDEST`. ولا `real_ip_recursive on`. ورأس القائمة فيه `api.cloudflare.com/client/v4/ips` وتاريخ `YYYY-MM-DD` |
| AC‑1.2 | Cloudflare (المنسّق) | `acceptance.md` | §3.5.2 يوم الدمج |
| AC‑1.3 | وحدة + مراجعة | الملفّات الثلاثة كما هي | تنجح دون تعديل (مقيسٌ مسبقاً، §5.1). و`git diff master -- deploy/runpod docker-compose.yml docker-compose.gpu.yml` فارغ، يكتبه QA |
| AC‑1.4 | حاوية | `test-plan.md` | §5.4 |
| AC‑1.5 | 7.3 | — | §3.5.3 الخطوة 8. **بمسارٍ فريد لا بـ`/health`:** `/health` بلا سجلّ وصول (`app-locations.conf:133`)، فلا سطر يُقارن. فيُطلب مثلاً `https://api.aizzak.com/__cf_probe_<random>` (404 من التطبيق، وسطره في السجلّ)، ثمّ `cdn-cgi/trace` |
| AC‑1.6 | 7.3 | — | Should. وانظر §7 (حماية DDoS في Cloudflare) |
| AC‑2.1 | نصّ | `test_cloudflare_tls_docs.py` | مواضع `openssl req -new` ثمّ «Use my private key and CSR» ثمّ `-checkend 31536000` (في خطوة «تحقّق قبل التفعيل») ثمّ `origin.crt deploy/nginx/certs/server.crt` ثمّ `nginx -s reload` ثمّ `up -d nginx-certs`، تصاعديّة في §4. وكلمة «أضعف» مع الطريق البديل |
| AC‑2.2 | وحدة | `test_gen_cert.py` | `test_both_files_present_…` |
| AC‑2.3 | وحدة | `test_gen_cert.py` | `test_one_file_alone_…` بالحالتين |
| AC‑2.4 | 7.3 (مالك) | — | §3.5.3 الخطوتان 4 و7 |
| AC‑2.5 | 7.3 | — | §3.5.3 الخطوة 8 |
| AC‑2.6 | وحدة | `test_gen_cert.py` (+ `test_release_bundle_guard.py` القائم) | `test_no_tls_material_is_tracked_…` |
| AC‑2.7 | 7.3 | — | §3.5.3 الخطوة 9 |
| AC‑3.1 | Cloudflare (المنسّق) | `acceptance.md` | `snap before-1` (§3.5) قبل البند 1، ومثلها قبل كلّ بند |
| AC‑3.2 | غير مطلوب | — | Q‑3 (أ) |
| AC‑3.3 | 7.3 (Cloudflare) | — | §3.5.3 الخطوة 5 |
| AC‑3.4 | 7.3 | — | 526 في الخطوة 5، ثمّ 200 في الخطوة 8 |
| AC‑3.5 | 7.3 | — | الخطوة 8 |
| AC‑3.6 | 7.3 (ونصف المنطقة الآن) | — | مسبار 1.1 على `aizzak.com` في البند 4 (§3.5.1)، والثلاثة على `api.aizzak.com` في الخطوة 8 |
| AC‑3.7 | Cloudflare (المنسّق) | `acceptance.md` | `probe.txt` قبل كلّ بندٍ وبعده، متساوٍ |
| AC‑3.8 | 7.3 | — | الخطوة 8 |
| AC‑4.1 | وحدة | `test_edge_cloudflare.py` | `hsts_entries` = `[("default", '""'), ("api.aizzak.com", "max-age=31536000")]` بالضبط، ولا `includeSubDomains` ولا `preload` في قيم الخريطة. والحارسان القائمان في `test_edge_hardening.py` باقيان |
| AC‑4.2 | حاوية | `test-plan.md` | §5.4 |
| AC‑4.3 | 7.3 | — | الخطوة 8، و`security_header` في صورة 7.3 كما في `before-1` |
| AC‑4.4 | نصّ | `test_cloudflare_tls_docs.py` | في §4 من الدليل وفي نصّ 6.1: «HSTS» و«DNS» و«7.3»، وفي الدليل `max-age=0` |
| AC‑5.1 | وحدة | `test_install_sh_tls.py` | التشغيل الكامل الواحد |
| AC‑5.2 | وحدة | نفسه | `report READY` عبر `run_function` (0.1 ث)، بـ`server.crt` من CA الاختبار لـ`example.com` ثمّ لـ`other.example` |
| AC‑5.3 | وحدة | نفسه | `openssl` المسجِّل، والعلامة المزروعة، والحارس الثابت على نصّ السكربت |
| AC‑5.4 | وحدة | `test_install_sh.py` و`test_install_sh_config.py` و`test_install_bundle_docs.py` | كما هي، وتنجح. وجدول الرموز يحرسه `test_the_header_and_the_document_list_the_same_exit_codes` |
| AC‑6.1 | نصّ | `test_cloudflare_tls_docs.py` (+ الحارس القائم للشريط) | صفّ 6.1 بلا ⬜ وفيه «7.3». وصفّ قرار 4 فيه «Cloudflare» و«2026‑10‑20». ونصّ 6.1 فيه «Origin CA» و«Full (strict)» و`-checkend 31536000`. وبطاقة ٦٫١ `step partial` وفيها «Cloudflare» |
| AC‑6.2 | نصّ | نفسه | في §4: `521` و`522` و`524` و`525` و`526`، و«100»، و`api.cloudflare.com/client/v4/ips`، و`ping`، و`CF-Connecting-IP`. و§3.8 لا يقول «يبقى ما يولّده `nginx-certs` ذاتيّ التوقيع» |
| AC‑6.3 | نصّ | نفسه | ث‑13 وث‑14 وث‑15 مرّةً واحدة لكلٍّ منها، و`count("|") == 5`. ث‑13 فيه `?token=`، وث‑14 فيه «6.3» و«Authenticated Origin Pulls»، وث‑15 فيه «Vault» و`server.key`. والاتّصال يحرسه `test_every_security_row_sits_in_one_contiguous_table` |
| AC‑6.4 | نصّ | نفسه | لا «deploy-linux-server» في نصوص `deploy/` و`docs/design/*.md` و`docs/*.md` |
| AC‑6.5 | نصّ | نفسه | `08 §3.2` فيه «Origin CA» و`deploy-server.md`. و`05 §3` فيه `server.key` و«Vault» |
| AC‑7.1 | 7.3 | — | §3.5.3 الخطوة 11 |
| AC‑7.2 | 7.3 | — | كذلك |
| AC‑7.3 | نصّ | `test_cloudflare_tls_docs.py` | في §4: `524` و`runWorkflow` و«SSE» |
| AC‑7.4 | 7.3 | — | كذلك |
| AC‑7.5 | 7.3 | — | كذلك (معلومة لا بوّابة) |

**الحصيلة:** 10 بالوحدة، و8 بالنصّ (`pytest` كلّها)، و2 بالحاوية، و3 للمنسّق في Cloudflare قبل 7.3، و15 في 7.3، و1 غير مطلوب. المجموع 39، ويطابق ملخّص `stories.md`: 20 في المستودع، و3 في Cloudflare الآن، و15 تنتظر 7.3.

### 5.4 فحوص الحاوية (AC‑1.4، AC‑4.2، NFR‑1؛ يشغّلها QA ويكتبها في `test-plan.md`)

العُدّة كما قيست هنا، والنصّ في `/tmp/claude-1000/-home-AIZZAK/81328ec9-6c6d-4155-a4e6-26b05fb3b1f3/scratchpad/edge/run.sh`:
- `docker run --rm --network none -v <scratch>:/work:ro --entrypoint /bin/sh nginx:1.27 /work/run.sh`.
- لا منفذ منشور، ولا شبكة المكدّس، فلا يُلمس المكدّس الحيّ. وتُوقف الحاوية بمعرّفها وحده إن علقت.
- شهادةٌ ذاتيّة تولّدها الحاوية.
- **سطران للعُدّة لا غير، ويُطبَّقان على نسختي `master` والفرع بالتساوي:**
  - `set $aizzak_app "127.0.0.1:8000";` بدل `app:8000`: لا محلِّل DNS بلا شبكة، وإلّا انتظر كلّ طلبٍ 30 ثانية.
  - و`server { listen 127.0.0.1:8000; … return 200 "xri=$http_x_real_ip xff=$http_x_forwarded_for"; }` قبل `:80`.
- **AC‑1.4:** `nginx -t`. ثمّ طلبٌ بـ`CF-Connecting-IP: 203.0.113.9` من مصدرٍ ليس من Cloudflare: `remote_addr` في السجلّ و`xri=` هما عنوان المرسل. وصيغة «حاويةٌ أخرى على الشبكة نفسها»: شبكةٌ مؤقّتة `docker network create cftls-check` لحاويتين، ثمّ `docker network rm cftls-check`.
- **AC‑4.2:** خمس قيم `Host` × `:80` و`:443` (قيس هنا، §3.2.3). والترويسات الأمنيّة الثلاث وCORS مع `Origin: https://aizzak-agent.web.app`.
- **NFR‑1:** النسختان، وبـ`Host: localhost`: `/health` 200، و`/metrics` 404، و`/health/drain` 404، و`OPTIONS /` 204. ومجموعة الترويسات وقيمها متساوية ما عدا `Date`. و`gen-cert.sh` مع الملفّين: لا كتابة (AC‑2.2 يثبته بالوحدة).

### 5.5 الزمن (NFR‑6: ≤ 5 ث زيادة)

**مقيس:** تشغيلٌ كامل لـ`install.sh` بالعُدّة نحو 1.9 ث (شجرةٌ 0.24 ث، وتشغيلٌ 1.65 ث)، و`report READY` عبر `run_function` نحو 0.1 ث.

**تقدير:**
- تشغيلٌ كامل واحد 1.9 ث.
- ونحو ثمانية نداءات `tls_state`/`report` نحو 0.9 ث.
- وتوليد شهادات EC للاختبار نحو 0.2 ث.
- و`gen-cert.sh` (منه توليد RSA 2048 حقيقيّ واحد) نحو 0.5 ث.
- والقراءات النصّيّة أقلّ من 0.2 ث.

المجموع نحو **3.7 ث**. ويقيس QA بـ`pytest tests/unit --durations=0` قبل وبعد.

## 6. ترتيب التنفيذ والتوازي

الفرع `feat/cloudflare-tls` من `master` (`28512bb`). كاتبٌ واحد، فالتنفيذ متتابع. لكلّ commit البوّابات الخمس خضراء، و`deploy/prometheus/test-rules.sh` لا يلزم (لا تنبيه). وفي ذيل كلّ رسالة سطور الإسناد التي تحدّدها الجلسة.

| # | Commit | الملفّات | يعتمد على |
|---|---|---|---|
| C1 | `feat(edge): take the client address from CF-Connecting-IP for Cloudflare ranges only` | `nginx.conf` (§3.2.1 وجملة التعليق)، و`test_edge_cloudflare.py` (أجزاء AC‑1.1) | — |
| C2 | `feat(edge): send HSTS for api.aizzak.com only and drop the dangling guide reference` | `nginx.conf` (§3.2.3، §3.2.4)، و`test_edge_cloudflare.py` (AC‑4.1) | C1 (الملفّ نفسه) |
| C3 | `fix(edge): stop nginx-certs from overwriting half of an operator's TLS pair` | `gen-cert.sh`، و`test_gen_cert.py` | — |
| C4 | `feat(install): report the state of the edge certificate` | `install.sh`، و`test_install_sh_tls.py` | — |
| C5 | `docs(cloudflare-tls): origin certificate guide, three security gaps and step 6.1 status` | `deploy-server.md`، والخطّة، والملخّص، و`08`، و`05`، و`test_cloudflare_tls_docs.py`، و`test_install_bundle_docs.py` | C1…C4 (النصوص المختبرة)، ونتائج §3.5.1 لعمود «من ومتى» |

- **إحالةٌ إلى الأمام مقبولة:** تعليقات C1 وC2 وسطر C4 تشير إلى `docs/deploy-server.md` §4، ولا يوجد القسم قبل C5. والـPR يُدمج كاملاً.
- **بالتوازي مع C1…C4:** المنسّق يطلب موافقة المالك على البنود الأربعة في §3.5.1 بنداً بنداً، ويطبّقها، ويعطي المطوّر تواريخها لـC5. وإن لم تُطبَّق بعد، فعمود «من ومتى» يقول «لم يُطبَّق بعد»، ويُحدَّث في commit الإغلاق.
- **بعد C5:**
  - QA: البوّابات، و`--durations`، وفحوص §5.4، و`git diff` لـAC‑1.3 وNFR‑1.
  - ثمّ مراجعة الشيفرة والمراجعة الأمنيّة.
  - ثمّ يوم الدمج: AC‑1.2 (§3.5.2).
  - ثمّ commit الإغلاق: صفّ 6.1 «مدموجة»، والملخّص.
  - ثمّ وسم إصدار، وبعده 7.3 (§3.5.3).

## 7. المخاطر والقرارات

### بحاجةٍ إلى قرارٍ بشريّ قبل البناء

**لا بند ⚠️.** الملفّات المحميّة الثلاثة كلّها في قائمة Q‑12، ولا شيء خارجها، ولا انحراف عن المتطلّبات. **والموافقات المطلوبة لا تحجز البناء،** وسياستها محسومة في Q‑3 وQ‑12: موافقة المالك على كلّ تغييرٍ في Cloudflare بعينه. وهي سبع:
- الأربعة في §3.5.1، الآن.
- وسجلّ DNS، وإصدار شهادة المنشأ، وإبطال الأولى في التمرين، في 7.3.

### قراراتٌ اتّخذها التصميم (تُراجَع في المراجعة)

1. **قواعد Cloudflare الثلاث قبل الدمج،** وقبل سجلّ DNS حتماً: خاملةٌ اليوم، وتمنع لحظة (أ0) في 7.3 (§3.5.1).
2. **أدنى TLS 1.2 الآن:** مستقلٌّ عن الخادم ومقيسٌ على `aizzak.com`. ويمسّ موقع الجذر الحيّ، وهذا ما وافق عليه المالك في Q‑3. والرجوع بنداءٍ واحد.
3. **التحويل 308** لا 301 (§3.5.1).
4. **ECDSA P‑256** لمفتاح المنشأ (§3.7، A‑3).
5. **`gen-cert.sh`:** متغيّرٌ للاختبار (`AIZZAK_CERT_DIR`) لا يضعه Compose، والكتابة باسمين مؤقّتين. فالقاعدة الجديدة لا ترفض أثر فشلها هي.
6. **`openssl` اختياريّ في `install.sh`،** لا أداةٌ إلزاميّة: لا رمز 11 جديد، وسطرٌ يقول إنّه لم يُفحص.
7. **AC‑1.5 بمسارٍ فريد بدل `/health`:** `/health` لا يُسجَّل، فلا سطر يُقارن. هذا تصحيحٌ لطريقة القياس لا لغرض المعيار، ويعرفه PO وQA.
8. **لا حقل `$realip_remote_addr` في سجلّ الوصول** (أقلّ فرق). فالسجلّ لا يميّز عميلاً عبر Cloudflare من عميلٍ يصل الأصل مباشرة. يُقترح مع 6.3.
9. **الحارس يمنع نطاقاً أوسع من /12 أو /28.** إن نشر Cloudflare نطاقاً أوسع يوماً، فشل البناء ونظر فيه إنسان.

### مخاطر

- **حزمةٌ قبل الدمج لا تحمل 6.1.** يلزم وسمٌ بعد الدمج وقبل 7.3 (§3.5.3).
- **`nginx.conf` مربوطٌ ملفّاً:** تحديث قائمة النطاقات على الخادم يحتاج `docker compose restart nginx`، لا `reload` (§3.6، §4.5 من الدليل). وعلى جهاز التطوير كذلك، لالتقاط النصّ الجديد، والسلوك مساوٍ.
- **قائمة نطاقات Cloudflare تتغيّر:** نطاقٌ جديد غير مدرج يجعل عملاءه يتقاسمون حدّاً واحداً حتّى التحديث (AC‑1.2 من جديد).
- **حفظ عنوان المصدر على الخادم:** AC‑1.5 يفترض أنّ Docker يحفظ عنوان المصدر الخارجيّ عبر DNAT، كما قيس على هذا الجهاز (`nginx.conf:191-203`). وDocker بلا root (rootless) أو وكيلٌ أمام الأصل يكسرانه. فيُتحقَّق في 7.3 قبل AC‑1.6.
- **الأصل يُبلَغ مباشرةً** حتّى 6.3 (ث‑14). **وCloudflare يرى النصّ الصريح** (ث‑13).
- **CORS:** إن كانت الواجهة على `https://aizzak.com`، فطلباتها إلى `api.aizzak.com` تُرفض حتّى التعديل المنفصل (Q‑10).
- **Bot Fight Mode في الخطّة المجّانيّة لا يُستثنى منه اسمٌ بقاعدة.** وثائق Cloudflare، غير مقيس. فإن تحدّى عميل API في 7.3، فالخيار المجّانيّ إطفاؤه للمنطقة كلّها، وهذا قرارٌ جديد للمالك حينها (A‑11).
- **AC‑1.6 (800 طلب في الثانية من عميلٍ واحد)** قد يستثير حماية DDoS في Cloudflare قبل nginx. يُسجَّل إن حدث.
- **`concat()` في هدف التحويل** من أمثلة Cloudflare، ولم يُجرَّب هنا. والرفض لا يغيّر شيئاً، والبديل مكتوب (§3.5.1).
- **طلبات Cloudflare Workers** تصل من نطاقات Cloudflare بعنوان خروج Worker، فتتقاسم حدّاً واحداً: هذه حالة العنوان الثابت عبر المناطق وحدها. والأوسع (SR‑1): حدّ الثقة هو كلّ عملاء Cloudflare، وWorker في منطقةٍ أخرى يختار `CF-Connecting-IP`، فالأثر على حدّ المعدّل لكلّ عنوان وعلى السجلّ، ولا يُبنى قبل 6.3 `allow`/`deny` على `$remote_addr`. ومعروفٌ ومؤجَّل إلى 6.3 (SR‑3): بعد `realip` يأخذ عملاء IPv6 مفتاحاً لكلّ /128، فشبكة /64 واحدة تستطيع أخذ مفاتيح كثيرة. ويُذكر في §4.5 من الدليل. والإغلاق التقني في 6.3.
- **WebSocket الخامل وردٌّ مجمَّع فوق 100 ثانية** قد يُقطعان (US‑7، 7.3).

</div>
