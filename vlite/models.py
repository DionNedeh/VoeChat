from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


VISION_HINTS = ("mmproj", "projector")


@dataclass
class ModelRecord:
    id: str
    name: str
    path: str
    size: int
    projectors: list[dict] = field(default_factory=list)

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "size": self.size,
            "projectors": self.projectors,
            "vision": bool(self.projectors),
        }


class ModelScanner:
    def __init__(self, app_root: Path):
        self.app_root = Path(app_root)

    def smart_folders(self, extra: list[str] | None = None) -> list[str]:
        home = Path.home()
        candidates = [
            home / "Downloads",
            home / "Documents",
            home / ".cache" / "huggingface" / "hub",
            home / ".lmstudio" / "models",
            home / ".ollama" / "models",
            Path("C:/models"),
            self.app_root / "models",
        ]
        for item in extra or []:
            if item:
                candidates.append(Path(item))
        seen: set[str] = set()
        folders: list[str] = []
        for folder in candidates:
            try:
                resolved = str(folder.expanduser().resolve())
            except Exception:
                resolved = str(folder)
            if resolved.lower() not in seen and Path(resolved).exists():
                seen.add(resolved.lower())
                folders.append(resolved)
        return folders

    def scan(self, folders: list[str], max_files: int = 5000) -> list[dict]:
        ggufs: list[Path] = []
        for folder in folders:
            root = Path(folder)
            if not root.exists():
                continue
            try:
                for current, dirs, files in os.walk(root):
                    dirs[:] = [d for d in dirs if d not in {"$Recycle.Bin", "System Volume Information", ".git", "node_modules"}]
                    for name in files:
                        if name.lower().endswith(".gguf"):
                            ggufs.append(Path(current) / name)
                            if len(ggufs) >= max_files:
                                break
                    if len(ggufs) >= max_files:
                        break
            except (OSError, PermissionError):
                continue

        projectors = [p for p in ggufs if self._is_projector(p)]
        models = [p for p in ggufs if not self._is_projector(p)]
        records = [self._record_for(model, projectors) for model in sorted(models, key=lambda p: p.name.lower())]
        return [record.public() for record in records]

    @staticmethod
    def _is_projector(path: Path) -> bool:
        lowered = path.name.lower()
        return any(hint in lowered for hint in VISION_HINTS)

    def _record_for(self, model: Path, projectors: list[Path]) -> ModelRecord:
        linked = []
        model_tokens = self._tokens(model.stem)
        for projector in projectors:
            if self._compatible(model, projector, model_tokens):
                linked.append({
                    "name": projector.name,
                    "path": str(projector),
                    "size": self._size(projector),
                })
        return ModelRecord(
            id=str(model).lower(),
            name=model.stem,
            path=str(model),
            size=self._size(model),
            projectors=linked[:8],
        )

    @staticmethod
    def _compatible(model: Path, projector: Path, model_tokens: set[str]) -> bool:
        if projector.parent == model.parent:
            return True
        try:
            if projector.parent.parent == model.parent or model.parent.parent == projector.parent:
                return True
        except Exception:
            pass
        projector_tokens = ModelScanner._tokens(projector.stem)
        shared = model_tokens.intersection(projector_tokens)
        return len(shared) >= 2

    @staticmethod
    def _tokens(value: str) -> set[str]:
        cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in value)
        return {part for part in cleaned.split() if len(part) > 1 and part not in {"gguf", "q4", "q5", "q6", "q8", "mmproj"}}

    @staticmethod
    def _size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0
