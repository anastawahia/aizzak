<div dir="rtl">

# التصميم التقني: F0، إصلاح `ele-parse` والتصنيف وتقرير التوصيف

- **المرجع:** `inc-1/p1-fix-path.md` §6، و`inc-1/characterization.md`.
- **الفرع:** `fix/ele-parse-curves`.
- **المستودع عام:** أعداد وأنماط فقط، والاختبارات على ملفات PDF اصطناعية.

## 1. الملخص

إصلاح صغير في ثلاث طبقات، بلا ترحيل وبلا تغيير عقد `ele-parse`:

1. **`ele-parse`:** قراءة نقاط `c` بالفهرس في `_paths`.
2. **النطاق:** قاعدة `VECTOR_OUTLINED` تقرأ `vector_path_count` أيضاً، بالعتبة القائمة `outlined_curves_min`.
3. **تقرير التوصيف:** عدّ الصفحات الفاشلة، و`symbol_standard_guess` يصير `None` حين لا اصطلاح مرصوداً.

والملف المقترح لا يتغيّر، فالتغيير في ادعاء التقرير وحده.

### معايير القبول

| المعرّف | البند | المعيار |
|---|---|---|
| F0‑AC‑1 | 1 | صفحة فيها منحنى بيزييه حقيقي تنجح عبر `_paths`، ولا يظهر عنها `parse_error`. ويُعدّ المنحنى الذي امتداده ≤ 10 pt ولا يُعدّ الأكبر. |
| F0‑AC‑2 | 2 | صفحة تجمع `l` و`re` و`qu` و`c` تنجح، ولا يقرأ `_paths` نقاطاً من غير `c`. ونتيجة الفحص مسجلة في §3.3. |
| F0‑AC‑3 | 3 | سجل الوثيقة، والتقرير المجمّع، وJSON، وMarkdown، وAPI، كلها تُظهر `failed_pages`. وصفحة `EMPTY` ناجحة لا تُعدّ فاشلة. |
| F0‑AC‑4 | 3 | عند صفحة فاشلة أو متروكة لكبر حجمها، تُطابق الأداة سجل العامل بايتاً ببايت، ويعطي الطرفان العدد نفسه. |
| F0‑AC‑5 | 4 | صفحة نصها أقل من `min_text_chars` ومساراتها ≥ `outlined_curves_min` تكون `VECTOR_OUTLINED` و`needs_ocr`. ولا يتغيّر `RASTER` و`VECTOR_GARBLED` و`MIXED`. و`PIPELINE_VERSION` = `1.1.0`. |
| F0‑AC‑6 | 5 | بلا اصطلاح مرصود: `symbol_standard_guess` = `None`، و`symbol_standard_basis` لا يذكر `convention:`، والتقرير المجمّع يعدّها تحت `unknown`، وMarkdown لا يعرض `iec_60617`. |
| F0‑AC‑7 | 5 | الملف المقترح وثقته كما كانا تماماً. والصف المخزّن قبل F0 يُقرأ بلا خطأ، و`failed_pages` فيه صحيح. والصف ذو `null` يُقرأ أيضاً. |
| F0‑AC‑8 | 6 | بعد الدمج فقط: الملفات الثلاثة تعطي 3 صفحات، و0 فاشلة، و3 `vector_outlined`، و`ocr_needed_ratio` = 1.0، و`symbol_standards` = `unknown` 3، وpipeline `1.1.0` (§9). |

## 2. الملفات

المنفّذ في كل الملفات: `backend-developer`. **لا عمل لـ`data-engineer`، فلا ترحيل.**

| المسار | الحالة | المسؤولية |
|---|---|---|
| `services/ele_parse/inspect.py` | تعديل | قراءة النقاط بالفهرس في `_paths` |
| `src/app/modules/electrical/drawings/domain/classification.py` | تعديل | القاعدة 4 ووصف الوحدة |
| `src/app/modules/electrical/drawings/domain/pipeline.py` | تعديل | `PIPELINE_VERSION` إلى `1.1.0` |
| `src/app/modules/electrical/drawings/domain/profile_detection.py` | تعديل | `_symbol` يذكر `convention:` حين يُرصد فقط |
| `src/app/modules/electrical/drawings/domain/characterization.py` | تعديل | `failed_pages`، والتخمين الاختياري، و`aggregate` |
| `src/app/modules/electrical/drawings/adapters/sql_repository.py` | تعديل | كتابة `null` وقراءته |
| `src/app/api/v1/dto/electrical.py` | تعديل | `failed_pages`، و`symbol_standard_guess: str \| None` |
| `src/app/api/v1/routers/electrical.py` | تعديل | التحويلان `_characterization_out` و`_report_out` |
| `src/app/ops/electrical_characterize.py` | تعديل | `build_report` و`render_markdown` |
| `docs/design/openapi.yaml` | تعديل | المخططان `CharacterizationOut` و`CharacterizationReportOut` |
| `tests/unit/test_ele_parse_service.py` | تعديل | F0‑AC‑1، F0‑AC‑2 |
| `tests/unit/test_electrical_classification.py` | تعديل | F0‑AC‑5 |
| `tests/unit/test_electrical_profile_detection.py` | تعديل | F0‑AC‑3، F0‑AC‑6، F0‑AC‑7 |
| `tests/unit/test_ops_electrical_characterize.py` | تعديل | F0‑AC‑3، F0‑AC‑4، F0‑AC‑6 |
| `tests/unit/test_electrical_adapters.py` | تعديل | F0‑AC‑7 (الصفوف المخزّنة) |
| `tests/unit/test_api_electrical_router.py` | تعديل | F0‑AC‑3، F0‑AC‑6 على الواجهة |

- `openapi.yaml` يتغيّر في هذا الفرع لا لاحقاً، لأن `test_every_electrical_schema_has_the_published_properties_and_required_set` يفشل بدونه.
- لا تغيير في `services/ele_parse/contract.py` و`ele_parse_client.py` و`domain/pages.py` و`domain/profile.py`.

## 3. العقود

### 3.1 الواجهات البرمجية

لا مسار جديد. تغيّر شكل الاستجابة 200 فقط، ولا عميل واجهة في هذا المستودع يحتاج إعادة توليد.

| الطريقة | المسار | الطلب | الاستجابة | الأخطاء | الصلاحية |
|---|---|---|---|---|---|
| GET | `/electrical/documents/{id}/characterization` | لا يتغيّر | `CharacterizationOut` مع `failed_pages: integer` (إلزامي)، و`symbol_standard_guess: string \| null` | كما هي (404) | `electrical:read` كما هي |
| GET | `/electrical/projects/{id}/characterization` | لا يتغيّر | `CharacterizationReportOut` مع `failed_pages: integer` (إلزامي)، و`symbol_standards` قد يحوي المفتاح `unknown` | كما هي (404) | `electrical:read` كما هي |

`ocr_needed_ratio` يبقى مقامه كل الصفحات، والفاشلة منها. تغيير المقام تعريف جديد لرقم FR-EVAL-14، وخارج نطاق F0. ويظهر السطر `failed pages` فوقه مباشرة في Markdown.

### 3.2 التواقيع

```python
# services/ele_parse/inspect.py, _paths (the signature is unchanged)
xs = [point[0] for point in item[1:]]  # get_cdrawings() gives tuples, not Points
ys = [point[1] for point in item[1:]]

# domain/pipeline.py
PIPELINE_VERSION: Final = "1.1.0"

# domain/classification.py, _kind, rule 4. Rule 5 (MIXED) stays on curves only.
if little_text and (
    signals.short_curve_path_count >= t.outlined_curves_min
    or signals.vector_path_count >= t.outlined_curves_min
):
    return PageKind.VECTOR_OUTLINED

# domain/profile_detection.py
def _symbol(
    convention: Convention,
    convention_confidence: float,
    hits: Sequence[ProducerSignature],
    zh_share: float,
    *,
    observed: bool,  # a convention was seen in the tokens, not taken from the base profile
) -> tuple[SymbolStandard, float, tuple[str, ...]]: ...
# scores are unchanged; only: basis = [f"convention:{convention.value}"] if observed else []
# detect_profile passes observed=bool(token_basis)

# domain/characterization.py
UNKNOWN_SYMBOL_STANDARD: Final = "unknown"

class Characterization:
    symbol_standard_guess: SymbolStandard | None  # None: no convention was seen

    @property
    def failed_pages(self) -> int:
        """Pages with no classification. ``text_kinds`` holds every classified page and only
        those, so a record stored before this property reads right as it is."""
        return max(0, self.page_count - sum(self.text_kinds.values()))

class CharacterizationReport:
    pages: int
    failed_pages: int  # new, right after ``pages``
    symbol_standards: Mapping[str, int]  # enum order, then UNKNOWN_SYMBOL_STANDARD

# characterize(): ranked = ranked_conventions(tally.conventions)
#   conventions=dict(ranked)
#   symbol_standard_guess=proposal.profile.symbol_standard if ranked else None
# aggregate(): failed_pages=sum(i.failed_pages for i in items)

# adapters/sql_repository.py
guess = report["symbol_standard_guess"]
symbol_standard_guess=None if guess is None else SymbolStandard(guess)
# and on write: None if item.symbol_standard_guess is None else item.symbol_standard_guess.value

# ops/electrical_characterize.py
# build_report(): entry["failed_pages"] = record.failed_pages  (to_plain walks fields only)
# render_markdown(): add "- failed pages: {failed_pages} of {pages}" above the OCR line,
#   a "failed" column after "pages", and doc["symbol_standard_guess"] or UNKNOWN_SYMBOL_STANDARD
```

- `failed_pages` خاصية لا حقل: لا تُخزَّن ولا تدخل `to_plain`، فلا يمكن أن تختلف عن `page_count` و`text_kinds`، والمساواة بين الأداة والعامل تبقى مثبتة كما هي.
- الأداة لا تقرأ مفتاح `failed_pages` من ملف الفحص، لأن `characterize` هو الموضع الوحيد المشترك بين الأداة والعامل، وقراءته في الأداة وحدها مسار ثانٍ. وأسباب الفشل تبقى حيث هي: `PageStageOut.reason` وملف الفحص.

### 3.3 نتيجة البند 2: فحص `get_cdrawings()`

- **الاستعمالات:** موضع واحد، `inspect.py:155`. ولا `get_drawings` في `src` ولا `services`، وفي `tests` استعمال واحد أضافه `fa030c4` للتحقق من الشكل. ولا قراءة `.x` أو `.y` أخرى في `services/ele_parse`.
- **الأشكال، مقيسة على 1.28.0 (`.venv`) و1.28.2 (الصورة):**

| العنصر | ما بعد الرمز | أثره في `_paths` |
|---|---|---|
| `c` | 4 نقاط tuple | يُقرأ، وهو موضع العيب |
| `l` | نقطتان tuple | يُتخطى، ولذلك لا يُعدّ النص المرسوم مقاطع |
| `re` | مستطيل tuple، وعدد صحيح | يُتخطى، ولا يصلح لقراءة نقاط |
| `qu` | رباعي tuple واحد | يُتخطى، ولا يصلح لقراءة نقاط |

لا شيء آخر يعتمد على عناصر غير `c`. وفحص `item[0] != "c"` قبل القراءة هو ما يحمي `re` و`qu`، فيبقى في مكانه.

## 4. البيانات

- **لا تغيير:** لا ترحيل، ولا جدول، ولا فهرس، ولا RLS. يتغيّر JSON داخل `drawings_characterizations.report` فقط: `symbol_standard_guess` قد يكون `null`، ولا CHECK عليه غير الحجم.
- **`detection.basis.symbol`** في نسخ الملف الجديدة قد يأتي فارغاً حين لا دليل. والقراءة لا تعتمد على شكله.
- **التراجع:** `git revert` يكفي، مع تحفظ واحد: الشيفرة القديمة تستدعي `SymbolStandard(None)` فترفع خطأً على صف جديد فيه `null`. فبعد التراجع تُعاد معالجة تلك الوثائق، فتُستبدل صفوفها. ويخص هذا صفوف ما بعد نشر F0 فقط.

## 5. خطة الاختبار

وحدة كلها، وتعمل بـ`pytest tests/unit tests/architecture tests/eval`. ولا اختبار حي، ولا تشغيل على المكدّس.

| المعيار | المستوى | الموضع والاختبار |
|---|---|---|
| F0‑AC‑1 | وحدة، وعملية فرعية حقيقية | `test_ele_parse_service.py`: مصنع `curve_pdf` يرسم بـ`page.new_shape().draw_bezier(...)` منحنياً صغيراً وآخر كبيراً. عبر `child.handle` ثم `inspect_file`: `ok`، و`short_curve_path_count == 1`، ولا `failed_pages`. |
| F0‑AC‑2 | وحدة | الملف نفسه: صفحة بـ`draw_line` و`draw_rect` و`draw_quad` ومنحنٍ صغير: `ok`، والعدّاد 1. |
| F0‑AC‑3 | وحدة وعقد | `test_electrical_profile_detection.py`: وثيقة 3 صفحات وصفحتان مصنفتان تعطي 1، وصفحة `EMPTY` تعطي 0، و`aggregate` يجمع. `test_ops_electrical_characterize.py`: JSON، وسطر Markdown وعموده. `test_api_electrical_router.py`: الحقل في الاستجابتين. |
| F0‑AC‑4 | وحدة | `test_ops_electrical_characterize.py`: ملف مثبت بثلاث صفحات تُحذف منه الثانية، بطريقتين (`failures={2: PARSE_ERROR}` وصفحة متروكة)، على الملف داخل `tmp_path`. يبقى عدد الملفات المثبتة 5. مقارنة `to_plain` و`ours.failed_pages == stored.failed_pages == 1`. |
| F0‑AC‑5 | وحدة | `test_electrical_classification.py`: `vector_path_count` 400 يعطي `VECTOR_OUTLINED`، و399 يعطي `VECTOR_TEXT`. صفحة بلا حرف و5,000 مسار تعطي `VECTOR_OUTLINED` و`needs_ocr`، وهو سلوك مقبول يُثبَّت عمداً. صفحة بنص سليم و5,000 مسار تبقى `VECTOR_TEXT` لا `MIXED`. `RASTER` و`VECTOR_GARBLED` قبل القاعدة الجديدة. وصفحة قريبة من العتبة لا تصير `NEEDS_REVIEW`. |
| F0‑AC‑6 | وحدة | `test_electrical_profile_detection.py`: `tally` فارغ: `None`، والأساس بلا `convention:`، ومع منتج له تلميح: `None` والأساس `producer:...`. `aggregate` لا يرفع `ValueError`، و`symbol_standards == {"unknown": n}`، ومجموعه = `documents`. `test_ops_...`: Markdown بلا `iec_60617`. `test_api_...`: `null` و`unknown`. |
| F0‑AC‑7 | وحدة | `test_electrical_profile_detection.py`: بلا رموز، `profile.symbol_standard` و`confidence` و`field_confidence` و`profile.fingerprint()` كما كانت (`IEC_60617`، وثقة الرمز 0.0). `test_electrical_adapters.py`: `_characterization` على صف قديم مكتوب باليد (`"iec_60617"`، بلا `mixed_standards`، و`page_count` 5، ومجموع `text_kinds` 2) يعطي `failed_pages == 3`، وذهاباً وإياباً مع `null`. |
| F0‑AC‑8 | يدوي بعد الدمج | §9. |

- الاختبارات القائمة في `test_electrical_classification.py` تبقى كما هي، لأن `make_signals` تضع 300 مسار، وهو دون 400.
- لا ملف مثبت في `fixtures/electrical/inspections` يتغيّر.
- البوابات كاملة بالترتيب في `CLAUDE.md`، و`deploy/prometheus/test-rules.sh` لا يتأثر.

## 6. ترتيب التنفيذ والتوازي

منفّذ واحد، فلا توازٍ. الخطوة 1 مستقلة، والخطوة 4 تعتمد على 3.

1. `inspect.py` مع اختباراته.
2. `classification.py` و`pipeline.py` مع اختباراتهما.
3. `profile_detection.py` و`domain/characterization.py` مع اختباراتهما.
4. `sql_repository.py` و`dto` و`routers` و`openapi.yaml` و`electrical_characterize.py` مع اختباراتها.
5. البوابات.

## 7. المخاطر والقرارات

**لا بند ⚠️.** لا شيء هنا يمس الأمن أو الصلاحيات أو العزل، ولا تبعية جديدة. وقرار القاعدة شمله أمر المالك «ابدأ F0». والقرارات المتخذة:

**ق‑1: الإشارة `vector_path_count`، لا إشارة جديدة.**

| | النتيجة على القياس |
|---|---|
| الصفحات الثلاث الحالية (نص 0) | مسارات 4,523 و5,242 و9,139، ومنحنيات قصيرة 1,571 و0 و175 |
| أعداد الجولة الثانية المحفوظة (76 صفحة) | 74 صفحة قليلة النص، أقلها 3,515 مساراً |
| الصفحات الواقعة في القاعدة 6 خطأً | 46، كلها بلا حرف، وكلها ≥ 3,515 مساراً |
| محاكاة القاعدة الجديدة على الـ76 | `vector_outlined` 74، و`mixed` 1، و`vector_text` 1، وOCR 75 من 76 |

الإشارة القائمة تفصل كل هذه الصفحات، فلا حاجة إلى تغيير العقد. وعلى هذا القياس لا تضيف إشارة عدّ المقاطع القصيرة فصلاً جديداً.

**ما رُفض:**
- **إشارة `short_line_count`:** تغيّر `contract.py` و`signals_from_json` و`PageSignals` و`_abridged_signals`، وتحتاج صوراً متزامنة مع العامل، وقراءة متسامحة للصفوف القديمة. ومزيتها الوحيدة تقليل خطأ غير ضار. وF1 سيبني الهندسة كلها، وهو الموضع الصحيح لإشارة أدق.
- **عتبة مستقلة `outlined_paths_min`:** تغيّر `_THRESHOLD_RANGES` و`to_body`، فتتغيّر بصمة كل ملف افتراضي. تُضاف لاحقاً إضافةً إن طلبها مشروع. والثمن الآن أن اسم `outlined_curves_min` أضيق من معناه.
- **ثابت في الشيفرة:** يخالف NFR-CFG-01.
- **تطبيق الإشارة على `MIXED`:** كل مخطط بنص سليم فيه مسارات كثيرة، فيصير كله `MIXED`. وكذلك إسقاط `short_curve_path_count`: فهو يعدّ عناصر لا مسارات، وقد تجتمع منحنيات كثيرة في مسار واحد.

**ق‑2: العتبة 400 قيمة بداية، لا قيمة مقيسة.** أي قيمة بين 1 و3,515 تعطي النتيجة نفسها، فالقياس يحدّها من أعلى فقط. والمصدر مورد واحد، وصفحاته كبيرة (نحو 3,370 × 2,384 pt)، ولا صفحة رسم خالص بينها. ويبقى `INC1‑R‑3` مفتوحاً.
- **لماذا القيمة المنخفضة:** الخطأان غير متكافئين. صفحة رسم خالص بقليل من التسميات تصير `needs_ocr`، فيضيع زمن OCR ولا يُستخرج شيء خاطئ. أما صفحة نصها مرسوم وتبقى `VECTOR_TEXT` فتفقد معرّفاتها بصمت.
- **وأثر الخطأ الأول اليوم صفر:** `needs_ocr` في INC‑1 يُخزَّن ويُعرض ويُعدّ، ولا شيء يوجّه عليه.
- **ما تفوّته القاعدة:** صفحة نص مرسوم فيها أقل من 400 مسار، ومُصدِّر يجمع الضربات كلها في مسارات قليلة. وفي عيّنتنا وسيط العناصر لكل مسار بين 1 و3، وأكبر مسار 405 عناصر، فلا يظهر هذا الخطر هنا، وقد يظهر عند مورد آخر.

**ق‑3: `_ambiguous` لا يتغيّر.** يقرأ النسب فقط، و`ambiguity_margin` نسبة لا عدد. فالصفحة قرب عتبة المسارات لا تصير `NEEDS_REVIEW`، كما هو الحال اليوم مع المنحنيات.

**ق‑4: معنى «الدليل» على معيار الرموز: اصطلاح مرصود في الرموز النصية.** تلميح المنتج واللغة يحرّكان المقترح، ولا يكفيان وحدهما للادعاء. فبلا رموز يفوز التصويت الافتراضي (1.0) على أي تلميح (≤ 0.5 في `producers.toml`)، ولو كان التلميح ضد `iec_60617`. وعندها يبقى التلميح ظاهراً في `symbol_standard_basis`، والتخمين `None`. ويتبع `None` في السجل و`unknown` في المجموع سابقة `producer_family` و`UNKNOWN_PRODUCER`.

**ما رُفض:** عضو `UNKNOWN` في `SymbolStandard` (يصير قيمة مقبولة في الملف)، وحقل `symbol_standard_evidence` بجوار `iec_60617` (الادعاء يبقى)، و`symbol_standard_guess = "unknown"` كنص (يكسر `SymbolStandard` عند القراءة).

**ق‑5: `PIPELINE_VERSION` إلى `1.1.0`.** تعليقه يوجب الرفع حين يمكن أن يتغيّر مخرج مرحلة، وهذا ما يحدث هنا. ولا شيء يعيد المعالجة تلقائياً بسببه. **تصحيح بعد المراجعة:** `_conclude` يبني السجل من صفوف الصفحات المخزّنة ويختم عليه الإصدار الحالي. فوثيقة من قبل F0 تُؤكَّد بعد النشر تحمل `1.1.0` وهي بتصنيف القاعدة القديمة، فلا يكشفها سطر `versions`. وكذلك، من القراءة، إعادة معالجة جزئية ومهمة استؤنفت عبر النشر.

## 8. التوافق والنشر

- **تقارير التوصيف المخزّنة:** تُقرأ بلا ترحيل، و`failed_pages` فيها صحيح من `page_count` و`text_kinds`. والصف القديم بلا دليل يبقى يقول `iec_60617` وأساسه `convention:iec_81346` حتى تُعاد معالجته. ويظهر عليه `pipeline 1.0.0` ما لم يُعَد بناؤه بالتأكيد، فعندها يحمل `1.1.0` بتصنيف قديم (ق‑5).
- **بعد النشر:** كل وثيقة من قبل F0 تحتاج `reprocess` كاملاً، ولو أُكّدت بعد F0، وإلا بقي تصنيفها القديم. ولم يُعَدّ ما على المكدّس منها.
- **ترتيب ذلك:** `reprocess` يرفض بـ409 `electrical.profile_unconfirmed` ما دام الملف مقترحاً، وهذه حال الوثائق بلا رموز عند ثقة 0. فلها: تأكيد الملف أو استبداله أولاً، ثم `reprocess` كامل. وبين الخطوتين يحمل السجل `1.1.0` بتصنيف قديم.
- **الصفحات الفاشلة قبل F0:** تبقى `failed`، لأن `_UNFINISHED_STAGE` هو `pending` و`running` فقط. فتحتاج `reprocess` صريحاً لكل وثيقة. ولم أعدّ الصفوف الموجودة، فالمكدّس خارج هذا العمل.
- **عقد `ele-parse`:** `SCHEMA` = 1، بلا مفتاح جديد ولا تغيّر معنى.
- **ترتيب النشر:** لا تزامن بين صورتي `ele-parse` والتطبيق:
  - تطبيق جديد مع `ele-parse` قديم: صفحات المنحنيات تبقى فاشلة، لكنها تظهر الآن في التقرير.
  - `ele-parse` جديد مع تطبيق قديم: تنجح الصفحات بالتصنيف القديم.
- **قيد واحد:** `app` قبل `worker-electrical` أو معه، لأن `app` القديم لا يقرأ صف `null` الذي يكتبه عامل جديد.
- **الصور:** إعادة بناء الصورتين ونشرهما قرار بشري بعد الدمج. ولا يمس البناء ولا الاختبار `aizzak/ele-parse:dev`. ولا تغيير في `docker-compose.yml` ولا CI ولا `.env.example`.

## 9. خطوة ما بعد الدمج (F0‑AC‑8)

ليست مهمة مطوّر. ينفذها المنسّق في الخطوة 11و:

1. بناء صورة من `master` المدموج بوسم مستقل، `aizzak/ele-parse:f0-<sha>`.
2. تشغيل `python -m services.ele_parse inspect` على كل ملف في حاوية مؤقتة `--network none --read-only --cap-drop ALL`، والمجلد `:ro`.
3. حفظ المخرج خارج المستودع باسم `<sha256>.json`، ثم `app.ops.electrical_characterize` عليه.
4. المقارنة بأرقام F0‑AC‑8. فأعداد §7 مأخوذة من مسبار منفصل، لا من الأداة، وهذه الخطوة تثبتها.
5. تحديث `inc-1/characterization.md` بالأعداد فقط. والـ76 لا تُعاد، لأن المالك أبقى ثلاثة ملفات.

## 10. وثائق تصبح قديمة (متابعة، لا تُعدَّل في F0)

- `docs/lld-inc-1.md`: السطر 934 (الإصدار)، والسطر 1043 (القاعدة 4)، والسطران 1169 و1188 (`symbol_standard_guess` و`symbol_standards`)، وإضافة `failed_pages`.
- `docs/design/06-domain-models.md:317`: التخمين اختياري، و`failed_pages`.
- `inc-1/characterization.md` §5: يتحدث في الخطوة 11و.
- لا تعديل مطلوب في SRS وHLD: FR-ING-08 يذكر الخطوط أحادية الضربة أصلاً، ولا نص في HLD عن هذه القاعدة. و`docs/design/03-api-spec.md` لا يسرد حقول `CharacterizationOut`.

</div>
