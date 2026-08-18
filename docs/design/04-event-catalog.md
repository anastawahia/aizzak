# كتالوج الأحداث (Event Catalog)

> نوعان (`EVT‑04`): **Domain Events** بالذاكرة داخل الوحدة، و**Global Events** عبر Redis Streams **للعمليات الثقيلة فقط** (`EVT‑10`).
> النشر عبر **Transactional Outbox** (D‑18)؛ التسليم **at‑least‑once + Idempotent + DLQ** (D‑19)؛ **مجرى لكل وحدة + Consumer Groups** (D‑20).
> المظروف **CloudEvents 1.0** (`DD‑07`)؛ التسمية/الإصدار (`DD‑08`)؛ المثالية (`DD‑09`). المخططات: [`events/schemas/`](events/schemas/).

> ⚠️ **قياس `usage` خارج الناقل (`FR‑131`, `EVT‑10`):** التقاط استهلاك وحدة `usage` **لا يمرّ عبر Redis Streams/Global Events** — بل عبر **منفذ وارد متزامن** يُلحِق قيداً في سجلّ `usage.usage_records` (append‑only، إدمبوتنسي عبر `operation_id`). هذا يصون قصر الأحداث على العمليات الثقيلة فقط. لا مجرى ولا Consumer Group لوحدة `usage`.

## 1) المظروف الموحّد — CloudEvents 1.0
```json
{
  "specversion": "1.0",
  "id": "018f2a7c-...-uuidv7",            // = event_id ومفتاح المثالية
  "source": "knowledge",                   // اسم الوحدة المُنتِجة
  "type": "knowledge.document.indexed.v1", // <module>.<aggregate>.<event>.vN
  "subject": "018f-doc-id",                // معرّف الـaggregate
  "time": "2026-07-09T10:15:00Z",          // UTC
  "datacontenttype": "application/json",
  "dataschema": "https://schemas.platform/knowledge.document.indexed.v1.json",
  "workspaceid": "018f-ws-id",             // امتداد — لتوجيه المستأجر وضبط RLS في العامل
  "correlationid": "018f-corr",            // امتداد — تتبّع (نقطة توسعة Observability)
  "causationid": "018f-cause",             // امتداد — الحدث/الطلب المُسبِّب
  "data": { }                              // الحمولة (schema لكل نوع)
}
```
- امتدادات CloudEvents بأحرف صغيرة بلا فواصل (قيد المواصفة): `workspaceid`, `correlationid`, `causationid`.
- المظروف كامل يُخزَّن في `platform.outbox.payload` ويُنشر كما هو.

## 2) طوبولوجيا المجاري (D‑20)
| المجرى (stream) | المنتِج | Consumer Group | العامل |
|-----------------|---------|----------------|--------|
| `stream.files` | files | — | **لا مستهلك** (منذ الفهرسة اليدويّة) |
| `stream.knowledge` | knowledge | `cg.notify.<host>.<pid>` | جسر الإشعارات → WebSocket |
| `stream.media` | media (API) | `cg.media` | media_worker (يولّد) |
| `stream.memory` | memory | `cg.memory` | memory_worker (يفهرس المتجهات) |
| `*` (فشل) | العامل | — | `stream.<m>.dlq` بعد N محاولات |

- ⚠️ **`stream.files` بلا مجموعةِ استهلاكٍ عمداً منذ صارت الفهرسة يدويّة.** كان `cg.knowledge` يقرؤه ويسجّل مستنداً لكلّ رفعٍ مكتمل — أي أنّ إتمام الرفع كان أمراً بالفهرسة ولم يُسأل أحد. صار التسجيل طلباً صريحاً (`POST /knowledge/documents`) يكتب المستند وحدثه في معاملةٍ واحدة، فلم يبقَ لهذا المجرى قارئ. ولا يُنشأ له صفٌّ في `STATIC_CONSUMER_TOPOLOGY`: مجموعةٌ لا يقرؤها أحد يتضخّم تأخّرها (lag) برفعةٍ كلَّ مرّة إلى الأبد، وتأخّرُ مجموعةٍ مهجورة هو بالضبط الإشارة التي يجب أن يثق بها المشغّل. الحدث **ما يزال يُنشر** (‏`XADD` لا يحتاج مجموعة) لأنّه واقعةٌ صادقة عن مساحة العمل.
- اسم الإدخال في Redis: `XADD stream.<module> * ce <json>`.
- كل عامل: `XREADGROUP GROUP cg.<name> <consumer> COUNT k BLOCK <ms>` ثم `XACK` بعد النجاح.
- ⚠️ **`cg.notify` أسرةُ مجموعاتٍ لكلّ عمليّة، لا مجموعةٌ واحدة** (مُصحَّحٌ في `docs/log/3.81.md`، P0‑2): خلافاً لـ`cg.knowledge`/`cg.media`/`cg.memory` — مجموعةٌ فعليّةٌ واحدة لكلٍّ منها، لأنّ عمّالها عمليّاتٌ مستقلّةٌ عن الـAPI — جسر الإشعارات يعيش **داخل عمليّة الـAPI نفسها**، والافتراضيّ في مساري النشر كليهما `WEB_CONCURRENCY=2` (عدّة عمليّات gunicorn شقيقة لنسخةٍ واحدة). و`ConnectionHub` سجلٌّ **داخل‑العمليّة** (جلسة WebSocket تعيش في عمليّةٍ واحدة فقط)، فمجموعةٌ مشتركةٌ بين الشقائق كانت تُوزِّع كلَّ حدثٍ على **نصفها فقط** ⇒ فقدُ نحو نصف الإشعارات صامتاً (`XACK` بلا خطأ). لذلك تصنع كلّ عمليّة gunicorn شقيقة مجموعتها الخاصّة `cg.notify.<hostname>.<pid>` عند الإقلاع (فترى كلُّ عمليّةٍ كلَّ حدثٍ وتُصفّي بمركز اتّصالها الداخليّ)، وتُتلفها (`XGROUP DESTROY`) عند إغلاقٍ نظيف؛ ومجموعةٌ يتيمة بعد إنهاءٍ غير نظيف (SIGKILL) تُكنَس عند إقلاعٍ تالٍ على المضيف نفسه (`CompositionRoot.sweep_stale_notify_groups`، مضمونةٌ ألّا تلمس مجموعة شقيقٍ حيّ — التفصيل في تلك الدالّة وفي `docs/log/3.81.md`).

> **التمثيل التنفيذيّ لهذا الجدول** (الأزواج الساكنة الثلاثة `cg.knowledge`/`cg.media`/`cg.memory` — لا `cg.notify` الغائبة عمداً أعلاه، ولا `stream.files` الذي صار بلا قارئ) هو `STATIC_CONSUMER_TOPOLOGY` في `src/app/framework/events/topology.py`. `tests/unit/test_stream_topology.py` يحرس الاثنين من الانحراف (تطابقٌ تامٌّ مع ثوابت `STREAM` ومع اشتراكات العمّال الثلاثة). ومنذ `stream-topology-plan.md` ([§3.106](../log/3.106.md)–[§3.108](../log/3.108.md))، `build_relay_from_env` (‏`workers/bootstrap.py`) يمرّ على هذا الجدول بعينه مستدعياً `ensure_group` لكلّ زوجٍ **قبل** أوّل نشرٍ لِـ`outbox_relay` — فمجموعات هذا الجدول تُنشأ قبل أن يُنشر أوّل حدثٍ على أيٍّ من مجاريه، لا بعده.

## 3) دلالات التسليم والمثالية (D‑19, `DD‑09`)
1. **الإنتاج ذرّي:** الأثر النطاقي + صف Outbox في نفس المعاملة ⇒ لا فقد.
2. **النشر at‑least‑once:** `outbox_relay` قد ينشر مرتين عند إعادة المحاولة.
3. **الاستهلاك idempotent:** المستهلك يُدرج `(consumer_group, event_id)` في `platform.processed_events` داخل معاملة الأثر ⇒ تكرار = تجاهُل + `XACK`.
4. **إعادة المحاولة:** فشل عابر ⇒ لا `XACK` ⇒ يُعاد التسليم؛ بعد **N=5** ⇒ يُنقل إلى `stream.<m>.dlq` مع سبب.
5. **مفاتيح عمل طبيعية** تعزّز المثالية (مثل `UNIQUE(document_id, seq)`).

### 3.1) مخطط تتابع — النشر الموثوق Outbox → Relay → Worker

يوضّح دلالات المعاملتين الذرّيتين (الإنتاج والاستهلاك)، وتحوّل `at‑least‑once` إلى `effectively‑once` عبر `platform.processed_events`، والتحويل إلى DLQ بعد `N=5`. المسار خاصّ بـ**العمليات الثقيلة فقط** (`EVT‑10`)؛ قياس `usage` **لا يمرّ هنا** (منفذ وارد متزامن — انظر التنبيه أعلى الملف).

```mermaid
sequenceDiagram
    autonumber
    participant Prod as المنتِج (Module / API)
    participant DB as PostgreSQL
    participant Relay as outbox_relay
    participant Stream as Redis Stream (stream.&lt;module&gt;)
    participant Worker as العامل (Consumer Group)
    participant DLQ as stream.&lt;module&gt;.dlq

    rect rgb(230, 240, 255)
        Note over Prod,DB: معاملة واحدة (ذرّية) — لا فقد
        Prod->>DB: كتابة الأثر النطاقي
        Prod->>DB: INSERT platform.outbox (المظروف CloudEvents)
    end

    loop استطلاع دوري (poll)
        Relay->>DB: قراءة صفوف outbox غير المنشورة
        Relay->>Stream: XADD stream.&lt;module&gt; * ce &lt;json&gt;
        Note over Relay,Stream: at‑least‑once — قد يُنشر مرتين عند إعادة المحاولة
        Relay->>DB: تحديث platform.stream_offsets (تقدّم النشر)
    end

    Worker->>Stream: XREADGROUP GROUP cg.&lt;name&gt; &lt;consumer&gt; COUNT k BLOCK ms
    Stream-->>Worker: الحدث (ce)

    alt نجاح المعالجة
        rect rgb(230, 255, 235)
            Note over Worker,DB: معاملة واحدة — الأثر + المثالية معاً
            Worker->>DB: تطبيق الأثر الجانبي (Postgres · Qdrant · MinIO)
            Worker->>DB: INSERT platform.processed_events (consumer_group, event_id)
        end
        Worker->>Stream: XACK
    else تكرار — تعارض PK(consumer_group, event_id)
        Worker->>DB: INSERT يفشل ⇒ تجاهُل بهدوء (effectively‑once)
        Worker->>Stream: XACK
    else فشل عابر
        Note over Worker,Stream: لا XACK ⇒ يُعاد التسليم
        alt تجاوز N=5 محاولة
            Worker->>DLQ: نقل الحدث + سبب الفشل
            Worker->>Stream: XACK (إخراج من المجرى الأصلي)
        end
    end
```

## 4) الأحداث العالمية (Global · عبر المجاري)

| النوع (type) | المنتِج → المستهلك | subject | الحمولة (data) |
|--------------|--------------------|---------|-----------------|
| `files.file.uploaded.v1` | files → — (لا مستهلك) | file_id | `{file_id, content_type, size_bytes, storage_key, space_id?}` |
| `knowledge.document.registered.v1` | knowledge(API) → knowledge(worker) | document_id | `{document_id, file_id}` |
| `knowledge.document.indexed.v1` | knowledge → notify | document_id | `{document_id, chunk_count}` |
| `knowledge.document.indexing_failed.v1` | knowledge → notify | document_id | `{document_id, reason}` |
| `knowledge.summary.requested.v1` | knowledge → knowledge(worker) | job_id | `{job_id, document_id, kind, lang}` |
| `knowledge.summary.built.v1` | knowledge(worker) → notify | job_id | `{job_id, document_id, kind, lang}` |
| `knowledge.summary.build_failed.v1` | knowledge → notify | job_id | `{job_id, document_id, reason}` |
| `media.job.requested.v1` | media(API) → media(worker) | job_id | `{job_id, kind, prompt, params}` |
| `media.job.generated.v1` | media(worker) → notify | job_id | `{job_id, result_file_id}` |
| `media.job.failed.v1` | media(worker) → notify | job_id | `{job_id, reason}` |
| `memory.item.stored.v1` | memory → memory(worker) | memory_id | `{memory_id, agent_key, content_ref}` |

> **مَن يُنتج `knowledge.document.registered.v1`؟** الـAPI، منذ صارت الفهرسة يدويّة: `POST /knowledge/documents` (فهرسةٌ أولى، `IndexFileService`) و`POST /knowledge/reindex` (إعادة بناء، `ReindexService`) — كلاهما يسجّل مستنداً `pending` ويُلحِق هذا الحدث في المعاملة نفسها. لم يعد لعامل المعرفة منتِجٌ لهذا الحدث ولا مشترَكٌ في `stream.files`؛ يستهلكه فقط.

## 5) الأحداث الداخلية (Domain · بالذاكرة، لا تعبر المجاري)
تُرفع داخل الوحدة عبر `framework/events/event_bus.py` وتُترجَم إلى صفوف Outbox عند الحاجة لنشرها عالمياً.

| الوحدة | الأحداث الداخلية |
|--------|------------------|
| workspace | `WorkspaceCreated`, `WorkspaceArchived`, `UserProvisioned` |
| access | `RoleAssigned`, `RoleRevoked` |
| credentials | `CredentialAdded`, `CredentialRevoked` |
| conversations | `ConversationStarted`, `MessageAppended` |
| memory | `MemoryStored`, `MemoryForgotten` |
| files | `FileUploaded`*, `FileDeleted` |
| knowledge | `DocumentRegistered`*, `DocumentIndexed`*, `DocumentIndexingFailed`* |
| media | `MediaRequested`*, `MediaGenerated`*, `MediaFailed`* |
| integrations | `ConnectionEstablished`, `ConnectionRevoked`, `TokenRefreshed`, `McpServerRegistered` |
| usage | `UsageRecorded`, `LimitExceeded` |

(*) تُرقّى إلى حدث عالمي عبر Outbox.
> أحداث `integrations` و`usage` **داخلية بالذاكرة فقط** — لا تُرقّى إلى مجارٍ عالمية في v1 (لا مهام ثقيلة؛ التجديد كسول متزامن، والقياس عبر منفذ وارد). تبقى نقطة توسعة عبر Outbox عند الحاجة مستقبلاً.

## 6) قواعد التطوّر (Versioning · `DD‑08`)
- **إضافة حقل اختياري** ⇒ نفس الإصدار (`.vN`) — المستهلكون يتجاهلون المجهول.
- **حذف/تغيير معنى/إلزام حقل** ⇒ إصدار جديد `.vN+1`، ويُنشر الحدث بالإصدارين خلال نافذة الترحيل، ثم يُوقَف القديم بعد ترقية كل المستهلكين.
- كل إصدار له ملف JSON Schema مستقل؛ الـ`dataschema` في المظروف يشير إليه.
- كسر المظروف نفسه (نادر) ⇒ ترقية `specversion` مُنسّقة.

## 7) مخططات JSON (مكتملة — schema لكل نوع)
- [`events/schemas/envelope.cloudevents.json`](events/schemas/envelope.cloudevents.json) — المظروف العام.
- [`events/schemas/files.file.uploaded.v1.json`](events/schemas/files.file.uploaded.v1.json)
- [`events/schemas/knowledge.document.registered.v1.json`](events/schemas/knowledge.document.registered.v1.json)
- [`events/schemas/knowledge.document.indexed.v1.json`](events/schemas/knowledge.document.indexed.v1.json)
- [`events/schemas/knowledge.document.indexing_failed.v1.json`](events/schemas/knowledge.document.indexing_failed.v1.json)
- [`events/schemas/knowledge.summary.requested.v1.json`](events/schemas/knowledge.summary.requested.v1.json)
- [`events/schemas/knowledge.summary.built.v1.json`](events/schemas/knowledge.summary.built.v1.json)
- [`events/schemas/knowledge.summary.build_failed.v1.json`](events/schemas/knowledge.summary.build_failed.v1.json)
- [`events/schemas/media.job.requested.v1.json`](events/schemas/media.job.requested.v1.json)
- [`events/schemas/media.job.generated.v1.json`](events/schemas/media.job.generated.v1.json)
- [`events/schemas/media.job.failed.v1.json`](events/schemas/media.job.failed.v1.json)
- [`events/schemas/memory.item.stored.v1.json`](events/schemas/memory.item.stored.v1.json)
> جميع الأحداث الثمانية العالمية في الجدول (§4) لها الآن ملف JSON Schema مستقل بنفس النمط؛ الـ`dataschema` في المظروف يشير إليه.
