#  NIFTY 50 0920 Short strangle, % based SL

from kiteconnect import KiteConnect
import pandas as pd
import datetime as dt
import time
import logging
import threading

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURABLE PARAMETERS - MODIFY THESE AS NEEDED
# =============================================================================

# Trading Configuration
LOTS = 1  # Number of lots to trade
LOT_SIZE = 75  # NIFTY lot size (changed from 50 to 75 from March 2025)
CE_STOPLOSS_PERCENT = 25  # Call option stop loss percentage
PE_STOPLOSS_PERCENT = 25  # Put option stop loss percentage

# Strangle width: how many points away from ATM to sell each leg.
# NIFTY strikes are in 50-point intervals; 50 = 1 strike OTM
STRANGLE_WIDTH = 50

# Stop Loss Configuration
SL_SLIPPAGE_BUFFER = 5.0  # Additional points buffer for slippage protection
SL_MONITORING_INTERVAL = 0.5  # Seconds between SL monitoring checks
MAX_LOSS_MULTIPLIER = 1.5  # Maximum loss allowed (1.5x of intended SL)
ENABLE_SL_MONITORING = True  # Enable continuous SL monitoring

# API Configuration
API_KEY = ""  # Your Zerodha API key
API_SECRET = ""  # API secret (if needed)
ACCESS_TOKEN_FILE = "/home/ubuntu/access_token.txt"

# Trading Times
OPEN_TIME = dt.time(hour=9, minute=15)
TRADE_ENTRY_TIME = dt.time(hour=9, minute=20)
RE_ENTRY_TIME = dt.time(hour=12, minute=30)
SQUARE_OFF_TIME = dt.time(hour=15, minute=6)

# NSE Holidays for 2025 - UPDATE AS NEEDED
NSE_HOLIDAYS = [
    dt.date(2025, 1, 26), dt.date(2025, 3, 14), dt.date(2025, 3, 31),
    dt.date(2025, 4, 11), dt.date(2025, 4, 14), dt.date(2025, 4, 18),
    dt.date(2025, 5, 1), dt.date(2025, 8, 15), dt.date(2025, 10, 2),
    dt.date(2025, 10, 31), dt.date(2025, 11, 15), dt.date(2025, 12, 25)
]

# =============================================================================
# END OF CONFIGURABLE PARAMETERS
# =============================================================================

class NiftyStrangleBot:
    def __init__(self):
        self.lots = LOTS
        self.lot_size = LOT_SIZE
        self.ce_stoploss_per = CE_STOPLOSS_PERCENT
        self.pe_stoploss_per = PE_STOPLOSS_PERCENT
        self.strangle_width = STRANGLE_WIDTH
        self.sl_slippage_buffer = SL_SLIPPAGE_BUFFER
        self.sl_monitoring_interval = SL_MONITORING_INTERVAL
        self.max_loss_multiplier = MAX_LOSS_MULTIPLIER
        self.enable_sl_monitoring = ENABLE_SL_MONITORING

        self.nse_holidays = NSE_HOLIDAYS

        self.api_key = API_KEY
        self.api_secret = API_SECRET

        self.open_time = OPEN_TIME
        self.trade_entry_time = TRADE_ENTRY_TIME
        self.re_entry_time = RE_ENTRY_TIME
        self.sqf_time = SQUARE_OFF_TIME

        self.access_token = self.load_access_token()
        self.kite = KiteConnect(api_key=self.api_key)
        self.kite.set_access_token(self.access_token)

        self.bn_ltp = None
        self.atm_strike = None
        self.ce_symbol = None
        self.pe_symbol = None
        self.ce_order_id = None
        self.pe_order_id = None
        self.ce_sell_price = None
        self.pe_sell_price = None
        self.ce_sl_orderid = None
        self.pe_sl_orderid = None
        self.bn_exp_df = None
        self.expiry_date = None

    def load_access_token(self):
        try:
            with open(ACCESS_TOKEN_FILE, 'r') as f:
                return f.read().strip()
        except FileNotFoundError:
            logger.error(f"Access token file not found: {ACCESS_TOKEN_FILE}")
            raise

    def get_expiry_date(self):
        current_date = dt.date.today()
        wd = current_date.weekday()

        if wd <= 3:
            x = (3 - wd)
        else:
            x = 6

        exp_date = current_date + dt.timedelta(days=x)

        while exp_date in self.nse_holidays:
            exp_date = exp_date - dt.timedelta(days=1)

        return exp_date

    def find_nearest_expiry(self, nifty_df, target_expiry):
        available_expiries = sorted(nifty_df['expiry'].unique())
        logger.info(f"Available expiry dates: {available_expiries}")

        if target_expiry in available_expiries:
            return target_expiry

        nearest_expiry = min(available_expiries, key=lambda x: abs((x - target_expiry).days))
        logger.info(f"Target expiry {target_expiry} not found. Using nearest expiry: {nearest_expiry}")
        return nearest_expiry

    def get_nifty_atm_strike(self, ltp):
        r = ltp % 50
        if r < 25:
            atm = ltp - r
        else:
            atm = ltp - r + 50
        return int(atm)

    def get_trading_symbol(self, df, strike, option_type):
        try:
            df_filtered = df[df.strike == strike]

            if len(df_filtered) == 0:
                unique_strikes = sorted(df['strike'].unique())
                if not unique_strikes:
                    raise ValueError("No strikes available in the instrument data")

                closest_strike = min(unique_strikes, key=lambda x: abs(x - strike))
                logger.info(f"Exact strike {strike} not found. Using closest available strike: {closest_strike}")
                df_filtered = df[df.strike == closest_strike]
                strike = closest_strike

            available_types = df_filtered['instrument_type'].unique()
            if option_type not in available_types:
                raise ValueError(f"Option type {option_type} not available for strike {strike}. Available types: {available_types}")

            symbol_data = df_filtered[df_filtered.instrument_type == option_type]
            if len(symbol_data) == 0:
                raise ValueError(f"No {option_type} option found for strike {strike}")

            symbol = symbol_data.tradingsymbol.values[0]
            logger.info(f"Found {option_type} symbol for strike {strike}: {symbol}")
            return symbol

        except Exception as e:
            logger.error(f"Error getting trading symbol for strike {strike}, type {option_type}: {str(e)}")
            raise

    def get_nifty_ltp(self, max_retries=10):
        for attempt in range(max_retries):
            try:
                nifty_data = self.kite.ltp('NSE:NIFTY 50')
                ltp = nifty_data['NSE:NIFTY 50']['last_price']
                logger.info(f"NIFTY 50 LTP: {ltp}")
                return ltp
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1}: Can't extract LTP data - {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                else:
                    raise Exception("Unable to fetch NIFTY LTP after maximum retries")

    def get_option_ltp(self, symbol, max_retries=5):
        for attempt in range(max_retries):
            try:
                nfo_symbol = f'NFO:{symbol}'
                option_data = self.kite.ltp([nfo_symbol])
                ltp = option_data[nfo_symbol]['last_price']
                return ltp
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1}: Failed to get LTP for {symbol} - {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                else:
                    logger.error(f"Failed to get LTP for {symbol} after {max_retries} attempts")
                    return None

    def check_manual_exit_conditions(self, ce_ltp, pe_ltp):
        try:
            ce_loss = max(0, ce_ltp - self.ce_sell_price)
            pe_loss = max(0, pe_ltp - self.pe_sell_price)

            ce_intended_sl = self.ce_sell_price * self.ce_stoploss_per / 100
            pe_intended_sl = self.pe_sell_price * self.pe_stoploss_per / 100

            ce_max_loss = ce_intended_sl * self.max_loss_multiplier
            pe_max_loss = pe_intended_sl * self.max_loss_multiplier

            ce_exit_needed = ce_loss > ce_max_loss
            pe_exit_needed = pe_loss > pe_max_loss

            if ce_exit_needed or pe_exit_needed:
                logger.warning(f"EXCESSIVE LOSS DETECTED!")
                logger.warning(f"CE - Current Loss: {ce_loss:.2f}, Max Allowed: {ce_max_loss:.2f}, Exit Needed: {ce_exit_needed}")
                logger.warning(f"PE - Current Loss: {pe_loss:.2f}, Max Allowed: {pe_max_loss:.2f}, Exit Needed: {pe_exit_needed}")

            return ce_exit_needed, pe_exit_needed

        except Exception as e:
            logger.error(f"Error in manual exit condition check: {str(e)}")
            return False, False

    def emergency_exit_position(self, option_type, symbol, quantity):
        try:
            logger.critical(f"EMERGENCY EXIT: Placing market buy order for {symbol}")
            order_id = self.place_market_order_buy(symbol, quantity)
            logger.critical(f"Emergency exit order placed: {order_id}")
            return order_id
        except Exception as e:
            logger.critical(f"FAILED TO PLACE EMERGENCY EXIT for {symbol}: {str(e)}")
            return None

    def monitor_stop_loss_continuously(self):
        if not self.enable_sl_monitoring:
            return

        logger.info("Starting continuous stop loss monitoring...")

        ce_sl_level = self.ce_sell_price * (1 + self.ce_stoploss_per / 100)
        pe_sl_level = self.pe_sell_price * (1 + self.pe_stoploss_per / 100)

        quantity = self.lots * self.lot_size
        ce_position_open = True
        pe_position_open = True

        while (ce_position_open or pe_position_open) and dt.datetime.now().time() < self.sqf_time:
            try:
                ce_ltp = self.get_option_ltp(self.ce_symbol) if ce_position_open else None
                pe_ltp = self.get_option_ltp(self.pe_symbol) if pe_position_open else None

                if ce_position_open:
                    ce_sl_status = self.get_order_status(self.ce_sl_orderid)
                    if ce_sl_status == 'executed':
                        logger.info("CE stop loss executed normally")
                        ce_position_open = False

                if pe_position_open:
                    pe_sl_status = self.get_order_status(self.pe_sl_orderid)
                    if pe_sl_status == 'executed':
                        logger.info("PE stop loss executed normally")
                        pe_position_open = False

                if ce_position_open and pe_position_open and ce_ltp and pe_ltp:
                    ce_exit_needed, pe_exit_needed = self.check_manual_exit_conditions(ce_ltp, pe_ltp)

                    if ce_exit_needed and ce_position_open:
                        logger.critical("CE SLIPPAGE PROTECTION TRIGGERED")
                        self.cancel_order(self.ce_sl_orderid)
                        self.emergency_exit_position("CE", self.ce_symbol, quantity)
                        ce_position_open = False

                    if pe_exit_needed and pe_position_open:
                        logger.critical("PE SLIPPAGE PROTECTION TRIGGERED")
                        self.cancel_order(self.pe_sl_orderid)
                        self.emergency_exit_position("PE", self.pe_symbol, quantity)
                        pe_position_open = False

                if ce_position_open or pe_position_open:
                    logger.info(f"Monitoring - CE LTP: {ce_ltp if ce_ltp else 'N/A'} (SL: {ce_sl_level:.2f}), "
                                f"PE LTP: {pe_ltp if pe_ltp else 'N/A'} (SL: {pe_sl_level:.2f})")

                time.sleep(self.sl_monitoring_interval)

            except Exception as e:
                logger.error(f"Error in continuous SL monitoring: {str(e)}")
                time.sleep(1)

        logger.info("Stop loss monitoring completed")

    def place_market_order_sell(self, symbol, quantity):
        try:
            order_id = self.kite.place_order(
                tradingsymbol=symbol,
                exchange=self.kite.EXCHANGE_NFO,
                transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                quantity=quantity,
                order_type=self.kite.ORDER_TYPE_MARKET,
                product=self.kite.PRODUCT_MIS,
                variety=self.kite.VARIETY_REGULAR
            )
            logger.info(f"Sell order placed for {symbol}: {order_id}")
            return order_id
        except Exception as e:
            logger.error(f"Error placing sell order for {symbol}: {str(e)}")
            raise

    def place_market_order_buy(self, symbol, quantity):
        try:
            order_id = self.kite.place_order(
                tradingsymbol=symbol,
                exchange=self.kite.EXCHANGE_NFO,
                transaction_type=self.kite.TRANSACTION_TYPE_BUY,
                quantity=quantity,
                order_type=self.kite.ORDER_TYPE_MARKET,
                product=self.kite.PRODUCT_MIS,
                variety=self.kite.VARIETY_REGULAR
            )
            logger.info(f"Buy order placed for {symbol}: {order_id}")
            return order_id
        except Exception as e:
            logger.error(f"Error placing buy order for {symbol}: {str(e)}")
            raise

    def place_stoploss_order_buy(self, symbol, quantity, trigger_price):
        try:
            limit_price = self.round_to_tick_size(trigger_price + self.sl_slippage_buffer)

            order_id = self.kite.place_order(
                tradingsymbol=symbol,
                exchange=self.kite.EXCHANGE_NFO,
                transaction_type=self.kite.TRANSACTION_TYPE_BUY,
                quantity=quantity,
                order_type=self.kite.ORDER_TYPE_SL,
                product=self.kite.PRODUCT_MIS,
                variety=self.kite.VARIETY_REGULAR,
                trigger_price=trigger_price,
                price=limit_price
            )
            logger.info(f"Stop loss buy order placed for {symbol} - Trigger: {trigger_price}, Limit: {limit_price}, Order ID: {order_id}")
            return order_id
        except Exception as e:
            logger.error(f"Error placing stop loss order for {symbol}: {str(e)}")
            raise

    def get_trade_price(self, order_id, max_retries=10):
        for attempt in range(max_retries):
            try:
                trades = self.kite.trades()
                tb_df = pd.DataFrame(trades)
                trade_price = tb_df[tb_df.order_id == order_id].average_price.values[0]
                logger.info(f"Trade price for order {order_id}: {trade_price}")
                return trade_price
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1}: Can't extract trade data - {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    raise Exception(f"Unable to fetch trade price for order {order_id}")

    def get_order_status(self, order_id, max_retries=10):
        for attempt in range(max_retries):
            try:
                trades = self.kite.trades()
                tb_df = pd.DataFrame(trades)
                df_filtered = tb_df[tb_df.order_id == order_id]

                if len(df_filtered) > 0:
                    return 'executed'
                else:
                    return 'pending'
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1}: Can't extract order status - {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    return 'unknown'

    def cancel_order(self, order_id):
        try:
            self.kite.cancel_order(order_id=order_id, variety=self.kite.VARIETY_REGULAR)
            logger.info(f"Order {order_id} cancelled")
        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {str(e)}")

    def round_to_tick_size(self, price):
        r = round(price % 0.05, 2)
        rounded_price = round(price - r, 2)
        return float(rounded_price)

    def download_instrument_data(self):
        max_retries = 16
        for attempt in range(max_retries):
            try:
                instrument_dump = self.kite.instruments("NFO")
                logger.info(f"Successfully downloaded {len(instrument_dump)} instruments from NFO")
                return pd.DataFrame(instrument_dump)
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1}: Instrument dump download error - {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    raise Exception("Unable to download instrument data")

    def calculate_atm_and_place_order(self):
        try:
            self.bn_ltp = self.get_nifty_ltp()
            logger.info(f'NIFTY LTP: {self.bn_ltp}')

            self.atm_strike = self.get_nifty_atm_strike(self.bn_ltp)
            logger.info(f'ATM Strike: {self.atm_strike}')

            # Strangle: sell OTM call above ATM, OTM put below ATM
            ce_strike = self.atm_strike + self.strangle_width
            pe_strike = self.atm_strike - self.strangle_width
            logger.info(f'CE Strike (OTM): {ce_strike}, PE Strike (OTM): {pe_strike}')

            self.ce_symbol = self.get_trading_symbol(self.bn_exp_df, ce_strike, 'CE')
            self.pe_symbol = self.get_trading_symbol(self.bn_exp_df, pe_strike, 'PE')

            logger.info(f"CE Symbol: {self.ce_symbol}")
            logger.info(f"PE Symbol: {self.pe_symbol}")

            quantity = self.lots * self.lot_size
            logger.info(f"Placing sell orders for quantity: {quantity} (lots: {self.lots} x lot_size: {self.lot_size})")

            self.ce_order_id = self.place_market_order_sell(self.ce_symbol, quantity)
            self.pe_order_id = self.place_market_order_sell(self.pe_symbol, quantity)

            time.sleep(2)

            self.ce_sell_price = self.get_trade_price(self.ce_order_id)
            self.pe_sell_price = self.get_trade_price(self.pe_order_id)

            logger.info(f'CE sell price: {self.ce_sell_price}')
            logger.info(f'PE sell price: {self.pe_sell_price}')

            ce_stoploss_value = self.round_to_tick_size(self.ce_sell_price * self.ce_stoploss_per / 100)
            pe_stoploss_value = self.round_to_tick_size(self.pe_sell_price * self.pe_stoploss_per / 100)

            ce_trigger_price = self.round_to_tick_size(self.ce_sell_price + ce_stoploss_value)
            pe_trigger_price = self.round_to_tick_size(self.pe_sell_price + pe_stoploss_value)

            self.ce_sl_orderid = self.place_stoploss_order_buy(self.ce_symbol, quantity, ce_trigger_price)
            self.pe_sl_orderid = self.place_stoploss_order_buy(self.pe_symbol, quantity, pe_trigger_price)

            logger.info(f"Strangle setup completed - CE: {self.ce_sell_price}, PE: {self.pe_sell_price}")

        except Exception as e:
            logger.error(f"Error in calculate_atm_and_place_order: {str(e)}")
            raise

    def wait_until_time(self, target_time):
        logger.info(f"Waiting until {target_time}")
        while dt.datetime.now().time() < target_time:
            time.sleep(1)

    def execute_square_off_logic(self):
        logger.info("Executing square off logic")

        ce_sl_status = self.get_order_status(self.ce_sl_orderid)
        pe_sl_status = self.get_order_status(self.pe_sl_orderid)

        quantity = self.lots * self.lot_size

        if ce_sl_status == 'pending':
            self.cancel_order(self.ce_sl_orderid)
            self.place_market_order_buy(self.ce_symbol, quantity)

        if pe_sl_status == 'pending':
            self.cancel_order(self.pe_sl_orderid)
            self.place_market_order_buy(self.pe_symbol, quantity)

        logger.info("Square off completed")

    def run_trading_strategy(self):
        try:
            logger.info("Starting NIFTY 50 Short Strangle Strategy")

            instrument_df = self.download_instrument_data()

            nifty_options = instrument_df[instrument_df.name == 'NIFTY']
            logger.info(f"Found {len(nifty_options)} NIFTY instruments")

            target_expiry_date = self.get_expiry_date()
            logger.info(f"Target expiry date: {target_expiry_date}")

            self.expiry_date = self.find_nearest_expiry(nifty_options, target_expiry_date)
            logger.info(f"Using expiry date: {self.expiry_date}")

            self.bn_exp_df = nifty_options[nifty_options.expiry == self.expiry_date]
            logger.info(f"Found {len(self.bn_exp_df)} NIFTY instruments for expiry {self.expiry_date}")

            if len(self.bn_exp_df) == 0:
                available_expiries = sorted(nifty_options['expiry'].unique())
                logger.error(f"Available expiry dates: {available_expiries}")
                raise Exception("No instruments available for trading")

            self.wait_until_time(self.trade_entry_time)

            logger.info("Placing initial strangle orders")
            self.calculate_atm_and_place_order()

            if self.enable_sl_monitoring:
                logger.info("Starting continuous stop loss monitoring")
                monitor_thread = threading.Thread(target=self.monitor_stop_loss_continuously)
                monitor_thread.daemon = True
                monitor_thread.start()

            self.wait_until_time(self.re_entry_time)

            ce_sl_status = self.get_order_status(self.ce_sl_orderid)
            pe_sl_status = self.get_order_status(self.pe_sl_orderid)

            if ce_sl_status == 'executed' and pe_sl_status == 'executed':
                logger.info("Both stop losses hit - Re-entering strangle")
                self.calculate_atm_and_place_order()

            self.wait_until_time(self.sqf_time)

            self.execute_square_off_logic()

            logger.info("Trading strategy completed successfully")

        except Exception as e:
            logger.error(f"Error in trading strategy: {str(e)}")
            raise


# Main execution
if __name__ == "__main__":
    try:
        bot = NiftyStrangleBot()
        bot.run_trading_strategy()
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        print(f"Trading bot failed with error: {str(e)}")
