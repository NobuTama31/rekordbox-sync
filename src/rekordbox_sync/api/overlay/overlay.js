"use strict";

const POLL_INTERVAL_MS = 500;
const REQUEST_TIMEOUT_MS = 3000;
const ARTWORK_TIMEOUT_MS = 5000;
const ARTWORK_RETRY_MIN_MS = 1000;
const ARTWORK_RETRY_MAX_MS = 30000;
const LOADING_TITLE = "Now loading...";
const LOADING_ARTIST = "情報を取得しています";

function isSupportedArtwork(source) {
  if (typeof source !== "string" || source === "") return false;
  if (/^data:image\/(?:png|jpeg|jpg|webp);base64,/i.test(source)) return true;
  try {
    const url = new URL(source);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

async function preloadArtwork(source) {
  if (!isSupportedArtwork(source)) return null;
  const image = new Image();
  image.className = "cover";
  image.alt = "";
  image.src = source;
  let timer;
  try {
    await Promise.race([
      image.decode(),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error("Artwork preload timed out")), ARTWORK_TIMEOUT_MS);
      }),
    ]);
    return image;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function trackKey(track) {
  return JSON.stringify([
    track.status,
    track.title,
    track.artist,
    track.work,
    track.artwork,
    track.manual_revision,
  ]);
}

function normalizeTrack(track) {
  if (!track || typeof track !== "object") return null;
  const status = track.status;
  if (status !== "playing" && status !== "loading" && status !== "hidden") return null;
  const text = value => (typeof value === "string" ? value.trim() : "");
  const title = text(track.title);
  const artist = text(track.artist);
  if (status === "playing" && (title === "" || artist === "")) {
    return { status: "hidden", title: "", artist: "", work: "", artwork: "", manual_revision: "" };
  }
  const revision = typeof track.manual_revision === "string" ? track.manual_revision : "";
  if (status === "hidden") {
    return { status, title: "", artist: "", work: "", artwork: "", manual_revision: revision };
  }
  if (status === "loading") {
    return { status, title: LOADING_TITLE, artist: LOADING_ARTIST, work: "", artwork: "", manual_revision: revision };
  }
  return {
    status,
    title,
    artist,
    work: text(track.work),
    artwork: typeof track.artwork === "string" ? track.artwork : "",
    manual_revision: revision,
  };
}

class BackgroundView {
  constructor() {
    this.layers = [...document.querySelectorAll(".background-artwork")];
    this.activeIndex = -1;
    this.currentSource = null;
  }

  show(source) {
    if (!source) {
      for (const layer of this.layers) {
        layer.classList.remove("is-active");
        layer.style.backgroundImage = "";
      }
      this.activeIndex = -1;
      this.currentSource = null;
      return;
    }
    if (source === this.currentSource || this.layers.length < 2) return;
    const nextIndex = this.activeIndex === 0 ? 1 : 0;
    const nextLayer = this.layers[nextIndex];
    const previousLayer = this.activeIndex >= 0 ? this.layers[this.activeIndex] : null;
    nextLayer.style.backgroundImage = `url(${JSON.stringify(source)})`;
    void nextLayer.offsetWidth;
    nextLayer.classList.add("is-active");
    previousLayer?.classList.remove("is-active");
    this.activeIndex = nextIndex;
    this.currentSource = source;
  }
}

class NowPlayingView {
  constructor(card) {
    this.card = card;
    this.scene = card.closest(".scene");
    this.coverSlot = card.querySelector(".cover-slot");
    this.titleNode = card.querySelector(".title");
    this.artistNode = card.querySelector(".artist");
    this.workNode = card.querySelector(".work");
    this.background = new BackgroundView();
    this.currentKey = null;
    this.displayedArtwork = null;
    this.pending = null;
    this.busy = false;
    this.artworkRetryKey = null;
    this.artworkRetryAt = 0;
    this.artworkRetryDelay = ARTWORK_RETRY_MIN_MS;
  }

  enqueue(track) {
    const normalized = normalizeTrack(track);
    if (!normalized) return;
    const key = trackKey(normalized);
    if (key !== this.artworkRetryKey) {
      this.artworkRetryKey = key;
      this.artworkRetryAt = 0;
      this.artworkRetryDelay = ARTWORK_RETRY_MIN_MS;
    }
    // Skip only when the track is fully on screen, artwork included. A
    // failed artwork preload must not settle the track: later polls retry.
    if (!this.busy && this.currentKey === key &&
        (normalized.artwork === "" || this.displayedArtwork === normalized.artwork)) return;
    this.pending = normalized;
    if (!this.busy) void this.drain();
  }

  async loadArtwork(track) {
    if (performance.now() < this.artworkRetryAt) return null;
    const image = await preloadArtwork(track.artwork);
    // A completion for an older track must not schedule another stale retry.
    if (this.artworkRetryKey === trackKey(track)) {
      if (image) {
        this.artworkRetryAt = 0;
        this.artworkRetryDelay = ARTWORK_RETRY_MIN_MS;
      } else {
        this.artworkRetryAt = performance.now() + this.artworkRetryDelay;
        this.artworkRetryDelay = Math.min(this.artworkRetryDelay * 2, ARTWORK_RETRY_MAX_MS);
      }
    }
    return image;
  }

  async drain() {
    this.busy = true;
    try {
      while (this.pending) {
        const next = this.pending;
        this.pending = null;
        const key = trackKey(next);
        if (key === this.currentKey) {
          // Same track again: retry a failed artwork preload in place,
          // without fading the card or touching the text.
          if (next.artwork && next.artwork !== this.displayedArtwork) {
            const retry = await this.loadArtwork(next);
            if (retry && (!this.pending || trackKey(this.pending) === key)) {
              this.coverSlot.replaceChildren(retry);
              this.background.show(next.artwork);
              this.displayedArtwork = next.artwork;
            }
          }
          continue;
        }

        const image = next.artwork ? await this.loadArtwork(next) : null;
        if (this.pending && trackKey(this.pending) !== key) continue;

        if (this.currentKey !== null) await this.setHidden(true);

        if (image) {
          this.coverSlot.replaceChildren(image);
          this.background.show(next.artwork);
          this.displayedArtwork = next.artwork;
        } else {
          this.coverSlot.replaceChildren();
          this.background.show(null);
          this.displayedArtwork = null;
        }
        this.titleNode.textContent = next.title;
        this.artistNode.textContent = next.artist;
        this.workNode.textContent = next.work;
        this.workNode.classList.toggle("is-empty", next.work === "");
        this.card.classList.toggle("is-loading", next.status === "loading");
        this.card.setAttribute("aria-busy", String(next.status === "loading"));
        this.scene?.classList.toggle("is-suppressed", next.status === "hidden");
        this.currentKey = key;
        if (next.status !== "hidden") await this.setHidden(false);
      }
    } finally {
      this.busy = false;
      if (this.pending) void this.drain();
    }
  }

  setHidden(hidden) {
    const card = this.card;
    const variable = hidden ? "--fade-out" : "--fade-in";
    const duration = parseFloat(getComputedStyle(card).getPropertyValue(variable)) || 0;
    return new Promise(resolve => {
      let finished = false;
      const finish = () => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        card.removeEventListener("transitionend", onEnd);
        // OBS may suspend transitions while a source is hidden; force the
        // end state before any DOM content changes.
        const transition = card.style.transition;
        card.style.transition = "none";
        card.classList.toggle("is-hidden", hidden);
        void card.offsetWidth;
        card.style.transition = transition;
        resolve();
      };
      const onEnd = event => {
        if (event.target === card && event.propertyName === "opacity") finish();
      };
      void card.offsetWidth;
      card.addEventListener("transitionend", onEnd);
      const timer = setTimeout(finish, duration + 160);
      card.classList.toggle("is-hidden", hidden);
    });
  }
}

const view = new NowPlayingView(document.querySelector(".now-playing"));
let etag = null;
let lastTrack = null;

async function poll() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch("/nowplaying.json", {
      cache: "no-cache",
      headers: etag ? { "If-None-Match": etag } : {},
      signal: controller.signal,
    });
    if (response.status === 304) {
      // The payload is unchanged, but a failed artwork preload must
      // not settle: re-offer the last track so the view retries it.
      // enqueue() is a no-op once the track is fully displayed,
      // artwork included, so a settled track costs nothing (audit #6).
      if (lastTrack) view.enqueue(lastTrack);
      return;
    }
    if (!response.ok) throw new Error(`Now Playing HTTP ${response.status}`);
    lastTrack = await response.json();
    view.enqueue(lastTrack);
    etag = response.headers.get("ETag") || etag;
  } catch (error) {
    console.debug("Waiting for Now Playing data", error);
  } finally {
    clearTimeout(timer);
    setTimeout(poll, POLL_INTERVAL_MS);
  }
}

void poll();
