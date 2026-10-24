// Ora complement export 1.0.0 — https://github.com/bleuetdesign/ora-attachments-exporter
//
// Saves what Ora's "Export as JSON" leaves out: the order of lists and tasks, custom fields, list
// workflows, views, sprints, recurring tasks, templates, folders, favorites and, optionally, Team Chat.
//
// How to use: open https://app.ora.pm, log in, open the browser console, paste this whole file and
// press Return. Leave the tab open until the file "ora-complement-<date>.json" is downloaded.
//
// What it does:
// - It only READS. Every request is checked against the READS list below; anything else is refused.
// - It talks only to Ora (wss://ws.ora.pm), with the session you are already logged in with.
//   Nothing is sent anywhere else, and your session token is not written into the file.
// - It sends one request at a time, with a pause between requests, to go easy on Ora's servers.
(async () => {
  const OPTIONS = {
    chat: true,      // Team Chat channels, members and messages
    history: false,  // full activity history of each project (many requests: can take a long time)
    pause: 300,      // milliseconds between two requests
  };

  const READS = new Set([
    'socket_authentication', 'get_organizations', 'get_organization', 'organization_members',
    'get_organization_projects', 'folder_contents', 'folders_root_layer', 'get_edit_projects',
    'get_shared_projects', 'get_starred_projects', 'get_starred_tasks', 'get_favorites', 'get_tasks_view_configurations',
    'project_all', 'get_project_views', 'get_sprint',
    'project_get_more_activity_records', 'get_channels', 'get_shared_channels', 'get_threads',
    'get_channel_members', 'get_channel_messages',
  ]);

  const log = (...a) => console.log('%c[Ora complement]', 'color:#1b9af7;font-weight:bold', ...a);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  if (location.host !== 'app.ora.pm') {
    alert('Open https://app.ora.pm, log in, then paste this script again in the console of that tab.');
    return;
  }
  const account = JSON.parse(localStorage.getItem('authenticatedAccount') || '{}');
  const user = account.user || account;
  const token = account.token || user.token;
  if (!token) {
    alert('You do not seem to be logged in to Ora. Log in, then paste this script again.');
    return;
  }

  // A separate connection to Ora, so the app itself is not disturbed.
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

  // Large answers come compressed (zlib), as raw bytes or as base64.
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

  const out = {
    format: 'ora-complement',
    version: 1,
    exportedAt: new Date().toISOString(),
    account: { id: user.id, username: user.username },
    options: OPTIONS,
    projects: {},  // id -> title, for a quick look
    responses: {}, // intention -> key -> { request, status, data }: Ora's answers, unchanged
    errors: [],
  };
  let count = 0;
  async function get(msg, key = 'all') {
    if (!READS.has(msg.intention)) throw new Error('Refused (not a read): ' + msg.intention);
    let r;
    for (let attempt = 0; attempt < 4; attempt++) {
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        if (attempt) { log('Connection lost, reconnecting…'); await sleep(5000 * attempt); }
        try { await connect(); } catch (e) { r = { closed: true }; continue; }
      }
      await sleep(OPTIONS.pause);
      r = await send(msg);
      if (!r.timeout && !r.closed) break;
    }
    const status = r[msg.intention];
    const data = await inflate(r.data);
    (out.responses[msg.intention] ??= {})[key] = { request: msg, status, data };
    if (status !== 200) out.errors.push({ intention: msg.intention, key, status: status ?? (r.timeout ? 'timeout' : 'closed') });
    if (++count % 25 === 0) log(count, 'requests…');
    return status === 200 ? data : undefined;
  }

  log('Started. Leave this tab open until the file is downloaded.');

  // 1. Organizations, folders, favorites: also gives the list of all projects, archived ones included.
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
  log(projectIds.size, 'projects found.');

  // 2. Projects: project_all holds positions, custom fields, workflows, views, sprints, recurring tasks…
  let n = 0;
  for (const P of [...projectIds].sort((a, b) => a - b)) {
    const pa = await get({ intention: 'project_all', project_id: P, version: 4 }, P);
    const pd = pa?.data || pa;
    n++;
    if (!pd?.tasks) continue;
    out.projects[P] = pd.project?.title ?? '';
    log(`Project ${n}/${projectIds.size}:`, pd.project?.title, `(${pd.tasks.length} tasks)`);
    if (pd.views?.length) await get({ intention: 'get_project_views', project_id: P }, P);
    for (const s of pd.sprints || []) await get({ intention: 'get_sprint', project_id: P, sprint_id: s.id }, `${P}_${s.id}`);
    if (OPTIONS.history) {
      for (let k = (pd.activity || []).length; k < 100000;) {
        const a = await get({ intention: 'project_get_more_activity_records', project_id: P, current_records: k }, `${P}_${k}`);
        if (!Array.isArray(a) || a.length === 0) break;
        k += a.length;
      }
    }
  }

  // 3. Team Chat: messages come 40 at a time, newest first.
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
      log(`Channel ${++c}/${channels.size}:`, ch.name || '(direct messages)');
      await get({ intention: 'get_channel_members', channel_id: ch.id }, ch.id);
      const base = { intention: 'get_channel_messages', channel_id: ch.id, version: 2 };
      let page = await get(base, `channel${ch.id}`);
      for (let i = 1; Array.isArray(page) && page.length >= 40 && i < 1000; i++) {
        const oldest = page.map((m) => m.created_at).sort()[0];
        page = await get({ ...base, before: oldest }, `channel${ch.id}_page${i}`);
      }
    }
  }

  try { ws.close(); } catch { /* already closed */ }
  window.oraComplement = out;
  const blob = new Blob([JSON.stringify(out)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `ora-complement-${out.exportedAt.slice(0, 10)}.json`;
  document.body.append(a);
  a.click();
  a.remove();
  log(`Done: ${Object.keys(out.projects).length} projects, ${count} requests, ${(blob.size / 1e6).toFixed(1)} MB.`,
    out.errors.length ? `${out.errors.length} requests were refused by Ora (listed in "errors" in the file).` : 'No errors.');
  log(`Saved as ${a.download} in your Downloads folder.`);
})();
