"""Stage 1: selectively retained v1 extraction, classification and QC.

Function bodies and schema/constants retain the private v1 baseline except for
the documented extraction extensions in source_provenance.json.
No providers, logging handlers, checkpoints, reconstruction, Word or SVG imports.
"""
from __future__ import annotations
import copy
import hashlib
import importlib
import posixpath
import re
from collections import Counter
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from . import bible_reference_helpers as bible_refs
PathLike = Union[str, Path]


TRANSLATION_SCHEMA_DEFAULTS = {
    "primary_translation": None,
    "primary_translation_model": None,
    "primary_error": None,
    "primary_history": [],
    "primary_attempt_count": 0,
    "evaluator_ran": None,
    "evaluator_passed": None,
    "evaluator_feedback": None,
    "evaluator_error": None,
    "evaluator_history": [],
    "evaluator_attempt_count": 0,
    "fallback_translation": None,
    "fallback_translation_model": None,
    "fallback_error": None,
    "fallback_history": [],
    "fallback_attempt_count": 0,
    "final": None,
    "final_model": None,
}


SMARTART_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "dsp": "http://schemas.microsoft.com/office/drawing/2008/diagram",
}


SMARTART_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _coerce_path(path: PathLike) -> Path:
    """Return a Path object for a string or Path input."""
    return path if isinstance(path, Path) else Path(path)


def _fresh_schema_value(value: Any) -> Any:
    """Return a copy for mutable schema defaults."""
    if isinstance(value, (list, dict)):
        return copy.deepcopy(value)
    return value


def approximate_token_count(text: str, chars_per_token: float = 4.0) -> int:
    """Return a rough token estimate for text without requiring tokenizer packages."""
    if not text:
        return 0
    return max(1, int(round(len(text) / chars_per_token)))


def check_python_pptx_available() -> Tuple[bool, Optional[str]]:
    """Return whether python-pptx can be imported and its version when available."""
    try:
        pptx_module = importlib.import_module("pptx")
    except ModuleNotFoundError:
        return False, None
    return True, str(getattr(pptx_module, "__version__", "unknown"))


def require_python_pptx() -> Any:
    """Import and return python-pptx's Presentation class, or raise a clear error."""
    try:
        pptx_module = importlib.import_module("pptx")
    except ModuleNotFoundError as exc:
        raise ImportError(
            "python-pptx is required for PPTX opening and inspection. "
            "Install it in the active notebook environment before running this section."
        ) from exc
    return getattr(pptx_module, "Presentation")


def open_presentation(pptx_path: PathLike) -> Tuple[Any, Dict[str, Any]]:
    """Open a PPTX file with python-pptx and return (Presentation, summary)."""
    Presentation = require_python_pptx()
    path = _coerce_path(pptx_path)
    if not path.exists():
        raise FileNotFoundError(f"PPTX file not found: {path}")
    if path.name.startswith("~$"):
        raise ValueError(f"Refusing to open temporary Office lock file: {path.name}")

    presentation = Presentation(str(path))
    summary = {
        "filename": path.name,
        "path": str(path),
        "file_size_bytes": path.stat().st_size,
        "file_size_mb": round(path.stat().st_size / (1024 * 1024), 2),
        "slide_count": len(presentation.slides),
    }
    print(
        f"Opened PPTX: {summary['filename']} | "
        f"{summary['file_size_mb']} MB | slides={summary['slide_count']}"
    )
    return presentation, summary


def _shape_type_name(shape: Any) -> str:
    """Return a readable shape type name for a python-pptx shape."""
    shape_type = getattr(shape, "shape_type", None)
    return str(getattr(shape_type, "name", shape_type))


def _has_attr_true(obj: Any, attr_name: str) -> bool:
    """Safely read boolean-ish python-pptx shape capability flags."""
    try:
        return bool(getattr(obj, attr_name, False))
    except Exception:
        return False


def _safe_getattr(obj: Any, attr_name: str, default: Any = None) -> Any:
    """Read an attribute/property without letting python-pptx property errors escape."""
    try:
        return getattr(obj, attr_name, default)
    except Exception:
        return default


def _shape_type_contains(shape: Any, *needles: str) -> bool:
    """Return True when a shape type name contains any provided text fragment."""
    shape_type = _shape_type_name(shape).upper()
    return any(needle.upper() in shape_type for needle in needles)


def _is_group_shape(shape: Any) -> bool:
    """Return True when a shape behaves like a group shape."""
    nested_shapes = _safe_getattr(shape, "shapes")
    return nested_shapes is not None


def _is_picture_or_media_shape(shape: Any) -> bool:
    """Return True for picture/movie/media-like shapes without touching image blobs."""
    return _shape_type_contains(shape, "PICTURE", "MEDIA", "MOVIE")


def _paragraph_text(paragraph: Any) -> str:
    """Read paragraph.text so DrawingML soft breaks remain python-pptx's \v.

    Runs alone omit a:br siblings. Paragraph boundaries are still represented
    by separate extraction records; this getter never rewrites source XML.
    """
    return str(getattr(paragraph, "text", "") or "")


def _shape_note(shape: Any) -> Optional[str]:
    """Return a note for special shape categories that need later review."""
    notes: List[str] = []
    if _has_attr_true(shape, "has_chart"):
        notes.append("chart detected")
    if _has_attr_true(shape, "has_table"):
        notes.append("table detected")
    if _is_picture_or_media_shape(shape):
        notes.append("picture/image detected")
    if _shape_type_contains(shape, "MEDIA", "MOVIE"):
        notes.append("media detected")
    if _is_group_shape(shape):
        notes.append("group shape detected")
    return "; ".join(notes) if notes else None


def iter_shapes_recursive(
    shapes: Iterable[Any],
    parent_path: str = "",
    parent_is_group: bool = False,
) -> Iterable[Tuple[Any, str, bool]]:
    """Yield shapes recursively as (shape, shape_path, is_group_child)."""
    for index, shape in enumerate(shapes, start=1):
        shape_id = getattr(shape, "shape_id", None)
        path_part = f"{index}"
        if shape_id is not None:
            path_part = f"{index}:{shape_id}"
        shape_path = f"{parent_path}/{path_part}" if parent_path else path_part
        yield shape, shape_path, parent_is_group

        nested_shapes = _safe_getattr(shape, "shapes")
        if nested_shapes is not None:
            yield from iter_shapes_recursive(
                nested_shapes,
                parent_path=shape_path,
                parent_is_group=True,
            )


def extract_text_from_text_frame(text_frame: Any) -> List[Dict[str, Any]]:
    """Extract paragraph-level text records from a python-pptx text frame."""
    records: List[Dict[str, Any]] = []
    paragraphs = list(getattr(text_frame, "paragraphs", []) or [])
    for paragraph_index, paragraph in enumerate(paragraphs, start=1):
        runs = list(getattr(paragraph, "runs", []) or [])
        text = _paragraph_text(paragraph)
        records.append(
            {
                "paragraph_index": paragraph_index,
                "run_count": len(runs),
                "text": text,
                "is_empty": not text.strip(),
            }
        )
    return records


def extract_text_from_table(table: Any) -> List[Dict[str, Any]]:
    """Extract paragraph-level text records from table cells."""
    records: List[Dict[str, Any]] = []
    for row_index, row in enumerate(getattr(table, "rows", []) or [], start=1):
        for col_index, cell in enumerate(getattr(row, "cells", []) or [], start=1):
            text_frame = getattr(cell, "text_frame", None)
            if text_frame is None:
                continue
            for paragraph_record in extract_text_from_text_frame(text_frame):
                records.append(
                    {
                        "row_index": row_index,
                        "col_index": col_index,
                        **paragraph_record,
                    }
                )
    return records


def extract_slide_text_units(slide: Any, slide_number: int) -> List[Dict[str, Any]]:
    """Extract preliminary paragraph-level text units from one slide."""
    units: List[Dict[str, Any]] = []
    unit_index = 0

    for shape, shape_path, is_group_child in iter_shapes_recursive(slide.shapes):
        shape_id = getattr(shape, "shape_id", None)
        shape_name = str(getattr(shape, "name", "") or "")

        if _has_attr_true(shape, "has_text_frame"):
            for paragraph_record in extract_text_from_text_frame(shape.text_frame):
                text = paragraph_record["text"]
                if not str(text).strip():
                    continue
                unit_index += 1
                source_type = (
                    "grouped_shape_text_frame_paragraph"
                    if is_group_child
                    else "text_frame_paragraph"
                )
                units.append(
                    {
                        "unit_key": f"s{slide_number:03d}_u{unit_index:04d}",
                        "element_id": f"s{slide_number:03d}_u{unit_index:04d}",
                        "slide_number": slide_number,
                        "shape_path": shape_path,
                        "shape_id": shape_id,
                        "shape_name": shape_name,
                        "shape_type": _shape_type_name(shape),
                        "source_type": source_type,
                        "paragraph_index": paragraph_record["paragraph_index"],
                        "run_count": paragraph_record["run_count"],
                        "row_index": None,
                        "col_index": None,
                        "text": text,
                        "tokens": approximate_token_count(str(text)),
                        "preserve_source": None,
                        "action": None,
                        "batch_number": None,
                    }
                )

        if _has_attr_true(shape, "has_table"):
            for table_record in extract_text_from_table(shape.table):
                text = table_record["text"]
                if not str(text).strip():
                    continue
                unit_index += 1
                units.append(
                    {
                        "unit_key": f"s{slide_number:03d}_u{unit_index:04d}",
                        "element_id": f"s{slide_number:03d}_u{unit_index:04d}",
                        "slide_number": slide_number,
                        "shape_path": shape_path,
                        "shape_id": shape_id,
                        "shape_name": shape_name,
                        "shape_type": _shape_type_name(shape),
                        "source_type": "table_cell_paragraph",
                        "paragraph_index": table_record["paragraph_index"],
                        "run_count": table_record["run_count"],
                        "row_index": table_record["row_index"],
                        "col_index": table_record["col_index"],
                        "text": text,
                        "tokens": approximate_token_count(str(text)),
                        "preserve_source": None,
                        "action": None,
                        "batch_number": None,
                    }
                )

    return units


def _xml_local_name(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _xml_attr_local_map(element: Optional[Any]) -> Dict[str, str]:
    if element is None:
        return {}
    return {_xml_local_name(key): value for key, value in element.attrib.items()}


def _read_zip_xml(zip_file: ZipFile, part_name: str) -> Any:
    return ET.fromstring(zip_file.read(part_name))


def _slide_number_from_part(part_name: str) -> Optional[int]:
    match = re.search(r"slide(\d+)\.xml$", part_name)
    return int(match.group(1)) if match else None


def _slide_part_from_rels_part(rels_part: str) -> str:
    name = Path(rels_part).name.replace(".rels", "")
    return f"ppt/slides/{name}"


def _resolve_relationship_target(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))


def _smartart_text_chunks(container: Any) -> List[str]:
    return [
        str(text_node.text or "").strip()
        for text_node in container.findall(".//a:t", SMARTART_NS)
        if str(text_node.text or "").strip()
    ]


def _smartart_text_for_translation(container: Any) -> str:
    chunks = _smartart_text_chunks(container)
    return "\n".join(chunks).strip()


def _parse_slide_relationships(zip_file: ZipFile, rels_part: str) -> List[Dict[str, Any]]:
    root = _read_zip_xml(zip_file, rels_part)
    source_part = _slide_part_from_rels_part(rels_part)
    rows: List[Dict[str, Any]] = []
    for rel in root:
        rel_id = rel.attrib.get("Id")
        rel_type = rel.attrib.get("Type", "")
        target = rel.attrib.get("Target", "")
        rows.append(
            {
                "rels_part": rels_part,
                "source_part": source_part,
                "slide_number": _slide_number_from_part(source_part),
                "rel_id": rel_id,
                "rel_type": rel_type,
                "target": target,
                "target_part": _resolve_relationship_target(source_part, target),
            }
        )
    return rows


def _smartart_graphic_frames(zip_file: ZipFile, slide_part: str) -> List[Dict[str, Any]]:
    root = _read_zip_xml(zip_file, slide_part)
    slide_number = _slide_number_from_part(slide_part)
    rows: List[Dict[str, Any]] = []
    for graphic_index, graphic_frame in enumerate(
        root.findall(".//p:graphicFrame", SMARTART_NS),
        start=1,
    ):
        graphic_data = graphic_frame.find(".//a:graphicData", SMARTART_NS)
        uri = graphic_data.attrib.get("uri", "") if graphic_data is not None else ""
        if "diagram" not in uri:
            continue
        c_nv_pr = graphic_frame.find(".//p:cNvPr", SMARTART_NS)
        rel_ids = graphic_frame.find(".//dgm:relIds", SMARTART_NS)
        rel_attrs = _xml_attr_local_map(rel_ids)
        rows.append(
            {
                "slide_number": slide_number,
                "slide_part": slide_part,
                "graphic_frame_index": graphic_index,
                "shape_id": c_nv_pr.attrib.get("id") if c_nv_pr is not None else None,
                "shape_name": c_nv_pr.attrib.get("name") if c_nv_pr is not None else None,
                "shape_type": "SMART_ART",
                "dm_rel_id": rel_attrs.get("dm"),
                "lo_rel_id": rel_attrs.get("lo"),
                "qs_rel_id": rel_attrs.get("qs"),
                "cs_rel_id": rel_attrs.get("cs"),
            }
        )
    return rows


def inspect_pptx_smartart_parts(pptx_path: PathLike) -> Dict[str, Any]:
    """Inspect SmartArt relationships and diagram parts directly from PPTX XML."""
    path = _coerce_path(pptx_path)
    relationship_rows: List[Dict[str, Any]] = []
    graphic_frame_rows: List[Dict[str, Any]] = []
    with ZipFile(path) as zip_file:
        package_parts = zip_file.namelist()
        slide_parts = sorted(
            part
            for part in package_parts
            if part.startswith("ppt/slides/slide") and part.endswith(".xml")
        )
        slide_rels_parts = sorted(
            part
            for part in package_parts
            if part.startswith("ppt/slides/_rels/slide") and part.endswith(".xml.rels")
        )
        diagram_parts = sorted(part for part in package_parts if part.startswith("ppt/diagrams/"))

        for rels_part in slide_rels_parts:
            relationship_rows.extend(_parse_slide_relationships(zip_file, rels_part))
        for slide_part in slide_parts:
            graphic_frame_rows.extend(_smartart_graphic_frames(zip_file, slide_part))

    diagram_relationships = [
        row
        for row in relationship_rows
        if "diagram" in str(row.get("rel_type", "")).lower()
        or "ppt/diagrams/" in str(row.get("target_part", "")).lower()
    ]
    return {
        "slide_parts": slide_parts,
        "slide_rels_parts": slide_rels_parts,
        "diagram_parts": diagram_parts,
        "diagram_data_parts": [
            part for part in diagram_parts if re.search(r"/data\d+\.xml$", part)
        ],
        "diagram_drawing_parts": [
            part for part in diagram_parts if re.search(r"/drawing\d+\.xml$", part)
        ],
        "diagram_relationships": diagram_relationships,
        "smartart_graphic_frames": graphic_frame_rows,
    }


def _resolve_smartart_instances(inspection: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rels_by_slide: Dict[int, Dict[str, Dict[str, Any]]] = {}
    for rel in inspection.get("diagram_relationships", []) or []:
        slide_number = rel.get("slide_number")
        rel_id = rel.get("rel_id")
        if slide_number is None or not rel_id:
            continue
        rels_by_slide.setdefault(int(slide_number), {})[str(rel_id)] = dict(rel)

    instances: List[Dict[str, Any]] = []
    for frame in inspection.get("smartart_graphic_frames", []) or []:
        slide_number = int(frame.get("slide_number") or 0)
        rel_lookup = rels_by_slide.get(slide_number, {})
        dm_row = rel_lookup.get(str(frame.get("dm_rel_id")))
        lo_row = rel_lookup.get(str(frame.get("lo_rel_id")))
        qs_row = rel_lookup.get(str(frame.get("qs_rel_id")))
        cs_row = rel_lookup.get(str(frame.get("cs_rel_id")))
        data_part = dm_row.get("target_part") if dm_row else None
        inferred_drawing_part = (
            re.sub(r"/data(\d+)\.xml$", r"/drawing\1.xml", str(data_part))
            if data_part
            else None
        )
        drawing_part = inferred_drawing_part
        instances.append(
            {
                **dict(frame),
                "data_part": data_part,
                "layout_part": lo_row.get("target_part") if lo_row else None,
                "quickstyle_part": qs_row.get("target_part") if qs_row else None,
                "colors_part": cs_row.get("target_part") if cs_row else None,
                "drawing_part": drawing_part,
            }
        )
    return instances


def extract_smartart_text_units(
    pptx_path: PathLike,
    start_index_by_slide: Optional[Mapping[int, int]] = None,
) -> List[Dict[str, Any]]:
    """Extract SmartArt text-bearing data points as PPTX translation units."""
    path = _coerce_path(pptx_path)
    inspection = inspect_pptx_smartart_parts(path)
    instances = _resolve_smartart_instances(inspection)
    next_index_by_slide = {int(k): int(v) for k, v in dict(start_index_by_slide or {}).items()}
    units: List[Dict[str, Any]] = []

    with ZipFile(path) as zip_file:
        for instance in instances:
            data_part = instance.get("data_part")
            if not data_part or data_part not in zip_file.namelist():
                continue
            root = _read_zip_xml(zip_file, str(data_part))
            slide_number = int(instance.get("slide_number") or 0)
            for point_index, point in enumerate(root.findall(".//dgm:pt", SMARTART_NS), start=1):
                text = _smartart_text_for_translation(point)
                if not text.strip():
                    continue
                attrs = _xml_attr_local_map(point)
                pr_set = point.find("./dgm:prSet", SMARTART_NS)
                pr_attrs = _xml_attr_local_map(pr_set)
                next_index_by_slide[slide_number] = next_index_by_slide.get(slide_number, 0) + 1
                unit_index = next_index_by_slide[slide_number]
                unit_key = f"s{slide_number:03d}_u{unit_index:04d}"
                chunks = _smartart_text_chunks(point)
                units.append(
                    {
                        "unit_key": unit_key,
                        "element_id": unit_key,
                        "slide_number": slide_number,
                        "shape_path": None,
                        "shape_id": instance.get("shape_id"),
                        "shape_name": instance.get("shape_name"),
                        "shape_type": "SMART_ART",
                        "source_type": "smartart_data_point_text",
                        "paragraph_index": None,
                        "run_count": len(chunks),
                        "row_index": None,
                        "col_index": None,
                        "text": text,
                        "tokens": approximate_token_count(text),
                        "preserve_source": None,
                        "action": None,
                        "batch_number": None,
                        "smartart_data_part": data_part,
                        "smartart_drawing_part": instance.get("drawing_part"),
                        "smartart_point_index": point_index,
                        "smartart_model_id": attrs.get("modelId"),
                        "smartart_point_type": attrs.get("type"),
                        "smartart_custom_text": pr_attrs.get("custT"),
                        "smartart_placeholder": pr_attrs.get("phldr"),
                        "smartart_text_chunks": chunks,
                        "smartart_text_chunk_count": len(chunks),
                        "smartart_graphic_frame_index": instance.get("graphic_frame_index"),
                    }
                )
    return units


def inspect_presentation_structure(presentation: Any) -> Dict[str, Any]:
    """Inspect all slides and shapes in a Presentation without modifying it."""
    shape_records: List[Dict[str, Any]] = []
    warnings: List[str] = []
    shape_type_counts: Counter[str] = Counter()

    for slide_number, slide in enumerate(presentation.slides, start=1):
        for shape_index, (shape, shape_path, is_group_child) in enumerate(
            iter_shapes_recursive(slide.shapes),
            start=1,
        ):
            shape_type = _shape_type_name(shape)
            shape_type_counts[shape_type] += 1
            has_text_frame = _has_attr_true(shape, "has_text_frame")
            has_table = _has_attr_true(shape, "has_table")
            has_chart = _has_attr_true(shape, "has_chart")
            is_group = _is_group_shape(shape)
            is_picture_or_media = _is_picture_or_media_shape(shape)
            note = _shape_note(shape)

            text_records: List[Dict[str, Any]] = []
            table_records: List[Dict[str, Any]] = []
            if has_text_frame:
                text_records = extract_text_from_text_frame(shape.text_frame)
            if has_table:
                table_records = extract_text_from_table(shape.table)

            text_parts = [record["text"] for record in text_records]
            text_parts.extend(record["text"] for record in table_records)
            plain_text = "\n".join(part for part in text_parts if str(part).strip())

            if note:
                warnings.append(f"slide {slide_number} shape {shape_path}: {note}")

            shape_records.append(
                {
                    "slide_number": slide_number,
                    "shape_index": shape_index,
                    "shape_path": shape_path,
                    "shape_id": getattr(shape, "shape_id", None),
                    "shape_name": str(getattr(shape, "name", "") or ""),
                    "shape_type": shape_type,
                    "has_text_frame": has_text_frame,
                    "text_frame_paragraph_count": len(text_records),
                    "paragraph_count": len(text_records) + len(table_records),
                    "run_count": sum(int(record.get("run_count", 0)) for record in text_records + table_records),
                    "plain_text": plain_text,
                    "is_text_empty": not plain_text.strip(),
                    "has_table": has_table,
                    "table_text_paragraph_count": len(table_records),
                    "is_group_shape": is_group,
                    "is_group_child": is_group_child,
                    "has_chart": has_chart,
                    "is_picture_or_media": is_picture_or_media,
                    "notes": note,
                }
            )

    return {
        "slide_count": len(presentation.slides),
        "shape_records": shape_records,
        "shape_type_counts": dict(sorted(shape_type_counts.items())),
        "warnings": warnings,
    }


def summarize_presentation_inspection(inspection: Mapping[str, Any]) -> Dict[str, Any]:
    """Build high-level counts from an inspection result."""
    records = list(inspection.get("shape_records", []) or [])
    non_empty_text_shapes = [
        record for record in records if not record.get("is_text_empty")
    ]
    text_unit_count = sum(
        int(record.get("paragraph_count", 0) or 0)
        for record in records
        if not record.get("is_text_empty")
    )
    summary = {
        "slide_count": int(inspection.get("slide_count", 0) or 0),
        "shape_count": len(records),
        "shapes_with_text": len(non_empty_text_shapes),
        "text_frame_count": sum(1 for record in records if record.get("has_text_frame")),
        "table_count": sum(1 for record in records if record.get("has_table")),
        "group_shape_count": sum(1 for record in records if record.get("is_group_shape")),
        "group_child_shape_count": sum(1 for record in records if record.get("is_group_child")),
        "chart_count": sum(1 for record in records if record.get("has_chart")),
        "picture_or_media_count": sum(1 for record in records if record.get("is_picture_or_media")),
        "other_shape_count": sum(
            1
            for record in records
            if not (
                record.get("has_text_frame")
                or record.get("has_table")
                or record.get("is_group_shape")
                or record.get("has_chart")
                or record.get("is_picture_or_media")
            )
        ),
        "non_empty_text_unit_count": text_unit_count,
        "shape_type_counts": dict(inspection.get("shape_type_counts", {}) or {}),
        "warning_count": len(inspection.get("warnings", []) or []),
    }
    return summary


def _max_unit_index_by_slide(units: Iterable[Mapping[str, Any]]) -> Dict[int, int]:
    max_by_slide: Dict[int, int] = {}
    for unit in units:
        slide_number = int(unit.get("slide_number", 0) or 0)
        unit_key = str(unit.get("unit_key") or "")
        match = re.search(r"_u(\d+)$", unit_key)
        index = int(match.group(1)) if match else 0
        max_by_slide[slide_number] = max(max_by_slide.get(slide_number, 0), index)
    return max_by_slide


def _span_xml_signature(element, style=False):
    if element is None:
        return None
    ignored = {'dirty', 'err', 'smtClean'} if style else set()
    text = element.text or ''
    if element.tag != '{%s}t' % SMARTART_NS['a'] and not text.strip():
        text = ''  # Ignore XML indentation, never text-node whitespace.
    return (element.tag, tuple(sorted((k, v) for k, v in element.attrib.items() if k not in ignored)),
            text, tuple(_span_xml_signature(c, style) for c in element))


def reconcile_span_formats(current_unit, saved_unit, *, coverage_error, formatting_error):
    """Restore saved format serialization after strict XML-equivalence checks.

    Only a differing span_formats map is inspected or replaced. Equal maps keep
    the existing no-parse path; differing maps must have identical keys and XML
    signatures (default mode, not the looser style-grouping mode). Replacement
    happens after all entries match and is isolated from the saved map.
    Callers retain unit pairing, coverage, ordering and complete identity checks.
    """
    current_formats, saved_formats = current_unit.get('span_formats'), saved_unit.get('span_formats')
    if current_formats != saved_formats:
        if not isinstance(current_formats, dict) or not isinstance(saved_formats, dict) or current_formats.keys() != saved_formats.keys():
            raise ValueError(coverage_error)
        for ref, xml in current_formats.items():
            current_signature = _span_xml_signature(ET.fromstring(xml) if xml else None)
            saved_xml = saved_formats[ref]
            if current_signature != _span_xml_signature(ET.fromstring(saved_xml) if saved_xml else None):
                raise ValueError(formatting_error)
        current_unit['span_formats'] = copy.deepcopy(saved_formats)


def describe_styled_spans(paragraph, unit_key):
    """Merge only adjacent equal-style text; retain explicit structural tokens."""
    # Standard-library XML avoids lxml's custom text/itertext behavior.
    if hasattr(paragraph, '_p'):
        paragraph = ET.fromstring(paragraph._p.xml)
    NS = SMARTART_NS
    spans, formats, signatures = [], {}, {}

    def format_ref(properties):
        signature = _span_xml_signature(properties, style=True)
        if signature not in signatures:
            ref = f'{unit_key}.f{len(signatures)+1}'
            signatures[signature] = ref
            formats[ref] = ET.tostring(properties, encoding='unicode') if properties is not None else None
        return signatures[signature]

    run_index = 0
    for child_index, child in enumerate(paragraph, 1):
        kind = child.tag.rsplit('}', 1)[-1]
        if kind in ('pPr', 'endParaRPr'):
            continue
        if kind == 'br':
            spans.append(dict(kind='soft_break', text='\v', format_ref=format_ref(child.find('a:rPr', NS)),
                              source_xml_child_index=child_index))
            continue
        if kind != 'r' or len(child.findall('a:t', NS)) > 1 or any(c.tag.rsplit('}', 1)[-1] not in ('rPr', 't') for c in child):
            raise ValueError('Unsupported paragraph content: fields or nonstandard runs require manual review.')
        run_index += 1
        node = child.find('a:t', NS)
        text = (node.text or '') if node is not None else ''
        if not text:
            continue  # Keep empty source runs and their XML; they are not targets.
        ref = format_ref(child.find('a:rPr', NS))
        for match in re.finditer(r'\t| {2,}|[^\t]+?(?=\t| {2,}|$)', text):
            value = match.group()
            kind = 'tab' if value == '\t' else 'fixed_whitespace' if value.isspace() else 'text'
            fragment = dict(run_index=run_index, start=match.start(), end=match.end())
            if kind == 'text' and spans and spans[-1]['kind'] == 'text' and spans[-1]['format_ref'] == ref:
                spans[-1]['text'] += value
                spans[-1]['source_fragments'].append(fragment)
            else:
                spans.append(dict(kind=kind, text=value, format_ref=ref, source_fragments=[fragment]))
    for index, span in enumerate(spans, 1):
        span['span_id'] = f'p{index:02d}'
    return spans, formats


def _span_source_hash(paragraph):
    if hasattr(paragraph, '_p'):
        paragraph = ET.fromstring(paragraph._p.xml)
    return hashlib.sha256(repr(_span_xml_signature(paragraph)).encode('utf-8')).hexdigest()


def build_pptx_text_units(
    presentation: Any,
    pptx_path: Optional[PathLike] = None,
    include_smartart: bool = True,
    include_styled_spans: bool = False,
    smartart_styled_spans: bool = True,
) -> List[Dict[str, Any]]:
    """Build preliminary paragraph-level PPTX text units from slides and SmartArt XML."""
    units: List[Dict[str, Any]] = []
    for slide_number, slide in enumerate(presentation.slides, start=1):
        units.extend(extract_slide_text_units(slide, slide_number))
    if include_smartart and pptx_path is not None:
        units.extend(
            extract_smartart_text_units(
                pptx_path,
                start_index_by_slide=_max_unit_index_by_slide(units),
            )
        )
    if include_styled_spans:
        for unit in units:
            if unit['source_type'] == 'smartart_data_point_text':
                if not smartart_styled_spans:
                    unit['styled_span_review'] = 'SmartArt styled spans are unsupported.'
                    continue
                from .smartart_xml import paragraphs
                try:
                    with ZipFile(pptx_path) as archive:
                        targets, label = paragraphs(archive, unit)
                    paragraph = targets[0][0]
                    spans, formats = describe_styled_spans(paragraph, unit['unit_key'])
                    unit.update(text=''.join(s['text'] for s in spans),
                                smartart_text_version=1,
                                smartart_cached_shape_id=label['cached_shape_id'],
                                smartart_drawing_part=targets[1][1],
                                styled_spans=spans, span_formats=formats,
                                span_source_hash=_span_source_hash(paragraph))
                    unit['tokens'] = approximate_token_count(unit['text'])
                    unit['run_count'] = len(paragraph.findall('a:r', SMARTART_NS))
                except ValueError as exc:
                    unit['styled_span_review'] = 'Unsupported SmartArt: ' + str(exc)
                continue
            shape = _shape_from_path(presentation.slides[unit['slide_number'] - 1], unit['shape_path'])
            frame = (shape.table.cell(unit['row_index'] - 1, unit['col_index'] - 1).text_frame
                     if unit['source_type'] == 'table_cell_paragraph' else shape.text_frame)
            paragraph = frame.paragraphs[unit['paragraph_index'] - 1]
            try:
                spans, formats = describe_styled_spans(paragraph, unit['unit_key'])
                if ''.join(span['text'] for span in spans) != unit['text']:
                    raise ValueError('Span text does not reproduce paragraph text.')
            except ValueError as exc:
                unit['styled_span_review'] = str(exc)
                continue
            unit.update(styled_spans=spans, span_formats=formats,
                        span_source_hash=_span_source_hash(paragraph))
    return units


def summarize_pptx_text_units(units: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return summary statistics for extracted PPTX text units."""
    unit_list = list(units)
    by_slide = Counter(str(unit.get("slide_number") or "") for unit in unit_list)
    by_source_type = Counter(str(unit.get("source_type") or "") for unit in unit_list)
    token_values = [int(unit.get("tokens", 0) or 0) for unit in unit_list]
    return {
        "total_text_units": len(unit_list),
        "total_token_estimate": sum(token_values),
        "min_tokens": min(token_values) if token_values else 0,
        "median_tokens": median(token_values) if token_values else 0,
        "max_tokens": max(token_values) if token_values else 0,
        "mean_tokens": mean(token_values) if token_values else 0,
        "text_units_by_slide": dict(sorted(by_slide.items(), key=lambda item: int(item[0]) if item[0].isdigit() else 0)),
        "text_units_by_source_type": dict(sorted(by_source_type.items())),
    }


def build_table_audit_rows(units: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Return table-cell text units with reconstruction-relevant coordinates."""
    rows: List[Dict[str, Any]] = []
    for unit in units:
        if unit.get("source_type") != "table_cell_paragraph":
            continue
        rows.append(
            {
                "unit_key": unit.get("unit_key"),
                "slide_number": unit.get("slide_number"),
                "shape_path": unit.get("shape_path"),
                "shape_id": unit.get("shape_id"),
                "shape_name": unit.get("shape_name"),
                "row_index": unit.get("row_index"),
                "col_index": unit.get("col_index"),
                "paragraph_index": unit.get("paragraph_index"),
                "run_count": unit.get("run_count"),
                "tokens": unit.get("tokens"),
                "text": unit.get("text"),
            }
        )
    return rows


def build_source_type_samples(
    units: Iterable[Mapping[str, Any]],
    sample_size: int = 5,
) -> List[Dict[str, Any]]:
    """Return sample text units for each source_type."""
    samples: List[Dict[str, Any]] = []
    seen_counts: Counter[str] = Counter()
    for unit in units:
        source_type = str(unit.get("source_type") or "")
        if seen_counts[source_type] >= sample_size:
            continue
        seen_counts[source_type] += 1
        samples.append(
            {
                "source_type": source_type,
                "unit_key": unit.get("unit_key"),
                "slide_number": unit.get("slide_number"),
                "shape_name": unit.get("shape_name"),
                "shape_path": unit.get("shape_path"),
                "paragraph_index": unit.get("paragraph_index"),
                "row_index": unit.get("row_index"),
                "col_index": unit.get("col_index"),
                "run_count": unit.get("run_count"),
                "tokens": unit.get("tokens"),
                "text": unit.get("text"),
            }
        )
    return samples


def reconcile_extraction_counts(
    inspection: Mapping[str, Any],
    units: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Compare inspection paragraph counts with extracted non-empty text units."""
    shape_records = list(inspection.get("shape_records", []) or [])
    unit_list = list(units)
    smartart_units = [
        unit for unit in unit_list if unit.get("source_type") == "smartart_data_point_text"
    ]
    non_smartart_units = [
        unit for unit in unit_list if unit.get("source_type") != "smartart_data_point_text"
    ]
    inspected_non_empty_paragraphs = sum(
        int(record.get("paragraph_count", 0) or 0)
        for record in shape_records
        if not record.get("is_text_empty")
    )
    extracted_units = len(non_smartart_units)
    slides_with_units = {int(unit["slide_number"]) for unit in unit_list if unit.get("slide_number")}
    slide_count = int(inspection.get("slide_count", 0) or 0)
    slides_without_units = [
        slide_number
        for slide_number in range(1, slide_count + 1)
        if slide_number not in slides_with_units
    ]
    empty_text_frame_count = sum(
        1
        for record in shape_records
        if record.get("has_text_frame") and record.get("is_text_empty")
    )
    return {
        "inspected_non_empty_paragraphs": inspected_non_empty_paragraphs,
        "extracted_text_units": len(unit_list),
        "extracted_non_smartart_text_units": extracted_units,
        "smartart_text_units": len(smartart_units),
        "difference": inspected_non_empty_paragraphs - extracted_units,
        "empty_text_frame_count": empty_text_frame_count,
        "slides_without_text_units": slides_without_units,
    }


def build_empty_text_frame_report(
    inspection: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Return text-capable shapes whose extracted text is empty or whitespace."""
    rows: List[Dict[str, Any]] = []
    for record in inspection.get("shape_records", []) or []:
        if not record.get("has_text_frame") or not record.get("is_text_empty"):
            continue
        rows.append(
            {
                "slide_number": record.get("slide_number"),
                "shape_index": record.get("shape_index"),
                "shape_path": record.get("shape_path"),
                "shape_id": record.get("shape_id"),
                "shape_name": record.get("shape_name"),
                "shape_type": record.get("shape_type"),
                "paragraph_count": record.get("paragraph_count"),
                "run_count": record.get("run_count"),
            }
        )
    return rows


def build_long_text_unit_report(
    units: Iterable[Mapping[str, Any]],
    min_tokens: int = 100,
) -> List[Dict[str, Any]]:
    """Return text units at or above a token threshold."""
    rows = [
        {
            "unit_key": unit.get("unit_key"),
            "slide_number": unit.get("slide_number"),
            "source_type": unit.get("source_type"),
            "shape_name": unit.get("shape_name"),
            "shape_path": unit.get("shape_path"),
            "run_count": unit.get("run_count"),
            "tokens": unit.get("tokens"),
            "text": unit.get("text"),
        }
        for unit in units
        if int(unit.get("tokens", 0) or 0) >= min_tokens
    ]
    return sorted(rows, key=lambda row: int(row.get("tokens", 0) or 0), reverse=True)


def build_run_fragmentation_report(
    units: Iterable[Mapping[str, Any]],
    min_run_count: int = 2,
) -> List[Dict[str, Any]]:
    """Return units whose paragraph text is split across multiple runs."""
    rows = [
        {
            "unit_key": unit.get("unit_key"),
            "slide_number": unit.get("slide_number"),
            "source_type": unit.get("source_type"),
            "shape_name": unit.get("shape_name"),
            "shape_path": unit.get("shape_path"),
            "paragraph_index": unit.get("paragraph_index"),
            "row_index": unit.get("row_index"),
            "col_index": unit.get("col_index"),
            "run_count": unit.get("run_count"),
            "tokens": unit.get("tokens"),
            "text": unit.get("text"),
        }
        for unit in units
        if int(unit.get("run_count", 0) or 0) >= min_run_count
    ]
    return sorted(rows, key=lambda row: int(row.get("run_count", 0) or 0), reverse=True)


def build_duplicate_text_report(
    units: Iterable[Mapping[str, Any]],
    min_count: int = 2,
) -> List[Dict[str, Any]]:
    """Return exact duplicate text values and where they occur."""
    by_text: Dict[str, List[Mapping[str, Any]]] = {}
    for unit in units:
        text = str(unit.get("text") or "").strip()
        if not text:
            continue
        by_text.setdefault(text, []).append(unit)

    rows: List[Dict[str, Any]] = []
    for text, grouped_units in by_text.items():
        if len(grouped_units) < min_count:
            continue
        rows.append(
            {
                "count": len(grouped_units),
                "slides": sorted({unit.get("slide_number") for unit in grouped_units}),
                "unit_keys": [unit.get("unit_key") for unit in grouped_units],
                "tokens": grouped_units[0].get("tokens"),
                "text": text,
            }
        )
    return sorted(rows, key=lambda row: (-int(row["count"]), str(row["text"])))


def classify_preserve_candidate(text: str) -> List[str]:
    """Return lightweight reasons a text unit may need preserve/special handling."""
    stripped = str(text or "").strip()
    reasons: List[str] = []
    if not stripped:
        return reasons
    if re.search(r"https?://|www\.|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", stripped):
        reasons.append("url_or_email")
    if re.fullmatch(r"[\d\s.,:;/%()+\-–—_^#]+", stripped):
        reasons.append("numeric_or_symbolic")
    if re.search(r"\b(?:Genesis|Exodus|Leviticus|Numbers|Deuteronomy|Joshua|Judges|Ruth|Samuel|Kings|Chronicles|Ezra|Nehemiah|Esther|Job|Psalm|Proverbs|Ecclesiastes|Song|Isaiah|Jeremiah|Lamentations|Ezekiel|Daniel|Hosea|Joel|Amos|Obadiah|Jonah|Micah|Nahum|Habakkuk|Zephaniah|Haggai|Zechariah|Malachi|Matthew|Mark|Luke|John|Acts|Romans|Corinthians|Galatians|Ephesians|Philippians|Colossians|Thessalonians|Timothy|Titus|Philemon|Hebrews|James|Peter|Jude|Revelation|Rev|Luk|Acts)\b\.?\s*\d", stripped, flags=re.IGNORECASE):
        reasons.append("scripture_reference")
    if len(stripped) <= 3:
        reasons.append("very_short")
    if re.search(r"\d", stripped) and len(stripped) <= 40:
        reasons.append("short_contains_digit")
    return reasons


def build_preserve_candidate_report(
    units: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Return text units that may need preserve or special translation rules."""
    rows: List[Dict[str, Any]] = []
    for unit in units:
        text = str(unit.get("text") or "")
        reasons = classify_preserve_candidate(text)
        if not reasons:
            continue
        rows.append(
            {
                "unit_key": unit.get("unit_key"),
                "slide_number": unit.get("slide_number"),
                "source_type": unit.get("source_type"),
                "shape_name": unit.get("shape_name"),
                "tokens": unit.get("tokens"),
                "reasons": ", ".join(reasons),
                "text": text,
            }
        )
    return rows


def build_manual_shape_review_rows(
    inspection: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Return locator rows for picture, unknown, grouped, chart, and other shapes."""
    rows: List[Dict[str, Any]] = []
    for record in inspection.get("shape_records", []) or []:
        shape_type = record.get("shape_type")
        needs_review = (
            record.get("is_picture_or_media")
            or record.get("is_group_shape")
            or record.get("is_group_child")
            or record.get("has_chart")
            or shape_type in (None, "None")
        )
        if not needs_review:
            continue
        rows.append(
            {
                "slide_number": record.get("slide_number"),
                "shape_index": record.get("shape_index"),
                "shape_path": record.get("shape_path"),
                "shape_id": record.get("shape_id"),
                "shape_name": record.get("shape_name"),
                "shape_type": shape_type,
                "is_picture_or_media": record.get("is_picture_or_media"),
                "is_group_shape": record.get("is_group_shape"),
                "is_group_child": record.get("is_group_child"),
                "has_chart": record.get("has_chart"),
                "notes": record.get("notes"),
            }
        )
    return rows


def build_per_slide_extraction_report(
    inspection: Mapping[str, Any],
    units: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Return slide-level shape and extracted text-unit counts."""
    shape_records = list(inspection.get("shape_records", []) or [])
    unit_list = list(units)
    slide_count = int(inspection.get("slide_count", 0) or 0)
    rows: List[Dict[str, Any]] = []
    for slide_number in range(1, slide_count + 1):
        slide_shapes = [
            record for record in shape_records if record.get("slide_number") == slide_number
        ]
        slide_units = [
            unit for unit in unit_list if unit.get("slide_number") == slide_number
        ]
        rows.append(
            {
                "slide_number": slide_number,
                "shape_count": len(slide_shapes),
                "text_frame_count": sum(1 for record in slide_shapes if record.get("has_text_frame")),
                "table_count": sum(1 for record in slide_shapes if record.get("has_table")),
                "picture_or_media_count": sum(1 for record in slide_shapes if record.get("is_picture_or_media")),
                "group_shape_count": sum(1 for record in slide_shapes if record.get("is_group_shape")),
                "non_empty_shape_count": sum(1 for record in slide_shapes if not record.get("is_text_empty")),
                "text_unit_count": len(slide_units),
                "token_estimate": sum(int(unit.get("tokens", 0) or 0) for unit in slide_units),
            }
        )
    return rows


def build_shape_path_stability_report(
    inspection: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return lightweight checks for duplicate shape paths and ids per slide."""
    shape_records = list(inspection.get("shape_records", []) or [])
    path_counts: Counter[Tuple[Any, Any]] = Counter(
        (record.get("slide_number"), record.get("shape_path")) for record in shape_records
    )
    id_counts: Counter[Tuple[Any, Any]] = Counter(
        (record.get("slide_number"), record.get("shape_id")) for record in shape_records
        if record.get("shape_id") is not None
    )
    duplicate_paths = [
        {"slide_number": slide, "shape_path": shape_path, "count": count}
        for (slide, shape_path), count in path_counts.items()
        if count > 1
    ]
    duplicate_ids = [
        {"slide_number": slide, "shape_id": shape_id, "count": count}
        for (slide, shape_id), count in id_counts.items()
        if count > 1
    ]
    return {
        "duplicate_shape_paths": duplicate_paths,
        "duplicate_shape_ids": duplicate_ids,
        "shape_path_count": len(path_counts),
        "shape_record_count": len(shape_records),
    }


def _shape_from_path(slide: Any, shape_path: str) -> Any:
    """Locate a shape from a recursive one-based shape path."""
    current_shapes = slide.shapes
    shape = None
    path_segments = str(shape_path).split("/")
    for segment_index, segment in enumerate(path_segments):
        index_text = segment.split(":", 1)[0]
        index = int(index_text) - 1
        shape = current_shapes[index]
        current_shapes = _safe_getattr(shape, "shapes")
        if current_shapes is None and segment_index < len(path_segments) - 1:
            raise ValueError(f"Shape path descends into a non-group shape: {shape_path}")
    return shape


def _run_style_flags(run: Any) -> Dict[str, Any]:
    """Return a compact set of style attributes for a python-pptx run."""
    font = _safe_getattr(run, "font")
    size = _safe_getattr(font, "size") if font is not None else None
    return {
        "bold": _safe_getattr(font, "bold") if font is not None else None,
        "italic": _safe_getattr(font, "italic") if font is not None else None,
        "underline": _safe_getattr(font, "underline") if font is not None else None,
        "font_name": _safe_getattr(font, "name") if font is not None else None,
        "font_size_pt": round(size.pt, 2) if size is not None else None,
    }


def inspect_text_unit_runs(
    presentation: Any,
    units: Iterable[Mapping[str, Any]],
    min_run_count: int = 2,
) -> List[Dict[str, Any]]:
    """Return run-level read-only details for extracted units with many runs."""
    rows: List[Dict[str, Any]] = []
    slides = list(presentation.slides)
    for unit in units:
        if unit.get("source_type") == "smartart_data_point_text" or not unit.get("shape_path"):
            continue
        if int(unit.get("run_count", 0) or 0) < min_run_count:
            continue
        slide_number = int(unit.get("slide_number", 0) or 0)
        if slide_number < 1 or slide_number > len(slides):
            continue
        shape = _shape_from_path(slides[slide_number - 1], str(unit.get("shape_path")))
        paragraph_index = int(unit.get("paragraph_index", 0) or 0) - 1

        if unit.get("source_type") == "table_cell_paragraph":
            table = _safe_getattr(shape, "table")
            row_index = int(unit.get("row_index", 0) or 0) - 1
            col_index = int(unit.get("col_index", 0) or 0) - 1
            paragraph = table.rows[row_index].cells[col_index].text_frame.paragraphs[paragraph_index]
        else:
            paragraph = shape.text_frame.paragraphs[paragraph_index]

        for run_index, run in enumerate(getattr(paragraph, "runs", []) or [], start=1):
            rows.append(
                {
                    "unit_key": unit.get("unit_key"),
                    "slide_number": slide_number,
                    "source_type": unit.get("source_type"),
                    "shape_name": unit.get("shape_name"),
                    "shape_path": unit.get("shape_path"),
                    "paragraph_index": unit.get("paragraph_index"),
                    "row_index": unit.get("row_index"),
                    "col_index": unit.get("col_index"),
                    "run_index": run_index,
                    "run_text": _safe_getattr(run, "text", ""),
                    **_run_style_flags(run),
                }
            )
    return rows


def inspect_text_unit_paragraph_structure(
    presentation: Any,
    units: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Return paragraph-level structure details for extracted text units."""
    rows: List[Dict[str, Any]] = []
    slides = list(presentation.slides)
    for unit in units:
        if unit.get("source_type") == "smartart_data_point_text" or not unit.get("shape_path"):
            continue
        slide_number = int(unit.get("slide_number", 0) or 0)
        if slide_number < 1 or slide_number > len(slides):
            continue
        shape = _shape_from_path(slides[slide_number - 1], str(unit.get("shape_path")))
        paragraph_index = int(unit.get("paragraph_index", 0) or 0) - 1

        if unit.get("source_type") == "table_cell_paragraph":
            table = _safe_getattr(shape, "table")
            row_index = int(unit.get("row_index", 0) or 0) - 1
            col_index = int(unit.get("col_index", 0) or 0) - 1
            paragraph = table.rows[row_index].cells[col_index].text_frame.paragraphs[paragraph_index]
        else:
            paragraph = shape.text_frame.paragraphs[paragraph_index]

        text = str(unit.get("text") or "")
        rows.append(
            {
                "unit_key": unit.get("unit_key"),
                "slide_number": slide_number,
                "source_type": unit.get("source_type"),
                "shape_name": unit.get("shape_name"),
                "shape_path": unit.get("shape_path"),
                "paragraph_index": unit.get("paragraph_index"),
                "row_index": unit.get("row_index"),
                "col_index": unit.get("col_index"),
                "level": _safe_getattr(paragraph, "level"),
                "alignment": str(_safe_getattr(paragraph, "alignment")),
                "run_count": unit.get("run_count"),
                "tokens": unit.get("tokens"),
                "contains_line_break": "\n" in text or "\v" in text,
                "starts_with_bullet_like_text": bool(re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", text)),
                "text": text,
            }
        )
    return rows


def build_pretranslation_qc_summary(
    inspection: Mapping[str, Any],
    units: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Return compact counts for pre-translation QC reports."""
    unit_list = list(units)
    return {
        "table_unit_count": len(build_table_audit_rows(unit_list)),
        "run_fragmented_unit_count": len(build_run_fragmentation_report(unit_list)),
        "long_text_unit_count": len(build_long_text_unit_report(unit_list)),
        "duplicate_text_count": len(build_duplicate_text_report(unit_list)),
        "preserve_candidate_count": len(build_preserve_candidate_report(unit_list)),
        "manual_shape_review_count": len(build_manual_shape_review_rows(inspection)),
        "empty_text_frame_count": len(build_empty_text_frame_report(inspection)),
        "count_reconciliation": reconcile_extraction_counts(inspection, unit_list),
        "shape_path_stability": build_shape_path_stability_report(inspection),
        "masters_and_notes_scope": "slide masters, layouts, and speaker notes intentionally skipped",
    }


def split_number_markers(text: str) -> Dict[str, str]:
    """Split leading/trailing numeric markers from a text unit when present."""
    original = str(text or "")
    leading = ""
    trailing = ""
    core = original

    leading_match = re.match(r"^(\s*(?:\(\d+[A-Za-z]?\)|\d+[A-Za-z]?[.)]|[IVXLCDM]+[.)])(?:\s|\u00a0)+)", core)
    if leading_match:
        leading = leading_match.group(1)
        core = core[len(leading):]

    trailing_match = re.search(r"((?:\s|\u00a0)+(?:\(\d+[A-Za-z]?\)|\d+[A-Za-z]?[.)]|[IVXLCDM]+[.)]))\s*$", core)
    if trailing_match:
        trailing = trailing_match.group(1)
        core = core[: trailing_match.start()]

    return {
        "number_prefix": leading,
        "number_suffix": trailing,
        "translatable_text": core,
    }


def is_bible_reference_like(text: str) -> bool:
    """Return True when text appears to be only a Bible/scripture reference."""
    return bool(bible_refs.is_bible_reference_like(str(text or "")))


def contains_bible_reference(text: str) -> bool:
    """Return True when text contains a Bible/scripture reference."""
    return bool(bible_refs.contains_bible_reference(str(text or "")))


def classify_pptx_text_unit(text: Optional[str]) -> Dict[str, Any]:
    """Classify a PPTX text unit for preserve/translate payload construction."""
    source_text = str(text or "")
    norm = re.sub(r"\s+", " ", source_text.replace("\xa0", " ")).strip()
    split = split_number_markers(source_text)
    translatable_text = split["translatable_text"].strip()

    result: Dict[str, Any] = {
        "action": "translate",
        "preserve_source": False,
        "preserve_reason": "",
        "text_kind": "text",
        "number_prefix": split["number_prefix"],
        "number_suffix": split["number_suffix"],
        "translatable_text": split["translatable_text"],
        "translation_note": "",
    }

    if not norm:
        result.update(
            {
                "action": "preserve",
                "preserve_source": True,
                "preserve_reason": "blank_or_empty",
                "text_kind": "blank",
                "translation_note": "Preserve blank/empty text.",
            }
        )
        return result

    if re.search(r"https?://|www\.|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", norm):
        result.update(
            {
                "action": "preserve",
                "preserve_source": True,
                "preserve_reason": "url_or_email",
                "text_kind": "url_or_email",
                "translation_note": "Preserve URL/email exactly.",
            }
        )
        return result

    if re.fullmatch(r"[\W_]+", norm, flags=re.UNICODE):
        result.update(
            {
                "action": "preserve",
                "preserve_source": True,
                "preserve_reason": "punctuation_or_symbol_only",
                "text_kind": "punctuation_symbol",
                "translation_note": "Preserve punctuation/symbol-only text exactly.",
            }
        )
        return result

    if re.fullmatch(r"[\d\s.,:;/%()+\-–—_^#]+", norm):
        result.update(
            {
                "action": "preserve",
                "preserve_source": True,
                "preserve_reason": "numeric_or_symbolic",
                "text_kind": "number",
                "translation_note": "Preserve numeric/symbolic text exactly.",
            }
        )
        return result

    if is_bible_reference_like(norm):
        result.update(
            {
                "text_kind": "bible_reference",
                "number_prefix": "",
                "number_suffix": "",
                "translatable_text": source_text,
                "translation_note": "Translate Bible book names when appropriate; preserve chapter, verse, range, comma, and version numbers.",
            }
        )
        return result

    if contains_bible_reference(norm):
        result.update(
            {
                "text_kind": "text_with_bible_reference",
                "translation_note": "Translate prose and Bible book names when appropriate; preserve chapter, verse, range, comma, and version numbers.",
            }
        )
        return result

    if split["number_prefix"]:
        result.update(
            {
                "text_kind": "numbered_label_or_verse",
                "translation_note": "Preserve the leading number marker exactly and translate the remaining text.",
            }
        )
        return result

    if split["number_suffix"]:
        result.update(
            {
                "text_kind": "text_with_trailing_number",
                "translation_note": "Translate the text and preserve the trailing number marker exactly.",
            }
        )
        return result

    if len(norm) <= 3 and not re.search(r"[A-Za-z]", norm):
        result.update(
            {
                "action": "preserve",
                "preserve_source": True,
                "preserve_reason": "very_short_nonword",
                "text_kind": "very_short_nonword",
                "translation_note": "Preserve very short non-word text exactly.",
            }
        )
        return result

    if re.search(r"\d", norm) and re.search(r"[A-Za-z]", norm):
        result["text_kind"] = "mixed_number_text"
        result["translation_note"] = "Translate text while preserving numeric values and sequence markers."
        return result

    if len(norm.split()) <= 5:
        result["text_kind"] = "short_phrase"
        return result

    result["text_kind"] = "prose"
    return result


def classify_pptx_text_units(
    units: Iterable[Mapping[str, Any]],
    apply_schema: bool = True,
) -> List[Dict[str, Any]]:
    """Return text units enriched with preserve/translate classification fields."""
    classified_units: List[Dict[str, Any]] = []
    for unit in units:
        classified = dict(unit)
        classification = classify_pptx_text_unit(str(unit.get("text") or ""))
        classified.update(classification)
        if classification["preserve_source"]:
            classified["final"] = classified.get("text")
            classified["final_model"] = "preserve_source"
            classified["final_source"] = "preserve_source"
        classified_units.append(classified)

    if apply_schema:
        for classified in classified_units:
            for key, value in TRANSLATION_SCHEMA_DEFAULTS.items():
                classified.setdefault(key, _fresh_schema_value(value))
    return classified_units


def summarize_pptx_classification(units: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return counts for PPTX preserve/translate classification."""
    unit_list = list(units)
    action_counts = Counter(str(unit.get("action") or "") for unit in unit_list)
    kind_counts = Counter(str(unit.get("text_kind") or "") for unit in unit_list)
    preserve_counts = Counter(str(unit.get("preserve_reason") or "") for unit in unit_list)
    return {
        "total_units": len(unit_list),
        "translate_units": int(action_counts.get("translate", 0)),
        "preserve_units": int(action_counts.get("preserve", 0)),
        "counts_by_action": dict(sorted(action_counts.items())),
        "counts_by_text_kind": dict(sorted(kind_counts.items())),
        "counts_by_preserve_reason": dict(sorted(preserve_counts.items())),
    }


def build_classification_sample_rows(
    units: Iterable[Mapping[str, Any]],
    sample_size: int = 8,
) -> List[Dict[str, Any]]:
    """Return sample classified units grouped by action/text_kind."""
    samples: List[Dict[str, Any]] = []
    seen_counts: Counter[Tuple[str, str]] = Counter()
    for unit in units:
        key = (str(unit.get("action") or ""), str(unit.get("text_kind") or ""))
        if seen_counts[key] >= sample_size:
            continue
        seen_counts[key] += 1
        samples.append(
            {
                "action": unit.get("action"),
                "text_kind": unit.get("text_kind"),
                "preserve_reason": unit.get("preserve_reason"),
                "unit_key": unit.get("unit_key"),
                "slide_number": unit.get("slide_number"),
                "source_type": unit.get("source_type"),
                "tokens": unit.get("tokens"),
                "number_prefix": unit.get("number_prefix"),
                "number_suffix": unit.get("number_suffix"),
                "translation_note": unit.get("translation_note"),
                "text": unit.get("text"),
            }
        )
    return samples

