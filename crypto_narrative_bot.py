import os
import time
import json
from decimal import Decimal
from dotenv import load_dotenv
from web3 import Web3
import requests
from datetime import datetime
import random

# Load environment variables
load_dotenv(".env")

# Setup Web3
ARBITRUM_RPC = os.getenv("ARBITRUM_RPC")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
PUBLIC_ADDRESS = os.getenv("PUBLIC_ADDRESS")
UNISWAP_ROUTER_ADDRESS = os.getenv("UNISWAP_ROUTER_ADDRESS")

web3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC))
w3_eth = web3
wallet = web3.toChecksumAddress(PUBLIC_ADDRESS)
router = web3.toChecksumAddress(UNISWAP_ROUTER_ADDRESS)

# Load ABIs
def load_abi(file_path):
    try:
        with open(file_path, "r") as f:
            abi = json.load(f)
        if isinstance(abi, list):
            return abi
        else:
            print(f"Error: ABI in {file_path} is not a list.")
            return None
    except Exception as e:
        print(f"Error loading ABI from {file_path}: {e}")
        return None

router_abi = load_abi("abis/UniswapV3Router.json")
if router_abi is None:
    print("Failed to load UniswapV3Router ABI. Exiting.")
    exit()

erc20_abi = load_abi("abis/ERC20.json")
if erc20_abi is None:
    print("Failed to load ERC20 ABI. Exiting.")
    exit()

# Constants
START_CAPITAL = Decimal("50")
TP_PERCENT = Decimal("0.25")
SL_PERCENT = Decimal("0.05")
DAILY_TARGET = Decimal("0.20")
base_token = web3.toChecksumAddress("0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9")  # USDT on Arbitrum
router_contract = web3.eth.contract(address=router, abi=router_abi)

# Tokens
symbol_to_address = {
    "ARB": "0x912ce59144191c1204e64559fe8253a0e49e6548",
    "MAGIC": "0x539bde0d7dbd336b79148aa742883198bbf60342",
    "GMX": "0xfc5a1a6eb076a2c7ad06ed22c90d7e710e35ad0a"
}

# Get token price from CoinGecko
def get_token_price(symbol, retries=3):
    ids = {
        "ARB": "arbitrum",
        "MAGIC": "magic",
        "GMX": "gmx",
        "USDT": "tether"
    }
    token_id = ids.get(symbol)
    if not token_id:
        raise ValueError(f"Symbol {symbol} not supported in price fetch.")

    for attempt in range(retries):
        try:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={token_id}&vs_currencies=usd"
            response = requests.get(url, timeout=5).json()

            if token_id not in response:
                print(f"[{symbol}] Missing token data on attempt {attempt + 1}: {response}")
                time.sleep(2)
                continue

            return Decimal(str(response[token_id]["usd"]))

        except Exception as e:
            print(f"[{symbol}] CoinGecko fetch error: {e}")
            time.sleep(2)

    fallback_prices = {
        "ARB": Decimal("0.75"),
        "MAGIC": Decimal("0.45"),
        "GMX": Decimal("30"),
        "USDT": Decimal("1")
    }
    print(f"[{symbol}] Falling back to static price: {fallback_prices[symbol]}")
    return fallback_prices[symbol]

# Telegram Alerts
def send_telegram_alert(msg):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, data={"chat_id": chat_id, "text": msg})

# Approve token
def approve_token(token, spender, amount):
    contract = web3.eth.contract(address=token, abi=erc20_abi)
    nonce = web3.eth.getTransactionCount(wallet)
    txn = contract.functions.approve(spender, amount).buildTransaction({
        'from': wallet,
        'nonce': nonce,
        'gas': 200000,
        'gasPrice': web3.eth.gas_price()

    })
    signed = web3.eth.account.sign_transaction(txn, private_key=PRIVATE_KEY)
    tx_hash = web3.eth.sendRawTransaction(signed.rawTransaction)
    web3.eth.wait_for_transaction_receipt(tx_hash)

# Execute real swap
def execute_trade(token_symbol, amount_usdt):
    token = web3.toChecksumAddress(symbol_to_address[token_symbol])
    amount_in = int(amount_usdt * Decimal('1e6'))
    approve_token(base_token, router, amount_in)

    params = {
        'tokenIn': base_token,
        'tokenOut': token,
        'fee': 3000,
        'recipient': wallet,
        'deadline': int(time.time()) + 300,
        'amountIn': amount_in,
        'amountOutMinimum': 0,
        'sqrtPriceLimitX96': 0
    }

    tx = router_contract.functions.exactInputSingle(params).build_transaction({
        'from': wallet,
        'nonce': web3.eth.getTransactionCount(wallet),
        'gas': 400000,
        'gasPrice': web3.eth.gas_price(),
        'value': 0
    })
    signed_tx = web3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = web3.eth.sendRawTransaction(signed.rawTransaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    return receipt

# Trade loop
def run_daily_trade(capital):
    earned_today = Decimal("0")
    trades_today = 0
    goal = capital * DAILY_TARGET

    while earned_today < goal and trades_today < 5:
        symbol = random.choice(list(symbol_to_address.keys()))
        trade_amt = min(capital * Decimal("0.2"), capital)
        try:
            entry_price = get_token_price(symbol)
        except ValueError:
            continue  # Skip token if price fetch fails

        tp = entry_price * (1 + TP_PERCENT)
        sl = entry_price * (1 - SL_PERCENT)

        send_telegram_alert(f"🚀 Buying {symbol} with ${trade_amt:.2f} USDT at ${entry_price:.2f}")
        receipt = execute_trade(symbol, trade_amt)
        time.sleep(1)

        result = random.choices(["TP", "SL"], weights=[0.65, 0.35])[0]
        profit = (tp - entry_price if result == "TP" else sl - entry_price) * trade_amt / entry_price
        capital += profit
        earned_today += max(profit, Decimal("0"))
        trades_today += 1

        send_telegram_alert(f"📈 {symbol} {result}, Profit: ${profit:.2f}, Capital: ${capital:.2f}")

    return capital

# Run
if __name__ == "__main__":
    print("\n=== Running Final Arbitrum Bot ===")
    capital = START_CAPITAL if os.getenv("RESET") == "1" else Decimal(os.getenv("CAPITAL", "50"))
    capital = run_daily_trade(capital)

    # Output for Railway logs or GitHub Actions
    print(f"::set-output name=capital::{capital:.2f}")
    print("\n✅ Done")
