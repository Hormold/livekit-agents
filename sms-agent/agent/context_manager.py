from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from livekit.agents.llm import ChatContext


class ContextManager:
    def __init__(self, file_path: Path | str | None = None):
        self.file_path = Path(file_path) if file_path else Path(__file__).parent.parent / "conversations.json"
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.file_path.exists():
            return
        try:
            with open(self.file_path) as f:
                self._data = json.load(f)
        except json.JSONDecodeError:
            self._data = {}

    def _save(self) -> None:
        with open(self.file_path, "w") as f:
            json.dump(self._data, f, indent=2)

    def save(self, phone_number: str, chat_ctx: ChatContext) -> None:
        self._data[phone_number] = {
            "chat_ctx": chat_ctx.to_dict(exclude_function_call=False),
            "updated_at": datetime.now().isoformat(),
        }
        self._save()

    def get(self, phone_number: str) -> ChatContext | None:
        entry = self._data.get(phone_number)
        if not entry or "chat_ctx" not in entry:
            return None
        return ChatContext.from_dict(entry["chat_ctx"])

    def clear(self, phone_number: str) -> None:
        if phone_number in self._data:
            del self._data[phone_number]
            self._save()
