"""Point MQTTX's saved connections at the LOCAL broker instead of broker.emqx.io.

MQTTX's new-connection template defaults to broker.emqx.io (the public test
broker, which allows anonymous), which is why configs looked like they "connected
fine even when left blank" while nothing from the local pipeline ever showed up.

Nothing secret is printed here: stored passwords are only ever tested, never
echoed. The replacement password is read from reshape.json at runtime.
"""
import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "reshape.json"), encoding="utf-8"))
GOOD_USER = CFG["brokers"]["本地EMQX"]["username"]
GOOD_PASS = CFG["brokers"]["本地EMQX"]["password"]

DB = os.path.join(os.environ["USERPROFILE"], "AppData", "Roaming", "MQTTX", "MQTTX.db")
HOST, PORT = "127.0.0.1", 1883

sys.path.insert(0, HERE)
from kio_reshaper import probe_broker   # noqa: E402


def works(user, pw):
    return probe_broker(HOST, PORT, user or None, pw, 5)["ok"]


print("目标 broker: %s:%s  (本机 EMQX)" % (HOST, PORT))
print("可用账号: %s  (来自 reshape.json,不打印密码)" % GOOD_USER)
print()

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT id, name, host, port, username, password, client_id FROM ConnectionEntity"
).fetchall()
print("MQTTX 里有 %d 条连接:" % len(rows))

for r in rows:
    print("  %-8s host=%-20s user=%-8s cid=%s" % (
        r["name"], r["host"], r["username"] or "(空)", r["client_id"]))

print()
changed = 0
for r in rows:
    user, pw = r["username"] or "", r["password"] or ""
    # keep the existing identity when it already authenticates against the local
    # broker; otherwise fall back to the known-good account
    if works(user, pw):
        print("  %-8s 账号可用,保留 %s" % (r["name"], user or "(空)"))
    else:
        if user and works(user, GOOD_PASS):
            pw = GOOD_PASS
            print("  %-8s 密码无效,换用配置里的密码 (用户 %s)" % (r["name"], user))
        else:
            user, pw = GOOD_USER, GOOD_PASS
            print("  %-8s 账号不可用,改用 %s" % (r["name"], GOOD_USER))
    con.execute(
        "UPDATE ConnectionEntity SET host=?, port=?, username=?, password=?, "
        "protocol='mqtt', mqttVersion='5.0', ssl=0 WHERE id=?",
        (HOST, PORT, user, pw, r["id"]))
    changed += 1

con.commit()
print()
print("已改写 %d 条连接 -> %s:%s" % (changed, HOST, PORT))

print()
print("改后:")
for r in con.execute(
        "SELECT name, host, port, username, client_id FROM ConnectionEntity"):
    print("  %-8s %s:%s  user=%-10s cid=%s" % (
        r[0], r[1], r[2], r[3] or "(空)", r[4]))
con.close()
