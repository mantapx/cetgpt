import asyncio
import aiohttp
from aiohttp import ClientTimeout
from colorama import init, Fore
import os, json, time
from urllib.parse import urlparse, urlunparse

# =========================
# Konfigurasi untuk SKALA BESAR
# =========================
CPU = os.cpu_count() or 4
GLOBAL_CONCURRENCY = max(128, min(1024, CPU * 64))   # jumlah koneksi paralel global
PER_HOST_LIMIT = 8                                    # batasi per host supaya stabil
QUEUE_MAXSIZE = 50_000                                # backpressure; jangan terlalu besar
TIMEOUT = ClientTimeout(total=10, connect=4, sock_read=6)
READ_LIMIT_BYTES = 160_000
CHUNK_SIZE = 8_192
RETRIES = 1
PRINT_DETECTED = True      # print hijau saat WP ketemu
PRINT_PROGRESS_EVERY = 10000  # tampilkan progress tiap N baris (hemat I/O)

# Pola deteksi (bytes, biar tak perlu decode)
WP_PATTERNS = (
    b"wp-content", b"wp-includes", b"wordpress",
    b'name=\"generator\"', b"api.w.org", b"wp-json"
)

# =========================
# Inisialisasi
# =========================
init(autoreset=True)

# limiter per-host
_host_locks: dict[str, asyncio.Semaphore] = {}
def host_sem(host: str) -> asyncio.Semaphore:
    if host not in _host_locks:
        _host_locks[host] = asyncio.Semaphore(PER_HOST_LIMIT)
    return _host_locks[host]

def normalize_url(url: str) -> str | None:
    u = url.strip()
    if not u:
        return None
    if not u.startswith(("http://", "https://")):
        u = "http://" + u  # default aman
    return u

def alt_scheme(u: str) -> str:
    return ("https://" if u.startswith("http://") else "http://") + u.split("://", 1)[1]

def base_host(u: str) -> str:
    try:
        return urlparse(u).hostname or ""
    except:
        return ""

# =========================
# Detektor hemat CPU & bandwidth
# =========================
async def read_and_detect_streaming(resp: aiohttp.ClientResponse) -> bool:
    read_total = 0
    prev_tail = b""
    max_pat = max(len(p) for p in WP_PATTERNS)
    async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
        if not chunk:
            break
        buf = (prev_tail + chunk).lower()
        # rolling window: antisipasi pola ke-belah antar-chunk
        for pat in WP_PATTERNS:
            if pat in buf:
                return True
        prev_tail = buf[-(max_pat - 1):] if max_pat > 1 else b""
        read_total += len(chunk)
        if read_total >= READ_LIMIT_BYTES:
            break
    return False

async def try_head_then_get(session: aiohttp.ClientSession, url: str) -> bool:
    # HEAD -> murah
    try:
        r = await session.request("HEAD", url, allow_redirects=True)
        hdrs = str(r.headers).lower()
        if ("x-generator" in hdrs and "wordpress" in hdrs) or "wordpress" in hdrs or "api.w.org" in hdrs:
            return True
    except:
        pass
    # GET streaming singkat
    try:
        async with session.get(url, allow_redirects=True) as r:
            link_hdr = (r.headers.get("Link") or "").lower()
            if "api.w.org" in link_hdr:
                return True
            return await read_and_detect_streaming(r)
    except:
        return False

async def is_wordpress(session: aiohttp.ClientSession, url: str) -> bool:
    # Coba http/https dua kali maksimal; retry ringan sudah di dalam
    for _scheme_try in range(2):
        for _attempt in range(RETRIES + 1):
            ok = await try_head_then_get(session, url)
            if ok:
                return True
            break  # try schema lain
        url = alt_scheme(url)
    return False

# =========================
# Writer: append segera ke wordpress.txt (aman untuk paralel)
# =========================
class SafeWriter:
    def __init__(self, path: str):
        self.path = path
        # kosongkan diawal
        open(self.path, "w", encoding="utf-8").close()
        self._lock = asyncio.Lock()

    async def append(self, line: str):
        async with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line.rstrip("\n") + "\n")

# =========================
# Checkpointing untuk ratusan juta baris
# =========================
def ckpt_path_for(input_file: str) -> str:
    return input_file + ".ckpt.json"

def load_checkpoint(input_file: str) -> int:
    """Return byte offset to seek. 0 if none/invalid."""
    p = ckpt_path_for(input_file)
    try:
        st = os.stat(input_file)
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("size") == st.st_size and data.get("mtime") == int(st.st_mtime):
            return int(data.get("offset", 0))
    except Exception:
        pass
    return 0

def save_checkpoint(input_file: str, offset: int):
    p = ckpt_path_for(input_file)
    try:
        st = os.stat(input_file)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"offset": int(offset), "size": st.st_size, "mtime": int(st.st_mtime)}, f)
    except Exception:
        pass

# =========================
# Pipeline Streaming (Queue)
# =========================
async def producer(input_file: str, q: asyncio.Queue):
    """Baca file baris-per-baris dengan seek offset (checkpoint)."""
    offset = load_checkpoint(input_file)
    processed = 0
    with open(input_file, "rb") as bf:
        if offset:
            bf.seek(offset)
        while True:
            pos_before = bf.tell()
            line = bf.readline()
            if not line:
                break
            pos_after = bf.tell()
            # decode aman
            try:
                s = line.decode("utf-8", errors="ignore").strip()
            except:
                s = ""
            if s:
                await q.put((s, pos_after))
                processed += 1
                # progress tulis checkpoint tiap ~50k baris
                if processed % 50_000 == 0:
                    save_checkpoint(input_file, pos_after)
            # throttle ringan agar tidak flood queue jika consumer lambat
            if q.qsize() >= QUEUE_MAXSIZE * 0.9:
                await asyncio.sleep(0)  # yield
    # final checkpoint posisi file
    try:
        save_checkpoint(input_file, bf.tell())
    except:
        pass
    # sinyal selesai
    for _ in range(8):
        await q.put((None, -1))

async def worker(id_: int, q: asyncio.Queue, session: aiohttp.ClientSession, writer: SafeWriter, counters: dict, input_file: str):
    global_count = 0
    while True:
        item = await q.get()
        if item is None or item[0] is None:
            q.task_done()
            break
        url_raw, file_pos = item
        norm = normalize_url(url_raw)
        if not norm:
            q.task_done()
            continue
        host = base_host(norm)
        sem = host_sem(host)

        async with sem:
            ok = await is_wordpress(session, norm)

        counters["seen"] += 1
        if ok:
            if PRINT_DETECTED:
                print(Fore.GREEN + f"[WordPress detected] {url_raw}")
            await writer.append(url_raw)
        # progress minimal
        if counters["seen"] % PRINT_PROGRESS_EVERY == 0:
            print(f"… processed {counters['seen']:,} lines")
            # simpan checkpoint terbaru
            save_checkpoint(input_file, file_pos)

        q.task_done()

async def run_streaming(input_file: str, output_file: str):
    # queue dengan batas (hemat RAM)
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    writer = SafeWriter(output_file)
    counters = {"seen": 0}

    # konektor & session
    connector = aiohttp.TCPConnector(limit=GLOBAL_CONCURRENCY, ssl=False, ttl_dns_cache=600)
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0 (compatible; WPDetector-XL/1.0)"}
    ) as session:
        # jalankan producer
        prod_task = asyncio.create_task(producer(input_file, q))

        # jalankan workers
        workers = [
            asyncio.create_task(worker(i, q, session, writer, counters, input_file))
            for i in range(GLOBAL_CONCURRENCY)
        ]

        # tunggu semua selesai
        await prod_task
        await q.join()
        # hentikan worker
        for w in workers:
            w.cancel()
        # swallow cancellation
        try:
            await asyncio.gather(*workers, return_exceptions=True)
        except:
            pass

    print(f"\n✓ Selesai. Total diproses: {counters['seen']:,}. Hasil tersimpan di '{output_file}'.")
    print("   (Kalau proses terputus, jalankan lagi — akan otomatis lanjut dari checkpoint.)")

def check_cms_from_input_file():
    input_file = input("\t[ W O R D P R E S S  -  D E T E C T O R ]\n\n[*] Masukkan list web : ").strip()
    output_file = "wordpress.txt"
    try:
        # pastikan file ada
        open(input_file, "rb").close()
        asyncio.run(run_streaming(input_file, output_file))
    except FileNotFoundError:
        print(f"⚠️ File '{input_file}' tidak ditemukan.")

if __name__ == "__main__":
    # Catatan: untuk throughput maksimal di Linux, bisa pakai uvloop, tapi sengaja tidak diaktifkan
    check_cms_from_input_file()
