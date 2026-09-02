// One options builder, two profiles. `peak.js` and `average.js` differ by a
// single scale factor and a duration; everything else -- the scenario mix, the
// budgets, the guards -- is shared, so the two cannot drift into testing
// different systems.

import {
  BROWSE_RPS_PEAK,
  BUDGET_MS,
  INDEX_STARTS_PER_S,
  PEAK_FACTOR,
  STREAM_STARTS_PER_S,
  TARGET,
  TLS_GLOBAL_OPTIONS,
  assertRunnable,
} from './config.js';
import { assertTokensCoverRun, poolSize } from './auth.js';

// §0.1's acceptance criterion asks for p50/p95/p99; k6's default trend stats
// carry neither p50 by that name nor p99 at all.
const TREND_STATS = ['min', 'med', 'p(50)', 'p(90)', 'p(95)', 'p(99)', 'max', 'avg', 'count'];

export function buildOptions({ scale, durationS, wsVus }) {
  const s = (n) => round2(n * scale);
  const duration = `${durationS}s`;

  return {
    ...TLS_GLOBAL_OPTIONS,
    summaryTrendStats: TREND_STATS,
    // Every scenario starts at once: §0's targets are simultaneous, and a
    // staggered start would measure five systems in sequence instead of one
    // under all five loads.
    scenarios: {
      browse: arrival('browse', s(BROWSE_RPS_PEAK), duration, 200, 800),
      rag: arrival('rag', s(TARGET.ragQpsPeak), duration, 60, 400),
      stream: arrival('stream', s(STREAM_STARTS_PER_S), duration, 80, 400),
      index: arrival('indexFile', s(INDEX_STARTS_PER_S), duration, 150, 600),
      ws: {
        // A POPULATION, not a rate -- see `scenarios/ws_hold.js`.
        executor: 'constant-vus',
        exec: 'wsHold',
        vus: wsVus,
        duration,
        tags: { profile_part: 'ws' },
      },
    },
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
      `${wsVus} WS VUs across ${poolSize()} tokens is ${perUser.toFixed(1)} sockets per user; ` +
        'ws_connections_per_user is 5 and §0 assumes 3. Mint more tokens (README §2) -- ' +
        'otherwise the refusals this produces are the limiter, not a capacity finding.',
    );
  }
  return { started_at: new Date().toISOString(), ws_sockets_per_user: round2(perUser) };
}

function arrival(exec, rate, duration, preAllocatedVUs, maxVUs) {
  return {
    executor: 'constant-arrival-rate',
    exec,
    rate,
    timeUnit: '1s',
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

export function scaleFor(profile) {
  return profile === 'peak' ? 1 : 1 / PEAK_FACTOR;
}

function round2(n) {
  return Math.round(n * 100) / 100;
}
