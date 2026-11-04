/* Local stand-in for the claude.ai artifact runtime (window.claude.use).
   Loaded only by the local server, before the desk's own script. It provides:
   - "db":  the artifact database API (doc/collection get, set, update, delete, onSnapshot), backed by SQLite on the server
   - "mcp": callTool() for the two connectors the page uses (Google Drive, Claude Code Remote), answered by the server
   - "user": a single local owner
   Changes are noticed by polling /api/seq every 2 seconds. */
(function(){
  "use strict";
  const enc = encodeURIComponent;
  async function api(url, opts){
    const r = await fetch(url, opts);
    let j = {}; try { j = await r.json(); } catch(e) {}
    if(!r.ok){ const err = new Error(j.message || ("HTTP " + r.status)); err.code = j.code || "upstream_error"; throw err; }
    return j;
  }
  const post = (url, body) => api(url, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  const subs = new Set(); let seq = -1, timer = null;
  async function poll(){
    clearTimeout(timer);
    try{ const s = await api("/api/seq"); if(s.seq !== seq){ seq = s.seq; for(const f of [...subs]) f(); } }catch(e){}
    timer = setTimeout(poll, 2000);
  }
  const clone = v => v == null ? v : JSON.parse(JSON.stringify(v));
  function docSnap(path, r){ return { id: path.split("/").pop(), exists: !!r.exists, data: () => clone(r.data), metadata: { fromCache: false } }; }
  function docRef(path){
    const write = body => post("/api/doc?path=" + enc(path), body).then(r => { poll(); return r; });
    return {
      id: path.split("/").pop(), path,
      get: async () => docSnap(path, await api("/api/doc?path=" + enc(path))),
      set: data => write({op:"set", data}),
      update: data => write({op:"update", data}),
      delete: () => write({op:"delete"}),
      onSnapshot(cb, err){
        const run = async () => { try{ cb(docSnap(path, await api("/api/doc?path=" + enc(path)))); }catch(e){ if(err) err(e); } };
        subs.add(run); run(); return () => subs.delete(run);
      }
    };
  }
  function colRef(path){
    const snap = r => ({ docs: r.docs.map(d => ({ id: d.id, exists: true, data: () => clone(d.data) })), size: r.docs.length, empty: !r.docs.length, metadata: { fromCache: false } });
    return {
      path, doc: id => docRef(path + "/" + id),
      get: async () => snap(await api("/api/col?path=" + enc(path))),
      onSnapshot(cb, err){
        const run = async () => { try{ cb(snap(await api("/api/col?path=" + enc(path)))); }catch(e){ if(err) err(e); } };
        subs.add(run); run(); return () => subs.delete(run);
      }
    };
  }
  const db = Object.freeze({ doc: docRef, collection: colRef });
  const mcp = Object.freeze({
    async callTool(server, tool, input){
      const r = await post("/api/mcp", {server, tool, input: input || {}});
      return { payload: r.payload, content: [{ type: "text", text: JSON.stringify(r.payload) }] };
    },
    async listTools(){ return { servers: [
      { server: "Google Drive", authStatus: "connected", tools: ["create_file","read_file_content","download_file_content","trash_file"].map(name => ({name, description:""})) },
      { server: "Claude Code Remote", authStatus: "connected", tools: [{ name: "fire_trigger", description: "" }] } ] }; },
    invalidate: async () => {},
    watchTool(){ return () => {}; }
  });
  const user = Object.freeze({
    isOwner: () => true, canEdit: () => true, can: () => true,
    id: async () => "local", me: async () => ({ id: "local", name: "You" }),
    profiles: async ids => Object.fromEntries((ids || []).map(i => [i, { id: i, name: "You" }])), search: async () => []
  });
  window.claude = Object.freeze({ local: true, use: async name => name === "db" ? db : name === "mcp" ? mcp : name === "user" ? user : null });
  poll();
})();
