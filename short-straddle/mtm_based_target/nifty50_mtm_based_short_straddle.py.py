from kiteconnect import KiteConnect
import datetime as dt
import time
import pandas as pd

api_key = '1t306nxawlfv28w4'
access_token_zer = open("/home/ubuntu/utilities/subash_algo_trading/key_files/access_token.txt", 'r').read()

kite = KiteConnect(api_key=api_key)
kite.set_access_token(access_token_zer)

trade_entry_time=dt.time(hour=11,minute=45,second=2)
square_off_time=dt.time(hour=17,minute=59,second=50)
lot_size=50
strike_points=0  # 0(atm), 50(itm 50) 100(itm 100)...etc
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





def get_nifty_ltp():
    a = 0
    while a < 10:
        try:
            nt=kite.ltp('NSE:NIFTY 50')
            nt_ltp=nt['NSE:NIFTY 50']['last_price']
            break
        except:
            time.sleep(1)
            a+=1
    return nt_ltp

def get_nifty_atm_strike(nifty_ltp):
    r=nifty_ltp%50
    if r<25:
       atm=nifty_ltp-r
    else:
        atm=nifty_ltp-r+50
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
    while a < 25:
        try:
            option=kite.ltp(ce,pe)
            ce_ltp=option[ce]['last_price']
            pe_ltp=option[pe]['last_price']
            break
        except:
            time.sleep(1)
            a+=1
    return ce_ltp,pe_ltp

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

def cancel_order(order_id):    
    try:
        kite.cancel_order(order_id=order_id,
                    variety=kite.VARIETY_REGULAR)
    except:
        print('order cancellatin error')
    
##########################################################################
##########################################################################
qty=quantity*lot_size
a=0
while a<=15:
    try:
        instrument_dump = kite.instruments("NFO")
        break
    except:
        print('instrument dump download error..Retrying')
        a=a+1
        time.sleep(1)

instrument_df = pd.DataFrame(instrument_dump)

nifty=instrument_df[instrument_df.name=='NIFTY']
expiry_date=get_expiry_date()

nifty_exp_df=nifty[nifty.expiry==expiry_date]

while dt.datetime.now().time()<trade_entry_time:
    time.sleep(1)

nt_ltp=get_nifty_ltp()
print('Nifty : ',nt_ltp)

atm=get_nifty_atm_strike(nt_ltp)
print('ATM Strike: ',atm)

ce_symbol=get_trading_symbol(nifty_exp_df, atm-strike_points, 'CE')
pe_symbol=get_trading_symbol(nifty_exp_df, atm+strike_points, 'PE')

print('ce symbol is ',ce_symbol)
print('pe symbol is ',pe_symbol)

ce_order_id=marketorder_sell(ce_symbol, qty)
pe_order_id=marketorder_sell(pe_symbol, qty)
time.sleep(3)

ce_status,ce_sell_price,ce_f_qty,ce_p_qty=get_order_status_price_qty(ce_order_id)
pe_status,pe_sell_price,pe_f_qty,pe_p_qty=get_order_status_price_qty(pe_order_id)

if ce_status=='COMPLETE' and pe_status=='COMPLETE':
    
    print('ce sell price is ',ce_sell_price)
    print('pe sell price is ',pe_sell_price)
    
    sell_value=(ce_sell_price+pe_sell_price)*qty
    
    while True:
        ce_ltp,pe_ltp=get_ce_and_pe_ltp(ce_symbol, pe_symbol)
        current_value=(ce_ltp+pe_ltp)*qty
        
        pnl=sell_value-current_value
        
        if pnl>0:
            print('profit ',pnl)
        else:
            print('loss ',pnl)
        
        if pnl>=target_profit:
            marketorder_buy(ce_symbol, qty)
            marketorder_buy(pe_symbol, qty)
            print('Profit booked')
            break
        elif pnl<=max_loss*(-1):
            marketorder_buy(ce_symbol, qty)
            marketorder_buy(pe_symbol, qty)
            print('Exited in loss')
            break
        
        if dt.datetime.now().time()>=square_off_time:
            marketorder_buy(ce_symbol, qty)
            marketorder_buy(pe_symbol, qty)
            print('Exited -- square off time --')
            break
        
        time.sleep(1)
else:
    if ce_status!='COMPLETE':
        print('CE sell order status is ',ce_status)
        if pe_status=="COMPLETE":
            print('PE order status is ', pe_status)
            if int(pe_f_qty)>0:
                print('pe filled quantity buying....')
                marketorder_buy(pe_symbol, int(pe_f_qty))
    if pe_status!='COMPLETE':
        print('PE sell order status is ',ce_status)
        if ce_status=="COMPLETE":
            print('PE order status is ', pe_status)
            if int(ce_f_qty)>0:
                print('ce filled quantity buying....')
                marketorder_buy(ce_symbol, int(ce_f_qty))
    
    
print('End of Program')



