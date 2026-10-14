"""KingIOServer MQTT payload reshaper.

Subscribes to a source broker/topic, rewrites each message according to a
config-driven template, and publishes to a destination broker/topic. Source and
destination may be different brokers. The vendor DLL is left untouched -- every
format change is a config edit.

Path syntax in templates:
    "@PVs.1"        -> payload["PVs"]["1"]
    "@Objs.0.N"     -> payload["Objs"][0]["N"]
    "@."            -> the payload itself (useful with "each")
    "@#key"         -> current key    (inside "each" over an object)
    "@#value"       -> current value  (inside "each")
    "@#index"       -> current index/key
    "@#now"         -> local time "YYYY-MM-DD HH:MM:SS.mmm"
    "@#ts"          -> effective time of the current item: PVs.2 + item's "2" ms
    anything else is emitted as a literal

Run:  python kio_reshaper.py [path-to-reshape.json]
"""
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import socket
import struct
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta

# Windows consoles often default to a legacy codepage (gbk) while showing utf-8;
# force utf-8 so Chinese keys/values print correctly.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ---------------------------------------------------------------- MQTT client

# 下面几个口径照搬 EMQX 的默认值（etc/examples/mqtt.conf.example 等）：
#   keepalive_multiplier = 1.5  -> 判定死连接用 keepalive × 1.5
#   max_packet_size      = 1MB  -> 超过就当异常包
#   send_timeout         = 15s  -> 发送阻塞超时即断，别把转发线程卡死
#   idle_timeout         = 15s  -> 连上之后等 CONNECT/CONNACK 也不许无限等
KEEPALIVE_MULTIPLIER = 1.5
MAX_PACKET_SIZE = 1024 * 1024
SEND_TIMEOUT = 15.0
CONNECT_TIMEOUT = 15.0


def _enc_len(n):
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        if n > 0:
            b |= 0x80
        out.append(b)
        if n == 0:
            return bytes(out)


def _enc_str(s):
    raw = s.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


class MqttClient:
    """Minimal MQTT 3.1.1 client with auto-reconnect. QoS 0 only.

    超时口径照 EMQX：连接后不再 settimeout(None)，读写都带超时。
    半死连接（TCP 还在但对端不说话）会在 keepalive × 1.5 后被判死并重连，
    而不是永远挂在那里让重连机制失效。
    """

    def __init__(self, host, port, client_id, username, password, keepalive=30):
        self.host, self.port = host, int(port)
        self.client_id = client_id
        self.username, self.password = username, password
        self.keepalive = int(keepalive)
        self.sock = None
        self._buf = b""
        self._lock = threading.Lock()
        self.on_message = None
        self._running = False
        # keepalive<=0 时 MQTT 语义是"不超时"，但我们仍要留个上限，
        # 否则死连接会永久占着线程
        ka = self.keepalive if self.keepalive > 0 else 60
        self._rd_timeout = max(5.0, ka * KEEPALIVE_MULTIPLIER)

    def _send(self, packet):
        # 发送用 SEND_TIMEOUT（照 EMQX listeners.tcp.send_timeout）：
        # 目标 broker 一慢就抛超时，由 run_forever 断开重连，
        # 而不是把转发线程永久卡住。发完把读超时还原，别影响死连接判定。
        old = None
        try:
            old = self.sock.gettimeout()
            self.sock.settimeout(SEND_TIMEOUT)
            self.sock.sendall(packet)
        finally:
            if old is not None:
                try:
                    self.sock.settimeout(old)
                except OSError:
                    pass

    def _read_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("broker closed connection")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _read_packet(self):
        first = self._read_exact(1)[0]
        mult, rem = 1, 0
        while True:
            b = self._read_exact(1)[0]
            rem += (b & 0x7F) * mult
            mult *= 128
            if not (b & 0x80):
                break
            if mult > 128 ** 3:          # 剩余长度最多 4 字节
                raise ConnectionError("报文长度字段异常")
        if rem > MAX_PACKET_SIZE:
            raise ConnectionError(
                "报文过大: %d 字节 (上限 %d, 照 EMQX max_packet_size)" % (rem, MAX_PACKET_SIZE))
        body = self._read_exact(rem) if rem else b""
        return first, body

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port),
                                             timeout=CONNECT_TIMEOUT)
        # 关键：不要 settimeout(None)。读超时 = keepalive × 1.5（照 EMQX
        # mqtt.keepalive_multiplier），半死连接到点就被判死，重连机制才真的有效。
        # 发送超时由 _send 单独设，两个口径互不干扰。
        self.sock.settimeout(self._rd_timeout)
        flags = 0x02
        payload = _enc_str(self.client_id)
        if self.username is not None:
            flags |= 0x80 | 0x40
            payload += _enc_str(self.username)
            payload += _enc_str(self.password or "")
        vh = _enc_str("MQTT") + bytes([0x04, flags]) + struct.pack(">H", self.keepalive)
        self._send(bytes([0x10]) + _enc_len(len(vh) + len(payload)) + vh + payload)
        ptype, body = self._read_packet()
        if ptype != 0x20 or len(body) != 2:
            raise ConnectionError(f"unexpected CONNACK: {body!r}")
        if body[1] != 0:
            raise ConnectionError(f"CONNACK refused, code={body[1]}")
        self._buf = b""
        self._running = True

    def loop_forever(self, topic=None, qos=0):
        if topic is not None:
            topics = topic if isinstance(topic, (list, tuple)) else [topic]
            for t in topics:
                if t:
                    self.subscribe(t, qos)
        threading.Thread(target=self._keepalive, daemon=True).start()
        try:
            while self._running:
                ptype, body = self._read_packet()
                if ptype >> 4 == 3:
                    tlen = struct.unpack(">H", body[:2])[0]
                    t = body[2:2 + tlen].decode("utf-8", "replace")
                    idx = 2 + tlen + (2 if ((ptype >> 1) & 0x03) > 0 else 0)
                    if self.on_message:
                        self.on_message(t, body[idx:])
                elif ptype >> 4 == 14:
                    pass
        except (OSError, ConnectionError, struct.error):
            self._running = False

    def _keepalive(self):
        while self._running:
            time.sleep(max(1, self.keepalive // 2))
            try:
                with self._lock:
                    if self._running:
                        self._send(b"\xc0\x00")
            except OSError:
                self._running = False
                return

    def publish(self, topic, payload, qos=0, retain=False):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        flags = (3 << 4) | ((1 if retain else 0) | (qos << 1))
        body = _enc_str(topic) + payload
        with self._lock:
            self._send(bytes([flags]) + _enc_len(len(body)) + body)

    def subscribe(self, topic, qos=0):
        body = struct.pack(">H", 1) + _enc_str(topic) + bytes([qos])
        with self._lock:
            self._send(bytes([0x82]) + _enc_len(len(body)) + body)

    def disconnect(self):
        self._running = False
        try:
            with self._lock:
                self._send(b"\xe0\x00")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def make_client(b, fallback_id):
    # Empty client_id -> generate one. A blank id is illegal in MQTT, and reusing a
    # fixed id here would collide with the IOServer client and kick it off the broker.
    cid = (b.get("client_id") or "").strip()
    if not cid:
        cid = "%s_%s" % (fallback_id, uuid.uuid4().hex[:6])
    return MqttClient(
        b["host"], b.get("port", 1883),
        cid,
        b.get("username"), b.get("password"),
        b.get("keepalive", 30),
    )


# CONNACK return codes (MQTT 3.1.1 §3.2.2.3)
_CONNACK_MSG = {
    0: "连接成功",
    1: "协议版本不被接受",
    2: "Client ID 被拒绝",
    3: "服务器不可用",
    4: "用户名或密码错误",
    5: "未授权,请检查账号权限",
}


def probe_broker(host, port, username=None, password=None, timeout=5):
    """One-shot connectivity test: TCP handshake + MQTT CONNECT + CONNACK.

    Always uses a throwaway random client_id, so probing never collides with the
    live IOServer session (a duplicate id would kick that client off the broker).
    Returns a dict; never raises.
    """
    t0 = time.time()
    out = {"ok": False, "stage": "tcp", "code": None, "message": "",
           "elapsed_ms": 0.0, "client_id": ""}
    cid = "kio_probe_" + uuid.uuid4().hex[:8]
    out["client_id"] = cid
    sock = None
    try:
        sock = socket.create_connection((host, int(port or 1883)), timeout=timeout)
        sock.settimeout(timeout)
        out["stage"] = "connack"

        flags = 0x02
        payload = _enc_str(cid)
        if username:
            flags |= 0x80 | 0x40
            payload += _enc_str(username)
            payload += _enc_str(password or "")
        vh = _enc_str("MQTT") + bytes([0x04, flags]) + struct.pack(">H", 30)
        sock.sendall(bytes([0x10]) + _enc_len(len(vh) + len(payload)) + vh + payload)

        # CONNACK is always 4 bytes: type 0x20, remaining length 2, flags, code
        head = b""
        while len(head) < 4:
            chunk = sock.recv(4 - len(head))
            if not chunk:
                raise ConnectionError("broker 断开,没收到 CONNACK")
            head += chunk
        if head[0] != 0x20 or head[1] != 2:
            raise ConnectionError("CONNACK 包异常: %r" % head)
        code = head[3]
        out["code"] = code
        out["ok"] = code == 0
        out["message"] = _CONNACK_MSG.get(code, "CONNACK 拒绝 code=%s" % code)
    except socket.gaierror:
        out["message"] = "IP / 域名无法解析:%s" % host
    except socket.timeout:
        out["message"] = "连接超时(%.0f 秒内无响应)" % timeout
    except ConnectionRefusedError:
        out["message"] = "连接被拒绝:%s:%s 端口未监听" % (host, port)
    except OSError as e:
        out["message"] = "网络错误 %s:%s — %s" % (host, port, e)
    except Exception as e:
        out["message"] = str(e) or e.__class__.__name__
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        out["elapsed_ms"] = round((time.time() - t0) * 1000, 1)
    return out


def run_forever(cli, label, log, subscribe_topic=None, subscribe_qos=0):
    """Connect, serve, reconnect on loss. Runs until the process is killed.

    断线重连带抖动抑制（照 EMQX flapping_detect 的用意）：退避指数上升到 30s 封顶，
    连续失败时日志降频——否则 broker 停机那几分钟会把日志刷爆。
    """
    backoff = 1
    fails = 0
    while True:
        try:
            cli.connect()
            if fails:
                log.info("%s 已恢复,此前连续失败 %d 次", label, fails)
            backoff, fails = 1, 0
            log.info("%s connected to %s:%s", label, cli.host, cli.port)
            cli.loop_forever(subscribe_topic, subscribe_qos)
            log.warning("%s connection lost", label)
        except Exception as e:
            fails += 1
            # 前 3 次每次都记,之后每 10 次记一次;中间不刷屏
            if fails <= 3 or fails % 10 == 0:
                log.error("%s 连不上 (第 %d 次): %s", label, fails, e)
        try:
            cli.disconnect()
        except Exception:
            pass
        time.sleep(backoff)
        backoff = min(backoff * 2, 30)


def _resolve_broker(cfg, ref):
    """ref is either a broker name (index into cfg['brokers']) or an inline dict."""
    if isinstance(ref, dict):
        return dict(ref)
    if isinstance(ref, str):
        b = (cfg.get("brokers") or {}).get(ref)
        if not b:
            raise ValueError("未找到 broker 定义: %s" % ref)
        return dict(b)
    raise ValueError("broker 引用无效: %r" % (ref,))


def _resolve_rules(cfg, refs):
    """refs is a list of rule names (into cfg['rule_library']) or inline rule dicts."""
    lib = cfg.get("rule_library") or {}
    out = []
    for r in (refs or []):
        if isinstance(r, str):
            rule = lib.get(r)
            if not rule:
                raise ValueError("未找到转换规则: %s" % r)
            rr = dict(rule)
            rr.setdefault("name", r)
            out.append(rr)
        elif isinstance(r, dict):
            out.append(dict(r))
    return out


def load_routes(cfg):
    """Resolve the config into a list of runnable routes.

    New form:
      brokers:      {name: {host,port,client_id,username,password,topic,role}}
      rule_library: {name: {match, each, template}}
      routes:       [{name, enabled,
                      from: {broker, topics, qos, client_id},
                      to:   [{broker, topic, qos, retain, client_id}, ...],
                      rules: [ruleName | ruleObject, ...]}]

    Legacy forms (single source/destination, or auto_source) are folded into one route.
    """
    if cfg.get("routes"):
        out = []
        for i, r in enumerate(cfg["routes"]):
            frm = r.get("from") or {}
            fb = _resolve_broker(cfg, frm.get("broker"))
            topics = frm.get("topics")
            if not topics:
                topics = [frm["topic"]] if frm.get("topic") else [fb.get("topic") or "kingscada"]
            if isinstance(topics, str):
                topics = [topics]

            # targets: new form is a list, legacy form is a single "to"
            raw_targets = r.get("targets")
            if raw_targets is None:
                raw_targets = [r["to"]] if r.get("to") else []
            targets = []
            for t in raw_targets:
                tb = _resolve_broker(cfg, t.get("broker"))
                # each target may carry its own rule set; fall back to the route's
                t_rules = t.get("rules")
                if t_rules is None:
                    t_rules = r.get("rules")
                targets.append({
                    "broker": tb,
                    "topic": t.get("topic") or tb.get("topic") or "MES",
                    "qos": int(t.get("qos", 0)),
                    "retain": bool(t.get("retain", False)),
                    "client_id": (t.get("client_id") or tb.get("client_id") or "").strip(),
                    "rules": _resolve_rules(cfg, t_rules or cfg.get("rules")),
                })
            if not targets:
                raise ValueError("方案「%s」没有配置目标" % (r.get("name") or i + 1))

            out.append({
                "name": r.get("name") or ("方案%d" % (i + 1)),
                "enabled": bool(r.get("enabled", True)),
                "source": {
                    "broker": fb,
                    "topics": list(topics),
                    "qos": int(frm.get("qos", 0)),
                    "client_id": (frm.get("client_id") or fb.get("client_id") or "").strip(),
                },
                "targets": targets,
                "rules": _resolve_rules(cfg, r.get("rules") or cfg.get("rules")),
            })
        return out

    # legacy: one route built from source/destination (or auto_source)
    src, dst = load_config(cfg)
    topics = src.get("topics") or [src.get("topic") or "kingscada"]
    return [{
        "name": cfg.get("route_name") or "默认方案",
        "enabled": True,
        "source": {
            "broker": dict(src["broker"]),
            "topics": list(topics),
            "qos": int(src.get("qos", 0)),
            "client_id": "",
        },
        "targets": [{
            "broker": dict(dst["broker"]),
            "topic": dst.get("topic") or "MES",
            "qos": int(dst.get("qos", 0)),
            "retain": bool(dst.get("retain", False)),
            "client_id": "",
        }],
        "rules": cfg.get("rules") or [],
    }]


# ---------------------------------------------------------------- template


def _now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"


_TS_FMTS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S")


def _shift_ms(ts, offset):
    """Add `offset` milliseconds to a 'YYYY-MM-DD HH:MM:SS[.mmm]' string.

    Returns None when the string cannot be parsed, so the caller can decide
    whether to fall back rather than emit a confidently wrong time.
    """
    for fmt in _TS_FMTS:
        try:
            dt = datetime.strptime(ts, fmt)
        except (TypeError, ValueError):
            continue
        dt = dt + timedelta(milliseconds=offset)
        base = dt.strftime("%Y-%m-%d %H:%M:%S")
        # keep the fractional part only if the source had one
        return base if fmt == _TS_FMTS[1] else "%s.%03d" % (base, dt.microsecond // 1000)
    return None


def _resolve_ts(doc, item_value):
    """Effective timestamp for the current item.

    kxMQTT reports each object's time as an offset in milliseconds from the
    packet's PVs.2 baseline, omitting the field entirely when the offset is zero.
    So the real sample time is PVs.2 + item["2"]ms. Falls back to the raw
    baseline, then to the current clock.
    """
    ok, base = _walk(doc, "PVs.2")
    if not ok or base in (None, ""):
        return True, _now_str()
    offset = 0
    if isinstance(item_value, dict) and item_value.get("2") is not None:
        try:
            offset = int(item_value["2"])
        except (TypeError, ValueError):
            offset = 0
    if offset == 0:
        return True, base
    shifted = _shift_ms(base, offset)
    return True, (shifted if shifted is not None else base)


def _walk(cur, path):
    for seg in path.split("."):
        if not seg:
            continue
        if isinstance(cur, list):
            if not seg.isdigit():
                return False, None
            i = int(seg)
            if not 0 <= i < len(cur):
                return False, None
            cur = cur[i]
        elif isinstance(cur, dict):
            if seg not in cur:
                return False, None
            cur = cur[seg]
        else:
            return False, None
    return True, cur


def resolve(doc, path, item=None):
    """Resolve a template path. Supports '@a.b', '@.', '@#key', '@#value.1', '@#ts'."""
    if path in ("", "."):
        return True, doc
    if path.startswith("#"):
        head, _, rest = path[1:].partition(".")
        if head == "ts":
            # works with or without an enclosing "each": no item -> plain baseline
            return _resolve_ts(doc, item[1] if item else None)
        if item is None:
            return False, None
        key, value = item
        if head in ("key", "index"):
            target = key
        elif head == "value":
            target = value
        elif head == "now":
            return True, _now_str()
        else:
            return False, None
        return (True, target) if not rest else _walk(target, rest)
    return _walk(doc, path)


def _pick(doc, spec, item):
    """按 '@a|b|c' 依次回落取值。返回 (是否取到, 值)。"""
    ok, val = False, None
    for alt in spec[1:].split("|"):        # fallbacks, left to right
        ok, val = resolve(doc, alt, item)
        if ok:
            break
    return ok, val


def apply_template(doc, template, omit_missing, item=None):
    out = {}
    for key, spec in template.items():
        # 字段名本身也能取值：以 @ 开头就从报文里取出来当键名。
        #   {"@#value.N": "@#value.1|PVs.1"}  ->  {"pub11": 0}
        # 用来把「变量名」直接当输出的键，而不是多包一层。
        k = key
        if isinstance(key, str) and key.startswith("@"):
            okk, kv = _pick(doc, key, item)
            if not okk:
                if omit_missing:
                    continue
                kv = None
            k = "" if kv is None else str(kv)
        if isinstance(spec, str) and spec.startswith("@"):
            ok, val = _pick(doc, spec, item)
            if not ok:
                if omit_missing:
                    continue
                val = None
            out[k] = val
        else:
            out[k] = spec
    return out


def iter_each(doc, path):
    ok, target = resolve(doc, path)
    if not ok:
        return
    if isinstance(target, dict):
        for k, v in target.items():
            yield k, v
    elif isinstance(target, list):
        for i, v in enumerate(target):
            yield i, v
    else:
        yield None, target


def reshape(doc, rules, omit_missing):
    msgs = []
    for rule in rules:
        match = rule.get("match")
        if match:
            # match 支持 | 多选，任一路径能取到值就算命中——和模板取值一个写法。
            # 直发报文的键名在不同工程里可能是 tag1 或 /tag1，写全两种才不会漏。
            alts = (match[1:] if match.startswith("@") else match).split("|")
            hit = False
            for alt in alts:
                ok, _ = resolve(doc, alt)
                if ok:
                    hit = True
                    break
            if not hit:
                continue
        template = rule["template"]
        each = rule.get("each")
        if each:
            for item in iter_each(doc, each[1:] if each.startswith("@") else each):
                msgs.append(apply_template(doc, template, omit_missing, item))
        else:
            msgs.append(apply_template(doc, template, omit_missing))
    return msgs


# ---------------------------------------------------------------- config


def _autodiscover_source(cfg):
    """Read the source connection out of KingIOServer's own config files."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from kio_autodiscover import discover, pick_source
    except Exception as e:
        raise RuntimeError("自动发现模块不可用: %s" % e)
    info = discover(cfg.get("auto_source_root"))
    prefer = cfg.get("auto_source_method") or "kxmqtt-forward"
    m = pick_source(info, prefer)
    topic = cfg.get("auto_source_topic") or m.get("topic") or "kingscada"
    # Never reuse the discovered client_id: that id belongs to the IOServer client
    # itself, and a duplicate id makes the broker kick one of the two off.
    return {
        "broker": {
            "host": m["host"],
            "port": int(m.get("port") or 1883),
            "username": m.get("username"),
            "password": m.get("password"),
            "client_id": "kio_reshaper_src",
            "keepalive": 30,
        },
        "topic": topic,
        "qos": cfg.get("source_qos", 0),
        "_discovered": {
            "method": m["name"],
            "file": m["source_file"],
            "root": info["root"],
            "ioserver_client_id": m.get("client_id"),
        },
    }


def load_config(cfg):
    """Support auto-source (read from IOServer), split source/destination, and the legacy form."""
    if cfg.get("auto_source"):
        src = _autodiscover_source(cfg)
        dst = dict(cfg.get("destination") or {})
        if not dst.get("broker"):
            raise ValueError("auto_source 开启时必须配置 destination")
    elif "source" in cfg or "destination" in cfg:
        src = dict(cfg.get("source") or {})
        dst = dict(cfg.get("destination") or {})
    else:
        shared = cfg.get("broker") or {}
        src = {"broker": shared, "topic": cfg.get("input_topic")}
        dst = {"broker": dict(shared), "topic": cfg.get("output_topic"),
               "qos": cfg.get("qos", 0), "retain": cfg.get("retain", False)}
    # fall back to the other side if one is incomplete
    for side in (src, dst):
        side.setdefault("broker", (dst if side is src else src).get("broker", {}))
        side.setdefault("topic", "kingscada")
        side.setdefault("qos", 0)
    src.setdefault("client_id", "kio_reshaper_src")
    dst.setdefault("client_id", "kio_reshaper_dst")
    # client_id lives on the broker dict for make_client()
    for side in (src, dst):
        side["broker"] = dict(side["broker"])
        side["broker"].setdefault("client_id", side["client_id"])
    return src, dst


# ---------------------------------------------------------------- main


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "reshape.json")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    log_file = cfg.get("log_file") or os.path.join(
        os.path.dirname(os.path.abspath(cfg_path)), "kio_reshaper.log")
    # 日志轮转（照 EMQX log.file）：写满就换新文件，留最近几份。
    # 不轮转的话日志会一路涨到把盘占满。
    log_max_mb = int(cfg.get("log_max_mb") or 5)
    log_backups = int(cfg.get("log_backups") or 3)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[RotatingFileHandler(log_file, encoding="utf-8",
                                      maxBytes=log_max_mb * 1024 * 1024,
                                      backupCount=log_backups),
                  logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger("kio_reshaper")

    src, dst = load_config(cfg)
    rules = cfg.get("rules") or [{"name": "default", "template": cfg["template"]}]
    omit_missing = bool(cfg.get("omit_missing", True))

    dst_cli = make_client(dst["broker"], "kio_reshaper_dst")
    src_cli = make_client(src["broker"], "kio_reshaper_src")

    stats = {"in": 0, "out": 0, "bad": 0, "drop": 0}

    def on_msg(topic, payload):
        stats["in"] += 1
        try:
            doc = json.loads(payload.decode("utf-8"))
        except Exception as e:
            stats["bad"] += 1
            log.warning("drop non-JSON from %s: %s", topic, e)
            return
        if not isinstance(doc, dict):
            stats["bad"] += 1
            log.warning("drop non-object payload from %s", topic)
            return
        try:
            msgs = reshape(doc, rules, omit_missing)
        except Exception as e:
            stats["bad"] += 1
            log.error("reshape failed: %s", e)
            return
        for out in msgs:
            try:
                dst_cli.publish(dst["topic"], out,
                                qos=int(dst.get("qos", 0)),
                                retain=bool(dst.get("retain", False)))
            except Exception as e:
                stats["drop"] += 1
                log.error("publish to %s failed: %s", dst["topic"], e)
                continue
            stats["out"] += 1
            log.info("in=%s -> %s", topic, json.dumps(out, ensure_ascii=False))

    src_cli.on_message = on_msg

    log.info("=== reshaper start ===")
    log.info("SOURCE      %s:%s  topic=%s  client_id=%s",
             src["broker"]["host"], src["broker"].get("port", 1883),
             src["topic"], src["broker"].get("client_id"))
    if src.get("_discovered"):
        d = src["_discovered"]
        log.info("  ^ 自动读取自 IOServer: method=%s  file=%s", d["method"], d["file"])
    else:
        log.info("  ^ 来源取自 reshape.json 的 source 段")
    log.info("DESTINATION %s:%s  topic=%s  client_id=%s  qos=%s retain=%s",
             dst["broker"]["host"], dst["broker"].get("port", 1883),
             dst["topic"], dst["broker"].get("client_id"),
             dst.get("qos", 0), dst.get("retain", False))
    log.info("rules=%s", [r.get("name") for r in rules])

    threading.Thread(target=run_forever, daemon=True,
                     args=(dst_cli, "destination", log)).start()
    run_forever(src_cli, "source", log,
                subscribe_topic=src["topic"],
                subscribe_qos=int(src.get("qos", 0)))


if __name__ == "__main__":
    main()
