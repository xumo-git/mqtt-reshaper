"""稳定性加固的回归测试 —— 这些是从 EMQX 的默认值里吸收来的。

  · 读写都带超时（不再 settimeout(None)），半死连接能被发现
  · 包大小有上限，异常包撑不爆内存
  · 剩余长度字段有 4 字节上限，恶意包不会无限算下去
  · 日志轮转，不会一路涨到占满盘
"""
import os
import socket
import struct
import sys
import threading
import time

RUN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "2运势")
sys.path.insert(0, RUN)
sys.stdout.reconfigure(encoding="utf-8")

from kio_reshaper import (  # noqa: E402
    MqttClient, KEEPALIVE_MULTIPLIER, MAX_PACKET_SIZE, SEND_TIMEOUT, CONNECT_TIMEOUT)

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print("  PASS  %s" % label)
    else:
        fail += 1
        print("  FAIL  %s\n          got  %r\n          want %r" % (label, got, want))


def client(keepalive=30):
    return MqttClient("127.0.0.1", 1883, "t", "u", "p", keepalive=keepalive)


print("== 超时口径照 EMQX ==")
check("keepalive 30 -> 读超时 45s", client(30)._rd_timeout, 30 * KEEPALIVE_MULTIPLIER)
check("keepalive 10 -> 读超时 15s", client(10)._rd_timeout, 15.0)
check("keepalive 2  -> 至少 5s", client(2)._rd_timeout, 5.0)
check("keepalive 0  -> 不会是 0，留 60s 兜底", client(0)._rd_timeout, 60 * KEEPALIVE_MULTIPLIER)
check("发送超时 15s", SEND_TIMEOUT, 15.0)
check("连接超时 15s", CONNECT_TIMEOUT, 15.0)
check("包上限 1MB", MAX_PACKET_SIZE, 1024 * 1024)


class FakeBroker(threading.Thread):
    """接受连接、回 CONNACK、之后装死（模拟半死连接）。"""

    def __init__(self, connack=True, then_silent=True):
        super().__init__(daemon=True)
        self.connack, self.then_silent = connack, then_silent
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(2)
        self.port = self.srv.getsockname()[1]

    def run(self):
        try:
            c, _ = self.srv.accept()
        except OSError:
            return
        try:
            c.settimeout(10)
            c.recv(4096)                       # 丢掉 CONNECT
            if self.connack:
                c.sendall(b"\x20\x02\x00\x00")  # CONNACK accepted
            if self.then_silent:
                time.sleep(30)                  # 之后一个字节都不回
        except Exception:
            pass
        finally:
            try:
                c.close()
            except Exception:
                pass

    def close(self):
        try:
            self.srv.close()
        except Exception:
            pass


print("\n== 连接后不再 settimeout(None) ==")
fb = FakeBroker()
fb.start()
c = client(10)
c.port = fb.port
c.connect()
to = c.sock.gettimeout()
check("socket 有超时", to is not None, True)
check("读超时 = keepalive × 1.5（发送超时另算，不互相顶）", to, c._rd_timeout)
# 发送时才临时换成 SEND_TIMEOUT，发完还原
c.sock.settimeout(99)
c._send(b"\xc0\x00")
check("发完把读超时还原", c.sock.gettimeout(), 99)
c.disconnect()
fb.close()

print("\n== 半死连接能被发现（原来会永远挂着）==")
fb = FakeBroker(connack=True, then_silent=True)
fb.start()
c = client(2)            # 读超时 5s，测起来快
c.port = fb.port
c.connect()
t0 = time.time()
try:
    c.loop_forever()     # 对端装死 -> recv 超时 -> 抛出 -> 循环结束
    ended, why = True, "正常返回"
except Exception as e:
    ended, why = True, type(e).__name__
took = time.time() - t0
check("loop_forever 会退出(不再永久挂起)", ended, True)
check("在超时窗口内退出", took < c._rd_timeout + 3, True)
print("        退出原因: %s, 用时 %.1fs (读超时 %.1fs)" % (why, took, c._rd_timeout))
try:
    c.disconnect()
except Exception:
    pass
fb.close()

print("\n== 包大小上限 ==")
try:
    import kio_reshaper as kr
    saved = kr.MAX_PACKET_SIZE
    kr.MAX_PACKET_SIZE = 64          # 临时调小，好造包
    c = client()

    def fake_read(n):
        # 返回一个 remaining length = 1000 的包头，然后给足 body
        if n == 1:
            return b"\x30"
        return b"\x00"               # 逐字节喂，长度字段自己算

    reads = [b"\x30"] + [b"\x7f"] * 1 + [b"\x07"] + [b"x"] * 2000
    idx = [0]

    def rd(n):
        out = b""
        while len(out) < n and idx[0] < len(reads):
            out += reads[idx[0]]
            idx[0] += 1
        return out

    c._read_exact = rd
    try:
        c._read_packet()
        check("超大包被拒绝", False, True)
    except ConnectionError as e:
        check("超大包被拒绝", "过大" in str(e), True)
    kr.MAX_PACKET_SIZE = saved
except Exception as e:
    check("包大小测试跑通", "ERR:" + str(e), "ERR:none")

print("\n== 剩余长度字段不会被撑爆 ==")
c = client()
idx = [0]
reads = [b"\x30"] + [b"\xff"] * 6      # 6 字节长度字段，超过 MQTT 允许的 4 字节
idx = [0]


def rd2(n):
    out = b""
    while len(out) < n and idx[0] < len(reads):
        out += reads[idx[0]]
        idx[0] += 1
    return out


c._read_exact = rd2
try:
    c._read_packet()
    check("超长长度字段被拒绝", False, True)
except ConnectionError as e:
    check("超长长度字段被拒绝", "异常" in str(e), True)

print("\n== 日志轮转 ==")
import logging  # noqa: E402
from logging.handlers import RotatingFileHandler  # noqa: E402
import inspect  # noqa: E402
import kio_reshaper  # noqa: E402
src = inspect.getsource(kio_reshaper.main)
check("用 RotatingFileHandler", "RotatingFileHandler" in src, True)
check("有 maxBytes", "maxBytes" in src, True)
check("有 backupCount", "backupCount" in src, True)
check("默认 5MB / 留 3 份", ("log_max_mb" in src and "or 5" in src
                            and "log_backups" in src and "or 3" in src), True)
check("上限可由配置改", 'cfg.get("log_max_mb")' in src, True)

print("\n%d 项,失败 %d" % (ok + fail, fail))
sys.exit(1 if fail else 0)
