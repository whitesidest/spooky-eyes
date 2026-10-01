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
  const ACTION_LABELS = { blink: "Blink", wink_left: "Wink left", wink_right: "Wink right", startle: "Startle", roll: "Eye roll", release: "Release" };
  const ACTION_DONE = { blink: "Blinked", wink_left: "Winked left", wink_right: "Winked right", startle: "Startled", roll: "Rolled", release: "Released" };

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
      $("#name-note").hidden = !device.name_is_local;
      $$("[data-needs-online]").forEach((el) => el.classList.toggle("hidden", !device.online));
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
    }
    const getTarget = targetChips($("#targets"), devices, groups, showLive, initial);
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
      if (s.action?.action === "look") parts.push(`look ${s.action.x >= 0 ? "right" : "left"}${s.action.duration ? ` ${s.action.duration}s` : ""}`);
      else if (s.action?.action) parts.push(ACTION_LABELS[s.action.action].toLowerCase());
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
      const a = s?.action;
      sceneForm.set_look.checked = a?.action === "look";
      $("#look-fields").hidden = a?.action !== "look";
      sceneForm.duration.value = a?.action === "look" ? a.duration : 5;
      sceneForm.after.value = a && a.action !== "look" ? a.action : "";
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
      let action = null;
      if (f.set_look.checked) action = { action: "look", x: +lookPad.pos.x.toFixed(2), y: +lookPad.pos.y.toFixed(2), duration: +f.duration.value || 0 };
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
