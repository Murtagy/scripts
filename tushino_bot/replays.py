import datetime
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import requests

logger = logging.getLogger(__name__)
REPLAY_BASE_URL = "https://replay.tsgames.ru/rv"
STATE_FILE = Path(__file__).with_name("parsed_replays.json")
# T2 and T3 game servers, limited to TSG replays.
REPLAY_FILTERS = (2, 3, 10)


@dataclass
class Frag:
    killer: str
    killed: str
    time: str
    teamkill: bool
    mission: str

    def __repr__(self):
        teamkill_str = ''
        if self.teamkill:
            teamkill_str = '. Это тимкил =('
        return f'{self.time}: {self.killer} убивает {self.killed}{teamkill_str}'

    def __str__(self):
        return repr(self)


@dataclass
class Replay:
    name: str
    url: str


def get_frags(url: str, mission: str) -> list[Frag]:
    frags = []

    get_replay = requests.get(url, timeout=(10, 120))
    get_replay.raise_for_status()
    payload = get_replay.json()
    if payload.get("error"):
        raise RuntimeError(f"Replay server error: {payload['error']}")
    replay = json.loads(payload['json'])
    players_list = replay[1][1]
    players_dict = {}  # id: name
    side_dict = {}
    for player_opt in players_list:
        if player_opt[0] == 1:
            id = player_opt[1]
            side = player_opt[4]
            side_dict[id] = side
        if player_opt[0] == 3:
            id = player_opt[1]
            player_name = player_opt[3]
            players_dict[id] = player_name

    for move in replay[2:]:
        if len(move[1]) > 0:
            for event in move[1]:
                if event[0] != 4:
                    continue
                event_type, seconds, p1, p2, gun, *args = event
                if p1 == p2:
                    continue
                if p1 not in players_dict or p2 not in players_dict:
                    continue

                tk = side_dict.get(p1) == side_dict.get(p2)

                frag = Frag(
                    killer=players_dict[p1],
                    killed=players_dict[p2],
                    time=str(datetime.timedelta(seconds=seconds)),
                    teamkill=tk,
                    mission=mission
                )
                frags.append(frag)
    return frags


def frag_print(url, squad):
    frags = get_frags(url, '')
    for frag in frags:
        if squad:
            if frag.killer.startswith(squad):
                print(frag)
        else:
            print(frag)


def _list_replays_page(offset: int = 0, today: datetime.date | None = None) -> dict:
    today = today or datetime.date.today()
    filters = [*REPLAY_FILTERS, f"20:{today:%Y%m}"]
    params = {"a": "l", "params[offset]": offset}
    params.update({f"params[f][{i}]": value for i, value in enumerate(filters)})

    r = requests.get(f"{REPLAY_BASE_URL}/ajax.php", params=params, timeout=(10, 30))
    r.raise_for_status()
    payload = r.json()
    if payload.get("error"):
        raise RuntimeError(f"Replay server error: {payload['error']}")
    return payload


def get_new_replays(known_names: Sequence[str]):
    replays = []
    offset = 0
    seen = set(known_names)

    while True:
        page = _list_replays_page(offset)
        rows = page.get('rows', [])
        for row in rows:
            name = row['name']
            if name in seen:
                continue
            url = f'{REPLAY_BASE_URL}/ajax.php?a=gl&params[f]={name}&params[ar]=0&params[a]=3'
            replays.append(Replay(name=name, url=url))
            seen.add(name)
        if not page.get('more') or not rows:
            break
        offset = page.get('offset', offset) + len(rows)
    return replays


def _known_replay_names() -> list[str]:
    try:
        names = json.loads(STATE_FILE.read_text())
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not read replay state %s", STATE_FILE)
        return []
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        logger.error("Invalid replay state in %s", STATE_FILE)
        return []
    return names


def mark_replays_processed(names: Sequence[str]) -> None:
    """Persist replays only after their Telegram report was delivered."""
    if not names:
        return
    known_names = sorted(set(_known_replay_names()).union(names))
    temp_file = STATE_FILE.with_suffix(".tmp")
    temp_file.write_text(json.dumps(known_names), encoding="utf-8")
    temp_file.replace(STATE_FILE)


def collect_new_frags() -> tuple[list[Frag], list[str]]:
    """Return successful parses. Caller must acknowledge them after delivery."""
    frags: list[Frag] = []
    parsed_games: list[str] = []
    try:
        for replay in get_new_replays(_known_replay_names()):
            try:
                replay_frags = get_frags(replay.url, replay.name)
            except Exception:
                logger.exception("Failed to parse replay %s", replay.name)
                continue
            frags.extend(replay_frags)
            parsed_games.append(replay.name)
    except Exception:
        logger.exception("Replay collection failed")

    return frags, parsed_games


if __name__ == "__main__":
    # frag_print(
    #     'https://replay.tsgames.ru/ajax.php?a=gl&params%5Bf%5D=T4.2024-06-21-23-40-40.tsg%40170_fra_Ihtamnet_M_v15.tem_kujari&params%5Bar%5D=1&params%5Ba%5D=3',
    #     '[DER]'
    # )
    frags, games = collect_new_frags()
    for f in frags:
        print(f)
