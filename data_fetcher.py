"""
Fetches data for the ASRS (AI Systemic Risk Score) tool and writes to data/asrs_data.json.

FRED (no API key required):
  DGS10      - 10-Year Treasury Constant Maturity Rate (%)
  UMCSENT    - University of Michigan Consumer Sentiment Index
  BAMLC0A0CM - ICE BofA US Corporate Index OAS (IG credit spread, bps)

Massive (formerly Polygon.io) — requires MASSIVE_API_KEY in .env:
  P/S ratio = market_cap / trailing-12-month revenue
  Tickers: NVDA, MSFT, GOOGL, AMZN, META
"""

import csv
import json
import os
import sys
from datetime import datetime, timezone
from io import StringIO

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
MASSIVE_BASE_URL = "https://api.massive.com"

FRED_SERIES = {
    "treasury_yield_10y": {
        "id": "DGS10",
        "description": "10-Year Treasury Constant Maturity Rate (%)",
    },
    "consumer_confidence": {
        "id": "UMCSENT",
        "description": "University of Michigan Consumer Sentiment Index",
    },
    "ig_credit_spread": {
        "id": "BAMLC0A0CM",
        "description": "ICE BofA US Corporate Index Option-Adjusted Spread (IG, bps)",
    },
}

EQUITY_TICKERS = ["NVDA", "MSFT", "GOOGL", "AMZN", "META"]

OUTPUT_PATH  = os.path.join(os.path.dirname(__file__), "data", "asrs_data.json")
HISTORY_PATH = os.path.join(os.path.dirname(__file__), "data", "asrs_history.json")
MAX_HISTORY_WEEKS = 52

# ---------------------------------------------------------------------------
# FRED
# ---------------------------------------------------------------------------

def fetch_fred_latest(series_id: str) -> tuple[str, float]:
    """Return (date, value) for the most recent non-missing FRED observation."""
    resp = requests.get(FRED_CSV_URL, params={"id": series_id}, timeout=15)
    resp.raise_for_status()

    latest_date = latest_value = None
    for row in csv.DictReader(StringIO(resp.text)):
        raw = row.get(series_id, "").strip()
        if raw and raw != ".":  # FRED encodes missing values as "."
            latest_date = row["observation_date"]
            latest_value = float(raw)

    if latest_date is None:
        raise ValueError(f"No valid observations for FRED series {series_id}")

    return latest_date, latest_value

# ---------------------------------------------------------------------------
# Polygon.io
# ---------------------------------------------------------------------------

def load_api_key() -> str:
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    key = os.getenv("MASSIVE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("MASSIVE_API_KEY missing — add it to asrs-tool/.env")
    return key


def fetch_market_cap(ticker: str, api_key: str) -> float:
    """Fetch current market cap from Massive ticker details."""
    url = f"{MASSIVE_BASE_URL}/v3/reference/tickers/{ticker}"
    resp = requests.get(url, params={"apiKey": api_key}, timeout=15)
    resp.raise_for_status()
    market_cap = resp.json()["results"].get("market_cap")
    if not market_cap:
        raise ValueError(f"market_cap not available for {ticker} from Massive")
    return market_cap


def fetch_ttm_revenue(ticker: str, api_key: str) -> tuple[float, str]:
    """Fetch trailing-12-month revenue directly from Massive income statements.

    Returns (ttm_revenue_usd, period_end_date).
    """
    url = f"{MASSIVE_BASE_URL}/stocks/financials/v1/income-statements"
    params = {
        "tickers": ticker,
        "timeframe": "trailing_twelve_months",
        "limit": 1,
        "apiKey": api_key,
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    results = resp.json().get("results", [])

    if not results:
        raise ValueError(f"No TTM income statement returned for {ticker}")

    row = results[0]
    rev = row.get("revenue")
    if rev is None:
        raise ValueError(f"Revenue field missing in TTM income statement for {ticker}")

    return rev, row["period_end"]


def fetch_ps_ratio(ticker: str, api_key: str) -> dict:
    market_cap = fetch_market_cap(ticker, api_key)
    ttm_revenue, as_of_date = fetch_ttm_revenue(ticker, api_key)
    ps_ratio = round(market_cap / ttm_revenue, 2)
    return {
        "ps_ratio": ps_ratio,
        "market_cap": market_cap,
        "ttm_revenue": ttm_revenue,
        "as_of_date": as_of_date,
    }

# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def append_to_history(output: dict) -> None:
    """Append a flat snapshot to asrs_history.json, keeping last MAX_HISTORY_WEEKS entries."""
    macro    = output.get("macro", {})
    equities = output.get("equities", {})

    snapshot: dict = {"date": output["fetched_at"][:10]}

    if v := macro.get("treasury_yield_10y", {}).get("value"):
        snapshot["treasury_yield_10y"] = v
    if v := macro.get("consumer_confidence", {}).get("value"):
        snapshot["consumer_confidence"] = v
    if v := macro.get("ig_credit_spread", {}).get("value"):
        snapshot["ig_credit_spread_bps"] = round(v * 100, 1)

    ratios = sorted(e["ps_ratio"] for e in equities.values() if e.get("ps_ratio") is not None)
    if ratios:
        mid = len(ratios) // 2
        median = (ratios[mid - 1] + ratios[mid]) / 2 if len(ratios) % 2 == 0 else ratios[mid]
        snapshot["median_ps_ratio"] = round(median, 1)

    history: list = []
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH) as fh:
            try:
                history = json.load(fh)
            except json.JSONDecodeError:
                history = []

    history = [h for h in history if h.get("date") != snapshot["date"]]
    history.append(snapshot)
    history = history[-MAX_HISTORY_WEEKS:]

    with open(HISTORY_PATH, "w") as fh:
        json.dump(history, fh, indent=2)
    print(f"History updated ({len(history)} {'entry' if len(history) == 1 else 'entries'}) → {HISTORY_PATH}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    output = {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "macro": {},
        "equities": {},
    }

    # --- FRED ---
    print("=== FRED ===")
    for key, meta in FRED_SERIES.items():
        print(f"  {meta['id']}  ({meta['description']}) ...", end=" ", flush=True)
        try:
            date, value = fetch_fred_latest(meta["id"])
            output["macro"][key] = {
                "series_id": meta["id"],
                "description": meta["description"],
                "date": date,
                "value": value,
            }
            print(f"{date}: {value}")
        except Exception as exc:
            print(f"ERROR — {exc}", file=sys.stderr)
            sys.exit(1)

    # --- Polygon ---
    print("\n=== Massive (P/S ratios) ===")
    try:
        api_key = load_api_key()
    except RuntimeError as exc:
        print(f"  SKIP — {exc}", file=sys.stderr)
    else:
        for ticker in EQUITY_TICKERS:
            print(f"  {ticker} ...", end=" ", flush=True)
            try:
                data = fetch_ps_ratio(ticker, api_key)
                output["equities"][ticker] = data
                print(f"P/S {data['ps_ratio']}  (as of {data['as_of_date']})")
            except Exception as exc:
                print(f"ERROR — {exc}", file=sys.stderr)
                sys.exit(1)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as fh:
        json.dump(output, fh, indent=2)
    print(f"\nWrote {OUTPUT_PATH}")
    append_to_history(output)


if __name__ == "__main__":
    main()
