"""Minimal JARVIS-inspired orchestration model for the repository."""

import os
from pathlib import Path


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
- First inspect the page with get_web_elements().
- Use get_ui_elements() only for form, control, or accessibility context.
- Use read_screen() as a visual fallback only.
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
    """Represent a secure env with browser and fallback interfaces."""

    def __init__(self):
        self.browser_active = False
        self.current_url = None
        self.capabilities = {
            "browser": True,
            "get_web_elements": True,
            "get_ui_elements": True,
            "read_screen": True,
        }

    def open_browser(self, url: str):
        self.current_url = url
        self.browser_active = True

    def close_browser(self):
        self.browser_active = False
        self.current_url = None

    def get_web_elements(self):
        return get_web_elements()

    def get_ui_elements(self):
        return get_ui_elements()

    def read_screen(self):
        return {"screen": "ready"}
