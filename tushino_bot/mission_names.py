"""Mission names from the public, dated mission-list announcements."""

import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx


@dataclass(frozen=True)
class MissionName:
    name: str
    url: str


class MissionScheduleError(Exception):
    pass


def heading_date(text: str, published: date | None) -> date | None:
    if published is None:
        return None
    match = re.search(r"\b(\d{1,2})\s+([а-яё]+)\b", text.lower())
    if match is None:
        return None
    months = {
        "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
        "мая": 5, "июня": 6, "июля": 7, "августа": 8,
        "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    }
    month = months.get(match[2])
    if month is None:
        return None
    candidates: list[date] = []
    for year in (published.year - 1, published.year, published.year + 1):
        try:
            candidates.append(date(year, month, int(match[1])))
        except ValueError:
            continue
    if not candidates:
        return None
    nearest = min(candidates, key=lambda day: abs((day - published).days))
    return nearest if abs((nearest - published).days) <= 21 else None


class _ScheduleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.games: dict[tuple[date, int], MissionName] = {}
        self._stack: list[str] = []
        self._article_depth: int | None = None
        self._mission_article = False
        self._published: date | None = None
        self._heading: list[str] | None = None
        self._game_date: date | None = None
        self._position = 0
        self._url: str | None = None
        self._label: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "div" and "panel-news" in classes:
            self._article_depth = len(self._stack)
            self._mission_article = False
            self._published = None
            self._game_date = None
            self._heading = None
            self._url = None
        void = tag in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
        if not void:
            self._stack.append(tag)
        if self._article_depth is None:
            return
        if "bg-missions" in classes:
            self._mission_article = True
        if tag == "h4" and self._mission_article:
            self._heading = []
            self._game_date = None
            self._position = 0
        if tag == "a" and self._game_date is not None:
            try:
                target = urlsplit(urljoin("https://tsgames.ru/", attributes.get("href") or ""))
            except ValueError:
                return
            mission_link = (
                target.scheme in {"http", "https"} and target.hostname == "tsgames.ru"
                and target.username is None and target.password is None
                and re.fullmatch(r"/missions/(?:test/)?[^/]+/?", target.path) is not None
            )
            if mission_link:
                self._url = urlunsplit(("https", "tsgames.ru", target.path, "", ""))
                self._label = []

    def handle_data(self, data: str) -> None:
        if self._article_depth is None:
            return
        if self._published is None:
            match = re.search(r"\b(\d{2})\.(\d{2})\.(\d{4})\b", data)
            if match is not None:
                try:
                    self._published = date(int(match[3]), int(match[2]), int(match[1]))
                except ValueError:
                    pass
        if self._heading is not None:
            self._heading.append(data)
        if self._url is not None:
            self._label.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h4" and self._heading is not None:
            self._game_date = heading_date("".join(self._heading), self._published)
            self._heading = None
        if tag == "a" and self._url is not None:
            name = " ".join("".join(self._label).split())
            if name and self._game_date is not None:
                self.games.setdefault((self._game_date, self._position), MissionName(name, self._url))
                self._position += 1
            self._url = None
        index = next((i for i in range(len(self._stack) - 1, -1, -1) if self._stack[i] == tag), None)
        if index is None:
            return
        del self._stack[index:]
        if self._article_depth is not None and index <= self._article_depth:
            self._article_depth = None
            self._game_date = None
            self._heading = None
            self._url = None


def parse_mission_schedule(html: str) -> dict[tuple[date, int], MissionName]:
    parser = _ScheduleParser()
    parser.feed(html)
    parser.close()
    return parser.games


async def fetch_mission_schedule() -> dict[tuple[date, int], MissionName]:
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.get("https://tsgames.ru/")
    except httpx.RequestError as exc:
        raise MissionScheduleError(f"Public mission schedule unavailable: {type(exc).__name__}") from None
    if response.status_code != 200:
        raise MissionScheduleError(f"Public mission schedule HTTP {response.status_code}")
    schedule = parse_mission_schedule(response.text)
    if not schedule:
        raise MissionScheduleError("No dated missions found in public announcements")
    return schedule
