import os
import re
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes
)
from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── Conversation states ───────────────────────────────────────────────────────
WAITING_NAME, WAITING_AREA = range(2)

# ── Helpers ───────────────────────────────────────────────────────────────────
def extract_ig_url(text: str):
    pattern = r'https?://(?:www\.)?instagram\.com/reel/[^\s]+'
    match = re.search(pattern, text)
    return match.group(0) if match else None

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
            "🤔 搵唔到 IG Reel 連結。\n"
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
        "喺邊個地區？\n"
        "（例如：Downtown、Richmond、Burnaby、West End）"
    )
    return WAITING_AREA

async def get_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    area = update.message.text.strip()
    if not area:
        await update.message.reply_text("俾個地區我 😅")
        return WAITING_AREA

    name   = context.user_data['name']
    ig_url = context.user_data['ig_url']
    uid    = context.user_data['user_id']

    try:
        supabase.table("restaurants").insert({
            "user_id": uid,
            "name":    name,
            "area":    area,
            "ig_url":  ig_url,
        }).execute()

        await update.message.reply_text(
            f"✅ 已加落食圖！\n\n"
            f"🍽 {name}\n"
            f"📍 {area}\n\n"
            f"繼續 forward 下一條片！"
        )
    except Exception as e:
        await update.message.reply_text(
            "⚠️ 儲存失敗，請再試一次。"
        )
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
            WAITING_AREA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_area)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.run_polling()

if __name__ == "__main__":
    main()
