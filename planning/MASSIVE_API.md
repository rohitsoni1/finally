# Massive (formerly Polygon.io) API — Reference for FinAlly

> Research notes on the Massive REST API for retrieving **real-time / delayed** and **end-of-day** stock prices for multiple tickers. Polygon.io rebranded to **Massive** (massive.com) on 30 Oct 2025. Existing Polygon keys keep working, and `api.polygon.io` stays up alongside `api.massive.com` during the transition.
>
> Sources: official docs at `https://massive.com/docs/rest/...` (see the [index](https://massive.com/docs/llms.txt)) and the official Python client at [github.com/massive-com/client-python](https://github.com/massive-com/client-python), read November 2026. Items marked **(unverified)** could not be confirmed from those sources.

---

## 1. Basics

| Item | Value |
|---|---|
| Base URL | `https://api.massive.com` (legacy `https://api.polygon.io` still accepted) |
| Auth (option A) | Query parameter `?apiKey=YOUR_KEY` |
| Auth (option B) | Header `Authorization: Bearer YOUR_KEY` (used by the Python SDK) |
| Response format | JSON. Every response has a `status` field (`"OK"`) and usually a `request_id` |
| Pagination | List endpoints return `next_url`. Follow it (with your key) to get the next page |
| Ticker case | Case-sensitive. Always send upper-case (`AAPL`, not `aapl`) |

### Getting an API key (free tier included)

An account is required even for the free **Stocks Basic** plan, because every request must carry an API key.
1. Sign up at <https://massive.com/dashboard/signup> (free; no credit card for the Basic tier).
2. Copy your key from the dashboard's **API Keys** page.
3. Put it in the project-root `.env` as `MASSIVE_API_KEY=...`. Leave it empty to use the simulator instead.

Existing Polygon.io accounts and keys keep working after the rebrand.

### Timestamp units (important)

| Field | Unit |
|---|---|
| Aggregate bar `t` (`/v2/aggs/...`) | Unix **milliseconds** |
| Snapshot `updated` | Unix **nanoseconds** |
| Last trade `t`, `y`, `f` | Unix **nanoseconds** |

### Plans and data freshness

| Plan | Price (approx) | Freshness | Snapshots | Last Trade | Aggregates history |
|---|---|---|---|---|---|
| **Stocks Basic (free)** | $0 | **End-of-day** | ❌ | ❌ | 2 years |
| Stocks Starter | ~$29/mo | 15-min delayed | ✅ | ❌ | 5 years |
| Stocks Developer | ~$79/mo | 15-min delayed | ✅ | ✅ | 10 years |
| Stocks Advanced | ~$199/mo | **Real-time** | ✅ | ✅ | All (since 2003-09-10) |
| Business | custom | Real-time | ✅ (+ `fmv`) | with Expansion | All |

**Rate limits:** the free tier is limited to **5 requests/minute**. Throttled requests get HTTP `429`. Paid individual plans are advertised as "unlimited" under a fair-use policy. Rate limits are **not** stated on the endpoint doc pages; these figures come from Massive's pricing material and third-party summaries.

> **Design consequence for FinAlly:** a free-tier key **cannot call the snapshot endpoints**, so "live" prices on the free tier are really end-of-day closes. The free-tier fallback is the **grouped daily** endpoint, which returns every ticker's bar in **one call**. See §4 and `MARKET_INTERFACE.md`.

---

## 2. Endpoints for FinAlly

### 2.1 Full Market Snapshot, multiple tickers in one call ⭐ (primary live source)

```
GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT,TSLA
```

| Param | Type | Notes |
|---|---|---|
| `tickers` | comma-separated string | Optional, case-sensitive. Empty returns **all** ~10k tickers. No documented maximum count |
| `include_otc` | bool | Default `false` |

**Plans:** Starter and above (not Basic). 15-min delayed on Starter/Developer, real-time on Advanced+.
**Note:** snapshot data is cleared at 3:30 AM ET and repopulates from about 4:00 AM ET.

**Response (trimmed):**

```json
{
  "status": "OK",
  "count": 1,
  "tickers": [
    {
      "ticker": "AAPL",
      "todaysChange": 1.23,
      "todaysChangePerc": 0.65,
      "updated": 1605192894630916600,
      "day":     {"o": 189.1, "h": 191.2, "l": 188.7, "c": 190.4, "v": 51234567, "vw": 190.02},
      "prevDay": {"o": 188.0, "h": 189.9, "l": 187.5, "c": 189.17, "v": 48765432, "vw": 188.8},
      "min":     {"o": 190.3, "h": 190.5, "l": 190.2, "c": 190.4, "v": 120345, "vw": 190.38,
                  "av": 51234567, "n": 812, "t": 1605192840000},
      "lastTrade": {"p": 190.41, "s": 100, "x": 4, "i": "118749", "c": [37], "t": 1605192894630916600},
      "lastQuote": {"p": 190.40, "s": 2, "P": 190.42, "S": 3, "t": 1605192894630916600}
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `lastTrade.p` | Last trade price. **Use this as the current price** |
| `day.c` | Today's close so far (fallback if `lastTrade` is missing) |
| `prevDay.c` | Previous session close. **Reference price for daily change %** |
| `todaysChange` / `todaysChangePerc` | Change versus `prevDay.c` |
| `min` | Latest minute bar |
| `lastQuote.p` / `lastQuote.P` | Bid / ask price (lowercase = bid, uppercase = ask) |
| `updated` | Last update time (ns) |

**curl:**

```bash
curl -s "https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT" \
  -H "Authorization: Bearer $MASSIVE_API_KEY" | jq '.tickers[] | {ticker, price: .lastTrade.p, prev: .prevDay.c}'
```

### 2.2 Single Ticker Snapshot

```
GET /v2/snapshot/locale/us/markets/stocks/tickers/{stocksTicker}
```

Returns `{"status","request_id","ticker": { ...same object as above... }}`. Same plan gating. Not needed by FinAlly because the multi-ticker form covers it.

### 2.3 Unified Snapshot (multi-asset alternative)

```
GET /v3/snapshot?ticker.any_of=AAPL,MSFT&limit=250
```

- `ticker.any_of`: up to **250** tickers. `limit`: default 10, max 250. Paginated via `next_url`.
- Per result: `ticker`, `type` (`stocks|options|fx|crypto|indices`), `name`, `market_status` (`open|closed|early_trading|late_trading`), `last_trade{price,size,exchange,sip_timestamp}`, `last_quote{bid,ask,midpoint,...}`, `session{open,high,low,close,previous_close,change,change_percent,volume,...}`.
- An unknown ticker comes back **inside** a 200 response as `{"ticker":"XXXX","error":"NOT_FOUND","message":"Ticker not found."}`.
- Starter and above. This is useful later if FinAlly adds crypto/FX (`X:BTCUSD`, `C:EURUSD`).

### 2.4 Daily Market Summary (grouped daily) ⭐ (free-tier, end-of-day for all tickers)

```
GET /v2/aggs/grouped/locale/us/market/stocks/{date}     # date = YYYY-MM-DD
```

| Param | Notes |
|---|---|
| `adjusted` | Split-adjusted, default `true` |
| `include_otc` | Default `false` |

Returns **one bar per ticker for the whole US market in a single call**. Filter it client-side to the watchlist. **Available on all plans, including Basic.** For a date with no session (weekend or holiday) the result is empty or `resultsCount: 0`, so walk back to the previous weekday.

```json
{
  "status": "OK", "adjusted": true, "queryCount": 10342, "resultsCount": 10342,
  "results": [
    {"T": "AAPL", "o": 188.0, "h": 191.2, "l": 187.5, "c": 190.4, "v": 51234567, "vw": 190.02, "n": 612345, "t": 1730923200000}
  ]
}
```

`T` = ticker, `o/h/l/c` = OHLC, `v` = volume, `vw` = VWAP, `n` = number of trades, `t` = ms timestamp.

### 2.5 Previous Day Bar (per ticker)

```
GET /v2/aggs/ticker/{stocksTicker}/prev
```

All plans. Response: `{"ticker":"AAPL","resultsCount":1,"results":[{"T":"AAPL","o":..,"h":..,"l":..,"c":115.97,"v":..,"vw":..,"t":1605042000000}]}`. You need one call per ticker, so it is **not suitable** for a 10-ticker free-tier watchlist (2 minutes of quota). Prefer grouped daily.

### 2.6 Custom Bars / Aggregates (history, charts)

```
GET /v2/aggs/ticker/{stocksTicker}/range/{multiplier}/{timespan}/{from}/{to}
     ?adjusted=true&sort=asc&limit=5000
```

- `timespan`: `minute | hour | day | week | month | quarter | year`. `from`/`to`: `YYYY-MM-DD` or ms timestamp, both **inclusive**.
- `limit`: default 5000, max 50000 (limits the base aggregates scanned, not the output rows). Paginates via `next_url`.
- Results: `[{o,h,l,c,v,vw,n,t}]`, where `t` = ms start of the bar. No bar is emitted for an interval with no trades.
- All plans (history depth varies by plan). Could seed a 1-year daily chart for a selected ticker. **Not required by the current PLAN**, which accumulates chart data from SSE.

### 2.7 Daily Ticker Summary (open/close for one date)

```
GET /v1/open-close/{stocksTicker}/{date}
```

Response: `{"symbol":"AAPL","from":"2023-01-09","open":324.66,"high":326.2,"low":322.3,"close":325.12,"volume":26122646,"preMarket":324.5,"afterHours":322.1,"status":"OK"}`. All plans. One call per ticker.

### 2.8 Last Trade (per ticker)

```
GET /v2/last/trade/{stocksTicker}
```

`results` is an object: `{T, p (price), s (size), t (SIP ns), x (exchange), c (conditions), i, q, z (tape)}`. **Developer+ only (not Starter).** One call per ticker, so the multi-ticker snapshot is strictly better for FinAlly.

---

## 3. Python client (`massive`)

```bash
uv add massive        # or: pip install -U massive
```

- Import: `from massive import RESTClient`. Requires Python ≥ 3.10.
- **Synchronous** (urllib3). In FastAPI, call it via `asyncio.to_thread(...)` so it never blocks the event loop.
- API key: the constructor arg `api_key`, or env var `MASSIVE_API_KEY`. **Gotcha:** the env var is read as a default argument **at import time**, so in FinAlly always pass `api_key=` explicitly. An empty string is not rejected by the client; it fails later as an HTTP error.
- Built-in retries: urllib3 `Retry(total=3, backoff_factor=0.1)` on `413, 429, 499, 500, 502, 503, 504`.
- Errors: `massive.exceptions.AuthError` (key is `None`) and `massive.exceptions.BadResponse` (any final non-200; the message is the raw response body).
- List methods (`list_*`) return iterators that auto-follow `next_url`. `get_*` methods return a list or object.

### 3.1 Relevant methods

```python
client.get_snapshot_all(market_type, tickers=None, include_otc=False) -> list[TickerSnapshot]
client.get_snapshot_ticker(market_type, ticker)                       -> TickerSnapshot
client.list_universal_snapshots(ticker_any_of=None, limit=10, ...)    -> Iterator[UniversalSnapshot]
client.get_grouped_daily_aggs(date, adjusted=None, include_otc=False) -> list[GroupedDailyAgg]
client.get_previous_close_agg(ticker, adjusted=None)                  -> PreviousCloseAgg (list in practice, see below)
client.get_aggs(ticker, multiplier, timespan, from_, to, adjusted=None, sort=None, limit=None) -> list[Agg]
client.list_aggs(... same ...)                                        -> Iterator[Agg]
client.get_daily_open_close_agg(ticker, date, adjusted=None)          -> DailyOpenCloseAgg
client.get_last_trade(ticker)                                         -> LastTrade
client.get_last_quote(ticker)                                         -> LastQuote   # /v2/last/nbbo
```

### 3.2 Model fields (snake_case Python attributes ← JSON keys)

| Model | Fields |
|---|---|
| `TickerSnapshot` | `ticker`, `day: Agg`, `prev_day: Agg`, `min: MinuteSnapshot`, `last_trade: LastTrade`, `last_quote: LastQuote`, `todays_change`, `todays_change_percent`, `updated`, `fair_market_value` |
| `Agg` | `open`, `high`, `low`, `close`, `volume`, `vwap`, `timestamp`, `transactions`, `otc` |
| `MinuteSnapshot` | `open`, `high`, `low`, `close`, `volume`, `vwap`, `accumulated_volume`, `timestamp`, `transactions` |
| `GroupedDailyAgg` | `ticker`, `open`, `high`, `low`, `close`, `volume`, `vwap`, `timestamp`, `transactions`, `otc` |
| `PreviousCloseAgg` | `ticker`, `open`, `high`, `low`, `close`, `volume`, `vwap`, `timestamp` |
| `DailyOpenCloseAgg` | `symbol`, `from_`, `open`, `high`, `low`, `close`, `volume`, `pre_market`, `after_hours`, `status` |
| `LastTrade` | `ticker`, `price`, `size`, `exchange`, `sip_timestamp`, `participant_timestamp`, `conditions`, `id`, `tape` |
| `LastQuote` | `ticker`, `bid_price`, `bid_size`, `ask_price`, `ask_size`, `sip_timestamp`, ... |

Any nested object can be `None` (for example `last_trade` before the open, or for illiquid names). Always guard for it.

### 3.3 Code examples

**Live or delayed prices for the watchlist (paid plans): one call**

```python
from massive import RESTClient

client = RESTClient(api_key=api_key)  # always pass explicitly

tickers = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA"]
snapshots = client.get_snapshot_all("stocks", tickers=tickers)

for s in snapshots:
    price = (s.last_trade.price if s.last_trade and s.last_trade.price
             else s.day.close if s.day and s.day.close
             else None)
    prev_close = s.prev_day.close if s.prev_day else None
    print(f"{s.ticker:6} {price:>10.2f}  prev {prev_close}  {s.todays_change_percent:+.2f}%  (updated ns={s.updated})")
```

**End-of-day closes for the watchlist (free plan): one call for all tickers**

```python
from datetime import date, timedelta
from massive import RESTClient

def latest_grouped_daily(client: RESTClient, watch: set[str], max_lookback: int = 7):
    """Return {ticker: GroupedDailyAgg} for the most recent session with data."""
    d = date.today()
    for _ in range(max_lookback):
        if d.weekday() < 5:  # skip Sat/Sun; holidays come back empty
            bars = client.get_grouped_daily_aggs(d.isoformat(), adjusted=True)
            hits = {b.ticker: b for b in bars if b.ticker in watch}
            if hits:
                return d, hits
        d -= timedelta(days=1)
    return None, {}

session, bars = latest_grouped_daily(client, {"AAPL", "MSFT", "NVDA"})
for t, b in bars.items():
    print(session, t, b.close)
```

> On the free plan, "today" is often not available until after the close. The walk-back loop handles that, but each iteration costs one of your 5 calls/min. FinAlly caches the response for 2 minutes (see §4) and keeps **all** tickers from it, so watchlist additions cost no extra calls.

**Previous close for a single ticker**

```python
prev = client.get_previous_close_agg("AAPL")
# The SDK annotates PreviousCloseAgg but parses the "results" array, so normalise:
bar = prev[0] if isinstance(prev, list) else prev
print(bar.close)
```

**Historical daily bars (for an optional longer-range chart)**

```python
bars = client.get_aggs("AAPL", 1, "day", "2026-01-01", "2026-11-06", adjusted=True, sort="asc")
series = [(b.timestamp, b.close) for b in bars]     # timestamp = ms
```

**Intraday minute bars**

```python
for b in client.list_aggs("TSLA", 1, "minute", "2026-11-06", "2026-11-06", limit=50000):
    ...
```

**Without the SDK (httpx, async)**

```python
import httpx

async def fetch_snapshot(api_key: str, tickers: list[str]) -> dict[str, dict]:
    url = "https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers"
    async with httpx.AsyncClient(timeout=10) as http:
        r = await http.get(url, params={"tickers": ",".join(tickers)},
                           headers={"Authorization": f"Bearer {api_key}"})
        r.raise_for_status()
        return {t["ticker"]: t for t in r.json().get("tickers", [])}
```

**Error handling**

```python
from massive.exceptions import BadResponse, AuthError

try:
    snaps = client.get_snapshot_all("stocks", tickers=tickers)
except BadResponse as e:
    # Body is raw JSON text. A plan without snapshot access typically returns
    # status "NOT_AUTHORIZED" (exact HTTP code: unverified, historically 403).
    if "NOT_AUTHORIZED" in str(e) or "not entitled" in str(e).lower():
        ...  # switch to end-of-day (grouped daily) mode
    else:
        raise
```

---

## 4. Recommendations for FinAlly

1. **Paid key:** call `get_snapshot_all("stocks", tickers=<watchlist>)`. That is one request for the whole watchlist. Current price = `last_trade.price` (fallback `day.close`), reference = `prev_day.close`.
2. **Free key:** snapshots return NOT_AUTHORIZED. Fall back to `get_grouped_daily_aggs` for the latest session (one request for all tickers). Prices are end-of-day and static, so the UI shows few flashes. That is expected, and it is why the simulator is the default.
3. **Cache every response for 2 minutes (rate-limit protection).** The free tier allows only 5 calls/min, so FinAlly never calls the API more often than needed:
   - **Response cache, TTL 120 s** (`MASSIVE_CACHE_TTL`). Each API response is stored with its fetch time. Until it is 2 minutes old, every poll reuses it and **no API call is made**. The browser keeps receiving the same prices over SSE.
   - **Call budget, ≤ 5 calls per rolling 60 s** (`MASSIVE_MAX_CALLS_PER_MIN`). This is a hard ceiling even when the cache misses (new ticker, weekend walk-back, restarts). With no budget left, the stale cached response is served.
   - **`RESTClient(api_key=..., retries=0)`.** The SDK's default 3 quick retries on 429/5xx would burn quota.
   - **On a 429**, stop calling for 60 s and serve cached data.
   - Result: steady state is **1 call every 2 minutes** (10 % of the free limit). Prices may be up to 2 min stale, which is fine for simulated money.
   - Paid plans can lower the TTL (e.g. `MASSIVE_CACHE_TTL=10`) and raise the budget.
   - Full design and code: `MARKET_INTERFACE.md` §7.1–7.3.
4. **Poll loop interval:** 15 s (`MASSIVE_POLL_INTERVAL`). This only checks the cache; it is *not* the API call rate. It just makes newly added tickers appear quickly when their data is already in the cached response.
5. Wrap all SDK calls in `asyncio.to_thread`. Never let an exception kill the poll loop: log it, keep the last cached prices, and retry next tick.
6. Upper-case tickers before sending. Drop tickers the API doesn't return, because they may be invalid symbols.
7. Treat `api.polygon.io` as a legacy alias only. The SDK's default base URL is already `api.massive.com`.
