# مصفوفة التقنيات (02-techniques-matrix)

> تاريخ الإعداد: 2026-10-25. لكل مرحلة معالجة: الخيارات المتاحة، ومن يستخدمها، والمزايا والعيوب، والنضج، والأدلة. **لا توجد هنا توصية أو اختيار** — العرض محايد ومخصص لوكيل التصميم.
>
> اصطلاحات: **[كود]** = تحققت منه في نسخة سطحية من المستودع؛ **[README]** = ورد في الـ README فقط؛ **[ورقة]** = من ورقة بحثية قرأت ملخصها/نصها؛ **ملاحظة الباحث** = استنتاج غير مدعوم مباشرة بدليل منشور. روابط المصادر في `04-sources.md`.
>
> مقياس النضج المستخدم: **عالٍ** (مكتبة واسعة الاستخدام، صيانة نشطة)، **متوسط** (مشروع عامل بتوثيق واختبارات لكن مجتمع صغير)، **منخفض** (إثبات مفهوم/بحثي/بلا اختبارات).

---

## 1. صيغ المدخلات ومعاييرها

### 1.1 الصيغ ومحتواها من حيث الاتصال

| الصيغة | هل تحمل الاتصال صراحة؟ | المراجع المتقاطعة | أدوات مفتوحة للقراءة | من يستخدمها | المزايا | العيوب | النضج | الأدلة |
|---|---|---|---|---|---|---|---|---|
| صورة ممسوحة (raster) | لا — يجب استنتاجه بالرؤية | نص فقط (يحتاج OCR) | OpenCV، أي OCR | Azure P&ID، STA، SINA، CGHD، PIDCon، CircuitDiagOCR | متاحة دائماً | أصعب مسار؛ أخطاء تراكمية | عالٍ (للأدوات) | PID2Graph: F1/mAP للحواف 46–95% حسب الطريقة [ورقة] |
| PDF متجهي عام | جزئياً — قطع خطوط بإحداثيات دقيقة، دون دلالة "سلك" | نص قابل للاستخراج | PyMuPDF (AGPL-3.0)، pdfplumber (MIT)، pdfminer.six (MIT) | MEPdetect [كود]، hyogrin SLD [README]، Werizu/schaltplan-marker [README] | دقة هندسية كاملة؛ نص حقيقي | لا تمييز تلقائي بين السلك والإطار والرمز؛ تختلف حسب المصدِّر | متوسط | MEPdetect يعلل اختياره: "Rasterizing a drawing to trace it throws away exactly what matters" [كود/docstring] — لا مقارنة كمية منشورة |
| PDF تقارير EPLAN (قوائم اتصالات، مخطط أطراف، مخطط PLC...) | نعم في الجداول (مصدر/هدف/نقطة اتصال) | ضمني عبر التاغات | indu-doc-transformer (MIT) [كود] | indu-doc-transformer | بيانات جدولية منظمة؛ تاغات IEC 81346 | تعتمد على قالب التقرير (ROI)؛ لا تغطي رسوم صفحات المخطط | منخفض–متوسط | لا مقاييس منشورة |
| EPLAN `.epdz` (SVG + `manifest.db` SQLite + AML + E3D) | نعم: حزم `mergedconnection` (مصدر/هدف/جهد/لون/مقطع/طول) | نعم: روابط `jumpToFunction` في SVG، وحزم `interruptionpoint` بمرجع ep.19007 وقائمة صفحات | ecad-view (MIT) [كود] | ecad-view | اتصال وأجهزة وصفحات دون ترخيص EPLAN | دلالات معرّفات الخصائص مستنتجة من الكود (**ملاحظة الباحث**)؛ خاصة بـ EPLAN | منخفض–متوسط | لا مقاييس؛ اختبارات عينات E3D |
| EPLAN P8 API (مباشر) | نعم: `XMExportConnectionsAction` إلى TXT/XLSX/XML، Live DataModel | نعم (نموذج المشروع) | eplan-mcp (MIT) [كود]، Suplanus.Sepla (MIT) | eplan-mcp، EasyEPLANner | المصدر المرجعي الكامل | يتطلب EPLAN مرخصاً قيد التشغيل (Windows) | متوسط | وثائق EPLAN API الرسمية للمحوّلات XML (XPalXmlExporter، XDLXmlExporter) |
| AutomationML (IEC 62714 / CAEX) | نعم (هرمية وروابط InternalLink) | حسب المصدِّر | AMLEngine2.1 (.NET، MIT)، automationml-python (MIT، 2026)، ecad-view (يقرأ AML داخل epdz)، indu-doc (يصدّر AML) | indu-doc، ecad-view، PanelPilot (تصدير) | معيار تبادل محايد؛ EPLAN يصدّره (دليل Bosch Rexroth) | التنوع في استخدام CAEX بين الأدوات | متوسط | AML→RDF ontology وتحقق SHACL [ورقة 2504.21694] |
| DXF/DWG | نعم لـ BLOCK/INSERT وخصائص ATTRIB؛ الخطوط هندسة فقط | نص | ezdxf (MIT، 1,491 نجمة)، unitedrhino/go-cad (Apache-2.0) | building-electrical-copilot (C#) [README]، yashpatil582/circuit-extract [README]، MEPdetect (مدخل DXF) | الكتل وخصائصها تعطي هوية الجهاز مباشرة | الاتصال غير مضمون إن رُسمت الأسلاك كخطوط حرة | عالٍ (ezdxf) | لا مقاييس في المشاريع الممسوحة |
| QElectroTech `.qet` (XML) | نعم (conductors بين terminals) | نعم (master/slave يتحدث آلياً) | qelectrotech (GPL-2.0)، qet_terminal_tables (LGPL-3.0)، QET_Klemmenplan | PanelPilot (تصدير QET) | صيغة مفتوحة لمخططات كهربائية | GPL؛ الصيغة غير موثقة في الـ README | عالٍ (الأداة) | [README] |
| uniwire JSON | نعم (رسم terminal/connectivity-node مشتق) | تقسيم صفحات آلي مع مراجع | uniwire (Apache-2.0) | — | مخطط typed مع تعيين إلى CIM/IFC/SCL | صيغة تأليف لا استخراج | منخفض–متوسط | [README] |
| PLCopen XML / IEC 61131-3 | منطق البرنامج (لا أسلاك) | — | plcopen-xml-xcore (EPL-2.0)، RoDoerIng/PlcOpen (MIT)، plcopen-go (MIT)، blark (GPL-2.0، TwinCAT ST)، tree-sitter-iec61131-3-st (MIT) | PanelPilot (محلل ST خاص) | يربط عناوين I/O بالمنطق | لا يحمل توصيلات العتاد | متوسط | — |
| TIA Portal Openness (XML) | منطق وعتاد Siemens | — | TIA_Portal_Openness_MCP (MIT، 346 نجمة)، tia-portal-xml-generator (MIT) | — | وصول برمجي لمشاريع Siemens | يتطلب TIA Portal مرخصاً | متوسط | — |
| IEC 61850 SCL | طوبولوجيا محطة (ConnectivityNode، Terminal) | — | open-scd (Apache-2.0، **مؤرشف**)، librescl (LGPL-3.0)، iec61850-scl-rdf-converter، open61850 (Apache-2.0) | uniwire (تعيين) | معيار طوبولوجي صريح | خاص بالمحطات | متوسط | — |
| CIM/CGMES (IEC 61970/61968) | نعم (Terminal/ConnectivityNode) | — | cimpy (Apache-2.0)، pycgmes (Apache-2.0)، pypowsybl (MPL-2.0)، cimpyorm (BSD-3) | uniwire (تعيين) | نموذج شبكة قياسي | لشبكات القوى لا لوحات التحكم | عالٍ | — |
| DEXPI (P&ID، للمقارنة) | نعم | — | pyDEXPI (AGPL-3.0)، DEXPI2graphML (LGPL-3.0) | DiagEx (يولّد DEXPI 2.0)، jgaz/pid_reader | تمثيل رسم بياني قياسي | مجال عمليات لا كهرباء | متوسط | — |
| KiCad/SPICE netlists (للمقارنة) | نعم (net ↔ pin) | — | kinparse (MIT)، universal-netlist (Apache-2.0) | SINA، Masala-CHAI، Image2Net (كمخرجات) | صيغ مرجعية للتقييم | إلكترونيات لا تحكم صناعي | عالٍ | تقييم بـ graph isomorphism (SINA) وNED (Image2Net) [ورقة] |

### 1.2 معايير التعريف والرموز

| المعيار | ما وجدته في المشاريع | الحالة |
|---|---|---|
| IEC 81346 (رموز التعريف المرجعية) | فواصل الجوانب `=` (وظيفة) و`+` (موقع) و`-` (منتج) قابلة للضبط في indu-doc (`config.json`) [كود]؛ تقسيم بنية EPLAN `==` و`=` و`++` و`+` و`&` في ecad-view [كود]؛ قواعد Lark للبحث بالتاغ `TAGWORD /([=+-.][A-Za-z0-9_]+)+/` في indu-doc [كود]؛ PanelPilot يحفظ إشارات `-` و`=` و`+` ملاصقة للاسم اللاتيني في النص العربي (rtl.py) [كود] | **لا توجد مكتبة مفتوحة مخصصة لتحليل/التحقق من IEC 81346** (W13، q22: النتائج اقتصرت على `PrediktorAS/RDSdsl` المؤرشف — DSL استعلام لـ RDS — ومراجع رموز أحرف) |
| IEC 60617 (الرموز) | مكتبات عناصر QElectroTech؛ مجموعة صور رموز EPLAN IEC/NFPA/GB (covaga، 33,502 صورة)؛ مكتبة رموز ng-diagram-single-line-diagram (MIT) [README] | لا يوجد معيار مقروء آلياً للرموز في المسح |
| IEC 61082 (توثيق المخططات، ومنه المراجع المتقاطعة) | لم يظهر صراحة في أي مستودع ممسوح | لم أبحث عنه بمعزل |
| IEC 62714 (AutomationML) | انظر الجدول أعلاه | — |
| IEC 61131-3 / PLCopen XML | انظر الجدول أعلاه | — |
| IEC 61850 SCL / CIM | انظر الجدول أعلاه | — |

---

## 2. رقمنة المخططات

### 2.1 كشف الرموز

| الخيار | من يستخدمه | المزايا | العيوب | النضج | الأدلة (مع المصدر) |
|---|---|---|---|---|---|
| YOLO (v5/v7/v8/v10/v11، Ultralytics) | SINA (YOLOv11)، Hemker et al. (YOLOv7)، SkeySpot (YOLOv8)، أوراق SLD (YOLOv5/v8)، MEPdetect وYOLOplan (YOLO11)، HR-DONK (YOLOv8n)، wanghaoyu (YOLOv10)، zakaneki (YOLO11 OBB) | سريع؛ أدوات تدريب جاهزة | **Ultralytics بترخيص AGPL-3.0**؛ يحتاج بيانات موسومة لكل مكتبة رموز | عالٍ | SINA: P 94% / R 99% / F1 96.47% على 75 مخططاً [ورقة]؛ SkeySpot: mAP 82.5% (DELP) [ورقة]؛ SLD: دقة >96.3% (YOLOv5 + GAN) [ورقة]؛ Hemker: mAP50-95 ≥0.73 لمعظم الفئات [ورقة] |
| Faster R-CNN | Bayer et al. (ResNet-152) على CGHD؛ خط الأساس المعياري في PID2Graph | معروف ومستقر | أبطأ؛ ضعيف مع الفئات الكثيرة النادرة | عالٍ | CGHD (59 فئة، مرسوم يدوياً): mAP 18% على validation [ورقة 2402.11093]؛ PID2Graph modular: symbol mAP 74–84% [ورقة 2411.13929] |
| DETR / RT-DETR / RF-DETR | STA (RF-DETR + SAHI) [كود] | Apache-2.0؛ بلا NMS | يحتاج ضبطاً على الدقة العالية | عالٍ (المكتبات) | بطاقة HF تذكر دقة واستدعاء عاليين على 32 فئة P&ID (لم أتحقق من الأرقام) |
| Relationformer (رموز + علاقات معاً) | PID2Graph [ورقة] | يتعلم الحواف مباشرة | لا كود منشور مرتبط بالورقة (لم أجده) | منخفض (بحثي) | edge mAP: 75.46 (OPEN100) مقابل 45.89 للمعياري؛ 88.95 مقابل 50.26 (اصطناعي)؛ symbol mAP أقل من المعياري على OPEN100 (73.14 مقابل 76.99) [ورقة] |
| AutoML سحابي | Azure P&ID (Azure ML AutoML) [كود/وثائق] | لا حاجة لخبرة تدريب | ارتباط بمزوّد | متوسط | لا مقاييس خاصة |
| VLM zero-shot | DiagEx (Claude Opus 4.7، أدوات tiles) [كود]؛ deepkick (Claude Opus 4.6) [README]؛ pid-parser (Sonnet 5.5 / gpt-6-luna حسب الـ README) | بلا تدريب؛ يتعامل مع رموز جديدة | كلفة لكل صفحة؛ عدم حتمية؛ الدقة تتأثر بالدقة البصرية | منخفض–متوسط | deepkick: F1 العقد 0.802 (صورة كاملة) و0.884 (tiles) على 12 مخطط OPEN100 [README]؛ pid-parser: F1 الرموز 0.84 كاملة مقابل 0.96–0.99 بالـ tiles [README]؛ DiagEx: F1 المعدات 0.67–1.00 [كود/results] |
| استرجاع بالتضمين (CLIP) لهوية الرمز | eplan-mcp: 33,502 صورة رمز EPLAN بـ CLIP ViT-B/32 (512-d) [README/كود extension] | يتعامل مع مكتبة رموز كبيرة دون تدريب كاشف | يحتاج قصاً مسبقاً للرمز؛ لا تقييم منشور | منخفض | لا أرقام |
| Panoptic symbol spotting على CAD متجهي | CADTransformer (MIT)، SymPoint، SymPointV2، VecFormer (Apache-2.0) على FloorPlanCAD | يعمل على البدائيات المتجهية مباشرة | مدرَّب على مخططات معمارية لا كهربائية | متوسط (بحثي) | أوراق CVPR 2022 / ICLR 2024 / NeurIPS 2025 (لم أقرأ أرقامها) |
| Template matching | ورد في المراجعات كطريقة أقدم (Rezvanifar et al. 2019؛ ملخص SINA) | بسيط، بلا بيانات تدريب | هش مع التدوير والضوضاء | عالٍ (OpenCV) | المراجعة تذكر استدعاء جيداً ودقة ضعيفة |
| Few-shot للرموز النادرة | **لم أجد مستودعاً مفتوحاً مخصصاً** للمخططات الكهربائية | — | — | — | البدائل الموجودة: CLIP nearest-neighbour، VLM zero-shot، وتوليد بيانات بـ GAN (أوراق SLD: MFCGAN/DCGAN/LSGAN) |
| Tiling / sliced inference | SAHI (MIT) في STA [كود]؛ tiles في DiagEx وdeepkick وpid-parser | يحفظ تفاصيل الرموز الصغيرة في الصفحات الكبيرة | يولّد تكرارات على الحواف (يحتاج dedup/seam filter) | عالٍ | DiagEx ablation بلا tiles: F1 المعدات −0.189 والأجهزة −0.257 [results]؛ deepkick: ΔF1 +0.258 على المخطط الكبير [README] |

### 2.2 كشف الأسلاك والخطوط ونقاط الوصل

| الخيار | من يستخدمه | المزايا | العيوب | النضج | الأدلة |
|---|---|---|---|---|---|
| Probabilistic Hough (`cv2.HoughLinesP`) | Azure P&ID [كود]، PID2Graph modular [ورقة]، TaylorTurnerIT SLD [README] | بسيط وسريع | ضوضاء، تقطيع الخطوط المتقطعة | عالٍ | Hemker et al.: "noisy" في تجاربهم، فاستبدلوه [ورقة] |
| Thinning/Skeleton (Zhang–Suen) | Azure P&ID (`cv2.ximgproc.thinning`) [كود]، CircuitDiagOCR [README]، C-R-Kelly [README] | يحوّل السماكة إلى خط مركزي | يولّد spurs وتشظي عند الزوايا اليدوية | عالٍ | CircuitDiagOCR يوثق فشل الزوايا اليدوية المتجاوزة [docs] |
| Wireframe parsing (L-CNN، HAWP) | Hemker (L-CNN) [ورقة]، wanghaoyu (HAWP) [كود] | يكشف قطعاً ونقاط وصل معاً | نماذج بحثية ثقيلة | منخفض–متوسط | Hemker: sAP في شكل بلا أرقام نصية [ورقة] |
| تقطيع (U-Net، SAM2) | Bayer et al. (U-Net) [ورقة]، mah-sam (SAM2 + LoRA) [README]، AMSnet 2.0 (تقطيع للشبكات) [ورقة] | يفصل الحبر عن الخلفية حتى في الصور الملتقطة | يحتاج أقنعة موسومة | متوسط | Bayer: دقة بكسل 98.16% (validation) [ورقة] |
| Connected-component labeling على قناع الأسلاك | SINA [ورقة]، pid-parser [README]، خط أساس CircuitDiagOCR | بسيط وقوي مع الأقنعة النظيفة | يدمج كل تقاطع بلا نقطة (crossing) خطأً | عالٍ | pid-parser: مع صناديق الحقيقة F1 الاتصال 0.98 [README]؛ CircuitDiagOCR: CCL أفضل من منطقه المعقد على بيانات حقيقية (11/13 مقابل 9/13) [docs] |
| تمييز junction عن crossing | CircuitDiagOCR (نقطة الوصل/تزويج المماسات) [README/docs]؛ SINA (crossing-wire detection) [ورقة]؛ Image2Net (jumper identification بـ YOLO OBB: 1,983 صورة، 28,195 تقاطعاً) [README/ورقة]؛ CGHD تضم فئتي `junction` و`crossover` [كود classes.json] | يحل أكبر مصدر لأخطاء الدمج | حساس للدقة وسماكة الخط | منخفض–متوسط | CircuitDiagOCR اصطناعي: 60/60 مقابل CCL 36/60 [docs] |
| Union-Find على قطع PDF متجهية | MEPdetect [كود] | دقة كاملة، يحفظ الـ taps | يحتاج تمييز طبقة/سماكة الأسلاك | منخفض–متوسط | لا أرقام |
| تجميع قطع SVG من PDF | hyogrin SLD [README] | يستغل الطبقات | يعتمد على تسمية الطبقة | منخفض | لا أرقام |
| BFS على شبكة مضغوطة | TaylorTurnerIT SLD [README] | بسيط | فقدان دقة | منخفض | لا أرقام |

### 2.3 قراءة النصوص والتاغات

| الخيار | من يستخدمه | المزايا | العيوب | النضج | الأدلة |
|---|---|---|---|---|---|
| Tesseract | MEPdetect (pytesseract حول homeruns) [كود] | محلي، Apache-2.0 | ضعيف مع النص الصغير/المدوّر | عالٍ | — |
| PaddleOCR (PP-OCRv5) | STA [كود]؛ XD-mu (PaddleOCR) | Apache-2.0؛ دقيق مع النص الصغير | اعتماديات ثقيلة | عالٍ | Hemker قارن PaddleOCR وdocTR وEasyOCR ووجد EasyOCR الأفضل لبياناتهم [ورقة] |
| EasyOCR (+ CRAFT) | SINA [ورقة]، Hemker (CRAFT مُعاد ضبطه) [ورقة]، PID2Graph modular [ورقة] | 80+ لغة | أبطأ | عالٍ | SINA: استخراج النص 97.55% [ورقة] |
| docTR | ضمن مقارنة Hemker [ورقة] | Apache-2.0 | — | عالٍ | لم يتفوق في مقارنة Hemker |
| OCR سحابي (Azure Document Intelligence، Amazon Bedrock Data Automation) | Azure P&ID [كود]، AWS guidance [README] | دقة عالية ودعم الصفحات الكبيرة | كلفة وارتباط بمزوّد | عالٍ | AWS: $105 شهرياً لـ OCR 100 P&ID (تقدير الـ README) |
| PARSeq / CRNN | wanghaoyu (PARSeq) [كود]، yemenoglu (CRNN) [README] | للنص اليدوي | تدريب مخصص | متوسط | — |
| VLM كقارئ نص للمناطق | BLUEPRINT (LLaMA-4 على مناطق YOLOv8) [ورقة]؛ DiagEx (مطابقة تاغ OCR EM 0.77 متوسط 3 مخططات) [results] | يفهم السياق | هلوسة ممكنة | منخفض–متوسط | — |
| OCR مقسّم (tiles) | STA (1280/640) [كود] **مقابل** Azure (جرّب tiling ولم يجد فائدة فلم يطبقه) [وثائق] | يقلل التاغات المفقودة (حسب STA) | تكرارات تحتاج دمجاً | — | **تعارض**: STA يعرض تحسناً، وAzure لم يجد "significant benefit" — يختلف حسب محرك OCR وحجم الصورة (**ملاحظة الباحث**) |
| دمج سطور التاغ المنقسمة | STA ("FIT"+"101" → "FIT 101") [كود]؛ Azure (دمج النصوص داخل الرمز) [وثائق] | يعيد التاغ الكامل | قواعد هندسية حساسة | منخفض | — |

**أنماط regex للتاغات الموجودة فعلياً في الكود**:

| المشروع | النمط | الغرض |
|---|---|---|
| dimitri009/STA | `^[A-Z]{1,5}-?\d{1,5}[A-Z]?$` | تاغ أجهزة P&ID (ISA) [كود] |
| DynMEP/MEPdetect | `CIRCUIT_NUMBERS = ^\d{1,2}(?:\s*[,/]\s*\d{1,2})+$`؛ `SHEET_REFERENCE = ^[EMPAS]\d{3,4}[-‐-―]\d{1,2}$` (لاستبعاد أرقام الصفحات من تاغات الدوائر) | دوائر لوحات المباني [كود] |
| EPDF-Extractor/indu-doc-transformer | `TAGWORD: /([=+-.][A-Za-z0-9_]+)+/` + `@dotted.name=value` | بحث بتاغ IEC 81346 [كود] |
| Canary-ECAD/ecad-view | `(?:==\|=\|\+\+\|\+\|&)([^=+&#]+)` | تقسيم معرّف البنية في EPLAN [كود] |
| arabaya3/PanelPilot | `\b(?:f\|e\|al\|alm\|err\|flt\|fc\|ec)-?\d{2,5}\b` وأخواتها + أرقام بارامترات (`21.03`، `P-1204`) | رموز أعطال المحركات/المغيرات [كود] |
| Azure P&ID | رفض نصوص المقاسات بالبوصة `^([0-9]+\|[0-9]+/[0-9]+)["%*]+$` | تنقية تاغات الرموز [كود] |

لم أجد نمطاً لصيغ أرقام الأسلاك أو صفوف الأطراف من نوع `X1:14` ولا للمراجع المتقاطعة من نوع `/12.3` في أي مستودع مدروس.

### 2.4 بناء الاتصال (netlist / connectivity graph)

| الخيار | من يستخدمه | المزايا | العيوب | النضج | الأدلة |
|---|---|---|---|---|---|
| قواعد قرب هندسي + BFS | Azure P&ID (Shapely + NetworkX، BFS عبر خطوط/أسهم) [كود] | قابل للتفسير والتصحيح | حساس للفجوات والعتبات | متوسط | — |
| مكون يلمس كائنين = حافة | Bayer et al. [ورقة] | بسيط | يفشل مع الشبكات متعددة الأطراف | منخفض | نتيجة نوعية على عينة واحدة فقط [ورقة] |
| Connected components على القناع بعد حذف الرموز | SINA [ورقة]، pid-parser [README] | فعّال مع الصور النظيفة | يتطلب صناديق دقيقة؛ يدمج التقاطعات | متوسط | SINA: بنية الدائرة 99.3% مقابل 59.8% لـ Masala-CHAI على 40 مخططاً [ورقة] |
| مطابقة ثنائية مثلى (Hungarian) للنص ↔ الرمز | STA [كود] | حل عالمي بدل جشعي | يتطلب دالة كلفة مضبوطة | منخفض | — |
| GNN للاتصال | PIDCon [README] | يتعلم من بيانات الاتصال | بلا ترخيص؛ بيانات جزئية | منخفض | F1 0.963، GED 0.017، NCA 0.975 مقابل position-based F1 0.933 [README] |
| End-to-end (Relationformer) | PID2Graph [ورقة] | يتعلم الرموز والحواف معاً | لا كود منشور وجدته | منخفض | انظر 2.1 |
| VLM للإدراك + تسوية حتمية | DiagEx [كود] | لا تدريب؛ مخرجات DEXPI | الحواف ضعيفة | منخفض | F1 الحواف 0.21–0.40 [results] |
| VLM للرموز + رؤية تقليدية للاتصال | pid-parser [README] | يجمع قوة كل منهما | يفقد الخطوط المتقطعة | منخفض | F1 الاتصال 0.96 مقابل 0.29 للـ VLM وحده (ورقة واحدة) [README] |
| Union-Find على PDF متجهي + دمج عبر الصفحات بالتاغ | MEPdetect [كود] | يحفظ الدقة | يحتاج قواعد خاصة بالمجال | منخفض–متوسط | — |
| قراءة الاتصال من بيانات CAD | ecad-view (`mergedconnection`)، indu-doc (جداول التقارير)، eplan-mcp (`XMExportConnectionsAction`) [كود] | لا حاجة لرؤية حاسوبية | خاص بـ EPLAN | منخفض–متوسط | — |

**مقاييس تقييم الاتصال المستخدمة**: edge mAP بمطابقة Hungarian وgIoU (PID2Graph)؛ graph isomorphism (SINA)؛ Netlist Edit Distance (Image2Net)؛ GED وNCA (PIDCon)؛ exact pin partition وpairwise P/R/F1 وsplit/merge (CircuitDiagOCR)؛ F1 أزواج الرموز المتصلة (pid-parser، deepkick).

### 2.5 الاستخراج المباشر من PDF المتجهي مقابل الرؤية الحاسوبية

| الجانب | المسار المتجهي | مسار الرؤية |
|---|---|---|
| من يستخدمه | MEPdetect (`get_drawings`، `get_text('words')`) [كود]؛ hyogrin (PDF→SVG) [README]؛ indu-doc (جداول) [كود]؛ BLUEPRINT (PyMuPDF كبديل نصي) [ورقة]؛ IEEE 7301510 (P&ID متجهي) [ملخص فقط] | Azure، SINA، STA، Bayer، Hemker، DiagEx (يحوّل PDF إلى صور عمداً) |
| الدقة الهندسية | كاملة؛ يحفظ الـ taps والفجوات الصغيرة (حسب docstring في MEPdetect) | محدودة بدقة البكسل |
| النص | نص حقيقي بإحداثيات | يحتاج OCR |
| التحدي | تمييز السلك عن الإطار/الجدول/الرمز؛ MEPdetect يستخدم سماكة القلم | كشف الرموز والخطوط وتقاطعاتها |
| مقارنة كمية مباشرة | **لم أجد** مقارنة منشورة بين المسارين على نفس المخططات الكهربائية | — |
| موقف مخالف | DiagEx يحوّل PDF إلى صور ويستبعد CV التقليدي كجزء من أطروحته البحثية [ARCHITECTURE.md] | — |

### 2.6 VLMs: القدرة والحدود الموثقة

| الدليل | النتيجة | النوع |
|---|---|---|
| EEE-Bench (CVPR 2025) | GPT-4o أفضل نموذج بدقة 46.78%؛ >50% من الأخطاء استدلالية و26.5% إدراكية | ورقة |
| OmniSch (arXiv 2604.00270) | 1,854 مخطط PCB حقيقي؛ النماذج تتعثر في grounding الدقيق وتحويل التخطيط إلى رسم والاتصال العام | ورقة |
| Hitachi (near sight correction) | VLMs تتعرف على المكونات وتفشل في استنتاج الاتصال (التقاطعات والوصلات) | صفحة بحث |
| deepkick/pid2graph-vlm-eval | حواف F1 ≈ 0.03 (OPEN100، 12 مخططاً) مقابل عقد F1 0.80–0.88 | README |
| dzakybd/pid-parser | اتصال F1 0.29 (VLM صورة كاملة) و≤0.22 (قراءة الأنابيب بالوكيل) مقابل 0.96 مع CCL | README |
| hkoziolek/DiagEx-Repro | حواف F1 0.21–0.40 مقابل معدات/أجهزة 0.67–1.00 | results في الكود |
| SINA | يستخدم GPT-4o للتحقق وتعيين reference designators، ويحيل عدم تطابق عدد المكونات بين VLM وYOLO للمراجعة البشرية | ورقة |
| Circuit-MLLM (2609.15668) | GPT-4o بمتوسط 62.16 على معيارهم ونموذج 7B مدرَّب أعلى؛ أعمدة الاتصال غير واضحة في المقتطف — **لا أعتمد أرقاماً تفصيلية** | ورقة (مقتطف) |
| CircuitSense (2509.22339) | 8,006+ مسألة لاشتقاق معادلات من مخططات | ورقة |
| BLUEPRINT (2602.13345) | في الاسترجاع: خط أنابيب layout-aware يتفوق على VLMs كاملة الصفحة (Success@3 0.715 مقابل 0.623) | ورقة |

**نمط متكرر عبر مصادر مستقلة**: أداء VLMs على تحديد الرموز/المعدات أعلى بكثير من أدائها على استخراج الاتصال (5 مصادر أعلاه). هذا استنتاج من الأدلة المذكورة، وليس قياساً على مخططات تحكم صناعية بنمط EPLAN — **لم أجد أي تقييم منشور لـ VLM على مخططات EPLAN أو مخططات خزائن التحكم** (W25).

### 2.7 المراجع المتقاطعة بين الصفحات، وقوائم الأجهزة والكابلات والأطراف

| الآلية | من يستخدمها | ملاحظات |
|---|---|---|
| روابط `jumpToFunction` في SVG + حزم `interruptionpoint` (مرجع ep.19007 + صفحات) | ecad-view [كود] | من بيانات EPLAN المصدَّرة، لا من صورة |
| عقد Connector خارج الصفحة (معرّف ~4 أحرف) وحواف ResidesIn/OutputsTo/InputsTo | Azure P&ID [وثائق design-db] | نُمذج في قاعدة الرسم؛ استخراجه من الصورة عبر OCR |
| دمج الدائرة عبر الصفحات بنفس التاغ | MEPdetect `merge_cross_sheet_circuits` [كود] | يعتمد على تطابق نص التاغ |
| جداول تقارير EPLAN (Connection list، Terminal diagram، Cable diagram، PLC diagram بعنوان `plc_addr`) | indu-doc [كود] | يتطلب أن تكون صفحات التقارير ضمن الـ PDF |
| master/slave يتحدث آلياً | QElectroTech [README] | داخل أداة CAD |
| تقرير المراجع المتقاطعة (مع كشف جهات اتصال بلا ملف أو ملف بلا جهات) | AutoCAD Electrical (وثائق Autodesk) | أداة تجارية، للمقارنة |
| تقسيم صفحات آلي بمراجع وروابط قابلة للنقر في PDF | uniwire [README] | في التوليد لا الاستخراج |
| **تحليل نص المرجع المتقاطع (مثل `/12.3` أو `4K5`) من مخطط ممسوح** | **لم أجد أي مشروع مفتوح** | فجوة |

---

## 3. تمثيل المعرفة

### 3.1 نماذج الرسم البياني الموجودة فعلياً

| المشروع | أنواع العقد | الحواف/العلاقات | التخزين | الربط بالوثيقة |
|---|---|---|---|---|
| Azure P&ID [وثائق/كود] | PnID، Sheet، Asset، AssetType، Connector | BelongsTo، IsPartOf، ConnectedTo، LabeledWith، ResidesIn، OutputsTo، InputsTo | Azure SQL Graph؛ NetworkX أثناء البناء | bounding boxes وsheet |
| indu-doc-transformer [كود] | XTarget (جهاز/كابل/صف أطراف)، Pin، Link، Connection، Aspect، Attribute | src_pin → dst_pin عبر Link | DB (peewee)، AML | PagesObjectsMapper: كائن ↔ (صفحة، ملف) |
| ecad-view [كود] | Function، Page، MergedConnection، InterruptionPoint، Location، InstallationSpace | source/target/potential؛ page_functions؛ page_interruptionpoints | SQLite (manifest.db) | معرّف عنصر SVG (IdX_Y) لكل كائن |
| uniwire [README] | 53 نوعاً (مصادر، قواطع، كونتاكتورات، أطراف، أحمال، PLC...) | شجرة تغذية + روابط (تعشيق، فصل، قياس، تحكم، ناقل بيانات) → terminal/connectivity-node | JSON | معرّفات ثابتة (`uniwire describe`) |
| CircuitDiagOCR [README] | Pin، Physical Net، Logical Net، branch site | pin ∈ net؛ أدلة بكسل | JSON بمخطط | مسارات بكسل كدليل |
| pid-parser [README] | valve...، instrument، off_page، junction | process / signal | CSV، SQL، GraphML، Cypher | bbox |
| DiagEx [كود] | ReconciledGraph → فئات DEXPI 2.0 | اتصالات DEXPI | JSON/XML | annotations مع bbox |
| IndustryGraphRAG [كود] | Equipment، Component، Symptom، Cause، Solution، Threshold، Document | HAS_COMPONENT، HAS_SYMPTOM، CAUSED_BY، SOLVED_BY، OCCURS_ON، APPLIES_TO، RELATED_TO، MENTIONED_IN | Neo4j 5 | عقد Document وعلاقة MENTIONED_IN |
| industrial-agent-harness [README] | Équipement، Symptôme، Historique، Procédure، Capteurs | مسار multi-hop | Neo4j | مصادر Neo4j في طلب الموافقة |
| MaintIE (مخطط وسم) [README/ورقة] | 5 فئات عليا و224 كياناً فرعياً | 6 علاقات | JSON | نصوص أوامر العمل (شرائح IOF تذكر توافق الكائنات الفيزيائية مع IEC 81346) |
| أنطولوجيات/معايير | CIM/CGMES (Terminal، ConnectivityNode)، IEC 61850 SCL، AutomationML→RDF (2504.21694)، DEXPI | — | RDF/XML | — |

**ملاحظة**: لم أجد مشروعاً مفتوحاً يجمع في رسم واحد أنواع العقد التي يحتاجها تتبع العطل في خزائن التحكم معاً (جهاز، طرف، رقم سلك، potential/net، صفحة/عمود، عنوان I/O، كابل، حالة/حادثة). الأقرب: ecad-view (اتصال + جهد + صفحات من EPLAN) وindu-doc (أطراف + PLC addr + صفحات).

### 3.2 قواعد البيانات والأدوات المستخدمة

| الأداة | الترخيص | من يستخدمها في المسح |
|---|---|---|
| Neo4j | GPL-3.0 (Community) | IndustryGraphRAG، industrial-agent-harness، PID-Digitization-using-Neo4j، pid-parser (تصدير Cypher) |
| NetworkX | NOASSERTION في الـ API (BSD-3 حسب المشروع — **لم أتحقق**) | Azure P&ID، pyDEXPI، indu-doc (اعتمادية) |
| Azure SQL Graph | تجاري | Azure P&ID |
| SQLite | Public domain | ecad-view (manifest.db)، maintenance-qa-agent (FTS5) |
| Memgraph | NOASSERTION | لم يظهر في أي مشروع مدروس |
| RDF/SPARQL | — | GhinwaFAKIH/industrial-graphrag-copilot [README]، watare/iec61850-scl-rdf-converter [README]، AML→RDF [ورقة] |

---

## 4. فهرسة الوثائق واسترجاعها

### 4.1 تحليل الكتيبات

| الأداة | الترخيص | من يستخدمها | الأدلة |
|---|---|---|---|
| Docling | MIT (69,524 نجمة) | RAGOps (structure-aware: 113 قطعة مقابل 23 بالنافذة المنزلقة؛ معيار 80% = 12/15) [README] | ورقة Docling تقارن السرعة لا دقة الجداول (89 ملفاً) [ورقة 2501.17887] |
| Marker | Apache-2.0 (الكود) | — | مقارنات بائعين فقط (Datalab، olmOCR-bench) — غير محايدة |
| MinerU | NOASSERTION | — | ورقة 2409.18839؛ مقارنات طرف ثالث تصف شروطها بأنها غير واضحة |
| Unstructured | Apache-2.0 | — | لا مقارنة محايدة على كتيبات تقنية |
| PyMuPDF / pdfplumber | AGPL-3.0 / MIT | indu-doc، MEPdetect، BLUEPRINT | — |
| VLM لوصف الأشكال | Sea-RAG [README]، ORNL agentic RAG [ملخص] | — | ORNL: الكتيبات تحمل حصة كبيرة من المحتوى البصري يفوتها RAG النصي |

**فجوة أدلة**: لا يوجد في المسح معيار محايد يقارن Docling وMarker وMinerU وUnstructured على كتيبات المعدات الكهربائية (خاصة الجداول بلا حدود والخلايا المدمجة).

### 4.2 التقطيع والـ metadata

| النمط | من يستخدمه |
|---|---|
| تقطيع حسب البنية (أقسام Docling) | RAGOps [README] |
| قطع بمعرّفات ثابتة تُستخدم للاستشهاد والتحقق | fixit-lens (citation_ids ⊂ retrieved ids) [كود]؛ PanelPilot (PassageId) [كود]؛ maintenance-qa-agent [كود] |
| metadata للجهاز/الماركة وترشيح بها | PLCPILOT (brand detection + brand filtering) [README]؛ PanelPilot (equipment_hint) [كود: اسم الملف] |
| metadata للمنطقة (رقم الرسم، المراجعة، المنشأة) | BLUEPRINT (YOLOv8 لأربع مناطق: drawing number، data block، parts list، revisions) [ورقة] |
| فصل staging/production مع طابور تحقق | PanelPilot [كود] |

### 4.3 البحث الهجين وإعادة الترتيب

| الخيار | من يستخدمه | التفاصيل |
|---|---|---|
| RRF (BM25 + dense) | maintenance-qa-agent (FTS5 + bge-small-en) [كود]؛ RAGOps (BGE + BM25 + phrase-match bonus) [README]؛ RetAgGen PowerUP (BM25 + dense + RRF) [مُجمِّع طرف ثالث] | بلا أوزان؛ يدمج الترتيب |
| مزج موزون بعد تطبيع min-max حسب نوع السؤال | PanelPilot (OpenSearch search pipeline؛ 0.6/0.4 افتراضياً؛ 0.15 للمتجه في أسئلة رموز الأعطال) [كود] | الأوزان مرتبطة بمصنف قواعد |
| مزج ثابت | PLCPILOT (FAISS 70% + BM25 30%) [README]؛ fixit-lens (BM25 + TF-IDF) [كود] | — |
| z-normalization لكل سؤال + reranker يكافئ تطابق المناطق ويعاقب عدم تطابق المراجعة | BLUEPRINT [ورقة] | — |
| إعادة ترتيب بـ LLM | IndustryGraphRAG (مسارات) [كود] | — |
| Cross-encoder reranking | لم أتحقق من مشروع مدروس يستخدمه (يُذكر في READMEs ممسوحة مثل luannamorim/docquery دون تحقق) | — |

### 4.4 المطابقة الحرفية للتاغات والرموز

| الأسلوب | من يستخدمه | الدليل |
|---|---|---|
| تصنيف السؤال بقواعد → وزن BM25 أعلى لرموز الأعطال | PanelPilot [كود] | تعليل في الكود: "every fault code in a manual is semantically adjacent to every other, so vector similarity is close to noise for it" |
| anchors رقمية: يجب ظهور رقم الرمز كاملاً في الفقرة (لا داخل رقم أطول) | PanelPilot `is_anchored` [كود] | — |
| فهرس حرفي منفصل (SQLite FTS5/bm25) مقابل دلالي | eplan-mcp (خادمان: 2026 دلالي، 2027 FTS5) [README] | "FTS5 wins exact-name lookups; semantic search wins when the query shares no vocabulary" — نوعي، بلا أرقام |
| مكافأة تطابق العبارة | RAGOps [README] | — |
| تحقق regex من الحقائق المفتاحية في الإجابة | PLCPILOT [README] | — |
| بحث منظم بالتاغ على كائنات مستخرجة (لا نص حر) | indu-doc (Lark `TAGWORD` + `@attr=`) [كود] | — |
| تطبيع التاغ | MEPdetect `normalize_tag` [كود]؛ PanelPilot (حذف البادئة: F0001 = F 0001 = F-0001) [كود] | — |
| دليل عام | BEIR (2104.08663): BM25 خط أساس قوي في التقييم zero-shot | ورقة |

لم أجد مشروعاً يعالج تحديداً رموز IEC 81346 المركبة (`=A1+P2-Q3`) أو `X1:14` داخل محرك بحث نصي (tokenizer مخصص) — الأقرب indu-doc الذي يبحث في كائنات منظمة لا في نص.

### 4.5 الاسترجاع متعدد الوسائط لصفحات المخططات

| الخيار | الترخيص | من يستخدمه | الأدلة |
|---|---|---|---|
| ColPali / ColQwen2 / ColSmol (late interaction) | MIT (illuin-tech/colpali، 2,843 نجمة) | byaldi (Apache-2.0)، Softlandia demo (ColPali + Qdrant)، morphik-core | ViDoRe (2407.01449): مجالات طاقة وحكومة وصحة؛ **لا مهمة رسومات هندسية** حسب بحثي |
| استرجاع layout-aware بمناطق + هجين | — | BLUEPRINT (أرشيف رسومات هندسية، 5k ملف، 350 سؤال خبير) | Success@3 0.715 / nDCG@3 0.626 مقابل 0.623/0.521 لأقوى VLM؛ لم يقارن بـ ColPali [ورقة] |
| CLIP لصور الرموز | — | eplan-mcp | لا أرقام |
| VLM يحوّل الأشكال إلى وصف نصي ثم RAG | — | Sea-RAG [README]، ORNL [ملخص] | — |

### 4.6 GraphRAG / LightRAG وأنماط الدمج

| الخيار | الترخيص | الاستخدام في المسح |
|---|---|---|
| microsoft/graphrag (2404.16130) | MIT | لم يظهر في مشروع كهربائي مدروس |
| HKUDS/LightRAG (2410.05779، EMNLP 2025) | MIT | لم يظهر في مشروع كهربائي مدروس |
| neo4j-graphrag-python | NOASSERTION | — |
| Cypher مخصص لمسارات عرض→سبب→حل | MIT | IndustryGraphRAG [كود] |
| GraphRAG multi-hop مع موافقة بشرية | Apache-2.0 | industrial-agent-harness [README] |
| رسم اتصال مستخرج + أسئلة VLM | — | DiagEx Phase 1 (أسئلة connectivity/counting/inventory) [results] |

**فجوة**: لم أجد مشروعاً مفتوحاً يدمج رسم الاتصال الكهربائي (أجهزة/أطراف/أسلاك) مع فهرس متجهي للكتيبات في نظام واحد لتتبع الأعطال.

### 4.7 التضمينات متعددة اللغات

| النموذج | الأدلة | الاستخدام في المسح |
|---|---|---|
| BGE-M3 (2402.03216) | MIRACL (18 لغة منها العربية): متوسط nDCG@10 = 70.0 (كل الأوضاع) مقابل ~65.4 لـ mE5 — **مبلّغ ذاتياً** من BAAI؛ لم أجد رقماً عربياً منفصلاً في البحث | RAGOps يذكر "BGE" [README] |
| multilingual-e5 (2402.05672) | تقرير تقني | — |
| bge-small-en-v1.5 (إنجليزي) | — | maintenance-qa-agent [كود] |
| text-embedding-3-small / Voyage | تجاري | PanelPilot [كود] |
| **حل عملي للاستعلام العربي** | PanelPilot: ساق BM25 لا يطابق السؤال العربي على كتيبات إنجليزية؛ الحل في الكود: ترجمة السؤال إلى استعلام بحث إنجليزي يحفظ الرموز والوحدات، مع التأليف بالعربية [كود query_translation.py] | — |
| الألمانية | indu-doc يتعرف على عناوين صفحات EPLAN بالألمانية (Klemmenplan، Kabelübersicht، artikelstückliste) [كود] | — |

### 4.8 قواعد المتجهات ومحركات البحث

| المحرك | الترخيص (API) | النجوم | من يستخدمه في المسح |
|---|---|---|---|
| OpenSearch | Apache-2.0 | 13,902 | PanelPilot (BM25 + kNN، search pipeline) [كود] |
| Elasticsearch | NOASSERTION | 78,370 | — |
| Qdrant | Apache-2.0 | 35,229 | Softlandia ColPali demo [README] |
| Milvus | Apache-2.0 | 46,469 | — |
| Weaviate | NOASSERTION | 16,907 | — |
| pgvector | NOASSERTION | 23,468 | — |
| Chroma | Apache-2.0 | 29,571 | — |
| FAISS | — (لم أتحقق عبر API) | — | PLCPILOT [README] |
| SQLite FTS5 + brute-force cosine | Public domain | — | maintenance-qa-agent [كود]، eplan-mcp RAG 2027 [README] |
| Cloudflare Vectorize | تجاري | — | eplan-mcp (وثائق ورموز) [README] |

---

## 5. منهجيات تشخيص الأعطال

| المنهجية | المشاريع/المصادر | الترخيص | الحالة في المجال الكهربائي | الأدلة |
|---|---|---|---|---|
| شجرة الأعطال (FTA) | rakhimov/scram (GPL-3.0، آخر push 2023-09)، volkm/dftlib (GPL-3.0)، jmateusms/faultree (BSD-3) | — | أدوات عامة؛ لم أجد ربطاً بمخططات كهربائية | — |
| FMEA/FMECA | YuchenXia/LLMRiskAnalyzer (بلا ترخيص)، BDSN-FDC (FMECA → شبكة بايزية، MIT)، ranjith FMEA RAG (ETFA 2026، README) | — | عام | — |
| الشبكات البايزية | pgmpy (MIT، 3,363 نجمة)، BDSN-FDC (pgmpy)، netops-fault-lab (اختيار probe تالٍ، MIT) | — | لم أجد تطبيقاً على مخطط كهربائي | netops: 49.8%/49.8% → 96.7% بعد probe واحد (اصطناعي) [README] |
| التشخيص المبني على النموذج / GDE | **لم أجد تنفيذ Python مصاناً لـ GDE** (W18)؛ DES-Lab/Automata-Learning-Based-Diagnosis (MIT، 2021)؛ "Efficient Model Based Diagnosis" (2209.09819) | — | لا شيء مخصص | نظرية de Kleer: اختيار القياس بأقل entropy متوقعة، عادة one-step lookahead |
| الأنظمة الخبيرة القاعدية | FaultTrace (أوزان ثابتة، شرط PLC الحاجب، الفحص التالي) [README]؛ Gabsolat/compressed-air-Network-diagnosis (Prolog) [وصف فقط] | MIT / بلا ترخيص | FaultTrace: آلة CNC اصطناعية | — |
| الاستدلال المبني على الحالات (CBR) | wi2trier/cbrkit (MIT، نشط 2026-10-24) | MIT | أداة عامة؛ لم أجد تطبيقاً على أعطال كهربائية | — |
| ربط الإنذارات وسجلات الأحداث | PyRCA (BSD-3: PC/GES/FGES/LiNGAM، Bayesian inference، random walk؛ مقاييس مستمرة لا أحداث منفصلة)؛ FaultTrace (إنذار HMI + منطق PLC + أحداث)؛ hsu-aut (timed automata + KG + وكلاء Claude Opus 4.6 على HaiCPPS) | — | لا شيء على خزائن كهربائية | — |
| تتبع المسار في الرسم | BFS (Azure)، Union-Find (MEPdetect)، Cypher متغير الطول (IndustryGraphRAG)، connected components (SINA، pid-parser) | — | — | — |
| اختيار نقطة القياس التالية / half-split | netops-fault-lab (شبكات)، FaultTrace ("next diagnostic check" قاعدي)، نظرية GDE | — | **لا تنفيذ مفتوح لـ half-split على دوائر تحكم كهربائية** | — |
| وكلاء LLM بالأدوات | DiagEx (tool use على tiles)، eplan-mcp (MCP، 199 أداة)، maintenance-qa-agent (بحث/حاسبة/RUL)، PanelPilot (أدوات حساب حتمية)، industrial-agent-harness (LangGraph)، AssetOpsBench (Tool-As-Agent مقابل Plan-Executor؛ 141 سيناريو) | متنوع | — | AssetOpsBench [ورقة 2506.03828] |
| الإنسان في الحلقة | Azure (تصحيح بين الخطوات)، industrial-agent-harness (موافقة حاجبة)، SINA (مراجعة عند عدم التطابق)، CircuitDiagOCR (علامة AUTO_DOT_FALLBACK للمراجعة) | — | — | — |
| التعلم من الحالات المحلولة | MaintIE (استخراج معلومات من أوامر العمل)، IndustryGraphRAG (سجلات صيانة كمصدر للرسم)، Azure (active learning للمخرجات المعتمدة — فكرة في الوثائق) | MIT / MIT / MIT | — | MaintIE: 1,076 نصاً دقيق الوسم و7,000 خشن [ورقة LREC-COLING 2024] |

---

## 6. السلامة والموثوقية

| الآلية | من يطبقها | تفاصيل التنفيذ | تحقق |
|---|---|---|---|
| بوابة cite-or-refuse بثقة من الاسترجاع لا من النموذج؛ الرفض يتجاوز التوليد كلياً | PanelPilot | `evaluate_confidence` نقطة قرار وحيدة؛ عتبة احتياطية 1.0 عند فشل الإعدادات | [كود] |
| حذف الخطوات غير المستشهد بها | fixit-lens | `validate_steps`: citation_ids غير فارغة وكلها ⊂ المسترجع؛ نسبة تغطية | [كود] |
| حقول إلزامية للخطوة: citation_ids (غير مكررة) وseverity | PanelPilot | Pydantic `DiagnosisStep` | [كود] |
| امتناع معاير إحصائياً (conformal risk control) | maintenance-qa-agent | ضمان حد أعلى لمعدل الخطأ بين المُجاب عنها؛ نتائج مقاسة | [كود + results] |
| مصنف خطر قاعدي قبل التوليد ومستقل عن LLM | fixit-lens (12 فئة محظورة، 4 مستويات)، PLCPILOT (4 مستويات: كهربائي، ميكانيكي، فئة العطل) | كلمات مفتاحية | [كود] / [README] |
| فحص هلوسة | PLCPILOT (containment overlap + regex key-facts) | — | [README] |
| تصعيد عند ثقة <50% | PLCPILOT | — | [README] |
| موافقة بشرية حاجبة + سجل تدقيق HMAC متسلسل | industrial-agent-harness | `require_approval`؛ `verify_audit_chain()` | [README] |
| فصل الإدراك (LLM) عن البناء (حتمي) | DiagEx | ReconciledGraph بكود Python | [كود] |
| فهرس staging منفصل عن production | PanelPilot | البحث في staging فعل صريح منفصل | [كود] |
| رفض متجه بعرض خاطئ ورفض متجه صفري عند تعطل المزوّد | PanelPilot | لمنع "confidently wrong retrieval with citations attached" | [README] |
| SafeLLM: طبقة مراقبة سلامة مجالية | ورقة (2410.10852، صيانة توربينات الرياح البحرية) | نتائج أولية | [ورقة] |
| **LOTO / IEC 60204-1 / NFPA 70E** | **لا أحد** من المستودعات المنسوخة الـ 15 (grep على `lockout`، `tagout`، `LOTO`، `60204`، `NFPA 70E` في ملفات py/md/yaml/json/ts؛ استُبعدت تطابقات رقمية عرضية في cassettes الخاصة بـ DiagEx) | ذكر arc-flash فقط في تعليق PanelPilot (حول severity)، وفي نص كتيب داخل بيانات fixit-lens | [كود/grep] |

---

## 7. محاور إضافية اكتُشفت أثناء البحث

| المحور | ما وُجد | الأدلة |
|---|---|---|
| **الوصول "الأصلي" للوكلاء إلى أدوات CAD/PLC عبر MCP** | eplan-mcp (199 أداة)، TIA_Portal_Openness_MCP (346 نجمة، MIT)، tiacommander-mcp، tia-portal-openness-mcpserver، uniwire-mcp، HR-DONK circuit-recognition MCP، Copperbrain (KiCad) | [README]/[كود] لـ eplan-mcp |
| **تصدير EPLAN كمصدر بيانات منظم بدل الرؤية** | ثلاثة مسارات مستقلة: `.epdz` (ecad-view)، PDF التقارير (indu-doc)، API الحي (eplan-mcp) | [كود] |
| **مشهد التراخيص** | اعتماديات أساسية بـ AGPL-3.0 (PyMuPDF، Ultralytics، pyDEXPI، MEPdetect، Pinscope)؛ كثير من المستودعات البحثية بلا ترخيص (PanelPilot، CircuitDiagOCR، PIDCon، Masala-CHAI، wanghaoyu، NYCU-AI-EDA/Netlistify، ECNUACRush/PCBnet — حقل الترخيص فارغ في الـ API؛ وتدقيق CircuitDiagOCR يذكر أن كود PCBnet غير منشور لأسباب ملكية فكرية)؛ DiagEx استبدل pyDEXPI لتجنب AGPL | [API]/[كود] |
| **الكلفة والزمن كمقياس** | DiagEx ($0.63–$10.35 لكل P&ID)؛ pid-parser ($0.34–$0.74، 35 ث–3.2 د)؛ AWS ($879/شهر لنقطة الاستدلال) | [results]/[README] |
| **إعادة الإنتاج والتقييم** | DiagEx (cassettes)، CircuitDiagOCR (مخططات JSON + مقاييس partition)، maintenance-qa-agent (runs/*.json) | [كود] |
| **AAS (Asset Administration Shell)** | eplan-mcp يقدّم أدوات aas_* للتصدير/الاستيراد؛ SAP aas-transformation-library (مؤرشف) يحول AML إلى AAS | [README] — لم أبحث في VDI 2770/توثيق المصنّعين بشكل مستقل |
