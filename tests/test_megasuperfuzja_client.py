"""Offline tests for the MEGASUPERFUZJA read-only client."""
import importlib.util
import pathlib
import unittest
from unittest.mock import patch
from io import BytesIO

ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("megafuzja_client",ROOT/".fuzja"/"megasuperfuzja_client.py")
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TestMegaClient(unittest.TestCase):
    def test_fails_closed_without_secret(self):
        c=mod.MegaFuzjaClient(token="")
        with self.assertRaises(mod.MegaFuzjaUnavailable):
            c.overview()

    def test_rejects_arbitrary_urls_and_paths(self):
        with self.assertRaises(ValueError):
            mod.MegaFuzjaClient(token="X"*64,base_url="https://example.com")
        c=mod.MegaFuzjaClient(token="X"*64)
        with self.assertRaises(ValueError):
            c.get("/v1/write")
        with self.assertRaises(ValueError):
            c.catalog(category="SQL injection")

    def test_bearer_and_no_write(self):
        class Stub:
            def __enter__(self): return BytesIO(b'{"data":[]}')
            def __exit__(self,*a): return False
        c=mod.MegaFuzjaClient(token="Y"*64)
        with patch.object(mod,"urlopen",return_value=Stub()) as mocked:
            self.assertEqual(c.catalog(category="skill"),{"data":[]})
            req=mocked.call_args.args[0]
            self.assertEqual(req.get_method(),"GET")
            self.assertEqual(req.headers["Authorization"],"Bearer "+"Y"*64)
            self.assertIn("type=skill",req.full_url)


if __name__=="__main__":
    unittest.main()
