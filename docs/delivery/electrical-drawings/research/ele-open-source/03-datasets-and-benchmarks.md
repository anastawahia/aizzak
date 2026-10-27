# مجموعات البيانات ومعايير التقييم (03-datasets-and-benchmarks)

> تاريخ الإعداد والوصول: 2026-10-25. القاعدة: لا تُدرج مجموعة في القسم (أ) إلا إذا تحققت من وجود الرابط ومن الترخيص من مصدر أولي (ملف LICENSE، أو حقل الترخيص في GitHub API، أو Zenodo API، أو Hugging Face API). القسم (ب) لمجموعات تحققت من وجود رابطها لكن ترخيصها غائب أو مقيد. القسم (ج) لما ورد في الأدبيات ولم يكتمل التحقق منه — **لا يُعتمد عليه**.

## (أ) مجموعات متحقق من رابطها وترخيصها

### مخططات كهربائية/إلكترونية

| المجموعة | الرابط | الترخيص (المصدر) | المحتوى | المهام | ملاحظات |
|---|---|---|---|---|---|
| **CGHD** (Circuit Graph Hand-Drawn) | https://zenodo.org/records/15333233 ؛ https://github.com/DFKI/cghd | **CC-BY-4.0** (Zenodo API، الإصدار 15، 2025-05-03)؛ **CC0-1.0** (ملف LICENSE في مستودع GitHub) — **تعارض بين المصدرين** | 3,269 صورة خام من 31 راسماً (12 دائرة × 2 رسم × 4 صور لكل راسم)؛ صناديق PASCAL VOC؛ أقنعة تقطيع لبعض الصور؛ netlists بصيغة LTspice ASC لبعضها؛ `classes.json` بـ 53 فئة منها `junction` و`crossover` و`text` و`relay` و`fuse` و`motor` | كشف رموز، تقطيع، استخراج رسم | مرسوم يدوياً (تعليمي)؛ مستودع GitHub الآن يحوي README وclasses.json فقط والبيانات على Zenodo. الإصدار المستخدم في 2402.11093: Zenodo 10056817 (2,424 صورة، 201,142 صندوقاً، 59 فئة) |
| **CI2N / Image2Net datasets** | https://github.com/LAD021/ci2n_datasets | **Apache-2.0** (GitHub API)؛ تدقيق CircuitDiagOCR ينقل أن المستودع يطلب أيضاً احترام تراخيص المواد الأصلية | حسب الـ README والورقة 2508.13157 (v2): 2,914 مخطط دوائر تماثلية، 84,195 وسماً؛ تقاطعات/jumpers في 1,983 صورة (28,195 تقاطعاً، YOLO OBB)؛ 12,086 وسماً لاتجاه/انعكاس المكونات؛ 104 أزواج مخطط-netlist مُتحقق منها يدوياً (122 حالياً حسب تدقيق CircuitDiagOCR) | كشف مكونات، تقاطعات، اتجاه، netlist | المقياس المقترح: Netlist Edit Distance (NED) |
| **IEC-electrical-symbols-dataset** (covaga) | https://huggingface.co/datasets/covaga/IEC-electrical-symbols-dataset | **MIT** (وسم HF API) | 33,502 صورة رمز EPLAN (IEC وNFPA وGB) مع `short_name` ورقم الكتالوج والوصف ووصف بصري مولَّد | تصنيف رموز، vision RAG | **ملاحظة الباحث**: الصور مأخوذة من مكتبات رموز EPLAN؛ وسم MIT من الرافع ولا يحسم حقوق المصدر |

### P&ID (مخططات قريبة بنيوياً)

| المجموعة | الرابط | الترخيص (المصدر) | المحتوى | المهام | ملاحظات |
|---|---|---|---|---|---|
| **PID2Graph** | https://zenodo.org/records/14803338 | **CC-BY-SA-4.0** (Zenodo API، 2025-02-14) | P&IDs مع رسومها البيانية (graphml)؛ اختبار اصطناعي: 23,323 عقدة و23,994 حافة؛ OPEN100: 12 مخططاً حقيقياً مشروحاً يدوياً (2,173 عقدة، 2,100 حافة) | كشف رموز + حواف (رسم كامل) | حسب الورقة أول مجموعة P&ID عامة بحقيقة رسم كاملة من بيانات حقيقية؛ استخدمها deepkick وpid-parser وDiagEx |
| **FloorPlanCAD** (للمقارنة: رموز على CAD متجهي) | https://floorplancad.github.io/ | **CC BY-NC 4.0** للوسوم والموقع (حسب الموقع)؛ المؤلفون لا يملكون حقوق الرسومات | 15,663 رسم CAD بصيغة SVG مع وسوم | panoptic symbol spotting | غير تجاري؛ مخططات معمارية لا كهربائية؛ المشروع أُغلق مطلع 2022 حسب الموقع |

### استرجاع الوثائق متعدد الوسائط

| المجموعة | الرابط | الترخيص | المحتوى | ملاحظات |
|---|---|---|---|---|
| **ViDoRe** (benchmark code + بعض مجموعات الاختبار) | https://github.com/illuin-tech/vidore-benchmark ؛ مثال: https://huggingface.co/datasets/vidore/syntheticDocQA_energy_test | **MIT** للكود (GitHub API)؛ **MIT** لـ `vidore/syntheticDocQA_energy_test` و`vidore/shiftproject_test` (HF API) | استرجاع صفحات بصرية متعدد المجالات واللغات | مجموعات التدريب `vidore/*_train` بلا وسم ترخيص في الـ API؛ **لا مهمة رسومات هندسية** ضمن ما وجدته |

### الصيانة الصناعية ووكلاء التشغيل

| المجموعة | الرابط | الترخيص | المحتوى | ملاحظات |
|---|---|---|---|---|
| **AssetOpsBench** (IBM) | https://github.com/IBM/AssetOpsBench | **Apache-2.0** (GitHub API؛ 2,384 نجمة) | إطار تقييم وكلاء لعمليات وصيانة الأصول الصناعية؛ 141 سيناريو (حسب مدونة IBM) / "over 140" (حسب الورقة 2506.03828)؛ أربعة وكلاء مجال؛ بيئة IoT محاكاة على CouchDB | مقارنة Tool-As-Agent وPlan-Executor؛ ليست مخططات كهربائية |
| **FailureSensorIQ** (IBM) | https://github.com/IBM/FailureSensorIQ | **CC-BY-4.0** (GitHub API) | 8,296 سؤال اختيار من متعدد (2,667 بإجابة واحدة، 5,629 متعددة) عن علاقات الحساسات وأنماط الأعطال لـ 10 أنواع أصول؛ مصدره وثائق ISO؛ NeurIPS 2025 | معرفة مجالية للـ LLM |
| **MaintIE** | https://github.com/nlp-tlp/maintie | **MIT** (GitHub API) | مخطط وسم: 5 فئات عليا، 224 كياناً فرعياً، 6 علاقات؛ 1,076 نصاً دقيق الوسم و7,000 نص خشن من أوامر عمل الصيانة (LREC-COLING 2024) | استخراج معلومات من الحالات المحلولة؛ شرائح IOF تذكر توافق الكائنات الفيزيائية مع IEC 81346 |

## (ب) مجموعات بيانات موجودة الرابط لكن ترخيصها غائب أو مقيد

| المجموعة | الرابط | حالة الترخيص | المحتوى (حسب المصدر) |
|---|---|---|---|
| Dataset-P&ID (Digitize-PID، 2109.03794) — مرايا HF | https://huggingface.co/datasets/hamzas/digitize-pid-symbols ؛ https://huggingface.co/datasets/SherifAhmed/digitize-pid-yolo | **لا وسم ترخيص** في HF API؛ الأصل على Google Drive بلا ترخيص معلن | 500 P&ID اصطناعي، 32 رمزاً (استخدمها Azure P&ID وPID2Graph) |
| PIDCon | https://github.com/sad123-yx/PIDCon (رابط Baidu Disk في الـ README) | **لا ترخيص** (GitHub API) | 600 صورة P&ID، 82 فئة، 7,212 زوج اتصال؛ النسخة العامة جزئية |
| Netlistify dataset | https://huggingface.co/datasets/hanky2397/schematic_images ؛ https://github.com/NYCU-AI-EDA/Netlistify | **gated (auto)** ولا ترخيص في HF API؛ المستودع بلا ترخيص (GitHub API) | صور مخططات (تفاصيل غير مقروءة) |
| Masala-CHAI | https://github.com/jitendra-bhandari/Masala-CHAI | **لا ترخيص** (GitHub API) | حسب الورقة 2411.14299: 7,500 مخطط من 10 كتب مع SPICE netlists |
| PCBnet | https://github.com/ECNUACRush/PCBnet | **لا ترخيص** (GitHub API)؛ تدقيق CircuitDiagOCR: ~300 عينة عرض والكود غير منشور | حسب الورقة 2608.27923: مخططات بصيغة رسم pins واتصالات مع أقنعة أسلاك |
| EEE-Bench | https://github.com/Jike338/EEE-Bench | **لا ترخيص** (GitHub API) | معيار متعدد الوسائط للهندسة الكهربائية والإلكترونية (CVPR 2025)؛ GPT-4o 46.78% |
| JUHCCR-v1 | https://github.com/AyushRoy2001/JUHCCR-v1-A-Database-for-Hand-drawn-Electrical-and-Electronics-Circuit-Component-Recognition (يحيل إلى Kaggle) | **لا ترخيص** في GitHub API؛ ترخيص Kaggle لم أتحقق منه | مكونات دوائر مرسومة يدوياً للتصنيف |
| SINA benchmark | https://anonymous.4open.science/r/SINA-213F/ | الكود MIT حسب تدقيق CircuitDiagOCR (لم أتحقق مباشرة)؛ مجموعة الـ 40 مخططاً غير منشورة كاملة حسب التدقيق | 75 مخططاً للكشف و40 للمقارنة (الورقة) |
| RetAgGen PowerUP (مدونة PLC اصطناعية) | https://github.com/shenmali/RetAgGen-PowerUP | **لا ترخيص** (GitHub API) | مدونة صيانة PLC اصطناعية لتقييم RAG (حسب مُجمِّع طرف ثالث) |
| PID2Graph eval subsets داخل مستودعات | deepkick، pid-parser | تتبع ترخيص PID2Graph (CC-BY-SA-4.0) للبيانات؛ المستودعان: الأول بلا LICENSE، الثاني MIT | — |

## (ج) وردت في الأدبيات ولم يكتمل التحقق منها (لا يُعتمد عليها)

| الاسم | سبب عدم الإدراج |
|---|---|
| SESYD (Delalandre et al. 2010؛ مخططات كهربائية ومعمارية اصطناعية) | موقع المؤلف (mathieu.delalandre.free.fr) لم يستجب عند المحاولة؛ ترخيص Papers with Code ("MIT") غير منسوب للمجموعة نفسها |
| AMSnet / AMSnet 2.0 (2405.09045 / 2505.09155؛ 2,686 دائرة) | لم أتحقق من مستودع رسمي وترخيص (نتيجة `lll143653/amsnet` خاطئة — مشروع إزالة ضوضاء) |
| OmniSch (2604.00270؛ 1,854 مخطط PCB) | لم أجد رابط بيانات/كود |
| CircuitSense (2509.22339) | يوجد مستودع موقع `circuitsense-benchmark/circuitsense-benchmark.github.io` بلا ترخيص؛ لم أتحقق من البيانات |
| SLED وبيانات SLD (UTP/Mahidol؛ 3,078 مثيلاً) | "متاحة للمجتمع الأكاديمي" دون رابط وجدته |
| DELP (SkeySpot، 2508.10449؛ 45 مخطط تمديدات كهربائية، 2,450 مثيلاً، 34 فئة) | لا رابط بيانات في صفحة الورقة |
| BLUEPRINT benchmark (2602.13345؛ 5k ملف، 350 سؤالاً) | المؤلفون يذكرون الإصدار؛ لم أجد رابطاً |
| HaiCPPS (مرجع hsu-aut) | استعلام `repo:j-ehrhardt/haicpps` لم يُعد نتيجة |
| بيانات Hemker et al. (Microchip، 67 PDF Altium) | "Data available on request" — غير عامة |
| Kaggle CGHD1152 (johannesbayer/cghd1152) | ذُكر في README مستودع mah-sam؛ لم أتحقق من الصفحة والترخيص |

## (د) فجوة البيانات الأساسية

**لم أجد أي مجموعة بيانات عامة متحقق من ترخيصها لمخططات تحكم صناعية مطبوعة بنمط EPLAN/IEC (مخططات دوائر تحكم بمراجع متقاطعة، صفوف أطراف، أرقام أسلاك، بطاقات I/O للـ PLC) مع حقيقة أرضية للاتصال.** أقرب البدائل: CGHD (يدوي تعليمي)، CI2N (تماثلي إلكتروني)، PID2Graph (P&ID). مجموعة صور رموز EPLAN (covaga) تغطي الرموز لا الاتصال، وأصل حقوقها غير واضح.

## (هـ) المقاييس المستخدمة في المشاريع والأوراق

| المرحلة | المقياس | أين استُخدم |
|---|---|---|
| كشف الرموز | mAP (IoU 0.5، أو 0.5:0.95)، Precision/Recall/F1 | PID2Graph، SINA، SkeySpot، Hemker، Bayer |
| العقد (رموز + تقاطعات + زوايا) | Node AP | PID2Graph |
| الحواف | edge mAP (Hungarian + gIoU)؛ F1 أزواج متصلة؛ GED؛ NCA | PID2Graph؛ pid-parser، deepkick، DiagEx؛ PIDCon |
| netlist كامل | graph isomorphism؛ Netlist Edit Distance (NED)؛ exact pin partition وpairwise P/R/F1 وsplit/merge | SINA؛ Image2Net؛ CircuitDiagOCR |
| OCR | character error rate؛ exact match للتاغ | Bayer (ورد في نسخة HTML الرقم 83.27% بوصف CER — ملتبس)؛ DiagEx (tag OCR EM) |
| التقطيع | pixel accuracy | Bayer (98.16%) |
| الاتجاه | accuracy ضمن ±5° | Bayer (53.37% مع قوالب) |
| الاسترجاع | nDCG@k، Success@k، MAP@k، P@k، R@k؛ MRR؛ keyword hit | BLUEPRINT؛ ViDoRe (nDCG)؛ IndustryGraphRAG (MRR) |
| الإجابة | faithfulness، citation accuracy، refusal correctness | RetAgGen PowerUP (حسب مُجمِّع)؛ fixit-lens (citation coverage) |
| الامتناع | selective risk، coverage، AUC، hold rate عبر تقسيمات متداخلة | maintenance-qa-agent |
| الكلفة | دولار لكل مخطط، tokens، زمن | DiagEx، pid-parser |
