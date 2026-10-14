"""Regression: 直发报文（扁平 tag）必须能按规则转换。

现场报文的键名是 tag1..tag20，不带前导斜杠；而规则原来写的是 @/tag1，
match 永远不成立，整条规则被跳过——既不转换也不发 MES。
"""
import json
import os
import sys

RUN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "2运势")
sys.path.insert(0, RUN)
sys.stdout.reconfigure(encoding="utf-8")

from kio_reshaper import reshape   # noqa: E402

HERE = RUN   # reshape.json 和模块都在 运行目录
CFG = json.load(open(os.path.join(HERE, "reshape.json"), encoding="utf-8"))
LIB = CFG["rule_library"]

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print("  PASS  %s" % label)
    else:
        fail += 1
        print("  FAIL  %s\n          got  %r\n          want %r" % (label, got, want))


# 现场抓到的直发报文原文（键名不带斜杠，且带多余空白）
DIRECT_LIVE = ('{"tag1": 0 ,"tag2": 0 ,"tag3": 0 ,"tag4": 0,    "tag5": 0,"tag6": 0,'
               '"tag7": 0,"tag8": 0,"tag9": 0,"tag10": 0,    "tag11": 0 ,"tag12": 0 ,'
               '"tag13": 0 ,"tag14": 0,    "tag15": 0,"tag16": 0,"tag17": 0,"tag18": 0,'
               '"tag19": 0,"tag20": 0}')
# 老文档/样例里的写法（带斜杠）
DIRECT_SLASH = json.dumps({("/tag%d" % i): i for i in range(1, 4)})
# 转发报文
FORWARD = {
    "PNs": {"1": "V", "2": "T", "3": "Q"},
    "PVs": {"1": 1616, "2": "2026-10-09 17:16:47.569", "3": 192},
    "Objs": [{"N": "pub10"}, {"1": 0, "N": "pub11"}, {"1": 15, "N": "pub14"}],
}

RULES = [LIB["IOSERVER转发"], LIB["IOSERVER直发"]]

print("== 直发：现场报文（tag1，无斜杠）==")
msgs = reshape(json.loads(DIRECT_LIVE), RULES, True)
check("扇出 20 条", len(msgs), 20)
check("字段名来自键", msgs[0]["DataName"], "tag1")
check("值来自值", msgs[0]["DataValue"], 0)
check("带转发规则标记", msgs[0]["转发规则"], "IOSERVER直发")
check("末条是 tag20", msgs[-1]["DataName"], "tag20")
check("有时间戳", "DataTime" in msgs[0], True)
check("没混进转发规则的产物",
      all("DataQuality" not in m for m in msgs), True)

print("\n== 直发：老写法（/tag1，带斜杠）也该命中 ==")
msgs = reshape(json.loads(DIRECT_SLASH), RULES, True)
check("扇出 3 条", len(msgs), 3)
check("键名原样输出", msgs[0]["DataName"], "/tag1")

print("\n== match 多选：任一命中即可 ==")
check("两个都不在则不命中", reshape({"other": 1}, RULES, True), [])
check("只有后半个也能命中",
      len(reshape({"/tag1": 5}, [LIB["IOSERVER直发"]], True)), 1)
check("只有前半个也能命中",
      len(reshape({"tag1": 5}, [LIB["IOSERVER直发"]], True)), 1)

print("\n== 转发规则不受影响 ==")
msgs = reshape(FORWARD, RULES, True)
check("扇出 3 条", len(msgs), 3)
check("pub10 值回落基准", msgs[0]["DataValue"], 1616)
check("pub14 取自己的值", msgs[2]["DataValue"], 15)
check("时间戳是 @#ts", msgs[0]["DataTime"], "2026-10-09 17:16:47.569")
check("带转发规则标记", msgs[0]["转发规则"], "IOSERVER转发")

print("\n== 两类报文混在一起时各走各的规则 ==")
msgs = reshape(json.loads(DIRECT_LIVE), RULES, True) + reshape(FORWARD, RULES, True)
check("合计 23 条", len(msgs), 23)
check("两类标记都在",
      sorted(set(m["转发规则"] for m in msgs)), ["IOSERVER直发", "IOSERVER转发"])

print("\n== 字段名也能取值（把变量名当输出的键）==")
# 用户要的形态：左边是变量名 pub11，右边是 pub11 的值
DYN = {"match": "@PNs", "each": "@Objs",
       "template": {"@#value.N": "@#value.1|PVs.1"}}
msgs = reshape(FORWARD, [DYN], True)
check("一个对象一条", len(msgs), 3)
check("键 = 变量名", [list(m)[0] for m in msgs], ["pub10", "pub11", "pub14"])
check("pub10 没有 1 -> 值回落 PVs.1", msgs[0]["pub10"], 1616)
check("pub11 自己的值", msgs[1]["pub11"], 0)
check("pub14 自己的值", msgs[2]["pub14"], 15)
check("没有多余的外层字段", len(msgs[1]), 1)

print("\n== 动态字段名的边角 ==")
# 键取不到时 omit_missing=True 会省掉这个字段。注意 omit_missing 是**字段级**语义，
# 整包一个字段都不剩时仍输出一条空对象——真实规则里总有字面量字段，走不到这。
DYN2 = {"match": "@PNs", "each": "@Objs", "template": {"@#value.XXX": "@#value.1"}}
check("键取不到 -> 字段省略", reshape(FORWARD, [DYN2], True), [{}, {}, {}])
# omit_missing=False 时才留位：键取不到变空串，值取不到变 None
check("键取不到且不省略 -> 空键占位",
      reshape(FORWARD, [DYN2], False), [{"": None}, {"": 0}, {"": 15}])
# 键也能 | 回落
DYN3 = {"match": "@PNs", "each": "@Objs",
        "template": {"@#value.N|@#value.other": "@#value.1|PVs.1"}}
check("键支持 | 回落", [list(m)[0] for m in reshape(FORWARD, [DYN3], True)],
      ["pub10", "pub11", "pub14"])
# 静态键和动态键混用
DYN4 = {"match": "@PNs", "each": "@Objs",
        "template": {"@#value.N": "@#value.1|PVs.1", "固定": "x"}}
m = reshape(FORWARD, [DYN4], True)[1]
check("静态动态混用", m, {"pub11": 0, "固定": "x"})

print("\n%d 项,失败 %d" % (ok + fail, fail))
sys.exit(1 if fail else 0)
