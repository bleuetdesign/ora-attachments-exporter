// Save my Ora 1.3.0 — https://github.com/bleuetdesign/ora-attachments-exporter
//
// Saves all your Ora data in one ZIP file, "ora-backup-<date>.zip":
// - the official "Export as JSON" file of every project you administer (the same file Ora's button gives);
// - a spreadsheet (.csv) of every project, like Ora's "Export as CSV" with every option on, but with all tasks;
// - "ora-complement.json": what that export leaves out (order of lists and tasks, custom fields, list
//   workflows and automations, views, sprints, recurring tasks, templates, folders, favorites, Team Chat,
//   Git commits, account preferences), for every project, and for every task its history, comments,
//   timers and watchers.
//
// Saving every task takes long: about 3 requests per task, so 1 to 2 hours for 6,000 tasks. Keep the tab
// visible and the computer awake. The "Stop and save now" button saves what is done so far.
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
    tasks: true, /* each task's history, comments, timers and watchers (3 requests per task: the longest part) */
    pause: 300, /* milliseconds between two requests */
  };

  const READS = new Set([
    'socket_authentication', 'get_organizations', 'get_organization', 'organization_members',
    'get_organization_projects', 'folder_contents', 'folders_root_layer', 'get_edit_projects',
    'get_shared_projects', 'get_starred_projects', 'get_starred_tasks', 'get_favorites',
    'get_tasks_view_configurations', 'project_all', 'export_project_json', 'get_project_views', 'get_sprint',
    'get_project_task_comments', 'get_project_attachments',
    'project_get_more_activity_records', 'get_channels', 'get_shared_channels', 'get_threads',
    'get_channel_members', 'get_channel_messages', 'get_archived_channels',
    'get_tasks_view_preferences', 'get_user_emails', 'get_templates', 'get_organization_csv_settings',
    'get_time_labels', 'get_project_watchers', 'get_project_csv_settings', 'get_shared_custom_fields',
    'get_list_actions', 'project_get_commits', 'all_task_data', 'get_task_watchers',
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
  const buttonStyle = 'margin-top:10px;padding:5px 14px;border:0;border-radius:6px;background:#1b9af7;color:#fff;font:inherit;cursor:pointer';
  let stopRequested = false;
  const stop = document.createElement('button');
  stop.textContent = 'Stop and save now';
  stop.style.cssText = buttonStyle;
  stop.onclick = () => { stopRequested = true; stop.disabled = true; stop.textContent = 'Saving…'; };
  box.append(title, line, note, stop);
  document.body.append(box);
  const show = (t, l, n = '') => { title.textContent = t; line.textContent = l; note.textContent = n; };
  const log = (...a) => console.log('%c[Save my Ora]', 'color:#1b9af7;font-weight:bold', ...a);
  /* Time left: the average time of the last 200 requests, times the number of requests still to send. Each part
     of the backup sets "remaining" to its own estimate of that number. */
  const stamps = [];
  let remaining = () => null;
  const timeLeft = () => {
    const r = remaining();
    if (r == null || stamps.length < 20) return '';
    const ms = r * (stamps[stamps.length - 1] - stamps[0]) / (stamps.length - 1);
    const min = Math.ceil(ms / 60000);
    if (min <= 1) return 'less than a minute left';
    return min < 60 ? `about ${min} min left` : `about ${Math.floor(min / 60)} h ${String(min % 60).padStart(2, '0')} min left`;
  };
  const progress = (l) => {
    const t = timeLeft();
    show('Saving your Ora data…' + (t ? ` ${t}` : ''), l, 'Keep this tab visible (in its own window if you want to '
      + 'use other tabs) and your computer awake: a hidden tab runs much slower.');
    log(l, t);
  };
  progress('Starting…');

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  /* Ask the computer not to go to sleep while the tab is visible (not available in every browser). */
  let wakeLock = null;
  const keepAwake = async () => {
    try { if (document.visibilityState === 'visible') wakeLock = await navigator.wakeLock?.request('screen'); } catch { /* refused */ }
  };
  document.addEventListener('visibilitychange', keepAwake);
  keepAwake();

  /* Account fields that must never be saved: integration tokens, secrets, passwords, calendar feeds, API keys.
     Booleans such as "password_set" say nothing secret and stay. */
  const SECRET = /token|secret|passw|api_?key|private_?key|signature|cookie|session|ical|feed/i;
  const scrub = (x) => {
    if (Array.isArray(x)) return x.map(scrub);
    if (!x || typeof x !== 'object') return x;
    return Object.fromEntries(Object.entries(x).filter(([k, v]) => !(SECRET.test(k) && typeof v !== 'boolean'))
      .map(([k, v]) => [k, scrub(v)]));
  };

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
      if (m.request === 'socket_authentication' && onAuth) onAuth(m);
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
  /* Ora's answer to the login of this connection: the full account (preferences, etc.). Kept from the first one. */
  let me = null;
  const connect = async () => {
    await open();
    const authed = new Promise((ok) => { onAuth = ok; setTimeout(ok, 10000); });
    ws.send(JSON.stringify({ intention: 'socket_authentication', client_type: 'web', token,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }));
    const m = await authed;
    if (m && !me) me = m;
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

  /* A spreadsheet of the project: the columns of Ora's "Export as CSV" with every option on, but with all tasks
     (archived ones and those without a list included), dates as YYYY-MM-DD and an "Archived" column. */
  const toCsv = (pd, comments, files) => {
    const byId = (rows) => new Map((rows || []).map((r) => [r.id, r]));
    const group = (rows, key) => {
      const m = new Map();
      for (const r of rows || []) { if (!m.has(r[key])) m.set(r[key], []); m.get(r[key]).push(r); }
      return m;
    };
    const lists = byId(pd.lists);
    const milestones = byId(pd.milestones);
    const types = byId(pd.task_types);
    const labels = byId(pd.labels);
    const people = new Map((pd.developers || []).map((d) => [d.dev_id, d.full_name || d.username || '']));
    const devs = group((pd.tasks_developers || []).filter((d) => d.status !== false), 'task_id');
    const taskLabels = group(pd.tasks_labels, 'task_id');
    const values = group((pd.task_custom_fields || []).filter((v) => !v.deleted), 'task_id');
    const notes = group((Array.isArray(comments) ? comments : []).filter((c) => c.comment && !c.deleted), 'task_id');
    const attached = group((Array.isArray(files) ? files : []).filter((f) => !f.deleted), 'task_id');
    const fields = (pd.custom_fields || []).slice().sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
    const workflow = pd.features?.workflow;
    const STATUS = workflow ? { 0: 'Frozen', 1: 'Open', 2: 'Closed', 3: 'Review', 4: 'Frozen' }
      : { 0: 'Frozen', 1: 'Open', 2: 'Review', 3: 'Closed', 4: 'Frozen' };
    const day = (d) => (d ? String(d).slice(0, 10) : '');
    const hours = (t) => {
      if (!t) return '';
      const [h, m] = String(t).split(':').map(Number);
      return Math.round(100 * (h + (m || 0) / 60)) / 100 || '';
    };
    const cell = (v) => {
      const x = v == null ? '' : String(v);
      return /[",;\r\n]|^\s|\s$/.test(x) ? `"${x.replace(/"/g, '""')}"` : x;
    };
    const custom = (t, f) => {
      const v = (values.get(t.id) || []).find((x) => x.custom_field_id === f.id);
      if (!v) return '';
      switch (f.custom_field_type?.type) {
        case 1: return (f.custom_field_type.options || []).find((o) => o.uuid === (v.selected_dropdowns || [])[0])?.text ?? '';
        case 2: return v.numeric_value ?? '';
        case 3: return v.text ?? '';
        case 4: return (v.members || []).map((m) => (typeof m === 'object' ? m.full_name || m.fullName : people.get(m)) || '').join(',');
        case 5: return day(v.date);
        case 6: return v.is_checked ? 'checked' : '';
        default: return '';
      }
    };
    const rows = [['Global ID', 'List', 'ID', 'Title', 'Assignee', 'Milestone', 'Estimate', 'Tracked Time', 'Status',
      'Task Type', 'Due Date', 'Updated', 'Description', 'Labels', 'Attachments', 'Story points', 'Completed story points',
      'Business value', ...fields.map((f) => f.name), 'Archived']];
    for (const t of pd.tasks || []) {
      if (t.deleted) continue;
      const list = lists.get(t.list_id);
      let description = t.description || '';
      const said = (notes.get(t.id) || []).sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
      if (said.length) {
        description += '\n\nCOMMENTS:' + said.map((c) => `\nAT: ${c.created_at}\nFROM: ${people.get(c.user_id) || ''}\nCOMMENT: ${c.comment}`).join('\n');
      }
      rows.push([t.id, list?.title ?? '', t.second_id, t.title,
        (devs.get(t.id) || []).map((d) => people.get(d.dev_id) || '').join(','),
        milestones.get(t.milestone_id)?.title ?? '', hours(t.estimated), t.tracked_time || '',
        (workflow ? STATUS[list?.list_type] : STATUS[t.state]) ?? '', types.get(t.task_type)?.name ?? '',
        day(t.due_date), day(t.updated_at), description,
        (taskLabels.get(t.id) || []).map((l) => labels.get(l.label_id)?.title ?? '').join(','),
        (attached.get(t.id) || []).map((f) => f.external_url || `https://ora-attachments.s3.amazonaws.com/${f.data_path}`).join(','),
        t.points || '', t.points_done || '', t.value || '', ...fields.map((f) => custom(t, f)), t.archived ? 'yes' : '']);
    }
    /* The byte order mark lets Excel read accents correctly. */
    return '\ufeff' + rows.map((r) => r.map(cell).join(',')).join('\r\n') + '\r\n';
  };

  /* Minimal ZIP writer. Files are compressed when the browser can (JSON shrinks about tenfold: a big account
     can hold several hundred MB of JSON), and stored as they are otherwise. */
  const deflate = async (bytes) => {
    try {
      const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream('deflate-raw'));
      return new Uint8Array(await new Response(stream).arrayBuffer());
    } catch { return null; }
  };
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
  const zip = async (files) => {
    const enc = new TextEncoder();
    const now = new Date();
    const time = (now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1);
    const date = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
    const parts = [];
    const central = [];
    let offset = 0;
    for (const f of files) {
      const name = enc.encode(f.name);
      const raw = enc.encode(f.text);
      const crc = crc32(raw);
      const packed = await deflate(raw);
      const method = packed && packed.length < raw.length ? 8 : 0;
      const data = method ? packed : raw;
      const head = (centralEntry) => {
        const h = new DataView(new ArrayBuffer((centralEntry ? 46 : 30) + name.length));
        let i = 0;
        const u32 = (v) => { h.setUint32(i, v, true); i += 4; };
        const u16 = (v) => { h.setUint16(i, v, true); i += 2; };
        u32(centralEntry ? 0x02014b50 : 0x04034b50);
        if (centralEntry) u16(20);
        u16(20); u16(0x0800); u16(method); u16(time); u16(date); u32(crc); u32(data.length); u32(raw.length);
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
    version: 2,
    exportedAt: new Date().toISOString(),
    account: { id: user.id, username: user.username },
    options: OPTIONS,
    projects: {}, /* id -> title, for a quick look */
    exported: {}, /* id -> name of the official JSON export in the ZIP (the .csv has the same name) */
    responses: {}, /* intention -> key -> { request, status, data }: Ora's answers, unchanged */
    errors: [],
  };
  const exports = [];
  let count = 0;
  /* On a lost connection, keep reconnecting for up to 30 minutes before giving up on a request: a network
     outage or a short sleep of the computer must not lose everything. A request that times out is tried 4 times. */
  async function get(msg, key = 'all') {
    if (!READS.has(msg.intention)) throw new Error('Refused (not a read): ' + msg.intention);
    let r;
    for (let attempt = 0, since = Date.now(), timeouts = 0; ; attempt++) {
      if (stopRequested) throw new Error('stopped by you');
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        if (attempt) {
          show('Connection lost, reconnecting…', 'Your data is safe: the backup continues where it stopped.',
            'Check your internet connection.');
          await sleep(Math.min(5000 * attempt, 60000));
        }
        try { await connect(); } catch (e) { r = { closed: true }; if (Date.now() - since > 1800000) break; continue; }
      }
      await sleep(OPTIONS.pause);
      r = await send(msg);
      if (!r.timeout && !r.closed) break;
      if (r.timeout && ++timeouts >= 4) break;
      if (Date.now() - since > 1800000) break;
    }
    count++;
    stamps.push(Date.now());
    if (stamps.length > 200) stamps.shift();
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
    if (me) {
      out.responses.socket_authentication = { me: { request: { intention: 'socket_authentication', client_type: 'web' },
        status: me.socket_authentication, data: scrub(await inflate(me.data)) } };
    }
    for (const intention of ['get_edit_projects', 'get_shared_projects', 'get_starred_projects', 'get_starred_tasks',
      'get_favorites', 'get_tasks_view_configurations', 'get_tasks_view_preferences', 'get_user_emails',
      'get_templates']) await get({ intention });
    await get({ intention: 'get_templates', mine: true }, 'mine');
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
      await get({ intention: 'get_organization_csv_settings', organization_id: org }, org);
      await get({ intention: 'get_templates', organization_id: org, mine: true, system: true }, `org${org}_mine`);
      await get({ intention: 'get_templates', organization_id: org, mine: false }, `org${org}_others`);
    }

    /* 2. Projects: the official export, and project_all (positions, custom fields, workflows, views, sprints…). */
    const names = new Set();
    const tasks = []; /* [project id, task id], read after Team Chat */
    const perTask = OPTIONS.tasks ? 3 : 0;
    let n = 0;
    /* Until all projects are read, the number of tasks is extrapolated from the projects read so far. */
    const projectsStart = count;
    remaining = () => (n < 2 ? null : (projectIds.size - n + 1) * ((count - projectsStart) / (n - 1))
      + (tasks.length / (n - 1)) * projectIds.size * perTask);
    for (const P of [...projectIds].sort((a, b) => a - b)) {
      n++;
      const pa = await get({ intention: 'project_all', project_id: P, version: 4 }, P);
      const pd = pa?.data || pa;
      if (!pd?.tasks) continue;
      const projectTitle = pd.project?.title ?? '';
      out.projects[P] = projectTitle;
      progress(`Project ${n} of ${projectIds.size}: ${projectTitle}`);
      const clean = (projectTitle.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').trim() || `project-${P}`).slice(0, 120);
      let base = clean;
      for (let k = 2; names.has(base.toLowerCase()); k++) base = `${clean}-${k}`;
      names.add(base.toLowerCase());
      const official = await get({ intention: 'export_project_json', project_id: P }, P);
      if (official?.project) {
        out.exported[P] = `${base}.json`;
        exports.push({ name: `${base}.json`, text: JSON.stringify(camel(official), null, 2) });
      }
      const comments = await get({ intention: 'get_project_task_comments', project_id: P }, P);
      const files = await get({ intention: 'get_project_attachments', project_id: P }, P);
      exports.push({ name: `${base}.csv`, text: toCsv(pd, comments, files) });
      if (pd.features?.views && pd.views?.length) await get({ intention: 'get_project_views', project_id: P }, P);
      for (const s of pd.sprints || []) await get({ intention: 'get_sprint', project_id: P, sprint_id: s.id }, `${P}_${s.id}`);
      await get({ intention: 'get_time_labels', project_id: P }, P);
      await get({ intention: 'get_project_watchers', project_id: P }, P);
      await get({ intention: 'get_project_csv_settings', project_id: P }, P);
      const O = pd.project?.organization_id;
      if (O) await get({ intention: 'get_shared_custom_fields', project_id: P, organization_id: O }, `${P}_${O}`);
      for (const l of pd.lists || []) await get({ intention: 'get_list_actions', project_id: P, list_id: l.id }, `${P}_${l.id}`);
      /* project_all only gives the last 20 commits. The first page (from 0) usually holds them all. */
      if (pd.commits?.length) {
        for (let k = 0; k < 1000000;) {
          const c = await get({ intention: 'project_get_commits', project_id: P, current_records: k }, `${P}_${k}`);
          if (!Array.isArray(c) || c.length === 0) break;
          k += c.length;
        }
      }
      for (const t of pd.tasks) tasks.push([P, t.id]);
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
        addChannels(await get({ intention: 'get_archived_channels', organization_id: org }, org));
        await get({ intention: 'get_threads', organization_id: org }, org);
      }
      let c = 0;
      const chatStart = count;
      remaining = () => (channels.size - c + 1) * (c > 1 ? (count - chatStart) / (c - 1) : 3) + tasks.length * perTask;
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

    /* 4. Each task: its card (timers, files, links…), its watchers, and its thread, which holds the task history
       ("moved this card"…) and the comments with their attachments. With after=1970, Ora sends the whole thread. */
    if (OPTIONS.tasks) {
      const tasksStart = count;
      let i = 0;
      remaining = () => (tasks.length - i) * (i ? (count - tasksStart) / i : 3);
      for (; i < tasks.length; i++) {
        const [P, T] = tasks[i];
        progress(`Task ${i + 1} of ${tasks.length}`);
        const d = await get({ intention: 'all_task_data', project_id: P, task_id: T }, `${P}_${T}`);
        await get({ intention: 'get_task_watchers', project_id: P, task_id: T }, `${P}_${T}`);
        const th = d?.discussionThread?.id;
        if (th) {
          await get({ intention: 'get_channel_messages', after: '1970-01-01T00:00:00Z', discussion_thread: th,
            project_id: P, version: 2 }, `thread_${th}`);
        }
      }
    }
  } catch (e) {
    out.errors.push({ fatal: String((e && e.message) || e) });
    log('Stopped early:', e);
  }

  try { ws.close(); } catch { /* already closed */ }
  stop.remove();
  show('Writing the ZIP file…', 'This can take a minute for a big account.');
  window.oraComplement = out;
  const file = await zip([...exports, { name: 'ora-complement.json', text: JSON.stringify(out) }]);
  document.removeEventListener('visibilitychange', keepAwake);
  try { await wakeLock?.release(); } catch { /* already released */ }
  const a = document.createElement('a');
  a.href = URL.createObjectURL(file);
  a.download = `ora-backup-${out.exportedAt.slice(0, 10)}.zip`;
  document.body.append(a);
  a.click();
  a.remove();
  window.__saveMyOraRunning = false;

  const total = Object.keys(out.projects).length;
  const official = Object.keys(out.exported).length;
  const others = total - official;
  const fatal = out.errors.find((e) => e.fatal);
  const byYou = fatal?.fatal === 'stopped by you';
  show(fatal ? 'Saved, but stopped early' : 'Done! 🎉',
    `${a.download} is in your Downloads folder.`,
    (byYou ? 'It holds what was saved before you stopped. ' : '')
    + (fatal && !byYou ? `Problem: ${fatal.fatal}. Click "Save my Ora" again to retry. ` : '')
    + `${total} project${total === 1 ? '' : 's'} saved, each with a spreadsheet (.csv).`
    + (others > 0 ? ` Ora only gives its official export for projects you administer: ${others} other`
      + `${others === 1 ? ' is' : 's are'} in the spreadsheets and the complement only.` : '')
    + ' Next step: the attachments, with ora_attachments.py.');
  const close = document.createElement('button');
  close.textContent = 'Close';
  close.style.cssText = buttonStyle;
  close.onclick = () => box.remove();
  box.append(close);
  log(`Done: ${official} official exports, ${total} projects, ${count} requests, ${(file.size / 1e6).toFixed(1)} MB.`, out.errors);
})();
