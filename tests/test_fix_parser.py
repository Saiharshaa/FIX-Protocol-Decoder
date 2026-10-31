from pathlib import Path

import pandas as pd
import pytest

from fix_parser import SOH, FIXParser, build_message, compute_checksum, main

SAMPLES = Path(__file__).resolve().parent.parent / 'samples'

# A small New Order Single used by most tests (header + body, no 8/9/10)
ORDER = [('35', 'D'), ('49', 'CLIENT'), ('56', 'BROKER'), ('34', '7'),
         ('52', '20261029-13:30:00.000'), ('11', 'ORD_42'), ('55', 'NVDA'),
         ('54', '1'), ('38', '100'), ('40', '2'), ('44', '450.00'), ('59', '0')]

# Same message written out by hand. Its BodyLength and CheckSum were cross-checked
# against the independent simplefix library when this test was written.
ORDER_PIPE = ("8=FIX.4.2|9=105|35=D|49=CLIENT|56=BROKER|34=7|52=20261029-13:30:00.000|"
              "11=ORD_42|55=NVDA|54=1|38=100|40=2|44=450.00|59=0|10=053|")


@pytest.fixture
def parser():
    return FIXParser()


def read_sample(name):
    return (SAMPLES / name).read_text(encoding='latin-1').splitlines()


def with_new_checksum(msg):
    """Recomputes 10= after a test edits a SOH message, so only the edit is wrong."""
    cut = msg.rfind(SOH + '10=') + 1
    return msg[:cut] + '10=' + compute_checksum(msg[:cut].encode('latin-1')) + SOH


# --- Decoding ---

def test_tag_names(parser):
    msg = parser.parse_line(ORDER_PIPE)
    assert msg['Symbol'] == 'NVDA'
    assert msg['ClOrdID'] == 'ORD_42'
    assert msg['OrderQty'] == '100'


def test_enum_values(parser):
    msg = parser.parse_line(ORDER_PIPE)
    assert msg['MsgType'] == 'D (New Order)'
    assert msg['Side'] == '1 (Buy)'
    assert msg['OrdType'] == '2 (Limit)'
    assert msg['TimeInForce'] == '0 (Day)'


def test_execution_report_enums(parser):
    msg = parser.parse_line("35=8|150=F|39=1|54=5|")
    assert msg['MsgType'] == '8 (Execution Report)'
    assert msg['ExecType'] == 'F (Trade)'
    assert msg['OrdStatus'] == '1 (Partially Filled)'
    assert msg['Side'] == '5 (Sell Short)'


def test_unknown_tag_and_value_pass_through(parser):
    msg = parser.parse_line("54=Z|9999=abc|")
    assert msg['Side'] == 'Z'
    assert msg['Tag_9999'] == 'abc'


def test_pipe_and_soh_give_same_result(parser):
    pipe_msg = build_message(ORDER, delimiter='|')
    soh_msg = build_message(ORDER, delimiter=SOH)
    assert pipe_msg.replace('|', SOH) == soh_msg
    assert parser.parse_line(pipe_msg) == parser.parse_line(soh_msg)
    assert parser.validate(pipe_msg) == ([], [])
    assert parser.validate(soh_msg) == ([], [])


def test_soh_message_with_pipe_in_text(parser):
    # When SOH is present, a | inside a value is just data
    msg = build_message(ORDER + [('58', 'note | with pipe')], delimiter=SOH)
    assert parser.parse_line(msg)['Text'] == 'note | with pipe'
    assert parser.validate(msg) == ([], [])


# --- Checksum and BodyLength ---

def test_checksum_is_sum_mod_256_three_digits():
    assert compute_checksum(b'A\x01') == '066'          # 65 + 1
    assert compute_checksum(bytes([200, 100])) == '044'  # 300 mod 256


def test_build_message_matches_known_good():
    assert build_message(ORDER, delimiter='|') == ORDER_PIPE


def test_good_message_is_valid(parser):
    assert parser.validate(ORDER_PIPE) == ([], [])


def test_bad_checksum(parser):
    errors, _ = parser.validate(ORDER_PIPE.replace('10=053', '10=054'))
    assert errors == ['CheckSum(10) is 054, expected 053']


def test_checksum_must_be_three_digits(parser):
    errors, _ = parser.validate(ORDER_PIPE.replace('10=053', '10=53'))
    assert errors == ['CheckSum(10) is 53, expected 053']


def test_bad_body_length(parser):
    msg = build_message(ORDER).replace(f'{SOH}9=105{SOH}', f'{SOH}9=106{SOH}')
    errors, _ = parser.validate(with_new_checksum(msg))
    assert errors == ['BodyLength(9) is 106, expected 105']


def test_edited_field_breaks_length_and_checksum(parser):
    # Someone "fixes" the quantity in a log by hand: both 9 and 10 go stale
    errors, _ = parser.validate(ORDER_PIPE.replace('|38=100|', '|38=1000|'))
    assert 'BodyLength(9) is 105, expected 106' in errors
    assert any(e.startswith('CheckSum(10)') for e in errors)


# --- Structure ---

def test_missing_required_header_tag(parser):
    no_time = [f for f in ORDER if f[0] != '52']
    errors, _ = parser.validate(build_message(no_time))
    assert errors == ['missing required tag 52 (SendingTime)']


def test_missing_checksum(parser):
    msg = build_message(ORDER)
    errors, _ = parser.validate(msg[:msg.rfind('10=')])
    assert 'missing required tag 10 (CheckSum)' in errors


def test_field_order(parser):
    # MsgType placed after SenderCompID instead of right after BodyLength
    swapped = [ORDER[1], ORDER[0]] + ORDER[2:]
    errors, _ = parser.validate(build_message(swapped))
    assert errors == ['MsgType(35) must be field 3']


def test_empty_value_and_double_delimiter(parser):
    msg = with_new_checksum(build_message(ORDER).replace(f'{SOH}59=0{SOH}', f'{SOH}59={SOH}{SOH}'))
    errors, _ = parser.validate(msg)
    assert 'tag 59 has no value' in errors
    assert "malformed field '' (expected tag=value)" in errors


def test_unknown_tag_is_a_warning_not_an_error(parser):
    msg = build_message(ORDER + [('9999', 'x')])
    errors, warnings = parser.validate(msg)
    assert errors == []
    assert warnings == ['unknown tag 9999']


# --- Sample logs ---

def test_sample_malformed_message(parser):
    df = parser.decode_lines(read_sample('orders.log'))
    bad = df[~df['valid']]
    assert list(bad['line']) == [7]
    errors = bad.iloc[0]['errors']
    assert "malformed field '55MSFT'" in errors
    assert 'BodyLength(9) is 137, expected 136' in errors
    assert 'CheckSum(10) is 087, expected 026' in errors


def test_sample_logs_rest_are_valid(parser):
    orders = parser.decode_lines(read_sample('orders.log'))
    session = parser.decode_lines(read_sample('session_soh.log'))
    assert orders['valid'].sum() == 6 and len(orders) == 7
    assert session['valid'].all() and len(session) == 8
    # partial fill then fill on the NVDA order
    assert list(orders['OrdStatus'][1:4]) == ['0 (New)', '1 (Partially Filled)', '2 (Filled)']
    # the session log's bad OrdType is only a warning; the Reject message itself decodes
    assert session.iloc[6]['warnings'] == "unknown OrdType(40) value 'Z'"
    assert session.iloc[7]['MsgType'] == '3 (Reject)'


def test_blank_lines_are_skipped_but_line_numbers_kept(parser):
    df = parser.decode_lines(['', ORDER_PIPE + '\n', '   '])
    assert list(df['line']) == [2]


# --- CLI ---

def test_cli_writes_csv(tmp_path, capsys):
    out = tmp_path / 'out.csv'
    assert main([str(SAMPLES / 'orders.log'), '--csv', str(out)]) == 0
    assert '7 messages, 6 valid, 1 invalid' in capsys.readouterr().out
    df = pd.read_csv(out)
    assert len(df) == 7
    assert {'line', 'valid', 'errors', 'warnings', 'MsgType', 'CheckSum'} <= set(df.columns)


def test_cli_missing_file(tmp_path, capsys):
    assert main([str(tmp_path / 'nope.log')]) == 2
    assert 'cannot read' in capsys.readouterr().err


def test_demo_still_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main([]) == 0
    assert '--- Decoded FIX Logs ---' in capsys.readouterr().out
    df = pd.read_csv(tmp_path / 'decoded_fix_logs.csv')
    assert len(df) == 3 and df['valid'].all()
