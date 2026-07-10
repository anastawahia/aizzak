# نماذج النطاق لكل وحدة (Domain Models · DDD)

> النطاق **نقي**: لا FastAPI، لا SQLAlchemy، لا I/O. كيانات وValue Objects وDomain Events فقط.
> المعرّفات UUIDv7 (`DD‑02`)؛ الأوقات UTC (`DD‑03`). كل وحدة = Bounded Context مستقل، لا يستورد نطاق وحدة أخرى.
> الرموز أدناه توضيحية للعقد النطاقي (dataclasses مجمّدة نمطياً)، لا كود نهائي.

**اصطلاح:** `AR` = Aggregate Root · `E` = Entity · `VO` = Value Object · `EV` = Domain Event · `INV` = Invariant · `RM` = Read‑model/Projection (مشتقّ، بلا هوية مستقلّة).

---

## 1) `workspace` — المستأجر والهوية

المستأجر الجذر، وصورة الهوية المُبذَّرة عند أول دخول (JIT) من Firebase.

```text
AR  Workspace   { id, owner_user_id, name: WorkspaceName, status: WorkspaceStatus,
                  created_at, updated_at, version }
AR  User        { id (=firebase_uid mapped), workspace_id, email: Email,
                  display_name, status: UserStatus, created_at, updated_at, version }
VO  WorkspaceName   (1..80 chars, trimmed, non-empty)
VO  WorkspaceStatus (active | suspended | archived)
VO  UserStatus      (active | disabled)
VO  Email           (RFC 5322 lite, lowercased)
EV  WorkspaceCreated(workspace_id, owner_user_id, occurred_at)
EV  WorkspaceArchived(workspace_id, occurred_at)
EV  UserProvisioned(user_id, workspace_id, email, occurred_at)   # JIT عند أول تحقّق توكن
```
- **INV‑W1:** لكل `User` مالك `Workspace` واحد يملكه (`owner_user_id`).
- **INV‑W2:** لا تُقبل عمليات على `Workspace` بحالة `archived` عدا إعادة التنشيط بيد `PlatformAdmin`.
- **Use‑cases:** `ProvisionUserOnFirstLogin`, `RenameWorkspace`, `ArchiveWorkspace`.

---

## 2) `access` — RBAC (تنفيذ D‑24)

الأدوار والصلاحيات وإسنادها. `Permission` كتالوج ثابت بالكود؛ `Role` قابل للإسناد.

```text
AR  RoleAssignment { id, workspace_id, user_id, role: RoleName, granted_by, created_at }
VO  RoleName    (owner | admin | member | viewer | platform_admin)
VO  Permission  ("<resource>:<action>"  e.g. files:read, agents:invoke)
    RoleCatalog: Mapping[RoleName, frozenset[Permission]]   # ثابت، يُنظر في 05
EV  RoleAssigned(assignment_id, workspace_id, user_id, role, occurred_at)
EV  RoleRevoked(assignment_id, workspace_id, user_id, role, occurred_at)
```
- **INV‑A1:** لكل `Workspace` **مالك واحد** على الأقل (لا يُلغى آخر `owner`).
- **INV‑A2:** `platform_admin` لا يُسنَد إلا بيد `platform_admin` آخر (بذرة أولية خارج التطبيق).
- **INV‑A3:** الإسناد فريد على `(workspace_id, user_id, role)`.
- **قرار التفويض** دالة نقية: `is_allowed(role, permission) = permission ∈ RoleCatalog[role]`.
- **Use‑cases:** `AssignRole`, `RevokeRole`, `AuthorizeAction(user, workspace, permission)`.

---

## 3) `credentials` — مفاتيح API (تنفيذ D‑16, DD‑11)

مفاتيح المزوّدين، على مستوى المنصّة أو المستخدم؛ القيمة **مشفّرة** عبر Vault Transit ولا تُخزَّن خاماً أبداً.

```text
AR  Credential { id, workspace_id (NULL للمنصّة), provider: ProviderRef, scope: CredentialScope,
                 label, ciphertext_ref: CipherRef, status: CredentialStatus,
                 created_by, created_at, updated_at, version }
VO  ProviderRef      (openai | gemini | claude | ollama | openrouter | embedding:* | image:* | video:*)
VO  CredentialScope  (platform | user)
VO  CredentialStatus (active | revoked)
VO  CipherRef        (نص مُعمّى من Vault Transit + معرّف مفتاح التشفير — لا سرّ خام)
EV  CredentialAdded(credential_id, provider, scope, occurred_at)
EV  CredentialRevoked(credential_id, occurred_at)
```
- **INV‑C1:** `scope=user` ⇒ `workspace_id NOT NULL`؛ `scope=platform` ⇒ `workspace_id IS NULL`.
- **INV‑C2:** لا تُعاد `ciphertext_ref` عبر أي API؛ فك التعمية داخل حدود المنصّة فقط عبر `SecretsProvider`.
- **قاعدة الاختيار (ProviderResolver):** لطلبٍ ما، يُفضَّل مفتاح المستخدم النشط لنفس المزوّد، وإلا مفتاح المنصّة؛ **بلا Fallback بين المزوّدين** (D‑16).
- **Use‑cases:** `AddUserCredential`, `RevokeCredential`, `ResolveCredential(provider, workspace)`.

---

## 4) `conversations` — المحادثات (تنفيذ D‑06, D‑12)

المحادثة تتبع **الوكيل** بمفتاح `(workspace + agent_key)`؛ وللـWorkflow محادثته الخاصة.

```text
AR  Conversation { id, workspace_id, agent_key: AgentKey, kind: ConversationKind,
                   title, created_by, created_at, updated_at, deleted_at, version }
E   Message      { id, conversation_id, workspace_id, role: MessageRole, content: MessageContent,
                   token_count, seq: int, created_at, deleted_at }
VO  AgentKey         (slug الوكيل من الـManifest؛ أو workflow_key عند kind=workflow)
VO  ConversationKind (agent | workflow)
VO  MessageRole      (user | assistant | system | tool)
VO  MessageContent   (نص + مرفقات اختيارية: قائمة file_id بالإشارة فقط)
EV  ConversationStarted(conversation_id, workspace_id, agent_key, kind, occurred_at)
EV  MessageAppended(message_id, conversation_id, role, seq, occurred_at)
```
- **INV‑CV1:** `seq` متتابع تصاعدي داخل المحادثة (منع الفجوات/التكرار عبر قفل تفاؤلي على `Conversation.version`).
- **INV‑CV2:** المرفقات تُشار إليها بـ`file_id` فقط (مِلكية المحتوى لوحدة `files`) — لا نسخ محتوى.
- **INV‑CV3:** لا إلحاق برسائل محادثة `deleted_at IS NOT NULL`. الحذف الناعم متاح على مستويين: المحادثة (`Conversation.deleted_at`) والرسالة المفردة (`Message.deleted_at`)؛ رسالة محذوفة ناعماً تُستبعَد من الاسترجاع دون تغيير `seq` (لا فجوات في العدّاد).
- **Use‑cases:** `StartConversation`, `AppendMessage`, `ListConversationsByAgent`, `RenameConversation`, `SoftDeleteConversation`.

---

## 5) `memory` — الذاكرة الدلالية (تنفيذ D‑06)

ذاكرة **مستقلة لكل وكيل**؛ المتجهات في Qdrant، والوصف في Postgres. عملية التخزين قد تُحال إلى العامل عند الحجم.

```text
AR  MemoryItem { id, workspace_id, agent_key: AgentKey, kind: MemoryKind, content,
                 vector_ref: VectorRef, salience: float, created_at, deleted_at }
VO  MemoryKind  (semantic | episodic)
VO  VectorRef   (collection + point_id في Qdrant؛ أو NULL ريثما يُفهرَس)
EV  MemoryStored(memory_id, workspace_id, agent_key, occurred_at)
EV  MemoryForgotten(memory_id, occurred_at)
```
- **INV‑M1:** كل `MemoryItem` مربوط بـ`agent_key` (لا ذاكرة مشتركة بين الوكلاء).
- **INV‑M2:** حذف عنصر ذاكرة يحذف نقطته المقابلة في Qdrant (اتساق نهائي عبر حدث).
- **Use‑cases:** `RememberInteraction`, `RecallRelevant(agent_key, query, k)`, `ForgetMemory`.

---

## 6) `files` — الملفات المشتركة

ملفات على مستوى الـWorkspace، **مشتركة بين كل الوكلاء**؛ البايتات في MinIO، الوصف في Postgres. رفع الملف يبثّ حدثاً عالمياً تلتقطه `knowledge`.

```text
AR  File { id, workspace_id, name: FileName, content_type: ContentType, size_bytes,
           storage_key: StorageKey, checksum: Sha256, status: FileStatus,
           uploaded_by, created_at, deleted_at, version }
VO  FileName     (1..255، مُنقّى من مسارات)
VO  ContentType  (MIME مسموح ضمن قائمة بيضاء)
VO  StorageKey   (مفتاح كائن MinIO: workspace_id/uuid)
VO  Sha256       (بصمة للتحقّق ومنع التكرار)
VO  FileStatus   (uploaded | scanning | ready | quarantined)
EV  FileUploaded(file_id, workspace_id, content_type, size_bytes, occurred_at)   # عالمي → knowledge
EV  FileDeleted(file_id, workspace_id, occurred_at)
```
- **INV‑F1:** `storage_key` مسبوق بـ`workspace_id/` (عزل على مستوى الكائن).
- **INV‑F2:** لا يُتاح ملف للاستخدام إلا بحالة `ready`.
- **INV‑F3:** أي وكيل داخل الـWorkspace يقرأ أي ملف `ready` (مِلكية على مستوى Workspace لا الوكيل).
- **Use‑cases:** `RegisterUpload`, `CompleteUpload`, `ListFiles`, `SoftDeleteFile`.

---

## 7) `knowledge` — RAG (استيعاب/فهرسة/استرجاع)

يحوّل الملفات إلى وثائق مُقطَّعة ومُفهرَسة. **الاستيعاب عملية ثقيلة → مدفوعة بالأحداث** (knowledge_worker).

```text
AR  Document { id, workspace_id, file_id, status: IndexStatus, chunk_count, error,
               created_at, updated_at, version }
E   Chunk    { id, document_id, workspace_id, seq: int, text, token_count, vector_ref: VectorRef }
VO  IndexStatus (pending | indexing | indexed | failed)
VO  VectorRef   (collection + point_id في Qdrant)
EV  DocumentRegistered(document_id, workspace_id, file_id, occurred_at)   # داخلي
EV  DocumentIndexed(document_id, workspace_id, chunk_count, occurred_at)  # عالمي → إشعار WS
EV  DocumentIndexingFailed(document_id, workspace_id, reason, occurred_at)
```
- **INV‑K1:** `Chunk.seq` فريد داخل الوثيقة (`UNIQUE(document_id, seq)`) — يخدم Idempotency (`DD‑09`).
- **INV‑K2:** انتقالات الحالة أحادية الاتجاه: `pending → indexing → (indexed | failed)`.
- **INV‑K3:** إعادة المعالجة بعد `failed` تبدأ وثيقة/إصداراً جديداً منطقياً (لا كتابة فوق chunks قديمة).
- **Use‑cases:** `RegisterDocumentFromFile`, `IndexDocument` (worker), `RetrieveContext(workspace, query, k)`.

---

## 8) `media` — توليد الصور/الفيديو

مهامّ توليد **ثقيلة → مدفوعة بالأحداث** (media_worker). الناتج يُحفَظ عبر `files`/MinIO.

```text
AR  MediaJob { id, workspace_id, agent_key: AgentKey, kind: MediaKind, prompt, params: GenParams,
               status: JobStatus, result_file_id, error, created_by, created_at, updated_at, version }
VO  MediaKind  (image | video)
VO  GenParams  (قاموس مُتحقَّق منه حسب النوع: أبعاد/مدّة/نموذج… ضمن حدود 07)
VO  JobStatus  (queued | running | succeeded | failed)
EV  MediaRequested(job_id, workspace_id, kind, occurred_at)    # عالمي → media_worker
EV  MediaGenerated(job_id, workspace_id, result_file_id, occurred_at)  # عالمي → إشعار WS
EV  MediaFailed(job_id, workspace_id, reason, occurred_at)
```
- **INV‑MJ1:** `succeeded` ⇒ `result_file_id NOT NULL`.
- **INV‑MJ2:** انتقالات: `queued → running → (succeeded | failed)`؛ لا رجوع.
- **INV‑MJ3:** `GenParams` مُتحقَّق منها مقابل حدود `07-nfr-slo.md` قبل الوضع في المجرى.
- **Use‑cases:** `RequestMedia`, `RunMediaJob` (worker), `GetJobStatus`.

---

## 9) `integrations` — الموصّلات والتكاملات (تنفيذ FR‑120…124)

موصّلات OAuth بطرف ثالث وخوادم MCP **بعيدة (HTTP/SSE) حصراً** لكل Workspace. الأسرار **مُعمّاة عبر Vault Transit** (`SEC‑07`)؛ الاستهلاك عبر نظام الأدوات لا باقتران مباشر.

```text
AR  Connection { id, workspace_id, connector_key: ConnectorKey, display_name, status: ConnectionStatus,
                 scopes, token_ref: CipherRef, expires_at, last_error, created_by,
                 created_at, updated_at, version }
AR  McpServer  { id, workspace_id, name, endpoint: McpEndpoint, auth_ref: CipherRef | None,
                 status: McpStatus, created_by, created_at, updated_at, version }
VO  ConnectorKey      (مُعرّف موصّل من الكتالوج: github | slack | …)
VO  ConnectionStatus  (pending | connected | revoked | error)
VO  McpEndpoint       (url + transport ∈ {http, sse} — بعيد حصراً؛ stdio مرفوض في v1)
VO  McpStatus         (active | disabled | error)
VO  CipherRef         (نص مُعمّى من Vault Transit + معرّف مفتاح — لا سرّ خام؛ نفس نمط credentials)
VO  OAuthTokens       (access + refresh + expiry + scopes — عابر، لا يُخزَّن خاماً)
EV  ConnectionEstablished(connection_id, workspace_id, connector_key, occurred_at)   # داخلي
EV  ConnectionRevoked(connection_id, occurred_at)
EV  TokenRefreshed(connection_id, expires_at, occurred_at)                            # تجديد كسول
EV  McpServerRegistered(server_id, workspace_id, occurred_at)
```
- **INV‑I1:** أسرار OAuth/الموصّل **لا تُخزَّن خاماً** ولا تُعاد عبر أي API — `token_ref` مُعمّى فقط (`FR‑121`, `SEC‑07`).
- **INV‑I2:** `McpEndpoint.transport ∈ {http, sse}` حصراً؛ رفض stdio/عملية فرعية (`ARC‑15` — يتبع `sandbox`).
- **INV‑I3:** **تجديد كسول** (`FR‑124`): قبل كل استخدام يُفحَص `expires_at`؛ إن انتهى يُجدَّد تزامنياً عبر `refresh_token` — بلا اعتماد على `scheduling`.
- **INV‑I4:** الاستهلاك عبر **نظام الأدوات** خلف عقد موحّد (`FR‑122`)؛ الاكتشاف عبر منفذ `ToolCatalog` لا اقتران مباشر بموصّل.
- **INV‑I5:** لا مرجع FK عابر لوحدات أخرى؛ المرجعية بالـ`id` فقط (`DAT‑02`).
- **Use‑cases:** `BeginConnection`, `CompleteOAuth`, `RefreshConnectionToken` (كسول), `RevokeConnection`, `RegisterMcpServer`, `DiscoverWorkspaceTools`, `InvokeConnectorTool`.

---

## 10) `usage` — القياس والحصص (تنفيذ FR‑130…134)

قياس استهلاك الرموز/التكلفة وفرض الحصص/الميزانيات **خارج الناقل**: التقاط متزامن في سجلّ append‑only، وفرض يعيد كائن قرار. الفوترة المالية خارج v1.

```text
AR  UsageRecord { id, workspace_id, agent_key, provider, tokens, cost_micros,
                  operation_id: OperationId, created_at }          # append‑only، بلا version/deleted_at
RM  UsageRollup { workspace_id, agent_key, provider, period: Period, period_start,
                  tokens_sum, cost_micros_sum, updated_at }        # Projection/Read‑model مشتقّ من UsageRecord (لا هوية `id` مستقلّة؛ مفتاحه مركّب) — لا Entity بهوية
AR  UsageLimit  { id, workspace_id, scope: LimitScope, scope_key, metric: Metric,
                  period: Period, limit_value, created_at, updated_at, version }
VO  OperationId  (مفتاح إدمبوتنسي طبيعي — مثل operation_id أو (agent_run_id, provider, seq))
VO  Period       (day | month)
VO  LimitScope   (workspace | agent | provider)
VO  Metric       (tokens | cost_micros)
VO  Decision     (allowed: bool, reason?, remaining?, reservation_id?=None)   # كائن قرار — لا bool
EV  UsageRecorded(record_id, workspace_id, agent_key, provider, occurred_at)   # داخلي فقط
EV  LimitExceeded(workspace_id, scope, metric, occurred_at)                    # داخلي فقط
```
- **INV‑U1:** `operation_id` فريد داخل الـWorkspace (`UNIQUE(workspace_id, operation_id)`) ⇒ إعادة الالتقاط **لا تُضاعف العدّ** (تُتجاهَل بهدوء) (`FR‑134`).
- **INV‑U2:** السجلّ **append‑only** (`DAT‑07`) — لا تعديل/حذف لقيد استهلاك؛ التصحيح بقيد معاكس إن لزم.
- **INV‑U3:** **الفرض يعيد `Decision`** (لا `bool`)، وعقده قابل للتطوّر إلى **reserve/commit** بلا كسر (`reservation_id` يبقى `None` في v1) (`FR‑132`).
- **INV‑U4:** منافذ `usage` الواردة تُستدعى من **المُنسِّق (طبقة الوكلاء) حصراً** — لا من داخل وحدة أعمال أخرى (`ARC‑07/08`).
- **INV‑U5:** **بلا Redis Streams** — الالتقاط متزامن على المسار الساخن (`FR‑131`, `EVT‑10`)؛ التجميع/التدوير شأن داخلي.
- **INV‑U6:** قيم الحدود متوائمة مع جداول SLO (`FR‑133`, `OQ‑02`).
- **Use‑cases:** `EnforceLimit(agent, provider) → Decision`, `CaptureUsage(charge)` (idempotent), `RollupUsage` (داخلي), `SetLimits`, `GetUsageSummary`.

---

## علاقات عابرة للوحدات (بالإشارة فقط — لا FK)

| من | إلى | الآلية |
|----|-----|--------|
| `files.FileUploaded` | `knowledge.RegisterDocumentFromFile` | حدث عالمي (Redis Streams) |
| `knowledge.DocumentIndexed` | إشعار العميل | حدث عالمي → WebSocket |
| `media.MediaGenerated` | `files` (حفظ الناتج) + إشعار | حدث عالمي |
| أي وكيل | `conversations`/`memory`/`files`/`knowledge` | عبر Inbound Port محقون (لا استيراد مباشر) |
| المُنسِّق (طبقة الوكلاء) | `usage` (فرض قبل + التقاط بعد) | منفذان واردان **متزامنان** (لا أحداث) |
| نظام الأدوات / الوكيل | `integrations` (اكتشاف/استدعاء أداة موصّل/MCP) | منفذ `ToolCatalog` وارد (لا اقتران بموصّل) |

> جميع المراجع بين الوحدات بالـ`id` (UUID). لا وحدة تستورد نطاق أخرى ولا جداولها (`DD‑01`). استدعاءات `usage`/`integrations` تعبر الحدود عبر **منافذها الواردة فقط**.
