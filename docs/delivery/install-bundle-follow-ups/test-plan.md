<div dir="rtl">

# خطة الاختبار: إصلاح الملاحظات المؤجَّلة من حزمة التثبيت

لكلّ ملاحظةٍ قسمٌ باسمها. القسم الحاليّ: SR‑2.

## SR‑2

الفرع `fix/install-bundle-follow-ups`، الإصلاح `10d02f6`، واختبارات QA الإضافيّة `7edb69b`.

### 1. مصفوفة التغطية
كلّ الاختبارات وحدات في `tests/unit/test_install_sh.py`. وقد قُرئ كلّ اختبارٍ للتأكّد أنّه يؤكّد ما يقوله المعيار، وأنّ تأكيداته قابلة للفشل (انظر الأحمر في §3).

| AC | نوع | الملف/الاختبار | الحالة |
|---|---|---|---|
| AC‑SR2.1 | وحدة | `test_a_0600_env_is_never_rewritten_through_a_more_open_temp_file` (إنشاء، `fchmod` بحجم 0، `fsync`؛ `fchmod` مرّة بـ0o600) | ✅ |
| AC‑SR2.1 | وحدة | `test_set_secret_id_on_a_0600_env_never_opens_a_wider_temp_file` (سيناريو التجربة 1) | ✅ |
| AC‑SR2.2 | وحدة | `test_a_new_env_temp_file_is_owner_only_until_it_gets_the_umask_mode` (لا `fchown`، `fchmod` بـ0o644) | ✅ |
| AC‑SR2.2 | وحدة، صدفة حقيقيّة | `test_a_new_env_takes_0666_minus_the_umask[022,027,002,077,277]` | ✅ |
| AC‑SR2.2 | قائمة | `test_compose_created_writes_the_template_with_the_changes_and_an_install_block` وأخواته | ✅ (ضمن الملفّ كاملاً) |
| AC‑SR2.3 | وحدة | `test_an_existing_env_gets_its_owner_then_its_mode_before_the_first_byte` (تسلسل open، fchown، fchmod، fsync) | ✅ |
| AC‑SR2.3 | وحدة | `test_an_existing_env_keeps_its_group_through_a_rewrite` (يتخطّى إن لم تكن للمستخدم مجموعةٌ ثانية؛ لم يتخطَّ هنا) | ✅ |
| AC‑SR2.3 | وحدة | `test_a_refused_fchown_is_ignored_unless_root[1000,0]` | ✅ |
| AC‑SR2.3 | **إضافة QA** | `test_the_temp_file_descriptor_is_closed_when_fchown_or_fchmod_raises[fchown,fchmod]` (الواصف يُغلق: EBADF، لا مؤقّت، `.env` كما هو) | ✅ |
| AC‑SR2.4 | وحدة | `test_a_link_planted_at_the_temp_path_is_refused_and_nothing_is_written_through_it[False,True]` | ✅ |
| AC‑SR2.5 | قائمة | `test_a_stale_temp_file_from_a_killed_run_is_removed_by_the_next_write` وأخواتها، والبوّابات الستّ، و`git diff` | ✅ |
| (حارس realpath) | **إضافة QA** | `test_a_symlinked_env_is_rewritten_in_place_and_stays_a_link` | ✅ |

كلّ المعايير مغطّاة، ولا فجوة.

### 2. نتائج بوابات المشروع
على `/home/AIZZAK`، بعد commit QA، وفي `tests/unit tests/architecture tests/eval` وحدها (المكدّس الحيّ مستعمَل، فلا `tests/integration`).
```
1. ruff format --check .   -> 833 files already formatted
2. ruff check .            -> All checks passed!
3. mypy src                -> Success: no issues found in 467 source files
4. lint-imports            -> Contracts: 8 kept, 0 broken.
5. pytest -rs tests/unit tests/architecture tests/eval
                           -> 6199 passed, 7 warnings in 189.95s  (لا متخطّى)
6. test-rules.sh           -> SUCCESS: 29 rules found / test-rules: OK
```
(6196 عند المطوّر + 3 اختبارات QA: اثنان بـ`parametrize` للواصف وواحد للرابط.)

`git diff master --stat` (بعد commit QA): `deploy/release/install.sh` (+32/‑…) و`tests/unit/test_install_sh.py` فقط، أي AC‑SR2.5 محقَّق. وثائق `docs/delivery/` غير مُلتزَمة بعد (لدى المنسّق).

### 3. الأحمر ثمّ الأخضر
فُحص في worktree مؤقّت على `4b44f54` (القديم) بنسخ ملفّ الاختبار الجديد إليه، ثمّ أُزيل الـworktree. لم تُمسّ شجرة الفرع.
- **القديم:** ستّة فشلت بالضبط، وهي الستّة التي يسمّيها التصميم: `…never_rewritten_through_a_more_open_temp_file`، و`…set_secret_id_on_a_0600_env…`، و`…new_env_temp_file_is_owner_only…`، و`…gets_its_owner_then_its_mode…`، و`…link_planted…[False]`، و`…link_planted…[True]`. وباقي الملفّ نجح، ومنه حرّاس `…takes_0666_minus_the_umask` و`…keeps_its_group…` و`…refused_fchown…` (لم تظهر في قائمة الفاشلة).
- **اختبار QA للواصف:** يفشل على القديم (الحالتان `fchown` و`fchmod`: الواصف يتسرّب)، وينجح على `10d02f6`. واختبار الرابط الرمزيّ يجتاز على القديم والجديد (حارس سلوك).
- **الجديد `10d02f6`:** `tests/unit/test_install_sh.py` كلّه أخضر.

### 4. الاختبار الاستكشافي
| السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|
| `umask 277` | `…takes_0666_minus_the_umask[277]` بصدفة وبرنامج حقيقيَّين | `.env` بـ0400، لا مؤقّت | 0400، لا مخلَّفات | ✅ |
| هدفٌ رابطٌ رمزيّ إلى `.env` حقيقيّ | اختبار QA الجديد | يُستبدل الملفّ الحقيقيّ، ويبقى الرابط والصلاحيّة 0640 | كما متوقّع | ✅ |
| مجلّدٌ للقراءة فقط (0500) | `size-block` على `.env` قائم (اختبار مؤقّت أُزيل) | الرمز 4، `.env` لم يتغيّر، لا مؤقّت | الرمز 4 `PermissionError while handling .env`، البايتات كما هي، لا مؤقّت | ✅ |
| كنس `.install-tmp-*` القديم | الاختبار القائم | يُحذف قبل الكتابة | يُحذف | ✅ |
| إغلاق الواصف عند فشل `fchown`/`fchmod` (root مزيَّف) | اختبار QA الجديد | `EBADF` على الواصف، الرمز 4، لا تسريب | كما متوقّع | ✅ |
| رابطٌ مزروع في مسار المؤقّت (قائم/معلَّق) | الاختبار المصمَّم | الرمز 4، لا كتابة عبره | كما متوقّع | ✅ |
| `fchown` مرفوض لغير root / root | الاختبار المصمَّم | يُتجاهل / الرمز 4 | كما متوقّع | ✅ |

لم يُجرَّ: مجموعةٌ تكميليّة فعليّة تحت root (لا صلاحيّة root على المضيف)؛ يغطّيه التزييف بـ`geteuid`.

### 5. الأخطاء المكتشفة
| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| — | لا أخطاء | — | — | — |

ملاحظة (ليست خطأً): فحص `fsync` في اختبار الملفّ الجديد مُستثنى عمداً (`through_fsync=False`) لأنّ المؤقّت يأخذ 0644 قبله بقرار Q‑4. هذا موثَّق في المطوّر وصحيح.

### 6. الحكم
**PASS** — كلّ معايير AC‑SR2.1…5 مغطّاة باختباراتٍ تفشل فعلاً على القديم (الستّة) وتنجح على الجديد، والبوّابات الستّ خضراء (6199 ناجحاً)، ولا أخطاء. أُضيف commit اختباراتٍ واحد `7edb69b`.

(الجولة 1 أعلاه تاريخ؛ بعدها وجدت المراجعة الأمنية SR2‑1 وSR2‑2 وSR2‑3، فجاءت الجولة 2.)

### الجولة 2 (بعد الإعادة 1: `1b18fce` فوق `7edb69b`)

#### 2‑1. مصفوفة التغطية AC‑SR2.1…8
كلّها في `tests/unit/test_install_sh.py`. قُرئت التأكيدات وتحقّقت من أنّها تفشل فعلاً (الأحمر أدناه).

| AC | الاختبار | الحالة |
|---|---|---|
| AC‑SR2.1 | `test_a_0600_env_is_never_rewritten_through_a_more_open_temp_file`، `test_set_secret_id_on_a_0600_env_never_opens_a_wider_temp_file` (معدَّل: `fchmod` مرّة بـ0o600) | ✅ |
| AC‑SR2.2 | `test_a_new_env_temp_file_is_born_with_its_final_mode`، `test_a_new_env_takes_0666_minus_the_umask[022,027,002,077,277]` | ✅ |
| AC‑SR2.3 | `test_an_existing_env_gets_its_owner_then_its_mode_before_the_first_byte`، `…keeps_its_group_through_a_rewrite`، `test_a_refused_fchown_drops_the_group_bits_unless_root[1000,0]`، `test_the_temp_file_descriptor_is_closed_when_fchown_or_fchmod_raises[fchown,fchmod,removexattr]` | ✅ |
| AC‑SR2.4 | `test_a_link_planted_at_the_temp_path_is_refused_and_nothing_is_written_through_it[dangling × existing/new]` | ✅ |
| AC‑SR2.5 | البوّابات الستّ، و`git diff master --stat`، ولا `SKIPPED` | ✅ |
| AC‑SR2.6 | `test_a_new_env_gets_what_the_directory_default_acl_gives[owner-only,named-user]` | ✅ |
| AC‑SR2.7 | `test_an_existing_env_keeps_its_access_acl`، `test_an_existing_env_does_not_take_the_directory_default_acl` | ✅ |
| AC‑SR2.8 | `test_a_non_root_writer_keeps_the_group_when_only_the_owner_is_refused`، `test_a_refused_fchown_drops_the_group_bits_unless_root[1000]` | ✅ |

لا فجوة، فلم يُضَف اختبار ولا commit في هذه الجولة.

#### 2‑2. الأحمر ثمّ الأخضر
worktree مؤقّت على `7edb69b` مع نسخ ملفّ الاختبار الحاليّ (`1b18fce`) إليه، ثمّ أُزيل:
- **على `7edb69b`:** `11 failed, 166 passed`. الأحد عشر بالضبط التي في §6 من التصميم: `…born_with_its_final_mode`، و`…default_acl_gives[owner-only]` و`[named-user]`، و`…gets_its_owner_then_its_mode…`، و`…keeps_its_access_acl`، و`…does_not_take_the_directory_default_acl`، و`…drops_the_group_bits_unless_root[1000]`، و`…keeps_the_group_when_only_the_owner_is_refused`، و`…link_planted…[False-new]` و`[True-new]`، و`…descriptor_is_closed…[removexattr]`. والحرّاس (AC‑SR2.1، umask، keeps_its_group، الرابط للقائم، الباقي) نجحت.
- **على `1b18fce`:** `tests/unit/test_install_sh.py`: `177 passed`، ولا متخطّى (`-rs`).

#### 2‑3. بوّابات الجولة 2
```
1. ruff format --check .   -> 833 files already formatted
2. ruff check .            -> All checks passed!
3. mypy src                -> Success: no issues found in 467 source files
4. lint-imports            -> Contracts: 8 kept, 0 broken.
5. pytest -rs tests/unit tests/architecture tests/eval
                           -> 6207 passed, 7 warnings in 187.69s  (لا متخطّى)
6. test-rules.sh           -> test-rules: OK
```
`git diff master --stat`: `deploy/release/install.sh` ‏(65) و`tests/unit/test_install_sh.py` ‏(437) فقط. اختبارات ACL تجري على هذا المضيف ولا تُتخطّى (ext4 بـACL؛ تتخطّى عند `ENOTSUP` وحده).

#### 2‑4. الاختبار الاستكشافي بـACL حقيقيّة (`setfacl`/`getfacl`، اختبارات مؤقّتة أُزيلت)
| السيناريو | الفعلي | الحكم |
|---|---|---|
| `.env` جديد في مجلّد بـACL افتراضيّ `u:65534:r,m::r-x`، عبر `run_function` (صدفة و`python3 -I` حقيقيَّان، `umask 022`) | الصلاحيّة و`getfacl` مطابقان لملفّ `touch` مرجعيّ | ✅ |
| `.env` قائم بـ0600 مع `user:65534:r` و`mask::r`، عبر الصدفة الحقيقيّة | بايتات `system.posix_acl_access` و`getfacl` كما كانت حرفيّاً | ✅ |
| `.env` قائم 0640 بلا ACL، في مجلّد بـACL افتراضيّ `u:65534:r` | لا ACL وصول بعد الإعادة (`ENODATA`)، ولا `65534` في `getfacl`، والصلاحيّة 0640 | ✅ |
| نظام ملفّاتٍ بلا ACL | `/mnt/c` (drvfs/9p): `setxattr` يعطي `ENOTSUP` (95). `size-block` وإنشاء `.env` جديد ينجحان بالرمز 0 دون مخلَّفات (الصلاحيّات هناك مُحاكاة من النظام، فلا يُحكم بها) | ✅ |
| tmpfs بلا ACL | غير متاح هنا (tmpfs يدعم ACL)؛ لم يُختبر | — |
| الاختبارات تُتخطّى؟ | لا شيء متخطّى (`-rs` نظيف) | ✅ |

#### 2‑5. الأخطاء المكتشفة
لا أخطاء (لا `QA‑SR2‑n`).

#### 2‑6. الحكم (الجولة 2)
**PASS** — AC‑SR2.1…8 مغطّاة؛ الأحد عشر اختباراً تفشل على `7edb69b` وتنجح على `1b18fce`؛ الاستكشاف بـACL حقيقيّة مطابق للمتوقّع؛ البوّابات الستّ خضراء (6207 ناجحاً، 0 متخطّى)؛ الفرق محصور في الملفّين.

### الجولة 3 (بعد الإعادة 2: `0aa13c9` فوق `1b18fce`)

#### 3‑1. ما تغيّر في المصفوفة
| AC | الاختبار | الحالة |
|---|---|---|
| AC‑SR2.7 (ج) / AC‑SR2.8 (SR2‑5) | `test_a_refused_fchown_drops_the_access_acl_too` (ACL الهدف `g::r` لا يُنسخ حين يُرفض `fchown` بالكامل: `removexattr` بلا `setxattr`، وعند `fchmod` لا ACL ولا بتّات مجموعة، والنهائيّ بلا ACL و0600) | ✅ |
| CR‑SR2‑5 | `test_an_unknown_error_reading_the_target_acl_is_raised_not_swallowed` (`EIO` من `getxattr`: الرمز 4، `.env` كما هو، لا مؤقّت) | ✅ |
| CR‑SR2‑6 | اختبار ACL الافتراضيّ بـ`parametrize`، واختبار الواصف أُعيدت تسميته `…closed_when_fchown_removexattr_or_fchmod_raises` | ✅ |
باقي مصفوفة الجولة 2 بلا تغيير.

#### 3‑2. الأحمر ثمّ الأخضر (worktree مؤقّت، أُزيل)
- **`1b18fce` مع ملفّ الاختبار الحاليّ:** `1 failed, 178 passed`، والفاشل هو `test_a_refused_fchown_drops_the_access_acl_too`. وعلى `0aa13c9` ينجح.
- **طفرة «ابتلاع كلّ شيء»:** على `0aa13c9` عدّلت `access_acl` لتُرجع `None` عند أيّ `OSError` (حذف `if exc.errno in NO_ACL … raise`): `1 failed, 178 passed`، والفاشل `test_an_unknown_error_reading_the_target_acl_is_raised_not_swallowed`. فالاختبار يقتل الطفرة.
- **على `0aa13c9`:** `tests/unit/test_install_sh.py` ‏`179 passed`، بلا متخطّى (`-rs`).

#### 3‑3. البوّابات
```
1. ruff format --check .   -> 833 files already formatted
2. ruff check .            -> All checks passed!
3. mypy src                -> Success: no issues found in 467 source files
4. lint-imports            -> Contracts: 8 kept, 0 broken.
5. pytest -rs tests/unit tests/architecture tests/eval
                           -> 6209 passed, 7 warnings in 187.94s  (لا متخطّى)
6. test-rules.sh           -> test-rules: OK
```
`git diff master --stat`: `deploy/release/install.sh` و`tests/unit/test_install_sh.py` فقط.

#### 3‑4. الأخطاء والحكم
لا أخطاء، ولا فجوة تستدعي اختباراً جديداً.

**PASS (الجولة 3)** — الاختباران الجديدان يفشلان حيث يجب وينجحان على `0aa13c9`، والبوّابات خضراء، والفرق محصور.

</div>
