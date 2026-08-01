# مراجع الهجرة من `alpha` (Reference Harvest — Phase 0)

> ملاحظات مرجعية **سلوكية/خوارزمية** مستخرجة من قاعدة `alpha` القديمة، تُجمّد المعرفة القابلة لإعادة الاستخدام قبل كتابة كود AIZZAK.
>
> **قاعدة حاكمة:** `docs/design/` هو مصدر الحقيقة؛ هذه المراجع **تُوضّح ما فعلته `alpha` وكيف** فقط. عند أي تعارض: **التصميم يفوز**. لا يُنسخ أي سرّ من `alpha` هنا.
>
> **الحالة:** ✅ **المرحلة 0 مكتملة** (2026‑07‑10) — الملفات الخمسة الأولى أدناه كافية لإعادة البناء دون فتح `alpha`. **حصاد لاحق عند الحاجة** (بند إعادة استخدام لم تشمله المرحلة 0) يُضاف بنفس القالب ويُوسم بمرحلته.

| الملف | المحتوى | وجهة التصميم | حالة |
|---|---|---|---|
| [`tools.md`](tools.md) | أداتا `web_search` (Exa) و`calculator` (safe‑AST) كمثالَي `BaseTool` | `11 §4` · `02 §3.1` | ✅ م0 |
| [`parsers.md`](parsers.md) | مُحلّلات المستندات (PDF/Excel/OCR/JSON/نص) | `06 §7` · `01 §2.7` | ✅ م0 |
| [`retrieval.md`](retrieval.md) | الاسترجاع: RRF · BM25 · العتبات · النية · parent‑chunk | `06 §7` · `11 (RAG)` | ✅ م0 |
| [`llm-providers.md`](llm-providers.md) | مزوّدو LLM: base_url · num_ctx/keep_alive · اكتشاف Ollama | `02 §1.1` · `00 DD‑13` | ✅ م0 |
| [`credentials-oauth-jobs.md`](credentials-oauth-jobs.md) | تشفير المفاتيح · Gmail OAuth/PKCE · وظائف Redis | `06 §3,§9` · `04` | ✅ م0 |
| [`auth-firebase.md`](auth-firebase.md) | مصادقة Firebase: تحقّق التوكن · المطالبات · خريطة 401/403 · كاش النتيجة (نمط مضادّ) | `02 §1.10` · `arch 11` · `D‑25` · م6.4 | ✅ **م2.7** (2026‑07‑16) |

**تنبيهات أمنية (من الحصاد):**
- `alpha/services/tools/web_search.py` يحمل مفتاح Exa API صريحاً كقيمة احتياطية — يُنصح بإبطاله/تدويره في `alpha`، ولا يُنقل النمط إلى AIZZAK (المفاتيح عبر `SecretsProvider`/Vault Transit).
- `alpha/serviceAccountKey.json` (حساب خدمة Firebase) و`alpha/gmail_credentials.json` (**سرّ عميل OAuth بنصّ صريح**) — قيَمهما **لم تُستنسَخ**؛ يحتاجان تدويراً. التفصيل في [`auth-firebase.md`](auth-firebase.md).
