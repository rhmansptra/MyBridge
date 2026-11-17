#!/usr/bin/env python3
"""MyBridge remote bridge daemon.

Menjembatani server MyBridge JARAK JAUH (di laptop kamu / host publik) dengan
antrean file lokal di VM ini, supaya worker agent yang hangat tetap bisa
menjawab cepat (tanpa polling HTTP yang lambat dari agent).

Cara kerja (loop):
  1. GET {BRIDGE_URL}/v1/worker/pending  -> daftar request belum diklaim.
  2. POST {BRIDGE_URL}/v1/worker/claim {"id"} -> ambil satu request.
  3. Tulis request itu ke antrean file lokal (requests/<id>.json) -> worker
     agent (cron mybridge-worker) menjawab -> answers/<id>.json.
  4. POST {BRIDGE_URL}/v1/worker/done {"id", "content"} -> kirim jawaban
     balik ke server jauh. Server jauh lalu membalas kliennya.

Konfigurasi (env):
  BRIDGE_URL  alamat server jauh, mis. http://100.108.26.110:8765
              (laptop kamu via Tailscale) atau https://xxx.fly.dev
  WORKER_KEY  kunci worker (default: dibaca dari config.json di folder ini)
  POLL_SECS   jeda antar poll pending (default 0.5)

Jalankan:  BRIDGE_URL=http://100.108.26.110:8765 python3 daemon.py
Log: stdout (arahkan ke daemon.log).
"""
import ipaddress
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
REQ_DIR = os.path.join(BASE, "requests")
ANS_DIR = os.path.join(BASE, "answers")

BRIDGE_URL = os.environ.get("BRIDGE_URL", "").rstrip("/")
if not BRIDGE_URL:
    raise SystemExit(
        "set BRIDGE_URL dulu, mis. BRIDGE_URL=http://100.108.26.110:8765 python3 daemon.py")
WORKER_KEY = os.environ.get("WORKER_KEY") or json.load(
    open(os.path.join(BASE, "config.json")))["worker_key"]
POLL = float(os.environ.get("POLL_SECS", "0.5"))

PRIVATE_NETS = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
    "127.0.0.0/8")]


def _is_private_host(host):
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or any(ip in net for net in PRIVATE_NETS)
    except ValueError:
        return False


def _opener():
    # Alamat privat/Tailscale: koneksi langsung (proxy egress tidak bisa
    # merutekan IP privat). Host publik: pakai proxy env (satu-satunya jalan
    # keluar VM ini).
    if _is_private_host(urlparse(BRIDGE_URL).hostname or ""):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


OPENER = _opener()


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BRIDGE_URL + path, method=method, data=data,
        headers={"Authorization": "Bearer " + WORKER_KEY,
                 "Content-Type": "application/json"})
    with OPENER.open(req, timeout=15) as r:
        return json.load(r)


def main():
    os.makedirs(REQ_DIR, exist_ok=True)
    os.makedirs(ANS_DIR, exist_ok=True)
    print("daemon jalan, bridge=%s poll=%.1fs" % (BRIDGE_URL, POLL), flush=True)
    while True:
        try:
            pending = call("GET", "/v1/worker/pending").get("pending", [])
        except Exception as e:
            print("pending gagal: %r (coba lagi)" % (e,), flush=True)
            time.sleep(2)
            continue
        for item in pending:
            rid = item["id"]
            try:
                req = call("POST", "/v1/worker/claim", {"id": rid})["request"]
            except urllib.error.HTTPError as e:
                print("claim %s: HTTP %s (mungkin sudah diambil)" % (rid, e.code),
                      flush=True)
                continue
            except Exception as e:
                print("claim %s gagal: %r" % (rid, e), flush=True)
                continue
            local = {"id": rid, "text": req.get("text", ""),
                     "model": req.get("model", "muse"),
                     "max_tokens": req.get("max_tokens"),
                     "received_at": time.time()}
            local_req = os.path.join(REQ_DIR, rid + ".json")
            with open(local_req, "w") as f:
                json.dump(local, f)
            print("claim %s -> antrean lokal" % rid, flush=True)
            deadline = time.time() + 120
            content = None
            while time.time() < deadline:
                ap = os.path.join(ANS_DIR, rid + ".json")
                if os.path.exists(ap):
                    try:
                        with open(ap) as f:
                            content = json.load(f).get("answer", "")
                        break
                    except Exception:
                        pass
                time.sleep(0.5)
            if content is None:
                print("timeout menunggu jawaban %s" % rid, flush=True)
                try:
                    os.remove(local_req)
                except OSError:
                    pass
                continue
            try:
                call("POST", "/v1/worker/done", {"id": rid, "content": content})
                print("done %s (jawaban terkirim)" % rid, flush=True)
            except Exception as e:
                print("done %s gagal: %r" % (rid, e), flush=True)
                continue
            # bersihkan antrean lokal supaya tidak dijawab ulang.
            # PENTING: worker mengklaim dengan rename .json -> .claimed, jadi
            # local_req (.json) sudah tidak ada saat ini — yang harus dihapus
            # adalah .claimed-nya. Kalau .claimed dibiarkan, reaper akan
            # membangkitkannya jadi .json dan request yang sudah terjawab
            # dijawab ulang terus-menerus (kuota terbuang).
            for p in (local_req, os.path.join(REQ_DIR, rid + ".claimed"),
                      os.path.join(ANS_DIR, rid + ".json"),
                      os.path.join(ANS_DIR, rid + ".txt")):
                try:
                    os.remove(p)
                except OSError:
                    pass
        time.sleep(POLL)


if __name__ == "__main__":
    main()
