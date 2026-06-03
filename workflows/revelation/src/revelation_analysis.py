"""Revelation-specific calculations for the public notebooks."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def get_rev_from(df: pd.DataFrame) -> pd.DataFrame:
    """Rows where Revelation is the source book."""
    return df[df["From Book"] == "Rev"].copy()


def get_rev_to(df: pd.DataFrame) -> pd.DataFrame:
    """Rows where Revelation is the target book."""
    return df[df["To Verse start Book"] == "Rev"].copy()


def summarize_revelation_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Return the core incoming/outgoing Revelation summary counts."""
    rev_from = get_rev_from(df)
    rev_to = get_rev_to(df)

    rows = [
        {
            "Metric": "Cross-references to Revelation",
            "Count": len(rev_to),
            "Definition": "Rows where To Verse start Book is Rev",
        },
        {
            "Metric": "Cross-references from Revelation",
            "Count": len(rev_from),
            "Definition": "Rows where From Book is Rev",
        },
        {
            "Metric": "Internal Revelation references",
            "Count": len(rev_to[rev_to["From Book"] == "Rev"]),
            "Definition": "Rows where both From Book and To Verse start Book are Rev",
        },
        {
            "Metric": "Cross-references to Revelation from other books",
            "Count": len(rev_to[rev_to["From Book"] != "Rev"]),
            "Definition": "Rows where To Verse start Book is Rev and From Book is not Rev",
        },
    ]
    return pd.DataFrame(rows)


def summarize_revelation_testaments(df: pd.DataFrame) -> pd.DataFrame:
    """Summarize OT/NT directions used in the Revelation explainer slides."""
    rev_from = get_rev_from(df)
    rev_to = get_rev_to(df)

    rows = [
        {
            "Direction": "To Revelation",
            "Testament field": "From Book Testament",
            "Old Testament": int((rev_to["From Book Testament"] == "Old").sum()),
            "New Testament": int((rev_to["From Book Testament"] == "New").sum()),
            "Total": len(rev_to),
        },
        {
            "Direction": "From Revelation",
            "Testament field": "To Book Testament",
            "Old Testament": int((rev_from["To Book Testament"] == "Old").sum()),
            "New Testament": int((rev_from["To Book Testament"] == "New").sum()),
            "Total": len(rev_from),
        },
    ]
    return pd.DataFrame(rows)


def count_references_to_revelation(
    df: pd.DataFrame, include_revelation: bool = True
) -> pd.DataFrame:
    """Count source books that point to Revelation."""
    rev_to = get_rev_to(df)
    if not include_revelation:
        rev_to = rev_to[rev_to["From Book"] != "Rev"]

    counts = (
        rev_to.groupby(
            ["From Book", "From Book number", "From Book Testament"], as_index=False
        )
        .size()
        .rename(
            columns={
                "From Book": "Book",
                "From Book number": "Book number",
                "From Book Testament": "Testament",
                "size": "Count",
            }
        )
        .sort_values("Book number")
    )
    return counts


def rank_nt_books_by_reference_method(df: pd.DataFrame) -> pd.DataFrame:
    """Rank NT books by outgoing, incoming, and combined cross-reference counts."""
    nt_books = (
        df[df["From Book Testament"] == "New"][["From Book", "From Book number"]]
        .drop_duplicates()
        .rename(columns={"From Book": "Book", "From Book number": "Book number"})
        .sort_values("Book number")
    )

    outgoing = df.groupby("From Book").size().rename("Outgoing")
    incoming = df.groupby("To Verse start Book").size().rename("Incoming")

    ranking = nt_books.merge(outgoing, left_on="Book", right_index=True, how="left")
    ranking = ranking.merge(incoming, left_on="Book", right_index=True, how="left")
    ranking[["Outgoing", "Incoming"]] = ranking[["Outgoing", "Incoming"]].fillna(0)
    ranking[["Outgoing", "Incoming"]] = ranking[["Outgoing", "Incoming"]].astype(int)
    ranking["Combined"] = ranking["Outgoing"] + ranking["Incoming"]

    for metric in ["Outgoing", "Incoming", "Combined"]:
        ranking[f"{metric} rank"] = (
            ranking[metric].rank(method="min", ascending=False).astype(int)
        )

    return ranking.sort_values("Incoming rank")


def load_structure_schema(path: str | Path) -> pd.DataFrame:
    """Load and lightly normalize the public Structure of Revelation schema."""
    schema = pd.read_csv(path)
    schema["Kayser"] = schema["Kayser"].str.strip()
    return schema


def _segment_mask(rev_from: pd.DataFrame, row: pd.Series) -> pd.Series:
    chapter_start = int(row["Chapter start"])
    verse_start = int(row["Verse start"])
    chapter_end = int(row["Chapter end"])
    verse_end = int(row["Verse end"])

    if chapter_start == chapter_end:
        return (rev_from["From Chapter"] == chapter_start) & (
            rev_from["From Verse number"].between(verse_start, verse_end)
        )

    starts_in_first_chapter = (rev_from["From Chapter"] == chapter_start) & (
        rev_from["From Verse number"] >= verse_start
    )
    between_chapters = (rev_from["From Chapter"] > chapter_start) & (
        rev_from["From Chapter"] < chapter_end
    )
    ends_in_last_chapter = (rev_from["From Chapter"] == chapter_end) & (
        rev_from["From Verse number"] <= verse_end
    )
    return starts_in_first_chapter | between_chapters | ends_in_last_chapter


def add_structure_segment_counts(
    schema: pd.DataFrame, rev_from: pd.DataFrame
) -> pd.DataFrame:
    """Add cross-reference counts for each row in the structure schema."""
    schema = schema.copy()
    schema["Number of cross-references"] = [
        int(_segment_mask(rev_from, row).sum()) for _, row in schema.iterrows()
    ]
    return schema


def validate_structure_counts(schema_with_counts: pd.DataFrame, rev_from: pd.DataFrame) -> None:
    """Ensure structure segment counts cover all Revelation-source rows once."""
    segment_total = int(schema_with_counts["Number of cross-references"].sum())
    rev_total = len(rev_from)
    if segment_total != rev_total:
        raise ValueError(
            "Structure segment counts do not match Revelation total: "
            f"{segment_total} != {rev_total}"
        )
