import os
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Store alerts: {user_id: [{symbol, target, direction}]}
alerts = {}

async def get_price(symbol: str) -> float | None:
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol = symbol + "USDT"
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data["price"])
    except Exception:
        pass
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Crypto Price Alert Bot\n\n"
        "Commands:\n"
        "/alert BTC 90000 — alert saat BTC sentuh $90,000\n"
        "/listalerts — lihat semua alert aktif\n"
        "/cancelalert BTC — hapus alert untuk BTC\n"
        "/price BTC — cek harga sekarang\n"
    )

async def alert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if len(context.args) < 2:
        await update.message.reply_text("Format: /alert BTC 90000")
        return

    symbol = context.args[0].upper()
    try:
        target = float(context.args[1])
    except ValueError:
        await update.message.reply_text("Harga harus angka. Contoh: /alert BTC 90000")
        return

    current = await get_price(symbol)
    if current is None:
        await update.message.reply_text(f"❌ Coin {symbol} tidak ditemukan.")
        return

    direction = "above" if target > current else "below"

    if user_id not in alerts:
        alerts[user_id] = []

    # Remove existing alert for same symbol
    alerts[user_id] = [a for a in alerts[user_id] if a["symbol"] != symbol]
    alerts[user_id].append({
        "symbol": symbol,
        "target": target,
        "direction": direction
    })

    arrow = "📈" if direction == "above" else "📉"
    await update.message.reply_text(
        f"✅ Alert set!\n"
        f"{arrow} {symbol} → ${target:,.4f}\n"
        f"Harga sekarang: ${current:,.4f}"
    )

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_alerts = alerts.get(user_id, [])
    if not user_alerts:
        await update.message.reply_text("Tidak ada alert aktif.")
        return

    msg = "🔔 Alert aktif lo:\n\n"
    for a in user_alerts:
        arrow = "📈" if a["direction"] == "above" else "📉"
        msg += f"{arrow} {a['symbol']} → ${a['target']:,.4f}\n"
    await update.message.reply_text(msg)

async def cancel_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Format: /cancelalert BTC")
        return

    symbol = context.args[0].upper()
    if user_id in alerts:
        before = len(alerts[user_id])
        alerts[user_id] = [a for a in alerts[user_id] if a["symbol"] != symbol]
        if len(alerts[user_id]) < before:
            await update.message.reply_text(f"✅ Alert {symbol} dihapus.")
            return

    await update.message.reply_text(f"❌ Tidak ada alert untuk {symbol}.")

async def price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Format: /price BTC")
        return
    symbol = context.args[0].upper()
    price = await get_price(symbol)
    if price is None:
        await update.message.reply_text(f"❌ Coin {symbol} tidak ditemukan.")
        return
    await update.message.reply_text(f"💰 {symbol}: ${price:,.6f}")

async def check_alerts(context: ContextTypes.DEFAULT_TYPE):
    for user_id, user_alerts in list(alerts.items()):
        triggered = []
        remaining = []
        for a in user_alerts:
            price = await get_price(a["symbol"])
            if price is None:
                remaining.append(a)
                continue
            hit = (a["direction"] == "above" and price >= a["target"]) or \
                  (a["direction"] == "below" and price <= a["target"])
            if hit:
                triggered.append((a, price))
            else:
                remaining.append(a)

        alerts[user_id] = remaining

        for a, price in triggered:
            arrow = "📈" if a["direction"] == "above" else "📉"
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🚨 ALERT KENA!\n\n"
                     f"{arrow} {a['symbol']} sudah sentuh ${a['target']:,.4f}\n"
                     f"Harga sekarang: ${price:,.6f}"
            )

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("alert", alert_cmd))
    app.add_handler(CommandHandler("listalerts", list_alerts))
    app.add_handler(CommandHandler("cancelalert", cancel_alert))
    app.add_handler(CommandHandler("price", price_cmd))

    # Check alerts every 60 seconds
    app.job_queue.run_repeating(check_alerts, interval=60, first=10)

    print("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
