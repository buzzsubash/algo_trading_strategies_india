from kiteconnect import KiteConnect
import datetime as dt
import time
import pandas as pd

# Your Zerodha Kite Connect API key, unique to your account, used for authentication.
api_key = '1t306nxawlfv28w4'

# Access token read from a file. This token is used to authenticate your session with the Kite Connect API.
# It's typically obtained after a successful login and has a limited validity period.
access_token_zer = open("/home/buzzsubash/utilities/kite-connect-data/key files/access_token_subash.txt", 'r').read()

# Initialize the KiteConnect object with your API key.
kite = KiteConnect(api_key=api_key)

# Set the access token for the session to authenticate your API requests.
kite.set_access_token(access_token_zer)

# Define the time at which you want to enter the trades. This is set before market opening.
trade_entry_time = dt.time(hour=8, minute=45, second=10)

# Define the time to square off (close) all open positions. This is set just before market closing.
square_off_time = dt.time(hour=17, minute=59, second=50)

# Define how many strike points away from the current price you want to trade.
# 0 for At The Money (ATM), positive for In The Money (ITM), negative for Out of The Money (OTM).
# Example # 0(atm), 50(ITM 50) -50(OTM 100)...etc
strike_points = 0  # ATM
# Number of lots you intend to trade.
lots = 36

# Define the stop loss value in points.
stop_loss = 5

# Trailing stop loss (TSL) value in points. This value is used to adjust the stop loss as the trade moves in your favor.
tsl = 1

# Define the lot size for the instrument being traded. This is specific to the contract and determines the number of units per lot.
lot_size = 50

# List of National Stock Exchange (NSE) holidays when the market is closed. No trading will occur on these dates.
nse_holidays = [
    dt.date(2023, 1, 26), dt.date(2023, 3, 8), dt.date(2023, 3, 30),
    dt.date(2023, 4, 4), dt.date(2023, 4, 7), dt.date(2023, 4, 14),
    dt.date(2023, 4, 21), dt.date(2023, 6, 28), dt.date(2023, 8, 15),
    dt.date(2023, 9, 19), dt.date(2023, 10, 2), dt.date(2023, 10, 24),
    dt.date(2023, 11, 14), dt.date(2023, 11, 27), dt.date(2023, 12, 25)
]


def get_expiry_date():
    """
    Calculates the expiry date of the current trading week. This function assumes the expiry
    to be on a Thursday, which is standard for weekly contracts in Indian markets.
    If the calculated expiry date is a holiday, it adjusts the date to the previous trading day.

    Returns:
        datetime.date: The calculated expiry date.
    """

    # Get today's date
    current_date = dt.date.today()

    # Find the current day of the week as an integer, where Monday is 0 and Sunday is 6
    wd = current_date.weekday()

    # Initialize the variable x, which will be used to calculate the offset to the next Thursday
    x = 0
    if wd <= 3:  # If today is Monday through Thursday,
        x = (3 - wd)  # set x to the number of days until Thursday
    else:  # If today is Friday through Sunday,
        x = 6  # set x to the number of days until next week's Thursday

    # Calculate the tentative expiry date by adding x days to the current date
    exp_date = current_date + dt.timedelta(days=x)

    # Check if the calculated expiry date is a holiday
    if exp_date in nse_holidays:
        exp_date = exp_date - dt.timedelta(days=1)  # If so, move the expiry to one day earlier

    # Check again in case the new date is also a holiday (double-checking for consecutive holidays)
    if exp_date in nse_holidays:
        exp_date = exp_date - dt.timedelta(days=1)  # Adjust again if necessary

    # Return the calculated expiry date
    return exp_date


def get_nifty_ltp():
    """
    Retrieves the Last Traded Price (LTP) for the NIFTY 50 index from the Kite Connect API.

    This function attempts to fetch the LTP up to 10 times in case of failure, with a 1-second
    pause between attempts. If it fails to retrieve the LTP after 10 attempts, it raises an
    exception.

    Returns:
        float: The last traded price of the NIFTY 50 index.

    Raises:
        ValueError: If the LTP cannot be retrieved after multiple attempts.
    """

    # Initialize nt_ltp with a default value of None
    nt_ltp = None
    # Counter for the number of attempts made to fetch the LTP
    a = 0
    while a < 10:  # Attempt up to 10 times
        try:
            # Attempt to fetch the LTP for the NIFTY 50 index
            nt = kite.ltp('NSE:NIFTY 50')
            # If successful, extract the LTP from the response and break the loop
            nt_ltp = nt['NSE:NIFTY 50']['last_price']
            break
        except Exception as e:  # Catch any exceptions during the API call
            # Print the attempt number and error message
            print(f"Attempt {a + 1}: Failed to retrieve NIFTY LTP - {e}")
            # Wait for 1 second before the next attempt
            time.sleep(1)
            # Increment the attempt counter
            a += 1
    # If the LTP could not be retrieved after 10 attempts, raise an error
    if nt_ltp is None:
        raise ValueError("Failed to retrieve NIFTY LTP after multiple attempts.")
    # Return the last traded price
    return nt_ltp


def get_nifty_atm_strike(nifty_ltp):
    """
    Calculates the nearest At The Money (ATM) strike price for Nifty options based on the Last Traded Price (LTP) of Nifty.

    The Nifty options are available in strike price intervals of 50. This function rounds the LTP to the nearest
    strike price that is a multiple of 50. If the LTP is exactly in the middle of two strike prices, it rounds
    to the next higher strike price.

    Parameters:
        nifty_ltp (float): The last traded price of Nifty.

    Returns:
        int: The ATM strike price, rounded to the nearest multiple of 50.
    """

    # Calculate the remainder when the LTP is divided by 50.
    # This determines how far the LTP is from the nearest lower multiple of 50.
    r = nifty_ltp % 50

    # If the remainder is less than 25, the LTP is closer to the lower multiple of 50,
    # so subtract the remainder from the LTP to get the lower strike price.
    # Otherwise, subtract the remainder and add 50 to get the next higher multiple of 50.
    if r < 25:
        atm = nifty_ltp - r
    else:
        atm = nifty_ltp - r + 50

    # Return the ATM strike price as an integer.
    return int(atm)


def get_trading_symbol(df, strike, CE_or_PE):
    """
    Retrieves the trading symbol for a specific Nifty option based on the strike price and option type (CE for Calls, PE for Puts).

    Parameters:
        df (DataFrame): A pandas DataFrame containing options data, including columns for 'strike' and 'instrument_type'.
        strike (int): The strike price of the option for which the trading symbol is required.
        CE_or_PE (str): The type of option - 'CE' for Call options and 'PE' for Put options.

    Returns:
        str: The trading symbol for the specified option.

    Raises:
        IndexError: If the specified option type and strike price combination does not exist in the dataframe.
    """

    # Filter the dataframe for the rows matching the specified strike price.
    df_1 = df[df.strike == strike]

    # From the filtered dataframe, select the row where the instrument type matches 'CE' (Call option)
    # and retrieve the trading symbol. Similarly, retrieve the trading symbol for 'PE' (Put option).
    ce_name = df_1[df_1.instrument_type == 'CE'].tradingsymbol.values[0]
    pe_name = df_1[df_1.instrument_type == 'PE'].tradingsymbol.values[0]

    # Based on the option type specified ('CE' or 'PE'), set the 'symbol' variable to the corresponding trading symbol.
    if CE_or_PE == 'CE':
        symbol = ce_name
    elif CE_or_PE == 'PE':
        symbol = pe_name

    # Return the trading symbol for the specified option.
    return symbol


def get_ce_and_pe_ltp(ce_symbol, pe_symbol):
    """
    Retrieves the Last Traded Price (LTP) for specified Call and Put options from the Kite Connect API.

    This function constructs the full trading symbols for the options by prefixing them with 'NFO:'
    to denote the Nifty Futures and Options segment. It then attempts to fetch the LTPs for these
    options up to 25 times in case of a failure, with a 1-second pause between attempts.

    Parameters:
        ce_symbol (str): The trading symbol for the Call option.
        pe_symbol (str): The trading symbol for the Put option.

    Returns:
        tuple: A tuple containing the LTPs for the Call and Put options (ce_ltp, pe_ltp).

    Raises:
        Exception: If the LTP cannot be retrieved for either option after 25 attempts.
    """

    # Prefix the given symbols with 'NFO:' to specify the Nifty Futures and Options segment.
    ce = 'NFO:' + ce_symbol
    pe = 'NFO:' + pe_symbol

    # Initialize a counter for the number of attempts.
    a = 0

    # Attempt to fetch the LTPs up to 25 times.
    while a < 25:
        try:
            # Attempt to fetch the LTP for both the Call and Put options.
            option = kite.ltp(ce, pe)
            ce_ltp = option[ce]['last_price']  # Extract the LTP for the Call option.
            pe_ltp = option[pe]['last_price']  # Extract the LTP for the Put option.
            break  # Exit the loop on successful retrieval.
        except:
            # If an attempt fails, pause for 1 second before retrying.
            time.sleep(1)
            # Increment the attempt counter.
            a += 1

    # If the LTPs were successfully retrieved, return them as a tuple.
    return ce_ltp, pe_ltp


def marketorder_buy(symbol, quantity):
    """
    Places a market order to buy a specified quantity of an instrument (options contract) through the Kite Connect API.

    This function constructs and sends a buy order request for the given trading symbol with the specified quantity.
    The order is placed in the Nifty Futures and Options segment (NFO) as a normal (NRML) product, which implies
    that it's not a day trade and can be carried overnight. The order is sent as a market order, meaning it will
    be executed immediately at the current market price.

    Parameters:
        symbol (str): The trading symbol for the instrument to be bought.
        quantity (int): The quantity of the instrument to be bought.

    Returns:
        str: The order ID of the placed order. This ID can be used for order tracking and management.

    Note:
        - This function assumes that the Kite Connect instance `kite` is already authenticated and available globally.
        - It's important to handle exceptions that may arise due to issues with the order placement request outside
          this function.
    """

    # Place the buy order using the Kite Connect API.
    return kite.place_order(
        tradingsymbol=symbol,  # Trading symbol of the instrument to buy.
        exchange=kite.EXCHANGE_NFO,  # The exchange segment (NFO for Nifty Futures and Options).
        transaction_type=kite.TRANSACTION_TYPE_BUY,  # The type of transaction (BUY in this case).
        quantity=quantity,  # The quantity of the instrument to buy.
        order_type=kite.ORDER_TYPE_MARKET,  # The type of order (MARKET for immediate execution at current price).
        product=kite.PRODUCT_NRML,  # The product code (NRML for normal orders that can be carried overnight).
        variety=kite.VARIETY_REGULAR  # The variety of the order (REGULAR for standard orders).
    )


def marketorder_sell(symbol, quantity):
    """
    Places a market order to sell a specified quantity of an instrument (options contract) through the Kite Connect API.

    This function sends a request to sell the given trading symbol at the current market price with the specified quantity.
    The order is placed in the Nifty Futures and Options segment (NFO) as a normal (NRML) product. This means the order
    is not a day trade and is intended for positions that may be held overnight. The order type is set to market, indicating
    it will execute immediately at the best available current price.

    Parameters:
        symbol (str): The trading symbol for the instrument to be sold.
        quantity (int): The quantity of the instrument to be sold.

    Returns:
        str: The order ID of the placed sell order. This ID is useful for future order tracking and management.

    Note:
        - It assumes that the Kite Connect instance `kite` is already authenticated and available globally.
        - Proper exception handling for issues related to order placement should be considered outside this function.
    """

    # Place the sell order using the Kite Connect API.
    return kite.place_order(
        tradingsymbol=symbol,  # Trading symbol of the instrument to sell.
        exchange=kite.EXCHANGE_NFO,  # The exchange segment for the order (NFO for Nifty Futures and Options).
        transaction_type=kite.TRANSACTION_TYPE_SELL,  # The type of transaction, SELL in this case.
        quantity=quantity,  # The quantity of the instrument to be sold.
        order_type=kite.ORDER_TYPE_MARKET,  # Order type, MARKET for immediate execution at the best available price.
        product=kite.PRODUCT_NRML,  # The product code (NRML), indicating it's a regular order not restricted to day trading.
        variety=kite.VARIETY_REGULAR  # The variety of the order, REGULAR for standard orders.
    )


def get_order_status_price_qty(order_id):
    """
    Fetches and returns the status, average price, filled quantity, and pending quantity of an order based on its ID.

    This function attempts to retrieve the complete order book from the Kite Connect API and filters out the specific
    order by its ID to extract relevant details. If the order book cannot be fetched after multiple attempts, it
    returns default values indicating an unsuccessful retrieval.

    Parameters:
        order_id (str): The unique identifier of the order.

    Returns:
        tuple: A tuple containing the order's status (str), average price (float),
               filled quantity (int), and pending quantity (int).

    Note:
        - This function assumes that the Kite Connect instance `kite` is already authenticated and globally accessible.
        - In case the order book cannot be fetched or the specific order is not found, default values ('', 0, 0, 0) are returned.
    """

    # Initialize default values for return variables.
    status = ''
    avg = 0.0
    f_qty = 0
    p_qty = 0

    # Ensure the order_id is a string, as required by the API.
    order_id = str(order_id)

    # Attempt to fetch the order book up to 11 times (from 0 to 10).
    a = 0
    while a <= 10:
        try:
            # Fetch the entire order book and convert it into a pandas DataFrame.
            ord_df = pd.DataFrame(kite.orders())
            break  # Break the loop if the order book is successfully fetched.
        except:
            # If fetching fails, print an error message, wait 2 seconds, and retry.
            print("can't extract ORDER BOOK data..retrying")
            time.sleep(2)
            a += 1

    # If the order book was fetched successfully,
    if len(ord_df) > 0:
        # Filter the DataFrame for the specific order ID.
        df = ord_df[ord_df.order_id == order_id]
        # If the specific order is found in the DataFrame,
        if len(df) > 0:
            # Extract the order's status, average price, filled quantity, and pending quantity.
            status = df['status'].iloc[-1]
            avg = float(df['average_price'].iloc[-1])
            f_qty = int(df['filled_quantity'].iloc[-1])
            p_qty = int(df['pending_quantity'].iloc[-1])

    # Return the extracted order details or default values if the order was not found.
    return status, avg, f_qty, p_qty


def cancel_order(order_id):
    """
    Attempts to cancel an order based on its ID.

    This function sends a request to the Kite Connect API to cancel a specific order. If the cancellation is unsuccessful,
    an error message is printed.

    Parameters:
        order_id (str): The unique identifier of the order to be cancelled.

    Note:
        - This function assumes that the Kite Connect instance `kite` is already authenticated and globally accessible.
        - It is important to handle exceptions appropriately to avoid unintended consequences of a failed cancellation.
    """

    try:
        # Attempt to cancel the order using its ID.
        kite.cancel_order(order_id=order_id, variety=kite.VARIETY_REGULAR)
    except:
        # If cancellation fails, print an error message.
        print('order cancellation error')


##########################################################################
##########################################################################
# Calculate the total quantity to be traded based on the number of lots and the lot size.
qty = lots * lot_size

# Try to download the instrument dump from the NFO segment up to 16 times (0 through 15).
a = 0
while a <= 15:
    try:
        instrument_dump = kite.instruments("NFO")
        break  # Exit the loop if the download is successful.
    except:
        # If the download fails, print an error message, wait for 1 second, and retry.
        print('instrument dump download error..Retrying')
        a = a + 1
        time.sleep(1)

# Convert the instrument dump to a pandas DataFrame for easier manipulation.
instrument_df = pd.DataFrame(instrument_dump)

# Filter the DataFrame to include only instruments with the name 'NIFTY'.
nifty = instrument_df[instrument_df.name == 'NIFTY']

# Calculate the expiry date for the current trading cycle.
expiry_date = get_expiry_date()

# Further filter the DataFrame to include only NIFTY instruments with the calculated expiry date.
nifty_exp_df = nifty[nifty.expiry == expiry_date]

# Wait until the trade entry time is reached.
while dt.datetime.now().time() < trade_entry_time:
    time.sleep(1)

# Fetch the current Last Traded Price (LTP) of NIFTY.
nt_ltp = get_nifty_ltp()
print('Nifty : ', nt_ltp)

# Calculate the At The Money (ATM) strike price based on the NIFTY LTP.
atm = get_nifty_atm_strike(nt_ltp)
print('ATM Strike: ', atm)

# Retrieve the trading symbols for the ATM call and put options.
ce_symbol = get_trading_symbol(nifty_exp_df, atm - strike_points, 'CE')
pe_symbol = get_trading_symbol(nifty_exp_df, atm + strike_points, 'PE')

print('ce symbol is ', ce_symbol)
print('pe symbol is ', pe_symbol)

# Place market orders to sell the calculated quantity of the ATM call and put options.
ce_order_id = marketorder_sell(ce_symbol, qty)
pe_order_id = marketorder_sell(pe_symbol, qty)
# Wait 10 seconds to ensure the orders have been processed.
time.sleep(10)

# Fetch and print the status, sell price, filled quantity, and pending quantity for both the call and put option orders.
ce_status, ce_sell_price, ce_f_qty, ce_p_qty = get_order_status_price_qty(ce_order_id)
pe_status, pe_sell_price, pe_f_qty, pe_p_qty = get_order_status_price_qty(pe_order_id)

# Check if both the Call and Put option orders have been completed successfully.
if ce_status == 'COMPLETE' and pe_status == 'COMPLETE':
    # If both orders are complete, print the sell prices for the Call and Put options.
    print('ce sell price is ', round(ce_sell_price, 2))
    print('pe sell price is ', round(pe_sell_price, 2))

    # Calculate the combined premium received from selling both the Call and Put options.
    sell_premium = ce_sell_price + pe_sell_price
    print('combined premium ', round(sell_premium, 2))

    # Set the initial premium base to the combined sell premium. This value may be used later
    # to calculate adjustments for a trailing stop loss or to assess the trade's profitability.
    premium_base = sell_premium

    # Calculate the initial stop loss premium by adding the predefined stop loss value to the combined sell premium.
    # This defines the premium level at which the positions should be exited to limit losses, based on the strategy's risk tolerance.
    sl_premium = sell_premium + stop_loss

    while True:
        # Fetch the current Last Traded Prices (LTPs) for the CE and PE options.
        ce_ltp, pe_ltp = get_ce_and_pe_ltp(ce_symbol, pe_symbol)
        # Calculate the current combined premium from both options.
        current_premium = ce_ltp + pe_ltp

        # Display the current trading metrics.
        print('===============')
        print('Combined premium ', round(sell_premium, 2))
        print('Stop loss ', round(sl_premium, 2))
        print('Gain ', round((sell_premium - current_premium), 2))
        print('MTM ', round((sell_premium - current_premium) * 50 * lots), 2)

        # Adjust the trailing stop loss (TSL) if the conditions are met.
        if current_premium < premium_base - tsl:
            premium_decrease = premium_base - current_premium
            premium_base = current_premium  # Update the base premium for TSL calculation.
            sl_premium = current_premium + stop_loss  # Adjust the stop loss premium.
            print(f"TSL Adjusted: New SL Premium {sl_premium}, Profit Locked {premium_decrease}")

        # Check if the current premium has hit or exceeded the stop loss level.
        if current_premium >= sl_premium:
            # Exit positions due to stop loss trigger.
            print("Exiting positions due to stop loss trigger.")
            marketorder_buy(ce_symbol, qty)  # Buy back the CE option to close the position.
            marketorder_buy(pe_symbol, qty)  # Buy back the PE option to close the position.
            break  # Exit the main loop.

        # Exit the positions at the predetermined square off time.
        if dt.datetime.now().time() >= square_off_time:
            print("Market close time reached. Exiting positions.")
            marketorder_buy(ce_symbol, qty)  # Buy back the CE option to close the position.
            marketorder_buy(pe_symbol, qty)  # Buy back the PE option to close the position.
            break  # Exit the main loop.

        # Wait a bit before the next iteration to not overwhelm the API.
        time.sleep(1)
else:
    # Handling scenarios where either of the orders (CE or PE) is not fully completed.
    # This part aims to address the incomplete execution of orders.

    # If the CE order is not completed, log its status.
    if ce_status != 'COMPLETE':
        print('CE sell order status is ', ce_status)

        # If the PE order is complete, suggesting partial strategy execution, take steps to mitigate risk.
        if pe_status == "COMPLETE" and int(pe_f_qty) > 0:
            # Consider buying back the PE option to close the position, aiming to neutralize the strategy's exposure.
            print('PE filled quantity buying back to close the position...')
            marketorder_buy(pe_symbol, int(pe_f_qty))

    # Similarly, if the PE order is not completed, log its status.
    if pe_status != 'COMPLETE':
        print('PE sell order status is ', pe_status)

        # If the CE order is complete, suggesting partial strategy execution, take steps to mitigate risk.
        if ce_status == "COMPLETE" and int(ce_f_qty) > 0:
            # Consider buying back the CE option to close the position, aiming to neutralize the strategy's exposure.
            print('CE filled quantity buying back to close the position...')
            marketorder_buy(ce_symbol, int(ce_f_qty))

print('End of Program')



