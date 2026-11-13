"""Pure headquarters models and DER-only plan rendering."""

import hashlib
import re
from dataclasses import dataclass
from datetime import date, timedelta
from html import unescape
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mission_names import MissionName


class PlanSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    reference: int
    mission_id: int
    event_datetime: date
    event_position: int = Field(ge=0)

    def topic_name(self, mission: MissionName | None = None) -> str:
        name = " ".join(mission.name.split()) if mission is not None else f"mission-{self.mission_id}"
        weekday = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")[self.event_datetime.weekday()]
        return telegram_chunks(f"{self.event_datetime:%m-%d}-{weekday}{self.event_position + 1}-{name}", limit=128)[0]


class Headquarters(BaseModel):
    plans: tuple[PlanSummary, ...]


class UniformImage(BaseModel):
    image: str
    title: str | None = None


class Uniforms(BaseModel):
    ally: tuple[UniformImage, ...] = ()
    enemy: tuple[UniformImage, ...] = ()

    @field_validator("ally", "enemy", mode="before")
    @classmethod
    def empty_side(cls, value: object) -> object:
        return () if value is None else value


class PlanDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    author_squad: str
    author_username: str
    event_datetime: date
    event_position: int = Field(ge=0)
    is_reserve: bool
    reversed: bool
    content: str
    screenshots: Uniforms = Field(default_factory=Uniforms)
    # Neither slots nor comments are consumed; their independent schemas must not block the plan.

    @field_validator("screenshots", mode="before")
    @classmethod
    def empty_screenshots(cls, value: object) -> object:
        return {} if value is None or value == [] else value

    def der_message(self, summary: PlanSummary, mission: MissionName | None = None) -> str:
        name = mission.name if mission is not None else f"миссия {summary.mission_id}"
        header = f"План для DER · {name}\n{self.event_datetime:%d.%m.%Y}, игра №{self.event_position + 1}"
        if mission is not None:
            header += f"\n{mission.url}"
        fragments = tuple(dict.fromkeys(fragment.render() for fragment in der_fragments(self.content)))
        body = "\n\n".join(fragments) or "Фрагменты DER пока не опубликованы."
        return f"{header}\n\n{body}"

    def media_notices(self) -> tuple["Notice", ...]:
        notices = {notice.image_url: notice for notice in image_notices(self.content)}
        uniforms: dict[str, list[tuple[str, str]]] = {}
        for label, side, images in (("Наша форма:", "ally", self.screenshots.ally), ("Противник:", "enemy", self.screenshots.enemy)):
            for image in images:
                if image.image:
                    uniforms.setdefault(image.image, []).append(("\n".join(filter(None, (label, image.title))), side))
        for url, entries in uniforms.items():
            caption = "\n".join((*dict.fromkeys(label for label, side in entries), url))
            sides = {side for label, side in entries}
            group: Literal["ally", "enemy"] | None = "ally" if sides == {"ally"} else "enemy" if sides == {"enemy"} else None
            notices[url] = Notice("uniform", digest(url), caption, url, group)
        return tuple(notices.values())


@dataclass(frozen=True)
class Paragraph:
    path: tuple[str, ...]
    text: str

    def render(self) -> str:
        return "\n".join(filter(None, ("-".join(self.path), self.text)))


@dataclass(frozen=True)
class Notice:
    kind: str
    identity: str
    text: str
    image_url: str
    album_key: Literal["ally", "enemy"] | None = None


def upcoming_plans(plans: tuple[PlanSummary, ...], today: date) -> tuple[PlanSummary, ...]:
    next_sunday = today - timedelta(days=today.weekday()) + timedelta(days=13)
    eligible = (plan for plan in plans if today <= plan.event_datetime <= next_sunday)
    ordered = sorted(eligible, key=lambda plan: (plan.event_datetime, plan.event_position, -plan.reference))
    games = tuple(dict.fromkeys((plan.event_datetime, plan.event_position) for plan in ordered))[:4]
    return tuple(plan for plan in ordered if (plan.event_datetime, plan.event_position) in games)


def normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").splitlines()).strip()


def paragraphs(content: str) -> tuple[Paragraph, ...]:
    """TSGames @@@ blocks nest; a bare @@@ closes the innermost block."""
    result: list[Paragraph] = []
    path: list[str] = []
    lines: list[str] = []
    for line in (*normalize(content).splitlines(), ""):
        stripped = line.strip()
        boundary = not stripped or stripped.startswith("@@@")
        if boundary and lines:
            result.append(Paragraph(tuple(path), "\n".join(lines)))
            lines = []
        if stripped.startswith("@@@"):
            title = stripped[3:].strip()
            if title:
                path.append(title)
            elif path:
                path.pop()
        elif stripped:
            lines.append(line)
    return tuple(result)


_IMAGE_PATTERN = re.compile(
    r"!\[[^\]]*\]\(\s*<?(https?://[^\s)>]+)>?[^)]*\)|<img\b[^>]*\bsrc\s*=\s*[\"'](https?://[^\"']+)[\"'][^>]*>",
    re.IGNORECASE,
)


def der_fragments(content: str) -> tuple[Paragraph, ...]:
    result: list[Paragraph] = []
    der = re.compile(r"(?<!\w)(?:DER(?:_c)?|ДЕР)(?!\w)", re.IGNORECASE)
    for paragraph in paragraphs(content):
        text = normalize(_IMAGE_PATTERN.sub("", paragraph.text))
        if not text:
            continue
        if der.search("-".join(paragraph.path)):
            result.append(Paragraph(paragraph.path, text))
            continue
        # Squad task lists often have no blank lines. Keep matching entries and their continuations.
        entries = re.split(r"\n(?=(?:[A-Za-z][A-Za-z0-9_]{1,14}|ДЕР)(?:\s*[-—:]\s*|\s+(?=[А-Яа-яЁё])))", text)
        matching = tuple(entry for entry in entries if der.search(entry))
        if matching:
            result.append(Paragraph(paragraph.path, "\n".join(matching)))
    return tuple(result)


def digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def image_urls(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(unescape(match[1] or match[2]) for match in _IMAGE_PATTERN.finditer(text)))


def image_notices(content: str, source: str = "План") -> tuple[Notice, ...]:
    return tuple(
        Notice("image", digest(url), f"{source}\n{'-'.join(paragraph.path)}\n{url}".replace("\n\n", "\n"), url)
        for paragraph in paragraphs(content) for url in image_urls(paragraph.text)
    )


def telegram_chunks(text: str, limit: int = 4000) -> tuple[str, ...]:
    """Telegram counts UTF-16 code units, including two units per astral emoji."""
    if limit < 2:
        raise ValueError("Chunk limit must fit a UTF-16 surrogate pair")
    remaining = text.strip()
    chunks: list[str] = []
    while remaining:
        units = 0
        end = 0
        for char in remaining:
            units += 2 if ord(char) > 0xFFFF else 1
            if units > limit:
                break
            end += 1
        if end < len(remaining):
            newline = remaining.rfind("\n", 0, end + 1)
            if newline > 0:
                end = newline
        chunks.append(remaining[:end])
        remaining = remaining[end:].lstrip("\n")
    return tuple(chunks)
