import os
import sqlite3
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

# Быстрая функция поиска по готовой базе данных
def search_laws_in_db(query):
    connection = sqlite3.connect("laws_database.db")
    cursor = connection.cursor()
    
    # Извлекаем все статьи из базы
    cursor.execute("SELECT law_name, article_num, text_content FROM laws")
    rows = cursor.fetchall()
    connection.close()
    
    query_lower = query.lower()
    results = []
    
    # Умный поиск на Python (игнорирует большие/маленькие буквы)
    for row in rows:
        law_name, article_num, text_content = row[0], row[1], row[2]
        if query_lower in law_name.lower() or query_lower in text_content.lower():
            results.append({
                "law_name": law_name,
                "article_num": article_num,
                "text_content": text_content
            })
    return results

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
    print("Веб-сервер справочника запускается...")
    print("Адрес для браузера: http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))