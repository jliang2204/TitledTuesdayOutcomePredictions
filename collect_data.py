"""Collect complete Titled Tuesday events from Chess.com's Published Data API."""

import argparse
import csv
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE = "https://api.chess.com/pub/"


class Client:
    def __init__(self, directory, refresh=False):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.refresh = refresh
        self.sources = {}

    def get(self, url):
        if not url.startswith(BASE):
            raise ValueError(f"Unexpected API URL: {url}")
        path = self.directory / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        if path.exists() and not self.refresh:
            record = json.loads(path.read_text(encoding="utf-8"))
        else:
            for attempt in range(5):
                try:
                    request = urllib.request.Request(
                        url, headers={"User-Agent": "ChessOutcomeResearch/0.1 (educational take-home project)"}
                    )
                    with urllib.request.urlopen(request, timeout=60) as response:
                        payload = json.load(response)
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code != 429 and exc.code < 500:
                        raise
                    if attempt == 4:
                        raise
                    time.sleep(min(60, max(2 ** attempt, int(exc.headers.get("Retry-After", "0")))))
                except (urllib.error.URLError, TimeoutError):
                    if attempt == 4:
                        raise
                    time.sleep(2 ** attempt)
            record = {"url": url, "fetched_at": datetime.now(timezone.utc).isoformat(), "data": payload}
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        self.sources[url] = {"file": str(path), "fetched_at": record["fetched_at"]}
        return record["data"]


def headers(pgn):
    return dict(re.findall(r'^\[(\w+) "(.*)"\]$', pgn, re.MULTILINE))


def collect(start, end, output, seeds, refresh=False):
    client = Client(output / "raw", refresh)
    expected = set()
    day = start
    while day <= end:
        if day.weekday() == 1:
            expected.add(day.isoformat())
        day += timedelta(days=1)
    months = sorted({(date.fromisoformat(d).year, date.fromisoformat(d).month) for d in expected})
    events = set()
    # Seeds discover event IDs only; every round/group is then fetched in full.
    for seed in seeds:
        for year, month in months:
            archive = client.get(f"{BASE}player/{seed.lower()}/games/{year}/{month:02d}")
            for game in archive["games"]:
                tournament = game.get("tournament", "")
                tags = headers(game.get("pgn", ""))
                played = tags.get("UTCDate", "").replace(".", "-")
                if "titled-tuesday" in tournament.lower() and played in expected:
                    events.add(tournament)
    rows = {}
    event_reports = []
    for url in sorted(events):
        event = client.get(url)
        event_day = datetime.fromtimestamp(event["start_time"], timezone.utc).date().isoformat()
        if event_day not in expected:
            raise ValueError(f"Event outside requested Tuesdays: {url}")
        if event["status"] != "finished":
            raise ValueError(f"Event is not finished: {url}")
        rounds = event["rounds"]
        if len(rounds) != event["settings"]["total_rounds"]:
            raise ValueError(f"Incomplete round listing: {url}")
        counts = {}
        for position, round_url in enumerate(rounds, 1):
            round_number = int(round_url.rsplit("/", 1)[1])
            if round_number != position or not round_url.startswith(url + "/"):
                raise ValueError(f"Unexpected round URL or ordering: {round_url}")
            round_data = client.get(round_url)
            count = 0
            if not round_data["groups"]:
                raise ValueError(f"Missing groups: {round_url}")
            for group_url in round_data["groups"]:
                group = client.get(group_url)
                for game in group["games"]:
                    tags = headers(game["pgn"])
                    result = tags.get("Result")
                    if result not in {"1-0", "0-1", "1/2-1/2"}:
                        raise ValueError(f"Unfinished or unknown result: {game['url']}")
                    if game.get("rules") != "chess":
                        raise ValueError(f"Unexpected chess variant: {game['url']}")
                    started = datetime.strptime(tags["UTCDate"] + " " + tags["UTCTime"], "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    if started.date().isoformat() != event_day:
                        raise ValueError(f"Game/event date mismatch: {game['url']}")
                    row = {
                        "game_url": game["url"], "tournament_url": url,
                        "event_date": event_day, "round": round_number,
                        "start_time_utc": started.isoformat(),
                        "white": game["white"]["username"].lower(),
                        "black": game["black"]["username"].lower(),
                        "white_rating": int(tags["WhiteElo"]),
                        "black_rating": int(tags["BlackElo"]),
                        "time_control": game["time_control"],
                        "rated": game["rated"],
                        "white_outcome": {"1-0": "win", "0-1": "loss", "1/2-1/2": "draw"}[result],
                    }
                    if game["url"] in rows and rows[game["url"]] != row:
                        raise ValueError(f"Conflicting duplicate: {game['url']}")
                    rows[game["url"]] = row
                    count += 1
            if not count:
                raise ValueError(f"Empty round: {round_url}")
            counts[str(round_number)] = count
        event_reports.append({"url": url, "date": event_day, "name": event["name"], "games_by_round": counts})
        print(f"{event_day}: {sum(counts.values())} games across {len(counts)} rounds", flush=True)
    missing = sorted(expected - {e["date"] for e in event_reports})
    if missing:
        raise ValueError(f"No event discovered for {missing}. Add discovery seeds with --seeds; cached progress is preserved.")
    if not rows:
        raise ValueError("No games collected")
    ordered = sorted(rows.values(), key=lambda r: (r["start_time_utc"], r["game_url"]))
    with (output / "games.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ordered[0]))
        writer.writeheader()
        writer.writerows(ordered)
    report = {
        "start": start.isoformat(), "end": end.isoformat(), "date_basis": "UTC, inclusive",
        "discovery_seeds": seeds, "expected_tuesdays": sorted(expected),
        "events": event_reports, "unique_games": len(rows),
        "unique_players": len({r[c] for r in ordered for c in ("white", "black")}),
        "outcomes": dict(Counter(r["white_outcome"] for r in ordered)),
        "sources": client.sources,
    }
    (output / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("unique_games", "unique_players", "outcomes")}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 9, 22))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 20))
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument("--seeds", nargs="+", default=["hikaru", "fairchess_on_youtube"])
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("--start must be on or before --end")
    collect(args.start, args.end, args.output, args.seeds, args.refresh)


if __name__ == "__main__":
    main()
