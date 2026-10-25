import csv
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ora_attachments as oa  # noqa: E402
from fixtures import CONTENT, build_exports  # noqa: E402


class FakeS3(BaseHTTPRequestHandler):
    """Local fake S3. Path: /<bucket>/<key>."""

    hits = {}
    fail_first = set()      # paths that return 503 on the first attempt
    truncate_first = set()  # paths whose connection drops on the first attempt

    def log_message(self, *args):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path.lstrip("/")
        n = FakeS3.hits[path] = FakeS3.hits.get(path, 0) + 1
        if path not in CONTENT:
            self.send_response(404)
            self.end_headers()
            return
        if path in FakeS3.fail_first and n == 1:
            self.send_response(503)
            self.end_headers()
            return
        body = CONTENT[path]
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "image/png" if path.endswith("abcdef") else "application/octet-stream")
        self.end_headers()
        if path in FakeS3.truncate_first and n == 1:
            self.wfile.write(body[: len(body) // 2])
            self.wfile.flush()
            self.close_connection = True
            return
        self.wfile.write(body)


def snapshot(folder: Path):
    return sorted((str(p.relative_to(folder)), p.stat().st_size, p.stat().st_mtime_ns) for p in folder.rglob("*"))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeS3)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeS3.hits = {}
        FakeS3.fail_first = set()
        FakeS3.truncate_first = set()
        self.tmp = Path(tempfile.mkdtemp())
        self.exports = build_exports(self.tmp / "exports")
        self.out = self.tmp / "output"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def downloader(self, **kw):
        port = self.port

        def rewrite(url):
            parts = urlsplit(url)
            bucket = parts.netloc.split(".")[0]
            return "http://127.0.0.1:%d/%s%s" % (port, bucket, parts.path)

        return oa.Downloader(rewrite=rewrite, retry_delays=(0, 0), pause=0, **kw)

    def export(self, *extra, downloader=None, exports=None, **kw):
        args = ["export", "--exports", str(exports or self.exports), "--out", str(self.out), *extra]
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = oa.main(args, downloader=downloader or self.downloader(), **kw)
        return code, buf.getvalue()

    def manifest(self):
        return json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))["entries"]

    def by_id(self):
        return {(e["attachmentId"], e["taskId"]): e for e in self.manifest()}


class TestHelpers(unittest.TestCase):
    def test_sanitize_replaces_forbidden_chars(self):
        self.assertEqual(oa.sanitize_name('a/b\\c:d*e?f"g<h>i|j\x01.png'), "a_b_c_d_e_f_g_h_i_j_.png")

    def test_sanitize_strips_trailing_dots_and_spaces(self):
        self.assertEqual(oa.sanitize_name("rapport final. . ", keep_ext=False), "rapport final")

    def test_sanitize_truncates_keeping_extension(self):
        name = oa.sanitize_name("x" * 300 + ".jpeg")
        self.assertEqual(len(name), 100)
        self.assertTrue(name.endswith(".jpeg"))

    def test_sanitize_windows_reserved(self):
        self.assertEqual(oa.sanitize_name("CON.txt"), "_CON.txt")

    def test_split_id_param(self):
        self.assertEqual(oa.split_id_param("https://h/k.png?id=42"), ("https://h/k.png", "42"))
        self.assertEqual(oa.split_id_param("https://h/k.png"), ("https://h/k.png", None))

    def test_resolve_url(self):
        self.assertEqual(oa.resolve_url("1007ggg.png"), "https://ora-attachments.s3.amazonaws.com/1007ggg.png")
        self.assertEqual(oa.resolve_url("a b.png"), "https://ora-attachments.s3.amazonaws.com/a%20b.png")
        self.assertEqual(oa.resolve_url("https://h/k.png?1"), "https://h/k.png?1")
        self.assertEqual(oa.resolve_url(None), "")
        self.assertEqual(oa.resolve_url("  "), "")

    def test_s3_key(self):
        self.assertEqual(oa.s3_key("https://ora-projects.s3.amazonaws.com/default-09.svg?1715"), "default-09.svg")

    def test_extension_inference(self):
        self.assertEqual(oa.ensure_extension("schéma", "https://h/k.svg", "image/svg+xml"), "schéma.svg")
        self.assertEqual(oa.ensure_extension("schéma", "https://h/k", "image/svg+xml"), "schéma.svg")
        self.assertEqual(oa.ensure_extension("note", "https://h/k", "png"), "note.png")
        self.assertEqual(oa.ensure_extension("deja.pdf", "https://h/k.png", "png"), "deja.pdf")


class TestPlan(Base):
    def test_json_entries_and_layout(self):
        code, out = self.export("--dry-run")
        self.assertEqual(code, 0)
        self.assertFalse(self.out.exists(), "--dry-run must not write anything")
        plan = oa.collect(self.exports, "auto", pictures=False)
        demo = [e for e in plan.entries if e["project"] == "Demo Project"]
        self.assertEqual({e["source"] for e in demo}, {"json", "description"})
        e = {e["attachmentId"]: e for e in demo}
        # empty url → externalUrl
        self.assertTrue(e["1002"]["sourceUrl"].startswith("https://message-attachments"))
        self.assertEqual(e["1002"]["localPath"],
                         "Demo Project/#12 - Review_ mockup _ home/1002 - 2025-05-21T10_47_04,78-04_00.png")
        # duplicate secondId: two distinct folders
        self.assertNotEqual(Path(e["1006"]["localPath"]).parent, Path(e["1002"]["localPath"]).parent)
        # unknown taskId
        self.assertEqual(e["1005"]["localPath"], "Demo Project/_no-task/1005 - diagram.svg")
        self.assertEqual(e["1005"]["taskSecondId"], "")
        # task whose list is missing
        self.assertEqual(e["1003"]["listName"], "")
        self.assertEqual(e["1001"]["listName"], "To do")
        # bare S3 key in "url" → full URL in the attachments bucket
        self.assertEqual(e["1007"]["sourceUrl"], "https://ora-attachments.s3.amazonaws.com/1007ggg.png")
        self.assertEqual(e["1007"]["localPath"], "Demo Project/#13 - Archived task/1007 - last.png")
        # URL only in the description, ?id= removed
        self.assertEqual(e["9999"]["source"], "description")
        self.assertEqual(e["9999"]["sourceUrl"], "https://ora-attachments.s3.amazonaws.com/9999desc.png")
        self.assertIn("csv", out.lower())  # the CSV-only project is announced

    def test_complement_file_skipped(self):
        complement = {"format": "ora-complement", "version": 1, "projects": {"70001": "Demo Project"}, "responses": {}}
        (self.exports / "ora-complement-2026-10-24.json").write_text(json.dumps(complement), encoding="utf-8")
        plan = oa.collect(self.exports, "auto", pictures=False)
        self.assertNotIn("ora-complement-2026-10-24", plan.projects)
        self.assertEqual(sorted(plan.projects), ["CSV-only Project", "Demo Project"])
        with self.assertRaises(oa.UsageError):
            oa.collect(self.exports / "ora-complement-2026-10-24.json", "auto", pictures=False)

    def zip_exports(self, name="ora-backup-2026-10-24.zip"):
        z = self.tmp / "downloads" / name
        z.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(z, "w") as zf:
            for p in self.exports.iterdir():
                zf.write(p, p.name)
            zf.writestr("ora-complement.json", json.dumps({"format": "ora-complement", "version": 1}))
        return z

    def test_zip_same_plan_as_folder(self):
        key = lambda plan: sorted((e["localPath"], e["sourceUrl"]) for e in plan.entries)  # noqa: E731
        self.assertEqual(key(oa.collect(self.zip_exports(), "auto")), key(oa.collect(self.exports, "auto")))

    def test_default_exports_newest_zip_in_downloads(self):
        old = self.zip_exports("ora-backup-2026-10-20.zip")
        new = self.zip_exports("ora-backup-2026-10-24 (1).zip")
        os.utime(old, (1, 1))
        with mock.patch.object(oa, "DOWNLOADS", new.parent):
            self.assertEqual(oa.default_exports(), new)
        with mock.patch.object(oa, "DOWNLOADS", self.tmp / "empty"):
            with self.assertRaises(oa.UsageError):
                oa.default_exports()

    def test_no_arguments_uses_zip_and_default_folder(self):
        z = self.zip_exports()
        out = self.tmp / "Documents" / "Ora - Attachments"
        buf = io.StringIO()
        with mock.patch.object(oa, "DOWNLOADS", z.parent), mock.patch.object(oa, "DEFAULT_OUT", out), \
                mock.patch.object(oa.subprocess, "run") as run, redirect_stdout(buf):
            code = oa.main([], downloader=self.downloader())
        self.assertEqual(code, 1, buf.getvalue())  # the fixtures include a file missing on purpose
        self.assertTrue((out / "manifest.json").exists())
        self.assertTrue((out / "_pictures").is_dir(), "--pictures is on by default")
        if sys.platform == "darwin":
            run.assert_called_once_with(["open", str(out)], check=False)

    def test_zip_output_next_to_it_allowed(self):
        z = self.zip_exports()
        oa.guard_paths(z, z.parent / "Ora - Attachments")  # no error: the ZIP is only read

    def test_csv_attachments_column(self):
        """Ora's CSV with the Attachments option (and the bookmark's CSV) lists the files of each task."""
        f = self.tmp / "With attachments.csv"
        f.write_text("\ufeffGlobal ID,List,ID,Title,Description,Attachments\r\n"
                     "900003,Done,9,Spec,,\"https://ora-attachments.s3.amazonaws.com/3001spec.pdf,"
                     "https://drive.google.com/file/d/abc,def/view\"\r\n", encoding="utf-8")
        plan = oa.collect(f, "auto", pictures=False)
        self.assertEqual([e["sourceUrl"] for e in plan.entries], ["https://ora-attachments.s3.amazonaws.com/3001spec.pdf"])
        self.assertEqual([l["url"] for l in plan.external_links], ["https://drive.google.com/file/d/abc,def/view"])
        self.assertEqual(plan.csv_projects, [], "no CSV warning: this CSV has an attachment column")

    def test_csv_source(self):
        plan = oa.collect(self.exports / "CSV-only Project.csv", "auto", pictures=False)
        self.assertEqual(len(plan.entries), 1)
        e = plan.entries[0]
        self.assertEqual((e["source"], e["attachmentId"], e["taskId"], e["taskSecondId"]), ("csv", "2001", "900001", "7"))
        self.assertEqual(e["sourceUrl"], "https://ora-attachments.s3.amazonaws.com/2001csv.png")
        self.assertEqual(e["listName"], "Backlog")
        self.assertTrue(plan.csv_projects)

    def test_source_csv_forced_on_folder(self):
        plan = oa.collect(self.exports, "csv", pictures=False)
        self.assertEqual({e["source"] for e in plan.entries}, {"csv"})
        self.assertEqual(len(plan.entries), 2)

    def test_external_links_only_non_s3(self):
        plan = oa.collect(self.exports, "json", pictures=False)
        urls = {l["url"] for l in plan.external_links}
        self.assertIn("https://drive.google.com/drive/folders/XYZ", urls)
        self.assertIn("https://www.figma.com/file/abc/Demo", urls)
        self.assertFalse(any("amazonaws" in u for u in urls))

    def test_pictures_option(self):
        plan = oa.collect(self.exports, "json", pictures=True)
        pics = [e for e in plan.entries if e["source"] == "picture"]
        self.assertEqual({e["localPath"] for e in pics},
                         {"_pictures/projects/70001.svg", "_pictures/members/501"})


class TestExport(Base):
    def test_full_export_then_verify(self):
        before = snapshot(self.exports)
        code, out = self.export("--pictures")
        self.assertEqual(code, 1, "a 404 → non-zero exit code")
        self.assertEqual(before, snapshot(self.exports), "the exports folder must stay untouched")
        m = self.by_id()
        self.assertEqual(m[("1004", "800002")]["status"], "missing_remote")
        self.assertEqual(m[("1007", "800003")]["status"], "ok")
        ok = [e for e in self.manifest() if e["status"] == "ok"]
        for e in ok:
            data = (self.out / e["localPath"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), e["sha256"])
            self.assertEqual(len(data), e["actualSize"])
        # shared URL: a single GET, two copies
        self.assertEqual(FakeS3.hits["ora-attachments/1003ccc.png"], 1)
        self.assertEqual(m[("1003", "800003")]["sha256"], m[("1006", "800002")]["sha256"])
        # extension taken from Content-Type for an image without one
        member = [e for e in self.manifest() if e["localPath"].startswith("_pictures/members/")][0]
        self.assertEqual(member["localPath"], "_pictures/members/501.png")
        self.assertTrue((self.out / "manifest.csv").exists())
        with (self.out / "external-links.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertTrue(any("figma" in r["url"] for r in rows))
        self.assertFalse(list(self.out.rglob("*.part")))
        self.assertIn("missing_remote", out)
        # verify: only the 404 is a problem
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(oa.main(["verify", "--out", str(self.out)]), 1)
        # Once fixed (the missing entry is removed), verify passes.
        data = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        data["entries"] = [e for e in data["entries"] if e["status"] != "missing_remote"]
        (self.out / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(oa.main(["verify", "--out", str(self.out)]), 0)
        # A modified file is detected.
        target = self.out / ok[0]["localPath"]
        target.write_bytes(b"corrupted")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(oa.main(["verify", "--out", str(self.out)]), 1)

    def test_retry_on_5xx_and_truncated_connection(self):
        FakeS3.fail_first = {"ora-attachments/1001aaa.png"}
        FakeS3.truncate_first = {"ora-attachments/1007ggg.png"}
        self.export()
        m = self.by_id()
        self.assertEqual(m[("1001", "800001")]["status"], "ok")
        self.assertEqual(m[("1007", "800003")]["status"], "ok")
        self.assertEqual(FakeS3.hits["ora-attachments/1001aaa.png"], 2)
        self.assertEqual(FakeS3.hits["ora-attachments/1007ggg.png"], 2)

    def test_size_mismatch(self):
        data = json.loads((self.exports / "Demo Project.json").read_text(encoding="utf-8"))
        data["attachments"][0]["fileSize"] = "1"
        (self.exports / "Demo Project.json").write_text(json.dumps(data), encoding="utf-8")
        self.export()
        self.assertEqual(self.by_id()[("1001", "800001")]["status"], "size_mismatch")

    def test_interrupt_then_resume(self):
        calls = {"n": 0}

        def boom(url):
            calls["n"] += 1
            if calls["n"] == 3:
                raise KeyboardInterrupt

        # An abandoned .part must never count as ok.
        code, _ = self.export(downloader=self.downloader(before_request=boom))
        self.assertEqual(code, 130)
        first = self.manifest()
        done = [e for e in first if e["status"] == "ok"]
        self.assertEqual(len({e["sourceUrl"] for e in done}), 2)
        self.assertFalse(list(self.out.rglob("*.part")))
        pending = [e for e in first if e["status"] not in ("ok", "skipped")]
        self.assertTrue(pending)
        # Simulate a .part left behind by a hard stop.
        stray = self.out / pending[0]["localPath"]
        stray.parent.mkdir(parents=True, exist_ok=True)
        Path(str(stray) + ".part").write_bytes(b"half")
        hits_before = dict(FakeS3.hits)
        code, out = self.export()
        m = self.manifest()
        for e in done:
            again = [x for x in m if x["attachmentId"] == e["attachmentId"] and x["taskId"] == e["taskId"]][0]
            self.assertEqual(again["status"], "skipped")
        for path, n in hits_before.items():
            if path in CONTENT and path != "ora-attachments/1004ddd.png":
                self.assertEqual(FakeS3.hits[path], n, "an already ok file was downloaded again: " + path)
        self.assertFalse(list(self.out.rglob("*.part")))
        self.assertTrue(all(e["status"] in ("ok", "skipped", "missing_remote") for e in m))

    def test_certificate_error_not_retried_and_aborts(self):
        import ssl
        import urllib.error
        d = self.downloader()
        calls = []

        def bad_cert(url, part):
            calls.append(url)
            raise urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed"))

        d._once = bad_cert
        code, out = self.export(downloader=d)
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1, "certificate error: no retry and no next file")
        self.assertIn("certificate", out.lower())
        self.assertTrue(all(e["status"] == "failed" for e in self.manifest()))

    def test_ssl_context_has_ca_certificates(self):
        self.assertGreater(oa.make_ssl_context().cert_store_stats()["x509_ca"], 0)

    def test_out_inside_exports_refused(self):
        self.out = self.exports / "output"
        before = snapshot(self.exports)
        code, _ = self.export()
        self.assertEqual(code, 2)
        self.assertEqual(before, snapshot(self.exports))

    def test_csv_warning(self):
        code, out = self.export("--source", "csv")
        self.assertIn("WARNING", out)
        self.assertEqual({e["source"] for e in self.manifest()}, {"csv"})


class TestRelink(Base):
    def test_relink_by_s3_key(self):
        files = self.tmp / "already"
        files.mkdir()
        (files / "1001aaa.png").write_bytes(CONTENT["ora-attachments/1001aaa.png"])
        sub = files / "subfolder"
        sub.mkdir()
        (sub / "1003ccc.png").write_bytes(CONTENT["ora-attachments/1003ccc.png"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = oa.main(["relink", "--exports", str(self.exports / "Demo Project.json"),
                            "--files", str(files), "--out", str(self.out)])
        self.assertEqual(code, 1)  # some files are missing
        self.assertEqual(FakeS3.hits, {}, "relink must not download anything")
        m = self.by_id()
        self.assertEqual(m[("1001", "800001")]["status"], "ok")
        self.assertEqual(m[("1003", "800003")]["status"], "ok")
        self.assertEqual(m[("1006", "800002")]["status"], "ok")
        self.assertEqual(m[("1002", "800001")]["status"], "failed")
        self.assertTrue((files / "1001aaa.png").exists(), "relink copies, it does not move")

    def test_relink_by_sha_from_previous_manifest(self):
        self.export()
        other = self.tmp / "output2"
        with redirect_stdout(io.StringIO()):
            oa.main(["relink", "--exports", str(self.exports), "--files", str(self.out), "--out", str(other)])
        entries = json.loads((other / "manifest.json").read_text(encoding="utf-8"))["entries"]
        st = {(e["attachmentId"], e["taskId"]): e["status"] for e in entries}
        self.assertEqual(st[("1002", "800001")], "ok")
        self.assertEqual(st[("9999", "800001")], "ok")



class TestBookmark(unittest.TestCase):
    def test_no_line_comment_inside_the_code(self):
        """docs/ora_backup.js also runs as a javascript: bookmark, where a // comment could swallow the rest."""
        js = Path(__file__).resolve().parent.parent / "docs" / "ora_backup.js"
        for n, line in enumerate(js.read_text(encoding="utf-8").splitlines(), 1):
            if not line.lstrip().startswith("//"):
                self.assertNotRegex(line, r"(?<![:'\"])//", "line %d" % n)


if __name__ == "__main__":
    unittest.main()
