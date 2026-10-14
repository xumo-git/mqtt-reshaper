"""模拟测试按规则试算（不再绕道方案）。

测模板就是测规则——所以离线转换测试直接勾规则，规则名解析、顺序、
匹配条件不命中时不出结果，这些都要守着。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

RUN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "2运势")
sys.path.insert(0, RUN)
sys.stdout.reconfigure(encoding="utf-8")

from kio_webui import _named_rules   # noqa: E402

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print("  PASS  %s" % label)
    else:
        fail += 1
        print("  FAIL  %s\n          got  %r\n          want %r" % (label, got, want))


CFG = json.load(open(os.path.join(RUN, "reshape.json"), encoding="utf-8"))
LIB = CFG.get("rule_library") or {}
NAME_FWD = next(n for n, r in LIB.items() if (r.get("match") or "").startswith("@PNs"))
NAME_DIR = next(n for n, r in LIB.items() if not (r.get("match") or "").startswith("@PNs"))

print("== 规则名解析 ==")
r = _named_rules(CFG, [NAME_FWD])
check("按名字取到一条", len(r), 1)
check("带 name 字段", r[0].get("name"), NAME_FWD)
check("match 带出来了", r[0].get("match") == LIB[NAME_FWD]["match"], True)

print("\n== 顺序保持（多条规则按顺序套用）==")
r = _named_rules(CFG, [NAME_DIR, NAME_FWD])
check("顺序 = 传入顺序", [x["name"] for x in r], [NAME_DIR, NAME_FWD])
r2 = _named_rules(CFG, [NAME_FWD, NAME_DIR])
check("反过来也一样", [x["name"] for x in r2], [NAME_FWD, NAME_DIR])

print("\n== 不存在的规则 ==")
check("未知名被丢弃", _named_rules(CFG, ["不存在的规则"]), [])
check("混合时只留存在的", [x["name"] for x in _named_rules(CFG, [NAME_FWD, "XX"])], [NAME_FWD])
check("空列表", _named_rules(CFG, []), [])
check("None 安全", _named_rules(CFG, None), [])

print("\n== 线上接口按规则试算 ==")
BASE = "http://127.0.0.1:8090"


def call(path, payload=None):
    if payload is None:
        req = urllib.request.Request(BASE + path)
    else:
        req = urllib.request.Request(
            BASE + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


FWD_DOC = {"PNs": {"1": "V", "2": "T", "3": "Q"},
           "PVs": {"1": 1616, "2": "2026-10-09 17:16:47.569", "3": 192},
           "Objs": [{"N": "pub10"}, {"1": 0, "N": "pub11"}]}
DIR_DOC = {("tag%d" % i): i for i in range(1, 6)}

try:
    r = call("/api/test", {"payload": FWD_DOC, "rules": [NAME_FWD]})
    check("只用转发规则 -> 拆 2 条", r["count"], 2)
    check("回传用了哪些规则", r["rules"], [NAME_FWD])

    r = call("/api/test", {"payload": DIR_DOC, "rules": [NAME_DIR]})
    check("只用直发规则 -> 拆 5 条", r["count"], 5)
    check("直发规则名", r["rules"], [NAME_DIR])

    # 报文和规则不匹配时，不该硬出结果
    r = call("/api/test", {"payload": FWD_DOC, "rules": [NAME_DIR]})
    check("规则匹配不上 -> 0 条", r["count"], 0)

    r = call("/api/test", {"payload": FWD_DOC, "rules": [NAME_FWD, NAME_DIR]})
    check("两条都勾 -> 只有匹配的那条出结果", r["count"], 2)
    check("回传两条规则名", r["rules"], [NAME_FWD, NAME_DIR])

    try:
        call("/api/test", {"payload": FWD_DOC, "rules": ["没这规则"]})
        check("未知规则名报错", False, True)
    except urllib.error.HTTPError as e:
        check("未知规则名报错", e.code, 400)
except urllib.error.URLError:
    print("  (WebUI 没起，跳过线上接口部分)")

print("\n%d 项,失败 %d" % (ok + fail, fail))
sys.exit(1 if fail else 0)
