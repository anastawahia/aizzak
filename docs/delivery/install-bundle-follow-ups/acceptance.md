<div dir="rtl">

# القبول: إصلاح الملاحظات المؤجَّلة من حزمة التثبيت

- **الميزة:** `install-bundle-follow-ups`، والفرع `fix/install-bundle-follow-ups`.
- **المراجِع:** product-owner.
- **لكلّ ملاحظةٍ قسمٌ باسمها** (`status.md` «طريقة العمل»). والقسم الحاليّ: SR‑2.

## SR‑2

- **التاريخ:** 2026‑10‑21.
- **النطاق:** `master` ‏`4b44f54..0aa13c9`، أي `10d02f6` (الإصلاح)، و`7edb69b` (اختبارات QA)، و`1b18fce` (الإعادة 1، ACL)، و`0aa13c9` (الإعادة 2، `acl = None`).
- **ما قرأتُ:**
  - `status.md`: جدول المراحل و«دورات الإعادة».
  - `design.md` §SR‑2: المعايير في §2، والإعادتان، و§4، و§6، و§8.
  - `test-plan.md` و`review.md` و`security-review.md` §SR‑2، في الجولات الثلاث.
  - صفّ SR‑2 في `../install-bundle/follow-ups.md`، والملاحظة SR‑2 في `../install-bundle/security-review.md`، وQ‑4 وNFR‑3 وFR‑11 في `../install-bundle/requirements.md`.
- **ما تحقّقتُ منه بنفسي** على `0aa13c9`:
  1. شغّلتُ `.venv/bin/pytest tests/unit/test_install_sh.py -rs`: **179 ناجحاً، و0 متخطّى**. ثمّ شغّلتُ قسم SR‑2 وجيرانه بـ`-rA`: 28 ناجحاً، وكلّ اسمٍ في الجدول أدناه ظهر فيها `PASSED`.
  2. `ruff format --check` و`ruff check` على `deploy/release` وملفّ الاختبار: سليمان.
  3. `git diff master --stat`: ملفّان فقط، `deploy/release/install.sh` و`tests/unit/test_install_sh.py`، بزيادة 528 وحذف 22. ووثائق `docs/delivery/` لم تُلتزَم بعد.
  4. قرأتُ فرق `install.sh` كلّه. فيه قطعتان: `import errno`، ثمّ `ACCESS_ACL` و`NO_ACL` و`access_acl` و`set_access_acl` و`atomic_write`. فالتوقيع والنداءات الأربعة لا يمسّها الفرق.
  5. `grep chmod deploy/release/install.sh` لا يجد غير `os.fchmod` على المؤقّت في فرع الملفّ القائم (`:524`)، وسطرَي التذكير في التقرير (`:1426`، `:1428`).
  6. **مسبارٌ لـAC‑SR2.8** (تفصيله في صفّه): `/tmp/claude-1000/-home-AIZZAK/cb0143f4-fafe-4724-ab31-57aa78dbf17e/scratchpad/po_probe/test_po_sr2_8.py`، يعيد استعمال أدوات ملفّ الاختبار. شغّلتُه بـ`PYTHONPATH=/home/AIZZAK .venv/bin/pytest <المسبار> --rootdir=/home/AIZZAK`: نجح (1). ولا يمسّ المستودع.
- **ولم أشغّل** `tests/integration` ولا `pytest` بلا مسار، فالمكدّس الحيّ مستعمَل.
- **الأولويّة:** التصميم لا يصنّف المعايير، فعاملتُ الثمانية كلّها Must.

### المعايير

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| AC‑SR2.1 | • `test_a_0600_env_is_never_rewritten_through_a_more_open_temp_file` (التبنّي بـ`compose`)، و`test_set_secret_id_on_a_0600_env_never_opens_a_wider_temp_file` (سيناريو التجربة 1).<br>• كلاهما يمرّ بـ`_assert_owner_only_throughout`: ‏`& 0o077 == 0` عند كلّ نداءٍ على المؤقّت (`open` و`fchown` و`removexattr` و`fchmod` و`fsync`)، والحجم 0 حتّى `fchmod`. والجاسوس يقارن `lstat` المسار بـ`fstat` الواصف عند `fchmod`.<br>• `fchmod` مرّةً واحدة بـ0o600 في الاختبارين (`:1906`، `:2059`، وهو CR‑SR2‑2).<br>• النهائيّ 0600، و`VAULT_SECRET_ID` في الملفّ. ومحتوى التبنّي يثبته `test_compose_adopts_a_hand_made_env_replacing_only_the_template_values`.<br>• مقيسٌ بخطّافٍ على كلّ خطوة: `security-review.md` §1 التجربة 2، ور2‑1 #1 | ✅ مقبول |
| AC‑SR2.2 | • `test_a_new_env_temp_file_is_born_with_its_final_mode`: السجلّ `[("open", (O_CREAT\|O_EXCL\|O_WRONLY\|O_NOFOLLOW, 0o666))]` وحده، فلا `fchown` ولا `fchmod` ولا نداء ACL. والمؤقّت `0o644` وحجمه 0 عند الإنشاء، و`os.umask` لم يستدعه البرنامج، والنهائيّ 0o644.<br>• `test_a_new_env_takes_0666_minus_the_umask[022,027,002,077,277]` بصدفةٍ ومفسّرٍ حقيقيَّين: ‏`0o666 & ~U`، ولا `.install-tmp-*`.<br>• فالمؤقّت يولد بصلاحيّة النهائيّ، لأنّه لا `fchmod` بعد الإنشاء، و`os.replace` يُبقي الـinode نفسه. ومقيسٌ تحت 022 و077 (`security-review.md` ر2‑1 #4).<br>• لا `chmod` على الهدف: البند 5 أعلاه.<br>• ومعها الاختبارات القائمة: `test_env_is_0644_under_umask_022_and_nothing_else_secret_appears` وأختاه | ✅ مقبول |
| AC‑SR2.3 | • `test_an_existing_env_gets_its_owner_then_its_mode_before_the_first_byte`: التسلسل بالضبط `open(0o600)`، ثمّ `fchown(uid, gid)`، ثمّ `removexattr`، ثمّ `fchmod(0o640)`، ثمّ `fsync`. والحجم 0 في الأربعة الأولى.<br>• `test_an_existing_env_keeps_its_group_through_a_rewrite`: مجموعةٌ تكميليّة، ولم يُتخطَّ هنا.<br>• `test_a_refused_fchown_drops_the_group_bits_unless_root[0]`: الرمز 4 و`PermissionError while handling .env`، بلا `Traceback`. و`sha` و`mode` كما كانا، ولا مؤقّت.<br>• `test_the_temp_file_descriptor_is_closed_when_fchown_removexattr_or_fchmod_raises[fchown,fchmod,removexattr]`: الواصف مغلق (`EBADF`).<br>• ومعها الاختبارات القائمة الثلاثة لـ0640 و0600.<br>• فرع root الحقيقيّ مقيسٌ بـ`unshare -r` (`security-review.md` §1 التجربتان 3 و5، ور2‑1 #2) | ✅ مقبول |
| AC‑SR2.4 | • `test_a_link_planted_at_the_temp_path_is_refused_and_nothing_is_written_through_it`، بأربع حالات: هدفٌ قائم أو جديد، والرابط إلى ملفٍّ أو معلَّق.<br>• في كلّ حالة: `os.open` مرّةً بالأعلام الأربعة، و0o600 للقائم و0o666 للجديد. والرمز 4 و`FileExistsError while handling .env`، بلا `Traceback`.<br>• الملفّ الضحيّة سليم، والمسار المعلَّق لم يُنشأ. و`.env` القائم كما هو بايتاً وصلاحيّة، والجديد لم يُنشأ.<br>• مقيسٌ بستّ حالات، ومعها الرابط الصلب (`security-review.md` ر2‑1 #7) | ✅ مقبول |
| AC‑SR2.5 | • البوّابات الستّ على `0aa13c9` (`test-plan.md` الجولة 3 §3‑3): ‏format ‏833، وcheck سليم، وmypy ‏467، وlint‑imports ‏8/0. وpytest على `tests/unit tests/architecture tests/eval`: ‏**6209 ناجحاً و0 متخطّى**. وtest-rules: OK.<br>• وأعدتُ بنفسي ملفّ الاختبار وruff: البندان 1 و2 أعلاه. واختبارات ACL تجري ولا تُتخطّى على هذا المضيف.<br>• `git diff master --stat`، وقطع `install.sh`، والتوقيع، والنداءات الأربعة: البندان 3 و4 أعلاه.<br>• الاختبارات المجاورة الخمسة التي في §6 كلّها ناجحة.<br>• **شرطٌ مفتوح لبوّابة ②:** عبارة «وفي CI» في المعيار لا دليل عليها قبل فتح الـPR، فـCI لا يعمل إلّا على PR وعلى `master`. انظر «ما يبقى لغيري» | ✅ مقبول على دليل المضيف، وشرط CI في بوّابة ② |
| AC‑SR2.6 | • `test_a_new_env_gets_what_the_directory_default_acl_gives[owner-only,named-user]`: صلاحيّة `.env` وبايتات ACL الوصول فيه تساوي ملفّ `touch` مرجعيّاً، و0o600 في `owner-only`.<br>• واستكشف QA بـ`setfacl` حقيقيّ عبر الصدفة، فطابق الملفّ المرجعيّ (`test-plan.md` 2‑4).<br>• والقياس الأمنيّ: 0600، و0640 مع `user:65534:r` كالمرجعيّ. وكان 0644 على `7edb69b` (SR2‑1، `security-review.md` ر2‑1 #4) | ✅ مقبول |
| AC‑SR2.7 | • **(أ)** `test_an_existing_env_keeps_its_access_acl`: التسلسل `open`، `fchown`، `setxattr`، `fchmod`، `fsync`. و`& 0o077 == 0` والحجم 0 في الثلاثة الأولى. وACL المؤقّت عند `fchmod` بايتات الهدف، والنهائيّ يساوي ما قبله بايتاً وصلاحيّة.<br>• **(ب)** `test_an_existing_env_does_not_take_the_directory_default_acl`: عند `open` ACL موروث قناعه 0، وعند `fchmod` لا ACL، وبعده `ENODATA` و0640.<br>• **(ج)** `test_an_unknown_error_reading_the_target_acl_is_raised_not_swallowed`: الرمز 4 و`OSError while handling .env`، بلا `Traceback`. و`.env` كما هو، ولا مؤقّت. وطفرة «`access_acl` يبتلع كلّ خطأ» تُسقطه (`test-plan.md` 3‑2، و`review.md` الجولة 3).<br>• والاستكشاف بالصدفة الحقيقيّة: الـACL بايتاً ببايت (`test-plan.md` 2‑4) | ✅ مقبول |
| AC‑SR2.8 | • `test_a_non_root_writer_keeps_the_group_when_only_the_owner_is_refused`: النداءات `[(uid, gid), (-1, gid)]`، و0640، والمجموعة باقية.<br>• `test_a_refused_fchown_drops_the_group_bits_unless_root[1000]`: الرمز 0، و0o600، ولا مؤقّت.<br>• `test_a_refused_fchown_drops_the_access_acl_too`: ‏`removexattr` ولا `setxattr`، وعند `fchmod` لا ACL و`& 0o070 == 0`. والنهائيّ 0600 بلا ACL، والرمز 0.<br>• **هذا الاختبار أضعف من نصّ المعيار:** يؤكّد `& 0o070` لا `& 0o077`، ولا يفحص الحجم عند `fchmod`. فتحقّقتُ من النصّ كاملاً بالمسبار (البند 6 أعلاه)، في الحالة نفسها: التسلسل `open`، `removexattr`، `fchmod(0o600)`، `fsync`. و`& 0o077 == 0` والحجم 0 في الثلاثة الأولى، ولا ACL عند `fchmod`. ونجح المسبار.<br>• ويوافقه القياس الأمنيّ في الجولة 3 (A5 وA5c): ‏`1000:1000 0600` بلا ACL في كلّ نقطة.<br>• **والفرع مُحاكى:** `geteuid` و`fchown` مزيَّفان، و`install.sh` لا يبلغه لأنّه يشترط root | ✅ مقبول |

**الحصيلة:** 8 من 8 معايير عليها دليل، وكلّها ✅. وعلى AC‑SR2.5 شرطٌ واحد يُتحقَّق منه في بوّابة ②.

### قرار المالك Q‑4 وNFR‑3
- **`.env` الجديد ما زال يأخذ صلاحيّة الإنشاء الافتراضيّة.** يُفتح بـ0o666 بلا `fchmod` ولا `os.umask`، فتطبّق النواة `umask`، أو ACL المجلّد الافتراضيّ إن وُجد. والدليل: AC‑SR2.2 بخمس قيمٍ لـ`umask`، وAC‑SR2.6.
- **ولم يُفرض 0600:** تحت 022 يبقى `.env` الجديد 0644.
- **`install.sh` لا يضبط صلاحيّة `.env` في أيّ مسار** (FR‑11): البند 5 أعلاه.
- **ولا يغيّر صلاحيّةً ضبطها المالك** (NFR‑3):
  - 0600 تبقى 0600 (AC‑SR2.1).
  - و0640 تبقى بمالكها ومجموعتها (AC‑SR2.3).
  - وACL الوصول يبقى بايتاً ببايت (AC‑SR2.7)، وكان قبل الإصلاح يسقط.
- **وسطر التذكير بـ`chmod 600 .env`** (FR‑22) لم يُمسّ.

### الملاحظة الأصليّة SR‑2: مُغلقة
- **الإصلاح الذي اقترحته متحقَّق للملفّ القائم:**
  - المؤقّت يُنشأ بـ`0o600` مع `O_EXCL|O_NOFOLLOW`.
  - ثمّ تُضبط صلاحيّة الهدف قبل أوّل بايت.
  - والاختبار يتجسّس على `fchmod` لا على `fsync`.
- **وللملفّ الجديد انحرافٌ مقبول:**
  - اقترحت الملاحظة `0o666 & ~umask` بـ`os.umask`. فاختار التصميم `0o666` مباشرة، وهو الحلّ (ب)، لأنّه يحترم ACL المجلّد (SR2‑1).
  - وقبلته المراجعة الأمنيّة (ر2‑2). فلا نافذة فيه، لأنّ المؤقّت يولد بصلاحيّة `.env` النهائيّ، ولا ملفّ أضيق قبله.
- **أحمر ثمّ أخضر بالتجربة نفسها:**
  - على القديم: المؤقّت 0644 فارغاً، والواصف المفتوح فيه قرأ الأسرار.
  - وعلى الجديد: لا انكشاف في أيّ خطوة، ومنها فرع root الحقيقيّ.
  - المصدر: `security-review.md` §1 التجربتان 1 و2، ور2‑1، والجولة 3.
- **المراجعتان PASS في الجولات الثلاث.**
  - `review.md`: ‏CR‑SR2‑1، وCR‑SR2‑2، وCR‑SR2‑4…6 أُغلقت. وCR‑SR2‑3 كان للمنسّق، وسطره موجودٌ في `../install-bundle/design.md:366`، في شجرة العمل، غير ملتزَم.
  - `security-review.md`: ‏SR2‑1 وSR2‑2 وSR2‑3 وSR2‑5 أُغلقت. وبقيت SR2‑4 وحدها، وهي معلومة، وأقبلها أدناه.
  - ولا FAIL في أيّ جولة.

### ملاحظات غير حاجبة مقبولة
- **SR2‑4 (معلومة):** من يكتب في مجلّد `.env` يستطيع السباق على الأسماء. هذا من نطاق SR‑1، و`require_trusted` يحجبه في `install.sh`. ويسجّل المنسّق سطرها تحت صفّ SR‑1 (`design.md` §8).
- **ضعف تأكيدٍ في `test_a_refused_fchown_drops_the_access_acl_too`:** يفحص `& 0o070` بدل `& 0o077`، ولا يفحص الحجم عند `fchmod`.
  - المعيار نفسه متحقَّق بالمسبار وبالقياس الأمنيّ.
  - وتشديد الاختبار تحسينٌ اختياريّ في دورةٍ لاحقة، ولا عمل الآن.
- **ما لم يُختبر:**
  - فتحٌ حقيقيّ بهويّةٍ ثانية: لا root ولا `newuidmap` على المضيف.
  - root حقيقيّ على المضيف: استُعمل `unshare -r` بدلاً منه.
  - NFS، وtmpfs بلا ACL، وSELinux.
  - وبقيّة الـxattr غير ACL الوصول تسقط مع `os.replace` (`SR‑13` المقترح، `design.md` §8).
  - أقبلها لأنّ `install.sh` يعمل بـroot، على `/opt` في المسار الموثَّق. وما قيس يغطّي ذلك المسار.

### ما يبقى لغيري
- **بوّابة ② (المنسّق، عند فتح الـPR):**
  - يُقرأ سجلّ `pytest -rs` في وظيفة `quality` في CI (`.github/workflows/ci.yml:69-70`)، ولا يجوز أن يظهر فيه `SKIPPED` للاختبارات التي تستدعي `_set_acl`:
    - `test_a_new_env_gets_what_the_directory_default_acl_gives[owner-only]` و`[named-user]`؛
    - `test_an_existing_env_keeps_its_access_acl`؛
    - `test_an_existing_env_does_not_take_the_directory_default_acl`؛
    - `test_a_refused_fchown_drops_the_access_acl_too`.
  - ويُنظر معها `test_an_existing_env_keeps_its_group_through_a_rewrite`، وهو يتخطّى إن لم يكن لمستخدم المشغّل مجموعةٌ ثانية.
  - **إن تُخطّي أيٌّ منها:** يصير AC‑SR2.5 🔴، ويعود العمل إلى المرحلة 6 (الخلفية)، ثمّ 8 (الجودة)، لتجري الاختبارات على نظام ملفّاتٍ فيه ACL.
- **المالك:** ⚠️ `deploy/vault/start.sh:138-139` يكتب مفتاح فكّ Vault والتوكن الجذر بنافذة SR‑2 نفسها، ثمّ يأتي `chmod 600`.
  - هو خارج نطاق SR‑2، ولا يحجبه.
  - الخيارات: (أ) يُضمّ إلى SR‑2، أو (ب) ملاحظةٌ جديدة `SR‑12`، أو (ج) لا عمل. ويوصي المصمّم بـ(ب) (`status.md` «قرارات وتصعيدات»).
- **5.3 (`upgrade.sh`):**
  - فروع غير root في AC‑SR2.8 (SR2‑3 وSR2‑5) لا يبلغها غيره، وقد اختُبرت بتزييف `geteuid` و`fchown` وحده.
  - فإن عمل `upgrade.sh` بغير root، يُقاس فيه رفض `fchown` حقيقيّاً بهويّةٍ ثانية.
  - وعليه أن يُبقي `require_root` و`require_trusted` على `ROOT` و`.env` قبل `atomic_write` (SR2‑4).
- **المنسّق:**
  - يعلّم صفّ SR‑2 في `../install-bundle/follow-ups.md` بـ«✅ أُصلح» مع الـcommits الأربعة.
  - ويضيف سطر SR2‑4 تحت صفّ SR‑1، و`SR‑13` المقترح.
  - ويلتزم سطر CR‑SR2‑3، ويحدّث `status.md`.

### الحكم النهائي
**مقبول.**
- المعايير الثمانية AC‑SR2.1…AC‑SR2.8 كلّها ✅، وعلى كلٍّ منها دليل: اختبارٌ موجودٌ ناجح، أو مخرج بوّابة، أو قياس مراجِع، أو المسبار.
- المراجعتان PASS في الجولات الثلاث، ولا ملاحظة حاجبة مفتوحة.
- قرار المالك Q‑4 وNFR‑3 محترمان، والملاحظة الأصليّة SR‑2 مُغلقة.
- **شرطٌ واحد ينتقل إلى بوّابة ②:** أن تجري اختبارات ACL في CI ولا تُتخطّى، وهذا جزء AC‑SR2.5.

</div>
