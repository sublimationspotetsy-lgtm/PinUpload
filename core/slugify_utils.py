"""
core/slugify_utils.py

Thin wrapper around python-slugify with fixed settings. Keeping settings
here (not inline) ensures slug generation is identical across the whole app.
"""

from slugify import slugify as _slugify


def slugify(text: str) -> str:
    """Return a lowercase, hyphen-separated slug. No unicode, no trailing
    hyphens. Consistent across all callers.

    Examples:
        slugify("Bedazzled Jean Shorts Outfit") -> "bedazzled-jean-shorts-outfit"
    """
    return _slugify(text, lowercase=True, separator="-", allow_unicode=False)
