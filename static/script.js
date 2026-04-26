/**
 * Qwen Gravity — Frontend Logic
 * Handles chat interaction, SSE streaming, sidebar, and settings.
 */

// ─── State ───
let isStreaming = false;
let currentAssistantEl = null;
let currentAssistantText = "";
let currentAbortController = null;
let pendingUploads = []; // Array of manifest objects

// ─── Marked.js Configuration ───
document.addEventListener("DOMContentLoaded", () => {
    if (typeof marked !== "undefined") {
        marked.setOptions({
            highlight: function (code, lang) {
                if (typeof hljs !== "undefined" && lang && hljs.getLanguage(lang)) {
                    try { return hljs.highlight(code, { language: lang }).value; } catch (e) {}
                }
                if (typeof hljs !== "undefined") {
                    try { return hljs.highlightAuto(code).value; } catch (e) {}
                }
                return code;
            },
            breaks: true,
            gfm: true,
        });
    }

    loadConfig();
    loadProjectInfo();
    loadHistory();
    loadSessions();
    checkOllamaStatus();

    // Poll Ollama status every 15 seconds
    setInterval(checkOllamaStatus, 15000);

    // Drag and drop
    const dropZone = document.querySelector('main.main');
    if (dropZone) {
        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.classList.add('drag-over');
        });
        dropZone.addEventListener('dragleave', () => {
            dropZone.classList.remove('drag-over');
        });
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('drag-over');
            if (e.dataTransfer.files.length > 0) {
                uploadFiles(e.dataTransfer.files);
            }
        });
    }

    document.getElementById("message-input").focus();
});


// ─── Chat ───

function sendMessage() {
    const input = document.getElementById("message-input");
    const text = input.value.trim();
    if ((!text && pendingUploads.length === 0) || isStreaming) return;

    // Hide welcome screen
    const welcome = document.getElementById("welcome-screen");
    if (welcome) welcome.remove();

    // Collect attachments
    const attachments = pendingUploads.map(m => m.upload_id);

    // Add user message
    addMessage("user", text, pendingUploads);

    // Clear input and state
    input.value = "";
    input.style.height = "auto";
    pendingUploads = [];
    renderAttachmentPreview();

    // Start streaming
    streamAgentResponse(text, attachments);
}

// --- File Uploads ---

async function handleFileUpload(event) {
    const files = event.target.files;
    if (!files || files.length === 0) return;
    
    await uploadFiles(files);
    event.target.value = ""; // Reset for same file re-selection
}

async function uploadFiles(files) {
    const formData = new FormData();
    for (const file of files) {
        formData.append("files[]", file);
    }
    
    try {
        const res = await fetch("/api/upload", {
            method: "POST",
            body: formData
        });
        
        if (!res.ok) throw new Error("Upload failed");
        
        const manifest = await res.json();
        pendingUploads.push(manifest);
        renderAttachmentPreview();
    } catch (e) {
        console.error(e);
        alert("Upload failed: " + e.message);
    }
}

function renderAttachmentPreview() {
    const container = document.getElementById("attachment-preview");
    if (!container) return;
    
    container.innerHTML = "";
    
    if (pendingUploads.length === 0) {
        container.style.display = "none";
        return;
    }
    
    container.style.display = "flex";
    
    pendingUploads.forEach((manifest, mIndex) => {
        manifest.files.forEach((file, fIndex) => {
            const chip = document.createElement("div");
            chip.className = "attachment-chip";
            
            const icon = file.is_image ? "🖼️" : (file.is_text ? "📄" : "📁");
            const size = (file.size / 1024).toFixed(1) + " KB";
            
            chip.innerHTML = `
                <span class="chip-icon">${icon}</span>
                <span class="chip-name" title="${file.name}">${file.name}</span>
                <span class="chip-size">${size}</span>
                <button class="chip-remove" onclick="removeAttachment(${mIndex}, ${fIndex})">✕</button>
            `;
            container.appendChild(chip);
        });
    });
}

function removeAttachment(mIndex, fIndex) {
    const manifest = pendingUploads[mIndex];
    manifest.files.splice(fIndex, 1);
    
    if (manifest.files.length === 0) {
        pendingUploads.splice(mIndex, 1);
    }
    
    renderAttachmentPreview();
}

function sendSuggestion(el) {
    const input = document.getElementById("message-input");
    input.value = el.textContent;
    sendMessage();
}

function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

function autoResize(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
}


// ─── Message Rendering ───

function addMessage(role, content, attachments = null) {
    const messages = document.getElementById("messages");
    const div = document.createElement("div");
    div.className = `message ${role}`;

    const avatarEmoji = role === "user" ? "👤" : "🚀";
    
    let attachmentHtml = "";
    if (attachments && attachments.length > 0) {
        attachmentHtml = `<div class="message-attachments">`;
        attachments.forEach(m => {
            if (m.files) {
                m.files.forEach(f => {
                    const icon = f.is_image ? "🖼️" : (f.is_text ? "📄" : "📁");
                    attachmentHtml += `
                        <div class="attachment-chip compact">
                            <span class="chip-icon">${icon}</span>
                            <span class="chip-name">${f.name}</span>
                        </div>
                    `;
                });
            }
        });
        attachmentHtml += `</div>`;
    }

    div.innerHTML = `
        <div class="message-avatar">${avatarEmoji}</div>
        <div class="message-body">
            <div class="message-content">${renderMarkdown(content)}</div>
            ${attachmentHtml}
            ${role === "assistant" ? `<button class="copy-btn" onclick="copyResponse(this)" title="Copy Response">📋</button>` : ""}
        </div>
    `;

    messages.appendChild(div);
    scrollToBottom();
    return div;
}

function renderMarkdown(text) {
    if (typeof marked !== "undefined") {
        try {
            return marked.parse(text);
        } catch (e) {
            return escapeHtml(text).replace(/\n/g, "<br>");
        }
    }
    return escapeHtml(text).replace(/\n/g, "<br>");
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function scrollToBottom() {
    const messages = document.getElementById("messages");
    requestAnimationFrame(() => {
        messages.scrollTop = messages.scrollHeight;
    });
}

function copyResponse(button) {
    const messageBody = button.closest('.message-body');
    const content = messageBody.querySelector('.message-content').innerText;
    
    navigator.clipboard.writeText(content).then(() => {
        const originalIcon = button.innerText;
        button.innerText = "✅";
        button.classList.add('copied');
        
        setTimeout(() => {
            button.innerText = originalIcon;
            button.classList.remove('copied');
        }, 2000);
    }).catch(err => {
        console.error('Failed to copy: ', err);
    });
}


// ─── SSE Streaming ───

function streamAgentResponse(message, attachments = []) {
    isStreaming = true;
    updateSendButton();

    // Create assistant message container
    const messages = document.getElementById("messages");
    currentAssistantEl = document.createElement("div");
    currentAssistantEl.className = "message assistant";
    currentAssistantEl.innerHTML = `
        <div class="message-avatar">🚀</div>
        <div class="message-body">
            <div class="message-content">
                <div class="thinking-indicator">
                    <div class="thinking-dots"><span></span><span></span><span></span></div>
                    <span>Thinking...</span>
                </div>
            </div>
        </div>
    `;
    messages.appendChild(currentAssistantEl);
    scrollToBottom();

    currentAssistantText = "";
    let toolCallBlocks = [];
    let thinkingCleared = false;

    currentAbortController = new AbortController();

    fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, attachments }),
        signal: currentAbortController.signal,
    }).then(response => {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        function read() {
            reader.read().then(({ done, value }) => {
                if (done) {
                    finishStreaming(toolCallBlocks);
                    return;
                }

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split("\n");
                buffer = lines.pop(); // keep incomplete line

                let eventType = null;
                for (const line of lines) {
                    if (line.startsWith("event: ")) {
                        eventType = line.slice(7).trim();
                    } else if (line.startsWith("data: ") && eventType) {
                        let data;
                        try {
                            data = JSON.parse(line.slice(6));
                        } catch (e) {
                            data = line.slice(6);
                        }
                        handleSSEEvent(eventType, data, toolCallBlocks, thinkingCleared);
                        if (!thinkingCleared && (eventType === "text" || eventType === "tool_call")) {
                            thinkingCleared = true;
                        }
                        eventType = null;
                    }
                }

                read();
            }).catch(err => {
                console.error("Stream error:", err);
                finishStreaming(toolCallBlocks);
            });
        }

        read();
    }).catch(err => {
        console.error("Fetch error:", err);
        const contentEl = currentAssistantEl.querySelector(".message-content");
        contentEl.innerHTML = `<span style="color: var(--accent-red);">⚠️ Connection error: ${escapeHtml(err.message)}</span>`;
        isStreaming = false;
        updateSendButton();
    });
}

function handleSSEEvent(type, data, toolCallBlocks, thinkingCleared) {
    const contentEl = currentAssistantEl.querySelector(".message-content");

    switch (type) {
        case "thinking":
            // Update thinking text
            if (!thinkingCleared) {
                const thinkSpan = contentEl.querySelector(".thinking-indicator span:last-child");
                if (thinkSpan) thinkSpan.textContent = data;
            }
            break;

        case "text":
            // Append streamed text
            currentAssistantText += data;
            // Strip tool_call blocks from displayed text
            const displayText = currentAssistantText.replace(/<tool_call>[\s\S]*?<\/tool_call>/g, "").trim();
            if (displayText) {
                contentEl.innerHTML = renderMarkdown(displayText);
                scrollToBottom();
            }
            break;

        case "tool_call":
            // Show tool call block
            let tc;
            try { tc = typeof data === "string" ? JSON.parse(data) : data; } catch(e) { break; }
            const tcBlock = createToolCallBlock(tc);
            toolCallBlocks.push(tcBlock);
            contentEl.appendChild(tcBlock);
            scrollToBottom();
            break;

        case "tool_result":
            // Update the last tool call block with result
            let tr;
            try { tr = typeof data === "string" ? JSON.parse(data) : data; } catch(e) { break; }
            if (toolCallBlocks.length > 0) {
                updateToolCallBlock(toolCallBlocks[toolCallBlocks.length - 1], tr);
            }
            scrollToBottom();
            break;

        case "error":
            contentEl.innerHTML += `<div style="color: var(--accent-red); margin-top: 8px;">⚠️ ${escapeHtml(data)}</div>`;
            scrollToBottom();
            break;

        case "done":
            finishStreaming(toolCallBlocks);
            break;
    }
}

function finishStreaming(toolCallBlocks) {
    isStreaming = false;
    updateSendButton();
    scrollToBottom();

    // Refresh sidebar
    loadProjectInfo();
    loadSessions();

    // Focus input
    document.getElementById("message-input").focus();
}

function updateSendButton() {
    const btn = document.getElementById("send-btn");
    if (isStreaming) {
        btn.innerHTML = "■";
        btn.title = "Stop generation";
        btn.onclick = stopGeneration;
        btn.style.background = "var(--accent-red)";
    } else {
        btn.innerHTML = "→";
        btn.title = "Send message";
        btn.onclick = null;
        btn.style.background = "";
    }
    btn.disabled = false;
}

function stopGeneration() {
    if (currentAbortController) {
        currentAbortController.abort();
        currentAbortController = null;
    }
    // Also tell backend to stop
    fetch("/api/stop", { method: "POST" }).catch(() => {});
    isStreaming = false;
    updateSendButton();
    // Add a note to the current response
    if (currentAssistantEl) {
        const contentEl = currentAssistantEl.querySelector(".message-content");
        if (contentEl) {
            contentEl.innerHTML += '<div style="color:var(--text-muted);font-style:italic;margin-top:8px;">⏹ Generation stopped</div>';
        }
    }
}


// ─── Tool Call Blocks ───

function createToolCallBlock(tc) {
    const block = document.createElement("div");
    block.className = "tool-call-block";

    const toolIcons = {
        read_file: "📖",
        write_file: "✍️",
        run_command: "⚡",
        list_directory: "📂",
        search_in_file: "🔍",
    };

    const icon = toolIcons[tc.name] || "🔧";
    const args = tc.arguments || {};
    const argSummary = Object.entries(args)
        .filter(([k, v]) => k !== "content")
        .map(([k, v]) => `${k}: ${typeof v === "string" ? v.substring(0, 60) : v}`)
        .join(", ");

    block.innerHTML = `
        <div class="tool-call-header" onclick="toggleToolCall(this)">
            <span class="tool-icon">${icon}</span>
            <span class="tool-name">${tc.name}</span>
            <span style="color: var(--text-muted); font-size: 11px; font-family: var(--font-mono);">${escapeHtml(argSummary)}</span>
            <span class="tool-status">running...</span>
            <span class="chevron">▶</span>
        </div>
        <div class="tool-call-body">Executing...</div>
    `;

    return block;
}

function updateToolCallBlock(block, tr) {
    const statusEl = block.querySelector(".tool-status");
    const bodyEl = block.querySelector(".tool-call-body");
    const result = tr.result || tr;

    if (result.success) {
        statusEl.textContent = "✓ success";
        statusEl.className = "tool-status success";
    } else {
        statusEl.textContent = "✗ error";
        statusEl.className = "tool-status error";
    }

    let output = "";
    if (result.output) output += result.output;
    if (result.error) output += (output ? "\n\n" : "") + "Error: " + result.error;
    if (result.note) output += (output ? "\n\n" : "") + "Note: " + result.note;

    bodyEl.textContent = output || "(no output)";
}

function toggleToolCall(header) {
    const body = header.nextElementSibling;
    const chevron = header.querySelector(".chevron");
    body.classList.toggle("open");
    chevron.classList.toggle("open");
}


// ─── Sidebar ───

function toggleSidebar() {
    document.getElementById("sidebar").classList.toggle("collapsed");
}

function loadProjectInfo() {
    fetch("/api/project")
        .then(r => r.json())
        .then(data => {
            // Workspace path
            document.getElementById("workspace-path").textContent = data.workspace;

            // File tree
            const fileTree = document.getElementById("file-tree");
            if (data.files && data.files.length > 0) {
                fileTree.innerHTML = data.files.map(f => {
                    const ext = f.path.split(".").pop();
                    const extIcons = {
                        py: "🐍", js: "📜", ts: "📘", html: "🌐", css: "🎨",
                        json: "📋", md: "📝", txt: "📄", yml: "⚙️", yaml: "⚙️",
                    };
                    const icon = extIcons[ext] || "📄";
                    return `<div class="file-item"><span class="icon">${icon}</span><span class="name" title="${escapeHtml(f.path)}">${escapeHtml(f.path)}</span></div>`;
                }).join("");
            } else {
                fileTree.innerHTML = '<div class="file-item" style="color: var(--text-muted); font-style: italic;"><span class="name">No files yet</span></div>';
            }

            // Decisions
            const decisionsList = document.getElementById("decisions-list");
            if (data.memory && data.memory.decisions && data.memory.decisions.length > 0) {
                decisionsList.innerHTML = data.memory.decisions.map(d =>
                    `<div class="decision-item">${escapeHtml(d.decision)}</div>`
                ).join("");
            } else {
                decisionsList.innerHTML = '<div class="decision-item" style="color: var(--text-muted); font-style: italic;">No decisions recorded</div>';
            }
        })
        .catch(() => {});
}

function loadHistory() {
    fetch("/api/history")
        .then(r => r.json())
        .then(data => {
            if (data.messages && data.messages.length > 0) {
                // Remove welcome screen
                const welcome = document.getElementById("welcome-screen");
                if (welcome) welcome.remove();

                // Render messages
                data.messages.forEach(msg => {
                    if (msg.role === "user" || msg.role === "assistant") {
                        addMessage(msg.role, msg.content, msg.attachments);
                    }
                });
            }
        })
        .catch(() => {});
}


// ─── Settings ───

let currentModelName = "";

function loadConfig() {
    fetch("/api/config")
        .then(r => r.json())
        .then(data => {
            currentModelName = data.model || "";
            document.getElementById("setting-workspace").value = data.workspace || "";
            document.getElementById("setting-model").value = currentModelName;
            document.getElementById("setting-ollama-url").value = data.ollama_url || "";
            document.getElementById("model-badge").textContent = currentModelName || "unknown";
        })
        .catch(() => {});
}

function checkOllamaStatus() {
    const dot = document.getElementById("status-dot");
    fetch("/api/status")
        .then(r => r.json())
        .then(data => {
            if (data.ollama && data.model_available) {
                dot.className = "status-dot online";
                dot.title = `Ollama connected — ${data.model} ready`;
                dot.onclick = null;
            } else if (data.ollama) {
                dot.className = "status-dot warning";
                dot.title = `Ollama connected — model "${data.model}" not found. Click to select model.`;
                dot.onclick = openSettings;
            } else {
                dot.className = "status-dot offline";
                dot.title = "Ollama is not running";
                dot.onclick = null;
            }
        })
        .catch(() => {
            dot.className = "status-dot offline";
            dot.title = "Cannot reach server";
            dot.onclick = null;
        });
}

function openSettings() {
    document.getElementById("settings-modal").classList.add("open");
    loadAvailableModels();
}

function closeSettings() {
    document.getElementById("settings-modal").classList.remove("open");
    // Hide manual container on close
    document.getElementById("manual-model-container").style.display = "none";
}

function loadAvailableModels() {
    const select = document.getElementById("setting-model");
    const manualContainer = document.getElementById("manual-model-container");
    const manualInput = document.getElementById("setting-model-manual");

    fetch("/api/models")
        .then(r => r.json())
        .then(data => {
            select.innerHTML = "";
            
            if (data.models && data.models.length > 0) {
                data.models.forEach(m => {
                    const option = document.createElement("option");
                    option.value = m.name;
                    
                    // Format size (bytes to GB)
                    const sizeGB = (m.size / (1024 * 1024 * 1024)).toFixed(1);
                    option.textContent = `${m.name} (${sizeGB} GB)`;
                    
                    if (m.name === currentModelName) {
                        option.selected = true;
                    }
                    select.appendChild(option);
                });
                
                // If current model isn't in the list, show manual input
                const found = data.models.some(m => m.name === currentModelName);
                if (currentModelName && !found) {
                    manualContainer.style.display = "block";
                    manualInput.value = currentModelName;
                }
            } else {
                const option = document.createElement("option");
                option.value = "";
                option.textContent = data.error || "No models found";
                select.appendChild(option);
                
                manualContainer.style.display = "block";
                manualInput.value = currentModelName;
            }
        })
        .catch(err => {
            select.innerHTML = '<option value="">Ollama unreachable</option>';
            manualContainer.style.display = "block";
            manualInput.value = currentModelName;
        });
}

function toggleManualModel(event) {
    if (event) event.preventDefault();
    const container = document.getElementById("manual-model-container");
    const isHidden = container.style.display === "none";
    container.style.display = isHidden ? "block" : "none";
    if (isHidden) {
        document.getElementById("setting-model-manual").value = document.getElementById("setting-model").value;
        document.getElementById("setting-model-manual").focus();
    }
}

function saveSettings() {
    const workspace = document.getElementById("setting-workspace").value.trim();
    
    // Check manual input if visible, otherwise select
    const manualContainer = document.getElementById("manual-model-container");
    const manualModel = document.getElementById("setting-model-manual").value.trim();
    const selectModel = document.getElementById("setting-model").value;
    
    const model = (manualContainer.style.display !== "none" && manualModel) ? manualModel : selectModel;

    if (!model) {
        alert("Please select or enter a model name.");
        return;
    }

    fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace, model }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.status === "ok") {
                currentModelName = data.model;
                document.getElementById("model-badge").textContent = data.model;
                
                if (data.model_available === false) {
                    const dot = document.getElementById("status-dot");
                    dot.className = "status-dot warning";
                    dot.title = `Model "${data.model}" not found in Ollama`;
                } else {
                    checkOllamaStatus();
                }
                
                closeSettings();
                loadProjectInfo();
            }
        })
        .catch(err => alert("Error saving settings: " + err.message));
}

function resetChat() {
    fetch("/api/reset", { method: "POST" })
        .then(r => r.json())
        .then(() => {
            window.location.reload();
        })
        .catch(err => alert("Error resetting: " + err.message));
}


// ─── Session Management ───

function loadSessions() {
    fetch("/api/sessions")
        .then(r => r.json())
        .then(data => {
            const list = document.getElementById("sessions-list");
            if (!data.sessions || data.sessions.length === 0) {
                list.innerHTML = '<div style="color:var(--text-muted);font-style:italic;padding:4px 10px;font-size:12px;">No chats yet</div>';
                return;
            }

            list.innerHTML = data.sessions.map(s => {
                const isActive = s.id === data.current;
                const icon = isActive ? "💬" : "🗨️";
                return `
                    <div class="session-item ${isActive ? 'active' : ''}" data-session-id="${s.id}">
                        <span>${icon}</span>
                        <span class="session-title" title="${escapeHtml(s.title)}">${escapeHtml(s.title)}</span>
                        <button class="session-action session-rename" data-action="rename" data-id="${s.id}" title="Rename">✏️</button>
                        <button class="session-action session-delete" data-action="delete" data-id="${s.id}" title="Delete">✕</button>
                    </div>
                `;
            }).join("");

            // Attach event listeners properly
            list.querySelectorAll(".session-item").forEach(el => {
                el.addEventListener("click", function(e) {
                    // Don't switch if clicking action buttons
                    if (e.target.closest(".session-action")) return;
                    const sid = this.dataset.sessionId;
                    if (sid) switchSession(sid);
                });
            });

            list.querySelectorAll("[data-action='delete']").forEach(btn => {
                btn.addEventListener("click", function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    deleteSession(this.dataset.id);
                });
            });

            list.querySelectorAll("[data-action='rename']").forEach(btn => {
                btn.addEventListener("click", function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    renameSession(this.dataset.id);
                });
            });
        })
        .catch(err => console.error("Failed to load sessions:", err));
}

function switchSession(sessionId) {
    fetch("/api/sessions/switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === "ok") {
            window.location.reload();
        } else {
            alert(data.error || "Failed to switch session");
        }
    })
    .catch(err => { console.error(err); alert("Error switching session: " + err.message); });
}

function deleteSession(sessionId) {
    if (!confirm("Delete this chat?")) return;

    fetch("/api/sessions/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
    })
    .then(r => {
        if (!r.ok) throw new Error("Server returned " + r.status);
        return r.json();
    })
    .then(data => {
        if (data.status === "ok") {
            window.location.reload();
        } else {
            alert(data.error || "Failed to delete");
        }
    })
    .catch(err => { console.error(err); alert("Error deleting session: " + err.message); });
}

function renameSession(sessionId) {
    const newTitle = prompt("Enter new chat name:");
    if (!newTitle || !newTitle.trim()) return;

    fetch("/api/sessions/rename", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, title: newTitle.trim() }),
    })
    .then(r => {
        if (!r.ok) throw new Error("Server returned " + r.status);
        return r.json();
    })
    .then(data => {
        if (data.status === "ok") {
            loadSessions(); // Refresh list without full reload
        } else {
            alert(data.error || "Failed to rename");
        }
    })
    .catch(err => { console.error(err); alert("Error renaming: " + err.message); });
}


// ─── Folder Browser ───

let currentBrowsePath = "";

function openFolderBrowser() {
    document.getElementById("folder-modal").classList.add("open");
    const pathInput = document.getElementById("folder-path-input");
    pathInput.value = "";
    pathInput.addEventListener("keydown", function(e) {
        if (e.key === "Enter") {
            e.preventDefault();
            goToPath();
        }
    });
    loadDirectory(""); // Load root/drives first
}

function closeFolderBrowser() {
    document.getElementById("folder-modal").classList.remove("open");
}

function goToPath() {
    const pathInput = document.getElementById("folder-path-input");
    const path = pathInput.value.trim();
    if (!path) return;
    loadDirectory(path);
}

function loadDirectory(path) {
    const list = document.getElementById("folder-list");
    list.innerHTML = `<div style="color:var(--text-muted);padding:20px;text-align:center;">Loading...</div>`;
    
    fetch("/api/browse?path=" + encodeURIComponent(path))
        .then(r => {
            if (!r.ok) throw new Error("Server returned " + r.status);
            return r.json();
        })
        .then(data => {
            if (data.error) {
                list.innerHTML = `<div style="color:var(--accent-red);padding:20px;">Error: ${escapeHtml(data.error)}</div>`;
                return;
            }

            currentBrowsePath = data.path;
            document.getElementById("folder-path").textContent = currentBrowsePath || "Select a Drive or Folder";
            document.getElementById("folder-path-input").value = currentBrowsePath || "";
            
            let html = "";
            
            // Add 'Up' directory if parent exists
            if (data.parent !== null && data.parent !== undefined) {
                html += `<div class="folder-item" data-path="${escapeHtml(data.parent)}" data-type="dir">
                    <span class="icon">⬆️</span>
                    <span class="name">..</span>
                </div>`;
            }

            if (data.items && data.items.length > 0) {
                data.items.forEach(item => {
                    if (item.is_dir) {
                        html += `<div class="folder-item" data-path="${escapeHtml(item.path)}" data-type="dir">
                            <span class="icon">📁</span>
                            <span class="name">${escapeHtml(item.name)}</span>
                        </div>`;
                    } else {
                        html += `<div class="folder-item is-file">
                            <span class="icon">📄</span>
                            <span class="name">${escapeHtml(item.name)}</span>
                        </div>`;
                    }
                });
            } else if (!data.parent && (!data.items || data.items.length === 0)) {
                html += `<div style="color:var(--text-muted);padding:20px;text-align:center;">Empty directory</div>`;
            }

            list.innerHTML = html;

            // Attach click handlers for directory navigation
            list.querySelectorAll('.folder-item[data-type="dir"]').forEach(el => {
                el.addEventListener("click", function() {
                    loadDirectory(this.dataset.path);
                });
            });
        })
        .catch(err => {
            console.error("Browse error:", err);
            list.innerHTML = `<div style="color:var(--accent-red);padding:20px;">Error: ${escapeHtml(err.message)}</div>`;
        });
}

function selectFolder() {
    if (!currentBrowsePath) {
        alert("Please navigate into a folder first.");
        return;
    }

    fetch("/api/open-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: currentBrowsePath }),
    })
    .then(r => {
        if (!r.ok) throw new Error("Server returned " + r.status);
        return r.json();
    })
    .then(data => {
        if (data.status === "ok") {
            closeFolderBrowser();
            window.location.reload();
        } else {
            alert(data.error || "Failed to open folder");
        }
    })
    .catch(err => { console.error(err); alert("Error: " + err.message); });
}

