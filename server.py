#!/usr/bin/env python3
"""MyBridge — OpenAI-compatible API backed by a warm Muse worker.

Stdlib only (no pip dependencies). Run:  python3 server.py
Reads config.json next to this file (host, port, keys, timeout).

Arsitektur:
- Klien (website kamu) -> POST /v1/chat/completions (format OpenAI).
- Server menulis request ke requests/<id>.json, lalu menunggu answers/<id>.json.
- Worker hangat (agent) mengawasi requests/ lewat watcher, menjawab, lalu
  menulis answers/<id>.json. Server mengembalikan jawaban dalam format
  OpenAI (chat.completion) ke klien.
- Endpoint /v1/worker/* ada untuk topologi produksi (server publik terpisah
  dari worker): worker jarak jauh bisa heartbeat/pending/claim/done via HTTP.
  Worker lokal memakai file langsung (jalur tercepat, terukur 3-7 detik).

Endpoint:
  GET  /health                      -> {"ok": true}
  GET  /                            -> info singkat
  GET  /v1/models                   -> daftar model (butuh client key)
  POST /v1/chat/completions        -> OpenAI chat (butuh client key)
  POST /v1/worker/heartbeat        -> {"ok": true} (butuh worker key)
  GET  /v1/worker/pending          -> daftar request belum diklaim
  POST /v1/worker/claim   {"id"}   -> klaim satu request (rename .claimed)
  POST /v1/worker/done    {"id","content"} -> tulis jawaban
  POST /v1/worker/quota   {"plan","used_pct","reset_at",...} -> simpan snapshot kuota
  GET  /v1/stats                    -> statistik + riwayat + kuota (butuh client key)
  GET  /dashboard                  -> halaman dashboard (HTML)
"""
import json
import os
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
REQ_DIR = os.path.join(BASE, "requests")
ANS_DIR = os.path.join(BASE, "answers")
os.makedirs(REQ_DIR, exist_ok=True)
os.makedirs(ANS_DIR, exist_ok=True)

def _load_config():
    cfg = {}
    try:
        with open(os.path.join(BASE, "config.json")) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    # Env var menang atas config.json (penting untuk hosting publik: kunci
    # jangan pernah masuk ke repo, isi lewat env var di dashboard host).
    # Render mengisi PORT sendiri; CLIENT_KEYS dipisah koma.
    return {
        "host": os.environ.get(
            "HOST", os.environ.get("BRIDGE_HOST", cfg.get("host", "127.0.0.1"))),
        "port": int(os.environ.get(
            "PORT", os.environ.get("BRIDGE_PORT", cfg.get("port", 8765)))),
        "client_keys": [k for k in os.environ.get("CLIENT_KEYS", "").split(",")
                        if k] or cfg.get("client_keys", []),
        "worker_key": os.environ.get("WORKER_KEY", cfg.get("worker_key", "")),
        "answer_timeout_secs": int(os.environ.get(
            "ANSWER_TIMEOUT_SECS", cfg.get("answer_timeout_secs", 120))),
    }


CONFIG = _load_config()
HOST = CONFIG["host"]
PORT = CONFIG["port"]
CLIENT_KEYS = set(CONFIG["client_keys"])
WORKER_KEY = CONFIG["worker_key"]
ANSWER_TIMEOUT = CONFIG["answer_timeout_secs"]

# Statistik dashboard (in-memory; hilang kalau server restart — riwayat
# panjang tetap ada di file answers/ di VM lokal).
STARTED_AT = time.time()
_STATS_LOCK = threading.Lock()
_RECENT = deque(maxlen=50)  # {id, received_at, question, answer, latency, model, pt, ct, status}
_TOTALS = {"total": 0, "ok": 0, "timeout": 0}
_WORKER = {"last_heartbeat": 0.0, "last_pending": 0.0,
           "last_claim": 0.0, "last_done": 0.0}
# Snapshot kuota Muse (dikirim reporter dari VM tiap 15 menit; in-memory).
_QUOTA = {"data": None, "updated_at": 0.0}


def _record(rid, req, answer, latency, pt, ct, status):
    with _STATS_LOCK:
        _TOTALS["total"] += 1
        if status == "ok":
            _TOTALS["ok"] += 1
        elif status == "timeout":
            _TOTALS["timeout"] += 1
        _RECENT.append({
            "id": rid,
            "received_at": req.get("received_at", time.time()),
            "question": (req.get("text", "") or "")[:120],
            "answer": (answer or "")[:200],
            "latency": round(latency, 2) if latency is not None else None,
            "model": req.get("model", "muse"),
            "prompt_tokens": pt, "completion_tokens": ct,
            "status": status,
        })


def auth_ok(handler, keys):
    h = handler.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        return False
    return h[7:].strip() in keys


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # stdout tetap bersih

    def _send(self, code, body_bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode())

    def _err(self, code, msg):
        self._json(code, {"error": {"message": msg, "type": "api_error", "code": code}})

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/health":
            return self._json(200, {"ok": True, "service": "my-bridge"})
        if p == "/":
            return self._json(200, {
                "service": "my-bridge",
                "docs": "lihat web dokumentasi",
                "endpoints": ["GET /health", "GET /v1/models",
                              "POST /v1/chat/completions",
                              "POST /v1/worker/heartbeat",
                              "GET /v1/worker/pending",
                              "POST /v1/worker/claim",
                              "POST /v1/worker/done"],
            })
        if p == "/v1/models":
            if not auth_ok(self, CLIENT_KEYS | {WORKER_KEY}):
                return self._err(401, "invalid api key")
            return self._json(200, {"object": "list", "data": [
                {"id": "muse", "object": "model", "created": 0, "owned_by": "muse"},
                {"id": "muse-image", "object": "model", "created": 0, "owned_by": "muse"},
                {"id": "muse-video", "object": "model", "created": 0, "owned_by": "muse"},
            ]})
        if p == "/v1/worker/pending":
            if not auth_ok(self, {WORKER_KEY}):
                return self._err(401, "invalid worker key")
            _WORKER["last_pending"] = time.time()
            items = []
            for fn in sorted(os.listdir(REQ_DIR)):
                if not fn.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(REQ_DIR, fn)) as f:
                        d = json.load(f)
                    items.append({"id": d["id"], "received_at": d["received_at"]})
                except Exception:
                    pass
            return self._json(200, {"pending": items, "worker_online": True})
        if p == "/v1/stats":
            if not auth_ok(self, CLIENT_KEYS | {WORKER_KEY}):
                return self._err(401, "invalid api key")
            with _STATS_LOCK:
                recent = list(_RECENT)
                total = _TOTALS["total"]
                ok = _TOTALS["ok"]
                timeout = _TOTALS["timeout"]
                quota = dict(_QUOTA["data"]) if _QUOTA["data"] else None
                quota_updated_at = _QUOTA["updated_at"]
            lats = sorted(r["latency"] for r in recent
                          if r["status"] == "ok" and r["latency"] is not None)
            now = time.time()

            def _ago(ts):
                return round(now - ts, 1) if ts else None

            def _pct(sorted_vals, p):
                if not sorted_vals:
                    return None
                i = min(len(sorted_vals) - 1, max(0, int(p * len(sorted_vals))))
                return round(sorted_vals[i], 2)

            rpm = sum(1 for r in recent if now - r["received_at"] <= 60)
            return self._json(200, {
                "uptime_secs": round(now - STARTED_AT, 1),
                "total": total, "ok": ok, "timeout": timeout,
                "success_rate_pct": round(ok / total * 100, 1) if total else None,
                "avg_latency_secs": round(sum(lats) / len(lats), 2) if lats else None,
                "median_latency_secs": _pct(lats, 0.5),
                "p95_latency_secs": _pct(lats, 0.95),
                "rpm_last_60s": rpm,
                "worker": {
                    "last_heartbeat_ago": _ago(_WORKER["last_heartbeat"]),
                    "last_pending_ago": _ago(_WORKER["last_pending"]),
                    "last_claim_ago": _ago(_WORKER["last_claim"]),
                    "last_done_ago": _ago(_WORKER["last_done"]),
                },
                "quota": quota,
                "quota_updated_ago": _ago(quota_updated_at) if quota else None,
                "recent": list(reversed(recent)),
            })
        if p == "/dashboard":
            try:
                with open(os.path.join(BASE, "dashboard.html"), "rb") as f:
                    return self._send(200, f.read(), ctype="text/html; charset=utf-8")
            except OSError:
                return self._err(404, "dashboard not found")
        return self._err(404, "not found")

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            n = 0
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode() or "{}")
        except Exception:
            return self._err(400, "invalid json")

        if p == "/v1/chat/completions":
            if not auth_ok(self, CLIENT_KEYS):
                return self._err(401, "invalid api key")
            msgs = body.get("messages") or []
            model = body.get("model", "muse")
            max_tokens = body.get("max_tokens")
            text = ""
            for m in reversed(msgs):
                if m.get("role") == "user":
                    text = m.get("content", "")
                    break
            if isinstance(text, list):  # content parts
                text = " ".join(
                    part.get("text", "") for part in text if isinstance(part, dict))
            if not text:
                return self._err(400, "no user message")
            rid = "req-" + uuid.uuid4().hex[:16]
            req = {"id": rid, "text": text, "model": model,
                   "max_tokens": max_tokens, "received_at": time.time()}
            with open(os.path.join(REQ_DIR, rid + ".json"), "w") as f:
                json.dump(req, f)
            # tunggu jawaban dari worker
            deadline = time.time() + ANSWER_TIMEOUT
            ans = None
            while time.time() < deadline:
                ap = os.path.join(ANS_DIR, rid + ".json")
                if os.path.exists(ap):
                    try:
                        with open(ap) as f:
                            ans = json.load(f)
                        break
                    except Exception:
                        pass
                time.sleep(0.5)
            if ans is None:
                try:
                    os.remove(os.path.join(REQ_DIR, rid + ".json"))
                except OSError:
                    pass
                _record(rid, req, "", None, len(text.split()), 0, "timeout")
                return self._err(504, "worker timeout: no answer in %ss" % ANSWER_TIMEOUT)
            # bersihkan antrean supaya watcher run berikutnya tidak menjawab
            # ulang request basi
            try:
                os.remove(os.path.join(REQ_DIR, rid + ".json"))
            except OSError:
                pass
            content = ans.get("answer", "")
            cid = "chatcmpl-" + uuid.uuid4().hex[:16]
            created = int(time.time())
            pt = len(text.split())
            ct = len(content.split())
            _record(rid, req, content, time.time() - req["received_at"],
                    pt, ct, "ok")
            if body.get("stream"):
                # Streaming "satu chunk": agent menulis jawaban utuh dulu,
                # jadi SSE di sini mengirim seluruh isi sekaligus lalu DONE.
                # Klien SSE tetap bisa memakainya seperti biasa.
                chunk = {"id": cid, "object": "chat.completion.chunk",
                         "created": created, "model": model,
                         "choices": [{"index": 0,
                                      "delta": {"role": "assistant", "content": content},
                                      "finish_reason": "stop"}]}
                payload = ("data: %s\n\n" % json.dumps(chunk)).encode()
                payload += b"data: [DONE]\n\n"
                return self._send(200, payload, ctype="text/event-stream")
            return self._json(200, {
                "id": cid, "object": "chat.completion", "created": created,
                "model": model,
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": pt, "completion_tokens": ct,
                          "total_tokens": pt + ct},
            })

        if p == "/v1/worker/heartbeat":
            if not auth_ok(self, {WORKER_KEY}):
                return self._err(401, "invalid worker key")
            _WORKER["last_heartbeat"] = time.time()
            return self._json(200, {"ok": True, "ts": time.time()})

        if p == "/v1/worker/claim":
            if not auth_ok(self, {WORKER_KEY}):
                return self._err(401, "invalid worker key")
            _WORKER["last_claim"] = time.time()
            rid = body.get("id")
            rp = os.path.join(REQ_DIR, rid + ".json") if rid else ""
            if not rid or not os.path.exists(rp):
                return self._err(404, "request not found or already claimed")
            with open(rp) as f:
                req = json.load(f)
            os.rename(rp, os.path.join(REQ_DIR, rid + ".claimed"))
            return self._json(200, {"request": req})

        if p == "/v1/worker/done":
            if not auth_ok(self, {WORKER_KEY}):
                return self._err(401, "invalid worker key")
            rid = body.get("id")
            content = body.get("content", "")
            if isinstance(content, dict):
                content = content.get("text", "") or json.dumps(content)
            if not rid:
                return self._err(400, "missing id")
            _WORKER["last_done"] = time.time()
            with open(os.path.join(ANS_DIR, rid + ".json"), "w") as f:
                json.dump({"id": rid, "answer": content,
                           "answered_at": time.time()}, f)
            cp = os.path.join(REQ_DIR, rid + ".claimed")
            if os.path.exists(cp):
                os.remove(cp)
            return self._json(200, {"ok": True})

        if p == "/v1/worker/quota":
            if not auth_ok(self, {WORKER_KEY}):
                return self._err(401, "invalid worker key")
            q = {
                "plan": str(body.get("plan", ""))[:40],
                "used_pct": body.get("used_pct"),
                "reset_at": str(body.get("reset_at", ""))[:40],
                "additional_used_pct": body.get("additional_used_pct"),
                "additional_tokens_left": str(body.get("additional_tokens_left", ""))[:40],
                "reported_at": body.get("reported_at"),
            }
            with _STATS_LOCK:
                _QUOTA["data"] = q
                _QUOTA["updated_at"] = time.time()
            return self._json(200, {"ok": True})

        return self._err(404, "not found")


if __name__ == "__main__":
    if not CLIENT_KEYS:
        print("PERINGATAN: tidak ada client key (isi CLIENT_KEYS di env/config.json), "
              "semua request akan 401", flush=True)
    if not WORKER_KEY:
        print("PERINGATAN: tidak ada worker key (isi WORKER_KEY di env/config.json)",
              flush=True)
    print("my-bridge jalan di http://%s:%d (Ctrl+C untuk berhenti)" % (HOST, PORT), flush=True)
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
