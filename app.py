import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer

from server import AppHandler


def main():
    server = ThreadingHTTPServer(("0.0.0.0", 8000), AppHandler)
    print("Starting JARVIS app on http://localhost:8000")

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    webbrowser.open("http://localhost:8000")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down JARVIS app...")
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
