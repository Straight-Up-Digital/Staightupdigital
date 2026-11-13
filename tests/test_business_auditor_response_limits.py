import unittest
from unittest.mock import patch

import httpx

import business_auditor


class ChunkStream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.started = False

    def __iter__(self):
        self.started = True
        yield from self.chunks

    def close(self):
        pass


class BoundedResponseTests(unittest.TestCase):
    def test_declared_oversized_response_is_rejected_before_body_read(self):
        stream = ChunkStream([b"12345"])
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-length": "5"},
                stream=stream,
                request=request,
            )
        )

        with httpx.Client(transport=transport) as client:
            with self.assertRaises(business_auditor.ResponseTooLargeError):
                business_auditor.get_bounded_response(client, "https://business.example", 4)

        self.assertFalse(stream.started)

    def test_chunked_response_is_rejected_when_streamed_bytes_exceed_limit(self):
        stream = ChunkStream([b"123", b"45"])
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream, request=request)
        )

        with httpx.Client(transport=transport) as client:
            with self.assertRaises(business_auditor.ResponseTooLargeError):
                business_auditor.get_bounded_response(client, "https://business.example", 4)

        self.assertTrue(stream.started)

    def test_response_at_limit_is_returned_with_content(self):
        stream = ChunkStream([b"12", b"34"])
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream, request=request)
        )

        with httpx.Client(transport=transport) as client:
            response = business_auditor.get_bounded_response(client, "https://business.example", 4)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"1234")

    def test_extract_page_reports_oversized_response_as_failure(self):
        stream = ChunkStream([b"12345"])
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream, request=request)
        )

        with patch.object(business_auditor, "MAX_RESPONSE_BYTES", 4):
            with httpx.Client(transport=transport) as client:
                page = business_auditor.extract_page("https://business.example", client)

        self.assertFalse(page["success"])
        self.assertIn("ResponseTooLargeError", page["error"])


if __name__ == "__main__":
    unittest.main()
