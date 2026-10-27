# سجل البحث (research-log)

> الغرض: توثيق الاستعلامات المنفذة وعدد النتائج وقرارات الفرز، لإتاحة التكرار والاستئناف.
> تاريخ الجلسة: 2026-10-25. الباحث: وكيل بحث (Claude). المفتاح الفريد للمشاريع: `full_name`.

## الخطوة 0: تجهيز الأدوات

| الفحص | النتيجة |
|---|---|
| `gh --version` | **غير متاح** (`gh: command not found` في Git Bash، وغير موجود في PATH ولا في `C:\Program Files\GitHub CLI`) |
| `gh auth status` | غير متاح (لا يوجد gh) |
| البديل المعتمد | GitHub REST API بدون مصادقة عبر `curl`/Python (`urllib`) من سكربت محلي في مجلد scratchpad، مع تأخير 7 ثوانٍ بين الطلبات وانتظار 65 ثانية عند 403/429 |
| حدود الطلبات المقاسة (`/rate_limit`) | `search`: 10/دقيقة، `core`: 60/ساعة (بدون مصادقة) |
| قراءة الـ README | عبر `raw.githubusercontent.com/<owner>/<repo>/HEAD/README.md` (لا يستهلك حصة الـ API) |
| النسخ السطحي | `git clone --depth 1 --filter=blob:none --no-checkout` ثم `sparse-checkout` لملفات النص والكود فقط، داخل `%TEMP%\ele-research\repos\<owner>__<repo>`، بدون تشغيل أي كود أو تثبيت أو تنزيل نماذج (`GIT_LFS_SKIP_SMUDGE=1`) |
| بيانات النجوم/التحديث/الترخيص | مأخوذة من حقول `stargazers_count` و`pushed_at` و`license.spdx_id` و`archived` في استجابات Search API بتاريخ 2026-10-25 |
| التحقق من الأوراق | arXiv API (`export.arxiv.org/api/query?id_list=...`) للتحقق من 33 معرّفاً دفعة واحدة |
| التحقق من مجموعات البيانات | Zenodo API (`zenodo.org/api/records/<id>`) وHugging Face API (`huggingface.co/api/datasets/<id>`) |

**قيد مهم**: عدم توفر `gh` يعني عدم القدرة على قراءة issues بكثافة (حصة core = 60/ساعة)؛ لذلك اعتمدت قراءة الـ README والكود المنسوخ محلياً بدلاً من الـ issues في أغلب الحالات.

## الخطوة 1: المسح الواسع — الدفعات 1–5 (GitHub Search API)

الاستعلامات (q01–q60) محفوظة مع نتائجها الخام في scratchpad الجلسة. الأعمدة: المعرّف | الاستعلام | total_count | عدد العناصر المسترجعة.

الاستعلامات نُفذت على خمس دفعات (q01–q60، r01–r50، v01–v15، w01، x01–x12) عبر سكربت محلي بـ Python (`urllib`) مع تأخير بين الطلبات. استعلامات `v01–v05` هي استعلامات تحقق من بيانات مستودعات محددة بصيغة `repo:owner/name` (لجلب النجوم والترخيص وآخر push للأدوات العامة دون استهلاك حصة core).

| المعرّف | الاستعلام (GitHub Search API, sort=stars) | total_count | المسترجع (≤50) |
|---|---|---|---|
| q01 | `electrical schematic symbol detection` | 2 | 2 |
| q02 | `"circuit diagram" recognition` | 15 | 15 |
| q03 | `wiring diagram netlist` | 3 | 3 |
| q04 | `ladder diagram parser` | 3 | 3 |
| q05 | `ladder logic OCR` | 0 | 0 |
| q06 | `single line diagram digitization` | 2 | 2 |
| q07 | `P&ID digitization` | 13 | 13 |
| q08 | `piping and instrumentation diagram` | 58 | 50 |
| q09 | `engineering drawing digitization` | 3 | 3 |
| q10 | `hand-drawn circuit` | 76 | 50 |
| q11 | `electrical drawing OCR` | 2 | 2 |
| q12 | `schematic to netlist` | 58 | 50 |
| q13 | `circuit image netlist` | 13 | 13 |
| q14 | `topic:pid-digitization` | 1 | 1 |
| q15 | `topic:symbol-detection` | 3 | 3 |
| q16 | `topic:engineering-drawings` | 36 | 36 |
| q17 | `topic:fault-diagnosis` | 255 | 50 |
| q18 | `EPLAN` | 851 | 50 |
| q19 | `AutomationML` | 81 | 50 |
| q20 | `PLCopen XML` | 30 | 30 |
| q21 | `ezdxf electrical` | 2 | 2 |
| q22 | `IEC 81346` | 4 | 4 |
| q23 | `fault diagnosis knowledge graph` | 17 | 17 |
| q24 | `troubleshooting assistant RAG` | 212 | 50 |
| q25 | `maintenance manual RAG` | 118 | 50 |
| q26 | `technical documentation RAG` | 632 | 50 |
| q27 | `ColPali` | 394 | 50 |
| q28 | `GraphRAG engineering` | 91 | 50 |
| q29 | `Stromlaufplan` | 4 | 4 |
| q30 | `Schaltplan` | 48 | 48 |
| q31 | `电气图纸` | 28 | 28 |
| q32 | `电路图 识别` | 8 | 8 |
| q33 | `IEC 61850 SCL` | 34 | 34 |
| q34 | `CGMES CIM` | 34 | 34 |
| q35 | `circuit diagram yolo` | 4 | 4 |
| q36 | `schematic LLM netlist` | 10 | 10 |
| q37 | `electrical diagram knowledge graph` | 0 | 0 |
| q38 | `industrial maintenance LLM agent` | 25 | 25 |
| q39 | `P&ID graph` | 35 | 35 |
| q40 | `single-line diagram` | 135 | 50 |
| q41 | `hand drawn circuit recognition` | 22 | 22 |
| q42 | `diagram digitization` | 44 | 44 |
| q43 | `engineering diagram symbol detection` | 2 | 2 |
| q44 | `PLC fault diagnosis` | 10 | 10 |
| q45 | `alarm root cause analysis` | 23 | 23 |
| q46 | `TIA Portal Openness` | 107 | 50 |
| q47 | `kicad netlist parser` | 7 | 7 |
| q48 | `circuit diagram dataset` | 19 | 19 |
| q49 | `wiring harness` | 189 | 50 |
| q50 | `electrical symbols floor plan detection` | 0 | 0 |
| q51 | `model-based diagnosis` | 121 | 50 |
| q52 | `case-based reasoning` | 398 | 50 |
| q53 | `topic:engineering-drawing` | 56 | 50 |
| q54 | `electrical schematic dataset` | 1 | 1 |
| q55 | `electrical schematic in:readme digitization` | 256 | 50 |
| q56 | `topic:circuit-recognition` | 1 | 1 |
| q57 | `schematic image to SPICE` | 1 | 1 |
| q58 | `electrical drawing` | 237 | 50 |
| q59 | `电气原理图` | 32 | 32 |
| q60 | `图纸 识别 目标检测` | 0 | 0 |
| r01 | `electrical schematic` | 375 | 50 |
| r02 | `wiring diagram recognition` | 3 | 3 |
| r03 | `circuit diagram netlist` | 20 | 20 |
| r04 | `electrical diagram` | 341 | 50 |
| r05 | `control cabinet electrical` | 10 | 10 |
| r06 | `ladder diagram image` | 0 | 0 |
| r07 | `QElectroTech` | 93 | 50 |
| r08 | `SPICE netlist dataset image` | 1 | 1 |
| r09 | `AMSnet` | 17 | 17 |
| r10 | `P&ID graph neural network` | 0 | 0 |
| r11 | `symbol spotting` | 59 | 50 |
| r12 | `engineering drawing line detection` | 4 | 4 |
| r13 | `line drawing vectorization` | 73 | 50 |
| r14 | `pdf vector drawings extraction` | 4 | 4 |
| r15 | `maintenance knowledge graph LLM` | 24 | 24 |
| r16 | `fault tree analysis python` | 10 | 10 |
| r17 | `FMEA LLM` | 23 | 23 |
| r18 | `bayesian network fault diagnosis` | 12 | 12 |
| r19 | `PLC alarm diagnosis` | 3 | 3 |
| r20 | `TwinCAT parser` | 7 | 7 |
| r21 | `AutomationML python` | 7 | 7 |
| r22 | `awesome engineering drawings` | 0 | 0 |
| r23 | `awesome industrial AI` | 12 | 12 |
| r24 | `electrical engineer LLM agent` | 1 | 1 |
| r25 | `电气 故障诊断 知识图谱` | 0 | 0 |
| r26 | `梯形图 识别` | 0 | 0 |
| r27 | `Klemmenplan` | 3 | 3 |
| r28 | `EPLAN XML` | 3 | 3 |
| r29 | `single line diagram detection` | 3 | 3 |
| r30 | `electrical symbols YOLO` | 6 | 6 |
| r31 | `schematic dataset graph` | 1 | 1 |
| r32 | `engineering diagram vision language model` | 3 | 3 |
| r33 | `model-based diagnosis python` | 10 | 10 |
| r34 | `consistency-based diagnosis` | 3 | 3 |
| r35 | `fault diagnosis toolbox structural analysis` | 0 | 0 |
| r36 | `EPLAN python` | 7 | 7 |
| r37 | `DEXPI` | 78 | 50 |
| r38 | `circuit schematic benchmark multimodal` | 0 | 0 |
| r39 | `topic:circuit-diagram` | 26 | 26 |
| r40 | `topic:netlist` | 112 | 50 |
| r41 | `topic:pid` | 1070 | 50 |
| r42 | `topic:automationml` | 21 | 21 |
| r43 | `topic:iec-61131-3` | 98 | 50 |
| r44 | `topic:eplan` | 25 | 25 |
| r45 | `topic:graphrag industrial` | 12 | 12 |
| r46 | `schematic OCR` | 13 | 13 |
| r47 | `electrical panel` | 545 | 50 |
| r48 | `MEP electrical drawing` | 14 | 14 |
| r49 | `CGMES python` | 10 | 10 |
| r50 | `IEC 61850 python` | 33 | 33 |
| v01 | `repo:dimitri009/STA repo:IBM/AssetOpsBench repo:nlp-tlp/maintie repo:shenmali/RetAgGen-PowerUP repo:yimin-u...` | 8 | 8 |
| v02 | `repo:jsvine/pdfplumber repo:pdfminer/pdfminer.six repo:docling-project/docling repo:datalab-to/marker repo:...` | 9 | 9 |
| v03 | `repo:tesseract-ocr/tesseract repo:microsoft/graphrag repo:HKUDS/LightRAG repo:neo4j/neo4j-graphrag-python r...` | 9 | 9 |
| v04 | `repo:chroma-core/chroma repo:opensearch-project/OpenSearch repo:ultralytics/ultralytics repo:lyuwenyu/RT-DE...` | 9 | 9 |
| v05 | `repo:neo4j/neo4j repo:memgraph/memgraph repo:pgmpy/pgmpy repo:salesforce/PyRCA repo:IC-Yuan/Circuit-MLLM re...` | 8 | 8 |
| v06 | `Masala-CHAI` | 63 | 50 |
| v07 | `EEE-Bench` | 5 | 5 |
| v08 | `CircuitSense benchmark` | 1 | 1 |
| v09 | `OmniSch` | 40 | 40 |
| v10 | `Image2Net circuit` | 1 | 1 |
| v11 | `Panoptic symbol spotting` | 5 | 5 |
| v12 | `PID2Graph` | 4 | 4 |
| v13 | `FloorPlanCAD` | 6 | 6 |
| v14 | `AMSnet circuit netlist` | 0 | 0 |
| v15 | `HaiCPPS` | 0 | 0 |
| w01 | `repo:NYCU-AI-EDA/Netlistify repo:ECNUACRush/PCBnet repo:j-ehrhardt/haicpps repo:Gabsolat/compressed-air-Net...` | 5 | 5 |
| x01 | `cross reference schematic` | 3 | 3 |
| x02 | `wire number OCR` | 0 | 0 |
| x03 | `terminal strip` | 346 | 50 |
| x04 | `half-split troubleshooting` | 0 | 0 |
| x05 | `Klemmleiste` | 0 | 0 |
| x06 | `Betriebsmittelkennzeichnung` | 0 | 0 |
| x07 | `端子排` | 162 | 50 |
| x08 | `IEC 61082` | 0 | 0 |
| x09 | `VDI 2770` | 3 | 3 |
| x10 | `relay contact cross reference` | 0 | 0 |
| x11 | `wiring diagram LLM` | 2 | 2 |
| x12 | `schematic RAG` | 18 | 18 |


**ملاحظات على النتائج الخام**:
- مجموع المستودعات الفريدة المعادة من كل الاستعلامات: **1,751** (أغلبها غير ذي صلة؛ مثل نتائج `电气图纸` و`电气原理图` التي أعادت مستودعات سياسية/عشوائية، و`topic:pid` التي أعادت متحكمات PID).
- استعلامات أعادت صفراً: `ladder logic OCR`، `electrical diagram knowledge graph`، `electrical symbols floor plan detection`، `图纸 识别 目标检测`، `P&ID graph neural network`، `awesome engineering drawings`، `ladder diagram image`، `电气 故障诊断 知识图谱`، `梯形图 识别`، `fault diagnosis toolbox structural analysis`، `circuit schematic benchmark multimodal`.
- **التشبع**: في الدفعة 2 أصبحت أغلب النتائج ذات الصلة مكررة من الدفعة 1 (مثل CGHD وCircuitVision وYOLOplan وMEPdetect وAzure P&ID)، فتوقف التوسع عند ذلك واقتصرت الدفعة 3 على التحقق.
- نتائج مزعجة (spam) استُبعدت: مستودعات `nextcombo/*` و`lonlonago/*` ("Dataset / resource listing. Sponsor to obtain the full data files").
- **دفعة التحقق من الفجوات (x01–x12)** نُفذت بعد الدراسة المعمقة لاختبار ادعاءات الفجوات: أعادت **صفراً** `wire number OCR`، `half-split troubleshooting`، `Klemmleiste`، `Betriebsmittelkennzeichnung`، `IEC 61082`، `relay contact cross reference`؛ و`cross reference schematic` أعاد 3 فقط (منها `dbc/gxref` لـ gEDA، GPL-3.0، آخر push 2014)؛ و`terminal strip` و`端子排` أعادا ضوضاء (Stripe Terminal، مستودعات عشوائية). `VDI 2770` أعاد `DigitalDataChainConsortium/vdi2770` (MIT) و`dev365code/vdi2770-validate` (Apache-2.0). `schematic RAG` أعاد `Dani25/YOLO-RAG-Electronic-Schematics-Audit` (MIT، 0 نجوم؛ README يدعي YOLOv8s بـ mAP 97.8% على مخططات سيارات وتدقيق netlist مقابل BOM — لم يُتحقق).
- **w01**: تحقق `repo:` من NYCU-AI-EDA/Netlistify (52 نجمة، بلا ترخيص) وECNUACRush/PCBnet (17، بلا ترخيص) وluannamorim/docquery (MIT) وzyrzjyzxy/Sea-RAG (NOASSERTION) وGabsolat/compressed-air-Network-diagnosis (بلا ترخيص)؛ `j-ehrhardt/haicpps` لم يُعَد.
- نتيجة خاطئة: `lll143653/amsnet` هو مشروع إزالة ضوضاء الصور (ECCV 2024) وليس AMSnet للدوائر.

## الخطوة 1: قائمة المرشحين (المسح الواسع)

من 1,751 مستودعاً فريداً، اختيرت **82 مرشحاً ذا صلة** (بعد استبعاد المكرر والخاطئ) للفحص اليدوي عبر الوصف والـ README. البيانات (النجوم، آخر push، الترخيص، الأرشفة) من Search API بتاريخ 2026-10-25. "لا يوجد" في عمود الترخيص = حقل `license` فارغ في الـ API.

| # | full_name | النجوم | آخر push | الترخيص | مؤرشف | الوصف المختصر | الاستعلامات | القرار |
|---|---|---|---|---|---|---|---|---|
| 1 | [EPDF-Extractor/indu-doc-transformer](https://github.com/EPDF-Extractor/indu-doc-transformer) | 1 | 2025-10-31 | MIT | لا | EPLAN PDF reports -> AutomationML | r28 | دراسة معمقة |
| 2 | [Canary-ECAD/ecad-view](https://github.com/Canary-ECAD/ecad-view) | 6 | 2026-10-20 | MIT | لا | EPLAN .epdz viewer (SVG+manifest.db) | q18,r44 | دراسة معمقة |
| 3 | [Canary-ECAD/eplan-mcp](https://github.com/Canary-ECAD/eplan-mcp) | 128 | 2026-10-16 | MIT | لا | EPLAN P8 MCP + docs RAG + symbol RAG | q18,r44 | دراسة معمقة |
| 4 | [Azure-Samples/digitization-of-piping-and-instrument-diagrams](https://github.com/Azure-Samples/digitization-of-piping-and-instrument-diagrams) | 143 | 2026-01-27 | MIT | لا | P&ID raster->graph | q42 | دراسة معمقة |
| 5 | [DynMEP/MEPdetect](https://github.com/DynMEP/MEPdetect) | 1 | 2026-08-03 | AGPL-3.0 | لا | MEP electrical vector/raster tracing | q11,q58,r30 | دراسة معمقة |
| 6 | [hkoziolek/DiagEx-Repro](https://github.com/hkoziolek/DiagEx-Repro) | 4 | 2026-05-02 | Apache-2.0 | لا | VLM agent P&ID->DEXPI | q07,r37 | دراسة معمقة |
| 7 | [dimitri009/STA](https://github.com/dimitri009/STA) | 3 | 2026-09-07 | MIT | لا | P&ID OCR+symbol-tag association | v01 | دراسة معمقة |
| 8 | [JJunjie97/CircuitDiagOCR](https://github.com/JJunjie97/CircuitDiagOCR) | 0 | 2026-08-21 | لا يوجد | لا | deterministic junction/crossing topology | q13,r46 | دراسة معمقة |
| 9 | [arabaya3/PanelPilot](https://github.com/arabaya3/PanelPilot) | 0 | 2026-10-24 | لا يوجد | لا | electrical/control diagnostic copilot | q44 | دراسة معمقة |
| 10 | [mateus-aleixo/maintenance-qa-agent](https://github.com/mateus-aleixo/maintenance-qa-agent) | 1 | 2026-10-12 | MIT | لا | conformal abstention RAG manuals | q25,q38 | دراسة معمقة |
| 11 | [rakexhs/fixit-lens](https://github.com/rakexhs/fixit-lens) | 1 | 2026-07-26 | MIT | لا | citation validator + safety gate | q24 | دراسة معمقة |
| 12 | [ScorpioPxg/IndustryGraphRAG](https://github.com/ScorpioPxg/IndustryGraphRAG) | 5 | 2026-08-26 | MIT | لا | Neo4j causal KG fault diagnosis | q23 | دراسة معمقة |
| 13 | [wanghaoyu6661/Hand-Drawn-Circuit-Diagram-Recognition](https://github.com/wanghaoyu6661/Hand-Drawn-Circuit-Diagram-Recognition) | 3 | 2026-03-08 | لا يوجد | لا | hand-drawn -> SPICE (YOLO+HAWP+PARSeq+ViTPose) | q02,q10,q41 | قائمة مختصرة |
| 14 | [deepkick/pid2graph-vlm-eval](https://github.com/deepkick/pid2graph-vlm-eval) | 3 | 2026-04-11 | لا يوجد | لا | VLM zero-shot eval on PID2Graph | q39,v12 | قائمة مختصرة |
| 15 | [uniwire-dev/uniwire](https://github.com/uniwire-dev/uniwire) | 4 | 2026-10-24 | Apache-2.0 | لا | electrical installation as code | q40,r04 | قائمة مختصرة |
| 16 | [qelectrotech/qelectrotech-source-mirror](https://github.com/qelectrotech/qelectrotech-source-mirror) | 590 | 2026-10-24 | GPL-2.0 | لا | open-source electrical CAD XML | r07 | قائمة مختصرة |
| 17 | [hyogrin/electronic-single-line-diagram-detection](https://github.com/hyogrin/electronic-single-line-diagram-detection) | 5 | 2026-03-11 | MIT | لا | SLD PDF panel detection hybrid | q40,r29 | قائمة مختصرة |
| 18 | [sad123-yx/PIDCon](https://github.com/sad123-yx/PIDCon) | 13 | 2025-04-25 | لا يوجد | لا | P&ID connectivity GNN | q08 | قائمة مختصرة |
| 19 | [mehrabfajar/FaultTrace](https://github.com/mehrabfajar/FaultTrace) | 0 | 2026-09-03 | MIT | لا | PLC dependency RCA ranking | q44 | قائمة مختصرة |
| 20 | [OlivierLAVAUD/industrial-agent-harness](https://github.com/OlivierLAVAUD/industrial-agent-harness) | 0 | 2026-10-23 | Apache-2.0 | لا | human approval + audit chain | r45 | قائمة مختصرة |
| 21 | [dzakybd/pid-parser](https://github.com/dzakybd/pid-parser) | 7 | 2026-10-15 | MIT | لا | P&ID VLM+CCL -> SQL/graph | q39 | قائمة مختصرة |
| 22 | [process-intelligence-research/pyDEXPI](https://github.com/process-intelligence-research/pyDEXPI) | 237 | 2026-04-17 | AGPL-3.0 | لا | DEXPI P&ID -> NetworkX | q08,r37 | قائمة مختصرة |
| 23 | [andrefdre/EplanInOut2xlsx](https://github.com/andrefdre/EplanInOut2xlsx) | 0 | 2026-05-15 | GPL-3.0 | لا | EPLAN PDF PLC I/O extraction | r36 | قائمة مختصرة |
| 24 | [LAD021/ci2n_datasets](https://github.com/LAD021/ci2n_datasets) | 9 | 2026-07-30 | Apache-2.0 | لا | Image2Net datasets/benchmark | q48,r03,r39 | مجموعة بيانات/معيار (ملف 03) |
| 25 | [DFKI/cghd](https://github.com/DFKI/cghd) | 11 | 2022-09-20 | CC0-1.0 | لا | CGHD dataset | q48 | مجموعة بيانات/معيار (ملف 03) |
| 26 | [IBM/AssetOpsBench](https://github.com/IBM/AssetOpsBench) | 2384 | 2026-10-24 | Apache-2.0 | لا | industrial agents benchmark | v01 | مجموعة بيانات/معيار (ملف 03) |
| 27 | [IBM/FailureSensorIQ](https://github.com/IBM/FailureSensorIQ) | 52 | 2026-10-22 | CC-BY-4.0 | لا | failure modes QA benchmark | v01 | مجموعة بيانات/معيار (ملف 03) |
| 28 | [nlp-tlp/maintie](https://github.com/nlp-tlp/maintie) | 24 | 2024-06-29 | MIT | لا | maintenance work order IE | v01 | مجموعة بيانات/معيار (ملف 03) |
| 29 | [aws-solutions-library-samples/guidance-for-piping-and-instrumentation-diagrams-digitization-on-aws](https://github.com/aws-solutions-library-samples/guidance-for-piping-and-instrumentation-diagrams-digitization-on-aws) | 26 | 2026-10-08 | MIT-0 | لا | P&ID AWS pipeline | q08,q42 | ممسوح — لم يدخل القائمة المختصرة |
| 30 | [C-R-Kelly/CircuitSchematicImageInterpreter](https://github.com/C-R-Kelly/CircuitSchematicImageInterpreter) | 32 | 2026-01-08 | NOASSERTION | لا | classical CV schematic graph | q55,r01 | ممسوح — لم يدخل القائمة المختصرة |
| 31 | [HR-DONK/circuit-recognition](https://github.com/HR-DONK/circuit-recognition) | 6 | 2026-06-26 | MIT | لا | YOLO+SAM+OCR MCP | q32 | ممسوح — لم يدخل القائمة المختصرة |
| 32 | [TaylorTurnerIT/digitizing-single-line-diagrams](https://github.com/TaylorTurnerIT/digitizing-single-line-diagrams) | 0 | 2026-10-14 | لا يوجد | لا | SLD YOLO+Hough+BFS | q06,q40,q42 | ممسوح — لم يدخل القائمة المختصرة |
| 33 | [VanujaDesilva/PLCPILOT](https://github.com/VanujaDesilva/PLCPILOT) | 0 | 2026-04-05 | لا يوجد | لا | offline PLC RAG | q44 | ممسوح — لم يدخل القائمة المختصرة |
| 34 | [ereztdev/RAGOps](https://github.com/ereztdev/RAGOps) | 24 | 2026-03-10 | NOASSERTION | لا | deterministic RAG GSE manuals | q25 | ممسوح — لم يدخل القائمة المختصرة |
| 35 | [mojeee/cablepilot](https://github.com/mojeee/cablepilot) | 0 | 2026-10-24 | Apache-2.0 | لا | IEC 81346 ECAD (early) | r05 | ممسوح — لم يدخل القائمة المختصرة |
| 36 | [wi2trier/cbrkit](https://github.com/wi2trier/cbrkit) | 29 | 2026-10-24 | MIT | لا | CBR toolkit | q52 | ممسوح — لم يدخل القائمة المختصرة |
| 37 | [rakhimov/scram](https://github.com/rakhimov/scram) | 166 | 2023-09-19 | GPL-3.0 | لا | fault tree analysis | r16 | ممسوح — لم يدخل القائمة المختصرة |
| 38 | [pgmpy/pgmpy](https://github.com/pgmpy/pgmpy) | 3363 | 2026-10-17 | MIT | لا | Bayesian networks | v05 | ممسوح — لم يدخل القائمة المختصرة |
| 39 | [salesforce/PyRCA](https://github.com/salesforce/PyRCA) | 571 | 2026-06-02 | BSD-3-Clause | لا | RCA library | v05 | ممسوح — لم يدخل القائمة المختصرة |
| 40 | [Suplanus/Suplanus.Sepla](https://github.com/Suplanus/Suplanus.Sepla) | 36 | 2026-09-29 | MIT | لا | EPLAN API library | q18,r44 | ممسوح — لم يدخل القائمة المختصرة |
| 41 | [AutomationML/AMLEngine2.1](https://github.com/AutomationML/AMLEngine2.1) | 31 | 2025-03-02 | MIT | لا | AML .NET engine | q19,r42 | ممسوح — لم يدخل القائمة المختصرة |
| 42 | [AutomationML/automationml-python](https://github.com/AutomationML/automationml-python) | 1 | 2026-10-17 | MIT | لا | AML python SDK | q19,r21 | ممسوح — لم يدخل القائمة المختصرة |
| 43 | [openscd/open-scd](https://github.com/openscd/open-scd) | 144 | 2026-01-12 | Apache-2.0 | نعم | IEC 61850 SCL editor (archived) | q33 | ممسوح — لم يدخل القائمة المختصرة |
| 44 | [sogno-platform/cimpy](https://github.com/sogno-platform/cimpy) | 79 | 2025-03-09 | Apache-2.0 | لا | CGMES/CIM python | q34,r49 | ممسوح — لم يدخل القائمة المختصرة |
| 45 | [devbisme/kinparse](https://github.com/devbisme/kinparse) | 27 | 2026-01-22 | MIT | لا | KiCad netlist parser | q47 | ممسوح — لم يدخل القائمة المختصرة |
| 46 | [JKc66/CircuitVision](https://github.com/JKc66/CircuitVision) | 18 | 2025-06-29 | Apache-2.0 | لا | VLM schematic->SPICE | q13,q55,r03 | ممسوح — لم يدخل القائمة المختصرة |
| 47 | [aaanthonyyy/CircuitNet](https://github.com/aaanthonyyy/CircuitNet) | 66 | 2025-02-20 | لا يوجد | لا | hand-drawn sketch recognizer | q10,q13,r40 | ممسوح — لم يدخل القائمة المختصرة |
| 48 | [daleysoftware/circuitsense](https://github.com/daleysoftware/circuitsense) | 20 | 2018-02-12 | GPL-3.0 | لا | hand-drawn -> netlist (old) | q02,q10,q41 | ممسوح — لم يدخل القائمة المختصرة |
| 49 | [3479504864-ctrl/building-electrical-copilot](https://github.com/3479504864-ctrl/building-electrical-copilot) | 1 | 2026-09-22 | لا يوجد | لا | DXF rule review evidence | q31 | ممسوح — لم يدخل القائمة المختصرة |
| 50 | [clei32879-gif/electrical-copilot](https://github.com/clei32879-gif/electrical-copilot) | 7 | 2026-09-14 | NOASSERTION | لا | CN electrical copilot | q18,r36,r44 | ممسوح — لم يدخل القائمة المختصرة |
| 51 | [Werizu/schaltplan-marker](https://github.com/Werizu/schaltplan-marker) | 0 | 2026-10-04 | لا يوجد | لا | vector PDF MV plan marker | q30 | ممسوح — لم يدخل القائمة المختصرة |
| 52 | [xiaowulai-s/Industrial-Drawing-Recognition](https://github.com/xiaowulai-s/Industrial-Drawing-Recognition) | 2 | 2026-07-25 | Apache-2.0 | لا | switch-state OCR | q31 | ممسوح — لم يدخل القائمة المختصرة |
| 53 | [jgaz/pid_reader](https://github.com/jgaz/pid_reader) | 69 | 2020-09-13 | GPL-3.0 | لا | P&ID reader (stalled) | q08 | ممسوح — لم يدخل القائمة المختصرة |
| 54 | [TUDoAD/DEXPI2graphML](https://github.com/TUDoAD/DEXPI2graphML) | 43 | 2023-08-14 | LGPL-3.0 | لا | DEXPI -> GraphML | q39,r37 | ممسوح — لم يدخل القائمة المختصرة |
| 55 | [yemenoglu/Handwritten-Circuit-Recognition-to-Netlist](https://github.com/yemenoglu/Handwritten-Circuit-Recognition-to-Netlist) | 1 | 2026-04-21 | MIT | لا | hand-drawn -> JSON netlist | q02,q10,q12 | ممسوح — لم يدخل القائمة المختصرة |
| 56 | [mah-sam/sam2-circuit-segmentation](https://github.com/mah-sam/sam2-circuit-segmentation) | 2 | 2025-07-13 | Apache-2.0 | لا | SAM2 LoRA segmentation | q10 | ممسوح — لم يدخل القائمة المختصرة |
| 57 | [zakaneki/circuit-vision](https://github.com/zakaneki/circuit-vision) | 5 | 2025-08-14 | لا يوجد | لا | YOLO11 OBB symbols | q35,r04,r30 | ممسوح — لم يدخل القائمة المختصرة |
| 58 | [A-SHOJAEI/ConstructDrawingAI](https://github.com/A-SHOJAEI/ConstructDrawingAI) | 44 | 2026-07-14 | NOASSERTION | لا | construction drawings graph | q39,q58 | ممسوح — لم يدخل القائمة المختصرة |
| 59 | [hsu-aut/Contextualizing-Timed-Automata-for-Agentic-LLM-Based-Fault-Diagnosis-Results](https://github.com/hsu-aut/Contextualizing-Timed-Automata-for-Agentic-LLM-Based-Fault-Diagnosis-Results) | 1 | 2026-05-11 | لا يوجد | لا | KG + timed automata agent results | q23 | ممسوح — لم يدخل القائمة المختصرة |
| 60 | [KyrieABC/MultiModal-Intelligent-Maintenance-Agent-](https://github.com/KyrieABC/MultiModal-Intelligent-Maintenance-Agent-) | 1 | 2026-04-05 | لا يوجد | لا | multimodal maintenance agent | q25 | ممسوح — لم يدخل القائمة المختصرة |
| 61 | [ten10do/ten10do-industrial-maintenance-agent](https://github.com/ten10do/ten10do-industrial-maintenance-agent) | 0 | 2026-09-17 | MIT | لا | evidence-grounded maintenance agent | q38 | ممسوح — لم يدخل القائمة المختصرة |
| 62 | [Faradworks/Pinscope](https://github.com/Faradworks/Pinscope) | 182 | 2026-07-17 | AGPL-3.0 | لا | datasheet-backed schematic reviewer | r01 | ممسوح — لم يدخل القائمة المختصرة |
| 63 | [VITA-Group/CADTransformer](https://github.com/VITA-Group/CADTransformer) | 137 | 2023-07-25 | MIT | لا | panoptic symbol spotting CAD | r11,v11 | ممسوح — لم يدخل القائمة المختصرة |
| 64 | [nicehuster/SymPoint](https://github.com/nicehuster/SymPoint) | 62 | 2024-03-20 | NOASSERTION | لا | symbol spotting points | r11,v11 | ممسوح — لم يدخل القائمة المختصرة |
| 65 | [WesKwong/VecFormer](https://github.com/WesKwong/VecFormer) | 49 | 2025-10-24 | Apache-2.0 | لا | line-based symbol spotting | r11,v11 | ممسوح — لم يدخل القائمة المختصرة |
| 66 | [illuin-tech/colpali](https://github.com/illuin-tech/colpali) | 2843 | 2026-09-21 | MIT | لا | ColPali/ColQwen | q27 | ممسوح — لم يدخل القائمة المختصرة |
| 67 | [illuin-tech/vidore-benchmark](https://github.com/illuin-tech/vidore-benchmark) | 278 | 2026-10-20 | MIT | لا | ViDoRe | q27 | ممسوح — لم يدخل القائمة المختصرة |
| 68 | [AnswerDotAI/byaldi](https://github.com/AnswerDotAI/byaldi) | 852 | 2025-01-28 | Apache-2.0 | لا | ColPali wrapper | q27 | ممسوح — لم يدخل القائمة المختصرة |
| 69 | [shenmali/RetAgGen-PowerUP](https://github.com/shenmali/RetAgGen-PowerUP) | 0 | 2026-07-23 | لا يوجد | لا | PLC docs RAG eval | v01 | ممسوح — لم يدخل القائمة المختصرة |
| 70 | [yimin-up/LLM_IEC-61131_Graphic](https://github.com/yimin-up/LLM_IEC-61131_Graphic) | 4 | 2024-04-25 | لا يوجد | لا | LLM LD/SFC generation | v01 | ممسوح — لم يدخل القائمة المختصرة |
| 71 | [jitendra-bhandari/Masala-CHAI](https://github.com/jitendra-bhandari/Masala-CHAI) | 128 | 2026-08-09 | لا يوجد | لا | schematic->SPICE dataset | v06 | ممسوح — لم يدخل القائمة المختصرة |
| 72 | [Jike338/EEE-Bench](https://github.com/Jike338/EEE-Bench) | 1 | 2025-05-31 | لا يوجد | لا | EEE benchmark | v07 | ممسوح — لم يدخل القائمة المختصرة |
| 73 | [wireviz/WireViz](https://github.com/wireviz/WireViz) | 5310 | 2026-06-06 | GPL-3.0 | لا | cable/harness docs YAML | q49 | ممسوح — لم يدخل القائمة المختصرة |
| 74 | [klauer/blark](https://github.com/klauer/blark) | 69 | 2025-12-03 | GPL-2.0 | لا | TwinCAT ST parser | r20 | ممسوح — لم يدخل القائمة المختصرة |
| 75 | [crown-sports/netops-fault-lab](https://github.com/crown-sports/netops-fault-lab) | 1 | 2026-10-23 | MIT | لا | Bayesian next-probe selection | r18 | ممسوح — لم يدخل القائمة المختصرة |
| 76 | [IPVS-AS/BDSN-FDC](https://github.com/IPVS-AS/BDSN-FDC) | 1 | 2025-11-09 | MIT | لا | Bayesian decision support diagnosis | r18 | ممسوح — لم يدخل القائمة المختصرة |
| 77 | [GhinwaFAKIH/industrial-graphrag-copilot](https://github.com/GhinwaFAKIH/industrial-graphrag-copilot) | 1 | 2026-10-22 | MIT | لا | RDF KG + SPARQL copilot | r15 | ممسوح — لم يدخل القائمة المختصرة |
| 78 | [YuchenXia/LLMRiskAnalyzer](https://github.com/YuchenXia/LLMRiskAnalyzer) | 40 | 2026-06-18 | لا يوجد | لا | FMEA with LLM | r17 | ممسوح — لم يدخل القائمة المختصرة |
| 79 | [DES-Lab/Automata-Learning-Based-Diagnosis](https://github.com/DES-Lab/Automata-Learning-Based-Diagnosis) | 7 | 2021-12-13 | MIT | لا | automata learning MBD | q51 | ممسوح — لم يدخل القائمة المختصرة |
| 80 | [acolomb/qet_terminal_tables](https://github.com/acolomb/qet_terminal_tables) | 5 | 2023-05-15 | LGPL-3.0 | لا | QET terminal tables | r07 | ممسوح — لم يدخل القائمة المختصرة |
| 81 | [plc-user/QET_Klemmenplan](https://github.com/plc-user/QET_Klemmenplan) | 2 | 2025-07-08 | NOASSERTION | لا | QET terminal plan | r27 | ممسوح — لم يدخل القائمة المختصرة |
| 82 | [yashpatil582/circuit-extract](https://github.com/yashpatil582/circuit-extract) | 2 | 2026-04-19 | MIT | لا | DXF/PDF/photo -> JSON graph | r39 | ممسوح — لم يدخل القائمة المختصرة |
| 83 | [15724894976/a0-drawing-ocr](https://github.com/15724894976/a0-drawing-ocr) | 0 | 2026-07-21 | MIT | لا | vector PDF large-format OCR | r46 | ممسوح — لم يدخل القائمة المختصرة |
| 84 | [lll143653/amsnet](https://github.com/lll143653/amsnet) | 35 | 2025-04-01 | MIT | لا | FALSE POSITIVE (image denoising) | r09 | مستبعد (نتيجة خاطئة) |


**أدوات عامة تحقق منها الـ API (تُستخدم في مصفوفة التقنيات وليست مشاريع تحليل مخططات)**: PyMuPDF (AGPL-3.0)، pdfplumber (MIT)، pdfminer.six (MIT)، ezdxf (MIT)، Docling (MIT)، Marker (Apache-2.0 — للكود؛ أوزان النماذج بشروط منفصلة لم تُفحص)، MinerU (NOASSERTION)، Unstructured (Apache-2.0)، PaddleOCR (Apache-2.0)، docTR (Apache-2.0)، EasyOCR (Apache-2.0)، Tesseract (Apache-2.0)، GraphRAG (MIT)، LightRAG (MIT)، neo4j-graphrag-python (NOASSERTION)، FlagEmbedding (MIT)، Qdrant (Apache-2.0)، Milvus (Apache-2.0)، Weaviate (NOASSERTION)، pgvector (NOASSERTION)، Chroma (Apache-2.0)، OpenSearch (Apache-2.0)، Elasticsearch (NOASSERTION)، Ultralytics (AGPL-3.0)، RT-DETR (Apache-2.0)، RF-DETR (Apache-2.0)، Detectron2 (Apache-2.0)، MMDetection (Apache-2.0، آخر push 2024-08)، SAHI (MIT)، NetworkX (NOASSERTION)، Neo4j (GPL-3.0)، Memgraph (NOASSERTION)، pgmpy (MIT)، PyRCA (BSD-3-Clause)، OpenCV (Apache-2.0)، ColPali (MIT)، byaldi (Apache-2.0).

**لم يُعثر عليها عبر الـ API (لم تُدرج)**: `LatentSpaceIITB/SynthPID` (ذُكر في نتيجة WebSearch فقط؛ استعلام `repo:` لم يُعده ورابط README أعاد 404)، `UKPLab/beir` و`jubnzv/iec-checker` (لم يُعادا في استعلام `repo:` المجمّع؛ غير مستخدمين في المخرجات).


## الخطوة 1: عمليات WebSearch المنفذة (أوراق ومصادر)

| # | الاستعلام | أهم ما أعاده |
|---|---|---|
| W1 | electrical schematic digitization symbol detection netlist arXiv github | AMSnet 2.0 (2505.09155)، SINA (2601.22114 / 2607.01609)، Hemker et al. ARS 2024 |
| W2 | hand-drawn circuit diagram dataset CGHD github license | DFKI/cghd، Zenodo 15333233، Modular Graph Extraction (2402.11093) |
| W3 | P&ID digitization deep learning symbol line detection graph | PID2Graph (2411.13929)، RF-DETR P&ID (HF)، dimitri009/STA، مدونة Microsoft ISE، LAPSE:2026.0420 |
| W4 | VLM benchmark electrical schematic understanding | EEE-Bench (CVPR 2025)، Circuit-MLLM (2609.15668)، CircuitSense (ICLR 2026)، Hitachi near-sight correction |
| W5 | ColPali technical drawings retrieval benchmark | ColPali/ViDoRe (2407.01449) — لا معيار خاص بالرسومات الهندسية |
| W6 | knowledge graph fault diagnosis LLM industrial maintenance | CNC LLM+KG (Engineering 2025)، رسالة Chalmers، محاضرة Neo4j NODES 2025 |
| W7 | EPLAN XML export parser python github | وثائق EPLAN API XML converters فقط — لا محلل مفتوح المصدر |
| W8 | connectivity from vector PDF schematics PyMuPDF | PCBnet (2608.27923)، IEEE 7301510 (vector P&ID)، لا وصفة منشورة لـ netlist من PDF متجهي |
| W9 | Masala-CHAI / Netlistify / Image2Net | Masala-CHAI (2411.14299) |
| W10 | ladder diagram image recognition PLC | 2311.10401، 2410.15200 (+ yimin-up/LLM_IEC-61131_Graphic) |
| W11 | single line diagram digitization substation | أوراق SLD (YOLOv5/v8 + GAN) من UTP/Mahidol، SLED |
| W12 | Digitize-PID / Dataset-P&ID license | مرايا HF بلا ترخيص معلن، SynthPID (2604.16513) |
| W13 | IEC 81346 parser library | لا مكتبة — صفحات المعيار فقط |
| W14 | RAG exact match part numbers hybrid BM25 SPLADE | مدونات ممارسين فقط (لا أدلة محكّمة على رموز تقنية) |
| W15 | Docling vs Marker vs MinerU vs Unstructured benchmark | ورقة Docling (2501.17887)، مقارنات بائعين |
| W16 | LLM agent troubleshooting industrial manuals RAG | ORNL agentic RAG، Tor Vergata، LLM-R، RetAgGen PowerUP |
| W17 | engineering drawing retrieval benchmark ViDoRe | BLUEPRINT (2602.13345)، MCERF (2604.09552)، DeepPatent |
| W18 | consistency-based diagnosis GDE open source | لا تنفيذ Python مصان لـ GDE؛ مراجع نظرية (de Kleer) |
| W19 | EPLAN AutomationML export topology graph | AML→RDF (2504.21694)، دليل Bosch Rexroth، رسالة Bologna |
| W20 | SESYD dataset | SESYD (Delalandre et al. 2010) — ترخيص غير مؤكد |
| W21 | OmniSch benchmark | OmniSch (2604.00270) |
| W22 | PID2Graph dataset zenodo license | Zenodo 14803338 |
| W23 | electrical engineering drawing symbol dataset | SLED، DELP/SkeySpot |
| W24 | BGE-M3 Arabic MIRACL | أرقام MIRACL المجمعة فقط |
| W25 | LLM vision EPLAN PDF evaluation | لا دراسة تقيّم VLM على مخططات EPLAN |
| W26 | AssetOpsBench / FailureSensorIQ | IBM/AssetOpsBench، IBM/FailureSensorIQ |
| W27 | MaintIE dataset | nlp-tlp/maintie (MIT حسب الورقة) |
| W28 | RetAgGen PowerUP | shenmali/RetAgGen-PowerUP (عبر مُجمِّع طرف ثالث) |
| W29 | LLM safety LOTO | SafeLLM (2410.10852) — لا دراسة LOTO كهربائية |
| W30 | cross-reference relay coil contacts extraction | وثائق AutoCAD Electrical / ProfiCAD / QElectroTech — لا ورقة أكاديمية |
| W31 | alarm flood root cause open source | PyRCA (2306.11417)، AlarmOracle |
| W32 | Bayer et al. graph extraction code | لا رابط كود مباشر |
| W33 | DiagEx Koziolek | 2311.10401 (DiagEx نفسه عُثر عليه عبر GitHub: hkoziolek/DiagEx-Repro) |
| W34 | WebFetch: arxiv.org/abs/2607.01609 و/html/2601.22114v1 (SINA) | YOLOv11 + EasyOCR + GPT-4o؛ مرآة كود مجهولة؛ التباس 96.47% |
| W35 | WebFetch: arxiv.org/abs/2402.11093 و/html/2402.11093v1 | Faster R-CNN mAP 18%، U-Net 98.16%، gitlab.com/circuitgraph، Zenodo 10056817 |
| W36 | WebFetch: arxiv.org/html/2411.13929v3 (PID2Graph) | جدول Symbol mAP / Node AP / Edge mAP |
| W37 | WebFetch: ars.copernicus.org (Hemker et al.) | تصحيح ملخص البحث (انظر التصحيحات) |
| W38 | WebFetch: arxiv.org/html/2602.13345 (BLUEPRINT) | Success@3 0.715، nDCG@3 0.626؛ لا مقارنة بـ ColPali |
| W39 | WebFetch: arxiv.org/abs/2508.10449 (SkeySpot) | DELP: 45 مخططاً، 2,450 مثيلاً، YOLOv8 mAP 82.5%؛ لا رابط بيانات |
| W40 | WebFetch: floorplancad.github.io | CC BY-NC 4.0 للوسوم |
| W41 | WebFetch: mathieu.delalandre.free.fr (SESYD) | فشل الاتصال |
| W42 | awesome list engineering drawings symbol spotting | لا قائمة awesome؛ مراجعة Rezvanifar 2019، CADSpotting، SkeySpot |
| W43 | arXiv API بحث: ti:CircuitSense، id_list=2508.13157,2412.12898,2502.16529، ti:hand-drawn AND circuit | CircuitSense 2509.22339، Image2Net، Sketch2Sim 2610.28836، 2106.11559 |

## تصحيحات وتعارضات لوحظت أثناء البحث

1. **Hemker et al. (ARS 2024)**: ملخص WebSearch ذكر YOLOv8 ودقة netlist 95.71% على 140 مخططاً؛ القراءة المباشرة للمقال (ars.copernicus.org) أظهرت YOLOv7 + L-CNN + CRAFT/EasyOCR، ولا رقم لدقة netlist، والكود غير منشور. اعتُمد ما في المقال.
2. **SINA**: نسخة 2601.22114 تذكر 96.47% و2607.01609 تذكر 96.67%؛ وفي HTML النسخة الأولى يظهر 96.47% أيضاً كـ F1 لكشف المكونات في Table I — التباس في الورقة نفسها.
3. **CGHD**: ملف LICENSE في مستودع GitHub (DFKI/cghd) هو CC0 1.0، بينما سجل Zenodo 15333233 (الإصدار 15) يعلن CC-BY-4.0.

## ملاحظات أمنية (حقن تعليمات)

- لم يُرصد حتى الآن نص في المستودعات يطلب تنفيذ أوامر موجهة للوكيل. بعض الـ README تتضمن أوامر تثبيت/تشغيل (مثل `install.sh`، `pip install`) — عوملت كبيانات ولم تُنفَّذ.
- README مستودع Canary-ECAD/eplan-mcp يحتوي فقرة موجهة للنماذج اللغوية ("Working with an LLM here? Read llm.md") — عوملت كبيانات ولم تُتبع.
- مستودعات تحتوي "skills" أو تعليمات موجهة لوكلاء البرمجة (CircuitDiagOCR: `skills/circuitdiag-eda/SKILL.md`؛ pid-parser: "Then ask your agent: Use the pid-parser skill..."؛ cablepilot: `CLAUDE.md`؛ uniwire: `llms.txt`) — عوملت كبيانات ولم تُقرأ كتعليمات ولم تُنفذ.
- لم تُرصد أي محاولة صريحة لتغيير مهمة الباحث في المحتوى المقروء.

## الخطوة 1 (تكملة): القوائم المنسقة (awesome-lists) وGitHub Topics

| المصدر | النتيجة |
|---|---|
| `awesome engineering drawings` (r22) | صفر نتائج؛ WebSearch مماثل لم يجد قائمة awesome للرسومات الهندسية |
| `liguge/Awesome-large-language-model-for-Prognostics-and-health-management` (138 نجمة) | 23 رابط GitHub؛ أغلبها PHM مبني على الإشارات/الاهتزاز (BearLLM، RotLLM، FM4Motor...) وأوراق LLM+KG للتشخيص؛ لا مشاريع لتحليل المخططات → خارج النطاق، لم تُضف للمرشحين |
| `ishandutta2007/Awesome-Industrial-AI` | 19 رابطاً؛ منصات صيانة تنبؤية وIoT (thingsboard، ditto، FAAAST-Service) → خارج النطاق |
| `myutzy/awesome-structured-text` (IEC 61131-3) | 14 رابطاً لأدوات ST؛ لم تُضف (المحور 1 مغطى بـ blark وtree-sitter-iec61131-3-st وPLCopen parsers) |
| `chistiq/awesome-graph-engineering` | جُلب ولم يُفحص بالتفصيل |
| Topics: `pid-digitization` (1)، `symbol-detection` (3)، `engineering-drawings` (36)، `engineering-drawing` (56)، `fault-diagnosis` (255، أغلبها إشارات)، `circuit-recognition` (1)، `circuit-diagram` (26)، `netlist` (112)، `pid` (1,070 أغلبها متحكمات PID)، `automationml` (21)، `iec-61131-3` (98)، `eplan` (25)، `graphrag industrial` (12) | انظر جدول الاستعلامات |

## الخطوة 2: الفرز الموزون (القائمة المختصرة)

الأوزان: الصلة 30%، العمق 20%، إعادة الاستخدام 20%، الأدلة 15%، النضج 15%. كل معيار 0–5. قواعد ثابتة طُبقت: (1) بلا ترخيص → إعادة الاستخدام = 1؛ (2) اعتماد جوهري على AGPL → إعادة الاستخدام ≤ 2–4 حسب مركزية الاعتماد؛ (3) الأدلة = 5 فقط مع نتائج مقاسة قابلة لإعادة الإنتاج؛ (4) النجوم لا تدخل مباشرة إلا كمؤشر نضج ثانوي.

| # | full_name | الصلة (30%) | العمق (20%) | إعادة الاستخدام (20%) | الأدلة (15%) | النضج (15%) | الموزون | القرار |
|---|---|---|---|---|---|---|---|---|
| 1 | Canary-ECAD/ecad-view | 5 | 4 | 5 | 2 | 4 | 4.2 | معمقة |
| 2 | Canary-ECAD/eplan-mcp | 5 | 4 | 3 | 2 | 5 | 3.95 | معمقة |
| 3 | mateus-aleixo/maintenance-qa-agent | 2 | 5 | 5 | 5 | 4 | 3.95 | معمقة |
| 4 | hkoziolek/DiagEx-Repro | 3 | 5 | 4 | 5 | 3 | 3.9 | معمقة |
| 5 | EPDF-Extractor/indu-doc-transformer | 5 | 4 | 4 | 2 | 3 | 3.85 | معمقة |
| 6 | Azure-Samples/digitization-of-piping-and-instrument-diagrams | 3 | 5 | 4 | 3 | 4 | 3.75 | معمقة |
| 7 | arabaya3/PanelPilot | 5 | 5 | 1 | 2 | 4 | 3.6 | معمقة |
| 8 | uniwire-dev/uniwire | 4 | 4 | 5 | 1 | 3 | 3.6 | مختصرة |
| 9 | dimitri009/STA | 3 | 4 | 4 | 3 | 3 | 3.4 | معمقة |
| 10 | rakexhs/fixit-lens | 2 | 4 | 5 | 3 | 3 | 3.3 | معمقة |
| 11 | qelectrotech/qelectrotech-source-mirror | 4 | 3 | 3 | 1 | 5 | 3.3 | مختصرة |
| 12 | hyogrin/electronic-single-line-diagram-detection | 4 | 4 | 4 | 1 | 2 | 3.25 | مختصرة |
| 13 | dzakybd/pid-parser | 3 | 3 | 4 | 3 | 3 | 3.2 | مختصرة |
| 14 | DynMEP/MEPdetect | 4 | 4 | 2 | 2 | 3 | 3.15 | معمقة |
| 15 | JJunjie97/CircuitDiagOCR | 3 | 5 | 1 | 4 | 3 | 3.15 | معمقة |
| 16 | OlivierLAVAUD/industrial-agent-harness | 2 | 4 | 4 | 2 | 3 | 2.95 | مختصرة |
| 17 | mehrabfajar/FaultTrace | 3 | 3 | 4 | 2 | 2 | 2.9 | مختصرة |
| 18 | process-intelligence-research/pyDEXPI | 2 | 4 | 2 | 3 | 4 | 2.85 | مختصرة |
| 19 | deepkick/pid2graph-vlm-eval | 3 | 3 | 2 | 4 | 2 | 2.8 | مختصرة |
| 20 | ScorpioPxg/IndustryGraphRAG | 2 | 3 | 4 | 2 | 3 | 2.75 | معمقة |
| - | sad123-yx/PIDCon | 3 | 4 | 1 | 4 | 2 | 2.8 | احتياط |
| - | IPVS-AS/BDSN-FDC | 2 | 3 | 4 | 3 | 2 | 2.75 | احتياط |
| - | andrefdre/EplanInOut2xlsx | 5 | 1 | 3 | 1 | 1 | 2.6 | احتياط |
| - | wanghaoyu6661/Hand-Drawn-Circuit-Diagram-Recognition | 2 | 4 | 1 | 3 | 3 | 2.5 | احتياط |
| - | crown-sports/netops-fault-lab | 1 | 3 | 4 | 2 | 3 | 2.45 | احتياط |
| - | jitendra-bhandari/Masala-CHAI | 2 | 3 | 1 | 4 | 3 | 2.45 | احتياط |

**قرارات خاصة**:
- `ScorpioPxg/IndustryGraphRAG` (2.75) أُدرج في القائمة المختصرة كاستثناء تغطية (الوحيد بكود متحقق منه لرسم سببي Neo4j لتشخيص الأعطال)، وأُزيح `sad123-yx/PIDCon` (2.80) إلى الاحتياط (تعادل مع deepkick وكُسر بإعادة الاستخدام 1 مقابل 2). `IPVS-AS/BDSN-FDC` (2.75) بقي احتياطاً لأنه دُرس عبر الـ README فقط.
- `andrefdre/EplanInOut2xlsx`: الصلة 5 لكن قسم About منسوخ من مشروع آخر (سحابة نقاط) وأُنشئ ودُفع في اليوم نفسه → العمق 1 والنضج 1.
- `arabaya3/PanelPilot`: أعلى صلة وعمق، لكن بلا ترخيص → إعادة الاستخدام 1؛ دُرس معمقاً كمرجع تصميمي فقط.
- `JJunjie97/CircuitDiagOCR`: بلا ترخيص صريح (الـ README ينص على ذلك)؛ دُرس لقيمة منهجيته وتدقيقاته للتراخيص.
- مشاريع عامة (Docling، ColPali، Qdrant...) لم تُقيَّم في الفهرس لأنها تقنيات لا مشاريع تحليل مخططات؛ وثقت في `02-techniques-matrix.md`.

## الخطوة 3: الدراسة المعمقة (12 مشروعاً + 2 جزئياً)

النسخ: `git clone --depth 1 --filter=blob:none --no-checkout` + `sparse-checkout` لأنماط `*.py *.md *.txt *.toml *.cfg *.yaml *.yml *.json *.cs *.js *.ts *.java *.rst LICENSE* COPYING* *.xml *.ipynb *.cpp *.h *.rs *.go *.sh` في `%TEMP%\ele-research\repos\<owner>__<repo>` (ملاحظة: `*.tsx` لم يُدرج فلم تُقرأ ملفات واجهة React في ecad-view). لم يُشغَّل أي كود ولم تُنزَّل نماذج.

| المشروع | الملفات المقروءة فعلياً |
|---|---|
| Canary-ECAD/ecad-view | packages/e3d-core/src/manifest.ts، structure.ts؛ apps/web/src/devices.ts، aml/amlWorker.ts؛ شجرة الملفات |
| Canary-ECAD/eplan-mcp | README.md؛ eplan-p8-mcp-server/mcp_server/api/actions/live.py، data_exchange.py، export_.py؛ LICENSE؛ شجرة الملفات |
| mateus-aleixo/maintenance-qa-agent | README.md؛ docs/model-card.md؛ src/conformal_rag/conformal.py، answer.py، embed.py، config.py، cli.py (grep)؛ شجرة runs/ |
| hkoziolek/DiagEx-Repro | README.md؛ docs/ARCHITECTURE.md؛ src/diagex/agent/tools.py (grep)؛ results/README.md، phase2/report.md، gpt41/report.md، ablation/delta_vs_baseline.md |
| EPDF-Extractor/indu-doc-transformer | README.md؛ config.json؛ page_settings.json؛ src/indu_doc/lark_parser.py، tag.py، connection.py/xtarget.py/god.py (grep)؛ plugins/eplan_pdfs/common_page_utils.py، footers_extractor.py؛ pyproject.toml |
| Azure-Samples/digitization-of-piping-and-instrument-diagrams | docs/architecture.md، text-detection-design.md، design-db.md، graph-construction-design.md (أقسام)؛ src/app/utils/regex_utils.py؛ grep على services/ (HoughLinesP، thinning، networkx، shapely) |
| arabaya3/PanelPilot | README.md (جزئياً)؛ apps/api/app/ai/guardrails/cite_or_refuse.py؛ retrieval/hybrid_search.py، query_classifier.py، relevance.py؛ ai/localisation.py، query_translation.py؛ design/rtl.py، designations.py؛ models/schemas/responses.py |
| dimitri009/STA | README.md؛ PID_pipeline_.py (grep للدوال والـ regex والكلفة)؛ requirements.txt؛ LICENSE |
| rakexhs/fixit-lens | README.md؛ backend/app/rag/citation_validator.py؛ backend/app/safety/classifier.py، safety_rules.yaml |
| DynMEP/MEPdetect | README.md؛ mepdetect-backend/pdf_geometry.py، segment_graph.py (docstrings/دوال)، circuit_tracer.py (grep)، tests/test_cross_sheet.py |
| JJunjie97/CircuitDiagOCR | README.md؛ docs/DATASET_COMPARISON.md، docs/UPSTREAM_PROJECTS.md؛ شجرة الوحدات |
| ScorpioPxg/IndustryGraphRAG | README.md؛ retrieval/causal_chain.py؛ extraction/prompts.py (grep)؛ evaluation/evaluate.py (grep)؛ requirements.txt |
| جزئياً: wanghaoyu6661/Hand-Drawn-Circuit-Diagram-Recognition | README.md؛ scripts/run_all.sh؛ src/pipeline/build_connections.py (المعاملات) |
| جزئياً: deepkick/pid2graph-vlm-eval | README.md؛ شجرة الملفات |
| DFKI/cghd (بيانات) | README.md، LICENSE، classes.json |

## قيود واجهتها

1. **gh غير متاح** → اعتماد REST API بدون مصادقة؛ حصة search 10/دقيقة استدعت انتظار 65 ثانية متكرراً (زمن المسح ~35 دقيقة). حصة core (60/ساعة) لم تسمح بقراءة issues المستودعات بشكل منهجي؛ **لم تُقرأ أي issues**.
2. **README مقابل الكود**: المشاريع في القائمة المختصرة غير المعمقة (uniwire، QElectroTech، hyogrin، pid-parser، harness، FaultTrace، pyDEXPI، deepkick) وُثقت من الـ README فقط، وكل أرقامها ادعاءات مؤلفيها.
3. **لا تشغيل**: كل الأرقام المذكورة منقولة من مستودعات/أوراق ولم أُعِد إنتاجها.
4. **WebFetch** فشل على موقع SESYD (لا استجابة).
5. أسماء نماذج مستقبلية في READMEs (مثل "Claude Opus 4.7"، "Sonnet 5.5"، "gpt-6-luna") نُقلت كما وردت دون تحقق.
6. IEC 61082 أعاد صفراً على GitHub؛ VDI 2770 أعاد مستودعين (لم يُفحصا). لم أبحث في أوراق IEC 61082 خارج GitHub.

## قرارات استمرارية (للاستئناف)

- المفتاح الفريد: `full_name`. أي جلسة لاحقة تقرأ `catalog.json` أولاً وتتجنب تكرار المشاريع.
- مرشحون جديرون بالتعمق لاحقاً إن طُلب: `uniwire-dev/uniwire` (قراءة المخطط JSON Schema)، `qelectrotech/qelectrotech-source-mirror` (صيغة .qet والمراجع المتقاطعة في الكود)، `hyogrin/electronic-single-line-diagram-detection`، `dzakybd/pid-parser` (connect.py)، `sad123-yx/PIDCon`، `AutomationML/automationml-python`، `yashpatil582/circuit-extract`، `Canary-ECAD/eplan-cloudflare-rags` (تقييم FTS5 مقابل الدلالي).
- استعلامات نُفذت لاحقاً للتحقق من الفجوات: انظر x01–x12 أعلاه. لم تُنفذ بعد: `Stromlaufplan Querverweis`، `端子排 识别 图纸`، `wire label detection`، `terminal block detection YOLO`، `alarm list PLC LLM`.
