"""Bible Gateway link helpers for optional reference lookup."""

from __future__ import annotations

from urllib.parse import quote_plus

import pandas as pd


def make_biblegateway_url(reference: str, version: str = "ESV") -> str:
    """Create a Bible Gateway passage URL for an OpenBible-style reference."""
    encoded_reference = quote_plus(reference)
    encoded_version = quote_plus(version)
    return (
        "https://www.biblegateway.com/passage/"
        f"?search={encoded_reference}&version={encoded_version}"
    )


def get_source_verse_references(
    df: pd.DataFrame, book: str, chapter: int, verse: int
) -> pd.DataFrame:
    """Return cross-reference rows from one source verse."""
    rows = df[
        (df["From Book"] == book)
        & (df["From Chapter"] == chapter)
        & (df["From Verse number"] == verse)
    ].copy()
    return rows.sort_values(
        ["To Verse start Book number", "To Verse start Chapter", "To Verse start number"]
    )


def add_biblegateway_links(
    df: pd.DataFrame, reference_column: str = "To Verse", version: str = "ESV"
) -> pd.DataFrame:
    """Add Bible Gateway links for references in an existing dataframe."""
    linked = df.copy()
    linked["Bible Gateway URL"] = linked[reference_column].apply(
        lambda reference: make_biblegateway_url(reference, version=version)
    )
    return linked
