from api import app
import config
import socket


def _get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


if __name__ == "__main__":
    host = "0.0.0.0"
    local_ip = _get_local_ip()
    print(f"Server starting at http://{local_ip}:{config.PORT} (listening on {host})")
    app.run(host=host, port=config.PORT, debug=config.DEBUG)