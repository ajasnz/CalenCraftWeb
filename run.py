from app.server import create_app

app = create_app()

if __name__ == "__main__":
    # Dev only — production uses gunicorn (see Dockerfile)
    app.run(debug=False, host="127.0.0.1", port=5001)
