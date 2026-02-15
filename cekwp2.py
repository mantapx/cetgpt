import asyncio
import aiohttp
from aiohttp import ClientTimeout
from colorama import init, Fore
import os

# =========================
# Konfigurasi hemat CPU
# =========================
CPU_CORES = os.cpu_count() or 2
MAX_CONCURRENCY = max(16, min(64, CPU_CORES * 8))
TIMEOUT = ClientTimeout(total=10, connect=4, sock_read=6)
READ_LIMIT_BYTES = 160_000
CHUNK_SIZE = 8_192
PRINT_NOT_WP = False
RETRIES = 1

# =========================
# Inisialisasi warna
# =========================
init(autoreset=True)

# =========================
# Util & Normalisasi
# =========================
def normalize_url(url):
    url = url.strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url

def alt_scheme(u: str) -> str:
    return ("https://" if u.startswith("http://") else "http://") + u.split("://", 1)[1]

WP_PATTERNS = (
    b"wp-content", b"wp-includes", b"wordpress",
    b'name="generator"', b"api.w.org", b"wp-json"
)

# =========================
# Detektor
# =========================
async def read_and_detect_streaming(resp: aiohttp.ClientResponse) -> bool:
    read_total = 0
    async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
        if not chunk:
            break
        low = chunk.lower()
        for pat in WP_PATTERNS:
            if pat in low:
                return True
        read_total += len(chunk)
        if read_total >= READ_LIMIT_BYTES:
            break
    return False

async def try_head_then_get(session: aiohttp.ClientSession, url: str) -> bool:
    try:
        r = await session.request("HEAD", url, allow_redirects=True)
        hdrs = str(r.headers).lower()
        if ("x-generator" in hdrs and "wordpress" in hdrs) or "wordpress" in hdrs:
            return True
    except:
        pass
    try:
        async with session.get(url, allow_redirects=True) as r:
            link_hdr = r.headers.get("Link", "") or ""
            if "api.w.org" in link_hdr.lower():
                return True
            return await read_and_detect_streaming(r)
    except:
        return False

async def is_wordpress(session: aiohttp.ClientSession, url: str) -> bool:
    for scheme_try in range(2):
        target = url if scheme_try == 0 else alt_scheme(url)
        for _ in range(RETRIES + 1):
            ok = await try_head_then_get(session, target)
            if ok:
                return True
            break
    return False

# =========================
# Worker & Pipeline
# =========================
async def worker(url, session, write_lock: asyncio.Lock, output_file: str):
    norm_url = normalize_url(url)
    if not norm_url:
        return
    ok = await is_wordpress(session, norm_url)
    if ok:
        print(Fore.GREEN + f"[WordPress detected] {url}")
        # LANGSUNG SIMPAN (append) SAAT TERDETEKSI
        async with write_lock:
            # gunakan mode append agar langsung tersimpan
            with open(output_file, "a", encoding="utf-8") as f:
                f.write(url + "\n")
    else:
        if PRINT_NOT_WP:
            print(Fore.RED + f"[Not WordPress] {url}")

async def run_scan(urls, output_file):
    # Dedup
    seen = set()
    deduped = [u for u in urls if (u not in seen and not seen.add(u))]

    # Kosongkan output DI AWAL
    open(output_file, "w", encoding="utf-8").close()

    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    write_lock = asyncio.Lock()

    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENCY, ssl=False, ttl_dns_cache=300)

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0 (compatible; WPDetector-Lite/1.1)"}
    ) as session:

        async def bounded_task(u):
            async with sem:
                await worker(u, session, write_lock, output_file)

        await asyncio.gather(*(bounded_task(u) for u in deduped))

    print(f"\n✓ Selesai. Hasil tersimpan di '{output_file}'")

def check_cms_from_input_file():
    input_file = input("\t[ W O R D P R E S S  -  D E T E C T O R ]\n\n[*] Masukkan list web : ").strip()
    output_file = "wordpress.txt"

    try:
        with open(input_file, 'r', encoding="utf-8", errors="ignore") as f:
            urls = [line.strip() for line in f if line.strip()]
        asyncio.run(run_scan(urls, output_file))
    except FileNotFoundError:
        print(f"⚠️ File '{input_file}' tidak ditemukan.")

if __name__ == "__main__":
    check_cms_from_input_file()
