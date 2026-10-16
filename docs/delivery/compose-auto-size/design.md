<div dir="rtl">

# التصميم التقني: حدودٌ تُحسب تلقائيّاً من موارد الجهاز (`compose-auto-size`)

> **المرجع:** [`requirements.md`](requirements.md) و[`stories.md`](stories.md)، وقرارات البوّابة ① في [`status.md`](status.md): Q‑1 هو الخيار (ج)، وQ‑2…Q‑13 كما أوصى المحلّل. لا يكرّر هذا الملفّ المتطلبات.

## 1. الملخص
الميزة طبقةُ نشرٍ فقط. لا تلمس `src/` ولا `docker-compose*.yml` ولا المخطّط.

- **المولِّد:** سكربتٌ جديد `deploy/auto_size.py`. يقرأ معالجات الجهاز وذاكرته، ثمّ يكتب أمرين:
  - كتلةً معلَّمة في `.env` فيها مفاتيح `large.env` الـ85.
  - ملفّ `deploy/postgres/sizes/auto.conf`.
- **الفحص:** خيارٌ جديد `--block FILE` في `deploy/resource-budget.sh`. المولِّد يستدعيه على ناتجه قبل أيّ كتابة (FR‑14)، والمشغّل يستدعيه على `.env` متى شاء.
- **مصدر الأرقام واحد:** الصيغة تقرأ `deploy/sizes/{small,large}.env` و`deploy/postgres/sizes/{small,large}.conf` و`docker-compose.yml` و`docker-compose.gpu.yml` كما هي. ولا تكرّر أيّ رقمٍ منها.
- **الأنماط المتّبعة:**
  - سكربت Python تحت `deploy/` تختبره الوحدة بـ`importlib`: ‏`deploy/ollama/pull_models.py` و`tests/unit/test_ollama_pull_models.py:46-51`.
  - قراءة الجهاز، ودمج ملفّ الكرت، وتصنيف الخدمات الدائمة: ‏`deploy/resource-budget.sh:80-88,136-142,178-199`.
  - شكل ملفّ الحجم (`# host:` ثمّ `KEY=VALUE`): ‏`tests/unit/support_compose.py:95-123`. **ما بين علامتَي الكتلة ملفُّ حجمٍ صالح**، فيقرؤه `read_size()` في الاختبارات ومحلّل `--size` في السكربت دون محلّلٍ جديد.
  - اختبارات [config] ببيئةٍ نظيفة: ‏`tests/unit/test_compose_sizes.py:198-228`.

## 2. الملفات
**المنفّذ لكلّ الملفّات: `backend-developer`.** المستودع واحد، والعمل متسلسل.

| المسار | إنشاء/تعديل | المسؤولية |
|---|---|---|
| `deploy/auto_size.py` | إنشاء، قابلٌ للتنفيذ (`chmod +x`) | المولِّد كلّه (§3‑أ…§3‑ح) |
| `deploy/resource-budget.sh` | تعديل | خيار `--block FILE` (§3‑ز). المسار الافتراضيّ ومسار `--size` لا يتغيّر مخرجهما بايتاً واحداً |
| `tests/unit/test_auto_size.py` | إنشاء | كلّ معايير US‑1…US‑6 الآليّة (§5) |
| `tests/unit/test_repo_hygiene.py` | تعديل | ‏`deploy/postgres/sizes/auto.conf` و`deploy/postgres/sizes/auto.conf.tmp1` في `_MUST_BE_IGNORED`، و`deploy/postgres/sizes/large.conf` في `_MUST_NOT_BE_IGNORED` |
| `.gitignore` | تعديل | ‏`/deploy/postgres/sizes/auto.conf*` مع تعليق: ملفٌّ مولَّد لكلّ جهاز (Q‑10) |
| `.env.example` | تعديل | سطرا تعليقٍ تحت «Per-size tuning» (`:114-115`). لا مفتاح جديد، ولا سطر يطابق علامة الكتلة (§3‑ج) |
| `docs/design/08-local-runbook.md` | تعديل | فقرةٌ في `§2‑ز` بعد فقرة «ميزانيّة خادم `ollama`»: الأوامر، وجدول الدورين، والرجوع، وQ‑13. **دون عنوانٍ جديد**، لأنّ اختبارات تقطع الأقسام عند كلّ عنوان |
| `docs/server-gpu-plan.md` | تعديل | ‏§3 القاعدة 1: نصّ الاستثناء (Q‑5). ‏§4: صفّ 3.5، و5.2 تعتمد على 3.5. ‏§5: «#### 3.5». ‏5.2 الخطوة 3: ‏`auto` افتراضيّ و`--size` لحجمٍ مسمّى (Q‑9) |
| `docs/server-gpu-summary.html` | تعديل | ٢٨ خطوة تصير ٢٩، وشريط التقدّم ٨/٧/١٤ مع `aria-label` والمفتاح، والمرحلة ٣ «٣ من ٥»، و`article` جديد لـ٣٫٥ بحالة `partial` |

**الاختبارات القائمة التي تقرأ ما يتغيّر** (بـgrep، تطبيقاً لدرس `test_compose_local_llm.py` في retro السابقة). كلّها تبقى دون تعديل، ما عدا `test_repo_hygiene`:

- **`resource-budget.sh`:**
  - `test_resource_budget.py` كلّه. المسار الافتراضيّ ومسار `--size` يبقيان بايتاً ببايت.
  - `:396` يفحص أنّ السكربت قابلٌ للتنفيذ.
  - `_run` يثبّت `COMPOSE_FILE` (`:255-266`)، ويُتّبع نمطه في الاختبارات الجديدة.
- **`.env.example`:**
  - `test_postgres_tuning.py:142`: سطر `POSTGRES_CONFIG_FILE=…` ونصّ وضع الإطفاء م‑8.
  - `test_gpu_compose_hygiene.py:272,442`: لا سرّ، ولا `COMPOSE_*` فعّال.
  - `test_compose_local_llm.py:178,238,260,364`.
  - ملفّاتٌ تمرّره `--env-file`: ‏`test_compose_sizes` و`test_compose_build_overlay` و`test_compose_gpu_overlay` و`test_embedding_cuda_image` و`test_monitoring_host_postgres_acceptance`.
  - `test_connection_budget` و`test_prebuilt_images_docs`.
  - تعليقٌ يبدأ بـ`#` لا يغيّر أيّاً منها.
- **`08-local-runbook.md`:**
  - `test_resource_budget.py:338,464`: سطور `resources.*` وجدول الأحجام.
  - `test_gpu_compose_hygiene.py:341-365`: جدول الخدمات في أوّل 60 سطراً، وترتيب `2‑ز` قبل `2‑ي`.
  - `test_connection_budget.py:856`: مقطع `§2‑ب`.
  - `test_monitoring_host_postgres_acceptance.py:111`، و`test_backup_wiring.py:434`.
- **`.gitignore`:** ‏`test_repo_hygiene.py:212-232`.
- **المفاتيح الـ85:** قيمها في المستودع لا تتغيّر، فلا يتغيّر أيّ اختبارٍ يقرؤها (قائمة compose-sizes §2).
- **`.env` المالك بعد أن يكتب فيه الكتلة:** كلّ اختبار وحدةٍ يشغّل `docker compose` يمرّر `--env-file` صراحةً، فلا يقرأ `.env`:
  - `test_compose_sizes.py:208-221`.
  - `test_compose_local_llm.py:68`، و`test_compose_gpu_overlay.py:57`.
  - `test_embedding_cuda_image.py:143`، و`test_monitoring_host_postgres_acceptance.py:205`.
  - `test_compose_build_overlay.py:167` يعمل على نسخةٍ في `tmp_path`.
  - فكتابة الكتلة على جهاز المالك لا تغيّر نتيجة أيّ اختبار.

## 3. العقود
### الواجهات البرمجية
لا شيء: لا مسار HTTP، ولا تغيير في `openapi.yaml`.

### (أ) اللغة ومكان التشغيل (ق‑1)
**Python في ملفٍّ واحد تحت `deploy/`، يعمل بـ`python3` النظام. لا Bash، ولا وحدة تحت `src/`.**

- **لماذا ليس Bash مع heredoc** كما في `resource-budget.sh`؟ فيه بحثٌ ثنائيّ، وتعديل ملفّ أسرارٍ تعديلاً ذرّيّاً، وكشف تعارض. وPython داخل heredoc:
  - لا يراه `ruff`.
  - لا تستورده الاختبارات، فكلّ حالةٍ تصير عمليّةً مستقلّة، وزمنُ AC‑1.4 وحده يتجاوز ميزانيّة AC‑6.3.
- **لماذا ليس تحت `src/`؟**
  - يعمل على المضيف: يقرأ `nproc` و`/proc/meminfo`، ويكتب `.env`. لا داخل صورة التطبيق.
  - الخادم و`install.sh` (5.2) ليس فيهما `pip install -e .`.
  - ويدخل حينها في عقود `.importlinter` دون فائدة.
- **البوّابات:**
  - `ruff format` و`ruff check` يغطّيانه، لأنّهما يعملان على `.`.
  - `PLR2004` يمنع الأرقام السحريّة، فكلّ ثابتٍ مسمّى (§3‑ح).
  - `PLR0912` و`PLR0915` يفرضان دوالّ صغيرة.
  - `mypy src` لا يغطّيه. الأنواع كاملة، ويُشغَّل `mypy --strict deploy/auto_size.py` استشاريّاً، لا بوّابة.
- **التبعيّات (NFR‑6):** المكتبة القياسيّة وPyYAML فقط، كـ`resource-budget.sh`.
  - Python ≥ 3.10، مع `from __future__ import annotations`.
  - لا `tomllib` ولا `typing.Self` ولا صيغة PEP 695، لأنّ خادم 5.2 قد يكون Ubuntu 22.04.
  - يُحمَّل YAML مرّةً واحدة بـ`getattr(yaml, "CSafeLoader", yaml.SafeLoader)`.

### (ب) سطر الأمر
```
deploy/auto_size.py [--role dev|server] [--dry-run] [--host-cpus N] [--host-memory-gb G]
                    [--mode cpu|gpu] [--profiles a,b] [--env-file PATH]
```
| الخيار | المعنى |
|---|---|
| `--role` | إن غاب: `# role:` من الكتلة الموجودة، وإلّا `server` (Q‑2، AC‑5.6). ويُطبع مصدره |
| `--host-cpus` و`--host-memory-gb` | عددٌ موجب (`^[0-9]+(\.[0-9]+)?$` و> 0)، وإلّا رمز 2. إن غابا: `len(os.sched_getaffinity(0))`، وهو ما يعطيه `nproc`. والذاكرة `MemTotal` kB ÷ 1048576 بصيغة `%.2f`، كما في `resource-budget.sh:80-88` |
| `--mode` و`--profiles` | يغلبان الاكتشاف. `--profiles ""` يعني لا profile |
| `--env-file` | الافتراضيّ `<repo>/.env`، و`<repo>` = `Path(__file__).resolve().parents[1]`. يُتبع الرابط الرمزيّ. **`auto.conf` يُكتب في `<مجلّد .env>/deploy/postgres/sizes/auto.conf`**، أي مجلّد مشروع Compose. فالاختبارات تكتب في `tmp_path` دون خيارٍ إضافيّ |
| `--dry-run` | الفحوص كلّها، ومنها `resource-budget.sh`. ثمّ يطبع الكتلة و`auto.conf`، ولا يكتب شيئاً (AC‑3.7) |

**اكتشاف الوضع والـprofiles** (FR‑8). الأولويّة كأولويّة Compose:
1. الخيار الصريح.
2. ثمّ `COMPOSE_FILE` و`COMPOSE_PROFILES` في بيئة العمليّة.
3. ثمّ آخر تعريفٍ لهما في `.env` خارج الكتلة.
4. ثمّ الافتراضيّ: `cpu` ولا profile.

القواعد:
- **الوضع** `gpu` إن كان اسم أحد عناصر `COMPOSE_FILE` هو `docker-compose.gpu.yml`. يُقسَم `COMPOSE_FILE` بـ`COMPOSE_PATH_SEPARATOR`، أو بـ`:` إن غاب.
- **الـprofiles** = تقاطع عناصر `COMPOSE_PROFILES` مع الـprofiles المعرَّفة في الملفّين. `*` تعني كلّها. وفي وضع `gpu` يُضاف `local-llm`، كما في `resource-budget.sh:169-171`.
- **لا تُطبع قيمةٌ من `.env`** (NFR‑5). يُطبع الوضع المشتقّ، وأسماء profiles معرَّفةٍ في Compose، والمصدر: اسم المتغيّر ومكانه، مثل `mode gpu (COMPOSE_FILE in .env)`.

**رموز الخروج.** لا يُكتب شيء قبل أن تنجح كلّ الفحوص.

| الرمز | متى | الرسالة (stderr). أرقامٌ وأسماء، لا قيم من `.env` |
|---|---|---|
| 0 | كُتب الناتج أو عُرض بالمعاينة. ويشمل ذلك تجاوز المعالج، وتجاوز الذاكرة في دور `dev` | ملخّصٌ على stdout (§3‑هـ) |
| 1 | دور `server` والجهاز أصغر من `small` في وضعه (FR‑5). أو رفض `resource-budget.sh` الناتج (FR‑14) | `auto-size: role server, mode gpu: the smallest limits need 14.22 GB, 13.50 GB allowed on a 15.00 GB host (10% margin): 0.72 GB short. Nothing was written. On a development machine use --role dev.` |
| 2 | خطأ استعمال (`argparse`): خيارٌ مجهول، أو رقمٌ غير صالح، أو دورٌ غير الدورين | رسالة الاستعمال، والملفّ لا يُمسّ |
| 3 | يرفض لمس `.env`: الملفّ غائب (`create it first: cp .env.example .env`)، أو مجلّد `deploy/postgres/sizes` غائب بجانبه، أو الكتلة معطوبة (بدايةٌ بلا نهاية، أو كتلتان، أو نهايةٌ قبل بداية)، أو مفتاحٌ متعارض (§3‑ج) | `auto-size: QDRANT_MEM is also defined outside the auto-size block (.env line 42); Compose keeps the last definition, so one would override the other silently. Remove that line and re-run. Nothing was written.` |

- **تحذيرٌ دون فشل:** إن كان أحد المفاتيح الـ85 معرَّفاً في بيئة العمليّة، يُذكر اسمه. فـCompose يقدّم البيئة على `.env`.

### (ج) الكتلة في `.env`
```
# >>> aizzak auto-size >>>
# Generated by deploy/auto_size.py. Do not edit: re-run it. Delete this block, both marker
# lines included, to return to the repository defaults (docs/design/08-local-runbook.md 2-z).
# role: dev
# host: cpus=10 memory_gb=12.67 mode=cpu
# profiles: wsl-ollama
# source: auto (deploy/auto_size.py, t=0.000)
POSTGRES_CONFIG_FILE=/etc/postgresql/sizes/auto.conf
WAL_ARCHIVE_INIT_CPUS=0.25
...                                   # the 85 keys of large.env, in large.env's order
# <<< aizzak auto-size <<<
```
- **التنسيق:**
  - المطابقة على السطر كاملاً بعد حذف `\r\n`.
  - سطر `# host:` بصيغة `support_compose._HOST` نفسها، والرقمان بـ`{:g}`. و`# profiles:` فارغٌ حين لا profile.
  - لا تاريخ ولا وقت (NFR‑1).
- **الكتابة:**
  - يُقرأ الملفّ ويُكتب بـ`encoding="utf-8", newline=""`، فتبقى نهايات الأسطر كما هي.
  - إن وُجدت كتلة: تُستبدل الأسطر من العلامة إلى العلامة في مكانها.
  - وإلّا تُلحق في آخر الملفّ. وإن لم ينتهِ الملفّ بـ`\n` يُضاف `\n` واحد قبلها، ويُعدّ جزءاً من الإدراج.
  - الرجوع: حذف الأسطر من العلامة إلى العلامة، فيعود الملفّ كما كان بايتاً ببايت.
- **التعارض (FR‑12، Q‑11):** كلّ سطرٍ خارج الكتلة يطابق `^\s*(export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=` واسمُه من المفاتيح الـ85 تعارضٌ، ورمز الخروج 3. **إلّا** إن اجتمع شرطان:
  - قيمته، بعد حذف المسافات وعلامتَي اقتباسٍ متطابقتين، تساوي قيمة المفتاح غير المعلَّق في `.env.example`. اليوم هذا `POSTGRES_CONFIG_FILE=/etc/postgresql/postgresql.conf` وحده.
  - **ويقع قبل الكتلة**، أو لا كتلة بعد (فستُلحق بعده). لأنّ Compose يأخذ آخر تعريف.
  - تُقارن القيم في الذاكرة ولا تُطبع.
- **الكتابة الذرّيّة:**
  - ملفٌّ مؤقّت في المجلّد نفسه، ثمّ `fsync`، ثمّ `chmod` بصلاحيّات الأصل (`S_IMODE`)، ثمّ `os.replace`.
  - إن كانت العمليّة root، يُعاد المالك والمجموعة بـ`os.chown`.
  - اسم المؤقّت `.env.bak.auto-size-XXXX`، فيغطّيه `.env.bak*` في `.gitignore` إن بقي بعد انهيار.
  - ويُحذف المؤقّت عند أيّ استثناء.
- **ترتيب الكتابة:** `auto.conf` أوّلاً بصلاحيّات `0644`، لأنّ مستخدم postgres داخل الحاوية يقرؤه. ثمّ `.env`.
  - `auto.conf` وحده لا يغيّر شيئاً حتّى تشير إليه الكتلة.
  - مؤقّته `auto.conf.XXXX` في المجلّد نفسه، فيغطّيه `/deploy/postgres/sizes/auto.conf*`.
- **تعليق `.env.example`** (لا يساوي أيٌّ من سطريه علامةً، ولا يبدأ بـ`KEY=`):
  ```
  # Or size this host automatically: deploy/auto_size.py appends a marked "aizzak auto-size"
  # block to .env (these keys, auto.conf for Postgres); see docs/design/08-local-runbook.md 2-z.
  ```

### (د) `auto.conf`
بشكل ملفّات الأحجام، ويمرّ بمحلّل `test_postgres_tuning._size_lines`:
```
# Generated by deploy/auto_size.py (role dev, host 10 vCPU / 12.67 GB, mode cpu). Not in git; re-run to change.
include '/etc/postgresql/postgresql.conf'
shared_buffers = 256MB
effective_cache_size = 4865MB
maintenance_work_mem = 128MB
work_mem = 16MB
```
- الوحدة `MB`، وتصير `GB` حين تقبل القسمة على 1024. فعلى 32/64 تطابق `large.conf` حرفيّاً (AC‑2.3).
- لا `max_connections`، ولا شيء في سطر الأمر (FR‑9).

### (هـ) الصيغة
الحساب بأعدادٍ صحيحة: الذاكرة بالـMiB، والمعالج بأجزاء المئة، و`t = k/1000`. فالناتج ثابتٌ بايتاً ببايت (NFR‑1).

- `s` و`l` قيمتا المفتاح في `small.env` و`large.env`.
- `host_cc = round(host_cpus × 100)`.

```
CPUS_i  = s + (clamp(host_cc - 800, 0, 2400) * (l - s)) // 2400   # 8 -> 32 vCPU, from the two headers
MEM_i   = s + (k * (l - s)) // 1000                                # MiB
  dev:    MEM_i = min(l, max(MEM_i, floor_i))                      # §3-و
REPMEM_i(k) = s + (2 * k * (l - s) + 1000) // 2000                 # replicas, rounded half up
REPCPU_i    = clamp(floor(host_cpus / 8), s, l)                    # one per 8 vCPU, at most large (FR-7)
REPLICAS_i  = min(REPMEM_i(k*), REPCPU_i)
REDIS_STREAM_MAXMEMORY = (REDIS_STREAM_MEM * 2) // 3  mb           # mem >= 1.5 x maxmemory (FR-10)
REDIS_CACHE_MAXMEMORY  = REDIS_CACHE_MEM - 768        mb           # mem >= maxmemory + 768m
POSTGRES_CONFIG_FILE   = /etc/postgresql/sizes/auto.conf
shared_buffers       = sb_s  + (k * (sb_l  - sb_s))  // 1000  MB  # 256MB -> 2GB, from small/large.conf
maintenance_work_mem = mwm_s + (k * (mwm_l - mwm_s)) // 1000  MB  # 128MB -> 1GB
effective_cache_size = (floor(host_gb * 1024) * 3) // 8       MB  # 37.5% of the host
work_mem             = 16MB
```
- **اختيار `k*`:**
  - `S(k)` = مجموع حدود الذاكرة للخدمات الدائمة، بعد ملفّ الوضع والـprofiles، **بنسخ `REPMEM(k)` لا بالنسخ المكتوبة**.
  - `k*` = أكبر `k` في 0…1000 يحقّق `S(k)/GiB ≤ host_gb × 0.9`، ويُوجد ببحثٍ ثنائيّ لأنّ `S` لا تنقص.
  - المقارنة بتعبير float نفسه في `resource-budget.sh:249-250`، فلا يختلف الاثنان عند الحدّ أبداً.
  - `server` و`S(0)` أكبر من المسموح: الرمز 1، والمطلوب هو `S(0)` (FR‑5).
  - `dev` و`S(0)` أكبر من المسموح: `k* = 0`، ويُطبع تحذير (Q‑3).
- **التقييم سريع:** يُحلَّل Compose مرّةً واحدة لكلّ وضع إلى صفوف (الخدمة، `replicas`، `cpus`، `memory` الخام). ثمّ يحلّ كلُّ تقييمٍ `${NAME:-default}` على الصفوف فقط، بالحلّ نفسه في `resource-budget.sh:124-133`.
  - **قيس على نموذج المعماريّ:** ‏0.1 ث لـ`resource-budget.sh` كاملاً. وتحميل `load_compose` في كلّ تقييمٍ أخذ 15 ث لـ20 جهازاً، فهو ممنوع.
- **لماذا التزايد مضمون (AC‑1.4، ق‑2):**
  - `k*` يعتمد على الذاكرة وحدها، لا على المعالج.
  - **ذاكرةٌ أكثر** ⇐ `k*` لا ينقص ⇐ لا ينقص `MEM` ولا `REPMEM` ولا `sb` ولا `mwm`.
  - **معالجٌ أكثر** ⇐ `k*` ثابت، و`REPCPU` و`CPUS` لا ينقصان.
  - والمجموع المكتوب ≤ `S(k*)` ≤ المسموح، لأنّ `REPLICAS ≤ REPMEM`.
  - **الثمن:** ذاكرةٌ محجوزة لنسخٍ لا يشغّلها المعالج تبقى غير مستعملة. مثلاً 12/24 بوضع المعالج: `k*=249`، لأنّ `k=250` تضيف نسخةً ثانية لـ`app` وأخرى لـ`embedding`.
- **وضع `gpu`:**
  - صفّ `embedding` يأتي من ملفّ الكرت (`replicas: 1`، `3g`)، فيُحسب نسخةً واحدة بـ3g تلقائيّاً.
  - المفتاح `EMBEDDING_REPLICAS` يُكتب بالقاعدة، والملفّ الإضافيّ يغلبه (AC‑1.5).
- **ما بين الحدّين:** على 32/64 وما فوقهما الناتج = `large` (عدا `POSTGRES_CONFIG_FILE`)، وتحت 8 معالجات المعالج = `small`. وما فوق `large` لم يُقَس، فلا يُتجاوز.
- **الملخّص على stdout:** الدور والوضع والـprofiles، ومصدر كلٍّ منها. ثمّ `t` والنسخ. ثمّ سطر `TOTAL` بصيغة `{:6.2f} vCPU  {:7.2f} GB`. ثمّ مخرج `resource-budget.sh` كما هو. ثمّ ما كُتب، و`next: docker compose up -d`. وإن لم يتغيّر حدّ postgres: `docker compose restart postgres`، لأنّه يقرأ `auto.conf` عند الإقلاع.

**جدولٌ مرجعيّ** من نموذج المعماريّ (مجلّد المسوّدة، لا المستودع). يثبّته `test_the_formula_matches_the_reference_table`:

| الجهاز (vCPU/GB) | gpu: ‏`k*` · vCPU · GB | cpu: ‏`k*` · vCPU · GB |
|---|---|---|
| 8/16 | 5 · 11.50 · 14.40 | 85 · 10.50 · 14.38 |
| 12/24 | 198 · 14.90 · 21.59 | 249 · 13.40 · 20.00 |
| 16/32 | 354 · 19.82 · 28.77 | 427 · 18.82 · 28.79 |
| 24/48 | 686 · 28.01 · 43.17 | 749 · 26.01 · 41.90 |
| 32/64 | 1000 · 37.60 · 57.53 (= `large`) | 1000 · 35.60 · 54.28 |
| 64/24 | 198 · 32.60 · 21.59 | 249 · 28.60 · 20.00 |
| 8/15 | **يُرفض:** 14.22 مطلوب، 13.50 مسموح | 59 · 10.50 · 13.48 |
| `dev` 10/12.67، cpu، `wsl-ollama` | — | 0 · 12.44 · **23.22** (1.83×)، و`server` يُرفض: 11.72 مقابل 11.40 |

### (و) أرضيّة التطوير (Q‑1 ج)
**المكان: ثابتٌ في `deploy/auto_size.py`** هو `DEV_PEAKS_MIB`، وفوقه تعليقٌ يذكر المصدر.
- **ما يُخزَّن هو الذروة المقيسة، لا الأرضيّة.** الأرضيّة تُحسب: `ceil(peak × 5/4 ÷ 256) × 256`، ثمّ `min(…, large)`. فالمراجِع يرى القياس، والاختبار يتحقّق من الحساب.
- **لماذا ليس ملفّ بيانات؟**
  - AC‑5.3 ينصّ على «جدول الأرضيّة في السكربت».
  - ملفٌّ ثانٍ يحتاج محلّلاً ومساراً.
  - ولا يجوز وضعه في `deploy/sizes/`، بسبب `test_compose_sizes.py:127`.
- **المصدر:** ‏`docs/delivery/monitoring-host-postgres/design.md:360-380`. ذروة `container_memory_working_set_bytes` في 15 يوماً على جهاز التطوير.
- **المفاتيح بلا ذروةٍ مقيسة** تأخذ قيمة الصيغة.
- القيم بالـMiB تُكتب نصّاً (`"7519.2"`) وتُحسب بـ`Fraction`، فلا خطأ تقريب.

| المفتاح | الذروة (MiB) | × 1.25 مقرّبة إلى 256 | `large` | الأرضيّة |
|---|---|---|---|---|
| `QDRANT_MEM` | 7519.2 | 9472m | 8g | **8g** |
| `POSTGRES_MEM` | 2877.2 | 3840m | 15g | **3840m** |
| `APP_MEM` | 1033.5 | 1536m | 2g | **1536m** |
| `LOKI_MEM` | 1020.8 | 1280m | 1g | **1g** |
| `MINIO_MEM` | 797.7 | 1g | 2g | **1g** |
| `GRAFANA_MEM` | 511.7 | 768m | 512m | **512m** |
| `PROMETHEUS_MEM` | 214.8 | 512m | 2g | **512m** |
| `OPS_SCHEDULER_MEM` | 183.2 | 256m | 1g | 256m (= `small`) |
| `REDIS_CACHE_MEM` | 21.0 | 256m | 1792m | أقلّ من `small` (896m) |
| `ALERT_SINK_MEM` | 31.1 | 256m | 32m | 32m |
| `OLLAMA_BRIDGE_MEM` | 5.9 | 256m | 256m | 256m |

- **`PROMETHEUS_MEM` 512m لم يكن في قائمة المحلّل.** ذروته 214.8 تحت رقم `small` (256m)، لكنّ 256m أقلّ من ذروته × 1.25 (268.5)، فقاعدة المالك تفرض 512m.
- **لذلك مجموع AC‑5.4 هو 23.22 GB و1.83×، لا «نحو 22.97 و1.81×».** الفرق 256m، والاختبار يثبّت الرقم الدقيق.

### (ز) `deploy/resource-budget.sh --block FILE`
```
deploy/resource-budget.sh [--host-cpus N] [--host-memory-gb G] [--with-profiles] [--size NAME | --block FILE]
```
- **ما يقرؤه:** الأسطر بين علامتَي الكتلة وحدها، بالعلامتين نفسيهما.
  - **لا يُبقي ولا يطبع أيّ سطرٍ خارجها.**
  - يحلّل ما بينهما بحلقة `--size` نفسها (`:155-165`)، ويقرأ معها `# role:` و`# profiles:`.
  - `mode=gpu` يدمج ملفّ الكرت ويفعّل `local-llm`، و`# profiles:` يُضاف إلى المفعَّل.
  - يطبع سطراً واحداً: `block <path>: role <r>, mode <m>, profiles <p|none>, written for <c> vCPU / <g> GB, source: <…>`.
- **الحكم:** الجهاز المقارَن هو هذا الجهاز، أو `--host-*`، كما في `--size`.
  - **`role server`:** قاعدة `--size` نفسها: هامش 10%، والتجاوز يعطي الرمز 1 ورسالة `MEMORY  OVER     block (role server): <t> GB of limits, <x> GB over the <a> GB allowed …`.
  - **`role dev`:** التجاوز يُطبع ولا يُفشل: `MEMORY  OVER     role dev: <t> GB of limits on a <h> GB host (<r>x; 90% = <a> GB). Warning only (owner decision Q-1).` والرمز 0.
- **رموز الخروج:**
  - **2:** ‏`--block` بلا قيمة أو مع `--size`، أو لا كتلة في الملفّ، أو كتلةٌ معطوبة، أو لا `# host:`، أو لا `# role:`.
  - **1:** ‏`NO LIMIT` كما اليوم. أو `POSTGRES_CONFIG_FILE=/etc/postgresql/sizes/<f>` والملفّ `<مجلّد FILE>/deploy/postgres/sizes/<f>` غائب: `Postgres would not start; re-run deploy/auto_size.py`. هذا يحمي من `git clean -X` ومن `.env` منسوخٍ إلى جهازٍ آخر.
- **كيف يستدعيه المولِّد:**
  - يكتب الكتلة وحدها في `<tmp>/.env`، و`auto.conf` المرشَّح في `<tmp>/deploy/postgres/sizes/`. المجلّد المؤقّت بصلاحيّات `0700`.
  - يشغّل `bash <repo>/deploy/resource-budget.sh --block <tmp>/.env --host-cpus … --host-memory-gb …`.
  - البيئة: `COMPOSE_FILE=<repo>/docker-compose.yml` و`GPU_COMPOSE_FILE=<repo>/docker-compose.gpu.yml`. **صراحةً**، لأنّ السكربت يقرأ `COMPOSE_FILE` مساراً واحداً، والمشغّل قد يحمل فيه قائمةً مفصولةً بنقطتين (`test_resource_budget.py:256-258`).
- **نصّ `--help`:** ‏`sed -n '2,11p'` يُمدّ ليشمل سطر `--block`. وسطر `usage:` يذكر `[--block FILE]`.

### (ح) التواقيع
```python
# deploy/auto_size.py
REPO_ROOT: Final[Path]                     # Path(__file__).resolve().parents[1]
BLOCK_START: Final = "# >>> aizzak auto-size >>>"
BLOCK_END: Final = "# <<< aizzak auto-size <<<"
CONTAINER_CONF: Final = "/etc/postgresql/sizes/auto.conf"
CONF_RELATIVE: Final = Path("deploy/postgres/sizes/auto.conf")   # under the .env's directory
STEPS: Final = 1000; MEMORY_SHARE: Final = 0.9; CPUS_PER_REPLICA: Final = 8
DEV_PEAK_MARGIN: Final = Fraction(5, 4); DEV_ROUND_MIB: Final = 256
REDIS_CACHE_OVERHEAD_MIB: Final = 768; ECS_SHARE: Final = Fraction(3, 8)
DEV_PEAKS_MIB: Final[Mapping[str, str]]   # §3-و, with its source comment
EXIT_OK, EXIT_REFUSED, EXIT_USAGE, EXIT_ENV = 0, 1, 2, 3
Role = Literal["server", "dev"]; Mode = Literal["cpu", "gpu"]

@dataclass(frozen=True)
class Host:
    cpus: str            # exactly what is printed and passed to resource-budget.sh
    memory_gb: str

@dataclass(frozen=True)
class Plan:
    role: Role; mode: Mode; profiles: tuple[str, ...]; host: Host; k: int
    values: dict[str, str]          # the 85 keys of large.env, in its order
    conf: str                       # auto.conf text
    total_cpus: float; total_memory_gb: float; allowed_gb: float

class Refused(Exception): ...       # exit 1; GB figures only
class EnvRefused(Exception): ...    # exit 3; key names, line numbers and paths only

def read_host(cpus: str | None, memory_gb: str | None) -> Host
def dev_floor_mib(key: str) -> int | None
def compute(host: Host, role: Role, mode: Mode, profiles: frozenset[str]) -> Plan  # pure; Refused
def render_block(plan: Plan) -> str
def render_conf(plan: Plan) -> str
def find_block(text: str) -> tuple[int, int] | None          # line indexes; EnvRefused if malformed
def block_role(text: str) -> Role | None
def splice(text: str, block: str, template: Mapping[str, str]) -> str           # EnvRefused
def detect(text: str, environ: Mapping[str, str], mode: Mode | None,
           profiles: str | None) -> tuple[Mode, frozenset[str], str]            # str = source label
def budget_check(plan: Plan) -> tuple[int, str]
def write_atomic(path: Path, text: str, mode: int) -> None
def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int
if __name__ == "__main__": sys.exit(main())
```
- `main()` يُرجع الرمز ولا يستدعي `sys.exit`، إلّا `argparse` عند الرمز 2.
- `environ=None` تعني `os.environ`. والاختبارات تمرّر `{}` أو قاموساً صريحاً.

## 4. البيانات
- **لا تغيير في المخطّط:** لا جداول، ولا Alembic، ولا RLS. **يمكن تخطّي مرحلة data-engineer.**
- **ما يتغيّر:** إعداد تشغيل Postgres، عبر ملفٍّ مولَّد خارج git يُختار بـ`POSTGRES_CONFIG_FILE` الموجود أصلاً. وهو متوافقٌ مع ما قبله:
  - دون كتلة: لا يتغيّر شيء (NFR‑2).
  - مجلّد الأحجام مربوطٌ للقراءة فقط من قبل (`docker-compose.yml:731`).
- **الرجوع:** حذف الكتلة من `.env`، ثمّ `docker compose up -d` و`docker compose restart postgres`. وحذف `auto.conf` اختياريّ. أو `git revert` للشيفرة.

## 5. خطة الاختبار المقترحة
- **كلّها في `tests/unit/test_auto_size.py`**، ما لم يُذكر غيره.
- **المسار المستعمل:**
  - الصيغة تُختبر بـ`compute()` داخل العمليّة.
  - سطر الأمر يُختبر بـ`main([... "--env-file", tmp/.env], environ={})` مع `capsys`.
  - شجرة `tmp_path` نسخةٌ من `.env.example`، ومعها `deploy/postgres/sizes/`.
  - عمليّةٌ فرعيّة حقيقيّة واحدة فقط (AC‑3.1)، لإثبات أنّ الملفّ ينفَّذ وحده.
- **حارسٌ آلي (autouse fixture):** يقارن `os.stat` لـ`<repo>/.env` و`<repo>/deploy/postgres/sizes/auto.conf` قبل كلّ اختبارٍ وبعده. يقارن الوجود و`st_mtime_ns` و`st_size`، **دون قراءة المحتوى**.
- **الزمن المقدَّر:**
  - نحو 30 تشغيلاً لـ`main()`، وفي كلٍّ منها `resource-budget.sh` بنحو 0.1 ث.
  - ومسحا AC‑1.4، أي 228 حلّاً بأعدادٍ صحيحة، في أقلّ من ثانية.
  - وتشغيل Compose واحد.
  - المجموع نحو 5 ث، والحدّ ≤ 15 ث.

| AC | المستوى | الاختبار |
|---|---|---|
| 1.1 | وحدة | `test_the_block_carries_exactly_the_keys_of_large`: المفاتيح وترتيبها = `read_size("large")`، 85 |
| 1.2 | وحدة | `test_every_host_fits_and_stays_between_small_and_large[mode-cpus-gb]`: 6 أجهزة × وضعان. المجموع ≤ 0.9 × الجهاز، وكلّ `_CPUS` و`_MEM` و`_MAXMEMORY` و`_REPLICAS` بين `small` و`large`، بـ`compose_bytes` و`redis_bytes` |
| 1.3 | وحدة | `test_the_reference_host_reproduces_large_exactly[mode]`: الفرق = `{POSTGRES_CONFIG_FILE}` فقط |
| 1.4 | وحدة | `test_no_value_shrinks_as_the_host_grows[mode]`: الذاكرة 16→128 بخطوة 2 والمعالج 32، ثمّ المعالج 8→64 والذاكرة 24 (حيث تحدّ الذاكرةُ النسخ) |
| 1.5 | وحدة | `test_replicas_follow_the_cpus_up_to_three`: 1/2/3/3/3 على 128 GB. وفي `gpu`، صفّ `embedding` المحلول نسخةٌ واحدة. ثمّ `test_a_many_core_small_memory_host_still_fits` على 64/24 |
| 1.6 | وحدة | `test_a_host_below_small_is_refused_naming_the_shortfall`: الرمز 1، و`gpu` و`14.22` و`13.50` و`0.72`، وsha256 الملفّ كما هو، ولا `auto.conf` |
| 1.7 | وحدة | `test_cpu_oversubscription_is_reported_not_refused`: ‏4/32، الرمز 0، و`OVERSUBSCRIBED` |
| 1.8 | وحدة | `test_nothing_runs_the_generator_on_its_own`: ‏`auto_size` غائبٌ عن `docker-compose*.yml` و`deploy/rolling-deploy.sh` وكلّ `Dockerfile` و`deploy/runpod/*` |
| حوافّ US‑1 | وحدة | `test_bad_arguments_are_usage_errors[abc,0,-1,role]`: الرمز 2 والملفّ كما هو. ثمّ `test_a_huge_host_gets_large` على 256/1024. ثمّ `test_gpu_mode_comes_from_compose_file`: ‏`COMPOSE_FILE` في `.env` المؤقّت، و`COMPOSE_PROFILES` فارغ. ثمّ `test_the_formula_matches_the_reference_table` (§3‑هـ) |
| 2.1، 2.2 | وحدة | `test_auto_conf_has_the_size_shape_and_fits[host]`: أجهزة 1.2 ومدخلات 5.1. ‏`include` أوّلاً ثمّ المفاتيح الأربعة. `ecs = floor(host_MiB×3/8)`. `sb + 4×mwm < POSTGRES_MEM`. `mwm ≤ 1gb`. `ecs <` الجهاز. `sb ≥ 256MB`. والحالة السالبة: `sb = 64GB` يعطي مشكلةً تذكر `auto` |
| 2.3 | وحدة | `test_the_reference_host_writes_large_conf_values` |
| 2.4 | وحدة | `test_redis_keeps_its_container_headroom[host]` |
| 2.5 | وحدة | `test_a_run_points_postgres_at_the_written_auto_conf`: قيمة الكتلة `CONTAINER_CONF`، والملفّ موجود بصلاحيّات `0644`. و`test_postgres_tuning.py::test_the_tuning_never_appears_as_a_command_line_argument` القائم يبقى أخضر |
| 2.6 | خادم | مؤجَّلٌ إلى 7.3 |
| 3.1 | وحدة (عمليّة فرعيّة) | `test_only_the_block_changes_mode_survives_and_nothing_leaks`: ‏`.env` بصلاحيّات `0600`، فيه `POSTGRES_SUPERUSER_PASSWORD=<قيمةٌ حارسة>`. كتلةٌ واحدة، وما خارجها كما كان، والصلاحيّات `0600`، والقيمة الحارسة غائبةٌ عن stdout وstderr |
| 3.2 | وحدة | `test_a_second_run_changes_nothing`: ‏sha256 |
| 3.3 | وحدة | `test_a_rerun_on_a_bigger_host_replaces_only_the_block`: ‏32 ثمّ 64 |
| 3.4 | وحدة | `test_a_conflicting_key_outside_the_block_is_refused_by_name[pgdata-conf, qdrant-mem, template-after-block]`: الرمز 3، والاسم مذكور، والقيمة غائبة، والملفّ كما هو |
| 3.5 | وحدة | `test_the_template_postgres_config_file_is_overridden_by_the_block` |
| 3.6 | config | `test_compose_resolves_the_block`، ويُتخطّى إن غاب Docker. على 16/32 cpu: حدود `postgres` و`qdrant` و`app` و`redis-stream`، ونسخ `app` (2) = الكتلة. وأمر `postgres` فيه `config_file=/etc/postgresql/sizes/auto.conf`، فآخر تعريفٍ يغلب. البيئة `PATH` و`HOME` و`DOCKER_*` فقط، كما في `test_compose_sizes.py:208-221` |
| 3.7 | وحدة | `test_dry_run_writes_nothing`: ‏sha256 كما هو، ولا `auto.conf`، والكتلة مطبوعة |
| حوافّ US‑3 | وحدة | `test_missing_env_is_refused_and_nothing_is_created`، و`test_a_malformed_block_is_refused[start-only,two,reversed]`، و`test_a_file_without_final_newline_gets_the_block_on_its_own_line`، و`test_a_limit_exported_in_the_shell_is_named_as_a_warning` |
| 4.1 | وحدة | `test_the_budget_script_agrees_with_the_generator[3 hosts]`: ‏`resource-budget.sh --block`، الرمز 0، وسطر `TOTAL` = سطر المولِّد. وقيمةٌ حارسة خارج الكتلة لا تظهر |
| 4.2 | وحدة | `test_the_budget_script_refuses_an_edited_block`: ‏`QDRANT_MEM` مرفوع، الرمز 1، و`over` |
| حوافّ US‑4 | وحدة | `test_the_budget_script_needs_one_well_formed_block` (الرمز 2)، و`test_the_budget_script_warns_on_a_dev_block` (الرمز 0 و`role dev`)، و`test_the_budget_script_names_a_missing_auto_conf` (الرمز 1) |
| 4.3 | وحدة + مراجعة | القائمة: `test_compose_sizes.py::test_the_size_directory_holds_exactly_the_three_sizes` و`::test_large_is_the_default_except_what_gpu_mode_needs`، و`test_resource_budget.py::test_the_runbook_ledger_carries_the_numbers_this_module_computes` (35.60 و57.28). ودليلٌ في الـPR: ‏`git diff --exit-code master -- deploy/sizes deploy/postgres/sizes docker-compose*.yml` |
| 5.1 | وحدة | `test_dev_role_on_the_development_host_writes_a_dev_block` |
| 5.2 | وحدة | `test_server_role_refuses_the_development_host`: ‏`11.72` و`11.40` و`0.32` |
| 5.3 | وحدة | `test_dev_floors_hold_and_never_pass_large`: جدول §3‑و. ومصدر `DEV_PEAKS_MIB` (`monitoring-host-postgres/design.md`) موجودٌ في نصّ السكربت |
| 5.4 | وحدة | `test_dev_overcommit_is_a_warning_with_the_ratio`: الرمز 0، و`23.22` و`12.67` و`1.83x` |
| 5.5 | وحدة | `test_dev_host_runs_one_replica_of_everything` |
| 5.6 | وحدة | `test_the_role_is_remembered_from_the_block` |
| حوافّ US‑5 | وحدة | `test_dev_role_follows_the_gpu_overlay`، و`test_dev_role_on_a_large_host_is_large_without_warning` (32/64) |
| 5.7 | مالك، يدويّ | بعد الدمج (§6، الخطوة 6) |
| 6.1 | وحدة | `test_the_runbook_and_env_example_document_the_block`: مقطع `§2‑ز` فيه `deploy/auto_size.py` و`--role dev` و`--role server` و`--dry-run` و`resource-budget.sh --block` و`aizzak auto-size`. وكلّ سطرٍ فيه `--env-file deploy/sizes` يذكر `--env-file .env` قبله (CR‑1). و`.env.example` لا يحوي سطراً يساوي علامة. ومفاتيحه غير المعلَّقة ∩ مفاتيح الكتلة = `{POSTGRES_CONFIG_FILE}` |
| 6.1 (`.gitignore`) | وحدة | الإضافات في `test_repo_hygiene.py` (§2) |
| 6.2 | مراجعة يدويّة | صفّ 3.5 وتفصيله ونصّ الاستثناء، و`server-gpu-summary.html` |
| 6.3 | بوّابات | البوّابات الخمس، وسطر خلاصة pytest ورمز خروجه من `${PIPESTATUS[0]}`. والزمن: `pytest tests/unit --durations=0` على `master` ثمّ على الفرع، والفرق ≤ 15 ث. و`test-rules.sh` لا يلزم، لأنّ التنبيهات لا تتغيّر |
| NFR‑4 | يدويّ | ‏`time deploy/auto_size.py --dry-run --env-file <tmp>` ≤ 5 ث، ويُلصق في الـPR |

## 6. ترتيب التنفيذ والتوازي
وكيلٌ واحد ومستودعٌ واحد، فلا توازي مفيد. بعد كلّ خطوة: البوّابات 1–5، ومنها `pytest tests/unit tests/architecture tests/eval` فقط.

0. على `master`: ‏`pytest tests/unit --durations=0`، ويُحفظ الزمن الكلّيّ لـAC‑6.3. ثمّ الفرع `feat/compose-auto-size`.
1. `compute()` والصيغة وأرضيّة التطوير، مع اختبارات US‑1 وUS‑2 وAC‑5.3…5.5 داخل العمليّة. ويُطابَق الجدول المرجعيّ.
2. `.env`: الاكتشاف، والكتلة، والتعارض، والكتابة الذرّيّة، وحفظ الدور. واختبارات US‑3 وAC‑5.1 وAC‑5.6.
3. `--block` في `resource-budget.sh`، واستدعاؤه من المولِّد (FR‑14). واختبارات US‑4، وAC‑1.6 وAC‑1.7 وAC‑5.2 وAC‑5.4 عبر `main()`.
4. `.gitignore` و`test_repo_hygiene`، ثمّ الوثائق الأربع. ثمّ AC‑6.1 وAC‑1.8 وAC‑3.6.
5. البوّابات كاملة، وقياس AC‑6.3 وNFR‑4. ثمّ دليل AC‑4.3 في الـPR.
6. **المالك، بعد الدمج:**
   - `deploy/auto_size.py --role dev --dry-run`، ثمّ الأمر نفسه دون `--dry-run`.
   - ثمّ `docker compose up -d` و`docker compose restart postgres`.
   - ثمّ 30 دقيقة استعمال: فهرسة ملفٍّ ومحادثة.
   - ثمّ `docker inspect -f '{{.Name}} {{.State.OOMKilled}}' $(docker compose ps -aq)` و`/health`.
   - هذا هو AC‑5.7.

## 7. المخاطر والقرارات
**لا بنود ⚠️:**
- لا تعديل في `docker-compose*.yml` (Q‑10 دون Compose).
- ولا تبعيّةٌ جديدة.
- ولا قرار أمنٍ أو أداءٍ لم يحسمه المالك في البوّابة ①.

**قرارات التصميم:**
- **ق‑1:** Python في `deploy/auto_size.py` (§3‑أ).
- **ق‑2: نسخٌ محجوزة بالذاكرة ومحدودة بالمعالج** (§3‑هـ).
  - هذا ما يجعل AC‑1.4 مضموناً في الاتّجاهين.
  - ثمنه ذاكرةٌ غير مستعملة حين يكون المعالج هو الحدّ. ويبقى المجموع تحت المسموح.
- **ق‑3: أرضيّة `PROMETHEUS_MEM` 512m** تطبيقاً حرفيّاً لقاعدة Q‑1 (§3‑و). فرقمُ AC‑5.4 الدقيق 23.22 GB و1.83×. **لمالك المنتج:** هذا ليس تغييراً في النطاق.
- **ق‑4: `auto.conf` بجانب `.env`** (`<مجلّد .env>/deploy/postgres/sizes/`)، لا بجانب السكربت. هو المجلّد الذي يربطه Compose فعلاً، والاختبارات تبقى في `tmp_path` دون خيارٍ إضافيّ.
- **ق‑5: `--block` يرفض `auto.conf` الغائب.** يحمي من Postgres لا يقلع بعد `git clean -X`، أو بعد نسخ `.env` إلى جهازٍ آخر.

**مخاطر غير حاجبة:**
- **خدماتٌ بلا ذروةٍ مقيسة على جهاز التطوير تنزل إلى `small`:**
  - `worker-knowledge` و`worker-media` بـ1g، و`worker-memory` بـ384m.
  - `redis-stream` بـ384m و`maxmemory 256mb`. هو `noeviction`، لكنّ المدخلة نحو 690 بايتاً (`capacity-status.md:1997`)، فـ256mb تتّسع لنحو 370 ألف مدخلة.
  - `shared_buffers` يصير 256MB.
  - **التخفيف:**
    - AC‑5.7.
    - والرجوع بحذف الكتلة.
    - واختياريّاً: يشغّل المالك استعلام PromQL في `monitoring-host-postgres/design.md:363-366` على بقيّة الخدمات قبل AC‑5.7، ويضيف صفوفاً إلى `DEV_PEAKS_MIB`. هذا تعديل بياناتٍ فقط.
- **أرضيّة Qdrant 8g فوق ذروته بـ9% فقط،** وهي من نافذة 15 يوماً (المحلّل، الخطر 1).
- **`MemTotal` على خادمٍ «بـ64 GB» أقلّ من 64 GiB،** فلن يطابق الناتج `large` تماماً هناك. هذا صحيح: الهامش يُحسب على ما يراه النظام. يُرى في 7.3.
- **`docker-compose.test.yml` و`docker-compose.wsl-gpu.yml` لا يدخلان الحساب** (خارج النطاق، كما في `resource-budget.sh`).
- **ملاحظةٌ لـ5.2، خارج النطاق:**
  - `install.sh` يستدعي `deploy/auto_size.py --role server`.
  - ويمرّر `--size` إلى `resource-budget.sh` فقط لحجمٍ مسمّى، وإلّا `--block .env`.
  - ويعامل الرمز 3 خطأَ إعداد، لا خطأَ حجم.

</div>
