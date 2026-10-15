"""Web console for the KingIOServer MQTT reshaper.

Zero third-party dependencies -- stdlib http.server only.

Config model:
  brokers : named connection presets, stored once and reused
  routes  : named schemes ("方案") composing a listen side and a target side

Each route runs its own pair of MQTT connections, keeps its own counters and
message feed, and applies its own rules.

Run:  python -I kio_webui.py [port] [reshape.json]
Then open http://127.0.0.1:<port>/
"""
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

from kio_reshaper import (  # noqa: E402
    MqttClient, load_routes, reshape, apply_template, probe_broker,
)
from kio_autodiscover import discover  # noqa: E402
from kio_store import get_store, get_actions, KINDS  # noqa: E402

CONFIG_PATH = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "reshape.json")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
BIND = os.environ.get("KIO_WEBUI_BIND", "127.0.0.1")
HTML_PATH = os.path.join(HERE, "webui.html")
MAX_FEED = 200


# ------------------------------------------------------------------ state

class RouteState:
    def __init__(self, name, idx):
        self.name = name
        self.idx = idx
        self.src_connected = False
        self.dst_connected = False
        self.src_last_ts = 0.0
        self.dst_last_ts = 0.0
        self.stats = {"in": 0, "out": 0, "bad": 0, "drop": 0}
        self.feed_raw = []
        self.pairs = {}
        self.rules = []
        self.omit_missing = True
        self.topics = []
        self.dst_topics = []     # one entry per target
        self._seq = 0


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.cfg = {}
        self.routes = []
        self.log = []
        self.started = time.time()
        self.engine_running = False   # 转发引擎在不在跑（区别于 Web 服务本身）

    def logline(self, level, text, route=None):
        now = time.time()
        # 日志文案统一是「[方案名] ...」,从这里回填 route,便于按方案筛选
        if route is None and text.startswith("["):
            i = text.find("]")
            if i > 1:
                route = text[1:i]
        with self.lock:
            self.log.append({"t": now, "level": level, "text": text, "route": route})
            self.log = self.log[-300:]
        try:
            get_actions().log(kind="连接", op=_op_of(text), target=route or "",
                              detail="", level=level, route=route or "", text=text)
        except Exception:
            pass

    def snapshot(self):
        with self.lock:
            return {
                "uptime": int(time.time() - self.started),
                "engine": self.engine_running,
                "routes": [{
                    "idx": r.idx,
                    "name": r.name,
                    "running": bool(getattr(r, "running", False)),
                    "src_connected": r.src_connected,
                    "dst_connected": r.dst_connected,
                    "src_last_ts": r.src_last_ts,
                    "dst_last_ts": r.dst_last_ts,
                    "stats": dict(r.stats),
                    "topics": list(r.topics),
                    "dst_topics": list(r.dst_topics),
                    "rules": [x.get("name") for x in r.rules],
                } for r in self.routes],
            }


STATE = State()
EVENTS = []
EVENTS_LOCK = threading.Lock()


def emit(evt):
    with EVENTS_LOCK:
        EVENTS.append(evt)
        if len(EVENTS) > 500:
            del EVENTS[:200]


def route_state(idx):
    with STATE.lock:
        for r in STATE.routes:
            if r.idx == idx:
                return r
    return None


# ------------------------------------------------------------------ engine


class RouteRunner:
    """Owns the two MQTT connections for one route.

    不再继承 Thread：start_route() 只是把收发线程拉起来，可以反复启停。
    """

    def __init__(self, conf, idx):
        self.conf = conf
        self.idx = idx
        self.name = conf.get("name") or ("方案%d" % (idx + 1))
        self.st = RouteState(self.name, idx)
        # rules now live on each target; collect the union for display / preview
        seen, union = set(), []
        for t in (conf.get("targets") or []):
            for r in (t.get("rules") or []):
                n = r.get("name") if isinstance(r, dict) else str(r)
                if n and n not in seen:
                    seen.add(n)
                    union.append(r if isinstance(r, dict) else {"name": n})
        self.st.rules = union
        self.st.topics = list(conf.get("source", {}).get("topics") or [])
        self.st.dst_topics = [(t.get("topic") or "") for t in (conf.get("targets") or [])]
        self.stop_evt = threading.Event()
        self.src_cli = None
        self.dst_clis = []        # one connection per target
        self._threads = []        # 这条方案的收发线程，启停据此判断

    @staticmethod
    def _client(b, tag, cid_hint):
        cid = (cid_hint or "").strip() or ("%s_%s" % (tag, uuid.uuid4().hex[:6]))
        return MqttClient(b["host"], int(b.get("port", 1883)), cid,
                          b.get("username"), b.get("password"), b.get("keepalive", 30))

    def start_route(self):
        """把这条方案的收发线程拉起来。可反复调用。

        服务启动时**不**自动跑方案——按说好的，重启后方案默认关闭，
        要在「方案配置」里手动启动。
        """
        if self._threads and any(t.is_alive() for t in self._threads):
            return False, "「%s」已经在运行" % self.name
        self.stop_evt.clear()
        self.dst_clis = []
        src = self.conf["source"]
        targets = self.conf.get("targets") or []
        self.src_cli = self._client(src["broker"], "r%dsrc" % self.idx, src.get("client_id"))
        self.src_cli.on_message = self._on_msg
        self._threads = []
        for k, t in enumerate(targets):
            cli = self._client(t["broker"], "r%ddst%d" % (self.idx, k), t.get("client_id"))
            self.dst_clis.append(cli)
            th = threading.Thread(target=self._serve, daemon=True,
                                  args=(cli, "dst", None, 0))
            th.start()
            self._threads.append(th)
        th = threading.Thread(target=self._serve, daemon=True,
                              args=(self.src_cli, "src", self.st.topics,
                                    int(src.get("qos", 0))))
        th.start()
        self._threads.append(th)
        with STATE.lock:
            self.st.running = True
        return True, "「%s」已启动" % self.name

    @property
    def running(self):
        return bool(self._threads) and any(t.is_alive() for t in self._threads)

    def shutdown(self):
        self.stop_evt.set()
        for c in [self.src_cli] + list(self.dst_clis or []):
            try:
                if c:
                    c.disconnect()
            except Exception:
                pass
        self._threads = []
        self.src_cli = None
        self.dst_clis = []
        with STATE.lock:
            self.st.src_connected = False
            self.st.dst_connected = False
            self.st.running = False

    def _serve(self, cli, role, topics, qos):
        backoff = 1
        fails = 0
        while not self.stop_evt.is_set():
            try:
                cli.connect()
                if fails:
                    STATE.logline("info", "[%s] %s 已恢复，此前连续失败 %d 次"
                                  % (self.name, role, fails))
                backoff, fails = 1, 0
                with STATE.lock:
                    setattr(self.st, ("src_connected" if role == "src" else "dst_connected"), True)
                STATE.logline("info", "[%s] %s 已连接 %s:%s" % (self.name, role, cli.host, cli.port))
                emit("status")
                cli.loop_forever(topics, qos)
                STATE.logline("warn", "[%s] %s 连接断开" % (self.name, role))
            except Exception as e:
                fails += 1
                # 连续失败时日志降频：前 3 次都记，之后每 10 次记一次，
                # 免得 broker 停机那几分钟把日志和动作记录刷爆
                if fails <= 3 or fails % 10 == 0:
                    STATE.logline("error", "[%s] %s 连接失败 (第 %d 次): %s"
                                  % (self.name, role, fails, e))
            with STATE.lock:
                setattr(self.st, ("src_connected" if role == "src" else "dst_connected"), False)
            emit("status")
            try:
                cli.disconnect()
            except Exception:
                pass
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def _on_msg(self, topic, payload):
        now = time.time()
        st = self.st
        try:
            text = payload.decode("utf-8")
            doc = json.loads(text)
        except Exception as e:
            with STATE.lock:
                st.stats["bad"] += 1
            STATE.logline("warn", "[%s] 非 JSON 已丢弃: %s" % (self.name, e))
            return

        with STATE.lock:
            st._seq += 1
            rid = "m%d" % st._seq
            st.src_last_ts = now
            st.stats["in"] += 1
            st.feed_raw.append({"id": rid, "t": now, "topic": topic, "text": text})
            st.pairs[rid] = []
            if len(st.feed_raw) > MAX_FEED:
                for d in st.feed_raw[:-MAX_FEED]:
                    st.pairs.pop(d["id"], None)
                st.feed_raw = st.feed_raw[-MAX_FEED:]
            rules = st.rules
            omit = st.omit_missing
        emit("raw")

        try:
            targets = self.conf.get("targets") or []
        except Exception:
            targets = []
        emit("raw")
        try:
            get_store().log_message(now, self.name, "in", topic, rid, text)
        except Exception:
            pass

        # each target carries its own rule set -> convert and publish per target
        for k, t in enumerate(targets):
            cli = self.dst_clis[k] if k < len(self.dst_clis) else None
            if not cli:
                continue
            rules = t.get("rules") or []
            try:
                msgs = reshape(doc, rules, omit) if isinstance(doc, dict) else []
            except Exception as e:
                with STATE.lock:
                    st.stats["bad"] += 1
                STATE.logline("error", "[%s] 目标#%d 转换失败: %s" % (self.name, k + 1, e))
                continue
            if not msgs:
                continue
            topic = t.get("topic") or ""
            for out in msgs:
                body = json.dumps(out, ensure_ascii=False)
                try:
                    cli.publish(topic, out,
                                qos=int(t.get("qos", 0)), retain=bool(t.get("retain", False)))
                except Exception as e:
                    with STATE.lock:
                        st.stats["drop"] += 1
                    STATE.logline("error", "[%s] 发布到 %s 失败: %s" % (self.name, topic, e))
                    continue
                with STATE.lock:
                    st.dst_last_ts = now
                    st.stats["out"] += 1
                    st.pairs.setdefault(rid, []).append({"t": now, "text": body, "topic": topic})
                try:
                    get_store().log_message(now, self.name, "out", topic, rid, body)
                except Exception:
                    pass
            emit("out")


RUNNERS = []


class Engine:
    def start(self):
        self.stop()
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            STATE.logline("error", "配置读取失败: %s" % e)
            return
        try:
            routes = load_routes(cfg)
        except Exception as e:
            STATE.logline("error", "配置解析失败: %s" % e)
            return

        with STATE.lock:
            STATE.cfg = cfg
            STATE.routes = []
            STATE.engine_running = True
        for i, r in enumerate(routes):
            # 不自动跑——重启后方案默认关闭，要在「方案配置」里手动启动
            run = RouteRunner(r, i)
            with STATE.lock:
                STATE.routes.append(run.st)
            RUNNERS.append(run)
            STATE.logline("info", "方案就绪 [%s]  %s -> %s  规则 %d 条（待手动启动）" % (
                run.name, ",".join(run.st.topics),
                " + ".join(run.st.dst_topics) or "-", len(run.st.rules)))
        STATE.logline("info", "服务已启动，%d 个方案已就绪（默认未启动）" % len(RUNNERS))

    def start_route(self, idx):
        for r in RUNNERS:
            if r.idx == idx:
                ok, msg = r.start_route()
                STATE.logline("info" if ok else "warn", msg)
                return ok, msg
        return False, "找不到方案 %s" % idx

    def stop_route(self, idx):
        for r in RUNNERS:
            if r.idx == idx:
                if not r.running:
                    return True, "「%s」本来就没在跑" % r.name
                r.shutdown()
                STATE.logline("info", "「%s」已停止" % r.name)
                return True, "「%s」已停止" % r.name
        return False, "找不到方案 %s" % idx

    def stop(self):
        while RUNNERS:
            r = RUNNERS.pop()
            try:
                r.shutdown()
            except Exception:
                pass
        with STATE.lock:
            STATE.engine_running = False
        time.sleep(0.2)


ENGINE = Engine()


# ------------------------------------------------------------------ http


def load_html():
    if os.path.isfile(HTML_PATH):
        with open(HTML_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>webui.html not found</h1>"


def _strip_brokers(cfg):
    """Remove passwords but tell the UI whether one is set (has_password)."""
    out = json.loads(json.dumps(cfg))
    for b in (out.get("brokers") or {}).values():
        if isinstance(b, dict):
            b["has_password"] = bool(b.pop("password", None))
    for r in out.get("routes") or []:
        for side in ("from", "to"):
            if isinstance(r.get(side), dict):
                r[side].pop("password", None)
        for t in r.get("targets") or []:
            if isinstance(t, dict):
                t.pop("password", None)
    for side in ("source", "destination"):
        if isinstance(out.get(side), dict) and isinstance(out[side].get("broker"), dict):
            out[side]["broker"].pop("password", None)
    return out


# ---------------------------------------------------------------- 动作留痕

def _op_of(text):
    """从日志文案猜动作类型，用于连接类事件。

    日志中英混杂（`reshaper start` / `已连接`），两套关键词都认。
    """
    t = text or ""
    for k in ("断开", "lost", "断连"):
        if k in t:
            return "断开"
    for k in ("失败", "error", "fail", "refused", "拒绝"):
        if k in t:
            return "失败"
    for k in ("启动", "start"):
        if k in t:
            return "启动"
    for k in ("连接", "connect"):
        if k in t:
            return "连接"
    return "事件"


def _summarise(kind, obj):
    """新增/删除时用一句话说清这个对象是什么。"""
    if not isinstance(obj, dict):
        return ""
    if kind == "broker":
        bits = ["%s:%s" % (obj.get("host") or "-", obj.get("port") or "-")]
        if obj.get("topic"):
            bits.append("主题 " + str(obj["topic"]))
        return " ".join(bits)
    if kind == "规则配置":
        bits = []
        if obj.get("match"):
            bits.append("匹配 " + str(obj["match"]))
        if obj.get("each"):
            bits.append("扇出 " + str(obj["each"]))
        tpl = obj.get("template") or {}
        if tpl:
            bits.append("字段 " + "、".join(sorted(tpl)))
        return " ".join(bits)
    if kind == "方案":
        f = obj.get("from") or {}
        tg = obj.get("targets") or []
        bits = ["监听 %s/%s" % (f.get("broker") or "-", ",".join(f.get("topics") or []) or "-")]
        if tg:
            bits.append("目标 %d 个" % len(tg))
        if obj.get("enabled") is False:
            bits.append("已停用")
        return " ".join(bits)
    return ""


def _brief(o, n):
    """修改时列出改了哪几个字段。"""
    if not isinstance(o, dict) or not isinstance(n, dict):
        return "内容有变"
    keys = {k for k in set(o) | set(n) if o.get(k) != n.get(k)}
    # 密码改动只说改了，不写值
    return ("改动项：" + "、".join(sorted(keys))) if keys else "内容有变"


def _for_diff(obj):
    """对比前归一化，避免把界面的回传差异当成真改动。

    has_password 是界面的展示字段（密码换成布尔），服务端配置里没有；
    拿它对比会让每个 broker 都被误判成「修改」。密码本身按值比（能发现真改密），
    但 detail 里只会出现字段名，不会带出值。
    """
    if isinstance(obj, dict):
        return {k: _for_diff(v) for k, v in obj.items() if k != "has_password"}
    if isinstance(obj, list):
        return [_for_diff(x) for x in obj]
    return obj


def diff_config(old, new):
    """对比两份配置，产出 [(kind, op, target, detail), ...]。

    kind 取 broker / 方案 / 规则配置；op 取 新增 / 修改 / 删除。
    按 name 归一比较，方案是列表也照样比。
    """
    old, new = _for_diff(old), _for_diff(new)
    out = []

    def cmp_named(kind, key):
        o = old.get(key) or {}
        n = new.get(key) or {}
        o = o if isinstance(o, dict) else {}
        n = n if isinstance(n, dict) else {}
        for name in sorted(set(o) | set(n)):
            if name not in o:
                out.append((kind, "新增", name, _summarise(kind, n[name])))
            elif name not in n:
                out.append((kind, "删除", name, _summarise(kind, o[name])))
            elif o[name] != n[name]:
                out.append((kind, "修改", name, _brief(o[name], n[name])))

    def cmp_routes():
        def norm(rs):
            d = {}
            for r in (rs or []):
                if isinstance(r, dict):
                    d[str(r.get("name") or "")] = r
            return d
        o, n = norm(old.get("routes")), norm(new.get("routes"))
        for name in sorted(set(o) | set(n)):
            if name not in o:
                out.append(("方案", "新增", name, _summarise("方案", n[name])))
            elif name not in n:
                out.append(("方案", "删除", name, _summarise("方案", o[name])))
            elif o[name] != n[name]:
                out.append(("方案", "修改", name, _brief(o[name], n[name])))

    cmp_named("broker", "brokers")
    cmp_named("规则配置", "rule_library")
    cmp_routes()
    return out


def log_config_diff(old, new, acts=None):
    """把配置差异写进动作记录。每一处增删改单独一条。"""
    try:
        acts = acts or get_actions()
        for kind, op, target, detail in diff_config(old, new):
            acts.log(kind=kind, op=op, target=target, detail=detail,
                     level="info" if op != "删除" else "warn",
                     text="%s %s「%s」%s" % (kind, op, target, detail))
    except Exception:
        pass


def _named_rules(cfg, names):
    """按名字从规则配置里取规则，返回 reshape 能直接用的字典列表。

    保持传入顺序——多条规则是按顺序套用的，打乱会得到不同结果。
    """
    lib = cfg.get("rule_library") or {}
    out = []
    for n in names or []:
        r = lib.get(str(n))
        if isinstance(r, dict):
            d = dict(r)
            d.setdefault("name", str(n))
            out.append(d)
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        q = self.path
        if path == "/":
            self._send(200, load_html(), "text/html; charset=utf-8")
        elif path == "/api/status":
            self._send(200, STATE.snapshot())
        elif path == "/api/feed":
            idx = _int(q, "route", 0)
            rs = route_state(idx)
            self._send(200, list(rs.feed_raw[-100:]) if rs else [])
        elif path == "/api/pair":
            idx = _int(q, "route", 0)
            rid = _str(q, "id")
            rs = route_state(idx)
            self._send(200, list(rs.pairs.get(rid, [])) if rs else [])
        elif path == "/api/broker":
            # Per-broker detail incl. password, fetched only when the user opens the
            # entry for editing. User explicitly approved showing the password: the
            # console binds to 127.0.0.1 and the value is already plaintext on disk.
            # Kept out of /api/config so a list refresh doesn't ship every password.
            name = _str(q, "name")
            with STATE.lock:
                b = (STATE.cfg.get("brokers") or {}).get(name)
            if not isinstance(b, dict):
                self._send(404, {"error": "not found"})
            else:
                self._send(200, {"name": name, "broker": dict(b)})
        elif path == "/api/config":
            with STATE.lock:
                cfg = json.loads(json.dumps(STATE.cfg))
            self._send(200, {"config": _strip_brokers(cfg),
                             "brokers": _strip_brokers(cfg).get("brokers") or {},
                             "routes": cfg.get("routes") or []})
        elif path == "/api/log":
            with STATE.lock:
                self._send(200, list(STATE.log)[-120:])
        elif path == "/api/history":
            st = get_store()
            rows = st.query_messages(
                route=_str(q, "route") or None,
                direction=_str(q, "direction") or None,
                keyword=_str(q, "keyword") or None,
                since=_float(q, "since"),
                until=_float(q, "until"),
                before=_float(q, "before"),
                limit=_int(q, "limit", 200))
            self._send(200, {"rows": rows, "counts": st.counts()})
        elif path == "/api/events":
            al = get_actions()
            rows = al.query(
                level=_str(q, "level") or None,
                kind=_str(q, "kind") or None,
                op=_str(q, "op") or None,
                keyword=_str(q, "keyword") or None,
                since=_float(q, "since"),
                until=_float(q, "until"),
                before=_float(q, "before"),
                limit=_int(q, "limit", 200))
            self._send(200, {"rows": rows, "counts": al.counts(),
                             "kinds": al.kinds()})
        elif path == "/api/storage":
            info = get_store().storage_info()
            info["actions"] = get_actions().counts()
            self._send(200, info)
        elif path == "/api/broker_status":
            # 已存 broker 的连通性（只读，所以放 GET）。并行探——串行的话每个
            # 都要等超时，切个页面要卡好几秒。随机 client_id，不会踢掉在用会话。
            try:
                with STATE.lock:
                    brokers = json.loads(json.dumps(STATE.cfg.get("brokers") or {}))
                out, lk = {}, threading.Lock()

                def _probe(name, b):
                    r = probe_broker(b.get("host"), b.get("port") or 1883,
                                     (b.get("username") or "").strip() or None,
                                     b.get("password"), 4)
                    with lk:
                        out[name] = r

                ths = [threading.Thread(target=_probe, args=(n, b), daemon=True)
                       for n, b in brokers.items()]
                for t in ths:
                    t.start()
                for t in ths:
                    t.join(timeout=8)
                self._send(200, {"brokers": out})
            except Exception as e:
                self._send(500, {"error": str(e)})
        elif path == "/api/export":
            st = get_store()
            csv = st.export_csv(
                route=_str(q, "route") or None,
                direction=_str(q, "direction") or None,
                keyword=_str(q, "keyword") or None,
                since=_float(q, "since"),
                until=_float(q, "until"))
            self._send(200, csv, "text/csv; charset=utf-8")
        elif path == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                last = 0
                while True:
                    with EVENTS_LOCK:
                        batch = EVENTS[last:]
                        last = len(EVENTS)
                    for e in batch or ["ping"]:
                        self.wfile.write(("data: %s\n\n" % e).encode())
                        self.wfile.flush()
                    time.sleep(1.0)
            except Exception:
                pass
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:
            self._send(400, {"error": "bad json: %s" % e})
            return
        path = self.path.split("?")[0]

        if path == "/api/discover":
            try:
                # Password is returned here so the "读取" button can pre-fill the
                # form. Acceptable because the server binds to 127.0.0.1 only and
                # the value is already stored in plaintext in the IOServer config.
                info = discover(body.get("root"))
                self._send(200, info)
            except Exception as e:
                self._send(500, {"error": str(e)})

        elif path == "/api/ping":
            # Connectivity probe for the 配置broker form. Only checks that IP /
            # port / username / password can get a CONNACK — it does not subscribe
            # or persist anything, and the client_id is random per call.
            try:
                b = body.get("broker") if isinstance(body.get("broker"), dict) else body
                host = (b.get("host") or "").strip()
                if not host:
                    self._send(400, {"error": "缺少 broker IP"})
                    return
                self._send(200, probe_broker(
                    host,
                    b.get("port") or 1883,
                    (b.get("username") or "").strip() or None,
                    b.get("password"),
                    float(b.get("timeout") or 5)))
            except Exception as e:
                self._send(500, {"error": str(e)})

        elif path == "/api/test":
            try:
                doc = body.get("payload")
                if isinstance(doc, str):
                    doc = json.loads(doc)
                # 优先按规则名来——试算模板就是试算规则，不必绕道方案
                names = body.get("rules")
                if names:
                    with STATE.lock:
                        cfg = STATE.cfg
                    rules = _named_rules(cfg, names)
                    if not rules:
                        self._send(400, {"error": "这些规则不存在: %s" % ", ".join(map(str, names))})
                        return
                    omit = bool(cfg.get("omit_missing", True))
                else:
                    idx = body.get("route", 0)
                    rs = route_state(idx)
                    rules = (rs.rules if rs else [])
                    omit = (rs.omit_missing if rs else True)
                msgs = reshape(doc, rules, omit)
                self._send(200, {"count": len(msgs), "messages": msgs,
                                 "rules": [r.get("name") or "?" for r in rules]})
            except Exception as e:
                self._send(500, {"error": str(e)})

        elif path == "/api/config":
            try:
                cfg = body.get("config")
                if isinstance(cfg, str):
                    cfg = json.loads(cfg)
                with STATE.lock:
                    old = json.loads(json.dumps(STATE.cfg))
                # keep passwords the browser never sees (they live on the broker entry;
                # routes only reference a broker by name, so never copy them into from/to)
                oldb = old.get("brokers") or {}
                for name, b in (cfg.get("brokers") or {}).items():
                    if isinstance(b, dict) and not b.get("password") and isinstance(oldb.get(name), dict):
                        b["password"] = oldb[name].get("password")
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                # 增删改留痕：按 name 对比，每处改动单独一条动作记录
                log_config_diff(old, cfg)
                # restart:false -> persist only (edits survive a page refresh),
                # restart:true  -> persist and rebuild the connections
                if body.get("restart", True):
                    ENGINE.start()
                else:
                    with STATE.lock:
                        STATE.cfg = cfg     # keep /api/config in sync with the file
                self._send(200, {"ok": True})
            except Exception as e:
                self._send(500, {"error": str(e)})

        elif path == "/api/storage":
            # 存储管理：设置段上限/段周期，连接/断开/删除某段，手动清除。
            # 全是本地管理动作，靠 body 的 action 分派。
            st = get_store()
            act = body.get("action") or ""
            name = body.get("name") or ""
            try:
                if act == "settings":
                    ok, msg = True, "已保存"
                    st.set_settings(body.get("max_mb"), body.get("segment_days"))
                    name = "段上限 %s MB / 段周期 %s 天" % (
                        body.get("max_mb"), body.get("segment_days"))
                elif act == "connect":
                    ok, msg = st.connect(name)
                elif act == "disconnect":
                    ok, msg = st.disconnect(name)
                elif act == "delete":
                    ok, msg = st.delete(name)
                elif act == "purge_all":
                    ok, msg = st.purge_all()
                    name = "全部数据库"
                elif act == "purge_oldest":
                    ok, msg = st.purge_oldest(body.get("n") or 1)
                    name = "最旧 %s 个库" % (body.get("n") or 1)
                elif act == "purge_range":
                    ok, msg = st.purge_range(float(body.get("since") or 0),
                                             float(body.get("until") or time.time()))
                    name = "%s ~ %s" % (body.get("since"), body.get("until"))
                elif act == "clear_current":
                    ok, msg = st.clear_current()
                    name = "当前库"
                elif act == "clear_actions":
                    ok, msg = get_actions().clear()
                    name = "动作记录"
                else:
                    ok, msg = False, "未知操作：%s" % act
                # 数据库的增删改也要留痕
                if act:
                    try:
                        get_actions().log(
                            kind="数据库",
                            op={"settings": "修改", "connect": "连接",
                                "disconnect": "断开", "delete": "删除",
                                "purge_all": "清除", "purge_oldest": "清除",
                                "purge_range": "清除", "clear_current": "清除",
                                "clear_actions": "清除"}
                            .get(act, act),
                            target=name, detail=msg,
                            level="warn" if act in ("delete", "purge_all",
                                                    "purge_oldest", "purge_range",
                                                    "clear_current") else "info",
                            text="数据库 %s %s — %s" % (act, name, msg))
                    except Exception:
                        pass
                resp = st.storage_info()
                try:
                    resp["actions"] = get_actions().counts()
                except Exception:
                    pass
                self._send(200 if ok else 400, {"ok": ok, "message": msg, **resp})
            except Exception as e:
                self._send(500, {"error": str(e)})

        elif path == "/api/live_test":
            # 在线转换测试：真发真收。发到方案的监听主题，走完整条转发链路，
            # 再把目标主题上实际收到的东西抓回来。
            try:
                idx = int(body.get("route") or 0)
                doc = body.get("payload")
                if isinstance(doc, str):
                    doc = json.loads(doc)
                wait = float(body.get("wait") or 2.5)
                with STATE.lock:
                    cfg = json.loads(json.dumps(STATE.cfg))
                routes = load_routes(cfg)
                if not routes or not (0 <= idx < len(routes)):
                    self._send(400, {"error": "没有可测的方案"})
                    return
                r = routes[idx]
                src = r["source"]
                tgt = (r.get("targets") or [{}])[0]
                src_topic = (src.get("topics") or ["kingscada"])[0]
                dst_topic = tgt.get("topic") or "MES"

                got, gl = [], threading.Lock()

                def on_msg(t, p):
                    with gl:
                        got.append({"topic": t,
                                    "body": p.decode("utf-8", "replace")})

                # 先挂好抓取再发，免得漏掉第一条
                sub = RouteRunner._client(tgt["broker"], "livesub", tgt.get("client_id"))
                sub.on_message = on_msg
                sub.connect()
                sub.subscribe(dst_topic, int(tgt.get("qos") or 0))
                threading.Thread(target=sub.loop_forever, daemon=True).start()
                time.sleep(0.5)

                pub = RouteRunner._client(src["broker"], "livepub", src.get("client_id"))
                pub.connect()
                time.sleep(0.3)
                raw = json.dumps(doc, ensure_ascii=False)
                pub.publish(src_topic, raw, int(src.get("qos") or 0))
                time.sleep(wait)
                for c in (pub, sub):
                    try:
                        c.disconnect()
                    except Exception:
                        pass

                self._send(200, {
                    "sent": {"topic": src_topic, "body": raw},
                    "expect_topic": dst_topic,
                    "received": list(got),
                    "count": len(got),
                    "wait": wait,
                })
            except Exception as e:
                self._send(500, {"error": str(e)})

        elif path == "/api/engine":
            # 转发引擎启停。注意：这只管「转发」，Web 服务本身不在这里管——
            # 网页没法启动/杀死自己所在的进程，那要靠 bat 或开机自启。
            act = (body.get("action") or "").strip()
            try:
                if act == "stop":
                    ENGINE.stop()
                    ok, msg = True, "转发已停止（Web 界面还在）"
                elif act in ("start", "restart"):
                    ENGINE.start()
                    ok = True
                    msg = ("服务已启动，%d 个方案已就绪（默认关闭，去「方案配置」逐个启动）"
                           % len(STATE.routes))
                else:
                    ok, msg = False, "未知操作：%s" % act
                self._send(200 if ok else 400,
                           {"ok": ok, "message": msg, "status": STATE.snapshot()})
            except Exception as e:
                self._send(500, {"error": str(e)})

        elif path == "/api/route":
            # 单个方案的启动 / 停止。服务启动时方案默认关闭，靠这个手动开。
            idx = body.get("idx")
            act = (body.get("action") or "").strip()
            try:
                if act == "start":
                    ok, msg = ENGINE.start_route(int(idx))
                elif act == "stop":
                    ok, msg = ENGINE.stop_route(int(idx))
                else:
                    ok, msg = False, "未知操作：%s" % act
                self._send(200 if ok else 400,
                           {"ok": ok, "message": msg, "status": STATE.snapshot()})
            except Exception as e:
                self._send(500, {"error": str(e)})

        elif path == "/api/selfheal":
            # 自动诊断 + 修复：能修的当场修，修不了的说清楚为什么。
            found, fixed, failed = [], [], []
            cfg = None
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                found.append("配置文件可读（%d 字节）" % os.path.getsize(CONFIG_PATH))
            except Exception as e:
                failed.append("配置文件读不出来：%s" % e)

            if cfg is not None:
                try:
                    rs = load_routes(cfg)
                    found.append("配置解析正常，%d 个方案" % len(rs))
                except Exception as e:
                    failed.append("方案配置有问题，可能是 broker 名字对不上：%s" % e)

            try:
                c = get_store().counts()
                found.append("报文库可用：%d 段 / %.1f MB / %d 条"
                             % (c.get("segments") or 0, c.get("total_mb") or 0,
                                c.get("messages") or 0))
            except Exception as e:
                failed.append("报文库不可用：%s" % e)

            try:
                a = get_actions().counts()
                found.append("动作记录可用：%d 条" % (a.get("actions") or 0))
            except Exception as e:
                failed.append("动作记录不可用：%s" % e)

            # 连接重建是「修」的核心——大多数问题重连一下就好
            try:
                ENGINE.start()
                fixed.append("已重建全部转发连接（监听 / 目标各重新握手）")
            except Exception as e:
                failed.append("重建连接失败：%s" % e)

            # broker 逐个探，通的说通，不通的报出来
            try:
                brokers = (cfg or {}).get("brokers") or {}
                for n, b in brokers.items():
                    r = probe_broker(b.get("host"), b.get("port") or 1883,
                                     (b.get("username") or "").strip() or None,
                                     b.get("password"), 4)
                    if r.get("ok"):
                        found.append("broker「%s」%s:%s 连得上" % (n, b.get("host"), b.get("port")))
                    else:
                        failed.append("broker「%s」%s:%s 连不上 —— %s"
                                      % (n, b.get("host"), b.get("port"), r.get("message")))
            except Exception as e:
                failed.append("broker 探测出错：%s" % e)

            self._send(200, {"found": found, "fixed": fixed, "failed": failed,
                             "ok": not failed})

        elif path == "/api/restart":
            threading.Thread(target=ENGINE.start, daemon=True).start()
            self._send(200, {"ok": True})

        else:
            self._send(404, {"error": "not found"})


def _float(q, key):
    try:
        for part in q.split("?", 1)[-1].split("&"):
            k, _, v = part.partition("=")
            if k == key and v:
                return float(v)
    except Exception:
        pass
    return None


def _int(q, key, default=0):
    try:
        for part in q.split("?", 1)[-1].split("&"):
            k, _, v = part.partition("=")
            if k == key:
                return int(v)
    except Exception:
        pass
    return default


def _str(q, key):
    for part in q.split("?", 1)[-1].split("&"):
        k, _, v = part.partition("=")
        if k == key:
            try:
                from urllib.parse import unquote
                return unquote(v)
            except Exception:
                return v
    return ""


def main():
    ENGINE.start()
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    print("Web UI  ->  http://%s:%s/" % (BIND, PORT))
    print("config  ->  %s" % CONFIG_PATH)
    print("Ctrl+C 退出")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
