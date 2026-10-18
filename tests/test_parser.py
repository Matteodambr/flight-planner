import json

from flight_planner.providers.google_fast import parse_options


def _seg(a, b, dep, arr, day=20, dur=110):
    s = [None] * 22
    s[3], s[4], s[5], s[6] = a, f"{a} airport", f"{b} airport", b
    s[8], s[10], s[11] = dep, arr, dur
    s[20], s[21] = [2026, 11, day], [2026, 11, day]
    return s


def _item(price, airline, segs):
    flight = [None] * 23
    flight[0], flight[1], flight[2] = "XX", [airline], segs
    return [flight, [[None, price]]]


def _page(best, other):
    payload = [None] * 8
    payload[2], payload[3] = [best], [other]
    return f'<html><script class="ds:1">AF_initDataCallback({{key: \'ds:1\', data:{json.dumps(payload)}, sideChannel: {{}}}});</script></html>'


def test_reads_best_and_other_lists_and_skips_unpriced():
    html = _page(
        [_item(60, "Wizz Air", [_seg("FCO", "LIS", [6, 5], [8, 20])])],
        [_item(151, "TAP", [_seg("FCO", "MAD", [None, 30], [3]), _seg("MAD", "LIS", [5], [6, 15])]),
         [[None, ["X"], [_seg("FCO", "LIS", [9], [11])]], []]],
    )
    opts = parse_options(html)
    assert [o.price for o in opts] == [60, 151]
    assert opts[0].departure.hour == 6 and opts[0].departure.minute == 5
    two = opts[1]
    assert two.stops == 1 and two.departure.minute == 30 and two.layovers_min == [120]
