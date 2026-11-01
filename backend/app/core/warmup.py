"""Optional start-up warm-up: ask for today's plan for a few configured farms once, so the live-data cache is full before the first
visitor. It calls the same tools as a real request (nothing is stored except the normal cache) and never raises or blocks start-up."""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("warmup")


def parse_places(spec: str) -> list[dict]:
    """'Nashik, Maharashtra|Onion; Pune, Maharashtra|Tomato' -> profiles. A part without '|' is a place with no crop."""
    out = []
    for part in (spec or "").split(";"):
        place, _, crop = part.partition("|")
        if place.strip():
            out.append({k: v.strip() for k, v in (("place", place), ("crop", crop)) if v.strip()})
    return out


def warm(profiles: list[dict], tool) -> int:
    """Runs `tool("", profile)` for each profile; returns how many succeeded."""
    done = 0
    for profile in profiles:
        try:
            if tool("", profile).ok:
                done += 1
        except Exception as e:
            log.warning("warm-up skipped %s: %s", profile.get("place"), type(e).__name__)
    return done


def warm_in_background(profiles: list[dict], tool) -> threading.Thread | None:
    if not profiles:
        return None
    t = threading.Thread(target=lambda: log.info("warmed %d of %d farms", warm(profiles, tool), len(profiles)), daemon=True, name="warmup")
    t.start()
    return t
