"""Offline tests for segmented storage: rotation, naming, connect/disconnect, purge.

Runs in a throwaway directory -- never touches the real data/.
"""
import os
import shutil
import sys
import tempfile
import time
from datetime import date, datetime, timedelta

RUN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "2运势")
sys.path.insert(0, RUN)
sys.stdout.reconfigure(encoding="utf-8")

import kio_store
from kio_store import Store

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print("  PASS  %s" % label)
    else:
        fail += 1
        print("  FAIL  %s\n          got  %r\n          want %r" % (label, got, want))


def rows(n, t0):
    for i in range(n):
        yield (t0 + i * 0.001, "方案一", "out", "MES", "r%d" % i, '{"i":%d}' % i)


tmp = tempfile.mkdtemp(prefix="kio_seg_")
kio_store.DEFAULT_DB = os.path.join(tmp, "kio_history.db")

print("== 基本建段与命名 ==")
st = Store(data_dir=os.path.join(tmp, "data"), max_mb=500, segment_days=1)
segs = st.segments()
check("初始只有一个段", len(segs), 1)
check("命名 yyyy-mm-dd-NN.db", segs[0]["name"],
      "%s-01.db" % date.today().strftime("%Y-%m-%d"))
check("当前段即写入段", segs[0]["current"], True)
check("初始段已连接", segs[0]["connected"], True)
check("数据目录在 data/", os.path.basename(st.data_dir), "data")

print("\n== 写入与跨段查询 ==")
t0 = time.time()
for row in rows(50, t0):
    st.log_message(*row)
time.sleep(1.2)
c = st.counts()
check("报文写入 50 条", c["messages"], 50)
check("跨段查到全部", len(st.query_messages(limit=200)), 50)
check("按方向过滤", len(st.query_messages(direction="out", limit=200)), 50)
check("关键词过滤", len(st.query_messages(keyword='"i":7', limit=200)), 1)
check("按 before 翻页", len(st.query_messages(before=t0 + 0.01, limit=200)), 10)

print("\n== 段上限触发轮转 ==")
st2 = Store(data_dir=os.path.join(tmp, "data2"), max_mb=1, segment_days=1)
for i in range(40):
    for row in rows(500, time.time()):
        st2.log_message(*row)
    time.sleep(0.05)
time.sleep(1.5)
n = len(st2.segments())
check("写爆后产生多个段", n > 1, True)
names = [s["name"] for s in st2.segments()]
check("序号连续递增", names,
      sorted(names))
check("都符合命名", all(len(x) == len("yyyy-mm-dd-01.db") for x in names), True)
check("只有一个是当前段", sum(1 for s in st2.segments() if s["current"]), 1)

print("\n== 连接 / 断开 ==")
cur = [s for s in st2.segments() if s["current"]][0]
old = [s for s in st2.segments() if not s["current"]][0]
check("归档段默认也连接(历史可查)", old["connected"], True)
base = st2.counts()["messages"]
check("断开前历史可见", base > old["messages"], True)
okc, msg = st2.disconnect(old["name"])
check("归档段可断开", okc, True)
check("断开后查询不再命中它", st2.counts()["messages"], base - old["messages"])
check("断开后查不到它的行",
      len(st2.query_messages(limit=100000)), base - old["messages"])
okc, msg = st2.connect(old["name"])
check("可重新连接", okc, True)
check("连回后条数复原", st2.counts()["messages"], base)
check("连回后历史又可见",
      len(st2.query_messages(limit=100000)), base)
okc, msg = st2.disconnect(cur["name"])
check("当前段拒绝断开", okc, False)
okc, msg = st2.delete(cur["name"])
check("当前段拒绝删除", okc, False)

print("\n== 手动清除 ==")
old2 = [s for s in st2.segments() if not s["current"]]
if old2:
    target = old2[0]["name"]
    okc, msg = st2.delete(target)
    check("归档段可删除", okc, True)
    check("删除后文件已消失",
          os.path.exists(os.path.join(st2.data_dir, target)), False)
    check("段列表同步减少", target not in [s["name"] for s in st2.segments()], True)

n0 = len([s for s in st2.segments() if not s["current"]])
okc, msg = st2.purge_oldest(2)
check("删最旧 N 个库", len([s for s in st2.segments() if not s["current"]]),
      max(0, n0 - 2))

okc, msg = st2.clear_current()
check("清空当前段成功", okc, True)
cur2 = [s for s in st2.segments() if s["current"]][0]
check("当前段数据清空", cur2["messages"], 0)
check("当前段文件还在",
      os.path.exists(os.path.join(st2.data_dir, cur2["name"])), True)

okc, msg = st2.purge_all()
check("清空全部成功", okc, True)
check("清空后只剩一个新段", len(st2.segments()), 1)
check("旧文件全没了",
      len([n for n in os.listdir(st2.data_dir) if n.endswith('.db')]), 1)

print("\n== 设置可改 ==")
s = st2.set_settings(max_mb=250, segment_days=7)
check("段上限已改", s["max_mb"], 250)
check("段周期已改", s["segment_days"], 7)
check("下限保护", st2.set_settings(max_mb=0, segment_days=0)["max_mb"], 1)

print("\n== 旧单文件迁移 ==")
legacy = os.path.join(tmp, "kio_history.db")
import sqlite3
con = sqlite3.connect(legacy)
con.executescript(kio_store._SCHEMA)
when = datetime(2026, 10, 10, 12, 0, 0).timestamp()
con.execute("INSERT INTO messages(ts,route,direction,topic,raw_id,body) "
            "VALUES (?,?,?,?,?,?)", (when, "方案一", "in", "kingscada", "r1", "{}"))
con.commit()
con.close()
st3 = Store(data_dir=os.path.join(tmp, "data3"), max_mb=500, segment_days=1)
names3 = [s["name"] for s in st3.segments()]
check("旧库被迁进 data/", any(x.startswith("2026-10-10") for x in names3), True)
check("按最早记录日期命名", "2026-10-10-01.db" in names3, True)
check("旧库原位置已清空", os.path.exists(legacy), False)
check("迁移的数据还在",
      st3.query_messages(limit=10)[0]["route"], "方案一")

st.close(); st2.close(); st3.close()
shutil.rmtree(tmp, ignore_errors=True)

print("\n%d 项,失败 %d" % (ok + fail, fail))
sys.exit(1 if fail else 0)
