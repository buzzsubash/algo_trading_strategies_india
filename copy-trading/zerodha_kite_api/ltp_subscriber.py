import os
import time
import yaml
import logging
import psycopg2
from psycopg2.extras import execute_values
from threading import Thread, Lock
from kiteconnect import KiteTicker

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'clients_config.yaml')

with open(CONFIG_PATH) as f:
    _config = yaml.safe_load(f)

_db = _config['database']
DB_HOST = _db['host']
DB_PORT = _db['port']
DB_NAME = _db['name']
DB_USER = _db['user']
DB_PASSWORD = _db['password']

ACCOUNT_NAME = _config['master_account']['name']
POSITION_CHECK_INTERVAL = 3


class LTPSubscriber:

    def __init__(self, account_name):
        self.account_name = account_name
        self.logger = logging.getLogger(__name__)
        self.conn_params = {
            'host': DB_HOST,
            'port': DB_PORT,
            'dbname': DB_NAME,
            'user': DB_USER,
            'password': DB_PASSWORD
        }

        self.ticker = None
        self.api_key = None
        self.access_token = None

        self.db_lock = Lock()
        self.subscribed_tokens = set()
        self.daily_instruments = set()
        self.current_date = time.strftime('%Y-%m-%d')

        self.symbol_cache = {}
        self.db_conn = None

    def get_connection(self):
        return psycopg2.connect(**self.conn_params)

    def get_persistent_connection(self):
        if self.db_conn is None or self.db_conn.closed:
            self.db_conn = psycopg2.connect(**self.conn_params)
        return self.db_conn

    def create_live_market_data_table(self):
        sql = """
            CREATE TABLE IF NOT EXISTS live_market_data (
                instrument_token BIGINT PRIMARY KEY,
                tradingsymbol    VARCHAR(100),
                exchange         VARCHAR(20),
                last_price       DECIMAL(20, 4) NOT NULL,
                last_quantity    BIGINT         DEFAULT 0,
                volume           BIGINT         DEFAULT 0,
                buy_quantity     BIGINT         DEFAULT 0,
                sell_quantity    BIGINT         DEFAULT 0,
                ohlc_open        DECIMAL(20, 4) DEFAULT 0,
                ohlc_high        DECIMAL(20, 4) DEFAULT 0,
                ohlc_low         DECIMAL(20, 4) DEFAULT 0,
                ohlc_close       DECIMAL(20, 4) DEFAULT 0,
                timestamp        DECIMAL(20, 6) NOT NULL,
                updated_at       TIMESTAMP      DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_live_market_data_token ON live_market_data (instrument_token);
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        conn.commit()
        cursor.close()
        conn.close()
        self.logger.info("live_market_data table verified/created")

    def truncate_live_market_data_table(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("TRUNCATE TABLE live_market_data")
        conn.commit()
        cursor.close()
        conn.close()
        self.logger.info("live_market_data table truncated")

    def load_credentials(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT api_key, access_token FROM master_accounts WHERE name = %s AND is_active = TRUE",
            (self.account_name,)
        )
        result = cursor.fetchone()
        cursor.close()
        conn.close()

        if not result or not result[1]:
            raise Exception(f"No access token for {self.account_name}. Run login_all_accounts.py first.")

        self.api_key = result[0]
        self.access_token = result[1]
        self.logger.info(f"Credentials loaded for {self.account_name}")

    def setup_websocket(self):
        self.ticker = KiteTicker(api_key=self.api_key, access_token=self.access_token)
        self.ticker.on_ticks = self.on_ticks
        self.ticker.on_connect = self.on_connect
        self.ticker.on_close = self.on_close
        self.ticker.on_error = self.on_error
        self.ticker.on_reconnect = self.on_reconnect
        self.logger.info("WebSocket client initialized")

    def on_connect(self, ws, response):
        self.logger.info("WebSocket connected")
        self.load_symbol_cache()
        self.subscribe_to_positions()

    def on_close(self, ws, code, reason):
        self.logger.warning(f"WebSocket closed: {code} - {reason}")

    def on_error(self, ws, code, reason):
        self.logger.error(f"WebSocket error: {code} - {reason}")

    def on_reconnect(self, ws, attempts_count):
        self.logger.info(f"WebSocket reconnecting (attempt {attempts_count})")

    def load_symbol_cache(self):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT instrument_token, tradingsymbol, exchange FROM master_account_positions WHERE master_account_name = %s",
                (self.account_name,)
            )
            results = cursor.fetchall()
            cursor.close()
            conn.close()

            for row in results:
                self.symbol_cache[row[0]] = {'tradingsymbol': row[1], 'exchange': row[2]}

            self.logger.info(f"Loaded {len(self.symbol_cache)} symbols into cache")
        except Exception as e:
            self.logger.error(f"Error loading symbol cache: {e}")

    def on_ticks(self, ws, ticks):
        try:
            current_time = time.time()
            tick_data_list = []

            for tick in ticks:
                instrument_token = tick['instrument_token']
                symbol_info = self.symbol_cache.get(instrument_token)

                if not symbol_info:
                    symbol_info = self.get_symbol_info(instrument_token)
                    if symbol_info:
                        self.symbol_cache[instrument_token] = symbol_info

                tradingsymbol = symbol_info.get('tradingsymbol', 'UNKNOWN') if symbol_info else 'UNKNOWN'
                exchange = symbol_info.get('exchange', 'UNKNOWN') if symbol_info else 'UNKNOWN'
                last_price = tick.get('last_price', 0.0)
                volume = tick.get('volume', 0)

                self.logger.info(f"TICK | {exchange}:{tradingsymbol} | LTP: {last_price:.2f} | Vol: {volume}")

                tick_data_list.append((
                    instrument_token,
                    tradingsymbol,
                    exchange,
                    last_price,
                    tick.get('last_quantity', 0),
                    volume,
                    tick.get('buy_quantity', 0),
                    tick.get('sell_quantity', 0),
                    tick.get('ohlc', {}).get('open', 0.0),
                    tick.get('ohlc', {}).get('high', 0.0),
                    tick.get('ohlc', {}).get('low', 0.0),
                    tick.get('ohlc', {}).get('close', 0.0),
                    current_time
                ))

            if tick_data_list:
                self.save_ticks_immediately(tick_data_list)

        except Exception as e:
            self.logger.error(f"Error processing ticks: {e}")

    def get_symbol_info(self, instrument_token):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT tradingsymbol, exchange FROM master_account_positions WHERE instrument_token = %s LIMIT 1",
                (instrument_token,)
            )
            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if result:
                return {'tradingsymbol': result[0], 'exchange': result[1]}
            return None
        except Exception as e:
            self.logger.error(f"Error getting symbol info: {e}")
            return None

    def save_ticks_immediately(self, tick_data_list):
        try:
            conn = self.get_persistent_connection()
            cursor = conn.cursor()

            execute_values(
                cursor,
                """
                INSERT INTO live_market_data (
                    instrument_token, tradingsymbol, exchange, last_price,
                    last_quantity, volume, buy_quantity, sell_quantity,
                    ohlc_open, ohlc_high, ohlc_low, ohlc_close, timestamp, updated_at
                ) VALUES %s
                ON CONFLICT (instrument_token)
                DO UPDATE SET
                    tradingsymbol = EXCLUDED.tradingsymbol,
                    exchange      = EXCLUDED.exchange,
                    last_price    = EXCLUDED.last_price,
                    last_quantity = EXCLUDED.last_quantity,
                    volume        = EXCLUDED.volume,
                    buy_quantity  = EXCLUDED.buy_quantity,
                    sell_quantity = EXCLUDED.sell_quantity,
                    ohlc_open     = EXCLUDED.ohlc_open,
                    ohlc_high     = EXCLUDED.ohlc_high,
                    ohlc_low      = EXCLUDED.ohlc_low,
                    ohlc_close    = EXCLUDED.ohlc_close,
                    timestamp     = EXCLUDED.timestamp,
                    updated_at    = NOW()
                """,
                tick_data_list,
                template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())"
            )

            conn.commit()
            cursor.close()

            symbols = set(d[1] for d in tick_data_list)
            self.logger.info(f"DB SAVED | {len(tick_data_list)} ticks | {', '.join(symbols)}")

        except Exception as e:
            self.logger.error(f"Error saving ticks: {e}")
            if self.db_conn:
                try:
                    self.db_conn.rollback()
                except Exception:
                    pass
                self.db_conn = None

    def check_daily_reset(self):
        today = time.strftime('%Y-%m-%d')
        if today != self.current_date:
            self.logger.info(f"New trading day: {today}")
            self.daily_instruments.clear()
            self.current_date = today
            self.symbol_cache.clear()

            if self.subscribed_tokens:
                try:
                    self.ticker.unsubscribe(list(self.subscribed_tokens))
                    self.subscribed_tokens.clear()
                except Exception as e:
                    self.logger.warning(f"Error clearing subscriptions: {e}")

    def get_positions_from_db(self):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT instrument_token, tradingsymbol, quantity, pnl
                FROM master_account_positions
                WHERE master_account_name = %s
            """, (self.account_name,))
            results = cursor.fetchall()
            cursor.close()
            conn.close()
            return results
        except Exception as e:
            self.logger.error(f"Error reading positions from DB: {e}")
            return []

    def subscribe_to_positions(self):
        try:
            self.check_daily_reset()
            positions = self.get_positions_from_db()

            if positions:
                current_tokens = {pos[0] for pos in positions}
                self.daily_instruments.update(current_tokens)

                for pos in positions:
                    self.symbol_cache[pos[0]] = {'tradingsymbol': pos[1], 'exchange': 'NSE'}

                open_count = sum(1 for pos in positions if pos[2] != 0)
                closed_count = len(positions) - open_count
                total_pnl = sum(pos[3] for pos in positions)

                self.logger.info(f"Positions: {open_count} open, {closed_count} closed, Total PnL: {total_pnl}")

                for pos in positions:
                    status = "OPEN" if pos[2] != 0 else "CLOSED"
                    self.logger.info(f"  {pos[1]} | Qty: {pos[2]} | PnL: {pos[3]} | {status}")

            if self.daily_instruments:
                new_tokens = list(self.daily_instruments - self.subscribed_tokens)
                if new_tokens:
                    self.ticker.subscribe(new_tokens)
                    self.ticker.set_mode(self.ticker.MODE_FULL, new_tokens)
                    self.subscribed_tokens.update(new_tokens)
                    self.logger.info(f"Subscribed to {len(new_tokens)} instruments")

                self.logger.info(f"Total subscriptions: {len(self.subscribed_tokens)}")

        except Exception as e:
            self.logger.error(f"Error in subscription: {e}")

    def monitor_positions(self):
        self.logger.info(f"Position monitor started (checking every {POSITION_CHECK_INTERVAL}s)")

        while True:
            try:
                time.sleep(POSITION_CHECK_INTERVAL)
                positions = self.get_positions_from_db()

                if positions:
                    current_tokens = {pos[0] for pos in positions}
                    new_instruments = current_tokens - self.daily_instruments

                    if new_instruments:
                        self.logger.info(f"Detected {len(new_instruments)} new instruments in DB")
                        self.subscribe_to_positions()

            except Exception as e:
                self.logger.error(f"Error in monitor: {e}")
                time.sleep(10)

    def get_total_pnl(self):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    SUM(pnl)                                          AS total_pnl,
                    SUM(CASE WHEN quantity != 0 THEN pnl ELSE 0 END)  AS open_pnl,
                    SUM(CASE WHEN quantity = 0  THEN pnl ELSE 0 END)  AS closed_pnl
                FROM master_account_positions
                WHERE master_account_name = %s
            """, (self.account_name,))
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            return {
                'total_pnl': result[0] or 0,
                'open_pnl': result[1] or 0,
                'closed_pnl': result[2] or 0
            }
        except Exception as e:
            self.logger.error(f"Error calculating PnL: {e}")
            return {'total_pnl': 0, 'open_pnl': 0, 'closed_pnl': 0}

    def start(self):
        self.logger.info("Starting LTP Subscriber...")

        self.create_live_market_data_table()
        self.truncate_live_market_data_table()
        self.load_credentials()
        self.setup_websocket()

        monitor_thread = Thread(target=self.monitor_positions, daemon=True)
        monitor_thread.start()

        self.logger.info("Starting WebSocket connection...")
        self.ticker.connect(threaded=False)

    def stop(self):
        self.logger.info("Stopping LTP Subscriber...")

        if self.db_conn:
            self.db_conn.close()

        if self.ticker:
            self.ticker.close()

        self.logger.info("LTP Subscriber stopped")


if __name__ == "__main__":
    subscriber = LTPSubscriber(ACCOUNT_NAME)
    try:
        subscriber.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        subscriber.stop()
