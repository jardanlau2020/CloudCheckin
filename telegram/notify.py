import http.client
import html
import urllib.parse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# follow the instructions from https://core.telegram.org/bots/features#botfather and get the bot token
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '').strip()
# add the bot to your contact and send a message to it
# then check the url https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates to get the chat id
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '').strip()

BEIJING_TIMEZONE = timezone(timedelta(hours=8))
NOTIFICATION_SENT_MARKER = "/tmp/cloudcheckin-telegram-notified"
TELEGRAM_MESSAGE_LIMIT = 4000
TRUNCATION_SUFFIX = "\n... [message truncated]"
HTTP_ERROR_DETAIL_LIMIT = 240


def summarize_http_failure(status_code, response_text):
    """Return a compact, notification-safe summary of an HTTP failure."""
    text = str(response_text or "").strip()
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)

    if title_match:
        detail = html.unescape(title_match.group(1))
        detail = re.sub(r"\s+", " ", detail).strip()
    elif text.startswith("<"):
        detail = "HTML error response"
    else:
        detail = re.sub(r"\s+", " ", text).strip()

    if len(detail) > HTTP_ERROR_DETAIL_LIMIT:
        detail = detail[:HTTP_ERROR_DETAIL_LIMIT - 3].rstrip() + "..."

    summary = f"HTTP {status_code}"
    return f"{summary}: {detail}" if detail else summary


CHECKIN_SUCCESS_BOOL_KEYS = (
    "success", "ok", "checked", "checked_in", "already", "already_checked",
    "signed", "is_success", "checkin", "attendance",
)
CHECKIN_SUCCESS_MARKERS = (
    "success", "checked in", "already checked", "already signed",
    "check-in successful", "checkin successful", "签到成功", "已签到", "已打卡",
)
CHECKIN_LOGIN_MARKERS = (
    "需要先登录", "请先登录", "未登录", "登录已失效", "cookie is expired",
    "login required", "please log in", "please login", "not logged in",
    "sign in to continue", "unauthorized", "invalid cookie",
)


def classify_checkin_result(status_code, response_text):
    """把一次签到响应判成 success / already / failure / unknown。

    2026-11-23 修假成功：HTTP 200 只说明请求通了 —— cookie 失效时站点常以 200
    返回登录页或 ``{"success": false}``，旧实现一律当成功。这里先看状态码，
    再解析响应体：JSON 布尔字段 > 登录失效文案 > 成功/已签文案 > 其余判 unknown
    （宁可报未知，也不把没签到当成功）。

    Returns:
        tuple[str, str]: (verdict, detail)，verdict ∈ {success, already, failure, unknown}
    """
    text = str(response_text or "")
    low = text.lower()

    if status_code != 200:
        return "failure", summarize_http_failure(status_code, text)

    # cookie 失效时常见：200 + HTML 登录页
    if low.lstrip().startswith("<"):
        return "failure", "HTML response (cookie expired or redirected to login page)"

    data = None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        data = None

    if isinstance(data, dict):
        for key in CHECKIN_SUCCESS_BOOL_KEYS:
            value = data.get(key)
            if isinstance(value, bool):
                return ("success" if value else "failure"), f"{key}={value}"
        nested = data.get("data")
        if isinstance(nested, dict):
            for key in CHECKIN_SUCCESS_BOOL_KEYS:
                value = nested.get(key)
                if isinstance(value, bool):
                    return ("success" if value else "failure"), f"data.{key}={value}"
        message = str(data.get("message") or data.get("msg") or data.get("error") or "").strip()
        if message:
            low = f"{low} {message.lower()}"

    if any(marker in low for marker in CHECKIN_LOGIN_MARKERS):
        return "failure", "response indicates an expired login / cookie"
    if "already" in low or "已签到" in low or "已打卡" in low:
        return "already", "already checked in"
    if any(marker in low for marker in CHECKIN_SUCCESS_MARKERS):
        return "success", "response body confirms the check-in"

    snippet = " ".join(text.split())[:HTTP_ERROR_DETAIL_LIMIT]
    return "unknown", f"HTTP 200 but no success field in body: {snippet or '(empty)'}"


def format_source_notification(source, messages, now=None):
    """Format one compact Telegram notification for a platform run."""
    if isinstance(messages, str):
        messages = [messages]

    body = "\n".join(
        str(message).strip()
        for message in messages
        if message is not None and str(message).strip()
    )
    if not body:
        body = "No result was reported."

    timestamp = now or datetime.now(BEIJING_TIMEZONE)
    notification = (
        f"{timestamp.strftime('%Y/%m/%d %H:%M:%S')}\n{source.upper()}\n{body}"
    )
    if len(notification) > TELEGRAM_MESSAGE_LIMIT:
        notification = (
            notification[:TELEGRAM_MESSAGE_LIMIT - len(TRUNCATION_SUFFIX)]
            + TRUNCATION_SUFFIX
        )
    return notification


def send_source_notification(source, messages):
    """Send exactly one formatted notification for a platform run."""
    send_tg_notification(format_source_notification(source, messages))


def send_tg_notification(message):
    """Send Telegram notification
    
    Args:
        message: The message to send
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram configuration is incomplete, cannot send notification", flush=True)
        sys.exit(1)
    
    # build the request parameters
    params = urllib.parse.urlencode({
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message
    })
    
    # create HTTPS connection
    conn = http.client.HTTPSConnection("api.telegram.org")
    
    try:
        # send GET request
        conn.request(
            "GET", 
            f"/bot{TELEGRAM_TOKEN}/sendMessage?{params}"
        )
        
        # get response
        response = conn.getresponse()
        data = response.read().decode('utf-8')
        
        # parse JSON response
        result = json.loads(data)
        
        # handle response
        if response.status == 200 and result.get('ok'):
            print("Notification sent successfully", flush=True)
            try:
                with open(NOTIFICATION_SENT_MARKER, "a", encoding="utf-8"):
                    pass
            except OSError as marker_error:
                print(
                    f"Could not record notification delivery: {marker_error}",
                    flush=True,
                )
        else:
            print(f"Notification sent failed: {response.status} - {data}", flush=True)
            raise Exception(f"Notification sent failed: {response.status} - {data}")
            
    except Exception as e:
        print(f"Notification sending process error: {str(e)}", flush=True)
        sys.exit(1)
    finally:
        # ensure connection is closed
        conn.close()

if __name__ == "__main__":
    send_source_notification("TELEGRAM", "Action test")
