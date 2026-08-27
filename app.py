from api import app
import config


if __name__ == "__main__":
    print(f"Server starting at http://localhost:{config.PORT}")
    app.run(host="127.0.0.1", port=config.PORT, debug=config.DEBUG)