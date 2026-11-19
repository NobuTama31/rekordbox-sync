"use strict";
// Node harness for audit finding #6: overlay.js must keep retrying a
// failed artwork preload while poll responses stay 304. Loads the real
// overlay.js with stubbed DOM/Image/fetch and manual timers.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(
  path.join(__dirname, "..", "src", "rekordbox_sync", "api", "overlay", "overlay.js"),
  "utf8");

// Manual timers: poll rescheduling and fade timers are fired by hand.
let timers = [];
let timerSeq = 0;
let now = 0;
function fakeSetTimeout(fn, ms) {
  const id = ++timerSeq;
  timers.push({ id, fn, at: now + ms });
  return id;
}
function fakeClearTimeout(id) {
  timers = timers.filter(t => t.id !== id);
}
function fireTimers() {
  if (timers.length === 0) return;
  now = Math.max(now, Math.min(...timers.map(t => t.at)));
  const due = timers.filter(t => t.at <= now);
  timers = timers.filter(t => t.at > now);
  for (const t of due) t.fn();
}

function classList() {
  return { add() {}, remove() {}, toggle() {} };
}
const scene = { classList: classList() };
function node() {
  return {
    classList: classList(), style: {}, textContent: "",
    setAttribute() {}, addEventListener() {}, removeEventListener() {},
    replaceChildren() {}, querySelector() { return node(); },
    closest() { return scene; }, offsetWidth: 0,
  };
}
const card = node();

let artworkAttempts = 0;
let failArtwork = true;
class FakeImage {
  constructor() {
    this.className = "";
    this.alt = "";
  }
  set src(value) {
    this._src = value;
    artworkAttempts += 1;
  }
  get src() {
    return this._src;
  }
  decode() {
    return failArtwork ? Promise.reject(new Error("no art")) : Promise.resolve();
  }
}

const payload = {
  status: "playing", title: "T", artist: "A", work: "",
  artwork: "https://example.com/art.jpg", manual_revision: "",
};
let fetchCalls = 0;
async function fakeFetch() {
  fetchCalls += 1;
  if (fetchCalls === 1) {
    return { status: 200, ok: true, headers: { get: () => "\"v1\"" }, json: async () => payload };
  }
  return { status: 304, ok: true, headers: { get: () => null }, json: async () => ({}) };
}

const sandbox = {
  document: { querySelector: () => card, querySelectorAll: () => [] },
  Image: FakeImage,
  fetch: fakeFetch,
  getComputedStyle: () => ({ getPropertyValue: () => "0" }),
  setTimeout: fakeSetTimeout,
  clearTimeout: fakeClearTimeout,
  AbortController, URL, console, performance: { now: () => now },
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);

async function flush(rounds = 40) {
  for (let i = 0; i < rounds; i++) await new Promise(r => setImmediate(r));
}
async function cycle() {
  fireTimers();
  await flush();
  fireTimers();
  await flush();
}

(async () => {
  const failures = [];
  await flush(); // poll 1: 200, artwork preload fails (no 304 seen yet)
  if (artworkAttempts !== 1) {
    failures.push(`after 200: attempts=${artworkAttempts}, want 1`);
  }
  await cycle(); // let the first drain settle
  await cycle(); // polls 2..4: 304 responses only
  await cycle();
  await cycle();
  if (artworkAttempts < 2) {
    failures.push(`after 304s: attempts=${artworkAttempts}, want >= 2 (retry starved by 304)`);
  }
  failArtwork = false;
  const failedAttempts = artworkAttempts;
  for (let i = 0; i < 10 && artworkAttempts === failedAttempts; i++) {
    await cycle(); // Wait through the bounded retry backoff.
  }
  if (artworkAttempts !== failedAttempts + 1) {
    failures.push(`successful retry count=${artworkAttempts - failedAttempts}, want 1`);
  }
  await cycle();
  const settled = artworkAttempts;
  await cycle(); // settled artwork must not be refetched on further 304s
  await cycle();
  if (artworkAttempts !== settled) {
    failures.push(`settled artwork refetched: ${settled} -> ${artworkAttempts}`);
  }
  if (failures.length) {
    console.error("FAIL: " + failures.join("; "));
    process.exit(1);
  }
  console.log(`PASS attempts=${artworkAttempts} fetches=${fetchCalls}`);
})().catch(error => {
  console.error("ERROR", error);
  process.exit(2);
});
