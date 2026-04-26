"""Context manager — decides which files to include in the LLM context."""

import os
from pathlib import Path
from typing import Optional
from agent.memory import ProjectMemory


# Max files to include in context
MAX_CONTEXT_FILES = 10
# Max characters per file in context
MAX_FILE_CHARS = 3000
# Extensions considered as code/text
TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".scss",
    ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".env",
    ".md", ".txt", ".rst", ".csv",
    ".sh", ".bash", ".ps1", ".bat", ".cmd",
    ".sql", ".graphql",
    ".xml", ".svg",
    ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".go", ".rs", ".rb",
    ".php", ".swift", ".kt", ".scala", ".r",
    ".dockerfile", ".gitignore", ".editorconfig",
}
# Skip these directories
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".agent_memory", ".venv",
             "venv", ".env", "dist", "build", ".next", ".cache", "coverage"}


class ContextManager:
    """Manages which files get included in the LLM context window."""

    def __init__(self, workspace: str, memory: ProjectMemory):
        self.workspace = Path(workspace).resolve()
        self.memory = memory

    def scan_project(self) -> list[dict]:
        """Scan the workspace and return a list of all text files."""
        files = []
        if not self.workspace.exists():
            return files

        for root, dirs, filenames in os.walk(self.workspace):
            # Prune directories
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in filenames:
                fpath = Path(root) / fname
                if fpath.suffix.lower() in TEXT_EXTENSIONS or fpath.name in {
                    "Makefile", "Dockerfile", "Procfile", "Gemfile", "Rakefile",
                    ".gitignore", ".env.example", "requirements.txt", "package.json"
                }:
                    try:
                        rel = str(fpath.relative_to(self.workspace)).replace("\\", "/")
                        size = fpath.stat().st_size
                        files.append({"path": rel, "size": size})
                    except (OSError, ValueError):
                        continue
        return files

    def get_relevant_files(self, query: str, max_files: int = MAX_CONTEXT_FILES) -> list[dict]:
        """Select the most relevant files for a given query/task."""
        all_files = self.scan_project()
        if not all_files:
            return []

        scored = []
        query_lower = query.lower()
        query_words = set(query_lower.split())

        for finfo in all_files:
            path = finfo["path"]
            score = self._score_file(path, query_lower, query_words)
            scored.append((score, finfo))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        # Take top N files that have any relevance
        results = []
        for score, finfo in scored[:max_files]:
            if score <= 0:
                break
            # Read file content (truncated)
            content = self._read_file_truncated(finfo["path"])
            if content is not None:
                results.append({
                    "path": finfo["path"],
                    "content": content,
                    "score": score
                })

        return results

    def _score_file(self, path: str, query_lower: str, query_words: set) -> float:
        """Score a file's relevance to the query."""
        score = 0.0
        path_lower = path.lower()
        path_parts = set(Path(path_lower).parts)
        filename = Path(path_lower).stem

        # Direct filename mention in query
        if filename in query_lower:
            score += 10.0

        # Extension mentioned (e.g., "python" -> .py)
        ext_map = {
            "python": ".py", "javascript": ".js", "typescript": ".ts",
            "html": ".html", "css": ".css", "react": ".jsx",
            "flask": ".py", "django": ".py", "fastapi": ".py",
            "database": ".sql", "docker": "dockerfile",
        }
        for keyword, ext in ext_map.items():
            if keyword in query_lower and ext in path_lower:
                score += 3.0

        # Word overlap between query and path
        for word in query_words:
            if len(word) > 2 and word in path_lower:
                score += 2.0

        # Keyword-based relevance
        relevance_keywords = {
            "test": ["test", "spec", "_test", "test_"],
            "config": ["config", "settings", "env", ".cfg", ".ini", ".toml"],
            "route": ["route", "view", "controller", "endpoint", "api"],
            "model": ["model", "schema", "entity"],
            "auth": ["auth", "login", "user", "permission", "jwt", "session"],
            "database": ["db", "database", "migration", "model", "sql"],
            "style": ["style", "css", "theme", "layout"],
            "component": ["component", "widget", "element"],
        }
        for topic, keywords in relevance_keywords.items():
            if topic in query_lower:
                for kw in keywords:
                    if kw in path_lower:
                        score += 2.0

        # Boost important files
        important_files = {
            "readme.md": 1.0, "package.json": 2.0, "requirements.txt": 2.0,
            "setup.py": 1.5, "pyproject.toml": 1.5, "app.py": 3.0,
            "main.py": 3.0, "index.py": 2.0, "index.js": 2.0, "index.ts": 2.0,
            "index.html": 2.0, "manage.py": 1.5, "server.py": 2.5,
        }
        basename = Path(path_lower).name
        if basename in important_files:
            score += important_files[basename]

        # Check memory for registered files with purposes
        file_info = self.memory.get_file_info(path)
        if file_info and file_info.get("purpose"):
            purpose_lower = file_info["purpose"].lower()
            for word in query_words:
                if len(word) > 2 and word in purpose_lower:
                    score += 3.0

        # Small penalty for deeply nested files
        depth = len(Path(path).parts)
        if depth > 3:
            score -= (depth - 3) * 0.5

        return score

    def _read_file_truncated(self, rel_path: str, max_chars: int = MAX_FILE_CHARS) -> Optional[str]:
        """Read a file, truncating if necessary."""
        try:
            full_path = self.workspace / rel_path
            content = full_path.read_text(encoding="utf-8", errors="replace")
            if len(content) > max_chars:
                # Keep first part and last part
                half = max_chars // 2
                content = content[:half] + f"\n\n... (truncated {len(content) - max_chars} chars) ...\n\n" + content[-half:]
            return content
        except (OSError, UnicodeDecodeError):
            return None

    def build_context_block(self, query: str) -> str:
        """Build the file context block to include in the prompt."""
        files = self.get_relevant_files(query)
        if not files:
            return ""

        parts = ["## Relevant Project Files\n"]
        for f in files:
            parts.append(f"### `{f['path']}`")
            parts.append(f"```\n{f['content']}\n```\n")

        return "\n".join(parts)

    def update_after_write(self, path: str, content: str):
        """Update memory after a file is written."""
        # Auto-detect purpose from content/path
        purpose = self._guess_purpose(path, content)
        self.memory.register_file(path, purpose=purpose)

    def _guess_purpose(self, path: str, content: str) -> str:
        """Try to guess a file's purpose from its path and content."""
        path_lower = path.lower()
        basename = Path(path_lower).name

        purpose_map = {
            "requirements.txt": "Python dependencies",
            "package.json": "Node.js project config and dependencies",
            "setup.py": "Python package setup",
            "pyproject.toml": "Python project configuration",
            "dockerfile": "Docker container definition",
            "docker-compose.yml": "Docker Compose services",
            ".gitignore": "Git ignore rules",
            "readme.md": "Project documentation",
            "makefile": "Build automation",
        }

        if basename in purpose_map:
            return purpose_map[basename]

        # Detect from path patterns
        if "test" in path_lower:
            return "Test file"
        if "migration" in path_lower:
            return "Database migration"
        if "config" in path_lower or "settings" in path_lower:
            return "Configuration"

        # Detect from content (first few lines)
        first_lines = content[:500].lower()
        if "from flask" in first_lines or "import flask" in first_lines:
            return "Flask application"
        if "from fastapi" in first_lines or "import fastapi" in first_lines:
            return "FastAPI application"
        if "def test_" in first_lines or "class Test" in first_lines:
            return "Test file"

        return ""
