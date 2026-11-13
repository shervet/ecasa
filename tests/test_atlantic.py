"""Suivi d'execution Atlantic : fenetre de confirmation et faux negatifs.

La passerelle cloud Atlantic accepte un ordre (HTTP 201) puis l'execute avec
une latence de plusieurs dizaines de secondes. Une fenetre de suivi trop
courte produisait une erreur « Execution non terminee » alors que la consigne
etait bien appliquee (incident du 2026-11-12 a 20:11:23).
"""

import json
import unittest
from unittest import mock

from ecasa.atlantic import AtlanticClient, AtlanticError, ExecutionTimeout


def client_with_states(states, status=200):
    client = AtlanticClient("a@b.c", "secret")
    calls = []

    def fake_request(method, path, payload=None):
        calls.append(path)
        state = states.pop(0) if states else 2
        return status, json.dumps({"state": state}).encode()

    client._request = fake_request
    return client, calls


class AwaitExecutionTests(unittest.TestCase):
    def test_done_is_immediate(self):
        client, calls = client_with_states([3])
        self.assertIsNone(client._await_execution(7, attempts=5, delay=0))
        self.assertEqual(len(calls), 1)

    def test_pending_then_done(self):
        client, calls = client_with_states([1, 2, 3])
        self.assertIsNone(client._await_execution(7, attempts=5, delay=0))
        self.assertEqual(len(calls), 3)

    def test_timeout_raises_execution_timeout(self):
        client, calls = client_with_states([2] * 10)
        with self.assertRaises(ExecutionTimeout) as ctx:
            client._await_execution(42, attempts=4, delay=0)
        self.assertEqual(ctx.exception.execution_id, 42)
        self.assertEqual(len(calls), 4)

    def test_error_state_still_raises_plain_error(self):
        client, _calls = client_with_states([4])
        with self.assertRaises(AtlanticError) as ctx:
            client._await_execution(9, attempts=3, delay=0)
        self.assertNotIsInstance(ctx.exception, ExecutionTimeout)

    def test_default_window_covers_gateway_latency(self):
        # 10 a 40 s de latence cote Atlantic : la fenetre par defaut doit
        # laisser au moins 30 s avant de declarer l'ordre non confirme.
        client, _calls = client_with_states([])
        attempts, delay = client._await_execution.__defaults__
        self.assertGreaterEqual(attempts * delay, 30)

    def test_write_capability_surfaces_timeout(self):
        client = AtlanticClient("a@b.c", "secret")

        def fake_request(method, path, payload=None):
            if method == "POST":
                return 201, b"99"
            return 200, json.dumps({"state": 2}).encode()

        client._request = fake_request
        # On neutralise l'attente : seul le comportement compte ici.
        with mock.patch("ecasa.atlantic.time.sleep"):
            with self.assertRaises(ExecutionTimeout):
                client.write_capability(1, 40, "19")


if __name__ == "__main__":
    unittest.main()
