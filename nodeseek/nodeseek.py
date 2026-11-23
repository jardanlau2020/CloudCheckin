import os
from curl_cffi import requests
import random
import time
from dotenv import load_dotenv
from telegram.notify import classify_checkin_result, send_source_notification

load_dotenv()

# Get COOKIE from environment variable, multiple cookies separated by &
cookies = os.environ.get('NODESEEK_COOKIE', '').strip()

# Split multiple cookies by & to form a list
cookie_list = cookies.split('&')

# Request headers
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0',
    'Origin': 'https://www.nodeseek.com',
    'Referer': 'https://www.nodeseek.com/board',
    'Content-Type': 'application/json',
}


def main():
    results = []
    failed = False

    if not cookies:
        results.append("Configuration error: NODESEEK_COOKIE is not set")
        send_source_notification("NODESEEK", results)
        return 1

    for idx, cookie in enumerate(cookie_list):
        account = idx + 1
        print(f"Using account {account} for check-in...", flush=True)
        random_delay = random.randint(1, 20)
        print(f"Account {account} will wait for {random_delay} seconds...", flush=True)
        time.sleep(random_delay)
        headers['Cookie'] = cookie.strip()

        try:
            url = 'https://www.nodeseek.com/api/attendance?random=true'
            response = requests.post(url, headers=headers, impersonate="chrome136")
            print(f"Account {account} status code: {response.status_code}", flush=True)
            print(f"Account {account} response: {response.text}", flush=True)

            # 2026-11-23 修假成功：不再「HTTP 200 即成功」。
            # 200 之后再解析响应体（JSON 布尔字段 / 成功或已签文案），解析不出就判 UNKNOWN。
            verdict, detail = classify_checkin_result(response.status_code, response.text)
            if verdict == "success":
                result = f"Account {account}: check-in successful"
            elif verdict == "already":
                result = f"Account {account}: already checked in today"
            elif verdict == "failure":
                result = f"Account {account}: check-in failed — {detail}"
                failed = True
            else:
                result = f"Account {account}: check-in UNKNOWN — {detail}"
                failed = True
        except Exception as exc:
            result = f"Account {account}: check-in error — {exc}"
            failed = True

        print(result, flush=True)
        results.append(result)

    send_source_notification("NODESEEK", results)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
