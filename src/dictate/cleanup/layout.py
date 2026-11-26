"""Message layout, done by rules so it's the same every time:

    "Hey Maria, thanks for ... Let me know. Thanks, Nick."
 -> "Hey Maria,\\n\\nThanks for ... Let me know.\\n\\nThanks,\\nNick"

A greeting gets its own line when the text is a real message (it has a sign-off, or 3+ sentences);
a sign-off with a name goes on its own lines after a blank line. A short "Hi Ben, thanks!" stays
inline. Nothing is changed when the speaker dictated line breaks themselves: their layout wins.
"""

from __future__ import annotations

import re

_GREET_WORDS = (
    r"hi|hey|hello|dear|good morning|good afternoon|good evening|hola|querid[oa]s?|estimad[oa]s?|buenos días|"
    r"buenas tardes|bonjour|bonsoir|salut|cher|chère|chers|chères"
)
_GROUP = r"team|all|everyone|everybody|folks|guys|there|both|y'all|equipo|todos|todas|à tous|tout le monde"
_NAME = r"(?-i:[A-ZÀ-Ý][\w'’.-]*)"
_GREETING = re.compile(
    rf"^((?:{_GREET_WORDS})(?:\s+(?:{_GROUP}|{_NAME}(?:\s+{_NAME}){{0,2}}))?)\s*[,!:—]\s+(?=\S)",
    re.IGNORECASE,
)
_SIGN_WORDS = (
    r"thanks|thank you|many thanks|thanks so much|thanks again|cheers|best|best regards|kind regards|warm regards|"
    r"regards|sincerely|all the best|talk soon|take care|best wishes|"
    r"gracias|saludos|un saludo|un abrazo|atentamente|merci|cordialement|bien à vous|à bientôt|bisous|amitiés"
)
_SIGN_OFF = re.compile(
    rf"(?:^|(?<=[.!?]))\s*((?:{_SIGN_WORDS}))[,.!]?\s+({_NAME}(?:\s+{_NAME})?)[.!]?$",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.!?…](?:[\"'”’)\]]*)(?=\s|$)")


# "Thanks, Maria" may be thanking the reader rather than signing: only trusted with a greeting to someone else
_THANKS = re.compile(r"^(?:thanks|thank you|many thanks|thanks so much|thanks again|gracias|merci)$", re.IGNORECASE)


def _cap(s: str) -> str:
    """'thanks for it' -> 'Thanks for it', "i'm" -> "I'm"; 'iPhone' is left alone."""
    w = re.match(r"[\w'’-]+", s)
    return s[:1].upper() + s[1:] if w and w.group(0).islower() else s


def layout_message(text: str) -> str:
    """Works on the first and last lines, so paragraphs the cleanup already made are kept."""
    body = text.strip()
    if not body:
        return text
    head, _, last = body.rpartition("\n")
    sign = ""
    m = _SIGN_OFF.search(last)
    g = _GREETING.match(body)
    if m and _THANKS.match(m.group(1)) and not (g and m.group(2).split()[0] not in g.group(1)):
        m = None  # "Thanks, Maria" with no greeting to someone else: probably thanking Maria
    if m and (head or _SENTENCE_END.search(last[: m.start()])):
        word = m.group(1)
        sign = f"{word[0].upper()}{word[1:]},\n{m.group(2).rstrip('.')}"
        body = (head + "\n" + last[: m.start()]).strip()
    first, nl, rest_lines = body.partition("\n")
    g = _GREETING.match(first)
    if g and (sign or nl or len(_SENTENCE_END.findall(body)) >= 3):
        rest = first[g.end() :]
        if _SENTENCE_END.search(rest):
            greet = g.group(1)
            first = f"{greet[0].upper()}{greet[1:]},\n\n{_cap(rest)}"
            body = first + nl + rest_lines
    return body + (f"\n\n{sign}" if sign else "")
