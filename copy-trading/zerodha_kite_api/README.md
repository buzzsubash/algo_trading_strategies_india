# Copy Trading — Zerodha Kite Connect

Automated copy trading system that mirrors positions from a master Zerodha account to multiple client accounts in real time, scaling quantities proportionally by capital.

Requires the **Kite Connect API**. Each account — master and every client — needs its own subscription.

- Sign up at: https://kite.trade
- Each subscription gives you an `api_key` and `api_secret`
- The API allows programmatic login via TOTP and full order placement

---

## How It Works

```
Master Account (Zerodha Kite)
        │
        ├─── fetch_master_positions.py  ──►  PostgreSQL DB  ◄── ltp_subscriber.py
        └─── fetch_master_orders.py              │                 (live prices via WebSocket)
                                                 │
                                                 ▼
                                      copy_trading_executor.py
                                  (reads master positions from DB,
                                   scales by capital ratio,
                                   places orders via Kite API)
                                                 │
                              ┌──────────────────┼──────────────────┐
                              ▼                  ▼                  ▼
                         Client1             Client2            Client3
                       (Kite API)          (Kite API)         (Kite API)
```

### Position Scaling

Each client's target quantity is calculated as:

```
scaling_factor = client_capital / master_capital
target_quantity = round_to_lot_size(master_quantity × scaling_factor)
```

For example, if the master holds 50 lots of NIFTY and a client's capital is 10% of the master's, the client targets 5 lots.

The executor also handles:
- **Freeze quantity limits** — automatically slices large orders (e.g. BANKNIFTY freeze limit = 900)
- **New vs existing positions** — locks in the scaling factor at entry so partial exits are proportional
- **Overnight positions** — resets entry tracking each day
- **Order retries** — limit orders with automatic price refresh and retry on OPEN/REJECTED status

---

## Scripts

| Script | Purpose | Run frequency |
|---|---|---|
| `login_all_accounts.py` | Logs in all accounts (master + clients), generates and stores access tokens | Daily before market open |
| `login_single_account.py` | Logs in a single named account | On demand |
| `fetch_master_positions.py` | Polls master account positions every second and stores them in the DB | All day while market is open |
| `fetch_master_orders.py` | Polls master account orders and trades every second; archives EOD tradebook | All day while market is open |
| `ltp_subscriber.py` | Subscribes to live market data via Kite WebSocket for open positions | All day while market is open |
| `copy_trading_executor.py` | Detects position changes in DB and places scaled orders for all clients | All day while market is open |

---

## Prerequisites

1. **PostgreSQL database** — SQLite would technically work for a single-machine setup, but I use PostgreSQL because it's already shared with my data collection pipelines and personal strategies, and it supports remote access from multiple machines. 
2. **Zerodha Kite Connect API** subscription for every account (master + each client)
3. **Python 3.9+**
4. **(Optional) Telegram bot** for trade notifications and error alerts

---

## Setup

### 1. Install dependencies

```bash
pip install -r ../requirements.txt
```

### 2. Configure

```bash
cp ../clients_config.yaml.example ../clients_config.yaml
```

Edit `clients_config.yaml` with your real credentials:

| Section | Field | Description |
|---|---|---|
| `master_account` | `user_id`, `password`, `totp_key`, `api_key`, `api_secret` | Zerodha credentials |
| `master_account` | `estimated_capital` | Master capital in INR (for scaling) |
| `database` | `host`, `name`, `user`, `password` | PostgreSQL connection |
| `telegram` | `token`, `error_chat_id` | Optional Telegram bot |
| `clients[]` | `user_id`, `password`, `totp_key`, `api_key`, `api_secret`, `capital` | Per-client credentials |

### 3. Create access token directory

```bash
mkdir -p ../access_tokens
```

### 4. Getting Your TOTP Secret Key

When enabling 2FA on a Zerodha account:

1. Go to Zerodha Console → My Profile → Security
2. Enable TOTP — you will see a QR code
3. Click **"Can't scan? Enter manually"** instead of scanning
4. Copy the displayed Base32 secret — that is your `totp_key`

> If 2FA is already set up and you don't have the original secret, reset TOTP in Zerodha Console to retrieve the base32 key.

### 5. Daily login

Run each trading morning before market open (access tokens expire daily):

```bash
python login_all_accounts.py
```

This logs in the master account and all enabled clients, writes access tokens to the files in the config, and saves credentials to the `master_accounts` database table.

To log in a single account (useful for debugging):

```bash
python login_single_account.py --account MasterAccount
python login_single_account.py --account Client1
```

The `--account` value must match the `name` field in `clients_config.yaml`.

### 6. Start the services

Run each in a separate terminal:

```bash
python fetch_master_positions.py
python fetch_master_orders.py
python ltp_subscriber.py
python copy_trading_executor.py
```

The executor accepts `--config` if your config is at a non-default path:

```bash
python copy_trading_executor.py --config /path/to/clients_config.yaml
```

---

## Database Tables

All tables are created automatically on first run — no manual schema setup needed.

| Table | Created by | Purpose |
|---|---|---|
| `master_accounts` | login scripts | API credentials and access tokens |
| `master_account_positions` | `fetch_master_positions.py` | Live positions of master account |
| `master_account_orders` | `fetch_master_orders.py` | Intraday orders of master account |
| `master_account_trades` | `fetch_master_orders.py` | Intraday trades of master account |
| `master_tradebook_eod` | `fetch_master_orders.py` | Historical EOD tradebook archive |
| `master_pnl_timeseries` | `fetch_master_positions.py` | PnL snapshots over time |
| `live_market_data` | `ltp_subscriber.py` | Real-time LTP per instrument token |
| `instruments_master` | `copy_trading_executor.py` | Instrument metadata (lot size, expiry, etc.) |
| `client_account_positions` | `copy_trading_executor.py` | Per-client position tracking |
| `client_trade_log` | `copy_trading_executor.py` | Audit log of all orders placed for clients |

---

## Cron Setup (Ubuntu/Linux)

Add to crontab (`crontab -e`). All times are in IST — ensure your server timezone is set to `Asia/Kolkata`. Scripts are launched in [GNU Screen](https://www.gnu.org/software/screen/) sessions so they continue running after the cron exits.

```cron
# Login to Kite at 7:30 AM IST
30 7 * * 1-5 /path/to/venv/bin/python /path/to/copy-trading/zerodha_kite_api/login_all_accounts.py >> /path/to/copy-trading/logs/login.log 2>&1

# Start position fetcher at 9:14 AM IST
14 9 * * 1-5 /usr/bin/screen -dmS copy_trading_positions /path/to/venv/bin/python /path/to/copy-trading/zerodha_kite_api/fetch_master_positions.py

# Start LTP subscriber at 9:14 AM IST
14 9 * * 1-5 /usr/bin/screen -dmS copy_trading_ltp /path/to/venv/bin/python /path/to/copy-trading/zerodha_kite_api/ltp_subscriber.py

# Start orders fetcher at 9:14 AM IST
14 9 * * 1-5 /usr/bin/screen -dmS copy_trading_orders /path/to/venv/bin/python /path/to/copy-trading/zerodha_kite_api/fetch_master_orders.py

# Start copy trading executor at 9:15 AM IST (market open)
15 9 * * 1-5 /usr/bin/screen -dmS copy_trading_executor /path/to/venv/bin/python /path/to/copy-trading/zerodha_kite_api/copy_trading_executor.py --config /path/to/copy-trading/clients_config.yaml

# Stop all scripts at 3:32 PM IST (after market close 3:30 PM IST)
32 15 * * 1-5 /usr/bin/screen -S copy_trading_positions -X quit
32 15 * * 1-5 /usr/bin/screen -S copy_trading_ltp -X quit
32 15 * * 1-5 /usr/bin/screen -S copy_trading_orders -X quit
32 15 * * 1-5 /usr/bin/screen -S copy_trading_executor -X quit
```

Replace `/path/to/venv` with your virtual environment path and `/path/to/copy-trading` with the absolute path to the `copy-trading/` directory.

---

## Notes

- The `master_accounts` table stores credentials (including passwords and TOTP keys) in plaintext for automated re-login. Ensure your database is not publicly accessible.
- Access token files (`access_tokens/*.txt`) are excluded from git via `.gitignore`.
- The copy trading executor only runs during market hours (Monday–Friday, 09:15–15:30 IST). Outside these hours it waits in a loop.
