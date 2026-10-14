"""Verify the segment size cap: writing past max_mb rotates to a new db.

分段之后「容量上限」的含义变了——不再是删行把文件压小（SQLite 删行不缩文件），
而是**写满就开新库**。这里验的就是轮转有没有真的发生、单库有没有被限制住。
"""
import os
import shutil
import sys
import tempfile
import time

RUN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "2运势")
sys.path.insert(0, RUN)
from kio_store import Store   # noqa: E402

tmp = tempfile.mkdtemp(prefix="kio_size_")
MAX_MB = 1

st = Store(data_dir=tmp, max_mb=MAX_MB, segment_days=365)

body = "x" * 400
N = 60000
print("写入大量数据 (单库上限 %d MB, 共 %d 行) ..." % (MAX_MB, N))
for i in range(N):
    st.log_message(time.time() - (N - i) * 0.01, "方案一", "out", "MES", "m%d" % i, body)
    if i % 400 == 0:
        st._flush()          # 队列每次只取 500 条，得反复刷
st._flush()
time.sleep(1.0)

segs = st.segments()
c = st.counts()
print("  段数 %d  合计 %s MB  行数 %d" % (len(segs), c["total_mb"], c["messages"]))
for s in segs:
    print("    %-24s %7.2f MB  %8d 条" % (s["name"], s["size_mb"], s["messages"]))

# 断言：确实切了段，且单库没失控（WAL 会有余量，给 30% 余地）
rotated = len(segs) > 1
bounded = all(s["size_mb"] <= MAX_MB * 1.3 for s in segs)
written = c["messages"] == N
print()
print("结论:")
print("  切段      %s (%d 个库)" % ("生效" if rotated else "未切段!", len(segs)))
print("  单库受限  %s" % ("生效" if bounded else "有库超过上限!"))
print("  一条不丢  %s (%d/%d)" % ("是" if written else "有丢失!", c["messages"], N))

st.close()
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if (rotated and bounded and written) else 1)
