import os
import sqlite3
import re
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

def parse_and_save_to_db():
    db_path = "laws_database.db"
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
    cursor.execute("SELECT COUNT(*) FROM laws")
    count = cursor.fetchone()[0]
    if count > 10:
        connection.close()
        return
    files_to_parse = [
        ("ecocode.txt", "Экологический кодекс РК"),
        ("ecocode (1).txt", "Экологический кодекс РК (Часть 2)"),
        ("koap_final.txt", "КоАП РК"),
        ("nedra.txt", "Закон о Недрах и недропользовании"),
        ("sanpin1.txt", "Санитарные правила и нормы (Часть 1)"),
        ("sanpin2.txt", "Санитарные правила и нормы (Часть 2)"),
        ("atom.txt", "Закон об использовании атомной энергии")
    ]
    total = 0
    for file_name, law_name in files_to_parse:
        if not os.path.exists(file_name):
            continue
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
                cursor.execute("INSERT INTO laws (law_name, article_num, text_content) VALUES (?, ?, ?)",
                    (law_name, article_num, text_content))
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
