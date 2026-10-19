"""Local PCam discovery and explicit phone-approved pairing."""
import json
import secrets
import socket
import struct
import subprocess
import time
from .run import read_utf


def parse_reply(data, address, nonce):
    try:
        info = json.loads(data)
        if info.get('protocol') != 'PCAM/1' or info.get('nonce') != nonce: return None
        port = info.get('control_port')
        if not isinstance(port, int) or not 1 <= port <= 65535: return None
        if not isinstance(info.get('id'), str) or not isinstance(info.get('name'), str): return None
        return dict(id=info['id'][:80], name=info['name'][:80], phone=address[0],
                    control_port=port, busy=bool(info.get('busy')))
    except (ValueError, TypeError, AttributeError): return None


def scan(seconds=2.5, targets=None):
    if targets is None:
        interfaces = json.loads(subprocess.check_output(['ip', '-j', '-4', 'addr', 'show', 'scope', 'global'], text=True))
        targets = {entry['broadcast'] for interface in interfaces
                   if not interface['ifname'].startswith(('docker', 'br-', 'veth'))
                   for entry in interface.get('addr_info', []) if entry.get('broadcast')}
        targets.add('255.255.255.255')
    nonce = secrets.token_hex(12)
    found = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(('', 0)); sock.settimeout(.2)
        deadline, sent = time.monotonic() + seconds, 0
        while time.monotonic() < deadline:
            if time.monotonic() - sent > .65:
                for target in targets:
                    try: sock.sendto(('PCAM_DISCOVER/1 ' + nonce).encode(), (target, 5005))
                    except OSError: pass
                sent = time.monotonic()
            try: data, address = sock.recvfrom(2048)
            except socket.timeout: continue
            result = parse_reply(data, address, nonce)
            if result: found[result['id']] = result
    return sorted(found.values(), key=lambda item: item['name'])


def pair(phone, port=27185):
    with socket.create_connection((phone, port), timeout=5) as control:
        control.settimeout(35)
        request = b'PCAM/PAIR'
        control.sendall(struct.pack('>H', len(request)) + request)
        try:
            if read_utf(control) != 'PCAM/PAIR/1': raise RuntimeError('Phone pairing was not approved')
            token = read_utf(control)
        except EOFError: raise RuntimeError('Connection cancelled on the phone. Tap Connect when you try again.') from None
        if not token or len(token) > 128: raise RuntimeError('Invalid phone pairing response')
        return dict(phone=phone, token=token, control_port=port)
