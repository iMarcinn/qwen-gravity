"""Project memory — persists codebase knowledge between sessions."""

import json
import time
import uuid
from pathlib import Path
from typing import Optional


class ProjectMemory:
    """Manages persistent project memory stored as JSON files."""

    def __init__(self, memory_dir: str):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # Sessions directory
        self.sessions_dir = self.memory_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        # Memory stores (project-level, shared across sessions)
        self.file_registry: dict = {}      # path -> {purpose, summary, last_modified}
        self.decisions: list = []           # [{decision, context, timestamp}]

        # Current chat session
        self.current_session_id: str = ""
        self.conversation_history: list = []  # [{role, content, timestamp}]

        self.load()
        self._ensure_session()

    # --- Persistence ---

    def _path(self, name: str) -> Path:
        return self.memory_dir / name

    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def load(self):
        """Load project-level memory from disk."""
        self.file_registry = self._load_json("file_registry.json", {})
        self.decisions = self._load_json("decisions.json", [])
        self.current_session_id = self._load_json("current_session.json", {}).get("id", "")

    def save(self):
        """Save project-level memory to disk."""
        self._save_json("file_registry.json", self.file_registry)
        self._save_json("decisions.json", self.decisions)
        self._save_json("current_session.json", {"id": self.current_session_id})

    def _load_json(self, filename: str, default):
        path = self._path(filename)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return default
        return default

    def _save_json(self, filename: str, data):
        path = self._path(filename)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- Session Management ---

    def _ensure_session(self):
        """Make sure we have an active session. Create one if none exists."""
        if self.current_session_id:
            session_path = self._session_path(self.current_session_id)
            if session_path.exists():
                session_data = json.loads(session_path.read_text(encoding="utf-8"))
                self.conversation_history = session_data.get("messages", [])
                return

        # Migrate: if there's a legacy conversation_history.json, import it
        legacy = self._path("conversation_history.json")
        legacy_messages = []
        if legacy.exists():
            try:
                legacy_messages = json.loads(legacy.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        # Create a new session
        self.new_session()

        # If legacy messages existed, put them in this session
        if legacy_messages:
            self.conversation_history = legacy_messages
            self._save_session()
            # Remove legacy file
            try:
                legacy.unlink()
            except OSError:
                pass

    def new_session(self) -> str:
        """Create a new empty chat session and switch to it."""
        session_id = str(uuid.uuid4())[:8]
        self.current_session_id = session_id
        self.conversation_history = []

        session_data = {
            "id": session_id,
            "title": "New Chat",
            "created_at": time.time(),
            "updated_at": time.time(),
            "messages": [],
        }
        self._session_path(session_id).write_text(
            json.dumps(session_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self.save()  # Save current_session.json
        return session_id

    def switch_session(self, session_id: str) -> bool:
        """Switch to an existing session."""
        session_path = self._session_path(session_id)
        if not session_path.exists():
            return False
        session_data = json.loads(session_path.read_text(encoding="utf-8"))
        self.current_session_id = session_id
        self.conversation_history = session_data.get("messages", [])
        self.save()
        return True

    def delete_session(self, session_id: str) -> bool:
        """Delete a chat session."""
        session_path = self._session_path(session_id)
        
        # Track uploads to delete
        attachments_to_delete = []
        if session_path.exists():
            try:
                # Read session data to find associated uploads
                content = session_path.read_text(encoding="utf-8")
                session_data = json.loads(content)
                for msg in session_data.get("messages", []):
                    if "attachments" in msg:
                        attachments_to_delete.extend(msg["attachments"])
                
                session_path.unlink()
            except (OSError, json.JSONDecodeError):
                if session_path.exists():
                    try:
                        session_path.unlink()
                    except OSError:
                        return False

        # Cleanup associated uploads
        if attachments_to_delete:
            import shutil
            uploads_dir = self.memory_dir / "uploads"
            for upload_id in set(attachments_to_delete):
                upload_path = uploads_dir / upload_id
                if upload_path.exists():
                    try:
                        shutil.rmtree(upload_path)
                    except Exception:
                        pass

        # If we just deleted the active session, create a new one
        if session_id == self.current_session_id:
            self.new_session()
        return True

    def rename_session(self, session_id: str, new_title: str) -> bool:
        """Rename a chat session."""
        session_path = self._session_path(session_id)
        if not session_path.exists():
            return False
        try:
            data = json.loads(session_path.read_text(encoding="utf-8"))
            data["title"] = new_title
            data["custom_title"] = True
            session_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            return True
        except (json.JSONDecodeError, OSError):
            return False

    def list_sessions(self) -> list:
        """List all chat sessions, newest first."""
        sessions = []
        for f in self.sessions_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sessions.append({
                    "id": data.get("id", f.stem),
                    "title": data.get("title", "Untitled"),
                    "created_at": data.get("created_at", 0),
                    "updated_at": data.get("updated_at", 0),
                    "message_count": len(data.get("messages", [])),
                })
            except (json.JSONDecodeError, OSError):
                continue
        sessions.sort(key=lambda x: x["updated_at"], reverse=True)
        return sessions

    def _save_session(self):
        """Save the current session's messages to its file."""
        if not self.current_session_id:
            return
        session_path = self._session_path(self.current_session_id)

        # Load existing data to preserve created_at and custom titles
        created_at = time.time()
        existing_title = None
        custom_title = False
        if session_path.exists():
            try:
                existing = json.loads(session_path.read_text(encoding="utf-8"))
                created_at = existing.get("created_at", created_at)
                existing_title = existing.get("title", None)
                custom_title = existing.get("custom_title", False)
            except (json.JSONDecodeError, OSError):
                pass

        # Auto-generate title from first user message ONLY if not custom-renamed
        if custom_title and existing_title:
            title = existing_title
        else:
            title = "New Chat"
            for msg in self.conversation_history:
                if msg["role"] == "user":
                    title = msg["content"][:50]
                    if len(msg["content"]) > 50:
                        title += "..."
                    break

        session_data = {
            "id": self.current_session_id,
            "title": title,
            "created_at": created_at,
            "updated_at": time.time(),
            "messages": self.conversation_history,
        }
        session_path.write_text(
            json.dumps(session_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # --- File Registry ---

    def register_file(self, path: str, purpose: str = "", summary: str = ""):
        """Register or update a file in the project memory."""
        self.file_registry[path] = {
            "purpose": purpose,
            "summary": summary,
            "last_modified": time.time()
        }
        self.save()

    def remove_file(self, path: str):
        """Remove a file from the registry."""
        self.file_registry.pop(path, None)
        self.save()

    def get_file_info(self, path: str) -> Optional[dict]:
        """Get stored info about a file."""
        return self.file_registry.get(path)

    # --- Decisions ---

    def add_decision(self, decision: str, context: str = ""):
        """Record an architectural or coding decision."""
        self.decisions.append({
            "decision": decision,
            "context": context,
            "timestamp": time.time()
        })
        # Keep only last 50 decisions
        if len(self.decisions) > 50:
            self.decisions = self.decisions[-50:]
        self.save()

    # --- Conversation ---

    def add_message(self, role: str, content: str, attachments: list = None):
        """Add a message to conversation history."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": time.time()
        }
        if attachments:
            msg["attachments"] = attachments
            
        self.conversation_history.append(msg)
        self._save_session()

    def get_recent_messages(self, limit: int = 20) -> list:
        """Get recent conversation messages for context."""
        return self.conversation_history[-limit:]

    def clear_conversation(self):
        """Start a new chat session (old one is preserved)."""
        self.new_session()

    # --- Context Building ---

    def get_project_summary(self) -> str:
        """Build a summary of the project state for the system prompt."""
        parts = []

        if self.file_registry:
            parts.append("### Project Files")
            for path, info in sorted(self.file_registry.items()):
                purpose = info.get("purpose", "")
                if purpose:
                    parts.append(f"- `{path}`: {purpose}")
                else:
                    parts.append(f"- `{path}`")

        if self.decisions:
            parts.append("\n### Key Decisions")
            for d in self.decisions[-10:]:  # Show last 10 decisions
                parts.append(f"- {d['decision']}")

        return "\n".join(parts) if parts else ""

    def get_file_list(self) -> list:
        """Get list of all registered file paths."""
        return list(self.file_registry.keys())

