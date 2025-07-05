import os
import time
import json
from decimal import Decimal
from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import geth_poa_middleware
import requests
from datetime import datetime
import random

# Load env
load_dotenv(".env")

# Setup Web3
ARBITRUM_RPC = os.getenv("ARBITRUM_RPC")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
PUBLIC_ADDRESS = os.getenv("PUBLIC_ADDRESS")
UNISWAP_ROUTER_ADDRESS = os.getenv("UNISWAP_ROUTER_ADDRESS")

web3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC))
web3.middleware_onion.inject(geth_poa_middleware, layer=0)
wallet = web3.toChecksumAddress(PUBLIC_ADDRESS)
router = web3.toChecksumAddress(UNISWAP_ROUTER_ADDRESS)

# Load ABIs
def load_abi(file_path):
    try:
        with open(file_path, "r") as f:
            abi = json.load(f)
        # Check if ABI is a list
        if isinstance(abi, list):
            return abi
        else:
            print(f"Error: ABI in {file_path} is not a list.")
            return None
    except Exception as e:
        print(f"Error loading ABI from {file_path}: {e}")
        return None

# Load the Uniswap Router ABI
router_abi = load_abi("abis/UniswapV3Router.json")
if router_abi is None:
    print("Failed to load UniswapV3Router ABI. Exiting.")
    exit()

# Load the ERC20 ABI
erc20_abi = load_abi("abis/ERC20.json")
if erc20_abi is None:
    print("Failed to load ERC20 ABI. Exiting.")
    exit()

# Constants
START_CAPITAL = Decimal("50")
TP_PERCENT = Decimal("0.25")
SL_PERCENT = Decimal("0.05")
DAILY_TARGET = Decimal("0.20")
capital_file = "capital_live.json"
base_symbol = "USDT"
base_token = web3.toChecksumAddress("0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9")  # USDT on Arbitrum
router_contract = web3.eth.contract(address=router, abi=router_abi)

# Tokens
symbol_to_address = {
    "ARB": "0x912ce59144191c1204e64559fe8253a0e49e6548",
    "MAGIC": "0x539bde0d7dbd336b79148aa742883198bbf60342",
    "GMX": "0xfc5a1a6eb076a2c7ad06ed22c90d7e710e35ad0a"
}

# Load or initialize capital
if os.path.exists(capital_file):
    with open(capital_file, "r") as f:
        capital = Decimal(json.load(f)["capital"])
else:
    capital = START_CAPITAL

# Get token price from CoinGecko
def get_token_price(symbol):
    ids = {"ARB": "arbitrum", "MAGIC": "magic", "GMX": "gmx", "USDT": "tether"}
    token_id = ids[symbol]
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={token_id}&vs_currencies=usd"
    response = requests.get(url).json()
    return Decimal(str(response[token_id]["usd"]))

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
    nonce = web3.eth.get_transaction_count(wallet)
    txn = contract.functions.approve(spender, amount).build_transaction({
        'from': wallet,
        'nonce': nonce,
        'gas': 200000,
        'gasPrice': web3.eth.gas_price
    })
    signed = web3.eth.account.sign_transaction(txn, private_key=PRIVATE_KEY)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    web3.eth.wait_for_transaction_receipt(tx_hash)

# Execute real swap
def execute_trade(token_symbol, amount_usdt):
    token = web3.toChecksumAddress(symbol_to_address[token_symbol])
    amount_in = int(amount_usdt * 1e6)  # USDT has 6 decimals

    # Approve USDT if needed
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
        'nonce': web3.eth.get_transaction_count(wallet),
        'gas': 400000,
        'gasPrice': web3.eth.gas_price,
        'value': 0
    })
    signed_tx = web3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    return receipt

# Trade loop
def run_daily_trade(capital):
    day = datetime.now().strftime('%Y-%m-%d')
    earned_today = Decimal("0")
    trades_today = 0
    goal = capital * DAILY_TARGET

    while earned_today < goal and trades_today < 5:
        symbol = random.choice(list(symbol_to_address.keys()))
        trade_amt = min(capital * Decimal("0.2"), capital)
        entry_price = get_token_price(symbol)
        tp = entry_price * (1 + TP_PERCENT)
        sl = entry_price * (1 - SL_PERCENT)

        send_telegram_alert(f"🚀 Buying {symbol} with ${trade_amt:.2f} USDT at ${entry_price:.2f}")
        receipt = execute_trade(symbol, trade_amt)
        time.sleep(1)

        # Simulate result
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
    capital = run_daily_trade(capital)

    # Save capital
    with open(capital_file, "w") as f:
        json.dump({"capital": str(capital)}, f)

    print("\n✅ Done")
