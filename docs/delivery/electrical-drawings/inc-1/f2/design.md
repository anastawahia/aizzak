<div dir="rtl">

# التصميم التقني: F2، البند 1، وسوم KKS وأرقام أجهزة ANSI وأطراف المورد الأول في `shared` وملف الإعداد

- **التاريخ:** 2026-11-08
- **الفرع:** `docs/electrical-drawings-f2` من `master` = `ab4da7f`. وهذه الجولة وثائق فقط. والبند 1 يُبنى بعد دمجها، على فرع شيفرة جديد من `master`.
- **المرجع:** `inc-1/f2/requirements.md` (§3.1 و§3.2 و§4 و§5 و§6، وA‑5)، و`inc-1/f2/stories.md` (US‑1..4 وUS‑10 وUS‑9)، و`docs/requirements.md` 0.20 (D‑36)، و`docs/hld.md` 0.19 (DEC‑20)، و`inc-1/f1/design.md` قالباً للشكل.
- **الحالة:** ✅ قرارات المالك في 2026-11-08: Q1..Q9 بالتوصية، وA‑5 بخلاف اقتراح المحلّل (أطراف `9A-99` في البند 1). وطُبّقت S1..S19 وH‑1..H‑12 في هذه الجولة (§10)، ونتائج التحقق في §10.4.
- **المستودع عام:** أنماط مجردة وأمثلة مخترعة فقط (`20XYZ10AB123`، `87T`، `1X-25`). لا وسم ولا اسم من العيّنات.
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
- **ما يُعدّ دليلاً على اصطلاح إضافي:** قراءة صالحة بلا `text.non_ascii`. ولـANSI قراءة بلا `alternatives`: لاحقة أو مثيل، لا رقم مجرد ولا مركّب. ولـKKS أي قراءة صالحة، ولو ببادئة (`=20XYZ10AB123` يُعدّ KKS في المسبار، ويبقى IEC في العدّ الأساسي، §7 خ‑7).
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

**`application/projects.py:82`:** يصير الفحص «تحت كل الاصطلاحات» `replace(selection, conventions=frozenset(Convention))`، لا `frozenset()`. فقبل F2 كان الفارغ يعني الكل. وبعده يعني اصطلاحات الأجهزة وحدها، فلو بقي لقبلت تسمية جهد مثل `87T` تُخفي جهاز ANSI. ولا تغيير غيره في `projects.py`.

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
| `7XY123` | أيّ | لا معرّف | — |
| `1X-25` | IEC 81346 وحده | طرف IEC (A‑5) | — |

- **البدائل لهذه الأزواج وحدها، وبلا `hint` فقط.** والالتباسات القديمة التي يحسمها الترتيب (`12.3` مرجع أو سلك، و`13` سلك أو نقطة اتصال) لا تحمل بدائل، فلا يتغيّر شكل أي حالة قائمة.
- **حساب البدائل** في `IdentifierParser._alternatives(found, forms)`. وهو يستدعي `is_bare_ansi_number` و`read_compound` و`parse_cross_reference` و`parse_reference_designation` على النص نفسه، ولا يقرأ إلا ما تفعّله القواعد.

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
- **الحقلان الجديدان،** لكل منهما `n = tally.conventions.get(c, 0)`:
  - `n > 0`: يُقترح المفعَّل (`kks` أو `ansi`)، وثقته `_sample_factor(n)`. فهي صفر تحت 20، فلا قبول آلي (`F2‑AC‑15`، على قاعدة `MIN_IDENTIFIERS`، `profile_detection.py:42`).
  - `n = 0`: يبقى ما في ملف الإعداد الأساسي، بثقة 1.0، كبقية الحقول بلا بيّنة (`profile_detection.py:13-15`).
- `field_confidence` يحمل `"plant_designation"` و`"device_function_numbers"`. و`confidence` أدناها كما اليوم. فوثيقة بلا KKS ولا ANSI لا تتغيّر ثقة اقتراحها.
- `basis["systems"]`: مثل `("kks:25", "ansi:22")`، للموجود منهما وحده. ويبقى `basis["tokens"]` لاصطلاحات الأجهزة.
- **`_SYMBOL_OF` لا يتغيّر:** أربعة مداخل لاصطلاحات الأجهزة، ولا يُستنتج معيار رموز من KKS ولا ANSI. ويُضاف اختبار يثبّت أن مفاتيحه `DEVICE_CONVENTIONS`.

### 3.11 تقرير التوصيف (`drawings/domain/characterization.py`)

- **سجلّ الوثيقة:** `conventions=dict(ranked_conventions(tally.conventions))` كما اليوم، فيحمل عدد KKS وANSI تلقائياً. و`symbol_standard_guess` يُشترط فيه `ranked_device_conventions(...)` لا الكل. فوثيقة فيها KKS وحده لا تدّعي معيار رموز، على سابقة `F0‑AC‑6`.
- **المجمّع:** `conventions` لاصطلاحات الأجهزة كما اليوم (الوثائق باصطلاحها المقترح، الأول بينها). ولكل من `kks` و`ansi`: عدد الوثائق التي فيها 20 منه على الأقل (`MIN_IDENTIFIERS`). ويقول وصف `openapi.yaml` ذلك (§3.12).
- **الإصدارات:** السجل الجديد يحمل `shared` = `1.3.0` و`rules` = `1.1.0`، والمجمّع يسرد كل إصدار رآه (`characterization.py:182-186`). فمجموعة نصفها قبل البند 1 تقول ذلك (§4).
- الشكل المخزَّن والـDTO لا يتغيّران.

### 3.12 العقد: `openapi.yaml` و`06-domain-models.md` واختبارهما

**`docs/design/openapi.yaml`، الأوصاف وحدها:**
- `DrawingProfileIn.body` (`openapi.yaml:1735`): بعد `identifier_convention (...)` يُضاف: «plant_designation (none, kks; default none) and device_function_numbers (none, ansi; default none), chosen independently of identifier_convention: KKS tags or ANSI device numbers beside one device convention are not mixed (SRS D-36); ansi_suffixes (extra ANSI suffix letters, at most 64, each 1..3 letters A-Z; default empty). A returned body omits these three while they hold their defaults.»
- `ProfileProposalOut.field_confidence` (`openapi.yaml:1750`): يُضاف `plant_designation` و`device_function_numbers` إلى القائمة. و`basis`: يُضاف `systems`.
- `CharacterizationOut.conventions` (`openapi.yaml:1856`): وصف جديد: «Identifiers per convention; kks and ansi count the unambiguous KKS tags and ANSI device numbers.»
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
- **التوافق:** غير كاسر للأمام. و**الرجوع** بإلغاء الـPR. قبل الرجوع يُلحق لكل مشروع أو وثيقة فعّل KKS أو ANSI أو لواحق ANSI إصدارٌ جديد لملف إعداده بلا هذه الحقول (`PUT …/profile`)، لأن الشيفرة القديمة ترفض جسداً فيه مفتاح لا تعرفه (`profile.py:137-141`). وما سواه يبقى مقروءاً، لأن جسده لا يحمل الحقول.

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
| `F2‑AC‑15` | وحدة | `…::test_f2_ac_15_under_twenty_tags_kks_has_zero_confidence` (19 وسماً: الحقل `kks` بثقة 0، والاقتراح بثقة 0) |
| `F2‑AC‑16` | وحدة على الموجّه | `test_api_electrical_router.py::test_f2_ac_16_the_new_fields_are_saved_validated_and_defaulted`: PUT بقيم صحيحة إصدار جديد؛ وقيمة مجهولة لكل حقل، و`identifier_convention: "kks"`، و`ansi_suffixes: [".*"]`، و`["("]`، و`["ABCD"]`، و65 لاحقة: 422 `electrical.invalid_profile` يبدأ `detail` فيه باسم الحقل، ولا يرد فيه ما أُرسل؛ وجسد قديم بلا الحقول: GET يعيد جسداً بلا المفاتيح الثلاثة، و`from_body` عليه يعطي `none` و`none` و`()`. ومعه في `test_electrical_f2_profile.py` اختبار نطاق: جسد افتراضي وبصمته بايتاً ببايت كما على `master` (ثابت مثبّت في الاختبار) |
| `F2‑AC‑17` | وحدة | `test_api_electrical_router.py::test_a_member_reads_and_previews_but_neither_registers_nor_changes_a_profile` و`…::test_a_viewer_reads_and_nothing_more`: يُضاف إلى كلٍّ PUT بجسد فيه `plant_designation: "kks"`، والجواب 403 ولا إصدار جديد. وصفوف `EXPECTED` في `test_api_rbac.py` لا تتغيّر، لأن المسارات لم تتغيّر |
| `F2‑AC‑18` | وحدة وعقد | اختبار العقد القائم كما هو؛ و`test_the_profile_body_description_names_every_profile_field` (§3.12)؛ و`test_electrical_shared_rules.py::test_the_rules_file_is_packaged_and_versioned` بـ`1.1.0` و`1.3.0`؛ و`test_electrical_f2_profile.py::test_f2_ac_18_the_release_fingerprint_moves_with_the_versions` (سجلّ `characterize` يحمل الإصدارين الجديدين، وبصمة ملف الإعداد نفسه لا تتغيّر) |
| `F2‑AC‑19` | تقييم ووحدة | حالات `appendix-a` في `cases/identifiers.toml` لأمثلة FR‑ID‑15 وFR‑ID‑16 و`1X-25`؛ و`_APPENDIX_A` في `test_electrical_shared_parser.py` يذكرها، ويذكر الاختبار القائم `test_every_appendix_a_example_is_in_the_case_set` كلَّ مثال؛ و`python -m tests.eval.electrical.m04_identifiers --out …` يعطي 1.0. والحالة الفاشلة تُفشل `test_electrical_shared_parser.py` في CI، كما اليوم |
| `F2‑AC‑20` | يدوي | المراجع يبحث في فرق الـPR بقائمة الوسوم الحقيقية المحفوظة خارج المستودع، والنتيجة 0 (NFR‑F2‑7). وفي هذه الجولة: §10.4 |
| `F2‑AC‑41` | وحدة | `test_electrical_shared_terminals_wires.py::test_f2_ac_41_a_digits_first_x_strip_reads_under_iec_and_gbt` |
| `F2‑AC‑42` | وحدة | `…::test_f2_ac_42_the_chinese_reading_and_the_xt_rule_stay` (دون تضييق، وIEC وحده، وNFPA وحده) |
| `F2‑AC‑43` | وحدة | `test_electrical_shared_rules.py::test_f2_ac_43_digits_first_conventions_are_data` (بلا الجدول: صيني وحده؛ ورمز مجهول، واصطلاح مجهول، وقائمة فارغة: `RuleError`؛ وتعديل الجدول وحده يغيّر القراءة) |

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

**لا قرار ⚠️ في هذا التصميم.** حسم المالك القرارات كلها (Q1..Q9 وA‑5). ولا يمسّ التصميم الأمن ولا المصادقة ولا العزل بين المستأجرين ولا النشر ولا CI، ولا يضيف اعتمادية. فما يلي اختيارات تصميم ومخاطر بمعالجتها:

| # | الخطر أو الاختيار | المعالجة |
|---|---|---|
| خ‑1 | تغيير الترتيب يغيّر ناتجاً قائماً بصمت | KKS وANSI لا يقرآن شيئاً إلا بالاختيار. و`F2‑AC‑7` يقارن كل حالة قائمة بهما مفعّلين ومعطّلين، ويسرد ما يتغيّر بـANSI بالاسم |
| خ‑2 | الرجوع، والنشر المتدرّج (`deploy/rolling-deploy.sh`): نسخة قديمة تقرأ جسد ملف إعداد كتبته الجديدة | `to_body` يحذف الحقول الافتراضية (§3.8)، فلا ترى القديمة مفتاحاً جديداً إلا في ملف فعّل KKS أو ANSI. وقبل الرجوع يُلحق لهذه الملفات إصدار بلا الحقول (§4). وفي النشر المتدرّج لا يُفعَّل KKS ولا ANSI حتى تكتمل النسخ |
| خ‑3 | المسبار يضاعف التحليل في `tally` | التحليل في `tally` على رموز الصفحة المأخوذة عيّنةً، لا على كل نص. ويُقاس مع NFR‑F2‑4 ويُسجَّل. وإن تجاوز الحدّ يقصر المسبار على الرموز التي تبدأ برقم، فـKKS وANSI لا يبدآن إلا به |
| خ‑4 | رموز قليلة بشكل KKS أو ANSI تُبقي الاقتراح بثقة 0 | على قاعدة `MIN_IDENTIFIERS` نفسها (`F2‑AC‑15`). وكل وثيقة اليوم تنتظر تأكيد إنسان أصلاً (INV‑E5). ويُضبط العامل مع العتبات بعد F3 (`INC1‑R‑15`) |
| خ‑5 | رموز البادئات والحروف المستثناة ومعنى الخانات الأربع لم يُتحقق منها من نص الإرشادات (A‑2، A‑3، وA‑8 أدناه، RSK‑28) | كلها بيانات في الملف. ويُتحقق منها في F3 على الحقيقة المرجعية، وتُعدَّل بالملف وحده |
| خ‑6 | جدول ANSI يقترب من نص المعيار | فئات عامة بأسمائنا، وأكثر من رقم في فئة، ولا وصف. ويحرسه `F2‑AC‑12` وتحقق المفاتيح، ويراجعه المراجع (NFR‑LIC‑05) |
| خ‑7 | الوسم ببادئة `=` يُعدّ IEC في العدّ الأساسي وKKS في المسبار | لا يغيّر الخلط ولا اصطلاح الأجهزة إلا في وثيقة تكثر فيها الوسوم ببادئة. ويُرى في M‑32 بعد F3. ولو ظهر، يُستثنى ما عدّه المسبار KKS من عدّ IEC |
| خ‑8 | اختيار: حقل ثالث `ansi_suffixes` في ملف الإعداد، وQ3 ذكر حقلين | يطلبه نص `FR‑ID‑16` («تُقبل لواحق إضافية للمشروع من ملف الإعداد») و`F2‑AC‑11`، وكلاهما أقرّه المالك. وهو المكان الذي تكتب فيه الأسطورة المؤكَّدة لواحقها في INC‑2. والاختيار إضافي ومحدود (64) ولا يُترجَم إلى تعبير |
| خ‑9 | اختيار: مفاتيح بحث KKS بلا حذف للأصفار البادئة | المواضع ثابتة العدد، فالحذف يجمع وسمين مختلفين في مفتاح. والمفتاح للاسترجاع وحده (S‑6)، ويعيد INC‑8 النظر فيه إن قاسه |
| خ‑10 | اختيار: `[confusables]` في ملف القواعد الآن، وFR‑ID‑18 في INC‑2 | يسمّي سبب عدم الصلاحية في `F2‑AC‑5` دون أن يصحّح شيئاً. ويقرؤه التصحيح في INC‑2، فتبقى قائمة الأزواج واحدة |

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

</div>
