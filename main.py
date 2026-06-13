import os
import re
import sqlite3
import json
import urllib.request
import anthropic
from flask import Flask, render_template_string, request, jsonify, Response, send_file, stream_with_context

try:
    import pymorphy3 as _pymorphy3
    _morph = _pymorphy3.MorphAnalyzer()
    print("pymorphy3 ready")
except ImportError:
    _morph = None
    print("pymorphy3 not available — using truncation stemmer")

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
    """Morphological stem for Russian via truncation.
    5-char words lose last char to strip case endings: нефти→нефт, землю→земл, почвы→почв."""
    if len(w) >= 9: return w[:6]
    if len(w) >= 6: return w[:5]
    if len(w) == 5: return w[:4]
    return w


def _lemma(w):
    """Return dictionary (base) form of a Russian word via pymorphy3.
    Falls back to _stem() when pymorphy3 is unavailable.
    Lemmatising before stemming gives better FTS prefixes:
      касок → каска → каск* (catches каска/каски/касок)
      vs raw  касок → касо* (only catches касок).
    """
    if _morph:
        p = _morph.parse(w)
        if p:
            return p[0].normal_form
    return _stem(w)


# Prepositions, conjunctions, pronouns that add noise to multi-word queries.
_STOP_WORDS = frozenset({
    "на", "в", "во", "из", "к", "ко", "с", "со", "по", "за", "о", "об",
    "при", "от", "до", "под", "над", "без", "у", "для", "а", "и", "но",
    "или", "что", "как", "это", "не", "нет", "же", "ли", "бы", "то", "ни",
    "их", "им", "его", "её", "он", "она", "они", "мы", "вы", "я",
})

# Detects "Статья 44", "ст. 44", "ст.44-1" in queries for direct article lookup.
_ARTICLE_NUM_RE = re.compile(
    r'\b(?:стать[яию]|ст\.?)\s*(\d+(?:[-\.]\d+)?)',
    re.IGNORECASE
)

_EXPAND_MAP: dict[str, list[str]] = {
    # Colloquial → legal: зарплата → заработная плата (zero overlap in legal texts)
    "зарплата":  ["заработная", "плата"],
    "зарплаты":  ["заработная", "плата"],
    "зарплате":  ["заработная", "плата"],
    "зарплату":  ["заработная", "плата"],
    "зарплатой": ["заработная", "плата"],
    "зарплатам": ["заработная", "плата"],
    "зарплат":   ["заработная", "плата"],
    # Abbreviations → full law name fragments (for article-num search like "ст. 44 УПК")
    "упк": ["уголовно-процессуальный", "кодекс"],
    "гк":  ["гражданский", "кодекс"],
    "ук":  ["уголовный", "кодекс"],
    "тк":  ["трудовой", "кодекс"],
    "нк":  ["налоговый", "кодекс"],
    "пк":  ["предпринимательский", "кодекс"],
    "экк": ["экологический", "кодекс"],
}


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
    """Expand query with legally equivalent terms from RK legislation.

    Bridges colloquial descriptions to statutory language across all legal
    domains (ecology, labor, civil, land, tax, admin), e.g.:
      'разлив нефти на землю' → 'загрязнение земли опасными'
      'увольнение беременной' → 'расторжение трудового договора'
    """
    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            system=(
                "Задача: для поискового запроса верни 2 ключевых юридических слова "
                "из законодательства РК, которыми это понятие описывается в текстах законов. "
                "Ответ — только 2 слова через пробел, без пояснений. "
                "Примеры: "
                "'разлив нефти на землю' → 'загрязнение земли'; "
                "'не выдали зарплату' → 'заработная плата'; "
                "'штраф за нет касок' → 'охрана труда'."
            ),
            messages=[{"role": "user", "content": query}]
        )
        result = msg.content[0].text.strip()
        # Reject refusals: valid keywords are ≤5 words with no sentence punctuation
        words = result.split()
        if len(words) > 5 or any(c in result for c in '.!?:;') or (words and words[0].lower() == 'я'):
            return None
        return result
    except Exception:
        return None


def _score(text, article_num, law_name, words, exact=True):
    char_count = max(len(text), 1)
    text_lower = text.lower()
    law_l = law_name.lower()
    hits = sum(text_lower.count(_stem(w)) for w in words)
    # Law-name keyword bonus: when query keywords appear in the law's name itself,
    # it's a strong signal that this law specifically covers the topic.
    # Example: "права потребителя" → "потреб" in "О защите прав потребителей" → +2
    # Example: "ст.44 КоАП" → "коап" in "КоАП РК" → +2 (disambiguates among ст.44 across laws)
    law_name_hits = sum(2.0 for w in words if len(w) >= 3 and _stem(w) in law_l)
    if hits == 0 and law_name_hits == 0:
        return 0
    k1, b, avgdl = 1.5, 0.75, 8000
    tf = hits * (k1 + 1) / (hits + k1 * (1 - b + b * char_count / avgdl)) if hits else 0
    title_words = re.findall(r'[а-яёa-z]+', article_num.lower())
    title = sum(4 for w in words
                if any(tw.startswith(_stem(w)) and len(tw) <= len(_stem(w)) + 5
                       for tw in title_words))
    hier = (0.5 if 'конституция' in law_l else
            0.4 if 'кодекс' in law_l or 'коап' in law_l else
            0.3 if 'закон' in law_l else
            0.2 if 'правила' in law_l else
            0.1 if 'приказ' in law_l else 0)
    return (tf + title + law_name_hits + hier) * (1 if exact else 0.6)


def search_laws_in_db(query, law_filter="", page=1, fetch_all=False):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Detect article-number pattern before expansion/lemmatization.
    # Removes the matched span (e.g., "ст. 44" / "Статья 134-2") from the
    # query so remaining words become regular keyword filters.
    art_match = _ARTICLE_NUM_RE.search(query)
    art_prefix = None
    if art_match:
        art_prefix = f'Статья {art_match.group(1)}'
        clean_query = query[:art_match.start()] + query[art_match.end():]
        all_words = [w for w in clean_query.lower().split() if w]
    else:
        all_words = [w for w in query.lower().split() if w]

    expanded = []
    for w in all_words:
        expanded.extend(_EXPAND_MAP.get(w, [w]))
    all_words = expanded

    # Keywords: stop-words and single-char tokens removed, then lemmatised.
    # Lemmatising before stemming gives better FTS prefixes, e.g.:
    #   касок → каска → каск* (catches каска/каски/каской/касок)
    #   нефти → нефть → нефт* (same breadth, cleaner root)
    kw_raw = [w for w in all_words if w not in _STOP_WORDS and len(w) >= 3]
    if not kw_raw:
        kw_raw = [w for w in all_words if len(w) >= 2]  # last resort
    kw = [_lemma(w) for w in kw_raw]

    results = []
    seen_ids = set()

    law_clause = 'AND LOWER(law_name) LIKE ?' if law_filter else ''
    law_param = [f'%{law_filter.lower()}%'] if law_filter else []

    rows = []
    seen_rowids = set()
    and_count = 0
    and_rowids: set[int] = set()

    if _fts_ready and kw:
        # AND: every keyword stem must appear in the document
        fts_and = ' '.join(_stem(w) + '*' for w in kw)
        try:
            cur.execute(f"""
                SELECT rowid, law_name, article_num, text_content
                FROM laws_fts WHERE laws_fts MATCH ?
                {law_clause} LIMIT {MAX_RESULTS}
            """, [fts_and] + law_param)
            for row in cur.fetchall():
                if row[0] not in seen_rowids:
                    rows.append(row)
                    seen_rowids.add(row[0])
            and_count = len(rows)
            and_rowids = {row[0] for row in rows}
        except Exception as e:
            print(f"FTS AND error: {e}")

        # OR supplement: run when AND returned very few results.
        # Threshold=5: catches "увольнение беременной" (AND→3 non-TK docs, ст.54
        # has "береме" but not "увольн" so AND missed it).
        # Does NOT fire for "разлив нефти" (AND→6) where OR would flood results
        # with hundreds of Земельный кодекс land-tenure articles via "земл*".
        if len(rows) < 5 and len(kw) > 1:
            fts_or = ' OR '.join(_stem(w) + '*' for w in kw)
            try:
                cur.execute(f"""
                    SELECT rowid, law_name, article_num, text_content
                    FROM laws_fts WHERE laws_fts MATCH ?
                    {law_clause} LIMIT {MAX_RESULTS}
                """, [fts_or] + law_param)
                for row in cur.fetchall():
                    if row[0] not in seen_rowids:
                        rows.append(row)
                        seen_rowids.add(row[0])
            except Exception as e:
                print(f"FTS OR error: {e}")

    # LIKE fallback (FTS unavailable)
    if not rows and kw:
        stems = [_stem(w) for w in kw]
        extra_parts = ["LOWER(law_name) LIKE ?"] if law_filter else []
        extra_params = law_param[:]

        # AND
        parts = [f"LOWER(text_content) LIKE ?" for _ in stems] + extra_parts
        params = [f"%{s}%" for s in stems] + extra_params
        cur.execute(f"""
            SELECT rowid, law_name, article_num, text_content
            FROM laws WHERE {' AND '.join(parts)} LIMIT {MAX_RESULTS}
        """, params)
        for row in cur.fetchall():
            if row[0] not in seen_rowids:
                rows.append(row)
                seen_rowids.add(row[0])
        and_count = len(rows)
        and_rowids = {row[0] for row in rows}

        # OR supplement: same threshold as FTS path
        if len(rows) < 5 and len(stems) > 1:
            or_parts = [f"LOWER(text_content) LIKE ?" for _ in stems] + extra_parts
            or_params = [f"%{s}%" for s in stems] + extra_params
            cur.execute(f"""
                SELECT rowid, law_name, article_num, text_content
                FROM laws WHERE {' OR '.join(or_parts)} LIMIT {MAX_RESULTS}
            """, or_params)
            for row in cur.fetchall():
                if row[0] not in seen_rowids:
                    rows.append(row)
                    seen_rowids.add(row[0])

    # Article-number direct lookup: if query contained "ст. N" or "Статья N",
    # fetch those rows and mark as exact hits so they score highest.
    # Precise suffix matching prevents "Статья 44" matching "Статья 442".
    if art_prefix:
        p = art_prefix
        cur.execute(f"""
            SELECT rowid, law_name, article_num, text_content FROM laws
            WHERE (article_num LIKE ? OR article_num LIKE ? OR
                   article_num LIKE ? OR article_num = ?) {law_clause}
            LIMIT 50
        """, [f'{p}.%', f'{p}-%', f'{p} %', p] + law_param)
        for row in cur.fetchall():
            if row[0] not in seen_rowids:
                rows.insert(0, row)
                seen_rowids.add(row[0])
                and_rowids.add(row[0])

    def _is_art_match(article_num):
        if not art_prefix or not article_num.startswith(art_prefix):
            return False
        rest = article_num[len(art_prefix):]
        return rest == '' or rest[0] in '.– -'

    for rowid, law_name, article_num, text_content in rows:
        score = _score(text_content, article_num, law_name, kw,
                       exact=(rowid in and_rowids))
        if _is_art_match(article_num):
            score += 50.0
        results.append({
            "law_name": law_name,
            "article_num": article_num,
            "text_content": text_content,
            "score": score
        })

    conn.close()
    results.sort(key=lambda x: x["score"], reverse=True)
    total = len(results)
    if fetch_all:
        return {"total": total, "items": results, "and_total": and_count, "kw": kw}
    offset = (page - 1) * PER_PAGE
    return {"total": total, "items": results[offset:offset + PER_PAGE], "and_total": and_count, "kw": kw}


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


_stats_cache: dict | None = None

@app.route("/api/stats")
def stats_api():
    global _stats_cache
    if _stats_cache is None:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT law_name) FROM laws")
        articles, laws = cur.fetchone()
        conn.close()
        _stats_cache = {"articles": articles, "laws": laws}
    return Response(
        json.dumps(_stats_cache, ensure_ascii=False),
        mimetype="application/json; charset=utf-8"
    )


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

    # AI expansion: fires when keyword search alone is insufficient.
    # Threshold = 10: catches semantic mismatches where article text uses
    # different terminology than the query (e.g. "разлив нефти на землю" →
    # ст.337 КоАП says "загрязнение опасными химическими", no "нефть").
    # "увольнение беременной" returns 52 via OR → well above threshold, no AI.
    meaningful_words = [w for w in query.lower().split()
                        if w not in _STOP_WORDS and len(w) >= 3]
    and_total = data.get("and_total", 1)
    # Fire AI when: too few results OR AND is near-empty (query needs semantic expansion).
    # and_total <= 1: with pymorphy3, "касок"→"каск*" now gets and_total=1 instead of 0,
    # but 1 AND result still drowns in OR-flood (e.g. 201 штраф articles from КоАП/НК).
    if (data["total"] < 10 or and_total <= 1) and len(meaningful_words) >= 2:
        synonyms = _ai_synonyms(query)
        if synonyms:
            syn_data = search_laws_in_db(synonyms, law_filter, 1, fetch_all=True)
            if syn_data["total"] > 0:
                corrected_query = synonyms
                if data["total"] < 4 or and_total <= 1:
                    # Very few original results or AND near-miss → use synonyms outright.
                    # and_total <= 1: pymorphy3 "касок"→"каск*" gets and_total=1, but
                    # 201 OR штраф articles still drown it; AI REPLACE produces cleaner results.
                    data = syn_data
                else:
                    # Re-score AI results against original query keywords.
                    # This filters out SanPiN/regulatory articles that have
                    # high BM25 for the AI terms but zero relevance to the
                    # original query (e.g. "загрязнение химическими" appears
                    # 30+ times in ДСМ sanitary rules, drowning out ст.337).
                    # ст.337 has "земл"×4 from "разлив нефти на ЗЕМЛЮ" → passes.
                    orig_kw = [_lemma(w) for w in query.lower().split()
                               if w not in _STOP_WORDS and len(w) >= 3]
                    seen = {(i["law_name"], i["article_num"]) for i in data["items"]}
                    new_items = []
                    for item in syn_data["items"]:
                        if (item["law_name"], item["article_num"]) in seen:
                            continue
                        rs = _score(item["text_content"], item["article_num"],
                                    item["law_name"], orig_kw)
                        if rs > 0:
                            item["score"] = rs
                            new_items.append(item)
                    data["items"].extend(new_items)
                    data["items"].sort(key=lambda x: x["score"], reverse=True)
                    data["items"] = data["items"][:PER_PAGE]
                    data["total"] += len(new_items)

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


_explain_cache: dict[tuple, str] = {}

@app.route("/api/explain", methods=["POST"])
def explain_api():
    data = request.get_json()
    article_text = data.get("text", "")
    law_name = data.get("law_name", "")
    article_num = data.get("article_num", "")
    if not article_text:
        return jsonify({"error": "Текст не передан"}), 400

    cache_key = (law_name, article_num)
    if cache_key in _explain_cache:
        return Response(_explain_cache[cache_key], mimetype="text/plain; charset=utf-8")

    client = anthropic.Anthropic()
    header = f"{law_name}, {article_num}\n\n" if law_name or article_num else ""

    def generate():
        chunks = []
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
                    chunks.append(text)
                    yield text
        except Exception as e:
            yield f"[Ошибка: {e}]"
        else:
            _explain_cache[cache_key] = "".join(chunks)

    return Response(stream_with_context(generate()), mimetype="text/plain; charset=utf-8")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
