<div dir="rtl">

# وثائق البيانات — AIZZAK

> منطقةُ `data-engineer`. كلُّ مهارةٍ أو وكيلٍ يلمس مخزنَ بياناتٍ **يقرأ `db-profile.md` أوّلاً**.
>
> الأعرافُ: الوثائقُ بالعربيّة ومُغلَّفةٌ بـ`<div dir="rtl">`؛ وأسماءُ الملفّات إنجليزيّةٌ بـkebab‑case؛ والتواريخُ ISO مطلقة؛ والمعرّفاتُ بشُرطةٍ غيرِ فاصلة (`CLAUDE.md`).

## الملفّ التعريفيّ

| الوثيقة | البيئات | آخر تحقّق | الحالة |
|---|---|---|---|
| [`db-profile.md`](db-profile.md) — المخازنُ والبيئاتُ ووصفاتُ الاتّصال وأدواتُ المشروع والأدوارُ وتعدّدُ المستأجرين والبياناتُ الحسّاسة | `aizzak` (‏shared‑dev) · `aizzak_test` (‏test) · مخازنُ المكدّس الحيّ (‏shared‑dev) · حزمةُ CI (‏test) | 2026‑10‑05 | ✅ نافذة — ثلاثةُ بنودٍ مفتوحة: **ح‑أ** قاعدةُ الاختبار تشارك العنقودَ الحيّ · **ح‑ب** ‏Redis/Qdrant/Vault/MinIO بلا نسخةٍ اختباريّة · **ح‑ج** لا دورَ قراءةٍ محضةٍ للتشخيص |

## المخطّط (‏`schema/`)

لا شيءَ بعد. المصدرُ المُلزِم للتصميم اليوم هو [`docs/design/01-data-model.md`](../design/01-data-model.md) و[`docs/architecture.md`](../architecture.md) (‏`D‑01`…`D‑26`) — وأيُّ تصميمٍ جديدٍ هنا يبني عليهما ولا ينسخهما.

## الهجرات (‏`migrations/`)

لا خطّةَ إطلاقٍ بعد. ملفّاتُ الهجرة نفسُها في `migrations/versions/<module>/`، **لا تحت `docs/`**. وتُطبَّق عبر `python -m app.ops.provision` وحده.

## الأداء (‏`performance/`)

لا شيءَ بعد. المقاييسُ القائمةُ اليوم في [`docs/capacity-status.md`](../capacity-status.md) و[`docs/capacity-plan.md`](../capacity-plan.md) و[`docs/delivery/baseline.md`](../delivery/baseline.md).

## الصحّة (‏`health/`)

لا تقريرَ بعد. أدواتُ المشروع الجاهزة: `python -m app.ops.slow_queries top` · `table_growth status` · `stream_trim status` · `notify_groups list` · `qdrant_capacity`.

## أدلّةُ التشغيل والتجارب (‏`runbooks/` · `drills/`)

لا شيءَ بعد. ‏`python -m app.ops.backup` وخدمتا `backup` و`wal-shipper` تعملان على المكدّس الحيّ، **ولم تُسجَّل تجربةُ استعادةٍ قطّ** — هو `Q‑3` في الملفّ التعريفيّ. وأدلّةُ التنبيهات في [`docs/runbooks/alerts.md`](../runbooks/alerts.md).

## إصلاحاتُ البيانات (‏`data-fixes/`)

لا شيءَ بعد.

</div>
