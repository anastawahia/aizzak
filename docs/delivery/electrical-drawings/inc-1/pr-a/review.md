<div dir="rtl">

# مراجعة الشيفرة: وسوم الملفات `PLT‑26` (`PR‑A` من `INC‑1`، ميزة `electrical-drawings`)

- **النطاق المراجَع:** `git diff 4bc6417..HEAD` في `/home/AIZZAK` (الفرع `feat/electrical-drawings-inc-1-pr-a`، الإيداعات `c12504d` و`0cd5fe6` و`ce733d0` و`2e0b7e8` و`ae13a38`، 35 ملفاً). المرجع: `docs/delivery/electrical-drawings/docs/lld-inc-1.md` 0.2 (§1.1، §2.1 صفوف `PR‑A`، §3.1–§3.3.4، §4.1، §4.4، §5.1 `INC1‑AC‑1..3` وحصة `PR‑A` من `INC1‑AC‑16`، §7.1 `INC1‑Q‑1` (ب)، §7.2)، و`CLAUDE.md` والوثائق الحاكمة.
- **الحكم:** **PASS** (لا ملاحظة حاجبة؛ ملاحظة مهمة واحدة وأربع تحسينات)

## الملاحظات

| # | الشدة | الملف:السطر | الملاحظة | الإصلاح المطلوب |
|---|---|---|---|---|
| 1 | مهمة | `src/app/modules/files/domain/value_objects.py:213-226` (`_clean_tag`)؛ ويظهر أثره في `src/app/modules/files/application/use_cases.py:208` و`src/app/modules/files/adapters/sql_repository.py` (`_translate`) | **وسمٌ فيه surrogate مفرد يمرّ التحقق كله ثم يصير 500.** FastAPI يحلّل الجسم بـ`json.loads` من المكتبة القياسية، وهي تقبل `"\ud800"`، و`FileTagsIn` و`FileTags` يقبلانه (تحققتُ: `FileTags.from_mapping({"system": "a\ud800b"})` ينجح، و`.encode("utf-8")` يفشل بـ`surrogates not allowed`). ثم يرفضه `asyncpg` عند الكتابة (`DataError`)، فيترجمه المحوّل إلى `common.internal`. السيناريو: `PUT /files/{id}/tags` بجسم `{"system":"a\ud800b"}` ← 500 بدل 422. وعلى `POST /files/multipart` يكون الضرر أكبر: `start_multipart` (`use_cases.py:208`) يفتح رفعاً في MinIO **قبل** الإدراج الفاشل، فيبقى رفع يتيم. وهذا يخالف روح `INC1‑AC‑1` («يرفض الوسم غير الصالح بـ422 … وقبل فتح رفع مجزأ»)، وإن لم يذكر المعيار هذا الصنف بالاسم. هو B‑1 في تقرير QA. و`FileName` فيه النمط نفسه، لكنه سابق لهذا الـPR | في `_clean_tag`، بعد `strip()`، ارفض ما لا يُرمَّز UTF‑8: `try: cleaned.encode("utf-8") except UnicodeEncodeError: raise InvalidFileInput(f"tag {kind.value!r} must be valid Unicode")`. وأضف حالة `"a\ud800b"` إلى `test_tags_refuse_a_control_character` أو إلى اختبار رفض مستقل في `tests/unit/test_files_domain.py`، وحالة على الموجّه (`PUT …/tags` ← 422 ولا `save`). أما إصلاح `FileName` فبند منفصل خارج `PR‑A` |
| 2 | تحسين | `src/app/framework/di/file_replacement.py:210-217`؛ `src/app/modules/files/domain/entities.py:160-177` | **إعادة وسم ملف `uploaded` (وعدٌ لم تصل بايتاته) تكنس الأقدم الجاهز.** `retag` يقبل أي حالة، و`_sweep` لا ينظر إلى حالة الملف. السيناريو: الملف F1 جاهز بالاسم `plan.pdf` والوسوم T. يسجّل العميل F2 بالاسم نفسه دون وسوم، ثم يعيد وسمه إلى T قبل الإتمام ← يُحذف F1 وفهرسه، وقد لا تصل بايتات F2 أبداً. هذا يخالف المبدأ المكتوب في رأس الوحدة «Nothing existing may be destroyed for a promise». **وهو سلوك سابق في `rename`** (نفس الطريق، وهو موثّق في `renameFile`: «Any status may be renamed»)، والتصميم (§3.3.1) يطلب «أي حالة» لـ`retag` صراحةً. فلا خلل مقابل LLD، لكن `PLT‑26` يضيف مدخلاً ثانياً لهذا الطريق | لا تغيير في `PR‑A`. يُرفع إلى المالك أو يُسجَّل متابعة: إما أن يكنس `rename`/`retag` حين `status == ready` وحده (ويكنس الإتمام لاحقاً كما اليوم)، وإما أن يُوثَّق الاستثناء في رأس `file_replacement.py` بجانب «nothing destroyed for a promise» |
| 3 | تحسين | `docs/design/openapi.yaml:1228-1243` | **مخطط `FileTagsIn` في العقد أضيق مما يقبله الخادم، و`FileTagsOut` بلا `required`.** (أ) `maxLength: 128` يُقاس على النص الخام، والخادم يقيس بعد NFC والقص. السيناريو: `{"system": " " + "x"*128 + " "}` (130 محرفاً) يقبله الخادم بـ200، ويرفضه أي عميل يتحقق بالعقد. (ب) وصف `FileTagsOut` يقول «All three keys are always present»، والمخطط لا يعلن `required`، فالعميل المولَّد يعامل الثلاثة اختيارية | (أ) اذكر في وصف `FileTagsIn` أن الحد يُقاس بعد التطبيع والقص (أو احذف `maxLength` من الحقول واتركه في الوصف). (ب) أضف `required: [system, equipment, department]` إلى `FileTagsOut`. ويبقى `test_api_conventions` أخضر |
| 4 | تحسين | `docs/design/openapi.yaml:1196`؛ `docs/delivery/electrical-drawings/docs/lld-inc-1.md` §3.3.2 (تعليق `RegisterUpload`) و§5.1 `INC1‑AC‑1` | **العبارة «422 before any limit is spent» توحي بأسبقية رمز لا تتحقق.** `SpaceQuotaService.register` (`src/app/framework/di/space_quota.py:154-188`) يأخذ القفلين ويفحص الحصة قبل أن يحلّل `RegisterUpload` الوسوم. فالوسم الذي يرفضه النطاق مع فضاء مجهول أو ممتلئ يُجاب بـ404 أو `409 spaces.quota_exceeded` لا 422. لا شيء يُنفق ولا رفع يُفتح، فالسلوك سليم ومطابق لـ`name`. و`03-api-spec.md` يصف الترتيب الفعلي بدقة. لكن وصف `FileRegisterIn` في `openapi.yaml` ونص LLD لا يصفانه | يُصحَّح وصف `FileRegisterIn` في `openapi.yaml` ليطابق `03`: «before anything is written and before a multipart upload is opened; an unknown space (404) or a full one (409) is answered first». وتُصحَّح عبارة LLD في مراجعة الوثيقة التالية، كما أوصى QA |
| 5 | تحسين | `tests/unit/support_files_media.py:77-88` | **المستودع المزيّف يعيد الكائن المخزَّن نفسه** (`get` بلا نسخ). فالتأكيدات على `files.rows[id].tags` تنجح ولو نُسي `save`. الاختبارات الجديدة تتحاشى هذا بتأكيد `files.saved` (`test_retag_replaces_all_three_tags_and_saves`، `test_re_tagging_with_the_tags_it_has_never_writes`، `test_a_no_op_re_tag_still_sweeps`)، و`test_save_writes_a_retag` الحيّ يغطي SQL. فلا ثغرة الآن، والنمط سابق | لا شيء مطلوب. يُحسن تذكّره في أي اختبار جديد يثبت الكتابة: يُؤكَّد `files.saved` لا حالة الصف |

## مطابقة التصميم

ما فحصتُه بنداً بنداً مقابل LLD والعقود الحاكمة:

- **النطاق (§3.3.1):**
  - `MAX_TAG_CHARS = 128` و`TagKind` بالقيم الثلاث و`FileTags` كما في التوقيع: NFC ثم قص ثم 1..128 بلا محرف تحكّم، و`from_mapping` يرفض المفتاح المجهول، و`as_mapping` للحاضر وحده، و`is_empty`.
  - `File.tags` آخر الحقول، و`File.retag` يعيد `False` بلا كتابة ولا `updated_at` عند عدم التغيير، ويرفض المحذوف بـ`FileStateError`.
  - `FileUploaded.tags` آخر الحقول.
  - `field(default_factory=FileTags)` انحراف مقبول (RUF009).
  - لا استيراد خارج المكتبة القياسية (`lint-imports`: 9 kept).
- **المنافذ والتطبيق (§3.3.2):**
  - `live_namesakes` موثّق بالوسوم، و`live_name_conflicts` جديد. `FileView.tags` بقيمة افتراضية فارغة (`INC1‑D‑30`).
  - `RegisterUpload` يحلّل الوسوم في الكتلة الأولى مع الاسم والنوع، قبل `files.too_many` وقبل `start_multipart`.
  - `RetagFile` بلا حدث (`INC1‑D‑5`)، ولا يحفظ عند عدم التغيير، ويترجم الخطأين إلى 422 و409.
  - `NamesakePreview` و`FindNamesakes.preview`. و`CompleteUpload` ينشر `file.tags` من الصف، و`FilesQueryService.get_readable` يملأ `FileView.tags`. و`names_for_files` لم يتغيّر.
- **الإطار (§3.3.3):**
  - `UploadRegistrar.register` و`SpaceQuotaService.register` يمرّران `tags` دون قراءة.
  - `FileRetagger` و`ReplaceNamesakesService(..., retag=...)` و`retag` يكنس حتى عند عدم التغيير. وفشل الكنس لا يُفشل الطلب، لأن الكنس المشترك `_sweep` نفسه.
  - جذر التركيب يربط نسخة `RetagFile` واحدة في الحزمة وفي الكنس، ويثبت ذلك `test_composition_root.py` بالهوية.
- **السلك (§3.3.4):**
  - `FileTagsIn` بـ`extra="forbid"`، و`FileTagsOut`، و`FileRegisterIn.tags` اختياري، و`FileOut.tags`.
  - `PUT /files/{file_id}/tags` بـ`files:write`، عبر `services.file_replacement` وحده، ويغلق بـ`common.internal` إن لم يُربط.
  - في `openapi.yaml`: `retagFile` والمخططان، ووصفا `completeFile` و`renameFile` بعبارة «الاسم والوسوم»، وسقطت «the ONLY mutable field».
  - مخطط الحدث فيه `tags` بـ`minProperties: 1` و`additionalProperties: false`. و`event_mapping` لا يضع المفتاح إلا حين يوجد وسم، فلا `null` ولا `{}` أبداً.
- **البيانات (§4.1، §4.4):**
  - الهجرة `0006_file_tags` مطابقة حرفياً لـ§4.1: ثلاثة أعمدة `NULL` بلا افتراض، و`ck_files_tags_shape` `NOT VALID` ثم `VALIDATE`، و`downgrade` موثّق بفقد البيانات. توسيع فقط.
  - `save` يكتب الوسوم بجانب `name`، و`add` و`_hydrate` يحملانها.
  - شرط `live_namesakes` هو `AND(col IS NOT DISTINCT FROM v)`. ونفيه في `live_name_conflicts` هو `OR(col IS DISTINCT FROM v)` لا `NOT(col = v)`، فصفّ `NULL` من أحد الطرفين لا يسقط. والشرطان من دالة واحدة `_tag_pairs`، و`_in_space` و`_argument_key` استُخرجتا دون تغيير في المعنى.
  - لا فهرس جديد (`INC1‑D‑4`)، والحارس الحيّ يثبت أن `name_key` باقٍ `Index Cond` والوسوم في `Filter`، على الجملة التي يرسلها المحوّل نفسه.
  - ترتيب `live_name_conflicts` بالمعرّف انحراف مقبول.
- **`INC1‑Q‑1` (ب):** المقارنة حرفية بلا طيّ حالة، على القيمة بعد NFC والقص، في SQL وفي المزيّف معاً.
- **الطبقات والأخطاء:** `application` لا يستورد `sqlalchemy` ولا `fastapi`. والأخطاء كلها `AppError`: `ValidationError` و`ConflictError` و`NotFoundError`. ولا `ignore_imports` جديد.
- **النطاق:** لا ملف خارج قائمة §2.1 و§2.2 إلا `tests/unit/test_files_tags_qa.py` وخطة الاختبار، وهما من QA. ولا تغيير في `deploy/` ولا `.github/` ولا `src/app/agents/rag_agent` ولا `src/app/modules/knowledge` (الفرق فارغ، حصة `PR‑A` من `INC1‑AC‑16`). ولا مستهلك اليوم لـ`files.file.uploaded.v1`، فالحقل الجديد لا يكسر أحداً.
- **الوثائق الحاكمة:** `01` §2.6 و`02` و`03` و`04` و`05` §4 و`06` §6 محدّثة ومتّسقة مع الشيفرة، وعليها الملاحظة 4 وحدها.

## الاختبارات

- **البوابات (شغّلتُها على هذا الفرع):**
  - `ruff format --check`: 870 files already formatted.
  - `ruff check`: All checks passed.
  - `mypy src`: لا أخطاء في 490 ملفاً.
  - `lint-imports`: 9 kept، 0 broken.
  - `pytest -rs tests/unit tests/architecture tests/eval`: خرج بالرمز 0، أي لا فشل. والمتخطّى وحده حزمة الحاويات الاختيارية `test_public_file_links_containers.py` (`RUN_EDGE_FILES_TEST=1`). وتقرير QA يذكر 6651 ناجحاً و19 متخطّى.
  - لم أشغّل `tests/integration` (المكدّس الحيّ مستعمل)، ولا `test-rules.sh`. ونتيجة E3/E4 الحيّة (94 ناجحاً على قاعدة مؤقتة) منقولة من تقرير QA.
- **التغطية:** كل بند في `INC1‑AC‑1..3` له اختبار يفشل بدون التغيير:
  - `test_files_domain.py`: التطبيع والحدود والرفض والمطابقة الحرفية و`retag`.
  - `test_files_use_cases.py`: 422 قبل `files.too_many` وقبل `start_multipart`، و`RetagFile` بفروعه، و`preview`، والحدث من الصف، و`FileView`.
  - `test_file_replacement.py`: الكنس عند الإتمام وإعادة الوسم في الاتجاهين، والكنس بلا تغيير، وفشل الكنس، والرفض قبل الكنس.
  - `test_api_files_media_router.py`: السلك في المسارين، و`PUT …/tags` بكل رموزه، والإغلاق حين لا يُربط الكنس، ومفتاح التكرار.
  - `test_files_event_mapping.py`: المخطط المنشور، ولا `null` ولا `{}`.
  - `test_space_quota.py`: التمرير بالهوية.
  - `test_api_rbac.py` و`test_composition_root.py`: الصلاحية والربط.
  - الاختبارات الحيّة: `test_files_repository_rls.py` للذهاب والإياب والقيد والعزل، و`test_file_namesakes_live.py` للمطابقة بالوسوم و`NULL` من طرف واحد والتقسيم وخطة الاستعلام.
- **النمط:** اختبارات الوحدة على المزيّفات، والحيّة بـ`live_db`. والمزيّف يطبّق القاعدة نفسها: مساواة `FileTags` هي `IS NOT DISTINCT FROM` لكل عمود.
- **عدم التراجع:** الأسطر المحذوفة في الاختبارات القائمة اتساع تواقيع واستيراد فقط، ولم يُخفَّف تأكيد.
- **الثغرة الوحيدة:** حالة الـsurrogate المفرد غير مغطاة (الملاحظة 1).

## إعادة المراجعة 1 (2026-11-01)

- **النطاق:** `git diff ae13a38..080199a` (`f5c48e6`، `080199a`؛ 10 ملفات). والمرجع الـLLD 0.3 غير المودَع، وهو مطابق للشيفرة.
- **البوابات (قراءة فقط):** `ruff format --check` (870)، و`ruff check`، و`mypy src` (490)، و`lint-imports` (9 kept): خضراء كلها. و`pytest` على `test_file_replacement.py` و`test_files_domain.py` و`test_api_files_media_router.py` ونسخة `080199a` من `test_files_tags_qa.py`، ومعها `test_api_rbac.py` و`test_api_conventions.py` و`test_composition_root.py` و`test_files_use_cases.py` و`test_files_event_mapping.py`: ناجحة كلها. والتعديل الجاري غير المودَع على `test_files_tags_qa.py` (من QA في أثناء المراجعة) ليس من هذا النطاق. انظر `security-review.md` «إعادة المراجعة 1».

| # | الحالة | الدليل |
|---|---|---|
| 1 (B‑1) | **مغلقة** | `_clean_tag` يرفض ما لا يُرمَّز UTF‑8 بـ`InvalidFileInput` (`value_objects.py:237-241`)، ثم يرفض الفئات المحظورة (`:242`). والاختبارات: المجال، و`PUT …/tags` (`test_api_files_media_router.py:865`: 422، بلا `save`، بلا صدى)، و`POST /files/multipart` (`:886`: لا رفع يُفتح، ولا صف). و`POST /files` تحقّقتُ منه يدوياً: 422. و`FileName` خارج النطاق كما اتُّفق. |
| 2 (كنس ملف غير جاهز) | **مغلقة** | `rename` و`retag` يكنسان حين `status == "ready"` وحده (`file_replacement.py:231`، `:242`)، و`complete` يكنس دائماً. والمقارنة صحيحة، لأن العائد `File` و`FileStatus` من نوع `StrEnum`. و`HasStatus` بروتوكول بنيوي يُبقي الملف بلا استيراد لوحدة. والاختبارات تغطي `uploaded` و`scanning` و`quarantined`، والإتمام اللاحق يكنس، وعلى المسار الحقيقي أيضاً (`test_file_replacement.py:699`، `:723`، `:742`؛ `test_api_files_media_router.py:965`). ولا تراجع: `_seed` يبدأ افتراضياً بـ`READY`، فاختبارات الكنس القائمة باقية بمعناها. |
| 3 (`maxLength`، `required`) | **مغلقة** | `FileTagsIn` في `openapi.yaml` بلا `maxLength`، ووصفه يذكر الحدّ بعد التطبيع والقص، وهذا مطابق لـ`FileTagsIn` في التطبيق (بلا `maxLength`). و`FileTagsOut.required: [system, equipment, department]` (`openapi.yaml:1239`)، وهو مطابق لمخطط pydantic (الحقول الثلاثة بلا افتراض). والاختبار `test_one_tag_limit_…` يثبت الحالتين. ومخطط الحدث يُبقي `maxLength: 128`، وهذا صحيح لأنه يحمل القيمة المطبَّعة. |
| 4 (ترتيب 422) | **مغلقة** | وصف `FileRegisterIn` (`openapi.yaml:1196`) صار: «before any write, any upload URL and any multipart start»، ويذكر أن 404 و409 قد يسبقان. وكذلك `03` والـLLD 0.3 (§3.3.2، و`INC1‑AC‑1`). |
| 5 | بلا تغيير | لا شيء مطلوب. |

**ملاحظات جديدة (تحسين، غير حاجبة):**

| # | الشدة | الملف:السطر | الملاحظة | الإصلاح |
|---|---|---|---|---|
| 6 | تحسين | `src/app/modules/files/domain/entities.py:166-167`؛ `src/app/framework/di/file_replacement.py:73-76`؛ `src/app/api/v1/dto/files.py:27-28` | **نصوص توثيق قديمة بعد التغيير:** `File.retag` يقول «the same-name sweep runs either way». ورأس `file_replacement.py` يقول إن إعادة التسمية بلا تغيير «still sweeps here» دون قيد «ready». و`FileTagsIn` يقول «no control character». | تُحدَّث الثلاثة إلى القاعدة الجديدة: الكنس للملف `ready` وحده، والفئات Cc وCf وCs وCo وCn وZl وZp. |
| 7 | تحسين | `tests/unit/test_api_files_media_router.py:865-903`؛ `tests/unit/test_file_replacement.py:723` | لا اختبار مسار لـ`POST /files` (رفع واحد) بالبديل المنفرد، والسلوك صحيح كما تحقّقتُ. وإعادة التسمية مختبرة على `uploaded` وحده، أما إعادة الوسم فعلى `uploaded` و`scanning` معاً. | حالة واحدة لـ`POST /files`، و`parametrize` على `status` في اختبار إعادة التسمية. |

- **الحكم:** **PASS.** الملاحظات 1 إلى 4 مغلقة، ولا حاجبة ولا مهمة جديدة، وتحسينان (6 و7).

</div>
