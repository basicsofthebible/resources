"""Small, local SVG display-text converter. Font data is never edited."""

from pathlib import Path
import csv
import unicodedata

from lxml import etree
from opencc import OpenCC

SVG = "{http://www.w3.org/2000/svg}"
FIELDS = ["text_element_id", "segment", "original_text", "converted_text",
          "changed", "warning_or_error"]


def has_chinese(value):
    return any(0x3400 <= ord(c) <= 0x4DBF or 0x4E00 <= ord(c) <= 0x9FFF
               or 0x20000 <= ord(c) <= 0x323AF for c in value)


def read_svg(path):
    """Strict parsing; preserve whitespace, comments, entities and namespaces."""
    parser = etree.XMLParser(recover=False, remove_blank_text=False,
                             resolve_entities=False, no_network=True,
                             strip_cdata=False)
    tree = etree.parse(str(path), parser)
    if tree.getroot().tag != SVG + "svg":
        raise ValueError("Source root must be an SVG element in the SVG namespace.")
    return tree


def display_segments(tree):
    """Yield text slots inside display text, including descendant tails."""
    for text in tree.iter(SVG + "text"):
        for element in text.iter():
            if not isinstance(element.tag, str):
                continue
            if element.text is not None:
                yield text, element, "text", element.text
            if element is not text and element.tail is not None:
                yield text, element, "tail", element.tail


def inspect_source(path):
    tree = read_svg(path)
    return {"input": Path(path).name,
            "text_elements": sum(1 for _ in tree.iter(SVG + "text")),
            "chinese_segments": sum(has_chinese(s) for _, _, _, s in display_segments(tree))}


def output_paths(input_file, direction, output_dir="outputs", report_dir="reports"):
    if direction not in ("s2t", "t2s"):
        raise ValueError('Direction must be "s2t" or "t2s".')
    source = Path(input_file)
    old, new = (("simplified_chinese", "traditional_chinese") if direction == "s2t"
                else ("traditional_chinese", "simplified_chinese"))
    stem = source.stem.replace(old, new)
    if stem == source.stem:
        stem += "_" + new
    return Path(output_dir) / (stem + ".svg"), Path(report_dir) / (stem + "_qc.csv")


def unicode_warnings(value):
    issues = sorted({f"U+{ord(c):04X} {unicodedata.name(c, 'unnamed')}"
                     for c in value if c == "\ufffd" or
                     (unicodedata.category(c) in {"Cc", "Cf", "Co", "Cs", "Cn"}
                      and c not in "\t\r\n")})
    return "Suspicious Unicode: " + ", ".join(issues) if issues else ""


def validate_structure(source, output, intended):
    """Compare every node/attribute/text slot; allow only recorded changes."""
    before, after = list(source.iter()), list(output.iter())
    issues = []
    if len(before) != len(after):
        return ["Element/node count changed"]
    for index, (a, b) in enumerate(zip(before, after)):
        if (a.tag != b.tag or dict(a.attrib) != dict(b.attrib)
                or a.nsmap != b.nsmap or len(a) != len(b)
                or getattr(a, "target", None) != getattr(b, "target", None)):
            issues.append(f"Hierarchy, attributes or namespace changed at node {index}")
        for slot in ("text", "tail"):
            expected = intended.get((index, slot), getattr(a, slot))
            if getattr(b, slot) != expected:
                issues.append(f"Unexpected {slot} at node {index}")
    if source.docinfo.doctype != output.docinfo.doctype:
        issues.append("DOCTYPE changed")
    return issues


def convert_svg(input_file, direction="s2t", output_dir="outputs", report_dir="reports"):
    source_path = Path(input_file)
    output_path, report_path = output_paths(source_path, direction, output_dir, report_dir)
    if output_path.resolve() == source_path.resolve() or source_path.parent.resolve() == output_path.parent.resolve():
        raise ValueError("Output must be separate from the source directory.")
    if report_path.resolve() == source_path.resolve():
        raise ValueError("Report cannot overwrite the source.")
    source = read_svg(source_path)  # Parse errors propagate before any output is written.
    working = read_svg(source_path)
    converter = OpenCC(direction)
    nodes = {element: i for i, element in enumerate(working.iter())}
    intended, rows = {}, []
    segments = list(display_segments(working))
    # Ignore indentation, but keep explicit spaces/punctuation as phrase boundaries.
    adjacent = set()
    adjacent_segments = [s for s in segments if not (s[2] == "tail" and not s[3].strip())]
    for left, right in zip(adjacent_segments, adjacent_segments[1:]):
        if left[0] is right[0] and has_chinese(left[3]) and has_chinese(right[3]):
            adjacent.update(((left[1], left[2]), (right[1], right[2])))
    failures = changed = inspected = 0
    for text, element, slot, original in segments:
        chinese = has_chinese(original)
        warning = unicode_warnings(original)
        if not chinese and not warning:
            continue
        warnings = [warning] if warning else []
        converted = original
        if chinese:
            inspected += 1
            if (element, slot) in adjacent:
                warnings.append("Adjacent Chinese segments: phrase context may cross boundaries")
            try:
                converted = converter.convert(original)
                if not isinstance(converted, str):
                    raise TypeError("OpenCC returned a non-string value")
                converted_warning = unicode_warnings(converted)
                if converted_warning and converted_warning != warning:
                    warnings.append("Converted " + converted_warning)
            except Exception as exc:
                converted = original
                failures += 1
                warnings.append(f"Conversion failed; original retained: {type(exc).__name__}: {exc}")
            setattr(element, slot, converted)
            intended[(nodes[element], slot)] = converted
            changed += converted != original
        else:
            warnings.append("Skipped conversion: no Chinese characters")
        rows.append({"text_element_id": text.get("id", ""),
                     "segment": working.getpath(element) + "/." + slot,
                     "original_text": original, "converted_text": converted,
                     "changed": converted != original,
                     "warning_or_error": "; ".join(warnings)})
    data = etree.tostring(working, encoding=source.docinfo.encoding or "UTF-8",
                          xml_declaration=True, pretty_print=False)
    # Validate serialized bytes before saving, then independently reparse the saved file.
    parser = etree.XMLParser(recover=False, remove_blank_text=False,
                             resolve_entities=False, no_network=True, strip_cdata=False)
    candidate = etree.ElementTree(etree.fromstring(data, parser))
    issues = validate_structure(source, candidate, intended)
    if issues:
        raise ValueError("Pre-save validation failed: " + "; ".join(issues))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)
    issues = validate_structure(source, read_svg(output_path), intended)
    rows.append({"warning_or_error": "XML validation: " + ("; ".join(issues) if issues else "PASS")})
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8-sig", newline="") as report:
        writer = csv.DictWriter(report, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    summary = {"input": source_path.name, "output": output_path.name,
               "direction": direction, "chinese_segments_inspected": inspected,
               "changed": changed, "unchanged": inspected - changed,
               "conversion_failures": failures,
               "segments_with_warnings": sum(bool(r.get("warning_or_error")) for r in rows[:-1]),
               "structural_validation": "FAIL" if issues else "PASS",
               "report": str(report_path)}
    if issues:
        raise ValueError("Saved SVG validation failed; see " + str(report_path))
    return summary
