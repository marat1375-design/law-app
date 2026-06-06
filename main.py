import os
import sqlite3
import re
import json
from flask import Flask, render_template_string, request, jsonify, Response, send_file
from docx import Document

app = Flask(__name__)

def read_docx(file_path):
    try:
        doc = Document(file_path)
        return "\n".join([para.text for para in doc.paragraphs])
    except:
        return ""

def parse_and_save_to_db():
    db_path = "laws_database.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    connection = sqlite3.connect(db_path)
    cursor = connection.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS laws (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        law_name TEXT,
        article_num TEXT,
        text_content TEXT
    )
    """)

    txt_files = [
        ("ecocode (1).txt", "Экологический кодекс РК от 02.01.2021 № 400-VI"),
        ("koap_final.txt", "КоАП РК"),
        ("nedra.txt", "Кодекс РК «О недрах и недропользовании» от 27.12.2017 № 125-VI"),
        ("sanpin1.txt", "Приказ МЗ РК от 15.12.2020 № ҚР ДСМ-275/2020"),
        ("sanpin2.txt", "Приказ МЗ РК от 25.08.2022 № ҚР ДСМ-90"),
        ("atom.txt", "Закон РК «Об использовании атомной энергии» от 12.01.2016 № 442-V"),
    ]

    docx_files = [
        ("Конституция РК новая.docx", "Конституция Республики Казахстан"),
        ("v2100024045.17-09-2025.rus.docx", "Технический регламент «Общие требования к пожарной безопасности»"),
        ("z1400000188.09-01-2026.rus.docx", "Закон РК «О гражданской защите» от 11.04.2014 № 188-V"),
        ("z1400000202.08-06-2026.rus.docx", "Закон РК «О разрешениях и уведомлениях» от 16.05.2014 № 202-V"),
        ("v1500011779.25-04-2026.rus.docx", "Правила перевозки опасных грузов автомобильным транспортом"),
        ("v2100026341.06-11-2023.rus.docx", "Правила управления коммунальными отходами"),
    ]

    seen = set()
    total = 0

    for file_name, law_name in txt_files:
        if not os.path.exists(file_name):
            print(f"Файл не найден: {file_name}")
            continue
        print(f"Читаем: {file_name}...")
        with open(file_name, "r", encoding="utf-8") as f:
            text = f.read()
        articles = re.split(r'(?=Статья\s+\d+|Раздел\s+\d+|Пункт\s+\d+)', text)
        for art in articles:
            art = art.strip()
            if not art:
                continue
            lines = art.split("\n")
            article_num = lines[0].strip()[:120]
            text_content = "\n".join(lines[1:]).strip()
            if len(text_content) > 10:
                fingerprint = text_content[:200]
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                cursor.execute(
                    "INSERT INTO laws (law_name, article_num, text_content) VALUES (?, ?, ?)",
                    (law_name, article_num, text_content)
                )
                total += 1

    for file_name, law_name in docx_files:
        if not os.path.exists(file_name):
            print(f"Файл не найден: {file_name}")
            continue
        print(f"Читаем: {file_name}...")
        text = read_docx(file_name)
        if not text:
            print(f"Не удалось прочитать: {file_name}")
            continue
        articles = re.split(r'(?=Статья\s+\d+|Раздел\s+\d+|Пункт\s+\d+)', text)
        for art in articles:
            art = art.strip()
            if not art:
                continue
            lines = art.split("\n")
            article_num = lines[0].strip()[:120]
            text_content = "\n".join(lines[1:]).strip()
            if len(text_content) > 10:
                fingerprint = text_content[:200]
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                cursor.execute(
                    "INSERT INTO laws (law_name, article_num, text_content) VALUES (?, ?, ?)",
                    (law_name, article_num, text_content)
                )
                total += 1

    connection.commit()
    connection.close()
    print(f"База заполнена: {total} статей")

parse_and_save_to_db()

def search_laws_in_db(query):
    connection = sqlite3.connect("laws_database.db")
    cursor = connection.cursor()
    query_lower = query.lower()
    words = query_lower.split()
    results = []

    like_clause = " AND ".join([f"LOWER(text_content) LIKE ?" for w in words])
    params = [f"%{w}%" for w in words]

    cursor.execute(f"""
        SELECT law_name, article_num, text_content
        FROM laws
        WHERE {like_clause}
        LIMIT 50
    """, params)

    rows = cursor.fetchall()
    connection.close()

    for row in rows:
        law_name, article_num, text_content = row[0], row[1], row[2]
        score = sum(text_content.lower().count(w) for w in words)
        results.append({
            "law_name": law_name,
            "article_num": article_num,
            "text_content": text_content,
            "score": score
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results

@app.route("/")
def home():
    with open("index.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return render_template_string(html_content)

@app.route("/manifest.json")
def manifest():
    return send_file("manifest.json", mimetype="application/json")

@app.route("/sw.js")
def sw():
    return send_file("sw.js", mimetype="application/javascript")

@app.route("/api/search")
def search_api():
    query = request.args.get("q", "")
    if not query:
        return jsonify([])
    return Response(
        json.dumps(search_laws_in_db(query), ensure_ascii=False),
        mimetype='application/json; charset=utf-8'
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))