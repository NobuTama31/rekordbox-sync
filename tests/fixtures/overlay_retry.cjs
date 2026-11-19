"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function harness() {
  let now = 0;
  const attempts = [];
  const cover = { images: [], replaceChildren(...images) { this.images = images; } };
  const nodes = { ".cover-slot": cover, ".title": {}, ".artist": {},
    ".work": { classList: { toggle() {} } } };
  const card = { closest: () => ({ classList: { toggle() {} } }),
    querySelector: selector => nodes[selector], classList: { toggle() {} }, setAttribute() {} };
  const track = name => ({ status: "playing", title: name, artist: "Artist", work: "",
    artwork: `https://example.test/${name}.jpg`, manual_revision: "" });
  const state = { response: track("A"), loader: async () => null };
  const context = vm.createContext({
    console, URL, AbortController, performance: { now: () => now },
    setTimeout: () => 1, clearTimeout() {},
    document: { querySelector: () => card, querySelectorAll: () => [] },
    fetch: async () => ({ status: state.response ? 200 : 304, ok: true,
      json: async () => state.response, headers: { get: () => '"unchanged"' } }),
    load: async source => { attempts.push(source); return state.loader(source); }
  });
  const source = fs.readFileSync(process.argv[2], "utf8").replace("void poll();", "");
  vm.runInContext(source + `
    preloadArtwork = load;
    view.setHidden = async () => {};
    globalThis.subject = { view, poll };
  `, context);
  async function poll(advance = 500) {
    now += advance;
    await context.subject.poll();
    for (let i = 0; i < 20; i++) await Promise.resolve();
  }
  return { state, attempts, cover, track, poll, view: context.subject.view };
}

(async () => {
  const retry = harness();
  await retry.poll(0); // First artwork request fails.
  retry.state.response = null; // Every later response is 304.
  retry.state.loader = async source => ({ source });
  await retry.poll(5000);
  assert.equal(retry.attempts.length, 2, "304 must retry a failed artwork request");
  assert.equal(retry.cover.images[0].source, retry.track("A").artwork);

  const failing = harness();
  await failing.poll(0);
  failing.state.response = null;
  for (let i = 0; i < 120; i++) await failing.poll();
  assert.ok(failing.attempts.length >= 2, "retry must remain possible");
  assert.ok(failing.attempts.length <= 8, "permanent failure must back off");

  const stale = harness();
  await stale.poll(0);
  stale.state.response = null;
  let resolveOld;
  stale.state.loader = () => new Promise(resolve => { resolveOld = resolve; });
  await stale.poll(5000); // Retry A is now in flight.
  assert.equal(typeof resolveOld, "function");
  stale.state.response = stale.track("B");
  stale.state.loader = async source => ({ source });
  await stale.poll();
  resolveOld({ source: stale.track("A").artwork });
  for (let i = 0; i < 30; i++) await Promise.resolve();
  assert.equal(stale.cover.images[0].source, stale.track("B").artwork);
  stale.state.response = null;
  await stale.poll(60000);
  assert.equal(stale.attempts.filter(url => url.endsWith("A.jpg")).length, 2);
  console.log("304 recovery, bounded backoff, and stale retry cancellation passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
