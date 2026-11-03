"""Conservative native IconCircleLabelList text mapping. No provider or layout logic."""
import posixpath
from xml.etree import ElementTree as ET
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
D = 'http://schemas.openxmlformats.org/drawingml/2006/diagram'
S = 'http://schemas.microsoft.com/office/drawing/2008/diagram'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS = dict(a=A, dgm=D, dsp=S, p=P)
LAYOUT = 'urn:microsoft.com/office/officeart/2018/5/layout/IconCircleLabelList'

def require(condition, message):
    if not condition:
        raise ValueError(message)


def one(items, label):
    items = list(items)
    require(len(items) == 1, f'{label}: expected one, found {len(items)}')
    return items[0]



def xml(z, name):
    return ET.fromstring(z.read(name))


def relationships(z, part):
    name = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
    rows = list(xml(z, name))
    require(len({r.get('Id') for r in rows}) == len(rows), 'Duplicate relationship IDs')
    result = {}
    for row in rows:
        if row.get('TargetMode') == 'External':
            continue
        target = row.get('Target', '')
        target = (target.lstrip('/') if target.startswith('/') else
                  posixpath.normpath(posixpath.join(posixpath.dirname(part), target)))
        require(target in z.namelist(), f'Missing relationship target: {target}')
        result[row.get('Id')] = (row.get('Type', '').rsplit('/', 1)[-1], target)
    return result


def resolve(z, slide_number, graphic_frame_index):
    require(z.testzip() is None, 'Corrupt source ZIP')
    require(len(z.namelist()) == len(set(z.namelist())), 'Duplicate ZIP members')
    pres = xml(z, 'ppt/presentation.xml')
    rels = relationships(z, 'ppt/presentation.xml')
    slides = pres.findall('p:sldIdLst/p:sldId', NS)
    require(1 <= slide_number <= len(slides), 'Invalid slide number')
    slide_rel = rels[slides[slide_number - 1].get(f'{{{R}}}id')]
    require(slide_rel[0] == 'slide', 'Invalid slide relationship')
    slide = slide_rel[1]
    frames = xml(z, slide).findall('.//p:graphicFrame', NS)
    require(1 <= graphic_frame_index <= len(frames), 'Invalid graphic frame index')
    frame = frames[graphic_frame_index - 1]
    refs = one(frame.findall('a:graphic/a:graphicData/dgm:relIds', NS), 'Diagram references')
    sr = relationships(z, slide)
    parts = {}
    for attr, kind in [('dm', 'diagramData'), ('lo', 'diagramLayout'),
                       ('qs', 'diagramQuickStyle'), ('cs', 'diagramColors')]:
        rel = sr.get(refs.get(f'{{{R}}}{attr}'))
        require(rel is not None and rel[0] == kind, f'Invalid {kind} relationship')
        parts[attr] = rel[1]
    require(xml(z, parts['lo']).get('uniqueId') == LAYOUT, 'Unsupported diagram layout')
    data = xml(z, parts['dm'])
    ext = one(data.findall('dgm:extLst/a:ext/dsp:dataModelExt', NS), 'Drawing extension')
    drawing_rel = sr.get(ext.get('relId'))
    require(drawing_rel is not None and drawing_rel[0] == 'diagramDrawing', 'Invalid cache relationship')
    parts['drawing'] = drawing_rel[1]
    drawing = xml(z, parts['drawing'])
    points = data.findall('dgm:ptLst/dgm:pt', NS)
    ids = [p.get('modelId') for p in points]
    require(all(ids) and len(ids) == len(set(ids)), 'Missing/duplicate data model IDs')
    shapes = drawing.findall('.//dsp:sp', NS)
    sids = [s.get('modelId') for s in shapes]
    require(all(sids) and len(sids) == len(set(sids)), 'Missing/duplicate cache model IDs')
    connections = data.findall('dgm:cxnLst/dgm:cxn', NS)
    mappings = []
    for point in points:
        body = point.find('dgm:t', NS)
        if body is None or not body.findall('.//a:t', NS):
            continue
        mid = point.get('modelId')
        require(point.get('type') in (None, 'node'), 'Unsupported text-bearing point type')
        associated = [p for p in points if p.find('dgm:prSet', NS) is not None
                      and p.find('dgm:prSet', NS).get('presAssocID') == mid]
        text_matches = []
        for p in associated:
            matches = [s for s in shapes if s.get('modelId') == p.get('modelId')
                       and s.findall('dsp:txBody//a:t', NS)]
            if matches:
                text_matches.append((p, one(matches, 'Cache shape')))
        presentation, shape = one(text_matches, f'Text presentation for {mid}')
        pid = presentation.get('modelId')
        require(presentation.find('dgm:prSet', NS).get('presName') == 'textRect', 'Unsupported text role')
        one([c for c in connections if c.get('type') == 'presOf'
             and c.get('srcId') == mid and c.get('destId') == pid], 'presOf connection')
        linked = {c.get('destId') for c in connections if c.get('type') == 'presOf' and c.get('srcId') == mid}
        require(linked == {pid}, 'Inconsistent text presentation connections')
        cached_body = one(shape.findall('dsp:txBody', NS), 'Cache text body')
        texts = []
        for b in (body, cached_body):
            para = one(b.findall('a:p', NS), 'Label paragraph')
            require(all(c.tag in {f'{{{A}}}{t}' for t in ('pPr', 'r', 'endParaRPr')} for c in para),
                    'Unsupported paragraph structure')
            runs = para.findall('a:r', NS)
            require(1 <= len(runs) <= 2 and all(all(c.tag in {f'{{{A}}}rPr', f'{{{A}}}t'} for c in r) for r in runs),
                    'Unsupported run structure')
            nodes = [one(r.findall('a:t', NS), 'Run text') for r in runs]
            require(len(nodes) == len(b.findall('.//a:t', NS)), 'Unexpected nested text')
            texts.append([n.text or '' for n in nodes])
        require(texts[0] == texts[1], f'Data/cache disagreement: {mid}')
        mappings.append(dict(data_point_id=mid, cached_shape_id=pid, source_spans=texts[0]))
    require(mappings and len({m['cached_shape_id'] for m in mappings}) == len(mappings), 'Shared cache target')
    return dict(slide_part=slide, parts=parts, labels=mappings)



def paragraphs(archive, unit, roots=None):
    """Resolve and validate a saved data-point identity; return both paragraphs."""
    mapping = resolve(archive, unit['slide_number'], unit['smartart_graphic_frame_index'])
    require(mapping['parts']['dm'] == unit['smartart_data_part'], 'Stale SmartArt data part')
    if unit.get('smartart_text_version') == 1:
        require(mapping['parts']['drawing'] == unit['smartart_drawing_part'], 'Stale SmartArt cache part')
    label = one([m for m in mapping['labels'] if m['data_point_id'] == unit['smartart_model_id']], 'SmartArt data identity')
    if 'smartart_cached_shape_id' in unit:
        require(label['cached_shape_id'] == unit['smartart_cached_shape_id'], 'Stale SmartArt cached identity')
    roots = {} if roots is None else roots
    result = []
    for role, path, mid in [('dm', 'dgm:ptLst/dgm:pt', unit['smartart_model_id']),
                             ('drawing', 'dsp:spTree/dsp:sp', label['cached_shape_id'])]:
        part = mapping['parts'][role]
        if part not in roots: roots[part] = xml(archive, part)
        node = one([n for n in roots[part].findall(path, NS) if n.get('modelId') == mid], 'SmartArt target')
        paragraph = one(node.findall('dgm:t/a:p' if role == 'dm' else 'dsp:txBody/a:p', NS), 'SmartArt paragraph')
        result.append((paragraph, part))
    return result, label
