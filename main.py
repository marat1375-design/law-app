import os
import sqlite3
import json
import urllib.request
import anthropic
from flask import Flask, render_template_string, request, jsonify, Response, send_file, stream_with_context

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

PER_PAGE = 10
MAX_RESULTS = 200

def _score(text, article_num, law_name, words, exact=True):
    char_count = max(len(text), 1)
    hits = sum(text.lower().count(w) for w in words)
    tf = hits / char_count * 10000          # hits per 10k chars
    title = sum(3 for w in words if w in article_num.lower())
    law_l = law_name.lower()
    hier = (5 if 'конституция' in law_l else
            4 if 'кодекс' in law_l else
            3 if 'закон' in law_l else
            2 if 'правила' in law_l else
            1 if 'приказ' in law_l else 0)
    return (tf + title + hier) * (1 if exact else 0.6)

def search_laws_in_db(query, law_filter="", page=1):
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
        LIMIT {MAX_RESULTS}
    """, params)
    for row in cursor.fetchall():
        rowid, law_name, article_num, text_content = row
        score = _score(text_content, article_num, law_name, words, exact=True)
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
            LIMIT {MAX_RESULTS}
        """, params2)
        for row in cursor.fetchall():
            rowid, law_name, article_num, text_content = row
            if rowid not in seen_ids:
                score = _score(text_content, article_num, law_name, words_root, exact=False)
                results.append({
                    "law_name": law_name,
                    "article_num": article_num,
                    "text_content": text_content,
                    "score": score
                })
                seen_ids.add(rowid)

    connection.close()
    results.sort(key=lambda x: x["score"], reverse=True)
    total = len(results)
    offset = (page - 1) * PER_PAGE
    return {"total": total, "items": results[offset:offset + PER_PAGE]}

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
    page = max(1, int(request.args.get("page", 1)))
    if not query:
        return jsonify({"total": 0, "items": []})
    return Response(
        json.dumps(search_laws_in_db(query, law_filter, page), ensure_ascii=False),
        mimetype='application/json; charset=utf-8'
    )

@app.route("/api/reformulate", methods=["POST"])
def reformulate_api():
    data = request.get_json()
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "Вопрос не передан"}), 400

    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=64,
        system=(
            "Ты — помощник по казахстанскому законодательству. "
            "Из вопроса пользователя извлеки 2–4 ключевых юридических слова на русском языке "
            "для поиска в базе законов РК. "
            "Верни только слова через пробел, без знаков препинания, без объяснений."
        ),
        messages=[{"role": "user", "content": question}]
    )
    keywords = message.content[0].text.strip()
    return jsonify({"keywords": keywords})

@app.route("/api/explain", methods=["POST"])
def explain_api():
    data = request.get_json()
    article_text = data.get("text", "")
    law_name = data.get("law_name", "")
    article_num = data.get("article_num", "")
    if not article_text:
        return jsonify({"error": "Текст не передан"}), 400

    client = anthropic.Anthropic()
    header = f"{law_name}, {article_num}\n\n" if law_name or article_num else ""

    def generate():
        try:
            with client.messages.stream(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=(
                    "Ты — помощник по казахстанскому законодательству. "
                    "Объясняй статьи простым и понятным языком. "
                    "Отвечай только на русском языке. "
                    "Будь кратким — 3–5 предложений."
                ),
                messages=[{
                    "role": "user",
                    "content": f"Объясни простыми словами:\n\n{header}{article_text[:4000]}"
                }]
            ) as stream:
                for text in stream.text_stream:
                    yield text
        except Exception as e:
            yield f"[Ошибка: {e}]"

    return Response(stream_with_context(generate()), mimetype="text/plain; charset=utf-8")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))