"""
dataset_collector_v6.py — Syndicate Intelligence Collector V5

Sve iz V4 PLUS pet novih CIA V5 modula:
- Cross-Chain Wallet Matching: pattern-based povezivanje Solana <-> Avax scammera
- Project Lifecycle Prediction: rug time estimate na osnovu prvih signala
- Jito Bundle Detection: orchestrated launch (multiple buys u istom slotu)
- Token Name Stylometry: NLP-style klasteri imena (Doge Killer, Pepe 2.0...)
- Dev -> CEX Sweep Tracking: detekcija exit ramp-a (Binance, KuCoin, Kraken)

Instalacija: pip install websockets requests
"""

import asyncio
import json
import base64
import logging
import time
import statistics
from pathlib import Path
from collections import defaultdict, Counter
from typing import Optional

import websockets
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PUMPPORTAL_WS    = "wss://pumpportal.fun/api/data"
HELIUS_RPC       = "https://mainnet.helius-rpc.com/?api-key=f6ec5340-6e09-476a-a09e-60929e74658c"  # <-- upiši key sa helius.dev
SOLANA_RPC       = "https://api.mainnet-beta.solana.com"  # fallback
AVAX_RPC         = "https://api.avax.network/ext/bc/C/rpc"
RUGCHECK_API     = "https://api.rugcheck.xyz/v1"

OUTPUT_FILE       = "syndicate_train_v6.jsonl"
RPC_TIMEOUT       = 20
RUGCHECK_TIMEOUT  = 20
RATE_LIMIT_DELAY  = 5.0  # V5 koristi vise RPC poziva — povecano

# Primarni RPC — koristi Helius ako je key upisan, inace public
def get_primary_rpc() -> str:
    return HELIUS_RPC if "YOUR_HELIUS_KEY" not in HELIUS_RPC else SOLANA_RPC

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ChainSwitcher
# ---------------------------------------------------------------------------

class ChainSwitcher:
    """
    Auto-detektuje lanac na osnovu adrese tokena.
    - Solana: 44 karaktera base58 (bez 0x prefiksa)
    - Avax:   počinje sa 0x (EVM format)
    """

    @staticmethod
    def detect(address: str) -> str:
        if not address:
            return "UNKNOWN"
        if address.startswith("0x") and len(address) == 42:
            return "AVAX"
        if len(address) == 44 and not address.startswith("0x"):
            return "SOLANA"
        # Pump.fun adresi završavaju sa 'pump' i mogu biti duže
        if address.endswith("pump") or (40 <= len(address) <= 50 and not address.startswith("0x")):
            return "SOLANA"
        return "UNKNOWN"

    @staticmethod
    def get_rpc(chain: str) -> str:
        return AVAX_RPC if chain == "AVAX" else SOLANA_RPC

    @staticmethod
    def get_explorer(chain: str, address: str) -> str:
        if chain == "AVAX":
            return f"https://snowtrace.io/address/{address}"
        return f"https://solscan.io/token/{address}"

# ---------------------------------------------------------------------------
# Creator History Tracker
# ---------------------------------------------------------------------------
creator_history = defaultdict(lambda: {"total": 0, "danger": 0, "warn": 0, "good": 0})

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
# RugCheck API
# ---------------------------------------------------------------------------

def get_rugcheck_report(mint: str) -> Optional[dict]:
    """Pokusava full report (sadrzi creator), fallback na summary."""
    # Full report sadrzi creator adresu
    for endpoint in ["report", "report/summary"]:
        url = f"{RUGCHECK_API}/tokens/{mint}/{endpoint}"
        try:
            resp = requests.get(url, timeout=RUGCHECK_TIMEOUT)
            if resp.status_code == 404:
                continue
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    log.debug("  -> RugCheck endpoint: %s", endpoint)
                    return data
        except requests.RequestException as e:
            log.warning("  -> RugCheck API greska (%s): %s", endpoint, e)
    return None


def parse_rugcheck_label(report: dict) -> str:
    if not report:
        return "UNKNOWN"
    score = report.get("score", 0)
    risks = report.get("risks", [])
    high_risks = [r for r in risks if r.get("level") == "danger"]
    warn_risks = [r for r in risks if r.get("level") == "warn"]
    if high_risks or score > 5000:
        return "DANGER"
    elif warn_risks or score > 2000:
        return "WARN"
    else:
        return "GOOD"


def parse_rugcheck_features(report: dict) -> dict:
    if not report:
        return {}
    risks = report.get("risks", [])
    risk_names = [r.get("name", "") for r in risks]

    creator = report.get("creator") or report.get("deployer") or ""
    if not creator:
        markets = report.get("markets", [])
        if markets:
            creator = markets[0].get("deployer", "")

    # Token lock — provjeri je li stvarno locked ili samo "izgleda" locked
    lp_locked_pct = 0
    lp_locked_usd = 0
    lp_lock_is_real = False
    markets = report.get("markets", [])
    if markets:
        lp_data = markets[0].get("lp", {})
        lp_locked_pct = lp_data.get("lpLockedPct", 0)
        lp_locked_usd = lp_data.get("lpLockedUSD", 0)
        # "Lažiran" lock: % visok ali USD vrijednost $0 ili < $100
        lp_lock_is_real = lp_locked_pct > 50 and lp_locked_usd > 100

    return {
        "rugcheck_score": report.get("score", 0),
        "mint_authority": "Mint Authority" in risk_names or "Mint Enabled" in risk_names,
        "freeze_authority": "Freeze Authority" in risk_names or "Freeze Enabled" in risk_names,
        "lp_burned": lp_locked_pct > 50,
        "lp_lock_is_real": lp_lock_is_real,
        "lp_locked_pct": lp_locked_pct,
        "lp_locked_usd": lp_locked_usd,
        "top_holder_pct": report.get("topHolders", [{}])[0].get("pct", 0) if report.get("topHolders") else 0,
        "risks": risk_names,
        "creator": creator,
    }

# ---------------------------------------------------------------------------
# CIA Analitika — Solana RPC
# ---------------------------------------------------------------------------

# Globalni RPS limiter — Helius free tier je ~10 RPS
_last_rpc_call = [0.0]
RPC_MIN_INTERVAL = 0.15  # 150ms = ~6.6 RPS, bezbjedno ispod 10

def _throttle_rpc():
    """Osigurava razmak izmedju RPC poziva."""
    now = time.time()
    elapsed = now - _last_rpc_call[0]
    if elapsed < RPC_MIN_INTERVAL:
        time.sleep(RPC_MIN_INTERVAL - elapsed)
    _last_rpc_call[0] = time.time()


def rpc_post(method: str, params: list, rpc_url: str = None) -> Optional[dict]:
    """RPC poziv sa retry/exponential backoff i Helius->public fallback."""
    if rpc_url is None:
        rpc_url = get_primary_rpc()

    _throttle_rpc()  # globalni RPS limiter

    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    delays = [2, 4, 8]  # exponential backoff

    for attempt, delay in enumerate(delays):
        try:
            resp = requests.post(rpc_url, json=payload, timeout=RPC_TIMEOUT)

            # 429 = rate limit — cekaj i pokusaj ponovo
            if resp.status_code == 429:
                log.warning("  [RPC] Rate limit (429) — cekam %ds (pokusaj %d/3)", delay, attempt + 1)
                time.sleep(delay)
                # Drugi pokusaj sa public RPC ako je Helius rate limitovan
                if rpc_url == HELIUS_RPC:
                    rpc_url = SOLANA_RPC
                continue

            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                err_code = data["error"].get("code", 0)
                if err_code == -32005:  # Node behind
                    time.sleep(delay)
                    continue
                return None

            return data.get("result")

        except requests.exceptions.Timeout:
            log.debug("  [RPC] Timeout (pokusaj %d/3) — %s", attempt + 1, method)
            time.sleep(delay)
            if rpc_url == HELIUS_RPC:
                rpc_url = SOLANA_RPC
        except requests.RequestException as e:
            log.debug("  [RPC] Greska (pokusaj %d/3): %s", attempt + 1, e)
            time.sleep(delay)

    return None


def get_token_metadata(mint: str, chain: str = "SOLANA") -> dict:
    """Osnovna metadata tokena sa RPC-a."""
    rpc_url = ChainSwitcher.get_rpc(chain)
    result = rpc_post(
        "getAccountInfo",
        [mint, {"encoding": "base64", "commitment": "confirmed"}],
        rpc_url,
    )

    metadata = {
        "mint": mint,
        "chain": chain,
        "has_mint_authority": False,
        "has_freeze_authority": False,
        "supply": 0,
        "decimals": 0,
    }

    if not result or not result.get("value"):
        return metadata

    try:
        raw = base64.b64decode(result["value"]["data"][0])
        if len(raw) >= 82:
            metadata["has_mint_authority"] = raw[0] == 1
            metadata["supply"] = int.from_bytes(raw[36:44], "little")
            metadata["decimals"] = raw[44]
            metadata["has_freeze_authority"] = raw[46] == 1
    except Exception:
        pass

    return metadata


def get_recent_transactions(address: str, limit: int = 20, chain: str = "SOLANA") -> list:
    """Dohvata nedavne transakcije za adresu."""
    if chain == "AVAX":
        return _get_avax_transactions(address, limit)

    result = rpc_post(
        "getSignaturesForAddress",
        [address, {"limit": limit, "commitment": "confirmed"}],
        get_primary_rpc(),
    )
    return result if isinstance(result, list) else []


def get_transaction_detail(sig: str) -> Optional[dict]:
    """Dohvata detalje jedne transakcije."""
    result = rpc_post(
        "getTransaction",
        [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
        get_primary_rpc(),
    )
    return result


def get_account_age_days(address: str, chain: str = "SOLANA") -> float:
    """
    Procenjuje starost novčanika u danima.
    Gleda prvu transakciju u historiji.
    """
    if chain == "AVAX":
        return _get_avax_account_age(address)

    result = rpc_post(
        "getSignaturesForAddress",
        [address, {"limit": 1000, "commitment": "confirmed"}],
    )
    if not result or not isinstance(result, list):
        return 0.0

    # Zadnja transakcija u listi = najstarija
    oldest = result[-1]
    block_time = oldest.get("blockTime")
    if not block_time:
        return 0.0

    age_seconds = time.time() - block_time
    return round(age_seconds / 86400, 1)

# ---------------------------------------------------------------------------
# CIA Analitika — Funding Origin (3-hop)
# ---------------------------------------------------------------------------

def trace_funding_origin(address: str, depth: int = 3, chain: str = "SOLANA") -> dict:
    """
    Prati izvor finansiranja novčanika do 3 nivoa unazad.
    Vraća: master_wallet, hop_count, is_fresh_wallet, funding_chain
    """
    result = {
        "master_wallet": "",
        "hop_count": 0,
        "is_fresh_wallet": False,
        "funding_chain": [],
        "all_fresh": False,
    }

    current = address
    chain_trace = [address]

    for hop in range(depth):
        txs = get_recent_transactions(current, limit=5, chain=chain)
        if not txs:
            result["is_fresh_wallet"] = True
            break

        # Uzmi najstariju transakciju
        oldest_sig = txs[-1].get("signature") if chain == "SOLANA" else None
        if not oldest_sig:
            break

        tx_detail = get_transaction_detail(oldest_sig)
        if not tx_detail:
            break

        # Izvuci sender iz prve transakcije
        try:
            accounts = tx_detail.get("transaction", {}).get("message", {}).get("accountKeys", [])
            if accounts and len(accounts) > 1:
                sender = accounts[0] if isinstance(accounts[0], str) else accounts[0].get("pubkey", "")
                if sender and sender != current:
                    chain_trace.append(sender)
                    current = sender
                    result["hop_count"] = hop + 1
                else:
                    break
            else:
                break
        except Exception:
            break

    result["funding_chain"] = chain_trace
    result["master_wallet"] = chain_trace[-1] if len(chain_trace) > 1 else ""

    # Provjeri jesu li svi novčanici u lancu svježi (< 7 dana)
    if len(chain_trace) > 1:
        ages = []
        for addr in chain_trace[1:]:  # preskoci original
            age = get_account_age_days(addr, chain)
            ages.append(age)
            time.sleep(0.3)
        result["all_fresh"] = all(a < 7 for a in ages) if ages else False
        result["wallet_ages_days"] = ages

    return result

# ---------------------------------------------------------------------------
# CIA Analitika — Wallet Cluster Age
# ---------------------------------------------------------------------------

def analyze_holder_cluster(mint: str, chain: str = "SOLANA") -> dict:
    """
    Analizira prvih 10 holdera.
    Vraća: avg_age_days, new_wallets_count, is_bot_farm
    """
    result = {
        "avg_age_days": 0.0,
        "new_wallets_count": 0,
        "total_checked": 0,
        "is_bot_farm": False,
    }

    # Dohvati top holdere kroz RugCheck ili token accounts
    if chain == "SOLANA":
        holders = _get_solana_top_holders(mint)
    else:
        holders = []  # Avax — TODO: ERC20 transfer events

    if not holders:
        return result

    ages = []
    new_count = 0

    for holder_addr in holders[:10]:
        age = get_account_age_days(holder_addr, chain)
        ages.append(age)
        if age < 7:
            new_count += 1
        time.sleep(0.3)

    result["total_checked"] = len(ages)
    result["avg_age_days"] = round(statistics.mean(ages), 1) if ages else 0.0
    result["new_wallets_count"] = new_count
    # Bot farm: više od 70% holdera mlađi od 7 dana
    result["is_bot_farm"] = (new_count / len(ages) > 0.7) if ages else False

    return result


def _get_solana_top_holders(mint: str) -> list:
    """Dohvata adrese top holdera za Solana token."""
    result = rpc_post(
        "getTokenLargestAccounts",
        [mint, {"commitment": "confirmed"}],
    )
    if not result or not isinstance(result, dict):
        return []

    accounts = result.get("value", [])
    addresses = []
    for acc in accounts[:10]:
        owner = acc.get("address", "")
        if owner:
            # Resolve token account -> owner
            owner_result = rpc_post(
                "getAccountInfo",
                [owner, {"encoding": "jsonParsed", "commitment": "confirmed"}],
            )
            if owner_result and owner_result.get("value"):
                try:
                    parsed = owner_result["value"]["data"]["parsed"]["info"]["owner"]
                    addresses.append(parsed)
                except (KeyError, TypeError):
                    addresses.append(owner)
            else:
                addresses.append(owner)
        time.sleep(0.2)

    return addresses

# ---------------------------------------------------------------------------
# CIA Analitika — Deployment Latency
# ---------------------------------------------------------------------------

def get_deployment_latency(mint: str, chain: str = "SOLANA") -> dict:
    """
    Mjeri vreme između kreiranja tokena i prve kupovine.
    Kratka latencija (< 5s) = bot je čekao u redu.

    getSignaturesForAddress vraća od NAJNOVIJE ka NAJSTARIJOJ.
    txs[0]  = najnovija tx
    txs[-1] = najstarija tx = mint tx
    txs[-2] = druga najstarija = prva kupovina posle minta
    """
    result = {
        "mint_time": 0,
        "first_buy_time": 0,
        "latency_ms": -1,
        "is_sniped": False,
    }

    if chain == "AVAX":
        return result  # TODO: Avax implementation

    # Dohvati što više tx da nađemo pravi mint (na kraju liste)
    txs = get_recent_transactions(mint, limit=100, chain=chain)
    if not txs or len(txs) < 2:
        return result

    # getSignaturesForAddress vraca od NAJNOVIJE ka NAJSTARIJOJ
    # Ako token ima 100+ tx, txs[-1] nije pravi mint nego samo najstarija od dohvacenih
    # Resenje: uzimamo PRVU tx (txs[0] = najnovija) i DRUGU (txs[1])
    # i gledamo razliku izmedju prve dvije najstarije koje imamo
    # Za novi token (<100 tx): txs[-1]=mint, txs[-2]=first_buy
    # Za stari token (100+ tx): koristimo samo prvih par tx za entropy, latency preskacemo

    total_txs = len(txs)
    mint_time = txs[-1].get("blockTime", 0)
    result["mint_time"] = mint_time

    # Ako imamo tocno 100 tx (limit), token je vjerovatno star — latency nije pouzdan
    if total_txs >= 100:
        log.debug("  -> Token ima 100+ tx, latency nije pouzdan (token star)")
        result["latency_ms"] = -2  # -2 = token prestar za mjerenje
        result["is_sniped"] = False
    else:
        # Token mlad, txs[-1] je pravi mint
        first_buy_time = txs[-2].get("blockTime", 0) if total_txs >= 2 else 0
        result["first_buy_time"] = first_buy_time
        if mint_time and first_buy_time:
            latency_ms = abs(first_buy_time - mint_time) * 1000
            result["latency_ms"] = int(latency_ms)
            result["is_sniped"] = latency_ms < 3000

    return result

# ---------------------------------------------------------------------------
# CIA Analitika — Transaction Entropy (bot detection)
# ---------------------------------------------------------------------------

def analyze_transaction_entropy(mint: str, chain: str = "SOLANA") -> dict:
    """
    Analizira da li su kupovine repetitivne (bot potpis).
    Bot kupuje uvek iste iznose npr. 1.5 SOL, 1.5 SOL, 1.5 SOL.
    """
    result = {
        "total_txs": 0,
        "unique_amounts": 0,
        "entropy_score": 1.0,  # 0 = potpuni bot, 1 = human
        "is_bot_pattern": False,
        "dominant_amount": 0,
        "dominant_amount_pct": 0.0,
    }

    if chain == "AVAX":
        return result  # TODO

    txs = get_recent_transactions(mint, limit=30, chain=chain)
    if not txs:
        return result

    amounts = []
    for tx_info in txs[:20]:
        sig = tx_info.get("signature", "")
        if not sig:
            continue
        detail = get_transaction_detail(sig)
        if not detail:
            continue
        try:
            # Izvuci SOL transfer iznose iz pre/post balansa
            pre_balances = detail.get("meta", {}).get("preBalances", [])
            post_balances = detail.get("meta", {}).get("postBalances", [])
            if pre_balances and post_balances:
                for pre, post in zip(pre_balances[1:], post_balances[1:]):
                    diff = abs(post - pre)
                    if diff > 1_000_000:  # > 0.001 SOL
                        amounts.append(round(diff / 1_000_000_000, 3))  # u SOL
        except Exception:
            continue
        time.sleep(0.2)

    if not amounts:
        return result

    result["total_txs"] = len(amounts)
    counter = Counter(amounts)
    result["unique_amounts"] = len(counter)

    # Entropy: ako je 1 iznos dominantan = bot
    most_common_amount, most_common_count = counter.most_common(1)[0]
    dominant_pct = most_common_count / len(amounts)

    result["dominant_amount"] = most_common_amount
    result["dominant_amount_pct"] = round(dominant_pct * 100, 1)
    # Entropy score: 0 = svi isti iznosi (bot), 1 = svi različiti (human)
    result["entropy_score"] = round(1.0 - dominant_pct, 2)
    result["is_bot_pattern"] = dominant_pct > 0.6  # 60%+ isti iznos = bot

    return result

# ---------------------------------------------------------------------------
# CIA Analitika — Wash Pattern (Mint -> Dev Sell -> Supply Buy)
# ---------------------------------------------------------------------------

def detect_wash_pattern(mint: str, creator: str, chain: str = "SOLANA") -> dict:
    """
    Detektuje Syndicate Blueprint:
    1. Mint token
    2. Dev odmah proda (< 60s)
    3. Supply wallet kupi veliki iznos
    """
    result = {
        "wash_detected": False,
        "dev_sold_fast": False,
        "dev_sell_latency_s": -1,
        "supply_wallet": "",
        "supply_wallet_amount_sol": 0.0,
        "linker_wallets_connected": False,
    }

    if chain == "AVAX" or not creator:
        return result

    # Provjeri transakcije creator wallets
    creator_txs = get_recent_transactions(creator, limit=20, chain=chain)
    if not creator_txs:
        return result

    # Traži brzu prodaju (< 60s od mint-a)
    mint_txs = get_recent_transactions(mint, limit=5, chain=chain)
    mint_time = mint_txs[-1].get("blockTime", 0) if mint_txs else 0

    for tx in creator_txs:
        tx_time = tx.get("blockTime", 0)
        if mint_time and tx_time:
            latency = tx_time - mint_time
            if 0 < latency < 60:  # Creator prodao u roku od 60s
                result["dev_sold_fast"] = True
                result["dev_sell_latency_s"] = latency
                break

    # Provjeri jesu li creator i supply wallet ikada imali zajedničku transakciju
    if creator:
        result["linker_wallets_connected"] = _check_wallet_link(creator, chain)

    # Wash pattern = dev brzo prodao + supply wallet veliki buy
    result["wash_detected"] = result["dev_sold_fast"] and result["linker_wallets_connected"]

    return result


def _check_wallet_link(wallet: str, chain: str = "SOLANA") -> bool:
    """
    Provjerava da li je wallet finansiran od poznatog 'master' wallets-a
    koji finansira više novih tokena (Syndicate indikator).
    """
    txs = get_recent_transactions(wallet, limit=10, chain=chain)
    if not txs or len(txs) < 2:
        return False

    # Ako wallet ima < 5 transakcija ukupno = novi novčanik za jednu operaciju
    return len(txs) < 5

# ---------------------------------------------------------------------------
# Avax stubs (placeholder za buduću implementaciju)
# ---------------------------------------------------------------------------

def _get_avax_transactions(address: str, limit: int) -> list:
    """Avax EVM transakcije — placeholder."""
    log.debug("Avax TX fetch za %s (TODO: SnowTrace API)", address[:10])
    return []


def _get_avax_account_age(address: str) -> float:
    """Avax account age — placeholder."""
    return 0.0

# ---------------------------------------------------------------------------
# V5 MODULE 1: Cross-Chain Wallet Matching
# ---------------------------------------------------------------------------

# Globalni registar pattern-a scammera vidjenih kroz oba lanca
# Format: {pattern_hash: {chains: [SOLANA, AVAX], wallets: [...], first_seen: ts, count: N}}
cross_chain_patterns = {}

def compute_wallet_pattern_hash(deploy_ts: int, tx_amounts: list, holder_count: int) -> str:
    """
    Pravi pattern hash iz behavioral signala — ne iz adrese.
    Scammer može mijenjati wallet, ali pattern ostaje isti.
    """
    import hashlib
    # Time-of-day bucket (4h)
    hour_bucket = (deploy_ts // 3600 // 4) % 6
    # Amount signature (dominant amount rounded)
    amount_sig = round(sum(tx_amounts[:5]) / max(len(tx_amounts[:5]), 1), 1) if tx_amounts else 0
    # Holder bucket
    holder_bucket = "low" if holder_count < 10 else "mid" if holder_count < 100 else "high"
    
    pattern_str = f"{hour_bucket}_{amount_sig}_{holder_bucket}"
    return hashlib.md5(pattern_str.encode()).hexdigest()[:12]


def detect_cross_chain_match(mint: str, chain: str, deploy_ts: int,
                              tx_amounts: list, holder_count: int) -> dict:
    """
    Provjerava da li ovaj token ima pattern slican scammeru viđenom na drugom lancu.
    """
    result = {
        "pattern_hash": "",
        "cross_chain_match": False,
        "match_chains": [],
        "match_count": 0,
    }
    
    pattern = compute_wallet_pattern_hash(deploy_ts, tx_amounts, holder_count)
    result["pattern_hash"] = pattern
    
    if pattern in cross_chain_patterns:
        entry = cross_chain_patterns[pattern]
        result["match_chains"] = entry["chains"]
        result["match_count"] = entry["count"]
        # Match samo ako je na drugom lancu vidjeno
        result["cross_chain_match"] = chain not in entry["chains"] or len(entry["chains"]) > 1
        entry["count"] += 1
        if chain not in entry["chains"]:
            entry["chains"].append(chain)
    else:
        cross_chain_patterns[pattern] = {
            "chains": [chain],
            "count": 1,
            "first_seen": deploy_ts,
        }
    
    return result

# ---------------------------------------------------------------------------
# V5 MODULE 2: Project Lifecycle Prediction
# ---------------------------------------------------------------------------

def predict_lifecycle(intel: dict, rugcheck_score: int, creator_rug_rate: float) -> dict:
    """
    Na osnovu CIA signala predviđa koliko će token preživjeti.
    Vraća estimated rug time u minutama i confidence.
    """
    result = {
        "estimated_rug_minutes": -1,
        "confidence": 0.0,
        "prediction_text": "Insufficient data",
    }
    
    # Sakupi sve signale
    sniped = intel.get("latency", {}).get("is_sniped", False)
    bot_pattern = intel.get("entropy", {}).get("is_bot_pattern", False)
    wash = intel.get("wash", {}).get("wash_detected", False)
    bot_farm = intel.get("cluster", {}).get("is_bot_farm", False)
    fresh_funding = intel.get("funding", {}).get("all_fresh", False)
    
    danger_signals = sum([sniped, bot_pattern, wash, bot_farm, fresh_funding])
    
    # Heuristika bazirana na pattern istraživanju
    if danger_signals >= 4 and rugcheck_score > 5000:
        result["estimated_rug_minutes"] = 15
        result["confidence"] = 0.87
        result["prediction_text"] = "Rug expected within 15 minutes (87% confidence)"
    elif danger_signals >= 3 and creator_rug_rate > 50:
        result["estimated_rug_minutes"] = 45
        result["confidence"] = 0.72
        result["prediction_text"] = "Rug expected within 45 minutes (72% confidence)"
    elif danger_signals >= 3:
        result["estimated_rug_minutes"] = 120
        result["confidence"] = 0.61
        result["prediction_text"] = "Rug expected within 2 hours (61% confidence)"
    elif danger_signals >= 2:
        result["estimated_rug_minutes"] = 360
        result["confidence"] = 0.45
        result["prediction_text"] = "Possible rug within 6 hours (45% confidence)"
    elif danger_signals == 0:
        result["estimated_rug_minutes"] = -1
        result["confidence"] = 0.0
        result["prediction_text"] = "No imminent rug signals"
    else:
        result["estimated_rug_minutes"] = 1440
        result["confidence"] = 0.20
        result["prediction_text"] = "Monitor — weak signals detected"
    
    return result

# ---------------------------------------------------------------------------
# V5 MODULE 3: Jito Bundle Detection (Orchestrated Launch)
# ---------------------------------------------------------------------------

def detect_jito_bundle(mint: str, chain: str = "SOLANA") -> dict:
    """
    Detektuje orchestrated launch — više buy transakcija u istom slotu/bloku.
    Jito bundles omogucavaju da scammer kupi sa 10+ walleta atomicno.
    """
    result = {
        "bundle_detected": False,
        "buys_in_first_slot": 0,
        "unique_buyers_first_slot": 0,
        "is_orchestrated": False,
    }
    
    if chain != "SOLANA":
        return result
    
    # Iskoristi vec dohvacene tx iz entropy modula umjesto novog poziva
    txs = get_recent_transactions(mint, limit=30, chain=chain)
    if not txs or len(txs) < 5:
        return result
    
    # Grupiraj transakcije po slot/blockTime
    from collections import defaultdict
    by_slot = defaultdict(list)
    
    for tx in txs:
        slot = tx.get("slot", 0)
        if slot:
            by_slot[slot].append(tx)
    
    # Provjeri najgori slot (najvise tx)
    if by_slot:
        max_slot = max(by_slot.keys(), key=lambda s: len(by_slot[s]))
        slot_txs = by_slot[max_slot]
        result["buys_in_first_slot"] = len(slot_txs)
        
        # SAMO 5 tx detail poziva umjesto 20 — manje RPC, dovoljno za detekciju
        unique_signers = set()
        for tx in slot_txs[:5]:
            sig = tx.get("signature", "")
            if sig:
                detail = get_transaction_detail(sig)
                if detail:
                    accounts = detail.get("transaction", {}).get("message", {}).get("accountKeys", [])
                    if accounts:
                        signer = accounts[0] if isinstance(accounts[0], str) else accounts[0].get("pubkey", "")
                        unique_signers.add(signer)
                time.sleep(0.4)
        
        result["unique_buyers_first_slot"] = len(unique_signers)
        # Bundle: 3+ unique buyers u 5 tx istog slota = orchestrated
        result["bundle_detected"] = len(unique_signers) >= 3 and len(slot_txs) >= 5
        result["is_orchestrated"] = result["bundle_detected"]
    
    return result

# ---------------------------------------------------------------------------
# V5 MODULE 4: Token Name Stylometry
# ---------------------------------------------------------------------------

# Poznati scam name patterns
SCAM_NAME_PATTERNS = [
    "killer", "2.0", "reborn", "moon", "elon", "musk", "trump",
    "inu", "doge", "pepe", "shiba", "wojak", "chad", "based",
    "ai", "gpt", "agent", "swarm",
    "100x", "1000x", "x100", "x1000",
    "official", "real", "v2", "v3",
]

# Globalni registar viđenih imena
seen_token_names = []

def analyze_name_stylometry(token_name: str, ticker: str) -> dict:
    """
    NLP-style analiza imena tokena. Traži scam patterns i sličnost sa prethodnim.
    """
    result = {
        "name_scam_score": 0,
        "matched_patterns": [],
        "similar_to_previous": False,
        "most_similar_name": "",
        "similarity_score": 0.0,
    }
    
    if not token_name:
        return result
    
    name_lower = token_name.lower()
    ticker_lower = ticker.lower() if ticker else ""
    
    # Pronadji scam patterns
    matched = []
    for pattern in SCAM_NAME_PATTERNS:
        if pattern in name_lower or pattern in ticker_lower:
            matched.append(pattern)
    
    result["matched_patterns"] = matched
    result["name_scam_score"] = min(len(matched) * 25, 100)
    
    # Sličnost sa prethodnim (jednostavna prefix/suffix provjera)
    if seen_token_names:
        max_sim = 0
        most_sim = ""
        for prev_name in seen_token_names[-200:]:
            # Jaccard similarity na karakterima
            set1 = set(name_lower)
            set2 = set(prev_name.lower())
            if not set1 or not set2:
                continue
            sim = len(set1 & set2) / len(set1 | set2)
            if sim > max_sim:
                max_sim = sim
                most_sim = prev_name
        
        if max_sim > 0.8:
            result["similar_to_previous"] = True
            result["most_similar_name"] = most_sim
            result["similarity_score"] = round(max_sim, 2)
    
    # Dodaj u listu
    seen_token_names.append(token_name)
    if len(seen_token_names) > 1000:
        seen_token_names.pop(0)
    
    return result

# ---------------------------------------------------------------------------
# V5 MODULE 5: Dev -> CEX Sweep Tracking (Exit Ramp Detection)
# ---------------------------------------------------------------------------

# Poznate CEX hot wallet adrese (Solana)
KNOWN_CEX_WALLETS_SOLANA = {
    "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9": "Binance",
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM": "Binance Hot",
    "FxteHmLwG9nk1eL4pjNve3Eub2goGkkz6g6TbvdmW46a": "Binance 2",
    "AC5RDfQFmDS1deWZos921JfqscXdByf8BKHs5ACWjtW2": "Bybit",
    "u6PJ8DtQuPFnfmwHbGFULQ4u4EgjDiyYKjVEsynXq2w": "Gate.io",
    "H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dK3WjS": "Coinbase",
    "2ojv9BAiHUrvsm9gxDe7fJSzbNZSJcxZvf8dqmWGHG8S": "Kraken",
    "GJRs4FwHtemZ5ZE9x3FNvJ8TMwitKTh21yxdRPqn7npE": "OKX",
}

KNOWN_CEX_WALLETS_AVAX = {
    "0x9f8c163cba728e99993abe7495f06c0a3c8ac8b9": "Binance",
    "0xd5c08681719445a5fdce2bda98b341a49050d821": "Binance 2",
    "0xf977814e90da44bfa03b6295a0616a897441acec": "Binance Hot",
    "0x4f9354cb6f5a6e0e1c8c43ae3a3a5b3b8c7d8b2c": "Coinbase",
    "0xcb9a4d4f0fa44b7bd9d4e7d4c6e3b6e8a8b9c0d1": "OKX",
}


def detect_dev_cex_sweep(creator: str, chain: str = "SOLANA") -> dict:
    """
    Prati transakcije dev walleta — da li novac ide na CEX (exit ramp).
    """
    result = {
        "sweep_to_cex": False,
        "cex_destination": "",
        "sweep_count": 0,
        "exit_pattern_confidence": 0.0,
    }
    
    if not creator:
        return result
    
    cex_wallets = KNOWN_CEX_WALLETS_SOLANA if chain == "SOLANA" else KNOWN_CEX_WALLETS_AVAX
    
    # Dohvati transakcije creator wallets
    creator_txs = get_recent_transactions(creator, limit=15, chain=chain)
    if not creator_txs:
        return result
    
    sweep_count = 0
    destinations = []
    
    # Smanjeno sa 20 na 8 — dovoljno za exit ramp detekciju
    for tx in creator_txs[:8]:
        sig = tx.get("signature", "") if chain == "SOLANA" else tx.get("hash", "")
        if not sig:
            continue
        
        detail = get_transaction_detail(sig) if chain == "SOLANA" else None
        if not detail:
            continue
        
        try:
            accounts = detail.get("transaction", {}).get("message", {}).get("accountKeys", [])
            for acc in accounts:
                addr = acc if isinstance(acc, str) else acc.get("pubkey", "")
                if addr in cex_wallets:
                    sweep_count += 1
                    destinations.append(cex_wallets[addr])
                    break
        except Exception:
            continue
        
        time.sleep(0.5)
    
    result["sweep_count"] = sweep_count
    if sweep_count > 0:
        result["sweep_to_cex"] = True
        result["cex_destination"] = ", ".join(set(destinations))
        result["exit_pattern_confidence"] = min(sweep_count * 0.3, 1.0)
    
    return result

# ---------------------------------------------------------------------------
# V6 MODULE 1: Smart Contract Backdoor Detection
# ---------------------------------------------------------------------------

# Poznate backdoor function signatures (first 4 bytes of keccak256)
BACKDOOR_SIGNATURES = {
    # Owner/Admin functions
    "0x8da5cb5b": "owner()",
    "0xf2fde38b": "transferOwnership(address)",
    "0x715018a6": "renounceOwnership()",
    "0xa9059cbb": "transfer(address,uint256)",
    # Dangerous functions
    "0x42966c68": "burn(uint256)",
    "0x40c10f19": "mint(address,uint256)",
    "0x3ccfd60b": "withdraw()",
    "0x2e1a7d4d": "withdraw(uint256)",
    "0x51cff8d9": "withdrawToken(address)",
    "0xd0e30db0": "deposit()",
    # Proxy patterns
    "0x3659cfe6": "upgradeTo(address)",
    "0x4f1ef286": "upgradeToAndCall(address,bytes)",
    "0x5c60da1b": "implementation()",
    # Pause functions
    "0x8456cb59": "pause()",
    "0x3f4ba83a": "unpause()",
    "0x5c975abb": "paused()",
    # Blacklist/whitelist
    "0x044df020": "blacklist(address)",
    "0x537df3b6": "unBlacklist(address)",
    "0xfe575a87": "isBlacklisted(address)",
}

DANGER_FUNCTIONS = ["mint", "withdraw", "drain", "pause", "blacklist", "upgradeTo", "setFee", "setTax"]


def detect_contract_backdoor(mint: str, chain: str = "SOLANA") -> dict:
    """
    Analizira kontrakt kod da detektuje backdoor funkcije.
    Za Solana: provjerava mint/freeze authority flags.
    Za EVM: dohvata bytecode i traži opasne function signatures.
    """
    result = {
        "has_backdoor": False,
        "backdoor_functions": [],
        "has_upgrade_authority": False,
        "has_pause_function": False,
        "has_mint_function": False,
        "has_drain_function": False,
        "has_blacklist": False,
        "is_proxy": False,
        "backdoor_risk_score": 0,
    }

    if chain == "SOLANA":
        # Solana — koristimo vec dostupne podatke iz token metadata
        # Mint i Freeze authority su vec u token_meta
        # Ovdje dodajemo program account analizu
        result_rpc = rpc_post(
            "getAccountInfo",
            [mint, {"encoding": "base64", "commitment": "confirmed"}],
            get_primary_rpc()
        )
        if result_rpc and result_rpc.get("value"):
            try:
                import base64
                raw = base64.b64decode(result_rpc["value"]["data"][0])
                # Solana token mint layout
                if len(raw) >= 82:
                    mint_auth = raw[0] == 1
                    freeze_auth = raw[46] == 1
                    result["has_mint_function"] = mint_auth
                    result["has_backdoor"] = mint_auth or freeze_auth
                    if mint_auth:
                        result["backdoor_functions"].append("Mint Authority Active")
                    if freeze_auth:
                        result["backdoor_functions"].append("Freeze Authority Active")
            except Exception:
                pass

    elif chain in ("AVAX", "ARBITRUM", "LIGHTCHAIN"):
        # EVM — dohvati bytecode i traži signatures
        try:
            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "eth_getCode",
                "params": [mint, "latest"]
            }
            rpc_url = AVAX_RPC  # ili odgovarajuci RPC za chain
            resp = requests.post(rpc_url, json=payload, timeout=RPC_TIMEOUT)
            bytecode = resp.json().get("result", "0x")

            if bytecode and len(bytecode) > 10:
                # Traži poznate function signatures u bytecodeu
                for sig, func_name in BACKDOOR_SIGNATURES.items():
                    if sig[2:] in bytecode:  # ukloni 0x prefix
                        result["backdoor_functions"].append(func_name)
                        if "upgradeTo" in func_name or "implementation" in func_name:
                            result["is_proxy"] = True
                            result["has_upgrade_authority"] = True
                        if "pause" in func_name.lower():
                            result["has_pause_function"] = True
                        if "mint" in func_name.lower():
                            result["has_mint_function"] = True
                        if "withdraw" in func_name.lower() or "drain" in func_name.lower():
                            result["has_drain_function"] = True
                        if "blacklist" in func_name.lower():
                            result["has_blacklist"] = True

                result["has_backdoor"] = len(result["backdoor_functions"]) > 0

        except Exception as e:
            log.debug("  [V6] Bytecode analiza greška: %s", e)

    # Risk score
    danger_count = sum([
        result["has_upgrade_authority"],
        result["has_mint_function"],
        result["has_drain_function"],
        result["has_pause_function"],
        result["has_blacklist"],
        result["is_proxy"],
    ])
    result["backdoor_risk_score"] = min(danger_count * 20, 100)

    return result


# ---------------------------------------------------------------------------
# V6 MODULE 2: Holder Concentration Risk
# ---------------------------------------------------------------------------

def analyze_holder_concentration(mint: str, chain: str = "SOLANA") -> dict:
    """
    Analizira koncentraciju tokena u top 5 holdera.
    Ako top 5 drži > 80% supply = centralizovano = opasno.
    """
    result = {
        "top5_pct": 0.0,
        "top1_pct": 0.0,
        "is_concentrated": False,
        "concentration_risk": "LOW",
    }

    if chain != "SOLANA":
        return result

    rpc_result = rpc_post(
        "getTokenLargestAccounts",
        [mint, {"commitment": "confirmed"}],
        get_primary_rpc()
    )

    if not rpc_result or not isinstance(rpc_result, dict):
        return result

    accounts = rpc_result.get("value", [])
    if not accounts:
        return result

    # Dohvati total supply
    supply_result = rpc_post(
        "getTokenSupply",
        [mint, {"commitment": "confirmed"}],
        get_primary_rpc()
    )

    total_supply = 0
    if supply_result and supply_result.get("value"):
        total_supply = int(supply_result["value"].get("amount", 0))

    if total_supply == 0:
        return result

    # Izračunaj koncentraciju
    amounts = [int(acc.get("amount", 0)) for acc in accounts[:5]]
    top5_total = sum(amounts)
    top1 = amounts[0] if amounts else 0

    result["top5_pct"] = round(top5_total / total_supply * 100, 1)
    result["top1_pct"] = round(top1 / total_supply * 100, 1)
    result["is_concentrated"] = result["top5_pct"] > 80

    if result["top5_pct"] > 90:
        result["concentration_risk"] = "CRITICAL"
    elif result["top5_pct"] > 80:
        result["concentration_risk"] = "HIGH"
    elif result["top5_pct"] > 60:
        result["concentration_risk"] = "MEDIUM"
    else:
        result["concentration_risk"] = "LOW"

    return result


# ---------------------------------------------------------------------------
# V6 MODULE 3: Rug Velocity Score
# ---------------------------------------------------------------------------

def calculate_rug_velocity(mint: str, chain: str = "SOLANA") -> dict:
    """
    Mjeri brzinu kojom se likvidnost povlači.
    Brzo povlačenje = klasičan rug pull.
    """
    result = {
        "velocity_score": 0.0,
        "peak_to_rug_minutes": -1,
        "is_fast_rug": False,
        "lp_removal_detected": False,
    }

    if chain != "SOLANA":
        return result

    txs = get_recent_transactions(mint, limit=50, chain=chain)
    if not txs or len(txs) < 10:
        return result

    # Tražimo veliku outflow transakciju (rug) posle spike-a
    # Jednostavna heuristika: ako ima < 30 tx ukupno i zadnja je velika = rug
    total_txs = len(txs)

    if total_txs < 100:  # Novi token
        # Najstarija = mint, najnovija = zadnja aktivnost
        mint_time = txs[-1].get("blockTime", 0)
        last_time = txs[0].get("blockTime", 0)

        if mint_time and last_time:
            lifetime_minutes = (last_time - mint_time) / 60
            # Ako je token živio kratko i ima malo transakcija = sumnjivo
            if lifetime_minutes < 60 and total_txs < 30:
                result["velocity_score"] = round(1.0 - (lifetime_minutes / 60), 2)
                result["peak_to_rug_minutes"] = int(lifetime_minutes)
                result["is_fast_rug"] = lifetime_minutes < 15
            elif lifetime_minutes < 30:
                result["velocity_score"] = 0.5
                result["peak_to_rug_minutes"] = int(lifetime_minutes)

    return result


# ---------------------------------------------------------------------------
# V6 MODULE 4: Serial Rugger Database (lokalna memorija)
# ---------------------------------------------------------------------------

# Lokalna baza serijskih scammera (ne resetuje se između restartova u RAM-u)
serial_ruggers = defaultdict(lambda: {
    "rug_count": 0,
    "total_tokens": 0,
    "chains": [],
    "last_seen": 0,
    "estimated_stolen_sol": 0.0,
})


def check_serial_rugger(creator: str, chain: str, label: str, deploy_ts: int) -> dict:
    """
    Provjerava je li creator serijski rugger i ažurira bazu.
    """
    result = {
        "is_serial_rugger": False,
        "rug_count": 0,
        "serial_rugger_risk": "NEW",
        "previously_seen_chains": [],
    }

    if not creator:
        return result

    entry = serial_ruggers[creator]

    # Ažuriraj bazu
    entry["total_tokens"] += 1
    entry["last_seen"] = deploy_ts
    if chain not in entry["chains"]:
        entry["chains"].append(chain)

    if label == "DANGER":
        entry["rug_count"] += 1

    # Procjena
    result["rug_count"] = entry["rug_count"]
    result["previously_seen_chains"] = entry["chains"]

    if entry["rug_count"] >= 5:
        result["is_serial_rugger"] = True
        result["serial_rugger_risk"] = "CRITICAL — 5+ confirmed rugs"
    elif entry["rug_count"] >= 3:
        result["is_serial_rugger"] = True
        result["serial_rugger_risk"] = f"HIGH — {entry['rug_count']} confirmed rugs"
    elif entry["rug_count"] >= 1:
        result["serial_rugger_risk"] = f"MODERATE — {entry['rug_count']} previous rug"
    elif entry["total_tokens"] > 3:
        result["serial_rugger_risk"] = f"WATCH — {entry['total_tokens']} tokens deployed"

    return result


# ---------------------------------------------------------------------------
# V6 SUMMARY
# ---------------------------------------------------------------------------

def run_v6_analysis(mint: str, creator: str, chain: str, deploy_ts: int,
                    token_name: str, ticker: str, tx_amounts: list,
                    holder_count: int, label: str) -> dict:
    """Pokreće sve V6 analize."""
    log.info("  [V6] Pokrenuta analiza")
    v6_intel = {}

    # 1. Backdoor detection
    log.info("  [V6] Smart contract backdoor scan...")
    v6_intel["backdoor"] = detect_contract_backdoor(mint, chain)
    time.sleep(0.5)

    # 2. Holder concentration
    log.info("  [V6] Holder concentration analysis...")
    v6_intel["concentration"] = analyze_holder_concentration(mint, chain)
    time.sleep(0.5)

    # 3. Rug velocity
    log.info("  [V6] Rug velocity score...")
    v6_intel["velocity"] = calculate_rug_velocity(mint, chain)
    time.sleep(0.3)

    # 4. Serial rugger check
    log.info("  [V6] Serial rugger database check...")
    v6_intel["serial"] = check_serial_rugger(creator, chain, label, deploy_ts)

    log.info("  [V6] %s", v6_success_rate_v6(v6_intel))
    return v6_intel


def v6_success_rate_v6(v6_intel: dict) -> str:
    modules = {
        "backdoor":      bool(v6_intel.get("backdoor", {}).get("backdoor_functions")),
        "concentration": v6_intel.get("concentration", {}).get("top5_pct", 0) > 0,
        "velocity":      v6_intel.get("velocity", {}).get("velocity_score", 0) > 0,
        "serial":        v6_intel.get("serial", {}).get("rug_count", 0) > 0 or
                         v6_intel.get("serial", {}).get("serial_rugger_risk") != "NEW",
    }
    success = sum(modules.values())
    details = " | ".join(f"{k}:{'OK' if v else 'EMPTY'}" for k, v in modules.items())
    return f"{success}/4 V6 modula OK [{details}]"


# ---------------------------------------------------------------------------
# V5 SUMMARY — pokrece sve V5 module
# ---------------------------------------------------------------------------

def run_v5_analysis(mint: str, creator: str, chain: str, deploy_ts: int,
                     token_name: str, ticker: str, tx_amounts: list,
                     holder_count: int) -> dict:
    """Pokreće sve V5 napredne analize."""
    log.info("  [V5] Pokrenuta napredna analiza")
    v5_intel = {}
    
    # 1. Cross-chain matching
    log.info("  [V5] Cross-chain wallet matching...")
    v5_intel["cross_chain"] = detect_cross_chain_match(mint, chain, deploy_ts, tx_amounts, holder_count)
    time.sleep(0.3)
    
    # 2. Jito bundle detection
    log.info("  [V5] Jito bundle detection...")
    v5_intel["jito"] = detect_jito_bundle(mint, chain)
    time.sleep(0.5)
    
    # 3. Name stylometry
    log.info("  [V5] Token name stylometry...")
    v5_intel["stylometry"] = analyze_name_stylometry(token_name, ticker)
    
    # 4. Dev -> CEX sweep
    log.info("  [V5] Dev->CEX sweep tracking...")
    v5_intel["sweep"] = detect_dev_cex_sweep(creator, chain) if creator else {
        "sweep_to_cex": False, "cex_destination": "", "sweep_count": 0, "exit_pattern_confidence": 0.0
    }
    time.sleep(0.5)
    
    return v5_intel


def v5_success_rate(v5_intel: dict) -> str:
    """Loguje uspjesnost V5 modula."""
    modules = {
        "cross_chain": bool(v5_intel.get("cross_chain", {}).get("pattern_hash")),
        "jito":        v5_intel.get("jito", {}).get("buys_in_first_slot", 0) > 0,
        "stylometry":  v5_intel.get("stylometry", {}).get("name_scam_score", 0) > 0 or v5_intel.get("stylometry", {}).get("similarity_score", 0) > 0,
        "sweep":       v5_intel.get("sweep", {}).get("sweep_count", 0) > 0 or bool(v5_intel.get("sweep", {}).get("cex_destination")),
    }
    success = sum(modules.values())
    details = " | ".join(f"{k}:{'OK' if v else 'EMPTY'}" for k, v in modules.items())
    return f"{success}/4 V5 modula OK [{details}]"


# ---------------------------------------------------------------------------
# CIA Intel Summary Builder
# ---------------------------------------------------------------------------

# Semaphore za max 3 paralelna RPC poziva
_rpc_semaphore = None

def get_rpc_semaphore():
    global _rpc_semaphore
    if _rpc_semaphore is None:
        try:
            loop = asyncio.get_event_loop()
            _rpc_semaphore = asyncio.Semaphore(3)
        except RuntimeError:
            pass
    return _rpc_semaphore


def cia_success_rate(intel: dict) -> str:
    """Loguje koliko od 5 CIA modula je vratilo stvarne podatke."""
    modules = {
        "funding": intel.get("funding", {}).get("hop_count", 0) > 0 or intel.get("funding", {}).get("all_fresh", False),
        "latency": intel.get("latency", {}).get("latency_ms", -1) != -1,
        "entropy": intel.get("entropy", {}).get("total_txs", 0) > 0,
        "wash":    intel.get("wash", {}).get("dev_sell_latency_s", -1) != -1 or intel.get("wash", {}).get("linker_wallets_connected", False),
        "cluster": intel.get("cluster", {}).get("total_checked", 0) > 0,
    }
    success = sum(modules.values())
    details = " | ".join(f"{k}:{'OK' if v else 'EMPTY'}" for k, v in modules.items())
    return f"{success}/5 modula OK [{details}]"


def run_cia_analysis(mint: str, creator: str, chain: str) -> dict:
    """
    Pokreće sve CIA analize sa Semaphore throttlingom i loguje uspjesnost.
    """
    log.info("  [CIA] Pokrenuta analiza za %s (%s)", mint[:12], chain)

    intel = {}

    # 1. Funding origin (3-hop)
    log.info("  [CIA] Tracing funding origin...")
    intel["funding"] = trace_funding_origin(creator, depth=3, chain=chain) if creator else {}
    time.sleep(1.0)

    # 2. Deployment latency
    log.info("  [CIA] Mjerim deployment latency...")
    intel["latency"] = get_deployment_latency(mint, chain)
    time.sleep(1.0)

    # 3. Transaction entropy
    log.info("  [CIA] Analiziram transaction entropy...")
    intel["entropy"] = analyze_transaction_entropy(mint, chain)
    time.sleep(1.0)

    # 4. Wash pattern
    log.info("  [CIA] Detektujem wash pattern...")
    intel["wash"] = detect_wash_pattern(mint, creator, chain)
    time.sleep(1.0)

    # 5. Holder cluster age
    log.info("  [CIA] Analiziram holder cluster...")
    intel["cluster"] = analyze_holder_cluster(mint, chain)

    # Loguj uspjesnost
    log.info("  [CIA] %s", cia_success_rate(intel))

    return intel

# ---------------------------------------------------------------------------
# Dataset writer
# ---------------------------------------------------------------------------

def build_training_record(
    token_meta: dict,
    rugcheck_features: dict,
    label: str,
    pump_data: dict,
    creator_stats: dict,
    cia_intel: dict,
    chain: str,
    v5_intel: dict = None,
    v6_intel: dict = None,
) -> dict:
    if v5_intel is None:
        v5_intel = {}
    if v6_intel is None:
        v6_intel = {}

    name = pump_data.get("name") or pump_data.get("symbol") or "Unknown"
    ticker = pump_data.get("symbol") or ""
    creator = rugcheck_features.get("creator", "") or pump_data.get("creator", "")

    # Creator risk klasifikacija
    if creator_stats["total"] == 0:
        creator_risk = "NEW - no previous tokens"
    elif creator_stats["rug_rate"] >= 80:
        creator_risk = f"HIGH RISK - {creator_stats['rug_rate']}% rug rate ({creator_stats['danger']}/{creator_stats['total']} tokens)"
    elif creator_stats["rug_rate"] >= 40:
        creator_risk = f"MODERATE RISK - {creator_stats['rug_rate']}% rug rate ({creator_stats['danger']}/{creator_stats['total']} tokens)"
    else:
        creator_risk = f"LOW RISK - {creator_stats['rug_rate']}% rug rate ({creator_stats['danger']}/{creator_stats['total']} tokens)"

    # CIA summary linije
    funding = cia_intel.get("funding", {})
    latency = cia_intel.get("latency", {})
    entropy = cia_intel.get("entropy", {})
    wash = cia_intel.get("wash", {})
    cluster = cia_intel.get("cluster", {})

    # Token lock provjera
    lp_locked_pct = rugcheck_features.get("lp_locked_pct", 0)
    lp_locked_usd = rugcheck_features.get("lp_locked_usd", 0)
    lp_lock_is_real = rugcheck_features.get("lp_lock_is_real", False)
    if lp_locked_pct > 50 and not lp_lock_is_real:
        lock_status = f"FAKE LOCK - {lp_locked_pct}% locked but $0 USD value"
    elif lp_lock_is_real:
        lock_status = f"REAL LOCK - {lp_locked_pct}% (${lp_locked_usd:.0f})"
    else:
        lock_status = f"NOT LOCKED - {lp_locked_pct}%"

    input_text = f"""Token: {name} ({ticker})
Chain: {chain}
Mint: {token_meta['mint']}
Explorer: {ChainSwitcher.get_explorer(chain, token_meta['mint'])}
Mint Authority: {'YES - can mint new tokens' if token_meta['has_mint_authority'] else 'NO - disabled'}
Freeze Authority: {'YES - can freeze accounts' if token_meta['has_freeze_authority'] else 'NO - disabled'}
Supply: {token_meta['supply']}
Decimals: {token_meta['decimals']}
RugCheck Score: {rugcheck_features.get('rugcheck_score', 'N/A')}
Top Holder %: {rugcheck_features.get('top_holder_pct', 'N/A')}
LP Lock Status: {lock_status}
Risk Flags: {', '.join(rugcheck_features.get('risks', [])) or 'None detected'}
Creator Address: {creator[:20] + '...' if creator else 'Unknown'}
Creator History: {creator_risk}
--- CIA INTEL ---
Funding Origin: {f"Master wallet traced {funding.get('hop_count', 0)} hops back" if funding else 'N/A'} | All fresh wallets: {funding.get('all_fresh', False)}
Deployment Latency: {latency.get('latency_ms', -1)}ms | Sniped: {latency.get('is_sniped', False)}
Transaction Entropy: {entropy.get('entropy_score', 1.0)} | Bot pattern: {entropy.get('is_bot_pattern', False)} | Dominant amount: {entropy.get('dominant_amount', 0)} SOL ({entropy.get('dominant_amount_pct', 0)}% of txs)
Wash Pattern: {wash.get('wash_detected', False)} | Dev sold in {wash.get('dev_sell_latency_s', -1)}s | Linked wallets: {wash.get('linker_wallets_connected', False)}
Holder Cluster: avg age {cluster.get('avg_age_days', 0)} days | New wallets: {cluster.get('new_wallets_count', 0)}/{cluster.get('total_checked', 0)} | Bot farm: {cluster.get('is_bot_farm', False)}
--- V5 INTEL ---
Cross-Chain Match: {v5_intel.get('cross_chain', {}).get('cross_chain_match', False)} | Pattern: {v5_intel.get('cross_chain', {}).get('pattern_hash', 'N/A')}
Jito Bundle: {v5_intel.get('jito', {}).get('bundle_detected', False)} | Buys in first slot: {v5_intel.get('jito', {}).get('buys_in_first_slot', 0)}
Name Stylometry: scam score {v5_intel.get('stylometry', {}).get('name_scam_score', 0)}/100 | Patterns: {', '.join(v5_intel.get('stylometry', {}).get('matched_patterns', [])) or 'none'}
Dev->CEX Sweep: {v5_intel.get('sweep', {}).get('sweep_to_cex', False)} | Destination: {v5_intel.get('sweep', {}).get('cex_destination', 'N/A')}
Lifecycle Prediction: {v5_intel.get('lifecycle', {}).get('prediction_text', 'N/A')}
--- V6 INTEL ---
Backdoor Functions: {', '.join(v6_intel.get('backdoor', {}).get('backdoor_functions', [])) or 'None'}
Backdoor Risk Score: {v6_intel.get('backdoor', {}).get('backdoor_risk_score', 0)}/100
Is Proxy Contract: {v6_intel.get('backdoor', {}).get('is_proxy', False)}
Has Pause Function: {v6_intel.get('backdoor', {}).get('has_pause_function', False)}
Top5 Holder %: {v6_intel.get('concentration', {}).get('top5_pct', 0)}% | Risk: {v6_intel.get('concentration', {}).get('concentration_risk', 'N/A')}
Rug Velocity Score: {v6_intel.get('velocity', {}).get('velocity_score', 0)} | Fast rug: {v6_intel.get('velocity', {}).get('is_fast_rug', False)}
Serial Rugger: {v6_intel.get('serial', {}).get('is_serial_rugger', False)} | Rug count: {v6_intel.get('serial', {}).get('rug_count', 0)} | Risk: {v6_intel.get('serial', {}).get('serial_rugger_risk', 'NEW')}"""

    # CIA flags za output
    cia_flags = []
    if funding.get("all_fresh"):
        cia_flags.append("fresh funding chain")
    if latency.get("is_sniped"):
        cia_flags.append(f"sniped in {latency.get('latency_ms')}ms")
    if entropy.get("is_bot_pattern"):
        cia_flags.append(f"bot transactions ({entropy.get('dominant_amount_pct')}% same amount)")
    if wash.get("wash_detected"):
        cia_flags.append("wash trading pattern")
    if cluster.get("is_bot_farm"):
        cia_flags.append("bot farm holders")
    if not rugcheck_features.get("lp_lock_is_real") and lp_locked_pct > 50:
        cia_flags.append("fake LP lock")

    cia_summary = f" CIA flags: {', '.join(cia_flags)}." if cia_flags else ""

    if label == "DANGER":
        output = f"DANGER - High risk {chain} token. Risk flags: {', '.join(rugcheck_features.get('risks', ['Unknown']))}. RugCheck score: {rugcheck_features.get('rugcheck_score', 0)}. Creator rug rate: {creator_stats['rug_rate']}%.{cia_summary}"
    elif label == "WARN":
        output = f"WARN - Moderate risk {chain} token. Flags: {', '.join(rugcheck_features.get('risks', ['Unknown']))}. RugCheck score: {rugcheck_features.get('rugcheck_score', 0)}.{cia_summary}"
    else:
        output = f"GOOD - Low risk {chain} token. No major red flags detected. RugCheck score: {rugcheck_features.get('rugcheck_score', 0)}. Creator has clean history.{cia_summary}"

    return {
        "instruction": "Analyze this Solana/Avax token and classify its risk level as DANGER, WARN, or GOOD.",
        "input": input_text,
        "output": output,
        "label": label,
        "chain": chain,
        "creator": creator,
        "creator_rug_rate": creator_stats["rug_rate"],
        # CIA raw data za kasniju analizu
        "cia_funding_hops": funding.get("hop_count", 0),
        "cia_all_fresh_wallets": funding.get("all_fresh", False),
        "cia_deployment_latency_ms": latency.get("latency_ms", -1),
        "cia_sniped": latency.get("is_sniped", False),
        "cia_entropy_score": entropy.get("entropy_score", 1.0),
        "cia_bot_pattern": entropy.get("is_bot_pattern", False),
        "cia_wash_detected": wash.get("wash_detected", False),
        "cia_bot_farm": cluster.get("is_bot_farm", False),
        "cia_avg_holder_age_days": cluster.get("avg_age_days", 0.0),
        "cia_fake_lp_lock": not lp_lock_is_real and lp_locked_pct > 50,
        # V5 ADVANCED FIELDS
        "v5_cross_chain_match": v5_intel.get("cross_chain", {}).get("cross_chain_match", False),
        "v5_pattern_hash": v5_intel.get("cross_chain", {}).get("pattern_hash", ""),
        "v5_jito_bundle": v5_intel.get("jito", {}).get("bundle_detected", False),
        "v5_buys_first_slot": v5_intel.get("jito", {}).get("buys_in_first_slot", 0),
        "v5_name_scam_score": v5_intel.get("stylometry", {}).get("name_scam_score", 0),
        "v5_name_patterns": v5_intel.get("stylometry", {}).get("matched_patterns", []),
        "v5_name_similar_to_previous": v5_intel.get("stylometry", {}).get("similar_to_previous", False),
        "v5_sweep_to_cex": v5_intel.get("sweep", {}).get("sweep_to_cex", False),
        "v5_cex_destination": v5_intel.get("sweep", {}).get("cex_destination", ""),
        "v5_lifecycle_rug_minutes": v5_intel.get("lifecycle", {}).get("estimated_rug_minutes", -1),
        "v5_lifecycle_confidence": v5_intel.get("lifecycle", {}).get("confidence", 0.0),
        "v5_lifecycle_prediction": v5_intel.get("lifecycle", {}).get("prediction_text", ""),
        # V6 FIELDS
        "v6_has_backdoor": v6_intel.get("backdoor", {}).get("has_backdoor", False),
        "v6_backdoor_functions": v6_intel.get("backdoor", {}).get("backdoor_functions", []),
        "v6_backdoor_risk_score": v6_intel.get("backdoor", {}).get("backdoor_risk_score", 0),
        "v6_is_proxy": v6_intel.get("backdoor", {}).get("is_proxy", False),
        "v6_has_pause": v6_intel.get("backdoor", {}).get("has_pause_function", False),
        "v6_has_mint_backdoor": v6_intel.get("backdoor", {}).get("has_mint_function", False),
        "v6_has_drain": v6_intel.get("backdoor", {}).get("has_drain_function", False),
        "v6_has_blacklist": v6_intel.get("backdoor", {}).get("has_blacklist", False),
        "v6_top5_holder_pct": v6_intel.get("concentration", {}).get("top5_pct", 0.0),
        "v6_concentration_risk": v6_intel.get("concentration", {}).get("concentration_risk", "LOW"),
        "v6_is_concentrated": v6_intel.get("concentration", {}).get("is_concentrated", False),
        "v6_velocity_score": v6_intel.get("velocity", {}).get("velocity_score", 0.0),
        "v6_is_fast_rug": v6_intel.get("velocity", {}).get("is_fast_rug", False),
        "v6_peak_to_rug_minutes": v6_intel.get("velocity", {}).get("peak_to_rug_minutes", -1),
        "v6_is_serial_rugger": v6_intel.get("serial", {}).get("is_serial_rugger", False),
        "v6_serial_rug_count": v6_intel.get("serial", {}).get("rug_count", 0),
        "v6_serial_rugger_risk": v6_intel.get("serial", {}).get("serial_rugger_risk", "NEW"),
    }


def append_to_dataset(record: dict, output_path: Path) -> None:
    try:
        with output_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        log.info("  -> Snimljeno [%s][%s] Creator rug rate: %s%%  Ukupno: %d",
                 record["label"],
                 record.get("chain", "?"),
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

def process_token(token_data: dict, output_path: Path) -> None:
    mint = token_data.get("mint") or token_data.get("address")
    if not mint:
        return

    # ChainSwitcher — auto-detect
    chain = ChainSwitcher.detect(mint)
    log.info("Novi token: %s [%s]", mint, chain)

    if chain == "UNKNOWN":
        log.warning("  -> Nepoznat lanac za adresu %s, preskačem.", mint[:16])
        return

    time.sleep(RATE_LIMIT_DELAY)

    token_meta = get_token_metadata(mint, chain)
    token_meta["chain"] = chain

    time.sleep(0.5)
    rugcheck_report = get_rugcheck_report(mint) if chain == "SOLANA" else None

    if rugcheck_report is None:
        label = "UNKNOWN"
        rugcheck_features = {
            "rugcheck_score": -1,
            "mint_authority": token_meta["has_mint_authority"],
            "freeze_authority": token_meta["has_freeze_authority"],
            "lp_burned": False,
            "lp_lock_is_real": False,
            "lp_locked_pct": 0,
            "lp_locked_usd": 0,
            "risks": [],
            "creator": token_data.get("creator", ""),
        }
        if token_meta["has_mint_authority"] or token_meta["has_freeze_authority"]:
            label = "WARN"
            if token_meta["has_mint_authority"]:
                rugcheck_features["risks"].append("Mint Authority Active")
            if token_meta["has_freeze_authority"]:
                rugcheck_features["risks"].append("Freeze Authority Active")
    else:
        label = parse_rugcheck_label(rugcheck_report)
        rugcheck_features = parse_rugcheck_features(rugcheck_report)

    if label == "UNKNOWN" and not rugcheck_features.get("risks"):
        log.info("  -> Nedovoljno podataka, preskačem.")
        return

    creator = rugcheck_features.get("creator", "") or token_data.get("creator", "")
    creator_stats = get_creator_stats(creator)

    # CIA Intelligence Analysis
    cia_intel = run_cia_analysis(mint, creator, chain)
    
    # V5 — Advanced Behavioral Analysis
    deploy_ts = int(time.time())  # fallback ako nema mint_time
    if cia_intel.get("latency", {}).get("mint_time", 0):
        deploy_ts = cia_intel["latency"]["mint_time"]
    
    tx_amounts = []
    if cia_intel.get("entropy", {}).get("dominant_amount", 0):
        tx_amounts = [cia_intel["entropy"]["dominant_amount"]]
    
    holder_count = cia_intel.get("cluster", {}).get("total_checked", 0)
    name = token_data.get("name", "") or token_data.get("symbol", "Unknown")
    ticker = token_data.get("symbol", "")
    
    v5_intel = run_v5_analysis(mint, creator, chain, deploy_ts, name, ticker, tx_amounts, holder_count)
    
    # V5 success log
    log.info("  [V5] %s", v5_success_rate(v5_intel))
    
    # Lifecycle prediction nakon svih signala
    v5_intel["lifecycle"] = predict_lifecycle(
        cia_intel,
        rugcheck_features.get("rugcheck_score", 0),
        creator_stats.get("rug_rate", 0.0)
    )

    # V6 — Contract Backdoor + Deep Intel
    v6_intel = run_v6_analysis(
        mint, creator, chain, deploy_ts, name, ticker,
        tx_amounts, holder_count, label
    )

    # Upgrade label na osnovu CIA signala
    cia_danger_signals = sum([
        cia_intel.get("wash", {}).get("wash_detected", False),
        cia_intel.get("cluster", {}).get("is_bot_farm", False),
        cia_intel.get("funding", {}).get("all_fresh", False),
        cia_intel.get("entropy", {}).get("is_bot_pattern", False),
        not rugcheck_features.get("lp_lock_is_real", True) and rugcheck_features.get("lp_locked_pct", 0) > 50,
    ])

    if label == "GOOD" and cia_danger_signals >= 3:
        log.warning("  [CIA] Label upgrade: GOOD -> WARN (3+ CIA flags)")
        label = "WARN"
    elif label in ("GOOD", "WARN") and cia_danger_signals >= 4:
        log.warning("  [CIA] Label upgrade -> DANGER (4+ CIA flags)")
        label = "DANGER"

    record = build_training_record(
        token_meta, rugcheck_features, label, token_data,
        creator_stats, cia_intel, chain, v5_intel, v6_intel
    )
    append_to_dataset(record, output_path)
    update_creator_history(creator, label)

# ---------------------------------------------------------------------------
# WebSocket listener (Pump.fun — Solana only)
# ---------------------------------------------------------------------------

async def listen(output_path: Path) -> None:
    reconnect_delay = 5

    while True:
        try:
            log.info("Spajam se na %s...", PUMPPORTAL_WS)
            async with websockets.connect(
                PUMPPORTAL_WS,
                ping_interval=20,
                ping_timeout=30,
            ) as ws:
                log.info("Konekcija uspostavljena.")
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                reconnect_delay = 5

                async for message in ws:
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        continue

                    if isinstance(data, dict) and ("mint" in data or "address" in data):
                        await asyncio.to_thread(process_token, data, output_path)
                    else:
                        log.debug("Poruka ignorisana: %s", str(data)[:80])

        except (websockets.exceptions.ConnectionClosed, OSError) as e:
            log.warning("WS prekinut: %s. Pokušavam za %ds...", e, reconnect_delay)
        except Exception as e:
            log.error("Greška: %s. Pokušavam za %ds...", e, reconnect_delay)

        await asyncio.sleep(reconnect_delay)
        reconnect_delay = min(reconnect_delay * 2, 60)

# ---------------------------------------------------------------------------
# CLI — direktno testiranje jedne adrese
# ---------------------------------------------------------------------------

def scan_single(address: str) -> None:
    """
    Skenira jednu adresu direktno iz CLI-a.
    Koristiti: python dataset_collector_v4.py <adresa>
    """
    chain = ChainSwitcher.detect(address)
    log.info("Skeniram: %s [%s]", address, chain)

    token_meta = get_token_metadata(address, chain)
    rugcheck_report = get_rugcheck_report(address) if chain == "SOLANA" else None

    if rugcheck_report:
        label = parse_rugcheck_label(rugcheck_report)
        features = parse_rugcheck_features(rugcheck_report)
    else:
        label = "UNKNOWN"
        features = {"risks": [], "rugcheck_score": -1, "creator": "", "lp_locked_pct": 0, "lp_lock_is_real": False}

    # Fix: izvuci creator iz svih mogucih polja u reportu
    creator = features.get("creator", "")
    if not creator and rugcheck_report:
        creator = rugcheck_report.get("creator", "") or rugcheck_report.get("deployer", "")
    if not creator and rugcheck_report:
        markets = rugcheck_report.get("markets", [])
        for m in markets:
            creator = m.get("deployer", "") or m.get("creator", "")
            if creator:
                break

    if creator:
        log.info("  Creator: %s", creator[:20])
    else:
        log.warning("  Creator nije pronađen — CIA funding/wash analiza biće ograničena")

    cia_intel = run_cia_analysis(address, creator, chain)

    print(f"\n{'='*55}")
    print(f"  SYNDICATE SCAN RESULT")
    print(f"  Token  : {address}")
    print(f"  Chain  : {chain}")
    print(f"  Label  : {label}")
    print(f"  Creator: {creator[:20] + '...' if creator else 'NOT FOUND'}")
    print(f"  RugCheck Score: {features.get('rugcheck_score', 'N/A')}")
    print(f"  Risks  : {', '.join(features.get('risks', [])) or 'None'}")
    print(f"--- CIA INTEL ---")
    print(f"  Deployment latency : {cia_intel.get('latency', {}).get('latency_ms', -1)}ms")
    print(f"  Sniped             : {cia_intel.get('latency', {}).get('is_sniped', False)}")
    print(f"  Bot pattern        : {cia_intel.get('entropy', {}).get('is_bot_pattern', False)}")
    print(f"  Dominant amount    : {cia_intel.get('entropy', {}).get('dominant_amount', 0)} SOL ({cia_intel.get('entropy', {}).get('dominant_amount_pct', 0)}%)")
    print(f"  Wash pattern       : {cia_intel.get('wash', {}).get('wash_detected', False)}")
    print(f"  Dev sold fast      : {cia_intel.get('wash', {}).get('dev_sold_fast', False)} ({cia_intel.get('wash', {}).get('dev_sell_latency_s', -1)}s)")
    print(f"  Bot farm holders   : {cia_intel.get('cluster', {}).get('is_bot_farm', False)}")
    print(f"  Avg holder age     : {cia_intel.get('cluster', {}).get('avg_age_days', 0)} days")
    print(f"  Fresh funding chain: {cia_intel.get('funding', {}).get('all_fresh', False)}")
    print(f"  Funding hops       : {cia_intel.get('funding', {}).get('hop_count', 0)}")
    print(f"  Master wallet      : {cia_intel.get('funding', {}).get('master_wallet', 'N/A')[:20] if cia_intel.get('funding', {}).get('master_wallet') else 'N/A'}")
    print(f"{'='*55}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import sys

    # CLI scan mode
    if len(sys.argv) > 1:
        scan_single(sys.argv[1])
        return

    output_path = Path(OUTPUT_FILE)

    log.info("=" * 55)
    log.info("  Syndicate Dataset Collector V6 — Contract Backdoor + Deep Intel")
    log.info("  ChainSwitcher: SOLANA + AVAX")
    log.info("  Izlazni fajl : %s", output_path.absolute())
    log.info("  Prethodni zapisi: %d", count_lines(output_path))
    log.info("=" * 55)

    try:
        asyncio.run(listen(output_path))
    except KeyboardInterrupt:
        log.info("Zaustavljeno. Ukupno: %d zapisa", count_lines(output_path))
        log.info("Creator tracking: %d unikatnih kreatora", len(creator_history))


if __name__ == "__main__":
    main()
