"""
dataset_collector.py — Sakuplja Solana token podatke i LM Studio analize za AI trening.

Instalacija: pip install websockets requests
"""

import asyncio
import json
import base64
import logging
import time
from pathlib import Path

import websockets
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PUMPPORTAL_WS   = "wss://pumpportal.fun/api/data"
SOLANA_RPC      = "https://api.mainnet-beta.solana.com"

LM_STUDIO_URL   = "http://localhost:1234/v1/chat/completions"
LM_STUDIO_MODEL = "qwen2.5-coder-14b-instruct"  # Qwen2.5 Coder 14B Instruct učitan u LM Studio
LM_TEMPERATURE  = 0.2             # niska temperatura = preciznija analiza koda
LM_MAX_TOKENS   = 512
LM_TIMEOUT      = 90              # sekunde; ako ne odgovori, preskači token

OUTPUT_FILE     = "syndicate_train.jsonl"
RPC_TIMEOUT     = 15
RATE_LIMIT_DELAY = 1.0            # sekunde između RPC poziva

SYSTEM_PROMPT = (
    "Ti si ekspert za bezbjednost Solana blockchain pametnih ugovora. "
    "Analiziraš Rust kod i identifikuješ prevare, rugpull mehanizme i sumnjive funkcije. "
    "Uvijek odgovaraš strukturirano na srpskom jeziku."
)

USER_PROMPT_TEMPLATE = (
    "Analiziraj sljedeći Rust kod za Solana pametni ugovor i identificiraj:\n"
    "1. Rugpull mehanizme (npr. skriveni mint, freeze authority, drain funkcije)\n"
    "2. Sumnjive ili opasne funkcije\n"
    "3. Nedostatke u kontroli pristupa\n"
    "4. Ukupnu procjenu rizika (NIZAK / SREDNJI / VISOK)\n\n"
    "Kod:\n```rust\n{code}\n```\n\n"
    "Odgovori strukturirano na srpskom."
)

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
# Solana RPC helpers
# ---------------------------------------------------------------------------

def rpc_post(method: str, params: list) -> dict | None:
    """Šalje JSON-RPC zahtjev na Solana RPC i vraća 'result' ili None."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        resp = requests.post(SOLANA_RPC, json=payload, timeout=RPC_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            log.warning("RPC greška (%s): %s", method, data["error"])
            return None
        return data.get("result")
    except requests.RequestException as e:
        log.warning("RPC zahtjev nije uspio (%s): %s", method, e)
        return None


def get_mint_metadata(mint: str) -> dict | None:
    """Dohvata account info za mint adresu."""
    result = rpc_post(
        "getAccountInfo",
        [mint, {"encoding": "base64", "commitment": "confirmed"}],
    )
    if result and result.get("value"):
        return result["value"]
    return None


def get_program_data(program_id: str) -> bytes | None:
    """
    Pokušava dohvatiti bytecode programa.
    Za upgradeable programe čita linked ProgramData account.
    """
    result = rpc_post(
        "getAccountInfo",
        [program_id, {"encoding": "base64", "commitment": "confirmed"}],
    )
    if not result or not result.get("value"):
        return None

    account = result["value"]
    raw     = base64.b64decode(account["data"][0])
    owner   = account.get("owner", "")

    # BPFLoaderUpgradeable: data[4:36] je ProgramData pubkey
    if owner == "BPFLoaderUpgradeab1e11111111111111111111111" and len(raw) >= 36:
        if raw[0] == 2:  # Program account discriminator
            program_data_key = _base58_encode(raw[4:36])
            pd_result = rpc_post(
                "getAccountInfo",
                [program_data_key, {"encoding": "base64", "commitment": "confirmed"}],
            )
            if pd_result and pd_result.get("value"):
                pd_raw = base64.b64decode(pd_result["value"]["data"][0])
                return pd_raw[45:] if len(pd_raw) > 45 else pd_raw

    return raw


def _base58_encode(data: bytes) -> str:
    """Minimalna base58 implementacija (bez vanjske biblioteke)."""
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(data, "big")
    result = []
    while n > 0:
        n, rem = divmod(n, 58)
        result.append(alphabet[rem])
    for byte in data:
        if byte == 0:
            result.append(alphabet[0])
        else:
            break
    return "".join(reversed(result))


# ---------------------------------------------------------------------------
# LM Studio helper
# ---------------------------------------------------------------------------

def lm_studio_available() -> bool:
    """Provjeri je li LM Studio server pokrenut i spreman."""
    try:
        resp = requests.get("http://localhost:1234/v1/models", timeout=5)
        resp.raise_for_status()
        models = [m.get("id", "") for m in resp.json().get("data", [])]
        if models:
            log.info("LM Studio aktivan. Učitani modeli: %s", models)
        else:
            log.warning("LM Studio radi, ali nema učitanog modela. Učitaj model u LM Studio GUI.")
        return True
    except requests.ConnectionError:
        log.error("LM Studio nije dostupan na http://localhost:1234")
        log.error("Pokreni LM Studio -> Local Server -> Start Server")
        return False
    except Exception as e:
        log.warning("Provjera LM Studio nije uspjela: %s", e)
        return False


def analyze_with_lm_studio(code: str) -> str | None:
    """
    Šalje kod LM Studiju u OpenAI Chat formatu i vraća analizu.
    Koristi streaming za brži prvi token i pravi timeout.
    """
    # Ograniči ulazni kod da ne premaši kontekst modela
    code_trimmed = code[:3000]

    payload = {
        "model": LM_STUDIO_MODEL,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": USER_PROMPT_TEMPLATE.format(code=code_trimmed),
            },
        ],
        "temperature": LM_TEMPERATURE,
        "max_tokens": LM_MAX_TOKENS,
        "stream": True,
    }

    try:
        with requests.post(
            LM_STUDIO_URL,
            json=payload,
            timeout=LM_TIMEOUT,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            parts = []

            for line in resp.iter_lines():
                if not line:
                    continue
                # SSE format: "data: {...}" ili "data: [DONE]"
                decoded = line.decode("utf-8") if isinstance(line, bytes) else line
                if not decoded.startswith("data:"):
                    continue
                raw_json = decoded[5:].strip()
                if raw_json == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw_json)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        parts.append(content)
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

            result = "".join(parts).strip()
            return result if result else None

    except requests.exceptions.Timeout:
        log.warning("LM Studio timeout (%ds) — preskačem token.", LM_TIMEOUT)
        return None
    except requests.exceptions.ConnectionError:
        log.warning("LM Studio konekcija prekinuta. Je li server još uvijek aktivan?")
        return None
    except requests.RequestException as e:
        log.warning("LM Studio zahtjev nije uspio: %s", e)
        return None


# ---------------------------------------------------------------------------
# Dataset writer
# ---------------------------------------------------------------------------

def append_to_dataset(code: str, analysis: str, output_path: Path) -> None:
    """Dodaje jedan trening par u JSONL fajl."""
    record = {
        "instruction": "Analiziraj Rust kod Solana pametnog ugovora na sumnjive obrasce i prevare.",
        "input": code,
        "output": analysis,
    }
    try:
        with output_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        log.info("Zapis dodan u %s (ukupno: %d)", output_path.name, count_lines(output_path))
    except OSError as e:
        log.error("Nije moguće zapisati u fajl: %s", e)


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
    """Kompletna obrada jednog tokena: RPC fetch -> LM Studio -> JSONL."""
    mint = token_data.get("mint") or token_data.get("address")
    if not mint:
        log.debug("Token bez mint polja, preskačem.")
        return

    log.info("Novi token: %s", mint)
    time.sleep(RATE_LIMIT_DELAY)

    # 1. Dohvati bytecode / program data
    raw_bytes = get_program_data(mint)
    if not raw_bytes:
        meta = get_mint_metadata(mint)
        if not meta:
            log.info("  -> Nema podataka na RPC-u za %s, preskačem.", mint)
            return
        raw_bytes = base64.b64decode(meta["data"][0]) if meta.get("data") else b""

    if not raw_bytes:
        log.info("  -> Prazan bytecode za %s, preskačem.", mint)
        return

    # 2. Konvertuj u tekst (UTF-8) ili hex dump ako je binarno
    try:
        code_str = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        code_str = raw_bytes[:2048].hex()

    if len(code_str) < 20:
        log.info("  -> Premalo podataka (%d znakova), preskačem.", len(code_str))
        return

    # 3. Analiza u LM Studiju
    log.info("  -> Šaljem LM Studiju (%d znakova koda)...", len(code_str))
    analysis = analyze_with_lm_studio(code_str)

    if not analysis:
        log.warning("  -> LM Studio nije vratio analizu za %s.", mint)
        return

    # 4. Snimi u dataset
    append_to_dataset(code_str, analysis, output_path)
    log.info("  -> Snimljeno. ✓")


# ---------------------------------------------------------------------------
# WebSocket listener
# ---------------------------------------------------------------------------

async def listen(output_path: Path) -> None:
    """Sluša PumpPortal WS stream i obrađuje svaki novi token."""
    reconnect_delay = 5

    while True:
        try:
            log.info("Spajam se na %s...", PUMPPORTAL_WS)
            async with websockets.connect(
                PUMPPORTAL_WS,
                ping_interval=20,
                ping_timeout=30,
            ) as ws:
                log.info("Konekcija uspostavljena. Pretplaćujem se na newToken...")
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                reconnect_delay = 5  # reset nakon uspješnog spajanja

                async for message in ws:
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        log.debug("Nije JSON: %s", message[:100])
                        continue

                    if isinstance(data, dict) and ("mint" in data or "address" in data):
                        await asyncio.to_thread(process_token, data, output_path)
                    else:
                        log.debug("Poruka (ignorisana): %s", str(data)[:80])

        except (websockets.exceptions.ConnectionClosed, OSError) as e:
            log.warning("WS konekcija prekinuta: %s. Pokušavam ponovo za %ds...", e, reconnect_delay)
        except Exception as e:
            log.error("Neočekivana greška: %s. Pokušavam ponovo za %ds...", e, reconnect_delay)

        await asyncio.sleep(reconnect_delay)
        reconnect_delay = min(reconnect_delay * 2, 60)  # exponential backoff, max 60s


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    output_path = Path(OUTPUT_FILE)
    log.info("=" * 55)
    log.info("  Dataset kolektor — LM Studio backend")
    log.info("  Izlazni fajl : %s", output_path.absolute())
    log.info("  Model server : %s", LM_STUDIO_URL)
    log.info("  Temperature  : %s", LM_TEMPERATURE)
    log.info("  Prethodni zapisi: %d", count_lines(output_path))
    log.info("=" * 55)

    if not lm_studio_available():
        return

    try:
        asyncio.run(listen(output_path))
    except KeyboardInterrupt:
        log.info("Zaustavljeno. Ukupno sakupljenih zapisa: %d", count_lines(output_path))


if __name__ == "__main__":
    main()
