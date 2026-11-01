import base64
import importlib.util
import io
import json
import subprocess
import tempfile
import time
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

    def test_plugin_account_store_is_separate_from_standalone_zcas(self):
        self.assertEqual(zcas.data_dir(), (ROOT / "zcas").resolve())
        with mock.patch.dict("os.environ", {"ZCAS_DATA_DIR": str(self.data)}):
            self.assertEqual(zcas.data_dir(), self.data)

    def write_live(self, user_id="user-1001"):
        return self.write_live_email(user_id, "user@example.com")

    def write_live_email(self, user_id, email):
        cred = {"oauth:active_provider": "zai",
                "oauth:zai:user_info": json.dumps({"id": user_id, "email": email, "name": "User"}),
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

    def test_rename_updates_label_and_preserves_existing_note(self):
        with mock.patch.dict("os.environ", self.env):
            self.write_live()
            captured = zcas.capture("Main", "keep-note", True)
            request = json.dumps({
                "id": "rename-1", "action": "rename",
                "accountId": captured["meta"]["id"], "name": "Renamed",
            })
            output = io.StringIO()
            with redirect_stdout(output), mock.patch("sys.stdin", io.StringIO(request + "\n")):
                zcas.serve()
            response = json.loads(output.getvalue())
            self.assertTrue(response["ok"])
            self.assertEqual(response["result"]["meta"]["label"], "Renamed")
            self.assertEqual(response["result"]["meta"]["note"], "keep-note")
            self.assertEqual(zcas.account_list()[0]["label"], "Renamed")

    def test_delete_current_snapshot_keeps_live_login(self):
        with mock.patch.dict("os.environ", self.env):
            self.write_live()
            captured = zcas.capture("Main", "", True)
            request = json.dumps({
                "id": "delete-1", "action": "delete",
                "accountId": captured["meta"]["id"],
            })
            output = io.StringIO()
            with redirect_stdout(output), mock.patch("sys.stdin", io.StringIO(request + "\n")):
                zcas.serve()
            response = json.loads(output.getvalue())
            self.assertTrue(response["ok"])
            state = zcas.current_account_state()
            self.assertEqual(state["accounts"], [])
            self.assertEqual(state["current"]["savedId"], "")
            self.assertTrue(zcas.has_session(zcas.read_json(zcas.credentials_file())))

    def test_oauth_finish_can_wait_for_polling_without_callback(self):
        provider = zcas.PROVIDERS["zai"]
        flow = {"provider": provider, "pollToken": "token", "flowId": "flow",
                "authorizeUrl": "https://example/auth", "expiresAt": time.time() + 60,
                "interval": 0, "state": "state"}
        tokens = (fake_jwt("poll-user"), fake_jwt("business-user"), "refresh", {"user_id": "poll-user"})
        saved = {"meta": {"id": "poll-use"}}
        output = io.StringIO()
        with mock.patch.dict("os.environ", self.env), \
             mock.patch.object(zcas, "start_oauth_flow", return_value=flow) as start, \
             mock.patch.object(zcas, "open_url") as opened, \
             mock.patch.object(zcas, "wait_oauth_flow", return_value=tokens) as waited, \
             mock.patch.object(zcas, "save_oauth_token_set", return_value=saved) as save, \
             redirect_stdout(output), mock.patch("sys.stdin", io.StringIO(
                 json.dumps({"id": "start", "action": "oauth-start", "provider": "zai"}) + "\n" +
                 json.dumps({"id": "finish", "action": "oauth-finish", "input": ""}) + "\n"
             )):
            zcas.serve()
        start_response, finish_response = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(start_response["ok"])
        self.assertTrue(opened.called)
        self.assertTrue(waited.called)
        self.assertTrue(finish_response["ok"])
        self.assertEqual(finish_response["result"], saved)
        self.assertEqual(start.call_args.args[0], provider)
        self.assertEqual(save.call_args.args[0]["provider"], provider)

    def test_save_oauth_token_set_converts_zai_platform_token(self):
        with mock.patch.dict("os.environ", self.env), \
             mock.patch.object(zcas, "resolve_zai_business_token", return_value=fake_jwt("oauth-user")) as resolve:
            provider = zcas.PROVIDERS["zai"]
            result = zcas.save_oauth_token_set(
                {"provider": provider, "accountId": "", "name": "Poll", "note": ""},
                fake_jwt("zcode-user"), fake_jwt("platform-user"), "refresh",
                {"user_id": "oauth-user", "email": "poll@example.com", "name": "Poll"},
            )
        self.assertTrue(result["created"])
        self.assertEqual(resolve.call_count, 1)
        with mock.patch.dict("os.environ", self.env):
            _, snap = zcas.load_account(result["meta"]["id"])
            profile = json.loads(zcas.decrypt(snap["credentialFields"]["oauth:zai:user_info"], "test-secret"))
            self.assertEqual(profile["email"], "poll@example.com")

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

    def test_switch_backs_up_account_when_email_identity_changes(self):
        with mock.patch.dict("os.environ", self.env):
            self.write_live_email("user-old", "old@example.com")
            old = zcas.capture("Old", "", True)
            self.write_live("user-new")
            new = zcas.capture("New", "", True)
            self.write_live_email("user-old", "")
            self.assertNotEqual(zcas.fingerprint(
                zcas.read_json(zcas.credentials_file()),
                zcas.read_json(zcas.config_file()), "test-secret"
            )["emailShortId"], old["meta"]["id"])
            with mock.patch.object(zcas, "is_zcode_running", return_value=False), \
                 mock.patch.object(zcas, "kill_zcode"), mock.patch.object(zcas, "launch_zcode"):
                zcas.switch_account(new["meta"]["id"], restart=False)

            state = zcas.current_account_state()
            self.assertEqual(len(state["accounts"]), 2)
            self.assertIn(old["meta"]["id"], [item["id"] for item in state["accounts"]])
            meta, _ = zcas.load_account(old["meta"]["id"])
            self.assertEqual(meta["userId"], "user-old")
            _, old_snap = zcas.load_account(old["meta"]["id"])
            self.assertEqual(old_snap["credentialFields"]["oauth:zai:user_info"],
                             json.dumps({"id": "user-old", "email": "", "name": "User"}))

            with mock.patch.object(zcas, "is_zcode_running", return_value=False), \
                 mock.patch.object(zcas, "kill_zcode"), mock.patch.object(zcas, "launch_zcode"):
                result = zcas.switch_account(old["meta"]["id"], restart=False)
            self.assertEqual(result["account"]["id"], old["meta"]["id"])
            profile = json.loads(zcas.read_json(zcas.credentials_file())["oauth:zai:user_info"])
            self.assertEqual(profile["id"], "user-old")

    def test_macos_restart_opens_zcode_in_foreground(self):
        calls = []
        results = [subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0)]
        with mock.patch.dict("os.environ", self.env), \
             mock.patch.object(zcas.subprocess, "run", side_effect=lambda args, **_: (
                 calls.append(args), results[len(calls) - 1]
             )[1]):
            zcas.launch_zcode()
        self.assertEqual(calls, [["/usr/bin/open", "-b", "dev.zcode.app"], ["/usr/bin/open", "-a", "ZCode"]])

    def test_macos_process_commands_do_not_depend_on_inherited_path(self):
        calls = []
        with mock.patch.dict("os.environ", self.env), \
             mock.patch.object(zcas.subprocess, "check_output", side_effect=lambda args, **_: (
                 calls.append(args), "123 ZCode\n"
             )[1]):
            self.assertEqual(zcas.zcode_pids(), [123])
        self.assertEqual(calls, [["/bin/ps", "-axo", "pid=,comm="]])

    def test_macos_quit_uses_bundle_id_before_signal_fallback(self):
        run_calls = []
        with mock.patch.dict("os.environ", self.env), \
             mock.patch.object(zcas.subprocess, "run", side_effect=lambda args, **_: (
                 run_calls.append(args), subprocess.CompletedProcess([], 0)
             )[1]), \
             mock.patch.object(zcas, "zcode_pids", side_effect=[[123], [123], [123], []]), \
             mock.patch.object(zcas.time, "time", side_effect=[0, 0, 10, 20, 30]), \
             mock.patch.object(zcas.time, "sleep"), \
             mock.patch.object(zcas.os, "kill") as kill:
            zcas.kill_zcode()
        self.assertEqual(run_calls, [["/usr/bin/osascript", "-e", 'tell application id "dev.zcode.app" to quit']])
        self.assertEqual(kill.call_args_list,
                         [mock.call(123, zcas.signal.SIGTERM), mock.call(123, zcas.signal.SIGKILL)])

    def test_macos_quit_timeout_falls_back_to_signals(self):
        with mock.patch.dict("os.environ", self.env), \
             mock.patch.object(zcas.subprocess, "run", side_effect=subprocess.TimeoutExpired("osascript", 15)), \
             mock.patch.object(zcas, "zcode_pids", side_effect=[[123], [], []]), \
             mock.patch.object(zcas.time, "time", side_effect=[0, 0, 1]), \
             mock.patch.object(zcas.time, "sleep"), \
             mock.patch.object(zcas.os, "kill") as kill:
            zcas.kill_zcode()
        self.assertEqual(kill.call_args_list, [mock.call(123, zcas.signal.SIGTERM)])

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
