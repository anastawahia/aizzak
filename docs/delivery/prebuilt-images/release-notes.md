<div dir="rtl">

# ملاحظات الإصدار: صورٌ جاهزة بدل البناء المحلّيّ (المرحلة 1 من خطّة خادم الكرت)

- **الإصدار/الوسم:** `v2026.10.1` على `master` ‏`759c82d`. أوّل إصدارٍ تبنيه CI وترفعه.
- **الـ PRs:** [PR #3](https://github.com/anastawahia/aizzak/pull/3) (الميزة)، و[PR #5](https://github.com/anastawahia/aizzak/pull/5) (رأس `master` الموسوم).
- **التشغيل:** [run 38500790209](https://github.com/anastawahia/aizzak/actions/runs/38500790209)، أخضر في المحاولة 4.

## ما الجديد
- صورتان في مستودع Docker Hub الخاصّ `anastawahia/aizzak`:

  | الوسم | البصمة | الحجم المحلّيّ |
  |---|---|---|
  | `app-2026.10.1` | `sha256:b09587ca718e71e89681bfee572f6d2b2ce4ba553a2d5939c9269b67962e155c` | 1.39 ج.ب |
  | `embedding-2026.10.1` | `sha256:dd817bb384edc95aa84c4412e3c56874f0deecaf769082b123858c31a6836127` | 3.75 ج.ب |

- كلتاهما `linux/amd64`، ومن الـcommit ‏`759c82dd1d9c8e8b0eea64ba87ff45e36423c48c`.
- لا صورة `embedding-cuda-*` بعد: مكانها محجوزٌ للخطوة 2.4.

## خطوات النشر
لا نشر على خادم في هذا الإصدار. الصور تُستعمل أوّل مرّة في الخطوة 5.2 (`install.sh`) وفي التجربة 7.3. أمّا خطوات الإصدار التالي، فهي:
1. commit أخضر على `master`.
2. `git tag v<YYYY.M.N> <commit> && git push origin v<YYYY.M.N>`. لا يدفع وسم `v*` ولا يحذفه إلّا admin (ruleset ‏`release-tags`).
3. يعمل `release.yml`: `preflight` ثمّ `quality` ثمّ `publish` في البيئة `release`.

## الترحيلات وتغييرات الإعدادات
- **لا ترحيلات.**
- **GitHub** (ش-3، 2026‑10‑14):
  - البيئة `release`، وسياسة نشرها وسوم `v*` فقط.
  - السرّان `DOCKERHUB_USERNAME` و`DOCKERHUB_TOKEN` أسرارُ بيئة.
  - ruleset ‏`release-tags` (id ‏`25041632`) على `refs/tags/v*`، يتجاوزه admin وحده.
- **Docker Hub:** المستودع `anastawahia/aizzak` خاصّ. أعدّه المالك.

## التحقق بعد النشر (smoke)
- [x] **(أ) AC-4.7:** دُفع `v2026.10.1-test`. في [run 38500738096](https://github.com/anastawahia/aizzak/actions/runs/38500738096) فشل `preflight` بعد 5 ثوانٍ (`is not a version`)، ولم يُبنَ شيء. ثمّ حُذف الوسم.
- [x] **(ب) AC-4.6:** `v2026.10.1` و`DOCKERHUB_USERNAME` وحده. فشل `publish` بعد 9 ثوانٍ عند «Registry secrets are set»، وذكر `DOCKERHUB_TOKEN`.
- [x] **(ج) AC-4.5 وAC-2.6 وAC-3.9:** بعد إضافة المفتاح، أعيد تشغيل المهمّة الفاشلة (re-run):
  - السكربت انتهى بـ`release: OK 2026.10.1 (2 images)`.
  - سطرا البصمة في السجلّ وفي الـartifact ‏`release-digests`.
  - `grep -ci dckr_pat` على السجلّ كلّه = 0.
- [x] **(د) AC-2.4:** على جهاز المالك المسجَّل:
  - `docker pull` للوسمين نجح.
  - `imagetools inspect` يعطي البصمتين أعلاه.
  - `{{.Os}}/{{.Architecture}}` = `linux/amd64`.
- [x] **(هـ) AC-2.5:** بـ`DOCKER_CONFIG` فارغ، `docker pull` يفشل بـ`pull access denied`، والصفحة العامّة `404`.

## خطة التراجع
1. لا شيء يعمل من هذه الصور اليوم، فلا تراجع على خادم.
2. إن تبيّن عيبٌ في الصورتين: لا يُعاد وسم `2026.10.1`، فالسكربت يرفض وسماً موجوداً. يُصلَح العيب على `master`، ثمّ يُدفع `v2026.10.2`.
3. حذف الوسم من Docker Hub قرارٌ بشريّ. ومفتاح CI بصلاحيّة Read & Write (ش-3 (2)) لا يحذف.

## نتيجة staging
لا staging لهذه الميزة: لا خادم بعد. وما جرى:
- محاولات `publish` 2 و3 فشلت عند `docker login` بـ`unauthorized: incorrect username or password`: قيمة `DOCKERHUB_TOKEN` كانت خاطئة. لم يُبنَ شيء ولم يُرفع شيء.
- اختبر المالك المفتاح محلّيّاً بـ`DOCKER_CONFIG` مؤقّت، ثمّ وضعه بـ`gh secret set DOCKERHUB_TOKEN --env release` حتّى لا تدخله مسافةٌ عند اللصق. فنجحت المحاولة 4.
- **مدّة `publish` الناجح:** 3 دقائق وثوانٍ للبناء والرفع معاً. الرفع من جهاز المالك كان ~150 ك.ب/ث، فالطريق المعتاد CI كما في الخطّة.

</div>
