"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { createAccountCenter } = require("../account-center.cjs");

function fakeBridge() {
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.stdin = { write: (line) => child.writes.push(line), end: () => {} };
  child.writes = [];
  child.kill = () => {};
  return child;
}

test("account requests use JSON lines and return only the bridge result", async () => {
  const child = fakeBridge();
  const center = createAccountCenter({ command: "/fixture/python", scriptPath: "/fixture/zcas.py", spawnProcess: () => child });
  const pending = center.handle({ action: "account-state" });
  const request = JSON.parse(child.writes[0]);
  assert.equal(request.action, "state");
  child.stdout.emit("data", Buffer.from(JSON.stringify({ id: request.id, ok: true, result: { accounts: [] } }) + "\n"));
  assert.deepEqual(await pending, { ok: true, accounts: [] });
  center.dispose();
});

test("unknown account actions never reach zcas", async () => {
  const child = fakeBridge();
  const center = createAccountCenter({ command: "/fixture/python", scriptPath: "/fixture/zcas.py", spawnProcess: () => child });
  await assert.rejects(center.handle({ action: "account-read-credentials" }), /未知账号操作/);
  assert.equal(child.writes.length, 0);
  center.dispose();
});

test("switch and rollback detach so zcas survives the ZCode restart", () => {
  const calls = [];
  const center = createAccountCenter({
    command: "/fixture/python",
    scriptPath: "/fixture/zcas.py",
    spawnProcess: () => { throw new Error("bridge should not start"); },
    spawnDetached: (command, args, options) => {
      const child = { unref: () => calls.push({ command, args, options, unref: true }) };
      return child;
    },
  });
  assert.deepEqual(center.restart({ action: "account-use", id: "acct_1" }),
    { ok: true, pending: true, restarting: true });
  assert.equal(calls[0].command, "/fixture/python");
  assert.deepEqual(calls[0].args, ["/fixture/zcas.py", "use", "acct_1"]);
  assert.equal(calls[0].options.detached, true);
  assert.equal(calls[0].unref, true);
});
