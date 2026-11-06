<div dir="rtl">

# خطة الاختبار: electrical-drawings، INC‑1، `PR‑D` (سلسلة التوريد، الخطوة S11)

الفرع: `feat/electrical-drawings-inc-1-pr-d` (الأساس `master` = `43cc74d`، الرأس `b534a65`، إيداع واحد). المرجع: `docs/lld-inc-1.md` الصفوف `O15`..`O19`، و`INC1‑AC‑15`، و`INC1‑AC‑16`، و§6 S11، و`INC1‑Q‑3`، و§1.3، وبند `C‑12` في `inc-1/pr-c/security-review.md:94`. كل ما في الجداول أدناه أُعيد تشغيله على الرأس، وإيداع QA يضيف ملف اختبار واحداً فقط (`tests/unit/test_supply_chain_scan_pr_d.py`، 34 اختباراً) وهذه الوثيقة.

نطاق `C‑12` هنا: بصمة الصورة الأساس و`--require-hashes` فقط. فحص الصور المبنية مؤجَّل بـ`INC1‑Q‑3` (أ) إلى خطوة «جاهزية الخادم»، ولا يُعدّ ثغرة تغطية.

## 1. مصفوفة التغطية

| المعيار | نوع الاختبار | الملف/الاختبار | الحالة |
|---|---|---|---|
| `INC1‑AC‑15` (1): `pip` في صورة `app` وبيئة الفحص بالإصدار المثبّت نفسه (26.2) | وحدة + **QA** | `test_supply_chain_check.py::test_the_pip_pin_is_one_version_everywhere_and_fixes_every_inc0_q_10_advisory` (المواضع الأربعة: `Dockerfile` و`services/ele_parse/Dockerfile` و`scan.sh` و`requirements.txt`)، `::test_the_app_dockerfile_installs_the_pinned_pip_before_the_project`، `::test_scan_sh_pins_pip_before_installing_the_project`؛ **جديد** `test_supply_chain_scan_pr_d.py::test_a_pip_pin_that_differs_in_any_one_of_the_four_places_fails` (4 حالات: كل موضع على حدة في نسخة مُحوَّرة)، `::test_a_pip_pin_that_no_longer_fixes_the_six_advisories_fails_even_when_uniform`، `::test_the_ele_parse_environment_is_installed_with_require_hashes_from_its_file` (ترتيب `pip==26.2` قبل `.[parsers]` في استدعاءات `pip` الفعلية)؛ تشغيل: `pip 26.2` في صورة `ele-parse` مبنيّة فعلاً (§3، س12) | ✅ |
| `INC1‑AC‑15` (2): الاستثناءات الستة لـ`pip` محذوفة | وحدة + **QA** | `test_supply_chain_check.py::test_the_shipped_policy_holds_no_vulnerability_exception`، `::test_every_widening_entry_of_the_shipped_policy_names_its_decision` (`raw.get("vulnerability") == []`)؛ **جديد** `test_supply_chain_scan_pr_d.py::test_no_vulnerability_exception_is_left_to_hide_a_pip_advisory` (السياسة الحقيقية والفاحص الحقيقي: `pip 25.0.1` مع `PYSEC-2026-3721` مخالفة لا مستثناة)؛ تشغيل: `scan.sh` بـ`pip_version=25.0.1` يخرج بـ1 مع 6 ثغرات (§3، س7) | ✅ |
| `INC1‑AC‑15` (3): `scan.sh` يفحص اعتماديات `ele-parse` أيضاً (SBOM وتدقيق وسياسة) | وحدة + **QA** + تشغيل | `test_supply_chain_check.py::test_scan_sh_scans_the_ele_parse_environment_and_keeps_the_worse_exit_code`؛ **جديد** `test_supply_chain_scan_pr_d.py::test_both_environments_clean_exit_zero_and_the_report_holds_both` (سطر ملخّص ومجلد `ele-parse/` بـ`sbom` و`audit` و`freeze` وتقرير لكل بيئة)، `::test_the_ele_parse_environment_is_installed_with_require_hashes_from_its_file`؛ تشغيل `scan.sh` الحقيقي: سطران `supply-chain [runtime (app image)]` و`[ele-parse image]`، 2 مجمَّدان و2 مدقَّقان | ✅ |
| `INC1‑AC‑15` (4): `pymupdf` معلَّمة AGPL في البيئتين | **QA** + تشغيل | **جديد** `test_both_environments_clean_exit_zero_and_the_report_holds_both` (`report.count("AGPL") >= 2`)؛ تشغيل `scan.sh` الحقيقي: `report.md` يحمل «AGPL-flagged components … pymupdf 1.28.2» في قسمَي `runtime` و`ele-parse`؛ **جديد** `::test_pymupdf_without_a_licence_in_the_ele_parse_environment_alone_fails_the_scan` (ترخيص غير مسموح لـ`pymupdf` في `ele-parse` وحدها ← 1) | ✅ |
| `INC1‑AC‑15` (5): `scan.sh` ينجح (PASS) | تشغيل | `deploy/supply-chain/scan.sh` على الرأس: خروج 0، والبيئتان PASS (§2) | ✅ |
| `INC1‑AC‑15`: الفشل حين يجب (لا قناع بين البيئتين) | **QA** (جديد) + تشغيل | `test_supply_chain_scan_pr_d.py::test_a_vulnerable_pip_in_one_environment_alone_fails_the_scan` (2)، `::test_an_unapproved_licence_in_the_ele_parse_environment_alone_fails_the_scan`، `::test_a_missing_input_of_one_environment_is_exit_2_not_a_pass` (4)، `::test_the_exit_code_is_the_worse_of_the_two_in_both_orders` (2)، `::test_a_tampered_hash_aborts_before_any_check_and_exits_nonzero`، `::test_a_failing_sbom_tool_aborts_and_exits_nonzero`، `::test_a_stale_output_of_an_earlier_run_is_never_read_as_fresh` (3)؛ تشغيل حقيقي في نسخة مؤقتة: 7 حالات (§3) | ✅ |
| `INC1‑AC‑15`: ملاحظة اختلاف إصدار `pymupdf` | **QA** | `::test_the_pymupdf_versions_are_noted_when_they_differ_and_are_not_a_failure`، `::test_the_pymupdf_note_says_so_when_the_versions_are_the_same` | ✅ |
| `INC1‑AC‑15` / `O18`: اختبارات الوحدة تفشل عند الانجراف | **QA** | `test_supply_chain_scan_pr_d.py`: `::test_the_guards_pass_on_an_unmutated_copy` (الشاهد)، `::test_a_second_pymupdf_pin_fails`، `::test_a_requirement_without_a_hash_fails`، `::test_a_malformed_hash_fails`، `::test_a_range_instead_of_an_exact_pin_fails`، `::test_a_tag_without_a_digest_in_the_ele_parse_dockerfile_fails`، `::test_an_install_without_require_hashes_in_the_ele_parse_dockerfile_fails`، `::test_a_truncated_digest_fails`؛ وتحوير مضاد: حذف سطر «الأسوأ من الاثنين» من نسخة `scan.sh` يُخرج 0 رغم فشل `ele-parse` (فيفشل اختبار `ele-parse` وحدها) | ✅ |
| `INC1‑AC‑15` / `O19` (جزء `scan.sh`): `docs/stack-commands.md` يطابق السلوك | قراءة + تشغيل | §31‑ب: بيئتان، رمز الخروج الأسوأ، مجلد `ele-parse/`، رفع `ci.yml` للملفات الأربعة وحدها، ملاحظة `pymupdf`، `--require-hashes`، «لا استثناء ثغرات اليوم»، التأجيل إلى «جاهزية الخادم»: كلها طابقت `scan.sh` وسلوكه المُجرَّب (§3، س12) | ✅ |
| `INC1‑AC‑16` (حصة `PR‑D`): البوابات الست خضراء، و`scan.sh` أخضر، ولا سطر في `rag_agent` ولا `knowledge` | بوابات | §2. `git diff master..HEAD --stat -- src/app/agents/rag_agent src/app/modules/knowledge` = 0 سطر. وإيداع `PR‑D` لا يمسّ `src/` ولا `.importlinter`. والاختبارات القائمة: عُدِّل منها اختبار واحد (`test_the_shipped_exceptions_are_the_pip_advisories_of_inc0_q_10_alone` حُذف) وحلقة التحقق من `INC0‑Q‑10` في اختبار آخر، واستُبدلا بما هو أقوى (`vulnerability == []` والتحقق من إصلاح الستة) وفق `O18`؛ لم يُضعَّف غيرهما | ✅ |
| `C‑12` (أ): الصورة الأساس مثبّتة بالبصمة | وحدة + تشغيل | `test_supply_chain_check.py::test_the_ele_parse_dockerfile_pins_the_base_by_digest_and_installs_with_hashes`؛ و`docker buildx imagetools inspect python:3.12-slim-bookworm@sha256:935c3c5b…` ← `application/vnd.oci.image.index.v1+json` بقوائم `linux/amd64` و`linux/arm/v7` وغيرهما (فهرس متعدّد المعماريات كما يقول الوسم والتعليق) | ✅ |
| `C‑12` (ب): `--require-hashes` | وحدة + تشغيل | `test_the_ele_parse_requirements_pin_pymupdf_once_and_hash_every_line`، `::test_the_shipped_files_say_the_hash_check_covers_pip_itself` (`pip==26.2` بتجزئته في الملف نفسه، والتثبيت واحد مع `--require-hashes` وفحص `pip --version`)؛ بناء حقيقي: انظر §3 س8 وس12 | ✅ |
| `C‑12` (ج): فحص الصورة المبنية | — | مؤجَّل بقرار المالك `INC1‑Q‑3` (أ)، خارج PR‑D | غير مطلوب |

لا معيار بلا تغطية.

## 2. نتائج بوابات المشروع

على الرأس `b534a65` مع ملف QA الجديد (غير مودَع وقت التشغيل)، بترتيب `CLAUDE.md`:

```
$ .venv/bin/ruff format --check .
984 files already formatted
$ .venv/bin/ruff check .
All checks passed!
$ .venv/bin/mypy src
Success: no issues found in 547 source files
$ .venv/bin/lint-imports
Contracts: 9 kept, 0 broken.
$ .venv/bin/pytest -rs tests/unit tests/architecture tests/eval
8260 passed, 37 skipped, 7 warnings in 255.02s (0:04:15)
$ deploy/prometheus/test-rules.sh
  SUCCESS: 31 rules found

test-rules: OK
$ deploy/supply-chain/scan.sh
supply-chain [runtime (app image)]: 89 frozen; 89 components, 0 licence violation(s), 0 not audited; 89 packages audited, 0 vulnerability finding(s), 0 excepted -> PASS
supply-chain [ele-parse image]: 2 frozen; 2 components, 0 licence violation(s), 0 not audited; 2 packages audited, 0 vulnerability finding(s), 0 excepted -> PASS
scan.sh exit code: 0 (runtime and ele-parse, the worse of the two)
(الخروج الفعلي من الغلاف: GATE_RC=0، الزمن 40 ثانية بذاكرة تخزين pip دافئة)
```

التخطّيات الـ37: كلها حزم حاويات اختيارية تحتاج متغيرات (`RUN_EDGE_FILES_TEST=1` وأخواتها) أو خدمات حيّة، ولا واحد منها يخصّ `PR‑D`. لم أشغّل `tests/integration` (المكدّس الحيّ مستعمل، `CLAUDE.md`). لم تُضَف مرشِّحات تخفي اختباراً.

## 3. الاختبار الاستكشافي

كل صف «تشغيل حقيقي» نُفِّذ بـ`scan.sh` الحقيقي (شبكة PyPI وقاعدة الثغرات حقيقيتان) على نسخة `git archive HEAD` في مجلد مؤقت خارج المستودع، مع تحوير واحد في كل نسخة. والصفوف «محاكاة» تجري في الملف الجديد بلا شبكة.

| # | السيناريو | الخطوات | المتوقع | الفعلي | الحكم |
|---|---|---|---|---|---|
| 1 | `pip` ضعيف في `ele-parse` وحدها | `requirements.txt`: `pip==25.0.1` بتجزئته الصحيحة | خروج 1، و`runtime` PASS لا يقنّعه | `pip-audit` (ele) = 1، `ele-parse image`: 6 ثغرات FAIL، `runtime` PASS، `scan.sh exit code: 1` | ✅ |
| 2 | ترخيص مخالف في `ele-parse` وحدها | إضافة `pyphen==0.17.0` (خارج مدى القرار) بتجزئته | خروج 1 | `ele-parse image`: مخالفة ترخيص واحدة FAIL، `runtime` PASS، خروج 1 | ✅ |
| 3 | SBOM مفقود لـ`ele-parse` | حذف `ele-parse/sbom.cdx.json` بعد خطوة SBOM | خروج 2 لا نجاح | `Result: NOT RUN` في تقرير `ele-parse`، `exit code: 2`؛ وفي المحاكاة: `skip` ← 2، و`cyclonedx-py` يفشل ← خروج غير صفري بلا تشغيل أي فاحص | ✅ |
| 4 | تدقيق `ele-parse` لم يُنتج ملفاً | حذف `ele-parse/audit.json` بعد التدقيق | خروج 2 | `NOT RUN`، `exit code: 2` (الفاحص يعمل على `runtime` ويمرّ، ولا يقنّع الثاني) | ✅ |
| 5 | `runtime` فاشل و`ele-parse` ناجح | `pip_version=25.0.1` في `scan.sh` | خروج 1 | `runtime`: 6 ثغرات FAIL، `ele-parse` PASS، خروج 1 | ✅ |
| 6 | تدقيق `runtime` مفقود و`ele-parse` ناجح | حذف `audit.json` الأعلى | خروج 2 | `runtime` NOT RUN، `ele-parse` PASS، خروج 2 | ✅ |
| 7 | الأسوأ من الاثنين بالترتيبين | محاكاة: (1 و2) و(2 و1) | خروج 2 | خروج 2 في الحالتين | ✅ |
| 8 | تجزئة مُعبَث بها | تغيير حرف في تجزئة `pymupdf` + نتائج قديمة مزروعة (`report.md` بـ«PASS STALE» و`ele-parse/audit.json`) | فشل التثبيت وخروج غير صفري | `THESE PACKAGES DO NOT MATCH THE HASHES`، خروج 1. مجلد `ele-parse/` أُفرغ فلا يُقرأ قديمه. لكن `build/supply-chain/report.md` القديم بقي (انظر **QA‑1**) | ✅ مع ملاحظة |
| 9 | `set -e` وقديم الملفات | محاكاة: `audit.json` أو `ele-parse/audit.json` أو `ele-parse/sbom.cdx.json` قديم صالح + إنتاج جديد مفقود | لا يُقرأ القديم: خروج 2 | خروج 2 في الحالات الثلاث (`scan.sh:63` `rm -rf $ele` و`:107` `rm -f audit.json`) | ✅ |
| 10 | `--label` والمخرج الافتراضي | تشغيل `supply_chain_check.py` على `master` وعلى الرأس بالمدخلات نفسها بلا `--label` | مطابقة بايت ببايت | تقرير وسطر ملخّص متطابقان (`cmp`)، والاختبارات القائمة خضراء بلا تعديل | ✅ |
| 11 | ملاحظة تعارض `pymupdf` | محاكاة: `runtime` 1.28.3 مقابل `ele-parse` 1.28.2 ثم متساويان | ملاحظة لا فشل | «the versions DIFFER» أو «The versions are the same.»، خروج 0 | ✅ |
| 12 | `--require-hashes` في الصورة، و`pip` في الاستدعاء نفسه | بناء `services/ele_parse/Dockerfile` الحقيقي بوسم مؤقت `qa-prd-eleparse:t1`، ثم نسخة بتجزئة مُعبَث بها، ثم `--build-arg PIP_VERSION=26.1` | الأول ينجح بـ`pip 26.2` و`pymupdf 1.28.2` فقط، والآخران يفشلان | الأول: `pip 26.2` و`pymupdf==1.28.2`. الثاني: `exit code: 1` من خطوة `RUN`. الثالث: `exit code: 1` من فحص `pip --version` بعد التثبيت. حُذفت الصورة (`docker rmi`) | ✅ |
| 13 | الفهرس متعدّد المعماريات | `docker buildx imagetools inspect` (قراءة فقط) على البصمة المثبّتة | فهرس OCI | `application/vnd.oci.image.index.v1+json`، `linux/amd64` و`linux/arm/v7` وغيرهما | ✅ |
| 14 | مطابقة الوثائق (`docs/stack-commands.md` §31‑ب، السطران 296 و467‑480) | قراءة السطور مقابل `scan.sh` | تطابق | تطابق في كل ادعاء (المخرجات، الأسوأ من الاثنين، الاستثناءات الصفرية، التأجيل) | ✅ |
| 15 | بقايا ادعاءات قديمة (استثناء `pip`، ملكية `PLT‑17` لـ`pip`، بيئة واحدة) | `grep` على المستودع كله | لا بقايا خارج التاريخ | بقايا واحدة فعلية: `docs/deploy-server.md:469` (**QA‑2**). والباقي سجلّ تاريخي (تعليق `license-policy.toml:94`، وثائق `inc-0`) أو تعليقات تشرح الحذف. `docs/capacity-status.md` و`capacity-summary.html` لا يذكران سلسلة التوريد: لا أثر | ✅ مع ملاحظة |
| 16 | مدخلات ضخمة/عربية | ليست ضمن سطح `PR‑D` (لا مدخل مستخدم؛ المدخلات ملفات مثبّتة) | — | لم يُنفَّذ، لا ينطبق | — |

## 4. الأخطاء المكتشفة

لا أخطاء حاجبة ولا كبرى. المنخفضة:

| # | الوصف | الشدة | خطوات إعادة الإنتاج | الحالة |
|---|---|---|---|---|
| QA‑1 | `scan.sh` لا يحذف في أول سطر `report.md` و`sbom.cdx.json` و`runtime-freeze.txt` الأعلى من تشغيل سابق. فإذا انقطع التشغيل الجديد بـ`set -e` (مثلاً تجزئة مُعبَث بها) بقي `report.md` القديم بنتيجة «PASS» في `build/supply-chain/` رغم خروج غير صفري. لا أثر في CI (نسخة نظيفة والخروج غير صفري يُحمِّر الوظيفة)، وأثره محلي فقط. `scan.sh:62-64` يحذف `ele-parse/` و`audit.json` وحدهما | منخفضة | زرع `build/supply-chain/report.md` بنص «PASS STALE»؛ تغيير حرف في تجزئة `pymupdf` في `services/ele_parse/requirements.txt`؛ تشغيل `scan.sh` ← خروج 1 والملف القديم باقٍ | مفتوحة (devops) |
| QA‑2 | `docs/deploy-server.md:469` (ث‑18) ما زال يقول إن قاعدة `ele-parse` «وسمٌ قابلٌ للتغيّر بلا بصمة» ويجعل «بصمة للقاعدة» أول العلاج، وقد نفّذه `PR‑D` (C‑12). الجملة تضلّل قارئ «جاهزية الخادم» | منخفضة (وثائق) | قراءة السطر 469 مقابل `services/ele_parse/Dockerfile:15` | مفتوحة (devops) |

ملاحظات لا تُعدّ أخطاء: (1) `ci.yml:214` يسمّي الخطوة «(runtime closure)» وهي الآن تفحص بيئتين، وتسمية فقط وCI مقصود ألّا يتغيّر (`INC1‑Q‑3` (أ))؛ (2) `Dockerfile` الجذر يثبّت `pip==${PIP_VERSION}` بلا تجزئة وصورته الأساس بوسم لا ببصمة، وهذا خارج `C‑12` (الذي يخصّ `ele-parse`)؛ (3) وقت `scan.sh` 40 ثانية دافئاً، ومهلة CI 15 دقيقة تكفي.

## 5. الحكم

**PASS** — البوابات السبع خضراء (`scan.sh` بخروج 0)، وكل بنود `INC1‑AC‑15` وحصة `PR‑D` من `INC1‑AC‑16` و`C‑12` (البصمة و`--require-hashes`) مغطّاة باختبار مُشغَّل، والفحص يفشل حيث يجب (7 تشغيلات حقيقية و34 اختبار محاكاة وانجراف)، ولا خطأ حاجب أو كبير، وفيه ملاحظتان منخفضتان (QA‑1، QA‑2).

</div>
