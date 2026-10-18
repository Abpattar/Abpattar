#!/usr/bin/env python3
"""Build a GitHub-style contribution calendar from the user's public GitHub calendar.

Uses only the Python standard library. It deliberately fails without replacing the
existing SVG if GitHub's public markup changes or no calendar data can be parsed.
"""
from __future__ import annotations

from datetime import date, timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

USERNAME = "Abpattar"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets" / "contribution-graph.svg"
COLORS = ["#111827", "#18335b", "#24539a", "#3a79d2", "#83b7ff"]


class CalendarDayParser(HTMLParser):
    """Collect contribution-calendar cell metadata from GitHub's HTML fragment."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.days: dict[date, tuple[int, int | None]] = {}
        self._active: date | None = None
        self._level = 0
        self._count: int | None = None
        self._inside_cell = False
        self._cell_text: list[str] = []

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key.lower(): (value or "") for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = self._attrs(attrs)
        raw_date = attrs_map.get("data-date")
        if raw_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
            try:
                self._active = date.fromisoformat(raw_date)
            except ValueError:
                self._active = None
            if self._active:
                self._inside_cell = tag.lower() == "td"
                self._level = self._parse_level(attrs_map)
                self._count = self._parse_count(" ".join(attrs_map.values()))
                self._cell_text = []
                if tag.lower() == "rect":
                    self.days[self._active] = (self._level, self._count)
                    self._active = None
                    self._inside_cell = False

        if self._active:
            text_blob = " ".join(attrs_map.values())
            found_count = self._parse_count(text_blob)
            if found_count is not None:
                self._count = found_count

    def handle_data(self, data: str) -> None:
        if self._active and self._inside_cell:
            self._cell_text.append(data)
            found_count = self._parse_count(data)
            if found_count is not None:
                self._count = found_count

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "td" and self._active and self._inside_cell:
            blob = " ".join(self._cell_text)
            found_count = self._parse_count(blob)
            if found_count is not None:
                self._count = found_count
            self.days[self._active] = (self._level, self._count)
            self._active = None
            self._inside_cell = False
            self._cell_text = []

    @staticmethod
    def _parse_level(attrs: dict[str, str]) -> int:
        value = attrs.get("data-level")
        if value and value.isdigit():
            return max(0, min(4, int(value)))
        # Fallback for a future markup variant that reports counts but no level.
        count = CalendarDayParser._parse_count(" ".join(attrs.values()))
        if count is None or count == 0:
            return 0
        return 1 if count < 4 else 2 if count < 8 else 3 if count < 15 else 4

    @staticmethod
    def _parse_count(value: str) -> int | None:
        match = re.search(r"([\d,]+)\s+contributions?\b", value, flags=re.I)
        if match:
            return int(match.group(1).replace(",", ""))
        match = re.search(r"\bdata-count=[\"']([\d,]+)[\"']", value, flags=re.I)
        if match:
            return int(match.group(1).replace(",", ""))
        return None


def fetch_calendar(start: date, end: date) -> dict[date, tuple[int, int | None]]:
    params = urllib.parse.urlencode({"from": start.isoformat(), "to": end.isoformat()})
    url = f"https://github.com/users/{USERNAME}/contributions?{params}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; ProfileContributionGraph/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not fetch GitHub's public contribution calendar: {exc}") from exc

    parser = CalendarDayParser()
    parser.feed(html)
    if len(parser.days) < 100:
        # A conservative regex fallback handles occasional wrapper changes in GitHub markup.
        for match in re.finditer(r"<td\b([^>]*data-date=[\"'](\d{4}-\d{2}-\d{2})[\"'][^>]*)>(.*?)</td>", html, re.I | re.S):
            attrs_text, raw_date, body = match.groups()
            try:
                day = date.fromisoformat(raw_date)
            except ValueError:
                continue
            level_match = re.search(r"data-level=[\"']([0-4])[\"']", attrs_text, re.I)
            aria = re.sub(r"<[^>]+>", " ", unescape(body + " " + attrs_text))
            count = CalendarDayParser._parse_count(aria)
            level = int(level_match.group(1)) if level_match else (0 if not count else 1 if count < 4 else 2 if count < 8 else 3 if count < 15 else 4)
            parser.days[day] = (level, count)

    if len(parser.days) < 100:
        raise RuntimeError(
            "GitHub returned too little contribution-calendar data to build a trustworthy graph. "
            "No existing graph was overwritten. Check the Actions log and try again later."
        )
    return parser.days


def xml_escape(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def make_svg(days: dict[date, tuple[int, int | None]], start: date, end: date, generated: date) -> str:
    # Sunday-aligned columns, matching the shape of GitHub's contribution calendar.
    while start.weekday() != 6:  # Sunday
        start -= timedelta(days=1)
    weeks = 53
    width, height = 1000, 154
    left, top, cell, gap = 111, 52, 10, 4
    step = cell + gap
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">Abpattar public GitHub contribution calendar</title>',
        '<desc id="desc">A weekly-updated contribution grid rendered from the public GitHub contribution calendar.</desc>',
        '<rect x="1" y="1" width="998" height="152" rx="8" fill="#080e18" stroke="#23334e"/>',
        '<text x="25" y="27" font-family="monospace" font-size="13" font-weight="700" letter-spacing="2" fill="#83b7ff">CONTRIBUTION GRAPH</text>',
        f'<text x="974" y="27" text-anchor="end" font-family="monospace" font-size="10" fill="#8194b5">UPDATED {generated.isoformat()}</text>',
    ]

    # Month labels: position each month at its first week in the calendar.
    month_date = date(start.year, start.month, 1)
    while month_date <= end:
        if month_date >= start:
            week = (month_date - start).days // 7
            if 0 <= week < weeks:
                parts.append(f'<text x="{left + week * step}" y="44" font-family="monospace" font-size="10" fill="#8798b6">{month_date.strftime("%b")}</text>')
        # Advance to next month.
        month_date = date(month_date.year + (month_date.month == 12), 1 if month_date.month == 12 else month_date.month + 1, 1)

    for row, label in [(1, "Mon"), (3, "Wed"), (5, "Fri")]:
        parts.append(f'<text x="25" y="{top + row * step + 8}" font-family="monospace" font-size="9" fill="#667c9e">{label}</text>')

    for week in range(weeks):
        for row in range(7):
            day = start + timedelta(days=week * 7 + row)
            if day > end:
                level, count = 0, None
            else:
                level, count = days.get(day, (0, None))
            # Future cells and dates before the queried window are quiet empty cells.
            fill = COLORS[max(0, min(4, level))]
            x, y = left + week * step, top + row * step
            title = f"{day.isoformat()} — {count} contributions" if count is not None else f"{day.isoformat()} — contribution level {level}"
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{fill}" stroke="#1a2840" stroke-width="0.5">'
                f'<title>{xml_escape(title)}</title></rect>'
            )

    legend_y = 140
    legend_label_x = width - 151
    parts.append(f'<text x="{legend_label_x}" y="{legend_y}" font-family="monospace" font-size="9" fill="#7185a5">Less</text>')
    legend_start = legend_label_x + 29
    for idx, fill in enumerate(COLORS):
        parts.append(f'<rect x="{legend_start + idx * 14}" y="{legend_y - 9}" width="10" height="10" rx="2" fill="{fill}"/>')
    parts.append(f'<text x="{legend_start + 5 * 14 + 3}" y="{legend_y}" font-family="monospace" font-size="9" fill="#7185a5">More</text>')
    parts.append('</svg>')
    return "\n".join(parts) + "\n"


def main() -> int:
    today = date.today()
    # Display 53 Sunday-aligned columns covering roughly the latest year.
    start = today - timedelta(days=364)
    while start.weekday() != 6:
        start -= timedelta(days=1)
    end = today
    print(f"Fetching {USERNAME}'s public contribution calendar: {start} to {end}")
    days = fetch_calendar(start, end)
    svg = make_svg(days, start, end, today)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUTPUT.with_suffix(".svg.tmp")
    temp.write_text(svg, encoding="utf-8")
    temp.replace(OUTPUT)
    print(f"Wrote {OUTPUT} from {len(days)} calendar cells.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
