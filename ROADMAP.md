<div dir="rtl">

# خارطة طريق المشروع — منصة ذكاء اصطناعي متعددة الوكلاء (Backend)

> **Project Roadmap · v1.0**
>
> مسار تنفيذ متسلسل من التصميم إلى القبول النهائي. الترتيب يحترم **قواعد الاعتماد** (`ARC‑09`):
> نبني من الداخل (Domain) نحو الخارج (Infrastructure / API).

| | |
|---|---|
| **المصدر** | [`Requirements-v1.md`](Requirements-v1.md) · [`architecture.md`](architecture.md) · [`design/`](design/) |
| **الحالة العامة** | 🟢 التصميم مكتمل — بدء التنفيذ |
| **آخر تحديث** | 2026‑07‑10 |

---

## مفتاح الحالة

| الرمز | المعنى |
|-------|--------|
| ✅ | **مكتملة** |
| 🔵 | قيد التنفيذ |
| ⬜ | لم تبدأ |

---

## نظرة عامة على المراحل

| # | المرحلة | الحالة |
|---|---------|--------|
| 1 | كتابة المتطلبات | ✅ مكتملة |
| 2 | بناء الهيكل المعماري (HLD) | ✅ مكتملة |
| 3 | مخططات LLD وكتابة العقود | ✅ مكتملة |
| 4 | هيكل مشروع فارغ (Skeleton) | ✅ مكتملة |
| 5 | الأساس والركائز المشتركة (Foundation) | ⬜ |
| 6 | المنافذ والمحوّلات التحتية (Ports & Adapters) | ⬜ |
| 7 | وحدات الأعمال العشر (Business Modules) | ⬜ |
| 8 | الإطار والوكلاء (Framework & Agents) | ⬜ |
| 9 | المعالجة الخلفية والبثّ (Events & Streaming) | ⬜ |
| 10 | طبقة الـAPI | ⬜ |
| 11 | النشر والتشغيل (Deployment) | ⬜ |
| 12 | القبول النهائي (Definition of Done) | ⬜ |

---

## المرحلة 1: كتابة المتطلبات ✅

**المخرَج:** [`docs/Requirements-v1.md`](Requirements-v1.md)

- وثيقة متطلبات كاملة (وظيفية `FR` · غير وظيفية `NFR` · أمنية `SEC` · بيانات `DAT` · أحداث `EVT` · تشغيل `OPS`).
- قيود معمارية إلزامية محسومة مسبقاً (`ARC‑01…15`).
- حزمة تقنيات إلزامية + معايير قبول (`AC‑01…16`).
- **نقاط القرار المفتوحة `OQ‑01…06` محسومة** (توقيع العميل 2026‑07‑10).

---

## المرحلة 2: بناء الهيكل المعماري (HLD) ✅

**المخرَج:** [`docs/architecture.md`](architecture.md)

- مخططات C4 (سياق النظام L1 · الحاويات L2).
- البنية الطبقية الخمسية + وحدات الأعمال العشر.
- سجل القرارات المعمارية **26 ADR** (`D‑01…D‑26`).
- معمارية الأمن · العمارة المدفوعة بالأحداث · معمارية النشر.

---

## المرحلة 3: مخططات LLD وكتابة العقود ✅

**المخرَج:** [`docs/design/`](design/)

- نموذج البيانات + هجرات لكل وحدة ([`01-data-model.md`](design/01-data-model.md)).
- عقود المنافذ المحايدة للمزوّد ([`02-port-contracts.md`](design/02-port-contracts.md)).
- مواصفة الـAPI (OpenAPI) ([`03-api-spec.md`](design/03-api-spec.md) · [`openapi.yaml`](design/openapi.yaml)).
- كتالوج الأحداث + JSON Schemas ([`04-event-catalog.md`](design/04-event-catalog.md) · [`events/`](design/events/)).
- RBAC + الإعداد/الأسرار · نماذج النطاق · NFR/SLO · دليل التشغيل · استراتيجية الاختبار · معايير الكود.
- دليلا تأليف الوكلاء والوحدات.

---

## المرحلة 4: هيكل مشروع فارغ (Skeleton) ✅

**المخرَج:** هيكل مجلدات المشروع مطابقاً للطبقات الخمس.

- شجرة المجلدات: `framework · api · agents · modules · infrastructure`.
- ملفات `__init__` وحدود الوحدات الأولية.
- تهيئة أدوات المطوّر (`pyproject` · `ruff` · `mypy` · `pytest`).

---

## المرحلة 5: الأساس والركائز المشتركة (Foundation) ⬜

> تُبنى أولاً لأن كل وحدة تعتمد عليها. **ابدأ بـ import-linter و CI قبل أي كود وحدة.**

- [ ] **Settings Service** مركزي + عقد `Settings` (Pydantic v2) — `ARC‑12`.
- [ ] **Composition Root** + Manual DI — `ARC‑11`.
- [ ] إعداد **import-linter** في CI مبكراً — `ARC‑10` · `D‑17`.
- [ ] الأعمدة القياسية + مولّد **UUIDv7** (في التطبيق) — `DAT‑04/05`.
- [ ] Base classes للـ Domain / Repository Pattern — `DAT‑08`.
- [ ] schema بنيوي `platform` (`outbox` · `processed_events` · `stream_offsets`).
- [ ] تفعيل **Ruff · mypy --strict · pytest + pytest-asyncio + testcontainers**.

**معيار الإنجاز:** `AC‑01` (import-linter يمرّ) · `AC‑09` (جودة الكود).

---

## المرحلة 6: المنافذ والمحوّلات التحتية (Ports & Adapters) ⬜

> تعريف المنافذ في **Framework** وتنفيذ محوّلاتها في **Infrastructure**.

- [ ] المنافذ المحايدة للمزوّد: `LLMProvider · EmbeddingProvider · VectorStore · StorageProvider · CacheProvider` — `FR‑70…73` · `POR‑01`.
- [ ] منافذ البنية التحتية: `SecretsProvider · AuthProvider · ConnectorProvider / MCPClient`.
- [ ] محوّلات: Postgres (SQLAlchemy 2.0 async) · Redis · MinIO · **Qdrant** · **Vault (Transit)** · Firebase.
- [ ] محوّلات المزوّدين الأصلية: OpenAI · Gemini · Claude · Ollama · OpenRouter — `D‑15`.
- [ ] `ProviderResolver` من الإعداد لا من الكود — `FR‑73` · `D‑16`.
- [ ] ضبط **PgBouncer + asyncpg** (`statement_cache_size=0`) — `OPS‑02`.

**معيار الإنجاز:** `AC‑10` (الأسرار عبر Vault).

---

## المرحلة 7: وحدات الأعمال العشر (Business Modules) ⬜

> كل وحدة كاملة (Domain → Application → Ports → Adapters + هجرة Alembic + RLS). الترتيب حسب الاعتماد.

**المجموعة أ — التمكين والأمان:**
- [ ] `workspace` (المستأجر / حدّ العزل) — `FR‑01…03`.
- [ ] `access` (RBAC) — `SEC‑02` · `D‑24`.
- [ ] `credentials` (مفاتيح LLM · Vault Transit) — `FR‑75`.

**المجموعة ب — التفاعل والمحتوى:**
- [ ] `conversations` · `memory` — `FR‑80/81`.
- [ ] `files` (مشتركة · MinIO) — `FR‑60…62`.

**المجموعة ج — المعرفة والتوليد:**
- [ ] `knowledge` (RAG · فهرسة + استرجاع).
- [ ] `media` (صور / فيديو).

**المجموعة د — التكامل والقياس:**
- [ ] `integrations` (موصّلات · OAuth · MCP بعيد · تجديد كسول) — `FR‑120…124`.
- [ ] `usage` (منافذ واردة · كائن قرار · التقاط إدمپوتنسي) — `FR‑130…134`.

**معيار الإنجاز:** `AC‑03` (عزل الوحدات) · `AC‑05` (عزل المستأجر) · `AC‑14` (توسّع الوحدات) · `AC‑15` · `AC‑16`.

---

## المرحلة 8: الإطار والوكلاء (Framework & Agents) ⬜

- [ ] **PluginLoader + AgentRegistry** (اكتشاف آلي عبر importlib) — `FR‑31` · `D‑13`.
- [ ] **BaseAgent** + دورة الحياة الخمسية (Created→…→Disposed) — `FR‑15`.
- [ ] الوكيل **عديم الحالة** يُنشأ لكل طلب ويُتلَف — `FR‑10/11`.
- [ ] **Tool System** + `ToolRegistry` + الكتالوج الديناميكي لكل Workspace — `FR‑50…52`.
- [ ] **WorkflowEngine + WorkflowRegistry** (قابل للتوسعة) — `FR‑40/41`.
- [ ] الوكلاء الخمسة المُسبقون: RAG · Data Analysis · Image · Video · File Editing — `FR‑20`.

**معيار الإنجاز:** `AC‑04` (إثبات Plugin) · `AC‑06` (دورة الحياة) · `AC‑12` (Workflow).

---

## المرحلة 9: المعالجة الخلفية والبثّ (Events & Streaming) ⬜

- [ ] **Transactional Outbox + Relay** → Redis Streams → Workers (Consumer Groups) — `EVT‑10…13` · `D‑18/20`.
- [ ] **Idempotency** الفعّالة (`processed_events` داخل نفس المعاملة) — `EVT‑05`.
- [ ] **DLQ** بعد عتبة الفشل `N` — `NFR‑04`.
- [ ] **WebSocket** تفاعلي + **Streaming Responses** (SSE للردّ الواحد) — `FR‑90/91` · `D‑10`.

**معيار الإنجاز:** `AC‑08` (عقد الأحداث + Idempotency) · `AC‑11` (البثّ).

---

## المرحلة 10: طبقة الـAPI ⬜

- [ ] Routers تحت `/api/v1` (بلا منطق أعمال — تفويض إلى Agents/Application) — `FR‑100`.
- [ ] نموذج أخطاء **RFC 9457 Problem Details** — `API‑01`.
- [ ] ترقيم بالمؤشّر (cursor) · تسمية `snake_case` · غلاف المجموعات — `API‑02…05`.
- [ ] تحقق **Firebase JWT محلي** + **RBAC guards** — `SEC‑01` · `D‑25`.

**معيار الإنجاز:** `AC‑07` (مطابقة عقد الـAPI).

---

## المرحلة 11: النشر والتشغيل (Deployment) ⬜

- [ ] **Docker Compose** — `D‑26`.
- [ ] طوبولوجيا: Nginx → Gunicorn+Uvicorn (نسخ عديمة الحالة) → خدمات البيانات — `OPS‑01…05`.
- [ ] عمّال ومُرحّل Outbox عمليات مستقلة قابلة للتوسّع الأفقي.
- [ ] دليل التشغيل المحلي — [`08-local-runbook.md`](design/08-local-runbook.md).

---

## المرحلة 12: القبول النهائي (Definition of Done) ⬜

> يُقبل التسليم فقط عند تحقّق **كل** معايير `AC‑01…16`، وإخفاق أيٍّ منها سبب للرفض.

- [ ] `AC‑01/02` مطابقة الطبقات ونقاء النطاق.
- [ ] `AC‑03/05` عزل الوحدات وعزل المستأجر (إثبات حيّ).
- [ ] `AC‑04` إثبات Plugin حيّ (إضافة/حذف وكيل دون تعديل النواة).
- [ ] `AC‑06…08` دورة الحياة · API · الأحداث.
- [ ] `AC‑09/10` جودة الكود والأسرار.
- [ ] `AC‑11/12` البثّ و Workflow.
- [ ] `AC‑13` حسم `OQ‑01…06` مطبّق.
- [ ] `AC‑14…16` توسّع الوحدات · `integrations` · `usage`.
- [ ] تسليم **مخرجات المورّد** الثلاثة عشر — `OPS‑10`.

---

<div align="center">

**خارطة طريق منصة الوكلاء الذكية · v1.0**

`4 / 12 مرحلة مكتملة`

</div>

</div>
