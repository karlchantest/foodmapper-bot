import os
import re
import httpx
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, CallbackQueryHandler, filters, ContextTypes
)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ADMIN_ID     = int(os.environ.get("ADMIN_ID", "0"))

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

# ── States ────────────────────────────────────────────────────────────────────
WAITING_USERNAME, WAITING_NAME, WAITING_SELECTION = range(3)

# ── Supabase helpers (pure httpx, no supabase-py) ────────────────────────────
async def db_get(table: str, filters: str = ""):
    url = f"{SUPABASE_URL}/rest/v1/{table}?select=*{filters}"
    async with httpx.AsyncClient() as c:
        r = await c.get(url, headers=HEADERS)
    return r.json() if r.status_code == 200 else []

async def db_insert(table: str, data: dict):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    async with httpx.AsyncClient() as c:
        r = await c.post(url, headers=HEADERS, json=data)
    print(f"INSERT {table}: {r.status_code} {r.text[:200]}")
    return r.json()[0] if r.status_code in (200, 201) and r.json() else None

# ── Places search ─────────────────────────────────────────────────────────────
async def search_places(name: str):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": f"{name} Vancouver BC Canada", "format": "json", "limit": 4}
    headers = {"User-Agent": "FoodMapperBot/1.0"}
    async with httpx.AsyncClient() as c:
        r = await c.get(url, params=params, headers=headers)
    return r.json()

# ── URL helper ────────────────────────────────────────────────────────────────
def extract_ig_url(text: str):
    m = re.search(r'https?://(?:www\.)?instagram\.com/(?:reel|p)/[^\s]+', text)
    return m.group(0) if m else None

# ── Handlers ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rows = await db_get("users", f"&user_id=eq.{uid}")
    if rows:
        await update.message.reply_text(
            f"你好返，{rows[0]['username']}！🗺\n\nForward 一條 IG 食物 Reel 俾我加落食圖！"
        )
        return ConversationHandler.END
    await update.message.reply_text("你好！我係食圖 Bot 🗺\n\n俾個花名我認識你？")
    return WAITING_USERNAME

async def save_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    username = update.message.text.strip()
    await db_insert("users", {"user_id": uid, "username": username})
    await update.message.reply_text(
        f"正！{username}，歡迎加入食圖 🎉\n\nForward 一條 IG 食物 Reel 嚟試吓！"
    )
    return ConversationHandler.END

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rows = await db_get("users", f"&user_id=eq.{uid}")
    if not rows:
        await update.message.reply_text("先用 /start 設定你嘅花名！")
        return ConversationHandler.END

    ig_url = extract_ig_url(update.message.text or "")
    if not ig_url:
        await update.message.reply_text("🤔 搵唔到 IG 連結，試吓 forward instagram.com/reel/ 連結。")
        return ConversationHandler.END

    context.user_data['ig_url'] = ig_url
    context.user_data['user_id'] = uid
    await update.message.reply_text("呢間叫咩名？")
    return WAITING_NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("俾個名我 😅")
        return WAITING_NAME

    await update.message.reply_text("⏳ 搵緊...")
    results = await search_places(name)
    context.user_data['search_name'] = name

    buttons = []
    if results:
        context.user_data['places'] = results
        for i, r in enumerate(results):
            addr = r.get("display_name", "")[:60]
            buttons.append([InlineKeyboardButton(f"{i+1}. {addr}", callback_data=str(i))])

    buttons.append([InlineKeyboardButton("🔄 都唔係，重新搵", callback_data="retry")])
    buttons.append([InlineKeyboardButton("📢 通知 Admin 加入", callback_data="report")])

    msg = "係咪以下其中一間？" if results else "🤔 搵唔到呢間餐廳。"
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons))
    return WAITING_SELECTION

async def handle_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid    = context.user_data['user_id']
    ig_url = context.user_data['ig_url']

    if query.data == "retry":
        await query.edit_message_text("好，再試吓。呢間叫咩名？")
        return WAITING_NAME

    if query.data == "report":
        name = context.user_data.get('search_name', '未知')
        await db_insert("pending_restaurants", {"name": name, "ig_url": ig_url, "reported_by": uid})
        if ADMIN_ID:
            rows = await db_get("users", f"&user_id=eq.{uid}")
            username = rows[0]['username'] if rows else str(uid)
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"📢 新餐廳 Request\n餐廳名：{name}\nIG：{ig_url}\n用戶：{username}"
            )
        await query.edit_message_text("✅ 已通知 Admin，我哋會盡快加入！")
        return ConversationHandler.END

    idx = int(query.data)
    place = context.user_data['places'][idx]
    lat   = float(place["lat"])
    lng   = float(place["lon"])
    addr  = place.get("display_name", "")
    name  = place.get("name") or addr.split(",")[0]

    try:
        # Find or create restaurant
        existing = await db_get("restaurants", f"&name=ilike.*{name}*&lat=gte.{lat-0.001}&lat=lte.{lat+0.001}")
        if existing:
            rest_id = existing[0]["id"]
            is_new = False
        else:
            new_rest = await db_insert("restaurants", {"name": name, "area": addr, "lat": lat, "lng": lng})
            rest_id = new_rest["id"]
            is_new = True

        # Add reel
        await db_insert("reels", {"restaurant_id": rest_id, "ig_url": ig_url, "added_by": uid})

        # Save to user map (ignore duplicate)
        existing_save = await db_get("user_saves", f"&user_id=eq.{uid}&restaurant_id=eq.{rest_id}")
        if not existing_save:
            await db_insert("user_saves", {"user_id": uid, "restaurant_id": rest_id})

        status = "新餐廳已加落地圖 🆕" if is_new else "條片已加落呢間餐廳 🎬"
        await query.edit_message_text(
            f"✅ {status}\n\n🍽 {name}\n📍 {addr[:80]}\n\n繼續 forward 下一條片！"
        )
    except Exception as e:
        print(f"Save error: {e}")
        await query.edit_message_text("⚠️ 儲存失敗，請再試一次。")

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("取消咗。Forward 新連結可以重新開始。")
    return ConversationHandler.END

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link),
        ],
        states={
            WAITING_USERNAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, save_username)],
            WAITING_NAME:      [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            WAITING_SELECTION: [CallbackQueryHandler(handle_selection)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)
    app.run_polling()

if __name__ == "__main__":
    main()
