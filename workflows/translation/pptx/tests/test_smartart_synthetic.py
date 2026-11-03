"""Synthetic editable SmartArt mapping and round-trip tests; no real deck content."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pptx_translation import smartart_xml as sx
from pptx_translation import pptx_helpers as ph
from pptx_translation import pptx_reconstruction as pr
from pptx_translation import primary_translation as pt
from pptx_translation import evaluation as ev
from pptx_translation import finalization as fin
from pptx_translation import finalized_reconstruction as fr
from test_primary_translation import FakeGemini
from test_evaluation import FakeOpenAI

A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
D = 'http://schemas.openxmlformats.org/drawingml/2006/diagram'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
S = 'http://schemas.microsoft.com/office/drawing/2008/diagram'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
LAYOUT = 'urn:microsoft.com/office/officeart/2018/5/layout/IconCircleLabelList'


def create_synthetic_smartart(path):
    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1)).text = 'Ordinary neighbor'
    base = path.with_name('base.pptx')
    prs.save(base)
    with ZipFile(base) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    frame = f'''<p:graphicFrame xmlns:p="{P}" xmlns:a="{A}" xmlns:dgm="{D}" xmlns:r="{R}">
      <p:nvGraphicFramePr><p:cNvPr id="3" name="Synthetic diagram"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>
      <p:xfrm><a:off x="0" y="0"/><a:ext cx="1000000" cy="1000000"/></p:xfrm>
      <a:graphic><a:graphicData uri="{D}"><dgm:relIds r:dm="rDm" r:lo="rLo" r:qs="rQs" r:cs="rCs"/></a:graphicData></a:graphic>
    </p:graphicFrame>'''
    root = ET.fromstring(parts['ppt/slides/slide1.xml'])
    root.find(f'{{{P}}}cSld/{{{P}}}spTree').append(ET.fromstring(frame))
    parts['ppt/slides/slide1.xml'] = ET.tostring(root)
    relationships = ET.fromstring(parts['ppt/slides/_rels/slide1.xml.rels'])
    for rid, kind, part in [('rDm','diagramData','data1'),('rLo','diagramLayout','layout1'),
                            ('rQs','diagramQuickStyle','quickStyle1'),('rCs','diagramColors','colors1'),
                            ('rDraw','diagramDrawing','drawing1')]:
        ET.SubElement(relationships, f'{{{REL}}}Relationship', Id=rid, Type=R+'/'+kind, Target='../diagrams/'+part+'.xml')
    parts['ppt/slides/_rels/slide1.xml.rels'] = ET.tostring(relationships)
    paragraph = '<a:p><a:r><a:rPr b="1"/><a:t>Synthetic </a:t></a:r><a:r><a:rPr i="1"/><a:t>label</a:t></a:r></a:p>'
    parts['ppt/diagrams/data1.xml'] = f'''<dgm:dataModel xmlns:dgm="{D}" xmlns:a="{A}" xmlns:dsp="{S}">
      <dgm:ptLst><dgm:pt modelId="data-label" type="node"><dgm:t>{paragraph}</dgm:t></dgm:pt>
      <dgm:pt modelId="cache-label" type="pres"><dgm:prSet presAssocID="data-label" presName="textRect"/></dgm:pt></dgm:ptLst>
      <dgm:cxnLst><dgm:cxn modelId="connection" type="presOf" srcId="data-label" destId="cache-label"/></dgm:cxnLst>
      <dgm:extLst><a:ext uri="synthetic"><dsp:dataModelExt relId="rDraw"/></a:ext></dgm:extLst>
    </dgm:dataModel>'''.encode()
    parts['ppt/diagrams/drawing1.xml'] = f'<dsp:drawing xmlns:dsp="{S}" xmlns:a="{A}"><dsp:spTree><dsp:sp modelId="cache-label"><dsp:txBody>{paragraph}</dsp:txBody></dsp:sp></dsp:spTree></dsp:drawing>'.encode()
    for filename, tag, attrs in [('layout1','layoutDef',f' uniqueId="{LAYOUT}"'),('quickStyle1','styleDef',''),('colors1','colorsDef','')]:
        parts['ppt/diagrams/'+filename+'.xml'] = f'<dgm:{tag} xmlns:dgm="{D}"{attrs}/>'.encode()
    types = ET.fromstring(parts['[Content_Types].xml'])
    for name in parts:
        if name.startswith('ppt/diagrams/'):
            ET.SubElement(types, f'{{{CT}}}Override', PartName='/'+name, ContentType='application/xml')
    parts['[Content_Types].xml'] = ET.tostring(types)
    with ZipFile(path, 'w', ZIP_DEFLATED) as z:
        for name, data in parts.items(): z.writestr(name, data)
    return parts


class SyntheticSmartArtTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.source = self.folder/'synthetic.pptx'
        self.parts = create_synthetic_smartart(self.source)
        self.original = self.source.read_bytes()

    def tearDown(self):
        self.assertEqual(self.source.read_bytes(), self.original)

    def mutate(self, part, operation):
        parts = dict(self.parts)
        root = ET.fromstring(parts[part]); operation(root); parts[part] = ET.tostring(root)
        target = self.folder/'mutated.pptx'
        with ZipFile(target, 'w', ZIP_DEFLATED) as z:
            for name, data in parts.items(): z.writestr(name, data)
        return target

    def test_supported_mapping_identity_and_both_paragraphs(self):
        with ZipFile(self.source) as z:
            mapping = sx.resolve(z, 1, 1)
            self.assertEqual(mapping['labels'], [dict(data_point_id='data-label', cached_shape_id='cache-label', source_spans=['Synthetic ', 'label'])])
            unit = dict(slide_number=1, smartart_graphic_frame_index=1, smartart_data_part='ppt/diagrams/data1.xml',
                        smartart_model_id='data-label', smartart_text_version=1,
                        smartart_drawing_part='ppt/diagrams/drawing1.xml', smartart_cached_shape_id='cache-label')
            paragraphs, label = sx.paragraphs(z, unit)
            self.assertEqual([part for _, part in paragraphs], ['ppt/diagrams/data1.xml','ppt/diagrams/drawing1.xml'])
            self.assertEqual([''.join(p.itertext()) for p, _ in paragraphs], ['Synthetic label']*2)
            with self.assertRaisesRegex(ValueError, 'Stale SmartArt cached identity'):
                sx.paragraphs(z, dict(unit, smartart_cached_shape_id='wrong'))

    def test_reject_unsupported_layout_and_data_cache_disagreement(self):
        cases = [('ppt/diagrams/layout1.xml', lambda root: root.set('uniqueId','unsupported'), 'Unsupported diagram layout'),
                 ('ppt/diagrams/drawing1.xml', lambda root: setattr(root.find('.//a:t',sx.NS),'text','Different'), 'Data/cache disagreement')]
        for part, operation, message in cases:
            with self.subTest(part=part):
                with ZipFile(self.mutate(part,operation)) as z:
                    with self.assertRaisesRegex(ValueError,message): sx.resolve(z,1,1)

    def test_reject_ambiguous_identity_and_missing_presentation_link(self):
        def duplicate(root):
            points = root.find('dgm:ptLst',sx.NS); points.append(copy.deepcopy(points[0]))
        def unlink(root):
            connections = root.find('dgm:cxnLst',sx.NS); connections.remove(connections[0])
        for operation, message in [(duplicate,'Missing/duplicate data model IDs'),(unlink,'presOf connection')]:
            with ZipFile(self.mutate('ppt/diagrams/data1.xml',operation)) as z:
                with self.assertRaisesRegex(ValueError,message): sx.resolve(z,1,1)

    def test_fake_primary_evaluation_finalization_and_reconstruction_sync_cache(self):
        from pptx import Presentation
        units = ph.classify_pptx_text_units(ph.build_pptx_text_units(Presentation(self.source),self.source,include_styled_spans=True))
        smart = [u for u in units if u['source_type']=='smartart_data_point_text']
        self.assertEqual(len(smart),1)
        self.assertEqual([s['text'] for s in smart[0]['styled_spans']],['Synthetic ','label'])
        self.assertEqual(smart[0]['smartart_text_version'],1)
        p,e = self.folder/'primary.json',self.folder/'evaluation.json'
        def translate(payload):
            from test_primary_translation import make_response
            response = make_response(payload)
            for row in response['translations']:
                if row['unit_key']==smart[0]['unit_key']:
                    row['translated_spans']=[dict(span_id=s['span_id'],text=t) for s,t in zip(smart[0]['styled_spans'],['Etiqueta ','sintética'])]
            return response
        pt.run_primary_translation(FakeGemini([translate]),units,self.source,'English','Spanish','fake','Synthetic fixture',p)
        ev.run_evaluation(FakeOpenAI(),p,self.source,e,'fake')
        snapshot = fin.build_finalization_snapshot(p,e,self.source)
        self.assertTrue(snapshot['summary']['translation_complete'])
        files = fin.write_finalization_artifacts(snapshot,self.folder/'finalization')
        result = fr.reconstruct_finalized_snapshot(files['snapshot'],self.folder/'reconstructed',self.source)
        self.assertEqual(result['report']['status'],'passed')
        fr.validate_reconstruction_artifacts(result['report_path'],files['snapshot'],self.source)
        with ZipFile(result['output_path']) as z:
            self.assertEqual(sx.resolve(z,1,1)['labels'][0]['source_spans'],['Etiqueta ','sintética'])
            self.assertEqual({name for name in z.namelist() if z.read(name)!=self.parts[name]},
                             {'ppt/diagrams/data1.xml','ppt/diagrams/drawing1.xml'})


if __name__ == '__main__':
    unittest.main()
