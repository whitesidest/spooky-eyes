// Spooky Eyes web controller — vanilla JS, no build step.
const SpookyEyes = (() => {
  const csrf = () => document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";

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

  let toastTimer;
  function toast(msg, bad) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.className = "show" + (bad ? " bad" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.className = ""), 3000);
  }

  // Reports failures from a fan-out {results: {id: {ok, error}}}.
  function report(data) {
    const failed = Object.values(data.results || {}).filter((r) => !r.ok);
    if (failed.length) toast(failed.map((r) => r.error).join(" · "), true);
    return data;
  }

  // Confirms a fan-out that fully succeeded, e.g. "Blink → 2 boards".
  function confirm(label) {
    return (data) => {
      const results = Object.values(data.results || {});
      if (results.length && results.every((r) => r.ok))
        toast(`${label} → ${results.length} board${results.length === 1 ? "" : "s"}`);
      return data;
    };
  }
  const LABELS = { blink: "Blink", wink_left: "Wink left", wink_right: "Wink right", startle: "Startle", roll: "Eye roll", release: "Release" };
  const label = (action) => LABELS[action] || action;

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

  const sendState = (target, state) => api("/api/state", { target, state }).then(report);
  const sendAction = (target, action) => api("/api/action", { target, ...action }).then(report);
  const fail = (err) => toast(err.message, true);

  // ---------- Dashboard ----------
  function dashboard() {
    const cards = document.getElementById("cards");
    const tpl = document.getElementById("card-tpl");
    const byId = new Map();

    const CATEGORY_ORDER = ["halloween", "creatures", "sci-fi", "holidays", "fun", "classic"];
    // Theme options grouped by category, Halloween first; boards without categories stay flat.
    function themeOptions(themes) {
      const rank = (c) => (CATEGORY_ORDER.includes(c) ? CATEGORY_ORDER.indexOf(c) : CATEGORY_ORDER.length);
      return themes
        .map((t, i) => ({ value: t.id, label: t.name, cat: t.category, i }))
        .sort((a, b) => rank(a.cat) - rank(b.cat) || a.i - b.i)
        .map((t) => ({ value: t.value, label: t.label, group: t.cat ? t.cat[0].toUpperCase() + t.cat.slice(1) : null }));
    }

    function fillSelect(sel, options, current) {
      const key = options.map((o) => o.value).join("|");
      if (sel.dataset.key !== key) {
        sel.innerHTML = "";
        let group = null;
        for (const o of options) {
          if (o.group && (!group || group.label !== o.group)) {
            group = document.createElement("optgroup");
            group.label = o.group;
            sel.appendChild(group);
          }
          (o.group ? group : sel).appendChild(new Option(o.label, o.value));
        }
        sel.dataset.key = key;
      }
      if (current !== undefined && document.activeElement !== sel) sel.value = current;
    }

    function update(card, d) {
      const s = d.state || {};
      card.classList.toggle("offline", !d.online);
      card.classList.toggle("off", s.on === false);
      card.querySelector(".name").textContent = d.name;
      card.querySelector(".meta").textContent = `${d.host} · fw ${d.fw || "?"} · ${d.online ? "online" : "offline"}`;
      card.querySelector(".on").checked = !!s.on;
      const bri = card.querySelector(".brightness");
      if (document.activeElement !== bri && s.brightness !== undefined) bri.value = s.brightness;
      fillSelect(card.querySelector(".theme"), themeOptions(d.themes), s.theme);
      fillSelect(card.querySelector(".mood"), d.moods.map((m) => ({ value: m, label: m })), s.mood);
      card.querySelector(".autonomous").checked = !!s.autonomous;
      const stats = [];
      if (s.fps !== undefined) stats.push(`${s.fps} fps`);
      if (s.rssi) stats.push(`${s.rssi} dBm`);
      if (s.uptime !== undefined) stats.push(`up ${Math.round(s.uptime / 60)} min`);
      card.querySelector(".stats").textContent = stats.join(" · ");
    }

    function build(d) {
      const card = tpl.content.firstElementChild.cloneNode(true);
      const target = { device: d.device_id };
      const apply = (state) =>
        sendState(target, state)
          .then((r) => {
            const res = r.results[d.device_id];
            if (res?.ok) update(card, { ...byId.get(d.device_id).data, state: res.state, online: true });
          })
          .catch(fail);
      card.querySelector(".on").addEventListener("change", (e) => apply({ on: e.target.checked }));
      card.querySelector(".brightness").addEventListener("change", (e) => apply({ brightness: +e.target.value }));
      card.querySelector(".theme").addEventListener("change", (e) => apply({ theme: e.target.value }));
      card.querySelector(".mood").addEventListener("change", (e) => apply({ mood: e.target.value }));
      card.querySelector(".autonomous").addEventListener("change", (e) => apply({ autonomous: e.target.checked }));
      card.querySelectorAll("[data-action]").forEach((b) =>
        b.addEventListener("click", () =>
          sendAction(target, { action: b.dataset.action }).then(confirm(label(b.dataset.action))).catch(fail)
        )
      );
      if (d.preview) {
        const img = card.querySelector(".live");
        img.hidden = false;
        livePreview(img, d.device_id);
      }
      return card;
    }

    function render(devices) {
      document.getElementById("count").textContent = `(${devices.length})`;
      if (!devices.length) {
        cards.innerHTML = '<p class="muted">No boards yet — scan the network or add one by IP.</p>';
        byId.clear();
        return;
      }
      if (!byId.size) cards.innerHTML = "";
      for (const d of devices) {
        let entry = byId.get(d.device_id);
        if (!entry) {
          entry = { card: build(d) };
          byId.set(d.device_id, entry);
          cards.appendChild(entry.card);
        }
        entry.data = d;
        update(entry.card, d);
      }
    }

    const load = (refresh) =>
      api("/api/devices" + (refresh ? "?refresh=1" : ""))
        .then((r) => render(r.devices))
        .catch(fail);

    document.getElementById("add-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const host = e.target.host.value.trim();
      api("/api/devices", { host })
        .then((r) => {
          toast(`Added ${r.device.name}`);
          e.target.reset();
          load(false);
        })
        .catch(fail);
    });
    document.getElementById("scan").addEventListener("click", (e) => {
      e.target.disabled = true;
      e.target.textContent = "Scanning…";
      api("/api/scan", { seconds: 4 })
        .then((r) => {
          toast(`Found ${r.devices.length} board(s)` + (r.errors.length ? ` · ${r.errors.join(", ")}` : ""), r.errors.length);
          load(false);
        })
        .catch(fail)
        .finally(() => {
          e.target.disabled = false;
          e.target.textContent = "Scan network";
        });
    });

    const all = { all: true };
    const afterFleet = () => load(false);
    document.getElementById("all-theme").addEventListener("change", (e) => {
      if (e.target.value) sendState(all, { theme: e.target.value }).then(afterFleet).catch(fail);
    });
    document.getElementById("all-mood").addEventListener("change", (e) => {
      if (e.target.value) sendState(all, { mood: e.target.value }).then(afterFleet).catch(fail);
    });
    document.querySelectorAll("[data-all-action]").forEach((b) =>
      b.addEventListener("click", () =>
        sendAction(all, { action: b.dataset.allAction }).then(confirm(label(b.dataset.allAction))).catch(fail)
      )
    );
    document.querySelectorAll("[data-all-state]").forEach((b) =>
      b.addEventListener("click", () => sendState(all, JSON.parse(b.dataset.allState)).then(afterFleet).catch(fail))
    );
    document.querySelectorAll("[data-scene]").forEach((b) =>
      b.addEventListener("click", () =>
        api(`/api/scenes/${b.dataset.scene}/apply`, {})
          .then((r) => {
            toast(`Scene: ${r.scene}`);
            afterFleet();
          })
          .catch(fail)
      )
    );

    load(true);
    setInterval(() => load(true), 15000);
  }

  // ---------- Gaze pad ----------
  function gazePad() {
    const pad = document.getElementById("pad");
    const puck = document.getElementById("puck");
    const readout = document.getElementById("readout");
    const targetSel = document.getElementById("target");
    const target = () => JSON.parse(targetSel.value);
    const config = JSON.parse(document.getElementById("gaze-config").textContent);
    const live = document.getElementById("live");
    let stops = [];

    // Show the live eyes of every simulated board the current target covers.
    function showLive() {
      stops.forEach((stop) => stop());
      stops = [];
      live.innerHTML = "";
      const t = target();
      const ids = t.all ? Object.keys(config.names) : t.group !== undefined ? config.groups[String(t.group)] || [] : [t.device];
      const shown = ids.filter((id) => config.previews.includes(id));
      for (const id of shown) {
        const fig = document.createElement("figure");
        const img = document.createElement("img");
        img.className = "live";
        img.alt = `Live view of ${config.names[id]}`;
        const cap = document.createElement("figcaption");
        cap.textContent = config.names[id];
        fig.append(img, cap);
        live.appendChild(fig);
        stops.push(livePreview(img, id));
      }
      if (!shown.length && ids.length)
        live.innerHTML = '<p class="muted">Real boards don\'t send a picture — watch the eyes themselves.</p>';
    }
    targetSel.addEventListener("change", showLive);
    showLive();
    const HZ = 15;
    let pos = { x: 0, y: 0 };
    let dragging = false;
    let dirty = false;
    let inFlight = false;

    function place(x, y) {
      const len = Math.hypot(x, y);
      if (len > 1) {
        x /= len;
        y /= len;
      }
      pos = { x, y };
      puck.style.left = `${(x + 1) * 50}%`;
      puck.style.top = `${(y + 1) * 50}%`;
      readout.textContent = `x ${x.toFixed(2)} · y ${y.toFixed(2)}`;
      dirty = true;
    }

    function fromEvent(e) {
      const r = pad.getBoundingClientRect();
      place(((e.clientX - r.left) / r.width) * 2 - 1, ((e.clientY - r.top) / r.height) * 2 - 1);
    }

    // Throttled sender: at most HZ requests/s, never more than one in flight.
    setInterval(() => {
      if (!dirty || inFlight) return;
      dirty = false;
      inFlight = true;
      // While dragging, gaze lapses back to idle 1.5 s after the last update; "hold" pins the final spot.
      const duration = !dragging && document.getElementById("hold").checked ? 0 : 1.5;
      sendAction(target(), { action: "look", x: +pos.x.toFixed(3), y: +pos.y.toFixed(3), duration })
        .catch(fail)
        .finally(() => (inFlight = false));
    }, 1000 / HZ);

    pad.addEventListener("pointerdown", (e) => {
      dragging = true;
      pad.setPointerCapture(e.pointerId);
      fromEvent(e);
    });
    pad.addEventListener("pointermove", (e) => dragging && fromEvent(e));
    const end = () => {
      if (!dragging) return;
      dragging = false;
      dirty = true;  // final position: held indefinitely if "hold" is ticked
    };
    pad.addEventListener("pointerup", end);
    pad.addEventListener("pointercancel", end);

    document.querySelectorAll("[data-action]").forEach((b) =>
      b.addEventListener("click", () => {
        if (b.dataset.action === "release") place(0, 0), (dirty = false);
        sendAction(target(), { action: b.dataset.action }).then(confirm(label(b.dataset.action))).catch(fail);
      })
    );
    place(0, 0);
    dirty = false;
  }

  return { dashboard, gazePad, api };
})();
