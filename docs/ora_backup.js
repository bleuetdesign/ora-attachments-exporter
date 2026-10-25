// Save my Ora 2.0.0 — https://github.com/bleuetdesign/ora-attachments-exporter
//
// Saves all your Ora data in one ZIP file, "ora-backup-<date>.zip":
// - the official "Export as JSON" file of every project you administer (the same file Ora's button gives);
// - "ora-complement.json": what that export leaves out (order of lists and tasks, custom fields, list
//   workflows, views, sprints, recurring tasks, templates, folders, favorites, Team Chat), for every project.
// Attachments are not in the ZIP: ora_attachments.py downloads them from it afterwards.
//
// How to use: the simplest is the "Save my Ora" bookmark, from
// https://bleuetdesign.github.io/ora-attachments-exporter/. You can also paste this file into the browser
// console on https://app.ora.pm.
//
// What it does:
// - It only READS. Every request is checked against the READS list below; anything else is refused.
// - It talks only to Ora (wss://ws.ora.pm), with the session you are already logged in with.
//   Nothing is sent anywhere else, and your session token is not written into the file.
// - It sends one request at a time, with a pause between requests, to go easy on Ora's servers.
//
// Note for editors: this file also runs as a bookmark, where line breaks may be lost. Inside the code
// below, use only /* block comments */.
(async () => {
  const OPTIONS = {
    chat: true, /* Team Chat channels, members and messages */
    history: false, /* full activity history of each project (many requests: can take a long time) */
    pause: 300, /* milliseconds between two requests */
  };

  const READS = new Set([
    'socket_authentication', 'get_organizations', 'get_organization', 'organization_members',
    'get_organization_projects', 'folder_contents', 'folders_root_layer', 'get_edit_projects',
    'get_shared_projects', 'get_starred_projects', 'get_starred_tasks', 'get_favorites',
    'get_tasks_view_configurations', 'project_all', 'export_project_json', 'get_project_views', 'get_sprint',
    'project_get_more_activity_records', 'get_channels', 'get_shared_channels', 'get_threads',
    'get_channel_members', 'get_channel_messages',
  ]);

  if (location.host !== 'app.ora.pm') {
    alert('Open https://app.ora.pm and log in, then click "Save my Ora" again on that page.');
    return;
  }
  if (window.__saveMyOraRunning) {
    alert('Save my Ora is already running in this tab.');
    return;
  }
  const account = JSON.parse(localStorage.getItem('authenticatedAccount') || '{}');
  const user = account.user || account;
  const token = account.token || user.token;
  if (!token) {
    alert('You do not seem to be logged in to Ora. Log in, then click "Save my Ora" again.');
    return;
  }
  window.__saveMyOraRunning = true;

  /* Progress box, top right of the page. */
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;top:16px;right:16px;z-index:2147483647;width:340px;padding:16px 18px;'
    + 'background:#fff;color:#222;border-radius:10px;box-shadow:0 6px 30px rgba(0,0,0,.25);'
    + 'font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
  const title = document.createElement('div');
  title.style.cssText = 'font-weight:600;font-size:15px;margin-bottom:6px';
  const line = document.createElement('div');
  const note = document.createElement('div');
  note.style.cssText = 'margin-top:8px;color:#666;font-size:13px';
  box.append(title, line, note);
  document.body.append(box);
  const show = (t, l, n = '') => { title.textContent = t; line.textContent = l; note.textContent = n; };
  const log = (...a) => console.log('%c[Save my Ora]', 'color:#1b9af7;font-weight:bold', ...a);
  const progress = (l) => { show('Saving your Ora data…', l, 'Keep this tab open. You can use other tabs meanwhile.'); log(l); };
  progress('Starting…');

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  /* A separate connection to Ora, so the app itself is not disturbed. */
  let ws;
  let nextId = 1;
  const waiting = new Map();
  let onAuth = null;
  const open = () => new Promise((ok, ko) => {
    ws = new WebSocket('wss://ws.ora.pm/ws');
    ws.onopen = () => ok();
    ws.onerror = () => ko(new Error('Could not connect to Ora.'));
    ws.onclose = () => {
      for (const w of waiting.values()) w({ closed: true });
      waiting.clear();
    };
    ws.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      if (!m || typeof m !== 'object') return;
      if (m.request === 'socket_authentication' && onAuth) onAuth();
      const w = waiting.get(Number(m.action_id));
      if (w) { waiting.delete(Number(m.action_id)); w(m); }
    };
  });
  const send = (msg, timeout = 60000) => new Promise((ok) => {
    const id = nextId++;
    waiting.set(id, ok);
    ws.send(JSON.stringify({ ...msg, action_id: id, token }));
    setTimeout(() => { if (waiting.delete(id)) ok({ timeout: true }); }, timeout);
  });
  const connect = async () => {
    await open();
    const authed = new Promise((ok) => { onAuth = ok; setTimeout(ok, 10000); });
    ws.send(JSON.stringify({ intention: 'socket_authentication', client_type: 'web', token,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }));
    await authed;
  };

  /* Large answers come compressed (zlib), as raw bytes or as base64. */
  const inflate = async (d) => {
    if (typeof d !== 'string' || d.length < 8) return d;
    const sources = [
      () => Uint8Array.from(d, (c) => c.charCodeAt(0) & 255),
      () => Uint8Array.from(atob(d), (c) => c.charCodeAt(0)),
    ];
    for (const bytes of sources) {
      try {
        const stream = new Blob([bytes()]).stream().pipeThrough(new DecompressionStream('deflate'));
        return JSON.parse(await new Response(stream).text());
      } catch { /* not this encoding */ }
    }
    return d;
  };

  /* Ora's app turns snake_case keys into camelCase before saving its JSON export: same here. Other keys,
     such as emoji names in reactions (":ok_hand:"), stay as they are. */
  const camel = (x) => {
    if (Array.isArray(x)) return x.map(camel);
    if (!x || typeof x !== 'object') return x;
    const o = {};
    for (const [k, v] of Object.entries(x)) {
      o[/^[a-z][a-z0-9_]*$/.test(k) ? k.replace(/_([a-z0-9])/g, (_, c) => c.toUpperCase()) : k] = camel(v);
    }
    return o;
  };

  /* Minimal ZIP writer (files stored without compression). */
  const crcTable = Array.from({ length: 256 }, (_, n) => {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
    return c >>> 0;
  });
  const crc32 = (bytes) => {
    let c = 0xFFFFFFFF;
    for (const b of bytes) c = crcTable[(c ^ b) & 255] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  };
  const zip = (files) => {
    const enc = new TextEncoder();
    const now = new Date();
    const time = (now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1);
    const date = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
    const parts = [];
    const central = [];
    let offset = 0;
    for (const f of files) {
      const name = enc.encode(f.name);
      const data = enc.encode(f.text);
      const crc = crc32(data);
      const head = (centralEntry) => {
        const h = new DataView(new ArrayBuffer((centralEntry ? 46 : 30) + name.length));
        let i = 0;
        const u32 = (v) => { h.setUint32(i, v, true); i += 4; };
        const u16 = (v) => { h.setUint16(i, v, true); i += 2; };
        u32(centralEntry ? 0x02014b50 : 0x04034b50);
        if (centralEntry) u16(20);
        u16(20); u16(0x0800); u16(0); u16(time); u16(date); u32(crc); u32(data.length); u32(data.length);
        u16(name.length); u16(0);
        if (centralEntry) { u16(0); u16(0); u16(0); u32(0); u32(offset); }
        new Uint8Array(h.buffer).set(name, i);
        return new Uint8Array(h.buffer);
      };
      const local = head(false);
      central.push(head(true));
      parts.push(local, data);
      offset += local.length + data.length;
    }
    const size = central.reduce((s, c) => s + c.length, 0);
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true);
    end.setUint16(8, files.length, true);
    end.setUint16(10, files.length, true);
    end.setUint32(12, size, true);
    end.setUint32(16, offset, true);
    return new Blob([...parts, ...central, new Uint8Array(end.buffer)], { type: 'application/zip' });
  };

  const out = {
    format: 'ora-complement',
    version: 1,
    exportedAt: new Date().toISOString(),
    account: { id: user.id, username: user.username },
    options: OPTIONS,
    projects: {}, /* id -> title, for a quick look */
    exported: {}, /* id -> name of the official JSON export in the ZIP */
    responses: {}, /* intention -> key -> { request, status, data }: Ora's answers, unchanged */
    errors: [],
  };
  const exports = [];
  let count = 0;
  async function get(msg, key = 'all') {
    if (!READS.has(msg.intention)) throw new Error('Refused (not a read): ' + msg.intention);
    let r;
    for (let attempt = 0; attempt < 4; attempt++) {
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        if (attempt) { progress('Connection lost, reconnecting…'); await sleep(5000 * attempt); }
        try { await connect(); } catch (e) { r = { closed: true }; continue; }
      }
      await sleep(OPTIONS.pause);
      r = await send(msg);
      if (!r.timeout && !r.closed) break;
    }
    count++;
    const status = r[msg.intention];
    const data = await inflate(r.data);
    if (msg.intention === 'export_project_json') return status === 200 ? data : undefined;
    (out.responses[msg.intention] ??= {})[key] = { request: msg, status, data };
    if (status !== 200) out.errors.push({ intention: msg.intention, key, status: status ?? (r.timeout ? 'timeout' : 'closed') });
    return status === 200 ? data : undefined;
  }

  try {
    /* 1. Organizations, folders, favorites: also gives the list of all projects, archived ones included. */
    progress('Looking for your projects…');
    const orgsData = await get({ intention: 'get_organizations' });
    const orgs = (Array.isArray(orgsData) ? orgsData : orgsData?.organizations || []).map((o) => String(o.id));
    for (const intention of ['get_edit_projects', 'get_shared_projects', 'get_starred_projects', 'get_starred_tasks',
      'get_favorites', 'get_tasks_view_configurations']) await get({ intention });
    const projectIds = new Set();
    const addProjects = (d) => { for (const p of (Array.isArray(d) ? d : d?.projects) || []) if (p?.id) projectIds.add(p.id); };
    const folders = [];
    const contents = async (msg, key) => {
      const d = await get({ intention: 'folder_contents', mine: true, ...msg }, key);
      addProjects(d);
      for (const f of d?.folders || []) folders.push([f.id, msg.organization_id]);
    };
    for (const archived of [false, true]) {
      await contents({ all_folders: true, archived }, `personal_archived${archived}`);
      await get({ intention: 'folders_root_layer', archived, mine: true }, `archived${archived}`);
      for (const org of orgs) await contents({ all_folders: true, archived, organization_id: org }, `org${org}_archived${archived}`);
    }
    for (const seen = new Set(); folders.length;) {
      const [id, org] = folders.shift();
      if (seen.has(id)) continue;
      seen.add(id);
      for (const archived of [false, true]) await contents({ folder_id: id, archived, organization_id: org }, `folder${id}_archived${archived}`);
    }
    addProjects(out.responses.get_edit_projects?.all?.data);
    addProjects(out.responses.get_shared_projects?.all?.data);
    addProjects(out.responses.get_starred_projects?.all?.data);
    for (const org of orgs) {
      await get({ intention: 'get_organization', organization_id: org }, org);
      await get({ intention: 'organization_members', organization_id: org }, org);
      addProjects(await get({ intention: 'get_organization_projects', organization_id: org }, org));
    }

    /* 2. Projects: the official export, and project_all (positions, custom fields, workflows, views, sprints…). */
    const names = new Set();
    let n = 0;
    for (const P of [...projectIds].sort((a, b) => a - b)) {
      n++;
      const pa = await get({ intention: 'project_all', project_id: P, version: 4 }, P);
      const pd = pa?.data || pa;
      if (!pd?.tasks) continue;
      const projectTitle = pd.project?.title ?? '';
      out.projects[P] = projectTitle;
      progress(`Project ${n} of ${projectIds.size}: ${projectTitle}`);
      const official = await get({ intention: 'export_project_json', project_id: P }, P);
      if (official?.project) {
        const base = (projectTitle.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').trim() || `project-${P}`).slice(0, 120);
        let name = `${base}.json`;
        for (let k = 2; names.has(name.toLowerCase()); k++) name = `${base}-${k}.json`;
        names.add(name.toLowerCase());
        out.exported[P] = name;
        exports.push({ name, text: JSON.stringify(camel(official), null, 2) });
      }
      if (pd.features?.views && pd.views?.length) await get({ intention: 'get_project_views', project_id: P }, P);
      for (const s of pd.sprints || []) await get({ intention: 'get_sprint', project_id: P, sprint_id: s.id }, `${P}_${s.id}`);
      if (OPTIONS.history) {
        for (let k = (pd.activity || []).length; k < 100000;) {
          const a = await get({ intention: 'project_get_more_activity_records', project_id: P, current_records: k }, `${P}_${k}`);
          if (!Array.isArray(a) || a.length === 0) break;
          k += a.length;
        }
      }
    }

    /* 3. Team Chat: messages come 40 at a time, newest first. */
    if (OPTIONS.chat) {
      const channels = new Map();
      const addChannels = (d) => { for (const c of (Array.isArray(d) ? d : d?.channels) || []) if (c?.id) channels.set(c.id, c); };
      await get({ intention: 'get_shared_channels' });
      for (const org of orgs) {
        addChannels(await get({ intention: 'get_channels', organization_id: org }, org));
        await get({ intention: 'get_threads', organization_id: org }, org);
      }
      let c = 0;
      for (const ch of channels.values()) {
        progress(`Team Chat ${++c} of ${channels.size}: ${ch.name || 'direct messages'}`);
        await get({ intention: 'get_channel_members', channel_id: ch.id }, ch.id);
        const base = { intention: 'get_channel_messages', channel_id: ch.id, version: 2 };
        let page = await get(base, `channel${ch.id}`);
        for (let i = 1; Array.isArray(page) && page.length >= 40 && i < 1000; i++) {
          const oldest = page.map((m) => m.created_at).sort()[0];
          page = await get({ ...base, before: oldest }, `channel${ch.id}_page${i}`);
        }
      }
    }
  } catch (e) {
    out.errors.push({ fatal: String((e && e.message) || e) });
    log('Stopped early:', e);
  }

  try { ws.close(); } catch { /* already closed */ }
  window.oraComplement = out;
  const file = zip([...exports, { name: 'ora-complement.json', text: JSON.stringify(out) }]);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(file);
  a.download = `ora-backup-${out.exportedAt.slice(0, 10)}.zip`;
  document.body.append(a);
  a.click();
  a.remove();
  window.__saveMyOraRunning = false;

  const total = Object.keys(out.projects).length;
  const others = total - exports.length;
  const fatal = out.errors.find((e) => e.fatal);
  show(fatal ? 'Saved, but stopped early' : 'Done! 🎉',
    `${a.download} is in your Downloads folder.`,
    (fatal ? `Problem: ${fatal.fatal}. Click "Save my Ora" again to retry. ` : '')
    + `${total} project${total === 1 ? '' : 's'} saved.`
    + (others > 0 ? ` Ora only gives its official export for projects you administer: ${others} other`
      + `${others === 1 ? ' is' : 's are'} in the complement only.` : '')
    + ' Next step: the attachments, with ora_attachments.py.');
  const close = document.createElement('button');
  close.textContent = 'Close';
  close.style.cssText = 'margin-top:10px;padding:5px 14px;border:0;border-radius:6px;background:#1b9af7;color:#fff;font:inherit;cursor:pointer';
  close.onclick = () => box.remove();
  box.append(close);
  log(`Done: ${exports.length} exports, ${total} projects, ${count} requests, ${(file.size / 1e6).toFixed(1)} MB.`, out.errors);
})();
