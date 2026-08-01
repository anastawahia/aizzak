<div dir="rtl">

# دليل التشغيل العملي — منصّة AIZZAK

> **ما هذه الوثيقة؟** دليلٌ عمليٌّ مختصر: «افعل هذا فيعمل». يجمع في مكانٍ واحد ما تحتاجه لإقلاع المنصّة والتحقّق منها وتشخيص أعطالها.
>
> **ما ليست هي؟** ليست مصدر الحقيقة. المرجع **المُلزِم** — بالقرارات وأسبابها والمقايضات — هو [`docs/design/08-local-runbook.md`](design/08-local-runbook.md). **عند أيّ تعارضٍ فـ08 هي الصحيحة**، وهذه الوثيقة هي التي تُصحَّح.
>
> مصادر هذه الوثيقة: `08-local-runbook.md` · `docker-compose.yml` · `.env.example` · `tests/integration/conftest.py` · [`implementation-status.md`](implementation-status.md).
>
> **آخر مطابقةٍ مع الواقع: 2026‑07‑24.**

---

## 0) حالة المنصّة اليوم — اقرأ هذا أوّلاً

قبل أن تُقلع، اعرف ما الذي يُفترض أن يعمل وما الذي **يُفترض ألّا يعمل**، وإلّا شخّصت سلوكاً مقصوداً على أنّه عطل.

| المكوّن | الحالة |
|---|---|
| المراحل 0–7 (API · الحافّة · RLS · Vault · الهجرات · مُرحّل Outbox) | ✅ حيّةٌ ومُثبَتة |
| خدمة التضمين المركزيّة (`services/embedding`، البند 2.10) | ✅ مبنيّة. `POST /search` يجيب 200 بدل 503 |
| عامل **`memory`** | ✅ يقلع ويعمل |
| عامل **`knowledge`** | ⛔ ينهار عند الإقلاع — ينقصه `DocumentContentResolver` (دَينٌ مسجَّل) |
| عامل **`media`** | ⛔ ينهار عند الإقلاع — ينقصه `MediaGenerator` (دَينٌ مسجَّل) |
| مزوّدو LLM السحابيّون (Gemini · Claude · OpenRouter — البند 2.8‑ب‑2) | ⛔ محجوبون بالمفاتيح. المزوّد المحلّي **Ollama** هو المسار العامل |

> ⚠️ ولهذا خدمة `worker` **خلف `profile`** في Compose: لو أُقلعت تلقائيّاً لدارت `knowledge`/`media` في حلقة انهيارٍ أبديّة وأظهرت مكدّساً سليماً بمظهر المعطوب. الطوبولوجيا مكتوبةٌ وصحيحة؛ ما يعطّلها **محوّلان ناقصان** لا خللٌ في النشر.

> ✅ **مستجدّ (2026‑07‑24): Docker متاحٌ في هذه البيئة** — قِيس: `docker 29.6.1` و`docker compose v5.3.1` داخل `Ubuntu-24.04`. الوثائق الأقدم (‏`docs/log/3.77.md §5`) تقول «لا Docker هنا» وتؤجّل الإثبات الحاويّ للبند 2.10؛ **ذلك الحجب زال**، فمسار §2 أدناه قابلٌ للتنفيذ فعلاً، ومعه الإثبات المؤجَّل (‏`/search` عبر حاوية تضمينٍ مُقلَعة + قياس SLO).

---

## 1) المتطلّبات المسبقة

- Docker + Docker Compose v2 (متوفّران — راجع أعلاه)
- Python 3.12+ (‏3.12.3 مثبَّتٌ في WSL) — لتشغيل الأدوات والاختبارات خارج الحاويات
- **لا شيء آخر.** `openssl` و`vault` و`mc` كلّها تعمل داخل صورٍ مثبّتة الأوسمة عبر خدماتٍ لمرّةٍ واحدة، فلا يُشترط وجودها على المضيف.

> ⚠️ **نفّذ كلّ الأوامر داخل WSL** (`Ubuntu-24.04`)، لا من Git‑Bash على ويندوز: قناة Git‑Bash لا تنفّذ ثنائيّات ELF (‏`Exec format error`). المشروع في `/home/AIZZAK` وبيئته الافتراضيّة في `/home/AIZZAK/.venv`.

---

## 2) المسار (أ): المكدّس الكامل بـDocker Compose

### 2.1 الإعداد لمرّةٍ واحدة

```bash
cd /home/AIZZAK
cp .env.example .env
```

ثمّ **املأ في `.env`** القيم التي تفشل الحاويات بدونها:

| المفتاح | لماذا يجب تغييره |
|---|---|
| `POSTGRES_SUPERUSER_PASSWORD` | Compose يرفض الإقلاع بدونها (‏`:?`) |
| `AIZZAK_OWNER_PASSWORD` | دور المُهاجِر ومالك الجداول |
| `APP_RW_PASSWORD` | دور التطبيق والعمّال (محكومٌ بـRLS) |
| `OUTBOX_RELAY_PASSWORD` | دور المُرحّل (SELECT/UPDATE على `platform.outbox` فقط) |
| `RETENTION_SWEEPER_PASSWORD` | دور مكنسة الاحتفاظ (SELECT/DELETE على الجداول الثلاثة غير المحدودة فقط — `python -m app.ops.retention`، يدويّاً لا خدمةً دائمة) |
| `METRICS_READER_PASSWORD` | دور قراءة `/metrics` (‏SELECT وحيدة على `platform.outbox` فقط — P1‑3، خدمةٌ دائمة داخل `app`) |
| `TRANSIT_ROTATOR_PASSWORD` | دور تدوير مفتاح Transit (‏`UPDATE` مقصورٌ على `ciphertext_ref` وحده — P1‑9، `python -m app.ops.rotate_transit`، يدويّاً لا خدمةً دائمة) |
| `MINIO_ROOT_USER` · `MINIO_ROOT_PASSWORD` | تخزين الكائنات |
| **`FIREBASE_PROJECT_ID`** | ⚠️ **فارغٌ في `.env.example` وهو إلزاميّ**: `FirebaseAuth` يفشل فشلاً سريعاً عند الإنشاء على قيمةٍ فارغة (`_guard_project_id`) ⇒ التطبيق **لا يقلع** أصلاً |

> هذه الكلمات هي **استثناءٌ بالضرورة** لا بالسهو: يجب أن يقلع Postgres وMinIO بكلمةِ سرٍّ قبل أن يستطيع أحدٌ قراءة كلمة سرٍّ من Vault. و`deploy/vault/bootstrap.sh` يبذر القيم نفسها في Vault، وهي **النسخة الوحيدة** التي يقرؤها `src/`. الملفّ `.env` مُتَجاهَلٌ في git — أبقِه كذلك.

> ⚠️ **حارس `:?` يكشف الفراغ لا `change-me-*`.** كلّ **كلمات المرور** أعلاه محميّةٌ في `docker-compose.yml` بـ`${VAR:?...}` (‏لا `FIREBASE_PROJECT_ID` — تلك `:-` وتفشل عند إنشاء `FirebaseAuth` كما يقول الصفّ الأخير)، وهذا يرفض **غير المضبوط والفارغ** فقط — أمّا `change-me-rotator` فقيمةٌ مضبوطةٌ غيرُ فارغةٍ تمرّ صامتة. وهذا ما حدث فعلاً: بقي `TRANSIT_ROTATOR_PASSWORD` على قيمةٍ من طراز `change-me-*` بينما إخوته الخمسة أسرارٌ حقيقيّة ([§3.97 (هـ)](log/3.97.md)). ولا حارسَ آليّ يمكنه كشفه — `.env` غير متعقَّبةٍ في git فلا اختبارَ يراها ولا CI. الفحص الوحيد هو الطول، **قبل أوّل `docker compose up`** لأنّ `10-roles.sh` يعمل مرّةً واحدةً عند تهيئة الحجم الأوّل ⇒ سرٌّ ضعيفٌ هنا يُولد معه الدور ويبقى:

```bash
for v in POSTGRES_SUPERUSER_PASSWORD AIZZAK_OWNER_PASSWORD APP_RW_PASSWORD \
         OUTBOX_RELAY_PASSWORD RETENTION_SWEEPER_PASSWORD METRICS_READER_PASSWORD \
         TRANSIT_ROTATOR_PASSWORD MINIO_ROOT_PASSWORD; do
  l=$(grep "^${v}=" .env); l=${l#*=}; printf "%-28s len=%s\n" "$v" "${#l}"   # 32، ولا يبدأ بـchange-me
done
grep "^MINIO_ROOT_USER=" .env   # معرّفٌ لا سرّ: لا قاعدةَ طولٍ عليه، لكن لا يبقى change-me-* أيضاً
```

### 2.2 الإقلاع

```bash
docker compose up -d
```

هذا وحده يكفي: الترتيب كلّه مُرمَّزٌ في `depends_on` — تقلع البيانات، ثمّ الخدمات الأربع لمرّةٍ واحدة (تهيئة Vault، دلو MinIO، شهادة TLS، الهجرات والمنح)، ثمّ التطبيق والمُرحّل والحافّة.

> ⏱️ **البناء الأوّل بطيء**: صورة `embedding` **تخبز أوزان النموذج وقت البناء** (‏MiniLM متعدّد اللغات، ~250–470م.ب) لتعمل بعدها بلا أيّ نداءٍ خارجيّ (`HF_HUB_OFFLINE=1`). الأبطاء مرّةٌ واحدة، ومقابله أثرٌ ثابتٌ لا يتغيّر بين إقلاعين.

### 2.3 ⚠️ المنافذ: المكدّس يعمل **بجانب** الخدمات الأصليّة لا بدلاً منها

على هذا المضيف تعمل خدماتٌ أصليّة (postgresql@16 · pgbouncer · redis · minio · qdrant · vault · ollama) تملك المنافذ القانونيّة، والحزام الحيّ للاختبارات مُهيَّأٌ عليها ويجب أن يبقى يعمل بلا مساس. لذلك: **داخل شبكة Compose** يحتفظ كلّ شيءٍ باسمه ومنفذه القانونيّ (`pgbouncer:6432`, `minio:9000` …)، و**النشر على المضيف وحده** هو المُزاح:

| الخدمة | داخل الشبكة | على المضيف |
|---|---|---|
| nginx | 80 / 443 | **80 / 443** |
| postgres | 5432 | 15432 |
| pgbouncer | 6432 | 16432 |
| redis | 6379 | 16379 |
| minio (API / كونسول) | 9000 / 9001 | 19000 / 19001 |
| qdrant | 6333 | 16333 |
| vault | 8200 | 18200 |
| **embedding** | 8080 | **غير منشور — `expose` لا `ports`** |
| app | 8000 | غير منشور (خلف nginx) |

> خدمة التضمين **داخليّةٌ عمداً**: نصّ المستأجِر يعبر شبكة Docker الداخليّة إليها، لا الشبكة العامّة — الحدّ الائتمانيّ نفسه الذي لـPostgres وRedis وQdrant.
>
> ⚠️ `MINIO_PUBLIC_ENDPOINT` ليس تجميلاً: الروابط المُوقَّعة مسبقاً تُوقَّع بـSigV4 **على المضيف**، فرابطٌ وُقِّع على `minio:9000` لا يصلح لمتصفّح ولا يمكن تصحيحه لاحقاً — لا بـnginx ولا بجراحة نصوص.

---

## 3) فحوص الصحّة

```bash
curl -s  http://localhost/health          # liveness
curl -sk https://localhost/health/ready   # readiness عبر TLS (الشهادة موقَّعةٌ ذاتيّاً ⇒ -k)
docker compose ps                          # حالة كلّ خدمة
```

> ⚠️ **فحص الحافّة هو الفحص المهمّ.** كلّ فحصٍ آخر يسبر خدمةً **من داخل نفسها**، ولهذا كانت العلّة #10 خفيّة: nginx يوجّه إلى عنوانٍ ميّت ويجيب **502** للعالم، بينما `docker compose ps` يقول إنّ كلّ شيءٍ `healthy` وسجلّ التطبيق مليءٌ بـ`200`. فحص خدمة `nginx` يعبر ذلك الرابط على المخطّطين معاً (‏`http` و`https`) — فراجع **حالة `nginx` نفسها** قبل أيّ شيءٍ آخر.
>
> `/health/ready` **لا يلمس أيّ تبعيّة** عمداً (الجاهزيّة = «انتهى الإقلاع»، لا «التبعيّات حيّة»). فحاويةٌ صحيحة ليست بنفسها دليلاً على مسار البيانات — ولهذا توجد إثباتات `deploy/smoke/`.

### 3.1 الإثباتات الحيّة

```bash
python3 deploy/smoke/ws_smoke.py localhost 80          # ws://  — 101 ثمّ إغلاق 1008
python3 deploy/smoke/ws_smoke.py localhost 443 --tls   # wss:// — نفس الفحوص فوق TLS
docker compose exec app python /app/deploy/smoke/stack_smoke.py                 # OPS-02 · RLS · توقيع مسبق
docker compose exec -e VAULT_SECRET_ID=<id> app python /app/deploy/smoke/approle_smoke.py
```

`approle_smoke.py` يثبت **الاتّجاهين**: ما تسمح به السياسة يعمل، وما تحجبه **مرفوضٌ فعلاً**. قراءة السياسة تثبت أنّ النصّ خُزِّن، لا أنّ التوكن محكوم.

---

## 4) العمّال والمُرحّل

```bash
WORKER=memory docker compose --profile workers up -d worker   # ✅ الوحيد الذي يقلع اليوم
docker compose up -d outbox-relay
```

> `WORKER` أعلاه صريحٌ عمداً رغم أنّ `docker-compose.yml`/`.env.example` يفترضان `memory` الآن أيضاً (‏release-blockers-plan.md §3 خطوة 3) — التصريح أوضح للقارئ من الاتّكال على افتراضيٍّ قد يتغيّر.

> ⚠️ **ترتيب الإقلاع مُلزِمٌ على مجرًى جديد: العمّال قبل المُرحّل.** مجموعات المستهلكين تُنشأ عند `$` (ذيل المجرى)، فمجموعةٌ تُنشأ **بعد** أن نشر المُرحّل مدخلاتٍ على مجرًى جديد **لن ترى تلك المدخلات أبداً**. على المجاري القائمة الترتيب غير مؤثّر.

> ⚠️ **`XACK` لا يحذف.** مجاري Redis سجلٌّ إلحاقيّ: `XACK` يمسح قائمة المعلَّق ولا يحذف المدخلة، فـ`stream.<وحدة>` ينمو بلا حدٍّ **حتّى مع عمّالٍ أصحّاء يُقرّون كلّ شيء**، وRedis هنا على `maxmemory 0`/`noeviction` ⇒ لا سقف في أيّ طبقة. العلاج المنفَّذ `STREAM_MAXLEN` (افتراضيّ 100000؛ `0` يعطّل القصّ). **راقب `XLEN` ولا تعامل السقف كحلّ**: القصّ قد يُسقط مدخلاتٍ لم يقرأها مستهلكٌ متعثّر وصفُّ الـoutbox موسومٌ `published` سلفاً ⇒ فقدٌ حقيقيّ لا إعادة تسليم.

---

## 5) الأسرار: Vault وAppRole

### 5.1 أيّ المتغيّرات مضبوطة **هو** المُبدِّل

`create_vault_client` **يفضّل التوكن متى وُجد**:

| الوضع | `VAULT_TOKEN` | `VAULT_ROLE_ID` | `VAULT_SECRET_ID` |
|---|---|---|---|
| Token (تجاوزٌ يدويّ — نادر) | توكنٌ جذرٌ حقيقيّ (§5.2) | — | — |
| **AppRole (الوضع العاديّ، محلّيّاً وفي كلّ بيئةٍ أخرى)** | **فارغ** | من Vault | يُحقن من الصَّدَفة، **لا من `.env`** |

> ⚠️ `VAULT_TOKEN=` الفارغ **ليس قيمةً ناقصة، بل هو المُبدِّل**. توكنٌ مضبوطٌ بجانب زوج AppRole يعني أنّ الزوج لا يُستعمل إطلاقاً — **والنشر يبدو ناجحاً**.

```bash
docker compose exec vault vault read  -field=role_id   auth/approle/role/app/role-id
docker compose exec vault vault write -f -field=secret_id auth/approle/role/app/secret-id
VAULT_TOKEN= VAULT_SECRET_ID='<الملتقَط>' docker compose up -d app
```

> `exec` لا `run`: مخرجات `exec` لا يلتقطها سائق سجلّات `json-file`، ومخرجات خدمةٍ يلتقطها **ويحتفظ بها**.

### 5.2 ⚠️ Vault دائمٌ الآن — واقرأ هذا قبل أوّل اعتمادٍ حقيقيّ (release-blockers-plan.md §3 خطوة 1)

**لم يعد Vault يعمل بوضع `-dev` (‏في الذاكرة بالكامل).** `docker-compose.yml` يشغّله بتخزين `file` دائم على حجمين مُسمَّيَين — `vault-data` (‏KV + Transit + AppRole، ما يملكه Vault نفسه) و`vault-init` (‏مفتاح فكّ الختم + التوكن الجذر، منفصلٌ عمداً) — و`deploy/vault/start.sh` هو ما يُقلع الحاوية الآن: يشغّل `vault server` بنفسه، ثمّ يقوده خلال `operator init` (أوّل إقلاعٍ فقط) أو `operator unseal` (كلّ إقلاعٍ بعده) **قبل** أن يستطيع فحص الصحّة (`vault status`) الإبلاغ بالنجاح — وهو ما يجعل `depends_on: {condition: service_healthy}` يعني ما يفترضه بقيّة هذا الملفّ: Vault **مفكوك الختم واستُخدِم فعلاً**، لا مجرّد «يستجيب».

**ما تغيّر عمليّاً:** `docker compose restart vault` **لا يمحو شيئاً** بعد الآن — KV والTransit وAppRole تبقى، ومفتاح Transit **نفسه** (لا يتجدّد؛ التحقّق حيٌّ في [`release-blockers-plan.md`](../release-blockers-plan.md) §4). لا حاجة لإعادة تشغيل `vault-bootstrap` بعد إعادة تشغيل Vault — القاعدة الذهبيّة القديمة «`docker compose up -d` لا `docker start`» **عادت غير ضروريّةٍ لهذا السبب تحديداً**، وإن بقيت صحيحةً لأسبابٍ أخرى (إعادة تشغيل خدماتٍ أخرى لم تُقلَع بعد).

**⚠️ لكنّ هذا وحده لا يكفي لإنتاجٍ حقيقيّ، وهذا مقصودٌ لا سهو.** لا يوجد KMS محلّيّاً يُفكّ الختم عبره تلقائيّاً بأمان، فـ`start.sh` يكتب **مفتاح فكّ الختم والتوكن الجذر في ملفٍّ نصّيٍّ عاديّ** على حجم `vault-init`، بصلاحيّة `chmod 600`: `docker compose exec vault cat /vault/init/init.json`. أيّ من يصل ذلك الحجم — نسخةً احتياطيّةً لم تُشفَّر، أو وصولاً لجذر المضيف — يستطيع فكّ ختم Vault وقراءة **كلّ** سرٍّ يحرسه. هذا **مقبولٌ لمضيفٍ واحدٍ محلّيّ فقط** (تجربة، تطوير) و**غير مقبولٍ إطلاقاً لإنتاجٍ حقيقيّ**.

**نسخٌ احتياطيّ واستعادة:**

```bash
# انسخ init.json إلى مكانٍ مُشفَّرٍ منفصل فور أوّل إقلاع -- هو المفتاح الوحيد
# لفكّ ختم Vault على هذا الحجم؛ فقدانه بينما vault-data قائمٌ يعني بياناتٍ
# غير قابلةٍ للفكّ إلى الأبد، تماماً كفقدان مفتاح Transit نفسه.
docker compose exec vault cat /vault/init/init.json > vault-init-backup.json   # احفظه خارج المستودع

# الاستعادة (حجم vault-init فُقد لكنّ vault-data ما زال سليماً): انسخ النسخة
# المحفوظة إلى الحجم قبل إقلاع vault من جديد -- start.sh يجدها ويفكّ الختم
# بها بدل محاولة تهيئةٍ جديدة (وهو يرفض ذلك تلقائيّاً إن وجد Vault مُهيَّأً
# بلا ملفٍّ محليٍّ يطابقه؛ راجع رسالة الخطأ في سجلّ الحاوية).
docker cp vault-init-backup.json aizzak-vault-1:/vault/init/init.json
docker compose restart vault
```

**مسار الترقية لإنتاجٍ حقيقيّ (وصفٌ لا تنفيذ):** auto-unseal عبر KMS خارجيّ (‏`seal "awskms"`/`"gcpckms"`/`"azurekeyvault"` في `server.hcl`) يزيل الحاجة لتخزين مفتاح فكّ ختمٍ على القرص إطلاقاً؛ أو Transit auto-unseal عبر مثيل Vault **آخر** خارج هذا المضيف يُستعمَل موفّر فكّ ختمٍ فقط. كلاهما يحتاج تلك الخدمة **موجودةً مسبقاً**، فلم تُنفَّذ هنا.

> ⚠️ **عمر التوكن مقيسٌ لا مُفترَض:** `token_ttl=1h` **لتوكن AppRole الذي يستعمله التطبيق** (لا علاقة له بالتوكن الجذر أعلاه). وVault على **مسار التشغيل** لا الإقلاع وحده، فالعَرَض تطبيقٌ يخدم الطلبات ساعةً ثمّ يعجز عن لمس أيّ سرّ مستأجِر — و`/health/ready` **أخضر طوال الوقت** لأنّه لا يسبر أيّ تبعيّة عمداً. العلاج منفَّذ (إعادة مصادقةٍ **واحدة** عند 403/401 ثمّ إعادة المحاولة).

---

## 6) المسار (ب): بلا حاويات — الحزام الأصليّ والاختبارات

الخدمات الأصليّة على هذا المضيف، **مقيسةٌ الآن** (‏2026‑07‑24):

| المنفذ | الخدمة | الحالة |
|---|---|---|
| 5432 | PostgreSQL 16 | ✅ مفتوح |
| 6432 | PgBouncer | ✅ مفتوح |
| 6379 | Redis | ✅ مفتوح |
| 9000 | MinIO | ✅ مفتوح |
| 6333 | Qdrant | ✅ مفتوح |
| 8200 | Vault | ✅ مفتوح |
| 11434 | Ollama | ✅ مفتوح |
| 8080 | خدمة التضمين | ⛔ مغلق — **حاويّةٌ فقط**، فاختبارات `live_embedding` تتخطّى نظيفاً |

اختبارات التكامل تعمل على قاعدة `aizzak_test` بدورَي `aizzak_owner` (المُهاجِر) و`app_rw` (المحكوم بـRLS)، والمنحُ خطوةُ `conftest`. كلّ عنوانٍ قابلٌ للتجاوز بمتغيّر بيئة (`TEST_DATABASE_URL`, `TEST_REDIS_URL`, `TEST_MINIO_ENDPOINT`, `TEST_QDRANT_URL`, `TEST_VAULT_ADDR`, `TEST_OLLAMA_BASE_URL`, `TEST_EMBEDDING_URL` …).

```bash
cd /home/AIZZAK && source .venv/bin/activate

pytest                               # الحزمة كاملةً — ما لا تجد خدمته يتخطّى نظيفاً
pytest -rs                           # المثل، مع طباعة اسم وسبب كلّ تخطٍّ (ما فعله CI)
pytest -m live_db                    # فوق PG16 الأصليّة (تتخطّى تلقائيّاً لو غير متاحة)
pytest -m live_qdrant                # وهكذا لكلّ مسبار live_*
```

> ⚠️ الوسم `integration` **حُذف** (§3.84): كان مُعلَناً في `pyproject.toml` وغيرَ مستعمَلٍ على أيّ اختبار، فـ`pytest -m "not integration"` لم يكن يستثني شيئاً و`pytest -m integration` كان يجمع صفراً ويخضرّ. لا تكتبه في أمرٍ جديد — البوّابة هي `pytest` وحدها.

> كلّ وسم `live_*` **يسبر المنفذ ويتخطّى تخطّياً حقيقيّاً** عند غيابه — لا تمريراً مقنّعاً. فـ`SKIPPED: no live embedding service reachable` نتيجةٌ صحيحة لا عطل.

---

## 7) دورة التطوير والبوّابات الخمس

```bash
ruff format . && ruff check .
mypy src
lint-imports                          # عقود الطبقات (.importlinter) — 8 عقود، الصفر خرقاً
pytest                                # الحزمة كاملةً (09-testing-strategy §7)
alembic revision -m "..."             # هجرة جديدة (لا FK عابراً بين schemas)
```

> ⚠️ `alembic upgrade head` **ليس أمراً صالحاً هنا**: v1 يشغّل إحدى عشرة سلسلةً مستقلّة (`version_table_schema` لكلّ وحدة)، فـ`head` غامضٌ وAlembic يرفضه بـ«Multiple head revisions are present». التسلسل الحقيقيّ **والمنحُ معه** مُرمَّزان في `app/ops/provision.py`:
>
> ```bash
> docker compose up migrate       # = python -m app.ops.provision
> ```
>
> والأدوار الثلاثة تُنشأ قبل ذلك عند تهيئة العنقود (`deploy/postgres/initdb/10-roles.sh`) لأنّ `CREATE ROLE` صلاحيّةُ عنقودٍ لا يملكها `aizzak_owner` عمداً.

---

## 8) التحقّق اليدويّ من التدفّق الكامل

1. احصل على Firebase ID Token (مشروع اختباريّ) وضعه في `Authorization: Bearer`.
2. أوّل طلبٍ مُصادَق ⇒ بذر المستخدم JIT + Workspace + دور `owner`.
3. جرّب:

```
POST /api/v1/files                    → upload_url ← ارفع إلى MinIO ← POST /files/{id}/complete
POST /api/v1/agents/rag_agent/invoke  (Accept: text/event-stream) → بثّ الردّ
POST /api/v1/media/jobs               → 202 ثمّ GET /media/jobs/{id} حتّى succeeded   ⛔ محجوب (MediaGenerator)
POST /api/v1/integrations/connections → authorize_url ← callback → GET /integrations/tools
GET  /api/v1/usage                    → ملخّص الاستهلاك
```

> ⛔ خطوة الفهرسة (‏`files.file.uploaded.v1` ← عامل المعرفة) **لا تكتمل اليوم**: عامل `knowledge` محجوبٌ بـ`DocumentContentResolver`.

---

## 9) استكشاف الأعطال

| العَرَض | الفحص |
|---|---|
| `app` في حلقة إعادة تشغيل بـ`secret does not exist in Vault` | Vault دائمٌ الآن (§5.2) فهذا لم يعد سببه فقدان الذاكرة عادةً — تحقّق أنّ `vault-bootstrap` خرج بنجاح: `docker compose ps vault-bootstrap` ثمّ سجلّه إن لم يكن كذلك |
| `vault-start: ⛔ 'operator init' failed, and .../init.json does not exist` | حجم `vault-data` مُهيَّأٌ من قبل لكنّ `vault-init` مفقودٌ أو استُبدل — **لا يمكن استرجاع الوصول بلا نسخةٍ احتياطيّةٍ من `init.json`** (§5.2) |
| التطبيق لا يقلع أصلاً | `FIREBASE_PROJECT_ID` فارغ (§2.1) — فشلٌ سريعٌ عند الإنشاء |
| الحافّة 502/504 بينما `ps` يقول `healthy` | راجع حالة خدمة **`nginx` نفسها** — فحص الحافّة هو الذي يعبر الرابط (§3) |
| AppRole لا يُستعمل رغم ضبط `ROLE_ID`/`SECRET_ID` | `VAULT_TOKEN` غير فارغ — التوكن يفوز دائماً (§5.1) |
| Vault يعمل ساعةً ثمّ يفشل كلّ سرّ | انتهاء `token_ttl` (توكن AppRole للتطبيق) بلا إعادة مصادقة — تحقّق أنّ `relogin` موصولٌ في جذر التركيب |
| `role_id` غير صالحٍ بعد أوّل إقلاع | الدور أُنشئ بمعرّفٍ عشوائيّ لأنّ `AIZZAK_APPROLE_ROLE_ID`/`VAULT_ROLE_ID` لم يكونا مضبوطَين وقتها؛ ثبّتهما في `.env` وأعد تشغيل `vault-bootstrap` (‏`docker compose up -d vault-bootstrap`) — الدور نفسه يبقى بعد ذلك عبر أيّ إعادة تشغيل |
| `https://localhost` يرفض الاتّصال | لم تُولَّد الشهادة: `docker compose up -d nginx-certs` ثمّ أعد تشغيل `nginx` |
| نموّ ذاكرة Redis | `XLEN stream.<وحدة>` — المجاري إلحاقيّة و`XACK` لا يحذف (§4) |
| 401 دائماً | صلاحية توكن Firebase / `FIREBASE_PROJECT_ID` |
| صفر صفوفٍ رغم وجود بيانات | لم يُضبط `app.workspace_id` (RLS) — تحقّق من `ExecutionContext`/`rls.py` |
| الأحداث لا تصل العمّال | حالة `outbox-relay`؛ `published_at` في `platform.outbox`؛ طول المجرى؛ **وترتيب الإقلاع** (§4) |
| مهامّ عالقة | نموّ `stream.<m>.dlq`؛ راجع سبب الفشل والمحاولات |
| رفض اتّصال Postgres | مرَّ عبر PgBouncer (6432) لا 5432؛ حجم التجمّع |
| `POST /search` يعيد 503 | خدمة `embedding` غير صحيحة — `docker compose ps embedding` وسجلّها |
| العامل ينهار عند الإقلاع | إن كان `knowledge`/`media` فهذا **مقصودٌ ومسجَّل** (§0)، لا عطل |
| رفض عملية بـ429 | تجاوز حصّة — `GET /usage` و`/usage/limits` (`USAGE_DEFAULT_LIMITS`) |
| `Exec format error` | تُشغّل من Git‑Bash على ويندوز؛ ادخل WSL (§1) |

---

## 10) القواعد الذهبيّة

1. **Vault دائمٌ الآن — `docker compose restart vault` لا يمحو شيئاً** (§5.2)، لكنّ حجم `vault-init` (المفتاح + التوكن) عاديٌّ غير مُشفَّر: انسخه احتياطيّاً قبل أيّ اعتمادٍ حقيقيّ.
2. **`VAULT_TOKEN` الفارغ هو المُبدِّل** — والتوكن يفوز دائماً حين يوجد.
3. **`VAULT_SECRET_ID` لا يُكتب في `.env` أبداً**؛ يُمرَّر من الصَّدَفة.
4. **العمّال قبل المُرحّل** على مجرًى جديد.
5. **حاويةٌ `healthy` ليست دليلاً على مسار بيانات** — الدليل في `deploy/smoke/`.
6. **`XACK` لا يحذف** — راقب `XLEN`.
7. **لا `alembic upgrade head`** — بل `python -m app.ops.provision`.
8. **`.env` مُتَجاهَلٌ في git.** أبقِه كذلك.

</div>
