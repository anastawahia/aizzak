// The two sides of 1.2's isolation criterion: "مستأجرٌ مسيءٌ في اختبار k6 لا
// يرفع p95 لجاره".
//
// Both run `browse`'s mix -- the same reads, the same heartbeat, the same one
// creating write -- so the abuser differs from its neighbours in RATE and in
// nothing else. What the criterion asks is whether volume alone, from one
// tenant, reaches anybody else; an abuser on a different endpoint would also
// be measuring that endpoint's cost.
//
// The tenant, the phase and who is who are SCENARIO tags (`lib/profile.js`),
// so every sample below -- including the 429s `graded()` counts, with the
// ceiling that refused them -- lands in its phase's slice without this file
// naming one.

import exec from 'k6/execution';
import { abuserToken, neighbourTokenForIteration } from '../lib/auth.js';
import { browseAs } from './browse.js';

export function neighbour() {
  const n = exec.scenario.iterationInTest;
  return browseAs(neighbourTokenForIteration(n), n);
}

export function abuser() {
  return browseAs(abuserToken(), exec.scenario.iterationInTest);
}
