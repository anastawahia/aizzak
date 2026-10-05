<div dir="rtl">

# وثائق البيانات — AIZZAK

> منطقةُ `data-engineer`. كلُّ مهارةٍ أو وكيلٍ يلمس مخزنَ بياناتٍ **يقرأ `db-profile.md` أوّلاً**.
>
> الأعرافُ: الوثائقُ بالعربيّة ومُغلَّفةٌ بـ`<div dir="rtl">`؛ وأسماءُ الملفّات إنجليزيّةٌ بـkebab‑case؛ والتواريخُ ISO مطلقة؛ والمعرّفاتُ بشُرطةٍ غيرِ فاصلة (`CLAUDE.md`).

## الملفّ التعريفيّ

| الوثيقة | البيئات | آخر تحقّق | الحالة |
|---|---|---|---|
| [`db-profile.md`](db-profile.md) — المخازنُ والبيئاتُ ووصفاتُ الاتّصال وأدواتُ المشروع والأدوارُ وتعدّدُ المستأجرين والبياناتُ الحسّاسة | `aizzak` (‏shared‑dev) · `aizzak_test` (‏test) · مخازنُ المكدّس الحيّ (‏shared‑dev) · حزمةُ CI (‏test) | 2026‑10‑05 | ✅ نافذة — ثلاثةُ بنودٍ مفتوحة: **ح‑أ** قاعدةُ الاختبار تشارك العنقودَ الحيّ · **ح‑ب** ‏Redis/Qdrant/Vault/MinIO بلا نسخةٍ اختباريّة · **ح‑ج** لا دورَ قراءةٍ محضةٍ للتشخيص |

## الأدلّة (‏`guides/`)

| الدليل | التاريخ | الحالة |
|---|---|---|
| [`cloud-migration.md`](guides/cloud-migration.md) — أيُّ خدمات البيانات تُنقل إلى خدماتٍ سحابيّةٍ مُدارة، وسهولةُ نقل كلٍّ منها، والمواردُ التي تتوفّر (≈15.5 vCPU و35 GB) | 2026‑10‑05 | ✅ للفهم والقرار — لا خطّةُ تنفيذ؛ القرارُ هو `ق‑2` |

## المخطّط (‏`schema/`)

لا شيءَ بعد. المصدرُ المُلزِم للتصميم اليوم هو [`docs/design/01-data-model.md`](../design/01-data-model.md) و[`docs/architecture.md`](../architecture.md) (‏`D‑01`…`D‑26`) — وأيُّ تصميمٍ جديدٍ هنا يبني عليهما ولا ينسخهما.

## الهجرات (‏`migrations/`)

لا خطّةَ إطلاقٍ بعد. ملفّاتُ الهجرة نفسُها في `migrations/versions/<module>/`، **لا تحت `docs/`**. وتُطبَّق عبر `python -m app.ops.provision` وحده.

## الأداء (‏`performance/`)

لا شيءَ بعد. المقاييسُ القائمةُ اليوم في [`docs/capacity-status.md`](../capacity-status.md) و[`docs/capacity-plan.md`](../capacity-plan.md) و[`docs/delivery/baseline.md`](../delivery/baseline.md).

## الصحّة (‏`health/`)

| التقرير | البيئة | التاريخ | النتيجة |
|---|---|---|---|
| [`2026-10-05-shared-dev.md`](health/2026-10-05-shared-dev.md) | `aizzak` (‏shared‑dev) + ‏Redis ×2 + ‏Qdrant + ‏MinIO + ‏Vault | 2026‑10‑05 | **0 critical · 2 high · 8 medium · 3 low** — الأبرز: مفتاحٌ أجنبيٌّ بلا فهرس على `knowledge.chunks` (‏1.28 مليون صفّ)، و30 رسالةً في صفوف الموتى. ستُّ نتائجَ تنتظر قرارَ المالك |

أدواتُ المشروع الجاهزة: `python -m app.ops.slow_queries top` · `table_growth status` · `stream_trim status` · `notify_groups list` · `qdrant_capacity`.
⚠️ ‏`slow_queries` و`table_growth` تحتاجان DSN المالك ⇒ تُشغَّلان عبر `docker compose run --rm --no-deps migrate python -m app.ops.<tool>`، لا من `ops-scheduler`.

## أدلّةُ التشغيل والتجارب (‏`runbooks/` · `drills/`)

لا شيءَ بعد. ‏`python -m app.ops.backup` وخدمتا `backup` و`wal-shipper` تعملان على المكدّس الحيّ، **ولم تُسجَّل تجربةُ استعادةٍ قطّ** — هو `Q‑3` في الملفّ التعريفيّ. وأدلّةُ التنبيهات في [`docs/runbooks/alerts.md`](../runbooks/alerts.md).

## إصلاحاتُ البيانات (‏`data-fixes/`)

| الإصلاح | البيئة | التاريخ | الحالة |
|---|---|---|---|
| [`2026-10-05-backup-lifecycle/`](data-fixes/2026-10-05-backup-lifecycle/README.md) — قواعدُ انتهاءِ النسخ في `aizzak-backups`: من 22 قاعدةً مكرّرة إلى 4 لكلّ بادئة (‏`qdrant/` يوم · `wal/` 7 أيّام · `base/` 7 أيّام · `dump/` 30 يوماً) | MinIO (‏shared‑dev) | 2026‑10‑05 | ✅ طُبّق وتُحقّق منه · `QDRANT_RETENTION` = 3 أيّام ينتظر إعادةَ بناء الصورة |

</div>
