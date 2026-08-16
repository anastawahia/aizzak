<div dir="rtl">

# خطّة الوحدات (Spaces) — الباك-اند

> **Spaces Backend Plan** · تقسيم مساحة العمل إلى وحدات داخليّة، كلٌّ منها يملك ملفّاته ومحادثاته
>
> الوحدة **ليست مستأجرًا جديدًا**: مساحة العمل تبقى الحدّ الأمنيّ الوحيد، وRLS تبقى على `app.workspace_id` وحدها. الوحدة **محور ملكيّة داخل المستأجر** — تُرشَّح في الاستعلام، لا في سياسة القاعدة.

| | |
|---|---|
| **أُنشئت** | 2026‑08‑16 |
| **الأساس** | `f2c4463` (‏`master`) · المكدّس الحاويّ يعمل (14 خدمة صحّية) |
| **النطاق** | وحدة `spaces` جديدة · أعمدة `space_id` في `files`/`conversations`/`knowledge` · حصّة 1 GiB لكلّ وحدة · تقسيم Qdrant ببطاقة `space` + فهرس `is_tenant` · حذف متسلسل · مسارات HTTP · العقود والوثائق |
| **خارج النطاق** | تعدّد مساحات العمل · مشاركة وحدة بين مستخدمين · نقل ملف بين الوحدات · حصّة على مستوى مساحة العمل · أي تغيير في RLS |
| **الحالة** | 🚧 **2/16** — الخطوتان ١ ([§3.139](log/3.139.md)) و٢ ([§3.140](log/3.140.md)) مغلقتان |

---

## 0. قاعدة التنفيذ

منقولةٌ من [`stream-topology-plan.md`](stream-topology-plan.md) §0 و[`deferred-adapters-plan.md`](deferred-adapters-plan.md) §0.

**خطوة واحدة في كلّ مرّة، ثمّ توقّف.** كلّ خطوة ملزَمة بـ:

1. تنفيذ **مهمّتها وحدها** — ما خرج عنها يُسجَّل في §7 ولا يُنفَّذ.
2. **البوّابات الخمس** قبل إعلان الانتهاء:
   `ruff format --check` · `ruff check` · `mypy src` · `lint-imports` · `pytest`
3. **برهان الفشل على الحالة القديمة بآليّة العطب نفسها** — لا بخطأ جمعٍ ولا `ImportError`.
4. مدخلة سجلّ في `docs/log/` بالرقم التالي، مربوطة في `log/INDEX.md` و`log/CHANGELOG.md`.
5. تحديث صفّها في §4.

---

## 1. القرارات المُقفلة

أُقرّت في جلسة 2026‑08‑16 ولا تُعاد مناقشتها داخل هذه الخطّة.

| # | السؤال | القرار |
|---|---|---|
| ١ | ما الذي تراه محادثة الوحدة؟ | **جميع ملفّات وحدتها** — ولا شيء خارجها |
| ٢ | حذف وحدة؟ | **حذف متسلسل** لكلّ ملفّاتها ومحادثاتها وفهرسها |
| ٣ | نقل ملف بين الوحدات؟ | **لا** |
| ٤ | حدّ الملفّات؟ | **1 GiB لكلّ وحدة** (حجمًا، لا عددًا) |
| ٥ | ميزة التثبيت (`conversation_files`)؟ | **تبقى** — تضييقٌ اختياريٌّ *داخل* الوحدة |

### 1.1 النموذج

```
workspace  (مستأجر — حدّ أمنيّ · RLS)
   └── space  (محور ملكيّة — ترشيح في الاستعلام)
         ├── files          ← ملكيّة · حصّة 1 GiB
         └── conversations  ← ملكيّة
               └── pins     ← تضييق اختياريّ داخل ملفّات الوحدة نفسها
```

**مستويان لا يتصادمان:** الوحدة تقرّر **ما تراه** المحادثة؛ التثبيت يقرّر **ما تسأل عنه** هذه المرّة.

### 1.2 ما ترتّب على القرار ١

لا يوجد «مستوى مشترك» فوق الوحدات ⇒ كل ملف وكل محادثة يخصّ وحدةً بالضبط ⇒

| كان في التحليل الأوّل | صار |
|---|---|
| `space_id uuid NULL` | **`NOT NULL`** — لا حالة يتيمة |
| `MatchAny(["workspace", id])` | **`MatchValue(id)`** — شرط واحد |
| ثلاثة مستويات رؤية | مستويان |

---

## 2. ما أثبته الاستكشاف

فحصٌ فعليٌّ على `f2c4463`، لا استنتاج.

| # | النتيجة | الدليل |
|---|---|---|
| ٢‑أ | محوّل Qdrant يترجم القيمة المفردة إلى `MatchValue` والقائمة إلى `MatchAny`، ويجمع المفاتيح بـ`must` (AND) | [`qdrant_store.py:162‑203`](../src/app/infrastructure/vector/qdrant_store.py#L162) |
| ٢‑ب | **لا يوجد فهرس بطاقة واحد في المشروع كلّه** — لا على `workspace_id` ولا على `document_id`. `create_payload_index` غير مستدعاة إطلاقًا | `grep -rn "create_payload_index\|is_tenant" src/` ⇒ صفر |
| ٢‑ج | النشرة تدعم `is_tenant`: خادم `qdrant/qdrant:v1.13.4` · عميل `qdrant-client 1.18.0` · `KeywordIndexParams` و`UuidIndexParams` كلاهما يعرض `['type','is_tenant','on_disk','enable_hnsw']` | فحص حيّ بـ`.venv/bin/python` |
| ٢‑د | `ensure_hybrid_collection` تخرج فورًا إن كان الصندوق موجودًا ⇒ **إضافة إنشاء الفهارس داخلها لا يطال الصناديق القائمة** | [`qdrant_store.py:288`](../src/app/infrastructure/vector/qdrant_store.py#L288) |
| ٢‑هـ | `_build_filter` يبني `must` فقط — **لا `should` ولا `IsEmpty`** ⇒ نقطة تفتقد مفتاح `space` لا يطابقها أيّ مرشِّح، بلا خطأ | [`qdrant_store.py:180`](../src/app/infrastructure/vector/qdrant_store.py#L180) |
| ٢‑و | `app.ops.purge` **لا يصلح** لكنس مساحة عمل حيّة: شرط أهليّته «محذوفة ومنتهية مدّة الاحتفاظ» | [`purge.py:252`](../src/app/ops/purge.py#L252) |
| ٢‑ز | ملفّ العقود **يطلب صراحةً** إلحاق أيّ وحدة جديدة بعقد الاستقلال | [`.importlinter:78`](../.importlinter#L78) |
| ٢‑ح | `FilesAccess.get_readable` **غير مقيّد بأيّ نطاق** — مسار تسريب قائم | `framework/agent_runtime/deps_ports.py` |
| ٢‑ط | `knowledge.chunks` تحمل `point_id`، و`files.files` تحمل `storage_key` ⇒ الحذف المتسلسل **لا يحتاج منفذًا جديدًا** | ترحيلات `knowledge/0001` و`files/0001` |
| ٢‑ي | الحدّ الحالي `max_files_per_workspace = 10_000` **عدد لا حجم** | [`settings.py:333`](../src/app/framework/settings/settings.py#L333) |

> **٢‑ب و٢‑ج معًا فائدةٌ مجّانيّة:** إنشاء فهرس على `workspace_id` يُسرّع النظام **اليوم**، قبل أيّ عمل على الوحدات.

---

## 3. التصميم

### 3.1 وحدة `spaces` — بنية مصغّرة من `files`

```
src/app/modules/spaces/
├── domain/       entities.py (Space) · value_objects.py (SpaceName) · errors.py · events.py
├── application/  use_cases.py (Create · Rename · List · Get · Delete · SpacesQueryService · حزمة SpaceUseCases)
├── ports/        inbound.py (SpacesQuery + SpaceView) · repository.py (SpaceRepository)
└── adapters/     sql_repository.py
```

> 📌 **تصحيحٌ من التنفيذ ([§3.139](log/3.139.md)):** المنفذ الوارد لِمَن **يربط به غيرُك** — `SpacesQuery`/`SpaceView` على سابقة `FilesQuery` حرفيًّا، وهو ما تحتاجه `files`/`conversations` لتُثبت أنّ `space_id` القادم في الطلب يعني شيئًا. أمّا حزمة `SpaceUseCases` فمكانها التطبيق حيث تسكن `FileUseCases`، لأنّ `10 §3` يجعل طبقة الواجهة تستهلك حالات استخدام الوحدة مباشرةً.

**لا تستورد أيّ وحدة أخرى، ولا تستوردها أيّ وحدة أخرى.** `files` و`conversations` تحملان `space_id` **كمعرّف معتِم** — رقمٌ تحفظه وترشّح به ولا تعرف ماذا يعني — وتتحقّقان منه عبر Protocol تُعلنه كلٌّ منهما لنفسها ويُربط في جذر التركيب. هذا **عكس** [`conversations/ports/files.py`](../src/app/modules/conversations/ports/files.py) القائم: نفس النمط، لا سابقة جديدة.

### 3.2 قاعدة البيانات

```sql
CREATE TABLE spaces.spaces (
  id           uuid PRIMARY KEY,
  workspace_id uuid NOT NULL,
  name         text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
  created_by   uuid,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz NULL,
  version      integer NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX ux_spaces_ws_name
  ON spaces.spaces(workspace_id, lower(name)) WHERE deleted_at IS NULL;

CREATE TRIGGER trg_touch BEFORE UPDATE ON spaces.spaces
  FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();

ALTER TABLE spaces.spaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE spaces.spaces FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON spaces.spaces
  USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
  WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
```

القالب حرفيًّا من [`files/0001_files.py`](../migrations/versions/files/0001_files.py)، بصيغة `NULLIF` المُقسّاة.

> 📌 **تصحيحٌ من التنفيذ ([§3.140](log/3.140.md)):** «المخطّط» في الخطوة ٢ **ليس** في `spaces/0001_spaces.py` بل في مراجعة منصّةٍ جديدة [`platform/0004_spaces_schema.py`](../migrations/versions/platform/0004_spaces_schema.py). السبب بنيويّ: `alembic` يُنشئ `spaces.alembic_version` **قبل** أن ينفّذ أوّل `upgrade()`، فالسلسلة لا تستطيع أن تُنشئ المخطّط الذي يسكنه جدولُ دفاترها؛ و`MODULE_SCHEMAS` في مراجعة الأساس مُقفلٌ لأنّها مُطبَّقةٌ على كلّ قاعدةٍ قائمة. وللسبب نفسه انتقلت ثلاثةٌ من بنود الخطوة ٣ إلى الخطوة ٢ — حارسا [`test_ops_provision.py`](../tests/unit/test_ops_provision.py) يرفضان جدولًا بلا منحٍ وسلسلةً بلا خطوة.

**RLS تبقى على `workspace_id` وحده — قرارٌ مقصود.** مساحة العمل تُشتقّ من **هويّة** المستخدم بعد المصادقة؛ الوحدة يختارها **الطلب**. وضع قيمة يختارها الطلب في متغيّر أمنيّ لا يضيف أمنًا — التطبيق هو من يضبطها أصلًا — ويحوّل خطأ ترشيح عاديًّا إلى ثغرة صامتة. الترشيح بالوحدة مكانه `WHERE` المستودع.

الأعمدة المضافة:

| الجدول | العمود | الفهرس |
|---|---|---|
| `files.files` | `space_id uuid NOT NULL` | `ix_files_space` جزئيّ على `deleted_at IS NULL` |
| `conversations.conversations` | `space_id uuid NOT NULL` | `ix_conv_space` جزئيّ |
| `knowledge.documents` | `space_id uuid NOT NULL` | `ix_kndoc_space` جزئيّ |

`knowledge.documents` تحمله أيضًا كي يعمل الحذف المتسلسل والسرد بلا استعلام مُتقاطع بين الوحدات.

**لا مفاتيح خارجيّة عبر المخطّطات** — القاعدة القائمة في `conversation_files` (`file_id` مرجعٌ منطقيّ بلا FK).

### 3.3 الحصّة — أدقّ نقطة في الخطّة

حدّ جديد في [`settings.py`](../src/app/framework/settings/settings.py) `Limits`:

```python
max_space_bytes: int = 1_073_741_824   # 1 GiB — حصّة الوحدة الواحدة
```

والفحص في `RegisterUpload`، **قبل توقيع رابط الرفع** لا بعده:

```
1. SELECT id FROM spaces.spaces WHERE id = :space FOR UPDATE     ← مرساة القفل
2. SELECT COALESCE(SUM(size_bytes), 0) FROM files.files
     WHERE space_id = :space AND deleted_at IS NULL
3. if used + size_bytes > max_space_bytes:
       raise QuotaExceeded → spaces.quota_exceeded (409)
4. INSERT + presign_put
```

**لماذا `FOR UPDATE`؟** بدونه، رفعان متزامنان بحجم 600 MiB لكلٍّ منهما يقرآن المجموع نفسه ويمرّان معًا، فتحمل الوحدة 1.2 GiB. القفل على صفّ الوحدة يُسلسل التسجيل ويجعل الحدّ حقيقيًّا لا تقريبيًّا.

**السطر ١ يقفل صفًّا في `spaces` والسطر ٢ يقرأ `files`** — والوحدتان لا تتعارفان. لذا التسلسل يعيش في **خدمة تنسيق** عند جذر التركيب (سابقة `CompleteUploadService`)، لا داخل وحدة.

> **قيدٌ قائمٌ لا يزيده هذا التعديل:** الحجم المفحوص هو المُعلَن من العميل. التحقّق الحقيقيّ في `CompleteUpload` (الحجم والبصمة والنوع). عميلٌ يكذب يُرفض عند الإتمام — لكن بايتاته وصلت MinIO بالفعل. سلوكٌ موجودٌ اليوم.

### 3.4 Qdrant

| البند | القرار |
|---|---|
| عدد الصناديق | **واحد لكلّ مساحة عمل** — `kn-<workspace_id>` بلا تغيير |
| مفتاح البطاقة | `payload["space"] = space_id` |
| مرشِّح الاسترجاع | `flt["space"] = space_id` (قيمة مفردة ⇒ `MatchValue`) |
| مع التثبيت | `flt["document_id"] = [...]` تُجمع بـAND فوق شرط الوحدة |
| فهارس البطاقة | `space` بـ**`is_tenant=True`** · `workspace_id` عاديّ · `document_id` عاديّ |

**لماذا صندوق واحد؟** لأن البديل — صندوق لكلّ وحدة — يدفع خمسة أثمان: تكرار الملفّات المشتركة أو دمجًا مكسورًا إحصائيًّا، وتدهور IDF (إحصائيّة تُحسب لكلّ صندوق على حدة، وهي تحمل نصف وزن الدمج: `_W_BM25 = 0.5`)، وانفجار عدد الصناديق، ونقل الإنشاء إلى المسار الساخن، وموت ميزة التثبيت. `is_tenant` يشتري ميزة الأداء الوحيدة التي كان يقدّمها، بلا أيّ من الخمسة.

**`is_tenant` هنا في أفضل حالاته:** عشرات الوحدات ⇒ إعادة ترتيب فيزيائيّ حقيقيّ للنقاط على القرص. (لو كان النطاق محادثةً لصارت الآلاف، فتفتّت التخزين بدل أن ترتّبه — وهذا سببٌ إضافيّ لاختيار الوحدة نطاقًا.)

> ⚠️ **لا تضبط `m=0`** رغم ما توصي به وثائق Qdrant مع `is_tenant`. تلك التوصية تفترض أن كلّ بحث مقيّد بمستأجر واحد بقيمة واحدة. صحيحٌ أن مرشِّحنا كذلك اليوم، لكن إطفاء الرسم البيانيّ العامّ يجعل أيّ استرجاعٍ غير مقيّد مستقبلًا (تلخيصٌ عابرٌ للوحدات، أداة تشخيص) مستحيلًا لا بطيئًا. الفائدة ذاكرةٌ فقط؛ الثمن بابٌ مغلقٌ بلا رجعة.

المنفذ يكسب دالّة واحدة في [`framework/ports/vector_store.py`](../src/app/framework/ports/vector_store.py):

```python
async def ensure_payload_index(
    self, collection: str, field: str, *, tenant: bool = False
) -> None: ...
```

تُستدعى من `ensure_hybrid_collection` — **وانتبه للنتيجة ٢‑د**: الصناديق القائمة تخرج من الدالّة قبل الوصول إليها، فتحتاج مهمّة تشغيليّة لمرّة واحدة (§5).

### 3.5 التثبيت — قاعدة تكامل جديدة

بقاء التثبيت يفرض شرطًا لم يكن موجودًا: **الملفّ المُثبَّت يجب أن يكون في وحدة المحادثة نفسها.**

`PinConversationFile` يتحقّق اليوم عبر `self._files.get_readable(ctx, file_id)` — وهي غير مقيّدة (النتيجة ٢‑ح). فيتوسّع منفذ `conversations/ports/files.py`:

```python
class ReadableFile(Protocol):
    @property
    def file_id(self) -> str: ...
    @property
    def space_id(self) -> str: ...      # ← جديد
```

ويرفض التثبيت بـ`409` إن اختلفت الوحدة.

**فشلٌ آمنٌ حتّى لو أُهمل هذا:** مرشِّح الاسترجاع يجمع `space` و`document_id` بـAND، فمستندٌ من وحدة أخرى يسقط في شرط الوحدة ولا يعود أبدًا. لكن السقوط الصامت ليس رفضًا — والمستخدم يستحقّ خطأً لا نتيجةً فارغة.

### 3.6 الحذف المتسلسل — أين يعيش

الحذف يعبر خمس وحدات، ولا وحدة تعرف الأخرى. السابقة موجودة: [`app/ops/purge.py`](../src/app/ops/purge.py) يفعل هذا لمساحة عمل كاملة عبر Qdrant وMinIO وPostgres.

⇒ **خدمة تنسيق عند جذر التركيب**، لا دالّة في وحدة:

```
DeleteSpaceService.execute(ctx, space_id):
  1. spaces        → soft-delete صفّ الوحدة            ← تختفي فورًا من الواجهة
  2. knowledge     → اجمع point_id من chunks لمستنداتها
  3. Qdrant        → vectors.delete(collection, point_ids)
  4. knowledge     → احذف summaries · summary_jobs
                            · reindex_job_items · reindex_jobs
                            · chunks · documents
  5. files         → اجمع storage_key ثمّ storage.delete(key) لكلٍّ منها
  6. files         → احذف الصفوف
  7. conversations → احذف messages · conversation_files · conversations
```

**لا شيء هنا يحتاج منفذًا جديدًا** (النتيجة ٢‑ط).

**الترتيب مقصود:** الخطوة ١ أوّلًا حتّى لا يرى المستخدم وحدةً نصف-محذوفة لو تعثّر ما بعدها. وكلّ خطوةٍ تالية idempotent — حذف نقطة غير موجودة لا شيء، وحذف كائن MinIO غائب يعيد 204، و`DELETE` بلا مطابقات ينجح — فإعادة التشغيل آمنة.

**مفاتيح MinIO تبقى `<workspace_id>/<file_id>`** بلا تعشيش بالوحدة. الحذف يمرّ على المفاتيح التي تعرفها القاعدة بالاسم، وهو أدقّ من الحذف ببادئة: لا يمسّ إلّا ما سجّلته القاعدة. ولو كبرت الأعداد لاحقًا تُضاف دالّة حرّة `delete_keys(client, bucket, keys)` على غرار [`delete_prefix`](../src/app/infrastructure/storage/minio_storage.py#L225) — **لا** منفذًا، لأن منفذًا قادرًا على الحذف الجماعيّ يصير متاحًا لكلّ وحدة عبر الحقن.

### 3.7 واجهة HTTP

| الطريقة | المسار | ملاحظة |
|---|---|---|
| `GET` | `/api/v1/spaces` | + `bytes_used` · `file_count` · `conversation_count` |
| `POST` | `/api/v1/spaces` | 201 |
| `PATCH` | `/api/v1/spaces/{id}` | إعادة تسمية |
| `DELETE` | `/api/v1/spaces/{id}` | 204 · حذف متسلسل · `Idempotency-Key` |

تعديلات على القائم:

| المسار | التعديل |
|---|---|
| `GET /files` · `GET /conversations` · `GET /knowledge/documents` | `?space_id=` **إلزاميّ** |
| `POST /files` · `POST /conversations` | `space_id` في الجسم |
| `POST /conversations/{id}/files` | يرفض ملفًّا من وحدة أخرى (§3.5) |

وصلاحيّتان في تعداد RBAC: `SPACES_READ` · `SPACES_WRITE`.

**«main space» تُنشأ من الخادم**، على غرار توفير مساحة العمل عند أوّل دخول: أوّل `GET /spaces` يجد وحدةً جاهزة. وضع ذلك في الواجهة يعني أن كلّ عميل قد يصنع واحدة، وأن الوكلاء والعمّال لن يجدوها أصلًا.

---

## 4. الخطوات

| # | الخطوة | يمسّ | الحالة |
|---|---|---|---|
| ١ | وحدة `spaces` (domain · application · ports · adapters) + اختباراتها | جديد | ✅ [§3.139](log/3.139.md) |
| ٢ | ترحيل `spaces/0001_spaces.py` — مخطّط · جدول · فهرس · trigger · RLS | migrations | ✅ [§3.140](log/3.140.md) |
| ٣ | الصلاحيات: ~~`_MODULE_SCHEMAS` · `_TENANT_TABLES` · `_MIGRATION_CHAINS`~~ (‏[§3.140](log/3.140.md)) · **`PURGE_GRANTS`** وتغطية `app.ops.purge` | `ops/provision.py` · `ops/purge.py` | 🔲 |
| ٤ | ترحيلات `space_id` الثلاثة (`files` · `conversations` · `knowledge`) — بنمط ADD ⇒ backfill ⇒ SET NOT NULL | migrations | 🔲 |
| ٥ | الحصّة: `max_space_bytes` + فحص `FOR UPDATE` في خدمة التسجيل | settings · files · DI | 🔲 |
| ٦ | ربط `files`: كيان · مستودع · `ports/spaces.py` · ترشيح | `modules/files` | 🔲 |
| ٧ | ربط `conversations`: كيان · مستودع · `ports/spaces.py` · ترشيح · شرط التثبيت (§3.5) | `modules/conversations` | 🔲 |
| ٨ | `knowledge`: عمود · بطاقة `space` · مرشِّح الاسترجاع | `modules/knowledge` | 🔲 |
| ٩ | Qdrant: `ensure_payload_index` في المنفذ والمحوّل + استدعاؤها | ports · infrastructure | 🔲 |
| ١٠ | سدّ تسريب `FilesAccess.get_readable` (النتيجة ٢‑ح) | `agent_runtime` | 🔲 |
| ١١ | `DeleteSpaceService` — الحذف المتسلسل السبعُ خطوات | DI | 🔲 |
| ١٢ | موجّه `spaces.py` + `?space_id=` على الموجّهات الثلاثة + صلاحيّتا RBAC | `api/v1` | 🔲 |
| ١٣ | جذر التركيب: ربط `SpaceUseCases` والمنفذين والخدمتين | `composition_root.py` | 🔲 |
| ١٤ | `.importlinter`: `app.modules.spaces` إلى عقد الاستقلال | `.importlinter` | 🔲 |
| ١٥ | الوثائق الملزِمة: 01‑data‑model · 02‑port‑contracts · 03‑api‑spec · 07‑nfr‑slo | `docs/design` | 🔲 |
| ١٦ | مهمّة تشغيليّة لمرّة واحدة: فهارس البطاقة على الصناديق القائمة (§5‑ب) | `ops/` | 🔲 |

**لا يرتفع إصدار أيّ حدث** — إضافة حقل اختياريّ ⇒ نفس الإصدار ([`04-event-catalog.md:136`](design/04-event-catalog.md#L136)).

---

## 5. الترحيل التشغيليّ — بندان لا يُنسيان

### 5‑أ · النقاط المفهرَسة اليوم ستختفي

النقاط القائمة في Qdrant **لا تحمل مفتاح `space`**، وQdrant لا تطابق نقطةً يغيب عنها المفتاح المطلوب. ومحوّلنا يبني `must` فقط — لا `IsEmpty` ولا `should` (النتيجة ٢‑هـ).

**النتيجة: لحظة تفعيل المرشِّح، كلّ ما فُهرِس سابقًا يصير غير مرئيّ — بلا خطأ، بلا تحذير، نتائج فارغة فقط.**

**العلاج المعتمد:** بعد الخطوة ٤، أعِد فهرسة كلّ مستند قائم عبر آليّة إعادة الفهرسة المبنيّة (`POST /api/v1/knowledge/reindex`). هي مبنيّة ومختبَرة، وتكتب البطاقة الجديدة كاملةً. **لا** جراحةَ بطاقاتٍ يدويّة.

> عند كتابة هذه الخطّة: 3 مستندات · 138 قصاصة. إعادة الفهرسة لحظيّة.

### 5‑ب · الصناديق القائمة لا تكسب الفهارس تلقائيًّا

`ensure_hybrid_collection` تخرج فورًا إن كان الصندوق موجودًا (النتيجة ٢‑د)، فإضافة `ensure_payload_index` داخلها تنفع الصناديق الجديدة وحدها. الخطوة ١٦ تسدّ ذلك. إنشاء فهرس عمليّة آمنة لا تمسّ البيانات.

### 5‑ج · ما لا يصلح

`app.ops.purge` **لا يُستعمل** هنا (النتيجة ٢‑و): شرط أهليّته «مساحة عمل محذوفة ومنتهية مدّة احتفاظها». أيّ مسحٍ لمساحة عملٍ حيّة يحتاج سكربتًا لمرّة واحدة، بـ`--dry-run` أوّلًا.

---

## 6. المخاطر

| # | الخطر | التخفيف |
|---|---|---|
| ١ | اختفاء المحتوى المفهرَس بعد تفعيل المرشِّح | §5‑أ — إعادة فهرسة إلزاميّة قبل التفعيل |
| ٢ | تجاوز الحصّة عند رفعين متزامنين | `FOR UPDATE` على صفّ الوحدة (§3.3) |
| ٣ | قراءة ملفّ وحدة أخرى بمعرّفه | الخطوة ١٠ — تقييد `get_readable` |
| ٤ | تثبيت ملفّ من وحدة أخرى | §3.5 — رفضٌ صريح، لا سقوطٌ صامت |
| ٥ | حذف متسلسل يتعثّر في منتصفه | الترتيب + idempotency (§3.6) — إعادة التشغيل تُكمل |
| ٦ | `SET NOT NULL` يفشل على صفوف قديمة | نمط ADD ⇒ backfill ⇒ SET NOT NULL في ترحيلٍ واحد |

---

## 7. خارج النطاق — يُسجَّل ولا يُنفَّذ

- تعدّد مساحات العمل، وإنشاؤها أو التبديل بينها
- مشاركة وحدة بين مستخدمين، وصلاحيّات على مستوى الوحدة
- نقل ملف أو محادثة بين الوحدات
- حصّة على مستوى مساحة العمل فوق حصص الوحدات
- تعشيش مفاتيح MinIO بالوحدة (§3.6 — مرفوض بحجّة)
- `m=0` في إعداد HNSW (§3.4 — مرفوض بحجّة)
- إزالة `conversation_files` (القرار ٥ — تبقى)

</div>
