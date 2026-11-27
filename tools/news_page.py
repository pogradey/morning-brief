"""Build the written news page that goes with each episode, as email-safe HTML: docs/email/DATE.html.

The routine sends this file as the email body, so the email reads like a front page:
markets, a short lede, each story with its summary, why it matters and sources, and one to watch.
Everything comes from episodes/DATE.json. All text is escaped; only https links are kept.
"""
import datetime as dt
import html
import pathlib
from urllib.parse import urlparse

SITE = "https://pogradey.github.io/morning-brief"
INK, DIM, LINE, SUN, BG = "#1d2433", "#5d6678", "#e4e1da", "#d9771f", "#faf8f4"


def e(text) -> str:
    return html.escape(str(text or ""), quote=True)


def sources(items) -> list[tuple[str, str]]:
    """Accept ["https://..."] or [{"title":..., "url":...}]; keep https links only."""
    out = []
    for s in items or []:
        url, title = (s.get("url"), s.get("title")) if isinstance(s, dict) else (s, None)
        try:
            u = urlparse(str(url))
        except ValueError:
            continue
        if u.scheme == "https" and u.netloc:
            out.append((str(url), title or u.netloc.removeprefix("www.")))
    return out


def nice_date(date: str) -> str:
    d = dt.date.fromisoformat(date)
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B %Y')}"


def minutes(seconds) -> str:
    s = int(seconds or 0)
    return f"{s // 60}:{s % 60:02d}"


def build(meta: dict) -> str:
    date = meta["date"]
    page = f"{SITE}/?d={date}"
    mp3 = f"{SITE}/{meta.get('audio', f'audio/{date}.mp3')}"

    chips = "".join(
        f'<td style="padding:8px 10px;border:1px solid {LINE};border-radius:10px;background:#fff;'
        f'font-size:12px;color:{DIM};white-space:nowrap">{e(m.get("name"))}<br>'
        f'<b style="font-size:15px;color:{INK}">{e(m.get("value"))}</b> '
        f'<span style="color:{"#c0392b" if str(m.get("move", "")).startswith(("-", "−")) else "#1e8e5a"}">'
        f'{e(m.get("move"))}</span></td><td style="width:6px"></td>'
        for m in meta.get("markets", []))

    stories = []
    for s in meta.get("stories", []):
        links = " &middot; ".join(f'<a href="{e(u)}" style="color:{DIM}">{e(t)}</a>' for u, t in sources(s.get("sources")))
        stories.append(
            f'<tr><td style="padding:18px 0;border-top:1px solid {LINE}">'
            f'<div style="font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:{SUN};font-weight:bold">{e(s.get("tag"))}</div>'
            f'<div style="font-size:19px;line-height:1.3;font-weight:bold;color:{INK};margin:4px 0 8px">{e(s.get("headline"))}</div>'
            f'<div style="font-size:15px;line-height:1.55;color:{INK}">{e(s.get("summary"))}</div>'
            + (f'<div style="margin-top:10px;padding:10px 12px;background:#fff4e6;border-left:3px solid {SUN};'
               f'font-size:14px;line-height:1.5;color:{INK}"><b>Why it matters to you:</b> {e(s.get("why"))}</div>'
               if s.get("why") else "")
            + (f'<div style="margin-top:8px;font-size:12px;color:{DIM}">Sources: {links}</div>' if links else "")
            + "</td></tr>")

    watch = (f'<tr><td style="padding:16px;border:1px dashed {LINE};border-radius:12px;font-size:14px;color:{INK}">'
             f'<b>One to watch:</b> {e(meta.get("watch"))}</td></tr>') if meta.get("watch") else ""

    return f"""<!doctype html><html><body style="margin:0;padding:0;background:{BG}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG}"><tr><td align="center" style="padding:20px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:620px;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
<tr><td style="font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:{DIM}">Morning Brief &middot; {e(nice_date(date))}</td></tr>
<tr><td style="font-size:28px;line-height:1.2;font-weight:bold;color:{INK};padding:8px 0 6px">{e(meta.get("title"))}</td></tr>
<tr><td style="font-size:16px;line-height:1.55;color:{INK};padding-bottom:14px">{e(meta.get("lede"))}</td></tr>
<tr><td style="padding:4px 0 18px">
  <a href="{e(page)}" style="display:inline-block;background:{SUN};color:#fff;text-decoration:none;font-weight:bold;padding:12px 20px;border-radius:24px">&#9654; Listen ({minutes(meta.get("seconds"))})</a>
  <a href="{e(mp3)}" style="margin-left:12px;color:{DIM};font-size:13px">MP3</a>
</td></tr>
<tr><td style="padding-bottom:16px"><table role="presentation" cellpadding="0" cellspacing="0"><tr>{chips}</tr></table></td></tr>
{"".join(stories)}
{watch}
<tr><td style="padding:22px 0;font-size:12px;color:{DIM}">Written and voiced automatically from the sources listed. Reply with what you'd like more or less of.</td></tr>
</table></td></tr></table></body></html>
"""


def write(meta: dict, docs: pathlib.Path) -> pathlib.Path:
    out = docs / "email" / f"{meta['date']}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(meta), encoding="utf-8")
    return out


if __name__ == "__main__":
    # Fallback for the routine when the audio is late: python3 tools/news_page.py DATE
    import json
    import sys

    root = pathlib.Path(__file__).resolve().parent.parent
    day = sys.argv[1]
    data = json.loads((root / "episodes" / f"{day}.json").read_text(encoding="utf-8"))
    data["date"] = day
    print(write(data, pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else root / "docs"))
