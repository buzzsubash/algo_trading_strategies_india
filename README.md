# Algo Trading Strategies India

[![GitHub stars](https://img.shields.io/github/stars/buzzsubash/algo_trading_strategies_india?style=social)](https://github.com/buzzsubash/algo_trading_strategies_india/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/buzzsubash/algo_trading_strategies_india?style=social)](https://github.com/buzzsubash/algo_trading_strategies_india/network/members)
[![License](https://img.shields.io/github/license/buzzsubash/algo_trading_strategies_india)](LICENSE)

## About
This repository is an **open-source** collection of **algorithmic trading strategies** for the Indian stock market, with a primary focus on **option selling** in:
- **NIFTY 50**
- **BANK NIFTY**
- **SENSEX**
- **MIDCAP NIFTY**
- **FIN NIFTY**

### 🔹 **Current Broker Support**
- ✅ **Zerodha** (Live & Ready to Deploy)
- ⚙️ **AngelOne, Upstox, Fyers, AliceBlue, etc.** *(Coming Soon!)*

## 🚀 Features
✅ **Multiple short-straddle strategies** with different risk management techniques  
✅ **Iron-fly strategies** for hedged option selling  
✅ **Stop-loss mechanisms** including **fixed, percentage-based, and trailing stops**  
✅ **Mark-to-market (MTM) based target execution**  
✅ Future expansion for **multi-broker support**

---

## 📂 Repository Structure

| Strategy Category | Sub-Category | Strategy Name | Zerodha | AngelOne | Upstox | Fyers | GitHub Link |
|------------------|-------------|---------------|---------|----------|--------|-------|-------------|
| **Short Straddle** | 0920 Expiry | FINNIFTY 0920 | ✅ | ❌ | ❌ | ❌ | [View Code](https://github.com/buzzsubash/algo_trading_strategies_india/blob/main/short-straddle/0920_short_straddle/finnifty_0920_short_straddle.py) |
|  |  | NIFTY50 0920 | ✅ | ❌ | ❌ | ❌ | [View Code](https://github.com/buzzsubash/algo_trading_strategies_india/blob/main/short-straddle/0920_short_straddle/nifty50_0920_short_straddle.py) |
|  | Combined Premium | BANK NIFTY Combined Premium | ✅ | ✅ | ❌ | ❌ | [View Code](https://github.com/buzzsubash/algo_trading_strategies_india/blob/main/short-straddle/combined_premium/bank_nifty_combined_premium_short_straddle.py) |
|  |  | FINNIFTY Combined Premium | ✅ | ❌ | ❌ | ❌ | [View Code](https://github.com/buzzsubash/algo_trading_strategies_india/blob/main/short-straddle/combined_premium/finnifty_combined_premium_short_straddle.py) |
|  |  | NIFTY50 Combined Premium | ✅ | ❌ | ❌ | ❌ | [View Code](https://github.com/buzzsubash/algo_trading_strategies_india/blob/main/short-straddle/combined_premium/nifty50_combined_premium_short_straddle.py) |
|  |  | SENSEX Combined Premium | ✅ | ❌ | ❌ | ❌ | [View Code](https://github.com/buzzsubash/algo_trading_strategies_india/blob/main/short-straddle/combined_premium/sensex_combined_premium_short_straddle.py) |
|  | Fixed Stop Loss | BANK NIFTY Fixed SL | ✅ | ❌ | ❌ | ❌ | [View Code](https://github.com/buzzsubash/algo_trading_strategies_india/blob/main/short-straddle/fixed_stop_loss/bank_nifty_fixed_stop_loss_short_straddle.py) |
|  |  | BANK NIFTY Account-Level MTM SL | ✅ | ❌ | ❌ | ❌ | [View Code](https://github.com/buzzsubash/algo_trading_strategies_india/blob/main/short-straddle/fixed_stop_loss/bank_nifty_account_level_mtm_with_fixed_stop_loss_short_straddle.py) |
|  | MTM Based Target | BANK NIFTY MTM-Based | ✅ | ❌ | ❌ | ❌ | [View Code](https://github.com/buzzsubash/algo_trading_strategies_india/blob/main/short-straddle/mtm_based_target/bank_nifty_mtm_based_short_straddle.py) |
|  |  | NIFTY50 MTM-Based | ✅ | ❌ | ❌ | ❌ | [View Code](https://github.com/buzzsubash/algo_trading_strategies_india/blob/main/short-straddle/mtm_based_target/nifty50_mtm_based_short_straddle.py) |
|  | Percentage-Based Stop Loss | BANK NIFTY Percentage-Based SL | ✅ | ❌ | ❌ | ❌ | [View Code](https://github.com/buzzsubash/algo_trading_strategies_india/blob/main/short-straddle/percentage_based_stop_loss/bank_nifty_percentage_based_stop_loss_short_straddle.py) |
|  | Trailing Stop Loss | BANK NIFTY Trailing Percentage-Based SL | ✅ | ❌ | ❌ | ❌ | [View Code](https://github.com/buzzsubash/algo_trading_strategies_india/blob/main/short-straddle/trailing_stop_loss/bank_nifty_trailing_percentage_based_stop_loss_short_straddle.py) |
| **Iron-Fly** | | | ⚙️ In Development | | | | *(Coming Soon!)* |
| **Short-Strangle** | | | ⚙️ In Development | | | | *(Coming Soon!)* |

---

## 📌 How to Use
1. Clone this repository:
   ```sh
   git clone https://github.com/buzzsubash/algo_trading_strategies_india.git
