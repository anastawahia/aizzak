<div dir="rtl">

# مرجع أوامر المكدّس — AIZZAK

> **ما هذه الوثيقة؟** جدولٌ واحدٌ يجمع كلّ أوامر تشغيل المكدّس ووظيفة كلٍّ منها، يليه شرحٌ مفصّلٌ لكلّ أمر: ماذا يفعل بالضبط، ولماذا هو مكتوبٌ بهذه الصيغة تحديداً، وما الذي يفشل إن أُهمل.
>
> **ما ليست هي؟** ليست مصدر الحقيقة. المرجع **المُلزِم** هو [`docs/design/08-local-runbook.md`](design/08-local-runbook.md)، والدليل العمليّ الأشمل (بالمنافذ والأعطال والإثباتات) هو [`docs/quickstart.md`](quickstart.md). **عند أيّ تعارضٍ فـ08 هي الصحيحة.**
>
> ⚠️ **كلّ أمرٍ هنا يُنفَّذ داخل WSL** (`Ubuntu-24.04`) من `/home/AIZZAK`، لا من Git‑Bash على ويندوز: قناة Git‑Bash لا تنفّذ ثنائيّات ELF (`Exec format error`).
>
> **آخر مطابقةٍ مع الواقع: 2026‑08‑05.**

---

## الجدول الجامع

### أ) الإعداد والإقلاع

| # | الأمر | الوظيفة |
|---|---|---|
| 1 | `cp .env.example .env` | إنشاء ملفّ البيئة من القالب — الخطوة الوحيدة قبل أوّل إقلاع |
| 2 | `docker compose up -d` | إقلاع المكدّس كاملاً بالترتيب الصحيح (بيانات ← تهيئة ← تطبيق ← حافّة) |
| 3 | `docker compose up migrate` | تشغيل الهجرات الإحدى عشرة والمنح — بديل `alembic upgrade head` الممنوع |
| 4 | `docker compose up -d nginx-certs` | توليد شهادة TLS الموقّعة ذاتيّاً عند رفض `https://localhost` الاتّصال |
| 5 | `docker compose up -d vault-bootstrap` | إعادة بذر Vault (سياسة · AppRole · أسرار) بعد تثبيت `VAULT_ROLE_ID` |

### ب) الفحص والإثبات

| # | الأمر | الوظيفة |
|---|---|---|
| 6 | `docker compose ps` | حالة كلّ خدمة — وأوّل ما يُراجَع فيه هو **`nginx`** لا `app` |
| 7 | `curl -s http://localhost/health` | فحص الحياة عبر الحافّة (‏HTTP) — يعبر رابط nginx ← app فعليّاً |
| 8 | `curl -sk https://localhost/health/ready` | فحص الجاهزيّة فوق TLS؛ `-k` لأنّ الشهادة موقّعةٌ ذاتيّاً |
| 9 | `python3 deploy/smoke/ws_smoke.py localhost 80` | إثبات ترقية WebSocket عبر `ws://` — 101 ثمّ إغلاق 1008 |
| 10 | `python3 deploy/smoke/ws_smoke.py localhost 443 --tls` | الإثبات نفسه فوق `wss://` — يكشف انكسار الترقية في طبقة TLS وحدها |
| 11 | `docker compose exec app python /app/deploy/smoke/stack_smoke.py` | إثبات مسار البيانات الحيّ: OPS‑02 · عزل RLS · توقيع MinIO المسبق |
| 12 | `docker compose exec -e VAULT_SECRET_ID=<id> app python /app/deploy/smoke/approle_smoke.py` | إثبات سياسة Vault في **الاتّجاهين**: المسموح يعمل والمحجوب **مرفوضٌ فعلاً** |
| 13 | `docker compose logs -f app` | متابعة سجلّ التطبيق حيّاً — أوّل خطوةٍ في أيّ عطل |

### ج) العمّال والمُرحّل

| # | الأمر | الوظيفة |
|---|---|---|
| 14 | `WORKER=memory docker compose --profile workers up -d worker` | إقلاع عامل الذاكرة (الوحيد المقيس حيّاً) خلف `profile` الاختياريّ |
| 15 | `WORKER=knowledge docker compose --profile workers up -d worker` | إقلاع عامل المعرفة — موصولٌ بالكامل ولم يُقلَع حاويّاً بعد |
| 16 | `WORKER=media docker compose --profile workers up -d worker` | إقلاع عامل الوسائط — يحتاج توجيه `image` في `PROVIDER_ROUTING` |
| 17 | `docker compose up -d outbox-relay` | إقلاع مُرحّل Outbox: من جدول `platform.outbox` إلى مجاري Redis |
| 18 | `docker compose exec redis redis-cli XLEN stream.<module>` | قياس طول المجرى — `XACK` لا يحذف، فالنموّ بلا حدٍّ سلوكٌ افتراضيّ |

### د) الأسرار — Vault وAppRole

| # | الأمر | الوظيفة |
|---|---|---|
| 19 | `docker compose exec vault vault read -field=role_id auth/approle/role/app/role-id` | قراءة `role_id` (معرّفٌ ثابت، ليس سرّاً) |
| 20 | `docker compose exec vault vault write -f -field=secret_id auth/approle/role/app/secret-id` | توليد `secret_id` جديد — **السرّ الوحيد في البيئة**، لمرّةٍ واحدة |
| 21 | `VAULT_TOKEN= VAULT_SECRET_ID='<id>' docker compose up -d app` | إعادة إقلاع التطبيق بوضع AppRole؛ `VAULT_TOKEN=` الفارغ **هو المُبدِّل** |
| 22 | `docker compose exec vault cat /vault/init/init.json` | قراءة مفتاح فكّ الختم والتوكن الجذر — نصٌّ عاديٌّ على حجم `vault-init` |
| 23 | `docker cp vault-init-backup.json aizzak-vault-1:/vault/init/init.json` | استعادة مفتاح فكّ الختم إلى الحجم قبل إعادة إقلاع Vault |
| 24 | `docker compose restart vault` | إعادة تشغيل Vault — **لا تمحو شيئاً** منذ أن صار التخزين `file` دائماً |

### هـ) البوّابات الخمس ودورة التطوير

| # | الأمر | الوظيفة |
|---|---|---|
| 25 | `ruff format --check .` | البوّابة 1 — تنسيقٌ موحّد (بلا `--check` تُصلَّح الملفّات مكانها) |
| 26 | `ruff check .` | البوّابة 2 — الفحص الساكن (‏lint) |
| 27 | `mypy src` | البوّابة 3 — تحقّق الأنواع الصارم على `src/` |
| 28 | `lint-imports` | البوّابة 4 — ثمانية عقود طبقاتٍ في `.importlinter` (‏D‑17)، الصفر خرقاً |
| 29 | `pytest` | البوّابة 5 — الحزمة كاملةً؛ اعتماد محلي غائب يتخطّى افتراضاً |
| 30 | `pytest -rs` | المِثل، مع طباعة **اسم وسبب** كلّ تخطٍّ — الفرق بين تخطٍّ سليمٍ ومقنّع |
| 31 | `pytest -m live_db` | مسبارٌ حيٌّ واحد فوق PostgreSQL الحاوي (وكذا `live_qdrant`, `live_minio` …) |
| 32 | `alembic revision -m "..."` | توليد هجرةٍ جديدة (بلا مفاتيح أجنبيّة عابرةٍ بين المخطّطات) |
| 33 | `python -m app.ops.retention` | مكنسة الاحتفاظ — تشغيلٌ **يدويّ**، لا خدمةٌ دائمة |
| 34 | `python -m app.ops.rotate_transit` | تدوير مفتاح Vault Transit — تشغيلٌ **يدويّ** بدور مقصورٍ على `ciphertext_ref` |
| 35 | `docker compose -f docker-compose.yml -f docker-compose.test.yml up -d` | **الصيغة المعتمَدة للحزمة الاختباريّة** — الوحيدة التي تنشر منفذ التضمين وتُحضر سكربت قاعدة الاختبار |
| 36 | `docker compose … exec -T postgres sh /opt/aizzak/testdb/20-test-database.sh` | تزويد `aizzak_test` — **يدويٌّ، مرّةً واحدةً لكلّ حجم**، ولا يُشغَّل تلقائيّاً أبداً. مُعاد التشغيل بأمان |

**الأمر الجامع للبوّابات الخمس** (من ويندوز، بمسارات venv اللينكسيّة):

```bash
wsl -d Ubuntu-24.04 -- bash -lc 'cd /home/AIZZAK && .venv/bin/ruff format --check . && .venv/bin/ruff check . && .venv/bin/mypy src && .venv/bin/lint-imports && .venv/bin/pytest'
```

---

# الشرح المفصّل

## أ) الإعداد والإقلاع

### 1 · `cp .env.example .env`

ينسخ قالب البيئة إلى الملفّ الفعليّ الذي يقرؤه Compose. النسخ وحده **لا يكفي**: القالب يحوي قيماً من طراز `change-me-*` وحقلاً فارغاً واحداً إلزاميّاً. عليك ملء:

- **سبع كلمات مرور**: `POSTGRES_SUPERUSER_PASSWORD` · `AIZZAK_OWNER_PASSWORD` (المُهاجِر ومالك الجداول) · `APP_RW_PASSWORD` (التطبيق والعمّال، محكومٌ بـRLS) · `OUTBOX_RELAY_PASSWORD` · `RETENTION_SWEEPER_PASSWORD` · `METRICS_READER_PASSWORD` · `TRANSIT_ROTATOR_PASSWORD` — ومعها `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`.
- **`FIREBASE_PROJECT_ID`**: فارغٌ في القالب و**إلزاميّ**. `FirebaseAuth` يفشل فشلاً سريعاً عند الإنشاء على قيمةٍ فارغة ⇒ التطبيق **لا يقلع أصلاً**.

⚠️ **حارس `:?` يكشف الفراغ لا `change-me-*`.** كلّ كلمات المرور محميّةٌ في `docker-compose.yml` بـ`${VAR:?...}`، وهذا يرفض **غير المضبوط والفارغ** فقط — أمّا `change-me-rotator` فقيمةٌ مضبوطةٌ غيرُ فارغةٍ تمرّ صامتة. ولا حارسَ آليّ يمكنه كشفها: `.env` غير متعقَّبةٍ في git، فلا اختبارَ يراها ولا CI. الفحص الوحيد يدويّ، و**قبل أوّل `docker compose up`** لأنّ `10-roles.sh` يعمل مرّةً واحدةً عند تهيئة الحجم الأوّل ⇒ سرٌّ ضعيفٌ هنا يُولد مع الدور ويبقى معه.

> لماذا كلماتٌ في ملفٍّ أصلاً بينما لدينا Vault؟ لأنّ **Postgres وMinIO يجب أن يقلعا بكلمة سرٍّ قبل أن يستطيع أحدٌ قراءة كلمة سرٍّ من Vault**. هذا استثناءٌ بالضرورة لا بالسهو، و`deploy/vault/bootstrap.sh` يبذر القيم نفسها في Vault — وتلك هي النسخة **الوحيدة** التي يقرؤها `src/`.

### 2 · `docker compose up -d`

الأمر الرئيس. يُقلع المكدّس كاملاً في الخلفيّة، والترتيب **ليس مسؤوليّتك**: هو مُرمَّزٌ في `depends_on` — تقلع خدمات البيانات، ثمّ أربع خدماتٍ لمرّةٍ واحدة (تهيئة Vault · دلو MinIO · شهادة TLS · الهجرات والمنح)، ثمّ التطبيق والمُرحّل والحافّة.

⏱️ **البناء الأوّل بطيء**: صورة `embedding` **تخبز أوزان النموذج وقت البناء** (‏MiniLM متعدّد اللغات، ~250–470 م.ب) لتعمل بعدها بلا أيّ نداءٍ خارجيّ (`HF_HUB_OFFLINE=1`). البطء مرّةٌ واحدة، ومقابله أثرٌ ثابتٌ لا يتغيّر بين إقلاعين.

⚠️ **المنافذ مُزاحةٌ على المضيف وحده.** أزيلت خدمات البنية الأصلية وصار Compose مصدرها المحليّ الوحيد؛ بقيت الإزاحة لأنها عقد المشغّل وحزام الاختبارات المستقر. داخل الشبكة يحتفظ كل شيء باسمه ومنفذه القانونيّ (`pgbouncer:6432`, `minio:9000` …)، وعلى المضيف: ‏15432 (‏PG) · 16432 (‏PgBouncer) · 16379 (‏Redis) · 19000/19001 (‏MinIO) · 16333 (‏Qdrant) · 18200 (‏Vault). خدمة `embedding` غير منشورة في مسار النشر، وينشرها ملف الاختبار وحده على `127.0.0.1:8080`.

### 3 · `docker compose up migrate`

يشغّل `python -m app.ops.provision` داخل الصورة: **الهجرات الإحدى عشرة بترتيبها الحقيقيّ، والمنحُ معها**.

⚠️ **`alembic upgrade head` ليس أمراً صالحاً هنا**: v1 يشغّل إحدى عشرة سلسلةً مستقلّة (`version_table_schema` لكلّ وحدة)، فـ`head` غامضٌ وAlembic يرفضه بـ«Multiple head revisions are present». التسلسل الحقيقيّ مُرمَّزٌ في `app/ops/provision.py` وحده.

والأدوار الثلاثة تُنشأ **قبل ذلك** عند تهيئة العنقود عبر `deploy/postgres/initdb/10-roles.sh`، لأنّ `CREATE ROLE` صلاحيّةُ عنقودٍ لا يملكها `aizzak_owner` عمداً.

### 4 · `docker compose up -d nginx-certs`

خدمةٌ لمرّةٍ واحدة تولّد شهادة TLS موقّعةً ذاتيّاً في `deploy/nginx/certs/`. تحتاجها حين يرفض `https://localhost` الاتّصال أصلاً — أي حين لم تُولَّد الشهادة بعد. بعدها **أعد تشغيل `nginx`** ليلتقطها.

### 5 · `docker compose up -d vault-bootstrap`

يعيد تشغيل خدمة بذر Vault: السياسة (`app-policy.hcl`) وAppRole والأسرار الأوّليّة. الحالة النموذجيّة لاستعماله: `role_id` صار غير صالحٍ بعد أوّل إقلاع لأنّ الدور أُنشئ بمعرّفٍ **عشوائيّ** حين لم يكن `AIZZAK_APPROLE_ROLE_ID` / `VAULT_ROLE_ID` مضبوطَين. ثبّتهما في `.env` ثمّ شغّل هذا — والدور نفسه يبقى بعدها عبر أيّ إعادة تشغيل.

---

## ب) الفحص والإثبات

### 6 · `docker compose ps`

حالة كلّ خدمة. لكن **لا تقرأه كدليل**: العلّة #10 كانت nginx يوجّه إلى عنوانٍ ميّت ويجيب **502** للعالم بينما `ps` يقول إنّ كلّ شيءٍ `healthy` وسجلّ التطبيق مليءٌ بـ`200`. السبب أنّ كلّ فحصٍ يسبر خدمةً **من داخل نفسها**. لذلك: **راجع حالة `nginx` نفسها أوّلاً** — فحصها هو الوحيد الذي يعبر الرابط على المخطّطين معاً (‏`http` و`https`).

### 7 · `curl -s http://localhost/health`

فحص الحياة (liveness) عبر الحافّة. قيمته أنّه **يعبر nginx ← app فعليّاً**، فيكشف ما لا يكشفه أيّ فحصٍ داخليّ.

### 8 · `curl -sk https://localhost/health/ready`

فحص الجاهزيّة فوق TLS. `-k` ضروريٌّ لأنّ الشهادة موقّعةٌ ذاتيّاً.

⚠️ `/health/ready` **لا يلمس أيّ تبعيّة عمداً**: الجاهزيّة تعني «انتهى الإقلاع»، لا «التبعيّات حيّة». هذا قرارٌ لا نقص، ونتيجته المباشرة: عند انتهاء `token_ttl` لتوكن AppRole يعجز التطبيق عن لمس أيّ سرّ مستأجِر بينما `/health/ready` **أخضر طوال الوقت**. فحاويةٌ صحيحة ليست بنفسها دليلاً على مسار بيانات — ولهذا وُجدت `deploy/smoke/`.

### 9–10 · `ws_smoke.py`

```bash
python3 deploy/smoke/ws_smoke.py localhost 80
```

```bash
python3 deploy/smoke/ws_smoke.py localhost 443 --tls
```

يثبت أنّ ترقية WebSocket تعبر الحافّة فعلاً: **101 Switching Protocols** ثمّ إغلاقٌ بالرمز **1008** (انتهاك سياسة — أي أنّ حارس المصادقة عمل، وهذه نتيجةٌ صحيحة لا عطل). تشغيله على المخطّطين ضروريٌّ لأنّ ترقية WS قد تنكسر في طبقة TLS وحدها بينما `ws://` سليم.

### 11 · `stack_smoke.py`

```bash
docker compose exec app python /app/deploy/smoke/stack_smoke.py
```

الإثبات الحيّ لمسار البيانات من داخل حاوية التطبيق: **OPS‑02** · **عزل RLS** فعليّاً (لا مجرّد وجود السياسة) · **التوقيع المسبق** لروابط MinIO. هذا هو ما يجيب سؤال «هل المكدّس يعمل؟» — لا `ps`.

### 12 · `approle_smoke.py`

```bash
docker compose exec -e VAULT_SECRET_ID=<id> app python /app/deploy/smoke/approle_smoke.py
```

يثبت سياسة Vault في **الاتّجاهين**: ما تسمح به السياسة يعمل، وما تحجبه **مرفوضٌ فعلاً**. الفارق جوهريّ — قراءة السياسة تثبت أنّ النصّ خُزِّن، لا أنّ التوكن محكومٌ به.

### 13 · `docker compose logs -f app`

متابعة السجلّ حيّاً. لاحظ قاعدةً متكرّرة في هذا المشروع: **`exec` لا `run`** عند التقاط المخرجات — مخرجات `exec` لا يلتقطها سائق سجلّات `json-file`، ومخرجات خدمةٍ يلتقطها **ويحتفظ بها**.

---

## ج) العمّال والمُرحّل

### 14–16 · إقلاع عامل

```bash
WORKER=memory docker compose --profile workers up -d worker
```

خدمة `worker` خلف `profile` **لأنّ اختيار العامل قرارُ نشرٍ صريح**، لا لأنّ شيئاً مكسور. ومتغيّر `WORKER` يُصرَّح به عمداً رغم أنّ الافتراضيّ صار `memory` أيضاً — التصريح أوضح للقارئ من الاتّكال على افتراضيٍّ قد يتغيّر.

| القيمة | الحالة |
|---|---|
| `memory` | ✅ يقلع ويعمل — **الوحيد المقيس حيّاً** |
| `knowledge` | 🟡 موصولٌ بالكامل (`DocumentContentResolver` مبنيّ) ولم يُقلَع حاويّاً بعد |
| `media` | 🟡 موصولٌ بالكامل (`WorkerMediaGenerator` مبنيّ)؛ يحتاج توجيه `image` في `PROVIDER_ROUTING` واعتماداً باسم `image:openai` |

> لم يعد أيّ عاملٍ ينهار **لنقصِ محوّل**. فإن انهار عاملٌ اليوم فهو **عطلٌ حقيقيٌّ يستحقّ القراءة**: بيئةٌ لا نقصُ كود (‏Vault · سرّ MinIO مشوَّه · `PROVIDER_ROUTING` يسمّي مزوّداً بلا محوّل) — والرسالة تسمّي السبب.

### 17 · `docker compose up -d outbox-relay`

يُقلع مُرحّل Outbox (نسخةٌ واحدة): يقرأ `platform.outbox` وينشر على مجاري Redis.

✅ **لم يعد الترتيب مُلزِماً.** مجموعات المستهلكين ما تزال تُنشأ عند `$` (ذيل المجرى)، لكنّ المُرحّل نفسه يضمنها الآن قبل أوّل نشرٍ على أيّ مجرًى — قبل أن يُقلع أيّ عامل (‏[design/08-local-runbook.md §4](design/08-local-runbook.md)، `stream-topology-plan.md`).

### 18 · `XLEN stream.<module>`

⚠️ **`XACK` لا يحذف.** مجاري Redis سجلٌّ إلحاقيّ: `XACK` يمسح قائمة المعلَّق فقط، فـ`stream.<وحدة>` ينمو بلا حدٍّ **حتّى مع عمّالٍ أصحّاء يُقرّون كلّ شيء**، وRedis هنا على `maxmemory 0`/`noeviction` ⇒ لا سقف في أيّ طبقة.

العلاج المنفَّذ هو `STREAM_MAXLEN` (افتراضيّ 100000؛ `0` يعطّل القصّ). لكن **راقب `XLEN` ولا تعامل السقف كحلّ**: القصّ قد يُسقط مدخلاتٍ لم يقرأها مستهلكٌ متعثّر وصفُّ الـoutbox موسومٌ `published` سلفاً ⇒ **فقدٌ حقيقيّ لا إعادة تسليم**.

---

## د) الأسرار — Vault وAppRole

### القاعدة الحاكمة: أيّ المتغيّرات مضبوطة **هو** المُبدِّل

`create_vault_client` **يفضّل التوكن متى وُجد**:

| الوضع | `VAULT_TOKEN` | `VAULT_ROLE_ID` | `VAULT_SECRET_ID` |
|---|---|---|---|
| Token (تجاوزٌ يدويّ — نادر) | توكنٌ جذرٌ حقيقيّ | — | — |
| **AppRole (الوضع العاديّ، محلّيّاً وفي كلّ بيئة)** | **فارغ** | من Vault | يُحقن من الصَّدَفة، **لا من `.env`** |

⚠️ `VAULT_TOKEN=` الفارغ **ليس قيمةً ناقصة، بل هو المُبدِّل**. وتوكنٌ مضبوطٌ بجانب زوج AppRole يعني أنّ الزوج لا يُستعمل إطلاقاً — **والنشر يبدو ناجحاً**.

### 19 · قراءة `role_id`

```bash
docker compose exec vault vault read -field=role_id auth/approle/role/app/role-id
```

`role_id` **معرّفٌ ثابتٌ لا سرّ** — يجوز وضعه في `.env` (ويُستحسن تثبيته بـ`AIZZAK_APPROLE_ROLE_ID`، وإلّا وُلِّد عشوائيّاً عند أوّل بذر).

### 20 · توليد `secret_id`

```bash
docker compose exec vault vault write -f -field=secret_id auth/approle/role/app/secret-id
```

`-f` تعني «بلا حقول» — العمليّة كتابةٌ تولّد قيمةً جديدة. هذا **السرّ الوحيد في البيئة** و**لا يُكتب في `.env` أبداً**.

### 21 · إقلاع التطبيق بوضع AppRole

```bash
VAULT_TOKEN= VAULT_SECRET_ID='<الملتقَط>' docker compose up -d app
```

يُمرَّر `VAULT_SECRET_ID` **من الصَّدَفة**: تفسير Compose يستشير البيئة الحقيقيّة أيضاً، **والصَّدَفة تفوز**. و`VAULT_TOKEN=` الفارغ يُبقي الوضع على AppRole.

⚠️ **عمر التوكن مقيسٌ لا مُفترَض:** `token_ttl=1h` لتوكن AppRole الذي يستعمله التطبيق (لا علاقة له بالتوكن الجذر). وVault على **مسار التشغيل** لا الإقلاع وحده، فالعَرَض تطبيقٌ يخدم الطلبات ساعةً ثمّ يعجز عن لمس أيّ سرّ مستأجِر — و`/health/ready` أخضر طوال الوقت. العلاج منفَّذ: إعادة مصادقةٍ **واحدة** عند 403/401 ثمّ إعادة المحاولة.

### 22 · `cat /vault/init/init.json`

```bash
docker compose exec vault cat /vault/init/init.json
```

Vault **لم يعد بوضع `-dev`**: يعمل بتخزين `file` دائمٍ على حجمين مُسمَّيَين — `vault-data` (‏KV + Transit + AppRole) و`vault-init` (مفتاح فكّ الختم + التوكن الجذر، منفصلٌ عمداً). و`deploy/vault/start.sh` يقود Vault خلال `operator init` (أوّل إقلاعٍ فقط) أو `operator unseal` (كلّ إقلاعٍ بعده) **قبل** أن يستطيع فحص الصحّة الإبلاغ بالنجاح — وهو ما يجعل `depends_on: {condition: service_healthy}` يعني: Vault **مفكوك الختم واستُخدِم فعلاً**، لا مجرّد «يستجيب».

⚠️ لا KMS محلّيّاً، فـ`start.sh` يكتب **مفتاح فكّ الختم والتوكن الجذر في ملفٍّ نصّيٍّ عاديّ** بصلاحيّة `chmod 600`. أيّ من يصل ذلك الحجم — نسخةً احتياطيّةً غير مشفّرة أو وصولاً لجذر المضيف — يفكّ ختم Vault ويقرأ **كلّ** سرّ يحرسه. **مقبولٌ لمضيفٍ واحدٍ محلّيّ فقط، وغير مقبولٍ إطلاقاً لإنتاجٍ حقيقيّ.** مسار الترقية: auto-unseal عبر KMS خارجيّ (`seal "awskms"` / `"gcpckms"` / `"azurekeyvault"`)، أو Transit auto-unseal عبر مثيل Vault **آخر** خارج هذا المضيف — وكلاهما يحتاج تلك الخدمة موجودةً مسبقاً، فلم يُنفَّذ هنا.

### 23 · النسخ الاحتياطيّ والاستعادة

```bash
docker compose exec vault cat /vault/init/init.json > vault-init-backup.json
```

انسخه إلى مكانٍ **مشفَّرٍ خارج المستودع فور أوّل إقلاع**: هو المفتاح الوحيد لفكّ ختم Vault على هذا الحجم، وفقدانه بينما `vault-data` قائمٌ يعني بياناتٍ **غير قابلةٍ للفكّ إلى الأبد** — تماماً كفقدان مفتاح Transit نفسه.

والاستعادة (فُقد حجم `vault-init` بينما `vault-data` سليم):

```bash
docker cp vault-init-backup.json aizzak-vault-1:/vault/init/init.json
```

ثمّ أعد تشغيل `vault` — يجد `start.sh` الملفّ ويفكّ الختم به بدل محاولة تهيئةٍ جديدة (وهو يرفض ذلك تلقائيّاً إن وجد Vault مُهيَّأً بلا ملفٍّ محليٍّ يطابقه).

### 24 · `docker compose restart vault`

**لا يمحو شيئاً بعد الآن**: KV والTransit وAppRole تبقى، ومفتاح Transit **نفسه** لا يتجدّد. ولا حاجة لإعادة تشغيل `vault-bootstrap` بعده — القاعدة الذهبيّة القديمة «`up -d` لا `docker start`» عادت غير ضروريّةٍ **لهذا السبب تحديداً**، وإن بقيت صحيحةً لأسبابٍ أخرى.

---

## هـ) البوّابات الخمس ودورة التطوير

تُشغَّل من داخل WSL بعد `source .venv/bin/activate` (أو بمسار `.venv/bin/` صراحةً — أدوات venv اللينكسيّة لا تعمل من Git‑Bash).

### 25–26 · `ruff format --check .` و`ruff check .`

الأولى تتحقّق من التنسيق دون تعديل (احذف `--check` لتصلح الملفّات مكانها)، والثانية هي الفحص الساكن. آخر قياسٍ مغلق: ✅ 471 ملفاً.

### 27 · `mypy src`

تحقّق الأنواع الصارم (‏`--strict` مضبوطٌ في `pyproject.toml`) على `src/` وحدها. آخر قياسٍ مغلق: ✅ 334 ملفاً.

### 28 · `lint-imports`

**البوّابة المعماريّة.** ينفّذ ثمانية عقودٍ في `.importlinter` (‏`D‑17`) تفرض قاعدة الاعتماد آليّاً: الاتّجاه للداخل فقط · `domain/` لا يستورد شيئاً تقنيّاً · **لا أحد يستورد `infrastructure/` إلّا `framework/di/composition_root.py`**. آخر قياس: ✅ **8/0**.

### 29–31 · `pytest`

```bash
pytest
```

الحزمة كاملةً. كلّ وسم بنيةٍ محليّة يسبر عنوانه ويتخطّى عند غيابه في التشغيل العادي. بعد رفع الحزمة الاختبارية استعمل `REQUIRE_LIVE=1 pytest -rs` كي يصير أي غيابٍ فشلاً صريحاً؛ وهذا هو نمط وظيفة التكامل في CI.

```bash
pytest -rs
```

يطبع **اسم وسبب كلّ تخطٍّ** — وهو ما فعله CI، والفرق الوحيد بين «تخطٍّ سليم» و«تخطٍّ يخفي عطلاً».

```bash
pytest -m live_db
```

مسبارٌ واحد فوق PostgreSQL الحاوي (وكذا `live_qdrant` · `live_minio` · `live_vault` …)، بينما `live_ollama` يقصد خدمة WSL الأصلية. قاعدة الاختبار `aizzak_test` بدورَي `aizzak_owner` و`app_rw`، و`.env.test` يحمل الاعتمادات الفعلية وكلّ عناوين `TEST_*`.

⚠️ إذا غاب `.env.test` لا يعني ذلك أن كل شيء سيتخطّى: المسبار قد يجد المنفذ ثم تفشل المصادقة بصوتٍ عالٍ. MinIO مثالٌ مقصود: `_MINIO_SECRET_DEFAULT` هي `aizzak-test-secret`، وبعد تدوير الحساب تنتج `SignatureDoesNotMatch`. حارس D‑1 يرفض اختلاف `MINIO_TEST_SECRET_KEY` و`TEST_MINIO_SECRET_KEY` عندما يكونان مضبوطين معاً، لكنه لا يربط الاسمين بنيوياً.

⚠️ الوسم `integration` **حُذف**: كان مُعلَناً في `pyproject.toml` وغيرَ مستعمَلٍ على أيّ اختبار، فـ`pytest -m "not integration"` لم يكن يستثني شيئاً و`pytest -m integration` كان يجمع صفراً **ويخضرّ**. لا تكتبه في أمرٍ جديد — البوّابة هي `pytest` وحدها.

### 32 · `alembic revision -m "..."`

يولّد هجرةً جديدة. القيد المعماريّ: **لا مفاتيح أجنبيّة عابرةً بين المخطّطات** (كلّ وحدةٍ مخطّطٌ وسلسلة هجراتٍ مستقلّة). وللتطبيق استعمل `provision` لا `alembic upgrade` (‏§3 أعلاه).

### 33–34 · عمليّات يدويّة

```bash
python -m app.ops.retention
```

مكنسة الاحتفاظ — تحذف من الجداول الثلاثة غير المحدودة بدور `RETENTION_SWEEPER` (‏SELECT/DELETE عليها وحدها). **يدويّاً لا خدمةً دائمة.**

```bash
python -m app.ops.rotate_transit
```

تدوير مفتاح Vault Transit بدورٍ صلاحيّته `UPDATE` مقصورٌ على العمود `ciphertext_ref` وحده (‏P1‑9). **يدويّاً لا خدمةً دائمة.**

### 35 · الحزمة الاختباريّة — `docker-compose.test.yml`

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d
```

**الصيغة المعتمَدة، ولا بديل لها:** Compose يقرأ `docker-compose.yml` و`docker-compose.override.yml` تلقائيّاً ولا يقرأ هذا الاسم أبداً، فالملفّ **لا يُدمَج إلّا بطلبٍ صريح**. وهذا هو سبب وجوده: نشر منفذ التضمين من `docker-compose.yml` نفسه — بـ`"${HOST_PORT_EMBEDDING:-8080}:8080"` — طُرح و**رُفض** (‏[`release-blockers-plan.md`](release-blockers-plan.md) ن‑3) لأنّ متغيّراً اختياريّاً «يفتح المنفذ دائماً بمجرّد ضبطه في `.env` قد يُنسى مفعَّلاً»، والافتراضيّ `:-8080` أخطر إذ ينشر بلا متغيّرٍ أصلاً. هنا المنفذ **مغلقٌ بالبناء**: أمر النشر لا يسمّي الملفّ، فلا وجود لمفتاح `ports` بالنسبة إليه.

يفعل شيئين لا ثالث لهما:

| | الأثر |
|---|---|
| `embedding` | يُنشَر **`127.0.0.1:8080`** (‏`HOST_PORT_EMBEDDING` يغيّر منفذ المضيف). والبادئة ليست تجميلاً: بدونها يربط Docker كلّ الواجهات وتسبق قواعدُه جدارَ المضيف |
| `postgres` | يُركَّب [`deploy/postgres/testdb/`](../deploy/postgres/testdb/20-test-database.sh) على `/opt/aizzak/testdb` للقراءة فقط |

⚠️ **الرفع لا يُنشئ قاعدة `aizzak_test`.** التركيب **خارج** `/docker-entrypoint-initdb.d` عمداً، فلا شيء يشغّل السكربت تلقائيّاً؛ يُشغّله المشغّل بيده — الأمر **36** أدناه. وهذه هي القاعدة الثابتة للخطّة سارية: **لا قاعدة اختبارٍ في عنقود إنتاج**، وكلّ تزويدٍ اختباريٍّ يدخل من ملفّ تجاوزٍ صريحٍ أو رايةٍ مطفأةٍ افتراضاً — بوّابتان: أن يُسمّى الملفّ بـ`-f`، ثمّ أن يُكتب الأمر.

---

### 36 · تزويد قاعدة الاختبار — الأمر اليدويّ

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d postgres
docker compose -f docker-compose.yml -f docker-compose.test.yml exec -T postgres sh /opt/aizzak/testdb/20-test-database.sh
```

**السطران كلاهما يحتاج `-f` الثانية**، لا الثاني وحده: بدونها لا وجود للتركيب أصلاً فلا وجود للسكربت.

يفعل ثلاثة أشياء: ينشئ `aizzak_test` **مملوكةً لـ`aizzak_owner`**، ويمنح `CONNECT` للأدوار الخمسة الأخرى، ويضبط مخطّط `public` داخلها (`REVOKE CREATE … FROM PUBLIC` ثمّ `GRANT CREATE, USAGE … TO aizzak_owner`). الحصيلة `nspacl` **مطابقةٌ حرفاً بحرف** لما في `aizzak`.

**مُعادُ التشغيل بأمان.** `CREATE DATABASE` لا `IF NOT EXISTS` لها ولا تقبل `DO $$…$$` (‏لا تجري داخل كتلة معاملة)، فالحارس هو `\gexec`: استعلامٌ يُرجع نصّ الـDDL ولا يُنفَّذ إلّا إن عاد بصفّ. عمليّاً: `CREATE DATABASE` تظهر في التشغيل الأوّل وتختفي فيما بعده، والخروج `0` في الجميع.

📌 **التزويد يعيش في الحجم `aizzak_postgres-data`، لا في الحاوية.** بعده يمكن هدم الحاوية وإعادة رفعها **بلا ملفّ التجاوز** والقاعدة باقية — فالملفّ لازمٌ للتزويد وحده لا للاستعمال. ولذلك هو أمرُ **مرّةٍ واحدةٍ لكلّ حجم**؛ ولا يعيده إلّا `down -v` (‏وهو ممنوعٌ لسببٍ آخر: حساب MinIO الاختباريّ في `aizzak_minio-data`).

✅ المحضن يقصد الآن هذا العنقود الحاوي افتراضياً على `127.0.0.1:15432`؛ وبعد تزويد القاعدة حمّل الاعتمادات الفعلية وشغّل الحارس الصارم:

```bash
set -a; . ./.env.test; set +a
REQUIRE_LIVE=1 pytest -rs
```

---

## القواعد الذهبيّة السبع

1. **Vault دائمٌ الآن — `docker compose restart vault` لا يمحو شيئاً**، لكنّ حجم `vault-init` نصٌّ عاديّ: انسخه احتياطيّاً قبل أيّ اعتمادٍ حقيقيّ.
2. **`VAULT_TOKEN` الفارغ هو المُبدِّل** — والتوكن يفوز دائماً حين يوجد.
3. **`VAULT_SECRET_ID` لا يُكتب في `.env` أبداً**؛ يُمرَّر من الصَّدَفة.
4. **حاويةٌ `healthy` ليست دليلاً على مسار بيانات** — الدليل في `deploy/smoke/`.
5. **`XACK` لا يحذف** — راقب `XLEN`.
6. **لا `alembic upgrade head`** — بل `python -m app.ops.provision`.
7. **`.env` مُتَجاهَلٌ في git.** أبقِه كذلك.

---

**للاستزادة:** [`quickstart.md`](quickstart.md) (المنافذ · جدول الأعطال · التحقّق اليدويّ من التدفّق الكامل) · [`design/08-local-runbook.md`](design/08-local-runbook.md) (المرجع المُلزِم) · [`deploy-linux-server.md`](deploy-linux-server.md) · [`deploy-runpod.md`](deploy-runpod.md).

</div>
