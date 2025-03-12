from kiteconnect import KiteConnect
import datetime as dt
import time
import pandas as pd

global ce_symbol,pe_symbol
api_key = ''
access_token_zer = open("/home/ubuntu/utilities/", 'r').read()

kite = KiteConnect(api_key=api_key)
kite.set_access_token(access_token_zer)

trade_entry_time=dt.time(hour=11,minute=45,second=2)
square_off_time=dt.time(hour=17,minute=59,second=50)

lot_size=25
strike_points=0  # 0(atm),  100(itm 100)...etc
quantity=20  # in lots
max_loss=5000 # per lot give only positive values
target_profit=20000 # per lot

limit_entry=10 # limit order limit pirce


nse_holidays=[dt.date(2022,1,26),dt.date(2022,3,1),dt.date(2022,3,18),
              dt.date(2022,4,14),dt.date(2022,4,15),dt.date(2022,5,3),
              dt.date(2022,8,9),dt.date(2022,8,15),dt.date(2022,8,31),
              dt.date(2022,10,5),dt.date(2022,10,24),dt.date(2022,10,26),
              dt.date(2022,11,8)]


def get_expiry_date():
    #this module will not work in sunday or saturday
    current_date=dt.date.today()
    wd=current_date.weekday()
    
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



a=0
while a<=30:
    try:
        instrument_dump = kite.instruments("NFO")
        break
    except:
        time.sleep(1)
        a+=1

instrument_df = pd.DataFrame(instrument_dump)

expiry_date=get_expiry_date()


bn=instrument_df[instrument_df.name=='BANKNIFTY']
bn_exp_df=bn[bn.expiry==expiry_date]
bn_exp_df.reset_index(inplace=True)

qty=quantity*lot_size

def cancel_order(order_id):    
    try:
        kite.cancel_order(order_id=order_id,
                    variety=kite.VARIETY_REGULAR)
    except:
        print('order cancellatin error')
def get_banknifty_ltp():
    a = 0
    while a < 10:
        try:
            bn=kite.ltp('NSE:NIFTY BANK')
            bn_ltp=bn['NSE:NIFTY BANK']['last_price']
            break
        except:
            print("can't extract LTP data..retrying")
            time.sleep(1)
            a+=1
    return bn_ltp


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

def get_ce_and_pe_ltp(ce_symbol,pe_symbol):
    ce='NFO:'+ce_symbol
    pe='NFO:'+pe_symbol
    a = 0
    while a < 50:
        try:
            option=kite.ltp(ce,pe)
            ce_ltp=option[ce]['last_price']
            pe_ltp=option[pe]['last_price']
            break
        except:
            time.sleep(1)
            a+=1
    return ce_ltp,pe_ltp



def limit_order_buy(symbol,quantity,price):    
    return kite.place_order(tradingsymbol=symbol,
                    exchange=kite.EXCHANGE_NFO,
                    transaction_type=kite.TRANSACTION_TYPE_BUY,
                    quantity=quantity,
                    order_type=kite.ORDER_TYPE_LIMIT,
                    product=kite.PRODUCT_NRML,
                    variety=kite.VARIETY_REGULAR,
                    price=price)
def limit_order_sell(symbol,quantity,price):    
    return kite.place_order(tradingsymbol=symbol,
                    exchange=kite.EXCHANGE_NFO,
                    transaction_type=kite.TRANSACTION_TYPE_SELL,
                    quantity=quantity,
                    order_type=kite.ORDER_TYPE_LIMIT,
                    product=kite.PRODUCT_NRML,
                    variety=kite.VARIETY_REGULAR,
                    price=price)

def get_trade_price(order_id):
    #order_id='220627001362408'
    a=0
    while a<=10:
        try:
            ord_df=pd.DataFrame(kite.orders())
            break
        except:
            print("can't extract ORDER BOOK data..retrying")
            time.sleep(1)
            a=a+1
    if len(ord_df)>0:
        df=ord_df[ord_df.order_id==order_id]
        if len(df)>0:
            avg=float(df['average_price'].iloc[-1])
    return avg
def get_order_status_price_qty(order_id):
    status=''
    avg=0
    f_qty=0
    p_qty=0
    order_id=str(order_id)
    a=0
    while a<=10:
        try:
            ord_df=pd.DataFrame(kite.orders())
            break
        except:
            print("can't extract ORDER BOOK data..retrying")
            time.sleep(2)
            a=a+1
    if len(ord_df)>0:
        df=ord_df[ord_df.order_id==order_id]
        if len(df)>0:
            status=df['status'].iloc[-1]
            avg=float(df['average_price'].iloc[-1])
            f_qty=int(df['filled_quantity'].iloc[-1])
            p_qty=int(df['pending_quantity'].iloc[-1])
    return status,avg,f_qty,p_qty
def modify_limit_order(order_id,price):    
    try:
        kite.modify_order(order_id=order_id,
                        price=price,
                        order_type=kite.ORDER_TYPE_LIMIT,
                        variety=kite.VARIETY_REGULAR)      
    except:
        print('order modification error..')
        pass
    
def market_order_buy(symbol,quantity):    
    return kite.place_order(tradingsymbol=symbol,
                    exchange=kite.EXCHANGE_NFO,
                    transaction_type=kite.TRANSACTION_TYPE_BUY,
                    quantity=quantity,
                    order_type=kite.ORDER_TYPE_MARKET,
                    product=kite.PRODUCT_NRML,
                    variety=kite.VARIETY_REGULAR)

def exit_entry():
    ce_ltp,pe_ltp=get_ce_and_pe_ltp(ce_symbol, pe_symbol)
    ce_order_id=limit_order_buy(ce_symbol, qty, ce_ltp+limit_entry)
    pe_order_id=limit_order_buy(pe_symbol, qty, pe_ltp+limit_entry)
   

    time.sleep(1)
    a=0
    while a<5:
        ce_status,ce_avg,ce_f_qty,ce_p_qty=get_order_status_price_qty(ce_order_id)
        if ce_f_qty<qty:
            ce_ltp,pe_ltp=get_ce_and_pe_ltp(ce_symbol, pe_symbol)
            modify_limit_order(ce_order_id, ce_ltp+limit_entry)
            a=a+1
            time.sleep(1)
        else:
            break 
              
    
    a=0
    while a<5:
        pe_status,pe_avg,pe_f_qty,pe_p_qty=get_order_status_price_qty(pe_order_id)
        if pe_f_qty<qty:
            ce_ltp,pe_ltp=get_ce_and_pe_ltp(ce_symbol, pe_symbol)
            modify_limit_order(pe_order_id, pe_ltp+limit_entry)
            a=a+1
            time.sleep(1)
        else:
            break        
    ce_status,ce_avg,ce_f_qty,ce_p_qty=get_order_status_price_qty(ce_order_id)
    pe_status,pe_avg,pe_f_qty,pe_p_qty=get_order_status_price_qty(pe_order_id)
 
    
    if ((ce_f_qty!=qty) or (pe_f_qty!=qty)):
        print('partial execution of order')
        # include code to cancel order
        
        if ce_p_qty>0:
            cancel_order(ce_order_id)
            market_order_buy(ce_symbol, ce_p_qty)
        if pe_p_qty>0:
            cancel_order(pe_order_id)
            market_order_buy(pe_symbol, pe_p_qty)
   


def short_entry():
    global ce_symbol,pe_symbol
    entry_status=True
    bn_ltp=get_banknifty_ltp()
    print('bank nifty ltp is ',bn_ltp)
    bn_atm=get_banknifty_atm_strike(bn_ltp)
    print('atm strike is ',bn_atm)
    ce_symbol=get_trading_symbol(bn_exp_df, bn_atm-strike_points, 'CE')
    pe_symbol=get_trading_symbol(bn_exp_df, bn_atm+strike_points, 'PE')
    ce_ltp,pe_ltp=get_ce_and_pe_ltp(ce_symbol, pe_symbol)
    print(ce_ltp,pe_ltp)
    ce_order_id=limit_order_sell(ce_symbol, qty, ce_ltp-limit_entry)
    pe_order_id=limit_order_sell(pe_symbol, qty, pe_ltp-limit_entry)
   

    time.sleep(1)
    a=0
    while a<5:
        ce_status,ce_avg,ce_f_qty,ce_p_qty=get_order_status_price_qty(ce_order_id)
        if ce_f_qty<qty:
            ce_ltp,pe_ltp=get_ce_and_pe_ltp(ce_symbol, pe_symbol)
            modify_limit_order(ce_order_id, ce_ltp-limit_entry)
            a=a+1
            time.sleep(5)
        else:
            break            
    
    a=0
    while a<5:
        pe_status,pe_avg,pe_f_qty,pe_p_qty=get_order_status_price_qty(pe_order_id)
        if pe_f_qty<qty:
            ce_ltp,pe_ltp=get_ce_and_pe_ltp(ce_symbol, pe_symbol)
            modify_limit_order(pe_order_id, pe_ltp-limit_entry)
            a=a+1
            time.sleep(5)
        else:
            break          
    

    ce_status,ce_avg,ce_f_qty,ce_p_qty=get_order_status_price_qty(ce_order_id)
    pe_status,pe_avg,pe_f_qty,pe_p_qty=get_order_status_price_qty(pe_order_id)
 
    
    if ((ce_f_qty!=qty) or (pe_f_qty!=qty)):
        print('partial execution of order')
        # include code to cancel order
        entry_status=False
        if ce_f_qty>0:
            market_order_buy(ce_symbol, ce_f_qty)
        if pe_f_qty>0:
            market_order_buy(pe_symbol, pe_f_qty)
    return ce_avg,pe_avg,entry_status



##########################################################################
##########################################################################

while dt.datetime.now().time()<trade_entry_time:
    time.sleep(1)

ce_sell_price,pe_sell_price,entry_status=short_entry()

if entry_status:
    sell_value=ce_sell_price*qty+pe_sell_price*qty
    while True:
        ce_ltp,pe_ltp=get_ce_and_pe_ltp(ce_symbol, pe_symbol)
        current_value=(ce_ltp+pe_ltp)*qty
        
        pnl=sell_value-current_value
        
        if pnl>0:
            print('profit ',pnl)
        else:
            print('loss ',pnl)
        
        if pnl>=target_profit:
            exit_entry()
            print('Profit booked')
            break
        elif pnl<=max_loss*(-1):
            exit_entry()
            print('Exited in loss')
            break
        if dt.datetime.now().time()>=square_off_time:
            exit_entry()
            print('Exited -- square off time --')
            break
        
        time.sleep(1)
    
print('End of Program')



