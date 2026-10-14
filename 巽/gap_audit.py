"""Frame-loss audit: watch raw + reshaped topics at a known rate and report any gaps.

Reports:
  * counts on each topic and the raw->output pairing ratio
  * inter-arrival intervals (min/avg/max) and every gap larger than the threshold
  * duplicate detection on the output side
  * values / capture timestamps of any frames that never made it through
"""
import json
import sys
import time
from datetime import datetime

sys.path.insert(0, r"C:\mqtt转发")
from mqtt_mini import MiniMQTT

RAW = "kingscada"
OUT = "MES"
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0
GAP = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5   # seconds

raw_rows = []   # (recv_epoch, capture_ts, value, obj)
out_rows = []


def parse(payload):
    try:
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return None


def on_msg(topic, payload):
    now = time.time()
    doc = parse(payload)
    if topic == RAW:
        if doc is None:
            return
        try:
            raw_rows.append((now, doc["PVs"]["2"], doc["PVs"]["1"], doc["Objs"][0]["N"]))
        except Exception:
            raw_rows.append((now, None, None, None))
    elif topic == OUT:
        if doc is None:
            return
        out_rows.append((now, doc.get("时间戳"), doc.get("值"), doc.get("变量名")))


c = MiniMQTT(client_id="gap_audit", username="kingscada", password="Aa@1165051755")
c.on_message = on_msg
c.connect()
c.subscribe(RAW)
c.subscribe(OUT)
print(f"== frame-loss audit: watching {RAW} / {OUT} for {DURATION:.0f}s ==")
sys.stdout.flush()
t0 = time.time()
time.sleep(DURATION)
c.disconnect()
elapsed = time.time() - t0


def stats(rows, label):
    print(f"\n{'='*68}\n{label}: {len(rows)} frames in {elapsed:.1f}s")
    if not rows:
        return
    times = [r[0] for r in rows]
    iv = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    if iv:
        print(f"  interval  min={min(iv):.3f}s  avg={sum(iv)/len(iv):.3f}s  max={max(iv):.3f}s")
    gaps = [(i + 1, iv[i]) for i in range(len(iv)) if iv[i] > GAP]
    print(f"  gaps > {GAP}s: {len(gaps)}")
    for idx, g in gaps[:15]:
        prev = datetime.fromtimestamp(times[idx - 1]).strftime("%H:%M:%S.%f")[:-3]
        cur = datetime.fromtimestamp(times[idx]).strftime("%H:%M:%S.%f")[:-3]
        print(f"     #{idx}  {prev} -> {cur}   gap {g:.3f}s")
    return gaps


g_raw = stats(raw_rows, f"RAW  {RAW}")
g_out = stats(out_rows, f"OUT  {OUT}")

print(f"\n{'='*68}\nPAIRING")
print(f"  raw={len(raw_rows)}  out={len(out_rows)}  "
      f"ratio={'1:1 OK' if len(raw_rows) == len(out_rows) else 'MISMATCH'}")

# duplicate check on output
seen = {}
dups = 0
for t, cap, val, obj in out_rows:
    k = (cap, val, obj)
    seen[k] = seen.get(k, 0) + 1
dups = sum(v - 1 for v in seen.values() if v > 1)
print(f"  duplicates on output: {dups}")

# per-frame pairing by capture timestamp
raw_caps = {}
for t, cap, val, obj in raw_rows:
    raw_caps[cap] = raw_caps.get(cap, 0) + 1
out_caps = {}
for t, cap, val, obj in out_rows:
    out_caps[cap] = out_caps.get(cap, 0) + 1

missing = [cap for cap in raw_caps if cap not in out_caps]
extra = [cap for cap in out_caps if cap not in raw_caps]
print(f"  raw frames with no matching output : {len(missing)}")
for cap in missing[:15]:
    row = next(r for r in raw_rows if r[1] == cap)
    print(f"     MISSING  capture={cap}  value={row[2]}  obj={row[3]}")
print(f"  output frames with no raw source  : {len(extra)}")
for cap in extra[:15]:
    print(f"     ORPHAN   capture={cap}")

print(f"\n{'='*68}")
ok = (not missing) and (not extra) and (dups == 0) and (len(raw_rows) == len(out_rows))
if not raw_rows:
    print("结论: 期间没有任何数据,请确认 IOServer 正在发布")
elif ok and not g_raw and not g_out:
    print("结论: 无丢帧 —— 收发 1:1,无缺口,无重复")
elif ok:
    print(f"结论: 收发 1:1 且无漏配,但上游本身有间隔空洞(raw {len(g_raw)} 处 / out {len(g_out)} 处)")
    print("      -> 空洞来自 IOServer 发送侧,不是转发程序丢的")
else:
    print("结论: 存在丢帧或异常,见上面 MISSING / ORPHAN / MISMATCH 明细")
