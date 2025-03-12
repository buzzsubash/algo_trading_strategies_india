from kiteconnect import KiteConnect
import pandas as pd
import datetime as dt
import time

lots = 10  # quanity
ce_stoploss_per = 16.67
pe_stoploss_per = 20

nse_holidays = [dt.date(2021, 7, 21), dt.date(2021, 8, 19), dt.date(2021, 9, 10), dt.date(2021, 10, 15), dt.date(2021, 11, 4), dt.date(2021, 11, 5), dt.date(2021, 11, 19)]

api_key = ""
api_secret = ""

access_token = open('/home/ec2-user/access_token.txt', 'r').read()

open_time = dt.time(hour=9, minute=15)
trade_entry_time = dt.time(hour=9, minute=59)
re_entry_time = dt.time(hour=12, minute=30)
sqf_time = dt.time(hour=15, minute=6)

kite = KiteConnect(api_key=api_key)

kite.set_access_token(access_token)
nse_holidays = [dt.date(2021, 7, 21), dt.date(2021, 8, 19), dt.date(2021, 9, 10), dt.date(2021, 10, 15), dt.date(2021, 11, 4), dt.date(2021, 11, 5), dt.date(2021, 11, 19)]


def get_expiry_date():
    # this module will not work in sunday or saturday
    current_date = dt.date.today()
    wd = current_date.weekday()
    # print(wd)   # 0 Monday, 6 sunday
    # this module will not work on sunday and saturday
    # calculating value of x ('current date' + 'x' will be next weekly exp day)
    x = 0
    if wd <= 3:
        x = (3 - wd)
    else:
        x = 6

    exp_date = current_date + dt.timedelta(days=x)

    if exp_date in nse_holidays:
        exp_date = exp_date - dt.timedelta(days=1)

    if exp_date in nse_holidays:
        exp_date = exp_date - dt.timedelta(days=1)
    return exp_date


def get_banknifty_atm_strike(ltp):
    r = ltp % 100
    if r < 50:
        atm = ltp - r
    else:
        atm = ltp - r + 100
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


def get_banknifty_ltp():
    a = 0
    while a < 10:
        try:
            bn = kite.ltp('NSE:NIFTY BANK')
            bn_ltp = bn['NSE:NIFTY BANK']['last_price']
            break
        except:
            print("can't extract LTP data..retrying")
            time.sleep(1)
            a += 1
    return bn_ltp


def marketorder_buy(symbol, quantity):
    return kite.place_order(tradingsymbol=symbol,
                            exchange=kite.EXCHANGE_NFO,
                            transaction_type=kite.TRANSACTION_TYPE_BUY,
                            quantity=quantity,
                            order_type=kite.ORDER_TYPE_MARKET,
                            product=kite.PRODUCT_MIS,
                            variety=kite.VARIETY_REGULAR)


def marketorder_sell(symbol, quantity):
    return kite.place_order(tradingsymbol=symbol,
                            exchange=kite.EXCHANGE_NFO,
                            transaction_type=kite.TRANSACTION_TYPE_SELL,
                            quantity=quantity,
                            order_type=kite.ORDER_TYPE_MARKET,
                            product=kite.PRODUCT_MIS,
                            variety=kite.VARIETY_REGULAR)


def stoploss_order_buy(symbol, quantity, trig_price):
    return kite.place_order(tradingsymbol=symbol,
                            exchange=kite.EXCHANGE_NFO,
                            transaction_type=kite.TRANSACTION_TYPE_BUY,
                            quantity=quantity,
                            order_type=kite.ORDER_TYPE_SLM,
                            product=kite.PRODUCT_MIS,
                            variety=kite.VARIETY_REGULAR,
                            trigger_price=trig_price)


def get_trade_price(order_id):
    a = 0
    while a < 10:
        try:
            tb_df = pd.DataFrame(kite.trades())
            trade_price = tb_df[tb_df.order_id == order_id].average_price.values[0]
            break
        except:
            print("can't extract oreder data..retrying")
            time.sleep(1)
            a += 1
    return trade_price


def get_order_status(order_id):
    a = 0
    while a < 10:
        try:
            tb_df = pd.DataFrame(kite.trades())
            break
        except:
            print("can't extract oreder data..retrying")
            time.sleep(1)
            a += 1

    df = tb_df[tb_df.order_id == order_id]

    if len(df) > 0:
        return 'executed'
    else:
        return 'pending'


def cancel_order(order_id):
    kite.cancel_order(order_id=order_id,
                      variety=kite.VARIETY_REGULAR)


def round_5ps(price):
    r = round(price % .05, 2)
    rp = round(price - r, 2)
    return float(rp)


def calculate_atm_and_place_order():
    global bn_ltp, atm_strike, ce_symbol, pe_symbol, ce_order_id, pe_order_id
    global ce_sell_price, pe_sell_price, ce_sl_orderid, pe_sl_orderid
    #######################################################################

    bn_ltp = get_banknifty_ltp()

    atm_strike = get_banknifty_atm_strike(bn_ltp)

    ce_symbol = get_trading_symbol(bn_exp_df, atm_strike, 'CE')

    pe_symbol = get_trading_symbol(bn_exp_df, atm_strike, 'PE')
    print(ce_symbol)
    print(pe_symbol)

    ce_order_id = marketorder_sell(ce_symbol, lots * 25)

    pe_order_id = marketorder_sell(pe_symbol, lots * 25)

    ce_sell_price = get_trade_price(ce_order_id)
    pe_sell_price = get_trade_price(pe_order_id)

    ce_stoploss_value = round_5ps(ce_sell_price * ce_stoploss_per / 100)
    pe_stoploss_value = round_5ps(pe_sell_price * pe_stoploss_per / 100)

    ce_sl_orderid = stoploss_order_buy(ce_symbol, lots * 25, float(round_5ps(ce_sell_price + ce_stoploss_value)))
    pe_sl_orderid = stoploss_order_buy(pe_symbol, lots * 25, float(round_5ps(pe_sell_price + pe_stoploss_value)))


#######################################################


# downloading instrument dump
a = 0
while a <= 10:
    try:
        instrument_dump = kite.instruments("NFO")
        break
    except:
        print("can't instrument data..retrying")
        time.sleep(1)
        a += 1

instrument_df = pd.DataFrame(instrument_dump)

expiry_date = get_expiry_date()

bn = instrument_df[instrument_df.name == 'BANKNIFTY']
bn_exp_df = bn[bn.expiry == expiry_date]

while dt.datetime.now().time() < trade_entry_time:
    time.sleep(1)
###############################

calculate_atm_and_place_order()

###############################

while dt.datetime.now().time() < re_entry_time:
    time.sleep(1)

ce_sl_status = get_order_status(ce_sl_orderid)
pe_sl_status = get_order_status(pe_sl_orderid)

if ce_sl_status == 'executed' and pe_sl_status == 'executed':
    calculate_atm_and_place_order()

while dt.datetime.now().time() < sqf_time:
    time.sleep(1)

ce_sl_status = get_order_status(ce_sl_orderid)
pe_sl_status = get_order_status(pe_sl_orderid)

if ce_sl_status == 'pending':
    cancel_order(ce_sl_orderid)
    marketorder_buy(ce_symbol, lots * 25)

if pe_sl_status == 'pending':
    cancel_order(pe_sl_orderid)
    marketorder_buy(pe_symbol, lots * 25)

















