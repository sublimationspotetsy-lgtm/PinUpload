"""
core/amazon_links.py

Amazon affiliate link builder. Keeps search_term and assembled link
separate so the tag can be rotated without re-calling Gemini.
"""

from urllib.parse import quote_plus

from core.pin_schema import PinBatch


def build_amazon_link(
    search_term: str,
    tag: str,
    domain: str = "www.amazon.com",
) -> str:
    """Assemble an Amazon search URL with affiliate tag.

    Args:
        search_term: Raw search query (e.g. "bedazzled denim shorts women").
        tag: Amazon Associates tag (e.g. "yourtag-20").
        domain: Amazon domain — override for non-US stores.

    Returns:
        Full URL string, e.g.:
        "https://www.amazon.com/s?k=bedazzled+denim+shorts+women&tag=yourtag-20"
    """
    return f"https://{domain}/s?k={quote_plus(search_term)}&tag={tag}"


def assemble_pins_with_links(
    pin_batch: PinBatch,
    slug: str,
    tag: str,
    domain: str = "www.amazon.com",
) -> list[dict]:
    """Enrich each Pin in the batch with a constructed amazon_link.

    Returns a list of dicts ready for the .md and .json writers so that
    link construction happens exactly once, in one place.

    Args:
        pin_batch: Validated PinBatch from Gemini.
        slug: The keyword slug (used for index key, not the link itself).
        tag: Amazon Associates tag.
        domain: Amazon domain.

    Returns:
        List of dicts, one per pin, with all Pin fields plus:
            "index": 1-based int
            "amazon_link": assembled URL string
    """
    result = []
    for i, pin in enumerate(pin_batch.pins, start=1):
        pin_dict = pin.model_dump()
        pin_dict["index"] = i
        pin_dict["amazon_link"] = build_amazon_link(
            pin.amazon_search_term, tag, domain
        )
        result.append(pin_dict)
    return result
