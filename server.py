import json
import os
import re
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import parse, request, error

from context_memory import summarize_document_context
from jarvis import SYSTEM_PROMPT

ROOT = Path(__file__).resolve().parent


def load_gemini_key():
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if not line or line.strip().startswith("#"):
                continue
            if "=" in line:
                key, value = [part.strip() for part in line.split("=", 1)]
                if key == "GEMINI_API_KEY":
                    return value
    return os.getenv("GEMINI_API_KEY")


def call_gemini(prompt: str, api_key: str | None = None, context: list | None = None) -> str:
    key = api_key or load_gemini_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing. Add it to the local .env file or save it in the app input.")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.6-flash:generateContent?key="
        f"{key}"
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

    with request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))

    candidate = data["candidates"][0]
    return candidate["content"]["parts"][0]["text"]


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        parsed = parse.urlparse(self.path)
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

                reply = call_gemini(prompt, api_key=api_key, context=context)
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
