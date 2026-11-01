"""Check the voice path end to end with a REAL recording: audio file -> /api/voice/transcribe -> /api/chat.

    cd backend && python -m scripts.voice_check my_question.m4a --lang mr --expect "मी माझा कांदा आता विकू की थांबू" [--base http://localhost:8000]

Record a clip on a phone (webm, m4a, mp3, wav, ogg), say a short question in Hindi or Marathi, and run this. It prints what the speech
service heard, the word error rate against what you meant to say (when --expect is given), and the intent and language the agent
chose for it. Nothing here is mocked: this is the check that was never done with a human voice, so quote its numbers with the
speaker, device, language and date, not as a general accuracy.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

import httpx


def _words(s: str) -> list[str]:
    s = unicodedata.normalize("NFC", s or "").lower()
    return re.sub(r"[^\w\s]", " ", s).split()


def wer(reference: str, heard: str) -> float:
    """Word error rate: word-level edit distance divided by the reference length (0.0 = exact)."""
    r, h = _words(reference), _words(heard)
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i]
        for j, hw in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw)))
        prev = cur
    return prev[-1] / len(r)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--lang", default=None, help="hi, mr, en, ... (the language you spoke; omit to let the service detect it)")
    ap.add_argument("--expect", default=None, help="the sentence you meant to say, to compute the word error rate")
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--place", default="Nashik, Maharashtra")
    ap.add_argument("--crop", default="Onion")
    a = ap.parse_args(argv)
    path = Path(a.audio)
    if not path.is_file():
        print(f"no such file: {path}")
        return 2
    with httpx.Client(timeout=90) as c:
        r = c.post(f"{a.base}/api/voice/transcribe", files={"file": (path.name, path.read_bytes())}, data={"language": a.lang} if a.lang else {}).json()
        heard = r.get("text", "")
        print("HEARD:", heard or "(nothing)", ("| " + r["error"]) if r.get("error") else "")
        if a.expect is not None:
            print(f"WORD ERROR RATE vs expected: {wer(a.expect, heard):.0%}  (0% = exact; expected: {a.expect})")
        if not heard:
            return 1
        chat = c.post(f"{a.base}/api/chat", json={"message": heard, "language": a.lang, "profile": {"place": a.place, "crop": a.crop}}).json()
    print(f"AGENT: intent={chat.get('intent')} language={chat.get('language')} degraded={chat.get('degraded')}")
    print((chat.get("answer") or "")[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
