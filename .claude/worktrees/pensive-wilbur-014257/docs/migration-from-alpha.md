<div dir="rtl">

# دراسة الهجرة — من `alpha` (القديم) إلى `AIZZAK` (الجديد)

> **Migration Study · v1.0**
>
> مرجع لعملية نقل ما يمكن إعادة استخدامه من المشروع القديم إلى المشروع الجديد، لتوفير جهد الكتابة من الصفر.

| | |
|---|---|
| **المصدر القديم** | `~/alpha` (يعمل ومُختبَر — التوثيق: `CODEMAP.md` · `ARCHITECTURE.md` · `AI_CONTEXT.md`) |
| **الهدف الجديد** | `~/AIZZAK` (تصميم مكتمل — انظر [`ROADMAP.md`](ROADMAP.md) · [`design/`](design/)) |
| **الحالة** | 🔵 دراسة مرجعية — لم يُنقل أو يُعدَّل أي كود بعد |
| **آخر تحديث** | 2026‑07‑10 |

---

## 1. الخلاصة التنفيذية

الفرق الجوهري بين المشروعين هو **الاقتران بالإطار (framework coupling)**:

- **القديم (`alpha`):** يعمل ومُختبَر، لكنه مقترن بعمق بـ **LangChain** (التنسيق والأدوات) و**LlamaIndex + FAISS** (محرك RAG)، ويعتمد على **حالة عامة (globals)**: `RUNTIME` · `_LOCK` · ذاكرات لكل عملية.
- **الجديد (`AIZZAK`):** معمارية سداسية/DDD نقية، منافذ **محايدة للمزوّد** (أنواع `LlmMessage`/`LlmResult` الخاصة، لا LangChain)، تخزين متجهات عبر **Qdrant**، كل شيء **async** ومحقون عبر Composition Root، مع **عزل مستأجر (`workspace_id`) + RLS**.

**القاعدة الحاكمة للنقل:**
> **الخوارزميات ومنطق الأعمال والمعرفة التشغيلية تُنقل بقيمة عالية. أمّا كود التنسيق المقترن بالإطار والحالة العامة فيُعاد بناؤه — لكن القديم يبقى مرجعاً سلوكياً دقيقاً.**

**تقدير الجهد الموفَّر:** ~**35–45%** من الكود الفعلي قابل لإعادة الاستخدام (نسخ مباشر أو تكييف)، والباقي يُعاد بناؤه مع مرجع سلوكي جاهز يقلّل زمن التصميم. أثمن كتلة: **المُحلّلات (parsers)** و**خوارزميات الاسترجاع**.

---

## 2. تصنيف المكوّنات حسب قابلية النقل

### 🟢 الطبقة أ — نسخ شبه مباشر (منطق نقي، تبديل غلاف رفيع فقط)

| المكوّن القديم | الملف/المسار | الوجهة الجديدة | ملاحظة النقل |
|---|---|---|---|
| **المُحلّلات (Parsers)** ⭐ | `rag/parsers/` (~156KB: PDF layout/tables، Excel، OCR/صور، JSON، نص) | `modules/knowledge` (خدمة استخلاص) | تعتمد PyMuPDF/pandas/PIL/pytesseract — **مستقلة عن الإطار**. الاقتران الوحيد هو غلاف `llama_index.Document` في المُخرَج → استبدله بنوع chunk الجديد. **أعلى كنز.** |
| **دمج RRF** | `rag/retrieval/reranking.py::reciprocal_rank_fusion` | `knowledge` (الاسترجاع الهجين) | خوارزمية **نقية**؛ يكفي تبديل `NodeWithScore` بنوعك. |
| **مُقسِّم BM25 (عربي/إنجليزي)** | `rag/indexing/bm25_tokenizer.py` | `knowledge` | تجزئة نصية نقية، عربية/لاتينية. |
| **الآلة الحاسبة الآمنة (AST)** | `services/tools/calculator.py` | أداة `BaseTool` | Python خالص + حواجز الحجم (H7: حدود `**`/factorial/طول التعبير). فقط بدّل `@tool` بـ `BaseTool.run`. |
| **أدوات الـAPI** | `services/tools/{web_search,weather,currency,news,sports}.py` | أدوات `BaseTool` | منطق نداء الـAPI (Exa، OpenWeather، ExchangeRate، NewsAPI، SportDB) **نقي**؛ لُف بـ `BaseTool` واستبدل الكاش بمنفذ `CacheProvider`. |
| **الرصد والارتباط** | `shared/{logging_config,request_context,observability,trace_log}.py` | ركائز `framework` المشتركة | تسجيل JSON منظّم + `request_id` + إخفاء PII (`redact`) + عقد الأخطاء. يتوافق مع NFR الجديد؛ تكييف طفيف. |
| **مُوجّهات النصّ والمساعدات** | `rag/utils/{text,timing}.py`، `services/_text_utils.py` | `knowledge`/`framework` | تنظيف نص، توقيت، كشف العربية، ترجمة. |

### 🟡 الطبقة ب — نقل مع تكييف معماري (المنطق ينتقل، تُعاد صياغته إلى منافذ/async/DDD)

| المكوّن القديم | الملف/المسار | الوجهة الجديدة | ما ينتقل vs يتغيّر |
|---|---|---|---|
| **خط أنابيب استرجاع RAG** ⭐ | `rag/retrieval/` (هجين، رِرانك، `intent_router`، `parent_chunk_postprocessor`، `file_resolver`، التكثيف) | `modules/knowledge` | **الخوارزميات والاستراتيجية تنتقل** (over-fetch ×4، doc-scoping، RRF، عتبات الثقة، parent-chunk swap، تصنيف النية METADATA/CONTENT/SUMMARIZE). **التنسيق يُعاد بناؤه**: LlamaIndex+FAISS → منفذ `VectorStore` (Qdrant) + أنواعك. أكبر جهد مفرد، وأعلى قيمة مرجعية. |
| **تشفير المفاتيح + دقّة الحلّ** | `services/provider_keys.py` | `modules/credentials` + `CredentialResolver` | **أسبقية الحل تنتقل حرفياً** (user → global → env). لكن التشفير يتغيّر من **Fernet** إلى **Vault Transit** (D‑03/22)، والـI/O يصير async عبر Repository بدل SQLAlchemy المتزامن. |
| **سجل المزوّدين + كتالوج الموديلات** | `services/{model_registry,models_catalog,provider_gate,llm}.py` | `framework/providers/ProviderResolver` + جدول توجيه في `Settings` + `usage`/`credentials` | **المفاهيم تنتقل**: اكتشاف Ollama الحيّ، ميتاداتا القدرات (`is_tool_calling`/`is_local`)، بوابة السماح، فصل STATIC/DYNAMIC. **الكود يُعاد كتابته** حول محوّلات SDK أصلية بدل بُناة LangChain. |
| **محوّلات المزوّدين (LLM)** | `services/model_registry.py::_build_*`، `rag/core/llm.py` | `infrastructure/ai_providers/llm/*` (ملفات **فارغة الآن** 0 بايت) | معرفة النقاط الطرفية (base_url، خصوصية ilmu، `num_ctx`/`keep_alive` لـ Ollama) مرجع ثمين، لكن التنفيذ يصبح `complete()/stream()` أصلي (OpenAI/Gemini/Claude/Ollama/OpenRouter) لا LangChain. |
| **كاش/حدود/وظائف Redis** | `services/{cache,ratelimit,jobs}.py`، `jobs/runner.py` | محوّل `CacheProvider` + **Outbox/Relay** (المرحلة 9) | الأنماط تنتقل (fail-open، presence TTL، نافذة ثابتة، RPOP loop آمن لـ WSL2، `reclaim_stale`). تُعاد صياغة async، ونظام الوظائف يُستبدل بـ Transactional Outbox → Redis Streams → Consumer Groups. |
| **تخزين MinIO** | `services/avatar_store.py` | محوّل `StorageProvider` (MinIO) | نقل شبه مباشر: put/get/delete/presign + كشف data-uri. |
| **مصادقة Firebase** | `shared/auth.py`، `integrations/firebase_admin_config.py` | محوّل `AuthProvider` + حرّاس RBAC | منطق التحقق ينتقل؛ الجديد يفضّل **تحقّق JWT محلي بمفاتيح مُخزّنة** (D‑25) بدل نداء Admin SDK لكل طلب. |
| **Gmail OAuth + PKCE** | `routers/gmail.py`، `services/{oauth_store,gmail_store}.py` | `modules/integrations` + `ConnectorProvider` | تدفّق OAuth، تخزين verifier في Redis (GETDEL أحادي الاستخدام fail-closed)، تخزين التوكن — مرجع ممتاز لمحوّل الموصّلات. |
| **موجّهات النظام** | `services/prompts.py` (`_build_system_prompt`, `_RAG_SYSTEM`) | manifests الوكلاء | تنتقل كمحتوى (نصوص). |

### 🔴 الطبقة ج — مرجع سلوكي فقط / لا يُنقل (تغيّرت المعمارية أو قفل الإطار)

| المكوّن القديم | لماذا لا يُنقل | قيمته |
|---|---|---|
| **تنسيق LangChain** (`services/langchain_service.py`: AgentExecutor + مسارات fallback الثلاثة) | الجديد يبني حلقة استدعاء الأدوات فوق `LLMProvider` الأصلي + `BaseAgent`؛ لا LangChain | **مرجع سلوكي عالٍ**: استراتيجيات الـfallback (إعادة حقن نتائج الأدوات، البحث القسري للأسئلة الواقعية، السلسلة البسيطة) تستحق التقليد. |
| **الحالة العامة**: `RUNTIME`، `_LOCK`، `_CTX_CACHE`، ذاكرات لكل عملية | مضادّ-نمط يلغيه التصميم الجديد صراحةً (وكيل عديم الحالة، حقن صريح) | تحذيري فقط. |
| **نماذج DB القديمة** (`db/models.py` — 6 جداول: users/agents/messages/keys/gmail) | متجاوَزة بنموذج بيانات غني بعشر وحدات + RLS + عزل مستأجر | لا شيء يُنقل. |
| **تخزين FAISS** (`rag/core/storage.py`) | مُستبدَل بـ Qdrant عبر منفذ `VectorStore` | لا يُنقل. |
| **البنية التحتية للتشغيل** (`nginx/`، `worker_control.*`، `start/stop.sh`، `scripts/setup_*`) | مُستبدَلة بـ **Docker Compose** (D‑26) + طوبولوجيا المرحلة 11 | مرجع لأوامر التشغيل فقط. |
| **Alembic/SQLite القديم** | هجرات لكل وحدة في الجديد + Postgres/PgBouncer/RLS | لا يُنقل. |

---

## 3. خريطة النقل حسب مراحل `ROADMAP.md`

| المرحلة | ما يُستعان به من القديم |
|---|---|
| **5 — الأساس** | ركائز الرصد (`shared/logging_config`، `observability`، `trace_log`)، ودقّة `redact` لإخفاء PII. |
| **6 — المنافذ والمحوّلات** | محوّل MinIO (`avatar_store`)، Firebase (`auth`)، Redis (`cache`)، ومعرفة نقاط مزوّدي LLM من `model_registry._build_*` و`rag/core/llm.py`. |
| **7 — وحدات الأعمال** | `credentials` ← منطق `provider_keys` (الأسبقية) · `knowledge` ← **المُحلّلات + خط الاسترجاع** (الكنز الأكبر) · `integrations` ← Gmail OAuth/PKCE · `usage` ← بوابة `models_catalog`. |
| **8 — الإطار والوكلاء** | **كل الأدوات** (`services/tools/*`) → `BaseTool` · موجّهات `prompts.py` → manifests · استراتيجيات fallback من `langchain_service` كمرجع للوكيل. |
| **9 — الأحداث والبثّ** | أنماط `services/jobs.py` + `jobs/runner.py` (RPOP loop، `reclaim_stale`، idempotency) كمرجع لـ Outbox/Relay/DLQ. |
| **10 — API** | عقد الأخطاء الموحّد من `observability.py` (يقترب من RFC 9457)، وحرّاس المصادقة من `shared/auth.py`. |

---

## 4. الترتيب العملي المقترح للنقل

1. **ابدأ بالمُحلّلات** (`rag/parsers/`) — أعلى قيمة، أقل اقتران، عائد فوري لوحدة `knowledge`.
2. **الأدوات** (`services/tools/*`) — لُفّها بـ `BaseTool` تدريجياً؛ كل أداة مستقلة.
3. **الخوارزميات النقية** (RRF، مُقسّم BM25، ثمّ خط الاسترجاع كاملاً) — أعد بناء التنسيق فوق منفذ Qdrant.
4. **منطق الحلّ/التشفير** إلى `credentials`، وتدفّق OAuth إلى `integrations`.
5. **محوّلات البنية التحتية** (MinIO/Redis/Firebase) — نقل مباشر نسبياً.
6. **محوّلات LLM الأصلية** — أعد الكتابة مستنداً لمعرفة النقاط الطرفية القديمة.

---

## 5. محاذير مهمّة

- **لا تنقل الحالة العامة معك.** كل قطعة تلمس `RUNTIME`/`_LOCK`/ذاكرة-عملية يجب أن تصير محقونة عديمة الحالة، وإلا ستُفسد ضمانات المعمارية الجديدة (وكيل لكل طلب، عزل مستأجر).
- **عزل المستأجر (`workspace_id`) غائب في القديم** — كل استعلام مُرحّل يجب أن يضيف `workspace_id` عبر `ExecutionContext` + RLS. القديم أحادي-المستأجر عملياً.
- **async في كل مكان** — القديم متزامن (SQLAlchemy sync، نداءات محظورة على thread pool). النقل يتطلب تحويلاً لـ async I/O.
- **تبديل نوع العقدة** — كل ما يلمس `NodeWithScore`/`Document` من LlamaIndex يحتاج تبديل نوع، وهو تغيير ميكانيكي واسع لكنه بسيط.

---

## 6. الأدلّة التي استندت إليها الدراسة (عيّنات مفحوصة)

| ما فُحِص | النتيجة |
|---|---|
| `services/tools/calculator.py` | Python نقي + حواجز H7؛ الاقتران الوحيد `@tool` من LangChain. |
| `services/provider_keys.py` | تشفير Fernet + أسبقية حلّ (user→global→env)؛ منطق نقي قابل للنقل، SQLAlchemy متزامن. |
| `rag/retrieval/hybrid_retriever.py` | مقترن بعمق بـ LlamaIndex (`NodeWithScore`، `VectorStoreIndex`، `docstore`، FAISS). |
| `rag/parsers/*` (imports) | PyMuPDF/pandas/PIL — الاستخلاص مستقل عن الإطار؛ فقط غلاف `Document`. |
| `rag/retrieval/reranking.py` | `reciprocal_rank_fusion` رياضيات نقية؛ فقط نوع العقدة. |
| `services/model_registry.py` | بُناة LangChain (`ChatOpenAI`/`ChatOllama`…)؛ النمط ينتقل، الكود يُعاد كتابته. |
| `src/app/infrastructure/ai_providers/llm/*` (الجديد) | ملفات فارغة 0 بايت — هيكل فقط، يحتاج تنفيذاً كاملاً. |

</div>
