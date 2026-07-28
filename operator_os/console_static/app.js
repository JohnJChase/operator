/* Console — tabs + status poll + inbox / directory / streams. */
(function () {
  const $ = (id) => document.getElementById(id);
  const TABS = ["plant", "messages", "directory", "streams"];

  let menuDigits = [];
  let chart = { states: [], edges: [] };
  let lastFlashKey = "";
  let streamsState = {};
  let inboxTimer = null;
  let currentTab = "plant";

  async function api(path, opts) {
    const r = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(opts && opts.headers) },
      ...opts,
    });
    const text = await r.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (_) {}
    if (!r.ok) throw new Error(data.error || r.statusText || String(r.status));
    return data;
  }

  function showApp(on) {
    $("app").classList.toggle("hidden", !on);
    $("login-gate").classList.toggle("hidden", on);
  }

  function showTab(name) {
    if (!TABS.includes(name)) name = "plant";
    currentTab = name;
    for (const t of TABS) {
      const panel = document.querySelector(`[data-panel="${t}"]`);
      const tab = document.querySelector(`.tab[data-tab="${t}"]`);
      if (panel) panel.classList.toggle("hidden", t !== name);
      if (tab) {
        tab.classList.toggle("active", t === name);
        tab.setAttribute("aria-selected", t === name ? "true" : "false");
      }
    }
    if (location.hash.replace("#", "") !== name) {
      history.replaceState(null, "", "#" + name);
    }
    if (name === "messages") refreshInbox();
    if (name === "directory") refreshPhonebook();
    if (name === "streams") loadStreams();
  }

  document.querySelectorAll(".tab").forEach((el) => {
    el.addEventListener("click", () => showTab(el.dataset.tab));
  });
  window.addEventListener("hashchange", () => {
    showTab(location.hash.replace("#", "") || "plant");
  });

  async function boot() {
    try {
      const w = await api("/api/whoami");
      if (w.ok) {
        showApp(true);
        await loadStatic();
        showTab(location.hash.replace("#", "") || "plant");
        startPolling();
        return;
      }
    } catch (_) {}
    showApp(false);
    $("login-gate").classList.remove("hidden");
  }

  function startPolling() {
    poll();
    setInterval(poll, 1000);
    refreshInbox();
    refreshPhonebook();
    if (inboxTimer) clearInterval(inboxTimer);
    inboxTimer = setInterval(() => {
      if (currentTab === "messages" || document.hasFocus()) refreshInbox();
    }, 5000);
  }

  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("login-err").textContent = "";
    const password = new FormData(e.target).get("password");
    try {
      await api("/api/login", { method: "POST", body: JSON.stringify({ password }) });
      showApp(true);
      await loadStatic();
      showTab(location.hash.replace("#", "") || "plant");
      startPolling();
    } catch (err) {
      $("login-err").textContent = err.message || "Login failed";
    }
  });

  $("btn-logout").addEventListener("click", async () => {
    try {
      await api("/api/logout", { method: "POST", body: "{}" });
    } catch (_) {}
    location.reload();
  });

  $("btn-ring").addEventListener("click", async () => {
    if (!confirm("Ring the physical bell for 1.5 seconds?")) return;
    try {
      await api("/api/ring-test", { method: "POST", body: "{}" });
    } catch (err) {
      alert(err.message || "Ring failed");
    }
  });

  $("btn-inbox-refresh").addEventListener("click", () => refreshInbox());
  $("log-filter").addEventListener("input", () => {
    renderLog(window.__lastStatus && window.__lastStatus.events);
  });

  $("pb-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      await api("/api/phonebook", {
        method: "POST",
        body: JSON.stringify({
          name: fd.get("name"),
          e164: fd.get("e164"),
          short_code: fd.get("short_code"),
          notes: fd.get("notes"),
        }),
      });
      e.target.reset();
      await refreshPhonebook();
      await refreshInbox();
    } catch (err) {
      alert(err.message || "Save failed");
    }
  });

  $("btn-streams-save").addEventListener("click", async () => {
    const status = $("streams-status");
    const btn = $("btn-streams-save");
    const s3l = $("stream-3-label").value;
    const s3u = $("stream-3-url").value;
    const s4l = $("stream-4-label").value;
    const s4u = $("stream-4-url").value;
    status.className = "fine";
    status.textContent = "Probing URLs…";
    btn.disabled = true;
    try {
      const out = await api("/api/streams", {
        method: "POST",
        body: JSON.stringify({
          streams: {
            "3": { label: s3l, url: s3u },
            "4": { label: s4l, url: s4u },
          },
        }),
      });
      streamsState = out.streams || {};
      const m = await api("/api/menu");
      menuDigits = m.digits || [];
      renderMenu(
        window.__lastStatus && window.__lastStatus.state,
        window.__lastStatus && window.__lastStatus.service_digit
      );
      status.className = "fine ok";
      status.textContent = "Validated and saved.";
    } catch (err) {
      status.className = "fine bad";
      status.textContent = err.message || "Save failed — not written.";
    } finally {
      btn.disabled = false;
    }
  });

  async function loadStatic() {
    chart = await api("/api/chart");
    const m = await api("/api/menu");
    menuDigits = m.digits || [];
    renderMenu(null, null);
    renderChart(null, null);
  }

  async function loadStreams() {
    const data = await api("/api/streams");
    streamsState = data.streams || {};
    const el = $("streams-form");
    el.innerHTML = "";
    for (const dig of ["3", "4"]) {
      const s = streamsState[dig] || {};
      const lab = document.createElement("label");
      lab.innerHTML = `Digit ${dig} label<input id="stream-${dig}-label" value="${escapeAttr(s.label || "")}" />`;
      const url = document.createElement("label");
      url.innerHTML = `Digit ${dig} URL<input id="stream-${dig}-url" value="${escapeAttr(s.url || "")}" />`;
      el.appendChild(lab);
      el.appendChild(url);
    }
    $("streams-status").textContent = "";
    $("streams-status").className = "fine";
  }

  async function poll() {
    try {
      const st = await api("/api/status");
      window.__lastStatus = st;
      paintStatus(st);
      if (currentTab === "plant") {
        renderChart(st.state, st.last_event);
        renderMenu(st.state, st.service_digit);
        renderLog(st.events);
      }
    } catch (err) {
      if (String(err.message).includes("unauthorized")) {
        showApp(false);
        $("login-gate").classList.remove("hidden");
      }
    }
  }

  function paintStatus(st) {
    $("st-state").textContent = st.state || "—";
    $("st-hook").textContent = st.off_hook ? "OFF_HOOK" : "ON_HOOK";
    $("st-digit").textContent = st.last_digit == null ? "—" : String(st.last_digit);
    $("st-digits").textContent = (st.last_digits || []).join(" ") || "—";
    $("st-outside").textContent = st.outside_buffer || "—";
    $("st-ring").textContent = st.ringing ? "RINGING" : "idle";
    const sip = st.sip || {};
    $("st-sip").textContent = sip.summary || "—";
    $("st-event").textContent = [st.last_event, st.last_reason].filter(Boolean).join(" / ") || "—";

    const r = st.readiness || {};
    const chip = $("readiness");
    chip.textContent = r.level || "—";
    chip.className = "chip " + (r.level || "");
    chip.title = (r.reasons || []).join(", ") || "ok";

    const patch = st.plant || {};
    $("patch-pre").textContent = JSON.stringify(patch, null, 2);
  }

  function renderChart(current, lastEvent) {
    const el = $("chart");
    if (!chart.edges || !chart.edges.length) {
      el.textContent = "(no chart)";
      return;
    }
    const out = new Set();
    if (current) {
      for (const e of chart.edges) {
        if (e.source === current) out.add(e.source + "|" + e.event + "|" + e.dest);
      }
    }
    const flashKey =
      current && lastEvent
        ? chart.edges.find((e) => e.source === current && e.event === lastEvent) ||
          chart.edges.find((e) => e.dest === current && e.event === lastEvent)
        : null;
    const lines = [];
    lines.push("states:");
    for (const s of chart.states || []) {
      const cls = s === current ? "node current" : "node";
      lines.push(`  <span class="${cls}">${s}</span>`);
    }
    lines.push("");
    lines.push("edges:");
    for (const e of chart.edges) {
      const key = e.source + "|" + e.event + "|" + e.dest;
      let cls = "edge";
      if (out.has(key)) cls += " out";
      if (flashKey && e === flashKey) cls += " flash";
      lines.push(`  <span class="${cls}">${e.source} --${e.event}--> ${e.dest}</span>`);
    }
    el.innerHTML = lines.join("\n");
    if (flashKey) {
      const k = flashKey.source + flashKey.event + flashKey.dest;
      if (k !== lastFlashKey) lastFlashKey = k;
    }
  }

  function renderMenu(state, serviceDigit) {
    const ul = $("menu-tree");
    ul.innerHTML = "";
    for (const d of menuDigits) {
      const li = document.createElement("li");
      const states = d.highlight_states || [];
      let on = state && states.includes(state);
      if (!on && serviceDigit != null && d.service_digit === serviceDigit && state === "PLAYING_SERVICE") {
        on = true;
      }
      if (!on && state === "OUTSIDE_LINE" && d.digit === 9) on = true;
      if (!on && state === "MEET_CHOOSING" && d.digit === 7) on = true;
      if (!on && state === "SIP_CALL" && (d.digit === 7 || d.digit === 9)) on = true;
      if (on) li.classList.add("on");
      li.innerHTML = `<span class="d">${d.digit}</span>${escapeHtml(d.label)}`;
      if (d.children && d.children.length) {
        const sub = document.createElement("ul");
        for (const c of d.children) {
          const sli = document.createElement("li");
          sli.textContent = c.label + (c.note ? " — " + c.note : "");
          sub.appendChild(sli);
        }
        li.appendChild(sub);
      }
      ul.appendChild(li);
    }
  }

  function renderLog(events) {
    const q = ($("log-filter").value || "").trim().toLowerCase();
    const lines = [];
    for (const ev of events || []) {
      const s = JSON.stringify(ev);
      if (q && !s.toLowerCase().includes(q)) continue;
      lines.push(s);
    }
    $("log").textContent = lines.slice(-80).join("\n") || "(no events)";
  }

  function setWaitingBadge(n) {
    const badge = $("tab-waiting");
    if (n > 0) {
      badge.textContent = String(n);
      badge.classList.remove("hidden");
    } else {
      badge.classList.add("hidden");
    }
  }

  async function refreshInbox() {
    try {
      const data = await api("/api/inbox");
      setWaitingBadge(data.waiting || 0);
      const root = $("inbox");
      root.innerHTML = "";
      const waiting = data.waiting || 0;
      const head = document.createElement("p");
      head.className = "fine";
      head.textContent = waiting ? `${waiting} waiting` : "No waiting messages";
      root.appendChild(head);

      for (const m of data.sms || []) {
        const div = document.createElement("div");
        div.className = "msg-ticket" + (m.direction === "in" && !m.heard_at ? " unheard" : "");
        const who =
          m.direction === "in"
            ? m.from_name || m.from_e164
            : "to " + (m.to_name || m.to_e164);
        div.innerHTML = `<div class="who">${escapeHtml(who)} <span class="meta">${m.direction}</span></div>
          <div class="meta">${fmtTime(m.created_at)} · ${escapeHtml(m.from_e164 || "")}</div>
          <div class="body">${escapeHtml(m.body || "")}</div>`;
        const actions = document.createElement("div");
        actions.className = "actions";
        if (m.direction === "in" && !m.heard_at) {
          actions.appendChild(
            btn("Mark heard", async () => {
              await api("/api/inbox/sms/heard", {
                method: "POST",
                body: JSON.stringify({ id: m.id }),
              });
              refreshInbox();
            })
          );
        }
        if (m.direction === "in") {
          actions.appendChild(
            btn("Reply…", async () => {
              const text = prompt("Reply text (confirm to send):");
              if (text == null || !text.trim()) return;
              if (!confirm("Send SMS to " + m.from_e164 + "?")) return;
              await api("/api/inbox/sms/reply", {
                method: "POST",
                body: JSON.stringify({ id: m.id, text: text.trim(), confirm: true }),
              });
              refreshInbox();
            })
          );
          actions.appendChild(
            btn("Call", async () => {
              if (
                !confirm(
                  "Place call to " + (m.from_name || m.from_e164) + "? Handset must be off-hook."
                )
              )
                return;
              await api("/api/place-call", {
                method: "POST",
                body: JSON.stringify({ e164: m.from_e164 }),
              });
            })
          );
        }
        actions.appendChild(
          btn("Delete", async () => {
            if (!confirm("Delete this message?")) return;
            await api("/api/inbox/sms/delete", {
              method: "POST",
              body: JSON.stringify({ id: m.id }),
            });
            refreshInbox();
          })
        );
        div.appendChild(actions);
        root.appendChild(div);
      }

      for (const vm of data.voicemails || []) {
        const div = document.createElement("div");
        div.className = "msg-ticket" + (!vm.heard_at ? " unheard" : "");
        const who = vm.from_name || vm.from_e164 || "unknown";
        div.innerHTML = `<div class="who">Voicemail · ${escapeHtml(who)}</div>
          <div class="meta">${fmtTime(vm.created_at)} · ${Number(vm.duration_s || 0).toFixed(0)}s</div>`;
        const audio = document.createElement("audio");
        audio.controls = true;
        audio.preload = "none";
        audio.src = vm.audio_url;
        div.appendChild(audio);
        const actions = document.createElement("div");
        actions.className = "actions";
        if (!vm.heard_at) {
          actions.appendChild(
            btn("Mark heard", async () => {
              await api("/api/inbox/vm/heard", {
                method: "POST",
                body: JSON.stringify({ id: vm.id }),
              });
              refreshInbox();
            })
          );
        }
        actions.appendChild(
          btn("Call", async () => {
            if (!confirm("Place call to " + who + "? Handset must be off-hook.")) return;
            await api("/api/place-call", {
              method: "POST",
              body: JSON.stringify({ e164: vm.from_e164 }),
            });
          })
        );
        actions.appendChild(
          btn("Delete", async () => {
            if (!confirm("Delete this voicemail?")) return;
            await api("/api/inbox/vm/delete", {
              method: "POST",
              body: JSON.stringify({ id: vm.id }),
            });
            refreshInbox();
          })
        );
        div.appendChild(actions);
        root.appendChild(div);
      }
    } catch (_) {}
  }

  async function refreshPhonebook() {
    try {
      const data = await api("/api/phonebook");
      const root = $("phonebook");
      root.innerHTML = "";
      for (const c of data.contacts || []) {
        const div = document.createElement("div");
        div.className = "msg-ticket";
        div.innerHTML = `<div class="who">${escapeHtml(c.name)}</div>
          <div class="meta">${escapeHtml(c.e164)}${c.short_code ? " · code " + escapeHtml(c.short_code) : ""}</div>
          <div class="body">${escapeHtml(c.notes || "")}</div>`;
        const actions = document.createElement("div");
        actions.className = "actions";
        actions.appendChild(
          btn("Call", async () => {
            if (!confirm("Call " + c.name + "? Handset must be off-hook.")) return;
            await api("/api/place-call", {
              method: "POST",
              body: JSON.stringify({ e164: c.e164 }),
            });
          })
        );
        actions.appendChild(
          btn("Call by name", async () => {
            if (!confirm("Call " + c.name + " by name? Handset must be off-hook.")) return;
            await api("/api/place-call", {
              method: "POST",
              body: JSON.stringify({ name: c.name }),
            });
          })
        );
        actions.appendChild(
          btn("Delete", async () => {
            if (!confirm("Remove " + c.name + "?")) return;
            await api("/api/phonebook/delete", {
              method: "POST",
              body: JSON.stringify({ id: c.id }),
            });
            refreshPhonebook();
          })
        );
        div.appendChild(actions);
        root.appendChild(div);
      }
      if (!(data.contacts || []).length) {
        root.textContent = "(empty directory)";
      }
    } catch (_) {}
  }

  function btn(label, fn) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.addEventListener("click", async () => {
      try {
        await fn();
      } catch (err) {
        alert(err.message || "Failed");
      }
    });
    return b;
  }

  function fmtTime(ts) {
    if (!ts) return "—";
    try {
      return new Date(ts * 1000).toLocaleString();
    } catch (_) {
      return String(ts);
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/"/g, "&quot;");
  }

  boot();
})();
