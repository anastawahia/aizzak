// The STEP profile -- 0.5's capacity figure: the same mix as `peak.js`,
// offered at rising rates one step after another, each step reported on its
// own. `lib/profile.js` (`stepPlan`) says why a constant rate above the
// ceiling cannot find the ceiling.
//
//   deploy/load/run.sh step
//
// LOAD_STEPS    total API rps per step, comma-separated (default 50,100,150,200
//               -- the average rate, then up past the ~160 the 2026-09-26 peak
//               run saturated at)
// LOAD_STEP_S   seconds each step holds (default 360)
// LOAD_WS_VUS   the socket population at PEAK; each step holds its share

import { buildStepOptions, guard, stepPlan, stepWsVus } from './lib/profile.js';
import { buildSummary } from './lib/summary.js';
import { TARGET } from './lib/config.js';

const STEPS_RPS = String(__ENV.LOAD_STEPS || '50,100,150,200')
  .split(',')
  .map((v) => Number(v.trim()))
  .filter((v) => v > 0);
const HOLD_S = Number(__ENV.LOAD_STEP_S || 360);
const WS_PEAK = Number(__ENV.LOAD_WS_VUS || TARGET.wsConnections);

const PLAN = stepPlan(STEPS_RPS, HOLD_S);
const DURATION_S = PLAN.length * HOLD_S;

export const options = buildStepOptions({ plan: PLAN, wsPeak: WS_PEAK });

export function setup() {
  // The guard's per-user socket ceiling is checked against the LARGEST step's
  // population, which is the one that could provoke the limiter.
  const wsMax = Math.max(...PLAN.map((step) => stepWsVus(step, WS_PEAK)));
  return guard({ durationS: DURATION_S, wsVus: wsMax });
}

export { browse } from './scenarios/browse.js';
export { rag } from './scenarios/rag.js';
export { stream } from './scenarios/stream.js';
export { indexFile } from './scenarios/index_file.js';
export { wsHold } from './scenarios/ws_hold.js';

export function handleSummary(data) {
  const out = __ENV.LOAD_OUT || 'deploy/load/results/step-latest.json';
  const summary = buildSummary('step', data, { steps: PLAN });
  return {
    [out]: JSON.stringify(summary, null, 2),
    stdout: textSummary(summary),
  };
}

function textSummary(summary) {
  const ms = (v) => (v === undefined || v === null ? '—' : `${Math.round(v)}`);
  const lines = [
    '\nstep: rps offered → served · dropped · read/write/rag/ttft p95 ms · errors · ' +
      'index timed out/verdicts',
  ];
  for (const s of summary.steps) {
    lines.push(
      `  ${String(s.rps).padStart(3)} → ${s.served_http_rps.toFixed(0).padStart(3)} · ` +
        `${String(s.dropped_iterations).padStart(6)} · ` +
        `${ms(s.p95.read)}/${ms(s.p95.write)}/${ms(s.p95.rag)}/${ms(s.p95.ttft)} · ` +
        `${(s.error_rate * 100).toFixed(2)}% · ` +
        `${s.index_timeouts.count}/${s.index_timeouts.verdicts}` +
        (s.within_budget ? '  ✓ budget' : s.delivered ? '  delivered' : ''),
    );
  }
  const k = summary.knee;
  lines.push(
    `knee: sustained up to ${k.sustained_rps ?? 'none'} rps · within budget up to ${k.within_budget_rps ?? 'none'} rps\n`,
  );
  return lines.join('\n');
}
