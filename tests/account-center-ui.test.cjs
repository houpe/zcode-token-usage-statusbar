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

test("account center embeds in statusbar settings and exposes a hover-only switcher", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "account-center.js"), "utf8");
  assert.match(source, /renderContainer\(host, "settings"\)/);
  assert.match(source, /renderContainer\(panel\.querySelector\("\.zcas-body"\), "hover"\)/);
  assert.match(source, /chip\.addEventListener\("pointerenter"/);
  assert.match(source, /request\("account-use", \{ accountId: account\.id \}\)/);
  const overlay = fs.readFileSync(path.join(__dirname, "..", "overlay.js"), "utf8");
  assert.match(overlay, /data-sec="account"/);
  assert.match(overlay, /class="zu-account-chip-toggle"/);
  assert.match(overlay, /class="zu-account-unbind"/);
  assert.match(overlay, /__zusageAccountSetPrefs/);
});

test("local account unbind is opt-out and never offered for the running account", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "account-center.js"), "utf8");
  assert.match(source, /request\("account-delete", \{ accountId: account\.id \}\)/);
  assert.match(source, /unbind\.dataset\.alwaysDisabled = isCurrent \? "true" : "false"/);
  assert.match(source, /if \(prefs\.unbind\)/);
  assert.match(source, /raw\.unbind === false/);
});

test("hiding the account chip beats its display rule", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "account-center.js"), "utf8");
  assert.match(source, /\.zusage-account-chip\[hidden\]\{display:none!important\}/);
});

test("account styles mount before chip listeners use the panel", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "account-center.js"), "utf8");
  assert.ok(
    source.lastIndexOf("ensurePanel();") < source.indexOf('panel.addEventListener("pointerenter"'),
    "ensurePanel must run before panel listeners",
  );
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

test("hover account panel uses compact statusbar density", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "account-center.js"), "utf8");
  assert.match(source, /\.zusage-account-panel\{[^}]*width:min\(320px/);
  assert.match(source, /\.zusage-account-panel\{[^}]*font:12px\/1\.35/);
  assert.match(source, /\.zcas-body\{[^}]*padding:8px/);
  assert.match(source, /\.zcas-account\{[^}]*padding:8px;margin:0 0 6px\}/);
  assert.match(source, /\.zcas-name\{[^}]*font-size:13px/);
  assert.match(source, /\.zcas-meter\{height:3px/);
  assert.match(source, /\.zcas-status:empty\{display:none\}/);
});

test("statusbar tooltip yields to the open account hover panel", () => {
  const overlay = fs.readFileSync(path.join(__dirname, "..", "overlay.js"), "utf8");
  const mousemove = overlay.slice(
    overlay.indexOf('listenGlobal(document, "mousemove"'),
    overlay.indexOf("/* 鼠标甩出窗口外/窗口失焦"),
  );
  assert.match(mousemove, /document\.querySelector\("\.zusage-account-panel\.zcas-open"\)/);
  assert.ok(
    mousemove.indexOf('.zusage-account-panel.zcas-open') <
      mousemove.indexOf("if (!bar.contains(t))"),
    "account panel check must precede statusbar hover retention",
  );
});
