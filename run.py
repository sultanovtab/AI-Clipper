"""Start the loopback server and open the browser only after it is listening."""

import socket
import threading
import time
import webbrowser

import uvicorn

from app import app, PORT


if __name__ == "__main__":
    address = f"http://127.0.0.1:{PORT}"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", PORT))
    except OSError:
        print(f"Port {PORT} is already in use. Close the previous instance or check {address}.")
        raise SystemExit(1)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="info"))

    def open_browser():
        while not server.started and not server.should_exit:
            time.sleep(0.1)
        if server.started:
            webbrowser.open(address)

    threading.Thread(target=open_browser, daemon=True).start()
    print(f"AI Clipper Phase 1: {address}\nPress Ctrl+C to stop.")
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()
