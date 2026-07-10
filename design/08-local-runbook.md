# دليل التشغيل المحلي (Local Runbook)

> التنسيق: Docker Compose (D‑26). البنية التحتية فقط من القائمة المعتمدة: PostgreSQL · PgBouncer · Redis · MinIO · Qdrant · Vault · Nginx + App/Workers (FastAPI/Uvicorn).
> الأوامر مرجعية؛ تُملأ ملفات الإعداد الفارغة (`docker-compose.yml`, `Dockerfile`, `.env`, …) عند بدء التنفيذ.

## 1) المتطلبات المسبقة
- Docker + Docker Compose v2
- Python 3.12+ و `uv` (أو `pip`) لتشغيل الأدوات محلياً خارج الحاوية
- `make` (اختياري، لاختصار الأوامر)

## 2) طوبولوجيا Compose (الخدمات)
| الخدمة | الصورة/المصدر | المنفذ | الدور |
|--------|----------------|--------|-------|
| `nginx` | nginx | 80/443 | Reverse proxy · TLS · WS upgrade |
| `app` | Dockerfile (هذا المستودع) | 8000 | FastAPI/Uvicorn (Gunicorn+UvicornWorker) — قابل للتوسّع |
| `worker` | نفس الصورة، أمر مختلف | — | مستهلكو Streams (knowledge/media/memory) |
| `outbox-relay` | نفس الصورة، أمر مختلف | — | مُرحّل Outbox (نسخة واحدة) |
| `postgres` | postgres:16 | 5432 | قاعدة البيانات (RLS) |
| `pgbouncer` | pgbouncer | 6432 | Transaction pooling |
| `redis` | redis:7 | 6379 | Cache + Streams |
| `minio` | minio | 9000/9001 | تخزين الكائنات |
| `qdrant` | qdrant | 6333 | مخزن المتجهات |
| `vault` | hashicorp/vault | 8200 | الأسرار + Transit (وضع dev محلياً) |

> التطبيق يصل Postgres **عبر PgBouncer** (`DATABASE_URL → pgbouncer:6432`)، لا مباشرةً.
>
> ⚠️ **إلزامي (`OPS‑02`)**: خلف PgBouncer بنمط **Transaction Pooling**، يجب أن يحمل `DATABASE_URL`/إعداد محرّك **asyncpg** الوسيط `statement_cache_size=0` وتعطيل **Prepared Statements المُسمّاة** (مثال: `?statement_cache_size=0` في السلسلة، أو `connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0}` عند بناء المحرّك) — وإلا تعطّلت المعاملات المجمّعة.

## 3) الإقلاع السريع
```bash
# 1) الإعداد
cp .env.example .env          # املأ القيم غير السرّية؛ الأسرار تُبذَر في Vault

# 2) رفع البنية التحتية أولاً
docker compose up -d postgres pgbouncer redis minio qdrant vault

# 3) تهيئة Vault (dev): تفعيل KV + Transit وبذر الأسرار
docker compose exec vault sh -c '
  vault secrets enable -path=secret kv-v2 || true
  vault secrets enable transit || true
  vault write -f transit/keys/tenant-secrets   # مفتاح موحّد: credentials + integrations (SEC-07)
  vault kv put secret/db password=app
  vault kv put secret/minio access_key=minio secret_key=minio12345
'

# 3.1) مصادقة AppRole (D‑22) — للنشر غير المحلي
#   محلياً (dev mode) يكفي Token جذر محلي عبر VAULT_TOKEN؛ AppRole أدناه إلزامي للبيئات غير المحلية.
docker compose exec vault sh -c '
  vault auth enable approle || true
  vault write auth/approle/role/app \
    token_policies=app token_ttl=1h token_max_ttl=4h secret_id_ttl=24h
  vault read  auth/approle/role/app/role-id            # → التقط role_id
  vault write -f auth/approle/role/app/secret-id       # → التقط secret_id
'
#   ضع القيمتين في .env: VAULT_ROLE_ID=... و VAULT_SECRET_ID=...

# 4) إنشاء الدلو في MinIO ومجموعات Qdrant تُنشأ عند أول استخدام
#    (بذر الدلو عبر mc أو عند الإقلاع من التطبيق)

# 5) الهجرات (تُنشئ 10 schemas وحدات + platform + جداول + RLS + دور app_rw + بذر الأدوار)
#    schemas المحجوزة (scheduling/sandbox/runs) لا تُنشأ في v1
docker compose run --rm app alembic upgrade head

# 6) تشغيل التطبيق والعمّال
docker compose up -d app worker outbox-relay nginx

# 7) فحص الصحّة
curl -s http://localhost/health           # liveness
curl -s http://localhost/health/ready     # readiness (DB/Redis/…)
```

## 4) أوامر العمّال (نفس الصورة، أمر مختلف)
```bash
# مستهلك مجرى وحدة (D‑20)
python -m app.workers.knowledge_worker
python -m app.workers.media_worker
python -m app.workers.memory_worker
# مُرحّل Outbox (نسخة واحدة فقط)
python -m app.workers.outbox_relay
```
> نقطة الدخول الموحّدة `app/workers/main.py` تختار العامل حسب وسيط/متغيّر بيئة.

## 5) دورة التطوير
```bash
uv sync                         # تثبيت التبعيات (pyproject.toml)
ruff format . && ruff check .    # تنسيق + فحص
mypy src                         # فحص الأنواع (strict)
pytest -q                        # الاختبارات (unit/integration/architecture)
lint-imports                     # فرض عقود import-linter (.importlinter)
alembic revision -m "..."        # هجرة جديدة (راجع ألا تُنشئ FK عابراً بين schemas)
```

## 6) البذر والتحقّق اليدوي
1. احصل على Firebase ID Token (مشروع اختباري) وضعه في `Authorization: Bearer`.
2. أول طلب مُصادَق ⇒ بذر المستخدم JIT + Workspace + دور `owner`.
3. جرّب التدفّق الكامل:
   ```
   POST /api/v1/files            → upload_url ثم ارفع إلى MinIO ثم POST /files/{id}/complete
   (حدث files.file.uploaded.v1 → knowledge_worker يفهرس → knowledge.document.indexed.v1)
   POST /api/v1/agents/rag_agent/invoke  (Accept: text/event-stream)  → بثّ الردّ
   POST /api/v1/media/jobs        → 202 ثم GET /media/jobs/{id} حتى succeeded
   POST /api/v1/integrations/connections → authorize_url ثم callback (السرّ مُعمّى عبر Transit) → GET /integrations/tools
   GET  /api/v1/usage             → ملخّص الاستهلاك (يُلتقَط عبر منفذ المُنسِّق، لا Streams)
   ```

## 7) استكشاف الأعطال
| العرض | الفحص |
|-------|-------|
| 401 دائماً | صلاحية توكن Firebase / `FIREBASE_PROJECT_ID` |
| صفر صفوف رغم وجود بيانات | لم يُضبط `app.workspace_id` (RLS) — تحقّق من `ExecutionContext`/`rls.py` |
| الأحداث لا تصل العمّال | حالة `outbox-relay`؛ `published_at` في `platform.outbox`؛ طول المجرى في Redis |
| مهام عالقة | نموّ `stream.<m>.dlq`؛ راجع سبب الفشل والمحاولات |
| رفض اتصال Postgres | مرّ عبر PgBouncer (6432) لا 5432؛ حجم التجمّع |
| فشل الأسرار | صحّة AppRole (`VAULT_ROLE_ID`/`VAULT_SECRET_ID`) وتفعيل Transit ووجود مفتاح `tenant-secrets` |
| فشل ربط موصّل | `OAUTH_REDIRECT_BASE_URL`؛ صلاحية `refresh_token`؛ نقل MCP (http/sse فقط) |
| رفض عملية بـ429 usage | تجاوز حصّة/ميزانية — راجع `GET /usage` و`/usage/limits` (`USAGE_DEFAULT_LIMITS`) |
