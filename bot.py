import time
import traceback
import requests
import uuid
import html
import sys
import logging
from bs4 import BeautifulSoup
from flask import Flask
import threading

# ========== CONFIG ==========
BOT_TOKEN = "8322782484:AAFabKLjwzaexfBwg18Jj8LBoDB6kc7MyUs"
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/"
AUTHORIZED_IDS = {-1002140219716, 5548923721, -1002347179579}
REQUEST_CHANNEL_ID = -1002445682159
BASE_SEARCH_URL = "https://cinebuzzbd.com/?s="
PER_PAGE = 5
SESSION_TTL = 60 * 60
# ===========================

# Enhanced logging for web hosting
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

SESSIONS = {}

BLOCK_SUBSTRINGS = [
    "recent posts", "recent comments", "attention", "login to your account",
    "login", "search", "archives", "categories", "meta", "follow", "subscribe",
    "about", "contact", "privacy", "terms", "comment", "footer", "sidebar"
]

def esc(s):
    return html.escape(s, quote=False)

def get_updates(offset=None, timeout=60):
    params = {}
    if offset:
        params["offset"] = offset
    params["timeout"] = timeout
    try:
        resp = requests.get(API_URL + "getUpdates", params=params, timeout=timeout+10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"get_updates error: {e}")
        raise

def send_message(chat_id, text, reply_to=None, parse_mode="HTML", reply_markup=None, disable_web_page_preview=True):
    payload = {
        "chat_id": chat_id, 
        "text": text, 
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview
    }
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(API_URL + "sendMessage", json=payload, timeout=15)
        r.raise_for_status()
        j = r.json()
        if j.get("ok"):
            return j["result"]["message_id"]
    except Exception as e:
        logger.error(f"send_message error: {e}")
    return None

def edit_message(chat_id, message_id, text, parse_mode="HTML", reply_markup=None, disable_web_page_preview=True):
    payload = {
        "chat_id": chat_id, 
        "message_id": message_id, 
        "text": text, 
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(API_URL + "editMessageText", json=payload, timeout=15)
        r.raise_for_status()
        return r.json().get("ok", False)
    except Exception as e:
        logger.error(f"edit_message error: {e}")
        return False

def answer_callback(callback_id, text=None, show_alert=False):
    payload = {"callback_query_id": callback_id, "show_alert": show_alert}
    if text:
        payload["text"] = text
    try:
        requests.post(API_URL + "answerCallbackQuery", json=payload, timeout=10)
    except Exception as e:
        logger.error(f"answer_callback error: {e}")

def fetch_titles_sync(query):
    url = BASE_SEARCH_URL + requests.utils.requote_uri(query.replace(" ", "+"))
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        logger.error(f"fetch_titles_sync request error: {e}")
        raise

    results = []
    
    # Find all article elements first (main posts)
    for article in soup.find_all("article"):
        title_tag = article.find(['h2', 'h3', 'h1'])
        if title_tag:
            a_tag = title_tag.find('a', href=True)
            if a_tag:
                title = a_tag.get_text(strip=True)
                link = a_tag['href']
                if title and is_valid_item(title, link):
                    if {"title": title, "link": link} not in results:
                        results.append({"title": title, "link": link})
    
    # If no articles found, search for posts with specific classes
    if not results:
        for post_class in ["post", "entry", "blog-post", "item"]:
            for post in soup.find_all(class_=post_class):
                title_tag = post.find(['h2', 'h3', 'h1', 'h4'])
                if title_tag:
                    a_tag = title_tag.find('a', href=True)
                    if a_tag:
                        title = a_tag.get_text(strip=True)
                        link = a_tag['href']
                        if title and is_valid_item(title, link):
                            if {"title": title, "link": link} not in results:
                                results.append({"title": title, "link": link})
    
    # Final fallback: direct links check
    if not results:
        for a in soup.find_all('a', href=True):
            link = a['href']
            if is_valid_link(link):
                title = a.get_text(strip=True)
                if title and len(title) > 10 and is_valid_item(title, link):
                    if {"title": title, "link": link} not in results:
                        results.append({"title": title, "link": link})
    
    logger.info(f"Found {len(results)} results for query: {query}")
    return results

def is_valid_link(link):
    l = (link or "").lower()
    valid_paths = ["/movies/", "/tvshows/", "/movie/", "/tvshow/"]
    return any(path in l for path in valid_paths)

def is_valid_item(title, link):
    t = title.strip().lower()
    for bad in BLOCK_SUBSTRINGS:
        if bad in t:
            return False
    
    if not is_valid_link(link):
        return False
        
    if len(t) < 5:
        return False
        
    return True

def build_page_text(sid, page_num):
    session = SESSIONS.get(sid)
    if not session:
        return "❌ Session expired or not found."
    query = session["query"]
    items = session["results"]
    total = len(items)
    if total == 0:
        return f"❌ No results found for '<b>{esc(query)}</b>'.\n\n🤖 <b>Powered By :</b> <a href='https://cinebuzzbd.com/'>CineBuzzBD.Com</a>"
    
    start = (page_num - 1) * PER_PAGE
    end = start + PER_PAGE
    page_items = items[start:end]
    total_pages = (total + PER_PAGE - 1) // PER_PAGE
    
    header = f"<b>Search Results for:</b> <code>{esc(query)}</code>\n<b>Page:</b> {page_num}/{total_pages}\n\n"
    
    lines = []
    for idx, item in enumerate(page_items, 1):
        title = esc(item["title"])
        link = item["link"]
        lines.append(f"🎬 <b>Title :</b> {title}\n📥 <b>Download Link :</b> <a href='{link}'>Click Here</a>")
        if idx < len(page_items):
            lines.append("──────────────")
    
    footer = f"\n\n⚡ <b>Total Found :</b> {total}\n🤖 <b>Powered By :</b> <a href='https://cinebuzzbd.com/'>CineBuzzBD.Com</a>"
    
    return header + "\n".join(lines) + footer

def build_keyboard(sid, page_num):
    session = SESSIONS.get(sid)
    if not session:
        return None
    total = len(session["results"])
    pages = (total + PER_PAGE - 1) // PER_PAGE
    keyboard = []
    row = []
    if page_num > 1:
        row.append({"text": "⬅️ Previous", "callback_data": f"nav:{sid}:{page_num-1}"})
    if page_num < pages:
        row.append({"text": "Next ➡️", "callback_data": f"nav:{sid}:{page_num+1}"})
    if row:
        keyboard.append(row)
    return {"inline_keyboard": keyboard} if keyboard else None

def cleanup_sessions():
    now = time.time()
    to_del = [k for k,v in SESSIONS.items() if now - v.get("ts",0) > SESSION_TTL]
    for k in to_del:
        del SESSIONS[k]

def validate_request_query(query):
    query = ' '.join(query.split())
    parts = query.split()
    if len(parts) < 2:
        return False, query
    
    last_part = parts[-1]
    
    if last_part.isdigit() and len(last_part) == 4 and 1900 <= int(last_part) <= 2100:
        return True, query
    
    if (last_part.startswith('(') and last_part.endswith(')') and 
        last_part[1:-1].isdigit() and len(last_part[1:-1]) == 4 and 
        1900 <= int(last_part[1:-1]) <= 2100):
        return True, query
    
    return False, query

def send_request_to_channel(user_id, username, query):
    try:
        user_info = f"@{username}" if username else f"User ID: {user_id}"
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        
        request_text = f"""
🎬 <b>NEW CONTENT REQUEST</b>
━━━━━━━━━━━━━━━━━━━━
👤 <b>Requested By:</b> {user_info}
📋 <b>Content:</b> <code>{esc(query)}</code>
🕐 <b>Request Time:</b> {timestamp}
━━━━━━━━━━━━━━━━━━━━
🔔 <i>This request has been queued for processing</i>
        """
        send_message(REQUEST_CHANNEL_ID, request_text)
        return True
    except Exception as e:
        logger.error(f"send_request_to_channel error: {e}")
        return False

def is_authorized_user(chat_id, user_id):
    return chat_id in AUTHORIZED_IDS or user_id in AUTHORIZED_IDS

def handle_message(msg):
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    text = msg.get("text") or ""
    message_id = msg.get("message_id")
    user = msg.get("from", {})
    user_id = user.get("id")
    username = user.get("username", "")
    
    if not text:
        return

    parts = text.strip().split()
    if not parts:
        return
    cmd = parts[0].split("@")[0].lower()
    
    if cmd == "/start":
        return

    if cmd == "/request":
        if not is_authorized_user(chat_id, user_id):
            send_message(chat_id, "🚫 You are not authorized to use this bot.")
            return
            
        if len(parts) == 1:
            usage_text = """
🎯 <b>CONTENT REQUEST SYSTEM</b>
━━━━━━━━━━━━━━━━━━━━

📋 <b>How to Request:</b>
<code>/request Movie Name Year</code>
<code>/request Series Name Year</code>

💡 <b>Examples:</b>
<code>/request Raghu Dakat 2025</code>
<code>/request Bachelor Point (2025)</code>

⚠️ <b>Important Guidelines:</b>
• Must include name + year (2025 or (2025))
• Year should be at the end
• Minimum 2 words required
• Including year ensures better matching

🔍 <b>Pro Tip:</b> Always include the release year for faster and more accurate processing!
            """
            send_message(chat_id, usage_text, disable_web_page_preview=True)
            return
        
        query = " ".join(parts[1:]).strip()
        
        is_valid, cleaned_query = validate_request_query(query)
        
        if not is_valid:
            error_text = f"""
❌ <b>INVALID REQUEST FORMAT</b>
━━━━━━━━━━━━━━━━━━━━

📝 <b>Your Request:</b> <code>{esc(query)}</code>

✅ <b>Correct Format Required:</b>
<code>/request Movie Name Year</code>
<code>/request Series Name Year</code>

💡 <b>Valid Examples:</b>
<code>/request Raghu Dakat 2025</code>
<code>/request Bachelor Point (2025)</code>

🎯 <b>Why Include Year?</b>
• Better search accuracy
• Faster processing
• Exact content matching
• Prevents confusion with similar titles

⚠️ <b>Note:</b> Year must be at the end (2025 or (2025))
            """
            send_message(chat_id, error_text, disable_web_page_preview=True)
            return

        success = send_request_to_channel(user_id, username, cleaned_query)
        if success:
            success_text = f"""
✅ <b>REQUEST SUBMITTED SUCCESSFULLY!</b>
━━━━━━━━━━━━━━━━━━━━

📋 <b>Your Request:</b> <code>{esc(cleaned_query)}</code>

⏳ <b>Status:</b> Queued for processing
👥 <b>Team Notification:</b> Sent

🎯 <b>What Happens Next?</b>
• Our team will review your request
• Content will be added if available
• You'll get better search results soon

🙏 Thank you for your patience and contribution!
            """
            send_message(chat_id, success_text, disable_web_page_preview=True)
        else:
            send_message(chat_id, "❌ Failed to send request. Please try again later.")
        return

    if cmd == "/search":
        if not is_authorized_user(chat_id, user_id):
            send_message(chat_id, "🚫 You are not authorized to use this bot.")
            return

        if len(parts) == 1:
            usage_text = """
🔍 <b>ADVANCED SEARCH SYSTEM</b>
━━━━━━━━━━━━━━━━━━━━

📋 <b>How to Search:</b>
<code>/search Movie Name Year</code>
<code>/search Series Name Year</code>

💡 <b>Examples:</b>
<code>/search Raghu Dakat 2025</code>
<code>/search Bachelor Point 2025</code>

🎯 <b>Search Tips for Better Results:</b>
• Include release year for precise matching
• Use exact movie/series names
• Year helps filter latest content
• Better accuracy with complete titles

🚀 <b>Pro Search Strategy:</b>
1. First try: <code>/search Movie Name Year</code>
2. If not found: <code>/request Movie Name Year</code>
3. Year inclusion = 90% better results!

📝 <b>Can't Find What You Need?</b>
Use: <code>/request Movie Name Year</code> to request unavailable content.
            """
            send_message(chat_id, usage_text, disable_web_page_preview=True)
            return

        query = " ".join(parts[1:]).strip()
        init_text = f"🔍 <b>Searching Database:</b> <code>{esc(query)}</code>\n\n⏳ Please wait while we fetch the best results..."
        sent_mid = send_message(chat_id, init_text, reply_to=message_id, disable_web_page_preview=True)
        
        try:
            results = fetch_titles_sync(query)
        except Exception as e:
            err = f"❌ <b>Search Error</b>\n\nError: <code>{esc(str(e))}</code>\n\nPlease try again later or use /request for specific content."
            if sent_mid:
                edit_message(chat_id, sent_mid, err, disable_web_page_preview=True)
            else:
                send_message(chat_id, err, disable_web_page_preview=True)
            return

        seen = set()
        filtered = []
        for r in results:
            key = (r["title"].strip(), r["link"].strip())
            if key in seen:
                continue
            seen.add(key)
            filtered.append(r)

        sid = uuid.uuid4().hex[:8]
        SESSIONS[sid] = {"query": query, "results": filtered, "ts": time.time()}

        page_num = 1
        text_out = build_page_text(sid, page_num)
        keyboard = build_keyboard(sid, page_num)
        
        if sent_mid:
            ok = edit_message(chat_id, sent_mid, text_out, reply_markup=keyboard, disable_web_page_preview=True)
            if not ok:
                send_message(chat_id, text_out, reply_markup=keyboard, disable_web_page_preview=True)
        else:
            send_message(chat_id, text_out, reply_markup=keyboard, disable_web_page_preview=True)

        cleanup_sessions()

def handle_callback(cb):
    callback_id = cb.get("id")
    from_user = cb.get("from", {})
    user_id = from_user.get("id")
    data = cb.get("data", "")
    message = cb.get("message") or {}
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    message_id = message.get("message_id")

    answer_callback(callback_id)

    if not is_authorized_user(chat_id, user_id):
        return

    if not data.startswith("nav:"):
        answer_callback(callback_id, "Unsupported action", show_alert=True)
        return
        
    try:
        _, sid, p = data.split(":")
        page_num = int(p)
    except:
        answer_callback(callback_id, "Invalid data", show_alert=True)
        return

    session = SESSIONS.get(sid)
    if not session:
        edit_message(chat_id, message_id, "❌ Session expired. Please search again.", disable_web_page_preview=True)
        return

    text_out = build_page_text(sid, page_num)
    keyboard = build_keyboard(sid, page_num)
    edit_message(chat_id, message_id, text_out, reply_markup=keyboard, disable_web_page_preview=True)

def run_long_polling():
    logger.info("🤖 Bot starting on Koyeb/Render...")
    offset = None
    error_count = 0
    max_errors = 10
    
    while True:
        try:
            data = get_updates(offset=offset, timeout=60)
            if not data.get("ok"):
                logger.warning("API response not OK, waiting...")
                time.sleep(2)
                continue
            
            error_count = 0  # Reset error count on success
            
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                if "message" in upd:
                    try:
                        handle_message(upd["message"])
                    except Exception as e:
                        logger.error(f"handle_message error: {e}")
                if "callback_query" in upd:
                    try:
                        handle_callback(upd["callback_query"])
                    except Exception as e:
                        logger.error(f"handle_callback error: {e}")
                        
        except requests.exceptions.RequestException as e:
            error_count += 1
            logger.error(f"Network error {error_count}/{max_errors}: {e}")
            if error_count >= max_errors:
                logger.error("Too many network errors, restarting...")
                time.sleep(30)
                error_count = 0
            time.sleep(5)
            
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            break
            
        except Exception as e:
            error_count += 1
            logger.error(f"Unexpected error {error_count}/{max_errors}: {e}")
            if error_count >= max_errors:
                logger.error("Too many errors, restarting...")
                time.sleep(30)
                error_count = 0
            time.sleep(3)

# Simple health check server for Koyeb
def run_health_server():
    app = Flask(__name__)
    
    @app.route('/')
    def health_check():
        return "🤖 Movie Search Bot is running!", 200
    
    @app.route('/health')
    def health():
        return "OK", 200
    
    # Port 8000 এ server run করবে (Koyeb এর default port)
    app.run(host='0.0.0.0', port=8000, debug=False, use_reloader=False)

if __name__ == "__main__":
    # Health check server আলাদা thread এ start করুন
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    logger.info("🚀 Starting Movie Search Bot...")
    while True:
        try:
            run_long_polling()
        except Exception as e:
            logger.error(f"Critical error in main: {e}")
            logger.info("Restarting in 10 seconds...")
            time.sleep(10)
