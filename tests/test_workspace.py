"""Exercise durable browser submissions through the real text pipeline, offline."""
import base64
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.llm import ModelReply
from refresher.web import Workspace
from tests.test_extract import TEXT, response
from tests.test_triage import FakeModel, LABELS


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.workspace = Workspace(Path(folder.name))
        self.addCleanup(lambda: self.workspace.worker.shutdown(wait=True))

    def submit(self, payload):
        # Keep tests synchronous while retaining the actual worker orchestration.
        with patch.object(self.workspace.worker, 'submit') as enqueue:
            job = self.workspace.submit(payload)
            enqueue.assert_called_once()
        self.assertEqual(self.workspace.list_jobs()[0]['id'], job['id'])
        return job

    def test_full_capture_extraction_packaging_and_exact_evidence(self):
        client = FakeModel([ModelReply(LABELS), response()])
        job = self.submit({'text': TEXT, 'title': 'Evidence test'})
        original = (self.workspace.vault / 'raw' / f"{job['items'][0]}.json").read_bytes()
        with patch('engine.llm.client_from_environment', return_value=client), patch('engine.llm.judge_client_from_environment', return_value=None), patch('engine.entities.entity_client_from_environment', return_value=None), patch.dict('os.environ', {'WEB_FETCH':'off'}):
            self.workspace.process(job)
        detail = self.workspace.detail(job['id'])
        self.assertEqual(detail['status'], 'complete')
        result = detail['results'][0]
        self.assertEqual(result['text'], TEXT)
        self.assertEqual(len(result['processed']['claims']), 2)
        self.assertEqual(result['processed']['quality_status'], 'pending')
        span = result['processed']['claims'][0]['evidence']
        self.assertIn('Keep explanations simple.', TEXT[span['start_char']:span['end_char']])
        self.assertEqual((self.workspace.vault / 'raw' / f"{job['items'][0]}.json").read_bytes(), original)

    def test_binary_upload_saved_unchanged_and_never_claimed_as_parsed(self):
        content = b'\x89PNG\r\n\x1a\nnot-real-image'
        job = self.submit({'kind':'file', 'filename':'../../image.png', 'content':base64.b64encode(content).decode()})
        self.workspace.process(job)
        detail = self.workspace.detail(job['id'])
        self.assertEqual(detail['status'], 'pending')
        self.assertIn('parser', detail['message'])
        self.assertEqual(detail['attachment']['name'], 'image.png')
        self.assertEqual((self.workspace.vault / 'uploads' / detail['attachment']['sha256']).read_bytes(), content)
        self.assertEqual(detail['results'], [])

    def test_text_upload_goes_through_capture_and_ai_disabled_is_explicit(self):
        job = self.submit({'kind':'file', 'filename':'note.md', 'content':base64.b64encode(TEXT.encode()).decode()})
        with patch('engine.llm.client_from_environment', return_value=None), patch.dict('os.environ', {'WEB_FETCH':'off'}):
            self.workspace.process(job)
        result = self.workspace.detail(job['id'])
        self.assertEqual(result['results'][0]['text'], TEXT)
        self.assertEqual(result['status'], 'pending')
        self.assertIn('disabled', result['message'])
        with patch.object(self.workspace.worker, 'submit') as enqueue:
            retry = self.workspace.retry(job['id'])
            enqueue.assert_called_once()
        self.assertEqual(retry['items'], job['items'])
        with self.assertRaises(ValueError):
            self.workspace.retry(job['id'])

    def test_invalid_inputs_do_not_create_submission(self):
        for payload in ({'text':' '}, {'kind':'link','text':'file:///etc/passwd'}, {'kind':'file','filename':'x','content':'bad$'}, {'text':'x'*60001}, {'kind':'bad','text':'x'}):
            with self.subTest(payload=str(payload)[:80]), self.assertRaises(ValueError):
                self.workspace.submit(payload)
        self.assertEqual(self.workspace.list_jobs(), [])

    def test_reviewed_pipeline_reaches_accepted_and_retains_review(self):
        client = FakeModel([ModelReply(LABELS), response()])
        judge = FakeModel([ModelReply({'claims': [
            {'claim_id': f'C{i}', 'verdict': 'supported', 'issues': [], 'reason': 'Source supports this.'}
            for i in (1, 2)], 'omitted_concepts': [], 'summary': 'Both claims match the source.'})])
        entities = FakeModel([ModelReply({'entities': []}), ModelReply({
            'entities': [], 'omitted_entities': [], 'summary': 'No named people or organizations.'})])
        job = self.submit({'text': TEXT})
        with patch('engine.llm.client_from_environment', return_value=client), patch('engine.llm.judge_client_from_environment', return_value=judge), patch('engine.entities.entity_client_from_environment', return_value=entities), patch.dict('os.environ', {'WEB_FETCH':'off'}):
            self.workspace.process(job)
        detail = self.workspace.detail(job['id'])
        self.assertEqual(detail['status'], 'complete')
        record = detail['results'][0]['processed']
        self.assertEqual(record['quality_status'], 'accepted')
        self.assertEqual(record['reviews']['claims']['status'], 'accepted')
        self.assertEqual(len(judge.calls), 1)

    def test_pdf_native_text_and_page_marker(self):
        try:
            import pypdfium2  # noqa: F401
        except ImportError:
            self.skipTest('Optional PDF extra is not installed.')
        stream = b'BT /F1 12 Tf 50 700 Td (Keep exact source evidence in knowledge notes.) Tj ET'
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
                   b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                   b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
                   b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
                   b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
        content = b'%PDF-1.4\n'
        offsets = [0]
        for index, obj in enumerate(objects, 1):
            offsets.append(len(content))
            content += f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n'
        start = len(content)
        content += b'xref\n0 6\n0000000000 65535 f \n'
        content += b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
        content += f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF'.encode()
        job = self.submit({'kind':'file', 'filename':'evidence.pdf', 'content':base64.b64encode(content).decode()})
        with patch('engine.llm.client_from_environment', return_value=None), patch.dict('os.environ', {'WEB_FETCH':'off'}):
            self.workspace.process(job)
        result = self.workspace.detail(job['id'])
        self.assertEqual(result['stage'], 'Ready for analysis')
        self.assertIn('[PDF page 1]', result['results'][0]['text'])
        self.assertIn('Keep exact source evidence', result['results'][0]['text'])
        self.assertEqual((self.workspace.vault / 'uploads' / job['attachment']['sha256']).read_bytes(), content)

    def test_triage_skip_is_a_result_not_a_processing_error(self):
        client = FakeModel([ModelReply({**LABELS, 'keep':False})])
        job = self.submit({'text':'A short social greeting'})
        with patch('engine.llm.client_from_environment', return_value=client), patch.dict('os.environ', {'WEB_FETCH':'off'}):
            self.workspace.process(job)
        result = self.workspace.detail(job['id'])
        self.assertEqual(result['status'], 'complete')
        self.assertFalse(result['results'][0]['processed']['triage']['keep'])


if __name__ == '__main__':
    unittest.main()
