import os
import json
import base64
import requests
import psycopg2
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="")

DATABASE_URL = os.environ.get("DATABASE_URL")
BACKUP_STORAGE_KEY = "company-data-v1"

# 브레보(Brevo) 메일 발송 정보: Render 대시보드의 Environment 탭에서 설정 (코드에는 절대 값 자체를 넣지 않음)
# BREVO_API_KEY: 브레보 SMTP & API > API Keys 에서 발급받은 키 (xkeysib- 로 시작)
# BREVO_SENDER_EMAIL: 브레보 Senders 메뉴에서 인증 완료한 발신 이메일 주소
BREVO_API_KEY = os.environ.get("BREVO_API_KEY")
BREVO_SENDER_EMAIL = os.environ.get("BREVO_SENDER_EMAIL")
BREVO_SENDER_NAME = os.environ.get("BREVO_SENDER_NAME", "HR Portal")

# 매일 자동 백업 메일 발송용 설정
# BACKUP_SECRET: 아무나 이 주소를 호출해서 백업 메일을 못 보내게 막는 비밀 값 (예약 작업에서만 사용)
# BACKUP_EMAIL_TO: 백업 파일을 받을 이메일 주소
BACKUP_SECRET = os.environ.get("BACKUP_SECRET")
BACKUP_EMAIL_TO = os.environ.get("BACKUP_EMAIL_TO")


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
    if not BREVO_API_KEY or not BREVO_SENDER_EMAIL:
        return jsonify({"error": "email not configured on server"}), 500

    try:
        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "api-key": BREVO_API_KEY,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "sender": {"email": BREVO_SENDER_EMAIL, "name": BREVO_SENDER_NAME},
                "to": [{"email": to_addr}],
                "subject": subject,
                "textContent": text,
            },
            timeout=10,
        )
        if resp.status_code >= 400:
            return jsonify({"error": resp.text}), 500
        return jsonify({"sent": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/backup-email", methods=["GET", "POST"])
def backup_email():
    # 비밀 값이 일치할 때만 동작 (매일 자동 실행되는 예약 작업 전용, 사람이 직접 누르는 버튼 아님)
    key = request.args.get("key")
    if not key and request.method == "POST":
        key = (request.get_json(force=True, silent=True) or {}).get("key")
    if not BACKUP_SECRET or key != BACKUP_SECRET:
        return jsonify({"error": "unauthorized"}), 401
    if not BREVO_API_KEY or not BREVO_SENDER_EMAIL or not BACKUP_EMAIL_TO:
        return jsonify({"error": "backup email not configured on server"}), 500

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM storage WHERE key = %s", (BACKUP_STORAGE_KEY,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        return jsonify({"error": "no data to back up"}), 404

    try:
        company_data = json.loads(row[0])
    except Exception:
        company_data = row[0]

    now = datetime.now(timezone.utc)
    date_stamp = now.strftime("%Y-%m-%d")
    backup_obj = {
        "exportedAt": now.isoformat(),
        "exportedBy": "자동 백업 (매일 06:00)",
        "data": company_data,
    }
    backup_json = json.dumps(backup_obj, ensure_ascii=False, indent=2)
    attachment_b64 = base64.b64encode(backup_json.encode("utf-8")).decode("ascii")

    try:
        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "api-key": BREVO_API_KEY,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "sender": {"email": BREVO_SENDER_EMAIL, "name": BREVO_SENDER_NAME},
                "to": [{"email": BACKUP_EMAIL_TO}],
                "subject": f"[HR Portal] 전체 데이터 자동 백업 ({date_stamp})",
                "textContent": "매일 자동으로 만들어지는 HR 포털 전체 데이터 백업이에요. 첨부된 JSON 파일을 안전한 곳에 보관해두세요.",
                "attachment": [{
                    "content": attachment_b64,
                    "name": f"hr-portal-backup_{date_stamp}.json",
                }],
            },
            timeout=20,
        )
        if resp.status_code >= 400:
            return jsonify({"error": resp.text}), 500
        return jsonify({"sent": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
