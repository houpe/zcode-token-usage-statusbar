import base64
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("zcas_python", ROOT / "zcas.py")
zcas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zcas)


def fake_jwt(user_id):
    payload = base64.urlsafe_b64encode(json.dumps({"user_id": user_id}).encode()).rstrip(b"=").decode()
    return "header." + payload + ".signature-is-long-enough-for-validation"


class ZcasPythonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.zcode = self.root / "zcode-v2"
        self.data = self.root / "zcas"
        self.zcode.mkdir()
        self.data.mkdir()
        self.env = {
            "ZCAS_HOME": str(self.root), "ZCAS_ZCODE_DIR": str(self.zcode),
            "ZCAS_DATA_DIR": str(self.data), "ZCODE_CREDENTIAL_SECRET": "test-secret",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def write_live(self, user_id="user-1001"):
        cred = {"oauth:active_provider": "zai",
                "oauth:zai:user_info": json.dumps({"id": user_id, "email": "user@example.com", "name": "User"}),
                "oauth:zai:access_token": fake_jwt(user_id), "zcodejwttoken": fake_jwt(user_id)}
        cfg = {"provider": {"builtin:zai-coding-plan": {
            "enabled": True, "models": {"glm": {}}, "options": {"apiKey": fake_jwt(user_id)}}}}
        zcas.atomic_write(self.zcode / "credentials.json", cred)
        zcas.atomic_write(self.zcode / "config.json", cfg)
        return cred, cfg

    def test_capture_state_rename_delete_and_no_credentials_escape(self):
        with mock.patch.dict("os.environ", self.env):
            self.write_live()
            captured = zcas.capture("Main", "", True)
            self.assertTrue(captured["created"])
            state = zcas.current_account_state()
            self.assertEqual(state["current"]["savedId"], captured["meta"]["id"])
            self.assertEqual(len(state["accounts"]), 1)
            encoded = json.dumps(state, ensure_ascii=False)
            self.assertNotIn("zcodejwttoken", encoded)
            self.assertNotIn("oauth:", encoded)
            meta, _ = zcas.load_account(captured["meta"]["id"])
            meta["label"] = "Renamed"
            self.assertEqual(zcas.account_list()[-1]["label"], "Main")

    def test_protocol_dispatch_is_json_lines(self):
        with mock.patch.dict("os.environ", self.env):
            self.write_live()
            output = io.StringIO()
            with redirect_stdout(output), mock.patch.object(sys_module := __import__("sys"), "stdin", io.StringIO(
                json.dumps({"id": "1", "action": "state"}) + "\n" +
                json.dumps({"id": "2", "action": "bogus"}) + "\n"
            )):
                zcas.serve()
            first, second = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertTrue(first["ok"])
            self.assertEqual(first["id"], "1")
            self.assertFalse(second["ok"])
            self.assertEqual(second["id"], "2")

    def test_switch_preserves_unrelated_config_and_restores_rollback_files(self):
        with mock.patch.dict("os.environ", self.env):
            self.write_live("user-old")
            zcas.capture("Old", "", True)
            self.write_live("user-new")
            new = zcas.capture("New", "", True)
            cfg = zcas.read_json(zcas.config_file())
            cfg["editor"] = {"fontSize": 14}
            zcas.atomic_write(zcas.config_file(), cfg)
            with mock.patch.object(zcas, "is_zcode_running", return_value=False), \
                 mock.patch.object(zcas, "kill_zcode"), mock.patch.object(zcas, "launch_zcode"):
                result = zcas.switch_account(new["meta"]["id"], restart=False)
            switched = zcas.read_json(zcas.config_file())
            self.assertEqual(switched["editor"]["fontSize"], 14)
            self.assertEqual(result["account"]["id"], new["meta"]["id"])

    def test_macos_restart_opens_zcode_in_foreground(self):
        calls = []
        results = [subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0)]
        with mock.patch.dict("os.environ", self.env), \
             mock.patch.object(zcas.subprocess, "run", side_effect=lambda args, **_: (
                 calls.append(args), results[len(calls) - 1]
             )[1]):
            zcas.launch_zcode()
        self.assertEqual(calls, [["open", "-b", "dev.zcode.app"], ["open", "-a", "ZCode"]])

    def test_quota_uses_coding_plan_limits(self):
        with mock.patch.dict("os.environ", self.env):
            _, snap = None, {"version": 2, "credentialFields": {
                "oauth:active_provider": "zai",
                "zcodejwttoken": zcas.encrypt(fake_jwt("quota-user"), "test-secret"),
            }, "providerSlots": {}}
            zcas.save_account({"id": "acct", "label": "A", "userId": "u"}, snap)
            response = {"code": 200, "data": {"level": "pro", "limits": [
                {"unit": 3, "number": 5, "usage": 100, "currentValue": 25, "remaining": 75,
                 "percentage": 25, "nextResetTime": 1}]}}
            with mock.patch.object(zcas, "http_json", return_value=response):
                quota = zcas.quota_for("acct")
            self.assertEqual(quota["codingPlan"]["limits"][0]["name"], "每5小时")
            self.assertEqual(quota["percentUsed"], 25)


if __name__ == "__main__":
    unittest.main()
