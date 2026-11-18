<div dir="rtl">

# التصميم التقني: F3، قواعد المعرّفات (تغيير شيفرة مستقل قبل LLD الزيادة INC‑2)

- **الإصدار:** 1.0، **مُقَرّ للبناء** (2026-11-18). وكان 0.1 مسودة بست قرارات معلَّقة.
- **التاريخ:** 2026-11-18
- **الفرع:** `docs/electrical-drawings-f3-design` من `master` = `8629341`. هذه الجولة وثيقة واحدة، بلا شيفرة. والبناء بعدها PR شيفرة مستقل من `master`، مثل البند 1 من F2.
- **قرار المالك عند البوابة ① (2026-11-18):** «الفجوات وحدها أولاً». تصميم صغير لقواعد المعرّفات، يُبنى قبل LLD الزيادة INC‑2. وليس هذا LLD الزيادة. فكل ما يحتاج سياق الصفحة أو الهندسة أو الرموز أو الموضع أو سياق STG‑05 أو استرجاع النص المرسوم خطوطاً مؤجَّل إليه، فجوةً فجوة، بسببه (§2).
- **المدخلات:** `inc-1/f3/gaps.md` (فج‑1..فج‑50 وترتيب §9)، و`measure-1.md` و`measure-2.md` و`measure-3.md`. والسابقة `inc-1/f2/design.md` (§3، و§4.1، و§5، و§11.1). و`docs/requirements.md` 0.20 (FR‑ID‑*، وM‑04، وD‑36)، و`docs/hld.md` 0.19 (§5.4، وDEC‑20، و§16).
- **المستودع عام:** أمثلة مخترعة فقط، والقاعدة `F2‑AC‑20` باستثناءاتها المكتوبة (`inc-1/f2/stories.md`، وقرارا 2026-11-09 في `status.md`). وفحصتُ كل مثال جديد هنا على الحقيقة المرجعية خارج المستودع، فلم يطابق شيئاً (§9). وتُكتب أنماط العيّنات مجردة: `9` رقم، و`A` حرف.
- **المسارات:** `src/...` و`tests/...` و`services/...` و`docs/design/...` نسبةً إلى جذر المستودع. و`docs/...` و`inc-1/...` نسبةً إلى `docs/delivery/electrical-drawings/`.
- **الحالة:** ✅ حسم المالك القرارات الستة في 2026-11-18، كلها بالتوصية: F3‑Q1 (ج)، وF3‑Q2 (ب)، وF3‑Q3 (أ)، وF3‑Q4 (ج)، وF3‑Q5 (أ)، وF3‑Q6 (أ) (§7.1). فلا قرار معلَّق، والتصميم كما كُتب.
- **التعديلات المطبَّقة في هذه الجولة:** `docs/requirements.md` من 0.20 إلى 0.21 (D‑37)، و`docs/hld.md` من 0.19 إلى 0.20 (§8). والبناء بعد دمج هذا الفرع.

## 1. الملخص

**ما يُبنى.** قواعد معرّفات جديدة أو مصحَّحة في `shared` (CMP‑06)، وأغلبها بيانات في `identifiers.toml`. وسطر واحد في `drawings`. وتغيير صغير في `ele-parse` (عيّنة الكلمات، F3‑Q4 (ج)). والمجموعات ست، على ترتيب `gaps.md` §9:

1. **ANSI بلا سياق** (فج‑1..3، و5..8، و18، و32): لا يُقرأ رقم ANSI بلاحقة أو مثيل، ولا مركّب، بلا `hint` إلا إن كان لكل رقم فيه فئة في `[ansi.functions]`. ونمط سلك للمورد يسمّيه ملف الإعداد يتقدّم على قراءة ANSI بلا سياق. وتُقبل لاحقة بعدها رقم واحد (§3.4).
2. **قيم التقدير قبل المعرّفات** (فج‑4، و9، و15): الرقم بوحدة من `[ratings]` ليس معرّفاً. والصفحة 0 ليست صفحة في المرجع المتقاطع (§3.5).
3. **القوائم والنطاقات** (فج‑10، و22، و24، و25، و29): نوع معرّف جديد `identifier_group` يُفكّ إلى عناصره (§3.6).
4. **التقطيع والتطبيع** (فج‑14، و27، و34): الفاصلة الصينية `、` تُطبَّع `,`، والرقم المدوّر لا يُطبَّع رقماً، ودالة `split_candidates` تقطّع الكلمة عند علامات الجملة، والشرطة العليا بعد رقم الطرف جزء منه (§3.7).
5. **الاكتشاف** (فج‑38، و39، و40، و41): العيّنة تبدأ بالكلمات التي فيها رقم، والكلمة الطويلة تُقطَّع قبل حدّ الطول، والطرف `X9:99` يُقرأ تحت اصطلاح الأجهزة الصيني أيضاً، و`MIN_IDENTIFIERS` يبقى 20 (§3.8، §3.9).
6. **أنماط المورد بالإعداد** (فج‑16، و17، و23، و30، و33): أنماط أسلاك تُقرأ حين يسمّيها ملف الإعداد وحده، وصيغ KKS وشرطة بين مستوياته، وصيغتا إشارة الموقع (§3.4، §3.10).

**أين يقع.** في الوحدة `electrical`، في الحزمة النقية `shared`. وفي `drawings/application/projects.py` سطر واحد. وفي `services/ele_parse/` دالة واحدة. لا مسار API جديد، ولا حقل في ملف الإعداد، ولا جدول، ولا ترحيل، ولا اعتمادية، ولا إعداد نشر. ولا يمسّ `circuits` ولا `search` ولا `diagnostics`.

**المبدأ الحاكم: نوع خاطئ بلا تحذير أسوأ من لا شيء.** كل قاعدة هنا تحوّل 🔴 (هوية خاطئة و`valid=True`) إلى قراءة صحيحة، أو إلى «لا معرّف» حين يكون الحسم بالسياق. والسياق يمرّره STG‑05 في INC‑2 بـ`hint`، كما قرر F2 للرقم المجرد (FR‑ID‑16، Q2).

**السابقة التي يتبعها.**
- **F2 البند 1** (`inc-1/f2/design.md`): البيانات في `identifiers.toml`، والقراءة بالاسم في ملف الإعداد، وحالات M‑04 عقد، والإصداران، وقاعدة الدليل (§11.1 هناك).
- **`cross_reference_pinned`** (`shared/ansi.py:100-108`): صيغة يثبّتها الملف تتقدّم على قراءة ANSI. ويُنسخ النهج للأسلاك.
- **`[terminals.digits_first]`** (A‑5، `shared/rules/systems.py`، `digits_first_from_mapping`): الاصطلاحات لكل رمز طرف بيانات. ويُنسخ للصيغة التي يسبق فيها الرمزُ الرقم.

**الأثر المقيس مسبقاً** (للتحقق فقط، على الحقيقة المرجعية خارج المستودع، §9):
- قُسّم كل نص موسوم ليس رقم ANSI إلى كلمات، وحُلّل بالاصطلاحين المقيسين ومعهما KKS وANSI.
- **قاعدة الفئة وحدها** تُسقط 240 من 248 قراءة ANSI صالحة زائفة (97%)، و126 من 137 قراءة ANSI غير صالحة زائفة.
- **ولا تمسّ** المكدّس الرقمي في الدوائر: 21 من 21، كل أرقامها في الجدول.
- **والكلفة:** 13 من 46 رقم ANSI بلاحقة في جداول الدليل أرقامها خارج الجدول. فلا تُقرأ بلا سياق حتى INC‑2، أو حتى يُضاف رقمها إلى الجدول بالبيانات (§7، خ‑1).

## 2. قرار كل فجوة، والملفات

### 2.1 قرار كل فجوة

**الأصناف:**
- **الآن (شيفرة):** يُبنى في هذا الـPR، شيفرةً وغالباً بياناتٍ معها.
- **الآن (بيانات):** يُبنى في هذا الـPR بملف القواعد وحده.
- **مؤجَّل:** إلى LLD الزيادة INC‑2، والسبب مكتوب.
- **لا إجراء:** بالتصميم.

**العدّ:** الآن (شيفرة) 25، والآن (بيانات) 4، ومؤجَّل 18، ولا إجراء 3. والمجموع 50.

**الأمثلة كلها مخترعة.** والمعيار في العمود الأخير في §5.

| # | خ | القرار | القاعدة، وأين تعيش | اليوم ← المتوقَّع (مثال مخترع) | المعيار |
|---|---|---|---|---|---|
| فج‑1 | 🔴 | الآن (شيفرة)، F3‑Q1 | قاعدة الفئة (شيفرة، `ansi.py`) وأنماط المورد بالاسم (بيانات `[wire_numbers_opt_in]`، وشيفرة `wire_number_pinned`)، §3.4 | `38Y` وANSI مفعّل: `ansi_device` صالح ← لا معرّف. وبـ`digits_letter` في الملف: `wire_number` ببديل `ansi_device` | F3‑AC‑1، 3 |
| فج‑2 | 🔴 | الآن (شيفرة)، مع فج‑1 | كما في فج‑1 | الشكل `9Z`: `ansi_device` صالح ← لا معرّف، أو سلك بالنمط المسمّى | F3‑AC‑1 |
| فج‑3 | 🔴 | الآن (شيفرة)، F3‑Q2 | قاعدة فج‑1 تكفي: رقم الطرف المفرد بلا فئة. لا قاعدة خاصة بـ`X` | `3X-41` باصطلاح NFPA: `ansi_device` صالح ← لا معرّف | F3‑AC‑5 |
| فج‑4 | 🔴 | الآن (شيفرة) | `[ratings]` (بيانات) وحاجز في المحلّل (شيفرة)، §3.5. والوحدة التي يطابق شكلها الكبير كود حروف مفعّلاً تُطابَق بحالتها المكتوبة | `37kV` بأكواد الحروف الصينية: `letter_code` صالح ← لا معرّف. و`3KA` يبقى `letter_code` | F3‑AC‑7 |
| فج‑5 | 🔴 | الآن (شيفرة)، مع فج‑1 | قاعدة الفئة | كلمة `8P` من «`MOTOR 8P`»: `ansi_device` صالح ← لا معرّف | F3‑AC‑1 |
| فج‑6 | 🔴 | الآن (شيفرة)، مع فج‑1 | قاعدة الفئة على المثيل | `23-4` و`4-23`: `ansi_device` صالح ← لا معرّف. و`27-1` كما هو | F3‑AC‑1، 2 |
| فج‑7 | 🔴 | الآن (شيفرة)، جزئياً | قاعدة الفئة على المركّب. **والباقي مؤجَّل مع فج‑9:** تمييزها نسبةً يحتاج الكلمة التالية في السطر (STG‑05) | `23/4`: `ansi_device` ← `cross_reference` ببديل `ansi_device` | F3‑AC‑1 |
| فج‑8 | 🔴 | الآن (شيفرة)، مع فج‑1 | قاعدة الفئة | `8P` في رمز طراز: `ansi_device` صالح ← لا معرّف | F3‑AC‑1 |
| فج‑9 | 🔴 | الآن (بيانات)، جزئياً | صنف الدقة: الصفحة 0 ليست صفحة في تعابير `[cross_references]`. **ومؤجَّل:** نسبة المحوّل `999/9`، فلا يميّزها إلا ما بعدها (`9A99`، `99AA`) في السطر نفسه | `0.7`: `cross_reference` ← لا معرّف. و`12.3` كما هو | F3‑AC‑10 |
| فج‑10 | 🔴 | الآن (شيفرة) | نطاق في `identifier_group` قبل الأطراف، §3.6 | `X23-X37` باصطلاح IEC: الطرف `X37` على الشريحة `X23` ← نطاق بخمسة عشر عنصراً، وكل عنصر وحده لا معرّف، فـ`valid=False` بسبب `group.member_unreadable` | F3‑AC‑12 |
| فج‑11 | 🔴 | مؤجَّل | اسم الجهاز يُعرف من موضعه فوق الرمز. وقائمة الجهود من `FR‑ID‑08` وملف الإعداد لا ينقص منها | — | — |
| فج‑12 | 🔴 | مؤجَّل، مع فج‑28 | جدول أسماء المورد أو الموضع | — | — |
| فج‑13 | 🔴 | مؤجَّل | رمز في صندوق: سياق الرموز (STG‑05، وINC‑4) | — | — |
| فج‑14 | 🔴 | الآن (شيفرة) | الرقم المدوّر لا يُقرأ بلا `hint` (`parser.py`، §3.7). وقراءته رقم كابل مؤجَّلة: الدائرة اصطلاح رمز | `⑰`: `wire_number` 17 ← لا معرّف | F3‑AC‑18 |
| فج‑15 | 🟠 | الآن (شيفرة) | حاجز `[ratings]` | `37MVA` و`47Hz` و`17VA` و`2PH` و`7KW`: `ansi_device` غير صالح ← لا معرّف | F3‑AC‑7 |
| فج‑16 | 🟠 | الآن (بيانات)، F3‑Q3 | صيغة KKS `system_code` (`NNAAA`) في `[kks.parts]` و`[[kks.forms]]`. والأقواس تُنزع بالتقطيع (فج‑34) | `20XYZ`: `ansi_device` غير صالح ← `kks_tag` بالمستوى 1، صالح، وليس دليلاً | F3‑AC‑21 |
| فج‑17 | 🟠 | الآن (بيانات)، F3‑Q3 | صيغة KKS `component_wide` (المكوّن `AANNN`) | `20XYZ10AB123CD456`: `kks_tag` غير صالح (`kks.malformed`) ← `kks_tag` مكوّن صالح | F3‑AC‑21 |
| فج‑18 | 🟠 | الآن (شيفرة)، مع فج‑1 | قاعدة الفئة، والنمط المسمّى | `16F`: `ansi_device` غير صالح ← لا معرّف، أو سلك بالنمط المسمّى | F3‑AC‑1، 3 |
| فج‑19 | 🟠 | الآن (شيفرة)، جزئياً | قاعدة الفئة تُسقط قراءة ANSI. **وقراءته شريحةً مؤجَّلة** مع فج‑26 | الشكل `9AA`: `ansi_device` غير صالح ← لا معرّف | F3‑AC‑1 |
| فج‑20 | 🟠 | مؤجَّل | الطراز يُعرف من موضعه (جدول، بعد اسم مصنّع). ويبقى معلَّماً غير صالح | — | — |
| فج‑21 | 🟠 | لا إجراء | `#-99` جانب `#` بشكل خاطئ، فيُعلَّم `aspect.malformed` كما يطلب FR‑ID‑10 | — | — |
| فج‑22 | 🟡 | الآن (شيفرة) | قائمة في `identifier_group`، والعنصر الرقمي يرث رأس الأول. والفاصلة العريضة يطبّعها NFKC، و`、` يطبّعها التطبيع الجديد | `X7:41,42,43`: لا معرّف ← مجموعة بثلاثة أطراف `X7:41` و`X7:42` و`X7:43` | F3‑AC‑11 |
| فج‑23 | 🟡 | الآن (بيانات)، جزئياً | صيغتان في `[cross_references]`: `page_dot_location` و`same_page_location`. **ومؤجَّل مع فج‑44:** حلّها برقم صفحة المخطط | `27.6E` و`.6E`: لا معرّف ← `cross_reference` بالصيغتين | F3‑AC‑22 |
| فج‑24 | 🟡 | الآن (شيفرة) | نطاق، وعناصره تُقرأ بالقواعد المضيَّقة | `38Y-43Y`: لا معرّف ← نطاق بستة عناصر، أسلاك حين يسمّي الملف `digits_letter` | F3‑AC‑12 |
| فج‑25 | 🟡 | الآن (شيفرة) | قائمة لاحقة بـ`/` لنوع `kks_tag` (بيانات `[groups] suffix_lists`) | `20XYZ10AB131/2/3`: لا معرّف ← ثلاثة وسوم KKS | F3‑AC‑13 |
| فج‑26 | 🟡 | مؤجَّل | نص المورد الأول لا يوجد إلا بعد استرجاع النص المرسوم (DEC‑19، INC‑2). والصيغة بلا فاصل بين الشريحة والطرف تحتاج قياساً على النص المسترجَع. واسم الشريحة وحده (`X999:`) يحتاج سياق جدول الشريحة | — | — |
| فج‑27 | 🟡 | الآن (شيفرة) | الشرطة العليا بعد رقم الطرف جزء منه (`connection_points.py`، `terminals.py`) | `1X-37'`: لا معرّف ← طرف، ونقطته `37'`، وشكله القانوني غير `1X-37` | F3‑AC‑19 |
| فج‑28 | 🟡 | مؤجَّل | جدول أسماء المورد يحتاج حقلاً في ملف الإعداد. وINC‑2 يضيف حقل مدخلات الأسطورة المؤكَّدة (FR‑ING‑14)، فيُحمل الجدول عليه بتغيير واحد لشكل الملف وإجراء رجوع واحد. والاسم بحرف واحد يتعارض مع الجهود ونقاط الاتصال، فيحتاج الموضع | — | — |
| فج‑29 | 🟡 | الآن (شيفرة)، جزئياً | النطاق `AA9-AA9`، والقائمة اللاحقة `AA9\9`، و`A9 A9` بالتقطيع. **ومؤجَّل:** `A-A9` و`A9-A9/99/99` و`AA9-9`، فمعناها لم يتحقق منه أحد، وفكّها في القياس قراءة المنسّق | `QS7-QS9`: لا معرّف ← `QS7` و`QS8` و`QS9`. و`QS7\8`: لا معرّف ← `QS7` و`QS8` | F3‑AC‑12، 13 |
| فج‑30 | 🟡 | الآن (شيفرة) | نمطان في `[wire_numbers_opt_in]`: `digits_letter` و`letter_digits`. يُقرآن حين يسمّيهما الملف وحده. ولا نمط لـ`AA9`، فهو شكل أسماء الأجهزة | `38Y` وANSI غير مفعّل: لا معرّف ← `wire_number` بصيغة `digits_letter` حين يُسمّى | F3‑AC‑3، 4 |
| فج‑31 | 🟡 | مؤجَّل | أطراف الجهاز تُقرأ في سياق الجهاز | — | — |
| فج‑32 | 🟡 | الآن (شيفرة) | لاحقة بعدها رقم واحد = مثيل بلا شرطة (`ansi.py`) | `59G2`: لا معرّف ← `ansi_device` 59، `G`، المثيل 2 | F3‑AC‑6 |
| فج‑33 | 🟡 | الآن (شيفرة)، F3‑Q3 | شرطة بين مستويين من KKS (بيانات `[kks] part_separators`) | `20XYZ10AB123-CD45`: لا معرّف ← `kks_tag` مكوّن، ومفتاح بحثه بلا شرطة | F3‑AC‑21 |
| فج‑34 | — | الآن (شيفرة) | `、` تصير `,` في التطبيع، و`split_candidates` تقطّع عند `。` و`；` و`;` وتنزع الأقواس وعلامات الآخر. و`＿` يطبّعه NFKC اليوم | `QS7；QS8` و`QS7。` و`(20XYZ)` و`X7:41、42`: لا معرّف ← المعرّفات | F3‑AC‑11، 17 |
| فج‑35 | — | لا إجراء | كلمتان ملتصقتان خطأ في الأصل. والتقطيع على حدود النمط تخمين. ويُذكر لفحوص الاتساق (فج‑49) | — | — |
| فج‑36 | — | مؤجَّل | نافذة الكلمتين تحتاج تجاور الكلمات في السطر (STG‑05). و`parse` يقبل الكلمتين معاً اليوم، لأن التطبيع يحذف المسافة | — | — |
| فج‑37 | — | مؤجَّل | لا شيء في القواعد. استرجاع النص بالهندسة (INC‑2) | — | — |
| فج‑38 | — | الآن (شيفرة، `ele-parse`)، F3‑Q4 | العيّنة تبدأ بالكلمات التي فيها رقم، بترتيب القراءة، ثم الباقي. والحدّ 200 كما هو (§3.8) | صفحة فيها 300 كلمة مختلفة، ومعرّفاتها بعد الكلمة 200: خارج العيّنة ← داخلها | F3‑AC‑23 |
| فج‑39 | — | الآن (شيفرة، `ele-parse`)، مع F3‑Q4 | الكلمة الأطول من 32 حرفاً تُقطَّع عند `,` `，` `、` `。` `；` `;` قبل الحدّ | قائمة KKS طويلة: تُسقط ← وسومها في العيّنة | F3‑AC‑24 |
| فج‑40 | — | الآن (شيفرة)، F3‑Q6 | `[terminals.letters_first]`: الاصطلاحات لكل رمز طرف في الصيغة التي يسبق فيها الرمزُ الرقم. و`X` تحت IEC ثم GB/T ثم الصيني، فلا يتغيّر العدّ (§3.9) | `X7:41` وملف إعداد صيني: لا معرّف ← طرف `cn_letter_codes` | F3‑AC‑20 |
| فج‑41 | — | لا إجراء، F3‑Q5 | `MIN_IDENTIFIERS` = 20 يبقى. وتزيد الأدلة بفكّ القوائم وبالعيّنة الجديدة. ويُضبط مع M‑32 في `INC1‑R‑15` | — | F3‑AC‑29 |
| فج‑42 | ⏸ | مؤجَّل (قرار Q2 في F2) | الرقم المجرد يحتاج الموضع والرمز | — | — |
| فج‑43 | ⏸ | مؤجَّل (البند 3 من F2، FR‑ID‑17) | التسمية المكدّسة هندسية. ولاحقة فيها رقم (فج‑32) جاهزة لها | — | — |
| فج‑44 | — | مؤجَّل | رقم صفحة المخطط من جدول العنوان (STG‑04) | — | — |
| فج‑45 | — | مؤجَّل | عقدة بأكثر من معرّف: نموذج الاتصال | — | — |
| فج‑46 | — | مؤجَّل | هوية الطرف = الجهاز + الطرف: FR‑ID‑02 والبنية الموروثة | — | — |
| فج‑47 | — | مؤجَّل | جدول التلامسات تحت الملف: STG‑04 | — | — |
| فج‑48 | — | مؤجَّل | طبقة خط اليد: هندسة وطبقات | — | — |
| فج‑49 | — | مؤجَّل | حالات لفحوص الاتساق لاحقاً | — | — |
| فج‑50 | — | مؤجَّل | جدول مختصر بنقاط: بنية الجدول (STG‑04) | — | — |

### 2.2 الملفات

**المنفّذ:** `backend-developer` للشيفرة والاختبارات والحالات، و`qa-engineer` لخطة الجودة واختباراتها بعد البناء. ولا عمل لـ`data-engineer`: لا ترحيل ولا DDL (§4). ولا لـ`frontend-developer`: لا واجهة في المستودع.

| المسار | إنشاء/تعديل | المسؤولية | المنفّذ |
|---|---|---|---|
| `src/app/modules/electrical/shared/kinds.py` | تعديل | `IdentifierKind.IDENTIFIER_GROUP`، و`GroupForm` و`GroupMember` و`IdentifierGroup`، و`ParsedIdentifier.group`، وتعليق `ConventionTally.identifiers` (§3.2) | backend-developer |
| `src/app/modules/electrical/shared/ansi.py` | تعديل | قاعدة الفئة بلا سياق، وتقدّم النمط المسمّى، واللاحقة بعدها رقم (§3.4) | backend-developer |
| `src/app/modules/electrical/shared/ratings.py` | إنشاء | `Rating` و`read_rating` (§3.5) | backend-developer |
| `src/app/modules/electrical/shared/groups.py` | إنشاء | `list_items` و`range_members` و`inherit` و`suffix_members` وأسباب المجموعة (§3.6) | backend-developer |
| `src/app/modules/electrical/shared/tokens.py` | إنشاء | `split_candidates` (§3.7) | backend-developer |
| `src/app/modules/electrical/shared/normalization.py` | تعديل | طيّ `、` والشرطات العليا، و`folded`، و`has_enclosed_number` (§3.5، §3.7) | backend-developer |
| `src/app/modules/electrical/shared/parser.py` | تعديل | حاجز الرقم المدوّر، وحاجز التقدير، و`_group`، و`_alternatives`، و`tally` على المرشّحين والعناصر (§3.4..§3.7) | backend-developer |
| `src/app/modules/electrical/shared/kks.py` | تعديل | حرف الفصل بين جزأين، و`near_miss_min_length` (§3.10) | backend-developer |
| `src/app/modules/electrical/shared/terminals.py` | تعديل | `terminal_letters_first`، والشرطة العليا في `_CHARSET` (§3.7، §3.9) | backend-developer |
| `src/app/modules/electrical/shared/connection_points.py` | تعديل | `_LABEL` يقبل شرطة عليا واحدة في آخره (§3.7) | backend-developer |
| `src/app/modules/electrical/shared/rules/systems.py` | تعديل | `RatingRules` و`GroupRules` وقرّاؤهما، ومفتاحا `[kks]`، وقراءة `[terminals.letters_first]` (§3.3) | backend-developer |
| `src/app/modules/electrical/shared/rules/__init__.py` | تعديل | الحقول الخمسة، و`from_mapping`، و`narrowed`، و`wire_number_pattern_names` (§3.3) | backend-developer |
| `src/app/modules/electrical/shared/rules/identifiers.toml` | تعديل | `version = "1.2.0"`، والجداول والصيغ والتعابير في §3.3 | backend-developer |
| `src/app/modules/electrical/shared/version.py` | تعديل | `SHARED_VERSION = "1.4.0"` | backend-developer |
| `src/app/modules/electrical/drawings/application/projects.py` | تعديل | السطر 67: `wire_number_pattern_names` (§3.11) | backend-developer |
| `src/app/modules/electrical/drawings/domain/pipeline.py` | تعديل | `PIPELINE_VERSION = "1.2.0"` (مع F3‑Q4 (ج)) | backend-developer |
| `services/ele_parse/inspect.py` | تعديل | `_tokens`: الكلمات التي فيها رقم أولاً، وتقطيع الطويلة (§3.8، مع F3‑Q4 (ج)) | backend-developer |
| `services/ele_parse/contract.py` | تعديل | `LONG_TOKEN_SPLIT` (مع F3‑Q4 (ج)) | backend-developer |
| `services/ele_parse/__init__.py` | تعديل | `SERVICE_VERSION = "1.1.0"` (مع F3‑Q4 (ج)) | backend-developer |
| `docs/design/openapi.yaml` | تعديل | وصف `DrawingProfileIn.body` وحده (§3.1) | backend-developer |
| `tests/eval/electrical/m04_identifiers.py` | تعديل | `wire_number_patterns` في `select`، و`group` في `observed` (§5.2) | backend-developer |
| `tests/eval/electrical/cases/identifiers.toml` | تعديل | حالات §5.3، و`c226`، وتعليق الرأس | backend-developer |
| `tests/eval/electrical/README.md` | تعديل | سطر عن المفتاحين | backend-developer |
| `tests/unit/test_electrical_f3_rules.py` | إنشاء | ANSI بلا سياق، والتقدير، والصفحة 0، والإشارات، والحتمية | backend-developer |
| `tests/unit/test_electrical_f3_groups.py` | إنشاء | القوائم والنطاقات والعدّ | backend-developer |
| `tests/unit/test_electrical_f3_tokens.py` | إنشاء | `split_candidates` والرقم المدوّر والشرطة العليا | backend-developer |
| `tests/unit/test_electrical_shared_ansi.py` | تعديل | صفوف قاعدة الدليل بـ`hint`، وF3‑AC‑6 | backend-developer |
| `tests/unit/test_electrical_shared_parser.py` | تعديل | `F2‑AC‑7` لـANSI، و`_APPENDIX_A` | backend-developer |
| `tests/unit/test_electrical_shared_kks.py` | تعديل | F3‑AC‑21 | backend-developer |
| `tests/unit/test_electrical_shared_terminals_wires.py` | تعديل | F3‑AC‑20 | backend-developer |
| `tests/unit/test_electrical_shared_rules.py` | تعديل | الإصداران، والأنواع الاثنا عشر، وطفرات الجداول الجديدة، وNFR‑F3‑3 | backend-developer |
| `tests/unit/test_electrical_profile_detection.py` | تعديل | صيغتا الموقع تُبقيان `auto` | backend-developer |
| `tests/unit/test_api_electrical_router.py` | تعديل | F3‑AC‑4 | backend-developer |
| `tests/unit/test_ele_parse_service.py` | تعديل | F3‑AC‑23..24، واختبار الترتيب القائم (مع F3‑Q4 (ج)) | backend-developer |
| `docs/delivery/electrical-drawings/inc-1/f3/test-plan.md` | إنشاء | خطة الجودة بعد البناء | qa-engineer |
| `tests/unit/test_electrical_f3_qa.py` | إنشاء | الحالات الحدّية وNFR‑F3‑5 | qa-engineer |

**ملفات لا تتغيّر، وتُذكر لأنها قد تبدو معنية:**
- `drawings/domain/profile.py`: لا حقل جديد، فـ`to_body` والبصمة كما هما.
- `drawings/application/inspection.py`: يستدعي `tally(signals.tokens)` (`inspection.py:626`)، والتقطيع داخل `tally`.
- `drawings/domain/profile_detection.py` و`characterization.py`: الأعداد تتغيّر من داخل `shared`.
- `drawings/adapters/rule_files.py`: يمرّر الخريطة كاملة، فتصلها الجداول الجديدة.
- `drawings/adapters/sql_repository.py`: شكل العدّ والجسد كما هو.
- `drawings/adapters/ele_parse_client.py`: الحدود والشكل كما هي.
- `src/app/api/...`: لا مسار ولا صلاحية.
- `.importlinter` و`tests/architecture/`: §3.12.
- `docker-compose.yml` و`.env.example`: لا إعداد نشر.

## 3. العقود

### 3.1 الواجهات البرمجية

**لا مسار جديد، ولا خاصية جديدة في أي مخطط.** يتغيّر شيئان فقط:

| الطريقة | المسار | ما يتغيّر | الأخطاء | الصلاحية |
|---|---|---|---|---|
| PUT | `/api/v1/electrical/projects/{id}/profile`، و`/api/v1/electrical/documents/{id}/profile` | `wire_number_patterns` يقبل أيضاً `digits_letter` و`letter_digits` | 422 `electrical.invalid_profile` لاسم مجهول، كما اليوم، ولا يعيد الاسم | `electrical:manage` |
| POST | `/api/v1/electrical/documents/{id}/profile-confirmation`، و`/api/v1/electrical/projects` | كما سبق | كما سبق | `electrical:manage` |
| GET | `…/projects/{id}/characterization`، و`…/documents/{id}/characterization` | `cross_reference_formats` قد تحمل `page_dot_location` و`same_page_location`. و`conventions` تعدّ عناصر القوائم. والإصدارات جديدة (§4) | كما هو | `electrical:read` |

- **`docs/design/openapi.yaml`، الوصف وحده:** في وصف `DrawingProfileIn.body` (`openapi.yaml:1788`) يصير «wire_number_patterns (NAMES of packaged patterns, never regular expressions)»: «wire_number_patterns (NAMES of packaged patterns, never regular expressions; digits_letter and letter_digits are vendor patterns read only when named, and a named pattern takes precedence over an ANSI reading without context)».
- **اختبار العقد القائم** `test_every_electrical_schema_has_the_published_properties_and_required_set` ينجح كما هو. و`test_the_profile_body_description_names_every_profile_field` كما هو، لأن `_TOP_KEYS` لا يتغيّر.
- **العميل وأنواعه:** لا واجهة أمامية في المستودع. والجسد كائن مفتوح، فلا يتغيّر عميل مولَّد. وتطبيق Android لا يحرّر ملف الإعداد.

### 3.2 أنواع النواة (`shared/kinds.py`)

```python
class IdentifierKind(StrEnum):
    ...  # the eleven kinds, unchanged
    IDENTIFIER_GROUP = "identifier_group"  # F3 (FR-ID-19): X7:41,42,43 ; QS7-QS9 ; 20XYZ10AB131/2/3


class GroupForm(StrEnum):  # F3, FR-ID-19
    LIST = "list"  # X7:41,42,43 ; QS7,QS8 (a later bare label inherits the first item's head)
    RANGE = "range"  # QS7-QS9 ; 38Y-43Y (same head and tail, the number counts up)
    SUFFIX_LIST = "suffix_list"  # 20XYZ10AB131/2/3 ; QS7\8 (digits replace the trailing digits)


@dataclass(frozen=True, slots=True)
class GroupMember:
    text: str  # the member as expanded, canonical: "X7:42"
    identifier: ParsedIdentifier | None  # None: the member alone is no identifier


@dataclass(frozen=True, slots=True)
class IdentifierGroup:
    form: GroupForm
    separator: str  # "," "-" "/" "\\"
    members: tuple[GroupMember, ...]  # written order; empty when there would be too many


@dataclass(frozen=True, slots=True)
class ParsedIdentifier:
    ...  # every field as today
    # Additive (F3), last and defaulted: the members of an IDENTIFIER_GROUP, None otherwise.
    group: IdentifierGroup | None = None
```

- **الهوية** (`kind`، `canonical`) كما هي. فللمجموعة هويتها بنصها كله (`identifier_group`، `X7:41,42,43`)، ولكل عنصر هويته.
- **مفاتيح البحث:** شكل المجموعة القانوني، ثم الشكل القانوني لكل عنصر قُرئ معرّفاً، بحدّ 64.
- **الصلاحية:** صالحة حين يُقرأ كل عنصر معرّفاً صالحاً. وإلا `valid=False` بأسباب:
  - `group.member_unreadable`: عنصر لا يُقرأ معرّفاً وحده.
  - `group.member_invalid`: عنصر يُقرأ معرّفاً غير صالح.
  - `group.too_many_members`: أكثر من `max_members`، ولا عناصر في الناتج.
  - `group.empty_member`: عنصر فارغ.
- **الاصطلاح:** اصطلاح العناصر حين تتفق كلها عليه، وإلا `None`.
- **`ConventionTally.identifiers`:** يصير عدد القراءات، ولكل عنصر قُرئ قراءة. ويُصحَّح تعليقه في `kinds.py`.
- **لا نوع «قيمة تقدير» ولا نوع «موقع»:**
  - قيمة التقدير ليست معرّفاً (FR‑ID‑20 الجديد). لها قيمة مستقلة `Rating` خارج `IdentifierKind` (§3.5)، يستعملها المحلّل حاجزاً، ويستعملها FR‑NLU‑02 لاحقاً.
  - إشارة الموقع مرجع متقاطع (FR‑ID‑06)، فهي صيغتان في البيانات (§3.10).

### 3.3 القواعد بيانات (`identifiers.toml` و`rules/`)

**`version = "1.2.0"`** (كان `1.1.0`). والإضافات:

```toml
[terminals.letters_first]  # F3 (gap 40), like [terminals.digits_first]: X7:41, X7.41, X7-41
X = ["iec_81346", "gbt_5094", "cn_letter_codes"]
XT = ["cn_letter_codes"]
TB = ["nfpa_jic"]

[kks]
excluded_letters = []
evidence_min_level = 2
# F3 (gap 33): one separator a tag may carry between two parts (20XYZ10AB123-CD45). The joined
# form is a search key, so the two spellings meet.
part_separators = ["-"]
# F3: the shortest text a near miss is reported for (kks.malformed). A data key, because the
# system_code form below is shorter than every earlier form.
near_miss_min_length = 7

[kks.parts]
system = { level = 1, mask = "NNAAANN" }
system_code = { level = 1, mask = "NNAAA" }  # F3 (gap 16): the system code alone (20XYZ)
equipment = { level = 2, mask = "AANNNa" }
component = { level = 3, mask = "AANN" }
component_wide = { level = 3, mask = "AANNN" }  # F3 (gap 17, decision F3-Q3)
cable = { mask = "NNNN" }

# [[kks.forms]]: the five forms as today, then, after "component":
#   name = "component_wide", parts = ["system", "equipment", "component_wide"], class = "component"
# and LAST:
#   name = "system_code", parts = ["system_code"], class = "system"

[cross_references]
# F3 (gap 9): page 0 is no page, so an accuracy class (0.7) is no cross-reference.
slash_page_dot_column = '^/(?!0+\.)(\d{1,4})\.(\d{1,2})$'
page_dot_column = '^(?!0+\.)(\d{1,4})\.(\d{1,2})$'
prefixed_slash = '^(=[A-Z0-9]+)/(?!0+\.)(\d{1,4})\.(\d{1,2})$'
page_slash_column = '^(?!0+/)(\d{1,4})/(\d{1,2})$'
# F3 (gap 23): a location: page, dot, column and row (27.6E), or the same page (.6E).
page_dot_location = '^(?!0+\.)(\d{1,4})\.(\d{1,2}[A-Z])$'
same_page_location = '^\.(\d{1,2}[A-Z])$'

# F3 (gaps 1, 18, 30): vendor wire-number patterns, read ONLY when a profile names them. A named
# pattern that matches wins over an ANSI reading without context, as a pinned cross-reference
# format does; the ANSI reading is kept as an alternative.
[wire_numbers_opt_in]
digits_letter = '^\d{1,3}[A-Z]$'
letter_digits = '^[A-Z]\d{1,3}$'

# F3 (gaps 4, 15): a number with one of these units is a rating, never an identifier (FR-ID-20).
# Written in SI case. A unit whose upper-case form is a letter code an enabled table has (KV, KA)
# matches only as written: 37kV is a rating, 3KA is a relay.
[ratings]
units = ["kV", "VA", "kVA", "MVA", "kW", "MW", "var", "kvar", "Mvar", "Hz", "kA", "mA", "VDC", "VAC", "PH"]

# F3 (FR-ID-19): lists and ranges in one text, expanded into identifiers.
[groups]
max_members = 64
# separator -> the kinds the FIRST item must read as; a later item of digits only replaces the
# first item's trailing digits (20XYZ10AB131/2/3 -> ...131, ...132, ...133)
suffix_lists = { "/" = ["kks_tag"], "\\" = ["letter_code"] }
```

**الجداول الإلزامية والاختيارية:**
- **إلزامية:** `[ratings]` و`[groups]`، ومفتاحا `[kks]` الجديدان. لأنها قواعد أمان، فغيابها يجب أن يُفشل الإقلاع، على سابقة `evidence_min_level` (§11.1 في F2).
- **اختيارية:** `[wire_numbers_opt_in]` و`[terminals.letters_first]`. وغيابهما = سلوك اليوم، على سابقة `[terminals.digits_first]`.

**التحقق** (`rules/systems.py`، دوال جديدة على نهج `ansi_from_mapping`):

| الجدول | القارئ | القواعد، والخطأ `RuleError` برسالة ثابتة لا تعيد القيمة |
|---|---|---|
| `[ratings]` | `ratings_from_mapping(data) -> RatingRules` | `units` قائمة غير فارغة، 1..32 عنصراً، كل عنصر 1..5 أحرف ASCII، فريدة بلا اعتبار للحالة |
| `[groups]` | `groups_from_mapping(data) -> GroupRules` | `max_members` من 2 إلى 64. و`suffix_lists` مفاتيحها من `/` و`\` وحدهما، وقيمها قوائم غير فارغة من أسماء `IdentifierKind` لا `identifier_group`. ومفتاح مجهول مرفوض |
| `[terminals.letters_first]` | `digits_first_from_mapping` نفسه، بمفتاح ثانٍ | المفاتيح من `[terminals] prefixes`، والقيم اصطلاحات أجهزة فريدة، كما في A‑5 |
| `[kks]` | `kks_from_mapping` | `part_separators` قائمة، كل عنصر حرف واحد من `-` و`.`، فريدة، وقد تكون فارغة. و`near_miss_min_length` من 1 إلى 32 |
| `[wire_numbers_opt_in]` | `_string_map` و`compiled` كما `[wire_numbers]` | الاسم لا يتكرر بين الجدولين، والتعبير يُترجَم |

```python
# rules/systems.py
@dataclass(frozen=True, slots=True)
class RatingRules:
    units: tuple[str, ...]  # as written in the file (SI case)
    folded_units: frozenset[str] = field(init=False, repr=False, compare=False)  # upper-cased


@dataclass(frozen=True, slots=True)
class GroupRules:
    max_members: int  # 2..64
    suffix_lists: Mapping[str, frozenset[IdentifierKind]]  # "/" -> {KKS_TAG}


@dataclass(frozen=True, slots=True)
class KksRules:
    ...  # as today
    part_separators: frozenset[str] = frozenset()  # F3
    near_miss_min_length: int = 7  # F3: replaces `shortest` in the near-miss gate


def ratings_from_mapping(data: Mapping[str, object]) -> RatingRules: ...
def groups_from_mapping(data: Mapping[str, object]) -> GroupRules: ...
```

```python
# rules/__init__.py: IdentifierRules gains, last and defaulted
    opt_in_wire_number_patterns: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )  # F3: read only when a profile names them
    wire_number_pinned: bool = False  # F3: the profile named its wire-number patterns
    terminal_letters_first: Mapping[str, tuple[Convention, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    ratings: RatingRules = field(default_factory=lambda: RatingRules(()))
    groups: GroupRules = field(default_factory=lambda: GroupRules(64, MappingProxyType({})))

    @property
    def wire_number_pattern_names(self) -> frozenset[str]:
        """Every name a profile may select: the default patterns and the opt-in ones."""
```

- **`from_mapping`** يملأ الحقول الخمسة. ويبقى `wire_number_patterns` الأنماط الافتراضية وحدها. فالقواعد دون تضييق، وهي ما يعدّ به `tally` (`inspection.py:626`) وحالات M‑04 بلا `select`، لا تقرأ نمطاً اختيارياً أبداً.
- **`narrowed`** (`rules/__init__.py:178-182`):
  - يختار الأسماء من اتحاد الجدولين.
  - و`wire_number_pinned=True` حين يسمّي الملف أنماطاً.
  - ولا يتغيّر الباقي.
- **`RuleSelection` لا يتغيّر.**

### 3.4 ANSI بلا سياق (`shared/ansi.py`، F3‑Q1 وF3‑Q2)

**القاعدة (التوصية F3‑Q1 (ج)):**
1. **بلا `hint`:** رقم ANSI بلاحقة أو مثيل يُقرأ جهازاً فقط إن كان لرقمه فئة في `[ansi.functions]`. والمركّب يُقرأ جهازاً فقط إن كان لكل رقم فيه فئة.
2. **ومع `hint=ANSI_DEVICE`** (فقاعة، أو تسمية مكدّسة، أو جدول دليل: STG‑05 وSTG‑04 في INC‑2): القراءة كما اليوم، بلا شرط الفئة.
3. **نمط السلك المسمّى في الملف يتقدّم:** حين يكون `wire_number_pinned` ويطابق نمطٌ مسمّى النصَّ، لا تُقرأ ANSI بلا سياق. فيُقرأ سلكاً، وقراءة ANSI بديل. وهذا نهج `cross_reference_pinned` نفسه.
4. **لاحقة بعدها رقم واحد** (فج‑32): مثيل بلا شرطة. `59G2` = 59، `G`، المثيل 2. واللاحقة والرقم والشرطة معاً (`59G2-1`) لا معرّف.

**لماذا تكفي هذه القاعدة الحالة الصعبة.** FR‑ID‑16 يطلب «أن يُعطى الرقم فئة وظيفية من جدول في ملف القواعد»، ويمنع الرقم المجرد بلا دليل. وهذه القاعدة تمدّ منع الرقم المجرد إلى الرقم الذي لا فئة له. فأرقام الأسلاك والقيم والمديات والأقطاب (`38Y`، `8P`، `23-4`، `16F`) أرقامها خارج الجدول. وأشكال ANSI الحقيقية الشائعة فيه (`87T`، `50BF`، `51N`، `27-1`، `50/51`). وقاعدة الدليل في F2 (`is_ansi_evidence`) تشترط الفئة أصلاً، فلا يتغيّر الاكتشاف.

```python
_SINGLE: Final = re.compile(
    r"(?P<num>[1-9][0-9]?)(?:(?P<suffix>[A-Z]{1,3})(?P<ord>[1-9])?)?(?:-(?P<inst>[1-9][0-9]?))?",
    re.ASCII,
)


def parse_ansi_compound(canonical, forms, rules) -> ParsedIdentifier | None:
    """As today, and None when a function has no class (F3: no context, no device)."""


def _single(canonical, forms, rules, *, bare: bool) -> ParsedIdentifier | None:
    """``bare`` is the in-context path (a hint). Without it (F3): None when the number has no
    class, or when a wire-number pattern the profile named reads the text (it is a wire)."""
```

- **`_alternatives` (`parser.py:102`):** البديل `ansi_device` لرقم السلك ونقطة الاتصال يُحسب بـ`parse_ansi_device_in_context(...) is not None` بدل `is_bare_ansi_number`. فالرقم المجرد كما اليوم، ويُضاف السلك الذي قرأه نمط مسمّى (`38Y`).
- والمرجع المتقاطع يحمل البديل `ansi_device` كما اليوم، لأن `read_compound` لا يشترط الفئة.
- **ما يتغيّر من حالات M‑04:** `c226` وحدها:
  - النص `12/3` وANSI مفعّل: `ansi_device` ببديل `cross_reference` ← `cross_reference` (`page_slash_column`) ببديل `ansi_device`، مثل `c227`.
  - وكل حالة أخرى كما هي: `87T` و`50BF` و`51N` و`27-1` و`50/51` و`50BF/51N` و`87QZ` (لها فئات)، و`100T` و`00` و`05` بلا معرّف.
- **الاختبارات التي تتغيّر** (يسردها المطوّر في جسد الـPR):
  - صفوف `16A` و`4P` و`2P` و`1N` و`24V` في `test_electrical_shared_ansi.py::test_the_ansi_evidence_rule` تُقرأ بـ`hint=ANSI_DEVICE`، ويبقى الحكم «ليس دليلاً».
  - و`test_electrical_shared_parser.py:203`: ANSI المفعّل لا يجعل أي حالة قائمة `ansi_device`. و`12/3` يكسب البديل وحده.
  - وأي اختبار يقرأ رقماً بلا فئة جهاز ANSI بلا `hint`.

### 3.5 قيم التقدير (`shared/ratings.py`، جديد)

```python
@dataclass(frozen=True, slots=True)
class Rating:
    value: str  # "37", "0.7", "7-29" as written (DATA-05 converts it, later)
    unit: str  # the rules' spelling: "kV"


def read_rating(folded: str, rules: IdentifierRules) -> Rating | None:
    """F3 (FR-ID-20): a number, or two joined by - or ~, then a unit of [ratings]. ``folded`` is
    the text NFKC-folded WITHOUT upper-casing (``normalization.folded``). A unit whose upper-case
    form is a letter code in an enabled table matches only in the rules' own case; any other
    unit matches in any case. Linear: one bounded pattern, then a set lookup."""
```

- **الحاجز** (`parser.py`، في `_first` حين `hint is None`، قبل `_ORDER`):
  - أولاً: النص في `rules.voltage_labels` يمضي إلى ترتيب اليوم. فـ`24VDC` و`+24V` و`0V`، وما أضافه الملف، تبقى جهوداً (FR‑ID‑08).
  - ثانياً: `read_rating(folded(text), rules) is not None`، فلا معرّف.
- **الشكل:** `^\d{1,6}(?:\.\d{1,3})?(?:[-~]\d{1,6}(?:\.\d{1,3})?)?(?P<unit>[A-Za-z]{1,5})$`. ولا يدخل التعبيرَ شيءٌ من البيانات، فالوحدة تُقارن بالعضوية (NFR‑F2‑3).
- **`normalization.folded(text) -> str`** (جديد): الطيّ كما في `canonical_form`، بلا تحويل إلى الحروف الكبيرة.
- **لا وحدة من حرف واحد** (`A`، `V`، `W`، `P`): كلها لواحق ANSI أو أسلاك أو جهود. وقاعدة الفئة (§3.4) تحلّ `16A` و`4P` و`8P`.
- **`_other_kind` لتسميات الجهد** (`rules/__init__.py:252`): قيمة التقدير لا تُقرأ نوعاً آخر، فتُقبل تسميةَ جهد. ولا تغيير في الشيفرة.

### 3.6 القوائم والنطاقات (`shared/groups.py`، جديد، وفي `parser.py`)

```python
ISSUE_MEMBER_UNREADABLE: Final = "group.member_unreadable"
ISSUE_MEMBER_INVALID: Final = "group.member_invalid"
ISSUE_TOO_MANY: Final = "group.too_many_members"
ISSUE_EMPTY_MEMBER: Final = "group.empty_member"
LIST_SEPARATOR: Final = ","  # after normalisation: "，" by NFKC, "、" by the new fold
RANGE_SEPARATOR: Final = "-"


def list_items(canonical: str) -> tuple[str, ...] | None:
    """Two or more items split at ",", or None. Empty items are kept (as "") for the issue."""


def range_members(canonical: str, max_members: int) -> tuple[tuple[str, ...], bool] | None:
    """``HEAD<n>TAIL-HEAD<m>TAIL`` with one "-", the same HEAD (not ending in a digit) and TAIL
    (letters), HEAD+TAIL not empty, n < m: the members HEAD<k>TAIL for k in n..m, zero-padded
    to n's width. (members, too_many); when m-n+1 > max_members nothing is expanded. None when
    the text is no range: it does not start with an aspect, and the readings of today (X1-14,
    L1-5, 27-1, 12.3-1, -K5-13, 1X-25) are no range by construction."""


def inherit(first: str, point: str, label: str) -> str:
    """A list's later bare label takes the first item's head: ("X7:41", "41", "42") -> "X7:42"."""


def suffix_members(first: str, tails: tuple[str, ...]) -> tuple[str, ...] | None:
    """Each tail of digits replaces as many trailing digits of ``first``; None when a tail is
    not digits, or is longer than ``first``'s trailing digit run."""
```

**التجميع في المحلّل** (`IdentifierParser._group(forms, text) -> ParsedIdentifier | None`، بلا `hint` وحده، بعد حاجز التقدير وقبل `_ORDER`):

1. **النطاق** إن طابق `range_members`.
   - يُحلَّل كل عنصر بـ`_first` بلا مجموعات. فالعنصر لا يكون مجموعة، والعمق 1.
2. **القائمة** إن طابقت `list_items`:
   - يُحلَّل البند الأول. فإن لم يُقرأ، فلا مجموعة.
   - وكل بند تالٍ يُحلَّل وحده. فإن لم يُقرأ، وكان للأول `connection_point`، وكان البند تسميةً صالحة (`is_valid_label`)، صار `inherit(...)`.
   - وإلا فالعنصر بلا قراءة.
3. **القائمة اللاحقة** حين يرد `/` أو `\`:
   - يُحلَّل البند الأول، ويُشترط أن يكون نوعه في `rules.groups.suffix_lists[separator]`.
   - ثم `suffix_members(...)`.
   - وإلا يمضي النص إلى `_ORDER` كما اليوم. فـ`50/51` و`12/3` و`=A1/12.3` لا تتغيّر.

- **الحدود:**
  - `max_members` (64): يُفحص قبل أي توليد، فـ`QS7-QS99` لا يولّد شيئاً.
  - والنص كله ≤ 200 حرف، كما اليوم.
  - والتقسيم خطّي.
- **في `tally`:** كل عنصر قُرئ قراءةٌ، يُعدّ اصطلاحه ودليله كما لو جاء وحده (`_readings(found)`). وكذلك في المسبار. فـ`X7:41,42,43` ثلاثة أدلة IEC. وأثره على التوصيف: `conventions` تعدّ العناصر، ولا حقل جديد.

### 3.7 التقطيع والتطبيع (`shared/tokens.py` جديد، و`normalization.py` و`parser.py`)

```python
# shared/tokens.py: stdlib only, a leaf module
SENTENCE_BREAKS: Final = frozenset("。｡；;")  # 。 ｡ ； ;
OPENERS: Final = "([{（【「『《〈\"'“‘"
CLOSERS: Final = (
    ")]}）】」』》〉\"'”’"
    ".,;:，、。；：｡"
)
PRIMES: Final = frozenset("'\"′″")


def split_candidates(word: str) -> tuple[str, ...]:
    """The identifier candidates a drawing's word holds (F3, gap 34): split at whitespace and at
    SENTENCE_BREAKS, then strip OPENERS from the front and CLOSERS from the end of each piece;
    a prime right after a digit stays (1X-37'). Empty pieces are dropped. A list (X7:41,42,43)
    stays one candidate: "," and "、" separate a list's items, not words. Linear."""
```

- **`normalization.py`:**
  - جدول طيّ ثانٍ بجانب `_DASHES` (`normalization.py:31`): `、` (U+3001) و`﹑` (U+FE51) و`､` (U+FF64) تصير `,`. و`′` (U+2032) تصير `'`، و`″` (U+2033) تصير `"`.
  - و`has_enclosed_number(text) -> bool`: U+2460..U+24FF، وU+2776..U+2793، وU+3251..U+325F، وU+32B1..U+32BF، وU+1F100..U+1F10C.
- **`parser.parse`:** بلا `hint`، النص الذي فيه رقم مدوّر لا معرّف (فج‑14)، قبل `normalize`. ومع `hint` كما اليوم.
- **`tally`:**
  - يمرّ على `split_candidates(token)` لكل رمز.
  - و`tokens` يبقى عدد الرموز التي رآها.
  - و`_may_be_additive` على كل مرشّح.
- **الشرطة العليا في الطرف** (فج‑27):
  - `connection_points._LABEL` (`connection_points.py:13`) يصير `[A-Z0-9]{1,6}['"]?`.
  - ويُضاف `'` و`"` إلى `terminals._CHARSET` (`terminals.py:38`).
  - ولا تتغيّر `_PROSE` في `reference_designation.py` ولا أنماط أكواد الحروف. فالأثر على الأطراف والجسور وحدها.
  - ولا مفتاح بحث بلا الشرطة العليا: `37'` طرف آخر غير `37`.

### 3.8 عيّنة الاكتشاف (`services/ele_parse/inspect.py`، F3‑Q4)

**التوصية (ج):** لا يتغيّر الحدّ ولا العقد. تتغيّر طريقة الملء وحدها:

```python
# services/ele_parse/contract.py
LONG_TOKEN_SPLIT: Final = frozenset(",，、。；;")  # , ， 、 。 ； ;


# services/ele_parse/inspect.py
def _tokens(words: list[Any], sample: int) -> list[str]:
    """Distinct tokens, at most ``sample``, each at most ``MAX_TOKEN_CHARS``: first the tokens
    with a digit in reading order, then the others in reading order (F3: identifiers almost
    always hold a digit). A word longer than ``MAX_TOKEN_CHARS`` is split at LONG_TOKEN_SPLIT
    first, and its pieces that fit are kept; a long word without one is skipped, as today."""
```

- **السبب:** على المورد الثاني تصل العيّنة اليوم إلى 55% من المعرّفات، وإلى أقل من الخُمسين في صفحة (`measure-3.md` §4).
  - والمعرّفات كلها تقريباً فيها رقم. والاستثناء أسماء من حروف وحدها (`A`، `AA`)، وهي لا تُقرأ اليوم أصلاً.
  - فملء العيّنة بها أولاً يوصلها دون تكبير الحمولة.
  - ولا يمسّ تصنيف الصفحة: عدّ الحروف من الصفحة كلها (`inspect.py:120`).
- **لا يتغيّر:**
  - `ELE_PARSE_TOKEN_SAMPLE` (200)، و`MAX_TOKEN_CHARS` (32)، و`MAX_PAGE_JSON_BYTES`.
  - و`docker-compose.yml` و`.env.example`.
  - والعميل (`ele_parse_client.py`) وشكل `PageSignals`.
- **الإصداران:** `SERVICE_VERSION` في `services/ele_parse/__init__.py` من `1.0.0` إلى `1.1.0`. و`PIPELINE_VERSION` في `drawings/domain/pipeline.py` من `1.1.0` إلى `1.2.0`، لأن ناتج STG‑01 (العدّ والاقتراح) يتغيّر للملف نفسه.
- **ويعمل التغيير بعد إعادة بناء `aizzak/ele-parse:dev`**، وهي قرار بشري كما في F0.

### 3.9 الأطراف تحت اصطلاح الأجهزة الصيني (`shared/terminals.py`، F3‑Q6)

- **اليوم** (`terminals.py:106-111`):
  - في الصيغة التي يسبق فيها الرمزُ الرقم، `XT` صيني، و`TB` NFPA، وغيرهما IEC ثم GB/T. والقاعدة في الشيفرة.
  - فملف إعداد صيني لا يقرأ `X7:41` (`measure-3.md` §4: الأطراف أدلة IEC، والأسماء أدلة صينية).
- **بعد:**
  - `convention = pick_convention(rules, *rules.terminal_letters_first.get(prefix, _LETTERS_FIRST_DEFAULT[prefix]))`.
  - و`_LETTERS_FIRST_DEFAULT` هو ما في الشيفرة اليوم، فالجدول الغائب = سلوك اليوم.
  - والجدول المعبّأ (§3.3) يضيف `cn_letter_codes` في آخر قائمة `X`.
- **أثره على الاكتشاف:** لا شيء. فالعدّ بالقواعد دون تضييق، وأول اصطلاح مفعّل في القائمة IEC، كما اليوم. أما ملف إعداد مؤكَّد بالصيني فيقرأ الأطراف والأسماء معاً.
- **وصيغة السلسلة** (`-X1:14`) تبقى IEC وGB/T وحدهما.

### 3.10 KKS (`shared/kks.py`، F3‑Q3)

- **الشرطة بين مستويين:**
  - حين يرد في `rest` حرف واحد من `rules.kks.part_separators` مرة واحدة، يُطابَق النص بدونه.
  - ويُقبل الوسم إن وقع الحرف على حدّ بين جزأين من الصيغة المطابِقة. وإلا فلا معرّف.
  - والشكل القانوني بالشرطة كما كُتب (DATA‑06)، ومفاتيح البحث: الشكل القانوني، ثم النص بلا شرطة. فيلتقي الشكلان في البحث، والهوية كما يطلب `normalization.py` (S‑6).
  - ويتسع `_CHARSET` (`kks.py:39`) لحرف الفصل الواحد. ويُبنى من الحروف المتحقَّق منها (`-` أو `.`) بـ`re.escape`.
- **حدّ الشبيه:** `len(rest) < kks.near_miss_min_length` بدل `kks.shortest` (`kks.py:149`). فصيغة `system_code` القصيرة لا توسّع وسم «الشبيه غير الصالح» إلى نصوص من خمسة أحرف.
- **الصيغتان الجديدتان** بيانات وحدها (§3.3). والدليل كما هو: `system_code` بالمستوى 1، فليس دليلاً (`evidence_min_level = 2`).

### 3.11 ملف الإعداد والتطبيق (`drawings`)

- **`drawings/application/projects.py:67`:** `known_patterns=frozenset(packaged.wire_number_pattern_names)`. وهذا السطر وحده.
- **`drawings/domain/profile.py`:** لا يتغيّر. لا حقل جديد، و`to_body` والبصمة كما هما.
- **`drawings/adapters/sql_repository.py:454`** يثق بأسماء الجسد المخزَّن، فلا يتغيّر.
- **`inspection.py`** و`profile_detection.py` و`characterization.py`: لا تغيير. فالعدّ يتغيّر من داخل `shared`.
- **صيغتا الموقع** لا تدخلان `CrossReferenceFormat`. فالاكتشاف لا يقترح إلا أعضاءه (`profile_detection.py:233-236`)، ويبقى `auto` حين تغلب الإشارات.

### 3.12 import-linter

- **لا عقد جديد ولا `ignore_imports`.**
  - `ratings.py` و`groups.py` و`tokens.py` وحدات في `app.modules.electrical.shared`، تستورد المكتبة القياسية و`shared` وحده، فيحرسها `domain-purity`.
  - و`tokens.py` ورقة بلا استيراد من النواة.
  - و`ansi.py` يستورد `wire_numbers` كما يستورد `cross_references` اليوم، بلا دورة.
- **لا حزمة جديدة**، فلا يتغيّر عدد ملفات `__init__.py` الذي يثبّته `test_the_skeleton_invariants_hold`.
- **`services/ele_parse`** لا يستورد `app`، ولا يستورده `app`.

## 4. البيانات

**لا تغيير في قاعدة البيانات، ولا ترحيل Alembic، ولا RLS جديد، ولا مستودع جديد.** فلا عمل لـ`data-engineer`، ولا اختبار RLS جديد.

- **جسد ملف الإعداد** (JSONB) لا يكسب مفتاحاً. والجسد الذي يسمّي `digits_letter` أو `letter_digits` يُقرأ بأسمائه (`sql_repository.py:454`).
- **العدّ المخزَّن لكل صفحة:** شكله كما هو.
  - المهام الجديدة تكتب أعداداً بالقواعد الجديدة، والقديمة تبقى.
  - والتوصيف يسرد كل إصدار رآه (`characterization.py:198-202`)، فالمجموعة المختلطة تقول ذلك.
- **البصمات (NFR‑REP‑01):**
  - كل سجلّ توصيف بعده يحمل `shared` = `1.4.0` و`rules` = `1.2.0` و`pipeline` = `1.2.0`.
  - وبصمة ملف الإعداد نفسه لا تتغيّر.
- **إعادة المعالجة:** لا حقائق معرّفات مخزّنة بعد (STG‑05 في INC‑2)، فلا شيء يبطل. وإعادة معالجة الوثائق القديمة لتحديث اقتراحها قرار بشري (`POST /api/v1/electrical/documents/{id}/reprocessing`).
- **التوافق:** غير كاسر للأمام.

### 4.1 الرجوع والنشر المتدرّج

أصغر من F2 §4.1، لأن الاكتشاف لا يكتب `wire_number_patterns` أبداً (`with_detected`، `profile.py:283-307`). فالأسماء الجديدة لا تدخل جسداً إلا بيد مستخدم.

**أ. النشر:**
- `app` أولاً، ثم `worker-electrical` (`deploy/rolling-deploy.sh --service …`)، ثم `ele-parse` بعد إعادة بناء صورته.
- ولا يرسل أحد `digits_letter` ولا `letter_digits` قبل أن تصير كل نسخ `app` جديدة.

**ب. قبل الرجوع إلى الصورة القديمة:**
1. **حصر المعنيّين، للقراءة فقط.** بدور يتجاوز RLS، ولا يُطبع الجسد:

   ```sql
   BEGIN READ ONLY;
   SELECT workspace_id, project_id, document_id, version
   FROM (
     SELECT DISTINCT ON (workspace_id, project_id, document_id)
            workspace_id, project_id, document_id, version, body
     FROM electrical.drawings_profile_versions
     ORDER BY workspace_id, project_id, document_id, version DESC
   ) AS latest
   WHERE latest.body -> 'wire_number_patterns' ?| ARRAY['digits_letter', 'letter_digits']
   ORDER BY workspace_id, project_id, document_id;
   ROLLBACK;
   ```

2. **لكل صف:** `PUT` بالجسد الحالي بعد حذف الاسمين، على الصورة الجديدة، كما في F2 §4.1 الخطوة 3.
3. **وإن بقي صف:** الصورة القديمة تقرأ الجسد. لكن تعديله أو تأكيده يعطي 422 باسم `wire_number_patterns[i]` حتى يُحذف الاسم. فلا ينكسر قارئ.
4. **العدّ المخزَّن بالقواعد الجديدة** تقرؤه الصورة القديمة كما هو: الشكل واحد.

## 5. معايير القبول وخطة الاختبار

**كل اختبار هنا وحدة أو معماري أو تقييم،** ويعمل في `pytest tests/unit tests/architecture tests/eval` بلا خدمات حية. والتكامل لا يتغيّر.

**الأمثلة كلها مخترعة.** و«ANSI مفعّل» = `parser_for(<اصطلاح>, KKS, ANSI)` من `tests/unit/support_electrical_shared.py`.

### 5.1 المعايير

**ANSI بلا سياق (فج‑1..3، و5..8، و18، و19، و30، و32):**
- **F3‑AC‑1** — **بافتراض** ANSI مفعّل مع IEC 81346، **عندما** تُحلَّل بلا `hint` `8Y` و`8P` و`11R` و`16F` و`23-4` و`4-23`، **إذن** لا معرّف. **وعندما** يُحلَّل `23/4`، **إذن** `cross_reference` ببديل `ansi_device`. **وعندما** تُحلَّل كلها مع `hint=ANSI_DEVICE`، **إذن** كلٌّ `ansi_device` بلا فئة، و`16F` غير صالح بسبب `ansi.unknown_suffix`. (وحدة)
- **F3‑AC‑2** — **بافتراض** القواعد نفسها، **عندما** تُحلَّل `87T` و`50BF` و`51N` و`27-1` و`50/51` و`87QZ`، **إذن** القراءة كما في F2 حرفاً. **وعندما** تُشغَّل حالات M‑04 كلها بـANSI مفعّل ومعطّل، **إذن** لا تتغيّر إلا `c226` (`12/3`)، فتصير مرجعاً متقاطعاً ببديل `ansi_device`. (وحدة وتقييم)
- **F3‑AC‑3** — **بافتراض** ملف إعداد يسمّي `sequential` و`digits_letter` وANSI مفعّل، **عندما** يُحلَّل `38Y`، **إذن** `wire_number` بصيغة `digits_letter` وبديل `ansi_device`. **وعندما** يُحلَّل `59Y` (59 له فئة)، **إذن** `wire_number` أيضاً. **وبافتراض** ملف لا يسمّي `digits_letter`، `38Y` لا معرّف و`59Y` `ansi_device`. **وبافتراض** القواعد دون تضييق، **عندما** تُشغَّل حالات M‑04 بلا `select`، **إذن** لا تتغيّر حالة. (وحدة)
- **F3‑AC‑4** — **بافتراض** `PUT …/profile` بـ`wire_number_patterns: ["sequential", "digits_letter", "letter_digits"]`، **عندما** يُحفظ، **إذن** إصدار جديد، والجسد بأسمائه مرتّبة. **وعندما** يحمل اسماً مجهولاً، **إذن** 422 `electrical.invalid_profile` يبدأ بـ`wire_number_patterns[i]`، ولا يعيد الاسم، كما اليوم. (وحدة على الموجّه)
- **F3‑AC‑5** — **بافتراض** NFPA/JIC وANSI مفعّلان، **عندما** يُحلَّل `3X-41` بلا `hint`، **إذن** لا معرّف. **وبافتراض** IEC 81346 وANSI، **إذن** طرف `iec_81346`، كما اليوم. (وحدة)
- **F3‑AC‑6** — **بافتراض** ANSI مفعّل، **عندما** يُحلَّل `59G2`، **إذن** `ansi_device` صالح: الرقم 59 بفئته، واللاحقة `G`، والمثيل 2. **وعندما** يُحلَّل `14B6` بلا `hint`، **إذن** لا معرّف، ومعه `hint` يُقرأ ANSI بلا فئة. **وعندما** يُحلَّل `59G2-1`، **إذن** لا معرّف. (وحدة)

**قيم التقدير (فج‑4، و9، و15):**
- **F3‑AC‑7** — **بافتراض** كل الاصطلاحات وKKS وANSI مفعّلة، **عندما** تُحلَّل بلا `hint` `37MVA` و`47Hz` و`17VA` و`29kA` و`2PH` و`7KW` و`13mA` و`7-29kV` و`0.7kV`، **إذن** لا معرّف. **وبافتراض** أكواد الحروف الصينية، **عندما** يُحلَّل `37kV`، **إذن** لا معرّف. **وعندما** يُحلَّل `3KA` أو `37KV`، **إذن** `letter_code` كما اليوم، لأن الوحدة التي يطابق شكلها الكبير كوداً تُطابَق بحالتها المكتوبة. (وحدة)
- **F3‑AC‑8** — **بافتراض** القواعد نفسها، **عندما** تُحلَّل `24VDC` و`+24V` و`0V`، أو تسمية جهد أضافها الملف مثل `12VDC`، **إذن** `voltage`. (وحدة)
- **F3‑AC‑9** — **بافتراض** خريطة القواعد المعبّأة بلا `kVA` في `units`، **عندما** يُحلَّل `37KVA` وKKS مفعّل، **إذن** القراءة كما قبل الحاجز. **وبإعادة الوحدة**، لا معرّف. **وعندما** تكون وحدة بحرف غير ASCII، أو بستة أحرف، أو مكرّرة بلا اعتبار للحالة، أو 33 وحدة، أو يغيب الجدول، **إذن** `RuleError` برسالة لا تعيد القيمة. (وحدة، NFR‑F3‑3)
- **F3‑AC‑10** — **بافتراض** القواعد دون تضييق، **عندما** تُحلَّل `0.7` و`0.9` و`/0.7`، **إذن** لا معرّف. **وعندما** تُحلَّل `12.3` و`/12.3` و`=A1/12.3` و`12/3`، **إذن** كما اليوم. (وحدة وتقييم)

**القوائم والنطاقات (فج‑10، و22، و24، و25، و29):**
- **F3‑AC‑11** — **بافتراض** IEC 81346، **عندما** يُحلَّل `X7:41,42,43`، **إذن** `identifier_group` صالح، بصيغة `list` وفاصل `,`، وعناصره `X7:41` و`X7:42` و`X7:43`، كلٌّ طرف `iec_81346`. ومفاتيح بحثه تضمّ الأطراف الثلاثة. **وعندما** يُحلَّل `X7:41，42，43` أو `X7:41、42、43`، **إذن** الشكل القانوني نفسه والعناصر نفسها، والأصل محفوظ. (وحدة)
- **F3‑AC‑12** — **بافتراض** أكواد الحروف الصينية، **عندما** يُحلَّل `QS7,QS8`، **إذن** قائمة بكودين. **وعندما** يُحلَّل `QS7-QS9`، **إذن** نطاق بثلاثة. **وبافتراض** ملف يسمّي `digits_letter`، `38Y-43Y` نطاق بستة أسلاك. **وبافتراض** IEC 81346، **عندما** يُحلَّل `X23-X37`، **إذن** نطاق بخمسة عشر عنصراً بلا قراءة، و`valid=False` بسبب `group.member_unreadable`، ولا يعود طرفاً. (وحدة)
- **F3‑AC‑13** — **بافتراض** KKS مفعّل، **عندما** يُحلَّل `20XYZ10AB131/2/3`، **إذن** `suffix_list` بثلاثة وسوم KKS: `20XYZ10AB131` و`20XYZ10AB132` و`20XYZ10AB133`. **وبافتراض** أكواد الحروف الصينية، `QS7\8` قائمة لاحقة بـ`QS7` و`QS8`. **وبافتراض** ANSI غير مفعّل، يبقى `12/3` مرجعاً متقاطعاً كما اليوم. (وحدة)
- **F3‑AC‑14** — **بافتراض** أكواد الحروف الصينية، **عندما** يُحلَّل `QS7-QS99` (93 عنصراً)، **إذن** مجموعة غير صالحة بسبب `group.too_many_members` وبلا عناصر. **وعندما** يُحلَّل `QS9-QS7`، **إذن** لا مجموعة. **وعندما** يُحلَّل `X7:41,,42`، **إذن** غير صالحة بسبب `group.empty_member`. وزمن 1,000 نص من 200 حرف بهذه الأشكال محدود (QA). (وحدة)
- **F3‑AC‑15** — **بافتراض** القواعد دون تضييق ثم كل اصطلاح، **عندما** تُحلَّل `X1-14` و`L1-5` و`27-1` و`12.3-1` و`-K5-13` و`1X-25` و`X1:1~X1:2`، **إذن** القراءة كما اليوم. ولا تتغيّر حالة M‑04 إلا `c226`. (وحدة وتقييم)
- **F3‑AC‑16** — **بافتراض** كيس فيه `X7:41,42,43` وحده، **عندما** يُعدّ بـ`tally`، **إذن** `iec_81346` = 3، و`identifiers` = 3، و`tokens` = 1. **وعندما** يُبنى سجلّ توصيف منه، **إذن** `conventions` تحمل 3. (وحدة)

**التقطيع والتطبيع (فج‑14، و27، و34، و40):**
- **F3‑AC‑17** — **عندما** تُعطى `split_candidates` الكلمات `QS7。` و`QS7；QS8` و`(20XYZ)` و`（QS7）` و`1X-37'` و`X7:41，42` و`QS7 QS8`، **إذن** تعيد على الترتيب: (`QS7`)، و(`QS7`، `QS8`)، و(`20XYZ`)، و(`QS7`)، و(`1X-37'`)، و(`X7:41，42`)، و(`QS7`، `QS8`). (وحدة)
- **F3‑AC‑18** — **عندما** يُحلَّل `⑰` أو `⑳` بلا `hint`، **إذن** لا معرّف. **وعندما** يُحلَّل `QS７` (رقم كامل العرض)، **إذن** `QS7` كما اليوم. (وحدة)
- **F3‑AC‑19** — **بافتراض** IEC 81346، **عندما** يُحلَّل `1X-37'` و`1X-37"` و`1X-37′`، **إذن** طرف صالح، ونقطته `37'` أو `37"`. والشكل القانوني غير شكل `1X-37`، ولا يلتقيان في مفتاح. **وعندما** يُحلَّل `-K5:13'`، **إذن** كما اليوم. (وحدة)
- **F3‑AC‑20** — **بافتراض** ملف إعداد اصطلاحه أكواد الحروف الصينية، **عندما** يُحلَّل `X7:41`، **إذن** طرف `cn_letter_codes`. **وبافتراض** القواعد دون تضييق، يبقى `iec_81346`. **وعندما** يذكر `[terminals.letters_first]` رمزاً أو اصطلاحاً مجهولاً أو قائمة فارغة، **إذن** `RuleError`. **وبلا الجدول**، سلوك اليوم. (وحدة، NFR‑F3‑3)

**KKS والإشارات (فج‑16، و17، و23، و33):**
- **F3‑AC‑21** — **بافتراض** KKS مفعّل:
  - `20XYZ` و`(20XYZ)` بعد `split_candidates`: `kks_tag` صالح، بصيغة `system_code` وصنف `system` والمستوى 1. **وعندما** يُعدّ 30 مرة بـ`tally`، **إذن** لا دليل KKS.
  - `20XYZ10AB123-CD45`: `kks_tag` مكوّن صالح، وشكله القانوني بالشرطة، ومفتاح بحثه `20XYZ10AB123CD45`. و`20XYZ10-AB123` وسم معدّة صالح كذلك.
  - `20XYZ1-0AB123` (الشرطة داخل جزء): لا معرّف.
  - `20XYZ10AB123CD456`: مكوّن صالح بصيغة `component_wide` (F3‑Q3 (أ)).
  - `1AB12`: لا معرّف كما اليوم.
  - (وحدة)
- **F3‑AC‑22** — **بافتراض** القواعد دون تضييق، **عندما** يُحلَّل `27.6E` و`.6E`، **إذن** `cross_reference` بصيغة `page_dot_location` و`same_page_location`. **وعندما** يُحلَّل `0.6E`، **إذن** لا معرّف. **وعندما** يُقترح ملف إعداد لكيس أغلب مراجعه `27.6E`، **إذن** يبقى `cross_reference_format` = `auto`. (وحدة)

**الاكتشاف (فج‑38، و39):**
- **F3‑AC‑23** — **بافتراض** صفحة كلماتها 300 كلمة مختلفة، ومنها 150 بلا رقم قبل 150 فيها رقم، **عندما** تُحسب `_tokens` بعيّنة 200، **إذن** الـ150 التي فيها رقم كلها، بترتيب القراءة، ثم أول 50 من غيرها. ولا تكرار، وكلٌّ ≤ 32 حرفاً. (وحدة، `test_ele_parse_service.py`)
- **F3‑AC‑24** — **عندما** تحوي الصفحة كلمة من 60 حرفاً هي أربعة وسوم KKS بينها `、`، **إذن** الوسوم الأربعة في العيّنة. **وعندما** تكون الكلمة الطويلة بلا فاصل، **إذن** تُسقط كما اليوم. (وحدة)

**الإصدارات والعقد والقياس:**
- **F3‑AC‑25** — **بافتراض** البناء:
  - `SHARED_VERSION` = `1.4.0`، و`identifiers.toml` `version` = `1.2.0`، و`PIPELINE_VERSION` = `1.2.0` و`SERVICE_VERSION` = `1.1.0`.
  - **عندما** يُبنى سجلّ توصيف، **إذن** يحمل الإصدارات الجديدة.
  - وبصمة ملف الإعداد الافتراضي بايتاً ببايت كما على `master`.
  - (وحدة)
- **F3‑AC‑26** — **بافتراض** فرق الـPR، **إذن**:
  - في `openapi.yaml` تتغيّر الأوصاف وحدها، واختبارا العقد القائمان ينجحان.
  - و`_TOP_KEYS` لا يتغيّر.
  - وقيم `IdentifierKind` اثنتا عشرة، يثبّتها `test_electrical_shared_rules.py`.
  - (وحدة وعقد)
- **F3‑AC‑27** — **بافتراض** صفوف ملحق أ الجديدة في SRS 0.21 (§8)، **عندما** يُشغَّل `m04_identifiers`، **إذن** لكل مثال حالة بمصدر `appendix-a`، وحالات §5.3 كلها، والنسبة 1.0، وكل حالة فاشلة تُفشل CI. (تقييم ووحدة)
- **F3‑AC‑28** — **بافتراض** فرق الـPR، **عندما** يبحث المراجع فيه بقائمة الوسوم الحقيقية المحفوظة خارج المستودع، **إذن** لا نتيجة، إلا استثناءات `F2‑AC‑20` المكتوبة. (يدوي)
- **F3‑AC‑29** — **إعادة القياس.** **بافتراض** فرع الـPR، **عندما** يعيد المنسّق برامج القياسات الثلاثة خارج المستودع على الحقيقة المرجعية لـF3، بملفّي إعداد (§5.4)، **إذن**:
  1. لا تتراجع مجموعة كانت ✅ في القياسات 1..3.
  2. وقراءات ANSI الصالحة الزائفة على كلمات ليست ANSI تنخفض إلى 10% أو أقل من عددها قبل.
  3. وقراءات `letter_code` و`ansi_device` لقيم التقدير بالوحدات المدرجة تصير 0.
  4. وعناصر مراجع الأطراف بقائمة (فج‑22) تُقرأ بنسبة 95% أو أكثر.
  5. وKKS بقائمة (فج‑25) 3 من 3.
  6. ونطاقات الأسلاك (فج‑24) وإشارات المواقع (فج‑23) 90% أو أكثر.
  7. ويُعدّ لكل صفحة من المورد الثاني دليل الاصطلاح على العيّنة الجديدة وعلى الصفحة كاملة.
  - والتقرير `inc-1/f3/measure-4.md` بالأعداد وبأنماط مجردة. (قياس)
- **F3‑AC‑30** — **بافتراض** قرارات المالك في F3‑Q1..F3‑Q6، **عندما** تُطبَّق تعديلات §8، **إذن**:
  - في `docs/requirements.md` 0.21 صفّ D‑37 واحد، ونصوص FR‑ID المعدّلة والجديدة.
  - و`git diff` لا يمسّ FR‑ID‑01..02 و05 و07 و08 و10..14 و17..18.
  - وكل وثيقة عربية بين `<div dir="rtl">` و`</div>`.
  - (مراجعة الفرق)

**المتطلبات غير الوظيفية:**

| المعرّف | المتطلب | القياس |
|---|---|---|
| NFR‑F3‑1 | **الأداء** (سابقة NFR‑F2‑4): لا يبطئ التغيير المحلّل ولا العدّ | سكربت في مجلد مؤقت خارج المستودع، على المضيف نفسه، على `master` ثم الفرع. متوسط `IdentifierParser.parse` على حالات M‑04 كلها، بالقواعد المعبّأة وبقواعد فيها KKS وANSI مفعّلان: الحدّ 1.2 ضعف. وزمن `tally` على 1,000 رمز اصطناعي فيها 10% قوائم ونطاقات: الحدّ 1.2 ضعف. وزمن `_tokens` على صفحة من 5,000 كلمة: الحدّ 1.2 ضعف. وتُسجَّل الأرقام في جسد الـPR |
| NFR‑F3‑2 | **الحتمية** (NFR‑REP‑02) | تحليل حالات F3 كلها مرتين بمحلّلين مستقلين، و`observed` متساوٍ حرفاً |
| NFR‑F3‑3 | **القواعد بيانات** (NFR‑MNT‑04، NFR‑CFG‑01) | وحدة جديدة، ونمط سلك اختياري جديد، وصيغة KKS، وحرف فصل، وفاصل قائمة لاحقة لنوع آخر، ورمز طرف في `letters_first`: كلٌّ بخريطة القواعد وحدها، فيغيّر القراءة دون شيفرة. وكل تغيير يرفع `version` |
| NFR‑F3‑4 | **لا نص مستخدم في تعبير** (NFR‑F2‑3) | الملف يختار الأنماط بالاسم. والوحدات واللواحق تُقارن بالعضوية. وحرف الفصل من مجموعة ثابتة. واختبار: `wire_number_patterns: [".*"]` مرفوض 422 |
| NFR‑F3‑5 | **الحدود** | حدّ العناصر قبل التوليد. والنص ≤ 200 حرف. والتقسيم خطّي. وحالات QA الحدّية في `test_electrical_f3_qa.py` |
| NFR‑F3‑6 | **الخصوصية** | `F3‑AC‑28` |

### 5.2 الربط بالاختبارات

| المعيار | المستوى | الملف والحالة |
|---|---|---|
| F3‑AC‑1 | وحدة | `tests/unit/test_electrical_f3_rules.py::test_f3_ac_1_ansi_without_a_class_needs_context` (مُعلَّم بالنصوص، بلا `hint` ومعه) |
| F3‑AC‑2 | وحدة وتقييم | `…::test_f3_ac_2_classed_ansi_reads_as_in_f2`. و`test_electrical_shared_parser.py::test_f2_ac_7_ansi_on_changes_only_the_listed_ambiguities` معدّلاً. وحالة `c226` معدّلة |
| F3‑AC‑3 | وحدة | `…::test_f3_ac_3_a_named_vendor_wire_pattern_wins_over_ansi`، و`…::test_opt_in_patterns_never_read_unnarrowed` (كل حالة M‑04 بلا `select`) |
| F3‑AC‑4 | وحدة على الموجّه | `test_api_electrical_router.py::test_f3_ac_4_opt_in_wire_pattern_names_are_accepted` |
| F3‑AC‑5 | وحدة | `test_electrical_f3_rules.py::test_f3_ac_5_a_digits_first_terminal_is_no_ansi_under_nfpa` |
| F3‑AC‑6 | وحدة | `test_electrical_shared_ansi.py::test_f3_ac_6_a_suffix_with_a_trailing_digit` |
| F3‑AC‑7..9 | وحدة | `test_electrical_f3_rules.py::test_f3_ac_7_ratings_are_no_identifiers`، و`…_8_voltage_labels_win`، و`test_electrical_shared_rules.py::test_f3_ac_9_units_are_data_and_checked` |
| F3‑AC‑10 | وحدة وتقييم | `test_electrical_f3_rules.py::test_f3_ac_10_page_zero_is_no_page`، وحالات §5.3 |
| F3‑AC‑11..16 | وحدة | `tests/unit/test_electrical_f3_groups.py::test_f3_ac_11_*` .. `test_f3_ac_16_*` |
| F3‑AC‑17..19 | وحدة | `tests/unit/test_electrical_f3_tokens.py::test_f3_ac_17_*` .. `test_f3_ac_19_*` |
| F3‑AC‑20 | وحدة | `test_electrical_shared_terminals_wires.py::test_f3_ac_20_letters_first_conventions_are_data` |
| F3‑AC‑21 | وحدة | `test_electrical_shared_kks.py::test_f3_ac_21_*` (مُعلَّم بالصيغ)، و`test_electrical_shared_rules.py` لطفرات `part_separators` و`near_miss_min_length` |
| F3‑AC‑22 | وحدة | `test_electrical_f3_rules.py::test_f3_ac_22_location_references`، و`test_electrical_profile_detection.py::test_location_formats_keep_auto` |
| F3‑AC‑23..24 | وحدة | `tests/unit/test_ele_parse_service.py::test_f3_ac_23_digit_tokens_fill_the_sample_first` و`…_24_a_long_word_is_split_before_the_bound`. و`test_the_tokens_are_distinct_bounded_and_in_reading_order` معدّلاً |
| F3‑AC‑25..26 | وحدة وعقد | `test_electrical_shared_rules.py::test_the_rules_file_is_packaged_and_versioned` (`1.2.0`، `1.4.0`، والأنواع الاثنا عشر)، و`test_electrical_characterization.py`، واختبارا العقد القائمان |
| F3‑AC‑27 | تقييم | `cases/identifiers.toml`، و`_APPENDIX_A` في `test_electrical_shared_parser.py`، و`python -m tests.eval.electrical.m04_identifiers` = 1.0 |
| F3‑AC‑28 | يدوي | المراجع |
| F3‑AC‑29 | قياس | المنسّق خارج المستودع، §5.4 |
| F3‑AC‑30 | مراجعة | المنسّق |
| NFR‑F3‑2 | وحدة | `test_electrical_f3_rules.py::test_nfr_f3_2_every_f3_case_parses_twice_the_same` |
| NFR‑F3‑3 | وحدة | `test_electrical_shared_rules.py::test_nfr_f3_3_f3_rules_need_no_code` |
| NFR‑F3‑5 | وحدة (QA) | `tests/unit/test_electrical_f3_qa.py` يكتبه `qa-engineer` بعد البناء |

**`tests/eval/electrical/m04_identifiers.py`:**
- `select` يقبل `wire_number_patterns` أيضاً.
- و`observed` يضيف `"group"` حين يوجد: `form`، و`separator`، و`members` بـ`text` و`kind` و`valid` لكل عنصر. وتضيفه `EXPECTED_KEYS`.
- وسطر في `tests/eval/electrical/README.md`.

### 5.3 حالات M‑04 الجديدة (`tests/eval/electrical/cases/identifiers.toml`)

**المصدر:** `appendix-a` لأمثلة ملحق أ الجديدة كما في SRS 0.21، و`extra` لصيغ أخرى، و`invalid` لما يُعلَّم، و`invalid-or-prose` لما ليس معرّفاً. ويُحدَّث تعليق الرأس (من `c237` حالات F3). وكل حقل يكتبه المطوّر من ناتج المحلّل، ثم يراجعه مقابل §3 حقلاً حقلاً. فالحالة عقد، لا لقطة.

| المصدر | النص | `select` | المتوقَّع |
|---|---|---|---|
| appendix-a | `X7:41,42,43` | `iec_81346` | `identifier_group` `list`، ثلاثة أطراف |
| appendix-a | `QS7-QS9`، `QS7\8` | `cn_letter_codes` | `identifier_group` `range` و`suffix_list` |
| appendix-a | `20XYZ10AB131/2/3` | `iec_81346`، `kks` | `identifier_group` `suffix_list`، ثلاثة وسوم |
| appendix-a | `27.6E`، `.6E` | — | `cross_reference` بالصيغتين |
| appendix-a | `20XYZ10AB123-CD45`، `20XYZ` | `iec_81346`، `kks` | `kks_tag`: مكوّن، و`system_code` |
| appendix-a | `1X-37'` | `iec_81346` | `terminal`، النقطة `37'` |
| appendix-a | `59G2` | `iec_81346`، `ansi` | `ansi_device`، المثيل 2 |
| extra | `X7:41，42，43`، `X7:41、42、43` | `iec_81346` | كالحالة الأولى، والأصل محفوظ |
| extra | `38Y-43Y` | `iec_81346`، و`wire_number_patterns = ["digits_letter"]` | نطاق بستة أسلاك |
| extra | `38Y`، `59Y` | `iec_81346`، `ansi`، و`wire_number_patterns = ["sequential", "digits_letter"]` | `wire_number` ببديل `ansi_device` |
| extra | `59Y` | `iec_81346`، `ansi` | `ansi_device` صالح |
| extra | `X7:41` | `cn_letter_codes` | `terminal` باصطلاح `cn_letter_codes` |
| extra | `12/3` (`c226` معدّلة) | `iec_81346`، `ansi` | `cross_reference` ببديل `ansi_device` |
| extra | `20XYZ10AB123CD456` | `iec_81346`، `kks` | `kks_tag` `component_wide` |
| invalid | `X23-X37` | `iec_81346` | `identifier_group` غير صالح، `group.member_unreadable` |
| invalid | `QS7-QS99` | `cn_letter_codes` | `identifier_group` غير صالح، `group.too_many_members` |
| invalid | `X7:41,,42` | `iec_81346` | `identifier_group` غير صالح، `group.empty_member` |
| invalid-or-prose | `8Y`، `8P`، `16F`، `23-4`، `4-23` | `iec_81346`، `ansi` | `none` |
| invalid-or-prose | `3X-41` | `nfpa_jic`، `ansi` | `none` |
| invalid-or-prose | `37MVA`، `47Hz`، `17VA`، `2PH`، `7KW`، `13mA`، `7-29kV` | الاصطلاحات كلها، `kks`، `ansi` | `none` |
| invalid-or-prose | `37kV`، `29kA` | `cn_letter_codes` | `none` |
| invalid-or-prose | `0.7`، `0.6E`، `⑰` | — | `none` |
| invalid-or-prose | `QS9-QS7` | `cn_letter_codes` | `none` |

**العدد** بعد البناء نحو 236 + 45. ويبقى M‑04 = 1.0 في CI.

### 5.4 إعادة القياس (F3‑AC‑29)

يجريها المنسّق بعد الـPR وقبل الدمج. البرامج والمخرجات خارج المستودع، كما في القياسات 1..3.

1. **ملفا الإعداد:**
   - **المورد الأول:** IEC 81346 وKKS وANSI، واللواحق الست من القياس 1 في `ansi_suffixes`، وبلا أنماط مسمّاة.
   - **المورد الثاني:** أكواد الحروف الصينية وKKS، وبلا ANSI، و`wire_number_patterns` = `sequential` و`digits_letter` و`letter_digits`.
   - **وللمقارنة:** المورد الثاني بـGB/T 5094.
2. **الطريقة:** كل معرّف موسوم كما رُسم، بالقواعد المضيَّقة بالملف، كما في القياسات 1..3. والكلمات التي ليست معرّفات تُقسَّم بـ`split_candidates` لا بالمسافة.
3. **الاكتشاف:** `inspect.py` من الفرع على صفحات المورد الثاني الثماني، ثم `tally`، على العيّنة الجديدة وعلى الصفحة كاملة (`measure-3.md` §4).
4. **التقرير:** `inc-1/f3/measure-4.md`، فيه لكل فجوة في النطاق العدد قبل وبعد، والبنود السبعة في F3‑AC‑29، وما بقي.

## 6. ترتيب التنفيذ والتوازي

**قبل البناء:** ✅ قرر المالك F3‑Q1..F3‑Q6 (2026-11-18)، وطُبّقت تعديلات §8 على SRS وHLD في فرع الوثائق هذا. ويُدمج الفرع، ثم PR شيفرة واحد من `master`. والمنفّذ `backend-developer`، ثم `qa-engineer`.

**كل خطوة إيداع،** والبوابات بعدها خضراء: `ruff format --check`، و`ruff check`، و`mypy src`، و`lint-imports`، و`pytest -rs` (`tests/unit tests/architecture tests/eval` على هذا المضيف، والتكامل في CI)، و`deploy/prometheus/test-rules.sh`.

1. **الأساس:**
   - `kinds.py`: النوع الثاني عشر، و`GroupForm` و`GroupMember` و`IdentifierGroup` و`ParsedIdentifier.group`.
   - `rules/systems.py` و`rules/__init__.py`: الجداول الجديدة وقرّاؤها، والحقول، و`narrowed`، و`wire_number_pattern_names`.
   - `identifiers.toml` بالإصدار `1.2.0`، و`version.py` بـ`1.4.0`، وسطر `projects.py:67`.
   - بلا تغيير في القراءة بعد.
   - الاختبارات: طفرات الجداول، والإصدارات، والأنواع الاثنا عشر، وF3‑AC‑4، وNFR‑F3‑3 للقراءة.
2. **ANSI بلا سياق (§3.4):** `ansi.py` و`_alternatives`، و`c226`، وتعديل اختبارات F2 المذكورة. F3‑AC‑1..6.
3. **قيم التقدير (§3.5):** `ratings.py`، و`normalization.folded`، والحاجز، وتعابير `[cross_references]`. F3‑AC‑7..10.
4. **التقطيع والتطبيع والأطراف (§3.7، §3.9):** `tokens.py`، وطيّ `、` والشرطات العليا، و`has_enclosed_number`، و`tally` على المرشّحين، والشرطة العليا في الطرف، و`letters_first`. F3‑AC‑17..20.
5. **القوائم والنطاقات (§3.6):** `groups.py`، و`_group`، والعناصر في `tally`، و`observed` و`select` في `m04_identifiers.py`. F3‑AC‑11..16.
6. **KKS والإشارات (§3.10):** `kks.py` (حرف الفصل وحدّ الشبيه) والصيغ والإشارتان في الملف. F3‑AC‑21..22.
7. **`ele-parse` (§3.8، F3‑Q4 (ج)):** `_tokens` و`LONG_TOKEN_SPLIT` و`SERVICE_VERSION` و`PIPELINE_VERSION`. F3‑AC‑23..24.
8. **العقد والتقييم:** وصف `openapi.yaml`، وحالات §5.3، و`_APPENDIX_A`، و`README.md`. ثم قياس NFR‑F3‑1 وتسجيله في جسد الـPR. F3‑AC‑25..27.

**التوازي:**
- المنفّذ واحد.
- الخطوات 2 و3 و4 و6 مستقلة بعد الخطوة 1.
- والخطوة 5 تحتاج 2 (العناصر تُقرأ بقاعدة الفئة) و4 (طيّ `、`).
- والخطوة 7 مستقلة عن 2..6، فلا تمسّ إلا `services/ele_parse` و`pipeline.py`. فيجوز أن تُبنى بالتوازي.
- والخطوة 8 أخيرة.

**بعد البناء:**
- `qa-engineer`: `inc-1/f3/test-plan.md` و`tests/unit/test_electrical_f3_qa.py`.
- ثم مراجعة الشيفرة والأمن بالتوازي.
- والمراجع ينفّذ F3‑AC‑28.
- ثم المنسّق F3‑AC‑29 (§5.4).
- والدمج وإعادة بناء `app` و`ele-parse` قرار بشري.

## 7. المخاطر والقرارات

### 7.1 قرارات المالك (✅ حُسمت كلها في 2026-11-18)

حسم المالك القرارات الستة بالتوصية في 2026-11-18. وتبقى الخيارات أدناه سجلاً.

**✅ F3‑Q1: ANSI بلا سياق** (قرار المالك: (ج)) (فج‑1، و2، و5، و6، و7، و8، و18، و30). يغيّر FR‑ID‑16 وجملة المركّب في HLD §5.4.

| الخيار | ما يحلّ | ما يبقى |
|---|---|---|
| (أ) أنماط أسلاك المورد في الملف تتقدّم على ANSI، وحدها | المورد الثاني بملفه (فج‑1، 2، 18، 30) | المورد الأول: ANSI عنده مفعّل، ونمط `9A` يجعل كل `87T` سلكاً، فيبقى فج‑5 و6 و8 🔴 |
| (ب) ANSI بلاحقة حرف واحد لا يُقرأ إلا بـ`hint` | كل الأشكال | يُسقط `87T` و`51N` و`50G` الحقيقية بلا سياق، فيتغيّر `F2‑AC‑8`. ويسقط دليل ANSI في الاكتشاف حتى INC‑2، فلا يُقترح ANSI للمورد الأول |
| **(ج) قاعدة الفئة، ومعها (أ)** | 240 من 248 زائفة (§1)، والمورد الثاني بملفه. ولا يتغيّر الاكتشاف، فالدليل يشترط الفئة أصلاً | 13 من 46 رقماً بلاحقة في جداول الدليل خارج الجدول، فلا تُقرأ بلا سياق حتى INC‑2، أو حتى يُضاف رقمها بالبيانات. و`c226` يتغيّر |

**التوصية: (ج)، وبها قرر المالك.** هي الوحيدة التي تُسقط 🔴 عند الموردين معاً دون أن تمسّ أشكال ANSI التي أقرّها F2. وهي امتداد لقاعدة الرقم المجرد في FR‑ID‑16، لا قاعدة جديدة في الروح.

**✅ F3‑Q2: الطرف `9X-99` باصطلاح NFPA** (قرار المالك: (ب)) (فج‑3).
- **(أ)** قاعدة خاصة: قراءة ANSI بمثيل ولاحقتها رمز طرف (`X`، `XT`، `TB`) لا تُقرأ بلا `hint`.
- **(ب)** لا قاعدة خاصة: قاعدة F3‑Q1 (ج) تكفي، لأن رقم الطرف المفرد خارج الجدول. ويبقى اختيار الاصطلاح للاكتشاف والمستخدم، فاصطلاح هذا المورد IEC (`measure-1.md` §3).

**التوصية: (ب)، مع (ج) في F3‑Q1، وبها قرر المالك.** ولو اختيرت (أ) في F3‑Q1 للزمت (أ) هنا.

**✅ F3‑Q3: صيغ KKS خارج نص FR‑ID‑15** (قرار المالك: (أ)) (فج‑16، و17، و33). FR‑ID‑15 يجيز إضافة الصيغ بملف القواعد، لكن نصه يصف المستويات ويقول «متصلة أو بمسافات».

| الخيار | ما فيه |
|---|---|
| **(أ) الثلاث بيانات** | `system_code` (`NNAAA`، المستوى 1، ليس دليلاً)، و`component_wide` (`AANNN`)، والشرطة بين مستويين. والأشكال متسقة في رسوم المورد نفسه: 30، و8، و4 حالات |
| (ب) `system_code` والشرطة وحدهما | ويبقى `component_wide` معلَّماً غير صالح حتى يتحقق المالك من وثيقة ترميز المورد |
| (ج) لا شيء الآن | تبقى الثلاث 🟠/🟡 إلى INC‑2 |

**التوصية: (أ)، وبها قرر المالك.** الشكل ذو السبعة عشر موضعاً الثابتة لا يطابقه نص عارض. والقصير ليس دليلاً، فلا يمسّ الاكتشاف. وخطره (خ‑4) قراءة نص `NNAAA` عارض وسماً بالمستوى 1 حين يُفعَّل KKS. ويُعدَّل نص FR‑ID‑15 (S‑7).

**✅ F3‑Q4: عيّنة الاكتشاف** (قرار المالك: (ج)) (فج‑38، و39). يمسّ `ele-parse` والأداء.

| الخيار | الكلفة | الأثر |
|---|---|---|
| (أ) يؤجَّل إلى INC‑2 | لا شيء الآن | STG‑02 يقرأ كلمات الصفحة كلها، فيُعدّ الاكتشاف منها هناك. ويبقى المورد الثاني على 55% حتى ذلك |
| (ب) `ELE_PARSE_TOKEN_SAMPLE` إلى 1,000 | إعداد نشر (`docker-compose.yml`، `.env.example`)، وحدود الحمولة: 1,000 × 32 حرفاً × 6 بايت = 192 KiB للصفحة، فوق `MAX_PAGE_JSON_BYTES` (64 KiB) و`MAX_ANSWER_BYTES` لمدى 200 صفحة، فيلزم رفعهما ومراجعة أمن للحدود العدائية | يصل كل ما في الصفحة تقريباً |
| **(ج) الكلمات التي فيها رقم أولاً، والكلمة الطويلة تُقطَّع** | دالة واحدة في `ele-parse` وإعادة بناء صورته. ولا حدّ ولا عقد ولا إعداد نشر | العيّنة تمتلئ بالمرشّحين، والحمولة كما هي |

**التوصية: (ج)، وبها قرر المالك.** تعالج سبب الفجوة (كلمات بلا رقم تملأ العيّنة) بلا كلفة في الحدود. وتُقاس في F3‑AC‑29 (البند 7). وإن لم تكفِ، يُعاد النظر في INC‑2 مع (أ).

**✅ F3‑Q5: `MIN_IDENTIFIERS` = 20** (قرار المالك: (أ)) (فج‑41).
- **(أ) يبقى 20.**
- (ب) 10.
- (ج) عتبة في ملف الإعداد (NFR‑CFG‑01)، وهي حقل جديد وإجراء رجوع.

**التوصية: (أ)، وبها قرر المالك.** الأدلة تزيد بهذا التصميم: عناصر القوائم، والعيّنة الجديدة، والطرف تحت الصيني. والحدّ يحمي من رمز عارض، وهذا أصل خ‑4 في F2. ويُضبط مع M‑32 في `INC1‑R‑15` بعد إعادة القياس، لا قبلها.

**✅ F3‑Q6: مورد يخلط أطراف IEC بأسماء صينية** (قرار المالك: (أ)) (فج‑40). يمسّ معنى «اصطلاح واحد للأجهزة» (D‑36 (3)).

| الخيار | ما فيه |
|---|---|
| **(أ) بيانات: `[terminals.letters_first]`** | `X` يُقرأ تحت IEC ثم GB/T ثم الصيني، على سابقة A‑5. والملف المؤكَّد بالصيني يقرأ الاثنين. ولا يتغيّر الاكتشاف، فيبقى المورد الثاني «IEC أو مختلطاً» حتى يؤكد المستخدم |
| (ب) اصطلاح ثانٍ للأطراف في ملف الإعداد | حقل جديد، وتعديل FR‑ING‑01 وD‑36 (3)، ووصف OpenAPI، وإجراء رجوع كـF2 §4.1، واكتشاف للحقل |
| (ج) يؤجَّل إلى INC‑2 | يبقى المورد الثاني لا يُقرأ كاملاً بأي ملف |

**التوصية: (أ)، وبها قرر المالك.** أصغر تغيير يجعل ملفاً واحداً يقرأ المورد كله. والاكتشاف الأدق، أي أن يقترح الصيني لمورد يخلط، يُنظر فيه في INC‑2 مع M‑32.

**تعديل SRS وHLD** نتيجةٌ لهذه القرارات، لا قرار سابع (§8): SRS 0.21 بصفّ D‑37، وHLD 0.20. وطُبّقت في هذا الفرع بعد قرار المالك، قبل PR الشيفرة.

### 7.2 مخاطر واختيارات بمعالجتها

| # | الخطر أو الاختيار | المعالجة |
|---|---|---|
| خ‑1 | قاعدة الفئة تُسقط بلا سياق أرقام ANSI حقيقية خارج الجدول: 13 من 46 بلاحقة في الدليل، و10 من 54 مجردة، و29 من 62 مكدّسة رقماً فوق حروف | المجردة والمكدّسة لا تُقرأ بلا سياق أصلاً. والباقي يقرؤه STG‑05 بـ`hint` في INC‑2. ويُضاف الرقم إلى `[ansi.functions]` بالبيانات بفئة باسمنا، مع تذكّر أن كل رقم يُضاف يعيد التصادم مع أسلاك `99A` (NFR‑LIC‑05 كما في F2) |
| خ‑2 | `letter_digits` المسمّى يقرأ نقاط الاتصال (`A1`) والأسماء بحرف واحد أسلاكاً | اختياري، ويسمّيه المستخدم لمورده وحده. ويُذكر في وصف OpenAPI. وفصلها بالموضع في INC‑2 |
| خ‑3 | النطاق `X23-X37` قد يكون طرفاً اسمه `X37` على `X23` عند مورد ما | لم يُرَ في العيّنات. والقراءة الجديدة تعلّم المجموعة غير صالحة ولا تدّعي هوية. والقارئ ذو السياق يمرّر `hint=TERMINAL` فيعود الطرف |
| خ‑4 | `system_code` يقرأ نصاً عارضاً بشكل `NNAAA` وسماً بالمستوى 1 حين يُفعَّل KKS | ليس دليلاً، فلا يمسّ الاكتشاف. والوحدات الشائعة (`kVA`، `MVA`، `VDC`، `VAC`) يحجبها حاجز التقدير قبله |
| خ‑5 | `37KV` بحروف كبيرة في ملف صيني يبقى `letter_code` | الحالة المكتوبة هي الفارق الوحيد بلا سياق. والقياس رأى `kV` بحالتها. ويُراجع في F3‑AC‑29 |
| خ‑6 | «الصفحة 0 ليست صفحة» يُسقط مرجعاً إلى صفحة غلاف رقمها 0 | لم يُرَ. والقاعدة بيانات تُعاد بالملف |
| خ‑7 | عدّ عناصر القوائم يضخّم اصطلاحاً | الحدّ 64. والعناصر هي ما تقوله الصفحة فعلاً |
| خ‑8 | ترتيب العيّنة الجديد يغيّر اقتراح وثائق قائمة عند إعادة معالجتها | يحمل السجلّ `pipeline` و`shared` الجديدين، والمجموعة المختلطة تقول ذلك. وإعادة المعالجة قرار بشري |
| خ‑9 | قائمة مكتوبة بمسافة بعد الفاصلة (`X7:41, 42`) تصير كلمتين في طبقة النص | `tally` يعدّ `X7:41` وحده، ولا ضرر في الاكتشاف. وSTG‑05 يجمع الكلمات من السطر (فج‑36، مؤجَّل) |
| خ‑10 | `ele-parse` ينزع النقطة الأولى، فلا تصل `.6E` إلى العدّ | لا أثر على الاكتشاف، فإشارة الموقع ليست دليل اصطلاح. وفي STG‑05 تُقرأ الكلمة كما هي |
| خ‑11 | الأداء: حاجزان وفحص مجموعة لكل رمز | فحوص `in` رخيصة قبل أي تعبير، وتعبير التقدير واحد ومحدود. ويُقاس في NFR‑F3‑1. وإن تجاوز الحدّ، يُقصر فحص المجموعة على نص فيه `,` أو `-` أو `/` أو `\` |
| خ‑12 | الرجوع: جسد يسمّي نمطاً اختيارياً | §4.1 |

## 8. تعديلات SRS وHLD (✅ طُبّقت في 2026-11-18 بعد قرار المالك)

**`docs/requirements.md`: من 0.20 إلى 0.21.** طُبّقت بالصياغة أدناه، والنص النهائي في الملف.

| # | الموضع | التعديل |
|---|---|---|
| S‑1 | الرأس | 0.21، والتاريخ، و«D-01..D-37 كلها محسومة» |
| S‑2 | سجل الإصدارات | صفّ 0.21: قواعد F3 (D-37)، وما أُضيف وعُدّل |
| S‑3 | FR‑ID‑03 | «وتُفكّ قوائم الأطراف ونطاقاتها إلى أطراف (FR-ID-19). والشرطة العليا بعد رقم الطرف جزء منه (`1X-37'`). والاصطلاحات التي تُقرأ تحتها الصيغة التي يسبق فيها الرمزُ الرقم بيانات لكل رمز أيضاً، ومنها أكواد الحروف الصينية لـ`X` (D-37)» |
| S‑4 | FR‑ID‑04 | «وأنماط إضافية للمورد في ملف القواعد لا تُقرأ إلا حين يسمّيها ملف الإعداد. والنمط المسمّى يتقدّم على قراءة رقم ANSI بلا سياق (D-37)» |
| S‑5 | FR‑ID‑06 | الأمثلة: «`27.6E`، `.6E` (إشارة موقع)». و«ولا يكون رقم الصفحة صفراً» |
| S‑6 | FR‑ID‑09 | «وتُطبَّع الفاصلة الصينية `、` إلى `,`. ولا يُطبَّع الرقم المدوّر (`⑰`) إلى رقم، ولا يُقرأ معرّفاً إلا بسياق» |
| S‑7 | FR‑ID‑15 | «متصلة أو بمسافات أو بشرطة بين المستويات». و«والصيغ الافتراضية صيغ الموردين المقيسين، ومنها رمز النظام وحده ومكوّن بثلاثة أرقام، وتُضاف غيرها...» |
| S‑8 | FR‑ID‑16 | بعد جملة الرقم المجرد: «وكذلك الرقم الذي لا فئة له في جدول القواعد، بلاحقة أو مثيل أو في مركّب: لا يُقرأ رقم جهاز إلا بدليل من سياقه. ويُقبل رقم واحد بعد اللاحقة مثيلاً (`59G2`)» |
| S‑9 | FR‑ID‑19 (جديد، M) | «يجب فكّ القوائم والنطاقات المكتوبة في نص واحد إلى معرّفاتها: قائمة بفاصلة يرث عنصرها الرقمي رأس الأول (`X7:41,42,43`)، ونطاق بشرطة بين معرّفين من شكل واحد (`QS7-QS9`)، وقائمة لاحقة تستبدل الأرقام الأخيرة لأنواع يحددها ملف القواعد (`20XYZ10AB131/2/3`، `QS7\8`). وبحدّ أعلى لعدد العناصر من ملف القواعد. ويُحفظ النص الأصلي، ويُعلَّم العنصر الذي لا يُقرأ (FR-ID-10)» · المصدر D-37 |
| S‑10 | FR‑ID‑20 (جديد، M) | «يجب ألا تُقرأ قيمة التقدير بوحدتها معرّفاً (`37MVA`، `47Hz`، `37kV`). والوحدات قائمة في ملف القواعد. وتسميات الجهد في FR-ID-08 تبقى جهوداً» · المصدر D-37 |
| S‑11 | §11.1 العنوان وصفّ D‑37 | السؤال: «فجوات F3 في قواعد المعرّفات (`inc-1/f3/gaps.md`)». والقرار: F3‑Q1..F3‑Q6 بالتوصية. والأثر: S‑3..S‑10 وS‑12. ويبقى FR-ID-01..02 و05 و07..08 و10..14 و17..18 كما هي |
| S‑12 | ملحق أ | صفّ «قائمة أو نطاق»: `X7:41,42,43`، `QS7-QS9`، `20XYZ10AB131/2/3`، `QS7\8`. وفي صفّ المرجع المتقاطع `27.6E`، `.6E`. وفي صفّ KKS `20XYZ` و`20XYZ10AB123-CD45`. وفي صفّ الطرف `1X-37'`. وفي صفّ ANSI `59G2` |
| S‑13 | §9.1، وR‑11 | «FR-ID-01..20» لـI‑04 |

**`docs/hld.md`: من 0.19 إلى 0.20.**

| # | الموضع | التعديل |
|---|---|---|
| H‑1 | الرأس وسجل الإصدارات | 0.20، وSRS 0.21، وD-01..D-37 |
| H‑2 | §5.4 البندان 1 و2 | النوع الجديد للقوائم والنطاقات، وقيم التقدير ليست معرّفات، وأنماط المورد الاختيارية، والطرف تحت الصيني بيانات |
| H‑3 | §5.4 «KKS وANSI في المحلّل» | جملة المركّب: «جهاز ANSI إن كان لكل رقم فيه فئة، ما لم يثبّت الملف صيغة مرجع تطابقه». ومعها قاعدة الفئة للمفرد، وتقدّم نمط السلك المسمّى |
| H‑4 | §5.4 «التسليم» | تغيير F3 مستقل من `master` قبل LLD الزيادة INC‑2، والإصداران. تصميمه في `../inc-1/f3/design.md` |
| H‑5 | §7.1 صفّ STG‑01 | «وعيّنة الكلمات تبدأ بما فيه رقم» (F3‑Q4 (ج)) |
| H‑6 | §16 صفّ INC‑2، النطاق و«تعتمد على» | النطاق يضمّ الفجوات الثماني عشرة المؤجَّلة في §2.1. و«تعتمد على»: «وتغيير قواعد F3، PR شيفرة مستقل من `master` قبل LLD هذه الزيادة» |
| H‑7 | §18.2 RSK‑28 وRSK‑29 | بنية KKS: صيغ المورد الثاني بيانات. والتباس ANSI: قاعدة الفئة وما بقي للسياق |

**لا ADR جديد.** لا تقنية ولا مكتبة ولا نموذج.
**ولا DEC جديد.** القرار في نطاق DEC‑20 (قواعد المعرّفات بيانات)، ويُسجَّل في SRS D‑37 وفي سجل إصدار HLD.

## 9. ما تحققتُ منه وما لم أتحقق منه

**ما شغّلتُه** (للقراءة فقط، بـ`.venv/bin/python` من مجلد الجلسة المؤقت، خارج المستودع، بلا تغيير في حالة أي شيء):
- المحلّل على `master` على كل مثال مخترع هنا، فعمود «اليوم» في §2 من ناتجه.
- **وعلى الحقيقة المرجعية خارج المستودع** (SRS D‑34): أنماط مجردة وأعداد فقط.
  - الأعداد في §1 وخ‑1.
  - ووجدتُ أن أرقام الأطراف بشكل `9AA99` عند المورد الأول برمزين لا يتغيّران، ولا أكتبهما هنا. وهو سبب من أسباب تأجيل فج‑26.
  - ووجدتُ أن أسماء أجهزة المورد الثاني خارج الجدول رموز خاصة بالمورد. فلا تُضاف إلى الجدول المعبّأ، وهذا سبب تأجيل فج‑28.
- **وفحصتُ كل مثال جديد هنا على نصوص الحقيقة المرجعية:** لا تطابق. أما الأمثلة القديمة في المستودع (`87T`، `50/51`، `1X-25`، `12/3`...) فكما أقرّها F2.

**ما لم أتحقق منه:**
- **زمن المحلّل والعدّ و`_tokens`** (NFR‑F3‑1): لم أقسه، ويُقاس عند البناء.
- **أثر العيّنة الجديدة على الصفحات:** لم أشغّل `ele-parse`. ويقيسه F3‑AC‑29.
- **وثيقة ترميز KKS عند المورد الثاني:** غير متاحة. فقبِل المالك الصيغ الثلاث بالبيانات (F3‑Q3 (أ))، وتُصحَّح بالملف وحده إن خالفتها الوثيقة.
- **أن الوحدات في `[ratings]` لا تصادف معرّفاً حقيقياً عند مورد ثالث:** لا مورد ثالث (`gaps.md` §8).
- **أن `、` لا تفصل غير القوائم في نص صيني:** النص الصيني ليس معرّفاً أصلاً (`INC1‑Q‑6`)، والعيّنات موردان فقط.
- **لم أشغّل `pytest`.** وكل مسار وسطر مذكور قرأته على `master` = `8629341`.

</div>
