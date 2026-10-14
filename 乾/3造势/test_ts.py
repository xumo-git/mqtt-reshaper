"""Offline tests for the @#ts effective-timestamp builtin.

No broker needed: feeds payloads straight into reshape(). Covers the kxMQTT
per-object time offset (field "2" is a millisecond delta from PVs.2), which is
what ~49% of real captures carry.
"""
import json
import os
import sys
from datetime import datetime

RUN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "2运势")
sys.path.insert(0, RUN)
sys.stdout.reconfigure(encoding="utf-8")

from kio_reshaper import _shift_ms, reshape   # noqa: E402

CFG = json.load(open(os.path.join(RUN, "reshape.json"), encoding="utf-8"))
# 规则名会被人改（如 kxmqtt多对象拆分 -> IOSERVER转发），按形状找而不是按名字找
_rules = list((CFG.get("rule_library") or {}).values())
RULES = [r for r in _rules if (r.get("match") or "").startswith("@PNs")][:1] or _rules[:1]

BASE = "2026-10-12 14:13:47.649"
ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print("  PASS  %s" % label)
    else:
        fail += 1
        print("  FAIL  %s\n          got  %r\n          want %r" % (label, got, want))


def payload(objs, pv1=0, pv2=BASE, pv3=192):
    return {"PNs": {"1": "V", "2": "T", "3": "Q"},
            "PVs": {"1": pv1, "2": pv2, "3": pv3},
            "Objs": objs}


print("== _shift_ms ==")
check("正偏移 +205ms", _shift_ms(BASE, 205), "2026-10-12 14:13:47.854")
check("负偏移 -20969ms (跨分)", _shift_ms(BASE, -20969), "2026-10-12 14:13:26.680")
check("负偏移 -6551ms", _shift_ms(BASE, -6551), "2026-10-12 14:13:41.098")
check("零偏移", _shift_ms(BASE, 0), "2026-10-12 14:13:47.649")
check("无小数秒格式", _shift_ms("2026-10-12 14:13:47", 1500), "2026-10-12 14:13:48")
check("非法串返回 None", _shift_ms("not-a-time", 10), None)

print("\n== @#ts 进 reshape ==")
doc = payload([
    {"N": "pub3200", "1": 0, "2": -20969, "3": 0},
    {"N": "pub3201"},                                  # 无 2 -> 用基准
    {"N": "pub3202", "2": 205},
])
msgs = reshape(doc, RULES, True)
check("拆出条数", len(msgs), 3)
check("带偏移的取偏移后", msgs[0]["DataTime"], "2026-10-12 14:13:26.680")
check("无偏移字段用基准", msgs[1]["DataTime"], BASE)
check("只有 2 无 1 -> 值回落基准", msgs[1]["DataValue"], 0)
check("正偏移", msgs[2]["DataTime"], "2026-10-12 14:13:47.854")
check("质量戳取对象自己的", msgs[0]["DataQuality"], 0)
check("质量戳省略时回落基准", msgs[1]["DataQuality"], 192)
check("变量名", [m["DataName"] for m in msgs], ["pub3200", "pub3201", "pub3202"])

print("\n== 值的回落 (payloadC 真实形状) ==")
doc = payload([{"N": "pub10"}, {"1": 0, "N": "pub11"}, {"1": 15, "N": "pub14"}],
              pv1=1616, pv2="2026-10-09 17:16:47.569")
msgs = reshape(doc, RULES, True)
check("pub10 省略 1 -> 取 PVs.1", msgs[0]["DataValue"], 1616)
check("pub11 自己的值", msgs[1]["DataValue"], 0)
check("pub14 自己的值", msgs[2]["DataValue"], 15)
check("三个的时间戳都等于基准(都无 2)",
      [m["DataTime"] for m in msgs], ["2026-10-09 17:16:47.569"] * 3)

print("\n== 无 PVs.2 -> 回落当前时间 ==")
msgs = reshape({"PNs": {"1": "V", "2": "T", "3": "Q"}, "Objs": [{"N": "x"}]}, RULES, True)
check("能出时间", "DataTime" in msgs[0], True)
try:
    datetime.strptime(msgs[0]["DataTime"], "%Y-%m-%d %H:%M:%S.%f")
    check("时间格式合法", True, True)
except Exception as e:
    check("时间格式合法", str(e), "")

print("\n== 基准串坏掉但有偏移 -> 退回基准串而不是报错 ==")
doc = payload([{"N": "x", "2": -100}], pv2="not-a-time")
msgs = reshape(doc, RULES, True)
check("退回原串", msgs[0]["DataTime"], "not-a-time")

print("\n== 偏移跨日 ==")
doc = payload([{"N": "x", "2": -90000}], pv2="2026-10-12 00:00:30.000")
msgs = reshape(doc, RULES, True)
check("跨到前一天", msgs[0]["DataTime"], "2026-10-11 23:59:00.000")

print("\n%d 项,失败 %d" % (ok + fail, fail))
sys.exit(1 if fail else 0)
