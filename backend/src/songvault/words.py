"""Writing helpers (spec 5.11): rhymes and syllable counts for the Notebook and the
lyrics page.

They come from the CMU Pronouncing Dictionary (`data/cmudict.dict.gz`, about 135,000
words; BSD-style licence in `data/cmudict-LICENSE.txt`), kept on the server so no outside
service sees the words. It's read on first use (about a second) and kept in memory.

- A word's syllables are the vowels in its pronunciation.
- Perfect rhymes share everything from the last stressed vowel on ("road", "owed").
- Near rhymes share those vowels and end in the same number of consonants of the same
  kind: a stop, a nasal, a hiss, an L, an R, or no consonant ("road", "boat").
- The dictionary is full of surnames ("brode", "goedde"). Everyday words come first: a
  word with other forms in the dictionary ("joke": jokes, joked, joking) is a real word.
"""

from __future__ import annotations

import gzip
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/words", tags=["words"])

DICT_PATH = Path(__file__).parent / "data" / "cmudict.dict.gz"
VOWELS = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW"}
ENDING = {
    **dict.fromkeys(("P", "B", "T", "D", "K", "G"), "stop"),
    **dict.fromkeys(("M", "N", "NG"), "nasal"),
    **dict.fromkeys(("F", "V", "TH", "DH", "S", "Z", "SH", "ZH", "HH", "CH", "JH"), "hiss"),
    "L": "l",
    "R": "r",
    "W": "glide",
    "Y": "glide",
}
PER_GROUP = 60
"""Words shown per syllable count; the rest are counted as `more`."""

# Words as they're written: letters, with apostrophes inside ("don't", "rock'n'roll").
WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)*")
CHORD = re.compile(r"\[[^\]\n]*\]")
TAG = re.compile(r"(?<![\w#&/])#[\w-]*[^\W\d_][\w-]*")
SUGGESTION = re.compile(r"^[a-z]+(?:'[a-z]+)?$")
# Words this short in the dictionary are mostly initials and abbreviations ("bo", "co", "jo"):
# only these are suggested.
SHORT_WORDS = set(
    "a i ah am an as at aw ax ay be by do eh go ha he hi ho if in is it lo ma me my no of oh ok "
    "on or ow ox pa so to uh um up us we ya ye yo".split()
)


def _plain(word: str) -> str:
    """Lowercase, without accents: "Café" -> "cafe"."""
    return unicodedata.normalize("NFKD", word).encode("ascii", "ignore").decode().lower()


@dataclass
class _Dictionary:
    syllables: dict[str, int] = field(default_factory=dict)
    perfect_keys: dict[str, tuple[str, ...]] = field(default_factory=dict)
    near_keys: dict[str, tuple[str, ...]] = field(default_factory=dict)
    by_perfect: dict[str, list[str]] = field(default_factory=dict)
    by_near: dict[str, list[str]] = field(default_factory=dict)


def _rhyme_part(phones: list[str]) -> list[str]:
    """From the last stressed vowel to the end (primary stress, else secondary, else the
    last vowel), stress marks taken off."""
    for stress in ("1", "2", "0"):
        for i in range(len(phones) - 1, -1, -1):
            if phones[i].endswith(stress) and phones[i][:-1] in VOWELS:
                return [p.rstrip("012") for p in phones[i:]]
    return [p.rstrip("012") for p in phones]


def _near_key(part: list[str]) -> str:
    vowels = " ".join(p for p in part if p in VOWELS)
    end = part[-1]
    # (A few entries, like "hmm", have no vowel at all.)
    after_last_vowel = len(part) - 1 - max((i for i, p in enumerate(part) if p in VOWELS), default=-1)
    return f"{vowels}|{'vowel' if end in VOWELS else ENDING.get(end, end)}|{after_last_vowel}"


def _load() -> _Dictionary:
    d = _Dictionary()
    perfect: dict[str, set[str]] = {}
    near: dict[str, set[str]] = {}
    with gzip.open(DICT_PATH, "rt", encoding="utf-8") as f:
        for line in f:
            entry = line.split("#", 1)[0].split()
            if len(entry) < 2:
                continue
            word = entry[0].split("(", 1)[0]
            phones = entry[1:]
            if word not in d.syllables:  # the first pronunciation counts the syllables
                d.syllables[word] = sum(p[:-1] in VOWELS for p in phones if p[-1].isdigit())
            part = _rhyme_part(phones)
            perfect.setdefault(word, set()).add(" ".join(part))
            near.setdefault(word, set()).add(_near_key(part))
    def suggestable(word: str) -> bool:
        return bool(SUGGESTION.match(word)) and (len(word) > 2 or word in SHORT_WORDS)

    for word, keys in perfect.items():
        d.perfect_keys[word] = tuple(keys)
        if suggestable(word):
            for key in keys:
                d.by_perfect.setdefault(key, []).append(word)
    for word, keys in near.items():
        d.near_keys[word] = tuple(keys)
        if suggestable(word):
            for key in keys:
                d.by_near.setdefault(key, []).append(word)
    return d


_dictionary: _Dictionary | None = None
_lock = threading.Lock()


def dictionary() -> _Dictionary:
    global _dictionary
    if _dictionary is None:
        with _lock:
            if _dictionary is None:
                _dictionary = _load()
    return _dictionary


def guess_syllables(word: str) -> int:
    """For words the dictionary doesn't have: groups of vowels, less a silent e."""
    w = _plain(word).replace("'", "")
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    return max(1, len(re.findall(r"[aeiouy]+", w)))


def word_syllables(word: str) -> int:
    plain = _plain(word)
    known = dictionary().syllables.get(plain)
    return known if known is not None else guess_syllables(word)


def line_syllables(line: str) -> int | None:
    """The syllables sung on a line: chords in [brackets] and #tags don't count. None for a
    line with no words."""
    words = WORD.findall(TAG.sub(" ", CHORD.sub("", line)))
    return sum(word_syllables(w) for w in words) if words else None


# --- the API ----------------------------------------------------------------------------


class RhymeGroup(BaseModel):
    syllables: int
    words: list[str]
    more: int
    """How many more there are with this many syllables, beyond the ones listed."""


class Rhymes(BaseModel):
    word: str
    known: bool
    """In the dictionary. If not, there are no rhymes to offer."""
    syllables: int | None
    perfect: list[RhymeGroup]
    near: list[RhymeGroup]


class LinesIn(BaseModel):
    lines: list[str] = Field(max_length=1000)


class SyllableCounts(BaseModel):
    counts: list[int | None]
    """One per line, None for a line with no words."""


def everyday(word: str, known: dict[str, int]) -> int:
    """How surely a word is an everyday word rather than a name: 2 if it has a past tense
    or an -ly form in the dictionary, or is one (surnames almost never do: there's no
    "Bainesed"); 1 if it has a plural or an -ing form (surnames can: Baines, Harding);
    0 otherwise."""
    w = word
    strong = (w + "ed", w + w[-1] + "ed", w + "ly")
    if w.endswith("e") and w[:-1] not in known:  # "framed" is frame's, but "pained" isn't Paine's
        strong += (w + "d",)
    elif w.endswith("y"):
        strong += (w[:-1] + "ied",)
    if any(f in known for f in strong):
        return 2
    if len(w) > 4 and w.endswith(("ed", "ly")):
        stems = (w[:-2], w[:-1], w[:-3], w[:-3] + "y")
        if any(len(s) > 2 and s in known for s in stems):
            return 2
    weak = (w + "s", w + "es", w + "ing") + ((w[:-1] + "ing",) if w.endswith("e") else ())
    if any(f in known for f in weak) or (w.endswith("s") and w[:-1] in known):
        return 1
    return 0


def _grouped(words: set[str], syllables: dict[str, int], limit: int) -> list[RhymeGroup]:
    groups: dict[int, list[str]] = {}
    for w in words:
        groups.setdefault(syllables.get(w, 1), []).append(w)
    out = []
    for n, ws in sorted(groups.items()):
        ranked = sorted(ws, key=lambda w: (-everyday(w, syllables), w))
        out.append(RhymeGroup(syllables=n, words=ranked[:limit], more=max(0, len(ws) - limit)))
    return out


def find_rhymes(text: str, limit: int = PER_GROUP) -> Rhymes:
    """Rhymes for the last word of `text` (so a phrase or a whole line works too)."""
    found = WORD.findall(text)
    word = _plain(found[-1]) if found else ""
    d = dictionary()
    if word not in d.perfect_keys:
        return Rhymes(word=word, known=False, syllables=guess_syllables(word) if word else None, perfect=[], near=[])

    def candidate(w: str) -> bool:
        # Not the word itself, nor a compound that ends in it ("railroad" for "road": rail is
        # a word too). "train" for "rain" is fine.
        front = w[: -len(word)]
        return w != word and not (w.endswith(word) and len(front) >= 2 and front in d.syllables)

    perfect = {w for key in d.perfect_keys[word] for w in d.by_perfect.get(key, ()) if candidate(w)}
    near = {w for key in d.near_keys[word] for w in d.by_near.get(key, ()) if candidate(w)} - perfect
    return Rhymes(
        word=word,
        known=True,
        syllables=d.syllables[word],
        perfect=_grouped(perfect, d.syllables, limit),
        near=_grouped(near, d.syllables, limit),
    )


@router.get("/rhymes")
def get_rhymes(word: str = Query(min_length=1, max_length=200)) -> Rhymes:
    return find_rhymes(word)


@router.post("/syllables")
def count_syllables(body: LinesIn) -> SyllableCounts:
    return SyllableCounts(counts=[line_syllables(line[:2000]) for line in body.lines])
