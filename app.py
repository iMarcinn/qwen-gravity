"""Qwen Gravity — Flask backend for the agentic coding environment."""

import json
import os
import requests as http_requests
import sys
import webbrowser
from threading import Timer
from flask import Flask, Response, request, jsonify, send_from_directory
from agent.core import AgentLoop
from agent.uploads import UploadManager

# Application paths for PyInstaller compatibility
if getattr(sys, 'frozen', False):
    # Running as compiled executable
    app_base_dir = sys._MEIPASS
    project_base_dir = os.path.dirname(sys.executable)
else:
    # Running dynamically from code
    app_base_dir = os.path.dirname(os.path.abspath(__file__))
    project_base_dir = app_base_dir

# Configuration
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", os.path.join(project_base_dir, "workspace"))
MODEL_NAME = os.environ.get("MODEL_NAME", "qwen2.5-coder:7b")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# Ensure workspace exists
os.makedirs(WORKSPACE_DIR, exist_ok=True)

# Stop flag for generation
_stop_flag = False

# Initialize Flask
app = Flask(__name__, static_folder=os.path.join(app_base_dir, "static"), static_url_path="/static")
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

@app.after_request
def add_header(response):
    """Prevent caching of API responses and static files during development."""
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

# Initialize agent
agent = AgentLoop(workspace=WORKSPACE_DIR, model=MODEL_NAME, ollama_url=OLLAMA_URL)


@app.route("/")
def index():
    """Serve the main UI."""
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/status")
def status():
    """Check Ollama connection and model availability."""
    try:
        r = http_requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        model_available = any(MODEL_NAME in m for m in models)
        return jsonify({
            "ollama": True,
            "model_available": model_available,
            "model": MODEL_NAME,
            "workspace": WORKSPACE_DIR,
        })
    except Exception:
        return jsonify({
            "ollama": False,
            "model_available": False,
            "model": MODEL_NAME,
            "workspace": WORKSPACE_DIR,
        })


@app.route("/api/upload", methods=["POST"])
def upload_files():
    """Handle file uploads."""
    if 'files[]' not in request.files:
        return jsonify({"error": "No files provided"}), 400
    
    files = request.files.getlist('files[]')
    if not files:
        return jsonify({"error": "No files provided"}), 400
    
    # Associate with current session
    session_id = agent.memory.current_session_id
    
    upload_manager = UploadManager(agent.memory.memory_dir)
    try:
        manifest = upload_manager.save_upload(files, session_id)
        return jsonify(manifest)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def chat():
    """Handle a chat message. Returns SSE stream of agent events."""
    data = request.get_json()
    if not data or ("message" not in data and "attachments" not in data):
        return jsonify({"error": "Missing 'message' or 'attachments' field"}), 400

    user_message = data.get("message", "").strip()
    attachments = data.get("attachments", [])
    
    if not user_message and not attachments:
        return jsonify({"error": "Empty message"}), 400

    def generate():
        global _stop_flag
        _stop_flag = False
        try:
            for event in agent.run(user_message, attachments=attachments):
                if _stop_flag:
                    yield f"event: done\ndata: \"\"\n\n"
                    return
                event_type = event.get("type", "text")
                event_data = event.get("data", "")
                # SSE format
                yield f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps(str(e))}\n\n"
            yield f"event: done\ndata: \"\"\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


@app.route("/api/stop", methods=["POST"])
def stop_generation():
    """Signal the agent to stop generating."""
    global _stop_flag
    _stop_flag = True
    return jsonify({"status": "ok"})


@app.route("/api/history")
def history():
    """Get conversation history."""
    return jsonify({"messages": agent.get_conversation_history()})


@app.route("/api/reset", methods=["POST"])
def reset():
    """Start a new chat session (old one is preserved)."""
    agent.clear_conversation()
    return jsonify({"status": "ok", "session_id": agent.memory.current_session_id})


@app.route("/api/sessions")
def list_sessions():
    """List all chat sessions."""
    sessions = agent.memory.list_sessions()
    current = agent.memory.current_session_id
    return jsonify({"sessions": sessions, "current": current})


@app.route("/api/sessions/switch", methods=["POST"])
def switch_session():
    """Switch to a different chat session."""
    data = request.get_json()
    session_id = data.get("session_id", "")
    if agent.memory.switch_session(session_id):
        return jsonify({"status": "ok", "session_id": session_id})
    return jsonify({"error": "Session not found"}), 404


@app.route("/api/sessions/delete", methods=["POST"])
def delete_session():
    """Delete a chat session."""
    data = request.get_json()
    session_id = data.get("session_id", "")
    if agent.memory.delete_session(session_id):
        return jsonify({"status": "ok", "current": agent.memory.current_session_id})
    return jsonify({"error": "Failed to delete session"}), 400


@app.route("/api/sessions/rename", methods=["POST"])
def rename_session():
    """Rename a chat session."""
    data = request.get_json()
    session_id = data.get("session_id", "")
    new_title = data.get("title", "").strip()
    if not new_title:
        return jsonify({"error": "Title cannot be empty"}), 400
    print(f"[RENAME DEBUG] session_id={session_id!r}, sessions_dir={agent.memory.sessions_dir}")
    print(f"[RENAME DEBUG] files in sessions_dir: {list(agent.memory.sessions_dir.glob('*.json'))}")
    session_path = agent.memory._session_path(session_id)
    print(f"[RENAME DEBUG] looking for: {session_path}, exists={session_path.exists()}")
    if agent.memory.rename_session(session_id, new_title):
        return jsonify({"status": "ok"})
    return jsonify({"error": "Session not found"}), 404


@app.route("/api/project")
def project_info():
    """Get project file tree and memory summary."""
    files = agent.get_project_files()
    memory = agent.get_memory_summary()
    return jsonify({
        "workspace": WORKSPACE_DIR,
        "files": files,
        "memory": memory
    })


@app.route("/api/models")
def get_models():
    """Fetch available models from Ollama."""
    try:
        r = http_requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        r.raise_for_status()
        ollama_models = r.json().get("models", [])
        
        models = []
        for m in ollama_models:
            models.append({
                "name": m["name"],
                "size": m.get("size", 0),
                "modified_at": m.get("modified_at", "")
            })
        
        # Sort by name
        models.sort(key=lambda x: x["name"])
        
        return jsonify({
            "models": models,
            "current": MODEL_NAME
        })
    except Exception:
        return jsonify({
            "models": [],
            "current": MODEL_NAME,
            "error": "Cannot connect to Ollama"
        })


@app.route("/api/config", methods=["GET", "POST"])
def config():
    """Get or update configuration."""
    global agent, WORKSPACE_DIR, MODEL_NAME  # noqa

    if request.method == "GET":
        return jsonify({
            "workspace": WORKSPACE_DIR,
            "model": MODEL_NAME,
            "ollama_url": OLLAMA_URL,
        })

    data = request.get_json()
    model_available = True
    
    if "workspace" in data:
        WORKSPACE_DIR = data["workspace"]
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        
    if "model" in data:
        new_model = data["model"]
        MODEL_NAME = new_model
        
        # Optional validation
        try:
            r = http_requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
            if r.status_code == 200:
                ollama_models = [m["name"] for m in r.json().get("models", [])]
                model_available = any(new_model in m for m in ollama_models)
        except Exception:
            pass # Soft validation: if Ollama unreachable, assume OK but warn in response

    # Reinitialize agent with new config
    agent = AgentLoop(workspace=WORKSPACE_DIR, model=MODEL_NAME, ollama_url=OLLAMA_URL)
    return jsonify({
        "status": "ok", 
        "workspace": WORKSPACE_DIR, 
        "model": MODEL_NAME,
        "model_available": model_available
    })


@app.route("/api/browse")
def browse_directory():
    """Browse the file system. Returns directories and files at a given path."""
    browse_path = request.args.get("path", "")

    # If no path given or asking for root, return available drives (Windows) or /
    if not browse_path:
        if os.name == "nt":
            # Windows: list available drive letters
            import string
            drives = []
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    drives.append({
                        "name": drive,
                        "path": drive,
                        "is_dir": True,
                    })
            return jsonify({"path": "", "parent": None, "items": drives})
        else:
            browse_path = "/"

    browse_path = os.path.abspath(browse_path)

    if not os.path.isdir(browse_path):
        return jsonify({"error": "Not a directory"}), 400

    parent = os.path.dirname(browse_path)
    if parent == browse_path:
        parent = ""  # at root

    items = []
    try:
        entries = sorted(os.listdir(browse_path), key=lambda x: (not os.path.isdir(os.path.join(browse_path, x)), x.lower()))
        for entry in entries:
            # Skip Windows system entries only (not regular dotfiles)
            if entry.startswith("$"):
                continue
            full = os.path.join(browse_path, entry)
            is_dir = os.path.isdir(full)
            items.append({
                "name": entry,
                "path": full,
                "is_dir": is_dir,
            })
    except PermissionError:
        return jsonify({"path": browse_path, "parent": parent, "items": [], "error": "Permission denied"})

    return jsonify({
        "path": browse_path,
        "parent": parent,
        "items": items,
    })


@app.route("/api/open-folder", methods=["POST"])
def open_folder():
    """Open a folder as the active workspace."""
    global agent, WORKSPACE_DIR  # noqa

    data = request.get_json()
    folder_path = data.get("path", "").strip()
    if not folder_path or not os.path.isdir(folder_path):
        return jsonify({"error": "Invalid directory path"}), 400

    WORKSPACE_DIR = os.path.abspath(folder_path)
    os.makedirs(WORKSPACE_DIR, exist_ok=True)

    # Reinitialize agent with the new workspace
    agent = AgentLoop(workspace=WORKSPACE_DIR, model=MODEL_NAME, ollama_url=OLLAMA_URL)
    return jsonify({"status": "ok", "workspace": WORKSPACE_DIR})


if __name__ == "__main__":
    print(f"""
================================================
     Qwen Gravity
     Local Agentic Coding Environment
================================================
  Workspace : {WORKSPACE_DIR}
  Model     : {MODEL_NAME}
  Ollama    : {OLLAMA_URL}
================================================
  Open http://localhost:5000 in your browser
================================================
""")
    # Automatically open the browser 1 second after starting
    Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
