/*
 * youtube-tv-gameday-card — live scoreboard; tap a game to put it on your TV via YouTube TV.
 * Data: websocket `youtube_tv_gameday/scoreboard` (ESPN scores + each game's channel in your lineup).
 * Tap: service youtube_tv_gameday.tune_channel.
 * Config (all optional): { type: custom:youtube-tv-gameday-card, favorites: [Guardians], refresh: 30, entry_id: ... }
 * Unofficial; not affiliated with Google, YouTube or ESPN.
 */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hex = (c, d) => (/^[0-9a-f]{6}$/i.test(c || "") ? "#" + c : d);
const TAG = { "College Football": "NCAAF", "College Basketball": "NCAAB", "Premier League": "EPL",
  "Champions League": "UCL", "International": "Intl" };  // short league tags for tiles

class YouTubeTvGamedayCard extends HTMLElement {
  static getStubConfig() { return { refresh: 30 }; }

  setConfig(config) {
    this._config = { refresh: 30, ...config };
    this._favs = (this._config.favorites || []).map((f) => f.toLowerCase());
    try { this._filter = localStorage.getItem("ytgd-filter") || "All"; } catch (e) { this._filter = "All"; }
    try { this._mine = localStorage.getItem("ytgd-mine") === "1"; } catch (e) { this._mine = false; }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._root) this._build();
    if (!this._timer) {
      this._load();
      this._timer = setInterval(() => this._load(), this._config.refresh * 1000);
    }
  }

  connectedCallback() { if (this._hass && !this._timer) this.hass = this._hass; }
  disconnectedCallback() { clearInterval(this._timer); clearInterval(this._dtimer); this._timer = null; }
  getCardSize() { return 12; }

  _build() {
    this._root = this.attachShadow({ mode: "open" });
    this._root.innerHTML = `<style>${CSS}</style><div class="wrap"><div id="main"></div>
      <div class="sheet-bg" id="sheet"></div><div class="toast" id="toast"></div></div>`;
    this._root.addEventListener("click", (ev) => {
      if (this._longFired) { this._longFired = false; ev.preventDefault(); return; }  // long-press already handled
      if (ev.target.id === "sheet" || ev.target.closest("[data-close]")) { this._closeDetail(); return; }
      const el = ev.target.closest("[data-nav],[data-nowhide],[data-info],[data-remember],[data-game],[data-app],[data-ch],[data-filter],[data-mine]");
      if (!el) return;
      if (el.dataset.nav) this._navigate(el.dataset.nav);
      else if (el.dataset.nowhide !== undefined) { this._nowHideGid = el.dataset.nowhide; this._nowDetail = null; this._render(); }
      else if (el.dataset.remember) this._remember(el.dataset.remember, el.dataset.label, el);
      else if (el.dataset.game && !el.dataset.info) this._tuneGame(el.dataset.game, el.dataset.label, el);
      else if (el.dataset.app) this._openApp(el.dataset.app, el.dataset.label, el);
      else if (el.dataset.info) this._openDetail(el.dataset.info);
      else if (el.dataset.filter) { this._filter = el.dataset.filter; this._save(); this._render(); }
      else if (el.dataset.mine !== undefined) { this._mine = !this._mine; this._save(); this._render(); }
      else if (el.dataset.ch) this._tune(el.dataset.ch, el.dataset.label, el);
    });
    // press-and-hold a tile -> game detail (a short tap still tunes)
    const cancel = () => { clearTimeout(this._press); this._press = null; };
    this._root.addEventListener("pointerdown", (ev) => {
      const tile = ev.target.closest(".tile[data-gid]");
      if (!tile || ev.target.closest("[data-info]")) return;
      this._pressAt = [ev.clientX, ev.clientY];
      cancel();
      this._press = setTimeout(() => { this._longFired = true; this._openDetail(tile.dataset.gid); }, 550);
    });
    this._root.addEventListener("pointermove", (ev) => {
      if (this._press && Math.hypot(ev.clientX - this._pressAt[0], ev.clientY - this._pressAt[1]) > 12) cancel();
    });
    for (const e of ["pointerup", "pointercancel", "pointerleave"]) this._root.addEventListener(e, cancel);
    this._root.addEventListener("contextmenu", (ev) => { if (ev.target.closest(".tile")) ev.preventDefault(); });
  }

  async _openDetail(gid) {
    this._detailId = gid;
    this._detail = null;
    this._renderDetail();
    await this._loadDetail();
    clearInterval(this._dtimer);
    this._dtimer = setInterval(() => this._loadDetail(), 20000);
  }

  _closeDetail() {
    this._detailId = null;
    clearInterval(this._dtimer);
    this._renderDetail();
  }

  async _loadDetail() {
    const gid = this._detailId;
    if (!gid) return;
    try {
      const msg = { type: "youtube_tv_gameday/game", game_id: gid };
      if (this._config.entry_id) msg.entry_id = this._config.entry_id;
      const d = await this._hass.connection.sendMessagePromise(msg);
      if (this._detailId === gid) { this._detail = d; this._detailErr = null; }
    } catch (e) {
      this._detailErr = (e && (e.message || e.code)) || "couldn't load the game";
    }
    this._renderDetail();
  }

  _save() {
    try { localStorage.setItem("ytgd-filter", this._filter); localStorage.setItem("ytgd-mine", this._mine ? "1" : "0"); } catch (e) { /* private mode */ }
  }

  async _load() {
    try {
      const msg = { type: "youtube_tv_gameday/scoreboard" };
      if (this._config.entry_id) msg.entry_id = this._config.entry_id;
      this._data = await this._hass.connection.sendMessagePromise(msg);
      this._error = null;
      if (this._data.now) this._now = this._data.now;
      if (this._data.now_label !== undefined) this._nowLabel = this._data.now_label;
      if (!this._config.favorites) this._favs = (this._data.favorites || []).map((f) => f.toLowerCase());
      this._short = Object.fromEntries((this._data.quick || []).map((q) => [q.channel, q.label]));
    } catch (e) {
      this._error = (e && (e.message || e.code)) || "couldn't load scores";
    }
    this._render();
    this._refreshNow();   // the live-stats panel for the game on your TV (own fetch, re-renders when ready)
  }

  _label(g) {
    const [a, h] = g.teams.length === 2 ? g.teams : [g.teams[0] || {}, g.teams[1] || {}];
    return `${a.abbr || a.name} @ ${h.abbr || h.name}`;
  }

  // The live game currently on the TV, if we can tell: the backend resolves it (a game we put on, or the
  // only live game on a tuned channel); fall back to matching what we last tuned here.
  _nowGid() {
    const d = this._data;
    if (!d || d.tv === "off") return null;
    const games = d.games || [];
    const liveById = (id) => games.find((g) => g.id === id && g.state === "in");
    if (d.now_game && liveById(d.now_game)) return d.now_game;
    if (this._nowLabel) { const g = games.find((x) => x.state === "in" && this._label(x) === this._nowLabel); if (g) return g.id; }
    if (this._now) { const g = games.find((x) => x.state === "in" && x.channel === this._now); if (g) return g.id; }
    return null;
  }

  async _refreshNow() {
    const gid = this._nowGid();
    if (this._nowHideGid && this._nowHideGid !== gid) this._nowHideGid = null;  // a new game cancels a dismissal
    const target = gid && gid !== this._nowHideGid ? gid : null;
    if (!target) {
      this._nowCur = null;
      if (this._nowDetail) { this._nowDetail = null; this._render(); }
      return;
    }
    try {
      const msg = { type: "youtube_tv_gameday/game", game_id: target };
      if (this._config.entry_id) msg.entry_id = this._config.entry_id;
      const d = await this._hass.connection.sendMessagePromise(msg);
      if (this._nowGid() === target) { this._nowDetail = d; this._nowCur = target; this._render(); }
    } catch (e) {
      if (this._nowCur !== target) { this._nowDetail = null; this._render(); }   // keep last good stats on a transient error
    }
  }

  async _tune(ch, label, el) {
    if (this._busy) return;
    this._busy = true;
    el?.classList.add("tapped");
    const what = `<b>${esc(ch)}</b>${label ? " · " + esc(label) : ""}`;
    this._toast(this._data?.tv === "off"
      ? `Turning on the TV and opening YouTube TV for ${what}… <span class="soft">(about 30 s)</span>`
      : `Switching the TV to ${what}…`, "busy");
    try {
      const data = { channel: ch };
      if (label) data.label = label;
      if (this._config.entry_id) data.config_entry_id = this._config.entry_id;
      await this._hass.callService("youtube_tv_gameday", "tune_channel", data);
      this._now = ch;
      if (this._data) this._data.tv = "on";
      this._toast(`📺 Now on <b>${esc(ch)}</b>`, "ok");
      this._nowHideGid = null;
      this._render();
      this._refreshNow();
    } catch (e) {
      this._toast(`Couldn't tune ${esc(ch)}: ${esc(e.message || e)}`, "err");
    } finally {
      this._busy = false;
      el?.classList.remove("tapped");
    }
  }

  _game(gid) { return (this._data?.games || []).find((g) => g.id === gid); }

  async _tuneGame(gid, label, el) {
    if (this._busy) return;
    const g = this._game(gid) || {};
    const what = g.saved_stream ? "your saved stream" : g.channel || g.app || "the game";
    this._busy = true;
    el?.classList.add("tapped");
    this._toast(`${this._data?.tv === "off" ? "Turning on the TV and putting on" : "Putting on"} <b>${esc(label || "")}</b> · ${esc(what)}…`, "busy");
    try {
      const data = { game_id: gid };
      if (this._config.entry_id) data.config_entry_id = this._config.entry_id;
      await this._hass.callService("youtube_tv_gameday", "tune_game", data);
      this._now = g.saved_stream ? "saved stream" : g.channel || g.app;
      this._nowLabel = label;
      if (this._data) this._data.tv = "on";
      const tip = g.regional && !g.saved_stream
        ? `<br><span class="soft">Regional game — if your ${esc(g.channel)} shows a different one, put it on via Sunday Ticket and tap 📌 in its details.</span>` : "";
      this._toast(`📺 <b>${esc(label || "")}</b> · ${esc(what)}${tip}`, g.regional && !g.saved_stream ? "warn" : "ok");
      this._nowHideGid = null;
      this._render();
      this._refreshNow();
    } catch (e) {
      this._toast(`Couldn't put it on: ${esc(e.message || e)}`, "err");
    } finally {
      this._busy = false;
      el?.classList.remove("tapped");
    }
  }

  async _remember(gid, label, el) {
    if (this._busy) return;
    this._busy = true;
    this._toast(`Checking what's on the TV…`, "busy");
    try {
      const data = { game_id: gid };
      if (this._config.entry_id) data.config_entry_id = this._config.entry_id;
      await this._hass.callService("youtube_tv_gameday", "remember_game", data);
      const g = this._game(gid);
      if (g) g.saved_stream = true;
      this._nowLabel = label;
      this._now = "saved stream";
      this._toast(`📌 Saved — tapping <b>${esc(label || "this game")}</b> today goes straight to what's on now`, "ok");
      this._render();
      if (this._detailId) this._loadDetail();
    } catch (e) {
      this._toast(`Couldn't save it: ${esc(e.message || e)}`, "err");
    } finally {
      this._busy = false;
    }
  }

  async _openApp(app, label, el) {
    if (this._busy) return;
    this._busy = true;
    el?.classList.add("tapped");
    this._toast(`${this._data?.tv === "off" ? "Turning on the TV and opening" : "Opening"} <b>${esc(app)}</b>…`, "busy");
    try {
      const data = { app };
      if (label) data.label = label;
      if (this._config.entry_id) data.config_entry_id = this._config.entry_id;
      await this._hass.callService("youtube_tv_gameday", "open_app", data);
      this._now = app;
      if (this._data) this._data.tv = "on";
      this._toast(`▶ <b>${esc(app)}</b> is open${label ? ` — pick <b>${esc(label)}</b> from its Live / Sports row` : ""}`, "ok");
      this._render();
    } catch (e) {
      this._toast(`Couldn't open ${esc(app)}: ${esc(e.message || e)}`, "err");
    } finally {
      this._busy = false;
      el?.classList.remove("tapped");
    }
  }

  _toast(html, kind) {
    const t = this._root.getElementById("toast");
    t.innerHTML = html;
    t.className = "toast show " + kind;
    clearTimeout(this._tt);
    if (kind !== "busy") this._tt = setTimeout(() => (t.className = "toast"), 3500);
  }

  // Times follow Home Assistant's time zone (where the TV is), not the browser's — a wall tablet set to
  // the wrong zone shouldn't show the wrong kickoff. `time_zone: browser` (or an IANA name) overrides.
  _tz() {
    const want = this._config.time_zone;
    if (want === "browser") return undefined;
    return want || this._hass?.config?.time_zone || undefined;
  }

  _time(iso) {
    try {
      return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", timeZone: this._tz() });
    } catch (e) {   // unknown zone name -> browser time
      return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    }
  }

  _isFav(g) {
    return g.teams.some((t) => [t.name, t.full, t.abbr].some((n) => n && this._favs.includes(String(n).toLowerCase())));
  }

  _render() {
    const main = this._root?.getElementById("main");
    if (!main) return;
    if (this._config.mode === "strip") { this._renderStrip(main); return; }
    const d = this._data;
    const games = (d?.games || []).filter((g) =>
      (this._filter === "All" || g.league === this._filter) && (!this._mine || g.channel || g.app));
    const leagues = ["All", ...new Set((d?.games || []).map((g) => g.league))];
    const byFav = (a, b) => (this._isFav(b) - this._isFav(a));
    const live = games.filter((g) => g.state === "in")
      .sort((a, b) => byFav(a, b) || (!!b.alert - !!a.alert) || (!!b.channel - !!a.channel));
    const next = games.filter((g) => g.state === "pre").sort((a, b) => byFav(a, b) || a.start.localeCompare(b.start));
    const done = games.filter((g) => g.state === "post").sort((a, b) => byFav(a, b) || b.start.localeCompare(a.start));
    const today = new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", timeZone: this._tz() });
    const upd = d?.updated ? this._time(d.updated) : "—";

    main.innerHTML = `
      <header>
        <div class="brand">${this._config.home ? `<button class="homebtn" data-nav="${esc(this._config.home)}" aria-label="Home">⌂ Home</button>` : ""}<span class="ball">●</span> GAME DAY <span class="date">${esc(today)}</span></div>
        <div class="meta">
          ${this._now ? `<span class="nowpill">📺 On TV: <b>${esc(this._now === "saved stream" && this._nowLabel ? this._nowLabel : this._now)}</b></span>` : ""}
          <span class="upd">${this._error ? `<span class="errtxt">⚠ ${esc(this._error)}</span>` : `Updated ${esc(upd)}`}</span>
        </div>
      </header>
      <nav class="chips">
        ${leagues.map((l) => `<button class="chip ${l === this._filter ? "on" : ""}" data-filter="${esc(l)}">${esc(l)}</button>`).join("")}
        <button class="chip mine ${this._mine ? "on" : ""}" data-mine>Watchable</button>
      </nav>
      <div class="scroll">
        ${this._nowPanel()}
        ${!d && !this._error ? `<div class="empty">Loading today's games…</div>` : ""}
        ${d && !games.length ? `<div class="empty">${(d.games || []).length ? "No games match — try another filter." : "No games today in your leagues."}</div>` : ""}
        ${this._section("Live now", live, "live")}
        ${this._section("Coming up", next, "next")}
        ${this._section("Final", done, "final")}
      </div>
      <footer class="chanbar">
        ${(d?.quick || []).map((q) =>
          `<button class="chan ${q.channel === this._now ? "on" : ""}" data-ch="${esc(q.channel)}">${esc(q.label)}</button>`).join("")}
      </footer>`;
  }

  // ---------------------------------------------------------------- compact strip (mode: strip)
  _renderStrip(main) {
    this._root.querySelector(".wrap").classList.add("strip");
    const d = this._data;
    const order = { in: 0, pre: 1, post: 2 };
    const items = (d?.games || [])
      .filter((g) => this._isFav(g) || (g.state === "in" && g.alert))
      .sort((a, b) => (order[a.state] - order[b.state]) || (!!b.alert - !!a.alert) || a.start.localeCompare(b.start))
      .slice(0, this._config.max || 8);
    const live = (d?.games || []).filter((g) => g.state === "in").length;
    const more = this._config.more;
    const mini = (g) => {
      const [a, h] = g.teams.length === 2 ? g.teams : [g.teams[0] || {}, g.teams[1] || {}];
      const st = g.state === "in" ? `<span class="livetag">LIVE</span> ${esc(g.detail)}`
        : g.state === "pre" ? (g.tbd ? "TBD" : esc(this._time(g.start)))
        : esc(g.detail || "Final");
      const tappable = (g.channel || g.app || g.saved_stream) && g.state !== "post";
      const row = (t) => `<div class="mrow ${g.state === "post" && t.winner === false ? "lose" : ""}">
        ${t.logo ? `<img src="${esc(t.logo.replace("/500/", "/500-dark/"))}" data-light="${esc(t.logo)}"
          onerror="if(this.dataset.light&&this.src!==this.dataset.light){this.src=this.dataset.light}" alt="">` : ""}
        <span class="mabbr">${esc(t.abbr || t.name)}</span><span class="msc">${g.state === "pre" ? "" : esc(t.score ?? "")}</span></div>`;
      return `<div class="mini tile ${g.state} ${tappable ? "tap" : ""} ${g.state === "in" && g.alert ? "alert " + esc(g.alert.kind) : ""}"
          data-gid="${esc(g.id)}" ${tappable ? `data-game="${esc(g.id)}" data-label="${esc((a.abbr || "") + " @ " + (h.abbr || ""))}"` : ""}
          style="--c1:${hex(a.color, "#3a4a63")};--c2:${hex(h.color, "#63503a")}">
        <div class="mtop"><span class="lg">${esc(TAG[g.league] || g.league)}</span><span class="st">${st}</span></div>
        ${row(a)}${row(h)}
        <div class="mfoot">${g.state === "in" && g.alert ? `<span class="malert">${esc(g.alert.text)}</span>`
          : `<span class="net ${g.channel ? "" : g.app ? "app" : "none"}">${esc(g.channel ? ((this._short || {})[g.channel] || g.channel) : g.app ? "▶ " + g.app : ((g.nets || [])[0] || ""))}</span>`}</div>
      </div>`;
    };
    main.innerHTML = `
      <div class="shead"><span class="brand"><span class="ball">●</span> GAME DAY</span>
        <span class="smeta">${this._now ? `📺 ${esc(this._now)} · ` : ""}${live} live</span>
        ${more ? `<a class="open" data-nav="${esc(more)}">Open ›</a>` : ""}</div>
      <div class="srow">${items.length ? items.map(mini).join("")
        : `<div class="sempty">${d ? "No games for your teams today." : "Loading…"}${live && more ? ` <a data-nav="${esc(more)}">See all ${live} live games ›</a>` : ""}</div>`}</div>`;
  }

  // Pinned live stats for the game on your TV. Uses the same summary as the long-press sheet,
  // refreshed on every poll; tap it for the full sheet. Shown only while that game is live.
  _nowPanel() {
    if (this._config.mode === "strip") return "";
    const d = this._nowDetail;
    if (!d || d.state !== "in") return "";
    const lite = this._game(d.game_id) || {};
    const [a, h] = d.teams.length === 2 ? d.teams : [d.teams[0] || {}, d.teams[1] || {}];
    const logo = (t) => t.logo ? `<img src="${esc(t.logo.replace("/500/", "/500-dark/"))}" data-light="${esc(t.logo)}"
      onerror="if(this.dataset.light&&this.src!==this.dataset.light){this.src=this.dataset.light}" alt="">` : `<span class="nologo"></span>`;
    const chan = d.saved_stream ? `<span class="net saved">📌 Saved stream</span>`
      : d.channel ? `<span class="net on">📺 ${esc((this._short || {})[d.channel] || d.channel)}</span>`
      : d.app ? `<span class="net app">▶ ${esc(d.app)}</span>` : "";
    const row = (t) => `<div class="ntrow ${t.winner === false ? "lose" : ""}">${logo(t)}
      <span class="nm">${esc(t.name)}</span>${t.record ? `<span class="rec">${esc(t.record)}</span>` : ""}
      <span class="sc">${esc(t.score ?? "")}</span></div>`;
    const lead = (d.leaders || []).map((l) => {
      const c = (l.cats || [])[0]; return c ? `<span class="nl"><b>${esc(l.team)}</b> ${esc(c.who || "")} · ${esc(c.line)}</span>` : "";
    }).join("");
    const play = (d.plays || [])[0] ? `<div class="nowplay"><span class="k">LATEST</span> ${esc(d.plays[0])}</div>` : "";
    // Two columns: teams on the left, win probability on the right. The line score fills whichever
    // side has room (under the teams when there's a chart, else it takes the right column itself).
    const wp = this._wpChart(d, a, h);
    const lscore = this._lineScore(d);
    const leftExtra = wp ? lscore : "";
    const rightMain = wp || lscore || "";
    return `<section class="nowtv" data-info="${esc(d.game_id)}" style="--c1:${hex(a.color, "#3a4a63")};--c2:${hex(h.color, "#63503a")}">
      <div class="nowtv-head"><span class="tag">📺 On your TV</span>
        <span class="st"><span class="livetag">LIVE</span> ${esc(d.detail || "")}</span>
        ${chan}${this._situation(lite)}
        <button class="nowx" data-nowhide="${esc(d.game_id)}" aria-label="Hide">✕</button></div>
      <div class="nowtv-body">
        <div class="nowleft"><div class="nowteams">${row(a)}${row(h)}</div>${leftExtra}</div>
        <div class="nowwp">${rightMain}</div>
      </div>
      ${this._atBat(d, lite)}
      ${lead ? `<div class="nowleaders">${lead}</div>` : ""}
      ${play}
      <div class="nowmore">Tap for full details ›</div>
    </section>`;
  }

  // Baseball at-bat: current batter and pitcher with pitch count and season AVG/OBP/ERA.
  // Names + today's line come from the scoreboard (always live); AVG/OBP/pitch count from the summary.
  _atBat(d, lite) {
    const ab = d && d.atbat;
    const sit = (lite && lite.situation) || {};
    const P = (ab && ab.pitcher) || {}, B = (ab && ab.batter) || {};
    const pName = P.name || (sit.pitcher && sit.pitcher.name);
    const bName = B.name || (sit.batter && sit.batter.name);
    if (!pName && !bName) return "";
    const today = B.today || (sit.batter && sit.batter.summary);
    const bSub = [today ? `${today} today` : "", B.avg ? `${B.avg} AVG` : "", B.obp ? `${B.obp} OBP` : ""].filter(Boolean).join(" · ");
    const pSub = [P.pcst ? `${P.pcst} P-St` : (P.pc ? `${P.pc} pitches` : ""), P.ip ? `${P.ip} IP` : "", P.era ? `${P.era} ERA` : ""].filter(Boolean).join(" · ");
    return `<div class="atbat">
      ${bName ? `<div class="ab"><span class="k">⚾ At bat</span><b>${esc(bName)}</b>${bSub ? `<span class="sub">${esc(bSub)}</span>` : ""}</div>` : ""}
      ${pName ? `<div class="ab"><span class="k">Pitching</span><b>${esc(pName)}</b>${pSub ? `<span class="sub">${esc(pSub)}</span>` : ""}</div>` : ""}
    </div>`;
  }

  _navigate(path) {
    history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed", { bubbles: true, composed: true }));
  }

  _section(title, games, kind) {
    if (!games.length) return "";
    return `<section class="${kind}"><h2>${kind === "live" ? '<span class="dot"></span>' : ""}${esc(title)}
      <span class="count">${games.length}</span></h2><div class="grid">${games.map((g) => this._tile(g)).join("")}</div></section>`;
  }

  _tile(g) {
    const [a, h] = g.teams.length === 2 ? g.teams : [g.teams[0] || {}, g.teams[1] || {}];
    const label = `${a.abbr || a.name} @ ${h.abbr || h.name}`;
    // highlight the game that was put on (not every game sharing that channel)
    const onTv = g.state !== "post" && (this._nowLabel ? this._nowLabel === label : !!g.channel && g.channel === this._now);
    let status;
    if (g.state === "in") status = `<span class="livetag">LIVE</span> ${esc(g.detail)}`;
    else if (g.state === "pre") status = g.tbd ? "TBD" : esc(this._time(g.start));
    else status = esc(g.detail || "Final");
    const tappable = (g.channel || g.app || g.saved_stream) && g.state !== "post";
    const short = (this._short || {})[g.channel] || g.channel;
    const net = g.saved_stream ? `<span class="net saved ${onTv ? "on" : ""}">📌 Saved stream</span>`
      : g.channel ? `<span class="net ${onTv ? "on" : ""} ${g.regional ? "regional" : ""}">${onTv ? "📺 " : ""}${esc(short)}${g.regional ? " · regional" : ""}</span>`
      : g.app ? `<span class="net app">▶ ${esc(g.app)}</span>`
      : `<span class="net none">${esc((g.nets || [])[0] || "TV TBA")}</span>`;
    const alert = g.state === "in" && g.alert;
    const target = `data-game="${esc(g.id)}"`;
    return `
      <div class="tile ${g.state} ${tappable ? "tap" : "dim"} ${onTv ? "ontv" : ""} ${this._isFav(g) ? "fav" : ""} ${alert ? "alert " + esc(alert.kind) : ""}"
           data-gid="${esc(g.id)}" ${tappable ? `${target} data-label="${esc(label)}"` : ""}
           style="--c1:${hex(a.color, "#3a4a63")};--c2:${hex(h.color, "#63503a")}">
        <div class="top"><span class="lg">${esc(TAG[g.league] || g.league)}${this._isFav(g) ? " ★" : ""}</span>
          <span class="st">${status}<button class="info" data-info="${esc(g.id)}" aria-label="Game details">i</button></span></div>
        ${this._team(a, g)}${this._team(h, g)}
        ${alert ? `<div class="alertline">${{ close: "🔥", ot: "⏱", upset: "⚠" }[alert.kind] || "🔥"} ${esc(alert.text)}</div>` : ""}
        <div class="foot">${net}${this._situation(g)}</div>
      </div>`;
  }

  _team(t, g) {
    const lose = g.state === "post" && t.winner === false;
    return `<div class="team ${lose ? "lose" : ""} ${t.winner ? "win" : ""}">
      ${t.logo ? `<img src="${esc(t.logo.replace("/500/", "/500-dark/"))}" data-light="${esc(t.logo)}"
        onerror="if(this.dataset.light&&this.src!==this.dataset.light){this.src=this.dataset.light}" loading="lazy" alt="">`
        : `<span class="nologo"></span>`}
      ${t.rank ? `<span class="rk">${esc(t.rank)}</span>` : ""}
      <span class="nm">${esc(t.name)}</span>${t.poss ? '<span class="poss" title="possession">◀</span>' : ""}
      ${t.record ? `<span class="rec">${esc(t.record)}</span>` : ""}
      <span class="sc">${g.state === "pre" ? "" : esc(t.score ?? "")}</span></div>`;
  }

  _situation(g) {
    const s = g.situation || {};
    if (g.sport === "baseball" && g.state === "in" && s.outs !== undefined) {
      const b = (on, x, y) => `<rect x="${x}" y="${y}" width="7" height="7" transform="rotate(45 ${x + 3.5} ${y + 3.5})" class="${on ? "on" : ""}"/>`;
      const outs = [0, 1, 2].map((i) => `<i class="${i < (s.outs || 0) ? "on" : ""}"></i>`).join("");
      return `<span class="sit"><svg class="dia" viewBox="0 0 26 20">${b(s.onSecond, 9.5, 1)}${b(s.onThird, 2, 8.5)}${b(s.onFirst, 17, 8.5)}</svg>
        <span class="outs">${outs}</span>${s.balls !== undefined ? `<span class="cnt">${esc(s.balls)}-${esc(s.strikes)}</span>` : ""}</span>`;
    }
    if (g.sport === "football" && g.state === "in" && s.down) {
      return `<span class="sit ${s.redzone ? "rz" : ""}">${esc(s.down)}${s.redzone ? " · RED ZONE" : ""}</span>`;
    }
    return g.note ? `<span class="sit note">${esc(g.note)}</span>` : "";
  }

  // ---------------------------------------------------------------- game detail sheet
  _renderDetail() {
    const box = this._root?.getElementById("sheet");
    if (!box) return;
    if (!this._detailId) { box.className = "sheet-bg"; box.innerHTML = ""; return; }
    box.className = "sheet-bg open";
    const d = this._detail;
    const lite = (this._data?.games || []).find((g) => g.id === this._detailId);
    if (!d) {
      box.innerHTML = `<div class="sheet"><button class="x" data-close aria-label="Close">✕</button>
        <div class="empty">${this._detailErr ? "⚠ " + esc(this._detailErr) : "Loading game…"}</div></div>`;
      return;
    }
    const [a, h] = d.teams.length === 2 ? d.teams : [d.teams[0] || {}, d.teams[1] || {}];
    const live = d.state === "in";
    const logo = (t) => t.logo ? `<img src="${esc(t.logo.replace("/500/", "/500-dark/"))}" data-light="${esc(t.logo)}"
      onerror="if(this.dataset.light&&this.src!==this.dataset.light){this.src=this.dataset.light}" alt="">` : "";
    const head = (t) => `<div class="dteam">${logo(t)}<div class="dname">${esc(t.name)}
      ${t.record ? `<span class="rec">${esc(t.record)}</span>` : ""}</div><div class="dscore">${d.state === "pre" ? "" : esc(t.score ?? "")}</div></div>`;
    const ch = d.channel;
    const lbl = esc((a.abbr || "") + " @ " + (h.abbr || ""));
    const open = d.state !== "post";
    const gid = esc(d.game_id || this._detailId);
    const watch = (open && d.saved_stream
      ? `<button class="watch" data-game="${gid}" data-label="${lbl}">📌 Watch saved stream</button>`
      : open && ch
      ? `<button class="watch" data-game="${gid}" data-label="${lbl}">📺 Watch on ${esc(ch)}${d.regional ? " (regional)" : ""}</button>`
      : open && d.app
      ? `<button class="watch app" data-game="${gid}" data-label="${lbl}">▶ Open ${esc(d.app)}</button>`
      : `<span class="nowhere">${esc(d.state === "post" ? "Final" : (d.nets || [])[0] ? "On " + d.nets[0] + " (not in your lineup)" : "TV not announced")}</span>`)
      + (open ? `<button class="remember" data-remember="${gid}" data-label="${lbl}">📌 It's on the TV now — remember for this game</button>` : "")
      + (open && d.regional && !d.saved_stream ? `<div class="regionote">Regional game: your local ${esc(ch)} may be showing a different one. If it's on NFL Sunday Ticket, put it on there, then tap 📌.</div>` : "");
    const chips = [d.odds?.line && `Line ${d.odds.line}${d.odds.total ? " · O/U " + d.odds.total : ""}`, d.venue, d.weather,
      d.attendance && `${Number(d.attendance).toLocaleString()} fans`].filter(Boolean);
    box.innerHTML = `<div class="sheet" style="--c1:${hex(a.color, "#3a4a63")};--c2:${hex(h.color, "#63503a")}">
      <button class="x" data-close aria-label="Close">✕</button>
      <div class="dtop"><span class="lg">${esc(d.league || "")}</span>
        <span class="st">${live ? '<span class="livetag">LIVE</span> ' : ""}${esc(d.detail || "")}</span></div>
      <div class="dhead">${head(a)}<div class="at">@</div>${head(h)}</div>
      ${d.alert && live ? `<div class="alertline big">${esc(d.alert.text)}</div>` : ""}
      <div class="dactions">${watch}</div>
      ${this._lineScore(d)}
      ${this._atBat(d, lite)}
      ${this._wpChart(d, a, h)}
      ${d.leaders?.length ? `<h3>Leaders</h3><div class="leaders">${d.leaders.map((l) => `<div class="lcol"><div class="lteam">${esc(l.team)}</div>
        ${l.cats.map((c) => `<div class="lrow"><span class="lcat">${esc(c.cat)}</span><span class="lwho">${esc(c.who || "")}</span>
          <span class="lline">${esc(c.line)}</span></div>`).join("")}</div>`).join("")}</div>` : ""}
      ${d.plays?.length ? `<h3>${d.state === "post" ? "Last plays" : "Latest"}</h3><ol class="plays">${d.plays.map((p) => `<li>${esc(p)}</li>`).join("")}</ol>` : ""}
      ${chips.length ? `<div class="dchips">${chips.map((c) => `<span>${esc(c)}</span>`).join("")}</div>` : ""}
    </div>`;
  }

  _lineScore(d) {
    const [a, h] = d.teams.length === 2 ? d.teams : [d.teams[0] || {}, d.teams[1] || {}];
    const periods = Math.max(a.periods?.length || 0, h.periods?.length || 0);
    if (!periods) return "";
    return `<table class="lines"><tr><th></th>${Array.from({ length: periods }, (_, i) => `<th>${i + 1}</th>`).join("")}<th>T</th></tr>
      ${[a, h].map((t) => `<tr><td>${esc(t.abbr)}</td>${Array.from({ length: periods }, (_, i) => `<td>${esc((t.periods || [])[i] ?? "")}</td>`).join("")}
        <td class="tot">${esc(t.score ?? "")}</td></tr>`).join("")}</table>`;
  }

  _wpChart(d, a, h) {
    const wp = d.winprob || [];
    if (wp.length < 2) return "";
    const W = 600, H = 120, n = wp.length - 1;
    const pts = wp.map((v, i) => `${(i / n * W).toFixed(1)},${((100 - v) / 100 * H).toFixed(1)}`).join(" ");
    const now = wp[wp.length - 1];
    const fav = now >= 50 ? h : a;
    const pct = Math.round(now >= 50 ? now : 100 - now);
    return `<h3>Win probability <span class="wpnow">${esc(fav.abbr)} ${pct}%</span></h3>
      <svg class="wp" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        <rect x="0" y="0" width="${W}" height="${H / 2}" fill="var(--c2)" opacity=".18"/>
        <rect x="0" y="${H / 2}" width="${W}" height="${H / 2}" fill="var(--c1)" opacity=".18"/>
        <line x1="0" y1="${H / 2}" x2="${W}" y2="${H / 2}" stroke="#6f7d90" stroke-dasharray="4 5" stroke-width="1"/>
        <polyline points="${pts}" fill="none" stroke="#e8edf5" stroke-width="2.5" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
      </svg>
      <div class="wplabels"><span>▲ ${esc(h.abbr)}</span><span>▼ ${esc(a.abbr)}</span></div>`;
  }
}

const CSS = `
  :host { display:block; height:100%; }
  * { box-sizing:border-box; }
  .wrap { position:relative; min-height:100vh; height:100%; display:flex; flex-direction:column;
    color:#e8edf5; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    background: radial-gradient(1200px 600px at 10% -10%, #1d3557 0%, transparent 60%),
                radial-gradient(900px 500px at 110% 10%, #3a1d2e 0%, transparent 55%), #0b0f16; }
  #main { display:flex; flex-direction:column; flex:1; min-height:0; }
  header { display:flex; justify-content:space-between; align-items:center; gap:12px; padding:16px 20px 6px; flex-wrap:wrap; }
  .brand { font-weight:900; letter-spacing:.14em; font-size:22px; }
  .brand .ball { color:#ff3b30; font-size:16px; vertical-align:middle; }
  .homebtn { margin-right:14px; background:#18202c; border:1px solid #2a3547; color:#e8edf5; border-radius:12px;
    padding:8px 14px; font-size:15px; font-weight:700; letter-spacing:.02em; cursor:pointer; vertical-align:middle; }
  .homebtn:active { transform:scale(.95); }
  .brand .date { font-weight:500; letter-spacing:.02em; color:#9aa7b8; font-size:15px; margin-left:10px; }
  .meta { display:flex; gap:12px; align-items:center; color:#9aa7b8; font-size:14px; }
  .nowpill { background:#1f6f3f; color:#d9ffe6; padding:6px 12px; border-radius:999px; font-size:14px; }
  .errtxt { color:#ffb4a8; }
  .chips { display:flex; gap:8px; padding:8px 20px 10px; overflow-x:auto; scrollbar-width:none; }
  .chip { background:#18202c; color:#c9d3e0; border:1px solid #273244; border-radius:999px; padding:8px 16px;
    font-size:15px; font-weight:600; white-space:nowrap; cursor:pointer; }
  .chip.on { background:#e8edf5; color:#0b0f16; border-color:#e8edf5; }
  .chip.mine { margin-left:auto; }
  .chip.mine.on { background:#2e7d4f; color:#fff; border-color:#2e7d4f; }
  .scroll { flex:1; overflow-y:auto; padding:0 20px 90px; -webkit-overflow-scrolling:touch; }
  h2 { font-size:13px; letter-spacing:.16em; text-transform:uppercase; color:#9aa7b8; margin:16px 2px 10px; display:flex; align-items:center; gap:8px; }
  h2 .count { background:#18202c; color:#c9d3e0; border-radius:999px; padding:2px 9px; font-size:12px; letter-spacing:0; }
  h2 .dot, .livetag::before { width:9px; height:9px; border-radius:50%; background:#ff3b30; display:inline-block; animation:pulse 1.6s infinite; }
  @keyframes pulse { 0%{box-shadow:0 0 0 0 rgba(255,59,48,.6)} 70%{box-shadow:0 0 0 9px rgba(255,59,48,0)} 100%{box-shadow:0 0 0 0 rgba(255,59,48,0)} }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(290px, 1fr)); gap:12px; }
  .tile { position:relative; background:#141b26; border:1px solid #222c3b; border-radius:16px; padding:12px 14px 10px;
    overflow:hidden; transition:transform .12s ease, border-color .2s; }
  .tile::before { content:""; position:absolute; inset:0 0 auto 0; height:4px; background:linear-gradient(90deg, var(--c1), var(--c2)); }
  .tile.tap { cursor:pointer; }
  .tile.tap:active, .tile.tapped { transform:scale(.97); border-color:#e8edf5; }
  .tile.dim { opacity:.62; }
  .tile.final.dim, .tile.post { opacity:.8; }
  .tile.ontv { border-color:#3ddc84; box-shadow:0 0 0 2px rgba(61,220,132,.35), 0 8px 30px rgba(61,220,132,.15); }
  .tile.fav { background:#18202f; }
  .top { display:flex; justify-content:space-between; align-items:center; font-size:12.5px; color:#9aa7b8; margin-bottom:8px; }
  .lg { text-transform:uppercase; letter-spacing:.1em; font-weight:700; white-space:nowrap; }
  .st { font-weight:700; color:#e8edf5; font-variant-numeric:tabular-nums; white-space:nowrap; display:flex; align-items:center; }
  .livetag { color:#ff6b61; font-weight:800; letter-spacing:.08em; margin-right:4px; }
  .livetag::before { content:""; margin-right:5px; vertical-align:1px; }
  .team { display:flex; align-items:center; gap:9px; padding:4px 0; font-size:18px; }
  .team img, .nologo { width:30px; height:30px; object-fit:contain; flex:none; }
  .nologo { border-radius:50%; background:#273244; display:inline-block; }
  .rk { font-size:12px; color:#9aa7b8; font-weight:700; }
  .nm { font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .poss { color:#f5c84b; font-size:12px; }
  .rec { color:#6f7d90; font-size:13px; }
  .sc { margin-left:auto; font-size:24px; font-weight:800; font-variant-numeric:tabular-nums; }
  .team.lose { color:#7d8899; } .team.win .sc { color:#fff; }
  .foot { display:flex; justify-content:space-between; align-items:center; margin-top:8px; gap:8px; min-height:26px; }
  .net { background:#233046; color:#dfe8f5; border-radius:8px; padding:4px 10px; font-weight:800; font-size:13px; letter-spacing:.04em; }
  .net.on { background:#2e7d4f; color:#fff; }
  .net.app { background:#3b2a5c; color:#e6dcff; }
  .net.regional { background:#4a3a14; color:#ffd98a; }
  .net.saved { background:#14404a; color:#bff3ff; }
  .remember { margin-left:10px; background:transparent; border:1px solid #3a4a63; color:#c9d3e0; border-radius:14px;
    padding:11px 14px; font-size:14px; font-weight:700; cursor:pointer; }
  .regionote { flex-basis:100%; text-align:center; color:#ffd98a; font-size:13px; margin-top:8px; }
  .dactions { flex-wrap:wrap; gap:8px; }
  .toast.warn { background:#ffe6a8; }
  .watch.app { background:#4b3478; border-color:#9b7bff; }
  .net.none { background:transparent; color:#6f7d90; font-weight:600; padding-left:0; }
  .sit { color:#c9d3e0; font-size:13px; display:flex; align-items:center; gap:8px; text-align:right; }
  .sit.rz { color:#ff8a80; font-weight:700; }
  .sit.note { color:#7d8899; font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:60%; }
  .dia { width:30px; height:22px; } .dia rect { fill:none; stroke:#6f7d90; stroke-width:1.4; } .dia rect.on { fill:#f5c84b; stroke:#f5c84b; }
  .outs { display:flex; gap:3px; } .outs i { width:7px; height:7px; border-radius:50%; border:1.4px solid #6f7d90; display:inline-block; }
  .outs i.on { background:#ff6b61; border-color:#ff6b61; }
  .cnt { font-variant-numeric:tabular-nums; color:#9aa7b8; }
  .empty { color:#9aa7b8; text-align:center; padding:60px 0; font-size:18px; }
  .chanbar { position:sticky; bottom:0; display:flex; gap:8px; overflow-x:auto; padding:12px 20px 16px; scrollbar-width:none;
    background:linear-gradient(180deg, rgba(11,15,22,0) 0%, rgba(11,15,22,.92) 30%, #0b0f16 100%); }
  .chan { flex:none; background:#18202c; border:1px solid #2a3547; color:#e8edf5; border-radius:12px; padding:10px 16px;
    font-weight:800; font-size:15px; letter-spacing:.03em; cursor:pointer; }
  .chan.on { background:#2e7d4f; border-color:#3ddc84; }
  .chan:active { transform:scale(.95); }
  .info { margin-left:8px; width:22px; height:22px; border-radius:50%; border:1.5px solid #6f7d90; background:transparent;
    color:#c9d3e0; font:italic 700 12px/1 Georgia, serif; cursor:pointer; vertical-align:middle; padding:0; }
  .tile.alert { border-color:#f5a524; animation:glow 2.2s ease-in-out infinite; }
  .tile.alert.upset { border-color:#ff6b61; }
  @keyframes glow { 0%,100% { box-shadow:0 0 0 1px rgba(245,165,36,.35); } 50% { box-shadow:0 0 0 3px rgba(245,165,36,.55), 0 0 26px rgba(245,165,36,.25); } }
  .alertline { margin-top:6px; color:#ffcf70; font-size:13px; font-weight:700; letter-spacing:.02em; }
  .tile.upset .alertline { color:#ff9a90; }
  .alertline.big { font-size:15px; text-align:center; margin:4px 0 10px; }
  .nowtv { position:relative; margin:14px 0 4px; background:linear-gradient(180deg,#10261b,#121924); border:1px solid #2e7d4f;
    border-radius:16px; padding:12px 14px 10px; cursor:pointer; box-shadow:0 0 0 1px rgba(61,220,132,.25), 0 10px 30px rgba(61,220,132,.12); }
  .nowtv::before { content:""; position:absolute; inset:0 0 auto 0; height:4px; border-radius:16px 16px 0 0; background:linear-gradient(90deg, var(--c1), var(--c2)); }
  .nowtv-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; font-size:13px; color:#9aa7b8; }
  .nowtv-head .tag { font-weight:900; letter-spacing:.1em; text-transform:uppercase; color:#8affc0; font-size:12px; }
  .nowtv-head .st { font-weight:700; color:#e8edf5; display:flex; align-items:center; }
  .nowtv-head .net { margin-left:2px; }
  .nowtv-head .sit { margin-left:0; }
  .nowx { margin-left:auto; width:26px; height:26px; border-radius:50%; border:none; background:#1f2836; color:#c9d3e0; font-size:13px; cursor:pointer; flex:none; }
  .nowtv-body { display:grid; grid-template-columns:minmax(230px,1fr) minmax(300px,1.2fr); gap:14px 20px; align-items:center; margin-top:8px; }
  .nowleft { min-width:0; } .nowleft .lines { margin-top:12px; }
  .nowwp { min-width:0; } .nowwp .wp { height:120px; } .nowwp h3 { margin-top:0; }
  .nowteams { display:flex; flex-direction:column; gap:4px; }
  .ntrow { display:flex; align-items:center; gap:9px; font-size:18px; }
  .ntrow img, .ntrow .nologo { width:28px; height:28px; object-fit:contain; flex:none; }
  .ntrow .nm { font-weight:800; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .ntrow .rec { color:#6f7d90; font-size:12px; }
  .ntrow .sc { margin-left:auto; font-size:26px; font-weight:900; font-variant-numeric:tabular-nums; }
  .ntrow.lose { color:#7d8899; }
  .nowtv .wp { height:88px; }
  .nowtv h3 { margin:6px 0 4px; }
  .nowleaders { display:flex; flex-wrap:wrap; gap:8px 16px; margin-top:8px; font-size:13px; color:#c9d3e0; }
  .nowleaders .nl b { color:#8affc0; margin-right:4px; }
  .nowplay { margin-top:8px; font-size:14px; color:#e8edf5; }
  .nowplay .k { font-size:10px; letter-spacing:.14em; color:#7d8899; margin-right:6px; }
  .nowmore { text-align:right; color:#8fc1ff; font-size:12px; font-weight:700; margin-top:6px; }
  .atbat { display:flex; flex-wrap:wrap; gap:8px 26px; margin-top:10px; padding-top:10px; border-top:1px solid #203042; }
  .atbat .ab { display:flex; align-items:baseline; gap:8px; font-size:15px; min-width:0; }
  .atbat .ab .k { font-size:10px; letter-spacing:.12em; text-transform:uppercase; color:#7d8899; white-space:nowrap; }
  .atbat .ab b { color:#e8edf5; font-weight:800; white-space:nowrap; }
  .atbat .ab .sub { color:#9aa7b8; font-size:13px; }
  @media (max-width: 700px) { .nowtv-body { grid-template-columns:1fr; } .atbat { gap:6px 14px; } }
  .sheet-bg { position:fixed; inset:0; background:rgba(5,8,12,.72); display:none; align-items:flex-end; justify-content:center; z-index:5; }
  .sheet-bg.open { display:flex; }
  .sheet { position:relative; width:min(760px, 100%); max-height:88vh; overflow-y:auto; background:#121924; border:1px solid #273244;
    border-bottom:none; border-radius:22px 22px 0 0; padding:18px 20px 26px; animation:up .22s ease-out; }
  .sheet::before { content:""; position:absolute; inset:0 0 auto 0; height:5px; border-radius:22px 22px 0 0;
    background:linear-gradient(90deg, var(--c1), var(--c2)); }
  @keyframes up { from { transform:translateY(40px); opacity:.4; } to { transform:none; opacity:1; } }
  .x { position:absolute; top:12px; right:14px; width:36px; height:36px; border-radius:50%; border:none; background:#1f2836;
    color:#e8edf5; font-size:16px; cursor:pointer; }
  .dtop { display:flex; gap:12px; align-items:center; color:#9aa7b8; font-size:13px; margin:4px 44px 10px 0; }
  .dhead { display:flex; align-items:center; gap:10px; }
  .dteam { flex:1; display:flex; align-items:center; gap:10px; min-width:0; }
  .dteam img { width:48px; height:48px; object-fit:contain; }
  .dname { font-size:19px; font-weight:800; min-width:0; overflow:hidden; text-overflow:ellipsis; }
  .dname .rec { display:block; font-weight:500; }
  .dscore { margin-left:auto; font-size:38px; font-weight:900; font-variant-numeric:tabular-nums; }
  .at { color:#6f7d90; font-weight:700; padding:0 4px; }
  .dactions { display:flex; justify-content:center; margin:12px 0 6px; }
  .watch { background:#2e7d4f; border:1px solid #3ddc84; color:#fff; border-radius:14px; padding:12px 22px; font-size:16px; font-weight:800; cursor:pointer; }
  .nowhere { color:#7d8899; font-size:14px; }
  h3 { font-size:12px; letter-spacing:.16em; text-transform:uppercase; color:#9aa7b8; margin:18px 0 8px; display:flex; justify-content:space-between; }
  .wpnow { letter-spacing:.04em; color:#e8edf5; }
  .lines { width:100%; border-collapse:collapse; margin-top:10px; font-variant-numeric:tabular-nums; font-size:14px; }
  .lines th, .lines td { text-align:center; padding:5px 4px; border-bottom:1px solid #1f2836; color:#c9d3e0; }
  .lines th { color:#7d8899; font-weight:600; } .lines td:first-child { text-align:left; font-weight:700; }
  .lines .tot { font-weight:900; color:#fff; }
  .wp { width:100%; height:110px; display:block; border-radius:10px; background:#0e141d; }
  .wplabels { display:flex; justify-content:space-between; color:#7d8899; font-size:12px; margin-top:4px; }
  .leaders { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  .lteam { font-weight:800; margin-bottom:4px; }
  .lrow { display:grid; grid-template-columns:1fr; font-size:13px; padding:5px 0; border-bottom:1px solid #1f2836; }
  .lcat { color:#7d8899; font-size:11px; text-transform:uppercase; letter-spacing:.08em; }
  .lwho { font-weight:700; } .lline { color:#c9d3e0; }
  .plays { margin:0; padding-left:20px; color:#c9d3e0; font-size:14px; line-height:1.4; }
  .plays li { padding:4px 0; } .plays li:first-child { color:#fff; font-weight:600; }
  .dchips { display:flex; flex-wrap:wrap; gap:8px; margin-top:16px; }
  .dchips span { background:#1b2432; color:#c9d3e0; border-radius:999px; padding:6px 12px; font-size:13px; }
  @media (max-width: 700px) { .leaders { grid-template-columns:1fr; } .dscore { font-size:30px; } .dteam img { width:38px; height:38px; } }
  .wrap.strip { min-height:0; height:auto; border-radius:var(--ha-card-border-radius, 12px); padding:10px 0 12px;
    border:1px solid #222c3b; }
  .shead { display:flex; align-items:center; gap:10px; padding:0 14px 8px; }
  .shead .brand { font-size:14px; letter-spacing:.16em; }
  .smeta { color:#9aa7b8; font-size:13px; margin-left:auto; }
  .open, .sempty a { color:#8fc1ff; font-weight:700; font-size:13px; cursor:pointer; text-decoration:none; }
  .srow { display:flex; gap:10px; overflow-x:auto; padding:2px 14px 4px; scrollbar-width:none; }
  .sempty { color:#9aa7b8; font-size:14px; padding:14px 0; }
  .mini { flex:none; width:200px; padding:9px 11px 8px; border-radius:14px; }
  .mtop { display:flex; justify-content:space-between; font-size:11px; color:#9aa7b8; margin-bottom:4px; gap:6px; }
  .mtop .st { font-size:11px; }
  .mrow { display:flex; align-items:center; gap:7px; font-size:15px; font-weight:700; padding:2px 0; }
  .mrow img { width:22px; height:22px; object-fit:contain; }
  .mrow .msc { margin-left:auto; font-size:18px; font-weight:800; font-variant-numeric:tabular-nums; }
  .mrow.lose { color:#7d8899; }
  .mfoot { margin-top:5px; min-height:20px; }
  .mfoot .net { font-size:11px; padding:2px 8px; }
  .malert { color:#ffcf70; font-size:11.5px; font-weight:700; }
  .toast { position:fixed; z-index:6; left:50%; bottom:86px; transform:translate(-50%, 20px); opacity:0; pointer-events:none;
    background:#e8edf5; color:#0b0f16; padding:12px 20px; border-radius:14px; font-size:16px; transition:all .25s;
    box-shadow:0 10px 30px rgba(0,0,0,.4); max-width:90%; }
  .toast.show { opacity:1; transform:translate(-50%, 0); }
  .toast.err { background:#ffb4a8; } .toast.ok { background:#c8f7d8; }
  .toast .soft { opacity:.6; font-size:14px; }
  @media (max-width: 700px) { .brand { font-size:18px; } .brand .date { display:none; } .grid { grid-template-columns:1fr; } }
`;

if (!customElements.get("youtube-tv-gameday-card")) {
  customElements.define("youtube-tv-gameday-card", YouTubeTvGamedayCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "youtube-tv-gameday-card", name: "YouTube TV Gameday",
    description: "Live scores; tap a game to put it on your TV.", preview: false });
}
