# refs/tools.md — أدوات `web_search` و`calculator` (مرجع من `alpha`)

> **الوجهة:** `11-agent-authoring-guide.md §4` · `02-port-contracts.md §3.1` (`BaseTool`).
> **النطاق:** الأداتان القابلتان للاستبقاء فقط. أدوات `weather/currency/news/sports/gmail` **خارج نطاق v1** (متجاهَلة).
> **مصادر `alpha`:** `services/tools/{__init__,web_search,calculator}.py` · `services/cache.py` · `services/langchain_service.py`.

## 0) واقع `alpha` (السياق)
- **لا توجد فئة أداة أساسية ولا سجلّ** في `alpha`. الأدوات كائنات **LangChain** (`StructuredTool.from_function` أو `@tool`)، و«السجلّ» قائمة يدوية `_BASE_TOOLS`، والاستدعاء عبر `create_tool_calling_agent` + `AgentExecutor` (بـ`max_iterations=5`, `max_execution_time=45`).
- في AIZZAK يُستبدَل ذلك بعقد `BaseTool(ABC)` + `ToolSpec` + `ToolRegistry` (ثابتة) / `ToolCatalog` (ديناميكية للموصّلات/MCP). **لا LangChain.**

## 1) `web_search` (اسم الأداة الفعلي: `exa_search`)
**المزوّد:** Exa (SDK `exa_py`، `Exa.search_and_contents`). **التوقيع:** `_web_search(query: str) -> str`.
**التدفّق (`web_search.py`):**
1. قراءة الكاش: `cache.search_get(query)` ⇒ إرجاع فوري عند الإصابة.
2. نداء Exa: `type="auto"`, `num_results=5`, `text={"max_characters":800}`, `highlights={"num_sentences":3,"highlights_per_url":2}` — داخل `try/except` (فشل ⇒ `"No search results found. (e)"`).
3. تشكيل النتيجة لكل عنصر (`getattr` دفاعي لـ`url,title,text,highlights`): **إزالة تكرار حسب URL**؛ `snippet = " … ".join(highlights[:2])` وإلا `text[:350]`؛ إن `len(snippet)<30` ⇒ رجوع لـ`text[:350]` وإلا **تخطّي**؛ سطر `[{i}] {title}\n    {url}\n    {snippet[:500]}`.
4. تجميع نصّي (رأس `Search results for: <q>` + خط 60×`=` + الأسطر + `Total sources: N`) ثم `cache.search_put(query, result)`.

**الكاش (`services/cache.py`):** المفتاح `search:exa:<sha256(query)>`، TTL من `EXA_SEARCH_CACHE_TTL` (افتراضي 600؛ 0 يعطّل)، **fail‑open** (إصابة سالبة عند أي خطأ Redis).

**البيئة (أسماء فقط):** `EXA_API_KEY` (⚠️ انظر §4) · `EXA_SEARCH_CACHE_TTL` · `REDIS_URL`.

## 2) `calculator` (safe‑AST، بلا `eval`)
**التوقيع:** `calculator(expression: str) -> str`. **الوصف (docstring):** «Evaluate a mathematical expression… Input: a valid mathematical expression as a string (e.g. "2 + 2 * 3").»
**التدفّق (`calculator.py`):**
1. حدّ الطول: `len > MAX_EXPR_LEN` ⇒ `ValueError`.
2. `ast.parse(expr, mode="eval")` ثم `_safe_eval(tree.body)` (نزول تعاودي).
3. معالجة العقد: `Constant` (int/float فقط) · `BinOp` (`+ - * / // % **`؛ `Pow`→`_pow_guard`) · `UnaryOp` (`+/-`) · `Name` (ثوابت `math`: pi,e,tau,inf,nan) · `Call` (اسم مجرّد في `_ALLOWED_FUNCS` = كل `math.*` + `abs,round,min,max`؛ `factorial/comb/perm` بحدّ `MAX_FACTORIAL_ARG`) · غير ذلك ⇒ `ValueError`.
4. **حراسة الحجم (تصلّب DoS):** `_check_magnitude` (int `bit_length > MAX_RESULT_BITS` ⇒ رفض) · `_pow_guard` يتنبّأ بحجم الأُسّ `base.bit_length()*exp` ويرفض **قبل** الحساب (يمنع `9**9**9**9`). 4096 بت ≈ 1233 رقماً (تحت حدّ CPython لتحويل int→str).
5. الإرجاع `str(result)`؛ أي استثناء ⇒ `"Error evaluating expression: e"` (لا يرفع أبداً).

**البيئة:** `CALC_MAX_RESULT_BITS`(4096) · `CALC_MAX_FACTORIAL_ARG`(1000) · `CALC_MAX_EXPR_LEN`(500). **تبعيات:** stdlib فقط (`ast,math,operator`).

## 3) المطابقة إلى `BaseTool` (AIZZAK)

| المفهوم | alpha (LangChain) | AIZZAK `BaseTool` |
|---|---|---|
| النوع الأساس | `StructuredTool`/`@tool` | `class X(BaseTool)`; `spec: ClassVar[ToolSpec]` |
| الاسم/الوصف | kwargs/docstring | `ToolSpec.name/description` (نُعيد استخدام النصّ حرفياً) |
| مخطط المعاملات | Pydantic/تلقائي | `ToolSpec.parameters` = **JSON Schema** صريح |
| نقطة الدخول | `func(...) -> str` متزامن | `async def run(self, ctx: ExecutionContext, args: Json) -> Json` |
| التبعيات | `os.getenv` + عملاء عامّون | `deps` محقونة (منافذ) فقط — **لا استيراد infrastructure** |
| الإرجاع | سلسلة منسّقة | **`Json` (dict)** |
| التسجيل/الاستدعاء | `_BASE_TOOLS` + `AgentExecutor` | `ToolRegistry` + حلّ **بالاسم** ثم `await tool.run(ctx,args)` |

- **`calculator`:** ينقل المُقيّم الآمن (`_safe_eval`/`_pow_guard`/`_check_magnitude` + القوائم البيضاء + حدود H7) **حرفياً**؛ `deps` غير مستخدمة (نقي)؛ الإرجاع `{"result": str(v)}` والخطأ `{"error": "..."}`.
- **`web_search`:** نداء Exa يصبح **محوّلاً مُقاداً في `infrastructure/`** محقوناً (لا `import exa_py`/`os.getenv` داخل الأداة)؛ المفتاح عبر `CredentialResolver`/`SecretsProvider`؛ الكاش عبر `CacheProvider` (نفس مفتاح `search:exa:<sha256>` + TTL + fail‑open)؛ الإرجاع `Json` منظّم (`{"query","results":[{title,url,snippet}],"total"}`) مع إبقاء منطق التشكيل (top‑5، auto، dedup، عتبة 30، سقف 500).
- **الموضع:** كلاهما **مثالان مرجعيان اختياريان** لعقد `BaseTool` (ثابتان في `ToolRegistry`) — **ليسا** أدوات موصّل/MCP (تلك عبر `integrations.ToolCatalog` كـ`DiscoveredTool`). الوكيل يحلّ الكل **بالاسم**.

## 4) مخاطر ونقاط قرار
1. **⚠️ أمني:** `web_search.py:9` فيه `os.getenv("EXA_API_KEY", "<literal>")` — مفتاح Exa صريح كقيمة احتياطية. **يُبطَل/يُدوَّر في `alpha`**، ولا يُنقل النمط (لا قيمة احتياطية؛ المفتاح عبر `SecretsProvider`).
2. **اسم الأداة:** `exa_search` مقابل اسم محايد `web_search` — القرار في AIZZAK (المحايد أنسب لتجريد المزوّد؛ يتبعه أي `default_tools`/موجّه).
3. **تغيّر شكل الإرجاع** (سلسلة→`Json`) سلوكي — تأكيد أن موجّه/عرض الوكيل يتعامل مع النتائج المنظّمة.
4. **عقد Exa REST** مغلّف داخل `exa_py` — لبناء محوّل نقي يُؤكَّد `POST https://api.exa.ai/search` مقابل توثيق Exa الحالي.
5. **سعة قائمة `math`:** `_ALLOWED_FUNCS` يقبل كل `math.*` — يُعاد تدقيقها عند تثبيت نسخة بايثون مختلفة (يُفضَّل allowlist صريح).
6. **نطاق الكاش:** مفتاح Exa عام (query فقط) — قرار هل يُنطَّق بـ`workspace_id` (غالباً لا لنتائج ويب عامة).
