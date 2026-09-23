import os
import socket
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer

from server import AppHandler


def find_available_port(start_port: int = 8000, max_attempts: int = 20) -> int:
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port found from {start_port} to {start_port + max_attempts - 1}.")


def main():
    preferred_port = int(os.getenv("PORT", "8000"))
    try:
        port = find_available_port(preferred_port)
    except RuntimeError:
        port = find_available_port(9000)

    server = ThreadingHTTPServer(("0.0.0.0", port), AppHandler)
    print(f"Starting JARVIS app on http://localhost:{port}")

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    webbrowser.open(f"http://localhost:{port}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down JARVIS app...")
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
