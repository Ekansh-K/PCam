"""Exercise discovery and approval over real local sockets."""
import json
import socket
import struct
import threading
import unittest
from pcam import discovery
from pcam.run import read_utf


class DiscoveryTests(unittest.TestCase):
    def test_replies_need_matching_probe_and_use_packet_address(self):
        reply = dict(protocol='PCAM/1', nonce='a' * 24, id='phone', name='Camera',
                     phone='untrusted', control_port=27185, busy=False)
        parsed = discovery.parse_reply(json.dumps(reply), ('127.0.0.1', 5005), 'a' * 24)
        self.assertEqual(parsed['phone'], '127.0.0.1')
        self.assertNotIn('token', parsed)
        self.assertIsNone(discovery.parse_reply(json.dumps(reply), ('127.0.0.1', 5005), 'b' * 24))
        for port in (0, 65536, '27185'):
            reply['control_port'] = port
            self.assertIsNone(discovery.parse_reply(json.dumps(reply), ('127.0.0.1', 5005), 'a' * 24))

    def test_scan_finds_phone_and_deduplicates_responses(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            server.bind(('127.0.0.1', 5005)); server.settimeout(2)
            def respond():
                request, peer = server.recvfrom(1024)
                response = json.dumps(dict(protocol='PCAM/1', nonce=request.decode()[16:],
                    id='fixture', name='Test phone', control_port=27185, busy=False)).encode()
                server.sendto(response, peer); server.sendto(response, peer)
            worker = threading.Thread(target=respond); worker.start()
            found = discovery.scan(seconds=.35, targets=['127.0.0.1']); worker.join()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['name'], 'Test phone')

    def test_pair_waits_for_explicit_phone_response(self):
        for approved in (True, False):
            with self.subTest(approved=approved), socket.socket() as server:
                server.bind(('127.0.0.1', 0)); server.listen(1)
                requests = []
                def respond():
                    with server.accept()[0] as peer:
                        requests.append(read_utf(peer))
                        if approved:
                            for text in ('PCAM/PAIR/1', 'private-test-token'):
                                encoded = text.encode(); peer.sendall(struct.pack('>H', len(encoded)) + encoded)
                worker = threading.Thread(target=respond); worker.start()
                if approved:
                    result = discovery.pair('127.0.0.1', server.getsockname()[1])
                    self.assertEqual(result['token'], 'private-test-token')
                else:
                    with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                        discovery.pair('127.0.0.1', server.getsockname()[1])
                worker.join(); self.assertEqual(requests, ['PCAM/PAIR'])


if __name__ == '__main__': unittest.main()
