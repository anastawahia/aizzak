# دليل تأليف الوحدات (Module Authoring Guide)

> نظير معماري لدليل تأليف الوكلاء ([`11-agent-authoring-guide.md`](11-agent-authoring-guide.md))، لكنه **مسار هندسي منظّم** لا «إسقاط مجلد» — يقنّن ضمانات `ARC‑03/07/08` القائمة (`FR‑110…112`).
> **مجموعة الوحدات مفتوحة للتوسّع:** تُضاف وحدة أعمال جديدة باتّباع القالب السداسي القياسي **دون تعديل كود أي وحدة قائمة**، والتواصل معها **حصراً** عبر Ports/Events.
> عقود النطاق/المنافذ المرجعية في [`06-domain-models.md`](06-domain-models.md) و[`02-port-contracts.md`](02-port-contracts.md). الوحدتان `integrations` و`usage` نموذجان مرجعيان حيّان لهذا المسار.

## 1) متى تُضاف وحدة جديدة؟
- قدرة أعمال **جديدة مغلقة الحدود** لها حالة/بيانات خاصّة (schema)، لا تخصّ وحدة قائمة.
- إن كانت القدرة مجرّد أداة فوق منافذ موجودة ⇒ **أداة (Tool)** لا وحدة. إن كانت وكيلاً منسّقاً ⇒ **Plugin وكيل** لا وحدة.
- الوحدات المرشّحة المحجوزة مسبقاً (schema + مقعد): `scheduling · sandbox · runs` (القسم 12.1 من المتطلبات).

## 2) تشريح مجلد الوحدة (القالب السداسي القياسي)
```
src/app/modules/<your_module>/
├── __init__.py
├── domain/            # نقي: Entities · Value Objects · Domain Events · Invariants — لا FastAPI/DB/infra (ARC‑04)
│   ├── models.py
│   └── events.py
├── application/       # Use‑Cases تنسّق النطاق عبر منافذ محقونة — بلا تقنية (ARC‑09)
│   └── use_cases.py
├── ports/             # عقود الوحدة
│   ├── repository.py  # Outbound: تجريد الاستمرار
│   └── inbound.py     # Inbound: ما تستدعيه الوكلاء/الوحدات الأخرى (بدل الاستيراد المباشر)
└── adapters/          # تنفيذ المنافذ (SQL/خارجي) — المكان الوحيد لاستيراد مكتبات التقنية
    └── sql_repository.py
```
- **Schema خاص** بالوحدة في PostgreSQL يملك جداولها (`DAT‑01`)؛ لا FK عابر لأي schema وحدة أخرى — المرجعية بالـ`id` فقط (`DAT‑02`).
- الأعمدة القياسية (`DAT‑03…07`): `id` (UUIDv7)، `workspace_id` للجداول المستأجَرة، `created_at/updated_at`، و`deleted_at`/`version` حيث تنطبق؛ الجداول السجلّية append‑only بلا `deleted_at`/`version`.

## 3) قواعد الحدود (إلزامية)
- **لا تستدعي وحدة أخرى مباشرة** (`ARC‑07`): لا استيراد لنطاقها ولا جداولها ولا مستودعاتها.
- التواصل **حصراً** عبر (`ARC‑08`):
  - **Inbound Port محقون** للاستدعاء المتزامن (نظير `usage.UsageEnforcement` / `integrations.ToolCatalog`).
  - **Global Event** عبر Redis Streams **للعمليات الثقيلة فقط** (`EVT‑10`) — عبر Outbox، لا نشر مباشر.
- **RLS + ترشيح تطبيقي** على كل جدول مستأجَر (`SEC‑03`): سياسة `workspace_id` + `WHERE workspace_id` في كل استعلام.
- الأسرار (إن وُجدت) عبر `SecretsProvider`/Vault Transit فقط، لا نصّ صريح (`SEC‑05/07`).

## 4) نقاط الربط الثلاث فقط (`FR‑111`)
ربط الوحدة الجديدة ينحصر في ثلاث نقاط محدّدة **دون مساس بالوحدات القائمة**:

1. **Composition Root** (`framework/di/composition_root.py`) — **حقن يدوي** (`ARC‑11`): بناء المستودعات/المحوّلات وربطها بمنافذ الوحدة، وتزويد المُنسِّق/الوكلاء بمنافذها الواردة.
2. **عقد `import‑linter`** (`.importlinter`) — إدخال الوحدة في عقد **استقلالية الوحدات** (`ARC‑10`) بحيث لا تستوردها/تستورد غيرها إلا عبر منافذ.
3. **سلسلة هجرات Alembic خاصّة** (`DAT‑03`) ضمن `version_table_schema` منفصل — تُنشئ schema الوحدة وجداولها وسياسات RLS، وتُدار مركزياً بأمر واحد.

> ما عدا هذه النقاط الثلاث: **لا تُعدَّل** أي وحدة قائمة. إضافة صفّ إلى جدول توجيه/كتالوج ثابت (عند اللزوم) تُعامَل كإعداد لا كتعديل منطق.

## 5) مثال مرجعي — كيف طُبِّق القالب على `usage`
- **domain:** `UsageRecord` (append‑only)، `UsageLimit`، `Decision` (كائن قرار لا `bool`)، ثوابت `INV‑U1…U6`.
- **ports/inbound:** `UsageEnforcement.check(...) → Decision` (قبل العملية) و`UsageCapture.record(charge)` (بعد العملية، idempotent) — **متزامنان بلا Streams** (`FR‑131/132`).
- **ports/repository:** `UsageLedgerRepository` (append + rollup + limits).
- **adapters:** مستودع SQL فوق schema `usage` (سجلّ + تجميع + حدود) مع `UNIQUE(workspace_id, operation_id)` للإدمبوتنسي (`FR‑134`).
- **الربط:** حقن في Composition Root ⇒ يستدعيه **المُنسِّق (طبقة الوكلاء) حصراً**؛ عقد `import‑linter` يمنع أي وحدة من استيراده؛ سلسلة هجرات `usage` مستقلّة.

## 6) مثال مرجعي — `integrations`
- **domain:** `Connection` (OAuth، سرّ مُعمّى)، `McpServer` (نقل بعيد http/sse حصراً)، ثوابت `INV‑I1…I5`.
- **ports/inbound:** `ToolCatalog` (كتالوج ديناميكي لكل Workspace + استدعاء أداة) يستهلكه نظام الأدوات (`FR‑52/122`).
- **ports (driven، في framework):** `ConnectorProvider` (OAuth + تجديد كسول)، `MCPClient` (اكتشاف/استدعاء بعيد).
- **الربط:** لا اقتران مباشر بموصّل؛ الأسرار عبر Vault Transit بمفتاح موحّد (`SEC‑07`).

## 7) الاختبار (مطابقة `AC‑14`)
- **وحدة:** النطاق النقي (ثوابت/انتقالات) + Use‑Cases بمنافذ مزيّفة.
- **تكامل:** المستودع عبر `testcontainers` + **عزل RLS** (مستأجر لا يرى صفوف آخر).
- **معمارية:** `import‑linter` أخضر — الوحدة معزولة ولا تُستورَد إلا عبر منافذ.
- **دليل + مثال مرجعي:** إثبات موثّق لإضافة وحدة عبر القالب دون تعديل الوحدات القائمة (`AC‑14`).

## 8) قائمة تحقّق قبل الدمج
- [ ] القالب السداسي كامل: `domain/ application/ ports/ adapters/` + **schema خاص**.
- [ ] **نقاء النطاق** — بلا FastAPI/SQLAlchemy/infra (`ARC‑04`)، يمرّ `import‑linter`.
- [ ] **لا استدعاء مباشر لوحدة أخرى**؛ فقط Inbound Ports/Events (`ARC‑07/08`).
- [ ] **لا FK عابر** بين schemas الوحدات؛ المرجعية بالـ`id` (`DAT‑02`).
- [ ] نقاط الربط الثلاث فقط: Composition Root + `.importlinter` + سلسلة Alembic (`FR‑111`).
- [ ] RLS + ترشيح تطبيقي على كل جدول مستأجَر؛ الأسرار عبر Vault Transit.
- [ ] العمليات الثقيلة (إن وُجدت) عبر Streams/Outbox لا في مسار الطلب.
- [ ] اختبارات وحدة + تكامل (RLS) + معمارية خضراء.
