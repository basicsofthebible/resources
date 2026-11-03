"""Create a six-slide, no-SmartArt fixture using installed python-pptx."""
from pathlib import Path

def create_sample(path):
    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation()
    def slide():
        return prs.slides.add_slide(prs.slide_layouts[6])
    def text(s, value):
        shape = s.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(2))
        shape.text = value
        return shape
    text(slide(), 'A small extraction example\nSecond paragraph')
    box = text(slide(), 'Mixed ')
    run = box.text_frame.paragraphs[0].add_run()
    run.text = 'formatting'; run.font.bold = True
    s = slide()
    table = s.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(6), Inches(2)).table
    for cell, value in zip([table.cell(r,c) for r in range(2) for c in range(2)], ['Label','Value','Count','42']):
        cell.text = value
    group = slide().shapes.add_group_shape()
    text(group, 'Grouped text')
    text(slide(), 'John 3:16\nSee Revelation 1:1 for context\n1. Numbered label')
    text(slide(), 'https://example.org\n2026\nRepeated label\nRepeated label')
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(path))
    return path

if __name__ == '__main__':
    print(create_sample(Path(__file__).resolve().parent / 'stage1_sample.pptx'))
