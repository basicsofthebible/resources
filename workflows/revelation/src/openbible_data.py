"""Data-loading helpers for the OpenBible Revelation notebooks."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = [
    "From Verse",
    "To Verse",
    "From Book",
    "From Chapter",
    "From Verse number",
    "To Verse start",
    "To Verse end",
    "To Verse start Book",
    "To Verse start Chapter",
    "To Verse start number",
    "To Verse end Book",
    "To Verse end Chapter",
    "To Verse end number",
    "From Book number",
    "To Verse start Book number",
    "To Verse end Book number",
    "From Book Testament",
    "To Book Testament",
]


INTEGER_COLUMNS = [
    "From Chapter",
    "From Verse number",
    "To Verse start Chapter",
    "To Verse start number",
    "From Book number",
    "To Verse start Book number",
]


def validate_cross_reference_columns(df: pd.DataFrame) -> None:
    """Raise a clear error if the public cross-reference file is incomplete."""
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(f"Missing required columns: {missing_text}")


def load_cross_references(path: str | Path) -> pd.DataFrame:
    """Load the public cross-reference CSV and normalize key numeric columns."""
    df = pd.read_csv(path)
    validate_cross_reference_columns(df)

    for column in INTEGER_COLUMNS:
        df[column] = df[column].astype(int)

    return df


def get_book_order(df: pd.DataFrame) -> list[str]:
    """Return Bible book abbreviations in canonical order from the dataset."""
    books = (
        df[["From Book", "From Book number"]]
        .drop_duplicates()
        .sort_values("From Book number")
    )
    return books["From Book"].tolist()
