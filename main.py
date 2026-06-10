import os
import sqlite3
import json
import urllib.request
from flask import Flask, render_template_string, request, jsonify, Response, send_file

app = Flask(__name__)

DB_PATH = "laws_database.db"
DB_URL = "https://github.com/marat1375-design/law-app/raw/main/laws_database.db"

def download_db():
    if not os.path.exists(DB_PATH):
        print("База не найдена — скачиваем с GitHub...")
        urllib.request.urlretrieve(DB_URL, DB_PATH)
        print(f"База скачана! Размер: {os.path.getsize(DB_PATH)} байт")
    else:
        print(f"База уже есть. Размер: {os.path.getsize(DB_PATH)} байт")

download_db()

def search_laws_in_db(query, law_filter=""):
    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()
    query_lower = query.lower()
    words = query_lower.split()
    results = []
    seen_ids = set()

    def build_query(word_list):
        parts = [f"LOWER(text_content) LIKE ?" for _ in word_list]
        params = [f"%{w}%" for w in word_list]
        if law_filter:
            parts.append("LOWER(law_name) LIKE ?")
            params.append(f"%{law_filter.lower()}%")
        return " AND ".join(parts), params

    like_clause, params = build_query(words)
    cursor.execute(f"""
        SELECT rowid, law_name, article_num, text_content
        FROM laws
        WHERE {like_clause}
        LIMIT 50
    """, params)
    for row in cursor.fetchall():
        rowid, law_name, article_num, text_content = row
        score = sum(text_content.lower().count(w) for w in words) * 2
        results.append({
            "law_name": law_name,
            "article_num": article_num,
            "text_content": text_content,
            "score": score
        })
        seen_ids.add(rowid)

    words_root = [w[:-2] if len(w) > 5 else w for w in words]
    if words_root != words:
        like_clause2, params2 = build_query(words_root)
        cursor.execute(f"""
            SELECT rowid, law_name, article_num, text_content
            FROM laws
            WHERE {like_clause2}
            LIMIT 50
        """, params2)
        for row in cursor.fetchall():
            rowid, law_name, article_num, text_content = row
            if rowid not in seen_ids:
                score = sum(text_content.lower().count(w) for w in words_root)
                results.append({
                    "law_name": law_name,
                    "article_num": article_num,
                    "text_content": text_content,
                    "score": score
                })
                seen_ids.add(rowid)

    connection.close()
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:50]

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
    law_filter = request.args.get("law", "")
    if not query:
        return jsonify([])
    return Response(
        json.dumps(search_laws_in_db(query, law_filter), ensure_ascii=False),
        mimetype='application/json; charset=utf-8'
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))