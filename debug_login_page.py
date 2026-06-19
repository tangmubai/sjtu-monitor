"""dump jaccount 登录页 HTML 用于调试。"""
import requests
import config

s = requests.Session()
s.headers["User-Agent"] = config.USER_AGENT

r = s.get(config.INDEX_URL, allow_redirects=True, timeout=15)
print("=== FINAL URL ===")
print(r.url)
print("=== STATUS ===", r.status_code)
print("=== HTML LEN ===", len(r.text))
print("=== HTML (first 4000 chars) ===")
print(r.text[:4000])
print("=== HTML (search for captcha / uuid / hidden) ===")
import re
for pat in [r'captcha[^"\'\s>]{0,80}', r'uuid[^"\'\s>]{0,80}', r'<input[^>]+hidden[^>]*>', r'name=["\']\w+["\']']:
    matches = re.findall(pat, r.text, re.IGNORECASE)[:10]
    print(f"  pattern {pat!r}: {matches}")
