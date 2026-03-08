import os
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DB_ID = "e756c4cf39eb48caa461a3faca6f8ab0"
NOTION_HISTORY_DB_ID = "47f287ad128844b0b4911c6e6f983b16"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

oi_cache = {}

# ─────────────────────────────────────────
# NOTION PERSISTENCE
# ─────────────────────────────────────────

async def notion_add(user_id: int, type_: str, symbol: str, target: str = "", direction: str = "", chat_id: int = None):
    name = f"{type_}:{user_id}:{symbol}:{target}"
    # simpan chat_id di direction field dengan separator | kalau ada
    direction_val = direction
    if chat_id and chat_id != user_id:
        direction_val = f"{direction}|chat:{chat_id}"
    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "Name": {"title": [{"text": {"content": name}}]},
            "user_id": {"rich_text": [{"text": {"content": str(user_id)}}]},
            "type": {"select": {"name": type_}},
            "symbol": {"rich_text": [{"text": {"content": symbol}}]},
            "target": {"rich_text": [{"text": {"content": target}}]},
            "direction": {"rich_text": [{"text": {"content": direction_val}}]},
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
    
    direction_raw = txt("direction")
    user_id = int(txt("user_id"))
    
    # extract chat_id jika tersimpan di direction field
    chat_id = user_id  # default: sama dengan user_id (personal chat)
    direction = direction_raw
    if "|chat:" in direction_raw:
        parts = direction_raw.split("|chat:")
        direction = parts[0]
        try:
            chat_id = int(parts[1])
        except:
            chat_id = user_id

    return {
        "page_id": row["id"],
        "user_id": user_id,
        "chat_id": chat_id,  # kirim notif ke sini
        "type": props.get("type", {}).get("select", {}).get("name", ""),
        "symbol": txt("symbol"),
        "target": txt("target"),
        "direction": direction,
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

async def get_top_movers(limit: int = 5, market: str = "spot") -> tuple[list, list]:
    """market = 'spot' (default, sama dengan Binance web) atau 'futures'"""
    if market == "futures":
        url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    else:
        url = "https://api.binance.com/api/v3/ticker/24hr"
    gainers, losers = [], []
    blacklist = ["UP", "DOWN", "BULL", "BEAR", "USDC", "TUSD", "FDUSD", "USDP"]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    tickers = await resp.json()
                    usdt = [
                        t for t in tickers
                        if t["symbol"].endswith("USDT")
                        and float(t.get("quoteVolume", 0)) > 1_000_000
                        and not any(x in t["symbol"].replace("USDT","") for x in blacklist)
                    ]
                    sorted_tickers = sorted(usdt, key=lambda x: float(x["priceChangePercent"]), reverse=True)
                    gainers = sorted_tickers[:limit]
                    losers = sorted_tickers[-limit:][::-1]
    except Exception:
        pass
    return gainers, losers

# ─────────────────────────────────────────
# DEXSCREENER HELPERS
# ─────────────────────────────────────────

async def dex_search_pairs(query: str) -> list:
    url = f"https://api.dexscreener.com/latest/dex/search?q={query}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("pairs", []) or []
    except Exception:
        pass
    return []

async def dex_by_contract(chain: str, address: str) -> list:
    url = f"https://api.dexscreener.com/token-pairs/v1/{chain}/{address}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list):
                        return data
                    return data.get("pairs", []) or []
    except Exception:
        pass
    return []

async def dex_trending_tokens() -> list:
    url = "https://api.dexscreener.com/token-boosts/top/v1"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list):
                        return data
    except Exception:
        pass
    return []

async def dex_new_listings() -> list:
    url = "https://api.dexscreener.com/token-profiles/latest/v1"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list):
                        return data
    except Exception:
        pass
    return []

async def dex_get_pair_detail(chain: str, address: str) -> dict | None:
    """Ambil detail satu pair by pair address untuk cek marketcap aktual"""
    url = f"https://api.dexscreener.com/latest/dex/pairs/{chain}/{address}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    return pairs[0] if pairs else None
    except Exception:
        pass
    return None

def format_price(price_usd: float) -> str:
    if price_usd >= 1:
        return f"${price_usd:,.4f}"
    elif price_usd >= 0.0001:
        return f"${price_usd:.6f}"
    else:
        return f"${price_usd:.10f}"

def pct_emoji(v) -> str:
    try:
        return "🟢" if float(v) >= 0 else "🔴"
    except:
        return "⚪"



def format_dex_pair(pair: dict) -> str:
    base = pair.get("baseToken", {})
    symbol = base.get("symbol", "?")
    name = base.get("name", "?")
    chain = pair.get("chainId", "?")
    dex = pair.get("dexId", "?")
    price_usd = float(pair.get("priceUsd") or 0)

    pc = pair.get("priceChange", {})
    p5m  = float(pc.get("m5") or 0)
    p1h  = float(pc.get("h1") or 0)
    p6h  = float(pc.get("h6") or 0)
    p24h = float(pc.get("h24") or 0)

    vol24 = float((pair.get("volume") or {}).get("h24") or 0)
    liq_usd = float((pair.get("liquidity") or {}).get("usd") or 0)
    fdv = float(pair.get("fdv") or 0)
    mcap = float(pair.get("marketCap") or 0)

    txns = (pair.get("txns") or {}).get("h24") or {}
    buys = txns.get("buys", 0)
    sells = txns.get("sells", 0)
    pair_url = pair.get("url", "")

    msg  = f"🔍 *{symbol}* — {name}\n"
    msg += f"⛓ {chain.upper()} | 🏦 {dex}\n\n"
    msg += f"💰 Harga   : {format_price(price_usd)}\n"
    msg += f"📊 5m {pct_emoji(p5m)}{p5m:+.2f}% | 1h {pct_emoji(p1h)}{p1h:+.2f}%\n"
    msg += f"   6h {pct_emoji(p6h)}{p6h:+.2f}% | 24h {pct_emoji(p24h)}{p24h:+.2f}%\n\n"
    msg += f"💧 Liquidity : ${liq_usd:,.0f}\n"
    msg += f"📦 Volume 24h: ${vol24:,.0f}\n"
    if mcap > 0:
        msg += f"🏆 Market Cap: ${mcap:,.0f}\n"
    if fdv > 0:
        msg += f"📈 FDV       : ${fdv:,.0f}\n"
    msg += f"🔄 Txns 24h  : {buys} buy / {sells} sell\n"
    if pair_url:
        msg += f"\n🔗 {pair_url}"
    return msg

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
        "/removealert BTC 90000 — hapus spesifik\n"
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
        "/heatmap — coin paling ramai ditrading\n\n"
        "🦎 DEX / MEMECOIN\n"
        "/dex PEPE — cari token by nama\n"
        "/dex solana <contract> — by contract\n"
        "/dexalert <token> <mcap> — alert mcap (CA/nama/ticker)\n"
        "/listdexalerts — lihat dex alert\n"
        "/removedexalert <contract> — hapus alert\n"
        "/dextrending — token paling banyak di-boost\n"
        "/dexnew — token baru listing\n\n"
        "📣 CALL TRACKER\n"
        "/buy BTC 75000 TP 80000 SL 73000 — long/buy call\n"
        "/sell BTC 75000 TP 70000 SL 78000 — short/sell call\n"
        "/mycalls — lihat call aktif lo\n"
        "/allcalls — semua call aktif di grup\n"
        "/removecall BTC — hapus call\n"
        "/stats — statistik global bulan ini\n"
        "/stats me — statistik lo sendiri bulan ini\n"
        "/stats 2025-03 — statistik global bulan tertentu\n"
        "/stats me 2025-03 — statistik lo bulan tertentu\n"
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
    chat_id = update.effective_chat.id
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
    await notion_add(user_id, "price_alert", symbol, str(target), direction, chat_id=chat_id)
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
    chat_id = update.effective_chat.id
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
    await notion_add(user_id, "funding_watch", symbol, chat_id=chat_id)
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
    chat_id = update.effective_chat.id
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
    await notion_add(user_id, "oi_watch", symbol, chat_id=chat_id)
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
    gainers, _ = await get_top_movers(5, "spot")
    if not gainers:
        await update.message.reply_text("❌ Gagal mengambil data. Coba lagi.")
        return
    msg = "🏆 Top 5 Gainers (Spot 24h)\n\n"
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
    _, losers = await get_top_movers(5, "spot")
    if not losers:
        await update.message.reply_text("❌ Gagal mengambil data. Coba lagi.")
        return
    msg = "💀 Top 5 Losers (Spot 24h)\n\n"
    for i, t in enumerate(losers, 1):
        sym = t["symbol"].replace("USDT", "")
        pct = float(t["priceChangePercent"])
        price = float(t["lastPrice"])
        vol = float(t["quoteVolume"])
        msg += f"{i}. {sym}: {pct:.2f}% | ${price:,.4f}\n"
        msg += f"   Vol: ${vol/1e6:.0f}M\n"
    await update.message.reply_text(msg)

# ─────────────────────────────────────────
# DEX COMMANDS
# ─────────────────────────────────────────

async def dex_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /dex PEPE                    → search by nama/ticker
    /dex solana <contract>       → by contract address + chain
    /dex <contract>              → auto detect chain
    """
    if not context.args:
        await update.message.reply_text(
            "Format:\n"
            "/dex PEPE — cari by nama atau ticker\n"
            "/dex solana <contract> — by contract address\n"
            "/dex bsc <contract> — BSC chain\n"
            "/dex ethereum <contract> — ETH chain\n"
            "/dex base <contract> — Base chain"
        )
        return

    await update.message.reply_text("🔍 Mencari token...")

    chains = ["solana", "bsc", "ethereum", "base", "arbitrum", "polygon", "avax"]

    # cek apakah arg pertama adalah chain name
    if len(context.args) >= 2 and context.args[0].lower() in chains:
        chain = context.args[0].lower()
        address = context.args[1]
        pairs = await dex_by_contract(chain, address)
    elif len(context.args[0]) > 30:
        # contract address - auto detect chain
        pairs = await dex_by_contract("solana", context.args[0])
        if not pairs:
            pairs = await dex_by_contract("ethereum", context.args[0])
        if not pairs:
            pairs = await dex_by_contract("bsc", context.args[0])
    else:
        # Search by nama/ticker - coba dua query sekaligus:
        # 1. query asli (biasanya nama lengkap)
        # 2. query versi uppercase (ticker)
        query = " ".join(context.args)
        pairs = await dex_search_pairs(query)

        # Filter: prioritaskan yang symbol/name-nya exact match dulu
        query_upper = query.upper()
        exact = [
            p for p in pairs
            if p.get("baseToken", {}).get("symbol", "").upper() == query_upper
            or p.get("baseToken", {}).get("name", "").upper() == query.upper()
        ]

        if exact:
            # ada exact match → pakai itu, sort by volume
            pairs = exact
        elif pairs:
            # tidak ada exact match → pakai semua hasil, sort by volume
            # filter: buang pair yang symbolnya terlalu beda (hindari false positive)
            # hanya ambil yang symbol atau nama mengandung query
            filtered = [
                p for p in pairs
                if query_upper in p.get("baseToken", {}).get("symbol", "").upper()
                or query.lower() in p.get("baseToken", {}).get("name", "").lower()
            ]
            pairs = filtered if filtered else pairs

    if not pairs:
        await update.message.reply_text("❌ Token tidak ditemukan di DexScreener.")
        return

    # Ambil pair terbaik = volume tertinggi
    pairs_sorted = sorted(pairs, key=lambda p: float((p.get("volume") or {}).get("h24") or 0), reverse=True)
    best = pairs_sorted[0]

    await update.message.reply_text(format_dex_pair(best), parse_mode="Markdown")

    # Kalau ada beberapa pair (multi-dex), kasih info singkat
    if len(pairs_sorted) > 1:
        other_dexes = list({p.get("dexId", "") for p in pairs_sorted[1:4]})
        if other_dexes:
            await update.message.reply_text(
                f"ℹ️ Token ini juga trading di: {', '.join(other_dexes)}\n"
                f"Total {len(pairs_sorted)} pair ditemukan."
            )

async def dexalert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /dexalert <CA|nama|ticker> <mcap_target> [chain]
    Contoh:
      /dexalert 7xKXtg...sgAsU 1000000
      /dexalert jatevo 1000000
      /dexalert JTVO 1000000
    """
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if len(context.args) < 2:
        await update.message.reply_text(
            "Set alert marketcap untuk memecoin!\n\n"
            "Format: /dexalert <token> <target_mcap>\n\n"
            "Bisa pakai:\n"
            "• Contract address: /dexalert 7xKXtg...sgAsU 1000000\n"
            "• Nama token      : /dexalert jatevo 1000000\n"
            "• Ticker          : /dexalert JTVO 1000000\n\n"
            "Optional chain (default: auto-detect):\n"
            "/dexalert <token> <mcap> solana"
        )
        return

    input_token = context.args[0]
    try:
        target_mcap = float(context.args[1].replace(",", ""))
    except ValueError:
        await update.message.reply_text("❌ Target mcap harus angka. Contoh: 1000000 atau 1500000")
        return

    chain_arg = context.args[2].lower() if len(context.args) > 2 else "auto"

    await update.message.reply_text("🔍 Mencari token...")

    chains = ["solana", "bsc", "ethereum", "base", "arbitrum", "polygon", "avax"]
    is_contract = len(input_token) > 30

    pairs = []
    detected_chain = "unknown"
    contract = input_token  # default fallback

    if is_contract:
        # Input adalah contract address
        if chain_arg != "auto" and chain_arg in chains:
            pairs = await dex_by_contract(chain_arg, input_token)
            detected_chain = chain_arg
        else:
            for ch in ["solana", "ethereum", "bsc", "base", "arbitrum"]:
                pairs = await dex_by_contract(ch, input_token)
                if pairs:
                    detected_chain = ch
                    break
    else:
        # Input adalah nama atau ticker — search dulu
        query = input_token
        all_pairs = await dex_search_pairs(query)

        query_upper = query.upper()
        exact = [
            p for p in all_pairs
            if p.get("baseToken", {}).get("symbol", "").upper() == query_upper
            or p.get("baseToken", {}).get("name", "").upper() == query.upper()
        ]
        if exact:
            pairs = exact
        else:
            filtered = [
                p for p in all_pairs
                if query_upper in p.get("baseToken", {}).get("symbol", "").upper()
                or query.lower() in p.get("baseToken", {}).get("name", "").lower()
            ]
            pairs = filtered if filtered else all_pairs

        # Filter by chain kalau dispesifikkan
        if chain_arg != "auto" and chain_arg in chains:
            pairs = [p for p in pairs if p.get("chainId", "").lower() == chain_arg]

    if not pairs:
        await update.message.reply_text(
            "❌ Token tidak ditemukan di DexScreener.\n"
            "Coba gunakan contract address untuk hasil lebih akurat."
        )
        return

    # Ambil pair terbaik = volume tertinggi
    best = sorted(pairs, key=lambda p: float((p.get("volume") or {}).get("h24") or 0), reverse=True)[0]
    base = best.get("baseToken", {})
    symbol = base.get("symbol", "?")
    contract = base.get("address", input_token)
    detected_chain = best.get("chainId", detected_chain)

    current_mcap = float(best.get("marketCap") or 0)
    if current_mcap == 0:
        current_mcap = float(best.get("fdv") or 0)

    if current_mcap == 0:
        await update.message.reply_text(
            f"⚠️ Tidak bisa baca market cap untuk {symbol}.\n"
            "Mungkin token terlalu baru atau data belum tersedia."
        )
        return

    direction = "above" if target_mcap > current_mcap else "below"
    direction_str = f"{detected_chain}|{direction}"

    # Cek duplikat
    existing = await notion_query(user_id, "dex_mcap_alert")
    for row in existing:
        r = parse_row(row)
        if r["symbol"] == contract and r["target"] == str(target_mcap):
            await update.message.reply_text(f"⚠️ Alert {symbol} mcap ${target_mcap:,.0f} sudah ada!")
            return

    await notion_add(user_id, "dex_mcap_alert", contract, str(target_mcap), direction_str, chat_id=chat_id)

    arrow = "📈" if direction == "above" else "📉"
    short_ca = f"{contract[:8]}...{contract[-6:]}" if len(contract) > 14 else contract
    await update.message.reply_text(
        f"✅ DEX Market Cap Alert set!\n\n"
        f"🪙 Token  : {symbol}\n"
        f"⛓ Chain  : {detected_chain.upper()}\n"
        f"💰 MCap sekarang : ${current_mcap:,.0f}\n"
        f"{arrow} Target MCap    : ${target_mcap:,.0f}\n"
        f"📊 Contract      : {short_ca}"
    )

async def listdexalerts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rows = await notion_query(user_id, "dex_mcap_alert")
    if not rows:
        await update.message.reply_text("Tidak ada DEX mcap alert aktif.")
        return
    msg = "🔔 DEX Market Cap Alert aktif:\n\n"
    for row in rows:
        r = parse_row(row)
        contract = r["symbol"]
        target = float(r["target"])
        parts = r["direction"].split("|")
        chain = parts[0] if parts else "?"
        direction = parts[1] if len(parts) > 1 else "?"
        arrow = "📈" if direction == "above" else "📉"
        msg += f"{arrow} {contract[:8]}...{contract[-6:]}\n"
        msg += f"   Chain: {chain.upper()} | Target MCap: ${target:,.0f}\n\n"
    await update.message.reply_text(msg)

async def removedexalert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Format: /removedexalert <contract>")
        return
    contract = context.args[0]
    rows = await notion_query(user_id, "dex_mcap_alert")
    deleted = 0
    for row in rows:
        r = parse_row(row)
        if r["symbol"] == contract or r["symbol"].startswith(contract[:8]):
            await notion_delete(r["page_id"])
            deleted += 1
    if deleted:
        await update.message.reply_text(f"✅ {deleted} DEX alert dihapus.")
    else:
        await update.message.reply_text("❌ Alert tidak ditemukan.")

async def dextrending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔥 Fetching DEX trending...")
    tokens = await dex_trending_tokens()
    if not tokens:
        await update.message.reply_text("❌ Gagal mengambil data trending.")
        return
    msg = "🔥 DEX Trending (Most Boosted)\n\n"
    for i, t in enumerate(tokens[:10], 1):
        chain = t.get("chainId", "?").upper()
        addr = t.get("tokenAddress", "")
        short_addr = f"{addr[:6]}...{addr[-4:]}" if len(addr) > 10 else addr
        desc = t.get("description", "")[:40] if t.get("description") else ""
        amount = t.get("totalAmount", 0)
        url = t.get("url", "")
        msg += f"{i}. [{chain}] {short_addr}\n"
        if desc:
            msg += f"   {desc}\n"
        msg += f"   💎 Boost: {amount}\n"
        if url:
            msg += f"   🔗 {url}\n"
        msg += "\n"
    await update.message.reply_text(msg)

async def dexnew_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🆕 Fetching new listings...")
    tokens = await dex_new_listings()
    if not tokens:
        await update.message.reply_text("❌ Gagal mengambil data new listings.")
        return
    msg = "🆕 Token Baru di DexScreener\n\n"
    for i, t in enumerate(tokens[:10], 1):
        chain = t.get("chainId", "?").upper()
        addr = t.get("tokenAddress", "")
        short_addr = f"{addr[:6]}...{addr[-4:]}" if len(addr) > 10 else addr
        desc = t.get("description", "")[:50] if t.get("description") else "—"
        url = t.get("url", "")
        msg += f"{i}. [{chain}] {short_addr}\n"
        msg += f"   {desc}\n"
        if url:
            msg += f"   🔗 {url}\n"
        msg += "\n"
    await update.message.reply_text(msg)

async def notion_add_call(user_id: int, symbol: str, entry: float, tp: float, sl: float,
                          username: str, chat_id: int, status: str = "waiting", call_type: str = "buy"):
    """Simpan call tracker ke Notion."""
    import json
    name = f"call:{user_id}:{symbol}:{call_type}"
    data = json.dumps({
        "entry": entry, "tp": tp, "sl": sl,
        "username": username, "chat_id": chat_id,
        "status": status, "call_type": call_type
    })
    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "Name": {"title": [{"text": {"content": name}}]},
            "user_id": {"rich_text": [{"text": {"content": str(user_id)}}]},
            "type": {"select": {"name": "call_tracker"}},
            "symbol": {"rich_text": [{"text": {"content": symbol}}]},
            "target": {"rich_text": [{"text": {"content": data}}]},
            "direction": {"rich_text": [{"text": {"content": call_type}}]},
            "active": {"checkbox": True}
        }
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS, json=payload) as resp:
            return await resp.json()

async def notion_update_call_status(page_id: str, status: str, data: dict):
    """Update status call (waiting → active → closed)."""
    import json
    data["status"] = status
    # direction field menyimpan call_type (buy/sell), jangan di-overwrite dengan status
    call_type = data.get("call_type", "buy")
    async with aiohttp.ClientSession() as session:
        async with session.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=NOTION_HEADERS,
            json={
                "properties": {
                    "direction": {"rich_text": [{"text": {"content": call_type}}]},
                    "target": {"rich_text": [{"text": {"content": json.dumps(data)}}]},
                    "active": {"checkbox": status not in ("tp_hit", "sl_hit")}
                }
            }
        ) as resp:
            return await resp.json()

def parse_call(row: dict) -> dict | None:
    """Parse call tracker row dari Notion."""
    import json
    props = row["properties"]
    def txt(key):
        items = props.get(key, {}).get("rich_text", [])
        return items[0]["text"]["content"] if items else ""
    try:
        data = json.loads(txt("target"))
        return {
            "page_id": row["id"],
            "user_id": int(txt("user_id")),
            "symbol": txt("symbol"),
            "entry": float(data["entry"]),
            "tp": float(data["tp"]),
            "sl": float(data["sl"]),
            "username": data.get("username", "unknown"),
            "chat_id": int(data.get("chat_id", data.get("user_id", 0))),
            "status": data.get("status", "waiting"),
            "call_type": data.get("call_type", "buy"),
        }
    except Exception:
        return None

async def notion_save_call_history(c: dict, result: str, pnl_pct: float):
    """Simpan call yang sudah closed ke history database."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")
    closed_date = now.strftime("%Y-%m-%d")
    name = f"{result}:{c['symbol']}:{c['call_type']}:{closed_date}"
    payload = {
        "parent": {"database_id": "47f287ad128844b0b4911c6e6f983b16"},
        "properties": {
            "Name": {"title": [{"text": {"content": name}}]},
            "user_id": {"rich_text": [{"text": {"content": str(c["user_id"])}}]},
            "username": {"rich_text": [{"text": {"content": c["username"]}}]},
            "chat_id": {"rich_text": [{"text": {"content": str(c["chat_id"])}}]},
            "symbol": {"rich_text": [{"text": {"content": c["symbol"]}}]},
            "call_type": {"select": {"name": c.get("call_type", "buy")}},
            "entry": {"rich_text": [{"text": {"content": str(c["entry"])}}]},
            "tp": {"rich_text": [{"text": {"content": str(c["tp"])}}]},
            "sl": {"rich_text": [{"text": {"content": str(c["sl"])}}]},
            "result": {"select": {"name": result}},
            "pnl_pct": {"rich_text": [{"text": {"content": f"{pnl_pct:.2f}"}}]},
            "closed_date": {"date": {"start": closed_date}},
            "month": {"rich_text": [{"text": {"content": month}}]},
        }
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS, json=payload) as resp:
            return await resp.json()

async def notion_query_history_by_month(month: str) -> list:
    """Query call history berdasarkan bulan (format: YYYY-MM)."""
    payload = {
        "filter": {
            "property": "month",
            "rich_text": {"equals": month}
        },
        "page_size": 100
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"https://api.notion.com/v1/databases/47f287ad128844b0b4911c6e6f983b16/query",
            headers=NOTION_HEADERS, json=payload
        ) as resp:
            data = await resp.json()
            return data.get("results", [])

# ─────────────────────────────────────────
# CALL TRACKER COMMANDS
# ─────────────────────────────────────────

async def _process_call(update: Update, context, call_type: str):
    """Shared logic untuk /buy dan /sell."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    username = update.effective_user.username or update.effective_user.first_name or str(user_id)
    is_long = (call_type == "buy")

    if len(context.args) < 6:
        if is_long:
            await update.message.reply_text(
                "📈 Format buy/long call:\n"
                "/buy BTC 75000 TP 80000 SL 73000\n\n"
                "TP harus di ATAS entry, SL harus di BAWAH entry."
            )
        else:
            await update.message.reply_text(
                "📉 Format sell/short call:\n"
                "/sell BTC 75000 TP 70000 SL 78000\n\n"
                "TP harus di BAWAH entry, SL harus di ATAS entry."
            )
        return

    symbol = context.args[0].upper()
    try:
        entry_arg = context.args[1].lower()
        if context.args[2].upper() != "TP" or context.args[4].upper() != "SL":
            raise ValueError
        tp = float(context.args[3])
        sl = float(context.args[5])
    except (ValueError, IndexError):
        cmd = "buy" if is_long else "sell"
        await update.message.reply_text(
            f"❌ Format salah.\n"
            f"Contoh: /{cmd} BTC 75000 TP 80000 SL 73000\n"
            f"Atau pakai harga sekarang: /{cmd} BTC now TP 80000 SL 73000"
        )
        return

    # Cek harga sekarang dulu (selalu butuh untuk validasi dan current price display)
    current = await get_price(symbol)
    if current is None:
        await update.message.reply_text(f"❌ {symbol} tidak ditemukan di Binance.")
        return

    # Entry: angka spesifik atau keyword now/haka = harga sekarang
    use_current_price = entry_arg in ("now", "haka", "skrg", "market")
    if use_current_price:
        entry = current
    else:
        try:
            entry = float(entry_arg)
        except ValueError:
            cmd = "buy" if is_long else "sell"
            await update.message.reply_text(
                f"❌ Entry harus angka atau 'now'.\n"
                f"Contoh: /{cmd} BTC 75000 TP 80000 SL 73000\n"
                f"Atau: /{cmd} BTC now TP 80000 SL 73000"
            )
            return

    # Validasi arah sesuai tipe call
    if is_long:
        if tp <= entry:
            await update.message.reply_text("❌ Untuk LONG/BUY, TP harus di atas entry.")
            return
        if sl >= entry:
            await update.message.reply_text("❌ Untuk LONG/BUY, SL harus di bawah entry.")
            return
    else:
        if tp >= entry:
            await update.message.reply_text("❌ Untuk SHORT/SELL, TP harus di bawah entry.")
            return
        if sl <= entry:
            await update.message.reply_text("❌ Untuk SHORT/SELL, SL harus di atas entry.")
            return

    # Hapus call lama user untuk coin + tipe yang sama
    existing = await notion_query(user_id, "call_tracker")
    for row in existing:
        c = parse_call(row)
        if c and c["symbol"] == symbol and c.get("call_type", "buy") == call_type:
            await notion_delete(c["page_id"])

    tp_pct = ((tp - entry) / entry) * 100
    sl_pct = ((sl - entry) / entry) * 100

    # Untuk short: balik tanda — TP di bawah = profit, SL di atas = loss
    if not is_long:
        tp_pct = -tp_pct
        sl_pct = -sl_pct

    rr = abs(tp_pct / sl_pct) if sl_pct != 0 else 0

    # Status: now/haka → langsung active, selainnya cek posisi harga
    if use_current_price:
        status = "active"
    elif is_long:
        status = "active" if current <= entry else "waiting"
    else:
        status = "active" if current >= entry else "waiting"

    await notion_add_call(user_id, symbol, entry, tp, sl, username, chat_id, status, call_type=call_type)

    direction_label = "LONG 📈" if is_long else "SHORT 📉"
    entry_label = f"${entry:,.4f} (harga sekarang)" if use_current_price else f"${entry:,.4f}"
    status_label = "✅ ACTIVE — entry di harga sekarang!" if use_current_price else \
                   "✅ ACTIVE — harga sudah di entry!" if status == "active" else \
                   "⏳ WAITING — menunggu harga sentuh entry"

    await update.message.reply_text(
        f"📣 {'BUY' if is_long else 'SELL'} CALL — {direction_label}\n"
        f"👤 @{username}\n\n"
        f"🪙 Coin   : {symbol}\n"
        f"🎯 Entry  : {entry_label}\n"
        f"🟢 TP     : ${tp:,.4f} ({tp_pct:+.2f}%)\n"
        f"🔴 SL     : ${sl:,.4f} ({sl_pct:+.2f}%)\n"
        f"⚖️ R/R    : 1:{rr:.2f}\n\n"
        f"💰 Harga skrg: ${current:,.4f}\n"
        f"📊 Status: {status_label}"
    )

async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/buy BTC 75000 TP 80000 SL 73000 — Long/Spot buy call"""
    await _process_call(update, context, "buy")

async def sell_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/sell BTC 75000 TP 70000 SL 78000 — Short/Sell call"""
    await _process_call(update, context, "sell")

async def mycalls_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lihat semua call aktif milik user."""
    user_id = update.effective_user.id
    rows = await notion_query(user_id, "call_tracker")
    if not rows:
        await update.message.reply_text("Tidak ada call aktif.")
        return

    msg = "📣 Call aktif lo:\n\n"
    for row in rows:
        c = parse_call(row)
        if not c:
            continue
        is_long = c.get("call_type", "buy") == "buy"
        direction = "LONG 📈" if is_long else "SHORT 📉"
        tp_pct = ((c["tp"] - c["entry"]) / c["entry"]) * 100
        sl_pct = ((c["sl"] - c["entry"]) / c["entry"]) * 100
        if not is_long:
            tp_pct = -tp_pct
            sl_pct = -sl_pct
        price = await get_price(c["symbol"])
        current_pnl = ""
        if price and c["status"] == "active":
            pnl = ((price - c["entry"]) / c["entry"]) * 100
            if not is_long:
                pnl = -pnl
            pnl_emoji = "🟢" if pnl >= 0 else "🔴"
            current_pnl = f"\n{pnl_emoji} PnL skrg: {pnl:+.2f}%"

        status_map = {"waiting": "⏳ Waiting", "active": "✅ Active"}
        status_label = status_map.get(c["status"], c["status"])

        msg += (
            f"🪙 {c['symbol']} — {direction}\n"
            f"   Entry: ${c['entry']:,.4f} | TP: ${c['tp']:,.4f} ({tp_pct:+.2f}%) | SL: ${c['sl']:,.4f} ({sl_pct:+.2f}%)\n"
            f"   Status: {status_label}{current_pnl}\n\n"
        )
    await update.message.reply_text(msg)

async def removecall_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hapus call aktif. /removecall BTC atau /removecall BTC sell"""
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(
            "Format:\n"
            "/removecall BTC — hapus semua call BTC\n"
            "/removecall BTC buy — hapus call buy BTC\n"
            "/removecall BTC sell — hapus call sell BTC"
        )
        return
    symbol = context.args[0].upper()
    call_type_filter = context.args[1].lower() if len(context.args) > 1 else None
    rows = await notion_query(user_id, "call_tracker")
    deleted = 0
    for row in rows:
        c = parse_call(row)
        if not c or c["symbol"] != symbol:
            continue
        if call_type_filter and c.get("call_type", "buy") != call_type_filter:
            continue
        await notion_delete(c["page_id"])
        deleted += 1
    if deleted:
        await update.message.reply_text(f"✅ {deleted} call {symbol} dihapus.")
    else:
        await update.message.reply_text(f"❌ Tidak ada call aktif untuk {symbol}.")

async def allcalls_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lihat semua call aktif di chat ini (dari semua user)."""
    rows = await notion_query_all("call_tracker")
    chat_id = update.effective_chat.id

    # Filter berdasarkan chat_id
    chat_calls = []
    for row in rows:
        c = parse_call(row)
        if c and c["chat_id"] == chat_id:
            chat_calls.append(c)

    if not chat_calls:
        await update.message.reply_text("Tidak ada call aktif di grup ini.")
        return

    msg = "📣 Semua call aktif di sini:\n\n"
    for c in chat_calls:
        is_long = c.get("call_type", "buy") == "buy"
        direction = "LONG 📈" if is_long else "SHORT 📉"
        tp_pct = ((c["tp"] - c["entry"]) / c["entry"]) * 100
        sl_pct = ((c["sl"] - c["entry"]) / c["entry"]) * 100
        if not is_long:
            tp_pct = -tp_pct
            sl_pct = -sl_pct
        status_map = {"waiting": "⏳", "active": "✅"}
        status_label = status_map.get(c["status"], "?")
        msg += (
            f"{status_label} @{c['username']} — {c['symbol']} {direction}\n"
            f"   Entry: ${c['entry']:,.4f} | TP: {tp_pct:+.2f}% | SL: {sl_pct:+.2f}%\n\n"
        )
    await update.message.reply_text(msg)

# ─────────────────────────────────────────
# BACKGROUND JOBS
# ─────────────────────────────────────────

async def check_calls(context: ContextTypes.DEFAULT_TYPE):
    """Monitor call tracker — cek entry hit, TP hit, SL hit."""
    rows = await notion_query_all("call_tracker")
    for row in rows:
        c = parse_call(row)
        if not c:
            continue

        price = await get_price(c["symbol"])
        if price is None:
            continue

        is_long = c.get("call_type", "buy") == "buy"
        tp_pct = ((c["tp"] - c["entry"]) / c["entry"]) * 100
        sl_pct = ((c["sl"] - c["entry"]) / c["entry"]) * 100
        if not is_long:
            tp_pct = -tp_pct
            sl_pct = -sl_pct

        async def notify(text, _c=c):
            try:
                await context.bot.send_message(chat_id=_c["chat_id"], text=text)
            except Exception:
                try:
                    await context.bot.send_message(chat_id=_c["user_id"], text=text)
                except Exception:
                    pass

        if c["status"] == "waiting":
            # Long: entry hit kalau harga turun ke entry
            # Short: entry hit kalau harga naik ke entry
            entry_hit = (is_long and price <= c["entry"]) or (not is_long and price >= c["entry"])
            if entry_hit:
                await notion_update_call_status(c["page_id"], "active", {
                    "entry": c["entry"], "tp": c["tp"], "sl": c["sl"],
                    "username": c["username"], "chat_id": c["chat_id"],
                    "call_type": c.get("call_type", "buy")
                })
                await notify(
                    f"✅ ENTRY HIT!\n\n"
                    f"📣 Call @{c['username']}\n"
                    f"🪙 {c['symbol']} {'LONG 📈' if is_long else 'SHORT 📉'}\n\n"
                    f"🎯 Entry  : ${c['entry']:,.4f}\n"
                    f"🟢 TP     : ${c['tp']:,.4f} ({tp_pct:+.2f}%)\n"
                    f"🔴 SL     : ${c['sl']:,.4f} ({sl_pct:+.2f}%)\n"
                    f"💰 Harga  : ${price:,.4f}"
                )

        elif c["status"] == "active":
            tp_hit = (is_long and price >= c["tp"]) or (not is_long and price <= c["tp"])
            sl_hit = (is_long and price <= c["sl"]) or (not is_long and price >= c["sl"])

            if tp_hit:
                await notion_save_call_history(c, "tp_hit", tp_pct)
                await notion_delete(c["page_id"])
                await notify(
                    f"🚀 TP HIT! PROFIT!\n\n"
                    f"📣 Call @{c['username']}\n"
                    f"🪙 {c['symbol']} {'LONG 📈' if is_long else 'SHORT 📉'}\n\n"
                    f"🎯 Entry : ${c['entry']:,.4f}\n"
                    f"🟢 TP    : ${c['tp']:,.4f}\n"
                    f"💰 Harga : ${price:,.4f}\n\n"
                    f"✅ Profit: {tp_pct:+.2f}%"
                )
            elif sl_hit:
                await notion_save_call_history(c, "sl_hit", -abs(sl_pct))
                await notion_delete(c["page_id"])
                await notify(
                    f"🔴 SL HIT! STOP LOSS!\n\n"
                    f"📣 Call @{c['username']}\n"
                    f"🪙 {c['symbol']} {'LONG 📉' if is_long else 'SHORT 📈'}\n\n"
                    f"🎯 Entry : ${c['entry']:,.4f}\n"
                    f"🔴 SL    : ${c['sl']:,.4f}\n"
                    f"💰 Harga : ${price:,.4f}\n\n"
                    f"❌ Loss: {sl_pct:+.2f}%"
                )

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stats, /stats me, /stats @budi, /stats 2025-03, /stats me 2025-03, /stats @budi 2025-03"""
    from datetime import datetime, timezone

    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name or str(user_id)

    args = context.args or []

    # Cek apakah ada @username
    target_username = None
    for a in args:
        if a.startswith("@"):
            target_username = a[1:].lower()
            break

    args_lower = [a.lower() for a in args if not a.startswith("@")]
    is_me = "me" in args_lower

    # Extract bulan
    month = None
    for a in args_lower:
        if a != "me":
            try:
                datetime.strptime(a, "%Y-%m")
                month = a
                break
            except ValueError:
                await update.message.reply_text(
                    "❌ Format salah.\n"
                    "Contoh:\n"
                    "/stats — global bulan ini\n"
                    "/stats me — stats lo sendiri\n"
                    "/stats @budi — stats si budi\n"
                    "/stats 2025-03 — global bulan tertentu\n"
                    "/stats me 2025-03 — stats lo bulan tertentu\n"
                    "/stats @budi 2025-03 — stats budi bulan tertentu"
                )
                return
    if not month:
        month = datetime.now(timezone.utc).strftime("%Y-%m")

    rows = await notion_query_history_by_month(month)

    def get_prop_txt(row, key):
        return (row["properties"].get(key, {}).get("rich_text", [{}])[0]
                .get("text", {}).get("content", ""))

    # Filter rows sesuai mode
    if target_username:
        rows = [r for r in rows if get_prop_txt(r, "username").lower() == target_username]
        display_name = f"@{target_username}"
        personal = True
    elif is_me:
        rows = [r for r in rows if get_prop_txt(r, "user_id") == str(user_id)]
        display_name = f"@{username}"
        personal = True
    else:
        display_name = None
        personal = False

    if not rows:
        month_label = datetime.strptime(month, "%Y-%m").strftime("%B %Y")
        if display_name:
            await update.message.reply_text(f"📊 {display_name} belum ada call yang closed di bulan {month_label}.")
        else:
            await update.message.reply_text(f"📊 Belum ada call yang closed di bulan {month_label}.")
        return

    # Parse semua rows
    calls = []
    for row in rows:
        props = row["properties"]
        def txt(k, p=props):
            items = p.get(k, {}).get("rich_text", [])
            return items[0]["text"]["content"] if items else ""
        def sel(k, p=props):
            s = p.get(k, {}).get("select")
            return s["name"] if s else ""
        calls.append({
            "username": txt("username"),
            "symbol": txt("symbol"),
            "call_type": sel("call_type"),
            "result": sel("result"),
            "pnl_pct": float(txt("pnl_pct") or "0"),
        })

    total = len(calls)
    wins = [c for c in calls if c["result"] == "tp_hit"]
    losses = [c for c in calls if c["result"] == "sl_hit"]
    win_rate = (len(wins) / total * 100) if total > 0 else 0
    avg_profit = sum(c["pnl_pct"] for c in wins) / len(wins) if wins else 0
    avg_loss = sum(c["pnl_pct"] for c in losses) / len(losses) if losses else 0
    total_pnl = sum(c["pnl_pct"] for c in calls)

    month_name = datetime.strptime(month, "%Y-%m").strftime("%B %Y")

    if personal:
        label = display_name
        msg = (
            f"📊 STATISTIK {label} — {month_name}\n"
            f"{'─' * 28}\n\n"
            f"📈 Total Call  : {total}\n"
            f"✅ TP Hit      : {len(wins)}\n"
            f"❌ SL Hit      : {len(losses)}\n"
            f"🎯 Win Rate    : {win_rate:.1f}%\n\n"
            f"💰 Avg Profit  : {avg_profit:+.2f}%\n"
            f"🩸 Avg Loss    : {avg_loss:+.2f}%\n"
            f"📉 Total PnL   : {total_pnl:+.2f}%\n\n"
            f"📋 Detail:\n"
        )
        for c in sorted(calls, key=lambda x: x["pnl_pct"], reverse=True):
            emoji = "✅" if c["result"] == "tp_hit" else "❌"
            direction = "📈" if c["call_type"] == "buy" else "📉"
            msg += f"   {emoji} {c['symbol']} {direction} {c['pnl_pct']:+.2f}%\n"
    else:
        # Mode global — per trader breakdown
        user_stats = {}
        for c in calls:
            u = c["username"]
            if u not in user_stats:
                user_stats[u] = {"win": 0, "loss": 0}
            if c["result"] == "tp_hit":
                user_stats[u]["win"] += 1
            else:
                user_stats[u]["loss"] += 1

        msg = (
            f"📊 STATISTIK GLOBAL — {month_name}\n"
            f"{'─' * 28}\n\n"
            f"📈 Total Call  : {total}\n"
            f"✅ TP Hit      : {len(wins)}\n"
            f"❌ SL Hit      : {len(losses)}\n"
            f"🎯 Win Rate    : {win_rate:.1f}%\n\n"
            f"💰 Avg Profit  : {avg_profit:+.2f}%\n"
            f"🩸 Avg Loss    : {avg_loss:+.2f}%\n"
            f"📉 Total PnL   : {total_pnl:+.2f}%\n\n"
            f"👥 Per Trader:\n"
        )
        for uname, s in sorted(user_stats.items(), key=lambda x: sum(c["pnl_pct"] for c in calls if c["username"] == x[0]), reverse=True):
            total_u = s["win"] + s["loss"]
            wr_u = s["win"] / total_u * 100 if total_u > 0 else 0
            roi_u = sum(c["pnl_pct"] for c in calls if c["username"] == uname)
            msg += f"   @{uname}: {s['win']}W/{s['loss']}L ({wr_u:.0f}%) ROI: {roi_u:+.2f}%\n"

        msg += f"\n📋 Detail:\n"
        for c in sorted(calls, key=lambda x: x["pnl_pct"], reverse=True):
            emoji = "✅" if c["result"] == "tp_hit" else "❌"
            direction = "📈" if c["call_type"] == "buy" else "📉"
            msg += f"   {emoji} @{c['username']} {c['symbol']} {direction} {c['pnl_pct']:+.2f}%\n"

    await update.message.reply_text(msg)


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
            try:
                await context.bot.send_message(
                    chat_id=r["chat_id"],
                    text=f"🚨 PRICE ALERT KENA!\n\n"
                         f"{arrow} {r['symbol']} sudah sentuh ${target:,.6f}\n"
                         f"💰 Harga sekarang: ${price:,.6f}"
                )
            except Exception:
                # fallback ke user personal jika grup gagal
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
                    try:
                        await context.bot.send_message(
                            chat_id=r2["chat_id"],
                            text=f"⚡ FUNDING SPIKE!\n\n"
                                 f"📊 {symbol}: {pct:+.4f}%\n"
                                 f"{funding_status(rate)}"
                        )
                    except Exception:
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
                        try:
                            await context.bot.send_message(
                                chat_id=r2["chat_id"],
                                text=f"⚡ OI SPIKE!\n\n"
                                     f"{arrow} {symbol} OI berubah {change_pct:+.2f}%\n"
                                     f"OI sekarang: {oi:,.2f}\n"
                                     f"OI sebelumnya: {last_oi:,.2f}"
                            )
                        except Exception:
                            await context.bot.send_message(
                                chat_id=r2["user_id"],
                                text=f"⚡ OI SPIKE!\n\n"
                                     f"{arrow} {symbol} OI berubah {change_pct:+.2f}%\n"
                                     f"OI sekarang: {oi:,.2f}\n"
                                     f"OI sebelumnya: {last_oi:,.2f}"
                            )
        oi_cache[symbol] = oi

async def check_dex_mcap_alerts(context: ContextTypes.DEFAULT_TYPE):
    rows = await notion_query_all("dex_mcap_alert")
    for row in rows:
        r = parse_row(row)
        contract = r["symbol"]
        target_mcap = float(r["target"])
        parts = r["direction"].split("|")
        chain = parts[0] if parts else "solana"
        direction = parts[1] if len(parts) > 1 else "above"

        pairs = await dex_by_contract(chain, contract)
        if not pairs:
            continue

        best = sorted(pairs, key=lambda p: float((p.get("volume") or {}).get("h24") or 0), reverse=True)[0]
        base = best.get("baseToken", {})
        symbol = base.get("symbol", contract[:8])
        current_mcap = float(best.get("marketCap") or best.get("fdv") or 0)

        if current_mcap == 0:
            continue

        hit = (direction == "above" and current_mcap >= target_mcap) or               (direction == "below" and current_mcap <= target_mcap)

        if hit:
            await notion_delete(r["page_id"])
            arrow = "📈" if direction == "above" else "📉"
            try:
                await context.bot.send_message(
                    chat_id=r["chat_id"],
                    text=f"🚨 DEX MCAP ALERT KENA!\n\n"
                         f"{arrow} {symbol} marketcap sentuh ${target_mcap:,.0f}\n"
                         f"💰 MCap sekarang: ${current_mcap:,.0f}\n"
                         f"⛓ Chain: {chain.upper()}\n"
                         f"🔗 {best.get('url', '')}"
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=r["user_id"],
                    text=f"🚨 DEX MCAP ALERT KENA!\n\n"
                         f"{arrow} {symbol} marketcap sentuh ${target_mcap:,.0f}\n"
                         f"💰 MCap sekarang: ${current_mcap:,.0f}\n"
                         f"⛓ Chain: {chain.upper()}\n"
                         f"🔗 {best.get('url', '')}"
                )

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
            try:
                await context.bot.send_message(
                    chat_id=r["chat_id"],
                    text=f"🚨 PRICE ALERT KENA!\n\n"
                         f"{arrow} {r['symbol']} sudah sentuh ${target:,.6f}\n"
                         f"💰 Harga sekarang: ${price:,.6f}"
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=r["user_id"],
                    text=f"🚨 PRICE ALERT KENA!\n\n"
                         f"{arrow} {r['symbol']} sudah sentuh ${target:,.6f}\n"
                         f"💰 Harga sekarang: ${price:,.6f}"
                )

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

    app.add_handler(CommandHandler("dex", dex_cmd))
    app.add_handler(CommandHandler("dexalert", dexalert_cmd))
    app.add_handler(CommandHandler("listdexalerts", listdexalerts_cmd))
    app.add_handler(CommandHandler("removedexalert", removedexalert_cmd))
    app.add_handler(CommandHandler("dextrending", dextrending_cmd))
    app.add_handler(CommandHandler("dexnew", dexnew_cmd))

    app.add_handler(CommandHandler("buy", buy_cmd))
    app.add_handler(CommandHandler("sell", sell_cmd))
    app.add_handler(CommandHandler("mycalls", mycalls_cmd))
    app.add_handler(CommandHandler("allcalls", allcalls_cmd))
    app.add_handler(CommandHandler("removecall", removecall_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))

    app.job_queue.run_repeating(check_price_alerts, interval=60, first=10)
    app.job_queue.run_repeating(check_funding_spikes, interval=3600, first=30)
    app.job_queue.run_repeating(check_oi_spikes, interval=3600, first=30)
    app.job_queue.run_repeating(check_dex_mcap_alerts, interval=300, first=20)
    app.job_queue.run_repeating(check_calls, interval=60, first=15)

    print("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
