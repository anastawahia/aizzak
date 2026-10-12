<div dir="rtl">

# نشر AIZZAK على RunPod (بيئة staging)

> **ما هذه الوثيقة؟** دليل تشغيل المكدّس كاملاً على Pod واحد في RunPod بمعالج رسوميّات (GPU)، وهو هدف الـstaging للمشروع (`CLAUDE.md`، قسم «Staging & deploy»).
>
> **الوثائق المرتبطة:**
> - بناء الصورة وتجربتها على جهازك خطوةً بخطوة: [`delivery/runpod-staging/build-image.html`](delivery/runpod-staging/build-image.html).
> - إنشاء الـPod من واجهة RunPod خطوةً بخطوة: [`delivery/runpod-staging/deploy-pod.html`](delivery/runpod-staging/deploy-pod.html).
> - التشغيل المحلّيّ بـDocker Compose: [`quickstart.md`](quickstart.md).
> - المرجع المُلزِم للقرارات التشغيليّة: [`design/08-local-runbook.md`](design/08-local-runbook.md).
>
> **آخر مطابقةٍ مع الواقع: 2026‑10‑10** (الصورة `staging-1` أقلعت محلّيّاً على قرصٍ نظيف، ونجح الإقلاعان الأوّل والثاني والفحص الأعمق). وفي اليوم نفسه كشف أوّل Pod على RunPod أنّ Network Volume لا يصلح للمكدّس، فصار التخزين Volume Disk ([§5.4](#54-التخزين)). وفي 2026‑10‑12 كشف أوّل رفعِ ملفٍّ من المتصفّح أنّ وكيل RunPod يغيّر ترويسة `Host`، فصارت الروابط المُوقَّعة تمرّ عبر nginx على المنفذ 80 (الصورة `staging-3`، [§5.3](#53-المنافذ)). وبعده كشف أوّل ملفٍّ مرفوع أنّ الصورة لا تشغّل عامل الفهرسة، فبقي المستند `pending`، فصار لكلّ عاملٍ برنامجه (الصورة `staging-4`).

> ⚠️ **مرحلة staging (قرار المالك، 2026‑10‑06):**
> - كشف المكدّس للعامّة عبر رابط RunPod مسموح، والبيانات الحقيقيّة مسموحة.
> - التقوية الأمنيّة مؤجّلةٌ إلى ما بعد أوّل إقلاعٍ على RunPod. كلّ ثغرةٍ تُكتشف تُسجَّل في [§8.4](#84-قائمة-الأمان-المؤجّلة-لمرحلة-staging) ولا توقف النشر.
> - يجوز للوكلاء النشر دون إنسان: البناء والرفع، وإنشاء الـPod وتحديثه وإعادة تشغيله. ويُبلَّغ عن كلّ نشرٍ بأربعة أشياء: وسم الصورة، ومعرّف الـPod، والرابط، ونتيجة `/health`.
> - **حذف Pod أو Network Volume يحتاج موافقة إنسان**، لأنّه يمحو البيانات. ومع Volume Disk ‏([§5.4](#54-التخزين)) يمحو حذفُ الـPod بياناتِه معه.
> - الإنتاج (الموجة 8) ما زال مؤجّلاً، وقرار إطلاقه بشريّ.

---

## 0) القيد الذي يحكم كلّ ما بعده

**RunPod لا يشغّل `docker compose`.** الـPod هو نفسه حاوية واحدة تعمل من صورةٍ واحدة، ولا يوجد داخله خادم Docker. وثائق RunPod تقول ذلك صراحةً:

> *"Docker Compose is not supported: Runpod runs Docker for you, so you cannot spin up your own Docker instance or use Docker Compose on Pods."*

لذلك تجمع صورة `deploy/runpod/` كلّ العمليّات في حاويةٍ واحدة:

| ما يحدث محلّيّاً | ما يقابله على RunPod |
|---|---|
| اثنتا عشرة حاوية، لكلٍّ منها صورتها | **صورةٌ واحدة** تحوي كلّ العمليّات |
| `depends_on` + `condition: service_healthy` | **supervisord** + سكربت إقلاعٍ (`aizzak-bootstrap.sh`) يفرض الترتيب |
| شبكة Docker الداخليّة (`minio:9000`) | **loopback** (`127.0.0.1:9000`) داخل الحاوية |
| أحجام Docker | قرصٌ دائم على المسار `/workspace` |

> **الخيار المعتمد:** صورةٌ واحدةٌ شاملة على Pod واحدٍ بمعالج رسوميّات. البديل الأرخص (خادمٌ عاديّ للمكدّس + Pod لـOllama وحده) موصوفٌ باختصار في [§11](#11-المسار-البديل--خادم-عاديّ--pod-لـollama-وحده).

---

## 1) ما يعمل داخل الـPod

```
┌─────────────────── RunPod Pod (حاوية واحدة) ───────────────────┐
│  supervisord: يبدأ كلّ عمليّة ويعيد تشغيل ما يسقط               │
│                                                                │
│  ── منشورٌ للعالم ──────────────────────────────────────────    │
│  nginx       :80    ← https://<POD_ID>-80.proxy.runpod.net     │
│    └ /workspace-files/ ← الروابط المُوقَّعة، تمرّ إلى MinIO :9000 │
│  sshd        :22    (منفذ TCP، للصيانة)                         │
│                                                                │
│  ── داخليٌّ فقط على 127.0.0.1 ─────────────────────────────    │
│  gunicorn/app :8000     PostgreSQL 16 :5432                    │
│  Redis streams :6379    Redis cache   :6380                    │
│  Qdrant       :6333     Vault         :8200                    │
│  التضمين      :8080     Ollama        :11434  ← يستخدم الـGPU   │
│  عامل memory + مُرحّل Outbox (بلا منافذ)                        │
│                                                                │
│  /workspace/data ← postgres · redis · minio · qdrant ·          │
│                    vault · vault-init · نماذج ollama · logs     │
└────────────────────────────────────────────────────────────────┘
```

كونسول MinIO يستمع على `9001` داخل الـPod، **لكنّه لا يُنشر** في الـstaging. للوصول إليه استعمل نفق SSH ([§8.2](#82-السطح-المكشوف)).

### الانحرافات المقصودة عن مكدّس Compose

| الانحراف | السبب |
|---|---|
| **لا PgBouncer** | تجميع المعاملات (`D‑21`) يخدم عدّة نسخٍ من التطبيق. في Pod واحدٍ تطبيقٌ واحدٌ بتجمّعٍ واحد. النصف الآخر من `OPS‑02` (`statement_cache_size=0`) مفروضٌ في `create_engine` على أيّ حال |
| **لا TLS داخل الـPod** | وكيل RunPod ينهي TLS ويمرّر HTTP عاديّاً إلى المنفذ 80 |
| **Ollama داخل الصورة** | في Compose هو على المضيف. هنا هو سبب استئجار الـGPU |
| **بيئتا Python منفصلتان** | `/opt/venv` للتطبيق و`/opt/venv-emb` لـtorch، فلا يدخل torch اعتماديّات التطبيق |
| **كلّ عاملٍ بنسخةٍ واحدة** | supervisord يشغّل برنامجاً لكلّ عامل: `worker` (يقرأ `WORKER`، والقيمة الافتراضيّة `memory`)، و`worker-knowledge`، و`worker-media`. Compose يشغّل نسختين من knowledge. قبل `staging-4` كان في الصورة `worker` وحده، فلم يُفهرَس أيّ ملفّ (`tests/unit/test_deploy_worker_default.py`) |
| **لا نسخ احتياطيّ ولا مراقبة آليّة** | لا `ops-scheduler` ولا Prometheus ولا تنبيهات داخل الصورة. مسجّلٌ في [§8.4](#84-قائمة-الأمان-المؤجّلة-لمرحلة-staging) |

---

## 2) المتطلّبات المسبقة

### 2.1 على جهازك

| المتطلّب | كيف تتحقّق | ملاحظة |
|---|---|---|
| **WSL Ubuntu‑24.04** | `wsl -d Ubuntu-24.04 -- echo ok` | نفّذ كلّ الأوامر داخل WSL |
| **Docker** | `docker --version` | مقيس: `29.6.1` |
| **مساحة حرّة ≥ 40 ج.ب** | `df -h /var/lib/docker` | الصورة ≈ 3.6 ج.ب، والبناء يحتاج مساحةً مؤقّتةً أكبر |
| **إنترنت مستقرّ للرفع** | — | الرفع ≈ 3.6 ج.ب. على وصلةٍ منزليّة قد يأخذ ساعة أو أكثر |

### 2.2 الحسابات

1. **RunPod:** [runpod.io](https://www.runpod.io)، ثمّ اشحن رصيداً.
   - النشر من الواجهة لا يحتاج مفتاح API. يحتاجه الوكيل فقط إن أنشأ الـPod بـ`runpodctl`، فأنشئه حينها من `Settings → API Keys`.
   - احفظه في ملفٍّ خارج المستودع بصلاحيّة 600، ولا تلصقه في محادثة:
     ```bash
     install -m 600 /dev/null ~/.runpod-api-key && read -rs -p "API key: " k && printf '%s' "$k" > ~/.runpod-api-key && unset k && echo
     ```
2. **Docker Hub:** اسم المستخدم المعتمد `anastawahia`، والمستودع `anastawahia/aizzak-runpod`.
3. **Firebase:** `FIREBASE_PROJECT_ID=aizzak-agent` (أكّده المالك، 2026‑10‑10).

> ⚠️ `FIREBASE_PROJECT_ID` **إلزاميّ**. `FirebaseAuth` يفشل عند الإنشاء إن كان فارغاً، فالعَرَض تطبيقٌ لا يقلع إطلاقاً، لا مصادقةٌ معطّلة.

### 2.3 مفتاح SSH

```bash
ls ~/.ssh/id_ed25519.pub 2>/dev/null || ssh-keygen -t ed25519 -C "aizzak-runpod" -N "" -f ~/.ssh/id_ed25519
```

أضف محتوى `~/.ssh/id_ed25519.pub` في RunPod من `Settings → SSH Public Keys`. يحقنه RunPod في كلّ Pod عبر المتغيّر `PUBLIC_KEY`، و`entrypoint.sh` يضعه في `authorized_keys`. بلا مفتاح لا يقبل sshd أيّ دخول، لأنّ الدخول بكلمة سرّ غير مفعّل.

---

## 3) كلمات السرّ

يطلب `entrypoint.sh` عشر كلمات سرّ واسم مستخدم MinIO و`FIREBASE_PROJECT_ID`، ولا يقلع بدونها. ولّدها مع `APP_ENV` في ملفٍّ واحدٍ خارج المستودع بصلاحيّة 600، دون طباعتها (13 سطراً). الأمر لا يلمس ملفّاً موجوداً:

```bash
test -e ~/aizzak-runpod-staging.env || { install -m 600 /dev/null ~/aizzak-runpod-staging.env && { for k in POSTGRES_SUPERUSER_PASSWORD AIZZAK_OWNER_PASSWORD APP_RW_PASSWORD OUTBOX_RELAY_PASSWORD RETENTION_SWEEPER_PASSWORD METRICS_READER_PASSWORD TRANSIT_ROTATOR_PASSWORD WORKSPACE_PURGER_PASSWORD BACKUP_OPERATOR_PASSWORD MINIO_ROOT_PASSWORD; do printf '%s=%s\n' "$k" "$(openssl rand -hex 24)"; done; echo MINIO_ROOT_USER=aizzak-minio; echo FIREBASE_PROJECT_ID=aizzak-agent; echo APP_ENV=staging; } > ~/aizzak-runpod-staging.env; }
```

> ⚠️ **لا تستعمل الرمز `%` في أيّ قيمة.** supervisord يفسّر `%(...)s` في ملفّ إعداده. `openssl rand -hex` يولّد أحرفاً سداسيّةً عشريّةً فقط.
>
> ⚠️ **احتفظ بالملفّ ما دام قرص بيانات الـPod قائماً.** العنقود يُهيَّأ بكلمة سرّ المستخدم الفائق مرّةً واحدة، وتغييرها بعد ذلك يكسر الإقلاع ([§9](#9-التشغيل-اليوميّ)).

| المتغيّر | الدور | ما يملكه |
|---|---|---|
| `POSTGRES_SUPERUSER_PASSWORD` | `postgres` | تهيئة العنقود وإنشاء الأدوار فقط |
| `AIZZAK_OWNER_PASSWORD` | `aizzak_owner` | المُهاجِر ومالك الجداول. لا يخدم أيّ طلب |
| `APP_RW_PASSWORD` | `app_rw` | التطبيق والعامل. بلا `BYPASSRLS` وليس مالكاً، فسياسة RLS تحصره في مساحة عملٍ واحدة |
| `OUTBOX_RELAY_PASSWORD` | `outbox_relay` | `SELECT/UPDATE` على `platform.outbox` فقط (`D‑18`) |
| `RETENTION_SWEEPER_PASSWORD` | `retention_sweeper` | مكنسة الاحتفاظ اليدويّة (`python -m app.ops.retention`) |
| `METRICS_READER_PASSWORD` | `metrics_reader` | قراءة `/metrics` داخل التطبيق |
| `TRANSIT_ROTATOR_PASSWORD` | `transit_rotator` | تدوير مفتاح Transit يدويّاً (`python -m app.ops.rotate_transit`) |
| `WORKSPACE_PURGER_PASSWORD` | `workspace_purger` | حذف مساحة عملٍ نهائيّاً (`python -m app.ops.purge`) |
| `BACKUP_OPERATOR_PASSWORD` | `backup_operator` | الدور الوحيد الذي يقرأ كلّ صفوف المستأجرين، للنسخ الاحتياطيّ |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | MinIO root | تخزين الكائنات |

**ما لا تولّده بنفسك:**
- **توكن Vault الجذر ومفتاح فكّ الختم:** يولّدهما `deploy/vault/start.sh` عند أوّل إقلاع ([§8.1](#81-مادّة-تهيئة-vault-على-القرص)).
- **دخول التطبيق إلى Vault (AppRole):** `aizzak-bootstrap.sh` يصدر `secret_id` جديداً في **كلّ** إقلاع، ويكتبه في `/run/aizzak/vault-approle.env` (‏0600، على قرص الحاوية لا على الـvolume). الغلاف `aizzak-with-vault` يسلّمه لـ`app` وللعمّال الثلاثة عند بدئها. لا تضع `VAULT_ROLE_ID` ولا `VAULT_SECRET_ID` في القالب.

---

## 4) بناء الصورة ورفعها

الخطوات المفصّلة مع الفحوص في [`delivery/runpod-staging/build-image.html`](delivery/runpod-staging/build-image.html). الملخّص:

```bash
cd /home/AIZZAK && docker build --platform linux/amd64 -f deploy/runpod/Dockerfile -t aizzak-runpod:staging-1 .
```

| الجزء | لماذا |
|---|---|
| `--platform linux/amd64` | خوادم RunPod كلّها x86‑64. صورة ARM تفشل بـ`exec format error` بعد رفعٍ طويل |
| `-f deploy/runpod/Dockerfile` | ملفّ البناء ليس في الجذر |
| `.` في آخر الأمر | سياق البناء هو جذر المستودع. `.dockerignore` يُبقي خارجه `.venv` و`.git` وملفّات الأسرار وبيانات التحميل |

تأكّد أنّ الصورة تحمل إصلاحات الإقلاع الأوّل قبل رفعها (الناتج يجب أن يكون `11`):

```bash
docker run --rm aizzak-runpod:staging-1 grep -c 'HOME="/home/aizzak"' /etc/supervisor/supervisord.conf
```

ثمّ الرفع:

```bash
docker tag aizzak-runpod:staging-1 anastawahia/aizzak-runpod:staging-1
```

```bash
docker push anastawahia/aizzak-runpod:staging-1
```

> - إن فشل الرفع بـ`502 Bad Gateway` أو انقطع، فأعد الأمر نفسه. هذا عطلٌ مؤقّت في Docker Hub، والإعادة ترفع الطبقات الناقصة فقط.
> - **وسمٌ جديدٌ لكلّ تحديث:** `staging-2`، `staging-3`... لا تعِد استخدام وسمٍ قديم، فقد يستعمل RunPod النسخة المخزّنة عنده.

> ⚠️ **أيّ تعديلٍ في `src/` أو `deploy/` يعيد رفع طبقةٍ بحجم 564 م.ب.** خطوة `RUN … chown -R aizzak:aizzak /app /opt/qdrant /opt/hf-cache` في `deploy/runpod/Dockerfile` تأتي بعد نسخ الشيفرة، فتُبنى من جديد مع كلّ تعديل، وتنسخ معها ملفّات النموذج كلّها. على خطّ رفعٍ بطيء (قيس 2026‑10‑11: نحو 150 ك.ب/ث) ينقطع الرفع قبل أن يكتمل.
>
> - **العلاج المقترح (يحتاج موافقة، لأنّه في `deploy/`):** تغيير مالك `/opt/qdrant` و`/opt/hf-cache` قبل نسخ الشيفرة، ونسخ الشيفرة بـ`COPY --chown`. عندها يرفع التحديث بضعة ميغابايتات فقط.
> - **ما فعلناه لـ`staging-2`:** بنيناه طبقةً صغيرةً فوق `staging-1` المرفوع أصلاً، بعد التحقّق من أنّ ملفّات `/app` الـ610 وسكربتات `/usr/local/bin` وإعدادات `/etc` مطابقةٌ للالتزام `9d60943` بايتاً ببايت، إلّا `entrypoint.sh`. فالمحتوى مطابقٌ لبناءٍ كامل من `deploy/runpod/Dockerfile`:
>
> ```dockerfile
> FROM anastawahia/aizzak-runpod:staging-1@sha256:368f2ab916b4b31370a07e6419eff51c3279d64814c06c72a59f78664671e8dc
> COPY --chmod=755 deploy/runpod/entrypoint.sh /usr/local/bin/entrypoint.sh
> COPY --chown=aizzak:aizzak --chmod=644 deploy/runpod/entrypoint.sh /app/deploy/runpod/entrypoint.sh
> LABEL org.opencontainers.image.revision="9d60943"
> ```
>
> الناتج: `anastawahia/aizzak-runpod:staging-2@sha256:4279d520d8cee59830e4c3aa8b0b07b327cbac609977b81bba96c38cad52a519`.
>
> - **ما فعلناه لـ`staging-3`:** بالطريقة نفسها، طبقةً صغيرةً فوق `staging-2`. تحمل إصلاح الروابط المُوقَّعة ([§5.3](#53-المنافذ))، وقيمة `shared_buffers` الجديدة في `deploy/postgres/postgresql.conf`. تحقّقنا من أنّ ملفّات `/app` الـ610 وسكربتات `/usr/local/bin` و`supervisord.conf` و`nginx.conf` مطابقةٌ للالتزام `346a743` بايتاً ببايت:
>
> ```dockerfile
> FROM anastawahia/aizzak-runpod:staging-2@sha256:4279d520d8cee59830e4c3aa8b0b07b327cbac609977b81bba96c38cad52a519
> COPY --chmod=755 deploy/runpod/entrypoint.sh /usr/local/bin/entrypoint.sh
> COPY --chmod=644 deploy/runpod/nginx.conf /etc/nginx/nginx.conf
> COPY --chown=aizzak:aizzak --chmod=644 deploy/runpod/entrypoint.sh deploy/runpod/nginx.conf deploy/runpod/Dockerfile /app/deploy/runpod/
> COPY --chown=aizzak:aizzak --chmod=644 deploy/smoke/stack_smoke.py /app/deploy/smoke/stack_smoke.py
> COPY --chown=aizzak:aizzak --chmod=644 deploy/postgres/postgresql.conf /app/deploy/postgres/postgresql.conf
> LABEL org.opencontainers.image.revision="346a743"
> ```
>
> الناتج: `anastawahia/aizzak-runpod:staging-3@sha256:15f30d190671be717508c65994f5eb1f28c8961b1eff2d81ee35647724666280`.
>
> - **ما فعلناه لـ`staging-4`:** طبقةً صغيرةً فوق `staging-3`. تضيف `[program:worker-knowledge]` و`[program:worker-media]`، فيُفهرَس الملفّ المرفوع ([§1](#الانحرافات-المقصودة-عن-مكدّس-compose)). تحقّقنا من أنّ الملفّات الأربعة في `/usr/local/bin` و`/etc` و`/app/deploy/runpod` مطابقةٌ للالتزام `4f6867e` بايتاً ببايت:
>
> ```dockerfile
> FROM anastawahia/aizzak-runpod:staging-3@sha256:15f30d190671be717508c65994f5eb1f28c8961b1eff2d81ee35647724666280
> COPY --chmod=755 deploy/runpod/entrypoint.sh /usr/local/bin/entrypoint.sh
> COPY --chmod=755 deploy/runpod/bootstrap.sh /usr/local/bin/aizzak-bootstrap.sh
> COPY --chmod=644 deploy/runpod/supervisord.conf /etc/supervisor/supervisord.conf
> COPY --chown=aizzak:aizzak --chmod=644 deploy/runpod/entrypoint.sh deploy/runpod/supervisord.conf /app/deploy/runpod/
> COPY --chown=aizzak:aizzak --chmod=755 deploy/runpod/bootstrap.sh /app/deploy/runpod/bootstrap.sh
> LABEL org.opencontainers.image.revision="4f6867e"
> ```
>
> الناتج: `anastawahia/aizzak-runpod:staging-4@sha256:57302e3daadef7afb7e8d4424c9fbd2110c9a21d625c4a25573ec254d24b246d`.
>
> ⚠️ **حالة الـPod الحاليّ `le15fwpryplqwa` (2026‑10‑12):** يعمل بـ`staging-2`، وإصلاحا `staging-3` و`staging-4` مطبَّقان عليه يدويّاً دون إعادة تشغيل: `/etc/nginx/nginx.conf` الجديد، و`MINIO_PUBLIC_ENDPOINT` في `[program:app]`، و`[program:worker-knowledge]` و`[program:worker-media]` في `supervisord.conf`. النسختان القديمتان بلاحقة `.staging-2`. وأُنشئ **بلا Volume Disk** (`volumeInGb = 0`)، فبياناته كلّها على قرص الحاوية، ويمسحها أيّ إيقافٍ أو تعديل، ومعها الإصلاح اليدويّ. **التعديل القادم يجمع ثلاثة أشياء:** الصورة `staging-4`، وVolume Disk ‏50 ج.ب ([§5.4](#54-التخزين))، وحذف المنفذ 9000. وبعده احفظ مفتاح Vault الجديد ([§8.1](#81-مادّة-تهيئة-vault-على-القرص)).

---

## 5) إنشاء الـPod

الخطوات بالترتيب من واجهة RunPod (القرص، ثمّ القالب، ثمّ الـPod، ثمّ الفحص وحفظ مفتاح Vault) في [`delivery/runpod-staging/deploy-pod.html`](delivery/runpod-staging/deploy-pod.html). هذا القسم يشرح كلّ قيمةٍ ولماذا.

> ⚠️ **القالب يجب أن يكون `Private`.** القالب العامّ يكشف متغيّرات البيئة، أي كلمات السرّ، لكلّ من يراه.

### 5.1 العتاد

| البند | القيمة | لماذا |
|---|---|---|
| النوع | **GPU Pod** | Ollama هو المستفيد. على المعالج وحده يصير توليد النصّ بطيئاً جدّاً |
| البطاقة | **16 ج.ب** (مثل RTX A4000 أو RTX 4000 Ada) | `gemma3:1b` صغير. ارفع الفئة فقط إن غيّرت النموذج إلى أكبر |
| السحابة | **Secure Cloud** | أثبت من Community Cloud |
| المنطقة | أيّ مركزٍ فيه بطاقة 16 ج.ب متاحة | قرص البيانات يُنشأ مع الـPod على جهازه، فلا قيد على المركز ([§5.4](#54-التخزين)) |

### 5.2 القالب

| الحقل | القيمة | ملاحظة |
|---|---|---|
| **Container Image** | `anastawahia/aizzak-runpod:staging-4` | المستودع عامّ، فلا حاجة لـRegistry Credentials |
| **Container Start Command** | **فارغ** | الصورة تعرّف `CMD`. أيّ قيمةٍ هنا تلغيه |
| **Container Disk** | **20 GB** | الصورة ≈ 3.6 ج.ب، وكلّ البيانات على `/workspace` |
| **Volume Disk** | **50 GB** | قرص البيانات ([§5.4](#54-التخزين)). يُزاد لاحقاً ولا يُنقَص |
| **Volume Mount Path** | `/workspace` | ⚠️ بالضبط هكذا. كلّ مسارات البيانات تحت `/workspace/data` |

### 5.3 المنافذ

| الحقل | القيمة |
|---|---|
| **Expose HTTP Ports** | `80` |
| **Expose TCP Ports** | `22` |

- `80`: واجهة التطبيق على `https://<POD_ID>-80.proxy.runpod.net`.
- **روابط الملفات المُوقَّعة تمرّ عبر `80` أيضاً، تحت `/workspace-files/`.** الرابط يُوقَّع بـSigV4 على اسم المضيف، ووكيل RunPod يستبدل ترويسة `Host` بعنوان الـPod الداخليّ (مثل `100.65.14.203:60576`) ويضع الاسم العامّ في `X-Forwarded-Host`. فكلّ رابطٍ يصل MinIO مباشرةً عبر `<POD_ID>-9000` يُرفض بـ`403 SignatureDoesNotMatch`، وهذا ما أفشل أوّل رفعٍ من الواجهة. لذلك يشتقّ `entrypoint.sh` قيمة `MINIO_PUBLIC_ENDPOINT` على `<POD_ID>-80.proxy.runpod.net`، وnginx يعيد الاسم العامّ إلى `Host` قبل أن يمرّر الطلب إلى MinIO (`$minio_signed_host` في `deploy/runpod/nginx.conf`).
- `9000` **لا يُنشر**. Pod أُنشئ قبل `staging-3` بالقيمة `80,9000` يعمل كما هو، والمنفذ 9000 فيه زائد: احذفه عند أوّل تعديلٍ للـPod.
- `22`: SSH كمنفذ **TCP** لا HTTP.
- `9001` (كونسول MinIO) **لا يُنشر**. استعمل نفق SSH عند الحاجة ([§8.2](#82-السطح-المكشوف)).

> ⚠️ **حدّ المئة ثانية:** وكيل RunPod الـHTTP خلف Cloudflare، ويقطع أيّ اتّصالٍ بعد نحو 100 ثانية. والتطبيق يبثّ عبر SSE وWebSocket.
>
> إن رأيت البثّ ينقطع عند نحو 100 ثانية، فانشر `80` كمنفذ **TCP** بدل HTTP، واستعمل العنوان المباشر من `Connect → Direct TCP Ports` (بصيغة `http://<IP>:<PORT>`). لا يمكن نشر المنفذ نفسه على HTTP وTCP معاً.

### 5.4 التخزين

| النوع | متى يُمحى؟ | يصلح للمكدّس؟ |
|---|---|---|
| **Container Disk** | عند **كلّ** إيقافٍ للـPod. كلّ ما هو خارج `/workspace` يضيع | لا |
| **Volume Disk** | عند حذف الـPod (Terminate) | **نعم، وهو المعتمد** |
| **Network Volume** | يبقى مستقلّاً عن الـPod | **لا** |

- **استعمل Volume Disk بحجم 50 ج.ب على `/workspace`، ولا تربط Network Volume.**
- **لماذا لا Network Volume؟** أوّل Pod (2026‑10‑10) مات عند `chown: changing ownership of '/workspace/data/postgres': Operation not permitted`، وأعاد RunPod تشغيله بلا نهاية:
  - القرص الشبكيّ يرفض تغيير مالك الملفّات، وPostgreSQL يرفض مجلّد بياناتٍ لا يملكه مستخدمه.
  - وQdrant لا يعمل على أنظمة ملفّات الشبكة أصلاً، بنصّ وثائقه.
  - `entrypoint.sh` يفحص ذلك الآن قبل تهيئة قاعدة البيانات، ويتوقّف برسالة `⛔ cannot chown under /workspace -- is it a RunPod Network Volume?`.
- **ثمن هذا الخيار:**
  - **Terminate يمحو البيانات.** لذلك يحتاج موافقة إنسان، وتؤخذ قبله نسخةٌ احتياطيّة ([§9](#9-التشغيل-اليوميّ)).
  - **القرص مربوطٌ بالجهاز الذي نشأ عليه الـPod.** الإيقاف (Stop) يحرّر الكرت، فقد يؤجَّر لغيرك، ويقلع الـPod عند Start **بلا GPU**. حينها انتظر وأعد المحاولة، أو أقلعه بلا GPU لتأخذ نسخةً احتياطيّة ثمّ أنشئ Pod جديداً. لذلك تجنّب Stop إلّا عند الحاجة.
  - عطل الجهاز نفسه يُفقد البيانات. هذا يرفع أولويّة ث‑6 ([§8.4](#84-قائمة-الأمان-المؤجّلة-لمرحلة-staging)).
- إن لم يُربط حجم، فسيحذّر سجلّ الإقلاع: `⚠️ /workspace is NOT a mounted volume. All data will be LOST`.

### 5.5 متغيّرات البيئة

| المفتاح | القيمة | إلزاميّ؟ |
|---|---|---|
| كلمات السرّ العشر + `MINIO_ROOT_USER` | من ملفّ [§3](#3-كلمات-السرّ) | ✅ |
| `FIREBASE_PROJECT_ID` | `aizzak-agent` (أكّده المالك، 2026‑10‑10) | ✅ |
| `APP_ENV` | `staging` | موصى به (الافتراضيّ `production`، ويظهر في سجلّ الإقلاع) |
| `OLLAMA_MODEL` | `gemma3:1b` | اختياريّ (هذا هو الافتراضيّ) |
| `WEB_CONCURRENCY` | `2` | اختياريّ |
| `LOG_LEVEL` | `INFO` | اختياريّ |

**ما لا تضبطه، وهذا مقصود:**
- `entrypoint.sh` يشتقّ وحده: `DATABASE_URL`، `REDIS_URL`، `CACHE_REDIS_URL`، `MINIO_ENDPOINT`، `QDRANT_URL`، `VAULT_ADDR`، `EMBEDDING_SERVICE_URL`، `OLLAMA_BASE_URL`، `PROVIDER_ROUTING`، `USAGE_DEFAULT_LIMITS`.
- `MINIO_PUBLIC_ENDPOINT` و`OAUTH_REDIRECT_BASE_URL` يُبنيان من `RUNPOD_POD_ID` الذي يحقنه RunPod، لأنّك لا تعرف معرّف الـPod قبل إنشائه.

> 💡 **بديلٌ للقالب:** ضع المتغيّرات في `/workspace/.env` (بصيغة `KEY=value`) عبر SSH. `entrypoint.sh` يقرأه في كلّ إقلاع، **وما في بيئة القالب يفوز على الملفّ**.

---

## 6) أوّل إقلاع

افتح `Pods → <الـPod> → Logs`. المراحل بالترتيب:

| # | ما تراه في السجلّ | ماذا يجري |
|---|---|---|
| 1 | `Downloading image…` | RunPod يسحب الصورة (مرّةً لكلّ خادم) |
| 2 | `[entrypoint] initialising a new PostgreSQL 16 cluster` | `initdb` على `/workspace/data/postgres`. **أوّل مرّةٍ فقط** |
| 3 | `vault-start: … attempting first-time initialization` ثمّ `vault-start: unsealed and ready` | `operator init` و`unseal`، ويُسجَّل مفتاح فكّ الختم والتوكن الجذر في `vault-init/init.json`. **أوّل مرّةٍ فقط** |
| 4 | `[bootstrap] creating roles …` ثمّ `creating extension pg_stat_statements` | سكربتا المشروع `10-roles.sh` و`20-extensions.sh` كما هما |
| 5 | `[bootstrap] seeding Vault (KV v2 + Transit + AppRole)` ثمّ `minting the app's AppRole login` | `deploy/vault/bootstrap.sh`، ثمّ `secret_id` جديد للتطبيق |
| 6 | `[bootstrap] creating bucket workspace-files` | `deploy/minio/bootstrap.sh` |
| 7 | `[bootstrap] running migrations + grants (app.ops.provision)` | الهجرات ثمّ المنح |
| 8 | `[bootstrap] pulling gemma3:1b (first boot only …)` | تنزيل النموذج إلى `/workspace/data/ollama`. **أوّل مرّةٍ فقط** |
| 9 | `waiting for embedding service` ← `starting workers` ← `starting outbox relay` ← `starting the API` ← `starting nginx` | طبقة التطبيق بهذا الترتيب |
| 10 | `[bootstrap] ✅ boot complete` | جاهز |

**المدّة المقيسة محلّيّاً (2026‑10‑09، على المعالج دون GPU):** نحو 7 دقائق لأوّل إقلاع، أغلبها تنزيل النموذج. ونحو دقيقةٍ لكلّ إقلاعٍ بعده. على RunPod يُضاف وقت سحب الصورة.

> ⚠️ **قبل `boot complete` يكون `app` و`worker` و`worker-knowledge` و`worker-media` و`outbox-relay` و`nginx` في حالة `STOPPED  Not started`، ولا يجيب `/health`.** هذا طبيعيّ وليس عطلاً: الإقلاع يبدأها بنفسه بعد اكتمال الخطوات السابقة.

### لماذا هذا الترتيب

- **الهجرات قبل التطبيق:** `app.ops.provision` يمنح الصلاحيّات بعد الهجرات. بدونها يتّصل `app_rw` بلا أيّ صلاحيّة، ويجيب أوّل طلبٍ يلمس جدولاً بـ`permission denied`.
- **دخول Vault قبل التطبيق:** لا يمكن أن يوجد `secret_id` قبل تهيئة Vault، فيُصدَر في الإقلاع ويُقرأ عند بدء `app` والعمّال.
- **nginx أخيراً:** لا تُفتح الحافّة إلّا بعد أن يجيب `/health` فعلاً.

### ليس عطلاً

| ما تراه | التفسير |
|---|---|
| `PUBLIC_KEY unset -- sshd will accept no logins` | لم يُضف مفتاح SSH في الحساب ([§2.3](#23-مفتاح-ssh)) |
| `[ERROR] … path is already in use` من Vault | يظهر عند إعادة بذر Vault في كلّ إقلاع. لا أثر له |
| أشرطة تقدّم كثيرة من Ollama | تنزيل النموذج في أوّل إقلاع |

---

## 7) التحقّق

من جهازك:

```bash
curl -s https://<POD_ID>-80.proxy.runpod.net/health
```

```bash
curl -s https://<POD_ID>-80.proxy.runpod.net/health/ready
```

المتوقّع: `{"status":"ok"}` ثمّ `{"status":"ready"}`، وكلاهما برمز 200.

> ⚠️ `/health/ready` لا يلمس أيّ تبعيّة عمداً، فهو يعني «انتهى الإقلاع» فقط. الدليل على أنّ مسار البيانات يعمل هو الفحص الأعمق أدناه.

### الدخول عبر SSH

من `Connect → Direct TCP Ports` خذ الـIP والمنفذ المقابل لـ22:

```bash
ssh root@<IP> -p <PORT> -i ~/.ssh/id_ed25519
```

### حالة العمليّات

```bash
supervisorctl -c /etc/supervisor/supervisord.conf status
```

السليم: كلّ شيءٍ `RUNNING` ما عدا `bootstrap`، الذي يجب أن يكون `EXITED` بعد أن أنهى عمله.

### الفحص الأعمق (من داخل الـPod)

جلسة SSH لا ترث بيئة الإقلاع، لذلك يعيد هذا الأمر استعمال بيئة gunicorn نفسها. ويعمل بالمستخدم `aizzak`، لأنّ root داخل الحاوية لا يقرأ بيئة عمليّة مستخدمٍ آخر:

```bash
cd /app && gosu aizzak sh -c 'pid=$(pgrep -o -f "gunicorn app.api.main"); xargs -0 -a /proc/$pid/environ sh -c '"'"'exec env -i "$@" /opt/venv/bin/python /app/deploy/smoke/stack_smoke.py'"'"' _'
```

المتوقّع في آخر سطر: `PASS: pgbouncer/OPS-02, RLS fail-closed, and presign addressing all verified`، والرابط المُوقَّع يذكر `<POD_ID>-80.proxy.runpod.net`. وقبله السطر `[4] presigned PUT through the public address stored the bytes  OK`: الفحص يرفع ملفّاً صغيراً فعلاً عبر وكيل RunPod ثمّ يحذفه، لأنّ قراءة نصّ الرابط وحدها لم تكشف عطل `Host`.

ثمّ خدمة التضمين والنموذج والـGPU:

```bash
curl -s 127.0.0.1:8080/health && curl -s 127.0.0.1:11434/api/tags
```

```bash
nvidia-smi && ollama ps
```

---

## 8) الأمان

### 8.1 مادّة تهيئة Vault على القرص

Vault دائم: `deploy/vault/start.sh` يشغّله بتخزين `file` تحت `/workspace/data/vault`. فلا تضيع KV ولا Transit ولا AppRole عند إعادة التشغيل، ولا يُعاد توليد مفتاح Transit أبداً.

**لكن لا يوجد KMS داخل Pod واحد.** لذلك يكتب `start.sh` **مفتاح فكّ الختم والتوكن الجذر في ملفٍّ نصّيٍّ عاديّ**: `/workspace/data/vault-init/init.json` (‏0600). من يقرأ هذا الملفّ يستطيع فكّ ختم Vault وقراءة كلّ سرٍّ فيه:
- عمليّةٌ أخرى على الـPod؛
- أو نسخةٌ غير مشفّرة من `/workspace`؛
- أو دخول SSH بمفتاحٍ مسروق.

هذا مقبولٌ للـstaging بقرار المالك، وغير مقبولٍ للإنتاج.

**النسخ الاحتياطيّ للملفّ:** هو المفتاح الوحيد لفكّ ختم Vault على هذا القرص. فقدانه مع بقاء `vault/` يعني بياناتٍ لا تُفكّ أبداً. انسخه بعد أوّل إقلاعٍ إلى مكانٍ مشفّرٍ منفصل (مدير كلمات سرّ)، ولا تطبعه في سجلٍّ أو محادثة:

```bash
runpodctl send /workspace/data/vault-init/init.json
```

**الاستعادة:** إن فُقد الملفّ وبقيت بيانات `vault/` سليمة، فأعد النسخة إلى المسار نفسه بصلاحيّة 600 قبل إقلاع `[program:vault]`. `start.sh` يجده ويفكّ الختم به، ويرفض أن يعيد تهيئة Vault مُهيَّأً لا يطابقه ملفّ.

**مسار الإنتاج (وصفٌ لا تنفيذ):** فكّ الختم الآليّ عبر KMS خارجيّ (`seal "awskms"` أو `"gcpckms"` أو `"azurekeyvault"`)، أو عبر Vault آخر خارج الـPod (Transit auto‑unseal). كلاهما يحتاج تلك الخدمة موجودةً مسبقاً.

### 8.2 السطح المكشوف

| المنفذ | مكشوف؟ | التقييم |
|---|---|---|
| 80 (nginx) | ✅ عامّ | مقصود. المصادقة عبر Firebase وحرّاس RBAC، وفيه حدود الطلبات (`limit_req`) |
| 9000 (MinIO API) | ⛔ غير منشور منذ `staging-3` | الروابط المُوقَّعة تمرّ عبر 80 ([§5.3](#53-المنافذ)). Pod قديمٌ ما زال ينشره: احذفه عند أوّل تعديل |
| 80 تحت `/workspace-files/` | ✅ عامّ | يمرّ إلى MinIO. الدلو خاصّ، والوصول بتوقيعٍ مؤقّتٍ فقط. دلو النسخ الاحتياطيّة لا مسار له |
| 22 (SSH) | ✅ عامّ | بمفتاحٍ فقط، بالمستخدم root |
| 9001 (كونسول MinIO) | ⛔ غير منشور | عبر نفق SSH فقط |
| البقيّة | ⛔ `127.0.0.1` | لا يصلها شيءٌ من خارج الـPod |

نفق كونسول MinIO عند الحاجة:

```bash
ssh -N -L 9001:127.0.0.1:9001 root@<IP> -p <PORT> -i ~/.ssh/id_ed25519
```

### 8.3 نطاقٌ خاصّ بدل رابط الوكيل (وHSTS)

- رابط `https://<POD_ID>-80.proxy.runpod.net` يتغيّر مع كلّ Pod جديد.
- لنطاقٍ ثابت ضع Cloudflare Tunnel أو وكيلاً عكسيّاً أمامه، ثمّ اضبط `OAUTH_REDIRECT_BASE_URL` و`MINIO_PUBLIC_ENDPOINT` يدويّاً في القالب.
- **HSTS غائبٌ عمداً على رابط الوكيل.** خريطة `$hsts_header` في `deploy/runpod/nginx.conf` فارغة، لأنّ اسم `proxy.runpod.net` مؤقّت ويُعاد استعماله لـPods لا علاقة لها بنا. أضف إليها **نطاقك أنت فقط** حين يصير لك نطاق.

### 8.4 قائمة الأمان المؤجّلة لمرحلة staging

ثغراتٌ معروفة قُبلت للـstaging ([قرار المالك](#نشر-aizzak-على-runpod-بيئة-staging))، وتُعالَج بعد أوّل إقلاعٍ على RunPod. أضف إلى هذا الجدول كلّ ثغرةٍ جديدة تجدها، برقمٍ جديد (`ث‑N`، ث = ثغرة) لا يُعاد استعماله.

| # | الثغرة | الأثر | العلاج المقترح |
|---|---|---|---|
| ث‑1 | مفتاح فكّ ختم Vault والتوكن الجذر نصٌّ عاديّ في `vault-init/init.json` على الـvolume نفسه ([§8.1](#81-مادّة-تهيئة-vault-على-القرص)) | من يقرأ الملفّ يملك كلّ أسرار Vault | KMS خارجيّ أو Vault خارجيّ لفكّ الختم، وإبطال التوكن الجذر بعد البذر |
| ث‑2 | كلمات السرّ العشر وُلّدت مرّةً لهذه المرحلة، وتُحفظ في ملفٍّ على جهاز المشغّل وفي متغيّرات القالب | تسرّب الملفّ أو لقطة شاشة للقالب يكشفها كلّها | تدويرها بعد أوّل تشغيل، ونقلها إلى RunPod Secrets بدل نصّ القالب |
| ث‑3 | Ollama يُثبَّت بـ`curl -fsSL https://ollama.com/install.sh \| sh` دون تثبيت إصدارٍ أو تحقّقٍ من البصمة (`deploy/runpod/Dockerfile`) | بناءان في يومين مختلفين قد يحملان Ollama مختلفاً، أو شيفرةً غير متوقّعة | تنزيل أرشيفٍ بإصدارٍ محدّد والتحقّق من `sha256` |
| ث‑4 | لا HSTS على رابط الوكيل ([§8.3](#83-نطاقٌ-خاصّ-بدل-رابط-الوكيل-وhsts)) | لا حماية من تخفيض البروتوكول عند أوّل زيارة | نطاقٌ خاصّ ثابت ثمّ تفعيل HSTS له |
| ث‑5 | `secret_id_ttl=720h` (30 يوماً) طويل، ولا شيء يجدّده إلّا إعادة تشغيل الـPod | `secret_id` مسروق يبقى صالحاً شهراً. وPod يعمل أكثر من 30 يوماً دون إعادة تشغيل يفقد دخوله إلى Vault عند أوّل تجديدٍ للتوكن | تجديدٌ دوريّ للـ`secret_id` وتقصير مدّته. وإلى أن يُنفَّذ: أعد تشغيل الـPod قبل 30 يوماً ([§9](#9-التشغيل-اليوميّ)) |
| ث‑6 | لا نسخ احتياطيّ آليّ ولا مراقبة ولا تنبيهات داخل الـPod | فقدان الـvolume يعني فقدان البيانات، وعطلٌ صامت لا يراه أحد. ومع Volume Disk ‏([§5.4](#54-التخزين)) يكفي لذلك حذف الـPod أو عطل جهازه | `ops-scheduler` وشحن النسخ إلى تخزينٍ خارجيّ، ومراقبة `/health` من خارج الـPod |
| ث‑7 | مستودع الصورة على Docker Hub عامّ | أيّ أحدٍ يسحب الصورة ويرى إعدادات المكدّس. لا أسرار فيها (فحص 4‑ب في دليل البناء)، والكود عامٌّ على GitHub أصلاً | مستودعٌ خاصّ مع Registry Credentials في RunPod |
| ث‑8 | SSH بالمستخدم root على منفذٍ عامّ | مفتاحٌ مسروق يعطي كلّ شيء، بما فيه `init.json` | مستخدمٌ غير root، أو إغلاق 22 وفتحه عند الحاجة فقط |
| ث‑9 | التطبيق يعمل بـ`APP_ENV=staging` على بياناتٍ حقيقيّة مع الثغرات أعلاه | قبولٌ صريح من المالك للمرحلة | مراجعة هذه القائمة قبل أيّ انتقالٍ إلى الإنتاج (الموجة 8) |

كونسول MinIO (9001) غير منشور، فقد عولجت هذه الثغرة قبل أوّل نشر.

---

## 9) التشغيل اليوميّ

### إيقافٌ وتشغيل

| العمليّة | الأثر | من ينفّذها |
|---|---|---|
| **Restart** | يُعيد الإقلاع، ويُصدر `secret_id` جديداً | الوكيل أو الإنسان |
| **Stop** | يُمحى قرص الحاوية ويبقى `/workspace`. تدفع ثمن التخزين فقط. ⚠️ يحرّر الكرت، فقد يقلع Start بلا GPU ([§5.4](#54-التخزين)) | الوكيل أو الإنسان |
| **Start** | يسحب الصورة ثانيةً، ويجد العنقود قائماً فلا يعيد تهيئته | الوكيل أو الإنسان |
| **Terminate** | يُحذف الـPod **ومعه قرص البيانات**. خذ نسخةً احتياطيّة أوّلاً | ⚠️ **موافقة إنسان** |

> ⚠️ **أعد تشغيل الـPod مرّةً على الأقلّ كلّ 30 يوماً**، إلى أن يُعالَج ث‑5 في [§8.4](#84-قائمة-الأمان-المؤجّلة-لمرحلة-staging).

### تحديث الصورة بعد تعديل الشيفرة

1. ابنِ وارفع بوسمٍ جديد (`staging-5` مثلاً) كما في [§4](#4-بناء-الصورة-ورفعها).
2. في RunPod: `Edit Pod → Container Image → anastawahia/aizzak-runpod:staging-5 → Save`. ⚠️ على Pod بلا Volume Disk يمسح هذا كلّ البيانات.
3. يعيد الـPod الإقلاع، وتُطبَّق الهجرات الجديدة تلقائيّاً عبر `aizzak-bootstrap.sh`.
4. أبلغ عن النشر: الوسم، ومعرّف الـPod، والرابط، ونتيجة `/health`.

### تغيير كلمات السرّ

- **أدوار قاعدة البيانات التسعة** (كلّ شيءٍ ما عدا `POSTGRES_SUPERUSER_PASSWORD`): غيّر القيمة في القالب ثمّ أعد التشغيل. `10-roles.sh` يعيد ضبط كلمات السرّ في كلّ إقلاع.
- **`POSTGRES_SUPERUSER_PASSWORD`:** لا تغيّره في القالب وحده. الإقلاع يتّصل بها، والعنقود يحمل القيمة القديمة، فيفشل عند `waiting for PostgreSQL` أو بعدها.

### نسخٌ احتياطيّ يدويّ

على الـPod (اتّصالٌ محلّيّ بالمستخدم postgres، فلا حاجة لكلمة سرّ):

```bash
gosu postgres pg_dump -Fc -d aizzak > /workspace/backup-$(date +%F).dump
```

ثمّ اسحبه إلى جهازك بـ`runpodctl send <الملفّ>` على الـPod، و`runpodctl receive <الرمز>` على جهازك.

---

## 10) جدول الأعطال

| العَرَض | السبب والعلاج |
|---|---|
| الـPod يقلع ثمّ يموت فوراً، والسجلّ يبدأ بـ`⛔ <VAR> is not set` | متغيّرٌ إلزاميّ ناقص ([§5.5](#55-متغيّرات-البيئة)) |
| البناء يتوقّف عند `RUN curl -fsSL https://ollama.com/install.sh \| sh` برسالة `requires zstd` | الصورة تُبنى من شيفرةٍ قديمة. حدّث الفرع وأعد البناء |
| `vault-start: ⛔ the HTTP listener never came up (60s)` أو `qdrant FATAL` أو خطأ تحقّقٍ في `provider_routing.llm` | الصورة قديمة، بُنيت قبل إصلاحات الإقلاع الأوّل. فحص `HOME` في [§4](#4-بناء-الصورة-ورفعها) يجب أن يعطي `11` |
| `app` في حالة `STOPPED  Not started` و`/health` لا يجيب | الإقلاع لم يكتمل بعد، خاصّةً في أوّل مرّة (تنزيل النموذج). انتظر `✅ boot complete` |
| `exec format error` | الصورة بُنيت لـARM. أعد البناء بـ`--platform linux/amd64` |
| `Downloading image` عالقٌ ثمّ يفشل | Container Disk صغير. ارفعه |
| `chown: changing ownership of '/workspace/data/postgres': Operation not permitted` يتكرّر، أو `⛔ cannot chown under /workspace` | الـPod مربوطٌ بـNetwork Volume. احذفه وأنشئه بلا Network Volume، مع Volume Disk ‏50 ج.ب ([§5.4](#54-التخزين)) |
| الـPod يقلع بعد Start بلا GPU (`nvidia-smi` لا يجد كرتاً) | كرت الجهاز صار مؤجَّراً لغيرك أثناء الإيقاف ([§5.4](#54-التخزين)) |
| `[bootstrap] ⛔ PostgreSQL did not come up` | راجع سطور `postgres` في السجلّ. غالباً `/workspace` بلا حجمٍ مربوط أو صلاحيّاتٌ خاطئة |
| `provisioning failed` | اقرأ التتبّع فوقه. غالباً `AIZZAK_OWNER_PASSWORD` لا يطابق حجماً قديماً، أو كلمة سرّ المستخدم الفائق تغيّرت ([§9](#9-التشغيل-اليوميّ)) |
| `[with-vault] ⛔ … is not readable` | `app` أو أحد العمّال بدأ قبل أن يُصدر الإقلاع دخول Vault. راجع سجلّ `bootstrap` |
| التطبيق لا يقلع إطلاقاً | `FIREBASE_PROJECT_ID` فارغ |
| 502 من رابط الوكيل | `supervisorctl status app`: إن كان `RUNNING` فراجع nginx، وإن كان `FATAL` فالسجلّ يحمل السبب |
| البثّ ينقطع عند نحو 100 ثانية | حدّ وكيل RunPod. انشر `80` كمنفذ TCP ([§5.3](#53-المنافذ)) |
| رفع الملفّ يفشل في الواجهة (`File upload failed (403)`)، وMinIO يردّ `SignatureDoesNotMatch` | الرابط المُوقَّع يذكر `<POD_ID>-9000`: صورةٌ أقدم من `staging-3`، أو `MINIO_PUBLIC_ENDPOINT` مضبوطٌ يدويّاً على 9000. وكيل RunPod يغيّر `Host` فيفشل التوقيع. استعمل `staging-3` أو أحدث، ولا تضبط المتغيّر يدويّاً ([§5.3](#53-المنافذ)) |
| الملفّ يُرفع لكنّ الفهرسة لا تنتهي (المستند `pending`) | لا أحد يقرأ `stream.knowledge`: صورةٌ أقدم من `staging-4`، أو `worker-knowledge` متوقّف. افحص `supervisorctl status worker-knowledge` و`redis-cli -p 6379 xinfo groups stream.knowledge` (‏`consumers 0` يعني لا عامل). الحدث ينتظر في المجرى، فيُفهرَس المستند حين يبدأ العامل |
| `POST /search` يعيد 503 | خدمة التضمين لم تحمّل النموذج بعد: `supervisorctl status embedding` |
| نداءات LLM تفشل بـ404 | النموذج غير مسحوب: `ollama list`، ثمّ `ollama pull gemma3:1b` |
| `secret does not exist in Vault` | الإقلاع لم يكتمل بعد. إن كان `bootstrap` في حالة `EXITED` برمزٍ غير 0 فاقرأ سجلّه |
| `vault-start: ⛔ 'operator init' failed, and …/init.json does not exist` | بيانات Vault مُهيَّأة لكنّ `init.json` مفقود. استعِد نسخته ([§8.1](#81-مادّة-تهيئة-vault-على-القرص)) |
| `vault-start: unseal FAILED with the recorded key` | `init.json` لا يطابق بيانات Vault الحاليّة. لا حلّ آليّ: استعِد زوجاً متطابقاً من نسخةٍ احتياطيّة |
| التطبيق يفقد Vault بعد شهرٍ دون إعادة تشغيل | انتهى `secret_id` (ث‑5). أعد تشغيل الـPod |
| صفر صفوفٍ رغم وجود بيانات | لم يُضبط `app.workspace_id` (RLS). راجع `ExecutionContext` |
| فقدت البيانات بعد إعادة تشغيل | لم يكن هناك حجمٌ مربوطٌ على `/workspace` ([§5.4](#54-التخزين)) |

أوامر تشخيصٍ سريعة من داخل الـPod:

```bash
supervisorctl -c /etc/supervisor/supervisord.conf status
```

```bash
supervisorctl -c /etc/supervisor/supervisord.conf tail -1000 app stderr
```

```bash
supervisorctl -c /etc/supervisor/supervisord.conf restart app
```

---

## 11) المسار البديل — خادم عاديّ + Pod لـOllama وحده

كلّ خدمات المشروع تعمل على المعالج، والمستفيد الوحيد من الـGPU هو Ollama، وهو أصلاً خارج مكدّس Compose (`OLLAMA_BASE_URL`). فإن كان الهدف تسريع النموذج فقط:

1. شغّل المكدّس كما هو بـ`docker compose up -d` على خادمٍ لينكسيّ عاديّ، بكلمات سرّ [§3](#3-كلمات-السرّ).
2. أنشئ Pod على RunPod من القالب الرسميّ `ollama/ollama`، وانشر المنفذ `11434`.
3. اضبط `OLLAMA_BASE_URL` في `.env` على الخادم إلى عنوان الـPod، ثمّ أعد إنشاء `app`.

> ⚠️ وكيل RunPod عامّ: من يعرف الرابط يستعمل نموذجك. استعمل Global Networking (`<POD_ID>.runpod.internal`) أو نفق SSH بدل الرابط العامّ.

---

## 12) القواعد الذهبيّة

1. RunPod لا يشغّل `docker compose`: صورةٌ واحدة لا اثنتا عشرة.
2. كلّ ما هو خارج `/workspace` يضيع عند الإيقاف.
3. `--platform linux/amd64` في كلّ بناء.
4. وسمٌ جديدٌ لكلّ تحديث (`staging-N`).
5. `MINIO_PUBLIC_ENDPOINT` هو `<POD_ID>-80.proxy.runpod.net` (عبر nginx)، لا منفذ MinIO: وكيل RunPod يغيّر `Host`، فيكسر كلّ رابطٍ مُوقَّعٍ يصل MinIO مباشرةً.
6. `vault-init/init.json` هو مفتاح كلّ الأسرار: انسخه احتياطيّاً ولا تطبعه.
7. `FIREBASE_PROJECT_ID` الفارغ يعني تطبيقاً لا يقلع.
8. `/health` الأخضر ليس دليلاً على مسار البيانات؛ الدليل هو `deploy/smoke/stack_smoke.py`.
9. حذف Pod يحتاج موافقة إنسان، لأنّه يمحو قرص بياناته معه.
10. كلّ نشرٍ يُبلَّغ عنه: الوسم، ومعرّف الـPod، والرابط، ونتيجة `/health`.
11. لا Network Volume: PostgreSQL وQdrant لا يعملان عليه. البيانات على Volume Disk.

---

## المراجع

- [Runpod Pods — Overview](https://docs.runpod.io/pods/overview): «Docker Compose is not supported».
- [Expose ports](https://docs.runpod.io/pods/configuration/expose-ports): وكيل HTTP، ومنافذ TCP، وحدّ المئة ثانية.
- [Environment variables](https://docs.runpod.io/pods/templates/environment-variables): `RUNPOD_POD_ID` وغيره.
- [Storage types](https://docs.runpod.io/pods/storage/types): Volume Disk يبقى عند الإيقاف ويُمحى عند الحذف.
- [Zero GPUs](https://docs.runpod.io/pods/troubleshooting/zero-gpus): Pod بقرصٍ محلّيّ مربوطٌ بجهازه، وقد يقلع بلا GPU بعد الإيقاف.
- [Network volumes](https://docs.runpod.io/storage/network-volumes): الثبات والربط وقت الإنشاء.
- [Network volume permissions](https://www.answeroverflow.com/m/1306342259364597790): تقريرٌ من مجتمع RunPod بأنّ صلاحيّات PostgreSQL لا تُضبط على القرص الشبكيّ.
- [Qdrant installation requirements](https://qdrant.tech/documentation/installation/): لا يعمل على NFS ولا على تخزين الكائنات.
- [Connect to a Pod with SSH](https://docs.runpod.io/pods/configuration/use-ssh): sshd في صورةٍ مخصّصة.
- [Global networking](https://docs.runpod.io/pods/networking): `<POD_ID>.runpod.internal`.

</div>
