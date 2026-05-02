# 🛡️ Syndicate Protocol: AI-Native Fraud Detection

> Real-time Solana token analysis powered by fine-tuned large language models.

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Dataset](https://img.shields.io/badge/Dataset-150K%2B%20Records-blue)
![Model](https://img.shields.io/badge/Model-Qwen3--14B-purple)
![Chain](https://img.shields.io/badge/Chain-Solana-green)

---

## Overview

Syndicate Protocol is an AI-native fraud detection system built specifically for the Solana blockchain. It monitors new token deployments in real time, analyzes on-chain metadata and behavioral signals, and classifies each token as **DANGER**, **WARN**, or **GOOD** — before retail investors are exposed.

Unlike existing tools that rely on static metadata checks, Syndicate Protocol combines:
- **Live on-chain data** via PumpPortal WebSocket + Solana RPC
- **RugCheck API** for verified ground-truth labels
- **150,000+ labeled training records** including 4 years of historical Solana DeFi data
- **Fine-tuned LLM** trained to recognize fraud patterns invisible to rule-based systems

---

## 📊 Dataset

| Metric | Value |
|--------|-------|
| Total Records | 150,000+ |
| Live Records (v2) | 30,000+ and growing |
| Historical Records | 116,304 (SolRPDS 2021–2024) |
| Source | PumpPortal stream + RugCheck API + SolRPDS academic dataset |
| Format | Instruction-tuned JSONL |
| Labels | DANGER / WARN / GOOD |
| Collection | Continuous WebSocket ingestion + RugCheck API labeling |

Each record contains structured token metadata (name, mint authority, freeze authority, liquidity signals, risk flags) paired with a verified risk classification.

### Dataset Format

```json
{
  "instruction": "Analyze this Solana token and classify its risk level as DANGER, WARN, or GOOD.",
  "input": "Token: SNAKE EYE (SNAKE)\nMint Authority: NO - disabled\nFreeze Authority: NO - disabled\nRugCheck Score: 117601\nRisk Flags: Creator history of rugged tokens",
  "output": "DANGER - High risk token. Risk flags: Creator history of rugged tokens. RugCheck score: 117601.",
  "label": "DANGER"
}
```

> ⚠️ Training datasets and model weights are excluded from this repository intentionally.

---

## 🧠 Model

**Base:** Qwen3-14B  
**Method:** QLoRA fine-tuning via Unsloth  
**Training data:** 150,000+ verified Solana token records

Fine-tuned to specialize in:
- Rugpull mechanism identification (Freeze Authority Abuse, Liquidity Withdrawal, Pump-and-Dump)
- Creator history pattern recognition
- Holder concentration and liquidity risk analysis
- Risk scoring: DANGER / WARN / GOOD

---

## 🏗️ Infrastructure

```
┌─────────────────────────────────────────────────┐
│              SYNDICATE PIPELINE v2              │
│                                                 │
│  PumpPortal WS  ──►  dataset_collector_v2.py   │
│  (new tokens)         │                         │
│                       ▼                         │
│              Solana RPC (mainnet)               │
│              token metadata fetch               │
│                       │                         │
│                       ▼                         │
│           RugCheck API                          │
│           (verified risk labels)                │
│                       │                         │
│                       ▼                         │
│           syndicate_train_v2.jsonl              │
│           (private training dataset)            │
└─────────────────────────────────────────────────┘
```

**Why this approach?**
- RugCheck API provides verified, ground-truth labels — not LLM-generated guesses
- Structured token metadata is interpretable and trainable
- Pipeline runs 24/7, continuously growing the dataset

---

## 🚀 Quick Start

### Prerequisites

```bash
pip install websockets requests
```

### Start Data Collection

```bash
python dataset_collector_v2.py
```

The collector will:
1. Connect to PumpPortal WebSocket
2. Subscribe to new token events
3. Fetch token metadata via Solana RPC
4. Classify each token via RugCheck API
5. Append labeled records to `syndicate_train_v2.jsonl`

### Analyze Your Dataset

```bash
python analyze_dataset.py
```

Shows label distribution, top risk flags, RugCheck score statistics, and fine-tuning readiness assessment.

---

## 📁 Repository Structure

```
syndicate_core/
├── dataset_collector_v2.py   # Live data collection pipeline
├── analyze_dataset.py        # Dataset quality analysis
├── convert_solrpds.py        # SolRPDS academic dataset converter
├── .gitignore
├── LICENSE
└── README.md
```

---

## 🗺️ Roadmap

- [x] Real-time WebSocket ingestion pipeline
- [x] RugCheck API integration for verified labeling
- [x] 30,000+ live record dataset milestone
- [x] SolRPDS academic dataset integration (116K historical records)
- [ ] Fine-tuning script (QLoRA on Qwen3-14B via Unsloth)
- [ ] Evaluation harness (precision / recall on known scams)
- [ ] REST API wrapper for real-time scoring
- [ ] Web dashboard for live token monitoring

---

## License

MIT — open source, use freely, attribution appreciated.
