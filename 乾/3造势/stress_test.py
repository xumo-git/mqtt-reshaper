"""Stress test: publish payloads with a growing object count and verify the reshaper
keeps up, then report loss / latency / throughput per step."""
import json
import os
import sys
import time
from datetime import datetime

RUN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "2运势")
sys.path.insert(0, RUN)
from mqtt_mini import MiniMQTT

CFG = json.load(open(os.path.join(RUN, "reshape.json"), encoding="utf-8"))
IN_TOPIC = "kingscada"
OUT_TOPIC = ((CFG.get("routes") or [{}])[0].get("targets") or [{}])[0].get("topic") or "MES"

START = int(sys.argv[1]) if len(sys.argv) > 1 else 10
END = int(sys.argv[2]) if len(sys.argv) > 2 else 50
SETTLE = float(sys.argv[3]) if len(sys.argv) > 3 else 1.2

print(f"源主题 {IN_TOPIC}  ->  目标主题 {OUT_TOPIC}")
print(f"对象数 {START} .. {END}   每轮等待 {SETTLE}s\n")

out_buf = []


def on_msg(topic, payload):
    out_buf.append((time.time(), payload.decode("utf-8", "replace")))


pub = MiniMQTT(client_id="stress_pub", username="kingscada", password="Aa@1165051755")
sub = MiniMQTT(client_id="stress_sub", username="kingscada", password="Aa@1165051755")
sub.on_message = on_msg
sub.connect()
sub.subscribe(OUT_TOPIC)
pub.connect()
time.sleep(0.6)

print(f"{'对象数':>6} {'期望':>6} {'实收':>6} {'丢':>5} {'耗时ms':>8} {'转发/ms':>9} {'峰值/秒':>8}")
print("-" * 62)

total_expect = total_got = 0
worst_loss = 0
slowest = 0.0

for n in range(START, END + 1):
    marker = 900000 + n
    objs = [{"N": f"pub{i}"} for i in range(10, 10 + n)]
    body = {
        "PNs": {"1": "V", "2": "T", "3": "Q"},
        "PVs": {"1": marker,
                "2": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "3": 192},
        "Objs": objs,
    }
    out_buf.clear()
    t0 = time.time()
    pub.publish(IN_TOPIC, json.dumps(body, ensure_ascii=False))
    time.sleep(SETTLE)
    elapsed = (time.time() - t0) * 1000

    got = [t for t, txt in out_buf if str(marker) in txt]
    total_expect += n
    total_got += len(got)
    loss = n - len(got)
    worst_loss = max(worst_loss, loss)
    rate = len(got) / (elapsed / 1000) if elapsed else 0
    peak = 0
    if len(got) >= 2:
        span = max(got) - min(got)
        peak = (len(got) - 1) / span if span > 0 else 0
    slowest = max(slowest, elapsed)
    flag = "  <-- 有丢" if loss else ""
    print(f"{n:>6} {n:>6} {len(got):>6} {loss:>5} {elapsed:>8.0f} {rate:>9.1f} {peak:>8.1f}{flag}")
    sys.stdout.flush()

pub.disconnect()
sub.disconnect()

print("-" * 62)
print(f"合计   期望 {total_expect}   实收 {total_got}   丢 {total_expect - total_got}")
print(f"最大单轮丢包 {worst_loss}   最长单轮 {slowest:.0f} ms")
if total_expect == total_got:
    print("结论: 全部转发成功,无丢帧")
else:
    print("结论: 存在丢帧,见上表标记")
