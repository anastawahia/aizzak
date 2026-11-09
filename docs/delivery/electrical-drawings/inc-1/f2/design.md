<div dir="rtl">

# التصميم التقني: F2، البند 1، وسوم KKS وأرقام أجهزة ANSI وأطراف المورد الأول في `shared` وملف الإعداد

- **التاريخ:** 2026-11-08
- **الفرع:** `docs/electrical-drawings-f2` من `master` = `ab4da7f`. وهذه الجولة وثائق فقط. والبند 1 يُبنى بعد دمجها، على فرع شيفرة جديد من `master`.
- **المرجع:** `inc-1/f2/requirements.md` (§3.1 و§3.2 و§4 و§5 و§6، وA‑5)، و`inc-1/f2/stories.md` (US‑1..4 وUS‑10 وUS‑9)، و`docs/requirements.md` 0.20 (D‑36)، و`docs/hld.md` 0.19 (DEC‑20)، و`inc-1/f1/design.md` قالباً للشكل.
- **الحالة:** ✅ قرارات المالك في 2026-11-08: Q1..Q9 بالتوصية، وA‑5 بخلاف اقتراح المحلّل (أطراف `9A-99` في البند 1). وطُبّقت S1..S19 وH‑1..H‑12 في هذه الجولة (§10)، ونتائج التحقق في §10.4.
- **الإعادة 1 (2026-11-09):** ردّ على مراجعة الشيفرة (`inc-1/f2/review.md`، FAIL) وعلى المراجعة الأمنية (`inc-1/f2/security-review.md`). وثائق فقط، بلا شيفرة. كل موضع تغيّر معلَّم «(عُدّل في الإعادة 1)»، والتفصيل وقائمة الإصلاح في §11.
- **المستودع عام:** أنماط مجردة وأمثلة مخترعة فقط (`20XYZ10AB123`، `87T`، `1X-25`). لا وسم ولا اسم من العيّنات. و`1X-25` استثناء مكتوب بقرار المالك (§11.5).
- **اصطلاح المعرّفات:** في هذه الوثيقة كل معرّف بالشرطة غير المنكسرة (`FR‑ID‑15`، `D‑36`، `F2‑AC‑1`)، كما طلب المنسّق. وأسماء الشيفرة بالإنجليزية كما هي، وتعليقات الشيفرة بالشرطة العادية كما في المستودع.
- **المسارات:** `src/...` و`tests/...` و`docs/design/...` نسبةً إلى جذر المستودع. و`docs/...` و`inc-1/...` نسبةً إلى `docs/delivery/electrical-drawings/`.

## 1. الملخص

**ما يُبنى.** البند 1 من F2 وحده، في PR شيفرة واحد من `master`، قبل LLD الزيادة INC‑2:
1. **في `shared` (CMP‑06):** نوعا معرّف جديدان، وسم KKS (`FR‑ID‑15`) ورقم جهاز ANSI (`FR‑ID‑16`)، واصطلاحان إضافيان لهما. وبنية KKS وجدول ANSI بيانات في `identifiers.toml`. وقواعد الالتباس التي تخصّ `shared` (§3.9). وأطراف المورد الأول بصيغة `1X-25` تحت IEC 81346 (A‑5، `FR‑ID‑03`).
2. **في `drawings` (CMP‑01):** ثلاثة حقول إضافية في ملف الإعداد، واختيارها في `rule_selection`، واكتشافها في STG‑01، و`is_mixed` على اصطلاحات الأجهزة وحدها، وأثرها في تقرير التوصيف.
3. **في العقد:** وصف الحقول في `docs/design/openapi.yaml` و`docs/design/06-domain-models.md`، واختبار يربط الوصف بالحقول.
4. **في القياس:** حالات M‑04 جديدة بأمثلة مخترعة، وM‑04 يبقى 1.0.

**أين يقع.** في الوحدة `electrical`: الحزمة النقية `shared`، والنطاق والتطبيق في القسم `drawings`. لا يمسّ `circuits` ولا `search` ولا `diagnostics` ولا `ele-parse` ولا عقود المنصة. ولا API جديد، ولا جدول، ولا ترحيل، ولا اعتمادية، ولا إعداد نشر.

**المبدأ الحاكم: KKS وANSI إضافيان ويُفعَّلان بالاختيار.** لا يقرؤهما المحلّل إلا حين تفعّلهما القواعد المضيَّقة بملف الإعداد. فقراءة كل وثيقة لا تفعّلهما، وكل حالات M‑04 الـ192، تبقى كما هي حرفاً. والاستثناء الوحيد عدّ الاصطلاحات (`tally`): يرى KKS وANSI دائماً، ليقترحهما الاكتشاف.

**السابقة التي يتبعها.** اصطلاح أكواد الحروف الصينية: جدول بيانات في `identifiers.toml` (`cn_letter_codes`)، ودالة تحليل في `_ORDER` (`parse_letter_code`)، وفئة جهاز باسمنا (`device_class`)، واختيار بالاسم في `RuleSelection`، وحالات في `cases/identifiers.toml`. والأطراف على `shared/terminals.py` كما هو. وملف الإعداد على `drawings/domain/profile.py` كما هو: `from_body` صارم لا يعيد ما أرسله العميل، و`to_body` قانوني، و`InvalidProfile` يسمّي الحقل.

**القصص المبنية الآن:** US‑1 (`F2‑AC‑1..7`)، وUS‑2 (`F2‑AC‑8..12`)، وUS‑3 (`F2‑AC‑13..18`)، وUS‑4 (`F2‑AC‑19..20`)، وUS‑10 (`F2‑AC‑41..43`). وUS‑9 (`F2‑AC‑21..22`) تتحقق في هذه الجولة (§10.4). وUS‑5..8 لـINC‑2 (§8).

## 2. الملفات

المنفّذ كله `backend-developer`. لا عمل لـ`data-engineer`: لا ترحيل ولا DDL (§4). ولا لـ`frontend-developer`: لا واجهة في المستودع.

| المسار | إنشاء/تعديل | المسؤولية | المنفّذ |
|---|---|---|---|
| `src/app/modules/electrical/shared/kinds.py` | تعديل | `IdentifierKind.KKS_TAG` و`ANSI_DEVICE`؛ `Convention.KKS` و`ANSI`؛ `DEVICE_CONVENTIONS` و`ADDITIVE_CONVENTIONS`؛ `KksClass` و`KksPart` و`KksTag` و`AnsiFunction` و`AnsiDevice`؛ حقول `kks` و`ansi` و`alternatives` وخاصية `needs_context` في `ParsedIdentifier` (§3.2) | backend-developer |
| `src/app/modules/electrical/shared/kks.py` | إنشاء | `parse_kks_tag`: الصيغ، والبادئة، والشكل الخارجي، وأسباب عدم الصلاحية (§3.5) | backend-developer |
| `src/app/modules/electrical/shared/ansi.py` | إنشاء | `parse_ansi_compound` و`parse_ansi_device` وصيغتاهما بالسياق، و`is_bare_ansi_number`، و`read_compound` (§3.7) | backend-developer |
| `src/app/modules/electrical/shared/terminals.py` | تعديل | اصطلاح الصيغة التي يسبق فيها الرقمُ الرمزَ من القواعد، لا من الشيفرة (A‑5، §3.6) | backend-developer |
| `src/app/modules/electrical/shared/parser.py` | تعديل | الترتيب الجديد، والمسار بالسياق، والقراءات البديلة، وعدّ KKS وANSI في `tally` (§3.4) | backend-developer |
| `src/app/modules/electrical/shared/rules/errors.py` | إنشاء | `RuleError` وحده، ورقة بلا استيراد، ليستورده `systems.py` دون دورة. ويعيد `rules/__init__.py` تصديره، فلا يتغيّر أي استيراد قائم | backend-developer |
| `src/app/modules/electrical/shared/rules/systems.py` | إنشاء | `KksPartRule` و`KksForm` و`KksRules` و`AnsiRules`، وقرّاء الجداول الجديدة والتحقق منها، و`checked_ansi_suffixes` (§3.3) | backend-developer |
| `src/app/modules/electrical/shared/rules/__init__.py` | تعديل | حقول `IdentifierRules` الجديدة و`from_mapping` و`narrowed` و`with_additive`، وحقل `RuleSelection.extra_ansi_suffixes` (§3.3) | backend-developer |
| `src/app/modules/electrical/shared/rules/identifiers.toml` | تعديل | `version = "1.1.0"`، وجداول `[kks]` و`[ansi]` و`[confusables]` و`[terminals.digits_first]` (§3.3) | backend-developer |
| `src/app/modules/electrical/shared/version.py` | تعديل | `SHARED_VERSION = "1.3.0"` | backend-developer |
| `src/app/modules/electrical/drawings/domain/profile.py` | تعديل | `PlantDesignation` و`DeviceFunctionNumbers`؛ الحقول الثلاثة؛ `from_body` و`to_body` و`rule_selection` و`with_detected`؛ وحصر `identifier_convention` في اصطلاحات الأجهزة (§3.8) | backend-developer |
| `src/app/modules/electrical/drawings/domain/profile_detection.py` | تعديل | `ranked_device_conventions`، و`is_mixed` و`_convention` على اصطلاحات الأجهزة، واكتشاف الحقلين، و`basis["systems"]` (§3.10) | backend-developer |
| `src/app/modules/electrical/drawings/domain/characterization.py` | تعديل | تخمين معيار الرموز من اصطلاحات الأجهزة وحدها، والمجمّع يعدّ وثائق KKS وANSI (§3.11) | backend-developer |
| `src/app/modules/electrical/drawings/application/projects.py` | تعديل | سطر واحد: فحص تسميات الجهد تحت كل الاصطلاحات، ومنها KKS وANSI (§3.8) | backend-developer |
| `docs/design/openapi.yaml` | تعديل | الأوصاف وحدها، ولا خاصية جديدة (§3.12) | backend-developer |
| `docs/design/06-domain-models.md` | تعديل | سطر `DrawingProfile` في §11 بالحقول الثلاثة (§3.12) | backend-developer |
| `tests/eval/electrical/m04_identifiers.py` | تعديل | المفتاح `select` في الحالة، و`parse_case`، والمفاتيح الجديدة في `observed` (§5.2) | backend-developer |
| `tests/eval/electrical/cases/identifiers.toml` | تعديل | حالات KKS وANSI و`1X-25` بأمثلة مخترعة، وتعليق الرأس يذكر `select` (§5.2) | backend-developer |
| `tests/eval/electrical/README.md` | تعديل | سطر عن `select` | backend-developer |
| `tests/unit/test_electrical_shared_kks.py` | إنشاء | US‑1 | backend-developer |
| `tests/unit/test_electrical_shared_ansi.py` | إنشاء | US‑2 | backend-developer |
| `tests/unit/test_electrical_f2_profile.py` | إنشاء | US‑3 في النطاق: الحقول، و`rule_selection`، والاكتشاف، والتوصيف | backend-developer |
| `tests/unit/test_electrical_shared_terminals_wires.py` | تعديل | US‑10 | backend-developer |
| `tests/unit/test_electrical_shared_parser.py` | تعديل | قراءة الحالات بـ`parse_case`، وأمثلة ملحق أ الجديدة في `_APPENDIX_A`، و`F2‑AC‑7` | backend-developer |
| `tests/unit/test_electrical_shared_rules.py` | تعديل | تثبيت الإصدارين الجديدين، وقيم `IdentifierKind` الإحدى عشرة، وطفرات الجداول الجديدة | backend-developer |
| `tests/unit/test_api_electrical_router.py` | تعديل | `F2‑AC‑16..18` على الموجّه، واختبار الوصف (§3.12) | backend-developer |

**ملفات لا تتغيّر، وتُذكر لأنها قد تبدو معنية:**
- `src/app/modules/electrical/drawings/adapters/rule_files.py`: يمرّر الخريطة كاملة إلى `from_mapping`، فتصلها الجداول الجديدة دون تعديل.
- `src/app/modules/electrical/drawings/adapters/sql_repository.py`: الجسد JSONB، والعدّ يُحفظ بمفاتيح `Convention` ويُقرأ بـ`_by_enum` (`sql_repository.py:1337-1339`)، فيحمل `kks` و`ansi` دون تعديل.
- `src/app/modules/electrical/drawings/application/inspection.py`: `combine_tallies` يمرّ على أعضاء `Convention` كلها (`inspection.py:157`)، و`detect_profile` يبني ملف الإعداد بنفسه.
- `src/app/api/...`: الجسد `dict` في `DrawingProfileIn` وأخواته، والصلاحيات كما هي.
- `.importlinter` و`tests/architecture/`: لا حزمة جديدة (`kks.py` و`ansi.py` و`systems.py` و`errors.py` وحدات لا حزم)، فلا يتغيّر عدد ملفات `__init__.py` الذي يثبّته `test_the_skeleton_invariants_hold`.
- `tests/eval/electrical/m32_profile_detection.py`: يبقى. وتوسيعه للحقلين في INC‑2 (`F2‑AC‑40`).

## 3. العقود

### 3.1 الواجهات البرمجية

لا مسار جديد. يتغيّر ما يقبله جسد ملف الإعداد في ثلاثة مسارات قائمة، وما تعيده ثلاثة:

| الطريقة | المسار | الطلب | الاستجابة | الأخطاء | الصلاحية |
|---|---|---|---|---|---|
| PUT | `/api/v1/electrical/projects/{id}/profile` | `DrawingProfileIn`، والجسد قد يحمل `plant_designation` و`device_function_numbers` و`ansi_suffixes` | `DrawingProfileOut`، إصدار جديد | 422 `electrical.invalid_profile` يسمّي الحقل ولا يعيد القيمة؛ 404 | `electrical:manage` |
| PUT | `/api/v1/electrical/documents/{id}/profile` | كما سبق | كما سبق | كما سبق | `electrical:manage` |
| POST | `/api/v1/electrical/documents/{id}/profile-confirmation` | `ProfileConfirmIn`، والجسد المعدّل كما سبق | كما هو اليوم | كما سبق، و409 كما هو | `electrical:manage` |
| POST | `/api/v1/electrical/projects` | `ElectricalProjectIn.profile` كما سبق | كما هو | 422 كما سبق | `electrical:manage` |
| GET | `…/projects/{id}/profile`، `…/documents/{id}/profile` | — | `body` يحمل الحقول الثلاثة حين تخالف افتراضاتها، و`proposal.field_confidence` يحمل مفتاحين جديدين، و`proposal.basis` مفتاح `systems` | كما هو | `electrical:read` |
| GET | `…/projects/{id}/characterization`، `…/documents/{id}/characterization` | — | `conventions` قد تحمل `kks` و`ansi` | كما هو | `electrical:read` |

**العميل وأنواعه:** لا واجهة أمامية في المستودع (`docs/hld.md` §12.5). ولا خاصية جديدة في أي مخطط، لأن الجسد كائن مفتوح (`additionalProperties: true`)، فلا يتغيّر عميل مولَّد. والواجهة المنفصلة تقرأ القيم الجديدة من وصف `DrawingProfileIn` في `openapi.yaml`.

### 3.2 أنواع النواة (`shared/kinds.py`)

```python
class IdentifierKind(StrEnum):
    ...  # the nine kinds, unchanged and in the same order
    KKS_TAG = "kks_tag"  # 20XYZ10AB123, =20XYZ10AB123 (FR-ID-15, D-36)
    ANSI_DEVICE = "ansi_device"  # 87T, 50BF, 27-1, 50/51 (FR-ID-16, D-36)


class Convention(StrEnum):  # FR-ID-13, D-36
    IEC_81346 = "iec_81346"
    GBT_5094 = "gbt_5094"
    CN_LETTER_CODES = "cn_letter_codes"
    NFPA_JIC = "nfpa_jic"
    # Additive: they add to the document's ONE device convention and never compete with it,
    # so they are never "mixed" (FR-ING-12) and never imply a symbol standard. Appended LAST:
    # ties go to the earlier member, and the four above keep their order.
    KKS = "kks"  # plant designation (FR-ID-15)
    ANSI = "ansi"  # ANSI/IEEE C37.2 device function numbers (FR-ID-16)


DEVICE_CONVENTIONS: Final = frozenset(
    {Convention.IEC_81346, Convention.GBT_5094, Convention.CN_LETTER_CODES, Convention.NFPA_JIC}
)
ADDITIVE_CONVENTIONS: Final = frozenset({Convention.KKS, Convention.ANSI})


class KksClass(StrEnum):  # FR-ID-15
    SYSTEM = "system"
    EQUIPMENT = "equipment"
    COMPONENT = "component"
    CABLE = "cable"


@dataclass(frozen=True, slots=True)
class KksPart:
    name: str  # the rules' part name: "system" | "equipment" | "component" | "cable"
    level: int | None  # 1..3 (FR-ID-15); None for a cable number
    text: str  # "20XYZ10"
    runs: tuple[str, ...]  # ("20", "XYZ", "10"): the maximal runs of one position type


@dataclass(frozen=True, slots=True)
class KksTag:
    form: str  # the rules' form name ("equipment", "equipment_cable", ...)
    kks_class: KksClass
    prefix: str | None  # "=" | "+" | "++" as written; None when absent
    prefix_kind: str | None  # the rules' name for it: "process" | "installation" | "location"
    parts: tuple[KksPart, ...]

    @property
    def level(self) -> int:  # the deepest level among the parts (1..3)
        return max((p.level for p in self.parts if p.level is not None), default=0)


@dataclass(frozen=True, slots=True)
class AnsiFunction:
    number: int  # 1..99
    suffix: str  # "" | "T" | "BF" ...
    function_class: str | None  # OUR class name from the rules (NFR-LIC-05); None if unmapped


@dataclass(frozen=True, slots=True)
class AnsiDevice:
    functions: tuple[AnsiFunction, ...]  # one; two or three for a compound (50/51)
    instance: int | None  # 27-1 -> 1


@dataclass(frozen=True, slots=True)
class ParsedIdentifier:
    ...  # every existing field, unchanged and in the same order
    # Additive (D-36), last and defaulted, so every existing construction stays valid:
    kks: KksTag | None = None
    ansi: AnsiDevice | None = None
    # The other kinds the SAME text reads as under the enabled rules, for the F2 pairs only
    # (bare number / ANSI, compound / cross-reference, prefixed KKS / IEC). Set only without
    # a hint: a caller with a hint has resolved it. Empty for every other kind (no change).
    alternatives: tuple[IdentifierKind, ...] = ()

    @property
    def needs_context(self) -> bool:
        """Q2 (a): an ANSI number names a function, not a device; its full identity comes
        from context (a KKS tag, a stacked second part, the inherited structure)."""
        return self.kind is IdentifierKind.ANSI_DEVICE or IdentifierKind.ANSI_DEVICE in self.alternatives
```

- **`ParsedIdentifier.convention`** يكون `Convention.KKS` لوسم KKS و`Convention.ANSI` لرقم ANSI. فيفصّل M‑04 وM‑30 حسب الاصطلاح دون حقل جديد.
- **`device_class`** لرقم ANSI المفرد هو فئته الوظيفية، على سابقة كود الحرف (`KM` → `contactor`). وللمركّب `None`، والفئات في `ansi.functions`. ولوسم KKS `None`: لا معنى لرمز النظام في المنتج (Q8).
- **أسباب عدم الصلاحية الجديدة:** `kks.malformed`، و`kks.letter_in_digit_position`، و`kks.digit_in_letter_position`، و`kks.excluded_letter`، و`ansi.unknown_suffix`. كلها تُبطل الصلاحية، ولا يُضاف شيء إلى `NON_INVALIDATING_ISSUES`.

### 3.3 القواعد بيانات (`identifiers.toml` و`rules/`)

**الإضافات في `identifiers.toml`** (القيم الافتراضية صيغ المورد الأول، Q1 (ج)):

```toml
version = "1.1.0"

[terminals]
prefixes = ["X", "XT", "TB"]

# FR-ID-03, D-36 (A-5): the device conventions a digits-first strip (`1XT-5`, `1X-25`) reads
# under; the first one the profile enables wins. A prefix not listed reads under
# cn_letter_codes only (the INC-1 behaviour).
[terminals.digits_first]
X = ["cn_letter_codes", "iec_81346", "gbt_5094"]
XT = ["cn_letter_codes"]
TB = ["cn_letter_codes"]

# FR-ID-18, D-36: digit/letter pairs OCR confuses, as two-character strings (digit, letter).
# INC-1 uses them only to NAME why a KKS tag is invalid; the correction itself is INC-2.
[confusables]
pairs = ["0O"]

# KKS (FR-ID-15, D-36): the STRUCTURE only, never a key catalogue and never a meaning
# (NFR-LIC-05). A mask has one character per position: N a digit, A a letter, then
# optional trailing positions: n an optional digit, a an optional letter.
[kks]
excluded_letters = []  # A-3: letters KKS never uses in a letter position; empty until verified

[kks.prefixes]  # the identification types, when a tag is written with one (A-8, §7)
process = "="
installation = "+"
location = "++"

[kks.parts]
system = { level = 1, mask = "NNAAANN" }
equipment = { level = 2, mask = "AANNNa" }
component = { level = 3, mask = "AANN" }
cable = { mask = "NNNN" }  # A-2: four digits after a system or equipment tag

# First full match in file order wins.
[[kks.forms]]
name = "component"
parts = ["system", "equipment", "component"]
class = "component"

[[kks.forms]]
name = "equipment_cable"
parts = ["system", "equipment", "cable"]
class = "cable"

[[kks.forms]]
name = "equipment"
parts = ["system", "equipment"]
class = "equipment"

[[kks.forms]]
name = "system_cable"
parts = ["system", "cable"]
class = "cable"

[[kks.forms]]
name = "system"
parts = ["system"]
class = "system"

# ANSI/IEEE C37.2 device function numbers (FR-ID-16, D-36): the numbers, the suffix letters
# and OUR class names only, never the standard's text (NFR-LIC-05). A class may serve
# several numbers; a number without a class is still a valid number (function_class None).
[ansi]
max_compound = 3
suffixes = ["A", "B", "BF", "C", "D", "G", "GS", "L", "N", "P", "Q", "R", "S", "T", "TN", "U", "V", "X", "Y", "Z"]

[ansi.functions]
21 = "distance_protection"
25 = "synchronizing"
27 = "voltage_protection"
32 = "power_protection"
46 = "unbalance_protection"
47 = "unbalance_protection"
49 = "thermal_protection"
50 = "overcurrent_protection"
51 = "overcurrent_protection"
52 = "circuit_breaker"
59 = "voltage_protection"
64 = "earth_fault_protection"
67 = "overcurrent_protection"
79 = "reclosing"
81 = "frequency_protection"
86 = "lockout_relay"
87 = "differential_protection"
89 = "disconnect_switch"
94 = "trip_relay"
```

- **الجدولان بلا مواضع اختيارية في وسط الجزء:** القناع يحمل `n` و`a` في آخره فقط، فلا تفسيران لجزء واحد. وترتيب الصيغ يحسم ما بقي، كما يحسمه ترتيب أنماط الأسلاك اليوم (`identifiers.toml:37`).
- **جدول ANSI بداية لا قائمة كاملة.** الأرقام فيه هي التي في أمثلة القصص، ومعها أرقام شائعة. والفئات أسماء عامة نكتبها نحن، وأكثر من رقم يشترك في فئة، فلا ينسخ الجدول أسماء المعيار. وما لا فئة له يبقى رقماً صالحاً. ويضيف المراجع ما يلزم بالملف وحده.
- **لا يقرأ `shared` الملف.** كما اليوم: `PackagedRuleSource` يقرؤه ويمرّر الخريطة (`drawings/adapters/rule_files.py:91`).

**أنواع القواعد الجديدة (`shared/rules/systems.py`):**

```python
MAX_KKS_PARTS: Final = 8
MAX_KKS_FORMS: Final = 16
MAX_PARTS_PER_FORM: Final = 4
MAX_MASK_CHARS: Final = 8
MAX_ANSI_SUFFIX_CHARS: Final = 3
MAX_ANSI_COMPOUND: Final = 3
MAX_EXTRA_ANSI_SUFFIXES: Final = 64
CLASS_NAME: Final = re.compile(r"[a-z][a-z_]{0,39}", re.ASCII)  # NFR-F2-6
_NAME: Final = re.compile(r"[a-z][a-z_]{0,31}", re.ASCII)
_MASK: Final = re.compile(r"[NA]+[na]*", re.ASCII)
_SUFFIX: Final = re.compile(r"[A-Z]{1,3}", re.ASCII)


@dataclass(frozen=True, slots=True)
class KksPartRule:
    name: str
    level: int | None  # 1..3, or None (a cable number)
    mask: str  # "NNAAANN"


@dataclass(frozen=True, slots=True)
class KksForm:
    name: str
    parts: tuple[str, ...]  # part names, in order
    kks_class: KksClass


@dataclass(frozen=True, slots=True)
class KksRules:
    prefixes: Mapping[str, str]  # kind name -> symbol, e.g. "process" -> "="
    parts: Mapping[str, KksPartRule]
    forms: tuple[KksForm, ...]  # file order: the first full match wins
    excluded_letters: frozenset[str]


@dataclass(frozen=True, slots=True)
class AnsiRules:
    suffixes: frozenset[str]  # the packaged ones, plus the profile's after narrowing
    functions: Mapping[int, str]  # number -> our class name
    max_compound: int  # 2..3


def kks_from_mapping(data: Mapping[str, object]) -> KksRules: ...
def ansi_from_mapping(data: Mapping[str, object]) -> AnsiRules: ...
def confusables_from_mapping(data: Mapping[str, object]) -> tuple[tuple[str, str], ...]: ...
def digits_first_from_mapping(
    terminals: Mapping[str, object], prefixes: tuple[str, ...]
) -> Mapping[str, tuple[Convention, ...]]: ...
def checked_ansi_suffixes(raw: tuple[str, ...]) -> tuple[str, ...]: ...  # canonical, unique
```

**تحقق `from_mapping` من الجداول الجديدة** (`RuleError` يسمّي المدخل، ويفشل الإقلاع لا أول وثيقة):
- `[kks]` و`[ansi]` و`[confusables]` لازمة. ومفاتيح `[kks]` محصورة في `excluded_letters` و`prefixes` و`parts` و`forms`، ومفاتيح `[ansi]` في `max_compound` و`suffixes` و`functions`. فلا يدخل الملفَ كتالوجٌ ولا وصفٌ دون أن يرفضه التحقق (NFR‑F2‑6).
- البادئة رمز من `=` و`+`، حرف أو حرفان، ولا تتكرر. واسم الجزء والصيغة يطابق `_NAME`. والقناع يطابق `_MASK` بطول 1..8. والمستوى 1..3 أو غائب. والحدود العليا كما في الثوابت.
- كل صيغة تذكر أجزاءً معروفة، 1..4، وصنفها من `KksClass`. ولا تتكرر أسماء الصيغ.
- `excluded_letters` حروف لاتينية كبيرة مفردة.
- لواحق ANSI تطابق `_SUFFIX` ولا تتكرر. ومفاتيح `functions` أعداد 1..99، وقيمها تطابق `CLASS_NAME`. و`max_compound` بين 2 و`MAX_ANSI_COMPOUND`.
- كل زوج في `pairs` حرفان: رقم ثم حرف لاتيني كبير.
- `[terminals.digits_first]` اختياري. مفاتيحه من `prefixes`، وقيمه قوائم غير فارغة من اصطلاحات الأجهزة بلا تكرار. والرمز الغائب يُقرأ تحت `cn_letter_codes` وحده (`F2‑AC‑43`).

**(عُدّل في الإعادة 1)** في الجداول والأنواع ثلاث إضافات، وتفصيلها في §11:
- **مفتاحان لقاعدة الدليل (§11.1):** `[kks] evidence_min_level = 2` و`[ansi] non_evidence_suffixes = ["A"]`. كلاهما لازم، ويُضاف إلى `_KKS_KEYS` و`_ANSI_KEYS`. ومعهما حقلان: `KksRules.evidence_min_level: int` و`AnsiRules.non_evidence_suffixes: frozenset[str]`.
- **حدّ المواضع الاختيارية (S‑1، §11.6):** `MAX_OPTIONAL_PER_PART = 2` و`MAX_OPTIONAL_PER_FORM = 4`.
- **حقلان محسوبان في `KksRules` (الملاحظة 9):** `spec` و`shortest`، يُحسبان مرة واحدة عند البناء.
- **الإصداران لا يتغيّران:** `version = "1.1.0"` و`SHARED_VERSION = "1.3.0"` جديدان على هذا الفرع ولم يُدمجا بعد، فتدخل الإضافات فيهما.

**`IdentifierRules` و`RuleSelection` (`shared/rules/__init__.py`):**

```python
@dataclass(frozen=True, slots=True)
class RuleSelection:
    ...  # the six existing fields, unchanged
    extra_ansi_suffixes: tuple[str, ...] = ()  # D-36: added to the packaged suffixes


@dataclass(frozen=True, slots=True)
class IdentifierRules:
    ...  # version .. voltage_labels, unchanged
    kks: KksRules
    ansi: AnsiRules
    confusables: tuple[tuple[str, str], ...]  # (("0", "O"),)
    terminal_digits_first: Mapping[str, tuple[Convention, ...]]
    # defaulted, as today:
    conventions: frozenset[Convention] = field(default_factory=lambda: DEVICE_CONVENTIONS)
    plc_families: frozenset[PlcFamily] = field(default_factory=lambda: frozenset(PlcFamily))
    connection_point_separator: str = ":"
    cross_reference_pinned: bool = False  # the profile chose ONE cross-reference format

    def narrowed(self, profile: RuleSelection) -> IdentifierRules: ...
    def with_additive(self) -> IdentifierRules:
        """The same rules with KKS and ANSI enabled too (the tally's probe, §3.4)."""
```

- **الافتراض `DEVICE_CONVENTIONS` لا `frozenset(Convention)`:** القواعد المعبّأة دون تضييق تبقى على اصطلاحات الأجهزة الأربعة. فلا يتغيّر ناتج أي مستهلك لها اليوم: M‑04، و`tally` الأساسي، والتوصيف.
- **`narrowed`:** إن كانت `profile.conventions` غير فارغة، فالمفعَّل `(self.conventions | ADDITIVE_CONVENTIONS) & profile.conventions`. فاصطلاحات الأجهزة تضيق كما اليوم، وKKS وANSI لا يُفعَّلان إلا بالاسم. وإن كانت فارغة بقي `self.conventions`. فيبقى `rules.narrowed(empty) == rules` (`test_an_empty_selection_changes_nothing`).
- **ولواحق الملف:** `ansi.suffixes | checked_ansi_suffixes(profile.extra_ansi_suffixes)`. و`checked_ansi_suffixes` يطبّع بـ`canonical_form`، ويرفض الحرف المخفي، ويرفض ما لا يطابق `_SUFFIX`، ويرفض أكثر من 64. ولا تُترجَم لاحقة إلى تعبير نمطي أبداً: تُقارن بالعضوية في مجموعة (NFR‑F2‑3).
- **`cross_reference_pinned`:** `self.cross_reference_pinned or profile.cross_reference_format is not None`.

**الإصداران وأثرهما:**
- `identifiers.toml`: `version` من `1.0.0` إلى `1.1.0`، لأن الجداول إضافية. ولا مفتاح «schema» آخر في الملف.
- `SHARED_VERSION` من `1.2.0` إلى `1.3.0`: نوعان جديدان وترتيب جديد، والناتج القديم لا يتغيّر إلا بالاختيار.
- وأثرهما على البصمات وإعادة المعالجة في §4.

### 3.4 المحلّل (`shared/parser.py`)

**الترتيب الجديد** (الأسطر الجديدة معلَّمة):

```python
_ORDER: tuple[tuple[IdentifierKind, _Parse], ...] = (
    (IdentifierKind.PLC_ADDRESS, parse_plc_address),
    (IdentifierKind.ANSI_DEVICE, parse_ansi_compound),  # NEW: 50/51 before 12/3 (D-36)
    (IdentifierKind.CROSS_REFERENCE, parse_cross_reference),
    (IdentifierKind.VOLTAGE, parse_voltage),
    (IdentifierKind.BRIDGE, parse_bridge),
    (IdentifierKind.TERMINAL, parse_terminal),
    (IdentifierKind.KKS_TAG, parse_kks_tag),  # NEW: before IEC, for =20XYZ10AB123 (D-36)
    (IdentifierKind.REFERENCE_DESIGNATION, parse_reference_designation),
    (IdentifierKind.LETTER_CODE, parse_letter_code),
    (IdentifierKind.ANSI_DEVICE, parse_ansi_device),  # NEW: 87T 50BF 27-1, never a bare number
    (IdentifierKind.WIRE_NUMBER, parse_wire_number),
    (IdentifierKind.CONNECTION_POINT, parse_connection_point),
)

# With a hint the caller has the context (STG-05: a bubble, a stacked label, a device symbol).
_HINTED: Mapping[IdentifierKind, tuple[_Parse, ...]] = MappingProxyType(
    {IdentifierKind.ANSI_DEVICE: (parse_ansi_compound_in_context, parse_ansi_device_in_context)}
)
```

**لماذا هذه المواضع:**
- **المركّب قبل المرجع المتقاطع:** ليقرأ `50/51` جهاز ANSI حين يكون ANSI مفعّلاً ولم يثبّت ملف الإعداد صيغة مرجع تطابقه (`F2‑AC‑10`). وهو لا يقرأ شيئاً حين يكون ANSI معطّلاً، فلا يتغيّر `12/3`.
- **KKS بعد الأطراف وقبل IEC:** ليقرأ `=20XYZ10AB123` وسماً ببادئة العملية حين يطابق ما بعد البادئة صيغة كاملة (`F2‑AC‑6`). وما قبله (PLC، والمرجع، والجهد، والجسر، والطرف) لا يقرأ نصاً يبدأ برقم ثم حروف ثم أرقام بلا فاصل، فلا يتنازعان.
- **المفرد بعد كود الحرف:** ليبقى `1KM` و`2QF` كودَي حروف. ولو سبقه، لقرأهما ANSI بلاحقة مجهولة غير صالحة. وقبل رقم السلك، ولا يتنازعان لأن المفرد لا يقبل رقماً مجرداً.

**`IdentifierParser._first(forms, hint, text)`:**
- بلا `hint`: يمرّ على `_ORDER` كما اليوم. ثم يضيف القراءات البديلة (§3.9) بـ`replace(found, alternatives=...)` قبل `_flag_text`.
- مع `hint` في `_HINTED`: يمرّ على دوالّه وحدها. ومع غيره: كما اليوم (يتخطّى ما لا يطابق النوع).
- `_parse_non_ascii` يمرّ عبر `_first`، فيرث القراءات البديلة، ويبقى ناتجه غير صالح بـ`text.non_ascii` كما اليوم.

**`tally(tokens)`، عدّ الاصطلاحات:**
- العدّ الأساسي كما هو حرفاً: `found = self.parse(token)`، ثم `conventions[found.convention] += 1`، والأسر، والصيغ، و`identifiers`، و`tokens`. ويُستثنى من عدّ `conventions` اصطلاح إضافي لا تجعله القاعدة التالية دليلاً.
- **مسبار الاصطلاحين الإضافيين:** إن لم تكن KKS وANSI كلاهما في `self.rules.conventions`، يبني المحلّل مرة واحدة `IdentifierParser(self.rules.with_additive())`، ويحلّل به كل رمز. ويعدّ منه ما لم تفعّله قواعده هو. وبهذا يرى الاكتشاف KKS وANSI بالقواعد المعبّأة نفسها (`inspection.py:254`)، دون أن يتغيّر عدّ اصطلاحات الأجهزة ولا الصيغ.
- **ما يُعدّ دليلاً على اصطلاح إضافي (عُدّل في الإعادة 1، §11.1):** قراءة صالحة بلا `text.non_ascii`، ثم:
  - **لـANSI:** وظيفة واحدة بلا `alternatives`، أي لا رقم مجرد ولا مركّب. ويجب أن يكون للرقم فئة في `[ansi.functions]`، وأن تكون لاحقته فارغة مع مثيل، أو خارج `[ansi] non_evidence_suffixes`.
  - **لـKKS:** مستوى الوسم `>= [kks] evidence_min_level`، أي وسم معدّة فما أعمق. ولو كان ببادئة (`=20XYZ10AB123` يُعدّ KKS في المسبار، ويبقى IEC في العدّ الأساسي، §7 خ‑7).
  - والشرطان في `is_ansi_evidence` و`is_kks_evidence`، و`_is_evidence(found, rules)` يستدعيهما.
- **مرشح المسبار (عُدّل في الإعادة 1، S‑4):** `_may_be_additive` يرفض أولاً الرمز الأطول من `MAX_TEXT_CHARS`، قبل `canonical_form`، كما يفعل `parse`.
- `identifiers` يبقى عدد ما قرأه العدّ الأساسي، فلا يتغيّر عامل العيّنة لاصطلاح الأجهزة ولا مقام `is_mixed`.
- `ConventionTally` لا يتغيّر شكله: `conventions` تحمل `Convention.KKS` و`Convention.ANSI` حين تُرى.

### 3.5 وسم KKS (`shared/kks.py`)

```python
ISSUE_MALFORMED: Final = "kks.malformed"
ISSUE_LETTER_IN_DIGIT: Final = "kks.letter_in_digit_position"
ISSUE_DIGIT_IN_LETTER: Final = "kks.digit_in_letter_position"
ISSUE_EXCLUDED_LETTER: Final = "kks.excluded_letter"
_CHARSET: Final = re.compile(r"[=+]{0,2}[0-9A-Z]+", re.ASCII)
# The outer shape of a near-miss: digits, letters, digits, then letter/digit groups.
_OUTER: Final = re.compile(r"\d{1,3}[A-Z]{1,4}\d{1,3}(?:[A-Z]{1,3}\d{1,4}[A-Z]?){0,3}", re.ASCII)


def parse_kks_tag(
    canonical: str, forms: NormalizedForms, rules: IdentifierRules
) -> ParsedIdentifier | None: ...
```

**الخطوات:**
1. `None` إن لم يكن `Convention.KKS` في `rules.conventions`، أو لم يطابق النص `_CHARSET`.
2. تُنزع بادئة من `rules.kks.prefixes`، الأطول أولاً (`++` قبل `+`). ويجب أن يبدأ الباقي برقم، وإلا `None`.
3. يُطابَق الباقي بتعبير كل صيغة بالترتيب. والتعبير يُبنى من الأقنعة وحدها، وتخزّنه `lru_cache` على `(forms, parts)`: `N` → `[0-9]`، و`A` → `[A-Z]`، و`n` → `[0-9]?`، و`a` → `[A-Z]?`، ولكل جزء مجموعة مسمّاة. فلا نص من ملف الإعداد يدخل تعبيراً (NFR‑F2‑3)، والتعبير خطي بلا تكرار متداخل.
4. **صيغة تطابق:** وسم KKS صالح. وإن وقع حرف مستثنى (`excluded_letters`) في موضع حرف، فغير صالح بـ`kks.excluded_letter`. والأجزاء بنصّها ومستواها وتتابعاتها من القناع: `NNAAANN` → `("20", "XYZ", "10")`.
5. **لا صيغة تطابق، والنص ببادئة:** `None`، فيقرؤه IEC كما اليوم (`F2‑AC‑6`).
6. **لا صيغة تطابق، والنص بلا بادئة، ويطابق `_OUTER`، وطوله لا يقلّ عن أقصر صيغة (7 بالقواعد المعبّأة):** وسم KKS غير صالح، والسبب كما يلي. ولا يتغيّر الشكل القانوني (`F2‑AC‑5`):
   - إن طابق صيغةً بعد إبدال كل حرف ملتبس في موضع رقم برقمه، من `rules.confusables`: `kks.letter_in_digit_position`.
   - وإن طابق بعد إبدال كل رقم ملتبس في موضع حرف بحرفه: `kks.digit_in_letter_position`. وقد يجتمع السببان.
   - **(عُدّل في الإعادة 1، الملاحظة 4)** وإن لم تطابق أيٌّ من التجربتين، فتجربة ثالثة بالإبدالين معاً. فإن طابقت، فالسببان معاً بهذا الترتيب، لأن كل إبدال منهما لازم. والتفصيل في §11.4.
   - وإلا: `kks.malformed`.
   - والإبدال تجربة على نسخة لاختيار السبب فقط، ولا يُعاد النص المبدَّل. والتصحيح نفسه خطوة مستقلة في INC‑2 (`FR‑ID‑18`).
7. **وإلا:** `None`. فالنص الأقصر من أقصر صيغة، مثل `7XY123`، ليس معرّفاً (§3.2 في `requirements.md`).

**الناتج:** `kind=KKS_TAG`، و`convention=Convention.KKS`، و`kks=KksTag(...)`، و`forms=with_keys(forms, unprefixed, only=True)`. فمفاتيح البحث هي الشكل القانوني والشكل بلا بادئة، ولا شيء غيرهما. ولا تُحذف الأصفار البادئة، لأن مواضع KKS ثابتة العدد. فلا يلتقي `AB012` و`AB12` في مفتاح.

**أمثلة مخترعة بالقواعد المعبّأة:**

| النص | KKS مفعّل | KKS معطّل |
|---|---|---|
| `20XYZ10` | KKS، `system`، المستوى 1 | لا معرّف |
| `20XYZ10AB123` | KKS، `equipment`، المستوى 2 | لا معرّف |
| `20XYZ10AB123C` | KKS، `equipment` بحرفه الاختياري | لا معرّف |
| `20XYZ10AB123CD45` | KKS، `component`، المستوى 3 | لا معرّف |
| `20XYZ10AB1234567`، `20XYZ104567` | KKS، `cable` (`equipment_cable`، `system_cable`) | لا معرّف |
| `20 xyz10 ab123` | KKS، الشكل القانوني `20XYZ10AB123`، والأصل محفوظ | لا معرّف |
| `=20XYZ10AB123` | KKS ببادئة `process`، والبديل `reference_designation` | IEC بجانب واحد، كما اليوم |
| `20XY10AB123`، `20XYZ10AB12` | غير صالح، `kks.malformed` | لا معرّف |
| `2OXYZ1OAB12O` | غير صالح، `kks.letter_in_digit_position`، والشكل القانوني كما ورد | لا معرّف |
| `2OXYZ10A0123` (عُدّل في الإعادة 1) | غير صالح، `kks.letter_in_digit_position` ثم `kks.digit_in_letter_position` | لا معرّف |
| `7XY123` | لا معرّف | لا معرّف |

### 3.6 أطراف المورد الأول (`shared/terminals.py`، A‑5)

- **اليوم:** `_split` يعطي الصيغة التي يسبق فيها الرقمُ الرمزَ اصطلاح `cn_letter_codes` وحده (`terminals.py:99-100`). فيُقرأ `1X-25` طرفاً صينياً بالقواعد كلها، ولا يُقرأ بملف إعداد IEC (تشغيل مباشر للمحلّل على `master`).
- **بعد البند 1:** `convention = pick_convention(rules, *rules.terminal_digits_first.get(prefix, (Convention.CN_LETTER_CODES,)))`. والرمز من `_prefix_of(strip, rules)`. ولا يتغيّر غير ذلك: الصيغة، والنقطة، والفئة، والمفاتيح.
- **النتائج بالقواعد المعبّأة:**
  - دون تضييق: `1X-25` و`1XT-5` كودا حروف صينية كما اليوم، لأن `cn_letter_codes` أول القائمة.
  - IEC وحده: `1X-25` طرف IEC صالح، و`1XT-5` بلا معرّف كما اليوم.
  - GB/T وحده: `1X-25` طرف GB/T.
  - NFPA وحده: `1X-25` بلا معرّف.
- **ولا يمسّ** الصيغة الأولى (`X1-14`)، ولا السلسلة (`-X1:14`)، ولا `TB`.

### 3.7 رقم جهاز ANSI (`shared/ansi.py`)

```python
ISSUE_UNKNOWN_SUFFIX: Final = "ansi.unknown_suffix"
_FUNCTION: Final = r"[1-9][0-9]?[A-Z]{0,3}"
_SINGLE: Final = re.compile(r"(?P<num>[1-9][0-9]?)(?P<suffix>[A-Z]{1,3})?(?:-(?P<inst>[1-9][0-9]?))?", re.ASCII)


def is_bare_ansi_number(canonical: str) -> bool: ...  # "1".."99", no leading zero
def read_compound(canonical: str, rules: IdentifierRules) -> AnsiDevice | None: ...
def parse_ansi_compound(canonical: str, forms: NormalizedForms, rules: IdentifierRules) -> ParsedIdentifier | None: ...
def parse_ansi_device(canonical: str, forms: NormalizedForms, rules: IdentifierRules) -> ParsedIdentifier | None: ...
def parse_ansi_compound_in_context(canonical: str, forms: NormalizedForms, rules: IdentifierRules) -> ParsedIdentifier | None: ...
def parse_ansi_device_in_context(canonical: str, forms: NormalizedForms, rules: IdentifierRules) -> ParsedIdentifier | None: ...
```

**القواعد** (كلها `None` إن لم يكن `Convention.ANSI` في `rules.conventions`):
- **الرقم:** 1..99 بلا صفر بادئ. فـ`00` و`05` و`100T` ليست أرقام ANSI (`F2‑AC‑11`).
- **اللاحقة:** 1..3 أحرف، ويجب أن تكون في `rules.ansi.suffixes` كاملة، وإلا جهاز غير صالح بـ`ansi.unknown_suffix` (`87QZ`). وتصير صالحة إن أضافها ملف الإعداد (`extra_ansi_suffixes`).
- **المثيل:** `-` ثم 1..99 (`27-1`)، للمفرد وحده.
- **المفرد بلا سياق (`parse_ansi_device`):** يطابق `_SINGLE`، ويجب أن يحمل لاحقة أو مثيلاً. فالرقم المجرد ليس جهازاً بلا سياق (`FR‑ID‑16`).
- **المركّب بلا سياق (`parse_ansi_compound`):** 2..`max_compound` وظيفة بـ`/` (`read_compound`)، لكلٍّ رقم ولاحقة اختيارية بلا مثيل. ويعيد `None` إن كان `rules.cross_reference_pinned` وطابق النصَّ مرجعٌ متقاطع في صيغه المختارة، فيقرؤه المرجع بعده (`F2‑AC‑10`).
- **بالسياق (`*_in_context`):** يقبل الرقم المجرد (`52`)، ويقرأ المركّب ولو ثبّت الملف صيغة مرجع. فهذا ما يمرّره STG‑05 في INC‑2 لفقاعة الرمز والتسمية المكدّسة.
- **الناتج:** `kind=ANSI_DEVICE`، و`convention=Convention.ANSI`، و`ansi=AnsiDevice(...)`. و`device_class` فئة المفرد أو `None`. والشكل القانوني بلا مسافة وبأحرف كبيرة (`87 t` → `87T`). والمفاتيح الشكل القانوني، ومعه كل وظيفة في المركّب (`50/51` → `50`، `51`)، بـ`only=True`.

### 3.8 ملف الإعداد (`drawings/domain/profile.py`)

```python
class PlantDesignation(StrEnum):  # D-36
    NONE = "none"
    KKS = "kks"


class DeviceFunctionNumbers(StrEnum):  # D-36
    NONE = "none"
    ANSI = "ansi"


MAX_ANSI_SUFFIXES: Final = 64  # the bound `shared` enforces when it narrows
_ANSI_SUFFIX: Final = re.compile(r"[A-Z]{1,3}", re.ASCII)

_TOP_KEYS: Final = (
    ...,  # the fourteen existing keys, unchanged and in the same order
    "plant_designation", "device_function_numbers", "ansi_suffixes",
)

# Omitted from `to_body` while they hold these values (§4: old bodies, fingerprints, rollback).
_F2_DEFAULTS: Final[Mapping[str, object]] = MappingProxyType(
    {"plant_designation": "none", "device_function_numbers": "none", "ansi_suffixes": []}
)


@dataclass(frozen=True, slots=True)
class DrawingProfile:
    ...  # the thirteen existing fields, unchanged and in the same order
    plant_designation: PlantDesignation = PlantDesignation.NONE
    device_function_numbers: DeviceFunctionNumbers = DeviceFunctionNumbers.NONE
    ansi_suffixes: tuple[str, ...] = ()  # canonical, sorted, unique; <= 64, each 1..3 of A-Z

    def with_detected(
        self,
        *,
        symbol_standard: SymbolStandard,
        identifier_convention: Convention,
        cross_reference_format: CrossReferenceFormat,
        plc_families: frozenset[PlcFamily],
        languages: frozenset[str],
        plant_designation: PlantDesignation,
        device_function_numbers: DeviceFunctionNumbers,
    ) -> DrawingProfile: ...
```

**`from_body`:**
- `identifier_convention`: قارئ جديد يقبل `DEVICE_CONVENTIONS` وحدها. فـ`"kks"` و`"ansi"` هنا 422، ورسالته تسرد القيم الأربع وحدها، كما اليوم.
- `plant_designation` و`device_function_numbers`: بـ`_enum` القائم، والغائب يأخذ `none`.
- `ansi_suffixes`: قائمة نصوص، 64 على الأكثر. وكل عنصر بلا حرف من الفئات المرفوضة (`_FORBIDDEN_TEXT_CATEGORIES`)، ثم `canonical_form`، ثم يطابق `_ANSI_SUFFIX`. وإلا `InvalidProfile(f"ansi_suffixes[{i}]", "must be 1..3 letters A-Z")`، ولا تُعاد القيمة. ويُحفظ مرتّباً بلا تكرار. ويُقبل الحقل ولو كان ANSI `none`: لا يُقرأ إلا حين يُفعَّل، ولا يُحذف حين يُعطَّل.
- `schema` يبقى 1: التغيير إضافي، والغائب يأخذ افتراضه (`profile.py:7-9`).

**`to_body`:** كل الحقول القائمة كما هي، ثم الحقول الثلاثة **حين تخالف افتراضاتها فقط**. فجسد ملف الإعداد الافتراضي وبصمته كما كانا بايتاً ببايت (§4). وتُحدَّث الوثيقة النصية للدالة.

**`rule_selection`:**

```python
def rule_selection(self) -> RuleSelection:
    conventions = {self.identifier_convention}
    if self.plant_designation is PlantDesignation.KKS:
        conventions.add(Convention.KKS)
    if self.device_function_numbers is DeviceFunctionNumbers.ANSI:
        conventions.add(Convention.ANSI)
    return RuleSelection(
        conventions=frozenset(conventions),
        ...,  # the five existing fields, unchanged
        extra_ansi_suffixes=self.ansi_suffixes,
    )
```

فيُحلَّل النص باصطلاح الأجهزة ومعه ما فُعّل من KKS وANSI معاً (`F2‑AC‑13`).

**`application/projects.py:83` (عُدّل في الإعادة 1، الملاحظة 2، §11.2):**
- **الفحص الأول:** تحت `DEVICE_CONVENTIONS | selection.conventions`، لا تحت `frozenset(Convention)`. فتبقى اصطلاحات الأجهزة الأربعة كلها، كما كان قبل F2، ومعها KKS أو ANSI حين يفعّلهما الملف وحده.
- **الفحص الثاني** `packaged.narrowed(selection)`: كما هو.
- **وفي `IdentifierRules._other_kind`** استثناء واحد: قراءة ANSI لا تجعلها `is_ansi_evidence` دليلاً لا تُعدّ جهازاً تخفيه التسمية. فتُقبل `24V` ولو فُعّل ANSI، وتُرفض `87T`.
- **وكان النص قبل الإعادة:** «تحت كل الاصطلاحات» `frozenset(Convention)`. فرفض `24V` و`12VDC` بـ422 في كل مشروع، ولو لم يفعّل ANSI.

### 3.9 قواعد الالتباس

**ما يقع في `shared` الآن** (من `requirements.md` §3.2):

| النص (مثال مخترع) | الشرط | الناتج | البديل في `alternatives` |
|---|---|---|---|
| `52`، `86` بلا سياق | ANSI مفعّل | رقم سلك (أو نقطة اتصال إن ضاقت أنماط الأسلاك)، كما اليوم | `ansi_device`، و`needs_context` صادق |
| `52` بسياق (`hint=ANSI_DEVICE`) | ANSI مفعّل | جهاز ANSI 52 بفئته، و`needs_context` صادق | — |
| `87T`، `50BF`، `51N`، `27-1` | ANSI مفعّل | جهاز ANSI | — |
| `50/51` | ANSI مفعّل، وصيغة المرجع `auto` أو مثبّتة على صيغة لا تطابقه | وظيفتا ANSI مركّبتان | `cross_reference` إن طابقه مرجع في القواعد |
| `50/51` | ANSI مفعّل، وصيغة المرجع مثبّتة على `page_slash_column` | مرجع متقاطع | `ansi_device` |
| `50/51`، `12/3` | ANSI معطّل | مرجع متقاطع، كما اليوم | — |
| `=20XYZ10AB123` | KKS مفعّل، وما بعد البادئة صيغة كاملة | وسم KKS ببادئة | `reference_designation` إن كان IEC أو GB/T مفعّلاً |
| `=20XYZ10AB12` | KKS مفعّل، والبنية لا تكتمل | IEC بجانب واحد، كما اليوم | — |
| `20XYZ10AB12` | KKS مفعّل | KKS غير صالح، `kks.malformed` | — |
| `2OXYZ1OAB12O` | KKS مفعّل | KKS غير صالح، `kks.letter_in_digit_position`، والأصل كما ورد | — |
| `2OXYZ10A0123` (عُدّل في الإعادة 1) | KKS مفعّل | KKS غير صالح بالسببين: `kks.letter_in_digit_position` ثم `kks.digit_in_letter_position` (§11.4) | — |
| `16A`، `32A`، `4P`، `2P`، `1N` (عُدّل في الإعادة 1) | ANSI مفعّل | أجهزة ANSI كما في §3.7، فلا يتغيّر التحليل | — |
| الرموز نفسها (عُدّل في الإعادة 1) | ANSI معطّل، وفي عدّ `tally` | لا معرّف، ولا دليل على ANSI (§11.1) | — |
| `7XY123` | أيّ | لا معرّف | — |
| `1X-25` | IEC 81346 وحده | طرف IEC (A‑5) | — |

- **البدائل لهذه الأزواج وحدها، وبلا `hint` فقط.** والالتباسات القديمة التي يحسمها الترتيب (`12.3` مرجع أو سلك، و`13` سلك أو نقطة اتصال) لا تحمل بدائل، فلا يتغيّر شكل أي حالة قائمة.
- **حساب البدائل** في `IdentifierParser._alternatives(found, forms)`. وهو يستدعي `is_bare_ansi_number` و`read_compound` و`parse_cross_reference` و`parse_reference_designation` على النص نفسه، ولا يقرأ إلا ما تفعّله القواعد.
- **(عُدّل في الإعادة 1) التحليل والدليل أمران منفصلان:**
  - قاعدة الدليل في §11.1 لا تغيّر ما يقرؤه المحلّل. فـ`32A` وANSI مفعّل يبقى جهاز ANSI صالحاً، وحالات M‑04 كما هي.
  - إنما تقرّر القاعدة ما يعدّه `tally` دليلاً على الاصطلاح.
  - وتقرّر ما يُعدّ جهازاً تخفيه تسمية جهد (§11.2).
  - وضجيج القراءة نفسها حين يُفعَّل ANSI (`24V`، `1NC`، `3PH`) يُقاس في F3 (§7 خ‑12).

**ما ينتظر سياق STG‑05 في INC‑2:**
- الرقم المجرد جهاز ANSI بدليل من الصفحة: داخل فقاعة رمز، أو في تسمية مكدّسة، أو ملاصقاً لرمز جهاز. فيمرّر STG‑05 `hint=ANSI_DEVICE`.
- المكدّس داخل فقاعة ANSI دائماً، ولو ثبّت الملف صيغة مرجع. فيمرّر النص المجموع `50/51` بالـ`hint` نفسه.
- المعرّف الكامل لجهاز ANSI: من وسم KKS المحيط، أو الجزء الثاني من التسمية المكدّسة (`FR‑ID‑17`)، أو البنية الموروثة (`FR‑ID‑02`).
- الحسم بين قراءتين بقيتا في `alternatives` بدليل الهندسة، أو وسم الصفحة «تحتاج مراجعة» بسبب مذكور.
- التصحيح الموضعي بثقة كل حرف (`FR‑ID‑18`)، على `rules.confusables` نفسها.

### 3.10 الاكتشاف و«الخلط» (`drawings/domain/profile_detection.py`)

```python
def ranked_device_conventions(conventions: Mapping[Convention, int]) -> list[tuple[Convention, int]]:
    """``ranked_conventions`` restricted to DEVICE_CONVENTIONS (KKS and ANSI never compete)."""


def is_mixed(conventions: Mapping[Convention, int], identifiers: int) -> bool:
    """FR-ING-12 after D-36: the second DEVICE convention holds a quarter of the identifiers,
    and 20 at least. KKS and ANSI beside one device convention are not mixed."""
```

- `_convention` يرتّب `ranked_device_conventions`. فلا يُقترح `kks` ولا `ansi` في `identifier_convention` أبداً.
- **الحقلان الجديدان (عُدّل في الإعادة 1، §11.1).** لكل منهما `n = tally.conventions.get(c, 0)`، وهو عدد ما تعدّه قاعدة الدليل وحده:
  - `n >= MIN_IDENTIFIERS` (20): يُقترح المفعَّل (`kks` أو `ansi`)، وثقته `_sample_factor(n)`، أي من 0.2 إلى 1.0.
  - `n < MIN_IDENTIFIERS`، ومنه الصفر: يبقى ما في ملف الإعداد الأساسي، بثقة 1.0، كبقية الحقول بلا بيّنة (`profile_detection.py:13-15`). فلا يُقترح KKS ولا ANSI من عيّنة صغيرة، ولا يُقبل آلياً (`F2‑AC‑15` بقراءته في §11.1).
  - **وكان قبل الإعادة:** `n > 0` يكفي ليُقترح الحقل بثقة صفر تحت 20. فأسقط رمزٌ واحد ثقة الاقتراح كله إلى صفر (الملاحظة 1).
- `field_confidence` يحمل `"plant_designation"` و`"device_function_numbers"`. و`confidence` أدناها كما اليوم.
  - **فوثيقة بلا KKS ولا ANSI تأخذ اقتراحها على `master` نفسه، ولو كان فيها ما يشبههما من رموز التيار والأقطاب:** الثقة نفسها، و`none` في الحقلين، ولا مفتاح `systems`.
  - والفرق الوحيد عن `master` مفتاحا `field_confidence` الجديدان بقيمة 1.0 (§3.12).
- `basis["systems"]`: مثل `("kks:25", "ansi:22")`، **للمقترَح منهما وحده (عُدّل في الإعادة 1)**. والعدد تحت الحدّ يُرى في `conventions` بسجلّ التوصيف، لا في الاقتراح. ويبقى `basis["tokens"]` لاصطلاحات الأجهزة.
- **(عُدّل في الإعادة 1) لا يقرأ الاكتشاف تسميات الجهد.** ملف إعداد أساسي فيه تسمية `87T`، والاكتشاف يقترح ANSI: يبقى الاقتراح كما هو، والتسمية فيه. وما يحدث بعدها في §11.2.
- **`_SYMBOL_OF` لا يتغيّر:** أربعة مداخل لاصطلاحات الأجهزة، ولا يُستنتج معيار رموز من KKS ولا ANSI. ويُضاف اختبار يثبّت أن مفاتيحه `DEVICE_CONVENTIONS`.

### 3.11 تقرير التوصيف (`drawings/domain/characterization.py`)

- **سجلّ الوثيقة:** `conventions=dict(ranked_conventions(tally.conventions))` كما اليوم، فيحمل عدد KKS وANSI تلقائياً. و`symbol_standard_guess` يُشترط فيه `ranked_device_conventions(...)` لا الكل. فوثيقة فيها KKS وحده لا تدّعي معيار رموز، على سابقة `F0‑AC‑6`.
- **المجمّع:** `conventions` لاصطلاحات الأجهزة كما اليوم (الوثائق باصطلاحها المقترح، الأول بينها). ولكل من `kks` و`ansi`: عدد الوثائق التي فيها 20 منه على الأقل (`MIN_IDENTIFIERS`). ويقول وصف `openapi.yaml` ذلك (§3.12).
- **الإصدارات:** السجل الجديد يحمل `shared` = `1.3.0` و`rules` = `1.1.0`، والمجمّع يسرد كل إصدار رآه (`characterization.py:182-186`). فمجموعة نصفها قبل البند 1 تقول ذلك (§4).
- الشكل المخزَّن والـDTO لا يتغيّران.

### 3.12 العقد: `openapi.yaml` و`06-domain-models.md` واختبارهما

**`docs/design/openapi.yaml`، الأوصاف وحدها:**
- `DrawingProfileIn.body` (`openapi.yaml:1735`): بعد `identifier_convention (...)` يُضاف: «plant_designation (none, kks; default none) and device_function_numbers (none, ansi; default none), chosen independently of identifier_convention: KKS tags or ANSI device numbers beside one device convention are not mixed (SRS D-36); ansi_suffixes (extra ANSI suffix letters, at most 64, each 1..3 letters A-Z; default empty). A returned body omits these three while they hold their defaults.»
- `ProfileProposalOut.field_confidence` (`openapi.yaml:1750`): يُضاف `plant_designation` و`device_function_numbers` إلى القائمة. و`basis`: يُضاف `systems`، **وفي الإعادة 1:** «systems (kks / ansi, only when proposed: 20 evidence identifiers at least)».
- `CharacterizationOut.conventions` (`openapi.yaml:1856`): وصف جديد. **(عُدّل في الإعادة 1)** نصّه: «Identifiers per convention; kks and ansi count only the identifiers that are evidence of them (SRS D-36): a KKS tag at equipment level or deeper, and one ANSI function that has a class, with a suffix that is not a rating letter or an instance.»
- **(عُدّل في الإعادة 1، الملاحظة 10)** `CharacterizationOut.symbol_standard_guess` (`openapi.yaml:1857`): «Null when no device convention (iec_81346, gbt_5094, cn_letter_codes, nfpa_jic) was seen in the document's text; KKS and ANSI imply no symbol standard.»
- `CharacterizationReportOut.conventions` (`openapi.yaml:1879`): «Documents by proposed device convention; for kks and ansi, the documents with at least 20 such identifiers.»
- ولا تتغيّر خاصية ولا `required`. فاختبار العقد القائم `test_every_electrical_schema_has_the_published_properties_and_required_set` ينجح كما هو.

**اختبار يربط الوصف بالحقول (جديد):** `test_the_profile_body_description_names_every_profile_field` في `tests/unit/test_api_electrical_router.py`. يقرأ `_TOP_KEYS` من `profile.py`، ويتحقق أن وصف `DrawingProfileIn.body` في `openapi.yaml` يذكر كل اسم، ويذكر قيم `PlantDesignation` و`DeviceFunctionNumbers` كلها. فحقل يُضاف إلى الشيفرة دون الوصف يُفشل CI. وهذا ما يجعل `F2‑AC‑18` اختباراً لا مراجعة.

**`docs/design/06-domain-models.md:314`:** يُضاف إلى سطر `DrawingProfile` «plant_designation (none|kks)، device_function_numbers (none|ansi)، ansi_suffixes ≤ 64».

### 3.13 import-linter

- **لا تغيير في العقود ولا `ignore_imports`.** `kks.py` و`ansi.py` و`rules/systems.py` و`rules/errors.py` في `app.modules.electrical.shared`، وتستورد المكتبة القياسية و`shared` وحده. فيحرسها `domain-purity` كما هو (`.importlinter:35-56`).
- `rules/systems.py` يستورد `kinds` و`rules/errors` و`normalization` وحدها، ولا يستورد `parser` ولا `rules/__init__`، فلا دورة. ودوال التحليل تستورد أنواع القواعد تحت `TYPE_CHECKING` وحده، كما تفعل `terminals.py:32-33` اليوم.
- `drawings/domain` يستورد `shared.kinds` و`shared.rules` كما اليوم، ومعهما `shared.normalization.canonical_form` في `profile.py`، وهو مسموح في `electrical-contexts` (`shared` الطبقة السفلى).
- `lint-imports` يبقى على عقوده الثمانية في `CLAUDE.md`، و`tests/architecture/` كما هو.

## 4. البيانات

**لا تغيير في قاعدة البيانات، ولا ترحيل Alembic، ولا RLS جديد، ولا مستودع جديد.** فلا اختبار RLS جديداً، ولا عمل لـ`data-engineer`.

- **ملف الإعداد جسد JSONB** (`drawings_profile_versions.body`، `sql_repository.py:225`)، ويُقرأ بـ`DrawingProfile.from_body` (`sql_repository.py:463`):
  - **الإصدارات القديمة** بلا الحقول الثلاثة: تُقرأ بافتراضاتها (`none`، `none`، `()`)، لأن الغائب يأخذ افتراضه. ولا تُعاد كتابتها.
  - **الكتابة:** `to_body` يحذف الحقول الثلاثة ما دامت افتراضية (§3.8). فملف الإعداد الافتراضي، وكل ملف لا يستعملها، يُحفظ بالبايتات نفسها، وبصمته (`fingerprint`، `profile.py:213-216`) كما كانت.
- **العدّ المخزَّن لكل صفحة** (`stored["tally"]`، `sql_repository.py:959-978`) والتقرير (`sql_repository.py:1352`): مفاتيح `kks` و`ansi` الجديدة تُكتب وتُقرأ بـ`_by_enum` دون تعديل. والشيفرة القديمة تتجاهلها، لأن `_by_enum` لا يقرأ إلا أعضاء التعداد الذي تعرفه.
- **`detection` في الاقتراح:** مفتاحان جديدان في `field_confidence` ومفتاح في `basis`. يُكتب كما هو ويُقرأ كما هو.
- **البصمات (NFR‑REP‑01):** كل سجلّ توصيف بعد البند 1 يحمل `shared_version` = `1.3.0` و`rules_version` = `1.1.0`. فتتغيّر «بصمة الإصدار» لأي وثيقة بملف الإعداد نفسه، وإن بقيت بصمة ملف الإعداد نفسه (`F2‑AC‑18`).
- **إعادة المعالجة:** لا حقائق معرّفات مخزّنة بعد (STG‑05 في INC‑2)، فلا شيء يبطل. لكن عدّ الصفحات المخزَّن قبل البند 1 لا يحمل KKS ولا ANSI، والاستئناف والتأكيد يقرآن المخزَّن (`inspection.py:34-37`). فوثائق المورد الأول التي استُوعبت قبله تحتاج إعادة معالجة كاملة (`POST /api/v1/electrical/documents/{id}/reprocessing`) ليقترح الاكتشاف KKS وANSI. ولا إعادة معالجة آلية: البيانات على المكدّس المحلي وحده، والموجة 8 مؤجّلة.
- **التوافق (عُدّل في الإعادة 1، الملاحظة 3 وS‑2):** غير كاسر للأمام. لكن الرجوع إلى الصورة القديمة، والنشر المتدرّج إليها، **ليسا بلا إجراء**:
  - `from_body` على `master` يرفض كل مفتاح لا يعرفه (`profile.py:137-141` هناك).
  - والاكتشاف يكتب الحقلين في إصدار `DETECTED` دون فعل بشري، حين تبلغ وثيقةٌ الحدّ في §11.1.
  - فالإصدارات المعنية تُعرَف **بمفاتيح الجسد نفسها**، لا بـ«ما فعّله المستخدم».
  - والإجراء في §4.1.
  - **وكان النص قبل الإعادة:** «يُلحق لكل مشروع أو وثيقة فعّل KKS أو ANSI إصدار بلا الحقول». وهذا لا يرى ما كتبه الاكتشاف.

### 4.1 إجراء النشر المتدرّج والرجوع (عُدّل في الإعادة 1، يُنسخ إلى جسد الـPR كما هو)

**الاختيار: إجراء مكتوب، لا مفتاح يطفئ الاكتشاف.** وسببه:
- قاعدة §11.1 تقصر الكتابة الآلية على وثائق فيها 20 دليلاً على الأقل، لكن هذه هي وثائق المورد الأول نفسها، فلا تكفي وحدها.
- ومفتاح تشغيل جديد إعداد نشر جديد، ولا حاجة إليه، لأن ترتيب الاستبدال يغلق النافذة:
  - الاكتشاف يجري في `worker-electrical` وحده: `InspectDocument` في `inspection.py:734`، ويُبنى في `workers/electrical_bootstrap.py:204`.
  - والذي يقرأ جسد ملف الإعداد `app` و`worker-electrical` وحدهما، فهما وحدهما يبنيان `SqlProfileRepository`: `app` في `framework/di/composition_root.py:1268`، والعامل في `workers/electrical_bootstrap.py`.
  - ويتحقق المطوّر من ذلك عند كتابة الـPR بـ`grep -rn "SqlProfileRepository" src/app`. فإن ظهرت خدمة ثالثة تقرأ الجسد، استُبدلت قبل `worker-electrical` أيضاً.
  - والقراءة كلها من `latest` (`sql_repository.py:476-494`)، فالإصدار الأحدث لكل مالك هو وحده ما يُقرأ.

**أ. النشر إلى الصورة الجديدة:**
1. يُستبدل `app` أولاً بكل نسخه: `deploy/rolling-deploy.sh --service app`. ثم `worker-electrical`: `deploy/rolling-deploy.sh --service worker-electrical`.
2. فلا يكتب الاكتشاف جسداً جديداً إلا بعد أن تصير كل نسخ `app` جديدة. فلا يبقى قارئ قديم.
3. **من أول نسخة `app` جديدة إلى آخر نسخة `worker-electrical`:** لا يرسل أحد `plant_designation` ولا `device_function_numbers` ولا `ansi_suffixes` في `PUT …/profile` ولا في `profile-confirmation`. فالمكدّس محلي، والنشر قرار إنسان يعلنه.
4. لا حاجة لإطفاء الاكتشاف بعدها.

**ب. قبل الرجوع إلى الصورة القديمة**، والصورة الجديدة ما زالت تعمل:
1. **التجميد:** يُعلَن ألّا يُعدَّل ملف إعداد ولا يؤكَّد اقتراح حتى ينتهي الرجوع.
2. **حصر المعنيّين** (للقراءة فقط). يُشغَّل بدور يتجاوز RLS وله `SELECT` على الجدول، كالمستخدم الأعلى أو `backup_operator`، لأن الجداول تحت `FORCE ROW LEVEL SECURITY`. ولا يُطبع الجسد:

   ```sql
   BEGIN READ ONLY;
   SELECT workspace_id, project_id, document_id, version, source
   FROM (
     SELECT DISTINCT ON (workspace_id, project_id, document_id)
            workspace_id, project_id, document_id, version, source, body
     FROM electrical.drawings_profile_versions
     ORDER BY workspace_id, project_id, document_id, version DESC
   ) AS latest
   WHERE latest.body ?| ARRAY['plant_designation', 'device_function_numbers', 'ansi_suffixes']
   ORDER BY workspace_id, project_id, document_id;
   ROLLBACK;
   ```

3. **المعالجة لكل صف**، عبر الواجهة على الصورة الجديدة، بمستخدم له `electrical:manage` في تلك المساحة. والجسد في كل حالة هو الجسد الحالي بعد حذف المفاتيح الثلاثة منه:

   | الصف | ما يُرسَل |
   |---|---|
   | `document_id` فارغ (ملف المشروع) | `GET /api/v1/electrical/projects/{project_id}/profile`، ثم `PUT` على المسار نفسه بـ`{"body": …}` |
   | وثيقة، ولها مهمة `awaiting_confirmation` | الجسد من `proposal` في `GET /api/v1/electrical/documents/{document_id}/profile`، ثم `POST /api/v1/electrical/documents/{document_id}/profile-confirmation` بـ`{"body": …}`. وهذا تأكيد يجريه المشغّل نيابةً عن صاحب الوثيقة، فيُسجَّل في سجل الرجوع |
   | وثيقة بلا مهمة تنتظر التأكيد | الجسد من `effective` في الجواب نفسه، ثم `PUT /api/v1/electrical/documents/{document_id}/profile` بـ`{"body": …}`. فتصير الوثيقة `confirmed` بإصدار `user` |

4. **التحقق:** يُعاد استعلام الخطوة 2، ويجب أن يعطي 0 صفوف. ثم يُنتظر حتى لا تبقى مهمة `queued` ولا `running`:

   ```sql
   SELECT count(*) FROM electrical.drawings_jobs WHERE status IN ('queued', 'running');
   ```

5. **الرجوع:** `worker-electrical` أولاً، فيقف الاكتشاف الجديد. ثم يُعاد استعلام الخطوة 2، فإن ظهر صف جديد عولج بالخطوة 3. ثم `app`.
6. **ما لا يحتاج شيئاً:**
   - الإصدارات الأقدم التي فيها المفاتيح: لا يقرؤها شيء.
   - مفاتيح `kks` و`ansi` في العدّ المخزَّن وفي سجلّ التوصيف: تتجاهلها الشيفرة القديمة (`_by_enum`).
   - مفاتيح `detection` الجديدة: تُقرأ كما هي.
7. **السجل:** تُحفظ قائمة المعرّفات التي عولجت، بلا جسد، في سجل الرجوع. وبعد العودة إلى الصورة الجديدة يعيد أصحابها تفعيل الحقول.

**للمستقبل (مؤجَّل إلى LLD الزيادة INC‑2، §8):** قارئ الجسد المخزَّن يتسامح مع المفاتيح التي لا يعرفها، ويبقى `from_body` صارماً على مدخل العميل. فـINC‑2 يضيف حقولاً أخرى إلى ملف الإعداد (الأسطورة، ومفتاح التصحيح)، فتتكرر المشكلة نفسها. لكن هذا لا ينفع رجوعاً إلى `master`، لأن قارئه القديم صارم.

## 5. خطة الاختبار لكل معيار قبول

كل اختبار هنا وحدة أو معماري أو تقييم، ويعمل في `pytest tests/unit tests/architecture tests/eval` بلا خدمات حية. والتكامل لا يتغيّر، ويشغّله CI في حاضنته (`CLAUDE.md`).

### 5.1 الربط بالمعايير

| المعيار | المستوى | الملف والحالة |
|---|---|---|
| `F2‑AC‑1` | وحدة | `test_electrical_shared_kks.py::test_f2_ac_1_an_equipment_tag_has_its_levels_and_parts`: `20XYZ10AB123` بقواعد KKS مفعّلة: النوع والاصطلاح والصنف والصلاحية، والجزآن بنصّهما ومستواهما وتتابعاتهما، والشكل القانوني |
| `F2‑AC‑2` | وحدة | `…::test_f2_ac_2_each_packaged_form_has_its_class` (مُعلَّم بالأشكال الخمسة) |
| `F2‑AC‑3` | وحدة | `…::test_f2_ac_3_spaces_case_and_full_width_meet_in_one_canonical_form`، والأصل محفوظ |
| `F2‑AC‑4` | وحدة | `…::test_f2_ac_4_a_near_miss_is_flagged_malformed_never_dropped` (`20XY10AB123`، `20XYZ10AB12`) |
| `F2‑AC‑5` | وحدة | `…::test_f2_ac_5_a_letter_o_in_a_digit_position_is_named_not_changed`: السبب، والشكل القانوني كما ورد |
| `F2‑AC‑6` | وحدة | `…::test_f2_ac_6_a_prefixed_tag_is_kks_only_when_enabled_and_complete`: ومعه البديل `reference_designation` |
| `F2‑AC‑7` | وحدة | `test_electrical_shared_parser.py::test_f2_ac_7_kks_on_or_off_changes_no_existing_case` (كل حالة بلا `select` بقواعد دون تضييق، ثم بقواعد فيها KKS مفعّل، والناتج متساوٍ؛ وأمثلة KKS بلا معرّف حين يُعطَّل). و`…::test_f2_ac_7_ansi_on_changes_only_the_listed_ambiguities`: بقواعد فيها ANSI مفعّل، لا يتغيّر إلا `12/3` (مركّب ببديل) والأرقام المجردة (بديل وحده). فيحرس ترتيب `_ORDER` (يبقى `1KM` و`2QF` كودَي حروف) |
| `F2‑AC‑8` | وحدة | `test_electrical_shared_ansi.py::test_f2_ac_8_numbers_suffixes_instances_and_classes` |
| `F2‑AC‑9` | وحدة | `…::test_f2_ac_9_a_bare_number_is_a_wire_unless_the_caller_has_context`: بلا `hint` رقم سلك ببديل `ansi_device`؛ ومعه جهاز 52؛ و`needs_context` صادق في الحالتين |
| `F2‑AC‑10` | وحدة | `…::test_f2_ac_10_a_compound_against_a_cross_reference` (مُعلَّم بالحالات الثلاث) |
| `F2‑AC‑11` | وحدة | `…::test_f2_ac_11_unknown_suffix_profile_suffix_and_out_of_range`: `87QZ` غير صالح، ثم صالح بـ`extra_ansi_suffixes=("QZ",)`، و`00` و`05` و`100T` بلا معرّف |
| `F2‑AC‑12` | وحدة | `…::test_f2_ac_12_every_ansi_class_is_a_class_name` (`CLASS_NAME` على كل قيمة)، و`test_electrical_shared_rules.py::test_the_kks_and_ansi_tables_refuse_unknown_keys` (لا يدخل الملفَ كتالوج ولا وصف) |
| `F2‑AC‑13` | وحدة | `test_electrical_f2_profile.py::test_f2_ac_13_one_profile_reads_iec_kks_and_ansi_together`: `DrawingProfile` بالحقلين، ثم `packaged.narrowed(profile.rule_selection())` |
| `F2‑AC‑14` | وحدة | `…::test_f2_ac_14_kks_and_ansi_are_proposed_and_not_mixed` (كيس اصطناعي: 25 وسم KKS، و22 رقم ANSI بلاحقة، و30 طرف IEC، عبر `IdentifierParser.tally` بالقواعد المعبّأة كما يفعل `inspection.py:626`)، و`…::test_f2_ac_14_iec_and_chinese_stay_mixed` (السابقة كما هي) |
| `F2‑AC‑15` | وحدة | `…::test_f2_ac_15_under_twenty_tags_kks_is_not_proposed` (عُدّل في الإعادة 1: 19 وسماً، فالحقل على الأساس بثقة 1.0 ولا يُقترح KKS؛ و20 وسماً تقترحه) |
| `F2‑AC‑16` | وحدة على الموجّه | `test_api_electrical_router.py::test_f2_ac_16_the_new_fields_are_saved_validated_and_defaulted`: PUT بقيم صحيحة إصدار جديد؛ وقيمة مجهولة لكل حقل، و`identifier_convention: "kks"`، و`ansi_suffixes: [".*"]`، و`["("]`، و`["ABCD"]`، و65 لاحقة: 422 `electrical.invalid_profile` يبدأ `detail` فيه باسم الحقل، ولا يرد فيه ما أُرسل؛ وجسد قديم بلا الحقول: GET يعيد جسداً بلا المفاتيح الثلاثة، و`from_body` عليه يعطي `none` و`none` و`()`. ومعه في `test_electrical_f2_profile.py` اختبار نطاق: جسد افتراضي وبصمته بايتاً ببايت كما على `master` (ثابت مثبّت في الاختبار) |
| `F2‑AC‑17` | وحدة | `test_api_electrical_router.py::test_a_member_reads_and_previews_but_neither_registers_nor_changes_a_profile` و`…::test_a_viewer_reads_and_nothing_more`: يُضاف إلى كلٍّ PUT بجسد فيه `plant_designation: "kks"`، والجواب 403 ولا إصدار جديد. وصفوف `EXPECTED` في `test_api_rbac.py` لا تتغيّر، لأن المسارات لم تتغيّر |
| `F2‑AC‑18` | وحدة وعقد | اختبار العقد القائم كما هو؛ و`test_the_profile_body_description_names_every_profile_field` (§3.12)؛ و`test_electrical_shared_rules.py::test_the_rules_file_is_packaged_and_versioned` بـ`1.1.0` و`1.3.0`؛ و`test_electrical_f2_profile.py::test_f2_ac_18_the_release_fingerprint_moves_with_the_versions` (سجلّ `characterize` يحمل الإصدارين الجديدين، وبصمة ملف الإعداد نفسه لا تتغيّر) |
| `F2‑AC‑19` | تقييم ووحدة | حالات `appendix-a` في `cases/identifiers.toml` لأمثلة FR‑ID‑15 وFR‑ID‑16 و`1X-25`؛ و`_APPENDIX_A` في `test_electrical_shared_parser.py` يذكرها، ويذكر الاختبار القائم `test_every_appendix_a_example_is_in_the_case_set` كلَّ مثال؛ و`python -m tests.eval.electrical.m04_identifiers --out …` يعطي 1.0. والحالة الفاشلة تُفشل `test_electrical_shared_parser.py` في CI، كما اليوم |
| `F2‑AC‑20` | يدوي | المراجع يبحث في فرق الـPR بقائمة الوسوم الحقيقية المحفوظة خارج المستودع، والنتيجة 0 (NFR‑F2‑7). وفي هذه الجولة: §10.4 |
| `F2‑AC‑41` | وحدة | `test_electrical_shared_terminals_wires.py::test_f2_ac_41_a_digits_first_x_strip_reads_under_iec_and_gbt` |
| `F2‑AC‑42` | وحدة | `…::test_f2_ac_42_the_chinese_reading_and_the_xt_rule_stay` (دون تضييق، وIEC وحده، وNFPA وحده) |
| `F2‑AC‑43` | وحدة | `test_electrical_shared_rules.py::test_f2_ac_43_digits_first_conventions_are_data` (بلا الجدول: صيني وحده؛ ورمز مجهول، واصطلاح مجهول، وقائمة فارغة: `RuleError`؛ وتعديل الجدول وحده يغيّر القراءة) |

**(عُدّل في الإعادة 1)** تتغيّر صفوف `F2‑AC‑5` و`F2‑AC‑14` و`F2‑AC‑15` و`F2‑AC‑16` و`F2‑AC‑17` و`F2‑AC‑20`، وتُضاف اختبارات تراجع. والقائمة كاملة في §11.7.

**المتطلبات غير الوظيفية:**
- **NFR‑F2‑1 (الحتمية):** `test_electrical_shared_kks.py::test_nfr_f2_1_every_f2_case_parses_twice_the_same` يحلّل حالات F2 كلها مرتين بمحلّلين مستقلين، ويقارن `observed` حرفاً.
- **NFR‑F2‑2 (القواعد بيانات):** `test_electrical_shared_rules.py::test_nfr_f2_2_a_new_kks_form_and_a_new_suffix_need_no_code`: خريطة القواعد المعبّأة مع صيغة KKS إضافية ولاحقة ANSI إضافية تقرأ وسماً جديداً. ومعه `F2‑AC‑43`.
- **NFR‑F2‑3 (لا نص مستخدم في تعبير):** حالات `ansi_suffixes` في `F2‑AC‑16`، وطفرات القواعد. وأسطورة INC‑2 في `F2‑AC‑35`.
- **NFR‑F2‑4 (الأداء):** قياس يدوي يُسجَّل رقماه في جسد الـPR. سكربت في مجلد مؤقت خارج المستودع يقيس متوسط زمن `IdentifierParser.parse` على حالات M‑04 الـ192، بالقواعد المعبّأة وبقواعد فيها KKS وANSI مفعّلان، على `master` ثم على الفرع، على المضيف نفسه. والحدّ 1.2 ضعف. ومعه زمن `tally` على 1,000 رمز اصطناعي، لأن المسبار يضاعف التحليل فيه (§7 خ‑3).
- **NFR‑F2‑5 (حدود الأسطورة):** INC‑2 (`F2‑AC‑35`). وفي البند 1 حدّ `ansi_suffixes` وحده (64).
- **NFR‑F2‑6 (التراخيص):** `F2‑AC‑12`.
- **NFR‑F2‑7 (الخصوصية):** `F2‑AC‑20`.

### 5.2 حالات M‑04 الجديدة (`tests/eval/electrical/cases/identifiers.toml`)

**المفتاح الجديد `select`** في الحالة: جدول بأسماء `RuleSelection` (`conventions`، و`cross_reference_format`، و`extra_ansi_suffixes`). وتحلّل الأداة الحالة بـ`IdentifierParser(rules.narrowed(selection))`، مخبّأً لكل اختيار. والحالة بلا `select` تُحلَّل بالقواعد دون تضييق كما اليوم، فلا تتغيّر الحالات الـ192. والمفتاح يدخل بصمة الحالة (`_case_hash`) تلقائياً.

**التغيير في `m04_identifiers.py`:**
- `parse_case(parser, case) -> ParsedIdentifier | None`، ويستعمله `matches` واختبار المحلّل.
- `observed` يضيف ما وُجد فقط: `"kks"` (`form`، و`class`، و`prefix`، والأجزاء بـ`name` و`level` و`text` و`runs`)، و`"ansi"` (`functions` بـ`number` و`suffix` و`class`، و`instance`)، و`"alternatives"`. و`EXPECTED_KEYS` تضيفها.
- `compute_m04(parser, cases)` بالتوقيع نفسه.

**الحالات** (أمثلة مخترعة فقط). المصدر `appendix-a` لأمثلة الملحق كما هي في SRS 0.20، و`extra` لصيغ أخرى، و`invalid` لما يجب وسمه:

| المصدر | النص | `select.conventions` | المتوقَّع |
|---|---|---|---|
| appendix-a | `20XYZ10`، `20XYZ10AB123`، `20XYZ10AB123CD45`، `20XYZ10AB1234567`، `20XYZ104567` | `iec_81346`، `kks` | `kks_tag` بصنفه وأجزائه |
| appendix-a | `=20XYZ10AB123` | `iec_81346`، `kks` | `kks_tag` ببادئة `process`، والبديل `reference_designation` |
| appendix-a | `87T`، `50BF`، `51N`، `27-1` | `iec_81346`، `ansi` | `ansi_device` |
| appendix-a | `52`، `86` مع `hint = "ansi_device"` | `iec_81346`، `ansi` | `ansi_device` |
| appendix-a | `50/51` | `iec_81346`، `ansi` | `ansi_device` مركّب، والبديل `cross_reference` |
| appendix-a | `1X-25` | `iec_81346` | `terminal` باصطلاح `iec_81346` |
| extra | `20XYZ10AB123C`، `20 xyz10 ab123`، والوسم بأرقام كاملة العرض | `iec_81346`، `kks` | `kks_tag` |
| extra | `=20XYZ10AB123` | بلا `select` | `reference_designation` بجانب واحد، كما اليوم |
| extra | `52` بلا `hint` | `iec_81346`، `ansi` | `wire_number` بالبديل `ansi_device` |
| extra | `87t`، `87 T` | `iec_81346`، `ansi` | `ansi_device`، الشكل القانوني `87T` |
| extra | `50/51` مع `cross_reference_format = "page_slash_column"` | `iec_81346`، `ansi` | `cross_reference` بالبديل `ansi_device` |
| extra | `87QZ` مع `extra_ansi_suffixes = ["QZ"]` | `iec_81346`، `ansi` | `ansi_device` صالح |
| extra | `1X-25` | بلا `select` | `terminal` باصطلاح `cn_letter_codes`، كما اليوم |
| extra | `1X-25` | `gbt_5094` | `terminal` باصطلاح `gbt_5094` |
| invalid | `20XY10AB123`، `20XYZ10AB12` | `iec_81346`، `kks` | `kks_tag` غير صالح، `kks.malformed` |
| invalid | `2OXYZ1OAB12O` | `iec_81346`، `kks` | `kks_tag` غير صالح، `kks.letter_in_digit_position` |
| invalid | `87QZ` | `iec_81346`، `ansi` | `ansi_device` غير صالح، `ansi.unknown_suffix` |
| invalid-or-prose | `7XY123`، `00`، `100T` | `iec_81346`، `kks`، `ansi` | `none` |
| invalid-or-prose | `20XYZ10AB123` | بلا `select` | `none`، كما اليوم |

- `search_keys` و`canonical` وكل حقل يكتبها المطوّر من ناتج المحلّل بعد بنائه، ثم يراجعها مقابل §3.5 و§3.7 حقلاً حقلاً. فالحالة عقد، لا لقطة.
- العدد بعد البند 1 نحو 192 + 40. ويبقى M‑04 = 1.0 في CI.

## 6. ترتيب التنفيذ والتوازي

PR شيفرة واحد من `master` بعد دمج PR الوثائق هذا. وكل خطوة إيداع، والبوابات الست خضراء بعدها: `ruff format --check`، و`ruff check`، و`mypy src`، و`lint-imports`، و`pytest -rs` (`tests/unit tests/architecture tests/eval` على هذا المضيف، والتكامل في CI)، و`deploy/prometheus/test-rules.sh`.

1. **الأساس:** `kinds.py` (التعدادات والأنواع والحقول)، و`rules/errors.py`، و`rules/systems.py`، و`rules/__init__.py` (الحقول، و`from_mapping`، و`narrowed`، و`with_additive`)، والجداول في `identifiers.toml`، والإصداران. ولا تغيير في `_ORDER` بعد. والاختبارات: `F2‑AC‑12`، وNFR‑F2‑2 للقراءة، وتثبيت الإصدارين، وقيم `IdentifierKind` الإحدى عشرة، وطفرات الجداول. وM‑04 كما هو.
2. **الأطراف (US‑10):** `terminals.py`، و`F2‑AC‑41..43`، وحالات `1X-25`، و`select` في `m04_identifiers.py`.
3. **KKS (US‑1):** `kks.py` في `_ORDER`، والبديل `reference_designation`، و`F2‑AC‑1..6`، وحالات KKS، و`F2‑AC‑7` لـKKS.
4. **ANSI (US‑2):** `ansi.py` في `_ORDER` و`_HINTED`، والبدائل الثلاثة، و`F2‑AC‑8..11`، وحالات ANSI، و`F2‑AC‑7` لـANSI.
5. **ملف الإعداد والاكتشاف والتوصيف (US‑3 في النطاق):** المسبار في `tally`، و`profile.py`، و`profile_detection.py`، و`characterization.py`، و`projects.py:82`. و`F2‑AC‑13..15`، وجزء النطاق من `F2‑AC‑16` و`F2‑AC‑18`.
6. **العقد والموجّه:** `openapi.yaml`، و`06-domain-models.md`، واختبار الوصف، و`F2‑AC‑16..18` على الموجّه، و`README.md` في التقييم. ثم قياس NFR‑F2‑4 وتسجيله.

**التوازي:** المنفّذ واحد. والخطوات 2 و3 و4 مستقلة بعد الخطوة 1، فيجوز تبديل ترتيبها. والخطوة 5 تحتاج 3 و4 (المسبار يقرأ KKS وANSI). والخطوة 6 تحتاج 5.

**بعد البناء:** مراجعة الشيفرة والأمن والجودة كما في الدورات السابقة. والمراجع ينفّذ `F2‑AC‑20` يدوياً، ويراجع جدول ANSI وفئاته (NFR‑LIC‑05).

## 7. المخاطر والقرارات

**لا قرار ⚠️ في هذا التصميم، ولا في الإعادة 1 (§11.9).** حسم المالك القرارات كلها (Q1..Q9 وA‑5، واستثناء `1X-25` في 2026-11-09). ولا يمسّ التصميم الأمن ولا المصادقة ولا العزل بين المستأجرين ولا النشر ولا CI، ولا يضيف اعتمادية. فما يلي اختيارات تصميم ومخاطر بمعالجتها:

| # | الخطر أو الاختيار | المعالجة |
|---|---|---|
| خ‑1 | تغيير الترتيب يغيّر ناتجاً قائماً بصمت | KKS وANSI لا يقرآن شيئاً إلا بالاختيار. و`F2‑AC‑7` يقارن كل حالة قائمة بهما مفعّلين ومعطّلين، ويسرد ما يتغيّر بـANSI بالاسم |
| خ‑2 | الرجوع، والنشر المتدرّج (`deploy/rolling-deploy.sh`): نسخة قديمة تقرأ جسد ملف إعداد كتبته الجديدة | **(عُدّل في الإعادة 1)** `to_body` يحذف الحقول الافتراضية (§3.8). لكن الاكتشاف يكتب الحقلين آلياً في إصدار `DETECTED` حين تبلغ الوثيقة حدّ §11.1، فـ«ملف فعّل KKS أو ANSI» لا يكفي لحصر المعنيّين. والعلاج إجراء §4.1: `app` قبل `worker-electrical` في النشر، والحصر بمفاتيح الجسد قبل الرجوع || خ‑3 | المسبار يضاعف التحليل في `tally` | التحليل في `tally` على رموز الصفحة المأخوذة عيّنةً، لا على كل نص. ويُقاس مع NFR‑F2‑4 ويُسجَّل. وإن تجاوز الحدّ يقصر المسبار على الرموز التي تبدأ برقم، فـKKS وANSI لا يبدآن إلا به |
| خ‑4 | رموز قليلة بشكل KKS أو ANSI تُبقي الاقتراح بثقة 0 | **(عُدّل في الإعادة 1)** هذا ما وقع فعلاً (الملاحظة 1): رمز `16A` واحد أسقط ثقة اقتراح وثيقة IEC إلى صفر. والعلاج في §11.1: قاعدة دليل أضيق، وحدّ `MIN_IDENTIFIERS` قبل الاقتراح. وتحت الحدّ يبقى الحقل على ملف الإعداد الأساسي بثقة 1.0. ويُضبط الحدّ والعامل مع العتبات بعد F3 (`INC1‑R‑15`) |
| خ‑5 | رموز البادئات والحروف المستثناة ومعنى الخانات الأربع لم يُتحقق منها من نص الإرشادات (A‑2، A‑3، وA‑8 أدناه، RSK‑28) | كلها بيانات في الملف. ويُتحقق منها في F3 على الحقيقة المرجعية، وتُعدَّل بالملف وحده |
| خ‑6 | جدول ANSI يقترب من نص المعيار | فئات عامة بأسمائنا، وأكثر من رقم في فئة، ولا وصف. ويحرسه `F2‑AC‑12` وتحقق المفاتيح، ويراجعه المراجع (NFR‑LIC‑05) |
| خ‑7 | الوسم ببادئة `=` يُعدّ IEC في العدّ الأساسي وKKS في المسبار | لا يغيّر الخلط ولا اصطلاح الأجهزة إلا في وثيقة تكثر فيها الوسوم ببادئة. ويُرى في M‑32 بعد F3. ولو ظهر، يُستثنى ما عدّه المسبار KKS من عدّ IEC |
| خ‑8 | اختيار: حقل ثالث `ansi_suffixes` في ملف الإعداد، وQ3 ذكر حقلين | يطلبه نص `FR‑ID‑16` («تُقبل لواحق إضافية للمشروع من ملف الإعداد») و`F2‑AC‑11`، وكلاهما أقرّه المالك. وهو المكان الذي تكتب فيه الأسطورة المؤكَّدة لواحقها في INC‑2. والاختيار إضافي ومحدود (64) ولا يُترجَم إلى تعبير |
| خ‑9 | اختيار: مفاتيح بحث KKS بلا حذف للأصفار البادئة | المواضع ثابتة العدد، فالحذف يجمع وسمين مختلفين في مفتاح. والمفتاح للاسترجاع وحده (S‑6)، ويعيد INC‑8 النظر فيه إن قاسه |
| خ‑10 | اختيار: `[confusables]` في ملف القواعد الآن، وFR‑ID‑18 في INC‑2 | يسمّي سبب عدم الصلاحية في `F2‑AC‑5` دون أن يصحّح شيئاً. ويقرؤه التصحيح في INC‑2، فتبقى قائمة الأزواج واحدة |
| خ‑11 | (جديد في الإعادة 1) قاعدة الدليل تُسقط دليلاً حقيقياً. أمثلته: `25A`، والوسم بمستوى النظام وحده، ورقم ANSI حقيقي لا فئة له بعد في الجدول | الرمز يبقى يُقرأ ANSI أو KKS حين يُفعَّل الاصطلاح، وإنما لا يُعدّ دليلاً. والوثيقة الحقيقية فيها أشكال أخرى كثيرة، مثل `87T` و`50BF` و`51N` و`27-1` ووسوم المعدّات. والقاعدة كلها بيانات، فتُضبط بالملف وحده بعد قياس M‑32 في F3 |
| خ‑12 | (جديد في الإعادة 1، الملاحظة 11) حين يُفعَّل IEC وANSI معاً، يصير `1NC` و`3PH` و`1KM` و`1XT-5` أجهزة ANSI غير صالحة، وتصير `24V` و`5V` أجهزة ANSI صالحة بلا فئة | **مؤجَّل إلى F3.** يُقاس مع M‑30 على صفحات المورد الأول، ويُذكر في مخاطر LLD الزيادة INC‑2 (§8). وهو مقصود في التصميم (`87QZ`)، ولا يمسّ الاكتشاف بعد §11.1، ولا وثيقة لم تفعّل ANSI. والعلاج المتاح للمستخدم الآن تسمية الجهد (§11.2) |
| خ‑13 | (جديد في الإعادة 1، الملاحظة 8) `_OUTER` في `kks.py:41` ثابت في الشيفرة | **مؤجَّل إلى INC‑2، مع حدّ موثَّق هنا.** الصيغة الصالحة الجديدة تُقرأ من الملف وحده (NFR‑F2‑2 محقَّق). أما الوسم شبه الصحيح لصيغة خارج `\d{1,3}[A-Z]{1,4}\d{1,3}(?:[A-Z]{1,3}\d{1,4}[A-Z]?){0,3}` فيعود بلا معرّف، لا غير صالح. ولاحقة الكابل بطول خاطئ (ملاحظة QA 1) من هذا الحدّ. وحين تُضاف أول صيغة مورد خارج الشكل، يُشتق الشكل الخارجي من الأقنعة |
| خ‑14 | (جديد في الإعادة 1) الاكتشاف يقترح ANSI فوق تسمية جهد مخزَّنة يجعلها ANSI جهازاً (§11.2) | في INC‑1 لا يضيّق شيء ملف الإعداد عند التحليل إلا `parse_profile`، فلا ينكسر قارئ. وأول تعديل يحمل التسمية يعطي 422 باسم `voltage_labels`. **ويُحسم في INC‑2 مع STG‑05** (§8) |

**A‑8 (افتراض جديد، بلا ⚠️):** رموز البادئات في الملف `=` للعملية و`+` لموضع التركيب و`++` للمكان، من المعرفة العامة لا من نص الإرشادات. ولم يُرَ في العيّنات إلا الوسم بلا بادئة. وهي بيانات تُصحَّح بالملف إن خالفها المعيار أو المورد. ويُتحقق منها مع A‑3.

## 8. ما يبقى لـINC‑2 (البنود 2..4)

**يعتمد على البند 1 مدموجاً:** النوعان، و`alternatives`، و`needs_context`، و`hint=ANSI_DEVICE`، و`rules.confusables`، والحقول الثلاثة في ملف الإعداد.

**ويحتاج من LLD الزيادة INC‑2:**
1. **عقد السطور من `ele-parse`** (HLD §7.1 STG‑02): لكل سطر صندوقه وزاويته وثقة كل حرف، ويُستحسن البديل الثاني لكل حرف. وهو شرط للبندين 3 و4.
2. **البند 3، التسمية المكدّسة (US‑6، `F2‑AC‑27..31`):** في STG‑05، من صناديق السطور وزواياها وقطعة الخط الفاصلة. ويحتاج STG‑02 (البدائيات) وعقد السطور. ونوع ناتجها (معرّف بجزأين) يحدّده LLD. ومعه M‑36.
3. **البند 4، التصحيح الموضعي (US‑5، `F2‑AC‑23..26`):** في STG‑05، على بنية KKS وثقة كل حرف. ومعه حقل «الشكل المصحَّح وقائمة التصحيحات» في سجلّ الظهور والدليل (DATA‑06، HLD §8.6)، ومفتاح تفعيله في ملف الإعداد، معطّلاً افتراضياً.
4. **البند 2، الأسطورة (US‑7، `F2‑AC‑32..37`):** نوع الصفحة (FR‑LAY‑01) وفصل الجداول (FR‑LAY‑04) في STG‑04، وحقل المدخلات المؤكَّدة في ملف الإعداد بحدود NFR‑F2‑5، ومسار الاقتراح والتأكيد (HLD §13.5). وتكتب لواحقها في `ansi_suffixes`.
5. **سياق ANSI في STG‑05** (§3.9): الرقم المجرد بدليل، والمكدّس في فقاعة، والمعرّف الكامل من KKS المحيط أو الجزء الثاني أو البنية الموروثة (FR‑ID‑02).
6. **القياس (US‑8، `F2‑AC‑38..40`):** على الحقيقة المرجعية من F3. وM‑32 بالحقلين في `m32_profile_detection.py` (ومعه حصر `identifier_convention` في اصطلاحات الأجهزة في ملف التسميات).
7. **(عُدّل في الإعادة 1) ما نقلته الإعادة 1 إلى LLD الزيادة INC‑2:**
   - **قارئ الجسد المخزَّن:** يتسامح مع المفاتيح التي لا يعرفها، ويبقى `from_body` صارماً على مدخل العميل (S‑2، §4.1).
   - **التسمية فوق ANSI المكتشَف (خ‑14):** حين يضيّق STG‑05 ملف الإعداد عند التحليل، يرفع `narrowed` خطأ `RuleError` على تسمية تخفي جهاز ANSI. فيُتحقق من الاقتراح كما يفعل `parse_profile` في موضعين: التأكيد بلا جسد (`documents.py:353-380`) والقبول الآلي (`inspection.py:817`). وعند التعارض يعطي التأكيد 422 باسم `voltage_labels`، ولا يقع القبول الآلي. ولا يرفع العامل خطأً لوثيقة.
   - **الضجيج والشكل الخارجي:** خ‑12 وخ‑13 في المخاطر. ومعهما ضبط قاعدة الدليل بالملف بعد M‑32 (خ‑11).

## 9. ما لم أتحقق منه

- نص إرشادات KKS ونص IEEE C37.2: لم أقرأهما (Q8)، كالمحلّل. والبنية في §3.3 من `requirements.md` §1 ومن المعرفة العامة.
- رموز البادئات (A‑8)، واستثناء `I` و`O` (A‑3)، ومعنى الخانات الأربع (A‑2).
- أن الصيغ الخمس لا تتداخل على وسوم حقيقية: تحققتُ منها على الأمثلة المخترعة وحدها.
- زمن المحلّل والمسبار (NFR‑F2‑4): لم أقسه. يُقاس عند البناء.
- شغّلتُ المحلّل الحالي على `master` على أمثلة مخترعة، للتحقق من سلوك اليوم في §3.5 و§3.6 و§3.9. ولم أشغّل غيره، ولم أغيّر حالة شيء.

## 10. التعديلات المطبَّقة في هذه الجولة

كل تعديل بقرار المالك في 2026-11-08. وأرقام الأسطر من الملفين بعد التعديل.

### 10.1 `docs/requirements.md`: من 0.19 إلى 0.20 (S1..S19 وA‑5)

| # | الموضع بعد التعديل | ما طُبّق |
|---|---|---|
| S1 | الرأس (`:8`، `:12`) | 0.20، والتاريخ 2026-11-08 كما هو، و«القرارات D‑01..D‑36 كلها محسومة» |
| S2 | سجل الإصدارات (`:37`) | صفّ 0.20، ويذكر FR‑ID‑03 (A‑5) و§1.4 و§1.5 و§3.4 و§9.1 وR‑11 أيضاً |
| S3 | §1.4 (`:156-159`) | الصفوف الأربعة، بعد صفّ أكواد الحروف الصينية. وتعريف KKS يذكر أن المثال مخترع |
| S4 | §1.5 (`:177-178`) | المرجعان، ومع كلٍّ ما يُضمَّن منه وما لا يُضمَّن (NFR‑LIC‑05) |
| S5 | §3.4 البند 3 (`:349`) | الجملة، ومعها «مع أطراف بصيغة IEC» |
| S6 | `FR‑ING‑01` (`:381`) | الحقلان ومدخلات الأسطورة، و`D‑36` في المصدر |
| S7 | `FR‑ING‑12` (`:393`) | الإضافتان، و`D‑36` في المصدر |
| S8 | `FR‑ING‑14` (`:394`) | النص، ومعه جملة NFR‑F2‑3 (لا قاعدة ولا تعبير من نص الأسطورة، وحدود يذكرها ملف الإعداد). **فرق في الموضع:** بعد `FR‑ING‑12` في آخر الجدول، لا بعد `FR‑ING‑13`. فـ`FR‑ING‑13` في الجدول بعد `FR‑ING‑03`، والجديد يتبع `FR‑ING‑12` موضوعاً |
| S9 | `FR‑ID‑13` (`:439`) | الجملة، و`D‑36` في المصدر |
| S10 | `FR‑ID‑15..18` (`:441-444`) | النصوص كما في `requirements.md` §3.1 بالتوصيات. وفروق للقرار: في `FR‑ID‑15` «والصيغ الافتراضية صيغ المورد الأول، وتُضاف غيرها بملف القواعد دون تعديل الشيفرة» (Q1 (ج))، و«الوسم الذي له شكل KKS ويخالف بنيته يُوسم غير صالح ولا يُغيَّر». وفي `FR‑ID‑16` «لواحق إضافية للمشروع من ملف الإعداد، ومنها ما أكده إنسان من الأسطورة»، و«يُسجَّل» بدل «يُوسم» للالتباس الباقي |
| S11 | `NFR‑LIC‑05` (`:884`) | كما في §3.1 |
| S12 | `DATA‑06` (`:938`) | كما في §3.1، ومعه `D‑36` |
| S13 | `M‑04` (`:1024`) | «تطابق تام للمعرّف ونوعه واصطلاحه (D‑36)» |
| S14 | `M‑30` (`:1050`) | الإضافة إلى التعريف، و«FR‑ID‑15..18» في المتطلبات، والأهداف كما هي |
| S15 | `M‑32` (`:1052`) | «ونظام تعريف المحطة وأرقام وظائف الأجهزة»، والأهداف كما هي |
| S16 | `M‑36` (`:1056`) | الصفّ كما في §4.1 |
| S17 | §8.3 (`:1065`) | الصفّ «معرّفات كل اصطلاح» |
| S18 | §11.1 العنوان (`:1149`) وصفّ `D‑36` (`:1188`) | «وD‑35 وD‑36 في 2026-11-08». والقرار Q1..Q9 بالتوصية وA‑5، والأثر قائمة S2 وما بقي بلا تغيير |
| S19 | ملحق أ (`:1203`، `:1210-1213`)، و§9.1 (`:1083`، `:1089`)، و`R‑11` (`:1129`) | الصفوف الأربعة بأمثلة مخترعة. وفي §9.1 «FR‑ID‑01..18» لـI‑04، وFR‑ID‑15 وFR‑ID‑16 وFR‑ING‑14 وM‑36 لـI‑10. وفي `R‑11` FR‑ID‑15 وFR‑ID‑16 وFR‑ING‑14 |
| A‑5 | `FR‑ID‑03` (`:429`) وصفّ «طرف» في ملحق أ (`:1203`) | المثال `1X-25` وجملة «تحت الاصطلاحات التي يحددها ملف القواعد لكل رمز، ومنها IEC 81346 عند المورد الأول»، و`D‑36` في المصدر |

**لم يُعدَّل، كما في `requirements.md` §8:** FR‑ID‑01..02 و04..12، وFR‑ID‑14، وFR‑VEC‑04، وFR‑SYM‑12، وFR‑NLU‑09، وD‑04، وD‑05، وملحق ب.

### 10.2 `docs/hld.md`: من 0.18 إلى 0.19 (H‑1..H‑12)

| # | الموضع بعد التعديل | ما طُبّق |
|---|---|---|
| رأس | الإصدار والمدخل الملزم والحالة (`:10-15`)، وصفّ 0.19 (`:39`) | 0.19 بعد F2، وSRS 0.20 وD‑01..D‑36، و«تسعة عشر قراراً محسوماً (DEC‑01..DEC‑06، DEC‑08..DEC‑20)» |
| H‑1 | §5.4 البندان 1 و2 (`:463-464`) | KKS وANSI اصطلاحان إضافيان، وبنيتهما وجدولهما بيانات، وأطراف `1X-25` تحت IEC (A‑5) |
| H‑2 | §13.5 صفّ ملف الإعداد (`:1414`) | الحقلان المستقلان ولواحق ANSI، والقراءة بهما وباصطلاح الأجهزة معاً. ومسألة `schema` في هذه الوثيقة §3.8 (يبقى 1)، لأنها تفصيل LLD |
| H‑3 | §12.5 (`:1341`) | بند البند 1 من F2: تغيير إضافي غير كاسر في وصف الجسد، ولا مسار ولا خاصية |
| H‑4 | §7.1 STG‑01 (`:666`)، و§5.4 (`:473` وما يليه) | الاكتشاف يقترح الحقلين، واجتماعهما مع اصطلاح الأجهزة ليس خلطاً. ولا معيار رموز منهما في هذه الوثيقة §3.10 |
| H‑5 | §5.4 كتلة «KKS وANSI في المحلّل» (`:473` وما يليه) | النوعان، والتفعيل بالاختيار، وقواعد الالتباس في `shared`، وما ينتظر سياق STG‑05 |
| H‑6 | §5.4 بند «التسليم» | رفع إصدار `shared` وإصدار ملف القواعد، وأثره على سجلّ التوصيف (NFR‑REP‑01). والتفصيل في هذه الوثيقة §4 |
| H‑7 | §7.1 STG‑05 (`:670`) | سياق ANSI، والتسمية المكدّسة، والتصحيح الموضعي معطّلاً، وFR‑ID‑15..18 في المتطلبات |
| H‑8 | §7.1 STG‑02 (`:667`) وصفّ INC‑2 (`:1731`) | لكل سطر صندوقه وزاويته وثقة كل حرف، والبديل الثاني مستحسن |
| H‑9 | §8.6 (`:844`) | التصحيح وأجزاء التسمية المكدّسة في الدليل، والخط الفاصل نص لا سلك (FR‑ID‑18، DATA‑06) |
| H‑10 | §7.1 STG‑04 (`:669`) و§13.5 (`:1414`) | الأسطورة مقترحاتٍ لملف إعداد الوثيقة، بحدود NFR‑F2‑5، والتأكيد بالمسار القائم، وإصدار جديد وإعادة التشغيل (FR‑ING‑14، FR‑ADM‑04) |
| H‑11 | §16 صفّ INC‑2 (`:1731`)، والشكل 10 (`:1764`)، وفقرتا «أول نتيجة» | النطاق يضمّ البنود 2..4 وM‑36. و«تعتمد على» البند 1 من F2 PR مستقلاً بلا معرّف زيادة. و«قرارات تحجبها»: «—»، فبعد دمج البند 1 لا تحجب F2 قبول STG‑05 |
| H‑12 | §13.7 صفّ «قواعد المورد الأول» (`:1432`) | M‑04 وM‑30 وM‑36 وM‑32 كما قرر المالك، والحالات المخترعة، وحدّ §8.3. وتفصيل الحالات في هذه الوثيقة §5.2 |
| مخاطر | §18.2 `RSK‑22` (`:1902`) و`RSK‑28..RSK‑30` (`:1908-1910`) | M‑32 ينتظر البند 1 وتسميات F3؛ وبنية KKS، والتباس ANSI، والتصحيح |
| قرار | §18.3 `DEC‑19` «يحجب» (`:2233`) و`✅ DEC‑20` (`:2235`) | سجلّ لقرار المالك على نهج DEC‑19، بلا ⚠️ |
| أخرى | §13.8 (`:1461`)، و§17.1 FR‑ING وFR‑ID (`:1780`، `:1783`)، و§18.1 R‑11 (`:1862`) | نصوص المعايير وما يُضمَّن منها؛ والزيادات في جدول التتبّع؛ وحقلا KKS وANSI في معالجة R‑11 |

**لم يُضَف ADR.** صفوف §15 لاختيار تقنية أو مكتبة أو نموذج (SRS AC‑08)، والبند 1 لا يضيف شيئاً منها.

### 10.3 وثائق الدورة

- `inc-1/f2/requirements.md`: سطر الحالة (القرارات وأين طُبّقت)، والبند 1 في §2 يضمّ أطراف `9A-99`، وبند «خارج النطاق» عنها مشطوب ومنقول، وصفّ البند 1 في جدول «يُبنى الآن»، وA‑5 «✅ قرّر المالك»، وسطر في رأس §6.2 بالقرارات. وبقي الباقي سجلاً.
- `inc-1/f2/stories.md`: سطر الافتراض، وUS‑10 (`F2‑AC‑41..43`)، و`F2‑AC‑19` (صفّا FR‑ID‑15 وFR‑ID‑16 ومثال `1X-25`؛ والتسمية المكدّسة مع US‑6)، و`F2‑AC‑21` (FR‑ID‑03 يتغيّر بـA‑5)، وجدول التقدير (10 قصص و43 معياراً).
- `status.md`: لم أعدّله. يعدّله المنسّق.

### 10.4 نتائج التحقق (2026-11-08، بعد التطبيق)

أجريتها بنفسي على الملفات بعد التعديل، بأوامر قراءة فقط.

| المعيار | النتيجة | الدليل |
|---|---|---|
| `F2‑AC‑21` | ✅ | `docs/requirements.md`: الرأس 0.20 و«D‑01..D‑36»، وصفّ 0.20 في السجل، وصفّ `D‑36` واحد (`:1188`)، والمتطلبات الجديدة والمعدّلة بنص القرار (§10.1). و`git diff -U0` لا يحوي سطراً محذوفاً من صفوف D‑04 وD‑05 وFR‑ID‑01..02 و04..12 وFR‑ID‑14 وFR‑VEC‑04 وFR‑SYM‑12 وFR‑NLU‑09 (0 سطور). وصفّ FR‑ID‑03 تغيّر بـA‑5 وحده، كما يقول المعيار بعد تعديله |
| `F2‑AC‑22` | ✅ | `git diff --stat` و`git status`: المعدَّل `docs/hld.md` و`docs/requirements.md` و`status.md` (عدّله المنسّق)، وغير المتتبَّع `inc-1/f2/` وحده، وكلها داخل `docs/delivery/electrical-drawings/`. ولا تعديل في `docs/design/*` ولا `src/` ولا `CLAUDE.md` ولا `.importlinter` |
| الأمثلة مخترعة | ✅ | الأمثلة الجديدة كلها من القائمة المخترعة في `requirements.md` §3.2 و§5 (`20XYZ10…`، `52`، `86`، `87T`، `50BF`، `51N`، `27-1`، `50/51`، `94`/`Q1`، `1X-25`، `87QZ`، `7XY123`). ولا اسم ملف `.pdf` ولا اسم مشروع في الأسطر المضافة |
| RTL | ✅ | `hld.md` وهذه الوثيقة و`requirements.md` و`stories.md` في `inc-1/f2/` تبدأ بـ`<div dir="rtl">` وتنتهي بـ`</div>`. و`docs/requirements.md` (SRS) لم يكن ملفوفاً، وبقي كذلك على عادته |
| الشرطة | ✅ | في `hld.md` لا معرّف HLD بشرطة عادية في الأسطر المضافة (`DEC-`، `RSK-`، `INC-`، `STG-`: 0)، ومعرّفات SRS فيه بالشرطة العادية كعادته. وفي SRS معرّفاته بالشرطة العادية، و`INC‑2` في صفّ D‑36 بالشرطة غير المنكسرة كما `INC‑0` في صفّ D‑32 |
| لا ⚠️ جديد | ✅ | `DEC‑20` ✅. وفي الأسطر المضافة إلى `hld.md` وSRS علامة ⚠️ واحدة، هي عبارة الحالة القائمة «لا قرار ⚠️ معلّقاً كاملاً» في سطر الرأس المعدَّل |

## 11. الإعادة 1 (2026-11-09)

- **المدخلات:**
  - `inc-1/f2/review.md`: FAIL. الملاحظتان الحاجبتان #1 و#2، والمهمّة #3..#5، والتحسين #6..#11.
  - `inc-1/f2/security-review.md`: PASS، بملاحظات S‑1..S‑5.
  - تأكيد المنسّق للملاحظة 1 بالتشغيل: `tally` بمحلّل IEC وحده على `-K1..-K120` يعطي `{iec_81346: 120}`، وبإضافة `16A` واحد `{ansi: 1, iec_81346: 120}`.
- **ما شغّلتُه:** المحلّل والمسبار ودوال `kks.py` على الفرع (`bd05ee8`) في `.venv`، بأمثلة مخترعة، للقراءة فقط. ولم أكتب ملفاً غير هذه الوثيقة و`stories.md`، ولم أشغّل شيئاً على المكدّس.
- **ما لا يتغيّر:** لا API جديد، ولا جدول، ولا ترحيل، ولا اعتمادية، ولا إعداد نشر. والمنفّذ كله `backend-developer`.

### 11.1 قاعدة الدليل (الملاحظة 1، حاجبة)

**ما قِسته.** القواعد المعبّأة كاملة، كما يستعملها `tally` (`inspection.py:254`)، والمسبار `with_additive()`. والنتيجة واحدة بمحلّل IEC وحده:

| الرموز | القراءة الأساسية | دليل على ANSI اليوم | للرقم فئة في `[ansi.functions]` |
|---|---|---|---|
| `16A`، `4P`، `2P`، `1N`، `24V`، `48V`، `12V` | لا معرّف | نعم | لا |
| `25A`، `32A`، `50A` | لا معرّف | نعم | نعم (25، 32، 50) |
| كل رقم بلا فئة تتبعه لاحقة معروفة (خانة واحدة ثم حرف من الجدول) | لا معرّف | نعم | لا |
| `87T`، `50BF`، `51N`، `27-1` | لا معرّف | نعم | نعم |
| `52`، `50/51` | رقم سلك، مرجع متقاطع | لا (ملتبس) | — |

**ولـKKS ضعف مشابه:**
- شكل مستوى النظام وحده (`NNAAANN`، رقمان ثم ثلاثة أحرف ثم رقمان) يطابق نصاً آخر يُقرأ وسماً صالحاً.
- منه تاريخ بصيغة يوم ثم ثلاثة أحرف للشهر ثم سنة من رقمين، وقيمة ثم وحدة من ثلاثة أحرف ثم رقمان.
- فكتلة العنوان وحدها، وفيها التاريخ نفسه في كل صفحة، تبلغ أي حدّ عددي في وثيقة طويلة.
- أما وسم المعدّة (`NNAAANN` ثم `AANNN`، 12 حرفاً بمواضع ثابتة) فلا يطابقه نص عارض عملياً.

**الخيارات:**

| الخيار | ما يحلّه | ما يبقى | الحكم |
|---|---|---|---|
| (أ) استثناء لواحق الوحدة والقطب (`A`، `P`، `N`، `V`) | `16A`، `4P`، `1N`، `24V` | يُسقط `50N` و`51N` الحقيقيين، فهما للتعادل. ويبقى كل رقم بلا فئة بلاحقة أخرى | لا يكفي وحده |
| (ب) حدّ أدنى للعدد وللحصة | الرمز الواحد | **العدد وحده لا يكفي:** لوحة توزيع فيها ثلاثون قاطعاً بالتسمية نفسها تبلغه. **والحصة بلا مقام ثابت:** `identifiers` يعدّ قراءات الأجهزة وحدها، فمقام وثيقة KKS خالصة قريب من الصفر، وحصة ANSI الحقيقي في وثيقة IEC كبيرة صغيرة | العدد نعم، والحصة لا |
| (د) رقم له فئة ولاحقة معروفة | كل رقم بلا فئة: `16A`، `4P`، `2P`، `1N`، `24V` | `25A` و`32A` و`50A`، لأن أرقامها في الجدول | لا يكفي وحده |
| (ج) الجمع | — | — | **المختار، بالصيغة أدناه** |

**القاعدة المختارة: (د)، ومعها استثناء لاحقة واحدة بالبيانات، ثم (ب) بالعدد وحده.** وتنطبق على KKS بشرط المستوى.

1. **دليل ANSI** (`shared/ansi.py`، دالة عامة جديدة):

   ```python
   def is_ansi_evidence(found: ParsedIdentifier, rules: IdentifierRules) -> bool:
       """D-36: a reading that proves a document uses ANSI device numbers. VALID, one function
       and no other reading (a bare number or a compound proves nothing), a number with a class
       in [ansi.functions], and a suffix outside [ansi] non_evidence_suffixes (a rating letter,
       as in a breaker's 32A) or else an instance (27-1). The tally counts these, and a voltage
       label may not hide one."""
       if found.ansi is None or not found.valid or found.alternatives:
           return False
       if len(found.ansi.functions) != 1:
           return False
       function = found.ansi.functions[0]
       if function.function_class is None:
           return False
       if function.suffix:
           return function.suffix not in rules.ansi.non_evidence_suffixes
       return found.ansi.instance is not None
   ```

2. **دليل KKS** (`shared/kks.py`، دالة عامة جديدة):

   ```python
   def is_kks_evidence(found: ParsedIdentifier, rules: IdentifierRules) -> bool:
       """D-36: a VALID tag at [kks] evidence_min_level or deeper (an equipment tag by default).
       A system-level shape alone also fits other text (a date, a rating with its unit)."""
       return (
           found.valid
           and found.kks is not None
           and found.kks.level >= rules.kks.evidence_min_level
       )
   ```

3. **`shared/parser.py`:** يصير `_is_evidence(found, rules)`:
   - يرفض غير الصالح وما فيه `text.non_ascii`، كما اليوم.
   - ثم يستدعي `is_ansi_evidence` لـ`Convention.ANSI` و`is_kks_evidence` لـ`Convention.KKS`.
   - ويُعيد `True` لغيرهما.
   - ويُستدعى بـ`self._rules` في `_count` وفي فرع المسبار. وجداول المسبار هي نفسها، لأن `with_additive` لا يغيّر إلا `conventions`.

4. **البيانات** (`identifiers.toml`، NFR‑F2‑2). الإصدار يبقى `1.1.0` (§3.3):

   ```toml
   [kks]
   excluded_letters = []
   # D-36: the shallowest level the tally counts as evidence of KKS (2 = an equipment tag).
   # A system-level shape alone (two digits, three letters, two digits) also fits other text.
   evidence_min_level = 2

   [ansi]
   max_compound = 3
   suffixes = [...]  # unchanged
   # D-36: suffix letters that also write a rating (A: amperes, as in a breaker's 32A). A reading
   # with one is still an ANSI device when ANSI is enabled; it is no evidence for the tally.
   non_evidence_suffixes = ["A"]
   ```

   - `evidence_min_level` لازم، ويُقرأ بـ`_int(..., "kks.evidence_min_level", 1, 3)`، فيرفض `bool` والنص.
   - `non_evidence_suffixes` لازم، قائمة قد تكون فارغة. كل عنصر فيها من `suffixes`، ولا يتكرر. والرسائل ثابتة لا تعيد القيمة.
   - الحقلان: `KksRules.evidence_min_level: int` و`AnsiRules.non_evidence_suffixes: frozenset[str]`.

5. **الحدّ** (`drawings/domain/profile_detection.py`، `_systems`):

   ```python
   def _systems(
       base: DrawingProfile, tally: ConventionTally
   ) -> tuple[PlantDesignation, DeviceFunctionNumbers, float, float, tuple[str, ...]]:
       """KKS and ANSI (D-36): proposed from MIN_IDENTIFIERS evidence identifiers on, at the
       sample factor. Under that, and at zero, the base profile's value stays at 1.0 and the
       field is not in basis["systems"], so a stray token never holds the proposal back."""
       kks = tally.conventions.get(Convention.KKS, 0)
       ansi = tally.conventions.get(Convention.ANSI, 0)
       kks_seen, ansi_seen = kks >= MIN_IDENTIFIERS, ansi >= MIN_IDENTIFIERS
       plant = PlantDesignation.KKS if kks_seen else base.plant_designation
       functions = DeviceFunctionNumbers.ANSI if ansi_seen else base.device_function_numbers
       basis = tuple(
           f"{c.value}:{n}"
           for c, n, seen in ((Convention.KKS, kks, kks_seen), (Convention.ANSI, ansi, ansi_seen))
           if seen
       )
       return (
           plant,
           functions,
           _sample_factor(kks) if kks_seen else 1.0,
           _sample_factor(ansi) if ansi_seen else 1.0,
           basis,
       )
   ```

   وتُصحَّح الوثيقة النصية للوحدة (`profile_detection.py:18-22`): «proposed from ``MIN_IDENTIFIERS`` evidence identifiers».

**أين تعيش القاعدة:**
- **في البيانات:** جدول الفئات، و`non_evidence_suffixes`، و`evidence_min_level`. فإضافة رقم إلى `[ansi.functions]` تجعله دليلاً، وتعديل المستوى أو اللواحق بالملف وحده (NFR‑F2‑2).
- **في الشيفرة:** الحدّ `MIN_IDENTIFIERS`. فهو ثابت الاكتشاف نفسه الذي يحكم اصطلاح الأجهزة اليوم (`profile_detection.py:53`)، وليس قاعدة تعريف. وNFR‑F2‑2 يخصّ الصيغ واللواحق.

**لماذا هذه الصيغة:**
- **الضجيج المقيس يسقط كله:** الرقم بلا فئة يسقط بالشرط (د)، و`25A`/`32A`/`50A` تسقط باللاحقة `A`.
- **الأشكال الحقيقية الملتبسة تبقى:** `50N` و`51N` (للرقم فئة، و`N` ليست مستثناة)، ومعها `87T` و`50BF` و`27-1`.
- **الحدّ يمنع ما يبقى من أن يسقط الاقتراح:** وسم عارض، أو `87T` واحد في ملاحظة مراجعة. وهذا أصل التراجع، فرمز واحد كان يكفي.
- **ما يُفقد مقبول ومسجَّل (§7 خ‑11):** `25A` الحقيقي، ووسوم مستوى النظام وحدها، ورقم ANSI حقيقي لم يدخل الجدول بعد. فالوثيقة الحقيقية فيها أشكال أخرى كثيرة.

**الأثر على ما بُني:**
- **التحليل نفسه لا يتغيّر:** حالات M‑04، و`F2‑AC‑7..11`، وكل ما يقرؤه المحلّل حين يُفعَّل ANSI أو KKS.
- **كيس `F2‑AC‑14`:** `ansi_numbers` اليوم يولّد أرقاماً من 50 إلى 71، وأكثرها بلا فئة، فلا يُعدّ منها إلا 6 من 22. فيصير يدور على `("87T", "50BF", "51N", "27-1")`، وكلها في الفرق منذ الجولة الأولى وقد فحصها `F2‑AC‑20`. والتأكيدات كما هي.
- **`F2‑AC‑15`:**
  - 19 وسماً: لا يُقترح KKS، والحقل على الأساس بثقة 1.0، فلا قبول آلي لـKKS.
  - والمعيار يقول «ثقة KKS صفر، ولا يُقبل آلياً». فالقراءة المعتمدة هنا: لا يُقترح KKS، فلا يُقبل آلياً. وقصده محقَّق وأقوى.
  - وتعديل نصّه في `stories.md` للمنسّق (§11.9).
- **سجلّ التوصيف:** `conventions` لكل وثيقة يعدّ الدليل وحده، والمجمّع يعدّ الوثائق التي فيها 20 منه على الأقل (§3.11). ووصفهما في §3.12.

**الاختبارات (وحدة):**
1. `test_electrical_f2_profile.py::test_rating_and_pole_tokens_leave_an_iec_proposal_as_it_was`:
   - `propose(iec(120) + ["16A", "32A", "4P", "2P", "1N"]) == propose(iec(120))`، مقارنة `ProfileProposal` كاملاً.
   - و`Convention.ANSI not in PARSER.tally(...).conventions`.
   - ثم الرموز الخمسة مكرّرة 30 مرة (150 رمزاً): الاقتراح نفسه. فهذا يثبت قاعدة الدليل، لا الحدّ.
   - ويؤكد على الاقتراح: الثقة نفسها، و`none` في الحقلين، و`"systems" not in basis`.
2. `…::test_system_level_shapes_are_no_kks_evidence`: `iec(120) + ["20XYZ10"] * 30 + ["20XYZ104567"] * 30` يعطي الاقتراح نفسه، ولا مفتاح `kks` في العدّ.
3. `…::test_under_twenty_evidence_identifiers_nothing_is_proposed`:
   - `iec(120) + ansi_numbers(19)` يعطي `propose(iec(120))` نفسه.
   - و`ansi_numbers(20)`: يُقترح `ansi`، والحقل بثقة 0.2، و`basis["systems"] == ("ansi:20",)`.
4. `…::test_f2_ac_15_under_twenty_tags_kks_is_not_proposed` (يحلّ محل `test_f2_ac_15_under_twenty_tags_kks_has_zero_confidence`):
   - `iec(100) + kks_tags(19)`: `plant_designation` على `none` بثقة 1.0، و`confidence == propose(iec(100)).confidence`، ولا `systems`.
   - و`kks_tags(20)`: يُقترح `kks` بثقة 0.2.
5. `…::test_f2_ac_14_*`: بالكيس الجديد، والتأكيدات كما هي.
6. `test_electrical_shared_ansi.py::test_the_ansi_evidence_rule`، مُعلَّم بالحالات:
   - **دليل:** `87T` و`50BF` و`51N` و`27-1`.
   - **ليس دليلاً:** `32A` و`25A` و`50A` (اللاحقة `A`)، و`16A` و`4P` و`2P` و`1N` و`24V` (بلا فئة)، و`52` مع `hint` و`50/51` (ملتبسان)، و`87QZ` (غير صالح).
7. `test_electrical_shared_kks.py::test_the_kks_evidence_rule`:
   - **دليل:** `20XYZ10AB123` و`20XYZ10AB123CD45` و`20XYZ10AB1234567`.
   - **ليس دليلاً:** `20XYZ10` و`20XYZ104567` (المستوى 1)، و`20XY10AB123` (غير صالح).
8. `test_electrical_shared_rules.py::test_the_evidence_rules_are_data` (NFR‑F2‑2):
   - خريطة القواعد المعبّأة مع `non_evidence_suffixes = []` تجعل `32A` يُعدّ في `tally`.
   - ومع `evidence_min_level = 1` يُعدّ `20XYZ10`.
   - **والطفرات تعطي `RuleError`:** غياب أي من المفتاحين؛ و`evidence_min_level` بقيمة 0 أو 4 أو `true` أو `"2"`؛ ولاحقة ليست في `suffixes`؛ وتكرار؛ وقيمة ليست قائمة.

### 11.2 فحص تسميات الجهد (الملاحظة 2، حاجبة)

**أؤكد إصلاح المراجع، وأضيف إليه استثناءً واحداً لـANSI.**

```python
# application/projects.py, parse_profile
    try:
        # under every DEVICE convention (a label must not hide `KM1` in a Chinese drawing
        # either) and the KKS / ANSI this profile enables, then under the profile's own
        packaged.narrowed(
            replace(selection, conventions=DEVICE_CONVENTIONS | selection.conventions)
        )
        packaged.narrowed(selection)
    except RuleError:
        raise invalid_profile(InvalidProfile("voltage_labels", _VOLTAGE_LABEL_TEXT)) from None
```

```python
# shared/rules/__init__.py, IdentifierRules._other_kind
        found = IdentifierParser(self).parse(label)
        if found is None or found.kind is IdentifierKind.VOLTAGE:
            return None
        if found.kind is IdentifierKind.ANSI_DEVICE and not is_ansi_evidence(found, self):
            return None  # D-36: 24V or 12VDC hide no device the product reads by its function
        ...  # the rail exception, unchanged
```

وتُحدَّث الوثيقة النصية لـ`_other_kind` و`_checked_voltage_labels` بالجملة نفسها.

**ما الصحيح في `24V` حين يُفعَّل ANSI؟**
- **القراءة:** `24V` يُقرأ فعلاً جهاز ANSI **صالحاً** تحت ذلك الملف: الرقم 24، واللاحقة `V` من الجدول. لكن الرقم 24 لا فئة له، فليس دليلاً بقاعدة §11.1. **فالصحيح أن يُقبل.**
- **السبب الأول:** حكم واحد لـ«هذا ANSI حقاً» في الاكتشاف وفي التحقق.
- **السبب الثاني:** في مشروع فعّل ANSI، التسمية هي طريق المستخدم الوحيد ليقول إن `24V` هنا جهد تغذية. فبدونها يُقرأ `24V` جهاز ANSI (§7 خ‑12)، ورفضها يمنع العلاج حيث يلزم.
- **ما يبقى مرفوضاً:** `87T` و`50BF` و`27-1` ما دام ANSI مفعّلاً. فهذه ما يحرسه `INC1‑Q‑5`: تسمية تخفي جهازاً.
- **وANSI معطّل:** تُقبل `87T` كما على `master`. فالملف لا يقرأ ANSI، ولا شيء يُخفى.
- **الحدّ:** إن أخذ الرقم 24 فئة في الجدول يوماً، صارت `24V` مرفوضة في أول تعديل وANSI مفعّل. فالإصدار يتغيّر، كما يقع اليوم حين تنمو أي قاعدة.
- **KKS بلا استثناء:** كل قراءة KKS، صالحة أو شبه صحيحة، ترفض التسمية ما دام KKS مفعّلاً. فتسمية جهد بشكل وسم KKS لا تقع عملياً.

**ما يحدث حين يفعّل الاكتشاف ANSI فوق تسمية مخزَّنة:**
1. ملف مشروع فيه التسمية `87T`، وANSI معطّل: مقبول.
2. وثيقة فيها 20 دليلاً على الأقل: يقترح الاكتشاف ANSI، و`with_detected` يُبقي التسمية، فالاكتشاف لا يقرأ التسميات.
3. **التأكيد بلا جسد** يخزّن الاقتراح `CONFIRMED` كما هو دون `parse_profile` (`documents.py:353-380`). وكذلك القبول الآلي (`inspection.py:817`).
4. **في INC‑1 لا ينكسر شيء:**
   - `narrowed` لا يستدعيه إلا `parse_profile` (`projects.py:73`، `:83-84`).
   - ولا يُحلَّل نص وثيقة بملف إعدادها المضيَّق بعد، فذلك STG‑05 في INC‑2.
5. **أول تعديل يحمل التسمية** (`PUT` أو تأكيد بجسد) يعطي 422 باسم `voltage_labels` برسالته الثابتة. فيحذف المستخدم التسمية أو يعطّل ANSI.
6. **في INC‑2:** يُتحقق من الاقتراح قبل تخزينه في الموضعين (§8 البند 7، §7 خ‑14).
7. **وفي الحالة الشائعة لا تعارض:** تسمية `24V` مع ANSI مكتشَف تبقى، لأنها ليست دليلاً.

**الاختبارات (وحدة):**
1. `test_electrical_f2_profile.py::test_voltage_labels_with_ansi_off_are_accepted_as_on_master`، مُعلَّم بـ`24V` و`48V` و`12VDC` و`87T`: `parse_profile` يقبلها، وتُخزَّن قانونية.
2. `test_api_electrical_router.py::test_a_supply_voltage_label_is_saved`: `PUT /projects/{id}/profile` بـ`{"body": {"schema": 1, "voltage_labels": ["24V"]}}` يعطي 200 وإصداراً جديداً.
3. `test_electrical_f2_profile.py::test_with_ansi_on_a_label_that_hides_an_ansi_device_is_refused`، مُعلَّم بـ`87T` و`50BF` و`27-1`، ومعها `"device_function_numbers": "ansi"` في الجسد نفسه: 422، و`detail` يبدأ بـ`voltage_labels`، ولا يرد فيه ما أُرسل.
4. `…::test_with_ansi_on_a_supply_label_is_accepted_and_read_as_a_voltage`، مُعلَّم بـ`24V` و`48V` و`12VDC`:
   - مقبول مع ANSI.
   - و`IdentifierParser(packaged.narrowed(profile.rule_selection())).parse("24V").kind is IdentifierKind.VOLTAGE`.
5. `…::test_with_kks_on_a_kks_shaped_label_is_refused`: `"plant_designation": "kks"` مع التسمية `20XYZ10AB123` يعطي 422.
6. `…::test_detection_can_enable_ansi_over_a_stored_label_and_the_next_edit_names_it`:
   - **المعطيات:** ملف أساسي من `parse_profile` بالتسمية `87T`، ثم `detect_profile` على `iec(120) + ansi_numbers(22)`.
   - **المتوقَّع:** الاقتراح ANSI، والتسمية فيه.
   - و`parse_profile(..., proposal.profile.to_body())` يرفع 422 باسم `voltage_labels`.
   - وبالتسمية `24V` بدلها: لا خطأ.
7. **اختبارات تسميات الجهد القائمة:** كما هي، بلا تعديل.

### 11.3 الرجوع والنشر المتدرّج (الملاحظة 3، وS‑2)

**الاختيار: الإجراء المكتوب في §4.1، ومعه قاعدة §11.1 وترتيب الاستبدال. ولا يُطفأ الاكتشاف.**
- **قاعدة §11.1 تقلّل المعنيّين** إلى وثائق فيها 20 دليلاً على الأقل. لكنها وثائق المورد الأول نفسها، فلا تكفي وحدها.
- **ترتيب الاستبدال يغلق نافذة النشر المتدرّج بلا مفتاح:**
  - `app` أولاً، ثم `worker-electrical`. فالاكتشاف لا يجري إلا في `worker-electrical`.
  - وبعد اكتمال `app` لا يبقى قارئ قديم.
  - ومفتاح يطفئ الاكتشاف إعداد نشر جديد لا يلزم.
- **قبل الرجوع:** المعنيّون يُعرَفون بمفاتيح الجسد في الإصدار الأحدث لكل مالك، ثم يُلحق لكلٍّ إصدار بلا المفاتيح، ثم تُستبدل الخدمتان بالترتيب العكسي.
- **جسد الـPR:** ينسخ §4.1 كما هو، ومعه رقما NFR‑F2‑4.

### 11.4 الالتباسان معاً (الملاحظة 4)

```python
def _near_miss_reasons(rest: str, rules: IdentifierRules) -> tuple[str, ...]:
    """Why a tag-shaped text misses the structure. A confusable letter where a digit belongs,
    a confusable digit where a letter belongs, both (each trial alone fits, or only the two
    swaps together fit: then both happened), or neither (malformed)."""
    spec = rules.kks.spec
    digits = "".join(digit for digit, _ in rules.confusables)
    letters = "".join(letter for _, letter in rules.confusables)
    if not digits:
        return (ISSUE_MALFORMED,)

    def fits(digit_extra: str, letter_extra: str) -> bool:
        return any(p.fullmatch(rest) for p in _patterns(spec, digit_extra, letter_extra))

    letter_in_digit = fits(letters, "")
    digit_in_letter = fits("", digits)
    if not (letter_in_digit or digit_in_letter) and fits(letters, digits):
        letter_in_digit = digit_in_letter = True  # only the two swaps together fit
    reasons = [
        issue
        for issue, held in (
            (ISSUE_LETTER_IN_DIGIT, letter_in_digit),
            (ISSUE_DIGIT_IN_LETTER, digit_in_letter),
        )
        if held
    ]
    return tuple(reasons) or (ISSUE_MALFORMED,)
```

- **لماذا تكون التجربة الثالثة شرطاً على فشل الأوليين:** إن كفى إبدال واحد، فالآخر لم يلزم، فلا يُذكر سببه.
- **قِستُ القاعدة على الفرع:**
  - `2OXYZ10A0123` و`2OX0Z1OAB12O`: السببان.
  - `2OXYZ1OAB12O`: الحرف في موضع الرقم وحده، كما اليوم.
  - `20XYZ10A0123`: الرقم في موضع الحرف وحده.
  - `20XY10AB123` و`20XYZ10AB12`: `kks.malformed`، كما اليوم.
- **الكلفة:** تجربة ثالثة على الوسم شبه الصحيح وحده، و`_patterns` مخبّأ.

**الاختبارات:**
- `test_electrical_shared_kks.py::test_f2_ac_5_both_confusions_together_name_both_reasons`: الحالات الست أعلاه بأسبابها وترتيبها، والشكل القانوني كما ورد.
- حالة M‑04 جديدة بمصدر `invalid`: `2OXYZ10A0123` مع `select.conventions = ["iec_81346", "kks"]`، والسببان.

### 11.5 `1X-25` (الملاحظة 5): استثناء مكتوب من `F2‑AC‑20`

- **القرار:** قرار المالك في 2026-11-09. المثال المخترع `1X-25` يبقى كما هو، في الشيفرة والحالات والاختبارات والوثائق وSRS ملحق أ، وعلى `master`.
- **السبب:** وسم طرف عام: الشريط `1X`، والطرف 25. لا يدل على مشروع ولا على مورد.
- **وثبت في `inc-1/f2/stories.md`:** `F2‑AC‑20` يسمّي الاستثناء الآن.
- **حدّه:** هذا الوسم وحده. وأي تطابق آخر في فرق الـPR فشل، ولو كان رمزاً عاماً، ويُعرض على المالك كما عُرض هذا.
- **توسيعه (قرار المالك، 2026-11-09، ر‑1 في إعادة المراجعة 1):** صار الاستثناء يشمل أيضاً خمسة أصناف من الرموز القياسية العامة، منها أمثلة هذا القسم (`16A` و`4P` و`24V` وغيرها). والنص الملزم في `stories.md` تحت `F2‑AC‑20`.
- **في هذه الإعادة:** لم أُدخل رمزاً حرفياً جديداً إلا ما في المهمة أو في `review.md` أصلاً (`16A`، `32A`، `25A`، `50A`، `4P`، `2P`، `1N`، `24V`، `48V`، `12V`، `12VDC`، `2OXYZ10A0123`، `2OX0Z1OAB12O`). وكيس `F2‑AC‑14` الجديد يعيد استعمال أربعة رموز فحصها `F2‑AC‑20`. وما سواها وصفتُه بنمطه، لا برموز.

### 11.6 الملاحظات الصغرى والأمنية

| # | القرار | التغيير الدقيق |
|---|---|---|
| 6 | يُصلَح الآن | الوثيقة النصية لـ`IdentifierRules.narrowed` (`rules/__init__.py:152-154`): «The subset a document's profile selects (device conventions, PLC families, patterns by NAME, extra voltage labels); KKS and ANSI are enabled by NAME. With conventions named, the result is ``(self.conventions \| ADDITIVE_CONVENTIONS) & profile.conventions``, so it may enable KKS or ANSI the receiver lacked: narrowing narrowed rules is not idempotent, and no caller chains it. A profile never carries a regex.» |
| 7 | يُصلَح الآن | `profile.py:364-383` (`_ansi_suffixes`): يبقى فحص العدد (`at most 64`). ثم لكل عنصر `(canonical,) = checked_ansi_suffixes((raw,))`، ويُحوَّل `RuleError` إلى `InvalidProfile(f"ansi_suffixes[{i}]", "must be 1..3 letters A-Z")` بـ`from None`. ويُحذف من `profile.py` ما لا يبقى له مستعمِل (`_ANSI_SUFFIX`، `MAX_ANSI_SUFFIX_RAW_CHARS`). **الأثر:** U+FE0F وU+034F وU+3164 وU+2800 صارت 422 بعد أن كانت تُطوى. ويبقى العرض الكامل والمسافة الداخلية يُطويان (`ＱZ` و`Q Z` → `QZ`، قِستُه). وتُطوى كذلك الجدولة وفاصل السطر (`\t\n\r\v\f`)، لأن `canonical_form` يعدّها مسافات (ر‑4 في `review.md`، إعادة المراجعة 1). والمخزَّن دائماً `[A-Z]{1,3}`. فتُحسم ملاحظة QA 2 وS‑3 بقاعدة واحدة، وتُعدَّل حالاتها في `tests/unit/test_electrical_f2_qa.py` إلى 422 |
| 8 | يؤجَّل إلى INC‑2 | §7 خ‑13: الحدّ موثَّق، ويُشتق الشكل الخارجي من الأقنعة مع أول صيغة مورد خارجه |
| 9 | يُصلَح الآن | `KksRules` يضيف `spec` و`shortest` بـ`field(init=False, repr=False, compare=False)`، ويحسبهما `__post_init__` بـ`object.__setattr__`. فلا يتغيّر من يبني `KksRules`. وينتقل الاسم المستعار `_Spec` إلى `systems.py` باسم `KksSpec`، ويستورده `kks.py` تحت `TYPE_CHECKING`. ويقرأ `kks.py` `rules.kks.spec` و`rules.kks.shortest` بدل `_spec()` و`_shortest()` المحذوفتين، ويبقى `_patterns` مخبّأً على `spec`. ثم يُعاد قياس NFR‑F2‑4 (`parse` و`tally`)، ويُسجَّل في جسد الـPR |
| 10 | يُصلَح الآن | `openapi.yaml:1857`، ومعه وصفا `basis` و`conventions`، كما في §3.12. والاختبار القائم لا يتغيّر |
| 11 | يؤجَّل إلى F3 | §7 خ‑12: يُقاس مع M‑30، ويُذكر في مخاطر INC‑2 (§8 البند 7) |
| S‑1 | يُصلَح الآن | `systems.py`: `MAX_OPTIONAL_PER_PART: Final = 2` و`MAX_OPTIONAL_PER_FORM: Final = 4`. في `_kks_parts`: عدد `n` و`a` في القناع ≤ 2، وإلا `RuleError(f"[kks.parts.{name}] at most {MAX_OPTIONAL_PER_PART} optional positions")`. وفي `_kks_forms`: مجموعها في أجزاء الصيغة ≤ 4، وإلا `RuleError(f"[[kks.forms]] {name!r}: at most {MAX_OPTIONAL_PER_FORM} optional positions")`. فأسوأ حالة 2⁴ = 16 توزيعاً، وقِيس 16 موضعاً كاملاً بـ0.9 ms. والقناع المعبّأ فيه موضع واحد. **اختبار الطفرة** `test_electrical_shared_rules.py::test_s1_optional_positions_are_capped`: `mask = "NNnnn"` يعطي `RuleError`؛ وصيغة من ثلاثة أجزاء `Nnn` و`Ann` و`Nnn` (ستة مواضع) تعطي `RuleError`؛ وعند الحدّ تماماً (2 لكل جزء، و4 للصيغة) يُقبل؛ والمعبّأ يُقبل |
| S‑2 | يُصلَح الآن، وجزء يؤجَّل | الإجراء في §4.1 وفي جسد الـPR. والقارئ المتسامح لـINC‑2 (§8 البند 7) |
| S‑3 | يُحسم بالملاحظة 7 | قاعدة واحدة: الرفض |
| S‑4 | يُصلَح الآن | `parser.py`، أول `_may_be_additive`: `if len(token) > MAX_TEXT_CHARS: return False`، وتعليقه: «parse refuses it before normalising, so does the probe (S-4)». **الاختبار** `test_electrical_shared_parser.py::test_the_probe_filter_checks_the_length_first`: `_may_be_additive("1" * MAX_TEXT_CHARS)` صادق، و`"1" * (MAX_TEXT_CHARS + 1)` كاذب، ورمز من ألف حرف U+FDFA كاذب. ولا اختبار زمن |
| S‑5 | يُصلَح الآن | `test_api_electrical_router.py::test_a_member_reads_and_previews_but_neither_registers_nor_changes_a_profile`: بعد PUT `member` بـ`plant_designation`، يعود إلى `owner` ويؤكد `env.get(f"/projects/{project['id']}/profile").json()["version"] == 1`، كما في اختبار `viewer` (`:1013-1014`) |

### 11.7 قائمة الإعادة للمطوّر (`backend-developer`)

على الفرع نفسه `feat/electrical-drawings-f2-rules`، وإيداع لكل بند أو بندين، والبوابات الست خضراء بعد كل إيداع. والترتيب: 1→2→3 متتابعة، و4..12 مستقلة بعدها.

1. **بيانات قاعدة الدليل وحدّ المواضع الاختيارية** (`identifiers.toml`، `rules/systems.py`):
   - `[kks] evidence_min_level = 2` و`[ansi] non_evidence_suffixes = ["A"]` بتعليقيهما.
   - الحقلان في `KksRules` و`AnsiRules`، والمفتاحان في `_KKS_KEYS` و`_ANSI_KEYS`، والتحقق.
   - و`MAX_OPTIONAL_PER_PART`/`MAX_OPTIONAL_PER_FORM` (S‑1).
   - والحقلان المحسوبان `spec` و`shortest` (الملاحظة 9).
   - الاختبارات: §11.1 البند 8، و§11.6 S‑1.
2. **دالتا الدليل والمسبار** (`ansi.py`، `kks.py`، `parser.py`):
   - `is_ansi_evidence` و`is_kks_evidence`، و`_is_evidence(found, rules)` في الموضعين.
   - وفحص الطول أول `_may_be_additive` (S‑4).
   - و`kks.py` يقرأ `spec` و`shortest`.
   - الاختبارات: §11.1 البندان 6 و7، و§11.6 S‑4.
3. **الحدّ في الاكتشاف** (`profile_detection.py`):
   - `_systems` كما في §11.1، والوثيقة النصية للوحدة.
   - الاختبارات: §11.1 البنود 1..5، ومعها كيس `F2‑AC‑14` الجديد واستبدال اختبار `F2‑AC‑15`.
4. **فحص تسميات الجهد** (`projects.py:83`، و`rules/__init__.py` في `_other_kind`):
   - كما في §11.2.
   - الاختبارات: §11.2 البنود 1..6.
5. **الالتباسان معاً** (`kks.py`، `_near_miss_reasons`):
   - كما في §11.4.
   - والاختبار، وحالة M‑04 الجديدة في `cases/identifiers.toml`.
6. **`_ansi_suffixes` بـ`checked_ansi_suffixes`** (`profile.py`):
   - الملاحظة 7.
   - وتعديل حالات `test_electrical_f2_qa.py` المعنية إلى 422.
7. **الوثيقة النصية لـ`narrowed`** (الملاحظة 6).
8. **أوصاف `openapi.yaml`** (الملاحظة 10، و§3.12).
9. **تأكيد الإصدار في اختبار `member`** (S‑5).
10. **إعادة قياس NFR‑F2‑4** بعد البنود 1..3 و5، بالسكربت نفسه خارج المستودع: `parse` بالقواعد المعبّأة وبالتفعيل، و`tally` على 1,000 رمز. ويُسجَّل الرقمان في جسد الـPR.
11. **جسد الـPR:**
    - §4.1 كما هو.
    - ورقما NFR‑F2‑4.
    - وسطر يذكر استثناء `1X-25` (§11.5).
    - وسطر يذكر أن `F2‑AC‑15` يُقرأ «لا يُقترح KKS» (§11.1).
12. **لا تغيير** في `.importlinter` ولا `tests/architecture/` ولا `inspection.py` ولا `documents.py` ولا `sql_repository.py`، ولا إعداد نشر.

### 11.8 ما أُجّل وأين سُجّل

| البند | إلى | أين سُجّل |
|---|---|---|
| الملاحظة 8 (`_OUTER` ثابت) | INC‑2، مع أول صيغة مورد خارج الشكل | §7 خ‑13 |
| الملاحظة 11 (`1NC` و`3PH` و`24V` أجهزة ANSI حين يُفعَّل) | F3، مع قياس M‑30 | §7 خ‑12، و§8 البند 7 |
| ضبط قاعدة الدليل (`evidence_min_level`، `non_evidence_suffixes`، جدول الفئات) | F3، بعد M‑32 | §7 خ‑11 |
| قارئ متسامح للجسد المخزَّن (S‑2) | LLD الزيادة INC‑2 | §4.1، و§8 البند 7 |
| التحقق من الاقتراح عند التأكيد بلا جسد والقبول الآلي | INC‑2 مع STG‑05 | §7 خ‑14، و§8 البند 7 |
| نسخ §4.1 إلى `docs/deploy-server.md` أو دليل التشغيل (S‑2) | أول نشر خادم، وهو قرار إنسان | هنا. ولا تعديل في `deploy-server.md` في هذا الـPR، لأنه لا نشر خادم الآن |

### 11.9 ما لم أحسمه، وملاحظات للمنسّق

- **لا ⚠️.**
  - لا شيء هنا يمسّ المصادقة ولا العزل ولا الأسرار ولا النشر ولا CI، ولا يضيف اعتمادية.
  - والاستثناء في `_other_kind` لا يرخي الرفض إلا لقراءة ANSI ليست دليلاً. ويبقى الرفض لكل ما يحرسه `INC1‑Q‑5` في اصطلاحات الأجهزة. وهو قاعدة صحة تسمية، لا ضابط أمن: القيمة المخزَّنة بالحروف المسموحة نفسها، ولا تُترجَم إلى تعبير.
  - والإجراء في §4.1 تشغيلي، يجريه إنسان عند نشر أو رجوع يقرّره إنسان.
- **نص `F2‑AC‑15` في `stories.md`:** لم أعدّله، لأن المهمة حصرت التعديل في `F2‑AC‑20`. والمقترح أن يصير: «… إذن لا يُقترح KKS، ويبقى الحقل على ملف الإعداد الأساسي، فلا يُقبل آلياً».
- **`F2‑AC‑20` على فرق الإعادة:** يُعاد الفحص بالقائمة نفسها. والرموز العامة الجديدة في §11.5 ليست في الاستثناء، فإن طابق أحدها صفحةً حقيقية فهو حالة `1X-25` نفسها، وتُعرض على المالك.
- **لم أتحقق منه:**
  - الزمن بعد التغييرات: يُقاس في البند 10 من §11.7.
  - استعلاما §4.1: لم أشغّلهما على قاعدة البيانات، وصحّتهما من نص الترحيل (`0001_electrical_drawings.py:255-285`، و`:164`، و`:190`).
  - سلوك `rolling-deploy.sh --service`: من رأس السكربت وخياراته وحدها.

</div>
