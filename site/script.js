// ---- settings: fill these in once the project is published -------------------------------------
const CONFIG = {
  repo: "omrin/kulmus",   // GitHub repository whose Releases hold the installers
  counterEndpoint: "",                      // optional, own server: "api/stats.php" (see api/README)
  downloadVia: "",                          // optional, own server: "api/download.php?file="
  goatcounter: "",                          // visit counter: the code chosen at goatcounter.com, e.g. "kulmus"
};
// --------------------------------------------------------------------------------------------------

const configured = !CONFIG.repo.startsWith("YOUR-");
const releaseUrl = (asset) => `https://github.com/${CONFIG.repo}/releases/latest/download/${asset}`;
const hrefFor = (asset) => CONFIG.downloadVia ? CONFIG.downloadVia + encodeURIComponent(asset) : releaseUrl(asset);

document.getElementById("source-link").href = configured ? `https://github.com/${CONFIG.repo}` : "#";
document.querySelectorAll("[data-gh]").forEach((a) => { if (configured) a.href = `https://github.com/${CONFIG.repo}`; });
document.querySelectorAll("[data-issue]").forEach((a) => {
  a.href = configured ? `https://github.com/${CONFIG.repo}/issues/new?template=${a.dataset.issue}` : "#contact";
  a.rel = "noopener";
});
document.querySelectorAll("[data-path]").forEach((a) => {
  a.href = configured ? `https://github.com/${CONFIG.repo}/${a.dataset.path}` : "#contact";
  a.rel = "noopener";
});

// visit counter: GoatCounter counts page views without cookies and without personal data
if (CONFIG.goatcounter) {
  const gc = document.createElement("script");
  gc.async = true;
  gc.src = "https://gc.zgo.at/count.js";
  gc.dataset.goatcounter = `https://${CONFIG.goatcounter}.goatcounter.com/count`;
  document.body.appendChild(gc);
  document.getElementById("privacy-note").hidden = false;
}

document.querySelectorAll("[data-asset]").forEach((a) => {
  a.href = configured ? hrefFor(a.dataset.asset) : "#download";
  a.rel = "noopener";
});

// suggest the visitor's own system
function detectOS() {
  const p = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || navigator.userAgent;
  if (/win/i.test(p)) return "win";
  if (/mac|iphone|ipad/i.test(p)) return "mac";
  if (/linux|x11/i.test(p) && !/android/i.test(navigator.userAgent)) return "linux";
  return null;
}
const os = detectOS();
const labels = { win: "הורדה ל-Windows", mac: "הורדה ל-Mac", linux: "הורדה ל-Linux" };
if (os) {
  const card = document.querySelector(`.os[data-os="${os}"]`);
  if (card) {
    card.classList.add("mine");
    card.querySelector(".tag").textContent = "המחשב שלך";
    const first = card.querySelector("[data-asset]");
    const hero = document.getElementById("hero-dl");
    hero.textContent = labels[os] + " · חינם";
    hero.href = first.href;
  }
}

// download counts: GitHub keeps a count per release file; an own-server counter can be added
const fmt = (n) => n.toLocaleString("he-IL");
async function loadCounts() {
  const counts = {};
  try {
    if (configured) {
      const r = await fetch(`https://api.github.com/repos/${CONFIG.repo}/releases?per_page=100`);
      if (r.ok) {
        const releases = await r.json();
        for (const rel of releases) for (const a of rel.assets || []) counts[a.name] = (counts[a.name] || 0) + a.download_count;
      }
    }
    if (CONFIG.counterEndpoint) {
      const r = await fetch(CONFIG.counterEndpoint, { cache: "no-store" });
      if (r.ok) for (const [k, v] of Object.entries(await r.json())) counts[k] = Math.max(counts[k] || 0, v);
    }
  } catch (e) { /* offline or blocked: the page works without the numbers */ }
  const total = Object.values(counts).reduce((s, n) => s + n, 0);
  if (total > 0) {
    document.getElementById("total-count").innerHTML = `כבר <b>${fmt(total)}</b> הורדות`;
    document.querySelectorAll("[data-count]").forEach((el) => {
      const n = el.dataset.count.split(" ").reduce((s, k) => s + (counts[k] || 0), 0);
      el.innerHTML = n ? `<b>${fmt(n)}</b> הורדות` : "";
    });
  }
}
loadCounts();
