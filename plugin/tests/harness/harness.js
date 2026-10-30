// ctutor 扩展的无 VS Code 冒烟测试。
//
// 为什么要有它：这台机器上没有 node，也开不了无头的 VS Code 扩展宿主。
// 于是把 extension.js 原文取过来，用一个**假的 vscode API + 假内核子进程**跑一遍，
// 至少能证明：激活不炸、provider 返回值对、开关真的能让功能闭麦、运行链路能闭环。
// 真实环境里再出错，只会是 API 细节差异，不会是逻辑错。
(function () {
  const results = [];
  const log = [];
  function check(name, ok, detail) {
    results.push({ name: name, ok: !!ok, detail: ok ? '' : String(detail) });
    log.push((ok ? '[PASS] ' : '[FAIL] ') + name + (ok ? '' : '  -> ' + detail));
    document.getElementById('out').textContent = log.join('\n');
  }

  // ------------------------------------------------------------ 假 vscode
  class EventEmitter {
    constructor() { this.list = []; }
    get event() {
      const self = this;
      return function (fn) { self.list.push(fn); return { dispose: function () {} }; };
    }
    fire(v) { this.list.slice().forEach(function (f) { f(v); }); }
    dispose() {}
  }
  class Position { constructor(line, ch) { this.line = line; this.character = ch; } }
  class Range {
    constructor(a, b, c, d) {
      if (a instanceof Position) { this.start = a; this.end = b; }
      else { this.start = new Position(a, b); this.end = new Position(c, d); }
    }
  }
  class Diagnostic {
    constructor(range, message, severity) {
      this.range = range; this.message = message; this.severity = severity;
    }
  }
  class DiagnosticRelatedInformation {
    constructor(location, message) { this.location = location; this.message = message; }
  }
  class Location { constructor(uri, range) { this.uri = uri; this.range = range; } }
  class CodeLens { constructor(range, command) { this.range = range; this.command = command; } }
  class CodeAction {
    constructor(title, kind) { this.title = title; this.kind = kind; }
  }
  class Hover { constructor(contents) { this.contents = contents; } }
  class MarkdownString {
    constructor(v) { this.value = v || ''; this.isTrusted = false; }
    appendMarkdown(s) { this.value += s; }
  }
  class WorkspaceEdit {
    constructor() { this.edits = []; }
    replace(uri, range, text) { this.edits.push({ uri: uri, range: range, text: text }); }
  }
  class Uri {
    constructor(s) { this.s = s; }
    toString() { return this.s; }
  }

  const store = {
    'enabled': true,
    'features.explain': true,
    'features.diagnostics': true,
    'features.quickFix': true,
    'features.run': true,
    'features.ai': false,
    'explain.firstOnly': true,
    'compiler': 'auto',
    'pythonPath': '',
    'run.timeout': 10,
    'check.delayMs': 0,
    'check.onType': false,
  };
  const updates = [];
  const configuration = {
    get: function (key, def) { return (key in store) ? store[key] : def; },
    update: function (key, value) {
      updates.push({ key: key, value: value });
      store[key] = value;
      return Promise.resolve();
    },
  };

  const commands = {};
  const providers = { hover: [], lens: [], actions: [] };
  const collections = [];
  const statusBar = { text: '', tooltip: '', show: function () {}, dispose: function () {} };
  const terminals = [];
  const messages = [];
  const appliedEdits = [];
  let activeEditor = null;

  function makeCollection(name) {
    const c = {
      name: name, byUri: {}, set: function (uri, list) { this.byUri[uri.toString()] = list; },
      delete: function (uri) { delete this.byUri[uri.toString()]; },
      clear: function () { this.byUri = {}; }, dispose: function () {},
    };
    collections.push(c);
    return c;
  }

  const vscode = {
    EventEmitter: EventEmitter,
    Position: Position, Range: Range, Diagnostic: Diagnostic,
    DiagnosticRelatedInformation: DiagnosticRelatedInformation, Location: Location,
    CodeLens: CodeLens, CodeAction: CodeAction, Hover: Hover,
    MarkdownString: MarkdownString, WorkspaceEdit: WorkspaceEdit, Uri: Uri,
    DiagnosticSeverity: { Error: 0, Warning: 1, Information: 2, Hint: 3 },
    CodeActionKind: { QuickFix: { value: 'quickfix' } },
    StatusBarAlignment: { Left: 1, Right: 2 },
    ProgressLocation: { Notification: 15 },
    ViewColumn: { Beside: -2 },
    ConfigurationTarget: { Global: 1, Workspace: 2 },
    workspace: {
      getConfiguration: function () { return configuration; },
      onDidOpenTextDocument: function () { return { dispose: function () {} }; },
      onDidSaveTextDocument: function () { return { dispose: function () {} }; },
      onDidChangeTextDocument: function () { return { dispose: function () {} }; },
      onDidChangeConfiguration: function () { return { dispose: function () {} }; },
      textDocuments: [],
      openTextDocument: function () { return Promise.resolve(activeEditor.document); },
      applyEdit: function (edit) { appliedEdits.push(edit); return Promise.resolve(true); },
    },
    window: {
      activeTextEditor: null,
      createOutputChannel: function () {
        return { appendLine: function () {}, dispose: function () {}, show: function () {} };
      },
      createStatusBarItem: function () { return statusBar; },
      setStatusBarMessage: function (m) { messages.push(m); },
      showInformationMessage: function (m) { messages.push(m); return Promise.resolve(undefined); },
      showWarningMessage: function (m) { messages.push(m); return Promise.resolve(undefined); },
      showErrorMessage: function (m) { messages.push(m); return Promise.resolve(undefined); },
      withProgress: function (_opts, fn) { return fn(); },
      createTerminal: function (opts) { terminals.push(opts); return { show: function () {} }; },
      onDidChangeActiveTextEditor: function () { return { dispose: function () {} }; },
      createWebviewPanel: function () {
        const panel = {
          webview: {
            html: '', posted: [],
            postMessage: function (m) { this.posted.push(m); return Promise.resolve(true); },
            onDidReceiveMessage: function () { return { dispose: function () {} }; },
          },
          reveal: function () {}, onDidDispose: function () { return { dispose: function () {} }; },
          dispose: function () {},
        };
        vscode.__panel = panel;
        return panel;
      },
    },
    languages: {
      createDiagnosticCollection: makeCollection,
      registerHoverProvider: function (_sel, p) { providers.hover.push(p); return { dispose: function () {} }; },
      registerCodeLensProvider: function (_sel, p) { providers.lens.push(p); return { dispose: function () {} }; },
      registerCodeActionsProvider: function (_sel, p) { providers.actions.push(p); return { dispose: function () {} }; },
    },
    commands: {
      registerCommand: function (name, fn) { commands[name] = fn; return { dispose: function () {} }; },
      executeCommand: function (name) {
        const args = Array.prototype.slice.call(arguments, 1);
        if (commands[name]) { return Promise.resolve(commands[name].apply(null, args)); }
        return Promise.resolve(undefined);
      },
    },
  };

  // ------------------------------------------------------------ 假内核子进程
  const sent = [];
  const REPLY = {
    version: '0.6.3', python: '3.12.0', compiler: 'gcc', kb: { msvc: 61, gcc: 53 },
  };
  function reportFor(text) {
    const broken = text.indexOf('int x = 10') >= 0 && text.indexOf('int x = 10;') < 0;
    if (!broken) {
      return { diagnostics: [], chosen: null, explanation: null, returncode: 0, fileName: 'main.c' };
    }
    const mk = function (line, ch, code, message, level) {
      return {
        compiler: 'gcc', code: code, level: level || 'error', message: message,
        file: 'main.c', line: line, column: ch, origin: 'text', col_unit: 'display',
        severity: (level === 'warning' ? 'warning' : 'error'),
        range: { start: { line: line - 1, character: ch - 1 }, end: { line: line - 1, character: ch + 3 } },
      };
    };
    const d1 = mk(4, 5, '', "expected ',' or ';' before 'return'");
    const d2 = mk(3, 9, '-Wunused-variable', "unused variable 'x'", 'warning');
    return {
      diagnostics: [d1, d2], chosen: d1, returncode: 1, fileName: 'main.c',
      explanation: {
        matched: true, title: '缺少分号',
        layers: [
          { title: '结论', lines: ['缺少分号', '上一条语句没有以分号结尾。'] },
          { title: '定位', lines: ['位置：第 4 行，第 9 列'] },
          { title: '原因', lines: ['C 语言规定每条语句必须以分号 ; 结束。'] },
          { title: '改法', lines: ['看报错行的上一行末尾，补上英文分号 ;。'] },
          { title: '知识点', lines: ['分号是语句结束符。'] },
        ],
        rendered: '（略）', ai: null, ai_meta: '',
      },
    };
  }

  function makeChild() {
    const h = { 'stdout:data': [], 'stderr:data': [], spawn: [], exit: [], error: [] };
    let currentText = '';
    function emit(obj) {
      const line = JSON.stringify(obj) + '\n';
      h['stdout:data'].forEach(function (f) { f(line); });
    }
    function handle(payload) {
      const req = JSON.parse(payload);
      sent.push(req);
      if (req.method === 'hello') {
        emit({ id: req.id, ok: true, result: REPLY });
        return;
      }
      if (req.method === 'check') {
        currentText = req.params.text;
        emit({ id: req.id, ok: true, result: reportFor(req.params.text) });
        return;
      }
      if (req.method === 'fix') {
        emit({
          id: req.id, ok: true,
          result: { changed: false, verified: false, reason: '测试桩：这条错误没有确定性修法', hint: '看第 4 行' },
        });
        return;
      }
      if (req.method === 'run') {
        emit({ id: req.id, ok: true, result: { runId: req.params.runId } });
        setTimeout(function () {
          emit({ event: 'run.out', runId: req.params.runId, text: '请输入 n: ' });
        }, 10);
        // 程序回显"你输入了 7"要等用户真的敲了回车才出现：这里跟着第一次 input 走
        setTimeout(function () {
          emit({
            event: 'run.done', runId: req.params.runId,
            result: { compiled: true, returncode: 0, notes: [], timedOut: false, stopped: false },
          });
        }, 400);
        return;
      }
      if (req.method === 'input') {
        emit({ id: req.id, ok: true, result: { delivered: true } });
        emit({
          event: 'run.out', runId: req.params.runId,
          text: '你输入了 ' + String(req.params.text || '').trim() + '\n',
        });
        return;
      }
      emit({ id: req.id, ok: true, result: { stopped: true } });
    }
    const child = {
      stdout: { setEncoding: function () {}, on: function (ev, fn) { h['stdout:' + ev].push(fn); } },
      stderr: { setEncoding: function () {}, on: function (ev, fn) { h['stderr:' + ev].push(fn); } },
      stdin: {
        write: function (payload) { handle(payload); },
        end: function () {},
      },
      kill: function () {},
      on: function (ev, fn) { (h[ev] = h[ev] || []).push(fn); },
      once: function (ev, fn) { (h[ev] = h[ev] || []).push(fn); },
    };
    setTimeout(function () {
      emit({ event: 'ready', result: { version: '0.6.3', python: '3.12.0' } });
      (h.spawn || []).forEach(function (f) { f(); });
    }, 0);
    return child;
  }

  const cpStub = { spawn: function () { return makeChild(); } };
  const fsStub = {
    existsSync: function () { return true; },
    readFileSync: function () { return '<html></html>'; },
  };
  const pathStub = {
    join: function () { return Array.prototype.join.call(arguments, '/'); },
    basename: function (p) { return String(p).split('/').pop(); },
    dirname: function (p) { return String(p).split('/').slice(0, -1).join('/'); },
  };
  const osStub = { tmpdir: function () { return '/tmp'; } };

  function makeRequire(name) {
    if (name === 'vscode') { return vscode; }
    if (name === 'child_process') { return cpStub; }
    if (name === 'fs') { return fsStub; }
    if (name === 'path') { return pathStub; }
    if (name === 'os') { return osStub; }
    throw new Error('未预料的 require: ' + name);
  }

  function fakeDoc(text) {
    return {
      uri: new Uri('file:///t/main.c'),
      fileName: 'C:/t/main.c',
      languageId: 'c',
      getText: function () { return text; },
      positionAt: function (offset) {
        const before = text.slice(0, offset).split('\n');
        return new Position(before.length - 1, before[before.length - 1].length);
      },
    };
  }

  // ------------------------------------------------------------ 跑测试
  fetch('/extension.js').then(function (r) { return r.text(); }).then(function (src) {
    const mod = { exports: {} };
    const fn = new Function('require', 'module', 'exports', 'process', 'console', src);
    fn(makeRequire, mod, mod.exports, { env: {}, platform: 'win32' },
      { log: function () {}, error: function () {}, warn: function () {} });
    const ext = mod.exports;
    check('extension.js 能被 require 且导出 activate', typeof ext.activate === 'function', typeof ext.activate);

    const context = {
      extensionPath: 'C:/ext', globalStoragePath: 'C:/store', subscriptions: [],
    };
    ext.activate(context);
    check('activate 不抛异常，且注册了订阅', context.subscriptions.length > 0, context.subscriptions.length);
    check('注册了 7 个命令', Object.keys(commands).length === 7, Object.keys(commands).join(','));
    check('注册了 hover / codelens / codeactions 三个 provider',
      providers.hover.length === 1 && providers.lens.length === 1 && providers.actions.length === 1,
      providers.hover.length + '/' + providers.lens.length + '/' + providers.actions.length);
    check('状态栏已显示', !!statusBar.text, statusBar.text);

    const TEXT = 'int main(void) {\n    int x = 10\n    return 0;\n}\n';
    const doc = fakeDoc(TEXT);
    activeEditor = { document: doc };
    vscode.window.activeTextEditor = activeEditor;

    return Promise.resolve(commands['ctutor.check']()).then(function () {
      const col = collections[0];
      const diags = col.byUri[doc.uri.toString()] || [];
      check('检查后只画 1 条波浪线（默认只讲最关键的一条）', diags.length === 1, diags.length);
      check('波浪线的文字是中文结论，不是编译器原文',
        diags.length === 1 && diags[0].message.indexOf('缺少分号') >= 0, diags[0] && diags[0].message);
      check('诊断的 source 标成 ctutor', diags[0] && diags[0].source === 'ctutor', diags[0] && diags[0].source);

      // hover
      const hover = providers.hover[0].provideHover(doc, new Position(3, 5));
      const md = hover && hover.contents ? hover.contents.value : '';
      check('悬停在报错行能拿到讲解', !!hover, 'null');
      check('讲解含"改法"这一层', md.indexOf('改法') >= 0, md.slice(0, 80));
      check('讲解含结论标题', md.indexOf('缺少分号') >= 0, md.slice(0, 40));
      const hover2 = providers.hover[0].provideHover(doc, new Position(0, 0));
      check('悬停在无关行不弹讲解', hover2 === null, !!hover2);

      // codelens
      const lens = providers.lens[0].provideCodeLenses(doc);
      check('报错行上方有 2 个 CodeLens（看讲解 + 改一下）', lens.length === 2, lens.length);
      check('CodeLens 的命令名正确',
        lens.length === 2 && lens[0].command.command === 'ctutor.explainAt'
        && lens[1].command.command === 'ctutor.applyFix',
        lens.map(function (l) { return l.command.command; }).join(','));

      // codeaction
      const actions = providers.actions[0].provideCodeActions(doc);
      check('提供 1 个快速修复动作', actions.length === 1, actions.length);
      check('动作标题带中文结论', actions.length === 1 && actions[0].title.indexOf('缺少分号') >= 0,
        actions[0] && actions[0].title);
    }).then(function () {
      // 关掉"只讲一条"后应该全显示
      store['explain.firstOnly'] = false;
      return Promise.resolve(commands['ctutor.check']()).then(function () {
        const diags = collections[0].byUri[doc.uri.toString()] || [];
        check('关掉降噪后显示全部 2 条诊断', diags.length === 2, diags.length);
        store['explain.firstOnly'] = true;
        store['features.quickFix'] = false;
        return Promise.resolve(commands['ctutor.check']());
      });
    }).then(function () {
      const lens = providers.lens[0].provideCodeLenses(doc);
      const actions = providers.actions[0].provideCodeActions(doc);
      check('关掉"改一下"后 CodeLens 只剩 1 个', lens.length === 1, lens.length);
      check('关掉"改一下"后不再提供快速修复', actions.length === 0, actions.length);
      store['features.quickFix'] = true;
      // 关掉讲解
      store['features.explain'] = false;
      const hover = providers.hover[0].provideHover(doc, new Position(3, 5));
      check('关掉"中文讲解"后悬停不再弹讲解', hover === null, !!hover);
      store['features.explain'] = true;
      return Promise.resolve();
    }).then(function () {
      // 关掉波浪线
      store['features.diagnostics'] = false;
      return Promise.resolve(commands['ctutor.check']()).then(function () {
        const diags = collections[0].byUri[doc.uri.toString()] || [];
        check('关掉"自己画波浪线"后诊断被清空', diags.length === 0, diags.length);
        store['features.diagnostics'] = true;
        return Promise.resolve(commands['ctutor.check']());
      });
    }).then(function () {
      // 总开关
      store['enabled'] = false;
      return Promise.resolve(commands['ctutor.check']()).then(function () {
        const diags = collections[0].byUri[doc.uri.toString()] || [];
        check('总开关关闭后不产生诊断', diags.length === 0, diags.length);
        check('总开关关闭后状态栏显示"已关"', statusBar.text.indexOf('已关') >= 0, statusBar.text);
        store['enabled'] = true;
        return Promise.resolve(commands['ctutor.check']());
      });
    }).then(function () {
      // 运行：创建终端 → pty.open → 假内核推 out/done
      store['features.run'] = true;
      commands['ctutor.run']();
      check('运行会创建终端', terminals.length === 1, terminals.length);
      const pty = terminals[0] && terminals[0].pty;
      check('终端用的是 Pseudoterminal（ctutor 自己喂数据）', !!pty && typeof pty.handleInput === 'function',
        typeof pty);
      const written = [];
      pty.onDidWrite(function (s) { written.push(s); });
      pty.open();
      return new Promise(function (r) { setTimeout(r, 60); }).then(function () {
        const out = written.join('');
        check('提示语在程序结束前就到达终端（真·实时，不是等结束了才一起吐）',
          out.indexOf('请输入 n:') >= 0, out.slice(0, 120));
        written.length = 0;
        pty.handleInput('1');
        pty.handleInput('2');
        check('本地回显：敲的字符要显示出来（管道不会自己回显）',
          written.join('') === '12', JSON.stringify(written.join('')));
        const before = sent.filter(function (r) { return r.method === 'input'; }).length;
        written.length = 0;
        pty.handleInput('\r');
        const inputCalls = sent.filter(function (r) { return r.method === 'input'; });
        check('回车后把整行送给内核（带换行）',
          inputCalls.length === before + 1
          && inputCalls[inputCalls.length - 1].params.text === '12\n',
          JSON.stringify(inputCalls[inputCalls.length - 1] && inputCalls[inputCalls.length - 1].params));
        check('回车也会回显换行', written.join('').indexOf('\r\n') >= 0, JSON.stringify(written.join('')));
        check('程序收到了我们送的输入（输出里能看到）',
          written.join('').indexOf('你输入了 12') >= 0, written.join(''));
        return new Promise(function (r) { setTimeout(r, 500); });
      }).then(function () {
        const out = written.join('');
        check('程序结束后打印退出码与用时', out.indexOf('退出码 0') >= 0, out.slice(-160));
        check('结束后再敲键不会把数据发给已结束的运行',
          sent.filter(function (r) { return r.method === 'stop'; }).length === 0, 'unexpected stop');
      });
    }).then(function () {
      // 面板
      commands['ctutor.openPanel']();
      const panel = vscode.__panel;
      check('面板被创建', !!panel, 'null');
      const state = panel.webview.posted.filter(function (m) { return m.type === 'state'; });
      check('面板收到状态（含各功能开关）',
        state.length >= 1 && state[state.length - 1].state.features
        && 'explain' in state[state.length - 1].state.features, state.length);
      const s = state[state.length - 1].state;
      check('面板状态里带出知识库条数（61/53）',
        s.kb && s.kb.msvc === 61 && s.kb.gcc === 53, JSON.stringify(s.kb));
      check('面板状态里带出"只讲一条"开关', s.firstOnly === true, s.firstOnly);
    }).then(function () {
      // 修复：没有确定性修法时要给出解释而不是静默
      return Promise.resolve(commands['ctutor.applyFix'](doc.uri)).then(function () {
        const last = messages[messages.length - 1] || '';
        check('无法确定性修复时会提示原因（而不是静默失败）',
          last.indexOf('没有确定性修法') >= 0, last.slice(0, 120));
      });
    }).then(function () {
      window.__RESULT = {
        total: results.length,
        failed: results.filter(function (r) { return !r.ok; }).length,
        results: results,
        log: log,
      };
      document.title = 'done ' + window.__RESULT.failed;
    });
  }).catch(function (e) {
    window.__RESULT = {
      total: results.length, failed: results.length + 1,
      results: results, crashed: String(e && e.stack ? e.stack : e), log: log,
    };
    document.getElementById('out').textContent = 'CRASH: ' + String(e && e.stack ? e.stack : e);
  });
}());