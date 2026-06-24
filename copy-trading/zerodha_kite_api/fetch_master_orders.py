import os
import time
import json
import yaml
import logging
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime
import pytz
from kiteconnect import KiteConnect

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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
FETCH_INTERVAL = 1
EOD_SAVE_HOUR = 15
EOD_SAVE_MINUTE = 30
IST = pytz.timezone('Asia/Kolkata')


class OrderFetcher:

    def __init__(self, account_name):
        self.account_name = account_name
        self.conn_params = {
            'host': DB_HOST,
            'port': DB_PORT,
            'dbname': DB_NAME,
            'user': DB_USER,
            'password': DB_PASSWORD
        }
        self.kite = None
        self.db_conn = None
        self.eod_saved_date = None

    def get_connection(self):
        return psycopg2.connect(**self.conn_params)

    def get_persistent_connection(self):
        if self.db_conn is None or self.db_conn.closed:
            self.db_conn = psycopg2.connect(**self.conn_params)
        return self.db_conn

    def create_tables(self):
        sql = """
            CREATE TABLE IF NOT EXISTS master_account_orders (
                id                        SERIAL PRIMARY KEY,
                master_account_name       VARCHAR(100) NOT NULL,
                order_id                  VARCHAR(50)  NOT NULL,
                exchange_order_id         VARCHAR(50),
                parent_order_id           VARCHAR(50),
                placed_by                 VARCHAR(50),
                status                    VARCHAR(50),
                status_message            TEXT,
                status_message_raw        TEXT,
                order_timestamp           TIMESTAMP,
                exchange_timestamp        TIMESTAMP,
                exchange_update_timestamp TIMESTAMP,
                variety                   VARCHAR(20),
                modified                  BOOLEAN        DEFAULT FALSE,
                exchange                  VARCHAR(20),
                tradingsymbol             VARCHAR(100),
                instrument_token          BIGINT,
                order_type                VARCHAR(20),
                transaction_type          VARCHAR(10),
                validity                  VARCHAR(10),
                validity_ttl              INTEGER        DEFAULT 0,
                product                   VARCHAR(20),
                quantity                  BIGINT         DEFAULT 0,
                disclosed_quantity        BIGINT         DEFAULT 0,
                price                     DECIMAL(20, 4) DEFAULT 0,
                trigger_price             DECIMAL(20, 4) DEFAULT 0,
                average_price             DECIMAL(20, 4) DEFAULT 0,
                filled_quantity           BIGINT         DEFAULT 0,
                pending_quantity          BIGINT         DEFAULT 0,
                cancelled_quantity        BIGINT         DEFAULT 0,
                market_protection         INTEGER        DEFAULT 0,
                meta                      JSONB,
                tag                       VARCHAR(100),
                fetched_at                TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (master_account_name, order_id)
            );
            CREATE INDEX IF NOT EXISTS idx_master_orders_account ON master_account_orders (master_account_name);
            CREATE INDEX IF NOT EXISTS idx_master_orders_status ON master_account_orders (status);
            CREATE INDEX IF NOT EXISTS idx_master_orders_tradingsymbol ON master_account_orders (tradingsymbol);

            CREATE TABLE IF NOT EXISTS master_account_trades (
                id                  SERIAL PRIMARY KEY,
                master_account_name VARCHAR(100) NOT NULL,
                trade_id            VARCHAR(50)  NOT NULL,
                order_id            VARCHAR(50)  NOT NULL,
                exchange_order_id   VARCHAR(50),
                exchange            VARCHAR(20),
                tradingsymbol       VARCHAR(100),
                instrument_token    BIGINT,
                product             VARCHAR(20),
                transaction_type    VARCHAR(10),
                quantity            BIGINT         DEFAULT 0,
                average_price       DECIMAL(20, 4) DEFAULT 0,
                fill_timestamp      TIMESTAMP,
                order_timestamp     VARCHAR(20),
                exchange_timestamp  TIMESTAMP,
                fetched_at          TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (master_account_name, trade_id)
            );
            CREATE INDEX IF NOT EXISTS idx_master_trades_account ON master_account_trades (master_account_name);
            CREATE INDEX IF NOT EXISTS idx_master_trades_tradingsymbol ON master_account_trades (tradingsymbol);

            CREATE TABLE IF NOT EXISTS master_tradebook_eod (
                id                  SERIAL PRIMARY KEY,
                master_account_name VARCHAR(100)   NOT NULL,
                trade_date          DATE           NOT NULL,
                trade_id            VARCHAR(50)    NOT NULL,
                order_id            VARCHAR(50)    NOT NULL,
                exchange_order_id   VARCHAR(50),
                exchange            VARCHAR(20),
                tradingsymbol       VARCHAR(100),
                instrument_token    BIGINT,
                product             VARCHAR(20),
                transaction_type    VARCHAR(10),
                quantity            BIGINT         DEFAULT 0,
                average_price       DECIMAL(20, 4) DEFAULT 0,
                trade_value         DECIMAL(20, 4) DEFAULT 0,
                fill_timestamp      TIMESTAMP,
                exchange_timestamp  TIMESTAMP,
                saved_at            TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (master_account_name, trade_date, trade_id)
            );
            CREATE INDEX IF NOT EXISTS idx_tradebook_account ON master_tradebook_eod (master_account_name);
            CREATE INDEX IF NOT EXISTS idx_tradebook_date ON master_tradebook_eod (trade_date);
            CREATE INDEX IF NOT EXISTS idx_tradebook_account_date ON master_tradebook_eod (master_account_name, trade_date);
            CREATE INDEX IF NOT EXISTS idx_tradebook_tradingsymbol ON master_tradebook_eod (tradingsymbol);
            CREATE INDEX IF NOT EXISTS idx_tradebook_transaction ON master_tradebook_eod (transaction_type);
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        conn.commit()
        cursor.close()
        conn.close()
        logging.info("Tables created/verified")

    def clear_intraday_tables(self):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM master_account_orders WHERE master_account_name = %s",
                (self.account_name,)
            )
            orders_cleared = cursor.rowcount
            cursor.execute(
                "DELETE FROM master_account_trades WHERE master_account_name = %s",
                (self.account_name,)
            )
            trades_cleared = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()
            logging.info(f"Cleared intraday tables | Orders: {orders_cleared} | Trades: {trades_cleared}")
        except Exception as e:
            logging.error(f"Error clearing intraday tables: {e}")

    def initialize(self):
        self.create_tables()
        self.clear_intraday_tables()

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

        self.kite = KiteConnect(api_key=result[0])
        self.kite.set_access_token(result[1])
        logging.info(f"Initialized for {self.account_name}")

    def parse_timestamp(self, ts_value):
        if ts_value is None:
            return None
        if isinstance(ts_value, datetime):
            return ts_value
        if isinstance(ts_value, str) and len(ts_value) >= 19:
            try:
                return datetime.strptime(ts_value[:19], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                return None
        return None

    def fetch_and_save_orders(self):
        try:
            orders = self.kite.orders()
            now = datetime.now()

            conn = self.get_persistent_connection()
            cursor = conn.cursor()

            cursor.execute(
                "DELETE FROM master_account_orders WHERE master_account_name = %s",
                (self.account_name,)
            )

            if not orders:
                conn.commit()
                cursor.close()
                logging.info("No orders found")
                return 0

            order_values = []
            for order in orders:
                meta_json = json.dumps(order.get('meta', {})) if order.get('meta') else None
                order_values.append((
                    self.account_name,
                    order.get('order_id', ''),
                    order.get('exchange_order_id'),
                    order.get('parent_order_id'),
                    order.get('placed_by'),
                    order.get('status', ''),
                    order.get('status_message'),
                    order.get('status_message_raw'),
                    self.parse_timestamp(order.get('order_timestamp')),
                    self.parse_timestamp(order.get('exchange_timestamp')),
                    self.parse_timestamp(order.get('exchange_update_timestamp')),
                    order.get('variety'),
                    order.get('modified', False),
                    order.get('exchange'),
                    order.get('tradingsymbol'),
                    order.get('instrument_token'),
                    order.get('order_type'),
                    order.get('transaction_type'),
                    order.get('validity'),
                    order.get('validity_ttl', 0),
                    order.get('product'),
                    order.get('quantity', 0),
                    order.get('disclosed_quantity', 0),
                    order.get('price', 0),
                    order.get('trigger_price', 0),
                    order.get('average_price', 0),
                    order.get('filled_quantity', 0),
                    order.get('pending_quantity', 0),
                    order.get('cancelled_quantity', 0),
                    order.get('market_protection', 0),
                    meta_json,
                    order.get('tag'),
                    now
                ))

            if order_values:
                execute_values(
                    cursor,
                    """
                    INSERT INTO master_account_orders (
                        master_account_name, order_id, exchange_order_id, parent_order_id,
                        placed_by, status, status_message, status_message_raw,
                        order_timestamp, exchange_timestamp, exchange_update_timestamp,
                        variety, modified, exchange, tradingsymbol, instrument_token,
                        order_type, transaction_type, validity, validity_ttl, product,
                        quantity, disclosed_quantity, price, trigger_price, average_price,
                        filled_quantity, pending_quantity, cancelled_quantity, market_protection,
                        meta, tag, fetched_at
                    ) VALUES %s
                    """,
                    order_values
                )

            conn.commit()
            cursor.close()

            open_orders = sum(1 for o in orders if o.get('status') in ('OPEN', 'TRIGGER PENDING', 'OPEN PENDING'))
            complete_orders = sum(1 for o in orders if o.get('status') == 'COMPLETE')

            logging.info(f"Orders: {len(orders)} | Open: {open_orders} | Complete: {complete_orders}")
            return len(orders)

        except Exception as e:
            logging.error(f"Error fetching orders: {e}")
            if self.db_conn:
                try:
                    self.db_conn.rollback()
                except Exception:
                    pass
            return 0

    def fetch_and_save_trades(self):
        try:
            trades = self.kite.trades()
            now = datetime.now()

            conn = self.get_persistent_connection()
            cursor = conn.cursor()

            cursor.execute(
                "DELETE FROM master_account_trades WHERE master_account_name = %s",
                (self.account_name,)
            )

            if not trades:
                conn.commit()
                cursor.close()
                return 0

            trade_values = []
            for trade in trades:
                trade_values.append((
                    self.account_name,
                    trade.get('trade_id', ''),
                    trade.get('order_id', ''),
                    trade.get('exchange_order_id'),
                    trade.get('exchange'),
                    trade.get('tradingsymbol'),
                    trade.get('instrument_token'),
                    trade.get('product'),
                    trade.get('transaction_type'),
                    trade.get('quantity', 0),
                    trade.get('average_price', 0),
                    self.parse_timestamp(trade.get('fill_timestamp')),
                    trade.get('order_timestamp'),
                    self.parse_timestamp(trade.get('exchange_timestamp')),
                    now
                ))

            if trade_values:
                execute_values(
                    cursor,
                    """
                    INSERT INTO master_account_trades (
                        master_account_name, trade_id, order_id, exchange_order_id,
                        exchange, tradingsymbol, instrument_token, product,
                        transaction_type, quantity, average_price, fill_timestamp,
                        order_timestamp, exchange_timestamp, fetched_at
                    ) VALUES %s
                    """,
                    trade_values
                )

            conn.commit()
            cursor.close()

            buy_trades = sum(1 for t in trades if t.get('transaction_type') == 'BUY')
            sell_trades = sum(1 for t in trades if t.get('transaction_type') == 'SELL')

            logging.info(f"Trades: {len(trades)} | Buy: {buy_trades} | Sell: {sell_trades}")
            return len(trades)

        except Exception as e:
            logging.error(f"Error fetching trades: {e}")
            if self.db_conn:
                try:
                    self.db_conn.rollback()
                except Exception:
                    pass
            return 0

    def save_eod_tradebook(self):
        try:
            today_ist = datetime.now(IST).date()
            conn = self.get_persistent_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO master_tradebook_eod (
                    master_account_name, trade_date, trade_id, order_id,
                    exchange_order_id, exchange, tradingsymbol, instrument_token,
                    product, transaction_type, quantity, average_price,
                    trade_value, fill_timestamp, exchange_timestamp, saved_at
                )
                SELECT
                    master_account_name, %s, trade_id, order_id,
                    exchange_order_id, exchange, tradingsymbol, instrument_token,
                    product, transaction_type, quantity, average_price,
                    (quantity * average_price), fill_timestamp, exchange_timestamp, NOW()
                FROM master_account_trades
                WHERE master_account_name = %s
                ON CONFLICT (master_account_name, trade_date, trade_id)
                DO UPDATE SET
                    average_price = EXCLUDED.average_price,
                    quantity      = EXCLUDED.quantity,
                    trade_value   = EXCLUDED.trade_value,
                    saved_at      = EXCLUDED.saved_at
            """, (today_ist, self.account_name))

            trades_saved = cursor.rowcount
            conn.commit()
            cursor.close()

            logging.info(f"EOD TRADEBOOK SAVED | Date: {today_ist} | Trades: {trades_saved}")
            return trades_saved

        except Exception as e:
            logging.error(f"Error saving EOD tradebook: {e}")
            if self.db_conn:
                try:
                    self.db_conn.rollback()
                except Exception:
                    pass
            return 0

    def check_and_save_eod(self):
        now_ist = datetime.now(IST)
        today_date = now_ist.date()

        if self.eod_saved_date == today_date:
            return

        if now_ist.hour == EOD_SAVE_HOUR and now_ist.minute == EOD_SAVE_MINUTE:
            self.save_eod_tradebook()
            self.eod_saved_date = today_date

    def run(self):
        self.initialize()
        logging.info(f"Fetching orders/trades every {FETCH_INTERVAL}s | EOD save at {EOD_SAVE_HOUR}:{EOD_SAVE_MINUTE:02d} IST")

        while True:
            try:
                self.fetch_and_save_orders()
                self.fetch_and_save_trades()
                self.check_and_save_eod()
            except Exception as e:
                logging.error(f"Error: {e}")
            time.sleep(FETCH_INTERVAL)

    def stop(self):
        logging.info("Stopping Order Fetcher...")
        if self.db_conn:
            self.db_conn.close()
        logging.info("Order Fetcher stopped")


if __name__ == "__main__":
    fetcher = OrderFetcher(ACCOUNT_NAME)
    try:
        fetcher.run()
    except KeyboardInterrupt:
        logging.info("Stopped")
    finally:
        fetcher.stop()
