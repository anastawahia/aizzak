// One options builder, two profiles. `peak.js` and `average.js` differ by a
// single scale factor and a duration; everything else -- the scenario mix, the
// budgets, the guards -- is shared, so the two cannot drift into testing
// different systems.

import http from 'k6/http';
import {
  AGENT_KEY,
  API,
  BASE_URL,
  BROWSE_RPS_PEAK,
  BUDGET_MS,
  INDEX_STARTS_PER_S,
  PEAK_FACTOR,
  STREAM_STARTS_PER_S,
  TARGET,
  TLS_GLOBAL_OPTIONS,
  assertRunnable,
  uploadTarget,
  wsSocketsPerVu,
} from './config.js';
import { anyToken, assertTokensCoverRun, authHeaders, poolSize } from './auth.js';

// §0.1's acceptance criterion asks for p50/p95/p99; k6's default trend stats
// carry neither p50 by that name nor p99 at all.
const TREND_STATS = ['min', 'med', 'p(50)', 'p(90)', 'p(95)', 'p(99)', 'max', 'avg', 'count'];

// The five-part mix every profile runs. `peak`/`average` run ONE set for the
// whole duration; `step` runs one set per step, each starting where the last
// ended and carrying its own `step` tag -- so all three profiles offer the
// same system the same mix, and differ only in how much of it and when.
function scenarioSet({ scale, durationS, wsVus, startS = 0, tags = {}, suffix = '' }) {
  const s = (n) => n * scale;
  const duration = `${durationS}s`;
  const socketsPerVu = wsSocketsPerVu(wsVus);
  const at = (scenario) => ({
    ...scenario,
    ...(startS ? { startTime: `${startS}s` } : {}),
    tags: { ...(scenario.tags || {}), ...tags },
  });

  return {
    [`browse${suffix}`]: at(arrival('browse', s(BROWSE_RPS_PEAK), duration, 200, 800)),
    [`rag${suffix}`]: at(arrival('rag', s(TARGET.ragQpsPeak), duration, 60, 400)),
    [`stream${suffix}`]: at(arrival('stream', s(STREAM_STARTS_PER_S), duration, 80, 400)),
    [`index${suffix}`]: at(arrival('indexFile', s(INDEX_STARTS_PER_S), duration, 150, 600)),
    [`ws${suffix}`]: at({
      // A POPULATION, not a rate -- see `scenarios/ws_hold.js`. `wsVus` is
      // the number of SOCKETS; each VU holds `socketsPerVu` of them
      // (`lib/config.js` has the memory measurement that made that
      // necessary), and learns how many from its scenario's env.
      executor: 'constant-vus',
      exec: 'wsHold',
      vus: wsVus / socketsPerVu,
      duration,
      env: { LOAD_WS_SOCKETS_THIS_VU: String(socketsPerVu) },
      tags: { profile_part: 'ws' },
    }),
  };
}

export function buildOptions({ scale, durationS, wsVus }) {
  return {
    ...TLS_GLOBAL_OPTIONS,
    summaryTrendStats: TREND_STATS,
    // Every scenario starts at once: §0's targets are simultaneous, and a
    // staggered start would measure five systems in sequence instead of one
    // under all five loads.
    scenarios: scenarioSet({ scale, durationS, wsVus }),
    thresholds: {
      // ── 07 §2's budgets, unrelaxed. These FAIL the run. ────────────────
      'http_req_duration{op:read}': [`p(95)<${BUDGET_MS.read}`],
      'http_req_duration{op:write}': [`p(95)<${BUDGET_MS.write}`],
      aizzak_rag_retrieval_ms: [`p(95)<${BUDGET_MS.ragRetrieval}`],
      aizzak_ttft_ms: [`p(95)<${BUDGET_MS.ttft}`],
      // §7 item 4. An intended 429 is not in this rate by construction
      // (`lib/metrics.js`), so this is the honest error budget and not a
      // proxy for one.
      aizzak_failed_requests: ['rate<0.001'],

      // ── Reporting-only submetrics ──────────────────────────────────────
      // k6 aggregates trends GLOBALLY and materialises a per-tag submetric
      // only where a threshold names one. §0.1 asks for p50/p95/p99 PER
      // SCENARIO, so each scenario gets a bound that is always true and
      // exists purely to make its slice appear in the summary. They are
      // marked here rather than left looking like forgotten limits.
      'http_req_duration{scenario:browse}': ['p(99)>=0'],
      'http_req_duration{scenario:rag}': ['p(99)>=0'],
      'http_req_duration{scenario:index}': ['p(99)>=0'],
      'http_req_duration{op:poll}': ['p(99)>=0'],
      aizzak_index_e2e_ms: ['p(99)>=0'],
      aizzak_ws_hold_seconds: ['p(99)>=0'],
    },
  };
}

// The guards that must run once, before any load. k6 aborts the test when
// `setup` throws, which is the behaviour every one of these wants: each
// describes a condition under which the run would produce a number that looks
// like a measurement and is not one.
export function guard({ durationS, wsVus }) {
  assertRunnable();
  assertTokensCoverRun(durationS);

  // §0 derives 1,500 sockets as "500 users × up to 3 tabs", against a
  // `ws_connections_per_user` ceiling of 5. A pool of 200 workspace tokens
  // would put 7.5 sockets on each user and the platform would correctly
  // refuse a third of them -- a limiter working exactly as designed, showing
  // up in the report as a WebSocket failure rate. The harness has to be able
  // to tell those apart, and the only way is to not provoke it.
  const perUser = wsVus / poolSize();
  if (perUser > 3) {
    throw new Error(
      `${wsVus} WS sockets across ${poolSize()} tokens is ${perUser.toFixed(1)} per user; ` +
        'ws_connections_per_user is 5 and §0 assumes 3. Mint more tokens (README §2) -- ' +
        'otherwise the refusals this produces are the limiter, not a capacity finding.',
    );
  }

  assertPlatformAnswers();
  assertEveryRouteAnswers();
  return { started_at: new Date().toISOString(), ws_sockets_per_user: round2(perUser) };
}

// The platform answers ONE authenticated request before it is asked to answer
// half a million. The 2026-09-20 peak run passed every guard above -- real
// tokens, TLS edge, a floor-sized seed -- and then every one of its 526,629
// requests failed in ~2ms: `run.sh` had recreated the app replicas as the
// run began, the fresh processes never obtained Firebase's public keys, and a
// verifier without keys refuses everything as `common.internal`. Thirty
// minutes of load measured the error path, and the archived file said
// `valid: true`. A probe here costs one round trip and turns that into a
// refusal before the first VU starts.
//
// Six probes, not one: the edge round-robins across replicas, and one warm
// replica proves nothing about the other two. `/me/context` because it is
// the request every scenario's first request depends on -- token verified,
// principal resolved, tenant found -- and nothing else.
const PREFLIGHT_PROBES = 6;

function assertPlatformAnswers() {
  const tok = anyToken();
  for (let i = 0; i < PREFLIGHT_PROBES; i++) {
    const res = http.get(`${BASE_URL}/api/v1/me/context`, {
      headers: authHeaders(tok),
      tags: { op: 'preflight', route: 'preflight' },
    });
    if (res.status !== 200) {
      throw new Error(
        `preflight: ${describeRefusal(res, 'GET /api/v1/me/context')} ` +
          `(probe ${i + 1} of ${PREFLIGHT_PROBES})`,
      );
    }
  }
}

// ...and then EVERY route the profile is about to drive, once each.
//
// The probe above proves there is a platform. It does not prove the harness
// still speaks its API, and on 2026-09-26 it did not: `GET /conversations`
// had gained two required query parameters (`agent_key`, and `space_id` at
// the spaces plan's step 12), the scenario still sent `?limit=20`, and 9,993
// of the run's first 32,376 edge requests -- 31% -- were 422s answered in
// 26ms without reaching the database. They are the fastest "reads" a profile
// can produce, they sit inside `http_req_duration{op:read}`, and the 150ms
// budget passes because of them. The same run lost its whole index scenario
// to an upload address the generator could not dial.
//
// Neither is a platform fault and neither is visible in a percentile, so both
// are refused here instead: one request per route, the real parameters, the
// real payloads. It costs ~10 requests and about a second.
//
// It is NOT free of side effects, and that is deliberate: a preflight that
// only read could not prove the write path. One conversation, one 9-byte
// file and one indexed document per run -- against a seed of 100,000 files
// and a run that creates ~45,000 rows of its own, that is noise, and a probe
// that lies about the path it checked would not be.
//
// The WebSocket scenarios are NOT probed here: `k6/experimental/websockets`
// is event-driven and `setup()` has nothing to await it with. A dead socket
// path shows up in the first seconds of the run as a rejection rate, which is
// the case this file cannot improve on.
function assertEveryRouteAnswers() {
  const tok = anyToken();
  const atTok = (t, route) => ({ headers: authHeaders(t), tags: { op: 'preflight', route } });
  const at = (route) => atTok(tok, route);

  probe(
    'GET /api/v1/conversations',
    http.get(
      `${API}/conversations?agent_key=${AGENT_KEY}&space_id=${tok.spaceId}&limit=20`,
      at('conversations'),
    ),
    [200],
  );
  probe('GET /api/v1/spaces', http.get(`${API}/spaces?limit=20`, at('spaces')), [200]);
  probe(
    'GET /api/v1/files',
    http.get(`${API}/files?space_id=${tok.spaceId}&limit=20`, at('files')),
    [200],
  );
  probe(
    'POST /api/v1/me/heartbeat',
    http.post(`${API}/me/heartbeat`, null, at('heartbeat')),
    [200, 204],
  );
  probe(
    'POST /api/v1/conversations',
    http.post(
      `${API}/conversations`,
      JSON.stringify({ space_id: tok.spaceId, agent_key: AGENT_KEY, title: 'load preflight' }),
      at('create_conversation'),
    ),
    [201],
  );
  probe(
    'POST /api/v1/knowledge/search',
    http.post(
      `${API}/knowledge/search`,
      JSON.stringify({ query: 'preflight', space_id: tok.spaceId, k: 1 }),
      at('knowledge_search'),
    ),
    [200],
  );

  // The index chain, in the order the scenario runs it, up to the worker.
  //
  // Its first call is the one probe that can be refused for a reason that is
  // about the TOKEN rather than about the platform: a space at its 1 GiB
  // ceiling answers `409 spaces.quota_exceeded`. On the dev-2026-09-17 corpus
  // 13 of the pool's 500 spaces are over it -- the seeder writes rows
  // directly and the ceiling is only enforced on registration -- and they are
  // pool entries 0 to 12, because the token file is ordered by space size.
  // So the probe SPREADS its attempts across the pool rather than walking its
  // head, and gives up only when every sample is full, which is a corpus in
  // which the index scenario cannot register anything.
  const body = 'preflight';
  const stride = Math.max(1, Math.floor(poolSize() / QUOTA_PROBES));
  let reg = null;
  let regTok = tok;
  for (let i = 0; i < QUOTA_PROBES; i++) {
    regTok = anyToken(i * stride);
    reg = http.post(
      `${API}/files`,
      JSON.stringify({
        space_id: regTok.spaceId,
        name: `preflight-${Date.now()}.txt`,
        content_type: 'text/plain',
        size_bytes: body.length,
      }),
      atTok(regTok, 'register_file'),
    );
    if (reg.status === 201 || !isSpaceFull(reg)) break;
  }
  probe(
    'POST /api/v1/files',
    reg,
    [201],
    isSpaceFull(reg)
      ? `all ${QUOTA_PROBES} spaces sampled across the pool are at their byte ceiling, so the ` +
        'index scenario would register nothing for the whole run. Purge the corpus ' +
        '(`python -m app.ops.purge --help`) or reseed it below the quota the API enforces.'
      : undefined,
  );
  const fileId = reg.json('file_id');
  const target = uploadTarget(reg.json('upload_url'));
  probe(
    `PUT ${target.url.split('?')[0]}`,
    http.put(target.url, body, {
      headers: { 'Content-Type': 'text/plain', ...target.headers },
      tags: { op: 'preflight', route: 'minio_put' },
    }),
    [200],
    'The presigned URL the platform issued names an address this generator cannot reach, ' +
      'or it reached one that rejects the signature. `LOAD_UPLOAD_ORIGIN` is what the ' +
      "generator dials and the URL's own host is what it sends as `Host` -- see " +
      '`lib/config.js`. Every upload of the run would have failed here.',
  );
  probe(
    'POST /api/v1/files/{id}/complete',
    http.post(
      `${API}/files/${fileId}/complete`,
      JSON.stringify({ checksum: null }),
      atTok(regTok, 'complete_file'),
    ),
    [200],
  );
  const idx = probe(
    'POST /api/v1/knowledge/documents',
    http.post(`${API}/knowledge/documents`, JSON.stringify({ file_id: fileId }), {
      headers: authHeaders(regTok, { 'Idempotency-Key': `preflight-${fileId}` }),
      tags: { op: 'preflight', route: 'index_file' },
    }),
    [202],
  );
  probe(
    'GET /api/v1/knowledge/documents/{id}',
    http.get(`${API}/knowledge/documents/${idx.json('id')}`, atTok(regTok, 'get_document')),
    [200],
  );
}

// How many of the pool's spaces the registration probe will try before it
// calls a corpus unindexable.
const QUOTA_PROBES = 5;

function isSpaceFull(res) {
  if (!res || res.status !== 409) return false;
  try {
    return JSON.parse(res.body).code === 'spaces.quota_exceeded';
  } catch {
    return false;
  }
}

function probe(what, res, want, hint) {
  if (!want.includes(res.status)) {
    throw new Error(
      `preflight: ${describeRefusal(res, what, want)}${hint ? `\n  ${hint}` : ''}`,
    );
  }
  return res;
}

function describeRefusal(res, what, want) {
  let code = '';
  try {
    code = JSON.parse(res.body).code || '';
  } catch {
    code = '';
  }
  const expected = want && want.length ? ` (wanted ${want.join('/')})` : '';
  const where = `${what} answered ${res.status}${code ? ` ${code}` : ''}${expected}`;
  if (res.status === 0) {
    return `${where} -- nothing answered (${res.error || 'connection failed'}).`;
  }
  if (res.status === 401) {
    return (
      `${where} -- the pool's token is refused: expired, or minted for another Firebase project. ` +
      '`python -m app.ops.mint_load_tokens refresh`, then `verify` (README §2).'
    );
  }
  if (res.status === 403) {
    return `${where} -- authenticated but not permitted; the pool's user lacks this route's right.`;
  }
  if (res.status === 404 || res.status === 405) {
    return `${where} -- the route moved or changed method. The harness and the API have drifted.`;
  }
  if (res.status === 422) {
    // The harness's fault, not the platform's, and the body names the field.
    return (
      `${where} -- the request is not the shape this API accepts (a required query parameter ` +
      `or body field). The scenario and the router have drifted; fix the scenario. ` +
      `Body: ${String(res.body).slice(0, 300)}`
    );
  }
  if (res.status === 500) {
    return (
      `${where} -- the platform cannot serve this route at all. If it is every route, typically ` +
      'a replica with no Firebase public keys: a fresh container that cannot reach ' +
      'www.googleapis.com. Read the app log for `firebase_auth.jwks_fetch_failed`, fix the route, ' +
      'and probe again -- the load would only have measured this refusal.'
    );
  }
  if (res.status === 502 || res.status === 503 || res.status === 504) {
    return `${where} -- the edge has no healthy upstream (\`docker compose ps\`).`;
  }
  return `${where} -- not a platform that can be measured; fix it, then rerun.`;
}

// ⚠️ k6 takes an INTEGER `rate` over a `timeUnit`, and a fractional one is not
// rounded -- it is REFUSED at init, before any load, with a message about
// unmarshalling a number into an int64 that names neither the scenario nor the
// field. MEASURED the first time the harness was ever executed (capacity
// blocker د‑3), and it made BOTH profiles unrunnable: §0's own arithmetic is
// fractional per second (100 index jobs a MINUTE is 1.67/s, and browse takes
// the remainder of 300 rps), and the average profile then divides all of it by
// six.
//
// So the rate is expressed in the finest unit that makes it a whole number:
// 1.67/s becomes exactly `100` per `1m`, which is also how §0 states it. When
// nothing divides evenly the per-hour form is used, where rounding costs at
// most half an arrival an hour -- far below the noise of any run, and visible
// in the archived options rather than silently applied.
function toIntegerRate(perSecond) {
  for (const [timeUnit, factor] of [
    ['1s', 1],
    ['1m', 60],
    ['1h', 3600],
  ]) {
    const scaled = perSecond * factor;
    const rounded = Math.round(scaled);
    if (rounded >= 1 && Math.abs(scaled - rounded) <= scaled * 1e-9) {
      return { rate: rounded, timeUnit };
    }
  }
  return { rate: Math.max(1, Math.round(perSecond * 3600)), timeUnit: '1h' };
}

function arrival(exec, perSecond, duration, preAllocatedVUs, maxVUs) {
  const { rate, timeUnit } = toIntegerRate(perSecond);
  return {
    executor: 'constant-arrival-rate',
    exec,
    rate,
    timeUnit,
    duration,
    // k6 warns and DROPS iterations when it runs out of VUs, which silently
    // turns a 300 rps profile into whatever the pool could sustain. `maxVUs`
    // is set well above the arithmetic need for that reason; the run summary
    // carries `dropped_iterations`, and a non-zero value there invalidates
    // the rate the report claims.
    preAllocatedVUs,
    maxVUs,
  };
}

// ── The STEP profile (0.5, decided 2026-09-26) ────────────────────────────
// The 2026-09-26 peak run offered 300 rps to a platform that serves ~160 and
// archived thirty minutes of queueing: every percentile in it was the length
// of a queue, not the cost of a request. A constant rate above the ceiling
// cannot measure the ceiling. This profile offers the same mix at rising
// rates, one step after another, and reports each step on its own -- the
// answer is the highest step that was still DELIVERED (no dropped arrivals)
// and still inside 07 §2's budgets, which is the number later waves move.
//
// Each step is a full `scenarioSet` at `rps / apiRpsPeak` of peak, sockets
// included, starting when the previous one's duration ends. The previous
// step's in-flight iterations drain into the next for up to k6's 30s
// graceful stop; that tail is in the NEXT step's clock but the PREVIOUS
// step's tag, so each step's numbers are its own requests.
export function stepPlan(stepsRps, holdS) {
  return stepsRps.map((rps, i) => ({
    label: `rps${String(rps).padStart(3, '0')}`,
    rps,
    scale: rps / TARGET.apiRpsPeak,
    startS: i * holdS,
    holdS,
  }));
}

export function stepWsVus(step, wsPeak) {
  return Math.max(1, Math.round(wsPeak * step.scale));
}

// Per-step submetrics. k6 materialises a tagged slice only where a threshold
// names it, so each step gets always-true bounds purely to appear in the
// summary; the budgets are then judged PER STEP in `lib/summary.js`, not
// here -- a step above the ceiling is expected to miss them, and that miss
// is the finding, not a failed run.
function stepThresholds(plan) {
  const out = {};
  for (const { label } of plan) {
    const t = `step:${label}`;
    out[`http_req_duration{op:read,${t}}`] = ['p(99)>=0'];
    out[`http_req_duration{op:write,${t}}`] = ['p(99)>=0'];
    out[`aizzak_rag_retrieval_ms{${t}}`] = ['p(99)>=0'];
    out[`aizzak_ttft_ms{${t}}`] = ['p(99)>=0'];
    out[`aizzak_index_e2e_ms{${t}}`] = ['p(99)>=0'];
    out[`aizzak_failed_requests{${t}}`] = ['rate>=0'];
    out[`http_reqs{${t}}`] = ['count>=0'];
    out[`iterations{${t}}`] = ['count>=0'];
    out[`dropped_iterations{${t}}`] = ['count>=0'];
  }
  return out;
}

export function buildStepOptions({ plan, wsPeak }) {
  let scenarios = {};
  for (const step of plan) {
    scenarios = {
      ...scenarios,
      ...scenarioSet({
        scale: step.scale,
        durationS: step.holdS,
        wsVus: stepWsVus(step, wsPeak),
        startS: step.startS,
        tags: { step: step.label },
        suffix: `_${step.label}`,
      }),
    };
  }
  return {
    ...TLS_GLOBAL_OPTIONS,
    summaryTrendStats: TREND_STATS,
    scenarios,
    thresholds: stepThresholds(plan),
  };
}

export function scaleFor(profile) {
  return profile === 'peak' ? 1 : 1 / PEAK_FACTOR;
}

function round2(n) {
  return Math.round(n * 100) / 100;
}
