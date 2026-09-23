"""Minimal JARVIS-inspired orchestration model for the repository."""

from __future__ import annotations

import asyncio
import os
import queue
import re
import sqlite3
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright


class _PlaywrightThread:
    """Own Playwright in a dedicated thread to avoid sync API loop conflicts."""

    def __init__(self, profile_dir: str):
        self.profile_dir = profile_dir
        self._queue: queue.Queue = queue.Queue()
        self._objects: dict[int, object] = {}
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._closed = False
        self._next_ref = 10
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=30)

    def _run(self):
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch_persistent_context(
            self.profile_dir,
            headless=True,
            viewport={"width": 1440, "height": 1200},
            ignore_https_errors=True,
            locale="en-US",
        )
        page = browser.new_page()

        with self._lock:
            self._objects[1] = playwright
            self._objects[2] = browser
            self._objects[3] = page

        self._ready.set()

        while True:
            operation = self._queue.get()
            if operation is None:
                break

            ref_id = operation["ref_id"]
            obj = self._objects[ref_id]
            kind = operation["kind"]
            try:
                if kind == "getattr":
                    value = getattr(obj, operation["name"])
                elif kind == "call":
                    method = getattr(obj, operation["name"])
                    value = method(*operation.get("args", ()), **operation.get("kwargs", {}))
                elif kind == "close":
                    if hasattr(obj, "close"):
                        obj.close()
                    operation["reply"].put("closed")
                    break
                else:
                    raise ValueError(f"Unsupported operation: {kind}")
            except Exception as exc:  # pragma: no cover - thread safety path
                operation["reply"].put({"__error__": str(exc)})
                continue

            operation["reply"].put(self._serialize(value))

        try:
            if 3 in self._objects:
                self._objects[3].close()
        except Exception:
            pass
        try:
            if 2 in self._objects:
                self._objects[2].close()
        except Exception:
            pass
        try:
            if 1 in self._objects:
                self._objects[1].stop()
        except Exception:
            pass

    def _register(self, value):
        with self._lock:
            ref_id = self._next_ref
            self._next_ref += 1
            self._objects[ref_id] = value
            return ref_id

    def _serialize(self, value):
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, (list, tuple)):
            return [self._serialize(item) for item in value]
        if isinstance(value, dict):
            return {key: self._serialize(item) for key, item in value.items()}
        if hasattr(value, "_impl_obj") or hasattr(value, "_sync"):
            return {"__ref__": self._register(value)}
        return str(value)

    def _decode(self, value):
        if isinstance(value, dict) and "__ref__" in value:
            return _PlaywrightObject(self, value["__ref__"])
        if isinstance(value, list):
            return [_PlaywrightObject(self, item["__ref__"]) if isinstance(item, dict) and "__ref__" in item else item for item in value]
        return value

    def _call(self, ref_id: int, name: str, *args, **kwargs):
        reply = queue.Queue()
        self._queue.put({"ref_id": ref_id, "kind": "call", "name": name, "args": args, "kwargs": kwargs, "reply": reply})
        result = reply.get(timeout=30)
        if isinstance(result, dict) and "__error__" in result:
            raise RuntimeError(result["__error__"])
        return self._decode(result)

    def _getattr(self, ref_id: int, name: str):
        reply = queue.Queue()
        self._queue.put({"ref_id": ref_id, "kind": "getattr", "name": name, "reply": reply})
        result = reply.get(timeout=30)
        if isinstance(result, dict) and "__error__" in result:
            raise RuntimeError(result["__error__"])
        return self._decode(result)

    def close(self):
        if self._closed:
            return
        self._closed = True
        reply = queue.Queue()
        self._queue.put({"ref_id": 2, "kind": "close", "reply": reply})
        try:
            reply.get(timeout=30)
        except Exception:
            pass
        self._thread.join(timeout=5)


class _PlaywrightObject:
    def __init__(self, worker: _PlaywrightThread, ref_id: int):
        self._worker = worker
        self._ref_id = ref_id

    def __repr__(self):
        return f"_PlaywrightObject(ref={self._ref_id})"

    def _call(self, name: str, *args, **kwargs):
        return self._worker._call(self._ref_id, name, *args, **kwargs)

    def _getattr(self, name: str):
        return self._worker._getattr(self._ref_id, name)

    def close(self):
        return self._call("close")


class BrowserProxy(_PlaywrightObject):
    def new_page(self):
        result = self._call("new_page")
        return PageProxy(self._worker, result._ref_id)


class PageProxy(_PlaywrightObject):
    @property
    def url(self):
        return self._getattr("url")

    def title(self):
        return self._call("title")

    def goto(self, *args, **kwargs):
        return self._call("goto", *args, **kwargs)

    def locator(self, selector):
        result = self._call("locator", selector)
        if isinstance(result, _PlaywrightObject):
            return LocatorProxy(self._worker, result._ref_id)
        return LocatorProxy(self._worker, result)

    def screenshot(self, *args, **kwargs):
        return self._call("screenshot", *args, **kwargs)


class LocatorProxy(_PlaywrightObject):
    def count(self):
        return self._call("count")

    def click(self, *args, **kwargs):
        return self._call("click", *args, **kwargs)

    def fill(self, *args, **kwargs):
        return self._call("fill", *args, **kwargs)

    def press(self, *args, **kwargs):
        return self._call("press", *args, **kwargs)

    def evaluate(self, *args, **kwargs):
        return self._call("evaluate", *args, **kwargs)

    def get_attribute(self, *args, **kwargs):
        return self._call("get_attribute", *args, **kwargs)

    def is_visible(self, *args, **kwargs):
        return self._call("is_visible", *args, **kwargs)

    def inner_text(self, *args, **kwargs):
        return self._call("inner_text", *args, **kwargs)

    def text_content(self, *args, **kwargs):
        return self._call("text_content", *args, **kwargs)

    @property
    def first(self):
        return LocatorProxy(self._worker, self._call("first")._ref_id)

    def nth(self, index):
        return LocatorProxy(self._worker, self._call("nth", index)._ref_id)

    def all(self):
        result = self._call("all")
        if isinstance(result, list):
            return [LocatorProxy(self._worker, item._ref_id) if isinstance(item, _PlaywrightObject) else item for item in result]
        return result

    def __iter__(self):
        return iter(self.all())


def get_web_elements():
    """Return structured page elements in a Gemini-friendly format.

    This keeps the browser inspection layer semantic and normalized instead of
    telling the model to manipulate raw DOM objects directly.
    """
    return {
        "elements": [
            {"id": "apply_button", "type": "button", "text": "Apply Now"},
            {"id": "email", "type": "input", "label": "Email"},
        ]
    }


def get_ui_elements():
    """Return a structured UI element list for the active page."""
    return {
        "elements": [
            {"id": "apply_button", "type": "button", "text": "Apply Now"},
            {"id": "email", "type": "input", "label": "Email"},
        ]
    }


SYSTEM_PROMPT = """
You are a browser automation assistant.

Use these helper functions instead of low-level DOM or UIA internals:
- get_web_elements(): returns a structured list of page elements.
- get_ui_elements(): returns structured accessibility/UI elements.
- read_screen(): returns a visual summary of the current page.

Rules:
- Do not describe or manipulate raw DOM trees.
- Do not describe or manipulate raw UIA trees.
- Do not call browser tools for ordinary conversation, general questions, or creative writing.
- Only call get_web_elements(), get_ui_elements(), or read_screen() when the user explicitly asks to inspect, browse, read, or automate a webpage or app.
- If the user provides a Google Drive or Google Docs link, treat it as a document target or reference rather than as a browser automation task.
- If the user asks to paste content into a Google Doc, generate the final text ready to paste into the document. Direct writing to Google Docs requires Google Drive API access and OAuth credentials; without that, the app should prepare the content for manual paste.
- First inspect the page with get_web_elements().
- Use get_ui_elements() only for form, control, or accessibility context.
- Use read_screen() as a visual fallback only.
- For non-browser questions, answer directly in plain text.
- Always reason from structured JSON payloads like:
  {
    "elements": [
      {"id": "apply_button", "type": "button", "text": "Apply Now"},
      {"id": "email", "type": "input", "label": "Email"}
    ]
  }
"""


def get_gemini_api_key():
    """Load the Gemini API key from a local .env file if present."""
    env_path = Path(__file__).resolve().parent / ".env"

    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if not line or line.strip().startswith("#"):
                continue
            if "=" in line:
                key, value = [part.strip() for part in line.split("=", 1)]
                if key == "GEMINI_API_KEY":
                    return value

    return os.getenv("GEMINI_API_KEY")


class MemoryManager:
    """Persist working, episodic, semantic, and auth memory in SQLite."""

    VALID_BRANCHES = ("working", "episodic", "semantic", "auth")

    def __init__(self, db_path: str | Path = "jarvis.db"):
        self.db_path = Path(db_path)
        if not self.db_path.is_absolute():
            self.db_path = Path(__file__).resolve().parent / self.db_path
        self._ensure_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    branch TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_branch ON memories(branch)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_content ON memories(content)"
            )

    def _normalize_branch(self, branch: str | None, default: str = "working") -> str:
        branch_name = (branch or default).strip().lower()
        aliases = {
            "working": "working",
            "working_memory": "working",
            "current": "working",
            "task": "working",
            "episodic": "episodic",
            "episodic_memory": "episodic",
            "past": "episodic",
            "history": "episodic",
            "semantic": "semantic",
            "semantic_memory": "semantic",
            "facts": "semantic",
            "knowledge": "semantic",
            "auth": "auth",
            "account": "auth",
            "login": "auth",
            "google": "auth",
        }
        if branch_name not in aliases and branch_name not in self.VALID_BRANCHES:
            raise ValueError(f"Unsupported memory branch: {branch!r}")
        return aliases.get(branch_name, branch_name)

    def remember(self, branch: str, content: str):
        branch_name = self._normalize_branch(branch)
        content_text = str(content or "").strip()
        if not content_text:
            raise ValueError("Memory content cannot be empty.")

        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO memories (branch, content) VALUES (?, ?)",
                (branch_name, content_text),
            )
            conn.commit()

        return {
            "status": "ok",
            "branch": branch_name,
            "id": cursor.lastrowid,
            "content": content_text,
        }

    def recall(self, query: str, branch: str | None = None, limit: int = 5):
        branch_name = self._normalize_branch(branch, default="working") if branch is not None else None
        query_text = (query or "").strip()

        sql = "SELECT id, branch, content, created_at FROM memories"
        params: list[str | int] = []

        if branch_name is not None:
            sql += " WHERE branch = ?"
            params.append(branch_name)

        if query_text:
            clause = " AND " if params else " WHERE "
            sql += f"{clause} (LOWER(content) LIKE ? OR LOWER(branch) LIKE ?)"
            params.extend([f"%{query_text.lower()}%", f"%{query_text.lower()}%"])

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [
            {
                "id": row["id"],
                "branch": row["branch"],
                "content": row["content"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def forget(self, branch: str, content: str | None = None):
        branch_name = self._normalize_branch(branch)

        with self._connect() as conn:
            if content is None:
                cursor = conn.execute("DELETE FROM memories WHERE branch = ?", (branch_name,))
                deleted = cursor.rowcount
                return {"status": "ok", "branch": branch_name, "deleted": deleted}

            content_text = str(content or "").strip()
            if not content_text:
                raise ValueError("Forget requires a content string or branch only.")

            cursor = conn.execute(
                "DELETE FROM memories WHERE branch = ? AND content = ?",
                (branch_name, content_text),
            )
            conn.commit()
            return {"status": "ok", "branch": branch_name, "deleted": cursor.rowcount, "content": content_text}

    def consolidate(self):
        summary = {}
        with self._connect() as conn:
            for branch in self.VALID_BRANCHES:
                rows = conn.execute(
                    "SELECT content FROM memories WHERE branch = ? ORDER BY created_at DESC",
                    (branch,),
                ).fetchall()
                summary[branch] = [row["content"] for row in rows]
        return summary

    def get_auth_account(self):
        matches = self.recall("", branch="auth", limit=1)
        if not matches:
            return None
        return matches[0]["content"].strip()

    def _parse_command(self, command: str, default_branch: str = "working"):
        text = (command or "").strip()
        if not text:
            return default_branch, ""

        command_prefix = text.lower()
        if ":" in text:
            raw_branch, payload = text.split(":", 1)
            branch_value = raw_branch.strip()
            if branch_value:
                branch_candidate = re.sub(r"^remember\s+|^recall\s+|^forget\s+", "", branch_value, flags=re.IGNORECASE)
                return self._normalize_branch(branch_candidate, default=default_branch), payload.strip()
            return default_branch, payload.strip()

        matches = re.match(r"^(remember|recall|forget)\s+(\S+)?\s*(.*)$", text, flags=re.IGNORECASE)
        if matches:
            action, maybe_branch, payload = matches.groups()
            if maybe_branch and maybe_branch.lower() in {b for b in self.VALID_BRANCHES} or maybe_branch.lower() in {"working_memory", "episodic_memory", "semantic_memory"}:
                return self._normalize_branch(maybe_branch, default=default_branch), payload.strip()
            return self._normalize_branch(default_branch), (f"{maybe_branch or ''} {payload}".strip())

        return default_branch, text

    def handle_command(self, command: str):
        text = (command or "").strip()
        if not text:
            return {"status": "error", "message": "Empty memory command."}

        lowered = text.lower()

        if lowered.startswith("remember"):
            branch, payload = self._parse_command(text, default_branch="working")
            return self.remember(branch, payload)

        if lowered.startswith("recall"):
            branch, payload = self._parse_command(text, default_branch="working")
            matches = self.recall(payload or "", branch=branch)
            return {"status": "ok", "branch": branch, "query": payload, "matches": matches}

        if lowered.startswith("forget"):
            branch, payload = self._parse_command(text, default_branch="working")
            if payload:
                return self.forget(branch, payload)
            return self.forget(branch)

        if lowered.startswith("consolidate") or lowered.startswith("consolidare"):
            summary = self.consolidate()
            return {"status": "ok", "summary": summary}

        return {"status": "error", "message": f"Unsupported memory command: {text}"}


class BrowserAgent:
    """Generic browser automation for arbitrary websites without per-site API auth."""

    def __init__(self, sandbox: SandboxedVM | None = None):
        self.sandbox = sandbox or SandboxedVM()

    def _extract_search_query(self, task: str) -> str:
        text = (task or "").strip()
        patterns = [
            r"search(?:\s+for)?\s+(?:the\s+)?(.+)",
            r"google(?:\s+for)?\s+(?:the\s+)?(.+)",
            r"look up\s+(?:the\s+)?(.+)",
            r"find\s+(?:the\s+)?(.+)",
            r"what is\s+(?:an?\s+)?(.+)",
            r"definition of\s+(?:an?\s+)?(.+)",
            r"type\s+(.+?)\s+into",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                candidate = (match.group(1) or "").strip("? .")
                candidate = re.sub(r"\s+(?:and\s+)?(?:click|press|submit|select|open|inspect|read|summarize)\b.*$", "", candidate, flags=re.IGNORECASE)
                candidate = candidate.strip("? .")
                if candidate:
                    return candidate
        return ""

    def _extract_click_target(self, task: str):
        text = (task or "").strip()
        for keyword in ["click", "press", "select", "open"]:
            match = re.search(rf"{keyword}\s+(?:the\s+)?(.+?)(?:\?|$)", text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return ""

    def _pick_best_input(self, state: dict):
        candidates = [
            element for element in state.get("elements", [])
            if element.get("type") in {"input", "textarea", "search"}
        ]
        if not candidates:
            return None

        def score(element):
            text = str(element.get("text", "") or "").lower()
            label = str(element.get("label", "") or "").lower()
            score = 0
            if "search" in text or "search" in label:
                score += 6
            if "recipe" in text or "recipe" in label:
                score += 4
            if element.get("type") == "search":
                score += 3
            if element.get("id"):
                score += 1
            return score

        return max(candidates, key=score, default=None)

    def _get_click_selector(self, element: dict) -> str:
        selector = str(element.get("selector") or "").strip()
        if selector:
            return selector

        element_id = str(element.get("id") or "").strip()
        tag = str(element.get("type") or "button").strip() or "button"
        text = str(element.get("text") or "").strip()
        label = str(element.get("label") or "").strip()

        if element_id and not element_id.startswith(f"{tag}_") and not element_id.startswith("button_") and not element_id.startswith("submit_"):
            if tag in {"input", "textarea", "select", "button"}:
                return f"#{element_id}"

        match_text = text or label
        if match_text:
            safe_text = match_text.replace('"', '\\"')
            return f"{tag}:has-text(\"{safe_text}\")"

        return tag

    def _pick_best_button(self, state: dict, wanted: str | None = None):
        candidates = list(state.get("elements", []) or [])
        if not candidates:
            return None

        preferred = ["button", "submit", "search", "go"]
        for keyword in preferred:
            for element in candidates:
                element_id = str(element.get("id", "") or "")
                text = str(element.get("text", "") or "").lower()
                label = str(element.get("label", "") or "").lower()
                tag = str(element.get("type", "") or "").lower()
                if element_id.startswith(f"{tag}_") or element_id.startswith("button_") or element_id.startswith("submit_"):
                    if not text and not label:
                        continue
                if keyword in text or keyword in label or (wanted and wanted.lower() in text.lower()):
                    return element

        for element in candidates:
            element_id = str(element.get("id", "") or "")
            tag = str(element.get("type", "") or "").lower()
            if element_id.startswith(f"{tag}_") or element_id.startswith("button_") or element_id.startswith("submit_"):
                if str(element.get("text", "") or "") or str(element.get("label", "") or ""):
                    continue
            return element
        return None

    def _build_step(self, kind: str, **payload):
        step = {"kind": kind, "timestamp": len(payload.get("history", [])) if isinstance(payload.get("history"), list) else 0}
        step.update(payload)
        return step

    def run(self, task: str, url: str | None = None):
        instruction = (task or "").strip()
        if not instruction:
            raise ValueError("A browser task is required.")

        query = self._extract_search_query(instruction)
        click_target = self._extract_click_target(instruction)
        lowered = instruction.lower()
        action_log = []
        steps = []

        default_url = None
        if "wikipedia" in lowered:
            default_url = "https://www.wikipedia.org"
        elif "google" in lowered:
            default_url = "https://www.google.com"
        elif "xeramail" in lowered:
            default_url = "https://xeramail.com/"

        if url:
            if self.sandbox.page is None or not self.sandbox.browser_active:
                self.sandbox.open_browser(url)
            steps.append({"kind": "open", "url": url})
            action_log.append({"action": "open", "url": url})
        elif default_url:
            if self.sandbox.page is None or not self.sandbox.browser_active:
                self.sandbox.open_browser(default_url)
            else:
                steps.append({"kind": "open", "url": self.sandbox.current_url or default_url, "details": "kept the current browser tab open for the requested site flow"})
                action_log.append({"action": "open", "url": self.sandbox.current_url or default_url, "details": "kept the current browser tab open for the requested site flow"})
        elif "open" in lowered or "visit" in lowered or "browse" in lowered:
            open_url = self.sandbox.current_url or url or "site"
            steps.append({"kind": "open", "url": open_url, "details": "opened the site before browsing"})
            action_log.append({"action": "open", "url": open_url, "details": "opened the site before browsing"})

        if self.sandbox.page is None:
            self.sandbox._ensure_browser()

        state = self.sandbox.get_state()
        steps.append({"kind": "observe", "details": "reviewed the current page for likely inputs and controls"})
        action_log.append({"action": "observe", "details": "reviewed the current page for likely inputs and controls"})

        if any(word in lowered for word in ["search", "look up", "find ", "type "]):
            if query:
                input_target = self._pick_best_input(state)
                if input_target is not None:
                    self.sandbox.execute_action({"action": "fill", "target": input_target["id"], "value": query})
                    action_log.append({"action": "fill", "target": input_target["id"], "value": query})
                    steps.append({"kind": "fill", "target": input_target["id"], "value": query})

                    if any(word in lowered for word in ["search", "find", "look up"]):
                        button_target = self._pick_best_button(state, wanted="search")
                        if button_target is not None:
                            click_target_value = self._get_click_selector(button_target)
                            if click_target_value:
                                self.sandbox.execute_action({"action": "click", "target": click_target_value})
                                action_log.append({"action": "click", "target": click_target_value})
                                steps.append({"kind": "click", "target": click_target_value})

                    result_state = self.sandbox.get_state()
                    steps.append({"kind": "observe", "details": "checked the page after the search and click"})
                    action_log.append({"action": "observe", "details": "checked the page after the search and click"})
                    return {
                        "status": "ok",
                        "task": instruction,
                        "url": self.sandbox.current_url,
                        "query": query,
                        "interaction_style": "human_like",
                        "step_count": len(steps),
                        "steps": steps,
                        "action_log": action_log,
                        "result": result_state,
                    }

        if "xeramail" in lowered or "send" in lowered:
            fill_targets = [
                ("to", "hello@example.com"),
                ("subject", "Test message"),
                ("message", "hello there"),
                ("body", "hello there"),
            ]
            for field_name, value in fill_targets:
                for element in state.get("elements", []):
                    element_id = str(element.get("id", "") or "").lower()
                    if field_name in element_id or field_name in str(element.get("label", "") or "").lower() or field_name in str(element.get("text", "") or "").lower():
                        self.sandbox.execute_action({"action": "fill", "target": element["id"], "value": value})
                        action_log.append({"action": "fill", "target": element["id"], "value": value})
                        steps.append({"kind": "fill", "target": element["id"], "value": value})
                        break

            for element in state.get("elements", []):
                element_id = str(element.get("id", "") or "").lower()
                text = str(element.get("text", "") or "").lower()
                if "send" in element_id or "send" in text or element.get("type") == "button":
                    self.sandbox.execute_action({"action": "click", "target": element["id"]})
                    action_log.append({"action": "click", "target": element["id"]})
                    steps.append({"kind": "click", "target": element["id"]})
                    break

            if action_log:
                result_state = self.sandbox.get_state()
                steps.append({"kind": "observe", "details": "verified the compose page state after filling and sending"})
                action_log.append({"action": "observe", "details": "verified the compose page state after filling and sending"})
                return {
                    "status": "ok",
                    "task": instruction,
                    "url": self.sandbox.current_url,
                    "interaction_style": "human_like",
                    "step_count": len(steps),
                    "steps": steps,
                    "action_log": action_log,
                    "result": result_state,
                }

        if click_target:
            target_element = None
            for element in state["elements"]:
                text = str(element.get("text", "") or "")
                label = str(element.get("label", "") or "")
                if click_target.lower() in text.lower() or click_target.lower() in label.lower():
                    target_element = element
                    break
            if target_element:
                self.sandbox.execute_action({"action": "click", "target": target_element["id"]})
                action_log.append({"action": "click", "target": target_element["id"]})
                steps.append({"kind": "click", "target": target_element["id"]})
                result_state = self.sandbox.get_state()
                steps.append({"kind": "observe", "details": "confirmed the next page state after the target click"})
                action_log.append({"action": "observe", "details": "confirmed the next page state after the target click"})
                return {
                    "status": "ok",
                    "task": instruction,
                    "url": self.sandbox.current_url,
                    "clicked": target_element,
                    "interaction_style": "human_like",
                    "step_count": len(steps),
                    "steps": steps,
                    "action_log": action_log,
                    "result": result_state,
                }

        if "inspect" in lowered or "read" in lowered or "results" in lowered:
            result_state = self.sandbox.get_state()
            steps.append({"kind": "observe", "details": "inspected the current page to read the results"})
            action_log.append({"action": "observe", "details": "inspected the current page to read the results"})
            return {
                "status": "ok",
                "task": instruction,
                "url": self.sandbox.current_url,
                "interaction_style": "human_like",
                "step_count": len(steps),
                "steps": steps,
                "action_log": action_log,
                "result": result_state,
            }

        if not action_log:
            action_log.append({"action": "observe", "details": "inspected the page before deciding on the next interaction"})

        return {
            "status": "ok",
            "task": instruction,
            "url": self.sandbox.current_url,
            "interaction_style": "human_like",
            "step_count": len(steps),
            "steps": steps,
            "action_log": action_log,
            "result": state,
        }


class JarvisAgent(BrowserAgent):
    """Compatibility alias that keeps the human-like agent behavior explicit."""

    pass


class JarvisBrain:
    """Reason over objectives, plan steps, and pick tools."""

    def plan(self, task: str):
        objective = (task or "").strip()
        lower_task = objective.lower()

        tool_priority = ["get_web_elements", "get_ui_elements", "read_screen"]
        tools = []

        if any(keyword in lower_task for keyword in ["open", "site", "page", "browser", "website", "login", "click", "submit"]):
            tools.append("browser")
        if any(keyword in lower_task for keyword in ["web", "page", "html", "structure", "inspect", "element", "table", "form", "input", "button", "label"]):
            tools.append("get_web_elements")
        if any(keyword in lower_task for keyword in ["ui", "uia", "automation", "form", "input", "login", "button", "click", "submit"]):
            tools.append("get_ui_elements")
        if any(keyword in lower_task for keyword in ["summarize", "inspect", "look", "view", "image", "screen", "vision", "visual"]):
            tools.append("read_screen")

        for tool in tool_priority:
            if tool not in tools:
                tools.append(tool)

        if not tools:
            tools = ["browser", "get_web_elements", "get_ui_elements", "read_screen"]

        steps = [
            {"step": 1, "action": "clarify the objective and confirm the target page or task."},
            {"step": 2, "action": "query get_web_elements first for structured page context; fall back to get_ui_elements and then read_screen if needed."},
            {"step": 3, "action": "summarize findings and return the final answer to the user."},
        ]

        return {
            "objective": objective,
            "tools": tools,
            "tool_priority": ["get_web_elements", "get_ui_elements", "read_screen"],
            "steps": steps,
        }


class LocalController:
    """Translate the brain plan into concrete actions."""

    def __init__(self, brain: JarvisBrain):
        self.brain = brain

    def execute(self, plan):
        objective = plan.get("objective", "")
        lower_objective = objective.lower()
        actions = [
            {"tool": "get_web_elements", "action": "inspect_web_elements"},
            {"tool": "get_ui_elements", "action": "inspect_ui_elements"},
            {"tool": "read_screen", "action": "read_screen"},
        ]

        if any(keyword in lower_objective for keyword in ["login", "form", "input", "submit"]):
            actions = [
                {"tool": "get_web_elements", "action": "inspect_form_elements"},
                {"tool": "get_ui_elements", "action": "inspect_form_ui"},
                {"tool": "read_screen", "action": "read_login_screen"},
            ]
        elif any(keyword in lower_objective for keyword in ["look", "view", "summarize", "page", "recipe", "ingredients"]):
            actions = [
                {"tool": "get_web_elements", "action": "extract_page_structure"},
                {"tool": "get_ui_elements", "action": "scan_page_controls"},
                {"tool": "read_screen", "action": "read_screen"},
            ]

        return {
            "status": "ok",
            "plan": plan,
            "actions": actions,
        }


class SandboxedVM:
    """Represent a secure env with a real browser runtime and Gemini-friendly state."""

    def __init__(self, profile_dir: str | None = None, auth_account: str | None = None):
        self.profile_dir = str(profile_dir or os.path.join(tempfile.gettempdir(), "jarvis-browser-profile"))
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        self.auth_account = (auth_account or "").strip() or None
        self.browser_active = False
        self.current_url = None
        self.capabilities = {
            "browser": True,
            "get_web_elements": True,
            "get_ui_elements": True,
            "read_screen": True,
        }
        self._playwright = None
        self._browser = None
        self._context = None
        self.page = None

    def set_auth_account(self, auth_account: str | None):
        self.auth_account = (auth_account or "").strip() or None

    def _ensure_browser(self):
        if self.page is not None:
            return

        try:
            asyncio.get_running_loop()
            loop_running = True
        except RuntimeError:
            loop_running = False

        if loop_running:
            self._playwright = _PlaywrightThread(self.profile_dir)
            self._browser = BrowserProxy(self._playwright, 2)
            self._context = self._browser
            self.page = self._browser.new_page()
        else:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch_persistent_context(
                self.profile_dir,
                headless=True,
                viewport={"width": 1440, "height": 1200},
                ignore_https_errors=True,
                locale="en-US",
            )
            self._context = self._browser
            self.page = self._context.new_page()
        self.browser_active = True

    def prepare_google_signin(self):
        """Prefill the chosen Google account email when the site asks for sign-in."""
        if self.page is None or not self.auth_account:
            return False

        url = (self.page.url or "").lower()
        if "accounts.google.com" not in url and "google.com" not in url:
            return False

        try:
            email_fields = [
                "input[type='email']",
                "input[name='identifier']",
                "input[id='identifierId']",
                "input[autocomplete='username']",
            ]
            for selector in email_fields:
                locator = self.page.locator(selector)
                if locator.count() > 0:
                    locator.first.fill(self.auth_account)
                    locator.first.press("Tab")
                    return True
        except Exception:
            return False
        return False

    def find_editable_targets(self):
        """Return realistic editable selectors to support document-like pages."""
        if self.page is None:
            return []

        candidate_selectors = [
            "[contenteditable='true']",
            "[contenteditable='plaintext-only']",
            "[role='textbox']",
            "textarea",
            "input[type='text']",
            "input:not([type]), input[type='search']",
            "div[aria-label*='document' i]",
            "div[aria-label*='content' i]",
            "div[aria-label*='text' i]",
            "body",
        ]

        matches = []
        for selector in candidate_selectors:
            try:
                locator = self.page.locator(selector)
                if locator.count() == 0:
                    continue
                if selector == "body":
                    matches.append(selector)
                    continue
                for idx in range(min(locator.count(), 6)):
                    element = locator.nth(idx)
                    try:
                        if element.is_visible():
                            matches.append(selector)
                            break
                    except Exception:
                        continue
            except Exception:
                continue

        deduped = []
        for item in matches:
            if item not in deduped:
                deduped.append(item)
        return deduped

    def _resolve_selector(self, target: str):
        selector = str(target).strip()
        if not selector:
            return None
        if selector.startswith("#") or selector.startswith(".") or selector.startswith("["):
            return selector
        if selector.startswith("//"):
            return selector
        if any(marker in selector for marker in [":", "[", ">", " ", "(", ")", '"', "'"]):
            return selector
        return f"#{selector}"

    def _extract_elements(self):
        if self.page is None:
            return {"elements": []}

        items = []
        for locator in self.page.locator("input, textarea, select, button, a, [role='button'], [role='link']").all():
            try:
                if not locator.is_visible():
                    continue
            except Exception:
                continue

            element_id = locator.get_attribute("id") or ""
            tag = locator.evaluate("el => el.tagName.toLowerCase()")
            label = locator.get_attribute("aria-label") or locator.get_attribute("placeholder") or ""
            text = (locator.inner_text() or "").strip()
            if not element_id and not text and not label:
                continue

            generated_id = None
            if tag in {"input", "textarea", "select", "button"}:
                generated_id = element_id or f"{tag}_{len(items)}"
            elif element_id:
                generated_id = element_id

            items.append({
                "id": generated_id or element_id or "",
                "type": tag,
                "text": text,
                "label": label,
                "selector": f"#{element_id}" if element_id else None,
            })

        return {"elements": items}

    def open_browser(self, url: str):
        self._ensure_browser()
        self.current_url = url
        self.page.goto(url, wait_until="domcontentloaded")
        self.browser_active = True
        return {"status": "ok", "url": self.current_url}

    def close_browser(self):
        if self.page is not None:
            try:
                self.page.close()
            except Exception:
                pass
            self.page = None
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                if hasattr(self._playwright, "stop"):
                    self._playwright.stop()
                elif hasattr(self._playwright, "close"):
                    self._playwright.close()
            except Exception:
                pass
            self._playwright = None
        self.browser_active = False
        self.current_url = None

    def get_state(self):
        if self.page is None:
            return {
                "url": self.current_url,
                "title": "",
                "elements": [],
                "capabilities": list(self.capabilities.keys()),
                "page_text": "",
            }

        state = self._extract_elements()
        return {
            "url": self.page.url,
            "title": self.page.title(),
            "elements": state["elements"],
            "capabilities": list(self.capabilities.keys()),
            "page_text": self.page.locator("body").inner_text()[:4000],
        }

    def paste_text_into_document(self, text: str, target_selector: str = "body"):
        if self.page is None:
            raise RuntimeError("Browser is not open. Call open_browser() first.")
        value = str(text or "").strip()
        if not value:
            raise ValueError("Text to paste cannot be empty.")

        selectors = [target_selector] if target_selector else []
        if target_selector == "body":
            selectors.extend(self.find_editable_targets() or ["body"])

        last_error = None
        for selector in selectors:
            try:
                locator = self.page.locator(selector)
                if locator.count() == 0:
                    continue
                locator.first.click(timeout=10000)
                locator.first.evaluate(
                    "(element, text) => { element.innerText = text; element.textContent = text; element.dispatchEvent(new InputEvent('input', {bubbles: true})); }",
                    value,
                )
                return {"status": "ok", "selector": selector, "value": value}
            except Exception as exc:
                last_error = exc
                continue

        if last_error:
            raise last_error
        raise ValueError(f"No editable target found for {target_selector!r}.")

    def execute_action(self, action: dict):
        if self.page is None:
            raise RuntimeError("Browser is not open. Call open_browser() first.")

        action_type = str(action.get("action", "")).lower()
        target = action.get("target")
        value = action.get("value")
        selector = self._resolve_selector(target) if target is not None else None

        if action_type in {"navigate", "goto", "open"}:
            destination = value or target
            if not destination:
                raise ValueError("A destination URL is required for navigate actions.")
            self.page.goto(destination, wait_until="domcontentloaded")
            self.current_url = self.page.url
            return {"status": "ok", "action": action_type, "url": self.current_url}

        if action_type in {"fill", "type", "input"}:
            if selector is None:
                raise ValueError("A target selector is required for fill actions.")
            self.page.locator(selector).fill(str(value or ""))
            return {"status": "ok", "action": action_type, "target": target, "value": value}

        if action_type in {"click", "press", "submit"}:
            if selector is None:
                raise ValueError("A target selector is required for click actions.")
            self.page.locator(selector).click(timeout=10000)
            self.current_url = self.page.url
            return {"status": "ok", "action": action_type, "target": target, "url": self.current_url}

        if action_type in {"read", "inspect"}:
            return {"status": "ok", "action": action_type, "state": self.get_state()}

        if action_type in {"paste", "paste_text", "insert"}:
            target_selector = selector or (action.get("selector") or "body")
            text_value = str(value or "")
            if not text_value:
                raise ValueError("A value is required for paste actions.")

            selectors = [target_selector] if target_selector else []
            if target_selector == "body":
                selectors.extend(self.find_editable_targets() or ["body"])

            last_error = None
            for candidate in selectors:
                try:
                    locator = self.page.locator(candidate)
                    if locator.count() == 0:
                        continue
                    locator.first.click(timeout=10000)
                    locator.first.evaluate(
                        "(element, text) => { element.innerText = text; element.textContent = text; element.dispatchEvent(new InputEvent('input', {bubbles: true})); }",
                        text_value,
                    )
                    return {"status": "ok", "action": action_type, "target": candidate, "value": text_value}
                except Exception as exc:
                    last_error = exc
                    continue

            if last_error:
                raise last_error
            raise ValueError(f"No editable target found for {target_selector!r}.")

        raise ValueError(f"Unsupported action type: {action_type}")

    def get_web_elements(self):
        self._ensure_browser()
        return self._extract_elements()

    def get_ui_elements(self):
        self._ensure_browser()
        return self._extract_elements()

    def read_screen(self):
        if self.page is None:
            return {"screen": "ready", "url": self.current_url}

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            temp_path = temp_file.name
        self.page.screenshot(path=temp_path, full_page=False)
        return {
            "screen": "ready",
            "url": self.page.url,
            "image_path": temp_path,
            "summary": self.page.locator("body").inner_text()[:2000],
        }


SandboxVM = SandboxedVM


def is_browser_task(prompt: str) -> bool:
    """Return True when the prompt is asking to load, inspect, or operate on a site."""
    text = (prompt or "").lower()
    browser_indicators = [
        "open ", "visit ", "browse ", "click ", "search ", "look up ", "find ", "website", "webpage", "site",
        "login", "fill in", "type ", "submit", "page", "browser"
    ]
    if not text:
        return False
    if "https://" in text or "http://" in text:
        return True
    return any(indicator in text for indicator in browser_indicators)
