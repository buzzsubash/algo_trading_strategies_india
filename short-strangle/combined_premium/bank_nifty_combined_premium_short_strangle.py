from kiteconnect import KiteConnect
import datetime as dt
import time
import pandas as pd

# Your Zerodha Kite Connect API key
api_key = ''

# Access token read from a file
access_token_zer = open("/home/ubuntu/utilities/", 'r').read()

# Initialize the KiteConnect object
kite = KiteConnect(api_key=api_key)

# Set the access token for the session
kite.set_access_token(access_token_zer)

# Trade entry and square-off times
trade_entry_time = dt.time(hour=8, minute=45, second=10)
square_off_time = dt.time(hour=17, minute=59, second=50)

# Strangle width: how many points away from ATM to sell each leg.
# 0 = straddle (ATM), positive = strangle (OTM)
# BankNifty strikes are in 100-point intervals; 100 = 1 strike OTM
strangle_width = 100

# Number of lots to trade
lots = 60

# Combined premium stop loss in points
stop_loss = 30

# Trailing stop loss in points
tsl = 1

# Lot size for BankNifty
lot_size = 15

# NSE holidays
nse_holidays = [
    dt.date(2023, 1, 26), dt.date(2023, 3, 8), dt.date(2023, 3, 30),
    dt.date(2023, 4, 4), dt.date(2023, 4, 7), dt.date(2023, 4, 14),
    dt.date(2023, 4, 21), dt.date(2023, 6, 28), dt.date(2023, 8, 15),
    dt.date(2023, 9, 19), dt.date(2023, 10, 2), dt.date(2023, 10, 24),
    dt.date(2023, 11, 14), dt.date(2023, 11, 27), dt.date(2023, 12, 25)
]


def get_expiry_date():
    current_date = dt.date.today()
    wd = current_date.weekday()
    if wd < 3:
        exp_date = current_date + dt.timedelta(days=2 - wd)
    elif wd == 3:
        exp_date = current_date - dt.timedelta(days=1)
    else:
        exp_date = current_date + dt.timedelta(days=9 - wd)

    while exp_date in nse_holidays:
        exp_date -= dt.timedelta(days=1)

    return exp_date


def get_nifty_ltp():
    nt_ltp = None
    a = 0
    while a < 10:
        try:
            nt = kite.ltp('NSE:NIFTY BANK')
            nt_ltp = nt['NSE:NIFTY BANK']['last_price']
            break
        except Exception as e:
            print(f"Attempt {a + 1}: Failed to retrieve NIFTY BANK LTP - {e}")
            time.sleep(1)
            a += 1
    if nt_ltp is None:
        raise ValueError("Failed to retrieve NIFTY BANK LTP after multiple attempts.")
    return nt_ltp


def get_nifty_atm_strike(nifty_ltp):
    r = nifty_ltp % 100
    if r < 50:
        atm = nifty_ltp - r
    else:
        atm = nifty_ltp - r + 100
    return int(atm)


def get_trading_symbol(df, strike, CE_or_PE):
    df_1 = df[df.strike == strike]
    ce_name = df_1[df_1.instrument_type == 'CE'].tradingsymbol.values[0]
    pe_name = df_1[df_1.instrument_type == 'PE'].tradingsymbol.values[0]
    if CE_or_PE == 'CE':
        symbol = ce_name
    elif CE_or_PE == 'PE':
        symbol = pe_name
    return symbol


def get_ce_and_pe_ltp(ce_symbol, pe_symbol):
    ce = 'NFO:' + ce_symbol
    pe = 'NFO:' + pe_symbol
    a = 0
    while a < 25:
        try:
            option = kite.ltp(ce, pe)
            ce_ltp = option[ce]['last_price']
            pe_ltp = option[pe]['last_price']
            break
        except:
            time.sleep(1)
            a += 1
    return ce_ltp, pe_ltp


def marketorder_buy(symbol, quantity):
    return kite.place_order(
        tradingsymbol=symbol,
        exchange=kite.EXCHANGE_NFO,
        transaction_type=kite.TRANSACTION_TYPE_BUY,
        quantity=quantity,
        order_type=kite.ORDER_TYPE_MARKET,
        product=kite.PRODUCT_NRML,
        variety=kite.VARIETY_REGULAR
    )


def marketorder_sell(symbol, quantity):
    return kite.place_order(
        tradingsymbol=symbol,
        exchange=kite.EXCHANGE_NFO,
        transaction_type=kite.TRANSACTION_TYPE_SELL,
        quantity=quantity,
        order_type=kite.ORDER_TYPE_MARKET,
        product=kite.PRODUCT_NRML,
        variety=kite.VARIETY_REGULAR
    )


def get_order_status_price_qty(order_id):
    status = ''
    avg = 0.0
    f_qty = 0
    p_qty = 0

    order_id = str(order_id)

    a = 0
    while a <= 10:
        try:
            ord_df = pd.DataFrame(kite.orders())
            break
        except:
            print("can't extract ORDER BOOK data..retrying")
            time.sleep(2)
            a += 1

    if len(ord_df) > 0:
        df = ord_df[ord_df.order_id == order_id]
        if len(df) > 0:
            status = df['status'].iloc[-1]
            avg = float(df['average_price'].iloc[-1])
            f_qty = int(df['filled_quantity'].iloc[-1])
            p_qty = int(df['pending_quantity'].iloc[-1])

    return status, avg, f_qty, p_qty


def cancel_order(order_id):
    try:
        kite.cancel_order(order_id=order_id, variety=kite.VARIETY_REGULAR)
    except:
        print('order cancellation error')


##########################################################################

qty = lots * lot_size

# Download instrument dump
a = 0
while a <= 15:
    try:
        instrument_dump = kite.instruments("NFO")
        break
    except:
        print('instrument dump download error..Retrying')
        a = a + 1
        time.sleep(1)

instrument_df = pd.DataFrame(instrument_dump)

nifty = instrument_df[instrument_df.name == 'BANKNIFTY']

expiry_date = get_expiry_date()

nifty_exp_df = nifty[nifty.expiry == expiry_date]

# Wait until trade entry time
while dt.datetime.now().time() < trade_entry_time:
    time.sleep(1)

# Get LTP and ATM strike
nt_ltp = get_nifty_ltp()
print('Bank Nifty : ', nt_ltp)

atm = get_nifty_atm_strike(nt_ltp)
print('ATM Strike: ', atm)

# Strangle: sell OTM call above ATM, OTM put below ATM
ce_symbol = get_trading_symbol(nifty_exp_df, atm + strangle_width, 'CE')
pe_symbol = get_trading_symbol(nifty_exp_df, atm - strangle_width, 'PE')

print('ce symbol is ', ce_symbol)
print('pe symbol is ', pe_symbol)

# Place sell orders
ce_order_id = marketorder_sell(ce_symbol, qty)
pe_order_id = marketorder_sell(pe_symbol, qty)
time.sleep(3)

ce_status, ce_sell_price, ce_f_qty, ce_p_qty = get_order_status_price_qty(ce_order_id)
pe_status, pe_sell_price, pe_f_qty, pe_p_qty = get_order_status_price_qty(pe_order_id)

if ce_status == 'COMPLETE' and pe_status == 'COMPLETE':
    print('ce sell price is ', round(ce_sell_price, 2))
    print('pe sell price is ', round(pe_sell_price, 2))

    sell_premium = ce_sell_price + pe_sell_price
    print('combined premium ', round(sell_premium, 2))

    premium_base = sell_premium
    sl_premium = sell_premium + stop_loss

    while True:
        ce_ltp, pe_ltp = get_ce_and_pe_ltp(ce_symbol, pe_symbol)
        current_premium = ce_ltp + pe_ltp

        print('===============')
        print('Combined premium ', round(sell_premium, 2))
        print('Stop loss ', round(sl_premium, 2))
        print('LTP  ', round((current_premium), 2))
        print('Gain ', round((sell_premium - current_premium), 2))
        print('MTM ', round((sell_premium - current_premium) * lot_size * lots), 2)

        if current_premium < premium_base - tsl:
            premium_decrease = premium_base - current_premium
            premium_base = current_premium
            sl_premium = current_premium + stop_loss
            print(f"TSL Adjusted: New SL Premium {sl_premium}, Profit Locked {premium_decrease}")

        if current_premium >= sl_premium:
            print("Exiting positions due to stop loss trigger.")
            marketorder_buy(ce_symbol, qty)
            marketorder_buy(pe_symbol, qty)
            break

        if dt.datetime.now().time() >= square_off_time:
            print("Market close time reached. Exiting positions.")
            marketorder_buy(ce_symbol, qty)
            marketorder_buy(pe_symbol, qty)
            break

        time.sleep(1)
else:
    if ce_status != 'COMPLETE':
        print('CE sell order status is ', ce_status)
        if pe_status == "COMPLETE" and int(pe_f_qty) > 0:
            print('PE filled quantity buying back to close the position...')
            marketorder_buy(pe_symbol, int(pe_f_qty))

    if pe_status != 'COMPLETE':
        print('PE sell order status is ', pe_status)
        if ce_status == "COMPLETE" and int(ce_f_qty) > 0:
            print('CE filled quantity buying back to close the position...')
            marketorder_buy(ce_symbol, int(ce_f_qty))

print('End of Program')
