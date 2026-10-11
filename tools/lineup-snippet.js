// YouTube TV Gameday — copy your YouTube TV channel lineup.
// 1. Open https://tv.youtube.com/live in a desktop browser (signed in).
// 2. Open the developer console (F12 -> Console), paste this whole snippet, press Enter.
// 3. Wait for "N channels copied", then paste into the integration's "Channel lineup" box.
// It scrolls the guide to load every channel and reads each channel's live link.
// Nothing is sent anywhere; it only reads the page and copies text to your clipboard.
(async () => {
  const out = {};
  const grab = () => {
    let channel = null;
    for (const a of document.querySelectorAll("a[href]")) {
      const href = a.getAttribute("href") || "";
      const label = (a.getAttribute("aria-label") || a.textContent || "").trim().replace(/\s+/g, " ");
      if (href.includes("/browse/") && label && label !== "..." && !/^\d{1,2}:\d\d/.test(label)) channel = label;
      const m = href.match(/\/watch\/([A-Za-z0-9_-]{11})/);
      if (m && channel && !(channel in out)) out[channel] = m[1];
    }
  };
  let last = -1, still = 0;
  for (let i = 0; i < 80 && still < 4; i++) {
    grab();
    const rows = [...document.querySelectorAll('a[href*="/browse/"]')];
    rows[rows.length - 1]?.scrollIntoView({ block: "end" });
    await new Promise((r) => setTimeout(r, 700));
    const n = Object.keys(out).length;
    still = n === last ? still + 1 : 0;
    last = n;
  }
  grab();
  const text = JSON.stringify(out, null, 1);
  try { await navigator.clipboard.writeText(text); } catch (e) { /* console copy() below still works */ }
  if (typeof copy === "function") copy(text);
  console.log(`${Object.keys(out).length} channels copied to your clipboard.`);
  return out;
})();
