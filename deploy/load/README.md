# `deploy/load/` — the capacity harness

Step **0.1** of [`docs/capacity-plan.md`](../../docs/capacity-plan.md). Wave 0
blocks every wave after it, and this directory is most of Wave 0's first step:
without it, every number in waves 1–8 is a guess.

English here, like the rest of `deploy/`; the plan and its status document are
Arabic, like the rest of `docs/`.

---

## 1. What it runs

Five scenarios, all at once, because §0's targets are simultaneous:

| scenario | file | peak load | what it is for |
|---|---|---|---|
| `browse` | `scenarios/browse.js` | ~253 rps | the auth path (`ح‑2`), the pool (`ح‑3`), the edge (`ح‑9`) |
| `rag` | `scenarios/rag.js` | 40 rps | the embedding service (`ح‑5`), Qdrant (`ح‑11`) |
| `stream` | `scenarios/stream.js` | 5 starts/s → 50 concurrent | the provider (`ح‑1`), time to first token |
| `index` | `scenarios/index_file.js` | 100/min | the worker engine (`ح‑6`), end to end |
| `ws` | `scenarios/ws_hold.js` | 1,500 sockets | `worker_connections` (`ح‑9`), the Redis registry (`ح‑10`) |

The rates are **derived, not typed**: `lib/config.js` holds §0's targets and
§3's equations and computes every rate from them, so changing a target changes
the profile and the arithmetic stays visible. `browse` takes the remainder of
the 300 rps rather than an absolute rate of its own — otherwise the five
scenarios would sum to more than the target they claim to test.

**`rag` asks many questions, not the same six.** `lib/queries.js` spells up
to 262,144 distinct questions from the seed's own vocabulary, so the sparse
leg of the hybrid search meets seeded text, and asks them with a Zipf-like
popularity: `LOAD_RAG_QUERIES` distinct ones (default 100,000) at exponent
`LOAD_RAG_QUERY_ZIPF` (default 0.8; 0 is uniform). Both are stamped into
`assumptions.rag_queries`, because how often a question repeats inside step
4.3's cache window follows from those two numbers, not from the platform: at
the defaults and a 600 s TTL, ~24% of requests repeat at 13 q/s (the 100 rps
step) and ~44% at 40 q/s (peak). Until 2026‑09‑28 the scenario rotated six
fixed questions, which with the cache on was ~100% hits by construction
(`د‑37`). Those shares are what the stream offers the cache; what the cache
actually answered is the platform's own count, `aizzak_embedding_cache_total`
(`hit`, `miss`, `unavailable`, one per question), read from Prometheus over
the run's window (`د‑38`, `08 §2‑ط`). The result file does not carry it.

**Three profiles over that one mix.** `peak.js` runs it at 300 rps for 30
minutes; `average.js` at 50 rps (8 hours by default, `LOAD_DURATION_S` to
shorten); `step.js` runs it at rising rates — `LOAD_STEPS` (default
`50,100,150,200`), `LOAD_STEP_S` each (default 360) — one full set of the
five scenarios per step, each tagged `step:rpsNNN`. The step profile exists
because the 2026‑09‑26 peak run offered 300 rps to a platform that serves
~160, and a constant rate above a ceiling measures the queue in front of it,
not the ceiling. Its result carries a `steps` table (per step: rps offered
and served, dropped arrivals, p50/p95/p99 per budget, error rate, index
timeouts) and a `knee`: `sustained_rps` — the highest step that, with every
step below it, dropped nothing and stayed inside the 0.1 % error budget — and
`within_budget_rps`, which adds 07 §2's four latency budgets. Index timeouts
decide neither (§6). Drops above the knee are the finding, so for this
profile `validity.rate_delivered` is judged on the lowest step only.

**And one profile outside that mix: `backlog.js`**, the load of capacity
5.5's live acceptance (`08 §4.21`) — §0's 100 index jobs a minute for 20
minutes (`LOAD_DURATION_S`), and nothing else. The runbook stops
`worker-knowledge` around it; the profile only offers the arrivals. Its
scenario (`scenarios/index_backlog.js`) differs from `index` in three ways,
each for a reason:

* **It stops at the 202.** `index` polls every job until the worker answers,
  and with the worker stopped none ever does — a VU parked on every arrival.
  The verdict is per message, in SQL, after the worker catches up.
* **Its document is two lines, one chunk** (`index`'s is ~40 KB, 37 chunks).
  Loss does not depend on size, and the backlog must drain before the verdict
  can be read: 2,000 big documents are three to four hours at 8–10 a minute.
* **The token is chosen per job, not per VU.** A job that ends at its 202
  holds a VU for a fraction of a second, so a few VUs serve every arrival,
  and a VU-bound token would put the run on two users — past 1.3's 30
  heavy jobs a minute in the first minute. `setup()` refuses a pool too small
  to stay under that and under the 100 files of room each upload space keeps.

Its result adds a `backlog` block: `offered`, `accepted` (each 202 is one
`knowledge.document.registered` event — the number the SQL verdict must find
twice) and `dropped`.

**And one for tenant isolation: `abuse.js`**, the half of step 1.2's
acceptance a functional test cannot reach — "an abusive tenant in a k6 test
does not raise its neighbour's p95". After a warm-up of neighbour traffic
that nothing judges (`LOAD_ABUSE_WARMUP_S`, default 60 s — a cold first phase
is a slow reference, and the abuser would pass it too easily), three phases
of `LOAD_ABUSE_PHASE_S` (default 300 s): the neighbours alone, the
neighbours with one abusive tenant, the neighbours alone again. Both sides run `browse`'s mix, so the
abuser differs in rate and nothing else.

* **The neighbours** are every pool entry but one, at `LOAD_NEIGHBOUR_RPS`
  (default §0's average, 50 rps), the token chosen per iteration so each
  stays far below its own 120/min — `setup()` refuses a pool where they
  would not, since their own 429s would read as the abuser reaching them.
* **The abuser** is the remaining entry (`LOAD_ABUSER_INDEX`, default the
  last) at `LOAD_ABUSE_RPS`, default **100 rps — 1.2's own figure** for a
  tenant of fifty users at the ceiling (6,000/min). `INV‑W1` gives each user
  one workspace and no membership route exists, so that volume can only come
  from ONE user here, and the user bucket holds it; the tenant bucket
  (2,400/min) sits above it and cannot bind first.
* **Two quiet phases, not one,** so a stack that drifts on its own during the
  run shows up as its two references disagreeing (`quiet_spread_pct`), not as
  an effect of the abuser. The abuser is charged only with what the worse of
  them did not also do.

Every 429 in the harness is now tagged with the ceiling that refused it
(`scope`: `user`, `workspace`, `in_flight`, `edge`, `heavy`, `other`),
read from the body — `lib/metrics.js` `refusalScope`. The result adds an
`isolation` block: per phase the neighbours' p95 for reads and writes, their
429s and errors; the comparison, at `LOAD_NEIGHBOUR_TOLERANCE` (default 10 %);
and the abuser's requests, how many were admitted against the most a 60 s
sliding log can admit in the phase, and which ceiling refused the rest.
`isolation.held` needs all six of its `checks`, and `run.sh` exits 99 when it
is false — the verdict is a comparison between phases, which no k6 threshold
can express.

---

## 2. The token pool — condition (1)

§0.1: a run on a stub authenticator reproduces `07-nfr-slo §2`'s existing
number and means nothing, because the auth path **is** bottleneck `ح‑2` (at
least two database round trips per request, before any work).

```
FIREBASE_WEB_API_KEY='<Project settings → Web API key>' \
  python -m app.ops.mint_load_tokens mint --count 500
```

That one command is the whole recipe. For each account it signs up an
Email/Password user (`accounts:signUp`, the project's public Web API key —
no service account is involved, see `lib/auth.js`), makes the first
authenticated request so the platform's own JIT provisioning creates the
tenant and returns its id (`GET /api/v1/me/context`), and creates the space
every scenario but one needs (`POST /api/v1/spaces`, س-32). It writes three
files, all gitignored:

| File | Reader | Holds |
|---|---|---|
| `tokens.json` | k6 (`lib/auth.js`) | `tokens[]` of `workspace` / `space_id` / `id_token`, `"stub": false` — plus what each space holds, which `verify` reads and k6 ignores |
| `accounts.json` | `mint_load_tokens` itself | the refresh token and password of every account — what `refresh` and `delete` need |
| `include-workspaces.txt` | `app.ops.load_seed run` | one tenant id per line, for `--include-workspace` (§3) |

**Three things stay with the owner, all in the Firebase console**, and the
tool says so rather than working around them: the Web API key; the
Email/Password provider (`Authentication → Sign-in method`, or every sign-up
answers `OPERATION_NOT_ALLOWED`); and the **sign-up quota** — Firebase caps
account creation at **100 accounts per hour per IP address**, so a 500-account
run from one machine stalls at 100 with `TOO_MANY_ATTEMPTS_TRY_LATER` unless a
temporary increase was scheduled first (`Authentication → Settings → Sign-up
quota`). The tool does not fail on that: sign-ups go through one lane, a
refusal pauses it for a minute and prints the fix once, every finished account
is saved, and `mint` re-run continues from where it stopped (an account whose
tenant was not provisioned is finished, not recreated). It gives up after
`--max-quota-wait-s` (65 minutes) with the state intact.

**Size the pool from the profile.** `peak` holds 1,500 sockets and §0 derives
them as 500 users × 3 tabs against a `ws_connections_per_user` ceiling of 5.
A 200-token pool would put 7.5 sockets on each user, the platform would
correctly refuse a third of them, and the report would show a WebSocket
failure rate that is the limiter working. `lib/profile.js` refuses to start
above 3 sockets per user for exactly that reason — and so does
`mint_load_tokens verify`, a second earlier and before the seed:

```
python -m app.ops.mint_load_tokens verify --duration-s 1800 --ws-vus 1500
```

**500 accounts are 500 tenants.** `INV‑W1` gives each user one workspace and
no membership route exists, so the pool's floor is also 500 Qdrant
collections — above §0's own 200–400, which `4.4` measured (boot 58.4 s at
202). `--count` does what it is told; whether to run above the target and
record it in the baseline, or run fewer users and fewer sockets
(`LOAD_WS_VUS`), is the operator's decision, not the tool's.

**Tokens live one hour; the seed takes tens of minutes; the order is fixed.**
`load_seed` places `--include-workspace` tenants first and derives every other
id from the ordinal, so the accounts must exist before the seed is written —
and by the time it is, the tokens are near their end. Hence `refresh`:

```
python -m app.ops.mint_load_tokens mint --count 500        # accounts + tenants
python -m app.ops.load_seed run --seed-id <id> \
  $(sed 's/^/--include-workspace /' deploy/load/include-workspaces.txt)
python -m app.ops.mint_load_tokens refresh                 # new tokens, seconds -- and each
                                                           # entry moved onto the seeded space
python -m app.ops.mint_load_tokens verify                  # profile.js's guards, early
deploy/load/run.sh peak
```

**`refresh` is also what points the pool at the seed.** The seed writes its
content into two spaces of its own per workspace (`seed-space-0/1`,
`load_seed._seed_workspace_identity`) — never into the `load` space `mint`
created — and four of the five scenarios are scoped to the entry's
`space_id` (س‑32). A pool left on `load` sends every search and listing into
an **empty** space: the filter is measured, not the platform, which is the
thing condition (3) forbids. So `refresh` lists each tenant's spaces
(`GET /api/v1/spaces` reports `file_count` / `conversation_count`) and moves
the entry onto the fullest; before the seed it reports `N at an empty space`,
and `verify` refuses such a pool (`space content`). Run it after the seed,
not only before the run.

**And uploads go somewhere else than reads.** The seed's heaviest tenants
are over the platform's own ceilings (13 of 500 content spaces above the
1 GiB byte cap, one tenant above 10,000 files), so every upload into their
content space is `409` -- the ceiling working, measured as failure: 59% of the
2026-09-28 step run's errors (`د‑33`). `refresh` therefore also writes
`upload_space_id`: a space of the same tenant with room under both ceilings,
or `null` when there is none. `index_file.js` uploads only through entries
that have one, and `verify` refuses a pool written before the field existed.

`refresh` exchanges every refresh token at `securetoken.googleapis.com`
(18,000 exchanges a minute per project is the limit; 500 is nothing) and
falls back to the password for an account whose refresh token was revoked, so
one revoked account never costs the pool a re-mint — or the seed its ids. The
30-minute `peak` profile fits inside one hour; the 8-hour `average` profile
does not, and the harness refuses to start rather than let hour two report a
100% error rate that is the harness. The pool is read once at init per VU,
so a mid-run rewrite only reaches VUs initialised afterwards; the reliable
form is to drive the soak as hour-long segments, `refresh` between them, and
concatenate the archived results.

**A pool you do not have to mint.** `deploy/load/smoke.sh` writes four
synthetic tokens, runs twenty seconds of the full peak mix and deletes them
again. Every request 401s and the error-rate threshold fails by design — what
it proves is that the harness *runs*: scripts parse, scenarios execute at
their stated rates, thresholds evaluate, the archive is written with
`valid: false` in it. Run it after every edit to this directory; §5 lists the
three bugs its first execution found.

**Cleaning up.** `python -m app.ops.mint_load_tokens delete --yes` deletes
the Firebase accounts and the three files. The tenants they created stay in
Postgres and Qdrant — `app.ops.purge` is the tool for those.

---

## 3. The seed — condition (3)

§0.1: "استعلامٌ على جدولٍ فارغٍ يقيس الفهرسَ لا المنصّة." The floor is
**1,000,000 messages · 100,000 files · 1,000,000 vectors · 200 workspaces**,
generated by a tool that respects RLS.

That tool is **`python -m app.ops.load_seed`** — read its module docstring
before the first run; it is where every choice this section only summarises is
argued.

```bash
# 1. Point THIS process at app_rw, directly at Postgres, never the pooler.
#    (`.env` holds unquoted JSON in PROVIDER_ROUTING, so `. ./.env` mangles
#    it — re-export that one raw, or run inside a container that has it.)
export DATABASE_URL="postgresql+asyncpg://app_rw:$APP_RW_PASSWORD@127.0.0.1:${HOST_PORT_POSTGRES:-15432}/aizzak"
export QDRANT_URL="http://127.0.0.1:${HOST_PORT_QDRANT:-16333}"

#    The real tenants (§2) go FIRST and take the largest shares -- a corpus
#    whose bulk sits in workspaces no VU authenticates as is one the harness
#    cannot see. So the pool is minted before the seed, and refreshed after.
INCLUDE=$(sed 's/^/--include-workspace /' deploy/load/include-workspaces.txt)
python -m app.ops.load_seed plan --seed-id dev-2026-09-03 $INCLUDE   # see the skew first
python -m app.ops.load_seed run  --seed-id dev-2026-09-03 $INCLUDE   # tens of minutes

# 2. Feed the archive what was actually written; renew the hour-old pool.
eval "$(python -m app.ops.load_seed status --seed-id dev-2026-09-03 --export)"
python -m app.ops.mint_load_tokens refresh
deploy/load/run.sh peak
```

`run` writes a manifest to `deploy/load/seeds/<seed-id>.json`; `status
--export` renders it as the `LOAD_SEED_*` block above. **The manifest is the
declaration** condition (3) asks for — a number recorded by the writer, not
remembered by the operator.

Three things worth knowing before the first run:

* **It is idempotent.** Ids derive from `(seed id, anchor, ordinal)` and every
  INSERT is `ON CONFLICT DO NOTHING`, so an interrupted run is resumed by
  running it again — not by purging and starting over.
* **`--scale` is for proving the tool, never for a baseline.** Anything below
  the floor is stamped into the manifest as such and the harness reads it.
* **`purge --seed-id ... --yes` takes it back out** — the seed's own
  workspaces only. A `--include-workspace` tenant is never purged: its content
  came from here, its account did not.

Anything below the floor sets `"valid": false` in the archived result. The run
still happens and the numbers are still real — they are just not a baseline,
and the file says so in a field rather than in a memory.

> ⚠️ **Seeding 200 workspaces needs Qdrant's file-descriptor limit raised, and
> that fix is in `docker-compose.yml` — a cluster started before it will fail.**
> One tenant is one collection, and the container's inherited soft `nofile` was
> **1024**: the seeder died on the **seventh** collection with `Too many open
> files (os error 24)`. The service now sets `ulimits.nofile` to 65536, but
> that is a container-creation setting: `docker compose up -d qdrant`
> (recreating it), not `restart`.

---

## 4. Running

```bash
deploy/load/smoke.sh          # ~30 s   (the harness itself, §2)
deploy/load/run.sh peak       # 30 min  (§7 item 1)
deploy/load/run.sh average    # 8 hours (§7 item 2)
deploy/load/run.sh backlog    # 20 min  (capacity 5.5 — `08 §4.21` stops the worker around it)
deploy/load/run.sh abuse      # 16 min  (capacity 1.2 — one abusive tenant, its neighbours' p95)
```

**k6 does not have to be installed.** It is a Go binary, not something this
project can vendor, and its absence was capacity blocker `د‑3` — it held the
acceptance of `0.1`, the peak-load half of `0.4` and the whole of `0.5`. So
the generator is also a pinned Compose service, `grafana/k6:1.3.0` under
`--profile load`, and `run.sh` chooses:

| `LOAD_K6` | behaviour |
|---|---|
| `auto` (default) | a host `k6` if one is installed, otherwise the container |
| `host` | refuse rather than silently containerise |
| `docker` | the pinned image even where a host k6 exists |

`LOAD_SRC_IPS` (default **32**, container only) is how many source addresses
the generator claims — 300 rps over 32 addresses is 9.4 r/s each against the
edge's 20, and 1,500 sockets is 47 each against its 100. `0` disables it and
reproduces the pre-`د‑8` ceiling. §5's last bullet has the measurement.

Both paths run the same scripts against the same edge, and both stamp the k6
version into the archived result. Two things differ and belong in any report
taken from the container: it reaches `nginx` **inside** the Compose network,
which is the same TLS termination one userland proxy hop earlier, and it
competes with the platform for the same CPU — as a host binary does too, and
a generator on separate hardware would not.

The container sees exactly `deploy/load/` and nothing else, so a `LOAD_OUT`
or `LOAD_TOKEN_FILE` outside that directory is refused rather than quietly
redirected. `LOAD_BASE_URL` defaults to `https://nginx` there; `localhost`
would be the generator's own loopback, and is refused for the same reason.

`run.sh` supplies what k6 cannot see from inside a script — commit SHA, image
digests, k6 version, host — and archives to
`deploy/load/results/<profile>-<UTC stamp>.json`. It passes k6's exit code
through unchanged: a missed budget is a **failed run**, not a note in a
report.

**`run.sh` never changes the platform it measures, and it refuses a platform
that does not match the files.** Both learned from the first §0.1-valid run
(2026‑09‑20), which measured nothing:

- `compose run` brings the generator's dependency chain "up", and "up"
  recreates any container whose config drifted from `docker-compose.yml` +
  `.env`. That run recreated all three app replicas five seconds after it
  began; the fresh processes never obtained Firebase's public keys, and every
  one of its 526,629 requests was refused as `common.internal` in ~2 ms. The
  generator now runs with `--no-deps`: whatever is running is what gets
  measured.
- Which is only safe if what is running is what the files say. `.env` is
  where `م‑8`'s kill switches live, and a switch reaches a container only
  when the container is recreated — so before the run, every running
  container's Compose config hash is compared with the hash the files
  produce now, and a mismatch is refused (`docker compose up -d --build`,
  then rerun). Refused rather than recorded: a dirty tree is a caveat on the
  claim, a stale container is a different platform.
- And before the first VU starts, `setup()` sends **six authenticated
  `GET /api/v1/me/context` probes** through the edge (six, because nginx
  round-robins across replicas) and aborts unless all six answer 200 — with
  the status, the error code and what it usually means (`401` → refresh the
  pool; `500 common.internal` → a replica that cannot verify tokens, read the
  app log for `firebase_auth.jwks_fetch_failed`; `502/503` → no healthy
  upstream). An aborted `setup()` still leaves a file: it says
  `platform_answered: false`, and `run.sh` says no load was generated.
- **Then every route the profile will drive, once each** — the real query
  parameters, the real payloads, including the whole index chain (register →
  PUT to the object store → complete → index → poll). That probe proves the
  platform is up; this one proves the *harness still speaks its API*, and on
  2026‑09‑26 it did not: `GET /conversations` had gained two required query
  parameters and answered 422 to 31% of the run's requests in 26 ms — the
  fastest "reads" in the profile, inside the 150 ms budget it then passed.
  A refusal here names the route, the status, the code and whose fault it
  is (`422` → the scenario drifted from the router; `409
  spaces.quota_exceeded` → that space is at its 1 GiB ceiling, so the probe
  samples five spaces across the pool before calling the corpus unindexable).
  It costs ~10 requests and leaves one conversation, one 9‑byte file and one
  indexed document behind; a preflight that only read could not prove the
  write path.
- **And while it runs, `run.sh` watches the generator from outside.** Every
  10 s it reads the k6 container's own cgroup (`cgroup_sample.sh`) into
  `results/<run>.generator.log`, and at the end folds the verdict into the
  archive as `generator` and `validity.generator_kept_up`: false if k6 hit
  its memory limit even once, or was CPU‑throttled in more than 1 % of
  periods after its first 30 s. It also records how much the *machine*
  swapped during the run (recorded, not gated). The 2026‑09‑26 peak run is
  why: k6 thrashed against its 2 GiB limit for minutes — 957 MiB swapped,
  5.97 M major faults — before the kernel killed it at 17m29s, and nothing
  in the output said its numbers had stopped being the platform's. The
  service now has no swap (a generator out of memory dies at once) and
  `GOMEMLIMIT` under the limit; a killed k6 exits 137, writes nothing, and
  `run.sh` says so.

**Where the generator's memory goes, measured.** An idle VU with this
harness's init context costs ~0.65 MiB on k6 1.3.0 (0.40 MiB live, the rest
GC headroom), and a peak run may allocate up to its `maxVUs`: 3,700 of them
needed ~2.4 GiB before a single connection was open. So `ws_hold.js` holds
**ten sockets per VU** (`LOAD_WS_SOCKETS_PER_VU`; `LOAD_WS_VUS` is still the
*socket* population, and the split always divides it exactly), the upload
document is built only in VUs that upload (it had been ~0.28 MiB of garbage
and string in every VU), and the ceiling is now 2,350 VUs — ~1.0 GiB idle
under the service's real limits, ~1.5 GiB at the peak of a 90 s smoke.

**Index polls back off.** A fixed 2 s poll was 13.5 % of everything the app
served on 2026‑09‑26 — 285 index VUs asking every 2 s once the workers fell
behind, i.e. the harness adding ~140 rps exactly when the platform could
least afford it. The interval is now a twentieth of the job's age, between
`LOAD_INDEX_POLL_S` (2 s) and `LOAD_INDEX_POLL_MAX_S` (10 s): a healthy
~50 s job is polled as before, and no e2e sample overshoots by more than ~5 %.

Useful overrides: `LOAD_BASE_URL` · `LOAD_DURATION_S` · `LOAD_WS_VUS` ·
`LOAD_WS_SOCKETS_PER_VU` · `LOAD_TOKEN_FILE` · `LOAD_AGENT_KEY` ·
`LOAD_P95_GENERATION_S` · `LOAD_RAG_QUERIES` · `LOAD_RAG_QUERY_ZIPF` ·
`LOAD_INDEX_POLL_MAX_S` · `LOAD_INDEX_TIMEOUT_S` · `LOAD_UPLOAD_ORIGIN` ·
`LOAD_VERBOSE=1` · and for `abuse` only: `LOAD_ABUSE_PHASE_S` · `LOAD_ABUSE_WARMUP_S` ·
`LOAD_NEIGHBOUR_RPS` · `LOAD_ABUSE_RPS` · `LOAD_ABUSER_INDEX` ·
`LOAD_NEIGHBOUR_TOLERANCE`.

**`LOAD_UPLOAD_ORIGIN`** is the one address the generator is *handed* rather
than configured with. `POST /files` answers with a URL presigned against
`MINIO_PUBLIC_ENDPOINT` — `localhost:19000`, what a browser on the host can
reach — and `localhost` inside the k6 container is the generator itself, so
every upload of the 2026‑09‑26 run died with `connection refused` and the
index scenario recorded nothing. SigV4 covers the **host header**, not the
address dialled, so the generator dials `http://minio:9000` (the Compose
default) and sends the signed host as `Host`. Measured, one presigned PUT:
as signed → `0`, rewritten with the `Host` header → `200`, rewritten without
it → `403 SignatureDoesNotMatch`. The platform's own setting is never
touched, and the archived result records what was dialled in
`run.upload_origin`.

**The edge is not optional.** Condition (2) — through TLS and the real nginx
edge, never `app:8000` — is the one condition the harness enforces itself:
a non-`https://` base URL aborts the run unless `LOAD_ALLOW_PLAINTEXT=1`,
which also marks the result invalid. The self-signed certificate is skipped
(`insecureSkipTLSVerify`), which asserts nothing about the certificate and is
not meant to; what is being measured is the cost of TLS termination.

---

## 5. What this deliberately does not cover

- **The SSE face of `POST /agents/{key}/invoke`.** k6's `http` module buffers
  a whole response, so it can time a stream's end and its first *byte* —
  neither of which is time to first *token*. The response headers of an SSE
  stream flush when the response opens, before the model has produced
  anything, so timing them would understate `ح‑1`, the single most important
  number in the plan. The streaming scenario uses `/api/v1/ws`, which gives
  one callback per frame and is also the path the product uses.
- **Media generation** — `worker-media` is blocked by a missing
  `MediaGenerator`, which the plan puts out of scope.
- **The load generator itself.** One k6 host driving 300 rps plus 1,500
  sockets can become the bottleneck before the platform does. Raise the file
  descriptor limit (`ulimit -n 65535`), read `generator` in the result (§4 —
  in a container run `run.sh` measures the generator's memory and CPU for
  you), and
  treat `dropped_iterations > 0` in the summary as invalidating the rate the
  report claims — k6 drops iterations rather than queueing them when it runs
  out of VUs. **Measured, not theoretical:** the first smoke run put 37,283
  iterations through the container in 20 s and began answering `connect:
  cannot assign requested address` — the generator out of *source ports* in
  its own namespace, ~28,000 of them held in `TIME_WAIT`. The service widens
  `ip_local_port_range` and shortens `tcp_fin_timeout` for that; a host k6
  needs the same two sysctls.

  ⚠️ **Corrected by capacity 3.2, and only half of that sentence was true.**
  `tcp_fin_timeout` does not shorten `TIME_WAIT` — measured, one client-closed
  socket watched out of `/proc/net/tcp`: 60.8 s at `fin_timeout=5` and 60.7 s
  at `fin_timeout=60`. It bounds `FIN_WAIT_2`; Linux fixes `TIME_WAIT` at 60 s
  in `TCP_TIMEWAIT_LEN` and no sysctl moves it. The widened *range* is what
  kept this generator alive, by itself. The setting is left in place because
  it is harmless, not because it helps.

  ⭐ **And this bullet described the platform too, exactly as the `nofile` one
  did.** The edge opens a new upstream connection per PROXIED REQUEST — it has
  no keepalive pool, because `keepalive` requires an `upstream` block and
  `app-locations.conf` refuses one to keep per-request DNS resolution — so it
  inherited the same ~28,000-port default: 15,782 in `TIME_WAIT` at 300 rps and
  4,090 `[crit] ... Cannot assign requested address` lines at 700 rps. Capacity
  3.2 gave the `nginx` service its own `ip_local_port_range` plus
  `tcp_tw_reuse: "1"` (the kernel default of 2 covers loopback only, which is
  why the RunPod publisher was never exposed and this bridge always was).

- **The reach of a single source IP — was the binding limit, and is no longer
  (`د‑8`).** The edge rate-limits on `$binary_remote_addr`, and at the time of this
  measurement that was `limit_req zone=api_req rate=20r/s burst=40 nodelay`
  plus `limit_conn ws_conn 100` (capacity 3.1 has since moved all three to
  200r/s, `burst=400` and 500 — see the last bullet). A
  generator is one address, so the first smoke run offered **300.1 rps** —
  §0's target, exactly — and the edge admitted **22.0**, returning 429 to
  92.7% of them. Nothing was broken: `limit_req` is P1-7's deliberate pre-auth
  line and it was doing its job. But the number measured the limiter, not the
  platform.

  The container now claims `LOAD_SRC_IPS` secondary addresses (default 32) off
  the top of its own subnet and passes k6 `--local-ips`, so §0's "hundreds of
  users" arrive from hundreds of users' worth of addresses. **Nothing in
  `deploy/nginx/` changed** — which is what `م‑8` demands before a baseline
  exists, and is the whole reason this option was taken over loosening the
  limit. Re-measured on the same 20 s peak mix: **7,539 requests offered at
  300 rps, 0 rejected** (against 5,561 of 6,001 before). `deploy/load/entrypoint.sh`
  carries the arithmetic and the full measurement table.

  ⚠️ **This raises nothing about the platform's real ceiling.** One NATed
  office got 20 r/s from this edge — capacity 3.1 made it 200, and settled the
  question this paragraph left open by answering it rather than widening it:
  per-IP metering was doing two jobs because nothing else did the second, and
  step `1.2` built the second (two Redis buckets, post-auth, per user). So the
  edge limiter went back to one job — a flood shield ahead of authentication,
  not a fairness accountant. `limit_conn ws_conn` moved 100 → 500 for the same
  reason: §0's office behind one NAT is one address holding 1,500 sockets.

  ⚠️ **And 3.1 did not retire this mechanism.** Spreading VUs across addresses
  is what makes the generator resemble the thing it claims to simulate; it was
  never a trick played on a limit that has since grown. A `peak` run still
  wants `LOAD_SRC_IPS`.
  And a **host** k6 still runs from one address: `run.sh` says so out loud
  before a `peak` run rather than let the ceiling be rediscovered.

---

## 6. Reading the result

The archived JSON leads with the things that decide whether it counts:

```json
{ "profile": "peak", "valid": true,
  "validity": { "real_tokens": true, "tls_edge": true, "realistic_seed": true,
                "platform_answered": true, "rate_delivered": true,
                "generator_kept_up": true },
  "generator": { "memory_peak_bytes": 1575235584, "memory_limit_hits": 0,
                 "cpu_throttled_pct": 0.4, "…": 0 },
  "run": { "commit": "…", "dirty": false, "images": { "app": "sha256:…" } },
  "seed": { "messages": 1000000, "…": 0 } }
```

then `thresholds` (each budget, pass or fail), `latency` (p50/p95/p99 per
metric and per scenario), `counters`, and the raw k6 metrics underneath.

`run.images` names the image each Compose service's containers were created
from — what was running, not what the files ask for — and holds a list where
replicas disagree. Files archived before 2026‑09‑28 hold one entry named `?`
instead and say nothing about which images ran (`د‑36`); for those, `commit`
and `dirty` are the reference.

`validity` carries §0.1's three conditions and three more. The three say the
run was *set up* as a baseline; `generator_kept_up` says the thing doing the
measuring was not itself the bottleneck (§4); `platform_answered` says there
was a platform to measure — it is false when more of what the generator sent was
refused than answered (`aizzak_failed_requests` rate ≥ 0.5, 429s excluded as
in §7 item 4), or when nothing was sent at all. The 2026‑09‑20 peak run met
all three conditions, failed 100 % of its requests, and said `valid: true`
until this field existed; its percentiles describe the error path.
`rate_delivered` says the profile's arrival rate actually arrived: it is false
when k6 recorded any `dropped_iterations` — it ran out of VUs because the
responses were slow enough to hold them all — and the rate in `targets` was
then never offered. The 2026‑09‑26 peak run dropped 264,140 arrivals (about
half), every endpoint's p50 sat near 7 s, and the file said `valid: true`.

Rate metrics in `counters` are objects, not numbers: `{ "rate": 1, "count":
526629, "total": 526629 }`, where `count` is the samples in which the metric
was true — for `http_req_failed`, the failures. The old rendering printed the
rate alone, and `http_req_failed: 1` read as one failed request when it meant
all of them.

Four fields are worth reading before the percentiles:

- `counters.aizzak_rate_limited_total` — intended 429s, which §7 item 4
  excludes from the error budget. **This paragraph used to say the value
  should be zero, and that was wrong** — written from `ح‑7` ("`api_rate_per_min`
  is defined and never read") on the assumption that the app was the only
  thing that could 429. The first run measured 5,561 of 6,001 requests
  rejected, all of them by nginx's `limit_req`, which has been at the edge
  all along. Read it against §5's last bullet: a large value is the per-IP
  limiter, which since `د‑8` should only appear when `LOAD_SRC_IPS=0` or a
  host k6 produced the run. A non-zero value in a container run with
  addresses claimed means the block was too small for the offered rate —
  divide the rate by 20 r/s for the minimum.
- `counters.aizzak_index_timeouts` — index jobs still neither `indexed` nor
  `failed` after `LOAD_INDEX_TIMEOUT_S` (300 s), out of the jobs that reached
  a verdict; per step as `index_timeouts`. **Not in the error rate**, since
  2026‑09‑28: §7 item 4's budget is on the synchronous paths, every request
  such a job made was answered, and folded in they decided that day's step
  run on their own — ~all of steps 125–175's errors, against one failed check
  in 197,584. A document the worker *failed* is still an error: that is a
  wrong answer, not a missing one. A job still running when its step ends
  reaches no verdict, so read this beside `latency.index_e2e`.
- `assumptions.p95_generation_s` — the stream arrival rate is derived from it
  through §3's provider equation, and nothing has measured it yet. Step 0.5
  replaces the assumption; until then it is stated in every result rather
  than buried in a default.
- `assumptions.rag_queries` — the RAG question stream's size and popularity
  exponent (§1). The share of RAG requests step 4.3's cache can answer
  follows from them, so compare a RAG p95 only with one taken at the same
  values, or with the cache off (`EMBEDDING_CACHE_TTL_S=0`). The share it
  did answer is `aizzak_embedding_cache_total` over the run (§1).

Requirements: **k6 1.x**, either installed or via `--profile load` (pinned at
`grafana/k6:1.3.0`); Python 3 for `run.sh`'s two JSON helpers; and a running
stack. The WebSocket scenarios import `k6/experimental/websockets` — *not*
`k6/net/websockets`, which this file claimed for months and which no released
k6 provides; the graduated name does not exist yet. `k6/timers` is graduated
and imported as such.
