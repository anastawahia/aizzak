// The RAG scenario's question stream -- capacity blocker د‑37.
//
// Until 2026-09-28 `scenarios/rag.js` rotated six fixed questions, and that
// measured two things that are not search. With step 4.3's query-vector cache
// on (`EMBEDDING_CACHE_TTL_S=600`), each question was embedded once and then
// answered from Redis for ten minutes -- ~100% hits by construction, so the
// retrieval p95 was the cache path. And none of the six shared a word with the
// seeded corpus, so the BM25 leg of the hybrid search matched nothing and cost
// nothing. So the stream has three properties:
//
// * DISTINCT questions, `RAG_QUERIES` of them (`lib/config.js`), each a pure
//   function of its rank and different from every other by construction: its
//   last two words spell the rank, scrambled, in base 512.
// * A POPULARITY that is written down rather than implied. A request asks for
//   rank ⌊x⌋−1, where x has density ∝ x^−s on [1, N+1): Zipf-like, with
//   s = `RAG_QUERY_ZIPF` (0 is uniform). How often a question repeats while
//   the cache still holds it FOLLOWS from N and s -- it is this harness's
//   assumption, not the platform's property, which is why both are stamped
//   into every result's `assumptions`.
// * The SEED'S VOCABULARY (`load_seed.TextPool`: two syllables per word, 512
//   words, in the same order), so both legs of the search do real work. One
//   or two words of each question are drawn Zipf-like over that order -- the
//   order the seed weights its paragraphs by -- so the corpus's most common
//   terms are the questions' most common words too, and walk the longest
//   posting lists.
//
// Reproducible: a request's question depends only on its scenario's name and
// its `iterationInTest`, so two runs of one profile ask the same questions in
// the same order, and each step of `step.js` draws its own sequence from the
// same popularity.

import { RAG_QUERIES, RAG_QUERY_ZIPF } from './config.js';

// `load_seed._SYLLABLES_AR` / `_SYLLABLES_EN`, copied -- and compared by
// `tests/unit/test_load_seed.py`, because a drift would put every question
// outside the corpus again and fail nothing.
const SYLLABLES_AR = [
  'مست', 'كتا', 'بيان', 'تقر', 'مشر', 'عمل', 'نظا', 'قرا',
  'دعم', 'خدم', 'منص', 'وحد', 'سجل', 'طلب', 'فهر', 'بحث',
];
const SYLLABLES_EN = [
  'data', 'report', 'proj', 'sys', 'req', 'serv', 'plat', 'unit',
  'log', 'index', 'search', 'note', 'plan', 'team', 'case', 'draft',
];

const HALF = SYLLABLES_AR.length * SYLLABLES_EN.length;
const VOCABULARY = 2 * HALF;

// Two words of 512 spell 512² = 2^18 ranks, so that is the most distinct
// questions the stream can promise.
const DIGIT_BITS = 9;
const RANK_BITS = 2 * DIGIT_BITS;
const RANK_MASK = (1 << RANK_BITS) - 1;

if (!Number.isInteger(RAG_QUERIES) || RAG_QUERIES < 1 || RAG_QUERIES > RANK_MASK + 1) {
  throw new Error(
    `LOAD_RAG_QUERIES must be an integer from 1 to ${RANK_MASK + 1} ` +
      `(got ${__ENV.LOAD_RAG_QUERIES}).`,
  );
}
if (!(RAG_QUERY_ZIPF >= 0)) {
  throw new Error(`LOAD_RAG_QUERY_ZIPF must be a number >= 0 (got ${__ENV.LOAD_RAG_QUERY_ZIPF}).`);
}

// The question a scenario's `iteration`-th request asks.
export function ragQuery(scenario, iteration) {
  return queryText(ragRank(scenario, iteration));
}

// Its popularity rank, without the spelling. Two requests ask the same
// question exactly when they draw the same rank (`queryText` is injective),
// so `rag.js` replays this to say how often the stream it offered repeated
// inside the cache window.
export function ragRank(scenario, iteration) {
  return rank(unit(1, fnv1a(scenario), iteration), RAG_QUERIES, RAG_QUERY_ZIPF);
}

// The question at popularity rank `r` (0 is the most asked). Exported so any
// rank can be looked at, and its distinctness checked, without a run.
export function queryText(r) {
  const words = [];
  const common = 1 + Math.floor(unit(2, r) * 2);
  for (let k = 0; k < common; k++) words.push(word(rank(unit(3, r, k), VOCABULARY, 1)));
  const id = scramble(r);
  words.push(word(id & ((1 << DIGIT_BITS) - 1)), word(id >>> DIGIT_BITS));
  return words.join(' ');
}

// Word `i` of the seed's vocabulary, in the seed's order: every Arabic+English
// pair, then every English+Arabic one. Computed rather than tabulated because
// every VU imports this module and only `rag` VUs ask (README §4: the init
// context is where the generator's memory goes).
function word(i) {
  const [first, second, j] =
    i < HALF ? [SYLLABLES_AR, SYLLABLES_EN, i] : [SYLLABLES_EN, SYLLABLES_AR, i - HALF];
  return first[Math.floor(j / second.length)] + second[j % second.length];
}

// ⌊x⌋−1 for x with density ∝ x^−s on [1, n+1), by inverting its CDF -- no
// table, so nothing is allocated per VU. s = 1 is where the general form
// divides by zero; s = 0 is uniform.
function rank(u, n, s) {
  const x =
    s === 1 ? Math.pow(n + 1, u) : Math.pow(1 + u * (Math.pow(n + 1, 1 - s) - 1), 1 / (1 - s));
  return Math.min(n - 1, Math.max(0, Math.floor(x) - 1));
}

// A bijection on [0, 2^18): an xor with a constant, a multiply by an odd one
// and an xorshift are each invertible there (the xor keeps rank 0 from
// spelling itself `word(0) word(0)`). It keeps a popular rank's spelling unlike
// its neighbour's; distinctness does not depend on it.
function scramble(r) {
  let x = Math.imul(r ^ 0x2d5a9, 0x2c9277b5) & RANK_MASK;
  x ^= x >>> DIGIT_BITS;
  return Math.imul(x, 0x5f356495) & RANK_MASK;
}

// Uniform in [0, 1) from integers, each folded through lowbias32 (C. Wellons'
// 32-bit integer hash), so neighbouring iterations land far apart.
function unit(...parts) {
  let h = 0;
  for (const p of parts) h = mix(h ^ p);
  return h / 4294967296;
}

function mix(x) {
  x = Math.imul(x ^ (x >>> 16), 0x7feb352d);
  x = Math.imul(x ^ (x >>> 15), 0x846ca68b);
  return (x ^ (x >>> 16)) >>> 0;
}

function fnv1a(text) {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193);
  return h >>> 0;
}
