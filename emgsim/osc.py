"""Minimal OSC 1.0 over UDP: encode messages and bundles, decode incoming messages.
No dependencies. Types: int32 'i', float32 'f', string 's'."""
import socket
import struct


def _pad(b):
    return b + b'\0' * (4 - len(b) % 4)


def _s(x):
    return _pad(x.encode('utf-8'))


def message(address, *args):
    tags = ','
    data = b''
    for a in args:
        if isinstance(a, bool):
            a = int(a)
        if isinstance(a, int):
            tags += 'i'
            data += struct.pack('>i', max(-2 ** 31, min(2 ** 31 - 1, a)))
        elif isinstance(a, float):
            tags += 'f'
            data += struct.pack('>f', a)
        else:
            tags += 's'
            data += _s(str(a))
    return _s(address) + _s(tags) + data


def bundle(messages):
    out = _s('#bundle') + struct.pack('>Q', 1)          # timetag 1 = immediately
    for m in messages:
        out += struct.pack('>i', len(m)) + m
    return out


def decode(packet):
    """Returns a list of (address, [args]). Bundles are flattened."""
    if packet.startswith(b'#bundle'):
        res, i = [], 16
        while i + 4 <= len(packet):
            n = struct.unpack('>i', packet[i:i + 4])[0]
            res += decode(packet[i + 4:i + 4 + n])
            i += 4 + n
        return res

    def rs(i):
        j = packet.index(b'\0', i)
        return packet[i:j].decode('utf-8', 'replace'), (j // 4 + 1) * 4

    addr, i = rs(0)
    if i >= len(packet):
        return [(addr, [])]
    tags, i = rs(i)
    args = []
    for t in tags[1:]:
        if t == 'i':
            args.append(struct.unpack('>i', packet[i:i + 4])[0]); i += 4
        elif t == 'f':
            args.append(struct.unpack('>f', packet[i:i + 4])[0]); i += 4
        elif t == 'd':
            args.append(struct.unpack('>d', packet[i:i + 8])[0]); i += 8
        elif t == 'h':
            args.append(struct.unpack('>q', packet[i:i + 8])[0]); i += 8
        elif t == 's':
            s, i = rs(i); args.append(s)
        elif t == 'T':
            args.append(1)
        elif t == 'F':
            args.append(0)
    return [(addr, args)]


class Sender:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sent = 0
        self.errors = 0

    def send(self, data, host, port):
        try:
            self.sock.sendto(data, (host, int(port)))
            self.sent += 1
        except OSError:
            self.errors += 1


class Receiver:
    def __init__(self, port, host='0.0.0.0'):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, int(port)))
        self.sock.setblocking(False)

    def poll(self):
        out = []
        while True:
            try:
                data, _ = self.sock.recvfrom(65536)
            except (BlockingIOError, OSError):
                break
            try:
                out += decode(data)
            except Exception:
                pass
        return out

    def close(self):
        self.sock.close()
