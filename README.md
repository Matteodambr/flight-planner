# flight-planner

Find the cheapest way for two (or more) people, leaving from different airports, to meet in the same city on the same days and fly home again (possibly to a different airport). Every candidate city and every date window within your flexibility range is searched, filtered by your time and stop rules, and ranked.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Use

1. Edit [`config.yaml`](config.yaml): dates, flexibility, cities, travelers, time rules, filters. Every option is commented there.
2. Check how many searches it will do:
   ```bash
   .venv/bin/python -m flight_planner --dry-run
   ```
3. Run it:
   ```bash
   .venv/bin/python -m flight_planner            # or: -c other-config.yaml
   ```
4. Open `results/<timestamp>/SUMMARY.md`. A Markdown previewer such as VS Code's (Ctrl+Shift+V) renders the tables and links.

Results are cached for `cache_ttl_hours`. Re-running after changing only the ranking, or after adding a city, is fast. Use `--no-cache` to force fresh prices.

## Output

```
results/2026-10-18_105716/
  SUMMARY.md          best city per window, city × window price matrix, top 20 overall, failed searches
  all_combos.csv      every combination found, for spreadsheet filtering
  01n_minus1/         windows one night shorter than requested
  02n_nominal/        windows with the requested length (incl. the exact requested dates, marked NOMINAL)
    02n__out-2026-11-19_Thu__ret-2026-11-21_Sat.md / .csv
    02n__out-2026-11-20_Fri__ret-2026-11-22_Sun__NOMINAL.md / .csv
    ...
  03n_plus1/  04n_plus2/ ...
```

Each window file ranks the cities. For each city it shows:
- the best combination, leg by leg: times, stops and layovers, airline, price
- links to the same search on Google Flights and on Skyscanner
- the next-best alternatives

It also shows:
- **Together**: time from when the second person lands until the first person leaves.
- **Arrival gap**: how far apart the two of you land.

## How flexibility works

With `flex_out: 1` and `flex_ret: 1`, every outbound day in {−1, 0, +1} is combined with every return day in {−1, 0, +1}. This covers both cases: moving the whole trip (same length) and moving only one end (longer or shorter trip). Both travelers always fly on the same outbound day and the same return day. Each one-way leg is searched only once and reused across windows, so a wider flexibility range only adds searches for the extra dates.

For one-sided flexibility, use a `[min, max]` range instead of a number. For example, `flex_out: [0, 1]` means the requested day or one day later, and `flex_ret: [-1, 0]` means the requested day or one day earlier.

## Self-transfer

With `hide_self_transfer: false`, connections on separate tickets are allowed, but only when every layover is at least `self_transfer_min_layover_minutes`. A connection counts as self-transfer when a segment is flown by an airline that doesn't sell connecting tickets (Ryanair, Wizz Air, easyJet and similar). These connections are marked ⚠ in the results. In practice, Google Flights rarely includes self-transfer itineraries in the results this tool reads.

## Ranking

- `sort_by: total_price` (default): cheapest total for everyone. Ties are broken by more time together, then by less travel time.
- `sort_by: score`: price + a penalty per travel hour + a penalty per stop − a bonus per hour together, with weights set in `score_weights`.
- Optional hard limits: `max_arrival_gap_hours` and `max_departure_gap_hours`.

## Will prices match Skyscanner?

Not exactly. Prices come from **Google Flights**, because Skyscanner has no public API for individuals and blocks scraping. The two are usually close for fares bought directly from airlines, but they can differ:

- Skyscanner lists more online travel agencies, which are sometimes cheaper and sometimes less reliable.
- Prices change constantly and each site caches them differently.
- One-way legs are summed. A return ticket on a traditional airline is occasionally cheaper than two one-ways.
- Bag fees: set `carry_on_bags` and `checked_bags` so that low-cost airline prices include them.

Use the Skyscanner and Google links in each file to confirm a price before booking.

## Caveats

- The tool uses [fast-flights](https://github.com/AWeirdDev/flights), an unofficial Google Flights scraper. It can break if Google changes its page. Failed searches are listed in `SUMMARY.md`, and the affected cities appear as "no result".
- Keep `delay_seconds` reasonable. Running hundreds of searches quickly may get you temporarily blocked. Set `search.proxy` if needed.
- Times are local to each airport. `latest_arrival` is relative to the departure date, so `"25:30"` means 01:30 the next day.

## Tests

```bash
.venv/bin/pytest
```
