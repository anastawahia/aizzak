<div dir="rtl">

# منصّة ذكاء اصطناعي متعدّدة الوكلاء (AIZZAK)

خلفيّةٌ بنمط **Modular Monolith** فوق **Hexagonal Architecture**، بـ**نظام إضافات** للوكلاء و**معماريّة مدفوعة بالأحداث** للعمليّات الثقيلة.

> **الحالة (2026‑09‑02):** البناءُ الوظيفيّ مكتمل — الشيفرةُ مكتوبةٌ ومُختبَرةٌ ومُقلَعةٌ حاويّاً: طبقةُ الـAPI وحرّاسُ RBAC والمصادقةُ والأحداثُ والبثُّ والنشرُ حيّة، **والعمّالُ الثلاثةُ (`memory` · `knowledge` · `media`) يقلعون افتراضيّاً** مع `docker compose up -d`. وخارطةُ الطريق تُعلن **12/12 مرحلةً مكتملة والقبولَ النهائيّ مُجتازاً** (`AC‑01…16`) — وتقريرُ القبول نفسُه أُزيل مع ملفّات التنفيذ (انظر التحذير أدناه).
>
> **والعملُ النشط اليوم شيءٌ آخر:** [خطّة السعة](docs/capacity-plan.md) على الفرع `capacity` — الانتقالُ من «تعمل» إلى «تخدم مئات المستخدمين». **الموجة 0 (القياس أوّلاً) قيد التنفيذ**: `0.2` (مقاييس RED والإشباع) و`0.3` (‏Prometheus + Grafana) و`0.6` (سجلّاتٌ مجمَّعةٌ في Loki — استعلامٌ واحدٌ يجمع أسطرَ الحافّة والتطبيق والعامل بمعرّفِ ارتباطٍ واحد) تمّت، و`0.1` (منصّة الحمل) **بُنيت كاملةً وشُغّلت**: الهيكلُ، والبذرةُ (`python -m app.ops.load_seed` — مليونُ رسالةٍ و100 ألف ملفٍّ ومليونُ متّجهٍ على 200 مساحةِ عملٍ **عبر RLS لا حولها**)، والمولّدُ (k6 خدمةَ Compose مثبَّتةَ الوسم، فلا يلزم تثبيتُه)؛ و`0.4` (‏`pg_stat_statements` وتقريرُ أغلى الاستعلامات) أداتُها تمّت وقبولُها تحت حمل الذروة محجوب، و`0.5` (خطُّ الأساس) محجوبةٌ كذلك. **وحاجبُ الحافّة رُفع بالقياس** (`د‑8`): كانت تحدُّ على عنوان المصدر، فمولّدٌ من مضيفٍ واحدٍ عَرَض 300.1 طلباً/ث فقُبل منه 22.0 ورُدّ 429 على 92.7٪؛ صار المولّدُ يطالب بـ32 عنواناً ويوزّع حملَه عليها — **7,539 طلباً بمعدّل 300/ث وصفرُ رفض**، دون تغييرِ حرفٍ في `deploy/nginx/` (‏`م‑8`). **والحاجبُ المتبقّي بِركةُ رموزِ Firebase الحقيقيّة** (الشرط (١) في `§0.1`)، ولا يملك المستودعُ ما يسكّها — قرارُ مالك. والموجات 1–8 لم تبدأ. الحالةُ الحيّةُ خطوةً خطوة في [`docs/capacity-status.md`](docs/capacity-status.md).
>
> **البوّابات الخمس — مُشغَّلةٌ على `capacity` بتاريخ 2026‑09‑03 (بعد مولّد `0.1`):** `ruff format` ✅ 699 ملفاً · `ruff check` ✅ · `mypy --strict` ✅ **437 ملفاً** · `lint-imports` ✅ **8/0** · `pytest` الكاملة **والمكدّس الحيّ مُقلَع** ✅ **4459 ناجحاً · 0 فشل · 14 متخطّى** (سبعةٌ بلا مفتاح OpenAI · وأربعةٌ بلا خدمةِ تضمينٍ منشورةٍ على 8080 · وواحدٌ بلا مفتاح Exa · واثنان خلف `RUN_P1_6_LOAD_TEST`).

---

## من أين تبدأ

| أريد أن… | اقرأ |
|---|---|
| **أشغّل المنصّة الآن** | [`docs/quickstart.md`](docs/quickstart.md) — دليلٌ عمليّ: إقلاع · فحوص · أعطال |
| أجد **الأمر** المناسب بسرعة | [`docs/stack-commands.md`](docs/stack-commands.md) — كلّ أوامر المكدّس ووظيفةُ كلٍّ منها |
| **أنشرها على RunPod** | [`deploy/runpod/`](deploy/runpod/) — ⚠️ ‏RunPod لا يشغّل `docker compose`: صورةٌ واحدةٌ شاملة (`Dockerfile` · `bootstrap.sh` · `supervisord.conf`) |
| أفهم **قرارات التشغيل** وأسبابها (المرجع المُلزِم) | [`docs/design/08-local-runbook.md`](docs/design/08-local-runbook.md) |
| أعرف **أين وصل العمل الآن** | [`docs/capacity-status.md`](docs/capacity-status.md) — ما أُنجز · ما يحجُب · ما تغيّر |
| أفهم **إلى أين نمضي** بالسعة وأرقامها | [`docs/capacity-plan.md`](docs/capacity-plan.md) — تسعُ موجات · اختناقاتٌ مرقَّمة (`ح‑*`) · قراراتٌ للتوقيع (`ق‑*`) |
| **أقيس** المنصّة تحت الحمل | [`deploy/load/README.md`](deploy/load/README.md) — خمسةُ سيناريوهات k6 تجري معاً |
| أفهم **المعمار** (‏26 قراراً · 8 مخطّطات) | [`docs/architecture.md`](docs/architecture.md) |
| أقرأ **التصميم التفصيليّ** (بيانات · منافذ · OpenAPI · أحداث · RBAC · NFR · اختبار) | [`docs/design/README.md`](docs/design/README.md) |
| **أضيف وكيلاً أو وحدة** | [`docs/design/11-agent-authoring-guide.md`](docs/design/11-agent-authoring-guide.md) · [`docs/design/12-module-authoring-guide.md`](docs/design/12-module-authoring-guide.md) |

> ⚠️ **روابطٌ بائدةٌ في الوثائق القديمة:** أُزيل سجلُّ البناء `docs/log/` ومعه `implementation-plan.md` و`implementation-status.md` و`acceptance-report.md` وخُطَطُ المراحل في الالتزام `054bd1a` («‏remove all implementation files»، 2026‑08‑19). فكلُّ إحالةٍ إلى `docs/log/3.NN.md` أو إلى تلك الملفّات — في `ROADMAP.md` أو `quickstart.md` أو تعليقات الشيفرة — تشير إلى ملفٍّ غير موجود؛ ومحتواها في تاريخ git وحده. **مصدرُ الحقيقة للحالة اليوم:** هذا الملفّ و[`capacity-status.md`](docs/capacity-status.md).

---

## الطبقات الخمس ← المجلّدات

| الطبقة | المجلّد | الدور |
|---|---|---|
| **Framework** (الكِرنل) | `src/app/framework/` | التجريدات والتنسيق: المنافذ المشتركة · السجلّات · المحرّكات · DI — بلا I/O |
| **API** (محوّل قيادة) | `src/app/api/` | موجّهات `/api/v1` · WebSocket · DTO · المصادقة · حرّاس RBAC · `/metrics` |
| **Agents** (منسّقون / إضافات) | `src/app/agents/` | الوكلاء كإضافات تُكتشَف تلقائيّاً |
| **Business Modules** (النواة) | `src/app/modules/` | **اثنتا عشرة وحدة**، كلٌّ Hexagonal مستقلّ |
| **Infrastructure** (محوّلات مُقادة) | `src/app/infrastructure/` | تنفيذ المنافذ فوق التقنيّات الفعليّة |

**قاعدة الاعتماد:** الاتّجاه للداخل فقط. `domain/` لا يستورد شيئاً تقنيّاً. لا أحد يستورد `infrastructure/` إلّا `framework/di/composition_root.py`. **مفروضةٌ آليّاً** بثمانية عقودٍ في `.importlinter` (‏`D‑17`) تعمل ضمن البوّابات الخمس.

---

## شجرة المشروع

```
AIZZAK/
├── docs/
│   ├── quickstart.md              # ⭐ دليل التشغيل العمليّ
│   ├── stack-commands.md          # مرجع أوامر المكدّس
│   ├── capacity-plan.md           # ⭐ خطّة السعة (تسع موجات) — العمل النشط
│   ├── capacity-status.md         # ⭐ حالة التنفيذ الحيّة
│   ├── architecture.md            # المعمار الكامل (26 قراراً · 8 مخطّطات)
│   ├── ROADMAP.md · Requirements-v1.md
│   ├── design/                    # 00..12 + openapi.yaml + events/schemas (12 مخطّطاً)
│   ├── rag-agent-scenarios-*.md · summarization-scenarios-*.md   # مراجعاتٌ وخُطَطُ سيناريوهات
│   └── tests-catalog.html         # أطلس الاختبارات
│
├── src/app/
│   ├── framework/                 # (1) الكِرنل — بلا I/O
│   │   ├── ports/                 #     21 منفذاً مشتركاً (llm · embedding · rerank · vector · storage · secrets …)
│   │   ├── agent_runtime/         #     BaseAgent · Metadata · Lifecycle · Registry · PluginLoader
│   │   ├── tools/ · workflows/    #     ToolRegistry (D-08) · WorkflowEngine+Registry (D-09)
│   │   ├── events/ · streaming/   #     DomainEvent · Envelope · ConnectionHub · جسر cg.notify
│   │   ├── providers/ · auth/     #     ProviderResolver (D-16)
│   │   ├── context/ · di/         #     ExecutionContext · Composition Root (حقن يدويّ)
│   │   ├── observability/         #     السجلّ JSON · التنقيح · النبض · المقاييس
│   │   └── errors.py              #     ERROR_CATALOG الواحد (RFC 9457) + pagination · identifiers · clock
│   │
│   ├── api/                       # (2) طبقة الواجهة
│   │   ├── main.py                #     مصنع تطبيق FastAPI + دورة الحياة
│   │   ├── v1/routers/            #     16 موجّهاً: agents · conversations · workflows · files · media
│   │   │                          #     knowledge · spaces · credentials · integrations (+ public)
│   │   │                          #     usage · workspace · admin · models · me
│   │   ├── v1/websocket/          #     البثّ الحيّ (D-10)
│   │   ├── v1/{dto,sse.py,dependencies.py,idempotency.py}
│   │   ├── errors.py · health.py · metrics.py
│   │   └── middleware/            #     المصادقة (Firebase) · RBAC
│   │
│   ├── agents/                    # (3) الوكلاء الخمسة + المُنسِّق — إسقاط مجلّد = وكيل جديد (D-13)
│   │   ├── orchestrator.py
│   │   └── rag_agent/ · data_analysis_agent/ · image_agent/ · video_agent/ · file_editing_agent/
│   │
│   ├── modules/                   # (4) اثنتا عشرة وحدة (D-11) — كلٌّ: domain/ application/ ports/ adapters/
│   │   ├── workspace/ · spaces/ · access/ · credentials/ · conversations/ · memory/
│   │   └── files/ · knowledge/ · media/ · integrations/ · usage/ · admin/
│   │
│   ├── infrastructure/            # (5) المحوّلات المُقادة
│   │   ├── persistence/           #     database · rls (SET LOCAL) — D-21 · D-23
│   │   ├── cache/ · messaging/    #     Redis · redis_streams · outbox (D-18) · consumers/
│   │   ├── vector/ · storage/     #     Qdrant (D-01) · MinIO
│   │   ├── secrets/ · auth/       #     Vault (AppRole + Transit — D-03 · D-22) · Firebase (D-25)
│   │   ├── ai_providers/          #     llm/ (ollama · gemini · claude · openai · openrouter)
│   │   │                          #     embedding/ · rerank/ · image/ · video/ (D-15 · D-16)
│   │   ├── streaming/ · monitoring/   # سجلّ اتصالات WS في Redis · مصادر المقاييس
│   │   └── integrations/ · web_search/ · config/
│   │
│   ├── workers/                   # ثلاثة عمّال + مُرحّل (نفس الصورة، أمرٌ مختلف — D-20)
│   │   ├── main.py · bootstrap.py · lifecycle.py
│   │   ├── knowledge_worker.py (+ content_resolver.py) · media_worker.py (+ media_generation.py)
│   │   ├── memory_worker.py
│   │   └── outbox_relay.py        #     مُرحّل Outbox (نسخةٌ واحدة)
│   │
│   └── ops/                       # ⭐ provision.py (الهجرات + المنح — لا `alembic upgrade head`)
│                                  #    retention · purge · revoke · rotate_transit · dlq · healthcheck …
│
├── services/embedding/            # ⭐ deployable منفصل: خدمة التضمين المركزيّة
│                                  #    torch يعيش هنا وحده ولا يدخل رسم استيراد app.*
├── migrations/versions/           # Alembic — اثنتا عشرة سلسلة (version_table_schema لكلّ وحدة + platform)
├── tests/                         # architecture/ · unit/ · integration/ · eval/ (معايرة الاسترجاع)
├── deploy/
│   ├── nginx/ (TLS · WS) · postgres/initdb/ · vault/ · minio/ · smoke/ · gunicorn.conf.py
│   ├── prometheus/ (prometheus.yml · alerts.yml — خمس قواعد) · grafana/ (لوحتا السعة والسجلّات)
│   ├── loki/ · alloy/             # ⭐ تجميعُ السجلّات (0.6) — مخزنٌ واحدٌ قابلٌ للبحث، بلا مقبس Docker
│   ├── load/                      # ⭐ منصّة الحمل: خمسة سيناريوهات k6 · ملفّا ذروةٍ ومتوسّط
│                                  #    run.sh (مضيف أو حاوية) · smoke.sh يُثبت الهيكل في 30 ث
│                                  #    والبذرةُ نفسُها في src/app/ops/load_seed.py (0.1 الشرط ٣)
│   └── runpod/                    # صورةٌ واحدةٌ شاملة لـRunPod
├── .github/workflows/ci.yml
├── docker-compose.yml (+ .test · .wsl-gpu) · Dockerfile · .env.example
└── pyproject.toml · alembic.ini · .importlinter
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

> الاستثناء الوحيد `admin/`: وحدةُ إدارةِ منصّةٍ بلا نطاقٍ خاصّ بها (‏`application/` · `ports/` · `adapters/` فقط)، إذ تنسّق كياناتِ وحداتٍ أخرى ولا تملك كياناً.

## آليّة الإضافة (Plugin)

لإضافة وكيلٍ جديد: أنشئ مجلّداً داخل `src/app/agents/`، وفّر `manifest.py` (يحمل `AgentMetadata`)، وطبّق `BaseAgent` في `agent.py`. يكتشفه `PluginLoader` عبر `importlib` ويسجّله — **دون تعديل النواة**. التفاصيل في [`11-agent-authoring-guide.md`](docs/design/11-agent-authoring-guide.md).

---

## البدء السريع

```bash
cp .env.example .env      # ⚠️ املأ كلمات السرّ + FIREBASE_PROJECT_ID (وإلّا لن يقلع التطبيق)
docker compose up -d
curl -s http://localhost/health
```

`docker compose up -d` يقلع المكدّس كاملاً: البيانات (Postgres · PgBouncer · Redis · MinIO · Qdrant · Vault) ثمّ التهيئةَ والهجرات، ثمّ `app` والعمّالَ الثلاثةَ ومُرحّلَ Outbox، ثمّ الحافّةَ (nginx بـTLS) ومكدّسَ المراقبة (Prometheus · Grafana على `127.0.0.1:13000` · مُصدِّرا PgBouncer وRedis). ‏cAdvisor وحده خلف بروفايل `container-metrics` — يُرفع عند قياس ذاكرة الحاويات لا دائماً.

التفصيل كلّه — المنافذ المُزاحة، وضعا مصادقة Vault، ترتيب إقلاع العمّال، الإثباتات الحيّة — في [`docs/quickstart.md`](docs/quickstart.md)، والأوامرُ مجدولةً في [`docs/stack-commands.md`](docs/stack-commands.md).

### البوّابات الخمس (تُشغَّل داخل WSL؛ أدوات venv اللينكسيّة لا تعمل من Git‑Bash)

```bash
cd /home/AIZZAK && .venv/bin/ruff format --check . && .venv/bin/ruff check . \
  && .venv/bin/mypy src && .venv/bin/lint-imports && .venv/bin/pytest
```

من ويندوز: `wsl -d Ubuntu-24.04 -- bash -lc '<الأمر أعلاه>'`.

---

## ما لا يعمل بعد — مذكورٌ صراحةً

| البند | الأثر |
|---|---|
| مفاتيح مزوّدي السحابة (‏`2.8‑ب‑2`) | محوّلات Gemini · Claude · OpenAI · OpenRouter **مكتوبةٌ ومُختبَرة**، والحاجزُ المفاتيح والقرارُ `ق‑1` غيرُ الموقَّع. **Ollama** المحلّيّ هو المسار العامل — وهو أيضاً سقفُ الأداء الذي تصفه خطّةُ السعة |
| اعتماد `image:openai` | عاملُ **`media`** يقلع ويستهلك فعلاً، لكن تنفيذَ مهمّةِ صورةٍ يحتاج اعتماداً مخزَّناً بهذا الاسم؛ غيابُه **يُفشل المهمّة لا الإقلاع** |
| خطُّ الأساس المُوثَّق (`0.5`) | البذرةُ والمولّدُ بُنيا وشُغّلا، وحدُّ الحافّة لكلّ عنوانٍ رُفع بعناوينَ عدّة (‏`د‑8`: صفرُ رفضٍ عند 300 طلب/ث). **وما بقي بِركةُ رموزِ Firebase حقيقيّة** — الشرطُ (١)، وبدونها يبقى `valid: false` مهما صحّت الأرقام. لا سبيل في المستودع لسكّها؛ تحتاج اعتمادَ المشروع |
| الموجات 1–8 من خطّة السعة | لم تبدأ. الطريقُ الحرج: `ق‑1` → الموجة 0 → 1 → 2 → 3 → بوّابةُ القبول ([التفصيل](docs/capacity-status.md)) |

جميع القرارات المرجعيّة (‏`D‑01` … `D‑26`) موثّقةٌ في [`docs/architecture.md`](docs/architecture.md)، واختناقاتُ السعة (‏`ح‑1` … `ح‑20`) وقراراتُها (‏`ق‑1` … `ق‑6`) في [`docs/capacity-plan.md`](docs/capacity-plan.md).

</div>
