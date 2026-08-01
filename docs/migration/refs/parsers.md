# refs/parsers.md — مُحلّلات المستندات (مرجع من `alpha`)

> **الوجهة:** `06-domain-models.md §7` (knowledge) · `01-data-model.md §2.7` (documents/chunks).
> **مصادر `alpha`:** `rag/parsers/{pdf_layout_extractor,pdf_table_extractor,image_extractor,text_parser,json_parser,excel_parser}.py` · `rag/indexing/{scanner,node_builder,table_processor}.py` · `rag/config/settings.py`.

## 0) عقدان للمخرجات (مفتاح فهم النظام)
- **العقد A — «RAGFlow dict»:** `parse_file(path) -> {"file_type","texts":[…],"tables":[…]}`. تستخدمه **JSON · DOCX · Excel** (dicts نقية، لا `Document`).
- **العقد B — «Document stream»:** يُنتج `llama_index.core.Document(text, metadata)`. تستخدمه **PDF‑layout · PDF‑table · image/OCR**.
- `scanner.py` يوحّد العقدين إلى قائمة `Document` واحدة، ثم `node_builder.py` يقسّمها إلى `TextNode`. **الوحدة المحورية في الذاكرة = `llama_index Document(text, metadata, doc_id)`.**

## 1) جرد المُحلّلات (6)
| المُحلّل | المكتبة | العقد | الملخّص |
|---|---|---|---|
| PDF نصّ | **PyMuPDF (`fitz`)** | B | `iter_pdf_layout_documents(pdf_path, base_metadata, table_locations) -> Iterable[Document]`؛ `get_text("text", sort=True)` (ترتيب قراءة RTL‑آمن)؛ يتجنّب مناطق الجداول بعتبة تداخل `PDF_TABLE_OVERLAP_THRESHOLD=0.5`. |
| PDF جداول | **Camelot** (+pandas+fitz) | B | `extract_tables_from_pdf(...) -> (List[Document], table_locations)`؛ `lattice` ثم `stream` fallback؛ عتبات دقّة `LATTICE_MIN_ACCURACY=80`/`STREAM=60`؛ دمج جداول عبر الصفحات (`detect/merge_continued_tables`). نصّ الجدول = JSON. |
| OCR/صور | **pytesseract**+PIL+fitz | B | `iter_{image_file,pdf_image,docx_image,xlsx_image}_documents(...)`؛ dedup بـSHA‑1؛ ترقية حجم؛ `image_to_string(lang=OCR_LANG, --oem/--psm)`؛ بوّابات جودة (`is_meaningful_text` تشمل العربية `؀..ۿ`). |
| DOCX | **python‑docx** | A | `RAGFlowTextParser().parse_file(path) -> {file_type,texts,tables,…}`؛ `.docx` فقط؛ كشف العناوين (style→regex→vocab)؛ تقسيم `DOCX_MAX_CHUNK_CHARS=2000` واعٍ للترقيم العربي `،؛؟`. |
| JSON | stdlib (+كشف ترميز) | A | `RAGFlowJsonParser(max_chunk_size=4000).parse_file(path)`؛ 3 أنماط جداول (list[dict]، grid، KV scalar)؛ `file_type ∈ {empty,structured,semi_structured,unstructured}_json`. |
| Excel | **pandas** (`read_excel`) | A | `RAGFlowExcelParser().parse_file(path)`؛ **`texts` فارغة دائماً بالتصميم**؛ `_split_large_table(max_rows=500)`؛ `file_type ∈ {excel_structured,excel_empty,excel_read_error}`. |

## 2) الإرسال (Dispatch) — `scan_docs(data_dir) -> List[Document]`
سلسلة `if/elif` على `p.suffix.lower()` (لا كائن سجلّ). حرّاس ما قبل التحليل: `_zip_within_limits` (قنبلة zip)، `_arm_parse_timeout` (SIGALRM، **الخيط الرئيسي/Unix فقط**)، سقف بكسل Pillow.
- `.json`→JsonParser · `.docx`→TextParser + `iter_docx_image_documents` · `.xls/.xlsx`→ExcelParser (+`iter_xlsx_image_documents` لـ`.xlsx`) · `.pdf`→3 مراحل (جداول→نصّ يتجنّب الجداول→صور) · صور→`iter_image_file_documents` · غيرها (`.txt/.md/.csv`)→`SimpleDirectoryReader`.
- إعداد: `ALLOWED_EXTS = {.pdf,.txt,.md,.docx,.csv,.json,.xls,.xlsx} | IMAGE_EXTS`؛ `MAX_UPLOAD_MB=25`؛ `PARSER_TIMEOUT_SECONDS=60`.

## 3) اقتران التحليل↔التقطيع (أين يعيش «الـchunking» فعلاً)
المُحلّلات تُقسّم **خشناً** فقط (صفحة/فقرة/ورقة/قسم). التقطيع الحقيقي في `node_builder.py::build_node_views(docs, persist_dir) -> List[TextNode]`:
- **مرحلة 1 — تفجير صفوف الجداول:** أي `Document` بـ`chunk_type ∈ {doc_table,docx_table,structured_table,pdf_table,excel_table,json_table}` يمرّ بـ`table_processor.split_table_doc` ⇒ **Document لكل صفّ**، نصّه `row_to_sentence = "; ".join(f"{header}: {value}")` (يُسقِط رؤوس ضجيج `no.,row,#,index,id`). **هنا** تحدث تسلسة «header: value» لكل الجداول (لا داخل مُحلّل Excel). جداول صغيرة (`<= 20` صفّاً) تحمل `original_chunk_text` + `parent_chunk_id`.
- **مرحلة 1.5 — تقسيم النصّ (غير المهيكل فقط):** خطّ LlamaIndex ثلاثي: `HierarchicalNodeParser` → `SemanticSplitterNodeParser` → `SentenceWindowNodeParser(window=3)` (كلٌّ قابل للتعطيل).
- **الترتيب/الهوية:** `_stamp_doc_order` يختم `seq` (page_number→page_index→chunk_index→position)؛ `_stable_doc_id = SHA1(source_path|path|file_name)[:16]`؛ نصوص الآباء تُحفَظ في `parent_chunks.json`؛ تقدير الرموز ≈ 1.3/كلمة.

## 4) التبعيات والإعداد
- **مكتبات:** PyMuPDF/`fitz` · `camelot-py[cv]` (→Ghostscript+OpenCV) · pandas · openpyxl (ضمنياً) · pytesseract (+ثنائي tesseract + `ara`/`eng`) · Pillow · python‑docx · `llama-index-core` · (اختياري) charset_normalizer/chardet.
- **OCR:** `OCR_LANG` (افتراضي الكود `"eng+ara"` — يخالف README `"ara+eng"`), `OCR_PSM=6`, `OCR_OEM=1`, ترقية `OCR_UPSCALE_*`, بوّابات `MIN_*`.
- **حراسة موارد:** `IMAGE_MAX_PIXELS=64M`, `PARSER_MAX_UNCOMPRESSED_MB=200`, `PARSER_MAX_COMPRESSION_RATIO=120`.
- **embedding (alpha):** `paraphrase-multilingual-MiniLM-L12-v2` (384‑dim) + FAISS(L2)+BM25 — **يُستبدَل بـQdrant في AIZZAK**. سرّ `SERVICE_API_KEY` (لم تُقرأ قيمته).

## 5) المطابقة إلى AIZZAK (`06 §7` / `01 §2.7`)
- **صفّ/فقرة/OCR/جدول‑JSON (Document ورقي في alpha) → صفّ `knowledge.chunks`:** `text`→`Chunk.text`؛ `seq`→`Chunk.seq` (يحقّق **INV‑K1** `UNIQUE(document_id,seq)` + `DD‑09`)؛ تقدير الرموز→`token_count`؛ المتجه→`vector_ref=(collection, point_id)` في Qdrant.
- **هوية الملف (`_stable_doc_id`) → `knowledge.documents`** (AR لكل ملف)؛ عدد العقد→`chunk_count`.
- **التدفّق الحدثي:** `files.FileUploaded → knowledge.RegisterDocumentFromFile` (يُصدر `DocumentRegistered`) → عامل `IndexDocument` يشغّل المُحلّلات → `DocumentIndexed{chunk_count,collection}` أو `DocumentIndexingFailed`. أي: `scan_docs`+`build_node_views` المتزامنان يصيران **جسم عامل knowledge اللامتزامن**.
- **الستّة تنقل حرفياً** (منطق الاستخراج مستقلّ عن التخزين).

**ما لا يُطابَق بنظافة:**
- **الغلاف:** `llama_index Document/TextNode`+`SimpleDirectoryReader` **ليست** جزءاً من عقد AIZZAK؛ الوحدة المحفوظة `Chunk`. البيانات الوصفية الغنية (`page_number,headers,table_name,view,parent_chunk_id,section_type…`) **بلا عمود** في `knowledge.chunks` ⇒ يجب أن تعيش في **payload نقطة Qdrant** أو تُسقَط — **قرار مطلوب لمخطط الـpayload**.
- **مخزن المتجهات:** FAISS+BM25+`parent_chunks.json`+التقسيم متعدّد المشاهد (Hierarchical/Semantic/SentenceWindow) خاصّ بـalpha؛ AIZZAK = `(collection, point_id)` واحد لكل chunk ⇒ إعادة تصميم parent‑chunk/sentence‑window أو تسطيحها. الهجين (dense+sparse) قرار Qdrant‑أصلي.
- **الإدمبوتنسي:** `Chunk.seq` يجب أن يكون **حتمياً** عبر إعادة البناء (INV‑K1)؛ لكن `seq` الحالي مشتقّ من ترتيب المعالجة ⇒ قاعدة ترتيب حتمية مطلوبة. **INV‑K3:** إعادة المعالجة بعد `failed` = **مستند/نسخة منطقية جديدة**، لا كتابة فوق القديم (بخلاف alpha الذي يعيد كتابة الفهرس).
- **المهلة لكل ملف** لا يمكن أن تعتمد `SIGALRM` (رئيسي/Unix) داخل عامل مخيوط/لامتزامن ⇒ مهلة/إلغاء على مستوى العامل.
- **تعدّد المستأجرين:** كل chunk/document يحمل `workspace_id` (RLS)؛ alpha بلا مستأجر (`data_dir` لكل محادثة → `(workspace_id, file_id)`).

## 6) مخاطر ونقاط قرار
1. **انحراف README↔الكود (Excel):** README يزعم openpyxl + تسلسة صفوف + إصدار Document + استدعاء صور — **الكود** يستخدم pandas، `texts` فارغة، التسلسة في node‑build، والصور في scanner. **الكود مرجع الحقيقة.**
2. **`OCR_LANG`:** README `"ara+eng"` مقابل كود `"eng+ara"` (الأول يُرجَّح لمحتوى عربي) — قرار صريح.
3. **⚠️ تبعيات ثقيلة/ترخيص:** camelot→Ghostscript+OpenCV؛ tesseract+traineddata؛ **PyMuPDF ترخيص AGPL/تجاري مزدوج** — تأكيد قبولها في صورة عامل AIZZAK أو بدائل (README يزعم `pdfplumber` بينما الكود Camelot — انحراف آخر).
4. **مخطط payload الـQdrant:** غير محسوم — جودة الاسترجاع (استشهاد الصفحة/القسم) تعتمد عليه.
5. **حتمية INV‑K1:** المُقسّم الدلالي معتمد على embedding وترتيب العقد معالجيّ ⇒ ضمان `seq` مستقرّ (hash محتوى أو ترتيب صفحة/فقرة صريح).
6. **ضجيج stdout:** المُحلّلات تطبع بكثرة بدل السجلّ المهيكل ⇒ تُستبدَل بسجلّ عامل AIZZAK.
7. **`.xls`:** مقبول لكن OCR صوره `.xlsx` فقط و`read_excel` يحتاج `xlrd` — تأكيد أو إسقاط.
