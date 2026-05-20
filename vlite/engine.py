from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import codecs
from pathlib import Path
from typing import Iterable


THINK_PATTERNS = [
    re.compile(r"(?is)<think>.*?</think>"),
    re.compile(r"(?is)<thinking>.*?</thinking>"),
    re.compile(r"(?is)<\|channel\|>analysis.*?(?=<\|channel\|>|$)"),
]

MOJIBAKE_REPLACEMENTS = {
    "Ã¢â‚¬â€": "-",
    "Ã¢â‚¬â€œ": "-",
    "Ã¢â‚¬Ëœ": "'",
    "Ã¢â‚¬â„¢": "'",
    "Ã¢â‚¬Å“": '"',
    "Ã¢â‚¬Â": '"',
    "Ã¢â‚¬Â¦": "...",
    "Ã‚ ": " ",
    "Ã‚": "",
    "ï¿½ï¿½ï¿½": "-",
    "ï¿½ï¿½": "-",
}


class ServerRuntime:
    def __init__(self, app_root: Path):
        self.app_root = Path(app_root)
        self.process: subprocess.Popen | None = None
        self.model: dict | None = None
        self.port = 0
        self.server_path = ""
        self.help_text = ""
        self.supported_flags: set[str] = set()
        self.last_error = ""
        self._lock = threading.Lock()

    def resolve_server_path(self, configured: str = "") -> str:
        candidates = [
            configured,
            os.getenv("VLITE_LLAMA_SERVER", ""),
            str(self.app_root / "bin" / "llama-server.exe"),
            str(self.app_root / "llama-server.exe"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return str(Path(candidate).resolve())
        return str((self.app_root / "bin" / "llama-server.exe").resolve())

    def inspect_help(self, server_path: str) -> dict:
        self.server_path = server_path
        if not Path(server_path).exists():
            self.help_text = ""
            self.supported_flags = set()
            return {"ok": False, "error": f"llama-server.exe was not found at {server_path}"}
        try:
            output = subprocess.run(
                [server_path, "--help"],
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.help_text = (output.stdout or "") + "\n" + (output.stderr or "")
            self.supported_flags = set(re.findall(r"(?<!\w)(--[a-zA-Z0-9][a-zA-Z0-9-]*)", self.help_text))
            return {"ok": True, "flags": sorted(self.supported_flags)}
        except Exception as exc:
            self.help_text = ""
            self.supported_flags = set()
            return {"ok": False, "error": str(exc)}

    def status(self) -> dict:
        running = self.process is not None and self.process.poll() is None
        return {
            "running": running,
            "loaded": running and bool(self.model),
            "model": self.model,
            "port": self.port,
            "url": f"http://127.0.0.1:{self.port}" if running else "",
            "server_path": self.server_path,
            "last_error": self.last_error,
            "supported_flags": sorted(self.supported_flags),
        }

    def start(self, model: dict, settings: dict, vision: bool = False, projector_path: str = "") -> dict:
        with self._lock:
            self.stop()
            self.last_error = ""
            server_path = self.resolve_server_path(settings.get("server_path", ""))
            help_result = self.inspect_help(server_path)
            if not Path(server_path).exists():
                self.last_error = help_result.get("error", "llama-server.exe is missing.")
                return {"success": False, "error": self.last_error, "status": self.status()}
            self.port = self._free_port()
            command, skipped = self.build_command(server_path, self.port, model, settings, vision, projector_path)
            try:
                self.process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception as exc:
                self.last_error = str(exc)
                return {"success": False, "error": self.last_error, "status": self.status()}
            ready = self._wait_until_ready(timeout=45)
            if not ready:
                tail = self._collect_output_tail()
                self.stop()
                self.last_error = tail or "llama-server did not become ready before timeout."
                return {"success": False, "error": self.last_error, "status": self.status(), "skipped_flags": skipped}
            self.model = {
                **model,
                "vision_loaded": bool(vision and projector_path),
                "projector_path": projector_path if vision else "",
                "context_size": int(settings.get("load", {}).get("ctx_size") or 0),
            }
            return {"success": True, "status": self.status(), "skipped_flags": skipped}

    def stop(self) -> dict:
        if self.process is not None:
            try:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
            finally:
                self.process = None
        self.model = None
        self.port = 0
        return {"success": True, "status": self.status()}

    def build_command(self, server_path: str, port: int, model: dict, settings: dict, vision: bool, projector_path: str = "") -> tuple[list[str], list[str]]:
        load = settings.get("load", {})
        command = [server_path, "--host", "127.0.0.1", "--port", str(port), "-m", model["path"]]
        skipped: list[str] = []

        def add(flag: str, value=None, enabled: bool = True):
            if not enabled:
                return
            if flag.startswith("--") and self.supported_flags and flag not in self.supported_flags:
                skipped.append(flag)
                return
            command.append(flag)
            if value is not None and value != "":
                command.append(str(value))

        add("--ctx-size", int(load.get("ctx_size") or 8192))
        add("--n-gpu-layers", int(load.get("gpu_layers") if load.get("gpu_layers") != "" else -1))
        add("--batch-size", int(load.get("batch") or 1024))
        add("--ubatch-size", int(load.get("ubatch") or 512))
        add("--threads", int(load.get("threads") or 0), enabled=int(load.get("threads") or 0) > 0)
        add("--threads-batch", int(load.get("threads_batch") or 0), enabled=int(load.get("threads_batch") or 0) > 0)
        add("--split-mode", load.get("split_mode") or "layer")
        add("--main-gpu", int(load.get("main_gpu") or 0))
        add("--tensor-split", load.get("tensor_split", "").strip(), enabled=bool(load.get("tensor_split", "").strip()))
        add("--flash-attn", self._on_off_auto(load.get("flash_attn", True)))
        add("--no-mmap", enabled=not bool(load.get("mmap", True)))
        add("--mlock", enabled=bool(load.get("mlock")))
        add("--cache-prompt", enabled=bool(load.get("cache_prompt", True)))
        add("--no-warmup", enabled=bool(load.get("no_warmup")))
        add("--cache-type-k", load.get("type_k", "").strip(), enabled=bool(load.get("type_k", "").strip()))
        add("--cache-type-v", load.get("type_v", "").strip(), enabled=bool(load.get("type_v", "").strip()))
        add("--chat-template", load.get("chat_template", "").strip(), enabled=bool(load.get("chat_template", "").strip()))
        add("--chat-template-kwargs", load.get("chat_template_kwargs", "").strip(), enabled=bool(load.get("chat_template_kwargs", "").strip()))
        add("--reasoning", load.get("reasoning", "auto"))
        add("--reasoning-budget", int(load.get("reasoning_budget") if load.get("reasoning_budget") != "" else -1))
        add("--reasoning-format", load.get("reasoning_format", "auto"), enabled=(load.get("reasoning_format", "auto") != "auto"))
        if vision and projector_path:
            add("--mmproj", projector_path)
            add("--no-mmproj-offload", enabled=not bool(load.get("mmproj_offload", True)))
        for flag in self._parse_extra_flags(load.get("extra_flags", "")):
            if flag.startswith("--") and self.supported_flags and flag not in self.supported_flags:
                skipped.append(flag)
                continue
            command.append(flag)
        return command, skipped

    def chat_stream(self, messages: list[dict], generation: dict, include_reasoning: bool = False) -> Iterable[str]:
        payload = {
            "model": "local",
            "messages": messages,
            "stream": True,
            "max_tokens": int(generation.get("max_tokens") or 1600),
            "temperature": float(generation.get("temperature") or 0.7),
            "top_p": float(generation.get("top_p") or 0.95),
            "top_k": int(generation.get("top_k") or 40),
            "min_p": float(generation.get("min_p") or 0.05),
            "typical_p": float(generation.get("typical_p") or 1.0),
            "repeat_penalty": float(generation.get("repeat_penalty") or 1.1),
            "presence_penalty": float(generation.get("presence_penalty") or 0.0),
            "frequency_penalty": float(generation.get("frequency_penalty") or 0.0),
            "mirostat": int(generation.get("mirostat") or 0),
            "mirostat_tau": float(generation.get("mirostat_tau") or 5.0),
            "mirostat_eta": float(generation.get("mirostat_eta") or 0.1),
        }
        stop = [item.strip() for item in str(generation.get("stop") or "").splitlines() if item.strip()]
        if stop:
            payload["stop"] = stop
        url = f"http://127.0.0.1:{self.port}/v1/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=900) as response:
            for data in self._iter_sse_data(response):
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    text = self._extract_stream_text(chunk, include_reasoning=include_reasoning)
                    if text:
                        yield text
                except Exception:
                    continue

    @staticmethod
    def _iter_sse_data(response) -> Iterable[str]:
        if hasattr(response, "read"):
            buffer = ""
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            while True:
                byte = response.read(1)
                if not byte:
                    break
                buffer += decoder.decode(byte) if isinstance(byte, bytes) else str(byte)
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line.startswith("data:"):
                        yield line[5:].strip()
            buffer += decoder.decode(b"", final=True)
            tail = buffer.strip()
            if tail.startswith("data:"):
                yield tail[5:].strip()
            return
        for raw in response:
            line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else str(raw).strip()
            if line.startswith("data:"):
                yield line[5:].strip()

    @staticmethod
    def _extract_stream_text(chunk: dict, include_reasoning: bool = False) -> str:
        choices = chunk.get("choices") or []
        if not choices:
            return str(chunk.get("content") or chunk.get("response") or "")
        choice = choices[0] or {}
        delta = choice.get("delta") or {}
        text = delta.get("content") or choice.get("text") or ""
        if include_reasoning:
            reasoning = delta.get("reasoning_content") or delta.get("reasoning") or choice.get("reasoning_content") or ""
            if reasoning:
                text = reasoning if not text else f"{reasoning}{text}"
        return text

    def count_tokens(self, text: str) -> int | None:
        if not self.port or not text:
            return None
        url = f"http://127.0.0.1:{self.port}/tokenize"
        payload = {"content": text}
        try:
            request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
            tokens = data.get("tokens")
            return len(tokens) if isinstance(tokens, list) else None
        except Exception:
            return None

    def _wait_until_ready(self, timeout: int) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process and self.process.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=1) as response:
                    if response.status < 500:
                        return True
            except Exception:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/v1/models", timeout=1) as response:
                        if response.status < 500:
                            return True
                except Exception:
                    time.sleep(0.4)
        return False

    def _collect_output_tail(self) -> str:
        if not self.process or not self.process.stdout:
            return ""
        lines = []
        try:
            while True:
                line = self.process.stdout.readline()
                if not line:
                    break
                lines.append(line.rstrip())
                if len(lines) > 40:
                    lines = lines[-40:]
        except Exception:
            pass
        return "\n".join(lines[-20:])

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _parse_extra_flags(raw: str) -> list[str]:
        blocked = {"--host", "--port", "-m", "--model", "--mmproj"}
        tokens = re.findall(r'"[^"]+"|\S+', raw or "")
        cleaned = [token.strip('"') for token in tokens]
        safe = []
        skip_next = False
        for token in cleaned:
            if skip_next:
                skip_next = False
                continue
            if token in blocked:
                skip_next = True
                continue
            if token.startswith(";") or token in {"&&", "||", "|", ">", ">>", "<"}:
                continue
            safe.append(token)
        return safe

    @staticmethod
    def _on_off_auto(value) -> str:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"on", "off", "auto"}:
                return lowered
            if lowered in {"true", "1", "yes"}:
                return "on"
            if lowered in {"false", "0", "no"}:
                return "off"
        if value is None:
            return "auto"
        return "on" if bool(value) else "off"


def sanitize_hidden_output(text: str) -> str:
    cleaned = text or ""
    for pattern in THINK_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    return cleaned


def normalize_output_text(text: str) -> str:
    cleaned = text or ""
    for bad, good in MOJIBAKE_REPLACEMENTS.items():
        cleaned = cleaned.replace(bad, good)
    cleaned = re.sub(r"\ufffd{2,}", "-", cleaned)
    return cleaned
