import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import requests
from datetime import datetime, timedelta
import os
import json
import time

# ============================================================
# 1. Credentials Setup
# ============================================================
creds_json = os.environ.get('GCP_CREDENTIALS')
if not creds_json:
    print("ERROR: GCP_CREDENTIALS secret missing!")
    exit(1)

creds_dict = json.loads(creds_json)
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

# your Google Spreadsheet ID
spreadsheet_id = "1EC9KVvEhdezj5fWp-4rjF90k5Qh4CHxEZEIsSfYxT-o"

# Connecting both the sheets
try:
    ws_volume = client.open_by_key(spreadsheet_id).worksheet("Top 250 Stocks")
    ws_turnover = client.open_by_key(spreadsheet_id).worksheet("Top 250 Turnover")
except Exception as e:
    print(f"Sheet Connection Error: {e}")
    exit(1)


# ============================================================
# 2. NSE Live Market Data Fetcher
# ============================================================
# NSE's site requires a valid session/cookies before its API will respond
# (a plain request without visiting the homepage first gets rejected).
# We use the "NIFTY TOTAL MARKET" index snapshot, which covers ~750 of the
# most liquid NSE equities with live volume/turnover - broad enough to
# reliably contain the top 250 by volume and by turnover at any point
# in the session.
NSE_HOME_URL = "https://www.nseindia.com"
NSE_LIVE_API_URL = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20TOTAL%20MARKET"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.nseindia.com/market-data/live-equity-market',
}


def fetch_live_market_data():
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        # Step 1: hit the homepage first to pick up NSE's session cookies
        session.get(NSE_HOME_URL, timeout=15)
        time.sleep(1)  # brief pause - firing the API immediately after can get rejected

        # Step 2: call the live index snapshot API using that session
        response = session.get(NSE_LIVE_API_URL, timeout=15)
        print(f"    Status: {response.status_code} | Content-Type: {response.headers.get('Content-Type')}")

        if response.status_code != 200:
            snippet = response.content[:200]
            print(f"    Body snippet: {snippet}")
            return None, None

        payload = response.json()
        rows = payload.get('data', [])
        if not rows:
            print("No data rows returned by NSE API")
            return None, None

        df = pd.DataFrame(rows)

        # Filter only equity series (EQ)
        if 'series' in df.columns:
            df = df[df['series'].astype(str).str.strip() == 'EQ']

        # Remove ETFs / gold / liquid / silver funds, same as before
        filter_keywords = 'BEES|ETF|GOLD|LIQUID|CASE|SILVER|LIQ'
        df = df[~df['symbol'].astype(str).str.contains(filter_keywords, case=False, na=False)]

        # NSE's live API uses these field names for volume/turnover/last price
        vol_col = 'totalTradedVolume'
        turnover_col = 'totalTradedValue'
        price_col = 'lastPrice'

        missing = [c for c in (vol_col, turnover_col, price_col) if c not in df.columns]
        if missing:
            print(f"Expected columns missing from NSE response: {missing}")
            return None, None

        df_vol = df.sort_values(by=vol_col, ascending=False).head(250)
        data_vol = df_vol[['symbol', vol_col, price_col]].values.tolist()

        df_turnover = df.sort_values(by=turnover_col, ascending=False).head(250)
        data_turnover = df_turnover[['symbol', turnover_col, price_col]].values.tolist()

        return data_vol, data_turnover

    except Exception as e:
        print(f"Error: {e}")
        return None, None


# ============================================================
# 3. Execution: fetch + update sheets (single pass)
# ============================================================
data_vol_to_insert, data_turnover_to_insert = fetch_live_market_data()

if not (data_vol_to_insert and data_turnover_to_insert):
    print("FAILED: Could not fetch live NSE market data")
    exit(1)

try:
    # A. Update Volume Sheet
    ws_volume.batch_clear(['A2:C251'])
    ws_volume.update('A2', data_vol_to_insert)

    # B. Update Turnover Sheet
    ws_turnover.batch_clear(['A2:C251'])
    ws_turnover.update('A2', data_turnover_to_insert)

    # Update Timestamp
    ist_now = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime('%d-%b %H:%M')
    status_msg = f"Live Snapshot | Last Update: {ist_now} (IST)"

    ws_volume.update('K2', [[status_msg]])
    ws_turnover.update('K2', [[status_msg]])

    print(f"SUCCESS: Both Sheets (Volume and Turnover) updated with live data at {ist_now} IST!")
except Exception as e:
    print(f"Google Sheet update error: {e}")
    exit(1)
