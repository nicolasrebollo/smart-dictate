"""Deterministic voice commands, applied before the AI cleanup (so they work in every mode):

  breaks       "new line", "next paragraph", ...                        -> \\n, \\n\\n
  punctuation  "comma", "period", "question mark", "open quote", ...    -> , . ? "
  editing      "scratch that"                                           -> drop the last sentence

A spoken command can come anywhere ("Hi Sam comma how are you question mark"). The speech
recognizer often adds its own punctuation around it ("Hi Sam, comma, how are you?"); that is
replaced by what was dictated. A phrase is left as an ordinary word when the words around it say so:
"the trial period", "a question mark over it", "a new line of products", "trois virgule cinq",
"gmail punto com".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# What each command inserts. Phrases per language live in config (DEFAULT_PHRASES).
MARKS: dict[str, str] = {
    "new_line": "\n",
    "new_paragraph": "\n\n",
    "period_new_line": ".\n",
    "period_new_paragraph": ".\n\n",
    "comma": ",",
    "period": ".",
    "question_mark": "?",
    "exclamation_mark": "!",
    "colon": ":",
    "semicolon": ";",
    "ellipsis": "…",
    "open_quote": '"',
    "close_quote": '"',
    "open_paren": "(",
    "close_paren": ")",
    "open_question": "¿",
    "open_exclamation": "¡",
}
_OPENERS = {"open_quote", "open_paren", "open_question", "open_exclamation"}
_FR_SPACED = set(";:!?")  # French puts a space before these

# A command word right after one of these is being talked about, not dictated ("the Oxford comma").
_DETERMINERS = {
    "en": "a an the this that these those my your his her its our their one another each every no any some which "
          "what whose oxford serial same",
    "es": "el la los las un una unos unas este esta ese esa aquel aquella mi tu su nuestro nuestra cada ningún "
          "ninguna otro otra al del en buen primer",
    "fr": "le la les l un une des ce cet cette mon ton son ma ta sa notre votre leur chaque aucun aucune autre au "
          "du à bon premier",
}  # fmt: skip
# ... or right before one of these ("a new line of products", "gmail punto com").
_NEXT_LITERAL = {"en": {"of"}, "es": {"de", "del"}, "fr": {"de", "du", "des", "d"}}
_TLDS = {"com", "net", "org", "io", "ai", "co", "app", "dev", "edu", "gov", "ca", "uk", "es", "fr", "mx", "ar"}
_TLDS |= {"py", "js", "ts", "md", "txt", "json", "html", "css", "csv", "pdf", "exe", "sh", "toml", "yaml"}


# "period" is also a common noun ("the waiting period", "a difficult period during the war").
def _words(s: str) -> set[str]:
    return set(s.split())


_PERIOD_BEFORE = _words(
    "trial grace waiting time free billing notice cooling rest study reporting holiday short long given certain "
    "whole entire transition sampling payback warranty honeymoon introductory probation probationary first second "
    "third last next previous final current class lunch difficult tough busy quiet"
)
_PERIOD_AFTER = _words(
    "of when during where in for between before after ends ended starts started lasts lasted is was that which "
    "runs ran from until"
)
_NUMBER_WORDS = _words(
    "zero one two three four five six seven eight nine ten cero uno dos tres cuatro cinco seis siete ocho nueve "
    "diez un deux trois quatre cinq six sept huit neuf dix"
)
# Words a dictated "," ";" ":" shouldn't leave capitalized, when the recognizer had started a new
# sentence there ("Hi Sam. Comma. How are you" -> "Hi Sam, how are you"). Names are never in here.
_LOWER_AFTER_COMMA = _words(
    "a an the and but or so then because if when while although though as since until unless that which who "
    "what where why how this these those there here it its we you they he she him her them us me my your our "
    "their his is are was were be been do does did have has had can could will would should may might must "
    "not no yes also just even still maybe perhaps please thanks thank let let's i'll we'll you'll it's that's "
    "there's here's for to of in on at with from by about like into over after before"
)

_ADDRESS_WORDS = {"arroba", "arobase", "www", "http", "https", "com", "org", "net"}

_ASR_PUNCT = ".,;:!?…"
_WORD_BEFORE = re.compile(r"([\w'’-]+)[ \t]*$")
_WORD_AFTER = re.compile(r"[ \t]*([\w'’-]+)")


@dataclass
class CommandResult:
    text: str
    scratch_previous: bool = False  # "scratch that" with nothing before it in this utterance


def _alt(phrase: str) -> str:
    """'point d'interrogation' -> regex that also accepts ’ and hyphens/spaces between words."""
    words = re.split(r"[ \t-]+", phrase.strip())
    return r"[ \t-]*".join(re.escape(w).replace("'", "['’]") for w in words)


# -- scratch that ----------------------------------------------------------------------------------
_BOUNDARY_BEFORE = r"(?:^|(?<=[.!?,;:\n]))"
_BOUNDARY_AFTER = r"[ \t]*(?:[.!?,;:]+|$)"


def _clause_regex(phrases: list[str]) -> re.Pattern[str] | None:
    alts = [_alt(p) for p in phrases if p.strip()]
    if not alts:
        return None
    return re.compile(
        _BOUNDARY_BEFORE + r"[ \t]*(?:" + "|".join(sorted(alts, key=len, reverse=True)) + r")" + _BOUNDARY_AFTER,
        re.IGNORECASE | re.MULTILINE,
    )


_SENTENCE_END = re.compile(r"[.!?\n]")


def _drop_last_sentence(prefix: str) -> str:
    """Remove the last sentence (or clause run) from `prefix`."""
    body = prefix.rstrip(" \t,;:")
    core = body.rstrip(".!?")  # the sentence being scratched may end with punctuation
    ends = [m.end() for m in _SENTENCE_END.finditer(core)]
    if not ends:
        return ""
    return core[: ends[-1]]


def _apply_scratch(text: str, phrases: list[str]) -> tuple[str, bool]:
    scratch_previous = False
    rx = _clause_regex(phrases)
    if rx is not None:
        while m := rx.search(text):
            prefix = text[: m.start()]
            if not prefix.strip():
                scratch_previous = True
                text = text[m.end() :]
            else:
                text = _drop_last_sentence(prefix) + " " + text[m.end() :].lstrip()
    return text, scratch_previous


# -- breaks and punctuation ------------------------------------------------------------------------
def _mark_regex(phrases: dict[str, list[str]]) -> tuple[re.Pattern[str], dict[str, str]] | None:
    by_alt: list[tuple[str, str]] = []
    for name, plist in phrases.items():
        if name in MARKS:
            by_alt += [(p, name) for p in plist if p.strip()]
    if not by_alt:
        return None
    by_alt.sort(key=lambda x: len(x[0]), reverse=True)
    groups = {f"c{i}": name for i, (_, name) in enumerate(by_alt)}
    body = "|".join(f"(?P<c{i}>{_alt(p)})" for i, (p, _) in enumerate(by_alt))
    return re.compile(r"(?<![\w'’-])(?:" + body + r")(?![\w'’-])", re.IGNORECASE), groups


def _is_literal(name: str, phrase: str, before: str, after: str, language: str) -> bool:
    """Is this occurrence an ordinary word rather than a command?"""
    pm = _WORD_BEFORE.search(before)
    prev = pm.group(1).lower().strip("'’") if pm else ""
    nm = _WORD_AFTER.match(after)
    nxt = nm.group(1).lower() if nm else ""
    dets = _DETERMINERS.get(language, _DETERMINERS["en"]).split()
    if prev in dets or nxt in _NEXT_LITERAL.get(language, set()):
        return True
    single = " " not in phrase.strip()
    if single and nxt in _TLDS and re.match(r"[ \t]+\w", after):  # "gmail punto com", "main point py"
        return True
    if single and (prev in _NUMBER_WORDS or prev.isdigit()) and (nxt in _NUMBER_WORDS or nxt.isdigit()):
        return True  # "trois virgule cinq", "tres punto cinco"
    if single and name == "period":  # inside a dictated address: "ana punto lee arroba gmail punto com"
        near = re.findall(r"[\w@]+", before)[-4:] + re.findall(r"[\w@]+", after)[:4]
        if any(w.lower() in _ADDRESS_WORDS or "@" in w for w in near):
            return True
    return (
        name == "period"
        and phrase.lower() == "period"
        and (prev in _PERIOD_BEFORE or nxt in _PERIOD_AFTER or bool(re.match(r"[ \t]*,", after)))
    )


def _apply_marks(text: str, phrases: dict[str, list[str]], language: str) -> str:
    built = _mark_regex(phrases)
    if built is None:
        return text
    rx, groups = built
    out = ""
    pos = 0
    glue = False  # drop whitespace before the next word (after an opener or a line break)
    case: str = ""  # "upper" / "lower": recase the next word
    for m in rx.finditer(text):
        name = next(groups[g] for g, v in m.groupdict().items() if v is not None)
        between = text[pos : m.start()]
        if _is_literal(name, m.group(0), text[: m.start()], text[m.end() :], language):
            continue
        out += _recase(between.lstrip(" \t") if glue else between, case)
        glue, case = False, ""
        mark = MARKS[name]
        if name in _OPENERS:
            out = out.rstrip(" \t") + ("" if not out or out.endswith(("\n", "(", '"')) else " ") + mark
            glue = True
        else:
            # before a punctuation mark, the recognizer's own guess is replaced by what was dictated
            out = out.rstrip(" \t" + ("" if mark[0] == "\n" else _ASR_PUNCT))
            sep = " " if language == "fr" and mark[0] in _FR_SPACED and out and not out.endswith("\n") else ""
            out += sep + mark
            if mark.endswith("\n"):
                glue, case = True, "upper"
            elif mark in ".?!…":
                case = "upper" if mark != "…" else ""
            elif mark in ",;:" and language == "en":
                case = "lower"
        # skip the recognizer's punctuation right after the command ("Comma." / "new line,")
        pos = m.end()
        while pos < len(text) and text[pos] in _ASR_PUNCT + " \t":
            pos += 1
        if not glue and pos < len(text) and text[pos] not in "\n":
            out += " "
    out += _recase(text[pos:].lstrip(" \t") if glue else text[pos:], case)
    return out


def _recase(s: str, case: str) -> str:
    if not case:
        return s
    m = re.match(r"([\s\"'“‘«(\[¿¡]*)([^\W\d_][\w'’]*)", s)
    if not m:
        return s
    lead, word = m.group(1), m.group(2)
    if case == "upper":
        word = word[0].upper() + word[1:]
    elif word[0].isupper() and word[1:].islower() and word.lower() in _LOWER_AFTER_COMMA and word != "I":
        word = word.lower()
    return lead + word + s[m.end() :]


def apply_commands(text: str, phrases: dict[str, list[str]], language: str = "en") -> CommandResult:
    text, scratch_previous = _apply_scratch(text, phrases.get("scratch_that", []))
    text = _apply_marks(text, phrases, language)
    # tidy whitespace around inserted line breaks (but keep breaks at either end: "new paragraph"
    # at the start or end of a dictation is meant)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return CommandResult(text=text.strip(" \t"), scratch_previous=scratch_previous)
