"""
Law update checker: compare revision dates in our DB against adilet.zan.kz.

Usage:
    python check_updates.py          — print report to stdout
    from check_updates import run_check  — returns structured dict (for Flask)
"""
import os, re, sys, sqlite3, time
sys.stdout.reconfigure(encoding='utf-8')

try:
    import requests
    requests.packages.urllib3.disable_warnings()
    USE_REQUESTS = True
except ImportError:
    import urllib.request, ssl
    USE_REQUESTS = False

DB_PATH = "laws_database.db"
ADILET   = "https://adilet.zan.kz/rus/docs/"

# (adilet_id, db_search_keyword, display_name)
LAWS_TO_CHECK = [
    # Кодексы
    ("k1500000414", "Трудовой кодекс",          "Трудовой кодекс"),
    ("k0300000442", "Земельный кодекс",          "Земельный кодекс"),
    ("k9400000268", "Гражданский кодекс%Общ",    "ГК (общая часть)"),
    ("k9900000409", "Гражданский кодекс%Особ",   "ГК (особенная часть)"),
    ("k2100000400", "Экологический кодекс",      "Экологический кодекс"),
    ("k1700000125", "недрах и недропользовании", "Кодекс о недрах"),
    ("k1400000235", "КоАП",                      "КоАП"),
    ("k1400000226", "Уголовный кодекс",          "Уголовный кодекс"),
    ("k2500000214", "Налоговый кодекс",          "Налоговый кодекс"),
    # Конституция
    ("k9500001000", "Конституция",               "Конституция"),
    # Законы
    ("z1400000188", "гражданской защите",        "О гражданской защите"),
    ("z1400000202", "разрешениях",               "О разрешениях"),
    ("z1600000442", "атомной энергии",           "Об атомной энергии"),
    ("z100000274_", "защите прав потребителей",  "О защите прав потребителей"),
    # Ведомственные НПА
    ("v1500011779", "опасных грузов",            "Правила пер. опасных грузов"),
    ("v2100024045", "пожарной безопасности",     "Пожарный регламент"),
    ("v2100026341", "коммунальными отходами",    "Правила ком. отходов"),
    ("v2300033003", "дорожного движения",        "ПДД"),
]

DATE_RE      = re.compile(r'(\d{2}\.\d{2}\.\d{4})')
REDACTION_RE = re.compile(r'ред\.\s*(\d{2}\.\d{2}\.\d{4})', re.IGNORECASE)

# Patterns for "current revision date" on adilet.zan.kz, in order of specificity.
# adilet pages show: "по состоянию на <b>12.03.2026</b>" or plain "по состоянию на 12.03.2026"
_SITE_DATE_PATTERNS = [
    re.compile(r'по состоянию на[:\s]*<[^>]+>(\d{2}\.\d{2}\.\d{4})', re.IGNORECASE),
    re.compile(r'по состоянию на[:\s]+(\d{2}\.\d{2}\.\d{4})',         re.IGNORECASE),
    re.compile(r'в редакции.{0,80}(\d{2}\.\d{2}\.\d{4})',            re.IGNORECASE),
    re.compile(r'последнег[оо] изменени[яе][:\s]+(\d{2}\.\d{2}\.\d{4})', re.IGNORECASE),
]

_session = None


def _get_session():
    global _session
    if _session is None and USE_REQUESTS:
        _session = requests.Session()
        _session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
            ),
            'Accept-Language': 'ru-RU,ru;q=0.9',
            'Accept': 'text/html,application/xhtml+xml',
        })
    return _session


def fetch_page(doc_id: str) -> tuple[str | None, str]:
    url = ADILET + doc_id.upper()
    try:
        if USE_REQUESTS:
            r = _get_session().get(url, verify=False, timeout=20)
            return (r.text if r.status_code == 200 else None), url
        else:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                return resp.read().decode('utf-8', errors='replace'), url
    except Exception:
        return None, url


def parse_latest_date(html: str) -> str | None:
    """
    Extract the current revision date from an adilet.zan.kz document page.
    Tries specific patterns first, then falls back to the header area only.
    """
    # 1. Try authoritative "по состоянию на" and related patterns
    for pattern in _SITE_DATE_PATTERNS:
        m = pattern.search(html)
        if m:
            return m.group(1)

    # 2. Fallback: look only in the first 5 000 chars (document title/header area).
    #    This avoids false positives from dates deep in the document text.
    header = html[:5000]
    candidates = []
    for d in DATE_RE.findall(header):
        try:
            y = int(d.split('.')[2])
            if 2020 <= y <= 2030:
                candidates.append(d)
        except Exception:
            pass
    if candidates:
        return max(candidates, key=lambda d: tuple(int(x) for x in reversed(d.split('.'))))

    return None


def _date_to_int(d: str | None) -> int:
    """Convert 'DD.MM.YYYY' → integer YYYYMMDD for comparison. Returns 0 on failure."""
    if not d or d == "нет даты":
        return 0
    try:
        parts = d.split('.')
        return int(parts[2]) * 10000 + int(parts[1]) * 100 + int(parts[0])
    except Exception:
        return 0


def get_db_date(keyword: str) -> tuple[str | None, str | None]:
    """
    Read the revision date of a law from the DB law_name field.
    Returns (law_name, revision_date_string) or (None, None) if not found.
    """
    if not os.path.exists(DB_PATH):
        return None, None
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT law_name FROM laws WHERE LOWER(law_name) LIKE LOWER(?)",
            (f"%{keyword}%",),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return None, None

    if not rows:
        return None, None

    law_name = rows[0][0]
    m = REDACTION_RE.search(law_name)
    if m:
        return law_name, m.group(1)
    m2 = DATE_RE.search(law_name)
    return law_name, (m2.group(0) if m2 else "нет даты")


def run_check() -> dict:
    """
    Check all tracked laws and return a structured report dict.
    Keys: checked_at, needs_update, ok, errors, not_in_db
    """
    from datetime import date as _date
    report = {
        "checked_at": _date.today().isoformat(),
        "needs_update": [],
        "ok": [],
        "errors": [],
        "not_in_db": [],
    }

    for doc_id, db_keyword, display_name in LAWS_TO_CHECK:
        url = ADILET + doc_id.upper()
        law_name, db_date = get_db_date(db_keyword)

        if not law_name:
            report["not_in_db"].append({"name": display_name, "url": url})
            time.sleep(0.5)
            continue

        html, _ = fetch_page(doc_id)
        if not html:
            report["errors"].append({"name": display_name, "db_date": db_date, "url": url,
                                     "error": "Не удалось загрузить страницу"})
            time.sleep(1)
            continue

        site_date = parse_latest_date(html)
        if not site_date:
            report["errors"].append({"name": display_name, "db_date": db_date, "url": url,
                                     "error": "Дата не найдена на странице"})
            time.sleep(1)
            continue

        if _date_to_int(site_date) > _date_to_int(db_date):
            report["needs_update"].append({
                "name": display_name,
                "db_date": db_date,
                "site_date": site_date,
                "url": url,
            })
        else:
            report["ok"].append({
                "name": display_name,
                "db_date": db_date,
                "site_date": site_date,
            })

        time.sleep(0.8)

    return report


def _print_report(report: dict):
    w = 85
    print(f"\n{'='*w}")
    print(f"  ПРОВЕРКА АКТУАЛЬНОСТИ ЗАКОНОВ — {report['checked_at']}")
    print(f"{'='*w}")
    print(f"{'Закон':<42} {'В базе':<12} {'На сайте':<12} Статус")
    print("-" * w)

    for item in report.get("ok", []):
        print(f"{item['name']:<42} {item['db_date']:<12} {item['site_date']:<12} ✓ актуально")

    for item in report.get("needs_update", []):
        print(f"{item['name']:<42} {item['db_date']:<12} {item['site_date']:<12} ⬆ ОБНОВИТЬ")

    for item in report.get("errors", []):
        print(f"{item['name']:<42} {item.get('db_date','?'):<12} {'ОШИБКА':<12} ? {item['error']}")

    for item in report.get("not_in_db", []):
        print(f"{item['name']:<42} {'НЕТ В БАЗЕ':<12} {'—':<12} ⚠")

    needs = report.get("needs_update", [])
    if needs:
        print(f"\n{'='*w}")
        print(f"Нужно обновить ({len(needs)}):")
        for item in needs:
            print(f"  • {item['name']}: {item['db_date']} → {item['site_date']}")
            print(f"    {item['url']}")
    else:
        print("\nВсе законы в базе актуальны!")
    print(f"{'='*w}\n")


def main():
    print("Проверяю актуальность законов на adilet.zan.kz...")
    report = run_check()
    _print_report(report)
    # Exit non-zero if outdated laws found (triggers GitHub Actions failure → email)
    if report["needs_update"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
