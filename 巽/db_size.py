"""Measure real row size in the history DB and project how much fits in 1 GB."""
import os
import sqlite3

DB = r"C:\mqtt转发\kio_history.db"
TARGET_GB = 1.0

con = sqlite3.connect(DB)
con.execute("PRAGMA journal_mode=WAL")

print("=== 表规模 ===")
for t in ("messages", "events"):
    n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {n} 行")

print("\n=== 报文体积 ===")
row = con.execute("""
  SELECT COUNT(*),
         AVG(LENGTH(body)), MAX(LENGTH(body)), MIN(LENGTH(body)),
         SUM(LENGTH(body))
  FROM messages""").fetchone()
n, avg, mx, mn, total = row
print(f"  行数        {n}")
print(f"  body 平均   {avg:.0f} B   最大 {mx} B   最小 {mn} B")
print(f"  body 总计   {total/1024/1024:.2f} MB")

print("\n=== 按方向 ===")
for d in ("in", "out"):
    r = con.execute("SELECT COUNT(*), AVG(LENGTH(body)) FROM messages WHERE direction=?", (d,)).fetchone()
    if r[0]:
        print(f"  {d}: {r[0]} 行, 平均 {r[1]:.0f} B")

print("\n=== 磁盘占用 ===")
for suf in ("", "-wal", "-shm"):
    p = DB + suf
    if os.path.exists(p):
        print(f"  {os.path.basename(p):22s} {os.path.getsize(p)/1024/1024:.2f} MB")

db_mb = sum(os.path.getsize(DB + s) for s in ("", "-wal", "-shm") if os.path.exists(DB + s))
print(f"  合计 {db_mb/1024/1024:.2f} MB")

print("\n=== 1 GB 能装多少 ===")
# 每行实际开销: body + 索引 + rowid/时间/主题等字段 + SQLite 页开销
avg_body = avg or 0
overhead = 120          # id/ts/route/dir/topic/raw_id + 页碎片,经验值
per_row = avg_body + overhead
cap = TARGET_GB * 1024 * 1024 * 1024
rows = int(cap / per_row)
print(f"  每行约 {per_row:.0f} B  (body {avg_body:.0f} + 开销 {overhead})")
print(f"  1 GB ≈ {rows:,} 行")

# 按典型业务量推算
print("\n=== 按业务量折算 ===")
for objs in (10, 50, 100):
    per_raw = 1 + objs          # 1 条原始 + N 条转换
    # 原始报文更大(整包),转换结果小
    raw_size = 200 + objs * 22  # 估算
    out_size = 70
    cycle = raw_size + objs * out_size + per_raw * overhead
    cycles = cap / cycle
    print(f"  每包 {objs:3d} 对象: 一轮 {per_raw} 行 / {cycle/1024:.1f} KB  "
          f"-> 1 GB ≈ {cycles:,.0f} 轮")
    for hz, label in ((1/60, "每分钟1包"), (1, "每秒1包")):
        days = cycles / (hz * 86400)
        print(f"      {label:10s} 可存约 {days:,.1f} 天")

con.close()
