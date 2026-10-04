<div dir="rtl">

# دليلُ التنبيهات — ماذا يعني كلُّ تنبيه، وماذا تفعل بالترتيب

> **خطّة السعة · الخطوة `7.3`** · 2026‑10‑04
> كلُّ قاعدةٍ في [`deploy/prometheus/alerts.yml`](../../deploy/prometheus/alerts.yml) تحمل رابطاً (`runbook_url`) إلى قسمها هنا. **تنبيهٌ بلا إجراءٍ مكتوبٍ ضجيجٌ يُطفَأ في ثالث مرّة.**
> والقسمُ لا يُعدّ صالحاً حتى **يجرّبه شخصٌ لم يكتبه** — الجدول في آخر الوثيقة يسجّل ذلك.

---

## 0) قبل أيّ تنبيه — أين تصل، وكيف تُسكَت

**أين تصل التنبيهات اليوم (قرار المالك 2026‑10‑04: محلّيّاً فقط):**

1. كلُّ تنبيهٍ يشتعل في Prometheus يُرسَل إلى **Alertmanager**، فيرسله إلى خدمة **`alert-sink`**.
2. `alert-sink` يكتب سطراً واحداً لكلّ تنبيه: اسمُه، وخطورتُه، وحالتُه (`firing` يعمل / `resolved` زال)، ورابطُ قسمه هنا.
3. لتقرأها:
   - في Grafana (`http://127.0.0.1:13000`): **Explore** ← مصدر **Loki** ← `{service="alert-sink"}`.
   - أو من الطرفيّة:

```bash
docker compose logs --since 1h alert-sink
```

4. **التنبيهُ نفسُه قد يظهر في أكثر من سطر.** حين يتغيّر شيءٌ في مجموعةٍ من التنبيهات (تنبيهٌ جديدٌ بالاسم نفسه، أو زوال)، تُرسَل المجموعةُ كلُّها من جديد. السطرُ المكرَّر يعني «ما زال قائماً»، لا حدثاً جديداً.
5. **قائمةُ ما يشتعل الآن** في Grafana: **Alerting ← Alert rules** (قواعد Prometheus) أو **Alerting ← Silences/Alerts** مع اختيار مصدر **Alertmanager**.

**الخطورة:**

| الخطورة | المعنى | التكرار ما دام قائماً |
|---|---|---|
| `critical` (حرج) | قدرةٌ ضاعت الآن (قاعدة بيانات أو Redis أو Vault، أو أخطاء كثيرة) | كلّ ساعة |
| `warning` (تحذير) | تراكمٌ أو اقترابٌ من حدّ — ما زال هناك وقت | كلّ 4 ساعات |
| `none` | نبضُ `AizzakWatchdog` وحده — ليس مشكلة | كلّ 5 دقائق |

**إسكاتُ التنبيهات أثناء عملٍ مخطَّط** (تشغيلُ حملٍ فوق نقطة الانكسار، صيانة):

1. Grafana ← **Alerting ← Silences** ← اختر مصدر **Alertmanager** ← **New silence**.
2. ضع مطابقةً بالاسم (مثلاً `alertname=~"AizzakApiErrorBudget.*|AizzakDbPoolSaturated"`) ومدّةَ العمل فقط.
3. **لا تعدّل حدّاً في `alerts.yml` لتُسكت تنبيهاً.** الحدُّ مُعايَرٌ على قياس؛ الإسكاتُ مؤقّتٌ ومرئيّ.

**ملاحظة عن تشغيلات الحمل:** تشغيلٌ فوق 150 طلباً/ث على هذا المضيف **سيُشعل** تنبيهات الإشباع وميزانيّة الخطأ. هذا هو عملها الصحيح.

---

## 1) فهرس التنبيهات

| التنبيه | الخطورة | باختصار |
|---|---|---|
| [`AizzakApiErrorBudgetBurnFast`](#aizzakapierrorbudgetburnfast) | حرج | أخطاء 5xx تأكل ميزانيّة الشهر بسرعة 14.4 ضعفاً |
| [`AizzakApiErrorBudgetBurnSlow`](#aizzakapierrorbudgetburnslow) | تحذير | تسرّبُ أخطاءٍ بطيءٌ ومستمرّ (6 أضعاف) |
| [`AizzakPgbouncerDown`](#aizzakpgbouncerdown) | حرج | مُجمِّع اتصالات قاعدة البيانات لا يردّ |
| [`AizzakRedisStreamDown`](#aizzakredisstreamdown) | حرج | Redis الخاصّ بالمجاري والجلسات لا يردّ |
| [`AizzakVaultAuthFailing`](#aizzakvaultauthfailing) | حرج | التطبيق لا يصادق مع Vault (أو Vault مختوم) |
| [`AizzakRedisStreamEvicted`](#aizzakredisstreamevicted) | حرج | Redis المجاري حذف مفتاحاً — سياستُه تغيّرت |
| [`AizzakStreamEntriesLost`](#aizzakstreamentrieslost) | حرج | أحداثٌ حُذفت قبل أن تُقرأ |
| [`AizzakDbPoolSaturated`](#aizzakdbpoolsaturated) | تحذير | مسبحُ اتصالات حاويةٍ ممتلئٌ 90% |
| [`AizzakPgbouncerClientsWaiting`](#aizzakpgbouncerclientswaiting) | تحذير | طلباتٌ تنتظر اتصالاً عند PgBouncer |
| [`AizzakEventLoopBlocked`](#aizzakeventloopblocked) | تحذير | عمليّةٌ في التطبيق تتجمّد أكثر من ثانية |
| [`AizzakStreamQueueWaitHigh`](#aizzakstreamqueuewaithigh) | تحذير | عملٌ في طابورٍ ينتظر أكثر من دقيقتين |
| [`AizzakRateLimitedShareHigh`](#aizzakratelimitedsharehigh) | تحذير | أكثر من 5% من الطلبات تُرفض بـ429 |
| [`AizzakRedisCacheDown`](#aizzakrediscachedown) | تحذير | Redis الذاكرة المخبّأة لا يردّ |
| [`AizzakLlmCircuitOpen`](#aizzakllmcircuitopen) | تحذير | مزوّدُ نموذجٍ لغويّ مقطوعٌ منذ دقيقتين |
| [`AizzakOutboxCycleTimeHigh`](#aizzakoutboxcycletimehigh) | تحذير | الأحداث لا تُنشر من قاعدة البيانات |
| [`AizzakDlqNotEmpty`](#aizzakdlqnotempty) | تحذير | رسالةٌ في طابور الرسائل الميّتة |
| [`AizzakRedisStreamMemoryHigh`](#aizzakredisstreammemoryhigh) | تحذير | Redis المجاري ممتلئ 80% |
| [`AizzakStreamBackstopHigh`](#aizzakstreambackstophigh) | تحذير | مجرًى طويلٌ يقترب من حدّ الحذف |
| [`AizzakScrapeTargetDown`](#aizzakscrapetargetdown) | تحذير | Prometheus لا يستطيع القراءة من خدمة |
| [`AizzakVectorShadowWrites`](#aizzakvectorshadowwrites) | تحذير | الفهرسة تكتب في نسخةٍ لا يبحث فيها أحد |
| [`AizzakOpsTaskOverdue`](#aizzakopstaskoverdue) | تحذير | مهمّةٌ مجدولةٌ لم تنجح منذ دورتين |
| [`AizzakOpsTaskNeverArmed`](#aizzakopstaskneverarmed) | تحذير | مهمّةٌ مجدولةٌ لم يشغّلها أحدٌ قطّ |
| [`AizzakWatchdog`](#aizzakwatchdog) | — | نبضُ نظام التنبيه نفسه |

---

## 2) التنبيهات الحرجة

<a id="aizzakapierrorbudgetburnfast"></a>

### `AizzakApiErrorBudgetBurnFast` — الأخطاء تأكل ميزانيّة الشهر بسرعة

**ماذا يعني:** الوعدُ 99.9% من الطلبات تنجح شهريّاً، أي ميزانيّةُ أخطاءٍ قدرُها 0.1%. الآن أكثرُ من **1.44%** من الطلبات تفشل بخطأ 5xx، في الساعة الأخيرة وفي الدقائق الخمس الأخيرة معاً. بهذا المعدّل تنفد ميزانيّةُ الشهر في يومين تقريباً. (رفضُ 429 المقصود لا يُحسَب.)

**الخطوات:**

1. هل يجري الآن تشغيلُ حمل؟ إن كان نعم، فهذا هو التشغيل — لا إصلاح.
2. في Grafana ← Explore ← Prometheus، اعرف أيَّ مسارٍ وأيَّ رمزٍ يفشل:
   `sum by (route, status) (rate(aizzak_http_requests_total{status=~"5.."}[5m]))`
3. انظر هل يشتعل معه `AizzakDbPoolSaturated` أو `AizzakPgbouncerClientsWaiting`. إن كان نعم، فالطلباتُ تنتظر اتصالاً بقاعدة البيانات ثمّ تفشل بعد 5 ثوانٍ — اتّبع قسمَ ذلك التنبيه.
4. وإلّا فاقرأ أخطاء التطبيق: Grafana ← لوحة **aizzak-logs**، أو Explore ← Loki ← `{service="app", level="error"}`.
5. إن بدأ بعد نشرٍ جديد، فارجع إلى الإصدار السابق ([`08 §4.15`](../design/08-local-runbook.md)).

**كيف تعرف أنّه زال:** يصل سطر `resolved` خلال دقائق من توقّف الأخطاء (النافذةُ القصيرة 5 دقائق).

---

<a id="aizzakpgbouncerdown"></a>

### `AizzakPgbouncerDown` — مُجمِّع اتصالات قاعدة البيانات لا يردّ

**ماذا يعني:** كلُّ الخدمات تصل إلى Postgres عبر PgBouncer. إن توقّف فكلُّ طلبٍ يلمس البيانات يفشل.

**الخطوات:**

1. تحقّق من حالته:

```bash
docker compose ps pgbouncer
```

2. إن كان متوقّفاً، شغّله (لا يحمل بياناتٍ خاصّةً به):

```bash
docker compose up -d pgbouncer
```

3. إن كان يعمل والتنبيهُ مستمرّ، فالمشكلةُ في دخول المُصدِّر لا في المُجمِّع. جرّب الدخول بيدك:

```bash
docker compose exec -T pgbouncer sh -c 'PGPASSWORD="$DB_PASSWORD" psql -h 127.0.0.1 -p 6432 -U "$DB_USER" -d pgbouncer -c "SHOW POOLS;"'
```

4. إن نجح الأمرُ أعلاه فالمنصّةُ سليمة، والخللُ في بيانات اعتماد المُصدِّر (`POSTGRES_SUPERUSER` في `.env`).

**كيف تعرف أنّه زال:** سطر `resolved` خلال أقلّ من دقيقة من عودته.

---

<a id="aizzakredisstreamdown"></a>

### `AizzakRedisStreamDown` — Redis المجاري والجلسات لا يردّ

**ماذا يعني:** `redis-stream` يحمل مجاري الأحداث، وجلسات WebSocket، ونوافذ حدود الطلبات، وقائمةَ الجلسات الملغاة. بدونه: الملفّات تُقبل ولا تُفهرس، وWebSocket يفشل، والحدود لا تُطبَّق. **لا يضيع حدث**: صندوق الصادر في Postgres يحتفظ بها حتى يعود.

**الخطوات:**

1. تحقّق وشغّله:

```bash
docker compose ps redis-stream
```

```bash
docker compose up -d redis-stream
```

2. بعد عودته، تأكّد أنّ المجاري رجعت (يجب أن يكون الرقم أكبر من صفر على مكدّسٍ مستعمَل):

```bash
docker compose exec -T redis-stream redis-cli XLEN stream.knowledge
```

3. إن عادت المجاري فارغةً على غير المتوقّع، أعد نشرَ الأحداث من صندوق الصادر ([`08 §4.22`](../design/08-local-runbook.md)).

**كيف تعرف أنّه زال:** سطر `resolved`، وتعود الفهرسة (لوحة السعة ← المجاري).

---

<a id="aizzakvaultauthfailing"></a>

### `AizzakVaultAuthFailing` — التطبيق لا يصادق مع Vault (ويشمل: Vault مختوم)

**ماذا يعني:** حاويةٌ من حاويات التطبيق لا تستطيع استعمال Vault منذ 5 دقائق: انتهت صلاحيّة `VAULT_SECRET_ID`، أو Vault مختوم، أو لا يُوصَل إليه. كلُّ عمليّةٍ تحتاج فكَّ تشفير تفشل الآن، **وإعادةُ التشغيل لن تُصلح — بل تمنع الإقلاع.**

**⚠️ الترتيبُ مهمّ — الترتيبُ الخاطئ يجعل الإصلاحَ الصحيح يبدو فاشلاً:**

1. هل Vault مختوم؟

```bash
docker compose exec -T -e VAULT_ADDR=http://127.0.0.1:8200 vault vault status
```

   إن قال `Sealed: true`، فأعد تشغيل حاوية Vault — سكربتُ إقلاعها يفكّ الختم وحده من المفتاح المسجَّل ([`08 §3.4`](../design/08-local-runbook.md)):

```bash
docker compose restart vault
```

   ثمّ انتظر 5 دقائق — يزول التنبيه وحده.
2. إن لم يكن مختوماً، فالسببُ انتهاءُ الـ`secret_id`. **أوقف التطبيق أوّلاً** (كلُّ محاولةٍ فاشلة تقرّب قفلَ المستخدم في Vault):

```bash
docker compose stop app
```

3. افكّ القفل إن وُجد، ثمّ اصنع `secret_id` جديداً وضعه في `.env` — الإجراءُ الكامل في [`08 §3.1`](../design/08-local-runbook.md).
4. أعد إنشاءَ التطبيق (لا `start`، فهو يعيد استعمال البيئة القديمة):

```bash
docker compose up -d --force-recreate --no-deps app
```

**كيف تعرف أنّه زال:** سطر `resolved`، و`aizzak_vault_authenticated` = 1 لكلّ حاوية.

---

<a id="aizzakredisstreamevicted"></a>

### `AizzakRedisStreamEvicted` — Redis المجاري حذف مفتاحاً

**ماذا يعني:** هذا الخادم مضبوطٌ ألّا يحذف شيئاً أبداً (`noeviction`). إن حذف، فقد غيّر أحدٌ سياستَه يدويّاً. قد تكون حُذفت أحداثٌ لم تُقرأ، أو جلسات، أو **مدخلةُ منعٍ لجلسةٍ ملغاة** (فتعود صالحة).

**الخطوات:**

1. افحص السياسة — يجب أن تقول `noeviction`:

```bash
docker compose exec -T redis-stream redis-cli CONFIG GET maxmemory-policy
```

2. إن كانت غيرَ ذلك، أعِدها:

```bash
docker compose exec -T redis-stream redis-cli CONFIG SET maxmemory-policy noeviction
```

3. اعرف ما ضاع: قارن المجاري بصندوق الصادر ([`08 §4.21`](../design/08-local-runbook.md) و[`§4.22`](../design/08-local-runbook.md)). واعتبر كلَّ جلسات WebSocket بحاجةٍ إلى إعادة اتصال.
4. إن كانت السياسةُ صحيحةً أصلاً، فالحاويةُ أُعيد تشغيلها بأمرٍ مختلف — قارن بـ`docker-compose.yml`.

**كيف تعرف أنّه زال:** يزول بعد 10 دقائق بلا حذفٍ جديد. الإصلاحُ الحقيقيّ هو الخطوة 3.

---

<a id="aizzakstreamentrieslost"></a>

### `AizzakStreamEntriesLost` — أحداثٌ حُذفت قبل أن تُقرأ

**ماذا يعني:** مجرًى قُصَّ فحُذفت منه أحداثٌ لم يقرأها العاملُ بعد. العملُ الذي طلبته (فهرسةُ مستند، معالجةُ وسائط، ذاكرة) **لن يحدث**، ولا شيءَ آخر يقول ذلك.

**الخطوات:**

1. اعرف المجموعةَ وحجمَ الفجوة:

```bash
docker compose exec -T app python -m app.ops.stream_trim status
```

2. أعد نشرَ ما ضاع من صندوق الصادر بأداة إعادة النشر ([`08 §4.22`](../design/08-local-runbook.md)) — التكرارُ آمن، فالعمّال يتجاهلون ما عالجوه.
3. ثمّ اعرف لماذا تأخّرت المجموعةُ حتى بلغها الحدّ (عاملٌ متوقّفٌ طويلاً عادةً).

**كيف تعرف أنّه زال:** يختفي الرقمُ حين تقرأ المجموعةُ ما بعد الفجوة؛ تأكّد أنّ المستندات المعنيّة صارت مفهرسة.

---

## 3) التحذيرات — السعة والإشباع

<a id="aizzakapierrorbudgetburnslow"></a>

### `AizzakApiErrorBudgetBurnSlow` — تسرّبُ أخطاءٍ بطيءٌ ومستمرّ

**ماذا يعني:** أكثرُ من **0.6%** من الطلبات تفشل منذ 6 ساعات، وما زالت في آخر نصف ساعة. ليس انقطاعاً، بل تسرّبٌ يُنفد ميزانيّةَ الشهر في نحو 5 أيّام.

**الخطوات:**

1. اعرف المسار (التسرّبُ البطيء يكون عادةً مساراً واحداً):
   `sum by (route, status) (increase(aizzak_http_requests_total{status=~"5.."}[6h]))`
2. افتح لوحة **aizzak-logs** وصفِّ بذلك المسار لترى سطرَ الخطأ.
3. أصلح، أو ارجع عن التغيير الذي أدخله ([`08 §4.15`](../design/08-local-runbook.md)).

**كيف تعرف أنّه زال:** يزول حين تنظف نصفُ الساعة الأخيرة.

---

<a id="aizzakdbpoolsaturated"></a>

### `AizzakDbPoolSaturated` — مسبحُ اتصالات حاويةٍ ممتلئٌ 90%

**ماذا يعني:** حاويةٌ من حاويات التطبيق تستعمل أكثر من 90% من اتصالاتها بقاعدة البيانات منذ دقيقتين. الطلبُ التالي ينتظر، وإن انتظر 5 ثوانٍ فشل بخطأ 500. (في القياس: 13% عند 150 طلباً/ث، و98% عند 200 مع بدء الأخطاء.)

**الخطوات:**

1. هل `AizzakPgbouncerClientsWaiting` يشتعل أيضاً؟ إن نعم، فالحدُّ في Postgres لا في المسبح — اذهب إلى قسمه.
2. هل حاويةٌ واحدةٌ فقط ممتلئة؟ اعرف توزيعَ الطلبات:
   `sum by (instance) (rate(aizzak_http_requests_total[5m]))`
   — توزيعٌ غيرُ متساوٍ مشكلةُ موازنة، لا سعة.
3. الحاوياتُ الثلاث ممتلئة = المنصّةُ فوق سعتها المقيسة: خفّف الحمل (حدود المستخدم والمساحة)، أو أضف سعةً وفق [`08 §2‑ب`](../design/08-local-runbook.md).
4. **لا ترفع `DB_POOL_SIZE` وحده** — ينقل الطابورَ إلى PgBouncer فقط.

**كيف تعرف أنّه زال:** سطر `resolved` حين يهبط تحت 90%.

---

<a id="aizzakpgbouncerclientswaiting"></a>

### `AizzakPgbouncerClientsWaiting` — طلباتٌ تنتظر اتصالاً عند PgBouncer

**ماذا يعني:** أكثرُ من 10 اتصالاتٍ تنتظر اتصالاً بـPostgres منذ دقيقتين. كلُّ واحدٍ طلبٌ أو مهمّةٌ متوقّفة. (في القياس: 4 عند نقطة الانكسار، وأكثر من 250 عند الإشباع.)

**الخطوات:**

1. اعرف أيَّ مسبحٍ ينتظر (عمود `cl_waiting`):

```bash
docker compose exec -T pgbouncer sh -c 'PGPASSWORD="$DB_PASSWORD" psql -h 127.0.0.1 -p 6432 -U "$DB_USER" -d pgbouncer -c "SHOW POOLS;"'
```

2. هل في Postgres معاملةٌ طويلةٌ تحجز اتصالات؟

```bash
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT pid, usename, state, now()-xact_start AS age, left(query,60) FROM pg_stat_activity WHERE xact_start IS NOT NULL ORDER BY xact_start LIMIT 10;"'
```

3. إن كانت هناك معاملةٌ عالقة منذ دقائق، فهي السبب — اعرف صاحبها قبل إنهائها.
4. وإلّا فهو حملٌ فوق السعة: نفسُ الخطوة 3 في `AizzakDbPoolSaturated`. ميزانيّةُ الاتصالات في [`08 §2‑ب`](../design/08-local-runbook.md).

**كيف تعرف أنّه زال:** `cl_waiting` يعود صفراً، وسطر `resolved`.

---

<a id="aizzakeventloopblocked"></a>

### `AizzakEventLoopBlocked` — عمليّةٌ في التطبيق تتجمّد أكثر من ثانية

**ماذا يعني:** عمليّةٌ في حاويةٍ من حاويات التطبيق لم تستطع تنفيذَ مؤقّتها أكثرَ من ثانية، في كلّ دقيقةٍ من آخر 5 دقائق. كلُّ ما تخدمه تلك العمليّة ينتظر. (الطبيعيّ: أجزاءٌ من الألف من الثانية.)

**الخطوات:**

1. هل `AizzakDbPoolSaturated` أو تنبيهُ ميزانيّة الخطأ يشتعل معه؟ إن نعم، فالسببُ الحمل — عالج الحمل.
2. إن اشتعل والمنصّةُ هادئة، فشيءٌ متزامنٌ يحجب العمليّة. افتح لوحة **aizzak-logs** على تلك الحاوية حول وقت التجمّد، وابحث عن الطلب الذي كان يجري.
3. إعادةُ تشغيلٍ متدرّجة ([`08 §4.15`](../design/08-local-runbook.md)) تزيل العَرَض مؤقّتاً — لكنّها لا تجد السبب؛ سجّل ما وجدته في الخطوة 2.

**كيف تعرف أنّه زال:** سطر `resolved` بعد دقيقةٍ بلا تجمّد.

---

<a id="aizzakstreamqueuewaithigh"></a>

### `AizzakStreamQueueWaitHigh` — عملٌ في طابورٍ ينتظر أكثر من دقيقتين

**ماذا يعني:** أقدمُ رسالةٍ لم تُسلَّم لعاملٍ تنتظر أكثر من 120 ثانية، منذ 5 دقائق. المستنداتُ لا تُفهرس (أو الوسائط لا تُعالج) بسرعة وصولها. وعلى طابور الفهرسة، الواجهةُ ترفض الرفعَ الجديد بـ429 الآن.

**الخطوات:**

1. اعرف العاملَ من اسم المجموعة في التنبيه: `cg.knowledge` ← `worker-knowledge` · `cg.media` ← `worker-media` · `cg.memory` ← `worker-memory`.
2. هل هو يعمل؟

```bash
docker compose ps worker-knowledge worker-media worker-memory
```

3. متوقّف؟ شغّله — لا شيء ضاع، الرسائلُ تنتظره:

```bash
docker compose up -d worker-knowledge
```

4. يعمل؟ اقرأ سجلّه بحثاً عن معالجٍ بطيءٍ أو يفشل:

```bash
docker compose logs --since 15m worker-knowledge
```

5. حملٌ دائمٌ أكبر من قدرة العمّال: [`08 §4.18`](../design/08-local-runbook.md) (`WORKER_CONCURRENCY`) و[`§4.0`](../design/08-local-runbook.md).

**كيف تعرف أنّه زال:** الانتظارُ يهبط تحت دقيقتين، وسطر `resolved`.

---

<a id="aizzakratelimitedsharehigh"></a>

### `AizzakRateLimitedShareHigh` — أكثر من 5% من الطلبات تُرفض بـ429

**ماذا يعني:** ليس عطلاً — كلُّ رفضٍ مقصود (حدُّ مستخدم، حدُّ مساحة عمل، سقفُ طلباتٍ متزامنة، مزوّدٌ ممتلئ، طابورُ فهرسةٍ ممتلئ). لكنّ رفضَ أكثر من طلبٍ من كلّ عشرين لعشر دقائق **محادثةُ سعة**.

**الخطوات:**

1. أيُّ حدٍّ يرفض؟
   `sum by (reason) (rate(aizzak_rate_limit_rejections_total[10m]))`
2. مساحةُ عملٍ واحدةٌ عند حدّها؟ هذا شأنُ ذلك المستأجر ([`08 §2‑د`](../design/08-local-runbook.md)).
3. سببٌ يبدأ بـ`llm`؟ مزوّدُ النموذج عند سقفه ([`08 §4.25`](../design/08-local-runbook.md)).
4. طابورُ الفهرسة؟ انظر `AizzakStreamQueueWaitHigh`.
5. **رفعُ حدٍّ قرارٌ عمّن يأخذ السعة، لا إصلاح.** قرّره صراحةً.

**كيف تعرف أنّه زال:** النسبةُ تهبط تحت 5%.

---

<a id="aizzakrediscachedown"></a>

### `AizzakRedisCacheDown` — Redis الذاكرة المخبّأة لا يردّ

**ماذا يعني:** لا شيءَ يضيع — كلُّ ما فيه يُبنى من جديد. لكنّ كلَّ طلبٍ يدفع ثمنَ قاعدة البيانات الذي كانت الذاكرةُ توفّره، فتوقّعْ بطئاً وضغطاً على المسبح، لا أخطاء.

**الخطوات:**

1. تحقّق وشغّله:

```bash
docker compose ps redis-cache
```

```bash
docker compose up -d redis-cache
```

2. يبدأ فارغاً ويمتلئ وحده — لا شيء لاسترجاعه.
3. إن ظلّ يسقط، اقرأ سجلّه:

```bash
docker compose logs --since 30m redis-cache
```

**كيف تعرف أنّه زال:** سطر `resolved` خلال دقيقة من عودته.

---

<a id="aizzakllmcircuitopen"></a>

### `AizzakLlmCircuitOpen` — مزوّدُ نموذجٍ لغويّ مقطوعٌ منذ دقيقتين

**ماذا يعني:** النداءاتُ إلى هذا المزوّد ظلّت تفشل، فأوقفها الحارس. المحادثاتُ عليه تفشل فوراً — أو يجيبها النموذجُ المحلّيّ مع إخبار المستخدم إن كان التحويلُ مفعّلاً.

**الخطوات:**

1. اقرأ سببَ الفشل:

```bash
docker compose logs --since 15m app | grep llm_guard
```

2. مزوّدٌ سحابيّ (`openai` مثلاً): صفحةُ حالته، ورصيدُ مفتاح المنصّة وحصّتُه.
3. `ollama` المحلّيّ:

```bash
docker compose ps ollama-bridge
```

   ثمّ تأكّد أنّ Ollama يعمل على المضيف.
4. لا شيء لإعادة ضبطه — القاطعُ يُغلق وحده حين تنجح محاولةُ اختبار ([`08 §4.25`](../design/08-local-runbook.md)).

**كيف تعرف أنّه زال:** سطر `resolved` بعد أوّل محاولةٍ ناجحة.

---

## 4) التحذيرات — المجاري والصادر والتخزين

<a id="aizzakoutboxcycletimehigh"></a>

### `AizzakOutboxCycleTimeHigh` — الأحداث لا تُنشر من قاعدة البيانات

**ماذا يعني:** أقدمُ حدثٍ في صندوق الصادر ينتظر أكثر من 3 ثوانٍ منذ دقيقتين. خدمةُ `outbox-relay` متوقّفةٌ أو عالقة. الأحداثُ محفوظةٌ في قاعدة البيانات ولن تضيع.

**الخطوات:**

1. تحقّق منها واقرأ سجلّها:

```bash
docker compose ps outbox-relay
```

```bash
docker compose logs --since 15m outbox-relay
```

2. أعد تشغيلها (هي تقرأ الصادر من جديد عند البدء) — **لا تعدّل البيانات يدويّاً**:

```bash
docker compose restart outbox-relay
```

3. إن عادت للتوقّف، فغالباً `redis-stream` لا يُوصَل إليه — انظر `AizzakRedisStreamDown`.

**كيف تعرف أنّه زال:** سطر `resolved` بعد دقيقتين من النشر الطبيعيّ.

---

<a id="aizzakdlqnotempty"></a>

### `AizzakDlqNotEmpty` — رسالةٌ في طابور الرسائل الميّتة

**ماذا يعني:** رسالةٌ فشلت معالجتُها 5 مرّات (أو كانت تالفة) فنُقلت إلى طابور الرسائل الميّتة. **لا تُعالج نفسها أبداً** — تحتاج قرارك.

**الخطوات:**

1. انظر ما فيها (القراءةُ لا تغيّر شيئاً) — المجرى من تسمية التنبيه أو من لوحة السعة:

```bash
docker compose exec -T app python -m app.ops.dlq peek stream.media
```

2. لكلّ رسالة: هل سببُ فشلها زال (خدمةٌ عادت، خطأٌ أُصلح)؟ إن نعم فأعدها إلى الطابور بأمر `requeue`. وإن كانت لا قيمةَ لها فاحذفها بـ`purge`. الإجراءُ الكامل في [`08 §4.2`](../design/08-local-runbook.md).

**كيف تعرف أنّه زال:** الطابورُ فارغ، وسطر `resolved` بعد 5 دقائق.

---

<a id="aizzakredisstreammemoryhigh"></a>

### `AizzakRedisStreamMemoryHigh` — Redis المجاري ممتلئ 80%

**ماذا يعني:** عند 100% لن يبطؤ ولن يحذف — **سيرفض كلَّ كتابة**: الأحداث، وجلسات WebSocket، وحسابَ الحدود، وإلغاءَ الجلسات. الشيءُ الوحيد بلا حدٍّ عليه هو طوابيرُ الرسائل الميّتة.

**الخطوات:**

1. كم المستعمَل:

```bash
docker compose exec -T redis-stream redis-cli INFO memory
```

2. أطوالُ طوابير الرسائل الميّتة الثلاثة:

```bash
docker compose exec -T redis-stream redis-cli XLEN stream.knowledge.dlq
```

   (وكذلك `stream.media.dlq` و`stream.memory.dlq`). إن كانت كبيرة، فرّغها باتّباع `AizzakDlqNotEmpty`.
3. إن كان النموُّ في المجاري الأصليّة، فمجموعةٌ متوقّفة — انظر `AizzakStreamBackstopHigh`.
4. **رفعُ `maxmemory` يشتري وقتاً ولا يُصلح شيئاً.**

**كيف تعرف أنّه زال:** الذاكرةُ تحت 80%، وسطر `resolved`.

---

<a id="aizzakstreambackstophigh"></a>

### `AizzakStreamBackstopHigh` — مجرًى طويلٌ يقترب من حدّ الحذف

**ماذا يعني:** مجرًى بلغ 70% من حدّه الأقصى. هذا يعني أنّ مجموعةً توقّفت عن القراءة. عند 100% يبدأ حذفُ أحداثٍ لم تُقرأ.

**الخطوات:**

1. اعرف المجموعةَ التي تمسك القصّ:

```bash
docker compose exec -T app python -m app.ops.stream_trim status
```

2. مجموعةُ عامل (`cg.knowledge` · `cg.media` · `cg.memory`)؟ أعد العاملَ إلى العمل — لم يضع شيءٌ بعد.
3. مجموعةٌ لن يقرأها أحدٌ أبداً؟ احذفها وفق [`08 §4.21`](../design/08-local-runbook.md).
4. كلُّ المجموعات لاحقة والمجرى ما زال طويلاً؟ القصُّ نفسه لا يعمل — اقرأ سجلّ `outbox-relay` (أسطر `stream_trim`).

**كيف تعرف أنّه زال:** الطولُ يهبط تحت 70%.

---

<a id="aizzakvectorshadowwrites"></a>

### `AizzakVectorShadowWrites` — الفهرسة تكتب في نسخةٍ لا يبحث فيها أحد

**ماذا يعني:** متوقَّعٌ أثناء ترحيل نموذج التضمين. خارج ذلك: نموذجُ التضمين تغيّر ولم يُرحَّل الفهرس، فكلُّ مستندٍ فُهرس منذئذٍ **غيرُ قابلٍ للبحث** — بلا خطأٍ في أيّ مكان.

**الخطوات:**

1. اعرف الحالة:

```bash
docker compose exec -T app python -m app.ops.embedding_migration status --json
```

2. هل ترحيلٌ (`build`) يجري الآن بعلمك؟ إن نعم فهذا هو الإجراء يعمل.
3. وإلّا: إمّا أكمل الترحيل (`build` ثمّ `verify`)، أو أرجع إعدادات النموذج السابقة — الإجراءُ في [`08 §4.17`](../design/08-local-runbook.md). المستنداتُ لم تضع؛ الترحيلُ التالي يلتقطها.

**كيف تعرف أنّه زال:** بعد 10 دقائق بلا كتابةٍ في النسخة الظلّ.

---

## 5) التحذيرات — المراقبة والمهامّ المجدولة

<a id="aizzakscrapetargetdown"></a>

### `AizzakScrapeTargetDown` — Prometheus لا يستطيع القراءة من خدمة

**ماذا يعني:** خدمةٌ لم تعد تجيب Prometheus منذ 30 ثانية. كلُّ تنبيهٍ يعتمد على أرقامها **صامتٌ الآن** — والصمتُ يشبه الصحّة تماماً.

**الخطوات:**

1. اسمُ الخدمة في تسمية `job` بالتنبيه. تحقّق منها:

```bash
docker compose ps
```

2. متوقّفة؟ شغّلها بـ`docker compose up -d <اسم الخدمة>`.
3. تعمل ولا تُقرأ؟ غالباً منفذٌ أو مسارٌ تغيّر. قائمةُ الأهداف وآخرُ خطأ:

```bash
docker compose exec -T prometheus wget -qO- http://127.0.0.1:9090/api/v1/targets
```

4. ⚠️ إن كانت الخدمةُ هي `alertmanager` فهذا التنبيه **لن يصلك** — تعرفه من توقّف نبض `AizzakWatchdog`.

**كيف تعرف أنّه زال:** سطر `resolved` بعد أوّل قراءةٍ ناجحة.

---

<a id="aizzakopstaskoverdue"></a>

### `AizzakOpsTaskOverdue` — مهمّةٌ مجدولةٌ لم تنجح منذ دورتين

**ماذا يعني:** مهمّةٌ مجدولة (النسخ الاحتياطيّ، التنظيف، تدوير المفاتيح…) لم تنجح منذ دورتين. إمّا لا تعمل، وإمّا تعمل وتفشل كلَّ مرّة.

**الخطوات:**

1. حالةُ كلّ المهامّ وآخرُ خطأ:

```bash
docker compose exec -T ops-scheduler python -m app.ops.scheduler status
```

2. اقرأ سجلَّ الخدمة التي تشغّلها (العمود الثالث في الأمر أعلاه)، مثلاً:

```bash
docker compose logs --since 2h ops-scheduler
```

3. أصلح السبب، ثمّ شغّلها مرّةً يدويّاً بالطريق نفسه:

```bash
docker compose exec -T ops-scheduler python -m app.ops.scheduler run-once backup --yes
```

   (ضع اسمَ المهمّة بدل `backup`). التفاصيل في [`08 §4.23`](../design/08-local-runbook.md).
4. ملاحظة: بعد توقّف المكدّس كلّه مدّةً طويلة، قد يشتعل هذا لمهامّ الدورات القصيرة ثمّ يزول وحده بعد أوّل نجاح.

**كيف تعرف أنّه زال:** `status` يقول `ok` ونجاحٌ حديث، وسطر `resolved`.

---

<a id="aizzakopstaskneverarmed"></a>

### `AizzakOpsTaskNeverArmed` — مهمّةٌ مجدولةٌ لم يشغّلها أحدٌ قطّ

**ماذا يعني:** مهمّةٌ في قائمة المنصّة لم تكتب عنها أيُّ خدمةٍ سطراً واحداً منذ نصف ساعة: الخدمةُ التي يجب أن تشغّلها غيرُ موجودة، أو لم تكتمل بدايتُها، أو صورتُها قديمة.

**الخطوات:**

1. اعرف من يجب أن يشغّلها:

```bash
docker compose exec -T ops-scheduler python -m app.ops.scheduler status
```

2. تأكّد أنّ تلك الخدمة تعمل (`docker compose ps`)، وإلّا شغّلها.
3. إن كانت تعمل، ابحث في سجلّها عن `scheduled_task.ledger_write_failed` أو `ops_scheduler.tick_failed`.

**كيف تعرف أنّه زال:** تظهر المهمّة مُسلَّحةً في `status`.

---

<a id="aizzakwatchdog"></a>

### `AizzakWatchdog` — نبضُ نظام التنبيه نفسه

**ماذا يعني:** **ليس مشكلة.** يشتعل دائماً عمداً، ويصل سطرُه كلَّ 5 دقائق ليثبت أنّ Prometheus وAlertmanager و`alert-sink` كلَّها تعمل. **المشكلةُ حين يتوقّف.**

**الخطوات (فقط إن لم يصل سطرٌ منه منذ 10 دقائق):**

1. تحقّق من الخدمات الثلاث:

```bash
docker compose ps prometheus alertmanager alert-sink
```

2. شغّل أيَّها متوقّف بـ`docker compose up -d <اسم الخدمة>`.
3. تأكّد من عودة النبض:

```bash
docker compose logs --since 10m alert-sink
```

---

## 6) سجلُّ التجربة — شرطُ قبول `7.3`

القسمُ يُعدّ صالحاً حين يجرّبه **شخصٌ لم يكتبه** ويصل إلى النتيجة دون مساعدة. لكلّ تجربة: الاسم، والتاريخ، وهل نجح، وما كان غامضاً.

| التنبيه | جرّبه | التاريخ | النتيجة | ملاحظات |
|---|---|---|---|---|
| `AizzakRedisCacheDown` | — | — | — | — |
| `AizzakPgbouncerDown` | — | — | — | — |
| `AizzakDlqNotEmpty` | — | — | — | — |
| `AizzakOpsTaskOverdue` | — | — | — | — |
| `AizzakWatchdog` | — | — | — | — |
| (البقيّة) | — | — | — | — |

</div>
