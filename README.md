# FIX Protocol Decoder

A small Python tool for reading **Financial Information eXchange (FIX)** logs during a trading-ops investigation. FIX logs are raw `tag=value` strings (`35=D|55=NVDA|54=1`), and decoding them by hand during a live incident is slow and error-prone. This script translates tags and coded values into plain English (`55` -> `Symbol`, `54=1` -> `Buy`), checks that each message is well-formed (BodyLength, CheckSum, required header fields), and loads the results into a pandas DataFrame or CSV so you can filter them.

## Features

* **Tag resolution:** about 35 common FIX 4.2/4.4 tags across the header, orders, execution reports and session messages.
* **Value translation:** MsgType, Side, OrdType, TimeInForce, OrdStatus, ExecType, ExecTransType, OrdRejReason and SessionRejectReason.
* **Both delimiters:** raw SOH (`\x01`) logs and pipe (`|`) logs copied out of a log viewer.
* **Validation, per message:**
  * BodyLength (9) and CheckSum (10) are recomputed and compared with what the message says. For pipe logs the `|` is swapped back to SOH first, because that is what the checksum was computed over.
  * Required header/trailer tags: 8, 9, 35, 49, 56, 34, 52, 10.
  * Field order: 8, 9 and 35 first, 10 last.
  * Malformed fields: no `=`, a non-numeric tag, an empty value or a double delimiter.
  * Tags or values that are missing from the built-in dictionary are flagged as **warnings**, not errors, since FIX allows custom tags.
* **Output:** a summary table plus a list of problems with line numbers. Optionally, a CSV with every decoded field plus `valid`, `errors` and `warnings` columns.

## Quick start

Requires Python 3.11+.

```bash
pip install -r requirements.txt

python fix_parser.py samples/orders.log                   # decode + validate a log file
python fix_parser.py samples/orders.log --csv decoded.csv # ...and save every field to CSV
python fix_parser.py                                      # original demo: 3 embedded messages -> decoded_fix_logs.csv
```

## Example output

```text
$ python fix_parser.py samples/orders.log --csv decoded.csv
--- samples/orders.log: 7 messages, 6 valid, 1 invalid ---
 line  valid              MsgType  ClOrdID Symbol     Side OrderQty  Price            OrdStatus CumQty           Text
    1   True        D (New Order) ORD_1001   NVDA  1 (Buy)     1000 450.00
    2   True 8 (Execution Report) ORD_1001   NVDA  1 (Buy)     1000                     0 (New)      0
    3   True 8 (Execution Report) ORD_1001   NVDA  1 (Buy)     1000        1 (Partially Filled)    400
    4   True 8 (Execution Report) ORD_1001   NVDA  1 (Buy)     1000                  2 (Filled)   1000
    5   True        D (New Order) ORD_1002   XYZQ 2 (Sell)      200  12.50
    6   True 8 (Execution Report) ORD_1002   XYZQ 2 (Sell)      200                8 (Rejected)      0 Unknown symbol
    7  False        D (New Order) ORD_1003         1 (Buy)      100 415.00

Problems:
  line 7: ERROR: malformed field '55MSFT' (expected tag=value)
  line 7: ERROR: BodyLength(9) is 137, expected 136
  line 7: ERROR: CheckSum(10) is 087, expected 026

Saved 7 rows to decoded.csv
```

Line 7 is broken on purpose: the `=` in `55=MSFT` was lost, so the Symbol cannot be read, and the stated BodyLength and CheckSum no longer match the bytes in the message.

## Sample data

All sample messages are synthetic and use generic CompIDs (`CLIENT` / `BROKER`).

* `samples/orders.log`: FIX 4.2, pipe-delimited. A buy order that is acknowledged, partially filled and then filled; an order rejected for an unknown symbol; and one malformed line.
* `samples/session_soh.log`: FIX 4.4, raw SOH-delimited. A logon, a market order filled in two trades (ExecType `F`), and an order with an invalid OrdType followed by the broker's session-level Reject (`35=3`).

The logs are built by `samples/generate_samples.py` (`python samples/generate_samples.py`), which computes BodyLength and CheckSum per the FIX spec.

## Running the tests

```bash
pip install -r requirements.txt
pytest -q
```

The tests cover tag and value decoding, pipe and SOH delimiters, CheckSum and BodyLength checks (good and bad), required tags, field order, the malformed sample line, and the CLI. GitHub Actions runs them on every push and pull request (Python 3.11).

## Limitations

* The tag dictionary covers common tags, not the full FIX spec.
* Repeating groups are not modelled: if a tag appears more than once, the decoded view keeps the last value.
* Each message is checked on its own. There is no check for sequence-number gaps between messages.

## Project layout

```text
fix_parser.py                 parser, validator and CLI
samples/orders.log            FIX 4.2 pipe log: fills, a reject, one malformed line
samples/session_soh.log       FIX 4.4 SOH log: logon, trades, session Reject
samples/generate_samples.py   rebuilds the two sample logs
tests/test_fix_parser.py      pytest suite
pytest.ini                    test discovery settings
requirements.txt              pinned pandas + pytest
.github/workflows/tests.yml   CI: pytest on push / pull request
```
