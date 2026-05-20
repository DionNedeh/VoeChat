from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from vlite.context import ContextTracker
from vlite.documents import DocumentService
from vlite.api import VLiteAPI
from vlite.engine import ServerRuntime, normalize_output_text, sanitize_hidden_output
from vlite.models import ModelScanner
from vlite.search import SearchService
from vlite.storage import StorageService


class ModelScannerTests(unittest.TestCase):
    def test_pairs_model_with_nearby_mmproj(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "gemma-vision-q4.gguf"
            mmproj = root / "mmproj-gemma-vision.gguf"
            model.write_bytes(b"model")
            mmproj.write_bytes(b"projector")
            found = ModelScanner(root).scan([str(root)])
            self.assertEqual(len(found), 1)
            self.assertTrue(found[0]["vision"])
            self.assertEqual(found[0]["projectors"][0]["path"], str(mmproj))


class ServerRuntimeTests(unittest.TestCase):
    def test_command_filters_unsupported_flags_and_blocks_overrides(self):
        runtime = ServerRuntime(Path.cwd())
        runtime.supported_flags = {"--host", "--port", "--ctx-size", "--flash-attn", "--mmproj", "--reasoning"}
        settings = {
            "load": {
                "ctx_size": 4096,
                "gpu_layers": -1,
                "batch": 512,
                "ubatch": 256,
                "split_mode": "layer",
                "flash_attn": True,
                "mmap": True,
                "reasoning": "off",
                "reasoning_budget": -1,
                "extra_flags": "--port 9999 --ctx-size 2048 --fake-flag",
            }
        }
        command, skipped = runtime.build_command("server.exe", 1234, {"path": "m.gguf"}, settings, True, "mm.gguf")
        self.assertIn("--ctx-size", command)
        self.assertEqual(command[command.index("--flash-attn") + 1], "on")
        self.assertIn("--reasoning", command)
        self.assertIn("--mmproj", command)
        self.assertNotIn("9999", command)
        self.assertIn("--fake-flag", skipped)

    def test_sanitizes_hidden_reasoning(self):
        cleaned = sanitize_hidden_output("hello <think>secret</think> world")
        self.assertEqual(cleaned, "hello  world")

    def test_extracts_reasoning_content_when_requested(self):
        runtime = ServerRuntime(Path.cwd())
        chunks = [
            b'data: {"choices":[{"delta":{"reasoning_content":"thinking","content":" answer"}}]}\n',
            b"data: [DONE]\n",
        ]

        class Response:
            def __enter__(self):
                return chunks

            def __exit__(self, *_args):
                return False

        import vlite.engine as engine_module

        original = engine_module.urllib.request.urlopen
        engine_module.urllib.request.urlopen = lambda *_args, **_kwargs: Response()
        try:
            runtime.port = 1234
            output = "".join(runtime.chat_stream([], {}, include_reasoning=True))
        finally:
            engine_module.urllib.request.urlopen = original
        self.assertEqual(output, "thinking answer")

    def test_sse_parser_yields_incremental_data_events(self):
        class ByteResponse:
            def __init__(self, payload):
                self.payload = payload
                self.index = 0

            def read(self, size=1):
                if self.index >= len(self.payload):
                    return b""
                end = min(len(self.payload), self.index + size)
                chunk = self.payload[self.index:end]
                self.index = end
                return chunk

        payload = b'data: {"content":"a"}\n\ndata: {"content":"b"}\n\ndata: [DONE]\n\n'
        self.assertEqual(
            list(ServerRuntime._iter_sse_data(ByteResponse(payload))),
            ['{"content":"a"}', '{"content":"b"}', "[DONE]"],
        )

    def test_sse_parser_preserves_multibyte_utf8(self):
        class ByteResponse:
            def __init__(self, payload):
                self.payload = payload
                self.index = 0

            def read(self, size=1):
                if self.index >= len(self.payload):
                    return b""
                end = min(len(self.payload), self.index + size)
                chunk = self.payload[self.index:end]
                self.index = end
                return chunk

        payload = 'data: {"content":"one â€” two"}\n\n'.encode("utf-8")
        self.assertEqual(list(ServerRuntime._iter_sse_data(ByteResponse(payload))), ['{"content":"one â€” two"}'])

    def test_normalizes_common_mojibake(self):
        self.assertEqual(normalize_output_text("one Ã¢â‚¬â€ two Ã¢â‚¬Å“quotedÃ¢â‚¬Â and ï¿½ï¿½ï¿½"), 'one - two "quoted" and -')


class SearchTests(unittest.TestCase):
    def test_rejects_private_url_targets(self):
        service = SearchService()
        self.assertIn("Rejected", service.read_url("http://127.0.0.1/admin"))
        self.assertIn("Rejected", service.read_url("http://169.254.169.254/latest/meta-data"))
        with self.assertRaises(ValueError):
            service._validate_public_http_url("http://10.0.0.5/internal")

    def test_allows_public_http_targets(self):
        parsed = SearchService._validate_public_http_url("https://93.184.216.34/")
        self.assertEqual(parsed.scheme, "https")

    def test_weather_location_extraction(self):
        self.assertEqual(SearchService._weather_location("Can you search Polkville NC weather?"), "Polkville NC")
        self.assertEqual(SearchService._weather_location("Can you search Shelby NC weather for me and give me the forecast?"), "Shelby NC")

    def test_search_queries_include_review_and_current_variants(self):
        service = SearchService()
        queries = service._search_queries("Latest sci-fi movies in 2026")
        self.assertTrue(any("latest" in item.lower() for item in queries))
        self.assertTrue(any("release dates" in item.lower() for item in queries))
        review_queries = service._search_queries("find reviews for VLite")
        self.assertTrue(any("reviews ratings" in item.lower() for item in review_queries))


class ApiTests(unittest.TestCase):
    def test_stream_queue_returns_incremental_chunks_and_done_state(self):
        api = VLiteAPI(Path.cwd())
        api._create_stream("m1", "chat1")
        api._append_stream_chunk("m1", "hello ")
        self.assertEqual(api.poll_stream("m1", 0)["chunks"], ["hello "])
        api._append_stream_chunk("m1", "world")
        self.assertEqual(api.poll_stream("m1", 1)["chunks"], ["world"])
        api._finish_stream("m1", {"id": "chat1"}, [{"id": "chat1"}], {"label": "2"})
        done = api.poll_stream("m1", 2)
        self.assertTrue(done["done"])
        self.assertEqual(done["chat"]["id"], "chat1")

    def test_delete_chat_preserves_valid_active_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = VLiteAPI(Path.cwd())
            api.storage = StorageService(root=tmp)
            first = api.storage.create_chat("First")
            second = api.storage.create_chat("Second")
            result = api.delete_chat(second["id"])
            self.assertTrue(result["success"])
            self.assertNotEqual(result["chat"]["id"], second["id"])
            self.assertEqual(result["chat"]["id"], first["id"])

    def test_chat_export_text_contains_messages(self):
        chat = {
            "title": "Export Test",
            "uploads": [{"name": "note.txt", "kind": "document"}],
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ],
        }
        text = VLiteAPI._chat_export_text(chat)
        self.assertIn("Title: Export Test", text)
        self.assertIn("USER:\nhello", text)
        self.assertIn("ASSISTANT:\nhi there", text)

    def test_export_path_stays_inside_user_profile(self):
        home_export = Path.home() / "unsafe<>name.md"
        safe_path = VLiteAPI._validate_export_path(str(home_export))
        self.assertEqual(safe_path.suffix, ".txt")
        self.assertTrue(safe_path.parent.resolve().is_relative_to(Path.home().resolve()))
        with self.assertRaises(ValueError):
            VLiteAPI._validate_export_path("relative.txt")

    def test_web_enabled_prompt_injects_search_context(self):
        api = VLiteAPI(Path.cwd())

        class FakeSearch:
            def read_urls_from_text(self, _text):
                return "URL: https://example.test\nExample page"

            def search_context(self, _text):
                return "Result: current source text"

        api.search = FakeSearch()
        chat = {"messages": [{"role": "user", "content": "latest news"}], "uploads": []}
        messages = api._build_messages(chat, "latest news", web_enabled=True)
        system = messages[0]["content"]
        self.assertIn("Web/search tools are ON", system)
        self.assertIn("latest user request only: latest news", system)
        self.assertIn("Do not say you lack web/search access", system)
        self.assertIn("Example page", system)
        self.assertIn("current source text", system)
        self.assertEqual(chat["_last_web_status"], "Web context attached.")

    def test_web_disabled_prompt_does_not_inject_search_context(self):
        api = VLiteAPI(Path.cwd())
        chat = {"messages": [{"role": "user", "content": "latest news"}], "uploads": []}
        system = api._build_messages(chat, "latest news", web_enabled=False)[0]["content"]
        self.assertNotIn("Web/search tools are enabled", system)

    def test_image_upload_requires_file_picker_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "image.png"
            path.write_bytes(b"png")
            api = VLiteAPI(Path.cwd())
            with self.assertRaises(ValueError):
                api._image_upload(str(path), "image/png")
            api._allowed_upload_paths = {str(path.resolve())}
            result = api._image_upload(str(path), "image/png")
            self.assertEqual(result["name"], "image.png")


class StorageTests(unittest.TestCase):
    def test_storage_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = StorageService(root=tmp)
            settings = storage.save_settings({"load": {"ctx_size": 16384}})
            self.assertEqual(settings["load"]["ctx_size"], 16384)
            chat = storage.create_chat("Test")
            chat["messages"].append({"role": "user", "content": "hello"})
            storage.save_chat(chat)
            self.assertEqual(storage.get_chat(chat["id"])["messages"][0]["content"], "hello")

    def test_storage_rejects_path_traversal_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = StorageService(root=tmp)
            with self.assertRaises(ValueError):
                storage.write_json("../escape.json", {"bad": True})


class DocumentTests(unittest.TestCase):
    def test_txt_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            path.write_text("hello document", encoding="utf-8")
            result = DocumentService().process(str(path))
            self.assertEqual(result["text"], "hello document")

    def test_docx_stdlib_fallback_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "doc.docx"
            xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>Hello DOCX</w:t></w:r></w:p></w:body>
</w:document>"""
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", xml)
            result = DocumentService().process(str(path))
            self.assertEqual(result["text"], "Hello DOCX")

    def test_rejects_oversized_uploads_before_parsing(self):
        original = DocumentService.MAX_UPLOAD_BYTES
        DocumentService.MAX_UPLOAD_BYTES = 3
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "large.txt"
                path.write_text("too large", encoding="utf-8")
                with self.assertRaises(ValueError):
                    DocumentService().process(str(path))
        finally:
            DocumentService.MAX_UPLOAD_BYTES = original


class ContextTests(unittest.TestCase):
    def test_context_estimate(self):
        summary = ContextTracker().estimate_messages([{"content": "a" * 400}], context_size=1000)
        self.assertGreaterEqual(summary.used_tokens, 100)
        self.assertGreater(summary.percent, 0)


if __name__ == "__main__":
    unittest.main()
