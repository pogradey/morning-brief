"""Copy the newest built episode into artifact/ so the Claude artifact page can be republished with it.

Usage: python tools/artifact_files.py [OUT_DIR]   -> prints the date it prepared, or exits 1 if no audio is built yet.
The artifact page cannot load anything from other sites, so it ships its own episode.json and episode.mp3.
"""
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
episodes = json.loads((ROOT / "docs" / "episodes.json").read_text(encoding="utf-8"))
latest = next((e for e in episodes if (ROOT / "docs" / e["audio"]).exists()), None)
if not latest:
    sys.exit("No built episode yet.")
out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "artifact"
out.mkdir(parents=True, exist_ok=True)
if out != ROOT / "artifact":
    shutil.copyfile(ROOT / "artifact" / "index.html", out / "index.html")
shutil.copyfile(ROOT / "docs" / latest["audio"], out / "episode.mp3")
(out / "episode.json").write_text(json.dumps(latest, ensure_ascii=False, indent=1), encoding="utf-8")
print(latest["date"])
