import os
import time
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
PNL_SNAPSHOT_INTERVAL = 60
EOD_SNAPSHOT_HOUR = 15
EOD_SNAPSHOT_MINUTE = 29
IST = pytz.timezone('Asia/Kolkata')


class PositionFetcher:

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
        self.last_pnl_snapshot_time = 0
        self.eod_saved_date = None

    def get_connection(self):
        return psycopg2.connect(**self.conn_params)

    def create_tables(self):
        sql = """
            CREATE TABLE IF NOT EXISTS master_account_positions (
                id                  SERIAL PRIMARY KEY,
                master_account_name VARCHAR(100)   NOT NULL,
                tradingsymbol       VARCHAR(100)   NOT NULL,
                exchange            VARCHAR(20)    NOT NULL,
                instrument_token    BIGINT,
                product             VARCHAR(20),
                quantity            BIGINT         DEFAULT 0,
                overnight_quantity  BIGINT         DEFAULT 0,
                multiplier          BIGINT         DEFAULT 1,
                average_price       DECIMAL(20, 4) DEFAULT 0,
                close_price         DECIMAL(20, 4) DEFAULT 0,
                last_price          DECIMAL(20, 4) DEFAULT 0,
                value               DECIMAL(20, 4) DEFAULT 0,
                pnl                 DECIMAL(20, 4) DEFAULT 0,
                m2m                 DECIMAL(20, 4) DEFAULT 0,
                unrealised          DECIMAL(20, 4) DEFAULT 0,
                realised            DECIMAL(20, 4) DEFAULT 0,
                buy_quantity        BIGINT         DEFAULT 0,
                buy_price           DECIMAL(20, 4) DEFAULT 0,
                buy_value           DECIMAL(20, 4) DEFAULT 0,
                buy_m2m             DECIMAL(20, 4) DEFAULT 0,
                sell_quantity       BIGINT         DEFAULT 0,
                sell_price          DECIMAL(20, 4) DEFAULT 0,
                sell_value          DECIMAL(20, 4) DEFAULT 0,
                sell_m2m            DECIMAL(20, 4) DEFAULT 0,
                day_buy_quantity    BIGINT         DEFAULT 0,
                day_buy_price       DECIMAL(20, 4) DEFAULT 0,
                day_buy_value       DECIMAL(20, 4) DEFAULT 0,
                day_sell_quantity   BIGINT         DEFAULT 0,
                day_sell_price      DECIMAL(20, 4) DEFAULT 0,
                day_sell_value      DECIMAL(20, 4) DEFAULT 0,
                fetched_at          TIMESTAMP      DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_master_account_positions_account ON master_account_positions (master_account_name);
            CREATE INDEX IF NOT EXISTS idx_master_account_positions_token ON master_account_positions (instrument_token);

            CREATE TABLE IF NOT EXISTS master_pnl_timeseries (
                id                  SERIAL PRIMARY KEY,
                master_account_name VARCHAR(100)   NOT NULL,
                recorded_at         TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
                total_positions     INTEGER                 DEFAULT 0,
                open_positions      INTEGER                 DEFAULT 0,
                closed_positions    INTEGER                 DEFAULT 0,
                total_realised      DECIMAL(20, 4)          DEFAULT 0,
                live_unrealised     DECIMAL(20, 4)          DEFAULT 0,
                live_total_pnl      DECIMAL(20, 4)          DEFAULT 0,
                snapshot_type       VARCHAR(20)             DEFAULT 'REGULAR'
            );
            CREATE INDEX IF NOT EXISTS idx_pnl_timeseries_account ON master_pnl_timeseries (master_account_name);
            CREATE INDEX IF NOT EXISTS idx_pnl_timeseries_recorded ON master_pnl_timeseries (recorded_at);
            CREATE INDEX IF NOT EXISTS idx_pnl_timeseries_account_recorded ON master_pnl_timeseries (master_account_name, recorded_at);
            CREATE INDEX IF NOT EXISTS idx_pnl_timeseries_type ON master_pnl_timeseries (snapshot_type);
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        conn.commit()
        cursor.close()
        conn.close()

    def initialize(self):
        self.create_tables()

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

    def fetch_and_save(self):
        positions_data = self.kite.positions()
        net_positions = positions_data.get('net', [])

        now = datetime.now()
        values = []

        for pos in net_positions:
            values.append((
                self.account_name,
                pos.get('tradingsymbol', ''),
                pos.get('exchange', ''),
                pos.get('instrument_token', 0),
                pos.get('product', ''),
                pos.get('quantity', 0),
                pos.get('overnight_quantity', 0),
                pos.get('multiplier', 1),
                pos.get('average_price', 0),
                pos.get('close_price', 0),
                pos.get('last_price', 0),
                pos.get('value', 0),
                pos.get('pnl', 0),
                pos.get('m2m', 0),
                pos.get('unrealised', 0),
                pos.get('realised', 0),
                pos.get('buy_quantity', 0),
                pos.get('buy_price', 0),
                pos.get('buy_value', 0),
                pos.get('buy_m2m', 0),
                pos.get('sell_quantity', 0),
                pos.get('sell_price', 0),
                pos.get('sell_value', 0),
                pos.get('sell_m2m', 0),
                pos.get('day_buy_quantity', 0),
                pos.get('day_buy_price', 0),
                pos.get('day_buy_value', 0),
                pos.get('day_sell_quantity', 0),
                pos.get('day_sell_price', 0),
                pos.get('day_sell_value', 0),
                now
            ))

        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM master_account_positions WHERE master_account_name = %s",
            (self.account_name,)
        )

        if values:
            execute_values(cursor, """
                INSERT INTO master_account_positions (
                    master_account_name, tradingsymbol, exchange, instrument_token, product,
                    quantity, overnight_quantity, multiplier, average_price, close_price,
                    last_price, value, pnl, m2m, unrealised, realised,
                    buy_quantity, buy_price, buy_value, buy_m2m,
                    sell_quantity, sell_price, sell_value, sell_m2m,
                    day_buy_quantity, day_buy_price, day_buy_value,
                    day_sell_quantity, day_sell_price, day_sell_value,
                    fetched_at
                ) VALUES %s
            """, values)

        conn.commit()
        cursor.close()
        conn.close()

        logging.info(f"Saved {len(net_positions)} positions for {self.account_name}")

    def get_live_pnl_snapshot(self):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    COUNT(*)                                          AS total_positions,
                    SUM(CASE WHEN p.quantity != 0 THEN 1 ELSE 0 END) AS open_count,
                    SUM(CASE WHEN p.quantity = 0  THEN 1 ELSE 0 END) AS closed_count,

                    COALESCE(SUM(
                        CASE
                            WHEN p.quantity = 0      THEN (p.sell_value - p.buy_value)
                            WHEN p.sell_quantity > 0 THEN (p.sell_value - (p.buy_price * p.sell_quantity))
                            ELSE 0
                        END
                    ), 0) AS total_realised,

                    COALESCE(SUM(
                        CASE
                            WHEN p.quantity != 0 THEN
                                (COALESCE(l.last_price, p.last_price) - p.average_price) * p.quantity * p.multiplier
                            ELSE 0
                        END
                    ), 0) AS live_unrealised,

                    COALESCE(
                        SUM(CASE
                            WHEN p.quantity = 0      THEN (p.sell_value - p.buy_value)
                            WHEN p.sell_quantity > 0 THEN (p.sell_value - (p.buy_price * p.sell_quantity))
                            ELSE 0
                        END) +
                        SUM(CASE
                            WHEN p.quantity != 0 THEN
                                (COALESCE(l.last_price, p.last_price) - p.average_price) * p.quantity * p.multiplier
                            ELSE 0
                        END),
                    0) AS live_total_pnl

                FROM master_account_positions p
                LEFT JOIN live_market_data l ON l.instrument_token = p.instrument_token
                WHERE p.master_account_name = %s
            """, (self.account_name,))

            result = cursor.fetchone()
            cursor.close()
            conn.close()

            return {
                'total_positions': result[0] or 0,
                'open_positions': result[1] or 0,
                'closed_positions': result[2] or 0,
                'total_realised': float(result[3] or 0),
                'live_unrealised': float(result[4] or 0),
                'live_total_pnl': float(result[5] or 0)
            }
        except Exception as e:
            logging.error(f"Error getting live PnL snapshot: {e}")
            return None

    def save_pnl_snapshot(self, snapshot_type='REGULAR'):
        try:
            snapshot = self.get_live_pnl_snapshot()
            if not snapshot:
                return

            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO master_pnl_timeseries (
                    master_account_name, recorded_at, total_positions, open_positions,
                    closed_positions, total_realised, live_unrealised, live_total_pnl, snapshot_type
                ) VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s, %s)
            """, (
                self.account_name,
                snapshot['total_positions'],
                snapshot['open_positions'],
                snapshot['closed_positions'],
                snapshot['total_realised'],
                snapshot['live_unrealised'],
                snapshot['live_total_pnl'],
                snapshot_type
            ))
            conn.commit()
            cursor.close()
            conn.close()

            logging.info(
                f"PNL SNAPSHOT [{snapshot_type}] | "
                f"Open: {snapshot['open_positions']} | Closed: {snapshot['closed_positions']} | "
                f"Realised: {snapshot['total_realised']:.2f} | "
                f"Unrealised: {snapshot['live_unrealised']:.2f} | "
                f"Total: {snapshot['live_total_pnl']:.2f}"
            )
        except Exception as e:
            logging.error(f"Error saving PnL snapshot: {e}")

    def check_and_save_pnl_snapshot(self):
        current_time = time.time()
        if current_time - self.last_pnl_snapshot_time >= PNL_SNAPSHOT_INTERVAL:
            self.save_pnl_snapshot('REGULAR')
            self.last_pnl_snapshot_time = current_time

    def check_and_save_eod_snapshot(self):
        now_ist = datetime.now(IST)
        today_date = now_ist.date()

        if self.eod_saved_date == today_date:
            return

        if now_ist.hour == EOD_SNAPSHOT_HOUR and now_ist.minute == EOD_SNAPSHOT_MINUTE:
            self.save_pnl_snapshot('EOD')
            self.eod_saved_date = today_date
            logging.info(f"EOD snapshot saved for {today_date}")

    def run(self):
        self.initialize()
        self.last_pnl_snapshot_time = time.time()
        self.save_pnl_snapshot('START')
        logging.info(
            f"Fetching positions every {FETCH_INTERVAL}s | "
            f"PnL snapshot every {PNL_SNAPSHOT_INTERVAL}s | "
            f"EOD at {EOD_SNAPSHOT_HOUR}:{EOD_SNAPSHOT_MINUTE:02d} IST"
        )

        while True:
            try:
                self.fetch_and_save()
                self.check_and_save_pnl_snapshot()
                self.check_and_save_eod_snapshot()
            except Exception as e:
                logging.error(f"Error: {e}")
            time.sleep(FETCH_INTERVAL)


if __name__ == "__main__":
    fetcher = PositionFetcher(ACCOUNT_NAME)
    try:
        fetcher.run()
    except KeyboardInterrupt:
        logging.info("Stopped")
    except Exception as e:
        logging.error(f"Fatal: {e}")
