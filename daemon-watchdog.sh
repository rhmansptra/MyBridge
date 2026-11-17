#!/bin/bash
# Watchdog daemon MyBridge: pastikan N daemon.py tetap jalan.
# Dijalankan cron tiap 5 menit. Kalau daemon kurang dari N (mis. VM diganti),
# start ulang yang kurang dengan BRIDGE_URL Render + POLL_SECS=2.
# Juga mengembalikan klaim basi (.claimed > 180 detik) ke .json supaya
# worker lain bisa mengambil alih request yang worker-nya mati di tengah jalan.
BRIDGE_URL="https://mybridge-67fc.onrender.com"
DIR="/home/hatch/workspace/my-bridge"
N=5
RUNNING=$(pgrep -f "^python3 daemon.py$" | wc -l)
if [ "$RUNNING" -lt "$N" ]; then
  NEED=$((N - RUNNING))
  cd "$DIR" || exit 1
  for i in $(seq 1 "$NEED"); do
    BRIDGE_URL="$BRIDGE_URL" POLL_SECS=2 nohup python3 daemon.py >> daemon.log 2>&1 &
  done
  echo "$(date -Iseconds) watchdog: daemon $RUNNING -> $N (start $NEED)" >> daemon.log
fi
# Reaper: klaim lokal yang basi (>180 detik) kembali jadi .json.
find "$DIR/requests" -maxdepth 1 -name "*.claimed" -mmin +3 -exec sh -c 'mv "$1" "${1%.claimed}.json"' _ {} \; 2>/dev/null
