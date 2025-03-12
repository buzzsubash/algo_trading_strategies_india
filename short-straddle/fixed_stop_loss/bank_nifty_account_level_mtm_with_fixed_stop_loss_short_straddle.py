#accout level sl, re attemt on exe range cancellation, telegram integration
#modified on 23/08/2021 17:15 IST
from kiteconnect import KiteConnect
import pandas as pd
import datetime as dt
import time
import requests


lots=10  # no of lots
ce_stoploss_value=50
pe_stoploss_value=60
max_loss_per_lot=1900
u_exit='no'

nse_holidays=[dt.date(2021,7,21),dt.date(2021,8,19),dt.date(2021,9,10),dt.date(2021,10,15),dt.date(2021,11,4),dt.date(2021,11,5),dt.date(2021,11,19)]


api_key = ""
api_secret = ""

access_token=open('/home/ec2-user/keyfiles/','r').read()

open_time=dt.time(hour=9,minute=15)
trade_entry_time=dt.time(hour=9,minute=59)
re_entry_time=dt.time(hour=12,minute=30)
sqf_time=dt.time(hour=14,minute=57)


kite = KiteConnect(api_key=api_key)

kite.set_access_token(access_token)

pe_df=pd.DataFrame(columns=['sl_order_id','qty','sl_amount','sl_triggered','pnl']) 
ce_df=pd.DataFrame(columns=['sl_order_id','qty','sl_amount','sl_triggered','pnl']) 

def telegram_bot_sendtext(bot_message):
        bot_token = ''
        bot_chatID = ''
        send_text = 'https://api.telegram.org/bot' + bot_token + \
                    '/sendMessage?chat_id=' + bot_chatID + '&parse_mode=Markdown&text=' + bot_message

        print(send_text)
        response = requests.get(send_text)

        return response.json()


def get_expiry_date():
    #this module will not work in sunday or saturday
    current_date=dt.date.today()
    wd=current_date.weekday()
    #print(wd)   # 0 Monday, 6 sunday
    #this module will not work on sunday and saturday
    #calculating value of x ('current date' + 'x' will be next weekly exp day)
    x=0
    if wd<=3:
        x=(3-wd)
    else:
        x=6
    
    exp_date=current_date+dt.timedelta(days=x)
   
    if exp_date in nse_holidays:
        exp_date=exp_date-dt.timedelta(days=1)
        
    if exp_date in nse_holidays:
        exp_date=exp_date-dt.timedelta(days=1)
    return exp_date

def get_banknifty_atm_strike(ltp):
    r=ltp%100
    if r<50:
       atm=ltp-r
    else:
        atm=ltp-r+100
    return int(atm)

def get_trading_symbol(df,strike,CE_or_PE):
    
    df_1=df[df.strike==strike]
    ce_name=df_1[df_1.instrument_type=='CE'].tradingsymbol.values[0]
    pe_name=df_1[df_1.instrument_type=='PE'].tradingsymbol.values[0]
    if CE_or_PE=='CE':
        symbol=ce_name
    elif CE_or_PE=='PE':
        symbol=pe_name
    return symbol
    
def get_banknifty_ltp():
 
    a = 0
    while a < 30:
        try:
            bn=kite.ltp('NSE:NIFTY BANK')
            bn_ltp=bn['NSE:NIFTY BANK']['last_price']
            break
        except:
            print("can't extract LTP data..retrying")
            time.sleep(2)
            a+=1
            try:
                telegram_bot_sendtext('50-60 kiteconnect api error unable to fetch ltp of banknifty, retrying....')
            except:
                pass

    return bn_ltp


def marketorder_buy(symbol,quantity):    
    return kite.place_order(tradingsymbol=symbol,
                    exchange=kite.EXCHANGE_NFO,
                    transaction_type=kite.TRANSACTION_TYPE_BUY,
                    quantity=quantity,
                    order_type=kite.ORDER_TYPE_MARKET,
                    product=kite.PRODUCT_MIS,
                    variety=kite.VARIETY_REGULAR)

def marketorder_sell(symbol,quantity):    
    return kite.place_order(tradingsymbol=symbol,
                    exchange=kite.EXCHANGE_NFO,
                    transaction_type=kite.TRANSACTION_TYPE_SELL,
                    quantity=quantity,
                    order_type=kite.ORDER_TYPE_MARKET,
                    product=kite.PRODUCT_MIS,
                    variety=kite.VARIETY_REGULAR)

def stoploss_order_buy(symbol,quantity,trig_price):    
    return kite.place_order(tradingsymbol=symbol,
                    exchange=kite.EXCHANGE_NFO,
                    transaction_type=kite.TRANSACTION_TYPE_BUY,
                    quantity=quantity,
                    order_type=kite.ORDER_TYPE_SL,
                    product=kite.PRODUCT_MIS,
                    variety=kite.VARIETY_REGULAR,
                    trigger_price=trig_price,
                    price=trig_price+50,)


def get_trade_price(order_id):
    a=0
    while a < 10:
        try:
            tb_df=pd.DataFrame(kite.trades())
            trade_price=tb_df[tb_df.order_id==order_id].average_price.values[0]
            break
        except:
            print("can't extract oreder data..retrying")
            time.sleep(1)
            a+=1
            try:
                telegram_bot_sendtext('50-60 kiteconnect api error, unable to open tradebook get_trade_price module, retrying....')
            except:
                pass

    return trade_price

def get_trade_quantity(order_id):
    a=0
    qty=0
    while a < 10:
        try:
            tb_df=pd.DataFrame(kite.trades())
            if len(tb_df)>0:
                df=tb_df[tb_df.order_id==order_id]
                if len(df)>0:
                    qty=df['quantity'].sum()
            break
        except:
            print("can't extract oreder data..retrying")
            time.sleep(1)
            a+=1
            try:
                telegram_bot_sendtext('50-60 kiteconnect api error, unable to open tradebook get_trade_quantity module, retrying....')
            except:
                pass

    return qty




def get_order_status(order_id):
    a=0
    while a < 30:
        try:
            tb_df=pd.DataFrame(kite.trades())
            break
        except:
            print("can't extract oreder data..retrying")
            time.sleep(1)
            a+=1
            try:
                telegram_bot_sendtext('50-60 kiteconnect api error, unable to open tradebook get_order_status module, retrying....')
            except:
                pass


    df=tb_df[tb_df.order_id==order_id]
    
    if len(df)>0:
        return 'executed'
    else:
        return 'pending'

def round_5ps(price):
        r = round(price % .05, 2)
        rp = round(price - r, 2)
        return float(rp)

def cancel_order(order_id):    
    kite.cancel_order(order_id=order_id,
                    variety=kite.VARIETY_REGULAR)
def get_fo_ltp(symbol):
    symbol='NFO:'+symbol
    a = 0
    while a < 30:
        try:
            option=kite.ltp(symbol)
            option_ltp=option[symbol]['last_price']
            break
        except:
            print("can't extract LTP data..retrying")
            time.sleep(2)
            a+=1
            try:
                telegram_bot_sendtext('50-60 kiteconnect api error, unable to fetch FO ltp get_fo_ltp module, retrying....')
            except:
                pass

    return option_ltp

def universal_exit():
    global u_exit
    u_exit='yes'
    ce_sl_status=get_order_status(ce_sl_orderid)
    pe_sl_status=get_order_status(pe_sl_orderid)
    
    if ce_sl_status=='pending':
        cancel_order(ce_sl_orderid)
        marketorder_buy(ce_symbol,lots*25)
        
    if pe_sl_status=='pending':
        cancel_order(pe_sl_orderid)
        marketorder_buy(pe_symbol,lots*25)
    try:
        telegram_bot_sendtext('50-60 univresal exit hit')
    except:
        pass


def calculate_atm_and_place_order():
    global bn_ltp, atm_strike, ce_symbol, pe_symbol, ce_order_id, pe_order_id
    global ce_sell_price, pe_sell_price, ce_sl_orderid, pe_sl_orderid
#######################################################################
     
    bn_ltp=get_banknifty_ltp()
    
    atm_strike=get_banknifty_atm_strike(bn_ltp)
    
    ce_symbol=get_trading_symbol(bn_exp_df, atm_strike,'CE')
    
    pe_symbol=get_trading_symbol(bn_exp_df, atm_strike,'PE')
    print(ce_symbol)
    print(pe_symbol)
    
    ce_order_id=marketorder_sell(ce_symbol, lots*25)
    
    pe_order_id=marketorder_sell(pe_symbol, lots*25)
    
    ce_sell_price=get_trade_price(ce_order_id)
    pe_sell_price=get_trade_price(pe_order_id)
    
    ce_sl_orderid=stoploss_order_buy(ce_symbol, lots*25,float(round_5ps(ce_sell_price+ce_stoploss_value)))
    pe_sl_orderid=stoploss_order_buy(pe_symbol, lots*25,float(round_5ps(pe_sell_price+pe_stoploss_value)))
    
    ce_df.loc[0,'sl_order_id']=ce_sl_orderid
    ce_df.loc[0,'qty']=lots*25
    ce_df.loc[0,'sl_amount']=float(round_5ps(ce_sell_price+ce_stoploss_value))
    ce_df.loc[0,'sl_triggered']='no'
    ce_df.loc[0,'pnl']=0
    
    pe_df.loc[0,'sl_order_id']=pe_sl_orderid
    pe_df.loc[0,'qty']=lots*25
    pe_df.loc[0,'sl_amount']=float(round_5ps(pe_sell_price+pe_stoploss_value))
    pe_df.loc[0,'sl_triggered']='no'
    pe_df.loc[0,'pnl']=0

def calculate_pnl_and_take_action():
    ce_sl_status=get_order_status(ce_sl_orderid)
    pe_sl_status=get_order_status(pe_sl_orderid)
    
    if ce_sl_status=='executed':
        ce_df.loc[0,'sl_triggered']='yes'
        ce_buy_price=get_trade_price(ce_sl_orderid)
        ce_df.loc[0,'pnl']=(ce_sell_price-ce_buy_price)*lots*25
    elif pe_sl_status=='executed':
        pe_df.loc[0,'sl_triggered']='yes'
        pe_buy_price=get_trade_price(pe_sl_orderid)
        pe_df.loc[0,'pnl']=(pe_sell_price-pe_buy_price)*lots*25
    if  ce_sl_status=='pending':
        ce_ltp=get_fo_ltp(ce_symbol)
        ce_df.loc[0,'pnl']=(ce_sell_price-ce_ltp)*lots*25
    if  pe_sl_status=='pending':
        pe_ltp=get_fo_ltp(pe_symbol)
        pe_df.loc[0,'pnl']=(pe_sell_price-pe_ltp)*lots*25
    pnl=(ce_df.loc[0,'pnl']+pe_df.loc[0,'pnl'])
    print('pnl:  ',pnl)
    if pnl<=-(max_loss_per_lot*lots):
        universal_exit()
        
    time.sleep(2)
def get_cancelled_qty(order_id):
    # this funcion is using for range execution error.   
    a = 0
    while a < 60:
        try:
            order_history_df=pd.DataFrame(kite.order_history(order_id))
            break
        except:
            print("can't extract cancelled Order history..retrying")
            time.sleep(2)
            a+=1
    cancelled_df=order_history_df[order_history_df.status=='CANCELLED']
    if len(cancelled_df)>0:
        trade_qty=get_trade_quantity(order_id)
        qty_can=(lots*25)-trade_qty
        """
        msg_status=str(cancelled_df['status_message'].iloc[-1])
        try:
            telegram_bot_sendtext('stoploss order cancelled')
            telegram_bot_sendtext(msg_status)
        except:
            pass
        """
    else:
        qty_can=0
    return qty_can
#######################################################



#downloading instrument dump 
a=0
while a<=30:
    try:
        instrument_dump = kite.instruments("NFO")
        break
    except:
        print("can't instrument data..retrying")
        time.sleep(3)
        a+=1

instrument_df = pd.DataFrame(instrument_dump)

expiry_date=get_expiry_date()

bn=instrument_df[instrument_df.name=='BANKNIFTY']
bn_exp_df=bn[bn.expiry==expiry_date]

while dt.datetime.now().time()<trade_entry_time:
    time.sleep(1)
###############################

calculate_atm_and_place_order()

###############################
time.sleep(2)

while dt.datetime.now().time()<re_entry_time:
    if u_exit=='no':
        cancelled_qty=get_cancelled_qty(ce_sl_orderid)
        if cancelled_qty>0:
            ce_sl_orderid=marketorder_buy(ce_symbol, cancelled_qty)
            ce_df.loc[0,'sl_order_id']=ce_sl_orderid
        cancelled_qty=get_cancelled_qty(pe_sl_orderid)
        if cancelled_qty>0:
            pe_sl_orderid=marketorder_buy(pe_symbol, cancelled_qty)
            pe_df.loc[0,'sl_order_id']=pe_sl_orderid
        calculate_pnl_and_take_action()
    time.sleep(2)
#second entry
u_exit='no' # clearing variable for second entry
ce_sl_status=get_order_status(ce_sl_orderid)
pe_sl_status=get_order_status(pe_sl_orderid)

if ce_sl_status=='executed' and pe_sl_status=='executed':
    pe_df=pd.DataFrame(columns=['sl_order_id','qty','sl_amount','sl_triggered','pnl']) 
    ce_df=pd.DataFrame(columns=['sl_order_id','qty','sl_amount','sl_triggered','pnl']) 

    calculate_atm_and_place_order()
    time.sleep(2)

while dt.datetime.now().time()<sqf_time:
    if u_exit=='no':
        cancelled_qty=get_cancelled_qty(ce_sl_orderid)
        if cancelled_qty>0:
            ce_sl_orderid=marketorder_buy(ce_symbol, cancelled_qty)
            ce_df.loc[0,'sl_order_id']=ce_sl_orderid
        cancelled_qty=get_cancelled_qty(pe_sl_orderid)
        if cancelled_qty>0:
            pe_sl_orderid=marketorder_buy(pe_symbol, cancelled_qty)
            pe_df.loc[0,'sl_order_id']=pe_sl_orderid
        calculate_pnl_and_take_action()
    time.sleep(2)

universal_exit()
    
