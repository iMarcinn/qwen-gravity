"""Core agent loop — orchestrates LLM calls, tool execution, and memory."""

import json
import re
import time
import requests
from typing import Generator

from agent.prompts import build_system_prompt
from agent.tools import execute_tool
from agent.memory import ProjectMemory
from agent.context import ContextManager
from agent.uploads import UploadManager


# Defaults
DEFAULT_MODEL = "qwen2.5-coder:7b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
MAX_ITERATIONS = 15
MAX_RETRIES = 3
RETRY_DELAY = 2


class AgentLoop:
    """Main agentic loop: plan → execute tools → observe → repeat."""

    def __init__(
        self,
        workspace: str,
        model: str = DEFAULT_MODEL,
        ollama_url: str = DEFAULT_OLLAMA_URL,
    ):
        self.workspace = workspace
        self.model = model
        self.ollama_url = ollama_url
        self.memory = ProjectMemory(f"{workspace}/.agent_memory")
        self.context_manager = ContextManager(workspace, self.memory)

    def run(self, user_message: str, attachments: list = None) -> Generator[dict, None, None]:
        """
        Run the agent loop for a user message.
        Yields event dicts: {type, data}
        Types: "thinking", "tool_call", "tool_result", "text", "error", "done"
        """
        # Save user message with attachment metadata
        attachments_meta = []
        if attachments:
            upload_manager = UploadManager(self.memory.memory_dir)
            for upload_id in attachments:
                manifest = upload_manager.get_upload(upload_id)
                if manifest:
                    attachments_meta.append(manifest)

        self.memory.add_message("user", user_message, attachments=attachments_meta)

        # Build context
        project_summary = self.memory.get_project_summary()
        file_context = self.context_manager.build_context_block(user_message)
        full_context = ""
        if project_summary:
            full_context += project_summary + "\n\n"
        if file_context:
            full_context += file_context

        system_prompt = build_system_prompt(full_context)

        # Build message history
        messages = [{"role": "system", "content": system_prompt}]

        # Add recent conversation history (without the current message since we add it below)
        recent = self.memory.get_recent_messages(limit=20)
        # Skip the last message since that's the user message we just added
        for msg in recent[:-1]:
            messages.append({"role": msg["role"], "content": msg["content"]})

        # Add current user message
        messages.append({"role": "user", "content": user_message})

        # Inject attachments if present
        if attachments:
            upload_manager = UploadManager(self.memory.memory_dir)
            attachment_content = ""
            for upload_id in attachments:
                files = upload_manager.read_upload_files(upload_id)
                for f in files:
                    attachment_content += f"\n### Attached File: {f['path']}\n```\n{f['content']}\n```\n"
            
            if attachment_content:
                messages.append({
                    "role": "user", 
                    "content": f"The following files have been attached to this message for your reference:\n{attachment_content}"
                })

        # Agentic loop
        iteration = 0
        while iteration < MAX_ITERATIONS:
            iteration += 1

            yield {"type": "thinking", "data": f"Iteration {iteration}/{MAX_ITERATIONS}..."}

            # Call LLM with early-stop tool detection
            full_response = ""
            tool_calls = []
            try:
                for token in self._call_ollama(messages):
                    full_response += token
                    yield {"type": "text", "data": token}

                    # Check for tool calls as we stream — abort early if found
                    # Only check periodically (when we see closing braces) to save CPU
                    if '}' in token:
                        tool_calls = self._parse_tool_calls(full_response)
                        if tool_calls:
                            # Tool call detected! Stop generation.
                            break

            except Exception as e:
                yield {"type": "error", "data": f"Ollama error: {str(e)}"}
                self.memory.add_message("assistant", f"[Error: {str(e)}]")
                yield {"type": "done", "data": ""}
                return

            # If we didn't detect during streaming, try one final parse
            if not tool_calls:
                tool_calls = self._parse_tool_calls(full_response)

            if not tool_calls:
                # No tool calls — agent is done
                self.memory.add_message("assistant", full_response)
                self.memory.save()
                yield {"type": "done", "data": ""}
                return

            # Execute tool calls
            messages.append({"role": "assistant", "content": full_response})

            tool_results_text = ""
            for tc in tool_calls:
                yield {"type": "tool_call", "data": json.dumps(tc)}

                result = execute_tool(tc["name"], tc["arguments"], self.workspace)
                yield {"type": "tool_result", "data": json.dumps({
                    "tool": tc["name"],
                    "result": result
                })}

                # Update context if a file was written
                if tc["name"] == "write_file" and result.get("success"):
                    path = tc["arguments"].get("path", "")
                    content = tc["arguments"].get("content", "")
                    self.context_manager.update_after_write(path, content)
                    # Record the file write as a project decision
                    self.memory.add_decision(
                        f"Created/updated `{path}`",
                        context=f"Tool: {tc['name']}"
                    )

                tool_results_text += f"\n<tool_result>\n{json.dumps(result, indent=2)}\n</tool_result>\n"

            # Feed results back to LLM
            messages.append({"role": "user", "content": f"Tool results:{tool_results_text}\n\nContinue with the task. If you're done, provide a summary. If not, make more tool calls."})

        # Max iterations reached
        yield {"type": "error", "data": "Max iterations reached. The agent stopped to prevent infinite loops."}
        self.memory.add_message("assistant", "[Stopped: max iterations reached]")
        self.memory.save()
        yield {"type": "done", "data": ""}

    def _call_ollama(self, messages: list) -> Generator[str, None, None]:
        """Call Ollama API with streaming. Yields tokens."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": 0.3,
                "num_predict": 4096,
                "top_p": 0.9,
            }
        }

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                response = requests.post(
                    f"{self.ollama_url}/api/chat",
                    json=payload,
                    stream=True,
                    timeout=300
                )
                response.raise_for_status()

                for line in response.iter_lines():
                    if line:
                        try:
                            chunk = json.loads(line)
                            token = chunk.get("message", {}).get("content", "")
                            if token:
                                yield token
                            if chunk.get("done", False):
                                return
                        except json.JSONDecodeError:
                            continue
                return

            except requests.exceptions.ConnectionError:
                last_error = "Cannot connect to Ollama. Is it running? (ollama serve)"
            except requests.exceptions.Timeout:
                last_error = "Ollama request timed out."
            except requests.exceptions.HTTPError as e:
                last_error = f"Ollama HTTP error: {e}"
            except Exception as e:
                last_error = f"Ollama error: {e}"

            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))

        raise RuntimeError(last_error or "Failed to get response from Ollama")

    def _parse_tool_calls(self, response: str) -> list[dict]:
        """Extract tool calls from the LLM response. Handles multiple formats."""
        tool_calls = []
        valid_tools = {"read_file", "write_file", "run_command", "list_directory", "search_in_file"}

        # Strategy 1: Match <tool_call>...</tool_call> blocks (or incomplete streaming blocks)
        pattern1 = r'<tool_call>\s*(.*?)(?:</tool_call>|$)'
        for match in re.findall(pattern1, response, re.DOTALL):
            tc = self._try_parse_tool_json(match)
            if tc:
                tool_calls.append(tc)

        if tool_calls:
            return tool_calls

        # Strategy 2: Match ```json ... blocks (even if unclosed during streaming)
        pattern2 = r'```(?:json)?\s*(\{[\s\S]*?"name"[\s\S]*?\})(?:```|$)'
        for match in re.findall(pattern2, response, re.DOTALL):
            tc = self._try_parse_tool_json(match)
            if tc and tc["name"] in valid_tools:
                tool_calls.append(tc)

        if tool_calls:
            return tool_calls

        # Strategy 3: Find any block that looks like a tool call JSON object anywhere in the text
        # We look for {"name": "something", "arguments": {...}} 
        # By finding the first { and parsing forward until valid JSON or end.
        for tool_name in valid_tools:
            # basic text search to see if tool name exists
            if f'"name": "{tool_name}"' in response.replace(" ", "") or f'"name":"{tool_name}"' in response.replace(" ", ""):
                # Find all '{' chars
                starts = [i for i, c in enumerate(response) if c == '{']
                for start in starts:
                    # Try to parse incremental substrings
                    depth = 0
                    for i in range(start, len(response)):
                        if response[i] == '{': depth += 1
                        elif response[i] == '}': depth -= 1
                        
                        if depth == 0:
                            tc = self._try_parse_tool_json(response[start:i+1])
                            if tc and tc.get("name") == tool_name:
                                tool_calls.append(tc)
                                break
                    if tool_calls:
                        break
            if tool_calls:
                break
        
        return tool_calls

    def _try_parse_tool_json(self, text: str):
        """Try to parse text as a tool call JSON object, aggressively attempting to fix truncated JSON."""
        text = text.strip()
        # Clean up common markdown artifacts if they got inside the capture group
        if text.startswith("```json"): text = text[7:]
        if text.startswith("```"): text = text[3:]
        if text.endswith("```"): text = text[:-3]
        text = text.strip()

        # If it looks truncated (missing closing braces), pad it
        open_braces = text.count('{')
        close_braces = text.count('}')
        if open_braces > close_braces:
            text += '}' * (open_braces - close_braces)

        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and "name" in obj:
                args = obj.get("arguments", obj.get("args", obj.get("params", obj.get("parameters", {}))))
                return {"name": obj["name"], "arguments": args if isinstance(args, dict) else {}}
        except json.JSONDecodeError:
            pass

        return None

    def get_conversation_history(self) -> list:
        """Get the full conversation history."""
        return self.memory.conversation_history

    def clear_conversation(self):
        """Clear conversation history."""
        self.memory.clear_conversation()

    def get_project_files(self) -> list:
        """Get list of project files for the UI sidebar."""
        return self.context_manager.scan_project()

    def get_memory_summary(self) -> dict:
        """Get memory summary for the UI."""
        return {
            "files": self.memory.file_registry,
            "decisions": self.memory.decisions[-10:],
            "message_count": len(self.memory.conversation_history)
        }
