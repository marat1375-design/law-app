import os
import sqlite3
import re
from flask import Flask, render_template_string, request, jsonify
from docx import Document

app = Flask(__name__)

def read_docx(file_path):
    try:
        doc = Document(file_path)
        return "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
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
    files_to_parse = [
        ("КОДЕКС_РК_ОТ_02_01_2021___400-VI__ЭКОЛОГИЧЕСКИЙ_КОДЕКС_РК_.doc", "Экологический кодекс РК от 02.01.2021 № 400-VI"),
        ("КОДЕКС_РК_ОТ_27_12_2017___125-VI__О_НЕДРАХ_И_НЕДРОПОЛЬЗОВАНИИ_.doc", "Кодекс РК «О недрах и недропользовании» от 27.12.2017 № 125-VI"),
        ("ГРАЖДАНСКИЙ_КОДЕКС_РК__ОСОБЕННАЯ_ЧАСТЬ__ОТ_01_07_1999___409-I.doc", "Гражданский кодекс РК (Особенная часть) от 01.07.1999 № 409-I"),
        ("ЗЕМЕЛЬНЫЙ_КОДЕКС_РК_ОТ_20_06_2003___442-II.doc", "Земельный кодекс РК от 20.06.2003 № 442-II"),
        ("ЗАКОН_РК__ОБ_ИСПОЛЬЗОВАНИИ_АТОМНОЙ_ЭНЕРГИИ__ОТ_12_01_2016___442-V.doc", "Закон РК «Об использовании атомной энергии» от 12.01.2016 № 442-V"),
        ("ПРИКАЗ_МИНИСТРА_ЗДРАВООХРАНЕНИЯ_РК_ОТ_25_08_2022___ҚР_ДСМ-90.doc", "Приказ МЗ РК от 25.08.2022 № ҚР ДСМ-90"),
        ("ПРИКАЗ_И_О__МИНИСТРА_ЗДРАВООХРАНЕНИЯ_РК_ОТ_25_12_2020___ҚР_ДСМ-331_2020.doc", "Приказ МЗ РК от 25.12.2020 № ҚР ДСМ-331/2020"),
        ("ПРИКАЗ_И_О__МИНИСТРА_ЭКОЛОГИИ__ГЕОЛОГИИ_И_ПРИРОДНЫХ_РЕСУРСОВ_РК_ОТ_28.doc", "Приказ и.о. Министра экологии, геологии и природных ресурсов РК"),
        ("ПРИКАЗ_МИНИСТРА_ЭНЕРГЕТИКИ_РК_ОТ_08_02_2016___39.doc", "Приказ Министра энергетики РК от 08.02.2016 № 39"),
        ("ПРИКАЗ_МИНИСТРА_ЭНЕРГЕТИКИ_РК_ОТ_11_07_2016_ГОДА___312__ОБ_УТВЕРЖДЕНИ.doc", "Приказ Министра энергетики РК от 11.07.2016 № 312"),
        ("ПРИКАЗ_МИНИСТРА_ЭНЕРГЕТИКИ_РК_ОТ_28_05_2021___183.doc", "Приказ Министра энергетики РК от 28.05.2021 № 183"),
        ("ГОСТ_17_4_3_02-85__СТ_СЭВ_4471-84_.doc", "ГОСТ 17.4.3.02-85 Охрана почв"),
        ("ГОСТ_17_5_3_06-85.doc", "ГОСТ 17.5.3.06-85 Рекультивация земель"),
        ("СТ_РК_1513-2019.doc", "СТ РК 1513-2019"),
    ]
    seen = set()
    total = 0
    for file_name, law_name in files_to_parse:
        if not os.path.exists(file_name):
            print(f"Файл не найден: {file_name}")
            continue
        print(f"Читаем: {file_name}...")
        text = read_docx(file_name)
        if not text:
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
    return jsonify(search_laws_in_db(query))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))