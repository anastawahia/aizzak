<div dir="rtl">

# حصر سجل التطبيق: ما يُكتب، ومن أين، وكم

> **المصدر:** طلب المالك (2026‑10‑23): «تفصيلٌ كاملٌ لكلّ ما يُطبع في سجلّ التطبيق». الخطّة المبنيّة على هذا الحصر في [`plan.md`](plan.md).
> **طريقة الحصر:** مسحٌ آليٌّ (AST) لكلّ استدعاء `debug/info/warning/error/exception` تحت `src/app`، وقياسٌ حيٌّ لسجلّ الحاوية `aizzak-app-31` (عمرها نحو 22 دقيقة يوم القياس، 694 سطرًا).

## 1) كيف يُكتب السطر اليوم

1. **المُنسِّق الواحد:** `JsonFormatter` في `src/app/framework/observability/logging.py`. يكتب كائن JSON واحدًا في كلّ سطر، بالحقول `time` و`level` و`logger` و`message`.
2. **معرّفات السياق:** تُضاف تلقائيًّا من متغيّرات السياق (`framework/observability/context.py`) متى كانت مربوطة:
   - `request_id`: معرّف nginx للقفزة الواحدة (`X-Request-Id`).
   - `correlation_id`: من العميل أو يساوي `request_id`.
   - `event_id`: في العمّال فقط.
   - `workspace_id`: يُكتب مستعارًا (`ws-…`) لا خامًا.
3. **حقول `extra=`:** تُدمج في السطر بعد تمريرها على `redact`.
4. **أماكن الربط الثلاثة:**
   - `api/main.py` (وسيط الترابط): يربط `request_id` و`correlation_id`.
   - `api/v1/dependencies.py` (`current_context`): يربط `workspace_id`.
   - `infrastructure/messaging/consumers/engine.py`: يربط الثلاثة حول كلّ رسالة.
5. **مُسجِّلات الخادم المتبنّاة:** `gunicorn*` و`uvicorn*` تُمرَّر إلى المُنسِّق نفسه، ومنها سطر `uvicorn.access` لكلّ طلب.
6. **المكتبات الخارجية:** `httpx` يكتب سطر INFO لكلّ استدعاء HTTP خارج العملية، ولا يُضبط مستواه إلّا في أدوات `ops`.
7. **المستوى:** `LOG_LEVEL` واحد للعملية كلّها (`INFO` افتراضيًّا). لا يوجد ضبطٌ لكلّ مُسجِّل على حدة.
8. **المسار المجمَّع:** Alloy يقرأ أسطر الحاويات ويرسلها إلى Loki، وعليها لوحة Grafana `aizzak-logs.json` ببحث `correlation_id`. هذا كلّه ضمن ملف التشغيل `monitoring`، وهو **غير مشغَّل الآن**، فالطرفية هي المنفذ الوحيد للمطوّر.

## 2) ما يظهر فعليًّا في سجلّ `app` (قياس حيّ)

| المصدر | الأسطر | النسبة | الحجم |
|---|---:|---:|---:|
| `uvicorn.access` — `GET /health` | 318 | 47.5٪ | 77 KB |
| `uvicorn.access` — `GET /health/ready` (فحص Docker) | 159 | 23.7٪ | 32 KB |
| `plugin_loader.registered` (5 لكلّ عامل gunicorn في كلّ إقلاع) | 40 | 6.0٪ | 8 KB |
| `uvicorn.access` — `GET /metrics` | 20 | 3.0٪ | 4 KB |
| `uvicorn.access` — `POST /api/v1/me/heartbeat` | 12 | 1.8٪ | 3 KB |
| `agent_lifecycle.transition` | 12 | 1.8٪ | 3.5 KB |
| أسطر الإقلاع والإيقاف (`composition_root.*` و`storage_binding.*` و`uvicorn.error` و`notify_groups.*`) | 70 تقريبًا | 10٪ | 13 KB |
| أسطر gunicorn الرئيسية **بنصّ عاديّ لا JSON** | 24 | — | — |
| **عمل الرسائل الحقيقيّ** (`knowledge.retrieval` و`rag_agent.answer` و`httpx` وطلبات المحادثة) | 27 تقريبًا | 4٪ | 17 KB |

**الخلاصة:** 71٪ من الأسطر فحوصات صحّة، و4٪ فقط عملٌ حقيقيّ.

## 3) أسطر رسالة واحدة من الواجهة، سطرًا سطرًا

مثال المالك (`request_id = 55a28616…`). الرسالة الواحدة أنتجت 12 سطرًا:

| # | السطر | مصدره | الملاحظة |
|---:|---|---|---|
| 1 | `uvicorn.access` — `POST …/messages 200` | uvicorn | يُكتب **عند بدء الردّ المتدفّق**، أي قبل أن يعمل الوكيل. بلا زمن، وبلا قالب المسار، وبلا مستخدم. |
| 2–4 | `agent_lifecycle.transition` (created، initialized، running) | `framework/agent_runtime/executor.py:161` | ثلاثة أسطر بلا معلومة للمطوّر. |
| 5 | `httpx` — `POST embedding-lb:8080/embed` | httpx | بلا زمن. |
| 6–7 | `httpx` — `POST qdrant…/points/query` ×2 | httpx | بلا زمن، واسم المجموعة الطويل يتكرّر. |
| 8 | `knowledge.retrieval` | `modules/knowledge/application/retrieval.py:1219` | **سطر واحد بنحو 3.6 KB**: قوائم الدرجات و20 مرشّحًا. |
| 9 | `httpx` — `POST …:11435/api/chat` | httpx | استدعاء النموذج، بلا زمن. |
| 10 | `rag_agent.answer` | `agents/rag_agent/agent.py:1422` | **السطر الأهمّ**: المسار، `llm_ms`، `total_ms`. |
| 11 | `agent_lifecycle.transition` (disposed) | `executor.py:161` | |
| 12 | `uvicorn.access` — `GET …/messages?limit=100` | uvicorn | طلبٌ تالٍ من الواجهة لتحديث القائمة. |

ويتخلّل ذلك `POST /api/v1/me/heartbeat` من الواجهة، وأحيانًا `httpx GET googleapis…/jwk` (تجديد مفاتيح Firebase)، و`/health` كلّ 10 ثوانٍ.

## 4) المشكلات التي كشفها الحصر

- **م‑1: `workspace_id` غائبٌ عن كلّ أسطر الطلب.**
  - `current_context` تبعيّةٌ متزامنة (`def` لا `async def`)، فيشغّلها FastAPI في خيطٍ منفصل بنسخةٍ من السياق، ويضيع `workspace_id_var.set(...)` بانتهاء الخيط.
  - الدليل: لا سطر في المثال يحمل `workspace_id`.
  - الاختبار الحاليّ `test_the_context_dependency_binds_the_workspace` يفحص نصّ الملفّ فقط، لا السلوك.
- **م‑2: فحوصات الصحّة تغرق السجلّ** (71٪ من الأسطر، §2).
- **م‑3: لا يوجد سطرٌ يُغلق الطلب.**
  - سطر `uvicorn.access` بلا زمن ولا قالب مسار ولا مستخدم.
  - في الردود المتدفّقة يُكتب **قبل** العمل، فيظهر `200` حتّى لو فشل الوكيل بعده.
  - الزمن موجودٌ في سجلّ nginx فقط (`duration_s`)، وهو حاويةٌ أخرى.
- **م‑4: `knowledge.retrieval` ضخم** (نحو 3.6 KB في السطر).
  - هو مقصودٌ في التصميم (P‑29، خطّة الاسترجاع §3.11)، ويقرؤه `tests/eval/run_calibration.py`.
  - لكنّه يملأ الشاشة في كلّ رسالة.
- **م‑5: `httpx` يكتب سطرًا لكلّ استدعاءٍ داخليّ** (4–5 في الرسالة)، بلا زمن ولا فائدة للتتبّع.
- **م‑6: `agent_lifecycle.transition` أربعة أسطر لكلّ رسالة** بمستوى INFO.
- **م‑7: ضجيج الإقلاع.**
  - `plugin_loader.registered` 5 أسطر لكلّ عامل، و`composition_root.agents_loaded` يقول الشيء نفسه في سطر.
  - أسطر gunicorn الرئيسية بنصٍّ عاديّ لا يفهمه Alloy.
- **م‑8: تكرار المعرّف.** حين لا يرسل العميل `X-Correlation-Id` يكون `correlation_id == request_id`، فيتكرّر 32 حرفًا في كلّ سطر.
- **م‑9: تسمية غير موحّدة.**
  - أحداث محرّك المستهلكين بلا بادئة منطقة (`dead_lettered` و`handler_failed` و`consumer_loop_failed`…).
  - حدثٌ اسمه متغيّر (`engine.py:703`، `_logger.error(reason, …)`).
  - أحداثٌ في `workers/bootstrap.py` بلا بادئة (`summary_delivery_declined`…).
  - `uid` في `auth.*` مقابل `user_id` في غيرها.
- **م‑10: لا ربط لـ `conversation_id` و`agent_key` في السياق.** تظهر في بعض الأسطر فقط، فلا يمكن جمع أسطر محادثة واحدة.
- **م‑11: مستوى واحد للعملية كلّها.** لا يمكن تشغيل تفاصيل الاسترجاع وحدها دون إغراق السجلّ.

## 5) كلّ مواضع الكتابة في الكود

**المجموع: 193 موضعًا.** حسب المستوى: INFO ‏76، WARNING ‏92، ERROR ‏23، EXCEPTION ‏2. منها 38 في أدوات `ops` التي تعمل في حاويات منفصلة لمرة واحدة.

**مصادر خارج الكود:** `uvicorn.access` (سطر لكلّ طلب)، و`uvicorn.error` و`gunicorn.error` (الإقلاع والإيقاف)، و`httpx` (سطر لكلّ استدعاء خارجيّ).

الجداول التالية مولَّدة آليًّا من الكود. المسارات نسبيّة إلى `src/app/`.

### طبقة API (عملية `app`) — 14

| الحدث | المستوى | الموضع | الحقول |
|---|---|---|---|
| `health.drain_requested` | INFO | `api/health.py:130` | sessions, window_s, repeat |
| `api.background_task_died` | ERROR | `api/main.py:366` | task_name + traceback |
| `api.unmapped_http_exception` | WARNING | `api/main.py:503` | path, status, correlation_id |
| `api.server_error` | ERROR | `api/main.py:553` | path, code, status, correlation_id + traceback |
| `api.unhandled_exception` | ERROR | `api/main.py:595` | path, correlation_id + traceback |
| `auth.revoked_subject` | WARNING | `api/middleware/auth.py:179` | uid |
| `auth.identity_without_email` | WARNING | `api/middleware/auth.py:188` | uid |
| `auth.disabled_account` | WARNING | `api/middleware/auth.py:233` | user_id, correlation_id |
| `auth.disabled_account` | WARNING | `api/middleware/auth.py:282` | user_id |
| `api.in_flight_limit_reached` | WARNING | `api/middleware/inflight.py:129` | correlation_id, in_flight |
| `api.queue_backpressure_unavailable` | WARNING | `api/middleware/queue_backpressure.py:141` | stream, error |
| `api.rate_limit_unavailable` | WARNING | `api/middleware/rate_limit.py:153` | error_code |
| `api.heavy_job_limit_unavailable` | WARNING | `api/middleware/rate_limit.py:214` | error_code |
| `ws.pump_ended` | WARNING | `api/v1/websocket/streaming.py:455` | agent_key, conversation_id + traceback |

### الوكلاء (عملية `app`) — 15

| الحدث | المستوى | الموضع | الحقول |
|---|---|---|---|
| `orchestrator.llm_fallback` | WARNING | `agents/orchestrator.py:667` | outcome, detail |
| `orchestrator.llm_fallback` | WARNING | `agents/orchestrator.py:675` | outcome, detail, fallback_error |
| `orchestrator.stream_deadline_exceeded` | WARNING | `agents/orchestrator.py:1155` | workflow_key, cap_s |
| `orchestrator.workflow_started` | INFO | `agents/orchestrator.py:1560` | workflow_key, conversation_id, steps |
| `orchestrator.step_output_persist_failed` | WARNING | `agents/orchestrator.py:1626` | conversation_id + traceback |
| `orchestrator.stream_deadline_exceeded` | WARNING | `agents/orchestrator.py:1903` | agent_key, cap_s |
| `orchestrator.partial_reply_persist_failed` | WARNING | `agents/orchestrator.py:2102` | conversation_id, reason + traceback |
| `orchestrator.partial_reply_persisted` | INFO | `agents/orchestrator.py:2112` | reason, chars |
| `orchestrator.pending_clarification_persist_failed` | WARNING | `agents/orchestrator.py:2195` | conversation_id, options + traceback |
| `orchestrator.reply_persist_failed` | WARNING | `agents/orchestrator.py:2240` | conversation_id + traceback |
| `orchestrator.usage_capture_failed` | WARNING | `agents/orchestrator.py:2325` | agent_key, provider, tokens + traceback |
| `orchestrator.usage_release_failed` | WARNING | `agents/orchestrator.py:2350` | reservation_id + traceback |
| `rag_agent.unscoped_turn` | WARNING | `agents/rag_agent/agent.py:1279` | conversation_id |
| `rag_agent.corpus_header_unavailable` | WARNING | `agents/rag_agent/agent.py:1326` | + traceback |
| `rag_agent.answer` | INFO | `agents/rag_agent/agent.py:1422` | path, retrieval_attempted, context_nodes, fallback, llm_ms, total_ms, error_type, best_dense_score, best_bm25_score |

### الإطار (`app` وكل العمّال) — 35

| الحدث | المستوى | الموضع | الحقول |
|---|---|---|---|
| `agent_lifecycle.failed` | WARNING | `framework/agent_runtime/executor.py:135` | agent_key, lifecycle + traceback |
| `agent_lifecycle.dispose_failed` | WARNING | `framework/agent_runtime/executor.py:150` | agent_key, lifecycle + traceback |
| `agent_lifecycle.transition` | INFO | `framework/agent_runtime/executor.py:161` | agent_key, lifecycle |
| `plugin_loader.isolated` | WARNING | `framework/agent_runtime/plugin_loader.py:125` | plugin, reason + traceback |
| `plugin_loader.registered` | INFO | `framework/agent_runtime/plugin_loader.py:132` | plugin, key |
| `auth.principal_cache_unreadable` | WARNING | `framework/auth/principal_cache.py:201` | — |
| `composition_root.notify_stale_groups_swept` | WARNING | `framework/di/composition_root.py:641` | groups |
| `composition_root.initialized` | INFO | `framework/di/composition_root.py:1851` | app_env, api_prefix |
| `composition_root.agents_loaded` | INFO | `framework/di/composition_root.py:1987` | loaded, failures |
| `composition_root.notify_orphan_groups_swept` | WARNING | `framework/di/composition_root.py:2404` | groups |
| `composition_root.notify_group_sweep_failed` | ERROR | `framework/di/composition_root.py:2409` | + traceback |
| `file.replacement.lookup_failed` | EXCEPTION | `framework/di/file_replacement.py:200` | file_id, workspace_id |
| `file.replacement.delete_failed` | EXCEPTION | `framework/di/file_replacement.py:213` | file_id, replaced_id, workspace_id |
| `file.replacement.replaced` | INFO | `framework/di/file_replacement.py:222` | file_id, replaced_id, workspace_id |
| `lifecycle.dispose_failed` | WARNING | `framework/di/lifecycle.py:58` | disposable + traceback |
| `storage_binding.minio_bound` | INFO | `framework/di/storage_binding.py:89` | endpoint, signing_endpoint, bucket |
| `heartbeat.write_failed` | WARNING | `framework/observability/heartbeat.py:131` | path + traceback |
| `heartbeat.write_recovered` | INFO | `framework/observability/heartbeat.py:137` | path |
| `scheduled_task.ledger_write_failed` | WARNING | `framework/observability/scheduled_tasks.py:269` | task + traceback |
| `scheduled_task.ledger_write_recovered` | INFO | `framework/observability/scheduled_tasks.py:276` | task |
| `hub.registry_unavailable_refusing_connection` | WARNING | `framework/streaming/hub.py:315` | workspace_id |
| `hub.registry_unavailable_on_release` | WARNING | `framework/streaming/hub.py:360` | workspace_id |
| `hub.user_disconnect_failed` | WARNING | `framework/streaming/hub.py:394` | user_id |
| `hub.drain_started` | INFO | `framework/streaming/hub.py:461` | sessions, window_s |
| `hub.drain_close_failed` | WARNING | `framework/streaming/hub.py:486` | workspace_id |
| `hub.registry_unavailable_on_renew` | WARNING | `framework/streaming/hub.py:560` | — |
| `hub.session_outbox_full_closing` | WARNING | `framework/streaming/hub.py:615` | workspace_id, event_type, queued |
| `hub.notify_send_failed` | WARNING | `framework/streaming/hub.py:649` | workspace_id |
| `hub.slow_session_close_failed` | WARNING | `framework/streaming/hub.py:701` | — |
| `tool_resolver.dynamic_tool_malformed_name` | WARNING | `framework/tools/resolver.py:102` | name_type, source |
| `tool_resolver.dynamic_shadowed_by_static` | WARNING | `framework/tools/resolver.py:108` | tool, source |
| `workflow.started` | INFO | `framework/workflows/engine.py:256` | workflow_key, steps |
| `workflow.halted` | WARNING | `framework/workflows/engine.py:274` | workflow_key, step + traceback |
| `workflow.halted` | WARNING | `framework/workflows/engine.py:293` | workflow_key, step |
| `workflow.completed` | INFO | `framework/workflows/engine.py:301` | workflow_key |

### الاسترجاع (عملية `app`) — 2

| الحدث | المستوى | الموضع | الحقول |
|---|---|---|---|
| `knowledge.rerank_degraded` | WARNING | `modules/knowledge/application/retrieval.py:1146` | error_code, rerank_candidates |
| `knowledge.retrieval` | INFO | `modules/knowledge/application/retrieval.py:1219` | قاموس كامل (`stages`) |

### وحدة المعرفة: التحليل والفهرسة والتلخيص (`worker-knowledge`) — 49

| الحدث | المستوى | الموضع | الحقول |
|---|---|---|---|
| `docx.table_skipped` | INFO | `modules/knowledge/adapters/parsers/docx.py:494` | table_index, rows |
| `excel.read_failed` | WARNING | `modules/knowledge/adapters/parsers/excel.py:79` | error, error_type |
| `knowledge.parse_failed` | WARNING | `modules/knowledge/adapters/parsers/extractor.py:365` | file_name, source_ext, error |
| `knowledge.parse_empty` | INFO | `modules/knowledge/adapters/parsers/extractor.py:377` | file_name, source_ext |
| `image_ocr.decompression_bomb` | WARNING | `modules/knowledge/adapters/parsers/image_ocr.py:204` | error |
| `image_ocr.open_failed` | WARNING | `modules/knowledge/adapters/parsers/image_ocr.py:207` | error |
| `image_ocr.pdf_open_failed` | WARNING | `modules/knowledge/adapters/parsers/image_ocr.py:236` | error |
| `image_ocr.pdf_encrypted` | WARNING | `modules/knowledge/adapters/parsers/image_ocr.py:241` | — |
| `image_ocr.archive_open_failed` | WARNING | `modules/knowledge/adapters/parsers/image_ocr.py:263` | error |
| `image_ocr.preprocess_failed` | WARNING | `modules/knowledge/adapters/parsers/image_ocr.py:294` | error |
| `image_ocr.tesseract_not_found` | WARNING | `modules/knowledge/adapters/parsers/image_ocr.py:300` | error |
| `image_ocr.ocr_failed` | WARNING | `modules/knowledge/adapters/parsers/image_ocr.py:303` | error |
| `image_ocr.capped` | INFO | `modules/knowledge/adapters/parsers/image_ocr.py:390` | images |
| `image_ocr.pdf_image_failed` | WARNING | `modules/knowledge/adapters/parsers/image_ocr.py:493` | xref, page_number, error |
| `image_ocr.media_read_failed` | WARNING | `modules/knowledge/adapters/parsers/image_ocr.py:508` | media, error |
| `image_ocr.unreadable_image` | WARNING | `modules/knowledge/adapters/parsers/image_ocr.py:520` | error |
| `image_ocr.open_failed` | WARNING | `modules/knowledge/adapters/parsers/image_ocr.py:531` | error |
| `json.no_clean_encoding` | WARNING | `modules/knowledge/adapters/parsers/json_doc.py:135` | — |
| `json.stream_skipped_bytes` | WARNING | `modules/knowledge/adapters/parsers/json_doc.py:204` | skipped |
| `json.stream_empty` | WARNING | `modules/knowledge/adapters/parsers/json_doc.py:206` | — |
| `json.max_depth_exceeded` | WARNING | `modules/knowledge/adapters/parsers/json_doc.py:224` | path, depth |
| `json.max_depth_exceeded_text_walk` | WARNING | `modules/knowledge/adapters/parsers/json_doc.py:448` | path, depth |
| `pdf_tables.all_filtered` | INFO | `modules/knowledge/adapters/parsers/pdf_tables.py:260` | — |
| `pdf_tables.encrypted` | WARNING | `modules/knowledge/adapters/parsers/pdf_tables.py:277` | — |
| `pdf_tables.probe_failed` | WARNING | `modules/knowledge/adapters/parsers/pdf_tables.py:280` | error |
| `pdf_tables.band_skipped_mixed_page_sizes` | INFO | `modules/knowledge/adapters/parsers/pdf_tables.py:383` | pages |
| `pdf_tables.band_probe_failed` | WARNING | `modules/knowledge/adapters/parsers/pdf_tables.py:388` | error |
| `pdf_tables.running_band_detected` | INFO | `modules/knowledge/adapters/parsers/pdf_tables.py:419` | header_cut, footer_cut, pages |
| `pdf_tables.band_strip_failed` | WARNING | `modules/knowledge/adapters/parsers/pdf_tables.py:456` | error |
| `pdf_tables.detected` | INFO | `modules/knowledge/adapters/parsers/pdf_tables.py:477` | table_count |
| `pdf_tables.read_failed` | WARNING | `modules/knowledge/adapters/parsers/pdf_tables.py:480` | error |
| `pdf_tables.dropped_single_column` | INFO | `modules/knowledge/adapters/parsers/pdf_tables.py:501` | count |
| `pdf_tables.dropped_low_accuracy` | INFO | `modules/knowledge/adapters/parsers/pdf_tables.py:505` | count, min_accuracy |
| `pdf_tables.continued_group` | INFO | `modules/knowledge/adapters/parsers/pdf_tables.py:679` | pages |
| `pdf_tables.caption_failed` | WARNING | `modules/knowledge/adapters/parsers/pdf_tables.py:827` | error |
| `pdf_tables.dropped_table_of_contents` | INFO | `modules/knowledge/adapters/parsers/pdf_tables.py:913` | table_index, page_number, rows |
| `pdf_tables.dropped_leader_rows` | INFO | `modules/knowledge/adapters/parsers/pdf_tables.py:923` | table_index, page_number, rows |
| `pdf_tables.skipped_all_leader_rows` | INFO | `modules/knowledge/adapters/parsers/pdf_tables.py:928` | table_index |
| `pdf_tables.caption_doc_failed` | WARNING | `modules/knowledge/adapters/parsers/pdf_tables.py:940` | error |
| `pdf_tables.skipped_empty` | INFO | `modules/knowledge/adapters/parsers/pdf_tables.py:962` | table_index |
| `pdf_text.open_failed` | WARNING | `modules/knowledge/adapters/parsers/pdf_text.py:133` | error |
| `pdf_text.encrypted` | WARNING | `modules/knowledge/adapters/parsers/pdf_text.py:138` | — |
| `pdf_text.table_only_page` | INFO | `modules/knowledge/adapters/parsers/pdf_text.py:162` | page_index, tables_avoided |
| `pdf_text.page_failed` | WARNING | `modules/knowledge/adapters/parsers/pdf_text.py:201` | page_index, error |
| `indexing.embed_batch_failed_retrying_per_chunk` | WARNING | `modules/knowledge/application/indexing.py:495` | document_id, batch_size |
| `indexing.semantic_batch_failed_retrying_per_chunk` | WARNING | `modules/knowledge/application/indexing.py:618` | document_id, batch_size |
| `indexing.semantic_split_failed_falling_back_to_plain_segment` | WARNING | `modules/knowledge/application/indexing.py:689` | document_id, sentence_count + traceback |
| `indexing.table_row_cap_reached` | INFO | `modules/knowledge/application/indexing.py:990` | kept_rows, table_order |
| `summarization.output_truncated` | WARNING | `modules/knowledge/application/summarization.py:827` | step, max_tokens |

### مُرحِّل Outbox (`outbox-relay`) — 3

| الحدث | المستوى | الموضع | الحقول |
|---|---|---|---|
| `outbox_relay.publish_failed` | WARNING | `infrastructure/messaging/outbox.py:120` | event_id, stream, attempts_bumped |
| `outbox_relay.batch_published` | INFO | `infrastructure/messaging/outbox.py:155` | count |
| `outbox_relay.cycle_failed` | ERROR | `infrastructure/messaging/outbox.py:196` | backoff_ms + traceback |

### قصّ التدفقات (`outbox-relay`) — 3

| الحدث | المستوى | الموضع | الحقول |
|---|---|---|---|
| `stream_trim.trimmed` | INFO | `infrastructure/messaging/stream_retention.py:416` | stream, trimmed, length, minid, holder, boundary_age_s |
| `stream_trim.backstop_near` | WARNING | `infrastructure/messaging/stream_retention.py:445` | stream, length, backstop, holder, boundary_age_s, triage |
| `stream_trim.failed` | ERROR | `infrastructure/messaging/stream_retention.py:471` | + traceback |

### البنية التحتية: المستهلكون والمصادقة وQdrant — 19

| الحدث | المستوى | الموضع | الحقول |
|---|---|---|---|
| `firebase_auth.jwks_fetch_failed` | WARNING | `infrastructure/auth/firebase_auth.py:420` | error, cached_keys, cache_fresh, next_attempt_in_s + traceback |
| `dlq.backlog` | WARNING | `infrastructure/messaging/consumers/dlq_watch.py:85` | stream, dlq, depth, oldest_entry_id, oldest_age_s, oldest_reason, triage |
| `consumer_loop_failed` | ERROR | `infrastructure/messaging/consumers/engine.py:584` | consumer, retry_in_s + traceback |
| `consumer_sweep_failed` | ERROR | `infrastructure/messaging/consumers/engine.py:614` | + traceback |
| `dlq_watch_failed` | ERROR | `infrastructure/messaging/consumers/engine.py:636` | + traceback |
| `consumer_deregister_failed` | ERROR | `infrastructure/messaging/consumers/engine.py:685` | + traceback |
| ⚠️ متغيّر `reason` | ERROR | `infrastructure/messaging/consumers/engine.py:703` | entry_id, stream |
| `unroutable_envelope` | ERROR | `infrastructure/messaging/consumers/engine.py:718` | entry_id, stream |
| `handler_failed` | ERROR | `infrastructure/messaging/consumers/engine.py:774` | entry_id, stream, type, delivery_count + traceback |
| `dead_lettered` | ERROR | `infrastructure/messaging/consumers/engine.py:823` | entry_id, stream, dlq, reason, delivery_count |
| `consumers.sweep_reclaimed` | WARNING | `infrastructure/messaging/consumers/sweeper.py:153` | stream, group, from_consumer, to_consumer, entries |
| `consumers.sweep_refused` | WARNING | `infrastructure/messaging/consumers/sweeper.py:167` | stream, group, consumer, pending, reason |
| `consumers.sweep_dropped_pending` | ERROR | `infrastructure/messaging/consumers/sweeper.py:185` | stream, group, consumer, dropped |
| `consumers.sweep_deleted` | INFO | `infrastructure/messaging/consumers/sweeper.py:195` | stream, group, consumer, idle_ms |
| `consumers.deregister_refused` | WARNING | `infrastructure/messaging/consumers/sweeper.py:226` | stream, group, consumer, pending |
| `consumers.deregistered` | INFO | `infrastructure/messaging/consumers/sweeper.py:233` | stream, group, consumer |
| `notify_groups.settled_live` | INFO | `infrastructure/messaging/consumers/sweeper.py:474` | stream, group, reason |
| `notify_groups.destroyed` | INFO | `infrastructure/messaging/consumers/sweeper.py:490` | stream, group |
| `qdrant.corpus_shadow_write` | WARNING | `infrastructure/vector/qdrant_store.py:575` | workspace_corpus, write_collection |

### إقلاع العمّال ودورة حياتهم — 15

| الحدث | المستوى | الموضع | الحقول |
|---|---|---|---|
| `outbox_relay.bootstrap_initialized` | INFO | `workers/bootstrap.py:342` | app_env, batch_size, poll_interval_ms, stream_trim_interval_s, stream_trim_margin_s, stream_maxlen |
| `summary_delivery_name_unavailable` | WARNING | `workers/bootstrap.py:958` | document_id + traceback |
| `summary_delivery_declined` | INFO | `workers/bootstrap.py:1090` | conversation_id, document_id, reason |
| `summary_failure_delivery_declined` | INFO | `workers/bootstrap.py:1182` | conversation_id, document_id, reason |
| `knowledge_worker.bootstrap_initialized` | INFO | `workers/bootstrap.py:1503` | app_env |
| `media_worker.bootstrap_initialized` | INFO | `workers/bootstrap.py:1894` | app_env |
| `memory_worker.bootstrap_initialized` | INFO | `workers/bootstrap.py:2111` | app_env |
| `worker.signal_handler_unavailable` | INFO | `workers/lifecycle.py:103` | signal |
| `worker.drain_started` | INFO | `workers/lifecycle.py:146` | timeout_s |
| `worker.drain_loop_failed` | ERROR | `workers/lifecycle.py:158` | + traceback |
| `worker.drain_complete` | INFO | `workers/lifecycle.py:160` | — |
| `worker.drain_timed_out` | WARNING | `workers/lifecycle.py:162` | timeout_s |
| `worker.shutdown_signal` | INFO | `workers/lifecycle.py:169` | signal |
| `stream_trim.stopped` | ERROR | `workers/outbox_relay.py:72` | + traceback |
| `summary_usage_capture_failed` | WARNING | `workers/summary_metering.py:265` | provider, tokens + traceback |

### أدوات التشغيل (حاويات منفصلة لمرة واحدة، لا تظهر في سجل `app`) — 38

| الحدث | المستوى | الموضع | الحقول |
|---|---|---|---|
| `ops.backup.spool_unlink_failed` | WARNING | `ops/backup.py:902` | file, error |
| `ops.backup.wal_follow_started` | INFO | `ops/backup.py:942` | interval_s |
| `ops.backup.wal_shipped` | INFO | `ops/backup.py:947` | expr:dict(result, segments=None) |
| `ops.backup.wal_ship_failed` | ERROR | `ops/backup.py:950` | error |
| `ops.dlq.requeue_skipped` | ERROR | `ops/dlq.py:241` | stream, dlq_entry_id, reason |
| `ops.dlq.requeued` | INFO | `ops/dlq.py:251` | stream, dlq_entry_id, new_entry_id |
| `ops.dlq.purged` | INFO | `ops/dlq.py:264` | stream, requested, deleted |
| `ops.embedding_migration.adopt` | INFO | `ops/embedding_migration.py:651` | count |
| `ops.embedding_migration.dropped` | WARNING | `ops/embedding_migration.py:775` | revision, count |
| `ops.explain_hot_paths.reported` | INFO | `ops/explain_hot_paths.py:1125` | workspace_id, statements, failing, unproven, skipped |
| `ops.load_seed.written` | INFO | `ops/load_seed.py:1430` | seed_id, stores, workspaces, seconds |
| `ops.load_seed.purged` | INFO | `ops/load_seed.py:1480` | seed_id |
| `online_ddl.dropping_invalid_index` | WARNING | `ops/online_ddl.py:121` | index |
| `online_ddl.backfill_batch` | INFO | `ops/online_ddl.py:194` | table, batch, rows, total |
| `ops.payload_indexes.collection` | INFO | `ops/payload_indexes.py:193` | expr:payload |
| `provision.migrating` | INFO | `ops/provision.py:434` | revision, version_schema |
| `provision.lock_acquired` | INFO | `ops/provision.py:582` | key, waited_ms |
| `provision.complete` | INFO | `ops/provision.py:634` | chains, grants |
| `ops.purge.candidate_skipped_no_actor` | WARNING | `ops/purge.py:289` | workspace_id, target_user_id |
| `ops.purge.workspace_purged` | INFO | `ops/purge.py:582` | workspace_id, dry_run |
| `ops.replay.published` | INFO | `ops/replay.py:699` | published, vanished, failed |
| `ops.retention.swept` | INFO | `ops/retention.py:392` | expr:payload |
| `ops.revoked` | INFO | `ops/revoke.py:62` | subject, ttl_s |
| `rotate_transit.row_changed_concurrently` | INFO | `ops/rotate_transit.py:261` | table, id |
| `ops.rotate_transit.cycle_ok` | INFO | `ops/rotate_transit.py:331` | expr:payload |
| `ops.rotate_transit.cycle_broken` | ERROR | `ops/rotate_transit.py:333` | expr:payload |
| `ops.rotate_transit.swept` | INFO | `ops/rotate_transit.py:344` | expr:payload |
| `ops_scheduler.job_refused` | ERROR | `ops/scheduler.py:431` | task, reason |
| `ops_scheduler.job_started` | INFO | `ops/scheduler.py:435` | task, argv, cycle, attempt |
| `ops_scheduler.job_succeeded` | INFO | `ops/scheduler.py:442` | task, duration_s |
| `ops_scheduler.job_failed` | ERROR | `ops/scheduler.py:449` | task, error, attempt |
| `ops_scheduler.tick_failed` | ERROR | `ops/scheduler.py:476` | + traceback |
| `ops_scheduler.tick_recovered` | INFO | `ops/scheduler.py:480` | — |
| `ops_scheduler.started` | INFO | `ops/scheduler.py:641` | jobs, offset_s |
| `ops_scheduler.stopped` | INFO | `ops/scheduler.py:652` | — |
| `ops.slow_queries.reset` | INFO | `ops/slow_queries.py:409` | scope |
| `ops.slow_queries.reported` | INFO | `ops/slow_queries.py:432` | rows, order_by, dealloc, censored |
| `ops.table_growth.vacuumed` | INFO | `ops/table_growth.py:353` | table |


</div>
