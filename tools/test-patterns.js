// Run: node tools/test-patterns.js
// Loads the pattern engine straight out of static/index.html (between the
// "Chart data and pattern recognition" and "Charts tab" markers) and checks it
// against synthetic shapes and the GKOS sample in data/seed/hist.
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(root, "static", "index.html"), "utf8");
const a = html.indexOf("/* ---------- Chart data and pattern recognition");
const b = html.indexOf("/* ---------- Charts tab");
if (a < 0 || b < 0) throw new Error("pattern engine markers not found in static/index.html");
const f2 = v => v == null ? "–" : (+v).toFixed(2);
const E = new Function("f2", html.slice(a, b) + "\nreturn {parseBars,toWeekly,detectPatterns,trendStage,rsProxy,smaOf};")(f2);
const { parseBars, toWeekly, detectPatterns, trendStage, rsProxy } = E;

function mk(closes, vol) {
  let d = new Date("2025-01-02T00:00:00Z"), prev = closes[0]; const out = [];
  closes.forEach((c, i) => {
    while ([0, 6].includes(d.getUTCDay())) d = new Date(d.getTime() + 864e5);
    const o = prev, nz = c * 0.006;
    out.push([d.toISOString().slice(0, 10).replace(/-/g, ""), o.toFixed(2), (Math.max(o, c) + nz).toFixed(2), (Math.min(o, c) - nz).toFixed(2), c.toFixed(2), vol ? vol(i) : 1000].join(","));
    prev = c; d = new Date(d.getTime() + 864e5);
  });
  return parseBars(out.join(";"));
}
const seg = (x, y, n) => Array.from({ length: n }, (_, i) => x + (y - x) * (i + 1) / n);
const wig = (x, y, n, amp) => Array.from({ length: n }, (_, i) => x + (y - x) * (i + 1) / n + amp * Math.sin((i + 1) / 3));
let fails = 0;
function expect(label, bars, key, W = false) {
  const got = detectPatterns(bars, W);
  const ok = got.some(p => p.key === key);
  if (!ok) fails++;
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}: ${got.map(p => `${p.name} [${p.status}] pivot ${f2(p.pivot)}`).join("; ") || "nothing"}`);
}
let p = [50, ...seg(50, 100, 60)]; for (let i = 1; i <= 60; i++) p.push(100 - 28 * Math.sin(Math.PI * i / 60)); p.push(...seg(99, 92, 6), ...seg(92, 97, 6));
expect("cup with handle", mk(p), "cup");
expect("flag", mk([...seg(40, 41, 150), ...seg(41, 55, 10), ...seg(55, 52, 8)]), "flag");
expect("double bottom", mk([...seg(60, 80, 80), ...seg(80, 62, 20), ...seg(62, 72, 15), ...seg(72, 61.5, 15), ...seg(61.5, 70, 10)]), "dbot");
expect("VCP", mk([...seg(50, 100, 80), ...seg(100, 75, 15), ...seg(75, 98, 15), ...seg(98, 86, 10), ...seg(86, 97, 10), ...seg(97, 91, 6), ...seg(91, 95, 5)]), "vcp");
expect("MA squeeze coil", mk([30, ...seg(30, 16, 100), ...wig(16, 16.4, 200, 0.7), ...wig(16.6, 16.9, 30, 0.12)], i => i > 300 ? 600 : 1000), "coil");
const gk = path.join(root, "data", "seed", "hist", "GKOS.json");
if (fs.existsSync(gk)) {
  const d = parseBars(JSON.parse(fs.readFileSync(gk, "utf8")).bars);
  console.log("\nGKOS daily :", detectPatterns(d, false).map(x => `${x.name} [${x.status}]`).join("; "));
  console.log("GKOS weekly:", detectPatterns(toWeekly(d), true).map(x => `${x.name} [${x.status}]`).join("; "));
  console.log("GKOS trend :", JSON.stringify(trendStage(d)), "RS proxy", f2((rsProxy(d) || 0) * 100) + "%");
}
process.exit(fails ? 1 : 0);
