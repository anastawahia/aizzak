<div dir="rtl">

# التصميم التقني: إصلاح الملاحظات المؤجَّلة من حزمة التثبيت

لكلّ ملاحظةٍ قسمٌ مستقلّ باسمها، يُكتب حين يأتي دورها (`status.md` «ترتيب العمل»).

## SR‑2: الملفّ المؤقّت لـ`atomic_write` لا يكون أوسع من الهدف في أيّ لحظة

> **المدخل:** [`../install-bundle/security-review.md`](../install-bundle/security-review.md) (صفّ SR‑2، والتجربة 1 في §1)، و[`../install-bundle/follow-ups.md`](../install-bundle/follow-ups.md) (صفّ SR‑2). مرحلة التحليل تُخطّيت، فمعايير القبول في §2 هنا.

> **دورة الإعادة 1 (2026‑10‑21)،** بعد [`security-review.md`](security-review.md) §SR‑2 و[`review.md`](review.md) §SR‑2. الشيفرة قبلها: `10d02f6`، واختبارات QA ‏`7edb69b`.
> - **SR2‑1 (تراجع):** يُصلح بالحلّ (ب). الملفّ الجديد يُنشأ بـ`0o666` بلا `fchmod` ولا `os.umask`، فتطبّق النواة `umask` أو ACL المجلّد الافتراضيّ. أُعيدت كتابة AC‑SR2.2، وأُضيف AC‑SR2.6. السبب في §4، القرار 1.
> - **SR2‑2:** يُصلح الآن. ACL الوصول للهدف القائم يُنقل كما هو، وإن لم يكن للهدف ACL أُزيل ما ورثه المؤقّت. وذلك بعد `fchown` وقبل `fchmod` (AC‑SR2.7). وتُضاف `import errno` ودالّتان صغيرتان فوق `atomic_write`.
> - **SR2‑3:** يُصلح الآن. لغير root، إن رُفض `fchown` جُرّب `fchown(fd, -1, gid)`، وإن رُفض أيضاً حُذفت بتّات المجموعة (AC‑SR2.8). وAC‑SR2.3 لم يعد يقول «يُتجاهل».
> - **SR2‑4:** لا عمل، فهو من نطاق SR‑1 (§8).
> - **CR‑SR2‑2:** صار شرطاً في §6. **وCR‑SR2‑1:** يُؤخذ به، فـ`_watch_temp` يحفظ الواصف ويخدم اختبار إغلاقه.
> - **CR‑SR2‑3:** للمنسّق (§8).
> - **AC‑SR2.4 يشمل الآن المسارين** (الملفّ القائم بـ0o600، والجديد بـ0o666). **وAC‑SR2.5 تغيّر** نطاق الفرق فيه.
> - **لا ⚠️ جديد.** و⚠️ `deploy/vault/start.sh` باقٍ كما كان، ينتظر المالك.

> **دورة الإعادة 2 (2026‑10‑21)،** بعد «الجولة 2» في [`review.md`](review.md) (CR‑SR2‑4…6) و[`security-review.md`](security-review.md) (SR2‑5). الشيفرة قبلها: `1b18fce`.
> - **CR‑SR2‑4 / SR2‑5:** الحالة: غير root، ورُفض `fchown` بشكليه، وللهدف ACL وصول. فيه كان `setxattr` يعطي مجموعة المنشئ `group::` و`mask` الهدف حتّى `fchmod`. **الإصلاح:** ‏`acl = None` بعد `final_mode &= ~0o070`، فيُزال الموروث ولا يُنسخ ACL الهدف. والوصول الفعليّ النهائيّ لا يتغيّر: مالكٌ وحده، كما كان مع `mask` صفر. عُدِّل §4 (الشيفرة والقرار 5) وAC‑SR2.8.
> - **CR‑SR2‑5:** خطأٌ مجهول في قراءة ACL الهدف (`EIO`) يوقف الكتابة بالرمز 4. أُضيف إلى AC‑SR2.7 ومعه اختبار.
> - **CR‑SR2‑6:** تسمية: `…descriptor_is_closed_when_a_call_on_it_raises`، وصلاحيّةٌ متوقَّعة صريحة في `parametrize` لـAC‑SR2.6 بدل `len(default) == 28` (§6).

### 1. الملخّص
- **المكان:** البرنامج المضمَّن `env_tool` في `deploy/release/install.sh`، والدالّة `atomic_write` (`:462-501` على `10d02f6`). هي الكاتب الوحيد لـ`.env`، ولها أربعة نداءات:
  - `cmd_compose` عند الإنشاء (`:602`)؛
  - `adopt` (`:684`)؛
  - `cmd_size_block` (`:775`)؛
  - `cmd_set_secret_id` (`:822`).
- **و`upgrade.sh` في 5.3** سيعيد استعمالها (`../install-bundle/design.md` §3.3.9)، فيرث الإصلاح.
- **المشكلة الأصليّة:** المؤقّت كان يُنشأ بـ0644 تحت `umask 022`، ثمّ يأتي `fchmod`. والصلاحيّة تُفحص عند `open` وحده. فواصفٌ يُفتح في تلك النافذة يقرأ الأسرار بعد كتابتها، ولو صار `.env` 0600. وهذا مُثبَت.
- **الإصلاح، بعد دورة الإعادة 1:**
  1. **الملفّ القائم:**
     - يُنشأ المؤقّت بـ0600 ومعه `O_NOFOLLOW`.
     - ثمّ يأخذ مالك الهدف (`fchown`).
     - ثمّ ACL الوصول للهدف، أو لا ACL.
     - ثمّ صلاحيّته (`fchmod`).
     - وكلّ ذلك قبل أوّل بايت، فلا يفتح المؤقّتَ في أيّ لحظةٍ أحدٌ لا يفتح الهدف.
  2. **الملفّ الجديد:** يُنشأ بـ`0o666` ومعه `O_NOFOLLOW`، بلا `fchmod`. فيأخذ ما يأخذه أيّ ملفٍّ جديد في المجلّد: `umask`، أو ACL المجلّد الافتراضيّ (Q‑4). فالمؤقّت بصلاحيّته النهائيّة من أوّل لحظة.
  3. **غير root:** إن رُفض `fchown`، لا تأخذ مجموعته بتّات مجموعة الهدف أبداً. وهذا يخصّ 5.3، و`install.sh` لا يبلغه.
- **خارج النطاق:**
  - Compose ووثائق التشغيل.
  - SR‑7: حذف المخلَّفات في بداية كلّ تشغيل.
  - SR‑1 وSR2‑4: ثقة المجلّدات، والعمل بالأسماء.
  - الـxattr غير ACL الوصول (§8).
  - الكتّاب الآخرون في `deploy/` (§8).

### 2. معايير القبول

| المعرّف | بافتراض | عندما | إذن |
|---|---|---|---|
| AC‑SR2.1 | `.env` قائمٌ بصلاحيّة 0600 بلا ACL، و`umask 022` | يعيد `env_tool` كتابته بالتبنّي (`compose`)، أو بـ`set-secret-id` (سيناريو التجربة 1) | • **عند الإنشاء** (بعد `os.open` مباشرةً): المؤقّت `.install-tmp-<pid>` فيه `st_mode & 0o077 == 0`.<br>• **وعند `os.fchmod`، قبل تغيير الصلاحيّة:** ما زال `& 0o077 == 0`، وحجمه 0. والفحص بـ`os.fstat` على الواصف، وبـ`lstat` على المسار.<br>• `os.fchmod` يُستدعى مرّةً واحدة بـ0o600، **في السيناريوهين** (CR‑SR2‑2).<br>• **وعند `os.fsync`**، بعد الكتابة: ما زال `& 0o077 == 0`.<br>• `.env` النهائيّ 0600، وفيه المحتوى الجديد |
| AC‑SR2.2 | لا `.env`، ولا ACL افتراضيّ على مجلّده. و`umask` قيمته U، وهي واحدةٌ من 022 و027 و002 و077 و277 | ينشئه `env_tool compose` | • `os.open` يُستدعى بالأعلام `O_CREAT\|O_EXCL\|O_WRONLY\|O_NOFOLLOW`، وبالصلاحيّة 0o666.<br>• لا `fchown`، ولا `fchmod`، ولا نداء ACL، ولا `os.umask`.<br>• المؤقّت عند الإنشاء صلاحيّته `0o666 & ~U`، وهي صلاحيّته النهائيّة، وحجمه 0.<br>• `.env` النهائيّ صلاحيّته `0o666 & ~U`، أي 0644 و0640 و0664 و0600 و0400 بالترتيب.<br>• لا `chmod` على الهدف، ولا يبقى `.install-tmp-*` |
| AC‑SR2.3 | `.env` قائمٌ بصلاحيّة 0640 بلا ACL. وإن كان للمستخدم مجموعةٌ تكميليّة، فمجموعة الملفّ هي تلك | يُعاد كتابته | • يبقى الملفّ بصلاحيّته ومالكه ومجموعته.<br>• تسلسل النداءات على المؤقّت: `open(…, 0o600)`، ثمّ `fchown(st_uid, st_gid)`، ثمّ إزالة ACL الوصول، ثمّ `fchmod(0o640)`، ثمّ `fsync`. والحجم 0 حتّى `fchmod`.<br>• `PermissionError` من `fchown` تحت root يُرفع: الرمز 4، و`.env` لم يتغيّر بايتاً ولا صلاحيّة، ولا يبقى مؤقّت، والواصف مغلق.<br>• لغير root: انظر AC‑SR2.8 |
| AC‑SR2.4 | رابطٌ رمزيّ زُرع في مسار المؤقّت، بعد حذف المخلَّفات وقبل فتحه. يشير إلى ملفٍّ قائم، أو إلى مسارٍ غير موجود. والهدف قائم، أو جديد | يكتب `env_tool`: ‏`size-block` للقائم، و`compose` للجديد | • `os.open` استُدعي مرّةً بالأعلام `O_CREAT\|O_EXCL\|O_WRONLY\|O_NOFOLLOW`، وبالصلاحيّة 0o600 للقائم و0o666 للجديد.<br>• الكتابة تُرفض بالرمز 4 (`FileExistsError while handling .env`)، بلا `Traceback`.<br>• الملفّ الذي يشير إليه الرابط لم يتغيّر، والمسار غير الموجود لم يُنشأ.<br>• `.env` القائم لم يتغيّر بايتاً ولا صلاحيّة، والجديد لم يُنشأ |
| AC‑SR2.5 | الإصلاح على الفرع | تُشغَّل البوّابات الستّ (`CLAUDE.md` «Quality gates») | • البوّابات كلّها خضراء، واختبارات ACL **تجري ولا تُتخطّى**، على هذا المضيف وفي CI (يُظهر ذلك `-rs`).<br>• الاختبارات القائمة التي في §6 تجتاز، وما يتغيّر منها مذكورٌ هناك باسمه.<br>• توقيع `atomic_write` ونداءاته الأربعة كما هي.<br>• في `install.sh` لا يتغيّر إلّا: `import errno`، والثابتان والدالّتان فوق `atomic_write`، و`atomic_write` نفسها.<br>• `git diff master --stat` لا يسمّي غير `deploy/release/install.sh` و`tests/unit/test_install_sh.py` ووثائق `docs/delivery/` |
| AC‑SR2.6 (SR2‑1) | لا `.env`، ولمجلّده ACL افتراضيّ: (أ) `u::rwx,g::---,o::---`؛ (ب) `u::rwx,u:65534:r,g::r-x,m::r-x,o::---`. و`umask 022` | ينشئه `env_tool compose` | • صلاحيّة `.env` وACL الوصول فيه (بايتاتٍ، أو غيابه) **يساويان** ما لملفٍّ مرجعيٍّ يُنشأ في المجلّد نفسه بـ`Path.touch()` (0o666).<br>• في (أ) الصلاحيّة **0600**، لا 0644.<br>• في (ب) لا يقرؤه الآخرون، و`65534` يقرؤه كما يقرأ الملفّ المرجعيّ |
| AC‑SR2.7 (SR2‑2) | (أ) `.env` بـ0600 ومعه ACL وصول `user:65534:r` (فيُرى 0640). (ب) `.env` بـ0640 بلا ACL، ولمجلّده ACL افتراضيّ فيه `user:65534:r` | يُعاد كتابته (`size-block`) | **(أ):**<br>• ACL الوصول بعد الإعادة يساوي ما كان، بايتاً ببايت. فـ`group::---` باقٍ، و`65534` يقرأ.<br>• والصلاحيّة كما كانت.<br>• والتسلسل: `open`، ثمّ `fchown`، ثمّ `setxattr`، ثمّ `fchmod`، ثمّ `fsync`.<br>• وعند الإنشاء وقبل `setxattr`: `& 0o077 == 0` والحجم 0.<br>**(ب):**<br>• لا ACL وصول بعد الإعادة، والصلاحيّة 0640.<br>• وعند الإنشاء: ACL المؤقّت الموروث بـ`mask` قيمته 0، فلا يفتحه `65534`.<br>• وعند `fchmod`: لا ACL وصول على المؤقّت.<br>• فلا يفتح المؤقّتَ `65534` في أيّ لحظة.<br>**(ج) خطأٌ مجهول** (CR‑SR2‑5): ‏`os.getxattr` على الهدف يرفع `OSError(EIO)`. فالرمز 4 و`OSError while handling .env` بلا `Traceback`، و`.env` كما كان بايتاً وصلاحيّة، ولا مؤقّت |
| AC‑SR2.8 (SR2‑3) | غير root (euid 1000)، و`.env` بـ0640. و`fchown(fd, uid, gid)` الكامل مرفوض | يُعاد كتابته | • يُجرَّب `fchown(fd, -1, gid)` بعده. **وإن نجح:** المجموعة مجموعة الهدف، والصلاحيّة 0640.<br>• **وإن رُفض أيضاً:** الصلاحيّة `0o640 & ~0o070` = **0600**. فلا تأخذ مجموعة المنشئ بتّات مجموعة الهدف.<br>• **وإن رُفض الاثنان وللهدف ACL وصول** (`u::rw-,u:65534:r--,g::r--,m::r--,o::---`، CR‑SR2‑4 / SR2‑5): لا `setxattr`، بل `removexattr`. وعند `fchmod` لا ACL على المؤقّت، و`& 0o077 == 0`. والنهائيّ 0600 بلا ACL وصول.<br>• الرمز 0 في كلّ الحالات، ولا مؤقّت باقٍ |

### 3. الملفّات

| المسار | إنشاء/تعديل | المسؤوليّة | المنفّذ |
|---|---|---|---|
| `deploy/release/install.sh` | تعديل | • `import errno` في رأس البرنامج المضمَّن.<br>• `ACCESS_ACL` و`NO_ACL` و`access_acl` و`set_access_acl` فوق `atomic_write` مباشرةً.<br>• و`atomic_write` بالشكل الذي في §4.<br>ولا يتغيّر سطرٌ آخر | backend-developer |
| `tests/unit/test_install_sh.py` | تعديل | قسم `# ── SR-2: the temp file of atomic_write ──`: ما يُعاد كتابته وما يُضاف في §6 | backend-developer، ويضيف qa-engineer ويتحقّق |
| `tests/unit/support_install.py` | لا تغيير | `env_tool` (في العمليّة نفسها) و`run_function` (صدفةٌ حقيقيّة) يكفيان | — |
| `docs/delivery/install-bundle/follow-ups.md` (صفّ SR‑2، وسطر SR‑1، و`SR‑13` المقترح)، و`status.md` | تعديل | تعليم SR‑2 «✅ أُصلح» مع الـcommit، وما في §8 | **المنسّق**، لا المطوّر |

### 4. العقود

#### الواجهات البرمجيّة
- لا واجهة برمجيّة، ولا تغيير في `openapi.yaml`.
- ولا رمز خروجٍ جديد: كلّ فشلٍ هنا `OSError`، فيبقى الرمز 4 من `main`.

#### التواقيع
التوقيع كما هو: `def atomic_write(path, text)`، والنداءات الأربعة لا تتغيّر. والشكل المطلوب:

```python
# the program's imports gain one line, in order:  import errno

ACCESS_ACL = "system.posix_acl_access"
NO_ACL = (errno.ENODATA, errno.ENOTSUP)  # none set / the file system has no ACLs (= EOPNOTSUPP)


def access_acl(path):
    """The POSIX access ACL of `path` as raw xattr bytes, or None when it has none (SR2-2)."""
    try:
        return os.getxattr(path, ACCESS_ACL)
    except OSError as exc:
        if exc.errno in NO_ACL:
            return None
        raise


def set_access_acl(fd, acl):
    """Give the open file exactly `acl`, or no access ACL when it is None: it may have inherited
    one from the directory's default ACL (SR2-2)."""
    if acl is not None:
        os.setxattr(fd, ACCESS_ACL, acl)
        return
    try:
        os.removexattr(fd, ACCESS_ACL)
    except OSError as exc:
        if exc.errno not in NO_ACL:
            raise


def atomic_write(path, text):
    """Temp file next to the target, fsync, rename. Temp files a killed run left behind are
    removed first, and chmod is never run on the target.
    A new file is created 0666, so the kernel applies the umask or the directory's default ACL,
    as for any new file (Q-4): the temp file has its final permissions from the start.
    An existing target's secrets are never in a file more open than it (SR-2): the temp file is
    created owner-only (0600, O_EXCL | O_NOFOLLOW), then gets the target's owner, then its access
    ACL (or none), then its mode, all before the first byte. A writer that is not root and cannot
    give the owner keeps the target's group if it can, and otherwise drops the group bits and the
    target's ACL."""
    target = Path(os.path.realpath(path))
    for stale in target.parent.glob(".install-tmp-*"):
        stale.unlink(missing_ok=True)
    tmp = target.with_name(f".install-tmp-{os.getpid()}")
    existing = os.stat(target) if target.exists() else None
    acl = None if existing is None else access_acl(target)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW
    fd = os.open(tmp, flags, 0o666 if existing is None else 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            if existing is not None:
                out = handle.fileno()
                final_mode = stat.S_IMODE(existing.st_mode)
                try:  # owner first: until the ACL and mode below, only the target's owner opens it
                    os.fchown(out, existing.st_uid, existing.st_gid)
                except PermissionError:
                    if os.geteuid() == 0:
                        raise
                    try:  # SR2-3: keep the group at least, as deploy/auto_size.py does
                        os.fchown(out, -1, existing.st_gid)
                    except PermissionError:
                        final_mode &= ~0o070  # the creator's group never gets the target's bits
                        acl = None  # SR2-5: a copy would give that group group:: and the mask
                set_access_acl(out, acl)  # the ACL before the mode: fchmod sets the ACL mask
                os.fchmod(out, final_mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        dfd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
```

**قرارات الشكل:**
1. **الملفّ الجديد بـ0o666 بلا `fchmod`. وهو الحلّ (ب) لـSR2‑1، لا (أ).**
   - **لا نافذة SR‑2 في الملفّ الجديد:**
     - لا ملفّ أقدم وأضيق تتسرّب أسراره.
     - والنواة تعطي المؤقّت عند `open` صلاحيّته النهائيّة، و`os.replace` يُبقي الـinode نفسه.
     - فمن يفتح المؤقّت يفتح `.env` النهائيّ أيضاً، وهذا قرار Q‑4.
   - **وهو أصحّ من (أ):**
     - (أ) يترك المؤقّت على 0600 حين يكون للمجلّد ACL افتراضيّ. فيُخفي الـ`mask` الصفريّ ما يعطيه الـACL لمستخدمٍ مسمّى، كما في AC‑SR2.6 (ب).
     - فيخرج الملفّ أضيق من صلاحيّة الإنشاء الافتراضيّة، ويبقى مخالفاً لـQ‑4، لكن في الاتّجاه الآخر.
     - أمّا (ب) فيطابق أيّ ملفٍّ جديدٍ في المجلّد، وهذا ما يقوله Q‑4 حرفيّاً.
   - **وهو أبسط:**
     - يزول نداءا `os.umask`، وحساب `0o666 & ~umask`، و`fchmod` الجديد.
     - ولا فحص `listxattr` على المجلّد.
   - **الثمن:** المؤقّت الجديد 0644 وهو فارغ تحت 022، وكان 0600 في `10d02f6`. وهي صلاحيّة النهائيّ نفسها، فلا انكشاف زائد. والمراجِع الأمنيّ يقول هذا في بديله.
   - **و`umask` يمسّ بتّات المالك** (277 مثلاً): ينشأ 0400، والواصف يبقى قابلاً للكتابة. وهذا مقيسٌ من قبل.
2. **الملفّ القائم بـ0o600.**
   - `umask` يضيّق فقط.
   - وإن كان للمجلّد ACL افتراضيّ، ورثه المؤقّت ومعه `mask::---`. لأنّ بتّات المجموعة في 0600 صفر، فلا يفتحه مستخدمٌ مسمّى ولا مجموعة.
   - مقيسٌ على المضيف (ext4، kernel 6.6) بـ`os.setxattr` على مجلّدٍ في الـscratchpad.
3. **`fchown` أوّلاً.**
   - بعده، والمؤقّت بـ0600 (وACL موروث بـ`mask` صفر إن وُجد)، لا يفتحه إلّا مالك الهدف.
   - وACL الهدف يُفهم بالنسبة إلى مالكه (`user::`)، فالمالك يُضبط قبله.
   - ولينكس يمسح `S_ISUID`/`S_ISGID` عند `chown`، فيأتي `fchmod` في الآخر.
4. **ACL الوصول قبل `fchmod`** (SR2‑2).
   - `fchmod` يضبط `mask` الـACL من بتّات المجموعة. فلو جاء أوّلاً على ACL موروث، أعطى المستخدمين المسمَّين حقّاً قبل إزالة الـACL. وهذا ما قاسه المراجِع في (ج).
   - بعد `setxattr` يساوي المؤقّت الهدف بالضبط: الـACL والصلاحيّة، لأنّ بتّات مجموعة `S_IMODE` هي `mask` الهدف. ثمّ `fchmod(final_mode)` لا يغيّر شيئاً منهما، ويعيد `S_ISUID`/`S_ISGID`.
   - وإن لم يكن للهدف ACL، أُزيل الموروث، فبقي المؤقّت 0600 بلا ACL حتّى `fchmod`.
   - **قيس على المضيف:** `setxattr` على الواصف بـACL الهدف، ثمّ `fchmod`، فساوت البايتات بايتات الهدف. و`removexattr` بلا ACL يعطي `ENODATA`، فيُتجاهل.
5. **غير root ورُفض `fchown`** (SR2‑3).
   - يُجرَّب `fchown(fd, -1, gid)`، وهو نمط `deploy/auto_size.py:659`.
   - وإن رُفض أيضاً: `final_mode & ~0o070`، و`acl = None` (دورة الإعادة 2).
     - المؤقّت ما زال لمجموعة المنشئ. فلو نُسخ ACL الهدف، لأعطاها `group::` و`mask` الهدف ما بين `setxattr` و`fchmod` (CR‑SR2‑4 / SR2‑5).
     - فيُزال الموروث بدل ذلك. والوصول الفعليّ النهائيّ هو نفسه، مالكٌ وحده: مع ACL الهدف كان `mask` سيصير صفراً.
   - فالنتيجة قد تكون أضيق من الهدف، ولا تكون أوسع أبداً.
   - وتحت root لا يتغيّر شيء: يُرفع الخطأ.
6. **الأخطاء المجهولة تُغلق ولا تُتجاهل.**
   - المتجاهَل وحده `ENODATA` (لا ACL)، و`ENOTSUP`، وهو نفسه `EOPNOTSUPP` على لينكس (نظام ملفّاتٍ بلا ACL).
   - وكلّ خطأٍ آخر في `getxattr` أو `removexattr`، وأيّ خطأٍ في `setxattr`، يعطي الرمز 4، و`.env` كما هو.
   - و`setxattr` لا يُتجاهل فيه شيء: للهدف ACL على نظام الملفّات نفسه. وإسقاط الـACL بصمتٍ قد يوسّع، كما في SR2‑2 (أ): `mask` يصير بتّات المجموعة.
7. **`O_NOFOLLOW` في المسارين.** `O_CREAT|O_EXCL` يرفض الرابط وحده (`EEXIST`)، و`O_NOFOLLOW` دفاعٌ ثانٍ (`ELOOP`).
8. **كلّ ما بعد `os.open` داخل `with`:** أيّ استثناءٍ يُغلق الواصف. ولا يُكتب بايتٌ قبل `fchmod`: ‏`fdopen` لا يكتب شيئاً، والمخزن فارغ.
9. **ما يبقى كما هو:**
   - حذف `.install-tmp-*` أوّلاً، و`realpath`.
   - `fsync` للملفّ وللمجلّد، و`os.replace`.
   - حذف المؤقّت عند أيّ استثناء.
   - ولا `chmod` على الهدف (Q‑4، FR‑11).
10. **دوالّ `*xattr` لينكسيّة.** وكذلك `install.sh` و`/proc` في الاختبارات، فلا أثر لذلك.

### 5. البيانات
لا تغيير: لا جداول، ولا ترحيل، ولا RLS.

### 6. خطة الاختبار

**المبادئ:**
- كلّ الاختبارات وحدات، في `tests/unit/test_install_sh.py`، في قسم SR‑2.
- التجسّس يجري على `env_tool` داخل العمليّة نفسها، و`umask` فيه 022. و`umask` الحقيقيّ عبر `run_function`.

**الأدوات المساعدة** (CR‑SR2‑1):
- **`_watch_temp`:**
  - يعيد سجلّاتٍ من نوع `TempCall(name, args, perm, size, acl, fd)` (`NamedTuple`).
  - `acl` هو ACL الوصول على المؤقّت لحظة التسجيل: بايتات، أو `None`.
  - `fd` يستعمله اختبار إغلاق الواصف، بدل غلافٍ ثالث لـ`os.open`.
  - ويتجسّس أيضاً على `os.setxattr` و`os.removexattr`، حين يكون الوسيط الأوّل واصفَ المؤقّت. ويسجّلهما بـ`args = ()`، قبل النداء الحقيقيّ.
  - والاختبارات القائمة تنتقل إلى الحقول بالاسم.
  - و`plant` في اختبار الرابط يبقى كما هو.
  - و`through_fsync` في `_assert_owner_only_throughout` يُحذف، فلم يعد أحدٌ يستعمله.
- **أدوات ACL:**
  - `_acl(*entries) -> bytes`: ACL بصيغة النواة، أي `struct.pack("<I", 2)` ثمّ `"<HHI"` (tag، perm، id) لكلّ مدخل، مرتّبةً بالـtag ثمّ بالـid.
  - الثوابت: `USER_OBJ=0x01` و`USER=0x02` و`GROUP_OBJ=0x04` و`MASK=0x10` و`OTHER=0x20`، و`id = 0xFFFFFFFF` لغير المسمّى.
  - `_acl_mask(raw) -> int`.
  - `_set_acl(path, name, raw)`: تستدعي `pytest.skip("no POSIX ACLs on this file system")` عند `ENOTSUP`.
- **لماذا `os.setxattr` لا `setfacl`:** لا تحتاج الاختبارات حزمة `acl` أصلاً، فلا تُتخطّى لغيابها. وما يجعلها تُتخطّى نظامُ ملفّاتٍ بلا ACL وحده.

**دعم الـACL:**
- **هذا المضيف:** `/usr/bin/setfacl` موجود. و`/tmp`، وفيه `tmp_path`، على ext4 بـACL، وقد قيس بـ`os.setxattr`.
- **CI:** `quality` على `ubuntu-latest` بلا حاوية (`.github/workflows/ci.yml:22`). و`/tmp` على قرص الجذر ext4، وext4 يركَّب بـACL افتراضيّاً.
- **و`acl` على صورة GitHub:** على حدّ علمي هي في قائمة حزم apt المثبّتة، **ولم أتحقّق منها من هنا**. ولا يهمّ ذلك، لأنّ الاختبارات لا تستدعي `setfacl`.
- **إن تُخطّيت في CI** فـ`-rs` يُظهر ذلك، وهو يخالف AC‑SR2.5.

| AC | الاختبار | جديد/قائم | ما يؤكّده |
|---|---|---|---|
| AC‑SR2.1 | `test_a_0600_env_is_never_rewritten_through_a_more_open_temp_file` | قائم، تتغيّر الأداة المساعدة وحدها | كما هو: التبنّي على 0600، و`fchmod` مرّةً بـ0o600. وفي التسلسل الآن `removexattr` قبل `fchmod` |
| AC‑SR2.1 | `test_set_secret_id_on_a_0600_env_never_opens_a_wider_temp_file` | **قائم، يُعدَّل** (CR‑SR2‑2) | يُضاف: `[c.args for c in calls if c.name == "fchmod"] == [(0o600,)]` |
| AC‑SR2.2 | `test_a_new_env_temp_file_is_born_with_its_final_mode` | **يحلّ محلّ** `…new_env_temp_file_is_owner_only_until_it_gets_the_umask_mode` | • `run_compose` على مسارٍ جديد.<br>• السجلّ `[("open", (flags, 0o666))]` وحده، بلا `fchown` ولا `fchmod` ولا `setxattr` ولا `removexattr`.<br>• `perm == 0o644` والحجم 0 عند الإنشاء.<br>• `os.umask` لم يُستدعَ: تجسّسٌ عليه يسجّل نداءات البرنامج وحدها، أي ما بين نداءي الأداة المساعدة.<br>• النهائيّ 0o644 |
| AC‑SR2.2 | `test_a_new_env_takes_0666_minus_the_umask[022,027,002,077,277]` | قائم | بلا تغيير |
| AC‑SR2.2 | `test_compose_created_writes_the_template_with_the_changes_and_an_install_block`، و`test_env_is_0644_under_umask_022_and_nothing_else_secret_appears`، و`test_the_key_is_minted_inside_vault_and_written_once` | قائمة | 0644 بعد الإنشاء، وبعد التشغيل الكامل، وبعد سكّ المفتاح |
| AC‑SR2.3 | `test_an_existing_env_gets_its_owner_then_its_mode_before_the_first_byte` | **قائم، يُعدَّل** | التسلسل المتوقَّع: `open`، ثمّ `fchown`، ثمّ `("removexattr", ())`، ثمّ `fchmod(0o640)`، ثمّ `fsync`. والحجم 0 في الأربعة الأولى |
| AC‑SR2.3 | `test_an_existing_env_keeps_its_group_through_a_rewrite` | قائم | بلا تغيير |
| AC‑SR2.3 | `test_a_refused_fchown_drops_the_group_bits_unless_root[1000,0]` | **يحلّ محلّ** `test_a_refused_fchown_is_ignored_unless_root` | • كلّ `fchown` مرفوض.<br>• عند 1000: الرمز 0، و**0o600** (AC‑SR2.8).<br>• عند 0: الرمز 4 و`PermissionError while handling .env`، و`sha` و`mode` كما كانا، ولا مؤقّت |
| AC‑SR2.3 | `test_the_temp_file_descriptor_is_closed_when_fchown_removexattr_or_fchmod_raises[fchown,fchmod,removexattr]` | **قائم، يُعدَّل، ويُعاد تسميته** (CR‑SR2‑6) | • كان `…_when_fchown_or_fchmod_raises`.<br>• يُضاف `removexattr`: يُرفض بـ`EPERM`، فيُرفع.<br>• والواصف يُؤخذ من `_watch_temp(…).fd` (CR‑SR2‑1) |
| AC‑SR2.3 | `test_compose_adopts_a_hand_made_env_replacing_only_the_template_values` (0640)، و`test_set_secret_id_inserts_two_lines_in_the_block_and_keeps_the_mode` (0640)، و`test_a_repeat_keeps_every_secret_the_edit_the_bytes_and_the_mode` (0600، تشغيلٌ كامل) | قائمة | الصلاحيّة القائمة تبقى |
| AC‑SR2.4 | `test_a_link_planted_at_the_temp_path_is_refused_and_nothing_is_written_through_it[dangling × new]` | **قائم، يُعدَّل** | • يُضاف `parametrize` على `new`: False (`size-block`، والصلاحيّة 0o600) وTrue (`compose` على مسارٍ جديد، والصلاحيّة 0o666).<br>• وفي الجديد: `.env` لم يُنشأ |
| AC‑SR2.5 | `test_a_stale_temp_file_from_a_killed_run_is_removed_by_the_next_write`، و`test_a_failed_write_leaves_env_and_no_temp_file`، و`test_compose_unchanged_writes_nothing_not_even_the_mtime`، و`test_no_secret_is_printed_or_passed_on_a_failure_path`، و`test_a_symlinked_env_is_rewritten_in_place_and_stays_a_link` | قائمة | السلوك المجاور لم يتغيّر |
| AC‑SR2.5 | البوّابات الستّ | — | • `ruff format --check .`، و`ruff check .`، و`mypy src`، و`lint-imports`.<br>• `pytest -rs`، وعلى هذا المضيف `tests/unit tests/architecture tests/eval` وحدها. والكامل في CI.<br>• `deploy/prometheus/test-rules.sh`.<br>• ولا `SKIPPED` لاختبارات ACL |
| AC‑SR2.6 | `test_a_new_env_gets_what_the_directory_default_acl_gives[owner-only,named-user]` | جديد | • `_set_acl(tmp_path, "system.posix_acl_default", …)` بالـACL (أ) أو (ب)، ثمّ `run_compose` على `tmp_path / "new.env"`.<br>• ثمّ `(tmp_path / "ref").touch()`.<br>• `mode` متساويان، وACL الوصول متساويان: بايتات، أو `ENODATA` في الاثنين.<br>• الصلاحيّة المتوقَّعة صريحةٌ في `parametrize` (CR‑SR2‑6): ‏`0o600` لـ`owner-only`، و`None` (لا تُفحص إلّا بالمساواة) لـ`named-user`. ولا `len(default) == 28` |
| AC‑SR2.7 | `test_an_existing_env_keeps_its_access_acl` | جديد | • الـfixture `installed`، ثمّ `chmod 600`، ثمّ `_set_acl(path, ACCESS, u::rw,u:65534:r,g::---,m::r,o::---)`، ثمّ `size-block` مع `_watch_temp`.<br>• بايتات ACL الوصول بعده = قبله، و`mode` كما كان.<br>• التسلسل: `open`، `fchown`، `setxattr`، `fchmod`، `fsync`.<br>• عند `open` و`setxattr`: `perm & 0o077 == 0` والحجم 0.<br>• وعند `fchmod`: `acl` = بايتات الهدف |
| AC‑SR2.7 | `test_an_existing_env_does_not_take_the_directory_default_acl` | جديد | • الـfixture `installed`، ثمّ `chmod 640`، ثمّ ACL افتراضيّ على `path.parent` فيه `u:65534:r` و`m::r-x`، ثمّ `size-block` مع `_watch_temp`.<br>• عند `open`: `acl` موجود و`_acl_mask(acl) == 0`.<br>• عند `fchmod`: `acl is None`.<br>• بعده: `os.getxattr(path, ACCESS)` يرفع `ENODATA`، و`mode == 0o640` |
| AC‑SR2.7 (ج) | `test_an_unknown_error_reading_the_target_acl_is_raised_not_swallowed` | جديد (CR‑SR2‑5) | • الـfixture `installed`، و`os.getxattr` مزيَّف: يرفع `OSError(errno.EIO, …)` على الهدف، ويمرّر غيره إلى الحقيقيّ.<br>• ثمّ `size-block`.<br>• الرمز 4 و`OSError while handling .env`، بلا `Traceback`.<br>• `sha` و`mode` كما كانا، ولا `.install-tmp-*` |
| AC‑SR2.8 | `test_a_non_root_writer_keeps_the_group_when_only_the_owner_is_refused` | جديد | • `os.geteuid` = 1000، و`os.fchown` مزيَّف: يرفض حين `uid != -1`، ويمرّر غير ذلك إلى الحقيقيّ ويسجّل.<br>• النداءات: `[(uid, gid), (-1, gid)]`.<br>• الرمز 0، و`mode == 0o640`، و`st_gid` كما كان |
| AC‑SR2.8 | `test_a_refused_fchown_drops_the_group_bits_unless_root[1000]` | (أعلاه) | كلّ `fchown` مرفوض، فالنتيجة 0o600 |
| AC‑SR2.8 | `test_a_refused_fchown_drops_the_access_acl_too` | جديد (CR‑SR2‑4 / SR2‑5) | • `.env` بـACL الوصول `u::rw-,u:65534:r--,g::r--,m::r--,o::---`، و`os.geteuid` = 1000، و`os.fchown` مرفوضٌ دائماً.<br>• ثمّ `size-block` مع `_watch_temp`.<br>• التسلسل: `open`، ثمّ `removexattr`، ثمّ `fchmod(0o600)`، ثمّ `fsync`. ولا `setxattr`. (`fchown` المزيَّف لا يمرّ بالجاسوس.)<br>• عند `fchmod`: `acl is None`، و`perm & 0o077 == 0`، والحجم 0.<br>• النهائيّ 0o600، و`os.getxattr(path, ACCESS)` يرفع `ENODATA`، والرمز 0 |

**أحمر قبل الإصلاح:** على `7edb69b`، أي الشيفرة الحاليّة. يُري المطوّر أنّ هذه الاختبارات تفشل، ثمّ تنجح بعد الإصلاح، وqa-engineer يتحقّق.

| الاختبار | لماذا يفشل اليوم |
|---|---|
| `…born_with_its_final_mode` | `open` بـ0o600، ثمّ `fchmod` |
| `…default_acl_gives[owner-only]` | 0644 لا 0600 (SR2‑1) |
| `…default_acl_gives[named-user]` | `fchmod(0o644)` يعطي `other::r` و`mask::r` |
| `…keeps_its_access_acl` | الـACL يسقط (SR2‑2 أ) |
| `…does_not_take_the_directory_default_acl` | `65534` يقرأ بعد `fchmod`، وفي `fchmod` ما زال ACL |
| `…gets_its_owner_then_its_mode…` | لا `removexattr` |
| `…drops_the_group_bits_unless_root[1000]` | 0640 |
| `…keeps_the_group_when_only_the_owner_is_refused` | لا نداء `(-1, gid)` |
| `…link_planted…[*-new]` | `open` الجديد بـ0o600 |
| `…descriptor_is_closed…[removexattr]` | لا نداء، فالرمز 0 |

أمّا AC‑SR2.1 و`…takes_0666_minus_the_umask` و`…keeps_its_group…` وبقيّة القائمة فتنجح قبل الإصلاح وبعده.

**دورة الإعادة 2، على `1b18fce`:**
- `…refused_twice_never_copies_the_target_acl` يفشل اليوم، لأنّ `setxattr` يُستدعى وACL الهدف على المؤقّت عند `fchmod`.
- و`…unknown_error_reading_the_target_acl_stops_the_write` حارسٌ ينجح اليوم. لكنّه يفشل على طفرة المراجِع: ‏`access_acl` يعيد `None` لكلّ `OSError`. فيُريه المطوّر بتلك الطفرة.

### 7. ترتيب التنفيذ والتوازي
1. **backend-developer:** تعديلات الاختبارات والاختبارات الجديدة أوّلاً، ويُري الأحمر الذي في §6 على `7edb69b`.
2. **backend-developer:** `install.sh` بالشكل الذي في §4. ثمّ `pytest tests/unit/test_install_sh.py -rs` (اختبارات ACL تجري ولا تُتخطّى)، ثمّ البوّابات الستّ، ثمّ commit واحد فوق `7edb69b`. ولا يُعاد كتابة التاريخ.
3. **qa-engineer:** تحديث `test-plan.md` §SR‑2، والتحقّق من الأحمر ثمّ الأخضر. ومعه استكشاف ACL بـ`setfacl` الحقيقيّ: (أ) و(ب) و(ج) من تجربة المراجِع 6.
4. **code-reviewer ‖ security-reviewer:** على فرق دورة الإعادة (`7edb69b..`)، بالتوازي. وتُعاد تجربة المراجِع 6.
5. **product-owner:** القبول على AC‑SR2.1…AC‑SR2.8.
6. **المنسّق:** ما في §8، وصفّ SR‑2 في `follow-ups.md`، و`status.md`. **وليس هذا عمل المطوّر.**

ولا توازي في البناء: الملفّان لمنفّذٍ واحد.

### 8. المخاطر والقرارات

**قرارات دورة الإعادة 1، وكلّها بلا ⚠️:**

| البند | القرار | لماذا |
|---|---|---|
| SR2‑1 | يُصلح الآن بالحلّ (ب) | §4، القرار 1 |
| SR2‑2 | يُصلح الآن | • NFR‑3 ملزِم: «لا يغيّر صلاحيّةً ضبطها المالك»، والـACL صلاحيّة.<br>• الإصلاح في الدالّة نفسها التي نعيد كتابتها: نحو 20 سطراً بالمكتبة القياسيّة.<br>• `auto_size.stage`، الكاتب الآخر لـ`.env`، ينقل الـACL أصلاً، فيتّفق الكاتبان.<br>• و5.3 يرث دالّةً صحيحة |
| SR2‑3 | يُصلح الآن | • خمسة أسطر في الكتلة نفسها، ونمطها في `auto_size.py:659`.<br>• `install.sh` لا يبلغه (root إلزاميّ)، لكنّ `upgrade.sh` في 5.3 قد يبلغه.<br>• فتصير قاعدة «لا يفتحه من لا يفتح الهدف» صحيحةً في كلّ الفروع، بلا شرطٍ على من يستدعي |
| SR2‑4 | لا عمل في SR‑2 | من نطاق SR‑1، وهو التالي في الترتيب. انظر السطر المقترح أدناه |
| CR‑SR2‑1 | يُؤخذ به | §6 |
| CR‑SR2‑2 | يُؤخذ به | §6 |
| CR‑SR2‑3 | للمنسّق | انظر أدناه |

**المخاطر:**
- **نظام ملفّاتٍ يعطي خطأً غير `ENODATA` و`ENOTSUP` في قراءة الـACL أو إزالته:** يتوقّف `env_tool` بالرمز 4، و`.env` كما هو، ولا يُسقط الـACL بصمت.
  - NFS بلا ACL وFUSE وoverlayfs تعطي `EOPNOTSUPP` (`= ENOTSUP`)، أو تمرّر ما تحتها.
  - لم يُجرَّب غير ext4 هنا.
- **الأداء:** نداء `getxattr` واحد، ونداء `setxattr` أو `removexattr` واحد، لكلّ إعادة كتابة. لا أثر يُذكر.
- **لا تبعيّة جديدة.**

**ما يسجّله المنسّق** في `follow-ups.md`، أو غيره كما يُذكر:
- **SR2‑4 (بلا معرّفٍ جديد)**، سطرٌ يُضاف إلى صفّ SR‑1: «ويشمل SR2‑4: `atomic_write` يعمل بالأسماء (`os.stat` و`os.replace` وحذف `.install-tmp-*`)، فيبقى `require_trusted` على `ROOT` و`.env` قبله، في `install.sh` و`upgrade.sh`».
- **`SR‑13` (مقترح، معلومة):** «`atomic_write` لا ينقل من xattr الهدف غير ACL الوصول، فتسقط تسمية SELinux و`user.*` مع `os.replace`. ولم يُجرَّب على مضيفٍ بـSELinux».
- **CR‑SR2‑3:** سطر إحالةٍ تحت `../install-bundle/design.md:361-363`: «استُبدل بـSR‑2، انظر `../install-bundle-follow-ups/design.md` §SR‑2».

**⚠️ (من التصميم الأوّل، لم يتغيّر، ينتظر المالك): `deploy/vault/start.sh:138-139` يكتب أسرار Vault بالنافذة نفسها، وهو خارج ما وافق عليه المالك.**

هذا القرار لا يحجب SR‑2.
- **ما وُجد:** عند أوّل تهيئة: `printf '%s' "$INIT_OUT" > "$INIT_FILE"`، ثمّ `chmod 600`. فمفتاح الفكّ والتوكن الجذر يُكتبان في ملفٍّ بصلاحيّة `umask`، ثمّ يضيَّق.
- **من يبلغه:**
  - **Compose:** السكربت root داخل حاوية `vault`، فعمليّاً لا أحد.
  - **RunPod:** المستخدم `aizzak` (`supervisord.conf:179-181`)، فيبلغه مستخدمٌ آخر في الـPod لحظة أوّل تهيئة.
- **الخيارات:**
  - **(أ)** يُضمّ إلى SR‑2: سطر `(umask 077; printf …)` بنمط `deploy/runpod/bootstrap.sh:90-94`، واختبارٌ نصّيّ في `tests/unit/test_runpod_vault_wiring.py`.
  - **(ب)** ملاحظةٌ جديدة `SR‑12` بدورتها، وتُسجَّل في `docs/deploy-runpod.md` §8.4.
  - **(ج)** لا عمل.
- **التوصية: (ب).** وهو مسجَّلٌ في `status.md` «قرارات وتصعيدات».

**ملاحظات، لا عمل في SR‑2:** كتّاب `deploy/` الآخرون ليس فيهم نافذة SR‑2:

| الملفّ | ما يفعله | لماذا لا نافذة |
|---|---|---|
| `deploy/auto_size.py:644-670` (`stage`، يكتب `.env`) | `mkstemp` ينشئ بـ0600 مع `O_EXCL\|O_NOFOLLOW`، ويكتب، ثمّ `copystat` (ومعه الـACL) و`chown` و`chmod` | المؤقّت لا يتجاوز صلاحيّة الهدف |
| `deploy/host-tuning.sh:149-150` | `> "$tmp"` تحت `umask`، ثمّ `chmod 0644` | لا سرّ فيه، و0644 بالتصميم. واسمه المتوقَّع يغطّيه SR‑4 |
| `deploy/runpod/bootstrap.sh:90-97` | `(umask 077; printf … > tmp)` | ينشأ 0600 |
| `deploy/nginx/gen-cert.sh:76-97` | `mktemp` | ينشأ 0600 |

</div>
