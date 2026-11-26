"""Decide when the LLM pass is worth it. Skipping saves 100-700 ms per utterance.

The LLM is only needed when the transcript has something a rule can't handle: fillers,
self-corrections, numbers or spoken symbols to format, list cues, repeated words, or missing
punctuation/capitalization. Short utterances without numbers/symbols are formatted by rules.
"""

from __future__ import annotations

import re

_COMMON = {
    "fillers": r"um+|uh+|uhm+|erm+|er|hmm+|mm+|like|you know|i mean|sort of|kind of|basically|literally|you see",
    "corrections": r"no wait|wait|sorry|actually|scratch that|make that|or rather|i mean|correction|instead",
    "numbers": (
        r"zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
        r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|"
        r"thousand|million|billion|percent|dollars?|euros?|pounds?|cents?|half|quarter|point|first|second|"
        r"third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth|fourteenth|fifteenth|"
        r"twentieth|thirtieth|o'clock|a\.?m\.?|p\.?m\.?"
    ),
    "symbols": (
        r"dot|underscore|slash|backslash|dash|hyphen|colon|hashtag|plus|minus|times|equals|squared|cubed|"
        r"divided|percent|ampersand|asterisk|at sign|snake case|camel case|pascal case"
    ),
    "lists": r"firstly|secondly|thirdly|finally|lastly|number one|step one",
    # Names that are also ordinary words: only the model can tell "python" the language from the snake.
    "names": (
        r"python|claude|notion|react|slack|zoom|excel|chrome|safari|firefox|outlook|gemini|copilot|azure|siri|"
        r"alexa|discord|telegram|stripe|vue|angular|flask|pandas|ruby|perl|rails|deno|vim|emacs|kafka|airflow"
    ),
}

_LANG = {
    "es": {
        "fillers": r"o sea|este|bueno|pues|eh|vale|en plan",
        "corrections": r"no|perdón|perdon|mejor dicho|quiero decir|digo|en realidad",
        "numbers": (
            r"uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|dieci\w+|"
            r"veinte|veinti\w+|treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa|cien|ciento|\w+cientos|"
            r"mil|millón|millones|por ciento|euros?|dólares?|media|cuarto|punto|primero|segundo|tercero"
        ),
        "symbols": r"punto|arroba|guion|barra|más|menos|por|igual|al cuadrado",
        "lists": r"primero|segundo|tercero|por último|finalmente",
    },
    "fr": {
        "fillers": r"euh|ben|bah|genre|en fait|enfin|du coup|bon|voilà",
        "corrections": r"non|pardon|plutôt|je veux dire|enfin|en fait",
        "numbers": (
            r"un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|treize|quatorze|quinze|seize|"
            r"dix-\w+|vingt\w*|trente|quarante|cinquante|soixante\w*|quatre-vingt\w*|cent|cents|mille|million|"
            r"millions|pour cent|euros?|dollars?|demie?|quart|virgule|premier|première|deuxième|troisième|heures?"
        ),
        "symbols": r"point|arobase|tiret|slash|barre|plus|moins|fois|égal|égale|au carré",
        "lists": r"premièrement|deuxièmement|troisièmement|enfin|finalement",
    },
}

_CACHE: dict[str, list[tuple[str, re.Pattern[str]]]] = {}


def _patterns(language: str) -> list[tuple[str, re.Pattern[str]]]:
    if language not in _CACHE:
        extra = _LANG.get(language, {})
        _CACHE[language] = [
            (
                name,
                re.compile(
                    r"(?<![\w'])(?:" + "|".join(filter(None, [pat, extra.get(name, "")])) + r")(?![\w'])",
                    0 if name == "names" else re.IGNORECASE,
                ),
            )
            for name, pat in _COMMON.items()
        ]
    return _CACHE[language]


_GREETING = re.compile(
    r"^\W*(?:hi|hey|hello|dear|good (?:morning|afternoon|evening)|hola|querid[oa]|estimad[oa]|bonjour|salut|cher|"
    r"chère)\b",
    re.IGNORECASE,
)
_SIGN_OFF = re.compile(
    r"\b(?:thanks|thank you|cheers|best|regards|sincerely|talk soon|gracias|saludos|un abrazo|merci|cordialement|"
    r"bisous|à bientôt)\W+(?:(?-i:[A-ZÀ-Ý])[\w'’-]*\W*){0,2}$",
    re.IGNORECASE,
)
_REPEAT = re.compile(r"(?<![\w'])(\w+)[ ,]+\1(?![\w'])", re.IGNORECASE)


def llm_reason(text: str, language: str = "en", *, short_max_words: int = 3) -> str:
    """Why this text needs the LLM, or "" if rule-based formatting is enough."""
    t = text.strip()
    if not t:
        return ""
    found = {name for name, rx in _patterns(language) if rx.search(t)}
    if found & {"numbers", "symbols"}:
        return "numbers/symbols"
    n_words = len(t.split())
    if n_words <= short_max_words:
        return ""
    # a message with a greeting or sign-off, or a long dictation, may need laying out in paragraphs
    if n_words >= 12 and (_GREETING.search(t) or _SIGN_OFF.search(t)):
        return "layout"
    if n_words >= 60 and "\n" not in t:
        return "layout"
    if found:
        return sorted(found)[0]
    if _REPEAT.search(t):
        return "repeats"
    if not t[0].isupper() and t[0] not in "¿¡\"'(":
        return "lowercase start"
    if not re.search(r"[.!?…:;)\"']$", t):
        return "no final punctuation"
    if language == "en" and re.search(r"(?<![\w'])i(?![\w'])", t):
        return "lowercase i"
    return ""
