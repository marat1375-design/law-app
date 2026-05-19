import os
import sqlite3
import re
import json
from flask import Flask, render_template_string, request, jsonify, Response
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
        ("Налоговый.docx", "Налоговый кодекс РК от 25.12.2017 № 120-VI"),
        ("Кодекс Республики Казахстан об административных правонарушениях от 5 июля 2014 года № 235-V (с изменениями и дополнениями по сос.docx", "КоАП РК от 05.07.2014 № 235-V"),
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
    cursor.execute("SELECT law_name, article_num, text_content FROM laws")
    rows = cursor.fetchall()
    connection.close()
    query_lower = query.lower()
    words = query_lower.split()
    results = []
    for row in rows:
        law_name, article_num, text_content = row[0], row[1], row[2]
        text_lower = text_content.lower()
        name_lower = law_name.lower()
        if all(w in text_lower or w in name_lower for w in words):
            score = text_lower.count(query_lower)
            if score > 0:
                results.append({
                    "law_name": law_name,
                    "article_num": article_num,
                    "text_content": text_content,
                    "score": score
                })
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:20]

@app.route("/")
def home():
    with open("index.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return render_template_string(html_content)

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