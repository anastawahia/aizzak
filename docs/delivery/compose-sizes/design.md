<div dir="rtl">

# التصميم التقني: الحجم من ملفّ بدل أرقامٍ ثابتة (المرحلة 3 من خطّة خادم الكرت)

> المرجع: [`requirements.md`](requirements.md)، و[`stories.md`](stories.md)، وأجوبة البوّابة ① في [`status.md`](status.md). لا يكرّر هذا الملفّ المتطلبات.

## 1. الملخص
الميزة طبقة إعدادٍ للنشر فقط: `docker-compose.yml` و`deploy/` و`tests/unit/`. لا تلمس `src/` ولا المخطّط.
- كلّ حدٍّ في Compose يصير `${<SERVICE>_CPUS|_MEM|_REPLICAS:-<رقم اليوم>}`.
- ملفّ حجمٍ (`deploy/sizes/<size>.env`) يعطي قيماً أخرى بـ`--env-file`.
- اختبارات الوحدة ونسخةٌ مدمجة في `deploy/resource-budget.sh` تحلّ هذه المتغيّرات دون Docker.
- النمط المتّبع موجودٌ اليوم:
  - `_to_bytes` منسوخٌ في السكربت والاختبار، ويُبقيهما متّفقَين `test_the_script_and_this_module_agree` (`tests/unit/test_resource_budget.py:81-88,265-275`).
  - `_resolve` يحلّ `${VAR:-default}` من `.env.example` (`tests/unit/test_connection_budget.py:283-293`).
  - مساعدات الاختبار المشتركة تأخذ اسم `tests/unit/support_*.py`، وتُستورد بـ`from tests.unit.support_x import …`.
- **حسابي يؤكّد رقم المحلّل:** `large` بوضع الكرت = 57.28 − 3.75 (3×1280m) + 3 (1×3g من ملفّ الكرت) + 4 (`ollama`) = **60.53 GB**، والمسموح 57.60.
  - تطبيقاً لـQ‑2: `POSTGRES_MEM=15g` في `large.env`، فيصير المجموع **57.53**.
  - الافتراضيّ في Compose يبقى `18g`.

## 2. الملفات
**المنفّذ لكلّ الملفّات: `devops-engineer`** (وكيلٌ واحد ومستودعٌ واحد، والعمل متسلسل).

| المسار | إنشاء/تعديل | المسؤولية |
|---|---|---|
| `docker-compose.yml` | تعديل | 39 `cpus` + 39 `memory` + 4 `replicas` تصير متغيّرات (§3‑أ). `--maxmemory` لخادمَي Redis. تركيب `./deploy/postgres/sizes:/etc/postgresql/sizes:ro` بعد سطر `postgresql.conf` (`:729`) |
| `deploy/postgres/sizes/{large,medium,small}.conf` | إنشاء | `include` للأساس، ثمّ أربعة مفاتيح ذاكرة (§3‑د) |
| `deploy/sizes/{large,medium,small}.env` | إنشاء | 85 مفتاحاً لكلّ حجم، ورأسٌ يُقرأ آليّاً (§3‑ج، §3‑هـ) |
| `deploy/resource-budget.sh` | تعديل | حلّ `${…}`، وخيار `--size`، وهامش 10٪ مع `--size` (§3‑و) |
| `.env.example` | تعديل | سطرا تعليق قرب `POSTGRES_CONFIG_FILE` (`:115`) يشيران إلى `deploy/sizes/` و`/etc/postgresql/sizes/<size>.conf` (A‑6) |
| `tests/unit/support_compose.py` | إنشاء | الدالّة المشتركة وقارئ ملفّ الحجم (§3‑ب) |
| `tests/unit/test_compose_sizes.py` | إنشاء | AC‑1.1، AC‑1.2، AC‑1.5، AC‑3.1…3.6، وحوافّ US‑1 |
| `tests/unit/test_resource_budget.py` | تعديل | `_compose()` ← `load_compose()`، و`_to_bytes` ← `compose_bytes`. `_classify(compose=None, profiles=frozenset())`. وفيه AC‑4.1…4.5 |
| `tests/unit/test_postgres_tuning.py` | تعديل | اختبار حدّ الذاكرة (`:207-231`) ينتقل إلى `load_compose()` ومحلّل وحداتٍ (kB/MB/GB). وفيه AC‑2.1…2.4 |
| `tests/unit/test_redis_split.py` | تعديل | AC‑2.5 |
| `tests/unit/test_connection_budget.py` | تعديل | `_compose_topology(values: Mapping[str, str] \| None = None)` يقرأ `load_compose(values)` (`:334-350`). وفيه AC‑2.6 |
| `tests/unit/test_embedding_fleet.py` | تعديل | `_service()` يقرأ `load_compose(files=(path,))` (`:64`، `:83`) |
| `tests/unit/test_deploy_worker_default.py` | تعديل | `load_compose()` (`:188`، `:303`) |
| `tests/unit/test_metrics_topology.py` | تعديل | `_app_replicas()` يقرأ `load_compose()` (`:185-187`) |
| `tests/unit/test_rolling_deploy.py` | تعديل | حارس AC‑1.7. السكربت نفسه لا يتغيّر: `TARGET="${#OLD[@]}"` (`deploy/rolling-deploy.sh:201`) |
| `docs/design/08-local-runbook.md` | تعديل | في §2‑ز: جدول الأحجام (§3‑ز)، وسطر `--size` في كتلة الأوامر، وفقرةٌ عن ميزانيّة خادم `ollama` (FR‑12) |
| `docs/server-gpu-plan.md` | تعديل | صفوف اللوحة 3.1–3.4 (§4). وخليّة `small` في جدول §5 3.3: `mode=gpu`، والمراقبة محسوبة، وسقوفٌ مخفوضة للتجربة فقط (Q‑3، ق‑0) |

**قائمة نقل الاختبارات** (بعد grep على كلّ ملفٍّ يذكر Compose):
- **ستّة ملفّات تنكسر دون الدالّة.** فيها `int()` أو `float()` أو `removesuffix` على `${…}`:
  - `test_resource_budget`
  - `test_postgres_tuning`
  - `test_connection_budget`
  - `test_embedding_fleet`
  - `test_deploy_worker_default`
  - `test_metrics_topology`
- **ملفّان يتوسّعان دون أن ينكسرا:** `test_redis_split` و`test_rolling_deploy`.
- **ملفّاتٌ لا تتغيّر:**
  - `test_compose_local_llm` (`:137,155`): فحصه صدقيّ فقط.
  - `test_monitoring_host_postgres_acceptance` و`test_compose_gpu_overlay`: يقرآن ناتج `docker compose config` المحلول.
  - `test_gpu_compose_hygiene` (`:436`): يقرأ نصّ وثيقة.
  - `test_edge_capacity`: يقرأ `ulimits` وحدها.

## 3. العقود
### الواجهات البرمجية
لا شيء: لا مسار HTTP، ولا تغيير في `openapi.yaml`.

### التواقيع
**(أ) Compose.**
- الاسم: اسم الخدمة بحروفٍ كبيرة، و`-` تصير `_`، ثمّ اللاحقة `_CPUS` أو `_MEM` أو `_REPLICAS` أو `_MAXMEMORY`. مثل `WORKER_KNOWLEDGE_REPLICAS` و`REDIS_STREAM_EXPORTER_MEM` و`K6_CPUS`.
- لا تصادم مع أيّ متغيّر قائم: فحصتُه بـgrep في `src` و`deploy` و`services` و`tests` و`.env*`. ولا توجد خدمةٌ بـ`env_file:`، فهذه المتغيّرات لا تدخل أيّ حاوية.
- الاقتباس يبقى كما هو اليوم:

```yaml
cpus: "${POSTGRES_CPUS:-6.0}"        # quoted, as today
memory: ${POSTGRES_MEM:-18g}
replicas: ${APP_REPLICAS:-3}
"--maxmemory", "${REDIS_STREAM_MAXMEMORY:-2gb}",   # redis-cache: ${REDIS_CACHE_MAXMEMORY:-1gb}
```
- تُستبدل المفاتيح تحت `deploy.resources.limits` و`deploy.replicas` وحدها، لا التعليقات (مثل `:43` و`:2039`).
- يُسمح بسكربت استبدالٍ لمرّةٍ واحدة في مجلّد المسوّدة، ولا يُلتزم به.

**(ب) `tests/unit/support_compose.py`:**
```python
REPO_ROOT: Final[Path]; COMPOSE: Final[Path]; GPU_OVERLAY: Final[Path]; SIZES_DIR: Final[Path]
SIZES: Final[tuple[str, ...]] = ("small", "medium", "large")          # ascending (AC-3.5)
MODE_OVERLAYS: Final[Mapping[str, tuple[Path, ...]]] = {"cpu": (), "gpu": (GPU_OVERLAY,)}
MODE_PROFILES: Final[Mapping[str, frozenset[str]]] = {"cpu": frozenset(), "gpu": frozenset({"local-llm"})}
SUFFIXES: Final = ("CPUS", "MEM", "REPLICAS", "MAXMEMORY")

class UnsupportedInterpolation(ValueError): ...      # message names the variable (or the raw text)

def var_name(service: str, suffix: str) -> str        # "worker-knowledge","REPLICAS" -> "WORKER_KNOWLEDGE_REPLICAS"
def resolve(raw: object, values: Mapping[str, str] | None = None) -> str
    # no "$" -> str(raw). Exact full match of r"\$\{([A-Z][A-Z0-9_]*):-([^}$]*)\}" ->
    # values.get(NAME) or DEFAULT (":-": empty == unset). Anything else with "$"
    # (${X}, ${X?}, ${X:?m}, two vars, text around) -> UnsupportedInterpolation.
    # Never reads os.environ (NFR-6).
def load_compose(values: Mapping[str, str] | None = None, *,
                 files: Sequence[Path] = (COMPOSE,)) -> dict[str, Any]
    # yaml.safe_load each file, deep-merge in order (mappings merge, later wins;
    # lists/scalars replace -- exact for `deploy`, which is all callers rely on),
    # then resolve() in place, as str, ONLY deploy.replicas and
    # deploy.resources.limits.{cpus,memory} of every service. Commands stay raw.
@dataclass(frozen=True)
class SizeFile:
    name: str; path: Path; host_cpus: float; host_memory_gb: float
    mode: Literal["cpu", "gpu"]; source: str; values: Mapping[str, str]
def read_size(name_or_path: str | Path) -> SizeFile
    # name -> SIZES_DIR/<name>.env. Header lines required, else AssertionError naming the file.
    # KEY=VALUE only; duplicate key -> AssertionError.
def size_compose(size: SizeFile) -> dict[str, Any]   # load_compose(size.values, files=(COMPOSE, *MODE_OVERLAYS[size.mode]))
def compose_bytes(raw: str) -> int   # moved from test_resource_budget._to_bytes, unchanged
def redis_bytes(raw: str) -> int     # only r"[1-9][0-9]*(mb|gb)", 1024-based ("1g" is 10**9 in Redis: refused)
```

**(ج) صيغة `deploy/sizes/<size>.env`** (للفحص بـ`--env-file`، ولنسخها إلى `.env` في 5.2):
```
# AIZZAK size: medium
# host: cpus=16 memory_gb=32 mode=gpu
# source: derived from large, measured in server-gpu-plan 7.3
# Overlays win over these values: docker-compose.gpu.yml pins embedding to 1 replica / 3g,
# docker-compose.test.yml pins postgres cpus. POSTGRES_CONFIG_FILE pointed at the data
# directory's own postgresql.conf (m-8) switches this size's Postgres tuning off too.
POSTGRES_CONFIG_FILE=/etc/postgresql/sizes/medium.conf
POSTGRES_CPUS=3.0
POSTGRES_MEM=4g
...
```
- المفاتيح بترتيب الخدمات في Compose، وبلا تعليقٍ في آخر السطر.
- في كلّ ملفّ 85 مفتاحاً بالضبط: 39×2 + 4 `_REPLICAS` + 2 `_MAXMEMORY` + `POSTGRES_CONFIG_FILE`.
- سطر `# source:` يبدأ بـ`measured` أو بـ`derived`.
- قيم `large`: كلّ القيم الافتراضيّة، إلّا `POSTGRES_MEM=15g` (Q‑2) و`POSTGRES_CONFIG_FILE=/etc/postgresql/sizes/large.conf`.
- رأس `large`: `# source: measured (POSTGRES_MEM 15g: Q-2 cut for GPU mode)`.

**(د) `deploy/postgres/sizes/<size>.conf`:**
- تعليق رأس.
- ثمّ أوّل سطرٍ غير تعليق: `include '/etc/postgresql/postgresql.conf'`.
- ثمّ هذه المفاتيح الأربعة بالضبط (`max_connections` غائب، فيبقى 300):

| المفتاح | `large` | `medium` | `small` |
|---|---|---|---|
| `shared_buffers` | 2GB | 1GB | 256MB |
| `effective_cache_size` | 24GB | 12GB | 6GB |
| `maintenance_work_mem` | 1GB | 512MB | 128MB |
| `work_mem` | 16MB | 16MB | 16MB |
| sb + 4×mwm مقابل `POSTGRES_MEM` | 6 < 15g | 3 < 4g | 0.75 < 1536m |

**(هـ) أرقام الأحجام** (`cpus/mem[×replicas]`):
- خدمات المرّة الواحدة والخدمات خلف profile (عدا `ollama`) تأخذ قيم `large` في كلّ حجم، لأنّها لا تُعدّ. وهي `wal-archive-init` و`vault-bootstrap` و`minio-bootstrap` و`migrate` و`nginx-certs` و`ollama-bridge` و`ollama-pull` و`backup` و`cadvisor` و`k6`.
- `EMBEDDING_CPUS=1.0` في كلّ حجم، لأنّ `OMP_NUM_THREADS` و`EMB_TORCH_THREADS` حرفيّان ويطابقان `cpus` (`test_embedding_fleet.py:67-95`).

| الخدمة | `large` (= الافتراضيّ) | `medium` | `small` |
|---|---|---|---|
| postgres | 6.0/**15g** (افتراضيّ 18g) | 3.0/4g | 2.0/**1536m** |
| pgbouncer | 1.0/256m | 0.5/256m | 0.25/128m |
| redis-stream (maxmemory) | 1.0/3g (2gb) | 0.5/1536m (1gb) | 0.25/**384m** (**256mb**) |
| redis-cache (maxmemory) | 1.0/1792m (1gb) | 0.5/896m (128mb) | 0.25/896m (128mb) |
| minio | 1.0/2g | 0.5/1g | 0.5/**512m** |
| qdrant | 3.0/8g | 1.5/3g | 1.0/**768m** |
| embedding | 1.0/1280m×3 | 1.0/1280m×2 | 1.0/1280m×1 |
| embedding-lb | 0.5/128m | 0.25/128m | 0.25/64m |
| vault | 1.0/512m | 0.5/256m | 0.25/256m |
| ollama | 4.0/4g | 2.0/3g | 1.0/**1g** |
| app | 2.0/2g×3 | 1.0/1536m×2 | 1.0/**1g**×1 |
| worker-memory | 1.0/1g | 0.5/1g | 0.25/**384m** |
| worker-knowledge | 1.0/2g×2 | 1.0/2g×1 | 0.5/**1g**×1 |
| worker-media | 1.0/2g | 1.0/1536m | 0.5/1g |
| outbox-relay | 1.0/512m×1 | 0.5/512m×1 | 0.25/256m×1 |
| wal-shipper | 0.5/256m | 0.25/256m | 0.1/128m |
| ops-scheduler | 1.0/1g | 0.5/512m | 0.25/**256m** |
| nginx | 2.0/512m | 1.0/256m | 0.5/128m |
| prometheus | 1.0/2g | 0.5/768m | 0.25/**256m** |
| grafana | 0.25/512m | 0.25/512m | 0.1/384m |
| alertmanager | 0.25/64m | 0.25/64m | 0.1/64m |
| alert-sink | 0.1/32m | 0.1/32m | 0.1/32m |
| pgbouncer-, redis-stream-, redis-cache-, postgres-exporter | 0.25/128m | 0.1/64m | 0.1/64m |
| node-exporter | 0.25/64m | 0.1/64m | 0.1/64m |
| loki | 0.5/1g | 0.5/512m | 0.25/384m |
| alloy | 0.25/512m | 0.25/256m | 0.1/256m |
| **رأس الملفّ** | `cpus=32 memory_gb=64 mode=gpu` | `cpus=16 memory_gb=32 mode=gpu` | `cpus=8 memory_gb=16 mode=gpu` (ق‑0) |
| **المجموع الدائم في وضع الرأس** | **37.60 vCPU / 57.53 GB** من 57.60 | **19.35 / 28.41** من 28.80 | **11.50 / 14.22** من 14.40 |

- قاعدة Redis (FR‑13) محقَّقة في كلّ حجم: `stream` = 1.5× بالضبط، و`cache` = `maxmemory` + 768m بالضبط.
- المراقبة محسوبةٌ في الأحجام الثلاثة (Q‑3).
- وضع الكرت = الملفّ الأساسيّ + `docker-compose.gpu.yml`، مع profile `local-llm`.
- ثمّ تُصنَّف كلّ خدمةٍ بمفتاح `restart`: `ollama` تُعدّ دائمة، و`ollama-pull` مرّةً واحدة فلا تُعدّ.

**(و) `deploy/resource-budget.sh`:**
```
deploy/resource-budget.sh [--host-cpus N] [--host-memory-gb G] [--with-profiles] [--size NAME]
env: COMPOSE_FILE (docker-compose.yml) · GPU_COMPOSE_FILE (docker-compose.gpu.yml) · SIZES_DIR (deploy/sizes)
exit: 0 fits · 1 memory over / NO LIMIT · 2 usage (unknown flag, --size without a value or followed
      by "--…", unknown size -> stderr "unknown size 'huge'; available: large medium small",
      size file without a valid "# host:" line, unsupported interpolation)
```
- **الحلّ:** نسخةٌ من `resolve()` داخل Python المدمج. تُطبَّق على `replicas` و`cpus` و`memory` وحدها.
  - القيم من ملفّ الحجم إن وُجد `--size`، وإلّا فالافتراضيّ.
  - لا يقرأ السكربت `os.environ` ولا `.env`، ولا يستدعي `docker compose`.
- **مع `--size`:**
  - يقرأ رأس ملفّ الحجم.
  - في وضع `gpu` يدمج `deploy` من `GPU_COMPOSE_FILE`، ويفعّل `local-llm`.
  - يطبع سطراً واحداً: `size <name> (<path>): mode <mode>, written for <c> vCPU / <g> GB, source: <…>`.
  - الخادم المقارَن يبقى كما هو اليوم: هذا الجهاز، أو `--host-*`.
  - حكم الذاكرة: المجموع ≤ ذاكرة الخادم × 0.9 (Q‑4).
  - عند التجاوز يطبع `MEMORY  OVER     size <name>: <t> GB of limits, <t-a> GB over the <a> GB allowed on a <h> GB host (10% margin)…` ويخرج بـ1.
- **دون `--size`:** المخرج ورمز الخروج كما على `master` (AC‑1.4). وسطر `TOTAL` بالصيغة نفسها `{:6.2f} vCPU  {:7.2f} GB`.
- نصّ `--help` يُحدَّث لمدى أسطر الرأس الجديد.

**(ز) جدول `08 §2‑ز`.** صفوفٌ يقرؤها الاختبار بتعبيرٍ نمطيّ:
```
| الحجم | الوضع | الخادم (vCPU / GB) | vCPU | GB | المسموح (−10٪) |
| `large` | gpu | 32 / 64 | 37.60 | 57.53 | 57.60 |
```
كتلة `resources.*` الحاليّة لا تتغيّر: 35.60 و57.28 (NFR‑1).

## 4. البيانات
- **المخطّط لا يتغيّر:** لا جداول، ولا Alembic، ولا RLS.
- **ما يتغيّر إعداد تشغيل Postgres وحده، وهو متوافقٌ مع ما قبله:**
  - بلا متغيّرات حجم: `POSTGRES_CONFIG_FILE` يبقى على الملفّ الأساسيّ.
  - تركيب المجلّد الجديد للقراءة فقط، ولا يغيّر ما يُقرأ.
- **الرجوع:** `git revert`. أو إزالة متغيّرات الحجم من `.env`، فتعود القيم الافتراضيّة.

## 5. خطة الاختبار المقترحة
- كلّها [وحدة] أو [config]، ولا حاوية تعمل (Q‑6).
- اختبارات [config] تمرّ عبر `docker compose … config --format json` ببيئةٍ نظيفة: `PATH` و`HOME` و`DOCKER_*` فقط.
  - تمرّر `--env-file .env.example` صراحةً، ثمّ ملفّ الحجم.
  - وتأخذ `--profile '*'`، وتتخطّى إن غاب Docker. نمطها `_needs_compose` في `test_compose_build_overlay.py:35-45`.
  - كلّ تشغيلٍ نحو 0.1 ث (قِيس)، والمجموع 11 تشغيلاً.

| AC | المستوى | المكان |
|---|---|---|
| 1.1 | وحدة | `test_compose_sizes.py::test_every_limit_and_replica_count_is_a_named_variable`. الصيغة والاسم، والعدد 39/39/4، والقيمة الافتراضيّة بنحو AC‑3.3 |
| 1.2 | وحدة | `test_compose_sizes.py::test_resolve_*`. يغطّي: غائب، ومُعطى، وفارغ ← افتراضيّ، و`monkeypatch.setenv` لا يؤثّر، و`${X}` و`${X?}` و`${X:?m}` ومتغيّران ← خطأ يسمّي `X`، ونصٌّ حرفيّ يمرّ كما هو |
| 1.3 | وحدة | الملفّات الستّة المنقولة تنجح بأرقامها كما هي، ومنها اختبار الدفتر 35.60/57.28 |
| 1.4 | وحدة | `test_the_script_and_this_module_agree` و`…passes_on_the_reference_host…` الحاليّان |
| 1.5 | config | دائم: `test_compose_resolves_limits_as_the_helper_does[default,small,medium,large × cpu,gpu]` يقارن `cpus` و`memory` بالبايت و`replicas` لكلّ خدمة.<br>مرّةً واحدة في البناء، ودليله في الـPR: بصمتا `{service: deploy}` متطابقتان بين `git show master:docker-compose.yml` والفرع. الأمر: `docker compose --project-directory . -f <file> --env-file .env.example --profile '*' config --format json` ثمّ `python3` يستخرج `deploy` ثمّ `sha256sum` |
| 1.6 | مالك | الأمر نفسه دون `--env-file` (فيُقرأ `.env`)، و`2>/dev/null`. لا يُطبع غير البصمتين |
| 1.7 | وحدة | `test_rolling_deploy.py::test_the_target_count_comes_from_running_containers`. الأسطر غير المعلّقة بلا `replicas:` ولا `safe_load`، و`TARGET="${#OLD[@]}"` موجود |
| حوافّ US‑1 | config | `test_compose_sizes.py::test_empty_falls_back_malformed_is_refused`. `APP_REPLICAS=` يعطي 3، و`APP_REPLICAS=abc` يعطي رمزاً ≠ 0، و`POSTGRES_MEM=18x` يعطي رمزاً ≠ 0 |
| 2.1 | وحدة | `test_postgres_tuning.py::test_each_size_file_includes_the_base_first_and_sets_only_memory` |
| 2.2 | وحدة | `…::test_the_sizes_directory_is_mounted_read_only`، والاختباران الحاليّان `:125`، `:157` |
| 2.3 | وحدة | `…::test_each_size_fits_its_postgres_container[size]`. والحالة السالبة: `tmp_path` فيه `.conf` بـ`shared_buffers` مرفوع يفشل ويسمّي الحجم |
| 2.4 | وحدة | `…::test_each_size_stays_inside_its_host_and_shm[size]`. `effective_cache_size` < ذاكرة الرأس، و`maintenance_work_mem` **≤** `shm_size` (ق‑3) |
| 2.5 | وحدة | `test_redis_split.py::test_maxmemory_is_a_variable_with_todays_default`، و`…::test_each_size_keeps_container_headroom[default+sizes]` |
| 2.6 | وحدة | `test_connection_budget.py::test_every_size_stays_inside_the_connection_ceilings[size]`. سقف عملاء الوسيط، والخلفيّات ≤ `max_connections`، و300 واحدٌ في كلّ الأحجام |
| 2.7 | — | مؤجَّل إلى 7.3 (Q‑6) |
| 3.1–3.5 | وحدة | `test_compose_sizes.py`: الرأس، والمفاتيح = المجموعة المشتقّة من Compose بالضبط، والصيغ (`_CPUS` > 0، و`_MEM` `^[1-9]\d*[mg]$`، و`_MAXMEMORY` Redis، و`_REPLICAS` ≥ 1)، و`large` = الافتراضيّ عدا `{POSTGRES_MEM, POSTGRES_CONFIG_FILE}`، و`small ≤ medium ≤ large`. ومسار `POSTGRES_CONFIG_FILE` يسمّي ملفّاً موجوداً |
| 3.6 | config | جزءٌ من تشغيلات AC‑1.5: رمز 0، ومع ملفّ الكرت `embedding` نسخةٌ واحدة و3221225472 بايت |
| 3.7 | — | مؤجَّل إلى 7.3 |
| 4.1 | وحدة | `test_resource_budget.py::test_the_script_and_this_module_agree_per_size[size]`. المضيف من الرأس |
| 4.2 | وحدة | `…::test_the_script_refuses_an_unknown_or_missing_size`. `huge`، و`--size` وحده، و`--size --host-cpus 1`: الرمز 2 والأحجام مسمّاة |
| 4.3 | وحدة | `…::test_every_size_fits_the_host_its_header_names[size]`. `_MEMORY_HEADROOM_GB` يبقى 6.4، ويُفحص أنّه = 64 × 0.10 |
| 4.4 | وحدة | `…::test_a_size_over_its_host_fails_naming_it`. نسخة `medium` في `tmp_path`، و`QDRANT_MEM` مرفوعٌ حتّى المسموح + 0.1 GB. يفشل الفحص في الوحدة، ويفشل السكربت بـ`SIZES_DIR=tmp_path` برمز 1، وفي الرسالة الاسم و`0.10 GB over` |
| 4.5 | وحدة | `…::test_the_runbook_size_table_carries_the_numbers_this_module_computes` |
| NFR‑3 | — | `pytest tests/unit --durations=0` قبل وبعد. الزيادة ≤ 15 ث |
| NFR‑5 | — | البوّابات الخمس. `test-rules.sh` لا يلزم، لأنّ التنبيهات لا تتغيّر |

## 6. ترتيب التنفيذ والتوازي
وكيلٌ واحد، والخطوات متسلسلة. كلّ خطوةٍ تنتهي والبوّابات 1–5 خضراء، ويُشغَّل منها `pytest tests/unit tests/architecture tests/eval` فقط.
1. `support_compose.py` واختبارات AC‑1.2. ثمّ نقل الملفّات الستّة إلى الدالّة. تبقى خضراء على `master`، لأنّ النصّ الحرفيّ يمرّ كما هو.
2. متغيّرات `docker-compose.yml` (§3‑أ). وفي الخطوة نفسها حلّ `${…}` في `resource-budget.sh` دون `--size`، واختبار AC‑1.1 وحوافّ US‑1، وفحص AC‑1.5 لمرّةٍ واحدة.
3. ملفّات `deploy/postgres/sizes/*.conf` وتركيبها، وAC‑2.1…2.4. ثمّ `--maxmemory` وAC‑2.5.
4. ملفّات `deploy/sizes/*.env`، وAC‑3.x وتشغيلات [config]، وAC‑2.6.
5. `--size` في السكربت وAC‑4.x، وجدول `08 §2‑ز`، ولوحة الخطّة، و`.env.example`، وقياس NFR‑3.
6. AC‑1.6 على جهاز المالك. يشغّله المالك، أو الوكيل بشرط ألّا يُطبع غير البصمتين.

لا توازي مفيد: الخطوات 3–5 تقرأ ناتج الخطوة 2.

## 7. المخاطر والقرارات
- **ق‑0 (قرار المالك، 2026‑10‑16، جواب م‑1 بالخيار (ب)): `small` بوضع `gpu`، للتجربة فقط، والمالك يقبل خطر `OOMKilled`.**
  - بأرقام `small` السابقة كان المجموع في وضع الكرت 17.97 GB مقابل 14.40. خُفّض **3.75 GB** (القيم بالخطّ العريض في §3‑هـ):
    - `ollama` 2g إلى 1g.
    - `postgres` 2g إلى 1536m، ومعه `.conf`: ‏`shared_buffers` 256MB، و`maintenance_work_mem` 128MB.
    - `worker-knowledge` 1536m إلى 1g.
    - `redis-stream` 768m إلى 384m، و`maxmemory` إلى 256mb.
    - `app` 1280m إلى 1g.
    - `minio` 768m إلى 512m.
    - `qdrant` 1g إلى 768m.
    - `ops-scheduler` 512m إلى 256m.
    - `prometheus` 512m إلى 256m.
    - `worker-memory` 512m إلى 384m.
  - المجموع الجديد **11.50 vCPU و14.22 GB من 14.40**.
  - سقوفٌ تحت ذروتها المقيسة في 15 يوماً على المضيف المرجعيّ (`docs/delivery/monitoring-host-postgres/design.md:369-380`):
    - `app`: ‏1024 مقابل 1033.5 MiB.
    - `postgres`: ‏1536 مقابل 2877.
    - `minio`: ‏512 مقابل 797.7.
    - `qdrant`: ‏768 مقابل 7519.
    - `grafana`: ‏384 مقابل 511.7.
    - `loki`: ‏384 مقابل 1020.8.
  - لم يُقَس قطّ: `ollama` والعمّال الثلاثة و`redis-stream`.
  - فوق ذروتهما: `prometheus` (256 مقابل 214.8) و`ops-scheduler` (256 مقابل 183.2).
- **ق‑1 (تصميم): ملفٌّ واحد لكلّ حجم، بوضعٍ واحد في رأسه.** الأحجام الثلاثة بوضع `gpu` (ق‑0). و`large` يتّسع لوضع المعالج أيضاً (54.28 GB)، فلا حاجة لملفٍّ ثانٍ.
- **ق‑2 (Q‑2): التخفيض من Postgres.**
  - ذروة Postgres المقيسة في 15 يوماً 2877 MiB.
  - أسوأ حالٍ باشتقاق `08 §2‑ز` مع `shared_buffers=2GB`: 2 + 3 + 1 + 5.03 + 0.5 = 11.53 GB، وهو < 15g.
  - الهامش تحت 57.60 هو **70 MiB**. فأيّ سقفٍ دائمٍ يُرفع لاحقاً يُسقط `large`، وهذا مقصود، كحال الدفتر اليوم.
- **ق‑3: الشرط `maintenance_work_mem ≤ shm_size`، وليس `<` كما في AC‑2.4.**
  - في `large` اليوم القيمتان متساويتان: 1GB = 1gb.
  - الحاجة المقيسة أصلاً 101 MB (`docker-compose.yml:537-547`).
  - الشرط الصارم كان سيفرض تغيير `large`، فيخالف AC‑3.4 وNFR‑1.
- **ق‑4: السكربت يحلّ المتغيّرات بنفسه، ولا يستدعي `docker compose config`.**
  - السبب: لا Docker في اختبارات الوحدة، ولا قراءة لـ`.env` (NFR‑4).
  - الثمن نسختان من `resolve()`، وتُبقيهما متّفقتين اختبارات «agree». هذا نمط `_to_bytes` نفسه.
- **مخاطر غير حاجبة:**
  - أرقام `medium` و`small` مشتقّة، وبعضها تحت ذروةٍ مقيسة تحت الحمل:
    - `qdrant`: ‏3g في `medium`، والذروة 7.5 GB ببذرة 200 مستأجر.
    - `loki` و`grafana` كانا عند سقفيهما.
  - تُقاس هذه الأرقام في 7.3 (AC‑3.7)، ورأس كلّ ملفّ يقول «derived».
- **ملاحظتان لـ5.2، خارج النطاق:**
  - السكربت دون `--size` يقرأ القيم الافتراضيّة لا `.env`، فعلى `install.sh` أن يمرّر `--size`.
  - `.env` فيه `POSTGRES_CONFIG_FILE` أصلاً، فكتلة الحجم في 5.2 يجب أن تستبدله لا أن تكرّره.

</div>
