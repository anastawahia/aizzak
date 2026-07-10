# سجلّ قرارات التصميم التفصيلي (Detailed‑Design Decisions)

> امتداد لسجلّ القرارات المعمارية الـ26 (`D‑01…D‑26`) في [`../architecture.md`](../architecture.md).
> هذه القرارات (`DD‑01…DD‑14`) تُترجم المعمارية المعتمدة إلى **عقود بناء** (schemas · signatures · protocols).
> نمط الحسم المُعتمد: **«احسم القياسي بنفسك، وقف عند عالي الأثر»** — لذا البنود القياسية محسومة هنا، والبنود عالية الأثر (كانت `DD‑10 · DD‑12 · I‑1`) كانت مُعلَّمة بـ ⚠️ **نقطة مراجعة** ثم **حُسمت جميعها بتوقيع العميل 2026‑07‑10** (`OQ‑01/OQ‑02/OQ‑03`).

| الحالة | المعنى |
|--------|--------|
| ✅ محسوم | قرار قياسي اتُّخذ وفق المعمارية وأفضل الممارسات |
| ⚠️ نقطة مراجعة | قرار افتراضي عالي الأثر (أمان/أداء/توسّع) — يعمل التصميم عليه، لكن يُرجى تأكيده أو تعديله |

---

## الأساس المشترك (Cross‑Cutting)

### DD‑01 · تنظيم PostgreSQL — ✅ Schema لكل وحدة
**عشرة** Schemas مملوكة للوحدات: `workspace · access · credentials · conversations · memory · files · knowledge · media · integrations · usage`،
بالإضافة إلى Schema بنيوي `platform` يملك جداول البنية التحتية للأحداث (`outbox`, `processed_events`, `stream_offsets`).
- **ممنوع** أي مفتاح أجنبي (FK) عابر بين schemas الوحدات — المرجعية بين الوحدات بالـ `id` فقط (UUID مُعتَّم)، لفرض حدود الـModular Monolith على مستوى قاعدة البيانات.
- FK مسموحة **داخل** schema الوحدة الواحدة فقط.
- كل وحدة تملك سلسلة هجرات Alembic خاصة بها ضمن `version_table_schema` منفصل، وتُدار مركزياً بأمر واحد.
- **المجموعة مفتوحة للتوسّع** (`FR‑110`): تُحجَز مسبقاً أسماء schemas للوحدات المستقبلية الثلاث `scheduling · sandbox · runs` — **لا تُنشأ في v1** وتُضاف بهجرة مستقلّة عند اعتمادها (القسم 12.1 من المتطلبات).

### DD‑02 · نوع المعرّف — ✅ UUIDv7 (RFC 9562)
مفتاح أساسي `id uuid` لكل الكيانات، يُولَّد **في التطبيق** (مكتبة `uuid6`/`uuid_utils`) لا في قاعدة البيانات — كي يملك النطاق (Domain) هويته قبل الحفظ.
- قابل للترتيب زمنياً ⇒ مَوضِعية فهرسة B‑Tree عالية تحت الحِمل الكتابي.
- آمن للكشف في الـAPI (لا يسرّب أعداداً)، ومناسب كـ`event_id` عبر مجاري Redis.
- التخزين نوع `uuid` الأصلي في Postgres.

### DD‑03 · الأعمدة القياسية للكيانات — ✅
| العمود | النوع | ينطبق على |
|--------|------|-----------|
| `id` | `uuid` PK (UUIDv7) | كل الجداول |
| `workspace_id` | `uuid NOT NULL` | كل جدول مستأجَر (كل الجداول عدا كتالوجات المنصّة) |
| `created_at` | `timestamptz NOT NULL DEFAULT now()` | كل الجداول |
| `updated_at` | `timestamptz NOT NULL DEFAULT now()` (trigger عند التحديث) | الكيانات القابلة للتعديل |
| `deleted_at` | `timestamptz NULL` (حذف ناعم) | المحتوى الموجَّه للمستخدم: `files`, `conversations`, `messages`, `memory_items` |
| `version` | `integer NOT NULL DEFAULT 1` (قفل تفاؤلي) | الـAggregate Roots القابلة للتعديل |
| `created_by` | `uuid NULL` (user_id) | حيث تُفيد نسبة الفعل |

- كل الأوقات **UTC** حصراً على مستوى التخزين والنقل (ISO‑8601 `Z`).
- الجداول السجلّية (append‑only) — `platform.outbox`, `platform.processed_events`, والأحداث — **بلا** `deleted_at` ولا `version`.

### DD‑04 · آلية عزل المستأجر (تنفيذ D‑23) — ✅
دفاع بعمق من طبقتين:
1. **RLS أصلية**: على كل جدول مستأجَر سياسة
   `USING (workspace_id = current_setting('app.workspace_id', true)::uuid)`.
   يُضبط السياق لكل معاملة عبر `SET LOCAL app.workspace_id = :ws` من `ExecutionContext` (`infrastructure/persistence/rls.py`).
2. **ترشيح تطبيقي**: كل استعلام Repository يحمل `WHERE workspace_id = :ws` صراحةً.
- دور قاعدة البيانات الخاص بالتطبيق **خاضع لـRLS** (لا `BYPASSRLS`، ليس مالك الجدول).
- العمّال يضبطون نفس السياق قبل أي كتابة نيابةً عن مستأجر.

---

## عقد الـAPI

### DD‑05 · نموذج الأخطاء — ✅ RFC 9457 Problem Details
`Content-Type: application/problem+json`. الحقول: `type` (URI), `title`, `status`, `detail`, `instance`،
وامتدادات: `code` (سلسلة آلة ثابتة مثل `files.too_large`), `correlation_id`, `errors[]` (لأخطاء التحقق حقلاً حقلاً).
كتالوج الأكواد الكامل في [`03-api-spec.md`](03-api-spec.md).

### DD‑06 · اصطلاحات الـAPI — ✅
- تسمية JSON: `snake_case` (توافق مع Python/النطاق).
- الترقيم: **بالمؤشّر (cursor‑based)** — `?limit=&cursor=`؛ الرد يحمل `meta.next_cursor`. (مستقرّ تحت الإدراج، مناسب للتوسّع.)
- غلاف المجموعات: `{ "data": [...], "meta": { "next_cursor": "...", "limit": N } }`؛ المورد المفرد يُعاد **مجرّداً**.
- الإصدار في المسار: `/api/v1`. الحقول الإضافية غير الكاسرة تُضاف داخل v1؛ الكسر ⇒ `/api/v2`.
- التوقيت في الـpayload: ISO‑8601 UTC. المعرّفات: نصوص UUID.

---

## عقد الأحداث

### DD‑07 · مظروف الحدث — ✅ CloudEvents 1.0 (JSON)
يحقّق «المظروف الموحّد + correlation_id» المحجوز كنقطة توسعة Observability. الحقول:
`specversion, id (=event_id UUIDv7), source (اسم الوحدة), type, subject (aggregate id), time, datacontenttype=application/json, dataschema (URI لمخطط JSON)`
+ امتدادات: `workspaceid, correlationid, causationid`. الحمولة تحت `data`.

### DD‑08 · تسمية الأحداث وإصدارها — ✅
النمط: `<module>.<aggregate>.<event>.vN` (مثل `knowledge.document.indexed.v1`).
- تغيير كاسر ⇒ إصدار `vN+1` جديد مع دعم مؤقت للإصدارين.
- إضافة حقل اختياري غير كاسرة ضمن نفس الإصدار.
- كل نوع حدث له مخطط JSON Schema في [`events/schemas/`](events/schemas/).

### DD‑09 · الـIdempotency (تنفيذ D‑19) — ✅
- جدول `platform.processed_events (consumer_group text, event_id uuid, processed_at timestamptz, PRIMARY KEY (consumer_group, event_id))`.
- المستهلك يُدرج السجلّ **داخل نفس معاملة** الأثر الجانبي؛ تعارض المفتاح ⇒ تجاهُل (سبق المعالجة) ⇒ `at‑least‑once` تتحول فعلياً إلى `effectively‑once`.
- مفاتيح عمل طبيعية إضافية حيث تتوفّر (مثل `UNIQUE(document_id, seq)` للـchunks).

---

## الأمان والإعداد

### DD‑10 · نموذج RBAC — ✅ محسومة — معتمدة بتوقيع العميل 2026‑07‑10 (OQ‑01)
أدوار داخل الـWorkspace: `Owner · Admin · Member · Viewer`، ودور منصّة عابر: `PlatformAdmin`.
الصلاحيات بصيغة `resource:action`. المصفوفة الكاملة في [`05-rbac-config-secrets.md`](05-rbac-config-secrets.md).
الأدوار **ثابتة** في v1 (كتالوج بالكود)؛ الأدوار المخصّصة نقطة توسعة.
> **محسومة (OQ‑01):** طقم الأدوار الأربعة وتوزيع الصلاحيات معتمد بتوقيع العميل 2026‑07‑10. (كان عالي‑الأثر لأنه حدّ التفويض؛ يبقى الجدول أدناه مرجعاً.)

### DD‑11 · تقسيم الإعداد/الأسرار — ✅
- **إعداد غير سرّي** (Settings Service من متغيّرات البيئة): عناوين الخدمات، أحجام التجمّع، الحدود الرقمية، رايات الميزات، جدول توجيه النماذج.
- **أسرار** (Vault): كلمات مرور DB/Redis/MinIO، حساب خدمة Firebase، مادّة توقيع JWT، مفاتيح المزوّدين على مستوى المنصّة (Vault KV)، ومفاتيح المستخدمين (تُشفَّر عبر **Vault Transit** وتُخزَّن مُعمّاة في وحدة `credentials`).
- **لا وحدة تقرأ `.env` مباشرة** — فقط `infrastructure/config` يجمع البيئة + Vault ويقدّمها عبر عقد `Settings`.

### DD‑12 · أهداف SLO والحدود الرقمية — ✅ محسومة — معتمدة بتوقيع العميل 2026‑07‑10 (OQ‑02)
جداول افتراضية لمنصّة تخدم «آلاف المستخدمين» في [`07-nfr-slo.md`](07-nfr-slo.md).
> **محسومة (OQ‑02):** الأرقام (مستخدمون متزامنون، p95، أحجام الرفع، حدود الرموز، أقصى وكلاء لكل Workflow، حدود المعدّل، N قبل DLQ) معتمدة بتوقيع العميل 2026‑07‑10. (كانت عالية‑الأثر لأنها تمسّ الأداء والتوسّع؛ تبقى جداول [`07-nfr-slo.md`](07-nfr-slo.md) مرجعاً.)

---

## المزوّدون والأدوات

### DD‑13 · المزوّدون الخارجيون — ✅ منافذ محايدة للمزوّد
المنافذ (`LLM/Embedding/Image/Video`) مُصمَّمة على **القاسم المشترك** بين كبار المزوّدين، ولا تُسرّب تفاصيل أيّ مزوّد.
- LLM: محوّلات أصلية لـ OpenAI · Gemini · Claude · Ollama · OpenRouter (D‑15).
- Embedding/Image/Video: توقيعات محايدة؛ المحوّل الملموس قابل للتوصيل. جدول التوجيه (`ProviderResolver`, D‑16) في الإعداد.

### DD‑14 · أدوات Python الأساسية — ✅
`Ruff` (تنسيق + فحص) · `mypy --strict` · `pytest` + `pytest-asyncio` + `testcontainers` ·
`SQLAlchemy 2.0` (async) + `asyncpg` · `Alembic` · `Pydantic v2` (DTO/Settings) · `import-linter` (فرض الطبقات، D‑17).
التفاصيل في [`10-code-standards.md`](10-code-standards.md).

---

## نقاط التفسير (Interpretations) — ✅ محسومة — معتمدة بتوقيع العميل 2026‑07‑10 (OQ‑03)

### I‑1 · نموذج عضوية الـWorkspace
القاعدة «لكل مستخدم Workspace واحد» + وجود أدوار `Owner/Admin/Member/Viewer` قد يتعارضان ظاهرياً.
التفسير المُعتمد في التصميم:
- **Workspace = مستأجر شخصي، واحد لكل مستخدم يملكه (Owner).** (يحقّق القاعدة الصريحة.)
- **نموذج `access` عام** (`RoleAssignment: user × workspace × role`) بحيث يصبح التعاون متعدّد المستخدمين **توسعةً مستقبلية بلا تغيير مخطط**. في v1 يُبذَر `Owner` فقط في workspace المالك، و`PlatformAdmin` عابر.
> **محسومة (OQ‑03):** التفسير المُعتمد (Workspace = مستأجر شخصي واحد لكل مستخدم، مع نموذج `access` عام قابل للتوسّع) معتمد بتوقيع العميل 2026‑07‑10. يعمل المخطط تحت التفسيرين؛ الاختلاف في منطق البذر/الإسناد فقط.

---

## مواءمة ما بعد الاعتماد (2026‑07‑10 · مطابقة للمتطلبات والمعمارية)

> تُبنى على سجلّي `D‑01…D‑26` و`DD‑01…DD‑14` **دون تغييرهما**، وتُترجم القرارات المُحدَّثة في `architecture.md` و`Requirements-v1.md` إلى عقود بناء في هذه الوثائق الفرعية:

1. **عشر وحدات v1** بعد ترقية `integrations` و`usage` من محجوزتين إلى وحدتَي v1 (`FR‑120…124`, `FR‑130…134`) — يُحدِّث حصر DD‑01؛ المحجوز الآن **3** فقط (`scheduling · sandbox · runs`).
2. **قياس `usage` خارج الناقل:** الالتقاط عبر **منفذ وارد متزامن** يُلحِق في **سجلّ استهلاك append‑only** (لا Redis Streams — صوناً لقصر `EVT‑10` على الثقيل)، والفرض عبر **منفذ وارد يعيد كائن قرار (Decision Object)** قابلاً للتطوّر إلى **reserve/commit** بلا كسر عقد (`FR‑131/132`). تُستدعى منافذ `usage` من **طبقة الوكلاء (المُنسِّق) حصراً**.
3. **إدمبوتنسي الالتقاط** عبر مفتاح طبيعي (`operation_id`) مفروض بـ`UNIQUE` داخل schema `usage` (`FR‑134`).
4. **`integrations`:** موصّلات OAuth + **MCP بنقل بعيد (HTTP/SSE) حصراً** في v1 (نقل stdio المحلي يتبع `sandbox`، `ARC‑15`)؛ **تجديد OAuth كسول** بلا اعتماد على `scheduling`؛ استهلاك الموصّلات **عبر نظام الأدوات** بكتالوج ديناميكي لكل Workspace (`FR‑52`, `FR‑122`, `FR‑124`).
5. **أسرار موحّدة (SEC‑07):** تعمية `credentials` و`integrations` عبر **Vault Transit** بمفتاح ودورة تدوير موحّدين، بحدّ ملكية واضح ولا ازدواج تخزين.
6. **مخرَج جديد #12:** دليل تأليف الوحدات ([`12-module-authoring-guide.md`](12-module-authoring-guide.md)) نظيراً لدليل تأليف الوكلاء (`FR‑110…112`).

---

## مصفوفة التتبّع (Deliverable → Decisions)

| المخرَج | القرارات الحاكمة |
|---------|------------------|
| 01 نموذج البيانات | DD‑01, DD‑02, DD‑03, DD‑04 |
| 02 عقود المنافذ | DD‑13 · FR‑52/122/124/131/132 |
| 03 مواصفة API | DD‑05, DD‑06 · FR‑120/130 |
| 04 كتالوج الأحداث | DD‑07, DD‑08, DD‑09 · EVT‑10 (قصر الأحداث؛ `usage` خارج الناقل) |
| 05 RBAC + الإعداد/الأسرار | DD‑10 ✅, DD‑11 · SEC‑07 |
| 06 نماذج النطاق | DD‑02, DD‑03, I‑1 ✅ · FR‑120…134 |
| 07 NFR/SLO | DD‑12 ✅ · FR‑133 |
| 08 دليل التشغيل | DD‑11, DD‑14 |
| 09 الاختبار + import‑linter | DD‑14, D‑17 · AC‑14/15/16 |
| 10 معايير الكود | DD‑14 |
| 11 دليل تأليف الوكلاء | D‑05, D‑08, D‑13 |
| 12 دليل تأليف الوحدات | FR‑110, FR‑111, FR‑112 · ARC‑03/07/08/10/11 |
