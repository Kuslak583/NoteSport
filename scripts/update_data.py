#!/usr/bin/env python3
"""Refresh NoteSport's cached season data from official and credited sources."""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CZ = ZoneInfo("Europe/Prague")
HEADERS = {"User-Agent": "NoteSport/1.0 (+https://github.com; sports data reader)"}
STANDINGS_URL = "https://ceskyhokej.cz/competition/standings/14"
GAMES_URL = "https://www.czehockey.cz/competition/games/14"
RSS_URL = "https://www.hokej.cz/rss/vse"
SCOREBOARD_URL = "https://json.esports.cz/hokejcz/scoreboard/{date}.json"


def season_label() -> str:
    now = datetime.now(CZ)
    start = now.year if now.month >= 7 else now.year - 1
    return f"{start}/{str(start + 1)[-2:]}"


def get(url: str) -> requests.Response:
    response = requests.get(url, headers=HEADERS, timeout=35)
    response.raise_for_status()
    return response


def write_json(name: str, value: object) -> None:
    DATA.mkdir(exist_ok=True)
    path = DATA / name
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def cell_text(cell) -> str:
    return " ".join(cell.stripped_strings).strip()


def scrape_standings() -> list[dict]:
    soup = BeautifulSoup(get(STANDINGS_URL).text, "html.parser")
    parsed: list[dict] = []
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"], recursive=False)
            club_link = row.select_one('a[href*="/club/detail/"]')
            if not club_link or len(cells) < 8:
                continue
            values = [cell_text(cell) for cell in cells]
            team_cell = next((i for i, cell in enumerate(cells) if cell.find('a', href=re.compile(r"/club/detail/"))), None)
            if team_cell is None:
                continue
            name = cell_text(club_link)
            rank_text = values[0] if values else ""
            rank_match = re.search(r"\d+", rank_text)
            if not rank_match:
                continue
            numbers = [value for i, value in enumerate(values) if i != team_cell and i != 0]
            numbers = [re.sub(r"\s+", "", n) for n in numbers if n]
            if len(numbers) < 7:
                raw = re.sub(r"\s+", " ", row.get_text(" ", strip=True))
                tokens = re.findall(r"\b\d+:\d+\b|\b\d+\b", raw)
                tokens = tokens[1:] if tokens and tokens[0] == rank_match.group(0) else tokens
                numbers = tokens[-7:]
            if len(numbers) < 7:
                continue
            try:
                gp, wins, otw, otl, losses = [int(re.search(r"\d+", n).group(0)) for n in numbers[:5]]
                goals = numbers[5]
                points = int(re.search(r"\d+", numbers[6]).group(0))
                rank = int(rank_match.group(0))
            except (ValueError, AttributeError):
                continue
            parsed.append({"rank": rank, "team": name, "gp": gp, "w": wins, "otw": otw, "otl": otl,
                           "l": losses, "goals": goals, "pts": points,
                           "club_url": urljoin(STANDINGS_URL, club_link.get("href", ""))})
    parsed.sort(key=lambda team: team["rank"])
    if len(parsed) != 14:
        raise RuntimeError(f"Expected 14 official teams in standings; parsed {len(parsed)}. Keeping previous data.")
    return parsed


def update_standings() -> None:
    teams = scrape_standings()
    write_json("standings.json", {"updated": datetime.now(CZ).isoformat(timespec="seconds"),
                                  "source": STANDINGS_URL, "season": season_label(), "teams": teams})
    print(f"Saved official standings for {len(teams)} teams")


def game_id_from_href(href: str) -> str | None:
    match = re.search(r"/game/detail/(\d+)", href or "")
    return match.group(1) if match else None


def parse_game(anchor, team_names: list[str]) -> dict | None:
    raw = re.sub(r"\s+", " ", html.unescape(anchor.get_text(" ", strip=True))).strip()
    game_id = game_id_from_href(anchor.get("href", ""))
    if not game_id:
        return None
    lead = raw
    prefix = ""
    # Result labels for overtime and shootouts are placed before the home team.
    marker = re.match(r"^(SN|P|PP|OT)\s+", lead, flags=re.I)
    if marker:
        prefix, lead = marker.group(1).upper(), lead[marker.end():]
    home = away = None
    for candidate_home in sorted(team_names, key=len, reverse=True):
        starter = candidate_home + " - "
        if lead.startswith(starter):
            remaining = lead[len(starter):]
            candidate_away = next((t for t in sorted(team_names, key=len, reverse=True)
                                   if remaining.startswith(t)), None)
            if candidate_away:
                home, away = candidate_home, candidate_away
                break
    if not home or not away:
        return None
    date_match = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4}),?\s*(\d{1,2}):(\d{2})", raw)
    if not date_match:
        return None
    day, month, year, hour, minute = map(int, date_match.groups())
    try:
        local_start = datetime(year, month, day, hour, minute, tzinfo=CZ)
    except ValueError:
        return None

    # A score is present only when the official game entry includes period details.
    payload = lead[len(home + " - " + away):].strip()
    payload = re.sub(r"^(?:SN|P|PP|OT)\s+", "", payload, flags=re.I)
    if payload.startswith(home):
        payload = payload[len(home):].strip()
    away_pos = payload.find(away)
    score_match = re.match(r"(\d{1,2})\s+(\d{1,2})\s+", payload[:away_pos] if away_pos >= 0 else "")
    periods = bool(re.search(r"\d+:\d+\s*,\s*\d+:\d+", raw))
    lower = raw.casefold()
    if re.search(r"odložen|odlozen|zrušen|zr[uú]šen", raw, flags=re.I):
        state = "postponed"
    elif re.search(r"\b(?:\d+\.)?\s*(?:třetina|tretina|prodloužení|prodlouzeni|nájezdy|najezdy)\b", lower):
        state = "live"
    else:
        state = "played" if periods and (local_start + timedelta(hours=4)) < datetime.now(CZ) else ("upcoming" if local_start > datetime.now(CZ) else "unknown")
    home_score, away_score = (int(score_match.group(1)), int(score_match.group(2))) if score_match and periods else (None, None)
    period_match = re.search(r"(\d+\.\s*(?:třetina|tretina)|prodloužení|prodlouzeni|samostatné nájezdy|samostatne najezdy)", raw, flags=re.I)
    clock_match = re.search(r"(?:^|[|·])\s*(\d{1,2}:\d{2})(?:\s|$)", raw)
    return {"id": game_id, "date": local_start.date().isoformat(), "time": local_start.strftime("%H:%M"),
            "home": home, "away": away, "home_score": home_score, "away_score": away_score,
            "state": state, "extra_time": prefix if periods else "",
            "period": period_match.group(1) if period_match else "",
            "clock": clock_match.group(1) if clock_match else "", "statistics": None,
            "source": "Český hokej",
            "url": urljoin(GAMES_URL, anchor.get("href", ""))}


def scrape_games(team_names: list[str]) -> list[dict]:
    games: dict[str, dict] = {}
    empty_pages = 0
    for page in range(1, 21):
        url = GAMES_URL if page == 1 else f"{GAMES_URL}?gameListWidget-page={page}"
        soup = BeautifulSoup(get(url).text, "html.parser")
        before = len(games)
        for anchor in soup.select('a[href*="/game/detail/"]'):
            game = parse_game(anchor, team_names)
            if game:
                games[game["id"]] = game
        if len(games) == before:
            empty_pages += 1
            if empty_pages >= 2:
                break
        else:
            empty_pages = 0
        time.sleep(0.35)
    result = sorted(games.values(), key=lambda g: (g["date"], g["time"], int(g["id"])))
    if len(result) < 364:
        raise RuntimeError(f"Expected at least 364 regular-season fixtures; parsed {len(result)}. Refusing to publish an incomplete schedule.")
    return result


def update_schedule() -> None:
    standings = scrape_standings()
    games = scrape_games([team["team"] for team in standings])
    write_json("standings.json", {"updated": datetime.now(CZ).isoformat(timespec="seconds"),
                                  "source": STANDINGS_URL, "season": season_label(), "teams": standings})
    write_json("games.json", {"updated": datetime.now(CZ).isoformat(timespec="seconds"),
                              "source": GAMES_URL, "season": season_label(), "games": games})
    print(f"Saved {len(standings)} teams and {len(games)} official games")


def update_live() -> None:
    # Pull the official table again after games, then merge the live game feed into the full schedule cache.
    update_standings()
    if not (DATA / "games.json").exists():
        update_schedule()
    today = datetime.now(CZ).date().isoformat()
    try:
        payload = get(SCOREBOARD_URL.format(date=today)).json()
    except requests.RequestException as exc:
        print(f"Live scoreboard unavailable; standings were refreshed: {exc}")
        return
    entries = payload if isinstance(payload, list) else payload.get("games", payload.get("data", []))
    path = DATA / "games.json"
    current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"games": []}
    by_id = {str(g.get("id")): g for g in current.get("games", [])}
    for item in entries:
        if str(item.get("league_id", "101")) != "101":
            continue
        game_id = str(item.get("hokejcz_game_id", item.get("game_id", "")))
        if not game_id or game_id not in by_id:
            continue
        game = by_id[game_id]
        game["home_score"] = item.get("home_score", game.get("home_score"))
        game["away_score"] = item.get("away_score", game.get("away_score"))
        status = (item.get("match_status") or item.get("status_name") or "").lower()
        period = item.get("period_name") or item.get("period") or ""
        clock = item.get("clock") or item.get("game_time") or ""
        if period:
            game["period"] = str(period)
        if clock and re.fullmatch(r"\d{1,2}:\d{2}", str(clock)):
            game["clock"] = str(clock)
        game["statistics"] = item.get("statistics") or None
        if item.get("status_code") == "K" or "konec" in status or "po zápase" in status:
            game["state"] = "played"
        elif any(word in status for word in ("hraje", "probíhá", "živě")):
            game["state"] = "live"
        elif any(word in status for word in ("třetina", "tretina", "prodloužení", "prodlouzeni", "nájezdy", "najezdy")):
            game["state"] = "live"
    current.update({"updated": datetime.now(CZ).isoformat(timespec="seconds"), "games": sorted(by_id.values(), key=lambda g: (g["date"], g["time"]))})
    write_json("games.json", current)
    print(f"Merged live score feed for {today}")


def clean_html(value: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)).strip()


def update_news() -> None:
    root = ET.fromstring(get(RSS_URL).content)
    path = DATA / "news.json"
    prior = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"articles": []}
    archive = {str(a.get("url")): a for a in prior.get("articles", []) if a.get("url")}
    team_names = [t["team"] for t in (json.loads((DATA / "standings.json").read_text(encoding="utf-8")).get("teams", []) if (DATA / "standings.json").exists() else [])]
    for item in root.findall(".//item"):
        def val(tag: str) -> str:
            node = item.find(tag)
            return (node.text or "").strip() if node is not None else ""
        title, link = val("title"), val("link")
        description, published = clean_html(val("description")), val("pubDate")
        haystack = f"{title} {description}".casefold()
        if not any(keyword.casefold() in haystack for keyword in ["extraliga", "tipsport elh", *team_names]):
            continue
        image = ""
        for child in item.iter():
            if child.tag.rsplit("}", 1)[-1] in ("content", "thumbnail"):
                image = child.attrib.get("url", "")
                if image:
                    break
        enclosure = item.find("enclosure")
        if not image and enclosure is not None:
            image = enclosure.attrib.get("url", "")
        if not link:
            continue
        if not image and link not in archive:
            try:
                article = BeautifulSoup(get(link).text, "html.parser")
                preview = article.select_one('meta[property="og:image"], meta[name="twitter:image"]')
                if preview:
                    image = preview.get("content", "")
            except requests.RequestException:
                pass
        archive[link] = {"title": title, "url": link, "description": description,
                         "published": published, "image": image, "source": "Hokej.cz"}
    articles = sorted(archive.values(), key=lambda a: a.get("published", ""), reverse=True)[:500]
    write_json("news.json", {"updated": datetime.now(CZ).isoformat(timespec="seconds"),
                              "source": RSS_URL, "articles": articles})
    print(f"Saved {len(articles)} real articles to the news archive")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("live", "schedule", "news", "all"), required=True)
    mode = parser.parse_args().mode
    try:
        if mode == "live": update_live()
        elif mode == "schedule": update_schedule()
        elif mode == "news": update_news()
        else:
            update_schedule()
            update_news()
    except Exception as exc:
        print(f"Data refresh failed; existing cached data was not replaced: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

