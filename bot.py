import os
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Store data
alerts = {}           # price alerts: {user_id: [{symbol, target, direction}]}
funding_watch = {}    # funding monitors: {user_id: [symbol, ...]}
oi_watch = {}         # OI monitors: {user_id: [symbol, ...]}
oi_cache = {}         # OI cache untuk deteksi spike: {symbol: last_oi}

# ─────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────

async def get_price(symbol: str) -> float | None:
    """Cek harga dari Spot dulu, fallback ke Futures."""
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol = symbol + "USDT"

    # Try Spot first
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data["price"])
    except Exception:
        pass

    # Fallback to Futures
    url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data["price"])
    except Exception:
        pass

    return None

async def get_usd_to_idr() -> float:
    """Ambil kurs USD/IDR realtime dari ExchangeRate API."""
    url = "https://open.er-api.com/v6/latest/USD"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data["rates"]["IDR"])
    except Exception:
        pass
    return 16300.0  # fallback

async def get_funding_rate(symbol: str) -> float | None:
    """Ambil funding rate dari Binance Futures."""
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol = symbol + "USDT"
    url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data["lastFundingRate"])
    except Exception:
        pass
    return None

async def get_open_interest(symbol: str) -> float | None:
    """Ambil Open Interest dari Binance Futures."""
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol = symbol + "USDT"
    url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data["openInterest"])
    except Exception:
        pass
    return None

def funding_status(rate: float) -> str:
    pct = rate * 100
    if pct > 0.1:
        return f"🔴 Extreme Positif → Long bayar Short"
    elif pct > 0:
        return f"🟢 Positif → Long bayar Short"
    elif pct < -0.1:
        return f"🔴 Extreme Negatif → Short bayar Long"
    else:
        return f"🟡 Negatif → Short bayar Long"

# ─────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Crypto Alert Bot by mungxbt\n"
        "@simpulcrypto\n\n"
        "📌 PRICE\n"
        "/price BTC — harga USD\n"
        "/price BTC IDR — harga Rupiah\n\n"
        "🔔 PRICE ALERT\n"
        "/alert BTC 90000 — alert harga\n"
        "/listalerts — lihat alert aktif\n"
        "/removealert BTC — hapus alert\n\n"
        "📊 FUNDING RATE\n"
        "/funding BTC — cek funding rate\n"
        "/addfunding BTC — monitor spike funding\n"
        "/removefunding BTC — hapus monitor\n"
        "/listfunding — lihat funding monitor\n\n"
        "📈 OPEN INTEREST\n"
        "/oi BTC — cek OI sekarang\n"
        "/addoi BTC — monitor spike OI\n"
        "/removeoi BTC — hapus monitor OI\n"
        "/listoi — lihat OI monitor\n"
    )

async def price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Format: /price BTC atau /price BTC IDR")
        return
    symbol = context.args[0].upper()
    currency = context.args[1].upper() if len(context.args) > 1 else "USD"

    price = await get_price(symbol)
    if price is None:
        await update.message.reply_text(f"❌ {symbol} tidak ditemukan di Spot maupun Futures Binance.")
        return

    if currency == "IDR":
        idr_rate = await get_usd_to_idr()
        price_idr = price * idr_rate
        await update.message.reply_text(
            f"💰 {symbol}/USDT\n"
            f"USD  : ${price:,.6f}\n"
            f"IDR  : Rp{price_idr:,.0f}\n"
            f"Rate : $1 = Rp{idr_rate:,.0f}"
        )
    else:
        await update.message.reply_text(f"💰 {symbol}/USDT: ${price:,.6f}")

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
        await update.message.reply_text(f"❌ {symbol} tidak ditemukan di Binance.")
        return

    direction = "above" if target > current else "below"
    if user_id not in alerts:
        alerts[user_id] = []
    alerts[user_id] = [a for a in alerts[user_id] if a["symbol"] != symbol]
    alerts[user_id].append({"symbol": symbol, "target": target, "direction": direction})

    arrow = "📈" if direction == "above" else "📉"
    await update.message.reply_text(
        f"✅ Alert set!\n"
        f"{arrow} {symbol} → ${target:,.6f}\n"
        f"💰 Sekarang: ${current:,.6f}"
    )

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_alerts = alerts.get(user_id, [])
    if not user_alerts:
        await update.message.reply_text("Tidak ada price alert aktif.")
        return
    msg = "🔔 Price Alert aktif:\n\n"
    for a in user_alerts:
        arrow = "📈" if a["direction"] == "above" else "📉"
        msg += f"{arrow} {a['symbol']} → ${a['target']:,.6f}\n"
    await update.message.reply_text(msg)

async def remove_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Format: /removealert BTC")
        return
    symbol = context.args[0].upper()
    if user_id in alerts:
        before = len(alerts[user_id])
        alerts[user_id] = [a for a in alerts[user_id] if a["symbol"] != symbol]
        if len(alerts[user_id]) < before:
            await update.message.reply_text(f"✅ Alert {symbol} dihapus.")
            return
    await update.message.reply_text(f"❌ Tidak ada alert untuk {symbol}.")

async def funding_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Format: /funding BTC")
        return
    symbol = context.args[0].upper()
    rate = await get_funding_rate(symbol)
    if rate is None:
        await update.message.reply_text(f"❌ {symbol} tidak ditemukan di Futures Binance.")
        return
    pct = rate * 100
    status = funding_status(rate)
    await update.message.reply_text(
        f"📊 {symbol} Funding Rate\n\n"
        f"Rate    : {pct:+.4f}%\n"
        f"Interval: 8 jam\n"
        f"Status  : {status}"
    )

async def add_funding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Format: /addfunding BTC")
        return
    symbol = context.args[0].upper()
    rate = await get_funding_rate(symbol)
    if rate is None:
        await update.message.reply_text(f"❌ {symbol} tidak ditemukan di Futures Binance.")
        return
    if user_id not in funding_watch:
        funding_watch[user_id] = []
    if symbol not in funding_watch[user_id]:
        funding_watch[user_id].append(symbol)
    await update.message.reply_text(
        f"✅ Monitor funding rate {symbol} aktif!\n"
        f"Notif kalau funding spike > 0.1% atau < -0.1%"
    )

async def remove_funding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Format: /removefunding BTC")
        return
    symbol = context.args[0].upper()
    if user_id in funding_watch and symbol in funding_watch[user_id]:
        funding_watch[user_id].remove(symbol)
        await update.message.reply_text(f"✅ Monitor funding {symbol} dihapus.")
    else:
        await update.message.reply_text(f"❌ {symbol} tidak ada di monitor list.")

async def list_funding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    watchlist = funding_watch.get(user_id, [])
    if not watchlist:
        await update.message.reply_text("Tidak ada funding monitor aktif.")
        return
    msg = "📊 Funding Monitor aktif:\n\n"
    for symbol in watchlist:
        rate = await get_funding_rate(symbol)
        if rate:
            pct = rate * 100
            msg += f"• {symbol}: {pct:+.4f}%\n"
        else:
            msg += f"• {symbol}: -\n"
    await update.message.reply_text(msg)

async def oi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Format: /oi BTC")
        return
    symbol = context.args[0].upper()
    oi = await get_open_interest(symbol)
    if oi is None:
        await update.message.reply_text(f"❌ {symbol} tidak ditemukan di Futures Binance.")
        return
    price = await get_price(symbol)
    oi_usd = oi * price if price else 0
    await update.message.reply_text(
        f"📈 {symbol} Open Interest\n\n"
        f"OI (Coin) : {oi:,.2f}\n"
        f"OI (USD)  : ${oi_usd:,.0f}"
    )

async def add_oi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Format: /addoi BTC")
        return
    symbol = context.args[0].upper()
    oi = await get_open_interest(symbol)
    if oi is None:
        await update.message.reply_text(f"❌ {symbol} tidak ditemukan di Futures Binance.")
        return
    if user_id not in oi_watch:
        oi_watch[user_id] = []
    if symbol not in oi_watch[user_id]:
        oi_watch[user_id].append(symbol)
    oi_cache[symbol] = oi
    await update.message.reply_text(
        f"✅ Monitor OI {symbol} aktif!\n"
        f"Notif kalau OI spike > 10% dalam 1 jam\n"
        f"OI sekarang: {oi:,.2f}"
    )

async def remove_oi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Format: /removeoi BTC")
        return
    symbol = context.args[0].upper()
    if user_id in oi_watch and symbol in oi_watch[user_id]:
        oi_watch[user_id].remove(symbol)
        await update.message.reply_text(f"✅ Monitor OI {symbol} dihapus.")
    else:
        await update.message.reply_text(f"❌ {symbol} tidak ada di OI monitor list.")

async def list_oi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    watchlist = oi_watch.get(user_id, [])
    if not watchlist:
        await update.message.reply_text("Tidak ada OI monitor aktif.")
        return
    msg = "📈 OI Monitor aktif:\n\n"
    for symbol in watchlist:
        oi = await get_open_interest(symbol)
        if oi:
            msg += f"• {symbol}: {oi:,.2f}\n"
        else:
            msg += f"• {symbol}: -\n"
    await update.message.reply_text(msg)

# ─────────────────────────────────────────
# BACKGROUND JOBS
# ─────────────────────────────────────────

async def check_price_alerts(context: ContextTypes.DEFAULT_TYPE):
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
                text=f"🚨 PRICE ALERT KENA!\n\n"
                     f"{arrow} {a['symbol']} sudah sentuh ${a['target']:,.6f}\n"
                     f"💰 Harga sekarang: ${price:,.6f}"
            )

async def check_funding_spikes(context: ContextTypes.DEFAULT_TYPE):
    all_symbols = set()
    for symbols in funding_watch.values():
        all_symbols.update(symbols)

    for symbol in all_symbols:
        rate = await get_funding_rate(symbol)
        if rate is None:
            continue
        pct = rate * 100
        if abs(pct) >= 0.1:
            for user_id, symbols in funding_watch.items():
                if symbol in symbols:
                    status = funding_status(rate)
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"⚡ FUNDING SPIKE!\n\n"
                             f"📊 {symbol}: {pct:+.4f}%\n"
                             f"{status}"
                    )

async def check_oi_spikes(context: ContextTypes.DEFAULT_TYPE):
    all_symbols = set()
    for symbols in oi_watch.values():
        all_symbols.update(symbols)

    for symbol in all_symbols:
        oi = await get_open_interest(symbol)
        if oi is None:
            continue
        last_oi = oi_cache.get(symbol)
        if last_oi:
            change_pct = ((oi - last_oi) / last_oi) * 100
            if abs(change_pct) >= 10:
                for user_id, symbols in oi_watch.items():
                    if symbol in symbols:
                        arrow = "📈" if change_pct > 0 else "📉"
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=f"⚡ OI SPIKE!\n\n"
                                 f"{arrow} {symbol} OI berubah {change_pct:+.2f}%\n"
                                 f"OI sekarang: {oi:,.2f}\n"
                                 f"OI sebelumnya: {last_oi:,.2f}"
                        )
        oi_cache[symbol] = oi

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    # Price commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("price", price_cmd))

    # Price alert commands
    app.add_handler(CommandHandler("alert", alert_cmd))
    app.add_handler(CommandHandler("listalerts", list_alerts))
    app.add_handler(CommandHandler("removealert", remove_alert))

    # Funding commands
    app.add_handler(CommandHandler("funding", funding_cmd))
    app.add_handler(CommandHandler("addfunding", add_funding))
    app.add_handler(CommandHandler("removefunding", remove_funding))
    app.add_handler(CommandHandler("listfunding", list_funding))

    # OI commands
    app.add_handler(CommandHandler("oi", oi_cmd))
    app.add_handler(CommandHandler("addoi", add_oi))
    app.add_handler(CommandHandler("removeoi", remove_oi))
    app.add_handler(CommandHandler("listoi", list_oi))

    # Background jobs
    app.job_queue.run_repeating(check_price_alerts, interval=60, first=10)
    app.job_queue.run_repeating(check_funding_spikes, interval=3600, first=30)
    app.job_queue.run_repeating(check_oi_spikes, interval=3600, first=30)

    print("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
