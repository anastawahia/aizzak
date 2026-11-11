# نموذج البيانات ومخطط قاعدة البيانات

> PostgreSQL · **Schema لكل وحدة** (`DD‑01`) · **UUIDv7** (`DD‑02`) · أعمدة قياسية (`DD‑03`) · **RLS + ترشيح تطبيقي** (`DD‑04`).
> لا FK عابر بين schemas الوحدات؛ المرجعية بالـ`id` فقط. FK مسموح داخل schema الوحدة.

## 0) الاصطلاحات

**Schemas (إحدى عشرة وحدة v1 + `electrical` لـWireTwin):** `spaces · workspace · access · credentials · conversations · memory · files · knowledge · media · integrations · usage · electrical` + `platform` (بنية الأحداث). `electrical` أحدثها (§2.12).
> `spaces` أحدثها (‏§2.11، [`spaces-backend-plan.md`](../spaces-backend-plan.md))، وتُذكر **أوّلاً** كما في `_MODULE_SCHEMAS` و`MIGRATION_CHAINS` في [`ops/provision.py`](../../src/app/ops/provision.py): ثلاث سلاسل تملأ `space_id` بالرجوع إلى صفوف هذا الجدول، فوجودُه يسبقها بالضرورة (‏§6).
> **محجوزة لا مُنشأة في v1** (`FR‑110`, DAT‑01): `scheduling · sandbox · runs` — تُضاف بهجرة مستقلّة عند اعتمادها.

**قالب الأعمدة القياسية** (يُطبَّق حسب `DD‑03`):
```sql
id           uuid        PRIMARY KEY,                 -- UUIDv7 من التطبيق
workspace_id uuid        NOT NULL,                    -- على كل جدول مستأجَر
created_at   timestamptz NOT NULL DEFAULT now(),
updated_at   timestamptz NOT NULL DEFAULT now(),      -- + trigger touch
deleted_at   timestamptz NULL,                        -- محتوى المستخدم فقط
version      integer     NOT NULL DEFAULT 1            -- aggregates قابلة للتعديل
-- created_by uuid NULL                               -- (DAT‑05) حيث يُفيد تتبّع المُنشئ (اختياري لكل جدول)
```

**دالة touch (تحديث `updated_at`):**
```sql
CREATE OR REPLACE FUNCTION platform.touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END $$ LANGUAGE plpgsql;
-- CREATE TRIGGER trg_touch BEFORE UPDATE ON <t> FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();
```

---

## 1) ERD — نظرة كلية

```mermaid
erDiagram
  WORKSPACE ||--o{ USER : owns
  WORKSPACE ||--o{ ROLE_ASSIGNMENT : scopes
  USER      ||--o{ ROLE_ASSIGNMENT : has
  WORKSPACE ||--o{ CREDENTIAL : holds
  WORKSPACE ||--o{ SPACE : contains
  SPACE     ||..o{ FILE : "owns (by id, no FK)"
  SPACE     ||..o{ CONVERSATION : "owns (by id, no FK)"
  SPACE     ||..o{ DOCUMENT : "owns (by id, no FK)"
  WORKSPACE ||--o{ CONVERSATION : contains
  CONVERSATION ||--o{ MESSAGE : has
  WORKSPACE ||--o{ MEMORY_ITEM : contains
  WORKSPACE ||--o{ FILE : contains
  WORKSPACE ||--o{ DOCUMENT : contains
  DOCUMENT  ||--o{ CHUNK : split_into
  FILE      ||..o{ DOCUMENT : "source (by id, no FK)"
  WORKSPACE ||--o{ MEDIA_JOB : contains
  WORKSPACE ||--o{ CONNECTION : contains
  CONNECTION ||--o{ MCP_SERVER : exposes
  WORKSPACE ||--o{ USAGE_RECORD : meters
  WORKSPACE ||--o{ USAGE_LIMIT : governs
  WORKSPACE ||--o{ OUTBOX : emits
```
> `FILE ..o{ DOCUMENT` منقّطة: علاقة **منطقية عبر الوحدات** (بالـ`id`)، بلا FK فيزيائي (`DD‑01`).
> **`SPACE` ليست مستأجرًا ثانيًا** بل **محور ملكيّة داخل المستأجر**: الأسهم الثلاثة الخارجة منها منقّطة للسبب نفسه (‏`space_id` مرجعٌ منطقيّ عبر المخطّطات)، والمستأجر يبقى `WORKSPACE` وحده — RLS على `workspace_id` وحده، والترشيح بالوحدة تطبيقيّ في `WHERE` (‏§3).
> `CONNECTION`/`MCP_SERVER` في schema `integrations`؛ `USAGE_RECORD`/`USAGE_LIMIT` في schema `usage`. لا FK بينها وبين بقية الوحدات — المرجعية بالـ`id` فقط (`DAT‑02`).

---

## 2) DDL لكل وحدة

### 2.1 `workspace`
```sql
CREATE SCHEMA workspace;

CREATE TABLE workspace.workspaces (
  id            uuid PRIMARY KEY,
  owner_user_id uuid NOT NULL,
  name          text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','archived')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  version       integer NOT NULL DEFAULT 1,
  purged_at     timestamptz,                     -- BE-ADM-014: set only by `python -m app.ops.purge`
  CONSTRAINT workspaces_purged_at_check CHECK (purged_at IS NULL OR status = 'archived')
);
-- ملاحظة: workspaces جدول جذر المستأجر ⇒ لا RLS بعمود workspace_id (هو المستأجر نفسه)؛
-- يُقيَّد بالوصول عبر id + AuthZ.
-- BE-ADM-014: `purged_at` غير NULL يعني أنّ محتوى مساحة العمل (كلّ الجداول المستأجَرة عبر
-- عشر وحدات، زائدًا Qdrant/MinIO) قد مُحي نهائيًا بعد نافذة احتفاظٍ (30 يومًا افتراضيًا) تلت
-- تدمير آخر مستخدمٍ فيها (`workspace.users.status='deleted'`)؛ الصفّ نفسه يبقى (لا حذف)
-- لأنّ `platform.admin_audit_log` يشير إليه.

CREATE TABLE workspace.users (
  id           uuid PRIMARY KEY,                 -- مشتق من firebase_uid عبر جدول ربط
  workspace_id uuid NOT NULL,
  firebase_uid text NOT NULL UNIQUE,
  email        text NOT NULL,
  display_name text,
  status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled','deleted')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),      -- + trigger touch (قابل للتعديل عبر status)
  deleted_at   timestamptz,                             -- BE-ADM-006
  version      integer NOT NULL DEFAULT 1,
  CONSTRAINT fk_user_ws FOREIGN KEY (workspace_id) REFERENCES workspace.workspaces(id),
  CONSTRAINT users_deleted_at_check CHECK ((status = 'deleted') = (deleted_at IS NOT NULL))
);
-- ملاحظة: `deleted` حالةٌ طرفيّة لا رجعة منها، والصفّ **شاهدُ قبرٍ** لا حساب: تُمحى
-- `email` و`display_name` في المعاملة نفسها وتُسحب كل إسناداته في `access.role_assignments`.
-- الصفّ يبقى لأن `platform.admin_audit_log` يشير إليه بمفتاحٍ أجنبيّ NOT NULL بلا ON DELETE،
-- فحذفُه يأخذ معه سجلَّ الحذف ذاته. و`firebase_uid` يبقى عمداً: بدونه يعود صاحبُ الهُويّة
-- نفسها فيُزوَّد بمساحة عملٍ جديدة، فيصير «الحذف» إعادةَ ضبط.
CREATE UNIQUE INDEX uq_users_owner ON workspace.users(workspace_id) WHERE status='active';
-- BE-ADM-014: `app.ops.purge.find_candidates` يمسح هذا العمود بحثًا عن أحدث شاهد قبرٍ
-- لكلّ مساحة عمل؛ فهرسٌ جزئيّ (الأغلبيّة الساحقة من الصفوف لم تُحذف قط، فلا داعي لفهرستها).
CREATE INDEX ix_users_deleted_at ON workspace.users(deleted_at) WHERE deleted_at IS NOT NULL;
```

### 2.2 `access`
```sql
CREATE SCHEMA access;

CREATE TABLE access.role_assignments (
  id           uuid PRIMARY KEY,
  workspace_id uuid NOT NULL,
  user_id      uuid NOT NULL,                    -- مرجع منطقي لـ workspace.users.id
  role         text NOT NULL CHECK (role IN ('owner','admin','member','viewer','platform_admin')),
  granted_by   uuid,
  created_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_assignment UNIQUE (workspace_id, user_id, role)
);
CREATE INDEX ix_ra_ws_user ON access.role_assignments(workspace_id, user_id);
-- Permission كتالوج ثابت بالكود (لا جدول) — انظر 05-rbac-config-secrets.md
```

### 2.3 `credentials`
```sql
CREATE SCHEMA credentials;

CREATE TABLE credentials.credentials (
  id             uuid PRIMARY KEY,
  workspace_id   uuid NULL,                       -- NULL ⇔ scope=platform
  provider       text NOT NULL,
  scope          text NOT NULL CHECK (scope IN ('platform','user')),
  label          text,
  ciphertext_ref text NOT NULL,                   -- ناتج Vault Transit (لا سرّ خام)
  key_id         text NOT NULL,                   -- معرّف مفتاح التشفير في Vault
  status         text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
  created_by     uuid,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  version        integer NOT NULL DEFAULT 1,
  CONSTRAINT ck_scope_ws CHECK ((scope='user' AND workspace_id IS NOT NULL)
                             OR (scope='platform' AND workspace_id IS NULL))
);
CREATE INDEX ix_cred_ws_provider ON credentials.credentials(workspace_id, provider) WHERE status='active';
```

### 2.4 `conversations`
```sql
CREATE SCHEMA conversations;

CREATE TABLE conversations.conversations (
  id           uuid PRIMARY KEY,
  workspace_id uuid NOT NULL,
  space_id     uuid NULL,                        -- §2.11 · مرجع منطقي لـ spaces.spaces.id (⇐ NOT NULL في الصفّ ٨‑ب)
  agent_key    text NOT NULL,                     -- slug الوكيل أو workflow_key
  kind         text NOT NULL DEFAULT 'agent' CHECK (kind IN ('agent','workflow')),
  title        text,
  model_route  text NULL,                        -- مفتاح توجيه من جدول D‑16 (لا اسم موديل خام)؛ NULL ⇒ يُحلّ بمفتاح الوكيل
  pending_clarification jsonb NULL,               -- ب‑9 · أسماءُ الملفّات التي سألها الدورُ السابق **بترتيب عرضها**؛ NULL ⇒ لا سؤالَ معلَّق
  created_by   uuid,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz NULL,
  version      integer NOT NULL DEFAULT 1
);
CREATE INDEX ix_conv_ws_agent ON conversations.conversations(workspace_id, agent_key)
  WHERE deleted_at IS NULL;
-- خطّةُ السعة `2.4`: كان `ix_conv_space(space_id)` وحدَه، فكان `counts_by_space` يعيد
-- فحصَ كلّ صفٍّ على الكومة لأجل `workspace_id` (‏4,253 صفّاً · 173 مخزناً على المستأجر
-- الأكبر). والمستأجرُ يتصدّر لأنّ كلّ مسندٍ على `space_id` في كلّ محوّلٍ يحمله بجانبه
-- (‏DD‑04 الطبقة 2) — بديلٌ لا إضافة.
CREATE INDEX ix_conv_ws_space ON conversations.conversations(workspace_id, space_id)
  WHERE deleted_at IS NULL;
-- `pending_clarification` (‏ب‑9، ف‑1أ): الوكيل يسأل «أيّ ملفّ تقصد؟» ويعدّد المرشّحين، ثمّ **لا يسمع
-- الجواب** — فالدورُ التالي يُصنَّف من الصفر، والردودُ الثلاثة الممكنة كلُّها `content`. هذا العمود هو
-- الطرفُ الآخر من الحوار. **أسماءٌ لا مُعرِّفات**: المعروضُ أسماءٌ، والجوابُ عن معروض، و«الثاني» لا
-- يُقرأ إلّا على القائمة التي عُرضت فعلاً — ولذلك **الترتيبُ جزءٌ من القيمة**. ولماذا عمودٌ لا رسالةٌ
-- سابقة: `content` نصٌّ ومرفقاتٌ فقط، والأجزاء المُهيكَلة الثلاثة للرسالة (`citations` ث‑1، و`status` و`fallback` ث‑2) لا يحمل أيٌّ منها المرشّحين (‏§2.4 هنا و06 §4)، فاستخراجُ المرشّحين منها تحليلُ نثرٍ
-- عربيٍّ حرٍّ يُعيد الوكيلُ صياغتَه — في الموضع الوحيد الذي يُنتج فيه سوءُ التحليل **الملفَّ الخطأ**.
-- NULL وحدَه يعني «لا سؤالَ معلَّق» (‏لا `[]` بجانبه: تهجئتان لحالةٍ واحدة تُنسى إحداهما في الاستعلام
-- الذي يهمّ). لا فهرس — العمودُ إسقاطٌ لا شرط. ولا CHECK: ما يجعل الاسمَ ذا معنًى وجودُ ملفٍّ بهذا
-- الاسم في المتن، وهي حقيقةٌ في schema أخرى تتغيّر بلا هجرة، ومسارُ القراءة يحتمل اسماً اختفى ملفُّه.
-- والنيّةُ **تعيش دوراً واحداً**: الدورُ التالي يقرؤها ثمّ يكتب ما بقي معلَّقاً — ولا شيءَ عادةً.

CREATE TABLE conversations.messages (
  id              uuid PRIMARY KEY,
  conversation_id uuid NOT NULL,
  workspace_id    uuid NOT NULL,
  role            text NOT NULL CHECK (role IN ('user','assistant','system','tool')),
  content         jsonb NOT NULL,                 -- نص + مرفقات (file_id بالإشارة)
  token_count     integer,
  seq             integer NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  deleted_at      timestamptz NULL,                 -- حذف ناعم على مستوى الرسالة (FR‑81)
  citations       jsonb NULL,                       -- ث‑1 · لقطة مصادر جواب المساعد {document_id, file_name, page, chunk_id, rank} بترتيبها؛ NULL ⇒ لم يُسجَّل، '[]' ⇒ صرّح الوكيل بلا مصادر
  status          text NULL,                        -- ث‑2 · كيف انتهى الدور الذي كتب رسالة المساعد: 'complete' | 'partial'؛ NULL ⇒ لم يُسجَّل
  fallback        jsonb NULL,                       -- ث‑2 · {from:{provider,model}, to:{provider,model}} حين أجاب النموذج المحلي (6.4)؛ NULL ⇒ أجاب الأساسي أو لم يُسجَّل
  reply_to        uuid NULL,                        -- ث‑3 · معرّف رسالة المستخدم التي يجيبها هذا المساعد، يُكتب في الإدراج نفسه؛ NULL ⇒ لم يُسجَّل
  CONSTRAINT ck_messages_status CHECK (status IN ('complete','partial')),   -- أُضيف NOT VALID في 0008
  CONSTRAINT fk_msg_conv FOREIGN KEY (conversation_id) REFERENCES conversations.conversations(id),
  CONSTRAINT uq_msg_seq UNIQUE (conversation_id, seq)
);
-- `citations` (‏ث‑1): عمودٌ لا مفتاحٌ داخل `content`، لأنّ `content` وُعد به العميل مغلقاً (نصٌّ ومرفقاتٌ
-- فقط). كان للرسالة نصفٌ مُهيكَل غائب (ب‑10)، فصار لها ثلاثة أجزاء مُهيكَلة: `citations` (ث‑1) و`status` و`fallback` (ث‑2، أدناه). لقطةٌ وقت
-- الجواب (‏`file_name` لا يُحدَّث من `files`)، بترتيب الوكيل، وسقفها 50 استشهاداً يفرضه INV‑CV4 في `Conversation.append_message` و`_recordable_citations`
-- لا قيد في القاعدة. `NULL` ⇒ لم يُسجَّل (كلُّ الصفوف القديمة، بلا backfill)، و`'[]'` ⇒ صرّح الوكيل بأن
-- لا مصادر. لا فهرس (إسقاطٌ لا شرط) ولا CHECK. RLS والصلاحيات على مستوى الصف/الجدول فتغطّي العمود.
-- `status` و`fallback` (‏ث‑2، `conversations/0008_message_status`): عمودان لا مفتاحان داخل `content`، وعمودان
-- لا عمود واحد لأنّ الحالة والتحوّل متعامدان (`partial` مع نموذج محلي ممكن). تكتبهما **الشيفرة التي كتبت الرسالة**،
-- لا النص: الجملتان («انقطعت الإجابةُ…» وجملة التحوّل) باقيتان في النص لكل قارئ لا يعرف العمودين.
-- `status` نصٌّ لا نوعُ enum (المجموعة المغلقة يفرضها `ck_messages_status` و`domain.MessageStatus`)، و`fallback`
-- بشكل `final.fallback` نفسه بلا CHECK (قواعده في `domain.MessageFallback`). `NULL` ⇒ لم يُسجَّل: رسائل المستخدم
-- وكلّ صف قبل الترحيل وصفوف `load_seed`، بلا backfill ولا DEFAULT. `fallback` غير NULL يستلزم `status` غير NULL
-- (INV‑CV5، في الـ domain لا في القاعدة). القيد `NOT VALID` **ولا يُنفَّذ له VALIDATE عن قصد**: الصفوف القائمة كلّها
-- `NULL` بالبناء (العمود وُلد في الترحيل نفسه) والـ CHECK يمرّر `NULL`، فالمسح الكامل لأكبر جدول في المنصة تحت
-- `ACCESS EXCLUSIVE` لا يثبت شيئاً، وPostgres يفرض القيد على كل INSERT/UPDATE بعده. لا فهرس (إسقاطٌ لا شرط).
-- `reply_to` (‏ث‑3، `conversations/0009_message_reply_to`): معرّف رسالة المستخدم التي تجيبها رسالة المساعد، بنوع `id`
-- نفسه (`uuid`). يكتبه **الدور الذي كتب الرسالتين** (`_persist_reply` و`_persist_partial` فقط) في الإدراج نفسه، لأنّ ترتيب
-- `seq` لا يكفي حين يتداخل دوران (WebSocket، جهازان على خيط واحد). `NULL` ⇒ لم يُسجَّل: رسائل المستخدم وكلّ صف قبل
-- الترحيل ومخرجات خطوات الـworkflow وملخصات الـworker وصفوف `load_seed`، بلا backfill ولا DEFAULT. INV‑CV6: لرسائل
-- `assistant` وحدها (في الـdomain). **لا FK عن قصد**: فحص FK في Postgres يتجاوز RLS فلا يثبت أنّ السؤال من المساحة نفسها
-- ولا من المحادثة نفسها ولا أنّه رسالة `user`، ويقيّد ترتيب الحذف في `purge.py`. السلامة يفرضها المحوّل بإدراجٍ محروس
-- في عبارة واحدة (`INSERT … SELECT … WHERE EXISTS (رسالة user في المحادثة نفسها)`) تحت RLS نفسها، فيُرفض ما لا يطابق بـ`ValidationError`
-- بلا صف. يبقى المعرّف بعد حذف السؤال ناعماً (السؤال يغادر كل قراءة والرابط يبقى). لا فهرس (إسقاطٌ لا شرط) ولا CHECK،
-- والترحيل `ADD COLUMN … NULL` تعديل catalog فقط. RLS والصلاحيات بلا تغيير.

-- نطاق استرجاع الخيط: أي مستندات مساحة العمل يُجيب منها هذا الخيط (BE‑RAG‑005).
-- الصفّ **مرجع** لا امتلاك: الملف يبقى على مستوى مساحة العمل ويُفهرَس مرّة واحدة في
-- مجموعة Qdrant الواحدة لكل مساحة عمل، و`knowledge.documents` لا يعرف المحادثات (§2.7).
-- مجموعةٌ فارغة ⇒ النطاق الشامل، وهو سلوك كلّ خيطٍ سبق هذا الجدول (لا backfill).
CREATE TABLE conversations.conversation_files (
  conversation_id uuid NOT NULL,
  file_id         uuid NOT NULL,                     -- مرجع منطقي لـ files.files.id
  workspace_id    uuid NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (conversation_id, file_id),
  CONSTRAINT fk_convfile_conv FOREIGN KEY (conversation_id) REFERENCES conversations.conversations(id)
);
```

### 2.5 `memory`
```sql
CREATE SCHEMA memory;

CREATE TABLE memory.memory_items (
  id           uuid PRIMARY KEY,
  workspace_id uuid NOT NULL,
  agent_key    text NOT NULL,
  kind         text NOT NULL CHECK (kind IN ('semantic','episodic')),
  content      text NOT NULL,
  collection   text,                              -- Qdrant collection
  point_id     uuid,                              -- Qdrant point (NULL ريثما يُفهرَس)
  salience     real NOT NULL DEFAULT 0,
  created_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz NULL
);
CREATE INDEX ix_mem_ws_agent ON memory.memory_items(workspace_id, agent_key) WHERE deleted_at IS NULL;
```

### 2.6 `files`
```sql
CREATE SCHEMA files;

CREATE TABLE files.files (
  id           uuid PRIMARY KEY,
  workspace_id uuid NOT NULL,
  space_id     uuid NULL,                        -- §2.11 · مرجع منطقي لـ spaces.spaces.id (⇐ NOT NULL في الصفّ ٨‑ب)
  name         text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 255),
  content_type text NOT NULL,
  size_bytes   bigint NOT NULL CHECK (size_bytes >= 0),
  storage_key  text NOT NULL UNIQUE,              -- workspace_id/uuid
  checksum     text,                              -- sha256 hex
  status       text NOT NULL DEFAULT 'uploaded'
                 CHECK (status IN ('uploaded','scanning','ready','quarantined')),
  uploaded_by  uuid,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz NULL,
  version      integer NOT NULL DEFAULT 1
);
CREATE INDEX ix_files_ws ON files.files(workspace_id) WHERE deleted_at IS NULL;
ALTER TABLE files.files                                          -- س-29 القاعدة ١
  ADD COLUMN name_key text
    GENERATED ALWAYS AS (lower(normalize(name, NFC))) STORED;
-- خطّةُ السعة `2.4`: `INCLUDE (size_bytes)` يجعل مجموعَ الحصّة مسحاً فهرسيّاً فقط
-- (‏754 مخزناً ← 95)، و`ix_files_space(space_id)` حُذف لأنّ بادئةَ هذا الفهرس تخدمه
-- وتخدمه أفضل. **والحمولةُ تركب هذا الفهرس ولا تُعطى فهرساً منافساً**: فهرسٌ أضيقُ
-- يتشارك البادئةَ `(workspace_id, space_id)` يسرق خطّةَ بحثِ الأسماء فيسقط `name_key`
-- من `Index Cond` إلى `Filter` — مُقاسٌ، وأمسكه حارسُ الخطّة الحيّ.
CREATE INDEX ix_files_space_name
  ON files.files(workspace_id, space_id, name_key)
  INCLUDE (size_bytes)
  WHERE deleted_at IS NULL;
ALTER TABLE files.files                                          -- PLT‑01 · files/0005_file_multipart
  ADD COLUMN upload_id  text    NULL,                            -- معرّف رفع S3 المجزأ (NULL لرفع PUT واحد)
  ADD COLUMN part_bytes integer NULL;                            -- حجم الجزء الذي قُطع به الرفع
ALTER TABLE files.files ADD CONSTRAINT ck_files_multipart_shape CHECK (
  (upload_id IS NULL AND part_bytes IS NULL)
  OR (upload_id IS NOT NULL
      AND char_length(upload_id) BETWEEN 1 AND 1024
      AND part_bytes IS NOT NULL
      AND part_bytes >= 5242880)
);
ALTER TABLE files.files                                          -- PLT‑26 · files/0006_file_tags
  ADD COLUMN tag_system     text NULL,                           -- وسم النظام (مرجع معتم)
  ADD COLUMN tag_equipment  text NULL,                           -- وسم المعدّة
  ADD COLUMN tag_department text NULL;                           -- وسم القسم (department، لا قسم الوحدة)
ALTER TABLE files.files ADD CONSTRAINT ck_files_tags_shape CHECK (
      (tag_system     IS NULL OR char_length(tag_system)     BETWEEN 1 AND 128)
  AND (tag_equipment  IS NULL OR char_length(tag_equipment)  BETWEEN 1 AND 128)
  AND (tag_department IS NULL OR char_length(tag_department) BETWEEN 1 AND 128)
);
```
> **`upload_id` و`part_bytes` وصفٌ لجلسة الرفع في التخزين، يُثبَّتان عند التسجيل ولا يتغيّران** (‏INV‑F5)، كـ`content_type`: يكتبهما `add` ولا يمسّهما `save`. إمّا أن يكونا معاً `NULL` (ملفّ رُفع بطلب PUT واحد) أو معاً مضبوطَين، والقيدُ هو ضمانُ قاعدة البيانات لكاتبٍ يتجاوز الكيان. و5 MiB (‏5242880) هو الحدّ الأدنى لجزءٍ في S3 لكلّ جزءٍ عدا الأخير. لا فهرس: لا استعلام يبحث بـ`upload_id`، والقراءة بالمفتاح الأساسي. والعمودان قابلان لـ`NULL` بلا قيمةٍ افتراضية فالإضافة بيانات وصفية فقط، والشيفرة السابقة تعمل على المخطّط الجديد. وتراجعُ الترحيل يُسقط معرّفات الرفوع الجارية فلا تكتمل.
> **الوسوم الثلاثة وصفٌ للملفّ لا لبايتاته، وهي قابلةٌ للتغيير** (‏INV‑F6)، بخلاف `upload_id` و`part_bytes`: يكتبها `add` ويكتبها `save` أيضًا (يعيد `File.retag` كتابتها كما تعيد `rename` كتابة `name`)، وتبقى كما قُرئت لكلّ مستدعٍ آخر. الوسمُ إمّا `NULL` (غائب) أو نصٌّ من 1 إلى 128 محرفًا، و`NULL` هو الصيغة الوحيدة للغياب — **لا `''`** — والقيدُ ضمانُ قاعدة البيانات لكاتبٍ يتجاوز الكيان (`FileTags` يرفض الفارغ). والقيمةُ مرجعٌ معتمٌ تُقارَن حرفيًّا بلا طيّ حالة. وهي الشطر الثاني من قاعدة الاستبدال بالاسم (‏س‑29 القاعدة ١): يتطابق الملفّان إذا تطابق `name_key` **والوسوم الثلاثة معًا**، والغائبُ لا يطابق إلا غائبًا، فكلّ ملفٍّ قائمٌ بلا وسوم يتبع القاعدة كما كانت. ويُبنى الشرطُ بـ`IS NOT DISTINCT FROM` في `live_namesakes`، وبنفيه `IS DISTINCT FROM` في `live_name_conflicts` (الملفّات التي لن يمسّها الاستبدال)، **لا** `NOT (col = value)`: مع `NULL` يصير النفيُ `NULL` فيُسقط الصفَّ الذي تختلف وسومه. **ولا فهرس** (‏INC1‑D‑4): الشرطُ مُرشِّحٌ بعد البحث بـ`ix_files_space_name` الذي يضيّق إلى صفٍّ أو صفّين، و`IS [NOT] DISTINCT FROM` ليس عاملَ بحثٍ في btree أصلًا، وفهرسٌ رابع يتشارك البادئةَ قد يسرق خطّةَ بحثِ الأسماء. والحارسُ الحيّ يبقى أخضر مع ملفّاتٍ موسومة. والأعمدةُ قابلةٌ لـ`NULL` بلا قيمةٍ افتراضية فالإضافة بيانات وصفية فقط. **وتراجعُ الترحيل يُسقط الوسوم كلّها**: يعود كلّ ملفّ «بلا وسوم» ويعود الاستبدالُ بالاسم وحده، فقد يحذف رفعٌ لاحقٌ ملفًّا كان الوسمُ يحميه — فالأولى ترك الأعمدة.
> **`ix_files_space_name` فهرسُ بحثٍ لا قيدُ تفرّد، وهذا فرقٌ في القرار لا في المخطّط.** للوحدات الاسمُ المكرَّر **خطأ** (`ux_spaces_ws_name` ⇒ `23505` ⇒ `spaces.duplicate_name`)، وللملفّات هو **استبدال** (‏س-29 القاعدة ١، قرار المالك 2026‑08‑25): الجديد يُرفع أوّلًا ثمّ يُحذف القديم بفهرسه. وبين الإدراج والوسم يكون **كلاهما حيًّا** — فقيدُ التفرّد كان سيرفض الإدراج الذي يأمر به القرار. فالتفرّد **حالةٌ يتقارب إليها التطبيق** لا لازمةٌ تؤكّدها القاعدة.
> ونصفا التعبير لازمان: `lower` قاعدةُ `ux_spaces_ws_name` («‏Report» و«‏report» اسمٌ واحدٌ لإنسان)، و`normalize(.., NFC)` هو ما يجعلها صحيحةً **للعربيّة** — الاسمُ نفسه على لوحتَي مفاتيح يختلف بعلامات التركيب وحدها، و`lower` لا تفعل له شيئًا.
> **‏و**عمودٌ مولَّدٌ مخزَّن لا فهرسُ تعبير، وهذا قياسٌ لا تفضيل**: `lower` و`normalize` `IMMUTABLE` لكنّهما **ليستا `LEAKPROOF`**، والمخطِّط لا يُقيّم دالّةً غير آمنة التسرّب قبل شرط أمن الصفّ — فعلى جدولٍ `FORCE ROW LEVEL SECURITY` **لا يصير تعبيرُ الفهرس مفتاحَ بحثٍ أبدًا**، بل مُرشِّحًا بعد الحاجز (‏مقيسًا بـ`EXPLAIN` على الوجهين). والعمودُ المخزَّن يحوّل المقارنة إلى `texteq` وهي `LEAKPROOF`، فتعود شرطَ فهرس.** وهي القاعدةُ نفسها على كلّ جدولٍ ذي RLS في هذا المخطّط، لا خصوصيّةَ هنا. والأثرُ عمليٌّ لا نظريّ: `max_files_per_workspace = 10_000`، والشرطُ يجري على **كلّ إتمام رفعٍ وكلّ إعادة تسمية**.
> و`GENERATED ALWAYS … STORED` يعني أنّ PostgreSQL يشتقّه لا التطبيق، فلا ينحرف عن الاسم الذي يلخّصه؛ و`SqlFileRepository` لا يذكره في `INSERT` ولا في `UPDATE`. والوسيطُ يُطبَّع في **SQL** أيضًا (`name_key = lower(normalize($1, NFC))`، يطويه المخطِّط ثابتًا) — فلا اتّفاقَ بين بايثون وSQL يمكن أن ينحرف.
> **`ix_files_space_name` هو فهرس الحصّة قبل أن يكون فهرس السرد:** مجموع `size_bytes` لوحدةٍ واحدة يُقرأ **تحت قفلٍ** في مسار **تسجيل كلّ رفع** (‏§2.11 والحدّ في [`07 §4`](07-nfr-slo.md#4-الحدود-الرقمية-limits))، فمدّتُه مدّةُ قفل. وقبل خطوة السعة `2.4` كان `size_bytes` في غير فهرسٍ على هذا الجدول، فكان المجموعُ يجلب **كلَّ** صفوف الوحدة من الكومة لإنتاج رقمٍ واحد (‏8,506 صفّاً · 754 مخزناً على المستأجر الأكبر). و`INCLUDE (size_bytes)` يجعله مسحاً فهرسيّاً فقط بـ95 مخزناً. **ويبقى O(ملفّات الوحدة)**: الحمولةُ تجعل القراءةَ رخيصةً لا ثابتة، والشكلُ الثابت عدّادٌ مخزَّن يرفضه [`07 §4`](07-nfr-slo.md#4-الحدود-الرقمية-limits) صراحةً.

### 2.7 `knowledge`
```sql
CREATE SCHEMA knowledge;

CREATE TABLE knowledge.documents (
  id           uuid PRIMARY KEY,
  workspace_id uuid NOT NULL,
  space_id     uuid NULL,                         -- §2.11 · يُورَث من وحدة الملفّ عبر الحدث، لا من طلب (⇐ NOT NULL في ٨‑ب)
  file_id      uuid NOT NULL,                     -- مرجع منطقي لـ files.files.id
  status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','indexing','indexed','failed')),
  chunk_count  integer NOT NULL DEFAULT 0,
  error        text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  version      integer NOT NULL DEFAULT 1
);
CREATE INDEX ix_doc_ws_status ON knowledge.documents(workspace_id, status);
-- ⚠️ `ix_kndoc_space` **كامل لا جزئيّ**، وحده من فهارس الوحدة الثلاثة: هذا الجدول
-- لا يحمل `deleted_at` أصلاً (المستند يُتلَف صلبًا، INV‑K4)، فشرط `WHERE deleted_at IS NULL`
-- لا يُصرَّف هنا. والاختبار الحيّ يوكّد **غياب** العمود بجانب الفهرس كي لا «يُصلِح»
-- قارئٌ لاحقٌ التفاوتَ إلى خطأ صياغة.
CREATE INDEX ix_kndoc_space ON knowledge.documents(space_id);
-- خطّةُ السعة `2.4`: `ids_for_files` يحلّ معرّفاتِ ملفّاتِ المتّصل إلى مستندات **قبل** بحث
-- المتّجهات (مسار RAG المقيَّد بملفّات) وعلى كلّ تسجيلِ مستند. ولم يكن `file_id` مفهرساً،
-- فكان أقربُ ما يجده المخطِّطُ هو `ix_doc_ws_status` — أي مجموعةُ مستندات المستأجر كاملةً
-- ثمّ ترشيح: 17,012 صفّاً لأجل 20 على المستأجر الأكبر. وليس فهرسَ تفرّدٍ: `INV‑K3` يرفض
-- ذلك أصلاً (ملفٌّ له أكثرُ من مستندٍ عبر إعادة الفهرسة).
CREATE INDEX ix_doc_ws_file ON knowledge.documents(workspace_id, file_id);

CREATE TABLE knowledge.chunks (
  id           uuid PRIMARY KEY,
  document_id  uuid NOT NULL,
  workspace_id uuid NOT NULL,
  seq          integer NOT NULL,
  text         text NOT NULL,
  token_count  integer,
  collection   text,
  point_id     uuid,                              -- Qdrant
  created_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_chunk_doc FOREIGN KEY (document_id) REFERENCES knowledge.documents(id),
  CONSTRAINT uq_chunk_seq UNIQUE (document_id, seq)   -- Idempotency للاستيعاب
);
-- ⚠️ خطّةُ السعة `2.4`، وهو أكبرُ ما وجدَته: توسيعُ الآباء (`P‑34`) على **المسار المتزامن
-- للإجابة** يحلّ كلَّ `point_id` أعادَه Qdrant إلى مقطعه، و`point_id` لم يكن في أيّ فهرس —
-- فكان **مسحاً تتابعيّاً متوازياً لهذا الجدول كلِّه** في كلّ استرجاع: 1,000,758 صفّاً
-- و125,390 مخزناً و111 ms لتُعيد الخمسين المطلوبة. الفهرسُ يجعلها 0.142 ms و51 مخزناً.
CREATE INDEX ix_chunks_ws_point ON knowledge.chunks(workspace_id, point_id);

-- BE-RAG-007/008: مهمّة إعادة الفهرسة. الصفّ لا يحمل تقدّماً ولا حالةً —
-- هُويّةً و`cancelled_at` فقط، والباقي يُشتقّ من حالات المستندات التي أنشأتها
-- (عدّادٌ مخزَّن يحتاج كاتباً في العامل ويهجر الحقيقة عند أوّل انهيار).
CREATE TABLE knowledge.reindex_jobs (
  id           uuid PRIMARY KEY,
  workspace_id uuid NOT NULL,
  cancelled_at timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  version      integer NOT NULL DEFAULT 1
);
CREATE INDEX ix_reindex_jobs_ws ON knowledge.reindex_jobs(workspace_id, id DESC);

CREATE TABLE knowledge.reindex_job_items (
  job_id             uuid NOT NULL,
  document_id        uuid NOT NULL,               -- المستند الجديد (INV-K3)
  workspace_id       uuid NOT NULL,
  file_id            uuid NOT NULL,               -- مرجع منطقي لـ files.files.id
  -- المستند المُتلَف الذي حلّ الجديدُ محلّه: سجلٌّ تاريخيّ بلا صفٍّ خلفه، فلا
  -- مفتاح أجنبيّ له — الـ FK كان سيمنع الإتلاف الذي هو غايةُ العملية نفسها.
  source_document_id uuid NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (job_id, document_id),
  CONSTRAINT fk_reindex_item_job FOREIGN KEY (job_id) REFERENCES knowledge.reindex_jobs(id),
  CONSTRAINT fk_reindex_item_doc FOREIGN KEY (document_id) REFERENCES knowledge.documents(id)
);

-- BE-RAG-009/010/011: الملخّص ومهمّةُ بنائه.
-- الملخّص معلّقٌ بالمستند لا بالملف: هو دعوى عن **نصّ**، والنصّ الذي كُتب منه
-- يخصّ مستنداً واحداً. إعادةُ الفهرسة تُتلف المستند (INV-K4) لأنّ بايتات الملف
-- قد تُحلَّل الآن تحليلاً آخر، وملخّصٌ مفتاحُه الملف كان سيبقى بعد ذلك الإتلاف
-- ويستمرّ في وصف مجموعةٍ لم تعد موجودة، بلا قيدٍ واحدٍ يستطيع ملاحظة ذلك.
CREATE TABLE knowledge.summaries (
  id            uuid PRIMARY KEY,
  workspace_id  uuid NOT NULL,
  document_id   uuid NOT NULL,
  kind          text NOT NULL CHECK (kind IN ('overview','full')),
  lang          text NOT NULL CHECK (lang IN ('auto','ar','en')),
  text          text NOT NULL,
  model         text NOT NULL,                    -- مَن كتبه (سابقةُ `host` في BE-ADM-007)
  source_chunks integer NOT NULL,                 -- كم مقطعاً قُرئ فعلاً
  truncated     boolean NOT NULL DEFAULT false,   -- هل تجاوز المستندُ سقف الخريطة
  built_at      timestamptz NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  version       integer NOT NULL DEFAULT 1,
  -- بلا `ON DELETE`، عمداً — سابقةُ `fk_chunk_doc`: `purge` يحذف الملخّصات ثمّ
  -- المقاطع ثمّ الصفّ، وغيابُ الشلّال هو ما يجعل ذلك الترتيب واجباً لا ذوقاً.
  CONSTRAINT fk_summary_doc FOREIGN KEY (document_id) REFERENCES knowledge.documents(id),
  CONSTRAINT uq_summary_key UNIQUE (document_id, kind, lang)
);
CREATE INDEX ix_summaries_ws ON knowledge.summaries(workspace_id, document_id);

-- **هذا الجدول يخزّن حالته وتقدّمه، وهي مخالفةٌ موثّقة لـINV-K5 لا سهو.**
-- مهمّةُ إعادة الفهرسة تشتقّ كلّ رقمٍ لأنّ المتن يسجّله أصلاً: بنودُها تشير إلى
-- مستنداتٍ تحمل حالاتها بنفسها. أمّا مهمّةُ التلخيص فلا شاهد ثانٍ لها — قبل أن
-- تنتهي لا وجود لصفّ ملخّصٍ إطلاقاً، و«الخطوة 7 من 42» حقيقةٌ لا يعرفها إلا
-- العاملُ الذي ينفّذ الخطوة 7. فلا شيء يُشتقّ منه، فيُكتَب؛ والثمن مُعلَن حيث
-- يعيش العدّاد: عاملٌ يموت بين خطوتين يترك `done_chunks` قديماً حتى تُعيد
-- الرسالةُ تسليمَه، والرقم في تلك النافذة آخرُ ما كان صادقاً لا ما هو صادق.
CREATE TABLE knowledge.summary_jobs (
  id            uuid PRIMARY KEY,
  workspace_id  uuid NOT NULL,
  -- بلا مفتاح أجنبيّ، سابقةُ `source_document_id`: المهمّة سجلُّ عمليةٍ وقعت،
  -- وإعادةُ الفهرسة قد تُتلف المستند تحت بناءٍ جارٍ — والـFK كان سيمنع الإتلاف
  -- نفسه. البناءُ الذي يحاول عندئذٍ تخزينَ نتيجته يوقفه `fk_summary_doc`، وهو
  -- الموضع الذي فيه الخطأُ فعلاً، ويحوّله العاملُ إلى مهمّةٍ فاشلةٍ بسبب.
  document_id   uuid NOT NULL,
  kind          text NOT NULL CHECK (kind IN ('overview','full')),
  lang          text NOT NULL CHECK (lang IN ('auto','ar','en')),
  status        text NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
  total_chunks  integer NOT NULL DEFAULT 0,
  done_chunks   integer NOT NULL DEFAULT 0,
  error         text,
  cancelled_at  timestamptz,
  finished_at   timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  version       integer NOT NULL DEFAULT 1
);
-- درسُ `uq_cred_active_platform` مُطبَّقاً هنا: بلا فهرسٍ فريدٍ جزئيّ على
-- الحالات غير الطرفيّة، يصطفّ طلبان متزامنان لنفس المفتاح، **يدفعان كلاهما**
-- ثمن المستند كاملاً، ثمّ يتسابقان إلى صفّ `uq_summary_key` واحد — فيدفع
-- أحدهما بالكامل ثمّ يفشل عند آخر جملة. الفهرس يحوّل ذلك إلى 409 قبل أوّل رمز.
CREATE UNIQUE INDEX uq_summary_job_active
  ON knowledge.summary_jobs(document_id, kind, lang)
  WHERE status IN ('queued','running');
CREATE INDEX ix_summary_jobs_ws ON knowledge.summary_jobs(workspace_id, id DESC);
```

**إتلافُ المستند القديم جزءٌ من العقد لا أثرٌ جانبيّ.** معرّف نقطة Qdrant مُشتقٌّ من
معرّف المستند (`chunk_point_id(document_id, seq)`), فمستندٌ ثانٍ على الملف نفسه يعني
نسختين من كل مقطعٍ في المجموعة **إلى الأبد**. لذلك تحذف إعادةُ الفهرسة نقاطَ المستند
القديم ثمّ صفوف `chunks` ثمّ صفّه، قبل تسجيل الجديد. والترتيب مقصود: فشلُ حذف النقاط
يترك كل شيءٍ كما كان، بينما العكس كان سيترك نقاطاً يتيمةً لمستندٍ لا وجود له.

### 2.8 `media`
```sql
CREATE SCHEMA media;

CREATE TABLE media.media_jobs (
  id             uuid PRIMARY KEY,
  workspace_id   uuid NOT NULL,
  agent_key      text NOT NULL,
  kind           text NOT NULL CHECK (kind IN ('image','video')),
  prompt         text NOT NULL,
  params         jsonb NOT NULL DEFAULT '{}',
  status         text NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued','running','succeeded','failed')),
  result_file_id uuid,                            -- مرجع منطقي لـ files.files.id
  error          text,
  created_by     uuid,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  version        integer NOT NULL DEFAULT 1
);
CREATE INDEX ix_media_ws_status ON media.media_jobs(workspace_id, status);
```

### 2.9 `integrations` (وحدة v1 — `FR‑120…124`)
موصّلات OAuth بطرف ثالث وخوادم MCP **بعيدة (HTTP/SSE) حصراً**، لكل Workspace. الأسرار (رموز/مفاتيح) **مُعمّاة عبر Vault Transit** (`SEC‑07`) — لا نصّ صريح.
```sql
CREATE SCHEMA integrations;

CREATE TABLE integrations.connections (
  id             uuid PRIMARY KEY,
  workspace_id   uuid NOT NULL,
  connector_key  text NOT NULL,                    -- مُعرّف الموصّل في الكتالوج (مثل 'github','slack')
  display_name   text,
  status         text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','connected','revoked','error')),
  scopes         text[] NOT NULL DEFAULT '{}',
  token_ref      text,                             -- ناتج Vault Transit (access/refresh) — لا سرّ خام
  key_id         text,                             -- معرّف مفتاح Transit المستخدَم للتعمية
  expires_at     timestamptz,                      -- انتهاء access token (لفحص التجديد الكسول — FR‑124)
  last_error     text,
  created_by     uuid,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  version        integer NOT NULL DEFAULT 1,
  CONSTRAINT uq_conn_ws_connector UNIQUE (workspace_id, connector_key)
);
CREATE INDEX ix_conn_ws ON integrations.connections(workspace_id) WHERE status = 'connected';

CREATE TABLE integrations.mcp_servers (
  id             uuid PRIMARY KEY,
  workspace_id   uuid NOT NULL,
  name           text NOT NULL,
  endpoint_url   text NOT NULL,                    -- بعيد فقط (https/sse) — لا stdio (v1)
  transport      text NOT NULL DEFAULT 'http'
                   CHECK (transport IN ('http','sse')),   -- نقل بعيد حصراً (خارج v1: stdio → sandbox)
  auth_ref       text,                             -- سرّ مصادقة MCP مُعمّى عبر Transit (اختياري)
  key_id         text,
  status         text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled','error')),
  created_by     uuid,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  version        integer NOT NULL DEFAULT 1,
  CONSTRAINT uq_mcp_ws_name UNIQUE (workspace_id, name)
);
CREATE INDEX ix_mcp_ws ON integrations.mcp_servers(workspace_id) WHERE status = 'active';
```
> اكتشاف أدوات MCP يتمّ **وقت التشغيل** عبر `MCPClient` (لا تُخزَّن قوائم الأدوات إلزامياً)؛ التخزين هنا للاتصال/الاعتماد فقط. **يُمنع** تشغيل خوادم MCP كعمليات فرعية داخل حاويات التطبيق/العمّال (§6.13 من المتطلبات).

### 2.10 `usage` (وحدة v1 — `FR‑130…134`)
قياس + حصص + ميزانيات + حدود. **خارج الناقل**: الالتقاط عبر منفذ وارد متزامن يُلحِق في **سجلّ append‑only** (لا Redis Streams). الفوترة المالية خارج v1.
```sql
CREATE SCHEMA usage;

-- سجلّ استهلاك append‑only (بلا deleted_at/version — DAT‑07)
CREATE TABLE usage.usage_records (
  id            uuid PRIMARY KEY,
  workspace_id  uuid NOT NULL,
  agent_key     text NOT NULL,
  provider      text NOT NULL,
  tokens        bigint NOT NULL DEFAULT 0 CHECK (tokens >= 0),
  cost_micros   bigint NOT NULL DEFAULT 0 CHECK (cost_micros >= 0),  -- تكلفة بوحدات صغرى (تفادي العائم)
  operation_id  uuid NOT NULL,                    -- مفتاح إدمبوتنسي طبيعي (FR‑134)
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_usage_op UNIQUE (workspace_id, operation_id)   -- إعادة الالتقاط بنفس المفتاح ⇒ تُتجاهَل بهدوء
);
CREATE INDEX ix_usage_ws_agent_prov ON usage.usage_records(workspace_id, agent_key, provider, created_at);

-- تجميع/تدوير داخلي للفرض السريع (rollup — شأن داخلي للوحدة)
CREATE TABLE usage.usage_rollups (
  workspace_id  uuid NOT NULL,
  agent_key     text NOT NULL DEFAULT '*',        -- '*' = كل الوكلاء
  provider      text NOT NULL DEFAULT '*',
  period        text NOT NULL CHECK (period IN ('day','month')),
  period_start  date NOT NULL,
  tokens_sum    bigint NOT NULL DEFAULT 0,
  cost_micros_sum bigint NOT NULL DEFAULT 0,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, agent_key, provider, period, period_start)
);

-- حصص/ميزانيات/حدود قابلة للإعداد (متوائمة مع SLO — FR‑133)
CREATE TABLE usage.limits (
  id            uuid PRIMARY KEY,
  workspace_id  uuid NOT NULL,
  scope         text NOT NULL CHECK (scope IN ('workspace','agent','provider')),
  scope_key     text NOT NULL DEFAULT '*',        -- agent_key أو provider عند تخصيص النطاق
  metric        text NOT NULL CHECK (metric IN ('tokens','cost_micros')),
  period        text NOT NULL CHECK (period IN ('day','month')),
  limit_value   bigint NOT NULL CHECK (limit_value >= 0),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  version       integer NOT NULL DEFAULT 1,
  CONSTRAINT uq_limit UNIQUE (workspace_id, scope, scope_key, metric, period)
);
CREATE INDEX ix_limits_ws ON usage.limits(workspace_id);

-- capacity-plan 2.7 — الحجوزات: القبول قبل التشغيل، لا الشحن بعده.
CREATE TABLE usage.reservations (
  id            uuid PRIMARY KEY,
  workspace_id  uuid NOT NULL,
  agent_key     text NOT NULL,
  provider      text NOT NULL,
  tokens        bigint NOT NULL DEFAULT 0 CHECK (tokens >= 0),
  cost_micros   bigint NOT NULL DEFAULT 0 CHECK (cost_micros >= 0),
  created_at    timestamptz NOT NULL DEFAULT now(),
  expires_at    timestamptz NOT NULL
);
CREATE INDEX ix_usage_reservations_ws_live ON usage.reservations(workspace_id, expires_at);
```
> **الفرض** (قبل العملية) يقرأ `usage_rollups` مقابل `limits` ويعيد **كائن قرار**؛ كلا المنفذين يُستدعى من **المُنسِّق** (طبقة الوكلاء) حصراً.
>
> **capacity-plan 2.7 — `reserve`/`commit` مُفعَّلةٌ الآن، والجدولُ أعلاه هو «جدول الحجوزات لاحقاً» الذي أعلنته هذه الفقرةُ نقطةَ توسعة.** السببُ مُقاس: الفرضُ **قراءةٌ**، ومئةُ قارئٍ متزامنين يرون الحصّةَ نفسَها — مساحةُ عملٍ يتبقّى في حصّتها رمزٌ واحدٌ قبلت **46** من مئةٍ وانتهى دفترُها عند 55 مقابل حدٍّ 10. **والصفُّ هو القبول**: يُكتب تحت قفلِ مساحةِ العمل الاستشاريّ في المعاملة التي تقرأ المجاميع، ويحسبه كلُّ قارئٍ بعده لأنّ `EnforceLimit` يجمع الحجوزاتِ الحيّةَ إلى `current` — فلا عدّادَ ولا إنقاصَ ولا رقمَ ينحرف عن الصفوف. **و`tokens` هنا ما حُجز لا ما أُنفق**: الإنفاقُ صفُّ الدفتر، ولا يجتمعان في صفٍّ واحد. **و`expires_at` حارسٌ للطلب الذي لا يعود** (عاملٌ قُتل، بثٌّ قُطع) وقيمتُه مهلةُ البثّ نفسُها (`Limits.stream_max_duration_s`) يمرّرها جذرُ التركيب؛ القارئُ **يُرشِّحه** فيكفّ المهجورُ عن كلفة الحصّة لحظةَ انتهائه، والحذفُ تنظيفٌ انتهازيٌّ يجريه الحاجزُ التاليُ لنفس المساحة تحت القفل نفسِه — فلا كانسَ ولا مهمّةَ دوريّة. ولا `updated_at` ولا `trg_touch`: الحجزُ يُنشَأ ثمّ يُحذَف بالتزامه أو ينتهي، ولا يُعدَّل.
>
> **BE-ADM-014**: الجداول الأربعة هنا تُمحى مع مساحة العمل التي تخصّها عند التطهير (`python -m
> app.ops.purge`) — قراءةُ عدّادٍ لكلّ مستأجرٍ لا التزامًا ماليًا (الفوترة خارج v1)، فلا تُجمَّع في
> سجلٍّ عابرٍ للمساحات قبل الحذف؛ REVIEW POINT في نصّ الوحدة نفسها إن ظهر التزام احتفاظٍ فوترةً/ضرائب.

### 2.11 `spaces` (وحدة v1 — [`spaces-backend-plan.md`](../spaces-backend-plan.md))

**الوحدة (space) محور ملكيّة داخل المستأجر، لا مستأجر ثانٍ.** مساحة العمل تبقى الحدّ الأمنيّ الوحيد؛ الوحدة تقسّم محتواها إلى أقسامٍ يملك كلٌّ منها ملفّاته ومحادثاته ومستنداته. **الرقم يأتي أخيرًا في هذا الترقيم وحده** — تسلسل §2 تاريخيّ، والإشارات إليه (‏`01 §2.7`…) مبثوثةٌ في بقيّة الوثائق فلا تُعاد ترقيمها — أمّا ترتيب الإنشاء الحقيقيّ فهو §6: مباشرةً بعد سلسلة الأساس.

```sql
CREATE SCHEMA spaces;

CREATE TABLE spaces.spaces (
  id           uuid PRIMARY KEY,
  workspace_id uuid NOT NULL,
  name         text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
  created_by   uuid,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz NULL,
  version      integer NOT NULL DEFAULT 1
);
-- تفرّدٌ **غير حسّاس لحالة الأحرف** وعلى **الحيّ وحده**: «Research» و«research» اسمٌ
-- واحدٌ لقارئ، ووحدةٌ محذوفةٌ ناعمًا يجب ألّا تحتجز اسمها إلى الأبد. وهذا الفهرس هو
-- **كامل دفاع الوحدة عن التفرّد**: التطبيق لا يقرأ قبل الكتابة عمدًا (زوج «اقرأ ثمّ أدرج»
-- خاطئٌ بالضبط حين يتسابق طلبان)، فـ`23505` الصادر من هنا هو ما يصير `spaces.duplicate_name`.
CREATE UNIQUE INDEX ux_spaces_ws_name
  ON spaces.spaces(workspace_id, lower(name)) WHERE deleted_at IS NULL;

CREATE TRIGGER trg_touch BEFORE UPDATE ON spaces.spaces
  FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at();

ALTER TABLE spaces.spaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE spaces.spaces FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON spaces.spaces
  USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
  WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
```

**المخطّط `spaces` نفسه لا تُنشئه هذه السلسلة** بل مراجعةُ منصّةٍ سابقة ([`platform/0004_spaces_schema.py`](../../migrations/versions/platform/0004_spaces_schema.py))، والسبب بنيويّ: Alembic يُنشئ `spaces.alembic_version` **قبل** تنفيذ أوّل `upgrade()`، فلا تستطيع السلسلة أن تُنشئ المخطّط الذي يسكنه جدولُ دفاترها.

**الأعمدة الثلاثة المضافة على الوحدات الأخرى** (‏§2.4 · §2.6 · §2.7) `space_id uuid NULL` اليوم، ونمطُ ترحيلها ADD ⇒ backfill ⇒ فهرس. والتشديد إلى `NOT NULL` **مؤجَّلٌ بقصد** إلى صفٍّ لاحق في الخطّة: عمودٌ إلزاميٌّ قبل أن يصير له كُتّاب كان يردّ `23502` على كلّ إدراجٍ في ثلاثة جداول. آخرُ ما يمنع التشديد اليوم كاتبٌ واحد — **الوسائط المولَّدة**، ووظيفتها لا تحمل خيطًا ولا وحدة.

**لا مفاتيح خارجيّة عبر المخطّطات** (`DD‑01`): `space_id` مرجعٌ منطقيّ، على سابقة `conversation_files.file_id`. ⇒ **إثبات وجود الوحدة قبل الكتابة مسؤوليّة التطبيق** (منفذٌ لكلّ مستهلك، [`02 §2`](02-port-contracts.md))، ولا شيء في القاعدة يمنع صفًّا يشير إلى وحدةٍ غير موجودة. والقراءة **لا تُثبت**: سردٌ بمعرّفٍ لا يسمّي وحدةً يعيد صفحةً فارغة لا `404`، وإلّا صار السرد عرّافَ وجودٍ لمعرّفاتٍ لم يُعطها أحد.

**الحذف حذفٌ متسلسل** يعبر خمس وحدات + Qdrant + MinIO؛ يعيش في **خدمة تنسيق** عند جذر التركيب لا في وحدة (سابقة [`app/ops/purge.py`](../../src/app/ops/purge.py))، بلا وحدة عملٍ جامعة: كلّ خطوةٍ معاملتُها وكلٌّ منها عديمةُ الأثر عند التكرار، فسلسلةٌ انقطعت يُصلحها إعادةُ التشغيل. والوسمُ (‏`deleted_at` على صفّ الوحدة) **أوّلًا** كي لا يرى المستخدم وحدةً نصف-محذوفة.

### 2.12 `electrical` (وحدة WireTwin — `electrical-drawings` INC‑1)

**قسم `drawings`: سبعة جداول ببادئة `drawings_`** في المخطّط `electrical` (الأقسام `circuits · search · diagnostics` تضيف جداولها في زياداتها). المصدر الملزِم: [`lld-inc-1.md` §4.3](../delivery/electrical-drawings/docs/lld-inc-1.md)، والهجرة [`electrical/0001_electrical_drawings.py`](../../migrations/versions/electrical/0001_electrical_drawings.py). **المخطّط نفسه لا تُنشئه هذه السلسلة** بل [`platform/0006_electrical_schema.py`](../../migrations/versions/platform/0006_electrical_schema.py) (السبب نفسه في §2.11: `electrical.alembic_version` يُنشأ قبل أوّل `upgrade()`).

| الجدول | المفتاح | أبرز القيود والفهارس |
|---|---|---|
| `drawings_projects` | `id` | `ux_drawings_projects_space (workspace_id, space_id)` — مشروعٌ واحد للوحدة (SRS D‑29)، `23505` ← `electrical.project_exists` |
| `drawings_documents` | `id` | `ux_drawings_documents_file (workspace_id, file_id) WHERE status <> 'purging'` ← `document_exists`؛ `ux_drawings_documents_content (workspace_id, project_id, content_sha256) WHERE status='active' AND content_sha256 IS NOT NULL` ← `duplicate_document`؛ `ck_drawings_documents_exclusion`: `(status='excluded') = (exclusion_reason IS NOT NULL)` |
| `drawings_jobs` | `id`، `document_id → documents` | `ux_drawings_jobs_open (workspace_id, document_id) WHERE status IN ('queued','running','awaiting_confirmation')` ← `job_conflict`؛ `ix_drawings_jobs_queue ... WHERE status='queued'` |
| `drawings_pages` | `(workspace_id, document_id, page_no)` | `kind`/`language`/`needs_ocr` تبقى `NULL` حتى تُصنَّف الصفحة؛ `signals jsonb` مختصرة ≤ 16 KiB |
| `drawings_page_stages` | `(workspace_id, document_id, page_no, stage)` | FK مركّب إلى `drawings_pages`؛ `ck_drawings_page_stages_stage` يقبل `'classify'` وحدها (تُوسَّع بكل مرحلة جديدة)؛ `(status IN ('failed','needs_review')) = (reason IS NOT NULL)` |
| `drawings_profile_versions` | `id` | إصدارات ملف الإعداد **إلحاقية**؛ فهرسان جزئيان فريدان: `ux_drawings_profiles_project (… WHERE document_id IS NULL)` و`ux_drawings_profiles_document (… WHERE document_id IS NOT NULL)`؛ `confidence real`؛ `detection jsonb NULL` (≤ 16384 بايت) يحفظ `{field_confidence, basis}` للإصدار `detected` فقط |
| `drawings_characterizations` | `(workspace_id, document_id)` | تقرير FR‑EVAL‑14 في `report jsonb` ≤ 256 KiB، والإصدارات الثلاثة أعمدة |

- **RLS** على الجداول السبعة بـ`workspace_id` وحده (`ENABLE` + `FORCE` + `tenant_isolation` بصيغة `NULLIF`)، والوحدة والمشروع مرشِّحان في `WHERE` كل مستودع (§3). و`trg_touch` على الخمسة التي فيها `updated_at` (كل الجداول عدا `profile_versions` و`characterizations`).
- **المفاتيح الأجنبية داخل القسم وحده**؛ `space_id` و`file_id` و`documents.project_id` مراجع **منطقية** (`DD‑01`)، فتطهير الوحدة يحذف المشروع قبل أن يكنس العامل وثائقه. ولا `ON DELETE CASCADE`: يُفرَّغ كل جدول بعبارة صريحة تُعدّ (`app.ops.purge`)، بترتيب `page_stages ← pages ← characterizations ← jobs ← profile_versions ← documents ← projects`، و`electrical` **قبل** `files` لأن الوثيقة تشير إلى ملفها.
- **المنح:** `app_rw` يأخذ USAGE وCRUD (`_TENANT_TABLES`، `_MODULE_SCHEMAS`)؛ و`workspace_purger` USAGE و`SELECT, DELETE` (`PURGE_GRANTS`). لا منح لـ`retention_sweeper` ولا `metrics_reader` ولا `backup_operator` (يملك `BYPASSRLS`)، ولا دور جديد.
- **الوثيقة المُطهَّرة (`purging`) تُكتب بلا `exclusion_reason`:** القيد يربط السبب بحالة `excluded`، و`Document.mark_purging` لا يمسح السبب، فالمحوّل يكتبه `NULL` عند `purging` (وإلا امتنع تطهير كل وثيقة استُبعدت قبلًا).
- **التراجع يُسقط البيانات:** `downgrade` يحذف الجداول السبعة بكل صفوفها، وتراجع `platform/0006` يُسقط المخطّط بـ`CASCADE`. ملفات `files` باقية. الأولى ترك الجداول وإيقاف `ELECTRICAL_ENABLED`.

---

## 3) عزل المستأجر — RLS (تنفيذ `DD‑04`, D‑23)

على **كل جدول مستأجَر** (يحمل `workspace_id`):
```sql
ALTER TABLE files.files ENABLE ROW LEVEL SECURITY;
ALTER TABLE files.files FORCE ROW LEVEL SECURITY;   -- يشمل مالك الجدول

CREATE POLICY tenant_isolation ON files.files
  USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
  WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
```
- ضبط السياق لكل معاملة (من `ExecutionContext` عبر `infrastructure/persistence/rls.py`):
```sql
SET LOCAL app.workspace_id = '018f...';   -- بداية كل معاملة تطبيق/عامل
```
- **دور التطبيق** `app_rw`: `NOINHERIT`, **بلا** `BYPASSRLS`, وليس مالك الجداول ⇒ خاضع للسياسة.
- **الترشيح التطبيقي** (دفاع ثانٍ): كل Repository يضيف `WHERE workspace_id = :ws` صراحةً.
- تنطبق نفس السياسة على **كل** الجداول المستأجَرة الجديدة: `spaces.spaces`, `integrations.connections`, `integrations.mcp_servers`, `usage.usage_records`, `usage.usage_rollups`, `usage.limits`.
- **و`space_id` ليست في أيّ سياسة — قرارٌ مقصود، لا سهو.** مساحة العمل تُشتقّ من **هويّة** المستخدم بعد المصادقة؛ الوحدة يختارها **الطلب**. ووضعُ قيمةٍ يختارها الطلب في متغيّرٍ أمنيّ لا يضيف أمنًا — التطبيق هو من يضبطها أصلًا — ويحوّل خطأ ترشيحٍ عاديًّا إلى ثغرةٍ صامتة. الترشيح بالوحدة مكانه `WHERE` المستودع، وهو **دفاعٌ أوّل** لا ثانٍ: لا سياسةَ خلفه تُمسك ما يسقط منه.
- الجداول غير المستأجَرة (`workspace.workspaces`, كتالوجات المنصّة) تُحمى بالـAuthZ لا بالـRLS.
- `current_setting(..., true)` يعيد `NULL` عند غياب السياق ⇒ **صفر صفوف** (فشل آمن، لا تسريب).
- **تصليب `NULLIF` إلزامي (مُثبَت تجريبياً على PG16، 2026‑07‑14):** على الاتصالات المجمّعة، أوّل `SET LOCAL` على الـbackend يجعل قيمة إعادة الضبط للـGUC **سلسلة فارغة `''`** لا «غير مضبوط»؛ فمعاملة لاحقة تنسى `SET LOCAL` تحصل على `''`، و`''::uuid` **يرمي خطأ** (22P02 ⇒ 500) بدل صفر صفوف. صيغة `NULLIF(...,'')::uuid` تُرجِع الفشل الآمن (`''`→`NULL`→صفر صفوف). خلف PgBouncer (D‑21) هذا مسار ساخن لا حالة حدّية.

### 3.1 حالة خاصّة — قراءة مفاتيح المنصّة (`credentials` بـ`workspace_id IS NULL`)

صفوف `credentials.credentials` على مستوى المنصّة (`scope='platform'`, `workspace_id IS NULL`) لا تطابق سياسة `tenant_isolation` (المطابقة على `workspace_id = app.workspace_id`)، والدور `app_rw` **بلا** `BYPASSRLS`؛ فتبقى غير مقروءة عبر السياسة القياسية. يحلّ `ProviderResolver` هذا عبر **سياسة RLS إضافية (permissive) للقراءة فقط** تسمح بصفوف المنصّة المشتركة — سياسات RLS تُدمَج بمنطق **OR**، فتوسّع القراءة لصفوف المنصّة دون كسر عزل صفوف المستأجرين الآخرين (تبقى مقيَّدة بـ`workspace_id IS NULL`):
```sql
-- قراءة فقط لمفاتيح المنصّة المشتركة، فوق سياسة العزل المستأجري
CREATE POLICY platform_credentials_read ON credentials.credentials
  FOR SELECT
  USING (workspace_id IS NULL AND scope = 'platform');
```
- النطاق **SELECT فقط** ⇒ لا كتابة/تعديل على مفاتيح المنصّة عبر مسار المستأجر (إدارتها بمسار PlatformAdmin منفصل عبر AuthZ).
- القيد `workspace_id IS NULL` يمنع تسريب مفاتيح مستأجرين آخرين؛ الرؤية تقتصر على المنصّة المشتركة + مفاتيح المستأجر الحالي (عبر `tenant_isolation`).
- يتّسق مع قاعدة الاختيار في `06-domain-models.md` (§3): تفضيل مفتاح المستخدم، وإلا مفتاح المنصّة، بلا Fallback بين المزوّدين (D‑16).

---

## 4) بنية الأحداث — `platform` (تنفيذ D‑18, D‑19)

### 4.1 Transactional Outbox
```sql
CREATE SCHEMA platform;

CREATE TABLE platform.outbox (
  id             uuid PRIMARY KEY,                -- = event_id (UUIDv7)
  workspace_id   uuid,                            -- للتتبّع (قد يكون NULL لأحداث منصّة)
  aggregate_type text NOT NULL,
  aggregate_id   uuid NOT NULL,
  event_type     text NOT NULL,                  -- <module>.<aggregate>.<event>.vN
  stream         text NOT NULL,                  -- اسم مجرى الوحدة الهدف
  payload        jsonb NOT NULL,                 -- مظروف CloudEvents كامل
  correlation_id uuid,
  causation_id   uuid,
  created_at     timestamptz NOT NULL DEFAULT now(),
  published_at   timestamptz NULL,               -- يملؤه المُرحّل بعد النشر
  attempts       integer NOT NULL DEFAULT 0
);
CREATE INDEX ix_outbox_unpublished ON platform.outbox(created_at)
  WHERE published_at IS NULL;
```
- الأثر النطاقي + صف الـOutbox يُكتبان في **نفس المعاملة** ⇒ لا فقد ولا نشر أشباح.
- `outbox_relay` (نسخة واحدة، D‑26): يسحب غير المنشور بترتيب `created_at`، ينشر إلى `XADD` للمجرى، ثم يضبط `published_at`. `at‑least‑once`.

### 4.2 Idempotency (تنفيذ D‑19, `DD‑09`)
```sql
CREATE TABLE platform.processed_events (
  consumer_group text NOT NULL,
  event_id       uuid NOT NULL,
  processed_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (consumer_group, event_id)
);
```
- المستهلك يُدرج `(group, event_id)` **داخل معاملة الأثر الجانبي**؛ تعارض المفتاح ⇒ سبق المعالجة ⇒ `ACK` بلا تكرار.

### 4.2‑ب مثاليّة الطلبات — `Idempotency-Key` (‏3.79، `03 §0`)
```sql
CREATE TABLE platform.idempotency_keys (
  workspace_id    uuid NOT NULL,
  endpoint        text NOT NULL,
  idempotency_key text NOT NULL,
  request_hash    text NOT NULL,                  -- بصمة sha256، لا الجسد
  response_body   jsonb,                          -- NULL ⇒ مُطالَبٌ به وقيد التنفيذ. وجسدٌ مع completed_at IS NULL ⇒ جسدٌ مؤقت (checkpoint، ث‑3): العملية أحدثت أثرها ولم تنتهِ، فلا يُحرَّر المفتاح
  created_at      timestamptz NOT NULL DEFAULT now(),
  completed_at    timestamptz,
  PRIMARY KEY (workspace_id, endpoint, idempotency_key)
);
CREATE INDEX ix_idempotency_keys_created ON platform.idempotency_keys(created_at);
```
- نظير §4.2 عند حدّ الـAPI لا حدّ المستهلك: عمليّةٌ قد تُسلَّم مرّتين يجب أن تُنفَّذ مرّة. الإدراج **أوّلاً** (وتعارض المفتاح الأساسيّ هو ما يجعل «واحدٌ فقط ينفّذ» صحيحاً تحت التزامن)، ثمّ يُملأ `response_body` بعد اكتمال العمليّة، وتُحذف المطالبة إن **رمَت** كي لا يتعطّل المفتاح للأبد.
- **الجدول الوحيد في `platform` بسياسة RLS**: جسد الردّ المخزَّن بيانات مستأجر، خلافاً لـ`outbox`/`processed_events` اللذين لا يحملان عمود مستأجر أصلاً. السياسة بصيغة `NULLIF` المُصلَّبة (‏§3).
- **الصلاحيات:** الجدول الوحيد في `platform` الذي يأخذ `SELECT, INSERT, UPDATE, DELETE` لـ`app_rw` — لأنّ غايته أن يُقرأ ويُحدَّث ويُحرَّر؛ ما يحصره هو RLS لا المنحة.
- **مقبض الدور (ث‑3):** لـ`postMessage` (`POST /conversations/{id}/messages`) يحمل `response_body` مقبض الدور `{kind, v, conversation_id, user_message_id, seq, outcome, message_id, code, status, usage}`، بلا نص ولا `content` ولا `citations` ولا `detail`، فطوله ثابت تقريباً (~300 بايت) ولا يتبع طول الرسالة. `outcome` هو `accepted` (جسد مؤقت) أو `replied` أو `failed` (جسد كامل مع `completed_at`). و`release` لا يحذف مطالبةً لها جسد مؤقت.
- **الاحتفاظ:** تكنس `ops-scheduler` يومياً الصفوف التي مضى على `created_at` فيها يومان (`IDEMPOTENCY_KEYS_RETENTION`، `src/app/ops/retention.py:140`)، فيعيش المفتاح بين يومين وثلاثة. والمفتاح بعد كنسه جديد. `created_at` مفهرسٌ لأنّ الكنسة تعمل عليه.

### 4.3 مواضع المجاري (اختياري للمراقبة)
```sql
CREATE TABLE platform.stream_offsets (
  stream         text NOT NULL,
  consumer_group text NOT NULL,
  last_id        text NOT NULL,                   -- Redis stream entry id
  updated_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (stream, consumer_group)
);
```
> نقطة توسعة Observability: `correlation_id`/`causation_id` محمولة في Outbox والمظروف ⇒ تتبّع لاحق بلا إعادة هيكلة.

---

## 5) الفهارس والأداء
- كل مفاتيح البحث الشائعة مفهرسة بـ`(workspace_id, …)` لتوافق مسح RLS.
- الفهارس الجزئية `WHERE deleted_at IS NULL` تُبقي المسح على الحيّ فقط.
- **فهارس الوحدة الثلاثة** — `ix_files_space_name` · `ix_conv_ws_space` (جزئيّان) · `ix_kndoc_space` (كامل، §2.7) — تخدم ثلاث قراءاتٍ لا واحدة: السرودَ الثلاثة (‏`?space_id=` إلزاميّ)، ومجموعَ الحصّة على مسار كلّ رفع، والحذفَ المتسلسل. **وكلُّها تتصدّرها `workspace_id` منذ خطّة السعة `2.4`** — عدا `ix_kndoc_space` — لأنّ كلَّ مسندٍ على `space_id` في كلّ محوّلٍ يحمل الوحدةَ ومساحةَ العمل معاً (‏DD‑04 الطبقة 2)، ففهرسٌ على `space_id` وحدَه يُجبر إعادةَ فحصٍ على الكومة ولا يقود مسنداً واحداً. ولا فهرسَ رابعاً على `files.files` بعد ذلك: الحصّةُ تركب `ix_files_space_name` نفسَه بـ`INCLUDE (size_bytes)` — فهرسٌ منافسٌ يتشارك البادئةَ كان سيسرق خطّةَ بحث الأسماء (§2.6). وفهرسٌ رابع خارج PostgreSQL: بطاقة `space` في Qdrant بـ`is_tenant=True` — إعادةُ ترتيبٍ فيزيائيّ للنقاط، لا تسريعُ مطابقة (‏[`07 §5`](07-nfr-slo.md#5-الأداء-والصيانة-بنيوياً)).
- `platform.outbox` فهرس جزئي على غير المنشور ⇒ سحب المُرحّل O(الطابور).
- UUIDv7 يقلّل تضخّم صفحات B‑Tree مقارنةً بـv4 تحت الإدراج.

## 6) استراتيجية الهجرات (Alembic)
- **سلسلة هجرات لكل وحدة** (‏**اثنتا عشرة سلسلة** = إحدى عشرة v1 + `electrical`، + سلسلة الأساس = **13 مدخلة** في `MIGRATION_CHAINS`، و`electrical` آخرها لأنها لا تقرأ جدول سلسلة أخرى) ضمن `version_table_schema` منفصل لكل وحدة (`DAT‑03`)، تُدار مركزياً بأمر واحد؛ سلسلة تمهيدية تُنشئ الـschemas والامتدادات وجداول `platform`.
- **`spaces` تلي الأساس مباشرةً وقبل كلّ سلسلةٍ كسبت `space_id`** (‏§2.11): ترحيلات تلك الأعمدة الثلاثة تملأ نفسها بالرجوع إلى صفوف `spaces.spaces`، فالجدول يجب أن يكون موجودًا. **ولا FK يفرض هذا الترتيب** (المرجعيّة منطقيّة، `DD‑01`) — ولهذا بالذات يُكتب الترتيب في `MIGRATION_CHAINS` بدل تركه لـAlembic ليكتشفه. و`workspace` تسبق الثلاثة كذلك، ولسببٍ أحدّ: `workspace.workspaces` بلا RLS (جدولُ جذر المستأجر، §2.1)، فهي الجدول الوحيد الذي يستطيع مُرحِّلٌ ليس superuser ولا `BYPASSRLS` أن يعدّده **قبل** أن يعرف أيّ مساحةٍ يضبط `app.workspace_id` عليها — وفراغُها هناك يعني أنّ كلّ backfill يمرّ ناجحًا وهو لا يفعل شيئًا.
- `alembic.ini` يوجّه `version_table_schema` لكل وحدة بمعزل ⇒ إضافة وحدة جديدة = **سلسلة هجرات مستقلّة** بلا مساس بالقائم (`FR‑111`).
- ترتيب البذر: schemas → جداول المنصّة → جداول الوحدات → سياسات RLS → الأدوار/الصلاحيات (`app_rw`).
- **schemas المحجوزة** (`scheduling · sandbox · runs`) **لا تُنشأ في v1**؛ تُضاف بسلسلة هجرات خاصّة عند اعتمادها.
- لا هجرة تُنشئ FK عابراً بين schemas الوحدات (يُفحص في مراجعة الهجرات).
