"""Exercise the new /api/ping endpoint across success and failure paths.

Credentials are read from reshape.json at runtime — nothing is hardcoded here.
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')

# __file__ = <根>\乾\3造势\test_ping.py；reshape.json 和模块都在 乾\2运势
RUN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '2运势')
HERE = RUN
CFG = json.load(open(os.path.join(HERE, 'reshape.json'), encoding='utf-8'))
B = ((CFG.get('brokers') or {}).get('本地EMQX')) or next(iter((CFG.get('brokers') or {}).values()))
HOST, PORT = B.get('host', '127.0.0.1'), B.get('port', 1883)
USER, PASS = B.get('username'), B.get('password')

URL = 'http://127.0.0.1:8090/api/ping'


def ping(**body):
    req = urllib.request.Request(
        URL,
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8'))


CASES = [
    ('正确的账号密码', dict(host=HOST, port=PORT, username=USER, password=PASS, timeout=5), True),
    ('错误密码', dict(host=HOST, port=PORT, username=USER, password='definitely-wrong', timeout=5), False),
    ('端口未监听', dict(host=HOST, port=int(PORT) + 1, username=USER, password=PASS, timeout=5), False),
    ('域名解析失败', dict(host='no-such-host.invalid', port=PORT, username=USER, password='x', timeout=5), False),
    ('缺少 host', dict(host='', port=PORT, timeout=5), 'http-4xx'),
    ('匿名(不带账号)', dict(host=HOST, port=PORT, timeout=5), None),
]

bad = 0
for label, body, want in CASES:
    code, r = ping(**body)
    ok = r.get('ok')
    if want == 'http-4xx':
        passed = code >= 400 and bool(r.get('error') or r.get('message'))
    elif want is None:
        passed = code in (200, 400, 500)
    else:
        passed = code == 200 and ok is want
    if not passed:
        bad += 1
    print('%-14s %s  HTTP %s  ok=%-5s stage=%-7s connack=%-4s  %s' % (
        label, 'PASS' if passed else 'FAIL', code, ok,
        r.get('stage'), r.get('code'), r.get('message') or r.get('error')))
    print('%18s elapsed=%sms  client_id=%s' % ('', r.get('elapsed_ms'), r.get('client_id')))

print('\n%d 项,失败 %d' % (len(CASES), bad))
sys.exit(1 if bad else 0)
