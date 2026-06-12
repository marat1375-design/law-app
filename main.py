import os
import re
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


def _stem(w):
    """First 5–6 chars as morphological stem for Russian."""
    if len(w) >= 9: return w[:6]
    if len(w) >= 6: return w[:5]
    return w


_fts_ready = False

def setup_fts():
    global _fts_ready
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='laws_fts'")
        if not cur.fetchone():
            cur.execute("""
                CREATE VIRTUAL TABLE laws_fts USING fts5(
                    law_name, article_num, text_content,
                    content='laws', content_rowid='rowid',
                    tokenize='unicode61'
                )
            """)
            cur.execute("INSERT INTO laws_fts(laws_fts) VALUES('rebuild')")
            conn.commit()
            print("FTS5 index built")
        conn.close()
        _fts_ready = True
        print("FTS5 ready")
    except Exception as e:
        print(f"FTS5 unavailable: {e}")


download_db()
setup_fts()

PER_PAGE = 10
MAX_RESULTS = 200

def _ai_synonyms(query):
    """Ask AI for synonyms/related legal terms when query finds nothing."""
    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=30,
            system=(
                "Ты — помощник по законодательству РК. "
                "Дай 2–3 русских синонима или близких юридических термина для слова/фразы. "
                "Только отдельные существительные через пробел. Без объяснений."
            ),
            messages=[{"role": "user", "content": query}]
        )
        return msg.content[0].text.strip()
    except Exception:
        return None


def _score(text, article_num, law_name, words, exact=True):
    char_count = max(len(text), 1)
    text_lower = text.lower()
    hits = sum(text_lower.count(_stem(w)) for w in words)
    if hits == 0:
        return 0
    k1, b, avgdl = 1.5, 0.75, 5000
    tf = hits * (k1 + 1) / (hits + k1 * (1 - b + b * char_count / avgdl))
    title = sum(4 for w in words if _stem(w) in article_num.lower())
    law_l = law_name.lower()
    hier = (0.5 if 'конституция' in law_l else
            0.4 if 'кодекс' in law_l else
            0.3 if 'закон' in law_l else
            0.2 if 'правила' in law_l else
            0.1 if 'приказ' in law_l else 0)
    return (tf + title + hier) * (1 if exact else 0.6)


def search_laws_in_db(query, law_filter="", page=1):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    words = [w for w in query.lower().split() if w]
    results = []
    seen_ids = set()

    law_clause = 'AND LOWER(law_name) LIKE ?' if law_filter else ''
    law_param = [f'%{law_filter.lower()}%'] if law_filter else []

    rows = []

    if _fts_ready and words:
        # AND search — all stems must appear
        fts_and = ' '.join(_stem(w) + '*' for w in words)
        try:
            cur.execute(f"""
                SELECT rowid, law_name, article_num, text_content
                FROM laws_fts WHERE laws_fts MATCH ?
                {law_clause} LIMIT {MAX_RESULTS}
            """, [fts_and] + law_param)
            rows = cur.fetchall()
        except Exception as e:
            print(f"FTS AND error: {e}")

        # OR fallback for multi-word queries
        if not rows and len(words) > 1:
            fts_or = ' OR '.join(_stem(w) + '*' for w in words)
            try:
                cur.execute(f"""
                    SELECT rowid, law_name, article_num, text_content
                    FROM laws_fts WHERE laws_fts MATCH ?
                    {law_clause} LIMIT {MAX_RESULTS}
                """, [fts_or] + law_param)
                rows = cur.fetchall()
            except Exception as e:
                print(f"FTS OR error: {e}")

    # LIKE fallback (if FTS unavailable or empty)
    if not rows and words:
        stems = [_stem(w) for w in words]
        parts = [f"LOWER(text_content) LIKE ?" for _ in stems]
        params = [f"%{s}%" for s in stems] + law_param
        if law_filter:
            parts.append("LOWER(law_name) LIKE ?")
        cur.execute(f"""
            SELECT rowid, law_name, article_num, text_content
            FROM laws WHERE {' AND '.join(parts)} LIMIT {MAX_RESULTS}
        """, params)
        rows = cur.fetchall()

    for rowid, law_name, article_num, text_content in rows:
        if rowid not in seen_ids:
            score = _score(text_content, article_num, law_name, words)
            results.append({
                "law_name": law_name,
                "article_num": article_num,
                "text_content": text_content,
                "score": score
            })
            seen_ids.add(rowid)

    conn.close()
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


@app.route("/api/suggest")
def suggest_api():
    q = request.args.get("q", "").strip().lower()
    if len(q) < 2:
        return jsonify([])
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT law_name FROM laws
        WHERE LOWER(law_name) LIKE ?
        ORDER BY law_name LIMIT 6
    """, (f"%{q}%",))
    results = [r[0] for r in cur.fetchall()]
    conn.close()
    return Response(
        json.dumps(results, ensure_ascii=False),
        mimetype='application/json; charset=utf-8'
    )


@app.route("/api/search")
def search_api():
    query = request.args.get("q", "")
    law_filter = request.args.get("law", "")
    page = max(1, int(request.args.get("page", 1)))
    if not query:
        return jsonify({"total": 0, "items": []})

    data = search_laws_in_db(query, law_filter, page)
    corrected_query = None

    if data["total"] == 0:
        synonyms = _ai_synonyms(query)
        if synonyms:
            syn_data = search_laws_in_db(synonyms, law_filter, page)
            if syn_data["total"] > 0:
                data = syn_data
                corrected_query = synonyms

    data["corrected_query"] = corrected_query
    return Response(
        json.dumps(data, ensure_ascii=False),
        mimetype='application/json; charset=utf-8'
    )


@app.route("/api/check-updates")
def check_updates_api():
    token = request.args.get("token", "")
    expected = os.environ.get("CHECK_TOKEN", "")
    if expected and token != expected:
        return jsonify({"error": "Unauthorized"}), 401
    from check_updates import run_check
    report = run_check()
    return Response(
        json.dumps(report, ensure_ascii=False),
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
        max_tokens=20,
        system=(
            "Извлеки 1–2 главных слова из вопроса для поиска в базе законов РК. "
            "Только существительные в именительном падеже, одиночные слова, без предлогов и союзов. "
            "Примеры: 'увольнение беременность', 'штраф нарушение', 'отпуск работник'. "
            "Верни только слова через пробел. Никаких пояснений."
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
