"""Watch the market data layer in the terminal: `uv run python scripts/market_demo.py`.

Uses Massive if MASSIVE_API_KEY is set, otherwise the simulator.
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable

from app.market import PriceCache, create_market_data_source  # noqa: E402

ARROW = {"up": "\033[32m▲\033[0m", "down": "\033[31m▼\033[0m", "flat": " "}


async def main(seconds: float = 5.0) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cache = PriceCache()
    source = create_market_data_source(cache)
    await source.start(["AAPL", "NVDA", "TSLA", "JPM"])
    await source.add_ticker("PYPL")  # dynamic add, like a watchlist change
    try:
        for _ in range(int(seconds / 0.5)):
            await asyncio.sleep(0.5)
            row = "  ".join(
                f"{t} {u.price:>8.2f}{ARROW[u.direction]} ({u.day_change_percent:+.2f}%)"
                for t, u in sorted(cache.get_all().items())
            )
            print(row)
        print(source.status())
    finally:
        await source.stop()


if __name__ == "__main__":
    asyncio.run(main())
