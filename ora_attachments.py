#!/usr/bin/env python3
"""Standalone exporter for Ora (ora.pm) attachments.

Reads Ora's JSON/CSV exports, or the ZIP saved by the "Save my Ora" bookmark
(read-only), downloads the attached files from
their public S3 URLs and sorts them by project and by task.
No call to the Ora API, no account login: only HTTP GET requests on the URLs
found in the exports.

Standard library only (Python 3.9+).
"""

import argparse
import csv
import hashlib
import http.client
import io
import json
import mimetypes
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

__version__ = "1.4.0"
DOWNLOADS = Path.home() / "Downloads"
DEFAULT_OUT = Path.home() / "Documents" / "Ora - Attachments"
BOOKMARK_PAGE = "https://bleuetdesign.github.io/ora-attachments-exporter/"
COMPLEMENT_FORMAT = "ora-complement"  # file written by ora_complement.js
USER_AGENT = "ora-attachments-exporter/" + __version__

S3_URL_RE = re.compile(r"https?://[A-Za-z0-9.-]+\.amazonaws\.com/[^\s()\[\]<>\"'`]+")
# In some exports, the "url" field of an attachment holds only the S3 key (e.g. "3411084c...f7261.zip"),
# not a full URL. These keys live in this bucket:
ATTACHMENTS_BASE = "https://ora-attachments.s3.amazonaws.com/"
ANY_URL_RE = re.compile(r"https?://[^\s()\[\]<>\"'`]+")
TRAILING_PUNCT = ".,;:!?"
FORBIDDEN_RE = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')
EXT_RE = re.compile(r"^(.+?)(\.[A-Za-z0-9]{1,10})$")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *("COM%d" % i for i in range(1, 10)),
                    *("LPT%d" % i for i in range(1, 10))}

MANIFEST_FIELDS = [
    "project", "attachmentId", "taskId", "taskSecondId", "taskTitle", "listName",
    "fileTitle", "uploadDate", "sourceUrl", "localPath", "expectedSize", "actualSize", "sha256",
    "source", "status", "error",
]
HAS_FILE = ("ok", "skipped", "size_mismatch")
LINKS_FILE = "external-links.csv"
LINKS_FIELDS = ["project", "taskId", "task", "origin", "url"]
NOT_DONE = "not processed: run interrupted before this file"
NO_TASK_DIR = "_no-task"
CSV_WARNING = (
    "WARNING: {projects} read from CSV. Ora's default CSV has no attachment column and only contains "
    "active tasks. Only images embedded in task descriptions are found ({count} URLs here); "
    "all other attachments are NOT exported. Use the JSON export for a complete export."
)


class UsageError(Exception):
    """Usage error: message shown, exit code 2."""


class RemoteMissing(Exception):
    """The server answers 403 or 404: the file no longer exists."""


class DownloadError(Exception):
    """Permanent download failure."""


class CertificateError(DownloadError):
    """TLS certificate cannot be verified: retrying is pointless."""


class _Retryable(Exception):
    pass


# Well-known root certificate bundles, used when Python's default store is empty
# (python.org Python on macOS until "Install Certificates.command" has been run).
CA_BUNDLES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
              "/etc/ssl/ca-bundle.pem", "/opt/homebrew/etc/ca-certificates/cert.pem",
              "/usr/local/etc/ca-certificates/cert.pem")
CERT_HELP = (
    "TLS certificate error: Python cannot find any root certificates. On macOS, run "
    "\"/Applications/Python 3.x/Install Certificates.command\", or set SSL_CERT_FILE to a "
    "certificate file (for example /etc/ssl/cert.pem), then run the same command again."
)


def make_ssl_context():
    """Verifying TLS context; fills the store if it is empty. Never disables verification."""
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0) == 0:
        try:
            import certifi  # optional
            ctx.load_verify_locations(certifi.where())
        except (ImportError, OSError, ssl.SSLError):
            pass
        for bundle in CA_BUNDLES:
            if ctx.cert_store_stats().get("x509_ca", 0):
                break
            if os.path.isfile(bundle):
                try:
                    ctx.load_verify_locations(bundle)
                except (OSError, ssl.SSLError):
                    pass
    return ctx


# --------------------------------------------------------------------------- names and URLs

def split_ext(name):
    m = EXT_RE.match(name)
    return (m.group(1), m.group(2)) if m else (name, "")


def sanitize_name(name, max_len=100, keep_ext=True):
    """File name valid on macOS, Linux and Windows."""
    name = unicodedata.normalize("NFC", str(name or ""))
    name = FORBIDDEN_RE.sub("_", name).strip().rstrip(". ")
    stem, ext = split_ext(name) if keep_ext else (name, "")
    if stem.split(".")[0].upper() in WINDOWS_RESERVED:
        stem = "_" + stem
    if len(stem) + len(ext) > max_len:
        stem = stem[: max_len - len(ext)].rstrip(". ")
    return (stem + ext).rstrip(". ") or "_"


def resolve_url(raw):
    """Turns a bare S3 key from the JSON "url" field into a full download URL."""
    raw = (raw or "").strip()
    if raw and not urlsplit(raw).scheme:
        return ATTACHMENTS_BASE + quote(raw.lstrip("/"), safe="/")
    return raw


def s3_key(url):
    """Last segment of the URL path, without the "?..." part."""
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


def split_id_param(url):
    """Removes the "?id=" parameter from a URL and returns it separately."""
    parts = urlsplit(url)
    found, rest = None, []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        if k == "id" and found is None:
            found = v
        else:
            rest.append((k, v))
    return urlunsplit(parts._replace(query=urlencode(rest))), found


def is_s3(url):
    host = urlsplit(url).hostname or ""
    return host.endswith(".amazonaws.com") and "s3" in host


def download_url(url):
    """URL actually requested: on S3, the "?<timestamp>" suffix is useless."""
    return urlunsplit(urlsplit(url)._replace(query="")) if is_s3(url) else url


def clean_found_url(url):
    return url.rstrip(TRAILING_PUNCT)


def ext_from_type(file_type):
    ft = str(file_type or "").strip().lower().lstrip(".")
    if not ft:
        return ""
    if "/" in ft:
        guess = mimetypes.guess_extension(ft.split(";")[0].strip())
        if guess and guess != ".bin":
            return guess
        ft = ft.split("/", 1)[1].split("+")[0]
    return "." + ft if re.fullmatch(r"[a-z0-9]{1,10}", ft) else ""


def ensure_extension(title, url, file_type):
    """Adds an extension to the title if it has none (taken from the URL or fileType)."""
    if split_ext(title)[1]:
        return title
    return title + (split_ext(s3_key(url))[1] or ext_from_type(file_type))


def to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def sid(value):
    return "" if value is None else str(value)


# --------------------------------------------------------------------------- reading exports

class Plan:
    def __init__(self):
        self.entries = []
        self.external_links = []
        self.projects = []       # names, in order
        self.csv_projects = []   # projects read from CSV
        self._paths = set()
        self._project_dirs = set()
        self._links_seen = set()

    def project_dir(self, name, stem):
        d = sanitize_name(name, keep_ext=False)
        if d in self._project_dirs:
            d = sanitize_name("%s (%s)" % (name, stem), keep_ext=False)
        self._project_dirs.add(d)
        return d

    def add(self, entry):
        path = entry["localPath"]
        if path in self._paths:  # should not happen: the attachmentId makes names unique
            stem, ext = split_ext(path)
            n = 2
            while "%s (%d)%s" % (stem, n, ext) in self._paths:
                n += 1
            path = entry["localPath"] = "%s (%d)%s" % (stem, n, ext)
        self._paths.add(path)
        self.entries.append(entry)

    def add_links(self, project, task_id, task_label, origin, text):
        for raw in ANY_URL_RE.findall(text or ""):
            url = clean_found_url(raw)
            key = (project, task_id, url)
            if is_s3(url) or key in self._links_seen:
                continue
            self._links_seen.add(key)
            self.external_links.append({"project": project, "taskId": task_id, "task": task_label,
                                        "origin": origin, "url": url})


class TaskDirs:
    """Folder name "#<secondId> - <title>", unique per task (secondIds can be duplicated)."""

    def __init__(self):
        self.by_task = {}
        self.used = set()

    def register(self, task_id, second_id, title):
        task_id = sid(task_id)
        if task_id in self.by_task:
            return
        label = ("#%s - %s" % (second_id, title)) if sid(second_id) else ("task %s - %s" % (task_id, title))
        name = sanitize_name(label, max_len=80, keep_ext=False)
        if name in self.used:
            suffix = " [%s]" % task_id
            name = sanitize_name(label, max_len=80 - len(suffix), keep_ext=False) + suffix
        self.used.add(name)
        self.by_task[task_id] = name

    def get(self, task_id):
        return self.by_task.get(sid(task_id), NO_TASK_DIR)


def new_entry(**kw):
    e = {f: "" for f in MANIFEST_FIELDS}
    e.update(expectedSize=None, actualSize=None)
    e.update(kw)
    return e


def file_name(attachment_id, title, url, file_type):
    title = ensure_extension(title or s3_key(url) or "file", url, file_type)
    return sanitize_name("%s - %s" % (attachment_id, title) if attachment_id else title)


def default_exports():
    """The newest ZIP saved by the "Save my Ora" bookmark in the Downloads folder."""
    found = sorted(DOWNLOADS.glob("ora-backup-*.zip"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise UsageError("No ora-backup-….zip file in %s. First save your data with the \"Save my Ora\" bookmark "
                         "(%s), or give your exports with --exports." % (DOWNLOADS, BOOKMARK_PAGE))
    return found[-1]


def read_export(src):
    """Text of an export: a file on disk, or ("zip", archive, member name)."""
    if isinstance(src, tuple):
        with zipfile.ZipFile(src[1]) as z:
            return z.read(src[2]).decode("utf-8-sig")
    return src.read_text(encoding="utf-8-sig")


def find_exports(exports, source):
    """Returns [(stem, kind, path)]: one file per project according to --source."""
    if not exports.exists():
        raise UsageError("Exports not found: %s" % exports)
    if exports.is_file() and exports.suffix.lower() == ".zip":
        groups = {}
        try:
            with zipfile.ZipFile(exports) as z:
                names = z.namelist()
        except zipfile.BadZipFile:
            raise UsageError("Damaged ZIP file: %s" % exports)
        for name in sorted(names):
            member = Path(name)
            kind = member.suffix.lower().lstrip(".")
            if kind in ("json", "csv") and not name.endswith("/") and "__MACOSX" not in member.parts:
                groups.setdefault(member.stem, {})[kind] = ("zip", exports, name)
        return _choose(groups, source, exports)
    if exports.is_file():
        kind = exports.suffix.lower().lstrip(".")
        if kind not in ("json", "csv"):
            raise UsageError("Unrecognized export file (expected .json or .csv): %s" % exports)
        if source not in ("auto", kind):
            raise UsageError("--source %s does not match the file %s" % (source, exports.name))
        return [(exports.stem, kind, exports)]
    groups = {}
    for p in sorted(exports.iterdir()):
        kind = p.suffix.lower().lstrip(".")
        if p.is_file() and kind in ("json", "csv"):
            groups.setdefault(p.stem, {})[kind] = p
    return _choose(groups, source, exports)


def _choose(groups, source, exports):
    chosen = []
    for stem, kinds in sorted(groups.items()):
        kind = ("json" if "json" in kinds else "csv") if source == "auto" else source
        if kind in kinds:
            chosen.append((stem, kind, kinds[kind]))
    if not chosen:
        raise UsageError("No %s export found in %s" % ("" if source == "auto" else source, exports))
    return chosen


def collect(exports, source="auto", pictures=False):
    exports = Path(exports).expanduser()
    plan = Plan()
    chosen = find_exports(exports, source)
    loaded = []
    att_keys, att_ids = set(), set()
    for stem, kind, path in chosen:
        if kind == "json":
            data = json.loads(read_export(path))
            if isinstance(data, dict) and data.get("format") == COMPLEMENT_FORMAT:
                continue  # ora_complement.js output: no attachments to download, kept as is
            for a in data.get("attachments") or []:
                att_ids.add(sid(a.get("id")))
                url = (a.get("url") or a.get("externalUrl") or "").strip()
                if url:
                    att_keys.add(s3_key(url))
            loaded.append((stem, kind, data))
        else:
            loaded.append((stem, kind, list(csv.DictReader(io.StringIO(read_export(path), newline="")))))
    if not loaded:
        raise UsageError("No project export found in %s (only the ora_complement.js file)" % exports)
    seen_members = set()
    for stem, kind, data in loaded:
        if kind == "json":
            _plan_json(plan, stem, data, att_keys, att_ids, pictures, seen_members)
        else:
            _plan_csv(plan, stem, data)
    return plan


def _plan_json(plan, stem, data, att_keys, att_ids, pictures, seen_members):
    proj = data.get("project") or {}
    name = proj.get("title") or stem
    pdir = plan.project_dir(name, stem)
    plan.projects.append(name)
    tasks = {sid(t.get("id")): t for t in data.get("tasks") or []}
    lists = {sid(l.get("id")): l.get("title") or l.get("name") or "" for l in data.get("lists") or []}
    dirs = TaskDirs()
    for t in data.get("tasks") or []:
        dirs.register(t.get("id"), t.get("secondId"), t.get("title") or "")

    def task_fields(task_id):
        t = tasks.get(sid(task_id))
        if not t:
            return dict(taskId=sid(task_id), taskSecondId="", taskTitle="", listName="")
        return dict(taskId=sid(task_id), taskSecondId=sid(t.get("secondId")), taskTitle=t.get("title") or "",
                    listName=lists.get(sid(t.get("listId")), ""))

    for a in data.get("attachments") or []:
        raw = resolve_url(a.get("url")) or (a.get("externalUrl") or "").strip()
        url, _ = split_id_param(raw) if raw else ("", None)
        aid = sid(a.get("id"))
        plan.add(new_entry(
            project=name, attachmentId=aid, **task_fields(a.get("taskId")),
            fileTitle=a.get("fileTitle") or "", uploadDate=a.get("uploadDate") or "", sourceUrl=url,
            localPath="/".join([pdir, dirs.get(a.get("taskId")),
                                file_name(aid, a.get("fileTitle"), url, a.get("fileType"))]),
            expectedSize=to_int(a.get("fileSize")), source="json"))

    seen = set()
    for t in data.get("tasks") or []:
        tid = sid(t.get("id"))
        desc = t.get("description") or ""
        for raw in S3_URL_RE.findall(desc):
            url, aid = split_id_param(clean_found_url(raw))
            key = s3_key(url)
            if key in att_keys or (aid and aid in att_ids) or (tid, key) in seen:
                continue
            seen.add((tid, key))
            plan.add(new_entry(
                project=name, attachmentId=aid or "", **task_fields(tid), fileTitle=key, sourceUrl=url,
                localPath="/".join([pdir, dirs.get(tid), file_name(aid or "desc", key, url, "")]),
                source="description"))
        plan.add_links(name, tid, dirs.get(tid), "description", desc)
    for c in data.get("taskComments") or []:
        plan.add_links(name, sid(c.get("taskId")), dirs.get(c.get("taskId")), "comment", c.get("comment"))
    plan.add_links(name, "", "", "project description", proj.get("description"))

    if pictures:
        pic = (proj.get("picture") or "").strip()
        if pic:
            plan.add(new_entry(project=name, fileTitle=s3_key(pic), sourceUrl=pic, source="picture",
                               localPath="_pictures/projects/%s%s" % (sid(proj.get("id")) or sanitize_name(stem),
                                                                     split_ext(s3_key(pic))[1])))
        for m in data.get("members") or []:
            uid = sid(m.get("userId") if m.get("userId") is not None else m.get("id"))
            pic = (m.get("profilePicture") or "").strip()
            if not pic or uid in seen_members:
                continue
            seen_members.add(uid)
            plan.add(new_entry(project=name, fileTitle=s3_key(pic), sourceUrl=pic, source="picture",
                               localPath="_pictures/members/%s%s" % (uid, split_ext(s3_key(pic))[1])))


def _plan_csv(plan, stem, rows):
    name = stem
    pdir = plan.project_dir(name, stem)
    plan.projects.append(name)
    if not any("Attachments" in r for r in rows[:1]):  # Ora's CSV with the Attachments option, or the bookmark's
        plan.csv_projects.append(name)
    dirs = TaskDirs()
    for r in rows:
        dirs.register(r.get("Global ID"), r.get("ID"), r.get("Title") or "")
    seen = set()
    for r in rows:
        tid = sid(r.get("Global ID"))
        desc = r.get("Description") or ""
        listed = [u.strip() for u in re.split(r",(?=https?://)", r.get("Attachments") or "") if u.strip()]
        for raw in [u for u in listed if is_s3(u)] + S3_URL_RE.findall(desc):
            url, aid = split_id_param(clean_found_url(raw))
            key = s3_key(url)
            if (tid, key) in seen:
                continue
            seen.add((tid, key))
            plan.add(new_entry(
                project=name, attachmentId=aid or "", taskId=tid, taskSecondId=sid(r.get("ID")),
                taskTitle=r.get("Title") or "", listName=r.get("List") or "", fileTitle=key, sourceUrl=url,
                localPath="/".join([pdir, dirs.get(tid), file_name(aid or "desc", key, url, "")]),
                source="csv"))
        plan.add_links(name, tid, dirs.get(tid), "description", desc)
        plan.add_links(name, tid, dirs.get(tid), "attachment", " ".join(u for u in listed if not is_s3(u)))


# --------------------------------------------------------------------------- downloading

class Downloader:
    """Sequential HTTP GETs, retried on network errors or 5xx."""

    def __init__(self, rewrite=None, retry_delays=(1, 3), pause=0.1, timeout=60, before_request=None):
        self.rewrite = rewrite
        self.retry_delays = tuple(retry_delays)  # 2 waits → at most 3 attempts
        self.pause = pause
        self.timeout = timeout
        self.before_request = before_request
        self.ssl_context = make_ssl_context()

    def fetch(self, url, part):
        """Downloads url into the file part. Returns (size, sha256, content_type)."""
        if urlsplit(url).scheme not in ("http", "https"):
            raise DownloadError("URL scheme refused")
        attempts = len(self.retry_delays) + 1
        last = ""
        for i in range(attempts):
            if self.before_request:
                self.before_request(url)
            try:
                return self._once(url, part)
            except urllib.error.HTTPError as e:
                if e.code in (403, 404):
                    raise RemoteMissing("HTTP %d" % e.code) from None
                if e.code < 500:
                    raise DownloadError("HTTP %d" % e.code) from None
                last = "HTTP %d" % e.code
            except urllib.error.URLError as e:
                if isinstance(e.reason, ssl.SSLCertVerificationError):
                    raise CertificateError("TLS certificate cannot be verified: %s" % e.reason) from None
                last = "%s: %s" % (type(e).__name__, e)
            except (_Retryable, urllib.error.URLError, http.client.HTTPException, OSError) as e:
                last = "%s: %s" % (type(e).__name__, e)
            if i < attempts - 1:
                time.sleep(self.retry_delays[i])
        raise DownloadError("%s (after %d attempts)" % (last, attempts))

    def _once(self, url, part):
        target = self.rewrite(url) if self.rewrite else url
        req = urllib.request.Request(target, headers={"User-Agent": USER_AGENT}, method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout, context=self.ssl_context) as resp:
            announced = to_int(resp.headers.get("Content-Length"))
            ctype = resp.headers.get("Content-Type") or ""
            h, n = hashlib.sha256(), 0
            with open(part, "wb") as f:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    n += len(chunk)
        if announced is not None and announced != n:
            raise _Retryable("connection cut: %d of %d bytes received" % (n, announced))
        return n, h.hexdigest(), ctype


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def part_path(path):
    return path.with_name(path.name + ".part")


def copy_atomic(src, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = part_path(dest)
    shutil.copyfile(src, part)
    os.replace(part, dest)


def set_result(e, size, digest):
    e["actualSize"], e["sha256"] = size, digest
    if e["expectedSize"] is not None and e["expectedSize"] != size:
        e["status"] = "size_mismatch"
        e["error"] = "expected size %d, got %d" % (e["expectedSize"], size)
    else:
        e["status"], e["error"] = "ok", ""


def add_ext_if_missing(e, ext):
    if ext and not split_ext(e["localPath"].rsplit("/", 1)[-1])[1]:
        e["localPath"] += ext


def file_matches(out, e):
    p = out / e["localPath"]
    return (p.is_file() and e.get("actualSize") is not None and p.stat().st_size == e["actualSize"]
            and sha256_file(p) == e.get("sha256"))


# --------------------------------------------------------------------------- manifest

def entry_key(e):
    return (e["project"], e["source"], e["attachmentId"], e["taskId"], e["sourceUrl"])


def write_atomic(path, text, encoding="utf-8"):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding=encoding, newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def save_manifest(out, entries, meta):
    data = dict(meta, generatedAt=datetime.now(timezone.utc).isoformat(timespec="seconds"), entries=entries)
    write_atomic(out / "manifest.json", json.dumps(data, ensure_ascii=False, indent=1))
    write_csv(out / "manifest.csv", MANIFEST_FIELDS, entries)


def write_csv(path, fields, rows):
    from io import StringIO
    buf = StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})
    write_atomic(path, buf.getvalue(), encoding="utf-8-sig")  # BOM: opens correctly in Excel


def load_manifest(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("entries") or []
    except (OSError, ValueError):
        return []


# --------------------------------------------------------------------------- commands

def guard_paths(exports, *outs):
    """The exports folder is read-only: no output inside it. A ZIP is only read, wherever the output goes."""
    if exports.is_file() and exports.suffix.lower() == ".zip":
        return
    root = exports.resolve() if exports.is_dir() else exports.resolve().parent
    for o in outs:
        r = o.resolve()
        if r == root or root in r.parents:
            raise UsageError("The output folder %s is inside the exports folder (read-only)." % o)


def remove_parts(out):
    for p in out.rglob("*.part"):
        try:
            p.unlink()
        except OSError:
            pass


def cmd_export(args, downloader):
    exports = Path(args.exports).expanduser() if args.exports else default_exports()
    out = Path(args.out).expanduser()
    print("Reading %s\nSaving into %s\n" % (exports, out))
    guard_paths(exports, out)
    plan = collect(exports, args.source, args.pictures)
    if args.dry_run:
        print_plan(plan)
        return 0
    downloader = downloader or Downloader()
    out.mkdir(parents=True, exist_ok=True)
    remove_parts(out)
    write_csv(out / LINKS_FILE, LINKS_FIELDS, plan.external_links)
    meta = {"tool": USER_AGENT, "command": "export", "exports": str(exports), "source": args.source}
    entries = plan.entries
    previous = {entry_key(e): e for e in load_manifest(out / "manifest.json")}
    for e in entries:
        e.update(status="failed", error=NOT_DONE)
        prev = previous.get(entry_key(e))
        if prev and prev.get("status") in ("ok", "skipped") and file_matches(out, prev):
            e.update(localPath=prev["localPath"], actualSize=prev["actualSize"], sha256=prev["sha256"],
                     status="skipped", error="")
        elif not e["sourceUrl"]:
            e.update(error="no URL in the export")

    groups = {}
    for e in entries:
        if e["sourceUrl"]:
            groups.setdefault(download_url(e["sourceUrl"]), []).append(e)
    save_manifest(out, entries, meta)
    print_csv_warning(plan)
    total = len(groups)
    succeeded = 0
    try:
        for i, (url, group) in enumerate(groups.items(), 1):
            todo = [e for e in group if e["status"] != "skipped"]
            if not todo:
                continue
            done = next((e for e in group if e["status"] == "skipped"), None)
            if done is None:
                first = todo[0]
                dest = out / first["localPath"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                part = part_path(dest)
                try:
                    size, digest, ctype = downloader.fetch(url, part)
                except (RemoteMissing, DownloadError) as exc:
                    status = "missing_remote" if isinstance(exc, RemoteMissing) else "failed"
                    for e in todo:
                        e.update(status=status, error=str(exc))
                    _unlink(part)
                    print("[%d/%d] %-14s %s — %s" % (i, total, status, first["localPath"], exc))
                    save_manifest(out, entries, meta)
                    if isinstance(exc, CertificateError) and not succeeded:
                        print("\n" + CERT_HELP)
                        print_summary(plan, entries)
                        return 1
                    time.sleep(downloader.pause)
                    continue
                ext = ext_from_type(ctype) if "octet-stream" not in ctype else ""
                for e in todo:
                    add_ext_if_missing(e, ext)
                dest = out / first["localPath"]
                os.replace(part, dest)
                set_result(first, size, digest)
                done = first
                succeeded += 1
                time.sleep(downloader.pause)
            src = out / done["localPath"]
            for e in todo:
                if e is done:
                    continue
                add_ext_if_missing(e, split_ext(src.name)[1])
                copy_atomic(src, out / e["localPath"])
                set_result(e, done["actualSize"], done["sha256"])
            print("[%d/%d] %-14s %s" % (i, total, todo[0]["status"], todo[0]["localPath"]))
            save_manifest(out, entries, meta)
    except KeyboardInterrupt:
        remove_parts(out)
        save_manifest(out, entries, meta)
        print("\nInterrupted. Run the same command again to resume where it stopped.")
        print_summary(plan, entries)
        return 130
    return print_summary(plan, entries)


def _unlink(p):
    try:
        p.unlink()
    except OSError:
        pass


def cmd_relink(args):
    exports, files, out = (Path(a).expanduser() for a in (args.exports, args.files, args.out))
    if not files.is_dir():
        raise UsageError("--files folder not found: %s" % files)
    guard_paths(exports, out)
    if out.resolve() == files.resolve():
        raise UsageError("--out must be different from --files.")
    plan = collect(exports, args.source, args.pictures)
    out.mkdir(parents=True, exist_ok=True)
    remove_parts(out)
    out_r = out.resolve()
    candidates = [p for p in files.rglob("*") if p.is_file() and not p.name.endswith((".part", ".tmp"))
                  and out_r not in p.resolve().parents]
    by_name, by_stem = {}, {}
    for p in candidates:
        n = unicodedata.normalize("NFC", p.name)
        by_name.setdefault(n, p)
        by_stem.setdefault(split_ext(n)[0], p)
    known_sha = {}
    for e in load_manifest(files / "manifest.json"):
        if e.get("sha256") and e.get("status") in HAS_FILE:
            known_sha.setdefault(download_url(e["sourceUrl"]), e["sha256"])
    by_sha = {}
    if known_sha:
        for p in candidates:
            by_sha.setdefault(sha256_file(p), p)
    for e in plan.entries:
        key = unicodedata.normalize("NFC", s3_key(e["sourceUrl"])) if e["sourceUrl"] else ""
        src = (by_name.get(key) or by_stem.get(split_ext(key)[0])) if key else None
        if src is None and e["sourceUrl"]:
            src = by_sha.get(known_sha.get(download_url(e["sourceUrl"]), ""))
        if src is None:
            e.update(status="failed", error="file not found in --files")
            continue
        add_ext_if_missing(e, split_ext(src.name)[1])
        copy_atomic(src, out / e["localPath"])
        set_result(e, (out / e["localPath"]).stat().st_size, sha256_file(out / e["localPath"]))
    meta = {"tool": USER_AGENT, "command": "relink", "exports": str(exports), "files": str(files),
            "source": args.source}
    save_manifest(out, plan.entries, meta)
    write_csv(out / LINKS_FILE, LINKS_FIELDS, plan.external_links)
    print_csv_warning(plan)
    return print_summary(plan, plan.entries)


def cmd_verify(args):
    out = Path(args.out).expanduser()
    path = out / "manifest.json"
    if not path.is_file():
        raise UsageError("Manifest not found: %s" % path)
    entries = load_manifest(path)
    problems, good = [], 0
    for e in entries:
        st = e.get("status")
        if st not in HAS_FILE:
            problems.append((e, "not downloaded (%s) %s" % (st, e.get("error") or "")))
            continue
        p = out / e["localPath"]
        if not p.is_file():
            problems.append((e, "file missing"))
        elif p.stat().st_size != e.get("actualSize"):
            problems.append((e, "size %d ≠ manifest %s" % (p.stat().st_size, e.get("actualSize"))))
        elif sha256_file(p) != e.get("sha256"):
            problems.append((e, "SHA-256 differs"))
        elif e.get("expectedSize") is not None and e["expectedSize"] != e.get("actualSize"):
            problems.append((e, "size %s ≠ fileSize %s" % (e.get("actualSize"), e["expectedSize"])))
        else:
            good += 1
    for p in out.rglob("*.part"):
        problems.append(({"localPath": str(p.relative_to(out)), "project": ""}, "incomplete .part file"))
    print("%d file(s) verified without problems, %d problem(s)." % (good, len(problems)))
    for e, why in problems:
        print("  - %s: %s" % (e.get("localPath") or e.get("sourceUrl"), why))
    return 1 if problems else 0


# --------------------------------------------------------------------------- output

def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024


def print_csv_warning(plan):
    if plan.csv_projects:
        count = sum(1 for e in plan.entries if e["source"] == "csv")
        print(CSV_WARNING.format(projects=", ".join(plan.csv_projects), count=count))
        print()


def print_plan(plan):
    print("Dry run (--dry-run): no file is written or downloaded.\n")
    print("%-34s %6s %5s %5s %5s %6s %10s %6s" % ("Project", "files", "json", "desc", "csv", "URLs", "size", "links"))
    for name in plan.projects:
        es = [e for e in plan.entries if e["project"] == name and e["source"] != "picture"]
        src = lambda s: sum(1 for e in es if e["source"] == s)  # noqa: E731
        size = sum(e["expectedSize"] or 0 for e in es)
        links = sum(1 for l in plan.external_links if l["project"] == name)
        print("%-34s %6d %5d %5d %5d %6d %10s %6d" % (name[:34], len(es), src("json"), src("description"),
                                                      src("csv"), len({download_url(e["sourceUrl"]) for e in es
                                                                       if e["sourceUrl"]}), human(size), links))
    pics = [e for e in plan.entries if e["source"] == "picture"]
    files = [e for e in plan.entries if e["source"] != "picture"]
    print("\nTotal: %d attachment(s), %d unique URL(s), %s expected (from fileSize)."
          % (len(files), len({download_url(e["sourceUrl"]) for e in files if e["sourceUrl"]}),
             human(sum(e["expectedSize"] or 0 for e in files))))
    print("  json: %d   description: %d   csv: %d   no task: %d   no URL: %d"
          % tuple([sum(1 for e in files if e["source"] == s) for s in ("json", "description", "csv")]
                  + [sum(1 for e in files if NO_TASK_DIR in e["localPath"]),
                     sum(1 for e in files if not e["sourceUrl"])]))
    if pics:
        print("  project and member pictures (--pictures): %d" % len(pics))
    print("  external links (not downloaded, listed in " + LINKS_FILE + "): %d" % len(plan.external_links))
    print()
    print_csv_warning(plan)


def print_summary(plan, entries):
    print("\nSummary")
    print("%-34s %6s %8s %7s" % ("Project", "ok", "skipped", "failed"))
    groups = list(plan.projects)
    if any(e["source"] == "picture" for e in entries):
        groups.append("_pictures")
    totals = [0, 0, 0]
    for name in groups:
        es = [e for e in entries if (e["source"] == "picture") == (name == "_pictures")
              and (name == "_pictures" or e["project"] == name)]
        c = [sum(1 for e in es if e["status"] == "ok"), sum(1 for e in es if e["status"] == "skipped")]
        c.append(len(es) - c[0] - c[1])
        totals = [a + b for a, b in zip(totals, c)]
        print("%-34s %6d %8d %7d" % (name[:34], *c))
    size = sum(e["actualSize"] or 0 for e in entries if e["status"] in HAS_FILE)
    print("%-34s %6d %8d %7d" % ("TOTAL", *totals))
    print("Bytes on disk (manifest): %d (%s)" % (size, human(size)))
    failures = [e for e in entries if e["status"] not in ("ok", "skipped")]
    if failures:
        print("\nFailures (%d):" % len(failures))
        for e in failures:
            print("  - [%s] %s — %s — %s" % (e["status"], e["localPath"], e["error"], e["sourceUrl"]))
    print_csv_warning(plan)
    return 1 if failures else 0


# --------------------------------------------------------------------------- CLI

def build_parser():
    p = argparse.ArgumentParser(prog="ora_attachments.py",
                                description="Exports Ora attachments from the JSON/CSV exports.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, defaults=False):
        if defaults:
            sp.add_argument("--exports", help="exports folder, a single .json/.csv file or an ora-backup ZIP "
                                              "(default: the newest ora-backup-*.zip in Downloads)")
            sp.add_argument("--out", default=str(DEFAULT_OUT),
                            help="output folder, outside the exports folder (default: %(default)s)")
        else:
            sp.add_argument("--exports", required=True, help="exports folder, a single .json/.csv file or a ZIP")
            sp.add_argument("--out", required=True, help="output folder (outside the exports folder)")
        sp.add_argument("--source", choices=("json", "csv", "auto"), default="auto",
                        help="auto (default): JSON if present, otherwise CSV")
        sp.add_argument("--pictures", action="store_true", help="also save project and member pictures")

    e = sub.add_parser("export", help="download the files and sort them by project and task")
    common(e, defaults=True)
    e.add_argument("--dry-run", action="store_true", help="show what would be done, without writing anything")
    r = sub.add_parser("relink", help="sort already downloaded files, without downloading anything")
    common(r)
    r.add_argument("--files", required=True, help="folder of already downloaded files")
    v = sub.add_parser("verify", help="check the files listed in the manifest")
    v.add_argument("--out", required=True)
    return p


def main(argv=None, downloader=None):
    argv = sys.argv[1:] if argv is None else argv
    simple = not argv  # started with no arguments: everything by default, then show the result
    args = build_parser().parse_args(argv or ["export", "--pictures"])
    try:
        if args.command == "export":
            code = cmd_export(args, downloader)
            if simple and sys.platform == "darwin" and Path(args.out).expanduser().is_dir():
                subprocess.run(["open", str(Path(args.out).expanduser())], check=False)
            return code
        if args.command == "relink":
            return cmd_relink(args)
        return cmd_verify(args)
    except UsageError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass
    sys.exit(main())
