// The AVERAGE profile -- §7 item 2: EIGHT continuous hours before any public
// opening, at 50 rps and a proportionally scaled population.
//
//   k6 run deploy/load/average.js
//
// ── Why eight hours exist at all, and what is actually measured ───────────
// §7 states it plainly: "تسرّبُ ذاكرةٍ بمعدّل 20MB/ساعة لا يظهر في ثلاثين
// دقيقةً بأيّ حالٍ من الأحوال، ويقتل النسخةَ في اليوم الثالث. والمقيسُ هنا
// **ميلُ** استهلاك الذاكرة لا قمّتُه." So the quantity this profile produces
// is not in k6's summary at all -- it is the SLOPE of process memory over the
// run, read from `aizzak_*` saturation metrics in Prometheus (step 0.2/0.3).
// k6's job here is to keep a steady, honest load on for eight hours and to
// prove nothing degraded while it did; the latency table it archives is the
// evidence for that half.
//
// ⚠️ A Firebase ID token lives ONE hour. An eight-hour run therefore needs a
// pool that is refreshed, and `assertTokensCoverRun` refuses to start
// otherwise rather than let hour two report a 100% error rate that is the
// harness. README §2 covers the refresh loop.

import { buildOptions, guard, scaleFor } from './lib/profile.js';
import { buildSummary } from './lib/summary.js';
import { TARGET } from './lib/config.js';

const DURATION_S = Number(__ENV.LOAD_DURATION_S || 8 * 3600);
// The same ×6 factor §0 derives between average and peak rps, applied to the
// socket population rather than restated as an independent number.
const WS_VUS = Number(__ENV.LOAD_WS_VUS || Math.round(TARGET.wsConnections * scaleFor('average')));

export const options = buildOptions({
  scale: scaleFor('average'),
  durationS: DURATION_S,
  wsVus: WS_VUS,
});

export function setup() {
  return guard({ durationS: DURATION_S, wsVus: WS_VUS });
}

export { browse } from './scenarios/browse.js';
export { rag } from './scenarios/rag.js';
export { stream } from './scenarios/stream.js';
export { indexFile } from './scenarios/index_file.js';
export { wsHold } from './scenarios/ws_hold.js';

export function handleSummary(data) {
  const out = __ENV.LOAD_OUT || 'deploy/load/results/average-latest.json';
  return {
    [out]: JSON.stringify(buildSummary('average', data), null, 2),
    stdout: textSummary(data),
  };
}

function textSummary(data) {
  const m = data.metrics || {};
  const p95 = (name, tag) => {
    const key = tag ? `${name}{${tag}}` : name;
    const v = (m[key] || {}).values || {};
    return v['p(95)'] === undefined ? '—' : `${Math.round(v['p(95)'])}`;
  };
  const rate = ((m.aizzak_failed_requests || {}).values || {}).rate;
  return (
    '\naverage: ' +
    `read p95 ${p95('http_req_duration', 'op:read')}ms · ` +
    `write p95 ${p95('http_req_duration', 'op:write')}ms · ` +
    `rag p95 ${p95('aizzak_rag_retrieval_ms')}ms · ` +
    `ttft p95 ${p95('aizzak_ttft_ms')}ms · ` +
    `errors ${rate === undefined ? '—' : (rate * 100).toFixed(3)}%\n` +
    'the memory SLOPE is the point of this profile and is not in this file — read it in Grafana.\n'
  );
}
