import os
import time
import yaml
import glob
import logging
import math
import requests
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from datetime import datetime, timedelta
from threading import Lock
from kiteconnect import KiteConnect
from logging.handlers import TimedRotatingFileHandler
import pytz

IST = pytz.timezone('Asia/Kolkata')


def setup_logging():
    logs_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    log_file = os.path.join(logs_dir, 'copy_trading.log')
    cleanup_old_logs(logs_dir, days=30)

    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler = TimedRotatingFileHandler(
        log_file, when='midnight', interval=1, backupCount=30, encoding='utf-8'
    )
    file_handler.suffix = '%Y-%m-%d'
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('kiteconnect').setLevel(logging.WARNING)


def cleanup_old_logs(logs_dir, days=30):
    try:
        cutoff_date = datetime.now() - timedelta(days=days)
        pattern = os.path.join(logs_dir, 'copy_trading.log.*')
        for log_file in glob.glob(pattern):
            file_time = datetime.fromtimestamp(os.path.getmtime(log_file))
            if file_time < cutoff_date:
                os.remove(log_file)
    except Exception:
        pass


setup_logging()


class InstrumentsCache:

    def __init__(self, db_params):
        self.db_params = db_params
        self.instruments = {}
        self.last_loaded = None
        self.logger = logging.getLogger("InstrumentsCache")

    def get_connection(self):
        return psycopg2.connect(**self.db_params)

    def create_instruments_table(self):
        sql = """
            CREATE TABLE IF NOT EXISTS instruments_master
            (
                id               SERIAL PRIMARY KEY,
                instrument_token BIGINT       NOT NULL,
                exchange_token   BIGINT,
                tradingsymbol    VARCHAR(100) NOT NULL,
                name             VARCHAR(100),
                exchange         VARCHAR(20)  NOT NULL,
                segment          VARCHAR(20),
                instrument_type  VARCHAR(20),
                lot_size         INTEGER        DEFAULT 1,
                tick_size        DECIMAL(10, 4) DEFAULT 0.05,
                expiry           DATE,
                strike           DECIMAL(20, 4),
                loaded_date      DATE         NOT NULL,
                UNIQUE (exchange, tradingsymbol, loaded_date)
            );
            CREATE INDEX IF NOT EXISTS idx_instruments_token ON instruments_master (instrument_token);
            CREATE INDEX IF NOT EXISTS idx_instruments_symbol ON instruments_master (exchange, tradingsymbol);
            CREATE INDEX IF NOT EXISTS idx_instruments_date ON instruments_master (loaded_date);
            CREATE INDEX IF NOT EXISTS idx_instruments_name ON instruments_master (name);
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        conn.commit()
        cursor.close()
        conn.close()

    def load_instruments(self, kite):
        try:
            today = datetime.now(IST).date()

            if self.last_loaded == today and self.instruments:
                self.logger.info("Instruments already loaded for today (from memory)")
                return True

            self.create_instruments_table()

            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM instruments_master WHERE loaded_date = %s", (today,))
            count = cursor.fetchone()[0]
            cursor.close()
            conn.close()

            if count > 0:
                self.logger.info(f"Loading instruments from database for {today}...")
                self.load_from_db(today)
            else:
                self.logger.info("Loading instruments from Zerodha API...")
                self.load_from_api_and_save(kite, today)

            self.last_loaded = today
            self.logger.info(f"Loaded {len(self.instruments)} instruments")
            return True

        except Exception as e:
            self.logger.error(f"Failed to load instruments: {e}")
            return False

    def load_from_db(self, today):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT instrument_token, tradingsymbol, name, exchange, segment,
                   instrument_type, lot_size, tick_size
            FROM instruments_master
            WHERE loaded_date = %s
        """, (today,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        self.instruments = {}
        for row in rows:
            key = f"{row[3]}:{row[1]}"
            self.instruments[key] = {
                'instrument_token': row[0],
                'tradingsymbol': row[1],
                'name': row[2] or '',
                'exchange': row[3],
                'segment': row[4] or '',
                'instrument_type': row[5] or '',
                'lot_size': row[6] or 1,
                'tick_size': float(row[7]) if row[7] else 0.05
            }

    def load_from_api_and_save(self, kite, today):
        nfo_instruments = kite.instruments("NFO")
        nse_instruments = kite.instruments("NSE")
        bfo_instruments = kite.instruments("BFO")

        all_instruments = nfo_instruments + nse_instruments + bfo_instruments

        self.instruments = {}
        values = []

        for inst in all_instruments:
            key = f"{inst['exchange']}:{inst['tradingsymbol']}"
            self.instruments[key] = {
                'instrument_token': inst['instrument_token'],
                'tradingsymbol': inst['tradingsymbol'],
                'name': inst.get('name', ''),
                'exchange': inst['exchange'],
                'segment': inst.get('segment', ''),
                'instrument_type': inst.get('instrument_type', ''),
                'lot_size': inst.get('lot_size', 1),
                'tick_size': inst.get('tick_size', 0.05)
            }

            expiry = inst.get('expiry')
            if expiry and hasattr(expiry, 'strftime'):
                expiry = expiry
            else:
                expiry = None

            values.append((
                inst['instrument_token'],
                inst.get('exchange_token'),
                inst['tradingsymbol'],
                inst.get('name', ''),
                inst['exchange'],
                inst.get('segment', ''),
                inst.get('instrument_type', ''),
                inst.get('lot_size', 1),
                inst.get('tick_size', 0.05),
                expiry,
                inst.get('strike'),
                today
            ))

        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM instruments_master WHERE loaded_date < %s", (today,))
        deleted_count = cursor.rowcount
        if deleted_count > 0:
            self.logger.info(f"Cleared {deleted_count} old instrument records")

        execute_values(
            cursor,
            """
            INSERT INTO instruments_master
            (instrument_token, exchange_token, tradingsymbol, name, exchange, segment,
             instrument_type, lot_size, tick_size, expiry, strike, loaded_date)
            VALUES %s
            ON CONFLICT (exchange, tradingsymbol, loaded_date) DO NOTHING
            """,
            values
        )
        conn.commit()
        cursor.close()
        conn.close()
        self.logger.info(f"Saved {len(values)} instruments to database")

    def get_lot_size(self, exchange, tradingsymbol):
        key = f"{exchange}:{tradingsymbol}"
        inst = self.instruments.get(key)
        if inst:
            return inst['lot_size']
        return 1

    def get_instrument(self, exchange, tradingsymbol):
        key = f"{exchange}:{tradingsymbol}"
        return self.instruments.get(key)

    def get_underlying(self, exchange, tradingsymbol):
        inst = self.get_instrument(exchange, tradingsymbol)
        if inst and inst.get('name'):
            return inst['name']
        return tradingsymbol


class ClientTrader:

    def __init__(self, client_config, db_params, master_account_name, master_capital, telegram_config, instruments_cache):
        self.config = client_config
        self.db_params = db_params
        self.master_account_name = master_account_name
        self.master_capital = master_capital
        self.telegram_config = telegram_config
        self.instruments_cache = instruments_cache

        self.name = client_config['name']
        self.display_name = client_config.get('display_name', self.name)
        self.api_key = client_config['api_key']
        self.access_token_file = client_config['access_token_file']
        self.capital = client_config['capital']

        self.logger = logging.getLogger(f"Client_{self.name}")
        self.kite = None
        self.access_token = None

        self.client_positions = {}
        self.position_lock = Lock()

    def get_connection(self):
        return psycopg2.connect(**self.db_params)

    def load_access_token(self):
        try:
            with open(self.access_token_file, 'r') as f:
                self.access_token = f.read().strip()
            if not self.access_token:
                raise ValueError("Empty access token")
            self.logger.info(f"Access token loaded from {self.access_token_file}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to load access token: {e}")
            return False

    def initialize(self):
        if not self.load_access_token():
            return False

        try:
            self.kite = KiteConnect(api_key=self.api_key)
            self.kite.set_access_token(self.access_token)

            profile = self.kite.profile()
            self.logger.info(f"Initialized for {profile.get('user_name', self.name)}")

            self.instruments_cache.load_instruments(self.kite)
            self.create_client_tables()
            self.load_client_positions()
            return True
        except Exception as e:
            self.logger.error(f"Initialization failed: {e}")
            return False

    def create_client_tables(self):
        sql = """
            CREATE TABLE IF NOT EXISTS client_account_positions
            (
                id                     SERIAL PRIMARY KEY,
                client_name            VARCHAR(100) NOT NULL,
                tradingsymbol          VARCHAR(100) NOT NULL,
                exchange               VARCHAR(20)  NOT NULL,
                instrument_token       BIGINT,
                product                VARCHAR(20),
                quantity               BIGINT         DEFAULT 0,
                overnight_quantity     BIGINT         DEFAULT 0,
                average_price          DECIMAL(20, 4) DEFAULT 0,
                last_price             DECIMAL(20, 4) DEFAULT 0,
                pnl                    DECIMAL(20, 4) DEFAULT 0,
                master_quantity        BIGINT         DEFAULT 0,
                target_quantity        BIGINT         DEFAULT 0,
                quantity_diff          BIGINT         DEFAULT 0,
                initial_scaling_factor DECIMAL(10, 6),
                current_scaling_factor DECIMAL(10, 6) DEFAULT 1,
                entry_date             DATE,
                entry_master_quantity  BIGINT         DEFAULT 0,
                sync_status            VARCHAR(20)    DEFAULT 'SYNCED',
                last_order_id          VARCHAR(50),
                last_order_status      VARCHAR(20),
                broker_synced_at       TIMESTAMP,
                updated_at             TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (client_name, tradingsymbol, exchange)
            );
            CREATE INDEX IF NOT EXISTS idx_client_positions_name ON client_account_positions (client_name);
            CREATE INDEX IF NOT EXISTS idx_client_positions_status ON client_account_positions (client_name, sync_status);
            CREATE INDEX IF NOT EXISTS idx_client_positions_token ON client_account_positions (instrument_token);

            CREATE TABLE IF NOT EXISTS client_trade_log
            (
                id                     SERIAL PRIMARY KEY,
                client_name            VARCHAR(100) NOT NULL,
                order_id               VARCHAR(50),
                tradingsymbol          VARCHAR(100) NOT NULL,
                exchange               VARCHAR(20),
                transaction_type       VARCHAR(10),
                quantity               BIGINT,
                price                  DECIMAL(20, 4),
                status                 VARCHAR(50),
                master_quantity        BIGINT,
                target_quantity        BIGINT,
                client_quantity_before BIGINT,
                scaling_factor         DECIMAL(10, 6),
                slice_number           INTEGER,
                total_slices           INTEGER,
                margin_required        DECIMAL(20, 4),
                error_message          TEXT,
                created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_trade_log_client ON client_trade_log (client_name);
            CREATE INDEX IF NOT EXISTS idx_trade_log_created ON client_trade_log (created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_trade_log_symbol ON client_trade_log (client_name, tradingsymbol);
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        conn.commit()
        cursor.close()
        conn.close()

    def sync_positions_from_broker(self):
        try:
            positions_data = self.kite.positions()
            net_positions = positions_data.get('net', [])

            conn = self.get_connection()
            cursor = conn.cursor()

            for pos in net_positions:
                cursor.execute("""
                    INSERT INTO client_account_positions
                    (client_name, tradingsymbol, exchange, instrument_token, product,
                     quantity, overnight_quantity, average_price, last_price, pnl,
                     broker_synced_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (client_name, tradingsymbol, exchange)
                    DO UPDATE SET instrument_token   = EXCLUDED.instrument_token,
                                  product            = EXCLUDED.product,
                                  quantity           = EXCLUDED.quantity,
                                  overnight_quantity = EXCLUDED.overnight_quantity,
                                  average_price      = EXCLUDED.average_price,
                                  last_price         = EXCLUDED.last_price,
                                  pnl                = EXCLUDED.pnl,
                                  broker_synced_at   = NOW(),
                                  updated_at         = NOW()
                """, (
                    self.name,
                    pos['tradingsymbol'],
                    pos['exchange'],
                    pos.get('instrument_token'),
                    pos.get('product'),
                    int(pos.get('quantity', 0)),
                    int(pos.get('overnight_quantity', 0)),
                    float(pos.get('average_price', 0)),
                    float(pos.get('last_price', 0)),
                    float(pos.get('pnl', 0))
                ))

            conn.commit()
            cursor.close()
            conn.close()

            with self.position_lock:
                self.client_positions = {}
                for pos in net_positions:
                    key = f"{pos['exchange']}:{pos['tradingsymbol']}"
                    self.client_positions[key] = {
                        'tradingsymbol': pos['tradingsymbol'],
                        'exchange': pos['exchange'],
                        'instrument_token': pos.get('instrument_token'),
                        'product': pos.get('product'),
                        'quantity': int(pos.get('quantity', 0)),
                        'average_price': float(pos.get('average_price', 0)),
                        'last_price': float(pos.get('last_price', 0)),
                        'pnl': float(pos.get('pnl', 0))
                    }

            open_count = sum(1 for p in self.client_positions.values() if p['quantity'] != 0)
            self.logger.info(f"Synced {len(net_positions)} positions from broker ({open_count} open)")
            return True

        except Exception as e:
            self.logger.error(f"Failed to sync positions from broker: {e}")
            return False

    def get_client_position_from_db(self, exchange, tradingsymbol):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT quantity, target_quantity, master_quantity, initial_scaling_factor,
                       current_scaling_factor, sync_status, instrument_token, product,
                       entry_date, entry_master_quantity
                FROM client_account_positions
                WHERE client_name = %s AND exchange = %s AND tradingsymbol = %s
            """, (self.name, exchange, tradingsymbol))
            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if result:
                return {
                    'quantity': int(result[0]) if result[0] else 0,
                    'target_quantity': int(result[1]) if result[1] else 0,
                    'master_quantity': int(result[2]) if result[2] else 0,
                    'initial_scaling_factor': float(result[3]) if result[3] else None,
                    'current_scaling_factor': float(result[4]) if result[4] else 1.0,
                    'sync_status': result[5] or 'SYNCED',
                    'instrument_token': result[6],
                    'product': result[7] or 'NRML',
                    'entry_date': result[8],
                    'entry_master_quantity': int(result[9]) if result[9] else 0
                }
            return None
        except Exception as e:
            self.logger.error(f"Failed to get position from DB: {e}")
            return None

    def update_position_tracking(self, exchange, tradingsymbol, master_quantity, target_quantity,
                                 scaling_factor, sync_status='PENDING', instrument_token=None,
                                 product='NRML', is_new_position=False):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            quantity_diff = target_quantity - self.get_current_quantity(exchange, tradingsymbol)
            today = datetime.now(IST).date()

            if is_new_position:
                cursor.execute("""
                    INSERT INTO client_account_positions
                    (client_name, tradingsymbol, exchange, instrument_token, product,
                     quantity, master_quantity, target_quantity, quantity_diff,
                     initial_scaling_factor, current_scaling_factor, entry_date,
                     entry_master_quantity, sync_status, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (client_name, tradingsymbol, exchange)
                    DO UPDATE SET master_quantity        = EXCLUDED.master_quantity,
                                  target_quantity        = EXCLUDED.target_quantity,
                                  quantity_diff          = EXCLUDED.quantity_diff,
                                  initial_scaling_factor = COALESCE(client_account_positions.initial_scaling_factor, EXCLUDED.initial_scaling_factor),
                                  current_scaling_factor = EXCLUDED.current_scaling_factor,
                                  entry_date             = COALESCE(client_account_positions.entry_date, EXCLUDED.entry_date),
                                  entry_master_quantity  = COALESCE(NULLIF(client_account_positions.entry_master_quantity, 0), EXCLUDED.entry_master_quantity),
                                  sync_status            = EXCLUDED.sync_status,
                                  updated_at             = NOW()
                """, (
                    self.name, tradingsymbol, exchange, instrument_token, product,
                    master_quantity, target_quantity, quantity_diff, scaling_factor, scaling_factor,
                    today, master_quantity, sync_status
                ))
            else:
                cursor.execute("""
                    UPDATE client_account_positions
                    SET master_quantity        = %s,
                        target_quantity        = %s,
                        quantity_diff          = %s,
                        current_scaling_factor = %s,
                        sync_status            = %s,
                        updated_at             = NOW()
                    WHERE client_name = %s AND exchange = %s AND tradingsymbol = %s
                """, (
                    master_quantity, target_quantity, quantity_diff, scaling_factor, sync_status,
                    self.name, exchange, tradingsymbol
                ))

            conn.commit()
            cursor.close()
            conn.close()
            return True
        except Exception as e:
            self.logger.error(f"Failed to update position tracking: {e}")
            return False

    def update_position_after_order(self, exchange, tradingsymbol, transaction_type,
                                    filled_quantity, order_id, order_status):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT quantity FROM client_account_positions
                WHERE client_name = %s AND exchange = %s AND tradingsymbol = %s
            """, (self.name, exchange, tradingsymbol))
            result = cursor.fetchone()
            current_qty = int(result[0]) if result else 0

            if transaction_type == 'BUY':
                new_qty = current_qty + filled_quantity
            else:
                new_qty = current_qty - filled_quantity

            cursor.execute("""
                UPDATE client_account_positions
                SET quantity          = %s,
                    quantity_diff     = target_quantity - %s,
                    sync_status       = CASE WHEN target_quantity = %s THEN 'SYNCED' ELSE 'PARTIAL' END,
                    last_order_id     = %s,
                    last_order_status = %s,
                    updated_at        = NOW()
                WHERE client_name = %s AND exchange = %s AND tradingsymbol = %s
            """, (new_qty, new_qty, new_qty, order_id, order_status, self.name, exchange, tradingsymbol))

            conn.commit()
            cursor.close()
            conn.close()

            with self.position_lock:
                key = f"{exchange}:{tradingsymbol}"
                if key in self.client_positions:
                    self.client_positions[key]['quantity'] = new_qty
                else:
                    self.client_positions[key] = {
                        'tradingsymbol': tradingsymbol,
                        'exchange': exchange,
                        'quantity': new_qty
                    }

            self.logger.info(f"Position updated | {tradingsymbol} | {current_qty} -> {new_qty} | Status: {order_status}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to update position after order: {e}")
            return False

    def get_current_quantity(self, exchange, tradingsymbol):
        key = f"{exchange}:{tradingsymbol}"
        with self.position_lock:
            if key in self.client_positions:
                return int(self.client_positions[key].get('quantity', 0))

        db_pos = self.get_client_position_from_db(exchange, tradingsymbol)
        if db_pos:
            return db_pos['quantity']
        return 0

    def load_client_positions(self):
        return self.sync_positions_from_broker()

    def calculate_scaling_factor(self):
        if self.master_capital <= 0:
            self.logger.warning("Master capital is zero or negative")
            return 0

        scaling_factor = self.capital / self.master_capital

        self.logger.info(
            f"Capital scaling | Master: {self.master_capital:.2f} | "
            f"Client: {self.capital:.2f} | Scaling: {scaling_factor:.4f}"
        )

        return scaling_factor

    def calculate_target_quantity(self, master_quantity, exchange, tradingsymbol, scaling_factor):
        master_qty = int(master_quantity) if master_quantity else 0
        if master_qty == 0:
            return 0

        lot_size = self.instruments_cache.get_lot_size(exchange, tradingsymbol)
        scaled_quantity = master_qty * scaling_factor

        target_lots = math.ceil(abs(scaled_quantity) / lot_size)

        if master_qty > 0:
            target_quantity = target_lots * lot_size
        else:
            target_quantity = -(target_lots * lot_size)

        return target_quantity

    def get_freeze_quantity(self, exchange, tradingsymbol):
        inst = self.instruments_cache.get_instrument(exchange, tradingsymbol)
        if not inst:
            return 900

        lot_size = inst.get('lot_size', 1)
        name = inst.get('name', '').upper()

        if exchange in ['NFO', 'BFO']:
            freeze_limits = {
                'NIFTY': 1800, 'BANKNIFTY': 900, 'FINNIFTY': 1800,
                'MIDCPNIFTY': 2800, 'SENSEX': 1000, 'BANKEX': 900
            }
            return freeze_limits.get(name, lot_size * 30)

        return 900

    def get_ltp(self, exchange, tradingsymbol, instrument_token=None):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT last_price FROM live_market_data
                WHERE instrument_token = %s OR tradingsymbol = %s
                LIMIT 1
            """, (instrument_token, tradingsymbol))
            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if result and result[0]:
                return float(result[0])
        except Exception as e:
            self.logger.warning(f"Failed to get LTP from DB: {e}")

        try:
            key = f"{exchange}:{tradingsymbol}"
            quote = self.kite.ltp([key])
            if quote and key in quote:
                return float(quote[key]['last_price'])
        except Exception as e:
            self.logger.warning(f"Failed to get LTP from API: {e}")

        return None

    def calculate_limit_price(self, ltp, transaction_type, tick_size=0.05):
        if not ltp:
            return None

        buffer_percent = 0.5

        if transaction_type == 'BUY':
            price = ltp * (1 + buffer_percent / 100)
        else:
            price = ltp * (1 - buffer_percent / 100)

        price = round(price / tick_size) * tick_size
        price = round(price, 2)

        return max(price, tick_size)

    def get_order_status(self, order_id):
        try:
            orders = self.kite.orders()
            for order in orders:
                if str(order.get('order_id')) == str(order_id):
                    return {
                        'status': order.get('status'),
                        'filled_quantity': order.get('filled_quantity', 0),
                        'pending_quantity': order.get('pending_quantity', 0),
                        'average_price': order.get('average_price', 0),
                        'status_message': order.get('status_message', ''),
                        'order_id': order_id
                    }
        except Exception as e:
            self.logger.error(f"Failed to get order status: {e}")
        return None

    def wait_for_order_completion(self, order_id, max_wait=10, check_interval=0.5):
        elapsed = 0
        status = None
        while elapsed < max_wait:
            status = self.get_order_status(order_id)
            if status:
                if status['status'] == 'COMPLETE':
                    return {'success': True, 'status': status}
                elif status['status'] in ['REJECTED', 'CANCELLED']:
                    return {'success': False, 'status': status, 'error': status.get('status_message', 'Order rejected/cancelled')}

            time.sleep(check_interval)
            elapsed += check_interval

        return {'success': False, 'status': status, 'error': 'Order timeout - not completed'}

    def cancel_order(self, order_id):
        try:
            self.kite.cancel_order(variety=self.kite.VARIETY_REGULAR, order_id=order_id)
            self.logger.info(f"Order cancelled: {order_id}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def modify_order_price(self, order_id, new_price, quantity):
        try:
            self.kite.modify_order(
                variety=self.kite.VARIETY_REGULAR,
                order_id=order_id,
                price=new_price,
                quantity=quantity
            )
            self.logger.info(f"Order modified: {order_id} | New price: {new_price}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to modify order {order_id}: {e}")
            return False

    def place_single_order(self, tradingsymbol, exchange, transaction_type, quantity, product, price,
                           master_quantity=None, target_quantity=None, client_qty_before=None,
                           scaling_factor=None, slice_number=1, total_slices=1, max_retries=3):
        inst = self.instruments_cache.get_instrument(exchange, tradingsymbol)
        tick_size = float(inst.get('tick_size', 0.05)) if inst else 0.05

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    ltp = self.get_ltp(exchange, tradingsymbol)
                    if ltp:
                        price = self.calculate_limit_price(ltp, transaction_type, tick_size)
                    self.logger.info(f"Retry {attempt + 1}/{max_retries} | New price: {price}")

                order_id = self.kite.place_order(
                    variety=self.kite.VARIETY_REGULAR,
                    exchange=exchange,
                    tradingsymbol=tradingsymbol,
                    transaction_type=transaction_type,
                    quantity=quantity,
                    product=product,
                    order_type=self.kite.ORDER_TYPE_LIMIT,
                    price=price
                )

                self.logger.info(f"ORDER PLACED | {transaction_type} {quantity} {exchange}:{tradingsymbol} @ {price} | OrderID: {order_id}")

                result = self.wait_for_order_completion(order_id, max_wait=5)

                if result['success']:
                    filled_qty = result['status'].get('filled_quantity', quantity)
                    self.log_trade(tradingsymbol, exchange, transaction_type, filled_qty, order_id, 'COMPLETE',
                                   None, price, master_quantity, target_quantity, client_qty_before,
                                   scaling_factor, slice_number, total_slices)
                    return {'success': True, 'order_id': order_id, 'status': result['status'], 'filled_quantity': filled_qty}

                status = result.get('status', {})
                order_status = status.get('status', '')

                if order_status == 'OPEN':
                    ltp = self.get_ltp(exchange, tradingsymbol)
                    if ltp:
                        new_price = self.calculate_limit_price(ltp, transaction_type, tick_size)
                        if new_price and abs(new_price - price) > tick_size:
                            self.modify_order_price(order_id, new_price, quantity)

                            result2 = self.wait_for_order_completion(order_id, max_wait=5)
                            if result2['success']:
                                filled_qty = result2['status'].get('filled_quantity', quantity)
                                self.log_trade(tradingsymbol, exchange, transaction_type, filled_qty, order_id, 'COMPLETE',
                                               None, new_price, master_quantity, target_quantity, client_qty_before,
                                               scaling_factor, slice_number, total_slices)
                                return {'success': True, 'order_id': order_id, 'status': result2['status'], 'filled_quantity': filled_qty}

                    self.cancel_order(order_id)
                    self.log_trade(tradingsymbol, exchange, transaction_type, quantity, order_id, 'CANCELLED',
                                   'Price moved, retrying', price, master_quantity, target_quantity, client_qty_before,
                                   scaling_factor, slice_number, total_slices)
                    time.sleep(0.5)
                    continue

                elif order_status in ['REJECTED', 'CANCELLED']:
                    error_msg = status.get('status_message', 'Unknown error')
                    self.logger.error(f"Order {order_status}: {error_msg}")
                    self.log_trade(tradingsymbol, exchange, transaction_type, quantity, order_id, order_status,
                                   error_msg, price, master_quantity, target_quantity, client_qty_before,
                                   scaling_factor, slice_number, total_slices)

                    if 'insufficient' in error_msg.lower() or 'margin' in error_msg.lower():
                        return {'success': False, 'error': error_msg, 'retry': False}

                    time.sleep(0.5)
                    continue

            except Exception as e:
                error_msg = str(e)
                self.logger.error(f"Order attempt {attempt + 1} failed: {error_msg}")
                self.log_trade(tradingsymbol, exchange, transaction_type, quantity, None, 'FAILED',
                               error_msg, price, master_quantity, target_quantity, client_qty_before,
                               scaling_factor, slice_number, total_slices)

                if 'insufficient' in error_msg.lower() or 'margin' in error_msg.lower():
                    return {'success': False, 'error': error_msg, 'retry': False}

                if 'token' in error_msg.lower() or 'session' in error_msg.lower():
                    return {'success': False, 'error': error_msg, 'retry': False}

                time.sleep(1)

        return {'success': False, 'error': 'Max retries exceeded'}

    def place_order(self, tradingsymbol, exchange, transaction_type, quantity, product='NRML',
                    instrument_token=None, master_quantity=0, target_quantity=0, scaling_factor=1.0):
        freeze_qty = self.get_freeze_quantity(exchange, tradingsymbol)
        lot_size = self.instruments_cache.get_lot_size(exchange, tradingsymbol)
        inst = self.instruments_cache.get_instrument(exchange, tradingsymbol)
        tick_size = float(inst.get('tick_size', 0.05)) if inst else 0.05

        client_qty_before = self.get_current_quantity(exchange, tradingsymbol)

        ltp = self.get_ltp(exchange, tradingsymbol, instrument_token)
        if not ltp:
            self.logger.error(f"Cannot get LTP for {exchange}:{tradingsymbol}")
            self.log_trade(tradingsymbol, exchange, transaction_type, quantity, None, 'FAILED',
                           'LTP not available', None, master_quantity, target_quantity, client_qty_before, scaling_factor)
            self.send_telegram_notification(f"❌ FAILED {transaction_type} {quantity} {tradingsymbol} | LTP not available")
            return []

        price = self.calculate_limit_price(ltp, transaction_type, tick_size)

        total_quantity = abs(quantity)
        total_slices = (total_quantity + freeze_qty - 1) // freeze_qty

        successful_orders = []
        failed_orders = []
        total_filled = 0
        slice_number = 0

        self.logger.info(
            f"ORDER START | {transaction_type} {total_quantity} {exchange}:{tradingsymbol} | "
            f"LTP: {ltp} | Limit: {price} | Slices: {total_slices} (freeze: {freeze_qty})"
        )

        while total_quantity > 0:
            slice_number += 1
            order_qty = min(total_quantity, freeze_qty)
            order_qty = (order_qty // lot_size) * lot_size

            if order_qty == 0:
                break

            self.logger.info(f"SLICE {slice_number}/{total_slices} | {transaction_type} {order_qty} {tradingsymbol}")

            current_client_qty = self.get_current_quantity(exchange, tradingsymbol)

            result = self.place_single_order(
                tradingsymbol, exchange, transaction_type, order_qty, product, price,
                master_quantity, target_quantity, current_client_qty, scaling_factor, slice_number, total_slices
            )

            if result['success']:
                successful_orders.append(result)
                filled = result.get('filled_quantity', order_qty)
                total_filled += filled
                total_quantity -= order_qty

                self.update_position_after_order(
                    exchange, tradingsymbol, transaction_type, filled,
                    result.get('order_id'), 'COMPLETE'
                )

                self.logger.info(
                    f"SLICE {slice_number}/{total_slices} COMPLETE | Filled: {filled} | "
                    f"Total filled: {total_filled}/{abs(quantity)} | Remaining: {total_quantity}"
                )

                if total_quantity > 0:
                    time.sleep(0.3)
                    ltp = self.get_ltp(exchange, tradingsymbol, instrument_token)
                    if ltp:
                        price = self.calculate_limit_price(ltp, transaction_type, tick_size)
            else:
                failed_orders.append(result)
                error_msg = result.get('error', 'Unknown')

                self.logger.warning(f"SLICE {slice_number}/{total_slices} FAILED | Error: {error_msg}")

                if result.get('retry') == False:
                    self.logger.error(f"Non-retryable error, stopping all slices: {error_msg}")
                    self.update_position_sync_status(exchange, tradingsymbol, 'PARTIAL',
                                                     f"Stopped at slice {slice_number}/{total_slices}: {error_msg}")
                    break

                time.sleep(0.5)
                ltp = self.get_ltp(exchange, tradingsymbol, instrument_token)
                if ltp:
                    price = self.calculate_limit_price(ltp, transaction_type, tick_size)

                self.logger.info(f"SLICE {slice_number}/{total_slices} RETRY | New price: {price}")

                current_client_qty = self.get_current_quantity(exchange, tradingsymbol)
                result2 = self.place_single_order(
                    tradingsymbol, exchange, transaction_type, order_qty, product, price,
                    master_quantity, target_quantity, current_client_qty, scaling_factor, slice_number, total_slices
                )

                if result2['success']:
                    successful_orders.append(result2)
                    filled = result2.get('filled_quantity', order_qty)
                    total_filled += filled
                    total_quantity -= order_qty

                    self.update_position_after_order(
                        exchange, tradingsymbol, transaction_type, filled,
                        result2.get('order_id'), 'COMPLETE'
                    )

                    self.logger.info(
                        f"SLICE {slice_number}/{total_slices} RETRY COMPLETE | Filled: {filled} | "
                        f"Total filled: {total_filled}/{abs(quantity)}"
                    )
                else:
                    self.logger.error(f"SLICE {slice_number}/{total_slices} RETRY FAILED | Stopping")
                    self.update_position_sync_status(exchange, tradingsymbol, 'PARTIAL',
                                                     f"Failed at slice {slice_number}/{total_slices}")
                    break

        final_client_qty = self.get_current_quantity(exchange, tradingsymbol)

        if total_filled == abs(quantity):
            sync_status = 'SYNCED'
        elif total_filled > 0:
            sync_status = 'PARTIAL'
        else:
            sync_status = 'FAILED'

        self.update_position_sync_status(exchange, tradingsymbol, sync_status, None)

        self.logger.info(
            f"ORDER SUMMARY | {tradingsymbol} | {transaction_type} | "
            f"Requested: {abs(quantity)} | Filled: {total_filled} | "
            f"Slices: {len(successful_orders)}/{total_slices} | "
            f"Position: {client_qty_before} -> {final_client_qty} | Status: {sync_status}"
        )

        if successful_orders:
            self.send_telegram_notification(
                f"✅ {transaction_type} {total_filled}/{abs(quantity)} {tradingsymbol} | "
                f"Slices: {len(successful_orders)}/{total_slices} | Price: ~{price}"
            )

        if failed_orders and not successful_orders:
            self.send_telegram_notification(
                f"❌ FAILED {transaction_type} {quantity} {tradingsymbol} | "
                f"Error: {failed_orders[0].get('error', 'Unknown')}"
            )
        elif failed_orders and successful_orders:
            self.send_telegram_notification(
                f"⚠️ PARTIAL {transaction_type} {total_filled}/{abs(quantity)} {tradingsymbol} | "
                f"Slices: {len(successful_orders)}/{total_slices}"
            )

        return successful_orders

    def update_position_sync_status(self, exchange, tradingsymbol, sync_status, status_message=None):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE client_account_positions
                SET sync_status   = %s,
                    quantity_diff = target_quantity - quantity,
                    updated_at    = NOW()
                WHERE client_name = %s AND exchange = %s AND tradingsymbol = %s
            """, (sync_status, self.name, exchange, tradingsymbol))
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            self.logger.error(f"Failed to update sync status: {e}")

    def update_entry_master_quantity(self, exchange, tradingsymbol, new_master_quantity):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE client_account_positions
                SET entry_master_quantity = %s, updated_at = NOW()
                WHERE client_name = %s AND exchange = %s AND tradingsymbol = %s
            """, (new_master_quantity, self.name, exchange, tradingsymbol))
            conn.commit()
            cursor.close()
            conn.close()
            self.logger.info(f"Updated entry_master_quantity for {tradingsymbol}: {new_master_quantity}")
        except Exception as e:
            self.logger.error(f"Failed to update entry_master_quantity: {e}")

    def log_trade(self, tradingsymbol, exchange, transaction_type, quantity, order_id, status,
                  error=None, price=None, master_quantity=None, target_quantity=None,
                  client_quantity_before=None, scaling_factor=None, slice_number=None, total_slices=None):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO client_trade_log
                (client_name, order_id, tradingsymbol, exchange, transaction_type, quantity,
                 price, status, error_message, master_quantity, target_quantity,
                 client_quantity_before, scaling_factor, slice_number, total_slices)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (self.name, order_id, tradingsymbol, exchange, transaction_type, quantity,
                  price, status, error, master_quantity, target_quantity,
                  client_quantity_before, scaling_factor, slice_number, total_slices))
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            self.logger.error(f"Failed to log trade: {e}")

    def sync_position(self, master_pos, current_scaling_factor):
        exchange = master_pos['exchange']
        tradingsymbol = master_pos['tradingsymbol']
        master_qty = int(master_pos['quantity']) if master_pos['quantity'] else 0
        instrument_token = master_pos.get('instrument_token')
        product = master_pos.get('product', 'NRML')

        existing_position = self.get_client_position_from_db(exchange, tradingsymbol)
        client_quantity = self.get_current_quantity(exchange, tradingsymbol)
        lot_size = self.instruments_cache.get_lot_size(exchange, tradingsymbol)

        is_new_position = False
        is_adding_to_position = False
        effective_scaling_factor = current_scaling_factor

        correct_target = self.calculate_target_quantity(
            master_qty, exchange, tradingsymbol, current_scaling_factor
        )

        if existing_position and existing_position.get('initial_scaling_factor'):
            locked_scaling_factor = existing_position['initial_scaling_factor']
            entry_master_qty = existing_position.get('entry_master_quantity', 0)
            entry_date = existing_position.get('entry_date')

            today = datetime.now(IST).date()
            is_same_day = entry_date == today if entry_date else False

            if master_qty == 0:
                target_quantity = 0
                effective_scaling_factor = locked_scaling_factor
                if client_quantity != 0:
                    self.logger.info(f"SYNC | {tradingsymbol} | Master closed | Client: {client_quantity} -> 0")

            elif client_quantity == 0 and master_qty != 0:
                is_new_position = True
                target_quantity = correct_target
                effective_scaling_factor = current_scaling_factor
                self.reset_position_tracking(exchange, tradingsymbol, master_qty, current_scaling_factor)
                self.logger.info(
                    f"SYNC | {tradingsymbol} | NEW (client has 0) | Master: {master_qty} | "
                    f"Target: {target_quantity} | Scale: {current_scaling_factor:.4f}"
                )

            elif is_same_day and abs(master_qty) >= abs(entry_master_qty):
                if abs(master_qty) > abs(entry_master_qty):
                    is_adding_to_position = True
                    self.logger.info(
                        f"SYNC | {tradingsymbol} | Master ADDED | "
                        f"Entry: {entry_master_qty} -> Current: {master_qty} | "
                        f"Target: {correct_target} | Scale: {current_scaling_factor:.4f}"
                    )
                target_quantity = correct_target
                effective_scaling_factor = current_scaling_factor

            elif is_same_day and abs(master_qty) < abs(entry_master_qty):
                reduction_ratio = abs(master_qty) / abs(entry_master_qty)
                target_quantity = int((client_quantity * reduction_ratio) / lot_size) * lot_size
                effective_scaling_factor = locked_scaling_factor
                self.logger.info(
                    f"SYNC | {tradingsymbol} | Master REDUCED | "
                    f"Entry: {entry_master_qty} -> Current: {master_qty} | "
                    f"Ratio: {reduction_ratio:.2f} | Target: {target_quantity}"
                )

            elif not is_same_day:
                target_quantity = correct_target
                effective_scaling_factor = current_scaling_factor
                self.reset_position_tracking(exchange, tradingsymbol, master_qty, current_scaling_factor)
                self.logger.info(
                    f"SYNC | {tradingsymbol} | OVERNIGHT | Reset entry to {master_qty} | "
                    f"Target: {target_quantity} | Client: {client_quantity}"
                )

            else:
                target_quantity = correct_target
                effective_scaling_factor = current_scaling_factor

        else:
            is_new_position = True
            target_quantity = correct_target
            effective_scaling_factor = current_scaling_factor

            if master_qty != 0:
                self.logger.info(
                    f"SYNC | {tradingsymbol} | NEW POSITION | Master: {master_qty} | "
                    f"Target: {target_quantity} | Scale: {current_scaling_factor:.4f}"
                )

        self.update_position_tracking(
            exchange, tradingsymbol, master_qty, target_quantity,
            effective_scaling_factor,
            'PENDING' if target_quantity != client_quantity else 'SYNCED',
            instrument_token, product, is_new_position
        )

        quantity_diff = target_quantity - client_quantity

        if quantity_diff == 0:
            return

        if abs(quantity_diff) < lot_size:
            return

        lots_to_trade = int(abs(quantity_diff) / lot_size)
        trade_quantity = lots_to_trade * lot_size

        if trade_quantity == 0:
            return

        transaction_type = 'BUY' if quantity_diff > 0 else 'SELL'

        self.logger.info(
            f"SYNC | {tradingsymbol} | Master: {master_qty} | "
            f"Target: {target_quantity} | Client: {client_quantity} | {transaction_type} {trade_quantity}"
        )

        successful_orders = self.place_order(
            tradingsymbol,
            exchange,
            transaction_type,
            trade_quantity,
            product,
            instrument_token,
            master_qty,
            target_quantity,
            effective_scaling_factor
        )

        if successful_orders and is_adding_to_position:
            self.update_entry_master_quantity(exchange, tradingsymbol, master_qty)

    def reset_position_tracking(self, exchange, tradingsymbol, master_qty, scaling_factor):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            today = datetime.now(IST).date()

            cursor.execute("""
                UPDATE client_account_positions
                SET entry_master_quantity  = %s,
                    initial_scaling_factor = %s,
                    current_scaling_factor = %s,
                    entry_date             = %s,
                    updated_at             = NOW()
                WHERE client_name = %s
                  AND exchange = %s
                  AND tradingsymbol = %s
            """, (master_qty, scaling_factor, scaling_factor, today,
                  self.name, exchange, tradingsymbol))

            conn.commit()
            cursor.close()
            conn.close()

        except Exception as e:
            self.logger.error(f"Failed to reset position tracking: {e}")

    def send_telegram_notification(self, message):
        if not self.config.get('notifications', {}).get('telegram_enabled', False):
            return

        try:
            url = f"https://api.telegram.org/bot{self.telegram_config['token']}/sendMessage"
            payload = {
                'chat_id': self.telegram_config['notification_chat_id'],
                'text': f"[{self.display_name}] {message}",
                'parse_mode': 'HTML'
            }
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            self.logger.error(f"Telegram notification failed: {e}")


class CopyTradingExecutor:

    def __init__(self, config_path):
        self.config_path = config_path
        self.config = None
        self.db_params = None
        self.clients = []
        self.master_account_name = None
        self.master_capital = 0
        self.instruments_cache = None
        self.logger = logging.getLogger("CopyTradingExecutor")
        self.is_running = False
        self.last_master_positions = {}

    def load_config(self):
        with open(self.config_path, 'r') as f:
            self.config = yaml.safe_load(f)

        self.db_params = {
            'host': self.config['database']['host'],
            'port': self.config['database']['port'],
            'dbname': self.config['database']['name'],
            'user': self.config['database']['user'],
            'password': self.config['database']['password']
        }

        self.master_account_name = self.config['master_account']['name']
        self.master_capital = self.config['master_account']['estimated_capital']
        self.instruments_cache = InstrumentsCache(self.db_params)

        log_level = self.config['global_settings'].get('log_level', 'INFO')
        logging.getLogger().setLevel(getattr(logging, log_level))

        self.logger.info(f"Configuration loaded | Master: {self.master_account_name} | Capital: {self.master_capital}")

    def get_connection(self):
        return psycopg2.connect(**self.db_params)

    def initialize_clients(self):
        telegram_config = self.config.get('telegram', {})

        for client_config in self.config.get('clients', []):
            if not client_config.get('enabled', False):
                self.logger.info(f"Skipping disabled client: {client_config['name']}")
                continue

            client = ClientTrader(
                client_config,
                self.db_params,
                self.master_account_name,
                self.master_capital,
                telegram_config,
                self.instruments_cache
            )

            if client.initialize():
                self.clients.append(client)
                self.logger.info(f"Client initialized: {client.display_name} | Capital: {client.capital}")
            else:
                self.logger.error(f"Failed to initialize client: {client_config['name']}")

        self.logger.info(f"Initialized {len(self.clients)} clients")
        return len(self.clients) > 0

    def fetch_master_positions(self):
        try:
            conn = self.get_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT tradingsymbol, exchange, instrument_token, product, quantity,
                       average_price, last_price, pnl, buy_quantity, sell_quantity, multiplier
                FROM master_account_positions
                WHERE master_account_name = %s
            """, (self.master_account_name,))
            positions = cursor.fetchall()
            cursor.close()
            conn.close()
            return [dict(p) for p in positions]
        except Exception as e:
            self.logger.error(f"Failed to fetch master positions: {e}")
            return []

    def positions_changed(self, new_positions):
        new_map = {f"{p['exchange']}:{p['tradingsymbol']}": p['quantity'] for p in new_positions}

        if new_map != self.last_master_positions:
            self.last_master_positions = new_map
            return True
        return False

    def sync_all_clients(self, master_positions):
        for client in self.clients:
            try:
                client.load_client_positions()

                scaling_factor = client.calculate_scaling_factor()

                if scaling_factor <= 0:
                    self.logger.warning(f"Skipping {client.name} - invalid scaling factor")
                    continue

                for master_pos in master_positions:
                    client.sync_position(master_pos, scaling_factor)

            except Exception as e:
                self.logger.error(f"Error syncing client {client.name}: {e}")

    def is_market_hours(self):
        now = datetime.now(IST)

        if now.weekday() >= 5:
            return False

        market_start = datetime.strptime("09:15", '%H:%M').time()
        market_end = datetime.strptime("15:30", '%H:%M').time()

        return market_start <= now.time() <= market_end

    def run(self):
        self.load_config()

        if not self.initialize_clients():
            self.logger.error("No clients initialized. Exiting.")
            return

        self.is_running = True
        sync_interval = self.config['global_settings']['position_sync_interval']
        status_interval = self.config['global_settings'].get('status_log_interval', 30)

        self.logger.info(f"Starting copy trading | Sync interval: {sync_interval}s | Status log: every {status_interval}s")

        loop_count = 0

        while self.is_running:
            try:
                if not self.is_market_hours():
                    if loop_count % 60 == 0:
                        self.logger.info("Outside market hours, waiting...")
                    time.sleep(60)
                    loop_count += 1
                    continue

                master_positions = self.fetch_master_positions()

                if self.positions_changed(master_positions):
                    open_count = sum(1 for p in master_positions if p.get('quantity', 0) != 0)
                    self.logger.info(f"Position change detected | Master: {len(master_positions)} ({open_count} open)")
                    self.sync_all_clients(master_positions)

                loop_count += 1

                if loop_count % status_interval == 0:
                    open_positions = sum(1 for p in master_positions if p.get('quantity', 0) != 0)
                    client_summary = []
                    for client in self.clients:
                        client_open = sum(1 for p in client.client_positions.values() if p.get('quantity', 0) != 0)
                        client_summary.append(f"{client.name}:{client_open}")

                    self.logger.info(
                        f"MONITOR | Loop: {loop_count} | Master open: {open_positions} | "
                        f"Clients: {', '.join(client_summary)}"
                    )

                time.sleep(sync_interval)

            except KeyboardInterrupt:
                break
            except Exception as e:
                self.logger.error(f"Error: {e}")
                time.sleep(5)

        self.logger.info("Copy trading stopped")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Copy Trading Executor')
    parser.add_argument('--config', default='clients_config.yaml', help='Config file path')
    args = parser.parse_args()

    executor = CopyTradingExecutor(args.config)
    try:
        executor.run()
    except KeyboardInterrupt:
        print("\nShutting down...")
