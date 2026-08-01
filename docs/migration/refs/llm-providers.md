# refs/llm-providers.md — مزوّدو LLM (مرجع من `alpha`)

> **الوجهة:** `02-port-contracts.md §1.1` (`LLMProvider`) · `§1.2` (`EmbeddingProvider`) · `§2` (`CredentialResolver`) · `§3.5` (`ProviderResolver`) · `00-detailed-design-decisions.md DD‑13/DD‑16/DD‑11` · `05 §2,§3`.
> **النطاق:** بناء عملاء LLM وتوليدهم واكتشافهم في `alpha`، للمطابقة على **5 محوّلات أصلية** في AIZZAK (`DD‑13`: **OpenAI · Gemini · Claude · Ollama · OpenRouter** — **بلا LangChain**).
> **مصادر `alpha`:** `services/{model_registry,llm,langchain_service,_token_counter,provider_keys,provider_gate,models_catalog,inference}.py` · `rag/core/{llm,embeddings}.py` · `rag/config/{settings,runtime_config}.py` · `integrations/rag_manager.py` · `routers/{agents,rag}.py` · `main.py`.

## 0) واقع `alpha` (السياق) — مساران مستقلّان
- **سجلّ المزوّدين** `model_registry.PROVIDERS` (tuple، **ترتيب الإعلان = ترتيب الأسبقية**) + `Provider`/`ModelSpec` (frozen dataclasses). كل مزوّد يحمل `langchain_builder` موحّد التوقيع.
- **المسار (أ) — دردشة/وكيل (LangChain):** `routers/agents.py → langchain_service.get_chat_response/get_rag_agent_response → llm.get_llm → model_registry.build_chat_llm → _build_*`؛ الاستدعاء عبر `AgentExecutor(create_tool_calling_agent(...)).invoke()` **حاجب**. يخدم كل النماذج ذات tool‑calling (كل السحابة + `OLLAMA_TOOL_MODELS`).
- **المسار (ب) — RAG حتمي (LlamaIndex):** `integrations/rag_manager._conv_llm → rag/core/llm.build_llm → Ollama(...) | YtlaiLLM(...)`؛ للنماذج المحلّية **غير** ذات tool‑calling فقط. عملاء مُخبّأون عملياتياً منفصلون عن المسار (أ).
- **نتيجة:** **بناءان مضبوطان مستقلّان لـOllama** بافتراضات مختلفة (§2)، وعميل ilmu مكرّر (ChatOpenAI متوافق مقابل `YtlaiLLM` httpx خام). **AIZZAK يوحّدهما في محوّل واحد لكل مزوّد.**

## 1) المزوّدون الأربعة (`PROVIDERS`)
| المفتاح | `kind` | مفتاح مطلوب؟ | `key_env` | مصدر النماذج | tool‑calling |
|---|---|---|---|---|---|
| `openai` | cloud | نعم | — (لا يُقرأ `OPENAI_API_KEY` في البُناة) | STATIC `OPENAI_MODELS` → `("gpt-5.4-mini","gpt-5.4")` | ثابت `True` |
| `google` | cloud | نعم | — (لا يُقرأ `GEMINI_API_KEY`) | STATIC `GOOGLE_MODELS` → `("gemini-2.0-flash","gemini-1.5-pro")` | ثابت `True` |
| `ilmu` | cloud | نعم | `SERVICE_API_KEY` | STATIC `ILMU_MODELS` → `("ilmu-trial",)` | ثابت `True` |
| `ollama` | **local** | **لا** | — | DYNAMIC `discover_ollama()` → `("qwen3.5:9b","llama3.2")` | لكل نموذج عبر `OLLAMA_TOOL_MODELS` (legacy `RAG_AGENT_OLLAMA_MODELS`) |

- `DEFAULT_CHAT_MODEL="gpt-5.4-mini"` · `_FALLBACK_PROVIDER="ollama"`.
- **`Provider` dataclass (حقول رئيسية):** `key,label,langchain_builder,env_models,discover,default_models,engine_provider,default_tool_calling,tool_models_env,kind,requires_user_key,key_env,base_url_env`.
- **`base_url_env` حقل ميّت:** لا مزوّد يضبطه، ولا `get_llm` يمرّر `base_url` ⇒ فرع التجاوز غير قابل للوصول. نقطة نهاية ilmu **حرفية مثبّتة داخل `_build_ilmu`** لا من env.
- **التوجيه:** `provider_of(name)`: STATIC/cloud أولاً ثم DYNAMIC، **وإلا `"ollama"`** (أي اسم غير معروف يُوجَّه محلّياً). `is_local(name)`: cloud‑معلَن‑صراحةً⇒False وإلا True. `engine_provider_for(name)`: cloud⇒`None`→`"ollama"` (السحابة لا تصل المحرّك أبداً).

## 2) بناء العملاء ومعاملات التوليد (حرفياً)
**كل البُناة تُثبّت `temperature=0.3`.** ولا مُنادٍ يتجاوز `num_predict` عن `-1` ⇒ **كل نداء LLM في alpha يطلب حدّ مخرجات غير محدود** (السحابة بلا سقف؛ المحلّي مقيّد فقط بـ`num_ctx=8192`).
```python
ChatOpenAI(model=name, temperature=0.3, openai_api_key=api_key, max_tokens=num_predict if num_predict>0 else None)
ChatGoogleGenerativeAI(model=name, temperature=0.3, google_api_key=api_key, max_output_tokens=num_predict if num_predict>0 else None)
ChatOpenAI(model=name, temperature=0.3, api_key=api_key, base_url=base_url or "https://api.ytlailabs.tech/preview/v1", max_tokens=...)  # ilmu
ChatOllama(model=name, temperature=0.3, keep_alive=get_ollama_keep_alive(), num_predict=num_predict, num_ctx=_OLLAMA_NUM_CTX, reasoning=False)  # المسار (أ)
```
**المسار (ب) — Ollama محرّك RAG** (`rag/core/llm.build_llm`، LlamaIndex): `Ollama(model, base_url=OLLAMA_URL, request_timeout=300.0, additional_kwargs={"num_predict": OLLAMA_NUM_PREDICT=1024}, keep_alive=OLLAMA_KEEP_ALIVE="30m", context_window=8192 [مثبّت], thinking=False, is_function_calling_model=False)`.
- **فرقان:** `num_predict` افتراضي `-1` (المسار أ) مقابل `1024` (المسار ب)؛ مفتاح تعطيل التفكير `reasoning=` مقابل `thinking=` (كلاهما يضبط `think` الأصلي في Ollama).
- **علّة `num_ctx`/`context_window=8192`:** بدونه يحمّل Ollama السياق الأصلي الكامل (افتراضي LlamaIndex `-1`، مثلاً qwen3.5:9b=262144) ⇒ انهيار GPU؛ أو يقتطع صامتاً عند ~4096.
- **ilmu `YtlaiLLM.complete()`** (httpx خام): `POST .../preview/v1/chat/completions`, `Authorization: Bearer`, `timeout=90.0`، **⚠️ يبتلع كل استثناء ويُرجعه كنصّ إجابة** `"[YtlaiLLM error: e]"`. `stream_complete` **بثّ مزيّف** (yield نتيجة واحدة كاملة).

**كاش العملاء (المسار أ، `llm.get_llm`):** `cache_key=f"{model}:{num_predict}:{keep_alive}:{sha256(api_key)[:12]}"` (بصمة المفتاح تمنع تسرّب عميل مستخدم لآخر)؛ `_llm_cache` dict عملياتي **غير محدود، بلا إخلاء** (متعمّد: عملاء غير قابلين للـpickle). المسار (ب) كاش منفصل `_LLM_CLIENTS` بمفتاح `model_name` فقط تحت قفل.

## 3) الاكتشاف · العدّ · التسخين
- **اكتشاف Ollama:** `fetch_ollama_tags()` = `GET {OLLAMA_URL}/api/tags` (timeout 5s، يرفع)؛ `discover_ollama()` مُخبّأ TTL `MODEL_DISCOVERY_TTL_SECONDS=45`، **`[]` عند الفشل** ⇒ ترجع `default_models`. نقطة الإدارة `GET /models/ollama` (admin، rate‑limited) تتجاوز الكاش لعرض حيّ.
- **عدّ الرموز (`_token_counter.py`، `BaseCallbackHandler`، يُطلَق مرّة على `on_llm_end`):** ثلاثي: `usage_metadata.total_tokens` (OpenAI/Google) → `prompt_eval_count+eval_count` (Ollama) → **تقدير `(chars)//4`** (ilmu لا يبلّغ استخداماً). ليس بثّاً.
- **التسخين/الصحّة (Ollama فقط):** خيط daemon في lifespan يستطلع `GET {OLLAMA_BASE_URL}/api/version` ثم `POST /api/generate {"prompt":"","keep_alive":…}` (تحميل في VRAM)؛ `/health` يفحص Ollama فقط إن كان أي نموذج محلّياً. **⚠️ `OLLAMA_URL` (اكتشاف/محرّك، افتراضي `127.0.0.1`) ≠ `OLLAMA_BASE_URL` (تسخين/صحّة، افتراضي `localhost`)** — متغيّران لخادم واحد.

## 4) البثّ (Streaming) — **لا وجود له في `alpha`**
بحث شامل: **صفر** `StreamingResponse|astream|on_llm_new_token|event-stream` في كود alpha. كل نداء `.invoke()/.complete()` حاجب يُرجع JSON كاملاً؛ `YtlaiLLM.stream_complete` مزيّف. **لا مرجع سلوكي لـ`LLMProvider.stream() -> AsyncIterator[LlmChunk]`** — بناؤه للمحوّلات الخمسة (OpenAI/Gemini/Claude SSE · Ollama NDJSON `stream:true` · OpenRouter SSE) **عمل جديد صافٍ** (`FR‑90/91` · المرحلة 5.3)، لا هجرة.

## 5) Embeddings (`rag/core/embeddings.py`)
- النموذج `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (**384‑بُعد، متعدّد اللغات AR+EN**)، `HuggingFaceEmbedding(trust_remote_code=True, torch_dtype=float16, truncation max_length=1024, padding=longest)`.
- **التطبيع L2 مُفعّل بالافتراضية** (`normalize=True` افتراضي المكتبة، لا تمرّره alpha صراحةً).
- env: `EMBED_MODEL_NAME` (≠ الثابت `EMB_MODEL_NAME`) · `EMB_DEVICE`(cuda) · `EMB_BATCH`(8) · `EMB_MAX_LEN`(1024) · `EMB_DTYPE`(float16) · `EMBED_CLIENT_CACHE_MAX`(4، LRU مقيّد). ربط لكل فهرس (استعلام يجب أن يطابق نموذج بناء الفهرس). محلّي/بلا مفتاح/GPU — بخلاف توقيع المنفذ `embed(texts, model, api_key)` (انظر §7‑5).

## 6) المطابقة إلى AIZZAK

**يُعاد استخدامه (المعرفة/القيم لا الكود):**
- **عقد نقاط Ollama:** `/api/tags` (اكتشاف) · `/api/version` (صحّة) · معاملات `num_ctx,keep_alive,temperature,num_predict/max_tokens` في `options` الطلب — قيَم حاملة تُنقل حرفياً لمحوّل Ollama الأصلي. **توحيد `OLLAMA_URL`/`OLLAMA_BASE_URL` في `base_url` واحد** في جدول توجيه `DD‑16`.
- **`num_ctx=8192` + مبرّره** (تفادي اقتطاع ~4096 وتفادي تحميل السياق الأصلي الضخم) ⇒ نافذة سياق Ollama الافتراضية (اختر رقماً **واحداً** — alpha متضارب: env مقابل مثبّت).
- **دلالة `keep_alive`** (إقامة VRAM، تُرسل **لكل طلب** لتتجاوز افتراضي الخادم) — معامل محوّل، مرشّح `"30m"` (مؤكَّد عبر 3 قراءات رغم سطر CODEMAP قديم يقول "1m").
- **نمط ilmu «نقطة متوافقة مع OpenAI»** (Bearer + `base_url` override + شكل chat‑completions) = **بالضبط ما يحتاجه OpenRouter** ⇒ يُعاد استخدام الآلية لمحوّل OpenRouter الأصلي (ilmu نفسه خارج قائمة AIZZAK).
- **خوارزمية عدّ الرموز الثلاثية** ⇒ نمط ملء `LlmResult.prompt_tokens/completion_tokens` في `complete()` لكل محوّل، خاصّة طابق «قدّر من الأحرف إن لم يبلّغ المزوّد».
- **`kind=="local"` يتجاوز حلّ المفتاح** (`resolve_key→None`؛ `usable_for` يتخطّى فحص المفتاح) ⇒ قاعدة داخل فرع Ollama من `ProviderResolver`/`CredentialResolver`.
- **أسبقية المفتاح ذات الطابقين «user → platform»** تُطابق `CredentialResolver` (`02 §2`) و`CredentialScope(platform|user)` (`06 §3`) — نفس الشكل والدلالة. (تفاصيل التشفير في [`credentials-oauth-jobs.md`](credentials-oauth-jobs.md).)

**يُسقَط صراحةً (لا يُنقل لمحوّلات AIZZAK):**
- كل كود LangChain/LlamaIndex (`_build_*`, `ChatOllama`, `YtlaiLLM`, `AgentExecutor`) — المحوّلات أصلية (`DD‑13`).
- **نمط «اسم غير معروف → `ollama`»** (`provider_of`/`engine_provider_for`) — يناقض `DD‑16` («لا Fallback بين المزوّدين»). `ProviderResolver` **يفشل مغلقاً** (422) على نموذج غير قابل للحلّ.
- **`temperature=0.3` المثبّت و`max_tokens` غير المحدود** — AIZZAK يملك `LlmParams(temperature=0.7, max_tokens=None, top_p, stop)` ككائن لكل طلب؛ قيَم alpha **نمط مضادّ يُصحَّح**. (يُقرَّر سقف افتراضي لـ`max_tokens` — مخاطرة تكلفة/زمن.)
- **طابق بذرة env `key_env`** (`SERVICE_API_KEY`) كآلية كود — `DD‑11` يمنع قراءة `.env` من أي وحدة؛ «trial جاهز» يأتي من **خطوة ops تبذر `Credential` بنطاق platform عبر Vault Transit** (`secret/data/providers/platform`)، لا `os.getenv` في المُحلِّل.
- **كاشات العملاء غير المحدودة** — مقبولة كتفصيل لكن أضِف تحديداً إن حمل المحوّل عملاء HTTP طويلي العمر.
- **بوّابة السيمافور local/cloud (`inference.py`)** — همّ طبقة تنسيق/`07-nfr-slo`، ليست سطح منفذ `LLMProvider`.

## 7) مخاطر ونقاط قرار
1. **⚠️ البثّ عمل جديد صافٍ** — لا سلوك alpha يُحاكى للمحوّلات الخمسة (§4). يُدرَج ضمن المرحلة 5.3 لا الحصاد.
2. **ilmu/YTLaiLabs خارج قائمة AIZZAK** (`ProviderRef=openai|gemini|claude|ollama|openrouter`). قرار: هل ينتقل دور «نموذج تجريبي دائم» إلى `openrouter` (متوافق OpenAI، الآلية تنتقل) أم يُسقَط في v1؟ يؤثّر على حاجة بذر مفتاح platform.
3. **تضارب توقيع المنفذ مع المحلّي:** `complete(messages, params, api_key: str)` يجعل `api_key` إلزامياً حتى لـollama. قرار: سلسلة فارغة؟ أم `ProviderResolver` يخصّ `kind=="local"` بتخطّي الحلّ (سلوك alpha)؟ أم `api_key` اختياري للمحلّي في المنفذ؟
4. **لا طريقة `discover()`/تعداد نماذج على `LLMProvider`** — تعداد Ollama (`GET /models/ollama`, `discover_ollama`) بلا مقابل. إن لزمت ميزة إدارية «سرد نماذج Ollama المحلّية»: طريقة منفذ جديدة أو نقطة إدارية خارج العقد.
5. **`EmbeddingProvider.embed(texts, model, api_key)` يفترض شكلاً مفتاحياً/سحابياً.** مسار alpha الوحيد محلّي HuggingFace (بلا مفتاح، GPU). **مفتوح:** هل يشحن v1 محوّل embedding محلّياً (فتُعاد قيَم §5) أم السحابة فقط (فالوحدة خارج نطاق هذا المرجع وتتبع حصاد `knowledge`)؟
6. **الحارس السحري `model_name=="rag"`** («اتبع النموذج الافتراضي للمحرّك») في موضعين — AIZZAK يملك `resolve_llm(ctx, *, capability, model=None)` عبر `None`؛ يُوصى بإسقاط نمط السلسلة السحرية.
7. **⚠️ علّة ابتلاع استثناء ilmu كنصّ إجابة** — `complete()` في AIZZAK يجب أن **يرفع/يُرجع خطأً مطبوعاً** لا محتوى مساعد ملفّقاً.
8. **كشف القدرات** (`supports('vision'/'tool_calling'/'streaming')`) — alpha يملك boolean ثابتاً لكل مزوّد أو allow‑list لكل نموذج (`OLLAMA_TOOL_MODELS`)؛ لا علم رؤية/بثّ لكل نموذج ⇒ **جدول القدرات يُؤلَّف من جديد** (لا يُحصَد).
