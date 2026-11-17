#!/usr/bin/env bash
# Watcher MyBridge: mengawasi ~/workspace/my-bridge/requests/*.json.
# Setiap request baru dicetak SATU baris:
#   REQUEST <id> <arrived_at_epoch> <watcher_epoch> <teks pertanyaan>
# Tidur 0.5 detik antar putaran -> deteksi < 1 detik.
set -u
DIR="$HOME/workspace/my-bridge/requests"
declare -A seen
while true; do
  for f in "$DIR"/*.json; do
    [ -e "$f" ] || continue
    id=$(basename "$f" .json)
    if [ -z "${seen[$id]:-}" ]; then
      seen[$id]=1
      line=$(python3 -c "import json; d=json.load(open('$f')); print(str(d.get('arrived_at', d.get('received_at',''))) + '|' + d.get('text',''))")
      arrived="${line%%|*}"
      q="${line#*|}"
      echo "REQUEST $id $arrived $(date +%s.%N) $q"
    fi
  done
  sleep 0.5
done
