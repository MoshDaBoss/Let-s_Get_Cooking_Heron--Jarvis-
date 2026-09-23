import base64
import json
import os
import re
import sys
import tempfile
from email.mime.text import MIMEText
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import parse, request, error

from context_memory import summarize_document_context
from jarvis import BrowserAgent, MemoryManager, SandboxedVM, SYSTEM_PROMPT, is_browser_task

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
WEB_ROOT = ROOT / "_internal" if getattr(sys, "frozen", False) and (ROOT / "_internal").exists() else ROOT
MEMORY_MANAGER = MemoryManager(ROOT / "jarvis.db")
DEFAULT_GEMINI_MODELS = (
    "gemini-3.6-flash",
)


def get_gemini_model() -> str:
    configured = (os.getenv("GEMINI_MODEL") or "").strip()
    if configured:
        return configured
    return DEFAULT_GEMINI_MODELS[0]


def extract_gemini_text(response_body: dict | object) -> str:
    """Normalize Gemini SDK/REST responses into plain assistant text."""
    if not isinstance(response_body, dict):
        return str(response_body or "")

    if "candidates" in response_body:
        candidates = response_body.get("candidates") or []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content")
            if isinstance(content, dict):
                parts = content.get("parts") or []
                texts: list[str] = []
                tool_calls: list[str] = []
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    function_call = part.get("functionCall")
                    if isinstance(function_call, dict):
                        name = function_call.get("name") or "tool_call"
                        tool_calls.append(str(name))
                    text = part.get("text")
                    if text:
                        texts.append(str(text))
                if texts:
                    return "\n".join(texts)
                if tool_calls:
                    return (
                        "I’m not using browser tools for this request. "
                        "Please ask a direct question or a browser task explicitly, and I’ll answer normally."
                    )

    if "text" in response_body and isinstance(response_body.get("text"), str):
        return response_body["text"]

    if "reply" in response_body and isinstance(response_body.get("reply"), str):
        return response_body["reply"]

    if "content" in response_body and isinstance(response_body.get("content"), str):
        return response_body["content"]

    return str(response_body)


def extract_openai_text(response_body: dict | object) -> str:
    if not isinstance(response_body, dict):
        return str(response_body or "")
    choices = response_body.get("choices") or []
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        if isinstance(message, dict) and message.get("content"):
            return str(message["content"])
    return ""


def build_memory_context(memory_manager: MemoryManager, limit_per_branch: int = 5) -> list[str]:
    """Create a chat-safe memory summary that is injected into the prompt."""
    context_entries: list[str] = []

    for branch in MemoryManager.VALID_BRANCHES:
        items = memory_manager.recall("", branch=branch, limit=limit_per_branch)
        for item in items:
            content = str(item.get("content", "")).strip()
            if content:
                context_entries.append(f"[{branch}] {content}")

    return context_entries


def strip_doc_context(context: list | None) -> list[str]:
    """Remove document-specific context from non-document prompts so stale doc summaries do not poison general questions."""
    if not context:
        return []

    cleaned: list[str] = []
    for item in context:
        if not isinstance(item, str):
            continue
        normalized = item.lower()
        if (
            "docs.google.com/document" in normalized
            or "google doc" in normalized
            or "google docs" in normalized
            or "the raven" in normalized
            or "lenore" in normalized
            or "poem identified" in normalized
            or "poem:" in normalized
        ):
            continue
        cleaned.append(item)
    return cleaned


def parse_doc_paste_task(prompt: str) -> dict:
    """Detect a Google Doc paste task and extract target URL plus content to paste."""
    text = (prompt or "").strip()
    if not text:
        return {"target_kind": "none", "url": "", "text": ""}

    url_match = re.search(r"https?://[^\s\)\]>]+", text, flags=re.IGNORECASE)
    url = url_match.group(0).strip() if url_match else ""
    is_google_doc = "docs.google.com/document" in url.lower()
    target_kind = "google_doc" if is_google_doc else "website" if url else "text"

    lower_text = text.lower()
    is_paste_task = any(keyword in lower_text for keyword in ["paste", "into the", "into this", "paste into", "insert into"])

    if is_google_doc and is_paste_task:
        quoted = re.findall(r'"([^"]+)"|\'([^\']+)\'', text)
        if quoted:
            extracted = next((part for group in quoted for part in group if part), "")
            if extracted:
                return {"target_kind": target_kind, "url": url, "text": extracted.strip()}

        trailing_text = re.sub(r".*?(paste|into the|into this|paste into|insert into).*?\b(?:google doc|google docs|document)\b\s*", "", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"^\s*[-:;,.]+\s*", "", trailing_text).strip()
        if cleaned:
            return {"target_kind": target_kind, "url": url, "text": cleaned}

    if is_google_doc and not is_paste_task:
        return {"target_kind": "website", "url": url, "text": text}

    if is_google_doc and url:
        return {"target_kind": target_kind, "url": url, "text": text}

    return {"target_kind": target_kind, "url": url, "text": text}


def build_dom_paste_plan(prompt: str, auth_account: str | None = None) -> dict:
    """Build a DOM-first paste plan before any browser automation attempt."""
    task = parse_doc_paste_task(prompt)
    if task["target_kind"] != "google_doc" or not task["url"]:
        return {
            "target_kind": "none",
            "url": "",
            "text": "",
            "mode": "not_applicable",
            "editable_targets": [],
            "message": "This prompt is not a Google Doc paste task.",
        }

    editable_targets = [
        "#docs-editor-container",
        "div[contenteditable='true']",
        "[role='textbox']",
        "textarea",
        "body",
    ]

    return {
        "target_kind": task["target_kind"],
        "url": task["url"],
        "text": task["text"],
        "mode": "dom_first_google_doc",
        "editable_targets": editable_targets,
        "auth_account": (auth_account or "").strip() or None,
        "message": (
            "Google Doc detected. The browser will inspect the document DOM and insert the prepared text into the first editable target. The text is ready_to_paste for the document flow."
        ),
    }


def fetch_google_doc_export_text(document_url: str | None) -> str:
    """Fetch the actual text payload for a Google Doc via the export endpoint."""
    url = (document_url or "").strip()
    if not url:
        return ""

    match = re.search(r"/document/d/([^/?#]+)", url, flags=re.IGNORECASE)
    if not match:
        return ""

    doc_id = match.group(1)
    export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"

    try:
        req = request.Request(
            export_url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/plain, */*;q=0.8",
            },
        )
        with request.urlopen(req, timeout=15) as response:
            payload = response.read().decode("utf-8", errors="replace")
            return payload.strip()
    except Exception:
        return ""


def read_google_doc_summary(document_url: str | None) -> dict:
    """Read a Google Doc by exporting its text and falling back to browser DOM only if needed."""
    url = (document_url or "").strip()
    if not url:
        return {"status": "error", "message": "A document URL is required."}

    export_text = fetch_google_doc_export_text(url)
    if export_text:
        title = "Google Doc"
        if "docs.google.com/document" in url.lower():
            title = "Google Doc"
        summary = re.sub(r"\s+", " ", export_text)[:1800].strip()
        return {
            "status": "ok",
            "url": url,
            "title": title,
            "summary": summary or "The document opened successfully but no readable text could be extracted.",
        }

    sandbox = SandboxedVM()
    try:
        sandbox.open_browser(url)
        page = sandbox.page
        if page is None:
            return {"status": "error", "message": "The browser could not load the document."}

        title = (page.title() or "Google Doc").strip() or "Google Doc"
        body_text = (page.locator("body").inner_text() or "").strip()
        summary = re.sub(r"\s+", " ", body_text)[:1800].strip()
        return {
            "status": "ok",
            "url": page.url,
            "title": title,
            "summary": summary or "The document opened successfully but no readable text could be extracted.",
        }
    except Exception as exc:
        return {"status": "error", "message": f"Unable to read the document: {exc}"}
    finally:
        sandbox.close_browser()


def answer_google_doc_question(prompt: str, document_text: str, api_key: str | None = None, context: list | None = None) -> str:
    """Reason over the extracted document text and answer the user's question without echoing the full document."""
    question = (prompt or "").strip()
    text = (document_text or "").strip()
    if not text:
        return "I couldn’t read any useful content from that document."

    cleaned = re.sub(r"\s+", " ", text)[:12000]

    try:
        analysis_prompt = (
            "Use only the document text below to answer the user's question. "
            "Answer in one or two complete, natural-sounding sentences. "
            "If the user asks what is in the file, briefly summarize its subject and do not guess a title, genre, or event that is not explicit. "
            "Do not combine unrelated phrases or repeat fragments from the document. "
            "Do not paste the entire document back into chat. Keep the answer concise and grounded in the text. "
            f"User question: {question}\n\nDocument text:\n{cleaned}"
        )
        answer = call_ai(analysis_prompt, api_key=api_key, context=context)
        if answer and answer.strip():
            return answer.strip()
    except Exception:
        pass

    title_match = re.search(r"(?i)([A-Z][A-Za-z0-9'’\- ]{2,80})\s+(?:by|BY)\s+([A-Z][A-Za-z .'-]+)", cleaned[:500])
    if title_match:
        title = f"{title_match.group(1).strip()} by {title_match.group(2).strip()}"
    else:
        title = "Google Doc"
        first_line_match = re.search(r"(?i)(?:^|\s)([A-Z][A-Za-z0-9'’\- ]{3,80})", cleaned[:300])
        if first_line_match:
            title = first_line_match.group(1).strip()

    lower_question = question.lower()
    if "what poem" in lower_question or "which poem" in lower_question or "what is this poem" in lower_question:
        if title != "Google Doc":
            return f"This appears to be {title}."

    if title != "Google Doc":
        return f"This seems to be {title}."

    return "I could read the document, but I need a clearer question to identify it confidently."


def run_google_doc_paste(prompt: str, auth_account: str | None = None):
    """Use the DOM-first browser flow to paste into a Google Doc without bypassing the app's browser abstraction."""
    task = parse_doc_paste_task(prompt)
    if task["target_kind"] != "google_doc" or not task["url"]:
        return None

    plan = build_dom_paste_plan(prompt, auth_account=auth_account)
    safe_account = (auth_account or "default").strip() or "default"
    profile_name = re.sub(r"[^a-zA-Z0-9._-]", "-", safe_account).lower() or "default"
    profile_dir = ROOT / "browser_profiles" / profile_name
    profile_dir.mkdir(parents=True, exist_ok=True)

    sandbox = SandboxedVM(profile_dir=str(profile_dir), auth_account=auth_account)
    sandbox.open_browser(task["url"])

    if "accounts.google.com" in (sandbox.page.url or "").lower() or "google.com" in (sandbox.page.url or "").lower() and "signin" in (sandbox.page.url or "").lower():
        sandbox.prepare_google_signin()
        return {
            "status": "pending_login",
            "mode": "dom_first_google_doc",
            "url": task["url"],
            "account": auth_account,
            "message": "The browser was opened with the selected Google account and the email was prefilled. Please finish the in-browser sign-in, then retry the paste.",
        }

    doc_text = str(task.get("text") or "").strip()
    if not doc_text:
        doc_text = "The user requested a paste, but no text payload was found."

    selector_candidates = list(plan.get("editable_targets") or []) + (sandbox.find_editable_targets() or [])

    for selector in dict.fromkeys(selector_candidates):
        try:
            result = sandbox.paste_text_into_document(doc_text, target_selector=selector)
            if result.get("status") == "ok":
                return {"status": "ok", "mode": "dom_first_google_doc", "url": task["url"], "text": doc_text}
        except Exception:
            continue

    return {"status": "error", "mode": "dom_first_google_doc", "url": task["url"], "message": "Unable to locate the editable document area in the DOM."}


def read_dotenv_values() -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if not line or line.strip().startswith("#"):
                continue
            if "=" in line:
                key, value = [part.strip() for part in line.split("=", 1)]
                values[key] = value
    return values


def get_env_value(name: str) -> str | None:
    values = read_dotenv_values()
    if name in values:
        return values[name]
    return os.getenv(name)


def load_gemini_key():
    return get_env_value("GEMINI_API_KEY")


def build_google_auth_url() -> str:
    client_id = get_env_value("GOOGLE_CLIENT_ID") or ""
    if not client_id:
        return ""

    redirect_uri = (
        get_env_value("GOOGLE_REDIRECT_URI")
        or os.getenv("GOOGLE_REDIRECT_URI")
        or "http://localhost:8000/api/google-auth/callback"
    )
    scopes = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/userinfo.email",
    ]
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + parse.urlencode(params)


def load_google_oauth_tokens() -> dict:
    token_path = ROOT / "google_oauth_tokens.json"
    if not token_path.exists():
        return {}
    try:
        return json.loads(token_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def parse_email_task(prompt: str) -> dict:
    """Extract recipient, subject, and body from an explicit email or Gmail prompt."""
    text = (prompt or "").strip()
    if not text:
        return {"recipient": "", "subject": "", "body": "", "kind": "email"}

    recipient_match = re.search(r"(?:to|send|email|mail)\s+([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", text, flags=re.IGNORECASE)
    recipient = recipient_match.group(1) if recipient_match else ""

    body_match = re.search(r"(?:with|saying|message|body|text)\s+(.*)$", text, flags=re.IGNORECASE)
    body = body_match.group(1).strip() if body_match else text
    if recipient:
        body = re.sub(rf"(?:to|send|email|mail)\s+{re.escape(recipient)}\s*", "", body, flags=re.IGNORECASE)
        body = re.sub(r"\s+(?:with|saying|message|body|text)\s+", " ", body, flags=re.IGNORECASE).strip()
    body = body or "Hello!"

    subject = "Message from JARVIS"
    if "subject" in text.lower():
        subject_match = re.search(r"subject\s*[:=]\s*([^\n]+)", text, flags=re.IGNORECASE)
        if subject_match:
            subject = subject_match.group(1).strip()

    return {
        "recipient": recipient,
        "subject": subject,
        "body": body,
        "kind": "email",
    }


def send_gmail_message(recipient: str, subject: str, body: str, access_token: str | None = None) -> dict:
    """Send a Gmail message via the Gmail API using an OAuth access token."""
    token = (access_token or "").strip()
    if not token:
        return {"status": "error", "message": "Missing Gmail access token."}

    if not recipient:
        return {"status": "error", "message": "Missing recipient email address."}

    message = MIMEText(body or "", _charset="utf-8")
    message["To"] = recipient
    message["From"] = "me"
    message["Subject"] = subject or "Message from JARVIS"
    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    payload = {"raw": encoded}
    payload_json = json.dumps(payload).encode("utf-8")

    request_obj = request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=payload_json,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )

    try:
        with request.urlopen(request_obj, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
        return {"status": "ok", "message": "Email sent successfully.", "response": data}
    except Exception as exc:
        return {"status": "error", "message": f"Failed to send email: {exc}"}


def save_google_oauth_tokens(tokens: dict):
    token_path = ROOT / "google_oauth_tokens.json"
    token_path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")


def get_gmail_status() -> dict:
    client_id = get_env_value("GOOGLE_CLIENT_ID")
    client_secret = get_env_value("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        return {
            "status": "not_configured",
            "message": "Google OAuth is not configured yet. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to your .env file to enable free Gmail access.",
            "auth_url": "",
        }

    tokens = load_google_oauth_tokens()
    if not tokens or not tokens.get("access_token"):
        auth_url = build_google_auth_url()
        return {
            "status": "needs_auth",
            "message": "Google OAuth is configured. Start the Gmail sign-in flow to authorize access.",
            "auth_url": auth_url,
        }

    email = tokens.get("email") or "Google account"
    return {
        "status": "authorized",
        "message": f"Google OAuth is active for {email}.",
        "auth_url": "",
        "account": email,
    }


def classify_browser_route(prompt: str, api_key: str | None = None, context: list | None = None) -> dict:
    """Ask Gemini whether a prompt should go through the browser automation route."""
    text = (prompt or "").strip()
    if not text:
        return {"route": "chat", "source": "empty"}

    lower = text.lower()
    explicit_browser_keywords = [
        "use browser tools",
        "browser tools",
        "go to",
        "google",
        "wikipedia",
        "search",
        "browse",
        "visit",
        "click",
        "login",
        "email",
        "gmail",
        "open website",
        "open the website",
        "open a website",
        "website",
        "webpage",
        "site",
        "use the browser",
        "look up",
        "find me",
    ]
    if any(keyword in lower for keyword in explicit_browser_keywords):
        return {"route": "browser", "source": "explicit-keyword"}

    if is_browser_task(text):
        return {"route": "browser", "source": "heuristic"}

    classification_prompt = (
        "Decide if the user is asking for browsing or web interaction that requires browser automation. "
        "Return only valid JSON: {\"route\": \"browser\"|\"chat\", \"reason\": \"short reason\"}. "
        f"User prompt: {text}"
    )
    try:
        answer = call_ai(classification_prompt, api_key=api_key, context=context)
        if not answer:
            return {"route": "chat", "source": "empty-response"}
        parsed = json.loads(answer)
        if isinstance(parsed, dict):
            route = str(parsed.get("route", "chat")).strip().lower()
            if route in {"browser", "chat"}:
                return {"route": route, "source": "gemini", "reason": parsed.get("reason", "")}
    except Exception:
        pass

    return {"route": "chat", "source": "fallback"}


def infer_browser_target(prompt: str) -> str | None:
    """Infer the intended site for a browser task while keeping it generic."""
    text = (prompt or "").strip()
    if not text:
        return None
    lower = text.lower()

    site_map = {
        "gmail": "https://mail.google.com",
        "google": "https://www.google.com",
        "wikipedia": "https://www.wikipedia.org",
        "docs": "https://docs.google.com",
        "youtube": "https://www.youtube.com",
        "amazon": "https://www.amazon.com",
        "xeramail": "https://xeramail.com/",
    }

    for label, url in site_map.items():
        if label in lower:
            return url

    url_match = re.search(r"https?://[^\s]+", text, flags=re.IGNORECASE)
    if url_match:
        return url_match.group(0).strip()

    return None


def call_gemini(prompt: str, api_key: str | None = None, context: list | None = None) -> str:
    key = api_key or load_gemini_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing. Add it to the local .env file or save it in the app input.")

    models = []
    configured = (os.getenv("GEMINI_MODEL") or "").strip()
    if configured:
        models = [configured]
    else:
        models = list(DEFAULT_GEMINI_MODELS)

    errors: list[str] = []
    for model in models:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={key}"
        )

        context_text = ""
        if context:
            context_entries = [str(item).strip() for item in context if str(item).strip()]
            if context_entries:
                context_text = "\nContext attachments:\n" + "\n".join(f"- {item}" for item in context_entries)

        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"parts": [{"text": f"{prompt}{context_text}"}]}],
        }

        req = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))
            parsed_text = extract_gemini_text(data)
            if parsed_text and parsed_text != "{}":
                return parsed_text
            errors.append(f"{model}: empty response")
        except error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
                message = detail.get("error", {}).get("message", str(exc))
            except Exception:
                message = str(exc)
            errors.append(f"{model}: {message}")
            continue
        except Exception as exc:
            errors.append(f"{model}: {exc}")
            continue

    if not errors:
        raise RuntimeError("Gemini request failed without a response.")
    raise RuntimeError("Gemini request failed: " + "; ".join(errors))


def call_openai_compatible(prompt: str, api_key: str | None = None, context: list | None = None) -> str:
    key = api_key or os.getenv("AI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("AI_API_KEY is missing. Add it to the local .env file or save a key in the app input.")

    base_url = (os.getenv("AI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = (os.getenv("AI_MODEL") or "gpt-4o-mini").strip()
    context_text = ""
    if context:
        entries = [str(item).strip() for item in context if str(item).strip()]
        if entries:
            context_text = "\nContext attachments:\n" + "\n".join(f"- {item}" for item in entries)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{prompt}{context_text}"},
        ],
    }
    req = request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
        answer = extract_openai_text(data)
        if answer:
            return answer
        raise RuntimeError("empty response")
    except error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
            message = detail.get("error", {}).get("message", str(exc))
        except Exception:
            message = str(exc)
        raise RuntimeError(f"OpenAI-compatible request failed: {message}") from exc


def call_ai(prompt: str, api_key: str | None = None, context: list | None = None) -> str:
    provider = (os.getenv("AI_PROVIDER") or "gemini").strip().lower()
    if provider in {"openai", "openai_compatible", "openai-compatible"}:
        return call_openai_compatible(prompt, api_key=api_key, context=context)
    return call_gemini(prompt, api_key=api_key, context=context)


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def do_GET(self):
        parsed = parse.urlparse(self.path)

        if parsed.path == "/api/google-auth/start":
            try:
                status = get_gmail_status()
                if status["status"] == "not_configured":
                    self._send_json(400, status)
                    return
                self._send_json(200, {"status": "ok", "auth_url": status.get("auth_url") or build_google_auth_url()})
                return
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
                return

        if parsed.path == "/api/google-auth/status":
            try:
                self._send_json(200, get_gmail_status())
                return
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
                return

        if parsed.path == "/api/google-auth/callback":
            try:
                code = parse.parse_qs(parsed.query).get("code", [""])[0]
                if not code:
                    self._send_json(400, {"error": "Missing OAuth code."})
                    return

                client_id = get_env_value("GOOGLE_CLIENT_ID")
                client_secret = get_env_value("GOOGLE_CLIENT_SECRET")
                redirect_uri = (
                    get_env_value("GOOGLE_REDIRECT_URI")
                    or os.getenv("GOOGLE_REDIRECT_URI")
                    or "http://localhost:8000/api/google-auth/callback"
                )
                if not client_id or not client_secret:
                    self._send_json(400, {"error": "Google OAuth is not configured."})
                    return

                token_payload = parse.urlencode({
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                }).encode("utf-8")
                token_req = request.Request(
                    "https://oauth2.googleapis.com/token",
                    data=token_payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with request.urlopen(token_req, timeout=60) as token_response:
                    token_data = json.loads(token_response.read().decode("utf-8"))

                save_google_oauth_tokens(token_data)

                access_token = token_data.get("access_token")
                if access_token:
                    user_req = request.Request(
                        "https://www.googleapis.com/oauth2/v2/userinfo",
                        headers={"Authorization": f"Bearer {access_token}"},
                        method="GET",
                    )
                    try:
                        with request.urlopen(user_req, timeout=60) as response:
                            user_data = json.loads(response.read().decode("utf-8"))
                        token_data["email"] = user_data.get("email") or token_data.get("email")
                        save_google_oauth_tokens(token_data)
                    except Exception:
                        pass

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<html><body><h2>Google Gmail authorization complete.</h2><p>You can close this window and return to the app.</p></body></html>")
                return
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
                return

        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        parsed = parse.urlparse(self.path)

        if parsed.path == "/api/chat":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length)
                payload = json.loads(raw_body.decode("utf-8"))
                prompt = payload.get("prompt", "").strip()
                api_key = payload.get("api_key")
                context = payload.get("context", [])
                if not prompt:
                    self._send_json(400, {"error": "Prompt is required."})
                    return

                doc_task = parse_doc_paste_task(prompt)
                prompt_lower = (prompt or "").lower()
                doc_related = bool(doc_task["url"] and "docs.google.com/document" in doc_task["url"].lower())
                doc_request = (
                    doc_related
                    and any(keyword in prompt_lower for keyword in ["read", "summary", "document", "poem", "what", "which", "doc", "google doc", "google docs", "paste", "into the", "into this", "insert into"])
                )

                if doc_task["target_kind"] == "google_doc" and doc_task["url"] and any(keyword in prompt_lower for keyword in ["paste", "into the", "into this", "insert into"]):
                    auth_account = MEMORY_MANAGER.get_auth_account()
                    sandbox_result = run_google_doc_paste(prompt, auth_account=auth_account)
                    if sandbox_result and sandbox_result.get("status") == "ok":
                        self._send_json(200, {"reply": f"Pasted into the Google Doc at {sandbox_result['url']} with the requested text."})
                        return
                    if sandbox_result and sandbox_result.get("status") == "pending_login":
                        self._send_json(200, {"reply": sandbox_result["message"]})
                        return
                    if sandbox_result and sandbox_result.get("status") == "error":
                        self._send_json(200, {"reply": sandbox_result["message"]})
                        return

                    context = list(context) + [
                        f"Google Doc target: {doc_task['url']}",
                        f"Paste this text into the document: {doc_task['text']}",
                    ]
                    prompt = (
                        "The user wants this content pasted into the provided Google Doc. "
                        "Prepare the exact text to paste and keep it clean and ready for document insertion."
                    )
                elif doc_task["url"] and "docs.google.com/document" in doc_task["url"].lower() and doc_request:
                    summary = read_google_doc_summary(doc_task["url"])
                    if summary.get("status") == "ok":
                        reply_text = answer_google_doc_question(prompt, summary.get("summary", ""), api_key=api_key, context=context)
                        self._send_json(200, {"reply": reply_text})
                        return
                    self._send_json(200, {"reply": summary.get("message", "I couldn’t read the document.")})
                    return
                else:
                    context = strip_doc_context(context)

                memory_context = strip_doc_context(build_memory_context(MEMORY_MANAGER))
                if memory_context:
                    context = list(context) + memory_context

                email_task = parse_email_task(prompt)
                if email_task.get("recipient"):
                    tokens = load_google_oauth_tokens()
                    access_token = (tokens or {}).get("access_token")
                    if not access_token:
                        status = get_gmail_status()
                        self._send_json(200, {
                            "reply": f"Email sending requires Google authentication. {status.get('message', '')}",
                            "gmail_status": status,
                        })
                        return

                    result = send_gmail_message(
                        recipient=email_task["recipient"],
                        subject=email_task.get("subject") or "Message from JARVIS",
                        body=email_task.get("body") or "Hello!",
                        access_token=access_token,
                    )
                    if result.get("status") == "ok":
                        self._send_json(200, {
                            "reply": f"Sent an email to {email_task['recipient']} with the requested message.",
                            "gmail_result": result,
                        })
                    else:
                        self._send_json(200, {
                            "reply": result.get("message", "Unable to send the email."),
                            "gmail_result": result,
                        })
                    return

                route_decision = classify_browser_route(prompt, api_key=api_key, context=context)
                if route_decision.get("route") == "browser":
                    sandbox = SandboxedVM()
                    agent = BrowserAgent(sandbox=sandbox)
                    default_url = infer_browser_target(prompt)
                    agent_result = agent.run(prompt, url=default_url)
                    summary = agent_result.get("result", {}).get("page_text") or agent_result.get("result", {}).get("title") or "Browser task completed."
                    browser_reply = {
                        "reply": f"Browser task completed: {summary[:2000]}",
                        "browser_result": agent_result,
                    }
                    self._send_json(200, browser_reply)
                    return

                reply = call_ai(prompt, api_key=api_key, context=context)
                self._send_json(200, {"reply": reply})
            except error.HTTPError as exc:
                try:
                    detail = json.loads(exc.read().decode("utf-8"))
                    message = detail.get("error", {}).get("message", str(exc))
                except Exception:
                    message = str(exc)
                self._send_json(500, {"error": message})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if parsed.path == "/api/upload-context":
            try:
                content_type = self.headers.get("Content-Type", "")
                if "multipart/form-data" not in content_type:
                    self._send_json(400, {"error": "Expected multipart form-data upload."})
                    return

                boundary = content_type.split("boundary=")[-1]
                raw_body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                parts = raw_body.split(f"--{boundary}".encode("utf-8"))
                extracted = []

                for part in parts:
                    if not part or b"--" in part:
                        continue

                    headers, _, body = part.partition(b"\r\n\r\n")
                    if not body:
                        continue
                    if b"filename=\"" not in headers:
                        continue

                    header_text = headers.decode("utf-8", errors="ignore")
                    match = re.search(r'filename="([^"]+)"', header_text)
                    if not match:
                        continue
                    filename = match.group(1)
                    file_bytes = body.rstrip(b"\r\n")

                    with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix, delete=False) as temp_file:
                        temp_file.write(file_bytes)
                        temp_path = Path(temp_file.name)

                    info = summarize_document_context(temp_path)
                    extracted.append({
                        "name": filename,
                        "summary": info["summary"],
                        "image_summary": info["image_summary"],
                    })
                    temp_path.unlink(missing_ok=True)

                self._send_json(200, {"documents": extracted})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if parsed.path == "/api/memory":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length)
                payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}

                action = str(payload.get("action", "")).strip().lower()
                branch = payload.get("branch")
                content = payload.get("content")
                query = payload.get("query")
                command = payload.get("command")

                if command:
                    result = MEMORY_MANAGER.handle_command(command)
                elif action == "remember":
                    result = MEMORY_MANAGER.remember(branch or "working", content or "")
                elif action == "recall":
                    result = {"status": "ok", "branch": branch or "working", "query": query or content or "", "matches": MEMORY_MANAGER.recall(query or content or "", branch=branch or "working")}
                elif action == "forget":
                    result = MEMORY_MANAGER.forget(branch or "working", content or query)
                elif action in {"consolidate", "consolidare"}:
                    result = {"status": "ok", "summary": MEMORY_MANAGER.consolidate()}
                elif action == "auth_account":
                    result = {"status": "ok", "account": MEMORY_MANAGER.get_auth_account()}
                else:
                    raise ValueError("Unsupported memory action.")

                self._send_json(200, result)
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        self.send_error(404, "Not found")

    def _send_json(self, status_code: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 8000), AppHandler)
    print("JARVIS server running on http://localhost:8000")
    server.serve_forever()
