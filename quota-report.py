#!/usr/bin/env python3
"""Reporter kuota MyBridge: baca `subscription-status status` di VM ini,
lalu kirim snapshot-nya ke server Render (POST /v1/worker/quota) supaya
dashboard bisa menampilkan kuota Muse saat ini.

Contoh output CLI yang diparse:
  The user does not have a subscription.
  Usage: 51% of free weekly limit.
  Weekly limit resets Nov 23 at 10:59 PM +07
  Additional tokens: 50% used (502M tokens left)
Jalankan tiap 15 menit via cron mybridge-quota-reporter.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
BRIDGE_URL = os.environ.get(
    "BRIDGE_URL", "https://mybridge-67fc.onrender.com")
MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def worker_key():
    key = os.environ.get("WORKER_KEY", "")
    if key:
        return key
    try:
        with open(os.path.join(BASE, "config.json")) as f:
            return json.load(f).get("worker_key", "")
    except (OSError, ValueError):
        return ""


def parse_status(text):
    q = {"plan": "Free", "used_pct": None, "reset_at": None,
         "additional_used_pct": None, "additional_tokens_left": None}
    m = re.search(r"Usage:\s*(\d+)%\s*of\s*free weekly limit", text)
    if m:
        q["used_pct"] = int(m.group(1))
    m = re.search(r"Additional tokens:\s*(\d+)%\s*used\s*\(([^)]+?)\s*tokens left\)",
                  text)
    if m:
        q["additional_used_pct"] = int(m.group(1))
        q["additional_tokens_left"] = m.group(2).strip()
    m = re.search(
        r"Weekly limit resets\s+([A-Z][a-z]{2})\s+(\d+)\s+at\s+"
        r"(\d+):(\d+)\s*(AM|PM)\s*\+07", text)
    if m:
        mon, day, hh, mm, ampm = (MONTHS[m.group(1)], int(m.group(2)),
                                  int(m.group(3)), int(m.group(4)), m.group(5))
        if ampm == "PM" and hh != 12:
            hh += 12
        if ampm == "AM" and hh == 12:
            hh = 0
        now = datetime.now(timezone(timedelta(hours=7)))
        year = now.year
        reset = datetime(year, mon, day, hh, mm,
                         tzinfo=timezone(timedelta(hours=7)))
        if reset < now - timedelta(hours=1):
            reset = reset.replace(year=year + 1)
        q["reset_at"] = reset.isoformat()
    if "does not have a subscription" in text:
        q["plan"] = "Free"
    else:
        m = re.search(r"plan[:\s]+([A-Za-z0-9 ]+)", text, re.I)
        if m:
            q["plan"] = m.group(1).strip()[:40]
    return q


def main():
    try:
        out = subprocess.run(
            ["/opt/hatch/bin/subscription-status", "status"],
            capture_output=True, text=True, timeout=60)
        text = (out.stdout or "") + (out.stderr or "")
    except Exception as e:
        print("quota-report: gagal menjalankan subscription-status:", e,
              file=sys.stderr)
        return 1
    q = parse_status(text)
    if q["used_pct"] is None:
        print("quota-report: tidak bisa parse output:\n", text, file=sys.stderr)
        return 1
    q["reported_at"] = time.time()
    key = worker_key()
    if not key:
        print("quota-report: worker key tidak ditemukan", file=sys.stderr)
        return 1
    req = urllib.request.Request(
        BRIDGE_URL.rstrip("/") + "/v1/worker/quota",
        data=json.dumps(q).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("quota-report: OK", r.status, json.dumps(q))
            return 0
    except Exception as e:
        print("quota-report: POST gagal:", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
