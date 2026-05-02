"""Text normalization utilities for course name matching."""

import re


# Full-width to half-width character mappings
_FULLWIDTH_UPPER = str.maketrans(
    "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
)
_FULLWIDTH_LOWER = str.maketrans(
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "abcdefghijklmnopqrstuvwxyz",
)
_FULLWIDTH_DIGITS = str.maketrans(
    "０１２３４５６７８９",
    "0123456789",
)
# Full-width punctuation mappings
_FULLWIDTH_PUNCT = str.maketrans(
    "（）＊＋－＝",
    "()*+-=",
)


def normalize_text(text: str, *, remove_spaces: bool = False) -> str:
    """Normalize text for course name matching.

    - Strip leading/trailing whitespace
    - Convert full-width parentheses to half-width
    - Convert full-width alphanumeric characters to half-width
    - Remove full-width spaces (ideographic space U+3000)
    - Optionally remove regular spaces
    - Normalize to lowercase
    """
    if not text:
        return ""

    result = text.strip()

    # Full-width to half-width conversions
    result = result.translate(_FULLWIDTH_UPPER)
    result = result.translate(_FULLWIDTH_LOWER)
    result = result.translate(_FULLWIDTH_DIGITS)
    result = result.translate(_FULLWIDTH_PUNCT)

    # Replace full-width space (U+3000) with nothing
    result = result.replace("　", "")

    if remove_spaces:
        result = result.replace(" ", "")

    # Normalize to lowercase
    result = result.lower()

    return result