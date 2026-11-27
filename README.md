# AMAN MUSIC API v2.1

Premium music API by **AMAN**. Multi-language search, genres, moods, playlists, favorites,
API keys with free / premium / ultra plans.

## Files
- `app.py` – the whole API
- `requirements.txt` – packages
- `Procfile` – start command for hosting

## Permanent hosting (Render, step by step)
1. GitHub par naya repo banao (private rakho) aur ye 4 files upload karo.
2. render.com -> **New -> Web Service** -> apna repo select karo.
3. Build command: `pip install -r requirements.txt`
   Start command: `gunicorn app:app --workers 1 --threads 8 --timeout 60`
   (`--workers 1` hi rakho, warna rate limit aur cache alag-alag ho jate hain)
4. **Environment** mein ye daalo:
   - `ADMIN_KEY` = apna lamba secret (zaroori, iske bina admin band rehta hai)
   - `JAMENDO_ID` = optional, developer.jamendo.com se free (poore gaane)
5. Deploy. Aapko `https://aapka-naam.onrender.com` milega. `/health` kholke check karo.

### Keys ko restart se bachana (zaroori)
Free hosting par disk reset ho sakti hai, to database ki keys ud sakti hain. Bachav:
1. `curl -H "X-Admin-Key: SECRET" https://URL/admin/create?plan=ultra&name=aman&days=3650`
2. `curl -H "X-Admin-Key: SECRET" https://URL/admin/export` -> `keys_json` ki value copy karo.
3. Render Environment mein `KEYS_JSON` naam se paste karo.
Ab ye keys har restart par wapas aa jati hain. (Playlists/favorites phir bhi disk par rehti hain,
unke liye persistent disk ya VPS chahiye.)

### Server sone se bachana
Free plan inactivity ke baad so sakta hai. uptimerobot.com par free monitor banao jo har
5 minute `https://URL/health` kholta rahe.

## Termux par chalana
```
pip install -r requirements.txt
export ADMIN_KEY="apna_secret"
python app.py
```
Phone band hone par API band ho jayegi, isliye permanent ke liye hosting use karo.

## Use
Header `X-API-Key: AMAN-xxxx` ya `?key=AMAN-xxxx`. Hindi errors ke liye `?msg=hi`.

| Endpoint | Kaam |
|---|---|
| `/search?q=&lang=&source=&limit=` | gaana search (lang: hindi, punjabi, tamil, telugu, bhojpuri, bengali, marathi, gujarati, kannada, malayalam, urdu, english, spanish, korean, japanese, arabic) |
| `/search?q=&limit=&page=` | `page=2` se agle gaane (har source ke) |
| `/play?q=gaane ka naam` ya `/play?id=source:id` | sabse achhi playable link: poora audio > YouTube player > 30s preview |
| `/batch?q=gaana 1\|gaana 2\|gaana 3` ya `/batch?ids=a:1,b:2` | ek call mein 20 tak gaane (har naam ka best link) |
| `/track?id=audius:123` | gaane ki detail (id search result se) |
| `/trending?genre=` | trending (Audius) |
| `/genre/<name>`, `/mood/<name>` | genre / mood se gaane |
| `/artist?q=`, `/album?q=` | artist / album search |
| `/similar?id=deezer:123` | milte-julte artists (premium, ultra) |
| `/playlist/create?name=` | playlist banao; `/playlist/list`, `/playlist/<id>`, `/playlist/<id>/add?id=&title=&artist=&play_url=&cover=`, `/remove?id=`, `/delete` |
| `/favorites`, `/favorites/add?id=`, `/favorites/remove?id=` | favorites |
| `/me` | apna plan, limit, bache din |
| `/languages`, `/health` | bina key ke |

## Secrets (zaroori)
Code mein koi password ya key mat likho. Render Environment mein ye daalo:
- `ADMIN_KEY` = lamba secret (iske bina admin band rehta hai)
- `BUILTIN_KEY` = (optional) apni khud ki ultra key, jo restart ke baad bhi rahe
- `YOUTUBE_API_KEY`, `JAMENDO_ID` = optional
- `CONTACT_EMAIL` = tumhara email (`/privacy` page par dikhta hai, Play Store form mein is page ka link do)

## Plans
| Plan | Requests/min | Max results | Playlists | Favorites |
|---|---|---|---|---|
| free | 30 | 10 | 1 | 20 |
| premium | 300 | 30 | 20 | 500 |
| ultra | 1000 | 100 | 200 | 5000 |

## Admin (header `X-Admin-Key`)
`/admin/create?plan=&days=&name=` · `/admin/upgrade?key=&plan=&days=` · `/admin/revoke?key=` ·
`/admin/list` · `/admin/stats` · `/admin/export`

## Dhyan rakhna
- Audius aur Jamendo ke gaane poore play hote hain. iTunes aur Deezer sirf 30 second ka preview dete hain.
- 43 languages hain (Hindi, Bhojpuri, Punjabi, Haryanvi, Tamil, Telugu, Nepali, Korean, Japanese, anime, Arabic, Spanish, French...). `/languages` se poori list dekho. Language filter search ko us bhasha ki taraf mod deta hai, 100% exact nahi hota.
- YouTube (premium/ultra) ke liye Render Environment mein `YOUTUBE_API_KEY` daalo (free, Google Cloud -> YouTube Data API v3). Gaana YouTube ke official player (`embed_url`) mein chalta hai.
- AnimeThemes se anime ke OP/ED milte hain.
- Koi bhi free legal API sab gaane poore nahi deti; unofficial scrapers use mat karo.
