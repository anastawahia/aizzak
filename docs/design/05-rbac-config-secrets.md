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
`electrical:read` · `electrical:diagnose` · `electrical:manage` · `electrical:eval` ·
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
| electrical:read | ✓ | ✓ | ✓ | ✓ | — |
| electrical:diagnose | ✓ | ✓ | ✓ | — | — |
| electrical:manage | ✓ | ✓ | — | — | — |
| electrical:eval | ✓ | ✓ | — | — | — |
| platform:admin | — | — | — | — | ✓ |

> `platform_admin` عمداً بلا وصول لمحتوى المستأجر (محادثات/ملفات) — دعمٌ لا اطّلاع؛ يُوسَّع عند الحاجة.
> صلاحيات `electrical:*` (WireTwin، SRS D-25) على الأدوار القائمة بلا دور جديد؛ و`platform_admin` بلا أيٍّ منها لأن الدعم عابر للمستأجرين ولا يرى محتواهم. و`electrical:review` غائبة عمداً حتى R2 (FR‑HIL‑06).
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

### 1.6 إبطال الجلسة — `auth:revoked:<sub>` (‏3.79)
- **القرار القائم (‏D7):** لا `check_revoked` في v1 — فحص الإبطال جولةُ شبكةٍ لكل تحقّق، وهو ما يناقض `D‑25` مباشرة. الضابط التعويضي: حالة الحساب تُقرأ **طازجةً من قاعدة البيانات في كل طلب بلا أي كاش** ⇒ حسابٌ مُعطَّل يُمنَع فوراً مهما بقي من عمر التوكن.
- **الخطر المتبقّي الذي لا يغطّيه ذلك الضابط:** توكنٌ **مسروقٌ قبل الإبطال** يبقى صالحاً حتى `exp` (‏≤ 3600ث + 60ث هامش). سُدّ في 3.79 بقائمة منعٍ قصيرة العمر فوق `CacheProvider`، مفتاحها `auth:revoked:<sub>` — **الـ`sub` لا التوكن أبداً** (قائمةٌ مفتاحها التوكن تُنشئ مخزناً يستحقّ السرقة، وهو عين ما تحاول احتواءه).
- **موضع الفحص: حارس 6.4 (`api/middleware/auth.py`) لا مِحوَل 2.7** — كي يبقى `verify_token` دالّةً نقيّةً بصفر I/O إضافيّ، فلا يخسر `D‑25` شيئاً. الرفض `401 auth.invalid_token` بنفس التفصيل الحرفيّ الثابت (إبطالٌ حكمٌ على **الاعتماد**، لا على ما يجوز لمعروفٍ فعله ⇒ ليس 403)، وغيابُ التمييز مقصود: إخبار حاملِ توكنٍ مسروقٍ بأنّ السرقة كُشفت خطأ.
- **سقف العمر:** ‏`3600 + 60 = 3660ث`، لا أكثر — بعدها لا وجود لتوكنٍ يمكن لهذا القيد أن يمنعه أصلاً، وإطالته تحبس تسجيل دخولٍ مشروعاً بلا مقابل. يُفرَض عند **البناء** (فشلٌ مبكّر، سابقة `FirebaseAuth._guard_ttl`).
- **الفشل:** غيابُ المفتاح ⇒ «غير مُبطَل» (المسار الطبيعيّ). **تعطّلُ المخزن ليس غياباً**: يُرفَع خطأ المِحوَل ولا يُبتلَع ⇒ `common.internal`/500 — فشلٌ مُغلَقٌ تجاه الوصول، لأنّ أسوأ ما تفعله قائمة منعٍ أن تتوقّف عن المنع صامتةً بينما مخزنها معطّل.
- **الكاتب:** `python -m app.ops.revoke <firebase_uid>` — لا مسار API، لأنّ `03 §1` لا يعلن أيّ عمليّة تسجيل خروج/تعطيل/حذف مستخدمٍ في v1، وبناء خدمةٍ بلا مستدعٍ يشحن سطحاً غير قابلٍ للبلوغ. أوّلُ مسارٍ يُصمَّم لاحقاً يستدعي `SessionRevocationList.revoke` نفسها.

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
| `REDIS_URL` | `redis://redis-stream:6379/0` | Streams · سجلّ WS · محدِّد المعدّل · قائمةُ المنع — `noeviction` (خطّة السعة `5.2`) |
| `CACHE_REDIS_URL` | `redis://redis-cache:6379/0` | الذاكرةُ المخبّأة القابلة للإخلاء وحدَها — `allkeys-lru`. **فارغاً = الخادمُ نفسُه الذي يشير إليه `REDIS_URL`** (مفتاحُ إطفاء `م‑8`) |
| `MINIO_ENDPOINT` | `minio:9000` | تخزين الكائنات |
| `MINIO_BUCKET` | `workspace-files` | الدلو |
| `MINIO_PRESIGN_PUT_TTL_S` / `MINIO_PRESIGN_GET_TTL_S` | `900` / `300` | عمر رابط الرفع/التنزيل الموقّع (ثوانٍ، ضمن مدى SigV4: `1..604800`) |
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

| `ELECTRICAL_ENABLED` | `false` | تفعيل WireTwin (INC‑1، `INC1‑D‑12`). مغلقٌ افتراضياً: كل مسار `/api/v1/electrical` يجيب `503 electrical.disabled` قبل حارس الصلاحية |
| `ELE_PARSE_URL` | `http://ele-parse:8090` | عنوان خدمة `ele-parse` على الشبكة الداخلية (بلا أسرار) |
| `ELE_PARSE_TIMEOUT_S` | `330` | مهلة طلب العامل إلى `ele-parse` (> سقف المدى في الخدمة نفسها) |
| `ELE_STAGE_DIR` | `/var/lib/aizzak/ele-stage` | الحجم المؤقت الذي يكتب فيه العامل نسخة الـPDF ويقرؤها `ele-parse` للقراءة فقط |
| `ELE_STAGE_CHUNK_BYTES` | `8388608` | حجم جزء القراءة المتدفقة من MinIO (65,536 .. 67,108,864) |
| `ELE_INSPECT_BATCH_PAGES` | `200` | صفحات كل دفعة فحص ومعاملة (1 .. 1,000) |
| `ELE_MAX_PAGES` | `5000` | سقف صفحات الوثيقة (FR‑ING‑02 «5,000 على الأقل»)؛ فوقه `electrical.too_many_pages` |
| `ELE_QUEUE_PROTECTION_LIMIT` | `1000` | سقف المهام المفتوحة في المساحة؛ فوقه `429 electrical.ingestion_queue_full` |
| `ELE_PROGRESS_MIN_INTERVAL_S` | `2.0` | أدنى فاصل بين إشعاري تقدّم لمهمة واحدة |
| `ELE_JOB_LEASE_S` | `600` | عقد المهمة الجارية (≥ 60)؛ انتهاؤه يجعل المهمة `stale` قابلة للاستئناف |
| `ELECTRICAL_WORKER_ROLES` | `document` | أدوار `worker-electrical` (قائمة بفواصل؛ INC‑1 يعرف دوراً واحداً) |

> **مفاتيح WireTwin إحدى عشرة لا عشرة** (ما نصّ عليه LLD §3.3.12 عدّها بلا `ELECTRICAL_WORKER_ROLES`): كلها **غير سرّية**، يقرؤها `env_settings.py` وحده (`DD‑11`) ويقدّمها `Settings.electrical` (`ElectricalSettings`). ولا سرّ جديد في INC‑1: `ele-parse` بلا أسرار، والعامل يقرأ اعتماد MinIO الموجود في Vault كما يفعل `worker-knowledge`. أمّا مفاتيح `ele-parse` نفسه (`ELE_PARSE_*`: الذاكرة والمهلة والعيّنة...) فتخصّ حاويته وحدها ولا يقرؤها `Settings`. العتبات والاصطلاحات ليست هنا: هي في **ملف إعداد المشروع** (`DrawingProfile`، NFR‑CFG‑01) لا في البيئة.

---

## 3) كتالوج الأسرار (Vault · سرّي)

> Vault AppRole (D‑22) + Transit (D‑03). التطبيق يصادق بـAppRole ثم يقرأ KV ويستخدم Transit. لا سرّ **تطبيقٍ** في `.env` أو الصورة. والاستثناءان: كلمات سرّ الإقلاع (تُولَّد في `.env` وتُستعمل قبل أن يكون Vault جاهزاً)، و`VAULT_SECRET_ID` (§3.3).
>
> ومفتاح TLS للحافّة (`deploy/nginx/certs/server.key`) ليس سرّ تطبيقٍ ولا يمرّ بـVault: ملفٌّ على قرص الخادم، صلاحيّته 0600 لـroot، يولَّد هناك بطلب توقيع، ولا يُلتزم ولا يدخل الحزمة (`docs/deploy-server.md` §4، ث‑15).

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
- `VAULT_SECRET_ID` (الوحيد الحسّاس في البيئة) يُحفظ في `.env`، ويكتبه `deploy/release/install.sh` على الخادم (لا يُطبع، لا يُسجَّل). وقيمةٌ مُصدَّرة في الصَّدَفة تفوز على `.env`.
- `SecretsProvider` (المنفذ 1.9) هو الواجهة الوحيدة للأسرار؛ لا وحدة تتصل بـVault مباشرة — تشمل `credentials` و`integrations`.
- تدوير المفاتيح: إعادة تعمية عبر Transit rewrap دون كشف النصّ الصريح، **دورة واحدة موحّدة** للموضعين.

---

## 4) خريطة الصلاحية ← المسار (عيّنة)
| المسار | الصلاحية المطلوبة |
|--------|-------------------|
| `POST /agents/{k}/invoke` | `agents:invoke` |
| `POST /conversations/{id}/messages` | `conversations:write` |
| `DELETE /files/{id}` | `files:delete` |
| `POST /files/multipart` | `files:write` |
| `PUT /files/{id}/tags` | `files:write` (إعادة وسم الملف، `PLT‑26`؛ كإعادة التسمية `PATCH /files/{id}`: لا تحذف بنفسها، لكنّ مجموعة وسومٍ جديدة قد تُصيّر ملفاً أقدم سميّاً فيُستبدل — القاعدة نفسها التي يملكها `files:write` عند الرفع والتسمية، فلا قدرة جديدة؛ INC1‑R‑2) |
| `GET /files/{id}/upload` | `files:write` (خطوة من بروتوكول الكتابة — الاستئناف — لا قراءة لمحتوى الملف)، **ولمن سجّل الملف وحده** |
| `POST /files/{id}/upload/part-urls` | `files:write`، **ولمن سجّل الملف وحده** |

> **تحقّقٌ على مستوى الكائن فوق الصلاحية (SR‑1، CWE‑639):** روابط أجزاء الرفع المجزّأ وقائمتها وإتمامه لمن سجّله (`uploaded_by`) وحده. فعضوٌ آخر يحمل `files:write` في المساحة نفسها ⇒ `404`، كالمستأجر الغريب. والسبب أنّ هذه الروابط تُعاد إصدارها، بخلاف رابط PUT الواحد الذي لا يُعطى إلا مرّةً لمن سجّل.
| `POST /media/jobs` | `media:create` |
| `POST /workflows/{k}/run` | `workflows:run` |
| `POST /credentials` | `credentials:manage` |
| `POST /integrations/connections` | `integrations:manage` |
| `GET /integrations/tools` | `integrations:read` |
| `DELETE /integrations/mcp-servers/{id}` | `integrations:manage` |
| `GET /usage` | `usage:read` |
| `PUT /usage/limits` | `usage:manage` |
| `PATCH /workspace` | `workspace:manage` |
| `GET /electrical/projects` · `GET /electrical/projects/{id}` · `GET …/{id}/profile` · `GET …/{id}/documents` · `GET …/{id}/characterization` | `electrical:read` (WireTwin، INC‑1) |
| `POST /electrical/projects` · `PATCH /electrical/projects/{id}` · `PUT …/{id}/profile` · `POST …/{id}/documents` | `electrical:manage` |
| `GET /electrical/documents/{id}` · `GET …/{id}/profile` · `GET …/{id}/pages` · `GET …/{id}/characterization` | `electrical:read` |
| `PUT /electrical/documents/{id}/profile` · `POST …/{id}/profile-confirmation` · `POST …/{id}/reprocessing` | `electrical:manage` |
| `GET /electrical/ingestion-jobs/{id}` · `GET /electrical/files/{id}/replacement-preview` | `electrical:read` |
| `POST /electrical/ingestion-jobs/{id}/cancel` · `POST …/{id}/resume` | `electrical:manage` |

> `PATCH /workspace` (تعديل إعدادات المساحة) يتطلّب `workspace:manage` ⇒ متاح لـ`owner`/`admin`/`platform_admin`. أمّا `workspace:transfer` و`workspace:archive` فصلاحيتان **حصريّتان للمالك** بلا مسار API مخصّص في v1 (لا يعرّضهما `03-api-spec.md` بعد) — نقطتا توسعة تُربَطان بمساريهما عند إضافتهما.

> **WireTwin (`electrical:*`، INC‑1): عشرون عملية، صلاحية واحدة لكل عملية** (يفرضها `test_api_rbac.py`): `electrical:read` للقراءات ومعاينة الاستبدال، و`electrical:manage` لما سواها (إنشاء المشروع وتعديله، وملفات الإعداد، وتسجيل الوثيقة، والتأكيد وإعادة المعالجة، والإلغاء والاستئناف). فالعضو (`member`) يقرأ ويعاين ولا يسجّل ولا يغيّر ملف إعداد، والمشاهد (`viewer`) يقرأ، و`owner`/`admin` يملكان الاثنتين، ولا شيء لـ`platform_admin`. **لا صلاحية جديدة** (أنشأ INC‑0 الأربع). والمفتاح `ELECTRICAL_ENABLED=false` يسبق الصلاحية: يجيب كل مسار 503 قبل أن يُسأل عنها.

