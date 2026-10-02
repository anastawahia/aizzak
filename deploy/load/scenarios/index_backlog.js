// Index jobs OFFERED and not awaited -- the load of capacity 5.5's acceptance
// (`docs/design/08-local-runbook.md` §4.21): "إيقافُ `worker-knowledge`
// عشرين دقيقةً تحت حمل 100 مهمّة/دقيقة ثمّ إعادتُه: صفرُ رسالةٍ مفقودة".
//
// `indexFile` measures the asynchronous path end to end, so it polls every job
// until the worker answers, for up to 300 s. With the worker STOPPED no job
// ever answers, and that scenario becomes a VU parked on every arrival, polling
// a document that cannot move -- API load the criterion does not ask for. What
// the criterion needs is only that the events ARRIVE at §0's rate while their
// reader is away, so this one stops at the 202. The verdict is not here at
// all: it is per message, in SQL, after the worker has caught up (§4.21 (٤)).
//
// The document is SMALL on purpose -- two lines and one chunk, where
// `indexFile`'s is ~40 KB and 37 chunks. Loss does not depend on size, and the
// backlog must drain before the verdict can be read: at the 8-10 documents a
// minute measured for the big one (`د‑31`), 2,000 jobs are three to four hours
// of waiting; at 5.1's ~1 small document a second, about half an hour.

import exec from 'k6/execution';
import { uploadTokenForIteration } from '../lib/auth.js';
import { indexJobsAccepted } from '../lib/metrics.js';
import { submitIndexJob } from './index_file.js';

export function indexBacklog() {
  // Unique across VUs within the scenario: the token is chosen by the job,
  // not by the VU (`lib/auth.js` says why), and the number goes into the
  // name and the text so no two documents are the same file.
  const n = exec.scenario.iterationInTest;
  const tok = uploadTokenForIteration(n);
  const id = submitIndexJob(tok, smallDocument(n), `load-backlog-${n}-${Date.now()}.txt`);
  if (id !== null) indexJobsAccepted.add(1);
}

function smallDocument(n) {
  return (
    `مستندٌ قصيرٌ رقم ${n}: تُحفظ سجلّاتُ التشغيل تسعين يوماً، وسجلّاتُ التدقيق سبعَ سنوات.\n` +
    `Short document ${n}: operational logs are kept for 90 days, audit records for seven years.\n`
  );
}
