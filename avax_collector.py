"""
avax_collector.py — Syndicate Avax C-Chain Collector

Prikuplja nove ERC-20 token deploymente na Avax C-Chain via SnowTrace API.
Ista CIA logika kao dataset_collector_v4.py ali za EVM/Avax.

Instalacija: pip install requests
Pokretanje:  python avax_collector.py
"""

import json
import logging
import time
import statistics
from pathlib import Path
from collections import defaultdict, Counter
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SNOWTRACE_API_KEY = "rs_c563928dc3b9c46d70d8698c"  # <-- upisi novi key sa routescan.io
SNOWTRACE_API     = "https://api.routescan.io/v2/network/mainnet/evm/43114/etherscan/api"
AVAX_RPC          = "https://api.avax.network/ext/bc/C/rpc"

OUTPUT_FILE       = "syndicate_train_avax.jsonl"
POLL_INTERVAL     = 60        # sekunde između pollinga
RPC_TIMEOUT       = 15
API_TIMEOUT       = 20
RATE_LIMIT_DELAY  = 1.0

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AVAX] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Creator History Tracker
# ---------------------------------------------------------------------------
creator_history = defaultdict(lambda: {"total": 0, "danger": 0, "warn": 0, "good": 0})
seen_contracts = set()  # da ne procesiramo isti token dva puta

def update_creator_history(creator: str, label: str):
    if not creator:
        return
    creator_history[creator]["total"] += 1
    if label == "DANGER":
        creator_history[creator]["danger"] += 1
    elif label == "WARN":
        creator_history[creator]["warn"] += 1
    elif label == "GOOD":
        creator_history[creator]["good"] += 1

def get_creator_stats(creator: str) -> dict:
    if not creator or creator not in creator_history:
        return {"total": 0, "danger": 0, "rug_rate": 0.0}
    stats = creator_history[creator]
    total = stats["total"]
    danger = stats["danger"]
    rug_rate = (danger / total * 100) if total > 0 else 0.0
    return {
        "total": total,
        "danger": danger,
        "rug_rate": round(rug_rate, 1)
    }

# ---------------------------------------------------------------------------
# SnowTrace API helpers
# ---------------------------------------------------------------------------

def snowtrace_get(params: dict) -> Optional[dict]:
    """Generic Routescan/SnowTrace API poziv."""
    params["apikey"] = SNOWTRACE_API_KEY
    headers = {
        "User-Agent": "SyndicateCollector/4.0",
        "Accept": "application/json",
    }
    try:
        resp = requests.get(SNOWTRACE_API, params=params, headers=headers, timeout=API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            status = data.get("status")
            result = data.get("result")
            if status == "1":
                return result
            # "0" sa "No transactions found" je ok — vrati praznu listu
            msg = data.get("message", "")
            if "No transactions" in msg or "No records" in msg:
                return []
            if result is not None:
                return result
            log.debug("API status 0: %s", data.get("message", ""))
            return []
        return []
    except requests.RequestException as e:
        log.warning("SnowTrace API greška: %s", e)
        return None


def get_new_token_deployments(from_block: int, to_block: int = 0) -> list:
    """
    Dohvata nove contract deploymente od zadanog bloka.
    Koristi getcontractcreation ili txlist sa praznim 'to' poljem.
    """
    # Metoda 1: getcontractcreation nije podrzan za range — koristimo txlist
    # i filtriramo transakcije gdje je 'to' prazan (= contract creation)
    params = {
        "module": "account",
        "action": "txlist",
        "startblock": from_block,
        "endblock": 99999999,
        "sort": "asc",
        "page": 1,
        "offset": 200,
    }

    # Routescan ne podrzava txlist bez adrese — koristimo interno RPC
    # da dobijemo contract creation txs direktno
    contracts = {}

    # Skeniraj sve blokove u opsegu via batch RPC pozivi
    try:
        # Batch poziv za sve blokove odjednom
        batch = []
        for i, block_num in enumerate(range(from_block, to_block + 1)):
            batch.append({
                "jsonrpc": "2.0",
                "id": i,
                "method": "eth_getBlockByNumber",
                "params": [hex(block_num), True]
            })

        # Pošalji batch u komadima od 20
        all_blocks = []
        for chunk_start in range(0, len(batch), 20):
            chunk = batch[chunk_start:chunk_start + 20]
            resp = requests.post(AVAX_RPC, json=chunk, timeout=30)
            results = resp.json()
            if isinstance(results, list):
                all_blocks.extend(results)
            time.sleep(0.2)

        for item in all_blocks:
            block_data = item.get("result")
            if not block_data:
                continue

            txs = block_data.get("transactions", [])
            ts = int(block_data.get("timestamp", "0x0"), 16)
            block_num = int(block_data.get("number", "0x0"), 16)

            for tx in txs:
                # Contract creation: 'to' je null/None
                if tx.get("to") is None or tx.get("to") == "":
                    deployer = tx.get("from", "").lower()
                    tx_hash = tx.get("hash", "")

                    # contractAddress je u receiptu — dohvati ga
                    receipt_payload = {
                        "jsonrpc": "2.0", "id": 1,
                        "method": "eth_getTransactionReceipt",
                        "params": [tx_hash]
                    }
                    rec_resp = requests.post(AVAX_RPC, json=receipt_payload, timeout=RPC_TIMEOUT)
                    receipt = rec_resp.json().get("result")

                    if receipt:
                        contract_addr = receipt.get("contractAddress", "")
                        if contract_addr and contract_addr.lower() not in contracts:
                            contracts[contract_addr.lower()] = {
                                "address": contract_addr.lower(),
                                "name": "Unknown",
                                "symbol": "",
                                "decimals": "18",
                                "deployer": deployer,
                                "block": block_num,
                                "timestamp": ts,
                            }
                            log.info("  Nova contract: %s od %s", contract_addr[:12], deployer[:12])
                    time.sleep(0.1)

    except Exception as e:
        log.warning("RPC block scan greška: %s", e)

    return list(contracts.values())


def get_latest_block() -> int:
    """Dohvata trenutni blok na Avax C-Chain — uvijek via direktni RPC."""
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}
        resp = requests.post(AVAX_RPC, json=payload, timeout=RPC_TIMEOUT)
        data = resp.json()
        block_hex = data.get("result", "0x0")
        block = int(block_hex, 16)
        return block
    except Exception as e:
        log.error("Ne mogu dohvatiti blok via RPC: %s", e)
        return 0


def get_contract_transactions(address: str, limit: int = 50) -> list:
    """Dohvata transakcije za kontrakt."""
    params = {
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": 0,
        "endblock": 99999999,
        "page": 1,
        "offset": limit,
        "sort": "asc",
    }
    result = snowtrace_get(params)
    return result if result else []


def get_token_transfers(address: str, limit: int = 50) -> list:
    """Dohvata token transfere za kontrakt."""
    params = {
        "module": "account",
        "action": "tokentx",
        "contractaddress": address,
        "startblock": 0,
        "endblock": 99999999,
        "page": 1,
        "offset": limit,
        "sort": "asc",
    }
    result = snowtrace_get(params)
    return result if result else []


def get_account_transactions(address: str, limit: int = 100) -> list:
    """Dohvata transakcije za wallet adresu."""
    params = {
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": 0,
        "endblock": 99999999,
        "page": 1,
        "offset": limit,
        "sort": "asc",
    }
    result = snowtrace_get(params)
    return result if result else []


def get_avax_balance(address: str) -> float:
    """Dohvata AVAX balans adrese."""
    params = {
        "module": "account",
        "action": "balance",
        "address": address,
        "tag": "latest",
    }
    result = snowtrace_get(params)
    if result:
        try:
            return int(result) / 1e18  # wei -> AVAX
        except (ValueError, TypeError):
            pass
    return 0.0


def get_token_holders(address: str) -> list:
    """Dohvata top holdere tokena."""
    params = {
        "module": "token",
        "action": "tokenholderlist",
        "contractaddress": address,
        "page": 1,
        "offset": 10,
    }
    result = snowtrace_get(params)
    return result if result else []

# ---------------------------------------------------------------------------
# CIA Analitika — Avax
# ---------------------------------------------------------------------------

def get_account_age_days_avax(address: str) -> float:
    """Procjenjuje starost Avax walleta u danima."""
    txs = get_account_transactions(address, limit=1000)
    if not txs:
        return 0.0
    # Prva transakcija = najstarija (sort=asc)
    oldest_ts = int(txs[0].get("timeStamp", 0))
    if not oldest_ts:
        return 0.0
    age_seconds = time.time() - oldest_ts
    return round(age_seconds / 86400, 1)


def trace_funding_origin_avax(deployer: str, depth: int = 3) -> dict:
    """
    Prati izvor finansiranja deployer walleta 3 nivoa unazad.
    """
    result = {
        "master_wallet": "",
        "hop_count": 0,
        "is_fresh_wallet": False,
        "funding_chain": [deployer],
        "all_fresh": False,
        "wallet_ages_days": [],
    }

    current = deployer
    chain_trace = [deployer]

    for hop in range(depth):
        txs = get_account_transactions(current, limit=10)
        if not txs:
            result["is_fresh_wallet"] = True
            break

        # Najstarija tx (sort=asc) — prva u listi
        first_tx = txs[0]
        sender = first_tx.get("from", "").lower()

        if sender and sender != current.lower() and sender != "0x0000000000000000000000000000000000000000":
            chain_trace.append(sender)
            current = sender
            result["hop_count"] = hop + 1
        else:
            break

        time.sleep(0.3)

    result["funding_chain"] = chain_trace
    result["master_wallet"] = chain_trace[-1] if len(chain_trace) > 1 else ""

    # Provjeri starost svih walleta u lancu
    ages = []
    for addr in chain_trace[1:]:
        age = get_account_age_days_avax(addr)
        ages.append(age)
        time.sleep(0.3)

    result["wallet_ages_days"] = ages
    result["all_fresh"] = all(a < 7 for a in ages) if ages else False

    return result


def get_deployment_latency_avax(contract_address: str, deploy_timestamp: int) -> dict:
    """
    Mjeri vreme između deploymenta i prve kupovine tokena.
    """
    result = {
        "deploy_time": deploy_timestamp,
        "first_buy_time": 0,
        "latency_ms": -1,
        "is_sniped": False,
    }

    transfers = get_token_transfers(contract_address, limit=10)
    if not transfers or len(transfers) < 2:
        return result

    # Prva transfer posle deploy-a
    for tx in transfers:
        ts = int(tx.get("timeStamp", 0))
        if ts > deploy_timestamp:
            result["first_buy_time"] = ts
            latency_ms = (ts - deploy_timestamp) * 1000
            result["latency_ms"] = int(latency_ms)
            result["is_sniped"] = latency_ms < 3000
            break

    return result


def analyze_transaction_entropy_avax(contract_address: str) -> dict:
    """
    Analizira bot potpis — repetitivni AVAX iznosi u transakcijama.
    """
    result = {
        "total_txs": 0,
        "unique_amounts": 0,
        "entropy_score": 1.0,
        "is_bot_pattern": False,
        "dominant_amount": 0,
        "dominant_amount_pct": 0.0,
    }

    transfers = get_token_transfers(contract_address, limit=30)
    if not transfers:
        return result

    # Grupiši po "from" adresi i broju tokena
    amounts = []
    for tx in transfers:
        value = tx.get("value", "0")
        try:
            decimals = int(tx.get("tokenDecimal", 18))
            amount = int(value) / (10 ** decimals)
            if amount > 0:
                amounts.append(round(amount, 2))
        except (ValueError, TypeError):
            continue

    if not amounts:
        return result

    result["total_txs"] = len(amounts)
    counter = Counter(amounts)
    result["unique_amounts"] = len(counter)

    most_common_amount, most_common_count = counter.most_common(1)[0]
    dominant_pct = most_common_count / len(amounts)

    result["dominant_amount"] = most_common_amount
    result["dominant_amount_pct"] = round(dominant_pct * 100, 1)
    result["entropy_score"] = round(1.0 - dominant_pct, 2)
    result["is_bot_pattern"] = dominant_pct > 0.6

    return result


def detect_wash_pattern_avax(contract_address: str, deployer: str, deploy_timestamp: int) -> dict:
    """
    Detektuje wash trading pattern na Avax:
    Deploy -> Dev instant sell -> Supply wallet buy
    """
    result = {
        "wash_detected": False,
        "dev_sold_fast": False,
        "dev_sell_latency_s": -1,
        "linker_wallets_connected": False,
    }

    if not deployer:
        return result

    # Dohvati transfere tokena
    transfers = get_token_transfers(contract_address, limit=20)
    if not transfers:
        return result

    # Traži izlazni transfer od deployer adrese u prvih 60s
    for tx in transfers:
        tx_from = tx.get("from", "").lower()
        ts = int(tx.get("timeStamp", 0))

        if tx_from == deployer.lower():
            latency = ts - deploy_timestamp
            if 0 < latency < 60:
                result["dev_sold_fast"] = True
                result["dev_sell_latency_s"] = latency
                break

    # Provjeri jesu li deployer i veliki kupac povezani
    # Ako deployer ima < 5 tx ukupno = jednonamjenski wallet (Syndicate)
    deployer_txs = get_account_transactions(deployer, limit=10)
    result["linker_wallets_connected"] = len(deployer_txs) < 5

    result["wash_detected"] = result["dev_sold_fast"] and result["linker_wallets_connected"]

    return result


def analyze_holder_cluster_avax(contract_address: str) -> dict:
    """
    Analizira starost prvih 10 holdera.
    """
    result = {
        "avg_age_days": 0.0,
        "new_wallets_count": 0,
        "total_checked": 0,
        "is_bot_farm": False,
    }

    holders = get_token_holders(contract_address)
    if not holders:
        # Fallback: uzmi adrese iz prvih transfera
        transfers = get_token_transfers(contract_address, limit=20)
        holder_addrs = list({tx.get("to", "") for tx in transfers if tx.get("to")})[:10]
    else:
        holder_addrs = [h.get("TokenHolderAddress", "") for h in holders[:10]]

    if not holder_addrs:
        return result

    ages = []
    new_count = 0

    for addr in holder_addrs[:10]:
        if not addr:
            continue
        age = get_account_age_days_avax(addr)
        ages.append(age)
        if age < 7:
            new_count += 1
        time.sleep(0.3)

    if not ages:
        return result

    result["total_checked"] = len(ages)
    result["avg_age_days"] = round(statistics.mean(ages), 1)
    result["new_wallets_count"] = new_count
    result["is_bot_farm"] = (new_count / len(ages) > 0.7)

    return result


def get_token_info_avax(contract_address: str) -> dict:
    """Dohvata osnovne info o tokenu."""
    params = {
        "module": "token",
        "action": "tokeninfo",
        "contractaddress": contract_address,
    }
    result = snowtrace_get(params)
    if result and isinstance(result, list) and len(result) > 0:
        info = result[0]
        return {
            "name": info.get("tokenName", "Unknown"),
            "symbol": info.get("symbol", ""),
            "total_supply": info.get("totalSupply", 0),
            "decimals": info.get("divisor", 18),
            "holders_count": int(info.get("holdersCount", 0)),
        }
    return {"name": "Unknown", "symbol": "", "total_supply": 0, "decimals": 18, "holders_count": 0}


def run_cia_analysis_avax(contract_address: str, deployer: str, deploy_timestamp: int) -> dict:
    """Pokreće sve CIA analize za Avax token."""
    log.info("  [CIA] Pokrenuta analiza za %s", contract_address[:12])

    intel = {}

    log.info("  [CIA] Tracing funding origin...")
    intel["funding"] = trace_funding_origin_avax(deployer, depth=3) if deployer else {}
    time.sleep(0.5)

    log.info("  [CIA] Mjerim deployment latency...")
    intel["latency"] = get_deployment_latency_avax(contract_address, deploy_timestamp)
    time.sleep(0.5)

    log.info("  [CIA] Analiziram transaction entropy...")
    intel["entropy"] = analyze_transaction_entropy_avax(contract_address)
    time.sleep(0.5)

    log.info("  [CIA] Detektujem wash pattern...")
    intel["wash"] = detect_wash_pattern_avax(contract_address, deployer, deploy_timestamp)
    time.sleep(0.5)

    log.info("  [CIA] Analiziram holder cluster...")
    intel["cluster"] = analyze_holder_cluster_avax(contract_address)

    return intel

# ---------------------------------------------------------------------------
# Label logika za Avax (bez RugCheck — koristimo CIA signale)
# ---------------------------------------------------------------------------

def classify_avax_token(token_info: dict, cia_intel: dict, deployer_balance: float) -> tuple[str, list]:
    """
    Klasifikuje Avax token bez RugCheck.
    Vraća (label, risk_flags).
    """
    flags = []

    funding = cia_intel.get("funding", {})
    latency = cia_intel.get("latency", {})
    entropy = cia_intel.get("entropy", {})
    wash = cia_intel.get("wash", {})
    cluster = cia_intel.get("cluster", {})

    if funding.get("all_fresh"):
        flags.append("Fresh funding chain")
    if funding.get("is_fresh_wallet"):
        flags.append("Deployer is fresh wallet")
    if latency.get("is_sniped"):
        flags.append(f"Sniped in {latency.get('latency_ms')}ms")
    if entropy.get("is_bot_pattern"):
        flags.append(f"Bot transactions ({entropy.get('dominant_amount_pct')}% same amount)")
    if wash.get("wash_detected"):
        flags.append("Wash trading pattern detected")
    if wash.get("dev_sold_fast"):
        flags.append(f"Dev sold in {wash.get('dev_sell_latency_s')}s")
    if wash.get("linker_wallets_connected"):
        flags.append("Deployer is single-use wallet")
    if cluster.get("is_bot_farm"):
        flags.append(f"Bot farm holders ({cluster.get('new_wallets_count')}/{cluster.get('total_checked')} new)")
    if deployer_balance < 0.1:
        flags.append("Deployer has near-zero AVAX balance")
    if token_info.get("holders_count", 0) < 10:
        flags.append("Less than 10 holders")

    danger_count = sum([
        wash.get("wash_detected", False),
        cluster.get("is_bot_farm", False),
        funding.get("all_fresh", False),
        entropy.get("is_bot_pattern", False),
        wash.get("linker_wallets_connected", False),
    ])

    if danger_count >= 4:
        return "DANGER", flags
    elif danger_count >= 2 or len(flags) >= 3:
        return "WARN", flags
    else:
        return "GOOD", flags

# ---------------------------------------------------------------------------
# Dataset writer
# ---------------------------------------------------------------------------

def build_training_record_avax(
    contract_address: str,
    token_info: dict,
    deployer: str,
    deploy_timestamp: int,
    creator_stats: dict,
    cia_intel: dict,
    label: str,
    risk_flags: list,
) -> dict:

    funding = cia_intel.get("funding", {})
    latency = cia_intel.get("latency", {})
    entropy = cia_intel.get("entropy", {})
    wash = cia_intel.get("wash", {})
    cluster = cia_intel.get("cluster", {})

    if creator_stats["total"] == 0:
        creator_risk = "NEW - no previous tokens"
    elif creator_stats["rug_rate"] >= 80:
        creator_risk = f"HIGH RISK - {creator_stats['rug_rate']}% rug rate ({creator_stats['danger']}/{creator_stats['total']} tokens)"
    elif creator_stats["rug_rate"] >= 40:
        creator_risk = f"MODERATE RISK - {creator_stats['rug_rate']}% rug rate"
    else:
        creator_risk = f"LOW RISK - {creator_stats['rug_rate']}% rug rate"

    input_text = f"""Token: {token_info.get('name', 'Unknown')} ({token_info.get('symbol', '')})
Chain: AVAX (C-Chain)
Contract: {contract_address}
Explorer: https://snowtrace.io/address/{contract_address}
Deployer: {deployer[:20] + '...' if deployer else 'Unknown'}
Total Supply: {token_info.get('total_supply', 'N/A')}
Holders: {token_info.get('holders_count', 0)}
Risk Flags: {', '.join(risk_flags) or 'None detected'}
Deployer History: {creator_risk}
--- CIA INTEL ---
Funding Origin: {f"Master wallet traced {funding.get('hop_count', 0)} hops back"} | All fresh: {funding.get('all_fresh', False)}
Deployment Latency: {latency.get('latency_ms', -1)}ms | Sniped: {latency.get('is_sniped', False)}
Transaction Entropy: {entropy.get('entropy_score', 1.0)} | Bot pattern: {entropy.get('is_bot_pattern', False)} | Dominant: {entropy.get('dominant_amount', 0)} tokens ({entropy.get('dominant_amount_pct', 0)}%)
Wash Pattern: {wash.get('wash_detected', False)} | Dev sold in {wash.get('dev_sell_latency_s', -1)}s | Single-use deployer: {wash.get('linker_wallets_connected', False)}
Holder Cluster: avg age {cluster.get('avg_age_days', 0)} days | New wallets: {cluster.get('new_wallets_count', 0)}/{cluster.get('total_checked', 0)} | Bot farm: {cluster.get('is_bot_farm', False)}"""

    cia_flags_summary = f" CIA flags: {', '.join(risk_flags)}." if risk_flags else ""

    if label == "DANGER":
        output = f"DANGER - High risk AVAX token. Flags: {', '.join(risk_flags[:3])}. Deployer rug rate: {creator_stats['rug_rate']}%.{cia_flags_summary}"
    elif label == "WARN":
        output = f"WARN - Moderate risk AVAX token. Flags: {', '.join(risk_flags[:3])}.{cia_flags_summary}"
    else:
        output = f"GOOD - Low risk AVAX token. No major red flags detected. Deployer has clean history."

    return {
        "instruction": "Analyze this Avalanche (AVAX) token and classify its risk level as DANGER, WARN, or GOOD.",
        "input": input_text,
        "output": output,
        "label": label,
        "chain": "AVAX",
        "creator": deployer,
        "creator_rug_rate": creator_stats["rug_rate"],
        "cia_funding_hops": funding.get("hop_count", 0),
        "cia_all_fresh_wallets": funding.get("all_fresh", False),
        "cia_deployment_latency_ms": latency.get("latency_ms", -1),
        "cia_sniped": latency.get("is_sniped", False),
        "cia_entropy_score": entropy.get("entropy_score", 1.0),
        "cia_bot_pattern": entropy.get("is_bot_pattern", False),
        "cia_wash_detected": wash.get("wash_detected", False),
        "cia_bot_farm": cluster.get("is_bot_farm", False),
        "cia_avg_holder_age_days": cluster.get("avg_age_days", 0.0),
    }


def append_to_dataset(record: dict, output_path: Path) -> None:
    try:
        with output_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        log.info("  -> Snimljeno [%s][AVAX] Deployer rug rate: %s%%  Ukupno: %d",
                 record["label"],
                 record.get("creator_rug_rate", "N/A"),
                 count_lines(output_path))
    except OSError as e:
        log.error("Nije moguće zapisati: %s", e)


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open("rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0

# ---------------------------------------------------------------------------
# Token processing
# ---------------------------------------------------------------------------

def process_token_avax(token_data: dict, output_path: Path) -> None:
    contract = token_data.get("address", "").lower()
    if not contract or contract in seen_contracts:
        return

    seen_contracts.add(contract)
    deployer = token_data.get("deployer", "")
    deploy_timestamp = token_data.get("timestamp", int(time.time()))
    name = token_data.get("name", "Unknown")
    symbol = token_data.get("symbol", "")

    log.info("Novi AVAX token: %s (%s) | %s", name, symbol, contract[:12])

    time.sleep(RATE_LIMIT_DELAY)

    # Dohvati detaljne info
    token_info = get_token_info_avax(contract)
    if token_info.get("name") == "Unknown" and name != "Unknown":
        token_info["name"] = name
        token_info["symbol"] = symbol

    # Deployer balans
    deployer_balance = get_avax_balance(deployer) if deployer else 0.0
    log.info("  Deployer balance: %.4f AVAX", deployer_balance)

    # Creator history
    creator_stats = get_creator_stats(deployer)

    # CIA analiza
    cia_intel = run_cia_analysis_avax(contract, deployer, deploy_timestamp)

    # Klasifikacija
    label, risk_flags = classify_avax_token(token_info, cia_intel, deployer_balance)

    # Zapis
    record = build_training_record_avax(
        contract, token_info, deployer, deploy_timestamp,
        creator_stats, cia_intel, label, risk_flags
    )
    append_to_dataset(record, output_path)
    update_creator_history(deployer, label)

# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------

def poll_loop(output_path: Path) -> None:
    """
    Glavni polling loop.
    Svake POLL_INTERVAL sekundi dohvata nove deploymente.
    """
    log.info("=" * 55)
    log.info("  Syndicate Avax Collector — SnowTrace Polling")
    log.info("  Interval     : %ds", POLL_INTERVAL)
    log.info("  Izlazni fajl : %s", output_path.absolute())
    log.info("  Prethodni zapisi: %d", count_lines(output_path))
    log.info("=" * 55)

    # Dohvati trenutni blok kao startnu tačku
    current_block = get_latest_block()
    if not current_block:
        log.error("Ne mogu dohvatiti trenutni blok. Provjeri API key.")
        return

    log.info("Start blok: %d", current_block)

    while True:
        try:
            log.info("Polling od bloka %d...", current_block)

            new_block = get_latest_block()
            log.info("Trenutni blok: %d (prethodni: %d, diff: %d)", new_block, current_block, new_block - current_block)
            if not new_block or new_block <= current_block:
                log.info("Nema novih blokova. Čekam %ds...", POLL_INTERVAL)
                time.sleep(POLL_INTERVAL)
                continue

            # Dohvati nove token deploymente
            deployments = get_new_token_deployments(current_block, new_block)
            log.info("Nađeno %d novih tokena u blokovima %d-%d",
                     len(deployments), current_block, new_block)

            for token_data in deployments:
                try:
                    process_token_avax(token_data, output_path)
                    time.sleep(RATE_LIMIT_DELAY)
                except Exception as e:
                    log.error("Greška pri procesiranju tokena: %s", e)
                    continue

            current_block = new_block
            log.info("Čekam %ds do sljedećeg pollinga...", POLL_INTERVAL)
            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            raise
        except Exception as e:
            log.error("Poll loop greška: %s. Nastavljam za 30s...", e)
            time.sleep(30)

# ---------------------------------------------------------------------------
# CLI scan mode
# ---------------------------------------------------------------------------

def scan_single_avax(address: str) -> None:
    """Skenira jednu Avax adresu direktno."""
    log.info("Skeniram AVAX token: %s", address)

    token_info = get_token_info_avax(address)
    deployer_balance = 0.0

    # Pokušaj naći deployer iz prvih transakcija
    txs = get_contract_transactions(address, limit=5)
    deployer = ""
    deploy_timestamp = int(time.time())
    if txs:
        first_tx = txs[0]
        deployer = first_tx.get("from", "")
        deploy_timestamp = int(first_tx.get("timeStamp", time.time()))
        deployer_balance = get_avax_balance(deployer)

    cia_intel = run_cia_analysis_avax(address, deployer, deploy_timestamp)
    label, risk_flags = classify_avax_token(token_info, cia_intel, deployer_balance)

    print(f"\n{'='*55}")
    print(f"  SYNDICATE AVAX SCAN")
    print(f"  Contract : {address}")
    print(f"  Token    : {token_info.get('name')} ({token_info.get('symbol')})")
    print(f"  Deployer : {deployer[:20] + '...' if deployer else 'NOT FOUND'}")
    print(f"  Balance  : {deployer_balance:.4f} AVAX")
    print(f"  Holders  : {token_info.get('holders_count', 0)}")
    print(f"  Label    : {label}")
    print(f"  Flags    : {', '.join(risk_flags) or 'None'}")
    print(f"--- CIA INTEL ---")
    print(f"  Latency      : {cia_intel.get('latency', {}).get('latency_ms', -1)}ms | Sniped: {cia_intel.get('latency', {}).get('is_sniped', False)}")
    print(f"  Bot pattern  : {cia_intel.get('entropy', {}).get('is_bot_pattern', False)}")
    print(f"  Wash pattern : {cia_intel.get('wash', {}).get('wash_detected', False)}")
    print(f"  Dev sold fast: {cia_intel.get('wash', {}).get('dev_sold_fast', False)} ({cia_intel.get('wash', {}).get('dev_sell_latency_s', -1)}s)")
    print(f"  Bot farm     : {cia_intel.get('cluster', {}).get('is_bot_farm', False)}")
    print(f"  Avg age      : {cia_intel.get('cluster', {}).get('avg_age_days', 0)} days")
    print(f"  Funding hops : {cia_intel.get('funding', {}).get('hop_count', 0)}")
    print(f"  All fresh    : {cia_intel.get('funding', {}).get('all_fresh', False)}")
    print(f"  Master wallet: {cia_intel.get('funding', {}).get('master_wallet', 'N/A')[:20]}")
    print(f"{'='*55}\n")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import sys

    if len(sys.argv) > 1:
        scan_single_avax(sys.argv[1])
        return

    output_path = Path(OUTPUT_FILE)
    try:
        poll_loop(output_path)
    except KeyboardInterrupt:
        log.info("Zaustavljeno. Ukupno: %d zapisa", count_lines(output_path))
        log.info("Deployer tracking: %d unikatnih deployera", len(creator_history))


if __name__ == "__main__":
    main()
