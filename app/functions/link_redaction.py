"""Remove links, meeting IDs and passwords from event text before it is shared.

Used on everything the app serves to other people: the public viewer, the embed and the
.ics feeds. Pure functions: easy to test and no I/O.
"""
import re

PLACEHOLDER = "[link removed]"
ID_PLACEHOLDER = "[ID removed]"
SECRET_PLACEHOLDER = "[password removed]"

# Calendar apps build "Join" buttons from these properties, so removing the link from the
# description alone would still hand it out. X-ALT-DESC is the HTML version of the description.
STRIP_PROPERTIES = (
    "URL", "CONFERENCE", "ATTACH", "X-ALT-DESC", "X-GOOGLE-CONFERENCE",
    "X-MICROSOFT-SKYPETEAMSMEETINGURL", "X-MICROSOFT-ONLINEMEETINGCONFLINK",
    "X-MICROSOFT-ONLINEMEETINGEXTERNALLINK", "X-MICROSOFT-SCHEDULINGSERVICEUPDATEURL",
)

# ── links ──────────────────────────────────────────────────────────────────
_END = r"""[^\s<>"']+"""
_SCHEME = r"(?:[a-z][a-z0-9+.\-]{1,20}://|(?:mailto|tel|sip|sips|callto|skype|webcal|sms|geo):)" + _END
_WWW = r"www\." + _END
# "zoom.us/j/123" or "example.com/page": a real domain ending in a well-known TLD, not preceded by
# "@" (emails), a word character or a dot, so "e.g." and "notes.txt" are left alone.
_TLDS = (r"com|net|org|edu|gov|io|co|us|nz|uk|au|ca|de|fr|me|app|dev|ly|gl|tv|chat|link|live|meet|"
         r"ai|xyz|info|biz|eu|jp|in|sg|im|be|ch|nl|se|no|dk|it|es|ru|cn|kr|to|gg|so")
_BARE = r"(?<![@\w./\-])(?:[a-z0-9\-]+\.)+(?:" + _TLDS + r")(?![\w@\-])(?::\d+)?(?:/" + _END + r")?"

_LINK_RX = re.compile("|".join(f"(?:{p})" for p in (_SCHEME, _WWW, _BARE)), re.IGNORECASE)
_TRAILING = ".,;:!?)]}"


def _split_trailing(text):
    """("https://a.b/c).", ) -> ("https://a.b/c", ")."): keep sentence punctuation outside the redaction."""
    kept = ""
    while text and text[-1] in _TRAILING:
        kept = text[-1] + kept
        text = text[:-1]
    return text, kept


def _replace_link(match):
    core, kept = _split_trailing(match.group(0))
    return (PLACEHOLDER + kept) if core else match.group(0)


def redact_links(text):
    """Replace every link in `text` with a placeholder. Non-strings pass through unchanged."""
    if not text or not isinstance(text, str):
        return text
    return _LINK_RX.sub(_replace_link, text)


# ── meeting IDs and passwords ───────────────────────────────────────────────
# "ID", "Meeting ID", "id:" ... followed by digits, which may be grouped by single spaces or hyphens
# ("863 9599 6406"). Zoom IDs are usually 11 digits, so the run is taken in full rather than cut at 10,
# which would leave the last digit showing. Three or more digits avoids noise like "ID 2".
_ID_RX = re.compile(r"(\bID\b[ \t]*[:#]?[ \t]*)(\d(?:[ \-]?\d){2,14})(?!\d)", re.IGNORECASE)

# password / passphrase / passcode (and the abbreviations pwd, passwd) / pin, with or without a colon
# (or "is"), then the next word.
_SECRET_RX = re.compile(
    r"(\b(password|passphrase|passcode|passwd|pwd|pin)\b[ \t]*(?:([:=])|\b(is)\b)?[ \t]*(?:\r?\n[ \t]*)?)"
    r"(\[[^\]\r\n]{1,40}\][^\s]*|[^\s]+)",
    re.IGNORECASE)
# Words that follow these keywords in ordinary sentences ("password protected", "password is required").
_NOT_A_SECRET = {
    "protected", "required", "reset", "manager", "policy", "needed", "optional", "field", "will", "has",
    "can", "not", "the", "a", "an", "and", "or", "to", "for", "if", "you", "we", "your", "below", "above",
    "sent", "provided", "given", "shown", "enabled", "disabled", "on", "off", "is", "are", "was", "of", "in",
}


def _replace_secret(match, found=None):
    keyword, has_separator, value = match.group(2).lower(), bool(match.group(3) or match.group(4)), match.group(5)
    if value.startswith("[") and "]" in value:              # a placeholder inserted by the link pass: take it whole
        end = value.index("]") + 1
        core, kept = value[:end], value[end:]
    else:
        core, kept = _split_trailing(value)
    if not core or core.lower() in _NOT_A_SECRET:
        return match.group(0)
    # "pin" is also an ordinary word ("pin the poster"): only treat it as a PIN when it is clearly labelled
    if keyword == "pin" and not has_separator and not any(ch.isdigit() for ch in core):
        return match.group(0)
    if found is not None:
        found.append(core)
    return match.group(1) + SECRET_PLACEHOLDER + kept


def redact_secrets(text):
    """Remove meeting IDs and the word after password/passphrase/passcode/PIN.

    Meeting invites repeat these values elsewhere (Zoom's dial-in "+1…,,86395996406#,,,,*449238#" and
    its SIP address "86395996406@zoomcrc.com"), where no label points at them. So once a value has been
    found by its label, every other occurrence of that same value is removed too.
    """
    if not text or not isinstance(text, str):
        return text
    ids, passwords = [], []

    def take_id(m):
        ids.append(re.sub(r"\D", "", m.group(2)))
        return m.group(1) + ID_PLACEHOLDER

    text = _ID_RX.sub(take_id, text)
    text = _SECRET_RX.sub(lambda m: _replace_secret(m, passwords), text)

    for digits in set(ids):
        # the same number, however it is grouped ("863 9599 6406", "86395996406", "863-9599-6406")
        grouped = r"[ \-]?".join(re.escape(d) for d in digits)
        text = re.sub(r"(?<!\d)" + grouped + r"(?!\d)", ID_PLACEHOLDER, text)
    for value in set(passwords):
        if len(value) >= 4 and not value.startswith("["):       # too-short values would hit unrelated text
            text = re.sub(r"(?<![\w])" + re.escape(value) + r"(?![\w])", SECRET_PLACEHOLDER, text)
    return text


def redact_text(text):
    """Everything. Links go first so a URL carrying "?pwd=..." is removed whole instead of being
    cut in half by the password rule; the password rule then handles a "[link removed]" value."""
    return redact_secrets(redact_links(text))


def redact_event(event):
    """Redact an icalendar event in place: description/location text and the properties that carry join links."""
    for prop in ("DESCRIPTION", "LOCATION"):
        if prop in event:
            event[prop] = redact_text(str(event[prop]))
    for prop in STRIP_PROPERTIES:
        if prop in event:
            del event[prop]
    return event
