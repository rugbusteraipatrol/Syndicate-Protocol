# 🛡️ Syndicate Protocol: AI-Native Fraud Detection

> Real-time multi-chain token fraud detection powered by fine-tuned large language models.

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Dataset](https://img.shields.io/badge/Dataset-200K%2B%20Records-blue)
![Model](https://img.shields.io/badge/Model-Qwen3--14B-purple)
![Solana](https://img.shields.io/badge/Chain-Solana-green)
![Avax](https://img.shields.io/badge/Chain-Avalanche-red)

---

## Overview

Syndicate Protocol is an AI-native fraud detection system built for **Solana and Avalanche C-Chain**. It monitors new token deployments in real time, runs multi-layer CIA intelligence analysis on each token, and classifies each as **DANGER**, **WARN**, or **GOOD** — before retail investors are exposed.

Unlike existing tools that rely on static metadata checks, Syndicate Protocol combines:
- **Live on-chain data** via PumpPortal WebSocket (Solana) + Avalanche RPC polling
- **RugCheck API** for verified ground-truth labels (Solana)
- **CIA Intelligence Engine** — 5-layer behavioral analysis invisible to rule-based scanners
- **200,000+ labeled training records** across Solana and Avalanche
- **Fine-tuned LLM** (Qwen3-14B QLoRA) trained to recognize fraud patterns

> 🚧 **Multi-chain expansion in progress** — Avalanche C-Chain support is live. Additional EVM chains coming soon.

---

## 📊 Dataset

| Dataset | Records | Chain | Source |
|---------|---------|-------|--------|
| Live v2 | 63,000+ | Solana | PumpPortal + RugCheck |
| Live v3 | 22,000+ | Solana | PumpPortal + RugCheck + Creator Tracking |
| Academic | 116,304 | Solana | SolRPDS 2021–2024 |
| Avax Historical | 2,238+ | Avalanche | SnowTrace + Avax RPC |
| Avax Real-time | Growing | Avalanche | Avax RPC polling |
| **Total** | **200,000+** | **Multi-chain** | |

Each record contains structured token metadata + CIA intelligence signals paired with a verified risk classification.

### Dataset Format (V4)

```json
{
  "instruction": "Analyze this Solana/Avax token and classify its risk level as DANGER, WARN, or GOOD.",
  "input": "Token: SNAKE EYE (SNAKE)\nChain: SOLANA\nMint Authority: NO\nRugCheck Score: 117601\n--- CIA INTEL ---\nDeployment Latency: 1200ms | Sniped: True\nTransaction Entropy: 0.1 | Bot pattern: True\nWash Pattern: True | Dev sold in 12s\nHolder Cluster: avg age 0.3 days | Bot farm: True",
  "output": "DANGER - High risk token. CIA flags: sniped in 1200ms, bot transactions, wash trading pattern.",
  "label": "DANGER",
  "chain": "SOLANA",
  "cia_sniped": true,
  "cia_bot_pattern": true,
  "cia_wash_detected": true
}
```

---

## 🧠 CIA Intelligence Engine

V4 introduces a 5-layer behavioral analysis system that goes beyond static metadata:

| Module | What it detects |
|--------|----------------|
| **Funding Origin (3-hop)** | Master wallet financing multiple scam deployments |
| **Deployment Latency** | Sniper bots waiting in mempool (< 3 second buys) |
| **Transaction Entropy** | Repetitive buy amounts = bot farm signature |
| **Wash Pattern** | Mint → Dev instant sell → Supply wallet buy sequence |
| **Holder Cluster Age** | 70%+ new wallets (< 7 days old) = coordinated bot farm |

**Label Upgrade Logic:** If RugCheck says GOOD but CIA detects 3+ flags → automatic upgrade to WARN or DANGER.

---

## 🧠 Model

**Base:** Qwen3-14B  
**Method:** QLoRA fine-tuning via Unsloth (RunPod RTX 4090, ~9 hours)  
**Training data:** 184,000+ verified records  
**Train loss:** 1.227  
**GGUF:** [`ffurduj/syndicate-gguf`](https://huggingface.co/ffurduj/syndicate-gguf) on HuggingFace

Fine-tuned to specialize in:
- Rugpull mechanism identification (Freeze Authority, Liquidity Withdrawal, Pump-and-Dump)
- Creator/deployer history pattern recognition
- Wash trading and coordinated bot farm detection
- Multi-chain risk scoring: DANGER / WARN / GOOD

---

## 🏗️ Infrastructure

```
┌─────────────────────────────────────────────────────────┐
│              SYNDICATE PIPELINE v4 — MULTI-CHAIN        │
│                                                         │
│  SOLANA                        AVALANCHE                │
│  PumpPortal WS ──► V4 Collector   Avax RPC Polling      │
│        │               │               │                │
│        ▼               ▼               ▼                │
│   Solana RPC      RugCheck API    SnowTrace API          │
│        │               │               │                │
│        └───────────────┴───────────────┘                │
│                        │                                │
│                        ▼                                │
│              CIA Intelligence Engine                    │
│     (Funding Origin / Latency / Entropy /               │
│      Wash Pattern / Holder Cluster)                     │
│                        │                                │
│                        ▼                                │
│         syndicate_train_v4.jsonl (Solana)               │
│         syndicate_train_avax.jsonl (Avax RT)            │
│         syndicate_train_avax_historical.jsonl           │
└─────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

```bash
pip install websockets requests
```

### Solana — Real-time Collection

```bash
python dataset_collector_v4.py
```

### Solana — Scan Single Token

```bash
python dataset_collector_v4.py <mint_address>
```

### Avalanche — Real-time Collection

```bash
python avax_collector.py
```

### Avalanche — Historical Bulk Scan

```bash
python avax_historical.py
```

### Analyze Dataset

```bash
python analyze_dataset.py
```

---

## 📁 Repository Structure

```
syndicate_core/
├── dataset_collector_v4.py        # Solana live collector + CIA engine
├── avax_collector.py              # Avax C-Chain real-time polling
├── avax_historical.py             # Avax historical bulk scanner
├── dataset_collector_v3.py        # Previous version (creator tracking)
├── analyze_dataset.py             # Dataset quality analysis
├── convert_solrpds.py             # SolRPDS academic dataset converter
├── syndicate_train_v4.jsonl       # Solana V4 + CIA signals (private)
├── syndicate_train_avax.jsonl     # Avax real-time dataset (private)
├── syndicate_academic.jsonl       # 116K SolRPDS records (private)
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
- [x] CIA Intelligence Engine V4 (5-layer behavioral analysis)
- [x] Multi-chain: Avalanche C-Chain support
- [x] ChainSwitcher: auto-detect Solana vs Avax
- [x] 200K+ record dataset milestone
- [x] Telegram alert bot ([@RugBusterAlerts](https://t.me/RugBusterAlerts))
- [ ] REST API for real-time scoring
- [ ] Web scanner connected to fine-tuned model (rugbuster.io)
- [ ] Portfolio scan — wallet connect + risk scoring
- [ ] Evaluation harness (F1 score vs RugCheck baseline)

---

## 📡 Community & Alerts

| | |
|---|---|
| 💬 **Telegram Alerts** | [t.me/RugBusterAlerts](https://t.me/RugBusterAlerts) |
| 🤖 **Telegram Bot** | [@RugBusterBot_bot](https://t.me/RugBusterBot_bot) |
| 🌐 **Website** | [rugbuster.io](https://rugbuster.io) *(coming soon)* |
| 🤗 **Model** | [ffurduj/syndicate-gguf](https://huggingface.co/ffurduj/syndicate-gguf) |

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
