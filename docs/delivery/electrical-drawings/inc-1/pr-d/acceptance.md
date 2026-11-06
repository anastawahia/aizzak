<div dir="rtl">

# قبول مالك المنتج: سلسلة التوريد (`PR‑D` من `INC‑1`، الخطوة S11، ميزة `electrical-drawings`)

- **الفرع:** `feat/electrical-drawings-inc-1-pr-d` عند `8168a22`، على `master@43cc74d`، وهو أساس الدمج نفسه. اثنا عشر إيداعاً، والفرق 14 ملفاً (+1681 و‑133).
- **المرجع:** `docs/delivery/electrical-drawings/docs/lld-inc-1.md` 0.7 (تعديلات غير مودعة): §5.1 و§5.2، والصفوف `O15`..`O19`، و`INC1‑Q‑3`، و`INC1‑R‑19`، و`INC1‑R‑20`. وقرأتُ فرق 0.7 غير المودع: ثلاث كتل فقط (الترويسة، و`O15` و`O16`، وصفّا المخاطر). فلا يمسّ §5، ونص `INC1‑AC‑15` و`INC1‑AC‑16` لم يتغيّر.
- **النطاق:** `INC1‑AC‑15` ببنوده، وحصة `PR‑D` من `INC1‑AC‑16`، والجزء المحدّد من `C‑12` (البصمة و`--require-hashes` في صورة `ele-parse`). وفحص الصورة المبنية مؤجَّل بقرار `INC1‑Q‑3` (أ)، فليس في المعايير. والـLLD لا يرتّب معاييره، فكل معيار في النطاق Must وشرط للقبول، كسوابق `PR‑A` و`PR‑B` و`PR‑C`.
- **الأدلة المقروءة:**
  - `inc-1/pr-d/test-plan.md`: PASS، ثم PASS بعد دورة الإعادة 1 (§6).
  - `review.md`: FAIL بحاجبة #1، ثم PASS في إعادة المراجعة 1 على `cc6e5a9`.
  - `security-review.md`: FAIL بحاجبة PD‑1، ثم PASS في إعادة المراجعة 1 على `cc6e5a9`.
  - `status.md`: الصفوف 1ز..13ز، وصفّا «مراجعة `PR‑D`» 1 و2.

## الجولة 2 (`bba8a28` و`8168a22`): لم يُعِد المراجعون النظر فيها، فقرأتُ فرقها بنفسي

قرأتُ `git diff 2826224..8168a22` كاملاً: 4 ملفات، +28 و‑8. وهو لا يفعل إلا ما طُلب منه:

| البند | الموضع | ما وجدتُه | الحكم |
|---|---|---|---|
| ر‑2 (سطر التنظيف) | `deploy/supply-chain/scan.sh:65-66` | `audit.json` صار في `rm -f` الأول، فالتشغيل المجهَض لا يترك أي مخرج قديم. والتعليق فوقه صحيح. وبقي `rm -f` الثاني في `:112` مكرراً بلا ضرر، فلا أشترط حذفه | ✅ |
| ر‑1 وQA2‑1 (تعليق السياسة) | `license-policy.toml:94-98` | طابقته بالملفات: 26.3 في `pip-bootstrap.txt` (يقرؤه `Dockerfile:52-54` و`scan.sh:73-74`) و`services/ele_parse/Dockerfile:32` و`requirements.txt:21-22`. والحدّ 26.2.2 في `test_supply_chain_check.py:185` فعلاً. والتعليق وحده تغيّر، ولا مدخل سياسة | ✅ |
| PD‑12 (1) و(2) (الاختبارات) | `test_supply_chain_scan_pr_d.py:142`، و`:636-638` | **جرّبتُ التحوير بنفسي** على نسخة `git archive` في مجلد مؤقت. حذف `audit.json` من سطر التنظيف يُفشل `test_an_aborted_run_leaves_no_output_of_an_earlier_run[audit.json]` وحده. وحذف `report-runtime.md` يُفشل `[report-runtime.md]` وحده. وتغيير حرف في تجزئة `pip` في `requirements.txt` يُفشل `test_the_pip_hash_is_the_same_in_the_bootstrap_and_the_ele_parse_requirements`. وعلى النسخة غير المحوَّرة لا فشل. فالاختبارات تقيس فعلاً | ✅ |
| PD‑10 (1) وQA‑2 (ث‑18 محصور في `ele-parse`) | `docs/deploy-server.md:469` | «الأثر في `ele-parse` وحدها محدود بالعزل»، وبقية الصف تطابق `services/ele_parse/Dockerfile:15` و`release.yml` (لا `scan.sh` فيه) | ✅ |
| PD‑11 ور‑4 (ث‑19) | `docs/deploy-server.md:470` | طابقتُ وقائعه: `FROM` بلا بصمة في `Dockerfile:15` و`:61`، و`.[parsers]` بلا تجزئات، و`hatchling` بلا إصدار في `pyproject.toml:2`، ولا `scan.sh` في `release.yml`. والتوقيت لم يعد منسوباً إلى `INC1‑Q‑3` (أ)، بل يُعرض عند البوابة ②. وذُكرت عجلة `ensurepip` | ✅ |
| PD‑10 (1) (ث‑20 الجديد) | `docs/deploy-server.md:471`، والبند 7 في `:441` | طابقتُ وقائعه: `fitz.open` في `pdf_text.py:131` و`image_ocr.py:234` و`pdf_tables.py:275`، والسقف في `pyproject.toml:122`، و`worker-knowledge` يأخذ `*app-env` وفيه `VAULT_ROLE_ID` و`VAULT_SECRET_ID`، و`DATABASE_URL` بدور `app_rw` (`docker-compose.yml:147-151`، `:399`، `:2031`). ويقول صادقاً إن النتيجة من قراءة الشيفرة ولم يثبت خطأ ذاكرة مبلوغ. والصف بأربعة أعمدة كبقية الجدول | ✅ |

**النبرة العربية:** الأسطر الثلاثة بنبرة الجدول نفسها، ولا مبالغة فيها ولا تخفيف. ولي ملاحظتان صغيرتان على الصياغة (PO‑1 وPO‑2 أدناه)، ولا تغيّران معنى.

لا شيء في الفرق خارج هذه البنود: لا مسار ولا هجرة ولا إعداد ولا اعتمادية ولا تغيير في `Dockerfile`. فأقبل ألا يُعاد النظر فيها، كسابقتَي `PR‑A` 2 و`PR‑C` 3.

## ما تحقّقتُ منه بنفسي على `8168a22` (2026-11-06، بلا أي تعديل في الشيفرة)

| البوابة | السطر الحرفي | الخروج |
|---|---|---|
| `.venv/bin/ruff format --check .` | `984 files already formatted` | 0 |
| `.venv/bin/ruff check .` | `All checks passed!` | 0 |
| `.venv/bin/mypy src` | `Success: no issues found in 547 source files` | 0 |
| `.venv/bin/lint-imports` | `Contracts: 9 kept, 0 broken.` | 0 |
| `.venv/bin/pytest -rs tests/unit tests/architecture tests/eval` | `8281 passed, 37 skipped, 7 warnings in 267.44s (0:04:27)` | 0 |
| `deploy/prometheus/test-rules.sh` | `SUCCESS: 31 rules found`، ثم `test-rules: OK` | 0 |
| `deploy/supply-chain/scan.sh` | الأسطر الثلاثة أدناه | 0 |

```
supply-chain [runtime (app image)]: 89 frozen; 89 components, 0 licence violation(s), 0 not audited; 89 packages audited, 0 vulnerability finding(s), 0 excepted -> PASS
supply-chain [ele-parse image]: 2 frozen; 2 components, 0 licence violation(s), 0 not audited; 2 packages audited, 0 vulnerability finding(s), 0 excepted -> PASS
scan.sh exit code: 0 (runtime and ele-parse, the worse of the two)
```

- **التخطّيات الـ37:** كلها في عُدّتَي الحاويات الاختياريتين: 18 في `test_ele_parse_containers.py` («set RUN_ELE_PARSE_CONTAINER_TEST=1») و19 في `test_public_file_links_containers.py` («set RUN_EDGE_FILES_TEST=1»). ولا FAILED ولا ERROR ولا `xfail`. والعدد يطابق صف «مراجعة `PR‑D`» 2 في `status.md`.
- **`git diff master..HEAD --stat -- src/app/agents/rag_agent src/app/modules/knowledge pyproject.toml .github .importlinter`: فارغ.** وفارغ أيضاً على `src` و`migrations` و`deploy/prometheus` و`docker-compose.yml` و`docker-compose.build.yml`. وعدد `ignore_imports` في `.importlinter` هو 5 على `master` و5 على الرأس.
- **الاختبارات القائمة:** `git diff --numstat master..HEAD -- tests` يذكر ملفين فقط: `test_supply_chain_check.py` (+187 و‑22) و`test_supply_chain_scan_pr_d.py` (جديد). وقرأتُ الأسطر الـ22 المحذوفة: اختبار الاستثناءات الستة وقائمتها وحلقة `INC0‑Q‑10`، واستُبدلت بـ`vulnerability == []` والتحقق من الإصلاح، كما يطلب `O18`. ولم يُمسّ اختبار رفع ولا استبدال ولا حصة.
- **التجزئات والبصمة، من المصدر لا من التقارير:**
  - PyPI JSON: `pip-26.3-py3-none-any.whl` هي `f3b60b20…fad13`، و`pymupdf-1.28.2-…x86_64.whl` هي `397d6715…a8227f`، وعجلة `aarch64` هي `3050a233…842545`. وكلها تطابق الملفات، وغير مسحوبة.
  - `docker buildx imagetools inspect`: البصمة `sha256:935c3c5b…c03df` فهرس OCI متعدّد المعماريات، ومبني في 2026-11-05، وهي بصمة الوسم `3.12-slim-bookworm` اليوم.
- **كل اختبار مذكور في الجدول أدناه وجدتُه باسمه في ملفه** (`grep "def <name>"`، على 30 اسماً). واسم واحد في `test-plan.md` غير صحيح، وانظر PO‑3.
- **ما لم أشغّله:**
  - **بناء صورة `app` أو `ele-parse`:** دوري قراءة، ولا أبني. وأدلة الصورة المبنية قياسان مستقلان: QA (`test-plan.md` §6.2) والمراجع الأمني (إعادة المراجعة 1)، كلاهما على شيفرة `cc6e5a9`. وتحقّقتُ أن الشيفرة لم تتغيّر بعدها: `git diff cc6e5a9..HEAD --stat -- Dockerfile services/ele_parse deploy/supply-chain/pip-bootstrap.txt deploy/supply-chain/supply_chain_check.py .dockerignore` فارغ.
  - **`tests/integration` وعدّة الحاويات:** المكدّس الحيّ مستعمل (`CLAUDE.md`)، و`PR‑D` لا يمسّ `src`.
  - **وظائف CI:** الفرع ليس على المستودع البعيد بعد (`git ls-remote` فارغ، ولا طلب دمج)، فلم تجرِ وظيفة `supply-chain` قط بالبيئتين. وهذا شرط دمج.
  - **تثبيت عجلة `aarch64`:** طابقتُ تجزئتها فقط. والمنصة التي يبنيها `build-push.sh` هي `linux/amd64`.
- **حدّ على تشغيلي لـ`scan.sh`:** بيئة `build/supply-chain/tools` على هذا المضيف عمرها من 2026-10-30، و`pip` فيها 24.0، ولا يعيد السكربت بناءها (PD‑5). فوظيفة CI النظيفة هي الدليل الأقوى.

## المعايير

| AC | الدليل (اختبار/لقطة/مخرج) | الحكم |
|---|---|---|
| `INC1‑AC‑15` (1): `pip` في بيئة الفحص بالإصدار المثبّت | تشغيلي: `pip==26.3` في `build/supply-chain/runtime-freeze.txt:48` وفي `ele-parse/freeze.txt`، وترويسة `report.md` تقرأ الإصدار من التجميد. والتثبيت من `pip-bootstrap.txt` بـ`--require-hashes` في `scan.sh:73-74`. واختبار: `test_supply_chain_check.py::test_scan_sh_installs_the_hashed_pip_before_the_project`. و«بيئة الفحص» هي البيئة المفحوصة `runtime`، لا بيئة الأدوات `tools` التي تشغّل `pip-audit` ولا تُشحن (PD‑5) | ✅ مقبول |
| `INC1‑AC‑15` (1): `pip` في صورة `app` بالإصدار نفسه، ونسخة مثبّتة واحدة (O15) | الملف نفسه في `Dockerfile:52-54`، و`pip` الأساس يُحذف في `Dockerfile:135-137`. واختبارات: `test_supply_chain_check.py::test_the_app_dockerfile_installs_the_hashed_pip_before_the_project`، و`::test_the_app_runtime_stage_removes_the_base_images_own_pip`، و`::test_the_pip_pin_is_one_version_everywhere_and_fixes_every_known_advisory`؛ و`test_supply_chain_scan_pr_d.py::test_a_pip_pin_that_differs_in_any_one_of_the_places_fails` (3)، و`::test_the_shipped_pip_pin_is_at_or_above_the_certifi_floor`. والصورة المبنية (قياسان مستقلان على شيفرة لم تتغيّر): `which -a pip` يعطي `/opt/venv/bin/pip` وحده، و`find` يجد `pip-26.3.dist-info` واحداً، و`/usr/local/bin/python -m pip` يقول `No module named pip`، و`pip` 26.3 | ✅ مقبول |
| `INC1‑AC‑15` (2): الاستثناءات الستة محذوفة | `grep -c '^\[\[vulnerability\]\]' license-policy.toml` يعطي 0. وتشغيلي: `0 excepted` في السطرين. واختبارات: `test_supply_chain_check.py::test_the_shipped_policy_holds_no_vulnerability_exception`، و`test_supply_chain_scan_pr_d.py::test_no_vulnerability_exception_is_left_to_hide_a_pip_advisory` (`pip 25.0.1` مخالفة لا مستثناة)، و`::test_a_pip_pin_that_no_longer_fixes_the_six_advisories_fails_even_when_uniform` | ✅ مقبول |
| `INC1‑AC‑15` (3): `scan.sh` يفحص اعتماديات `ele-parse` أيضاً | تشغيلي: سطر `ele-parse image`، و`build/supply-chain/ele-parse/` فيه `freeze.txt` و`sbom.cdx.json` و`audit.json` و`report.md`. واختبارات: `test_supply_chain_check.py::test_scan_sh_scans_the_ele_parse_environment_and_keeps_the_worse_exit_code`، و`test_supply_chain_scan_pr_d.py::test_both_environments_clean_exit_zero_and_the_report_holds_both`، و`::test_the_ele_parse_environment_is_installed_with_require_hashes_from_its_file`. والفشل لا يتقنّع: `::test_a_vulnerable_pip_in_one_environment_alone_fails_the_scan` (2)، و`::test_an_unapproved_licence_in_the_ele_parse_environment_alone_fails_the_scan`، و`::test_a_missing_input_of_one_environment_is_exit_2_not_a_pass` (4)، و`::test_the_exit_code_is_the_worse_of_the_two_in_both_orders` (2) | ✅ مقبول |
| `INC1‑AC‑15` (4): PyMuPDF معلَّمة AGPL | تشغيلي: `- pymupdf 1.28.2: AGPL-3.0-only OR LicenseRef-Artifex-Commercial` في `report.md:25` و`:142`، أي في قسمَي البيئتين. واختبارات: `test_both_environments_clean_exit_zero_and_the_report_holds_both` (يقسم التقرير ويطلب السطر في كل قسم)، و`::test_pymupdf_without_a_licence_in_the_ele_parse_environment_alone_fails_the_scan` | ✅ مقبول |
| `INC1‑AC‑15` (5): `scan.sh` ينجح | تشغيلي: خروج 0، والبيئتان PASS، و0 مخالفات، و0 ثغرات (أعلاه). والسلبي: 7 تشغيلات حقيقية عند QA (`test-plan.md` §3)، و`::test_a_tampered_hash_aborts_before_any_check_and_exits_nonzero`، و`::test_a_stale_output_of_an_earlier_run_is_never_read_as_fresh`، و`::test_an_aborted_run_leaves_no_output_of_an_earlier_run` (5) | ✅ مقبول |
| `INC1‑AC‑16`، حصة `PR‑D`: البوابات الست و`scan.sh` خضراء | سبعتها بتشغيلي على `8168a22` (أعلاه) | ✅ مقبول |
| `INC1‑AC‑16`، حصة `PR‑D`: لا سطر في `rag_agent` ولا `knowledge` | الفرق فارغ (أعلاه)، ومعه `pyproject.toml` و`.github` و`.importlinter` | ✅ مقبول |
| `INC1‑AC‑16`، حصة `PR‑D`: اختبارات الرفع والاستبدال والحصة بلا تعديل | لم يُمسّ منها شيء. والمعدَّل وحده `test_supply_chain_check.py`، وهو ما يطلبه `O18` (أعلاه) | ✅ مقبول |
| `C‑12` (الجزء المحدّد): بصمة الأساس في صورة `ele-parse` | `services/ele_parse/Dockerfile:15`، والبصمة بتشغيلي `imagetools inspect` (أعلاه). واختبارات: `test_supply_chain_check.py::test_the_ele_parse_dockerfile_pins_the_base_by_digest_and_installs_with_hashes`، و`test_supply_chain_scan_pr_d.py::test_a_tag_without_a_digest_in_the_ele_parse_dockerfile_fails`، و`::test_a_truncated_digest_fails` | ✅ مقبول |
| `C‑12` (الجزء المحدّد): `--require-hashes` في صورة `ele-parse` | `services/ele_parse/Dockerfile:33`، و`requirements.txt` بثلاث تجزئات، وطابقتُها من PyPI، وتثبيتها الفعلي بـ`--require-hashes` نجح في تشغيلي لـ`scan.sh`. واختبارات: `test_supply_chain_check.py::test_the_ele_parse_requirements_pin_pymupdf_once_and_hash_every_line`، و`test_supply_chain_scan_pr_d.py::test_an_install_without_require_hashes_in_the_ele_parse_dockerfile_fails`، و`::test_a_requirement_without_a_hash_fails`، و`::test_a_malformed_hash_fails`، و`::test_a_range_instead_of_an_exact_pin_fails`، و`::test_the_shipped_files_say_the_hash_check_covers_pip_itself`. والبناء الحقيقي بتجزئة معبث بها يفشل (`test-plan.md` §3 س12) | ✅ مقبول |
| `C‑12`، باقيه: فحص الصورة المبنية | مؤجَّل بقرار المالك `INC1‑Q‑3` (أ) إلى خطوة «جاهزية الخادم» | ليس في هذا الـPR |
| `O19`، جزء `scan.sh` | `docs/stack-commands.md` §31‑ب: طابقتُ البيئتين، والأسوأ من الرمزين، وملفات `ci.yml` الأربعة، و26.3 والحدّ 26.2.2، و«لا استثناء ثغرات اليوم» | ✅ مقبول |
| `INC1‑AC‑1` .. `INC1‑AC‑14`، وحصص `INC1‑AC‑16` الأخرى | قُبلت في `PR‑A` و`PR‑B` و`PR‑C` (مدموجة) | ليس في هذا الـPR |

## المراجعات

- **`test-plan.md`:** **PASS** على `661b00e`، ثم **PASS** على `cc6e5a9`. QA‑1 وQA‑2 مغلقتان، وQA2‑1 أُغلقت في الجولة 2 (أعلاه).
- **`review.md`:** FAIL بالحاجبة #1 ثم **PASS** في إعادة المراجعة 1: #1..#8 مغلقة بدليل. وأُغلقت ر‑1 ور‑2 ور‑4 في الجولة 2، وصحّح المنسّق ر‑3 في الـLLD 0.7، وتحقّقتُ منها.
- **`security-review.md`:** FAIL بالحاجبة PD‑1 (عالية) ثم **PASS** في إعادة المراجعة 1: PD‑1 وPD‑2 مغلقتان بتجربة على صور مبنية. وأُغلقت PD‑11 وشقّا PD‑12 (1) و(2) في الجولة 2، وPD‑10 (1) بصف ث‑20 وتصحيح `INC1‑R‑20`.
- لا FAIL مفتوح في أيٍّ منها، ولا حاجبة، ولا ملاحظة عالية أو حرجة. والمفتوح أدناه صنّفه المراجع الأمني «لا» في عمود «حاجب؟».

## البنود المفتوحة للمالك

لا يحجب أيٌّ منها الدمج، لأن `PR‑D` لم يُحدثها: هو يضيّق الخطر في `app` و`ele-parse` ولا يوسّعه. لكن البندين الأولين يُعرضان عليك عند البوابة ②، وقرارهما قبل أول نشر على خادم.

| البند | الشدة | ما هو | متى يُحسم | من يقرّر |
|---|---|---|---|---|
| **`INC1‑R‑19` / PD‑3** (ث‑19) | متوسطة | صورة `app`، وهي أهم الصور، بلا بصمة في `FROM` المرحلتين، و`.[parsers]` بلا ملف قفل ولا تجزئات، و`release.yml` لا يشغّل `scan.sh`. فما يُفحص قد يخالف ما يُشحن | **قبل أول نشر على خادم، إلزامي.** ويُعرض التوقيت عند البوابة ②. والبصمة سطران فقط، فالأرخص تقديمها، وهي تغلق أيضاً قيد الوسم المحلي القديم (انظر «إعادة بناء الصور»). وتشغيل `scan.sh` في `release.yml` في خطوة «جاهزية الخادم» على الأكثر | المالك |
| **`INC1‑R‑20` / PD‑4 / PD‑10** (ث‑20) | متوسطة، وPD‑10 في أعلاها. **ترتفع إلى عالية أو حرجة عند أول عيب ذاكرة مبلوغ مثبت** | PyMuPDF يحلّل ملفات PDF المرفوعة داخل `worker-knowledge` بلا عزل، وتلك العملية تحمل اعتماد Vault و`app_rw`. والسقف `<2` يمنع إصلاحات MuPDF المعروفة، وMuPDF خارج الـSBOM، فعبارة «No known vulnerability.» في التقرير (`supply_chain_check.py:893`، وهي في `report.md:123` و`:153`) تعني فقط أن لا تنبيه مربوطاً باسم الحزمة، وتعديلها لم يتم | **قرار مكتوب قبل أول نشر على خادم، إلزامي**، ويُعرض عند البوابة ②. ويشمل `worker-knowledge` لا `ele-parse` وحدها. والترقية إلى 2.x وحدها لا تكفي: تحدّث المكتبة ولا تعزلها، وتُدخل في `app` ما أجّله `INC0‑Q‑9`. والبديل عزل التحليل في عملية أو حاوية بلا أسرار | المالك |
| **PD‑5** | منخفضة | أداتا الفحص (`cyclonedx-bom` و`pip-audit`) بلا تجزئات، وأداة مخترَقة تعطي نتيجة نظيفة | يفضَّل قبل أول نشر، مع ملف قفل `app`. ومعه إعادة بناء `build/supply-chain/tools` في كل تشغيل | المالك، مع `INC1‑R‑19` |
| **PD‑6** | منخفضة | البصمة تجمّد إصلاحات النظام وCPython أيضاً، ولا موعد لتحديثها | قبل أول نشر، مع بصمة `app`: سطر في `stack-commands.md` بتحديث دوري، شهرياً وقبل كل نشر | المالك، مع `INC1‑R‑19` |
| **PD‑7** (رفع `ci.yml`) | منخفضة | SBOM و`audit.json` الخامّان لـ`ele-parse` لا يُرفعان من CI، والمعلومة كاملة في `report.md` | مع خطوة «جاهزية الخادم» حين تُفتح `ci.yml`، وليس شرطاً أمنياً. ومعه اسم الخطوة «(runtime closure)» في `ci.yml:214`، وصارت تفحص بيئتين | المنفّذ وقتها |
| **عجلة `ensurepip`** (`pip-25.0.1-py3-none-any.whl`) | إعلامية (بلا درجة في المراجعة) | ملف في الصورتين، لا توزيعة مثبّتة. لا يناديه شيء، و`python -m venv` داخل الحاوية وحده يستعمله، ومن يملك ذلك يملك التنفيذ أصلاً | في «جاهزية الخادم»: إن علّمها فاحص الصور، فحذفها سطر واحد في الصورتين، ولا يكسر تشغيلاً | المنفّذ وقتها |
| **ث‑1** (صورة `embedding`) | متوسطة | بلا تجزئات، و`--extra-index-url` في `requirements`، وهو التعرّض نفسه الذي أغلقه PD‑1 في `app`. وخارج هذا الفرع، وقائم قبله | **قبل أول نشر على خادم، إلزامي**: ملف قفل بتجزئات للملفين | المالك |
| باقي `C‑12` وAGPL (ث‑18) | قرار موقّع للتوقيت | فحص الصور المبنية وصورة إصدار `ele-parse` في «جاهزية الخادم» (`INC1‑Q‑3` (أ)). وترخيص AGPL لـPyMuPDF | الفحص قبل أول نشر لـWireTwin. **وAGPL قبل أي توزيع للصورة** | المالك |

## ملاحظات غير حاجبة مقبولة

أقبل كل بند أدناه باسمه، ولا يُسقط أيٌّ منها معياراً في النطاق.

### من المراجعات

- **PD‑3 وPD‑4 وPD‑5 وPD‑6 وPD‑7 وPD‑10 وعجلة `ensurepip` وث‑1:** مقبولة للدمج، بالتوقيت الذي في الجدول أعلاه. **سبب القبول:** ليست مما أحدثه `PR‑D`، وكلها مسجّلة في `INC1‑R‑19` و`INC1‑R‑20` وجدول `ث`، فلا تضيع. وهذا القبول يخصّ الدمج، **ولا يعني قبولها للنشر على خادم**.
- **PD‑8** (إعلامية): `--label` يخرج بلا تهريب. ثابت في `scan.sh`، فلا مسار لمهاجم. بلا عمل.
- **PD‑9** (إعلامية): `report.md` بلا سطر نتيجة إجمالية في أوله، و`scan.sh` لا يفحص إصدار المفسّر. والوظيفة تحمرّ بالرمز على أي حال. بلا عمل.
- **PD‑12 (3)** (إعلامية): حذف `pip` يُفحص في نص `Dockerfile` لا في صورة مبنية، فنسخة جديدة في أساس مستقبلي لا تُرى. يغطيها فحص الصور في «جاهزية الخادم».
- **الانحرافات الستة في `review.md`:** `--label`، و`ci.yml` بلا تغيير، وجذر `aizzak-ele-parse` الصوري، واختلاف `pymupdf` ملاحظة لا فشل، و`pip-bootstrap.txt`، وحذف `ARG PIP_VERSION`. مقبولة كما قبلها المراجع، وO15 وO16 في الـLLD 0.7 يصفان ما بُني.
- **السطر المكرر `scan.sh:112`:** بلا ضرر. يُحذف عند أول تعديل للسكربت، ولا أشترطه.

### ملاحظاتي على الوثائق (لا تحجب)

- **PO‑1:** ث‑20 يقول «يحلّل الملفّات PDF المرفوعة داخل العملية نفسها في `worker-knowledge`». والأوضح: «يحلّل ملفّات PDF المرفوعة داخل عملية `worker-knowledge` نفسها». صياغة لا معنى.
- **PO‑2:** ث‑19 يكتب `pip‑25.0.1` بشرطة U+2011، وهو جزء اسم ملف خارجي لا معرّف مشروع، فلا يُعثر عليه ببحث (القاعدة نفسها في ر‑3). يُصحَّح إلى شرطة ASCII عند أول تعديل للوثيقة.
- **PO‑3:** `test-plan.md` §1 يذكر `test_a_pip_pin_that_differs_in_any_one_of_the_of_the_places_fails`، واسمه `test_a_pip_pin_that_differs_in_any_one_of_the_places_fails` (`test_supply_chain_scan_pr_d.py:109`)، و§6.2 يذكره صحيحاً. يصحّحه المنسّق.
- **PO‑4:** `status.md:127` ما زال يقول «`docs/lld-inc-1.md` 0.6». يصحّحه المنسّق قبل إيداع الملف، فهو غير مودع.

## شروط قبل الدمج (ليست إعادة عمل)

1. **إيداع الملفين غير المودعين مع هذا الـPR:** `docs/lld-inc-1.md` 0.7 (المرجع المعياري لهذا القبول، وفيه `INC1‑R‑19` و`INC1‑R‑20` و`O15` و`O16`)، و`status.md`. وإلا دخل `master` قبولٌ يستند إلى نص غير موجود.
2. **وظائف CI على طلب الدمج خضراء:** `quality`، و`integration`، و`supply-chain`. والفرع لم يُرفع بعد، فلم تجرِ أيٌّ منها. ووظيفة `supply-chain` هي أول تشغيل لـ`scan.sh` بالبيئتين على مشغّل نظيف، وببيئة `tools` مبنية من الصفر.
3. **العرض على المالك عند البوابة ②:** جدول «البنود المفتوحة» أعلاه، و`INC1‑R‑19` و`INC1‑R‑20` خاصة. والمطلوب للدمج أن يُعرضا، أما القرار فموعده قبل أول نشر على خادم.
4. لا هجرة ولا سرّ جديد ولا تغيير في `docker-compose.yml`، فالدمج لا يغيّر المكدّس الحيّ.

## إعادة بناء الصور: قرار بشري، والدمج لا يفعلها

- **قبل إعادة البناء لا يصل الإصلاح إلى الصور المحلية.** `aizzak/app:dev` و`aizzak/ele-parse:dev` مبنيتان قبل 3 أيام، وتبقيان على `pip` 25.0.1. وعند تشغيلي لم تكن على المضيف حاويات جارية (`docker ps -q`: صفر)، وهذه لقطة لحظة لا ضمان.
- **لا حاجة وظيفية عاجلة:** `PR‑D` لا يمسّ `src`، فالمكسب أمني فقط، ولا يوجد ما ينتظر هذا البناء.
- **`app` لم يعد فيها `pip` الأساس.** لا `/usr/local/bin/pip`، و`/usr/local/bin/python -m pip` يفشل. أما `pip` و`python -m pip` عبر `PATH` فيعملان بـ26.3.
- **هل يعتمد عليه شيء على المضيف؟ لم أجد شيئاً.** بحثتُ في `docker-compose*.yml`، و`deploy/`، و`Dockerfile`، و`.github/`، و`src/`، و`services/`، و`tests/`، و`docs/` (خارج `docs/delivery`) و`README.md` و`CLAUDE.md`، عن أي استدعاء لـ`pip` أو `ensurepip` أو `/usr/local/bin/pip` داخل الحاوية: لا شيء. ولا مهمة `cron` للمستخدم تذكر `pip` أو `docker`، ولا مؤقّت `systemd` للمشروع. وما يبقى مما يستعمل `pip` مستقل عن هذه الصورة: `.venv` على المضيف، وخطوات `ci.yml` و`release.yml` على المشغّل، و`services/embedding/Dockerfile:36` في صورته. ولا صورة أخرى تبدأ بـ`FROM` من `aizzak/app`.
- **التجربة الوحيدة على الصورة كاملة** عند QA (`test-plan.md` §6.2، `--network none`): `import app` و`gunicorn` و`alembic` و`curl` و`pg_dump` و`pytesseract` و`weasyprint` تعمل. و`app.ops.provision` توقف عند حلّ اسم القاعدة لغياب الشبكة، لا بسبب `pip`. فلم تُجرَّب الصورة الجديدة على مكدّس كامل قط، ويلزم فحص صحة الخدمات بعد البناء.
- **ما يتغيّر فعلاً عند إعادة بناء `app`، وهو أكثر من `pip`:**
  1. صورة واحدة لعشر خدمات: `migrate` و`app` و`worker-memory` و`worker-knowledge` و`worker-media` و`worker-electrical` و`outbox-relay` و`wal-shipper` و`backup` و`ops-scheduler`.
  2. `.[parsers]` بلا ملف قفل، فتُحلّ الحزم من جديد. وقد وجد المراجع الأمني 6 حزم غير `pip` مختلفة بين `aizzak/app:dev` وتجميد اليوم، منها `pandas` و`pydantic` و`SQLAlchemy` (`INC1‑R‑19`).
  3. الوسم المحلي `python:3.12-slim-bookworm` قديم بشهرين (3.12.14)، ومخزن شهاداته فيه جذر `emSign ECC Root CA - G3`، والبناء عبر Compose لا يمرّر `--pull`. **فتوصيتي عند البناء: `--pull`،** كما يفعل `build-push.sh:208`.
- **`ele-parse`:** تسحب أساسها بالبصمة نفسها، فلا تحتاج `--pull`. وهي خلف ملف `electrical` المعطّل، فلا أثر لها حتى تفعيله.

## الحكم النهائي

**مقبول.** كل معيار Must في النطاق ✅، وهي بنود `INC1‑AC‑15` الخمسة، وحصة `PR‑D` من `INC1‑AC‑16`، ومعها الجزء المحدّد من `C‑12`. ولكل بند دليل مسمّى: اختبار موجود في ملفه وناجح، أو مخرج من تشغيلي. والبوابات السبع خضراء بتشغيلي على `8168a22`، و`scan.sh` خرج بالرمز 0 والبيئتان PASS. وQA والمراجعتان PASS في آخر جولاتها، ولا حاجبة ولا ملاحظة عالية مفتوحة. والجولة 2 قرأتُ فرقها بنفسي وجرّبتُ تحوير اختباراتها، فهي لا تفعل إلا ما طُلب. ولا سطر تغيّر في `rag_agent` ولا `knowledge` ولا `pyproject.toml` ولا `.github` ولا `.importlinter`.

والحدّ الوحيد في أدلتي أن الصورة المبنية لم أقسها بنفسي، بل أعتمد على قياسين مستقلين على ملفات تحقّقتُ أنها لم تتغيّر. وهذا القبول قبول للدمج، لا للنشر: `INC1‑R‑19` و`INC1‑R‑20` وث‑1 شروط إلزامية قبل أول نشر على خادم، ويُعرض الأولان عند البوابة ②. وشروط الدمج: إيداع الـLLD 0.7 و`status.md`، وCI أخضر بوظائفه الثلاث، والعرض على المالك. وإعادة بناء `app` و`ele-parse` قرار بشري منفصل.

</div>
