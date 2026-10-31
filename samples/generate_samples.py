"""
Generates the synthetic sample logs in this folder.

All messages are made up (generic CLIENT / BROKER CompIDs) and built with
build_message(), so BodyLength (9) and CheckSum (10) are correct - except
for the one message in orders.log that is broken on purpose.

Run from the repo root:  python samples/generate_samples.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # so we can import fix_parser from the repo root

from fix_parser import SOH, build_message  # noqa: E402

DAY = '20261029'


def ts(time):
    # SendingTime / TransactTime format: YYYYMMDD-HH:MM:SS.sss (UTC)
    return f"{DAY}-{time}"


def client_order(seq, time, clordid, symbol, side, qty, ord_type, price=None, fix44=False):
    fields = [('35', 'D'), ('49', 'CLIENT'), ('56', 'BROKER'), ('34', seq), ('52', ts(time)),
              ('11', clordid)]
    if not fix44:
        fields.append(('21', '1'))  # HandlInst is required in FIX 4.2
    fields += [('55', symbol), ('54', side), ('60', ts(time)), ('38', qty), ('40', ord_type)]
    if price is not None:
        fields.append(('44', price))
    fields.append(('59', '0'))
    return fields


def broker_exec(seq, time, order_id, clordid, exec_id, exec_type, ord_status,
                symbol, side, qty, last_qty, last_px, leaves, cum, avg_px, extra=(), fix44=False):
    fields = [('35', '8'), ('49', 'BROKER'), ('56', 'CLIENT'), ('34', seq), ('52', ts(time)),
              ('37', order_id), ('11', clordid), ('17', exec_id)]
    if not fix44:
        fields.append(('20', '0'))  # ExecTransType=New, FIX 4.2 only
    fields += [('150', exec_type), ('39', ord_status), *extra,
               ('55', symbol), ('54', side), ('38', qty)]
    if last_qty is not None:
        fields += [('32', last_qty), ('31', last_px)]
    fields += [('151', leaves), ('14', cum), ('6', avg_px)]
    return fields


def orders_log():
    """FIX 4.2, pipe delimited: one order filled in two pieces, one rejected, one damaged line."""
    msgs = [
        # Buy 1,000 NVDA limit 450.00 -> ack -> partial fill -> fill
        client_order('2', '13:35:02.114', 'ORD_1001', 'NVDA', '1', '1000', '2', '450.00'),
        broker_exec('2', '13:35:02.131', 'BRK_5001', 'ORD_1001', 'EXE_9001', '0', '0',
                    'NVDA', '1', '1000', '0', '0', '1000', '0', '0'),
        broker_exec('3', '13:35:04.502', 'BRK_5001', 'ORD_1001', 'EXE_9002', '1', '1',
                    'NVDA', '1', '1000', '400', '449.95', '600', '400', '449.95'),
        broker_exec('4', '13:35:09.877', 'BRK_5001', 'ORD_1001', 'EXE_9003', '2', '2',
                    'NVDA', '1', '1000', '600', '450.00', '0', '1000', '449.98'),
        # Sell 200 of a symbol the broker does not know -> rejected
        client_order('3', '13:36:15.020', 'ORD_1002', 'XYZQ', '2', '200', '2', '12.50'),
        broker_exec('5', '13:36:15.034', 'NONE', 'ORD_1002', 'EXE_9004', '8', '8',
                    'XYZQ', '2', '200', '0', '0', '0', '0', '0',
                    extra=[('103', '1'), ('58', 'Unknown symbol')]),
    ]
    lines = [build_message(m, 'FIX.4.2', '|') for m in msgs]

    # Deliberately malformed: the '=' of the Symbol field was lost, so the field
    # is unreadable and the stated BodyLength and CheckSum no longer match
    good = build_message(client_order('4', '13:37:41.300', 'ORD_1003', 'MSFT', '1', '100',
                                      '2', '415.00'), 'FIX.4.2', '|')
    lines.append(good.replace('|55=MSFT|', '|55MSFT|'))
    return lines


def session_log():
    """FIX 4.4, raw SOH delimited: logon, an order filled in two trades, a bad order + session reject."""
    msgs = [
        [('35', 'A'), ('49', 'CLIENT'), ('56', 'BROKER'), ('34', '1'), ('52', ts('13:29:58.002')),
         ('98', '0'), ('108', '30')],
        [('35', 'A'), ('49', 'BROKER'), ('56', 'CLIENT'), ('34', '1'), ('52', ts('13:29:58.019')),
         ('98', '0'), ('108', '30')],
        # Buy 300 MSFT at market -> ack -> two trades (ExecType F in FIX 4.4)
        client_order('2', '13:40:11.450', 'ORD_2001', 'MSFT', '1', '300', '1', fix44=True),
        broker_exec('2', '13:40:11.466', 'BRK_7001', 'ORD_2001', 'EXE_8001', '0', '0',
                    'MSFT', '1', '300', None, None, '300', '0', '0', fix44=True),
        broker_exec('3', '13:40:11.903', 'BRK_7001', 'ORD_2001', 'EXE_8002', 'F', '1',
                    'MSFT', '1', '300', '100', '415.20', '200', '100', '415.20', fix44=True),
        broker_exec('4', '13:40:12.288', 'BRK_7001', 'ORD_2001', 'EXE_8003', 'F', '2',
                    'MSFT', '1', '300', '200', '415.25', '0', '300', '415.2333', fix44=True),
        # Client sends OrdType=Z (not a valid value) -> broker answers with a session Reject
        client_order('3', '13:41:05.010', 'ORD_2002', 'MSFT', '2', '100', 'Z', '416.00', fix44=True),
        [('35', '3'), ('49', 'BROKER'), ('56', 'CLIENT'), ('34', '5'), ('52', ts('13:41:05.024')),
         ('45', '3'), ('371', '40'), ('372', 'D'), ('373', '5'),
         ('58', 'Invalid value for OrdType(40)')],
    ]
    return [build_message(m, 'FIX.4.4', SOH) for m in msgs]


def write_log(name, lines):
    # newline='\n' keeps the files identical on Windows and Linux
    with open(HERE / name, 'w', encoding='latin-1', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    print(f"wrote {len(lines)} messages to samples/{name}")


if __name__ == '__main__':
    write_log('orders.log', orders_log())
    write_log('session_soh.log', session_log())
