"""
SanjivFlix Comment Reply Bot
============================
3 kaam (mode):
  python reply_bot.py draft 25   -> 25 naye comments ke AI reply banakar drafts.txt mein rakhta hai
  python reply_bot.py send       -> drafts.txt ke saare reply Facebook par ek saath bhej deta hai
  python reply_bot.py auto 12    -> bina dikhaye seedha reply bana kar bhej deta hai

Settings GitHub "Secrets" se aati hain - is file mein koi password/token mat likhna.
"""

import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

GRAPH = "https://graph.facebook.com/v26.0"
HERE = os.path.dirname(os.path.abspath(__file__))
DRAFTS_FILE = os.path.join(HERE, "drafts.txt")
SEP = "=" * 40


def env(name, default=None, required=False):
    value = (os.environ.get(name) or "").strip()
    if not value:
        if required:
            fail(f"GitHub Secret '{name}' set nahi hai. Settings > Secrets and variables > Actions mein daaliye.")
        return default
    return value


def fail(msg):
    print(f"\n[ERROR] {msg}")
    summary(f"### ❌ Error\n{msg}")
    sys.exit(1)


def summary(text):
    """GitHub Actions ke result page par dikhne wala message."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


# ------------------------------------------------------------------ settings
PAGE_ID = PAGE_TOKEN = GEMINI_KEY = None
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-flash-latest")
# Ek model busy ho to agla try hoga
FALLBACK_MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest"]
LIKE_COMMENTS = env("LIKE_COMMENTS", "true").lower() in ("1", "true", "yes")
MAX_AGE_HOURS = int(env("MAX_COMMENT_AGE_HOURS", "168"))  # 7 din
POSTS_TO_CHECK = int(env("POSTS_TO_CHECK", "20"))
AUTO_MODE = len(sys.argv) > 1 and sys.argv[1] == "auto"
# Facebook ko "unusual activity" na lage - ek run mein seemit kaam, beech mein ruk-ruk kar
MAX_HIDES_PER_RUN = int(env("MAX_HIDES_PER_RUN", "8"))
POST_GAP_SECONDS = (8, 20)  # har reply ke beech 8-20 sec (random)
HIDE_LINK_COMMENTS = env("HIDE_LINK_COMMENTS", "true").lower() in ("1", "true", "yes")

# Link pehchanne ka niyam: http, www, .com/.in..., WhatsApp/Telegram links wagairah
LINK_RE = re.compile(
    r"(https?://|www\.|wa\.me|t\.me/|chat\.whatsapp|whatsapp\.com|bit\.ly|tinyurl|"
    r"\b[a-z0-9-]+\s?\.\s?(com|in|net|org|me|ly|link|xyz|site|online|io|co|app|club|top|live|info|shop|store|tk|cc)\b)",
    re.IGNORECASE)
OWN_LINK_WORDS = ("sanjivflix",)  # inke links spam nahi maane jaayenge
SAFE_ATTACHMENTS = {"sticker", "photo", "animated_image_share", "animated_image_autoplay",
                    "animated_image_video"}


def load_secrets(need_ai=True):
    global PAGE_ID, PAGE_TOKEN, GEMINI_KEY
    PAGE_ID = env("FB_PAGE_ID", required=True)
    PAGE_TOKEN = env("FB_PAGE_TOKEN", required=True)
    if need_ai:
        GEMINI_KEY = env("GEMINI_API_KEY", required=True)


# ------------------------------------------------------------------ Facebook
class FbError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def fb_request(method, path, params=None):
    params = dict(params or {})
    params["access_token"] = PAGE_TOKEN
    url = f"{GRAPH}/{path}"
    for attempt in range(3):
        try:
            if method == "GET":
                r = requests.get(url, params=params, timeout=60)
            else:
                r = requests.post(url, data=params, timeout=60)
        except requests.RequestException as e:
            if attempt < 2:
                print(f"  Facebook se network dikkat ({type(e).__name__}), 15 sec baad dobara...")
                time.sleep(15)
                continue
            raise FbError(f"network: {type(e).__name__}")
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.ok and "error" not in data:
            return data
        err = data.get("error", {}) if isinstance(data, dict) else {}
        code = err.get("code")
        if code in (4, 17, 32, 613) and attempt < 2:  # Facebook rate limit
            print("  Facebook rate limit, 60 sec ruk rahe hain...")
            time.sleep(60)
            continue
        if code == 190:
            fail("Facebook Page Token galat ya expire ho gaya hai. Naya token banakar "
                 "GitHub Secret FB_PAGE_TOKEN mein daaliye.")
        detail = f"{err.get('message') or 'HTTP ' + str(r.status_code)} (code {code}, subcode {err.get('error_subcode')}, type {err.get('type')})"
        raise FbError(detail, code)
    raise FbError("Facebook ne baar baar mana kiya")


def fb_get(path, params=None):
    return fb_request("GET", path, params)


def fb_post(path, params=None):
    return fb_request("POST", path, params)


def parse_time(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")


def recent_objects():
    """Page ki latest posts aur reels -> {id: caption}"""
    objs = {}
    errors = []
    for path, field in ((f"{PAGE_ID}/posts", "message"), (f"{PAGE_ID}/video_reels", "description")):
        try:
            data = fb_get(path, {"fields": f"id,{field},created_time", "limit": POSTS_TO_CHECK})
            for p in data.get("data", []):
                objs.setdefault(p["id"], p.get(field, "") or "")
        except FbError as e:
            print(f"[warn] {path} nahi mila: {e}")
            errors.append(str(e))
    if len(errors) == 2 and AUTO_MODE:
        # auto har 15 min chalta hai - fail karke email ki baarish nahi karni, bas saaf likh do
        print("\n[ERROR] Facebook se posts/reels nahi mil rahe - token ya app mein dikkat hai.\n" + errors[0])
        summary("### ❌ Facebook token/app mein dikkat - reply nahi ja rahe\n" + errors[0])
    elif len(errors) == 2:
        fail("Facebook se posts/reels nahi mil rahe - token ya app mein dikkat hai.\n" + errors[0])
    return objs


def page_already_replied(comment):
    replies = (comment.get("comments") or {}).get("data", [])
    return any((r.get("from") or {}).get("id") == PAGE_ID for r in replies)


def pending_comments(limit):
    """Wo comments jinka Page ne abhi tak reply nahi diya (naye pehle)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    fields = ("id,message,created_time,is_hidden,attachment{type,url},from{id,name},"
              "comments.limit(100){from{id}}")
    found, seen = [], set()
    hidden_now = 0
    for obj_id, caption in recent_objects().items():
        try:
            data = fb_get(f"{obj_id}/comments", {
                "fields": fields, "filter": "toplevel",
                "order": "reverse_chronological", "limit": 100,
            })
        except FbError as e:
            print(f"[warn] {obj_id} ke comments nahi mile: {e}")
            continue
        for c in data.get("data", []):
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            if (c.get("from") or {}).get("id") == PAGE_ID:
                continue  # Page ka apna comment
            if c.get("is_hidden"):
                continue  # pehle se hide (Moderation Assist ya bot ne kiya)
            if has_link(c):
                print(f"[link/spam] reply nahi hoga: {(c.get('message') or '')[:80]}")
                if HIDE_LINK_COMMENTS and hidden_now < MAX_HIDES_PER_RUN and hide_comment(c["id"]):
                    hidden_now += 1
                    time.sleep(random.uniform(*POST_GAP_SECONDS))
                continue  # link wale comment ka kabhi reply nahi
            if not (c.get("message") or "").strip():
                continue  # sirf sticker/GIF
            try:
                if parse_time(c["created_time"]) < cutoff:
                    continue  # bahut purana
            except (KeyError, ValueError):
                pass
            if page_already_replied(c):
                continue
            c["_caption"] = caption
            found.append(c)
    if hidden_now:
        print(f"🚫 {hidden_now} link/spam comments hide kiye")
        summary(f"🚫 {hidden_now} link/spam comments hide kiye")
    found.sort(key=lambda c: c.get("created_time", ""), reverse=True)
    return found[:limit]


def has_link(c):
    msg = c.get("message") or ""
    att = c.get("attachment") or {}
    if att and att.get("type") not in SAFE_ATTACHMENTS:
        url = (att.get("url") or "").lower()
        if not any(w in url for w in OWN_LINK_WORDS):
            return True  # link ka preview card (jaise WhatsApp channel)
    for m in LINK_RE.finditer(msg):
        around = msg[max(0, m.start() - 5): m.end() + 40].lower()
        if not any(w in around for w in OWN_LINK_WORDS):
            return True
    return False


def hide_comment(comment_id):
    try:
        fb_post(comment_id, {"is_hidden": "true"})
        return True
    except FbError as e:
        print(f"  [hide nahi hua] {e}")
        return False


def still_unreplied(comment_id):
    try:
        c = fb_get(comment_id, {"fields": "id,comments.limit(100){from{id}}"})
        return not page_already_replied(c)
    except FbError:
        return False  # comment delete ho gaya


def send_reply(comment_id, text):
    fb_post(f"{comment_id}/comments", {"message": text})
    if LIKE_COMMENTS:
        try:
            fb_post(f"{comment_id}/likes")
        except FbError as e:
            print(f"  [like nahi hua] {e}")


# ------------------------------------------------------------------ Gemini AI
def load_instructions():
    with open(os.path.join(HERE, "bot_instructions.txt"), encoding="utf-8") as f:
        return f.read()


def gemini_batch(instructions, items):
    """items: [{n, caption, name, comment}] -> {n: reply or None}"""
    listing = json.dumps(items, ensure_ascii=False, indent=1)
    prompt = (
        "Neeche SanjivFlix Page par aaye comments ki list hai (JSON). Har item mein "
        "n (number), caption (us post/reel ka caption), name (comment karne wala) aur comment hai.\n"
        "Har comment ka alag reply likho. Is list ke saare reply ek-doosre se bilkul alag hone chahiye - "
        "koi bhi do reply same line, same shuruaat ya same ending wale na hon. Caption mein movie ka naam ho to use karo.\n"
        "Jis comment ka reply nahi dena, uske reply mein sirf SKIP likho.\n\n" + listing
    )
    body = {
        "system_instruction": {"parts": [{"text": instructions}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.9,
            "maxOutputTokens": 16384,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {"n": {"type": "INTEGER"}, "reply": {"type": "STRING"}},
                    "required": ["n", "reply"],
                },
            },
        },
    }
    headers = {"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"}
    models = [GEMINI_MODEL] + [m for m in FALLBACK_MODELS if m != GEMINI_MODEL]
    models += [m for m in extra_flash_models() if m not in models]
    last_err = "?"
    rounds = 1 if AUTO_MODE else 3  # auto mein 1 chakkar - 15 min baad wese bhi dobara chalega
    for rnd in range(rounds):
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                r = requests.post(url, json=body, headers=headers, timeout=180)
            except requests.RequestException as e:
                last_err = str(e)
                print(f"  {model}: network error, agla model...")
                continue
            if r.status_code in (429, 500, 503, 404):
                last_err = f"{model} {r.status_code}"
                print(f"  {model} busy/uplabdh nahi ({r.status_code}), agla model try kar rahe hain...")
                continue
            if r.status_code in (400, 403) and "API key" in r.text:
                fail("Gemini API key galat hai. GitHub Secret GEMINI_API_KEY check kariye.")
            if not r.ok:
                last_err = f"{model} {r.status_code}: {r.text[:200]}"
                print(f"  {model} error {r.status_code}, agla model...")
                continue
            data = r.json()
            parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
            text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            try:
                rows = json.loads(text)
            except json.JSONDecodeError:
                last_err = f"{model}: adhoora jawab"
                print(f"  {model} ka jawab adhoora aaya, agla model...")
                continue
            print(f"  ✓ {model} se reply bane")
            return {int(row["n"]): clean(row.get("reply", "")) for row in rows if "n" in row}
        if rnd == rounds - 1:
            break
        wait = 40 * (rnd + 1)
        print(f"  Saare models busy, {wait} sec ruk kar dobara...")
        time.sleep(wait)
    raise RuntimeError(f"Gemini ne jawab nahi diya ({last_err}). Thodi der baad dobara chalaiye.")


_EXTRA = None


def extra_flash_models():
    """Google se poochho abhi kaun-kaun se Flash models chal rahe hain (backup ke liye)."""
    global _EXTRA
    if _EXTRA is not None:
        return _EXTRA
    _EXTRA = []
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         headers={"x-goog-api-key": GEMINI_KEY}, params={"pageSize": 200}, timeout=30)
        bad = ("image", "tts", "audio", "live", "embedding", "vision", "thinking", "exp", "robotics", "computer")
        for m in r.json().get("models", []):
            name = m.get("name", "").replace("models/", "")
            if ("flash" in name and "generateContent" in m.get("supportedGenerationMethods", [])
                    and not any(b in name for b in bad)):
                _EXTRA.append(name)
        _EXTRA.sort(reverse=True)  # naye version pehle
        _EXTRA = _EXTRA[:6]
        print(f"  Backup models: {', '.join(_EXTRA) or 'koi nahi'}")
    except Exception as e:
        print(f"  [models list nahi mili] {e}")
    return _EXTRA


def clean(text):
    text = (text or "").strip().strip('"').strip()
    if not text or text.upper().startswith("SKIP"):
        return None
    if len(text) > 400:
        text = text[:400].rsplit(" ", 1)[0] + "..."
    return text


def generate(comments):
    """comments -> [(comment, reply or None)]"""
    instructions = load_instructions()
    results = []
    for start in range(0, len(comments), 25):  # ek AI call mein 25 tak
        chunk = comments[start:start + 25]
        items = [{
            "n": i + 1,
            "caption": (c.get("_caption") or "")[:300],
            "name": (c.get("from") or {}).get("name", ""),
            "comment": c["message"].strip()[:1000],
        } for i, c in enumerate(chunk)]
        print(f"AI se {len(chunk)} reply ban rahe hain...")
        replies = gemini_batch(instructions, items)
        for i, c in enumerate(chunk):
            results.append((c, replies.get(i + 1)))
        if start + 25 < len(comments):
            time.sleep(15)
    return results


# ------------------------------------------------------------------ drafts.txt
HEADER = """# ===== SanjivFlix reply drafts =====
# Banaye gaye: {when}
#
# Kaise use karein:
#  - Reply badalna ho to "REPLY:" ke aage ka text badal dijiye.
#  - Koi reply nahi bhejna ho to "REPLY:" ke aage SKIP likh dijiye.
#  - Sab theek ho to Actions mein "2 - Sab Bhejo" chalaiye.
#  - "ID:" wali line mat badaliye.
"""


def write_drafts(rows):
    when = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%d %b %Y, %I:%M %p")
    out = [HEADER.format(when=when)]
    for c, reply in rows:
        name = (c.get("from") or {}).get("name", "") or "unknown"
        comment = " ".join(c["message"].split())
        caption = " ".join((c.get("_caption") or "").split())[:70]
        out += [SEP, f"ID: {c['id']}", f"POST: {caption}", f"COMMENT ({name}): {comment}",
                f"REPLY: {reply or 'SKIP'}"]
    out.append(SEP)
    with open(DRAFTS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def read_drafts():
    if not os.path.exists(DRAFTS_FILE):
        return []
    drafts, cur, in_reply = [], None, False
    with open(DRAFTS_FILE, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            s = line.strip()
            if s.startswith("#") and cur is None:
                continue
            if s.startswith("=" * 10):
                in_reply = False
                continue
            if s.startswith("ID:"):
                cur = {"id": s[3:].strip(), "reply": "", "comment": ""}
                drafts.append(cur)
                in_reply = False
            elif cur and s.startswith("REPLY:"):
                cur["reply"] = s[6:].strip()
                in_reply = True
            elif cur and s.startswith("COMMENT"):
                cur["comment"] = s
                in_reply = False
            elif cur and s.startswith("POST:"):
                in_reply = False
            elif cur and in_reply and s:
                cur["reply"] += "\n" + s  # reply ki agli line
    return drafts


# ------------------------------------------------------------------ modes
def mode_draft(count):
    load_secrets()
    print(f"=== Reply Banao: {count} comments tak ===")
    comments = pending_comments(count)
    if not comments:
        write_drafts([])
        print("Koi naya comment nahi mila jiska reply baaki ho. 🎉")
        summary("### ✅ Koi naya comment nahi hai jiska reply baaki ho 🎉")
        return
    rows = generate(comments)
    write_drafts(rows)
    ready = sum(1 for _, r in rows if r)
    lines = [f"### 📝 {ready} reply taiyaar ({len(rows) - ready} skip)",
             "", "Badalna ho to repo mein **drafts.txt** kholiye. Sab theek ho to **2 - Sab Bhejo** chalaiye.", ""]
    for c, r in rows:
        name = (c.get("from") or {}).get("name", "") or "unknown"
        msg = " ".join(c["message"].split())[:120]
        lines.append(f"- **{name}:** {msg}  \n  ↳ {r or '_SKIP_'}")
    summary("\n".join(lines))
    print("\n".join(lines))


def mode_send():
    load_secrets(need_ai=False)
    drafts = read_drafts()
    to_send = [d for d in drafts if d["reply"] and d["reply"].strip().upper() != "SKIP"]
    print(f"=== Sab Bhejo: {len(to_send)} reply ===")
    if not to_send:
        summary("### ⚠️ drafts.txt mein bhejne layak koi reply nahi hai. Pehle **1 - Reply Banao** chalaiye.")
        print("Bhejne ko kuch nahi.")
        return
    sent = skipped = failed = 0
    for d in to_send:
        if not still_unreplied(d["id"]):
            print(f"- {d['id']}: pehle hi reply ho chuka / comment hat gaya, chhod diya")
            skipped += 1
            continue
        try:
            send_reply(d["id"], d["reply"].strip())
            print(f"- bheja: {d['reply'][:80]}")
            sent += 1
            time.sleep(random.uniform(*POST_GAP_SECONDS))
        except FbError as e:
            print(f"- [fail] {d['id']}: {e}")
            failed += 1
    with open(DRAFTS_FILE, "w", encoding="utf-8") as f:
        f.write(f"# Pichhle drafts bhej diye gaye: {sent} bheje, {skipped} chhode, {failed} fail.\n"
                "# Naye reply ke liye Actions mein '1 - Reply Banao' chalaiye.\n")
    summary(f"### 🚀 {sent} reply bhej diye\n{skipped} chhode (pehle se reply tha), {failed} fail")


def mode_auto(count):
    count = min(count, int(env("MAX_REPLIES_PER_RUN", "12")))  # ek run mein 12 se zyada nahi
    load_secrets()
    print(f"=== Auto Reply: {count} comments tak ===")
    comments = pending_comments(count)
    if not comments:
        print("Koi naya comment nahi.")
        return
    sent = 0
    try:
        rows = generate(comments)
    except RuntimeError as e:
        # Gemini busy - koi baat nahi, 15 minute baad agla run dobara koshish karega
        print(f"AI abhi busy hai, agle run mein reply jaayenge. ({e})")
        summary("### ⏳ AI busy tha, agle run mein reply jaayenge")
        return
    for c, reply in rows:
        if not reply:
            continue
        try:
            if not still_unreplied(c["id"]):
                continue  # beech mein kisi ne reply kar diya
            send_reply(c["id"], reply)
            sent += 1
            print(f"- bheja: {reply[:80]}")
            time.sleep(random.uniform(*POST_GAP_SECONDS))
        except Exception as e:
            print(f"- [fail] {e}")
    summary(f"### 🤖 Auto: {sent} reply bheje")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "draft"
    count = 25
    if len(sys.argv) > 2:
        try:
            count = max(1, min(100, int(sys.argv[2])))
        except ValueError:
            pass
    if mode == "draft":
        mode_draft(count)
    elif mode == "send":
        mode_send()
    elif mode == "debug":
        load_secrets(need_ai=False)
        for path, params in (("me", {"fields": "id,name"}),
                             (f"{PAGE_ID}", {"fields": "id,name"}),
                             (f"{PAGE_ID}/posts", {"limit": 1}),
                             ("me/permissions", {})):
            try:
                print(path, "->", json.dumps(fb_get(path, params))[:400])
            except FbError as e:
                print(path, "-> ERROR", e)
            except SystemExit:
                print(path, "-> token invalid (190)")
    elif mode == "auto":
        mode_auto(count)
    else:
        fail(f"Unknown mode: {mode}")


if __name__ == "__main__":
    main()
