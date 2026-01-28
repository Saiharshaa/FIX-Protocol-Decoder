# FIX Protocol Decoder

## Overview
A Python-based utility for parsing and validating **Financial Information eXchange (FIX)** messages. Designed for Trading Operations teams to rapidly debug connectivity issues and visualize raw log files.

## Problem Statement
FIX logs are typically stored as raw, delimiter-separated strings (e.g., `35=D|55=AAPL`). Deciphering these manually during a live trading incident is slow and error-prone. This tool automates the translation of tags (Key 35) and values (Value D) into human-readable formats.

## Features
* **Tag Resolution:** Maps standard FIX 4.2/4.4 tags (e.g., `55` -> `Symbol`).
* **Value Translation:** Decodes enumerations (e.g., `Side 54=1` -> `Buy`).
* **Log Ingestion:** Handles both pipe (`|`) and SOH (`\x01`) delimiters.
* **Pandas Integration:** Converts logs into structured DataFrames for filtering and analysis.

## Usage
Simply run the main script to process the embedded sample logs:
```bash
python main.py
