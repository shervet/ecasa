"""Annonce mDNS/DNS-SD du service eCasa sur le reseau local.

Implementation stdlib uniquement (UDP multicast 224.0.0.251:5353), sans
dependance externe. L'echec reseau ne doit jamais planter le serveur.
"""

from __future__ import annotations

import socket
import struct
import threading
import time

MDNS_ADDR = "224.0.0.251"
MDNS_PORT = 5353
SERVICE_TYPE = "_ecasa._tcp.local."
DEFAULT_INSTANCE = "eCasa"

TYPE_A = 1
TYPE_PTR = 12
TYPE_TXT = 16
TYPE_AAAA = 28
TYPE_SRV = 33
TYPE_ANY = 255
CLASS_IN = 1
CLASS_IN_FLUSH = 0x8001  # cache-flush bit set
TTL = 120


def encode_name(name: str) -> bytes:
    """Encode un nom DNS en labels (sans compression)."""
    name = str(name or "").strip(".")
    if not name:
        return b"\x00"
    out = bytearray()
    for label in name.split("."):
        raw = label.encode("utf-8")
        if len(raw) > 63:
            raw = raw[:63]
        out.append(len(raw))
        out.extend(raw)
    out.append(0)
    return bytes(out)


def decode_name(data: bytes, offset: int) -> tuple[str, int]:
    """Decode un nom DNS (compression supportee). Retourne (nom, offset_suivant)."""
    labels: list[str] = []
    jumped = False
    next_offset = offset
    seen: set[int] = set()
    while True:
        if offset >= len(data):
            break
        if offset in seen:
            break
        seen.add(offset)
        length = data[offset]
        if length == 0:
            offset += 1
            if not jumped:
                next_offset = offset
            break
        if (length & 0xC0) == 0xC0:
            if offset + 1 >= len(data):
                break
            pointer = struct.unpack("!H", data[offset : offset + 2])[0] & 0x3FFF
            if not jumped:
                next_offset = offset + 2
            offset = pointer
            jumped = True
            continue
        offset += 1
        end = offset + length
        label = data[offset:end].decode("utf-8", errors="replace")
        labels.append(label)
        offset = end
        if not jumped:
            next_offset = offset
    return ".".join(labels) + ("." if labels else ""), next_offset


def _header(qdcount: int, ancount: int, nscount: int = 0, arcount: int = 0) -> bytes:
    # id=0, flags QR=1 AA=1
    return struct.pack("!HHHHHH", 0, 0x8400, qdcount, ancount, nscount, arcount)


def _rdata_a(ip: str) -> bytes:
    try:
        return socket.inet_aton(ip)
    except OSError:
        return socket.inet_aton("0.0.0.0")


def _record(name: str, rtype: int, rdata: bytes, ttl: int = TTL, flush: bool = True) -> bytes:
    rclass = CLASS_IN_FLUSH if flush else CLASS_IN
    return encode_name(name) + struct.pack("!HHIH", rtype, rclass, ttl, len(rdata)) + rdata


def _ptr_rdata(instance_fqdn: str) -> bytes:
    return encode_name(instance_fqdn)


def _srv_rdata(port: int, target_fqdn: str, priority: int = 0, weight: int = 0) -> bytes:
    return struct.pack("!HHH", priority, weight, int(port)) + encode_name(target_fqdn)


def _txt_rdata(values: dict[str, str] | None = None) -> bytes:
    values = values or {"ver": "1"}
    out = bytearray()
    for key, value in values.items():
        item = f"{key}={value}".encode("utf-8")
        if len(item) > 255:
            item = item[:255]
        out.append(len(item))
        out.extend(item)
    return bytes(out) if out else b"\x00"


def service_names(hostname: str | None = None, instance_name: str = DEFAULT_INSTANCE) -> dict[str, str]:
    host = str(hostname or socket.gethostname() or "ecasa").strip().lower()
    host = host.split(".")[0] or "ecasa"
    # Keep DNS label safe
    safe = []
    for ch in host:
        if ch.isalnum() or ch == "-":
            safe.append(ch)
    host_label = "".join(safe).strip("-") or "ecasa"
    instance = str(instance_name or DEFAULT_INSTANCE).strip().strip(".") or DEFAULT_INSTANCE
    instance_fqdn = f"{instance}.{SERVICE_TYPE}"
    host_fqdn = f"{host_label}.local."
    return {
        "service": SERVICE_TYPE,
        "instance": instance_fqdn,
        "host": host_fqdn,
    }


def get_lan_ip() -> str:
    """Retourne l'IP LAN reelle, sans paquet reel emis."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return "127.0.0.1"


def build_announcement(
    ip: str,
    port: int,
    hostname: str | None = None,
    instance_name: str = DEFAULT_INSTANCE,
    ttl: int = TTL,
) -> bytes:
    """Construit une annonce mDNS non sollicitee (reponse DNS)."""
    names = service_names(hostname, instance_name)
    ptr = _record(names["service"], TYPE_PTR, _ptr_rdata(names["instance"]), ttl=ttl)
    srv = _record(names["instance"], TYPE_SRV, _srv_rdata(port, names["host"]), ttl=ttl)
    txt = _record(names["instance"], TYPE_TXT, _txt_rdata({"ver": "1"}), ttl=ttl)
    a = _record(names["host"], TYPE_A, _rdata_a(ip), ttl=ttl)
    # PTR en reponse, SRV/TXT/A en additionnels
    return _header(qdcount=0, ancount=1, nscount=0, arcount=3) + ptr + srv + txt + a


def parse_questions(data: bytes) -> list[tuple[str, int, int]]:
    """Parse minimalement la section Question d'un paquet DNS."""
    if len(data) < 12:
        return []
    try:
        _, flags, qdcount, _, _, _ = struct.unpack("!HHHHHH", data[:12])
    except struct.error:
        return []
    # Only queries
    if flags & 0x8000:
        return []
    offset = 12
    questions: list[tuple[str, int, int]] = []
    for _ in range(qdcount):
        try:
            name, offset = decode_name(data, offset)
            if offset + 4 > len(data):
                break
            qtype, qclass = struct.unpack("!HH", data[offset : offset + 4])
            offset += 4
            questions.append((name.lower(), qtype, qclass & 0x7FFF))
        except Exception:  # noqa: BLE001
            break
    return questions


def build_response(
    query: bytes,
    ip: str,
    port: int,
    hostname: str | None = None,
    instance_name: str = DEFAULT_INSTANCE,
    ttl: int = TTL,
) -> bytes | None:
    """Construit une reponse mDNS si la requete cible notre service."""
    questions = parse_questions(query)
    if not questions:
        return None
    names = service_names(hostname, instance_name)
    service = names["service"].lower()
    instance = names["instance"].lower()
    host = names["host"].lower()

    want_ptr = False
    want_srv = False
    want_txt = False
    want_a = False
    for qname, qtype, _qclass in questions:
        if qname == service and qtype in (TYPE_PTR, TYPE_ANY):
            want_ptr = True
        if qname == instance and qtype in (TYPE_SRV, TYPE_ANY):
            want_srv = True
            want_a = True
        if qname == instance and qtype in (TYPE_TXT, TYPE_ANY):
            want_txt = True
        if qname == host and qtype in (TYPE_A, TYPE_ANY):
            want_a = True

    if not (want_ptr or want_srv or want_txt or want_a):
        return None

    answers: list[bytes] = []
    additionals: list[bytes] = []
    if want_ptr:
        answers.append(_record(names["service"], TYPE_PTR, _ptr_rdata(names["instance"]), ttl=ttl))
        # Include SRV/TXT/A as additionals for PTR query
        additionals.append(_record(names["instance"], TYPE_SRV, _srv_rdata(port, names["host"]), ttl=ttl))
        additionals.append(_record(names["instance"], TYPE_TXT, _txt_rdata({"ver": "1"}), ttl=ttl))
        additionals.append(_record(names["host"], TYPE_A, _rdata_a(ip), ttl=ttl))
    if want_srv:
        rec = _record(names["instance"], TYPE_SRV, _srv_rdata(port, names["host"]), ttl=ttl)
        if rec not in answers:
            answers.append(rec)
        a_rec = _record(names["host"], TYPE_A, _rdata_a(ip), ttl=ttl)
        if a_rec not in answers and a_rec not in additionals:
            additionals.append(a_rec)
    if want_txt:
        rec = _record(names["instance"], TYPE_TXT, _txt_rdata({"ver": "1"}), ttl=ttl)
        if rec not in answers:
            answers.append(rec)
    if want_a and not want_ptr:
        rec = _record(names["host"], TYPE_A, _rdata_a(ip), ttl=ttl)
        if rec not in answers and rec not in additionals:
            # if asked directly for A, put in answers
            answers.append(rec)

    # Ensure we don't duplicate A in both
    if not answers and additionals:
        answers = additionals
        additionals = []

    return _header(qdcount=0, ancount=len(answers), nscount=0, arcount=len(additionals)) + b"".join(answers + additionals)


def _create_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except OSError:
        pass
    try:
        # Windows
        sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_REUSEPORT", socket.SO_REUSEADDR), 1)
    except OSError:
        pass
    try:
        sock.bind(("", MDNS_PORT))
    except OSError:
        # Fallback: bind to ephemeral port to still send announcements
        sock.bind(("", 0))
    try:
        group = socket.inet_aton(MDNS_ADDR)
        mreq = struct.pack("4sL", group, socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except OSError:
        pass
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", 255))
    except OSError:
        pass
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, struct.pack("b", 1))
    except OSError:
        pass
    sock.settimeout(1.0)
    return sock


class MdnsAnnouncer:
    """Annonceur/repondeur mDNS en thread daemon."""

    def __init__(
        self,
        port: int,
        hostname: str | None = None,
        instance_name: str = DEFAULT_INSTANCE,
        announce_interval: float = 120.0,
        get_ip=get_lan_ip,
        sock_factory=_create_socket,
    ):
        self.port = int(port)
        self.hostname = hostname
        self.instance_name = instance_name
        self.announce_interval = float(announce_interval)
        self.get_ip = get_ip
        self.sock_factory = sock_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.sock: socket.socket | None = None

    def start(self) -> "MdnsAnnouncer":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ecasa-mdns", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        sock = self.sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _send(self, payload: bytes) -> None:
        sock = self.sock
        if sock is None:
            return
        try:
            sock.sendto(payload, (MDNS_ADDR, MDNS_PORT))
        except OSError:
            pass

    def _announce(self) -> None:
        try:
            ip = self.get_ip()
            packet = build_announcement(
                ip=ip,
                port=self.port,
                hostname=self.hostname,
                instance_name=self.instance_name,
            )
            self._send(packet)
        except Exception:  # noqa: BLE001
            pass

    def _run(self) -> None:
        try:
            self.sock = self.sock_factory()
        except Exception as err:  # noqa: BLE001
            print(f"[mdns] demarrage impossible : {err}")
            return

        try:
            # Quelques annonces non sollicitées au démarrage
            for _ in range(3):
                if self._stop.is_set():
                    return
                self._announce()
                self._stop.wait(1.0)

            last_announce = time.monotonic()
            while not self._stop.is_set():
                now = time.monotonic()
                if now - last_announce >= self.announce_interval:
                    self._announce()
                    last_announce = now
                try:
                    data, _addr = self.sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    self._stop.wait(0.5)
                    continue
                except Exception:  # noqa: BLE001
                    continue
                try:
                    ip = self.get_ip()
                    response = build_response(
                        data,
                        ip=ip,
                        port=self.port,
                        hostname=self.hostname,
                        instance_name=self.instance_name,
                    )
                    if response:
                        self._send(response)
                except Exception:  # noqa: BLE001
                    continue
        finally:
            try:
                if self.sock is not None:
                    self.sock.close()
            except OSError:
                pass


def start_mdns(
    port: int,
    hostname: str | None = None,
    instance_name: str = DEFAULT_INSTANCE,
    announce_interval: float = 120.0,
) -> MdnsAnnouncer | None:
    """Demarre l'annonceur mDNS en thread daemon. Ne leve jamais."""
    try:
        announcer = MdnsAnnouncer(
            port=port,
            hostname=hostname,
            instance_name=instance_name,
            announce_interval=announce_interval,
        )
        announcer.start()
        print(f"[mdns] annonce _ecasa._tcp.local sur le port {int(port)}")
        return announcer
    except Exception as err:  # noqa: BLE001
        print(f"[mdns] annonce desactivee : {err}")
        return None
