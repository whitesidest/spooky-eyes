"""The theme catalogue: ids/names/categories plus the pictures rendered by `render_theme_thumbs`."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from django.templatetags.static import static

# Fallback when no board has reported its theme list yet (mirrors firmware/src/render/themes.cpp).
DEFAULT_THEMES = [
    {"id": i, "name": n, "category": c}
    for i, n, c in (
        ("human", "Human", "classic"),
        ("cat", "Cat", "creatures"),
        ("fire", "Fire", "halloween"),
        ("alien", "Alien", "sci-fi"),
        ("sauron", "Sauron", "halloween"),
        ("terminator", "Terminator", "sci-fi"),
        ("dragon", "Dragon", "creatures"),
        ("zombie", "Zombie", "halloween"),
        ("demon", "Demon", "halloween"),
        ("werewolf", "Werewolf", "halloween"),
        ("vampire", "Vampire", "halloween"),
        ("ghost", "Ghost", "halloween"),
        ("jack_o_lantern", "Jack-o'-Lantern", "halloween"),
        ("hypnotic", "Hypnotic", "fun"),
        ("owl", "Owl", "creatures"),
        ("frost", "Frost", "holidays"),
        ("valentine", "Valentine", "holidays"),
        ("rainbow", "Rainbow", "fun"),
        ("robot", "Robot", "sci-fi"),
        ("snake", "Snake", "creatures"),
        ("spider", "Spider", "halloween"),
        ("chameleon", "Chameleon", "creatures"),
        ("puppy", "Sleepy Puppy", "fun"),
        ("anime", "Anime", "fun"),
        ("st_patricks", "St. Patrick's", "holidays"),
        ("easter", "Easter", "holidays"),
        ("fireworks", "Fireworks", "holidays"),
        ("dead", "Dead (X_X)", "halloween"),
    )
]
CATEGORY_ORDER = ["halloween", "creatures", "sci-fi", "holidays", "fun", "classic"]
CATEGORY_LABELS = {"halloween": "Halloween", "creatures": "Creatures", "sci-fi": "Sci-fi",
                   "holidays": "Holidays", "fun": "Fun", "classic": "Classic"}

THUMB_DIR = Path(__file__).resolve().parent / "static" / "devices" / "themes"


@lru_cache(maxsize=1)
def _manifest() -> dict[str, dict]:
    try:
        data = json.loads((THUMB_DIR / "themes.json").read_text())
    except (OSError, ValueError):
        return {}
    return {t["id"]: t for t in data.get("themes", [])}


def pictures(theme_id: str) -> dict:
    """Static URLs of a theme's pictures ({} when it has none rendered yet)."""
    entry = _manifest().get(theme_id)
    if not entry:
        return {}
    out = {"png": static(f"devices/themes/{entry['png']}"), "small": static(f"devices/themes/{entry['small']}")}
    if entry.get("gif"):
        out["gif"] = static(f"devices/themes/{entry['gif']}")
    return out


def catalog(devices) -> list[dict]:
    """Union of every board's themes (first-seen order) with pictures, falling back to the built-in list."""
    seen, out = set(), []
    for d in devices:
        for t in d.themes:
            if t.get("id") and t["id"] not in seen:
                seen.add(t["id"])
                out.append(t)
    themes = out or DEFAULT_THEMES
    defaults = {t["id"]: t for t in DEFAULT_THEMES}
    result = []
    for t in themes:
        base = defaults.get(t["id"], {})
        result.append({
            "id": t["id"],
            "name": t.get("name") or base.get("name") or t["id"],
            "category": t.get("category") or base.get("category") or "other",
            **pictures(t["id"]),
        })
    return result


def grouped(themes: list[dict]) -> list[tuple[str, str, list[dict]]]:
    """[(category id, label, themes)] in a stable, Halloween-first order."""
    groups: dict[str, list[dict]] = {}
    for t in themes:
        groups.setdefault(t["category"], []).append(t)
    order = CATEGORY_ORDER + sorted(set(groups) - set(CATEGORY_ORDER))
    return [(c, CATEGORY_LABELS.get(c, c.title()), groups[c]) for c in order if c in groups]


def lookup(themes: list[dict]) -> dict[str, dict]:
    return {t["id"]: t for t in themes}
