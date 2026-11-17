# MyBridge — bridge pribadi (OpenAI-compatible) untuk website kamu

Server API kecil (Python stdlib saja, tanpa pip) yang didukung worker agent
yang selalu hangat. Website kamu memanggil API ini seperti memanggil API
OpenAI biasa.

## Menjalankan (lokal, di VM ini)

```bash
cd ~/workspace/my-bridge
python3 server.py
# -> my-bridge jalan di http://127.0.0.1:8765
```

Worker (yang menjawab) jalan sebagai cron `mybridge-worker` (tiap 5 menit,
loop 25 menit, tumpang tindih supaya selalu ada worker hangat). Tanpa worker,
request akan timeout 120 detik (504).

## Kunci API

Ada di `config.json` (jangan dibagikan sembarangan):
- `client_keys[0]` — dipakai website kamu (header `Authorization: Bearer ...`).
- `worker_key` — dipakai worker untuk endpoint `/v1/worker/*`.

## Endpoint

- `GET /health` — cek server hidup (tanpa auth).
- `GET /v1/models` — daftar model: `muse`, `muse-image`, `muse-video`.
- `POST /v1/chat/completions` — format OpenAI (`model`, `messages`,
  `max_tokens`, `stream`). Balasan format `chat.completion` standar.
- `POST /v1/worker/heartbeat`, `GET /v1/worker/pending`,
  `POST /v1/worker/claim`, `POST /v1/worker/done` — untuk worker jarak jauh.

## Contoh pakai (dari website / curl)

```bash
KEY=$(python3 -c "import json; print(json.load(open('config.json'))['client_keys'][0])")
curl -s http://127.0.0.1:8765/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $KEY" \
  -d '{"model":"muse","messages":[{"role":"user","content":"Halo, jawab singkat: tes"}],"max_tokens":50}'
```

JavaScript (fetch):

```js
const res = await fetch("http://127.0.0.1:8765/v1/chat/completions", {
  method: "POST",
  headers: { "Content-Type": "application/json", "Authorization": "Bearer MB_SK_KAMU" },
  body: JSON.stringify({ model: "muse", messages: [{ role: "user", content: "Halo" }] }),
});
const data = await res.json();
console.log(data.choices[0].message.content);
```

Python (library `openai`):

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8765/v1", api_key="MB_SK_KAMU")
r = client.chat.completions.create(model="muse", messages=[{"role": "user", "content": "Halo"}])
print(r.choices[0].message.content)
```

## Latensi (hasil ukur 2026-11-17)

- Deteksi request: rata-rata 0,30 detik.
- Jawaban lengkap: median ~5,8 detik, rata-rata ~6,8 detik, sebaran 3,9–11,1 detik.
- Di bawah 5 detik: 6 dari 13 sampel. Bukan jaminan tiap request — bottleneck
  murni jeda antar-giliran agent, bukan panjang jawaban.
- `stream: true` didukung sebagai SSE satu chunk (jawaban utuh dikirim
  sekaligus lalu `[DONE]`), bukan streaming token beneran.

## Pakai dari laptop kamu (server di laptop + daemon)

Server di VM ini tidak bisa dijangkau dari laptop (tidak ada inbound).
Solusinya: servernya jalan di LAPTOP kamu, worker di VM ini nyambung
lewat Tailscale (yang sudah jalan untuk Studio).

1. Di laptop (Windows), pastikan Python 3 terinstal (`python --version`).
   Kalau belum: instal dari python.org (centang "Add python to PATH").
2. Salin folder ini ke laptop (isinya: server.py, daemon.py, config.json,
   README.md). Atau salin server.py + config.json saja.
3. Di laptop, jalankan: `python server.py`
   (Windows Firewall akan bertanya — klik Allow untuk Private network.)
4. Di VM ini, jalankan daemon yang menjembatani server laptop dengan
   worker lokal:
   `BRIDGE_URL=http://100.108.26.110:8765 python3 daemon.py`
   (100.108.26.110 = alamat Tailscale laptop kamu; cek dengan `tailscale ip`.)
5. Dari laptop, tes API-nya di `http://localhost:8765` (contoh curl di atas,
   key dari config.json). Jawaban ~5-7 detik, sama seperti tes lokal.
   Teruji 2026-11-18: jalur daemon "DAEMON-OK" dalam 6,0 detik.

Catatan: laptop harus hidup + Tailscale jalan selama API dipakai.
Daemon di VM ini perlu hidup terus (nanti dipasang watchdog cron).

## Go public (supaya website publik bisa memakainya)

Server di VM ini hanya bisa dijangkau dari dalam VM (`127.0.0.1`). Supaya
website publik bisa memanggilnya:

1. Salin SATU folder ini (`server.py` + `config.json`) ke host publik
   (VPS / Railway / Render / apa pun yang bisa jalanin Python 3).
2. Di host itu ubah `config.json`: `"host": "0.0.0.0"`, samakan `client_keys`
   dan `worker_key` dengan yang di sini.
3. Jalankan `python3 server.py` di host itu (pakai systemd / process manager
   supaya hidup terus).
4. Worker di VM ini tetap jalan, tapi menunjuk ke server publik itu
   (endpoint `/v1/worker/*` sudah disiapkan untuk itu).
5. Website kamu ganti base URL ke alamat publik server itu.
   Daemon juga bisa dipakai untuk server publik ini:
   `BRIDGE_URL=https://alamat-publik-kamu python3 daemon.py`
   (daemon otomatis pakai proxy egress VM untuk host publik).

Selama server masih lokal, yang bisa memakainya hanya yang jalan di VM ini.
