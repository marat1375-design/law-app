"""Check if laws in the DB are up-to-date by comparing dates on adilet.zan.kz."""
import sys, re, sqlite3, time
sys.stdout.reconfigure(encoding='utf-8')
try:
    import requests
    requests.packages.urllib3.disable_warnings()
    USE_REQUESTS = True
except ImportError:
    import urllib.request, ssl
    USE_REQUESTS = False

DB_PATH = "laws_database.db"

# Map of law keywords -> (adilet doc ID, human name)
LAWS_TO_CHECK = [
    # (adilet_id, db_search_keyword, display_name)
    # Кодексы
    ("k1500000414", "Трудовой кодекс",              "Трудовой кодекс"),
    ("k0300000442", "Земельный кодекс",              "Земельный кодекс"),
    ("k9400000268", "Гражданский кодекс%Общ",        "ГК (общая часть)"),
    ("k9900000409", "Гражданский кодекс%Особ",       "ГК (особенная часть)"),
    ("k2500000178", "Водный кодекс",                 "Водный кодекс"),
    ("k2100000400", "Экологический кодекс",          "Экологический кодекс"),
    ("k1700000125", "недрах и недропользовании",     "Кодекс о недрах"),
    ("k1400000235", "КоАП",                          "КоАП"),
    ("k2500000214", "Налоговый кодекс",              "Налоговый кодекс"),
    # Конституция
    ("k9500001000", "Конституция",                   "Конституция"),
    # Законы
    ("z1400000188", "гражданской защите",            "О гражданской защите"),
    ("z1400000202", "разрешениях",                   "О разрешениях"),
    ("z1600000442", "атомной энергии",               "Об атомной энергии"),
    # Ведомственные НПА (ID из имён файлов)
    ("v1500011779", "опасных грузов",                "Правила пер. опасных грузов"),
    ("v2100024045", "пожарной безопасности",         "Пожарный регламент"),
    ("v2100026341", "коммунальными отходами",        "Правила ком. отходов"),
    ("v2300033003", "дорожного движения",            "ПДД"),
]

DATE_RE     = re.compile(r'(\d{2}\.\d{2}\.\d{4})')
CURRENT_RE  = re.compile(r'по состоянию на[:\s]*<[^>]+>(\d{2}\.\d{2}\.\d{4})', re.IGNORECASE)

_session = None

def get_session():
    global _session
    if _session is None and USE_REQUESTS:
        _session = requests.Session()
        _session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
            'Accept-Language': 'ru-RU,ru;q=0.9',
            'Accept': 'text/html,application/xhtml+xml',
        })
    return _session


def fetch_page(doc_id):
    url = f"https://adilet.zan.kz/rus/docs/{doc_id.upper()}"
    try:
        if USE_REQUESTS:
            r = get_session().get(url, verify=False, timeout=20)
            if r.status_code == 200:
                return r.text, url
            return None, url
        else:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                return resp.read().decode('utf-8', errors='replace'), url
    except Exception:
        return None, url


def parse_latest_date(html):
    """Extract 'по состоянию на: DD.MM.YYYY' — the authoritative current date."""
    m = CURRENT_RE.search(html)
    if m:
        return m.group(1)
    # Fallback: latest plausible date in the page
    dates = []
    for d in DATE_RE.findall(html):
        try:
            y = int(d.split('.')[2])
            if 2015 <= y <= 2027:
                dates.append(d)
        except Exception:
            pass
    if not dates:
        return None
    return max(dates, key=lambda d: tuple(int(x) for x in reversed(d.split('.'))))


REDACTION_RE = re.compile(r'ред\.\s*(\d{2}\.\d{2}\.\d{4})', re.IGNORECASE)


def get_db_date(law_keyword):
    """Extract the REVISION date from the law_name in the DB (prefer ред. date)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT law_name FROM laws WHERE LOWER(law_name) LIKE LOWER(?)",
        (f"%{law_keyword}%",)
    )
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return None, None
    law_name = rows[0][0]
    # Prefer "(ред. DD.MM.YYYY)" — that's the amendment date
    m = REDACTION_RE.search(law_name)
    if m:
        return law_name, m.group(1)
    # Fallback: any date in name
    m2 = DATE_RE.search(law_name)
    return law_name, m2.group(0) if m2 else "нет даты"


def main():
    print("Проверка актуальности законов на adilet.zan.kz\n")
    print(f"{'Закон':<42} {'В базе':<12} {'На сайте':<12} {'Статус'}")
    print("-" * 85)

    needs_update = []

    for doc_id, db_keyword, display_name in LAWS_TO_CHECK:
        law_name, db_date = get_db_date(db_keyword)
        if not law_name:
            print(f"{display_name:<42} {'НЕТ В БАЗЕ':<12} {'—':<12} ⚠")
            continue

        html, url = fetch_page(doc_id)
        if not html:
            print(f"{display_name:<42} {str(db_date):<12} {'ОШИБКА':<12} ?")
            time.sleep(1)
            continue

        site_date = parse_latest_date(html)
        if not site_date:
            print(f"{display_name:<42} {str(db_date):<12} {'не найдена':<12} ?")
            time.sleep(1)
            continue

        # Compare: convert dd.mm.yyyy to yyyymmdd for comparison
        def to_int(d):
            if not d or d == "нет даты":
                return 0
            p = d.split('.')
            try:
                return int(p[2]) * 10000 + int(p[1]) * 100 + int(p[0])
            except Exception:
                return 0

        db_int   = to_int(db_date)
        site_int = to_int(site_date)

        if site_int > db_int:
            status = "⬆ ОБНОВИТЬ"
            needs_update.append((display_name, db_date, site_date, url))
        elif site_int == db_int or db_int == 0:
            status = "✓ актуально"
        else:
            status = "✓ актуально"

        print(f"{display_name:<42} {str(db_date):<12} {site_date:<12} {status}")
        time.sleep(0.8)   # polite delay

    if needs_update:
        print(f"\n{'='*85}")
        print(f"Нужно обновить ({len(needs_update)}):")
        for name, old, new, url in needs_update:
            print(f"  {name}: {old} → {new}")
            print(f"    {url}")
    else:
        print("\nВсе законы актуальны!")


if __name__ == "__main__":
    main()
