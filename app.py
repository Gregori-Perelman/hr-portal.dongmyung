import os
import json
import psycopg2
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="")

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_conn():
    # Render Postgres URL sometimes starts with postgres:// ; psycopg2 wants postgresql://
    url = DATABASE_URL
    if url and url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    conn = psycopg2.connect(url)
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS storage (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.commit()
    cur.close()
    conn.close()


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/storage/<path:key>", methods=["GET"])
def storage_get(key):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM storage WHERE key = %s", (key,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"key": key, "value": row[0]})


@app.route("/api/storage/<path:key>", methods=["POST"])
def storage_set(key):
    body = request.get_json(force=True, silent=True) or {}
    value = body.get("value")
    if value is None:
        return jsonify({"error": "value is required"}), 400
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO storage (key, value) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        (key, value),
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"key": key, "value": value})


@app.route("/api/storage/<path:key>", methods=["DELETE"])
def storage_delete(key):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM storage WHERE key = %s", (key,))
    deleted = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"key": key, "deleted": deleted})


@app.route("/api/storage", methods=["GET"])
def storage_list():
    prefix = request.args.get("prefix", "")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT key FROM storage WHERE key LIKE %s", (prefix + "%",))
    keys = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify({"keys": keys, "prefix": prefix})


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
