import os
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DB_ID = "e756c4cf39eb48caa461a3faca6f8ab0"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

oi_cache = {}

# ─────────────────────────────────────────
# NOTION PERSISTENCE
# ─────────────────────────────────────────

async def notion_add(user_id: int, type_: str, symbol: str, target: str = "", direction: str = ""):
    name = f"{type_}:{user_id}:{symbol}:{target}"
    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "Name": {"title": [{"text": {"content": name}}]},
            "user_id": {"rich_text": [{"text": {"content": str(user_id)}}]},
            "type": {"select": {"name": type_}},
            "symbol": {"rich_text": [{"text": {"content": symbol}}]},
            "target": {"rich_text": [{"text": {"content": target}}]},
            "direction": {"rich_text": [{"text": {"content": direction}}]},
            "active": {"checkbox": True}
        }
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS, json=payload) as resp:
            return await resp.json()

async def notion_delete(page_id: str):
    async with aiohttp.ClientSession() as session:
        async with session.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=NOTION_HEADERS,
            json={"archived": True}
        ) as resp:
            return await resp.json()

async def notion_query(user_id: int, type_: str = None) -> list:
    filters = [
        {"property": "user_id", "rich_text": {"equals": str(user_id)}},
        {"property": "active", "checkbox": {"equals": True}}
    ]
    if type_:
        filters.append({"property": "type", "select": {"equals": type_}})
    payload = {"filter": {"and": filters}}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query",
            headers=NOTION_HEADERS, json=payload
        ) as resp:
            data = await resp.json()
            return data.get("results", [])

async def notion_query_all(type_: str = None) -> list:
    filters = [{"property": "active", "checkbox": {"equals": True}}]
    if type_:
        filters.append({"property": "type", "select": {"equals": type_}})
    payload = {"filter": {"and": filters}}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query",
            headers=NOTION_HEADERS, json=payload
        ) as resp:
            data = await resp.json()
            return data.get("results", [])

def parse_row(row: dict) -> dict:
    props = row["properties"]
    def txt(key):
        items = props.get(key, {}).get("rich_text", [])
        return items[0]["text"]["content"] if items else ""
    return {
        "page_id": row["id"],
        "user_id": int(txt("user_id")),
        "type": props.get("type", {}).get("select", {}).get("name", ""),
        "symbol": txt("symbol"),
        "target": txt("target"),
        "direction": txt("direction"),
    }

# ─────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────

async def get_price(symbol: str) -> float | None:
    """Cek harga dari Spot dulu, fallback ke Futures."""
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
    url = "https://open.er-api.com/v6/latest/USD"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data["rates"]["IDR"])
    except Exception:
        pass
    return 16300.0

async def get_funding_rate(symbol: str) -> float | None:
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

async def get_fear_greed() -> dict | None:
    url = "https://api.alternative.me/fng/?limit=2"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["data"]
    except Exception:
        pass
    return None

async def get_dominance() -> dict | None:
    url = "https://api.coingecko.com/api/v3/global"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["data"]
    except Exception:
        pass
    return None

async def get_heatmap(limit: int = 10) -> list:
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    result = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    tickers = await resp.json()
                    usdt = [t for t in tickers if t["symbol"].endswith("USDT")]
                    sorted_by_vol = sorted(usdt, key=lambda x: float(x["quoteVolume"]), reverse=True)
                    result = sorted_by_vol[:limit]
    except Exception:
        pass
    return result

async def get_long_short_ratio(symbol: str, period: str = "5m") -> dict | None:
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol = symbol + "USDT"
    url = f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period={period}&limit=1"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data:
                        return data[0]
    except Exception:
        pass
    return None

async def get_top_movers(limit: int = 5) -> tuple[list, list]:
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    gainers, losers = [], []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    tickers = await resp.json()
                    usdt = [t for t in tickers if t["symbol"].endswith("USDT")]
                    sorted_tickers = sorted(usdt, key=lambda x: float(x["priceChangePercent"]), reverse=True)
                    gainers = sorted_tickers[:limit]
                    losers = sorted_tickers[-limit:][::-1]
    except Exception:
        pass
    return gainers, losers

def funding_status(rate: float) -> str:
    pct = rate * 100
    if pct > 0.1:
        return "🔴 Extreme Positif → Long bayar Short"
    elif pct > 0:
        return "🟢 Positif → Long bayar Short"
    elif pct < -0.1:
        return "🔴 Extreme Negatif → Short bayar Long"
    else:
        return "🟡 Negatif → Short bayar Long"

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
        "/alert BTC 90000 — set alert harga\n"
        "/listalerts — lihat alert aktif\n"
        "/removealert BTC 90000 — hapus alert spesifik\n"
        "/removealert BTC — hapus semua alert BTC\n\n"
        "📊 FUNDING RATE\n"
        "/funding BTC — cek funding rate\n"
        "/addfunding BTC — monitor spike funding\n"
        "/removefunding BTC — hapus monitor\n"
        "/listfunding — lihat funding monitor\n\n"
        "📈 OPEN INTEREST\n"
        "/oi BTC — cek OI sekarang\n"
        "/addoi BTC — monitor spike OI\n"
        "/removeoi BTC — hapus monitor OI\n"
        "/listoi — lihat OI monitor\n\n"
        "⚖️ LONG/SHORT RATIO\n"
        "/lsr BTC — cek L/S ratio\n\n"
        "🏆 TOP MOVERS\n"
        "/topgainers — top 5 coin naik 24h\n"
        "/toplosers — top 5 coin turun 24h\n\n"
        "😱 MARKET SENTIMENT\n"
        "/feargreed — Fear & Greed Index\n"
        "/dominance — BTC & ETH dominance\n"
        "/heatmap — coin paling ramai ditrading\n"
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
        await update.message.reply_text("Harga harus angka.")
        return
    current = await get_price(symbol)
    if current is None:
        await update.message.reply_text(f"❌ {symbol} tidak ditemukan di Binance.")
        return
    existing = await notion_query(user_id, "price_alert")
    for row in existing:
        r = parse_row(row)
        if r["symbol"] == symbol and r["target"] == str(target):
            await update.message.reply_text(f"⚠️ Alert {symbol} ${target:,.6f} sudah ada!")
            return
    direction = "above" if target > current else "below"
    await notion_add(user_id, "price_alert", symbol, str(target), direction)
    arrow = "📈" if direction == "above" else "📉"
    total = len([r for r in existing if parse_row(r)["symbol"] == symbol]) + 1
    await update.message.reply_text(
        f"✅ Alert set!\n"
        f"{arrow} {symbol} → ${target:,.6f}\n"
        f"💰 Sekarang: ${current:,.6f}\n"
        f"📊 Total alert {symbol}: {total}"
    )

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rows = await notion_query(user_id, "price_alert")
    if not rows:
        await update.message.reply_text("Tidak ada price alert aktif.")
        return
    msg = "🔔 Price Alert aktif:\n\n"
    for row in rows:
        r = parse_row(row)
        arrow = "📈" if r["direction"] == "above" else "📉"
        msg += f"{arrow} {r['symbol']} → ${float(r['target']):,.6f}\n"
    await update.message.reply_text(msg)

async def remove_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(
            "Format:\n"
            "/removealert BTC 90000 — hapus spesifik\n"
            "/removealert BTC — hapus semua alert BTC"
        )
        return
    symbol = context.args[0].upper()
    rows = await notion_query(user_id, "price_alert")
    if len(context.args) > 1:
        try:
            target = float(context.args[1])
        except ValueError:
            await update.message.reply_text("Format: /removealert BTC 90000")
            return
        deleted = 0
        for row in rows:
            r = parse_row(row)
            if r["symbol"] == symbol and float(r["target"]) == target:
                await notion_delete(r["page_id"])
                deleted += 1
        if deleted:
            await update.message.reply_text(f"✅ Alert {symbol} ${target:,.6f} dihapus.")
        else:
            await update.message.reply_text(f"❌ Alert {symbol} ${target:,.6f} tidak ditemukan.")
        return
    deleted = 0
    for row in rows:
        r = parse_row(row)
        if r["symbol"] == symbol:
            await notion_delete(r["page_id"])
            deleted += 1
    if deleted:
        await update.message.reply_text(f"✅ {deleted} alert {symbol} dihapus.")
    else:
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
    await update.message.reply_text(
        f"📊 {symbol} Funding Rate\n\n"
        f"Rate    : {pct:+.4f}%\n"
        f"Interval: 8 jam\n"
        f"Status  : {funding_status(rate)}"
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
    existing = await notion_query(user_id, "funding_watch")
    for row in existing:
        if parse_row(row)["symbol"] == symbol:
            await update.message.reply_text(f"⚠️ {symbol} sudah dimonitor.")
            return
    await notion_add(user_id, "funding_watch", symbol)
    await update.message.reply_text(
        f"✅ Monitor funding rate {symbol} aktif!\n"
        f"Notif kalau spike > 0.1% atau < -0.1%"
    )

async def remove_funding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Format: /removefunding BTC")
        return
    symbol = context.args[0].upper()
    rows = await notion_query(user_id, "funding_watch")
    deleted = 0
    for row in rows:
        r = parse_row(row)
        if r["symbol"] == symbol:
            await notion_delete(r["page_id"])
            deleted += 1
    if deleted:
        await update.message.reply_text(f"✅ Monitor funding {symbol} dihapus.")
    else:
        await update.message.reply_text(f"❌ {symbol} tidak ada di monitor list.")

async def list_funding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rows = await notion_query(user_id, "funding_watch")
    if not rows:
        await update.message.reply_text("Tidak ada funding monitor aktif.")
        return
    msg = "📊 Funding Monitor aktif:\n\n"
    for row in rows:
        r = parse_row(row)
        rate = await get_funding_rate(r["symbol"])
        pct = rate * 100 if rate else 0
        msg += f"• {r['symbol']}: {pct:+.4f}%\n" if rate else f"• {r['symbol']}: -\n"
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
    existing = await notion_query(user_id, "oi_watch")
    for row in existing:
        if parse_row(row)["symbol"] == symbol:
            await update.message.reply_text(f"⚠️ {symbol} sudah dimonitor.")
            return
    await notion_add(user_id, "oi_watch", symbol)
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
    rows = await notion_query(user_id, "oi_watch")
    deleted = 0
    for row in rows:
        r = parse_row(row)
        if r["symbol"] == symbol:
            await notion_delete(r["page_id"])
            deleted += 1
    if deleted:
        await update.message.reply_text(f"✅ Monitor OI {symbol} dihapus.")
    else:
        await update.message.reply_text(f"❌ {symbol} tidak ada di OI monitor list.")

async def list_oi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rows = await notion_query(user_id, "oi_watch")
    if not rows:
        await update.message.reply_text("Tidak ada OI monitor aktif.")
        return
    msg = "📈 OI Monitor aktif:\n\n"
    for row in rows:
        r = parse_row(row)
        oi = await get_open_interest(r["symbol"])
        msg += f"• {r['symbol']}: {oi:,.2f}\n" if oi else f"• {r['symbol']}: -\n"
    await update.message.reply_text(msg)

async def lsr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Format: /lsr BTC")
        return
    symbol = context.args[0].upper()
    data = await get_long_short_ratio(symbol)
    if data is None:
        await update.message.reply_text(f"❌ {symbol} tidak ditemukan atau tidak tersedia di Futures Binance.")
        return

    ratio = float(data["longShortRatio"])
    long_pct = float(data["longAccount"]) * 100
    short_pct = float(data["shortAccount"]) * 100

    if ratio >= 1.5:
        sentiment = "🔴 Terlalu banyak Long → potensi long squeeze"
    elif ratio >= 1.1:
        sentiment = "🟡 Condong Long"
    elif ratio <= 0.67:
        sentiment = "🔴 Terlalu banyak Short → potensi short squeeze"
    elif ratio <= 0.9:
        sentiment = "🟡 Condong Short"
    else:
        sentiment = "🟢 Relatif Seimbang"

    await update.message.reply_text(
        f"⚖️ {symbol} Long/Short Ratio\n\n"
        f"Ratio  : {ratio:.4f}\n"
        f"Long   : {long_pct:.2f}%\n"
        f"Short  : {short_pct:.2f}%\n\n"
        f"Sinyal : {sentiment}"
    )

async def feargreed_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = await get_fear_greed()
    if not data:
        await update.message.reply_text("❌ Gagal mengambil Fear & Greed Index.")
        return

    today = data[0]
    yesterday = data[1] if len(data) > 1 else None
    value = int(today["value"])
    label = today["value_classification"]

    if value <= 25:
        emoji = "😱"
    elif value <= 45:
        emoji = "😟"
    elif value <= 55:
        emoji = "😐"
    elif value <= 75:
        emoji = "😊"
    else:
        emoji = "🤑"

    msg = f"😱 Fear & Greed Index\n\n"
    msg += f"{emoji} Sekarang : {value}/100 — {label}\n"
    if yesterday:
        y_val = int(yesterday["value"])
        y_label = yesterday["value_classification"]
        diff = value - y_val
        arrow = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
        msg += f"{arrow} Kemarin  : {y_val}/100 — {y_label}\n"
        msg += f"   Perubahan: {diff:+d}\n"
    msg += "\n📊 Skala:\n"
    msg += "0-25: Extreme Fear 😱\n"
    msg += "26-45: Fear 😟\n"
    msg += "46-55: Neutral 😐\n"
    msg += "56-75: Greed 😊\n"
    msg += "76-100: Extreme Greed 🤑"

    await update.message.reply_text(msg)

async def dominance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = await get_dominance()
    if not data:
        await update.message.reply_text("❌ Gagal mengambil data dominance.")
        return

    market_cap_pct = data.get("market_cap_percentage", {})
    btc_dom = market_cap_pct.get("btc", 0)
    eth_dom = market_cap_pct.get("eth", 0)
    others = 100 - btc_dom - eth_dom
    total_mcap = data.get("total_market_cap", {}).get("usd", 0)
    total_vol = data.get("total_volume", {}).get("usd", 0)

    btc_bar = "█" * int(btc_dom / 5) + "░" * (20 - int(btc_dom / 5))
    eth_bar = "█" * int(eth_dom / 5) + "░" * (20 - int(eth_dom / 5))

    msg = "📊 Crypto Market Dominance\n\n"
    msg += f"₿ BTC : {btc_dom:.2f}%\n{btc_bar}\n\n"
    msg += f"Ξ ETH : {eth_dom:.2f}%\n{eth_bar}\n\n"
    msg += f"🪙 Altcoin: {others:.2f}%\n\n"
    msg += f"💰 Total Market Cap : ${total_mcap/1e12:.2f}T\n"
    msg += f"📈 Volume 24h        : ${total_vol/1e9:.1f}B"

    await update.message.reply_text(msg)

async def heatmap_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔥 Fetching heatmap...")
    coins = await get_heatmap(10)
    if not coins:
        await update.message.reply_text("❌ Gagal mengambil data heatmap.")
        return

    msg = "🔥 Heatmap — Paling Ramai Ditrading (Futures 24h)\n\n"
    for i, t in enumerate(coins, 1):
        sym = t["symbol"].replace("USDT", "")
        vol = float(t["quoteVolume"])
        pct = float(t["priceChangePercent"])
        price = float(t["lastPrice"])
        emoji = "🟢" if pct >= 0 else "🔴"
        msg += f"{i:2}. {emoji} {sym:<6} {pct:+.2f}%\n"
        msg += f"     ${price:,.4f} | Vol ${vol/1e6:.0f}M\n"

    await update.message.reply_text(msg)

async def top_gainers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Fetching top gainers...")
    gainers, _ = await get_top_movers(5)
    if not gainers:
        await update.message.reply_text("❌ Gagal mengambil data. Coba lagi.")
        return
    msg = "🏆 Top 5 Gainers (Futures 24h)\n\n"
    for i, t in enumerate(gainers, 1):
        sym = t["symbol"].replace("USDT", "")
        pct = float(t["priceChangePercent"])
        price = float(t["lastPrice"])
        vol = float(t["quoteVolume"])
        msg += f"{i}. {sym}: +{pct:.2f}% | ${price:,.4f}\n"
        msg += f"   Vol: ${vol/1e6:.0f}M\n"
    await update.message.reply_text(msg)

async def top_losers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Fetching top losers...")
    _, losers = await get_top_movers(5)
    if not losers:
        await update.message.reply_text("❌ Gagal mengambil data. Coba lagi.")
        return
    msg = "💀 Top 5 Losers (Futures 24h)\n\n"
    for i, t in enumerate(losers, 1):
        sym = t["symbol"].replace("USDT", "")
        pct = float(t["priceChangePercent"])
        price = float(t["lastPrice"])
        vol = float(t["quoteVolume"])
        msg += f"{i}. {sym}: {pct:.2f}% | ${price:,.4f}\n"
        msg += f"   Vol: ${vol/1e6:.0f}M\n"
    await update.message.reply_text(msg)

# ─────────────────────────────────────────
# BACKGROUND JOBS
# ─────────────────────────────────────────

async def check_price_alerts(context: ContextTypes.DEFAULT_TYPE):
    rows = await notion_query_all("price_alert")
    for row in rows:
        r = parse_row(row)
        price = await get_price(r["symbol"])
        if price is None:
            continue
        target = float(r["target"])
        hit = (r["direction"] == "above" and price >= target) or \
              (r["direction"] == "below" and price <= target)
        if hit:
            await notion_delete(r["page_id"])
            arrow = "📈" if r["direction"] == "above" else "📉"
            await context.bot.send_message(
                chat_id=r["user_id"],
                text=f"🚨 PRICE ALERT KENA!\n\n"
                     f"{arrow} {r['symbol']} sudah sentuh ${target:,.6f}\n"
                     f"💰 Harga sekarang: ${price:,.6f}"
            )

async def check_funding_spikes(context: ContextTypes.DEFAULT_TYPE):
    rows = await notion_query_all("funding_watch")
    symbols_done = set()
    for row in rows:
        r = parse_row(row)
        symbol = r["symbol"]
        if symbol in symbols_done:
            continue
        symbols_done.add(symbol)
        rate = await get_funding_rate(symbol)
        if rate is None:
            continue
        pct = rate * 100
        if abs(pct) >= 0.1:
            for row2 in rows:
                r2 = parse_row(row2)
                if r2["symbol"] == symbol:
                    await context.bot.send_message(
                        chat_id=r2["user_id"],
                        text=f"⚡ FUNDING SPIKE!\n\n"
                             f"📊 {symbol}: {pct:+.4f}%\n"
                             f"{funding_status(rate)}"
                    )

async def check_oi_spikes(context: ContextTypes.DEFAULT_TYPE):
    rows = await notion_query_all("oi_watch")
    symbols_done = set()
    for row in rows:
        r = parse_row(row)
        symbol = r["symbol"]
        if symbol in symbols_done:
            continue
        symbols_done.add(symbol)
        oi = await get_open_interest(symbol)
        if oi is None:
            continue
        last_oi = oi_cache.get(symbol)
        if last_oi:
            change_pct = ((oi - last_oi) / last_oi) * 100
            if abs(change_pct) >= 10:
                for row2 in rows:
                    r2 = parse_row(row2)
                    if r2["symbol"] == symbol:
                        arrow = "📈" if change_pct > 0 else "📉"
                        await context.bot.send_message(
                            chat_id=r2["user_id"],
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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("price", price_cmd))

    app.add_handler(CommandHandler("alert", alert_cmd))
    app.add_handler(CommandHandler("listalerts", list_alerts))
    app.add_handler(CommandHandler("removealert", remove_alert))

    app.add_handler(CommandHandler("funding", funding_cmd))
    app.add_handler(CommandHandler("addfunding", add_funding))
    app.add_handler(CommandHandler("removefunding", remove_funding))
    app.add_handler(CommandHandler("listfunding", list_funding))

    app.add_handler(CommandHandler("oi", oi_cmd))
    app.add_handler(CommandHandler("addoi", add_oi))
    app.add_handler(CommandHandler("removeoi", remove_oi))
    app.add_handler(CommandHandler("listoi", list_oi))

    app.add_handler(CommandHandler("lsr", lsr_cmd))
    app.add_handler(CommandHandler("topgainers", top_gainers_cmd))
    app.add_handler(CommandHandler("toplosers", top_losers_cmd))
    app.add_handler(CommandHandler("feargreed", feargreed_cmd))
    app.add_handler(CommandHandler("dominance", dominance_cmd))
    app.add_handler(CommandHandler("heatmap", heatmap_cmd))

    app.job_queue.run_repeating(check_price_alerts, interval=60, first=10)
    app.job_queue.run_repeating(check_funding_spikes, interval=3600, first=30)
    app.job_queue.run_repeating(check_oi_spikes, interval=3600, first=30)

    print("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
