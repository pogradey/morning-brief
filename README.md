# Morning Brief

A five minute spoken news briefing, made every morning by a Claude cloud routine.

- `PREFERENCES.md`: who it is for and what he has asked for. Feedback goes in "Learned".
- `BRIEF_RULES.md`: how the routine researches, writes, builds and emails each episode.
- `episodes/DATE.txt`: the script as spoken. `episodes/DATE.json`: headlines, markets and sources.
- `tools/make_episode.py`: script to MP3 (free Microsoft Edge voices via `edge-tts`), refreshes `docs/`.
- `docs/`: the player page, served by GitHub Pages at https://pogradey.github.io/morning-brief/
  (`docs/feed.xml` is a podcast feed if he ever wants it in Apple Podcasts).

Change the voice or speed at the top of `tools/make_episode.py`.
