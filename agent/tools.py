"""Tool implementations for the agent."""

import os
import re
import subprocess
import platform
from pathlib import Path


# Max file size to read (500KB)
MAX_READ_SIZE = 500_000
# Max lines to return from a file
MAX_READ_LINES = 500
# Command timeout in seconds
COMMAND_TIMEOUT = 60
# Max directory listing depth
MAX_DIR_DEPTH = 4
# Max search results
MAX_SEARCH_RESULTS = 50


def _validate_path(path: str, workspace: str) -> Path:
    """Validate and resolve a path, ensuring it stays within the workspace."""
    workspace_path = Path(workspace).resolve()
    # Handle absolute paths by making them relative
    if os.path.isabs(path):
        path = os.path.relpath(path, workspace_path)
    resolved = (workspace_path / path).resolve()
    # Security: ensure the path is within the workspace
    if not str(resolved).startswith(str(workspace_path)):
        raise ValueError(f"Path '{path}' escapes the project workspace")
    return resolved


def read_file(path: str, workspace: str) -> dict:
    """Read the contents of a file."""
    try:
        resolved = _validate_path(path, workspace)
        if not resolved.exists():
            return {"success": False, "error": f"File not found: {path}"}
        if not resolved.is_file():
            return {"success": False, "error": f"Not a file: {path}"}
        size = resolved.stat().st_size
        if size > MAX_READ_SIZE:
            return {"success": False, "error": f"File too large ({size} bytes). Max: {MAX_READ_SIZE} bytes"}

        content = resolved.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        if len(lines) > MAX_READ_LINES:
            # Truncate: show first 200 and last 100 lines
            truncated = lines[:200] + [f"\n... ({len(lines) - 300} lines omitted) ...\n"] + lines[-100:]
            content = "\n".join(truncated)
            return {
                "success": True,
                "output": content,
                "note": f"File truncated from {len(lines)} to ~300 lines. Use search_in_file to find specific content."
            }
        return {"success": True, "output": content}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Error reading file: {e}"}


def write_file(path: str, content: str, workspace: str) -> dict:
    """Write content to a file, creating directories as needed."""
    try:
        resolved = _validate_path(path, workspace)
        # Create parent directories
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        size = resolved.stat().st_size
        return {
            "success": True,
            "output": f"Successfully wrote {size} bytes to {path}"
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Error writing file: {e}"}


def run_command(command: str, workspace: str) -> dict:
    """Execute a shell command in the workspace directory."""
    try:
        is_windows = platform.system() == "Windows"
        if is_windows:
            shell_cmd = ["powershell", "-NoProfile", "-Command", command]
        else:
            shell_cmd = ["/bin/bash", "-c", command]

        result = subprocess.run(
            shell_cmd,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"}
        )

        output_parts = []
        if result.stdout.strip():
            output_parts.append(result.stdout.strip())
        if result.stderr.strip():
            output_parts.append(f"[STDERR]\n{result.stderr.strip()}")

        output = "\n".join(output_parts) or "(no output)"

        # Truncate very long output
        if len(output) > 10000:
            output = output[:5000] + f"\n\n... ({len(output) - 10000} characters omitted) ...\n\n" + output[-5000:]

        if result.returncode == 0:
            return {"success": True, "output": output}
        else:
            return {
                "success": False,
                "output": output,
                "error": f"Command exited with code {result.returncode}"
            }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Command timed out after {COMMAND_TIMEOUT}s"}
    except Exception as e:
        return {"success": False, "error": f"Error running command: {e}"}


def list_directory(path: str, workspace: str) -> dict:
    """List the contents of a directory."""
    try:
        resolved = _validate_path(path, workspace)
        if not resolved.exists():
            return {"success": False, "error": f"Directory not found: {path}"}
        if not resolved.is_dir():
            return {"success": False, "error": f"Not a directory: {path}"}

        workspace_path = Path(workspace).resolve()
        lines = []
        file_count = 0
        dir_count = 0

        def _walk(current: Path, prefix: str, depth: int):
            nonlocal file_count, dir_count
            if depth > MAX_DIR_DEPTH:
                lines.append(f"{prefix}... (max depth reached)")
                return

            try:
                entries = sorted(current.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
            except PermissionError:
                lines.append(f"{prefix}[permission denied]")
                return

            # Skip hidden and common non-essential directories
            skip_dirs = {".git", "__pycache__", "node_modules", ".agent_memory", ".venv", "venv", ".env"}

            for entry in entries:
                rel = entry.relative_to(workspace_path)
                if entry.is_dir():
                    if entry.name in skip_dirs:
                        lines.append(f"{prefix}📁 {entry.name}/ (skipped)")
                        continue
                    dir_count += 1
                    lines.append(f"{prefix}📁 {entry.name}/")
                    _walk(entry, prefix + "  ", depth + 1)
                else:
                    file_count += 1
                    size = entry.stat().st_size
                    if size < 1024:
                        size_str = f"{size}B"
                    elif size < 1024 * 1024:
                        size_str = f"{size / 1024:.1f}KB"
                    else:
                        size_str = f"{size / (1024 * 1024):.1f}MB"
                    lines.append(f"{prefix}📄 {entry.name} ({size_str})")

        _walk(resolved, "", 0)

        header = f"Directory: {path}\n{file_count} files, {dir_count} directories\n"
        return {"success": True, "output": header + "─" * 40 + "\n" + "\n".join(lines)}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Error listing directory: {e}"}


def search_in_file(pattern: str, path: str, workspace: str) -> dict:
    """Search for a pattern in files."""
    try:
        resolved = _validate_path(path, workspace)
        if not resolved.exists():
            return {"success": False, "error": f"Path not found: {path}"}

        workspace_path = Path(workspace).resolve()
        results = []
        skip_dirs = {".git", "__pycache__", "node_modules", ".agent_memory", ".venv", "venv"}
        # Binary file extensions to skip
        binary_exts = {".pyc", ".pyo", ".exe", ".dll", ".so", ".bin", ".jpg", ".png",
                       ".gif", ".ico", ".pdf", ".zip", ".tar", ".gz", ".whl"}

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            # Fall back to literal search
            regex = re.compile(re.escape(pattern), re.IGNORECASE)

        def _search_file(filepath: Path):
            if filepath.suffix.lower() in binary_exts:
                return
            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(content.splitlines(), 1):
                    if regex.search(line):
                        rel_path = filepath.relative_to(workspace_path)
                        results.append(f"{rel_path}:{i}: {line.strip()}")
                        if len(results) >= MAX_SEARCH_RESULTS:
                            return
            except (PermissionError, OSError):
                pass

        if resolved.is_file():
            _search_file(resolved)
        else:
            for root, dirs, files in os.walk(resolved):
                # Skip hidden/non-essential directories
                dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
                for fname in files:
                    _search_file(Path(root) / fname)
                    if len(results) >= MAX_SEARCH_RESULTS:
                        break
                if len(results) >= MAX_SEARCH_RESULTS:
                    break

        if not results:
            return {"success": True, "output": f"No matches found for '{pattern}' in {path}"}

        output = f"Found {len(results)} match(es) for '{pattern}':\n\n" + "\n".join(results)
        if len(results) >= MAX_SEARCH_RESULTS:
            output += f"\n\n(Results capped at {MAX_SEARCH_RESULTS})"
        return {"success": True, "output": output}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Error searching: {e}"}


# Tool dispatcher
TOOL_MAP = {
    "read_file": lambda args, ws: read_file(args["path"], ws),
    "write_file": lambda args, ws: write_file(args["path"], args["content"], ws),
    "run_command": lambda args, ws: run_command(args["command"], ws),
    "list_directory": lambda args, ws: list_directory(args.get("path", "."), ws),
    "search_in_file": lambda args, ws: search_in_file(args["pattern"], args.get("path", "."), ws),
}


def execute_tool(name: str, arguments: dict, workspace: str) -> dict:
    """Execute a tool by name with given arguments."""
    if name not in TOOL_MAP:
        return {"success": False, "error": f"Unknown tool: {name}. Available: {list(TOOL_MAP.keys())}"}
    try:
        return TOOL_MAP[name](arguments, workspace)
    except KeyError as e:
        return {"success": False, "error": f"Missing required argument: {e}"}
    except Exception as e:
        return {"success": False, "error": f"Tool execution error: {e}"}
