import sys, re, sqlite3
sys.stdout.reconfigure(encoding='utf-8')
from docx import Document

DB_PATH = "laws_database.db"
DOCX_PATH = "k1500000414.19-05-2026.rus.docx"
LAW_NAME = "Трудовой кодекс РК от 23.11.2015 № 414-V (ред. 19.05.2026)"

ARTICLE_RE = re.compile(r'^Статья \d+[\-\d]*\.')

def parse_articles(path):
    doc = Document(path)
    paragraphs = [p.text.strip() for p in doc.paragraphs]

    articles = []
    current_num = None
    current_lines = []

    for text in paragraphs:
        if not text:
            continue
        if ARTICLE_RE.match(text):
            if current_num:
                articles.append((current_num, "\n\n".join(current_lines)))
            # Split "Статья 1. Title" → article_num = "Статья 1. Title"
            current_num = text
            current_lines = []
        else:
            if current_num:
                current_lines.append(text)

    if current_num:
        articles.append((current_num, "\n\n".join(current_lines)))

    return articles

def main():
    articles = parse_articles(DOCX_PATH)
    print(f"Распознано статей: {len(articles)}")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("DELETE FROM laws WHERE law_name LIKE '%Трудовой кодекс%'")
    deleted = cur.rowcount
    print(f"Удалено старых записей: {deleted}")

    rows = [(LAW_NAME, num, text) for num, text in articles]
    cur.executemany(
        "INSERT INTO laws (law_name, article_num, text_content) VALUES (?, ?, ?)",
        rows
    )
    conn.commit()
    conn.close()

    print(f"Добавлено новых записей: {len(rows)}")
    print("Готово!")

if __name__ == "__main__":
    main()
