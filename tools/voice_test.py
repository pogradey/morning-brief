"""One-off speed and voice test for OmniVoice on a GitHub CPU runner. Not part of the daily build."""
import json, pathlib, time
import numpy as np, soundfile as sf, torch
from omnivoice.models.omnivoice import OmniVoice

OUT = pathlib.Path("voicetest")
OUT.mkdir(exist_ok=True)
import os
torch.set_num_threads(os.cpu_count() or 4)
log = {"threads": torch.get_num_threads()}

t = time.time()
model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map="cpu", dtype=torch.float32)
log["load_s"] = round(time.time() - t, 1)
SR = model.sampling_rate

LINE = ("Good morning, Pearce. So the big story today is inflation. It jumped to four point nine per cent, "
        "and honestly, that puts the Reserve Bank in a really tricky spot. Let's get into it.")
DESIGNS = {
    "a_male_aus": "male, young adult, low pitch, australian accent",
    "b_female_aus": "female, young adult, moderate pitch, australian accent",
    "c_male_brit": "male, middle-aged, moderate pitch, british accent",
    "d_female_us": "female, young adult, moderate pitch, american accent",
    "e_male_us": "male, young adult, moderate pitch, american accent",
    "f_female_brit": "female, middle-aged, low pitch, british accent",
    "g_male_aus_mid": "male, middle-aged, moderate pitch, australian accent",
    "h_female_aus_low": "female, young adult, low pitch, australian accent",
}


def timed(name, **kw):
    t = time.time()
    audio = model.generate(language="English", **kw)[0]
    secs = len(audio) / SR
    took = time.time() - t
    sf.write(str(OUT / f"{name}.flac"), audio, SR)
    log[name] = {"audio_s": round(secs, 1), "took_s": round(took, 1), "x_realtime": round(took / secs, 2)}
    print(name, log[name], flush=True)


for i, (name, instruct) in enumerate(DESIGNS.items()):
    torch.manual_seed(100 + i)
    timed(name, text=LINE, instruct=instruct)

REPLY = ("Right, and that matters for you because a rate rise would push rents up, and it makes "
         "borrowing more expensive right when a lot of people your age are trying to save. "
         "It's not great news. [laughter] But there's a bit of good news later, I promise.")
timed("clone_16step", text=REPLY, ref_audio=str(OUT / "b_female_aus.flac"), ref_text=LINE, num_step=16)

(OUT / "timing.json").write_text(json.dumps(log, indent=1))
print(json.dumps(log, indent=1))
