<div dir="rtl">

# 2026‑10‑05 · قواعدُ انتهاءِ النسخ في دلو `aizzak-backups`

> **البيئة:** MinIO في المكدّس الحيّ (‏`shared-dev`) · **الدلو:** `aizzak-backups` · **وافق عليه:** المالك، 2026‑10‑05 · **نفّذه:** وكيل `db-guide`

## لماذا

الدلو 49 GiB، ويتّجه إلى ≈ 300 GiB بالقواعد القديمة، للأسباب التالية:

1. الدلو بنظام النسخ (versioning)، فكلّ ما يحذفه `backup prune` يبقى نسخةً قديمةً 30 يوماً.
2. لقطاتُ Qdrant الليليّة 7.1 GiB للّيلة الواحدة.
3. في الدلو 22 قاعدةً مكرّرة، سببُها `deploy/minio/bootstrap.sh` القديم.

## ما الذي تغيّر

| | قبل | بعد |
|---|---|---|
| عددُ القواعد | 22 قاعدةً متطابقة بلا بادئة، كلٌّ 30 يوماً | 4 قواعد، واحدةٌ لكلّ بادئة |
| `qdrant/` | 30 يوماً | **يومٌ واحد** |
| `wal/` | 30 يوماً | **7 أيّام** |
| `base/` · `dump/` | 30 يوماً | 30 يوماً (بلا تغيير) |

التغييرُ في الكود: `deploy/minio/bootstrap.sh` (‏`mc ilm import` يتكرّر بلا أثرٍ جانبيّ) و`src/app/ops/backup.py:202` (‏`QDRANT_RETENTION` = 3 أيّام). ولم يُعمل له commit بعد.

## ما شُغِّل

1. حُفظت القواعدُ القديمة (22 قاعدة) في `~/aizzak-backups-snapshots/lifecycle-before-2026-10-05.json`، **خارج المستودع**.
2. سُجّلت الأحجامُ قبل التطبيق (‏`mc du --versions`):

   | البادئة | الحجم | عددُ النسخ |
   |---|---|---|
   | `qdrant/` | 33 GiB | 2219 |
   | `wal/` | 12 GiB | 2858 |
   | `base/` | 2.3 GiB | 20 |
   | `dump/` | 1.7 GiB | 10 |

   والنسخُ الحاليّة وحدها 32 GiB.
3. طُبّقت القواعدُ الجديدة بالأمر `docker compose up --no-deps minio-bootstrap`، فخرج بـ`exit 0` وطبع: «‏4 noncurrent-version rules: qdrant/ 1d, wal/ 7d, base/ and dump/ 30d».

## التحقّق

- `mc ilm rule ls loc/aizzak-backups` يعرض **4 قواعد بالضبط**.
- نظامُ النسخ ما زال مُفعَّلاً.
- ‏`minio` و`wal-shipper` و`ops-scheduler` في حالة `healthy`.
- **المتوقَّع خلال 24–48 ساعة:** يُحذف نهائيّاً ما يصل إلى ≈ 5 GiB من النسخ القديمة في `qdrant/`، والنسخُ القديمة في `wal/` الأقدمُ من 7 أيّام (من أصل ≈ 12 GiB). يُعاد القياسُ بالأمر `mc du --versions` على كلّ بادئة.

## طريقُ الرجوع

- **القواعد:** `mc ilm import loc/aizzak-backups < ~/aizzak-backups-snapshots/lifecycle-before-2026-10-05.json`، داخل حاوية `minio`.
- **الملفّات:** ما حذفه MinIO نهائيّاً **لا يُسترجَع**. كان ذلك مقبولاً لثلاثة أسباب:
  - لقطاتُ Qdrant حالةٌ مشتقّة، يُعاد بناؤها بإعادة التضمين.
  - نسخُ WAL القديمة لا تحتاجها أيُّ نسخةِ `base` باقية.
  - كلُّ نسخةِ `base` تحمل الـWAL الذي تحتاجه (`--wal-method stream`).

## ما بقي

- **`QDRANT_RETENTION` = 3 أيّام لا يعمل بعد.** يحتاج إعادةَ بناء الصورة وإعادةَ إنشاء `ops-scheduler`، والمالكُ أجّلها.
- **مدّةُ `base/` إلى 7 أيّام: ✅ طُبّقت حيّاً في 2026‑10‑05 بموافقة المالك.**
  - حُفظت القواعدُ الأربع السابقة في `~/aizzak-backups-snapshots/lifecycle-4rules-2026-10-05.json`.
  - شُغّل `docker compose up --no-deps minio-bootstrap`، فخرج بـ`exit 0` وطبع «qdrant/ 1d, wal/ 7d, base/ 7d, dump/ 30d».
  - `mc ilm rule ls` يعرض 4 قواعد بمُدد 1/7/7/30، ونظامُ النسخ مُفعَّل، و`minio` و`wal-shipper` و`ops-scheduler` في حالة `healthy`.
  - تفاصيلُ الإعداد:
  - **في الكود (بلا commit):** متغيّرٌ جديد `BACKUP_BASE_NONCURRENT_DAYS` (الافتراضيّ 7) في `deploy/minio/bootstrap.sh` تقرؤه قاعدةُ `noncurrent-base` وحدها، ويُتحقَّق منه كبقيّة المتغيّرات (عددٌ صحيح ≥ 1). و`BACKUP_NONCURRENT_DAYS` (‏30) صار يغطّي `dump/` وحدها. أُضيف السطرُ إلى `.env.example`، وحارسٌ في `tests/unit/test_backup_wiring.py`.
  - **جُرِّب على MinIO مؤقّت (scratch)** بالصورتين المثبّتتين: تشغيلان متتاليان أعطيا 4 قواعد بالضبط (‏1/7/7/30)، و`BACKUP_BASE_NONCURRENT_DAYS=0` خرج بـ`exit 1`.
  - **القياسُ الحيّ (قراءةٌ فقط، 2026‑10‑05):** في `base/` ‏2.3 GiB بكلّ النسخ، منها 1.9 GiB نسخٌ حاليّة (4 دُفعات). النسخُ غير الحاليّة **دُفعةٌ واحدة**: `20260904T104900Z`، أربعةُ ملفّات، 418.4 MiB، صارت غيرَ حاليّة في 2026‑10‑02 02:09 UTC (حذفها `prune`).
  - **أثرُ التطبيق:** لا يُحذف شيءٌ فوراً. تنتهي تلك الدُّفعةُ قرابةَ 2026‑10‑10 بدلَ 2026‑11‑02. وفي الحالة المستقرّة يبقى ≈ 7 دُفعاتٍ غير حاليّة بدل ≈ 30 (‏≈ 0.49 GiB للدُّفعة)، أي توفيرُ ≈ 11 GiB.
  - **التطبيق** (يحتاج موافقةً جديدة): `cd /home/AIZZAK && docker compose up --no-deps minio-bootstrap`، ويجب أن يطبع «‏qdrant/ 1d, wal/ 7d, base/ 7d, dump/ 30d». ثمّ `mc ilm rule ls loc/aizzak-backups` يعرض 4 قواعد، و`noncurrent-base` بـ7.
  - **الرجوع:** قبل التطبيق تُحفظ القواعدُ الأربع الحاليّة بـ`mc ilm export` في `~/aizzak-backups-snapshots/lifecycle-4rules-2026-10-05.json`، وللرجوع تُستورَد بـ`mc ilm import`، كلاهما داخل حاوية `minio`. ولا يصلح `.env` للرجوع، لأنّ `docker-compose.yml` لا يمرّر `BACKUP_BASE_NONCURRENT_DAYS` بعد. وما حذفه MinIO لا يُسترجَع، لكنّ الرجوعَ قبل 2026‑10‑09 لا يخسر شيئاً.
- **تمريرُ `BACKUP_QDRANT_NONCURRENT_DAYS` و`BACKUP_WAL_NONCURRENT_DAYS` و`BACKUP_BASE_NONCURRENT_DAYS`** في `docker-compose.yml`: الملفّ do‑not‑touch، ويحتاج موافقة.

</div>
