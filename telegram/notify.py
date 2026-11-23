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


_SUCCESS_TEXT = ("成功", "已签到", "签到成功", "已领取", "success", "already")
_FAIL_TEXT = ("失败", "未登录", "请先登录", "expired", "error", "invalid", "forbidden")


def classify_checkin(status_code, response_text):
    """判定签到是否真的成功（2026-11-23）。

    只看 HTTP 200 不够：登录态失效时接口常回 200 + HTML，或 200 + 业务失败 JSON。
    返回 ``(ok, detail)``；无法确认时返回 ``(False, 原因)``，宁可告警也不假成功。
    """
    text = str(response_text or "").strip()
    if status_code != 200:
        return False, "HTTP %s" % status_code
    if not text:
        return False, "HTTP 200 但响应体为空，无法确认"
    if text.startswith("<"):
        return False, "HTTP 200 返回 HTML（登录态可能失效）"

    parsed = None
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        parsed = None

    if isinstance(parsed, dict):
        for key in ("success", "ok"):
            value = parsed.get(key)
            if isinstance(value, bool):
                return value, "响应体 %s=%s" % (key, value)
        code = parsed.get("code")
        if code in (0, "0", 200, "200"):
            return True, "响应体 code=%s" % code
        if code not in (None, ""):
            return False, "响应体 code=%s" % code
        message = str(parsed.get("message") or parsed.get("msg") or "").strip()
        if message:
            low = message.lower()
            if any(k in low for k in _SUCCESS_TEXT):
                return True, "响应体 message=%s" % message[:40]
            if any(k in low for k in _FAIL_TEXT):
                return False, "响应体 message=%s" % message[:40]

    low = text.lower()
    if any(k in low for k in _SUCCESS_TEXT):
        return True, "响应体含成功标记"
    if any(k in low for k in _FAIL_TEXT):
        return False, "响应体含失败标记: %s" % text[:80]
    return False, "HTTP 200 但无法确认成功: %s" % text[:80]


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
