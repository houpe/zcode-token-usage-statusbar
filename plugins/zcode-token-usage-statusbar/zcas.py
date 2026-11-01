#!/usr/bin/env python3
"""Python account bridge for the ZCode token statusbar.

The protocol is one JSON request and one JSON response per line. Responses
contain account metadata only; credentials stay in this process and local files.
"""
import base64
import copy
import hashlib
import json
import os
import re
import signal
import secrets
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


PROVIDERS = {
    "zai": {
        "id": "zai",
        "authorize": "https://chat.z.ai/api/oauth/authorize",
        "app_id": "client_P8X5CMWmlaRO9gyO-KSqtg",
        "redirect": "zcode://zai-auth/callback",
        "slots": ["builtin:zai-start-plan", "builtin:zai-coding-plan", "builtin:zai"],
    },
    "bigmodel": {
        "id": "bigmodel",
        "authorize": "https://bigmodel.cn/login",
        "app_id": "zcode",
        "redirect": "zcode://oauth/callback",
        "slots": ["builtin:bigmodel-coding-plan", "builtin:bigmodel-start-plan"],
    },
}
TOKEN_URL = "https://zcode.z.ai/api/v1/oauth/token"
BILLING_URL = "https://zcode.z.ai/api/v1/zcode-plan/billing/balance"
BUSINESS_LOGIN_URL = "https://api.z.ai/api/auth/z/login"
USAGE_URL = "api/monitor/usage/quota/limit"
ENCRYPTION_PREFIX = "enc:v1:"


def home():
    return Path(os.environ.get("ZCAS_HOME", Path.home()))


def zcode_dir():
    return Path(os.environ.get("ZCAS_ZCODE_DIR", home() / ".zcode" / "v2"))


def data_dir():
    return Path(os.environ.get("ZCAS_DATA_DIR", home() / ".zcas"))


def credentials_file():
    return zcode_dir() / "credentials.json"


def config_file():
    return zcode_dir() / "config.json"


def store_dir():
    return data_dir() / "accounts"


def backup_dir():
    return data_dir() / ".last"


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def atomic_write(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".zcas.tmp-%d" % os.getpid())
    raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    tmp.write_bytes(raw)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def default_secret():
    value = os.environ.get("ZCODE_CREDENTIAL_SECRET")
    if value:
        return value
    return "zcode-credential-fallback:%s:%s:%s" % (
        sys.platform, home(), os.environ.get("USER") or "unknown"
    )


def b64url_decode(value):
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def decrypt(value, secret=None):
    if not value.startswith(ENCRYPTION_PREFIX):
        return value
    parts = value[len(ENCRYPTION_PREFIX):].split(".")
    if len(parts) != 3:
        raise ValueError("enc:v1 format is invalid")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce, tag, ciphertext = (b64url_decode(part) for part in parts)
        return AESGCM(hashlib.sha256(secret.encode()).digest()).decrypt(nonce, ciphertext + tag, None).decode()
    except ImportError:
        # ZCode is an Electron app; use its Node runtime for the small crypto boundary.
        script = (
            "const c=require('crypto');const p=process.argv[1].split('.');"
            "const d=b=>Buffer.from(b.replace(/-/g,'+').replace(/_/g,'/'),'base64');"
            "const a=c.createDecipheriv('aes-256-gcm',c.createHash('sha256').update(process.argv[2]).digest(),d(p[0]));"
            "a.setAuthTag(d(p[1]));process.stdout.write(Buffer.concat([a.update(d(p[2])),a.final()]))"
        )
        out = subprocess.check_output(["node", "-e", script, value, secret or default_secret()])
        return out.decode("utf-8")


def encrypt(value, secret=None):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = secrets.token_bytes(12)
        sealed = AESGCM(hashlib.sha256(secret.encode()).digest()).encrypt(nonce, value.encode(), None)
        b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        return ENCRYPTION_PREFIX + ".".join((b64(nonce), b64(sealed[-16:]), b64(sealed[:-16])))
    except ImportError:
        script = (
            "const c=require('crypto');const b=x=>Buffer.from(x,'base64');"
            "const k=c.createHash('sha256').update(process.argv[2]).digest(),n=c.randomBytes(12);"
            "const s=c.createCipheriv('aes-256-gcm',k,n),o=Buffer.concat([s.update(process.argv[1]),s.final()]);"
            "const u=x=>x.toString('base64').replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');"
            "process.stdout.write('enc:v1:'+u(n)+'.'+u(s.getAuthTag())+'.'+u(o))"
        )
        return subprocess.check_output(["node", "-e", script, value, secret or default_secret()]).decode()


def decrypt_json(value, secret=None):
    try:
        return json.loads(decrypt(value, secret))
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}


def djb2(value):
    number = 5381
    for byte in value.encode("utf-8"):
        number = ((number * 33) ^ byte) & 0xFFFFFFFF
    return "%x" % number


def decode_jwt(token):
    try:
        payload = token.split(".")[1]
        return json.loads(b64url_decode(payload))
    except (IndexError, ValueError, TypeError):
        return {}


def credential_profile(cred, secret):
    provider = "zai"
    raw = cred.get("oauth:active_provider")
    if isinstance(raw, str) and raw:
        try:
            provider = decrypt(raw, secret) or raw
        except Exception:
            provider = raw
    profile = {"provider": provider}
    user = decrypt_json(cred.get("oauth:%s:user_info" % provider, ""), secret)
    raw_profile = user.get("rawProfile") if isinstance(user.get("rawProfile"), dict) else {}
    pick = lambda *keys: next((str(v) for source in (user, raw_profile) for k, v in source.items()
                               if k in keys and isinstance(v, str) and v), "")
    profile.update(email=pick("email", "mail"), phone=pick("phone", "mobile", "phoneNumber", "phone_number"),
                   name=pick("name", "username", "displayName", "nickName"),
                   avatar=pick("avatar", "avatarUrl", "picture"),
                   user_id=pick("user_id", "userId", "id", "sub", "customerNumber"))
    token = decrypt(cred.get("oauth:%s:access_token" % provider, ""), secret)
    payload = decode_jwt(token) if token else {}
    profile["user_id"] = profile["user_id"] or payload.get("user_id") or payload.get("sub") or ""
    return profile


def fingerprint(cred, cfg, secret):
    profile = credential_profile(cred, secret)
    candidates = []
    providers = cfg.get("provider") if isinstance(cfg.get("provider"), dict) else {}
    for provider_id, slot in providers.items():
        if not isinstance(slot, dict):
            continue
        options = slot.get("options") if isinstance(slot.get("options"), dict) else {}
        key = options.get("apiKey")
        if not isinstance(key, str) or key.startswith(ENCRYPTION_PREFIX) or len(key) < 30:
            continue
        candidates.append((slot.get("enabled") is True, provider_id, key))
    candidates.sort(key=lambda item: not item[0])
    for _, provider_id, key in candidates:
        payload = decode_jwt(key)
        user_id = payload.get("user_id") or payload.get("sub")
        if user_id:
            return _fingerprint(str(user_id), provider_id, profile, "config.jwt")
    raw = cred.get("oauth:active_provider")
    if not raw:
        return None
    hashed = djb2(str(raw))
    user_id = profile.get("user_id") or "enc-" + hashed
    result = _fingerprint(user_id, "(encrypted)", profile, "credentials.fallback")
    result["shortId"] = hashed[:8]
    result["emailShortId"] = user_id[:8]
    if not (profile.get("email") or profile.get("name")):
        result["label"] = "账号-" + hashed[:8]
    return result


def _fingerprint(user_id, provider, profile, source):
    short = user_id[:8]
    email = profile.get("email", "")
    phone = profile.get("phone", "")
    identity = ("em-" + djb2(email.lower())[:10]) if email else ("ph-" + djb2(phone)[:10]) if phone else short
    label = email or (phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else "") or profile.get("name") or "账号-" + short
    return {"userId": user_id, "shortId": short, "emailShortId": identity, "provider": provider,
            "label": label, "email": email, "phone": phone, "name": profile.get("name", ""),
            "avatar": profile.get("avatar", ""), "source": source}


def has_session(cred):
    return bool(cred.get("zcodejwttoken") or cred.get("oauth:active_provider"))


def account_credential_key(key):
    return key in ("zcodejwttoken", "oauth:active_provider") or key.startswith(("oauth:", "account-provider:"))


def account_slot(provider_id, slot):
    if not provider_id.startswith("builtin:") or not isinstance(slot, dict):
        return False
    options = slot.get("options") if isinstance(slot.get("options"), dict) else {}
    return bool(options.get("apiKey"))


def slice_account(cred, cfg):
    fields = {key: value for key, value in cred.items() if account_credential_key(key) and isinstance(value, str)}
    slots = {}
    providers = cfg.get("provider") if isinstance(cfg.get("provider"), dict) else {}
    for provider_id, slot in providers.items():
        if account_slot(provider_id, slot):
            slots[provider_id] = copy.deepcopy(slot)
    return {"version": 2, "credentialFields": fields, "providerSlots": slots}


def safe_id(account_id):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{2,64}", account_id or ""):
        raise ValueError("非法账号 id")
    return account_id


def meta_path(account_id):
    return store_dir() / (safe_id(account_id) + ".meta.json")


def snap_path(account_id):
    return store_dir() / (safe_id(account_id) + ".snap.json")


def account_list():
    result = []
    for path in sorted(store_dir().glob("*.meta.json")) if store_dir().is_dir() else []:
        meta = read_json(path)
        if meta.get("id"):
            result.append(meta)
    return sorted(result, key=lambda item: item.get("capturedAt", 0))


def load_account(account_id):
    meta = read_json(meta_path(account_id))
    snap = read_json(snap_path(account_id))
    if not meta.get("id") or not snap.get("credentialFields"):
        raise ValueError("账号不存在或快照缺失: %s" % account_id)
    return meta, snap


def save_account(meta, snap):
    atomic_write(snap_path(meta["id"]), snap)
    atomic_write(meta_path(meta["id"]), meta)


def current_account():
    cred = read_json(credentials_file())
    if not has_session(cred):
        return None
    result = fingerprint(cred, read_json(config_file()), default_secret())
    if not result:
        return None
    return {key: result.get(key) for key in ("label", "provider", "shortId", "email", "savedId")}


def current_account_state():
    cred = read_json(credentials_file())
    current = None
    if has_session(cred):
        fp = fingerprint(cred, read_json(config_file()), default_secret())
        if fp:
            current = {key: fp.get(key) for key in ("label", "provider", "shortId", "email")}
            current["savedId"] = next((meta["id"] for meta in account_list()
                                       if meta.get("userId") and meta["userId"] == fp.get("userId")), "")
    return {"running": is_zcode_running(), "current": current, "accounts": account_list(),
            "hasLastBackup": (backup_dir() / "credentials.json").exists() or (backup_dir() / "config.json").exists()}


def meta_from_fingerprint(fp, label, note, source="capture"):
    return {"id": fp["emailShortId"], "shortId": fp["shortId"], "emailShortId": fp["emailShortId"],
            "userId": fp["userId"], "provider": fp["provider"], "label": label or fp["label"],
            "email": fp.get("email", ""), "phone": fp.get("phone", ""), "name": fp.get("name", ""),
            "avatar": fp.get("avatar", ""), "note": note, "source": source,
            "capturedAt": int(time.time() * 1000)}


def account_for_fingerprint(fp):
    if fp.get("userId"):
        match = next((meta for meta in account_list()
                      if meta.get("userId") and meta["userId"] == fp["userId"]), None)
        if match:
            return match
    path = meta_path(fp["emailShortId"])
    return read_json(path) if path.exists() else None


def capture(label, note, overwrite):
    cred, cfg = read_json(credentials_file()), read_json(config_file())
    fp = fingerprint(cred, cfg, default_secret())
    if not fp:
        raise ValueError("无法从当前登录态提取账号指纹（请先在 ZCode 里登录）")
    existing = account_for_fingerprint(fp)
    if existing and not overwrite:
        return {"meta": existing, "created": False}
    meta = meta_from_fingerprint(fp, label.strip(), note.strip())
    if existing:
        meta["id"] = existing["id"]
        meta["label"] = existing.get("label") or meta["label"]
        meta["capturedAt"] = existing.get("capturedAt", meta["capturedAt"])
        meta["updatedAt"] = int(time.time() * 1000)
    save_account(meta, slice_account(cred, cfg))
    return {"meta": meta, "created": not existing}


def token_iat(value, secret):
    try:
        return int(decode_jwt(decrypt(value, secret)).get("iat", 0))
    except Exception:
        return 0


def write_back_current():
    cred, cfg = read_json(credentials_file()), read_json(config_file())
    fp = fingerprint(cred, cfg, default_secret())
    meta = account_for_fingerprint(fp) if fp else None
    if not meta:
        return
    _, old = load_account(meta["id"])
    if meta.get("userId") and fp["userId"] != meta["userId"]:
        return
    snap = slice_account(cred, cfg)
    if token_iat(old.get("credentialFields", {}).get("zcodejwttoken", ""), default_secret()) > \
       token_iat(snap.get("credentialFields", {}).get("zcodejwttoken", ""), default_secret()):
        return
    atomic_write(Path(str(snap_path(meta["id"])) + ".bak"), old)
    meta["updatedAt"] = int(time.time() * 1000)
    save_account(meta, snap)


def apply_snapshot(snap, cred, cfg):
    new_cred = {key: value for key, value in cred.items() if not account_credential_key(key)}
    new_cred.update(snap.get("credentialFields", {}))
    new_cfg = copy.deepcopy(cfg)
    providers = new_cfg.setdefault("provider", {})
    for provider_id, slot in list(providers.items()):
        if account_slot(provider_id, slot):
            providers.pop(provider_id)
    for provider_id, slot in snap.get("providerSlots", {}).items():
        old = providers.get(provider_id)
        if isinstance(old, dict):
            merged = copy.deepcopy(old)
            merged.update(slot)
            if isinstance(slot.get("options"), dict):
                options = dict(merged.get("options") or {})
                options.update(slot["options"])
                merged["options"] = options
            providers[provider_id] = merged
        else:
            providers[provider_id] = copy.deepcopy(slot)
    return new_cred, new_cfg


def backup_last():
    backup_dir().mkdir(parents=True, exist_ok=True)
    for path in (credentials_file(), config_file()):
        if path.exists():
            atomic_write(backup_dir() / path.name, path.read_bytes())


def restore_last():
    for target in (credentials_file(), config_file()):
        source = backup_dir() / target.name
        if source.exists():
            atomic_write(target, source.read_bytes())
        elif target.exists():
            target.unlink()


def zcode_pids():
    try:
        ps = "/bin/ps" if sys.platform == "darwin" else "ps"
        output = subprocess.check_output([ps, "-axo", "pid=,comm="], text=True)
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in output.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2 and os.path.basename(fields[1]) == "ZCode":
            pids.append(int(fields[0]))
    return pids


def is_zcode_running():
    return bool(zcode_pids())


def kill_zcode():
    if sys.platform == "darwin":
        try:
            subprocess.run(["/usr/bin/osascript", "-e", 'tell application id "dev.zcode.app" to quit'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass
    pids = zcode_pids()
    for pid in pids:
        try: os.kill(pid, signal.SIGTERM)
        except ProcessLookupError: pass
    deadline = time.time() + 8
    while time.time() < deadline and zcode_pids():
        time.sleep(.2)
    pids = zcode_pids()
    for pid in pids:
        try: os.kill(pid, signal.SIGKILL)
        except ProcessLookupError: pass


def launch_zcode():
    open_cmd = "/usr/bin/open" if sys.platform == "darwin" else "open"
    for args in ([open_cmd, "-b", "dev.zcode.app"], [open_cmd, "-a", "ZCode"]):
        if subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            return
    raise RuntimeError("启动 ZCode 失败")


def switch_account(account_id, restart=True):
    meta, snap = load_account(account_id)
    kill_zcode()
    backup_last()
    write_back_current()
    try:
        new_cred, new_cfg = apply_snapshot(snap, read_json(credentials_file()), read_json(config_file()))
        atomic_write(credentials_file(), new_cred)
        atomic_write(config_file(), new_cfg)
    except Exception:
        restore_last()
        raise
    if restart:
        launch_zcode()
    return {"account": meta}


def rollback(restart=True):
    if not (backup_dir() / "credentials.json").exists() and not (backup_dir() / "config.json").exists():
        raise ValueError("没有可回滚的备份")
    kill_zcode()
    restore_last()
    if restart:
        launch_zcode()


def candidate_tokens(cred, cfg, secret):
    provider = credential_profile(cred, secret)["provider"]
    keys = ["zcodejwttoken", "oauth:%s:access_token" % provider, "oauth:zai:access_token",
            "oauth:bigmodel:access_token"]
    providers = cfg.get("provider") if isinstance(cfg.get("provider"), dict) else {}
    keys.extend("%s:apiKey" % provider_id for provider_id in sorted(providers)
                if isinstance(providers[provider_id], dict))
    tokens = []
    for key in keys:
        if key in cred:
            value = cred[key]
        elif key.endswith(":apiKey"):
            provider_id = key[:-8]
            slot = providers.get(provider_id, {})
            value = slot.get("options", {}).get("apiKey") if isinstance(slot, dict) else ""
        else:
            continue
        try:
            plain = decrypt(value, secret) if isinstance(value, str) else ""
        except Exception:
            plain = ""
        if len(plain) > 20 and plain not in tokens:
            tokens.append(plain)
    return tokens


def http_json(url, token, headers=None, timeout=15):
    request = urllib.request.Request(url, headers={"Authorization": token, "Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def quota_for(account_id=""):
    secret = default_secret()
    if account_id:
        _, snap = load_account(account_id)
        cred = snap.get("credentialFields", {})
        cfg = {"provider": snap.get("providerSlots", {})}
    else:
        cred, cfg = read_json(credentials_file()), read_json(config_file())
    tokens = candidate_tokens(cred, cfg, secret)
    if not tokens:
        raise ValueError("未找到可用于查询额度的 token")
    last_error = None
    result = {"refreshedAt": int(time.time() * 1000)}
    for token in tokens:
        try:
            provider = credential_profile(cred, secret)["provider"]
            host = "https://chat.z.ai" if provider == "zai" else "https://bigmodel.cn"
            body = http_json(host + "/" + USAGE_URL, token)
            if body.get("code") != 200:
                raise ValueError("额度接口返回异常")
            limits = []
            for limit in body.get("data", {}).get("limits", []):
                unit = {3: "小时", 6: "周"}.get(limit.get("unit"), "u%s" % limit.get("unit"))
                number = limit.get("number", 1)
                limits.append({"name": ("每" + unit) if number == 1 else "每%d%s" % (number, unit),
                               "limit": limit.get("usage", 0), "used": limit.get("currentValue", 0),
                               "remaining": limit.get("remaining", 0), "percent": limit.get("percentage", 0),
                               "nextReset": limit.get("nextResetTime", 0)})
            result["codingPlan"] = {"provider": provider, "level": body.get("data", {}).get("level", ""), "limits": limits}
            if limits:
                result["percentUsed"] = max(item["percent"] for item in limits)
            return result
        except Exception as error:
            last_error = error
    raise ValueError("额度查询失败: %s" % last_error)


def provider_from_input(value):
    key = (value or "zai").strip().lower()
    if key in ("", "zai", "global", "全球"):
        return PROVIDERS["zai"]
    if key in ("bigmodel", "cn", "china", "中国"):
        return PROVIDERS["bigmodel"]
    raise ValueError("未知渠道（可选 zai / bigmodel）")


def authorize_url(provider, state):
    query = {"redirect": provider["redirect"], "appId": provider["app_id"], "state": state} if provider["id"] == "bigmodel" else \
            {"response_type": "code", "client_id": provider["app_id"], "redirect_uri": provider["redirect"], "state": state}
    return provider["authorize"] + "?" + urllib.parse.urlencode(query)


def parse_callback(value):
    value = value.strip()
    if value.startswith(("zcode://", "http://", "https://")):
        parsed = urllib.parse.urlparse(value)
        query = urllib.parse.parse_qs(parsed.query)
    elif "code=" in value:
        query = urllib.parse.parse_qs(value)
    else:
        return value, ""
    code = (query.get("code") or query.get("authCode") or [""])[0]
    if not code:
        raise ValueError("回调链接里没有 code/authCode 参数")
    return code, (query.get("state") or [""])[0]


def exchange_code(provider, code, state):
    body = json.dumps({"provider": provider["id"], "code": code,
                       "redirect_uri": provider["redirect"], "state": state}).encode()
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.loads(response.read().decode())
    if result.get("code") not in (0, 200):
        raise ValueError(result.get("msg") or "token 交换失败")
    data = result.get("data", {})
    oauth = data.get(provider["id"], {})
    if not data.get("token") or not oauth.get("access_token"):
        raise ValueError("token 响应缺少必要字段")
    return data["token"], oauth["access_token"], oauth.get("refresh_token", ""), data.get("user", {})


def credential_fields(provider, token, access_token, refresh_token, user, secret):
    if provider["id"] == "zai" and isinstance(user.get("rawProfile"), dict):
        profile = dict(user["rawProfile"])
    else:
        profile = dict(user)
    for target, keys in (("id", ("id", "user_id", "userId", "sub", "customerNumber")),
                         ("username", ("username", "name", "displayName", "email")),
                         ("displayName", ("displayName", "name", "username", "email"))):
        profile.setdefault(target, next((user.get(key) for key in keys if user.get(key)), ""))
    fields = {
        "oauth:active_provider": encrypt(provider["id"], secret),
        "oauth:%s:access_token" % provider["id"]: encrypt(access_token, secret),
        "zcodejwttoken": encrypt(token, secret),
        "oauth:%s:user_info" % provider["id"]: encrypt(json.dumps(profile, ensure_ascii=False), secret),
    }
    if refresh_token:
        fields["oauth:%s:refresh_token" % provider["id"]] = encrypt(refresh_token, secret)
    return fields


def provider_slots(provider, token):
    live = read_json(config_file()).get("provider", {})
    template = next((live[key] for key in sorted(live) if key.startswith("builtin:") and
                     isinstance(live[key], dict) and isinstance(live[key].get("models"), dict)), None)
    slots = {}
    for slot_id in provider["slots"]:
        slot = copy.deepcopy(live.get(slot_id) or template or {})
        slot["enabled"] = True
        slot.setdefault("options", {})["apiKey"] = token
        slots[slot_id] = slot
    return slots


def add_or_update_oauth(pending, code, state, name, note):
    provider, account_id = pending["provider"], pending["accountId"]
    if state and state != pending["state"]:
        raise ValueError("授权回调 state 不匹配")
    token, access_token, refresh_token, user = exchange_code(provider, code, state)
    secret = default_secret()
    fields = credential_fields(provider, token, access_token, refresh_token, user, secret)
    if account_id:
        meta, old = load_account(account_id)
        slots = provider_slots(provider, token)
        for slot_id, old_slot in old.get("providerSlots", {}).items():
            if slot_id in slots:
                merged = copy.deepcopy(old_slot)
                merged["enabled"] = True
                merged.setdefault("options", {})["apiKey"] = token
                slots[slot_id] = merged
        fp = fingerprint(fields, {"provider": slots}, secret)
        if fp["emailShortId"] != account_id and meta.get("userId") and fp["userId"] != meta.get("userId"):
            raise ValueError("授权账号与目标账号不一致")
        meta["updatedAt"] = int(time.time() * 1000)
        save_account(meta, {"version": 2, "credentialFields": fields, "providerSlots": slots})
        return {"meta": meta, "reauth": True, "restartRequired": True}
    fp = fingerprint(fields, {"provider": provider_slots(provider, token)}, secret)
    if not fp:
        raise ValueError("无法从 token 中提取账号指纹")
    if meta_path(fp["emailShortId"]).exists():
        return {"meta": read_json(meta_path(fp["emailShortId"])), "created": False}
    meta = meta_from_fingerprint(fp, name, note, "oauth")
    save_account(meta, {"version": 2, "credentialFields": fields, "providerSlots": provider_slots(provider, token)})
    try:
        request = urllib.request.Request(BUSINESS_LOGIN_URL, data=json.dumps({"token": access_token}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(request, timeout=5).read()
    except Exception:
        pass
    return {"meta": meta, "created": True}


def open_url(url):
    command = "start" if os.name == "nt" else "open" if sys.platform == "darwin" else "xdg-open"
    if subprocess.run([command, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        raise RuntimeError("打开浏览器失败")


def serve():
    pending = None
    for line in sys.stdin:
        try:
            request = json.loads(line)
            action = request.get("action", "")
            account_id = request.get("accountId", "")
            if action == "state":
                result = current_account_state()
            elif action == "quota":
                result = {"accountId": account_id, "quota": quota_for(account_id)}
            elif action == "capture":
                result = capture(request.get("name", ""), request.get("note", ""), request.get("overwrite") is True)
            elif action == "rename":
                if not request.get("name", "").strip():
                    raise ValueError("账号名称不能为空")
                meta, snap = load_account(account_id)
                meta["label"] = request["name"].strip()
                meta["note"] = request.get("note", "")
                save_account(meta, snap)
                result = {"meta": meta}
            elif action == "delete":
                removed = False
                for path in (meta_path(account_id), snap_path(account_id), Path(str(snap_path(account_id)) + ".bak")):
                    if path.exists():
                        path.unlink(); removed = True
                if not removed: raise ValueError("找不到账号: %s" % account_id)
                result = {}
            elif action == "oauth-start":
                provider = provider_from_input(request.get("provider", "zai"))
                if account_id:
                    _, snap = load_account(account_id)
                    raw = snap.get("credentialFields", {}).get("oauth:active_provider", "zai")
                    try: provider = provider_from_input(decrypt(raw, default_secret()))
                    except Exception: pass
                state = secrets.token_hex(16)
                open_url(authorize_url(provider, state))
                pending = {"provider": provider, "state": state, "accountId": account_id,
                           "name": request.get("name", ""), "note": request.get("note", "")}
                result = {"pending": True, "reauth": bool(account_id)}
            elif action == "oauth-finish":
                if not pending: raise ValueError("没有进行中的授权，请先选择添加或重新授权")
                current, pending = pending, None
                parsed = parse_callback(request.get("input", ""))
                result = add_or_update_oauth(current, parsed[0], parsed[1],
                                             name=current.get("name", ""), note=current.get("note", ""))
            elif action == "oauth-cancel":
                pending = None; result = {}
            else:
                raise ValueError("未知操作: %s" % action)
            response = {"id": request.get("id"), "ok": True, "result": result}
        except Exception as error:
            response = {"id": request.get("id") if isinstance(request, dict) else None, "ok": False, "error": str(error)}
        print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "use":
        print(json.dumps(switch_account(sys.argv[2]), ensure_ascii=False))
    elif sys.argv == [sys.argv[0], "rollback"]:
        rollback()
    elif sys.argv == [sys.argv[0], "serve"]:
        serve()
    else:
        print("用法: zcas.py serve | use <accountId> | rollback")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
