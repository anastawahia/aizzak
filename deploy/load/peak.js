// The PEAK profile -- §7 item 1: 30 continuous minutes at 500 users · 300 rps
// · 1,500 WS · 50 streams · 100 index jobs/minute.
//
//   k6 run deploy/load/peak.js
//
// Read `deploy/load/README.md` first: this run is only a measurement if the
// three conditions in §0.1 hold, and two of them are things only the operator
// can arrange (a real token pool, a realistically sized seed). The harness
// refuses the third by itself.

import { buildOptions, guard, scaleFor } from './lib/profile.js';
import { buildSummary } from './lib/summary.js';
import { TARGET } from './lib/config.js';

const DURATION_S = Number(__ENV.LOAD_DURATION_S || 1800);
const WS_VUS = Number(__ENV.LOAD_WS_VUS || TARGET.wsConnections);

export const options = buildOptions({
  scale: scaleFor('peak'),
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
  const out = __ENV.LOAD_OUT || 'deploy/load/results/peak-latest.json';
  return {
    [out]: JSON.stringify(buildSummary('peak', data), null, 2),
    stdout: textSummary(data),
  };
}

// A deliberately small stdout line rather than k6's default renderer: the
// archived JSON is the artifact, and a terminal that scrolls three screens of
// tables trains people not to read either.
function textSummary(data) {
  const m = data.metrics || {};
  const p95 = (name, tag) => {
    const key = tag ? `${name}{${tag}}` : name;
    const v = (m[key] || {}).values || {};
    return v['p(95)'] === undefined ? '—' : `${Math.round(v['p(95)'])}`;
  };
  const rate = ((m.aizzak_failed_requests || {}).values || {}).rate;
  return (
    '\npeak: ' +
    `read p95 ${p95('http_req_duration', 'op:read')}ms · ` +
    `write p95 ${p95('http_req_duration', 'op:write')}ms · ` +
    `rag p95 ${p95('aizzak_rag_retrieval_ms')}ms · ` +
    `ttft p95 ${p95('aizzak_ttft_ms')}ms · ` +
    `index p95 ${p95('aizzak_index_e2e_ms')}ms · ` +
    `errors ${rate === undefined ? '—' : (rate * 100).toFixed(3)}%\n`
  );
}
