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

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── States ────────────────────────────────────────────────────────────────────
WAITING_NAME, WAITING_SELECTION = range(2)

# ── Helpers ───────────────────────────────────────────────────────────────────
def extract_ig_url(text: str):
    pattern = r'https?://(?:www\.)?instagram\.com/(?:reel|p)/[^\s]+'
    match = re.search(pattern, text)
    return match.group(0) if match else None

async def search_places(name: str):
    """Search restaurants via Nominatim — no API key needed."""
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": f"{name} Vancouver BC Canada",
        "format": "json",
        "limit": 4,
        "addressdetails": 1,
    }
    headers = {"User-Agent": "FoodMapperBot/1.0"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, headers=headers)
        results = resp.json()
    return results

# ── Handlers ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "你好！我係食圖 Bot 🗺\n\n"
        "Forward 一條 IG 食物 Reel 俾我，我幫你加落食圖！\n\n"
        "試吓 forward 一條連結嚟。"
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    ig_url = extract_ig_url(text)

    if not ig_url:
        await update.message.reply_text(
            "🤔 搵唔到 IG 連結。\n"
            "試吓 forward 一條 instagram.com/reel/ 連結嚟。"
        )
        return ConversationHandler.END

    context.user_data['ig_url']  = ig_url
    context.user_data['user_id'] = update.effective_user.id
    await update.message.reply_text("呢間叫咩名？")
    return WAITING_NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("俾個名我 😅")
        return WAITING_NAME

    await update.message.reply_text("⏳ 搵緊...")
    results = await search_places(name)

    if not results:
        await update.message.reply_text(
            "🤔 搵唔到呢間餐廳。\n試吓改吓名再試，或者用英文。"
        )
        return WAITING_NAME

    context.user_data['places'] = results

    buttons = []
    for i, r in enumerate(results):
        addr = r.get("display_name", "")[:60]
        buttons.append([InlineKeyboardButton(f"{i+1}. {addr}", callback_data=str(i))])
    buttons.append([InlineKeyboardButton("🔄 都唔係，重新搵", callback_data="retry")])

    await update.message.reply_text(
        "係咪以下其中一間？",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return WAITING_SELECTION

async def handle_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "retry":
        await query.edit_message_text("好，再試吓。呢間叫咩名？")
        return WAITING_NAME

    idx = int(query.data)
    place = context.user_data['places'][idx]
    ig_url = context.user_data['ig_url']
    uid    = context.user_data['user_id']

    lat  = float(place["lat"])
    lng  = float(place["lon"])
    addr = place.get("display_name", "")
    name = place.get("name") or addr.split(",")[0]

    try:
        supabase.table("restaurants").insert({
            "user_id": uid,
            "name":    name,
            "area":    addr,
            "ig_url":  ig_url,
            "lat":     lat,
            "lng":     lng,
        }).execute()

        await query.edit_message_text(
            f"✅ 已加落食圖！\n\n"
            f"🍽 {name}\n"
            f"📍 {addr[:80]}\n\n"
            f"繼續 forward 下一條片！"
        )
    except Exception as e:
        await query.edit_message_text("⚠️ 儲存失敗，請再試一次。")
        print(f"Supabase error: {e}")

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("取消咗。Forward 新連結可以重新開始。")
    return ConversationHandler.END

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link)],
        states={
            WAITING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            WAITING_SELECTION: [CallbackQueryHandler(handle_selection)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.run_polling()

if __name__ == "__main__":
    main()
