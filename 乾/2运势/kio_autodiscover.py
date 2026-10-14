"""Auto-discover the source MQTT connection from KingIOServer's own configuration.

KingIOServer keeps the broker address / credentials / topics in two places:

  method 2  kxMQTT forward     <project>\\MQTTConfig.JSON
                                ConnectConfig{ServerURL,Port,ClientID,SubscribeTopic}
                                SecurityProperty{UserName,Password}

  method 1  MQTT_Request PUB   <install>\\BIN\\Driver\\MQTT_Request.ini
                                0_DomainName 0_UserName 0_PassWord 0_ClientID 0_0PUB

Reading them means the reshaper needs no hand-entered source settings -- they
stay in sync with whatever is configured in the IOServer GUI.

CLI:  python kio_autodiscover.py [ioserver_root]
"""
import json
import os
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

DEFAULT_ROOTS = [
    r"C:\Program Files (x86)\KingIOServer3.8SP1",
    r"C:\Program Files\KingIOServer3.8SP1",
    r"C:\KingIOServer",
]


def _find_root(hint=None):
    candidates = []
    if hint:
        candidates.append(hint)
    candidates += DEFAULT_ROOTS
    # also scan Program Files for anything that looks like KingIOServer
    for base in (r"C:\Program Files (x86)", r"C:\Program Files", r"C:\\"):
        try:
            for name in os.listdir(base):
                if "ioserver" in name.lower() or "king" in name.lower():
                    candidates.append(os.path.join(base, name))
        except OSError:
            pass
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, "BIN", "KingIOServer.ini")):
            return c
    return None


def _read_text(path):
    for enc in ("utf-8-sig", "utf-8", "gbk", "utf-16", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, OSError):
            continue
    return ""


def _parse_ini(path):
    """Keep the FIRST non-empty value per key (the file repeats keys with blanks)."""
    out = {}
    for line in _read_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if k and k not in out and v:
            out[k] = v
    return out


def _url_host_port(url, default_port=1883):
    """tcp://host:port | ssl://host:port | host:port | host"""
    if not url:
        return None, default_port
    u = re.sub(r"^[a-z0-9]+://", "", url.strip(), flags=re.I)
    u = u.split("/")[0]
    if ":" in u:
        host, _, p = u.rpartition(":")
        try:
            return host or None, int(p)
        except ValueError:
            return u, default_port
    return u or None, default_port


def discover(root=None):
    """Return {'root':.., 'project':.., 'methods': [...], 'missing': [...]}

    'missing' lists the config files that were expected but not found, each as
    {'dir': .., 'file': ..} so the UI can say exactly which path lacked which file.
    """
    root = _find_root(root)
    if not root:
        tried = [os.path.join(c, "BIN", "KingIOServer.ini") for c in DEFAULT_ROOTS]
        raise FileNotFoundError(
            "没有找到 KingIOServer 安装目录,需要: " + "; ".join(tried))

    bin_dir = os.path.join(root, "BIN")
    missing = []

    # locate the runtime project directory
    project = None
    kio_ini = _parse_ini(os.path.join(bin_dir, "KingIOServer.ini"))
    cand = kio_ini.get("DefaultProject", "").strip()
    if cand and os.path.isdir(cand):
        project = cand
    if not project:
        kx = _parse_ini(os.path.join(bin_dir, "kxConfig.ini"))
        prj = kx.get("DefaultProject", kx.get("solution0", "")).strip()
        if prj.lower().endswith(".kxprj"):
            prj = os.path.dirname(prj)
        if prj and os.path.isdir(prj):
            project = prj
    if not project:
        missing.append({"dir": root, "file": "My Projects\\<工程>\\<工程>\\MQTTConfig.JSON"})

    methods = []

    # ---- method 2: kxMQTT forward ----
    if project:
        mc_path = os.path.join(project, "MQTTConfig.JSON")
        if os.path.isfile(mc_path):
            try:
                mc = json.loads(_read_text(mc_path))
            except Exception:
                mc = {}
            conn = mc.get("ConnectConfig") or {}
            sec = mc.get("SecurityProperty") or {}
            url = conn.get("ServerURL", "")
            host, _p = _url_host_port(url, 1883)
            port = conn.get("Port") or _p
            topic = conn.get("SubscribeTopic") or ""
            if mc.get("MQTTEnable") is not True:
                note = "MQTTEnable=false"
            else:
                note = ""
            methods.append({
                "name": "kxmqtt-forward",
                "enabled": mc.get("MQTTEnable") is True,
                "host": host,
                "port": int(port) if port else 1883,
                "username": sec.get("UserName", ""),
                "password": sec.get("Password", ""),
                "client_id": conn.get("ClientID", ""),
                "topic": topic,
                "source_file": mc_path,
                "note": note,
            })
        else:
            missing.append({"dir": project, "file": "MQTTConfig.JSON"})

    # ---- method 1: MQTT_Request driver, PUB registers ----
    ini_path = os.path.join(bin_dir, "Driver", "MQTT_Request.ini")
    if os.path.isfile(ini_path):
        ini = _parse_ini(ini_path)
        host = ini.get("0_DomainName") or ini.get("0_DomainName".lower())
        port = 1883
        m = re.search(r":(\d{2,5})\s*$", host or "")
        if m:
            port = int(m.group(1))
            host = host[:m.start()]
        pubs = {k: v for k, v in ini.items() if re.fullmatch(r"\d+_\d+PUB", k) and v}
        subs = {k: v for k, v in ini.items() if re.fullmatch(r"\d+_\d+SUB", k) and v}
        methods.append({
            "name": "mqtt-request-pub",
            "enabled": bool(pubs),
            "host": host,
            "port": port,
            "username": ini.get("0_UserName", ""),
            "password": ini.get("0_PassWord", ""),
            "client_id": ini.get("0_ClientID", ""),
            "topic": pubs.get("0_0PUB", ""),
            "publish_topics": pubs,
            "subscribe_topics": subs,
            "source_file": ini_path,
            "note": "",
        })
    else:
        missing.append({"dir": os.path.join(bin_dir, "Driver"), "file": "MQTT_Request.ini"})

    return {"root": root, "project": project, "methods": methods, "missing": missing}


def pick_source(info, prefer="kxmqtt-forward"):
    """Choose one discovered method as the reshaper's source. Prefer the forward path."""
    methods = [m for m in info["methods"] if m.get("enabled") and m.get("host")]
    if not methods:
        raise ValueError("没有发现可用的源连接配置")
    for m in methods:
        if m["name"] == prefer:
            return m
    return methods[0]


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        info = discover(root)
    except Exception as e:
        print("自动发现失败:", e)
        sys.exit(1)
    print("KingIOServer 目录:", info["root"])
    print("工程目录        :", info["project"])
    for m in info["methods"]:
        print()
        print(f"  [{m['name']}]  {'可用' if m['enabled'] else '未启用'}  {m.get('note','')}")
        print(f"    来源文件 : {m['source_file']}")
        print(f"    broker   : {m['host']}:{m['port']}")
        print(f"    用户名   : {m['username']}")
        print(f"    密码     : {'*' * len(m['password']) if m['password'] else '(空)'}")
        print(f"    client_id: {m['client_id']}")
        print(f"    主题     : {m['topic']}")
        if m.get("publish_topics"):
            print(f"    发布主题 : {m['publish_topics']}")
        if m.get("subscribe_topics"):
            print(f"    订阅主题 : {m['subscribe_topics']}")
