<div dir="rtl">

# مراجعة الشيفرة: ث‑2 حقل حالة الرسالة (`status` و`fallback`)

- **النطاق المراجَع:** `git diff 0976bcb..HEAD` في `/home/AIZZAK`، الفرع `feat/data-search-message-status` (الـ commits `0682512`، `a191011`، `39bbbb1`، `44ce03e`، `4503184`، `2ade90b`). التعديل غير المحفوظ على `status.md` لم يُراجَع.
- **الحكم:** **PASS**. لا ملاحظات حاجبة. فيه ملاحظتان مهمتان وأربع تحسينات.

## الملاحظات

| # | الشدة | الملف:السطر | الملاحظة | الإصلاح المطلوب |
|---|---|---|---|---|
| 1 | مهمة | `tests/unit/test_api_conversations_router.py:1492`، `tests/unit/test_conversations_sql_statements.py:249`، `tests/integration/test_conversations_repository_rls.py` (الإدراج الخام في `test_a_row_inserted_without_status_or_fallback_reads_back_as_none`)، `docs/delivery/data-search-message-status/test-plan.md:36` و`:103` | AC‑3.3 يطلب صفاً قديماً **نصه ينتهي بجملة الانقطاع** ويُعاد بـ `status: null`. هذا الشرط هو ما يثبت أن القراءة لا تستنتج الحالة من النص (FR‑3). لكن كل الاختبارات التي يذكرها `test-plan.md` تستعمل النص `"old"`. ولا يوجد في الفرق أي صف قديم فيه جملة الانقطاع. يقول `test-plan.md:103` إن هذا السيناريو جُرِّب («نصها ينتهي بجملة الانقطاع… كما هو متوقع»)، وهذا غير صحيح. السلوك نفسه سليم اليوم، لأن `_hydrate_message` يقرأ العمود فقط. لكن لا يوجد اختبار يفشل إن أضاف أحد لاحقاً استنتاجاً من النص عند القراءة. | في اختبار الراوتر عند `:1492`، أضف صفاً بلا حالة نصه `"الجواب هو\n\n⚠️ انقطعت الإجابةُ قبل اكتمالها."`. ويمكن ذلك بـ `stack.service.append(..., role="assistant", text=...)` بلا `status`. ثم أكّد أن الرد 200 وأن الصف يعود بـ `status is None` و`fallback is None`. وصحّح `test-plan.md:36` و`:103` ليذكرا الاختبار الفعلي. |
| 2 | مهمة | `docs/design/openapi.yaml:1365`، `docs/design/03-api-spec.md:121`، `src/app/agents/orchestrator.py:939-940` | الجملة «A turn the primary model started and then lost is not a fallback (`null`)»، ومثلها في `03 §2` («مجرد فقدان النموذج الأساسي بعد أن بدأ لا يجعله fallback») وفي docstring `_stored_fallback`، **خاطئة لدور فيه أكثر من استدعاء**. مثال: دور يجيب فيه السحابي عن استدعاء أول، ثم يفشل الاستدعاء الثاني قبل أول chunk، فيجيب المحلي. عندها `_FallbackLLM` يضبط `switched = True` (`orchestrator.py:844`، `:898`)، فيُحفظ `fallback` **بقيمة غير `null`**، ويبدأ النص بجملة التحول. هذا مثبت في `tests/unit/test_llm_fallback.py::test_a_turn_that_mixed_both_models_is_charged_to_the_cloud` (`"{_NOTICE_AR}\n\nc|l"`). القاعدة الصحيحة أضيق: الحالة الوحيدة التي يبقى فيها `fallback` بـ `null` هي **بثّ** بدأه الأساسي ثم انقطع بعد أول chunk. والعقد المنشور هو ما يقرأه Android. | أعد صياغة الجملة في الثلاثة. مثلاً بالإنجليزية: «A stream the primary model had started answering and then lost mid-answer is not a fallback (`null`); a turn where the local model answered any call is.»، وبالعربية في `03 §2` بالمعنى نفسه. القيمة نفسها صحيحة، ومطابقة لـ `final.fallback`. |
| 3 | تحسين | `tests/unit/test_conversations_sql_statements.py:301` | `test_no_credential_can_reach_the_stored_fallback` لا يختبر شيئاً. `_fb()` لا يحمل أي سر، و`MessageFallback` لا حقل فيه لسر أصلاً، فلا يمكن أن يفشل الاختبار. الإثبات الحقيقي لـ AC‑2.6 موجود في `test_orchestrator.py::test_the_stored_fallback_carries_no_credential` و`test_llm_fallback.py::test_the_stored_fallback_carries_four_names_and_no_credential` (`_Fallback` بـ `api_key`). | احذفه، أو اجعله يمر بالمسار من `_Fallback(..., api_key="sk-secret").ref()` حتى `_fallback_to_json`. |
| 4 | تحسين | `tests/unit/test_orchestrator.py:3124` | `test_a_partial_reply_states_its_status_once` يمر بمسار المهلة نفسه الذي يمر به `test_a_timed_out_turn_is_stored_partial`. ذلك الاختبار يؤكد أصلاً `statuses == [None, "partial"]`. وفي مسار المهلة `terminal = True`، فلا يمر الدور بالـ `finally` ولا يُختبر الحارس `record.message`. الحارس يُختبر فعلاً في `test_message_status_qa.py::test_a_machinery_failure_after_tokens_is_stored_partial` (فرع `except Exception` ثم `finally`). | احذف الاختبار المكرر، أو انقله إلى مسار `except Exception` وسمِّه باسم AC‑1.11. |
| 5 | تحسين | `src/app/agents/orchestrator.py:2256` | تحذير `orchestrator.fallback_not_recorded` على مسار `_persist_partial` لا يغطيه اختبار. الاختباران `test_llm_fallback.py::test_a_module_that_drops_the_fallback…` و`test_message_status_qa.py::test_a_name_the_module_refuses…` يغطيان مسار `_persist_reply` وحده. | أضف اختباراً: `_FakeThreads(drop_fallback=True)`، ثم دور متحوّل يموت المحلي فيه بعد أول chunk، ثم تأكد من تحذير واحد. |
| 6 | تحسين | `docs/delivery/data-search-message-status/design.md:54`، `:358`، `:715`، `:733` | مسافات زائدة في آخر الأسطر. `git diff --check 0976bcb..HEAD` يفشل بسببها. | احذف المسافات الزائدة. |

## مطابقة التصميم

**المنسّق (`src/app/agents/orchestrator.py`):**
- يمر كل كاتب رسالة مساعد بالحالة صراحة، والحالة ثابت في الشيفرة لا قيمة مستنتجة من النص:
  - `_persist_reply` يمرر `complete` (`:2409`)؛
  - `_persist_partial` يمرر `partial` (`:2246`)، ويغطي المهلة وحدث `error` واستثناء المنسّق والتخلّي؛
  - `_persist_step_output` يمرر `complete` (`:1766`).
- `_stored_fallback` يستعمل شرط `_with_fallback_notice` نفسه (`fallback.switched`). وهو أيضاً الشرط الذي يضع `fallback` على `final` (`:2095`). لذلك النص المحفوظ و`fallback` المحفوظ والإطار لا يمكن أن يختلفوا.
- مفتاح `fallback` الذي يضعه الوكيل نفسه لا يُحفظ (د‑ت5).
- إطارات `final` و`error` و`notice` لم تتغير. لا يُضاف مفتاح جديد.
- `_warn_if_fallback_dropped` يسجّل `conversation_id` وحده (NFR‑8).
- تعليق ب‑10 (`:427-437`) وتعليق جملة التحول وdocstring `_turn_content` و`_persist_reply` و`_persist_partial` و`_persist_step_output` كلها محدّثة. عبارة «turning this marker into a field is ث-2, not decided here» حُذفت (AC‑5.6).

**العاملان (`src/app/workers/bootstrap.py:1090`، `:1183`):** يمرران `status=_STATUS_COMPLETE`، كما في د‑ت6. لا يوجد كاتب آخر لرسالة مساعد في `src/`. تحققت بالبحث عن `role=_ROLE_ASSISTANT`، فظهرت المواضع الخمسة فقط.

**الوحدة (conversations):**
- `domain` يستورد stdlib فقط (`enum`، `dataclasses`)، وفيه `MessageStatus` و`MessageFallback` وINV‑CV5.
- `application` لا يستورد sqlalchemy ولا fastapi.
- ترتيب الفحص في `AppendMessage.execute` يطابق §3.4: الحالة صارمة، و`fallback` ليّن في قيمه وحدها، والفحص كله قبل `get`.
- فرع `except ConversationError → ValidationError` الجديد يأتي بعد `ConversationDeletedError`، والترتيب صحيح.
- المحوّل: `Column("fallback", JSONB(none_as_null=True))`، ويُكتب العمودان في `INSERT` نفسه ويُقرآن في `select(messages)` نفسه.

**API:**
- `MessageOut.status` و`.fallback` بلا قيمة افتراضية، فهما حاضران دائماً.
- `_status_out` صريح، و`_fallback_out` يخدم المصدرين.
- الاسم البديل `from` مثبت على السلك وفي المخطط الحي.

**الترحيل `0008_message_status`:**
- `NOT VALID` بلا `VALIDATE`.
- العمودان nullable بلا `DEFAULT`.
- `downgrade` يحذف القيد قبل العمودين.
- `revision` و`down_revision` صحيحان.
- `test_zero_downtime_migrations.py` و`test_migration_revision_ids.py` خضراوان.

**الطبقات:** `lint-imports` يقول «9 kept, 0 broken»، ولا تعديل على `.importlinter`.

**الوثائق الملزمة:**
- `01 §2.4` و`03 §2/§3.1` و`06 §4` (ومعه INV‑CV5 ونقض ب‑10 رقم 1) و`openapi.yaml` متسقة مع الشيفرة، عدا الجملة في الملاحظة 2.
- `gaps.md` §ث‑2 دقيق: يذكر أن «فشل بلا رسالة» باقٍ في ث‑3 (AC‑5.7).

**النطاق:**
- كل الملفات من قائمة `design.md` §2، عدا `tests/unit/test_message_status_qa.py`. هذا ملف اختبارات جديد أضافه QA، وهو مقبول.
- لا تعديل على deploy أو CI أو إعدادات الأمن.

## الاختبارات

**ما شغّلته محلياً (بلا `tests/integration`):**
- `ruff format --check` و`ruff check` و`mypy src` (551 ملفاً) و`lint-imports`: كلها خضراء.
- ملفات الاختبار الثلاثة عشر التي غيّرها الفرق، وملفات الترحيل، و`tests/architecture`: كلها خضراء.

**جودة الاختبارات:**
- الاختبارات ذات معنى، وتفشل بلا التغيير.
- مسار `_FallbackLLM` الحقيقي مغطى: التحول المكتمل، والمحلي الذي يموت أثناء الجواب، والدور المختلط داخل البث، والسؤال الإنجليزي، وWebSocket.
- كل اختبار يُجمِّع SQL يستعمل `asyncpg.dialect()` (`_PG`، `test_conversations_sql_statements.py:49`). هذا درس ث‑1.
- الأجزاء المضافة بـ heredoc إلى `test_conversations_schema_parity.py` و`test_conversations_repository_rls.py` نظيفة: لا بقايا heredoc، وينتهي كل ملف بسطر جديد، وruff سليم.
- الاختباران الحيان (`RLS` الأربعة، و`test_message_status_migration_live.py`) قرأتهما ولم أشغّلهما. يتبعان سابقة `test_space_columns_live.py`: الرفع في `finally`، و`NullPool`، وضبط GUC قبل الإدراج.

**الفجوات:**
- الجزء الخاص بالنص في AC‑3.3 (الملاحظة 1).
- تحذير المسار الجزئي (الملاحظة 5).

**شرط قبل بوابة ②:** نجاح CI job `integration`. هو الدليل الوحيد على AC‑6.2 وعلى الذهاب والإياب في AC‑5.4.

</div>
