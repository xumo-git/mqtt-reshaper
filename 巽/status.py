"""运行状态自检 —— 双击 状态.bat 就看，出问题自己动手修。

只读检查，不改任何东西。判断三件事：
  1. Web 控制台在不在跑
  2. MQTT broker 通不通
  3. 转发链路还活着吗（最近一条报文是多久前）

服务没起时也能看——直接读库文件的最后写入时间。
"""
import json
import os
import socket
import sqlite3
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))          # 巽
ROOT = os.path.dirname(HERE)                                 # 项目根
RUN = os.path.join(ROOT, "乾", "2运势")
MSG_DIR = os.path.join(ROOT, "坤", "messages")
ACTIONS_DB = os.path.join(ROOT, "坤", "actions.db")
WEBUI = "http://127.0.0.1:8090"

OK = "OK  "
BAD = "FAIL"


def fmt_age(sec):
    if sec is None:
        return "-"
    sec = int(sec)
    if sec < 60:
        return "%d 秒前" % sec
    if sec < 3600:
        return "%d 分钟前" % (sec // 60)
    if sec < 86400:
        return "%d 小时前" % (sec // 3600)
    return "%d 天前" % (sec // 86400)


def fmt_dur(sec):
    """时长（不是时间点），用来描述「已运行多久」。"""
    sec = int(sec or 0)
    if sec < 60:
        return "%d 秒" % sec
    if sec < 3600:
        return "%d 分 %d 秒" % (sec // 60, sec % 60)
    if sec < 86400:
        return "%d 小时 %d 分" % (sec // 3600, (sec % 3600) // 60)
    return "%d 天 %d 小时" % (sec // 86400, (sec % 86400) // 3600)


def get(path, timeout=4):
    with urllib.request.urlopen(WEBUI + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def tcp_open(host, port, timeout=3):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def newest_db_write():
    """服务没起时的兜底：直接看库里最后一条是什么时候写的。"""
    best = None
    try:
        for n in os.listdir(MSG_DIR):
            if not n.endswith(".db"):
                continue
            p = os.path.join(MSG_DIR, n)
            try:
                con = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
                r = con.execute("SELECT MAX(ts) FROM messages").fetchone()
                con.close()
                if r and r[0]:
                    best = max(best or 0, r[0])
            except Exception:
                pass
    except OSError:
        pass
    return best


def main():
    print("=" * 60)
    print("  MQTT 转发 · 运行状态          " + time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)

    problems = []

    # ---------- 1) Web 控制台 ----------
    st = None
    try:
        st = get("/api/status")
        up = st.get("uptime", 0)
        print("  Web 控制台   8090   %s 在线   （已运行 %s）"
              % (OK, fmt_dur(up)))
    except Exception:
        print("  Web 控制台   8090   %s 离线" % BAD)
        problems.append("Web 控制台没在跑 —— 双击 震\\启动WebUI.bat 启动")

    # ---------- 2) MQTT broker ----------
    broker_ok = tcp_open("127.0.0.1", 1883)
    print("  MQTT Broker  1883   %s %s" % (OK if broker_ok else BAD,
                                          "在线" if broker_ok else "离线"))
    if not broker_ok:
        problems.append("本机 1883 没监听 —— 起 broker（EMQX 是 bin\\emqx.cmd start）")

    # ---------- 3) 方案与转发 ----------
    if st:
        print()
        print("  方案运行情况")
        for r in (st.get("routes") or []):
            name = r.get("name") or "?"
            src = "监听OK" if r.get("src_connected") else "监听断"
            dst = "目标OK" if r.get("dst_connected") else "目标断"
            s = r.get("stats") or {}
            good = r.get("src_connected") and r.get("dst_connected")
            print("    %-10s %s  %s  %s   收 %d 转 %d 解析失败 %d 发布失败 %d"
                  % (name, OK if good else BAD, src, dst,
                     s.get("in", 0), s.get("out", 0), s.get("bad", 0), s.get("drop", 0)))
            if not good:
                problems.append("方案「%s」%s%s" % (name, "" if r.get("src_connected") else "监听没连上，",
                                                    "" if r.get("dst_connected") else "目标没连上"))
            if s.get("drop"):
                problems.append("方案「%s」有 %d 条发布失败" % (name, s["drop"]))

    # ---------- 4) 存储 ----------
    print()
    try:
        s = get("/api/storage")
        c = s.get("counts") or {}
        segs = s.get("segments") or []
        a = s.get("actions") or {}
        print("  报文库       %d 段 / %.1f MB   共 %d 条"
              % (len(segs), c.get("total_mb") or 0, c.get("messages") or 0))
        print("  动作记录     %d 条 / %s MB" % (a.get("actions") or 0, a.get("size_mb") or 0))
    except Exception:
        # 服务没起时自己数文件
        try:
            ns = len([x for x in os.listdir(MSG_DIR) if x.endswith(".db")])
            mb = sum(os.path.getsize(os.path.join(MSG_DIR, x))
                     for x in os.listdir(MSG_DIR)) / 1048576
            print("  报文库       %d 段 / %.1f MB   （服务未起，读的文件）" % (ns, mb))
        except Exception:
            print("  报文库       读不到")

    # ---------- 5) 最近活动 ----------
    print()
    last = None
    if st:
        for r in (st.get("routes") or []):
            for k in ("src_last_ts", "dst_last_ts"):
                v = r.get(k) or 0
                if v:
                    last = max(last or 0, v)
    if not last:
        last = newest_db_write()
    if last:
        age = time.time() - last
        mark = OK if age < 60 else ("闲置" if age < 600 else BAD)
        print("  最近报文     %s %s" % (mark, fmt_age(age)))
        if age > 600:
            problems.append("超过 10 分钟没有报文 —— 要么没人发，要么链路断了")
    else:
        print("  最近报文     还没有任何数据")

    # ---------- 结论 ----------
    print()
    print("-" * 60)
    if problems:
        print("  发现 %d 个问题：" % len(problems))
        for p in problems:
            print("   · " + p)
    else:
        print("  一切正常")
    print("-" * 60)
    return 1 if problems else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BrokenPipeError, OSError):
        # 被 `| head` 之类提前关掉管道时，Windows 会报 EINVAL。
        # 那不是错误，安静退出，别甩 traceback 出来吓人。
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
