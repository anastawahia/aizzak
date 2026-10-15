<div dir="rtl">

# المراجعة الأمنية: أحجام Compose (`compose-sizes`)

- **النطاق المراجَع:** `git diff dbb18ab..HEAD` على `feat/compose-sizes` (إعدادات وحدود فقط، بلا شيفرة تطبيق)
- **الحكم:** **PASS**

## قائمة الفحص
- [x] المصادقة والصلاحيات: لا مسار جديد (لا ينطبق)
- [x] عزل المستأجرين: لا مستودع ولا استعلام جديد (لا ينطبق)
- [x] التحقق من المدخلات: `resource-budget.sh` لا `eval` ولا `source` ولا `subprocess`؛ الوسائط تمرّ موضعيّةً إلى Python، و`<<'PY'` مقتبس، و`yaml.safe_load`، وملفّ الحجم يُقسَم `key=val` دون تنفيذ. يبقى عبور مسار في `--size` (م‑1)
- [x] لا أسرار: ملفّات `deploy/sizes/*.env` فيها مفاتيح `_CPUS/_MEM/_REPLICAS/_MAXMEMORY` و`POSTGRES_CONFIG_FILE` فقط، ويفرضها اختبار (`test_a_size_file_has_a_header_and_exactly_the_derived_keys`). تعديل `.env.example` تعليقان فقط. لا نمط مفتاح في الفرق. اختبارات [config] تمرّر `--env-file .env.example` ببيئةٍ نظيفة، ولا `env_file:` في Compose. AC‑1.6 سجّل التطابق وحده دون ناتجٍ محلول (NFR‑4)
- [x] رسائل الخطأ: تطبع المسار واسم الحجم، لا القيم
- [x] التبعيات الجديدة: لا شيء (`pyproject.toml` لم يتغيّر). `gitleaks`/`trufflehog` غير مثبّتين
- [x] الشبكة والتركيب: لا منفذ جديد ولا تغيير في `ports:`؛ التركيب الجديد `./deploy/postgres/sizes:/etc/postgresql/sizes:ro` للقراءة فقط
- [x] حدود الموارد: سقوف `small` تحت الذروة المقيسة، والمالك قبل خطر `OOMKilled` (ق‑0). ملاحظة لا تمنع (م‑2)

## الملاحظات
| # | الشدة | الفئة (OWASP/CWE) | الملف:السطر | الوصف | الإصلاح |
|---|---|---|---|---|---|
| م‑1 | منخفضة | A01 / CWE‑22 | `deploy/resource-budget.sh:145` (الفحص في `:65`) | `--size ../../` يصير `deploy/sizes/../../.env` فيقرأ `.env` الحقيقيّ. إن كان فيه سطر `# host:` تدخل قيم `*_CPUS/_MEM` منه في الحساب، وقد يطبع خطأ `float()` قيمةً منه. أداةٌ محلّيّة يشغّلها المشغّل نفسه، فلا حدّ صلاحيّاتٍ يُعبَر | قبول `--size` فقط إن طابق `^[a-z0-9-]+$` أو كان ضمن `available` قبل بناء المسار |
| م‑2 | معلومة | A05 / CWE‑400، CWE‑770 | `deploy/sizes/small.env:3` | سقوف `small` (ومنها `REDIS_*_MAXMEMORY`) تحت الذروة، فقد تُقتل خدمات حرجة (postgres، vault، redis-stream) بـ`OOMKilled` تحت الحمل فيتوقّف المكدّس. مقبولة (ق‑0، للتجربة فقط) | لا يُستعمل `small` على Pod مكشوفٍ للعموم ببيانات حقيقيّة؛ تُسجَّل في قائمة أمن staging في `docs/deploy-runpod.md` إن استُعمل هناك |

</div>
