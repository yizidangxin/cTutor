# ctutor for VS Code

> **红色的字不再是 `expected ';' before 'int'`，而是"上一条语句没写分号，看第 4 行末尾"。**

这个扩展**只做 VS Code 自己不会的四件事**。补全、格式化、跳转、调试、Git、多光标、搜索替换……
一律交给 VS Code 与它成熟的扩展生态，这里不重造。

## 它只做四件事

| 能力 | 你在编辑器里会看到什么 |
|---|---|
| **① 中文分层讲解** | 鼠标停到报错行 → 结论 / 定位 / 原因 / 改法 / 知识点，五层中文。行的上方还有一条 CodeLens「这一条错在讲什么」。 |
| **② 只讲最关键的一条** | 一次报 30 条，只画 **1 条**波浪线——后面 29 条几乎都是它引起的。想要全部就去开关面板里关掉降噪。 |
| **③ 改一下（修复并复检）** | 缺分号、全角标点这类有确定性修法的错误，改完**重新编译验证**通过才写进文件；没通过就一行都不动。 |
| **④ 运行（等输入不计时）** | 在终端里跑当前文件。程序等 `scanf` 时**不计超时**，手慢不会被杀；死循环可一键停止。 |

## 开关面板

`Ctrl+Alt+T`（或命令面板搜 `ctutor：打开开关与讲解面板`）打开面板，能逐项开关：

- 总开关（一键闭麦：不检查、不画线、不修复、不运行）
- 中文分层讲解 / 只讲最关键的一条 / 自己画波浪线 / 改一下 / 运行 / AI 兜底讲解

开关是**即时生效**的：关掉讲解，悬停立刻不再弹；关掉波浪线，已画的红线立刻消失。

## 快捷键

| 键 | 作用 |
|---|---|
| `Ctrl+Alt+C` | 检查当前文件（F5/F9 在 VS Code 里是调试与断点，所以另立一套） |
| `Ctrl+Alt+R` | 运行（等输入不计时） |
| `Ctrl+Alt+S` | 停止运行 |
| `Ctrl+Alt+T` | 开关与讲解面板 |

## 前置条件

1. **VS Code 1.75+**
2. **一个 C 编译器**：GCC 或 MSVC（`ctutor.compiler` 选 `auto` 会优先 GCC）
3. **Python 3.8+**：内核是 Python 写的。装完 Python 一般会自动找到；找不到就在设置里填 `ctutor.pythonPath`

> 与 C/C++ 扩展（cpptools）同时使用时，建议二选一显示波浪线：
> 关掉 `C_Cpp.errorSquiggles`，或关掉本扩展的「自己画波浪线」。两套都开会看到两层红线。

## 设计上的取舍

- **不搬编辑器**：Monaco、标签页、文件树、补全、对齐、缩放、书签……都不做。
- **不消费 cpptools 的诊断**：知识库是按 MSVC / GCC 的**原文措辞**建的（61 + 53 条），
  换成 clangd 的诊断会大面积漏匹配，所以它自己编译。
- **AI 默认关闭**：本地知识库命中时零延迟零费用；只有没收录的错误才需要模型，
  而且输出要过 schema 校验、位置必须锚在真代码上，不合格就**不显示**（宁可不说，也不乱说）。

## 装法

```bash
code --install-extension ctutor-0.1.0.vsix
```

或 VS Code 里 `扩展 → … → 从 VSIX 安装`。

## 开发

```bash
# 冒烟测试内核（不需要 VS Code）
python plugin/tests/test_sidecar.py

# 打 vsix（不需要 node）
python plugin/tools/build_vsix.py
```

目录：

```
plugin/
  extension.js        薄壳：provider、命令、终端、面板
  media/panel.html    开关面板 + 讲解渲染
  sidecar/sidecar.py  常驻内核进程（NDJSON over stdio）
  tests/test_sidecar.py
  tools/build_vsix.py
```

内核本体在仓库根目录的 `engine/` 与 `data/kb/`，与桌面版 ctutor 共用同一份——只在一边改，两边都受益。