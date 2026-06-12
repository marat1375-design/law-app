import sys, re, sqlite3
sys.stdout.reconfigure(encoding='utf-8')
from docx import Document

DB_PATH = "laws_database.db"

ARTICLE_RE    = re.compile(r'^Статья \d+[\-\d]*\.')
ARTICLE_ND_RE = re.compile(r'^Статья \d+$')   # Конституция: без точки
CHAPTER_RE    = re.compile(r'^Глава \d+[\.\s]')


def parse_by_article(path):
    doc = Document(path)
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    items, current_num, current_lines = [], None, []
    for text in paras:
        if ARTICLE_RE.match(text):
            if current_num:
                items.append((current_num, "\n\n".join(current_lines)))
            current_num, current_lines = text, []
        elif current_num:
            current_lines.append(text)
    if current_num:
        items.append((current_num, "\n\n".join(current_lines)))
    return items


def parse_by_article_no_dot(path):
    """Конституция: 'Статья N' без заголовка."""
    doc = Document(path)
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    items, current_num, current_lines = [], None, []
    for text in paras:
        if ARTICLE_ND_RE.match(text):
            if current_num:
                items.append((current_num, "\n\n".join(current_lines)))
            current_num, current_lines = text, []
        elif current_num:
            current_lines.append(text)
    if current_num:
        items.append((current_num, "\n\n".join(current_lines)))
    return items


def parse_by_chapter(path):
    """Правила: разбивка по главам."""
    doc = Document(path)
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    items, current_num, current_lines = [], None, []
    for text in paras:
        if CHAPTER_RE.match(text):
            if current_num:
                items.append((current_num, "\n\n".join(current_lines)))
            current_num, current_lines = text, []
        elif current_num:
            current_lines.append(text)
    if current_num:
        items.append((current_num, "\n\n".join(current_lines)))
    return items


FILES = [
    # (docx_file, law_name_in_db, delete_pattern, parser)
    (
        'z1400000188.09-01-2026.rus.docx',
        'Закон РК «О гражданской защите» от 11.04.2014 № 188-V (ред. 09.01.2026)',
        '%гражданской защите%',
        parse_by_article,
    ),
    (
        'z1400000202.08-06-2026.rus.docx',
        'Закон РК «О разрешениях и уведомлениях» от 16.05.2014 № 202-V (ред. 08.06.2026)',
        '%разрешениях%',
        parse_by_article,
    ),
    (
        'k1400000235.12-03-2026.rus.docx',
        'КоАП РК',
        '%КоАП%',
        parse_by_article,
    ),
    (
        'Налоговый.docx',
        'Налоговый кодекс РК от 18.07.2025 № 214-VIII',
        '%Налоговый%',
        parse_by_article,
    ),
    (
        'v2300033003.27-04-2026.rus.docx',
        'Правила дорожного движения РК (ред. 27.04.2026)',
        '%дорожного движения%',
        parse_by_chapter,
    ),
    (
        'v2100024045.17-09-2025.rus.docx',
        'Технический регламент «Общие требования к пожарной безопасности» (ред. 17.09.2025)',
        '%пожарной безопасности%',
        parse_by_chapter,
    ),
    (
        'v1500011779.25-04-2026.rus.docx',
        'Правила перевозки опасных грузов автомобильным транспортом (ред. 25.04.2026)',
        '%опасных грузов%',
        parse_by_chapter,
    ),
    (
        'v2100026341.06-11-2023.rus.docx',
        'Правила управления коммунальными отходами (ред. 06.11.2023)',
        '%коммунальными отходами%',
        parse_by_chapter,
    ),
    (
        'Конституция РК новая.docx',
        'Конституция Республики Казахстан (ред. 15.03.2026)',
        '%Конституция%',
        parse_by_article_no_dot,
    ),
]


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    total_deleted = 0
    total_added = 0

    for docx_file, law_name, delete_pattern, parser in FILES:
        print(f"\n--- {law_name} ---")
        items = parser(docx_file)
        print(f"  Распознано: {len(items)}")

        cur.execute("DELETE FROM laws WHERE LOWER(law_name) LIKE LOWER(?)", (delete_pattern,))
        deleted = cur.rowcount
        print(f"  Удалено старых: {deleted}")

        rows = [(law_name, num, text) for num, text in items]
        cur.executemany(
            "INSERT INTO laws (law_name, article_num, text_content) VALUES (?, ?, ?)",
            rows,
        )
        print(f"  Добавлено: {len(rows)}")
        total_deleted += deleted
        total_added += len(rows)

    conn.commit()
    conn.close()
    print(f"\n=== Итого: удалено {total_deleted}, добавлено {total_added} записей ===")


if __name__ == "__main__":
    main()
