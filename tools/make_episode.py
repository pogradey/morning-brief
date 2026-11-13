"""Turn episodes/DATE.txt into docs/audio/DATE.mp3 and refresh docs/episodes.json and docs/feed.xml.

Usage: python tools/make_episode.py 2026-11-14
Needs: pip install edge-tts
"""
import asyncio
import datetime as dt
import json
import pathlib
import sys
from email.utils import format_datetime
from xml.sax.saxutils import escape

import edge_tts

VOICE = "en-US-AndrewMultilingualNeural"
RATE = "+8%"
MAX_WORDS = 950
KEEP_DAYS = 30
SITE = "https://pogradey.github.io/morning-brief"

ROOT = pathlib.Path(__file__).resolve().parent.parent
EPISODES = ROOT / "episodes"
DOCS = ROOT / "docs"
AUDIO = DOCS / "audio"


async def speak(text: str, out: pathlib.Path) -> float:
    """Write the mp3 and return its length in seconds (from the last word boundary)."""
    end = 0.0
    communicate = edge_tts.Communicate(text, VOICE, rate=RATE, boundary="WordBoundary")
    with open(out, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                end = max(end, (chunk["offset"] + chunk["duration"]) / 1e7)
    return end


def main() -> None:
    date = sys.argv[1]
    dt.date.fromisoformat(date)
    text = (EPISODES / f"{date}.txt").read_text(encoding="utf-8").strip()
    words = len(text.split())
    if words > MAX_WORDS:
        sys.exit(f"Script is {words} words; the limit is {MAX_WORDS}. Cut it and run again.")
    meta = json.loads((EPISODES / f"{date}.json").read_text(encoding="utf-8"))

    AUDIO.mkdir(parents=True, exist_ok=True)
    mp3 = AUDIO / f"{date}.mp3"
    part = AUDIO / f"{date}.part"
    try:
        seconds = asyncio.run(speak(text, part))
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    if part.stat().st_size < 20000:
        part.unlink()
        sys.exit("The speech service returned no audio. Run again.")
    part.replace(mp3)
    if seconds <= 0:
        seconds = words / 2.9
    if seconds > 300:
        mp3.unlink()
        sys.exit(f"Audio is {seconds:.0f}s; the limit is 300s. Cut the script and run again.")

    meta.update({"date": date, "seconds": round(seconds), "words": words,
                 "audio": f"audio/{date}.mp3", "bytes": mp3.stat().st_size})

    index = DOCS / "episodes.json"
    episodes = json.loads(index.read_text(encoding="utf-8")) if index.exists() else []
    episodes = [e for e in episodes if e["date"] != date] + [meta]
    episodes.sort(key=lambda e: e["date"], reverse=True)

    cutoff = (dt.date.fromisoformat(date) - dt.timedelta(days=KEEP_DAYS)).isoformat()
    for old in [e for e in episodes if e["date"] < cutoff]:
        (DOCS / old["audio"]).unlink(missing_ok=True)
    episodes = [e for e in episodes if e["date"] >= cutoff]
    index.write_text(json.dumps(episodes, indent=1, ensure_ascii=False), encoding="utf-8")

    items = []
    for e in episodes:
        when = dt.datetime.fromisoformat(e["date"] + "T09:00:00+11:00")
        summary = " | ".join(s["headline"] for s in e.get("stories", []))
        items.append(
            f"<item><title>{escape(e['date'] + ': ' + e.get('title', 'Morning Brief'))}</title>"
            f"<description>{escape(summary)}</description>"
            f"<enclosure url=\"{SITE}/{e['audio']}\" length=\"{e['bytes']}\" type=\"audio/mpeg\"/>"
            f"<guid isPermaLink=\"false\">morning-brief-{e['date']}</guid>"
            f"<pubDate>{format_datetime(when)}</pubDate>"
            f"<itunes:duration>{e['seconds']}</itunes:duration></item>"
        )
    feed = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>'
        "<title>Morning Brief</title>"
        f"<link>{SITE}/</link><language>en-au</language>"
        "<description>A five minute daily briefing on markets, AI, health, politics and world news.</description>"
        "<itunes:block>Yes</itunes:block><itunes:explicit>false</itunes:explicit>"
        + "".join(items) + "</channel></rss>\n"
    )
    (DOCS / "feed.xml").write_text(feed, encoding="utf-8")
    print(f"OK {date}: {words} words, {seconds:.0f}s, {mp3.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
