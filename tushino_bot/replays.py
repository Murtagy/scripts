import json
import logging
import datetime
from dataclasses import dataclass
from typing import Sequence

import requests

logger = logging.getLogger(__name__)
REPLAY_BASE_URL = "https://replay.tsgames.ru/rv"


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

    get_replay = requests.get(url, timeout=20)
    replay = json.loads(json.loads(get_replay.content)['json'])
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


def _list_replays_page(offset: int = 0) -> dict:
    r = requests.get(
        f"{REPLAY_BASE_URL}/ajax.php",
        params={
            "a": "l",
            "params[offset]": offset,
            "params[f][0]": 3,
            "params[f][1]": 10,
            "params[f][2]": "20:202605",
        },
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


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


def collect_new_frags() -> tuple[list, list]:
    frags = []
    parsed_games = []
    try:
        try:
            with open('parsed_replays.json', 'r') as f:
                known_names = json.load(f)
        except FileNotFoundError:
            known_names = []

        for replay in get_new_replays(known_names):
            known_names.append(replay.name)
            parsed_games.append(replay.name)
            try:
                replay_frags = get_frags(replay.url, replay.name)
                frags.extend(replay_frags)
            except Exception as e:
                logger.exception("Failed to parse replay %s: %s", replay.name, e)
                continue

        known_names.sort()
        with open('parsed_replays.json', 'w') as f:
            json.dump(known_names, f)

    except Exception as e:
        logger.exception("Replay collection failed: %s", e)

    return frags, parsed_games


if __name__ == "__main__":
    # frag_print(
    #     'https://replay.tsgames.ru/ajax.php?a=gl&params%5Bf%5D=T4.2024-06-21-23-40-40.tsg%40170_fra_Ihtamnet_M_v15.tem_kujari&params%5Bar%5D=1&params%5Ba%5D=3',
    #     '[DER]'
    # )
    frags, games = collect_new_frags()
    for f in frags:
        print(f)
