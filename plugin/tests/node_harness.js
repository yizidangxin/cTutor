// ctutor 扩展的真·端到端测试：真 Node（VS Code 自带的 Electron）、真 Python、真 gcc。
//
// 跑法（Windows，不需要装 node）：
//   $env:ELECTRON_RUN_AS_NODE='1'
//   & 'E:\VS Code\Microsoft VS Code\Code.exe' plugin\tests\node_harness.js <装好的扩展目录>
//
// 它做的事：用 module._load 钩子把 require('vscode') 换成桩，
// 然后加载真正的 extension.js —— 于是 child_process / fs / path 全是真的，
// sidecar 会被真的拉起来、真的调 gcc、真的报错、真的修、真的跑。
'use strict';

const path = require('path');
const fs = require('fs');
const Module = require('module');

const EXT_DIR = process.argv[2] || path.join(__dirname, '..');
const results = [];
function check(name, ok, detail) {
  results.push({ name: name, ok: !!ok, detail: ok ? '' : String(detail === undefined ? '' : detail) });
}
function finish() {
  const failed = results.filter((r) => !r.ok);
  console.log('\n=== 结果 ===');
  results.forEach((r) => console.log((r.ok ? '[PASS] ' : '[FAIL] ') + r.name + (r.ok ? '' : '  -> ' + r.detail)));
  console.log('\n共 ' + results.length + ' 项，失败 ' + failed.length);
  console.log('HARNESS-RESULT ' + JSON.stringify({ total: results.length, failed: failed.length }));
  process.exit(failed.length ? 1 : 0);
}
function fail(err) {
  console.log('CRASH: ' + (err && err.stack ? err.stack : err));
  results.push({ name: '未捕获异常', ok: false, detail: String(err && err.message ? err.message : err) });
  finish();
}

// ------------------------------------------------------------------ 假 vscode
class EventEmitter {
  constructor() { this.list = []; }
  get event() {
    const self = this;
    return (fn) => { self.list.push(fn); return { dispose() {} }; };
  }
  fire(v) { this.list.slice().forEach((f) => f(v)); }
  dispose() {}
}
class Position { constructor(line, ch) { this.line = line; this.character = ch; } }
class Range {
  constructor(a, b, c, d) {
    if (a instanceof Position) { this.start = a; this.end = b; } else { this.start = new Position(a, b); this.end = new Position(c, d); }
  }
}
class Diagnostic { constructor(range, message, severity) { this.range = range; this.message = message; this.severity = severity; } }
class DiagnosticRelatedInformation { constructor(location, message) { this.location = location; this.message = message; } }
class Location { constructor(uri, range) { this.uri = uri; this.range = range; } }
class CodeLens { constructor(range, command) { this.range = range; this.command = command; } }
class CodeAction { constructor(title, kind) { this.title = title; this.kind = kind; } }
class Hover { constructor(contents) { this.contents = contents; } }
class MarkdownString {
  constructor(v) { this.value = v || ''; this.isTrusted = false; }
  appendMarkdown(s) { this.value += s; }
}
class WorkspaceEdit {
  constructor() { this.edits = []; }
  replace(uri, range, text) { this.edits.push({ uri, range, text }); }
}
class Uri {
  constructor(s) { this.s = s; }
  toString() { return this.s; }
}

const store = {
  enabled: true, 'features.explain': true, 'features.diagnostics': true,
  'features.quickFix': true, 'features.run': true, 'features.ai': false,
  'explain.firstOnly': true, compiler: 'auto', pythonPath: '',
  'run.timeout': 10, 'check.delayMs': 0, 'check.onType': false,
};
const configuration = {
  get: (k, d) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : d),
  update: (k, v) => { store[k] = v; return Promise.resolve(); },
};
const commands = {};
const providers = { hover: [], lens: [], actions: [] };
const collections = [];
const statusBar = { text: '', tooltip: '', show() {}, dispose() {}, backgroundColor: undefined };
const terminals = [];
const messages = [];
const panelRef = { current: null };
let activeEditor = null;

const vscode = {
  EventEmitter, Position, Range, Diagnostic, DiagnosticRelatedInformation, Location,
  CodeLens, CodeAction, Hover, MarkdownString, WorkspaceEdit, Uri,
  DiagnosticSeverity: { Error: 0, Warning: 1, Information: 2, Hint: 3 },
  CodeActionKind: { QuickFix: { value: 'quickfix' } },
  StatusBarAlignment: { Left: 1, Right: 2 },
  ProgressLocation: { Notification: 15 },
  ViewColumn: { Beside: -2 },
  ConfigurationTarget: { Global: 1, Workspace: 2 },
  workspace: {
    getConfiguration: () => configuration,
    onDidOpenTextDocument: () => ({ dispose() {} }),
    onDidSaveTextDocument: () => ({ dispose() {} }),
    onDidChangeTextDocument: () => ({ dispose() {} }),
    onDidChangeConfiguration: () => ({ dispose() {} }),
    textDocuments: [],
    openTextDocument: () => Promise.resolve(activeEditor.document),
    applyEdit: (edit) => { applied.push(edit); return Promise.resolve(true); },
  },
  window: {
    activeTextEditor: null,
    createOutputChannel: () => ({
      appendLine: (s) => { if (process.env.CTUTOR_SHOW_LOG) { console.log('[out] ' + s); } },
      append() {}, show() {}, dispose() {},
    }),
    createStatusBarItem: () => statusBar,
    setStatusBarMessage: (m) => messages.push(m),
    showInformationMessage: (m) => { messages.push(m); return Promise.resolve(undefined); },
    showWarningMessage: (m) => { messages.push(m); return Promise.resolve(undefined); },
    showErrorMessage: (m) => { messages.push(m); return Promise.resolve(undefined); },
    withProgress: (_o, fn) => fn(),
    createTerminal: (opts) => { terminals.push(opts); return { show() {}, dispose() {} }; },
    onDidChangeActiveTextEditor: () => ({ dispose() {} }),
    createWebviewPanel: () => {
      const panel = {
        webview: {
          html: '', posted: [],
          postMessage(m) { this.posted.push(m); return Promise.resolve(true); },
          onDidReceiveMessage: () => ({ dispose() {} }),
        },
        reveal() {}, onDidDispose: () => ({ dispose() {} }), dispose() {},
      };
      panelRef.current = panel;
      return panel;
    },
  },
  languages: {
    createDiagnosticCollection: () => {
      const c = {
        byUri: {}, set(u, l) { this.byUri[u.toString()] = l; },
        delete(u) { delete this.byUri[u.toString()]; }, clear() { this.byUri = {}; }, dispose() {},
      };
      collections.push(c);
      return c;
    },
    registerHoverProvider: (_s, p) => { providers.hover.push(p); return { dispose() {} }; },
    registerCodeLensProvider: (_s, p) => { providers.lens.push(p); return { dispose() {} }; },
    registerCodeActionsProvider: (_s, p) => { providers.actions.push(p); return { dispose() {} }; },
  },
  commands: {
    registerCommand: (n, fn) => { commands[n] = fn; return { dispose() {} }; },
    executeCommand: (n) => {
      const args = Array.prototype.slice.call(arguments, 1);
      return commands[n] ? Promise.resolve(commands[n].apply(null, args)) : Promise.resolve(undefined);
    },
  },
};
const applied = [];

// ------------------------------------------------------------------ 加载真扩展
const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === 'vscode') { return vscode; }
  return origLoad.apply(this, arguments);
};

const extMain = path.join(EXT_DIR, 'extension.js');
if (!fs.existsSync(extMain)) {
  console.log('找不到 extension.js：' + extMain);
  process.exit(2);
}
const ext = origLoad(extMain, null, false);

function fakeDoc(text) {
  return {
    uri: new Uri('file:///c%3A/ctutor-demo/main.c'),
    fileName: path.join(__dirname, 'main.c'),
    languageId: 'c',
    getText: () => text,
    positionAt: (offset) => {
      const before = text.slice(0, offset).split('\n');
      return new Position(before.length - 1, before[before.length - 1].length);
    },
  };
}

const BROKEN = '#include <stdio.h>\n\nint main(void) {\n    // 故意少一个分号\n'
  + '    int x = 10\n    printf("%d\\n", x);\n    return 0;\n}\n';
const INTERACTIVE = '#include <stdio.h>\nint main(void) {\n'
  + '    int n = 0;\n    printf("请输入 n: ");\n    fflush(stdout);\n'
  + '    if (scanf("%d", &n) != 1) return 1;\n    printf("你输入了 %d\\n", n);\n    return 0;\n}\n';

const context = { extensionPath: EXT_DIR, globalStoragePath: path.join(EXT_DIR, '.test-store'), subscriptions: [] };

(async function main() {
  const t0 = Date.now();
  ext.activate(context);
  check('activate 不抛异常', context.subscriptions.length > 0, context.subscriptions.length);
  check('注册了 7 个命令', Object.keys(commands).length === 7, Object.keys(commands).join(','));
  check('三个 provider 都注册了',
    providers.hover.length === 1 && providers.lens.length === 1 && providers.actions.length === 1,
    providers.hover.length + '/' + providers.lens.length + '/' + providers.actions.length);

  const doc = fakeDoc(BROKEN);
  activeEditor = { document: doc };
  vscode.window.activeTextEditor = activeEditor;

  // ---- 真编译 ----
  const tCheck = Date.now();
  await commands['ctutor.check']();
  const checkMs = Date.now() - tCheck;
  const diags = collections[0].byUri[doc.uri.toString()] || [];
  check('检查真的跑通了（真 python + 真 gcc），耗时 ' + checkMs + 'ms', checkMs > 0 && diags.length === 1,
    'diags=' + diags.length);
  const d0 = diags[0] || {};
  check('诊断文字是中文结论而不是 expected ...',
    /[\u4e00-\u9fa5]/.test(d0.message || '') && (d0.message || '').indexOf('expected') < 0, d0.message);
  check('诊断 source 是 ctutor', d0.source === 'ctutor', d0.source);
  check('诊断有合法 range（0 基行号，落在文件内）',
    d0.range && d0.range.start.line >= 0 && d0.range.start.line < BROKEN.split('\n').length, JSON.stringify(d0.range));
  check('只讲一条：报错 1 条时不会刷出一堆波浪线', diags.length === 1, diags.length);

  // ---- 真悬停 ----
  const pos = d0.range ? new Position(d0.range.start.line, d0.range.start.character) : new Position(4, 5);
  const hover = providers.hover[0].provideHover(doc, pos);
  const md = hover && hover.contents ? hover.contents.value : '';
  check('悬停标题是中文结论', /[\u4e00-\u9fa5]/.test(String(d0.message || '')), d0.message);
  check('悬停里五层讲解齐全（结论/定位/原因/改法/知识点）',
    md.indexOf('结论') >= 0 && md.indexOf('原因') >= 0 && md.indexOf('改法') >= 0 && md.indexOf('知识点') >= 0,
    md.slice(0, 120));
  check('悬停里贴出了编译器原文（可选对照）', md.indexOf('编译器原文') >= 0, md.slice(-160));

  // ---- CodeLens / CodeAction ----
  const lens = providers.lens[0].provideCodeLenses(doc);
  check('报错行有 2 个 CodeLens', lens.length === 2, lens.length);
  const acts = providers.actions[0].provideCodeActions(doc);
  check('提供 1 个快速修复', acts.length === 1, acts.length);

  // ---- 真修复（改完必须真的能编译过）----
  const tFix = Date.now();
  await commands['ctutor.applyFix'](doc.uri);
  const fixMs = Date.now() - tFix;
  check('修复走通了（真 gcc 复编验证），耗时 ' + fixMs + 'ms', applied.length === 1, applied.length);
  let fixedText = '';
  if (applied.length === 1 && applied[0].edits.length === 1) { fixedText = applied[0].edits[0].text; }
  check('修好的文本确实补上了分号', /int x = 10;/.test(fixedText), JSON.stringify(fixedText.slice(0, 80)));
  check('修好的文本保留了原来那一行注释', fixedText.indexOf('故意少一个分号') >= 0, '注释丢了');
  check('修完给出中文提示', /[\u4e00-\u9fa5]/.test(String(messages[messages.length - 1] || '')),
    messages[messages.length - 1]);

  // ---- 真运行 + 真输入 ----
  const doc2 = fakeDoc(INTERACTIVE);
  activeEditor = { document: doc2 };
  vscode.window.activeTextEditor = activeEditor;
  terminals.length = 0;
  await commands['ctutor.check']();          // 先让它知道这份文件能编译
  commands['ctutor.run']();
  check('运行会开一个终端', terminals.length === 1, terminals.length);
  const pty = terminals[0].pty;
  const written = [];
  pty.onDidWrite((s) => written.push(s));
  const tRun = Date.now();
  pty.open();
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    if (written.join('').indexOf('请输入') >= 0) { break; }
    await new Promise((r) => setTimeout(r, 100));
  }
  const gotPromptMs = Date.now() - tRun;
  check('运行起来后提示语实时到达（' + gotPromptMs + 'ms，没等程序结束）',
    written.join('').indexOf('请输入') >= 0, written.join('').slice(0, 200));

  pty.handleInput('7');
  pty.handleInput('\r');
  const deadline2 = Date.now() + 90000;
  while (Date.now() < deadline2) {
    const all = written.join('');
    if (all.indexOf('你输入了 7') >= 0 && all.indexOf('退出码') >= 0) { break; }
    await new Promise((r) => setTimeout(r, 100));
  }
  const finalOut = written.join('');
  check('程序真的收到了我们敲的输入', finalOut.indexOf('你输入了 7') >= 0, finalOut.slice(-200));
  check('结束后打印退出码', finalOut.indexOf('退出码 0') >= 0, finalOut.slice(-200));

  // ---- 面板状态 ----
  await commands['ctutor.openPanel']();
  const panel = panelRef.current;
  const states = panel ? panel.webview.posted.filter((m) => m.type === 'state') : [];
  check('面板拿到状态', states.length >= 1, states.length);
  if (states.length) {
    const s = states[states.length - 1].state;
    check('面板状态带出真实知识库条数（msvc 61 / gcc 53）', s.kb && s.kb.msvc === 61 && s.kb.gcc === 53,
      JSON.stringify(s.kb));
    check('面板状态带出编译器版本', !!s.compiler, s.compiler);
    check('面板里六个开关都有值',
      typeof s.enabled === 'boolean' && typeof s.features.explain === 'boolean'
      && typeof s.features.diagnostics === 'boolean' && typeof s.features.quickFix === 'boolean'
      && typeof s.features.run === 'boolean' && typeof s.features.ai === 'boolean',
      JSON.stringify(s.features));
  }

  // ---- 总开关闭麦 ----
  await commands['ctutor.check']();
  const beforeOff = (collections[0].byUri[doc2.uri.toString()] || []).length;
  store.enabled = false;
  await commands['ctutor.check']();
  const afterOff = (collections[0].byUri[doc2.uri.toString()] || []).length;
  check('总开关关闭后不再产生诊断', afterOff === 0, beforeOff + ' -> ' + afterOff);
  check('状态栏显示已关', statusBar.text.indexOf('已关') >= 0, statusBar.text);
  store.enabled = true;

  const totalMs = Date.now() - t0;
  console.log('\n耗时：检查 ' + checkMs + 'ms，修复 ' + fixMs + 'ms，运行到提示 ' + gotPromptMs + 'ms，总计 ' + totalMs + 'ms');
  process.env.CTUTOR_SHOW_LOG = '1';
  finish();
})().catch(fail);