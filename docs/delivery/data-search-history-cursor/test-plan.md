<div dir="rtl">

# خطة الاختبار: ث‑5 قراءة السجل من الأحدث (cursor للخلف)

الفرع `feat/data-search-history-cursor`. الاختصارات: `R` = `tests/unit/test_api_conversations_router.py`، `SQL` = `tests/unit/test_conversations_sql_statements.py`، `UC` = `tests/unit/test_conversations_use_cases.py`، `F` = `tests/unit/test_foundation.py`، `C` = `tests/unit/test_api_conventions.py`، `X` = `tests/unit/test_ops_explain_hot_paths.py`، `RLS` = `tests/integration/test_conversations_repository_rls.py`، `PLAN` = `tests/integration/test_hot_path_plans_live.py`. «حي (CI)» = مكتوب ويُجمع بلا خطأ (`--collect-only`: 53 + 25 اختباراً)، ولم يُشغَّل هنا لأن المكدس الحي مستعمل (CLAUDE.md).

## 1. مصفوفة التغطية

| AC | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑1.1 | وحدة | `R::test_desc_first_page_is_the_newest_twenty_newest_first` | ✅ |
| AC‑1.2 | وحدة | `R::test_desc_short_thread_is_one_page_with_a_null_cursor` | ✅ |
| AC‑1.3 | وحدة | `R::test_desc_exactly_limit_messages_has_no_phantom_page` (+ `LIMIT` = 21 في `SQL::test_list_messages_desc_is_one_select_below_the_cursor_newest_first`) | ✅ |
| AC‑1.4 | وحدة | `R::test_desc_of_a_fresh_thread_is_an_empty_page`، و`R::test_qa_a_thread_of_only_soft_deleted_messages_is_an_empty_page` (QA) | ✅ |
| AC‑1.5 | وحدة + حي (CI) | `R::test_desc_first_page_skips_the_soft_deleted_newest`؛ `RLS::test_list_messages_newest_first_pages_by_seq_cursor_and_excludes_deleted` | ✅ |
| AC‑1.6 | وحدة | `R::test_desc_and_asc_walks_return_the_same_messages_byte_for_byte` | ✅ |
| AC‑2.1 | وحدة + حي (CI) | `R::test_desc_full_walk_is_three_pages_of_20_20_5`؛ `RLS` (نفس اختبار 1.5، صفحات بالـ cursor الحقيقي) | ✅ |
| AC‑2.2 | وحدة | `R::test_desc_walk_is_the_exact_reverse_of_the_asc_walk_across_gaps` | ✅ |
| AC‑2.3 | وحدة | `R::test_messages_appended_mid_walk_never_enter_its_later_pages`، و`R::test_qa_appends_mid_backward_walk_with_a_tiny_limit_never_duplicate_or_skip` (QA، `limit=3` مع إلحاق بعد كل صفحة) | ✅ |
| AC‑2.4 | وحدة | `R::test_a_soft_delete_mid_walk_neither_shifts_nor_skips` (انظر §3، الانحراف 1) | ✅ |
| AC‑2.5 | وحدة | `R::test_a_non_null_desc_cursor_has_an_older_message_unless_all_were_deleted` | ✅ |
| AC‑2.6 | وحدة | `R::test_the_catch_up_recipe_finds_every_new_message_once` | ✅ |
| AC‑2.7 | وحدة | `R::test_desc_first_page_holds_the_turn_and_the_reply_to_it` | ✅ |
| AC‑3.1 | وحدة | `R::test_the_default_walk_is_unchanged` + بقية اختبارات `GET …/messages` القائمة (خضراء بلا تعديل) | ✅ |
| AC‑3.2 | وحدة | `R::test_no_order_and_order_asc_are_byte_identical` | ✅ |
| AC‑3.3 | وحدة | `R::test_a_cursor_minted_before_the_change_still_reads_forward`؛ `F::test_seq_cursor_payload_is_unchanged` | ✅ |
| AC‑3.4 | وحدة | `SQL::test_list_messages_forward_sql_is_unchanged` (المرجع `_master_forward_statement` مطابق للسطر المحذوف من `master`)، `SQL::test_an_explicit_asc_order_is_the_forward_statement` | ✅ |
| AC‑3.5 | وحدة | `R::test_page_keys_are_the_same_in_both_directions` | ✅ |
| AC‑3.6 | وحدة (عقد) | `C::test_the_order_parameter_is_declared_on_list_messages_alone` | ✅ |
| AC‑4.1 | وحدة | `R::test_an_unknown_order_is_a_validation_error[foo/DESC/""]`، و`R::test_qa_order_is_case_and_whitespace_strict` (QA، 9 قيم منها عربي) | ✅ |
| AC‑4.2 | وحدة + حي (CI) | `R::test_a_cursor_never_crosses_directions`؛ `SQL::test_a_cursor_of_the_other_direction_runs_no_statement`؛ `F::test_seq_cursors_refuse_the_other_direction`؛ `RLS` (الخلط حياً داخل اختبار 1.5) | ✅ |
| AC‑4.3 | وحدة | `R::test_desc_refuses_malformed_and_foreign_cursors`، و`R::test_qa_desc_refuses_every_malformed_cursor_with_422_and_never_500` (QA، 11 مدخلاً)؛ `F::test_decode_desc_seq_cursor_rejects_garbage` | ✅ |
| AC‑4.4 | وحدة + حي (CI) | `SQL::test_a_seq_past_int4_runs_no_statement_in_either_direction`، `SQL::test_int4_edges_reach_the_database_in_either_direction`، `R::test_a_cursor_past_int4_is_invalid_cursor_not_500`، `R::test_qa_the_int4_boundary_is_exact_in_both_directions` (QA)؛ `RLS::test_list_messages_refuses_a_seq_past_int4` | ✅ |
| AC‑4.5 | وحدة | `R::test_desc_limit_keeps_its_bounds`، و`R::test_qa_desc_limit_bounds_on_a_long_thread` (QA، يضيف `""`، `1.5`، رقماً عربياً) | ✅ |
| AC‑4.6 | وحدة | `R::test_desc_guards_are_the_asc_guards` (401/403/404 مجهولة/محذوفة/مساحة أخرى)؛ `UC::test_list_messages_desc_on_a_missing_or_deleted_thread_never_reads_messages`؛ `R::test_qa_an_unknown_thread_is_404_before_a_bad_cursor_is_looked_at` (QA) | ✅ |
| AC‑4.7 | وحدة | `R::test_a_desc_cursor_at_or_below_the_oldest_is_an_empty_last_page` | ✅ |
| AC‑5.1 | وحدة | `SQL::test_list_messages_desc_is_one_select_below_the_cursor_newest_first`، `SQL::test_list_messages_desc_page_cursor_is_a_desc_cursor_of_the_last_row` | ✅ |
| AC‑5.2 | حي (CI فقط) | `PLAN::test_the_newest_first_transcript_page_scans_uq_msg_seq_backward`، `…reads_a_page_plus_the_deleted_rows_it_passes`، `…without_uq_msg_seq…loses_its_backward_scan`، `…owner_gets_the_same_transcript_plan_as_app_rw`، `…uq_msg_seq_comes_back_on_rollback`؛ وتوأم وحدة `X::test_the_backward_scan_reader_reads_a_canned_plan` | ✅ (مكتوب، يُجمع، لم يُشغَّل) |
| AC‑5.3 | مراجعة | `git diff master...HEAD --stat -- migrations` فارغ (تحقق QA) | ✅ |
| AC‑5.4 | حي (CI فقط) | `RLS::test_list_messages_newest_first_is_tenant_scoped` | ✅ (مكتوب، يُجمع، لم يُشغَّل) |
| AC‑5.5 | وحدة | `X` الاختبارات المعلمة على `CATALOGUE` (المدخل `conversations.list_messages_newest_first`) | ✅ |
| AC‑5.6 | بوابة | `lint-imports`: 9 عقود kept، `.importlinter` بلا تغيير | ✅ |
| AC‑6.1 | وحدة (عقد) + مراجعة | `C` (AC‑3.6)؛ وصف العملية في `openapi.yaml` يذكر النقاط الخمس (قراءة QA) | ✅ |
| AC‑6.2 | مراجعة | فروق `03 §0`، `02 §2`، `06 §4`، وdocstrings `pagination.py`، `sql_repository.py`، `ports/repository.py` محدّثة (قراءة QA للفرق) | ✅ |
| AC‑6.3 | مراجعة | مؤجل عمداً بعد CI (`design.md` §6 خطوة 9، `status.md`): `gaps.md` لم يُعدَّل بعد | 🟡 مؤجل بقرار التصميم، لا يمنع المرحلة |
| AC‑6.4 | مراجعة | لا ملف في مستودع Android من هذا الفرع. (فيه تعديلات غير محفوظة سابقة لا تخص الميزة) | ✅ |
| AC‑6.5 | بوابة | الخمس محلياً خضراء (§2)؛ `test-rules.sh` أعاده المنسّق (OK، 31 قاعدة) ولم أعده؛ CI `integration` لم يجرِ بعد | 🟡 بانتظار CI |

**الحصيلة (37 معياراً):** تغطية بوحدة محلية (وحدها أو مع حي) = 27 (AC‑1.1…1.6، 2.1…2.7، 3.1…3.6، 4.1…4.7، 5.1، 5.5)؛ حي CI فقط = 2 (AC‑5.2، 5.4)؛ مراجعة/بوابة = 7 (AC‑5.3، 5.6، 6.1…6.5، منها 6.1 بوحدة أيضاً)، منها اثنان معلّقان بقصد (AC‑6.3 بعد CI، AC‑6.5 بانتظار CI). لا معيار بلا تغطية. لا 🔴.

## 2. نتائج بوابات المشروع

من `/home/AIZZAK` بـ `.venv`:

```
$ .venv/bin/ruff format --check .
1010 files already formatted                       (exit 0)
$ .venv/bin/ruff check .
All checks passed!
$ .venv/bin/mypy src
Success: no issues found in 552 source files
$ .venv/bin/lint-imports
...
Framework does not import outer layers KEPT
WireTwin sections are independent and shared is the floor KEPT
Contracts: 9 kept, 0 broken.
$ .venv/bin/pytest -rs tests/unit tests/architecture tests/eval
9241 passed, 37 skipped, 7 warnings in 295.44s (0:04:55)     (exit 0)
```

- الـ 37 المتخطاة: كلها «opt-in container harness» (`RUN_EDGE_FILES_TEST` وأشباهه) لا صلة لها بالميزة. لم تُضف فلاتر.
- قبل إضافاتي: 9210 passed (تقرير المنسّق). بعدها 9241، أي +31 اختباراً (QA).
- `deploy/prometheus/test-rules.sh` (يحتاج Docker): لم أشغّله. اعتمدت نتيجة المنسّق (OK، 31 قاعدة). لا تغيير في `deploy/` في الفرع.
- `tests/integration/`: لم يُشغَّل (المكدس الحي مستعمل). `--collect-only` على ملفي الحي المعدّلين: 53 و25 اختباراً بلا خطأ جمع. تحذير: عقد lint-imports يعدّ الآن 9 لا 8 كما في CLAUDE.md (CLAUDE.md قديم، ليس خللاً في الفرع).

## 3. الاختبار الاستكشافي

مستوى الـ API بـ `TestClient` على المستودع المزيّف الأمين (يستعمل الـ codec نفسه وحدّ `int4` نفسه). الـ SQL الحقيقي مغطى بـ `SQL` (عبارات مترجمة) وحياً في CI.

**فحص انحرافات المطوّر الخمسة:**

| # | الانحراف | الحكم |
|---|---|---|
| 1 | AC‑2.4: قيم design.md خاطئة | **صحيح.** بعد حذف 15 و40: الصفحة الأولى 45…26 (الـ cursor `<26`). الثانية `seq < 26` الظاهرة = 25…5 بلا 15 = 20 عنصراً (لا 19 كما في design، لأن الصفحة تمتلئ من الأدنى)، ثم الثالثة 4…1 و`null`. الاختبار يقارن القائمة الكاملة بالقيمة الدقيقة (لا شفع، لا تخطٍّ، لا تكرار)، ويثبت `len == 20` و`next_cursor is None`. حذف 40 (مقروءة) لا أثر له، كما يجب. المستودع المزيّف يطبّق `seq < cursor` فعلاً كالـ SQL، والـ SQL مغطى بـ `SQL`. يُقترح تصحيح نص `design.md` §5 (AC‑2.4: 19 → 20) في مرحلة لاحقة |
| 2 | اختبارا `forward-unchanged` وpayload كُتبا مع الشيفرة لا قبلها | **مقبول.** تحققت يدوياً أن `_master_forward_statement` مطابق حرفياً للسطر المحذوف من `master` (3 شروط، `seq > n`، `ORDER BY seq`، `LIMIT n+1`)، وأن `"MjA"` و`"MjY"` ثابتان حرفياً، فالمرجع مستقل عن المحوّل. لم أنفّذ الاختبار على شجرة `master` لأنه يستورد `MessageOrder` |
| 3 | اختبار `explicit asc` إضافي | **مقبول**، يسدّ ثغرة (الافتراضي الصريح = العبارة الأمامية) |
| 4 | `X` يستورد `_backward_scans` من ملف integration | **مقبول مع ملاحظة.** الاستيراد لا يلمس خدمة: ثوابت DSN نصية فقط، و`pytestmark` على الوحدة المستوردة لا ينتقل لاختبارات `X`. شُغّل `X` وحده (بلا بيئة) فنجح، والحزمة الكاملة نجحت. الكلفة: يتطلب تثبيت تبعيات `dev` (`hvac`، `minio`، …) في كل تشغيل وحدة، وهو الحال أصلاً في CI (`pip install -e .[dev]`). ملاحظة غير مانعة، والأنظف نقل المساعد إلى `tests/support` |
| 5 | حذف `test_the_conversations_and_files_adapters_still_call_the_unbounded_forms` | **مقبول.** الاختبار فحص نصّ مصدر لغياب `maximum`، وقد صار الحدّ مقصوداً (ق‑1/س‑5). يحرسه الآن سلوكاً `SQL` و`R` و`RLS` (AC‑4.4)، واختبار `…keep_the_bigint_range` للـ codec بلا `maximum` باقٍ |

**اختبارات QA الجديدة (كلها وحدة، في ذيل `R`، بعضها معلَّم):**

| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| محادثة مجهولة + cursor تالف | `GET /conversations/<uuid7 جديد>/messages?order=asc\|desc&cursor=!!!!` | 404 `common.not_found` قبل 422 | 404 | ✅ |
| حدّ `int4` بالضبط | cursor `2^31−1` ثم `2^31`، `−2^31`، `2^63` بكل اتجاه؛ وسالب داخل المدى | `2^31−1` ⇒ 200؛ الباقي 422 `common.invalid_cursor`؛ السالب ⇒ 200 (desc فارغ، asc من البداية) | كما المتوقع | ✅ |
| cursor مشوّه (11 شكلاً) | `%%%`، حرف عربي، أرقام عربية‑هندية، `<` وحده، `<<26`، `<abc`، `<2.5`، بايتات غير UTF‑8، حمولة أمامية `26`، حمولة فارغة | 422 `common.invalid_cursor`، لا 500 | كما المتوقع | ✅ |
| cursor خلفي مبطّن `PDI2=` | إرساله مع `order=desc` | (غير محدد في التصميم) | 200، نفس الموضع | ℹ️ سلوك `_decode` القائم لكل المؤشرات (يُكمل الحشو ويفكّ لموضع نفسه، لا موضع مختلف). لا خطر. غير مختبَر كرفض |
| round trip عبر HTTP | 403 قيماً: `0…399` و`4211` و`99999` و`2^31−1` | الـ cursor بلا `+/=` ومقبول والصفحة صحيحة | كما المتوقع | ✅ |
| محادثة كلها محذوفة ناعماً | 6 رسائل كلها محذوفة، كل اتجاه، `limit=2` | `data: []`، `meta: {next_cursor: null, limit: 2}` | كما المتوقع | ✅ |
| حدود `limit` مع `order=desc` | `1`، `100`، ثم `101`، `0`، `-1`، `""`، `1.5`، رقم عربي، على 105 رسائل | 1 و100 ⇒ 200 بالمحتوى الصحيح؛ الباقي 422 `common.validation_error` | كما المتوقع | ✅ |
| `order` غير صالح | `Desc`، `dEsC`، `DESC`، ` desc`، `desc `، `descending`، `0`، `null`، حرف عربي | 422 `common.validation_error` | كما المتوقع | ✅ |
| `order` مكرر | `?order=asc&order=desc` | لا 5xx | 200 أو 422 | ✅ |
| double submit | نفس الطلب مرتين، بلا وبمع cursor | جسدان متطابقان بايتاً (قراءة بلا أثر) | متطابقان | ✅ |
| إلحاق أثناء عبور خلفي بحدّ صغير | 12 رسالة، `limit=3`، إلحاق رسالة بعد كل صفحة | `12..1` كاملة، بلا تكرار ولا تخطٍّ، والجديدة لا تظهر | كما المتوقع | ✅ |
| صلاحيات | بلا توكن؛ بلا `conversations:read`؛ مساحة أخرى (في اختبار المطوّر 4.6 + اختباري) | 401 / 403 `authz.forbidden` / 404 | كما المتوقع | ✅ |
| RTL/LTR، 375px | — | — | لا واجهة في هذا المستودع. غير منطبق | ⏭ |
| أخطاء الشبكة/الـ API، شبكة تنقطع | — | الخادم بلا حالة | غير منطبق، يغطيه تصميم القراءة بلا أثر | ⏭ |
| تشغيل المكدس الحي | — | — | لم يُجرَ (المكدس الحي مستعمل؛ بلا موافقة بشرية). الإثبات الحي (RLS، الخطة) في CI فقط | ⏭ |
| نص عربي 100,000 حرف | — | مطابقة بايتية بين الاتجاهين | يغطيه AC‑1.6 للمطوّر | ✅ |

## 4. الأخطاء المكتشفة

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| — | لا أخطاء حاجبة ولا رئيسية في الشيفرة | — | — | — |
| N‑1 | `design.md` §5، AC‑2.4: «19 عنصراً» و«25..6» خطأ. الصحيح 20 عنصراً (25…5 بلا 15). الاختبار صحيح والوثيقة لا | minor (وثائق) | قارن `design.md` §5 (AC‑2.4) بـ`R::test_a_soft_delete_mid_walk_neither_shifts_nor_skips` | مفتوح، المالك: software-architect/المنسّق، غير مانع |
| N‑2 | `X` (وحدة) يستورد من ملف `tests/integration/…` | minor (بنية اختبارات) | `grep -n "tests.integration" tests/unit/test_ops_explain_hot_paths.py` | مفتوح، المالك: backend-developer، غير مانع |
| N‑3 | CLAUDE.md يقول «8 contracts» وlint-imports يعدّ 9 | minor (وثائق) | `.venv/bin/lint-imports \| tail -1` | مفتوح، خارج نطاق الميزة |

## 5. الحكم

**PASS** — البوابات 1–5 خضراء محلياً (9241 passed، 37 skipped لا صلة لها، exit 0)، كل المعايير الـ 37 مغطاة (اثنان حيان في CI فقط: AC‑5.2، AC‑5.4)، لا أخطاء حاجبة أو رئيسية. معلّق خارج نطاق QA: CI `integration` (AC‑5.2، 5.4، 6.5) و`test-rules.sh` ما أعدته، وتحديث `gaps.md` (AC‑6.3) بعد CI.

</div>
