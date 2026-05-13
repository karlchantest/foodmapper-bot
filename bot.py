import os
import re
import httpx
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, CallbackQueryHandler, filters, ContextTypes
)
from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ADMIN_ID     = int(os.environ.get("ADMIN_ID", "0"))

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── States ────────────────────────────────────────────────────────────────────
WAITING_USERNAME, WAITING_NAME, WAITING_SELECTION = range(3)

# ── Helpers ───────────────────────────────────────────────────────────────────
def extract_ig_url(text: str):
    pattern = r'https?://(?:www\.)?instagram\.com/(?:reel|p)/[^\s]+'
    match = re.search(pattern, text)
    return match.group(0) if match else None

async def get_user(user_id: int):
    res = supabase.table("users").select("*").eq("user_id", user_id).execute()
    return res.data[0] if res.data else None

async def search_places(name: str):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": f"{name} Vancouver BC Canada", "format": "json", "limit": 4, "addressdetails": 1}
    headers = {"User-Agent": "FoodMapperBot/1.0"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, headers=headers)
        return resp.json()

async def find_or_create_restaurant(name, area, lat, lng):
    # Check if restaurant already exists (by name + approximate location)
    res = supabase.table("restaurants").select("*").ilike("name", f"%{name}%").execute()
    for r in res.data:
        if r.get("lat") and abs(r["lat"] - lat) < 0.001 and abs(r["lng"] - lng) < 0.001:
            return r["id"], False  # existing
    # Create new
    new = supabase.table("restaurants").insert({
        "name": name, "area": area, "lat": lat, "lng": lng
    }).execute()
    return new.data[0]["id"], True  # new

# ── Handlers ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = await get_user(uid)
    if user:
        await update.message.reply_text(
            f"你好返，{user['username']}！🗺\n\nForward 一條 IG 食物 Reel 俾我加落食圖！"
        )
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            "你好！我係食圖 Bot 🗺\n\n俾個花名我認識你？"
        )
        return WAITING_USERNAME

async def save_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    uid = update.effective_user.id
    supabase.table("users").insert({"user_id": uid, "username": username}).execute()
    await update.message.reply_text(
        f"正！{username}，歡迎加入食圖 🎉\n\nForward 一條 IG 食物 Reel 嚟試吓！"
    )
    return ConversationHandler.END

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = await get_user(uid)
    if not user:
        await update.message.reply_text("先用 /start 設定你嘅花名！")
        return ConversationHandler.END

    text = update.message.text or ""
    ig_url = extract_ig_url(text)
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

    if not results:
        await update.message.reply_text("🤔 搵唔到，試吓用英文或改吓名。")
        return WAITING_NAME

    context.user_data['places'] = results
    buttons = []
    for i, r in enumerate(results):
        addr = r.get("display_name", "")[:60]
        buttons.append([InlineKeyboardButton(f"{i+1}. {addr}", callback_data=str(i))])
    buttons.append([InlineKeyboardButton("🔄 都唔係，重新搵", callback_data="retry")])
    buttons.append([InlineKeyboardButton("📢 通知 Admin 加入", callback_data="report")])

    await update.message.reply_text("係咪以下其中一間？", reply_markup=InlineKeyboardMarkup(buttons))
    return WAITING_SELECTION

async def handle_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = context.user_data['user_id']
    ig_url = context.user_data['ig_url']

    if query.data == "retry":
        await query.edit_message_text("好，再試吓。呢間叫咩名？")
        return WAITING_NAME

    if query.data == "report":
        name = context.user_data.get('search_name', '未知')
        supabase.table("pending_restaurants").insert({
            "name": name, "ig_url": ig_url, "reported_by": uid
        }).execute()
        if ADMIN_ID:
            user = await get_user(uid)
            username = user['username'] if user else str(uid)
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"📢 新餐廳 Request\n餐廳名：{name}\nIG：{ig_url}\n用戶：{username}"
            )
        await query.edit_message_text("✅ 已通知 Admin，我哋會盡快加入！")
        return ConversationHandler.END

    idx = int(query.data)
    place = context.user_data['places'][idx]
    lat  = float(place["lat"])
    lng  = float(place["lon"])
    addr = place.get("display_name", "")
    name = place.get("name") or addr.split(",")[0]

    try:
        rest_id, is_new = await find_or_create_restaurant(name, addr, lat, lng)

        # Add reel
        supabase.table("reels").insert({
            "restaurant_id": rest_id, "ig_url": ig_url, "added_by": uid
        }).execute()

        # Save to user's map (ignore if already saved)
        try:
            supabase.table("user_saves").insert({
                "user_id": uid, "restaurant_id": rest_id
            }).execute()
        except:
            pass  # already saved, that's fine

        status = "新餐廳已加落地圖 🆕" if is_new else "條片已加落呢間餐廳 🎬"
        await query.edit_message_text(
            f"✅ {status}\n\n🍽 {name}\n📍 {addr[:80]}\n\n繼續 forward 下一條片！"
        )
    except Exception as e:
        await query.edit_message_text("⚠️ 儲存失敗，請再試一次。")
        print(f"Error: {e}")

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
