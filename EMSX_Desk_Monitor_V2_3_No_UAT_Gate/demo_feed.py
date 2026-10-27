"""실주문 전송 없이 오늘 날짜의 모의 주문을 제공한다."""

from typing import Any
import queue
import threading
from order_fields import desk_today

SAMPLE = [
    (820001, "FUND_ALPHA", "7203 JP Equity", "BUY", 5000, "WORKING", "JEFF", 3500),
    (820002, "FUND_BETA", "700 HK Equity", "SELL", 3000, "FILLED", "CANT", 3000),
    (820003, "FUND_GAMMA", "2330 TT Equity", "BUY", 2000, "NEW", "", 0),
    (820004, "FUND_DELTA", "BHP AU Equity", "BUY", 10000, "WORKING", "GS", 2500),
    (820005, "FUND_EPSILON", "9984 JP Equity", "SELL", 1200, "ORD-PEND", "", 0),
]


class DemoWorker(threading.Thread):
    def __init__(self, output: queue.Queue) -> None:
        super().__init__(daemon=True)
        self.output = output
        self.stop_event = threading.Event()

    def emit(self, typ: str, **kwargs: Any) -> None:
        self.output.put({"type": typ, **kwargs})

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        self.emit("connected", message="DEMO: synthetic data, no Bloomberg connection")
        for feed in ("O", "R"):
            self.emit("subscription", feed=feed, status="STARTED")
        n = 0
        for seq, account, ticker, side, amount, status, broker, filled in SAMPLE:
            n += 1
            self.emit(
                "reference",
                security=ticker,
                fields={
                    "iso_country": ["JP", "HK", "TW", "AU", "JP"][n - 1],
                    "sec_type": "Common Stock",
                    "avg_volume_5d": 50000,
                    "avg_volume_20d": 100000 if n != 4 else 50000,
                },
            )
            self.emit(
                "data",
                feed="O",
                status=4,
                fields={
                    "API_SEQ_NUM": n,
                    "EMSX_SEQUENCE": seq,
                    "EMSX_ACCOUNT": account,
                    "EMSX_TICKER": ticker,
                    "EMSX_SIDE": side,
                    "EMSX_AMOUNT": amount,
                    "EMSX_FILLED": filled,
                    "EMSX_STATUS": status,
                    "EMSX_TRADER": "DEMO",
                    "EMSX_NOTES": "MOC" if n % 2 else "TWAP",
                    "EMSX_BASKET_NAME": "DEMO_ALPHA" if n % 2 else "DEMO_BETA",
                    "EMSX_DATE": int(desk_today().strftime("%Y%m%d")),
                    "EMSX_TIME": 32400 + n,
                },
            )
        self.emit("data", feed="O", status=11, fields={})
        i = 0
        for seq, account, ticker, side, amount, status, broker, filled in SAMPLE:
            if not broker:
                continue
            i += 1
            self.emit(
                "data",
                feed="R",
                status=4,
                fields={
                    "API_SEQ_NUM": i,
                    "EMSX_SEQUENCE": seq,
                    "EMSX_ROUTE_ID": 1,
                    "EMSX_BROKER": broker,
                    "EMSX_AMOUNT": amount,
                    "EMSX_FILLED": filled,
                    "EMSX_STATUS": status,
                },
            )
        self.emit("data", feed="R", status=11, fields={})
        progress = 3500
        while not self.stop_event.wait(6):
            if progress >= 5000:
                continue
            progress = min(progress + 250, 5000)
            n += 1
            i += 1
            self.emit(
                "data",
                feed="O",
                status=7,
                fields={
                    "API_SEQ_NUM": n,
                    "EMSX_SEQUENCE": 820001,
                    "EMSX_FILLED": progress,
                    "EMSX_STATUS": "FILLED" if progress == 5000 else "WORKING",
                },
            )
            self.emit(
                "data",
                feed="R",
                status=7,
                fields={
                    "API_SEQ_NUM": i,
                    "EMSX_SEQUENCE": 820001,
                    "EMSX_ROUTE_ID": 1,
                    "EMSX_FILLED": progress,
                    "EMSX_STATUS": "FILLED" if progress == 5000 else "WORKING",
                },
            )
        self.emit("ended")
