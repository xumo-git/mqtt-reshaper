"""Publish a raw payload to the input topic and capture what the reshaper emits."""
import json
import os
import sys
import time
from datetime import datetime

RUN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "2运势")
sys.path.insert(0, RUN)
from mqtt_mini import MiniMQTT

IN_TOPIC = sys.argv[1] if len(sys.argv) > 1 else "kingscada"
PAYLOAD = open(sys.argv[2], "rb").read() if len(sys.argv) > 2 else b"{}"
OUT_TOPIC = sys.argv[3] if len(sys.argv) > 3 else "kingscada/flat"

got = []


def on_msg(topic, payload):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    got.append(payload)
    print(f"  << [{ts}] {topic} ({len(payload)}B)")
    try:
        print("     " + json.dumps(json.loads(payload.decode("utf-8")), ensure_ascii=False))
    except Exception:
        print("     " + repr(payload[:200]))
    sys.stdout.flush()


c = MiniMQTT(client_id="reshape_tester", username="kingscada", password="Aa@1165051755")
c.on_message = on_msg
c.connect()
time.sleep(0.4)
c.subscribe(OUT_TOPIC)
time.sleep(0.4)
print(f">> publish {len(PAYLOAD)}B -> {IN_TOPIC}   watching {OUT_TOPIC}")
c.publish(IN_TOPIC, PAYLOAD)
time.sleep(3.0)
c.disconnect()
print(f"\n== {len(got)} reshaped message(s) ==")
if got:
    out = sys.argv[4] if len(sys.argv) > 4 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_output.json")
    open(out, "wb").write(b"\n".join(got))
    print("wrote", out, " count:", len(got))
