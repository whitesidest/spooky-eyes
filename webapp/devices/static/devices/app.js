// Spooky Eyes web controller — vanilla JS, no build step.
// One file: a small core (API, toasts, pictures, gaze pad) and one function per page.
const SE = (() => {
  "use strict";

  const snapshot = JSON.parse(document.getElementById("snapshot").textContent);
  const csrf = () => document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const MOODS = snapshot.moods || ["neutral", "angry", "surprised", "sleepy", "asleep"];
  const MOOD_LABELS = { neutral: "Neutral", angry: "Angry", surprised: "Surprised", sleepy: "Sleepy", asleep: "Asleep" };
  const ACTION_LABELS = { blink: "Blink", wink_left: "Wink left", wink_right: "Wink right", startle: "Startle", roll: "Eye roll", release: "Release", sound: "Sound", tone: "Tone", stop_sound: "Stop sound" };
  const ACTION_DONE = { blink: "Blinked", wink_left: "Winked left", wink_right: "Winked right", startle: "Startled", roll: "Rolled", release: "Released", stop_sound: "Silenced" };

  // ---------- Sounds: friendly names and glyphs for the built-in effects ----------
  const SOUND_LABELS = { growl: "Growl", heartbeat: "Heartbeat", whisper: "Whisper", creak: "Creak", zap: "Zap", chime: "Chime", test: "Test tone" };
  const soundLabel = (name) => SOUND_LABELS[name] || name;
  const GLYPHS = {
    growl: '<path d="M4 9c4-4 12-4 16 0"/><path d="m5 9 2 5 2-5 3 6 3-6 2 5 2-5"/>',
    heartbeat: '<path d="M3 12h4l2-5 3 10 2-7 1.5 2H21"/>',
    whisper: '<path d="M3 13c3-2 15-2 18 0"/><path d="M3 13c3 2 15 2 18 0"/><path d="M8 7c1-1 2-1 3 0M13 7c1-1 2-1 3 0"/>',
    creak: '<path d="M5 3h9v18H5z"/><path d="M14 3l5 2v16l-5-2"/><circle cx="11" cy="12" r=".6"/>',
    zap: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    chime: '<path d="M6 17h12l-1.5-2v-4a4.5 4.5 0 0 0-9 0v4z"/><path d="M10 20a2 2 0 0 0 4 0M12 3v1.5"/>',
    test: '<path d="M3 12c3-7 6-7 9 0s6 7 9 0"/>',
    clip: '<path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/>',
    speaker: '<path d="M4 10v4h3l4 3V7l-4 3z"/><path d="M15 9a4 4 0 0 1 0 6M18 6a8 8 0 0 1 0 12"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  };
  const glyph = (name, cls) => `<svg class="${cls || ""}" viewBox="0 0 24 24" aria-hidden="true">${GLYPHS[name] || GLYPHS.clip}</svg>`;

  // Battery badge: {voltage, percent} or null. Hidden when the board has no cell.
  function paintBattery(el, battery) {
    if (!el) return;
    const b = battery && typeof battery.percent === "number" ? battery : null;
    el.hidden = !b;
    if (!b) return;
    const pct = Math.max(0, Math.min(100, Math.round(b.percent)));
    el.classList.toggle("low", pct < 20);
    el.innerHTML = `<svg viewBox="0 0 24 14" aria-hidden="true"><rect x="0.75" y="0.75" width="19.5" height="12.5" rx="2.5"/><rect x="21.5" y="4.5" width="2" height="5" rx="1" class="charge"/><rect class="charge" x="3" y="3" width="${(15 * pct) / 100}" height="8" rx="1"/></svg><span></span>`;
    $("span", el).textContent = `${pct}%`;
    el.title = `Battery ${pct}%${b.voltage ? `, ${Number(b.voltage).toFixed(2)} V` : ""}${pct < 20 ? " — low" : ""}`;
    el.setAttribute("aria-label", el.title);
  }
  const batteryText = (b) => (b && typeof b.percent === "number" ? `${Math.round(b.percent)}% (${Number(b.voltage || 0).toFixed(2)} V)${b.percent < 20 ? ", low" : ""}` : "No battery");

  // Builds sound tiles into `host`: built-ins, then clips, then (optionally) an "Add clip" tile.
  // Returns { setPlaying(name) }. Tiles call onPlay(name); clip delete marks call onDelete(name).
  function soundTiles(host, { builtin = [], clips = [], onPlay, onDelete, onAdd, small }) {
    host.innerHTML = "";
    const make = (name, isClip, bytes) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "sound-tile" + (isClip ? " clip" : "");
      b.dataset.sound = name;
      b.setAttribute("aria-pressed", "false");
      b.innerHTML = glyph(isClip ? "clip" : name) + `<span></span>` + (isClip && bytes ? `<small>${(bytes / 32000).toFixed(bytes < 320000 ? 1 : 0)} s</small>` : "");
      $("span", b).textContent = isClip ? name : soundLabel(name);
      b.addEventListener("click", () => {
        b.setAttribute("aria-busy", "true");
        Promise.resolve(onPlay(name)).finally(() => b.removeAttribute("aria-busy"));
      });
      if (isClip && onDelete) {
        // A delete mark can't live inside the tile's <button>, so the clip gets a slot holding both.
        const slot = document.createElement("div");
        slot.className = "sound-slot";
        const del = document.createElement("button");
        del.type = "button";
        del.className = "del";
        del.setAttribute("aria-label", `Delete clip ${name}`);
        del.innerHTML = glyph("trash");
        del.addEventListener("click", () => onDelete(name));
        slot.append(b, del);
        return slot;
      }
      return b;
    };
    for (const s of builtin) host.appendChild(make(s, false));
    for (const c of clips) host.appendChild(make(typeof c === "string" ? c : c.name, true, typeof c === "string" ? 0 : c.bytes));
    if (onAdd) {
      const add = document.createElement("button");
      add.type = "button";
      add.className = "sound-tile add";
      add.innerHTML = glyph("plus") + `<span>Add clip</span>`;
      add.addEventListener("click", onAdd);
      host.appendChild(add);
    }
    return {
      setPlaying(name) {
        $$(".sound-tile[data-sound]", host).forEach((t) => t.setAttribute("aria-pressed", String(!!name && t.dataset.sound === name)));
      },
    };
  }

  // ---------- Voice of the skull: hold a button, talk into the phone, it plays on the board(s) ----------
  // Builds the block into `host`. getTarget() -> {device}|{group}|{all}; the server converts the recording
  // (with the chosen effect) and uploads it to every targeted speaker board, then plays it.
  // Needs a secure context (HTTPS or localhost) for the microphone; otherwise it says so plainly and
  // leaves the "use a recording" and "play from URL" routes, which always work.
  const VOICE_MAX_MS = 30000;
  const MIC_GLYPH = '<path d="M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3z"/><path d="M6 11a6 6 0 0 0 12 0"/><path d="M12 17v4M9 21h6"/>';
  function voiceControl(host, { getTarget, targetText, big }) {
    const effects = snapshot.voice_effects || [{ id: "natural", name: "Natural" }];
    let effect = (() => {
      try { return localStorage.getItem("se-voice-effect") || "natural"; } catch (_) { return "natural"; }
    })();
    if (!effects.some((e) => e.id === effect)) effect = "natural";
    host.innerHTML = `
      <div class="voice-head"><h3>Voice of the skull</h3><p class="muted small"></p></div>
      <div class="seg voice-effects" role="group" aria-label="Voice effect"></div>
      <div class="talk-row">
        <button type="button" class="talk" aria-label="Hold to talk"><svg viewBox="0 0 24 24" aria-hidden="true">${MIC_GLYPH}</svg></button>
        <div class="talk-text"><strong class="talk-label">Hold to talk</strong><span class="talk-status muted small"></span></div>
      </div>
      <p class="voice-note muted small" hidden></p>
      <div class="voice-alt">
        <label class="btn btn-ghost file-btn"><input type="file" accept="audio/*" capture class="sr-only"><span>Use a recording</span></label>
        <form class="voice-url add-row"><input class="input" type="url" name="url" placeholder="https://… an mp3, wav or ogg" required spellcheck="false"><button class="btn" type="submit">Play</button></form>
      </div>`;
    const sub = $(".voice-head p", host);
    const seg = $(".voice-effects", host);
    const talk = $(".talk", host);
    const row = $(".talk-row", host);
    const label = $(".talk-label", host);
    const status = $(".talk-status", host);
    const note = $(".voice-note", host);
    const file = $(".file-btn input", host);
    const urlForm = $(".voice-url", host);
    const setSub = () => (sub.textContent = `Hold the button, say something, let go — it comes out of ${targetText ? targetText() : "the board"}.`);
    setSub();

    for (const e of effects) {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.effect = e.id;
      b.textContent = e.name;
      b.setAttribute("aria-pressed", String(e.id === effect));
      b.addEventListener("click", () => {
        effect = e.id;
        $$("button", seg).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        try { localStorage.setItem("se-voice-effect", effect); } catch (_) {}
      });
      seg.appendChild(b);
    }

    const canRecord = window.isSecureContext && navigator.mediaDevices?.getUserMedia && window.MediaRecorder;
    if (!canRecord) {
      talk.disabled = true;
      label.textContent = "Microphone unavailable here";
      note.hidden = false;
      note.textContent = window.isSecureContext
        ? "This browser can't record audio. Pick a recording below, or play from a URL."
        : `Browsers only open the microphone on a secure page. Open this app over HTTPS, or as http://localhost on this device — plain http://${location.hostname} won't do. Picking a recording or playing from a URL still works.`;
    }

    // ---- sending ----
    let sending = false;
    async function speak(blob, filename) {
      if (sending) return;
      sending = true;
      talk.classList.add("busy");
      label.textContent = "Sending…";
      status.textContent = `${(blob.size / 1000).toFixed(0)} kB, ${effects.find((e) => e.id === effect)?.name || effect}`;
      const body = new FormData();
      body.append("file", blob, filename);
      body.append("effect", effect);
      body.append("target", JSON.stringify(getTarget()));
      try {
        const res = await fetch("/api/voice", { method: "POST", headers: { "X-CSRFToken": csrf() }, body });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
        report(data, "Spoke through");
        const ok = Object.values(data.results || {}).find((r) => r.ok);
        status.textContent = ok ? `Said ${ok.seconds} s as ${effects.find((e) => e.id === effect)?.name || effect}` : "";
      } catch (err) {
        fail(err);
        status.textContent = "";
      } finally {
        sending = false;
        talk.classList.remove("busy");
        label.textContent = canRecord ? "Hold to talk" : "Microphone unavailable here";
      }
    }

    // ---- recording ----
    let stream = null, recorder = null, chunks = [], startedAt = 0, timer = null, limit = null;
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus", ""].find(
      (m) => !m || (window.MediaRecorder && MediaRecorder.isTypeSupported(m))
    );
    const ext = mime.includes("mp4") ? "m4a" : mime.includes("ogg") ? "ogg" : "webm";
    const tick = () => {
      const s = (Date.now() - startedAt) / 1000;
      status.textContent = `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")} — let go to send`;
    };
    async function start() {
      if (!canRecord || recorder || sending) return;
      label.textContent = "Listening…";
      try {
        stream = stream || (await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }));
      } catch (err) {
        label.textContent = "Hold to talk";
        toast(err.name === "NotAllowedError" ? "Microphone access was refused. Allow it in the browser's site settings." : `Couldn't open the microphone (${err.message || err.name})`, "bad");
        return;
      }
      chunks = [];
      recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      recorder.onstop = () => {
        const held = Date.now() - startedAt;
        const blob = new Blob(chunks, { type: recorder.mimeType || mime || "audio/webm" });
        recorder = null;
        clearInterval(timer);
        clearTimeout(limit);
        row.classList.remove("rec");
        talk.classList.remove("rec");
        talk.setAttribute("aria-pressed", "false");
        if (held < 350 || blob.size < 1000) {
          label.textContent = "Hold to talk";
          status.textContent = "Keep the button held while you talk.";
          return;
        }
        speak(blob, `voice.${ext}`);
      };
      recorder.start();
      startedAt = Date.now();
      row.classList.add("rec");
      talk.classList.add("rec");
      talk.setAttribute("aria-pressed", "true");
      tick();
      timer = setInterval(tick, 250);
      limit = setTimeout(stop, VOICE_MAX_MS);
    }
    function stop() {
      if (recorder && recorder.state !== "inactive") recorder.stop();
    }
    talk.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      talk.setPointerCapture(e.pointerId);
      start();
    });
    talk.addEventListener("pointerup", stop);
    talk.addEventListener("pointercancel", stop);
    talk.addEventListener("lostpointercapture", stop);
    talk.addEventListener("contextmenu", (e) => e.preventDefault());
    talk.addEventListener("keydown", (e) => {
      if ((e.key === " " || e.key === "Enter") && !e.repeat) (e.preventDefault(), start());
    });
    talk.addEventListener("keyup", (e) => (e.key === " " || e.key === "Enter") && stop());
    window.addEventListener("blur", stop);

    // ---- the always-available routes ----
    file.addEventListener("change", () => {
      const f = file.files[0];
      if (f) speak(f, f.name || `voice.${ext}`);
      file.value = "";
    });
    urlForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const btn = $("button", urlForm);
      btn.setAttribute("aria-busy", "true");
      api("/api/voice/url", { target: getTarget(), url: urlForm.url.value.trim(), effect })
        .then((r) => {
          report(r, "Playing on");
          urlForm.url.value = "";
        })
        .catch(fail)
        .finally(() => btn.removeAttribute("aria-busy"));
    });
    return { refresh: setSub };
  }

  // ---------- Themes ----------
  const themes = new Map((snapshot.themes || []).map((t) => [t.id, t]));
  const themeName = (id) => themes.get(id)?.name || id || "—";
  const themePicture = (id, size) => themes.get(id)?.[size || "small"] || null;

  // ---------- Transport ----------
  async function api(path, body, method) {
    const opts = { method: method || (body ? "POST" : "GET"), headers: { "X-CSRFToken": csrf() } };
    if (body) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  // ---------- Toasts ----------
  function toast(message, kind) {
    const host = document.getElementById("toasts");
    const el = document.createElement("div");
    el.className = "toast" + (kind ? ` ${kind}` : "");
    el.textContent = message;
    host.appendChild(el);
    while (host.children.length > 3) host.firstElementChild.remove();
    setTimeout(() => {
      el.classList.add("leaving");
      setTimeout(() => el.remove(), 300);
    }, kind === "bad" ? 5000 : 2600);
  }
  const fail = (err) => toast(err.message || String(err), "bad");

  // Summarise a fan-out {results: {id: {ok, error}}}: "Blinked 3 boards" or the failures.
  function report(data, done) {
    const results = Object.values(data.results || {});
    const failed = results.filter((r) => !r.ok);
    if (failed.length) toast([...new Set(failed.map((r) => r.error))].join(" — "), "bad");
    else if (done && results.length) toast(`${done} ${results.length === 1 ? "1 board" : `${results.length} boards`}`, "good");
    return data;
  }
  const sendState = (target, state, done) => api("/api/state", { target, state }).then((d) => report(d, done));
  const sendAction = (target, action, done) => api("/api/action", { target, ...action }).then((d) => report(d, done));

  // ---------- Formatting ----------
  const describe = (s) => {
    if (!s || !s.theme) return "No state yet";
    const mood = s.mood && s.mood !== "neutral" ? `, ${s.mood}` : "";
    return `${themeName(s.theme)}${mood}`;
  };
  const pct = (b) => `${Math.round(((b ?? 0) / 255) * 100)}%`;
  function uptime(secs) {
    if (secs == null) return "—";
    const m = Math.floor(secs / 60), h = Math.floor(m / 60), d = Math.floor(h / 24);
    if (d) return `${d}d ${h % 24}h`;
    if (h) return `${h}h ${m % 60}m`;
    return `${m}m`;
  }
  const targetCount = (target, devices, groups) => {
    if (target.all) return devices.length;
    if (target.group !== undefined) return (groups.find((g) => g.id === target.group)?.devices || []).length;
    return 1;
  };

  // ---------- Pictures: a board's eyes (live picture when it has one, else the theme) ----------
  // Keeps an <img> showing a board's live picture: fetch the next frame as soon as one arrives
  // (capped at ~12 fps), pausing while the tab is hidden. Returns a stop() function.
  function livePreview(img, deviceId) {
    let stopped = false;
    let timer;
    const next = () => {
      if (stopped) return;
      if (document.hidden) return (timer = setTimeout(next, 500));
      img.src = `/api/devices/${deviceId}/preview?t=${Date.now()}`;
    };
    img.onload = () => (timer = setTimeout(next, 80));
    img.onerror = () => (timer = setTimeout(next, 2000));
    next();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }

  // Fills a .eyes element for a device. `size`: "small" | "png" | "gif" (gif falls back to png).
  function showEyes(el, device, size, quiet) {
    const s = device.state || {};
    el.classList.toggle("blank", !s.theme && !device.preview);
    let img = $("img", el);
    if (!img) {
      img = document.createElement("img");
      img.alt = "";
      el.prepend(img);
    }
    if (device.preview && device.online) {
      if (!el.dataset.live) {
        el.dataset.live = device.device_id;
        img.classList.add("live");
        img.hidden = false;
        el._stop = livePreview(img, device.device_id);
      }
    } else {
      if (el._stop) {
        el._stop();
        el._stop = null;
        delete el.dataset.live;
        img.classList.remove("live");
      }
      const want = size === "gif" && !reducedMotion ? themePicture(s.theme, "gif") || themePicture(s.theme, "png") : themePicture(s.theme, size);
      if (want && img.getAttribute("src") !== want) img.src = want;
      if (!want) img.removeAttribute("src");
      img.hidden = !want;
      let note = $(".no-picture", el);
      if (!want && !note && !quiet) {
        note = document.createElement("p");
        note.className = "no-picture";
        note.textContent = s.theme ? `No picture for “${s.theme}”` : "Waiting for the board";
        el.appendChild(note);
      } else if (want && note) note.remove();
    }
    el.classList.toggle("off", s.on === false || !device.online);
    el.classList.toggle("dimmed", s.on !== false && device.online && (s.brightness ?? 255) < 90);
    let badge = $(".badge", el);
    const text = !device.online ? "Offline" : s.on === false ? "Off" : device.preview ? "Live" : "";
    if (text) {
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "badge";
        el.appendChild(badge);
      }
      badge.textContent = text;
      badge.className = `badge ${text.toLowerCase()}`;
    } else if (badge) badge.remove();
  }

  // ---------- Gaze pad: drag to steer; calls onMove(x, y) throttled and onEnd(x, y) ----------
  function gazePad(pad, { onMove, onEnd, hz = 15 }) {
    const puck = $(".puck", pad);
    let pos = { x: 0, y: 0 };
    let dragging = false;
    let dirty = false;
    let inFlight = false;

    function place(x, y) {
      const len = Math.hypot(x, y);
      if (len > 1) (x /= len), (y /= len);
      pos = { x, y };
      puck.style.left = `${(x + 1) * 50}%`;
      puck.style.top = `${(y + 1) * 50}%`;
      pad.dispatchEvent(new CustomEvent("gaze", { detail: pos }));
    }
    const fromEvent = (e) => {
      const r = pad.getBoundingClientRect();
      place(((e.clientX - r.left) / r.width) * 2 - 1, ((e.clientY - r.top) / r.height) * 2 - 1);
      dirty = true;
    };
    setInterval(() => {
      if (!dirty || inFlight) return;
      dirty = false;
      inFlight = true;
      Promise.resolve(dragging ? onMove(pos.x, pos.y) : onEnd(pos.x, pos.y)).finally(() => (inFlight = false));
    }, 1000 / hz);
    pad.addEventListener("pointerdown", (e) => {
      dragging = true;
      pad.classList.add("dragging");
      pad.setPointerCapture(e.pointerId);
      fromEvent(e);
    });
    pad.addEventListener("pointermove", (e) => dragging && fromEvent(e));
    const end = () => {
      if (!dragging) return;
      dragging = false;
      pad.classList.remove("dragging");
      dirty = true;
    };
    pad.addEventListener("pointerup", end);
    pad.addEventListener("pointercancel", end);
    // Keyboard: arrows nudge, Home recentres.
    pad.addEventListener("keydown", (e) => {
      const step = e.shiftKey ? 0.25 : 0.1;
      const k = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step], Home: null }[e.key];
      if (k === undefined) return;
      e.preventDefault();
      k ? place(pos.x + k[0], pos.y + k[1]) : place(0, 0);
      dirty = true;
    });
    place(0, 0);
    return { place, get pos() { return pos; }, reset: () => (place(0, 0), (dirty = false)) };
  }

  // ---------- Fleet picker for puppeteer / scenes ----------
  function targetChips(host, devices, groups, onChange, initial) {
    const chips = [{ label: "All boards", target: { all: true } }]
      .concat(groups.map((g) => ({ label: g.name, target: { group: g.id }, count: g.devices.length })))
      .concat(devices.map((d) => ({ label: d.name, target: { device: d.device_id }, online: d.online })));
    host.innerHTML = "";
    let current = initial || chips[0].target;
    const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    for (const c of chips) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.setAttribute("aria-pressed", String(same(c.target, current)));
      if (c.online !== undefined) b.innerHTML = `<span class="dot ${c.online ? "on" : "off"}"></span>`;
      b.append(document.createTextNode(c.label + (c.count !== undefined ? ` (${c.count})` : "")));
      b.addEventListener("click", () => {
        current = c.target;
        $$(".chip", host).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        onChange(current);
      });
      host.appendChild(b);
    }
    return () => current;
  }

  // Keep a "set the slider fill" CSS variable in sync (WebKit has no range-progress).
  const syncRange = (input) => {
    const min = +input.min || 0, max = +input.max || 100;
    input.style.setProperty("--fill", `${((input.value - min) / (max - min)) * 100}%`);
  };

  // ======================================================================
  // Fleet (home)
  // ======================================================================
  function fleet() {
    const grid = $("#fleet");
    const tpl = $("#card-tpl");
    const cards = new Map();
    let devices = snapshot.devices;

    function paint(card, d) {
      const s = d.state || {};
      card.classList.toggle("offline", !d.online);
      card.dataset.id = d.device_id;
      const link = $(".card-title a", card);
      link.textContent = d.name;
      link.href = `/boards/${d.device_id}/`;
      $(".eyes a", card).href = link.href;
      $(".eyes a", card).setAttribute("aria-label", `Open ${d.name}`);
      showEyes($(".eyes", card), d, "small");
      const dot = $(".dot", card);
      dot.className = `dot ${d.online ? "on" : "off"}`;
      $(".status-text", card).textContent = d.online ? (s.on === false ? "Off" : "On") : "Offline";
      $(".look", card).textContent = d.online ? describe(s) : d.host;
      const playing = $(".playing", card);
      playing.hidden = !(d.online && s.playing);
      if (s.playing) {
        playing.innerHTML = glyph("speaker") + `<span></span>`;
        $("span", playing).textContent = soundLabel(s.playing);
      }
      paintBattery($(".battery", card), d.online ? s.battery : null);
      const sw = $(".power input", card);
      if (document.activeElement !== sw) sw.checked = s.on !== false && d.online;
      sw.disabled = !d.online;
      const range = $("input[type=range]", card);
      if (document.activeElement !== range && s.brightness !== undefined) {
        range.value = s.brightness;
        syncRange(range);
        $(".range output", card).textContent = pct(s.brightness);
      }
    }

    function build(d) {
      const card = tpl.content.firstElementChild.cloneNode(true);
      const target = { device: d.device_id };
      const current = () => devices.find((x) => x.device_id === d.device_id) || d;
      const apply = (state, undo) =>
        sendState(target, state)
          .then((r) => {
            const res = r.results[d.device_id];
            if (res?.ok) {
              const dev = current();
              dev.state = res.state;
              dev.online = true;
              paint(card, dev);
            } else if (undo) undo();
          })
          .catch((err) => (fail(err), undo && undo()));
      $(".power input", card).addEventListener("change", (e) => {
        const on = e.target.checked;
        const dev = current();
        dev.state = { ...dev.state, on };
        paint(card, dev); // optimistic
        apply({ on }, () => ((dev.state.on = !on), paint(card, dev)));
      });
      const range = $("input[type=range]", card);
      range.addEventListener("input", () => (syncRange(range), ($(".range output", card).textContent = pct(+range.value))));
      range.addEventListener("change", () => apply({ brightness: +range.value }));
      $$("[data-action]", card).forEach((b) =>
        b.addEventListener("click", () => sendAction(target, { action: b.dataset.action }, ACTION_DONE[b.dataset.action]).catch(fail))
      );
      $(".puppet-link", card).href = `/puppeteer/?device=${d.device_id}`;
      return card;
    }

    function render(list) {
      devices = list;
      const online = list.filter((d) => d.online).length;
      $("#fleet-count").textContent = list.length ? `${list.length === 1 ? "1 board" : `${list.length} boards`}, ${online} online` : "";
      $("#empty").hidden = list.length > 0;
      $("#fleet-bar").hidden = list.length === 0;
      for (const d of list) {
        let card = cards.get(d.device_id);
        if (!card) {
          card = build(d);
          cards.set(d.device_id, card);
          grid.appendChild(card);
        }
        paint(card, d);
      }
      for (const [id, card] of cards) if (!list.some((d) => d.device_id === id)) (card.remove(), cards.delete(id));
    }

    const load = (refresh) => api("/api/devices" + (refresh ? "?refresh=1" : "")).then((r) => render(r.devices)).catch(fail);

    // Add by address and scan (both in the empty state and the add row).
    $$("form.add").forEach((form) =>
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        const host = form.host.value.trim();
        const btn = $("button[type=submit]", form);
        btn.setAttribute("aria-busy", "true");
        api("/api/devices", { host })
          .then((r) => {
            toast(`Added ${r.device.name}`, "good");
            form.reset();
            load(false);
          })
          .catch(fail)
          .finally(() => btn.removeAttribute("aria-busy"));
      })
    );
    $$("[data-scan]").forEach((btn) =>
      btn.addEventListener("click", () => {
        const label = btn.textContent;
        btn.setAttribute("aria-busy", "true");
        btn.textContent = "Scanning…";
        api("/api/scan", { seconds: 4 })
          .then((r) => {
            const n = r.devices.length;
            toast(n ? `Found ${n === 1 ? "1 board" : `${n} boards`}` : "No boards answered. Are they on this Wi-Fi?", n ? "good" : "bad");
            if (r.errors.length) toast(r.errors.join(" — "), "bad");
            load(false);
          })
          .catch(fail)
          .finally(() => {
            btn.removeAttribute("aria-busy");
            btn.textContent = label;
          });
      })
    );

    // Whole-fleet bar and scene tiles.
    const all = { all: true };
    $$("[data-all-state]").forEach((b) =>
      b.addEventListener("click", () => sendState(all, JSON.parse(b.dataset.allState), b.dataset.done).then(() => load(false)).catch(fail))
    );
    $$("[data-all-action]").forEach((b) =>
      b.addEventListener("click", () => sendAction(all, { action: b.dataset.allAction }, ACTION_DONE[b.dataset.allAction]).catch(fail))
    );
    const strip = $("#scene-strip");
    if (strip) {
      for (const s of snapshot.scenes) {
        const tile = document.createElement("button");
        tile.type = "button";
        tile.className = "scene-tile";
        tile.innerHTML = `<div class="eyes"></div><strong></strong><span class="muted"></span>`;
        $("strong", tile).textContent = s.name;
        $("span", tile).textContent = s.group_name ? s.group_name : "All boards";
        showEyes($(".eyes", tile), { state: { theme: s.state?.theme, on: true }, online: true }, "small", true);
        tile.addEventListener("click", () => {
          tile.setAttribute("aria-busy", "true");
          api(`/api/scenes/${s.id}/apply`, {})
            .then((r) => {
              const all = Object.values(r.results.state || r.results.action || {});
              const failed = all.filter((x) => !x.ok);
              failed.length ? toast([...new Set(failed.map((x) => x.error))].join(" — "), "bad") : toast(`Applied “${s.name}”`, "good");
              load(false);
            })
            .catch(fail)
            .finally(() => tile.removeAttribute("aria-busy"));
        });
        strip.appendChild(tile);
      }
    }

    render(devices);
    load(true);
    setInterval(() => !document.hidden && load(true), 15000);
  }

  // ======================================================================
  // Board
  // ======================================================================
  function board() {
    const id = snapshot.device;
    let device = snapshot.devices.find((d) => d.device_id === id);
    const target = { device: id };
    const hero = $("#hero");
    const nameEl = $("#board-name");

    function paint() {
      const s = device.state || {};
      document.title = `${device.name} · Spooky Eyes`;
      nameEl.textContent = device.name;
      $("#board-status .dot").className = `dot ${device.online ? "on" : "off"}`;
      $("#board-status .status-text").textContent = device.online ? `${s.on === false ? "Off" : "On"}, ${describe(s)}` : "Offline — last seen " + (device.last_seen ? new Date(device.last_seen).toLocaleString() : "never");
      showEyes(hero, device, "gif");
      const power = $("#power");
      if (document.activeElement !== power) power.checked = s.on !== false;
      const range = $("#brightness");
      if (document.activeElement !== range && s.brightness !== undefined) {
        range.value = s.brightness;
        syncRange(range);
        $("#brightness-out").textContent = pct(s.brightness);
      }
      $$("#tiles .tile").forEach((t) => t.setAttribute("aria-pressed", String(t.dataset.theme === s.theme)));
      $$("#moods button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mood === s.mood)));
      const idle = $("#idle");
      if (document.activeElement !== idle) idle.checked = s.autonomous !== false;
      const pupilAuto = $("#pupil-auto");
      const pupil = $("#pupil");
      if (document.activeElement !== pupilAuto) pupilAuto.checked = s.pupil == null;
      pupil.disabled = s.pupil == null;
      if (document.activeElement !== pupil && s.pupil != null) (pupil.value = Math.round(s.pupil * 100)), syncRange(pupil);
      if (s.pupil == null) (pupil.value = 50), syncRange(pupil);
      $("#diag-host").textContent = `${device.host}${device.port !== 80 ? ":" + device.port : ""}`;
      $("#diag-id").textContent = device.device_id.replace(/(..)(?=.)/g, "$1:");
      $("#diag-fw").textContent = device.fw || "—";
      $("#diag-model").textContent = device.model || "—";
      $("#diag-rssi").textContent = s.rssi ? `${s.rssi} dBm` : "—";
      $("#diag-fps").textContent = s.fps !== undefined ? `${s.fps} fps` : "—";
      $("#diag-uptime").textContent = uptime(s.uptime);
      $("#diag-battery").textContent = s.battery === undefined ? "—" : batteryText(s.battery);
      paintBattery($("#board-battery"), device.online ? s.battery : null);
      $("#name-note").hidden = !device.name_is_local;
      $$("[data-needs-online]").forEach((el) => el.classList.toggle("hidden", !device.online));
      paintSound(s);
    }

    // ---- Sound (only when the firmware reports a speaker) ----
    const sound = $("#sound");
    const features = () => device.features || {};
    let tiles = null;
    let soundsListing = device.sounds || {};
    const volume = $("#volume");
    const sens = $("#sensitivity");
    const listen = $("#listen");
    const meter = $("#meter");
    const threshold = (sensitivity) => -18 - 0.4 * (sensitivity ?? 50); // dBFS, same curve as the firmware
    const dbToPct = (db) => Math.max(0, Math.min(100, ((db + 90) / 90) * 100));

    function paintSound(s) {
      const f = features();
      sound.hidden = !f.speaker;
      if (!f.speaker) return;
      if (document.activeElement !== volume && s.volume !== undefined) {
        volume.value = s.volume;
        syncRange(volume);
        $("#volume-out").textContent = `${s.volume}%`;
      }
      const stop = $("#sound-stop");
      stop.disabled = !s.playing;
      const now = $("#now-playing");
      now.hidden = !s.playing;
      if (s.playing) {
        now.innerHTML = `<span class="wave" aria-hidden="true"><i></i><i></i><i></i><i></i></span><span></span>`;
        $("span:last-child", now).textContent = `Playing ${soundLabel(s.playing)}`;
      }
      tiles?.setPlaying(s.playing);
      paintPairing(s);
      $("#listen-block").hidden = !f.microphone;
      if (f.microphone) {
        if (document.activeElement !== listen) listen.checked = !!s.listen;
        if (document.activeElement !== sens && s.sensitivity !== undefined) {
          sens.value = s.sensitivity;
          syncRange(sens);
          $("#sensitivity-out").textContent = `${s.sensitivity}`;
        }
        paintLevel(s);
      }
      const free = soundsListing.free_bytes;
      $("#sound-storage").textContent = free !== undefined ? `${(free / 1e6).toFixed(1)} MB free on the board, about ${Math.floor(free / 32000)} s of clips` : "";
    }
    // Theme sound: the effect paired with the current theme (firmware that reports theme_sounds only).
    const pairing = $("#pairing");
    const pairSelect = $("#theme-sound");
    const pairSwitch = $("#theme-sounds");
    function paintPairing(s) {
      pairing.hidden = s.theme_sounds === undefined;
      if (pairing.hidden) return;
      const names = (soundsListing.builtin || []).concat((soundsListing.clips || []).map((c) => c.name));
      const current = s.theme_sound || "";
      if (current && !names.includes(current)) names.push(current);
      const want = JSON.stringify(names);
      if (pairSelect.dataset.names !== want) {
        pairSelect.dataset.names = want;
        pairSelect.innerHTML = "";
        pairSelect.appendChild(new Option("No sound", ""));
        for (const n of names) pairSelect.appendChild(new Option(soundLabel(n) + ((soundsListing.builtin || []).includes(n) ? "" : " (clip)"), n));
      }
      if (document.activeElement !== pairSelect) pairSelect.value = current;
      if (document.activeElement !== pairSwitch) pairSwitch.checked = !!s.theme_sounds;
      pairing.classList.toggle("off", !s.theme_sounds);
      const theme = themeName(s.theme);
      $("#pairing-label").textContent = `Startle sound for ${theme}`;
      const dflt = (device.themes || []).find((t) => t.id === s.theme)?.sound;
      const change = dflt !== undefined && (dflt || "") !== current ? ` ${theme} comes with ${dflt ? soundLabel(dflt) : "no sound"}.` : "";
      $("#pairing-note").textContent = `Plays whenever the eyes startle — a loud noise, the Startle button, a scene.${change}`;
    }
    pairSelect.addEventListener("change", () => optimistic({ theme_sound: pairSelect.value || null }));
    pairSwitch.addEventListener("change", (e) => optimistic({ theme_sounds: e.target.checked }));

    function paintLevel(s) {
      const db = typeof s.sound_level === "number" ? s.sound_level : -90;
      const th = threshold(s.sensitivity);
      $(".meter-fill", meter).style.width = `${dbToPct(db)}%`;
      $(".meter-tick", meter).style.left = `${dbToPct(th)}%`;
      $(".meter-out", meter).textContent = `${Math.round(db)} dB`;
      meter.setAttribute("aria-valuenow", String(Math.round(db)));
      meter.classList.toggle("loud", s.listen && db > th);
      meter.classList.toggle("quiet", db <= -80);
      meter.classList.toggle("off", !s.listen);
    }
    function buildTiles() {
      tiles = soundTiles($("#sound-board"), {
        builtin: soundsListing.builtin || [],
        clips: soundsListing.clips || [],
        onPlay: (name) =>
          sendAction(target, { action: "sound", name })
            .then(() => {
              device.state = { ...device.state, playing: name };
              paintSound(device.state);
            })
            .catch(fail),
        onDelete: (name) => {
          if (!confirm(`Delete the clip “${name}” from ${device.name}?`)) return;
          api(`/api/devices/${id}/sounds/${encodeURIComponent(name)}`, null, "DELETE")
            .then((r) => {
              soundsListing = r.sounds;
              device.sounds = r.sounds;
              buildTiles();
              paintSound(device.state || {});
              toast(`Deleted “${name}”`, "good");
            })
            .catch(fail);
        },
        onAdd: () => $("#clip-file").click(),
      });
      tiles.setPlaying(device.state?.playing);
      $("#clips-edit").hidden = !(soundsListing.clips || []).length;
      if (!(soundsListing.clips || []).length) setEditing(false);
    }
    const setEditing = (on) => {
      $("#clips-edit").setAttribute("aria-pressed", String(on));
      $("#clips-edit").textContent = on ? "Done" : "Edit clips";
      $("#sound-board").classList.toggle("editing", on);
    };
    $("#clips-edit").addEventListener("click", () => setEditing($("#clips-edit").getAttribute("aria-pressed") !== "true"));
    $("#sound-stop").addEventListener("click", () =>
      sendAction(target, { action: "stop_sound" }, "Silenced")
        .then(() => ((device.state = { ...device.state, playing: null }), paintSound(device.state)))
        .catch(fail)
    );
    volume.addEventListener("input", () => (syncRange(volume), ($("#volume-out").textContent = `${volume.value}%`)));
    volume.addEventListener("change", () => optimistic({ volume: +volume.value }));
    listen.addEventListener("change", (e) => optimistic({ listen: e.target.checked }));
    sens.addEventListener("input", () => {
      syncRange(sens);
      $("#sensitivity-out").textContent = sens.value;
      paintLevel({ ...device.state, sensitivity: +sens.value });
    });
    sens.addEventListener("change", () => optimistic({ sensitivity: +sens.value }));

    // Upload a clip: pick a file, confirm the name, send it (the server converts it).
    const clipFile = $("#clip-file");
    const clipDialog = $("#clip-dialog");
    const clipForm = $("form", clipDialog);
    const suggestName = (filename) =>
      (filename || "clip")
        .replace(/\.[^.]+$/, "")
        .toLowerCase()
        .replace(/[^a-z0-9_-]+/g, "_")
        .replace(/^[_-]+|[_-]+$/g, "")
        .slice(0, snapshot.sound_name_max || 24) || "clip";
    clipFile.addEventListener("change", () => {
      const file = clipFile.files[0];
      if (!file) return;
      $("#clip-file-note").textContent = `${file.name} (${(file.size / 1e6).toFixed(1)} MB)`;
      clipForm.name.value = suggestName(file.name);
      clipDialog.showModal();
      clipForm.name.select();
    });
    clipForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const file = clipFile.files[0];
      if (!file) return clipDialog.close();
      const name = clipForm.name.value.trim();
      const body = new FormData();
      body.append("name", name);
      body.append("file", file, file.name);
      const btn = $("button[type=submit]", clipForm);
      btn.setAttribute("aria-busy", "true");
      btn.textContent = "Converting and uploading…";
      fetch(`/api/devices/${id}/sounds`, { method: "POST", headers: { "X-CSRFToken": csrf() }, body })
        .then(async (res) => {
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
          soundsListing = data.sounds;
          device.sounds = data.sounds;
          buildTiles();
          paintSound(device.state || {});
          clipDialog.close();
          toast(`Added “${data.name}”`, "good");
        })
        .catch(fail)
        .finally(() => {
          btn.removeAttribute("aria-busy");
          btn.textContent = "Upload";
          clipFile.value = "";
        });
    });

    // The mic meter polls the board's live state a few times a second, but only while on screen.
    let levelTimer = null;
    const pollLevel = () => {
      if (document.hidden || !features().microphone || !device.online) return;
      api(`/api/devices/${id}/state`)
        .then((r) => {
          device.state = { ...device.state, ...r.state };
          paintLevel(device.state);
          const stop = $("#sound-stop");
          stop.disabled = !device.state.playing;
          $("#now-playing").hidden = !device.state.playing;
          if (device.state.playing) $("#now-playing span:last-child").textContent = `Playing ${soundLabel(device.state.playing)}`;
          tiles?.setPlaying(device.state.playing);
          paintBattery($("#board-battery"), device.state.battery);
        })
        .catch(() => {});
    };
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(
        (entries) => {
          const visible = entries.some((e) => e.isIntersecting);
          clearInterval(levelTimer);
          levelTimer = visible ? setInterval(pollLevel, 350) : null;
          if (visible) pollLevel();
        },
        { threshold: 0.05 }
      ).observe($("#listen-block"));
    }
    if (features().speaker) {
      buildTiles();
      api(`/api/devices/${id}/sounds`)
        .then((r) => ((soundsListing = r.sounds), (device.sounds = r.sounds), buildTiles(), paintSound(device.state || {})))
        .catch(() => {});
      $("#voice").hidden = false;
      voiceControl($("#voice"), { getTarget: () => target, targetText: () => device.name });
    }

    const apply = (state, undo) =>
      sendState(target, state)
        .then((r) => {
          const res = r.results[id];
          if (res?.ok) {
            device.state = res.state;
            device.online = true;
          } else if (undo) undo();
          paint();
        })
        .catch((err) => (fail(err), undo && undo(), paint()));
    const optimistic = (patch) => {
      const before = { ...device.state };
      device.state = { ...device.state, ...patch };
      paint();
      apply(patch, () => (device.state = before));
    };

    $("#power").addEventListener("change", (e) => optimistic({ on: e.target.checked }));
    const range = $("#brightness");
    range.addEventListener("input", () => (syncRange(range), ($("#brightness-out").textContent = pct(+range.value))));
    range.addEventListener("change", () => optimistic({ brightness: +range.value }));
    $$("#tiles .tile").forEach((t) =>
      t.addEventListener("click", () => {
        t.setAttribute("aria-busy", "true");
        const before = device.state?.theme;
        device.state = { ...device.state, theme: t.dataset.theme };
        paint();
        apply({ theme: t.dataset.theme }, () => (device.state.theme = before)).finally(() => t.removeAttribute("aria-busy"));
      })
    );
    // Category chips filter the gallery.
    $$("#categories .chip").forEach((c) =>
      c.addEventListener("click", () => {
        $$("#categories .chip").forEach((x) => x.setAttribute("aria-pressed", String(x === c)));
        const cat = c.dataset.category;
        $$("#tiles .tile").forEach((t) => (t.hidden = cat !== "all" && t.dataset.category !== cat));
      })
    );
    $$("#moods button").forEach((b) => b.addEventListener("click", () => optimistic({ mood: b.dataset.mood })));
    $("#idle").addEventListener("change", (e) => optimistic({ autonomous: e.target.checked }));
    $("#pupil-auto").addEventListener("change", (e) => optimistic({ pupil: e.target.checked ? null : +$("#pupil").value / 100 }));
    const pupil = $("#pupil");
    pupil.addEventListener("input", () => syncRange(pupil));
    pupil.addEventListener("change", () => optimistic({ pupil: +pupil.value / 100 }));
    $$("[data-action]").forEach((b) =>
      b.addEventListener("click", () => sendAction(target, { action: b.dataset.action }, ACTION_DONE[b.dataset.action]).catch(fail))
    );

    // Gaze pad
    const padEl = $("#pad");
    const readout = $("#readout");
    const hold = $("#hold");
    const look = (x, y, duration) => sendAction(target, { action: "look", x: +x.toFixed(3), y: +y.toFixed(3), duration }).catch(fail);
    const pad = gazePad(padEl, {
      onMove: (x, y) => look(x, y, 1.5),
      onEnd: (x, y) => look(x, y, hold.checked ? 0 : 1.5),
    });
    padEl.addEventListener("gaze", (e) => (readout.textContent = `x ${e.detail.x.toFixed(2)}  y ${e.detail.y.toFixed(2)}`));
    $("#release").addEventListener("click", () => {
      pad.reset();
      sendAction(target, { action: "release" }, "Released").catch(fail);
    });

    // Rename
    const dialog = $("#rename-dialog");
    const form = $("form", dialog);
    $("#rename").addEventListener("click", () => {
      form.name.value = device.name;
      dialog.showModal();
      form.name.select();
    });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const name = form.name.value.trim();
      if (!name) return;
      $("button[type=submit]", form).setAttribute("aria-busy", "true");
      api(`/api/devices/${id}`, { name }, "PATCH")
        .then((r) => {
          device = { ...device, ...r.device };
          paint();
          dialog.close();
          toast(r.on_board ? `Renamed to “${name}” on the board` : `Renamed to “${name}” — ${r.note}`, r.on_board ? "good" : undefined);
        })
        .catch(fail)
        .finally(() => $("button[type=submit]", form).removeAttribute("aria-busy"));
    });

    // Forget
    $("#forget").addEventListener("click", () => {
      if (!confirm(`Forget ${device.name}? You can add it again by scanning.`)) return;
      api(`/api/devices/${id}`, null, "DELETE")
        .then(() => (location.href = "/"))
        .catch(fail);
    });

    // Save as scene
    $("#save-scene").addEventListener("click", () => {
      const s = device.state || {};
      const state = { theme: s.theme, mood: s.mood, brightness: s.brightness, on: s.on !== false, autonomous: s.autonomous !== false };
      sessionStorage.setItem("se-scene-draft", JSON.stringify({ name: `${device.name} look`, state }));
      location.href = "/scenes/?new=1";
    });

    const poll = () =>
      api(`/api/devices/${id}?refresh=1`)
        .then((r) => {
          device = r.device;
          paint();
        })
        .catch(fail);
    paint();
    poll();
    setInterval(() => !document.hidden && poll(), 6000);
  }

  // ======================================================================
  // Puppeteer
  // ======================================================================
  function puppeteer() {
    const devices = snapshot.devices;
    const groups = snapshot.groups;
    const params = new URLSearchParams(location.search);
    const initial = params.get("device") ? { device: params.get("device") } : params.get("group") ? { group: +params.get("group") } : null;
    const live = $("#live");
    let stops = [];

    function showLive(target) {
      stops.forEach((s) => s());
      stops = [];
      live.innerHTML = "";
      const ids = target.all
        ? devices.map((d) => d.device_id)
        : target.group !== undefined
        ? groups.find((g) => g.id === target.group)?.devices || []
        : [target.device];
      const shown = devices.filter((d) => ids.includes(d.device_id) && d.preview && d.online);
      live.hidden = !shown.length;
      for (const d of shown) {
        const fig = document.createElement("figure");
        fig.innerHTML = `<div class="eyes"></div><figcaption></figcaption>`;
        $("figcaption", fig).textContent = d.name;
        live.appendChild(fig);
        showEyes($(".eyes", fig), d, "small");
        stops.push(() => $(".eyes", fig)._stop?.());
      }
      const n = targetCount(target, devices, groups);
      $("#target-count").textContent = n === 1 ? "1 board" : `${n} boards`;
      showSounds(target, ids);
    }

    // Sound strip: the target's built-ins + clips; for a group, only what every speaker-board shares.
    const strip = $("#sound-strip");
    let voice = null;
    const voiceTargetText = () => {
      const t = getTarget();
      const n = devices.filter((d) => d.features?.speaker && (t.all || (t.group !== undefined ? groups.find((g) => g.id === t.group)?.devices || [] : [t.device]).includes(d.device_id))).length;
      return t.device ? devices.find((d) => d.device_id === t.device)?.name || "the board" : n === 1 ? "the one board with a speaker" : `${n} boards`;
    };
    function showSounds(target, ids) {
      const able = devices.filter((d) => ids.includes(d.device_id) && d.features?.speaker);
      $("#sound-strip-wrap").hidden = !able.length;
      if (!able.length) return;
      if (!voice) voice = voiceControl($("#voice"), { getTarget: () => getTarget(), targetText: voiceTargetText, big: true });
      else voice.refresh();
      let builtin = null, clips = null;
      for (const d of able) {
        const b = new Set(d.sounds?.builtin || []), c = new Set((d.sounds?.clips || []).map((x) => x.name));
        builtin = builtin ? new Set([...builtin].filter((x) => b.has(x))) : b;
        clips = clips ? new Set([...clips].filter((x) => c.has(x))) : c;
      }
      const order = Object.keys(SOUND_LABELS);
      const names = [...builtin].sort((a, b) => (order.indexOf(a) + 99) % 99 - (order.indexOf(b) + 99) % 99).concat([...clips].sort());
      const n = targetCount(target, devices, groups);
      $("#sound-strip-note").textContent = able.length < n ? `${able.length} of ${n} boards have a speaker` : "";
      strip.innerHTML = "";
      const stop = document.createElement("button");
      stop.type = "button";
      stop.className = "chip stop";
      stop.innerHTML = glyph("stop") + "Stop";
      stop.addEventListener("click", () => sendAction(getTarget(), { action: "stop_sound" }, "Silenced").catch(fail));
      strip.appendChild(stop);
      for (const name of names) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "chip";
        chip.dataset.sound = name;
        chip.setAttribute("aria-pressed", "false");
        chip.innerHTML = glyph(builtin.has(name) ? name : "clip");
        chip.append(document.createTextNode(soundLabel(name)));
        chip.addEventListener("click", () => {
          $$(".chip[data-sound]", strip).forEach((c) => c.setAttribute("aria-pressed", String(c === chip)));
          sendAction(getTarget(), { action: "sound", name }, `${soundLabel(name)} on`)
            .catch(fail)
            .finally(() => setTimeout(() => chip.setAttribute("aria-pressed", "false"), 1800));
        });
        strip.appendChild(chip);
      }
    }
    let getTarget = () => ({ all: true });
    getTarget = targetChips($("#targets"), devices, groups, showLive, initial);
    showLive(getTarget());

    const padEl = $("#pad");
    const hold = $("#hold");
    const look = (x, y, duration) => sendAction(getTarget(), { action: "look", x: +x.toFixed(3), y: +y.toFixed(3), duration }).catch(fail);
    const pad = gazePad(padEl, { onMove: (x, y) => look(x, y, 1.5), onEnd: (x, y) => look(x, y, hold.checked ? 0 : 1.5) });
    padEl.addEventListener("gaze", (e) => ($("#readout").textContent = `x ${e.detail.x.toFixed(2)}  y ${e.detail.y.toFixed(2)}`));
    $$("[data-action]").forEach((b) =>
      b.addEventListener("click", () => {
        if (b.dataset.action === "release") pad.reset();
        sendAction(getTarget(), { action: b.dataset.action }, ACTION_DONE[b.dataset.action]).catch(fail);
      })
    );
    $$("[data-mood]").forEach((b) => b.addEventListener("click", () => sendState(getTarget(), { mood: b.dataset.mood }, `${MOOD_LABELS[b.dataset.mood]}:`).catch(fail)));
  }

  // ======================================================================
  // Scenes & groups
  // ======================================================================
  function scenes() {
    let { scenes: list, groups, devices } = snapshot;
    const sceneList = $("#scene-list");
    const groupList = $("#group-list");
    const sceneDialog = $("#scene-dialog");
    const sceneForm = $("form", sceneDialog);
    const groupDialog = $("#group-dialog");
    const groupForm = $("form", groupDialog);
    const deviceName = (id) => devices.find((d) => d.device_id === id)?.name || id;

    const applyResults = (r, name) => {
      const all = Object.values(r.results.state || {}).concat(Object.values(r.results.action || {}));
      const failed = all.filter((x) => !x.ok);
      failed.length ? toast([...new Set(failed.map((x) => x.error))].join(" — "), "bad") : toast(all.length ? `Applied “${name}”` : `“${name}” has no boards to apply to`, all.length ? "good" : "bad");
    };

    function whatText(s) {
      const parts = [];
      const st = s.state || {};
      if (st.theme) parts.push(themeName(st.theme));
      if (st.mood) parts.push(MOOD_LABELS[st.mood] || st.mood);
      if (st.on === false) parts.push("off");
      else if (st.brightness !== undefined) parts.push(`${pct(st.brightness)} bright`);
      if (st.autonomous === false) parts.push("idle off");
      if (st.volume !== undefined) parts.push(`volume ${st.volume}%`);
      if (st.listen !== undefined) parts.push(st.listen ? "reacts to noise" : "ignores noise");
      if (s.action?.action === "look") parts.push(`look ${s.action.x >= 0 ? "right" : "left"}${s.action.duration ? ` ${s.action.duration}s` : ""}`);
      else if (s.action?.action === "sound") parts.push(`play ${soundLabel(s.action.name)}`);
      else if (s.action?.action) parts.push((ACTION_LABELS[s.action.action] || s.action.action).toLowerCase());
      return parts.join(", ") || "Nothing set";
    }

    function renderScenes() {
      sceneList.innerHTML = "";
      $("#scenes-empty").hidden = list.length > 0;
      for (const s of list) {
        const card = document.createElement("article");
        card.className = "scene-card";
        card.innerHTML = `<div class="eyes"></div><div class="body"><strong></strong><span class="what"></span><div class="row"><button type="button" class="btn btn-primary apply">Apply</button><button type="button" class="btn btn-ghost edit">Edit</button></div></div>`;
        $("strong", card).textContent = s.name;
        $(".what", card).textContent = `${s.group_name || "All boards"} — ${whatText(s)}`;
        showEyes($(".eyes", card), { state: { theme: s.state?.theme, on: true }, online: true }, "small", true);
        $(".apply", card).addEventListener("click", (e) => {
          e.target.setAttribute("aria-busy", "true");
          api(`/api/scenes/${s.id}/apply`, {})
            .then((r) => applyResults(r, s.name))
            .catch(fail)
            .finally(() => e.target.removeAttribute("aria-busy"));
        });
        $(".edit", card).addEventListener("click", () => openScene(s));
        sceneList.appendChild(card);
      }
    }

    function renderGroups() {
      groupList.innerHTML = "";
      $("#groups-empty").hidden = groups.length > 0;
      for (const g of groups) {
        const card = document.createElement("article");
        card.className = "group-card";
        card.innerHTML = `<strong></strong><div class="members"></div><div class="row"><a class="btn btn-ghost" href="/puppeteer/?group=${g.id}">Puppeteer</a><button type="button" class="btn btn-ghost edit">Edit</button></div>`;
        $("strong", card).textContent = g.name;
        const members = $(".members", card);
        if (!g.devices.length) members.innerHTML = `<span>No boards yet</span>`;
        for (const id of g.devices) {
          const m = document.createElement("span");
          m.textContent = deviceName(id);
          members.appendChild(m);
        }
        $(".edit", card).addEventListener("click", () => openGroup(g));
        groupList.appendChild(card);
      }
      // Group options in the scene form.
      const sel = sceneForm.group;
      sel.innerHTML = `<option value="">All boards</option>`;
      for (const g of groups) sel.appendChild(new Option(`${g.name} (${g.devices.length})`, g.id));
    }

    // ---- Scene editor ----
    let editing = null;
    let lookPad;
    const themeTiles = $$("#scene-themes .tile");
    const pickTheme = (id) => {
      sceneForm.theme.value = id || "";
      themeTiles.forEach((t) => t.setAttribute("aria-pressed", String(t.dataset.theme === id)));
    };
    themeTiles.forEach((t) => t.addEventListener("click", () => pickTheme(sceneForm.theme.value === t.dataset.theme ? "" : t.dataset.theme)));
    $$("#scene-moods button").forEach((b) =>
      b.addEventListener("click", () => {
        const v = sceneForm.mood.value === b.dataset.mood ? "" : b.dataset.mood;
        sceneForm.mood.value = v;
        $$("#scene-moods button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.mood === v)));
      })
    );
    const briRange = sceneForm.brightness;
    briRange.addEventListener("input", () => (syncRange(briRange), ($("#scene-bri-out").textContent = pct(+briRange.value))));
    sceneForm.set_brightness.addEventListener("change", () => (briRange.disabled = !sceneForm.set_brightness.checked));
    sceneForm.set_look.addEventListener("change", () => ($("#look-fields").hidden = !sceneForm.set_look.checked));
    const volRange = sceneForm.volume; // only when some board has a speaker
    if (volRange) {
      volRange.addEventListener("input", () => (syncRange(volRange), ($("#scene-vol-out").textContent = `${volRange.value}%`)));
      sceneForm.set_volume.addEventListener("change", () => (volRange.disabled = !sceneForm.set_volume.checked));
      // Friendly names for the built-in sounds in the "Then" list.
      $$("#after-sounds option").forEach((o) => {
        const name = o.value.slice("sound:".length);
        if (SOUND_LABELS[name]) o.textContent = SOUND_LABELS[name];
      });
    }
    // A saved sound the list doesn't know yet (clip deleted or on another board) still shows up when editing.
    const ensureAfterOption = (value, label) => {
      if (!value || [...sceneForm.after.options].some((o) => o.value === value)) return;
      sceneForm.after.appendChild(new Option(label, value));
    };

    function openScene(s, draft) {
      editing = s;
      $("h2", sceneDialog).textContent = s ? "Edit scene" : "New scene";
      $("#scene-delete").hidden = !s;
      const st = (s || draft)?.state || {};
      sceneForm.reset();
      sceneForm.name.value = (s || draft)?.name || "";
      sceneForm.group.value = s?.group || "";
      pickTheme(st.theme || "");
      sceneForm.mood.value = st.mood || "";
      $$("#scene-moods button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.mood === (st.mood || ""))));
      sceneForm.power.value = st.on === undefined ? "" : st.on ? "on" : "off";
      sceneForm.idle.value = st.autonomous === undefined ? "" : st.autonomous ? "on" : "off";
      sceneForm.set_brightness.checked = st.brightness !== undefined;
      briRange.disabled = st.brightness === undefined;
      briRange.value = st.brightness ?? 200;
      syncRange(briRange);
      $("#scene-bri-out").textContent = pct(+briRange.value);
      if (volRange) {
        sceneForm.set_volume.checked = st.volume !== undefined;
        volRange.disabled = st.volume === undefined;
        volRange.value = st.volume ?? 70;
        syncRange(volRange);
        $("#scene-vol-out").textContent = `${volRange.value}%`;
      }
      const a = s?.action;
      sceneForm.set_look.checked = a?.action === "look";
      $("#look-fields").hidden = a?.action !== "look";
      sceneForm.duration.value = a?.action === "look" ? a.duration : 5;
      const afterValue = !a || a.action === "look" ? "" : a.action === "sound" ? `sound:${a.name}` : a.action;
      ensureAfterOption(afterValue, a?.action === "sound" ? `${soundLabel(a.name)} (clip)` : ACTION_LABELS[a?.action] || afterValue);
      sceneForm.after.value = afterValue;
      if (!lookPad) {
        lookPad = gazePad($("#look-pad"), { onMove: () => {}, onEnd: () => {} });
        $("#look-pad").addEventListener("gaze", (e) => ($("#look-readout").textContent = `x ${e.detail.x.toFixed(2)}  y ${e.detail.y.toFixed(2)}`));
      }
      lookPad.place(a?.action === "look" ? a.x : 0, a?.action === "look" ? a.y : 0);
      sceneDialog.showModal();
      sceneForm.name.focus();
    }

    sceneForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const f = sceneForm;
      const state = {};
      if (f.theme.value) state.theme = f.theme.value;
      if (f.mood.value) state.mood = f.mood.value;
      if (f.power.value) state.on = f.power.value === "on";
      if (f.idle.value) state.autonomous = f.idle.value === "on";
      if (f.set_brightness.checked) state.brightness = +f.brightness.value;
      if (f.volume && f.set_volume.checked) state.volume = +f.volume.value;
      let action = null;
      if (f.set_look.checked) action = { action: "look", x: +lookPad.pos.x.toFixed(2), y: +lookPad.pos.y.toFixed(2), duration: +f.duration.value || 0 };
      else if (f.after.value.startsWith("sound:")) action = { action: "sound", name: f.after.value.slice("sound:".length) };
      else if (f.after.value) action = { action: f.after.value };
      const body = { name: f.name.value, group: f.group.value || null, state, action };
      const req = editing ? api(`/api/scenes/${editing.id}`, body, "PUT") : api("/api/scenes", body);
      $("button[type=submit]", f).setAttribute("aria-busy", "true");
      req
        .then((r) => {
          if (editing) list = list.map((x) => (x.id === r.scene.id ? r.scene : x));
          else list = list.concat([r.scene]);
          list.sort((a, b) => a.name.localeCompare(b.name));
          renderScenes();
          sceneDialog.close();
          toast(editing ? `Saved “${r.scene.name}”` : `Created “${r.scene.name}”`, "good");
        })
        .catch(fail)
        .finally(() => $("button[type=submit]", f).removeAttribute("aria-busy"));
    });
    $("#scene-delete").addEventListener("click", () => {
      if (!editing || !confirm(`Delete scene “${editing.name}”?`)) return;
      api(`/api/scenes/${editing.id}`, null, "DELETE")
        .then(() => {
          list = list.filter((x) => x.id !== editing.id);
          renderScenes();
          sceneDialog.close();
          toast("Scene deleted");
        })
        .catch(fail);
    });
    $("#new-scene").addEventListener("click", () => openScene(null));
    $("#scenes-empty button")?.addEventListener("click", () => openScene(null));

    // ---- Group editor ----
    let editingGroup = null;
    function openGroup(g) {
      editingGroup = g;
      $("h2", groupDialog).textContent = g ? "Edit group" : "New group";
      $("#group-delete").hidden = !g;
      groupForm.reset();
      groupForm.name.value = g?.name || "";
      const host = $("#group-members");
      host.innerHTML = "";
      if (!devices.length) host.innerHTML = `<p class="muted">No boards yet — add some on the Boards page first.</p>`;
      for (const d of devices) {
        const label = document.createElement("label");
        label.className = "check";
        label.innerHTML = `<input type="checkbox" name="devices"><span class="dot"></span><span></span>`;
        $("input", label).value = d.device_id;
        $("input", label).checked = !!g?.devices.includes(d.device_id);
        $(".dot", label).classList.add(d.online ? "on" : "off");
        $("span:last-child", label).textContent = d.name;
        host.appendChild(label);
      }
      groupDialog.showModal();
      groupForm.name.focus();
    }
    groupForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const body = { name: groupForm.name.value, devices: $$("input[name=devices]:checked", groupForm).map((i) => i.value) };
      const req = editingGroup ? api(`/api/groups/${editingGroup.id}`, body, "PUT") : api("/api/groups", body);
      req
        .then((r) => {
          groups = editingGroup ? groups.map((x) => (x.id === r.group.id ? r.group : x)) : groups.concat([r.group]);
          groups.sort((a, b) => a.name.localeCompare(b.name));
          list = list.map((s) => (s.group === r.group.id ? { ...s, group_name: r.group.name } : s));
          renderGroups();
          renderScenes();
          groupDialog.close();
          toast(editingGroup ? `Saved “${r.group.name}”` : `Created “${r.group.name}”`, "good");
        })
        .catch(fail);
    });
    $("#group-delete").addEventListener("click", () => {
      if (!editingGroup || !confirm(`Delete group “${editingGroup.name}”? Scenes aimed at it will target all boards.`)) return;
      api(`/api/groups/${editingGroup.id}`, null, "DELETE")
        .then(() => {
          groups = groups.filter((x) => x.id !== editingGroup.id);
          list = list.map((s) => (s.group === editingGroup.id ? { ...s, group: null, group_name: null } : s));
          renderGroups();
          renderScenes();
          groupDialog.close();
          toast("Group deleted");
        })
        .catch(fail);
    });
    $("#new-group").addEventListener("click", () => openGroup(null));
    $("#groups-empty button")?.addEventListener("click", () => openGroup(null));

    // Close buttons for both dialogs.
    $$("dialog [data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));

    renderGroups();
    renderScenes();
    // "Save this look" from a board page lands here with a draft.
    if (new URLSearchParams(location.search).get("new")) {
      let draft = null;
      try {
        draft = JSON.parse(sessionStorage.getItem("se-scene-draft") || "null");
        sessionStorage.removeItem("se-scene-draft");
      } catch (_) {}
      openScene(null, draft);
    }
  }

  return { api, toast, fleet, board, puppeteer, scenes, snapshot };
})();
