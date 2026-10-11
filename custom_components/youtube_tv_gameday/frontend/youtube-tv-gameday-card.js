/*
 * youtube-tv-gameday-card — live scoreboard; tap a game to put it on your TV via YouTube TV.
 * Data: websocket `youtube_tv_gameday/scoreboard` (ESPN scores + each game's channel in your lineup).
 * Tap: service youtube_tv_gameday.tune_channel.
 * Config (all optional): { type: custom:youtube-tv-gameday-card, favorites: [Guardians], refresh: 30, entry_id: ... }
 * Unofficial; not affiliated with Google, YouTube or ESPN.
 */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hex = (c, d) => (/^[0-9a-f]{6}$/i.test(c || "") ? "#" + c : d);

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
  disconnectedCallback() { clearInterval(this._timer); this._timer = null; }
  getCardSize() { return 12; }

  _build() {
    this._root = this.attachShadow({ mode: "open" });
    this._root.innerHTML = `<style>${CSS}</style><div class="wrap"><div id="main"></div><div class="toast" id="toast"></div></div>`;
    this._root.addEventListener("click", (ev) => {
      const el = ev.target.closest("[data-ch],[data-filter],[data-mine]");
      if (!el) return;
      if (el.dataset.filter) { this._filter = el.dataset.filter; this._save(); this._render(); }
      else if (el.dataset.mine !== undefined) { this._mine = !this._mine; this._save(); this._render(); }
      else if (el.dataset.ch) this._tune(el.dataset.ch, el.dataset.label, el);
    });
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
      if (!this._config.favorites) this._favs = (this._data.favorites || []).map((f) => f.toLowerCase());
      this._short = Object.fromEntries((this._data.quick || []).map((q) => [q.channel, q.label]));
    } catch (e) {
      this._error = (e && (e.message || e.code)) || "couldn't load scores";
    }
    this._render();
  }

  async _tune(ch, label, el) {
    if (this._busy) return;
    this._busy = true;
    el?.classList.add("tapped");
    this._toast(`Switching the TV to <b>${esc(ch)}</b>${label ? " · " + esc(label) : ""}…`, "busy");
    try {
      const data = { channel: ch };
      if (label) data.label = label;
      if (this._config.entry_id) data.config_entry_id = this._config.entry_id;
      await this._hass.callService("youtube_tv_gameday", "tune_channel", data);
      this._now = ch;
      this._toast(`📺 Now on <b>${esc(ch)}</b>`, "ok");
      this._render();
    } catch (e) {
      this._toast(`Couldn't tune ${esc(ch)}: ${esc(e.message || e)}`, "err");
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

  _isFav(g) {
    return g.teams.some((t) => [t.name, t.full, t.abbr].some((n) => n && this._favs.includes(String(n).toLowerCase())));
  }

  _render() {
    const main = this._root?.getElementById("main");
    if (!main) return;
    const d = this._data;
    const games = (d?.games || []).filter((g) =>
      (this._filter === "All" || g.league === this._filter) && (!this._mine || g.channel));
    const leagues = ["All", ...new Set((d?.games || []).map((g) => g.league))];
    const byFav = (a, b) => (this._isFav(b) - this._isFav(a));
    const live = games.filter((g) => g.state === "in").sort((a, b) => byFav(a, b) || (!!b.channel - !!a.channel));
    const next = games.filter((g) => g.state === "pre").sort((a, b) => byFav(a, b) || a.start.localeCompare(b.start));
    const done = games.filter((g) => g.state === "post").sort((a, b) => byFav(a, b) || b.start.localeCompare(a.start));
    const today = new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
    const upd = d?.updated ? new Date(d.updated).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "—";

    main.innerHTML = `
      <header>
        <div class="brand"><span class="ball">●</span> GAME DAY <span class="date">${esc(today)}</span></div>
        <div class="meta">
          ${this._now ? `<span class="nowpill">📺 On TV: <b>${esc(this._now)}</b></span>` : ""}
          <span class="upd">${this._error ? `<span class="errtxt">⚠ ${esc(this._error)}</span>` : `Updated ${esc(upd)}`}</span>
        </div>
      </header>
      <nav class="chips">
        ${leagues.map((l) => `<button class="chip ${l === this._filter ? "on" : ""}" data-filter="${esc(l)}">${esc(l)}</button>`).join("")}
        <button class="chip mine ${this._mine ? "on" : ""}" data-mine>On my channels</button>
      </nav>
      <div class="scroll">
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

  _section(title, games, kind) {
    if (!games.length) return "";
    return `<section class="${kind}"><h2>${kind === "live" ? '<span class="dot"></span>' : ""}${esc(title)}
      <span class="count">${games.length}</span></h2><div class="grid">${games.map((g) => this._tile(g)).join("")}</div></section>`;
  }

  _tile(g) {
    const [a, h] = g.teams.length === 2 ? g.teams : [g.teams[0] || {}, g.teams[1] || {}];
    const onTv = g.channel && g.channel === this._now && g.state !== "post";
    const label = `${a.abbr || a.name} @ ${h.abbr || h.name}`;
    let status;
    if (g.state === "in") status = `<span class="livetag">LIVE</span> ${esc(g.detail)}`;
    else if (g.state === "pre") status = g.tbd ? "TBD" : esc(new Date(g.start).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));
    else status = esc(g.detail || "Final");
    const tappable = g.channel && g.state !== "post";
    const net = g.channel ? `<span class="net ${onTv ? "on" : ""}">${onTv ? "📺 " : ""}${esc((this._short || {})[g.channel] || g.channel)}</span>`
      : `<span class="net none">${esc((g.nets || [])[0] || "TV TBA")}</span>`;
    return `
      <div class="tile ${g.state} ${tappable ? "tap" : "dim"} ${onTv ? "ontv" : ""} ${this._isFav(g) ? "fav" : ""}"
           ${tappable ? `data-ch="${esc(g.channel)}" data-label="${esc(label)}"` : ""}
           style="--c1:${hex(a.color, "#3a4a63")};--c2:${hex(h.color, "#63503a")}">
        <div class="top"><span class="lg">${esc(g.league)}${this._isFav(g) ? " ★" : ""}</span><span class="st">${status}</span></div>
        ${this._team(a, g)}${this._team(h, g)}
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
  .lg { text-transform:uppercase; letter-spacing:.1em; font-weight:700; }
  .st { font-weight:700; color:#e8edf5; font-variant-numeric:tabular-nums; }
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
  .toast { position:fixed; left:50%; bottom:86px; transform:translate(-50%, 20px); opacity:0; pointer-events:none;
    background:#e8edf5; color:#0b0f16; padding:12px 20px; border-radius:14px; font-size:16px; transition:all .25s;
    box-shadow:0 10px 30px rgba(0,0,0,.4); max-width:90%; }
  .toast.show { opacity:1; transform:translate(-50%, 0); }
  .toast.err { background:#ffb4a8; } .toast.ok { background:#c8f7d8; }
  @media (max-width: 700px) { .brand { font-size:18px; } .brand .date { display:none; } .grid { grid-template-columns:1fr; } }
`;

if (!customElements.get("youtube-tv-gameday-card")) {
  customElements.define("youtube-tv-gameday-card", YouTubeTvGamedayCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "youtube-tv-gameday-card", name: "YouTube TV Gameday",
    description: "Live scores; tap a game to put it on your TV.", preview: false });
}
