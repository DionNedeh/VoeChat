from __future__ import annotations

import base64
import mimetypes
import time
import threading
import traceback
from pathlib import Path

from .context import ContextTracker
from .documents import DocumentService
from .engine import ServerRuntime, normalize_output_text, sanitize_hidden_output
from .models import ModelScanner
from .search import SearchService
from .storage import StorageService, utc_now

try:
    import webview
except Exception:
    webview = None


class VLiteAPI:
    MAX_IMAGE_UPLOAD_BYTES = 25 * 1024 * 1024

    def __init__(self, app_root: Path):
        self.app_root = Path(app_root)
        self.window = None
        self.storage = StorageService()
        self.scanner = ModelScanner(self.app_root)
        self.engine = ServerRuntime(self.app_root)
        self.documents = DocumentService()
        self.context = ContextTracker()
        self.search = SearchService()
        self.models: list[dict] = []
        self._frontend_ready = False
        self._streams: dict[str, dict] = {}
        self._streams_lock = threading.Lock()
        self._allowed_upload_paths: set[str] = set()

    def set_window(self, window):
        self.window = window

    def bootstrap(self):
        self._frontend_ready = True
        settings = self.storage.settings()
        server_path = self.engine.resolve_server_path(settings.get("server_path", ""))
        self.engine.inspect_help(server_path)
        folders = self.scanner.smart_folders(settings.get("model_folders", []))
        self.models = self.scanner.scan(folders)
        chat = self.storage.get_chat()
        return {
            "settings": settings,
            "models": self.models,
            "folders": folders,
            "chats": self.storage.list_chats(),
            "active_chat": chat,
            "engine": self.engine.status(),
            "context": self.refresh_context(chat["id"] if chat else None),
        }

    def scan_models(self):
        settings = self.storage.settings()
        folders = self.scanner.smart_folders(settings.get("model_folders", []))
        self.models = self.scanner.scan(folders)
        return {"models": self.models, "folders": folders}

    def add_model_folder(self):
        files = self._pick_folder()
        if not files:
            return {"cancelled": True}
        settings = self.storage.settings()
        folders = list(settings.get("model_folders", []))
        folder = files[0]
        if folder not in folders:
            folders.append(folder)
        settings = self.storage.save_settings({"model_folders": folders})
        scanned = self.scan_models()
        return {"settings": settings, **scanned}

    def save_settings(self, payload):
        settings = self.storage.save_settings(payload or {})
        server_path = self.engine.resolve_server_path(settings.get("server_path", ""))
        self.engine.inspect_help(server_path)
        return {"settings": settings, "engine": self.engine.status()}

    def create_chat(self):
        chat = self.storage.create_chat("New Chat")
        return {"chat": chat, "chats": self.storage.list_chats(), "context": self.refresh_context(chat["id"])}

    def select_chat(self, chat_id):
        chat = self.storage.set_active_chat(chat_id)
        return {"chat": chat, "chats": self.storage.list_chats(), "context": self.refresh_context(chat["id"])}

    def rename_chat(self, chat_id, title):
        chat = self.storage.get_chat(chat_id)
        if not chat:
            return {"success": False, "error": "Chat not found."}
        chat["title"] = (title or "New Chat").strip()[:80]
        self.storage.save_chat(chat)
        return {"success": True, "chat": chat, "chats": self.storage.list_chats()}

    def delete_chat(self, chat_id):
        chats = self.storage.delete_chat(chat_id)
        return {"success": True, "chats": chats, "chat": self.storage.get_chat(), "context": self.refresh_context()}

    def export_chat(self, chat_id):
        chat = self.storage.get_chat(chat_id)
        if not chat:
            return {"success": False, "error": "Chat not found."}
        default_name = self._safe_export_name(chat.get("title") or "VLite Chat")
        path = self._save_file(f"{default_name}.txt")
        if not path:
            return {"success": False, "cancelled": True}
        try:
            export_path = self._validate_export_path(path)
            with export_path.open("w", encoding="utf-8") as handle:
                handle.write(self._chat_export_text(chat))
        except Exception as exc:
            return {"success": False, "error": f"Could not export chat: {exc}"}
        return {"success": True, "path": str(export_path)}

    def toggle_fullscreen(self):
        if not self.window:
            return {"success": False, "error": "Window is not ready."}
        try:
            self.window.toggle_fullscreen()
            return {"success": True}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def load_model(self, model_id, mode="text", projector_path=""):
        model = self._find_model(model_id)
        if not model:
            return {"success": False, "error": "Model not found. Rescan your model folders."}
        if mode == "vision" and not projector_path:
            projectors = model.get("projectors") or []
            if projectors:
                projector_path = projectors[0]["path"]
        settings = self.storage.settings()
        result = self.engine.start(model, settings, vision=(mode == "vision"), projector_path=projector_path)
        self._emit({"event": "engine", "engine": self.engine.status()})
        self._emit({"event": "context", "context": self.refresh_context()})
        return result

    def unload_model(self):
        result = self.engine.stop()
        self._emit({"event": "engine", "engine": self.engine.status()})
        self._emit({"event": "context", "context": self.refresh_context()})
        return result

    def upload_files(self):
        paths = self._pick_files()
        if not paths:
            return {"cancelled": True}
        self._allowed_upload_paths = {str(Path(path).resolve(strict=True)) for path in paths}
        chat = self.storage.get_chat()
        if not chat:
            chat = self.storage.create_chat("New Chat")
        uploads = list(chat.get("uploads", []))
        errors = []
        for path in paths:
            try:
                safe_path = self._validate_upload_path(path)
                safe_path_str = str(safe_path)
                mime = mimetypes.guess_type(path)[0] or ""
                if mime.startswith("image/") or safe_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
                    uploads.append(self._image_upload(safe_path_str, mime))
                else:
                    uploads.append(self.documents.process(safe_path_str))
            except Exception as exc:
                errors.append(f"{Path(path).name}: {exc}")
        chat["uploads"] = uploads
        self.storage.save_chat(chat)
        context = self.refresh_context(chat["id"])
        self._emit({"event": "chat", "chat": chat, "chats": self.storage.list_chats()})
        self._emit({"event": "context", "context": context})
        return {"chat": chat, "chats": self.storage.list_chats(), "errors": errors, "context": context}

    def clear_uploads(self):
        chat = self.storage.get_chat()
        if not chat:
            return {"success": False}
        chat["uploads"] = []
        self.storage.save_chat(chat)
        return {"success": True, "chat": chat, "chats": self.storage.list_chats(), "context": self.refresh_context(chat["id"])}

    def send_message(self, text, web_enabled=False):
        text = (text or "").strip()
        if not text:
            return {"queued": False, "error": "Message cannot be empty."}
        if not self.engine.status().get("loaded"):
            return {"queued": False, "error": "Load a model first."}
        chat = self.storage.get_chat()
        if not chat:
            chat = self.storage.create_chat("New Chat")
        user_message = {"role": "user", "content": text, "created_at": utc_now()}
        chat.setdefault("messages", []).append(user_message)
        if chat.get("title") == "New Chat":
            chat["title"] = text[:48]
        self.storage.save_chat(chat)
        assistant_id = f"msg_{utc_now()}_{len(chat['messages'])}"
        self._create_stream(assistant_id, chat["id"])
        self._emit({"event": "chat", "chat": chat, "chats": self.storage.list_chats()})
        self._emit({"event": "stream_start", "chat_id": chat["id"], "message_id": assistant_id})

        def run():
            reply = ""
            try:
                latest = self.storage.get_chat(chat["id"]) or chat
                messages = self._build_messages(latest, text, bool(web_enabled))
                if latest.get("_last_web_status"):
                    self._set_stream_notice(assistant_id, latest["_last_web_status"])
                settings = self.storage.settings()
                show_thinking = bool(settings.get("show_thinking"))
                for chunk in self.engine.chat_stream(messages, settings.get("generation", {}), include_reasoning=show_thinking):
                    visible = chunk if show_thinking else sanitize_hidden_output(chunk)
                    visible = normalize_output_text(visible)
                    if visible:
                        reply += visible
                        self._append_stream_chunk(assistant_id, visible)
                if not reply:
                    reply = "[No response returned]"
            except Exception as exc:
                traceback.print_exc()
                reply = normalize_output_text(f"[Generation error: {exc}]")
                self._append_stream_chunk(assistant_id, reply)
            finally:
                reply = normalize_output_text(reply)
                saved = self.storage.get_chat(chat["id"]) or chat
                saved.setdefault("messages", []).append({"role": "assistant", "content": reply, "created_at": utc_now()})
                self.storage.save_chat(saved)
                self._finish_stream(assistant_id, saved, self.storage.list_chats(), self.refresh_context(saved["id"]))
                self._emit({
                    "event": "stream_complete",
                    "message_id": assistant_id,
                    "chat": saved,
                    "chats": self.storage.list_chats(),
                    "context": self.refresh_context(saved["id"]),
                })

        threading.Thread(target=run, daemon=True).start()
        return {
            "queued": True,
            "chat": chat,
            "chats": self.storage.list_chats(),
            "context": self.refresh_context(chat["id"]),
            "message_id": assistant_id,
            "web_status": "Web tools running..." if web_enabled else "",
        }

    def poll_stream(self, message_id, cursor=0):
        cursor = max(0, int(cursor or 0))
        with self._streams_lock:
            stream = self._streams.get(message_id)
            if not stream:
                return {"found": False, "chunks": [], "cursor": cursor, "done": True}
            chunks = list(stream.get("chunks", []))
            result = {
                "found": True,
                "chunks": chunks[cursor:],
                "cursor": len(chunks),
                "done": bool(stream.get("done")),
                "error": stream.get("error", ""),
            }
            if stream.get("done"):
                result.update({
                    "chat": stream.get("chat"),
                    "chats": stream.get("chats"),
                    "context": stream.get("context"),
                })
            if stream.get("notice"):
                result["notice"] = stream.get("notice")
            return result

    def refresh_context(self, chat_id=None):
        chat = self.storage.get_chat(chat_id)
        if not chat:
            return self.context.estimate_messages([], [], 0).public()
        context_size = 0
        status = self.engine.status()
        if status.get("model"):
            context_size = int(status["model"].get("context_size") or 0)
        summary = self.context.estimate_messages(chat.get("messages", []), chat.get("uploads", []), context_size)
        return summary.public()

    def _build_messages(self, chat: dict, latest_text: str, web_enabled: bool) -> list[dict]:
        settings = self.storage.settings()
        system_parts = ["You are VLite, a concise local AI assistant. Respect the user's system instructions and local context."]
        instructions = settings.get("system_instructions") or ""
        if instructions:
            system_parts.append(instructions)
        if not settings.get("thinking_enabled", False):
            system_parts.append("Do not reveal hidden reasoning. Provide the final answer only.")

        uploads = chat.get("uploads", [])
        doc_context = []
        images = []
        for upload in uploads:
            if upload.get("kind") == "image":
                images.append(upload)
            else:
                doc_context.append(f"File: {upload.get('name')}\n{upload.get('text', '')[:12000]}")
        if doc_context:
            system_parts.append("Uploaded file context:\n" + "\n\n".join(doc_context))
        web_status = ""
        if web_enabled:
            url_context = self.search.read_urls_from_text(latest_text)
            search_context = self.search.search_context(latest_text)
            web_context = "\n\n".join(part for part in (url_context, search_context) if part)
            system_parts.append(
                f"Web/search tools are ON for the latest user request only: {latest_text}\n"
                "VLite has already run web search and URL-reading before generation. The online context below is available "
                "right now. Use it as source material for this answer. Do not say you lack web/search access when context is "
                "attached. Ignore stale web details from earlier turns if they conflict with the latest request. Synthesize a "
                "direct answer from snippets and page excerpts; do not merely list sources or tell the user to visit links "
                "unless the context explicitly failed or is irrelevant."
            )
            if web_context:
                web_status = "Web context attached."
                system_parts.append("Online context from VLite web tools:\n" + web_context[:16000])
            else:
                web_status = "No web results were returned."
                system_parts.append("Online context from VLite web tools: no readable online results were returned.")
        if web_status:
            chat["_last_web_status"] = web_status
        messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
        for item in chat.get("messages", [])[-30:]:
            if item.get("role") in {"user", "assistant"}:
                messages.append({"role": item["role"], "content": item.get("content", "")})
        if images and self.engine.status().get("model", {}).get("vision_loaded"):
            content = [{"type": "text", "text": latest_text}]
            for image in images[-4:]:
                content.append({"type": "image_url", "image_url": {"url": image.get("data_url")}})
            messages[-1] = {"role": "user", "content": content}
        return messages

    def _image_upload(self, path: str, mime: str) -> dict:
        safe_path = self._validate_upload_path(path)
        size = safe_path.stat().st_size
        if size > self.MAX_IMAGE_UPLOAD_BYTES:
            raise ValueError("Image upload is too large.")
        data = safe_path.read_bytes()
        encoded = base64.b64encode(data).decode("ascii")
        return {
            "name": safe_path.name,
            "path": str(safe_path),
            "kind": "image",
            "mime": mime or "image/png",
            "data_url": f"data:{mime or 'image/png'};base64,{encoded}",
            "tokens": 0,
        }

    def _validate_upload_path(self, path: str) -> Path:
        try:
            resolved = Path(path).resolve(strict=True)
        except Exception as exc:
            raise ValueError("Selected file is unavailable.") from exc
        if not resolved.is_file():
            raise ValueError("Selected upload is not a file.")
        if str(resolved) not in self._allowed_upload_paths:
            raise ValueError("Rejected file path that was not selected in the file picker.")
        return resolved

    def _find_model(self, model_id: str) -> dict | None:
        for model in self.models:
            if model.get("id") == model_id or model.get("path") == model_id:
                return model
        scanned = self.scan_models()["models"]
        for model in scanned:
            if model.get("id") == model_id or model.get("path") == model_id:
                return model
        return None

    def _emit(self, payload: dict):
        if not self.window or not self._frontend_ready:
            return
        try:
            import json

            self.window.evaluate_js(f"window.VLiteEvents && window.VLiteEvents.handle({json.dumps(payload)});")
        except Exception:
            pass

    def _create_stream(self, message_id: str, chat_id: str):
        with self._streams_lock:
            self._streams[message_id] = {
                "chat_id": chat_id,
                "chunks": [],
                "done": False,
                "created_at": time.time(),
                "error": "",
            }
            self._prune_streams_locked()

    def _append_stream_chunk(self, message_id: str, chunk: str):
        if not chunk:
            return
        with self._streams_lock:
            stream = self._streams.setdefault(message_id, {"chunks": [], "done": False, "created_at": time.time()})
            stream.setdefault("chunks", []).append(chunk)

    def _set_stream_notice(self, message_id: str, notice: str):
        if not notice:
            return
        with self._streams_lock:
            stream = self._streams.setdefault(message_id, {"chunks": [], "done": False, "created_at": time.time()})
            stream["notice"] = notice

    def _finish_stream(self, message_id: str, chat: dict, chats: list[dict], context: dict, error: str = ""):
        with self._streams_lock:
            stream = self._streams.setdefault(message_id, {"chunks": [], "created_at": time.time()})
            stream.update({
                "done": True,
                "chat": chat,
                "chats": chats,
                "context": context,
                "error": error,
                "finished_at": time.time(),
            })
            self._prune_streams_locked()

    def _prune_streams_locked(self):
        now = time.time()
        stale = [
            key for key, stream in self._streams.items()
            if now - float(stream.get("created_at", now)) > 1800
            or (stream.get("done") and now - float(stream.get("finished_at", now)) > 300)
        ]
        for key in stale:
            self._streams.pop(key, None)

    def _pick_files(self):
        if not self.window or webview is None:
            return []
        result = self.window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=True,
            file_types=(
                "Supported files (*.txt;*.md;*.pdf;*.docx;*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.gif)",
                "All files (*.*)",
            ),
        )
        return list(result or [])

    def _pick_folder(self):
        if not self.window or webview is None:
            return []
        result = self.window.create_file_dialog(webview.FOLDER_DIALOG, allow_multiple=False)
        return list(result or [])

    def _save_file(self, default_name: str):
        if not self.window or webview is None:
            return ""
        result = self.window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=default_name,
            file_types=("Text document (*.txt)", "All files (*.*)"),
        )
        if isinstance(result, (list, tuple)):
            return result[0] if result else ""
        return result or ""

    @staticmethod
    def _validate_export_path(path: str) -> Path:
        if not path:
            raise ValueError("No export path was selected.")
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            raise ValueError("Export path must be absolute.")
        parent = candidate.parent.resolve(strict=True)
        home = Path.home().resolve(strict=True)
        if not parent.is_relative_to(home):
            raise ValueError("Export path must stay inside your user profile.")
        if not parent.is_dir():
            raise ValueError("Export destination is not a directory.")
        filename = VLiteAPI._safe_export_name(candidate.stem) + ".txt"
        return parent / filename

    @staticmethod
    def _safe_export_name(title: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in (" ", "-", "_") else " " for ch in title)
        safe = " ".join(safe.split()).strip() or "VLite Chat"
        return safe[:64]

    @staticmethod
    def _chat_export_text(chat: dict) -> str:
        lines = [
            f"VLite Chat Export",
            f"Title: {chat.get('title') or 'New Chat'}",
            f"Exported: {utc_now()}",
            "",
        ]
        uploads = chat.get("uploads") or []
        if uploads:
            lines.append("Uploads:")
            for upload in uploads:
                lines.append(f"- {upload.get('name', 'file')} ({upload.get('kind', 'file')})")
            lines.append("")
        for item in chat.get("messages", []):
            role = (item.get("role") or "message").upper()
            content = item.get("content") or ""
            lines.extend([f"{role}:", content, ""])
        return "\n".join(lines).rstrip() + "\n"
