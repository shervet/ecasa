"""Tests de l'annonce mDNS/DNS-SD eCasa (stdlib uniquement)."""

from __future__ import annotations

import socket
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecasa import mdns  # noqa: E402


class FakeSocket:
    """Socket UDP factice pour tester l'annonceur sans reseau."""

    def __init__(self, recv_packets=None, fail_send=False, fail_bind=False):
        self.recv_packets = list(recv_packets or [])
        self.sent = []
        self.fail_send = fail_send
        self.fail_bind = fail_bind
        self.closed = False
        self.timeout = None
        self.options = []

    def setsockopt(self, *args):
        self.options.append(args)

    def bind(self, addr):
        if self.fail_bind:
            raise OSError("bind failed")
        self.bound = addr

    def settimeout(self, value):
        self.timeout = value

    def sendto(self, payload, addr):
        if self.fail_send:
            raise OSError("send failed")
        self.sent.append((payload, addr))

    def recvfrom(self, size):  # noqa: ARG002
        if self.recv_packets:
            return self.recv_packets.pop(0)
        raise socket.timeout()

    def close(self):
        self.closed = True


def decode_name(data: bytes, offset: int) -> tuple[str, int]:
    labels = []
    jumped = False
    next_offset = offset
    seen = set()
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
            pointer = struct.unpack("!H", data[offset : offset + 2])[0] & 0x3FFF
            if not jumped:
                next_offset = offset + 2
            offset = pointer
            jumped = True
            continue
        offset += 1
        labels.append(data[offset : offset + length].decode("utf-8", errors="replace"))
        offset += length
        if not jumped:
            next_offset = offset
    name = ".".join(labels) + "." if labels else "."
    return name, next_offset


def parse_dns_packet(data: bytes) -> dict:
    header = struct.unpack("!HHHHHH", data[:12])
    result = {
        "id": header[0],
        "flags": header[1],
        "qdcount": header[2],
        "ancount": header[3],
        "nscount": header[4],
        "arcount": header[5],
        "questions": [],
        "answers": [],
    }
    offset = 12
    for _ in range(result["qdcount"]):
        name, offset = decode_name(data, offset)
        qtype, qclass = struct.unpack("!HH", data[offset : offset + 4])
        offset += 4
        result["questions"].append({"name": name, "type": qtype, "class": qclass})
    total_rr = result["ancount"] + result["nscount"] + result["arcount"]
    for i in range(total_rr):
        name, offset = decode_name(data, offset)
        rtype, rclass, ttl, rdlen = struct.unpack("!HHIH", data[offset : offset + 10])
        offset += 10
        rdata = data[offset : offset + rdlen]
        offset += rdlen
        entry = {
            "name": name,
            "type": rtype,
            "class": rclass & 0x7FFF,
            "cache_flush": bool(rclass & 0x8000),
            "ttl": ttl,
            "rdata": rdata,
        }
        if rtype == mdns.TYPE_PTR:
            entry["ptr"], _ = decode_name(data, offset - rdlen)
        elif rtype == mdns.TYPE_SRV:
            priority, weight, port, = struct.unpack("!HHH", rdata[:6])
            target, _ = decode_name(rdata, 6)
            entry["srv"] = {"priority": priority, "weight": weight, "port": port, "target": target}
        elif rtype == mdns.TYPE_TXT:
            txts = []
            p = 0
            while p < len(rdata):
                ln = rdata[p]
                p += 1
                txts.append(rdata[p : p + ln].decode("utf-8", errors="replace"))
                p += ln
            entry["txt"] = txts
        elif rtype == mdns.TYPE_A:
            entry["a"] = socket.inet_ntoa(rdata)
        section = "answers" if i < result["ancount"] else "additional"
        entry["section"] = section
        result["answers"].append(entry)
    return result


class MdnsBuildTests(unittest.TestCase):
    def test_build_announcement_contains_expected_records(self):
        packet = mdns.build_announcement(
            ip="192.168.1.42",
            port=8780,
            hostname="raspberrypi",
            instance_name="eCasa",
        )
        parsed = parse_dns_packet(packet)
        self.assertEqual(parsed["flags"] & 0x8000, 0x8000)  # QR
        self.assertEqual(parsed["flags"] & 0x0400, 0x0400)  # AA
        self.assertEqual(parsed["qdcount"], 0)
        self.assertEqual(parsed["ancount"], 1)
        self.assertEqual(parsed["arcount"], 3)

        ptr = [r for r in parsed["answers"] if r["type"] == mdns.TYPE_PTR and r["section"] == "answers"]
        self.assertEqual(len(ptr), 1)
        self.assertEqual(ptr[0]["name"].lower(), "_ecasa._tcp.local.")
        self.assertEqual(ptr[0]["ptr"].lower(), "ecasa._ecasa._tcp.local.")

        srv = [r for r in parsed["answers"] if r["type"] == mdns.TYPE_SRV]
        self.assertEqual(len(srv), 1)
        self.assertEqual(srv[0]["name"].lower(), "ecasa._ecasa._tcp.local.")
        self.assertEqual(srv[0]["srv"]["port"], 8780)
        self.assertEqual(srv[0]["srv"]["target"].lower(), "raspberrypi.local.")

        txt = [r for r in parsed["answers"] if r["type"] == mdns.TYPE_TXT]
        self.assertEqual(len(txt), 1)
        self.assertIn("ver=1", txt[0]["txt"])

        a = [r for r in parsed["answers"] if r["type"] == mdns.TYPE_A]
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["name"].lower(), "raspberrypi.local.")
        self.assertEqual(a[0]["a"], "192.168.1.42")

    def test_build_announcement_uses_real_port(self):
        packet = mdns.build_announcement(ip="10.0.0.5", port=9999, hostname="pi")
        parsed = parse_dns_packet(packet)
        srv = [r for r in parsed["answers"] if r["type"] == mdns.TYPE_SRV][0]
        self.assertEqual(srv["srv"]["port"], 9999)

    def test_build_response_to_ptr_query(self):
        query = build_query("_ecasa._tcp.local.", mdns.TYPE_PTR)
        packet = mdns.build_response(
            query, ip="192.168.1.42", port=8780, hostname="raspberrypi", instance_name="eCasa"
        )
        self.assertIsNotNone(packet)
        parsed = parse_dns_packet(packet)
        types = {(r["type"], r["section"]) for r in parsed["answers"]}
        self.assertIn((mdns.TYPE_PTR, "answers"), types)
        self.assertTrue(any(r["type"] == mdns.TYPE_SRV for r in parsed["answers"]))
        self.assertTrue(any(r["type"] == mdns.TYPE_A for r in parsed["answers"]))
        a = [r for r in parsed["answers"] if r["type"] == mdns.TYPE_A][0]
        self.assertEqual(a["a"], "192.168.1.42")

    def test_build_response_ignores_unrelated_query(self):
        query = build_query("_other._tcp.local.", mdns.TYPE_PTR)
        packet = mdns.build_response(query, ip="192.168.1.42", port=8780, hostname="pi")
        self.assertIsNone(packet)

    def test_get_lan_ip_uses_socket_connect(self):
        class FakeProbe:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def connect(self, addr):
                self.addr = addr

            def getsockname(self):
                return ("192.168.1.99", 12345)

        with mock.patch("ecasa.mdns.socket.socket", return_value=FakeProbe()):
            self.assertEqual(mdns.get_lan_ip(), "192.168.1.99")

    def test_get_lan_ip_fallback_on_failure(self):
        with mock.patch("ecasa.mdns.socket.socket", side_effect=OSError("boom")):
            with mock.patch("ecasa.mdns.socket.getaddrinfo", side_effect=OSError("boom")):
                self.assertEqual(mdns.get_lan_ip(), "127.0.0.1")

    def test_multicast_failure_does_not_raise(self):
        def bad_factory():
            raise OSError("no network")

        announcer = mdns.MdnsAnnouncer(port=8780, sock_factory=bad_factory, announce_interval=0.01)
        # must not raise
        announcer.start()
        announcer._thread.join(timeout=1)
        announcer.stop()

    def test_start_mdns_failure_does_not_raise(self):
        with mock.patch("ecasa.mdns.MdnsAnnouncer.start", side_effect=RuntimeError("boom")):
            result = mdns.start_mdns(8780)
        self.assertIsNone(result)


def build_query(name: str, qtype: int) -> bytes:
    header = struct.pack("!HHHHHH", 0, 0x0000, 1, 0, 0, 0)
    qname = mdns.encode_name(name)
    question = qname + struct.pack("!HH", qtype, 1)
    return header + question


class MdnsRuntimeTests(unittest.TestCase):
    def test_announcer_sends_announcement_and_responds(self):
        query = build_query("_ecasa._tcp.local.", mdns.TYPE_PTR)
        fake = FakeSocket(recv_packets=[(query, ("192.168.1.50", 5353))])

        announcer = mdns.MdnsAnnouncer(
            port=8780,
            hostname="pi",
            instance_name="eCasa",
            announce_interval=60,
            get_ip=lambda: "192.168.1.42",
            sock_factory=lambda: fake,
        )
        announcer.start()
        # wait for at least startup announcements + response
        for _ in range(50):
            if len(fake.sent) >= 4:
                break
            import time
            time.sleep(0.02)
        announcer.stop()
        if announcer._thread:
            announcer._thread.join(timeout=1)

        self.assertGreaterEqual(len(fake.sent), 1)
        # first packets are announcements
        parsed0 = parse_dns_packet(fake.sent[0][0])
        self.assertTrue(any(r["type"] == mdns.TYPE_PTR for r in parsed0["answers"]))
        # ensure response was sent
        all_types = []
        for payload, addr in fake.sent:
            self.assertEqual(addr, (mdns.MDNS_ADDR, mdns.MDNS_PORT))
            parsed = parse_dns_packet(payload)
            all_types.extend(r["type"] for r in parsed["answers"])
        self.assertIn(mdns.TYPE_PTR, all_types)
        self.assertIn(mdns.TYPE_A, all_types)


class MdnsMainIntegrationTests(unittest.TestCase):
    def _run_main(self, config):
        from ecasa import main

        calls = []
        fake_announcer = mock.Mock()
        fake_announcer.stop = mock.Mock()

        class FakeServer:
            def __init__(self, *args, **kwargs):
                self.app = None

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                pass

        store = mock.Mock()
        store.load_error = None
        store.devices.return_value = []
        store.close = mock.Mock()
        engine = mock.Mock()
        engine.poll = mock.Mock()
        engine.seed_demo = mock.Mock()
        engine.start = mock.Mock()
        engine.stop = mock.Mock()

        daily = mock.Mock()
        update_watch = mock.Mock()
        cpu_sampler = mock.Mock()

        with mock.patch.object(main, "load_config", return_value=config), \
                mock.patch.object(main, "DATA_DIR") as data_dir, \
                mock.patch.object(main, "_resolve_path", side_effect=lambda v, d: Path("/tmp") / d), \
                mock.patch.object(main, "MemoryStore", return_value=store), \
                mock.patch.object(main, "MockAtlanticClient", return_value=mock.Mock()), \
                mock.patch.object(main, "AtlanticClient", return_value=mock.Mock()), \
                mock.patch.object(main, "MockWiserClient", return_value=mock.Mock()), \
                mock.patch.object(main, "WiserClient", return_value=mock.Mock()), \
                mock.patch.object(main, "WeatherService", return_value=mock.Mock()), \
                mock.patch.object(main, "EnergyService", return_value=mock.Mock()), \
                mock.patch.object(main, "Engine", return_value=engine), \
                mock.patch.object(main, "Server", FakeServer), \
                mock.patch.object(main, "DailySync", return_value=daily), \
                mock.patch.object(main, "UpdateWatcher", return_value=update_watch), \
                mock.patch.object(main, "CpuSampler", return_value=cpu_sampler), \
                mock.patch.object(main, "apply_tailscale_startup", return_value={}), \
                mock.patch.object(main, "network_urls", return_value=[]), \
                mock.patch.object(main, "start_mdns", side_effect=lambda *a, **k: calls.append((a, k)) or fake_announcer), \
                mock.patch.object(main.sys, "dont_write_bytecode", True, create=True):
            data_dir.mkdir = mock.Mock()
            main.main()
        return calls

    def test_no_announce_when_config_mock_true(self):
        calls = self._run_main({"mock": True, "port": 8780, "host": "127.0.0.1", "email": "a@b", "password": "x"})
        self.assertEqual(calls, [])

    def test_announce_when_config_mock_false(self):
        calls = self._run_main({"mock": False, "port": 8781, "host": "127.0.0.1", "email": "a@b", "password": "x"})
        self.assertEqual(calls, [((8781,), {})])


if __name__ == "__main__":
    unittest.main()
