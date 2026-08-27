import os
import json
import smtplib
from email.mime.text import MIMEText
import psycopg2
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="")

DATABASE_URL = os.environ.get("DATABASE_URL")

# 네이버 메일 발송 계정 정보: Render 대시보드의 Environment 탭에서 설정 (코드에는 절대 값 자체를 넣지 않음)
NAVER_EMAIL_USER = os.environ.get("NAVER_EMAIL_USER")
NAVER_EMAIL_APP_PASSWORD = os.environ.get("NAVER_EMAIL_APP_PASSWORD")


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


@app.route("/api/send-email", methods=["POST"])
def send_email():
    body = request.get_json(force=True, silent=True) or {}
    to_addr = body.get("to")
    subject = body.get("subject", "")
    text = body.get("text", "")

    if not to_addr:
        return jsonify({"error": "to is required"}), 400
    if not NAVER_EMAIL_USER or not NAVER_EMAIL_APP_PASSWORD:
        return jsonify({"error": "email not configured on server"}), 500

    try:
        msg = MIMEText(text, _charset="utf-8")
        msg["Subject"] = subject
        msg["From"] = NAVER_EMAIL_USER
        msg["To"] = to_addr

        with smtplib.SMTP("smtp.naver.com", 587) as server:
            server.starttls()
            server.login(NAVER_EMAIL_USER, NAVER_EMAIL_APP_PASSWORD)
            server.sendmail(NAVER_EMAIL_USER, [to_addr], msg.as_string())

        return jsonify({"sent": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
