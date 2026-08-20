<div dir="rtl">

# منصّة ذكاء اصطناعي متعدّدة الوكلاء (AIZZAK)

خلفيّةٌ بنمط **Modular Monolith** فوق **Hexagonal Architecture**، بـ**نظام إضافات** للوكلاء و**معماريّة مدفوعة بالأحداث** للعمليّات الثقيلة.

> **الحالة (2026‑07‑24): المراحل 0–7 مكتملة.** الشيفرة مكتوبةٌ ومُختبَرة ومُقلَعةٌ حاويّاً — طبقة الـAPI وحرّاس RBAC والمصادقة والأحداث والبثّ والنشر كلّها حيّة. **المرحلة 8 (القبول النهائيّ) لم تبدأ.**
>
> **البوّابات الخمس عند آخر خطوةٍ مغلقة (§3.77):** `ruff` ✅ 471 ملفاً · `mypy --strict` ✅ 334 ملفاً · `lint-imports` ✅ **8/0** · `pytest -m "not integration"` ✅ **2178 ناجحاً · 0 فشل · 10 متخطّى**.

---

## من أين تبدأ

| أريد أن… | اقرأ |
|---|---|
| **أشغّل المنصّة الآن** | [`docs/quickstart.md`](docs/quickstart.md) — دليلٌ عمليّ: إقلاع · فحوص · أعطال |
| **أنشرها على خادم Linux خاصّ بي** | [`docs/deploy-linux-server.md`](docs/deploy-linux-server.md) — نقلٌ وتشغيلٌ بـ`docker compose` على خادمٍ نظيف، خطوةً بخطوة لمن لا خبرة له |
| **أنشرها على RunPod** | [`docs/deploy-runpod.md`](docs/deploy-runpod.md) — ⚠️ ‏RunPod لا يشغّل `docker compose`: صورةٌ واحدة شاملة، خطوةً بخطوة |
| أفهم **قرارات التشغيل** وأسبابها (المرجع المُلزِم) | [`docs/design/08-local-runbook.md`](docs/design/08-local-runbook.md) |
| أعرف **أين وصل البناء** وما الخطوة التالية | [`docs/implementation-status.md`](docs/implementation-status.md) |
| أقرأ **سجلّ البناء** خطوةً خطوة | [`docs/log/INDEX.md`](docs/log/INDEX.md) |
| أفهم **المعمار** (‏26 قراراً · 9 مخطّطات) | [`docs/architecture.md`](docs/architecture.md) |
| أقرأ **التصميم التفصيليّ** (بيانات · منافذ · OpenAPI · أحداث · RBAC · NFR · اختبار) | [`docs/design/README.md`](docs/design/README.md) |
| **أضيف وكيلاً أو وحدة** | [`docs/design/11-agent-authoring-guide.md`](docs/design/11-agent-authoring-guide.md) · [`docs/design/12-module-authoring-guide.md`](docs/design/12-module-authoring-guide.md) |

---

## الطبقات الخمس ← المجلّدات

| الطبقة | المجلّد | الدور |
|---|---|---|
| **Framework** (الكِرنل) | `src/app/framework/` | التجريدات والتنسيق: المنافذ المشتركة · السجلّات · المحرّكات · DI — بلا I/O |
| **API** (محوّل قيادة) | `src/app/api/` | موجّهات `/api/v1` · WebSocket · DTO · المصادقة · حرّاس RBAC |
| **Agents** (منسّقون / إضافات) | `src/app/agents/` | الوكلاء كإضافات تُكتشَف تلقائيّاً |
| **Business Modules** (النواة) | `src/app/modules/` | عشر وحدات، كلٌّ Hexagonal مستقلّ |
| **Infrastructure** (محوّلات مُقادة) | `src/app/infrastructure/` | تنفيذ المنافذ فوق التقنيّات الفعليّة |

**قاعدة الاعتماد:** الاتّجاه للداخل فقط. `domain/` لا يستورد شيئاً تقنيّاً. لا أحد يستورد `infrastructure/` إلّا `framework/di/composition_root.py`. **مفروضةٌ آليّاً** بثمانية عقودٍ في `.importlinter` (‏`D‑17`) تعمل ضمن البوّابات الخمس.

---

## شجرة المشروع

```
AIZZAK/
├── docs/
│   ├── quickstart.md              # ⭐ دليل التشغيل العمليّ
│   ├── architecture.md            # المعمار الكامل (26 قراراً · 9 مخطّطات)
│   ├── implementation-status.md   # حالة البناء ونقطة الاستئناف
│   ├── implementation-plan.md · ROADMAP.md · Requirements-v1.md
│   ├── design/                    # 00..12 + openapi.yaml + events/
│   ├── log/                       # سجلّ البناء: INDEX.md + 3.NN.md لكلّ خطوة + CHANGELOG.md
│   └── migration/refs/            # مراجع محصودة من الشيفرة القديمة (alpha)
│
├── src/app/
│   ├── framework/                 # (1) الكِرنل — بلا I/O
│   │   ├── ports/                 #     15 منفذاً مشتركاً (llm · embedding · vector · storage · secrets …)
│   │   ├── agent_runtime/         #     BaseAgent · Metadata · Lifecycle · Registry · PluginLoader
│   │   ├── tools/ · workflows/    #     ToolRegistry (D-08) · WorkflowEngine+Registry (D-09)
│   │   ├── events/ · streaming/   #     DomainEvent · Envelope · ConnectionHub · جسر cg.notify
│   │   ├── providers/             #     ProviderResolver (D-16)
│   │   ├── context/ · di/         #     ExecutionContext · Composition Root (حقن يدويّ)
│   │   ├── observability/ · settings/
│   │   └── errors.py              #     ERROR_CATALOG الواحد (RFC 9457) + pagination · identifiers · clock
│   │
│   ├── api/                       # (2) طبقة الواجهة
│   │   ├── main.py                #     مصنع تطبيق FastAPI + دورة الحياة
│   │   ├── v1/routers/            #     agents · conversations · workflows · files · media · knowledge
│   │   │                          #     credentials · integrations (+ integrations_public) · usage · workspace
│   │   ├── v1/websocket/          #     البثّ الحيّ (D-10)
│   │   ├── v1/{dto,sse.py,dependencies.py}
│   │   ├── errors.py · health.py
│   │   └── middleware/            #     المصادقة (Firebase) · RBAC
│   │
│   ├── agents/                    # (3) الوكلاء الخمسة + المُنسِّق — إسقاط مجلّد = وكيل جديد (D-13)
│   │   ├── orchestrator.py
│   │   └── rag_agent/ · data_analysis_agent/ · image_agent/ · video_agent/ · file_editing_agent/
│   │
│   ├── modules/                   # (4) الوحدات العشر (D-11) — كلٌّ: domain/ application/ ports/ adapters/
│   │   ├── workspace/ · access/ · credentials/ · conversations/ · memory/
│   │   └── files/ · knowledge/ · media/ · integrations/ · usage/
│   │
│   ├── infrastructure/            # (5) المحوّلات المُقادة
│   │   ├── persistence/           #     database · rls (SET LOCAL) — D-21 · D-23
│   │   ├── cache/ · messaging/    #     Redis · redis_streams · outbox (D-18) · consumers/
│   │   ├── vector/ · storage/     #     Qdrant (D-01) · MinIO
│   │   ├── secrets/ · auth/       #     Vault (AppRole + Transit — D-03 · D-22) · Firebase (D-25)
│   │   ├── ai_providers/          #     llm/ · embedding/ · image/ · video/ (D-15 · D-16)
│   │   ├── integrations/ · web_search/ · config/
│   │
│   ├── workers/                   # نقاط تشغيل العمّال (نفس الصورة، أمرٌ مختلف — D-20)
│   │   ├── main.py · bootstrap.py
│   │   ├── knowledge_worker.py · media_worker.py · memory_worker.py
│   │   └── outbox_relay.py        #     مُرحّل Outbox (نسخةٌ واحدة)
│   │
│   └── ops/provision.py           # ⭐ الهجرات (11 سلسلة) + المنح — لا `alembic upgrade head`
│
├── services/embedding/            # ⭐ deployable منفصل: خدمة التضمين المركزيّة (2.10)
│                                  #    torch يعيش هنا وحده ولا يدخل رسم استيراد app.*
├── migrations/                    # Alembic — إحدى عشرة سلسلة (version_table_schema لكلّ وحدة)
├── tests/                         # architecture/ · unit/ · integration/
├── deploy/                        # nginx/ (TLS · WS) · postgres/initdb/ · vault/ · minio/ · smoke/
├── .github/workflows/ci.yml
├── docker-compose.yml · Dockerfile · .env.example
├── pyproject.toml · alembic.ini · .importlinter
└── docs/
```

---

## بنية الوحدة الواحدة (Hexagonal)

كلّ وحدةٍ في `src/app/modules/<module>/` تتبع التشريح نفسه:

```
<module>/
├── domain/            # كيانات · Value Objects · Domain Events — نقيّ، بلا I/O
├── application/       # حالات الاستخدام (تنسيق النطاق)
├── ports/             # واجهات الوحدة (مثل Repository)
└── adapters/          # تنفيذ منافذ الوحدة (SQL Repository)
```

## آليّة الإضافة (Plugin)

لإضافة وكيلٍ جديد: أنشئ مجلّداً داخل `src/app/agents/`، وفّر `manifest.py` (يحمل `AgentMetadata`)، وطبّق `BaseAgent` في `agent.py`. يكتشفه `PluginLoader` عبر `importlib` ويسجّله — **دون تعديل النواة**. التفاصيل في [`11-agent-authoring-guide.md`](docs/design/11-agent-authoring-guide.md).

---

## البدء السريع

```bash
cp .env.example .env      # ⚠️ املأ كلمات السرّ الخمس + FIREBASE_PROJECT_ID (وإلّا لن يقلع التطبيق)
docker compose up -d
curl -s http://localhost/health
```

التفصيل كلّه — المنافذ المُزاحة، وضعا مصادقة Vault، ترتيب إقلاع العمّال، الإثباتات الحيّة — في [`docs/quickstart.md`](docs/quickstart.md).

### البوّابات الخمس (تُشغَّل داخل WSL؛ أدوات venv اللينكسيّة لا تعمل من Git‑Bash)

```bash
wsl -d Ubuntu-24.04 -- bash -lc 'cd /home/AIZZAK && .venv/bin/ruff format --check . && .venv/bin/ruff check . && .venv/bin/mypy src && .venv/bin/lint-imports && .venv/bin/pytest'
```

---

## ما لا يعمل بعد — مذكورٌ صراحةً

| البند | الأثر |
|---|---|
| `DocumentContentResolver` غائب | عامل **`knowledge`** لا يقلع ⇒ لا فهرسةَ مستنداتٍ من طرفٍ إلى طرف |
| `MediaGenerator` غائب | عامل **`media`** لا يقلع ⇒ مهامّ الوسائط تُقبَل (202) ولا تكتمل |
| مفاتيح مزوّدي السحابة (2.8‑ب‑2) | Gemini · Claude · OpenRouter محجوبون. **Ollama** المحلّيّ هو المسار العامل |
| القبول النهائيّ (المرحلة 8) | لم يبدأ |

> عامل **`memory`** يقلع ويعمل كاملاً منذ البند 2.10. وخدمة `worker` خلف `profile` في Compose لئلّا يدور العاملان المحجوبان في حلقة انهيارٍ فيُظهرا مكدّساً سليماً بمظهر المعطوب.

جميع القرارات المرجعيّة (‏`D‑01` … `D‑26`) موثّقةٌ في [`docs/architecture.md`](docs/architecture.md).

</div>
