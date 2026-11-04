/* Local-only status strip (injected by desk/server.py, never part of the claude.ai artifact):
   scheduled-job status, market regime source, run-now buttons, sign out. */
(function(){
  "use strict";
  const el = document.createElement("div");
  el.id = "localPanel";
  el.style.cssText = "position:fixed;right:12px;bottom:12px;z-index:50;max-width:min(560px,calc(100vw - 24px));" +
    "font:12px/1.4 system-ui,sans-serif;background:rgba(20,22,28,.92);color:#ddd;border:1px solid #333;" +
    "border-radius:10px;padding:8px 10px;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center";
  const btn = "font:inherit;padding:3px 8px;border-radius:6px;border:1px solid #444;background:#262a33;color:#ddd;cursor:pointer";
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const dot = s => s === "ok" ? "#3c3" : s === "running" ? "#fc3" : s === "error" ? "#e55" : "#777";
  let open = false, data = null;
  async function load(){
    try{ const r = await fetch("/api/jobs"); if(r.status === 401){ location.href = "/login"; return; } data = await r.json(); }catch(e){ data = null; }
    render();
  }
  function when(iso){ if(!iso) return "never"; const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString([], {weekday:"short", hour:"2-digit", minute:"2-digit"}); }
  function render(){
    const j = (data && data.jobs) || {}, sc = (data && data.schedule) || {};
    const d = j.daily || {};
    const narrow = window.innerWidth < 700;
    if(narrow && !open){
      el.innerHTML = `<button data-toggle style="${btn};display:flex;gap:6px;align-items:center"><span style="color:${dot(d.status)}">●</span> Jobs</button>`;
      el.style.padding = "4px";
      return;
    }
    el.style.padding = "8px 10px";
    let h = `<span title="Daily routine"><span style="color:${dot(d.status)}">●</span> Daily ${esc(d.status || "not run")} · ${esc(when(d.finished || d.started))}</span>`;
    h += `<button data-job="daily" style="${btn}">Run daily</button><button data-job="backup" style="${btn}">Backup</button>`;
    h += `<button data-toggle style="${btn}">${open ? "Hide" : "Details"}</button>`;
    h += `<form method="post" action="/logout" style="margin:0"><button style="${btn}">Sign out</button></form>`;
    if(open){
      const rows = ["daily","update","news","backup"].map(k => { const x = j[k] || {};
        return `<div><span style="color:${dot(x.status)}">●</span> <b>${k}</b> ${esc(x.status || "–")} · ${esc(when(x.finished || x.started))}</div>`; }).join("");
      const out = esc((d.output || "").slice(-1500));
      h += `<div style="flex-basis:100%">${rows}<div style="margin-top:4px;color:#aaa">Schedule (${esc(data.tz || "server time")}): daily ${esc(sc.daily)} on days ${esc((sc.days||[]).join(","))}, backup ${esc(sc.backup)}${sc.news ? ", news " + esc(sc.news) : ""}</div>` +
           (out ? `<pre style="white-space:pre-wrap;max-height:240px;overflow:auto;margin:6px 0 0;background:#111;padding:6px;border-radius:6px">${out}</pre>` : "") + `</div>`;
    }
    el.innerHTML = h;
  }
  el.addEventListener("click", async e => {
    const b = e.target.closest("[data-job]");
    if(b){ b.disabled = true; try{ await fetch("/api/job?name=" + b.dataset.job, {method:"POST"}); }catch(err){} setTimeout(load, 800); return; }
    if(e.target.closest("[data-toggle]")){ open = !open; render(); }
  });
  function mount(){ document.body.appendChild(el); document.body.style.paddingBottom = "72px"; load(); setInterval(load, 15000);
    window.addEventListener("resize", () => render()); }
  if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
})();
