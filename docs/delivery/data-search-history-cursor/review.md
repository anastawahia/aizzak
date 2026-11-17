<div dir="rtl">

# مراجعة الشيفرة: ث‑5 قراءة السجل من الأحدث (cursor للخلف)

- **النطاق المراجَع:** `git diff master...feat/data-search-history-cursor` (`5d54b15...49f02c0`) في `/home/AIZZAK`، 26 ملفاً. قُرئ كل ملف معدَّل في `src/` كاملاً، والأقسام الجديدة في الاختبارات مع مساعداتها القائمة.
- **الحكم:** **PASS**. لا ملاحظة حاجبة. واحدة مهمة، والباقي تحسين.

## الملاحظات

| # | الشدة | الملف:السطر | الملاحظة | الإصلاح المطلوب |
|---|---|---|---|---|
| 1 | مهمة | `tests/unit/test_conversations_sql_statements.py:610` (يحرس `src/app/modules/conversations/adapters/sql_repository.py:490`) | التأكيد `"conversations.messages.seq <" in sql` يطابق `seq <=` أيضاً. فإن صار الشرط الخلفي `<=` في المحوّل (`$2` = 26 ⇒ `seq <= 26`) تظهر رسالة حدّ الصفحة مرتين في كل عبور خلفي، وتبقى كل اختبارات الوحدة خضراء: اختبارات `R` تعمل على المستودع المزيّف لا على المحوّل، و`SQL` يفحص نصاً جزئياً. لا يمسك هذا الخطأ إلا `RLS` الحي، وهو في CI فقط. أما الاتجاه الأمامي فمحروس بمقارنة النص كاملاً (`_master_forward_statement`) | ابنِ عبارة خلفية متوقعة مستقلة، كما في `_master_forward_statement`، بـ`seq < n` و`order_by(messages.c.seq.desc())`، وقارن `_sql(...)` كاملاً. أو اكتفِ بتأكيد أدق، مثل `"conversations.messages.seq < $" in sql` |
| 2 | تحسين | `src/app/modules/conversations/adapters/sql_repository.py:479`، و`tests/unit/support_conversations.py:231` | `backward = order is MessageOrder.DESC` مقارنة هوية على `StrEnum`. لو مرّر مستدعٍ النص `"desc"` (يمرّ وقت التشغيل، و`mypy` لا يفحص `tests/` ولا الاستدعاءات الديناميكية) لصار `backward` خطأً بصمت: صفحة تصاعدية بلا cursor، أو 422 مع cursor خلفي. لا مسار في `src` يمرر نصاً اليوم (الراوتر يحوّل بـ`MessageOrder(order)`) | `order == MessageOrder.DESC`، أو `MessageOrder(order) is MessageOrder.DESC` |
| 3 | تحسين | `src/app/framework/pagination.py:171`، و`:7` | docstring `_decode` يقول «both typed callers reject one on their own terms»، والفاكّات صارت ثلاثة. وعنوان `:7` «Two typed codecs» صار ثلاثة أزواج على مفتاحين. المعنى ما زال صحيحاً، والصياغة قديمة | «each typed decoder rejects one…»، و«Typed codecs, one per keyset (and, for ``seq``, per direction)» أو ما يشبهه |
| 4 | تحسين | `tests/unit/test_electrical_qa_pr_c_round2.py:5` | docstring الوحدة ما زال يقول «the bigint range of the conversation cursor untouched». بعد حذف الاختبار (د‑ت6) صار محوّل `conversations` محدوداً بـ`int4` عمداً، وما بقي (`test_conversation_style_seq_cursors_keep_the_bigint_range`) يثبت مدى الـ codec بلا `maximum` لا مدى المحوّل | «the unbounded ``decode_seq_cursor`` keeps the bigint range (the conversations adapter bounds it itself since th-5)» |
| 5 | تحسين | `tests/unit/test_api_conversations_router.py:3102` | الاسم `test_qa_the_order_parameter_does_not_leak_to_the_unbacked_message_routes`، والـ docstring «A viewer without conversations:read is refused identically» لا يصفان ما يُفحص: الاختبار يطلب المسار نفسه بلا توكن ويتوقع 401. لا مسار آخر يُطلب، ولا مستخدم بلا صلاحية (يغطي ذلك `test_desc_guards_are_the_asc_guards`، `:2899`، بما فيه 401 نفسه) | احذفه لأنه مكرر، أو سمِّه `test_qa_both_orders_need_a_token` وصحّح الـ docstring |
| 6 | تحسين | `tests/unit/test_ops_explain_hot_paths.py:46` (N‑2 من QA) | اختبار وحدة يستورد `_backward_scans` من `tests/integration/test_hot_path_plans_live.py`، فيحمّل معه `tests.integration.conftest` و`app.infrastructure`. تحققت: لا أثر جانبي (لا `pytestmark` ينتقل، ولا خدمة تُلمس)، ولهذا الاستيراد سابقة في ثلاثة ملفات وحدة قائمة (`test_live_harness_guards.py`، و`test_message_reply_to_migration.py`، و`test_message_status_migration_live_template.py`). فليس نمطاً جديداً | اختياري: انقل `_backward_scans` (ومعه `_index_scans`) إلى وحدة مساعدة خالصة تحت `tests/`، ويستوردها الملفان |
| 7 | تحسين | `docs/delivery/data-search-history-cursor/design.md:483` (N‑1 من QA) | قيم AC‑2.4 خاطئة في التصميم: «25..6 بلا 15 (19 عنصراً)، والثالثة 5..1». الصحيح، وهو ما يثبته `R` `:2703`: الصفحة الثانية 25..5 بلا 15 (20 عنصراً، لأن الصفحة تمتلئ من الأدنى)، والثالثة 4..1 و`null`. الاختبار مطابق لـ`stories.md:55`، والخطأ في وثيقة التصميم وحدها | صحّح السطر في `design.md` (وثيقة تسليم، لا عقد ملزم) |

**ليست ملاحظات على هذا الفرع:**
- **N‑3:** `CLAUDE.md:24` («8 contracts») و`design.md:505` («8 عقود») قديمان، و`lint-imports` يعدّ 9. الفرع لا يعدّل `.importlinter` (`git diff --stat` فارغ)، فالفرق سابق له. يُصحَّح `CLAUDE.md` في commit وثائق مستقل بقرار بشري.
- **`gaps.md` بلا تعديل:** مؤجل عمداً إلى ما بعد CI (`design.md` §6 خطوة 9، و`status.md`).

## مطابقة التصميم

**SQL في الاتجاهين.**
- **الأمامي:** النص نفسه. `ordering` هو `messages.c.seq` نفسه لا `.asc()`. والاختبار `test_list_messages_forward_sql_is_unchanged` يقارن النص المترجم والمعاملات بعبارة مستقلة، وهي مطابقة حرفياً للسطر المحذوف من `master`.
- **الخلفي:** `seq < n` و`ORDER BY seq DESC`، والشروط الثلاثة نفسها (`conversation_id`، و`workspace_id`، و`deleted_at IS NULL`)، و`LIMIT limit+1`. ترجمتُ العبارة بلهجة `asyncpg`، فخرجت `... seq < $2::INTEGER ... ORDER BY conversations.messages.seq DESC LIMIT $3::INTEGER`.
- **الـ cursor التالي:** `_paginate` بلا تغيير، فيرمّز آخر صف في الصفحة، وهو في الاتجاه الخلفي أصغر `seq`. و`_desc_seq_cursor_of` يرمّزه خلفياً (FR‑5).
- **الصفحة الخلفية الأولى بلا cursor:** أحدث `limit` رسالة ظاهرة، من الأحدث إلى الأقدم.
- **الحذف الناعم:** شرط على الصف، والـ cursor موضع `seq` لا عدّاد صفوف، فلا إزاحة ولا تخطٍّ.

**الـ codec.**
- **الحمولة الأمامية:** `str(seq)` بلا تغيير (`"MjA"`، `"MjY"`)، فالـ cursors الأمامية القديمة صالحة.
- **الوسم الخلفي:** `"<"`. `int("<26")` يفشل في الفاكّ الأمامي، و`decode_desc_seq_cursor` يرفض حمولة بلا الوسم. فالخلط 422 `common.invalid_cursor` في الاتجاهين.
- **حدّ `int4`:** `_INT4_MAX` على الفاكّين في المحوّل، قبل فتح الجلسة.
- **لا 500 من مدخل العميل.** كل فشل في `int()` هو `ValueError`، ويخرج بـ`_reject()`، ومنه سقف 4300 رقم في Python 3.12. و`""` و`"<"` و`"<<26"` و`"<abc"` وبايتات غير UTF‑8 كلها مرفوضة. أما ما يقبله `int()` من مسافات أو `+` أو أرقام غير ASCII، فقبوله مقصود في `design.md` §3.3. القيمة بعده محدودة المدى، وتُربط معاملاً.

**الطبقات.**
- `MessageOrder(StrEnum)` في `domain/value_objects.py`، وفيه stdlib وحده.
- `application/use_cases.py` يستورده من domain، بلا `fastapi` ولا `sqlalchemy`.
- المنفذ وحالة الاستخدام: `order` بقيمة افتراضية `ASC`، فلا يتغير مستدعٍ قائم.
- `composition_root` بلا تغيير.
- `lint-imports` أخضر، و`.importlinter` بلا تغيير.

**الراوتر.** `order: Annotated[Literal["asc", "desc"], Query()] = "asc"` (`routers/conversations.py:357`)، ثم `MessageOrder(order)`، كسابقة `electrical`. والقيمة الخاطئة 422 `common.validation_error` قبل حالة الاستخدام. ترتيب الحراسات كما هو: 401، ثم 403، ثم 422 للتحقق، ثم 404، ثم 422 للـ cursor.

**العقد والوثائق.**
- **`openapi.yaml`:** معامل `order` inline (`enum [asc, desc]`، `default: asc`، اختياري)، و`description` يذكر النقاط الخمس: الترتيب في كل اتجاه، و`next_cursor` في كل اتجاه، والخلط 422، و`null` في النهاية، ووصفة اللحاق. يطابق السلوك، ويطابق `/openapi.json` الحي باختبار `C`.
- **`03 §0`، و`02 §2`، و`06 §4`، وdocstrings `pagination.py` و`sql_repository.py` و`ports/repository.py` و`use_cases.py`:** النص المطلوب في `design.md` §3.7 حرفياً. و`grep` الصياغات القديمة («ONE exception»، و«One direction»، و«reads forward by») لا يجد شيئاً.
- **ما لا يُمس:** لا تعديل في `migrations/`، ولا `deploy/`، ولا `.github/`، ولا `.env.example`. ولا ملف خارج جدول §2 في التصميم.

**الاختبار المحذوف (د‑ت6).** `test_the_conversations_and_files_adapters_still_call_the_unbounded_forms` كان يفحص نص مصدر محوّل `conversations` وحده، رغم أن اسمه يذكر `files`. وغياب `maximum` الذي كان يثبته ألغاه قرار المالك (س‑5). صار الحدّ محروساً سلوكياً بـ`SQL` و`R` و`RLS` (AC‑4.4)، وبقي اختبار مدى الـ codec بلا `maximum`. فالحذف مبرَّر، ولم يبقَ إلا docstring الملف (الملاحظة 4).

**SQLAlchemy 2.0 أمام 2.1.** المحلي `2.0.51`، و`pyproject.toml` يطلب `>=2.0.36` بلا سقف، فقد يثبّت CI إصداراً أحدث. اختبار النص الأمامي يقارن عبارتين يترجمهما الإصدار المثبَّت نفسه، فلا يتأثر بالإصدار. والتأكيدات الخلفية نصوص ثابتة (`FROM conversations.messages`، و`ORDER BY conversations.messages.seq DESC`، والمعامل 21). لا ملاحظة.

## الاختبارات

**التغطية.** كل AC من الـ 37 له اختبار أو بند مراجعة (مصفوفة `test-plan.md` §1، وتحققت منها). اختبارات الوحدة تفشل بدون التغيير: المعامل `order`، والـ codec الخلفي، و`list_messages_orders`، والعبارة الخلفية، والعقد. ومسارات الخطأ مغطاة:
- `order` خاطئ: 9 قيم، منها حالة الأحرف والمسافات.
- cursor مشوّه: 11 شكلاً.
- الخلط بين الاتجاهين، وحدّا `int4`، والحراسات 401/403/404.
- 404 قبل 422 للـ cursor.

وتتبع الاختبارات نمط المشروع: مستودع مزيّف أمين يستعمل الـ codec والحدّ نفسيهما، وعبارات بلهجة `_PG` على المحوّل الحقيقي، والحي تحت `live_db` في CI فقط. شغّلت الاختبارات المستهدفة: 86 passed.

**الضعف الوحيد** هو الملاحظة 1: الشرط الخلفي `<` غير محروس بدقة في الوحدة.

**`test_hot_path_plans_live.py`** (لم يُشغَّل هنا، وقُرئ مقابل المخطط):
- **الـ fixture:** يملأ كل عمود `NOT NULL` بلا قيمة افتراضية، في `0001`…`0009` (`space_id` وأعمدة `0007`–`0009` كلها `NULL`able). ويكتب بصفة `app_rw` تحت GUC المساحة، فيحقق `WITH CHECK`. و`:deleted` في `CASE WHEN` يُستنتج `boolean`. و`'{"text": "m"}'` لا يصير معامل ربط، لأن النقطتين يتبعهما فراغ. و`truncate_tables` يفرغ جداول `conversations` بعد كل اختبار.
- **الخطة:** `Index Scan` و`uq_msg_seq` و`Scan Direction = Backward`، بلا `Sort` ولا `Seq Scan`، وتحت قيد RLS. و`rows_read <= 21 + 5` صحيح بحساب `summarise`: صفوف الفحص الفعلية زائد `Rows Removed by Filter`.
- **عزل `DROP CONSTRAINT`، وهو آمن في قاعدة CI:**
  - داخل `async with conn.begin()` يُلغى دائماً، ويُلغى أيضاً إن رمى `EXPLAIN` استثناءً.
  - pytest في CI تسلسلي، بلا xdist، فلا جلسة أخرى تنتظر قفل `ACCESS EXCLUSIVE`.
  - القفل على قاعدة `aizzak_test` وحدها.
  - لا قيد أجنبي يعتمد على `uq_msg_seq` (تحققت من `0001`…`0009`)، فالحذف لا يحتاج `CASCADE`.
  - المالك `NOSUPERUSER` وخاضع لـ`FORCE RLS`، و`test_the_owner_gets_the_same_transcript_plan_as_app_rw` يثبت أنه يرى الخطة نفسها.
  - و`test_uq_msg_seq_comes_back_on_rollback` يفحص `pg_constraint` بعد الإلغاء.
- **قدرة الاختبار على الفشل:** بعد الحذف لا يبقى على `messages` إلا المفتاح الأساسي `id`، فالخطة `Seq Scan` و`Sort` حتماً. والتأكيد `Sort or Seq Scan` يمسك ذلك.

**`test_conversations_repository_rls.py`:**
- الصفحات الخلفية `[4, 3]` ثم `[1]` و`None`، مع حذف 5 و2 بصفة المالك.
- الخلط في الاتجاهين.
- العزل: المساحة B ترى `[]`.
- حدّ `int4` في الاتجاهين، وقبل أي قراءة.
- المساعدات المستعملة موجودة بالتواقيع نفسها.

</div>
