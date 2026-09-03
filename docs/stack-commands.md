<div dir="rtl">

# مرجع أوامر المكدّس — AIZZAK

> **ما هذه الوثيقة؟** جدولٌ واحدٌ يجمع كلّ أوامر تشغيل المكدّس ووظيفة كلٍّ منها، يليه شرحٌ مفصّلٌ لكلّ أمر: ماذا يفعل بالضبط، ولماذا هو مكتوبٌ بهذه الصيغة تحديداً، وما الذي يفشل إن أُهمل.
>
> **ما ليست هي؟** ليست مصدر الحقيقة. المرجع **المُلزِم** هو [`docs/design/08-local-runbook.md`](design/08-local-runbook.md)، والدليل العمليّ الأشمل (بالمنافذ والأعطال والإثباتات) هو [`docs/quickstart.md`](quickstart.md). **عند أيّ تعارضٍ فـ08 هي الصحيحة.**
>
> ⚠️ **كلّ أمرٍ هنا يُنفَّذ داخل WSL** (`Ubuntu-24.04`) من `/home/AIZZAK`، لا من Git‑Bash على ويندوز: قناة Git‑Bash لا تنفّذ ثنائيّات ELF (`Exec format error`).
>
> **آخر مطابقةٍ مع الواقع: 2026‑08‑13** (‏[§3.133](log/3.133.md) — تقسيم `worker` إلى ثلاث خدمات؛ ثمّ [§3.134](log/3.134.md) — قياس إقلاع `media` وحذف آخر بروفايل في الملفّ).

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
| 14 | `docker compose up -d worker-memory worker-knowledge worker-media` | إقلاع العمّال الثلاثة صراحةً — وهم يقلعون أصلاً مع `docker compose up -d` كاملاً |
| 15 | `docker compose exec redis redis-cli XINFO CONSUMERS stream.media cg.media` | **هل يستهلك فعلاً؟** `Up` تقول إنّ العمليّة حيّة فقط؛ هذا يقول إنّها مشترِكةٌ وتستقصي (`idle` بالثواني) |
| 16 | `docker compose restart worker-knowledge` | إعادة تشغيل مستهلكٍ واحدٍ دون مسّ الآخرَين — ما كان مستحيلاً قبل التقسيم |
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
| 24‑ب | `docker builder prune` | **أمرُ التنظيف الوحيد المسموح** — لا يمسّ الأحجام. انظر «قاعدة الأحجام» أدناه |

### هـ) البوّابات الخمس ودورة التطوير

| # | الأمر | الوظيفة |
|---|---|---|
| 25 | `ruff format --check .` | البوّابة 1 — تنسيقٌ موحّد (بلا `--check` تُصلَّح الملفّات مكانها) |
| 26 | `ruff check .` | البوّابة 2 — الفحص الساكن (‏lint) |
| 27 | `mypy src` | البوّابة 3 — تحقّق الأنواع الصارم على `src/` |
| 28 | `lint-imports` | البوّابة 4 — ثمانية عقود طبقاتٍ في `.importlinter` (‏D‑17)، الصفر خرقاً |
| 29 | `pytest` | البوّابة 5 — الحزمة كاملةً؛ اعتماد محلي غائب يتخطّى افتراضاً |
| 29‑ب | `set -a; . ./.env.test; set +a` | **شرط تشغيل `tests/integration`** — تحميل اعتمادات `TEST_*` قبل `pytest`؛ بدونه تتخطّى الحزمة كلّها بسببٍ مُعلَن |
| 30 | `pytest -rs` | المِثل، مع طباعة **اسم وسبب** كلّ تخطٍّ — الفرق بين تخطٍّ سليمٍ ومقنّع |
| 31 | `pytest -m live_db` | مسبارٌ حيٌّ واحد فوق PostgreSQL الحاوي (وكذا `live_qdrant`, `live_minio` …) |
| 32 | `alembic revision -m "..."` | توليد هجرةٍ جديدة (بلا مفاتيح أجنبيّة عابرةٍ بين المخطّطات) |
| 33 | `python -m app.ops.retention` | مكنسة الاحتفاظ — تشغيلٌ **يدويّ**، لا خدمةٌ دائمة |
| 34 | `python -m app.ops.rotate_transit` | تدوير مفتاح Vault Transit — تشغيلٌ **يدويّ** بدور مقصورٍ على `ciphertext_ref` |
| 34‑ب | `python -m app.ops.notify_groups list` | مجموعات `cg.notify` كلّها مع LIVE/ORPHAN وسببه — **قراءةٌ محضة**. و`sweep --yes` يكنس اليتيمة ([§3.135](log/3.135.md)) |
| 34‑ج | `python -m app.ops.slow_queries top` | أغلى الاستعلامات بالزمن التراكميّ من `pg_stat_statements` (خطّة السعة `0.4`) — **قراءةٌ محضة**؛ و`reset --yes` يصفّر العنقودَ كلَّه |
| 34‑د | `python -m app.ops.load_seed run` | بذرةُ الحمل الواقعيّة (خطّة السعة `0.1` الشرط ٣): مليونُ رسالةٍ و100 ألف ملفٍّ ومليونُ متّجهٍ على 200 مساحةِ عمل، **عبر RLS**. و`plan` جفافٌ، و`status --export` يُخرج `LOAD_SEED_*`، و`purge --yes` يسترجع |
| 34‑هـ | `deploy/load/run.sh peak` · `deploy/load/smoke.sh` | مولّدُ الحمل (خطّة السعة `0.1`): k6 من المضيف إن وُجد وإلّا من `--profile load` (`grafana/k6:1.3.0`)، و`LOAD_K6=host\|docker` يُلزم أحدَهما. و`smoke.sh` يُثبت الهيكلَ في نصف دقيقةٍ ببِركةٍ صناعيّة — 401 وعتبةٌ مُخفَقةٌ **بالتصميم** |
| 35 | `docker compose -f docker-compose.yml -f docker-compose.test.yml up -d` | **الصيغة المعتمَدة للحزمة الاختباريّة** — الوحيدة التي تنشر منفذ التضمين وتُحضر سكربت قاعدة الاختبار |
| 36 | `docker compose … exec -T postgres sh /opt/aizzak/testdb/20-test-database.sh` | تزويد `aizzak_test` — **يدويٌّ، مرّةً واحدةً لكلّ حجم**، ولا يُشغَّل تلقائيّاً أبداً. مُعاد التشغيل بأمان |
| 37 | `docker compose -f docker-compose.yml -f docker-compose.wsl-gpu.yml up -d app` | **قراءة الـGPU في صفحة System Monitor** — تمرير بطاقة WSL إلى حاوية `app` وحدها. بلا هذه الـ`-f` الثانية تبقى القراءة `nvidia-smi is not available on this host` |

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

### 14–16 · العمّال الثلاثة

**لا تُمرّر `WORKER` بعد الآن** ([§3.133](log/3.133.md)). صارت لكلّ مستهلكٍ خدمتُه، تحمل قيمتها **حرفيّاً**، و**الثلاثة في الإقلاع الافتراضيّ** منذ [§3.134](log/3.134.md):

```bash
docker compose up -d                                  # الثلاثة ضمن الجميع — لا بروفايل في الملفّ
```

| الخدمة | الحالة |
|---|---|
| `worker-memory` | ✅ **افتراضيّة** — مقيسة حيّاً: 138 ثانيةً على مجرىً هادئ بـ`RestartCount = 0` ([§3.105](log/3.105.md) §5‑ب) |
| `worker-knowledge` | ✅ **افتراضيّة** — مقيسة حيّاً: ≥ 5 دقائق بـ`RestartCount = 0`، وهو **أوّل إقلاعٍ حاويٍّ لها في تاريخ المشروع** ([§3.105](log/3.105.md) §5‑ج) |
| `worker-media` | ✅ **افتراضيّة منذ [§3.134](log/3.134.md)** — أوّل إقلاعٍ حاويٍّ لها: **45 د 46 ث**، `RestartCount = 0`، صفر سطر خطأ، ومستهلكٌ **مسجَّل** على `cg.media` بـ`pending 0` |

> **ما فصل الخدمات لم يكن القدرة بل الدليل.** لم يكن أيٌّ منها ينهار لنقصِ محوّل حتّى وهي خلف البروفايل؛ الفارق أنّها لم تكن قد شُغّلت. دخلت كلٌّ منها بعد إقلاعٍ واحدٍ مقيس، **وبهذا لم يبقَ في `docker-compose.yml` مفتاح `profiles:` واحد**.
>
> ⭐ **والدليل المقبول ليس `docker ps`.** حاويةٌ `Up` تعني عمليّةً حيّة؛ وعمليّةٌ عالقةٌ في جمودٍ تبقى `Up` إلى الأبد. المقياس الصحيح هو الأمر 15: مستهلكٌ **مسجَّل** في المجموعة، و`idle` يُقاس بالثواني لا بالساعات (دورة `XREADGROUP` حيّة)، و`pending 0`. 📌 وإن رأيت مستهلكاً خاملاً **بالساعات** بـ`pending 0` فهو **شبحُ حاويةٍ ماتت** — Redis لا يحذف المستهلك عند موت العمليّة — يُنظَّف بـ`XGROUP DELCONSUMER`؛ أمّا `pending > 0` فرسائل محتجزة تُستردّ بـ`XAUTOCLAIM` قبل أيّ حذف.
>
> ✅ **ومنذ [§3.137](log/3.137.md) لم يعد هذا فعلاً يدويّاً**: العامل الحيّ يكنس إخوته الموتى كلّ `CONSUMER_SWEEP_INTERVAL_S` بالتسلسل الآمن نفسه (استردادٌ ⇒ إعادةُ تحقّق ⇒ حذفٌ أو رفض)، والخروج النظيف يحذف قيدَ العمليّة قبل موتها. يبقى الأمر 15 هو **مقياس الحياة** كما هو — وشبَحٌ باقٍ بعد نشرٍ حديثٍ صار بذاته إشارةَ خللٍ تستحقّ نظرةً في سطور `consumers.sweep_refused`.
>
> ⭐ **قيمة `WORKER` في `.env` لم تعد تعني شيئاً لـCompose** — كانت `${WORKER:-...}` تجعل `.env` يغلب `docker-compose.yml` بصمت، ولا يُكشَف إلّا بـ`docker compose config`. تبقى ذات معنىً لصورة **RunPod** (حاويةٌ واحدة ⇒ عاملٌ واحد) ولـ`python -m app.workers.main` المجرّد.
>
> فإن انهار عاملٌ اليوم فهو **عطلٌ حقيقيٌّ يستحقّ القراءة**: بيئةٌ لا نقصُ كود (‏Vault · سرّ MinIO مشوَّه · `PROVIDER_ROUTING` يسمّي مزوّداً بلا محوّل) — والرسالة تسمّي السبب. و`media` وحده يضيف حاجة اعتمادٍ مخزَّنٍ باسم `image:openai`، وغيابُه يُفشل **المهمّة** لا الإقلاع — ولهذا لم يمنع غيابُه قياسَ الإقلاع في §3.134.

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

### 24‑ب · قاعدة الأحجام — أيُّ أمرٍ يمحو ماذا

الأحجام المُسمّاة تحمل **كلّ** حالةٍ دائمةٍ على هذا المضيف: قاعدة البيانات، وتخزين Vault ومفتاح فكّ ختمه، وQdrant، وMinIO — إضافةً إلى بيانات مشروع `alpha` القديم. وسلوكُ `prune` ليس ما يظنّه أكثرُ الناس، فقُيس في 2026‑08‑13 على **Docker 29.6.1** بحجمٍ مُسمّىً تجريبيٍّ معزولٍ بلافتة:

| الأمر | الأحجام المجهولة | الأحجام **المُسمّاة** |
|---|---|---|
| `docker volume prune` | تُحذف | **تنجو** ✅ |
| `docker system prune --volumes` | تُحذف | **تنجو** ✅ |
| `docker volume prune -a` | تُحذف | **تُحذف** ❌ |
| `docker compose down -v` | — | **تُحذف** (أحجام المشروع) ❌ |

منذ Docker 23 صار `volume prune` بلا `-a` يمسّ **المجهولة وحدها**. فالسلاح الحقيقيّ ليس `prune` المجرّد بل **`-a`** و**`down -v`**.

⚠️ **ومزلقةٌ مقيسةٌ بالتجربة:** `docker system prune --filter label=…` **يتجاهل اللافتة عند ذاكرة البناء** — تجربةٌ قُصد بها حجمٌ واحدٌ مُلافَتٌ مسحت 34.82 GB من الـbuild cache. اللافتة تُقيّد الحاويات والصور والأحجام، **لا** ذاكرة البناء.

**القاعدة:**

1. لا `prune` للأحجام على هذا المضيف. للتنظيف استعمل `docker builder prune` وحده — لا يمسّ الأحجام إطلاقاً.
2. أيُّ حذفٍ **يُسمّي الحجم صراحةً**: `docker volume rm <name>`.
3. `docker compose down -v` ممنوعٌ إلّا بعد نسخٍ احتياطيٍّ مثبَت — يمحو `postgres-data` و`vault-data` و`vault-init` معاً، وفقدُ الأخيرين = **فقدُ اعتماد المستأجرين نهائيّاً** (§23).

وهذه القاعدة **مفروضةٌ آليّاً** لا موصىً بها: خطّاف `PreToolUse` في [`.claude/settings.json`](../.claude/settings.json) يرفض الأوّل والثالث ويطلب تأكيداً على الثاني.

📦 وبيانات `alpha` الثلاثة مؤرشفةٌ خارج Docker في `/home/volume-archive/` (‏8.0 MB مضغوطة، 2026‑08‑13)، فحذفها لم يعد نهائيّاً. والاستعادة:

```bash
docker run --rm -v alpha-infra_pg-data:/dest -v /home/volume-archive:/src alpine:3.20 tar -xzf /src/alpha-infra_pg-data.20260813.tar.gz -C /dest
```

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

#### 29‑ب · `set -a; . ./.env.test; set +a` — شرط تشغيل اختبارات التكامل

```bash
set -a; . ./.env.test; set +a
pytest tests/integration
```

**حمّل البيئة قبل `pytest` لا بعده.** ملفّ `.env.test` يحمل كلّ متغيّرات `TEST_*`: العناوين والاعتمادات الفعليّة — سبعة `TEST_DATABASE_URL*` بأدوارها السبعة، و`TEST_REDIS_URL`، و`TEST_MINIO_*`، و`TEST_QDRANT_URL`، و`TEST_VAULT_ADDR`/`TEST_VAULT_TOKEN`، و`TEST_EMBEDDING_URL`، و`TEST_OLLAMA_BASE_URL`. و`set -a` يجعل كلّ إسنادٍ تالٍ مُصدَّراً، والنقطة تنفّذ الملفّ في الصَّدَفة الحاليّة (لا في صَدَفةٍ ابنة تموت بمتغيّراتها)، و`set +a` يُعيد السلوك إلى أصله بعده مباشرةً.

> ⚠️ **لهذا الملفّ وحده.** لا تُصدِّر `.env` بالطريقة نفسها — الصَّدَفة تجرّد اقتباس JSON في `PROVIDER_ROUTING` فيفشل التحميل بـ`SettingsError` (‏الأمر 34‑ب).

بدون هذا السطر تبقى الافتراضيّات المكتوبة في [`tests/integration/conftest.py`](../tests/integration/conftest.py): تقصد المكدّس الصحيح على منافذه المزاحة، لكنّها تحمل كلمات سرٍّ نائبة تُرفَض. والنتيجة **تخطٍّ مُعلَنٌ بسببه** لا انفجار:

```
SKIPPED [1] tests/integration/test_workspace_repository_rls.py:142: live PostgreSQL (role owner)
at 127.0.0.1:15432 refused the handshake -- InvalidPasswordError: password authentication failed
for user "aizzak_owner"
```

📌 **والتخطّي ليس نجاحاً.** والفرق مقيسٌ لا مُقدَّر، على المكدّس نفسه وفي الدقيقة نفسها: مع تحميل البيئة **382 نجحت · 10 تخطٍّ** (‏3:10 دقيقة)، وبدونه **392 تخطٍّ · صفر تشغيل** (‏2.86 ثانية). سطرٌ واحد يفصل بين حزمة تكاملٍ تُثبت شيئاً وحزمةٍ لا تقول شيئاً. و`REQUIRE_LIVE=1` يقلب كلّ تخطٍّ من هذا النوع إلى فشلٍ صريح — استعمله حين تكون قد رفعت المكدّس فعلاً.

```bash
pytest -rs
```

يطبع **اسم وسبب كلّ تخطٍّ** — وهو ما فعله CI، والفرق الوحيد بين «تخطٍّ سليم» و«تخطٍّ يخفي عطلاً».

```bash
pytest -m live_db
```

مسبارٌ واحد فوق PostgreSQL الحاوي (وكذا `live_qdrant` · `live_minio` · `live_vault` …)، بينما `live_ollama` يقصد خدمة WSL الأصلية. قاعدة الاختبار `aizzak_test` بدورَي `aizzak_owner` و`app_rw`، و`.env.test` يحمل الاعتمادات الفعلية وكلّ عناوين `TEST_*`.

⚠️ **غياب `.env.test` كان ينفجر ولا يتخطّى.** المِسبار كان يفحص المنفذ وحده، ومنفذٌ مفتوحٌ بكلمة سرٍّ خاطئة ليس «غير قابلٍ للوصول»، فيمضي التجهيز ثمّ ينهار: **392 خطأً** في `pytest tests/integration` بينما `pyproject.toml` يَعِد بالتخطّي. الآن يُجري كلّ مِسبار `live_*` **مصافحةً حقيقيّة** بعد فحص المنفذ — اتّصالٌ ثمّ استعلامٌ تافه بالمصنع نفسه الذي سيستعمله الاختبار: `SELECT 1` لكلّ دورٍ من أدوار Postgres السبعة · `PING` لـRedis · `bucket_exists` **موقَّعٌ** لـMinIO · سرد المجموعات لـQdrant · `GET /health` لخدمة التضمين · حالة الختم ثمّ قبول التوكن لـVault · سرد النماذج لـOllama. فأيّ فشلٍ في الاعتماد يصير تخطّياً يحمل رسالة السائق نفسها، ويُدفع ثمنه **مرّةً واحدةً لكلّ جلسة** لا مرّةً لكلّ اختبار.

MinIO مثالٌ مقصود: `_MINIO_SECRET_DEFAULT` هي `aizzak-test-secret`، وبعد تدوير الحساب كانت تنتج `SignatureDoesNotMatch` في كلّ اختبار؛ اليوم تردّ المصافحة `AccessDenied` مرّةً واحدةً ويُعلَن السبب. وحارس D‑1 يظلّ يرفض اختلاف `MINIO_TEST_SECRET_KEY` و`TEST_MINIO_SECRET_KEY` عندما يكونان مضبوطين معاً، لكنه لا يربط الاسمين بنيوياً.

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

```bash
python -m app.ops.notify_groups list
python -m app.ops.notify_groups sweep --yes
```

يكنس مجموعات `cg.notify.<مضيف>.<pid>` التي لا يملك أحدٌ في المنظومة كنسها ([§3.135](log/3.135.md)): كانس الإقلاع محصورٌ في مضيفه **عمداً**، فمجموعات حاويةٍ أُعيد إنشاؤها تبقى إلى الأبد — 4 لكلّ إعادة إنشاء، و**20** منها قِيست هنا.

> ⚠️ `sweep` لا رجعة فيه ويرفض بلا `--yes`؛ شغّل `list` أوّلاً. ثلاث بوّابات أمان (`consumers = 0` · `pending = 0` · ليست PID حيّاً على هذا المضيف) **زائد قراءتين يفصلهما 5 ث** — لأنّ جسراً بين `ensure_group` وأوّل `XREADGROUP` يُظهر صفر مستهلكين للحظة، وتدميرُ مجموعته حينها يُفشل قراءته التالية بـ`NOGROUP`. ولا تمسّ الأداة `cg.knowledge`/`cg.media`/`cg.memory` إطلاقاً.

>
> 📌 **من مضيف WSL** مرّر `REDIS_URL=redis://127.0.0.1:16379/0` أمام الأمر — ولا تُصدِّر `.env` بـ`set -a` قبله: الصدفة تجرّد اقتباس JSON في `PROVIDER_ROUTING` فيفشل التحميل بـ`SettingsError`.
>
> ✅ **والقاعدة نفسها تعمل الآن بلا مشغّل** ([§3.137](log/3.137.md)): مهمّةٌ خلفيّةٌ في API كلّ `NOTIFY_GROUP_SWEEP_INTERVAL_S` (‏افتراضيّ 900 ث · `0` يُعطّل)، **بالنصّ نفسه لا بنسخةٍ منه** — انتقلت القاعدة إلى `consumers/sweeper.py` وبقي هذا الأمر غلافاً فوقها. تُستعمل الأداة حين لا يُنتظَر المؤقّت، أو من خارج الحاوية. **العشرون المقيسة أعلاه كُنِست فعلاً** (قياسٌ لاحق: صفر يتيمة).

```bash
python -m app.ops.load_seed plan   --seed-id dev-2026-09-03
python -m app.ops.load_seed run    --seed-id dev-2026-09-03
python -m app.ops.load_seed status --seed-id dev-2026-09-03 --export
python -m app.ops.load_seed purge  --seed-id dev-2026-09-03 --yes
```

بذرةُ الحمل التي تطلبها خطّةُ السعة `0.1` شرطاً ثالثاً: **مليونُ رسالةٍ · 100 ألف ملفّ · مليونُ متّجه · 200 مساحةَ عمل**، مكتوبةً **عبر** سياسات الصفوف لا حولها — والأداةُ ترفض العملَ بدورٍ `SUPERUSER` أو `BYPASSRLS`. حتميّةٌ ومُستأنَفة: إعادةُ التشغيل بعد انقطاعٍ تُكمل ولا تُضاعف.

> ⚠️ `DATABASE_URL` لهذه العمليّة دورُ `app_rw` **مباشرةً إلى `postgres:5432`** لا عبر المجمّع (‏`MAX_CLIENT_CONN` هو `ح‑3` نفسُه، وأداةُ قياسٍ تحتلّ مقعداً ممّا تقيسه تُفسد القياس). ومن المضيف: `PROVIDER_ROUTING` و`USAGE_DEFAULT_LIMITS` في `.env` **بلا اقتباس**، فـ`. ./.env` يُتلفهما — أعِد تصديرَهما خامَّين. التفصيل في [`08-local-runbook §4.10`](design/08-local-runbook.md).

### 34‑هـ · مولّدُ الحمل — `deploy/load/run.sh` و`--profile load`

```bash
deploy/load/smoke.sh                      # ~30 ث: يُثبت الهيكلَ لا المنصّة
deploy/load/run.sh peak                   # k6 من المضيف إن وُجد، وإلّا الحاوية
LOAD_K6=docker deploy/load/run.sh peak    # الصورةُ المثبَّتة قسراً
docker compose --profile load run --rm k6 version
```

‏k6 ثنائيّةُ Go لا مكتبةٌ تُضَمّ، وغيابُها كان الحاجبَ `د‑3`. صارت خدمةَ Compose مثبَّتةَ الوسم ترى `deploy/load/` وحدَها، و`LOAD_BASE_URL` فيها `https://nginx` — و`localhost` هناك حلقةُ المولّد نفسِه فيُرفض صراحةً.

> ⚠️ **تشغيلٌ من مضيفٍ واحدٍ لا يتجاوز 22 طلباً/ث مهما قال الملفّ.** الحافّةُ تحدُّ على `$binary_remote_addr` (`limit_req rate=20r/s burst=40`، و`limit_conn ws_conn 100` للمقابس). مُقاس: عُرض **300.1 طلب/ث** فقُبل **22.0** ورُدّ 429 على **92.7٪**. اقرأ `counters.aizzak_rate_limited_total` في الملفّ المؤرشَف **قبل** أيّ مئينٍ فيه. التفصيل في [`08-local-runbook §4.11`](design/08-local-runbook.md) و`docs/capacity-status.md` (`د‑8`).

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

### 37 · قراءة الـGPU — `docker-compose.wsl-gpu.yml`

```bash
docker compose -f docker-compose.yml -f docker-compose.wsl-gpu.yml up -d app
```

**العطل الذي يعالجه:** صفحة System Monitor كانت تعرض `nvidia-smi is not available on this host`. الواجهة سليمة والخادم سليم — [`system_stats.py`](../src/app/infrastructure/monitoring/system_stats.py) يبحث عن `nvidia-smi` بـ`shutil.which` ويُبلِّغ غيابه بدل أن يدّعي «لا بطاقات». البطاقة كانت مرئيّةً في WSL (`NVIDIA GeForce RTX 4060 Laptop GPU`) وغير ممرَّرةٍ إلى الحاوية: `DeviceRequests = null`.

**ولماذا ملفُّ تجاوزٍ لا سطرٌ في `docker-compose.yml`؟** كلُّ ما فيه — `/dev/dxg` و`/usr/lib/wsl` — موجودٌ على مضيف WSL2 **وحده**. كتابته في الملفّ الأصل تجعل `up -d` يفشل فشلاً قاطعاً على مضيف اللينكس الذي يستهدفه [`deploy-linux-server.md`](deploy-linux-server.md) وعلى كلّ عدّاءٍ في CI. وهو النمط نفسه المعتمَد في **35** أعلاه: ملفٌّ لا يُدمَج إلّا بطلبٍ صريح، فلا وجود له عند المضيف الذي يكسره.

**ولماذا لا `gpus: all`؟** لأنّها تمرُّ عبر NVIDIA Container Toolkit، وهي **غير مثبَّتة هنا** (`nvidia-ctk` غائب · `docker info` لا يذكر إلّا `runc` · لا وجود لـ`/etc/docker/daemon.json`)، وإضافةُ المفتاح بدونها تُفشل الحاوية بـ«could not select device driver». تثبيتها يحتاج `root` وإعادة تشغيل عفريت Docker؛ هذا الملفّ لا يحتاج أيّاً منهما ولا إعادة بناءٍ للصورة. والمقايضة صحيحةٌ لما نريده: الصفحة **تقرأ** البطاقة ولا تحسب عليها، والتركيبان أدناه يكفيان NVML — فيكفيان `nvidia-smi` — بلا منح الحاوية سياق CUDA لا شيفرة لديها تستعمله. فإن احتاجت خدمةٌ يوماً أن **تحسب** على البطاقة، فثبِّت العُدّة وامنح تلك الخدمة `gpus: all`؛ ولا يُوسَّع هذا الملفّ ليكون بديلاً عنها.

⚠️ **`/usr/lib/wsl` كاملاً لا `lib/` وحدها.** جُرِّب الاقتصار على `lib/` فأجاب `nvidia-smi` بـ«couldn't communicate with the NVIDIA driver / Driver Not Loaded»: ‏`libdxcore.so` يمدّ يده جانباً إلى `/usr/lib/wsl/drivers` نحو مخزن تعريف ويندوز.

📌 **و`nvidia-smi` يصل إلى `PATH` بتركيبِ ملفٍّ واحدٍ فوق `/usr/local/bin`**، لا بإعادة تعريف `PATH:` في ملفّ التجاوز: إعادة تعريفها كانت ستُعيد كتابة قيمة الصورة كاملةً بما فيها `/opt/venv/bin` الذي يضيفه [`Dockerfile`](../Dockerfile) — ويوم يتغيّر ذلك السطر يسقط الـvenv صامتاً ويصير `gunicorn` غير موجود: رفاهيةُ مراقبةٍ تُسقط الـAPI.

**القياس بعد التطبيق** (‏من داخل `aizzak-app-1`، بمسار الشيفرة نفسه لا بأمرٍ يدويّ):

```
gpu_error : None
gpu       : index=0 · NVIDIA GeForce RTX 4060 Laptop GPU · util 0% · mem 1.16/8.0 GB (14.5%) · 50°C · 4.64 W
```

⚠️ **الملفّ يُسمّى في كلّ `up` لاحق.** `docker compose up -d` مجرَّداً يعيد إنشاء `app` بلا التجاوز فتعود القراءة غائبة. لتثبيته لهذا المضيف وحده: `COMPOSE_FILE=docker-compose.yml:docker-compose.wsl-gpu.yml` في `.env` (‏وهو مُتَجاهَلٌ في git — القاعدة الذهبيّة 7).

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
