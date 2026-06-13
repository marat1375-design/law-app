"""Download updated DOCX files from adilet.zan.kz for 12 outdated laws."""
import sys, os, time, requests
sys.stdout.reconfigure(encoding='utf-8')

requests.packages.urllib3.disable_warnings()

LAWS = [
    # (doc_id, filename_out)
    ("K1500000414", "k1500000414.11-06-2026.rus.docx"),
    ("K2100000400", "k2100000400.11-06-2026.rus.docx"),
    ("K1700000125", "k1700000125.11-06-2026.rus.docx"),
    ("K1400000235", "k1400000235.11-06-2026.rus.docx"),
    ("K2500000214", "k2500000214.11-06-2026.rus.docx"),
    ("Z1400000188", "z1400000188.11-06-2026.rus.docx"),
    ("Z1400000202", "z1400000202.11-06-2026.rus.docx"),
    ("Z1600000442", "z1600000442.11-06-2026.rus.docx"),
    ("V1500011779", "v1500011779.11-06-2026.rus.docx"),
    ("V2100024045", "v2100024045.11-06-2026.rus.docx"),
    ("V2100026341", "v2100026341.11-06-2026.rus.docx"),
    ("V2300033003", "v2300033003.11-06-2026.rus.docx"),
    ("K1400000226", "k1400000226.11-06-2026.rus.docx"),
    ("Z100000274_", "z100000274_.11-06-2026.rus.docx"),
]

BASE = "https://adilet.zan.kz"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def download_one(session, doc_id, out_path):
    page_url = f"{BASE}/rus/docs/{doc_id}"
    docx_url = f"{BASE}/rus/docs/{doc_id}/download/docx"

    # 1. Fetch page to get cookies
    r = session.get(page_url, headers=HEADERS, verify=False, timeout=30)
    if r.status_code != 200:
        return f"Page {r.status_code}"

    # 2. Download DOCX with Referer + cookies
    dl_headers = {**HEADERS, "Referer": page_url}
    r2 = session.get(docx_url, headers=dl_headers, verify=False, timeout=60)
    if r2.status_code != 200:
        return f"Download {r2.status_code}"

    # Validate it's a ZIP (DOCX)
    if r2.content[:2] != b'PK':
        return f"Not a DOCX (got {len(r2.content)} bytes, starts {r2.content[:4]})"

    with open(out_path, 'wb') as f:
        f.write(r2.content)
    return f"OK {len(r2.content):,} bytes"


def main():
    session = requests.Session()
    results = []

    for doc_id, filename in LAWS:
        print(f"  {doc_id} → {filename} ... ", end='', flush=True)
        try:
            status = download_one(session, doc_id, filename)
        except Exception as e:
            status = f"ERROR: {e}"
        print(status)
        results.append((doc_id, filename, status))
        time.sleep(1.5)  # polite crawl

    ok = [r for r in results if r[2].startswith("OK")]
    fail = [r for r in results if not r[2].startswith("OK")]
    print(f"\n=== Скачано: {len(ok)}/{len(LAWS)} ===")
    if fail:
        print("Ошибки:")
        for doc_id, fn, status in fail:
            print(f"  {doc_id}: {status}")


if __name__ == "__main__":
    main()
