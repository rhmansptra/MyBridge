# Deploy MyBridge ke Render (gratis)

Server MyBridge hanya butuh Python stdlib (tanpa dependency), jadi pas
dengan free tier Render: 750 jam/bulan (cukup untuk 1 service jalan terus),
512 MB RAM, tanpa kartu untuk daftar. Catatan jujur dari riset 2026-11-18:
beberapa sumber bilang Render minta kartu saat deploy web service (hold $1
lalu dilepas, sebagai anti-penyalahgunaan), sumber lain bilang tidak.
Kalau kamu tidak punya kartu / tidak mau, pakai Koyeb saja (disebut di
perbandingan yang sama: tanpa kartu, tanpa tidur) — tanya aku kalau mau
jalur itu.

## Kenapa Render free tidak akan tidur di kasus ini

Free tier Render tidur setelah 15 menit tanpa traffic, dan bangunnya
~30-60 detik. Tapi daemon (`daemon.py`) nge-poll server tiap 2 detik,
jadi server selalu menerima traffic dan tidak pernah tidur. Bandwidth
free 5 GB/bulan; poll daemon ~0,4 GB/bulan, aman.

## Langkah-langkah

1. **Repo GitHub.** Buat repo baru (mis. `mybridge`), upload file-file ini:
   `server.py`, `daemon.py`, `render.yaml`, `requirements.txt`,
   `README.md`, `DEPLOY_RENDER.md`.
   JANGAN upload `config.json` (sudah ada di `.gitignore` — file itu berisi
   kunci rahasia, jangan pernah masuk repo).
2. **Akun Render.** Daftar di render.com (gratis, bisa login pakai GitHub).
3. **Web Service baru.** Dashboard Render → New → Web Service → pilih
   repo `mybridge` → Render membaca `render.yaml` otomatis → Create
   Web Service.
4. **Env vars.** Di dashboard service → Environment, isi:
   - `CLIENT_KEYS` = isi `client_keys[0]` dari `config.json` di VM ini
     (kunci untuk website/klien kamu).
   - `WORKER_KEY` = isi `worker_key` dari `config.json` di VM ini
     (kunci untuk daemon/worker).
   Jangan isi `PORT` (Render yang mengisinya sendiri) dan jangan isi
   `HOST` (sudah `0.0.0.0` di render.yaml).
5. **Deploy.** Tunggu deploy selesai → dapat URL, mis.
   `https://mybridge-xxxx.onrender.com`.
6. **Daemon di VM ini.** Jalankan daemon yang menjembatani server Render
   dengan worker lokal:
   `BRIDGE_URL=https://mybridge-xxxx.onrender.com POLL_SECS=2 python3 daemon.py`
   (daemon otomatis pakai proxy egress VM untuk host publik).
7. **Tes dari laptop.** Contoh curl (key sama dengan `CLIENT_KEYS`):
   ```bash
   curl -s https://mybridge-xxxx.onrender.com/v1/chat/completions \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer MB_SK_KAMU" \
     -d '{"model":"muse","messages":[{"role":"user","content":"Halo"}]}'
   ```
   Jawaban ~6-8 detik (poll daemon 2 detik + worker ~5 detik).

## Dashboard

Setelah deploy, dashboard ada di `https://mybridge-xxxx.onrender.com/dashboard`:
status server/worker/daemon, total request, rata-rata latensi, riwayat
50 request terakhir, konsol tes chat, dan info API. Dashboard minta
client key sekali (disimpan di browser saja) untuk membaca statistik —
riwayat request bersifat pribadi, jangan bagikan key-nya. Statistik
in-memory: hilang kalau server Render restart; riwayat panjang tetap
ada di file `answers/` di VM lokal.

Server ini satu file Python portabel. Yang perlu diubah hanya
`BRIDGE_URL` daemon dan base URL di website kamu. Kunci tetap sama
(selama `CLIENT_KEYS`/`WORKER_KEY` di host baru disamakan).
