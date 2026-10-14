"""动作记录：增删改留痕 + 与报文记录分离。

报文记录继续分段分时；动作记录独立单文件、不分段，记 broker / 方案 / 规则配置 /
数据库 的新增修改删除，以及连接运行事件。
"""
import os
import shutil
import sys
import tempfile
import time

RUN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "2运势")
sys.path.insert(0, RUN)
sys.stdout.reconfigure(encoding="utf-8")

import kio_store
from kio_store import ActionLog, Store
from kio_webui import diff_config, log_config_diff, _op_of

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print("  PASS  %s" % label)
    else:
        fail += 1
        print("  FAIL  %s\n          got  %r\n          want %r" % (label, got, want))


tmp = tempfile.mkdtemp(prefix="kio_act_")
al = ActionLog(path=os.path.join(tmp, "actions.db"))

print("== 动作记录基本写入与查询 ==")
al.log(kind="broker", op="新增", target="本地EMQX", detail="127.0.0.1:1883")
al.log(kind="规则配置", op="修改", target="IOSERVER直发", detail="改动项：match")
al.log(kind="方案", op="删除", target="方案二", level="warn")
al.log(kind="数据库", op="清除", target="全部数据库", level="warn")
al.log(kind="连接", op="连接", target="方案一", text="src 已连接 127.0.0.1:1883")
time.sleep(1.2)
rows = al.query(limit=50)
check("写入 5 条", len(rows), 5)
check("默认按时间倒序", rows[0]["kind"], "连接")
check("字段齐全", sorted(rows[-1].keys()),
      ['detail', 'kind', 'level', 'op', 'route', 'target', 'text', 'ts'])

print("\n== 按类别 / 动作 / 级别筛选 ==")
check("类别筛选", [r["kind"] for r in al.query(kind="规则配置")], ["规则配置"])
check("动作筛选", [r["op"] for r in al.query(op="新增")], ["新增"])
check("级别筛选", len(al.query(level="warn")), 2)
check("关键词命中对象名", [r["target"] for r in al.query(keyword="本地EMQX")], ["本地EMQX"])
check("关键词命中详情", len(al.query(keyword="改动项")), 1)
check("组合筛选", len(al.query(kind="数据库", op="清除")), 1)

print("\n== 统计 ==")
c = al.counts()
check("总数", c["actions"], 5)
check("分类统计", c["by_kind"],
      {"broker": 1, "规则配置": 1, "方案": 1, "数据库": 1, "连接": 1})

print("\n== diff_config：broker 增删改 ==")
old = {"brokers": {"A": {"host": "1.1.1.1", "port": 1883},
                   "B": {"host": "2.2.2.2", "port": 1883}},
       "rule_library": {"R1": {"match": "@PNs", "template": {"a": "@PVs.1"}}},
       "routes": [{"name": "方案一", "from": {"broker": "A"}, "targets": []}]}
new = {"brokers": {"A": {"host": "1.1.1.1", "port": 8883},          # 改端口
                   "C": {"host": "3.3.3.3", "port": 1883}},         # 新增
       "rule_library": {"R1": {"match": "@PNs", "template": {"a": "@#ts"}},
                        "R2": {"match": "@/tag1", "template": {}}},  # 新增
       "routes": [{"name": "方案一", "from": {"broker": "C"}, "targets": [{}]},
                  {"name": "方案三", "from": {"broker": "C"}, "targets": []}]}  # 新增
d = diff_config(old, new)
by = {(k, o): t for k, o, t, _ in d}
check("broker 改了 A", ("broker", "修改") in by, True)
check("broker 删了 B", ("broker", "删除") in by, True)
check("broker 增了 C", ("broker", "新增") in by, True)
check("规则配置 增了 R2", ("规则配置", "新增") in by, True)
check("规则配置 改了 R1", ("规则配置", "修改") in by, True)
check("方案 改了 方案一", ("方案", "修改") in by, True)
check("方案 增了 方案三", ("方案", "新增") in by, True)
check("共 7 处改动", len(d), 7)
mod = [x for x in d if x[0] == "broker" and x[1] == "修改"][0]
check("改动项写清楚了字段", mod[3], "改动项：port")

print("\n== diff_config：完全没改时不产生记录 ==")
check("同配置零改动", diff_config(old, old), [])

print("\n== diff_config：界面回传的 has_password 不算改动 ==")
# 真实流程：服务端会先把密码补回去再对比，所以两边的 password 是一样的，
# 界面只是多带了一个 has_password 展示字段
svr_side = {"brokers": {"A": {"host": "1.1.1.1", "port": 1883, "password": "secret"}},
            "rule_library": {}, "routes": []}
ui_side = {"brokers": {"A": {"host": "1.1.1.1", "port": 1883,
                             "password": "secret", "has_password": True}},
           "rule_library": {}, "routes": []}
check("只有 has_password 差异 -> 零改动", diff_config(svr_side, ui_side), [])
pw_changed = {"brokers": {"A": {"host": "1.1.1.1", "port": 1883,
                                "password": "other", "has_password": True}},
              "rule_library": {}, "routes": []}
d2 = diff_config(svr_side, pw_changed)
check("真改了密码 -> 记一条", len(d2), 1)
check("只写字段名不泄值", d2[0][3], "改动项：password")
check("详情里不含密码原文",
      "secret" not in d2[0][3] and "other" not in d2[0][3], True)

print("\n== log_config_diff 写进动作库 ==")
before = al.counts()["actions"]
log_config_diff(old, new, acts=al)     # 传自己的库，别写到真实 data/ 去
time.sleep(1.2)
check("每处改动一条", al.counts()["actions"] - before, 7)
dele = [r for r in al.query(op="删除", limit=20) if r["kind"] == "broker"]
check("删除记为 warn", dele[0]["level"], "warn")
cre = [r for r in al.query(op="新增", limit=20) if r["kind"] == "broker"]
check("新增记为 info", cre[0]["level"], "info")

print("\n== 连接类事件的动作归类 ==")
check("断开", _op_of("[方案一] dst 连接断开"), "断开")
check("连接", _op_of("[方案一] dst 已连接"), "连接")
check("启动", _op_of("=== reshaper start ==="), "启动")
check("失败", _op_of("source connect error"), "失败")
check("兜底", _op_of("随便一句话"), "事件")

print("\n== 报文记录里不再写事件 ==")
# 顺带守一条：用临时目录建 Store 时，绝不能碰真实 data/ 里的段文件
real = os.path.join(os.path.dirname(os.path.dirname(RUN)), "坤")


def snap(d):
    try:
        return sorted((n, os.path.getsize(os.path.join(d, n)))
                      for n in os.listdir(d) if n.endswith(".db"))
    except OSError:
        return []


before_real = snap(real)
st = Store(data_dir=os.path.join(tmp, "msgs"), max_mb=500, segment_days=1)
check("临时 Store 不动真实 data/", snap(real), before_real)
for i in range(20):
    st.log_message(time.time() + i * 0.001, "方案一", "out", "MES", "r%d" % i, "{}")
time.sleep(1.2)
check("报文照常写入", st.counts()["messages"], 20)
seg = st.segments()[0]
import sqlite3
con = sqlite3.connect(os.path.join(tmp, "msgs", seg["name"]))
tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
con.close()
check("段里没有 events 表", "events" in tabs, False)
check("段里有 messages 表", "messages" in tabs, True)

print("\n== 旧段里的 events 迁进动作库 ==")
al2 = ActionLog(path=os.path.join(tmp, "actions2.db"))
legacy_dir = os.path.join(tmp, "legacy")
os.makedirs(legacy_dir, exist_ok=True)
con = sqlite3.connect(os.path.join(legacy_dir, "2026-10-10-01.db"))
con.executescript(
    "CREATE TABLE messages(id INTEGER PRIMARY KEY, ts REAL);"
    "CREATE TABLE events(id INTEGER PRIMARY KEY, ts REAL, level TEXT, route TEXT, text TEXT);")
con.executemany("INSERT INTO events(ts,level,route,text) VALUES (?,?,?,?)",
                [(1.0, "info", "方案一", "旧事件一"), (2.0, "warn", "", "旧事件二")])
con.commit(); con.close()
moved = al2.migrate_from_segments(legacy_dir)
check("迁移了 2 条", moved, 2)
texts = [r["text"] for r in al2.query(limit=20)]
check("旧事件一在", "旧事件一" in texts, True)
check("旧事件二在", "旧事件二" in texts, True)
check("另记一条迁移说明", any("迁移" in t for t in texts), True)
check("不再重复迁移", al2.migrate_from_segments(legacy_dir), 0)

print("\n== 清空动作记录 ==")
okc, msg = al2.clear()
check("清空成功", okc, True)
check("清空后为 0", al2.counts()["actions"], 0)

al.close(); al2.close(); st.close()
shutil.rmtree(tmp, ignore_errors=True)

print("\n%d 项,失败 %d" % (ok + fail, fail))
sys.exit(1 if fail else 0)
