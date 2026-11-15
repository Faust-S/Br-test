"""U01 document adapter contract: synthetic files, real disk writes, cold readback."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iwxa_universal_install.document_operations import (
    DocumentOperationError, append_docx_paragraph, publish_approved,
    verify_cold_readback, discover_document_programs, select_document_executor,
    make_document_cli_spec, convert_to_pdf_via_u01, pdf_text_coverage, digest,
    files_binary_write_candidate, DocumentCandidate,
)


CONTENT_TYPES = b'''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>'''
RELS = b'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''
WORD = b'''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t>Original user document</w:t></w:r></w:p><w:sectPr/></w:body>
</w:document>'''


def docx_bytes(extra=None, word=WORD):
    from io import BytesIO
    target = BytesIO()
    with ZipFile(target, 'w') as z:
        z.writestr('[Content_Types].xml', CONTENT_TYPES)
        z.writestr('_rels/.rels', RELS)
        z.writestr('word/document.xml', word)
        z.writestr('word/styles.xml', b'<styles/>')
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return target.getvalue()


def minimal_pdf():
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] >>',
    ]
    result = b'%PDF-1.4\n'
    offsets = []
    for n, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += str(n).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    start = len(result)
    result += b'xref\n0 4\n0000000000 65535 f \n'
    for offset in offsets:
        result += f'{offset:010d} 00000 n \n'.encode()
    return result + b'trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n' + str(start).encode() + b'\n%%EOF\n'


class FileOwner:
    """Disposable stand-in for Agent Files: approval and no-clobber owned by host."""
    def __init__(self, root, original):
        self.source = root / 'original.docx'
        self.output = root / 'result.docx'
        self.source.write_bytes(original)

    def read_source(self):
        return self.source.read_bytes()

    def read_output(self):
        return self.output.read_bytes()

    def write_new(self, data):
        with self.output.open('xb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='iwxa-doc-u01-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = docx_bytes()
        self.owner = FileOwner(self.root, self.raw)

    def test_real_docx_edit_approved_disk_write_cold_read(self):
        candidate = append_docx_paragraph(self.owner.read_source(),
                                          'Approved addition 2026', expected_source_sha256=digest(self.raw))
        self.assertNotEqual(candidate.output_sha256, candidate.source_sha256)
        self.assertFalse(self.owner.output.exists())
        with self.assertRaisesRegex(DocumentOperationError, 'HUMAN_APPROVAL_REQUIRED'):
            publish_approved(candidate, approved_plan_digest='invalid',
                             read_source=self.owner.read_source,
                             write_new=self.owner.write_new,
                             read_output=self.owner.read_output)
        self.assertFalse(self.owner.output.exists())
        result = publish_approved(candidate, approved_plan_digest=candidate.plan_digest,
                                  read_source=self.owner.read_source,
                                  write_new=self.owner.write_new,
                                  read_output=self.owner.read_output)
        self.assertEqual(result['status'], 'READBACK_VERIFIED')
        self.assertFalse(result['result_persisted'])
        with ZipFile(self.owner.output) as z:
            self.assertIn(b'Approved addition 2026', z.read('word/document.xml'))
            self.assertEqual(z.read('word/styles.xml'), b'<styles/>')
        self.assertEqual(self.owner.source.read_bytes(), self.raw)
        # Distinct objects and file handles after simulated cold restart.
        restarted = FileOwner.__new__(FileOwner)
        restarted.source = self.root / 'original.docx'
        restarted.output = self.root / 'result.docx'
        self.assertEqual(verify_cold_readback(candidate, read_source=restarted.read_source,
                                              read_output=restarted.read_output)['status'], 'PASS')

    def test_binary_write_action_binds_source_destination_and_exact_payload(self):
        import base64
        c = append_docx_paragraph(self.raw, 'User-approved change',
                                  expected_source_sha256=digest(self.raw))
        action = files_binary_write_candidate(c, root_id='personal',
                    source_path='original.docx', destination_path='reviewed.docx')
        self.assertEqual(action['operation'], 'files.write_binary')
        self.assertEqual(set(action), {'operation','target','arguments','risk','reversible'})
        self.assertEqual(action['target'], 'reviewed.docx')
        self.assertEqual(action['arguments']['mode'], 'create')
        self.assertEqual(action['arguments']['expected_source_sha256'], digest(self.raw))
        self.assertEqual(base64.b64decode(action['arguments']['content_base64']), c.output_bytes)
        self.assertEqual(action['arguments']['expected_content_sha256'], c.output_sha256)
        for source, output in [('a.docx', 'a.docx'), ('a.docx', '../escape.docx'),
                               ('a.docx', '.hidden.docx'), ('a.docx', 'output.pdf')]:
            with self.assertRaises(DocumentOperationError):
                files_binary_write_candidate(c, root_id='personal',
                                             source_path=source, destination_path=output)
        tampered = DocumentCandidate(c.operation, c.executor, c.source_sha256,
                                     'f'*64, c.output_format, c.output_bytes, c.summary)
        with self.assertRaisesRegex(DocumentOperationError, 'BINARY_ACTION_PAYLOAD_MISMATCH'):
            files_binary_write_candidate(tampered, root_id='personal',
                    source_path='original.docx', destination_path='edited.docx')
        oversize = DocumentCandidate('pdf.convert','DOCUMENT_LIBREOFFICE',c.source_sha256,
                                     digest(b'Z'*700001),'pdf',b'Z'*700001,'big')
        with self.assertRaisesRegex(DocumentOperationError,'BINARY_ACTION_TOO_LARGE'):
            files_binary_write_candidate(oversize,root_id='personal',
                    source_path='original.docx',destination_path='big.pdf')

    def test_preserve_source_revision_and_do_not_overwrite(self):
        candidate = append_docx_paragraph(self.raw, 'First change', expected_source_sha256=digest(self.raw))
        self.owner.source.write_bytes(self.raw + b'edited')
        with self.assertRaisesRegex(DocumentOperationError, 'SOURCE_REVISION_CHANGED'):
            publish_approved(candidate, approved_plan_digest=candidate.plan_digest,
                             read_source=self.owner.read_source,
                             write_new=self.owner.write_new, read_output=self.owner.read_output)
        self.assertFalse(self.owner.output.exists())
        self.owner.source.write_bytes(self.raw)
        self.owner.output.write_bytes(b'pre-existing file')
        with self.assertRaises(FileExistsError):
            publish_approved(candidate, approved_plan_digest=candidate.plan_digest,
                             read_source=self.owner.read_source,
                             write_new=self.owner.write_new, read_output=self.owner.read_output)
        self.assertEqual(self.owner.output.read_bytes(), b'pre-existing file')

    def test_reject_signed_macros_complex_and_missing_parts(self):
        for additions in ({'_xmlsignatures/sig1.xml': b'signed'}, {'word/vbaProject.bin': b'vba'}):
            src = docx_bytes(additions)
            with self.assertRaisesRegex(DocumentOperationError, 'DOCX_SIGNED_OR_MACRO_UNSUPPORTED'):
                append_docx_paragraph(src, 'change', expected_source_sha256=digest(src))
        advanced = WORD.replace(b'xmlns:w=', b'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" mc:Ignorable="w15" xmlns:w=')
        src = docx_bytes(word=advanced)
        edited = append_docx_paragraph(src, 'change', expected_source_sha256=digest(src))
        with ZipFile(__import__('io').BytesIO(edited.output_bytes)) as z:
            self.assertIn(b'mc:Ignorable=\"w15\"', z.read('word/document.xml'))
        with self.assertRaisesRegex(DocumentOperationError, 'DOCX_PACKAGE_UNREADABLE'):
            append_docx_paragraph(b'not a zip', 'change', expected_source_sha256=digest(b'not a zip'))

    def test_readback_detects_tampering_and_failed_write(self):
        candidate = append_docx_paragraph(self.raw, 'receipt', expected_source_sha256=digest(self.raw))
        with self.assertRaisesRegex(DocumentOperationError, 'OUTPUT_READBACK_MISMATCH'):
            publish_approved(candidate, approved_plan_digest=candidate.plan_digest,
                             read_source=self.owner.read_source,
                             write_new=lambda data: self.owner.output.write_bytes(b'corrupt'),
                             read_output=self.owner.read_output)
        self.assertEqual(verify_cold_readback(candidate, read_source=self.owner.read_source,
                                              read_output=self.owner.read_output)['status'], 'FAIL')

    def test_discovery_does_not_infer_api_or_install(self):
        home = self.root / 'programfiles'
        office = home / 'Microsoft Office' / 'root' / 'Office16'
        office.mkdir(parents=True)
        (office / 'WINWORD.EXE').write_bytes(b'test')
        lo = home / 'LibreOffice' / 'program'
        lo.mkdir(parents=True)
        (lo / 'soffice.com').write_bytes(b'test')
        inv = discover_document_programs(environ={'PROGRAMFILES': str(home)},
                                         which_fn=lambda _: None, platform_name='nt',
                                         association_probe=lambda ext: 'Word.Document.12' if ext == '.docx' else None)
        rows = {r['program_id']: r for r in inv['programs']}
        self.assertTrue(rows['MICROSOFT_WORD']['installed_detected'])
        self.assertFalse(rows['MICROSOFT_WORD']['api_usable_verified'])
        self.assertTrue(rows['LIBREOFFICE']['installed_detected'])
        self.assertFalse(rows['LIBREOFFICE']['cli_usable_verified'])
        self.assertEqual(inv['default_associations']['.docx'], 'Word.Document.12')
        self.assertEqual(select_document_executor('pdf.convert', inv)['status'], 'U01_CONNECT_REQUIRED')
        self.assertEqual(select_document_executor('pdf.convert', inv,
                                                 u01_connected_program_ids=('LIBREOFFICE',))['status'], 'READY_U01_CLI')
        self.assertEqual(select_document_executor('docx.append_paragraph', inv)['status'], 'READY_NATIVE')
        self.assertEqual(select_document_executor('pdf.ocr', inv)['status'], 'PROGRAM_NOT_INSTALLED')
        self.assertFalse(inv['installation_mutated'])
        self.assertFalse(inv['program_launched'])

    def test_cli_contract_for_u01_and_pdf_conversion_readback(self):
        raw_pdf = minimal_pdf()
        source = self.owner.source
        dest_file = self.root / 'output.pdf'
        class ConnectedFake:
            spec = make_document_cli_spec('LIBREOFFICE', '/usr/bin/soffice',
                                          binary_sha256='a' * 64, version_contains='24.2')
            def capabilities(self):
                return {'status': 'DECLARED', 'installation_verified': True}
            def invoke(self, operation, params, **kw):
                self.last_op = operation
                Path(params['outdir'], source.stem + '.pdf').write_bytes(raw_pdf)
                return {'status': 'EXECUTED', 'executionOutcome': 'ACKNOWLEDGED'}
        connection = ConnectedFake()
        with self.assertRaisesRegex(DocumentOperationError, 'HUMAN_APPROVAL_REQUIRED'):
            convert_to_pdf_via_u01(connection, source_path=source,
                                   expected_source_sha256=digest(self.raw), scratch_root=self.root, approved=False)
        candidate = convert_to_pdf_via_u01(connection, source_path=source,
                                           expected_source_sha256=digest(self.raw), scratch_root=self.root, approved=True)
        self.assertEqual(connection.last_op, 'pdf_docx')
        self.assertFalse(dest_file.exists())  # Staging is not publication.
        self.assertEqual(candidate.output_format, 'pdf')
        self.assertEqual(self.owner.source.read_bytes(), self.raw)

    def test_ocrmypdf_u01_connect_uses_help_when_version_goes_to_stderr(self):
        spec = make_document_cli_spec('OCRMY_PDF', '/usr/bin/ocrmypdf',
                                      binary_sha256='b'*64,
                                      version_contains='15.2.0')
        self.assertEqual(spec['version_args'], ['--help'])
        self.assertEqual(spec['version_contains'], 'ocrmypdf')
        self.assertEqual(spec['executable_sha256'], 'b'*64)
        self.assertEqual(spec['operations']['ocr_pdf'][0], '--skip-text')

    def test_pdf_coverage_requires_proof_not_guess(self):
        self.assertEqual(pdf_text_coverage(b'garbage')['status'], 'UNDETERMINED')

    def test_pdf_without_text_asks_before_ocr_when_reader_exists(self):
        try:
            import pypdf
        except ImportError:
            self.skipTest('pypdf is optional')
        coverage = pdf_text_coverage(minimal_pdf())
        self.assertEqual(coverage['status'], 'OCR_OFFER')
        self.assertEqual(coverage['pages_without_text'], [1])
        self.assertEqual(coverage['ocr_executor'], 'OCRMY_PDF')

    def test_reject_invalid_source_and_paragraph(self):
        with self.assertRaisesRegex(DocumentOperationError, 'DOCX_SOURCE_BYTES_REQUIRED'):
            append_docx_paragraph('invalid', 'X', expected_source_sha256='')
        with self.assertRaisesRegex(DocumentOperationError, 'PARAGRAPH_SINGLE_LINE_REQUIRED'):
            append_docx_paragraph(self.raw, 'multiline\ntext', expected_source_sha256=digest(self.raw))
        with self.assertRaisesRegex(DocumentOperationError, 'SOURCE_REVISION_CHANGED'):
            append_docx_paragraph(self.raw, 'Change', expected_source_sha256='0' * 64)

    def test_ocr_image_requires_connected_u01_and_valid_result(self):
        from iwxa_universal_install.document_operations import ocr_image_via_u01
        input_file = self.root / 'page.png'
        input_file.write_bytes(b'fake scanner fixture')
        class TesseractFake:
            spec = make_document_cli_spec('TESSERACT', '/usr/bin/tesseract',
                                          binary_sha256='a' * 64, version_contains='5.5')
            def capabilities(self):
                return {'status': 'DECLARED', 'installation_verified': True}
            def invoke(self, operation, params, **kw):
                Path(params['output_base'] + '.txt').write_text('Invoice total 1500\n', encoding='utf-8')
                return {'status': 'EXECUTED', 'executionOutcome': 'ACKNOWLEDGED'}
        program = TesseractFake()
        source_sha = digest(input_file.read_bytes())
        with self.assertRaisesRegex(DocumentOperationError, 'HUMAN_APPROVAL_REQUIRED'):
            ocr_image_via_u01(program, source_path=input_file,
                             expected_source_sha256=source_sha, scratch_root=self.root, approved=False)
        candidate = ocr_image_via_u01(program, source_path=input_file,
                                      expected_source_sha256=source_sha, scratch_root=self.root, approved=True)
        self.assertEqual(candidate.output_format, 'txt')
        self.assertIn(b'Invoice total', candidate.output_bytes)
        self.assertEqual(source_sha, digest(input_file.read_bytes()))
        self.assertEqual(list(self.root.glob('iwxa-image-ocr-*')), [])

    def test_external_failure_never_promotes_unknown_outcome(self):
        class BrokenConnection:
            spec = make_document_cli_spec('LIBREOFFICE', '/usr/bin/soffice',
                                          binary_sha256='a' * 64, version_contains='24.2')
            def capabilities(self):
                return {'status': 'DECLARED', 'installation_verified': True}
            def invoke(self, operation, params, **kw):
                return {'status': 'CLI_TIMED_OUT', 'executionOutcome': 'UNKNOWN'}
        with self.assertRaisesRegex(DocumentOperationError, 'U01_CONVERSION_NOT_ACKNOWLEDGED'):
            convert_to_pdf_via_u01(BrokenConnection(), source_path=self.owner.source,
                                   expected_source_sha256=digest(self.raw), scratch_root=self.root, approved=True)
        self.assertEqual(len(list(self.root.rglob('*.pdf'))), 0)


if __name__ == '__main__':
    unittest.main()
