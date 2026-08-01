# refs/auth-firebase.md — مصادقة Firebase وتحقّق التوكن (مرجع من `alpha`)

> **الوجهة:** `02-port-contracts.md §1.10` (`AuthProvider`) · `architecture.md §11` (معمارية الأمن) · `D‑25` (تحقّق JWT محلي بمفاتيح مُخزّنة) · `05-rbac-config-secrets.md §1.4/§3` · `03-api-spec.md §4` (رموز `auth.*`) · المرحلة 6.4 (حرّاس RBAC).
> **النطاق:** كيف يتحقّق `alpha` من هوية Firebase، وما الذي ينتقل (دلالات/مطالبات) مقابل ما يُعاد بناؤه (النقل عبر Admin SDK + الحالة العامة + كاش النتيجة).
> **مصادر `alpha`:** `shared/auth.py` (103 سطراً) · `integrations/firebase_admin_config.py` · `services/cache.py §auth` · `routers/{users,admin,agents}.py` · `shared/deps.py` · `requirements.txt` · `fix/PRODUCTION_AUDIT_EN.md`.
> **حُصد:** 2026‑07‑16 (المرحلة 2.7 — 🔍 Harvester؛ خارج حصاد المرحلة 0 الأصلي الذي لم يشمل المصادقة).

## 0) واقع `alpha` (السياق)
- **النقل:** **Firebase Admin SDK** حصراً — `firebase_admin.auth.verify_id_token(token)` (`shared/auth.py:41`). **لا فكّ JWT يدوي في أي مكان** (`PyJWT` مُثبَّت في `requirements.txt:87` لكنه **غير مستورَد إطلاقاً** في كود `alpha` — تثبيت أثري).
- **الاعتماد:** ملف حساب خدمة محلّي `credentials.Certificate(path)` عبر `FIREBASE_SERVICE_ACCOUNT_KEY` (افتراضي `serviceAccountKey.json`). **لا ADC، ولا `FIREBASE_PROJECT_ID`** — معرّف المشروع مدفون داخل ملف حساب الخدمة (`project_id = "aizzak-agent"`).
- **التفويض ليس في التوكن:** لا مطالبات مخصّصة (`custom_claims`) إطلاقاً؛ `is_admin`/`is_disabled` أعمدة في جدول `users` المحلي تُقرأ من DB **كل طلب**.

## 1) ميكانيكا التحقّق (`shared/auth.py:22‑46`)
```python
decoded = get_firebase_auth().verify_id_token(token)     # auth.py:41 — النداء الوحيد في المشروع
```
**المعاملات المُمرَّرة: لا شيء** ⇒ الافتراضات النافذة فعلياً (مؤكَّدة من مصدر الحزمة `firebase_admin/auth.py:200`):
`check_revoked=False` · `clock_skew_seconds=0` (**صفر تسامح زمني**).

**نداء الشبكة:** طبقتا كاش **لا تطابق أيّهما هدف `D‑25`**:
1. **كاش شهادات داخل SDK** (سلوك مكتبة لا كود `alpha`): `cachecontrol.CacheControl(requests.Session())` فوق `ID_TOKEN_CERT_URI = https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com` (`_token_gen.py:37,245‑251`) — **صيغة X.509 لا JWK**؛ TTL يمليه رأس `Cache-Control` من Google (**غير قابل للضبط ولا موثَّق**)؛ لكل عملية على حدة (5 عمّال خلف nginx ⇒ 5 كاشات مستقلة). أول تحقّق في كل عملية = نداء شبكي حقيقي.
2. **كاش «نتيجة التحقّق» في Redis** (`services/cache.py:82‑124`, `AUTH_TOKEN_CACHE_TTL=300`، مفتاح `sha256(token)` — التوكن الخام لا يُخزَّن): عند الإصابة **يُتخطّى `verify_id_token` كلّياً** (`auth.py:36‑39`) — **لا فحص توقيع محلي حتى**.

> **⚠️ الفارق الحاكم:** `alpha` تكاش **النتيجة** (⇒ نافذة إبطال/إعادة تشغيل 0–300ث)، بينما `D‑25`/`FIREBASE_JWKS_CACHE_TTL` يطلبان كاش **المفاتيح العامة فقط** مع **إعادة تحقّق التوقيع/الانتهاء في كل نداء**. **نقل شكل هذا الكاش يُعيد إدخال ثغرة إبطال تحت تصميم لا يريدها.**

- **علّة مفتوحة مُقرّة في `alpha` نفسها — M3** (`fix/PRODUCTION_AUDIT_EN.md:341‑345`، **بلا وسم «FIXED»** خلافاً لجيرانها): «التوكن المُبطَل يبقى صالحاً حتى ~ساعة؛ تعطيل الحساب يُفرَض عبر DB فيخفّف جزئياً».
- **`_LEEWAY = 10`** (`cache.py:84`) ليس تسامح `exp` بل حساب TTL للكاش: `min(300, exp - now - 10)`.

## 2) التهيئة والحالة العامة (`integrations/firebase_admin_config.py`)
```python
_initialized = False                       # :5 — حالة عامة على مستوى الوحدة
def get_firebase_auth():                   # :7 — كسول، أوّل نداء يفوز
    global _initialized
    if not _initialized:                   # :9 — فحص‑ثمّ‑ضبط بلا قفل
        ...; firebase_admin.initialize_app(cred); _initialized = True
    return firebase_auth                   # يعيد **الوحدة** لا عميلاً
```
- **كسول لا عند الاستيراد** ⇒ غياب ملف المفتاح لا يُسقط الإقلاع بل يُفشل أوّل طلب مُصادَق (`RuntimeError`→500).
- **سباق حقيقي (ضيّق):** الدوال كلها `def` متزامنة (تعمل في thread pool لـFastAPI)؛ فحص `_initialized` بلا قفل ⇒ طلبان متزامنان على عامل بارد قد ينفّذان `initialize_app` مرّتين ⇒ `ValueError: The default Firebase app already exists`.
- **طبقتا حالة عامة:** علم `_initialized` + سجلّ التطبيق الافتراضي داخل `firebase_admin` نفسه.

## 3) المطالبات المُستهلَكة فعلياً (مسح شامل)
```python
uid = claims.get("uid") or claims.get("user_id") or claims.get("sub")   # auth.py:73 · users.py:26
email = claims.get("email") or user.email                               # users.py:27 (جسم الطلب احتياطٌ لا مصدر ثقة)
```
- **`email_verified` · `name` · `picture` · أي مطالبة مخصّصة: لا تُقرأ إطلاقاً** (صفر إصابات grep) ⇒ عقد `Identity{firebase_uid, email, email_verified, claims}` الجديد **superset** لما احتاجته `alpha`؛ لا شيء يُهاجَر لأجل `email_verified`.
- **سلسلة الاحتياط `uid or user_id or sub`** مصطنعة لتطبيع Admin SDK؛ **الفكّ اليدوي يُظهر `sub` الخام** (وهو مطالبة JWT القياسية) ⇒ في الجديد **`sub` هو القراءة الأساسية**.
- **الشكل المُعاد:** `get_token_claims` → dict خام غير مُنمَّط؛ `get_current_user` → **كائن ORM منفصل** (`s.expunge(user)`, `auth.py:80`) كي لا يحجز اتصال تجمّع أثناء نداء LLM يمتدّ 45–120ث (حيلة SQLAlchemy متزامن — **غير ذات صلة** بعقد يعيد `Identity` بلا اقتران DB).

## 4) سطح الحارس وخريطة الأخطاء (`shared/auth.py`)
**الربط:** معامل `Header(default=None)` عادي باسم `authorization` — **لا `HTTPBearer`** ⇒ `docs/openapi.json` **بلا `securitySchemes`/`bearerAuth` إطلاقاً**، والرأس يظهر `"required": false` رغم أنه إلزامي فعلياً (لا قفل «Authorize» في Swagger).

| الحالة | الرمز | الرسالة | السطر |
|---|---|---|---|
| رأس مفقود/لا يبدأ بـ`bearer ` (غير حسّاس للحالة) | 401 | `Missing or malformed Authorization header` | `:28‑29` |
| توكن فارغ بعد `Bearer` | 401 | `Empty bearer token` | `:32` |
| **أيّ استثناء** من `verify_id_token` (توقيع·انتهاء·تشوّه·`aud` خطأ·**فشل جلب الشهادات**) — `except Exception:` عارٍ | 401 | `Invalid authentication token` (تعليق صريح: «لا تُسرِّب السبب») | `:40‑44` |
| لا صفّ `User` محلي للـ`uid` المُتحقَّق | **404** | `User not found` | `:76‑77` |
| `is_disabled=True` | 403 | `Account disabled` | `:78‑79` |
| `is_admin=False` في `get_current_admin` | 403 | `Admin access required` | `:88‑89` |
| `require_owned_agent`: المورد غير موجود | **404** | `Agent not found` | `:99‑100` |
| `require_owned_agent`: موجود لكن `user_id` مختلف | 403 | `Forbidden` | `:101‑102` |

- **فصل 401/403 نظيف ومقصود:** 401 = «لا نعرف من أنت» (طبقة التوكن)؛ 403 = «نعرفك، والجواب لا». **و404 قبل 403** على فحص الملكية مقصود بتوثيق صريح (`:96‑97`) كي لا يستكشف غريبٌ أيّ المعرّفات موجودة.
- **حارسان بطبقتين:** الهوية عبر `Depends()`؛ **ملكية المورد نداء دالة يدوي داخل جسم المسار** (`agents.py:66,72,91,209,225,235,250`) لا Dependency.

## 5) منافذ التطوير/الاختبار — **الغياب نتيجة**
**لا شيء.** مسحٌ شامل لـ`AUTH_DISABLED|DEV_MODE|FAKE_AUTH|MOCK_AUTH|bypass|dev_uid|skip.*auth` ⇒ **صفر** مسارات التفاف: لا علم بيئة يعطّل المصادقة، ولا uid تطويري مُصلَّب، ولا `if env == "dev"`. (إصابات `bypass` الوحيدة نثر غير ذي صلة.)

**لكن — تغطية اختبارية صفرية للمنطق نفسه:** `tests/test_admin_auth.py` **استبطان لشجرة التبعيات فقط** («لا HTTP/لا Firebase/لا DB» — نصّ توثيقه) يؤكّد أن `get_current_admin` مُعلَّق على المسار الصحيح، ولا يُشغّل `_verify_bearer` قط. صفر إصابات لـ`dependency_overrides|verify_id_token|monkeypatch.*auth` في `tests/` ⇒ **لا سلوك مرجعي مُختبَر للحالات الحديّة** (الجدول أعلاه مقروء من المصدر لا من تأكيدات).

## 6) اقتران البذر JIT (منفصل — سابقة مفيدة)
- `get_token_claims` (`:49‑54`) = **تحقّق فقط، بلا لمس DB** — يستخدمه `POST /users/` حصراً (الصفّ المحلي قد لا يوجد بعد).
- **البذر نفسه في الموجّه لا في وحدة المصادقة** (`routers/users.py:17‑54`): بحث بـ`firebase_uid` → احتياط بحث بـ`email` (**إعادة توجيه `firebase_uid` على الصفّ القائم** — حساب Firebase جديد بنفس البريد) → إنشاء؛ `is_admin = (email.lower() == FIRST_ADMIN_EMAIL.lower())` (`shared/deps.py:8`، بريد مُصلَّب — **قاعدة الترقية التلقائية الوحيدة**). فحص `is_disabled` **مُكرَّر** هنا لأن المسار لا يمرّ بـ`get_current_user`.
- `get_current_user` يفترض وجود الصفّ (404 وإلا) — **لا يُنشئ**.

## 7) أنماط فشل/حواف (تُقلَّد أو تُتجنَّب)
- **⚠️ ابتلاع أخطاء البنية التحتية:** `except Exception:` يطوي `CertificateFetchError` (**فشل شبكي عابر عند Google**) في **نفس** 401 «توكنك غير صالح» — **انقطاع Google يبدو كتوكن فاسد** لكل مستدعٍ: لا 503، لا إعادة محاولة، لا إشارة مميّزة. **لا منطق إعادة محاولة في كود `alpha` إطلاقاً** (مُفوَّض لسلوك `requests`/`cachecontrol` الافتراضي = لا شيء).
- **تسريب PII/أسرار: لا شيء** — لا التوكن الخام ولا المطالبات المفكوكة تُمرَّر لمسجّل/طباعة في وحدتَي المصادقة. (`users.py:95`/`admin.py:158` يسجّلان `uid` — معرّف مبهم — عند فشل حذف.)
- **تعدّد المستأجرين: نظيف** — الهوية دائماً من التوكن المُتحقَّق، **لا من مسار/استعلام/جسم** (`auth.py:5‑6`)؛ التوثيق نفسه يذكر أن هذه الوحدة **حلّت محلّ** نمط أقدم بـ`uid` مُصرَّح ذاتياً و`admin_uid` كمعامل استعلام — نمط مضادّ مُصلَح، لا يُعاد إدخاله.
- **حذف الحساب (خارج نطاق `verify_token`):** `auth.delete_user()` best‑effort **بعد** حذف الصفّ المحلي المُلتزَم، وفشله يُسجَّل تحذيراً بلا ارتداد ⇒ حساب Firebase يتيم ممكن.

## 8) التبعيات (وقائع مُتحقَّقة)
| الحزمة | في `requirements.txt` | يستوردها كود `alpha` | ملاحظة |
|---|---|---|---|
| `firebase-admin` | ✅ `>=6.0.0` (`:77`) | ✅ الآلية كلّها | النقل الوحيد |
| `PyJWT` | ✅ `>=2.0.0` (`:87`) | ❌ **صفر استيراد** | تثبيت أثري — لا فكّ يدوي في `alpha` |
| `python-jose` | ❌ | ❌ | غير موجود |
| `google-auth` | ✅ `>=2.15.0` (`:71`) | ✅ لكن لـ**Gmail OAuth** لا لمسار Firebase (يعتمده SDK ضمنياً) | |
| `cryptography` | ❌ سطر مباشر | ضمنياً (`49.0.0`) خلفيةَ RSA تحت `google-auth` | داخل SDK |

**متغيّرات البيئة (أسماء فقط):** `FIREBASE_SERVICE_ACCOUNT_KEY` (مسار JSON، افتراضي `serviceAccountKey.json`) · `AUTH_TOKEN_CACHE_TTL` (ث، افتراضي 300، `0` يعطّل). **لا `FIREBASE_PROJECT_ID`** ولا `GOOGLE_APPLICATION_CREDENTIALS`.

**سطح Admin SDK المُستخدَم كاملاً:** `verify_id_token` (`auth.py:41`) · `delete_user` (`users.py:93`, `admin.py:155`). **لا** `create_user`/`get_user`/`set_custom_user_claims`/`create_custom_token`/`verify_session_cookie`.

## 9) الحكم: يُعاد استخدامه · يُعاد بناؤه · **لا يُنقل**
| البند | الحكم |
|---|---|
| المطالبات المُستهلَكة (`sub`/`uid` + `email` فقط) وسلسلة الاحتياط | **يُعاد استخدامه** — يثبت أن `Identity` الهدف superset؛ في الجديد `sub` أساسي (لا تطبيع SDK) |
| فصل 401/403، رسالة 401 عامة «لا تُسرِّب السبب» | **يُعاد استخدامه** (موقف أمني سليم) — **لكن** لا تطوِ فشل البنية التحتية معه (انظر أدناه) |
| فحص `is_disabled`/حالة الحساب طازجاً كل طلب بلا كاش | **يُعاد استخدامه** — ضابط تعويضي يبقى صالحاً مهما كوشِفت المفاتيح |
| فصل «تحقّق نقي» (`get_token_claims`) عن «تحقّق+DB» (`get_current_user`) | **يُعاد استخدامه كسابقة** — و`AuthProvider.verify_token` يكون **بنقاء النصف الأنقى** (صفر DB) |
| نقل Admin SDK (`verify_id_token`، نداء أول‑لكل‑عملية، TTL شهادات مبهم) | **مرجع سلوكي فقط** — يُستبدَل بـJWKS محلي (`D‑25`) |
| كاش نتيجة التحقّق في Redis بمفتاح hash التوكن (300ث) | **❌ لا يُنقل** — شكل كاش خاطئ للعقد؛ يُعيد نافذة الإبطال/الإعادة |
| `_initialized` عام + `initialize_app` كسول بلا قفل | **❌ لا يُنقل** — حالة عامة + سباق؛ التصميم ينصّ «لا globals» |
| `Header(default=None)` بدل مخطّط أمان OpenAPI | **❌ لا يُنقل** — يفسد وفاء عقد OpenAPI (`AC‑07`؛ شأن م6.4) |
| `check_revoked=False` · `clock_skew_seconds=0` | **قرار مفتوح للجديد** — لا يُورَث صامتاً؛ يُوثَّق صراحةً |
| ترقية admin تلقائياً ببريد مُصلَّب · بذر JIT بإعادة ربط البريد | **مرجع فقط** — سياسة بذر/تفويض، خارج `AuthProvider` (وفي الجديد: `access` بكتالوج ثابت + بذر خارج API `05 §1.5`) |
| كائن ORM منفصل (`s.expunge`) | **غير ذي صلة** — حيلة SQLAlchemy متزامن؛ العقد يعيد `Identity` بلا DB |

## 10) مخاطر/أسئلة مفتوحة للجديد

> **✅ حُسمت كلّها في المرحلة 2.7 (2026‑07‑16) — لا تُعاد مقاضاتها.** التفصيل في [`implementation-status.md §3.22`](../../implementation-status.md): (1) الإبطال → **خارج v1 صراحةً** (يناقض `D‑25`؛ الضابط `is_disabled` طازجة كل طلب؛ نقطة التوسعة = قائمة منع `auth:revoked:<sub>` في حارس م6.4) · (2) فصل فشل البنية عن التوكن الفاسد → **مُطبَّق** (شجرة PyJWT ترسمه: `InvalidTokenError`→401، وكل ما عداها→500) · (3) TTL محدَّد → **مُطبَّق** + `_MIN_REFRESH_INTERVAL_S=60` لتقييد التجديد · (4) النقطة → **JWK** (تربط `PyJWK` الخوارزمية بنيوياً) · (5) `FIREBASE_PROJECT_ID` → **ما زال غير مستوثَق** (لم يُوضَع في `.env.example`) لكنه صار **إلزامياً عند الإقلاع**. وبما أن v1 حسم مصدر المفاتيح إلى **JWKS العامة**، فـ**لا حساب خدمة Firebase يُجهَّز أصلاً** ⇒ لا فعل مطلوب من جهة AIZZAK تجاه `serviceAccountKey.json` (تدويره شأن `alpha` وحدها).
1. **الإبطال (M3):** `check_revoked=True` يتطلّب نداءً شبكياً لكل فحص ⇒ **يناقض «لا نداء لكل طلب»**. إن لزم إبطال فوري فآليته المتوافقة قائمة منع قصيرة العمر (`CacheProvider`) — **قرار تصميمي صريح مطلوب، لا وراثة صامتة**.
2. **تمييز فشل البنية عن التوكن الفاسد:** إن فشل جلب/تحديث JWKS ⇒ **5xx لا 401** (علّة `alpha` §7). *(نفس عائلة اكتشاف 2.6: «الصمت أسوأ من 500».)*
3. **TTL محدَّد بدل مبهم:** `FIREBASE_JWKS_CACHE_TTL=3600` **ترقية** عن سلوك `cachecontrol` غير الموثَّق في `alpha`، لا نكوص.
4. **نقطة النهاية:** `alpha` تلمس صيغة **X.509** حصراً (داخل SDK)؛ تنفيذ JWKS من الصفر يستهدف طبيعياً صيغة **JWK** (`.../service_accounts/v1/jwk/securetoken@system.gserviceaccount.com`) — **`alpha` لا تُشغّلها إطلاقاً** (معرفة عامّة لا سلوك مرجعي حامل).
5. **`FIREBASE_PROJECT_ID`:** القيمة المرشّحة `"aizzak-agent"` (حقل `project_id` غير السرّي داخل ملف حساب الخدمة) — **تُستوثَق من وحدة تحكّم Firebase** قبل الاعتماد.

## ⚠️ ملاحظة أمنية (سابقة مفتاح Exa في حصاد م0)
- **`/home/alpha/serviceAccountKey.json`** — ملف حساب خدمة Firebase في جذر الخلفية (موسوم «never commit» في `CODEMAP.md:73`). **لم يُفتَح محتواه**؛ ظهر حقل `project_id` غير السرّي عبر grep فقط. **المفتاح الخاص داخله يحتاج تدوير/إعادة توليد** عند تجهيز اعتماد Firebase للمشروع الجديد — **ولا يُنقل الملف**. (وفي الجديد أصلاً: **التحقّق العام بـJWKS لا يحتاج حساب خدمة إطلاقاً** — انظر `05 §3.1`: «حساب خدمة Firebase (JSON) — **أو JWKS إن اكتُفي بالتحقّق العام**».)
- **اكتشاف عَرَضي خارج النطاق (يُبلَّغ بحكم قاعدة «لا تنسخ الأسرار»):** `/home/alpha/gmail_credentials.json` يحوي **سرّ عميل OAuth بنصّ صريح** (شأن تكامل Gmail لا المصادقة) — **قيمته لم تُستنسَخ**؛ يحتاج تدويراً أسوة بمفتاح Exa.
