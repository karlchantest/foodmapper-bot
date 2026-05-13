import os
import re
import httpx
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes
)
from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN       = os.environ.get("BOT_TOKEN")
SUPABASE_URL    = os.environ.get("SUPABASE_URL")
SUPABASE_KEY    = os.environ.get("SUPABASE_KEY")
GOOGLE_API_KEY  = os.environ.get("GOOGLE_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── Conversation states ───────────────────────────────────────────────────────
WAITING_NAME, WAITING_AREA = range(2)

# ── Helpers ───────────────────────────────────────────────────────────────────
def extract_ig_url(text: str):
    pattern = r'https?://(?:www\.)?instagram\.com/(?:reel|p)/[^\s]+'
    match = re.search(pattern, text)
    return match.group(0) if match else None

async def geocode(query: str):
    """Returns (lat, lng, formatted_address) or None."""
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": query, "key": GOOGLE_API_KEY}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        data = resp.json()
    print(f"Geocode query: {query} | status: {data.get('status')}")
    if data.get("status") == "OK":
        result = data["results"][0]
        loc = result["geometry"]["location"]
        return loc["lat"], loc["lng"], result["formatted_address"]
    return None

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

    context.user_data['name'] = name
    await update.message.reply_text(
        "喺邊度？\n"
        "（地區或地址都得，例如：Kingsway Burnaby 或 Richmond）"
    )
    return WAITING_AREA

async def get_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    area = update.message.text.strip()
    if not area:
        await update.message.reply_text("俾個地址我 😅")
        return WAITING_AREA

    name   = context.user_data['name']
    ig_url = context.user_data['ig_url']
    uid    = context.user_data['user_id']

    await update.message.reply_text("⏳ 搵緊地址...")

    # Try progressively simpler queries
    geo = None
    queries = [
        f"{name} {area}",           # Restaurant name + address
        area,                        # Address only
        f"{name} Vancouver BC",      # Name + city fallback
    ]
    for q in queries:
        geo = await geocode(q)
        if geo:
            break

    if geo:
        lat, lng, formatted = geo
        try:
            supabase.table("restaurants").insert({
                "user_id": uid,
                "name":    name,
                "area":    area,
                "ig_url":  ig_url,
                "lat":     lat,
                "lng":     lng,
            }).execute()

            await update.message.reply_text(
                f"✅ 已加落食圖！\n\n"
                f"🍽 {name}\n"
                f"📍 {formatted}\n\n"
                f"繼續 forward 下一條片！"
            )
        except Exception as e:
            await update.message.reply_text("⚠️ 儲存失敗，請再試一次。")
            print(f"Supabase error: {e}")
    else:
        await update.message.reply_text(
            f"🤔 搵唔到位置。\n"
            f"試吓只入地區名，例如：Burnaby 或 Kingsway"
        )
        return WAITING_AREA

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
            WAITING_AREA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_area)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.run_polling()

if __name__ == "__main__":
    main()
