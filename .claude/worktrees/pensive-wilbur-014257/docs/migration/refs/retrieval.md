# refs/retrieval.md — محرك الاسترجاع (Retrieval) — مرجع من `alpha`

> **الوجهة:** `06-domain-models.md §7` (`knowledge` — `RetrieveContext(workspace, query, k)`) · `02-port-contracts.md §1.5` (`VectorStore`) و§2 (`KnowledgeRetrieval`) · `11-agent-authoring-guide.md` (`rag_agent`/`RagSearchTool`) · `01-data-model.md §2.7` (`knowledge.documents/chunks`).
> **مصادر `alpha`:** `rag/retrieval/*` (router · intent_router · query_pipeline · hybrid_retriever · reranking · cross_encoder_reranker · relevance_filter · parent_chunk_postprocessor · document_registry/summarizer · file_resolver) · `rag/core/{llm,embeddings,storage}.py` · `rag/indexing/{builder,node_builder,bm25_builder,bm25_tokenizer,table_processor,manifest}.py` · `rag/config/{settings,bm25_settings,runtime_config}.py` · `rag/api/{app,utils,schemas}.py` · `integrations/rag_manager.py` (جسر backend↔engine).
> **لم تُنسخ أي قيمة سرّية** (`SERVICE_API_KEY`, `.env`) — تُذكر أسماء المتغيّرات فقط.

## 1) خلاصة سريعة (TL;DR)
`alpha` تنفّذ استرجاعاً **هجيناً محلياً بالكامل** (بلا قاعدة وسيطة): FAISS (`IndexHNSWFlat`, L2) للـdense + `rank-bm25` داخل العملية (pickle على القرص) للـsparse، دمج بـ**RRF يدوي**، إعادة ترتيب اختيارية بـcross-encoder، وتوسيع «القطعة الأم» (small-to-big) عبر ملف جانبي `parent_chunks.json`. كل هذا **لكل محادثة** (مجلد بيانات + فهرس مستقل لكل `(uid, agent_id)`)، بلا مفهوم Workspace.
عقد AIZZAK (`VectorStore`/`KnowledgeRetrieval`) يغطّي **بحثاً كثيفاً فقط** (`vector: list[float]`, `search(collection, vector, k, flt)`) ولا يذكر: BM25/sparse · RRF · rerank · parent-chunk. **فجوة تصميم حقيقية** — §6‑7.

## 2) التدفّق الكامل للاستعلام (End-to-end)
```
route_query(index, query, registry, overrides, llm, persist_dir)        [router.py]
  ├─ classify_intent(query, use_llm)                                     [intent_router.py]
  │     ├─ METADATA      → _metadata_flow(query, registry)               بلا استرجاع ولا LLM
  │     ├─ SUMMARIZE_DOC → _summarize_doc_flow(...)                      [§4.10]
  │     └─ CONTENT (افتراضي) →
  ├─ _scope_to_file(query, registry)?  → doc_filter (اختياري)           [§4.4]
  └─ run_query(index, query, overrides, llm, doc_filter, corpus_files, persist_dir)  [query_pipeline.py]
        1. cfg = RUNTIME.merged(overrides)
        2. EnhancedHybridRetriever(vector_index, bm25_index, cfg).retrieve(query, doc_filter=)
               ├─ _retrieve_dense()  → FAISS + MMR                       [§4.3]
               └─ _retrieve_bm25()   → rank_bm25.get_top_n               [§4.2]
        3. reciprocal_rank_fusion(dense, bm25, k, weight_dense, weight_bm25, rrf_k)  [§4.1]
        4. RelevanceFilter.filter(nodes)                                 [§4.8]
        5. postprocess: ParentChunkReplacement أو MetadataReplacement(window)  [§4.5]
        6. rerank_nodes(query, nodes)  إن enable_reranker                [§4.6]
        7. sort(desc score) + nodes[:final_top_n]
        8. بوابة سقوط (fallback) أو 9. _build_context_prompt(...) → llm.complete(prompt)  [§4.9]
```
**كل التوابع بلا حالة عامة** (de-globalized منذ Phase 1/2): `index`/`registry`/`llm`/`persist_dir` تُمرَّر صراحةً من الجسر `rag_manager.answer(uid, agent_id, question, …)` الذي يحمّل `ConversationContext` لكل محادثة. المسار الوكيلي (`kind='rag'` + نموذج tool-calling) يستدعي `run_query(..., synthesize=False)` عبر أداة `search_documents`، فيُعيد `context_text` بلا LLM محرّك — الوكيل الخارجي (LangChain) يولّف الإجابة.

## 3) التوقيعات العامة (الأهمّ، حرفياً)
```python
# router.py
def route_query(index, query, registry=None, overrides=None, llm=None, persist_dir=None) -> Dict  # {"answer","nodes","metrics"}
def _scope_to_file(query, registry) -> "tuple[doc_id,file_name,method] | None"
def _index_embed_model(index)  # يقرأ index["faiss"]._embed_model (مربوط بهذا الفهرس)
# intent_router.py
class Intent(str, Enum): CONTENT="content"; SUMMARIZE_DOC="summarize_doc"; METADATA="metadata"
def classify_intent(query, use_llm=None) -> Intent
@lru_cache(maxsize=512)
def classify_intent_llm(query_norm) -> Optional[Intent]   # ⚠️ يستخدم Settings.llm العمومي — §7‑5
# query_pipeline.py
def run_query(index, query, overrides=None, synthesize=True, llm=None, doc_filter=None, corpus_files=None, persist_dir=None) -> Dict
_FALLBACK_ANSWER = "I could not find information relevant to this question in the indexed documents, ..."
# hybrid_retriever.py
_SCOPE_FETCH_MULT = 4
class EnhancedHybridRetriever:
    def __init__(self, vector_index, bm25_index=None, cfg=None)
    def retrieve(self, query, cfg=None, recorder=None, doc_filter=None) -> Dict
      # {"fused_nodes","dense_nodes","bm25_nodes","best_dense_score","best_dense_distance","best_bm25_score","recorder"}
# reranking.py
def reciprocal_rank_fusion(dense_nodes, bm25_nodes, k, weight_dense=0.5, weight_bm25=0.5, rrf_k=60) -> List[NodeWithScore]
# cross_encoder_reranker.py
def rerank_nodes(query, nodes, model_name=None, top_n=None, device="cpu") -> List[NodeWithScore]
# relevance_filter.py
class RelevanceFilter:
    def __init__(self, min_score=None, relative_floor=0.0, remove_duplicates=True, verbose=False)
    def filter(self, nodes) -> List[NodeWithScore]   # Jaccard dedup عتبة 0.95 ثابتة
# parent_chunk_postprocessor.py
class ParentChunkReplacementPostProcessor:
    def __init__(self, metadata_key="original_chunk_text", id_key="parent_chunk_id", dedup=True, max_chars=0, verbose=True, persist_dir=None)
# file_resolver.py
def resolve_file(query, registry, embed_model=None, max_candidates=5) -> Dict  # status: match|candidates|none
_HIGH,_BAND,_LOW = 0.75,0.10,0.40   # عتبات FUZZY ؛ SEMANTIC: best<0.45→none، tied[best-s<=0.05]، len==1∧best>=0.6→match
# indexing/bm25_*.py
class BM25Index:  def get_top_n(self, query, n=10) -> List[tuple]   # [(node_id, score), ...]
def tokenize(text, tokenizer_type="multilingual") -> List[str]      # @lru_cache(1000)
def normalize_arabic(text) -> str ; def detect_language(text) -> str  # arabic|english|mixed|unknown
# config/runtime_config.py
RUNTIME = _Runtime()  # .get/.snapshot/.merged(overrides)/.update/.attach_sync(redis) ; TUNABLE_KEYS/REBUILD_KEYS/ENV_ONLY_KEYS
# integrations/rag_manager.py (الجسر — للفهم، ليس جزء rag/)
def conv_dirs(uid, agent_id) -> "tuple[data_dir, storage_dir, persist_dir]"   # <DATA>/<uid>/<agent_id>, ...
def answer(uid, agent_id, question, history=None, model_name=None, trace=None) -> dict  # {"answer","tokens_used":0,"sources"}
def make_doc_search_tool(uid, agent_id, trace=None) -> StructuredTool          # search_documents(query, file=None)
```

## 4) الخوارزميات بالتفصيل

### 4.1 RRF (Reciprocal Rank Fusion) — `reranking.py`
لكل معرّف عقدة فريد `n`، `r_d(n)`/`r_b(n)` = رتبة صفرية-الأساس (الأفضل أولاً):
```
RRF(n) = weight_dense · 1/(rrf_k + r_d(n) + 1)   [إن وُجد n في dense]
       + weight_bm25  · 1/(rrf_k + r_b(n) + 1)   [إن وُجد n في bm25]
```
- `rrf_k=60` (تخميد؛ env `RRF_K`). `weight_dense/bm25` (0.5/0.5) **يُطبَّعان لمجموع 1 في `_fuse`** لا داخل الدالة.
- **⚠️ فخّ تسمية:** بارامتر `k` في التوقيع = **عدد النتائج بعد القطع** (`k = max(final_top_n*3, final_top_n)`)، **مختلف تماماً** عن `rrf_k` التخميد.
- **⚠️ حالتا حافّة:** إن كانت إحدى القائمتين فارغة، **لا رياضيات RRF إطلاقاً** — تُعاد الأخرى كما هي (`[:k]`) بـ`node.score` **الخام** (L2 distance أو BM25 raw). أي أن دلالة `node.score` بعد الدمج **ليست ثابتة**. قرار واعٍ مطلوب عند إعادة البناء في Qdrant.
- إزالة تكرار طبيعية (نفس `node_id` تُجمَع درجتاه، أسبقية عرض dense). الترتيب: `sort(desc)` ثم `node.score = rrf_score` (**يكتب فوق الخام** — أي دمب debug يجب أن يسبق).

### 4.2 BM25 (Sparse) — `bm25_builder.py`/`bm25_tokenizer.py`
- المكتبة `rank-bm25` (`BM25Okapi`، بلا إصدار مثبَّت). `k1=1.2` (env `BM25_K1`, 0–3)، `b=0.75` (env `BM25_B`, 0–1). صيغة Okapi القياسية (المكتبة تطبّقها).
- **المُقطِّع:** `BM25_TOKENIZER="multilingual"` (أو `arabic`/`english`). في multilingual **يُكتَشف لغة كل نصّ** (`detect_language`: نسبة عربي/(عربي+إنجليزي) `>0.6`→عربي، `<0.3`→إنجليزي، وإلا مختلط → **يُشغَّل المُقطِّعان ويُدمَجان** مع dedup).
  - تطبيع عربي (`BM25_NORMALIZE_ARABIC=true`): توحيد الألف `إأآا→ا`، الهمزة `ؤئ→ء`، حذف تشكيل `[ً-ٰٟ]`، تاء مربوطة→هاء، ياء→ي. ثم `re.findall(r'[؀-ۿ\w]+', text.lower())`.
  - إيقاف الكلمات `BM25_REMOVE_STOPWORDS=true` (قوائم عربي/إنجليزي مضمَّنة ~60 لكلّ). الجذعنة `BM25_STEMMING=false` (Porter، إنجليزي فقط، تسقط بصمت بلا nltk). حدّ طول الرمز `>1` ثابت.
- الحفظ pickle+gzip (`BM25_USE_COMPRESSION=true`) إلى `<persist_dir>/bm25/index.pkl.gz` + `metadata.json`.
- **ختم البصمة:** `tokenizer/k1/b` الفعليّة تُختَم في `bm25_index.metadata`؛ الاستعلام يُقطِّع بـ`metadata["tokenizer"] or BM25_TOKENIZER` ⇒ **تغيير env لا يؤثّر على فهرس قائم** (يمنع تعارض استعلام/فهرس).
- **`BM25_ENABLED`** (env، افتراضي true) **ثابت وقت الاستيراد — ليس ضمن RUNTIME**؛ يتحكّم بالبناء+التحميل+التفعيل (`have_bm25 = bm25_index is not None and BM25_ENABLED`). **مختلف** عن `use_fusion` (RUNTIME حيّ) الذي يتحكّم بكيفية **الدمج** فقط.

### 4.3 الاسترجاع الكثيف (Dense / FAISS)
- `faiss.IndexHNSWFlat(dim=384, M=32)`, `efConstruction=200`, `efSearch=64`، مُغلَّف بـ`FaissVectorStore` + `SimpleDocumentStore`.
- **المقياس L2 (إقليدي) — الأصغر = الأقرب** (ليس جيب تمام). كل العتبات مبنية على هذا — قلبه يكسر كل شيء.
- التضمين `paraphrase-multilingual-MiniLM-L12-v2` (env `EMBED_MODEL_NAME`)، **384‑بُعد**، AR+EN، fp16، دفعة 8، `max_length=EMB_MAX_LEN`.
- **⚠️ ربط لكل فهرس (حرِج):** النموذج **يُختَم في `manifest.json`** وقت البناء (من `Settings.embed_model` الحيّ)؛ `load_index` يقرأ الختم ويربطه عبر `get_embed_client(stamped)` (كاش LRU `EMBED_CLIENT_CACHE_MAX=4`، مفتاح `"<model>@<device>"`). **فشل = فشل مغلق (`raise`)** لا رجوع صامت لبُعد مختلف. **لا تبديل حيّ للنموذج**.
- **MMR:** `as_retriever(similarity_top_k=fetch_top_k, vector_store_query_mode="mmr", vector_store_kwargs={"mmr_threshold":mmr_lambda, "fetch_k":fetch_k})`.
  - **⚠️ فخّ تسمية:** `similarity_top_k` هنا = **العدد المُعاد بعد اختيار MMR**، لا عمق الجلب؛ العمق الحقيقي `vector_store_kwargs["fetch_k"]`. و`mmr_lambda` يُمرَّر باسم `mmr_threshold`. افتراضياً `dense_top_k=12, fetch_k=40, mmr_lambda=0.5`.
  - **FAISS لا يدعم MMR أصلاً** — تنويع LlamaIndex فوق الجلب الخام. **لا فلترة ميتاداتا أصلية** (سبب حيلة doc_filter §4.4). لا عتبة مطلقة افتراضياً (`min_dense_score=0.0`؛ إن ضُبطت تُطبَّق post-hoc).

### 4.4 تقييد نطاق الوثيقة (doc-scope)
موحّد في 3 مواضع: `router._scope_to_file`، أداة الوكيل، `hybrid_retriever`.
- **لا فلترة ميتاداتا أصلية** → **over-fetch×4** (`_SCOPE_FETCH_MULT=4`): كلا الساقين يجلبان `top_k×4` (dense يرفع `fetch_k` أيضاً)، ثم `_keep_doc(nodes, doc_id)` يُقلِّم، ثم `[:top_k]`.
- **اسم ملف → `doc_id`** عبر `resolve_file` (EXACT→FUZZY→SEMANTIC). في router: **طبقات معجمية فقط** (`embed_model` غير ممرَّر) + يتطلّب `has_file_reference(query)` **و** تطابق `"match"` واحد بثقة — أي غموض/فشل ⇒ **بلا تقييد** (استرجاع كل الكوربَس). مسار SUMMARIZE_DOC وحده يمرّر `embed_model` (SEMANTIC مفعَّلة).
- `doc_filter` **صارم** (hard): لا شيء ذو صلة ⇒ سقوط عادي، **لا** سحب من ملفات أخرى.

### 4.5 القطعة الأم / small-to-big (parent-chunk)
1. **وقت الفهرسة** (`node_builder.build_node_views`): لكل `Document` أصلي (قبل التقسيم) يُختَم `parent_chunk_id` (الموجود؛ وإلا `doc_id`؛ وإلا `f"{file_name}|{chunk_type}|{chunk_index}"`) ويُحفَظ نصّه في `parent_text_map[pid]`. المعرِّف **يُورَّث** عبر مراحل التقسيم الثلاث لأن كل مرحلة تُنشئ `Document` من سابقتها حاملةً metadata.
2. **صفوف الجداول** (`table_processor.split_table_doc`): جداول صغيرة (`<= TABLE_PARENT_MAX_ROWS=20`) تحمل `original_chunk_text` + `parent_chunk_id=f"{doc_id}#table"` inline، يُحصَد لاحقاً ثم يُحذَف `original_chunk_text` (`md.pop`) لتفادي تكرار ×N.
3. **الحفظ:** ملف واحد `<persist_dir>/parent_chunks.json = {pid: full_text}` — **مرّة لكل بناء** (توفير مساحة كبير عند تشعّب عقدة أم لعشرات الورقية).
4. **وقت الاستعلام** (`ParentChunkReplacementPostProcessor`، `REPLACE_WITH_PARENT_CHUNK=true`): يُحمَّل `parent_chunks.json` **من persist_dir لهذه المحادثة فقط** (لا احتياط عمومي؛ كاش بـmtime لكل مسار). تُستبدَل `node.text` بنصّ الأمّ، مع **dedup بمعرِّف الأمّ** (الأعلى ترتيباً يبقى). سقف `MAX_PARENT_CHUNK_CHARS=4000` (+علامة اقتطاع).
5. بديل أخفّ إن غاب: `MetadataReplacementPostProcessor(target_metadata_key="window")` (نافذة `±SENTENCE_WINDOW_SIZE=3` جُمَل).
6. **إعادة استخدام ثانٍ:** نفس `parent_chunks.json` يخدم `document_summarizer._doc_blocks()` (تلخيص ملف كامل، §4.10).
7. **⚠️ ترتيب:** التوسّع يحدث **قبل** rerank و**قبل** قصّ `final_top_n` — أي المُعاد ترتيبه هو **النص الموسَّع الكامل** (تكلفة CPU أعلى إن كان السقف كبيراً).

### 4.6 إعادة الترتيب (Reranking) — `cross_encoder_reranker.py`
- **معطَّل افتراضياً** (`ENABLE_RERANKER=false`)، تحميل كسول. النموذج `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (متعدد اللغات، `max_length=512`). **الجهاز CPU** افتراضياً (تفادي تنافس VRAM). كاش عملية-عمومي `(model_name, device)`.
- `rr_top_n = max(reranker_top_n or final_top_n, final_top_n)` — **لا يجوّع** السياق النهائي. يعمل **بعد** فلتر الصلة والتوسّع، **قبل** القصّ.

### 4.7 تصنيف النيّة (Intent) — `intent_router.py`
- **الطبقة 1 (دائماً، بلا LLM):** regex عربي+إنجليزي، الأولوية METADATA→SUMMARIZE_DOC→CONTENT.
  - أنماط METADATA: `"كم عدد"`,`"كم ملف"`,`"عدد الملفات"`,`"اعرض الملفات"`,`"how many (files|documents|docs)"`,`"list (the )?(files|...)"` …
  - **حارس الشرط الموضوعي:** METADATA + كلمة شرط ("عن/حول/يتحدث/about/regarding/mentions…") ⇒ **يُخفَّض لـCONTENT** («كم ملف يتحدث عن الرواتب؟» سؤال محتوى عابر).
  - SUMMARIZE_DOC: كلمات تلخيص صريحة أو صياغة يكون فيها الملف مفعول السؤال (`"ما هو ملف X"`) — لتفادي تصنيف سؤال يذكر ملفاً عرضاً.
- **الطبقة 2 (اختيارية، `intent_llm=False`):** تُستشار **فقط** عند ترجيح CONTENT + `has_file_reference`. موجِّه: «Reply with ONLY the label word: CONTENT, SUMMARIZE_DOC, or METADATA». مكاشَف `@lru_cache(512)`.
  - **⚠️ عيب:** تستدعي `Settings.llm` **العمومي** لا عميل المحادثة الممرَّر — عكس بقية المسار de-globalized. غير موثَّق كقرار ⇒ إغفال. **لا يُعاد إنتاجه في AIZZAK** (§7‑5).

### 4.8 فلتر الصلة والسقوط
1. عتبة مطلقة `min_score` (`min_relevance_score`, 0.0 معطّل). 2. نسبية `relative_floor` (`× max(scores)`, 0.0 معطّل). 3. **dedup شبه-تكرار:** Jaccard على مجموعات الكلمات، **عتبة ثابتة 0.95** غير قابلة للضبط، `O(n²)`.
4. **بوابة السقوط** (`run_query`): `weak = (gate>0) and (best_dense_distance>gate) and (best_bm25<=0.0)` مع `gate=fallback_max_distance` (0.0 معطّل)؛ `if not nodes or weak → _FALLBACK_ANSWER` بلا LLM. (`best_dense_distance` = **أصغر** مسافة L2، الأقرب.)

### 4.9 تجميع السياق والتوليف
- تسمية المقطع: `[{file_name} p.{page}|sheet:{name} | section: {title}]` (`_page_label`).
- ميزانية: `char_budget = min(max_context_chars, max_context_tokens×chars_per_token)` إن tokens>0 وإلا chars فقط. افتراضي `12000 / 6000 / 4.0` ⇒ `min(12000,24000)=12000`. تجاوز ⇒ قصّ + علامة.
- رأس الموجِّه (سلوكي): «أجب من CONTEXT فقط، اجمع من كل الأقسام (لا واحد)، أدرج كل عناصر القائمة، استشهد بالملف/القسم، بلا سرد تفكير، إن غاب الجواب قل ذلك» + سطر «Files indexed…» (أول 15 + `+N more`).
- الاستدعاء `llm.complete(prompt)` (عميل المحادثة، لا Settings.llm). **⚠️ عند الاستثناء:** `answer=f"[LLM error: {e}]"` **يُعاد كنجاح** — يُراجَع (مغلَّف خطأ صريح أفضل).
- **وضع الاسترجاع فقط** (`synthesize=False`، المسار الوكيلي): يعيد `context_text` (`_build_context_only`، مصدر حقيقة مشترك) بلا LLM محرّك.

### 4.10 مسار SUMMARIZE_DOC (تلخيص ملف كامل)
مختلف جذرياً — **لا بحث تشابه**. 1. `fetch_doc_nodes` مسح مباشر لـ`docstore.docs` بفلتر `doc_id`، ترتيب بـ`seq`. 2. `summarize_nodes` إعادة تجميع من القطع الأم (`_doc_blocks`+`_load_parent_map`) لا الورقية؛ ميزانية `(ctx_tokens=8192 - 2048)×4 ≈ 24576` حرفاً؛ تمريرة واحدة أو `summary_strategy` (`map_reduce` افتراضي / `refine`)، طيّ تكراري بحدّ `summary_max_fold_depth=3`. `ollama_num_predict=1024` يحدّ كل مخرج **عمداً** (بدونه Ollama يعيد كتابة النص فلا تتقارب المرحلة). 3. **كاش بصمة:** `summary_fingerprint==fingerprint` ⇒ إرجاع مخزَّن بلا LLM؛ لغات مختلفة تُترجَم وتُخزَّن في `entry["i18n"]`. 4. `"candidates"` ⇒ سؤال توضيح بلا LLM؛ `"none"` ⇒ سقوط لـCONTENT.

## 5) الإعداد والتبعيات
**المكتبات:** `llama-index==0.13.5` (+core/ollama/huggingface/`vector-stores-faiss`) · `sentence-transformers==5.1.2` · `rank-bm25` (بلا تثبيت) · `faiss-cpu`|`faiss-gpu-cu12` · `torch` (خارجي) · `httpx` (YtlaiLLM) · `python-dotenv`.

**الفئة 1 (حيّة عبر `RUNTIME`/`POST /v1/config/retrieval`، بلا إعادة فهرسة):**

| مفتاح RUNTIME | env | افتراضي |
|---|---|---|
| `dense_top_k` | `SIMILARITY_TOP_K` | 12 |
| `bm25_top_k` | `BM25_TOP_K` | 20 (.env المشحون=40) |
| `fetch_k` | `FETCH_K` | 40 |
| `mmr_lambda` | `MMR_LAMBDA` | 0.5 |
| `final_top_n` | `FINAL_TOP_N` | 8 |
| `use_fusion` | `USE_FUSION` | true |
| `weight_dense`/`weight_bm25` | `WEIGHT_DENSE`/`WEIGHT_BM25` | 0.5/0.5 (تُطبَّع لـ1) |
| `rrf_k` | `RRF_K` | 60 |
| `min_dense_score` | `MIN_DENSE_SCORE` | 0.0 (خام L2) |
| `min_bm25_score` | `MIN_BM25_SCORE`←`BM25_MIN_SCORE` | 0.1 |
| `bm25_max_results` | `BM25_MAX_RESULTS` | 50 |
| `min_relevance_score` | `MIN_RELEVANCE_SCORE` | 0.0 |
| `relevance_relative_floor` | `RELEVANCE_RELATIVE_FLOOR` | 0.0 |
| `remove_duplicates` | `REMOVE_DUPLICATES` | true (Jaccard 0.95 ثابتة) |
| `fallback_max_distance` | `FALLBACK_MAX_DISTANCE` | 0.0 (معطّل) |
| `max_parent_chunk_chars` | `MAX_PARENT_CHUNK_CHARS` | 4000 |
| `enable_reranker` | `ENABLE_RERANKER` | false |
| `reranker_model` | `RERANKER_MODEL` | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` |
| `reranker_top_n` | `RERANK_TOP_N` | 3 (يُرفَع لـfinal_top_n) |
| `reranker_device` | `RERANKER_DEVICE` | cpu |
| `intent_llm` | `INTENT_LLM` | false |
| `summary_strategy` | `SUMMARY_STRATEGY` | map_reduce |
| `summary_max_fold_depth` | `SUMMARY_MAX_FOLD_DEPTH` | 3 |
| `ollama_num_predict` | `OLLAMA_NUM_PREDICT` | 1024 |
| `ollama_keep_alive` | `OLLAMA_KEEP_ALIVE` | 30m |
| `max_context_chars`/`max_context_tokens` | `MAX_CONTEXT_CHARS`/`MAX_CONTEXT_TOKENS` | 12000/6000 |

**ENV-ONLY (حيّة عبر `merged()` لكن مخفيّة عن الإدارة):** `chars_per_token=4.0` · `replace_with_parent_chunk=false` · `save_final_context`/`final_context_dir`.

**الفئة 2 (تتطلّب `POST /v1/reindex`):** `bm25_k1`/`bm25_b`/`bm25_tokenizer`/`bm25_*stopwords/stemming/normalize_arabic` · `disable_hierarchical/semantic/sentence_window` · `hierarchical_chunk_sizes="2048,512,128"` · `sentence_window_size=3` · `min_node_chars=15` · `table_parent_max_rows=20` · `emb_max_len=1024` (.env المشحون=256) · `emb_dtype/emb_batch` · `faiss_enabled=true` · `precompute_summaries=false`.

**إعادة تشغيل فقط (خارج RUNTIME):** `BM25_ENABLED=true` · `EMBED_MODEL_NAME` (مختوم لكل فهرس، لا تبديل حيّ) · `EMB_DEVICE=cuda` · `EMBED_CLIENT_CACHE_MAX=4` · `OLLAMA_URL=http://127.0.0.1:11434` · `LLM_PROVIDER=ollama` · `OLLAMA_MODEL/DEFAULT_MODEL=llama3.2:1b` · `SERVICE_API_KEY` (سرّ ilmu) · `DATA_DIR/STORAGE_DIR/PERSIST_DIR`.

## 6) المطابقة إلى AIZZAK

**6.1 يُنقَل كخوارزمية بحتة (تُنسخ الصيغة/الثوابت):**
- **صيغة RRF بالكامل** (§4.1) — صالحة فوق أي قائمتَي `(id, rank)`؛ تُنسَخ 1:1 إن قُرِّر دمج هجين تطبيقي.
- **معاملات BM25** (`k1=1.2, b=0.75`) + قواعد المُقطِّع العربي/الإنجليزي (تطبيع، إيقاف، اكتشاف لغة) — تُنقَل حرفياً بصرف النظر عن المحرّك.
- **فلتر الصلة** (مطلق→نسبي→Jaccard) وبوابة السقوط — منطق تطبيقي صرف (يحتاج إعادة معايرة أرقام، §7‑6).
- **قواعد النيّة** (regex + حارس الشرط) — نصّي صرف. **ميزانية السياق** ونمط الاستشهاد — قابل للنقل للوكيل (`11`).

**6.2 يُبنى فوق العقود لكن يحسّنها:**
- **doc-scope** (§4.4): over-fetch×4 كان ضرورة غياب فلترة ميتاداتا في FAISS/rank-bm25. عقد `VectorStore.search(collection, vector, k, flt)` **يدعم فلترة payload أصلية** (`flt` يحمل `workspace_id` دائماً، `02 §1.5`) ⇒ تمرير `document_id` كشرط `must` = تقييد دقيق **بلا over-fetch**. تبسيط حقيقي.

**6.3 خاصّ بـ`alpha` — يحتاج قراراً/تصميماً جديداً:**
- **الفهرس نفسه:** `IndexHNSWFlat` + pickle محلي لكل محادثة — لا معنى له في AIZZAK (Postgres+Qdrant مشتركان معزولان بـ`workspace_id`). يُستبدَل بالكامل بمحوّل `QdrantAdapter`.
- **`parent_chunks.json`** (§4.5): خارج المخطّط. `knowledge.chunks` (`01 §2.7`) **مسطَّح** (`id, document_id, workspace_id, seq, text, token_count, collection, point_id`) — **لا عمود** لـ`parent_chunk_id`/نصّ الأمّ. توسيع small-to-big **غير ممكن بلا تغيير مخطّط**: (أ) جدول `knowledge.parent_chunks` + تكرار في Payload نقطة Qdrant، أو (ب) إسقاطه في v1 (أبسط، يفقد مكسب جودة أثبتته alpha).
- **التقسيم متعدّد المشاهد** (Hierarchical→Semantic→SentenceWindow) — تفصيل بناء (مُغطًّى في `parsers.md §3`) لكنه **مصدر** `parent_chunk_id`/`seq`.
- **لوحة `RUNTIME` الحيّة + مزامنة Redis** — لا معادل في `05`؛ النمط (لا القيم) صالح لكن يحتاج تكييفاً متعدّد-المستأجرين (Workspace/منصّة لا Singleton).
- **rerank بـCPU مضمَّن** — قرار نشر صريح في بنية أفقية (تحميل لكل Worker أم خدمة مشتركة).

**6.4 مطابقة `KnowledgeRetrieval`/`RagAgent`:** `async retrieve(ctx, query, k) -> list[RetrievedChunk]` **لا يحمل** `doc_filter/intent/synthesize/corpus_files` ⇒ `RagAgent.run()` الحالي **مسطَّح** (بلا نيّة/تقييد/توسيع). لنفس مكاسب alpha: (أ) توسيع `KnowledgeRetrieval` (`count_documents`, `summarize_document`) + موجِّه نيّة داخل `rag_agent`، أو (ب) CONTENT-only في v1. `RetrievedChunkOut{document_id, chunk_id, text, score}` (`03`) يقابل `Source` في alpha لكن alpha تُرجِع أيضاً `file_name/page_label/preview` (تجربة استشهاد) **غير موجودة** بالعقد.

## 7) مخاطر ونقاط قرار
1. **⚠️ فجوة تصميم رئيسية — الهجين غير موجود في العقد:** `VectorPoint{id, vector, payload}` كثيف فقط، بلا حقل sparse. RRF جاهز لكن **لا مكان لتخزين/استعلام إشارة BM25 في Qdrant حسب العقد**. قرار: (أ) توسيع `VectorStore`/`VectorPoint` لمتّجهات sparse (Qdrant الحديث يدعم sparse + Query API بدمج RRF/DBSF خادمي — يحتاج تأكيد إصدار Qdrant المنشور)، أو (ب) dense فقط في v1 (يتخلّى عن دقّة المطابقة اللفظية/الأرقام/أسماء العلم).
2. **⚠️ مخطّط payload/parent-chunk غير محسوم** (نفس نقطة `parsers.md`، مؤكَّدة من زاوية الاسترجاع). يقرِّر هل تُنقَل small-to-big أصلاً.
3. **لوحة الضبط الحيّ غير مذكورة في `05`** — إن أُريد ضبط `top_k`/الأوزان/العتبات بلا إعادة نشر، النمط صالح لكن يحتاج تكييفاً متعدّد-المستأجرين.
4. **⚠️ ميزانية SLO (`07`): استرجاع RAG p50 120ms / p95 400ms / p99 800ms** لا تشمل — إن نُقل الهجين كما هو — BM25 محلي + RRF يدوي + rerank (عشرات ms/CPU) + قراءة `parent_chunks.json`. **قد يتجاوز الهجين الكامل p95=400ms** ⇒ قياس فعلي أو تبسيط (rerank معطّل افتراضياً كما في alpha).
5. **✅ عيب يستحق الانتباه:** طبقة النيّة بـLLM (§4.7) تستخدم `Settings.llm` العمومي بدل عميل المحادثة، خلافاً للمسار de-globalized. **لا تُكرَّر** — موجِّه النيّة في `rag_agent` يجب أن يستخدم `LLMProvider` المحقون (`deps.llm`) لا مورِّداً مشتركاً.
6. **⚠️ مقياس L2 غير مُعايَر لأي بُعد/نموذج جديد:** كل عتبات alpha (`fallback_max_distance`, `min_dense_score`, …) لـ**L2 خام 384‑بُعد MiniLM**. `VectorStore.ensure_collection(name, dim, distance='cosine')` (العقد يقترح **جيب تمام** لا L2!) ⇒ **لا يُنسَخ أي رقم عتبة كما هو** — إعادة معايرة تجريبية كاملة بعد اختيار البُعد/المسافة.
7. **حتمية `Chunk.seq` (`INV‑K1`) غير مضمونة:** `_stamp_doc_order` يشتقّ `seq` من ترتيب معالجة + إشارات صفحة — لا hash محتوى. إعادة بناء بترتيب مختلف قد تُنتج `seq` مختلفاً ⇒ يخالف `INV‑K1/K3`. قاعدة ترتيب حتمية صريحة مطلوبة (نفس `parsers.md`).
8. **لا-حتمية طفيفة في تعادل RRF:** `set(...)` غير مضمون الترتيب عبر العمليات عند تساوي درجتَي RRF تماماً؛ `sort` مستقرّ لكن يحفظ ترتيب دخل غير محدَّد. أثر ضئيل لكن يُذكَر لأي اختبار تراجعي يقارن الترتيب حرفياً.
9. **نشر نموذجَي rerank/embed في بنية أفقية:** alpha عملية واحدة بـGPU (كاش عملية-عمومي). AIZZAK أفقي ⇒ قرار: تحميل لكل Worker أم خدمة مركزية خلف `EmbeddingProvider` (العقد يعزل التضمين كمزوّد خارجي أصلاً — إيجابي، بعكس alpha).
10. **رخصة/تبعيات ثقيلة (مكرَّر من `parsers.md`):** `sentence-transformers` (rerank) تضيف `torch` قد يتكرّر مع أعباء استدلال أخرى — نفس ملاحظة الترخيص/الحجم على نموذج rerank.
