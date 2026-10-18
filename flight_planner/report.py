"""Write one Markdown + CSV file per date window (grouped in folders by trip length) and a summary."""

from __future__ import annotations

import csv
from pathlib import Path

from .combine import Combo, LegPick, WindowResult, sort_key
from .config import Config
from .models import FlightOption, LegResult
from .windows import Window


# ---------- naming ----------

def length_folder(w: Window) -> str:
    d = w.nights_delta
    suffix = "nominal" if d == 0 else (f"plus{d}" if d > 0 else f"minus{-d}")
    return f"{w.nights:02d}n_{suffix}"


def window_stem(w: Window, cfg: Config) -> str:
    stem = f"{w.nights:02d}n__out-{w.out:%Y-%m-%d_%a}__ret-{w.ret:%Y-%m-%d_%a}"
    return stem + ("__NOMINAL" if w.is_nominal(cfg.trip) else "")


def window_title(w: Window, cfg: Config) -> str:
    d = w.nights_delta
    length = "same length as requested" if d == 0 else f"{d:+d} night{'s' if abs(d) != 1 else ''} vs requested"
    tag = " · ★ requested dates" if w.is_nominal(cfg.trip) else ""
    nights = f"{w.nights} night{'s' if w.nights != 1 else ''}"
    return f"{nights} ({length}) · {w.out:%a %d %b} → {w.ret:%a %d %b %Y}{tag}"


def short_label(w: Window, cfg: Config) -> str:
    star = " ★" if w.is_nominal(cfg.trip) else ""
    return f"{w.out:%a} {w.out.day} {w.out:%b} → {w.ret:%a} {w.ret.day} {w.ret:%b}, {w.nights} nights{star}"


# ---------- formatting ----------

def money(v: float, cfg: Config) -> str:
    return f"{cfg.currency} {v:,.0f}"


def clock(o: FlightOption, which: str) -> str:
    t = o.departure if which == "dep" else o.arrival
    s = f"{t:%H:%M}"
    days = (t.date() - o.departure.date()).days
    return s + (f"+{days}" if days else "")


def hm(minutes: float) -> str:
    h, m = divmod(round(minutes), 60)
    return f"{h}h{m:02d}"


def stops_text(o: FlightOption) -> str:
    if o.stops == 0:
        return "direct"
    lays = ", ".join(f"{a} {hm(m)}" for a, m in zip(o.connection_airports, o.layovers_min))
    st = " ⚠ self-transfer" if o.self_transfer else ""
    return f"{o.stops} stop{'s' if o.stops > 1 else ''} ({lays}){st}"


def flex_text(r: tuple[int, int]) -> str:
    lo, hi = r
    if lo == -hi:
        return f"±{hi} day(s)" if hi else "fixed"
    return f"{lo:+d} to {hi:+d} day(s)"


def leg_compact(p: LegPick, cfg: Config) -> str:
    o = p.option
    s = "" if o.stops == 0 else f", {o.stops} stop{'s' if o.stops > 1 else ''}"
    return f"{o.origin} {clock(o, 'dep')}→{clock(o, 'arr')} {o.destination} {' + '.join(o.operators)}{s} {money(o.price, cfg)}"


def _md_escape(s: str) -> str:
    return s.replace("|", "\\|")


# ---------- per-window files ----------

def _filter_note(cfg: Config) -> str:
    f = cfg.filters
    bags = f"{f.carry_on_bags or 0} carry-on + {f.checked_bags or 0} checked bag(s) included where Google knows the fee"
    sort = "total price" if cfg.ranking.sort_by == "total_price" else "weighted score (see config)"
    names = " + ".join(t.name for t in cfg.travelers)
    return (f"Sorted by **{sort}**. Each person's price is their outbound + return (one adult, one-way fares summed); "
            f"Total = {names}. All in {cfg.currency}; {bags}.")


def operated_by(f: FlightOption) -> str:
    """Per segment: flight number and operating airline, e.g. 'KL 1760 by KLM Cityhopper, KM 395 by KM Malta Airlines'."""
    parts = []
    for s in f.segments:
        op = s.operator or ", ".join(f.airlines)
        parts.append(f"{s.flight_number} by {op}" if s.flight_number else op)
    return ", ".join(parts)


def flight_line(f: FlightOption) -> str:
    """e.g. 'Fri MXP 14:55 → DUB 16:30 (Ryanair)'; '+1' marks arrival on the next day."""
    return (f"{f.departure:%a} {f.origin} {clock(f, 'dep')} → {f.destination} {clock(f, 'arr')} "
            f"({' + '.join(f.operators)})")


def stay(c: Combo, name: str) -> str:
    """Both of a traveler's flights, outbound and return on separate lines of one table cell."""
    o, r = (p.option for p in c.legs[name])
    return f"Out {flight_line(o)}<br>Back {flight_line(r)}"


def split(c: Combo, cfg: Config, sep: str = " · ") -> str:
    """Per-person prices, e.g. 'Matteo EUR 65 · Elena EUR 210'."""
    return sep.join(f"{t.name} {money(c.traveler_price(t.name), cfg)}" for t in cfg.travelers)


def write_window_md(wr: WindowResult, cfg: Config, path: Path) -> None:
    names = [t.name for t in cfg.travelers]
    w = wr.window
    lines = [f"# {window_title(w, cfg)}", "", _filter_note(cfg), ""]

    header = (["#", "City", "Total"] + [f"{n}" for n in names] + [f"{n} flights" for n in names]
              + ["Together", "Arrival gap", "Stops", "Travel time"])
    lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    rank = 0
    for cr in wr.cities:
        b = cr.best
        if b is None:
            continue
        rank += 1
        row = [str(rank), f"[{cr.city}](#{rank}-{_anchor(cr.city)})", f"**{money(b.total_price, cfg)}**"]
        row += [money(b.traveler_price(n), cfg) for n in names]
        row += [stay(b, n) for n in names]
        row += [hm(b.hours_together * 60), hm(b.arrival_gap_h * 60), str(b.stops), hm(b.travel_min)]
        lines.append("| " + " | ".join(row) + " |")
    missing = [cr for cr in wr.cities if cr.best is None]
    if missing:
        lines += ["", "**No result:**", ""]
        lines += [f"- {cr.city}: {_md_escape(cr.problem or '')}" for cr in missing]

    rank = 0
    for cr in wr.cities:
        if cr.best is None:
            continue
        rank += 1
        b = cr.best
        lines += ["", f"## {rank}. {cr.city}", "",
                  f"**{money(b.total_price, cfg)}** total · {hm(b.hours_together * 60)} together · "
                  f"arrivals {hm(b.arrival_gap_h * 60)} apart · departures {hm(b.departure_gap_h * 60)} apart", ""]
        lines += _legs_table(b, cfg)
        alts = cr.combos[1 : cfg.ranking.top_combos_per_city]
        if alts:
            lines += ["", "<details><summary>Alternatives</summary>", "",
                      "| Total | Together | " + " | ".join(f"{n} out | {n} return" for n in names) + " |",
                      "|" + "---|" * (2 + 2 * len(names))]
            for c in alts:
                cells = [money(c.total_price, cfg), hm(c.hours_together * 60)]
                for n in names:
                    o, r = c.legs[n]
                    cells += [leg_compact(o, cfg), leg_compact(r, cfg)]
                lines.append("| " + " | ".join(_md_escape(x) for x in cells) + " |")
            lines += ["", "</details>"]
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _anchor(s: str) -> str:
    return "".join(ch for ch in s.lower().replace(" ", "-") if ch.isalnum() or ch == "-")


def _legs_table(c: Combo, cfg: Config) -> list[str]:
    lines = ["| Traveler | Leg | Date | Route | Depart | Arrive | Duration | Stops | Flights (operated by) | Price | Check |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, (o, r) in c.legs.items():
        for label, p in (("out", o), ("return", r)):
            f = p.option
            lines.append(
                "| " + " | ".join([
                    name, label, f"{f.departure:%a %d %b}", f"{f.origin}→{f.destination}",
                    clock(f, "dep"), clock(f, "arr"), hm(f.duration_min), stops_text(f),
                    _md_escape(operated_by(f)),
                    money(f.price, cfg),
                    f"[Google]({p.google_url}) · [Skyscanner]({p.skyscanner_url})",
                ]) + " |"
            )
    return lines


def combo_row(c: Combo, cfg: Config) -> dict:
    row = {
        "out_date": c.window.out.isoformat(),
        "ret_date": c.window.ret.isoformat(),
        "nights": c.window.nights,
        "nights_vs_requested": c.window.nights_delta,
        "requested_dates": c.window.is_nominal(cfg.trip),
        "city": c.city,
        "total_price": round(c.total_price, 2),
        "score": round(c.score, 1),
        "hours_together": round(c.hours_together, 2),
        "arrival_gap_h": round(c.arrival_gap_h, 2),
        "departure_gap_h": round(c.departure_gap_h, 2),
        "total_stops": c.stops,
        "total_travel_h": round(c.travel_min / 60, 2),
    }
    for name, pair in c.legs.items():
        row[f"{name}_price"] = round(c.traveler_price(name), 2)
        for label, p in zip(("out", "ret"), pair):
            f = p.option
            pre = f"{name}_{label}"
            row |= {
                f"{pre}_route": f"{f.origin}-{f.destination}",
                f"{pre}_depart": f.departure.isoformat(sep=" ", timespec="minutes"),
                f"{pre}_arrive": f.arrival.isoformat(sep=" ", timespec="minutes"),
                f"{pre}_duration_h": round(f.duration_min / 60, 2),
                f"{pre}_stops": f.stops,
                f"{pre}_via": " ".join(f.connection_airports),
                f"{pre}_airlines": ", ".join(f.airlines),
                f"{pre}_operated_by": " + ".join(f.operators),
                f"{pre}_flights": " ".join(n.replace(" ", "") for n in f.flight_numbers),
                f"{pre}_self_transfer": f.self_transfer,
                f"{pre}_price": f.price,
                f"{pre}_skyscanner": p.skyscanner_url,
                f"{pre}_google": p.google_url,
            }
    return row


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        path.write_text("no results\n", encoding="utf-8")
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)


# ---------- summary ----------

def dates_label(w: Window, cfg: Config) -> str:
    """e.g. 'Fri 4 Dec → Mon 7 Dec ★' (★ = the dates you asked for)."""
    star = " ★" if w.is_nominal(cfg.trip) else ""
    return f"{w.out:%a} {w.out.day} {w.out:%b} → {w.ret:%a} {w.ret.day} {w.ret:%b}{star}"


def nights_label(w: Window) -> str:
    n = f"{w.nights} night{'s' if w.nights != 1 else ''}"
    return n if w.keeps_duration else f"{n} ({w.nights_delta:+d})"


def _window_link(w: Window, cfg: Config) -> str:
    return f"{length_folder(w)}/{window_stem(w, cfg)}.md"


def combo_table(combos: list[Combo], cfg: Config, *, with_dates: bool = True, link_prefix: str = "") -> list[str]:
    """The one table layout used everywhere: one row per option."""
    names = [t.name for t in cfg.travelers]
    head = ["#"] + (["Dates", "Nights"] if with_dates else []) + ["City", "Total"] + names
    head += [f"{n} flights" for n in names] + ["Together", "Stops", "Flying"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for i, c in enumerate(combos, 1):
        row = [str(i)]
        if with_dates:
            row += [f"[{dates_label(c.window, cfg)}]({link_prefix}{_window_link(c.window, cfg)})", nights_label(c.window)]
        row += [c.city, f"**{money(c.total_price, cfg)}**"]
        row += [money(c.traveler_price(n), cfg) for n in names]
        row += [stay(c, n) for n in names]
        row += [hm(c.hours_together * 60), str(c.stops), hm(c.travel_min)]
        lines.append("| " + " | ".join(row) + " |")
    return lines


def _column_guide(cfg: Config) -> list[str]:
    names = " + ".join(t.name for t in cfg.travelers)
    rk = cfg.ranking
    gaps = []
    if rk.max_arrival_gap_hours is not None:
        gaps.append(f"you land within {rk.max_arrival_gap_hours:g}h of each other")
    if rk.max_departure_gap_hours is not None:
        gaps.append(f"take off within {rk.max_departure_gap_hours:g}h of each other")
    return [
        "**How to read the tables**",
        "",
        f"- **Total** = {names}. Each person's price is their outbound + return flight (one-way fares, {cfg.currency}).",
        "- **flights**: each person's outbound (Out) and return (Back) flight with departure → arrival times "
        "(local) and the operating airline, e.g. `Out Fri MXP 14:55 → DUB 16:30 (Ryanair)`. "
        "`+1` = lands the next day.",
        "- **Together**: from the second person landing until the first person leaves.",
        "- **Stops**: connections over all four flights. **Flying**: total time in the air and at layovers, all flights.",
        "- **Nights**: (−1)/(+1) = shorter/longer than the trip you asked for. ★ = your exact dates. Click the dates for full flight details and booking links.",
        *([f"- Only options where {' and '.join(gaps)} are shown."] if gaps else []),
        "",
    ]


def write_summary(results: list[WindowResult], cfg: Config, out: Path, failed: list[LegResult]) -> None:
    key = sort_key(cfg)
    t = cfg.trip
    sort = "total price" if cfg.ranking.sort_by == "total_price" else "weighted score"
    lines = [
        "# Trip search summary",
        "",
        f"Requested **{t.outbound_date:%a} {t.outbound_date.day} {t.outbound_date:%b} → {t.return_date:%a} {t.return_date.day} {t.return_date:%b %Y}**"
        f" (outbound {flex_text(t.flex_out)}, return {flex_text(t.flex_ret)}, at least {t.min_nights} night(s)). "
        + " · ".join(f"{tr.name}: {'/'.join(tr.from_)} → … → {'/'.join(tr.to)}" for tr in cfg.travelers)
        + f". Everything is sorted by **{sort}**.",
        "",
        *_column_guide(cfg),
        f"Contents: [Top overall](#top-{cfg.ranking.summary_top_overall}-overall) · [Best for each date option](#best-for-each-date-option) · "
        f"[Top by city](#top-{cfg.ranking.summary_top_per_city}-by-city) · [Price matrix](#price-matrix)",
        "",
    ]

    all_combos = sorted((c for wr in results for cr in wr.cities for c in cr.combos), key=key)
    rk = cfg.ranking
    top, seen = [], {}
    for c in all_combos:
        k = (c.city, c.window)
        if seen.get(k, 0) < rk.summary_overall_max_per_city_dates:
            seen[k] = seen.get(k, 0) + 1
            top.append(c)
            if len(top) == rk.summary_top_overall:
                break
    lines += [f"## Top {rk.summary_top_overall} overall", "",
              f"At most {rk.summary_overall_max_per_city_dates} per city and date option, so other cities "
              "aren't crowded out by small variations of the same trip (see Top by city for more).", ""]
    lines += combo_table(top, cfg)

    lines += ["", "## Best for each date option", ""]
    best_per_window = [cr.best for wr in results for cr in wr.cities[:1] if cr.best]
    lines += combo_table(sorted(best_per_window, key=lambda c: (c.window.nights, c.window.out)), cfg)

    lines += ["", f"## Top {rk.summary_top_per_city} by city", ""]
    by_city = {c.name: [] for c in cfg.cities}
    for c in all_combos:
        by_city[c.city].append(c)
    order = sorted(by_city, key=lambda n: (not by_city[n], key(by_city[n][0]) if by_city[n] else ()))
    for name in order:
        lines += [f"### {name}", ""]
        if by_city[name]:
            lines += combo_table(by_city[name][: rk.summary_top_per_city], cfg)
        else:
            reasons = {cr.problem for wr in results for cr in wr.cities if cr.city == name and cr.problem}
            lines.append("No options found. " + _md_escape(" / ".join(sorted(reasons))))
        lines.append("")

    names = [tr.name for tr in cfg.travelers]
    lines += ["## Price matrix", "",
              "Cheapest option for every city and date option: total, then " + " / ".join(names) + ".", "",
              "| City | " + " | ".join(f"{dates_label(wr.window, cfg)}<br>{nights_label(wr.window)}" for wr in results) + " |",
              "|---|" + "---|" * len(results)]
    col_min = [min((cr.best.total_price for cr in wr.cities if cr.best), default=None) for wr in results]
    for name in order:
        cells = []
        for wr, mn in zip(results, col_min):
            cr = next(cr for cr in wr.cities if cr.city == name)
            if cr.best is None:
                cells.append("–")
                continue
            v = money(cr.best.total_price, cfg)
            v = f"**{v}**" if cr.best.total_price == mn else v
            cells.append(v + "<br>" + " / ".join(money(cr.best.traveler_price(n), cfg) for n in names))
        lines.append(f"| {name} | " + " | ".join(cells) + " |")

    if failed:
        lines += ["", "## Searches that failed", "",
                  "These legs could not be fetched; affected cities may be missing. Re-run later (successful searches are cached).", ""]
        lines += [f"- {r.query.from_airport}→{r.query.to_airport} {r.query.day}: {r.error}" for r in failed]

    lines += ["", "---", "Prices come from Google Flights and can differ from Skyscanner; use the links in the detail "
              "files to check before booking. One-way fares are summed: a return ticket on a traditional airline "
              "can occasionally be cheaper."]
    (out / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_all(results: list[WindowResult], cfg: Config, out: Path, failed: list[LegResult]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    all_rows = []
    for wr in results:
        folder = out / length_folder(wr.window)
        folder.mkdir(exist_ok=True)
        stem = window_stem(wr.window, cfg)
        write_window_md(wr, cfg, folder / f"{stem}.md")
        n = cfg.ranking.csv_combos_per_city
        combos = sorted((c for cr in wr.cities for c in cr.combos[:n]), key=sort_key(cfg))
        rows = [{"rank": i, **combo_row(c, cfg)} for i, c in enumerate(combos, 1)]
        write_csv(rows, folder / f"{stem}.csv")
        all_rows += rows
    write_csv([{k: v for k, v in r.items() if k != "rank"} for r in all_rows], out / "all_combos.csv")
    write_summary(results, cfg, out, failed)
