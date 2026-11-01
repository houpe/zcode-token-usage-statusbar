/* ZCS account center: metadata-only renderer client for the local zcas bridge. */
(function () {
  "use strict";
  if (typeof window === "undefined" || !window.document) return;
  if (window.__zusageAccountDispose) window.__zusageAccountDispose();

  var state = { current: null, accounts: [], hasLastBackup: false };
  var prefs = readPrefs();
  var quotas = {};
  var panel = null, anchor = null, busy = false, oauth = null, captureOpen = false;
  var chips = [];
  var closeTimer = 0, pinned = false;
  var ICONS = {
    user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
    switch: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 8h13m0 0-3-3m3 3-3 3"/><path d="M20 16H7m0 0 3-3m-3 3 3 3"/></svg>',
    key: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="8" cy="15" r="4"/><path d="m11 12 8-8m-3 3 3 3"/></svg>',
    save: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8M7 3v5h8"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M20 6v5h-5M4 18v-5h5"/><path d="M18.5 9A7 7 0 0 0 6 6.5L4 9m2 6a7 7 0 0 0 12 2.5l2-2.5"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>',
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>',
  };

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function iconButton(name, label) {
    var button = el("button", "zcas-icon-btn");
    button.type = "button";
    button.title = label;
    button.setAttribute("aria-label", label);
    button.innerHTML = ICONS[name];
    return button;
  }
  function command(text, className) {
    var button = el("button", "zcas-command " + (className || ""), text);
    button.type = "button";
    return button;
  }
  function readPrefs() {
    var result = { chip: true, unbind: true };
    try {
      var raw = JSON.parse(localStorage.getItem("zusage3.accountUi") || "{}");
      if (raw && typeof raw === "object") {
        if (raw.chip === false) result.chip = false;
        if (raw.unbind === false) result.unbind = false;
      }
    } catch (error) { }
    return result;
  }
  function request(action, extra) {
    return new Promise(function (resolve, reject) {
      if (typeof window.__zusagePluginRequest !== "function") {
        reject(new Error("主进程尚未加载账号中心，请完全重启 ZCode"));
        return;
      }
      window.__zusagePluginRequest(action, extra || {}, function (result) {
        if (!result || result.ok === false) reject(new Error(result && result.error || "账号操作失败"));
        else resolve(result);
      });
    });
  }
  function quotaText(quota) {
    if (!quota) return "额度未刷新";
    if (quota.error) return "额度查询失败";
    var cp = quota.codingPlan;
    if (cp && Array.isArray(cp.limits) && cp.limits.length) {
      return cp.limits
        .filter(function (limit) { return limit.name === "每5小时" || limit.name === "每周"; })
        .map(function (limit) { return (limit.name === "每周" ? "周" : "5小时") + " " + limit.percent + "%"; })
        .join(" · ");
    }
    if (typeof quota.percentUsed === "number") return "套餐已用 " + Math.round(quota.percentUsed) + "%";
    if (quota.isEmpty) return "暂无套餐数据";
    return "额度已更新";
  }
function quotaBlock(quota, className) {
  var line = el("div", "zcas-quota " + (className || ""), quotaText(quota));
  var cp = quota && quota.codingPlan;
  if (!quota || quota.error || !cp || !Array.isArray(cp.limits)) return line;
  cp.limits.forEach(function (limit) {
    var meter = el("div", "zcas-meter");
    var bar = el("span", "zcas-meter-fill");
    var percent = Math.max(0, Math.min(100, Number(limit.percent) || 0));
    bar.style.width = percent + "%";
    bar.dataset.percent = String(percent);
    meter.appendChild(bar);
      meter.title = limit.name + " " + percent + "%";
      meter.setAttribute("aria-label", limit.name + " " + percent + "%");
    line.appendChild(meter);
  });
  return line;
}
  function isCurrentAccount(account) {
    var current = state.current;
    if (!current) return false;
    return (current.savedId && current.savedId === account.id) ||
      (!!current.shortId && current.shortId === account.shortId) ||
      (!!current.email && current.email === account.email);
  }
  function ensurePanel() {
    if (panel) return panel;
    panel = el("section", "panel zusage-panel zusage-account-panel zcas-panel");
    panel.setAttribute("aria-label", "账号中心");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "false");
    var style = el("style");
    style.textContent =
      ".zusage-account-chip{display:inline-flex;align-items:center;gap:5px;box-sizing:border-box;height:24px;max-width:142px;padding:0 7px;border:0;border-radius:6px;background:transparent;color:inherit;font:inherit;cursor:pointer;flex:0 0 auto}" +
      ".zusage-account-chip[hidden]{display:none!important}" +
      ".zusage-account-chip:hover,.zusage-account-chip[aria-expanded=true]{background:var(--color-background-hover,rgba(127,127,127,.12))}" +
      ".zusage-account-chip svg{width:14px;height:14px;flex:0 0 auto}.zusage-account-chip span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" +
      ".zusage-account-panel{position:fixed;z-index:2147483000;box-sizing:border-box;width:min(430px,calc(100vw - 16px));max-height:min(560px,calc(100vh - 16px));display:none;flex-direction:column;overflow:hidden;border:1px solid var(--color-popover-border,var(--color-border,rgba(127,127,127,.2)));border-radius:8px;background:var(--color-popover,#16181d);color:var(--color-popover-foreground,var(--color-foreground,#f3f4f6));box-shadow:0 18px 48px rgba(0,0,0,.34);font:13px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;letter-spacing:0}" +
      ".zusage-account-panel.zcas-open{display:flex}.zusage-account-panel *{box-sizing:border-box;letter-spacing:0}" +
      ".zcas-head{display:flex;align-items:center;gap:8px;padding:14px 16px;border-bottom:1px solid var(--color-border,rgba(127,127,127,.2));background:var(--color-card,rgba(127,127,127,.04))}.zcas-head strong{font-size:14px;font-weight:650;flex:1}.zcas-head svg{width:16px;height:16px}" +
      ".zcas-body{overflow:auto;min-height:0;padding:12px 14px;scrollbar-width:thin;scrollbar-color:var(--color-foreground-subtlest,#6b7280) transparent}" +
      ".zcas-account{border:1px solid var(--color-border,rgba(127,127,127,.16));border-radius:8px;background:var(--color-card,rgba(127,127,127,.055));padding:12px;margin:0 0 10px}.zcas-name{font-size:15px;font-weight:650;overflow-wrap:anywhere}" +
      ".zcas-meta,.zcas-quota{color:var(--color-foreground-subtle,#a7adb8);font-size:12px;margin-top:4px}.zcas-quota{display:grid;gap:5px}.zcas-quota[data-error=true]{color:#f0a35e}" +
      ".zcas-meter{height:5px;border-radius:999px;background:var(--color-background-hover,rgba(127,127,127,.18));overflow:hidden}.zcas-meter-fill{display:block;height:100%;border-radius:inherit;background:var(--color-brand,#8aa4ff)}.zcas-meter-fill[data-percent^=\"9\"],.zcas-meter-fill[data-percent=\"100\"]{background:var(--color-warning,#f5a942)}" +
      ".zcas-actions{display:flex;align-items:center;gap:8px;padding:12px 0}.zcas-actions .zcas-spacer{flex:1}" +
      ".zcas-command,.zcas-icon-btn{border:1px solid var(--color-border,rgba(127,127,127,.28));border-radius:6px;background:var(--color-background-elevated,rgba(127,127,127,.08));color:inherit;font:inherit;cursor:pointer;min-height:28px;padding:4px 10px}" +
      ".zcas-command:hover,.zcas-icon-btn:hover{background:var(--color-background-hover,rgba(127,127,127,.16))}.zcas-command:disabled,.zcas-icon-btn:disabled{opacity:.45;cursor:default}" +
      ".zcas-command.zcas-primary{background:var(--color-brand,#6d8dff);color:var(--color-on-brand,#101218);border-color:transparent;font-weight:600}" +
      ".zcas-icon-btn{display:inline-flex;align-items:center;justify-content:center;width:28px;padding:5px;flex:0 0 auto}.zcas-icon-btn svg{width:16px;height:16px}" +
      ".zcas-account{display:flex;align-items:center;gap:10px}.zcas-account-main{flex:1;min-width:0}.zcas-account-actions{display:flex;gap:6px;align-items:center;flex:0 0 auto}" +
      ".zcas-current-badge{display:inline-block;margin-left:6px;padding:0 5px;border-radius:4px;background:var(--color-background-hover,rgba(127,127,127,.14));font-size:11px;font-weight:500;color:var(--color-foreground-subtle,#b4bac5)}" +
      ".zcas-empty{padding:22px 0;text-align:center;color:var(--color-foreground-subtle,#a7adb8)}" +
      ".zcas-form{padding:12px 0;border-top:1px solid var(--color-border,rgba(127,127,127,.18));display:grid;gap:9px}.zcas-form label{display:grid;gap:4px;color:var(--color-foreground-subtle,#b4bac5);font-size:12px}" +
      ".zcas-form input,.zcas-form select,.zcas-form textarea{width:100%;border:1px solid var(--color-input-border,rgba(127,127,127,.32));border-radius:6px;background:var(--color-input,#191a1c);color:var(--color-foreground,#f3f4f6);font:inherit;padding:7px 8px;outline:none}" +
      ".zcas-form textarea{min-height:68px;resize:vertical}.zcas-form input:focus,.zcas-form select:focus,.zcas-form textarea:focus,.zcas-command:focus-visible,.zcas-icon-btn:focus-visible,.zusage-account-chip:focus-visible{outline:2px solid var(--color-input-border-focused,#8aa4ff);outline-offset:2px}" +
      ".zcas-form-row{display:flex;gap:8px;justify-content:flex-end}.zcas-form-note{margin:0;color:var(--color-foreground-subtle,#a7adb8);font-size:12px}" +
      ".zcas-status{min-height:20px;padding:0 16px 8px;color:var(--color-foreground-subtle,#b4bac5);font-size:12px}.zcas-status[data-error=true]{color:#f0a35e}" +
      ".zcas-foot{display:flex;align-items:center;gap:8px;padding:10px 16px;border-top:1px solid var(--color-border,rgba(127,127,127,.2));color:var(--color-foreground-subtle,#a7adb8);font-size:12px}.zcas-foot span{flex:1}" +
      ".zu-account-host{display:block;width:100%}.zu-account-host .zcas-content{padding:4px 12px 12px}.zu-account-host .zcas-actions{padding-top:8px}" +
      "@media(max-width:520px){.zusage-account-panel{width:calc(100vw - 16px)}.zcas-account{align-items:flex-start;flex-wrap:wrap}.zcas-account-actions{width:100%;justify-content:flex-end}.zusage-account-chip{max-width:108px}}";
    panel.appendChild(style);
    var body = el("div", "zcas-body");
    var status = el("div", "zcas-status");
    status.setAttribute("aria-live", "polite");
    panel.append(body, status);
    document.body.appendChild(panel);
    return panel;
  }

  function renderChips() {
    var label = state.current && state.current.label || "账号";
    chips.forEach(function (chip) {
      if (!chip.isConnected) return;
      var text = chip.querySelector("span");
      // childList 观察器会被 textContent 的子节点替换再次触发；同值不写，阻断自激循环。
      if (text.textContent !== label) text.textContent = label;
      chip.title = state.current ? "账号中心 · " + label : "账号中心 · 保存或添加账号";
      chip.hidden = !prefs.chip;
    });
  }

  function closePanel() {
    if (!panel) return;
    pinned = false;
    cancelClose();
    panel.classList.remove("zcas-open");
    if (anchor) anchor.setAttribute("aria-expanded", "false");
    anchor = null;
  }

  function positionPanel() {
    if (!panel || !anchor || !anchor.isConnected) return;
    var rect = anchor.getBoundingClientRect();
    var width = Math.min(430, window.innerWidth - 16);
    panel.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)) + "px";
    panel.style.bottom = Math.max(8, window.innerHeight - rect.top + 8) + "px";
    panel.style.top = "auto";
  }

  function setStatus(message, error) {
    document.querySelectorAll(".zcas-status").forEach(function (node) {
      node.textContent = message || "";
      node.dataset.error = error ? "true" : "false";
    });
  }

  function setBusy(value) {
    busy = value;
    document.querySelectorAll(".zcas-content button,.zcas-content input,.zcas-content select,.zcas-content textarea").forEach(function (node) {
      node.disabled = value || node.dataset.alwaysDisabled === "true";
    });
  }

  function renderContainer(container, mode) {
    var previousStatus = container.querySelector(".zcas-status");
    previousStatus = previousStatus ? previousStatus.textContent : "";
    var content = el("div", "zcas-content");
    if (mode === "settings") {
      var actions = el("div", "zcas-actions");
      var save = iconButton("save", "保存当前");
      save.onclick = function () { captureOpen = !captureOpen; oauth = null; render(); };
      var add = iconButton("plus", "添加账号");
      add.onclick = function () { oauth = { accountId: "", provider: "zai", started: false }; captureOpen = false; render(); };
      var spacer = el("span", "zcas-spacer");
      var refresh = iconButton("refresh", "刷新全部额度");
      refresh.onclick = function () { refreshQuotas(); };
      actions.append(save, add, spacer, refresh);
      content.appendChild(actions);
      if (captureOpen) content.appendChild(captureForm());
      if (oauth) content.appendChild(oauthForm());
    }
    if (!state.accounts.length && !(mode === "settings" && (captureOpen || oauth))) {
      content.appendChild(el("div", "zcas-empty", "还没有保存的账号"));
    }
    state.accounts.forEach(function (account) { content.appendChild(accountRow(account, mode)); });
    if (mode === "settings") {
      var status = el("div", "zcas-status");
      status.setAttribute("aria-live", "polite");
      status.textContent = previousStatus;
      var foot = el("div", "zcas-foot");
      foot.appendChild(el("span", "", state.accounts.length + " 个账号 · 数据仅在本机处理"));
      var rollback = command("回滚上次切换", "zcas-rollback");
      rollback.hidden = !state.hasLastBackup;
      rollback.onclick = function () {
        if (!window.confirm("回滚会恢复切换前的登录态并重启 ZCode。确定继续吗？")) return;
        setBusy(true); setStatus("正在回滚并重启 ZCode…");
        request("account-rollback").catch(function (error) { setBusy(false); setStatus(error.message, true); });
      };
      foot.appendChild(rollback);
      content.append(status, foot);
    }
    container.textContent = "";
    container.appendChild(content);
    setBusy(busy);
  }

  function render() {
    renderChips();
    document.querySelectorAll(".zu-account-host").forEach(function (host) { renderContainer(host, "settings"); });
    if (panel && panel.classList.contains("zcas-open")) {
      renderContainer(panel.querySelector(".zcas-body"), "hover");
    }
  }

  function accountRow(account, mode) {
    var row = el("div", "zcas-account");
    var main = el("div", "zcas-account-main");
    var name = el("div", "zcas-name", account.label || account.email || "账号");
    if (isCurrentAccount(account)) {
      name.appendChild(el("span", "zcas-current-badge", "当前"));
    }
    main.appendChild(name);
    var quota = quotas[account.id];
    main.appendChild(quotaBlock(quota || (isCurrentAccount(account) ? quotas.__current : null)));
    var actions = el("div", "zcas-account-actions");
    var isCurrent = isCurrentAccount(account);
    var use = iconButton("switch", isCurrent ? "当前账号" : "切换");
    use.dataset.alwaysDisabled = isCurrent ? "true" : "false";
    use.onclick = function () {
      if (!window.confirm("切换到「" + account.label + "」会关闭并重启 ZCode。未提交的对话或终端任务请先保存。确定继续吗？")) return;
      setBusy(true);
      setStatus("正在切换账号并重启 ZCode…");
      request("account-use", { accountId: account.id }).then(function () {
        setStatus("切换已启动，ZCode 即将重启…");
      }).catch(function (error) { setBusy(false); setStatus(error.message, true); });
    };
    actions.appendChild(use);
    if (mode === "settings") {
      var reauth = iconButton("key", "重新授权");
      reauth.onclick = function () { oauth = { accountId: account.id, provider: account.provider, started: false }; captureOpen = false; render(); };
      actions.appendChild(reauth);
      if (prefs.unbind) {
        var unbind = iconButton("trash", "解绑本机快照");
        unbind.dataset.alwaysDisabled = isCurrent ? "true" : "false";
        unbind.onclick = function () {
          if (!window.confirm("解绑只删除本机保存的「" + account.label + "」登录快照，不影响线上账号。确定继续吗？")) return;
          setBusy(true); setStatus("正在解绑本机快照…");
          request("account-delete", { accountId: account.id }).then(function () {
            setBusy(false); setStatus("本机快照已解绑"); return loadState(false);
          }).catch(function (error) { setBusy(false); setStatus(error.message, true); });
        };
        actions.appendChild(unbind);
      }
    }
    row.append(main, actions);
    return row;
  }

  function captureForm() {
    var form = el("div", "zcas-form");
    var name = el("input"); name.placeholder = "账号名称（默认使用邮箱）";
    var note = el("input"); note.placeholder = "备注（可选）";
    form.appendChild(labeled("保存当前 ZCode 登录态", name));
    form.appendChild(labeled("备注", note));
    var row = el("div", "zcas-form-row");
    var cancel = command("取消"); cancel.onclick = function () { captureOpen = false; render(); };
    var save = command("保存快照", "zcas-primary");
    save.onclick = function () {
      setBusy(true); setStatus("正在保存当前登录态…");
      request("account-capture", { name: name.value, note: note.value, overwrite: true }).then(function () {
        captureOpen = false; setBusy(false); setStatus("账号快照已保存"); return loadState(false);
      }).catch(function (error) { setBusy(false); setStatus(error.message, true); });
    };
    row.append(cancel, save); form.appendChild(row);
    return form;
  }

  function labeled(text, control) {
    var label = el("label", "", text);
    label.appendChild(control);
    return label;
  }

  function oauthForm() {
    var form = el("div", "zcas-form");
    var title = oauth.accountId ? "重新授权账号" : "通过浏览器添加账号";
    var provider = el("select");
    [["zai", "z.ai（全球）"], ["bigmodel", "BigModel（中国）"]].forEach(function (item) {
      var option = el("option", "", item[1]); option.value = item[0]; provider.appendChild(option);
    });
    provider.value = oauth.provider === "bigmodel" ? "bigmodel" : "zai";
    provider.disabled = !!oauth.accountId || oauth.started;
    var name = el("input"); name.placeholder = "账号名称（可选）"; name.disabled = oauth.started;
    form.appendChild(labeled(title, provider));
    if (!oauth.accountId) form.appendChild(labeled("账号名称", name));
    var callback = el("textarea");
    callback.placeholder = "粘贴浏览器地址栏中的 zcode:// 回调链接";
    if (oauth.started) {
      form.appendChild(labeled("登录完成后粘贴回调链接", callback));
      var note = el("p", "zcas-form-note", "若浏览器唤起 ZCode，可关闭提示并复制地址栏中的完整 zcode:// 链接。");
      form.appendChild(note);
    }
    var row = el("div", "zcas-form-row");
    var cancel = command("取消");
    cancel.onclick = function () {
      if (oauth && oauth.started) request("account-oauth-cancel").catch(function () {});
      oauth = null; render();
    };
    var submit = command(oauth.started ? "完成授权" : "打开登录页", "zcas-primary");
    submit.onclick = function () {
      setBusy(true);
      if (!oauth.started) {
        setStatus("正在打开浏览器登录页…");
        request("account-oauth-start", { accountId: oauth.accountId, provider: provider.value, name: name.value }).then(function () {
          oauth.started = true; setBusy(false); setStatus("请在浏览器中完成登录，然后粘贴回调链接"); render();
        }).catch(function (error) { setBusy(false); setStatus(error.message, true); });
      } else {
        setStatus("正在校验授权并保存账号…");
        request("account-oauth-finish", { input: callback.value }).then(function (result) {
          var reauth = result.reauth;
          oauth = null; setBusy(false); setStatus(reauth ? "授权已更新；切换该账号后新登录态生效" : "账号已添加"); return loadState(true);
        }).catch(function (error) { setBusy(false); setStatus(error.message, true); });
      }
    };
    row.append(cancel, submit); form.appendChild(row);
    return form;
  }

  function refreshQuotas() {
    setBusy(true); setStatus("正在刷新账号额度…");
    var jobs = state.accounts.map(function (account) {
      return request("account-quota", { accountId: account.id }).then(function (result) {
        quotas[account.id] = result.quota;
      }).catch(function (error) { quotas[account.id] = { error: error.message }; });
    });
    if (state.current && !state.current.savedId) {
      jobs.push(request("account-quota", { accountId: "" }).then(function (result) {
        quotas.__current = result.quota;
      }).catch(function (error) { quotas.__current = { error: error.message }; }));
    }
    Promise.all(jobs).then(function () {
      setBusy(false); setStatus("额度已刷新"); render();
    });
  }

  function loadState(withQuota) {
    return request("account-state").then(function (next) {
      state.current = next.current || null;
      state.accounts = Array.isArray(next.accounts) ? next.accounts : [];
      state.hasLastBackup = !!next.hasLastBackup;
      render();
      if (withQuota) refreshQuotas();
    }).catch(function (error) { setStatus(error.message, true); });
  }

  function cancelClose() {
    if (closeTimer) {
      clearTimeout(closeTimer);
      closeTimer = 0;
    }
  }
  function scheduleClose() {
    cancelClose();
    closeTimer = setTimeout(function () {
      closeTimer = 0;
      closePanel();
    }, 220);
  }
  function openPanel(chip) {
    if (document.querySelector(".zusage-panel.zu-settings.open")) return;
    ensurePanel();
    cancelClose();
    pinned = false;
    if (anchor) anchor.setAttribute("aria-expanded", "false");
    anchor = chip;
    anchor.setAttribute("aria-expanded", "true");
    panel.classList.add("zcas-open");
    positionPanel(); render(); loadState(true);
  }

  function createChip() {
    var chip = el("button", "zusage-account-chip");
    chip.type = "button";
    chip.setAttribute("aria-expanded", "false");
    chip.setAttribute("aria-haspopup", "dialog");
    chip.hidden = !prefs.chip;
    chip.innerHTML = ICONS.user;
    chip.appendChild(el("span", "", state.current && state.current.label || "账号"));
    chip.addEventListener("pointerenter", function () { openPanel(chip); });
    chip.addEventListener("pointerleave", function () { if (!pinned) scheduleClose(); });
    chip.addEventListener("focus", function () { openPanel(chip); });
    chip.addEventListener("blur", function () { if (!pinned) scheduleClose(); });
    chip.onclick = function () { pinned = true; openPanel(chip); };
    chips.push(chip);
    return chip;
  }

  function mount() {
    if (panel && !panel.isConnected) panel = null;
    chips = chips.filter(function (chip) { return chip.isConnected; });
    document.querySelectorAll(".zu-account-host").forEach(function (host) {
      if (host.__zcasReady) return;
      host.__zcasReady = true;
      render();
      loadState(false);
    });
    document.querySelectorAll(".zusage-root").forEach(function (root) {
      if (root.querySelector(":scope > .zusage-account-chip")) return;
      root.appendChild(createChip());
    });
    renderChips();
    reconcileWithHostPopup();
  }

  function isAccountMutation(mutation) {
    var node = mutation.target;
    while (node && node !== document.documentElement) {
      if (node === panel || (node.classList && (node.classList.contains("zusage-account-chip") ||
          node.classList.contains("zu-account-host") || node.classList.contains("zcas-content")))) return true;
      node = node.parentElement;
    }
    return false;
  }

  function scheduleMount(mutations) {
    if (mutations) {
      for (var i = 0; i < mutations.length; i++) {
        if (isAccountMutation(mutations[i])) return;
      }
    }
    if (mountTimer) return;
    mountTimer = setTimeout(function () {
      mountTimer = 0;
      mount();
    }, 200);
  }

  function reconcileWithHostPopup() {
    if (!panel || !panel.classList.contains("zcas-open")) return;
    if (document.querySelector(".zusage-panel.zu-settings.open")) {
      closePanel();
      return;
    }
    var dialogs = document.querySelectorAll('[role="dialog"],[role="alertdialog"]');
    for (var i = 0; i < dialogs.length; i++) {
      var dialog = dialogs[i];
      if (panel.contains(dialog) || dialog === panel || dialog.contains(panel)) continue;
      var style = window.getComputedStyle(dialog);
      if (style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) > .05) {
        closePanel();
        return;
      }
    }
  }

  var mountTimer = 0;
  var observer = new MutationObserver(scheduleMount);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  function outside(event) {
    if (!panel || !panel.classList.contains("zcas-open")) return;
    if (panel.contains(event.target) || (anchor && anchor.contains(event.target))) return;
    closePanel();
  }
  function keydown(event) { if (event.key === "Escape") closePanel(); }
  function panelPointerEnter() { cancelClose(); }
  function panelPointerLeave() { if (!pinned) scheduleClose(); }
  function syncPrefsFromStorage(event) {
    if (!event || event.key !== "zusage3.accountUi") return;
    prefs = readPrefs();
    applyPrefs();
  }
  function applyPrefs() {
    renderChips();
    if (!prefs.chip) closePanel();
    render();
  }
  window.__zusageAccountSetPrefs = function (next) {
    prefs = { chip: !(next && next.chip === false), unbind: !(next && next.unbind === false) };
    applyPrefs();
  };
  window.__zusageAccountRefresh = function () {
    return loadState(true);
  };
  ensurePanel();   // 样式随隐藏面板先挂载，避免入口首次出现时使用 SVG 默认尺寸。
  panel.addEventListener("pointerenter", panelPointerEnter);
  panel.addEventListener("pointerleave", panelPointerLeave);
  window.addEventListener("storage", syncPrefsFromStorage);
  document.addEventListener("pointerdown", outside, true);
  document.addEventListener("keydown", keydown, true);
  window.addEventListener("resize", positionPanel);
  window.__zusageAccountDispose = function () {
    observer.disconnect();
    clearTimeout(mountTimer);
    document.removeEventListener("pointerdown", outside, true);
    document.removeEventListener("keydown", keydown, true);
    window.removeEventListener("resize", positionPanel);
    window.removeEventListener("storage", syncPrefsFromStorage);
    if (panel) {
      panel.removeEventListener("pointerenter", panelPointerEnter);
      panel.removeEventListener("pointerleave", panelPointerLeave);
    }
    chips.forEach(function (chip) { chip.remove(); });
    if (panel) panel.remove();
    panel = null;
    delete window.__zusageAccountSetPrefs;
    delete window.__zusageAccountRefresh;
    delete window.__zusageAccountDispose;
  };
  mount();
  loadState(false);
})();
