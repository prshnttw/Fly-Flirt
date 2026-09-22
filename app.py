"""Entry point.

Development:  python app.py
Production :  gunicorn -k gthread -w 1 --threads 100 -b 0.0.0.0:$PORT app:app
"""
from flyflirt import create_app, socketio

app = create_app()

if __name__ == "__main__":
    socketio.run(
        app,
        host="0.0.0.0",
        port=app.config["PORT"],
        debug=app.config.get("DEBUG", False),
        allow_unsafe_werkzeug=True,
    )
