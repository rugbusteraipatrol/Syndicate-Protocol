# 🛡️ Syndicate Protocol: AI-Native Fraud Detection

> Real-time multi-chain token fraud detection powered by fine-tuned large language models.

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Dataset](https://img.shields.io/badge/Dataset-358K%2B%20Records-blue)
![Model](https://img.shields.io/badge/Model-Qwen3--14B-purple)
![Solana](https://img.shields.io/badge/Chain-Solana-green)
![Avax](https://img.shields.io/badge/Chain-Avalanche-red)

---

## Latest Hackathon Update - 2026-06-03

Syndicate Protocol now has a live Solana intelligence pipeline behind the public wallet scanner.

- **358K+ historical Solana scan records** across V1-V6 and academic evidence.
- **12,707 Solana scan records** collected in the live V6 PostgreSQL pipeline.
- Records are persisted as full JSON evidence payloads in PostgreSQL.
- Portfolio Scan UI is live for Phantom wallets.
- Duplicate mint records are skipped by database constraint.
- API-ready scoring layer is being prepared for partner access.

See: [`docs/HACKATHON_UPDATE_2026-06-03.md`](docs/HACKATHON_UPDATE_2026-06-03.md)

---

## Overview

Syndicate Protocol is an AI-native fraud detection system built for **Solana and Avalanche C-Chain**. It monitors new token deployments in real time, runs multi-layer CIA + V5 + V6 intelligence analysis on each token, and classifies each as **DANGER**, **WARN**, or **GOOD** — before retail investors are exposed.

Unlike existing tools that rely on static metadata checks, Syndicate Protocol combines:
- **Live on-chain data** via PumpPortal WebSocket (Solana) + Avalanche RPC polling
- **RugCheck API** for verified ground-truth labels (Solana)
- **CIA Intelligence Engine V6** — 15+ behavioral analysis modules
- **358,000+ historical Solana scan records** across V1-V6 and academic evidence
- **Fine-tuned LLM** (Qwen3-14B QLoRA) trained to recognize fraud patterns

> 🚧 **Multi-chain expansion in progress** — Avalanche C-Chain live. Arbitrum and Lightchain coming soon.

---

## 📊 Dataset

| Dataset | Records | Chain | Source |
|---------|---------|-------|--------|
| Live v2 | 63,000+ | Solana | PumpPortal + RugCheck |
| Live v3 | 22,000+ | Solana | PumpPortal + RugCheck + Creator Tracking |
| Live v4 | 30,000+ | Solana | + CIA Intelligence Engine |
| Live v5 | Growing | Solana | + Jito, Stylometry, CEX Sweep |
| Live v6 | 12,707+ | Solana | + Backdoor, Concentration, Velocity |
| Academic | 116,304 | Solana | SolRPDS 2021–2024 |
| Avax Historical | 2,238 | Avalanche | SnowTrace + Avax RPC |
| Avax Real-time | Growing | Avalanche | Avax RPC polling |
| **Solana Historical Corpus** | **358,000+** | **Solana** | V1-V6 + academic scan evidence |
| **Total** | **358,000+** | **Multi-chain** | Historical Solana corpus plus live Avalanche expansion |

---

## 🧠 CIA Intelligence Engine V6

15+ behavioral analysis modules across 3 generations:

### CIA Core (V4)
| Module | What it detects |
|--------|----------------|
| **Funding Origin (3-hop)** | Master wallet financing multiple scam deployments |
| **Deployment Latency** | Sniper bots in mempool (< 3 second buys) |
| **Transaction Entropy** | Repetitive buy amounts = bot farm signature |
| **Wash Pattern** | Mint → Dev instant sell → Supply wallet buy |
| **Holder Cluster Age** | 70%+ new wallets (< 7 days) = coordinated bot farm |

### Advanced Intel (V5)
| Module | What it detects |
|--------|----------------|
| **Cross-Chain Matching** | Same scam pattern on Solana AND Avalanche |
| **Jito Bundle Detection** | 5+ unique buyers in same slot = orchestrated launch |
| **Token Name Stylometry** | NLP clusters: "Doge Killer", "Pepe 2.0", "AI Agent" |
| **Dev→CEX Sweep** | Creator wallet sending funds to Binance/Coinbase/OKX |
| **Lifecycle Prediction** | "Rug expected in 15 minutes (87% confidence)" |

### Contract Intelligence (V6)
| Module | What it detects |
|--------|----------------|
| **Smart Contract Backdoor** | Hidden mint/drain/pause/blacklist/upgradeTo functions |
| **Proxy Pattern** | Upgradeable contracts (dev can change code after deploy) |
| **Holder Concentration** | Top 5 wallets hold > 80% supply = CRITICAL risk |
| **Rug Velocity Score** | Token lives < 15 minutes = fast rug (score 0.98/1.0) |
| **Serial Rugger DB** | Same creator rugged 3+ tokens = SERIAL RUGGER flag |

**Label Upgrade Logic:** If RugCheck says GOOD but CIA/V5/V6 detects 3+ flags → automatic upgrade to WARN or DANGER.

---

## 🧠 Model

**Base:** Qwen3-14B
**Method:** QLoRA fine-tuning via Unsloth (RunPod RTX 4090, ~9 hours)
**Training data:** 184,000+ verified records
**Train loss:** 1.227
**GGUF:** [`ffurduj/syndicate-gguf`](https://huggingface.co/ffurduj/syndicate-gguf) on HuggingFace

---

## 🏗️ Infrastructure

```
┌─────────────────────────────────────────────────────────────┐
│           SYNDICATE PIPELINE V6 — MULTI-CHAIN               │
│                                                             │
│  SOLANA                          AVALANCHE                  │
│  PumpPortal WS ──► V6 Collector  Avax RPC Polling           │
│        │                │               │                   │
│        ▼                ▼               ▼                   │
│   Helius RPC       RugCheck API    SnowTrace API             │
│        │                │               │                   │
│        └────────────────┴───────────────┘                   │
│                         │                                   │
│                         ▼                                   │
│           CIA V4 + V5 + V6 Intelligence Engine              │
│  (Funding / Latency / Entropy / Wash / Cluster /            │
│   Jito / Stylometry / CEX Sweep / Lifecycle /               │
│   Backdoor / Concentration / Velocity / Serial Rugger)      │
│                         │                                   │
│                         ▼                                   │
│              syndicate_train_v6.jsonl                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

```bash
pip install websockets requests
```

### Solana — Real-time Collection (V6)
```bash
python dataset_collector_v6.py
```

### Solana — Scan Single Token
```bash
python dataset_collector_v6.py <mint_address>
```

### Avalanche — Real-time Collection
```bash
python avax_collector.py
```

### Avalanche — Historical Bulk Scan
```bash
python avax_historical.py
```

---

## 📁 Repository Structure

```
syndicate_core/
├── dataset_collector_v6.py        # ACTIVE — Solana V6 + full CIA engine
├── dataset_collector_v5.py        # Previous version
├── dataset_collector_v4.py        # Previous version
├── avax_collector.py              # Avax C-Chain real-time polling
├── avax_historical.py             # Avax historical bulk scanner
├── analyze_dataset.py             # Dataset quality analysis
├── convert_solrpds.py             # SolRPDS academic dataset converter
└── workspace/syndicate_model/     # LoRA adapter 246MB (private)
```

---

## 🗺️ Roadmap

- [x] Real-time WebSocket ingestion (Solana)
- [x] RugCheck API integration
- [x] 150K+ record dataset milestone
- [x] SolRPDS academic dataset (116K records)
- [x] Fine-tuning: Qwen3-14B QLoRA (train loss 1.227)
- [x] GGUF model published (ffurduj/syndicate-gguf)
- [x] CIA Intelligence Engine V4 (5 behavioral modules)
- [x] Multi-chain: Avalanche C-Chain support
- [x] V5: Jito bundles, name stylometry, CEX sweep tracking
- [x] V6: Contract backdoor, holder concentration, rug velocity, serial rugger DB
- [x] 358K+ historical Solana scan corpus milestone
- [x] Telegram alerts ([@RugBusterAlerts](https://t.me/RugBusterAlerts))
- [x] Portfolio scan — wallet connect + risk scoring
- [x] Live Solana V6 collector persisted to PostgreSQL
- [ ] REST API for real-time scoring
- [ ] Token Extensions vulnerability detection
- [ ] Evaluation harness (F1 score vs RugCheck baseline)
- [ ] V2 fine-tuning run (target: 500K+ records)

---

## 📡 Community & Alerts

| | |
|---|---|
| 💬 **Telegram Alerts** | [t.me/RugBusterAlerts](https://t.me/RugBusterAlerts) |
| 🔔 **Avax Alerts** | [t.me/RugBusterAvax](https://t.me/RugBusterAvax) |
| 🤖 **Telegram Bot** | [@RugBusterBot_bot](https://t.me/RugBusterBot_bot) |
| 🌐 **Website** | [rugbuster.io](https://rugbuster.io) |
| 🤗 **Model** | [ffurduj/syndicate-gguf](https://huggingface.co/ffurduj/syndicate-gguf) |
| 💻 **GitHub** | [rugbusteraipatrol](https://github.com/rugbusteraipatrol) |

---

## 💼 Business Model

| Tier | Target | Price |
|------|--------|-------|
| Operator | Retail traders | $19/month |
| Ghost | Power users | $79/month |
| B2B API | Wallet providers (Phantom, Solflare) | $2,000–5,000/month |
| Dataset License | Analytics firms | $10,000–50,000/year |

---

## License

MIT — open source, use freely, attribution appreciated.
