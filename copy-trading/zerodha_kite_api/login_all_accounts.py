import os
import yaml
import requests
import threading
from flask import Flask
from kiteconnect import KiteConnect
import pyotp
import logging
from werkzeug.serving import make_server
import psycopg2
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

PORT = 5000
HOST = "127.0.0.1"

CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'clients_config.yaml')


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


CONFIG = load_config()
DB = CONFIG['database']
TG = CONFIG.get('telegram', {})

TELEGRAM_TOKEN = TG.get('token', '')
CHAT_ID_ERROR = TG.get('error_chat_id', '')

# Build accounts list: master first, then all enabled clients
ACCOUNTS = []

master = CONFIG['master_account']
ACCOUNTS.append({
    'name': master['name'],
    'user_id': master['user_id'],
    'password': master['password'],
    'totp_key': master['totp_key'],
    'api_key': master['api_key'],
    'api_secret': master['api_secret'],
    'access_token_file': master['access_token_file'],
})

for client in CONFIG.get('clients', []):
    if client.get('enabled', False):
        ACCOUNTS.append({
            'name': client['name'],
            'user_id': client['user_id'],
            'password': client['password'],
            'totp_key': client['totp_key'],
            'api_key': client['api_key'],
            'api_secret': client['api_secret'],
            'access_token_file': client['access_token_file'],
        })


class DatabaseManager:

    def __init__(self):
        self.conn_params = {
            'host': DB['host'],
            'port': DB['port'],
            'dbname': DB['name'],
            'user': DB['user'],
            'password': DB['password'],
        }

    def get_connection(self):
        return psycopg2.connect(**self.conn_params)

    def create_table_if_not_exists(self):
        sql = """
            CREATE TABLE IF NOT EXISTS master_accounts (
                id                 SERIAL PRIMARY KEY,
                name               VARCHAR(100) UNIQUE NOT NULL,
                user_id            VARCHAR(50)         NOT NULL,
                api_key            VARCHAR(100)        NOT NULL,
                api_secret         VARCHAR(100)        NOT NULL,
                totp_key           VARCHAR(100)        NOT NULL,
                password           VARCHAR(100)        NOT NULL,
                access_token       VARCHAR(255),
                request_token      VARCHAR(255),
                token_generated_at TIMESTAMP,
                is_active          BOOLEAN   DEFAULT TRUE,
                created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        conn.commit()
        cursor.close()
        conn.close()
        logging.info("Table master_accounts verified/created")

    def save_token(self, account_name, user_id, api_key, api_secret, totp_key, password, access_token, request_token):
        sql = """
            INSERT INTO master_accounts (name, user_id, api_key, api_secret, totp_key, password,
                                         access_token, request_token, token_generated_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
                access_token       = EXCLUDED.access_token,
                request_token      = EXCLUDED.request_token,
                token_generated_at = EXCLUDED.token_generated_at,
                updated_at         = EXCLUDED.updated_at
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        now = datetime.now()
        cursor.execute(sql, (account_name, user_id, api_key, api_secret, totp_key, password,
                             access_token, request_token, now, now))
        conn.commit()
        cursor.close()
        conn.close()
        logging.info(f"Token saved to database for {account_name}")


class ServerThread(threading.Thread):

    def __init__(self, app):
        threading.Thread.__init__(self)
        self.server = make_server(HOST, PORT, app)
        self.ctx = app.app_context()
        self.ctx.push()

    def run(self):
        logging.info('Starting server')
        self.server.serve_forever()

    def shutdown(self):
        self.server.shutdown()


class KiteLoginManager:

    def __init__(self):
        self.db_manager = DatabaseManager()
        self.server = None
        self.results = []

    def start_server(self):
        app = Flask(__name__)
        self.server = ServerThread(app)
        self.server.start()
        logging.info('Server started')

    def stop_server(self):
        if self.server:
            self.server.shutdown()
            logging.info('Server stopped')

    def telegram_post_message(self, text, chat_id):
        if not TELEGRAM_TOKEN or not chat_id:
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(url, json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}, timeout=5)
        except Exception as e:
            logging.error(f"Telegram error: {e}")

    def login_single_account(self, account):
        account_name = account['name']
        user_id = account['user_id']
        password = account['password']
        totp_key = account['totp_key']
        api_key = account['api_key']
        api_secret = account['api_secret']
        access_token_file = account['access_token_file']

        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                logging.info(f"Attempting login for {account_name} (Attempt {retry_count + 1}/{max_retries})")

                session = requests.Session()
                twofa = pyotp.TOTP(totp_key).now()
                login_resp = session.post(
                    "https://kite.zerodha.com/api/login",
                    data={"user_id": user_id, "password": password},
                ).json()
                request_id = login_resp["data"]["request_id"]
                session.post(
                    "https://kite.zerodha.com/api/twofa",
                    data={"user_id": user_id, "request_id": request_id, "twofa_value": twofa},
                )
                api_session = session.get(f"https://kite.trade/connect/login?api_key={api_key}")
                request_token = api_session.url.split("request_token=")[1].split("&")[0]

                kite = KiteConnect(api_key=api_key)
                data = kite.generate_session(request_token, api_secret=api_secret)
                access_token = data["access_token"]

                os.makedirs(os.path.dirname(os.path.abspath(access_token_file)), exist_ok=True)
                with open(access_token_file, 'w') as f:
                    f.write(access_token)

                self.db_manager.save_token(
                    account_name, user_id, api_key, api_secret, totp_key, password,
                    access_token, request_token
                )

                self.telegram_post_message(
                    f"✅ <b>{account_name}</b>\nAccess token generated!\nUser ID: {user_id}",
                    CHAT_ID_ERROR
                )
                logging.info(f"Login successful for {account_name}")
                return {
                    'account_name': account_name,
                    'user_id': user_id,
                    'access_token': access_token,
                    'kite': kite,
                    'success': True,
                    'error': None,
                }

            except Exception as e:
                retry_count += 1
                logging.error(f"Login failed for {account_name}: {e}")
                if retry_count >= max_retries:
                    self.telegram_post_message(
                        f"❌ <b>{account_name}</b>\nLogin FAILED after {max_retries} attempts!\nError: {e}",
                        CHAT_ID_ERROR
                    )
                    return {
                        'account_name': account_name,
                        'user_id': user_id,
                        'access_token': None,
                        'kite': None,
                        'success': False,
                        'error': str(e),
                    }

    def login_all_accounts(self):
        self.db_manager.create_table_if_not_exists()
        total = len(ACCOUNTS)
        successful = 0
        failed = 0

        self.telegram_post_message(
            f"🚀 <b>Starting Kite Login</b>\nTotal accounts: {total}",
            CHAT_ID_ERROR
        )

        for i, account in enumerate(ACCOUNTS, 1):
            logging.info(f"Processing account {i}/{total}: {account['name']}")
            result = self.login_single_account(account)
            self.results.append(result)
            if result['success']:
                successful += 1
            else:
                failed += 1

        summary = f"📊 <b>Login Summary</b>\nTotal: {total} | ✅ {successful} | ❌ {failed}"
        self.telegram_post_message(summary, CHAT_ID_ERROR)
        return self.results


if __name__ == "__main__":
    logging.info(f"Starting local server on {HOST}:{PORT}")
    manager = KiteLoginManager()
    manager.start_server()
    try:
        results = manager.login_all_accounts()
        print("\n" + "=" * 60)
        print("LOGIN RESULTS SUMMARY")
        print("=" * 60)
        for r in results:
            if r['success']:
                print(f"\n✅ {r['account_name']} ({r['user_id']})")
                print(f"   Access Token: {r['access_token'][:20]}...")
                print(f"   Profile: {r['kite'].profile()}")
            else:
                print(f"\n❌ {r['account_name']} ({r['user_id']})")
                print(f"   Error: {r['error']}")
        print("\n" + "=" * 60)
    except Exception as e:
        logging.error(f"Critical error: {e}")
        manager.telegram_post_message(f"Critical Error: {e}", CHAT_ID_ERROR)
    finally:
        manager.stop_server()
