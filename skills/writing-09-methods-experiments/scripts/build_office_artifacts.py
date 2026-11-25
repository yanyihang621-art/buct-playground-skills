"""Create editable Word output and render the actual Office document when requested."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _target(root, filename, suffix):
    dest = (root / filename).resolve()
    if not dest.is_relative_to(root.resolve()) or dest.suffix.lower() != suffix:
        raise ValueError(f'expected a {suffix} filename inside the output directory')
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def _inline(paragraph, text):
    for span in re.split(r'(\*\*[^*]+\*\*|`[^`]+`)', text):
        if span.startswith('**') and span.endswith('**'):
            paragraph.add_run(span[2:-2]).bold = True
        elif span.startswith('`') and span.endswith('`'):
            paragraph.add_run(span[1:-1]).font.name = 'Courier New'
        else:
            paragraph.add_run(span)


def _word_document(markdown, path):
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.styles['Normal'].font.size = Pt(11)
    table = None
    code = False
    for line in markdown.splitlines():
        if line.strip().startswith(('```', '~~~')):
            code = not code
            continue
        if not line.strip():
            table = None
            continue
        if code:
            doc.add_paragraph().add_run(line).font.name = 'Courier New'
        elif line.strip().startswith('|'):
            cells = [s.strip() for s in re.split(r'(?<!\\)\|', line.strip().strip('|'))]
            if all(re.fullmatch(r':?-{2,}:?', s) for s in cells):
                continue
            if table is None or len(table.columns) != len(cells):
                table = doc.add_table(rows=0, cols=len(cells))
                table.style = 'Table Grid'
            row = table.add_row()
            for cell, value in zip(row.cells, cells):
                _inline(cell.paragraphs[0], value.replace('\\|', '|'))
        else:
            table = None
            heading = re.match(r'^(#{1,6})\s+(.+)', line)
            bullet = re.match(r'^\s*([-+*]|\d+[.)])\s+(.+)', line)
            if heading:
                _inline(doc.add_heading(level=len(heading[1])), heading[2])
            elif bullet:
                style = 'List Number' if bullet[1][0].isdigit() else 'List Bullet'
                _inline(doc.add_paragraph(style=style), bullet[2])
            else:
                _inline(doc.add_paragraph(), line)
    doc.save(path)


def _convert_office(draft, scratch):
    tool = shutil.which('libreoffice') or shutil.which('soffice')
    if tool:
        env = dict(os.environ, SAL_USE_VCLPLUGIN='svp')
        subprocess.run([tool, '-env:UserInstallation=' + (scratch / 'profile').as_uri(), '--headless',
                        '--convert-to', 'pdf', '--outdir', str(scratch), str(draft)],
                       check=True, timeout=70, capture_output=True, env=env)
    elif sys.platform == 'win32':
        # Launch a separate hidden instance, and give the converter a timeout.
        subprocess.run([sys.executable, str(Path(__file__).resolve()), str(draft)], check=True,
                       timeout=70, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        raise RuntimeError('LibreOffice unavailable. Render the actual DOCX using the case office renderer.')


def _export_with_windows_word(docx):
    from win32com.client import DispatchEx

    app = DispatchEx('Word.Application')
    try:
        app.Visible = False
        app.DisplayAlerts = 0
        app.AutomationSecurity = 3
        document = app.Documents.Open(str(docx), ReadOnly=True, AddToRecentFiles=False, ConfirmConversions=False)
        try:
            document.ExportAsFixedFormat(str(docx.with_suffix('.pdf')), 17)
        finally:
            document.Close(False)
    finally:
        app.Quit(False)


def render_office(markdown, output, docx_name, pdf_name):
    artifacts, issues = {}, []
    if not docx_name and not pdf_name:
        return artifacts, issues
    try:
        with tempfile.TemporaryDirectory(prefix='methods-render-') as temp:
            scratch = Path(temp)
            draft = scratch / 'chapter.docx'
            _word_document(markdown, draft)
            if docx_name:
                word_dest = _target(output, docx_name, '.docx')
                shutil.copyfile(draft, word_dest)
                artifacts['manuscript_docx'] = word_dest.relative_to(output.resolve()).as_posix()
            _convert_office(draft, scratch)
            pdf = scratch / 'chapter.pdf'
            if not pdf.is_file() or not pdf.read_bytes().startswith(b'%PDF-'):
                raise ValueError('Office renderer did not produce a PDF')
            dest = _target(output, pdf_name or str(Path(docx_name).with_suffix('.pdf')), '.pdf')
            shutil.copyfile(pdf, dest)
            artifacts['manuscript_pdf'] = dest.relative_to(output.resolve()).as_posix()
            try:
                import fitz
            except ImportError:
                return artifacts, issues  # The rendered PDF is the full preview.
            with fitz.open(pdf) as rendered:
                if not len(rendered):
                    raise ValueError('rendered PDF has no pages')
                preview = dest.with_name(dest.stem + '-page1.png')
                rendered[0].get_pixmap().save(str(preview))
                artifacts['preview_png'] = preview.relative_to(output.resolve()).as_posix()
    except (ImportError, RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        issues.append({'code': 'office_render_incomplete', 'detail': str(exc)})
    return artifacts, issues


if __name__ == '__main__' and sys.platform == 'win32' and len(sys.argv) == 2:
    _export_with_windows_word(Path(sys.argv[1]))
