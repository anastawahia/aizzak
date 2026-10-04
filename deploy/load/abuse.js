// The ABUSE profile -- the half of 1.2's acceptance criterion that only a
// load run can answer: "مستأجرٌ مسيءٌ في اختبار k6 لا يرفع p95 لجاره".
//
//   deploy/load/run.sh abuse
//
// After LOAD_ABUSE_WARMUP_S of neighbour traffic that is judged by nothing
// (default 60), three phases of LOAD_ABUSE_PHASE_S each (default 300): the neighbours alone,
// the neighbours with one abusive tenant, the neighbours alone again
// (`lib/profile.js` says why two references and not one). The neighbours are
// every pool entry but one, at LOAD_NEIGHBOUR_RPS (default §0's average, 50
// rps); the abuser is the remaining entry (LOAD_ABUSER_INDEX, default the
// last), at LOAD_ABUSE_RPS. Both run `browse`'s mix.
//
// The abuser's default rate is not chosen, it is 1.2's own figure: "a tenant
// with fifty users reaches 6,000 requests a minute" -- 100 rps, fifty times
// the 120 a minute one user is allowed. `INV-W1` gives every user one
// workspace and no membership route exists, so on this platform that volume
// can only come from ONE user, and the ceiling that holds it is the user
// bucket; the tenant bucket (2,400/min) is above it and cannot bind first.
//
// The verdict is a comparison between phases, which a k6 threshold cannot
// express, so it is computed here into the archive's `isolation` block, and
// `run.sh` fails the run (exit 99, k6's own code for a crossed threshold)
// when `isolation.held` is false.

import { ABUSE_PHASES, REFUSAL_SCOPES, buildAbuseOptions, guard } from './lib/profile.js';
import { buildSummary } from './lib/summary.js';
import { abuserIndex, poolSize } from './lib/auth.js';
import { BUDGET_MS, TARGET } from './lib/config.js';

// The API's per-user request ceiling, copied rather than imported (this is
// k6, not Python); `tests/unit/test_load_abuse.py` pins it to
// `Limits.api_rate_per_min`.
const USER_RATE_PER_MIN = 120;
// 1.2's example tenant: fifty users, each at that ceiling.
const EXAMPLE_TENANT_USERS = 50;

const PHASE_S = Number(__ENV.LOAD_ABUSE_PHASE_S || 300);
// Neighbour traffic before the first phase, judged by nothing
// (`lib/profile.js`). A minute at 50 rps is ~6 requests from every neighbour:
// each one's principal cached, the pages their queries touch in memory.
// Empty is unset, not zero: Compose hands the container every knob it lists,
// set or not, and `Number('')` would switch the warm-up off unasked.
const WARMUP_S = (__ENV.LOAD_ABUSE_WARMUP_S || '') === '' ? 60 : Number(__ENV.LOAD_ABUSE_WARMUP_S);
const NEIGHBOUR_RPS = Number(__ENV.LOAD_NEIGHBOUR_RPS || TARGET.apiRpsAverage);
const ABUSE_RPS = Number(
  __ENV.LOAD_ABUSE_RPS || (EXAMPLE_TENANT_USERS * USER_RATE_PER_MIN) / 60,
);
// How much higher the neighbours' p95 may sit under abuse than in the worse of
// the two quiet phases before the run says the abuser reached them.
const TOLERANCE = Number(__ENV.LOAD_NEIGHBOUR_TOLERANCE || 0.1);

export const options = buildAbuseOptions({
  warmupS: WARMUP_S,
  phaseS: PHASE_S,
  neighbourRps: NEIGHBOUR_RPS,
  abuseRps: ABUSE_RPS,
});

export function setup() {
  assertShape();
  return {
    ...guard({ durationS: WARMUP_S + PHASE_S * ABUSE_PHASES.length, wsVus: 0 }),
    abuser_index: abuserIndex(),
  };
}

export { neighbour, abuser } from './scenarios/abuse.js';

// Two ways this profile can measure something other than isolation, refused
// before any load. Neighbours near their own ceiling would be refused for
// their own rate, and those 429s would read as the abuser reaching them --
// half the ceiling, as `backlog.js` does, because the limiter's window and the
// arrivals' are not aligned. And an "abuser" inside its ceiling abuses
// nothing: the run would compare three quiet phases and call it a verdict.
function assertShape() {
  const neighbours = poolSize() - 1;
  if (neighbours < 1) {
    throw new Error('the abuse profile needs at least two pool entries: one abuser, one neighbour.');
  }
  const perNeighbour = (NEIGHBOUR_RPS * 60) / neighbours;
  if (perNeighbour > USER_RATE_PER_MIN / 2) {
    throw new Error(
      `${NEIGHBOUR_RPS} rps over ${neighbours} neighbours is ${perNeighbour.toFixed(1)} requests ` +
        `a minute each, against a ceiling of ${USER_RATE_PER_MIN}: their own refusals would read ` +
        'as the abuser reaching them. Mint more tokens (README §2) or lower LOAD_NEIGHBOUR_RPS.',
    );
  }
  if (ABUSE_RPS * 60 <= USER_RATE_PER_MIN) {
    throw new Error(
      `LOAD_ABUSE_RPS=${ABUSE_RPS} is ${ABUSE_RPS * 60} a minute, inside the ${USER_RATE_PER_MIN} ` +
        'ceiling: that tenant abuses nothing, and the run would compare three quiet phases.',
    );
  }
}

export function handleSummary(data) {
  const out = __ENV.LOAD_OUT || 'deploy/load/results/abuse-latest.json';
  const summary = buildSummary('abuse', data);
  summary.isolation = isolation(data);
  return {
    [out]: JSON.stringify(summary, null, 2),
    stdout: textSummary(summary.isolation),
  };
}

function isolation(data) {
  const m = data.metrics || {};
  const val = (name) => (m[name] || {}).values || {};
  const trend = (name) => {
    const v = val(name);
    return v.count
      ? { count: v.count, p50: v['p(50)'] ?? v.med, p95: v['p(95)'], p99: v['p(99)'] }
      : null;
  };
  const rate = (name) => {
    const v = val(name);
    const samples = (v.passes || 0) + (v.fails || 0);
    return { count: v.passes || 0, samples, rate: samples > 0 ? v.rate : null };
  };

  const phases = {};
  for (const phase of ABUSE_PHASES) {
    const t = `tenant:neighbour,phase:${phase}`;
    const failed = rate(`aizzak_failed_requests{${t}}`);
    const iterations = val(`iterations{phase:${phase}}`).count || 0;
    const dropped = val(`dropped_iterations{phase:${phase}}`).count || 0;
    phases[phase] = {
      requests: val(`http_reqs{${t}}`).count || 0,
      // Every iteration of the phase, the abuser's included in `abuse`.
      iterations,
      dropped_iterations: dropped,
      delivered: iterations > 0 && dropped === 0,
      refused: val(`aizzak_rate_limited_total{${t}}`).count || 0,
      // No samples is "nothing answered", never "no errors" (`lib/summary.js`).
      error_rate: failed.samples > 0 ? failed.rate : 1,
      read: trend(`http_req_duration{${t},op:read}`),
      write: trend(`http_req_duration{${t},op:write}`),
      all: trend(`http_req_duration{${t}}`),
    };
  }

  const comparison = {};
  for (const op of ['read', 'write']) {
    const [before, abuse, after] = ABUSE_PHASES.map((p) => phases[p][op] && phases[p][op].p95);
    if ([before, abuse, after].some((v) => typeof v !== 'number')) {
      comparison[op] = { held: false, reason: 'a phase has no samples' };
      continue;
    }
    // The WORSE of the two quiet phases is the reference: the abuser is
    // charged only with what the stack did not also do without it.
    const reference = Math.max(before, after);
    comparison[op] = {
      p95_before: before,
      p95_abuse: abuse,
      p95_after: after,
      reference,
      change_pct: round2((100 * (abuse - reference)) / reference),
      // How far the two quiet phases are apart: the stack's own drift over the
      // run. A change smaller than this cannot be told from noise in either
      // direction, so read it beside `change_pct`.
      quiet_spread_pct: round2((100 * Math.abs(before - after)) / Math.min(before, after)),
      held: abuse <= reference * (1 + TOLERANCE),
      within_budget: abuse < BUDGET_MS[op],
    };
  }

  const abuserRequests = val('http_reqs{tenant:abuser}').count || 0;
  const abuserRefused = val('aizzak_rate_limited_total{tenant:abuser}').count || 0;
  const abuserFailed = rate('aizzak_failed_requests{tenant:abuser}').count;
  const byScope = {};
  for (const scope of REFUSAL_SCOPES) {
    const n = val(`aizzak_rate_limited_total{tenant:abuser,scope:${scope}}`).count || 0;
    if (n) byScope[scope] = n;
  }
  const retryAfter = rate('aizzak_429_retry_after{tenant:abuser}');
  // A sliding 60 s log admits at most `ceiling` in any 60 s, so over a closed
  // interval of PHASE_S it admits at most ceiling × (⌊PHASE_S/60⌋ + 1) -- the
  // extra window is an entry at each end. Exceeding it means the abuser got
  // past its ceiling, whatever the neighbours saw.
  const admittedBound = USER_RATE_PER_MIN * (Math.floor(PHASE_S / 60) + 1);
  const admitted = abuserRequests - abuserRefused - abuserFailed;
  const abuser = {
    pool_index: abuserIndex(),
    offered_rps: ABUSE_RPS,
    offered_per_min: ABUSE_RPS * 60,
    ceiling_per_min: USER_RATE_PER_MIN,
    requests: abuserRequests,
    admitted,
    admitted_bound: admittedBound,
    refused: abuserRefused,
    refused_by_scope: byScope,
    failed: abuserFailed,
    retry_after_rate: retryAfter.rate,
    latency: trend('http_req_duration{tenant:abuser}'),
    dropped_iterations: val('dropped_iterations{tenant:abuser}').count || 0,
  };
  abuser.contained =
    abuserRefused > 0 && admitted <= admittedBound && retryAfter.rate === 1;

  const neighboursRefused = ABUSE_PHASES.reduce((n, p) => n + phases[p].refused, 0);
  const checks = {
    delivered: ABUSE_PHASES.every((p) => phases[p].delivered),
    neighbours_never_refused: neighboursRefused === 0,
    neighbour_errors_within_budget: phases.abuse.error_rate < 0.001,
    read_p95_held: comparison.read.held === true,
    write_p95_held: comparison.write.held === true,
    abuser_contained: abuser.contained,
  };
  return {
    warmup_s: WARMUP_S,
    phase_s: PHASE_S,
    neighbour_rps: NEIGHBOUR_RPS,
    neighbours: poolSize() - 1,
    tolerance_pct: TOLERANCE * 100,
    held: Object.values(checks).every(Boolean),
    checks,
    comparison,
    phases,
    abuser,
  };
}

function textSummary(iso) {
  const ms = (v) => (typeof v === 'number' ? `${Math.round(v)}` : '—');
  const pct = (v) => (typeof v === 'number' ? `${v > 0 ? '+' : ''}${v.toFixed(1)}%` : '—');
  const a = iso.abuser;
  const lines = [
    `\nabuse: ${iso.neighbours} neighbours at ${iso.neighbour_rps} rps · one abuser at ` +
      `${a.offered_rps} rps (${a.offered_per_min}/min against ${a.ceiling_per_min}/min) · ` +
      `3 × ${iso.phase_s} s`,
  ];
  for (const op of ['read', 'write']) {
    const c = iso.comparison[op];
    lines.push(
      `  neighbour ${op.padEnd(5)} p95 before/abuse/after: ` +
        `${ms(c.p95_before)} / ${ms(c.p95_abuse)} / ${ms(c.p95_after)} ms · ` +
        `${pct(c.change_pct)} vs the worse quiet phase (tolerance ${iso.tolerance_pct}%, ` +
        `quiet phases ${pct(c.quiet_spread_pct)} apart)${c.held ? '  ✓' : '  ✗'}`,
    );
  }
  lines.push(
    `  neighbour 429s before/abuse/after: ${ABUSE_PHASES.map((p) => iso.phases[p].refused).join(' / ')} · ` +
      `errors during abuse ${(iso.phases.abuse.error_rate * 100).toFixed(3)}%`,
  );
  const scopes = Object.entries(a.refused_by_scope)
    .map(([k, v]) => `${k} ${v}`)
    .join(', ');
  lines.push(
    `  abuser: ${a.requests} sent · ${a.admitted} admitted (at most ${a.admitted_bound}) · ` +
      `${a.refused} refused (${scopes || 'none'}) · Retry-After on ` +
      `${a.retry_after_rate === null ? '—' : `${(a.retry_after_rate * 100).toFixed(1)}%`} · ` +
      `p95 ${ms(a.latency && a.latency.p95)} ms`,
  );
  const failed = Object.entries(iso.checks)
    .filter(([, ok]) => !ok)
    .map(([k]) => k);
  lines.push(`isolation: ${iso.held ? 'HELD' : `NOT HELD (${failed.join(', ')})`}\n`);
  return lines.join('\n');
}

function round2(n) {
  return Math.round(n * 100) / 100;
}
