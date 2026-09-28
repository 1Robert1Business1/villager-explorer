"""Rebuild data/villagers.csv from Nookipedia's public Cargo tables.

The app never runs this: it reads the committed CSV. This script exists so the
snapshot is reproducible and can be refreshed when Nookipedia adds villagers.

    python scripts/build_dataset.py

Source: Nookipedia (https://nookipedia.com), CC BY-SA 3.0. Three Cargo tables
are exported (no API key needed) and joined:

    villager    - every villager in the series (~490 rows): core attributes, main artwork
    nh_villager - New Horizons villagers (~417 rows): hobby, favourite styles/colours, icon
    nh_house    - New Horizons houses: the villager's house music (favourite song)

villager <-> nh_villager join on page URL, because names are not unique across
the series (two Carmens, two Lulus) and the `id` field is blank for ~64
villagers. nh_house only carries a display name, so it joins on name (unique
within New Horizons; the script checks this).

This script only extracts and joins. Cleaning (entity decoding, blanks -> NA,
list parsing, dtypes) lives in src/data.py so the app owns its data contract.
"""

from __future__ import annotations

import csv
import html
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import requests
import truststore

# Verify TLS against the OS trust store rather than certifi's bundle, so the
# script works behind antivirus/corporate TLS inspection without disabling checks.
truststore.inject_into_ssl()

CARGO_EXPORT = "https://nookipedia.com/w/index.php"
USER_AGENT = "villager-explorer dataset build (https://github.com/; Nookipedia CC BY-SA 3.0)"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "villagers.csv"

# Game flags in the villager table, in release order, with display labels.
GAMES = {
    "dnm": "DnM",
    "ac": "AC",
    "e_plus": "e+",
    "ww": "WW",
    "cf": "CF",
    "nl": "NL",
    "wa": "WA",
    "nh": "NH",
    "pc": "PC",
    "hhd": "HHD",
    "film": "Film",
}

VILLAGER_FIELDS = [
    "url", "name", "image_url", "species", "personality", "gender",
    "birthday", "birthday_month", "birthday_day", "sign", "quote", "phrase",
    "clothing", "islander", "debut", "title_color", "text_color", *GAMES,
]
NH_FIELDS = [
    "url", "icon_url", "photo_url", "sub_personality", "catchphrase",
    "hobby", "fav_style1", "fav_style2", "fav_color1", "fav_color2",
]
HOUSE_FIELDS = ["villager", "music"]

OUTPUT_COLUMNS = [
    "key", "name", "species", "personality", "sub_personality", "gender",
    "birthday", "birthday_month", "birthday_day", "sign", "catchphrase", "quote",
    "hobby", "fav_style1", "fav_style2", "fav_color1", "fav_color2",
    "favorite_song", "clothing", "debut", "games", "in_nh", "islander",
    "image_url", "icon_url", "photo_url", "nookipedia_url", "title_color", "text_color",
]


def cargo_export(table: str, fields: list[str]) -> list[dict]:
    resp = requests.get(
        CARGO_EXPORT,
        params={
            "title": "Special:CargoExport",
            "tables": table,
            "fields": ",".join(fields),
            "limit": 5000,
            "format": "json",
        },
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise RuntimeError(f"Cargo export for {table!r} returned no rows")
    return rows


_HEX6 = re.compile(r"[0-9a-fA-F]{6}")


def hex_colour(value: object) -> str:
    """Recover the 6-digit hex colour Cargo's JSON export may have turned into a number.

    The export emits all-digit-looking colours as JSON numbers, so "515151" arrives
    as 515151, "051515" loses its leading zero, and "7352e8" / "878e86" are read
    as scientific notation (735200000000, 8.78e+88). Returns "" when there is no
    colour; raises if a value can't be recovered, so bad data never ships.
    """
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        text = value.strip().lstrip("#")
    elif isinstance(value, int) and 0 <= value < 10**6:
        text = str(value).zfill(6)
    elif isinstance(value, (int, float)):
        # Find the one "<digits>e<digits>" spelling, six characters long, equal to the value.
        matches = {
            f"{mantissa}e{exp}"
            for exp in range(1, 100)
            if (mantissa := round(value / 10**exp)) > 0
            and len(f"{mantissa}e{exp}") == 6
            and float(f"{mantissa}e{exp}") == float(value)
        }
        if len(matches) != 1:
            raise ValueError(f"can't recover a hex colour from {value!r}: {sorted(matches)}")
        text = matches.pop()
    else:
        raise ValueError(f"unexpected colour value {value!r}")
    if not _HEX6.fullmatch(text):
        raise ValueError(f"not a 6-digit hex colour: {value!r}")
    return text.lower()


def page_key(url: str) -> str:
    """'https://nookipedia.com/wiki/Carmen_(mouse)' -> 'Carmen_(mouse)' (stable, unique)."""
    return unquote(url.rsplit("/wiki/", 1)[1])


def build() -> list[dict]:
    villagers = cargo_export("villager", VILLAGER_FIELDS)
    nh_by_url = {r["url"]: r for r in cargo_export("nh_villager", NH_FIELDS)}
    # nh_house is keyed by display name (not URL), and some of those names are
    # double HTML-escaped ("O&amp;#39;Hare"), so decode the key before joining.
    # Display names are unique within New Horizons, which makes this join safe.
    song_by_name = {
        html.unescape(html.unescape(r["villager"])): r["music"]
        for r in cargo_export("nh_house", HOUSE_FIELDS)
    }
    nh_names = [v["name"] for v in villagers if v["url"] in nh_by_url]
    if len(set(nh_names)) != len(nh_names):
        raise RuntimeError("New Horizons villager names are not unique; song join is ambiguous")

    rows = []
    for v in villagers:
        nh = nh_by_url.get(v["url"], {})
        key = page_key(v["url"])
        rows.append({
            "key": key,
            "name": v["name"],
            "species": v["species"],
            "personality": v["personality"],
            "sub_personality": nh.get("sub_personality", ""),
            "gender": v["gender"],
            "birthday": v["birthday"],
            "birthday_month": v["birthday_month"],
            "birthday_day": v["birthday_day"],
            "sign": v["sign"],
            # Prefer the New Horizons catchphrase where one exists (it is the
            # one current players see); fall back to the series-wide field.
            "catchphrase": nh.get("catchphrase") or v["phrase"],
            "quote": v["quote"],
            "hobby": nh.get("hobby", ""),
            "fav_style1": nh.get("fav_style1", ""),
            "fav_style2": nh.get("fav_style2", ""),
            "fav_color1": nh.get("fav_color1", ""),
            "fav_color2": nh.get("fav_color2", ""),
            "favorite_song": song_by_name.get(v["name"], "") if nh else "",
            "clothing": v["clothing"],
            "debut": v["debut"],
            "games": "|".join(label for flag, label in GAMES.items() if str(v[flag]) == "1"),
            "in_nh": int(bool(nh)),
            "islander": v["islander"],
            "image_url": v["image_url"],
            "icon_url": nh.get("icon_url", ""),
            "photo_url": nh.get("photo_url", ""),
            "nookipedia_url": v["url"],
            "title_color": hex_colour(v["title_color"]),
            "text_color": hex_colour(v["text_color"]),
        })

    keys = [r["key"] for r in rows]
    if len(set(keys)) != len(keys):
        raise RuntimeError("Villager keys are not unique; join logic needs revisiting")
    return sorted(rows, key=lambda r: r["name"].casefold())


def main() -> int:
    rows = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    in_nh = sum(r["in_nh"] for r in rows)
    print(f"Wrote {len(rows)} villagers ({in_nh} in New Horizons) to {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
