"""
AMAN MUSIC API v2.1  -  Advanced Premium Music API
Owner : AMAN

Local run  : pip install -r requirements.txt && python app.py
Production : gunicorn app:app --workers 1 --threads 8 --timeout 60 --bind 0.0.0.0:$PORT

Environment variables
  ADMIN_KEY   (recommended) secret for /admin/* endpoints. Set your own on the server.
  JAMENDO_ID  (optional)  free client id from developer.jamendo.com -> full-length songs
  KEYS_JSON   (optional)  keys that must survive server restarts (see README)
  DB_FILE     (optional)  sqlite path, default aman_api.db (point it to a persistent disk)
  PORT        (optional)  default 5000

Sources: Audius (full songs), Jamendo (full songs), iTunes + Deezer (30 sec previews)
"""
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import wraps

import requests
from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException, NotFound

OWNER = "AMAN"
APP_NAME = "AMAN_MUSIC_API"
VERSION = "2.2"
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")     # must be set on the server; admin stays off without it
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "your-email@example.com")
JAMENDO_ID = os.environ.get("JAMENDO_ID", "")
YOUTUBE_KEY = os.environ.get("YOUTUBE_API_KEY", "")   # free: Google Cloud -> YouTube Data API v3
# On Vercel only /tmp is writable (and it is wiped), so keep the DB there.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.environ.get("DB_FILE") or (
    "/tmp/aman_api.db" if os.environ.get("VERCEL") else os.path.join(BASE_DIR, "aman_api.db"))
HTTP_TIMEOUT = 8
CACHE_TTL = 300
MAX_PLAYLIST_TRACKS = 500

# Keys that are always present (re-created on every start if the DB was wiped).
# (key, name, plan, days)
# Optional: set BUILTIN_KEY in the server environment to always have one ultra key.
# (Never write real keys or passwords in the code that goes to GitHub.)
BUILTIN_KEYS = []
if os.environ.get("BUILTIN_KEY"):
    BUILTIN_KEYS.append((os.environ["BUILTIN_KEY"], "owner", "ultra", 3650))

PLANS = {
    "free":    {"rpm": 30,   "max_results": 10, "sources": ["audius", "itunes", "deezer", "animethemes"],
                "playlists": 1,   "favorites": 20},
    "premium": {"rpm": 300,  "max_results": 30, "sources": ["audius", "jamendo", "itunes", "deezer", "animethemes", "youtube"],
                "playlists": 20,  "favorites": 500},
    "ultra":   {"rpm": 1000, "max_results": 100, "sources": ["audius", "jamendo", "itunes", "deezer", "animethemes", "youtube"],
                "playlists": 200, "favorites": 5000},
}

# ---------------- languages ----------------
LANGUAGES = {
    "hindi": {"hint": "hindi", "country": "IN", "code": "hi"},
    "bhojpuri": {"hint": "bhojpuri", "country": "IN", "code": "hi"},
    "punjabi": {"hint": "punjabi", "country": "IN", "code": "pa"},
    "haryanvi": {"hint": "haryanvi", "country": "IN", "code": "hi"},
    "rajasthani": {"hint": "rajasthani", "country": "IN", "code": "hi"},
    "odia": {"hint": "odia", "country": "IN", "code": "or"},
    "assamese": {"hint": "assamese", "country": "IN", "code": "as"},
    "bengali": {"hint": "bengali", "country": "IN", "code": "bn"},
    "marathi": {"hint": "marathi", "country": "IN", "code": "mr"},
    "gujarati": {"hint": "gujarati", "country": "IN", "code": "gu"},
    "tamil": {"hint": "tamil", "country": "IN", "code": "ta"},
    "telugu": {"hint": "telugu", "country": "IN", "code": "te"},
    "kannada": {"hint": "kannada", "country": "IN", "code": "kn"},
    "malayalam": {"hint": "malayalam", "country": "IN", "code": "ml"},
    "urdu": {"hint": "urdu", "country": "IN", "code": "ur"},
    "nepali": {"hint": "nepali", "country": "NP", "code": "ne"},
    "sinhala": {"hint": "sinhala", "country": "LK", "code": "si"},
    "english": {"hint": "", "country": "US", "code": "en"},
    "spanish": {"hint": "spanish", "country": "ES", "code": "es"},
    "french": {"hint": "french", "country": "FR", "code": "fr"},
    "german": {"hint": "german", "country": "DE", "code": "de"},
    "italian": {"hint": "italian", "country": "IT", "code": "it"},
    "portuguese": {"hint": "portuguese", "country": "BR", "code": "pt"},
    "dutch": {"hint": "dutch", "country": "NL", "code": "nl"},
    "polish": {"hint": "polish", "country": "PL", "code": "pl"},
    "russian": {"hint": "russian", "country": "RU", "code": "ru"},
    "ukrainian": {"hint": "ukrainian", "country": "UA", "code": "uk"},
    "turkish": {"hint": "turkish", "country": "TR", "code": "tr"},
    "greek": {"hint": "greek", "country": "GR", "code": "el"},
    "arabic": {"hint": "arabic", "country": "AE", "code": "ar"},
    "persian": {"hint": "persian", "country": "IR", "code": "fa"},
    "hebrew": {"hint": "hebrew", "country": "IL", "code": "he"},
    "swahili": {"hint": "swahili", "country": "KE", "code": "sw"},
    "indonesian": {"hint": "indonesian", "country": "ID", "code": "id"},
    "malay": {"hint": "malay", "country": "MY", "code": "ms"},
    "filipino": {"hint": "filipino opm", "country": "PH", "code": "tl"},
    "thai": {"hint": "thai", "country": "TH", "code": "th"},
    "vietnamese": {"hint": "vietnamese", "country": "VN", "code": "vi"},
    "chinese": {"hint": "mandopop chinese", "country": "CN", "code": "zh"},
    "cantonese": {"hint": "cantopop", "country": "HK", "code": "zh"},
    "korean": {"hint": "k-pop", "country": "KR", "code": "ko"},
    "japanese": {"hint": "j-pop", "country": "JP", "code": "ja"},
    "anime": {"hint": "anime", "country": "JP", "code": "ja"},
}

# API message language: ?msg=en | hi | hinglish
MSG = {
    "en": {
        "need_key": "Invalid or missing API key",
        "expired": "API key expired",
        "limit": "Rate limit reached ({n}/min)",
        "need_q": "Parameter 'q' is required",
        "need_id": "Parameter 'id' is required",
        "need_name": "Parameter 'name' is required",
        "not_found": "Not found",
        "plan_lock": "This feature needs a higher plan",
        "bad_lang": "Unsupported language",
        "bad_source": "Unknown source",
        "full": "Limit reached for your plan",
        "upstream": "Music source is not responding, try again",
    },
    "hi": {
        "need_key": "API key galat hai ya nahi di gayi",
        "expired": "API key ki samay seema khatam ho gayi",
        "limit": "Rate limit poori ho gayi ({n}/min)",
        "need_q": "'q' dena zaroori hai",
        "need_id": "'id' dena zaroori hai",
        "need_name": "'name' dena zaroori hai",
        "not_found": "Nahi mila",
        "plan_lock": "Ye feature upar ke plan mein milta hai",
        "bad_lang": "Ye bhasha supported nahi hai",
        "bad_source": "Ye source maujood nahi hai",
        "full": "Aapke plan ki limit poori ho gayi",
        "upstream": "Music source jawab nahi de raha, dobara try karo",
    },
}
MSG["hinglish"] = MSG["hi"]

MOODS = {
    "happy": "happy upbeat", "sad": "sad emotional", "love": "romantic love",
    "party": "party dance", "chill": "chill lofi", "workout": "workout gym",
    "devotional": "devotional bhajan", "focus": "instrumental focus",
}
GENRES = ["pop", "rock", "hiphop", "electronic", "jazz", "classical", "lofi",
          "devotional", "folk", "rnb"]

app = Flask(__name__)
app.url_map.strict_slashes = False
try:
    app.json.ensure_ascii = False          # Flask >= 2.2
except AttributeError:
    app.config["JSON_AS_ASCII"] = False

hits = {}
hits_lock = threading.Lock()
cache = {}
cache_lock = threading.Lock()


def t(key, **kw):
    lang = request.args.get("msg", "en")
    return MSG.get(lang, MSG["en"])[key].format(**kw)


# ---------------- database ----------------
def connect():
    c = sqlite3.connect(DB_FILE, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    d = g.pop("db", None)
    if d is not None:
        d.close()


def init_db():
    c = connect()
    try:
        c.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass
    c.executescript("""
    CREATE TABLE IF NOT EXISTS keys(
        key TEXT PRIMARY KEY, name TEXT, plan TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        expires REAL, created REAL, usage INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS playlists(
        id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, name TEXT NOT NULL,
        tracks TEXT NOT NULL DEFAULT '[]', is_fav INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS idx_playlists_key ON playlists(key);
    """)
    c.commit()
    import_builtin_keys(c)
    import_preset_keys(c)
    c.close()


def import_builtin_keys(c):
    """Insert BUILTIN_KEYS if missing. Never overwrites (a revoked key stays revoked)."""
    now = time.time()
    for key, name, plan, days in BUILTIN_KEYS:
        c.execute("INSERT OR IGNORE INTO keys(key,name,plan,expires,created) VALUES(?,?,?,?,?)",
                  (key, name, plan, now + days * 86400, now))
    c.commit()


def import_preset_keys(c):
    """KEYS_JSON = '[{"key":"AMAN-abc","name":"me","plan":"ultra","days":3650}]'
    Inserted only if the key does not exist yet, so restarts never overwrite changes."""
    raw = os.environ.get("KEYS_JSON")
    if not raw:
        return
    try:
        items = json.loads(raw)
    except ValueError:
        print("KEYS_JSON is not valid JSON, ignored")
        return
    if not isinstance(items, list):
        return
    for it in items:
        try:
            plan = it.get("plan", "free")
            if plan not in PLANS:
                continue
            days = int(it.get("days", 3650))
            c.execute(
                "INSERT OR IGNORE INTO keys(key,name,plan,expires,created) VALUES(?,?,?,?,?)",
                (str(it["key"]), str(it.get("name", "user"))[:100], plan,
                 time.time() + days * 86400, time.time()))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    c.commit()


init_db()


# ---------------- helpers ----------------
def reply(data=None, status=200, error=None):
    body = {"owner": OWNER, "api": APP_NAME, "version": VERSION, "success": error is None}
    if error:
        body["error"] = error
    else:
        body["data"] = data
    return jsonify(body), status


@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "X-API-Key, X-Admin-Key, Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.errorhandler(Exception)
def on_error(e):
    if isinstance(e, HTTPException):
        return reply(status=e.code or 500, error=e.name)
    app.logger.exception(e)
    return reply(status=500, error="Internal server error")


def to_int(value, default, lo, hi):
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def params():
    """Query/form values, plus a JSON body if one is sent."""
    d = request.values.to_dict()
    j = request.get_json(silent=True)
    if isinstance(j, dict):
        d.update({k: str(v) for k, v in j.items() if v is not None})
    return d


def allow(key, rpm):
    now = time.time()
    with hits_lock:
        recent = [x for x in hits.get(key, []) if now - x < 60]
        if len(recent) >= rpm:
            hits[key] = recent
            return False
        recent.append(now)
        hits[key] = recent
        if len(hits) > 5000:
            for k in [k for k, v in hits.items() if not v or now - v[-1] >= 60]:
                hits.pop(k, None)
    return True


def require_key(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        key = request.headers.get("X-API-Key") or request.args.get("key")
        row = db().execute("SELECT * FROM keys WHERE key=?", (key,)).fetchone() if key else None
        if not row or not row["active"]:
            return reply(status=401, error=t("need_key"))
        if row["expires"] and time.time() > row["expires"]:
            return reply(status=403, error=t("expired"))
        plan = PLANS.get(row["plan"], PLANS["free"])
        if not allow(key, plan["rpm"]):
            return reply(status=429, error=t("limit", n=plan["rpm"]))
        db().execute("UPDATE keys SET usage=usage+1 WHERE key=?", (key,))
        db().commit()
        g.key, g.plan_name, g.plan = key, row["plan"], plan
        return fn(*a, **kw)
    return wrapper


def require_admin(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not ADMIN_KEY or ADMIN_KEY == "CHANGE_THIS_ADMIN_KEY":
            return reply(status=503, error="ADMIN_KEY is not configured on the server")
        given = request.headers.get("X-Admin-Key", "")
        if not hmac.compare_digest(given.encode(), ADMIN_KEY.encode()):
            return reply(status=401, error="Admin key required")
        return fn(*a, **kw)
    return wrapper


def http_get(url, **kw):
    r = requests.get(url, params=kw, timeout=HTTP_TIMEOUT,
                     headers={"User-Agent": APP_NAME + "/" + VERSION})
    r.raise_for_status()
    return r.json()


def cached(name, args, loader):
    k = (name, json.dumps(args, sort_keys=True, default=str))
    now = time.time()
    with cache_lock:
        hit = cache.get(k)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    data = loader()
    with cache_lock:
        cache[k] = (now, data)
        if len(cache) > 2000:
            for old in sorted(cache, key=lambda x: cache[x][0])[:500]:
                cache.pop(old, None)
    return data


def get_q():
    return request.args.get("q", "").strip()[:200]


def page_arg():
    return to_int(request.args.get("page"), 1, 1, 20)


def limit_arg(default=5):
    return to_int(request.args.get("limit"), default, 1, g.plan["max_results"])


def lang_ok():
    lang = request.args.get("lang")
    return (not lang) or lang.lower() in LANGUAGES


def lang_info(lang):
    if not lang:
        return {"hint": "", "country": "IN", "code": ""}
    return LANGUAGES[lang.lower()]


# ---------------- sources ----------------
def audius_item(x):
    art = x.get("artwork") or {}
    return {
        "source": "audius", "id": "audius:" + str(x["id"]), "title": x.get("title"),
        "artist": (x.get("user") or {}).get("name"), "duration": x.get("duration"),
        "cover": art.get("480x480"),
        "play_url": f"https://api.audius.co/v1/tracks/{x['id']}/stream?app_name={APP_NAME}",
        "type": "full",
    }


def jamendo_item(x):
    return {
        "source": "jamendo", "id": "jamendo:" + str(x["id"]), "title": x.get("name"),
        "artist": x.get("artist_name"), "album": x.get("album_name"),
        "duration": x.get("duration"), "cover": x.get("image"),
        "play_url": x.get("audio"), "type": "full",
    }


def itunes_item(x):
    return {
        "source": "itunes", "id": "itunes:" + str(x["trackId"]), "title": x.get("trackName"),
        "artist": x.get("artistName"), "album": x.get("collectionName"),
        "duration": int((x.get("trackTimeMillis") or 0) / 1000),
        "cover": (x.get("artworkUrl100") or "").replace("100x100", "600x600") or None,
        "play_url": x.get("previewUrl"), "type": "preview_30s",
    }


def deezer_item(x):
    return {
        "source": "deezer", "id": "deezer:" + str(x["id"]), "title": x.get("title"),
        "artist": (x.get("artist") or {}).get("name"),
        "album": (x.get("album") or {}).get("title"),
        "duration": x.get("duration"), "cover": (x.get("album") or {}).get("cover_big"),
        "play_url": x.get("preview"), "type": "preview_30s",
    }


def youtube_item(x):
    vid = x["id"]["videoId"]
    sn = x.get("snippet") or {}
    th = sn.get("thumbnails") or {}
    return {
        "source": "youtube", "id": "youtube:" + vid, "title": sn.get("title"),
        "artist": sn.get("channelTitle"),
        "cover": (th.get("high") or th.get("medium") or th.get("default") or {}).get("url"),
        "play_url": "https://www.youtube.com/watch?v=" + vid,
        "embed_url": "https://www.youtube.com/embed/" + vid,
        "type": "youtube_embed",
    }


def anime_item(x):
    song = x.get("song") or {}
    anime = x.get("anime") or {}
    link = None
    for e in x.get("animethemeentries") or []:
        for v in e.get("videos") or []:
            link = ((v.get("audio") or {}).get("link")) or v.get("link")
            if link:
                break
        if link:
            break
    return {
        "source": "animethemes", "id": "animethemes:" + str(x.get("id")),
        "title": song.get("title") or x.get("slug"),
        "artist": ", ".join(a.get("name", "") for a in (song.get("artists") or [])) or None,
        "album": f"{anime.get('name')} ({x.get('slug')})" if anime.get("name") else None,
        "cover": None, "play_url": link, "type": "full",
    }


def src_youtube(q, limit, li):
    if not YOUTUBE_KEY:
        return []
    j = http_get("https://www.googleapis.com/youtube/v3/search", part="snippet", type="video",
                 videoCategoryId="10", q=q, maxResults=min(limit, 50), key=YOUTUBE_KEY,
                 regionCode=li["country"], **({"relevanceLanguage": li["code"]} if li.get("code") else {}))
    return [youtube_item(x) for x in j.get("items", []) if (x.get("id") or {}).get("videoId")]


def src_animethemes(q, limit, li):
    if q.lower().endswith(" anime"):          # drop the language hint we appended
        q = q[:-6].strip() or q
    j = http_get("https://api.animethemes.moe/search", q=q, **{
        "fields[search]": "animethemes",
        "include[animetheme]": "song.artists,anime,animethemeentries.videos.audio",
        "page[limit][animetheme]": limit})
    items = ((j.get("search") or {}).get("animethemes")) or []
    return [anime_item(x) for x in items if x][:limit]


def src_audius(q, limit, li):
    j = http_get("https://api.audius.co/v1/tracks/search", query=q, app_name=APP_NAME,
                 limit=min(limit, 100))
    return [audius_item(x) for x in j.get("data", [])[:limit]]


def src_jamendo(q, limit, li):
    if not JAMENDO_ID:
        return []
    j = http_get("https://api.jamendo.com/v3.0/tracks/", client_id=JAMENDO_ID, format="json",
                 limit=limit, search=q, audioformat="mp32")
    return [jamendo_item(x) for x in j.get("results", [])]


def src_itunes(q, limit, li):
    j = http_get("https://itunes.apple.com/search", term=q, media="music", limit=limit,
                 country=li["country"])
    return [itunes_item(x) for x in j.get("results", []) if x.get("trackId")]


def src_deezer(q, limit, li):
    j = http_get("https://api.deezer.com/search", q=q, limit=limit)
    return [deezer_item(x) for x in j.get("data", [])]


CREDITS = {
    "audius": "Music via Audius", "jamendo": "Music via Jamendo",
    "itunes": "Preview via Apple iTunes", "deezer": "Preview via Deezer",
    "youtube": "Video via YouTube (play in the YouTube player only)",
    "animethemes": "Anime themes via AnimeThemes",
}


def with_credit(fn):
    def wrapper(x):
        item = fn(x)
        item["credit"] = CREDITS.get(item.get("source"))
        return item
    return wrapper


audius_item = with_credit(audius_item)
jamendo_item = with_credit(jamendo_item)
itunes_item = with_credit(itunes_item)
deezer_item = with_credit(deezer_item)
youtube_item = with_credit(youtube_item)
anime_item = with_credit(anime_item)

SOURCES = {"audius": src_audius, "jamendo": src_jamendo, "itunes": src_itunes, "deezer": src_deezer,
           "youtube": src_youtube, "animethemes": src_animethemes}


MAX_FETCH = 100     # most results asked from one source (pages beyond this come back empty)


def do_search(q, lang, names, limit, page=1):
    """Searches all given sources in parallel. A failing source is skipped.
    Page N returns results (N-1)*limit .. N*limit of every source."""
    li = lang_info(lang)
    full_q = (q + " " + li["hint"]).strip()
    if not names:
        return []
    fetch = min(limit * page, MAX_FETCH)
    start = (page - 1) * limit

    def run(n):
        try:
            part = cached("s_" + n, [full_q, fetch, li["country"]],
                          lambda: SOURCES[n](full_q, fetch, li))
            return part[start:start + limit]
        except Exception as e:
            app.logger.warning("source %s failed: %s", n, e)
            return []

    with ThreadPoolExecutor(max_workers=len(names)) as ex:
        parts = list(ex.map(run, names))
    return [item for part in parts for item in part]


def source_names(source):
    """Returns (names, error_response). source = 'all' or one source name."""
    allowed = g.plan["sources"]
    if source == "all":
        return allowed, None
    if source not in SOURCES:
        return None, reply(status=400, error=t("bad_source"))
    if source not in allowed:
        return None, reply(status=403, error=t("plan_lock"))
    return [source], None


# ---------------- public routes ----------------
@app.route("/")
def home():
    return reply({
        "name": APP_NAME, "version": VERSION,
        "auth": "Header X-API-Key or ?key=",
        "tip": "Add ?msg=hi for Hindi/Hinglish messages",
        "endpoints": [
            "/search?q=&lang=&source=&limit=&page=", "/play?q= or ?id=", "/batch?q=a|b|c or ?ids=x,y", "/track?id=", "/trending?genre=",
            "/genre/{name}", "/mood/{name}", "/artist?q=", "/album?q=", "/similar?id=",
            "/languages", "/playlist/create|list|{id}|{id}/add|{id}/remove|{id}/delete",
            "/favorites | /favorites/add | /favorites/remove", "/me", "/health",
        ],
    })


@app.route("/privacy")
def privacy():
    html = (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Privacy Policy - " + APP_NAME + "</title>"
        "<body style='font-family:sans-serif;max-width:700px;margin:auto;padding:16px;line-height:1.5'>"
        "<h1>Privacy Policy</h1>"
        "<p>" + APP_NAME + " is a music search service. We store only: your API key, its plan and usage count, "
        "and the playlists and favourites you create. We do not collect names, phone numbers, location or "
        "payment details, and we do not sell data.</p>"
        "<p>Search requests are sent to third-party services (Audius, Jamendo, Apple iTunes, Deezer, "
        "AnimeThemes and YouTube). Videos from YouTube are played in the YouTube player and are subject to the "
        "<a href='https://www.youtube.com/t/terms'>YouTube Terms of Service</a> and the "
        "<a href='https://policies.google.com/privacy'>Google Privacy Policy</a>.</p>"
        "<p>Music and previews belong to their owners. To delete your data or report content, contact: "
        + CONTACT_EMAIL + ".</p></body>")
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/health")
@app.route("/healthz")
@app.route("/ping")
def health():
    try:
        c = connect()
        try:
            c.execute("SELECT 1").fetchone()
        finally:
            c.close()
        return reply({"status": "ok", "time": int(time.time())})
    except sqlite3.Error:
        return reply(status=503, error="database error")


@app.route("/languages")
def languages():
    return reply({"languages": list(LANGUAGES), "moods": list(MOODS), "genres": GENRES,
                  "message_languages": list(MSG), "sources": list(SOURCES)})


@app.route("/search")
@require_key
def search():
    q = get_q()
    if not q:
        return reply(status=400, error=t("need_q"))
    if not lang_ok():
        return reply(status=400, error=t("bad_lang"))
    names, err = source_names(request.args.get("source", "all"))
    if err:
        return err
    lang = request.args.get("lang")
    page = page_arg()
    res = do_search(q, lang, names, limit_arg(), page)
    return reply({"query": q, "language": lang or "any", "page": page,
                  "count": len(res), "results": res})


def lookup_track(tid):
    """Returns the track dict for 'source:id', or None if it cannot be found."""
    src, _, raw = tid.partition(":")
    if src == "youtube" and raw.replace("-", "").replace("_", "").isalnum():
        return {"source": "youtube", "id": tid, "play_url": "https://www.youtube.com/watch?v=" + raw,
                "embed_url": "https://www.youtube.com/embed/" + raw, "type": "youtube_embed",
                "credit": CREDITS["youtube"]}
    if not raw.isalnum():
        return None
    try:
        if src == "animethemes":
            j = cached("t", [tid], lambda: http_get(
                "https://api.animethemes.moe/animetheme/" + raw,
                include="song.artists,anime,animethemeentries.videos.audio"))
            return anime_item(j["animetheme"])
        if src == "audius":
            j = cached("t", [tid], lambda: http_get(f"https://api.audius.co/v1/tracks/{raw}",
                                                    app_name=APP_NAME))
            return audius_item(j["data"])
        if src == "deezer":
            x = cached("t", [tid], lambda: http_get(f"https://api.deezer.com/track/{raw}"))
            return deezer_item(x)
        if src == "itunes":
            j = cached("t", [tid], lambda: http_get("https://itunes.apple.com/lookup", id=raw))
            return itunes_item(j["results"][0])
        if src == "jamendo" and JAMENDO_ID:
            j = cached("t", [tid], lambda: http_get("https://api.jamendo.com/v3.0/tracks/",
                                                    client_id=JAMENDO_ID, format="json", id=raw))
            return jamendo_item(j["results"][0])
    except Exception:
        pass
    return None


@app.route("/track")
@require_key
def track():
    tid = request.args.get("id", "").strip()
    if not tid:
        return reply(status=400, error=t("need_id"))
    item = lookup_track(tid)
    if not item:
        return reply(status=404, error=t("not_found"))
    return reply(item)


PLAY_RANK = {"full": 0, "youtube_embed": 1, "preview_30s": 2}


@app.route("/play")
@require_key
def play():
    """Best playable link. ?id=source:id  or  ?q=song name (&lang=).
    Order of preference: full audio > YouTube player > 30s preview."""
    tid = request.args.get("id", "").strip()
    if tid:
        item = lookup_track(tid)
        if not item:
            return reply(status=404, error=t("not_found"))
        return reply({"best": item, "alternatives": []})
    q = get_q()
    if not q:
        return reply(status=400, error=t("need_q"))
    if not lang_ok():
        return reply(status=400, error=t("bad_lang"))
    names, err = source_names(request.args.get("source", "all"))
    if err:
        return err
    best, alts = best_for(q, request.args.get("lang"), names)
    if not best:
        return reply(status=404, error=t("not_found"))
    return reply({"query": q, "best": best, "alternatives": alts})


def best_for(q, lang, names):
    """(best item, alternatives) for a song name, or (None, [])."""
    tops, rest = [], []
    for n in names:
        res = [r for r in do_search(q, lang, [n], 5) if r.get("play_url")]
        if res:
            tops.append(res[0])
            rest.extend(res[1:])
    if not tops:
        return None, []
    # the first hit of each source is its most relevant one; pick the best kind of link among them
    tops.sort(key=lambda r: PLAY_RANK.get(r.get("type"), 9))
    return tops[0], tops[1:] + rest


MAX_BATCH = 20


@app.route("/batch")
@require_key
def batch():
    """Many songs in one call.
    /batch?q=song one|song two|song three   -> best playable link for each name
    /batch?ids=audius:1,deezer:2,youtube:abc -> details of each id"""
    ids = [x.strip() for x in request.args.get("ids", "").split(",") if x.strip()][:MAX_BATCH]
    queries = [x.strip()[:200] for x in request.args.get("q", "").split("|") if x.strip()][:MAX_BATCH]
    if not ids and not queries:
        return reply(status=400, error=t("need_q"))
    if ids:
        with ThreadPoolExecutor(max_workers=min(5, len(ids))) as ex:
            items = list(ex.map(lookup_track, ids))
        return reply({"count": len(ids), "results": [
            {"id": i, "found": it is not None, "track": it} for i, it in zip(ids, items)]})
    if not lang_ok():
        return reply(status=400, error=t("bad_lang"))
    names, err = source_names(request.args.get("source", "all"))
    if err:
        return err
    lang = request.args.get("lang")
    with ThreadPoolExecutor(max_workers=min(5, len(queries))) as ex:
        found = list(ex.map(lambda q: best_for(q, lang, names), queries))
    return reply({"count": len(queries), "results": [
        {"query": q, "found": b is not None, "best": b, "alternatives": a[:4]}
        for q, (b, a) in zip(queries, found)]})


@app.route("/trending")
@require_key
def trending():
    p = {"app_name": APP_NAME}
    genre = request.args.get("genre")
    if genre:
        p["genre"] = genre
    try:
        j = cached("trend", p, lambda: http_get("https://api.audius.co/v1/tracks/trending", **p))
        return reply([audius_item(x) for x in j.get("data", [])[:limit_arg(20)]])
    except Exception:
        return reply(status=502, error=t("upstream"))


@app.route("/genre/<name>")
@require_key
def genre(name):
    if not lang_ok():
        return reply(status=400, error=t("bad_lang"))
    res = do_search(name, request.args.get("lang"), g.plan["sources"], limit_arg())
    return reply({"genre": name, "count": len(res), "results": res})


@app.route("/mood/<name>")
@require_key
def mood(name):
    if name not in MOODS:
        return reply(status=404, error=t("not_found"))
    if not lang_ok():
        return reply(status=400, error=t("bad_lang"))
    res = do_search(MOODS[name], request.args.get("lang"), g.plan["sources"], limit_arg())
    return reply({"mood": name, "count": len(res), "results": res})


@app.route("/artist")
@require_key
def artist():
    q = get_q()
    if not q:
        return reply(status=400, error=t("need_q"))
    n = limit_arg()
    try:
        j = cached("artist", [q, n], lambda: http_get("https://api.deezer.com/search/artist", q=q, limit=n))
        return reply([{"id": a["id"], "name": a["name"], "picture": a.get("picture_big"),
                       "fans": a.get("nb_fan")} for a in j.get("data", [])])
    except Exception:
        return reply(status=502, error=t("upstream"))


@app.route("/album")
@require_key
def album():
    q = get_q()
    if not q:
        return reply(status=400, error=t("need_q"))
    n = limit_arg()
    try:
        j = cached("album", [q, n], lambda: http_get("https://api.deezer.com/search/album", q=q, limit=n))
        return reply([{"id": a["id"], "title": a["title"], "artist": (a.get("artist") or {}).get("name"),
                       "cover": a.get("cover_big"), "tracks": a.get("nb_tracks")}
                      for a in j.get("data", [])])
    except Exception:
        return reply(status=502, error=t("upstream"))


@app.route("/similar")
@require_key
def similar():
    """Related artists for a deezer:<id> track (premium / ultra)."""
    if g.plan_name == "free":
        return reply(status=403, error=t("plan_lock"))
    tid = request.args.get("id", "").strip()
    if not tid:
        return reply(status=400, error=t("need_id"))
    src, _, raw = tid.partition(":")
    if src != "deezer" or not raw.isdigit():
        return reply(status=400, error="Use a deezer:<id> track (search with source=deezer)")
    n = limit_arg(10)
    try:
        x = cached("t", [tid], lambda: http_get(f"https://api.deezer.com/track/{raw}"))
        aid = x["artist"]["id"]
        j = cached("sim", [aid], lambda: http_get(f"https://api.deezer.com/artist/{aid}/related"))
        return reply([{"id": a["id"], "name": a["name"], "picture": a.get("picture_big")}
                      for a in j.get("data", [])[:n]])
    except Exception:
        return reply(status=404, error=t("not_found"))


# ---------------- playlists & favorites ----------------
def track_from(p):
    out = {k: ((p.get(k) or "").strip()[:500] or None)
           for k in ("id", "title", "artist", "play_url", "embed_url", "cover", "source", "type")}
    return out


def get_playlist(pid):
    return db().execute("SELECT * FROM playlists WHERE id=? AND key=? AND is_fav=0",
                        (pid, g.key)).fetchone()


write_lock = threading.Lock()


def update_tracks(pid, fn):
    """Atomic read-modify-write of one playlist's track list (no lost updates).
    fn(tracks) -> (new_tracks or None to leave unchanged, result)."""
    with write_lock:
        d = db()
        d.execute("BEGIN IMMEDIATE")
        try:
            row = d.execute("SELECT tracks FROM playlists WHERE id=?", (pid,)).fetchone()
            if row is None:
                d.rollback()
                raise NotFound()
            new, result = fn(json.loads(row["tracks"]))
            if new is not None:
                d.execute("UPDATE playlists SET tracks=? WHERE id=?", (json.dumps(new), pid))
            d.commit()
            return result
        except Exception:
            d.rollback()
            raise


@app.route("/playlist/create", methods=["GET", "POST"])
@require_key
def playlist_create():
    name = (params().get("name") or "").strip()[:100]
    if not name:
        return reply(status=400, error=t("need_name"))
    n = db().execute("SELECT COUNT(*) c FROM playlists WHERE key=? AND is_fav=0",
                     (g.key,)).fetchone()["c"]
    if n >= g.plan["playlists"]:
        return reply(status=403, error=t("full"))
    cur = db().execute("INSERT INTO playlists(key,name) VALUES(?,?)", (g.key, name))
    db().commit()
    return reply({"id": cur.lastrowid, "name": name})


@app.route("/playlist/list")
@require_key
def playlist_list():
    rows = db().execute("SELECT id,name,tracks FROM playlists WHERE key=? AND is_fav=0 ORDER BY id",
                        (g.key,)).fetchall()
    return reply([{"id": r["id"], "name": r["name"], "count": len(json.loads(r["tracks"]))} for r in rows])


@app.route("/playlist/<int:pid>")
@require_key
def playlist_get(pid):
    p = get_playlist(pid)
    if not p:
        return reply(status=404, error=t("not_found"))
    return reply({"id": pid, "name": p["name"], "tracks": json.loads(p["tracks"])})


@app.route("/playlist/<int:pid>/add", methods=["GET", "POST"])
@require_key
def playlist_add(pid):
    p = get_playlist(pid)
    if not p:
        return reply(status=404, error=t("not_found"))
    tr = track_from(params())
    if not tr["id"]:
        return reply(status=400, error=t("need_id"))
    def add(tracks):
        if any(x["id"] == tr["id"] for x in tracks):
            return None, ("dup", len(tracks))
        if len(tracks) >= MAX_PLAYLIST_TRACKS:
            return None, ("full", len(tracks))
        tracks.append(tr)
        return tracks, ("added", len(tracks))

    state, count = update_tracks(pid, add)
    if state == "dup":
        return reply({"added": False, "reason": "already in playlist", "count": count})
    if state == "full":
        return reply(status=403, error=t("full"))
    return reply({"added": True, "count": count})


@app.route("/playlist/<int:pid>/remove", methods=["GET", "POST"])
@require_key
def playlist_remove(pid):
    p = get_playlist(pid)
    if not p:
        return reply(status=404, error=t("not_found"))
    tid = (params().get("id") or "").strip()
    if not tid:
        return reply(status=400, error=t("need_id"))
    count = update_tracks(pid, lambda tr: (lambda kept: (kept, len(kept)))(
        [x for x in tr if x["id"] != tid]))
    return reply({"count": count})


@app.route("/playlist/<int:pid>/delete", methods=["GET", "POST"])
@require_key
def playlist_delete(pid):
    cur = db().execute("DELETE FROM playlists WHERE id=? AND key=? AND is_fav=0", (pid, g.key))
    db().commit()
    if cur.rowcount == 0:
        return reply(status=404, error=t("not_found"))
    return reply({"deleted": pid})


def fav_row():
    with write_lock:
        r = db().execute("SELECT * FROM playlists WHERE key=? AND is_fav=1", (g.key,)).fetchone()
        if not r:
            db().execute("INSERT INTO playlists(key,name,is_fav) VALUES(?,?,1)", (g.key, "favorites"))
            db().commit()
            r = db().execute("SELECT * FROM playlists WHERE key=? AND is_fav=1", (g.key,)).fetchone()
    return r


@app.route("/favorites")
@require_key
def fav_list():
    return reply(json.loads(fav_row()["tracks"]))


@app.route("/favorites/add", methods=["GET", "POST"])
@require_key
def fav_add():
    tr = track_from(params())
    if not tr["id"]:
        return reply(status=400, error=t("need_id"))
    cap = g.plan["favorites"]

    def add(tracks):
        if any(x["id"] == tr["id"] for x in tracks):
            return None, ("ok", len(tracks))
        if len(tracks) >= cap:
            return None, ("full", len(tracks))
        tracks.append(tr)
        return tracks, ("ok", len(tracks))

    state, count = update_tracks(fav_row()["id"], add)
    if state == "full":
        return reply(status=403, error=t("full"))
    return reply({"count": count})


@app.route("/favorites/remove", methods=["GET", "POST"])
@require_key
def fav_remove():
    tid = (params().get("id") or "").strip()
    if not tid:
        return reply(status=400, error=t("need_id"))
    count = update_tracks(fav_row()["id"], lambda tr: (lambda kept: (kept, len(kept)))(
        [x for x in tr if x["id"] != tid]))
    return reply({"count": count})


@app.route("/me")
@require_key
def me():
    row = db().execute("SELECT * FROM keys WHERE key=?", (g.key,)).fetchone()
    exp = row["expires"]
    return reply({
        "name": row["name"], "plan": row["plan"], "total_requests": row["usage"],
        "limit_per_min": g.plan["rpm"], "max_results": g.plan["max_results"],
        "sources": g.plan["sources"], "expires": exp,
        "days_left": max(0, int((exp - time.time()) / 86400)) if exp else None,
    })


# ---------------- admin (owner only, header X-Admin-Key) ----------------
@app.route("/admin/create", methods=["GET", "POST"])
@require_admin
def admin_create():
    p = params()
    plan = p.get("plan", "free")
    if plan not in PLANS:
        return reply(status=400, error="plan must be free / premium / ultra")
    days = to_int(p.get("days"), 30, 1, 36500)
    name = (p.get("name") or "user").strip()[:100]
    key = "AMAN-" + secrets.token_hex(12)
    db().execute("INSERT INTO keys(key,name,plan,expires,created) VALUES(?,?,?,?,?)",
                 (key, name, plan, time.time() + days * 86400, time.time()))
    db().commit()
    return reply({"key": key, "plan": plan, "days": days, "name": name})


@app.route("/admin/upgrade", methods=["GET", "POST"])
@require_admin
def admin_upgrade():
    p = params()
    key, plan = p.get("key"), p.get("plan")
    if plan not in PLANS:
        return reply(status=400, error="plan must be free / premium / ultra")
    cur = db().execute("UPDATE keys SET plan=? WHERE key=?", (plan, key))
    if cur.rowcount == 0:
        return reply(status=404, error="Key not found")
    if p.get("days"):
        days = to_int(p["days"], 30, 1, 36500)
        db().execute("UPDATE keys SET expires=? WHERE key=?", (time.time() + days * 86400, key))
    db().commit()
    return reply({"key": key, "plan": plan})


@app.route("/admin/revoke", methods=["GET", "POST"])
@require_admin
def admin_revoke():
    key = params().get("key")
    cur = db().execute("UPDATE keys SET active=0 WHERE key=?", (key,))
    db().commit()
    if cur.rowcount == 0:
        return reply(status=404, error="Key not found")
    return reply({"revoked": key})


@app.route("/admin/list")
@require_admin
def admin_list():
    rows = db().execute("SELECT * FROM keys ORDER BY created DESC").fetchall()
    return reply([dict(r) for r in rows])


@app.route("/admin/export")
@require_admin
def admin_export():
    """Active keys in KEYS_JSON format. Paste the 'keys_json' value into the KEYS_JSON
    environment variable so the keys survive a server wipe."""
    now = time.time()
    rows = db().execute("SELECT * FROM keys WHERE active=1").fetchall()
    out = [{"key": r["key"], "name": r["name"], "plan": r["plan"],
            "days": max(1, int(((r["expires"] or now + 3650 * 86400) - now) / 86400))}
           for r in rows if not r["expires"] or r["expires"] > now]
    return reply({"keys_json": json.dumps(out, ensure_ascii=False)})


@app.route("/admin/stats")
@require_admin
def admin_stats():
    d = db()
    return reply({
        "total_keys": d.execute("SELECT COUNT(*) c FROM keys").fetchone()["c"],
        "active_keys": d.execute("SELECT COUNT(*) c FROM keys WHERE active=1").fetchone()["c"],
        "by_plan": {r["plan"]: r["c"] for r in d.execute("SELECT plan, COUNT(*) c FROM keys GROUP BY plan")},
        "total_requests": d.execute("SELECT COALESCE(SUM(usage),0) s FROM keys").fetchone()["s"],
        "cache_entries": len(cache),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
