"""Storage for the reshaper, split in two:

  报文记录  messages  -> 分段分时存储，data/messages/ 下多个 yyyy-mm-dd-NN.db
  动作记录  actions   -> 独立单文件 data/actions.db，不分段，记增删改

stdlib only. Writes are queued and flushed in batches so a burst of traffic
(e.g. one payload fanning out to 400 messages) never blocks the forwarding path.

-- 报文记录 --
History is spread over several small db files, each named `yyyy-mm-dd-NN.db`.

  一个新段的两个触发条件（谁先到谁触发）
    · 达到段上限 max_mb        -> 大于了就开个新 DB
    · 跨过段周期 segment_days  -> 一天一个新 DB

分段的直接好处：删旧数据 = 删掉整个段文件，磁盘空间立刻回来。
单文件方案里 DELETE 不会缩小 SQLite 文件（空页留作复用），只能靠 VACUUM 硬压。
旧数据不自动删——段多了由人在界面上手动删。

-- 动作记录 --
配置的增删改（broker / 方案 / 规则配置 / 数据库）和连接运行事件都进这里。
这类数据量小、需要长期可查，所以**不分段**，单文件即可。
"""
import os
import queue
import sqlite3
import threading
import time
from datetime import date, datetime

# 目录约定（照知识工坊Web_V1.0 的八卦分层）
#   乾\2运势  运行代码与 reshape.json，本文件在这
#   坤\       数据（报文分段 messages\ + 动作记录 actions.db）
#   震\       启动入口（bat + launch.py）
#   巽\       运维工具
#   艮\       跳过——本项目无冗余
HERE = os.path.dirname(os.path.abspath(__file__))     # ...\乾\2运势
ROOT = os.path.dirname(os.path.dirname(HERE))         # 项目根
DATA_DIR = os.path.join(ROOT, "坤")                   # 数据都归坤
MSG_DIR = os.path.join(DATA_DIR, "messages")
ACTIONS_DB = os.path.join(DATA_DIR, "actions.db")
LEGACY_DB = os.path.join(ROOT, "kio_history.db")      # 旧单文件位置，仅用于迁移
DEFAULT_DB = LEGACY_DB

FLUSH_EVERY = 0.4          # seconds
FLUSH_MAX = 500            # rows per flush
SCAN_EVERY = 30            # seconds between rotation checks

DEFAULT_SEGMENT_MB = 500       # 段上限：超过就开个新 DB
DEFAULT_SEGMENT_DAYS = 1       # 段周期：一天一个新 DB

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        REAL NOT NULL,
  route     TEXT,
  direction TEXT,
  topic     TEXT,
  raw_id    TEXT,
  body      TEXT
);
CREATE INDEX IF NOT EXISTS ix_msg_ts  ON messages(ts);
CREATE INDEX IF NOT EXISTS ix_msg_raw ON messages(raw_id, direction);
CREATE INDEX IF NOT EXISTS ix_msg_dir ON messages(direction, ts);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

_ACTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  ts     REAL NOT NULL,
  level  TEXT,
  kind   TEXT,
  op     TEXT,
  target TEXT,
  detail TEXT,
  route  TEXT,
  text   TEXT
);
CREATE INDEX IF NOT EXISTS ix_act_ts   ON actions(ts);
CREATE INDEX IF NOT EXISTS ix_act_kind ON actions(kind, ts);
"""

# 动作类别（kind）——界面上的筛选项
KINDS = ("broker", "方案", "规则配置", "数据库", "连接", "系统")


# ------------------------------------------------------------------ segments

class Segment:
    """One history db file.

    Connected = its rows take part in queries. Every segment starts connected
    so history stays visible across the whole retention window; disconnect is a
    deliberate action for releasing handles / shrinking the query set.

    Row counts and time span live in the segment's own `meta` table, so listing
    a directory of segments does not need a COUNT(*) scan per file.
    """

    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.connected = False
        self.is_current = False
        self.messages = 0
        self.first_ts = None
        self.last_ts = None
        self.created = None
        self._con = None

    # -- lifecycle ---------------------------------------------------
    def open(self):
        self._con = sqlite3.connect(self.path, check_same_thread=False)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=NORMAL")
        self._con.execute("PRAGMA cache_size=-2000")     # ~2MB per segment
        self._con.executescript(_SCHEMA)
        self._con.commit()
        self.connected = True
        self._load_stats()
        return self

    def close(self):
        if self._con is None:
            self.connected = False
            return
        try:
            self._save_stats()
            self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._con.commit()
            self._con.close()
        except Exception:
            pass
        self._con = None
        self.connected = False

    # -- stats (persisted in meta) -----------------------------------
    def _load_stats(self):
        if self._con is None:
            return
        try:
            rows = dict(self._con.execute("SELECT k,v FROM meta").fetchall())
            if rows:
                self.messages = int(rows.get("messages", 0))
                self.first_ts = self._num(rows.get("first_ts"))
                self.last_ts = self._num(rows.get("last_ts"))
                return
        except Exception:
            pass
        self._refresh_stats()
        self._save_stats()

    def _save_stats(self):
        if self._con is None:
            return
        try:
            self._con.executemany(
                "INSERT OR REPLACE INTO meta(k,v) VALUES (?,?)",
                [("messages", str(self.messages)),
                 ("first_ts", "" if self.first_ts is None else repr(self.first_ts)),
                 ("last_ts", "" if self.last_ts is None else repr(self.last_ts))])
            self._con.commit()
        except Exception:
            pass

    def _refresh_stats(self):
        """Recount from SQL -- used when meta is missing (legacy / migrated files)."""
        if self._con is None:
            return
        try:
            self.messages = self._con.execute(
                "SELECT COUNT(*) FROM messages").fetchone()[0]
            r = self._con.execute("SELECT MIN(ts), MAX(ts) FROM messages").fetchone()
            if r and r[0] is not None:
                self.first_ts, self.last_ts = r[0], r[1]
        except Exception:
            pass

    @staticmethod
    def _num(v):
        if v in (None, ""):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # -- disk --------------------------------------------------------
    def file_bytes(self):
        total = 0
        for s in ("", "-wal", "-shm"):
            p = self.path + s
            try:
                if os.path.exists(p):
                    total += os.path.getsize(p)
            except OSError:
                pass
        return total

    def drop_files(self):
        """Unlink the db and its sidecars."""
        for s in ("", "-wal", "-shm"):
            try:
                if os.path.exists(self.path + s):
                    os.remove(self.path + s)
            except OSError:
                pass

    def snapshot(self):
        return {
            "name": self.name,
            "size_mb": round(self.file_bytes() / 1048576, 2),
            "messages": self.messages,
            "connected": self.connected,
            "current": self.is_current,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
        }


# ------------------------------------------------------------------ store

class Store:
    """报文记录：分段分时。只存 messages，动作记录在 ActionLog。"""

    def __init__(self, data_dir=None, max_mb=DEFAULT_SEGMENT_MB,
                 segment_days=DEFAULT_SEGMENT_DAYS):
        self.data_dir = data_dir or MSG_DIR
        self.max_mb = int(max_mb)
        self.segment_days = int(segment_days)
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._segs = []             # Segment, oldest first
        self._cur = None            # Segment being written to
        self._cur_start_day = None
        self._last_scan = 0.0

        os.makedirs(self.data_dir, exist_ok=True)
        self._migrate_legacy()
        self._scan()
        if self._cur is None:
            self._rotate(force=True)
        threading.Thread(target=self._worker, daemon=True).start()

    # ---------------------------------------------------------------- setup
    def _migrate_legacy(self):
        """Relocate older layouts into the current one. Move only -- never rewrite.

        1) data/*.db        -> data/messages/*.db   （早先段和动作库混在一个目录）
        2) <root>kio_history.db -> data/messages/   （最早的单文件方案）

        段名沿用原来的；单文件按它最早一条记录的日期命名。
        目标已存在时绝不覆盖——宁可多留一个文件，也不能拿小的盖掉大的。
        """
        os.makedirs(self.data_dir, exist_ok=True)

        # 1) 旧目录里的段搬进 messages/
        # 只有当 self.data_dir 就是正式目录时才做——否则测试用临时目录时会把
        # 真实段文件搬进临时目录，rmtree 一清就全没了
        if os.path.abspath(self.data_dir) == os.path.abspath(MSG_DIR):
            # 1a) 最早的一版：段直接放在 data\ 下
            try:
                leftovers = [n for n in os.listdir(DATA_DIR)
                             if n.endswith(".db") and os.path.isfile(os.path.join(DATA_DIR, n))]
            except OSError:
                leftovers = []
            for n in leftovers:
                self._safe_move(os.path.join(DATA_DIR, n),
                                os.path.join(self.data_dir, n))
            # 1b) 改成坤之前的一版：data\messages\ + data\actions.db
            old_root = os.path.join(ROOT, "data")
            old_msg = os.path.join(old_root, "messages")
            if os.path.isdir(old_msg):
                try:
                    for n in os.listdir(old_msg):
                        self._safe_move(os.path.join(old_msg, n),
                                        os.path.join(self.data_dir, n))
                except OSError:
                    pass
            for n in ("actions.db", "actions.db-wal", "actions.db-shm"):
                self._safe_move(os.path.join(old_root, n),
                                os.path.join(DATA_DIR, n))
            try:
                os.rmdir(old_msg)
                os.rmdir(old_root)
            except OSError:
                pass

        # 2) 最早的单文件方案（DEFAULT_DB 可被测试重定向，所以跟着它走）
        if not os.path.exists(DEFAULT_DB):
            return
        day, seq = None, 1
        try:
            con = sqlite3.connect(DEFAULT_DB)
            r = con.execute("SELECT MIN(ts) FROM messages").fetchone()
            con.close()
            if r and r[0] is not None:
                day = datetime.fromtimestamp(r[0]).strftime("%Y-%m-%d")
        except Exception:
            pass
        day = day or date.today().strftime("%Y-%m-%d")
        dest = os.path.join(self.data_dir, "%s-%02d.db" % (day, seq))
        while os.path.exists(dest):
            seq += 1
            dest = os.path.join(self.data_dir, "%s-%02d.db" % (day, seq))
        self._safe_move(DEFAULT_DB, dest)

    @staticmethod
    def _safe_move(src, dst):
        """Move src -> dst, refusing anything that could destroy data."""
        if not os.path.exists(src):
            return False
        if os.path.abspath(src) == os.path.abspath(dst):
            return False
        if os.path.exists(dst):
            try:
                if os.path.getsize(dst) >= os.path.getsize(src):
                    # 目标不比源小：多半是重复迁移，留着不动，绝不覆盖
                    return False
            except OSError:
                return False
        try:
            os.replace(src, dst)
            for s in ("-wal", "-shm"):
                if os.path.exists(src + s):
                    os.replace(src + s, dst + s)
            return True
        except OSError:
            return False

    def _scan(self):
        """(Re)read data/ and pick the segment to write into."""
        with self._lock:
            for s in self._segs:
                s.close()
            self._segs = []
            names = []
            try:
                names = [n for n in os.listdir(self.data_dir) if n.endswith(".db")]
            except OSError:
                pass
            for n in sorted(names):
                seg = Segment(n, os.path.join(self.data_dir, n))
                try:
                    seg.open()
                except Exception:
                    continue
                seg.created = self._created_from_name(n)
                self._segs.append(seg)

            self._cur = None
            self._cur_start_day = None
            today = date.today()
            # 只有「今天的段」且还没到上限，才继续往里写
            for seg in reversed(self._segs):
                if seg.created == today and seg.file_bytes() < self.max_mb * 1048576:
                    self._cur = seg
                    self._cur_start_day = seg.created
                    break
            for seg in self._segs:
                seg.is_current = (seg is self._cur)

    @staticmethod
    def _created_from_name(name):
        try:
            return datetime.strptime(name[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    def _rotate(self, force=False):
        """Stop writing to the current segment and open a fresh one for today.

        The old segment stays CONNECTED so its history remains queryable --
        追溯 is the whole reason to keep it around. Disconnect is a separate,
        deliberate action for when you want to release handles or shrink the
        query set.
        """
        with self._lock:
            if self._cur is not None and not force:
                self._cur.is_current = False
                self._cur._save_stats()
            today = date.today()
            seq = 1
            for seg in self._segs:
                if seg.name.startswith(today.strftime("%Y-%m-%d")):
                    seq += 1
            name = "%s-%02d.db" % (today.strftime("%Y-%m-%d"), seq)
            path = os.path.join(self.data_dir, name)
            seg = Segment(name, path)
            seg.created = today
            seg.is_current = True
            seg.open()
            self._segs.append(seg)
            self._cur = seg
            self._cur_start_day = today

    def _maybe_rotate(self):
        if self._cur is None:
            self._rotate(force=True)
            return
        # 触发一：段上限
        if self._cur.file_bytes() >= self.max_mb * 1048576:
            self._rotate()
            return
        # 触发二：段周期（一天一个新 DB）
        today = date.today()
        if self._cur_start_day is not None:
            if (today - self._cur_start_day).days >= max(1, self.segment_days):
                self._rotate()

    # ---------------------------------------------------------------- writes
    def log_message(self, ts, route, direction, topic, raw_id, body):
        self._q.put(("msg", (ts, route, direction, topic, raw_id, body)))

    def _worker(self):
        while not self._stop.is_set():
            time.sleep(FLUSH_EVERY)
            self._flush()
            if time.time() - self._last_scan > SCAN_EVERY:
                self._last_scan = time.time()
                self._maybe_rotate()
                # 周期把计数写进 meta，中途断电也不会让统计失真
                try:
                    with self._lock:
                        if self._cur is not None:
                            self._cur._save_stats()
                except Exception:
                    pass

    def _flush(self):
        msgs = []
        try:
            while True:
                kind, row = self._q.get_nowait()
                if kind == "msg":
                    msgs.append(row)
                if len(msgs) >= FLUSH_MAX:
                    break
        except queue.Empty:
            pass
        if not msgs:
            return
        try:
            with self._lock:
                if self._cur is None or self._cur._con is None:
                    return
                con = self._cur._con
                con.executemany(
                    "INSERT INTO messages(ts,route,direction,topic,raw_id,body) "
                    "VALUES (?,?,?,?,?,?)", msgs)
                self._cur.messages += len(msgs)
                con.commit()
                for row in msgs:
                    ts = row[0]
                    if self._cur.first_ts is None or ts < self._cur.first_ts:
                        self._cur.first_ts = ts
                    if self._cur.last_ts is None or ts > self._cur.last_ts:
                        self._cur.last_ts = ts
        except Exception:
            pass
        # 落盘后立刻看要不要切段，别等周期扫描——写满就该马上开新库
        try:
            self._maybe_rotate()
        except Exception:
            pass

    # ---------------------------------------------------------------- admin
    def segments(self):
        with self._lock:
            return [s.snapshot() for s in self._segs]

    def settings(self):
        return {
            "max_mb": self.max_mb,
            "segment_days": self.segment_days,
            "data_dir": self.data_dir,
            "naming": "yyyy-mm-dd-{序号}.db",
        }

    def set_settings(self, max_mb=None, segment_days=None):
        with self._lock:
            if max_mb is not None:
                self.max_mb = max(1, int(max_mb))
            if segment_days is not None:
                self.segment_days = max(1, int(segment_days))
        self._maybe_rotate()
        return self.settings()

    def connect(self, name):
        with self._lock:
            seg = self._find(name)
            if seg is None:
                return False, "找不到该数据库 %s" % name
            if seg.is_current:
                return True, "当前段本就保持连接（写入中）"
            if seg.connected:
                return True, "已连接"
            try:
                seg.open()
            except Exception as e:
                return False, "连接失败：%s" % e
            return True, "已连接"

    def disconnect(self, name):
        with self._lock:
            seg = self._find(name)
            if seg is None:
                return False, "找不到该数据库 %s" % name
            if seg.is_current:
                return False, "当前段是写入目标，不能断开；切段后才能断"
            if not seg.connected:
                return True, "已断开"
            seg.close()
            return True, "已断开（查询不再命中，文件句柄已释放）"

    def delete(self, name):
        with self._lock:
            seg = self._find(name)
            if seg is None:
                return False, "找不到该数据库 %s" % name
            if seg.is_current:
                return False, "当前段是写入目标，不能删；切段后才能删"
            seg.close()
            seg.drop_files()
            self._segs.remove(seg)
            return True, "已删除 %s" % name

    def _find(self, name):
        for s in self._segs:
            if s.name == name:
                return s
        return None

    # -- 手动清除 ----------------------------------------------------
    def purge_all(self):
        """Drop every segment and start a fresh one. 一了百了。"""
        with self._lock:
            for seg in list(self._segs):
                seg.close()
                seg.drop_files()
            self._segs = []
        self._rotate(force=True)
        return True, "已清空全部历史"

    def purge_range(self, since, until):
        """Delete rows inside [since, until]; drop the file when it empties."""
        deleted = 0
        with self._lock:
            for seg in list(self._segs):
                if seg.is_current:
                    continue
                if seg.last_ts is not None and seg.last_ts < since:
                    continue
                if seg.first_ts is not None and seg.first_ts > until:
                    continue
                try:
                    if not seg.connected:
                        seg.open()
                    con = seg._con
                    d1 = con.execute(
                        "DELETE FROM messages WHERE ts >= ? AND ts <= ?",
                        (since, until)).rowcount
                    con.commit()
                    deleted += (d1 or 0)
                    seg._refresh_stats()
                    if seg.messages == 0:
                        seg.close()
                        seg.drop_files()
                        self._segs.remove(seg)
                except Exception:
                    pass
            # 当前段也清，但保留文件继续写
            if self._cur is not None and self._cur._con is not None:
                try:
                    d1 = self._cur._con.execute(
                        "DELETE FROM messages WHERE ts >= ? AND ts <= ?",
                        (since, until)).rowcount
                    self._cur._con.commit()
                    deleted += (d1 or 0)
                    self._cur._refresh_stats()
                except Exception:
                    pass
        return True, "已清除时间段内的 %d 条报文" % deleted

    def purge_oldest(self, n):
        """Delete the N oldest non-current segments."""
        removed = []
        with self._lock:
            olds = [s for s in self._segs if not s.is_current][:max(0, int(n))]
            for seg in olds:
                seg.close()
                seg.drop_files()
                self._segs.remove(seg)
                removed.append(seg.name)
        return True, ("已删除最旧 %d 个数据库：%s" % (len(removed), "、".join(removed))
                      if removed else "没有可删的归档段")

    def clear_current(self):
        """Empty the segment being written to, keep the file."""
        with self._lock:
            if self._cur is None or self._cur._con is None:
                return True, "没有正在写入的数据库"
            try:
                self._cur._con.execute("DELETE FROM messages")
                self._cur._con.commit()
                self._cur._refresh_stats()
            except Exception as e:
                return False, "清除失败：%s" % e
        return True, "已清空当前段"

    # ---------------------------------------------------------------- queries
    def _connected(self):
        with self._lock:
            return [s for s in self._segs if s.connected and s._con is not None]

    def _query_all(self, sql, args, limit):
        """Run the same query against every connected segment, merge newest-first.

        Each segment is asked for at most `limit` rows, so the cost stays bounded
        no matter how many segments are mounted.
        """
        out = []
        for seg in self._connected():
            try:
                with self._lock:
                    rows = seg._con.execute(sql, args).fetchall()
            except Exception:
                continue
            out.extend(rows)
        out.sort(key=lambda r: r[0], reverse=True)
        return out[:int(limit)] if limit else out

    def query_messages(self, route=None, direction=None, keyword=None,
                       since=None, until=None, limit=200, before=None):
        """newest first. 'before' (epoch) pages further back."""
        sql = "SELECT ts,route,direction,topic,raw_id,body FROM messages WHERE 1=1"
        args = []
        if route:
            sql += " AND route = ?"
            args.append(route)
        if direction:
            sql += " AND direction = ?"
            args.append(direction)
        if keyword:
            sql += " AND body LIKE ?"
            args.append("%" + keyword + "%")
        if since:
            sql += " AND ts >= ?"
            args.append(since)
        if until:
            sql += " AND ts <= ?"
            args.append(until)
        if before:
            sql += " AND ts < ?"
            args.append(before)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(int(limit))
        rows = self._query_all(sql, args, limit)
        return [{"ts": r[0], "route": r[1], "direction": r[2], "topic": r[3],
                 "raw_id": r[4], "body": r[5]} for r in rows]

    def counts(self):
        with self._lock:
            segs = list(self._segs)
        m = sum(s.messages for s in segs if s.connected)
        used = sum(s.file_bytes() for s in segs)
        out = {"messages": m, "events": 0}
        out.update(self.sizes())
        out["total_mb"] = round(used / 1048576, 2)
        out["segments"] = len(segs)
        out["connected"] = sum(1 for s in segs if s.connected)
        return out

    def sizes(self):
        with self._lock:
            segs = list(self._segs)
            cur = self._cur
        total = sum(s.file_bytes() for s in segs)
        cur_b = cur.file_bytes() if cur else 0
        return {
            "file_mb": round(cur_b / 1048576, 2),      # 当前段
            "used_mb": round(total / 1048576, 2),       # 全部段合计
            "disk_mb": round(total / 1048576, 2),
            "max_mb": self.max_mb,
            "keep_days": self.segment_days,             # 兼容旧界面字段
            "segment_days": self.segment_days,
            "segments": len(segs),
        }

    def storage_info(self):
        return {
            "settings": self.settings(),
            "segments": self.segments(),
            "counts": self.counts(),
        }

    def export_csv(self, route=None, direction=None, keyword=None, since=None, until=None):
        rows = self.query_messages(route, direction, keyword, since, until, limit=100000)
        import io, csv
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["time", "route", "direction", "topic", "raw_id", "body"])
        for r in reversed(rows):
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])) +
                        (".%03d" % int((r["ts"] % 1) * 1000)),
                        r["route"], r["direction"], r["topic"], r["raw_id"], r["body"]])
        return buf.getvalue()

    def close(self):
        self._stop.set()
        time.sleep(FLUSH_EVERY + 0.2)
        try:
            self._flush()
        except Exception:
            pass
        with self._lock:
            for seg in self._segs:
                seg.close()


# ------------------------------------------------------------------ 动作记录

class ActionLog:
    """动作记录：配置的增删改 + 连接运行事件。

    单文件 data/actions.db，**不分段**——这类数据量小、要长期可查，
    分段只会让查询变麻烦。报文记录才分段。
    """

    def __init__(self, path=None):
        self.path = path or ACTIONS_DB
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._con = sqlite3.connect(self.path, check_same_thread=False)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=NORMAL")
        self._con.executescript(_ACTION_SCHEMA)
        self._con.commit()
        threading.Thread(target=self._worker, daemon=True).start()

    # ---------------------------------------------------------------- writes
    def log(self, kind="系统", op="事件", target="", detail="", level="info",
            route="", text=""):
        """记一条动作。text 留空时按 kind/op/target/detail 自动拼一句人话。"""
        if not text:
            parts = [kind, op, target, detail]
            text = " ".join(p for p in parts if p)
        self._q.put((time.time(), level, kind, op, target, detail, route, text))

    def _worker(self):
        while not self._stop.is_set():
            time.sleep(FLUSH_EVERY)
            self._flush()

    def _flush(self):
        rows = []
        try:
            while True:
                rows.append(self._q.get_nowait())
                if len(rows) >= FLUSH_MAX:
                    break
        except queue.Empty:
            pass
        if not rows:
            return
        try:
            with self._lock:
                self._con.executemany(
                    "INSERT INTO actions(ts,level,kind,op,target,detail,route,text) "
                    "VALUES (?,?,?,?,?,?,?,?)", rows)
                self._con.commit()
        except Exception:
            pass

    # ---------------------------------------------------------------- queries
    def query(self, level=None, kind=None, op=None, keyword=None,
              since=None, until=None, before=None, limit=200):
        sql = ("SELECT ts,level,kind,op,target,detail,route,text "
               "FROM actions WHERE 1=1")
        args = []
        for col, val in (("level", level), ("kind", kind), ("op", op)):
            if val:
                sql += " AND %s = ?" % col
                args.append(val)
        if keyword:
            sql += " AND (text LIKE ? OR target LIKE ? OR detail LIKE ?)"
            args.extend(["%" + keyword + "%"] * 3)
        if since:
            sql += " AND ts >= ?"
            args.append(since)
        if until:
            sql += " AND ts <= ?"
            args.append(until)
        if before:
            sql += " AND ts < ?"
            args.append(before)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            rows = self._con.execute(sql, args).fetchall()
        return [{"ts": r[0], "level": r[1], "kind": r[2], "op": r[3],
                 "target": r[4], "detail": r[5], "route": r[6], "text": r[7]}
                for r in rows]

    def counts(self):
        with self._lock:
            n = self._con.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
            by_kind = dict(self._con.execute(
                "SELECT kind, COUNT(*) FROM actions GROUP BY kind").fetchall())
        size = 0
        for s in ("", "-wal", "-shm"):
            try:
                if os.path.exists(self.path + s):
                    size += os.path.getsize(self.path + s)
            except OSError:
                pass
        return {"actions": n, "by_kind": by_kind,
                "size_mb": round(size / 1048576, 2), "path": self.path}

    def kinds(self):
        return list(KINDS)

    def clear(self):
        """动作记录也允许手动清，但默认不自动删。"""
        with self._lock:
            try:
                self._con.execute("DELETE FROM actions")
                self._con.commit()
            except Exception as e:
                return False, "清除失败：%s" % e
        return True, "已清空动作记录"

    # ---------------------------------------------------------------- 迁移
    def migrate_from_segments(self, msg_dir):
        """把早先写进报文段里的 events 表搬进动作库（只搬一次）。

        以前动作和报文混在一个库里，现在分开了，旧的事件别丢。
        """
        flag = "migrated_events_v1"
        # 顺带归一旧类别名（页面改名后库里留着旧值），幂等
        with self._lock:
            try:
                self._con.execute(
                    "UPDATE actions SET kind='规则配置' WHERE kind='规则库'")
                self._con.commit()
            except Exception:
                pass
        with self._lock:
            try:
                done = self._con.execute(
                    "SELECT 1 FROM actions WHERE detail=? LIMIT 1", (flag,)).fetchone()
            except Exception:
                done = None
            if done:
                return 0
        moved = 0
        try:
            names = [n for n in os.listdir(msg_dir) if n.endswith(".db")]
        except OSError:
            names = []
        for n in sorted(names):
            p = os.path.join(msg_dir, n)
            try:
                con = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
                tabs = [r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")]
                if "events" not in tabs:
                    con.close()
                    continue
                rows = con.execute(
                    "SELECT ts,level,route,text FROM events").fetchall()
                con.close()
            except Exception:
                continue
            if not rows:
                continue
            batch = [(r[0], r[1] or "info", "连接", "事件", n, "", r[2] or "", r[3] or "")
                     for r in rows]
            with self._lock:
                try:
                    self._con.executemany(
                        "INSERT INTO actions(ts,level,kind,op,target,detail,route,text) "
                        "VALUES (?,?,?,?,?,?,?,?)", batch)
                    self._con.commit()
                    moved += len(batch)
                except Exception:
                    pass
        self.log(kind="系统", op="迁移", target="动作记录",
                 detail=flag, text="从报文段迁移了 %d 条历史事件" % moved)
        self._flush()
        return moved

    def close(self):
        self._stop.set()
        time.sleep(FLUSH_EVERY + 0.2)
        try:
            self._flush()
            self._con.close()
        except Exception:
            pass


STORE = None
ACTIONS = None
# RLock：get_actions() 内部要调 get_store()，普通 Lock 会自锁死掉
STORE_LOCK = threading.RLock()


def get_store():
    global STORE
    with STORE_LOCK:
        if STORE is None:
            STORE = Store()
        return STORE


def get_actions():
    global ACTIONS
    with STORE_LOCK:
        if ACTIONS is not None:
            return ACTIONS
        ACTIONS = ActionLog()
    # 迁移放在锁外做：要挨个打开段文件，别把 get_store 一起堵住
    try:
        ACTIONS.migrate_from_segments(get_store().data_dir)
    except Exception:
        pass
    return ACTIONS
