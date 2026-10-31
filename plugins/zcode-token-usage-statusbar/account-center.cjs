"use strict";

const { spawn } = require("child_process");

const BRIDGE_ACTIONS = new Set([
  "state", "quota", "capture", "rename", "delete",
  "oauth-start", "oauth-finish", "oauth-cancel",
]);

function createAccountCenter(options) {
  const command = options.command;
  const scriptPath = options.scriptPath;
  const spawnProcess = options.spawnProcess || spawn;
  const spawnDetached = options.spawnDetached || spawn;
  const log = options.log || (() => {});
  let child = null;
  let buffer = "";
  let sequence = 0;
  const pending = new Map();

  function rejectPending(error) {
    for (const item of pending.values()) {
      clearTimeout(item.timer);
      item.reject(error);
    }
    pending.clear();
  }

  function ensureBridge() {
    if (child) return child;
    child = spawnProcess(command, [scriptPath, "serve"], { windowsHide: true });
    child.stdout.on("data", (chunk) => {
      buffer += chunk.toString("utf8");
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        newline = buffer.indexOf("\n");
        if (!line) continue;
        let response;
        try { response = JSON.parse(line); } catch { continue; }
        const item = pending.get(response.id);
        if (!item) continue;
        pending.delete(response.id);
        clearTimeout(item.timer);
        if (!response.ok) item.reject(new Error(response.error || "账号操作失败"));
        else if (response.result && typeof response.result === "object") item.resolve({ ok: true, ...response.result });
        else item.resolve({ ok: true });
      }
    });
    child.on("error", (error) => {
      rejectPending(error);
      child = null;
    });
    child.on("exit", () => {
      rejectPending(new Error("zcas 已退出，请重新打开账号面板"));
      child = null;
    });
    return child;
  }

  function call(action, req) {
    if (!BRIDGE_ACTIONS.has(action)) return Promise.reject(new Error("未知账号操作: " + action));
    return new Promise((resolve, reject) => {
      const id = "account-" + (++sequence);
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error("账号操作超时，请稍后重试"));
      }, action === "quota" || action === "oauth-finish" ? 120000 : 15000);
      pending.set(id, { resolve, reject, timer });
      const wire = { id, action };
      if (typeof req.accountId === "string") wire.accountId = req.accountId;
      if (typeof req.name === "string") wire.name = req.name.slice(0, 120);
      if (typeof req.note === "string") wire.note = req.note.slice(0, 500);
      if (typeof req.provider === "string") wire.provider = req.provider;
      if (typeof req.input === "string") wire.input = req.input.slice(0, 16384);
      wire.overwrite = req.overwrite === true;
      try { ensureBridge().stdin.write(JSON.stringify(wire) + "\n"); }
      catch (error) {
        clearTimeout(timer);
        pending.delete(id);
        reject(error);
      }
    });
  }

  function handle(req) {
    if (!req || typeof req.action !== "string" || !req.action.startsWith("account-")) {
      return Promise.reject(new Error("未知账号操作"));
    }
    return call(req.action.slice(8), req);
  }

  function restart(req) {
    let args;
    const accountId = req.accountId || req.id;
    if (req.action === "account-use" && typeof accountId === "string" && accountId) {
      args = ["use", accountId];
    } else if (req.action === "account-rollback") {
      args = ["rollback"];
    } else {
      return { ok: false, error: "无效的重启操作" };
    }
    const detached = spawnDetached(command, [scriptPath].concat(args), { detached: true, stdio: "ignore", windowsHide: true });
    detached.unref();
    return { ok: true, pending: true, restarting: true };
  }

  function dispose() {
    rejectPending(new Error("ZCode 正在退出"));
    if (child) {
      try { child.stdin.end(); } catch {}
      try { child.kill(); } catch {}
      child = null;
    }
  }

  return { handle, restart, dispose, _log: log };
}

module.exports = { createAccountCenter };
