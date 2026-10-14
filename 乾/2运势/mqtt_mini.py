"""Minimal MQTT 3.1.1 client (QoS 0) — zero dependencies.

Only what the KIO/EMQX integration test needs: connect with username/password,
publish, subscribe, and receive. Not a general-purpose library.
"""
import socket
import struct
import threading
import time


def _enc_len(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        if n > 0:
            b |= 0x80
        out.append(b)
        if n == 0:
            return bytes(out)


def _enc_str(s: str) -> bytes:
    raw = s.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


class MiniMQTT:
    def __init__(self, host="127.0.0.1", port=1883, client_id="", username=None, password=None, keepalive=30):
        self.host, self.port = host, port
        self.client_id = client_id
        self.username, self.password = username, password
        self.keepalive = keepalive
        self.sock = None
        self._buf = b""
        self._lock = threading.Lock()
        self.on_message = None  # (topic:str, payload:bytes) -> None
        self._running = False

    # --- framing -------------------------------------------------------
    def _send(self, packet: bytes) -> None:
        self.sock.sendall(packet)

    def _read_exact(self, n: int) -> bytes:
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
        body = self._read_exact(rem) if rem else b""
        return first, body

    # --- lifecycle -----------------------------------------------------
    def connect(self, timeout=10.0) -> int:
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.sock.settimeout(None)

        flags = 0x02  # clean session
        payload = _enc_str(self.client_id)
        if self.username is not None:
            # MQTT 3.1.1: password flag MUST be set whenever a password is present,
            # and a password MUST be present whenever the password flag is set.
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

        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._keepalive, daemon=True).start()
        return body[1]

    def _keepalive(self):
        while self._running:
            time.sleep(max(1, self.keepalive // 2))
            try:
                with self._lock:
                    if self._running:
                        self._send(b"\xc0\x00")
            except OSError:
                return

    def _loop(self):
        try:
            while self._running:
                ptype, body = self._read_packet()
                if ptype >> 4 == 3:  # PUBLISH
                    tlen = struct.unpack(">H", body[:2])[0]
                    topic = body[2:2 + tlen].decode("utf-8", "replace")
                    qos = (ptype >> 1) & 0x03
                    idx = 2 + tlen
                    if qos > 0:  # skip packet id
                        idx += 2
                    payload = body[idx:]
                    if self.on_message:
                        self.on_message(topic, payload)
                elif ptype >> 4 == 13:  # PINGRESP
                    pass
        except (OSError, ConnectionError, struct.error):
            pass
        finally:
            self._running = False

    # --- operations ----------------------------------------------------
    def publish(self, topic: str, payload, qos=0, retain=False) -> None:
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        flags = (3 << 4) | ((1 if retain else 0) | (qos << 1))
        body = _enc_str(topic) + payload
        with self._lock:
            self._send(bytes([flags]) + _enc_len(len(body)) + body)

    def subscribe(self, topic: str, qos=0) -> None:
        body = struct.pack(">H", 1) + _enc_str(topic) + bytes([qos])
        with self._lock:
            self._send(bytes([0x82]) + _enc_len(len(body)) + body)

    def unsubscribe(self, topic: str) -> None:
        body = struct.pack(">H", 1) + _enc_str(topic)
        with self._lock:
            self._send(bytes([0xA2]) + _enc_len(len(body)) + body)

    def disconnect(self) -> None:
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
