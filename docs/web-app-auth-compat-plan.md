# خطة توافق مصادقة web_app مع AIZZAK backend

> **الحالة:** لم يبدأ التنفيذ · **حُرِّرت:** 2026-08-05 · **النطاق:** تسجيل الدخول والمصادقة فقط
>
> هذا المستند مكتفٍ بذاته — الجلسة المنفِّذة لا تحتاج أي سياق سابق.

---

## 0. السياق الذي تحتاجه الجلسة المنفِّذة

### المستودعان

| الاسم | المسار | ماذا |
|---|---|---|
| الـ backend | `/home/AIZZAK` | FastAPI · Python 3.12 · معماريّة ports/adapters · مستودع git على `master` |
| الواجهة | `/home/web_app` | React 19 + Vite 8 + TypeScript · Firebase Auth |

الوصول من Windows عبر WSL مباشرةً — **لا تستعمل مسارات `\\wsl.localhost\`**:

```bash
wsl.exe -d Ubuntu-24.04 -- bash -lc 'cd /home/web_app && npm run typecheck'
```

### الاكتشاف الجذري الذي تقوم عليه الخطة كلها

الواجهة مبنيّة على backend **مختلف**: ملف `/home/web_app/openapi.json` الذي يولّد `src/api/schema.d.ts` عنوانه `"Student AI Assistant API"` — أي backend الـ `alpha` القديم (`/home/alpha`)، لا `"AIZZAK Platform API"`.

وأهمّ من ذلك، بفحص **كل** الـ 41 route في `/home/AIZZAK/src/app/api/v1/routers/`:

> **لا يوجد ولا endpoint واحد يأخذ `user_id` أو `workspace_id` في المسار.**

كل الهوية تُشتقّ من توكن Firebase في الـ middleware، والعزل بين المستأجرين يتم عبر RLS على مستوى الـ workspace. لذلك معظم ما يبدو «نقصاً في الـ backend» هو في الحقيقة **نموذج قديم تحمله الواجهة**.

### ما هو متوافق أصلاً (لا تلمسه)

| البند | الواجهة | الـ backend |
|---|---|---|
| مزوّد الهوية | Firebase، مشروع `aizzak-agent` | تحقّق JWKS محلي، `aud == project_id`، `iss == https://securetoken.google.com/<pid>` |
| نقل التوكن | `Authorization: Bearer <getIdToken()>` على كل طلب — `src/api/http.ts:33` | يقرأ نفس الترويسة — `src/app/api/middleware/auth.py` |
| إنشاء المستخدم أول دخول | يستدعي `POST /users/` صراحةً | **يفعله تلقائياً** — `provision_on_login` idempotent، تُنشئ workspace + user وتمنح دور `owner` |
| الحساب المعطّل | يعامل 403 كـ `is_disabled` | 403 `authz.forbidden` مقروءة من قاعدة البيانات بلا كاش، على كل طلب |

**شرط تشغيلي وحيد:** ضبط `FIREBASE_PROJECT_ID=aizzak-agent` في بيئة الـ backend.

---

## 1. البنود الثمانية

كل بند يحدّد: **أين النقص فعلياً** · **أين يقع التعديل** · **لماذا لا يمكن حلّه في الطرف الآخر**.

| # | البند | النقص في | التعديل في | النوع | حاجز تشغيل؟ |
|---|---|---|---|---|---|
| 1 | CORS | الحافة (nginx) | الحافة | إعداد نشر | ✅ نعم |
| 2 | `POST /users/` | **الواجهة** | الواجهة | حذف | ✅ نعم |
| 3 | البادئة `/api/v1` | **الواجهة** | الواجهة | متغيّر بيئة | ✅ نعم |
| 4 | نوع مُعرّف المستخدم | **الواجهة** | الواجهة | حذف معاملات | ✅ نعم |
| 5 | `email_verified` | الـ backend | الـ backend | تحسين أمني | ❌ لا |
| 6-أ | اسم/صورة/heartbeat | **الواجهة** | الواجهة | Firebase مباشرة | ❌ لا |
| 6-ب | حذف الحساب | الـ backend | الـ backend | إضافة | ❌ لا |
| 7 | إبطال الجلسة | الـ backend | الـ backend | إضافة | ❌ لا |
| 8 | تطبيع الأخطاء | **الواجهة** | الواجهة | تصحيح | ❌ لا |

**5 من 8 تُحلّ في الواجهة وحدها · 1 في إعداد nginx · 2 إضافات حقيقية للـ backend.**

المبدأ الحاكم: **تجنّب تعديل الـ backend إلا حين يكون التعديل إضافة أو تحسيناً مبرَّراً بذاته.** البنود 5 و6-ب و7 تستوفي هذا الشرط — كلها إضافات صافية تسدّ ثغرات قائمة في الـ backend بصرف النظر عن هذه الواجهة.

---

## 2. البند 1 — CORS · الحافة

**أين النقص:** لا `CORSMiddleware` في `/home/AIZZAK/src/app/api/main.py`، ولا `add_header Access-Control-*` في `/home/AIZZAK/deploy/nginx/app-locations.conf` ولا `/home/AIZZAK/deploy/runpod/nginx.conf`. تحقّقتُ من غيابها في الاثنين.

**لماذا الواجهة عاجزة:** المتصفّح هو من يفرض القاعدة، لا الكود. ترويسة `Authorization` تجعل كل طلب «غير بسيط» فيسبقه `OPTIONS` preflight يجب أن يردّ عليه الخادم. لا سطر JavaScript يلتفّ على هذا.

**لماذا لا Firebase Hosting rewrites:** تُوجّه إلى Cloud Run / Cloud Functions فقط، لا إلى مضيف خارجي مثل RunPod.

**التنفيذ** — في `/home/AIZZAK/deploy/nginx/app-locations.conf` داخل `location /` (وما يقابله في `deploy/runpod/nginx.conf`):

- `Access-Control-Allow-Origin` بقائمة origins **صريحة** — نطاق Firebase Hosting للإنتاج + `http://localhost:5173` للتطوير. لا `*` إطلاقاً (تتعارض مع بيانات الاعتماد ومع كون كل طلب مصادَقاً).
- `Access-Control-Allow-Headers: Authorization, Content-Type`
- `Access-Control-Allow-Methods: GET, POST, PATCH, PUT, DELETE, OPTIONS`
- `Access-Control-Max-Age` معقول لتقليل الـ preflights
- `if ($request_method = OPTIONS) { return 204; }`

انتبه لمسار الـ WebSocket `location /api/v1/ws` — بروتوكول WS لا يخضع لـ CORS لكنه يخضع لفحص `Origin`؛ راجعه إن استُعمل.

**إعداد نشر — صفر تعديل على كود الـ backend.**

---

## 3. البند 2 — `POST /users/` · الواجهة

**التصوّر الخاطئ الذي يجب تفكيكه:** «الـ backend ينقصه endpoint لمزامنة المستخدم.»

**الحقيقة:** الـ backend يزامن **قبل** أن يصل الطلب إلى أي router. في `/home/AIZZAK/src/app/api/middleware/auth.py`، الدالة `ApiAuthenticator.authenticate` تنفّذ على **كل** طلب: تحقّق التوكن ← فحص قائمة الإبطال ← `provision_on_login` (idempotent، تُنشئ الـ workspace والمستخدم على أول دخول) ← رفض الحساب المعطّل ← `seed_owner` إن كان الـ workspace وليداً ← قراءة الأدوار.

استدعاء endpoint للمزامنة يكرّر عملاً يحدث تلقائياً.

**ما تحتاجه الواجهة من `dbUser` وأين تجده بدلاً منه:**

| الحقل | المصدر الجديد |
|---|---|
| `email` · `uid` · `displayName` · `photoURL` | `auth.currentUser` — من التوكن نفسه، بلا أي طلب شبكة |
| `is_disabled` | أول استجابة 403 من أي endpoint |
| `id` (لبناء المسارات) | **لم يعد مطلوباً** — راجع البند 4 |
| `is_admin` | أدوار RBAC داخل الـ backend؛ الواجهة لا تراها ولا تحتاجها |

**التنفيذ** — في `/home/web_app/src/contexts/AuthContext.jsx`:

- احذف `syncWithBackend` بالكامل وكل استدعاءات `syncUser`.
- ابنِ حالة المستخدم من `currentUser` مباشرة.
- احذف `sessionStorage` لـ `pending_username` — الاسم يذهب إلى ملف Firebase الشخصي (البند 6-أ).
- استبدل `dbUser` بحالة مشتقّة؛ راجع كل مستهلك لـ `dbUser` في `src/pages/` و`src/components/`.
- أبقِ معالجة 403 → معطّل، لكن انقلها إلى معترض الاستجابة في `src/api/http.ts` بدل دالة المزامنة، لأنها الآن قد تأتي من أي endpoint.

⚠️ **الحساب المعطّل والرفض بـ RBAC يتشاركان `code: "authz.forbidden"`** ويختلفان في `detail` فقط:
- معطّل: `"account is disabled"` — `middleware/auth.py`
- صلاحية ناقصة: `"missing permission: <perm>"` — `middleware/rbac.py:70`

التمييز بالنص هشّ. راجع البند 8.

**احذف من `/home/web_app/src/api/index.ts`:** `syncUser`.

---

## 4. البند 3 — البادئة · الواجهة

**أين الاختلاف:** الواجهة تنادي `/users/`, `/agents/...` على الجذر. الـ backend يركّب كل الـ routers تحت `api_prefix = "/api/v1"` — `/home/AIZZAK/src/app/framework/settings/settings.py:306`. (`/health` و`/metrics` وحدهما على الجذر، غير مُصادَقين.)

**التنفيذ:**

1. في `/home/web_app/.env` — `VITE_API_BASE_URL` يبقى **جذر المضيف بلا `/api/v1`**. سبب دقيق: مخطط OpenAPI الذي يولّد المسارات يحمل البادئة داخل مفاتيح `paths` أصلاً، فإضافتها إلى الـ base URL تُنتج `/api/v1/api/v1/...`.
2. في `/home/web_app/package.json` — اجعل `gen:api` يسحب من `<host>/openapi.json`. FastAPI ينشره على **الجذر** بالإعداد الافتراضي (لا `openapi_url` مخصّص في `create_app`) وبلا مصادقة.
3. أعد التوليد، واحذف `/home/web_app/openapi.json` و`openapi.json.bak` القديمين (مخطط `alpha`).

بهذا تُصحَّح كل المسارات دفعة واحدة من المخطط، لا يدوياً.

---

## 5. البند 4 — مُعرّف المستخدم · الواجهة

**التشخيص السطحي:** `dbUser.id` عدد صحيح؛ الـ backend يستعمل UUIDv7.

**التشخيص الصحيح:** المسألة ليست `int` → `string`. **لا مسار يأخذ مُعرّف مستخدم إطلاقاً** — المعامل يُحذف نهائياً.

**خريطة التحويل:**

| نداء الواجهة الحالي | ما يقابله في AIZZAK |
|---|---|
| `GET /users/{user_id}/gmail/status` | `GET /api/v1/integrations/connections` |
| `GET /users/{user_id}/gmail/auth-url` | مسار OAuth في `integrations.py` |
| `DELETE /users/{user_id}/gmail/disconnect` | `DELETE` على الاتصال في `integrations.py` |
| `GET /users/{user_id}/agents` | `GET /api/v1/agents` |
| `POST /users/{user_id}/rag-agents` | لا مقابل مباشر — خارج نطاق المصادقة |

**التنفيذ** — في `/home/web_app/src/api/index.ts`: احذف معاملات `firebaseUid`/`userId` من توقيعات الدوال، ثم عدّل مواقع النداء.

💡 **`npm run typecheck` هو قائمة المهام هنا.** بعد إعادة توليد `schema.d.ts` (البند 3)، الأنواع المولّدة في `src/api/http.ts` تجعل كل نداء لمسار محذوف أو معامل زائد **خطأ تصريف**، لا 404 وقت التشغيل. هذه هي الآلية المصمَّمة لهذا الغرض بالضبط — استعملها بدل التفتيش اليدوي.

---

## 6. البند 5 — فرض `email_verified` · الـ backend (تحسين أمني)

**أين النقص:** المحوّل يقرأ الادّعاء إلى `Identity.email_verified` — `/home/AIZZAK/src/app/infrastructure/auth/firebase_auth.py:263` — لكن **لا أحد يستهلكه**. بحثتُ في كل `src/`: الاستعمالان الوحيدان هما تعريف الحقل في `ports/auth_provider.py:19` وتعبئته في المحوّل. صفر قرّاء.

**هل هو حاجز توافق؟ لا.** الواجهة تفرضه client-side: `login()` في `AuthContext.jsx` يسجّل الخروج ويرمي `auth/email-not-verified` إن لم يكن البريد موثّقاً. تسجيل الدخول سيعمل بدون هذا التعديل.

**لماذا يستحق الإصلاح رغم ذلك:** الفرض client-side ليس فرضاً. من ينادي الـ API بـ `curl` وتوكن غير موثّق البريد يحصل على workspace كامل ودور `owner`. الواجهة عاجزة عن سدّ هذا بحكم التعريف.

**التنفيذ** — في `/home/AIZZAK/src/app/api/middleware/auth.py`، داخل `authenticate`، **بعد** فحص الإبطال و**قبل** `provision_on_login` (نفس منطق الترتيب الموثّق في المستند: الرفض يجب ألّا يكلّف شيئاً):

- إن كان `identity.email_verified` غير `True` → `UnauthorizedError(_INVALID_TOKEN_DETAIL, code="auth.invalid_token")`.
- استعمل **نفس** النص المبهم الثابت — انسجاماً مع القاعدة الموثّقة في نفس الملف: العميل لا يجب أن يميّز سبب الرفض.
- سجّل بـ `firebase_uid` وحده، أبداً لا التوكن ولا أي ادّعاء (10 §10) — تماماً كفرع `auth.identity_without_email` المجاور، وهو القالب الذي تحتذيه.

**إضافة صافية، سطران، تنسجم مع منطق الملف القائم ولا تكسر شيئاً.** أضف اختبار وحدة يقابل اختبارات الرفض الموجودة.

---

## 7. البند 6 — إدارة الحساب · منقسم

### 6-أ · الاسم والصورة و heartbeat → **الواجهة**

| الاستدعاء الحالي | البديل بلا أي تعديل backend |
|---|---|
| `PATCH /users/{uid}/username` | `updateProfile(auth.currentUser, {displayName})` — الـ backend يقرأ ادّعاء `name` أصلاً في `_display_name` بـ `middleware/auth.py` |
| `PATCH /users/{uid}/photo` | ارفع إلى Firebase Storage (الدلو `aizzak-agent.firebasestorage.app` مُعدّ في `.env`) ثم `updateProfile({photoURL: <url>})` |
| `POST /users/{uid}/heartbeat` | **احذفه** — يخدم لوحة إدارة online/offline لا وجود لها في AIZZAK |

⚠️ `photoURL` لا يقبل base64 data URL بسبب قيود الطول. الواجهة اليوم تمرّر base64 في `updatePhoto(base64)` — **يجب** أن تصير رفعاً إلى Storage ثم تخزين الرابط.

**التنفيذ:** في `AuthContext.jsx` عدّل `updateUsername` و`updatePhoto`، واحذف مؤقّت الـ heartbeat (`useEffect` بفاصل 30 ثانية) بالكامل. احذف `updateUsername`/`updatePhoto`/`sendHeartbeat` من `src/api/index.ts`.

### 6-ب · حذف الحساب → **الـ backend** (الإضافة الوحيدة التي لا بديل لها)

**أين النقص:** `deleteAccount()` في `AuthContext.jsx` تعيد المصادقة ثم تحذف حساب Firebase — فيبقى الـ workspace والمستخدم والملفات والاعتمادات **يتيمة بلا أي مالك** في قاعدة البيانات. تسرّب بيانات دائم، لا مجرد إزعاج.

**لماذا الواجهة عاجزة:** لا تملك صلاحية لمس قاعدة البيانات.

**التنفيذ:** `DELETE /api/v1/me` تحت `Permission.WORKSPACE_MANAGE`، في `workspace.py` أو router جديد. تُنادى **قبل** حذف حساب Firebase (بعده يفقد العميل التوكن اللازم للمصادقة).

قرارات تحتاج حسماً وقت التنفيذ:
- حذف فعلي أم إبطال ناعم (soft delete)؟ راجع `Workspace.status` في نموذج المجال.
- ماذا يحدث لـ workspace يملكه أعضاء آخرون؟ (اليوم كل مستخدم يملك workspace خاصاً به من الـ JIT provisioning، لكن لا تبنِ على بقاء ذلك.)
- تنظيف الملفات في MinIO والاعتمادات في Vault.

**هذه إضافة تسدّ ثغرة امتثال (حق المحو) قائمة في الـ backend بصرف النظر عن هذه الواجهة.**

---

## 8. البند 7 — إبطال الجلسة عند الخروج · الـ backend (اختياري)

**أين النقص:** الـ backend يملك الآلية **كاملة** — `SessionRevocationList` في `/home/AIZZAK/src/app/framework/auth/revocation.py`، وقائمة `auth:revoked:<sub>` تُفحص على **كل** طلب في `middleware/auth.py` (مع سلوك fail-closed موثّق: انقطاع الكاش = 500، لا تجاوز صامت).

لكن **لا يوجد أي endpoint يضيف إلى هذه القائمة**. آلية مبنيّة بالكامل وغير موصولة بأي مُشغِّل.

**السلوك الحالي:** `logout()` تُنهي الجلسة محلياً فقط؛ التوكن المسروق يبقى صالحاً حتى `exp` (ساعة افتراضياً).

**التنفيذ:** `POST /api/v1/me/logout` يستدعي `revocations.revoke(uid)` بـ TTL يساوي عمر التوكن المتبقّي. في الواجهة: نادِها في `logout()` **قبل** `signOut(auth)`.

**إضافة تُفعّل استثماراً قائماً — مؤجَّلة بأمان، ليست حاجز تشغيل.**

---

## 9. البند 8 — تطبيع الأخطاء · الواجهة

**أين الاختلاف:** `detailToMessage` في `/home/web_app/src/api/http.ts` يتوقّع شكل `alpha`:
- 429 → `{detail: {message, scope, retry_after}}`
- 422 → `detail` مصفوفة أخطاء تحقّق FastAPI

الـ backend يرسل **RFC 9457** — `/home/AIZZAK/src/app/api/errors.py`:

```
{ "type": "<base><code>", "title": ..., "status": ..., "code": ...,
  "detail"?: <نص دائماً>, "instance"?, "correlation_id"?, "errors"?: [...] }
```

بوسيط `application/problem+json`. الحقول `None` **محذوفة**، لا `null`.

الفروق الجوهرية: `detail` **نص دائماً** (لا كائن ولا مصفوفة أبداً) · التفاصيل البنيوية في `errors[]` · `code` هو المُعرّف المستقرّ للتصنيف، لا نص `detail`.

**عملياً يعمل** عبر فرع `typeof detail === 'string'`، لكن أخطاء التحقّق ستظهر مبتورة بلا اسم الحقل، والفرعان الآخران ميّتان.

**التنفيذ** — أعد كتابة `detailToMessage` ليقرأ `code` و`errors[]`، واستعمل `code` للتفريع المنطقي (لا نص `detail`). احتفظ بـ `correlation_id` في السجلّ — يربط خطأ العميل بسجلّ الخادم.

**تحسين backend اختياري مرتبط:** كود مستقلّ `authz.account_disabled` بدل مشاركة `authz.forbidden` مع رفض RBAC (راجع البند 2). صغير ومبرَّر، لكنه غير ضروري — التمييز بنص `detail` ممكن مؤقتاً وإن كان هشّاً.

---

## 10. ترتيب التنفيذ

### المرحلة أ — الحد الأدنى لتشغيل تسجيل الدخول فعلياً

| الترتيب | البند | الطرف |
|---|---|---|
| 1 | البادئة + إعادة توليد `schema.d.ts` (البند 3) | الواجهة |
| 2 | حذف `syncUser` وبناء الحالة من التوكن (البند 2) | الواجهة |
| 3 | حذف معاملات المُعرّفات — يقودها `typecheck` (البند 4) | الواجهة |
| 4 | CORS في nginx (البند 1) | الحافة |

نفّذ 1 أولاً: إعادة التوليد تُحوّل بقية العمل إلى قائمة أخطاء تصريف ملموسة.

### المرحلة ب — إضافات backend مبرَّرة بذاتها

| الترتيب | البند | ملاحظة |
|---|---|---|
| 5 | فرض `email_verified` (البند 5) | سطران + اختبار |
| 6 | `DELETE /api/v1/me` (البند 6-ب) | يحتاج قرارات تصميم — راجع §7 |

### المرحلة ج — مؤجَّل

- البند 6-أ: نقل الاسم/الصورة إلى ملف Firebase الشخصي + Storage
- البند 8: تطبيع `errors[]`
- البند 7: `POST /me/logout`
- كود `authz.account_disabled`

---

## 11. معايير القبول

**المرحلة أ منتهية حين:**

1. `npm run build` ينجح في `/home/web_app` (يشمل `gen:api` + `typecheck`) مقابل مخطط AIZZAK الحقيقي.
2. تسجيل دخول ببريد موثّق من المتصفّح ينتهي بجلسة فعّالة، **بلا أي 404 وبلا أي خطأ CORS في وحدة التحكّم**.
3. أول دخول لمستخدم جديد يُنشئ workspace في قاعدة البيانات (تأكيد بالاستعلام) بلا استدعاء `POST /users/`.
4. `GET /api/v1/workspace` يرجع 200 بالتوكن، و401 بدونه.
5. حساب معطّل يتلقّى 403 وتعرض الواجهة الحالة الصحيحة.
6. لا أثر لـ `dbUser` ولا لأي مسار `/users/` في `/home/web_app/src/`.

**المرحلة ب منتهية حين:**

7. توكن بـ `email_verified: false` يتلقّى 401 بنفس النص المبهم — باختبار وحدة.
8. حذف الحساب لا يترك صفوفاً يتيمة — بتأكيد استعلام.
9. البوّابات الخمس تمرّ في `/home/AIZZAK` (راجع `docs/implementation-plan.md`).

---

## 12. ملفات ستُلمس

**`/home/web_app`** — `.env` · `package.json` · `src/api/http.ts` · `src/api/index.ts` · `src/api/schema.d.ts` (مولّد) · `src/contexts/AuthContext.jsx` · `openapi.json` (يُستبدل) · مستهلكو `dbUser` في `src/pages/` و`src/components/`

**`/home/AIZZAK`** — `deploy/nginx/app-locations.conf` · `deploy/runpod/nginx.conf` · `src/app/api/middleware/auth.py` (البند 5) · router لـ `DELETE /me` (البند 6-ب) · اختباراتها

---

## 13. خارج النطاق

هذه الخطة تغطي **تسجيل الدخول والمصادقة فقط**. لم تُفحص ولم تُخطَّط:

- صفحات المحادثة و RAG ورفع الملفات (`Chat.jsx` · `Rag.jsx` · `pages/rag/`)
- لوحة الإدارة (`pages/admin/`) — تنادي `/admin/*` التي **لا وجود لها** في AIZZAK؛ يقابلها نظام RBAC بالأدوار والصلاحيات، وهي إعادة تصميم لا ترحيل
- تكامل Gmail — يقابله `integrations.py`، بنموذج مختلف كلياً
- مفاتيح المزوّدين (`ProviderKeys.jsx`) — يقابلها `credentials.py` مدعوماً بـ Vault
- WebSocket للبثّ (`/api/v1/ws`) — الواجهة لا تستعمله اليوم
