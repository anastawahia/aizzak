<div dir="rtl">

# مراجعة الشيفرة: ث‑7 التثبيت على ملف محذوف

- **النطاق المراجَع:** `git diff master...feat/data-search-deleted-pins` (`137874c...82f0d26`) في `/home/AIZZAK`، 23 ملفاً. قُرئ كل ملف معدَّل في `src/` كاملاً حول المواضع المعدلة، والأقسام الجديدة في الاختبارات مع مساعداتها القائمة، والاختبار الحي قراءةً ساكنة (لا يُشغَّل محلياً، CLAUDE.md).
- **الحكم:** **PASS**. لا ملاحظة حاجبة. واحدة مهمة، والباقي تحسين.

## الملاحظات

| # | الشدة | الملف:السطر | الملاحظة | الإصلاح المطلوب |
|---|---|---|---|---|
| 1 | مهمة | `tests/unit/test_conversations_use_cases.py:1818-1841` | `test_qa_the_files_check_runs_under_the_callers_own_context` يقول في الـ docstring «Tenant isolation at the seam»، ثم يطلب قائمة محادثة W1 بسياق W2 (`:1840`) ويتوقع `[]`. هذه حالة لا تحدث في الإنتاج: `_FakeConversations.get` يتجاهل `ctx` (`:65`، «ignores ctx»)، والمحوّل الحقيقي يعيد `None` فيكون الرد `NotFoundError`. وهذا ما يثبته الاختبار الحي نفسه (`test_deleted_pins_live.py:307-308`). فالاختبار يثبت تمرير `ctx` فقط، ويوثّق سلوكاً مستحيلاً. و`test-plan.md:41` يعدّه «عزلاً بوحدة QA» لـ AC‑5.3، وهذا يبالغ في التغطية | أبقِ تأكيد التمرير وحده: محادثة في W1 تُقرأ بسياق W1، وتأكيد `seen == ["w1"]`. أو ازرع محادثة ثانية في W2 بتثبيت على "A" وتأكد أن قائمتها `[]`. وصحّح الـ docstring إلى «the bulk read is asked with the caller's own ctx»، وسطر AC‑5.3 في `test-plan.md` |
| 2 | تحسين | `tests/unit/test_conversations_use_cases.py:1609`، و`:1754` | `match=r"\(currently 50\)"` يطابق جزءاً من رسالة الحد. لو انقلب النصف الآخر من الرسالة (مثلاً `at most {len(live)}` بدل `MAX_PINNED_FILES`) لبقي الاختبار أخضر | قارن النص كاملاً: `match=rf"^a conversation may pin at most {MAX_PINNED_FILES} files \(currently {live}\)$"` |
| 3 | تحسين | `tests/unit/test_conversations_use_cases.py:1652` | `assert "A" not in str(excinfo.value)` يفحص حرفاً واحداً. ينجح اليوم لأن الرسالة «the pinned files could not be checked» بلا A كبيرة، وسيفشل خطأً لو أُضيفت كلمة فيها A، دون أن يكون في الرد معرّف ملف | `assert str(excinfo.value) == "the pinned files could not be checked"`، أو ثبّت بمعرّف UUID حقيقي (`new_uuid7()`) وافحص غيابه |
| 4 | تحسين | `tests/unit/test_orchestrator.py:3695`، و`:3846`، و`:3885`، و`:3906` | `_T7_APOLOGY` بادئة منسوخة من `_FALLBACK_ANSWER_EN` (`src/app/agents/rag_agent/agent.py:156`)، والتأكيد `startswith`. وفي AC‑2.2 (`:3846`) هو نفي فقط (`not startswith`)، فيمر أي نص ليس اعتذاراً | قارن النص كاملاً: الاعتذار هو الجملة كلها `"I don't have enough information in the workspace documents to answer that."`. وفي `:3846` أكّد إيجاباً `data["text"] == "answer"` (ما يبثّه `_T7LLM`) |
| 5 | تحسين | `src/app/modules/conversations/application/use_cases.py:942-952` | docstring `ConversationService.pinned_files` يقول «Missing thread ⇒ ``()``» مرتين متتاليتين: في الفقرة الجديدة (`:945`) وفي الفقرة القائمة بعدها (`:948`). الكثافة صارت أعلى مما حولها بلا معلومة جديدة. (التصميم §3.8 طلب الإضافة مع إبقاء الفقرة القائمة، فالتكرار من النص المقرر) | احذف «Missing thread ⇒ ``()``;» من الفقرة الجديدة، وأبقِ «a failed files check RAISES …» |
| 6 | تحسين | `tests/integration/test_deleted_pins_live.py:79-86`، و`:170` | `_RecordingVectors` و«recording only» لا يسجلان شيئاً (الطريقتان تعيدان `None`). والاسم منقول من `test_file_cascade_live.py:89`، وهناك يسجّل `deleted` فعلاً. وفي `:170` `type: ignore[arg-type]` لا يلزم (`mypy` على الملف: unused-ignore). أما `:133-136` فمثلها في الملف المرجعي، وليست جديدة | سمِّه `_NoopVectors` وصحّح الـ docstring، واحذف التعليق في `:170` |

**ليست ملاحظات على هذا الفرع:**
- **AC‑6.3 (`gaps.md`):** مؤجل عمداً إلى ما بعد اخضرار CI (`design.md` §6 الخطوة 7). يبقى مفتوحاً حتى يُكتب.
- **docstring `_pinned_scope`** (`src/app/agents/orchestrator.py:2827-2836`) ما زال يقول «the whole workspace corpus». هذا سابق للفرع (منذ س‑32)، والتصميم يمنع لمس `agents/`. يُصحَّح في commit مستقل إن رأى المالك ذلك.
- **`lint-imports` يعدّ 9 عقود** و`CLAUDE.md` يقول 8. فرق سابق للفرع، و`.importlinter` بلا تعديل.

## مطابقة التصميم

**المنطق** (`use_cases.py:621-640`، `:739-750`):
- **`_live_pins`:**
  - يعيد `[]` بلا استدعاء حين لا تثبيت.
  - وإلا يستدعي `names_for_files` مرة واحدة بكل المعرّفات وبترتيب المستودع.
  - ويُبقي كائنات `PinnedFile` نفسها بترتيبها. فـ`pinned_at` هو `created_at` المخزن كما هو، ولا نسخ ولا إعادة بناء.
  - تحققت أن نوعي المعرّف متطابقان في الطرفين: `Uuid(as_uuid=False)` في `files/adapters/sql_repository.py:78` و`conversations/adapters/sql_repository.py:127`. فاختبار العضوية `pin.file_id in live` يقارن نصاً بنص، لا `UUID` بنص.
- **حد الخمسين:**
  - يُقرأ مجمّعاً فقط حين `len(pinned) >= MAX_PINNED_FILES`. وهذا صحيح منطقياً، لأن الحي جزء من المخزن، فمخزن أقل من 50 يعني حياً أقل من 50.
  - `already` يُحسب من الحي. فإعادة تثبيت ملف حي عند الحد تمر.
  - وتثبيت ملف محذوف له صف يتيم يصل إلى `get_readable`، فيُرد 422. وعند الحد قد يُرد 422 برسالة الحد بدل رسالة «not readable»، والرمز واحد (`common.validation_error`)، فلا أثر على العقد.
  - تحت الحد صار `already` لا يُحسب أصلاً. وهذا لا يغير جواباً، لأنه كان لا يؤثر إلا مع الحد.
- **حارس `NotFoundError`:**
  - يتحول إلى `AppError(code="common.internal")`، والرمز وحده يحدد 500 (`framework/errors.py`). والرسالة بلا معرّف.
  - `ConversationService.pinned_files` يلتقط `NotFoundError` وحده. فالمحادثة المجهولة تعطي `()`، وأي فشل آخر من منفذ الملفات يخرج ولا يصير `()`.
  - `FilesQueryService.names_for_files` الحقيقي يمرر إلى `ready_names`، ولا يرفع إلا `AppError(common.internal)` عبر `_translate` (`files/adapters/sql_repository.py:573-593`). فالحارس دفاعي كما يقول د‑ت4.
- **السباق داخل الدور:** المنسّق بلا تغيير. `_pinned_scope` يُقرأ مرة واحدة (`orchestrator.py:1632`)، قبل `_admit` (`:1657`) و`_open_turn` (`:1681`)، ولا قارئ آخر لـ`pinned_files` في `src`. فالدور الجاري لا يتسع، وفشل القراءة يخرج قبل الحجز وكتابة رسالة المستخدم. ومسار WebSocket (`api/v1/websocket/streaming.py:397-407`) يلتقط `AppError` قبل الضخ، فيصير خطأ مقبس لا توسعاً.
- **حالات الملف:** `READY` نهائية في الكيان (`files/domain/entities.py:86-115`، لا انتقال منها إلا الحذف). فلا نافذة عابرة يختفي فيها تثبيت حي ثم يعود ويتسع الدور أثناءها.

**العقود والمعمارية:**
- `ReadableFiles.names_for_files` مطابق حرفاً لتوقيع `FilesQuery.names_for_files`. والربط في `composition_root.py:1342` بالنسخة نفسها التي يأخذها `PinConversationFile`.
- `mypy src` نظيف (552 ملفاً)، و`lint-imports` 9 kept و0 broken. ولا `ignore_imports`، ولا استيراد من `app.modules.files` في `conversations` (يظهر الاسم في docstring قائم فقط، `ports/files.py:64`).
- `git diff --stat master...HEAD` على `migrations/`، و`src/app/modules/files/`، و`file_deletion.py`، و`src/app/agents/`، و`.importlinter`، و`deploy/`، و`.github/`، و`.env.example`: فارغ.
- قائمة الملفات المعدلة مطابقة لجدول `design.md` §2. الفرق `gaps.md` وحده، وهو مؤجل بخطة.
- الوثائق الملزمة (`openapi.yaml` العمليات الثلاث، و`01 §2.4`، و`02 §2`، و`03 §1`) مطابقة لنصوص §3.1 و§3.9. ولا تبقى «retrieval simply finds nothing for it». و`default: Problem` يغطي 500 الجديد بلا تعديل في `responses`.
- grep عبارات ما قبل ث‑7 في `src/app/modules/conversations` و`src/app/api` فارغ (AC‑6.2).
- **الأعراف:** المعرّفات في الوثائق المضافة بـ U+2011 (الاستثناء الوحيد داخل كتلة شيفرة في `design.md`). والتعليقات والـ docstrings بالإنجليزية بـ«gap th‑7»، ورؤوس أقسام الاختبار «ث-7» لها سابقة (ث‑1، ث‑2، ث‑5).

## الاختبارات

- **البوابات التي شغّلتها:** `ruff format --check`، و`ruff check`، و`mypy src`، و`lint-imports` خضراء. والملفات الخمسة المتأثرة (`test_conversations_use_cases`، و`test_api_conversations_router`، و`test_orchestrator`، و`test_composition_root`، و`test_workflow_orchestration`) مع `test_api_conventions`: 575 ناجحاً، exit 0.
- **هل تفشل بلا التغيير؟** نعم:
  - `test_the_bound_counts_and_reports_live_pins_only` كان سيرى «(currently 55)».
  - اختبارات الإخفاء كلها كانت سترى الصف اليتيم.
  - `test_a_not_found_from_the_files_seam_is_never_an_unknown_thread` كان سيرى `NotFoundError`.
  - وفحص الطفرات الخمس في `test-plan.md` §3 يؤكد ذلك.
  - الاستثناء المقصود هو `test_a_fully_live_list_is_byte_identical`، فهو مرجع على سلوك `master` (AC‑1.5).
- **المزيّفات:** `StubReadableFiles.names_for_files` و`_FakeReadableFiles.names_for_files` يعيدان `{fid: fid}` للحاضر في `ready`. والحقيقي يعيد `{id: name}` بالشرط نفسه («ready وغير محذوف»)، وهذا وفيّ لدلالة الحضور التي يقرؤها الكود. والمزيّفان لا يحترمان `ctx`، ويغطي ذلك الاختبار الحي، وانظر الملاحظة 1. و`_UnusedFiles` يرفع في الطريقتين، فأي قراءة ملفات من مسار workflow تفشل بصوت.
- **AC‑4.4:**
  - الراوتر يثبت الفشل في `stream=true/false`: 500 problem+json بلا `event: accepted`، ولا رسالة مستخدم، ولا تشغيل.
  - وإعادة الطلب بالمفتاح نفسه تنجح، أي أن `Idempotency-Key` حُرّر.
  - والمنسّق يثبت `reserved == []` ولا استدعاء معرفة.
- **AC‑4.1:** `_ScopedKnowledge.before_answer` يحذف الملف داخل الاسترجاع. والتأكيد `scopes == [("A",)]` لا `[None]`، والنموذج لم يُستدعَ، والدور التالي `None`.
- **AC‑2.4:** تعداد كامل (243 حالة) يؤكد المساواة بين القائمة والنطاق، ويؤكد المحتوى المتوقع أيضاً، لا المساواة وحدها.
- **الاختبار الحي** `tests/integration/test_deleted_pins_live.py` (قراءة ساكنة):
  - **يمر بالمحوّلات الحقيقية:**
    - `SqlFileRepository` ← `FilesQueryService` واحد، يأخذه `PinConversationFile` و`ListConversationFiles`.
    - `SqlConversationRepository`.
    - `DeleteFileService` بتوصيل جذر التركيب إلا Qdrant.
  - **البذر:** الملفات عبر `RegisterUpload`/`CompleteUpload`، والمحادثة عبر `StartConversation`.
  - **RLS:** `tenant_session` على دور `app_rw` (`conftest.py:600-618`)، لا المالك. فالقراءة المجمّعة بسياق W2 تمر عبر RLS حقاً (`:306`). والقائمة بسياق W2 تعطي `NotFoundError` (`:307-308`).
  - **الصفوف بصفة المالك:** قراءتها وإدراجها تضبط `app.workspace_id` داخل المعاملة نفسها، بنمط `test_purge_ops_live.py`.
  - **لا يتخطى بصمت:** السلسلة `tenant_session` ← `app_engine` ← `live_db` ← `_skip_unless_live`، وفي CI `REQUIRE_LIVE=1` يحوّل التخطي فشلاً. وأمر pytest في CI (`ci.yml:181-188`) لا يستبعد `live_db`.
  - **ما لا يُثبت إلا هنا:** AC‑3.4 وAC‑5.3، ومعهما نصف AC‑3.2/3.7 الحي. شرطها اخضرار CI `integration` قبل الدمج.

</div>
