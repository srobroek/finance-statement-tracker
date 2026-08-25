from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


PROBE_PATH = Path(__file__).parents[1] / "apps" / "cashback-control" / "probe_health.py"
PROBE_SPEC = importlib.util.spec_from_file_location("cashback_probe", PROBE_PATH)
if PROBE_SPEC is None or PROBE_SPEC.loader is None:
    raise RuntimeError("cannot load cashback health probe")
probe = importlib.util.module_from_spec(PROBE_SPEC)
PROBE_SPEC.loader.exec_module(probe)


class _Response:
    status = 200

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    @staticmethod
    def read() -> bytes:
        return b'{"status":"ok"}'


class CashbackProbeTests(unittest.TestCase):
    @patch.dict(os.environ, {"CASHBACK_INGEST_TOKEN": "probe-token"}, clear=True)
    @patch.object(probe.urllib.request, "urlopen", return_value=_Response())
    def test_probe_injects_token_in_container_request_and_returns_no_body(
        self,
        urlopen: object,
    ) -> None:
        self.assertTrue(probe.probe())
        request = urlopen.call_args.args[0]  # type: ignore[attr-defined]
        self.assertEqual(request.get_header("Authorization"), "Bearer probe-token")

    @patch.dict(os.environ, {}, clear=True)
    @patch.object(probe.urllib.request, "urlopen")
    def test_probe_fails_closed_without_container_token(self, urlopen: object) -> None:
        self.assertFalse(probe.probe())
        urlopen.assert_not_called()  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
