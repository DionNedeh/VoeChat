from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class StorageService:
    def __init__(self, app_name: str = "VLite", root: str | None = None):
        base = root or os.path.join(os.getenv("APPDATA") or str(Path.home()), app_name)
        self.root = Path(base)
        self.root.mkdir(parents=True, exist_ok=True)
        self.upload_dir = self.root / "uploads"
        self.upload_dir.mkdir(exist_ok=True)

    def read_json(self, name: str, default: Any) -> Any:
        path = self._data_file(name)
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def write_json(self, name: str, payload: Any) -> Any:
        path = self._data_file(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        tmp.replace(path)
        return payload

    def _data_file(self, name: str) -> Path:
        candidate = Path(name)
        if candidate.is_absolute() or len(candidate.parts) != 1 or candidate.name != name:
            raise ValueError("Storage file name must be a simple file name.")
        resolved = (self.root / candidate.name).resolve()
        root = self.root.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("Storage file path escaped the data directory.")
        return resolved

    def settings(self) -> dict:
        defaults = {
            "model_folders": [],
            "server_path": "",
            "web_enabled": False,
            "load": {
                "ctx_size": 8192,
                "gpu_layers": -1,
                "batch": 1024,
                "ubatch": 512,
                "threads": 0,
                "threads_batch": 0,
                "split_mode": "layer",
                "main_gpu": 0,
                "tensor_split": "",
                "flash_attn": True,
                "mmap": True,
                "mlock": False,
                "cache_prompt": True,
                "no_warmup": False,
                "type_k": "",
                "type_v": "",
                "chat_template": "",
                "chat_template_kwargs": "",
                "reasoning": "auto",
                "reasoning_budget": -1,
                "reasoning_format": "auto",
                "mmproj_offload": True,
                "extra_flags": "",
            },
            "generation": {
                "max_tokens": 1600,
                "temperature": 0.7,
                "top_p": 0.95,
                "top_k": 40,
                "min_p": 0.05,
                "typical_p": 1.0,
                "repeat_penalty": 1.1,
                "presence_penalty": 0.0,
                "frequency_penalty": 0.0,
                "mirostat": 0,
                "mirostat_tau": 5.0,
                "mirostat_eta": 0.1,
                "stop": "",
            },
            "thinking_enabled": False,
            "show_thinking": False,
        }
        saved = self.read_json("settings.json", {})
        return self._merge(defaults, saved)

    def save_settings(self, payload: dict) -> dict:
        current = self.settings()
        merged = self._merge(current, payload or {})
        return self.write_json("settings.json", merged)

    def list_chats(self) -> list[dict]:
        chats = self.read_json("chats.json", [])
        if not chats:
            chat = self.create_chat("New Chat")
            return [chat]
        return chats

    def create_chat(self, title: str = "New Chat") -> dict:
        chats = self.read_json("chats.json", [])
        chat = {
            "id": uuid.uuid4().hex,
            "title": title,
            "messages": [],
            "uploads": [],
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        chats.insert(0, chat)
        self.write_json("chats.json", chats)
        self.write_json("active_chat.json", {"id": chat["id"]})
        return chat

    def active_chat_id(self) -> str:
        active = self.read_json("active_chat.json", {})
        chats = self.list_chats()
        ids = {chat["id"] for chat in chats}
        if active.get("id") in ids:
            return active["id"]
        self.write_json("active_chat.json", {"id": chats[0]["id"]})
        return chats[0]["id"]

    def set_active_chat(self, chat_id: str) -> dict:
        chat = self.get_chat(chat_id)
        if chat:
            self.write_json("active_chat.json", {"id": chat_id})
        return chat or self.list_chats()[0]

    def get_chat(self, chat_id: str | None = None) -> dict | None:
        target = chat_id or self.active_chat_id()
        for chat in self.list_chats():
            if chat["id"] == target:
                return chat
        return None

    def save_chat(self, chat: dict) -> dict:
        chats = self.list_chats()
        chat["updated_at"] = utc_now()
        for index, item in enumerate(chats):
            if item["id"] == chat["id"]:
                chats[index] = chat
                break
        else:
            chats.insert(0, chat)
        chats.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        self.write_json("chats.json", chats)
        return chat

    def delete_chat(self, chat_id: str) -> list[dict]:
        chats = [chat for chat in self.list_chats() if chat["id"] != chat_id]
        if not chats:
            chats = [self.create_chat("New Chat")]
        self.write_json("chats.json", chats)
        self.write_json("active_chat.json", {"id": chats[0]["id"]})
        return chats

    @staticmethod
    def _merge(base: dict, update: dict) -> dict:
        merged = dict(base)
        for key, value in (update or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = StorageService._merge(merged[key], value)
            else:
                merged[key] = value
        return merged
