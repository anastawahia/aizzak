# استراتيجية الاختبار + عقود import-linter

> الأدوات (`DD‑14`): `pytest` + `pytest-asyncio` + `pytest-cov` · `import-linter` (D‑17) · `mypy --strict` · `Ruff`.
> عقود الاعتماد التنفيذية في [`../../.importlinter`](../../.importlinter) (الجذر).
>
> ✅ **الواقع الحالي (2026‑08‑05).** لا تستعمل الحزمة `testcontainers`: PostgreSQL 16 وRedis وMinIO وQdrant وVault وخدمة التضمين تأتي من مكدّس Compose نفسه، بينما يبقى Ollama خدمة systemd أصليّة داخل WSL. ملف `docker-compose.test.yml` الاختياريّ ينشر التضمين ويُحضِر سكربت قاعدة الاختبار، و`.env.test` يوجّه المحضن إلى منافذ المضيف المزاحة. وسوم `pytest.mark.live_*` تتخطّى افتراضاً عند غياب اعتماد محلّي؛ أمّا التشغيل الذي يعلن `REQUIRE_LIVE=1` فيحوّل الغياب إلى فشلٍ صريح. `testcontainers>=4.8` حُذفت سلفاً لأنّ المستودع لا يستوردها.

> **ملاحظة نطاق:** `Requirements-v1.md` (`ARC‑14`/`AC‑09`) و`implementation-plan.md`/`00-detailed-design-decisions.md`/`12-module-authoring-guide.md`/`ROADMAP.md` ما زالت تسمّي `testcontainers` ضمن أدوات `DD‑14` — لم تُلمَس في هذه الخطوة (نطاقها هذه الوثيقة وحدها)؛ مسجَّلةٌ في `p1-hardening-plan.md §5‑ب` لمن يتولّى تصفيتها لاحقاً.

## 1) هرم الاختبار
```
        ┌───────────────┐
        │  E2E / smoke  │  قليلة — تدفّق كامل عبر HTTP + بنية حقيقية
        ├───────────────┤
        │  Integration  │  محوّلات فعلية عبر Compose + Ollama أصلي داخل WSL (وسوم `live_*`)
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
عبر البنية المحليّة الحقيقية: PostgreSQL 16 · Redis · MinIO · Qdrant · Vault · التضمين من Compose، وOllama أصلي داخل WSL. يوجّه `docker-compose.test.yml` + `.env.test` المحضن إلى هذه الخدمات بوسوم `pytest.mark.live_db`/`live_redis`/`live_minio`/`live_qdrant`/`live_vault`/`live_ollama`/`live_embedding`. كل وسم يسبر عنوانه؛ التشغيل العادي يتخطّى حين يتعذّر، والتشغيل الصارم `REQUIRE_LIVE=1` يفشل كي لا تخضرّ حزمةٌ وعدت ببنيةٍ مزوّدة (`tests/integration/conftest.py`؛ أوصاف `markers` في `pyproject.toml`). ما يُختبَر:
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
>
> ⚠️ **حقيقة MinIO ذات الأطراف الثلاثة.** التزويد يقرأ `MINIO_TEST_SECRET_KEY`، والمحضن يقرأ `TEST_MINIO_SECRET_KEY`، و`conftest.py::_MINIO_SECRET_DEFAULT` يبقى `aizzak-test-secret`. الحارس يرفض اختلاف الاسمين حين يكونان مضبوطين معاً، لكنه لا يربطهما بنيوياً. بعد تدوير الحساب، تشغيلٌ بلا `.env.test` يستعمل القيمة النائبة ويصل MinIO ثم يفشل `SignatureDoesNotMatch` بصوتٍ عالٍ؛ لا يتحوّل إلى تخطٍّ.

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
- عزل: قاعدة `aizzak_test` الدائمة في PostgreSQL الحاوي، لا schema/DB مؤقّتة لكلّ اختبار — العزل عبر معرّفاتٍ/بادئاتٍ فريدة (‏`UUIDv7`) تُكنَس صراحةً في كلّ اختبار، ومعاملاتٌ تُلغى حيث يلائم. تُنشأ الأدوار الستّة عند تهيئة حجم Postgres، ويعيد المحضن الهجرات والمنح لكلّ جلسة (‏01-data-model §6: منح الأدوار خطوةُ نشرٍ لا DDL).

## 7) البوّابات (CI Gates)
`quality` يشغّل `ruff format --check` → `ruff check` → `mypy --strict` → `lint-imports` → `pytest -rs`؛ البنية المحليّة غائبة على العدّاء فتظهر تخطّياتها بأسبابها. بعده تشغّل وظيفة `integration` ملف التجاوز وسكربتات التزويد نفسها، ثم تختبر الاعتمادات المحلية المختارة تحت `REQUIRE_LIVE=1`. Ollama/التضمين مستبعدان من الوظيفة البعيدة لحجم النموذج/الصورة، وOpenAI/Exa لاحتياجهما مفاتيح مدفوعة. أي فشل ⇒ كسر البناء.

## 8) أهداف التغطية
> تُقاس عبر **`pytest-cov`** (`--cov` + `--cov-report`)، وتُفرض العتبات الرقمية أدناه في CI (مثل `--cov-fail-under`).

| الطبقة | الهدف |
|--------|-------|
| Domain + Application | ≥ 90% |
| Adapters (Infrastructure) | ≥ 75% عبر التكامل |
| API (routers/DTO) | مسارات سعيدة + كل رمز خطأ في الكتالوج |
| المعمارية | 100% من العقود خضراء (بوّابة صلبة) |
