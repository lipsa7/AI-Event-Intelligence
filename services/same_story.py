"""Find news events that report the SAME story (v13.6, 20 Nov 2026).

Why this exists
---------------
One story is usually reported by several outlets. In the saved corpus the OpenAI /
Australia breach arrived as about ten separate events, so the insight exploration spent
several of its few slots on the same story and never reached other stories. The first
v13.6 attempt merged groups that shared one or two capitalised words; that chained
unrelated items together ("China", "Sept", "Anthony" - a reporter's first name), producing
a "China" story that mixed BRICS, a coal deal and a funding round.

How it works (standard near-duplicate detection, in plain words)
----------------------------------------------------------------
* Each event becomes a set of content words from its title and summary. Outlet names at
  the end of a title ("... - SiliconANGLE") and bylines ("by Maria Deutscher") are removed,
  because they are shared by unrelated stories from the same outlet.
* Rare words count more than common words (inverse document frequency: a word found in
  few events of this run is strong evidence; "artificial" or "OpenAI" is weak evidence).
* Two events are the same story when the weighted share of the SHORTER event's words that
  the other also has is at least ``threshold`` (0.40), they share at least four content
  words, and at least two of the shared words are names (capitalised mid-sentence) in both.
* Thresholds were chosen on the saved 657-event corpus (10 Sep - 25 Oct 2026): at 0.40 the
  breach becomes one 10-event story with no unrelated item; at 0.35 unrelated stories chain
  into 30-event groups. Known remaining false merge: interview write-ups from one outlet
  that share its hosts' names (low insight value, so harmless for exploration).
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Mapping, Sequence

_STOP = frozenset("""that this with from have were been their they said will would about which after more than
into when what also over some there other these those such only just year years month months last first since while
where could should being under most many much very week weeks earlier today tuesday monday wednesday thursday friday
saturday sunday january february march april june july august september october november december sept company
companies including according announced reported report reports says told added people time based like make made
because does even well still back them then here percent million billion""".split())
_OUTLET_SUFFIX = re.compile(r"\s+[-|–—]\s+[A-Z][^-|–—]{1,30}$")
_BYLINE = re.compile(r"\b[Bb]y\s+[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+){1,2}(?:\s+and\s+[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+){1,2})?")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9-]{3,}")
_SENTENCE_START = re.compile(r"(?:^|[.!?:\"'(]\s*)$")

THRESHOLD = 0.40
MIN_SHARED_WORDS = 4
MIN_SHARED_NAMES = 2


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def story_words(event: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    """(content words, names) of one event; names are words capitalised mid-sentence."""
    raw_title = _clean(event.get("title"))
    suffix = _OUTLET_SUFFIX.search(raw_title)
    outlet = {w.casefold() for w in _WORD.findall(suffix.group(0))} if suffix else set()
    text = _BYLINE.sub(" ", " ".join((raw_title, _clean(event.get("summary")))))
    words: set[str] = set()
    names: set[str] = set()
    for match in _WORD.finditer(text):
        key = match.group(0).casefold()
        if key in _STOP or key in outlet:
            continue
        words.add(key)
        if match.group(0)[0].isupper() and not _SENTENCE_START.search(text[max(0, match.start() - 3):match.start()]):
            names.add(key)
    return words, names


def same_story_pairs(events: Sequence[Mapping[str, Any]], threshold: float = THRESHOLD) -> list[tuple[int, int]]:
    """Index pairs (i, j) of events that report the same story."""
    tokens = [story_words(e) for e in events]
    total = len(tokens) or 1
    doc_freq: dict[str, int] = defaultdict(int)
    for words, _ in tokens:
        for word in words:
            doc_freq[word] += 1
    idf = {w: math.log(total / c) for w, c in doc_freq.items()}
    weight = [sum(idf[w] for w in words) for words, _ in tokens]
    # Only pairs sharing at least one fairly rare word are compared (keeps this fast).
    rare_limit = max(3, int(0.05 * total))
    index: dict[str, list[int]] = defaultdict(list)
    for i, (words, _) in enumerate(tokens):
        for word in words:
            if doc_freq[word] <= rare_limit:
                index[word].append(i)
    candidates: set[tuple[int, int]] = set()
    for ids in index.values():
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                candidates.add((ids[a], ids[b]))
    pairs = []
    for i, j in sorted(candidates):
        shared = tokens[i][0] & tokens[j][0]
        if len(shared) < MIN_SHARED_WORDS:
            continue
        if len(shared & tokens[i][1] & tokens[j][1]) < MIN_SHARED_NAMES:
            continue
        if sum(idf[w] for w in shared) / (min(weight[i], weight[j]) or 1.0) >= threshold:
            pairs.append((i, j))
    return pairs


def story_components(events: Sequence[Mapping[str, Any]], max_size: int | None = None) -> list[int]:
    """Story number for every event (events of one story share a number).

    ``max_size`` stops runaway chaining: a link that would make a story larger than it is
    skipped (default: the larger of 25 events and 4% of the run).
    """
    count = len(events)
    limit = max_size or max(25, int(0.04 * count))
    parent = list(range(count))
    size = [1] * count

    def root(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in same_story_pairs(events):
        a, b = root(i), root(j)
        if a == b or size[a] + size[b] > limit:
            continue
        if size[a] < size[b]:
            a, b = b, a
        parent[b] = a
        size[a] += size[b]
    for i, story in _lone_reports_to_attach(events, [root(k) for k in range(count)]).items():
        a = root(story)
        if size[a] < limit:
            parent[root(i)] = a
            size[a] += 1
    return [root(i) for i in range(count)]


ATTACH_MIN_STORY_EVENTS = 3
ATTACH_MIN_SHARE = 0.25
ATTACH_MIN_SHARED_WORDS = 2


def _lone_reports_to_attach(events: Sequence[Mapping[str, Any]], story_of: Sequence[int]) -> dict[int, int]:
    """v13.8 (20 Nov 2026): a lone report (one event, often one sentence: "Despite the breach
    happening in June, Albanese said the tech giant only notified the government...") is too short
    to pass the pair test, so it stayed a story of its own and took an exploration slot. It joins a
    story of at least three events when ALL of these hold:
    * it names one of the story's CORE names - a name in at least half of the story's events and
      with at least 80% of all its mentions in this run inside that story ("Albanese"; not
      "Microsoft" or "American", which are spread across many stories);
    * exactly one story qualifies (no guessing between stories);
    * at least 25% of its weighted words occur in the story, including at least two words that
      are not names and appear in at most 5% of the run's events ("breach", "incident").
    On the saved corpus this joins two breach reports and nothing else; the closest rejected cases
    (an interview listing, a court case sharing "Gaza" with the Nobel story) scored at most 0.22."""
    tokens = [story_words(e) for e in events]
    total = len(tokens) or 1
    word_freq: dict[str, int] = defaultdict(int)
    name_freq: dict[str, int] = defaultdict(int)
    for words, names in tokens:
        for word in words:
            word_freq[word] += 1
        for name in names:
            name_freq[name] += 1
    idf = {w: math.log(total / c) for w, c in word_freq.items()}
    members: dict[int, list[int]] = defaultdict(list)
    for index, story in enumerate(story_of):
        members[story].append(index)
    stories = {s: m for s, m in members.items() if len(m) >= ATTACH_MIN_STORY_EVENTS}
    cores: dict[int, set[str]] = {}
    unions: dict[int, set[str]] = {}
    for story, indexes in stories.items():
        counts: dict[str, int] = defaultdict(int)
        for index in indexes:
            for name in tokens[index][1]:
                counts[name] += 1
        cores[story] = {n for n, k in counts.items() if k >= max(2, 0.5 * len(indexes)) and k >= 0.8 * name_freq[n]}
        unions[story] = set().union(*(tokens[index][0] for index in indexes))
    rare_limit = max(3, int(0.05 * total))
    out: dict[int, int] = {}
    for story, indexes in members.items():
        if len(indexes) != 1:
            continue
        index = indexes[0]
        words, names = tokens[index]
        hits = [s for s in stories if names & cores[s]]
        if len(hits) != 1 or not words:
            continue
        target = hits[0]
        shared = words & unions[target]
        share = sum(idf[w] for w in shared) / (sum(idf[w] for w in words) or 1.0)
        rare_shared = [w for w in shared - names if word_freq[w] <= rare_limit]
        if share >= ATTACH_MIN_SHARE and len(rare_shared) >= ATTACH_MIN_SHARED_WORDS:
            out[index] = stories[target][0]
    return out
