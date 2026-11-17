#!/bin/bash
# Watchdog daemon MyBridge: pastikan N daemon.py tetap jalan.
# Dijalankan cron tiap 5 menit. Kalau daemon kurang dari N (mis. VM diganti),
# start ulang yang kurang dengan BRIDGE_URL Render + POLL_SECS=2.
# Juga mengembalikan klaim basi (.claimed > 180 detik) ke .json supaya
# worker lain bisa mengambil alih request yang worker-nya mati di tengah jalan.
# (DIPERBAIKI 18 Nov: reaper lama membangkitkan SEMUA .claimed basi, termasuk
# yang sudah terjawab — karena daemon tidak pernah menghapus .claimed lokal,
# setiap request yang sudah selesai dibangkitkan lagi dan dijawab ulang
# terus-menerus, membuang kuota. Sekarang daemon.py sudah menghapus .claimed
# saat done, jadi yang tersisa di sini hanya klaim yang benar-benar macet.)
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
# Reaper: klaim lokal yang basi (>180 detik). Kalau jawabannya sudah ada di
# answers/, request itu sudah selesai — hapus .claimed-nya, jangan dibangkitkan
# (membangkitkan = dijawab ulang = kuota terbuang). Kalau belum ada jawaban,
# baru kembalikan jadi .json supaya worker lain bisa mengambil alih.
find "$DIR/requests" -maxdepth 1 -name "*.claimed" -mmin +3 -print0 2>/dev/null | while IFS= read -r -d '' c; do
  id=$(basename "$c" .claimed)
  if [ -f "$DIR/answers/$id.json" ]; then rm -f "$c"; else mv "$c" "${c%.claimed}.json"; fi
done
