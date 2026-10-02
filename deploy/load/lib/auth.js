// The token pool -- condition (١) of `docs/capacity-plan.md` §0.1.
//
// `07-nfr-slo §2`'s existing measurement used a STUB authenticator, which is
// why the plan calls repeating it "meaningless": the auth path is ح‑2, at
// least two database round trips per request, and a harness that skips it
// measures a system nobody deploys. So this module has exactly one job --
// hand every VU a real Firebase ID token belonging to a real workspace -- and
// two refusals, because the two ways this silently degrades are both easy.
//
// Tokens are NOT minted here -- `python -m app.ops.mint_load_tokens` mints
// them (README §2), and the output is a JSON file this reads and `.gitignore`
// refuses to commit. An earlier version of this comment said minting "needs a
// Firebase service account"; it does not. An Email/Password sign-up is a
// client operation against the project's Web API key, and a service account
// could only mint CUSTOM tokens, which `firebase_auth.py` refuses anyway
// (`iss` must be securetoken.google.com). Keeping the key out of the harness
// is still right: k6 has no reason to hold a credential that creates accounts.

import { SharedArray } from 'k6/data';
import encoding from 'k6/encoding';

// `open()` resolves a relative path against THIS MODULE, not the working
// directory -- so the obvious `./tokens.json` looked for `lib/tokens.json`
// and reported `no such file or directory` for a pool that was sitting
// exactly where README §2 says to put it. Measured on the first execution
// (capacity blocker د‑3); `run.sh` passes an absolute path, which is why
// only a bare `k6 run` ever saw it.
const TOKEN_FILE = __ENV.LOAD_TOKEN_FILE || '../tokens.json';

// `SharedArray` is not an optimisation here, it is a requirement: at 1,500 WS
// VUs a per-VU copy of the pool is 1,500 copies of every token in memory, and
// k6 would be measuring its own allocator alongside the platform.
const pool = new SharedArray('firebase-tokens', () => {
  const raw = JSON.parse(open(TOKEN_FILE));
  if (!Array.isArray(raw.tokens) || raw.tokens.length === 0) {
    throw new Error(`${TOKEN_FILE} carries no tokens; see deploy/load/README.md §2.`);
  }
  return raw.tokens.map((t, i) => {
    // `space_id` is not optional decoration. `KnowledgeSearchIn`,
    // `FileRegisterIn` and `ConversationCreateIn` all REQUIRE it (س-32: a
    // search spans one space or it does not run), so a pool without one can
    // execute exactly one of the five scenarios. Refusing at load time beats
    // a run that reports 422 on four scenarios out of five.
    if (!t.space_id) {
      throw new Error(`tokens[${i}] (${t.workspace || '?'}) has no space_id; see README §2.`);
    }
    return {
      idToken: t.id_token,
      workspace: t.workspace || `unknown-${i}`,
      spaceId: t.space_id,
      // Where uploads go (د-33): the content space is often over a ceiling
      // for the seed's heaviest tenants. A pool from before the field falls
      // back to `space_id`, and `mint_load_tokens verify` refuses such a pool.
      uploadSpaceId: t.upload_space_id === undefined ? t.space_id : t.upload_space_id,
      exp: expiryOf(t.id_token),
    };
  });
});

// Declared by whoever minted the file, and carried into the run summary. A
// stub run is not refused -- it is a legitimate way to exercise the harness
// itself -- but it can never be a baseline, and this is what stops it from
// being read as one six weeks later.
export const TOKENS_ARE_REAL = new SharedArray('firebase-tokens-real', () => {
  const raw = JSON.parse(open(TOKEN_FILE));
  return [raw.stub === false];
})[0];

// One token per VU, stable for the VU's whole life. Round-robin rather than
// random: a workspace must be able to see its own uploads on a later
// iteration, and a VU that changes identity between iterations cannot.
export function poolSize() {
  return pool.length;
}

export function tokenForVu() {
  return pool[(__VU - 1) % pool.length];
}

// For the upload scenario: the same round-robin, over the entries whose
// tenant has room. A tenant at the byte or file ceiling answers every upload
// `409` -- correctly -- and a VU bound to one fails in milliseconds, is idle
// again at once, and so takes a far larger share of arrivals than 1/N: in the
// 2026-09-28 run 13 such spaces of 500 were 59% of all failures (د-33).
// Indices, built on first use: only the upload scenario's VUs ever call
// this, and a copy of the pool itself in each of 1,500 VUs is what
// `SharedArray` exists to prevent.
let uploaders = null;

function uploaderIndices() {
  if (uploaders === null) {
    uploaders = [];
    for (let i = 0; i < pool.length; i++) if (pool[i].uploadSpaceId) uploaders.push(i);
  }
  if (uploaders.length === 0) {
    throw new Error('no pool entry has an upload_space_id; run `mint_load_tokens refresh`.');
  }
  return uploaders;
}

export function uploadTokenForVu() {
  const idx = uploaderIndices();
  return pool[idx[(__VU - 1) % idx.length]];
}

// The same entries, round-robin over the ITERATION instead of the VU -- for a
// profile that does not wait on its jobs (`scenarios/index_backlog.js`). A job
// that ends at its index request holds a VU for well under a second, so k6
// serves 100 arrivals a minute from one or two VUs, and a VU-bound token would
// put nearly every job on one or two users: past 1.3's heavy-job ceiling
// (30 a minute per user) within the first minute, and past their upload
// space's file room within the run. Per iteration, 2,000 jobs over 500
// entries is four each.
export function uploadTokenForIteration(i) {
  const idx = uploaderIndices();
  return pool[idx[i % idx.length]];
}

export function uploaderCount() {
  return uploaderIndices().length;
}

// For a VU that holds several sockets (`ws_hold.js`): socket `slot` of
// `slots`, a stride apart, so one VU's sockets belong to `slots` different
// users and the population spreads over the pool as evenly as one socket per
// VU did -- `ws_connections_per_user` is 5, and the guard in `profile.js`
// counts on nobody holding more than their share.
export function tokenForSlot(slot, slots) {
  const stride = Math.max(1, Math.floor(pool.length / slots));
  return pool[(__VU - 1 + slot * stride) % pool.length];
}

// For code that runs outside a VU (`setup()`, where `__VU` is 0): one real
// token, any of them -- the pre-flight probes in `profile.js`. The index is
// for the one probe that can be legitimately refused for the token it picked
// rather than for the platform's state: a space at its byte ceiling answers
// `409 spaces.quota_exceeded`, which says nothing about the other 499.
export function anyToken(index) {
  return pool[(index || 0) % pool.length];
}

export function authHeaders(tok, extra) {
  return Object.assign(
    {
      Authorization: `Bearer ${tok.idToken}`,
      'Content-Type': 'application/json',
    },
    extra || {},
  );
}

// A Firebase ID token lives one hour. §7's acceptance gate demands a
// CONTINUOUS eight-hour run at the average profile, so the pool WILL expire
// mid-run, and the failure mode is the quiet one: every request turns 401,
// the error-rate threshold trips, and the report reads like a platform
// failure. Refusing up front costs one line; diagnosing it afterwards costs
// the run.
export function assertTokensCoverRun(runSeconds) {
  const now = Math.floor(Date.now() / 1000);
  let earliest = Infinity;
  for (const t of pool) {
    if (t.exp > 0 && t.exp < earliest) earliest = t.exp;
  }
  if (earliest === Infinity) return; // no `exp` claim readable -- a stub pool
  const remaining = earliest - now;
  if (remaining < runSeconds) {
    throw new Error(
      `The earliest token expires in ${remaining}s but the profile runs for ${runSeconds}s. ` +
        'Run `python -m app.ops.mint_load_tokens refresh` (seconds; README §2) -- ' +
        'an expiring pool reports a 100% error rate that is the harness, not the platform.',
    );
  }
}

function expiryOf(jwt) {
  try {
    const payload = JSON.parse(encoding.b64decode(jwt.split('.')[1], 'rawurl', 's'));
    return Number(payload.exp) || 0;
  } catch {
    return 0;
  }
}
