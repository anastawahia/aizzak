// The BACKLOG profile -- the load of capacity 5.5's live acceptance
// (`docs/design/08-local-runbook.md` §4.21): §0's 100 index jobs a minute for
// the criterion's 20 minutes, through the real edge with the real pool, and
// no other traffic.
//
//   deploy/load/run.sh backlog
//
// LOAD_DURATION_S  seconds of arrivals (default 1200 -- the criterion's 20 minutes)
//
// It only OFFERS the load. Stopping `worker-knowledge` before it, starting it
// after, and the per-message verdict are the runbook's steps around it: a k6
// script cannot stop a container, and the verdict cannot be read until the
// worker has caught up, long after k6 has exited.

import { buildBacklogOptions, guard } from './lib/profile.js';
import { buildSummary } from './lib/summary.js';
import { uploaderCount } from './lib/auth.js';
import { TARGET } from './lib/config.js';

const DURATION_S = Number(__ENV.LOAD_DURATION_S || 1200);

// Two of the platform's ceilings, copied rather than imported (this is k6,
// not Python); `tests/unit/test_load_backlog.py` pins both to their source.
// 1.3's per-user budget on queue entrances (`Limits.heavy_jobs_per_min`) ...
const HEAVY_JOBS_PER_MIN = 30;
// ... and the file room `mint_load_tokens` leaves in every upload space
// (`UPLOAD_HEADROOM_FILES`).
const UPLOAD_HEADROOM_FILES = 100;

export const options = buildBacklogOptions({ durationS: DURATION_S });

export function setup() {
  assertSpread();
  return guard({ durationS: DURATION_S, wsVus: 0 });
}

export { indexBacklog } from './scenarios/index_backlog.js';

// The jobs are spread over the pool one per entry in turn (`lib/auth.js`), so
// each user's share is arithmetic. A pool too small for it would be refused
// by the platform -- 429s from 1.3's ceiling, 409s from a full space -- and
// those arrivals would never become events: a run that offered less than the
// criterion's load while reporting it. Half the heavy-job budget, because the
// limiter's window and the arrivals' are not aligned.
function assertSpread() {
  const users = uploaderCount();
  const perMinute = TARGET.indexJobsPerMinute / users;
  const perRun = Math.ceil((TARGET.indexJobsPerMinute * DURATION_S) / 60 / users);
  if (perMinute > HEAVY_JOBS_PER_MIN / 2 || perRun > UPLOAD_HEADROOM_FILES) {
    throw new Error(
      `${TARGET.indexJobsPerMinute} jobs a minute over ${users} uploading tokens is ` +
        `${perMinute.toFixed(1)} a minute and ${perRun} over the run for each user, against ` +
        `${HEAVY_JOBS_PER_MIN} a minute (1.3) and ${UPLOAD_HEADROOM_FILES} files of room. ` +
        'Mint more tokens (README §2) or shorten LOAD_DURATION_S.',
    );
  }
}

export function handleSummary(data) {
  const out = __ENV.LOAD_OUT || 'deploy/load/results/backlog-latest.json';
  const summary = buildSummary('backlog', data);
  const count = (name) => (((data.metrics || {})[name] || {}).values || {}).count || 0;
  summary.backlog = {
    duration_s: DURATION_S,
    jobs_per_minute: TARGET.indexJobsPerMinute,
    offered: count('iterations'),
    // 202s: each one is a `knowledge.document.registered` event in the outbox,
    // the number the SQL verdict of §4.21 (٤) must find twice.
    accepted: count('aizzak_index_jobs_accepted'),
    dropped: count('dropped_iterations'),
  };
  return {
    [out]: JSON.stringify(summary, null, 2),
    stdout: textSummary(summary),
  };
}

function textSummary(summary) {
  const b = summary.backlog;
  const rate = ((summary.counters || {}).aizzak_failed_requests || {}).rate;
  return (
    `\nbacklog: ${b.jobs_per_minute}/min for ${b.duration_s}s · offered ${b.offered} · ` +
    `accepted ${b.accepted} · dropped ${b.dropped} · ` +
    `errors ${rate === undefined ? '—' : (rate * 100).toFixed(3)}%\n`
  );
}
