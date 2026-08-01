<div dir="rtl">

# خطة إغلاق الدَّينَين المُعلَنَين — `DocumentContentResolver` · `MediaGenerator`

> **Deferred-Adapters Plan** · وثيقةٌ حيّة تُحدَّث بعد كلّ خطوة · مصمَّمةٌ لتُستأنف عبر جلساتٍ منفصلة
>
> هذه الخطة تُغلق ما أخرجته الخطتان السابقتان من نطاقهما **صراحةً لا سهواً**: [`release-blockers-plan.md`](release-blockers-plan.md) §4 («الدَّين المُعلَن — بلا تغيير») و[`p1-hardening-plan.md`](p1-hardening-plan.md) §5 («خلطُه بمسار التصليب يُفسد كليهما»). تلك رفعت حاجبات النشر ثمّ مخاطر ما قبل الفتح؛ وهذه تفتح **العاملَين الباقيَين** من ثلاثة.

| | |
|---|---|
| **أُنشئت** | 2026‑08‑01 |
| **الأساس** | `6946e3e` (‏`master`) · [`log/INDEX.md`](log/INDEX.md) حتّى §3.97 · البوّابات الخمس لم تُقَس عند الإنشاء (وثيقةُ تخطيطٍ لا تغيّر كوداً) |
| **النطاق** | `DocumentContentResolver` (يحجب عامل `knowledge`) · `MediaGenerator` (يحجب عامل `media`) · وثغرةُ الملفّ المسموم المكتشَفة أثناء التخطيط (§1‑ج) |
| **خارج النطاق** | **`2.8‑ب‑2`** (محوّلات Gemini · Claude · OpenRouter — محجوبةٌ بالمفاتيح، ولا علاقة لها بالعمّال) · **توليد الفيديو** (§6، مؤجَّلٌ بقرارٍ مسجَّل) |
| **الحالة العامّة** | 🔵 **4 / 6 — الخطوة 18 بانتظار مراجعة المستخدم** · ✅ **المسار (أ) مكتمل 3/3: دَينُ `DocumentContentResolver` أُغلق** · 🔵 المسار (ب) 1/3 |
| **الخطوة التالية** | **الخطوة 19** — محوّل `ImageProvider` (OpenAI Images، §6‑أ) + `MediaGenerator` |

---

## 0. قاعدة التنفيذ

منقولةٌ من [`p1-hardening-plan.md`](p1-hardening-plan.md) §0 — أثبتت جدواها أربع عشرة مرّة.

**وكيلٌ فرعيٌّ واحد لكلّ خطوة، ثمّ توقّف.** لا تُطلَق خطوتان معاً ولا يُدمَج بندان في وكيل.

كلّ وكيل ملزَمٌ بـ:

1. تنفيذ **مهمّته وحدها** — ما خرج عن نطاقها يُسجَّل ملاحظةً في §6‑ب ولا يُنفَّذ.
2. تشغيل **البوابات الخمس** قبل إعلان الانتهاء: `ruff format --check` · `ruff check` · `mypy src` · `lint-imports` · `pytest`.
3. **برهانُ الفشل على الحالة القديمة بآليّة العطب نفسها** — لا بخطأ جمعٍ ولا `ImportError`.
4. كتابة مدخلة سجلٍّ في `docs/log/` بالرقم التالي (**§3.98 فما فوق**) وربطها في [`log/INDEX.md`](log/INDEX.md) و[`log/CHANGELOG.md`](log/CHANGELOG.md).
5. تحديث صفّه في §3 وسجلّ §5 من هذه الوثيقة.
6. **لا تُطبَع قيمة سرٍّ أبداً** (ن‑11 · قاعدةٌ دائمة). هذه الخطة تلمس Vault في خطوتها الأولى، فالقاعدة **ملزِمةٌ من الخطوة 15 نفسها**: الفحص المسموح `printenv <اسمٍ غير حسّاس>` أو الطول وحده (`${#VAR}`)، ولا `env`/`declare -p`/`set` على بيئةٍ قد تحمل سرّاً.

بعد كلّ وكيل: **المُنسِّق يراجع الفرق ويعيد تشغيل البوابات بنفسه ويعرض النتيجة على المستخدم**، ولا يُطلق التالي إلّا بإذن.

---

## 1. ما وجده الفحص — وتصحيحان للصورة المسجَّلة

فحصُ الشجرة عند `6946e3e` غيّر تقدير البندَين في اتّجاهين متعاكسين. الخطّة مبنيّةٌ على ما وُجد، لا على ما هو مكتوبٌ في الوثائق.

### 1‑أ · `MediaGenerator` أثقل ممّا هو مسجَّل — ثلاثة محوّلاتٍ لا واحد

جدول الدَّين في [`release-blockers-plan.md`](release-blockers-plan.md) §4 يقول «لا محوّل إطلاقاً»، وهو صحيحٌ لكنّه ناقص. الحقيقة أنّ **منفذَي المزوّد نفسَيهما بلا محوّل**:

| الملفّ | الحجم |
|---|---|
| `src/app/infrastructure/ai_providers/image/external_image.py` | **0 بايت** |
| `src/app/infrastructure/ai_providers/video/external_video.py` | **0 بايت** |

وأبعد من ذلك: `ProviderResolver` **لا يعرف فضاءً اسمه صورةٌ أو فيديو** — فضاءاته `("llm", "embedding")` وحدها (`framework/providers/resolver.py:139`)، والإعدادات تحمل سقوفاً رقميّة (`max_image_dim` · `max_video_seconds`، `settings.py:254‑255`) بلا **أيّ** توجيه مزوّد. ومراجع الهجرة من `alpha` لا تحوي توليد وسائط إطلاقاً ⇒ لا سابقة تُحتذى.

⇒ المسار (ب) = محوّل مزوّدٍ + توسعةُ محلِّل + محوّلٌ لاصق، لا محوّلٌ واحد.

### 1‑ب · `DocumentContentResolver` أخفّ ممّا هو مسجَّل

في المقابل، القطعُ الثقيلة **مكتوبةٌ وجاهزةٌ** ولم تُوصَل:

- مُرسِل الاستخراج `DocumentContentExtractor` مكتوبٌ كاملاً بجدول توجيهٍ فوق عشرة امتدادات (`knowledge/adapters/parsers/extractor.py:110`) — وليس صحيحاً ما يقوله docstring المصنع من أنّ «لا تركيب إرسالٍ موجوداً بعد».
- محوّل MinIO مكتوبٌ ومختبَر (`infrastructure/storage/minio_storage.py:114`).
- خطّ `IndexDocument` صار حقيقيّاً منذ 2.10 (‏`embeddings` + `vectors`).

**فالحاجب الحقيقيّ الوحيد معماريٌّ:** مفاتيح MinIO قراءةُ سرٍّ **لا‑متزامنة** من Vault، و`build_knowledge_worker_from_env` مصنعٌ **متزامن** (‏`workers/bootstrap.py:457`). وهذا الحاجب بعينه **محلولٌ سلفاً في طرف الـAPI** منذ 6.1‑هـ‑1 عبر `StorageHandle` + `connect_storage` (‏`framework/di/composition_root.py:1225`)، ولم يُنقَل الحلّ إلى العمّال.

⇒ المسار (أ) = نقلُ حلٍّ قائم + محوّلٌ لاصق من ~40 سطراً.

### 1‑ج · ثغرة «الملفّ المسموم» — اكتشافٌ جديد يجب أن يُغلق مع فتح العامل

جدولان في المستودع **غير متّسقين**، والفجوة بينهما لم تكن قابلةً للانفجار ما دام العامل محجوباً — وتنفجر يوم يُفتَح:

| الجدول | الموضع | المحتوى |
|---|---|---|
| `Limits.allowed_mime` | `framework/settings/settings.py:240‑248` | يسمح بـ**DOCX**، ولا يسمح بـ`.xlsx`/`.json`/`.csv` |
| `_ROUTES` | `knowledge/adapters/parsers/extractor.py:83‑94` | **لا يوجّه `.docx`** (مؤجَّلٌ صراحةً في 3.k1)، ويوجّه `.xlsx`/`.json`/`.csv` |

ومسارُ الانفجار محدَّد: `extract` يرفع `UnsupportedTypeError` ⇒ يخرج من `content.resolve` عند `workers/bootstrap.py:389` — وهو موضعٌ **خارج** الالتقاط الواسع داخل `index.run` ⇒ يخرج من المعالج كاملاً ⇒ إعادةُ تسليمٍ حتّى `max_deliveries` ⇒ DLQ، **والوثيقة عالقةٌ في `pending` إلى الأبد** بلا حدث فشلٍ واحد يخبر المستخدم.

⇒ الخطوة 16 تحمل إصلاحه. **ليس تحسيناً اختياريّاً**: فتحُ العامل بدونه يفتح معه حلقة إعادة تسليم على أوّل ملفّ DOCX يرفعه مستخدم.

### 1‑د · أين يسكن المحوّلان — مُتحقَّقٌ منه لا مُفترَض

كلا المحوّلَين يجمع **وحدتَين** (‏`files` + `knowledge`، و`files` + `media`) مع بنيةٍ تحتيّة، وهو ممنوعٌ داخل `app.modules.*` بعقد `modules-independent`. الموضع الصحيح `app.workers`: العقود في [`.importlinter`](../.importlinter) **لا تُدرج `app.workers`** في طبقاتها ولا في عقد الاستقلال، وترويسة العقد 6 تقول حرفيّاً «(and worker entrypoints, which are composition roots for their process)» (سطر 120). فالسماح بنيويٌّ ومقصود، لا ثغرةٌ في الفحص.

---

## 2. لماذا هذا الترتيب تحديداً

**المسار (أ) كاملاً قبل (ب).** الخطوة 15 ليست خطوةً في المسار (أ) وحده — إنّها **شرطٌ لازمٌ للخطوة 19 أيضاً**: عامل الوسائط يحتاج التخزين اللا‑متزامن نفسه لحفظ البايتات المولَّدة. تنفيذها مرّةً واحدةً في أوّل الخطّة يمنع اختراع حلَّين لمشكلةٍ واحدة.

**الثغرة مع فتح العامل لا بعده.** الخطوة 16 تحمل إصلاح §1‑ج في نفس الـPR الذي يفتح المسار، لأنّ الفصل بينهما يعني نافذةً يكون فيها العامل حيّاً وقابلاً للتسميم.

**التوثيق خطوةٌ لا ذيل.** سلاسل «لا محوّل بعد» موزَّعةٌ على **ستّة مواضع في الكود وأربع وثائق** (§4، الخطوة 17) — 🐛 **والمسحُ الفعليّ وجدها تسعةً وإحدى عشرة** ([§3.101](log/3.101.md))؛ التقديرُ كُتب قبل المسح، ولهذا كان معيار الإنجاز `grep` لا عدّاداً. تركُها للاحق يعني مستودعاً يكذب على قارئه التالي — وهو بالضبط ما صحّحه الالتزام `560016d` من قبل.

**البوّابة قبل الشيفرة.** الخطوة 18 لا تُطلَق قبل أن يكون المزوّد محسوماً (§6‑أ)، لأنّ `_parse_namespace` يرفض البناء أصلاً على فضاءٍ بلا محوّلٍ موصول (`resolver.py:224`).

> 🐛 **تصحيحٌ من تنفيذ الخطوة 18** ([§3.102](log/3.102.md)): هذه البوّابة **لم تكن ملزِمة**، والحجّةُ المكتوبة لها تُثبت عكسها. `_parse_namespace` يرفض البناء على مزوّدٍ بلا محوّل — أي أنّ الفضاء يُعلَن بخريطةٍ **فارغة** بلا ضررٍ إطلاقاً: توجيهُ صورةٍ يبقى مرفوضاً كما كان، وكلُّ ما يتغيّر دقّةُ سبب الرفض. البوّابة الحقيقيّة تخصّ **الخطوة 19** (لا يُبنى محوّلٌ لمزوّدٍ غير محسوم). حُسم المزوّد فعلاً قبل التنفيذ (**OpenAI Images**، 2026‑08‑01) فلم يتغيّر شيءٌ عمليّاً، والنصُّ الأصليّ محفوظٌ سجلّاً.

**الفيديو خارجاً.** `VideoResult` يسمح بـ`remote_url` بدل بايتات (`framework/ports/video_provider.py:24`) ⇒ خطوةُ جلبٍ إضافيّةٌ بمهلةٍ وسقفِ حجمٍ ومعالجةِ فشل. خلطُها بمسار الصورة يضاعف سطح الخطوة 19 بلا داعٍ.

---

## 3. لوحة التقدّم

| # | الخطوة | المسار | يغيّر الحالة؟ | الوكيل | الحالة | السجلّ |
|---|---|---|---|---|---|---|
| **15** | **ربط MinIO مشترَك + مصنعٌ لا‑متزامن للعامل** | أ | ❌ ملفّات فقط | `implementer` | 🔵 بانتظار مراجعة المستخدم | [§3.98](log/3.98.md) |
| **16** | **`WorkerDocumentContentResolver` + ثغرة الملفّ المسموم** | أ | ❌ ملفّات فقط | المُنسِّق | 🔵 بانتظار مراجعة المستخدم | [§3.100](log/3.100.md) |
| **17** | **توثيق: عامل `knowledge` صار يُقلع** | أ | ❌ ملفّات فقط | المُنسِّق | 🔵 بانتظار مراجعة المستخدم | [§3.101](log/3.101.md) |
| **18** | **توسيع `ProviderResolver` بفضاء `image`** | ب | ❌ ملفّات فقط | المُنسِّق | 🔵 بانتظار مراجعة المستخدم | [§3.102](log/3.102.md) |
| **19** | **محوّل `ImageProvider` + `MediaGenerator`** | ب | ❌ ملفّات فقط | `implementer` | ⬜ لم تبدأ | — |
| **20** | **`build_media_worker_from_env` + توثيق الإغلاق** | ب | ❌ ملفّات فقط | `implementer` | ⬜ لم تبدأ | — |

> رموز الحالة: ⬜ لم تبدأ · 🟡 قيد التنفيذ · 🔵 بانتظار مراجعة المستخدم · ✅ مكتملة ومُراجَعة · ⛔ محجوبة (اذكر السبب)

**لا خطوةَ في هذه الخطّة تغيّر حالة النظام الحيّ.** لا هجرات، ولا لمسَ عنقود، ولا إعادة إنشاء حاوية — التغييراتُ كلّها ملفّات، وأثرُها يظهر عند أوّل إقلاعٍ لاحقٍ للعامل بإذنٍ منفصل.

---

## 4. تفصيل الخطوات

### الخطوة 15 — ربط MinIO مشترَك + مصنعٌ لا‑متزامن للعامل ⬜

**المشكلة.** مفاتيح MinIO قراءةُ سرٍّ لا‑متزامنة (`await secrets.get_secret("secret/data/minio")`، 05 §3)، و`build_knowledge_worker_from_env` مصنعٌ متزامن ⇒ لا يستطيع بناء التخزين، فيرفع `AppError` مسمّياً الفجوة (`workers/bootstrap.py:504‑527`). طرف الـAPI حلّ هذا سلفاً؛ العمّال لم يرثوا الحلّ.

**الملفّات.** `framework/di/storage_binding.py` (**جديد**) · `framework/di/composition_root.py` (‏`connect_storage` سطر 1225 · ورفعُ `_build_vault` سطر 530 إلى دالّةٍ عامّة مشترَكة) · `workers/bootstrap.py` · `workers/knowledge_worker.py` · `tests/unit/test_workers_bootstrap.py:775`.

**القرار المتّخَذ.**

1. استخراج جسم `connect_storage` (سطور 1240‑1256: قراءة السرّ · التحقّق من `access_key`/`secret_key` · بناء العميلَين · `bind`) إلى `bind_minio(handle, secrets, settings)`. يصير `connect_storage` مناديّاً من سطرين، ويصير للتحقّق من شكل السرّ **موضعٌ واحد** بدل نسختين حرّتين في الانجراف.
2. `build_knowledge_worker_from_env` تصير **`async def`**. المدخل يعمل أصلاً داخل `asyncio.run` (`knowledge_worker.py:36`) فالكلفة `await` واحد. **البديل المرفوض:** إعادةُ قائمة `startup=` على نمط `create_production_app` — أثقل ولا تشتري شيئاً هنا، إذ لا خطّاف إقلاعٍ ثانٍ في العامل.
3. إضافة `vault_client.close()` إلى `disposables` — سابقةُ كلّ سائقٍ آخر على الجذر.

**معيار الإنجاز.** اختبارٌ يُثبت أنّ المصنع **يبني** بدل أن يرفع، واختبارٌ سلبيٌّ جديد يُثبت أنّ سرَّ MinIO المشوَّه (‏`access_key` فارغ) يرفع `ValidationError` من الموضع المشترَك الجديد — على الحالة القديمة يفشل الأوّل بـ`AppError` والثاني بعدم وجود المسار أصلاً.

---

### الخطوة 16 — `WorkerDocumentContentResolver` + ثغرة الملفّ المسموم 🔵

**المشكلة.** نصفان: الدرز في `workers/bootstrap.py:299` بلا محوّل (فلا فهرسة)، والفجوةُ المكتشَفة في §1‑ج (فحلقةُ إعادة تسليمٍ يوم تعمل الفهرسة).

**الملفّات.** `workers/content_resolver.py` (**جديد**) · `workers/bootstrap.py` (‏`build_knowledge_index_handler` سطر 359 · `build_knowledge_worker_from_env`) · `modules/knowledge/application/use_cases.py` · `framework/settings/settings.py:240` · `tests/unit/test_content_resolver.py` (**جديد**) · `tests/integration/test_e2e_outbox_to_worker.py`.

**القرار المتّخَذ — المحوّل.** أربع خطوات تحقّق البروتوكول:

| # | الخطوة | التبعيّة |
|---|---|---|
| 1 | `file = await files.get(ctx, file_id)` ← `NotFoundError` إن غاب | `SqlFileRepository` |
| 2 | `data = await storage.get(file.storage_key.value)` | `StorageHandle` (الخطوة 15) |
| 3 | `await asyncio.to_thread(extractor.extract, ...)` | `DocumentContentExtractor` |
| 4 | `resolved = await resolver.resolve_embedding(ctx)` | ↓ |

`asyncio.to_thread` **إلزاميّة**: المنفذ متزامنٌ بالتصميم ويُلزم مستدعيه بالإزاحة صراحةً (`knowledge/ports/content_extractor.py:33‑38`) — PyMuPDF/pandas/pytesseract كلّها حاجبة، وبدونها يتجمّد حلقةُ حدث العامل بأكملها على أوّل PDF.

**القرار المتّخَذ — من يحلّ نموذج التضمين.** يُبنى في العامل **نفس** `SettingsProviderResolver` + `ResolveCredential` اللذين يبنيهما جذر التركيب (`composition_root.py:977`)، لا محلِّلٌ محلّيٌّ يقرأ الإعدادات مباشرة. السبب حجّةُ صحّةٍ لا أناقة: لو فهرس العاملُ بنموذجٍ واستعلم `/search` بآخر، انكسر الاسترجاع **بصمتٍ تامّ** (اختلاف الأبعاد/الفضاء المتجهيّ) بلا خطأٍ واحد يظهر في سجلٍّ أو مقياس. المكوّنات جاهزة: `SqlCredentialRepository` + `secrets` القادم من الخطوة 15.

**القرار المتّخَذ — الثغرة.** نصفان أيضاً:

1. **ترجمة الفشل إلى حالةٍ نهائيّة:** في `build_knowledge_index_handler`، يصير فشلُ `content.resolve` (‏`UnsupportedTypeError`/`ValidationError`) وثيقةً `failed` + حدث `knowledge.document.indexing_failed.v1` داخل **نفس** كتلة `uow.begin` — عمليّاً بمصنعٍ صغير `IndexRegisteredDocument.fail(ctx, document_id, reason) -> IndexAttempt` يُعيد استخدام `finalize` كما هي، بلا مسارٍ ثانٍ للحالة النهائيّة.
2. **توحيد الجدولين:** إسقاط DOCX من `allowed_mime` (لا محلّل له، و`python-docx` ليست تبعيّةً معتمدة) وإضافة `.xlsx`/`.json`/`.csv` إليه (لها محلّلات جاهزة لا تصلها ملفّات).

**معيار الإنجاز.** اختبارُ وحدةٍ يُثبت أنّ ملفّاً بامتدادٍ غير مدعوم **ينتهي `failed` بحدثٍ**، لا برفعٍ يخرج من المعالج — وعلى الحالة القديمة يفشل بآليّة العطب نفسها (الاستثناء يعبر المعالج). واختبارُ تكاملٍ حيٍّ لمسارٍ كامل `file.uploaded → registered → indexed` لملفّ `.txt` حقيقيّ فوق PG + MinIO + Qdrant الحيّة.

---

### الخطوة 17 — توثيق: عامل `knowledge` صار يُقلع 🔵

**المشكلة.** بعد الخطوة 16 يصير المستودع **يكذب على قارئه**: ستّة مواضع في الكود وأربع وثائق تقول «لا محوّل بعد» عن محوّلٍ صار موجوداً.

**الملفّات.** الكود: `workers/bootstrap.py:79` · `:299` · `:457‑527` · `workers/knowledge_worker.py:6` · `framework/di/composition_root.py:113` · `docker-compose.yml:426‑455`. الوثائق: [`ROADMAP.md`](ROADMAP.md) (سطر 236) · [`release-blockers-plan.md`](release-blockers-plan.md) §4 · [`pre-release-review.md`](pre-release-review.md) §P0‑4 · [`implementation-status.md`](implementation-status.md).

**القرار المتّخَذ.** تعليق `WORKER` في `docker-compose.yml` يُعاد كتابته لا يُحذف: الافتراضيّ يبقى `memory` (‏`tests/unit/test_deploy_worker_default.py` يحرس اتّفاقه مع `deploy/runpod/entrypoint.sh`)، لكنّ نصّه يصير «اثنان من ثلاثة يُقلعان» بدل «قيمةٌ واحدةٌ تُقلع». وتصحيحُ docstring المصنع الذي يزعم غياب تركيب الإرسال (§1‑ب) جزءٌ من هذه الخطوة.

**معيار الإنجاز.** `grep` على «`no adapter`»/«`still-nonexistent`»/«`DocumentContentResolver`» لا يُرجع ادّعاءً باقياً بالغياب، ومدخلةُ سجلٍّ تُسجّل إغلاق نصف الدَّين.

> ✅ **مُنجَزة ([§3.101](log/3.101.md)).** المسحُ وجد **أكثر** ممّا قُدِّر: تسعةُ مواضعَ في الكود والنشر لا ستّة، و**إحدى عشرة وثيقةً لا أربع**. والقرار الحامل: **«موصولٌ» ليست «يقلع»** — الصيغةُ المعتمدة في كلّ موضع «موصولٌ بالكامل، لم يُقلَع بعد»، والافتراضيّ `WORKER=memory` **لم يتغيّر** لأنّه الوحيد المقيسُ حاويّاً (§3.83) لا لأنّ `knowledge` محجوب. البرهانُ بآليّة المعيار نفسه (`git archive HEAD` + `grep` مؤتمَت): **24 ادّعاءَ غيابٍ قبل · 5 بعد**، والخمسةُ كلُّها ماضٍ أو نصٌّ مؤرَّخٌ مُصانٌ فوقه مؤشّرُ إغلاق.

---

### الخطوة 18 — توسيع `ProviderResolver` بفضاء `image` 🔵

**المشكلة.** لا توجيه صورةٍ في المنظومة إطلاقاً (§1‑أ).

**الملفّات.** `framework/providers/resolver.py` (‏`_NAMESPACES` سطر 139 · `ProviderResolver` سطر 119 · `SettingsProviderResolver` سطر 233) · `framework/settings/settings.py` (‏`provider_routing`) · `framework/di/composition_root.py:977` · `tests/unit/test_provider_resolver.py` · `.env.example`.

**القرار المتّخَذ.** يُضاف فضاء **`image` وحده** و`resolve_image`، على غرار `resolve_embedding` حرفيّاً. فضاء `video` **لا يُضاف** — و**المنع بنيويٌّ مجّاناً**: `_parse_namespace` يرفض البناء على مزوّدٍ بلا محوّلٍ موصول برسالة `provider {...} has no wired adapter` (سطر 224)، فمحاولةُ توجيه فيديو تُوقِف الإقلاع بدل أن تفشل وقت الطلب.

**معيار الإنجاز.** بطاريّة التحليل الصارم القائمة (‏R6) مُوسَّعةٌ على الفضاء الجديد: فضاءٌ مجهول · مدخلةٌ ناقصةُ مفتاح · مزوّدٌ غير موصول — كلّها ترفض البناء بـ422 تُسمّي `provider_routing` نفسه.

✅ **أُنجزت 2026‑08‑02** — [§3.102](log/3.102.md). 12 اختباراً جديداً، والبوّابات الخمس خضراء.

🐛 **ما لم تتوقّعه هذه الخطّة، وكان سيشحن عطباً**: إعلانُ الفضاء كان **سيكسر عامل `knowledge`**. الخطوة 16 كتبت `_embedding_routing` لتحذف `llm` وحده (عمداً: الاسمُ المغلوط يجب أن يصل إلى المحلِّل الصارم فيُرفَض باسمه). فلحظةَ صار `image` شرعيّاً، كان توجيهُ صورةٍ يكتبه المشغّل سيعبر التضييق إلى عاملٍ يمرّر `image_providers={}` فيرفض الإقلاع بسبب **قدرةٍ لا يدّعيها أصلاً**. انضمّ `image` إلى `llm` في `_NAMESPACES_FOREIGN_TO_THIS_WORKER` بالحجّة نفسها، والاختبارُ يبني المحلِّل الحقيقيّ مرّتين على الجدول ذاته. ⇒ الملفّات الفعليّة تجاوزت ما ذُكر أعلاه بـ`workers/bootstrap.py` و`tests/unit/test_workers_bootstrap.py`.

---

### الخطوة 19 — محوّل `ImageProvider` + `MediaGenerator` ⬜

**المشكلة.** الملفّان صفرا بايت، والدرزُ في `media/ports/generation.py:24` بلا محوّل.

**الملفّات.** `infrastructure/ai_providers/image/external_image.py` · `workers/media_generation.py` (**جديد**) · `tests/unit/test_media_generation.py` (**جديد**) · `tests/integration/test_media_worker_live.py:47`.

**القرار المتّخَذ — المزوّد.** **OpenAI Images أوّلاً** (قرار المستخدم 2026‑08‑01، §6‑أ). وهو المزوّد الوحيد الموصول من طرفٍ لطرف: `create_openai_http_client` جاهز (`llm/openai_llm.py:224`)، والمفتاح يُحلّ عبر `ResolveCredential` القائم، و`keyless_providers` لا تُمَسّ. المحوّل يُبنى فوق `llm/shared.py` (‏`create_llm_http_client` · `translate_http_error` · `off_contract`) بسمة `provider = "openai-image"` — نفسُ نمط `openai_llm.py` حرفيّاً.

**القرار المتّخَذ — المولّد.** يسكن `app.workers` (§1‑د)، ومساره:

```
resolve_image → provider.generate → RegisterUpload → storage.put → CompleteUpload → return file.id
```

و`MediaKind.VIDEO` يُقابَل بخطأٍ **مصنَّفٍ صريح** (`media.unsupported_kind`) لا `NotImplementedError` عارٍ — فيلتقطه الالتقاطُ الواسع في `RunMediaJob.run` (`media/application/use_cases.py:215`) ويُنزل المهمّة `failed` برسالةٍ مفهومة. وكيل الفيديو يفشل فشلاً نظيفاً معروف السبب، لا حلقة إعادة تسليم.

**⚠️ أثرٌ جانبيٌّ يُحسَم قبل الكتابة لا بعدها.** `CompleteUpload` يُصدِر `FileUploaded` (`files/application/use_cases.py:113`) — وهو **بعينه** الحدث الذي يوقظ عامل `knowledge`. فكلّ صورةٍ يولّدها وكيلٌ ستُسجَّل وثيقةً وتمرّ على OCR وتدخل قاعدة معرفة مساحة العمل. **القرار: تُخرَج الملفّات المولَّدة من مسار الحدث** — فهرسةُ ناتجٍ توليديٍّ ثمّ استرجاعُه لاحقاً كـ«مصدر» حلقةُ تلوّثٍ للسياق، والوكيل يستشهد بما اختلقه هو.

**معيار الإنجاز.** اختبارُ وحدةٍ بمزوّدٍ وتخزينٍ مزيَّفَين (المسار السعيد · فشلُ المزوّد ⇒ `failed` برسالة · `VIDEO` ⇒ `media.unsupported_kind`)، ثمّ استبدالُ `FakeMediaGenerator` في اختبار التكامل الحيّ بالمولّد الحقيقيّ فوق MinIO حيٍّ ومزوّدٍ مُوقَفٍ عند حدود HTTP وحدها. **وإثباتٌ صريح** أنّ توليد صورةٍ لا يُنتِج صفَّ Outbox لـ`files.file.uploaded.v1`.

---

### الخطوة 20 — `build_media_worker_from_env` + توثيق الإغلاق ⬜

**الملفّات.** `workers/bootstrap.py` (‏`build_media_worker_from_env`) · `workers/media_worker.py:6` · `modules/media/ports/generation.py:1‑13` · الوثائق الأربع نفسها من الخطوة 17.

**القرار المتّخَذ.** نفسُ نمط الخطوة 15 (لا‑متزامن + خزانة + تخزين) وحذفُ الرفع. والدرزُ في `media/ports/generation.py` **يبقى كما هو** ولا يُلغى: docstring‑ه يعِد بأنّ المولّد يُوصَل خلفه «دون لمس `RunMediaJob`» — وهذه الخطوة تُنفّذ الوعد لا تنقضه.

**معيار الإنجاز.** العمّال الثلاثة يُقلعون، ويُحدَّث [`ROADMAP.md`](ROADMAP.md) بإسقاط الدَّينَين من قائمة «ما بقي مفتوحاً»، مع بقاء الفيديو و`2.8‑ب‑2` مذكورَين صراحةً.

---

## 5. سجلّ التنفيذ

> يُملأ صفّاً صفّاً بعد مراجعة كلّ خطوة — سابقةُ §4 في الخطتين السابقتين.

| الخطوة | التاريخ | السجلّ | ما تغيّر فعلاً |
|---|---|---|---|
| 15 | 2026‑08‑01 | [§3.98](log/3.98.md) | `framework/di/vault_binding.py` + `framework/di/storage_binding.py` (جديدان) · `composition_root.py` (‏`connect_storage` مناديّاً سطرين، `_build_vault` حُذفت) · `.importlinter` (سطران في العقد 6) · `workers/bootstrap.py` (‏`build_knowledge_worker_from_env` صارت `async` وتبني `storage` حقيقيّاً) · `workers/knowledge_worker.py` · `tests/unit/test_workers_bootstrap.py` + `tests/unit/test_storage_binding.py` (جديد) |
| 16 | 2026‑08‑01 | [§3.100](log/3.100.md) | `workers/content_resolver.py` (**جديد** — `WorkerDocumentContentResolver`) · `workers/bootstrap.py` (‏فرعُ فشلِ `content.resolve` في معالج الفهرسة · `_embedding_routing` + `_KEYLESS_PROVIDERS` · `build_knowledge_worker_from_env` **لم تعد ترفع** وتعيد خمسة `disposables`) · `knowledge/application/use_cases.py` (‏`IndexRegisteredDocument.fail`) · `framework/settings/settings.py` (‏`allowed_mime` صارت مرآةَ `_ROUTES`: DOCX أُسقط، و`csv`/`json`/`xlsx` أُضيفت) · `tests/unit/test_content_resolver.py` (**جديد**، 5) · `tests/unit/test_workers_bootstrap.py` (+5) · `tests/integration/test_e2e_outbox_to_worker.py` (+1 حيّ فوق PG+MinIO+Qdrant+Redis) |
| 17 | 2026‑08‑02 | [§3.101](log/3.101.md) | **نصٌّ فقط، ولا سطرَ منطقٍ واحد.** كود: `workers/bootstrap.py` (docstringا الوحدة والبروتوكول) · `workers/knowledge_worker.py` · `framework/di/composition_root.py`. نشر: `docker-compose.yml` (‏تعليق `WORKER` **أُعيدت كتابتُه لا حُذف**، والافتراضيّ `memory` كما هو) · `deploy/runpod/entrypoint.sh` · `deploy/runpod/supervisord.conf` · `.env.example`. اختبارات (نصٌّ واسمُ ثابت، بلا تأكيدٍ ولا قيمة): `tests/unit/test_workers_bootstrap.py` · `tests/unit/test_deploy_worker_default.py` (‏`_ONLY_WORKER_THAT_BOOTS_TODAY` ← `_EXPECTED_WORKER_DEFAULT`). وثائق (11): `ROADMAP` · `implementation-status` · `implementation-plan` · `pre-release-review` · `quickstart` · `deploy-linux-server` · `deploy-runpod` · `design/08-local-runbook` · `release-blockers-plan` · `p1-hardening-plan` · `acceptance-report` |
| 18 | 2026‑08‑02 | [§3.102](log/3.102.md) | `framework/providers/resolver.py` (‏`_NAMESPACES` +`image` · `_Routes` **جديدة** بدل الثلاثيّة · `image_providers` **مُلزِمة** في المُنشئ · `resolve_image` في المنفذ وفي التنفيذ) · `framework/di/composition_root.py` + `workers/bootstrap.py` (‏`image_providers={}`) · 🐛 `workers/bootstrap.py` (‏`_NAMESPACES_FOREIGN_TO_THIS_WORKER` — `image` انضمّ إلى `llm`، وإلّا كسر توجيهُ صورةٍ عاملَ `knowledge`) · `framework/settings/settings.py` + `.env.example` (توثيقٌ فقط؛ **لا توجيه صورةٍ في `PROVIDER_ROUTING`** — يكسر كلَّ إقلاعٍ حتّى الخطوة 19) · `tests/unit/test_provider_resolver.py` (+11) · `tests/unit/test_workers_bootstrap.py` (+1) |

---

## 6. خارج نطاق هذه الخطة

### 6‑أ · قرارٌ محسوم — لماذا OpenAI أوّلاً

عُرضت أربعة خيارات في 2026‑08‑01 (‏OpenAI · مزوّدٌ محلّيٌّ بلا مفاتيح · مزوّدٌ آخر يُسمّى · تأجيل المسار (ب) كلّه)، واختير **OpenAI Images**. الحجّة: صفرُ بنيةٍ جديدة — العميل والمفتاح والمُحلِّل كلّها قائمة، فالخطوة 19 تكتب محوّلاً لا تبني مساراً.

### 6‑ب · بنودٌ تبقى مفتوحةً بعد إغلاق هذه الخطّة

- **توليد الفيديو** — يحتاج مزوّداً يُختار، ومسارَ جلبٍ لـ`remote_url` بمهلةٍ وسقفِ حجم (§2).
- **`2.8‑ب‑2`** — محوّلات Gemini · Claude · OpenRouter، محجوبةٌ بالمفاتيح.
- **`.docx` كمحلّلٍ للمعرفة** — الخطوة 16 تُسقطه من المسموح؛ إعادتُه تحتاج `python-docx` كتبعيّةٍ معتمدة.
- **مهلةُ تحليلٍ لكلّ ملفّ** — `alpha` كانت تحملها (`PARSER_TIMEOUT_SECONDS=60`) ولم تُنقَل: `SIGALRM` غير صالحٍ من عاملٍ لا‑متزامن (`extractor.py:121‑124`). بديلُها مهلةٌ على مستوى العامل، وهي عملُ تشغيلٍ تالٍ لا جزءٌ من فتح المسار.

### 6‑ج · ملاحظاتٌ تنشأ أثناء التنفيذ — تُسجَّل ولا تُنفَّذ

> ما يُكتشَف أثناء خطوةٍ ويقع **خارج نطاقها** يُسجَّل هنا ولا يُنفَّذ (§0 القاعدة 1).

**من الخطوة 16 ([§3.100](log/3.100.md)):**

1. **`NotFoundError` من `content.resolve` تبقى مسارَ إعادة تسليم.** الخطوة 16 أغلقت `UnsupportedTypeError`/`ValidationError` — النوعين اللذين سمّتهما الخطة صراحةً. لكنّ صفَّ ملفٍّ حُذف وسط الطريق لن ينجح بإعادة المحاولة أبداً، فتنتهي الوثيقةُ `pending` بعد DLQ: **نفسُ شكل الثغرة المُغلقة، بسببٍ مختلف**. السؤال «هل غيابُ الملفّ فشلٌ نهائيّ؟» يستحقّ حسماً مستقلّاً.
2. **`_KEYLESS_PROVIDERS` صارت نسختين** — واحدةٌ في `workers/bootstrap.py` وأخرى مضمّنةٌ في `composition_root.py`. الانجرافُ يفشل **بصوتٍ عالٍ** (`credentials.none_available`) لا صامتاً، لكنّ رفعَها إلى موضعٍ مشترَك واحد — سابقةُ `vault_binding`/`storage_binding` في الخطوة 15 — تنظيفٌ يستحقّ خطوةً صغيرة.
3. **حالةُ الملفّ لا تُفحَص قبل الفهرسة.** المحوّل يقرأ بايتات أيّ ملفٍّ يجده، ولو كان `quarantined` أو محذوفاً منطقيّاً. لا مسارَ حيّ يضع `quarantined` في v1 (لا فحص مضادّ فيروسات) فالأثرُ اليوم **صفر** — لكنّ يوم يُوصَل الفحص يصير `file.is_ready` شرطاً واجباً هنا.

**من الخطوة 17 ([§3.101](log/3.101.md)):**

4. **`api/v1/routers/knowledge.py` ما يزال يقول إنّ `POST /search` يجيب 503 لأنّ «لا محوّل `EmbeddingProvider` بعد».** كذبةٌ من صنف ما كنسته الخطوة 17 بالضبط — لكنّها تخصّ دَيناً **آخر** (‏2.10، أُغلق في [§3.77](log/3.77.md))، ونطاقُ الخطوة كان مسمّىً. تُصحَّح في كنسةٍ صغيرةٍ مستقلّة أو مع الخطوة 20.
5. **أوّلُ إقلاعٍ حاويٍّ لعامل `knowledge` لم يُجرَّب.** الخطوة 17 وثّقت «موصولٌ لا مُقلَع» عن قصد، والافتراضيّ `WORKER` بقي `memory` لهذا. أوّلُ إقلاعٍ ناجح **يكسب** تغييرَ الافتراضيّ — وهو عملُ تشغيلٍ يغيّر حالة النظام الحيّ، أي **خارج نطاق هذه الخطة كلّها** ويحتاج إذناً منفصلاً.
6. **`ProviderResolver` صار منفذاً بثلاث دوالّ، وأكثرُ مستهلكيه يحتاج واحدة.** `WorkerDocumentContentResolver` يحتاج `resolve_embedding` وحدها، والمنسّق `resolve_llm` وحدها؛ وكلُّ زائفٍ في الاختبارات يُنفِّذ ما يستعمله فقط (يمرّ لأنّ `mypy` لا يفحص `tests/`). فصلُ المنفذ إلى وجوهٍ ضيّقة — على سابقة `ResolvedKeyView` «المفصولة عمداً» في الملفّ نفسه — قرارُ تصميمٍ مستقلّ، لا من نطاق خطوةٍ تضيف فضاءً. ([§3.102](log/3.102.md))
7. **`max_image_dim`/`max_video_seconds` سقفان بلا نافذ.** لا موضعَ في الشيفرة يقرؤهما اليوم. الخطوة 19 هي التي يجب أن تصل الأوّل بمحوّل الصورة، وإلّا بقي إعداداً زخرفيّاً — والثاني يبقى معلّقاً ما بقي الفيديو خارج النطاق.


---

<div align="center">

**خطة إغلاق الدَّينَين المُعلَنَين · v1.0 · أُنشئت 2026‑08‑01**

`ستّ خطوات · مسارَان · لا خطوةَ تغيّر حالة النظام الحيّ`

</div>

</div>
