def retry_delay(attempt, base):
    return base * (2 ** attempt)

def fetch(url, attempt):
    while True:
        try:
            return request(url)
        except Exception:
            time.sleep(retry_delay(attempt, 1))
