"""Writing helpers (spec 5.11): rhymes and syllable counts from the CMU dictionary."""

from __future__ import annotations

from fastapi.testclient import TestClient

from songvault.words import find_rhymes, guess_syllables, line_syllables


def _all(groups: list) -> set[str]:
    return {w for g in groups for w in g.words}


def test_syllables_per_line() -> None:
    assert line_syllables("Headlights on the county road") == 7
    # Chords and tags aren't sung.
    assert line_syllables("[D]Headlights on the [A]county road #hook #sad") == 7
    assert line_syllables("Café at 3am, everything tastes like rain") == 10  # ca-fe at am ev-ery-thing tastes like rain
    assert line_syllables("") is None
    assert line_syllables("   [G] [D]  #intro") is None


def test_words_it_doesnt_know_are_guessed() -> None:
    assert guess_syllables("blorptastic") == 3
    assert guess_syllables("snake") == 1
    assert guess_syllables("table") == 2
    assert guess_syllables("ok") == 1
    assert line_syllables("zzyzx road") == 2


def test_perfect_and_near_rhymes() -> None:
    r = find_rhymes("road", limit=1000)
    assert r.known and r.word == "road" and r.syllables == 1
    perfect, near = _all(r.perfect), _all(r.near)
    assert {"load", "owed", "toad", "slowed", "explode"} <= perfect
    assert "road" not in perfect and "railroad" not in perfect  # not itself, nor a word ending in it
    assert {"boat", "coat", "joke"} <= near and not perfect & near
    assert not {"boast", "approached", "bold"} & near  # a different ending isn't near enough
    # Grouped by syllables, fewest first.
    assert [g.syllables for g in r.perfect] == sorted(g.syllables for g in r.perfect)
    assert "explode" in next(g for g in r.perfect if g.syllables == 2).words


def test_everyday_words_come_before_names() -> None:
    one = next(g for g in find_rhymes("rain").perfect if g.syllables == 1).words
    assert {"brain", "chain", "gain", "train"} <= set(one[:20])  # "train" ends in "rain" but isn't a compound
    two = next(g for g in find_rhymes("night").perfect if g.syllables == 2).words
    assert {"delight", "invite", "polite"} <= set(two[:15])
    low = next(g for g in find_rhymes("low").perfect if g.syllables == 1).words
    assert {"blow", "know", "slow", "go", "so", "no"} <= set(low) and not {"bo", "co", "jo", "mo"} & set(low)
    heart = next(g for g in find_rhymes("heart").perfect if g.syllables == 1).words
    assert set(heart[:7]) <= {"cart", "chart", "dart", "part", "smart", "start", "tart", "art", "mart", "hart"}


def test_rhymes_for_a_phrase_use_its_last_word() -> None:
    assert find_rhymes("down the open road,").word == "road"
    # A word said two ways rhymes with both: fire as "fi-er" and "fire".
    assert {"tire", "higher", "liar"} <= _all(find_rhymes("Fire", limit=1000).perfect)


def test_unknown_word() -> None:
    r = find_rhymes("blorptastic")
    assert (r.known, r.perfect, r.near) == (False, [], [])


def test_api(authed: TestClient, client: TestClient) -> None:
    r = authed.get("/api/words/rhymes", params={"word": "heart"})
    assert r.status_code == 200 and "start" in _all_json(r.json()["perfect"])
    r = authed.post("/api/words/syllables", json={"lines": ["one two three", "", "[G] #tag", "beautiful"]})
    assert r.json() == {"counts": [3, None, None, 3]}
    assert authed.get("/api/words/rhymes", params={"word": ""}).status_code == 422


def _all_json(groups: list[dict]) -> set[str]:
    return {w for g in groups for w in g["words"]}


def test_needs_sign_in(client: TestClient) -> None:
    assert client.get("/api/words/rhymes", params={"word": "road"}).status_code == 401
