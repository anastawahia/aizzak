# استراتيجية الاختبار + عقود import-linter

> الأدوات (`DD‑14`): `pytest` + `pytest-asyncio` + `pytest-cov` + `testcontainers` · `import-linter` (D‑17) · `mypy --strict` · `Ruff`.
> عقود الاعتماد التنفيذية في [`../../.importlinter`](../../.importlinter) (الجذر).

## 1) هرم الاختبار
```
        ┌───────────────┐
        │  E2E / smoke  │  قليلة — تدفّق كامل عبر HTTP + بنية حقيقية
        ├───────────────┤
        │  Integration  │  محوّلات فعلية عبر testcontainers (PG/Redis/MinIO/Qdrant)
        ├───────────────┤
        │  Architecture │  import-linter — حدود الطبقات والوحدات (سريعة، بلا I/O)
        ├───────────────┤
        │     Unit      │  الأغلبية — نطاق نقي + Use-Cases بمنافذ مزيّفة
        └───────────────┘
```
مجلدات: `tests/unit/` · `tests/integration/` · `tests/architecture/`.

## 2) اختبارات الوحدة (Unit) — النواة
- **النطاق نقي** ⇒ يُختبر بلا أي بنية تحتية (لا DB، لا شبكة). ثوابت (INV) وValue Objects وانتقالات الحالة.
- **دورة حياة الوكيل (آلة الحالات `FR‑15`):** حالة صريحة تختبر المسار الناجح `Created → Initialized → Running → Completed → Disposed` ومسار الفشل `Created → Initialized → Running → Failed → Disposed`، وأن الوكيل **عديم الحالة** يُنشأ/يُتلَف لكل طلب (`FR‑10/11`) مع **ضمان `dispose` دائماً** (حتى عند الاستثناء) — يغطّي `AC‑06`.
- **Use-Cases** تُختبر بمنافذ **مزيّفة** (Fakes/InMemory) تحقّق عقود `02-port-contracts.md` — لا Mock هشّ للتفاصيل.
- الدوال النقية (`is_allowed` في RBAC، ProviderResolver) تُغطّى بجداول حالات.
- الهدف: تغطية عالية للنطاق والتطبيق (≥ 90%).

## 3) اختبارات التكامل (Integration) — المحوّلات
عبر `testcontainers` بحاويات فعلية:
| المحوّر | ما يُختبر |
|---------|-----------|
| SQL Repositories | CRUD + قفل تفاؤلي (`version`) + الترقيم بالمؤشّر |
| **RLS** | ضبط `app.workspace_id` يعزل الصفوف؛ غياب السياق ⇒ صفر صفوف؛ محاولة عبور مستأجر ⇒ لا تسرّب |
| Outbox + Relay | كتابة ذرّية (أثر + صف) ثم نشر؛ لا نشر قبل الالتزام |
| **Idempotency** | إعادة تسليم نفس `event_id` ⇒ أثر واحد؛ `processed_events` يمنع التكرار |
| DLQ | فشل متكرّر ⇒ النقل بعد N=5 |
| VectorStore/Qdrant | upsert/search مع مرشّح `workspace_id` |
| Storage/MinIO | presigned put/get، عزل المفتاح بالبادئة |
| Auth/Firebase | تحقّق توكن (بمفاتيح اختبار)، رفض المنتهي |
| **usage (idempotency)** | إعادة `record()` بنفس `operation_id` ⇒ قيد واحد (لا عدّ مزدوج، `FR‑134`)؛ وتأكيد **عدم انبعاث أي حدث Streams** (`FR‑131`) |
| **usage (enforcement)** | `check()` يعيد **Decision** بسماح/رفض عند تجاوز الحصّة/الميزانية؛ حدود من `usage.limits` (`FR‑132`) |
| **integrations (secrets)** | تخزين رمز OAuth **مُعمّى عبر Transit** لا خاماً؛ لا يُعاد السرّ عبر API (`FR‑121`, `SEC‑07`) |
| **integrations (lazy refresh)** | رمز منتهٍ ⇒ تجديد تزامني عبر `refresh_token` قبل الاستخدام، بلا `scheduling` (`FR‑124`) |
| **integrations (tools)** | اكتشاف أدوات MCP بعيدة وقت التشغيل + استهلاكها عبر `ToolCatalog` دون اقتران مباشر (`FR‑52/122`)؛ رفض نقل غير http/sse |

> **اختبار العزل حرج:** لكل جدول مستأجَر (يشمل `integrations.*` و`usage.*`)، حالة تُثبت أن مستأجراً لا يرى صفوف آخر (RLS + الترشيح التطبيقي معاً).
> **مطابقة معايير القبول:** حالات هذه الوثيقة تُغطّي `AC‑05` (عزل المستأجر — القسم 3 · RLS)، `AC‑06` (دورة حياة الوكيل — القسم 2)، `AC‑08` (Idempotency الأحداث — القسمان 3 و5)، إضافةً إلى `AC‑14` (توسّع الوحدات)، `AC‑15` (integrations)، `AC‑16` (usage) من المتطلبات.

## 4) اختبارات المعمارية (Architecture) — الحدود
`tests/architecture/test_import_contracts.py` يشغّل import-linter برمجياً في CI:
```python
def test_import_contracts():
    result = subprocess.run(["lint-imports"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
```
العقود (في `.importlinter`) تفرض:
1. **الطبقات** (inward): `api → agents → modules → framework`.
2. **نقاء النطاق**: `modules.*.domain` لا يستورد framework/infra/تقنيات.
3. **حدود التطبيق**: `modules.*.application` بلا FastAPI/infra.
4. **استقلال الوحدات**: الوحدات **العشر** (بما فيها `integrations` و`usage`) لا تستورد بعضها؛ استدعاء `usage`/`integrations` عبر منافذها الواردة فقط.
5. **استقلال الوكلاء**: الوكلاء لا يستوردون بعضهم ولا API/infra.
6. **عزل البنية التحتية**: لا أحد يستورد `infrastructure` إلا Composition Root.
7. **نحافة الكِرنل**: `framework` لا يستورد الطبقات الخارجية.

## 5) اختبارات العقود (Contract)
- **منافذ ↔ محوّلات:** طقم اختبار مشترك (parametrized) يُشغَّل على كل محوّل مقابل عقد المنفذ ⇒ قابلية الاستبدال (Liskov).
- **OpenAPI:** تحقّق أن مخطط FastAPI المولّد يطابق `openapi.yaml` (لا انحراف صامت).
- **الأحداث:** كل حمولة حدث تُحقَّق مقابل JSON Schema في `events/schemas/` قبل النشر وفي الاختبار.
- **الإضافات (Plugins):** اختبار أن `PluginLoader` يكتشف وكيلاً بمجرد `manifest.py` + `BaseAgent`، ويعزل المعطوب دون إسقاط النواة.

## 6) بيانات وتهيئة الاختبار
- Fixtures: `execution_context(workspace_id, roles)` لضبط RLS في اختبارات التكامل.
- مصنع كيانات (builders) لكل Aggregate.
- عزل: كل اختبار تكامل في معاملة تُلغى، أو schema/DB مؤقتة عبر testcontainers.

## 7) البوّابات (CI Gates)
`ruff format --check` → `ruff check` → `mypy --strict` → `lint-imports` → `pytest`.
أي فشل ⇒ كسر البناء. تُشغَّل عقود import-linter مبكراً (سريعة) قبل اختبارات التكامل الأبطأ.

## 8) أهداف التغطية
> تُقاس عبر **`pytest-cov`** (`--cov` + `--cov-report`)، وتُفرض العتبات الرقمية أدناه في CI (مثل `--cov-fail-under`).

| الطبقة | الهدف |
|--------|-------|
| Domain + Application | ≥ 90% |
| Adapters (Infrastructure) | ≥ 75% عبر التكامل |
| API (routers/DTO) | مسارات سعيدة + كل رمز خطأ في الكتالوج |
| المعمارية | 100% من العقود خضراء (بوّابة صلبة) |
