# مصفوفة RBAC + كتالوج الإعدادات/الأسرار

> RBAC: Role → Permissions (D‑24) · الهوية Firebase / التفويض داخل التطبيق.
> ✅ **معتمد — توقيع 2026‑07‑10 (`OQ‑01`/`DD‑10`)**: طقم الأدوار وتوزيع الصلاحيات أدناه محسوم (حدّ التفويض) — نموذج RBAC مُعتمَد باعتماد الافتراض الحالي (Requirements §13).
> تقسيم الإعداد/الأسرار وفق `DD‑11`: لا وحدة تقرأ `.env` — فقط `infrastructure/config`.

---

## 1) نموذج RBAC

### 1.1 الأدوار
| الدور | النطاق | الوصف |
|-------|--------|-------|
| `owner` | Workspace | مالك المستأجر — كل الصلاحيات داخل مساحته |
| `admin` | Workspace | إدارة كاملة عدا نقل المِلكية/أرشفة المساحة |
| `member` | Workspace | استخدام القدرات (وكلاء/ملفات/محادثات/توليد) |
| `viewer` | Workspace | قراءة فقط |
| `platform_admin` | Platform | عابر للمساحات — دعم وإدارة منصّة |

### 1.2 الصلاحيات (`resource:action`)
`workspace:read` · `workspace:manage` · `workspace:transfer` · `workspace:archive` · `agents:read` · `agents:invoke` ·
`conversations:read` · `conversations:write` · `conversations:delete` ·
`files:read` · `files:write` · `files:delete` ·
`knowledge:read` · `knowledge:manage` ·
`media:read` · `media:create` ·
`workflows:read` · `workflows:run` ·
`credentials:read` · `credentials:manage` ·
`integrations:read` · `integrations:manage` ·
`usage:read` · `usage:manage` ·
`access:read` · `access:manage` ·
`platform:admin`

> **وحدة `memory` — بلا صلاحيات في الكتالوج (مقصود):** `memory` وحدة **داخلية بالكامل** يديرها الوكيل (المُنسِّق) أثناء التنفيذ ولا تعرّض أي سطح API مباشر للمستخدم (لا مسار في `03-api-spec.md`)؛ لذا لا `memory:read`/`memory:manage`. الوصول إلى عناصر الذاكرة محكومٌ ضمناً بعزل المستأجر (RLS على `memory.memory_items`) وبحدود الوكيل، لا بصلاحية RBAC مباشرة. تُضاف صلاحيات صريحة لاحقاً فقط إن كُشِف سطح API للذاكرة.

### 1.3 المصفوفة (✓ مسموح · — ممنوع)
| Permission \ Role | owner | admin | member | viewer | platform_admin |
|---|:--:|:--:|:--:|:--:|:--:|
| workspace:read | ✓ | ✓ | ✓ | ✓ | ✓ |
| workspace:manage | ✓ | ✓ | — | — | ✓ |
| workspace:transfer | ✓ | — | — | — | — |
| workspace:archive | ✓ | — | — | — | — |
| agents:read | ✓ | ✓ | ✓ | ✓ | ✓ |
| agents:invoke | ✓ | ✓ | ✓ | — | — |
| conversations:read | ✓ | ✓ | ✓ | ✓ | — |
| conversations:write | ✓ | ✓ | ✓ | — | — |
| conversations:delete | ✓ | ✓ | — | — | — |
| files:read | ✓ | ✓ | ✓ | ✓ | — |
| files:write | ✓ | ✓ | ✓ | — | — |
| files:delete | ✓ | ✓ | — | — | — |
| knowledge:read | ✓ | ✓ | ✓ | ✓ | — |
| knowledge:manage | ✓ | ✓ | — | — | — |
| media:read | ✓ | ✓ | ✓ | ✓ | — |
| media:create | ✓ | ✓ | ✓ | — | — |
| workflows:read | ✓ | ✓ | ✓ | ✓ | — |
| workflows:run | ✓ | ✓ | ✓ | — | — |
| credentials:read | ✓ | ✓ | — | — | — |
| credentials:manage | ✓ | ✓ | — | — | — |
| integrations:read | ✓ | ✓ | ✓ | ✓ | — |
| integrations:manage | ✓ | ✓ | — | — | — |
| usage:read | ✓ | ✓ | ✓ | ✓ | — |
| usage:manage | ✓ | ✓ | — | — | — |
| access:read | ✓ | ✓ | — | — | ✓ |
| access:manage | ✓ | ✓ | — | — | — |
| platform:admin | — | — | — | — | ✓ |

> `platform_admin` عمداً بلا وصول لمحتوى المستأجر (محادثات/ملفات) — دعمٌ لا اطّلاع؛ يُوسَّع عند الحاجة.
> منح `platform_admin` صلاحية `workspace:manage` مقصورٌ على **إعدادات/إدارة المساحة** (تعليق/تهيئة على مستوى المنصّة) لا على المحتوى؛ أمّا `workspace:transfer` (نقل الملكية) و`workspace:archive` (الأرشفة) فتبقيان **حصريّتين للمالك (`owner`)** اتّساقاً مع وصف الأدوار (§1.1).

### 1.4 الفرض
- كتالوج ثابت بالكود: `RoleCatalog: dict[RoleName, frozenset[Permission]]` في وحدة `access`.
- حارس المسار: `require("files:write")` — Dependency في `api/middleware/rbac.py` يقرأ أدوار المستخدم من `access` ويطبّق `is_allowed`.
- التفويض دالة نقية قابلة للاختبار بلا I/O.
- خرق ⇒ `403 authz.forbidden` (RFC 9457).
- **الربط بعزل المستأجر (طبقة AuthZ ↔ عزل البيانات):** طبقة RBAC هذه تحدّد *ما* يُسمح للدور بفعله، بينما **عزل المستأجر** يحدّد *أيّ* بيانات مرئية أصلاً — طبقتان متكاملتان. بعد اجتياز حارس المسار، تُضبط هوية المستأجر لكل معاملة عبر `SET LOCAL app.workspace_id` المشتقّة من `ExecutionContext` (`SEC‑03`)، ويعمل التطبيق بدور DB **خاضع لـRLS** (بلا `BYPASSRLS`، وليس مالك الجدول — `SEC‑04`). التفصيل في **`01-data-model.md` §3 (عزل المستأجر — RLS)**؛ فحتى مع صلاحية RBAC، لا يرى الطلب صفوفاً خارج `workspace_id` سياقه (فشل آمن ⇒ صفر صفوف عند غياب السياق).

### 1.5 البذر
- عند JIT للمستخدم: يُنشأ Workspace ويُسنَد له `owner`.
- `platform_admin` يُبذَر خارج التطبيق (هجرة بذر/أمر إداري) — لا يُمنح عبر الـAPI.

---

## 2) كتالوج الإعدادات (Settings · غير سرّي)

> تُقرأ من متغيّرات البيئة عبر `infrastructure/config/env_settings.py` وتُقدَّم عبر عقد `framework/settings`. القيم أمثلة.

| المفتاح | المثال | المعنى |
|---------|--------|--------|
| `APP_ENV` | `production` | البيئة |
| `APP_HOST` / `APP_PORT` | `0.0.0.0` / `8000` | ربط Uvicorn |
| `API_PREFIX` | `/api/v1` | بادئة الـAPI |
| `LOG_LEVEL` | `INFO` | مستوى السجلّ |
| `DATABASE_URL` | `postgresql+asyncpg://app@pgbouncer:6432/app` | عبر PgBouncer |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `10` / `20` | تجمّع التطبيق (Transaction pooling) |
| `REDIS_URL` | `redis://redis:6379/0` | Cache + Streams |
| `MINIO_ENDPOINT` | `minio:9000` | تخزين الكائنات |
| `MINIO_BUCKET` | `workspace-files` | الدلو |
| `QDRANT_URL` | `http://qdrant:6333` | مخزن المتجهات |
| `VAULT_ADDR` | `http://vault:8200` | عنوان Vault |
| `VAULT_ROLE_ID` | `<approle>` | AppRole (المعرّف غير سرّي؛ الـsecret_id سرّي) |
| `FIREBASE_PROJECT_ID` | `my-project` | مشروع Firebase (للتحقّق) |
| `FIREBASE_JWKS_CACHE_TTL` | `3600` | تخزين مفاتيح التحقّق |
| `EVENT_STREAM_PREFIX` | `stream.` | بادئة المجاري |
| `OUTBOX_POLL_INTERVAL_MS` | `500` | دورة المُرحّل |
| `CONSUMER_BLOCK_MS` | `5000` | حجب قراءة العامل |
| `MAX_RETRIES_BEFORE_DLQ` | `5` | N قبل DLQ |
| `PROVIDER_ROUTING` | `{"llm":{"default":"openai"},...}` | جدول توجيه المزوّدين (D‑16) |
| `OAUTH_REDIRECT_BASE_URL` | `https://api.example.com` | أساس عنوان ردّ OAuth للموصّلات (integrations) |
| `MCP_ALLOWED_TRANSPORTS` | `http,sse` | أنواع نقل MCP المسموحة (بعيد حصراً — v1) |
| `OAUTH_REFRESH_SKEW_S` | `60` | هامش زمني لفحص انتهاء الرمز قبل التجديد الكسول (FR‑124) |
| `USAGE_ROLLUP_PERIODS` | `day,month` | نوافذ تدوير عدّادات الاستهلاك للفرض السريع |
| `USAGE_DEFAULT_LIMITS` | `{"tokens":{"month":...}}` | حدود افتراضية قابلة للتخصيص لكل Workspace (FR‑133) |
| `LIMITS_*` | — | الحدود الرقمية (انظر `07-nfr-slo.md`) |

---

## 3) كتالوج الأسرار (Vault · سرّي)

> Vault AppRole (D‑22) + Transit (D‑03). التطبيق يصادق بـAppRole ثم يقرأ KV ويستخدم Transit. لا سرّ في `.env` أو الصورة.

### 3.1 Vault KV (`secret/`)
| المسار | المحتوى |
|--------|---------|
| `secret/data/db` | `{ "password": "..." }` |
| `secret/data/redis` | `{ "password": "..." }` |
| `secret/data/minio` | `{ "access_key": "...", "secret_key": "..." }` |
| `secret/data/firebase` | حساب خدمة Firebase (JSON) — أو JWKS إن اكتُفي بالتحقّق العام |
| `secret/data/jwt` | مادّة توقيع/تحقّق داخلية إن لزمت |
| `secret/data/providers/platform` | مفاتيح المزوّدين على مستوى المنصّة `{ "openai":"...", "gemini":"..." }` |

### 3.2 Vault Transit (`transit/`) — أسرار موحّدة (`SEC‑07`)
| المفتاح | الاستخدام |
|---------|-----------|
| `transit/keys/tenant-secrets` | **مفتاح Transit موحّد** لتعمية/فك أسرار المستأجرين في **موضعين**: مفاتيح LLM في `credentials` ورموز OAuth/أسرار الموصّلات في `integrations` — بنمط ودورة تدوير موحّدين، بلا نصّ صريح (`DD‑11`, `FR‑75`, `FR‑121`). |

> **حدّ الملكية (`SEC‑07`):** أسرار الاتصال بموصّل خارجي تُخزَّن في `integrations`؛ مفاتيح مزوّدي LLM للمستخدم في `credentials` — **لا ازدواج تخزين للسرّ نفسه**. يشتركان في مفتاح Transit ودورة التدوير فقط، لا في الجداول.

### 3.3 بوابة الوصول
- `VAULT_SECRET_ID` (الوحيد الحسّاس في البيئة) يُحقن كـsecret آمن (لا يُطبع، لا يُسجَّل).
- `SecretsProvider` (المنفذ 1.9) هو الواجهة الوحيدة للأسرار؛ لا وحدة تتصل بـVault مباشرة — تشمل `credentials` و`integrations`.
- تدوير المفاتيح: إعادة تعمية عبر Transit rewrap دون كشف النصّ الصريح، **دورة واحدة موحّدة** للموضعين.

---

## 4) خريطة الصلاحية ← المسار (عيّنة)
| المسار | الصلاحية المطلوبة |
|--------|-------------------|
| `POST /agents/{k}/invoke` | `agents:invoke` |
| `POST /conversations/{id}/messages` | `conversations:write` |
| `DELETE /files/{id}` | `files:delete` |
| `POST /media/jobs` | `media:create` |
| `POST /workflows/{k}/run` | `workflows:run` |
| `POST /credentials` | `credentials:manage` |
| `POST /integrations/connections` | `integrations:manage` |
| `GET /integrations/tools` | `integrations:read` |
| `DELETE /integrations/mcp-servers/{id}` | `integrations:manage` |
| `GET /usage` | `usage:read` |
| `PUT /usage/limits` | `usage:manage` |
| `PATCH /workspace` | `workspace:manage` |

> `PATCH /workspace` (تعديل إعدادات المساحة) يتطلّب `workspace:manage` ⇒ متاح لـ`owner`/`admin`/`platform_admin`. أمّا `workspace:transfer` و`workspace:archive` فصلاحيتان **حصريّتان للمالك** بلا مسار API مخصّص في v1 (لا يعرّضهما `03-api-spec.md` بعد) — نقطتا توسعة تُربَطان بمساريهما عند إضافتهما.
