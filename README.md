# 🛡️ The Syndicate Protocol: AI-Native Fraud Detection

> **Real-time Solana smart contract analysis powered by fine-tuned large language models.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Dataset](https://img.shields.io/badge/Dataset-2%2C900%2B%20Records-blue)]()
[![Model](https://img.shields.io/badge/Model-Qwen2.5%20Coder%2014B-purple)]()
[![Chain](https://img.shields.io/badge/Chain-Solana-green)]()

---

## Overview

Syndicate Protocol is an AI-native fraud detection system built specifically for the Solana blockchain. It monitors new token deployments in real time, extracts on-chain program data, and runs each contract through a fine-tuned code analysis model to surface rugpull mechanisms, unauthorized mint authorities, drain functions, and access control flaws — before retail investors are exposed.

---

## 📊 Dataset

| Metric | Value |
|---|---|
| **Verified Records** | 2,900+ |
| **Source** | Live Solana mainnet (PumpPortal stream) |
| **Format** | Instruction-tuned JSONL |
| **Schema** | `instruction` / `input` (raw program data) / `output` (structured analysis) |
| **Collection method** | Continuous WebSocket ingestion + LM-assisted labeling |

Each record is a `(program_bytecode, structured_analysis)` pair produced by the data collection pipeline and verified against known scam patterns.

---

## 🧠 Model

**Base:** [Qwen2.5-Coder-14B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-14B-Instruct)

Fine-tuned on the Syndicate dataset to specialize in:
- Rugpull mechanism identification
- Hidden mint / freeze authority detection
- Unauthorized fund drain patterns
- Access control vulnerability classification
- Risk scoring: `LOW` / `MEDIUM` / `HIGH`

---

## 🏗️ Infrastructure

```
┌─────────────────────────────────────────────────┐
│              SYNDICATE PIPELINE                 │
│                                                 │
│  PumpPortal WS  ──►  dataset_collector.py       │
│  (new tokens)         │                         │
│                       ▼                         │
│              Solana RPC (mainnet)               │
│              program data fetch                 │
│                       │                         │
│                       ▼                         │
│           LM Studio — Qwen2.5-Coder-14B        │
│           (local GPU inference)                 │
│                       │                         │
│                       ▼                         │
│           syndicate_train.jsonl                 │
│           (private training dataset)            │
└─────────────────────────────────────────────────┘
```

**Hardware:**
- Local GPU training — no cloud dependency
- LM Studio for GPU-accelerated local inference (AMD GPU)
- Solana public RPC with exponential backoff

**Why local?**
- Training data stays private
- No API rate limits or costs
- Full control over fine-tuning hyperparameters

---

## 🚀 Quick Start

### 1. Prerequisites

```bash
pip install websockets requests
```

- [LM Studio](https://lmstudio.ai) with `Qwen2.5-Coder-14B-Instruct` loaded
- LM Studio Local Server running on `http://localhost:1234`

### 2. Start Data Collection

```bash
python dataset_collector.py
```

The collector will:
1. Connect to PumpPortal WebSocket
2. Subscribe to new token events
3. Fetch program data via Solana RPC
4. Analyze each contract with the local LLM
5. Append results to `syndicate_train.jsonl`

### 3. Dataset Format

```json
{
  "instruction": "Analiziraj Rust kod Solana pametnog ugovora na sumnjive obrasce i prevare.",
  "input": "<raw program data / hex bytecode>",
  "output": "1. Rugpull mehanizmi: ...\n2. Sumnjive funkcije: ...\n3. Kontrola pristupa: ...\n4. Procjena rizika: VISOK"
}
```

---

## 📁 Repository Structure

```
syndicate_core/
├── dataset_collector.py   # Real-time data collection pipeline
├── .gitignore             # Excludes private dataset & model weights
├── LICENSE                # MIT
└── README.md
```

> ⚠️ `syndicate_train.jsonl` and all model weights are excluded from this repository intentionally.

---

## 🗺️ Roadmap

- [x] Real-time WebSocket ingestion pipeline
- [x] LM Studio / local GPU inference integration
- [x] 2,900+ record dataset milestone
- [ ] Fine-tuning script (LoRA / QLoRA on Qwen2.5-14B)
- [ ] Evaluation harness (precision / recall on known scams)
- [ ] REST API wrapper for real-time scoring
- [ ] Explorer integration

---

## License

[MIT](LICENSE) — open source, use freely, attribution appreciated.
