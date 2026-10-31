// ctutor for VS Code —— 只做 VS Code 本身没有的四件事：
//   ① 中文五层讲解（hover / CodeLens / 侧面板）
//   ② 只讲最关键的一条（降噪）
//   ③ 确定性修复 + 改完重编验证
//   ④ 运行时的"等输入不计时 + 实时输出"语义
// 编辑器该有的（补全、格式化、跳转、多光标、Git……）一律交给 VS Code 自己。
//
// 架构：本文件只是薄壳；真正的判断在内核里，通过 sidecar 子进程（NDJSON over stdio）调用。
// 为什么要子进程：内核是 Python（114 条知识库 + 编译管线），重写成 TS 不划算；
// 常驻进程只导入一次（约 77ms），按保存触发检查才不会卡。
'use strict';

const vscode = require('vscode');
const cp = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const CFG = 'ctutor';
const OUT_NAME = 'ctutor 内核日志';

// ---------------------------------------------------------------- 配置读取
function cfg() { return vscode.workspace.getConfiguration(CFG); }
function featureOn(key) {
  if (!cfg().get('enabled', true)) { return false; }
  return cfg().get('features.' + key, true) === true;
}
function firstOnly() { return cfg().get('explain.firstOnly', true) === true; }
function compilerPrefer() { return cfg().get('compiler', 'auto'); }
function runTimeout() { return Number(cfg().get('run.timeout', 10)) || 0; }
function checkDelay() { return Math.max(0, Number(cfg().get('check.delayMs', 500)) || 0); }

// ---------------------------------------------------------------- 子进程桥
class Sidecar {
  constructor(context, out) {
    this.context = context;
    this.out = out;
    this.proc = null;
    this.pending = new Map();
    this.nextId = 0;
    this.buf = '';
    this.listeners = [];
    this.starting = null;
    this.version = '';
    this.kb = null;
    this.lastError = '';
  }

  onEvent(fn) { this.listeners.push(fn); }

  pythonCandidates() {
    const list = [];
    // ① 包里自带解释器（首选：学生机经常根本没有 Python）
    const bundled = path.join(this.context.extensionPath, 'runtime', 'python.exe');
    if (fs.existsSync(bundled)) { list.push(bundled); }
    // ② 用户明确指定的
    const configured = (cfg().get('pythonPath') || '').trim();
    if (configured) { list.push(configured); }
    // ③ 环境变量（调试用）
    if (process.env.CTUTOR_PYTHON) { list.push(process.env.CTUTOR_PYTHON); }
    // ④ 常见安装位置
    const local = process.env.LOCALAPPDATA || '';
    const bases = [path.join(local, 'Programs', 'Python'), 'C:\\Python312', 'C:\\Python311',
      'C:\\Python310', 'C:\\Program Files\\Python312', 'C:\\Program Files\\Python311'];
    for (let i = 0; i < bases.length; i += 1) {
      const base = bases[i];
      try {
        if (fs.existsSync(base) && fs.statSync(base).isDirectory()) {
          const kids = fs.readdirSync(base);
          for (let j = 0; j < kids.length; j += 1) {
            if (kids[j].toLowerCase().indexOf('python') === 0) {
              list.push(path.join(base, kids[j], 'python.exe'));
            }
          }
          list.push(path.join(base, 'python.exe'));
        }
      } catch (e) { /* 目录读不到就算了 */ }
    }
    // ⑤ 最后才信 PATH：Windows 商店那个 0 字节的 python.exe 占位符会在这里被筛掉
    list.push('python', 'python3', 'py');
    return list.filter((v, i) => v && list.indexOf(v) === i);
  }

  // 真的跑一下才算数：商店占位符会返回 9009 或直接弹商店
  _usable(exe) {
    if (this._usableCache && Object.prototype.hasOwnProperty.call(this._usableCache, exe)) {
      return this._usableCache[exe];
    }
    if (!this._usableCache) { this._usableCache = {}; }
    let ok = false;
    try {
      const probe = cp.spawnSync(exe, ['-c', 'import sys;sys.stdout.write(str(sys.version_info[0]))'], {
        windowsHide: true, timeout: 8000, encoding: 'utf8',
      });
      ok = !!probe && probe.status === 0 && String(probe.stdout || '').trim() === '3';
    } catch (e) { ok = false; }
    this._usableCache[exe] = ok;
    if (!ok) { this.out.appendLine('[探测] 不能用：' + exe); }
    return ok;
  }

  sidecarPath() {
    return path.join(this.context.extensionPath, 'sidecar', 'sidecar.py');
  }

  ensure() {
    if (this.proc) { return Promise.resolve(); }
    if (this.starting) { return this.starting; }
    this.starting = new Promise((resolve, reject) => {
      const script = this.sidecarPath();
      if (!fs.existsSync(script)) {
        reject(new Error('找不到内核脚本：' + script));
        return;
      }
      const cands = this.pythonCandidates().filter((exe) => this._usable(exe));
      if (!cands.length) {
        reject(new Error('找不到可用的 Python 3。装一个 Python 3，或在设置 ctutor.pythonPath 里填 python.exe 的完整路径。'));
        return;
      }
      const tryOne = (index) => {
        if (index >= cands.length) {
          reject(new Error('Python 都启动失败。请在设置 ctutor.pythonPath 里指定 python.exe 的路径。'));
          return;
        }
        const exe = cands[index];
        let child;
        try {
          child = cp.spawn(exe, ['-X', 'utf8', '-u', script], {
            cwd: this.context.extensionPath,
            env: Object.assign({}, process.env, {
              PYTHONIOENCODING: 'utf-8',
              CTUTOR_CACHE: path.join(this.context.globalStoragePath || os.tmpdir(), 'cache'),
            }),
            windowsHide: true,
          });
        } catch (e) {
          tryOne(index + 1);
          return;
        }
        let settled = false;
        child.on('error', () => {
          if (!settled) { settled = true; tryOne(index + 1); }
        });
        child.stdout.setEncoding('utf8');
        child.stdout.on('data', (chunk) => this._feed(chunk));
        child.stderr.setEncoding('utf8');
        child.stderr.on('data', (chunk) => {
          this.out.appendLine('[内核] ' + chunk.trim());
        });
        child.on('exit', (code) => {
          this.proc = null;
          this.starting = null;
          this.pending.forEach((p) => p.reject(new Error('内核退出（' + code + '）')));
          this.pending.clear();
          this.out.appendLine('[内核] 已退出，代码 ' + code);
        });
        // 等它先冒个泡，确认能跑起来
        const timer = setTimeout(() => {
          if (!settled) { settled = true; this.proc = child; resolve(); }
        }, 1200);
        child.once('spawn', () => {
          clearTimeout(timer);
          if (!settled) { settled = true; this.proc = child; resolve(); }
        });
      };
      tryOne(0);
    }).then(() => this.call('hello', { compiler: compilerPrefer() }).then((res) => {
      this.version = res.version || '';
      this.kb = res.kb || null;
      this.out.appendLine('[内核] 就绪 v' + this.version + ' 编译器=' + (res.compiler || '无') +
        ' 知识库 msvc=' + (res.kb ? res.kb.msvc : '?') + ' gcc=' + (res.kb ? res.kb.gcc : '?'));
      return res;
    })).catch((err) => {
      this.starting = null;
      this.lastError = err.message;
      this.out.appendLine('[错误] ' + err.message);
      throw err;
    });
    return this.starting;
  }

  _feed(chunk) {
    this.buf += chunk;
    let idx;
    while ((idx = this.buf.indexOf('\n')) >= 0) {
      const line = this.buf.slice(0, idx);
      this.buf = this.buf.slice(idx + 1);
      if (!line.trim()) { continue; }
      let msg;
      try { msg = JSON.parse(line); } catch (e) {
        this.out.appendLine('[内核] 坏行：' + line.slice(0, 200));
        continue;
      }
      if (msg.event) {
        this.listeners.forEach((fn) => { try { fn(msg); } catch (e) { /* 忽略 */ } });
        continue;
      }
      const p = this.pending.get(msg.id);
      if (!p) { continue; }
      this.pending.delete(msg.id);
      if (msg.ok) { p.resolve(msg.result || {}); } else { p.reject(new Error(msg.error || '未知错误')); }
    }
  }

  call(method, params) {
    const self = this;
    if (!this.proc) { return this.ensure().then(() => self._send(method, params)); }
    return this._send(method, params);
  }

  _send(method, params) {
    const id = ++this.nextId;
    const payload = JSON.stringify({ id: id, method: method, params: params || {} }) + '\n';
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve: resolve, reject: reject });
      try {
        this.proc.stdin.write(payload, 'utf8');
      } catch (e) {
        this.pending.delete(id);
        this.proc = null;
        reject(e);
        return;
      }
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error('内核响应超时：' + method));
        }
      }, 120000);
    });
  }

  dispose() {
    if (this.proc) {
      try { this.proc.stdin.end(); } catch (e) { /* 忽略 */ }
      try { this.proc.kill(); } catch (e) { /* 忽略 */ }
      this.proc = null;
    }
  }
}

// ---------------------------------------------------------------- 主控制器
class Ctutor {
  constructor(context) {
    this.context = context;
    this.out = vscode.window.createOutputChannel(OUT_NAME);
    this.sidecar = new Sidecar(context, this.out);
    this.reports = new Map();          // uriString -> 报告
    this.diags = vscode.languages.createDiagnosticCollection('ctutor');
    this.timers = new Map();
    this.status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 90);
    this.status.command = 'ctutor.openPanel';
    this.runs = new Map();             // runId -> {pty, alive, buffer}
    this.currentRun = null;
    this.panel = null;
    this.sidecar.onEvent((ev) => this._onEvent(ev));
    this.status.show();
    this._paintStatus();
  }

  // ------------------------------------------------------------ 状态栏
  _paintStatus(extra) {
    const on = cfg().get('enabled', true);
    if (!on) {
      this.status.text = '$(circle-slash) ctutor 已关';
      this.status.tooltip = 'ctutor 的中文讲解与运行功能已关闭。点这里打开开关面板。';
      this.status.backgroundColor = undefined;
      return;
    }
    const rep = this._activeReport();
    const n = rep && rep.diagnostics ? rep.diagnostics.length : 0;
    const chosen = rep && rep.chosen;
    this.status.text = '$(book) ctutor' + (chosen ? '  ' + (chosen.message || '').slice(0, 28) : '');
    const lines = ['编译器：' + (this.sidecar.version ? this.sidecar.version : '…')];
    if (rep) {
      lines.push('本次报错：' + n + ' 条' + (chosen && firstOnly() ? '（只讲 1 条）' : ''));
      if (rep.explanation && rep.explanation.title) { lines.push('结论：' + rep.explanation.title); }
    }
    if (extra) { lines.push(extra); }
    lines.push('点一下打开 ctutor 开关面板');
    this.status.tooltip = lines.join('\n');
  }

  _activeReport() {
    const ed = vscode.window.activeTextEditor;
    if (!ed) { return null; }
    return this.reports.get(ed.document.uri.toString()) || null;
  }

  // ------------------------------------------------------------ 检查
  scheduleCheck(doc, immediate) {
    if (!doc || doc.languageId !== 'c') { return; }
    const key = doc.uri.toString();
    if (this.timers.has(key)) { clearTimeout(this.timers.get(key)); }
    const delay = immediate ? 0 : checkDelay();
    this.timers.set(key, setTimeout(() => {
      this.timers.delete(key);
      this.checkDocument(doc).catch(() => { /* 已在内部提示 */ });
    }, delay));
  }

  async checkDocument(doc) {
    if (!doc || doc.languageId !== 'c') { return null; }
    if (!cfg().get('enabled', true)) {
      this.diags.delete(doc.uri);
      this.reports.delete(doc.uri.toString());
      this._paintStatus();
      return null;
    }
    const want = featureOn('diagnostics') || featureOn('explain') || featureOn('quickFix');
    if (!want) {
      this.diags.delete(doc.uri);
      this.reports.delete(doc.uri.toString());
      this._paintStatus();
      return null;
    }
    let report;
    try {
      report = await this.sidecar.call('check', {
        text: doc.getText(),
        fileName: path.basename(doc.fileName || 'main.c'),
        compiler: compilerPrefer(),
        depth: 5,
        ai: false,
      });
    } catch (e) {
      this.diags.delete(doc.uri);
      const msg = String(e.message || e);
      if (msg.indexOf('没有可用的编译器') >= 0) {
        vscode.window.showWarningMessage('ctutor：本机没找到 C 编译器（MSVC 或 GCC），中文讲解需要它。' +
          '装一个 gcc 后在设置 ctutor.compiler 里指定。');
      } else {
        this.out.appendLine('[检查失败] ' + msg);
      }
      this._paintStatus(msg);
      return null;
    }
    this.reports.set(doc.uri.toString(), report);
    this._publishDiagnostics(doc, report);
    this._paintStatus();
    this.pushPanelState();
    return report;
  }

  _publishDiagnostics(doc, report) {
    if (!featureOn('diagnostics')) { this.diags.delete(doc.uri); return; }
    const all = report.diagnostics || [];
    const chosen = report.chosen;
    const keep = firstOnly() ? (chosen ? [chosen] : []) : all;
    const list = keep.map((d) => {
      const r = d.range || { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } };
      const range = new vscode.Range(r.start.line, r.start.character, r.end.line, r.end.character);
      const sev = d.severity === 'warning' ? vscode.DiagnosticSeverity.Warning
        : (d.severity === 'hint' ? vscode.DiagnosticSeverity.Information : vscode.DiagnosticSeverity.Error);
      const title = (report.explanation && report.explanation.title) || '';
      const short = title ? title + '（' + (d.code || '编译器原文') + '）' : (d.code || '编译器没给错误码');
      const dg = new vscode.Diagnostic(range, short, sev);
      dg.source = 'ctutor';
      dg.code = d.code || '';
      dg.relatedInformation = [new vscode.DiagnosticRelatedInformation(
        new vscode.Location(doc.uri, range), '编译器原文：' + (d.message || ''))];
      return dg;
    });
    this.diags.set(doc.uri, list);
  }

  // ------------------------------------------------------------ provider：悬停
  provideHover(doc, position) {
    if (!featureOn('explain')) { return null; }
    const report = this.reports.get(doc.uri.toString());
    if (!report) { return null; }
    const hit = this._diagAt(report, position);
    if (!hit) { return null; }
    const md = new vscode.MarkdownString();
    md.isTrusted = false;
    const exp = report.explanation || {};
    const isChosen = report.chosen && hit.line === report.chosen.line;
    md.appendMarkdown('### ctutor' + (isChosen ? '：最该先改的一条' : '：另一条报错') + '\n\n');
    if (!exp.matched) {
      md.appendMarkdown('**这条错误本地知识库还没收录。**\n\n');
      md.appendMarkdown('编译器原文：`' + (hit.message || '') + '`\n\n');
      md.appendMarkdown('_点下面的「问 AI」可以让模型补一段讲解（需先配置密钥）。_\n');
      return new vscode.Hover(md);
    }
    const layers = exp.layers || [];
    layers.forEach((layer) => {
      if (layer.title === '定位') { return; }        // 悬停里不需要重复贴源码
      md.appendMarkdown('**' + layer.title + '**\n\n');
      (layer.lines || []).forEach((line) => { md.appendMarkdown(line + '\n\n'); });
    });
    if (hit.message) {
      md.appendMarkdown('---\n\n编译器原文：`' + hit.message + '`\n');
    }
    return new vscode.Hover(md);
  }

  _diagAt(report, position) {
    const list = report.diagnostics || [];
    const at = (d) => {
      const r = d.range;
      if (!r) { return false; }
      return r.start.line === position.line || r.start.line === position.line + 1;
    };
    const chosen = report.chosen;
    if (chosen && at(chosen)) { return chosen; }
    for (let i = 0; i < list.length; i += 1) { if (at(list[i])) { return list[i]; } }
    return null;
  }

  // ------------------------------------------------------------ provider：CodeLens
  provideCodeLenses(doc) {
    if (!featureOn('explain') && !featureOn('quickFix')) { return []; }
    const report = this.reports.get(doc.uri.toString());
    if (!report || !report.chosen) { return []; }
    const ch = report.chosen;
    const line = ch.range ? ch.range.start.line : 0;
    const range = new vscode.Range(line, 0, line, 0);
    const out = [];
    if (featureOn('explain')) {
      out.push(new vscode.CodeLens(range, {
        title: '$(book) ctutor：这一条错在讲什么',
        command: 'ctutor.explainAt',
        arguments: [doc.uri, ch],
      }));
    }
    if (featureOn('quickFix')) {
      out.push(new vscode.CodeLens(range, {
        title: '$(wrench) 改一下（改完自动复检）',
        command: 'ctutor.applyFix',
        arguments: [doc.uri],
      }));
    }
    return out;
  }

  // ------------------------------------------------------------ provider：快速修复
  provideCodeActions(doc) {
    if (!featureOn('quickFix')) { return []; }
    const report = this.reports.get(doc.uri.toString());
    if (!report || !report.chosen) { return []; }
    const ch = report.chosen;
    const r = ch.range || { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } };
    const range = new vscode.Range(r.start.line, r.start.character, r.end.line, r.end.character);
    const title = (report.explanation && report.explanation.title) || '当前错误';
    const action = new vscode.CodeAction('ctutor：修一下「' + title + '」并复检',
      vscode.CodeActionKind.QuickFix);
    action.command = { title: 'ctutor 修复', command: 'ctutor.applyFix', arguments: [doc.uri] };
    action.diagnostics = [];
    action.isPreferred = true;
    return [action];
  }

  // ------------------------------------------------------------ 修复
  async applyFix(uri) {
    const doc = uri ? await vscode.workspace.openTextDocument(uri) : vscode.window.activeTextEditor.document;
    if (!doc) { return; }
    await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: 'ctutor：改一下' },
      async () => {
        let res;
        try {
          res = await this.sidecar.call('fix', {
            text: doc.getText(),
            fileName: path.basename(doc.fileName || 'main.c'),
            compiler: compilerPrefer(),
          });
        } catch (e) {
          vscode.window.showErrorMessage('ctutor：修复失败 —— ' + e.message);
          return;
        }
        if (res.changed && res.verified) {
          const edit = new vscode.WorkspaceEdit();
          const full = new vscode.Range(doc.positionAt(0), doc.positionAt(doc.getText().length));
          edit.replace(doc.uri, full, res.newText);
          await vscode.workspace.applyEdit(edit);
          vscode.window.showInformationMessage('ctutor：' + res.reason);
          await this.checkDocument(doc);
          return;
        }
        const detail = res.reason || '这条错误没有确定性修法。';
        const hint = res.hint ? ' 改法：' + String(res.hint).replace(/\*\*/g, '') : '';
        const pick = await vscode.window.showWarningMessage('ctutor：' + detail + hint, '看看讲解');
        if (pick === '看看讲解') { this.showExplanation(doc, null); }
      });
  }

  // ------------------------------------------------------------ 运行（等输入不计时）
  runActive() {
    if (!featureOn('run')) {
      vscode.window.showInformationMessage('ctutor：运行功能已在开关面板里关闭。');
      return;
    }
    const ed = vscode.window.activeTextEditor;
    if (!ed) { vscode.window.showInformationMessage('ctutor：先打开一个 .c 文件。'); return; }
    if (ed.document.languageId !== 'c') {
      vscode.window.showWarningMessage('ctutor：只支持单文件 C 程序。');
      return;
    }
    const term = vscode.window.createTerminal({
      name: 'ctutor 运行',
      pty: new CtutorPty(this, ed.document),
      isTransient: true,
    });
    term.show();
  }

  stopRun() {
    if (this.currentRun) {
      this.sidecar.call('stop', { runId: this.currentRun }).catch(() => { /* 忽略 */ });
      vscode.window.setStatusBarMessage('ctutor：已请求停止', 3000);
    }
  }

  async startRun(pty, doc) {
    const runId = 'run-' + Date.now();
    this.currentRun = runId;
    this.runs.set(runId, { pty: pty, alive: true, buf: '' });
    pty.write('\x1b[2m ctutor：等输入的时间不算超时；不输入就一直等。要停就按 Ctrl+C。\x1b[0m\r\n\r\n');
    try {
      await this.sidecar.call('run', {
        text: doc.getText(),
        fileName: path.basename(doc.fileName || 'main.c'),
        compiler: compilerPrefer(),
        timeout: runTimeout(),
        runId: runId,
      });
    } catch (e) {
      pty.write('\r\n[ctutor] 启动失败：' + e.message + '\r\n');
      this.runs.delete(runId);
      this.currentRun = null;
    }
  }

  sendLine(runId, text) {
    this.sidecar.call('input', { runId: runId, text: text }).catch(() => { /* 走开就算了 */ });
  }

  _onEvent(ev) {
    if (ev.event === 'ready') { return; }
    const run = this.runs.get(ev.runId);
    if (!run) { return; }
    if (ev.event === 'run.out') {
      run.pty.write((ev.text || '').replace(/\r?\n/g, '\r\n'));
      return;
    }
    if (ev.event === 'run.done') {
      run.alive = false;
      const res = ev.result || {};
      if (!res.compiled) {
        const d = (res.diagnostics || [])[0];
        run.pty.write('\r\n\x1b[31m[ctutor] 没有编译通过，所以没有运行。\x1b[0m\r\n');
        if (d) {
          run.pty.write('  第 ' + d.line + ' 行：' + d.message + '\r\n');
        }
        run.pty.write('  打开时、保存时会自动检查；也可以右键「检查当前文件」看中文讲解。\r\n');
      } else {
        const bits = [];
        bits.push(res.stopped ? '已手动停止' : (res.timedOut ? '超时被终止' : '正常结束'));
        bits.push('退出码 ' + res.returncode);
        if (res.exitReason) { bits.push(res.exitReason); }
        run.pty.write('\r\n\x1b[2m[ctutor] ' + bits.join('　') + '\x1b[0m\r\n');
        (res.notes || []).forEach((n) => run.pty.write('\x1b[2m[ctutor] ' + n + '\x1b[0m\r\n'));
      }
      this.runs.delete(ev.runId);
      if (this.currentRun === ev.runId) { this.currentRun = null; }
    }
  }

  // ------------------------------------------------------------ 面板（开关 + 讲解）
  openPanel() {
    if (this.panel) { this.panel.reveal(); this.pushPanelState(); return; }
    this.panel = vscode.window.createWebviewPanel('ctutor.panel', 'ctutor 开关与讲解',
      vscode.ViewColumn.Beside, { enableScripts: true, retainContextWhenHidden: true });
    this.panel.webview.html = this._panelHtml();
    this.panel.onDidDispose(() => { this.panel = null; }, null, this.context.subscriptions);
    this.panel.webview.onDidReceiveMessage((msg) => this._onPanelMessage(msg),
      null, this.context.subscriptions);
    this.pushPanelState();
  }

  _panelHtml() {
    const file = path.join(this.context.extensionPath, 'media', 'panel.html');
    let html = '<!doctype html><html lang="zh-CN"><body><p>面板资源缺失</p></body></html>';
    try { html = fs.readFileSync(file, 'utf8'); } catch (e) { this.out.appendLine('[面板] ' + e.message); }
    return html;
  }

  pushPanelState() {
    if (!this.panel) { return; }
    const rep = this._activeReport();
    this.panel.webview.postMessage({
      type: 'state',
      state: {
        enabled: cfg().get('enabled', true),
        features: {
          explain: cfg().get('features.explain', true),
          diagnostics: cfg().get('features.diagnostics', true),
          quickFix: cfg().get('features.quickFix', true),
          run: cfg().get('features.run', true),
          ai: cfg().get('features.ai', false),
        },
        firstOnly: firstOnly(),
        compiler: this.sidecar.version || '',
        kb: this.sidecar.kb,
        count: rep && rep.diagnostics ? rep.diagnostics.length : 0,
        explanation: rep ? rep.explanation : null,
        hasFile: !!rep,
      },
    });
  }

  _onPanelMessage(msg) {
    if (!msg || !msg.type) { return; }
    if (msg.type === 'set') {
      const key = msg.key;
      const value = msg.value;
      const target = vscode.ConfigurationTarget.Global;
      const c = cfg();
      if (key === 'enabled' || key === 'explain.firstOnly') {
        c.update(key, value, target).then(() => this._afterConfigChange());
      } else if (key.indexOf('features.') === 0) {
        c.update(key, value, target).then(() => this._afterConfigChange());
      }
      return;
    }
    if (msg.type === 'action') {
      if (msg.name === 'check') { vscode.commands.executeCommand('ctutor.check'); }
      if (msg.name === 'run') { vscode.commands.executeCommand('ctutor.run'); }
      if (msg.name === 'stop') { vscode.commands.executeCommand('ctutor.stop'); }
      if (msg.name === 'fix') {
        const ed = vscode.window.activeTextEditor;
        if (ed) { vscode.commands.executeCommand('ctutor.applyFix', ed.document.uri); }
      }
    }
  }

  async _afterConfigChange() {
    this._paintStatus();
    this.pushPanelState();
    const docs = vscode.workspace.textDocuments.filter((d) => d.languageId === 'c');
    for (let i = 0; i < docs.length; i += 1) {
      // eslint-disable-next-line no-await-in-loop
      await this.checkDocument(docs[i]);
    }
    if (!cfg().get('enabled', true)) { this.diags.clear(); }
  }

  showExplanation(doc, diag) {
    this.openPanel();
    const report = doc ? this.reports.get(doc.uri.toString()) : this._activeReport();
    if (!report) {
      vscode.window.showInformationMessage('ctutor：先让它检查一次（右键「检查当前文件」或保存一下）。');
      return;
    }
    this.panel.webview.postMessage({
      type: 'focusExplain',
      explanation: report.explanation,
      chosen: diag || report.chosen,
      fileName: path.basename(doc ? (doc.fileName || 'main.c') : 'main.c'),
    });
  }

  async aiExplain() {
    if (!featureOn('ai')) {
      vscode.window.showInformationMessage('ctutor：AI 补充讲解已在开关面板里关闭（默认关闭，需配密钥）。');
      return;
    }
    const ed = vscode.window.activeTextEditor;
    if (!ed) { return; }
    const doc = ed.document;
    try {
      const report = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'ctutor：请 AI 补一段讲解' },
        () => this.sidecar.call('check', {
          text: doc.getText(),
          fileName: path.basename(doc.fileName || 'main.c'),
          compiler: compilerPrefer(),
          depth: 5,
          ai: true,
        }));
      this.reports.set(doc.uri.toString(), report);
      this.showExplanation(doc, report.chosen);
      if (report.explanation && report.explanation.ai) {
        vscode.window.showInformationMessage('ctutor：AI 讲解已补上（' +
          (report.explanation.ai_meta || '') + '）');
      } else {
        vscode.window.showWarningMessage('ctutor：AI 没有返回可用结果 —— ' +
          ((report.explanation && report.explanation.ai_meta) || '未配置密钥或网络不通'));
      }
    } catch (e) {
      vscode.window.showErrorMessage('ctutor：' + e.message);
    }
  }

  dispose() {
    this.diags.dispose();
    this.status.dispose();
    this.sidecar.dispose();
    this.out.dispose();
  }
}

// ---------------------------------------------------------------- 运行用的伪终端
class CtutorPty {
  constructor(ctutor, doc) {
    this.ctutor = ctutor;
    this.doc = doc;
    this.line = '';
    this.writeEmitter = new vscode.EventEmitter();
    this.closeEmitter = new vscode.EventEmitter();
    this.onDidWrite = this.writeEmitter.event;
    this.onDidClose = this.closeEmitter.event;
  }

  open() { this.ctutor.startRun(this, this.doc); }

  close() {
    const runId = this.ctutor.currentRun;
    if (runId) { this.ctutor.sidecar.call('stop', { runId: runId }).catch(() => { /* 忽略 */ }); }
  }

  write(text) { this.writeEmitter.fire(text); }

  handleInput(data) {
    for (let i = 0; i < data.length; i += 1) {
      const ch = data[i];
      if (ch === '\r' || ch === '\n') {
        this.write('\r\n');
        const runId = this.ctutor.currentRun;
        if (runId) { this.ctutor.sendLine(runId, this.line + '\n'); }
        this.line = '';
      } else if (ch === '\u007f' || ch === '\b') {
        if (this.line.length > 0) {
          this.line = this.line.slice(0, -1);
          this.write('\b \b');
        }
      } else if (ch === '\u0003') {
        const runId = this.ctutor.currentRun;
        if (runId) { this.ctutor.sidecar.call('stop', { runId: runId }).catch(() => { /* 忽略 */ }); }
        this.write('^C\r\n');
      } else if (ch >= ' ') {
        this.line += ch;
        this.write(ch);        // 程序输出走管道，不会自己回显，所以这里手动回显
      }
    }
  }

  setDimensions() { /* 不需要 */ }
}

// ---------------------------------------------------------------- 注册
function activate(context) {
  const ctutor = new Ctutor(context);

  const languages = ['c'];
  const sel = languages.map((l) => ({ language: l, scheme: '*' }));

  context.subscriptions.push(
    vscode.languages.registerHoverProvider(sel, {
      provideHover: (doc, pos) => ctutor.provideHover(doc, pos),
    }),
    vscode.languages.registerCodeLensProvider(sel, {
      provideCodeLenses: (doc) => ctutor.provideCodeLenses(doc),
    }),
    vscode.languages.registerCodeActionsProvider(sel, {
      provideCodeActions: (doc) => ctutor.provideCodeActions(doc),
    }, { providedCodeActionKinds: [vscode.CodeActionKind.QuickFix] }),
  );

  const cmd = (name, fn) => vscode.commands.registerCommand(name, fn);
  context.subscriptions.push(
    cmd('ctutor.openPanel', () => ctutor.openPanel()),
    cmd('ctutor.check', async () => {
      const ed = vscode.window.activeTextEditor;
      if (!ed) { return; }
      const rep = await ctutor.checkDocument(ed.document);
      if (rep) {
        const n = (rep.diagnostics || []).length;
        vscode.window.setStatusBarMessage(n === 0
          ? 'ctutor：编译通过，没有发现错误'
          : 'ctutor：' + n + ' 条报错，先看最关键的那一条', 4000);
      }
    }),
    cmd('ctutor.run', () => ctutor.runActive()),
    cmd('ctutor.stop', () => ctutor.stopRun()),
    cmd('ctutor.applyFix', (uri) => ctutor.applyFix(uri)),
    cmd('ctutor.aiExplain', () => ctutor.aiExplain()),
    cmd('ctutor.explainAt', (uri, diag) => {
      const ed = vscode.window.activeTextEditor;
      ctutor.showExplanation(ed ? ed.document : null, diag);
    }),
  );

  // 打开/保存/切换时检查
  context.subscriptions.push(
    vscode.workspace.onDidOpenTextDocument((doc) => ctutor.scheduleCheck(doc, false)),
    vscode.workspace.onDidSaveTextDocument((doc) => ctutor.scheduleCheck(doc, true)),
    vscode.workspace.onDidChangeTextDocument((e) => {
      if (cfg().get('check.onType', false)) { ctutor.scheduleCheck(e.document, false); }
    }),
    vscode.window.onDidChangeActiveTextEditor((ed) => {
      ctutor._paintStatus();
      if (ed) { ctutor.scheduleCheck(ed.document, false); }
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration(CFG)) { ctutor._afterConfigChange(); }
    }),
  );

  // 已经打开的文件也检查一遍
  vscode.workspace.textDocuments.forEach((doc) => ctutor.scheduleCheck(doc, false));
  context.subscriptions.push({ dispose: () => ctutor.dispose() });
}

function deactivate() { /* 子进程在 Ctutor.dispose 里收掉 */ }

module.exports = { activate: activate, deactivate: deactivate };