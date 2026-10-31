"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

test("account chip text updates cannot retrigger its own MutationObserver forever", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "account-center.js"), "utf8");
  const render = source.slice(source.indexOf("function renderChips()"), source.indexOf("function closePanel()"));
  assert.match(render, /textContent\s*!==\s*label/, "unchanged text must not be written on every childList mutation");
});

test("account entry stays inside the plugin root and throttles host DOM observations", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "account-center.js"), "utf8");
  const mount = source.slice(source.indexOf("function mount()"), source.indexOf("function reconcileWithHostPopup"));
  assert.ok(mount.includes('document.querySelectorAll(".zusage-root")'));
  assert.ok(!mount.includes(".zu-tsb,.zu-quota"), "host toolbar insertion triggers host layout loops");
  assert.match(source, /new MutationObserver\(scheduleMount\)/);
  assert.match(source, /setTimeout\(function \(\) \{[\s\S]*mount\(\);[\s\S]*\}, 200\);/);
  assert.match(source, /isAccountMutation\(mutation\)/);
  assert.match(source, /clearTimeout\(mountTimer\)/, "dispose must not run mount after teardown");
});

test("account panel follows statusbar panel styling and yields to host popups", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "account-center.js"), "utf8");
  assert.match(source, /panel zusage-panel zusage-account-panel/);
  assert.match(source, /quotaBlock/);
  assert.match(source, /reconcileWithHostPopup/);
  assert.match(source, /\.zusage-panel\.zu-settings\.open/);
});

test("account cards stay compact and expose icon-only actions accessibly", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "account-center.js"), "utf8");
  assert.doesNotMatch(source, /zcas-current(?!\-badge)/, "current account must not render as a duplicate card");
  assert.match(source, /current\.savedId === account\.id/);
  assert.match(source, /current\.shortId === account\.shortId/);
  const row = source.slice(source.indexOf("function accountRow("), source.indexOf("function captureForm("));
  assert.doesNotMatch(row, /providerText|account\.shortId/);
  assert.match(row, /iconButton\("switch", isCurrent \? "当前账号" : "切换"\)/);
  assert.match(row, /iconButton\("key", "重新授权"\)/);
  assert.match(source, /iconButton\("save", "保存当前"\)/);
  assert.match(source, /iconButton\("plus", "添加账号"\)/);
  assert.doesNotMatch(source, /Start Plan|Coding Plan/);
});
