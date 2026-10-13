<div dir="rtl">

# خطّة إصلاح اختبارات integration في CI — خلط حلقات asyncio

> **الحالة:** منفَّذة، وCI أخضر · 2026‑10‑14 · بانتظار قرار الدمج ([PR #3](https://github.com/anastawahia/aizzak/pull/3))
> **النتيجة:** [run 38470311343](https://github.com/anastawahia/aizzak/actions/runs/38470311343): ‏`quality` و`integration` أخضران. في `integration`: 5772 ناجح، و0 فشل، و0 خطأ، و5 متخطّاة كلّها متوقّعة. لم تظهر أعطال جديدة.
> **الـ commits:** ‏`784b1c8` (حذف الوسم + `-p no:anyio` + الحارس) و`5db1654` (`-rfEs` في job ‏`integration` فقط، لأنّ `release.yml` يجب أن يطابق job ‏`quality` حرفيّاً).
> **الهدف:** أن يصبح job ‏`integration` في CI أخضر، بإزالة الـ46 خطأً وفشلاً التي سببها خلط حلقات asyncio.
> **المرجع:** [run 38373161101](https://github.com/anastawahia/aizzak/actions/runs/38373161101) على `master` ‏`de707f0`. شُغّل مرّتين في 2026‑10‑13 وكانت النتيجة نفسها بالضبط.
> **السياق السابق:** سُجّل العطل أوّل مرّة في [`delivery/monitoring-host-postgres/status.md`](delivery/monitoring-host-postgres/status.md) في 2026‑10‑06. وقتها جرّبنا تثبيت إصدارات المكتبات فلم يحلّ شيئاً، وبقي السبب «غير محسوم». هذه الخطّة تحسمه.

---

## 0) أين نحن اليوم

- **النتيجة:** 5661 ناجح، و**10 فشل**، و**36 خطأ**. المجموع 46.
- العطل ثابت لا عشوائيّ: الـ46 هي نفسها في المحاولتين.
- `quality` أخضر. العطل في job ‏`integration` وحده.

| النوع | الرسالة | المكان | العدد |
|---|---|---|---|
| خطأ عند الهدم (teardown) | `RuntimeError: Event loop is closed` | `redis_client` في [`tests/integration/conftest.py`](../tests/integration/conftest.py) عند `await client.aclose()` | 36 |
| فشل داخل الاختبار | `got Future ... attached to a different loop` | الـ fixture ‏`isolated_stream` في `test_log_correlation_live.py`، والـ fixture ‏`owner_conn` في `test_slow_queries_ops_live.py` | 10 |

توزيع الـ36 خطأً على الملفّات:

| الملفّ | العدد |
|---|---|
| `test_stream_consumer_live.py` | 10 |
| `test_ws_connection_cap_live.py` | 5 |
| `test_notify_bridge_per_process_group_live.py` | 4 |
| `test_replay_ops_live.py` | 4 |
| `test_notification_bridge_live.py` | 3 |
| `test_dlq_ops_live.py` · `test_e2e_outbox_to_worker.py` · `test_metrics_source_live.py` · `test_queue_wait_live.py` | 2 لكلّ ملفّ |
| `test_media_worker_live.py` · `test_outbox_relay_live.py` | 1 لكلّ ملفّ |

---

## 1) السبب الجذري (مُثبَت بتجربة)

1. في المشروع إضافتان (plugins) لـ pytest تشغّلان الكود غير المتزامن معاً:
   - **pytest-asyncio**، مفعّلة لكلّ الاختبارات عبر `asyncio_mode = "auto"` في [`pyproject.toml`](../pyproject.toml).
   - **anyio**، تعمل على كلّ اختبار عليه `@pytest.mark.anyio`. يوجد هذا الوسم في 29 ملفّاً (24 integration و5 unit).
2. في الاختبار الموسوم بـ anyio تتنافس الإضافتان على الـ fixtures غير المتزامنة. أيّهما تُحمَّل أوّلاً هي التي تفوز:
   - إن فازت pytest-asyncio: تعمل الـ fixture والاختبار في **حلقة واحدة**، فلا مشكلة.
   - إن فازت anyio: تعمل الـ fixture في حلقة anyio، **والاختبار نفسه في حلقة pytest-asyncio**.
3. ترتيب تحميل الإضافتين **غير مضمون**، ويختلف من بيئة إلى أخرى:
   - على هذا الجهاز تُحمَّل anyio أوّلاً، فتفوز pytest-asyncio وتنجح الاختبارات.
   - في CI يحدث العكس.
   - لهذا لم يُصلح تثبيت الإصدارات شيئاً في 2026‑10‑06.
4. **كيف ينتج النوعان:**
   - الـ36 خطأً: الاختبار يفتح اتصال Redis في حلقته، وتُغلق تلك الحلقة بعد الاختبار. بعدها تحاول anyio إغلاق الاتصال من حلقتها هي.
   - الـ10 فشل: الـ fixture يفتح الاتصال قبل الاختبار، في حلقة anyio. ثمّ يستعمله الاختبار من الحلقة الأخرى.
5. **الإثبات:** أعدنا إنتاج الرسالتين محلّيّاً بملفّ صغير، دون أيّ خدمة، فقط بعكس ترتيب التحميل:
   - الملفّ فيه fixture غير متزامنة، واختبار عليه `@pytest.mark.anyio` يستعملها.
   - الترتيب الافتراضي على هذا الجهاز: ينجح الملفّ.
   - الترتيب المعكوس: يظهر الخطآن نفسهما اللذان في CI، `Event loop is closed` و`different loop`. الأمر هو:

     ```bash
     .venv/bin/pytest <الملفّ> -p no:asyncio -p pytest_asyncio.plugin
     ```

6. **كود المنتج (`src`) ليس السبب.** العطل كلّه في إعداد الاختبارات.

---

## 2) الحلّ المختار: أداة تشغيل واحدة، هي pytest-asyncio

- نحذف وسم `pytest.mark.anyio` من الاختبارات كلّها، فتتولّى pytest-asyncio كلّ شيء في حلقة واحدة.
- ثمّ نعطّل إضافة anyio في pytest، حتى لا يعود الخلط مستقبلاً.
- **لماذا هذا الاتّجاه لا عكسه:**
  - في المشروع 2445 اختباراً غير متزامن، وكلّها تعتمد على `asyncio_mode = "auto"`.
  - الوسم anyio موجود على 107 فقط.
  - لا يستعمل أيّ اختبار ميزة خاصّة بـ anyio، مثل `anyio_backend` أو `free_tcp_port` أو تشغيل trio.
- **جُرّب على الملفّ الصغير:** بعد حذف الوسم ينجح الملفّ في **الترتيبين**.

---

## 3) خطوات التنفيذ

### الخطوة 1 — فرع جديد
- اسم الفرع: `fix/integration-asyncio-loops`.
- نقطة البداية: `origin/master` (انظر القرار ق‑أ في §5).

### الخطوة 2 — حذف الوسم (29 ملفّاً، 107 مواضع)
- ثلاثة أشكال للوسم، ولكلّ شكل طريقة:
  - السطر `@pytest.mark.anyio` فوق دالّة: يُحذف السطر.
  - داخل قائمة `pytestmark = [pytest.mark.live_redis, pytest.mark.anyio]`: يُحذف العنصر فقط. يوجد هذا الشكل في `test_log_correlation_live.py` و`test_queue_wait_live.py` و`test_load_seed_live.py`.
  - السطر `pytestmark = pytest.mark.anyio` وحده: يُحذف السطر. يوجد في `test_heavy_job_limit.py` و`test_queue_backpressure.py` و`test_worker_lifecycle.py`.
- إن صار `import pytest` غير مستعمل بعد الحذف، يُحذف أيضاً (ruff سيكشفه).
- يُصحَّح نصّ الشرح الذي يذكر `pytest.mark.anyio` في `test_conversation_get_slo_live.py` (حول السطر 109).
- الملفّات وعدد المواضع:

| integration | العدد | integration | العدد |
|---|---|---|---|
| `test_stream_consumer_live.py` | 10 | `test_processed_events_live.py` | 4 |
| `test_ledger_autovacuum_live.py` | 7 | `test_replay_ops_live.py` | 4 |
| `test_idempotency_live.py` | 6 | `test_e2e_outbox_to_worker.py` | 3 |
| `test_outbox.py` | 6 | `test_media_request_seam.py` | 3 |
| `test_metrics_source_live.py` | 5 | `test_notification_bridge_live.py` | 3 |
| `test_retention_ops_live.py` | 5 | `test_dlq_ops_live.py` | 2 |
| `test_rotate_transit_ops_live.py` | 5 | `test_producer_atomicity.py` | 2 |
| `test_slow_queries_ops_live.py` | 5 | `test_conversation_get_slo_live.py` | 1 (+ الشرح) |
| `test_ws_connection_cap_live.py` | 5 | `test_load_seed_live.py` · `test_log_correlation_live.py` | 1 لكلّ ملفّ |
| `test_notify_bridge_per_process_group_live.py` | 4 | `test_media_worker_live.py` · `test_nginx_edge_hop_slo_live.py` · `test_queue_wait_live.py` | 1 لكلّ ملفّ |
| `test_outbox_relay_live.py` | 4 | | |

| unit | العدد |
|---|---|
| `test_media_outbox_seam.py` | 9 |
| `test_ops_replay.py` | 6 |
| `test_heavy_job_limit.py` · `test_queue_backpressure.py` · `test_worker_lifecycle.py` | 1 لكلّ ملفّ |

- **أثر جانبيّ متوقَّع:** أسماء هذه الاختبارات في التقارير تفقد اللاحقة `[asyncio]`. لا توجد وثيقة في المستودع تعتمد على تلك اللاحقة.

### الخطوة 3 — حارس يمنع عودة العطل
- نضيف `-p no:anyio` إلى `addopts` في [`pyproject.toml`](../pyproject.toml)، مع تعليق قصير يشرح السبب (ترتيب التحميل غير المضمون).
- مع `--strict-markers` الموجود أصلاً، يصبح أيّ `pytest.mark.anyio` جديد **خطأً صريحاً عند الجمع (collection)**، بدل أن يمرّ هنا ويفشل في CI.
- جُرّب: على الملفّ القديم ظهرت الرسالة `'anyio' not found in markers`، والملفّ المُصلَح نجح.
- مكتبة anyio نفسها تبقى، لأنّ Starlette وhttpx يستعملانها. ما يُعطَّل هو إضافتها لـ pytest فقط.
- اختياريّ: سطر في `tests/unit/test_ci_workflow_truthfulness.py` يتأكّد أنّ `-p no:anyio` ما زال في `addopts`، على نسق حرّاس ذلك الملفّ.

### الخطوة 4 — تحقّق محلّيّ (مسموح بلا موافقة)
1. البوّابات الخمس: `ruff format --check .` ثمّ `ruff check .` ثمّ `mypy src` ثمّ `lint-imports` ثمّ `pytest tests/unit tests/architecture tests/eval`.
2. `grep -rn "mark.anyio" tests` يجب أن يرجع فارغاً.
3. جمع اختبارات integration بلا تشغيل: `pytest tests/integration --co -q`.
   - الجمع لا يتّصل بأيّ خدمة.
   - يجب أن يمرّ بلا أخطاء، وألّا يبقى اسم ينتهي بـ `[asyncio]`.
4. اختبارات integration نفسها **لا تُشغَّل محلّيّاً**، لأنّها تلمس الحزمة الحيّة (CLAUDE.md).

### الخطوة 5 — تشغيل CI على GitHub
- دفع الفرع وفتح PR إلى `master`، لأنّ CI لا يعمل إلّا على PR أو على دفع إلى `master`.
- يحتاج موافقة المالك، لأنّه نشر في مستودع عامّ (ق‑ب).
- **المتوقَّع:** يختفي الـ46 كلّه، ويبقى `quality` أخضر.

### الخطوة 6 — إن ظهرت أعطال جديدة
- هذه أوّل مرّة منذ زمن تعمل فيها اختبارات integration فعلاً حتى نهايتها. لذلك قد تظهر أعطال كانت مختبئة خلف هذا العطل.
- كلّ عطل جديد يُسجَّل بسببه ويُعرض على المالك قبل إصلاحه، ولا يُوسَّع نطاق هذه الخطّة بصمت.

### الخطوة 7 — الإغلاق
- بعد أن يخضرّ CI: الدمج بقرار المالك.
- تحديث «المشكلات المعروفة» في [`delivery/monitoring-host-postgres/release-notes.md`](delivery/monitoring-host-postgres/release-notes.md): العطل حُلّ، مع رقم الـ commit ورقم الـ run.
- تحديث حالة هذه الخطّة إلى «منفَّذة».

---

## 4) معيار القبول

- job ‏`integration` أخضر في CI. أو: صفر أعطال من نوع خلط الحلقات، وكلّ عطل آخر مسجَّل وحده بسببه.
- لا يبقى `pytest.mark.anyio` في `tests/`.
- `-p no:anyio` موجود في `addopts`.
- البوّابات المحلّيّة خضراء.

---

## 5) قرارات تحتاج المالك ⚠️

- **ق‑أ: من أين يبدأ الفرع؟**
  - **المقترح:** من `origin/master`. فيحمل الـ PR هذا الإصلاح وحده.
  - البديل: من `master` المحلّيّ. هذا يدفع معه 11 commit من `prebuilt-images`، وقرار المالك فيها «دمج محلّيّ فقط، بلا دفع».
  - الإصلاح لا يلمس أيّ ملفّ غيّرته `prebuilt-images`، فدمجه لاحقاً في `master` المحلّيّ نظيف.
- **ق‑ب: دفع الفرع وفتح PR** على المستودع العامّ لتشغيل CI. هذا ضروريّ للتحقّق.
- **ق‑ج (اختياريّ): إضافة `-rfE` إلى أمر pytest في `ci.yml`.**
  - اليوم يطبع CI أسماء الاختبارات المتخطّاة فقط (`-rs`)، ولا يطبع أسماء الفاشلة في الملخّص.
  - الملفّ محميّ، فالتعديل يحتاج موافقة. وقد يحتاج معه تعديل حارس في `test_ci_workflow_truthfulness.py`.

---

## 6) المخاطر

| الخطر | الاحتمال | ماذا نفعل |
|---|---|---|
| أعطال جديدة كانت مختبئة تظهر بعد الإصلاح | متوسّط | الخطوة 6: تُسجَّل وتُعرض، ولا تُدمج في هذا الإصلاح بصمت |
| اختبار كان يعتمد على سلوك anyio (مثل فكّ `ExceptionGroup` عند الفشل) | منخفض | يؤثّر في نصّ رسالة الفشل فقط لا في النتيجة، ولا يستعمل أيّ اختبار ميزة خاصّة بـ anyio |
| CI يثبّت إصدارات أحدث من `.venv` المحلّيّ (لا ملفّ قفل) | قائم | خارج النطاق. تجربة 2026‑10‑06 أثبتت أنّه ليس سبب هذا العطل |

---

## 7) خارج النطاق

- تثبيت إصدارات التبعيات بملفّ قفل.
- أيّ تعديل على كود المنتج في `src`.
- تشغيل اختبارات integration على الحزمة الحيّة في هذا الجهاز.

</div>
