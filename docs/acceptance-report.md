<div dir="rtl">

# تقرير القبول النهائي — منصة الوكلاء الذكية (`AIZZAK`)

> **Acceptance Report · Definition of Done · المرحلة 8**
>
> يُقبل التسليم فقط عند تحقّق **كل** معايير `AC‑01…16` (`Requirements‑v1.md §15`). هذا التقرير يربط كل معيار بدليلٍ ملموسٍ **قابلٍ لإعادة التشغيل** (اختبار مسمّى أو بوابةٌ خضراء)، لا بادّعاء.

| | |
|---|---|
| **التاريخ** | 2026‑07‑25 |
| **المصدر الملزِم** | [`Requirements-v1.md §14,§15`](Requirements-v1.md) · [`implementation-plan.md` المرحلة 8](implementation-plan.md) · [`09-testing-strategy.md`](design/09-testing-strategy.md) |
| **الحالة** | ✅ **16/16 معيار قبول مُحقَّق** — بفجواتٍ مُعلَنة بصدق خارج نطاق `AC` (§6) |
| **البوابات الخمس** | ruff format **471** · ruff check نظيف · mypy‑strict **334** · import‑linter **8/0** · pytest **2178 ناجح / 0 فشل / 0 خطأ / 10 متخطًّى** |

---

## 0. الخلاصة التنفيذية (Verdict)

كل معايير القبول الستة عشر مُحقَّقة ومُثبَتة بدليلٍ قابلٍ للتشغيل. البوابات الخمس خضراء في تشغيلٍ واحد (2026‑07‑25). التخطّيات العشرة كلّها **مُبرَّرة ومُوثَّقة** (مفاتيح سحابيّة غير متوفّرة أو خدمة تضمين حاويّة غير مُقلَعة أو أثر `PATH`) ولا يمسّ أيٌّ منها معياراً من `AC‑01…16` — التفصيل في §6.

| النطاق | النتيجة |
|---|---|
| مطابقة الطبقات والنطاق (`AC‑01/02`) | ✅ import‑linter 8/0 · mypy‑strict نظيف |
| عزل الوحدات والمستأجر (`AC‑03/05`) | ✅ لا FK عابر schema + import‑linter · **121 اختبار عزل RLS حيّ** |
| الـPlugin ودورة الحياة (`AC‑04/06`) | ✅ اكتشاف/تسجيل + الحالات الخمس والتخلّص دائماً |
| عقدا الـAPI والأحداث (`AC‑07/08`) | ✅ مطابقة `openapi.yaml` ثنائيّة الاتّجاه · مظاريف CloudEvents + مثاليّة |
| الأسرار والبثّ والـWorkflow (`AC‑10/11/12`) | ✅ Vault Transit · SSE+WS · سلسلة متعدّدة الوكلاء |
| حسم القرارات والتوسّع (`AC‑13/14`) | ✅ `OQ‑01…06` مُوقَّعة · دليلا تأليف + مثالان مرجعيّان |
| `integrations` و`usage` (`AC‑15/16`) | ✅ OAuth+Transit+تجديد كسول · فرض حصّة + التقاط إدمبوتنسي |

---

## 1. بوابات الجودة الخمس (الخطوة 8.1)

أُعيد تشغيلها كلّها في venv الأصليّ داخل WSL (Ubuntu‑24.04، مطابق CI‑Linux) في 2026‑07‑25:

| البوابة | الأمر | النتيجة |
|---|---|---|
| **تنسيق Ruff** | `ruff format --check .` | ✅ **471 ملفاً منسّقاً** |
| **فحص Ruff** | `ruff check .` | ✅ `All checks passed!` |
| **الأنواع** | `mypy src` (‏`--strict`) | ✅ `no issues found in 334 source files` |
| **الطبقات** | `lint-imports` | ✅ **8 عقود مُبقاة · 0 مكسور** |
| **الاختبارات** | `pytest -m "not integration"` | ✅ **2188 حالة: 2178 ناجحة · 0 فشل · 0 خطأ · 10 متخطّاة** (‏142.5s) |

> إعادة التحقّق بأمرٍ واحد:
> ```bash
> wsl -d Ubuntu-24.04 -- bash -lc 'cd /home/AIZZAK && \
>   .venv/bin/ruff format --check . && .venv/bin/ruff check . && \
>   .venv/bin/mypy src && .venv/bin/lint-imports && \
>   .venv/bin/pytest -m "not integration"'
> ```

عقود import‑linter الثمانية المُبقاة (تُثبت `AC‑01/02/03`):

1. `Application depends on domain + framework only` — KEPT
2. `Business modules do not import each other` — KEPT *(‏`AC‑03` كوداً)*
3. `Agents do not import each other` — KEPT
4. `Agents never import API or Infrastructure` — KEPT
5. `Infrastructure imported only by Composition Root` — KEPT
6. `Framework does not import outer layers` — KEPT
7. + عقدان مساعدان لنقاء النطاق *(`AC‑02`)*

---

## 2. مصفوفة معايير القبول `AC‑01…16` (الخطوة 8.1)

| المعيار | البند | الحالة | الدليل الملموس (قابل لإعادة التشغيل) |
|---|---|:--:|---|
| **AC‑01** | مطابقة الطبقات (import‑linter) | ✅ | `lint-imports` → **8/0**؛ عقد `framework-kernel` مُبقًى مع `PluginLoader` عاملاً. |
| **AC‑02** | نقاء النطاق (لا FastAPI/DB/Framework في Domain) | ✅ | عقدا import‑linter «Framework does not import outer layers» + «Application depends on domain + framework only»؛ `mypy --strict` نظيف (334 ملفاً). |
| **AC‑03** | عزل الوحدات (لا `A→B`، لا FK عابر schema) | ✅ | كوداً: عقد «Business modules do not import each other» KEPT · قاعدةً: مسح الهجرات ⇒ **كل FK داخل schema واحدة** (‏`conversations→conversations` · `users→workspace.workspaces` · `chunk→knowledge.documents`)، صفر FK عابر. |
| **AC‑04** | إثبات الـPlugin (إضافة/حذف بلا مساس النواة) | ✅ | `test_agent_runtime.py::test_discovers_and_registers_every_valid_agent` (اكتشاف من القرص) · `::test_register_refuses_a_duplicate_key_and_keeps_the_first` · `::test_real_app_agents_tree_boots_with_all_five_agents_registered` · حيّ: `test_orchestrator_live.py::test_an_unknown_agent_raises_404_from_the_wired_orchestrator`. |
| **AC‑05** | عزل المستأجر (RLS + ترشيح تطبيقيّ) | ✅ | **121 حالة عزل حيّة على Postgres**؛ منها `test_user_tenant_isolation_raw_select_cross_tenant_yields_zero_rows` · `test_forged_cross_tenant_*_is_rejected_by_rls_with_check` عبر الوحدات العشر كلّها. |
| **AC‑06** | دورة حياة الوكيل (خمس حالات + فشل + عديم حالة) | ✅ | `test_agent_lifecycle.py`: `test_success_path_streams_events_then_completes_and_disposes` · `test_initialize_failure_yields_terminal_error_and_disposes` · `test_dispose_runs_exactly_once_on_every_path` · `test_cancellation_propagates_and_still_disposes` · `test_one_executor_drives_concurrent_requests_without_cross_talk`. |
| **AC‑07** | مطابقة عقد الـAPI (RFC 9457 · مؤشّر · snake_case · `/api/v1`) | ✅ | `test_api_conventions.py` (14 اختباراً) يقارن المستند المولَّد بـ`openapi.yaml` **ثنائيّ الاتّجاه** · `test_error_catalog.py` (كتالوج 34 مدخلاً بمسح AST) · `test_api_errors.py` · `test_sse.py`. |
| **AC‑08** | مطابقة عقد الأحداث (CloudEvents · تسمية · Schemas · إدمبوتنسي) | ✅ | `test_event_envelope.py::test_envelope_validates_against_the_published_schema` · `::test_event_type_pattern_has_not_drifted_from_the_published_schema` · حيّ: `test_processed_events_live.py::test_a_double_published_event_registers_exactly_one_document` · 9 مخطّطات في [`design/events/schemas/`](design/events/schemas/). |
| **AC‑09** | جودة الكود (Ruff · mypy‑strict · pytest خضراء) | ✅ | البوابات الخمس §1 كلّها خضراء. |
| **AC‑10** | الأسرار (Vault · Transit للمفاتيح) | ✅ | حيّ: `test_vault_secrets.py::test_ciphertext_has_the_vault_transit_wire_shape` · `::test_encrypt_with_one_key_cannot_decrypt_with_another` · `::test_encrypt_with_a_path_traversal_key_name_is_rejected_before_any_network_call`؛ لا سرّ في الكود (‏`.env` مثالٌ فقط، الأسرار عبر Vault/AppRole §3.76). |
| **AC‑11** | البثّ (WebSocket + SSE) | ✅ | `test_sse.py` (13) · `test_websocket_endpoint.py` (16) · `test_streaming_hub.py`؛ حيّ من الحافّة §3.76 (‏wss 101⇒1008 فوق TLSv1.3). |
| **AC‑12** | Workflow (سلسلة متعدّدة الوكلاء قابلة للتوسعة) | ✅ | `test_workflow_orchestration.py`: `test_result_collects_each_steps_final_in_order` · `test_each_step_resolves_its_own_agent_key` · `test_the_engine_asks_the_provider_once_per_step` · `test_workflows.py` (السجلّ الصارم/حدّ 10 خطوة). |
| **AC‑13** | حسم `OQ‑01…06` مُطبَّق | ✅ | [`00-detailed-design-decisions.md`](design/00-detailed-design-decisions.md): `OQ‑01/02/03` (‏RBAC · SLO/الحدود · تفسير Workspace) **مُوقَّعة بتوقيع العميل 2026‑07‑10**، والباقي محسوم قياسيّاً. |
| **AC‑14** | توسّع الوحدات (دليل + مثال مرجعيّ) | ✅ | [`12-module-authoring-guide.md`](design/12-module-authoring-guide.md) + عشر وحدات مرجعيّة كاملة بالقالب السداسيّ؛ إضافة وحدة جديدة تمرّ import‑linter دون مساس القائم. |
| **AC‑15** | `integrations` (OAuth · Transit · استهلاك عبر الأدوات · تجديد كسول) | ✅ | `test_integrations_module.py::test_connect_stores_cipher_ref_only` (لا نصّ صريح) · `test_api_integrations_callback_router.py` (OAuth عموميّ، هويّة من ربط `state` الخادميّ) · `test_integrations_repository_rls.py::test_update_tokens_is_a_narrow_write_...` (تجديد كسول)؛ الاستهلاك عبر `ToolCatalog` بلا اقتران مباشر (‏4.3). |
| **AC‑16** | `usage` (فرض حصّة + التقاط إدمبوتنسي بلا Streams) | ✅ | حيّ: `test_usage_repository_rls.py::test_append_same_operation_id_is_idempotent_and_never_double_counts` · `::test_two_tenant_isolation_across_records_rollups_and_limits`؛ فرضٌ عبر منفذٍ واردٍ متزامن قبل العملية (‏`FR‑131/132`)، لا أحداث Redis. |

---

## 3. الإثباتات الحيّة (الخطوة 8.2)

أُعيد تشغيلها مُوجَّهةً في 2026‑07‑25، وكلّها خضراء:

**‏`AC‑04` (الـPlugin) + `AC‑06` (دورة الحياة) — 65 اختباراً hermetic:**
```bash
.venv/bin/pytest tests/unit/test_agent_runtime.py tests/unit/test_agent_lifecycle.py
# 65 passed
```
يُثبت: اكتشاف الوكلاء آليّاً من القرص وتسجيلهم دون تعديل النواة (`AC‑04`)؛ والحالات الخمس + مسار الفشل + **التخلّص يجري بالضبط مرّةً على كل مسار** بما فيه الإلغاء والفشل (`AC‑06`).

**‏`AC‑05` (عزل المستأجر) — 121 اختباراً حيّاً على Postgres:**
```bash
.venv/bin/pytest tests/integration/ -k "rls or isolation"
# 121 passed
```
يُثبت لكلّ وحدةٍ من العشر: القراءة العابرة للمستأجر تُعيد **صفر صفوف** تحت RLS، والكتابة بمعرّف مستأجرٍ مُزوّر **تُرفَض بـWITH CHECK**.

**‏`AC‑03` (عزل الوحدات) — قاعدةً وكوداً:**
- كوداً: عقد import‑linter «Business modules do not import each other» — KEPT.
- قاعدةً: مسح كل هجرات `migrations/versions/**` ⇒ لا `REFERENCES`/FK يعبر حدود schema وحدةٍ إلى أخرى.

---

## 4. هرم الاختبار (`09-testing-strategy`)

| الطبقة | الملفّات | الحالات الناجحة | ملاحظة |
|---|---|---|---|
| **قاعدةٌ عريضة — وحدة/hermetic** | 78 ملفّ وحدة + 1 معماريّ | **≈ 1966** | نقيّة، بلا خدمة، ثوانٍ. |
| **قمّةٌ ضيّقة — تكامل حيّ** | 27 ملفّ تكامل | **212** (‏9 متخطّاة) | على Postgres/Redis/MinIO/Qdrant/Vault حقيقيّة. |
| **الإجمالي** | | **2178 ناجح · 0 فشل** | نسبةٌ صحّيّة (القاعدة ≫ القمّة). |

الطفرات (mutation testing) طُبِّقت خطوةً بخطوة عبر كل §3.NN (سجلّ التنفيذ [`log/`](log/)) — «الاختبار الذي لا يمكن أن يسقط ليس اختباراً».

---

## 5. مخرجات المورّد الثلاثة عشر (الخطوة 8.3 · `Req §14` · `OPS‑10`)

| # | المخرَج | الأثر المُسلَّم | ✅ |
|---|---|---|:--:|
| 1 | نموذج البيانات + هجرات Alembic لكل وحدة | [`01-data-model.md`](design/01-data-model.md) + `migrations/versions/` (‏**11 سلسلة**: العشر + `platform`) | ✅ |
| 2 | عقود المنافذ المحايدة | [`02-port-contracts.md`](design/02-port-contracts.md) + `src/app/framework/ports/` | ✅ |
| 3 | مواصفة API كاملة (OpenAPI) | [`03-api-spec.md`](design/03-api-spec.md) + [`openapi.yaml`](design/openapi.yaml) (مطابقة مُختبَرة ثنائيّاً) | ✅ |
| 4 | كتالوج الأحداث + JSON Schemas | [`04-event-catalog.md`](design/04-event-catalog.md) + [`events/schemas/`](design/events/schemas/) (‏**9 مخطّطات**) | ✅ |
| 5 | RBAC + الإعداد/الأسرار (Vault) | [`05-rbac-config-secrets.md`](design/05-rbac-config-secrets.md) + محوّل Vault (KV+Transit) + AppRole | ✅ |
| 6 | نماذج النطاق | [`06-domain-models.md`](design/06-domain-models.md) + `src/app/modules/*/domain/` | ✅ |
| 7 | وثيقة NFR/SLO بالأرقام | [`07-nfr-slo.md`](design/07-nfr-slo.md) | ✅ |
| 8 | دليل التشغيل والنشر | [`08-local-runbook.md`](design/08-local-runbook.md) + `docker-compose.yml` + `deploy/` (nginx·vault·minio·postgres·runpod·smoke) + [أدلّة النشر](deploy-linux-server.md) | ✅ |
| 9 | الاختبارات + import‑linter في CI | `tests/` (‏106 ملفّ) + `.importlinter` + [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | ✅ |
| 10 | معايير الكود المطبّقة (Ruff · mypy‑strict) | [`10-code-standards.md`](design/10-code-standards.md) + `pyproject.toml` | ✅ |
| 11 | دليل تأليف الوكلاء | [`11-agent-authoring-guide.md`](design/11-agent-authoring-guide.md) | ✅ |
| 12 | الكود المصدريّ العامل + سكربتات التشغيل | `src/app/` (الطبقات الخمس + `ops` + `workers`) + `docker-compose.yml` + `app/ops/provision.py` | ✅ |
| 13 | دليل تأليف الوحدات | [`12-module-authoring-guide.md`](design/12-module-authoring-guide.md) | ✅ |

---

## 6. الفجوات المُعلَنة بصدق (خارج نطاق `AC‑01…16`)

**المبدأ: ما لم يُشغَّل لم يُدَّعَ.** التخطّيات العشرة في بوابة pytest، ولا يمسّ أيٌّ منها معيار قبول:

| # | المتخطَّى | السبب | أثره على `AC`؟ |
|---|---|---|:--:|
| 1 | `test_import_contracts` (المعماريّ) | أثر `PATH`: يبحث عن ثنائيّ `lint-imports` غير مثبَّتٍ في مسار الاختبار — والبوابة الحقيقيّة `lint-imports` تمرّ **8/0** | لا (‏`AC‑01` مُثبَتٌ بالبوابة نفسها) |
| 2 | `test_exa_web_search` (‏1) | لا `TEST_EXA_API_KEY` | لا (‏`web_search` أداةٌ مثالٌ اختياريّة `§0.6`) |
| 3–9 | `test_openai_llm` (‏7) | لا `TEST_OPENAI_API_KEY` | لا (`AC‑09` عبر Ollama الحيّ؛ الشكل مُثبَتٌ hermetic) |
| 10 | `test_orchestrator_live` RAG (‏1) | خدمة التضمين الحاويّة غير مُقلَعةٍ على `:8080` (لا Docker مُشغَّل للخدمة) | لا (المنطق مُثبَتٌ فوق `MockTransport`؛ §3.77) |

**دَين المرحلة 2 المتبقّي (محوّلات خارجيّة، لا يمسّ `AC`):** `2.8‑ب‑2` (Gemini·Claude·OpenRouter — محجوزةٌ بالمفاتيح) · `DocumentContentResolver` (حاجز عامل المعرفة الثاني) · `MediaGenerator` (يفكّ عامل `media`). كلّها **محجوزةٌ بمفاتيحَ أو بيئةٍ حاويّة**، ومعايير القبول لا تتطلّب أيّاً منها.

---

## 7. الحكم النهائي

> **✅ المنصّة تجتاز القبول النهائي.** جميع معايير `AC‑01…16` مُحقَّقة ومُثبَتة بدليلٍ قابلٍ لإعادة التشغيل، والبوابات الخمس خضراء، ومخرجات المورّد الثلاثة عشر مُسلَّمة. الفجوات المتبقّية مُعلَنةٌ بصدق، خارج نطاق معايير القبول، ومحجوزةٌ بمفاتيحَ سحابيّةٍ أو ببيئةٍ حاويّة — لا بعمل هندسيّ ناقص.

</div>
