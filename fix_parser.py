# -*- coding: utf-8 -*-
"""FIX Protocol Decoder

Decodes and validates FIX 4.2 / 4.4 messages from raw log lines.
Originally prototyped in Colab (FIX_Parser.ipynb).

Usage:
    python fix_parser.py                                  # demo on the embedded sample logs
    python fix_parser.py samples/orders.log               # decode + validate a log file
    python fix_parser.py samples/orders.log --csv out.csv # ...and save every field to CSV
"""

import argparse
import sys

import pandas as pd

SOH = '\x01'  # The real FIX field delimiter (Start Of Header, byte 0x01)


def to_wire_bytes(text):
    # latin-1 maps each character to exactly one byte, so lengths and
    # checksums are computed over the same bytes that were in the log file
    return text.encode('latin-1', errors='replace')


def compute_checksum(data):
    """
    FIX CheckSum (tag 10): sum of every byte up to and including the SOH
    before "10=", mod 256, written as 3 digits (e.g. 7 -> "007").
    """
    return f"{sum(data) % 256:03d}"


def build_message(fields, begin_string='FIX.4.2', delimiter=SOH):
    """
    Builds a FIX message from (tag, value) pairs (everything except 8, 9 and 10).
    Fills in BodyLength (9) and CheckSum (10) so the message is valid on the wire.
    Used to generate the sample logs and the test messages.
    """
    body = ''.join(f"{tag}={val}{SOH}" for tag, val in fields)
    head = f"8={begin_string}{SOH}9={len(to_wire_bytes(body))}{SOH}"
    checksum = compute_checksum(to_wire_bytes(head + body))
    msg = f"{head}{body}10={checksum}{SOH}"
    return msg.replace(SOH, delimiter)


class FIXParser:
    def __init__(self):
        # 1. The "Dictionary" (Ops Knowledge)
        # In real life, this is an XML spec, but we hardcode common tags here.
        self.tags = {
            # Header / trailer
            '8': 'BeginString',
            '9': 'BodyLength',
            '35': 'MsgType',
            '49': 'SenderCompID',
            '56': 'TargetCompID',
            '34': 'MsgSeqNum',
            '52': 'SendingTime',
            '10': 'CheckSum',
            # Orders
            '11': 'ClOrdID',   # Client Order ID
            '21': 'HandlInst',
            '55': 'Symbol',
            '54': 'Side',
            '38': 'OrderQty',
            '40': 'OrdType',
            '44': 'Price',
            '59': 'TimeInForce',
            '60': 'TransactTime',
            # Execution reports
            '37': 'OrderID',   # Broker's order ID
            '17': 'ExecID',
            '20': 'ExecTransType',  # FIX 4.2 only
            '150': 'ExecType',
            '39': 'OrdStatus',
            '32': 'LastQty',   # called LastShares in FIX 4.2
            '31': 'LastPx',
            '14': 'CumQty',
            '151': 'LeavesQty',
            '6': 'AvgPx',
            '103': 'OrdRejReason',
            '58': 'Text',
            # Session level (logon, reject)
            '98': 'EncryptMethod',
            '108': 'HeartBtInt',
            '45': 'RefSeqNum',
            '371': 'RefTagID',
            '372': 'RefMsgType',
            '373': 'SessionRejectReason',
        }

        # Value maps (Translating codes like "1" to "Buy")
        self.values = {
            '35': {'D': 'New Order', '8': 'Execution Report', '3': 'Reject',
                   'F': 'Cancel Request', '9': 'Cancel Reject',
                   'A': 'Logon', '5': 'Logout', '0': 'Heartbeat'},
            '54': {'1': 'Buy', '2': 'Sell', '5': 'Sell Short'},
            '39': {'0': 'New', '1': 'Partially Filled', '2': 'Filled',
                   '4': 'Canceled', '8': 'Rejected'},
            '150': {'0': 'New', '1': 'Partial Fill', '2': 'Fill',  # 1/2 are FIX 4.2
                    '4': 'Canceled', '8': 'Rejected', 'F': 'Trade'},  # F is FIX 4.4
            '40': {'1': 'Market', '2': 'Limit', '3': 'Stop', '4': 'Stop Limit'},
            '59': {'0': 'Day', '1': 'GTC', '3': 'IOC', '4': 'FOK'},
            '20': {'0': 'New', '1': 'Cancel', '2': 'Correct', '3': 'Status'},
            '103': {'0': 'Broker option', '1': 'Unknown symbol', '2': 'Exchange closed',
                    '3': 'Order exceeds limit', '6': 'Duplicate order'},
            '373': {'0': 'Invalid tag number', '1': 'Required tag missing',
                    '4': 'Tag specified without a value', '5': 'Value is incorrect for this tag',
                    '6': 'Incorrect data format for value', '11': 'Invalid MsgType'},
        }

        # Every message must carry these (standard header + trailer)
        self.required_tags = ['8', '9', '35', '49', '56', '34', '52', '10']

    def split_fields(self, raw_fix):
        """
        Splits a raw FIX string into (tag, value) pairs.
        Returns (fields, delimiter, bad_chunks) where bad_chunks are pieces
        that are not in tag=value form.
        """
        # Raw engine logs use SOH; logs copied out of a viewer usually show | instead
        delimiter = SOH if SOH in raw_fix else '|'

        # A message normally ends with a delimiter after 10=xxx, so drop that one
        if raw_fix.endswith(delimiter):
            raw_fix = raw_fix[:-1]

        fields, bad_chunks = [], []
        for chunk in raw_fix.split(delimiter):
            tag, sep, val = chunk.partition('=')
            if sep and tag.isascii() and tag.isdigit():
                fields.append((tag, val))
            else:
                bad_chunks.append(chunk)  # e.g. "55MSFT" or "" from a double delimiter
        return fields, delimiter, bad_chunks

    def decode_value(self, tag, val):
        # Get the English Value (e.g., 54=1 -> Buy)
        # Check if this tag has a value map
        if tag in self.values and val in self.values[tag]:
            return f"{val} ({self.values[tag][val]})"
        return val

    def parse_line(self, raw_fix):
        """
        Parses a single raw FIX string into a readable dictionary.
        Delimiter can be | or SOH (0x01)
        """
        fields, _, _ = self.split_fields(raw_fix)
        parsed_msg = {}

        for tag, val in fields:
            # Get the English Tag Name (e.g., 55 -> Symbol)
            tag_name = self.tags.get(tag, f"Tag_{tag}")
            parsed_msg[tag_name] = self.decode_value(tag, val)

        return parsed_msg

    def validate(self, raw_fix):
        """
        Checks one raw FIX message against the basic FIX framing rules.
        Returns (errors, warnings). Errors make the message invalid;
        warnings are tags/values our small dictionary does not know.
        """
        fields, delimiter, bad_chunks = self.split_fields(raw_fix)
        errors, warnings = [], []
        tags = [tag for tag, _ in fields]
        values = dict(fields)

        # 1. Every field must look like tag=value, with a value
        for chunk in bad_chunks:
            errors.append(f"malformed field {chunk!r} (expected tag=value)")
        for tag, val in fields:
            if val == '':
                errors.append(f"tag {tag} has no value")

        # 2. Required header / trailer tags
        for tag in self.required_tags:
            if tag not in values:
                errors.append(f"missing required tag {tag} ({self.tags[tag]})")

        # 3. Field order: 8, 9, 35 must come first and 10 must be last
        for pos, tag in enumerate(['8', '9', '35']):
            if tag in values and (len(tags) <= pos or tags[pos] != tag):
                errors.append(f"{self.tags[tag]}({tag}) must be field {pos + 1}")
        if '10' in values and tags[-1] != '10':
            errors.append("CheckSum(10) must be the last field")
        if '34' in values and not values['34'].isdigit():
            errors.append(f"MsgSeqNum(34) is not a number: {values['34']!r}")

        # 4. BodyLength and CheckSum, computed over the message as it was sent.
        # Pipe logs are SOH logs with | swapped in for display, so swap it back first.
        wire = to_wire_bytes(raw_fix.replace(delimiter, SOH))
        trailer = wire.rfind(b'\x0110=')  # the SOH just before 10=
        if trailer != -1:
            # BodyLength counts from after the 9=... field up to and including that SOH
            tag9 = wire.find(b'\x019=')
            if tag9 != -1 and '9' in values:
                body_start = wire.find(b'\x01', tag9 + 1) + 1
                actual_len = trailer + 1 - body_start
                declared = values['9']
                if not declared.isdigit() or int(declared) != actual_len:
                    errors.append(f"BodyLength(9) is {declared}, expected {actual_len}")

            expected = compute_checksum(wire[:trailer + 1])
            if values.get('10') != expected:
                errors.append(f"CheckSum(10) is {values.get('10')}, expected {expected}")

        # 5. Things our dictionary does not know (not fatal: FIX allows custom tags)
        if values.get('8') not in (None, 'FIX.4.2', 'FIX.4.4'):
            warnings.append(f"BeginString {values['8']!r} is not FIX.4.2 or FIX.4.4")
        for tag, val in fields:
            if tag not in self.tags:
                warnings.append(f"unknown tag {tag}")
            elif tag in self.values and val not in self.values[tag]:
                warnings.append(f"unknown {self.tags[tag]}({tag}) value {val!r}")

        return errors, warnings

    def decode_lines(self, lines):
        """
        Decodes and validates raw log lines (one message per line).
        Returns a DataFrame with one row per message, plus
        line / valid / errors / warnings columns.
        """
        rows = []
        for line_no, line in enumerate(lines, start=1):
            line = line.strip()
            if not line:
                continue  # skip blank lines

            errors, warnings = self.validate(line)
            row = {'line': line_no, 'valid': not errors}
            row.update(self.parse_line(line))
            row['errors'] = '; '.join(errors)
            row['warnings'] = '; '.join(warnings)
            rows.append(row)

        if not rows:
            return pd.DataFrame(columns=['line', 'valid', 'errors', 'warnings'])

        df_log = pd.DataFrame(rows)

        # Reorder columns to put important stuff first (Ops Logic)
        cols = ['line', 'valid', 'MsgType', 'Symbol', 'Side', 'OrderQty', 'Price', 'OrdStatus']
        # Only select cols that exist in the data
        existing_cols = [c for c in cols if c in df_log.columns]
        last_cols = ['errors', 'warnings']  # validation results go at the end
        remaining_cols = [c for c in df_log.columns if c not in existing_cols + last_cols]

        return df_log[existing_cols + remaining_cols + last_cols]


# --- REPORTING ---

# The columns an ops person checks first; everything else goes to the CSV
SUMMARY_COLS = ['line', 'valid', 'MsgType', 'ClOrdID', 'Symbol', 'Side',
                'OrderQty', 'Price', 'OrdStatus', 'CumQty', 'Text']


def print_report(df, source):
    n_valid = int(df['valid'].sum())
    print(f"--- {source}: {len(df)} messages, {n_valid} valid, {len(df) - n_valid} invalid ---")
    if len(df) == 0:
        return

    cols = [c for c in SUMMARY_COLS if c in df.columns]
    print(df[cols].fillna('').to_string(index=False))

    # List every problem under the table so nothing gets cut off
    problems = []
    for _, row in df.iterrows():
        for kind in ['errors', 'warnings']:
            for msg in filter(None, row[kind].split('; ')):
                label = 'ERROR' if kind == 'errors' else 'warning'
                problems.append(f"  line {row['line']}: {label}: {msg}")
    if problems:
        print("\nProblems:")
        print('\n'.join(problems))


# --- MAIN EXECUTION ---

def run_demo(csv_path='decoded_fix_logs.csv'):
    # 1. Initialize the Parser
    decoder = FIXParser()

    # 2. Dummy Data (Raw Logs you might see in Ops)
    raw_logs = [
        "8=FIX.4.2|9=106|35=D|49=CLIENT|56=BROKER|34=1|52=20261029-13:30:00.000|11=ORD_001|55=NVDA|54=1|38=100|40=2|44=450.00|59=0|10=091|",
        "8=FIX.4.2|9=102|35=8|49=BROKER|56=CLIENT|34=1|52=20261029-13:30:00.015|11=ORD_001|150=0|39=0|55=NVDA|38=100|44=450.00|10=167|",
        "8=FIX.4.2|9=78|35=3|49=BROKER|56=CLIENT|34=2|52=20261029-13:30:01.002|45=2|58=Unknown Symbol|10=091|",
    ]

    # 3. Process the Logs (decode + validate) into a DataFrame
    df_final = decoder.decode_lines(raw_logs)

    # 4. Display
    print("--- Decoded FIX Logs ---")
    print(df_final.to_string())

    # 5. Save to CSV
    df_final.to_csv(csv_path, index=False)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Decode and validate FIX 4.2/4.4 messages (one per line, | or SOH delimited).")
    parser.add_argument('logfile', nargs='?',
                        help="FIX log file to decode. Leave out to run the built-in demo.")
    parser.add_argument('--csv', metavar='OUT',
                        help="also save every decoded field to this CSV file")
    args = parser.parse_args(argv)

    # No file given: keep the original demo behaviour
    if args.logfile is None:
        run_demo(args.csv or 'decoded_fix_logs.csv')
        return 0

    decoder = FIXParser()
    try:
        with open(args.logfile, encoding='latin-1') as f:
            df_final = decoder.decode_lines(f)
    except OSError as e:
        print(f"error: cannot read {args.logfile}: {e.strerror}", file=sys.stderr)
        return 2

    print_report(df_final, args.logfile)

    if args.csv:
        df_final.to_csv(args.csv, index=False)
        print(f"\nSaved {len(df_final)} rows to {args.csv}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
