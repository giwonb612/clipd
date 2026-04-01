from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import time
import webbrowser
import threading
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from clipd.db import Database
from clipd.utils import fmt_time, fmt_size, _to_png
from clipd.clipboard import write_clipboard_text, write_clipboard_image

DB_PATH = Path.home() / ".clipd" / "history.db"
HTML_PATH = Path(__file__).parent / "web_frontend.html"

_local = threading.local()

def _get_db() -> "Database":
    """Return a per-thread Database instance."""
    if not getattr(_local, "db", None):
        _local.db = Database()
    return _local.db


def clip_to_dict(row) -> dict:
    d = dict(row)
    d.pop("content", None)
    d["pinned"] = bool(d.get("pinned"))
    d["created_at_fmt"] = fmt_time(d["created_at"])
    if d["type"] == "text":
        tc = d.get("text_content") or ""
        d["preview"] = tc[:200]
        d["size"] = len(tc.encode())
    else:
        d["preview"] = None
        d["size"] = 0
    return d


class ClipHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status, msg):
        self.send_json({"error": msg}, status)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        db = _get_db()

        if path == "/":
            html = HTML_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        elif path == "/api/clips":
            limit = int(qs.get("limit", [50])[0])
            offset = int(qs.get("offset", [0])[0])
            clip_type = qs.get("type", [None])[0]
            tag = qs.get("tag", [None])[0]
            pinned = qs.get("pinned", [None])[0] == "1"
            rows = db.list(limit=limit, clip_type=clip_type, tag=tag, pinned_only=pinned, offset=offset)
            self.send_json([clip_to_dict(r) for r in rows])

        elif path == "/api/clips/search":
            q = qs.get("q", [""])[0].strip()
            limit = int(qs.get("limit", [50])[0])
            offset = int(qs.get("offset", [0])[0])
            if not q:
                self.send_json([])
                return
            rows = db.search(q, limit=limit, offset=offset)
            self.send_json([clip_to_dict(r) for r in rows])

        elif m := re.match(r"^/api/clips/(\d+)/image$", path):
            id_ = int(m.group(1))
            content = db.get_content(id_)
            if content is None:
                self.send_error_json(404, "not found")
                return
            png = _to_png(content)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(png)

        elif m := re.match(r"^/api/clips/(\d+)$", path):
            id_ = int(m.group(1))
            row = db.get(id_)
            if row is None:
                self.send_error_json(404, "not found")
                return
            d = clip_to_dict(row)
            if row["type"] == "text":
                d["full_text"] = row["text_content"] or ""
            self.send_json(d)

        elif path == "/api/stats":
            self.send_json(db.stats())

        elif path == "/api/export":
            fmt = qs.get("format", ["json"])[0]
            rows = db.list(limit=100000)
            if fmt == "csv":
                import csv
                import io
                out = io.StringIO()
                w = csv.writer(out)
                w.writerow(["id", "type", "text_content", "ocr_text", "pinned", "tags", "created_at"])
                for r in rows:
                    w.writerow([r["id"], r["type"], r["text_content"], r["ocr_text"], r["pinned"], r["tags"], r["created_at"]])
                body = out.getvalue().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/csv")
                self.send_header("Content-Disposition", "attachment; filename=clips.csv")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                data = [clip_to_dict(r) for r in rows]
                body = json.dumps(data, ensure_ascii=False, indent=2).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Disposition", "attachment; filename=clips.json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        elif path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            last_ts = time.time()
            while True:
                try:
                    rows = db.latest_after(last_ts)
                    for row in rows:
                        last_ts = max(last_ts, row["created_at"])
                        data = json.dumps(clip_to_dict(row), ensure_ascii=False)
                        self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(1.0)
                except (BrokenPipeError, ConnectionResetError):
                    break

        else:
            self.send_error_json(404, "not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        db = _get_db()

        if m := re.match(r"^/api/clips/(\d+)/open$", path):
            import subprocess, tempfile, os
            id_ = int(m.group(1))
            row = db.get(id_)
            if row is None:
                self.send_error_json(404, "not found")
                return
            if row["type"] == "text":
                text = row["text_content"] or ""
                with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8") as f:
                    f.write(text)
                    tmp = f.name
                subprocess.Popen(["open", "-a", "Visual Studio Code", tmp])
            else:
                content = db.get_content(id_)
                if not content:
                    self.send_error_json(404, "no content")
                    return
                png = _to_png(bytes(content))
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                    f.write(png)
                    tmp = f.name
                subprocess.Popen(["open", "-a", "Preview", tmp])
            self.send_json({"ok": True})

        elif m := re.match(r"^/api/clips/(\d+)/save$", path):
            id_ = int(m.group(1))
            content = db.get_content(id_)
            if content is None:
                self.send_error_json(404, "not found")
                return
            body = self.read_json_body()
            save_dir = Path(body.get("dir", str(Path.home() / "Downloads"))).expanduser()
            if not save_dir.is_dir():
                self.send_error_json(400, f"directory not found: {save_dir}")
                return
            png = _to_png(content)
            max_seq = 0
            for f in save_dir.iterdir():
                hit = re.match(r"^clipd-(\d+)\.png$", f.name)
                if hit:
                    max_seq = max(max_seq, int(hit.group(1)))
            filename = f"clipd-{max_seq + 1:03d}.png"
            dest = save_dir / filename
            dest.write_bytes(png)
            self.send_json({"ok": True, "path": str(dest)})

        elif m := re.match(r"^/api/clips/(\d+)/copy$", path):
            id_ = int(m.group(1))
            row = db.get(id_)
            if row is None:
                self.send_error_json(404, "not found")
                return
            if row["type"] == "text":
                write_clipboard_text(row["text_content"] or "")
            else:
                content = db.get_content(id_)
                if content:
                    write_clipboard_image(content)
            self.send_json({"ok": True})

        elif m := re.match(r"^/api/clips/(\d+)/pin$", path):
            id_ = int(m.group(1))
            db.pin(id_, True)
            self.send_json({"ok": True})

        elif m := re.match(r"^/api/clips/(\d+)/unpin$", path):
            id_ = int(m.group(1))
            db.pin(id_, False)
            self.send_json({"ok": True})

        elif m := re.match(r"^/api/clips/(\d+)/tag$", path):
            id_ = int(m.group(1))
            body = self.read_json_body()
            tag = body.get("tag", "").strip()
            if not tag:
                self.send_error_json(400, "tag required")
                return
            db.tag(id_, tag)
            self.send_json({"ok": True})

        elif m := re.match(r"^/api/clips/(\d+)/untag$", path):
            id_ = int(m.group(1))
            body = self.read_json_body()
            tag = body.get("tag", "").strip()
            if not tag:
                self.send_error_json(400, "tag required")
                return
            db.untag(id_, tag)
            self.send_json({"ok": True})

        elif path == "/api/clear":
            body = self.read_json_body()
            days = body.get("days")
            before_ts = None
            if days:
                before_ts = time.time() - int(days) * 86400
            count = db.clear(before_ts=before_ts)
            self.send_json({"deleted": count})

        else:
            self.send_error_json(404, "not found")

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path
        db = _get_db()

        if m := re.match(r"^/api/clips/(\d+)/text$", path):
            id_ = int(m.group(1))
            body = self.read_json_body()
            text = body.get("text")
            if text is None:
                self.send_error_json(400, "text required")
                return
            db.update_text(id_, text)
            self.send_json({"ok": True})
        else:
            self.send_error_json(404, "not found")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        db = _get_db()

        if m := re.match(r"^/api/clips/(\d+)$", path):
            id_ = int(m.group(1))
            ok = db.delete(id_)
            self.send_json({"ok": ok})
        else:
            self.send_error_json(404, "not found")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def _kill_existing(port: int) -> bool:
    """Kill any existing process listening on the given port. Returns True if killed."""
    import subprocess, signal, os
    try:
        out = subprocess.check_output(
            ["lsof", "-ti", f"tcp:{port}"], text=True
        ).strip()
    except subprocess.CalledProcessError:
        return False
    if not out:
        return False
    for pid_str in out.splitlines():
        pid = int(pid_str.strip())
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    return True


def run_server(port: int = 8432, open_browser: bool = True):
    server = ThreadingHTTPServer(("127.0.0.1", port), ClipHandler)
    url = f"http://localhost:{port}"
    print(f"clipd web  →  {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        print("\nServer stopped.")
