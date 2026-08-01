# refs/credentials-oauth-jobs.md — تشفير المفاتيح · Gmail OAuth/PKCE · وظائف Redis (مرجع من `alpha`)

> **الوجهة:** `06-domain-models.md §3` (credentials) · `§9` (integrations) · `04-event-catalog.md` (Outbox/Streams) · إثراء من `02 §1.9/§3.5` · `05 §3`.
> **النطاق:** ثلاث مناطق تندمج في وحدتَي AIZZAK `credentials` + `integrations` + بنية `platform`: (1) تشفير مفاتيح المزوّدين، (2) تدفّق Gmail OAuth/PKCE، (3) حلقة وظائف Redis.
> **مصادر `alpha`:** `services/{provider_keys,model_registry,models_catalog,oauth_store,gmail_store,jobs,summarize_jobs}.py` · `routers/{providers,gmail,jobs,rag,agents}.py` · `services/tools/gmail_tool.py` · `cli/setup_gmail_auth.py` · `jobs/runner.py` · `db/{models,schemas}.py` · `migrations/{0003,0005}`.

## 0) واقع `alpha` (السياق)
- **جداول DB:** `user_provider_keys` (PK `user_id+provider`؛ `key_enc,key_last4,enabled`) · `provider_global_keys` (PK `provider`؛ `key_enc,key_last4`) · `user_gmail_tokens` (PK `user_id`؛ `token_json NOT NULL,account_email`). كلها FK→`users` ON DELETE CASCADE. الهجرتان `0005` (مفاتيح) و`0003` (فصل token عن صفّ users الساخن).
- **مفاتيح Redis:** `rag:jobs:queue` (LIST) · `rag:job:<id>` (HASH) · `oauth:pkce:<user_id>` (verifier، GETDEL) · `rag:summary_job:<id>` (+`:cancel`) · `rag:conv:<agent_id>:{indexed_at,registry_at}`.

## 1) تشفير المفاتيح + أسبقية الحلّ (`services/provider_keys.py`)
**المخطّط:** Fernet متماثل (`cryptography.fernet`)، **مفتاح واحد لكل الصفوف** من env `PROVIDER_KEY_ENC_KEY` (urlsafe‑base64 32‑بايت). لا KDF ولا salt في الكود (Fernet داخلياً AES‑128‑CBC + HMAC‑SHA256 + IV/timestamp لكل token). العمودان: `key_enc` (Text، token) + `key_last4` (عرض مقنّع). **المفتاح الخام لا يُخزَّن ولا يُعاد أبداً — فقط `key_last4` يغادر الخلفية.**

**التوقيعات المفتاحية:**
```
_encrypt(raw)->str · _decrypt(token)->str|None (None عند الفشل + تحذير) · _last4(raw) · is_configured()->bool
set_key(db,user_id,provider,raw)->last4   # upsert؛ المُنادي يعمل commit
get_user_key / has_user_key / list_for_user / delete_key
set_global_key / get_global_key / has_global_key / delete_global_key   # جدول provider_global_keys
resolve_key(db,user_id,provider)->str|None   # مسار الدردشة
```

**أنماط الفشل:** `PROVIDER_KEY_ENC_KEY` مفقود/غير صالح ⇒ `_fernet()=None` ⇒ **الكتابة** ترفع `ProviderKeyConfigError`→503؛ **القراءة** تُرجع None (نماذج السحابة تختفي بصمت). `_decrypt` الفاشل ⇒ تحذير + None.

**الأسبقية ذات الطبقتين (الجزء الحامل):**
1. **بوّابة الصلاحية** `models_catalog.usable_for(db,user_id,model)`: `is_allowed(model)` (allow‑list) AND `is_provider_enabled(provider)` AND توفّر مفتاح: **local(ollama) دائماً صالح؛ cloud يتطلّب `has_user_key OR has_global_key`**. `require_usable`→422. (المُستهلِك «rag»/فارغ ⇒ دائماً True.)
2. **حلّ المفتاح** `resolve_key`: local⇒None؛ cloud⇒ **مفتاح المستخدم (enabled) → admin‑global (صفّ DB) → بذرة env `key_env` → None**. الأسبقية الكاملة: `user BYOK → admin‑global DB → env seed (ilmu=SERVICE_API_KEY فقط) → None`. **مفتاح المستخدم يفوز** («فوترته/حصّته»). openai/google **بلا بذرة env** عمداً (منع استخدام `OPENAI_API_KEY`/`GEMINI_API_KEY` القديم صامتاً).

**مسار الدردشة (`routers/agents.py::chat_with_agent`):** ضمن جلسة DB قصيرة: (أ) إعادة فحص `usable_for` وفشل مغلق **409** إن صار النموذج غير صالح (حُذف المفتاح/عُطِّل المزوّد)، (ب) `resolve_key` للنصّ الصريح، ثم **إغلاق الجلسة قبل الاستدلال** (لا اتصال DB محجوز أثناء نداء LLM). البُناة السحابية `_build_openai/_build_google/_build_ilmu` ترفع `ValueError` إن كان `api_key` زائفاً (fail‑closed). بوّابات الكتابة عبر `require_usable`→422. **الفحص (`_probe`):** يبني العميل بالمفتاح المرشّح، `invoke("ping")` بـ`num_predict=1`، يُرجع `(ok, error[:200])`، لا يرفع أبداً.

## 2) Gmail OAuth/PKCE (`routers/gmail.py` · `oauth_store.py` · `gmail_store.py` · `gmail_tool.py`)
**المكتبة:** `google_auth_oauthlib.flow.Flow` (تدفّق خادم ويب) للتطبيق؛ `InstalledAppFlow` للـCLI. **النطاقات:** `gmail.readonly` + `gmail.send`.
**النقاط:** `GET /users/{id}/gmail/status`→`GmailStatus{connected,email}` · `GET …/gmail/auth-url`→`GmailAuthUrl{auth_url}` (كلاهما self فقط) · `GET /gmail/callback?code=&state=` (**عام، بلا bearer**) · `DELETE …/gmail/disconnect`.

**الخطوة A — authorize:** حارس `_require_self`؛ 503 إن غاب ملف الاعتماد. `Flow.from_client_secrets_file(_GMAIL_CREDENTIALS_FILE, scopes, redirect_uri=GMAIL_REDIRECT_URI)`؛ `authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent", state=str(user_id))`. **PKCE تلقائي** (google‑lib تولّد `code_verifier/challenge` S256 وتضع الـchallenge في الرابط). `offline+consent` يضمنان `refresh_token`. **حفظ الـverifier عبر العمّال:** `oauth_store.pkce_put(str(user_id), flow.code_verifier)` → Redis `oauth:pkce:<user_id>` TTL 600s **fail‑closed** (استبدل dict `_gmail_pending` بالذاكرة الذي كسر مع ≥2 عمّال).

**الخطوة B — callback (عام):** `user_id=int(state)` (400 عند الخطأ)؛ تحميل المستخدم (404)؛ 503 إن غاب الاعتماد؛ `Flow.from_client_secrets_file(..., state=state)`؛ `code_verifier=oauth_store.pkce_pop(str(user_id))` (**GETDEL ذرّي، استخدام واحد**)؛ `flow.fetch_token(code=code, code_verifier=code_verifier)` داخل try/except (كود سيّئ/منتهٍ/مُعاد ⇒ **400 نظيف** لا 500)؛ جلب الإيميل عبر `getProfile` (best‑effort، `""` عند الفشل)؛ **حقن `account_email` في token JSON** ثم `gmail_store.set_token`+commit؛ صفحة HTML عربية RTL (200).

**التخزين (`oauth_store` + `gmail_store`):** `pkce_put(key,verifier)` (setex، fail‑closed) · `pkce_pop(key)` (GETDEL) · `get_token_json/get_status/set_token/clear_token`.

**التجديد (كسول، عند الاستخدام — `gmail_tool._get_gmail_service_for_user`):** `Credentials.from_authorized_user_info(json.loads(token_json), _GMAIL_SCOPES)`؛ `if creds.expired and creds.refresh_token: creds.refresh(GoogleRequest())`. **⚠️ العلّة: creds المُجدَّدة لا تُكتب إلى DB أبداً** (كل نداء يُعيد التجديد). القراءة `messages().list(q=query or "is:unread", maxResults=5)` ثم `get(format="full")` + `_decode_body` (base64url). الإرسال `MIMEMultipart`→`urlsafe_b64encode`→`messages().send(body={"raw":…})`. **CLI:** `InstalledAppFlow.run_local_server(port=0)`→`gmail_token.json`.

## 3) حلقة وظائف Redis (`services/jobs.py` · `jobs/runner.py` · `routers/jobs.py`)
**القائمة:** `QUEUE="rag:jobs:queue"` (LIST FIFO) + `_JOB_PREFIX="rag:job:"`+`<id>` (HASH). `_JOB_TTL=3600` · `_STALE_AFTER=600`.
**التوقيعات:** `client()` (singleton كسول؛ `decode_responses=True`, `socket_connect_timeout=10`, `MaintNotificationsConfig(enabled=False)`) · `enqueue(type,agent_id,uid,payload)->jid` (jid=`uuid4().hex[:12]`؛ يكتب HASH ثم LPUSH) · `get(jid)` (يفكّ payload/result JSON) · `request_cancel/is_cancelled` · `claim_next(timeout=5)` (**RPOP + sleep(0.5) — ليس BRPOP**، تفادٍ متعمّد لإسقاط WSL2/proxy للاتصالات الخاملة) · `set_running/update_progress(0..100)/finish/fail/cancelled` · `reclaim_stale()->int` · `worker_id()`.
**API:** `GET /jobs/{id}`→`JobOut` (404/503/403) · `POST /jobs/{id}/cancel`. `JobOut{job_id,type,status,progress,detail?,agent_id?,result?,error?,cancel_requested,created_at?,updated_at?,finished_at?}` + `from_job(dict)`.

**التدفّق:** المنتِج (أي عامل ويب، مثل `routers/rag.py`) `enqueue("reindex"/"summary_export"/"summary_build", …)`؛ Redis معطّل⇒503. المُشغِّل (`python -m jobs.runner`، عملية مستقلة خارج مجمّع nginx): تهيئة (`load_dotenv`+`_init_rag`) ثم `jobs._r=None` (إسقاط اتصال ping الخامل أثناء تهيئة 15–30s)؛ الحلقة: `reclaim_stale` كل 60s → `claim_next` → فحص إلغاء (queued‑cancel) → `set_running` → dispatch حسب `type` → إعادة فحص إلغاء → `finish(result)`/`fail(str(e))`. `handle_reindex` يشغّل البناء المتزامن في خيط daemon ويستطلع `PROGRESS.snapshot()` كل ثانية (`_percent` يخرّط المراحل 0..100). **إلغاء الجاري best‑effort** (لا خطّاف مقاطعة)؛ **المطبور فقط قابل للإلغاء فعلاً.**

**⚠️ لا إعادة محاولة ولا DLQ في alpha.** الإنقاذ الوحيد `reclaim_stale()`: `status==running` أقدم من `_STALE_AFTER=600s` ⇒ يُعاد لـ`queued` (worker_id يُمسح، `detail="requeued after stale runner"`) + LPUSH. الحالات النهائية (done/error/cancelled) ⇒ `EXPIRE _JOB_TTL`. **مخطّط summary الثانوي** (`summarize_jobs.py`): `rag:summary_job:<id>`(+`:cancel`) مرآة حالة، مجسور عبر `handle_summary_build` بنفس الـid + نبضة تقدّم على `rag:job:<id>` كي لا يُعاد كـstale.

## 4) المطابقة إلى AIZZAK

### 4.1 Credentials: Fernet → Vault Transit (async)، مع إبقاء الأسبقية
`06 §3` يستبدل جدولَي Fernet بـ**Credential aggregate واحد**: `{workspace_id (NULL=platform), provider: ProviderRef, scope: platform|user, ciphertext_ref: CipherRef, status}`.
- `provider_global_keys → scope=platform` (`workspace_id IS NULL`, **INV‑C1**) · `user_provider_keys → scope=user`.
- `key_enc` (Fernet token) → **`CipherRef` = ciphertext من Vault Transit + encryption‑key‑id، لا خام**. التشفير ينتقل من `cryptography.fernet` داخل العملية إلى **منفذ `SecretsProvider` async** (`02 §1.9`): `async encrypt(key_name, plaintext)->str` / `async decrypt(key_name, ciphertext)->bytes`، مفتاح `transit/keys/tenant-secrets` (مشترك credentials+integrations، `05 §3.2`, **SEC‑07**). **كل `_encrypt/_decrypt` تصير `await`.**
- `resolve_key` (user→global→seed→None) → **`ProviderResolver/CredentialResolver`** (`02 §3.5`): `async resolve(ctx, provider)->ResolvedKey`، مفتاح المستخدم النشط ثم مفتاح platform، **لا رجوع بين المزوّدين** (**D‑16**). **يُسقَط طابق env seed** (لا `SERVICE_API_KEY`) — مفاتيح platform صفوف Credential صريحة. اختيار المزوّد **من جدول إعداد** (`FR‑73`) لا فروع كود مثل `model_registry.PROVIDERS`.
- **يُبقى حرفياً:** المفتاح الخام لا يغادر (**INV‑C2**: `ciphertext_ref` لا يُعاد، فكّ التشفير داخل حدود المنصّة فقط عبر `SecretsProvider`)؛ الموقف fail‑closed (alpha 409/503) — لكن «مفتاح مفقود⇒إخفاء النماذج» يُستبدَل بصحّة Vault AppRole. الفصل بين البوّابة (→RBAC `access` + `usage.EnforceLimit→Decision`) والجلب (→`CredentialResolver`). أحداث `CredentialAdded/Revoked` (`04 §5` داخلية فقط، لا Streams في v1).

### 4.2 Gmail → `integrations.Connection` + `ConnectorProvider`، تجديد كسول
`06 §9` يُعمّم Gmail أحادي‑المستأجر إلى **Connection لكل workspace**: `{connector_key, scopes, token_ref: CipherRef, expires_at, status}`.
- token JSON blob → **`OAuthTokens` VO** (access+refresh+expiry+scopes، عابر، لا يُخزَّن خاماً) + `token_ref: CipherRef` (Transit، **INV‑I1/FR‑121**). `account_email` يصير حقل `display_name/metadata` على Connection **لا داخل ciphertext**.
- `routers/gmail.py` → منفذ `ConnectorProvider` (`02 §1.11`): `authorize_url(redirect_uri, state, scopes)` (A) · `async exchange_code(code, redirect_uri)->OAuthTokens` (B) · use‑cases `BeginConnection/CompleteOAuth` (`06 §9`). القاعدة من `OAUTH_REDIRECT_BASE_URL`.
- verifier PKCE في Redis (`oauth:pkce`، GETDEL أحادي، fail‑closed) **نمط قابل لإعادة الاستخدام مباشرة** — لكن **يُعاد مفتحته بـ`state` عشوائي أحادي‑الاستخدام** (قيمته `{workspace_id, verifier}`)، ما **يغلق ثغرة CSRF** في alpha (`state==user_id` المكشوف — TODO C5 في `oauth_store.py`). عبر منفذ `CacheProvider` (`02 §1.7`) لا redis خام.
- **التجديد الكسول (FR‑124) = ثابتة أولى (INV‑I3):** قبل كل استخدام افحص `expires_at` (بـ`OAUTH_REFRESH_SKEW_S=60`) و`await ConnectorProvider.refresh(refresh_token)` إن انتهى — بلا وحدة جدولة. **يُصلح علّة alpha:** يجب **حفظ `OAuthTokens` المُجدَّدة** إلى `token_ref` + إصدار `TokenRefreshed(connection_id, expires_at)` (`04 §5` داخلي). (Google قد يُدوّر `refresh_token` ⇒ احفظه أيضاً.)
- **MCP:** alpha بلا MCP؛ AIZZAK يضيف `McpServer` بـ`McpEndpoint.transport ∈ {http,sse}` **بعيد فقط** (**INV‑I2**، `MCP_ALLOWED_TRANSPORTS=http,sse`)، stdio/عملية فرعية مرفوض. الاستهلاك عبر منافذ `ToolCatalog/MCPClient` لا اقتران مباشر (**INV‑I4**).

### 4.3 Redis jobs → Outbox + Streams + Consumer Groups
- **ذرّية المنتِج:** استبدل `enqueue` (LPUSH ناري) بـ**Transactional Outbox** (**D‑18**): الأثر النطاقي + صفّ `platform.outbox` (مظروف CloudEvents 1.0، `04 §1`) في **معاملة Postgres واحدة** — لا وظائف ضائعة (LPUSH في alpha قد يسقط عند تعطّل بين كتابة HASH والدفع). `outbox_relay` يستطلع ويعمل `XADD stream.<module> * ce <json>`.
- **القائمة → Streams لكل وحدة + Consumer Groups** (**D‑20**): dispatch‑حسب‑type في alpha (reindex/summary_export/summary_build) يصير **streams مطبوعة** — عمل alpha = ابتلاع knowledge (`stream.files→cg.knowledge` عامل knowledge) وتوليد media (`stream.media→cg.media`). استهلاك `XREADGROUP … BLOCK + XACK` (حجب سليم **يتفوّق على RPOP+sleep(0.5)**).
- **الدلالة (D‑19):** at‑least‑once + مستهلك idempotent عبر `INSERT (consumer_group, event_id) INTO platform.processed_events` في معاملة الأثر (تكرار⇒PK conflict⇒تخطٍّ صامت+XACK) + **DLQ بعد N=5** (`stream.<m>.dlq`). يستبدل نموذج alpha بلا‑إعادة/بلا‑DLQ الذي شبكة أمانه الوحيدة `reclaim_stale()` (600s). مفاتيح عمل طبيعية تعزّز الإدمبوتنسي (`UNIQUE(document_id, seq)`).
- `JobOut` (poll/cancel/progress 0..100) → `media.MediaJob {status: queued|running|succeeded|failed, result_file_id}` (`06 §8`) بـAPI 202‑ثم‑استطلاع (`08 §media`). **التقاط `usage` خارج المجرى صراحةً** (`FR‑131`, EVT‑10) — منفذ وارد متزامن لا مجرى. هويّة العامل (`worker_id`) وطوابع `indexed_at/registry_at` تُستوعَب في `platform.stream_offsets` + دفاتر Consumer Group.

## 5) مخاطر ونقاط قرار
1. **⚠️ انحراف docstring↔كود (jobs):** docstrings تزعم BRPOP، الكود RPOP + sleep(0.5) (تفادي WSL2/proxy). AIZZAK `XREADGROUP…BLOCK` يزيل القيد — **لا تنقل حلّ الاستطلاع** ولا تُصدّق docstrings alpha حرفياً.
2. **⚠️ علّة تجديد Gmail (حقيقية):** `creds.refresh()` لا يُكتب إلى `user_gmail_tokens` أبداً. `FR‑124` يجب أن **يحفظ ويُصدر `TokenRefreshed`**؛ أكّد حفظ `refresh_token` المُدوَّر إن دوّره Google.
3. **⚠️ CSRF (state==user_id):** state عدد صحيح مكشوف غير أحادي. AIZZAK يستخدم **state عشوائياً أحادياً مربوطاً بالـverifier** — لا تنقل مخطّط alpha. `CompleteOAuth` يصادق state خادمياً ويربط Connection بـ`workspace_id` الصحيح (الـcallback العام سطح CSRF حسّاس).
4. **بذرة platform تجريبية؟** AIZZAK يُسقط طابق env seed (لا `SERVICE_API_KEY`) ⇒ سلوك ilmu «trial جاهز» يحتاج **Credential platform مبذور صراحةً** عند التزويد إن بقي مطلوباً. **مفتوح: هل مفتاح trial مبذور ضمن v1؟**
5. **حبيبية مفتاح Transit:** مفتاح واحد `tenant-secrets` لـcredentials+integrations (SEC‑07). alpha استخدم مفتاح Fernet واحداً أيضاً (لا انحدار) — **أكّد وجود أدوات تدوير/rewrap** (Transit يعيد التشفير كسولاً؛ alpha بلا شيء).
6. **شكل client‑secrets:** مستنتَج من استخدام google‑lib (لا يُحلَّل في كود alpha: `installed`/`web` مع `client_id/secret/uris`). إن خُزّنت اعتمادات الموصّل في Vault KV، أكّد حقول كل موصّل من كتالوج الموصّلات لا افتراض تخطيط Google.
7. **مخطط payload:** `payload: dict` غير مطبوع في alpha. مظروف CloudEvents يتطلّب **JSON Schema لكل نوع** (`events/schemas/`, **DD‑08**) ونوعاً مُصدَّراً `.vN` — تُشكَّل reindex/summary_export/summary_build رسمياً (مثل `knowledge.document.registered.v1`, `media.job.requested.v1`) قبل إعادة الاستخدام.
