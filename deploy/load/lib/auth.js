// The token pool -- condition (١) of `docs/capacity-plan.md` §0.1.
//
// `07-nfr-slo §2`'s existing measurement used a STUB authenticator, which is
// why the plan calls repeating it "meaningless": the auth path is ح‑2, at
// least two database round trips per request, and a harness that skips it
// measures a system nobody deploys. So this module has exactly one job --
// hand every VU a real Firebase ID token belonging to a real workspace -- and
// two refusals, because the two ways this silently degrades are both easy.
//
// Tokens are NOT minted here. Minting needs a Firebase service account, and
// putting one inside the load harness would make every k6 run a credential
// holder. `deploy/load/README.md` §2 has the minting recipe; the output is a
// JSON file this reads and `.gitignore` refuses to commit.

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
        'Re-mint the pool (README §2), or drive the run with a refresh loop -- ' +
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
