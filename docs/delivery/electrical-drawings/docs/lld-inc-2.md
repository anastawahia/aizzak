<div dir="rtl">

# التصميم التفصيلي (LLD) للزيادة INC‑2 «الاستخراج المتجهي واسترجاع النص والتخطيط والعرض»: WireTwin

| البند | القيمة |
|---|---|
| المعرّف | `electrical-drawings` · الزيادة `INC‑2` |
| نوع الوثيقة | تصميم تفصيلي: ملفات، وتواقيع، وDDL، ونص العقود، وخطة اختبار. **لا شيفرة تُكتب في هذه الدورة** |
| الإصدار | 0.2 (**مُقَرّ للبناء**؛ قرارات المالك الستة في §7.1، وثلاث ملاحظات مراجعة: حدّ الـ25 دقيقة في `INC2‑Q‑2` و`G2`، و`G3` بعد `PR‑F` أيضاً، وأمثلة مجردة في §3.2.3) |
| التاريخ | 2026-11-25 (0.2)؛ 0.1 في اليوم نفسه |
| المدخل الملزم | [`hld.md`](hld.md) 0.21 (كُتبت 0.1 على 0.20، وجولة الوثائق في §8) (§4.4، §5.4، §6، §7، §8، §12، §13، §16، §18، وصفّ INC‑2 في §16)، و[`requirements.md`](requirements.md) 0.21، و[`lld-inc-1.md`](lld-inc-1.md) 0.7 (مبنيّ ومدموج) |
| المدخل المساند | `../inc-1/f1/design.md` (القرارات الستة و§4.6)، و`../inc-1/f2/requirements.md` و`stories.md` (US‑5..US‑8) و`design.md` (§7 و§8)، و`../inc-1/f3/design.md` (§2.1) و`gaps.md` و`measure-1..4.md`، و`../inc-1/characterization.md`، و`../inc-1/p1-fix-path.md`، و`../inc-1/f0/design.md` وتقارير المراجعة والقبول في `../inc-1/*/`، و`../status.md` «قرارات وتصعيدات» |
| الوثائق الحاكمة | `CLAUDE.md`، و`docs/design/00…12`، و`docs/design/openapi.yaml`، و`docs/design/events/schemas/`، و`docs/architecture.md`، و`.importlinter` |
| خط الأساس | `master` = `de439b4`، والفرع `docs/electrical-drawings-inc-2-lld`. **كل إحالة `path:line` في هذه الوثيقة إلى ذلك الالتزام**، وفُتح كل ملف مُحال إليه |
| النطاق | صفّ INC‑2 في HLD §16 كاملاً، وما يستشهد به من SRS 0.21، و`PLT‑11`، و`PLT‑17`، وقياس القبول `F1‑MS‑1`. وINC‑3 وما بعدها خارج النطاق |
| الحالة | مُقَرّ للبناء. حسم المالك القرارات الستة في 2026-11-25: (أ) في `INC2‑Q‑1` و`Q‑2` و`Q‑3` و`Q‑5` و`Q‑6`، و(ب) في `INC2‑Q‑4`، كلها بالتوصية. ولا قرار معلّقاً. وتبقى بوابتا البناء `G1` و`G2`، وفيهما ما قد يعود إلى المالك (§6.2) |

## 0. كيفية قراءة الوثيقة

**المعرّفات.** على اصطلاح `lld-inc-1.md` §0، ببادئة `INC2`:

| الصيغة | المعنى | مثال |
|---|---|---|
| `INC2‑AC‑n` | معيار قبول لهذه الزيادة، قابل للاختبار (§5.1) | `INC2‑AC‑7` |
| `INC2‑Q‑n` | قرار ⚠️ احتاج المالك، بخيارات وتوصية وحسم (§7.1) | `INC2‑Q‑1` |
| `INC2‑D‑n` | قرار تصميم داخل صلاحية المعماري (§7.2) | `INC2‑D‑4` |
| `INC2‑R‑n` | خطر (§7.3) | `INC2‑R‑2` |
| `INC2‑F‑n` | تعارض أو انحراف وُجد في وثيقة حاكمة أو في الشيفرة، مُبلَّغ عنه لا مُعدَّل (§7.4) | `INC2‑F‑3` |
| `B` · `E` · `O` · `H` + رقم | صف ملف لمنفّذه في §2: `backend-developer` · `data-engineer` · `devops-engineer` · بشري | `B12` |
| `S` + رقم | خطوة في ترتيب البناء (§6) | `S3` |
| `G` + رقم | بوابة بشرية أو قياسية داخل البناء (§6.2) | `G2` |
| `PR‑A..PR‑I` | طلبات الدمج (§6.1) | `PR‑E` |
| `SRS D-nn` · `FR-…` · `M-nn` · `NFR-…` · `DATA-nn` | معرّفات SRS كما كُتبت فيها (شَرطة ASCII) | `FR-VEC-08` |
| `PLT‑nn` · `DEC‑nn` · `STG‑nn` · `RSK‑nn` · `ADR‑nn` | معرّفات HLD | `STG‑04` |
| `فج‑n` · `خ‑n` · `م‑n` · `INC1‑…` · `F1‑…` · `F2‑AC‑n` | معرّفات دورات INC‑1 وF0..F3 | `فج‑24` |

**الوحدات.** الإحداثيات كلها في هذه الوثيقة «نقطة صفحة» (pt، 1/72 بوصة)، في **فضاء الصفحة المعروض**: الأصل أعلى اليسار، بعد CTM والقص ودوران الصفحة. «MiB» و«GiB» قوى العدد 2.

**المصطلحات.**
- «البدائية»: عنصر رسم واحد (قطعة، منحنى، مستطيل، رباعي) خرج من PyMuPDF.
- «السطر»: سطر نص، إما من طبقة النص وإما معزول هندسياً ومتعرَّف عليه.
- «المسح»: التعرّف على منطقة لم تغطّها السطور (DEC‑19).
- «الفقاعة»: دائرة مغلقة صغيرة حول نص.
- «الظهور»: معرّف واحد في موضع واحد من صفحة، بدليله.
- «الدليل»: سجلّ DATA-01.
- «الحزمة الأولية»: مشتق ثنائي لصفحة واحدة في MinIO (§3.4).
- «القسم» قسم الوحدة (`drawings`)، كما في `lld-inc-1.md` §0.

**الخصوصية.** المستودع عام. كل مثال هنا مخترع أو نمط مجرد (`9` رقم و`A` حرف). ولا اسم ملف ولا مشروع ولا وسم من العيّنات.

**ما قيس هنا.** شغّلتُ PyMuPDF 1.28.0 من `.venv` على ملفات اصطناعية في مجلد مؤقت خارج المستودع، ومحلّل `shared` على أمثلة مخترعة. وكل رقم من ذلك مكتوب معه «مسبار». وما عداه تقدير أو منقول بمصدره. ولم أشغّل حاوية ولا قاعدة ولا اختباراً.

## 1. الملخص

INC‑2 أول زيادة تعطي نتيجة على مخططات المالك: معرّفات كل صفحة بنصها وموضعها ودليلها، معروضة على الصفحة وقابلة للبحث المكاني. وتقع في المعمارية هكذا:

- **`ele-parse` (CMP‑07).** ثلاث عمليات جديدة في الابن المحدود نفسه: `extract` (البدائيات والنص والروابط وسطور النص المرسوم)، و`scan` (المسح الاحتياطي)، و`render` (مربعات العرض). ومعها محرك التعرّف على CPU (DEC‑07، DEC‑19).
- **قسم `drawings` (CMP‑01).** STG‑02 وSTG‑03 وSTG‑04 وSTG‑05، ونموذج الدليل، والظهورات، ومقترحات الأسطورة، والعرض والبحث المكاني، وطبقة التسميات الذهبية. النطاق النقي فيه كل خوارزمية لا تحتاج بايتات PDF.
- **`shared` (CMP‑06).** الكميات (DATA-05)، والمعرّفات الحتمية uuid5، ومفاتيح الجوانب (FR-IDX-01)، وقرار N‑2، ونطاقات الأسلاك بترتيب المورد (فج‑24).
- **`worker-electrical` (CMP‑09).** دوران جديدان، `pages` و`ocr`، في العملية نفسها وبالتزامن 1.
- **المنصة.** رابطتان جديدتان في الطوبولوجيا (`PLT‑11`)، وصورة `ele-parse` أكبر (`PLT‑17`)، وهجرتان في سلسلة `electrical`.

**قرار الترتيب الأهم (يحسم ما تركه HLD §7.1 لهذه الوثيقة):** الصفحة **تنتظر مسحها** قبل STG‑04 وSTG‑05، ولا تُعاد المرحلتان بعده (`INC2‑D‑1`). وفشل المسح لا يحبس الصفحة: تُحلَّل بالسطور وحدها وتُوسم `needs_review`.

### 1.1 النطاق ومصدره

| البند | SRS وHLD | ما يُبنى في INC‑2 | ما يبقى لغيرها |
|---|---|---|---|
| البدائيات | FR-VEC-01، 02، 07؛ STG‑02 | كل بدائية بعد CTM والدوران والقص، بخصائصها وطبقتها (OCG)، وعضوية Form XObject والمحتوى المعلَّم حيث يثبت الربط، ومعرّف حتمي | دمج المقاطع الدقيقة قبل الرموز (INC‑4) |
| النص المتجهي | FR-VEC-03، 04، 11 (الكشف) | الحرف والكلمة والسطر، وأي زاوية، والمعكوس، وإزالة المتراكب، وحفظ المعرّف المركّب، وCID وToUnicode والعمودي، وكشف المشوّه وتعليم منطقته | مسح المنطقة المشوّهة (INC‑3) |
| الروابط وإعادة الاستعمال | FR-VEC-05، 06 | الروابط والوجهات والإشارات المرجعية، وتواقيع المجموعات المتطابقة وXObjects | حلّ المراجع بها (INC‑5)، ومثيلات الرموز (INC‑4) |
| استرجاع النص المرسوم | FR-VEC-08، FR-VEC-10، FR-RAS-01 (للنص المرسوم)، DEC‑19، DEC‑07؛ STG‑02 وSTG‑03 | عزل السطور والتعرّف عليها على CPU، والمسح الاحتياطي المقصور، وقياس القبول `F1‑MS‑1` | الصفحات النقطية، والنص الصيني المرسوم، وفرع GPU، و`OcrProvider`، و`ocr_unavailable` (INC‑3) |
| العرض | FR-VEC-09، FR-API-04، NFR-I18N-04 | مربعات بمستويات، وطبقة تظليل، وخط CJK بديل موسوم في بيانات الصفحة | — |
| البحث المكاني | FR-IDX-03 | «ما عند هذا الموقع» و«أين هذا الظهور» لكل صفحة | البحث عبر المشروع (INC‑8) |
| التخطيط (STG‑04) | FR-LAY-01..04، 06 | نوع الصفحة، والإطار، وكتلة العنوان، والشبكة، والمناطق، والجداول، ورقم الصفحة الظاهري | القالب المستنتج من التكرار (FR-LAY-05، S) |
| الأسطورة | FR-ING-14، SRS §13.5 في HLD | قراءتها، واقتراح مدخلاتها، وتأكيدها بإنسان في ملف إعداد الوثيقة | جدول رموز الأسطورة (FR-SYM-12) |
| المعرّفات (STG‑05) | FR-ID-02، FR-ID-15..20 في سياقها، FR-ID-10، DATA-06 | الظهورات بأشكالها، والمعرّف الكامل، وسياق ANSI، والتسمية المكدّسة، والتصحيح الموضعي معطّلاً، وفجوات F3 (§1.3) | الحلّ عبر الصفحات (INC‑5)، والجهاز المجمّع (INC‑6) |
| الدليل والحقائق | DATA-01..06، P-03؛ §8.6 | سجلّ الدليل بطريقتي OCR، وحالة كل حقيقة، وبصمة الإصدار، والكميات بوحدتها الأصلية | الأدلة المستوردة (R2)، وتصحيح الإنسان (INC‑5) |
| التسميات الذهبية | FR-EVAL-02 | التخزين والواجهة واللقطة المصدَّرة | الحقيقة شبه الآلية (R2) |
| التقييم | M-01..M-05، M-29 (المتجهي)، M-30 مفصّلاً، M-36، F2‑AC‑38..40 | الأدوات والقياس الأول، ومعه M-32 بالحقلين الجديدين | الحكم على أهداف R1 (INC‑11) |
| الأداء | NFR-PERF-01 (جزء)، NFR-PERF-05 | حدود المهلة والذاكرة والتوازي، وقياسها على المضيف | الإنتاجية الكاملة ومضاعف التوسع (INC‑11) |
| المنصة | `PLT‑11`، `PLT‑14`، `PLT‑15`، `PLT‑17`، `PLT‑18`، `PLT‑21`، `PLT‑23` | كما في §3 و§4 و§8 | `PLT‑22` (INC‑5)، وصورة الإصدار وفحص الصور المبنية («جاهزية الخادم»، ✅ `INC1‑Q‑3`) |

### 1.2 ما لا يدخل INC‑2

- INC‑3: أي قراءة لصفحة نقطية أو لمنطقة مشوّهة، و`OcrProvider`، وGPU، و`ocr_unavailable`. وصفحة `raster` تنتهي في INC‑2 `needs_review` بسبب `raster_page` (`INC2‑D‑14`).
- INC‑4 فصاعداً: الرموز، والاتصال، والمراجع المتقاطعة، والتقارير، والتحقق المتبادل، والنموذج الموحّد.
- `cg.electrical.model` و`cg.electrical.index`، ونشر `electrical.document.extracted.v1`: لا مستهلك لها قبل INC‑6 (HLD §7.8، RSK‑14).
- `pg_trgm` وQdrant. و`src/app/agents/`. وعقود `.importlinter` تبقى **9** بلا `ignore_imports` جديد.
- صورة إصدار `ele-parse` في `release.yml` و`bundle.sh`: `deploy/release/bundle.sh:48` يقبل ثلاث صور، ويبقى التغيير في خطوة «جاهزية الخادم».
- قرارات محسومة لا تُفتح: DEC‑07، DEC‑19، DEC‑20، SRS D-35..D-37، قرارات F1 الستة، Q1..Q9 في F2، F3‑Q1..Q6، N‑2، وC‑3/C‑6/C‑13 (شروط ما قبل الإنتاج في `INC1‑R‑17`).

### 1.3 مصير كل بند مؤجَّل إلى INC‑2

**(أ) فجوات F3 الثماني عشرة المؤجَّلة كلها** (`../inc-1/f3/design.md` §2.1). كل قاعدة في §3.8 و§3.9.

| الفجوة | الحكم | ما يُبنى، أو سبب التأجيل | الـPR |
|---|---|---|---|
| فج‑11 اسم جهاز يُقرأ جهداً | يُبنى | الاسم في جدول أسماء المورد **وفي موضع الاسم** معاً يتقدّم على الجهد، ويُسجَّل الجهد بديلاً. والموضع وحده لا يكفي: يُسجَّل بديلاً فقط | `PR‑H` |
| فج‑12 اسم جهاز يُقرأ رقم سلك | يُبنى | مع فج‑28، بالقاعدة نفسها | `PR‑H` |
| فج‑13 الحرف `M` في صندوق | يُبنى | حرف أو حرفان داخل مستطيل صغير: دور النص `box_code`، ولا ظهور معرّف. ورمزه في INC‑4 | `PR‑H` |
| فج‑20 رمز الطراز يُقرأ KKS | يُبنى | عمود بترويسة «طراز/نوع»، أو ما بعد اسم مصنّع من قائمة ملف الإعداد: دور النص `model`، ولا ظهور | `PR‑H` |
| فج‑26 أطراف وشرائح المورد الأول | يُبنى | أقنعة أطراف اختيارية بالاسم في ملف القواعد (`[terminals_opt_in]`)، لا تُقرأ إلا داخل منطقة جدول شريحة. وترويسة `A999:` اسم شريحة. ويُقاس على النص المسترجَع قبل التسمية في ملف المورد | `PR‑A`، `PR‑H` |
| فج‑28 أسماء الأجهزة | يُبنى | جدول أسماء المورد في حقل مدخلات الأسطورة (`kind = device_name`)، مقترحاً من الصفحة أو مكتوباً، ومؤكَّداً بإنسان | `PR‑G`، `PR‑H` |
| فج‑31 أطراف الجهاز | يُبنى | داخل صندوق جهاز واحد المعرّف: نقطة اتصال بمعرّف كامل `<جهاز>:<طرف>`. وبلا صندوق واحد: لا يُقرأ | `PR‑H` |
| فج‑36 نافذة الكلمتين | يُبنى | كلمتان متجاورتان في سطر واحد تُجرَّبان معاً لأنواع محددة (§3.9.2) | `PR‑H` |
| فج‑37 نصّان فوق بعضهما | يُبنى | سطران متقاطعان بخطين أو بلغتين يبقيان سطرين، وتُعلَّم المنطقة `overlap`. ولا دمج | `PR‑B` |
| فج‑42 الرقم المجرد | يُبنى | رقم ANSI بدليل سياق: فقاعة، أو تسمية مكدّسة، أو عمود رقم في جدول أسطورة | `PR‑H` |
| فج‑43 ANSI المكدّس | يُبنى | التسمية المكدّسة (FR-ID-17)، ومعها M-36 | `PR‑H`، `PR‑I` |
| فج‑44 رقم صفحة المخطط | يُبنى | رقم الصفحة من كتلة العنوان، وخريطة «ظاهري ↔ فعلي» للوثيقة (FR-LAY-06) | `PR‑G` |
| فج‑45 عقدة بأكثر من معرّف | يُبنى (الرابط) | رابط `co_located` بين ظهورين على نقطة واحدة. ودمجهما في عقدة في INC‑6 | `PR‑H` |
| فج‑46 هوية الطرف بالجهاز | يُبنى | المعرّف الكامل يرث الجهاز المحيط (FR-ID-02) | `PR‑H` |
| فج‑47 جدول التلامسات | يُبنى (القراءة) | جدول صغير تحت ملف: صفوف (زوج أطراف، موقع) برابط `contact_of`. والحلّ والمرايا في INC‑5 | `PR‑G`، `PR‑H` |
| فج‑48 خط اليد | يُبنى جزئياً | الحواشي (annotations) وطبقات OCG المسمّاة في ملف الإعداد: طبقة `annotation`، لا ظهور منها. وخط اليد المرسوم ضربات بلا طبقة: لا كاشف له، لأنه لم يُقس، فيبقى سطراً ضعيف الثقة يذهب إلى `needs_review`. وكاشفه في INC‑5 مع الجودة | `PR‑B`، `PR‑H` |
| فج‑49 حالات فحوص الاتساق | يُبنى (التسمية) | نوع تسمية ذهبية `known_discrepancy`. والفحص نفسه في INC‑5 (FR-XV) | `PR‑I` |
| فج‑50 جدول مختصر بنقاط | يُبنى | الجدول يُعلَّم `truncated`، ولا يُستنتج ما بين النقاط | `PR‑G` |

**(ب) نصف الفجوات التي بُنيت جزئياً، وفج‑24:**

| البند | الحكم | ما يُبنى، أو سبب التأجيل | الـPR |
|---|---|---|---|
| **فج‑24** نطاق أسلاك بحرفين | يُبنى | حقل `wire_sequence` في ملف الإعداد (الحروف، وأول رقم وآخره، وأيهما أسرع). بلا الحقل يبقى النطاق نصاً معلَّماً. والنطاق المكتوب بكلمة (`9A to 9A`) يجمعه STG‑05 من السطر | `PR‑A`، `PR‑H` |
| فج‑7 وفج‑9 نسبة المحوّل | يُبنى | `999/9` تليها في السطر كلمة تقدير (`[ratings] ratio_followers`): دور `rating`، لا مرجع ولا ANSI | `PR‑A`، `PR‑H` |
| فج‑14 رقم كابل في دائرة | يؤجَّل إلى INC‑4 | الدائرة حول الرقم المدوّر اصطلاح رمز كابل (FR-CON-09). وفي INC‑2 دور النص `enclosed_number`، بلا ظهور | — |
| فج‑19 `9AA` شريحةً | يُبنى | مع فج‑26: داخل منطقة جدول شريحة فقط | `PR‑H` |
| فج‑23 إشارات المواقع | يُبنى (الخريطة) | تُحفظ ظهوراً بهدفه الخام، وخريطة الأرقام من فج‑44. والحلّ في INC‑5 (FR-XREF-02) | `PR‑H` |
| فج‑29 `A-A9` و`A9-A9/99/99` و`AA9-9` | يؤجَّل | معناها لم يتحقق منه أحد (`../inc-1/f3/design.md` §2.1)، وفكّها تخمين. تبقى نصاً، وتدخل الحقيقة الذهبية | — |
| M‑3 نثر بعد قائمة أطراف | يُبنى | في STG‑05: عنصر موروث حروفٌ فقط لا يُقبل إلا إن كان تسمية معروفة في `[connection_points]`، وإلا `group.member_unreadable` | `PR‑H` |
| خ‑9 (F3) قائمة بمسافة بعد الفاصلة | يُبنى | نافذة الكلمتين (فج‑36) | `PR‑H` |
| خ‑10 (F3) نقطة أولى منزوعة | يُبنى | STG‑05 يقرأ كلمات STG‑02 كما هي، لا عيّنة STG‑01 | `PR‑H` |

**(ج) بنود F2 من 2 إلى 4 وما تركته F2** (`../inc-1/f2/design.md` §8):

| البند | الحكم | أين | الـPR |
|---|---|---|---|
| البند 2: الأسطورة (US‑7، F2‑AC‑32..37) | يُبنى | §3.8.7 و§3.3.5 | `PR‑G` |
| البند 3: التسمية المكدّسة (US‑6، F2‑AC‑27..31) | يُبنى | §3.9.4 | `PR‑H`؛ وM-36 في `PR‑I` |
| البند 4: التصحيح الموضعي (US‑5، F2‑AC‑23..26) | يُبنى معطّلاً | §3.9.6. والتفعيل الافتراضي بقياس F2‑AC‑26 | `PR‑H`؛ والقياس `PR‑I` |
| سياق ANSI (§8 البند 5) | يُبنى | §3.9.3 | `PR‑H` |
| القياس (US‑8، F2‑AC‑38..40) | يُبنى | §5.5 | `PR‑I` |
| قارئ الجسد المخزَّن المتسامح (S‑2) | يُبنى **أولاً** | §3.2.6: قارئ مستقل في المحوّل، و`from_body` يبقى صارماً على العميل | `PR‑A` |
| التحقق من الاقتراح عند التأكيد بلا جسد والقبول الآلي (خ‑14) | يُبنى | §3.2.6 | `PR‑A` |
| `_OUTER` الثابت في KKS (خ‑13) | يُبنى بياناً | مفتاح `near_miss_shape` في `[kks]` بقيمة اليوم نفسها. فلا تتغيّر قراءة، وتُضاف صيغة مورد بالملف | `PR‑A` |
| كلفة `tally` | لا إجراء | `tally` يبقى في STG‑01 على العيّنة وحدها. وSTG‑05 ينادي `parse` لا `tally`، فلا يصير على مسار ساخن | — |
| خ‑11 (F2) قاعدة الدليل بعد M-32، و`MIN_IDENTIFIERS` | يؤجَّل | ينتظران تسميات M-32 (`INC1‑R‑15`) | — |
| خ‑12 (F2) ضجيج ANSI غير الصالح | يُقاس | M-30 المفصّل يعدّه | `PR‑I` |
| `hint=IDENTIFIER_GROUP` يعيد `None` (مراجعة F3 #6) | لا إجراء | سلوك موثّق (`shared/parser.py:171`)، وSTG‑05 لا يمرّره | — |

**(د) متابعات بقية الدورات:**

| البند | المصدر | الحكم | الـPR |
|---|---|---|---|
| N‑2 (`-X1.14` طرف مفتاحه `X1:14`) | قرار المالك 2026-11-07 | يُبنى (§3.2.1، ومعه أثره على تسميات المرجع) | `PR‑A` |
| مفاتيح الجوانب لـFR-IDX-01 | `../inc-1/pr-b/acceptance.md:75` | يُبنى (`aspect_keys`، §3.2.2)، ويُخزَّن مع الظهور لـINC‑8 | `PR‑A`، `PR‑H` |
| F0 S‑2 انهيار `get_cdrawings()` عند نفاد الذاكرة | `../inc-1/f0/security-review.md:27` | يُبنى: حدّ لحجم المحتوى قبل النداء، وميزانية عناصر، وخروج 507 آمن. ومنع تفريغ الذاكرة ✅ `INC2‑Q‑6` (أ) | `PR‑B`، ومنع التفريغ `PR‑C` |
| F0 S‑4 سقف كلفة قبل التوجيه على `needs_ocr` | `../inc-1/f0/security-review.md:29` | يُبنى (§3.6.5)، والأرقام ✅ `INC2‑Q‑5` (أ) | `PR‑F` |
| F0 #1 ختم السجل بأقدم `pipeline_version` | `../inc-1/f0/acceptance.md` | لا إجراء | كل حقيقة في INC‑2 تحمل إصدارها، فيزول سببه |
| RSK‑24 سقوف `ele-parse` لا تناسب ONNX | HLD §18.2 | يُبنى (§3.1.6)، والأرقام ✅ `INC2‑Q‑2` (أ)، مؤقتة حتى `G2` | `PR‑C`، `PR‑F` |
| RSK‑21 وF1‑R‑10 (حرف `X` في صفحة، سبب مجهول) | HLD، F1 | شرط في بوابة `G2`: يُفسَّر قبل تثبيت العتبات | `G2` |
| `INC1‑D‑20` عمر النسخة المؤقتة | `lld-inc-1.md:2564` | يُعاد تصميمه (`INC2‑D‑7`) | `PR‑E` |
| `INC1‑R‑15` و`INC1‑R‑16` (العتبات والقبول الآلي) | `lld-inc-1.md` §7.3 | لا إجراء: ينتظران M-32 | — |
| `INC1‑R‑17`، C‑6، C‑13 | `lld-inc-1.md:2597` | لا إجراء: شروط ما قبل الإنتاج. وINC‑2 يحفظ شرطها (تزامن 1، ونسخة واحدة، ووثيقة واحدة في الحجم المؤقت) | — |
| صيغة الجسر `~` | `../status.md` | لا إجراء: لم تظهر في العيّنات الموسومة | — |
| `letter_digits` يقرأ الأسماء أسلاكاً (measure-4 §3.1) | قرار المالك 2026-11-21 | يُبنى الفصل: اسم في جدول الأسماء وفي موضع الاسم يتقدّم على السلك (فج‑12). ويبقى النمط اختيارياً بتحذيره | `PR‑H` |
| أرقام ANSI بلا فئة في جداول الدليل (12 رقماً) | `../inc-1/f3/measure-4.md` §3 | يُبنى: عمود رقم في جدول أسطورة يمرّر `hint=ANSI_DEVICE` | `PR‑H` |

## 2. الملفات

القاعدة كما في INC‑1: **لا يحرّر منفّذان الملف نفسه في خطوة واحدة**، وكل ملف اختبار أو وثيقة يقع كاملاً عند منفّذ واحد. «إ» إنشاء و«ت» تعديل. والعمود الأخير الـPR.

### 2.1 `backend-developer`

**`PR‑A`: النواة والقارئ المتسامح**

| # | المسار | إ/ت | المسؤولية | الـPR |
|---|---|---|---|---|
| B1 | `src/app/modules/electrical/shared/terminals.py` | ت | N‑2، والأقنعة الاختيارية للأطراف (§3.2.1) | A |
| B2 | `src/app/modules/electrical/shared/aspects.py` | إ | `aspect_keys` (§3.2.2) | A |
| B3 | `src/app/modules/electrical/shared/quantities.py` | إ | `Quantity` و`read_quantity` (DATA-05، §3.2.3) | A |
| B4 | `src/app/modules/electrical/shared/deterministic_ids.py` | إ | uuid5 (§3.2.4) | A |
| B5 | `src/app/modules/electrical/shared/groups.py` | ت | `WireSequence` و`wire_range_members` (فج‑24) | A |
| B6 | `src/app/modules/electrical/shared/{kinds,parser,ratings,kks}.py` | ت (4) | تمرير `WireSequence`، و`is_ratio_follower`، و`near_miss_shape` | A |
| B7 | `src/app/modules/electrical/shared/rules/{__init__,systems}.py`، `…/rules/identifiers.toml` | ت (3) | المفاتيح الجديدة، و`version = "1.3.0"` | A |
| B8 | `src/app/modules/electrical/shared/version.py` | ت | `SHARED_VERSION = "1.5.0"` | A |
| B9 | `src/app/modules/electrical/drawings/domain/profile.py` | ت | `from_stored_body` المتسامح (`PR‑A`)؛ ثم الحقول الجديدة: `text_recovery` (`PR‑F`)، `layout` و`legend` (`PR‑G`)، `identifiers` (`PR‑H`) | A، F، G، H |
| B10 | `src/app/modules/electrical/drawings/application/{projects,documents,inspection}.py` | ت (3) | `PR‑A`: التحقق من الاقتراح (خ‑14). `PR‑E`: الانتقال إلى مرحلة الصفحات، و`from_stage` في إعادة المعالجة | A، E |
| BT1 | `tests/unit/test_electrical_inc2_shared.py`، `tests/eval/electrical/cases/identifiers.toml` | إ، ت | N‑2 ومفاتيحه، والجوانب، والكميات، ونطاقات الأسلاك، والشكل الخارجي، والأقنعة | A |
| BT2 | `tests/unit/test_electrical_profile_stored.py` | إ | الجسد المستقبلي يُقرأ، والعميل يُرفض، وخ‑14 في الموضعين | A |

**`PR‑B`: `ele-parse`، الاستخراج المتجهي والتصيير**

| # | المسار | إ/ت | المسؤولية | الـPR |
|---|---|---|---|---|
| B11 | `services/ele_parse/contract.py` | ت | عمليات المخطط 2 وحدودها (§3.1) | B، C |
| B12 | `services/ele_parse/primitives.py` | إ | البدائيات، وفضاء الصفحة، والطبقات، وحدود المحتوى | B |
| B13 | `services/ele_parse/content_ops.py` | إ | ماسح معاملات المحتوى (FR-VEC-02: المحتوى المعلَّم وXObject، §3.1.3) | B |
| B14 | `services/ele_parse/native_text.py` | إ | الأحرف والكلمات والأسطر، والمتراكب، والعمودي، والمعكوس، والمشوّه | B |
| B15 | `services/ele_parse/links.py` | إ | الروابط والوجهات والإشارات المرجعية والحواشي | B |
| B16 | `services/ele_parse/reuse.py` | إ | تواقيع المجموعات المتطابقة (FR-VEC-06) | B |
| B17 | `services/ele_parse/render.py` | إ | التصيير بالمستويات والمربعات (§3.1.5) | B |
| B18 | `services/ele_parse/frames.py` | إ | تأطير الجواب الثنائي وقراءته (§3.1.2) | B |
| B19 | `services/ele_parse/{app,child,sandbox,__main__,__init__}.py` | ت (5) | المسارات الجديدة، وحدود لكل عملية، وبيئة الابن، و`SERVICE_VERSION = "2.0.0"`، وأوامر CLI `extract` و`render` | B، C |
| BT3 | `tests/unit/test_ele_parse_extract.py`، `tests/unit/test_ele_parse_render.py`، `tests/unit/test_ele_parse_frames.py` | إ (3) | على ملفات PDF تولَّد في `tmp_path` | B |
| BT4 | `tests/unit/test_ele_parse_service.py` | ت | الحدود لكل عملية والرفض | B، C |

**`PR‑C`: `ele-parse`، عزل السطور والتعرّف والمسح، وأداة `F1‑MS‑1`**

| # | المسار | إ/ت | المسؤولية | الـPR |
|---|---|---|---|---|
| B20 | `services/ele_parse/lines.py` | إ | عزل السطور والفقاعات والتغطية (§3.1.4)، NumPy | C |
| B21 | `services/ele_parse/recognize.py` | إ | صورة السطر، وجلسة ONNX، وفكّ CTC بثقة كل حرف وبديله | C |
| B22 | `services/ele_parse/scan.py` | إ | مسح المنطقة بمربعات، وإزالة التكرار، ودمج المقطوع | C |
| B23 | `services/ele_parse/models.py` | إ | تحميل النموذج من `/app/models` بالبصمة؛ ولا منزّل أبداً؛ وغيابه `recognizer: none` | C |
| B24 | `tests/eval/electrical/f1_ms1.py` | إ | أداة القياس خارج CI (§5.5) | C |
| B25 | `tests/eval/electrical/recovery.py` | إ | مطابقة النص والمعرّفات والصيغ الخاطئة، مشتركة مع M-30 | C |
| BT5 | `tests/unit/test_ele_parse_lines.py`، `tests/unit/test_ele_parse_recognize.py`، `tests/unit/test_ele_parse_scan.py` | إ (3) | سطور اصطناعية مرسومة ضربات، ومحرك مزيّف للتعرّف؛ وبالمحرك الحقيقي بعلامة `live_ocr` تُتخطى بلا نموذج | C |
| BT6 | `tests/unit/test_electrical_f1_ms1.py` | إ | الأداة على مخرجات مثبّتة وحكم العتبات | C |

**`PR‑D`: المنافذ والنطاق المشترك** (الترحيل والمستودعات في §2.2)

| # | المسار | إ/ت | المسؤولية | الـPR |
|---|---|---|---|---|
| B26 | `src/app/modules/electrical/drawings/domain/{geometry,text,primitives,evidence,occurrences,layout,artifacts}.py` | إ (7) | أنواع القيم فقط (§3.4، §3.5) | D |
| B27 | `src/app/modules/electrical/drawings/domain/{pipeline,pages,job}.py` | ت (3) | `Stage` والأسباب، و`from_stage` و`fan_in_attempt` و`profile_version_id` (§3.6) | D |
| B28 | `src/app/modules/electrical/drawings/ports/{extraction,rendering,artifacts,page_facts}.py` | إ (4) | المنافذ الجديدة (§3.5) | D |
| B29 | `src/app/modules/electrical/drawings/ports/repository.py` | ت | توسيع `PageRepository` (§3.5.3) | D |
| BT7 | `tests/unit/support_electrical.py` | ت | المستودعات والمنافذ المزيّفة الجديدة | D |
| BT8 | `tests/unit/test_electrical_inc2_domain.py` | إ | القيم، وآلة حالة المهمة الجديدة | D |

**`PR‑E`: خط الصفحات والعرض**

| # | المسار | إ/ت | المسؤولية | الـPR |
|---|---|---|---|---|
| B30 | `src/app/modules/electrical/drawings/application/{dispatch,extraction,analysis,fan_in,artifacts}.py` | إ (5) | STG‑02 والتوزيع والتجميع (§3.6) | E |
| B31 | `src/app/modules/electrical/drawings/application/{page_views,evidence_views}.py` | إ (2) | العرض والتظليل والبحث المكاني والدليل (§3.3) | E |
| B32 | `src/app/modules/electrical/drawings/application/{jobs,purge,event_mapping,use_cases,_common}.py` | ت (5) | مراحل المهمة، وكنس المشتقات، والأحداث الثلاثة، والحزمة | E |
| B33 | `src/app/modules/electrical/drawings/domain/{events,views}.py` | ت، إ | الأحداث، وقيم العرض | E |
| B34 | `src/app/modules/electrical/drawings/adapters/{ele_parse_client,source_stager}.py` | ت (2) | العمليات الثلاث، و`ensure` | E |
| B35 | `src/app/modules/electrical/drawings/adapters/{bundle_codec,minio_artifacts}.py` | إ (2) | فكّ الحزمة الأولية، و`ArtifactStore` فوق `StorageProvider` | E |
| B36 | `src/app/framework/settings/settings.py`، `src/app/infrastructure/config/env_settings.py` | ت (2) | المفاتيح في §3.7.3 | E، F |
| B37 | `src/app/framework/events/topology.py` | ت | `cg.electrical.pages` (`PR‑E`)، و`cg.electrical.ocr` (`PR‑F`)، وأنواع `cg.electrical.document` | E، F |
| B38 | `src/app/framework/errors.py` | ت | الرموز في §3.3.6، كلٌّ مع موضع إصداره | E، G، I |
| B39 | `src/app/framework/di/composition_root.py` | ت | ربط `DrawingsUseCases` الموسّع | E، G، I |
| B40 | `src/app/workers/electrical_bootstrap.py` | ت | الدوران `pages` و`ocr` (§3.7.2) | E، F |
| B41 | `src/app/api/v1/routers/electrical.py`، `src/app/api/v1/dto/electrical.py` | ت (2) | المسارات والـDTO (§3.3) | E، G، I |
| B42 | `docs/design/openapi.yaml` | ت | كل المسارات الجديدة وتوسيع المعدّدات | E، F، G، H، I |
| B43 | `docs/design/events/schemas/electrical.{page_batch.requested,document.pages_completed}.v1.json` | إ (2) | `PR‑E`؛ و`page_batch.ocr_requested.v1.json` في `PR‑F` | E، F |
| B44 | `docs/design/events/schemas/electrical.{job.progressed,job.requested}.v1.json` | ت (2) | قيم `stage` الجديدة و`from_stage` الاختياري | E |
| BT9 | `tests/unit/test_electrical_{dispatch,extraction,fan_in,page_views,artifacts}.py` | إ (5) | — | E |
| BT10 | `tests/unit/test_stream_topology.py`، `tests/unit/test_electrical_{event_mapping,worker,jobs,purge,settings}.py`، `tests/unit/test_api_electrical_router.py`، `tests/unit/test_api_rbac.py`، `tests/unit/test_composition_root.py`، `tests/unit/test_heavy_job_limit.py` | ت (10) | الصفوف الجديدة | E، F، G، I |
| BT11 | `tests/integration/test_electrical_pages_live.py` | إ | STG‑02 حتى اكتمال المهمة بالمستودع الحقيقي وMinIO وRedis، و`ele-parse` في العملية نفسها | E |

**`PR‑F`: تفعيل السطور والمسح ودور `ocr`** (بعد `G2`)

| # | المسار | إ/ت | المسؤولية | الـPR |
|---|---|---|---|---|
| B45 | `src/app/modules/electrical/drawings/domain/{scan_plan,recovery_profile}.py` | إ (2) | خطة المسح، وعتبات الاسترجاع | F |
| B46 | `src/app/modules/electrical/drawings/application/scanning.py` | إ | STG‑03 والميزانية (§3.6.5) | F |
| B47 | `src/app/modules/electrical/drawings/adapters/data/page_analysis.toml`، `…/adapters/rule_files.py` | إ، ت | القيم المعبّأة لعتبات الاسترجاع والتخطيط والسياق بإصدار (§3.2.5) | F، G، H |
| BT12 | `tests/unit/test_electrical_{scan_plan,scanning,recovery_profile}.py` | إ (3) | — | F |

**`PR‑G`: STG‑04 والأسطورة**

| # | المسار | إ/ت | المسؤولية | الـPR |
|---|---|---|---|---|
| B48 | `src/app/modules/electrical/drawings/domain/{frame,title_block,grid,tables,regions,page_type,sheet_labels,legend}.py` | إ (8) | §3.8 | G |
| B49 | `src/app/modules/electrical/drawings/application/{layout,legend}.py` | إ (2) | STG‑04، ودمج الأسطورة، وتأكيدها | G |
| B50 | `src/app/modules/electrical/drawings/adapters/data/page_types.toml` | إ | قوالب أنواع الصفحات وترويسات الجداول بالإنجليزية والصينية، بإصدار | G |
| BT13 | `tests/unit/test_electrical_{frame,title_block,grid,tables,page_type,sheet_labels,legend}.py` | إ (7) | صفحات اصطناعية | G |

**`PR‑H`: STG‑05**

| # | المسار | إ/ت | المسؤولية | الـPR |
|---|---|---|---|---|
| B51 | `src/app/modules/electrical/drawings/domain/{tokens_in_line,context,bubbles,stacked,structure,correction,text_roles,identify}.py` | إ (8) | §3.9 | H |
| B52 | `src/app/modules/electrical/drawings/application/identification.py` | إ | STG‑05 وحفظه | H |
| BT14 | `tests/unit/test_electrical_{context,stacked,structure,correction,text_roles,identify}.py` | إ (6) | حالات F2‑AC‑23..30 وكل فجوة في §1.3 | H |

**`PR‑I`: التسميات الذهبية والتقييم**

| # | المسار | إ/ت | المسؤولية | الـPR |
|---|---|---|---|---|
| B53 | `src/app/modules/electrical/drawings/domain/gold.py`، `…/application/gold.py`، `…/application/replacement.py` | إ، إ، ت | التسميات واللقطة؛ وعدد التسميات في معاينة الاستبدال | I |
| B54 | `tests/eval/electrical/{m01_page_type,m02_title_block,m03_grid,m05_full_identifier,m29_vector_zh,m30_recovery,m36_stacked}.py` | إ (7) | §5.5 | I |
| B55 | `tests/eval/electrical/{m04_identifiers,m32_profile_detection,framework}.py`، `tests/eval/electrical/README.md` | ت (4) | M-04 على ظهورات STG‑05، وM-32 بالحقلين، وتفصيل FR-EVAL-13 | I |
| B56 | `src/app/ops/electrical_perf.py` | إ | قياس NFR-PERF-01 وNFR-PERF-05 من أزمنة الصفحات المسجَّلة | I |
| BT15 | `tests/unit/test_electrical_gold.py`، `tests/unit/test_electrical_eval_inc2.py`، `tests/unit/test_ops_electrical_perf.py` | إ (3) | — | I |

**الوثائق (`backend-developer`، كل ملف في الـPR المذكور في §8):** BD1 `docs/design/02-port-contracts.md`، BD2 `03-api-spec.md`، BD3 `04-event-catalog.md`، BD4 `05-rbac-config-secrets.md`، BD5 `06-domain-models.md`، BD6 `07-nfr-slo.md`، BD7 `09-testing-strategy.md`، BD8 `docs/architecture.md`، BD9 `docs/architecture.html`.

### 2.2 `data-engineer`

| # | المسار | إ/ت | المسؤولية | الـPR |
|---|---|---|---|---|
| E1 | `migrations/versions/electrical/0002_page_facts.py` | إ | ستة جداول وتوسيع قيدين وأعمدة المهمة الثلاثة (§4.1) | D |
| E2 | `migrations/versions/electrical/0003_gold_labels.py` | إ | جدولا التسميات واللقطات (§4.2) | I |
| E3 | `src/app/modules/electrical/drawings/adapters/sql_repository.py` | ت | توسيع المستودعات القائمة والكنس | D، I |
| E4 | `src/app/modules/electrical/drawings/adapters/sql_page_facts.py` | إ | مستودع الحقائق والأدلة والمشتقات والأسطورة (ملف مستقل: الملف القائم 1,506 أسطر) | D |
| E5 | `src/app/ops/provision.py` | ت | `_TENANT_TABLES` (`:212`) و`PURGE_GRANTS` (`:431`) | D، I |
| E6 | `src/app/ops/purge.py` | ت | `_SchemaSpec("electrical", …)` (`:393-401`) بالترتيب الجديد | D، I |
| E7 | `docs/design/01-data-model.md` | ت | §2.12 | D، I |
| ET1 | `tests/integration/test_electrical_drawings_repository_rls.py` | ت | RLS لكل جدول جديد، والقيود، والكنس، وخطة «قرب موقع» | D، I |
| ET2 | `tests/unit/test_ops_provision.py`، `tests/unit/test_ops_purge.py`، `tests/integration/test_purge_ops_live.py` | ت (3) | الجداول الجديدة، وبذرة الكنس | D، I |
| ET3 | `tests/architecture/test_electrical_tables.py` | ت | يقرأ الملفين | D |

### 2.3 `devops-engineer`

| # | المسار | إ/ت | المسؤولية | الـPR |
|---|---|---|---|---|
| O1 | `services/ele_parse/requirements.txt` | ت | RapidOCR وONNX Runtime وتوابعهما، كلها بالبصمات (§3.1.7) | C |
| O2 | `services/ele_parse/Dockerfile` | ت | الاعتماديات (`PR‑C`)؛ ثم مرحلة بناء تجلب النموذج المختار بـ`ADD --checksum` وتنسخه (`PR‑F`)؛ وتحديث التعليق الذي يقول «PyMuPDF and nothing else» | C، F |
| O3 | `deploy/supply-chain/scan.sh`، `license-policy.toml` | ت (2) | بيئة `ele-parse` تكبر وحدها؛ ولا توسيع للسياسة إلا بقرار مالك من `G1` | C |
| O4 | `docs/delivery/electrical-drawings/inc-2/notices.md` | إ | سجلّ مراجعة إشعارات الثنائيات (`G1`)، ومنها OpenCV في صورة `app`، وترخيص خط CJK الاحتياطي المضمَّن في عجلة PyMuPDF وكفاية عرضه (`INC2‑F‑4`) | C |
| O5 | `docker-compose.yml` | ت | سقوف `ele-parse`، والأدوار `document,pages,ocr`، و`CONSUMER_BLOCK_MS` للعامل (§3.7) | C، E، F |
| O6 | `deploy/sizes/{small,medium,large}.env`، `.env.example` | ت (4) | السقوف والمفاتيح | C، E، F |
| O7 | `docs/design/08-local-runbook.md` | ت | الدفتر، و§4.29 (شرط العزل) بالأدوار الثلاثة | C، E |
| O8 | `deploy/prometheus/alerts.yml`، `alerts.test.yml`، `docs/runbooks/alerts.md` | ت (3) | وصف `AizzakElectricalQueueWaitHigh` للمجموعات الثلاث (§3.7.5) | E، F |
| O9 | `docs/stack-commands.md`، `docs/deploy-server.md` | ت (2) | أوامر `ele-parse extract`، وإعادة المعالجة بعد الترقية | E |
| OT1 | `tests/unit/test_compose_electrical_profile.py`، `tests/unit/test_compose_sizes.py`، `tests/unit/test_resource_budget.py` | ت (3) | السقوف الجديدة، والأدوار، وبقاء التزامن 1 | C، E، F |
| OT2 | `tests/unit/test_ele_parse_containers.py` | ت | العُدّة الاختيارية: النموذج للقراءة فقط، وخيوط ONNX تحت سقف العمليات | C، F |
| OT3 | `tests/unit/test_supply_chain_check.py` | ت | القائمة المثبّتة | C |

### 2.4 بشري

| # | البند | متى |
|---|---|---|
| H1 | ✅ قرارات §7.1 | حُسمت في 2026-11-25 |
| H2 | `G1`: مراجعة إشعارات ثنائيات ONNX Runtime وOpenCV (ومنها OpenCV في صورة `app`) وقبولها | قبل دمج `PR‑C` |
| H3 | وسم 14 صفحة أخرى على الأقل ونص 200 سطر بمواضعها، ومراجعتها (✅ `INC2‑Q‑1`) | قبل `G2` |
| H4 | `G2`: قبول تقرير `F1‑MS‑1`، والقرار إن تجاوز إسقاط المشروع 25 دقيقة (§6.2) | قبل `PR‑F` |
| H5 | على المكدّس الحيّ، بهذا الترتيب: (أ) `electrical/0002` عبر `migrate` بعد `PR‑D` وقبل تشغيل أي صورة من `PR‑E`؛ (ب) إعادة بناء `app` و`ele-parse` وخدماتها من `master` فيه `PR‑F` و`PR‑H` معاً؛ (ج) `electrical/0003` قبل تشغيل صورة `PR‑I` | (أ) بعد `PR‑D`؛ (ب) قبل `H6`؛ (ج) بعد `PR‑I` |
| H6 | إعادة معالجة وثائق INC‑1 كاملة | بعد `PR‑F` **و**`PR‑H` وبعد `H5` (أ) و(ب). مرة واحدة، فلا تُعاد المعالجة مرتين. وهو شرط `G3` |

### 2.5 العدد

- **`backend-developer`:** 109 ملفات مصدر وعقد وبيانات (جديدة ومعدَّلة)، و61 ملف اختبار وأداة تقييم، و9 وثائق.
- **`data-engineer`:** 12 ملفاً (هجرتان ومستودع جديد، والباقي معدَّل).
- **`devops-engineer`:** 21 ملفاً (واحد جديد، سجلّ `G1`).
- **بشري:** ستة بنود (H1..H6)، ولا ملف شيفرة.
- **لا `frontend-developer`:** لا واجهة في المستودع. ولا مرحلة UX.

## 3. العقود

### 3.1 عقد `ele-parse` (CMP‑07)

العملان القائمان `summary` و`pages` لا يتغيّران (`services/ele_parse/app.py:116-139`)، ومخطّطهما يبقى 1 (`contract.py:16`). والعمليات الثلاث الجديدة بمخطّط 2.

#### 3.1.1 المسارات

| المسار | الطلب (JSON ≤ 16 KiB لهذه العمليات) | الجواب | الأخطاء |
|---|---|---|---|
| `POST /v1/documents/extract` | `{file, pages: [int] (1..8، تصاعدية بلا تكرار), recognize: bool, options: ExtractOptions}` | `200 application/octet-stream` مؤطَّر (§3.1.2)، جزآن لكل صفحة: `page_json` و`primitives` | كالقائم، ومعه `503 recognizer_unavailable` (طُلب `recognize` ولا نموذج). والصفحة فوق حدود §3.1.6 تعود موسومة لا رمزاً |
| `POST /v1/documents/scan` | `{file, page, regions: [Region] (1..8), options: ScanOptions}` | `200 application/json` ≤ 2 MiB: `{schema: 2, page, regions: [{id, lines: [Line], truncated}]}` | كذلك |
| `POST /v1/documents/render` | `{file, page, levels: [0, 1] \| [2]}` | `200` مؤطَّر: جزء لكل مربع PNG، وترويسة بأبعاد كل مستوى | كذلك |
| `GET /health` | — | ما اليوم، ومعه `{"schemas": [1, 2], "recognizer": {"engine", "engine_version", "model", "model_sha256", "charset_sha256", "threads"} \| null}` | — |

- **رموز الجواب كما في INC‑1** (`sandbox.py:124-173`): 400/404 طلب أو ملف، و422 رفض أو `bad_range`، و504 مهلة، و507 ذاكرة، و500 `parse_error`، و503 `busy` أو `recognizer_unavailable`. ونصوص الرفض ثابتة، ولا يعود نص من الملف.
- **`too_complex` حكم على الصفحة لا على الملف، وليس رمز HTTP.** تُعاد الصفحات الأخرى، وتأتي الصفحة المتجاوزة جزءاً `page_json` فيه `{"page_no": n, "failed": "too_complex"}` وحده. فالرفض لا يُسقط جاراتها.
- **`recognizer_unavailable` عابر للصفحة لا للرسالة:** يسجّل العامل الصفحة `needs_review` بسبب `recognizer_unavailable` بنصها المتجهي وحده، ولا يعيد الرسالة (`INC2‑D‑12`).
- **`MAX_BODY_BYTES`** يبقى 4,096 للعملين القائمين، ويصير 16,384 للثلاث الجديدة. والحدّ يُفحص قبل القراءة كما في `app.py:165-175`.

#### 3.1.2 التأطير والحدود

```python
# services/ele_parse/frames.py  -- stdlib only; the worker's reader is adapters/bundle_codec.py
# wire: b"EPF1" | u32be header_len | header JSON (utf-8) | part bytes, in header order
#   header = {"schema": 2, "op": "extract"|"render", "service_version": str,
#             "parts": [{"page": int, "kind": "page_json"|"primitives"|"tile",
#                        "name": str, "bytes": int, "sha256": hex64}]}
MAX_HEADER_BYTES: Final = 256 * 1024
MAX_PARTS: Final = 320                           # render: up to 256 tiles of one level
MAX_PAGE_JSON_BYTES_V2: Final = 4 * 1024 * 1024  # text, lines, links of ONE page
MAX_PRIMITIVES_BYTES: Final = 12 * 1024 * 1024   # one page, compressed
MAX_TILE_BYTES: Final = 4 * 1024 * 1024
MAX_EXTRACT_ANSWER: Final = 64 * 1024 * 1024     # the child's whole answer
MAX_RENDER_ANSWER: Final = 64 * 1024 * 1024
MAX_SCAN_ANSWER: Final = 2 * 1024 * 1024
```

- الأب يقرأ أنبوب الابن بسقف العملية (`_BoundedReader` القائم، `sandbox.py:176-206`)، ويقتل مجموعة العملية عند أول بايت زائد.
- العميل يقرأ الجواب بسقف أعلى بقليل (72 MiB)، ويتحقق من الطول والبصمة لكل جزء قبل أي فكّ ضغط.
- فكّ الضغط محدود: كل عمود يُفكّ بـ`zlib.decompressobj(...).decompress(data, max_length)` بطول معلن مسبقاً ≤ 96 MiB للصفحة، فلا قنبلة ضغط من جهة الابن المخترَق.

#### 3.1.3 حزمة البدائيات وJSON الصفحة

**الحزمة الأولية (`EPP1`):** أعمدة ثابتة العرض، little-endian، كل عمود مضغوط بـzlib (مستوى 6). وهي صيغة المشتق في MinIO نفسها (§3.4)، فلا تحويل في العامل.

```text
EPP1 | u32 version=1 | u32 n | u32 n_styles | u32 n_layers | u32 n_groups | column directory
columns over n primitives, in painting order (seqno, item index):
  kind    u8   1 line, 2 curve (4 control points), 3 rect, 4 quad, 5 closed-path marker
  seq     u32  MuPDF drawing sequence number           item u16  index inside that path
  x0 y0 x1 y1           i32  hundredths of a point, displayed page space
  cx0 cy0 cx1 cy1       i32  curve controls / quad's other corners; 0 otherwise
  style   u16  -> styles[]   layer u16 -> layers[] (OCG name, 0 = none)
  xobject u16  -> groups[]   (Form XObject nesting path; 0 = page content or unknown)
  mc      u16  -> groups[]   (marked-content tag path; 0 = none or unknown)
  line    u32  text-line id + 1 the primitive belongs to; 0 = not text
  flags   u16  bit0 clipped, bit1 fill, bit2 stroke, bit3 even-odd, bit4 reused group member
styles[]: width f32, stroke rgb u24, fill rgb u24, dash str, cap u8, join u8, opacity f32
```

- **فضاء الصفحة.** `get_cdrawings()` يعيد الإحداثيات بلا دوران الصفحة (مسبار: صفحة مدوّرة 90° تعيد نقاطها الأصلية، و`rotation_matrix` يحوّلها). فتُطبَّق `page.rotation_matrix` على كل نقطة، ثم تُقص على `cropbox` المعروض: القطعة خارجه تُسقط، والمقطوعة تُقص ويُعلَّم bit0. والدقة جزء من مئة من النقطة (FR-VEC-01: دقة أعلى من دقة الرسم المطبوع، والخطأ ≤ 0.005 pt).
- **الطبقة (OCG):** حقل `layer` من `get_cdrawings(extended=True)`.
- **عضوية Form XObject والمحتوى المعلَّم:** لا يعيدهما PyMuPDF مع الرسم. فيقرؤهما `content_ops.py`: مقطّع معاملات محدود (≤ 2,000,000 معامل، ≤ 64 MiB محتوى مفكوك، عمق XObject ≤ 12) يعدّ معاملات الرسم بترتيبها ومعها مكدّس `BDC/EMC` و`Do`. والربط بـ`seq` يُقبل **فقط** إن ساوى عدد معاملات الرسم عدد المسارات التي أعادها MuPDF؛ وإلا العمودان صفر و`page_json.attribution = "unavailable"`. فلا عضوية مخمَّنة (`INC2‑R‑4`).
- **معرّف البدائية:** `primitive_id(file_id, page, seq, item)` (§3.2.4)، يُحسب عند الحاجة ولا يُخزَّن.

```jsonc
// page_json, schema 2 (shape; every list ordered; floats rounded to 2 decimals)
{ "page_no": 3, "width_pt": 1190.55, "height_pt": 841.89, "rotation": 90,
  "attribution": "exact|unavailable",
  "spans":  [ {"i":0,"text":"-K5","chars":[[c,x0,y0,x1,y1],…],"font":2,"size":9.0,"color":"#000000",
               "angle":0.0,"wmode":0,"mirrored":false,"layer":0,"garbled":false} ],      // <= 20,000 chars
  "lines":  [ Line, … ],                                                                  // <= 1,500
  "bubbles":[ {"i":0,"cx":..,"cy":..,"r":..,"prims":[[a,b],…]} ],                        // <= 2,000
  "uncovered":[ {"i":0,"box":[..],"strokes":123,"why":"no_line|low_confidence"} ],       // <= 64
  "garbled":[ {"box":[..],"chars":41,"why":"unmapped|pua|implausible"} ],                // <= 64
  "overlaps":[ {"a":12,"b":40,"box":[..]} ],
  "fonts":  [ {"name":"…","cid":true,"embedded":false,"substituted":"builtin_cjk|builtin_latin|null"} ],
  "links":  [ {"kind":"goto|named|uri|launch","box":[..],"page":7,"to":[x,y],"dest":"…"} ], // <= 2,000; uri never opened
  "outline":[ {"level":1,"title":"…","page":4} ],        // only on the first page of the request; <= 5,000
  "xobjects":[ {"g":3,"name":"…","box":[..],"uses":14} ], "groups":[ReuseGroup…],
  "annotations":[ {"kind":"ink|freetext|stamp","box":[..],"text":"…"} ],                  // <= 500
  "stats":{"primitives":52987,"lines":311,"covered_strokes":0.94,"recognize_ms":4210,"extract_ms":640} }

// Line: the contract DEC-20 asks for
{ "i": 17, "source": "native|ocr", "method": "vector_text|isolated_line|scan",
  "text": "20XYZ10AB123", "box": [x0,y0,x1,y1], "quad": [8 floats], "angle": 0|90|180|270|float,
  "height": 8.6, "conf": 0.973,
  "chars": [ {"c":"2","conf":0.99,"alt":["Z",0.004],"x0":..,"x1":..}, … ],  // alt null for native text
  "spans": [12,13], "prims": [[100,124],[131,140]],                          // runs of primitive indexes
  "reading": "up|down|null" }
```

- **كل قيمة نصية** تمرّ بـ`contract.clean` (`contract.py:98-103`) قبل الخروج، فلا محرف تحكم ولا اتجاهي. والنص الأصلي قبل التنظيف لا يخرج، لأن DATA-06 تطلب الشكل «كما ظهر» للقارئ، والمحرف الخفي ليس شكلاً: يُسجَّل عدده في `stats.hidden_characters`، ويعلّم STG‑05 الظهور `text.hidden_character` كما يفعل المحلّل اليوم.
- **`links.uri`** نص محدود بـ512 حرفاً، للعرض مهرَّباً فقط، ولا يفتحه شيء.

#### 3.1.4 عزل السطور والتعرّف والمسح (داخل الابن)

الخوارزمية حتمية: ترتيب ثابت في كل خطوة، والتعادل بأصغر فهرس بدائية. والعتبات تأتي في الطلب من ملف الإعداد (§3.2.5)، ويتحقق العقد من مداها قبل الابن.

**(1) المرشّحات.** كل قطعة، وكل ضلع مستطيل أو رباعي، وكل منحنى يُحوَّل إلى 8 أوتار، بشرط أن يكون أكبر امتداد ≤ `max_stroke_pt`. ويُسقط:
  - الخط المتقطع: سلسلة ≥ 4 قطع على استقامة واحدة (انحراف ≤ 0.15 pt)، وأطوالها متساوية ±15٪، وفجواتها متساوية ±15٪، وطولها الكلي > 3 × `line_height_max`.
  - قطعة طرفها ملاصق (≤ 0.2 pt) لقطعة طويلة (> 4 × `line_height_max`) وهي على استقامتها: جزء من سلك.

**(2) الحروف المرشّحة.** اتحاد (union-find) بين المرشّحات التي تتقارب نهاياتها أو تتقاطع ≤ `glyph_join_pt`، بشبكة خلاياها 2 pt. ولكل مجموعة صندوقها. تُحفظ المجموعة إن كان أكبر ضلعها بين `glyph_min_pt` و`glyph_max_pt`، وتُعلَّم «علامة صغيرة» دون `glyph_min_pt`.

**(3) الفقاعات أولاً.** مجموعة مغلقة: ≥ 8 قطع، نسبة الانحراف المعياري لنصف القطر إلى متوسطه ≤ 0.08، والقطر بين 0.6 و6 × `line_height_max`. أو منحنيات أربع تكوّن دائرة. تُسجَّل فقاعة وتخرج من الحروف. هذا يمنع قراءة دائرة الطرف `Q` (`../inc-1/p1-fix-path.md` §3).

**(4) السطور لكل اتجاه.** لكل زاوية في `angles` (الافتراضي 0 و90): يُرسم رسم بياني بين حرفين إن تحقق: فجوة على طول السطر ≤ `gap_ratio` × الارتفاع، وتداخل على العرض ≥ `overlap_ratio`، ونسبة ارتفاعين بين 0.6 و1.6، وارتفاع ضمن [`line_height_min`, `line_height_max`]. والحرف يأخذ جاره الأقرب إلى اليمين (في فضاء السطر) وحده، فالسلاسل خطوط. وتُقسم السلسلة عند فجوة > `column_gap_ratio` × الارتفاع. والكلمات عند فجوة > `word_gap_ratio` × الارتفاع.

**(5) التنازع بين الاتجاهين.** لكل سلسلة درجة = عدد حروفها × متوسط التداخل. ويأخذ كل حرف السلسلة الأعلى درجة، ثم 0° عند التعادل. سلسلة بقي منها حرف واحد تُحلّ. وعمود من أرقام فوق بعضها يخسر أمام السطور الأفقية، فلا أسطر عمودية وهمية (`p1-fix-path.md` §2).

**(6) العلامات الصغيرة** (نقطة، شرطة، شرطة عليا) تلتحق بسطر واحد إن كانت ضمن 0.6 × ارتفاعه، وإلا تبقى رسماً.

**(7) الحرف الوحيد** يُقبل سطراً إن كان داخل فقاعة أو مستطيل صغير، أو ابتعد عن أي رسم غير نصي ≥ 0.5 × ارتفاعه.

**(8) صورة السطر.** تُرسم بدائيات السطر وحدها (لا الجوار) على خلفية بيضاء، بسماكتها المضبوطة ≥ 1.4 بكسل، بارتفاع 40 بكسل وهامش 4، بعد تدوير السطر إلى 0°. فالدائرة والسلك الملاصقان لا يدخلان الصورة.

**(9) التعرّف.** دفعات مرتبة بالعرض، وعرض مبطَّن إلى مضاعفات 32 حتى 1,280 (الأطول يُقسم عند أكبر فجوة كلمة). جلسة ONNX واحدة: `CPUExecutionProvider`، `intra_op_num_threads = ELE_PARSE_OCR_THREADS`، `inter_op_num_threads = 1`، `ORT_SEQUENTIAL`، وتعطيل الذاكرة النمطية `enable_mem_pattern = False`. وفكّ CTC جشع، وثقة الحرف أعلى احتمال على خطواته، والبديل ثاني حرف عند خطوة الذروة (`INC2‑D‑10`).

**(10) زاوية 90 و270.** يُقرأ السطر الرأسي بالاتجاه «صاعد»، فإن كانت ثقته < `min_line_confidence` قُرئ «هابط» أيضاً، وتُؤخذ الأعلى ثقة، وتُسجَّل `reading`.

**(11) التغطية.** المرشّحات غير الملتحقة بسطر تُجمَّع على شبكة 24 pt. خلية فيها ≥ `uncovered_min_strokes` تصير منطقة، وتُدمج المتجاورة. ومعها كل سطر ثقته < `min_line_confidence`.

**التعليم نصاً.** كل بدائية في سطر مقبول تُكتب في عمود `line`. وسطر متوسط ثقته < `min_accept_confidence` لا يعلّم بدائياته (DEC‑19 شرط 3: لا تخرج بدائية من الرسم بلا دليل).

**النص المتجهي.** من `get_text("rawdict")`: لكل حرف رمزه وصندوقه وأصله، ولكل سطر `dir` و`wmode`. والمتراكب: حرف بالرمز نفسه وأصلاهما ≤ 0.3 × الحجم يُسقط ثانيهما (FR-VEC-04). والأسطر تُجمع هندسياً بقواعد الخطوة 4. والمعرّف المركّب وحدة: لا يُقسم عند `=` و`+` و`-` و`:` و`.` و`/` بلا مسافة هندسية. والسطر المعكوس يُكشف من اتجاه تسلسل أصول الأحرف مقابل `dir`، ويُعكس نصه ويُوسم. والزاوية من `dir`، أي زاوية كانت.

**المشوّه (FR-VEC-11، الكشف فقط).** منطقة فيها ≥ 8 أحرف ونسبة (بلا Unicode + PUA) ≥ 0.20 (العتبة القائمة `garbled_ratio`، `domain/pages.py:89`)، أو نص CJK نسبة ثنائياته غير الموجودة في قائمة تكرار مضمَّنة ≥ 0.85 وطوله ≥ 24 حرفاً. القائمة ثنائيات صينية عامة نكتبها من نص عام بترخيص مسموح، في `services/ele_parse/data/zh_bigrams.bin` (≤ 64 KiB). وإن تعذّر مصدر بترخيص مسموح، يُترك هذا الفرع ويُذكر في M-29 (`INC2‑R‑9`).

**المسح (`scan`).** لكل منطقة: مربعات 960 × 960 بكسل عند 200 dpi بتراكب 96، وكشف وتعرّف RapidOCR، وإزالة التكرار: صندوقان IoU ≥ 0.5 ونصهما متساوٍ بعد التطبيع، أو أحدهما محتوى في الآخر، يبقى الأعلى ثقة. وسطران مقطوعان عند حدّ مربع يُدمجان إن تتابعا على خط واحد وفجوتهما ≤ 0.5 × الارتفاع. وكل سطر `method = scan`، وثقته تُضرب في `scan_confidence_factor` (0.8)، فهي أدنى دائماً من ثقة السطر المعزول المساوي.

#### 3.1.5 التصيير

- **المستويات:** 0 = 50 dpi، و1 = 100 dpi، و2 = 200 dpi. ومربع 1,024 بكسل، رمادي 8 بت PNG.
- **رسمة واحدة لا رسمة لكل مربع.** تصيير المستوى كاملاً ثم قطعه بـ`Pixmap.copy`. مسبار على صفحة A3 اصطناعية فيها 95,000 قطعة: 0.2 ث عند 25 dpi، و1.5 ث عند 100، و5.6 ث عند 200، وترميز PNG ≤ 0.15 ث؛ وتصيير مربع بمربع عند 200 كلّف 4.9 ث لـ12 مربعاً. فكلفة المستوى 2 سبب ✅ `INC2‑Q‑4` (ب).
- **المستويان 0 و1** من رسمة واحدة عند 100 dpi، ومنها المستوى 0 بـ`Pixmap.shrink(1)`.
- **سقف البكسل** 64 MP للمستوى (A0 عند 200 dpi نحو 62 MP)؛ وما فوقه يُخفض dpi للصفحة ويُذكر `scale` الفعلي في الترويسة. فذاكرة الرسمة ≤ 64 MB.
- **خط CJK البديل (NFR-I18N-04):** MuPDF يضمّ خطاً احتياطياً لـCJK داخل عجلة PyMuPDF؛ ومسبار على نص صيني بخط غير مضمَّن أظهر حبراً في التصيير. فلا يُضاف Noto إلى الصورة، قراراً يُؤكَّد في `G1` (ترخيص الخط وكفاية عرضه، `INC2‑F‑4`)، ويُوسم كل خط غير مضمَّن `substituted` في بيانات الصفحة.

#### 3.1.6 حدود الابن لكل عملية

```python
# services/ele_parse/sandbox.py -- additions
@dataclass(frozen=True, slots=True)
class OpLimits:
    memory_mb: int           # RLIMIT_AS
    page_wall_s: float       # multiplied by pages, capped by range_timeout_s
    nofile: int = 64         # RLIMIT_NOFILE
    answer_bytes: int = contract.MAX_ANSWER_BYTES
OP_LIMITS: Mapping[str, OpLimits]  # summary, pages (unchanged) | extract | extract_ocr | scan | render
```

| العملية | `RLIMIT_AS` | مهلة الصفحة | سقف المدى | ملاحظة |
|---|---|---|---|---|
| `summary`، `pages` | 1,024 MiB (كما اليوم) | 10 ث | 300 ث | `app.py:38-42` |
| `extract` بلا تعرّف | 1,536 MiB | 15 ث | 300 ث | مسبار: `get_cdrawings` على 98,168 عنصراً زادت 32 MB وأخذت 0.1 ث؛ والباقي لبناء الأعمدة |
| `extract` مع التعرّف | 2,560 MiB | 45 ث | 300 ث | ✅ `INC2‑Q‑2` (أ)؛ تقدير مؤقت يثبّته `G2` |
| `scan` | 2,560 MiB | 90 ث للصفحة | 300 ث | تقدير |
| `render` | 1,024 MiB | 30 ث للمستوى | 60 ث | 64 MP × بايت + قائمة العرض |

- **قبل النداء المكلف (F0 S‑2):** يُحسب طول المحتوى المفكوك مقطَّعاً بحدّ 64 MiB، فإن تجاوزه فـ`too_complex` دون استدعاء `get_cdrawings()`. والعناصر فوق 1,500,000 في الصفحة `too_complex` أيضاً. والانهيار بإشارة يبقى 507 كما اليوم (`sandbox.py:150-155`)، وتفشل الصفحة وحدها.
- **خيوط مثبّتة:** بيئة الابن (`sandbox.py:91-97`) يُضاف إليها `OMP_NUM_THREADS=1` و`OPENBLAS_NUM_THREADS=1` و`MKL_NUM_THREADS=1`، وتنادى `cv2.setNumThreads(1)`. وخيوط ONNX وحدها `ELE_PARSE_OCR_THREADS`.
- **`RLIMIT_FSIZE = 0`** يبقى: لا تحتاج ONNX Runtime ملفاً. والنموذج من `/app/models` للقراءة فقط.
- **`RLIMIT_CORE = 0` و`PR_SET_DUMPABLE = 0`** في الابن قبل فتح أي ملف (✅ `INC2‑Q‑6` (أ)).
- **الفحص قبل الفتح** كما هو (`inspect.py:68-98`): `O_NOFOLLOW`، وملف عادي، و`%PDF-`، و`filetype="pdf"`.

#### 3.1.7 الاعتماديات وسلسلة التوريد (`PLT‑17`، `G1`)

| الحزمة | لماذا | الترخيص المعلن (فُحص في `../inc-1/f1/design.md` §4.3) |
|---|---|---|
| `rapidocr` | كشف المسح وتعرّفه، ونماذج PP-OCR | Apache-2.0 |
| `onnxruntime` (CPU) | جلسة التعرّف | MIT، وإشعارات ثنائيات تُراجع |
| `opencv-python-headless`، `numpy`، `shapely`، `pyclipper`، `pillow`، `pyyaml`، `omegaconf`، `antlr4-python3-runtime`، `requests`، `tqdm`، `six`، `colorlog`، `flatbuffers`، `protobuf`، `packaging` | توابع | كما في الجدول هناك؛ و`opencv` تُراجع مكتباتها المضمَّنة |

- كل سطر في `requirements.txt` بإصدار مثبّت وبصمات عجلات linux/amd64 وaarch64، بـ`--require-hashes` كما اليوم.
- **لا منزّل نماذج.** `models.py` لا يستورد منزّل RapidOCR. والابن بلا شبكة أصلاً (`docker-compose.yml:2231`).
- **النماذج** تُخبز في الصورة من مرحلة بناء بمسار وبصمة SHA-256 معلنة في `Dockerfile`، وترخيص كلٍّ منها في تقرير `F1‑MS‑1` (NFR-LIC-04). وقبل `G2` لا نموذج في الصورة، فـ`recognizer: null`.
- **`G1` (بوابة المالك):** `docs/delivery/electrical-drawings/inc-2/notices.md` يسرد كل مكتبة مضمَّنة في عجلتي `onnxruntime` و`opencv-python-headless` (من `ThirdPartyNotices.txt` و`LICENSE-3RD-PARTY.txt` وأسماء `.so`)، وترخيصها، وموقفها من NFR-LIC-01. وما خرج عنه يعود إلى المالك قراراً في SRS على سابقة D-33. ويشمل OpenCV في صورة `app` (ملاحظة F1 القائمة، `pyproject.toml:145-150`).
- **البديل إن رُفض OpenCV:** التعرّف على السطور وحده بـONNX Runtime وNumPy وPillow (F1 §4.3)، والمسح معطّل حتى بديل. يُرفع قراراً جديداً، لا يُنفَّذ تلقائياً.

### 3.2 النواة `shared` وملف الإعداد

#### 3.2.1 N‑2: `-X1.14` طرف

قراءة اليوم (مسبار على `de439b4`): `-X1.14` ← `reference_designation` **غير صالح** بسبب `aspect.malformed`، ومفاتيحه `-X1.14` و`X1.14` و`X1`. و`X1.14` و`-X1:14` طرفان صالحان مفتاحهما `X1:14`.

**القاعدة الجديدة** (في `terminals._split_chain`، `shared/terminals.py:76-89`): حين يبدأ النص بجانب، ولا يفصل `:` نقطة، ويكون آخر جانب `-` + رمز شريحة (بادئة من `terminals.prefixes` ثم 1..4 أرقام) يليه `.` ثم تسمية تبدأ **برقم** ولا شيء بعدها، فهو طرف: الجوانب كما هي، والشريحة الرمز، والنقطة التسمية، والمفتاح الموحّد `STRIP:POINT`.

| النص | اليوم | بعدها |
|---|---|---|
| `-X1.14` | مرجع غير صالح | طرف صالح، مفاتيحه `-X1.14` و`X1.14` و`X1:14` |
| `=A1+P2-X1.14` | مرجع غير صالح | طرف صالح، ومعه `X1:14` |
| `-XT1.5` | مرجع غير صالح | طرف صالح، ومعه `XT1:5` |
| `-K5.A1`، `-K5.1`، `+P2.1` | مرجع غير صالح | **كما هي** (ليست شريحة) |
| `-X1.K5`، `-X1.14.2` | مرجع غير صالح | **كما هي** (التسمية لا تبدأ برقم، أو بعدها شيء) |
| `-X1-14` | مرجع صالح (عنصر فرعي 14) | **كما هي**: الشرطة جانب IEC، والمالك قرر النقطة وحدها |

**الأثر على تسميات المرجع التي تشبهه** (ما طلبه قرار المالك): لا تتغيّر قراءة صالحة واحدة، لأن كل نص ينتقل كان `valid=False`. وتتغيّر هويته `(kind, canonical)` من مرجع غير صالح إلى طرف صالح. ويدخل `tally` دليلاً لـIEC بعد أن كان خارجه، فيزيد دليل IEC على صفحات تكتب هذه الصيغة. ولا يُفقد مفتاح `X1` وحده من البحث لتسمية الجهاز: الطرفان الآخران لا يحملانه اليوم أيضاً. ولا حقائق مخزَّنة من INC‑1 تُصحَّح، فهي لا تخزّن ظهورات.

#### 3.2.2 مفاتيح الجوانب (FR-IDX-01)

```python
# shared/aspects.py
def aspect_keys(aspects: Sequence[Aspect]) -> tuple[str, ...]:
    """Every prefix-run of the chain, canonical and deduplicated, in order:
    =A1+P2-K5 -> ("=A1", "+P2", "-K5", "=A1+P2", "=A1+P2-K5"). At most 16 keys."""
```

#### 3.2.3 الكميات (DATA-05)

```python
# shared/quantities.py
@dataclass(frozen=True, slots=True)
class Quantity:
    original: str          # "24kV", exactly as printed
    value_si: str          # decimal text, so float noise never enters a stored value
    si_unit: str           # "V", "A", "W", "Hz", "VA", "var", "m", "ohm"
    unit: str              # the unit as printed, canonical case
def read_quantity(text: str, rules: IdentifierRules) -> Quantity | None: ...
    # the units are [quantities] in identifiers.toml: {unit: {si, factor}}; a superset of [ratings].
def is_ratio_follower(text: str, rules: IdentifierRules) -> bool: ...  # patterns 9P99, 9.9, 99VA after 999/9
```

#### 3.2.4 المعرّفات الحتمية (FR-VEC-07، §7.4 في HLD)

```python
# shared/deterministic_ids.py  -- uuid5 over one fixed namespace; inputs joined with "\x1f"
WIRETWIN_NAMESPACE: Final = UUID("6d0f2f0e-4f0b-5f3e-9a6b-7c2b1c0e9a51")  # fixed here, never changed
def primitive_id(file_id: str, page: int, seq: int, item: int) -> str: ...
def text_id(file_id: str, page: int, method: str, index: int, pipeline: str) -> str: ...
def evidence_id(file_id: str, page: int, method: str, refs: str, pipeline: str, engine: str) -> str: ...
def occurrence_id(file_id: str, page: int, text_id: str, kind: str, canonical: str, ordinal: int) -> str: ...
def region_id(file_id: str, page: int, kind: str, index: int) -> str: ...
```

القيمة الثابتة هنا مخترعة، وتُثبَّت في أول بناء بقيمة تولّدها الأداة. والنص نفسه مع إصدار آخر للمحرك يعطي معرّف نص آخر (`pipeline` جزء منه)، فلا يُنسب دليل قديم إلى نص جديد.

#### 3.2.5 القيم المعبّأة والحقول الجديدة في ملف الإعداد

كل عتبة بيانات في `page_analysis.toml` (`version`)، يقرؤها `PackagedRuleSource`، ويتجاوزها ملف الإعداد في المجموعات أدناه. ولا رقم في الشيفرة إلا حدود الأمان.

| المجموعة | الحقل: الافتراضي (المدى) | مصدر الافتراضي |
|---|---|---|
| `text_recovery` | `max_stroke_pt` 12 (2..40) | `characterization.md` §4: ≥ 98.5٪ من المقاطع ≤ 12 pt |
| | `line_height_min` 5، `line_height_max` 16 (2..60) | `p1-fix-path.md` §2: نص المورد الأول 8..10 pt؛ والهامش تخمين |
| | `glyph_join_pt` 0.6 (0.05..3)، `glyph_min_pt` 0.8، `glyph_max_pt` 14 | تخمين يُضبط على مجموعة الضبط في `G2` |
| | `gap_ratio` 0.9، `overlap_ratio` 0.6، `word_gap_ratio` 0.45، `column_gap_ratio` 2.2 (0.05..10) | كذلك |
| | `angles` `[0, 90]` (0، 90، 180، 270) | `p1-fix-path.md` §2 |
| | `uncovered_min_strokes` 12 (1..1000) | تخمين |
| | `min_line_confidence` 0.60، `min_accept_confidence` 0.80 (0..1) | تخمين؛ `G2` |
| | `min_stroke_coverage` 0.60 (0..1)، `outlined_lines_min` 10 (1..1000) | تخمين؛ `G2` |
| | `scan_uncovered` `false`، `scan_title_block` `false` | DEC‑19 شرط 2: لا تفعيل قبل عتبة `F1‑MS‑1` (5)، وكلٌّ يُحكم عليه وحده |
| | `scan_confidence_factor` 0.8 (0.1..1) | تخمين (ثقة أدنى، FR-RAS-05 مفهوماً) |
| `layout` | `frame_margin_pt` 60، `min_frame_ratio` 0.6، `title_block_min_fields` 2 | تخمين |
| | `title_block_template` `null` (نسب صندوق وحقوله) | لا يُستنتج (FR-LAY-05 ‏S) |
| | `annotation_layers` `[]` (≤ 16 اسماً) | فج‑48 |
| `identifiers` | `stacked`: `align_tolerance` 0.25، `gap_max_ratio` 1.2، `bar_overhang_ratio` 0.35 | تخمين، على F2‑AC‑27 |
| | `bubble_gap_ratio` 0.35، `near_symbol_pt` 12، `box_code_max_ratio` 3.0 | تخمين |
| | `context_strong` 0.9، `context_weak` 0.7، `min_fact_confidence` 0.5 (0..1) | تخمين؛ يُعاير في INC‑11 (NFR-ACC-02) |
| | `manufacturers` `[]` (≤ 64 اسماً، كل اسم ≤ 32 حرفاً) | فج‑20 |
| | `wire_sequence` `null` (§3.2.7) | فج‑24 |
| | `terminal_patterns` `[]` (أسماء من `[terminals_opt_in]`) | فج‑26 |
| | `correction.enabled` `false`، `correction.confidence_factor` 0.8، `correction.max_char_confidence` 0.90 | DEC‑20 البند 4، والمعامل تخمين |
| `legend` | `[]` مدخلات (≤ 1,000؛ المفتاح ≤ 16 حرفاً، والنص ≤ 128) | NFR‑F2‑5 |

- **الحدود الصارمة على العميل:** كل مجموعة تُفحص كما يفحص `_THRESHOLD_RANGES` اليوم (`domain/profile.py:95-103`): مفتاح مجهول أو نوع أو مدى خطأ يعطي `InvalidProfile` باسم الحقل.
- **الحذف من الجسد عند الافتراض:** كما في حقول D-36 (`profile.py:112-114`). فالبصمة لا تتغيّر لملف لا يستعمل الحقول.
- **خريطة «الحقل ← أول مرحلة تقرؤه»** (HLD §7.5، وتركها INC‑1 لأن مرحلة واحدة كانت تقرأ كل شيء، `lld-inc-1.md:1609`):

| المجموعة | أول مرحلة |
|---|---|
| `classification` | `classify` |
| `text_recovery` | `extract` |
| `layout`، `languages` | `layout` |
| كل حقول المعرّفات القائمة، و`identifiers`، و`legend` | `identify` |

```python
# drawings/domain/pipeline.py
FIRST_STAGE_OF_FIELD: Final[Mapping[str, Stage]]
def first_stage(before: DrawingProfile, after: DrawingProfile) -> Stage | None:
    """The earliest stage that reads a changed field; None when nothing changed."""
```

#### 3.2.6 القارئ المتسامح والتحقق من الاقتراح (`PR‑A`، أولاً)

- `DrawingProfile.from_stored_body(body, *, known_patterns)` للمخزَّن وحده (`sql_repository.py:463`): يتجاهل المفتاح المجهول، والقيمة المجهولة في مجموعة اختيارية تصير افتراضها، ولا يرمي إلا على `schema` غير مدعوم. و`from_body` يبقى صارماً (`profile.py:157-173`). فالرجوع إلى صورة `PR‑A` من أي PR لاحق يقرأ كل جسد مخزَّن.
- **خ‑14:** التأكيد بلا جسد (`documents.py:370-379`) والقبول الآلي (`inspection.py:817-823`) يمرّان بـ`parse_profile`-المعادل الداخلي `checked_proposal(rules, profile)`. فشل التأكيد ← `422 electrical.invalid_profile` باسم `voltage_labels`. وفي القبول الآلي: لا قبول، وتنتظر الوثيقة إنساناً، ولا يفشل العامل.
- **ترتيب النشر:** `PR‑A` يُنشر قبل أي PR يضيف حقلاً. فالرجوع خطوة واحدة آمن دائماً.

#### 3.2.7 نطاق الأسلاك بترتيب المورد (فج‑24)

```python
# shared/groups.py
@dataclass(frozen=True, slots=True)
class WireSequence:            # from the profile, data only, never a regular expression
    letters: tuple[str, ...]   # 1..26 distinct upper-case letters, in the vendor's order
    first: int; last: int      # 1 <= first <= last <= 999
    fastest: Literal["letter", "number"]   # which one changes first along the sequence
def wire_range_members(left: str, right: str, seq: WireSequence, max_members: int
                       ) -> tuple[tuple[str, ...], bool] | None:
    """38Y .. 43Z under the vendor's numbering; None when either end is not <number><letter>
    of the sequence or right is not after left. Over max_members: ((), True)."""
```

- الترتيب `letter`: الرقم ثابت وتمرّ الحروف ثم يزيد الرقم. و`number`: العكس.
- الفكّ في المحلّل حين يحمل `IdentifierRules` تسلسلاً، قبل قاعدة النطاق ذي الحرف الواحد، وبعدها حين لا يحمل (السلوك اليوم: `38Y-43Z` ← `None`، مسبار).
- العناصر تُقرأ بالقواعد المضيّقة كأي مجموعة، فإن لم تكن أسلاكاً فـ`group.member_unreadable`.
- النطاق بكلمة (`38Y to 43Z`): يجمعه STG‑05 إن كانت الكلمة الوسطى في `[groups] range_words` (`to`، `至`، `~`).

### 3.3 الواجهات البرمجية

كلها تحت `/api/v1`، بحارس `electrical_enabled` قبل حارس الصلاحية كما في INC‑1 (`src/app/api/v1/dependencies.py:387`)، وأخطاؤها RFC 9457. والقوائم `Page[T]` بترقيم keyset، و`limit` المشترك (≤ 100).

#### 3.3.1 الجدول

| الطريقة | المسار (`operationId`) | الطلب | الاستجابة | الأخطاء | الصلاحية |
|---|---|---|---|---|---|
| GET | `/electrical/documents/{id}/pages/{page_no}` (`getDocumentPage`) | — | `200 PageDetailOut` | `404` (وثيقة غير حية، أو صفحة خارج المدى) | `electrical:read` |
| GET | `…/pages/{page_no}/image` (`getPageImage`) | `level` 0..2 إلزامي؛ و`col`/`row` اختياريان معاً | `200 PageImageOut` | `404`، `409 electrical.page_not_ready` (المستوى لم يُصيَّر)، `422` | `electrical:read` |
| GET | `…/pages/{page_no}/overlay` (`getPageOverlay`) | `identifier_id`\*، `evidence_id`\*، `text_id`\* (مجموعها 1..100) | `200 PageOverlayOut` | `404` (معرّف مجهول أو من صفحة أخرى)، `422`، `503 electrical.source_unavailable` (حزمة الصفحة لا تُقرأ) | `electrical:read` |
| GET | `…/pages/{page_no}/near` (`findNearPagePoint`) | `x`، `y` (pt)، `radius` 1..200 (افتراضي 24)، `layers` من (identifiers, regions, text)، `limit` ≤ 100 | `200 PageNearOut` | `404`، `409 electrical.page_not_ready`، `422`، `503 electrical.source_unavailable` | `electrical:read` |
| GET | `/electrical/documents/{id}/identifiers` (`listDocumentIdentifiers`) | `page_no?`، `kind?`، `valid?`، `fact_status?`، `limit`، `cursor` | `200 DrawingIdentifierPage` (بترتيب الصفحة ثم القراءة) | `404`، `422` | `electrical:read` |
| GET | `/electrical/identifiers/{id}` (`getDrawingIdentifier`) | — | `200 DrawingIdentifierOut` ومعه أدلته وروابطه | `404` | `electrical:read` |
| GET | `/electrical/evidence/{id}` (`getDrawingEvidence`) | — | `200 EvidenceOut` | `404` (مجهول، أو دليل وثيقة مطهَّرة) | `electrical:read` |
| GET | `/electrical/documents/{id}/legend` (`getLegendProposal`) | — | `200 LegendProposalOut` (المدخلات والتعارضات وما أُسقط بسببه) | `404` (لا أسطورة: قائمة فارغة، لا 404) | `electrical:read` |
| POST | `/electrical/documents/{id}/legend/confirmation` (`confirmLegend`) | `LegendConfirmIn {entries: [{id, accept, kind?, text?}] (1..1000)}`؛ `Idempotency-Key` | `202 IngestionJobOut` | `404`، `409 electrical.job_conflict` (مهمة مفتوحة)، `409 electrical.profile_unconfirmed`، `422 electrical.invalid_profile` (مفتاح أو نص خارج الحدود بعد التعديل)، `422 electrical.legend_selection` (مدخل متعارض مقبول، أو معرّف مجهول) | `electrical:manage` |
| POST | `/electrical/documents/{id}/reprocessing` (`reprocessDocument`، قائم) | يُضاف `from_stage?` من (`classify`, `extract`, `layout`, `identify`)؛ الغائب `classify` كما اليوم | كما هو | كما هو، و`409 electrical.page_not_ready` حين يُطلب `layout`/`identify` ولا نص مستخرَج | `electrical:manage` |
| GET | `/electrical/eval/gold-labels` (`listGoldLabels`) | `project_id` إلزامي، `document_id?`، `kind?`، `state?`، `limit`، `cursor` | `200 GoldLabelPage` | `422` | `electrical:eval` |
| POST | `/electrical/eval/gold-labels` (`createGoldLabel`) | `GoldLabelIn {document_id, page_no, kind, body (≤ 4 KiB), box?}` | `201 GoldLabelOut` | `404`، `409 common.conflict` (حدّ 20,000 تسمية للوثيقة)، `422` | `electrical:eval` |
| PATCH | `/electrical/eval/gold-labels/{id}` (`updateGoldLabel`) | `GoldLabelPatchIn {version, body?, box?, state?}` | `200 GoldLabelOut` | `404`، `409 common.conflict` (نسخة قديمة)، `422` | `electrical:eval` |
| DELETE | `/electrical/eval/gold-labels/{id}` (`deleteGoldLabel`) | — | `204` | `404` | `electrical:eval` |
| POST | `/electrical/eval/gold-snapshots` (`createGoldSnapshot`) | `GoldSnapshotIn {project_id, name}` | `201 GoldSnapshotOut {id, fingerprint, labels, documents, url, expires_at}` | `404`، `409 common.conflict` (لا تسميات `reviewed`)، `422` | `electrical:eval` |
| GET | `/electrical/eval/gold-snapshots` (`listGoldSnapshots`) | `project_id` إلزامي، `limit`، `cursor` | `200 GoldSnapshotPage` | `422` | `electrical:eval` |

- **المساران اللذان يجيبان 202** (`confirmLegend` ومعه `reprocessDocument` القائم) عليهما `heavy_job` و`Idempotency-Key`، فيصير عدد عمليات 202 في `electrical` خمساً (`tests/unit/test_heavy_job_limit.py:368`).
- **لا معرّف صفحة.** مفتاح الصفحة (الوثيقة، رقمها)، فالمسار متداخل تحت الوثيقة لا `/electrical/pages/{id}` كما في HLD §12.1 (`INC2‑F‑5`).
- **`listDocumentPages`** القائم يعيد المراحل الست بالقيم الموسّعة (`PageStageOut.stage` و`reason`، `docs/design/openapi.yaml:1878-1880`).
- **لا بحث نصي هنا.** المرشّح `kind`/`page_no` سرد، والبحث بالمعرّف عبر المشروع في INC‑8.

#### 3.3.2 العزل والصلاحية

- كل مسار يبدأ بـ`documents.get` تحت RLS، ثم `status = active`، وإلا `404`. والصفحة والدليل والظهور تُحلّ إلى وثيقتها قبل أي قراءة.
- **لا URL من المتصفح يقود إلى مستأجر آخر:** مفتاح الكائن يبنيه الخادم من (مساحة العمل، الوثيقة، الصفحة، الصفّ المخزَّن)، ولا يأتي من الطلب.
- **`getPageImage`** يعيد روابط موقّعة قصيرة: `min(minio.presign_get_ttl_s, 300)` ث، والجواب JSON لا تحويل، برأس `Cache-Control: private, no-store`. والكائن نفسه يُكتب بـ`image/png` (بلا تفسير HTML). والرابط الموقّع يعمل حتى انتهائه ولو أُخرجت الوثيقة، وهو سلوك تنزيل الملفات القائم (`src/app/modules/files/application/use_cases.py:1024`)، فتبقى النافذة ≤ 300 ث (`INC2‑R‑7`).
- **النص المستخرَج غير موثوق:** كل حقل نصي من الصفحة يخرج عبر `escape_untrusted_text` كما في `creator`/`producer` (`src/app/api/v1/routers/electrical.py:270-272`)، إلا `original` في الظهور فيخرج كما طُبع (DATA-06) داخل JSON ويُذكر في وصفه «نص غير موثوق من الرسم، لا يُدرج HTML ولا تعليمات». والواجهة تعرضه نصاً. ولا يدخل نص رسم في قالب رسالة ولا في تعليمات نموذج في هذه الزيادة.

#### 3.3.3 الأشكال (مختصرة)

```python
# src/app/api/v1/dto/electrical.py -- additions (snake_case; no bare arrays)
class BoxOut(BaseModel): x0: float; y0: float; x1: float; y1: float          # page points
class PageStageOut(BaseModel):  # extended enums only
    stage: str      # classify | extract | render | scan | layout | identify
    status: str; reason: str | None; attempts: int
class PageDetailOut(BaseModel):
    page_no: int; kind: str | None; language: str | None
    width_pt: float; height_pt: float; stages: list[PageStageOut]
    page_type: str | None; page_type_confidence: float | None
    sheet_label: str | None; sheet_number: str | None          # as printed
    structure: PageStructureOut | None                          # {function: "=A1", location: "+P2"} or null
    title_block: list[TitleFieldOut]                            # {field, text, confidence, evidence_id, box}
    grid: GridOut | None                                        # columns/rows labels and their edges
    regions: list[RegionOut]                                    # {id, kind, box, confidence, truncated}
    links: list[PageLinkOut]; fonts_substituted: list[str]
    counts: dict[str, int]                                       # identifiers by kind, lines by method
    needs_review: list[str]                                      # reasons of this page's stages
    images: list[ImageLevelOut]                                  # {level, width, height, tile_size, columns, rows, ready}
    pipeline_version: str; engine: str | None; profile_fingerprint: str
class PageImageOut(BaseModel):
    level: int; width: int; height: int; scale: float; tile_size: int
    tiles: list[TileOut]        # {col, row, width, height, url}; all of them, or the one asked for
    expires_at: datetime
class PageOverlayOut(BaseModel):
    page: PageGeometryOut       # width_pt, height_pt, and per level the pt -> pixel scale
    shapes: list[OverlayShapeOut]  # {kind: identifier|evidence|text|separator, id, quad[8], box, label}
class PageNearOut(BaseModel):
    at: PointOut; grid: GridCellOut | None      # page, column, row as printed
    items: list[NearItemOut]    # {layer, id, kind, label, box, distance}; sorted by distance, then id
class DrawingIdentifierOut(BaseModel):
    id: str; document_id: str; page_no: int; kind: str; convention: str | None
    original: str               # untrusted text from the drawing
    canonical: str; full: str | None; corrected: str | None
    aspects: list[AspectOut]; connection_point: str | None; device_class: str | None
    ansi: AnsiOut | None; parts: list[IdentifierPartOut]       # the stacked label's second part, KKS parts
    fact_status: str            # automatic | cross_confirmed | corrected | disputed
    valid: bool; issues: list[str]; confidence: float
    method: str; box: BoxOut; angle: float; evidence_ids: list[str]
    corrections: list[CorrectionOut]                            # {position, before, after, rule, char_confidence}
    links: list[IdentifierLinkOut]                              # {kind: stacked|owner|co_located|contact_of|group_member, id}
    versions: VersionsOut
class EvidenceOut(BaseModel):
    id: str; document_id: str; page_no: int; sheet_label: str | None
    grid: GridCellOut | None; box: BoxOut; angle: float
    method: str                 # vector_geometry | vector_text | ocr_line | ocr_scan | report_table |
                                # internal_link | human | imported | knowledge
    refs: list[str]             # primitive runs "p12:100-124" and text ids
    extractor: VersionsOut; confidence: float; created_at: datetime
class LegendEntryOut(BaseModel):
    id: str; key: str; text: str; kind: str   # ansi | abbreviation | device_name | symbol_name
    suffixes: list[str]; confidence: float; conflict: bool; dropped: str | None
    evidence_id: str; accepted: bool | None
```

#### 3.3.4 «ما عند هذا الموقع» (FR-IDX-03)

- المصدر: ظهورات الصفحة ومناطقها من Postgres (كل صفوف الصفحة، ≤ 5,000)، ونص السطور من حزمة النص في MinIO (≤ 4 MiB) حين تُطلب طبقة `text`. والحساب في التطبيق، بلا امتداد هندسي.
- المسافة: من الدائرة (x, y, radius) إلى الصندوق (0 إن كانت داخله). والترتيب بالمسافة ثم المعرّف، فالتعادل حتمي.
- `at.grid` من شبكة الصفحة (FR-LAY-03): العمود والصف بحروفهما المطبوعة.
- **لا فهرس GiST** (`INC2‑D‑6`، `INC2‑F‑6`): المتوقع أن مشغّل الصناديق `&&` ليس leakproof، فلا يصير شرط فهرس على جدول `FORCE ROW LEVEL SECURITY` (المبدأ نفسه في `docs/design/01-data-model.md` §2.6)، ويؤكده اختبار `EXPLAIN` في ET1. والصفحة الواحدة لا تزيد على آلاف الصفوف، فيكفي فهرس (مساحة العمل، الوثيقة، الصفحة).

#### 3.3.5 تأكيد الأسطورة (FR-ING-14، F2‑AC‑33..35)

1. المدخلات المقبولة تُكتب في `legend` داخل إصدار جديد من ملف إعداد **الوثيقة** بمصدر `confirmed`. وما لم يُذكر يبقى كما كان في الإصدار السابق، وما رُفض يُحذف منه.
2. المدخل المتعارض (مفتاح واحد بقيمتين) لا يُقبل بلا `text` صريح يختاره الإنسان: بلا نص ← `422 electrical.legend_selection`.
3. لواحق ANSI في المدخلات المقبولة تُضاف إلى `ansi_suffixes` (الحد 64 القائم).
4. تُنشأ مهمة `reprocess` لكل الصفحات بـ`from_stage = identify` (خريطة §3.2.5)، وتعيد `202` كما يعيد التأكيد اليوم. ولا تُعاد قراءة PDF ولا التعرّف.
5. `viewer` و`member`: `403` (`electrical:manage` عند `owner` و`admin` وحدهما، `src/app/modules/access/domain/value_objects.py:94-117`).

#### 3.3.6 الرموز الجديدة (`ERROR_CATALOG`)

| الرمز | الحالة | العنوان | موضع الإصدار |
|---|---|---|---|
| `electrical.page_not_ready` | 409 | Page has not been processed yet | `page_views` و`documents` |
| `electrical.source_unavailable` | 503 | Derived page data could not be read | `adapters/minio_artifacts.py` |
| `electrical.legend_selection` | 422 | Legend selection is not valid | `application/legend.py` |

`electrical.source_unavailable` هو الرمز الذي ذكره HLD §12.4 ولم يُبنَ في INC‑1. ورموز فشل الصفحات أسباب (`StageReason`) لا رموز HTTP.

### 3.4 التخزين

| المشتق | المفتاح | المحتوى | الحجم المقدَّر للصفحة |
|---|---|---|---|
| البدائيات | `<ws>/ele/<doc>/<page:05d>/primitives-<v>.epp` | `EPP1` (§3.1.3) | مسبار على 95,000 قطعة عشوائية: 1.5 MB خام و0.98 MB مضغوطة؛ والصفحات الحقيقية أقل (إحداثيات متقاربة) |
| النص والروابط | `<ws>/ele/<doc>/<page:05d>/text-<v>.json.z` | `page_json` كما خرج، مضغوطاً zlib | 20..120 KiB |
| مربعات العرض | `<ws>/ele/<doc>/<page:05d>/tile-<level>-<c>-<r>-<v>.png` | PNG رمادي | مسبار: 0.64 MB عند 100 dpi، و1.60 MB عند 200 dpi لصفحة ضجيج عشوائي |
| لقطة ذهبية | `<ws>/ele-gold/<snapshot_id>.json` | JSON التسميات | ≤ 8 MiB |

- `<v>` = أول 12 حرفاً من بصمة (إصدار الخط، بصمة ملف الإعداد، بصمة المحرك). فالكتابة لا تستبدل كائناً بمحتوى آخر بالاسم نفسه، والرابط المخبّأ لا يعرض غير ما طُلب.
- **الصفّ قبل الكائن:** كل كائن يُسجَّل في `drawings_page_artifacts` بحالة `pending` في المعاملة التي تبدأ المرحلة، ثم يُكتب، ثم تصير `stored` في معاملة النتيجة. فكنس الوثيقة يحذف كل مفتاح مسجَّل، ولا يحتاج سرد بادئة. ولا نقطة في الشيفرة تملك حذف بادئة: `delete_prefix` محصور في `app.ops.purge` بالتصميم (`src/app/infrastructure/storage/minio_storage.py:376-395`).
- كائن `pending` لا يُقرأ، وإعادة المحاولة تكتب المفتاح نفسه.
- **إعادة المعالجة لا تترك يتيماً:** المفتاح الجديد صفّ جديد. وفي معاملة النتيجة يصير الجديد `stored` والقديم `obsolete`، ثم يُحذف كائن القديم وصفّه بعدها. وما انقطع يبقى صفّاً `obsolete` تحذفه الكتابة التالية للصفحة أو كنس الوثيقة.
- **التطهير (PLT‑04):** `SweepPurgedDocument` (القائم) يقرأ المفاتيح، ويحذف الكائنات واحداً واحداً بـ`StorageProvider.delete` **قبل** معاملة حذف الصفوف. وكل خطوة مثالية: الكائن المحذوف «لا شيء» والصف المحذوف صفر. وسقوط بينهما تكمله إعادة التسليم. وكائن تكتبه دفعة جارية بعد الكنس: الدفعة تحذف ما كتبته حين تفشل معاملتها على `lock_live` (`INC2‑R‑6`).
- **الاستبدال بالاسم** يسلك الطريق نفسه (`ReplaceNamesakesService` ← `DeleteFileService` ← المشارك)، بلا منطق جديد (س‑29).
- **تطهير مساحة العمل:** بادئتها `<ws>/` تمحى كما اليوم، فتشمل `ele/` و`ele-gold/`.

### 3.5 النطاق والمنافذ

#### 3.5.1 قيم النطاق (توقيعات، المكتبة القياسية و`shared` وحدهما)

```python
# drawings/domain/geometry.py
@dataclass(frozen=True, slots=True)
class Box: x0: float; y0: float; x1: float; y1: float
    # + width, height, center, area, contains(), intersect(), union(), distance_to_point()
@dataclass(frozen=True, slots=True)
class Quad: points: tuple[tuple[float, float], ...]   # 4
def rotate(box: Box, page: "PageGeometry", degrees: int) -> Box: ...

# drawings/domain/text.py
class TextMethod(StrEnum): VECTOR_TEXT="vector_text"; OCR_LINE="ocr_line"; OCR_SCAN="ocr_scan"
@dataclass(frozen=True, slots=True)
class CharReading: char: str; confidence: float; alternative: str | None; alternative_confidence: float | None
@dataclass(frozen=True, slots=True)
class TextLine:
    index: int; text: str; box: Box; quad: Quad; angle: float; height: float
    method: TextMethod; confidence: float; chars: tuple[CharReading, ...]
    primitive_runs: tuple[tuple[int, int], ...]; layer: str | None; mirrored: bool; vertical: bool
@dataclass(frozen=True, slots=True)
class Bubble: index: int; cx: float; cy: float; r: float; primitive_runs: tuple[tuple[int, int], ...]
@dataclass(frozen=True, slots=True)
class PageText:
    page_no: int; lines: tuple[TextLine, ...]; bubbles: tuple[Bubble, ...]
    uncovered: tuple[Box, ...]; garbled: tuple[Box, ...]; overlaps: tuple[tuple[int, int], ...]
    annotations: tuple[...]; links: tuple[PageLink, ...]; language: PageLanguage

# drawings/domain/primitives.py -- columns as array('i'/'B'/'I'); read by adapters/bundle_codec
@dataclass(frozen=True, slots=True)
class PrimitiveTable:
    n: int; kind: array[int]; seq: array[int]; item: array[int]
    x0: array[int]; y0: array[int]; x1: array[int]; y1: array[int]
    style: array[int]; layer: array[int]; line: array[int]; flags: array[int]
    styles: tuple[Style, ...]; layers: tuple[str, ...]
    def long_segments(self, min_pt: float) -> Iterator[Segment]: ...   # frame, title block, tables, separators
    def closed_boxes(self, max_side_pt: float) -> tuple[RectShape, ...]: ...

# drawings/domain/evidence.py (DATA-01, DATA-02)
class ExtractionMethod(StrEnum):
    VECTOR_GEOMETRY="vector_geometry"; VECTOR_TEXT="vector_text"; REPORT_TABLE="report_table"
    INTERNAL_LINK="internal_link"; OCR_LINE="ocr_line"; OCR_SCAN="ocr_scan"
    VISION_MODEL="vision_model"; HUMAN="human"; IMPORTED="imported"; KNOWLEDGE="knowledge"
class FactStatus(StrEnum): AUTOMATIC="automatic"; CROSS_CONFIRMED="cross_confirmed"; CORRECTED="corrected"; DISPUTED="disputed"
@dataclass(frozen=True, slots=True)
class VersionStamp:
    pipeline: str; shared: str; rules: str; analysis: str   # page_analysis.toml version
    profile_fingerprint: str; engine: str | None; model_sha256: str | None
@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    id: str; document_id: str; page_no: int; sheet_label: str | None
    column: str | None; row: str | None; box: Box; angle: float
    method: ExtractionMethod; refs: tuple[str, ...]; confidence: float; versions: VersionStamp

# drawings/domain/occurrences.py (DATA-06)
@dataclass(frozen=True, slots=True)
class Correction: position: int; before: str; after: str; rule: str; char_confidence: float
@dataclass(frozen=True, slots=True)
class IdentifierOccurrence:
    id: str; page_no: int; kind: IdentifierKind; convention: Convention | None
    original: str; canonical: str; full: str | None; corrected: str | None
    corrections: tuple[Correction, ...]; aspects: tuple[Aspect, ...]
    aspect_keys: tuple[str, ...]; search_keys: tuple[str, ...]
    connection_point: str | None; device_class: str | None
    ansi: AnsiDevice | None; kks: KksTag | None; parts: tuple[IdentifierPart, ...]
    quantity: Quantity | None
    box: Box; angle: float; method: TextMethod; confidence: float
    status: FactStatus; valid: bool; issues: tuple[str, ...]
    evidence_ids: tuple[str, ...]; versions: VersionStamp
class LinkKind(StrEnum):
    STACKED="stacked"; OWNER="owner"; CO_LOCATED="co_located"; CONTACT_OF="contact_of"; GROUP_MEMBER="group_member"
@dataclass(frozen=True, slots=True)
class IdentifierLink: kind: LinkKind; from_id: str; to_id: str; evidence_id: str
```

#### 3.5.2 منافذ `ele-parse`

```python
# drawings/ports/extraction.py  -- PdfExtraction in HLD §6; PdfInspection stays (STG-01 only)
@dataclass(frozen=True, slots=True)
class ExtractOptions:  # everything ele-parse needs, from the profile; no profile object crosses the port
    recognize: bool; lines: Mapping[str, float]; angles: tuple[int, ...]
@dataclass(frozen=True, slots=True)
class ExtractedPage:
    text: PageText; primitives: PrimitiveTable; page_json: bytes; primitives_blob: bytes
    stats: Mapping[str, float]; engine: EngineStamp | None; fonts_substituted: tuple[str, ...]
class PdfExtraction(Protocol):
    async def extract(self, source: StagedSource, pages: Sequence[int], options: ExtractOptions
                      ) -> Sequence[ExtractedPage | PageFailure]: ...
    async def scan(self, source: StagedSource, page: int, regions: Sequence[ScanRegion],
                   options: ScanOptions) -> Sequence[TextLine]: ...
    async def engine(self) -> EngineStamp | None: ...       # from /health, cached per process
# drawings/ports/rendering.py  -- PageRendering in HLD §6
class PageRendering(Protocol):
    async def render(self, source: StagedSource, page: int, levels: Sequence[int]) -> Sequence[RenderedLevel]: ...
# drawings/ports/artifacts.py  -- over StorageProvider
class ArtifactStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str, *, max_bytes: int) -> bytes:
        """AppError(electrical.source_unavailable) past max_bytes, missing or unreadable."""
    async def delete(self, key: str) -> None: ...          # idempotent
    async def presign(self, key: str, ttl_s: int) -> str: ...
```

- الاسم: `PdfExtraction` لا `PdfInspection` الموسّع. فـ`PdfInspection` يبقى لـSTG‑01 بعمله ومخطّطه (`ports/pdf.py:18-21`)، ويحسم فرق الاسم الذي أجّله F1 (§7، الصف 7).
- المحوّل الوهمي في `tests/unit/support_electrical.py` يعيد الصفحات من ملفات مثبّتة، ومعه طقم عقد واحد للمحوّلين (`ele_parse_client` و`FakePdfExtraction`): الحدود، والبصمات، والترتيب، والصفحة الفاشلة.
- الترجمة: `504` ← `PARSE_TIMEOUT`، و`507` ← `MEMORY_LIMIT`، والصفحة الموسومة `too_complex` ← `THRESHOLD_EXCEEDED`، و`500 parse_error` ← `PARSE_ERROR`، و`503 recognizer_unavailable` ← نتيجة «بلا تعرّف» مع السبب، وأي شيء آخر ← `InspectionUnavailable` (عابر). و`400`/`404` ← `common.internal` كما اليوم (`adapters/ele_parse_client.py:139-143`).

#### 3.5.3 توسيع المستودعات

```python
# drawings/ports/repository.py -- PageRepository gains (others unchanged)
async def seed_stages(self, ctx, document_id: Uuid, pages: Sequence[int],
                      stages: Sequence[Stage], pipeline_version: str) -> None: ...
async def record_stage(self, ctx, document_id: Uuid, page_no: int, stage: Stage,
                       status: StageStatus, reason: StageReason | None, attempts: int) -> None: ...
async def unfinished_pages(self, ctx, document_id: Uuid, pages: Sequence[int] | None) -> Sequence[PageTodo]: ...
async def pending_count(self, ctx, document_id: Uuid) -> int: ...     # pages not terminal in a required stage
async def phase_counts(self, ctx, document_id: Uuid) -> Mapping[Stage, Mapping[StageStatus, int]]: ...
async def reset_stages(self, ctx, document_id: Uuid, pages: Sequence[int] | None, stages: Sequence[Stage]) -> int: ...

# drawings/ports/page_facts.py
class PageFactsRepository(Protocol):
    async def replace_extraction(self, ctx, document_id, page_no, layout_seed, evidence, artifacts) -> None: ...
    async def replace_scan(self, ctx, document_id, page_no, lines_digest, evidence) -> None: ...
    async def replace_layout(self, ctx, document_id, layout: PageLayout, evidence, legend) -> None: ...
    async def replace_identifiers(self, ctx, document_id, page_no, occurrences, links, evidence) -> None:
        """Delete this page's rows, then insert, in the caller's unit of work: the SAME inputs
        give the SAME ids, so a reprocess rewrites the same rows (FR-ING-05)."""
    async def layout(self, ctx, document_id, page_no) -> PageLayout | None: ...
    async def identifiers(self, ctx, document_id, *, page_no, kind, valid, status, limit, cursor) -> Page[...]: ...
    async def identifier(self, ctx, identifier_id) -> IdentifierOccurrence | None: ...
    async def page_boxes(self, ctx, document_id, page_no) -> Sequence[BoxedItem]: ...   # near + overlay
    async def evidence(self, ctx, evidence_id) -> EvidenceRecord | None: ...
    async def artifacts(self, ctx, document_id) -> Sequence[ArtifactRow]: ...
    async def mark_artifacts_pending(self, ctx, document_id, page_no, keys) -> None: ...
    async def mark_artifacts_stored(self, ctx, document_id, page_no, keys_sizes) -> None: ...
    async def sheet_map(self, ctx, document_id) -> Sequence[tuple[int, str | None, str | None]]: ...
    async def legend_entries(self, ctx, document_id) -> Sequence[LegendEntry]: ...
```

### 3.6 خط المعالجة

#### 3.6.1 المراحل والحالات

```python
# drawings/domain/pipeline.py
PIPELINE_VERSION: Final = "2.0.0"      # rises again with every PR that changes a stage's output
class Stage(StrEnum):
    CLASSIFY="classify"; EXTRACT="extract"; RENDER="render"; SCAN="scan"
    LAYOUT="layout"; IDENTIFY="identify"          # widen ck_drawings_page_stages_stage (§4.1)
PAGE_STAGES: Final = (Stage.EXTRACT, Stage.RENDER, Stage.LAYOUT, Stage.IDENTIFY)  # always required
# The tuple grows with the PR that ships the stage: PR-E (EXTRACT, RENDER), PR-G adds LAYOUT,
# PR-H adds IDENTIFY. A job never waits for a stage no code runs. Progress reports the last of it.
# Stage.SCAN is required only when a row for it exists (a region asked for it).
```

| السبب الجديد في `StageReason` | متى | حالة المرحلة |
|---|---|---|
| `threshold_exceeded` | الصفحة فوق حدود §3.1.6 | `extract` فاشلة |
| `recognizer_unavailable` | لا نموذج في `ele-parse` | `extract` تحتاج مراجعة |
| `raster_page` | صفحة نقطية، ولا مسار لها في INC‑2 | `extract` تحتاج مراجعة |
| `garbled_text` | مناطق مشوّهة لم تُقرأ | `extract` تحتاج مراجعة |
| `low_coverage` | تغطية الضربات < `min_stroke_coverage` ولا مسح مفعّل | `extract` تحتاج مراجعة |
| `scan_failed` | فشل مسح منطقة | `scan` تحتاج مراجعة |
| `ocr_budget_exceeded` | بلوغ ميزانية §3.6.5 | `scan` أو `extract` تحتاج مراجعة |
| `upstream_failed` | فشلت `extract`، فلا نص | `layout` و`identify` فاشلتان |
| `title_block_missing` | صفحة مخطط بلا كتلة عنوان مقروءة | `layout` تحتاج مراجعة |
| `stacked_label.ambiguous` | F2‑AC‑30 | `identify` تحتاج مراجعة |
| `legend_conflict` | مفتاح واحد بقيمتين | `layout` تحتاج مراجعة |
| `render_failed` | مستوى لم يُصيَّر | `render` فاشلة |
| `duplicate_sheet_number` | رقم ورقة مكرر في الوثيقة | `layout` تحتاج مراجعة |
| `grid_mismatch` | ضلعان متقابلان يتناقضان | `layout` تحتاج مراجعة |
| `structure_conflict` | جانبان مختلفان من صندوقين متداخلين | `identify` تحتاج مراجعة |
| `low_confidence` | ظهور جهاز ثقته < `min_fact_confidence`، أو بسياق ضعيف | `identify` تحتاج مراجعة |

- **سبب واحد في الصفّ** (القيد القائم `ck_drawings_page_stages_reason`): يُكتب الأعلى أولوية بترتيب الجدول، وبقية الأسباب في `layout.needs_review` من صفّ الصفحة.
- **اكتمال الصفحة:** كل مرحلة مطلوبة لها صفّ نهائي (`succeeded` أو `failed` أو `needs_review`).
- **فشل صفحة لا يُفشل الملف** (NFR-REL-01): الفشل يُسجَّل في صفحته، وبقية الدفعة تستمر.
- **فشل `render` أو `scan` لا يمنع الاكتمال**، فالفشل حالة نهائية. ومستوى العرض العالي (2) خارج العدّ أصلاً (`INC2‑Q‑4`).

#### 3.6.2 انتقالات المهمة

| من | الفعل | إلى | من يفعله |
|---|---|---|---|
| `running` (STG‑01) | انتهى الفحص، والملف مؤكَّد أو مقبول | `running` مع `stage = extract` وتوزيع الدفعات | العامل (دور `document`) |
| `running` (STG‑01) | ثقة دون العتبة | `awaiting_confirmation` | كما اليوم |
| `running` (الصفحات) | آخر صفحة اكتملت | حدث `document.pages_completed` | دور `pages` أو `ocr` |
| `running` (الصفحات) | وصول `pages_completed` | `succeeded` | دور `document` |
| `running` (الصفحات) | إلغاء | `cancel_requested_at`، وتتوقف الدفعة عند الصفحة التالية، ثم `cancelled` | الواجهة ثم أي دفعة |
| `running` بعقد منتهٍ | استئناف | `queued` (`attempt + 1`)، ويعيد دور `document` توزيع الصفحات غير المنتهية | الواجهة |
| أي حالة مفتوحة | تطهير | `cancelled` بـ`electrical.document_not_live` | المشارك أو العامل |

- **العقد في مرحلة الصفحات:** كل معاملة صفحة تمدّ `lease_until` بـ`job_lease_s`. فالعقد حيّ ما دام تقدُّم، أياً كانت الدفعة التي أحرزته، وانتهاؤه يعني لا تقدم منذ `job_lease_s`.
- **`attempt` رمز الملكية** كما في INC‑1 (`application/inspection.py:308-311`): حدث دفعة `attempt` فيه أقل من المهمة يُقَرّ ويُهمل.
- **`from_stage`** عمود جديد في المهمة: غائب = من `classify`. ومرحلة أبعد تعيد ضبط مراحلها وما بعدها فقط.
- **لا STG‑01 حين لا يلزم:** مهمة `from_stage` فيها أبعد من `classify`، أو استئناف مهمة بلغت الصفحات، تُوزَّع مباشرة. فلا يُلحق إصدار `detected` جديد لوثيقة مقبولة عند كل استئناف. ولذلك لا يكتب `_take` المرحلة `classify` إلا حين تجري (`inspection.py:338` يكتبها اليوم دائماً).
- **ملف الإعداد مثبَّت للمهمة:** `profile_version_id` يُكتب عند التوزيع، وتقرؤه كل دفعة. فتعديل الملف أثناء مهمة لا يخلط صفحتين بإعدادين (FR-ING-05).
- **التزامن مع الكنس:** كل معاملة صفحة تبدأ بـ`lock_live`، فلا تُكتب حقيقة لوثيقة وُسمت.

#### 3.6.3 التوزيع (STG‑01 ← الصفحات)

في معاملة `_settle` حين ينتهي STG‑01 مقبولاً (`inspection.py:780-842`)، بدل `succeed`:
1. تُبذر صفوف `PAGE_STAGES` للصفحات (وللمعاد معالجتها: `reset_stages` من `from_stage`)، وصفّ `classify` يبقى كما هو.
2. `job.stage = Stage.EXTRACT`، و`extend_lease`.
3. تُقسم الصفحات دفعات من `ELE_PAGE_BATCH` (20)، ولكل دفعة `electrical.page_batch.requested.v1` بالحمولة `{job_id, document_id, pages[], attempt, mode: "analyze", from_stage?}`، كلها في المعاملة نفسها. دفعات وثيقة 5,000 صفحة 250 حدثاً (RSK‑05).
4. إن لم تبق صفحة غير منتهية (استئناف بعد ضياع آخر حدث)، يُلحق `pages_completed` مباشرة بدل الدفعات.
5. الأحداث `subject = job_id` كما في HLD §7.8. ولا يُنشر `document.extracted` (لا مستهلك قبل INC‑6).

حين `from_stage` هي `layout` أو `identify` (تأكيد أسطورة، أو تغيير ملف): لا يُقرأ PDF. الدفعة تقرأ نص الصفحة وبدائياتها من MinIO، وتعيد المرحلة المطلوبة وما بعدها. ودفعة `render_hd` تتحقق من أن الوثيقة حية وحدها، لأن مهمتها انتهت.

#### 3.6.4 دفعة الصفحات (دور `pages`)

```
ExtractPages.run(ctx, event):
  1. a page whose required stages are already terminal for THIS attempt and these versions is skipped
  2. lock_live(document), job owned by event.attempt, not cancelled  -> else ack and stop
  3. stage the file once (stager.ensure, §3.6.6) when the batch needs the PDF
  4. `recognize` per page: kind is vector_outlined or mixed, or the geometry alone isolates
     >= outlined_lines_min candidate lines on a page with native text; and the budget allows it
     for the pages in groups of ELE_EXTRACT_GROUP (5), at most 2 groups in flight, SAME document:
       extract -> persist (§3.6.7)  ; a group that fails is asked again page by page
  5. render level 0+1 for the pages of the group; a render failure is `render` only
  6. LAYOUT and IDENTIFY for every page that has no pending SCAN (§3.6.5)
  7. after the last page: lock the job row FOR UPDATE, count pending pages; 0 -> pages_completed (once)
  8. a transaction that claims (cg.electrical.pages, event_id) and finishes
```

- **مثالية التسليم.** المطالبة `claim` في معاملة آخر خطوة، على سابقة INC‑1 (`inspection.py:748-750`). وكل صفحة معاملة مستقلة، فإعادة التسليم تتخطى المنتهية وتكمل الباقي.
- **خارج المعاملة:** التنزيل ونداءات `ele-parse` وكتابة MinIO كلها خارج المعاملات (R2، كما في INC‑1).
- **معاملة الصفحة الواحدة:** `lock_live` ← `mark_artifacts_pending` ← (كتابة الكائنات خارجها) ← معاملة: `replace_extraction` + `record_stage` + `mark_artifacts_stored` + `extend_lease` + تقدّم.
- **الإلغاء والتطهير** يُفحصان قبل كل مجموعة ويُحترمان عند حدّ صفحة، بالشكل القائم (`inspection.py:637-659`).
- **خطأ عابر** (`InspectionUnavailable`، قاعدة، MinIO): يُرفع، فيعيد المحرّك التسليم حتى خمس ثم DLQ. ولا يُحرَّر العقد هنا كما يفعل STG‑01، لأن الدفعة لا تأخذ عقداً: ملكيتها `attempt`، فإعادة التسليم لا تُرفض. وبعد DLQ ينتهي العقد بلا تقدّم، فتظهر `stale: true` وتُستأنف المهمة بالواجهة.
- **التوازي:** مجموعتان معاً من الوثيقة نفسها، في مهمتين asyncio، فلا يعمل ابنان لمستأجرين. وتزامن العامل 1 ونسخته واحدة، فيبقى شرط `INC1‑R‑17` (`tests/unit/test_compose_electrical_profile.py:195-208`).
- **التقدّم:** `job.progressed` كما اليوم مقيّد المعدّل، `stage` آخر مرحلة في `PAGE_STAGES`، و`done` صفحات اكتملت كل مراحلها المطلوبة. و`JobView.counts` (`application/jobs.py:85`) تصير عدّ المرحلة الجارية، والعدّ لكل مرحلة في `getDocumentPage`.

#### 3.6.5 المسح والميزانية (دور `ocr`، STG‑03)

- **متى تُطلب المناطق:** بعد `extract` لصفحة `vector_outlined` أو `mixed` حين يكون `scan_uncovered` أو `scan_title_block` مفعّلاً. خطة المسح دالة نقية:

```python
# drawings/domain/scan_plan.py
def plan_scan(text: PageText, frame: FrameGeometry | None, profile: TextRecoveryProfile,
              budget: PageScanBudget) -> tuple[ScanRegion, ...]:
    """(a) the title block's frame cell when geometry found it and no line inside reads (D-19
    condition 2); (b) the uncovered boxes, largest stroke count first, merged to <= 8 regions
    covering <= 35% of the page. Deterministic; empty means the page needs no scan."""
```

- كتلة العنوان هنا تُعرف **هندسياً** من الإطار (§3.8.2 الخطوة 1..3)، بلا نص. فلا حلقة بين STG‑03 وSTG‑04.
- لا مناطق ← لا صفّ `scan` ولا حدث. ومناطق ← صفّ `scan = pending` في معاملة الصفحة نفسها، وحدث `electrical.page_batch.ocr_requested.v1` بالحمولة `{job_id, document_id, page, region_refs[], attempt}`. و`region_refs` معرّفات مناطق (`region_id`) محفوظة مع `page_json`؛ فالحمولة مراجع لا بيانات (HLD §7.8).
- **الصفحة تنتظر.** دور `ocr` يمسح، ويضيف السطور بطريقة `scan` إلى نص الصفحة (مشتق نص جديد)، ثم **هو** يجري `layout` و`identify` لتلك الصفحة ويفحص الاكتمال. وفشل المسح ← `scan = needs_review (scan_failed)` ثم `layout` و`identify` بالنص الموجود. فلا صفحة محبوسة.
- **الميزانيات** (F0 S‑4، ✅ `INC2‑Q‑5` (أ)): لكل صفحة 8 مناطق و35٪ من مساحتها. ولكل وثيقة `ELE_OCR_DOCUMENT_PAGES` صفحة تتعرّف (1,000). ولكل مساحة عمل في 24 ساعة `ELE_OCR_WORKSPACE_DAILY_PAGES` (10,000؛ 0 = بلا حدّ)، تُعدّ من `drawings_page_layouts.recognized_at` خلال آخر 24 ساعة بفهرسه الجزئي. والمتجاوز: لا تعرّف ولا مسح، والصفحة `needs_review` بسبب `ocr_budget_exceeded`، ولا يفشل الملف.
- **رابطة المجموعة:** `cg.electrical.ocr` يستهلكها دور CPU في ملف `electrical` (RSK‑14)، ويدخلان معاً في `PR‑F`، فلا مجموعة بلا مستهلك.

#### 3.6.6 الملف المؤقت (يعيد تصميم `INC1‑D‑20`)

- `SourceStager.ensure(ctx, document_id, storage_key, expected_size, sha256)`: إن وُجد الملف المرحّل بحجمه وتاريخه أُعيد كما هو؛ وإلا حُذفت أي نسخة أخرى في الحجم ثم نُزّل وتُحقّق البصمة على أول تنزيل.
- **وثيقة واحدة في الحجم في أي لحظة.** هذا يحفظ معنى شرط `INC1‑R‑17` (التعرّض ملفات مستأجر واحد فقط) بعد أن صارت الوثيقة تمرّ على عدة رسائل. وكلفته إعادة تنزيل حين تتداخل وثيقتان: ملف 30 MB جزء من الثانية؛ وملف 1 GB ثوانٍ لكل دفعة.
- يُحذف عند `pages_completed` أو الإلغاء أو التطهير، وعند الإقلاع كل ما هو أقدم من ضعفي العقد (كما اليوم، `electrical_bootstrap.py:191-196`).

#### 3.6.7 نشر حقائق الصفحة وحدودها

- لكل صفحة: ≤ 1,500 سطر، و≤ 4,000 ظهور، و≤ 500 منطقة، و≤ 2,000 رابط. الزائد يُسقط من آخر الترتيب، ويُسجَّل في `needs_review` بعدده.
- كل كتابة لحقائق صفحة «حذف ثم إدراج» في معاملة واحدة، بمفاتيح حتمية. ففشل المعاملة يترك حقائق الإصدار السابق كما هي، لا نصفها.
- لا يُكتب نص صفحة في أي سجلّ: السطور مجاميع وأعداد ومعرّفات فقط (§3.7.4).

#### 3.6.8 الاكتمال (دور `document`، `pages_completed`)

مستهلك `electrical.document.pages_completed.v1` (فكرة HLD §7.3)، بمعاملة تطالب ثم:
1. يعيد **تجميع التوصيف** بعد النص كله (FR-EVAL-14، «تجميع بعد STG‑02 وSTG‑03»): لغات الصفحات الفعلية، وعدد السطور بطريقتها، وعدّ الاصطلاحات من **ظهورات** STG‑05 لا من عيّنة STG‑01. ولا يغيّر الاقتراح ولا الإعداد.
2. يبني **خريطة الأوراق** (FR-LAY-06): (صفحة فعلية ← رقم ظاهري، وسم)، ويفحص التفرّد: رقمان متساويان أو ترتيب يخالف ← تُوسم الصفحات `needs_review` بسبب `duplicate_sheet_number`، ولا يُصحَّح شيء.
3. يدمج **مقترحات الأسطورة** عبر الصفحات (§3.8.7).
4. `job.succeed`، ويحذف النسخة المؤقتة، ويُصدر `job.completed`.
5. ينشر دفعات التصيير العالي (`mode: render_hd`) إن فُعّلت (`INC2‑Q‑4`)؛ ليست جزءاً من المهمة.

وكل خطوة مثالية: إعادة الحدث تعيد كتابة القيم نفسها، و`fan_in_attempt` يمنع إصدارين متزامنين من الوثيقة.

### 3.7 العامل والطوبولوجيا والإعداد

#### 3.7.1 الأحداث والمجموعات (`PLT‑11`)

| النوع | الحمولة | المنتِج ← المستهلك | المجموعة | الـPR |
|---|---|---|---|---|
| `electrical.page_batch.requested.v1` | `job_id`، `document_id`، `pages[]` (1..50)، `attempt`، `mode` (`analyze` \| `render_hd`)، `from_stage?` | دور `document` (أو `pages` لدفعة تكميلية) ← `pages` | `cg.electrical.pages` | `PR‑E` |
| `electrical.page_batch.ocr_requested.v1` | `job_id`، `document_id`، `page`، `region_refs[]` (1..8)، `attempt` | دور `pages` ← `ocr` | `cg.electrical.ocr` | `PR‑F` |
| `electrical.document.pages_completed.v1` | `job_id`، `document_id`، `attempt`، `counts` (حسب المرحلة والحالة) | `pages`/`ocr` ← دور `document` | `cg.electrical.document` | `PR‑E` |

- لكل نوع JSON Schema بحقول معلنة، بلا `null`، وبلا نص رسم ولا اسم ملف، ومعه اختبار تطابق.
- **`STATIC_CONSUMER_TOPOLOGY`** (`framework/events/topology.py:143-153`): `cg.electrical.document` تصير بأنواع ثلاثة (`document.registered`، `job.requested`، `document.purged`) ومعها `document.pages_completed` (`PR‑E`)، و`cg.electrical.pages` (`PR‑E`)، و`cg.electrical.ocr` (`PR‑F`). **لا رابطة بلا مستهلك في الزيادة نفسها:** `cg.electrical.ocr` تدخل مع دورها في `PR‑F`، لا في `PR‑E`.
- **`PUBLISHED_STREAMS`** لا يتغيّر (مجرى واحد). و**`stream_trim`**: المجموعة `cg.electrical.ocr` لها مستهلك في ملف `electrical`، فإن كان الملف متوقفاً تقف المجموعات الثلاث معاً كما يقف `cg.electrical.document` اليوم، وهذا سلوك موثّق لا حبس جديد (`RSK‑14`).
- **المُرحِّل ينشئ كل مجموعة في الجدول قبل أول نشر** (`src/app/workers/bootstrap.py:392`)، والأنواع جديدة، فلا حدث يفوت مجموعته.
- **الترقية:** تُبنى صورة `app` وتُعاد خدماتها (`app` و`outbox-relay` و`worker-electrical`) معاً. فلو عمل المُرحِّل الجديد قبل العامل الجديد بقيت الأحداث معلّقة في مجموعتها حتى يأتي مستهلكها، ولا تُقص. ولا تُحذف مجموعة.
- **الإشعارات** `_NOTIFY_STREAMS` (`composition_root.py:520-523`) لا تتغيّر: لا نوع جديد للمستخدم. `job.progressed` يكتفي بتوسيع قيم `stage`.

#### 3.7.2 العامل (`worker-electrical`)

```python
# src/app/workers/electrical_bootstrap.py
ROLE_PAGES: Final = "pages"; ROLE_OCR: Final = "ocr"
CG_ELECTRICAL_PAGES: Final = "cg.electrical.pages"; CG_ELECTRICAL_OCR: Final = "cg.electrical.ocr"
EV_PAGES_REQUESTED = "electrical.page_batch.requested.v1"
EV_OCR_REQUESTED = "electrical.page_batch.ocr_requested.v1"
EV_PAGES_COMPLETED = "electrical.document.pages_completed.v1"
# roles -> subscriptions; each group has its OWN GroupLedger, so ids never collide across groups
```

- الأدوار في عملية واحدة وبالتزامن 1 (كما اليوم، `electrical_bootstrap.py:8-18`)، والمحرّك يقرأ كل مجموعة مرة لكل دورة (`engine.py:369-381`). فثلاث مجموعات تعطي تأخيراً أقصاه مجموع `CONSUMER_BLOCK_MS` للمجموعتين الفارغتين حين تحمل الثالثة عملاً. لذلك يُخفَض في `worker-electrical` وحدها: `CONSUMER_BLOCK_MS: 500` (`docker-compose.yml` بيئة الخدمة، والقيمة العامة 5000 تبقى، `docker-compose.yml:208`). التأخير الأقصى ثانية واحدة بدل عشر (`INC2‑D‑11`).
- `stale_idle_ms` = `max(consumer_stale_idle_s, 2 × job_lease_s)` كما اليوم (`electrical_bootstrap.py:86-88`)، أي 1,200 ث. والدفعة المعتادة دونه بكثير. ودفعة تتجاوزه (مجموعات تفشل كلها وتُعاد صفحةً صفحة) لا تُستعاد أثناء عملها، لأن المستهلك واحد والكنس يجري بين الدورات.
- النبض: `liveness.beat()` بعد كل نداء `ele-parse` (المهلة 600 ث القائمة تكفي: أطول نداء ≤ 300 ث).
- **`worker-electrical` لا يستورد ONNX ولا OpenCV ولا NumPy.** يبقى طرفَ HTTP (`services/` خارج `src/`)، فلا يتغيّر عقد import-linter.

#### 3.7.3 الإعداد

`ElectricalSettings` يكتسب (كلها غير سرّية، كلٌّ في `.env.example` و`05` §2):

| المفتاح | الافتراضي | المعنى |
|---|---|---|
| `ELE_PAGE_BATCH` | 20 (1..50) | صفحات الدفعة |
| `ELE_EXTRACT_GROUP` | 5 (1..8) | صفحات النداء الواحد |
| `ELE_RENDER_HD` | `false` | ✅ `INC2‑Q‑4` (ب): مطفأ، ويفعّله المالك بعد قياس حقيقي |
| `ELE_OCR_DOCUMENT_PAGES` | 1000 | §3.6.5 |
| `ELE_OCR_WORKSPACE_DAILY_PAGES` | 10000 | §3.6.5 |
| `ELECTRICAL_WORKER_ROLES` | `document,pages,ocr` | قيمة Compose تتغيّر في `PR‑E` و`PR‑F`؛ وثابت الأسماء (`settings.py:690`) يتوسّع |

و`ele-parse` يكتسب: `ELE_PARSE_OCR_THREADS` (2)، و`ELE_PARSE_MODELS_DIR` (`/app/models`)، و`ELE_PARSE_EXTRACT_MEMORY_MB`، و`ELE_PARSE_OCR_MEMORY_MB`، و`ELE_PARSE_PAGE_TIMEOUT_OCR_S`، و`ELE_PARSE_PAGE_TIMEOUT_SCAN_S`. وكلها في بيئة `ele-parse` وحدها، بلا سرّ، ويثبّتها اختبار `test_compose_electrical_profile.py` ومفتاح `.env.example`.

#### 3.7.4 السجلات (NFR-SEC، خصوصية)

- أسطر بمعرّفات وأعداد وأزمنة: `electrical.pages.batch_started/finished`، و`electrical.page.stage` (الصفحة، المرحلة، المدة، الحالة، السبب)، و`electrical.ocr.finished` (السطور، التغطية، زمن التعرّف)، و`electrical.stage.ensured`.
- **لا نص رسم، ولا معرّف مقروء، ولا مسار ملف، ولا حقل من كتلة العنوان في سجلّ.** خطأ يحمل نصاً يُهرّب (`framework/observability/untrusted.py`)، واختبار يقرأ كل سطر سجلّ في اختبارات الوحدة ويرفض أي نص من الصفحة.
- أزمنة الصفحة (`extract_ms`، `recognize_ms`، `render_ms`، `scan_ms`) في `drawings_page_layouts.stats`، وهي مصدر قياس NFR-PERF-01 (§5.5).

#### 3.7.5 المراقبة

- العامل بلا `/metrics` (`framework/observability/metrics.py:311`)، فلا مقياس جديد منه؛ والأرقام من سطور السجلّ والمجرى.
- `AizzakElectricalQueueWaitHigh` (`deploy/prometheus/alerts.yml:962-994`) يشمل المجموعات الثلاث لأن قياسه `max by (stream, group)`. ويُعدَّل وصفه ودليله: كل مجموعة ودورها وأمرها، وسبب انتظار `ocr` طبيعي حتى سقف الميزانية. العتبة 1,800 ث تبقى، ويُقترح قياسها بعد أول حمل (`INC2‑R‑10`). وحالة `promtool` لـ`cg.electrical.ocr`.
- لا تنبيه جديد: صفحة `needs_review` ليست حادثة، وتظهر في `getDocumentPage`.

#### 3.7.6 الموارد (✅ `INC2‑Q‑2` (أ)، مؤقتة حتى `G2`)

| الخدمة | اليوم (`docker-compose.yml:2245-2250`، `:2288-2289`) | المُقَرّ | لماذا |
|---|---|---|---|
| `ele-parse` CPU | 1.0 | **3.0** (`ELE_PARSE_CPUS`) | ابنان، كلٌّ خيط استخراج وخيطان لـONNX |
| `ele-parse` ذاكرة | 2,560 MiB | **3,072 MiB** (`ELE_PARSE_MEM`) | ابنان × نحو 1.2 GiB RSS للتعرّف، والخادم. تقدير يثبّته `G2` |
| `ele-parse` `pids` | 64 | **192** | ابنان × (بايثون + ONNX + خيوط) مقيسة |
| `ele-parse` `/tmp` | 64 MiB | 64 MiB | ONNX لا يكتب ملفاً (`RLIMIT_FSIZE = 0`) |
| `worker-electrical` ذاكرة | 1 GiB | **1 GiB** | حزمتان لمجموعتين ≤ 72 MiB لكل جواب، والحزم المؤقتة تُفرَّغ |
| `worker-electrical` CPU | 1.0 | 1.0 | حلّ الحزم أعمدة `array`، لا NumPy |

- الفرق في حدود الذاكرة فوق اليوم 512 MiB (وفي CPU +2)، خلف ملف `electrical` المعطّل افتراضياً (`INC1‑R‑5`). والمضيف المحلي له اليوم نحو 8 GiB متاحة من 12 (فحص `free`، وقته غير مضمون)، والمكدّس قبل تفعيل `electrical` يستعمل ~4 GiB.
- `RLIMIT_AS` يقيّد العنوان الافتراضي لا RSS، وONNX Runtime تحجز خلف خيوطها. لذلك يُثبَّت الرقمان معاً بقياس `G2`، لا بحساب.
- إن تجاوز إسقاط المشروع في `G2` 25 دقيقة، عاد القرار إلى المالك ومعه البديل الجاهز: 3 أبناء، و4 نوى، و4 GiB (§6.2). ولا يُطبَّق البديل بلا قراره.
- لا GPU في أي قرار.

### 3.8 STG‑04: التخطيط

كله في `drawings/domain` النقي، مدخلاته `PrimitiveTable` و`PageText` وملف الإعداد، ومخرجاته `PageLayout`. **وكل رقم في §3.1.4 و§3.8 و§3.9 مفتاح باسمه في `page_analysis.toml`** بقيمته المكتوبة هنا افتراضاً، ومصدره «تخمين يُضبط بالقياس» ما لم يُذكر غيره. والجدول §3.2.5 يسرد ما يجوز لملف الإعداد أن يتجاوزه منها.

```python
# drawings/domain/layout.py
class PageType(StrEnum):  # FR-LAY-01
    COVER; TOC; CIRCUIT; SINGLE_LINE; PLC_IO; TERMINAL_DIAGRAM; CABLE_DIAGRAM; CONNECTION_LIST
    DEVICE_LIST; PARTS_LIST; STRUCTURE; PANEL_LAYOUT; SYMBOL_LEGEND; TEXT; OTHER
@dataclass(frozen=True, slots=True)
class PageLayout:
    page_no: int; type: PageType; type_confidence: float
    frame: Box | None; title_block: tuple[TitleField, ...]; grid: PageGrid | None
    regions: tuple[Region, ...]; structure: PageStructure | None
    sheet_label: str | None; sheet_number: str | None; tables: tuple[PageTable, ...]
    legend_candidates: tuple[LegendCandidate, ...]; language: PageLanguage
```

#### 3.8.1 الإطار (FR-LAY-02)

1. **خطوط الإطار:** قطع أفقية ورأسية بطول ≥ `min_frame_ratio` × بُعد الصفحة، تُدمج المتراكبة على خط واحد (فجوة ≤ 1 pt، انحراف ≤ 0.6 pt).
2. **مرشّح الإطار:** أوسع مستطيل أركانه تقاطعات هذه الخطوط، وكل ضلع فيه على بُعد ≤ `frame_margin_pt` من حافة الصفحة أو من إطار خارجي. عند تساوي مرشّحين: الأكبر مساحة ثم الأصغر بُعد من الحافة.
3. بلا إطار مكتشف: `frame = None`، والصفحة تُعامَل كاملة منطقة رسم، ولا كتلة عنوان، و`needs_review`.

#### 3.8.2 كتلة العنوان (FR-LAY-02، M-02)

1. **الخلايا:** خطوط أقل طولاً داخل الإطار تُبنى منها شبكة محلية (قطع متصلة بالحدود أو ببعضها). خلية = مستطيل أصغر بين خطوط، بلا خط يخترقه.
2. **المرشّح:** مجموعة خلايا ملتصقة بحافة الإطار السفلى أو اليمنى (أو اليسرى) وفيها ≥ 3 خلايا، مساحة المجموعة بين 1٪ و25٪ من مساحة الإطار، وعدد الخلايا الأكبر في الركن الأيمن الأسفل. (يمين/أسفل لأنها عادة IEC وGB/T؛ وملف الإعداد يثبّت الركن.)
3. **الحقول:** لكل خلية نص، وتسمية الحقل بمطابقة معجم في `page_types.toml` (مثل `drawing_no`، `sheet`، `project`، `title`، `rev`، `date`، `drawn`، `checked`، `approved`، `function`، `location`) بالإنجليزية والصينية. التسمية قد تكون في الخلية نفسها أو فوقها أو على يسارها (الخلية المجاورة الصغيرة النص)؛ وتُؤخذ أقرب خلية قيمة في الاتجاه المألوف للغة.
4. **الثقة لكل حقل** = 0.5 + 0.3 (تسمية قريبة مطابقة) + 0.2 (نوع القيمة يوافق الحقل: رقم، تاريخ، رمز). وللحقل `sheet` قالب `\d+` أو `\d+/\d+`.
5. `structure`: قيمتا `function` و`location` (مثل `=A1` و`+P2`) يحلّلهما `IdentifierParser` بالاصطلاحات؛ وهما «البنية الموروثة» لكل ظهور في الصفحة (FR-ID-02).
6. أقل من `title_block_min_fields` حقل بثقة ≥ 0.6: `title_block_missing` ما لم يكن نوع الصفحة `text` أو `cover`.

**النموذج المُعطى (القالب):** إن حمل ملف الإعداد `layout.title_block_template` (نسب صندوق وحقول بنسب داخله) استُعمل بلا كشف، وذُكر `template`. (FR-LAY-05 لا يُبنى: قالب مكتوب يدوياً فقط.)

#### 3.8.3 الشبكة (FR-LAY-03، M-03)

1. أرقام وحروف على حواف الإطار الأربع: سطر نص ارتفاعه ≤ ارتفاع صفحة/25 ومسافته من خط الإطار ≤ 24 pt. تُقبل السلسلة إن كانت أعمدتها أرقاماً متتابعة (1..n) أو صفوفها حروفاً متتابعة (A..) أو العكس، بفجوات متقاربة (±10٪).
2. لكل تسمية حدّان: منتصف المسافة إلى الجارة، أو حدّ الإطار. و`PageGrid.column_at(x)` و`row_at(y)` دالتان نقيتان.
3. الحاجة إلى ≥ 3 تسميات متتابعة على ضلع واحد؛ وإلا `grid = None` ويُستعمل (صفحة، إحداثية) ولا يُختلق.
4. نقطتان متناقضتان بين ضلعين متقابلين: يُؤخذ الضلع الأطول ويُسجَّل `grid_mismatch` في `needs_review`.

#### 3.8.4 المناطق والجداول (FR-LAY-04)

- **المناطق:** `drawing`، `title_block`، `notes`، `table`، `legend`، `revision_block`، `terminal_strip`، `contact_list`، `border`. ما لا يُصنَّف يبقى في `drawing`.
- **الجدول المُحدّد:** شبكة من ≥ 2 صفوف و≥ 2 أعمدة من خطوط (عتبة تقاطع 1.5 pt)، تُدمج خلاياها الناقصة الخط من صفّ واحد. **والجدول بلا خطوط:** أسطر متوازية الأعمدة: 3 أسطر أو أكثر مشتركة في ≥ 2 نقطة بداية أعمدة (بعتبة 0.6 × الارتفاع) وارتفاع خطي منتظم. وكلاهما يمنح رقم الجدول وصفوفه وأعمدته، وترويسة (سطر أول بلا أرقام، أو مطابق معجم الترويسات).
- **دور الجدول** من الترويسة: `terminals`، `contacts`، `wires`، `parts`، `device_list`، `legend`، `plc_io`، `generic`.
- **`truncated`** (فج‑50): خلية من نقاط متتالية (`…` أو `...` أو `⋯`) أو صف من شرطات. الصفوف الأخرى تُقرأ، ولا يُستنتج ما بينها، ومنطقة الجدول تحمل `truncated: true`.
- **قائمة التلامسات (فج‑47):** جدول بصفين فأكثر يقع ضمن 3 ارتفاعات أسفل نص معرّف جهاز، وفيه عمودان على الأقل يحمل كل خلية أرقام أطراف أو موقعاً. دوره `contacts`.

#### 3.8.5 نوع الصفحة (FR-LAY-01، M-01)

دالة نقاط حتمية على معجم `page_types.toml`: كلمات العنوان، ورؤوس الجداول، ونسبة النص، وعدد الجداول ونوعها، ووجود شبكة، وكثافة الرموز المغلقة (فقاعات/مستطيلات). وكل نوع حدّ أدنى، والأعلى يفوز، والتعادل بترتيب `PageType`. **الثقة** = هامش الأول على الثاني محدوداً بـ[0.3، 1.0]. و`OTHER` حين لا نوع يبلغ حدّه. الصينية المبسطة والإنجليزية معاً (NFR-I18N-02)، واللغات إضافات بيانات (NFR-MNT-04).

#### 3.8.6 رقم الصفحة الظاهري (FR-LAY-06، فج‑44)

- `sheet_label` نص كتلة العنوان كما طُبع (مثل `=A1+P2/12`)، و`sheet_number` الجزء الرقمي أو الحرفي منه؛ يُحلّل بقواعد `IdentifierParser`.
- الخريطة لكل وثيقة تُبنى في `CompletePages` (§3.6.8)، وتُستعمل في INC‑5 لحلّ `27.6E`. وفي INC‑2 يظهر التحويل في `near.at.grid` وفي `getDocumentPage`.
- رقم غير مقروء: `None`، ولا يُخمَّن من الترتيب.

#### 3.8.7 الأسطورة (FR-ING-14)

```python
# drawings/domain/legend.py
@dataclass(frozen=True, slots=True)
class LegendCandidate:
    key: str; text: str; kind: LegendKind   # ansi | abbreviation | device_name | symbol_name
    suffixes: tuple[str, ...]; box: Box; confidence: float; page_no: int
def extract_legend(tables: Sequence[PageTable], lines: Sequence[TextLine], rules: IdentifierRules
                   ) -> tuple[LegendCandidate, ...]: ...
def merge_legend(pages: Sequence[Sequence[LegendCandidate]]) -> tuple[LegendEntry, ...]:
    """Same (kind, normalised key) across pages: identical text -> one entry, highest
    confidence, every evidence kept; different text -> one entry per text, conflict=True on
    each, and neither may be applied (F2-AC-34). Deterministic order: kind, key."""
```

- **صفحة الأسطورة** = نوعها `SYMBOL_LEGEND`، أو جدول دوره `legend`، أو عمودان متقابلان (رمز، وصف) بمطابقة ترويسة. خارجها لا استخراج.
- **الأنواع:** `ansi` (مفتاح `\d{1,3}[A-Z]{0,3}`)، `abbreviation` (حروف كبيرة ≤ 12)، `device_name` (`^[A-Z]{1,3}\d{1,3}$`، تغذّي فج‑28)، `symbol_name`. غيرها يُسجَّل ويُسقط بسببه.
- **النص الحرفي:** لا يصير تعبيراً نمطياً أبداً. `.` و`(` و`[` و`*` تُحفظ كما كُتبت (NFR‑F2‑3)، والنص بتطبيع NFC، والمدخل لا يُقرأ معرّفاً.
- **الحدود (NFR‑F2‑5):** 1,000 مدخل لكل وثيقة؛ والمفتاح 16 والنص 128؛ والزائد أو الطويل يُرفض بسبب مُحصى في التقرير، لا بصمت.
- **التعارض والعلم:** `legend_conflict` للصفحة، والمدخلات المتعارضة `conflict: true` ولا يُقبل أحدها بلا اختيار نص صريح.
- **التأكيد:** §3.3.5. **لا تُطبَّق** أي قيمة قبل إنسان: `STG‑05` يقرأ `profile.legend` المؤكَّد فقط، لا المرشّحين.
- **إعادة المعالجة:** المرشحون يُحسبون من جديد بكل نتيجة `layout`، والمؤكَّد يبقى في ملف الإعداد. حذف الملف يحذف المرشحين والأسطورة معاً لأن كلاهما مفتاحه الوثيقة (DEC‑18).

### 3.9 STG‑05: المعرّفات في سياقها

المدخل: `PageText` + `PageLayout` + ملف الإعداد المؤكَّد + `IdentifierParser(rules.narrowed(selection))` بقواعد `shared` المضيّقة. **مخرجاته حقيقة بحالة `automatic`**، لا `cross_confirmed`.

#### 3.9.1 ترتيب القراءة

1. السطور تُرتَّب بترتيب القراءة: أعلى (أو أسفل) حسب المنطقة، ثم يمين/يسار حسب لغة الصفحة؛ وداخل الخلية ترتيب الجدول. ونص جداول يُقرأ صفاً صفاً.
2. لكل سطر كلماته بحدود هندسية (§3.1.4) أو من `spans`.
3. لكل كلمة: `IdentifierParser.parse` بلا تلميح. ثم سياق (§3.9.3) يقرر تلميحاً أو لا. والقراءة البديلة تُحفظ دائماً (`alternatives`، `shared/kinds.py:151`).
4. دور النص يُحدَّد أولاً ويمنع الظهور: `rating` (ومعه `is_ratio_follower`)، و`enclosed_number`، و`box_code`، و`model`، و`annotation`. كلمة بدور لا تصير ظهوراً. ويُحفظ دورها وموضعها (معرّف نصها لا نصها) في `layout.text_roles` من صفّ الصفحة، فتظهر في طبقة `text` من `near`.

#### 3.9.2 نافذة الكلمتين (فج‑36، فج‑24)

- إذا قرأت الكلمتان المتجاورتان **معاً** (بلا مسافة، أو بالفاصلة المسموح بها لـKKS) معرّفاً من نوع (`kks_tag`، `terminal`، `wire_number`، `ansi_device`، `cross_reference`)، وكانت كلٌّ منهما بمفردها غير صالحة أو بلا معنى، فالنتيجة الجمع. والمسافة بين صندوقيهما ≤ 1.0 × ارتفاع السطر.
- القوائم بمسافة بعد الفاصلة (`X7:41, 42`): تُجمَّع إذا كان آخر الأولى قائمة مفتوحة.
- النطاق بكلمة (`9A` ، `to` ، `9A`): تجمع الكلمات الثلاث إن كانت الوسطى في `[groups] range_words`.

#### 3.9.3 سياق ANSI (FR-ID-16، فج‑42، F2‑AC‑29، §8 في F2)

دليل السياق (أي منها، ولا شيء آخر يكفي):
- **داخل فقاعة** (§3.1.4 الخطوة 3): `hint = ANSI_DEVICE` يُمرَّر.
- **في تسمية مكدّسة** (§3.9.4).
- **ملاصق لرمز جهاز:** في INC‑2 لا رموز معرّفة (INC‑4)، فيُقبل «الموضع قرب فقاعة أو صندوق مغلق بضلع ≤ 3 × ارتفاع السطر، ومسافة ≤ `near_symbol_pt`» دليلاً ضعيفاً يخفّض الثقة × `context_weak` ويُعلَّم `weak_context`.
- **عمود رقم في جدول دوره `legend` أو `device_list`:** `hint = ANSI_DEVICE` للخلايا المطابقة `^\d{1,2}[A-Z]{0,3}$`. يعالج 12 رقماً بلا فئة (`../inc-1/f3/measure-4.md` §3).
- الرقم المجرد بلا دليل يبقى كما يقرأه المحلّل (رقم سلك أو نقطة)، ويُسجَّل البديل `ANSI_DEVICE` ولا يُحسم.
- **اسم نوع بلا فئة** بلا سياق لا يُقرأ (القاعدة في `shared` تبقى).

**اكتمال المعرّف** (FR-ID-16 Q2): أول مصدر متوفر من ترتيب ثابت: وسم KKS في **صندوق البنية** المحيط (§3.9.5)، ثم الجزء الثاني من التسمية المكدّسة، ثم `structure` من كتلة العنوان، ثم لا شيء (يبقى `needs_context`). والأصل مخزَّن.

#### 3.9.4 التسمية المكدّسة (FR-ID-17، M-36)

```python
# drawings/domain/stacked.py
@dataclass(frozen=True, slots=True)
class StackedLabel: top: TextLine; bottom: TextLine; separator: Segment | None; in_bubble: bool
def find_stacked(lines, bubbles, segments, p: IdentifiersProfile) -> tuple[list[StackedLabel], list[Ambiguity]]: ...
```

1. مرشّح = سطران بالزاوية نفسها (0°/90°، وتفاوت ≤ 2°)، الفوقي فوق السفلي باتجاه القراءة، ومركزاهما في عرض السطر الأعرض ضمن `align_tolerance`، ومسافة حافتيهما ≤ `gap_max_ratio` × الارتفاع، وارتفاعاهما متقاربان (≤ 1.5×).
2. **بفاصل:** قطعة أفقية في فضاء السطر، طولها ≥ 0.6 من العرض، بين السطرين (ضمن `gap_max_ratio`)، و`bar_overhang_ratio` بحدّ من الجانبين. **أو داخل فقاعة واحدة** (مركز الفقاعة في اتحاد صندوقيهما).
3. نص الأول يجب أن يقرأ جهازاً (`ansi_device` بالتلميح، أو `letter_code`)، وإلا لا ربط. ونص السطر السفلي **لا** يلزمه أن يكون معرّفاً: يُحفظ كما هو (FR-ID-17).
4. **الغموض:** سطر علوي له مرشّحا سفليان ضمن الشرط، أو سفلي له مرشّحا علويان، أو زاويتان مختلفتان بأكثر من 2°، أو قريب من فاصل لا يغطي العرض، أو بلا فاصل خارج فقاعة ← لا ربط، `stacked_label.ambiguous`، ولا سطر من الاثنين يفقد نصه.
5. **الناتج:** ظهور واحد بجزأين، كل جزء نصه وصندوقه في دليل واحد. `parts[0]` جهاز ANSI (أو رمز) برقمه ولاحقته ومثيله؛ و`parts[1]` `{text, kind?, value?}`: حروف ANSI (`T` فوق `87`)، أو رقم (`51` تحت `50` ← مركّب)، أو غيرهما نصاً. `full` = `50/51` للمركّب، و`87T` للاحقة، وإلا الأول وحده.
6. **تعليم الفاصل:** بدائيات القطعة توضع في مجموعة `stacked_separator` وتُكتب في الدليل (`refs`). ولا تدخل INC‑4 سلكاً (DEC‑19، §8.6).
7. فقاعة واحدة فيها سطر واحد رقماً: جهاز ANSI بلا جزء ثانٍ (§3.9.3).

#### 3.9.5 البنية الموروثة والمعرّف الكامل (FR-ID-02، M-05، فج‑46)

1. **صندوق البنية:** مستطيل مغلق من خطوط متصلة (أو متقطعة)، بداخله بدائية معرّف `=`/`+` في ركنه، أو `=` داخل إطاره: يُخزَّن كتلة بنية. الصفحة كلها الصندوق الأدنى بـ`structure` من كتلة العنوان.
2. `full(occurrence)`: تُجمع الجوانب: جوانب الصندوق الأعمق المحتوي (من الخارج للداخل) ثم جوانب الظهور؛ ويُزال الجانب المكرر حرفياً، ويُنبَّه التعارض (جانب `=` مختلف بين صندوقين متداخلين) بـ`structure.conflict` ولا يُختار أحدهما.
3. ترتيب الأولوية: جوانب الظهور المكتوبة > أعمق صندوق > الصفحة؛ والملف (FR-ING-01) يعرّف **جوانب إضافية** لا يُستنتج منها شيء.
4. **الطرف بالجهاز (فج‑46، فج‑31):** سطر نقطة اتصال (`terminal`/`connection_point`) داخل صندوق جهاز واحد المعرّف ← `full = <device>:<point>`. صندوقان أو صفر ← لا هوية طرف، ويبقى المحلي وحده، و`needs_review` إن تعدد.
5. **فج‑45** (`co_located`): ظهوران أنواعهما مختلفة على نقطة واحدة (مسافة مركزيهما ≤ 0.8 × ارتفاع السطر) يربطهما `co_located`؛ لا دمج ولا رمي.
6. **M-05**: `full` بعد الموروث مقابل الحقيقة.

#### 3.9.6 التصحيح الموضعي (FR-ID-18، F2‑AC‑23..26)

```python
# drawings/domain/correction.py
def correct_kks(line: TextLine, spec: KksRules, profile: CorrectionProfile) -> CorrectionResult | None:
    """None: switched off, no reading with one correction, or no per-character confidence."""
```

1. يعمل **فقط** على سطر OCR، KKS مفعّل، والقارئ أعاد `kks` غير صالح بسبب `letter_in_digit_position` أو `digit_in_letter_position`، والسطر له ثقة لكل حرف.
2. الزوج مسموح في `[confusables] pairs` (`0O`)، والموضع نوعه محدد في القناع (رقم أو حرف)، ولا يُستثنى الحرف في القواعد (`excluded_letters`).
3. مرشّح واحد فقط: بديلان صالحان، أو صيغة تعارض أخرى، أو البديل الثاني لـONNX يقرأ حرفاً آخر بثقة أعلى من الأول (`alternative_confidence`)، ← لا تصحيح.
4. ثقة الحرف المبدَّل ≤ `correction.max_char_confidence` (0.90)، لأن الحرف شبه الأكيد لا يُصحَّح.
5. الناتج: `canonical` المصحَّح، و`corrected` = الشكل، و`corrections` قائمة (الموضع، قبل، بعد، `confusable.0O`، الثقة)، والمعرّف `valid` إن صار القناع صالحاً، و`confidence` × `confidence_factor`، و`status = automatic`، وعَلَم `auto_corrected` في `issues` (غير مبطِل).
6. الأصل (`original`) لا يُمس، ويُخزَّن النص الأصلي والمصحَّح معاً (DATA-06).
7. **معطّل افتراضياً** حتى F2‑AC‑26، ولا يُصحَّح غير KKS في INC‑2 (الموضع الواحد مؤكد فيه فقط).

#### 3.9.7 التلامسات والمواضع

- **`contact_of`:** صفوف قائمة تلامسات (§3.8.4) ربطُ زوج الأطراف بالجهاز فوقها برابط. `xref` خام في النص.
- **الأسماء:** جدول أسماء المورد (مدخلات `legend` المؤكَّدة بنوع `device_name`، §3.8.7) يتقدّم على الجهد أو السلك **فقط** حين يكون السطر فوق/عند رمز مغلق ≤ 3 × ارتفاعه (فج‑11)؛ وبلا ذلك يبقى الآخر والاسم بديل.
- **الفقاعات غير المعروفة** تُسجَّل بلا تأويل.
- **`model`** (فج‑20): عمود بترويسة «type/型号/model» أو بعد مطابقة مصنّع في `manufacturers` من ملف الإعداد.

#### 3.9.8 الثقة والحالة والحدود

- الثقة الأساسية = ثقة السطر (1.0 للنص المتجهي) × 1.0 للمعرّف بلا سياق، أو × `context_strong` بسياق قوي (فقاعة، مكدّس، عمود)، أو × `context_weak` بسياق ضعيف، أو × `confidence_factor` لو صُحِّح. ولا تزيد على 1.0. (المعايرة NFR-ACC-02 في INC‑11.)
- **حالة الدليل:** كل ظهور مربوط بدليل واحد على الأقل؛ دليل الحرف المُصحَّح يحمل الأصل (DATA-06). والطريقة `vector_text`/`ocr_line`/`ocr_scan`.
- **`needs_review` بدل السكوت:** ثقة دون `min_fact_confidence`، أو سياق ضعيف في معرّف جهاز، يجعل `identify = needs_review` بسبب `low_confidence`.
- **حدود:** نص ≤ 200 حرف للكلمة الواحدة (`parser.MAX_TEXT_CHARS`)، و4,000 ظهور للصفحة.

### 3.10 إعادة معالجة وثائق INC‑1

- وثيقة أكملت STG‑01 قبل INC‑2 (`classify` فقط، `pipeline_version = 1.2.0`) تعمل بـ`reprocess` الكامل: `from_stage = classify` (الافتراضي)، فيمر STG‑01 (يعيد تصنيفها بالمعايير نفسها) ثم تُوزَّع الصفحات.
- `ElectricalDocumentOut` يكتسب `pipeline_version: str | null` و`needs_reprocessing: bool` (`pipeline_version != PIPELINE_VERSION` الحالي والوثيقة `active`). ولا إعادة معالجة آلية: `H6`، قرار بشري (كـF0‑AC وكـ`lld-inc-1` §1.4).
- مهمة قائمة (`awaiting_confirmation`) من INC‑1 عند الترقية: تأكيدها بعد `PR‑E` يمر بالمسار الجديد لأن `_conclude` يتخذ القرار عند إتمام الفحص. وكذلك المهمة `queued` من صورة INC‑1.
- لا أدلة ولا ظهورات لوثيقة لم تُعَد معالجتها: `getDocumentPage` يجيبها بمراحل غير موجودة، و`image` و`near` بـ`409 electrical.page_not_ready`.

## 4. البيانات

هجرتان في سلسلة `electrical` القائمة، تُطبَّقان بـ`python -m app.ops.provision` وحده. عدد السلاسل يبقى 13، ولا schema جديد، ولا دور جديد.

### 4.1 `electrical/0002_page_facts` (`PR‑D`)

```sql
-- 1. widen what 0001 froze (0001's docstring names the way: drop, ADD ... NOT VALID, VALIDATE)
ALTER TABLE electrical.drawings_page_stages DROP CONSTRAINT ck_drawings_page_stages_stage;
ALTER TABLE electrical.drawings_page_stages ADD CONSTRAINT ck_drawings_page_stages_stage
  CHECK (stage IN ('classify','extract','render','scan','layout','identify')) NOT VALID;
ALTER TABLE electrical.drawings_page_stages VALIDATE CONSTRAINT ck_drawings_page_stages_stage;

-- FR-ING-14: 1,000 confirmed legend entries do not fit 64 KiB (INC2-F-7).
ALTER TABLE electrical.drawings_profile_versions DROP CONSTRAINT drawings_profile_versions_body_check;
ALTER TABLE electrical.drawings_profile_versions ADD CONSTRAINT ck_drawings_profile_versions_body
  CHECK (octet_length(body::text) <= 1048576) NOT VALID;
ALTER TABLE electrical.drawings_profile_versions VALIDATE CONSTRAINT ck_drawings_profile_versions_body;

-- 2. the job knows where a reprocess starts and which attempt already fanned in
ALTER TABLE electrical.drawings_jobs
  ADD COLUMN from_stage     text NULL
    CHECK (from_stage IS NULL OR from_stage IN ('classify','extract','layout','identify')),
  ADD COLUMN fan_in_attempt integer NULL CHECK (fan_in_attempt IS NULL OR fan_in_attempt >= 0),
  ADD COLUMN profile_version_id uuid NULL REFERENCES electrical.drawings_profile_versions(id);

-- 3. what STG-02..04 say about a page: one row per page
CREATE TABLE electrical.drawings_page_layouts (
  workspace_id        uuid NOT NULL,
  document_id         uuid NOT NULL,
  page_no             integer NOT NULL,
  page_type           text NULL CHECK (page_type IS NULL OR page_type IN
                        ('cover','toc','circuit','single_line','plc_io','terminal_diagram',
                         'cable_diagram','connection_list','device_list','parts_list','structure',
                         'panel_layout','symbol_legend','text','other')),
  page_type_confidence real NULL CHECK (page_type_confidence BETWEEN 0 AND 1),
  sheet_label         text NULL CHECK (char_length(sheet_label) <= 128),
  sheet_number        text NULL CHECK (char_length(sheet_number) <= 32),
  width_pt            real NOT NULL, height_pt real NOT NULL,
  layout              jsonb NULL CHECK (octet_length(layout::text) <= 262144), -- frame, title block, grid, regions, tables
  stats               jsonb NULL CHECK (octet_length(stats::text) <= 16384),    -- counts and timings, never text
  recognized_at       timestamptz NULL,   -- OCR ran on this page (the budget counts these, §3.6.5)
  pipeline_version    text NOT NULL,
  profile_fingerprint text NOT NULL CHECK (profile_fingerprint ~ '^[0-9a-f]{64}$'),
  versions            jsonb NOT NULL CHECK (octet_length(versions::text) <= 4096),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, document_id, page_no),
  FOREIGN KEY (workspace_id, document_id, page_no)
    REFERENCES electrical.drawings_pages(workspace_id, document_id, page_no)
);
CREATE INDEX ix_drawings_page_layouts_sheet
  ON electrical.drawings_page_layouts(workspace_id, document_id, sheet_number);
CREATE INDEX ix_drawings_page_layouts_recognized
  ON electrical.drawings_page_layouts(workspace_id, recognized_at) WHERE recognized_at IS NOT NULL;

-- 4. derived objects: the row exists BEFORE the object does (§3.4)
CREATE TABLE electrical.drawings_page_artifacts (
  workspace_id uuid NOT NULL,
  document_id  uuid NOT NULL,
  page_no      integer NOT NULL,
  kind         text NOT NULL CHECK (kind IN ('primitives','text','tile')),
  variant      text NOT NULL CHECK (char_length(variant) BETWEEN 1 AND 64),  -- "1-0-3" of a tile; "-" else
  object_key   text NOT NULL CHECK (char_length(object_key) <= 300),
  state        text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','stored','obsolete')),
  bytes        bigint NULL CHECK (bytes IS NULL OR bytes >= 0),
  sha256       text NULL CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
  width_px     integer NULL, height_px integer NULL,
  pipeline_version text NOT NULL,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, document_id, object_key),
  FOREIGN KEY (workspace_id, document_id, page_no)
    REFERENCES electrical.drawings_pages(workspace_id, document_id, page_no)
);
-- the current object of each slot; a reprocess turns the old one 'obsolete' in the same transaction
CREATE UNIQUE INDEX ux_drawings_page_artifacts_current
  ON electrical.drawings_page_artifacts(workspace_id, document_id, page_no, kind, variant)
  WHERE state = 'stored';

-- 5. evidence (DATA-01)
CREATE TABLE electrical.drawings_evidence (
  id           uuid PRIMARY KEY,                -- deterministic uuid5 (shared)
  workspace_id uuid NOT NULL,
  document_id  uuid NOT NULL REFERENCES electrical.drawings_documents(id),
  page_no      integer NOT NULL CHECK (page_no >= 1),
  stage        text NOT NULL CHECK (stage IN ('extract','scan','layout','identify')),  -- whose rewrite replaces it
  sheet_label  text NULL CHECK (char_length(sheet_label) <= 128),
  grid_column  text NULL CHECK (char_length(grid_column) <= 8),
  grid_row     text NULL CHECK (char_length(grid_row) <= 8),
  x0 real NOT NULL, y0 real NOT NULL, x1 real NOT NULL, y1 real NOT NULL,
  angle        real NOT NULL DEFAULT 0,
  method       text NOT NULL CHECK (method IN
                 ('vector_geometry','vector_text','report_table','internal_link','ocr_line',
                  'ocr_scan','vision_model','human','imported','knowledge')),
  refs         jsonb NOT NULL CHECK (octet_length(refs::text) <= 8192),   -- primitive runs, text ids, table cell
  original     text NULL CHECK (char_length(original) <= 512),           -- as printed; never logged
  confidence   real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  versions     jsonb NOT NULL CHECK (octet_length(versions::text) <= 4096),
  created_at   timestamptz NOT NULL DEFAULT now(),
  CHECK (x1 >= x0 AND y1 >= y0)
);
CREATE INDEX ix_drawings_evidence_page
  ON electrical.drawings_evidence(workspace_id, document_id, page_no, stage);

-- 6. identifier occurrences (DATA-02, DATA-06)
CREATE TABLE electrical.drawings_identifiers (
  id           uuid PRIMARY KEY,                -- deterministic uuid5
  workspace_id uuid NOT NULL,
  space_id     uuid NOT NULL,
  project_id   uuid NOT NULL,
  document_id  uuid NOT NULL REFERENCES electrical.drawings_documents(id),
  page_no      integer NOT NULL CHECK (page_no >= 1),
  position     integer NOT NULL CHECK (position >= 0),   -- reading order on the page
  kind         text NOT NULL CHECK (char_length(kind) BETWEEN 1 AND 40),
  convention   text NULL CHECK (char_length(convention) <= 40),
  original     text NOT NULL CHECK (char_length(original) BETWEEN 1 AND 512),
  canonical    text NOT NULL CHECK (char_length(canonical) <= 256),
  full_form    text NULL CHECK (char_length(full_form) <= 512),
  corrected    text NULL CHECK (char_length(corrected) <= 256),
  corrections  jsonb NULL CHECK (octet_length(corrections::text) <= 8192),
  aspect_keys  text[] NOT NULL DEFAULT '{}' CHECK (cardinality(aspect_keys) <= 16),
  search_keys  text[] NOT NULL DEFAULT '{}' CHECK (cardinality(search_keys) <= 128),
  connection_point text NULL CHECK (char_length(connection_point) <= 32),
  detail       jsonb NULL CHECK (octet_length(detail::text) <= 8192),  -- ansi, kks, parts, quantity, aspects
  x0 real NOT NULL, y0 real NOT NULL, x1 real NOT NULL, y1 real NOT NULL,
  angle        real NOT NULL DEFAULT 0,
  method       text NOT NULL CHECK (method IN ('vector_text','ocr_line','ocr_scan')),
  confidence   real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  fact_status  text NOT NULL DEFAULT 'automatic'
                 CHECK (fact_status IN ('automatic','cross_confirmed','corrected','disputed')),
  valid        boolean NOT NULL,
  issues       text[] NOT NULL DEFAULT '{}' CHECK (cardinality(issues) <= 16),
  evidence_ids uuid[] NOT NULL CHECK (cardinality(evidence_ids) BETWEEN 1 AND 16),
  versions     jsonb NOT NULL CHECK (octet_length(versions::text) <= 4096),
  created_at   timestamptz NOT NULL DEFAULT now(),
  CHECK (x1 >= x0 AND y1 >= y0)
);
CREATE UNIQUE INDEX ux_drawings_identifiers_position
  ON electrical.drawings_identifiers(workspace_id, document_id, page_no, position);
CREATE INDEX ix_drawings_identifiers_kind
  ON electrical.drawings_identifiers(workspace_id, document_id, kind, valid, page_no, position);
-- INC-8 reads by key: a B-tree on the normalised key, never an expression (01 §2.6)
CREATE INDEX ix_drawings_identifiers_canonical
  ON electrical.drawings_identifiers(workspace_id, project_id, kind, canonical);

-- 7. relations between occurrences (فج-43, 45, 46, 47)
CREATE TABLE electrical.drawings_identifier_links (
  workspace_id uuid NOT NULL,
  document_id  uuid NOT NULL REFERENCES electrical.drawings_documents(id),
  page_no      integer NOT NULL CHECK (page_no >= 1),
  kind         text NOT NULL CHECK (kind IN ('stacked','owner','co_located','contact_of','group_member')),
  from_id      uuid NOT NULL REFERENCES electrical.drawings_identifiers(id) ON DELETE CASCADE,
  to_id        uuid NOT NULL REFERENCES electrical.drawings_identifiers(id) ON DELETE CASCADE,
  evidence_id  uuid NOT NULL REFERENCES electrical.drawings_evidence(id),
  PRIMARY KEY (workspace_id, kind, from_id, to_id)
);
CREATE INDEX ix_drawings_identifier_links_page
  ON electrical.drawings_identifier_links(workspace_id, document_id, page_no);

-- 8. legend candidates (FR-ING-14): proposals; the confirmed copy lives in the profile version
CREATE TABLE electrical.drawings_legend_entries (
  id           uuid PRIMARY KEY,                -- deterministic uuid5
  workspace_id uuid NOT NULL,
  document_id  uuid NOT NULL REFERENCES electrical.drawings_documents(id),
  page_no      integer NOT NULL CHECK (page_no >= 1),
  kind         text NOT NULL CHECK (kind IN ('ansi','abbreviation','device_name','symbol_name')),
  entry_key    text NOT NULL CHECK (char_length(entry_key) BETWEEN 1 AND 16),
  entry_text   text NOT NULL CHECK (char_length(entry_text) <= 128),
  suffixes     text[] NOT NULL DEFAULT '{}',
  conflict     boolean NOT NULL DEFAULT false,
  dropped      text NULL CHECK (dropped IS NULL OR char_length(dropped) <= 64),
  confidence   real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  evidence_id  uuid NOT NULL REFERENCES electrical.drawings_evidence(id),
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_drawings_legend_document
  ON electrical.drawings_legend_entries(workspace_id, document_id, kind, entry_key);

-- RLS: ENABLE + FORCE + tenant_isolation (NULLIF form) on every new table, as in 0001.
-- trg_touch on the two tables with updated_at (page_layouts, page_artifacts).
```

- **اسم القيد القديم** `drawings_profile_versions_body_check` هو الاسم الذي يولّده Postgres لقيد عمود غير مسمّى (`0001_electrical_drawings.py:262`)؛ لم أفحصه على قاعدة، ويثبّته اختبار الترحيل على القاعدة المؤقتة.
- **FK `ON DELETE CASCADE` في الروابط وحدها** (مستواها هو الظهور): الكنس يحذفها قبل الظهور على أي حال، والوثيقة لا تُحذف إلا عبر الكنس. وفي 0001 لا Cascade قط؛ هذا استثناء لأن الظهور يُستبدل في كل إعادة، وحذفه بسطر واحد يطول إن لم يُكنس (`INC2‑D‑8`).
- **`detail` و`versions` و`layout` و`refs` jsonb لا أعمدة**: حقول لا يُرشَّح عليها؛ ولا يخرج منها إلى طلب خارجي غير محدد الحجم.
- **الحذف بالترتيب** (كنس الوثيقة): `legend_entries` ← `identifier_links` ← `identifiers` ← `evidence` ← `page_artifacts` (صفوف فقط بعد الكائنات) ← `page_layouts` ← القائم (`page_stages` ← `pages` ← `characterizations` ← `jobs` ← `profile_versions` ← الوثيقة). `_SCHEMA_ORDER` (`src/app/ops/purge.py:393-401`) بالترتيب نفسه.
- **لا فهرس GiST** ولا امتداد (§3.3.4).
- **حجم مُقدَّر** (تقدير، لم يُقس): لصفحة 150 ظهوراً، و200 دليل، و30 رابطاً، وصفّ تخطيط. مشروع 5,000 صفحة ≈ 750 ألف ظهور (~0.9 KB مع الفهارس ≈ 700 MB)، و1 مليون دليل (~0.5 KB ≈ 500 MB)، وجدول المشتقات 8..20 صفاً للصفحة = 40..100 ألف. فالمجموع دون 2 GB لأكبر مشروع في NFR-SCAL-01، ويُقاس في INC‑11.
- **نمو الهجرة على بيانات حية:** إنشاء جداول وفهارس مولودة معها، و`ADD COLUMN` بلا افتراض حاسب، وتوسيع قيد `NOT VALID` ثم `VALIDATE`. لا قفل طويل. (اختبار `test_zero_downtime_migrations`.)

### 4.2 `electrical/0003_gold_labels` (`PR‑I`)

```sql
CREATE TABLE electrical.drawings_gold_labels (
  id           uuid PRIMARY KEY,
  workspace_id uuid NOT NULL,
  project_id   uuid NOT NULL,
  document_id  uuid NOT NULL REFERENCES electrical.drawings_documents(id),
  page_no      integer NOT NULL CHECK (page_no >= 1),
  kind         text NOT NULL CHECK (kind IN ('page_type','title_field','grid','identifier',
                 'stacked_label','legend_entry','text_line','page_language','known_discrepancy')),
  body         jsonb NOT NULL CHECK (octet_length(body::text) <= 4096),
  x0 real NULL, y0 real NULL, x1 real NULL, y1 real NULL,
  state        text NOT NULL DEFAULT 'draft' CHECK (state IN ('draft','reviewed')),
  created_by   uuid NULL, created_at timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(), version integer NOT NULL DEFAULT 1
);
CREATE INDEX ix_drawings_gold_labels_document
  ON electrical.drawings_gold_labels(workspace_id, document_id, kind, page_no, id);
CREATE TABLE electrical.drawings_gold_snapshots (
  id           uuid PRIMARY KEY, workspace_id uuid NOT NULL, project_id uuid NOT NULL,
  name         text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
  fingerprint  text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
  label_count  integer NOT NULL, documents jsonb NOT NULL CHECK (octet_length(documents::text) <= 65536),
  object_key   text NOT NULL, created_by uuid NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_drawings_gold_snapshots_project
  ON electrical.drawings_gold_snapshots(workspace_id, project_id, id DESC);
```

- التسميات الحية تتبع وثيقتها: تُكنس معها، ومعاينة الاستبدال القائمة تعدّها (`ReplacementPreviewOut`، يُضاف حقل عددها؛ ‏HLD §8.11). واللقطة المصدَّرة تبقى سجلاً بعد الوثيقة (✅ DEC‑18).
- ترتيب الكنس يبدأ بـ`drawings_gold_labels`. واللقطات ≤ 50 للمشروع، وتبقى بعد حذف ملفها. وحذف **الفضاء** يحذفها: كائناتها ثم صفوفها داخل `PurgeSpaceDrawings`، مثالياً.
- اللقطة: JSON حتمي مرتب، بصمتها SHA-256 على محتواها، ومعها بصمة المحتوى لكل وثيقة (لا اسمها). لا تُشغَّل إلا ما دام ملفها ببصمته موجوداً، ويذكر تقرير التقييم ما استُبعد.

### 4.3 التوافق والتراجع

| | الحكم |
|---|---|
| `0002` | توسيع: ستة جداول جديدة، وثلاثة أعمدة قابلة للفراغ، وتوسيع قيدين. الكود القديم لا يقرأها ولا يكتبها. **متوافق مع الخلف** |
| التراجع عن `0002` | يفشل صراحةً إن وُجدت قيم مرحلة جديدة في `page_stages` أو جسد ملف أكبر من 64 KiB، فتُحذف تلك الصفوف أولاً بقرار مشغّل؛ الجداول نفسها تُسقط. **البيانات المشتقة تُعاد من الملفات** (DATA-04) |
| `0003` | جدولان جديدان فقط. التراجع يحذف التسميات واللقطات (بيانات بشرية: تُصدَّر قبلها) |
| النشر المتدرج | `0002` قبل صورة `PR‑E`. وبعدها صورة `worker-electrical` الجديدة تتعامل مع `classify` القديم. والصورة القديمة لا تفهم `pages_completed` فتُقرّه بلا أثر لأن لا معالج له (سلوك المحرّك)؛ لذلك تُبنى `app` و`worker-electrical` معاً (`H5`) |
| جسد الملف الأكبر | لا يُكتب مدخل أسطورة إلا بعد `PR‑A` مفعّلاً في الأدوار كلها |

### 4.4 المستأجرون وRLS والمنح

- كل جدول جديد: `ENABLE` و`FORCE ROW LEVEL SECURITY` و`tenant_isolation` بصيغة `NULLIF`، وسطر في `_TENANT_TABLES`، و`SELECT, DELETE` لـ`workspace_purger` في `PURGE_GRANTS`. لا منح لـ`retention_sweeper` (بيانات مستخدم، DEC‑16).
- FK إلى `drawings_pages` وجد عمود `workspace_id` فيه، فمفتاحه مركّب. `drawings_identifiers(project_id, space_id)` قيم تُكتب من الوثيقة وتُقرأ بفهارس (workspace_id أولاً).
- **لا استعلام يعبر قسماً:** الجداول كلها `drawings_` ولا FK خارجها.

### 4.5 التخزين والحجم المؤقت

- كل كائن تحت `<workspace_id>/ele/<document_id>/…` و`<workspace_id>/ele-gold/…`. لا كائن بلا صفّ (§3.4).
- حجم تقديري لمشروع 500 صفحة بالمستويات الثلاثة: بين 0.4 و1.7 GB (مسبار 3.4 MB لصفحة ضجيج عشوائي قصوى؛ والصفحات الحقيقية أقل لأن أكثرها بياض). وبـ✅ `INC2‑Q‑4` (ب) لا يُخزَّن المستوى 200 dpi ما دام مطفأً، فالحجم الفعلي دون ذلك.
- حجم `ele-stage` يبقى نسخة واحدة في أي لحظة (§3.6.6)، وسقف الحجم قرص المضيف لا رقم جديد.

## 5. خطة الاختبار

### 5.1 معايير القبول

| المعرّف | المعيار | المصدر | الـPR |
|---|---|---|---|
| INC2‑AC‑1 | **النواة:** `-X1.14` و`=A1+P2-X1.14` طرفان صالحان مفتاحهما `X1:14`، و`-K5.A1` و`-X1.K5` و`-X1.14.2` لا تتغير؛ وحالة في `identifiers.toml`؛ والكميات بوحدتها الأصلية وSI؛ ونطاق `wire_sequence` يفك ويحدّ؛ والحدود القائمة في F2/F3 لا تتراجع، وM-04 = 1.0000 على الحالات | N‑2، DATA-05، فج‑24، FR-ID-09، FR-IDX-01 | A |
| INC2‑AC‑2 | **الجسد المخزَّن:** جسد من إصدار لاحق يُقرأ ويُعاد بلا خسارة المعروف، والعميل يُرفض بمفتاح مجهول؛ وتأكيد بلا جسد وقبول آلي لاقتراح ANSI فوق تسمية جهد مخزَّنة يعطيان `422` أو لا قبول، ولا خطأ عامل | F2 S‑2، خ‑14 | A |
| INC2‑AC‑3 | **البدائيات:** صفحة فيها خط ومستطيل ومنحنى ومغلق ومتقطع وطبقة وXObject ودوران وقص تخرج إحداثياتها بعد CTM والدوران والقص ضمن 0.01 pt، بسماكتها ولونها وطبقتها؛ وعضوية XObject والمحتوى المعلَّم تصح حين يساوي العدد، وتكون صفراً و`unavailable` وإلا؛ ومعرّف البدائية حتمي في تشغيلين | FR-VEC-01، 02، 07 | B |
| INC2‑AC‑4 | **النص المتجهي:** الحرف والكلمة والزاوية واللون، ومدوّر بزاوية 37° ومعكوس، وعمودي `wmode`، ومتراكب، ومعرّف مركّب لا يُقسم، وCID بلا ToUnicode منطقته `garbled`؛ والصفحة المشوّهة لا تدخل نصاً | FR-VEC-03، 04، 11، M-29 (الكشف) | B |
| INC2‑AC‑5 | **الروابط وإعادة الاستعمال والخطوط البديلة:** روابط صفحة ووجهات مسماة وإشارات مرجعية تخرج ولا تُفتح؛ ومجموعات متطابقة تُكتشف؛ وكل خط غير مضمَّن موسوم | FR-VEC-05، 06، NFR-I18N-04 | B |
| INC2‑AC‑6 | **العزل:** ملف `get_cdrawings()` الضخم يعطي `too_complex` قبل النداء، أو 507 وتفشل صفحته وحدها؛ والابن تحت `RLIMIT_AS` وبلا ملفات وبلا شبكة؛ والجواب الأكبر من السقف يُقتل ابنه؛ واختبار حاوية اختياري يثبت النموذج للقراءة فقط | F0 S‑2، NFR-SEC-02 | B، C |
| INC2‑AC‑7 | **التصيير:** ثلاثة مستويات مربعات 1,024، وحدود البكسل تُحترم (64 MP)، والروابط قصيرة وموقّعة وللمستأجر نفسه، وجواب JSON برأس `no-store` | FR-VEC-09، FR-API-04 | B، E |
| INC2‑AC‑8 | **عزل السطور:** على سطور ضربات أفقية و90° وملاصقة لدائرة وفقاعة وعمود أرقام ومتقطعة: السطور الصحيحة بصناديقها وزواياها وبدائياتها، ولا `Q` من الدائرة ولا سطر عمودي وهمي، والمتقطع ليس نصاً؛ والمحرك مزيّف في الوحدة وحقيقي بعلامة `live_ocr` | FR-VEC-08، FR-VEC-10 | C |
| INC2‑AC‑9 | **عقد السطر:** لكل سطر صندوقه وزاويته وثقة كل حرف والبديل الثاني، وبلا ثقة لا تصحيح؛ والطريقة «سطر معزول» أو «مسح» على النص والدليل، وكل بدائية في سطر معلَّمة نصاً؛ وثقة المسح أدنى من السطر المعزول المماثل | DEC‑19، DEC‑20، DATA-01 | C، D |
| INC2‑AC‑10 | **المسح:** يُمسح ما لم يُغطَّ وكتلة العنوان فقط، مُعطّل افتراضياً، ويحترم الميزانيات، وبلا مسح لا صفحة محبوسة (وفاشل المسح يصل `needs_review`)؛ والتكرار بين المربعات يزال، والسطر المقطوع يُدمج | FR-RAS-01، F0 S‑4 | F |
| INC2‑AC‑11 | **خط الصفحات:** التوزيع والعمل والتجميع والاكتمال مرة واحدة حتى مع تسليم مزدوج ودفعتين متزامنتين؛ والإلغاء والاستئناف والتطهير عند حد الصفحة؛ وفشل صفحة لا يُفشل المهمة، و`upstream_failed` لتابعيها، والعقد يمتد بالتقدم | FR-ING-04، 05، 10، NFR-REL-01، NFR-SCAL-05 | E |
| INC2‑AC‑12 | **الطوبولوجيا:** كل مجموعة لها مستهلك في الزيادة نفسها، ومعالجات العمّال تطابق الأنواع، ولا نوع بلا JSON Schema ولا `null` ولا نص رسم | `PLT‑11`، RSK‑14 | E، F |
| INC2‑AC‑13 | **المشتقات:** كل كائن له صفّ قبله، والكنس يحذف الكائنات قبل الصفوف، وتطهير الوثيقة لا يترك صفاً ولا كائناً، والبادئات مساحة عمل؛ ورفع الاستبدال يمحو المشتقات | PLT‑04، §3.4 | D، E |
| INC2‑AC‑14 | **الدليل:** كل ظهور مرتبط بدليل، وDATA-01 كاملة الحقول (الصفحة، الموقع، الإحداثيات، المراجع، الطريقة، الإصدار، الثقة، الزمن)؛ والحالة «آلي» | P-03، DATA-01، DATA-02 | D، H |
| INC2‑AC‑15 | **نوع الصفحة:** مطابقة على صفحات اصطناعية لكل نوع بالإنجليزية والصينية المبسطة، والثقة، و`OTHER` لعدم المعرفة؛ وM-01 مفصّل | FR-LAY-01، M-01 | G، I |
| INC2‑AC‑16 | **كتلة العنوان والإطار:** إطاران بأبعاد مختلفة وكتلتا عنوان في ركنين وكتلة بلا إطار؛ والحقول كما طُبعت، و`revision` حقل وصفي؛ وM-02 مفصّل | FR-LAY-02، M-02 | G، I |
| INC2‑AC‑17 | **الشبكة والمناطق والورقة:** عمود وصف لأي إحداثية، والمناطق لا تختلط، والجدول المختصر `truncated`، ورقم الورقة ظاهري ↔ فعلي ذهاباً وإياباً والتكرار موسوم؛ وM-03 مفصّل | FR-LAY-03، 04، 06، M-03، فج‑44، فج‑47، فج‑50 | G، I |
| INC2‑AC‑18 | **الأسطورة:** F2‑AC‑32..36 كلها، والتأكيد ينتج إصداراً ومهمة، والمرفوض لا يُطبَّق، والتعارض لا يُطبَّق، والحدود، و`.*` نص حرفي، و403 لـmember | FR-ING-14، فج‑28 | G |
| INC2‑AC‑19 | **المعرّف الكامل:** ورقة بصناديق بنية متداخلة وبنية من كتلة العنوان وتعارض لا يُختار؛ والطرف بجهازه؛ وM-05 مفصّل | FR-ID-02، M-05، فج‑46، فج‑31 | H، I |
| INC2‑AC‑20 | **سياق ANSI:** F2‑AC‑29 وحالات الفقاعة والجدول، ورقم مجرد بلا سياق يبقى رقم سلك بالبديل، ورقم بلا فئة في جدول أسطورة يُقرأ بالتلميح | FR-ID-16، فج‑42 | H |
| INC2‑AC‑21 | **التسمية المكدّسة:** F2‑AC‑27..30، وM-36 مفصّل | FR-ID-17، M-36، فج‑43 | H، I |
| INC2‑AC‑22 | **التصحيح معطّلاً وثقة كل حرف:** F2‑AC‑23..25؛ وM-30 بلا تصحيح وبالتصحيح لا يخفي خطأ | FR-ID-18 | H، I |
| INC2‑AC‑23 | **الحقائق والتعارض:** فج‑11 و12 و13 و20 و36 و45، وM‑3 | FR-ID-19، 20 | H |
| INC2‑AC‑24 | **الواجهات:** المسارات كلها بأخطائها وصلاحياتها، والعزل بين مستأجرين (404 لا 403 ولا تسريب)، ومن لا يملك `electrical:manage` لا يؤكد أسطورة، وحدود المدخلات (المعرّفات، ونصف القطر، وعدد النتائج) مفروضة، ونتائج «قرب موقع» مرتبة حتمية، ومواضع `grid` صحيحة | FR-API-04، FR-IDX-03، FR-API-08، FR-API-09 | E، G |
| INC2‑AC‑25 | **التسميات الذهبية:** CRUD بصلاحية `electrical:eval`، وتتبع الوثيقة عند الكنس والمعاينة، واللقطة حتمية بصمتها وترفض ما استُبعد | FR-EVAL-02، DEC‑18 | I |
| INC2‑AC‑26 | **السجلات والأسرار:** لا نص من الرسم في سجل، ولا سرّ ولا DSN في بيئة `ele-parse`؛ وحقول النص الوصفية في الجواب مُهرَّبة | NFR-SEC، خصوصية | E |
| INC2‑AC‑27 | **القياس:** M-30 مفصّل حسب الطريقة والاصطلاح وبلا صيغة خاطئة مخفية؛ وM-36؛ وM-32 بالحقلين أو «مؤجّل» بلا رقم؛ وتقرير `F1‑MS‑1` بالأعداد فقط | F2‑AC‑38..40، M-30، M-36 | I |
| INC2‑AC‑28 | **الأداء:** متوسط صفحة متجهية ≤ 3 ث، ومشروع 500 صفحة ≤ 25 دقيقة على المضيف **وفيه صفحات OCR** (SRS D-35)، وصفحة يُسترجع نصها ≤ 15 ث في المتوسط، وذروة ذاكرة الابن ضمن السقف؛ بأداة `electrical_perf`، ويذكر التقرير الحجم المقيس. **تقدير التصميم لوثيقة كلها OCR بالسقوف المُقَرّة نحو 33 دقيقة (`INC2‑R‑3`)، فالمعيار لا يُفترض محققاً.** إسقاط `G2` يُذكر، وما تجاوز 25 دقيقة فيه أو في القياس النهائي يعود إلى المالك بالبديل الجاهز، ولا يُقبل المعيار صامتاً | NFR-PERF-01، 05 | إسقاط في `G2`، والقياس في I |
| INC2‑AC‑29 | **الوثائق الحاكمة:** كل تغيير مطابق في `01`..`09` و`architecture` و`openapi` واختبار التطابق؛ و9 عقود محفوظة، بلا `ignore_imports` | SRS AC-07، AC-09 | كل PR |

### 5.2 الاختبارات لكل معيار

| المعيار | المستوى | الموضع |
|---|---|---|
| AC‑1، AC‑2 | وحدة، وحالات M-04 | BT1، BT2 |
| AC‑3..5 | وحدة على PDF تولَّد، وحتمية | BT3 |
| AC‑6 | وحدة، وعُدّة حاوية اختيارية | BT3، OT2 |
| AC‑7 | وحدة + تكامل حيّ (MinIO) | BT3، BT11، BT10 |
| AC‑8، 9 | وحدة (محرك مزيّف) و`live_ocr` | BT5 |
| AC‑10 | وحدة نقيّة (خطة، ميزانية) + تكامل | BT12، BT11 |
| AC‑11 | وحدة (مستودعات مزيّفة) + تكامل حي، وسباق دفعتين | BT9، BT11 |
| AC‑12 | وحدة | BT10 |
| AC‑13 | RLS/قيود حية، وكنس حيّ | ET1، ET2 |
| AC‑14 | وحدة + مستودع حيّ | BT8، ET1 |
| AC‑15..18 | وحدة على صفحات اصطناعية | BT13 |
| AC‑19..23 | وحدة | BT14 |
| AC‑24 | واجهة (عميل اختبار) + `BT10` للصلاحية | BT10 |
| AC‑25 | وحدة + RLS + API | BT15، ET1 |
| AC‑26 | وحدة (سجل فارغ من نص) | BT9، BT10 |
| AC‑27، 28 | أدوات خارج CI، واختبارات وحدة لحسابها | BT15، BT6 |
| AC‑29 | `test_import_contracts.py` و`test_openapi` | القائم |

**اختبارات RLS** (كلها في ET1): لكل جدول جديد: مستأجر لا يرى صفوف آخر، وإدراج بغير سياق مساحة عمل يُرفض، و`FORCE` على المالك؛ ولكل قيد فريد قيمة مقبولة ومرفوضة؛ وقراءة الكنس من جدول في الترتيب الخاطئ تُفشل FK.

**ما يحتاج عيّنات خارج المستودع** (لا CI): تشغيل `F1‑MS‑1`، وM-30/M-36، وM-02/M-03 على صفحات حقيقية، وقياس NFR. كلها بأدوات محلية خارج الشجرة، والمستودع يحمل حساب المقياس على حالات اصطناعية فقط (SRS D-23، D-34).

### 5.3 ما لا يُشغَّل محلياً دون موافقة

- `tests/integration/` (تكامل حي على المكدّس المستعمل): بطلب المالك أو CI.
- `live_ocr` يحتاج النموذج؛ لا يدخل المستودع، ويتخطى حين لا نموذج.
- `F1‑MS‑1`: على المضيف بحاوية مؤقتة `--network none --read-only --cap-drop ALL` (`F1‑MS‑1` §4.6).

### 5.4 اختبار الحتمية

تشغيلان على ملف PDF اصطناعي وعلى صفحات السطور بالمحرك المثبّت: حزم بدائيات متطابقة بايتاً، وسطور متطابقة، وظهورات بمعرّفات متساوية، وأدلة متساوية. وفشل `live_ocr` بغياب النموذج لا يفشل CI، لكن `F1‑MS‑1` يثبّته على المضيف (RSK‑25).

### 5.5 القياس (`F1‑MS‑1` وM-*)

**`F1‑MS‑1` (بوابة `G2`)** كما في `../inc-1/f1/design.md` §4.6، و§13.7 من HLD. الأداة `tests/eval/electrical/f1_ms1.py`:
- مدخلها: مجلد PDF محجوز، وحقيقة مرجعية (JSON، خارج المستودع)، وقائمة مرشحين (نموذج ومساره وبصمته وجيل)؛ مخرجاتها JSON بلا نص.
- تشغّل `ele-parse extract` من الحاوية لكل مرشح، وخطوط (V1/F المقصور)، ثم تحسب: تطابق المعرّفات تام (M-30)، الصيغ الخاطئة، دقة أحرف السطور (حيث أُرفق نص سطر)، تغطية الضربات، زمن الصفحة (متوسط، p95) بعدد خيوط مثبّت {1، 2، 4}، وأقصى RSS للابن، وتفصيل لكل نوع صفحة وطريقة (FR-EVAL-13).
- **العتبات** (من F1): لا تُختار فئة أحدث من v4 إلا إن لم ينقص تطابقها ولم تزد صيغها الخاطئة على v4 في المحجوزة؛ وعند التقارب الأصغر والأسرع؛ ومتوسط الصفحة ≤ 15 ث؛ وذروة الذاكرة ضمن سقوف `INC2‑Q‑2`؛ ويُفعَّل المسح (`scan_uncovered` و`scan_title_block` كلٌّ وحده) افتراضياً فقط إن زاد التطابق دون زيادة صيغ خاطئة على V1.
- **إسقاط زمن المشروع (يُذكر ويحكم عودة القرار):** `T500 = 500 × (متوسط استخراج الصفحة + متوسط التعرّف + متوسط تصيير 50 و100 dpi + متوسط المسح إن فُعّل) ÷ عدد الأبناء`، ويُقاس معه زمن فعلي لدفعة واحدة على الأقل بابنين معاً، لأن التوازي لا يضمن نصف الزمن. إن زاد `T500` على 25 دقيقة عاد `INC2‑Q‑2` إلى المالك قبل `PR‑F`. والحدّ المكافئ عند ابنين: متوسط الصفحة 6 ث.
- **شروط البوابة:** (1) مجموعة محجوزة من 20 صفحة وألف معرّف على الأقل، تُسمّى وتُحسب بصمتها، ولا صفحة منها في مجموعة الضبط ولا مكررة عنها (✅ `INC2‑Q‑1` (أ))؛ (2) تفسير نمط `X` (F1‑R‑10) أو قبول المالك بقاءه؛ (3) `T500` مذكور.
- ملخص الأعداد في `docs/delivery/electrical-drawings/inc-2/f1-ms-1.md` بلا نص ولا أسماء؛ ومعه إصدار الحزمة وONNX Runtime، وبصمات النماذج، وعدد الخيوط، وطراز المعالج (لا أرقام P1 المجهولة).

| المقياس | الأداة | الحقيقة المطلوبة | هدف R1 (SRS) |
|---|---|---|---|
| M-01 | `m01_page_type` | نوع كل صفحة | ≥ 0.98 |
| M-02 | `m02_title_block` | حقول كتلة العنوان كما طُبعت | ≥ 0.98 |
| M-03 | `m03_grid` | (إحداثية ← عمود/صف) على 50 نقطة لكل صفحة | ≥ 0.99 |
| M-04 | `m04_identifiers` على ظهورات STG‑05 من طبقة نص متجهية، بلا تصحيح | معرّف ونوع واصطلاح | ≥ 0.995 |
| M-05 | `m05_full_identifier` | المعرّف الكامل | ≥ 0.98 |
| M-29 (المتجهي) | `m29_vector_zh` | نص صيني متجهي + مناطق مشوّهة | أحرف ≥ 0.995؛ كشف ≥ 0.95 |
| M-30 | `m30_recovery` | معرّفات ونص سطور، مفصّل حسب الطريقة والاصطلاح | ≥ 0.97؛ صيغ خاطئة ≤ 0.005؛ أحرف ≥ 0.95 |
| M-36 | `m36_stacked` | أزواج الأجزاء | ≥ 0.97؛ خطأ ≤ 0.005 |
| M-32 | `m32_profile_detection` بالحقلين | تسميات اصطلاح (F2‑AC‑40) | «مؤجّل» بلا رقم قبل التسميات |
| F2‑AC‑26، F2‑AC‑37، F2‑AC‑38 | كما فوق | الأسطورة ≥ 0.95 | — |
| NFR-PERF-01، 05 | `electrical_perf` من أزمنة الصفحات المسجَّلة | — | 3 ث/صفحة؛ 25 دقيقة/500؛ 15 ث للمستردّة |

M-04 هنا على الظهورات الحقيقية من STG‑05، لا على محلّل الحالات وحده (ذاك يبقى وحدة). وعند غياب حقيقة أو سمة يقول التقرير «غير مقيس» ولا يخترع رقماً.

### 5.6 تتبّع صفّ INC‑2 في HLD §16

| المعرّف | المعيار | الـPR |
|---|---|---|
| FR-VEC-01، 02، 07 | INC2‑AC‑3 | B |
| FR-VEC-03، 04، 11 (الكشف) | INC2‑AC‑4 | B |
| FR-VEC-05، 06 | INC2‑AC‑5 | B |
| FR-VEC-08، 10؛ DEC‑19؛ DEC‑20 (عقد السطر) | INC2‑AC‑8، 9 | C، F |
| FR-VEC-09؛ FR-API-04 | INC2‑AC‑7، 24 | B، E |
| FR-RAS-01 (النص المرسوم)، STG‑03 | INC2‑AC‑10 | C، F |
| FR-LAY-01 · 02 · 03 · 04 · 06 | INC2‑AC‑15 · 16 · 17 | G |
| FR-ID-02 | INC2‑AC‑19 | H |
| FR-ID-15، 16 (السياق) | INC2‑AC‑20 | H |
| FR-ID-17 | INC2‑AC‑21 | H |
| FR-ID-18 | INC2‑AC‑22 | H |
| FR-ID-19، 20 (السياق) | INC2‑AC‑1، 23 | A، H |
| FR-ING-14 | INC2‑AC‑18 | G |
| FR-IDX-03 | INC2‑AC‑24 | E |
| FR-EVAL-02 | INC2‑AC‑25 | I |
| NFR-I18N-03، DATA-06 | INC2‑AC‑4، 14، 22 | B، H |
| NFR-I18N-04 | INC2‑AC‑5 | B |
| DATA-01، 02 | INC2‑AC‑9، 14 | D، H |
| DATA-03 (لا تعديل لملف) | INC2‑AC‑13 (المشتق منفصل عن الأصل) | D، E |
| DATA-04 | INC2‑AC‑13، 14 (إصدار على كل حقيقة، وإعادة الاشتقاق) | D، E |
| DATA-05 | INC2‑AC‑1 | A |
| `PLT‑11` | INC2‑AC‑12 | E، F |
| `PLT‑17` | INC2‑AC‑6، و`G1` و`G2` | C، F |
| `F1‑MS‑1` | INC2‑AC‑27، و`G2` | C (الأداة)، قبل F |
| M-01 · M-02 · M-03 | INC2‑AC‑15 · 16 · 17 | I |
| M-04 · M-05 | INC2‑AC‑1 و27 · INC2‑AC‑19 | A، I |
| M-29 (المتجهي) | INC2‑AC‑4، 27 | I |
| M-30 · M-36 | INC2‑AC‑22، 27 · INC2‑AC‑21، 27 | I |
| NFR-PERF-01، 05 | INC2‑AC‑28 | I |
| N‑2، فج‑24، الفجوات الثماني عشرة | INC2‑AC‑1، 17، 18، 20..23 | A، G، H |

## 6. ترتيب التنفيذ

### 6.1 طلبات الدمج

كل PR من `master` بعد دمج ما قبله، ويُدمج وحده بالبوابات الست خضراء، وبدورة `/company:deliver` الكاملة (جودة، ومراجعة، وأمن، وقبول). والأسطر تقدير خام للمصدر والاختبار معاً.

| الـPR | العنوان | يعتمد على | ما يصل المستخدم | التقدير (أسطر · أيام) |
|---|---|---|---|---|
| `PR‑A` | نواة `shared` 1.5.0 والقارئ المتسامح | — | لا شيء ظاهر؛ ورجوع آمن لما بعده | 1,800 · 3..4 |
| `PR‑B` | `ele-parse` 2.0: الاستخراج المتجهي والتصيير | — | لا شيء: لا عامل يناديه بعد | 3,200 · 5..6 |
| `PR‑C` | `ele-parse`: السطور والتعرّف والمسح، وأداة `F1‑MS‑1` | `PR‑B`، و`G1` قبل الدمج | لا شيء: لا نموذج في الصورة | 3,000 · 5..6 |
| `PR‑D` | `electrical/0002`، والنطاق المشترك، والمنافذ، والمستودعات | `PR‑A` | لا شيء | 3,000 · 4..5 |
| `PR‑E` | خط الصفحات (`cg.electrical.pages`) والعرض والبحث المكاني | `PR‑B`، `PR‑D` | صفحات النص المتجهي: نص وموضع وصور وتظليل. وصفحات النص المرسوم: صورة وهندسة بلا نص | 4,500 · 7..9 |
| `PR‑F` | تثبيت النموذج، وتفعيل السطور، ودور `ocr` والمسح | `PR‑C`، `PR‑E`، و**`G2`** | نص صفحات النص المرسوم خطوطاً (المورد الأول) | 2,200 · 4..5 |
| `PR‑G` | STG‑04 والأسطورة | `PR‑E` | نوع الصفحة، وكتلة العنوان، والشبكة، والأسطورة | 4,000 · 6..8 |
| `PR‑H` | STG‑05 | `PR‑G` | معرّفات كل صفحة لها نص، بدليلها. وقبل `PR‑F`: صفحات النص المتجهي وحدها | 3,800 · 6..8 |
| `PR‑I` | التسميات الذهبية والتقييم والأداء | `PR‑H`، `PR‑F` | تقارير M-01..M-05 وM-29 وM-30 وM-36 وM-32 | 3,000 · 4..6 |

**ما يجري معاً** (بقرار المنسّق، وكل الكتّاب في مستودع واحد فلا يحرّران ملفاً واحداً):
- `PR‑A` مع `PR‑B`: لا ملف مشترك.
- `PR‑C` مع `PR‑D`: لا ملف مشترك (`services/` و`tests/eval` مقابل `src/` و`migrations/`).
- وسم المحجوزة (`H3`) مع كل ما قبل `G2`.
- `PR‑G` لا ينتظر `PR‑F`، لأن STG‑04 يعمل على النص المتجهي.

**المجموع:** نحو 28,500 سطر، و45..57 يوم عمل وكيل. أكبر من تقدير F2 (7..10 أيام للبنود 2..4)، لأن ذاك لا يعدّ الاستخراج والعرض (`INC2‑R‑1`).

### 6.2 البوابات داخل البناء

| البوابة | متى | الشرط | من |
|---|---|---|---|
| **`G1`** مراجعة سلسلة التوريد | قبل دمج `PR‑C` | `inc-2/notices.md` كامل، وكل مكتبة مضمَّنة مذكورة بترخيصها، و`scan.sh` PASS، ولا شيء خارج NFR-LIC-01 إلا بقرار مالك في SRS. وتشمل OpenCV في صورة `app` (✅ `INC2‑Q‑3` (أ)) | المالك |
| **`G2`** قياس القبول `F1‑MS‑1` | بعد `PR‑C` وقبل `PR‑F` | §5.5: المجموعة المحجوزة مسمّاة ومبصومة، والتقرير كامل، ونمط `X` مفسَّر أو مقبول، وسقوف الذاكرة والزمن مقيسة، **وإسقاط زمن مشروع 500 صفحة `T500` مذكور. إن زاد على 25 دقيقة (NFR-PERF-01، M) عاد قرار السقوف إلى المالك قبل `PR‑F`، ومعه البديل الجاهز (3 أبناء، 4 نوى، 4 GiB)** | المالك يقبل التقرير؛ ومعه ترخيص النموذج |
| **`G3`** أول نتيجة مفيدة | بعد `PR‑F` **و**`PR‑H`، وبعد `H5` (أ) و(ب) ثم `H6` | تشغيل على عيّنات المالك على المضيف بقرار بشري (`H6`)، وعرض المعرّفات على الصفحة. لا يمنع `PR‑I` | المالك |

**لماذا `G3` بعد `PR‑F` أيضاً.** نص المورد الأول مرسوم خطوطاً في 74 صفحة من 76 (SRS D-35)، و`PR‑F` هو الذي يقرؤه. فبعد `PR‑H` بلا `PR‑F` لا تظهر معرّفات على تلك الصفحات.

**ما يُرى قبل `PR‑F`:**
- صفحات النص المتجهي (المورد الثاني، وصفحتان من المورد الأول): النص والصورة والتظليل بعد `PR‑E`، ثم نوع الصفحة وكتلة العنوان والشبكة بعد `PR‑G`، ثم المعرّفات بعد `PR‑H`.
- صفحات النص المرسوم: الصورة والبدائيات والإطار وحدود كتلة العنوان هندسياً. بلا نص ولا حقول ولا معرّفات، و`extract = needs_review (recognizer_unavailable)`.
- عرض ذلك على المالك معاينة جزئية، لا `G3`.

**إن لم يُجتز `G2`:** لا `PR‑F`، فلا `G3` ولا `H6` حتى قرار المالك. يبقى `recognizer: null`، وصفحات النص المرسوم `needs_review (recognizer_unavailable)`، ولا يتوقف `PR‑G` ولا `PR‑H` على النص المتجهي. ويعود المالك إلى F1 §11.1 بقرار جديد.

### 6.3 الخطوات

| الخطوة | المنفّذ | الـPR | المحتوى | تُغلق بـ |
|---|---|---|---|---|
| S0 | منسّق | — | ✅ قرارات §7.1 (2026-11-25). يبقى الفرع `feat/electrical-drawings-inc-2-a` من `master` | — |
| S1 | `backend-developer` | A | B1..B10 (جزء A)، BT1، BT2 | البوابات 1..5 |
| S2 | `backend-developer` | B | B11..B19، BT3، BT4 | البوابات 1..5 |
| S3 | `backend-developer` | C | B20..B25، BT5، BT6 | البوابات 1..5 |
| S4 | `devops-engineer` | C | O1..O6 (جزء C)، OT1..OT3، وملف `G1` | البوابات 1..6 و`scan.sh` |
| S5 | `backend-developer` | D | B26..B29، BT7، BT8 | البوابات 1..5 |
| S6 | `data-engineer` | D | E1، E3..E7 (جزء D)، ET1..ET3؛ والتحقق على `postgres:16` مؤقتة: `provision` ثم تراجع ثم إعادة | البوابات 1..5، وET1 على القاعدة المؤقتة |
| S7 | `backend-developer` | E | B30..B44 (جزء E)، BT9..BT11، BD1..BD6 | البوابات 1..5 |
| S8 | `devops-engineer` | E | O5..O9 (جزء E)، OT1 | البوابات 1..6 |
| S9 | بشري ومنسّق | — | `G2` | تقرير في `inc-2/f1-ms-1.md` |
| S10 | `devops-engineer` ثم `backend-developer` | F | O2، O5، O6، O8 (النموذج والسقوف)؛ ثم B45..B47 وجزء F من B36..B40 وB42، BT12، BD3 وBD6 | البوابات 1..6 |
| S11 | `backend-developer` | G | B47 (جزء G)، B48..B50، جزء G من B9 وB41 وB42، BT13، BD2 وBD5 | البوابات 1..5 |
| S12 | `backend-developer` | H | B47 (جزء H)، B51، B52، جزء H من B9 وB42، BT14، BD5 | البوابات 1..5 |
| S13 | `data-engineer` | I | E2، وجزء I من E3 وE5 وE6 وE7، ET1، ET2 | البوابات 1..5 |
| S14 | `backend-developer` | I | B53..B56، BT15، BD1..BD7 (جزء I)، BD8، BD9 | البوابات 1..6 |

كل خطوة تُغلق بالبوابات (`CLAUDE.md` «Quality gates»)، ومنها «Contracts: 9 kept, 0 broken.». ويشغّل `qa-engineer` دورته على كل PR.

## 7. المخاطر والقرارات

### 7.1 قرارات ⚠️ احتاجت المالك (حُسمت)

**الحسم (المالك، 2026-11-25):** التوصية في القرارات الستة. ولا قرار يحجب البناء بعد الحسم.

| القرار | الموضوع | التوصية | الحسم | أثره |
|---|---|---|---|---|
| `INC2‑Q‑1` | المجموعة المحجوزة لـ`F1‑MS‑1` | (أ) وسم 14 صفحة أخرى | ✅ (أ) | `H3`: 14 صفحة على الأقل ونص 200 سطر بمواضعها، قبل `G2` |
| `INC2‑Q‑2` | سقوف `ele-parse` على المضيف | (أ) الأرقام في §3.7.6 | ✅ (أ)، **مؤقتاً حتى `G2`** | ابنان، و3.0 نوى، و3,072 MiB، و192 عملية، و`RLIMIT_AS` 2,560 MiB للتعرّف، و45 ث للصفحة. وإن أظهر `G2` تجاوز 25 دقيقة عاد القرار بالبديل الجاهز: 3 أبناء، و4 نوى، و4 GiB |
| `INC2‑Q‑3` | نطاق مراجعة الثنائيات، ومتى تُقبل | (أ) مراجعة العجلتين، وOpenCV في صورة `app` معها | ✅ (أ) | `G1` قبل دمج `PR‑C` |
| `INC2‑Q‑4` | مستويات التصيير والتخزين | (ب) 50 و100 dpi في المهمة، و200 dpi خلفية اختيارية معطّلة | ✅ (ب) | `ELE_RENDER_HD=false` |
| `INC2‑Q‑5` | ميزانية OCR للوثيقة ولمساحة العمل | (أ) 1,000 و10,000 كل 24 ساعة | ✅ (أ) | §3.6.5 و§3.7.3 |
| `INC2‑Q‑6` | تقوية الابن: لا تفريغ ذاكرة | (أ) `RLIMIT_CORE=0` و`PR_SET_DUMPABLE=0` | ✅ (أ) | `PR‑C`، مع اختباره |

النص أدناه سجلّ كما عُرض على المالك، والحسم في رأس كل قرار. وأُضيف إلى `INC2‑Q‑2` ما أظهرته مراجعة 0.2، وصُحّح عدد الصفحات غير الموسومة في `INC2‑Q‑1`.

**✅ INC2‑Q‑1: المجموعة المحجوزة لـ`F1‑MS‑1`. الحسم: (أ)، التوصية (المالك، 2026-11-25).**
- **المشكلة.** HLD §13.7 يطلب 20 صفحة محجوزة على الأقل وألف معرّف من المورد نفسه. والموجود خارج المستودع: 9 صفحات موسومة من المورد الأول، منها 3 للضبط. فالمحجوزة اليوم **6 صفحات ونحو 396 نصاً مميزاً شبيهاً بالمعرّف** (عددتُها من مفاتيح الوسم، بلا محتوى). وليس في الوسم نص السطور بمواضعها، فلا تُقاس دقة الأحرف ولا صيغة المعرّف حسب الموضع. وفي ملفَي المورد الإضافيين 39 صفحة (`../inc-1/f3/measure-2.md` §1)، منها 6 موسومة، وبعضها يكرر صفحات الضبط فيُستبعد من المحجوزة. فالمتاح للوسم نحو 30 صفحة.
- **الخيارات:** (أ) وسم 14 صفحة أخرى على الأقل من الملفات الموجودة، ومعها نص 200 سطر بمواضعها في صفحتين، يراجعها المالك كما راجع السابقة. (ب) تشغيل `G2` على 6 صفحات بحكم مؤقت، وتثبيت النموذج مؤقتاً، وإعادة القياس عند توفر الـ20. (ج) تخفيض الحدّ في HLD إلى ما يُتاح.
- **التوصية: (أ).** نموذج يُخبز في صورة بقياس لا يبلغ الحدّ المقرر قرارٌ هشّ، ويصير بعدها كل إصدار نموذج جديد قياساً جديداً على قاعدة ضعيفة. والبناء لا يتوقف أثناءه: `PR‑A` إلى `PR‑E` و`PR‑G` لا تعتمد عليه.
- **الكلفة:** نحو 1..2 يوم وسم للمنسّق ومراجعة المالك.

**✅ INC2‑Q‑2: سقوف `ele-parse` (أداء وأمن). الحسم: (أ)، التوصية، مؤقتاً حتى `G2` (المالك، 2026-11-25).**
- **المشكلة.** التعرّف يحتاج خيوطاً وذاكرة أكثر من السقوف الحالية (1 نواة، 2,560 MiB، 64 عملية، `RLIMIT_AS` 1,024 MiB؛ RSK‑24). وموازنة المضيف ضيقة (نحو 8 GiB متاحة).
- **الخيارات:** (أ) §3.7.6: 3.0 نوى، و3,072 MiB، و192 عملية، وابنان، و`RLIMIT_AS` 2,560 MiB للتعرّف، ومهلة 45 ث للصفحة. (ب) ابن واحد بخيطين: 2 نوى و2,048 MiB؛ يضاعف الزمن، فيقدَّر بنحو 67 دقيقة لـ500 صفحة كلها OCR. (ج) إبقاء السقوف وقبول فشل الصفحات الكثيفة.
- **ما تعد به (أ) وما لا تعد به.** (أ) نفسها مقدَّرة بنحو **33 دقيقة** لوثيقة من 500 صفحة كلها OCR (`INC2‑R‑3`)، أي فوق حدّ NFR-PERF-01 (25 دقيقة، أولوية M، ويشمل صفحات OCR بـSRS D-35). فليس سبب رفض (ب) أنها وحدها تفوّت الحدّ، بل أنها تفوّته بضعف ما تفوّته (أ). والحدّ يبلغه ابنان حين يكون متوسط الصفحة ≤ 6 ث، وتقدير البديل الجاهز (3 أبناء) نحو 22 دقيقة. والأرقام كلها تقدير لم يُقس، وأزمنة P1 مجهولة الخيوط والمعالج.
- **التوصية: (أ)**، والأرقام مؤقتة حتى `G2` (العتبة 4 من `F1‑MS‑1`). وإن أظهر `G2` أن إسقاط المشروع يتجاوز 25 دقيقة عاد القرار إلى المالك، والبديل الجاهز: 3 أبناء، و4 نوى، و4 GiB. والملف `electrical` يبقى معطّلاً افتراضياً، ويُفعَّل عند العمل عليه فقط، كما اليوم.

**✅ INC2‑Q‑3: مراجعة ثنائيات ONNX Runtime وOpenCV (تراخيص وسلسلة توريد). الحسم: (أ)، التوصية (المالك، 2026-11-25).**
- **المشكلة.** قرّر المالك في F1 القرار 5 أن تُراجع الإشعارات عند البناء، وما خرج عن NFR-LIC-01 يعود إليه. ولم يقرَّر نطاق المراجعة: هل تشمل عجلة OpenCV في صورة `app` القائمة (`pyproject.toml:145-150`)، ومتى يتم القبول.
- **الخيارات:** (أ) مراجعة العجلتين في `ele-parse` ومعهما OpenCV في `app`، بتقرير `inc-2/notices.md`، وقبول المالك قبل دمج `PR‑C`. (ب) مراجعة `ele-parse` وحدها، و`app` في «جاهزية الخادم». (ج) القبول بعد الدمج.
- **التوصية: (أ).** المراجعة واحدة، وصورة `app` تحمل المكتبات نفسها اليوم.
- **سؤال فرعي تجيب عنه البوابة لا المعماري:** إن وُجدت مكتبة خارج القائمة (مثل Intel MKL في عجلة ONNX، لم أتحقق منها)، فالبدائل: قرار SRS جديد، أو العجلة بلا تلك المكتبة، أو بديل F1 (ب) بلا OpenCV.

**✅ INC2‑Q‑4: مستويات التصيير (أداء وتخزين). الحسم: (ب)، التوصية (المالك، 2026-11-25).**
- **المشكلة.** مسبار على صفحة كثيفة: 100 dpi أخذت 1.5 ث، و200 dpi أخذت 5.6 ث (نواة واحدة). فثلاثة مستويات لـ500 صفحة كثيفة نحو 59 دقيقة CPU للتصيير وحده، أي نحو 30 دقيقة على ابنين. وهذا وحده فوق حدّ NFR-PERF-01 (25 دقيقة). والتخزين في أسوأ حالة مقيسة 3.4 MB للصفحة.
- **الخيارات:** (أ) ثلاثة مستويات في المهمة، وتُزاد الموارد حتى تبلغ الحدّ. (ب) 50 و100 dpi في المهمة (≈ 1.5 ث للصفحة)، و200 dpi بعد اكتمال المهمة بدفعات خلفية لا تُحسب فيها، مطفأة بـ`ELE_RENDER_HD=false` حتى يقررها المالك بعد قياس حقيقي. (ج) 100 dpi وحده.
- **التوصية: (ب).** 100 dpi يقرأ التكبير العادي، والتظليل متجهي (إحداثيات) يبقى دقيقاً عند أي تكبير.

**✅ INC2‑Q‑5: ميزانية OCR (أمن وكلفة). الحسم: (أ)، التوصية (المالك، 2026-11-25).**
- **المشكلة.** متابعة F0 S‑4 (`../inc-1/f0/security-review.md:29`): 400 مسار في الصفحة تكفي لإطلاق التعرّف، فصاحب ملف واحد يضع كلفة المعالج. والنظام متعدد المستأجرين على معالج واحد.
- **الخيارات:** (أ) ميزانية للوثيقة 1,000 صفحة متعرَّف عليها، ولمساحة العمل 10,000 كل 24 ساعة، والزائد `needs_review (ocr_budget_exceeded)`. (ب) بلا حدّ لمساحة العمل (يكفي حدّ الطابور). (ج) حدّ أصغر.
- **التوصية: (أ)**، والأرقام إعداد (بيئة) تُرفع متى احتاج المالك. لا تُحذف بيانات ولا يفشل ملف: النص المتجهي يُعالج كما هو.

**✅ INC2‑Q‑6: لا تفريغ ذاكرة للابن (تقوية، متابعة F0 S‑2). الحسم: (أ)، التوصية (المالك، 2026-11-25).**
- **المشكلة.** انهيار PyMuPDF عند نفاد الذاكرة قد يكتب تفريغ ذاكرة فيها محتوى PDF إلى معالج أعطال المضيف (`core_pattern` أنبوب). والتعرّف يضيف مكتبات أصلية أكبر.
- **الخيارات:** (أ) في الابن قبل فتح أي ملف: `setrlimit(RLIMIT_CORE, 0)` و`prctl(PR_SET_DUMPABLE, 0)` (ctypes، بلا صلاحية)؛ مع اختبار. (ب) تعطيلها على المضيف. (ج) لا شيء.
- **التوصية: (أ).** سطران، وتقييد للابن لا للخدمة. وتمنع عملية أخرى بالمستخدم نفسه من قراءة ذاكرة الابن بـ`ptrace`. ويُثبت اختبارٌ أن فتح `/proc/self/fd/N` (`services/ele_parse/inspect.py:87`) يعمل بعدها، لأن `/proc` يتغيّر مالكه لعملية غير قابلة للتفريغ.
- **لا يحسم** C‑13 (قتل العمليات المقيمة): يبقى شرطاً قبل الإنتاج بقرار المالك.

### 7.2 قرارات التصميم (بلا ⚠️)

- **INC2‑D‑1: الصفحة تنتظر مسحها**، فلا إعادة لـSTG‑04 وSTG‑05 بعده (HLD §7.1 ترك الخيار). الأسباب: حقائق مزدوجة تغيّر المعرّف بين قراءتين، وحتميتها أصعب (NFR-REP-02)، والمسح نادر ومقصور. والفشل لا يحبس (§3.6.5).
- **INC2‑D‑2: العمليات الثلاث في ابن واحد لكل نداء** بحدود لكل عملية، لا خدمة ثانية (DEC‑07). ومسار كل ابن يبقى «طلب ثم خروج».
- **INC2‑D‑3: عتبات القراءة بيانات** في `page_analysis.toml` مع تجاوز ملف الإعداد، ومصدر كل افتراض مكتوب (§3.2.5). والافتراضات الموسومة «تخمين» تُعاد بعد `G2` بتغيير بيانات بلا شيفرة.
- **INC2‑D‑4: صيغة المشتقات مكتبة قياسية** (أعمدة ثنائية وzlib) لا msgpack وzstd (ADR‑05). لا حاجة لاعتماديتين في صورة `ele-parse`، والحجم المقيس مقبول، والصيغة بإصدار فتُستبدل بلا ترحيل لأنها مشتقة (DATA-04). `INC2‑F‑3`.
- **INC2‑D‑5: منفذان للاستخراج والتصيير والمشتقات** مستقلان، لا منفذ واحد (HLD §6)، لأن أعمارها وأخطاءها تختلف.
- **INC2‑D‑6: لا امتداد هندسي** في Postgres، ومقارنة ضمن الصفحة في التطبيق (§3.3.4، `INC2‑F‑6`).
- **INC2‑D‑7: وثيقة واحدة في الحجم المؤقت** (§3.6.6)، يعيد تصميم `INC1‑D‑20`.
- **INC2‑D‑8: FK `ON DELETE CASCADE` في روابط الظهورات وحدها** (§4.1).
- **INC2‑D‑9: الدليل بمفتاح حتمي، يُستبدل ولا يتراكم.** إعادة المعالجة تعيد المعرّفات نفسها، فلا تتراكم نسخ. أثره: لا تاريخ للأدلة السابقة؛ والتاريخ إصدار الخط وإصدار ملف الإعداد (DATA-04).
- **INC2‑D‑10: جلسة ONNX مباشرة** للتعرّف على السطور (لاحتمالات CTC والبديل الثاني)، وRapidOCR للكشف في المسح. النموذج نفسه والحزمة نفسها المثبّتان (DEC‑07).
- **INC2‑D‑11: تزامن 1 في العامل، ومجموعات الأدوار في عملية واحدة**، مع `CONSUMER_BLOCK_MS = 500` للعامل وحده (§3.7.2). لا نسخة ثانية قبل حلّ `INC1‑R‑17`.
- **INC2‑D‑12: غياب النموذج ليس عطلاً** (§3.1.1): النص المتجهي يُعالَج، والصفحة `needs_review`.
- **INC2‑D‑13: الترتيب:** `signals.tokens` (عيّنة STG‑01) لا تُستعمل بعد STG‑02 إلا للاكتشاف. STG‑05 يقرأ النص كاملاً.
- **INC2‑D‑14: الصفحة النقطية `raster_page`** في INC‑2، لا `ocr_unavailable`: ذاك يعني «الخدمة معطلة» وينتظر INC‑3 (`../inc-1/f1/design.md` §5.5).
- **INC2‑D‑15: المسح المتعدد:** لكل صفحة حدث واحد، لا لكل منطقة.
- **INC2‑D‑16: `electrical.document.extracted` و`extraction_failed` لا يُنشران** (HLD §7.8): لا مستهلك لهما قبل INC‑6، وسبب فشل المهمة في `job.completed`.
- **INC2‑D‑17: سجلّ القراءات البديلة** يبقى في `detail` لا ظهوراً ثانياً: الظهور واحد لكل موضع، ويحسمه السياق في STG‑05، أو التحقق المتبادل في INC‑5.
- **INC2‑D‑19: تصويت الأشكال لا يُبنى في INC‑2.** DEC‑19 جعله اختيارياً ومعطّلاً، ومكسبه المقيس معرّف واحد. فلا مفتاح له في ملف الإعداد ولا شيفرة ميتة، ويُضاف إن أثبت قياس أوسع حاجته (`INC2‑F‑12`).
- **INC2‑D‑18: لا ADR جديد.** ADR‑05 وADR‑24 تتغيّران بقرار معماري (يُطلب تحديثهما في §8).

### 7.3 المخاطر

| # | الخطر | الأثر | المعالجة |
|---|---|---|---|
| INC2‑R‑1 | حجم INC‑2: 9 PRs (F1‑R‑11) | أول نتيجة مفيدة على صفحات المورد الأول بعد `PR‑F` و`PR‑H` معاً | `G3` بعدهما؛ و`PR‑E` ثم `PR‑G` ثم `PR‑H` تعطي نتيجة على صفحات النص المتجهي قبلها؛ ويجوز تقديم `PR‑F` على `PR‑G` إن اجتيز `G2` مبكراً |
| INC2‑R‑2 | أرقام P1 متفائلة، وعتبات الهندسة تخمين (RSK‑21، RSK‑22) | سطور تُفقد أو تُدمج على مورد آخر | `G2` على محجوزة، والتفصيل حسب المورّد، والمسح الاحتياطي، والعتبات في ملف الإعداد |
| INC2‑R‑3 | NFR-PERF-01 مع OCR وتصيير: التقدير (تعرّف 5 ث + استخراج 1 + مستويان 1.7) ≈ 8 ث/صفحة على ابن واحد، وعلى ابنين ≈ 33 دقيقة لـ500 صفحة | **تجاوز حدّ NFR-PERF-01 (25 دقيقة، M) بالسقوف المُقَرّة نفسها**، لا بالبديل (ب) وحده | `T500` في `G2` يُذكر ويعيد `INC2‑Q‑2` إلى المالك إن زاد على 25 دقيقة؛ والبديل الجاهز 3 أبناء و4 نوى و4 GiB (تقديره نحو 22 دقيقة)؛ وعدد خيوط ONNX يُقاس مع ذلك؛ ولا يُقبل INC2‑AC‑28 صامتاً |
| INC2‑R‑4 | الربط بين معاملات المحتوى وMuPDF (`seq`) قد لا يتطابق على ملفات حقيقية | عضوية XObject والمحتوى المعلَّم غير متاحة | `unavailable` صريح، لا تخمين؛ FR-VEC-02 لبقية الخصائص |
| INC2‑R‑5 | ONNX وOpenCV يوسّعان سطح هجوم داخل الابن (RSK‑26) | ثغرة في فك ترميز النموذج أو الصورة | `RLIMIT_AS` و`NOFILE` و`FSIZE`، وشبكة داخلية، و`cap_drop`، و`INC2‑Q‑6`، وثقة بالصور المولّدة داخل الابن لا الواردة، وبصمات النماذج والعجلات |
| INC2‑R‑6 | كائن MinIO يتيم إن مات العامل بين الكتابة والمعاملة | بقايا لوثيقة مطهَّرة | الصف `pending` قبل الكتابة يحمل المفتاح، فالكنس يحذفه؛ ومن مات أثناء الكنس يكمله التسليم المكرر؛ وكنس `PLT‑22` في INC‑5 |
| INC2‑R‑7 | رابط موقّع يعمل ≤ 300 ث بعد التطهير | تسريب صورة لدقائق | مدة قصيرة، ومستأجر فقط؛ سلوك المنصة القائم |
| INC2‑R‑8 | وثيقة واحدة في الحجم المؤقت: وثيقتان كبيرتان متداخلتان تعيدان التنزيل لكل دفعة | بطء على ملفات قرب 1 GB | مقبول مقابل شرط العزل (`INC1‑R‑17`)؛ ويُقاس في `PR‑I` |
| INC2‑R‑9 | قائمة الثنائيات الصينية غير متاحة بترخيص مسموح، ولا عيّنة صينية متجهية في المحجوزة (RSK‑27) | كشف المشوّه ناقص لصيني سليم الترميز | فرع الترميز وحده، ويُذكر في M-29 «غير مقيس» |
| INC2‑R‑10 | طول انتظار مجموعة `ocr` يفعّل `AizzakElectricalQueueWaitHigh` | إنذار كاذب | وصف الدليل، وضبط العتبة بعد أول حمل حقيقي |
| INC2‑R‑11 | تضخّم ذاكرة `worker-electrical` بحزم الجواب | OOM | مجموعتان، والحزم تُفرَّغ بعد الكتابة، وسقف الجواب، وسقف حاوية العامل |
| INC2‑R‑12 | `PIPELINE_VERSION` يتغير في كل PR، فتظهر الوثائق كلها «تحتاج إعادة» | تعب المشغّل | `needs_reprocessing` وصفي، والإعادة مرة واحدة بعد `PR‑F` و`PR‑H` (`H6`) |
| INC2‑R‑13 | معنى التسمية المكدّسة والسياق ANSI بخلاف مورد ثالث (F3 لا مورد ثالث) | هوية خاطئة | القواعد بيانات وسياق صريح، و`needs_review` بدل التخمين |
| INC2‑R‑14 | قراءة نص غير موثوق (حقن في المخرجات) | عرض HTML أو تعليمات في نموذج لاحق | لا قالب ولا نموذج في INC‑2، والتهريب في السجلّ والجواب، والوصف «نص غير موثوق» |

### 7.4 تعارضات وانحرافات وُجدت

| # | ما وجدته | الموضع | الحكم |
|---|---|---|---|
| INC2‑F‑1 | المجموعة المحجوزة المطلوبة 20 صفحة وألف معرّف، والموسوم من المورد الأول 9 صفحات منها 3 للضبط، وبلا نص سطور بمواضعها | HLD §13.7 وF1 §4.6، مقابل الوسم الموجود | ✅ `INC2‑Q‑1` (أ): يُوسم ما ينقص قبل `G2`، والحدّ كما هو |
| INC2‑F‑2 | `CLAUDE.md` يذكر «8 contracts» و«11 modules»؛ الفعلي 9 عقود، و12 مدخلاً في عقد استقلال الوحدات (`INC0‑Q‑7`، `INC1‑F‑17`) | `CLAUDE.md` | تصحيحه للمالك |
| INC2‑F‑3 | ADR‑05 يختار msgpack وzstd، وهنا stdlib | HLD §15 | حُدّث ADR‑05 في HLD 0.21. والاعتماديتان إذن (DEC‑13) لا إلزام |
| INC2‑F‑4 | ADR‑24 ينص على Noto في صورة `ele-parse`؛ والمسبار يظهر أن خط MuPDF الاحتياطي يُصيّر الصيني غير المضمَّن | HLD §15، §13.4 | إن طابق الجودة (M-29 مرئياً) لا يُضاف Noto؛ وإلا يُضاف بـOFL-1.1 المقر. **لم يُتحقق من ترخيص الخط المضمَّن في عجلة PyMuPDF**، ويتحقق منه `G1` (يُضاف الخط إلى `inc-2/notices.md`). وحُدّث ADR‑24 في HLD 0.21 «قراراً يُؤكَّد في `G1`» |
| INC2‑F‑5 | HLD §12.1 يذكر `/electrical/pages/{id}` و`…/image`؛ والصفحة مفتاحها مركّب بلا معرّف مفرد | HLD §12.1 | مسارات متداخلة تحت الوثيقة (§3.3.1)، وحُدّث في HLD 0.21 |
| INC2‑F‑6 | HLD §8.1 يطلب فهرس GiST على صندوق الإحاطة | HLD §8.1 | المتوقع أنه لا يفيد تحت `FORCE RLS` (غير leakproof)؛ وبديله فهرس الصفحة. **غير متحقق بخطة استعلام**: يؤكده اختبار تكامل `EXPLAIN` في ET1. وحُدّثت §8.1 وADR‑23 في HLD 0.21 بهذا الشرط |
| INC2‑F‑7 | `NFR‑F2‑5` يطلب 1,000 مدخل أسطورة، وحدّ جسد ملف الإعداد في القاعدة 64 KiB (`0001_electrical_drawings.py:262`) | HLD §13.5، قاعدة | توسيع القيد إلى 1 MiB (§4.1) |
| INC2‑F‑8 | FR-VEC-02 يطلب المحتوى المعلَّم وعضوية XObject؛ وPyMuPDF لا يعيدهما في `get_cdrawings()` | SRS FR-VEC-02 | ماسح معاملات محدود، و`unavailable` صريح (§3.1.3) |
| INC2‑F‑9 | HLD §7.8 يذكر حدث `document.extraction_failed` | HLD §7.8 | لا يُنشر في INC‑2 (`INC2‑D‑16`)، وذُكر ذلك في HLD 0.21 |
| INC2‑F‑10 | HLD §7.1 وF1 §5.5 يقولان «ثم يُعاد تشغيل STG‑04..07 أو تنتظر»، وهذه الوثيقة تختار الانتظار | HLD §7.1 | `INC2‑D‑1`، وحُدّثت §7.1 و§14.1 في HLD 0.21 |
| INC2‑F‑11 | HLD §4.1 يذكر `ele-parse` «تصيير المربعات» ضمن STG‑02، وهنا مستويان قصيران ومستوى ثالث خلفي | HLD §4.1 | ✅ `INC2‑Q‑4` (ب)، وحُدّث النص في HLD 0.21 |
| INC2‑F‑12 | F1 §4.6 يقيس مسار V2 (مع التصويت) «للعلم فقط» في `F1‑MS‑1`، ولا تصويت مبني هنا | F1 §4.6 | يُحذف V2 من القياس ويُذكر ذلك في التقرير (`INC2‑D‑19`). لا يمسّ عتبة |

## 8. تحديثات الوثائق الحاكمة (`PLT‑23`)

`docs/hld.md` عُدّل في جولة الوثائق (0.21). وبقية الجدول تُطلب في الـPR المذكور بيد المنفّذ الموضح، ويبقى `status.md` للمنسّق، و`CLAUDE.md` للمالك.

| الوثيقة | التغيير | الـPR | المنفّذ |
|---|---|---|---|
| `docs/design/01-data-model.md` §2.12 | الجداول الثمانية، والقيود الموسّعة، ولماذا لا GiST | D، I | `data-engineer` (E7) |
| `02-port-contracts.md` | `PdfExtraction`، و`PageRendering`، و`ArtifactStore`، وتوسيع `PageRepository` | E | `backend` |
| `03-api-spec.md` §1، §2، §4 | 15 عملية، ومخططات، و3 رموز، و`page_stage` الموسّع | E، G، I | `backend` |
| `04-event-catalog.md` §2، §4، §7 | الأحداث الثلاثة والمجموعتان، وملاحظة `ele-parse` | E، F | `backend` |
| `05-rbac-config-secrets.md` §1.3، §2، §4 | المفاتيح الجديدة، ومسارات `electrical:manage` و`electrical:eval` (الصلاحيات موجودة) | E، F، I | `backend` |
| `06-domain-models.md` | صفحات، وظهورات، وأدلة، ومراحل | E، H | `backend` |
| `07-nfr-slo.md` §4 (`:70-79`) | الحدود الجديدة وسقوف العمليات | E، F | `backend` |
| `08-local-runbook.md` §2‑ج و§4.29 | السقوف، وأدوار العامل، وشرط العزل | C، E، F | `devops` (O7) |
| `09-testing-strategy.md` §5.1 | `live_ocr`، وأدوات `tests/eval`، وقياس الأداء | C، I | `backend` |
| `docs/architecture.md` و`.html` | وصف `ele-parse` و`worker-electrical` بدورَي الصفحات والمسح | I | `backend` (BD8، BD9) |
| `docs/design/openapi.yaml`، ومخططات الأحداث | B42، B43، B44 | E..I | `backend` |
| `docs/runbooks/alerts.md` | قسم `AizzakElectricalQueueWaitHigh` بالمجموعات الثلاث | E، F | `devops` |
| `docs/stack-commands.md`، `docs/deploy-server.md` | أمر `ele-parse extract`، وإعادة المعالجة بعد الترقية، و`0002`/`0003` | E، I | `devops` |
| `docs/hld.md` (دورة الوثائق) | ✅ **تمّ في 0.21 (2026-11-25):** §4.1 (المستويات)، و§7.1 و§14.1 (الانتظار لا الإعادة)، و§7.8 (`extraction_failed` لا يُنشر في INC‑2)، و§8.1 وADR‑23 (لا GiST، يؤكده `EXPLAIN`)، و§12.1 (المسار)، وADR‑05 وADR‑24 (§15؛ والأخير يؤكده `G1`)، وصفّ INC‑2 في §16 (البوابات)، وRSK‑21 وRSK‑24 بالقرارات. ولا قياس فيه، وRSK‑22 وRSK‑23 بلا تغيير | 0.21 | معماري (جولة الوثائق) |
| `docs/requirements.md` | لا تغيير. ومقترح للمالك إن شاء لاحقاً: في FR-VEC-02 «المحتوى المعلَّم حيث يتاح» (`INC2‑F‑8`) | — | — |
| `docs/delivery/electrical-drawings/status.md` | صفوف INC‑2 والقرارات | — | المنسّق |
| `CLAUDE.md` | عدد العقود والوحدات (`INC2‑F‑2`) | — | المالك |
| `README.md`، `docs/quickstart.md` | لا تغيير | — | — |

## 9. ملخص التسليم للمنسّق

- **الملفات لكل منفّذ:** `backend-developer` 109 ملفات مصدر وعقد وبيانات و61 ملف اختبار وتقييم و9 وثائق؛ و`data-engineer` 12 ملفاً (هجرتان)؛ و`devops-engineer` 21 ملفاً؛ وبشري: ستة بنود (H1..H6). ولا واجهة أمامية، فمرحلة UX والـ`frontend-developer` تُتخطى.
- **الترحيل: نعم، اثنان.** `electrical/0002_page_facts` (`PR‑D`) و`electrical/0003_gold_labels` (`PR‑I`)؛ توسيع متوافق مع الخلف، وتراجع `0002` يشترط خلوّ القيم الجديدة.
- **9 PRs** (`PR‑A..PR‑I`)، ولا PR ينتظر كله:
  1. `PR‑A` نواة وقارئ متسامح · 2. `PR‑B` استخراج وتصيير · 3. `PR‑C` سطور وتعرّف ومسح وأداة `F1‑MS‑1` · 4. `PR‑D` ترحيل ومنافذ · 5. `PR‑E` خط الصفحات والعرض · 6. `PR‑F` تثبيت النموذج ودور `ocr` · 7. `PR‑G` STG‑04 والأسطورة · 8. `PR‑H` STG‑05 · 9. `PR‑I` التسميات والقياس.
- **البوابات داخل البناء:** `G1` (مراجعة الثنائيات، تحجب دمج `PR‑C`)، و`G2` (`F1‑MS‑1`، تحجب `PR‑F`، وتعيد سقوف `ele-parse` إلى المالك إن زاد إسقاط 500 صفحة على 25 دقيقة)، و`G3` (أول نتيجة على عيّنات المالك، بعد `PR‑F` و`PR‑H` ثم `H5` و`H6`).
- **قبل `PR‑F`** لا يظهر نص ولا معرّف على صفحات النص المرسوم (74 من 76 عند المورد الأول)؛ ويظهران على صفحات النص المتجهي.
- **المراحل المتخطاة:** UX، والواجهة الأمامية. ولا نشر ولا تغيير على خادم.
- **قرارات المالك الستة ✅ (2026-11-25، كلها بالتوصية):**
  1. `INC2‑Q‑1` **(أ)**: وسم 14 صفحة أخرى على الأقل ونص 200 سطر بمواضعها، قبل `G2`.
  2. `INC2‑Q‑2` **(أ) مؤقتاً حتى `G2`**: ابنان، و3.0 نوى، و3,072 MiB، و192 عملية، و`RLIMIT_AS` 2,560 MiB للتعرّف، و45 ث. وتقديرها نحو 33 دقيقة لـ500 صفحة OCR، فوق حدّ 25؛ والبديل الجاهز 3 أبناء و4 نوى و4 GiB بقرار المالك.
  3. `INC2‑Q‑3` **(أ)**: مراجعة الثنائيات قبل دمج `PR‑C`، ومعها OpenCV في `app`.
  4. `INC2‑Q‑4` **(ب)**: 50 و100 dpi في المهمة، و200 خلفية معطّلة.
  5. `INC2‑Q‑5` **(أ)**: 1,000 صفحة للوثيقة، و10,000 لمساحة العمل كل 24 ساعة.
  6. `INC2‑Q‑6` **(أ)**: `RLIMIT_CORE=0` و`PR_SET_DUMPABLE=0`.
- **لا قرار معلّقاً.** ما يحجب بعد اليوم بوابات لا قرارات: `G1` لدمج `PR‑C`، و`G2` لـ`PR‑F`.
- **أهم ما يُراقَب:** حدّ الـ25 دقيقة (INC2‑AC‑28)، ووسم المحجوزة (`H3`) الذي ينتظره `G2`.
- **ما لم أتحقق منه:** ترخيص الخط المضمَّن وملف إشعارات ONNX Runtime وOpenCV (بوابة `G1`)؛ خطة استعلام الفهارس تحت RLS (اختبار `EXPLAIN`)؛ أزمنة الاسترجاع الحقيقية على المضيف (`G2`)؛ قيد `drawings_profile_versions_body_check` الافتراضي؛ وأن `seq` PyMuPDF يساوي عدّ معاملات المحتوى على الملفات الحقيقية (`INC2‑R‑4`).

</div>
