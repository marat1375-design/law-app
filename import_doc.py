"""Import .doc files via Word COM (requires MS Word installed + pywin32)."""
import sys, re, sqlite3, os
sys.stdout.reconfigure(encoding='utf-8')
import win32com.client

DB_PATH = "laws_database.db"
FOLDER  = r'C:\Users\Marat\OneDrive\Desktop\Закон'

ARTICLE_RE    = re.compile(r'^Статья \d+[\-\d]*\.')
CHAPTER_RE    = re.compile(r'^Глава \d+[\.\s]')
TOP_SECTION_RE = re.compile(r'^(\d{1,2})\.\s+\S')   # top-level numbered section


def read_doc(word_app, path):
    doc = word_app.Documents.Open(os.path.abspath(path), ReadOnly=True)
    paras = [p.Range.Text.strip() for p in doc.Paragraphs if p.Range.Text.strip()]
    doc.Close(False)
    return paras


def parse_by_article(paras):
    items, cur_num, cur_lines = [], None, []
    for t in paras:
        if ARTICLE_RE.match(t):
            if cur_num:
                items.append((cur_num, "\n\n".join(cur_lines)))
            cur_num, cur_lines = t, []
        elif cur_num:
            cur_lines.append(t)
    if cur_num:
        items.append((cur_num, "\n\n".join(cur_lines)))
    return items


def parse_by_chapter(paras):
    items, cur_num, cur_lines = [], None, []
    for t in paras:
        if CHAPTER_RE.match(t):
            if cur_num:
                items.append((cur_num, "\n\n".join(cur_lines)))
            cur_num, cur_lines = t, []
        elif cur_num:
            cur_lines.append(t)
    if cur_num:
        items.append((cur_num, "\n\n".join(cur_lines)))
    return items


def parse_by_section(paras):
    """Split by top-level numbered sections (1., 2., ...) for technical standards."""
    items, cur_num, cur_lines = [], None, []
    for t in paras:
        if TOP_SECTION_RE.match(t) and len(t) < 150:
            if cur_num:
                items.append((cur_num, "\n\n".join(cur_lines)))
            cur_num, cur_lines = t, []
        elif cur_num:
            cur_lines.append(t)
    if cur_num:
        items.append((cur_num, "\n\n".join(cur_lines)))
    # If almost nothing parsed, fall back to single record
    if len(items) < 2:
        full = "\n\n".join(paras)
        return [(paras[0][:100] if paras else "Документ", full)]
    return items


def parse_as_one(paras):
    """Entire document as a single record (for tiny docs)."""
    return [(paras[0][:100] if paras else "Документ", "\n\n".join(paras))]


FILES = [
    # (filename, law_name, delete_like, parser)
    (
        'ЗЕМЕЛЬНЫЙ КОДЕКС РК ОТ 20.06.2003 № 442-II.doc',
        'Земельный кодекс РК от 20.06.2003 № 442-II (ред. 04.12.2024)',
        '%Земельный кодекс%',
        parse_by_article,
    ),
    (
        'ПРИКАЗ И.О. МИНИСТРА ЗДРАВООХРАНЕНИЯ РК ОТ 25.12.2020 № ҚР ДСМ-331 2020.doc',
        'Приказ МЗ РК от 25.12.2020 № ҚР ДСМ-331/2020 (Санитарные правила по биоотходам)',
        '%ДСМ-331%',
        parse_by_chapter,
    ),
    (
        'ПРИКАЗ МИНИСТРА ЭНЕРГЕТИКИ РК ОТ 08.02.2016 № 39.doc',
        'Приказ Министра энергетики РК от 08.02.2016 № 39 (Правила сбора, хранения, захоронения РАО)',
        '%энергетики%№ 39%',
        parse_by_chapter,
    ),
    (
        'ПРИКАЗ МИНИСТРА ЭНЕРГЕТИКИ РК ОТ 11.07.2016 ГОДА № 312 «ОБ УТВЕРЖДЕНИ.doc',
        'Приказ Министра энергетики РК от 11.07.2016 № 312 (Правила учёта отходов производства)',
        '%энергетики%№ 312%',
        parse_by_chapter,
    ),
    (
        'ПРИКАЗ МИНИСТРА ЭНЕРГЕТИКИ РК ОТ 28.05.2021 № 183.doc',
        'Приказ Министра энергетики РК от 28.05.2021 № 183 (Правила транспортировки ядерных материалов)',
        '%транспортировки ядерных%',
        parse_by_chapter,
    ),
    (
        'ГОСТ 17.4.3.02-85 (СТ СЭВ 4471-84).doc',
        'ГОСТ 17.4.3.02-85 Охрана природы. Почвы. Требования к охране плодородного слоя почвы',
        '%ГОСТ 17.4.3.02%',
        parse_as_one,
    ),
    (
        'ГОСТ 17.5.3.06-85.doc',
        'ГОСТ 17.5.3.06-85 Охрана природы. Земли. Нормы снятия плодородного слоя почвы',
        '%ГОСТ 17.5.3.06%',
        parse_as_one,
    ),
    (
        'СТ РК 1513-2019.doc',
        'СТ РК 1513-2019 Ресурсосбережение. Обращение с отходами',
        '%СТ РК 1513%',
        parse_by_section,
    ),
]


def upsert(cur, law_name, delete_like, items):
    cur.execute("DELETE FROM laws WHERE LOWER(law_name) LIKE LOWER(?)", (delete_like,))
    deleted = cur.rowcount
    cur.executemany(
        "INSERT INTO laws (law_name, article_num, text_content) VALUES (?, ?, ?)",
        [(law_name, num, text) for num, text in items],
    )
    return deleted, len(items)


def main():
    word = win32com.client.Dispatch('Word.Application')
    word.Visible = False

    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    total_deleted = total_added = 0

    for fname, law_name, delete_like, parser in FILES:
        path = os.path.join(FOLDER, fname)
        print(f"\n--- {fname[:60]} ---")
        try:
            paras = read_doc(word, path)
        except Exception as e:
            print(f"  ОШИБКА: {e}")
            continue

        items = parser(paras)
        print(f"  Распознано: {len(items)}")

        deleted, added = upsert(cur, law_name, delete_like, items)
        print(f"  Удалено старых: {deleted}  Добавлено: {added}")
        total_deleted += deleted
        total_added   += added

    conn.commit()
    conn.close()
    word.Quit()
    print(f"\n=== Итого: удалено {total_deleted}, добавлено {total_added} ===")


if __name__ == "__main__":
    main()
