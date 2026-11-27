"""Turn episodes/DATE.txt (a two-host script) into docs/audio/DATE.mp3, then refresh docs/episodes.json and feed.xml.

The script is a conversation between the hosts in tools/hosts.json, one turn per line:

    MAYA: Good morning Pearce. It's Saturday the twenty-eighth of November.
    TOM: And the big one today is jobs. [surprise-oh] Way better than anyone expected.

Commands:
    python tools/make_episode.py all DATE                   render every turn here, then assemble
    python tools/make_episode.py render DATE --shard 2 --of 6   render one slice of the turns into parts/DATE/
    python tools/make_episode.py assemble DATE              join parts/DATE/ into the mp3 and update the index
    python tools/make_episode.py check DATE                 parse the script and print turns and words only

Engines (BRIEF_TTS):
- omnivoice (default): k2-fsa OmniVoice, the engine behind VoiceStudio. Each host is cloned from a short
  reference clip in voices/, so the voices stay the same every day. CPU only, about 6.5x slower than real
  time on a GitHub runner, so the workflow splits the turns across several runners.
- kokoro: the small offline Kokoro model with one stock voice per host. The workflow falls back to it
  if OmniVoice fails, so an episode always ships.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time
from email.utils import format_datetime
from xml.sax.saxutils import escape

import numpy as np

import news_page

ENGINE = os.environ.get("BRIEF_TTS", "omnivoice")
KOKORO_DIR = pathlib.Path(os.environ.get("KOKORO_DIR", "models"))
MAX_WORDS = 950
SOFT_MAX_SECONDS = 330      # longer than this is logged as a warning; the episode still ships
CHUNK_WORDS = 45            # a long turn is split at sentence ends into pieces of about this size
PIECE_GAP_MS = 120          # pause between pieces of one turn
KEEP_DAYS = 30
SITE = "https://pogradey.github.io/morning-brief"
SR = 24000                  # both engines speak at 24 kHz

ROOT = pathlib.Path(__file__).resolve().parent.parent
EPISODES = ROOT / "episodes"
DOCS = ROOT / "docs"
AUDIO = DOCS / "audio"
PARTS = ROOT / "parts"
CONFIG = json.loads((ROOT / "tools" / "hosts.json").read_text(encoding="utf-8"))
HOSTS = CONFIG["hosts"]

# Tags OmniVoice performs instead of reading aloud. Anything else in square brackets is removed.
TAG_WORDS = ("laughter|sigh|confirmation-en|question-en|question-ah|question-oh|surprise-ah|"
             "surprise-oh|surprise-wa|dissatisfaction-hnn")
KNOWN_TAG = re.compile(rf"\[({TAG_WORDS})\]")
ANY_TAG = re.compile(r"\[[^\]]*\]")


# ---------- script ----------

def parse_script(text: str) -> list[tuple[str, str]]:
    """Return [(host, line)]. A line without a known 'NAME:' prefix continues the previous speaker."""
    turns: list[tuple[str, str]] = []
    names = "|".join(re.escape(n) for n in HOSTS)
    prefix = re.compile(rf"^\s*\**\s*({names})\s*\**\s*:\s*(.*)$", re.IGNORECASE)
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = prefix.match(line)
        if m:
            turns.append((m.group(1).upper(), m.group(2).strip()))
        elif turns:
            host, said = turns[-1]
            turns[-1] = (host, f"{said} {line}")
        else:
            sys.exit(f"The script must start with a host name ({', '.join(HOSTS)}), got: {line[:60]}")
    turns = [(h, clean(t)) for h, t in turns]
    return [(h, t) for h, t in turns if spoken_words(t)]


def clean(text: str) -> str:
    """Keep the tags OmniVoice acts out, drop every other bracketed or markdown bit."""
    keep = ENGINE == "omnivoice"
    text = ANY_TAG.sub(lambda m: m.group(0) if keep and KNOWN_TAG.fullmatch(m.group(0)) else " ", text)
    text = text.replace("*", " ").replace("#", " ")
    return re.sub(r"\s+", " ", text).strip()


def spoken_words(text: str) -> int:
    return len(ANY_TAG.sub(" ", text).split())


def chunks(text: str) -> list[str]:
    """Split a long turn at sentence ends so each piece stays near CHUNK_WORDS."""
    out, cur = [], ""
    for s in re.split(r"(?<=[.!?])\s+", text):
        if cur and spoken_words(f"{cur} {s}") > CHUNK_WORDS:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def load_script(date: str) -> list[tuple[str, str]]:
    dt.date.fromisoformat(date)
    turns = parse_script((EPISODES / f"{date}.txt").read_text(encoding="utf-8"))
    words = sum(spoken_words(t) for _, t in turns)
    if words > MAX_WORDS:
        sys.exit(f"Script is {words} words; the limit is {MAX_WORDS}. Cut it and run again.")
    if len({h for h, _ in turns}) < 2:
        print("WARNING: only one host speaks in this script.")
    return turns


# ---------- audio ----------

def level(x: np.ndarray, target_rms: float = 0.07) -> np.ndarray:
    """Bring each piece to the same loudness so neither host is quieter."""
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    rms = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
    if rms > 1e-4:
        x = x * (target_rms / rms)
    return np.clip(x, -0.98, 0.98)


def silence(ms: int) -> np.ndarray:
    return np.zeros(int(SR * ms / 1000), dtype=np.float32)


class OmniVoiceSpeaker:
    def __init__(self):
        import torch
        from omnivoice.models.omnivoice import OmniVoice

        # Leave torch on its default thread count: on a 4-vCPU runner, 4 threads ran twice as slow as 2.
        t0 = time.time()
        self.torch = torch
        self.model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map="cpu", dtype=torch.float32)
        self.prompts = {name: self.model.create_voice_clone_prompt(str(ROOT / h["ref"]), ref_text=h["ref_text"])
                        for name, h in HOSTS.items()}
        print(f"OmniVoice ready in {time.time() - t0:.0f}s on {torch.get_num_threads()} threads", flush=True)

    def say(self, host: str, text: str, seed: int, duration: float | None = None) -> np.ndarray:
        self.torch.manual_seed(seed)
        audio = self.model.generate(text=text, language="English", voice_clone_prompt=self.prompts[host],
                                    num_step=int(CONFIG.get("num_step", 16)), duration=duration,
                                    speed=None if duration else float(HOSTS[host].get("speed", 1.0)))[0]
        return np.asarray(audio, dtype=np.float32).reshape(-1)


class KokoroSpeaker:
    def __init__(self):
        from kokoro_onnx import Kokoro
        self.kokoro = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))

    def say(self, host: str, text: str, seed: int) -> np.ndarray:
        h = HOSTS[host]
        samples, _ = self.kokoro.create(text, voice=h["kokoro"], speed=float(h.get("kokoro_speed", 1.05)),
                                        lang=h.get("kokoro_lang", "en-us"))
        return np.asarray(samples, dtype=np.float32).reshape(-1)


def say_safely(speaker, host: str, text: str, seed: int, label: str) -> np.ndarray:
    """Very short lines ("See you then.") can come back near-silent. Retry them with a set length; never fail
    the whole episode over one short line unless it is completely empty."""
    audio = speaker.say(host, text, seed=seed)
    if len(audio) >= SR * 0.25 or not isinstance(speaker, OmniVoiceSpeaker):
        return audio
    target = max(0.9, 0.38 * spoken_words(text) + 0.3)
    print(f"{label}: only {len(audio) / SR:.2f}s, retrying at {target:.1f}s", flush=True)
    retry = speaker.say(host, text, seed=seed + 500, duration=target)
    best = retry if len(retry) > len(audio) else audio
    if len(best) == 0:
        raise RuntimeError(f"{label} came back empty")
    return best


def render(date: str, shard: int, of: int) -> None:
    """Render turns shard, shard+of, shard+2*of ... into parts/DATE/turn_NNN.flac."""
    import soundfile as sf

    turns = load_script(date)
    mine = [i for i in range(len(turns)) if i % of == shard]
    out = PARTS / date
    out.mkdir(parents=True, exist_ok=True)
    speaker = OmniVoiceSpeaker() if ENGINE == "omnivoice" else KokoroSpeaker()
    t0 = time.time()
    for n, i in enumerate(mine, 1):
        host, said = turns[i]
        pieces = []
        for k, piece in enumerate(chunks(said)):
            audio = say_safely(speaker, host, piece, seed=1000 * (i + 1) + k, label=f"turn {i + 1} ({host})")
            pieces += [level(audio), silence(PIECE_GAP_MS)]
        sf.write(str(out / f"turn_{i:03d}.flac"), np.concatenate(pieces[:-1]), SR)
        print(f"shard {shard}: turn {i + 1} ({host}) done, {n}/{len(mine)}, {time.time() - t0:.0f}s", flush=True)


def assemble(date: str) -> None:
    import soundfile as sf

    turns = load_script(date)
    gap = silence(int(CONFIG.get("turn_gap_ms", 260)))
    audio = [silence(300)]
    missing = []
    for i in range(len(turns)):
        f = PARTS / date / f"turn_{i:03d}.flac"
        if not f.exists():
            missing.append(i + 1)
            continue
        x, rate = sf.read(str(f), dtype="float32")
        if rate != SR:
            sys.exit(f"{f.name} is {rate} Hz, expected {SR}")
        audio += [x.reshape(-1), gap]
    if missing:
        sys.exit(f"Missing audio for turns {missing}; not publishing a broken episode.")
    audio = np.concatenate(audio)
    seconds = len(audio) / SR
    if seconds < 30:
        sys.exit(f"Only {seconds:.0f}s of audio came out. Something went wrong.")
    if seconds > SOFT_MAX_SECONDS:
        print(f"WARNING: audio is {seconds:.0f}s, over the {SOFT_MAX_SECONDS}s target. It still ships.")

    AUDIO.mkdir(parents=True, exist_ok=True)
    mp3 = AUDIO / f"{date}.mp3"
    part = AUDIO / f"{date}.tmp.mp3"
    sf.write(str(part), audio, SR, format="MP3")
    part.replace(mp3)

    meta = json.loads((EPISODES / f"{date}.json").read_text(encoding="utf-8"))
    words = sum(spoken_words(t) for _, t in turns)
    meta.update({"date": date, "seconds": round(seconds), "words": words, "engine": ENGINE,
                 "hosts": [HOSTS[h].get("display", h.title()) for h in HOSTS],
                 "audio": f"audio/{date}.mp3", "bytes": mp3.stat().st_size})
    update_index(meta)
    print(f"OK {date}: {words} words, {len(turns)} turns, {seconds:.0f}s, {mp3.stat().st_size // 1024} KB, {ENGINE}")


def update_index(meta: dict) -> None:
    date = meta["date"]
    index = DOCS / "episodes.json"
    episodes = json.loads(index.read_text(encoding="utf-8")) if index.exists() else []
    episodes = [e for e in episodes if e["date"] != date] + [meta]
    episodes.sort(key=lambda e: e["date"], reverse=True)

    cutoff = (dt.date.fromisoformat(date) - dt.timedelta(days=KEEP_DAYS)).isoformat()
    for old in [e for e in episodes if e["date"] < cutoff]:
        (DOCS / old["audio"]).unlink(missing_ok=True)
        (DOCS / "email" / f"{old['date']}.html").unlink(missing_ok=True)
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
    print(f"news page: {news_page.write(meta, DOCS).relative_to(ROOT)}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["all", "render", "assemble", "check"])
    p.add_argument("date")
    p.add_argument("--shard", type=int, default=0)
    p.add_argument("--of", type=int, default=1)
    a = p.parse_args()
    if a.command == "check":
        turns = load_script(a.date)
        for host, said in turns:
            print(f"{host}: {said}")
        print(f"{len(turns)} turns, {sum(spoken_words(t) for _, t in turns)} words")
    elif a.command == "render":
        render(a.date, a.shard, a.of)
    elif a.command == "assemble":
        assemble(a.date)
    else:
        render(a.date, 0, 1)
        assemble(a.date)


if __name__ == "__main__":
    main()
