# التصميم التفصيلي (Detailed Design)

> يترجم المعمارية المعتمدة ([`../architecture.md`](../architecture.md) — 15 مرحلة · 26 قراراً) إلى **عقود بناء**.
> نمط الحسم: «احسم القياسي بنفسك، وقف عند عالي الأثر» — القرارات القياسية محسومة، وعالية الأثر مُعلَّمة ⚠️.

## المخرجات
| # | الوثيقة | المحتوى |
|---|---------|---------|
| 00 | [قرارات التصميم التفصيلي](00-detailed-design-decisions.md) | سجلّ `DD‑01…DD‑14` + التفسيرات + مصفوفة التتبّع |
| 01 | [نموذج البيانات](01-data-model.md) | ERD لكل وحدة · schema‑per‑module · RLS · Outbox · Idempotency |
| 02 | [عقود المنافذ](02-port-contracts.md) | المنافذ المُقادة (+ ConnectorProvider/MCPClient) + منافذ الوحدات (usage/integrations الواردة) + عقود الإطار |
| 03 | [مواصفة API](03-api-spec.md) + [`openapi.yaml`](openapi.yaml) | المسارات · DTOs · WebSocket/SSE · نموذج أخطاء RFC 9457 |
| 04 | [كتالوج الأحداث](04-event-catalog.md) + [`events/schemas/`](events/schemas/) | CloudEvents · التسمية/الإصدار · Idempotency |
| 05 | [RBAC + الإعداد/الأسرار](05-rbac-config-secrets.md) | ✅ مصفوفة RBAC · كتالوج الإعدادات · كتالوج أسرار Vault |
| 06 | [نماذج النطاق](06-domain-models.md) | DDD لكل وحدة: Aggregates · VOs · Events · Invariants |
| 07 | [NFR/SLO](07-nfr-slo.md) | ✅ الجاهزية · زمن الاستجابة · السعة · الحدود الرقمية |
| 08 | [دليل التشغيل المحلي](08-local-runbook.md) | Docker Compose · الهجرات · العمّال · التحقّق |
| 09 | [استراتيجية الاختبار](09-testing-strategy.md) + [`.importlinter`](../../.importlinter) | الهرم · العزل/RLS · عقود الاعتماد |
| 10 | [معايير الكود](10-code-standards.md) | الأدوات · الطبقات · DI · الأخطاء · التسجيل |
| 11 | [دليل تأليف الوكلاء](11-agent-authoring-guide.md) | Plugin: manifest · BaseAgent · Tools · دورة الحياة |
| 12 | [دليل تأليف الوحدات](12-module-authoring-guide.md) | القالب السداسي · نقاط الربط الثلاث · مثال مرجعي |

> **نطاق v1 — عشر وحدات:** `workspace · access · credentials · conversations · memory · files · knowledge · media · integrations · usage` (+ schema بنيوي `platform`)؛ المحجوز للتوسّع **3** فقط (`scheduling · sandbox · runs`). المجموعة مفتوحة عبر القالب السداسي القياسي (`FR‑110…112` → المخرَج 12).

> **ملاحظة تسمية (مواءمة ملحق ج بالمتطلبات):** أسماء الملفات الفعلية هنا أوصف من الأسماء المختصرة في ملحق ج بـ`Requirements-v1.md`؛ المقابلة واحدة‑لواحد ولا فرق في المحتوى:
> - `02-port-contracts.md` ↔ `02-ports`
> - `04-event-catalog.md` ↔ `04-events`
> - `08-local-runbook.md` ↔ `08-ops-guide`
> - `09-testing-strategy.md` ↔ `09-testing`

## ✅ قرارات محسومة (توقيع العميل 2026‑07‑10)
كانت هذه البنود عالية الأثر نقاطَ مراجعة مفتوحة، وحُسمت باعتماد `OQ‑01/OQ‑02/OQ‑03` (§13 من [`../Requirements-v1.md`](../Requirements-v1.md)):
1. **`DD‑10`** طقم أدوار RBAC وتوزيع الصلاحيات — `OQ‑01` → [05](05-rbac-config-secrets.md).
2. **`DD‑12`** أرقام SLO والحدود الرقمية — `OQ‑02` → [07](07-nfr-slo.md).
3. **`I‑1`** تفسير عضوية الـWorkspace (شخصي واحد لكل مستخدم) — `OQ‑03` → [00](00-detailed-design-decisions.md).

> بقيّة القرارات (`DD‑01…09, 11, 13, 14`) محسومة قياسياً وموثّقة في [00](00-detailed-design-decisions.md).

## الأساس المُثبَّت
Schema‑per‑module (×10) · UUIDv7 · RFC 9457 · CloudEvents 1.0 · CQRS‑lite عبر Ports · Manual DI · RLS + ترشيح تطبيقي · Transactional Outbox + Idempotency + DLQ · قياس `usage` بمنفذ وارد متزامن خارج الناقل · أسرار موحّدة عبر Vault Transit (SEC‑07).
