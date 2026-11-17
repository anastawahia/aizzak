<div dir="rtl">

# التصميم التقني: ث‑7 التثبيت على ملف محذوف

- **المدخلات الملزمة:**
  - `requirements.md` و`stories.md` في هذا المجلد (6 قصص، 35 معياراً، FR‑1…FR‑18، NFR‑1…NFR‑8).
  - قرار بوابة ① في `status.md` (2026‑11‑17): ق‑1، أي الموافقة على كل توصيات ت‑1…ت‑7 وس‑1…س‑8.
- **الشيفرة التي فُحصت:** `master` على `137874c`، والفرع `feat/data-search-deleted-pins` منه. أرقام الأسطر أدناه منه.
- **المرجع الأقرب الذي نتبعه:**
  - **توسيع منفذ مستهلِك بـ`names_for_files`:** `knowledge/ports/files.py:83-100` و`electrical/drawings/ports/files.py:46`. كلاهما أعلن الطريقة نفسها التي في `FilesQuery` (`files/ports/inbound.py:68-101`) على منفذه الخاص، وربطها جذر التركيب بالنسخة نفسها من `FilesQueryService` بلا محوّل. وmypy يفحص الربط في سطر الاستدعاء.
  - **ربط منفذ `conversations` القائم:** `_build_conversations(..., files: ReadableFiles, ...)` (`framework/di/composition_root.py:1305-1373`)، ويُمرَّر إليه `files_query` في `:2251`.
  - **اختبار حي يربط الوحدتين كما يربطهما جذر التركيب:** `tests/integration/test_pin_space_rule_live.py` (الملفات والمحادثات الحقيقية)، و`tests/integration/test_file_cascade_live.py:158-175` (`DeleteFileService` الحقيقي بلا Qdrant).
  - **تصميم ث‑5** (`docs/delivery/data-search-history-cursor/design.md`): جدول ملفات بمنفّذ واحد لكل ملف، واختبار مرجعي يُكتب ويمرّ قبل تعديل `src`، والاختبارات الحية في CI فقط، و`gaps.md` بعد اخضرار CI.

## 1. الملخص

الميزة **فلترة عند القراءة** داخل وحدة `conversations` وحدها، في طبقة application. لا ترحيل، ولا حدث، ولا إعداد، ولا سر. ولا تغيير في وحدة `files`، ولا في المنسّق، ولا في `rag_agent`، ولا في `knowledge`، ولا في تسلسل حذف الملف.

1. **المنفذ** `conversations/ports/files.py`: يُضاف إلى `ReadableFiles` سطر واحد، `names_for_files(ctx, file_ids) -> Mapping[Uuid, str]`، بتوقيع `FilesQuery.names_for_files` نفسه. فيرضيه `FilesQueryService` القائم بنيوياً، ولا يُكتب محوّل. وحضور المعرّف في الجواب يعني أن الملف حي (`ready` وغير محذوف، `files/adapters/sql_repository.py:342-351`). أما الاسم فلا يُقرأ.
2. **application** `conversations/application/use_cases.py`:
   - دالة خاصة `_live_pins(files, ctx, pinned)`. تعيد التثبيتات التي ملفها حي، بالترتيب الذي جاءت به. ولا تستدعي المنفذ إن كانت القائمة فارغة (NFR‑2).
   - `ListConversationFiles` يأخذ `files` في مُنشئه، ويمرّ كل ما يقرؤه بـ`_live_pins`. وهو المصدر الوحيد للقائمة (`GET …/files`) ولنطاق الدور (`ConversationService.pinned_files`)، فالاثنان جواب واحد (FR‑3).
   - `PinConversationFile` يعدّ التثبيتات الحية وحدها مقابل `MAX_PINNED_FILES`. ولا يقرأ الملفات المجمّعة إلا حين يبلغ عدد الصفوف الخام 50 (د‑ت3).
3. **جذر التركيب:** سطر واحد، `ListConversationFiles(repository, files)`، بالنسخة نفسها التي يأخذها `PinConversationFile`.
4. **المنسّق:** بلا تغيير. يقرأ النطاق مرة واحدة في التحضير (`orchestrator.py:1632`)، قبل حجز الاستهلاك (`:1657`) وقبل كتابة رسالة المستخدم (`:1681`). فيتحقق بلا سطر جديد أمران:
   - الدور الجاري لا يتسع إن حُذف ملفه بعد التحضير (FR‑12).
   - فشل قراءة الملفات يُفشل الدور قبل أن يُكتب شيء أو يُحجز شيء (FR‑14).
5. **العقد والوثائق:** أوصاف العمليات الثلاث في `openapi.yaml`، و`01 §2.4`، و`02 §2`، و`03 §1`، والـ docstrings التي تقول إن الفحص «لا يُعاد عند القراءة». ثم `gaps.md` بعد اخضرار CI.

**الدلالة بعد التغيير:**

| حال تثبيتات المحادثة | `GET …/files` | نطاق الدور | `file_ids` في `knowledge.answer` |
|---|---|---|---|
| لا صفوف | `data: []`، `limit: 0`، بلا قراءة للملفات | `()` | `None` (كل مساحة المحادثة) |
| كلها حية | كما اليوم بايتاً ببايت | كل المعرّفات بالترتيب | المعرّفات نفسها |
| بعضها يتيم | الحية وحدها، بترتيبها و`pinned_at` الأصلي | الحية وحدها | الحية وحدها |
| كلها يتيمة | `data: []`، `limit: 0` | `()` | `None` (كل مساحة المحادثة) |
| قراءة الملفات فشلت | 500 `common.internal` | — (الدور يفشل قبل `accepted`) | لا استدعاء |

## 2. الملفات

منفّذ واحد لكل ملف هو backend-developer. لا ملف يعدّله اثنان. ولا مرحلة لـ data-engineer.

| المسار | إنشاء/تعديل | المسؤولية | المنفّذ |
|---|---|---|---|
| `src/app/modules/conversations/ports/files.py` | تعديل | `ReadableFiles.names_for_files` (§3.3)، وفقرتا docstring الوحدة والصنف (§3.8) | backend-developer |
| `src/app/modules/conversations/application/use_cases.py` | تعديل | `_live_pins` (§3.4)، و`ListConversationFiles(conversations, files)`، والحدّ في `PinConversationFile.execute` (`:702-707`)، وdocstrings `ListConversationFiles` و`PinConversationFile` (`:663-670`) و`ConversationService.pinned_files` (`:892-904`) (§3.8) | backend-developer |
| `src/app/modules/conversations/domain/entities.py` | تعديل (docstring فقط) | `PinnedFile` (`:285-295`) (§3.8) | backend-developer |
| `src/app/modules/conversations/ports/inbound.py` | تعديل (docstring فقط) | `ConversationThreads.pinned_files` (`:179-200`) (§3.8) | backend-developer |
| `src/app/modules/conversations/ports/repository.py` | تعديل (docstring فقط) | جملة عن `list_files` في docstring الصنف (`:54-62`) (§3.8) | backend-developer |
| `src/app/framework/di/composition_root.py` | تعديل | `ListConversationFiles(repository, files)` (`:1341`)، وجملة في docstring `_build_conversations` (`:1316-1321`) | backend-developer |
| `src/app/api/v1/routers/conversations.py` | تعديل (docstring فقط) | `list_conversation_files` (`:515-524`)، ويصير `description` في `/openapi.json` الحي (§3.8) | backend-developer |
| `docs/design/openapi.yaml` | تعديل | أوصاف `listConversationFiles` (`:314`) و`pinConversationFile` (`:322`) و`unpinConversationFile` (`:333`) (§3.1) | backend-developer |
| `docs/design/01-data-model.md` (§2.4) | تعديل | تعليق الجدول (`:233-236`) (§3.9) | backend-developer |
| `docs/design/02-port-contracts.md` (§2) | تعديل | كتلة `ReadableFiles` (`:570-577`) (§3.9) | backend-developer |
| `docs/design/03-api-spec.md` (§1) | تعديل | تعليق `…/files` (`:156-167`) (§3.9) | backend-developer |
| `docs/delivery/data-search-android/gaps.md` | تعديل | سطر الحالة (`:11`)، وفقرة «الإصلاح» لـ ث‑7 بعد `:69`. **بعد اخضرار البوابات وCI** (نمط ث‑3 وث‑5) | backend-developer |
| `tests/unit/support_conversations.py` | تعديل | `StubReadableFiles.names_for_files` و`bulk_calls` و`bulk_error`، و`ListConversationFiles(repository, readable)` في `build_conversations` (`:346`)، والـ docstring | backend-developer |
| `tests/unit/test_conversations_use_cases.py` | تعديل | `_FakeReadableFiles.names_for_files` و`bulk_calls` و`bulk_error` و`on_get_readable`، والمواضع الأربعة لـ`ListConversationFiles(conversations)` (`:897`، `:994`، `:1001`، `:1056`)، وقسم جديد «ث‑7» | backend-developer |
| `tests/unit/test_workflow_orchestration.py` | تعديل | `ListConversationFiles(repo, _UnusedFiles())` (`:908`)، و`_UnusedFiles` يرفع `AssertionError` في الطريقتين | backend-developer |
| `tests/unit/test_api_conversations_router.py` | تعديل | قسم جديد «ث‑7» في آخر الملف. الاختبارات القائمة لا تُعدَّل | backend-developer |
| `tests/unit/test_orchestrator.py` | تعديل | قسم جديد «ث‑7» في آخر الملف: `rag_agent` الحقيقي من شجرة الإضافات، و`ConversationService` الحقيقي من `build_conversations`، ومزيّف معرفة يحترم النطاق | backend-developer |
| `tests/unit/test_composition_root.py` | تعديل | `test_the_pin_list_and_bound_share_one_files_seam` (§5) | backend-developer |
| `tests/integration/test_deleted_pins_live.py` | **إنشاء** | `live_db`، في CI job `integration` فقط (§5) | backend-developer |

**ما لا يتغير، عمداً:**
- **`migrations/`:** لا ملف جديد ولا معدّل (NFR‑1، AC‑3.7). وملف `0003_conversation_files.py` لا يُمس، ومنه docstring الذي يصف ما قبل ث‑7 (ت‑2).
- **`src/app/modules/files/`:** لا تغيير (AC‑5.6). `names_for_files` و`ready_names` قائمتان ومختبرتان حياً (`tests/integration/test_files_repository_rls.py:716-800`).
- **`src/app/framework/di/file_deletion.py`:** لا تغيير ولا حتى في docstring (AC‑3.7). جملة `:143-144` («a pin to a deleted file already resolves to nothing») تصير أصدق بعد الميزة، فلا داعي لمسها.
- **`src/app/agents/`:** المنسّق و`rag_agent` بلا تغيير (§1 البند 4).
- **`src/app/modules/conversations/adapters/sql_repository.py`:** `list_files` تبقى تعيد الصفوف الخام. الفلترة في application لأن المحوّل لا يملك منفذ الملفات (د‑ت1).
- **`ConversationFileOut` و`ConversationFilePage`:** بلا تغيير (NFR‑6، س‑6).
- **`.importlinter`، و`docs/design/events/schemas/`، و`deploy/`، و`.github/workflows/`، و`.env.example`:** بلا تغيير.
- **مستودع Android و`backend-reply.md`:** لا يُمسّان (س‑7، AC‑6.4).

## 3. العقود

### الواجهات البرمجية

لا مسار جديد، ولا معامل جديد، ولا حقل جديد، ولا رمز خطأ جديد (A‑4). يتغير السلوك وحده:

| الطريقة | المسار | الطلب | الاستجابة | الأخطاء | الصلاحية |
|---|---|---|---|---|---|
| GET | `/api/v1/conversations/{id}/files` | بلا تغيير | 200 `ConversationFilePage` بلا تغيير في الشكل. `data` = **التثبيتات الحية وحدها** بترتيب `(created_at, file_id)` و`pinned_at` الأصلي. `meta.next_cursor = null`، و`meta.limit` = عدد العناصر المعادة | 401 بلا توكن. 403 `authz.forbidden`. 404 `common.not_found` لمحادثة مجهولة أو من مستأجر آخر (قبل أي قراءة للملفات). **500 `common.internal`** إن فشلت قراءة الملفات المجمّعة (جديد، FR‑14) | `conversations:read` |
| POST | `/api/v1/conversations/{id}/files` | بلا تغيير | 201 `ConversationFileOut` بلا تغيير | 404، و409 `common.conflict` لمحادثة محذوفة ناعماً، و409 `spaces.cross_space_pin`، كما اليوم. 422 `common.validation_error` لملف مجهول أو غير جاهز أو محذوف، **حتى إن كان له صف يتيم** (FR‑9). 422 للحد حين يبلغ عدد **التثبيتات الحية** 50، ورسالته تذكر العدد الحي (FR‑8). 500 `common.internal` إن فشلت القراءة المجمّعة وقد بلغت الصفوف الخام 50 | `conversations:write` |
| DELETE | `/api/v1/conversations/{id}/files/{file_id}` | بلا تغيير | 204 بلا تغيير، ومنه فك تثبيت يتيم ثم تكراره (FR‑10). لا يقرأ الملفات | 404، و409 لمحادثة محذوفة ناعماً، كما اليوم | `conversations:write` |
| POST | `/api/v1/conversations/{id}/messages` (و`/agents/{key}/invoke` مع `conversation_id`) | بلا تغيير | بلا تغيير. نطاق الدور = القائمة أعلاه | **500 `common.internal` problem+json قبل أي إطار `accepted`**، في `stream=true` و`stream=false`، إن فشلت القراءة المجمّعة في التحضير. لا تُكتب رسالة المستخدم، ولا يُحجز استهلاك، ويُحرَّر `Idempotency-Key` (FR‑14) | `conversations:write` (بلا تغيير) |
| DELETE | `/api/v1/files/{id}` | بلا تغيير | 204 بلا تغيير. لا يكتب في `conversations` | بلا تغيير (FR‑11) | `files:delete` |

**ترتيب الحراسات في `GET …/files` (FR‑14، AC‑1.6):** المصادقة (401)، ثم الصلاحية (403)، ثم `conversations.get` (404)، ثم `list_files`، ثم القراءة المجمّعة إن كانت الصفوف غير فارغة. فلا يقرأ الملفاتِ طلبٌ مرفوض.

**ترتيب التحضير في الدور (بلا تغيير، `orchestrator.py:1601-1690`):** `_authorize`، ثم `_pinned_route`، ثم **`_pinned_scope`** (القراءة المجمّعة هنا)، ثم `_turn_space`، ثم `_pending_clarification`، ثم `_resolve_llm`، ثم `_admit` (الحجز)، ثم `_open_turn` (رسالة المستخدم)، ثم `_note_accepted`. فالاستثناء من القراءة المجمّعة يخرج من `invoke`/`invoke_once` قبل `StreamingResponse` (`routers/conversations.py:441-449`). يعالجه `_handle_app_error` أو `_handle_unexpected` (`api/main.py:530`، `:598`) فيصير problem+json. ويحرّر `post_message` المفتاح (`:423-425`).

#### 3.1 `docs/design/openapi.yaml`

**`listConversationFiles` (`:314`)، يُستبدل `description` كله بـ:**

```yaml
      description: "The files this thread's retrieval is PINNED to -- live pins only. Files themselves stay workspace-level (uploaded once, indexed once, reusable) — a pin is a retrieval SCOPE, not ownership: with pins, `rag_agent` searches only the documents built from these files; with none, it searches every indexed document of the thread's own space, which is every existing thread's behaviour and stays the default. A pin counts only while its file is live (`status=ready` and not deleted): once the file is deleted (`DELETE /files/{id}`, a same-name replacement included) its pin is no longer listed here and no longer narrows retrieval, and when no live pin is left the thread searches its whole space again, exactly as after unpinning the last file. This list and the scope the next turn retrieves from are one read, so what is listed is what is searched; a turn already running keeps the scope it started with and never widens. If the files cannot be checked the answer is 500 `common.internal`, never an unchecked list. Returns references (`file_id`) and not file metadata, because the pin belongs to `conversations` and the name/size/status belong to `GET /files` — the client joins them by id, exactly as it already joins knowledge status. The set is bounded, so the page carries every pin, `meta.next_cursor` is always null and `meta.limit` is the number of pins returned. Permission `conversations:read`."
```

**`pinConversationFile` (`:322`)، تُستبدل الجملة «A pinned file that is later deleted is not an error — retrieval simply finds nothing for it, the same as a file that was never indexed.» بـ:**

```text
At most 50 live pins per thread: a new pin beyond that is 422, while re-pinning a file already pinned still passes. A pinned file that is later deleted is not an error either: its pin stops being listed, stops narrowing retrieval and stops counting toward the 50 (see `listConversationFiles`); pinning that deleted file again is the 422 above.
```

**`unpinConversationFile` (`:333`)، يُستبدل `description` كله بـ:**

```yaml
      description: "Remove one pin. Deletes NO file and no index — it only widens this thread's retrieval scope back out, and unpinning the last one returns it to every indexed document of the thread's space. Idempotent (204 whether or not the pin was there), and that includes the pin of a file that has since been deleted: such a pin is no longer listed, and unpinning it is still 204. Soft-deleted thread ⇒ 409, not 404. Permission `conversations:write`, not `conversations:delete`: nothing is destroyed."
```

- العبارة «the whole workspace corpus» في الوصفين الأول والثالث تُستبدل بـ«every indexed document of the thread's space»، لأن النطاق الشامل هو مساحة المحادثة منذ س‑32 (FR‑6، AC‑6.1 البند 2). انظر د‑ت8.
- لا تغيير في `parameters` ولا `responses` ولا `components`. واختبار العقد (`tests/unit/test_api_conventions.py`) يقارن البنية لا الأوصاف، فيمر بلا تعديل.

#### 3.2 `/openapi.json` المولَّد وعميل Android

- `description` الحي لـ`GET …/files` يأتي من docstring الراوتر (§3.8). لا نوع ولا معامل يتغير، فلا يتغير المولَّد بنيوياً.
- لا frontend في هذا المستودع. وعميل Android لا يحتاج إعادة توليد: الشكل لا يتغير، والتغيير غير كاسر (`API‑05`). ومنطق «التثبيت اليتيم» إن بناه العميل على `backend-reply.md:146` لن يجد يتيماً بعد الآن، ولا ينكسر شيء (§5.7 في `requirements.md`).

### التواقيع

#### 3.3 المنفذ: `src/app/modules/conversations/ports/files.py`

```python
from collections.abc import Mapping, Sequence
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


class ReadableFiles(Protocol):
    """Structurally satisfied by ``app.modules.files.ports.inbound.FilesQuery``. ..."""

    async def get_readable(self, ctx: ExecutionContext, file_id: Uuid) -> ReadableFile | None: ...

    async def names_for_files(
        self, ctx: ExecutionContext, file_ids: Sequence[Uuid]
    ) -> Mapping[Uuid, str]:
        """Which of ``file_ids`` are still LIVE files, in ONE read (gap th-7).

        Presence is the whole answer: an id in the mapping names a file that
        is ``ready`` and not deleted, exactly what a non-``None``
        ``get_readable`` means, and an absent id -- deleted, quarantined,
        unknown, or another tenant's -- is a pin this thread no longer
        answers from. The NAME is never read here: it is file metadata this
        module has no business projecting (module docstring), and the method
        is ``FilesQuery``'s own so the binding stays one line and one
        instance (the ``knowledge`` port's precedent).
        """
        ...
```

- **التوقيع مطابق حرفاً لـ`FilesQuery.names_for_files`** (`files/ports/inbound.py:68-70`) ولـ`knowledge/ports/files.py:83-85`. فيرضيه `FilesQueryService` (`files/application/use_cases.py:569-577`) بلا محوّل، ويفحص mypy ذلك عند `_build_conversations(..., files_query, ...)` (`composition_root.py:2251`) (AC‑5.5).
- `app.modules.conversations` لا يستورد `app.modules.files` (العقد 4 `modules-independent`، `.importlinter:89`). ولا `ignore_imports` (AC‑5.4).

#### 3.4 application: `src/app/modules/conversations/application/use_cases.py`

يضاف `AppError` إلى استيراد `app.framework.errors` القائم (`:30`)، ولا استيراد غيره.

```python
async def _live_pins(
    files: ReadableFiles, ctx: ExecutionContext, pinned: Sequence[PinnedFile]
) -> list[PinnedFile]:
    """The pins among ``pinned`` whose file is still live, in ``pinned``'s order (gap th-7).

    ONE bulk read for the whole set, and none at all for an empty one: a thread
    that pins nothing -- almost every thread -- pays nothing for this rule.
    """
    if not pinned:
        return []
    try:
        live = await files.names_for_files(ctx, [pin.file_id for pin in pinned])
    except NotFoundError as exc:
        # A 404 out of the files seam is NOT "this thread is unknown". Letting it
        # through would make `ConversationService.pinned_files` answer `()` --
        # an unscoped turn -- which is the one thing a failed check must never
        # do (gap th-7, FR-14), and would make `GET …/files` a 404 on a thread
        # that exists. No file id goes into the detail.
        raise AppError("the pinned files could not be checked", code="common.internal") from exc
    return [pin for pin in pinned if pin.file_id in live]


class ListConversationFiles:
    def __init__(self, conversations: ConversationRepository, files: ReadableFiles) -> None:
        self._conversations = conversations
        self._files = files

    async def execute(self, ctx: ExecutionContext, conversation_id: Uuid) -> list[PinnedFile]:
        conversation = await self._conversations.get(ctx, conversation_id)
        if conversation is None:
            raise NotFoundError("conversation not found")
        pinned = await self._conversations.list_files(ctx, conversation_id)
        return await _live_pins(self._files, ctx, pinned)
```

**`PinConversationFile.execute`: يُستبدل السطران `:702-707` وحدهما.** الباقي (الحراسات قبلهما، و`get_readable` وفحص المساحة و`pin_file` بعدهما) بلا تغيير:

```python
        pinned = await self._conversations.list_files(ctx, conversation_id)
        # The bound counts LIVE pins only (gap th-7): a pin whose file was
        # deleted holds no place. Fewer stored rows than the bound means fewer
        # live pins too, so the bulk read is paid only at the bound itself.
        if len(pinned) >= MAX_PINNED_FILES:
            live = await _live_pins(self._files, ctx, pinned)
            already = any(pin.file_id == file_id for pin in live)
            if not already and len(live) >= MAX_PINNED_FILES:
                raise ValidationError(
                    f"a conversation may pin at most {MAX_PINNED_FILES} files "
                    f"(currently {len(live)})"
                )
```

- **FR‑9 بلا سطر خاص:** الصف اليتيم لـA لا يظهر في `live`، فـ`already` خطأ. ثم `get_readable(A)` يعيد `None`، فالرد 422 قبل `pin_file`. ولا يُعاد `pinned_at` الصف اليتيم.
- **التكرار على ملف حي مثبت** (`test_pinning_stops_at_the_scope_bound_but_a_repeat_still_passes`) يبقى كما هو: إن كان الخام 50 فـ`already` صحيح من `live`، وإن كان أقل فلا حدّ.
- `UnpinConversationFile` بلا تغيير ولا يقرأ الملفات (FR‑10).

**`ConversationService.pinned_files` (`:891-909`): الشيفرة بلا تغيير**، والـ docstring وحده (§3.8). يبقى `except NotFoundError: return ()` يغطي المحادثة المجهولة وحدها، لأن `_live_pins` يحوّل أي `NotFoundError` من المنفذ إلى `common.internal`. وكل استثناء آخر يمر كما هو.

#### 3.5 جذر التركيب: `src/app/framework/di/composition_root.py` (`:1341`)

```python
    list_files = ListConversationFiles(repository, files)
```

`files` هو المعامل الذي يأخذه `PinConversationFile(repository, files)` في `:1352` نفسه، أي `files_query` الوحيد في العملية (`:2175`، `:2251`). وجملة الـ docstring في `:1316-1321` تصير: «… and ONE ``ListConversationFiles`` behind both the API's ``GET …/files`` and the orchestrator's scope read, over the SAME files seam the pin is checked through, so the list, the turn scope and the pin bound share one liveness rule (gap th-7) and the orchestrator sees exactly what the API lists.»

#### 3.6 السباقات

| الحالة | ما يحدث | لماذا لا يتسع النطاق |
|---|---|---|
| **حذف ملف مثبت أثناء الدور** (FR‑12، AC‑4.1) | `_pinned_scope` قرأ `(A,)` في التحضير، ثم حُذف A. يُبنى `AgentDependencies.knowledge_scope = (A,)` مرة واحدة (`orchestrator.py:1669`، `:2155`)، و`rag_agent` يمرر `(A,)` (`agent.py:1016`، `:1079-1083`). وبعد التطهير يعيد `ids_for_files` قائمة فارغة، فيعيد الاسترجاع صفر قطع (`retrieval.py:786-794`) ويرد الوكيل بالاعتذار الثابت | لا قارئ آخر لـ`pinned_files` في `src` (`grep`: `orchestrator.py:2842` وحده). والنطاق tuple ثابت في الحزمة، و`[]` في `knowledge` يعني «لا شيء» لا «الكل». والدور التالي يقرأ من جديد فيجد `()` |
| **حذف بين الوسم والتطهير** | دور جديد بعد الوسم لا يرى A في نطاقه أصلاً، لأن القراءة المجمّعة تشترط `deleted_at IS NULL`. ودور حضّر قبل الوسم قد يعيد قطعاً من A في النافذة الموثقة (`file_deletion.py:47-57`) | سلوك قائم ولا يتغير. والنافذة تضيق للأدوار الجديدة |
| **تثبيت يتزامن مع حذف الملف نفسه** (FR‑13، AC‑4.2) | (أ) الحذف قبل `get_readable`: 422 ولا صف. (ب) الحذف بعد `get_readable` وقبل `pin_file`: 201 وصف يتيم | في (ب) تُخفيه كل قراءة لاحقة، لأن الفحص عند القراءة لا عند الكتابة. فلا قفل ولا تغيير في `PinConversationFile` |
| **تثبيتان متزامنان عند الحد** | قد يصير عدد الحية 51، كما اليوم (`use_cases.py:680-683`) | تسامح موثق ومقبول. الحد حدّ نطاق لا حصة |
| **فشل القراءة المجمّعة** (FR‑14، AC‑4.3، AC‑4.4) | القائمة 500. الدور يفشل في `_pinned_scope` قبل `_admit` و`_open_turn` | الاستثناء لا يُحوَّل إلى `()` في أي طبقة: `_live_pins` يحرس `NotFoundError`، و`pinned_files` يلتقط `NotFoundError` وحده، و`_pinned_scope` لا يلتقط شيئاً |

#### 3.7 الأداء

- **محادثة بلا تثبيت:** صفر استدعاء للمنفذ في القائمة وفي التحضير، لأن `_live_pins` يخرج قبل الاستدعاء (NFR‑2). وصفر استدعاء في التثبيت ما دام الخام أقل من 50.
- **محادثة فيها N صف (يتيم أو حي):** استدعاء واحد بالضبط لكل قائمة، وواحد لكل تحضير دور، بكل المعرّفات وبترتيبها. وصفر `get_readable` (NFR‑3). وفي التثبيت استدعاء واحد فقط إن كان N ≥ 50.
- **العبارة:** `ready_names` القائمة، أي `SELECT id, name FROM files.files WHERE workspace_id = :w AND id IN (…) AND status = 'ready' AND deleted_at IS NULL` تحت `SET LOCAL app.workspace_id` (`files/adapters/sql_repository.py:333-357`)، على المفتاح الأساسي. وهي القراءة نفسها التي يدفعها كل دور مجيب اليوم في مشي `knowledge` لأسماء المستندات (`knowledge/application/use_cases.py:911`). فلا عبارة جديدة على القاعدة.
- **الكلفة على الدور المثبت:** رحلة واحدة إلى Postgres في التحضير، داخل صف «أول رمز في البث» (p95 1.2 s، `07-nfr-slo.md:17`). لا يُقاس حياً (NFR‑7).

#### 3.8 نصوص الـ docstrings (backend-developer)

**`ports/files.py`، docstring الوحدة، الفقرة `:16-21`، تُستبدل بـ:**

> The Protocol is deliberately narrower than ``FilesQuery``: this module asks two questions, and ``ReadableFile`` exposes only what answering them takes. ``get_readable`` asks about ONE file at the moment it is pinned -- "is there a readable file with this id **in this thread's space**?" (the space half arrived with the spaces plan's §3.5). ``names_for_files`` asks about a thread's whole pinned set on every read -- "which of these are still live?" (gap th-7) -- and only the PRESENCE of an id is read, never its name: a name or a size here would be file metadata ``conversations`` has no business projecting (`03 §1` keeps that on ``GET /files``, which the client joins by id).

**`ports/files.py`، docstring `ReadableFiles` (`:60-67`)، تضاف بعده جملة:** «``names_for_files`` is that same rule asked of a whole pinned set at once (gap th-7): it is how a pin whose file was deleted after it was pinned drops out of the list, the turn scope and the pin bound alike.»

**`entities.py`، `PinnedFile` (`:285-295`)، يُستبدل المقطع من «is checked once at the boundary» إلى «that was never indexed.» بـ:**

> is checked at the boundary (``PinConversationFile`` against the ``FilesQuery`` seam), and readability -- not the space -- is asked AGAIN on every read, in one bulk read (gap th-7): a file deleted after it was pinned leaves the row standing, but a pin to it is skipped by the thread's list, its retrieval scope and its pin bound alike, so a thread never shows or searches a file that is gone. The space is not re-asked: a file cannot change space after it is pinned.

وتبقى بعده الجملة القائمة: «There is no ``space_id`` on the pin itself …».

**`use_cases.py`، `PinConversationFile`، الفقرة `:663-670`، تُستبدل بـ:**

> **Readability is asked again on every read; the space is not** (gap th-7). A file deleted after it was pinned leaves the row standing (there is no cross-schema FK to cascade, 01 §2.4, and the delete is soft), and ``ListConversationFiles`` skips it: it is not listed, it is not in the turn scope, and it does not count toward ``MAX_PINNED_FILES``. Hiding rather than refusing is what keeps another module's deletion from becoming this module's error: no read fails because a file went away, it simply stops being pinned. (A file cannot change space after it is pinned: decision 3 makes that unwritable, and the files adapter's ``save`` has no such column.)

**وفقرة `MAX_PINNED_FILES` (`:676-683`)، تضاف إلى آخرها:** «Only LIVE pins count, and the files seam is asked only once the stored rows reach the bound: fewer rows than the bound are fewer live pins too.»

**`use_cases.py`، `ListConversationFiles` (`:622-628`)، تضاف فقرة قبل فقرة «A soft-deleted thread»:**

> **Live pins only** (gap th-7): a pin counts while its file is ``ready`` and not deleted, asked of the ``files`` module in ONE bulk read per call (none for a thread with no pins). The rows of pins whose file is gone stay in the table and are skipped here, the one place both the API's ``GET …/files`` and the orchestrator's ``ConversationService.pinned_files`` read through, so what a thread lists and what it searches can never differ. A failed check raises rather than answering with an unchecked or an empty list: an empty list is an UNSCOPED turn.

**`use_cases.py`، `ConversationService.pinned_files` (`:892-904`)، تضاف بعد الفقرة الأولى:**

> The ids are the thread's LIVE pins (gap th-7), the very list ``GET …/files`` returns: a thread whose every pinned file was deleted answers ``()`` and is unscoped again, exactly as after unpinning the last file. Missing thread ⇒ ``()``; a failed files check RAISES, and the turn fails before anything is written -- it never becomes ``()``.

وتبقى فقرة الحذف الناعم القائمة («A soft-deleted one answers with its real pins …»)، وتصير «… answers with its real live pins …».

**`ports/inbound.py`، `ConversationThreads.pinned_files` (`:196-199`)، تُستبدل الفقرة الأخيرة بـ:**

> Only LIVE pins cross -- a pin whose file has been deleted is not part of the scope (gap th-7) -- so when none is left the answer is ``()`` and the thread is unscoped again. Missing thread ⇒ ``()``, never an error, for ``routed_model``'s reason: this is a read-ahead in front of the write that does the real reporting. A soft-deleted thread answers with its live pins (``ConversationService``). A failed files check is the one error this read raises, and it must: answering ``()`` instead would widen the turn.

(النص القائم «Missing or soft-deleted ⇒ ()» يخالف `use_cases.py:899-903` اليوم، فيُصحَّح في الفقرة نفسها. انظر د‑ت9.)

**`ports/repository.py`، فقرة `list_files` (`:54-62`)، تضاف إليها جملة:** «``list_files`` returns the STORED rows, pins whose file has since been deleted included: which of them are live is the application's question (``ListConversationFiles``, gap th-7), because only it holds the files seam.»

**`routers/conversations.py`، `list_conversation_files` (`:515-524`)، تضاف بعد السطر الأول:** «Live pins only (gap th-7): a pin whose file was deleted is not listed and is not in the scope the next turn searches, and when none is left the thread is unscoped again.»

#### 3.9 نصوص الوثائق الملزمة (backend-developer)

**`docs/design/01-data-model.md` §2.4، السطر `:236`، يُستبدل بـ:**

```sql
-- مجموعةٌ بلا تثبيتٍ حيّ ⇒ النطاق الشامل لمساحة المحادثة، وهو سلوك كلّ خيطٍ سبق هذا الجدول (لا backfill).
-- **التثبيت الحيّ وحده يُقرأ (ث‑7):** ملفّه `ready` وغير محذوف. الصفّ على ملفٍ حُذف يبقى (لا FK عبر
-- المخطّطات، DD‑01، والحذف ناعم)، لكنّ القائمة ونطاق الدور وحدّ الخمسين تتجاهله، بقراءةٍ مجمّعة واحدة
-- من `files` (`ReadableFiles.names_for_files`) عند كلّ قراءة. ولا ترحيل: الفلترة تغطّي الصفوف القائمة.
-- ونصّ ترحيل `0003` («retrieval simply finds nothing for them») يصف ما قبل ث‑7، ولا يُعدَّل لأنّ الترحيل المطبَّق لا يُمسّ.
```

**`docs/design/02-port-contracts.md` §2، كتلة `ReadableFiles` (`:576-577`)، تصير:**

```python
class ReadableFiles(Protocol):
    async def get_readable(self, ctx, file_id: Uuid) -> ReadableFile | None: ...
    async def names_for_files(self, ctx, file_ids: Sequence[Uuid]) -> Mapping[Uuid, str]: ...  # ث‑7
# `names_for_files` هي نفسها التي في `FilesQuery` (أعلاه)، ويربطها جذر التركيب بالنسخة نفسها. تسأل بها
# المحادثة أيّ تثبيتاتها ما زال ملفّه حيّاً (`ready` وغير محذوف)، بقراءةٍ واحدة لكلّ قائمة ولكلّ تحضير دور،
# ولا تُستدعى لخيطٍ بلا تثبيت. الحضور وحده هو الجواب، والاسم لا يُقرأ. وفشلها خطأ `common.internal`،
# لا «لا تثبيتات»، لأنّ «لا تثبيتات» نطاقٌ شامل.
```

ويبقى تعليق «قاعدة التكامل التي فرضها بقاء التثبيت …» بعدها كما هو.

**`docs/design/03-api-spec.md` §1، بعد السطر `:166` («… خيطٌ محذوف ⇒ 409.»)، يضاف:**

```text
# **التثبيت الحيّ وحده يُحسب (ث‑7):** التثبيت على ملفٍ حُذف (ومنه الاستبدال باسمٍ متطابق) لا يُعاد في
# `GET …/files`، ولا يدخل نطاق الدور، ولا يُعدّ في حدّ الخمسين، والصفّ يبقى. والقائمة ونطاق الدور قراءةٌ واحدة،
# فما يُعرض هو ما يُبحث فيه. فإن لم يبقَ تثبيتٌ حيّ عاد النطاق شاملاً لمساحة المحادثة، كفكّ آخر تثبيت.
# والدور الجاري يبقى على النطاق الذي بدأ به ولا يتّسع. تثبيت الملف المحذوف نفسه 422، وفكّه 204.
# وإن فشلت قراءة الملفات فالرد 500 `common.internal`، والدور يفشل قبل كتابة رسالة المستخدم، ولا يتّسع النطاق.
```

**`docs/delivery/data-search-android/gaps.md`** (بعد اخضرار CI. التاريخ ورقم الـ PR يُملآن عند الكتابة):

- **السطر `:11`** يصير: «الحالة (2026‑11‑ي ي): … **وأُصلحت ث‑5** (PR #42، دُمج في `master`). **وأُصلحت ث‑7** (PR #NN، دُمج في `master`) بلا ترحيل ولا تغيير في قاعدة البيانات. لا ثغرة مفتوحة.»
- **بعد `:69`** يضاف:

> - **الإصلاح (2026‑11‑ي ي):** التثبيت يُحسب ما دام ملفه حياً (`ready` وغير محذوف). بعد `DELETE /files/{id}`، أو الاستبدال باسم متطابق، يختفي التثبيت من `GET /conversations/{id}/files` ومن نطاق الدور معاً، ولا يُعدّ في حدّ الخمسين. والقائمة والنطاق قراءة واحدة، فما يُعرض هو ما يُبحث فيه. فإن لم يبقَ تثبيت حي بحثت المحادثة في كل مصادر مساحتها، كما بعد فك آخر تثبيت (المعيار 10). والدور الجاري لا يتسع إن حُذف ملفه أثناءه. تثبيت الملف المحذوف 422 كما كان، وفك تثبيته 204. وإن فشلت قراءة الملفات فالرد 500 `common.internal`، ولا يتسع النطاق. لا ترحيل ولا تنظيف: صفوف التثبيت تبقى في الجدول وتُتجاهل عند القراءة، فيصح الإصلاح للصفوف القديمة أيضاً. ولا حقل جديد في الرد.
> - ⚠️ **للعميل:** السطر 146 من `backend-reply.md` («التثبيتات عليه تبقى … لا يعود للكل تلقائياً. على Android أن يعرض التثبيت اليتيم ويقترح فكه») لم يعد صحيحاً، ومعه تنبيه السطر 269. لم يُعدَّل `backend-reply.md` عمداً (س‑7). العقد في `openapi.yaml` و`/openapi.json` الحي.

## 4. البيانات

**لا تغيير في المخطط.** لا ترحيل، ولا عمود، ولا فهرس، ولا قيد، ولا سياسة RLS، ولا دور، ولا backfill، ولا تنظيف (س‑2، س‑3، NFR‑1).

- **لماذا لا يلزم شيء:** «هل الملف حي؟» حقيقة تملكها `files`، وتُقرأ بقراءة مجمّعة قائمة على المفتاح الأساسي (§3.7). والمفتاح الأجنبي عبر المخططات ممنوع (DD‑01)، ولا يُطلق على حذف ناعم أصلاً. وترحيل التنظيف يعبر سلسلتين ومساحات العمل كلها تحت RLS (`requirements.md` §5.6).
- **الصفوف القائمة اليوم** (FR‑7): تُفلتر عند القراءة كأي صف، فلا خطوة تشغيل.
- **عزل المستأجر:** طبقتان كما اليوم، ولا جديد:
  - `conversation_files`: `SET LOCAL app.workspace_id` و`WHERE workspace_id` في `list_files` (`sql_repository.py:513-519`).
  - `files.files`: الشيء نفسه في `ready_names` (`files/adapters/sql_repository.py:346-351`)، ومختبر حياً (`test_ready_names_cannot_reach_another_tenants_readable_file`، `test_files_repository_rls.py:786`).
  - ويضيف `tests/integration/test_deleted_pins_live.py` الحالة الحية المطلوبة (AC‑5.3).
- **لا مستودع جديد ولا طريقة مستودع جديدة**، فلا يلزم اختبار RLS جديد بقاعدة CLAUDE.md. الحالة الحية في §5 تغطي NFR‑4.
- **التوافق والرجوع:** لا شيء يُرجَع في القاعدة. الرجوع عن الميزة revert للـ commit، فتعود الصفوف اليتيمة إلى الظهور كما اليوم، ولا تضيع بيانات.

## 5. خطة الاختبار المقترحة

**المستويات:**
- **unit:** تحت `tests/unit/`، وتُشغَّل محلياً بـ`pytest tests/unit tests/architecture tests/eval`.
- **integration:** تحت `tests/integration/` (`live_db`). تُكتب الآن وتُشغَّل في CI job `integration` فقط (س‑8، CLAUDE.md).
- **مراجعة:** فحص يدوي يقوم به code-reviewer.

**مفاتيح:**
- `UC` = `test_conversations_use_cases.py`، قسم «ث‑7». و`_FakeReadableFiles` يُوسَّع بثلاثة أشياء:
  - `names_for_files(ctx, file_ids)` يعيد `{fid: fid for fid in file_ids if fid in self.ready}`، ويسجّل `tuple(file_ids)` في `bulk_calls`.
  - `bulk_error: Exception | None`، يُرفع قبل الجواب إن ضُبط.
  - `on_get_readable: Callable[[str], None] | None`، يُستدعى بعد جواب `get_readable`، لفرض التداخل في AC‑4.2.
  - و`calls` القائم يبقى لـ`get_readable` وحده، فلا يتغير أي توقع قائم (منه `test_unpinning_is_idempotent_and_never_consults_the_files_module`).
  - «الحذف» في المزيّف = `files.ready.discard(id)`. و«صف يتيم مكتوب مباشرة» = `conversations.pins[cid].append(PinnedFile(...))` لمعرّف ليس في `ready`.
- `R` = `test_api_conversations_router.py`، قسم «ث‑7»، فوق `build_conversations`، و`StubReadableFiles` يُوسَّع بالطريقة نفسها (`bulk_calls` و`bulk_error`).
- `O` = `test_orchestrator.py`، قسم «ث‑7»:
  - `PluginLoader().load_into(registry)` و`rag_agent` الحقيقي (نمط `:517-576`).
  - `conversations=stack.service` من `build_conversations(files=StubReadableFiles(...), spaces=StubActiveSpaces(live={_SPACE, _OTHER}))`، ومحادثة `rag_agent` في `_SPACE`.
  - مزيّف `_ScopedKnowledge(_FakeKnowledge)` يعيد قطعة لكل ملف حي في النطاق، أو لـC حين `file_ids is None`، وقائمة فارغة للنطاق الذي كل ملفاته محذوفة. ويسجّل `scopes` و`spaces` كالأصل، وله خطاف `before_answer` لـAC‑4.1.
  - ومزيّف الاستهلاك القائم (`:616-660`) لـAC‑4.4.
- `CR` = `test_composition_root.py`. و`LIVE` = `tests/integration/test_deleted_pins_live.py`.

| AC | المستوى | المكان والاسم | ما يُثبت |
|---|---|---|---|
| AC‑1.1 | unit + integration | `UC` `test_a_deleted_file_drops_out_of_the_list_with_its_original_pinned_at`. و`R` `test_a_deleted_file_is_no_longer_listed_as_pinned`: الجسد `data == [{file_id: B, pinned_at: <قبل الحذف>}]` و`meta == {"next_cursor": None, "limit": 1}`، ومعرّف A ليس في `response.text`. و`LIVE` | FR‑1، FR‑2 |
| AC‑1.2 | unit | `R` `test_all_pins_deleted_lists_an_empty_page`: `{"data": [], "meta": {"next_cursor": None, "limit": 0}}` | FR‑2 |
| AC‑1.3 | unit | `UC` `test_live_pins_keep_their_order_and_their_timestamps`: A ثم B ثم C بأوقات متزايدة (`now` صريح في `pin_file`)، ويُحذف B ⇒ `[A, C]` و`created_at` لكل منهما مساوٍ لما قبل الحذف | FR‑2 |
| AC‑1.4 | unit | `UC` `test_a_deleted_thread_lists_only_its_live_pins`. و`R` `test_a_soft_deleted_thread_lists_only_its_live_pins` (200 بـ B) | FR‑15 |
| AC‑1.5 | unit | `R` `test_a_fully_live_list_is_byte_identical`: الجسد يساوي `json.dumps` لما يبنيه `Page[ConversationFileOut]` من صفوف المستودع، بلا إضافة ولا نقص في المفاتيح. **يُكتب ويمرّ على الفرع قبل أي تعديل في `src`** (§6 الخطوة 1) | NFR‑6 |
| AC‑1.6 | unit | `R` `test_the_list_guards_run_before_any_files_read`: بلا توكن 401، و`Principal` بـ`roles=frozenset()` (نمط `:2920`) 403، ومجهولة 404 `common.not_found`، ومن مستأجر آخر 404. وفي كل حالة `stack.files.bulk_calls == []` | FR‑14 |
| AC‑2.1 | unit | `O` `test_a_deleted_pin_leaves_the_rest_as_the_turn_scope`: صفّا A وB، ويُحذف A ⇒ `knowledge.scopes == [("B",)]` و`knowledge.spaces[0] == _SPACE` | FR‑3، FR‑5 |
| AC‑2.2 | unit | `O` `test_no_live_pin_left_widens_the_turn_to_the_threads_space`: A وB محذوفان ⇒ `knowledge.scopes == [None]` و`spaces == [_SPACE, …]`، وإطار `final` بـ`citations` على `doc-C` وبلا علامة `fallback`، والنموذج استُدعي (`resolver` سجّل الدور) | FR‑4، س‑1 |
| AC‑2.3 | unit | `O` `test_pinning_again_after_the_widening_narrows_the_next_turn`: بعد AC‑2.2، `stack.use_cases.pin_file` لـC ثم دور ⇒ `scopes[-1] == ("C",)`، و`list_files` يعيد C وحده | FR‑3 |
| AC‑2.4 | unit | `UC` `test_the_list_and_the_turn_scope_are_one_answer`: خاصية بالتعداد الكامل (`itertools.product`) على خمسة ملفات، لكل منها ثلاث حالات (حي، محذوف بعد التثبيت، صف يتيم مكتوب مباشرة لمعرّف مجهول)، أي 243 حالة. في كل حالة `[p.file_id for p in await list_files.execute(...)] == list(await service.pinned_files(...))`، والترتيب ترتيب المستودع. (لا `hypothesis` في `pyproject.toml`، فالتعداد هو الخاصية) | FR‑3 |
| AC‑2.5 | unit | `O` `test_the_widened_scope_never_leaves_the_threads_space`: ملف حي في `_OTHER` يطابق السؤال، و`_ScopedKnowledge` لا يعيده إلا لـ`space_id == _OTHER` ⇒ `knowledge.spaces` كلها `_SPACE`، ولا استشهاد من `_OTHER` | FR‑6 |
| AC‑2.6 | unit | `UC` `test_a_live_unindexed_pin_stays_listed_and_scoped`. و`O` `test_a_live_unindexed_pin_still_falls_back`: `scopes == [("F",)]`، و`_ScopedKnowledge` بلا قطعة لـF ⇒ إطار `final` بالاعتذار الثابت | FR‑16 |
| AC‑2.7 | unit | القائم `test_an_unpinned_thread_gives_the_run_an_empty_scope` (`:1902`) بلا تعديل. و`O` `test_a_never_pinned_thread_is_unscoped_and_reads_no_files`: `scopes == [None]` و`stack.files.bulk_calls == []` | FR‑4، NFR‑2 |
| AC‑3.1 | unit | `UC` `test_a_directly_written_orphan_row_is_hidden_and_left_in_place`: القائمة `[B]`، و`pinned_files == ("B",)`، و`conversations.pins[cid]` ما زال يحمل صف A بـ`created_at` نفسه | FR‑7 |
| AC‑3.2 | unit + integration | `UC` `test_a_pin_on_an_id_files_never_knew_is_hidden`. و`LIVE` `test_a_row_naming_no_file_at_all_is_not_listed` (صف يُدرج بصفة المالك لمعرّف `new_uuid7()`) | FR‑1، FR‑7 |
| AC‑3.3 | unit | `R` `test_unpinning_a_deleted_files_pin_is_204_twice_and_drops_the_row`: 204 مرتين، و`stack.repository.pins[cid]` بلا A بعد الأولى، والقائمة `[B]`. والقائم `test_unpinning_on_a_deleted_thread_is_a_conflict` يغطي 409 | FR‑10 |
| AC‑3.4 | integration | `LIVE` `test_deleting_the_file_twice_keeps_the_list_at_the_rest`: `DeleteFileService.delete` مرتين (التكرار مثالي كما يثبت `test_file_cascade_live.py:453`)، وبعد كل مرة `ListConversationFiles` ⇒ `[B]` | FR‑11 |
| AC‑3.5 | unit | `UC` `test_pinning_a_deleted_file_with_an_orphan_row_is_still_422`: `ValidationError`، و`conversations.pins[cid]` بلا تغيير (صف A اليتيم بـ`created_at` الأصلي، ولا صف جديد)، و`files.calls == ["A"]`. و`R` على السلك: 422 `common.validation_error` | FR‑9 |
| AC‑3.6 | unit | `UC` `test_orphans_hold_no_place_under_the_scope_bound`: 40 حياً و10 يتيمة ⇒ تثبيت حي جديد ينجح، و`bulk_calls` طوله 1. و`UC` `test_the_bound_counts_and_reports_live_pins_only`: 50 حياً و5 يتيمة ⇒ `ValidationError` ونصها فيه `"(currently 50)"`. و`UC` `test_below_the_bound_pinning_reads_no_bulk_files`: 3 صفوف ⇒ `bulk_calls == []` | FR‑8، د‑ت3 |
| AC‑3.7 | مراجعة + integration | `git diff --stat master -- migrations/ src/app/framework/di/file_deletion.py` فارغ. و`LIVE` `test_the_cascade_leaves_the_pin_row_and_the_list_hides_it`: بعد `DeleteFileService.delete(A)`، صف `conversation_files` لـA ما زال موجوداً (قراءة بصفة المالك)، والقائمة `[B]` | FR‑11، س‑2 |
| AC‑4.1 | unit | `O` `test_a_file_deleted_mid_turn_never_widens_that_turn`: تثبيت واحد على A. خطاف `before_answer` يحذف A من `stack.files.ready` (بعد التحضير، قبل `knowledge.answer`)، و`_ScopedKnowledge` يعيد `[]` للنطاق `("A",)` ⇒ `scopes == [("A",)]` لا `[None]`، وإطار `final` بالاعتذار الثابت، والنموذج لم يُستدعَ. ثم دور ثانٍ ⇒ `scopes[-1] is None` | FR‑12 |
| AC‑4.2 | unit | `UC` `test_a_file_deleted_between_the_check_and_the_write_never_shows`، مُعلَم بالترتيبين: (أ) `ready.discard` قبل التثبيت ⇒ `ValidationError`. (ب) `on_get_readable` يحذفه بعد الجواب ⇒ التثبيت يعيد `PinnedFile`. وفي الحالتين بعد الانتهاء: القائمة `[]` و`pinned_files == ()` | FR‑13 |
| AC‑4.3 | unit | `R` `test_a_failed_files_read_is_a_500_that_names_no_file`: `stack.files.bulk_error = AppError("boom", code="common.internal")` ⇒ 500، و`content-type` `application/problem+json`، و`code == "common.internal"`، ولا معرّف ملف مثبت في `response.text`. ومُعلَم بـ`RuntimeError("boom")` ⇒ 500 `common.internal` من `_handle_unexpected`. و`UC` `test_a_not_found_from_the_files_seam_is_never_an_unknown_thread`: `bulk_error = NotFoundError(...)` ⇒ `ListConversationFiles` يرفع `AppError` بـ`code == "common.internal"` (لا `NotFoundError`)، و`pinned_files` يرفع ولا يعيد `()` | FR‑14، د‑ت4 |
| AC‑4.4 | unit | `R` مُعلَم بـ`stream` true/false: `test_a_failed_files_read_fails_the_turn_before_anything_is_written`. 500 problem+json، ولا `text/event-stream` ولا `event: accepted` في الجسد، وعدد رسائل المحادثة في `stack.repository` لا يتغير. ومعه حالة بـ`Idempotency-Key`: إعادة الطلب بعد إزالة `bulk_error` تُشغّل الدور (المفتاح حُرّر). و`O` `test_a_failed_files_read_reserves_nothing_and_never_reaches_knowledge`: مزيّف الاستهلاك `reserved == []`، و`knowledge.scopes == []`، و`invoke` يرفع قبل أي حدث | FR‑14، س‑5 |
| AC‑5.1 | unit | `UC` `test_a_thread_with_no_pins_never_asks_the_files_module`: القائمة و`pinned_files` ⇒ `bulk_calls == []` و`calls == []`. و`O` في AC‑2.7 | NFR‑2 |
| AC‑5.2 | unit | `UC` مُعلَم بـN = 1 و2 و50 (نصفها يتيم): `test_one_bulk_read_per_list_and_per_turn_whatever_the_size`. القائمة ⇒ `bulk_calls == [tuple(كل المعرّفات بترتيب المستودع)]`. ثم `pinned_files` ⇒ استدعاء ثانٍ واحد. و`calls == []` | NFR‑3 |
| AC‑5.3 | integration | `LIVE` `test_a_pin_on_a_file_this_tenant_deleted_is_gone_and_another_tenant_sees_nothing`: في W1، تثبيت على A ثم `DeleteFileService.delete(A)` ⇒ القائمة بسياق W1 لا تحمل A. و`FilesQueryService.names_for_files(ctx_w2, [معرّفات W1 الحية]) == {}`. و`ListConversationFiles.execute(ctx_w2, محادثة W1)` ⇒ `NotFoundError` | NFR‑4 |
| AC‑5.4 | بوابة | `lint-imports` (8 عقود)، و`git diff master -- .importlinter` فارغ، و`grep -rn "app.modules.files" src/app/modules/conversations` فارغ | NFR‑5 |
| AC‑5.5 | بوابة + unit | `mypy src` يمر (الربط البنيوي في `composition_root.py:2251`). و`CR` `test_the_pin_list_and_bound_share_one_files_seam`: `booted.conversations.list_files._files is booted.conversations.pin_file._files`، وهو `isinstance(..., FilesQueryService)`، و`booted.orchestrator._deps.conversations._list_files is booted.conversations.list_files` | NFR‑5، FR‑3 |
| AC‑5.6 | مراجعة | `git diff --stat master -- src/app/modules/files` فارغ | س‑2 |
| AC‑6.1 | unit (contract) + مراجعة | `test_api_conventions.py` يمر بلا تعديل. والمراجعة: الأوصاف الثلاثة في §3.1 تحمل النقاط الأربع، ولا تبقى «retrieval simply finds nothing for it» | FR‑17 |
| AC‑6.2 | مراجعة | نصوص §3.9. و`grep -rn "NOT repeated on read\|not re-checked on read\|leaves the pin standing" src/app/modules/conversations` فارغ. و`git diff master -- migrations/versions/conversations/0003_conversation_files.py` فارغ | FR‑17، ت‑2 |
| AC‑6.3 | مراجعة | `gaps.md`: فقرة الإصلاح بتاريخها، والتنبيه على `backend-reply.md:146`، وسطر الحالة بلا «تغيّر قاعدة البيانات» | FR‑18، ت‑7 |
| AC‑6.4 | مراجعة | `git -C /mnt/c/Users/anast/Desktop/aizzak-android status --short` قبل الفرع وبعده: لا ملف جديد من هذه الميزة | س‑7 |
| AC‑6.5 | بوابة | البوابات 1–5 محلياً، و`deploy/prometheus/test-rules.sh`، وCI `quality` و`integration` أخضران (ومنه `LIVE`) | NFR‑8 |

**`tests/integration/test_deleted_pins_live.py` (جديد، `pytestmark = [pytest.mark.live_db]`):**
- **الربط كما في جذر التركيب:**
  - `SqlFileRepository` خلف `FilesQueryService`.
  - `SqlConversationRepository`، و`StartConversation` فوق `SpacesQueryService(SqlSpaceRepository)`.
  - `PinConversationFile` و`ListConversationFiles` فوق `FilesQueryService` نفسه، و`ConversationService` بالمستودع نفسه.
  - `DeleteFileService` بنمط `_service` في `test_file_cascade_live.py:158-175` (Qdrant مزيّف مسجِّل، و`electrical` حقيقي).
- **البذر:** الملفات بـ`RegisterUpload` و`CompleteUpload` (نمط `_ready_file`)، والمحادثة بـ`StartConversation`. صف المعرّف المجهول وحده يُدرج بصفة المالك.
- **الاختبارات:** الأربعة المذكورة في الجدول (AC‑3.2، AC‑3.4، AC‑3.7، AC‑5.3)، ومعها `test_no_live_pin_left_reads_as_an_unscoped_thread`: بعد حذف كل الملفات المثبتة، `ConversationService.pinned_files == ()` (AC‑1.2 وAC‑2.2 حياً، في طرف `conversations`).
- **التنسخ المحلي للمساعدات** (`_ctx` و`_space` و`_ready_file` و`_service`) مقصود، وهو نمط الملفين الحيين المرجعيين. فلا استيراد بين ملفات الاختبار.

## 6. ترتيب التنفيذ والتوازي

منفّذ واحد (backend-developer) على الفرع `feat/data-search-deleted-pins`. الخطوات التي تحمل الحرف نفسه يمكن أن تُنفَّذ بأي ترتيب بينها.

1. **أولاً، بلا تعديل في `src`:** يُكتب `R` `test_a_fully_live_list_is_byte_identical` ويمر. هذا يثبت أن المرجع هو سلوك `master` (AC‑1.5).
2. **المزيّفات:**
   - `names_for_files` و`bulk_calls` و`bulk_error` في `StubReadableFiles`، ومعها `on_get_readable` في `_FakeReadableFiles`.
   - و`_UnusedFiles` في `test_workflow_orchestration.py`.
   - الطرق الإضافية لا تكسر شيئاً قبل تغيير المُنشئ.
3. **commit واحد يغيّر مُنشئ `ListConversationFiles`**، حتى لا يحمر `pytest` في منتصفه:
   - المنفذ `ports/files.py`.
   - ثم `use_cases.py` (`_live_pins`، والمُنشئ، والحد).
   - ثم `composition_root.py:1341`.
   - ثم مواضع البناء في الاختبارات: `support_conversations.py:346`، و`test_conversations_use_cases.py` (أربعة مواضع)، و`test_workflow_orchestration.py:908`.
4. **(أ) اختبارات `UC`** (قسم «ث‑7»).
   **(أ) اختبارات `R`.**
   **(أ) اختبارات `O`.**
   **(أ) `CR`.**
5. **(ب) الـ docstrings** في §3.8. يمكن موازاتها بالخطوة 4.
   **(ب) الوثائق الملزمة** في §3.1 و§3.9 (`openapi.yaml`، و`01`، و`02`، و`03`). يمكن موازاتها بالخطوتين 3‑4 بعد تثبيت التواقيع في §3.
   **(ب) `LIVE`** (`tests/integration/test_deleted_pins_live.py`). يُكتب محلياً ولا يُشغَّل (الحزمة الحية مستعملة، CLAUDE.md).
6. **البوابات 1–5 و`deploy/prometheus/test-rules.sh`**، ثم PR، ثم CI `quality` و`integration`.
7. **بعد اخضرار CI:** `gaps.md` (سطر الحالة، وفقرة الإصلاح) في commit وثائق على الفرع نفسه.

**المراحل التي تُتخطّى:**
- **UX وfrontend:** لا واجهة في هذا المستودع.
- **البيانات/data-engineer:** لا ترحيل ولا تغيير في المخطط (§4). وعلى `status.md` أن يحوّل المرحلة 5 من ⏸ إلى ⏭.
- **staging وبوابة ③:** لا هدف staging، والموجة 8 مؤجلة.

## 7. المخاطر والقرارات

**لا بنود ⚠️.** كل ما يمس الأمن أو المستأجر أو الأداء أو العقد الملزم حسمه المالك في بوابة ① (ق‑1):
- توسّع النطاق إلى مساحة المحادثة حين لا يبقى تثبيت حي (س‑1).
- الفلترة عند القراءة بلا ترحيل ولا تغيير في سلسلة الحذف (س‑2).
- لا تنظيف للصفوف اليتيمة (س‑3)، ونقل التثبيت عند الاستبدال مؤجَّل (س‑4).
- الفشل 5xx بلا توسّع (س‑5)، ولا حقل جديد (س‑6)، ولا تعديل في Android (س‑7)، والاختبارات الحية في CI فقط (س‑8).
- تعديل الوثائق الملزمة (ت‑1، ت‑3)، وتصحيح `gaps.md` (ت‑7).

وما بقي قرارات تصميم داخل وحدة واحدة: لا تضيف تبعية، ولا عبارة SQL جديدة، ولا سراً، ولا إعداداً، ولا تمس deploy ولا CI.

### 7.1 قرارات التصميم (لا تحتاج قراراً بشرياً)

- **د‑ت1، الفلترة في application لا في SQL.**
  - البديل: `JOIN` على `files.files` في `list_files`. ممنوع لأن الوحدات مستقلة (DD‑01، `.importlinter:89`)، ولأن دور `conversations` لا يملك صلاحية على `files.files` (`deploy/postgres/initdb/10-roles.sh`).
  - والمحوّل لا يملك منفذ الملفات. فالمكان الوحيد الذي يملك المستودع والمنفذ معاً هو حالة الاستخدام.
- **د‑ت2، إعادة استعمال `names_for_files` حرفياً، والاسم يُهمل.**
  - البديل الأول: طريقة جديدة `live_ids` على `FilesQuery`. تغيّر وحدة `files` (يخالف AC‑5.6)، وتضيف سؤالاً ثالثاً لسؤال واحد.
  - البديل الثاني: نوع إرجاع أضيق في منفذ `conversations` (`Mapping[Uuid, object]`)، يقبله mypy بالتغاير. رُفض لأن السابقتين (`knowledge` و`electrical`) أعلنتا التوقيع نفسه حرفاً، ولأن الـ docstring يقول صراحة إن الاسم لا يُقرأ.
  - الاسم لا يخرج من حالة الاستخدام ولا يصل إلى رد ولا سجل.
- **د‑ت3، قراءة الحد عند بلوغ الصفوف الخام 50 فقط.** صفوف أقل من الحد تعني تثبيتات حية أقل منه أيضاً، فلا يتغير أي جواب، ويبقى التثبيت العادي بلا رحلة جديدة. ويُختبر الطرفان (AC‑3.6).
- **د‑ت4، `_live_pins` يحوّل `NotFoundError` من المنفذ إلى `common.internal`.**
  - `ConversationService.pinned_files` يلتقط `NotFoundError` ليعيد `()` لمحادثة مجهولة. فلو خرج `NotFoundError` من منفذ الملفات لصار الدور بلا نطاق، أي توسّعاً صامتاً يخالف FR‑14، ولصارت القائمة 404 على محادثة موجودة.
  - `ready_names` لا يرفعه اليوم (`files/adapters/sql_repository.py:573-593`)، لكن الضمان يجب ألا يعتمد على انضباط وحدة أخرى. الكلفة `try` واحدة واختبار واحد (AC‑4.3).
- **د‑ت5، لا تغيير في المنسّق.** ترتيب التحضير القائم يحقق FR‑12 وFR‑14: النطاق يُقرأ مرة واحدة قبل `_admit` و`_open_turn`. وكل تعديل هناك يمس ملفاً كبيراً ساخناً بلا حاجة.
- **د‑ت6، الراوتر بلا تغيير في الشيفرة.** `meta.limit=len(pinned)` يصير عدد الحية تلقائياً (FR‑2). والتعديل docstring واحد لأنه وصف `/openapi.json` الحي.
- **د‑ت7، ملف حي جديد بدل توسيع `test_file_cascade_live.py` أو `test_pin_space_rule_live.py`.** لكل ملف قائم docstring يحدد موضوعاً آخر (SQL التسلسل، وقاعدة المساحة). وتنسخ المساعدات هو نمطهما نفسه.
- **د‑ت8، انحراف صغير عن النص الحرفي للأوصاف:** «the whole workspace corpus» في `listConversationFiles` و`unpinConversationFile` تصير «every indexed document of the thread's space». العبارة القديمة غير دقيقة منذ س‑32، وAC‑6.1 (البند 2) وFR‑6 يطلبان «مساحتها». التعديل في الجمل التي تُعدَّل أصلاً، ولا يغيّر سلوكاً.
- **د‑ت9، تصحيح docstring `ConversationThreads.pinned_files`:** «Missing or soft-deleted ⇒ ()» يخالف الشيفرة اليوم (`use_cases.py:899-903`: المحذوفة ناعماً تعيد تثبيتاتها). يُصحَّح في الفقرة التي تُعاد كتابتها أصلاً.

### 7.2 المخاطر المُدارة

- **تراكم الصفوف اليتيمة يكبّر `IN (…)`.**
  - عدد الحية محدود بالخمسين، لكن الخام قد يتجاوزها مع الوقت، إن حذفت المساحة ملفات مثبتة ثم ثُبّتت غيرها.
  - العبارة على المفتاح الأساسي، فمئات المعرّفات رخيصة. والقائمة غير مرقّمة اليوم على الخام أصلاً.
  - إن صار العدد مشكلة تُقاس، فالبديل (ج) في س‑2 (التنظيف داخل تسلسل الحذف) يأتي لاحقاً بقرار المالك (A‑6).
- **رحلة إضافية إلى Postgres في تحضير الدور المثبت.** عبارة قائمة يدفعها كل دور مجيب في `knowledge` اليوم (§3.7). لا تُقاس حياً في الميزة (NFR‑7).
- **دور جارٍ حُذف ملفه الوحيد يرد بالاعتذار.** هذا مقصود (FR‑12): الضمان أن النطاق لا يتسع داخل الدور، والدور التالي يتسع.
- **المستخدم قد لا ينتبه إلى أن محادثته صارت تبحث في كل المساحة** بعد حذف ملفاتها أو استبدالها (خطر 2 في `requirements.md`). قرّره المالك (س‑1). والحدّ مساحة المحادثة، والتثبيت لا يمنح صلاحية قراءة.
- **المزيّفات المتعددة** (`StubReadableFiles`، و`_FakeReadableFiles`، و`_UnusedFiles`): دلالة واحدة في الثلاثة، «الحي = في `ready`»، كدلالة `get_readable` القائمة. وAC‑2.4 وAC‑5.2 يثبتانها على المزيّف الذي يستعمله الراوتر.
- **عميل Android بنى منطق «التثبيت اليتيم»:** يصير منطقاً لا يُستدعى، ولا ينكسر شيء. والتنبيه في `gaps.md` (§3.9).
- **الإفصاح:** يقل ولا يزيد. القائمة لم تعد تذكر معرّف ملف محذوف، والاسم لا يعبر. ورد الفشل لا يحمل معرّفاً (AC‑4.3).
- **الاختبارات الحية لا تُشغَّل محلياً.** `LIVE` يعتمد على CI job `integration`، كسابقتي ث‑3 وث‑5.

</div>
