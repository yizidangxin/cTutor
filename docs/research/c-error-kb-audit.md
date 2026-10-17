# 开源「编译错误知识库」覆盖范围审计

> 审计目标：为中文 C 报错知识库做对比与补充。
> 所有结论均来自实际抓取的源码（原始文件已下载到 `out/`）。
> 审计日期：本次会话。

---

## 0. 一句话结论

**这五个项目里，只有 3 个真正拥有「静态错误知识库」**：

| 项目 | 知识库形态 | 条目数 | 对中文 C 库的价值 |
|---|---|---|---|
| **DCC** (`compiler_explanations.py`) | Python 列表，每条 = 正则 + f-string 解释 | **67 条** | ⭐⭐⭐⭐⭐ 最值得抄 |
| **CS50 help50** (`helpers/clang.py`) | 每错误一个 helper 函数 | **55 条编译 + 9 条运行/Valgrind** | ⭐⭐⭐⭐⭐ 最值得抄 |
| **c-error-helper** (`src/rules.ts`) | TS 数组 `RULES`，纯关键词 | **36 条** | ⭐⭐⭐⭐⭐ 直接就是中文库，结构最接近你的需求 |
| cwhy | ❌ 无知识库（LLM） | 10 条正则仅用于**定位行号** | ⭐ 只能抄 prompt 工程 |
| wut | ❌ 无知识库（LLM） | 0 条 | ⭐ 只能抄 prompt 工程 |
| compiler-explorer/explain | ❌ 无知识库（LLM） | 0 条 | ⭐ 只能抄 prompt 工程、且方向是汇编不是报错 |

**关于你给的 URL 的重要更正：**

1. `https://github.com/TeamSpen210/cwhy` → **404，不存在**。cwhy 的真实地址是 [plasma-umass/cwhy](https://github.com/plasma-umass/cwhy)（也是 [camelid/cwhy](https://github.com/camelid/cwhy)，同一代码库）。
2. `https://github.com/COMP90042-A2/dcc` → **404，不存在**（COMP90042 是 UNSW 的一门课代码，不是这个项目）。
   dcc 的真实地址是 [COMP1511UNSW/dcc](https://github.com/COMP1511UNSW/dcc) —— 论文 *dcc --help*（SIGCSE'24）就是基于它。

---

## 1. cwhy

- **仓库**：https://github.com/plasma-umass/cwhy （你给的 TeamSpen210 链接 404）
- **许可证**：Apache-2.0
- **语言**：Python；`pip install cwhy`
- **本质**：**LLM 前端**，不含任何错误解释知识库。必须配 `OPENAI_API_KEY`。
- **作者**：Nicolas van Kempen、Emery Berger（UMass PLASMA）
- **支持语言**：C, C++, C#, Go, Java, LaTeX, PHP, Python, Ruby, Rust, Swift, TypeScript

### 1.1 它唯一的结构化数据：`_error_patterns`（源码 `src/cwhy/prompts.py`）

这不是知识库，是**报错定位器**——只用来从编译器输出里抠出「文件名 + 行号」，然后把上下文源码喂给 LLM。原文注释：

> `# Define error patterns with associated information. The numbers correspond to the groups matching file name and line number.`

| 语言标签 | 正则 | file组 | line组 |
|---|---|---|---|
| C# | `([a-zA-Z0-9./][^:\r\n]+)\((\d+),(\d+)\): error ([A-Za-z0-9]+): (.*)` | 1 | 2 |
| C/C++/Rust | `([a-zA-Z0-9./][^:->]+):([0-9]+):([0-9]+)` | 1 | 2 |
| Visual Studio C/C++ | `([a-zA-Z]?:?[\\\/a-zA-Z0-9._-]+)\(([0-9]+)\)` | 1 | 2 |
| LaTeX | `(.*\.tex):(\d+): error: (.*)` | 1 | 2 |
| Java | `([a-zA-Z0-9./][^:->]+):([0-9]+): error:` | 1 | 2 |
| Python | `\s*File "(.*?)", line (\d+), in ([^\<].*)` | 1 | 2 |
| Go | `([a-zA-Z0-9./][^:\r\n]+):([0-9]+):([0-9]+): (.*): (.*)` | 1 | 2 |
| TypeScript | `([a-zA-Z0-9./][^:\r\n]+)\((\d+),(\d+)\): error ([A-Za-z0-9]+): (.*)` | 1 | 2 |
| Ruby | `"(.*\.rb)", line (\d+)(?:, in \`.*\')?: (.*)` | 1 | 2 |
| PHP | `PHP (?:Parse\|Fatal) error: (.*) in (.*) on line (\d+)` | 2 | 3 |

> 注意两处细节：注释写着 `# Note: LaTeX must precede Java`；且匹配时会 `if match and "warning" not in line.lower()` 排除 warning 行。

### 1.2 解释是怎么组织的

**没有分层，就是一句话指令**（`prompts.py::explain_prompt`）：

```python
def explain_prompt(args, diagnostic):
    return (
        _base_prompt(args, diagnostic)
        + "What's the problem? If you can, suggest code to fix the issue."
    )
```

`_base_prompt` 组装三样东西：
1. **我的代码**（报错行 ±7/+3 行，带行号）
2. **git diff**（若在 git 仓库，标出「这个文件被改过」）—— 上限 1024 字符
3. **我的报错**（首尾交替取样，直到超 `max_error_tokens`，中间插入 `[...]` 省略）

还配了 **function calling 工具**（`src/cwhy/functions.py`），让 LLM 自己去查：
`get_compile_or_run_command` / `get_code_surrounding` / `list_directory` / `find_definition`(需 clangd) / `git_status` / `git_diff`。

### 1.3 知识库：数据文件还是硬编码

**两者都不是——它没有知识库。** 唯一的硬编码是上面 10 条正则（在 `prompts.py` 里）。所有解释由 GPT-3.5/GPT-4 现场生成。

### 1.4 值得抄的地方

1. **行号定位正则表**（10 条）——你这套如果要做「vscode/gcc/clang/msvc 多种输出格式」规范化，这张表可以直接抄。
2. **首尾交替截断算法**（`_Context.get_diagnostic`）——编译器报错常常「头尾都有用、中间是模板展开垃圾」。它保留前 N 行 + 后 N 行、中间打 `[...]`，非常实用。
3. **`CWHY_DISABLE` 环境变量**——解决 CMake/Autoconf 探测性编译误触发解释的痛点。
4. **git diff 入 prompt**——「你刚改过这个文件」是极强的上下文信号。
5. **function calling 让模型自己看代码**，而不是一次性把仓库塞进去。

---

## 2. dcc（Debugging C Compiler）

- **仓库**：https://github.com/COMP1511UNSW/dcc （你给的 COMP90042-A2/dcc 是 404）
- **许可证**：**GPL-3.0**（注意：GPL-3.0 有传染性，抄进你的库要留意许可证兼容；dcc 的编译器包装是 Python）
- **语言**：Python；UNSW COMP1511 课程用，171 stars
- **论文**：Taylor, Vassar, Renzella, Pearce. *"Dcc --help: Generating Context-Aware Compiler Error Explanations with Large Language Models"*, SIGCSE'24 — https://arxiv.org/abs/2308.11873
- **本质**：**两层**——① 静态知识库（67 条，本次审计重点）② `dcc --help` 把静态知识库 + 源码位置喂给 LLM 生成「不给答案」的新手向解释。

> 论文关键数据：2565 名学生、10 周内使用 64,000+ 次；LLM 解释在编译期错误上 **90% 概念正确**、运行期 **75% 正确**；但**经常违反「不要直接给出代码答案」的指令**。

### 2.1 解释是怎么组织的（三层结构，最值得学）

源码 `compile_time_python/compiler_explanations.py`，文件头注释就是设计文档：

```
# label - unique identifier, used as file name
# regex - if set, matched against text, if match fails no explanation is returned
# precondition - if callable, called with message and regex match results
#                if value returned is False, no explanation is returned
#                if value returned is non-empty string and explanation is not set
#                the value is returned as explanation
# explanation - if callable, called with message and regex match results
#               and value returned as explanation
#               if explanation is a string, it is evaluated as f-string with the
#               fields from the message object available as local variables,
#               Note, this includes the highlighted_word and underlined_word in
#               the compiler message (if any)
# show_note - print the note (if any) on a clang warning
# no_following_explanations - if True, don't print explanations after this one
#                             use where confusing parasitic errors likely
# reproduce - C program which should yield explanation
```

**分层体现在 `Explanation` 的字段上：**

| 字段 | 作用 |
|---|---|
| `label` | 唯一 ID，同时是**长文档的文件名**（`docs/<label>.md`） |
| `regex` | 匹配 clang 原文（`re.I \| re.DOTALL`） |
| `precondition` | 可调用，做「同一正则下的二次区分」（例如同一条 warning 区分是数组还是指针） |
| `explanation` | 字符串（f-string 求值）**或** callable |
| `show_note` | 是否附带 clang 的 note |
| `no_following_explanations` | **压制后续寄生错误**——一个错引发 20 个连锁错时，只讲第一个 |
| `reproduce` | 能复现该错误的最小 C 程序 |
| `long_explanation` / `long_explanation_url` | 指向长文档页 |

**短/长两层**：`get()` 先拿短解释，若有 long 则追加
`"\n  See more information here: " + explanation_url(self.label)`，其中
`EXPLANATION_BASE_URL = https://comp1511unsw.github.io/dcc/`（源码 `util.py`），即 `docs/<label>.md` 渲染出的网页。

**解释文本的写法特色**：字符串里用 `**加粗**` 语法，
```python
f_string = re.sub(r"\*\*\{(.*?)\}\*\*", r"{emphasize(\1)}", f_string)
f_string = re.sub(r"\*\*(.*?)\*\*", r"{emphasize('\1')}", f_string)
```
然后才包成 `f"""..."""` 用 `eval` 求值。**也就是说它的解释是「带变量的模板」，不只是静态文本。**

### 2.2 完整覆盖清单：67 条（`label` | `regex`）

下表按源码顺序。`(auto)` = 未显式给 label，由 `re.sub(r"\W+", "_", regex).strip("_")` 从正则生成。

| # | label | regex（匹配 clang 原文） |
|---|---|---|
| 1 | two_main_functions | `multiple definition of \W*main\b` |
| 2 | no_main_function | `undefined reference to \W*main\b` |
| 3 | scanf_missing_ampersand | `format specifies type '(?P<type>int\|double) \*' but the argument has type '(?P=type)'` |
| 4 | format_type_mismatch | `format specifies type '[^:]+' but the argument has type '[^:]+'` |
| 5 | missing_semicolon_line_before_assert | `called object type 'int' is not a function or function pointer` |
| 6 | assert_without_closing_parenthesis | `unterminated function-like macro invocation` |
| 7 | double_int_literal_conversion | `implicit conversion from 'double' to 'int'` |
| 8 | assign_to_multidimensional_array | `array type .*?\]\[.* is not assignable` |
| 9 | assign_to_array | `array type .*?[^\]]\[(\d+)\]' is not assignable` |
| 10 | stack_use_after_return | `address of stack memory associated with local variable '(.*?)' returned` |
| 11 | assign_function_to_int | `incompatible pointer to integer conversion (assigning to\|initializing) '(\w+)'.*\(` |
| 12 | assign_array_to_int | `incompatible pointer to integer conversion (assigning to\|initializing) '(\w+)'.*]'` |
| 13 | assign_pointer_to_int | `incompatible pointer to integer conversion (assigning to\|initializing) '(\w+)'` |
| 14 | missing_library_include | `(implicitly declaring library function\|call to undeclared library function) '(\w+)'` |
| 15 | misspelt_printf | `(implicit declaration of\|call to undeclared) function '(print.?.?)'` |
| 16 | implicit_function_declaration | `(implicit declaration of function\|call to undeclared function) '(\w+)'` |
| 17 | *(auto)* | `expression is not assignable` |
| 18 | uninitialized-local-variable | `'(.*)' is used uninitialized in this function` |
| 19 | function-variable-clash | `called object type .* is not a function or function pointer` |
| 20 | *(auto)* | `function definition is not allowed here` |
| 21 | indirection-requires-pointer-operand | `indirection requires pointer operand \('(.*)' invalid\)` |
| 22 | duplicated-cond | `duplicated .*\bif\b.* condition` |
| 23 | *(auto)* | `condition has identical branches` |
| 24 | logical-or-always-true | `logical .?\bor\b.* is always true` \| `logical.*or.*of collectively exhaustive tests is always true` \| `overlapping comparisons always evaluate to true` |
| 25 | logical-and-always-false | `logical .?\band\b.* is always false` \| `overlapping comparisons always evaluate to false` |
| 26 | logical-equal-expressions | `logical .?((and\|or)).? of equal expressions` |
| 27 | *(auto)* | `declaration shadows a local variable` |
| 28 | nonnull | `argument (\d+) null where non-null expected` |
| 29 | indexing_one_too_far | `array index (\d+) is past the end of the array.*(which contains \1 element\|\[\1\])` |
| 30 | *(auto)* | `array subscript is not an integer` |
| 31 | *(auto)* | `continue.* statement not in loop` |
| 32 | *(auto)* | `break.* statement not in loop` |
| 33 | non_void_function_does_not_return_a_value_in_all_control_paths | `non-void function does not return a value in all control paths` |
| 34 | non_void_function_does_not_return_a_value | `non-void function does not return a value \[` |
| 35 | *(auto)* | `data argument not used by format string` |
| 36 | *(auto)* | `more '%' conversions than data arguments` |
| 37 | *(auto)* | `expected ';' in 'for' statement specifier` |
| 38 | *(auto)* | `expression result unused` |
| 39 | *(auto)* | `extra tokens at end of #include directive` |
| 40 | *(auto)* | `extra tokens at end of #include directive`（与 39 重复，两条不同解释路径） |
| 41 | h_file_not_found | `s.*o.h' file not found` |
| 42 | *(auto)* | `has empty body` |
| 43 | *(auto)* | `ignoring return value of function` |
| 44 | ignoring_return_value_of_function | `ignoring return value of function` |
| 45 | invalid_equal_equal_at_end_of_declaration | `invalid '==' at end of declaration; did you mean '='` |
| 46 | *(auto)* | `invalid preprocessing directive` |
| 47 | *(auto)* | `return type of 'main' is not 'int'` |
| 48 | *(auto)* | `multiple unsequenced modifications` |
| 49 | *(auto)* | ` parameter o. 'main'` |
| 50 | *(auto)* | `relational comparison result unused` |
| 51 | *(auto)* | `result of comparison against a string literal is unspecified` |
| 52 | *(auto)* | `subscripted value is not an array` |
| 53 | missing_function_return_type | `type specifier missing, defaults to 'int'` |
| 54 | missing_parameter_type | `type specifier missing, defaults to 'int'`（与 53 同正则，靠 precondition 区分） |
| 55 | *(auto)* | ` warning: unknown escape sequence '\\ '` |
| 56 | *(auto)* | ` warning: unknown escape sequence '\\ '`（同上，两条） |
| 57 | *(auto)* | `using the result of an assignment as a condition without parenthese` |
| 58 | *(auto)* | `use of undeclared identifier` |
| 59 | *(auto)* | `unknown type name 'define'` |
| 60 | *(auto)* | `unknown type name 'include'` |
| 61 | *(auto)* | `is uninitialized when used here` |
| 62 | *(auto)* | `is uninitialized when used within its own initialization` |
| 63 | *(auto)* | `void function '(.*)' should not return a value` |
| 64 | *(auto)* | `void function '(.*)' should not return a value` |
| 65 | *(auto)* | `too many arguments to function call, expected (\d+), have (\d+)` |
| 66 | *(auto)* | `comparison between pointer and integer \('char \*' and 'int'\)` |
| 67 | *(auto)* | `unknown escape sequence '\\(.)'` |

**其中带长文档链接的**：`assign_to_array`、`stack_use_after_return`、`assign_function_to_int`、`function-variable-clash`、`function definition is not allowed here`。

### 2.3 运行期错误知识库（另一套，`run_time_python/explain_error.py`）

除编译期 67 条外，dcc 还有一套**运行期**解释，且是**「内存填充值指纹」驱动的**：

```python
MEMORY_FILL = {
    "gdb_unknown": "(unknown: 0xaaaaaaaa)",
    "gdb_ascii" : "-86 (not valid ASCII)",
    "gdb_int8" : "-86 '\\252'",
    "gdb_uint8" : "170 '\\252'",
    "clang_float" : "-nan(0x7fffff)",
    "clang_double" : "-nan(0xfffffffffffff)",
    "int64_hex" : "0xaaaaaaaaaaaaaaaa",
    "int64" : "-6148914691236517206",
    "uint64" : "12297829382473034410",
    "float" : "-3.03164883e-13",
    "double" : "-3.7206620809969885e-103",
    "int32_hex" : "0xaaaaaaaa",
    "int32" : "-1431655766",
    "uint32" : "2863311530",
    "int8_hex" : "0xaa",
    "int8_char" : '\\252',
}
```

逻辑：`0xaa` 是 clang/GDB 给未初始化内存的填充值 → **报错里出现 `0xaaaaaaaa` 或 2863311530 或 -1431655766，就说明是「用了未初始化变量」**。这招极聪明，值得抄。

覆盖的运行期类别：

| 类别 | 匹配 / 触发 |
|---|---|
| UBSan pointer | `(load\|store\|applying).*({int32_hex}\|null pointer)` → 区分 uninitialized vs NULL 指针 |
| UBSan member access | `member access.*({int32_hex}\|null pointer)` → `p->field` 的两种错 |
| 除零 | `"division by zero" in message` → 按源码含 `/` 还是 `%` 挑 `x / y` 或 `x % y` |
| 未初始化值 | 遍历 `MEMORY_FILL.values()` 命中即报 |
| 溢出/下溢 | `"overflow" in message or "underflow" in message` |
| 数组大小 0 | `index .* out of bounds .*\[0\]` → 「你创建了大小为 0 的数组，非法」 |
| 越界（带大小） | `index (-?\d+) out of bounds .*\[(\d+)\]` → 打印合法区间 `0..size-1` |
| 越界（不带大小） | `index (-?\d+) out of bounds` |
| 兜底越界 | `"out of bounds" in message` |
| ASan | `explain_asan_error()`，用环境变量 `DCC_ASAN_ERROR`，做字符串改写：`"-"→" "`、`"heap"→"malloc"`、`"null deref"→"NULL pointer dereferenced"` |
| 信号 | `SIGINT` / `SIGFPE` / `SIGXCPU` / `SIGXFSZ` / `SIGSEGV`（各一条专属解释；`SIGABRT` 走 sanitizer 路线） |

### 2.4 知识库：数据文件还是硬编码

**硬编码在 Python 源码里**（`compiler_explanations.py` 是 `explanations = [...]` 一个列表；`explain_error.py` 里是 if/elif 链）。
但**长文档是数据文件**：`docs/*.md`，实测存在的有：
`assign_function_to_int.md`、`assign_to_array.md`、`eof_byte.md`、`function-definition-not-allowed-here.md`、`function-variable-clash.md`、`malloc_sizeof.md`、`missing_newline.md`、`stack_use_after_return.md`、`zero_byte.md`。

### 2.5 值得抄的地方

1. **`no_following_explanations` 字段** —— 解决「一个错引发一屏寄生错误」这个中文库最该解决的问题。CS50 也承认这个痛点（它的 `catch_all` 是最后兜底）。
2. **`precondition` 二次区分** —— 同一条 clang 正则（如 `type specifier missing`）能指向不同解释，比纯关键词精确一档。
3. **正则里用命名组 `(?P<type>...)` + 反向引用 `(?P=type)`** —— 见第 3 条，精确捕捉「`%d` 却传了 `int *`」。
4. **`reproduce` 字段：每条解释附带可复现的最小 C 程序** —— 这是**回归测试基础设施**，中文库强烈建议照抄。
5. **运行期「填充值指纹」**（`0xaa` 系列表）。
6. **短解释 + `docs/<label>.md` 长文档的两层结构**，且 label 即文件名即 URL slug。
7. **`**加粗**` → `{emphasize(...)}` 的模板预处理**，让解释文本既好读又好渲染。
8. **`tests/expected_output/<label>/0000NN-clang-<version>-<triple>.txt`** —— 按**编译器版本 × 平台三元组**分别存快照。因为 clang 14 和 16 的同一报错文案不同，这是真实工程痛点。

---

## 3. c-error-helper（C 语言报错翻译器）

- **仓库**：https://github.com/xiaogaokunkun/c-error-helper
- **许可证**：**MIT** ✅（对中文库最友好）
- **市场页**：https://marketplace.visualstudio.com/items?itemName=xiaogaokunkun.c-error-helper
- **形态**：VS Code 扩展（`code --install-extension xiaogaokunkun.c-error-helper`），纯本地离线关键词匹配，无网络
- **知识库文件**：**`src/rules.ts`**（21 KB，就是你说的「源码里的一个数组」）
- **作者定位**：「学长视角，不是教授视角」

> 源码第一行注释交代了它的来历：
> `// 从 C语言报错翻译器.html 的 const RULES 数组原样提取，仅改头部为 export。`
> 字段说明原文：`k 关键词数组 / n 名称 / why 解释 / fix 修复建议数组 / code 示例(模板串) / t 简称 / s 严重度 e|w|i / p 优先级 / ig 可忽略`

### 3.1 解释的组织方式（四段式 + 分层）

每条规则的结构（**这是最接近「原因+改法」分层的设计**）：

| 字段 | 含义 |
|---|---|
| `k` | **关键词数组**（任一命中即匹配） |
| `n` | 名称（一句话结论） |
| `why` | **为什么错**（含编译器视角的直译，如 «`invalid suffix` 直译是「非法后缀」»） |
| `fix` | **怎么改**的字符串数组（多条，按可能性排序） |
| `code` | ✅/❌ 「正确写法」对照示例（**11 条规则有**） |
| `t` | 简称（hover 时显示） |
| `s` | 严重度 `e`=error / `w`=warning / `i`=info |
| `p` | 优先级（数值越小越优先；**2 条有**：全角字符、`invalid suffix`，都是 `p:0`） |
| `ig` | 可忽略标志（**1 条有**：`unused variable`） |

**四段式 = 错在哪(`n`) + 为什么错(`why`) + 怎么改(`fix[]`) + 正确写法(`code`)** —— 正是你想要的「原因+改法」结构，而且它把「正确写法」独立成字段。

### 3.2 完整覆盖清单：36 条规则

> 下表 `k` 关键词数组逐条列全（`||` 分隔 = 数组元素）。自测情况：匹配逻辑单测 20/20、扩展 host 冒烟 14/14、真机端到端 6/6。

| # | `s` | 名称 `n` | 关键词 `k` |
|---|---|---|---|
| 1 | e | 上一行末尾漏了分号 | `expected ';' before` \|\| `expected a ';'` |
| 2 | e | 花括号没配平，少了一个 `}` | `expected declaration or statement at end of input` \|\| `expected '}' at end of input` \|\| `expected declaration or statement` |
| 3 | e | 用了一个没声明的变量名 | `undeclared (first use in this function)` \|\| `was not declared in this scope` \|\| `undeclared identifier` \|\| `undefined identifier` \|\| `use of undeclared identifier` \|\| `is undefined` |
| 4 | e | 代码里混进了中文全角字符（`p:0`） | `stray '\` \|\| `stray '\\` \|\| `stray` |
| 5 | e | 数字后面粘上了中文标点（全角字符）（`p:0`） | `invalid suffix` |
| 6 | e | 链接失败（Dev-C++ 最出名的报错） | `ld returned 1 exit status` \|\| `id returned 1 exit status` \|\| `ld returned` |
| 7 | e | 函数/变量声明了但没实现 | `undefined reference to` \|\| `undefined reference` |
| 8 | e | 头文件找不到 / 文件名写错 | `no such file or directory` \|\| `cannot open source file` \|\| `could not open source file` \|\| `include errors detected` \|\| `no directories in search list` |
| 9 | w | 没 `#include` 就把函数拿来用了 | `implicit declaration of function` \|\| `incompatible implicit declaration` |
| 10 | e | 函数调用的参数个数不对 | `too few arguments to function` \|\| `too many arguments to function` |
| 11 | e | 把 `%` 用在了小数上 | `invalid operands to binary %` \|\| `invalid operands to binary` |
| 12 | w | `scanf` 漏了取地址符 `&` | `expects argument of type 'int *', but argument` |
| 13 | w | `printf`/`scanf` 的格式符和变量类型对不上 | `expects argument of type 'double', but argument` \|\| `expects argument of type 'int', but argument` |
| 14 | e | 等号左边不是一个能装东西的变量 | `lvalue required as left operand of assignment` \|\| `lvalue required` |
| 15 | e | 在 `for` 里直接声明变量，但编译器按老标准（C89） | `for' loop initial declarations are only allowed in` \|\| `loop initial declarations` |
| 16 | w | 小数被偷偷截成整数了 | `conversion from 'double' to 'int'` \|\| `conversion from` \|\| `possible loss of data` |
| 17 | w | 数组越界了 | `array subscript is above array bounds` \|\| `array subscript` |
| 18 | w | `scanf` 里漏了 `&`（指针版） | `assignment makes pointer from integer without a cast` \|\| `makes pointer from integer` |
| 19 | e | 同一个名字重复定义了 | `redeclaration of` \|\| `conflicting types for` \|\| `previous definition` |
| 20 | e | `main` 的写法不对 | `'main' must return 'int'` \|\| `return type of 'main'` |
| 21 | e | 生成的 exe 被占用（程序还在运行） | `cannot open output file` \|\| `permission denied` |
| 22 | e | 某个符号前面缺了个东西（表达式不完整） | `expected expression before` \|\| `expected an expression` |
| 23 | e | 圆括号没配对，或括号里内容写错 | `expected ')' before` \|\| `expected a ')'` |
| 24 | e | 语句结构坏了（通常还是上面的老问题） | `expected '=' , ',' , ';' , 'asm' or` \|\| `expected identifier or '(' before` |
| 25 | w | 声明了变量但没用（**这条可忽略** `ig:1`） | `unused variable` \|\| `set but not used` |
| 26 | w | 有返回值的函数可能没走到 `return` | `control reaches end of non-void function` |
| 27 | e | 把变量当成函数调用了 | `called object` |
| 28 | e | `do-while` 少写了结尾分号 | `expected 'while'` \|\| `expected 'while' at` |
| 29 | e | 类型不兼容（多见于你把文件存成了 .cpp） | `invalid conversion` \|\| `cannot convert` |
| 30 | e | 用了微软特有的 `_s` 版本函数 | `scanf_s` \|\| `strcpy_s` \|\| `strcat_s` |
| 31 | e | 表达式缺了内容（通常是格式串或参数写坏） | `expected primary-expression` |
| 32 | e | 函数没声明（常见于 .cpp 文件里用 C 函数） | `was not declared in this scope` \|\| `'scanf' was not declared` |
| 33 | e | 内存操作出错（指针/数组写飞了） | `free(): invalid` \|\| `double free` \|\| `corrupted` |
| 34 | e | 程序运行时崩溃（越界 / 空指针 / 死递归） | `segmentation fault` \|\| **`程序奔溃`** \|\| **`程序崩溃`** \|\| **`将停止工作`** |
| 35 | w | `if` 里把 `==` 写成了 `=` | `suggest parentheses around assignment used as truth value` \|\| `parentheses around assignment` |
| 36 | w | 变量声明了但没给初值就在用 | `is used uninitialized` |

### 3.3 知识库：数据文件还是硬编码

**硬编码在 TS 源码 `src/rules.ts`**，导出 `export const RULES: any[]`。配套脚本 `scripts/extract-rules.js` 说明它是从 HTML 版抽出来的。**运行时纯本地关键词匹配**（`src/match.ts` 只有 657 字节）。

### 3.4 值得抄的地方（对中文库最直接）

1. **`k` 用「关键词数组」而非正则** —— 对中文库维护成本最低，新手也能加规则。
2. **`code` 字段做 ✅/❌ 正确写法对照** —— 只有 11/36 条有，**这是明显可补强的空位**：你可以在自己的库里把它补到 100%。
3. **`s` 严重度 + `p` 优先级 + `ig` 可忽略** —— 三档元数据，解决「warning 要不要报」「同一个错多条规则谁先赢」。
4. **收录中文输入法造成的全角标点 bug**（第 4、5 条，都是 `p:0` 最高优先级）—— **这是英文项目完全没有、中文用户最高频的坑**。而且给出了 `stray '\357' in program` 的八进制解释，属于真正的中文场景知识。
5. **收录 Dev-C++/课程环境特有的坑**：`ld returned 1 exit status`（「上次的黑窗口没关，九成是这个」）、`cannot open output file`/`permission denied`（exe 被占用）、`scanf_s`（「别抄 VS 的代码」）、`.cpp` 后缀导致按 C++ 报错。**这些「环境类错误」是纯编译器知识库的盲区。**
6. **`why` 里解释编译器的「视角」** —— 例如「报错的位置是撞墙的地方，不是出错的地方，真正少分号的是上面那一行」。这正是新手需要的心智模型。
7. **同义关键词并列覆盖多前端**：`undeclared identifier`(gcc) / `use of undeclared identifier`(clang) / `was not declared in this scope`(cpptools) 一条规则全收。

---

## 4. wut

- **仓库**：https://github.com/shobrook/wut （**不是** cogpy/wut —— 后者是 0 star 的副本）
- **许可证**：MIT
- **语言**：Python；1426 stars；最后推送 2024-12-20（**已基本停更**）
- **描述**：*A terminal assistant for the hopelessly confused*
- **本质**：**纯 LLM**（Anthropic / OpenAI / Ollama 三选一）。抓取终端最后 3 条命令的输出，丢给模型解释。

### 4.1 覆盖了哪些错误

**没有错误码、没有错误消息模式、没有知识库。** 全部源码只有 4 个文件：

| 文件 | 大小 | 作用 |
|---|---|---|
| `wut/__main__.py` | — | CLI 入口 |
| `wut/prompts.py` | 1950 B | **2 个 prompt 字符串** |
| `wut/utils.py` | 10311 B | 抓 shell 输出、截断、调用 LLM |
| `wut/wut.py` | 1974 B | 编排 |

它唯一的「配置」是这些常量（`utils.py`）：

```python
MAX_CHARS = 10000      # 输出截断上限
MAX_COMMANDS = 3       # 保留最近 3 条命令
SHELLS = ["bash", "fish", "zsh", "csh", "tcsh", "powershell", "pwsh"]
```

### 4.2 解释是怎么组织的

**一个 system prompt 三层 XML 标签**（`prompts.py::EXPLAIN_PROMPT`）：

```
<assistant>      角色：解释终端最近一条命令的输出
<instructions>   接收最后一条命令 + 之前的命令作上下文；解释输出；识别 root cause 并给修复建议
<formatting>     Markdown；命令/代码放 fenced block；只有警告或要点才加粗；
                 拆成易消化的小段；**尽量短，不超过 5 句**（除非问题复杂）
```

另有一个 `ANSWER_PROMPT`（回答用户对上次命令的提问，结构相同）。

### 4.3 知识库：数据文件还是硬编码

**没有知识库。** 硬编码的只有 2 个 prompt 字符串 + 3 个常量。

### 4.4 值得抄的地方

1. **`SHELLS` 列表 + 按 shell 解析 prompt** —— 如果要覆盖 zsh/fish/powershell 的报错样式，这是最小实现参考。
2. **`MAX_COMMANDS = 3`：把「前几条命令」当上下文** —— 对应你库里可以记「上一条命令/上一次编译」。
3. **格式约束写得极具体**：「不要超过 5 句」「只有警告和要点才加粗」。中文库如果将来接 LLM 兜底，这段 prompt 可直接借鉴。
4. **三provider 抽象**（`run_anthropic` / `run_openai` / `run_ollama`）—— 本地 Ollama 兜底，离线可用，符合中文教学场景。

---

## 5. CS50 help50

- **仓库**：https://github.com/cs50/help50-deprecated （**注意：已废弃**，README 明确 `deprecated`）
- **许可证**：**GPL-3.0**（LICENSE 35149 字节 = GPL-3.0 全文）
- **文档**：https://cs50.readthedocs.io/help50/
- **知识库实际位置**：⚠️ **不在 help50 仓库里**，而在 **[cs50/helpers](https://github.com/cs50/helpers)**（默认下载 `cs50/helpers@main`）
- **本质**：命令行包装器，`help50 make hello`

### 5.1 架构（插件式，很干净）

```
help50 → 运行你的命令（pty.spawn 保留颜色）→ 捕获全部输出
       → 去掉 ANSI 转义码
       → 对每个 domain 依次跑 preprocessor
       → 对每行做起始切片（lines[i:]），依次尝试该 domain 的 helper
       → 第一个返回非空的 helper 胜出，渲染 ("原始报错", "翻译")
       → 都不匹配 → catch_all 兜底
```

关键设计（`help50/internal.py::get_help`）：
- **逐行起始切片**：`for i in range(len(lines)): slice = lines[i:]` —— 允许报错不在第一行。
- **第一个匹配即返回**，靠注册顺序控制优先级。
- **catch_all 必须最后** —— 源码 docstring：`This helper should **ALWAYS** be the last helper -- it is a catch-all, worst case scenario.`
- **未命中时**输出：`"Sorry, help50 does not yet know how to help with this!"`（诚实地承认不知道）。

### 5.2 解释的组织方式

**每个错误 = 一个 Python 函数**（`@helper("clang")` 装饰器注册），返回 `(被翻译的原始报错行, [解释句1, 解释句2, ...])`。

输出渲染（`render_help`）：
1. 先把**原始报错行**用 `grey on yellow` 高亮打印；
2. 再把翻译用 **yellow** 打印，且 `` `反引号` `` 内的内容转成**粗体**：
   ```python
   re.sub(r"`([^`]+)`", r"\033[1m\1\033[22m", " ".join(help[1]))
   ```

**解释的写法：两句话 —— ①「定位 + 事实」，②「引导性反问或建议」**，例如：
```
"It looks like your function, `{}`, is returning `{}` on line {} of `{}`, but its return type is `void`."
"Are you sure you want to return a value?"
```
**注意它刻意不给答案**——用反问引导学生。这对中文教学库是很值得学的语气。

它还从报错里**自动抽取变量名/值**辅助定位：
- `_caret_extract(lines)` —— 从 `^` 符号上方取出变量名（`left_aligned` 参数控制对齐方式）
- `_tilde_extract(lines)` —— 提取第一段 `~~~~` 上方对应的原始文本
- `_has_caret(line)` —— `^[ ~]*\^[ ~]*$` 判断是否是 caret 诊断行
- `_match(expression, line)` —— 统一前缀正则

`_match` 的前缀模式（**这是 clang 报错解析的关键**）：
```python
query = expression if raw else fr"^([^:\s]+):(\d+):\d+: (?:warning|(?:fatal |runtime )?error): {expression}"
```
即先吃掉 `file:line:col: error:` 头，第 1 组 = 文件名、第 2 组 = 行号，**写 helper 时只需写报错正文**。

### 5.3 完整覆盖清单

#### A. 编译期（`helpers/clang.py`）— 55 个 helper

| # | 函数名 | 匹配的正则（`_match` 的正文部分） |
|---|---|---|
| 1 | array_bounds | `array index (\d+) is past the end of the array` |
| 2 | array_subscript | `array subscript is not an integer` |
| 3 | assignment_as_condition | `using the result of an assignment as a condition without parentheses` |
| 4 | bad_define | `unknown type name 'define'` |
| 5 | **bad_include** | *(无 `_match`，自定义逻辑)* |
| 6 | comp_str_literal_unspecified | `result of comparison against a string literal is unspecified` |
| 7 | conflicting_types | `conflicting types for '(.*)'` |
| 8 | continue_not_in_loop | `'continue' statement not in loop statement` |
| 9 | control_reaches_non_void | `control (may )?reach(es)? end of non-void function` |
| 10 | declaration_shadows_local_var | `declaration shadows a local variable` |
| 11 | div_by_zero | `division by zero is undefined` |
| 12 | expected_closing_brace | `expected '}'` |
| 13 | expected_closing_parens | `expected '\)'` |
| 14 | expected_for_semi_colon | `expected ';' in 'for' statement specifier` |
| 15 | expected_identifier_or_parens | `expected identifier or '\('` |
| 16 | expected_if_open_parens | `expected '\(' after 'if'` |
| 17 | expected_param_declarator | `expected parameter declarator` |
| 18 | expected_semi_colon | `expected ';' (?:after expression\|at end of declaration\|after do/while statement)` |
| 19 | expected_while_in_do_while | `expected 'while' in do/while loop` |
| 20 | expression_not_int_const | `expression is not an integer constant expression` |
| 21 | expression_result_unused | `expression result unused` |
| 22 | extra_tokens_at_end_of_include | `extra tokens at end of #include directive` |
| 23 | extraneous_closing_brace | `extraneous closing brace \('}'\)` |
| 24 | extraneous_closing_parens | `extraneous '\)' before ';'` |
| 25 | file_not_found_include | `'(.*)' file not found` |
| 26 | fmt_specifies_type | `format specifies type '[^:]+' but the argument has type '[^:]+'` |
| 27 | fmt_string_not_string_literal | `format string is not a string literal` |
| 28 | has_empty_body | `(if statement\|while loop\|for loop) has empty body` |
| 29 | ignoring_return_value | `ignoring return value of function declared with (.+) attribute` |
| 30 | implicit_declaration_of_fun | `implicit declaration of function '([^']+)' is invalid` |
| 31 | implicitly_declaring_lib_fun | `implicitly declaring library function '([^']+)'` |
| 32 | incompatible_conversion | `incompatible (.+) to (.+) conversion` |
| 33 | index_out_of_bounds | `index (-?\d+) out of bounds for type '.+'` |
| 34 | invalid_append_string | `adding '(.+)' to a string does not append to the string` |
| 35 | invalid_equals | `invalid '==' at end of declaration; did you mean '='?` |
| 36 | invalid_preprocessing_directive | `invalid preprocessing directive` |
| 37 | main_must_return_int | `'main' must return 'int'` |
| 38 | missing_parens | `assigning to '(.+)' from incompatible type '.+ \(.+\)'` |
| 39 | more_conversions_than_data_args | `more '%' conversions than data arguments` |
| 40 | multiple_unsequenced_modifications | `multiple unsequenced modifications to '(.*)'` |
| 41 | one_param_on_main_dec | `only one parameter on 'main' declaration` |
| 42 | relational_comp_res_unused | `relational comparison result unused` |
| 43 | second_param_main_char | `second parameter of 'main' \(argument array\) must be of type 'char \*\*'` |
| 44 | self_initialization | `variable '(.+)' is uninitialized when used within its own initialization` |
| 45 | subscripted_val_not_array | `subscripted value is not an array, pointer, or vector` |
| 46 | too_many_args_to_fun_call | `too many arguments to function call, expected (\d+), have (\d+)` |
| 47 | type_specifier_missing | `type specifier missing, defaults to 'int'` |
| 48 | undefined_reference | `undefined reference to \`([^']+)'` |
| 49 | unknown_escape_sequence | `unknown escape sequence '\ '` |
| 50 | unknown_type | `unknown type name '(.+)'` |
| 51 | unused_arg_in_fmt_string | `data argument not used by format string` |
| 52 | unused_var | `unused variable '([^']+)'` |
| 53 | use_of_undeclared_indentifier | `use of undeclared identifier '([^']+)'`（原函数名拼写就是 `indentifier`） |
| 54 | variable_uninitialized | `variable '(.*)' is uninitialized when used here` |
| 55 | void_return | `void function '(.+)' should not return a value` |
| 56 | **catch_all** | `.*` ← **永远最后** |

#### B. 运行期（`helpers/runtime.py`）

| 函数 | 匹配 |
|---|---|
| `floating_point_exception` | *(自定义逻辑)* |
| `segmentation_fault` | *(自定义逻辑)* |

#### C. Valgrind（`helpers/valgrind.py`）— 7 个

> 注意：Valgrind helper 的匹配是**多行顺序断言**（数组里每个元素必须在对应位置命中）。

| 函数 | 匹配序列（前两行固定） |
|---|---|
| all_heap_blocks_freed | `^==\d+== Memcheck, a memory error detector$` → `^==\d+== All heap blocks were freed -- no leaks are possible$` |
| bytes_definitely_lost | Memcheck 头 → `^==\d+== ([\d,]+)(?: \(([\d,]+) direct, ([\d,]+) indirect\))? bytes in ([\d,]+) blocks ` |
| conditional_jump_depends | Memcheck 头 → `^==\d+== Conditional jump or move depends on uninitialised value\(s\)$` |
| definitely_lost | Memcheck 头 → `^==\d+==    definitely lost: ([\d,]+) bytes in ([\d,]+) blocks$` → `^==\d+== Command: ([^\n]+)$` … `Rerun with --leak-check=full to see details` |
| invalid_read | Memcheck 头 → `^==\d+== Invalid read of size ([\d,]+)$` |
| invalid_write | Memcheck 头 → `^==\d+== Invalid write of size ([\d,]+)$` |
| use_of_uninitialized_val | Memcheck 头 → `^==\d+== Use of uninitialised value of size ([\d,]+)$` → `^==\d+==    (?:at\|by) (0x[0-9A-Fa-f]+): (.+) \((.+?)(?::(\d+))?\)` |

#### D. 其它 domain（同为 cs50/helpers）

`bash.py`、`cp.py`、`ls.py`、`make.py`、`mv.py`、`rm.py`、`_common.py` —— 覆盖**shell 命令**的报错（不只是编译器），扩展性强。

### 5.4 覆盖范围的自证：测试语料

`cs50/helpers` 的 `test_files/` 就是它声明的覆盖范围清单，**可直接当你的回归测试用例集**：

- `test_files/clang/` — 63 个 `.c` 文件，文件名就是知识库条目名（`array_bounds.c`、`self_initialization.c`、`unknown_type_size_t.c`、`catch_all_expected_expression.c` …）
- `test_files/runtime/` — `floating_point_exception`、`segmentation_fault`
- `test_files/valgrind/` — `all_heap_blocks_freed`、`bytes_definitely_lost`、`conditional_jump_depends`、`definitely_lost`、`invalid_read`、`invalid_write`、`use_of_uninitialised_val`

> 比 dcc 的 `tests/compile_time_errors/`（只有 11 个 `.c`）完备得多；dcc 则强在 `tests/expected_output/` 有**逐编译器的版本化快照**。**两者结合 = 理想测试基建。**

### 5.5 知识库：数据文件还是硬编码

**硬编码为 Python 函数**，用 `@helper("clang")` 装饰器注册到 `HELPERS` 字典。
但注意：**helper 本身是「可下载的插件包」**（`--slug cs50/helpers/main`，`lib50.local()` 下载到本地），所以「知识库」在架构上**是可热更新的外部仓库**，这是很聪明的设计。

### 5.6 值得抄的地方

1. **"Not quite sure how to help, but focus your attention on line {} of `{}`!"** —— catch_all 兜底话术：即使不知道原因，也**至少给出定位**。比直接说「不认识」有用得多。
2. **每个 helper 的 docstring 里写 doctest** —— 测试和知识库同文件、同函数，改规则必须改测试。这是它能维护 55 条的根因。
3. **`test_files/` 与 helper 函数一一同名** —— 覆盖范围自解释。
4. **用反问代替答案**（"Are you sure you want to return a value?"）—— 教学伦理，论文也印证了这点（dcc 的 LLM 就常违反）。
5. **`_caret_extract` / `_tilde_extract` 从 clang 的 `^` / `~~~` 下划线上抽变量名和值** —— 让解释能带上具体变量名，从「有个变量没用」升级到「变量 `count` 没用」。**这是纯关键词匹配做不到的。**
6. **插件化 + 可下载 slug**，让知识库独立于工具版本演进。
7. **多 domain 架构**（clang / valgrind / runtime / bash / make / cp / rm）—— 一个框架吃下所有「看不懂的输出」。
8. **`catch_all` 强制最后** 的注册顺序约定 + docstring 里写明这条规矩。

---

## 5.9 补充：Clang 的 `-fdiagnostics-*`（不是知识库，需澄清）

你提到 `clang 的 -fdiagnostics-*`，实测后需要**明确纠正一个常见误解**：

**`-fdiagnostics-*` 是「诊断的呈现/控制开关」，不含任何错误解释知识。** 官方文档 [Diagnostic flags in Clang](https://clang.llvm.org/docs/DiagnosticsReference.html) 的定位是：

> "This page lists the diagnostic flags currently supported by Clang."
> 正文以 `-W<flag>` 为条目，每条列 **Diagnostic text**（即该 flag 控制的**警告原文**）+ 默认开关状态 + 它 controls 了哪些子 flag。

它**只解决两件事**，且都对你的中文库有直接工程价值：

1. **报错格式规范化（让正则好写）**
   `-fdiagnostics-format=clang|msvc|vi` —— 强制统一输出格式。你的库如果要同时吃 gcc / clang / msvc 三种文案，先用这个把格式压平，比写三套正则省事得多（cwhy 就是因为没有这个，才需要 10 条正则 + "Visual Studio C/C++" 单独一条）。
2. **机器可读的 fix-it 建议**
   `-fdiagnostics-parseable-fixits` —— 让 clang 直接输出「第几行第几列插入/替换成什么」，即**编译器自己给出的改法**。这与 c-error-helper 的 `fix[]` 字段目标完全一致，而且是**权威来源**，不是人工猜测。

其它常用同族 flag：`-fno-show-column`、`-fshow-column`（配合解析列号）、`-fdiagnostics-color=always|never`（对应 help50 要 `re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", output)` 去 ANSI 码的那一步）、`-fmax-errors=N`（截断报错洪水，对应 dcc 的 `no_following_explanations`）。

另外两个高价值**开发/参考**资源（非知识库，但能当数据源）：
- **[WarningSuppressionMappings](https://clang.llvm.org/docs/WarningSuppressionMappings.html)** —— `-W` flag 与 GCC `-W` flag 的**双向映射表**。你的库若按 flag 分类，这张表能解决「同一条错 gcc 和 clang 叫什么」的归一化问题。
- **诊断的机器可读清单**：Clang 的诊断不是列举式的枚举，而是由 TableGen 在源码里定义（`clang/include/clang/Basic/Diagnostic*.td`），因此**不存在「clang 错误码列表」这种东西**——这是 clang 与 Rust 的关键差异。对比之下 **rustc 有显式错误码** `Exxxx`，每个一页文档（见 [rustc 错误码索引](https://github.com/ferrocene/ferrocene/blob/main/compiler/rustc_error_codes/src/lib.rs)）——**如果你要给中文 C 库引入「错误码 + 长文档」的范式，该抄的是 rustc，不是 clang。**

**结论：`-fdiagnostics-*` 覆盖 0 条错误。** 但 `-fdiagnostics-format` 与 `-fdiagnostics-parseable-fixits` 是你应该**在采集层**就用起来的两个开关。

---

## 6. compiler-explorer 的错误解释（现状澄清）

你提到「compiler-explorer 的错误解释」，实测结论：

- **Compiler Explorer 主仓库**（https://github.com/compiler-explorer/compiler-explorer，BSD-2-Clause）**有** `Explain` 按钮，但它调用的是**独立服务**：[compiler-explorer/explain](https://github.com/compiler-explorer/explain)（**BSD-2-Clause**，Matt Godbolt 版权）。
- **该服务解释的是「汇编」，不是「报错」**。README/设计文档原文：

  > "The service provides AI-powered explanations of compiler output for Compiler Explorer users. The service receives compiled code and its resulting assembly, then uses Claude 3.5 Haiku to generate explanations that help users understand the relationship between their source code and the generated assembly."

- **覆盖了哪些错误**：**0 条**。没有任何错误码或错误消息模式表。它的「知识」是 `app/prompt.yaml`（5734 B）+ `app/prompt.py`（17347 B）里的 prompt 模板，配 `explanation_types`（`assembly` / `haiku`）与 `audience_levels`（`beginner` / `experienced`）。
- 其设计文档自己记录了大量 LLM 幻觉问题（很诚实，值得一读）：
  > "compile time optimization - bullshit" / "'likely leverages LEA' - bullshit" / "The code demonstrates a straightforward, optimized implementation of integer squaring with minimal stack manipulation."
- 有 `prompt_testing/test_cases/hard_cases.yaml`（**154 KB**）作为 prompt 评测语料，以及「自动打分 + Claude 评审 + 人工复核」三路评测框架。

**结论：对「C 报错知识库」而言，compiler-explorer 这条线没有可迁移的错误码知识。** 唯一值得借鉴的是它的**prompt 评测框架**（版本化 prompt + 金标语料 + 自动/模型/人工三层评分），如果中文库将来要接 LLM 兜底，这套评测方法论有价值。

### 6.1 另外两个找到的同类项目（价值有限）

| 项目 | 地址 | 许可证 | 结论 |
|---|---|---|---|
| Error-Explainer | https://github.com/Abhiram-kandiyana/Error-Explainer | GPL-3.0 | 2024-10 创建、**10 月即停更**，0 star，Jupyter Notebook，主题为 code-llama / few-shot / LoRA —— 是**微调实验**，无错误码知识库 |
| errex | https://pypi.org/project/errex/ | — | PyPI 包「Error Explainer」，同上，无静态知识库 |

---

## 7. 合并清单（去重 + 高频标注）

> **重叠度说明**：`★★★` = 3 个项目都覆盖（**确认高频**，你必须有）
> `★★` = 2 个项目覆盖（**高频**）
> `★` = 仅 1 个项目覆盖（补强点/长尾）

### 7.1 编译期错误（按主题分组）

#### A. 语法/标点类

| 错误（规范化） | c-error-helper | dcc | CS50 | 重叠 |
|---|---|---|---|---|
| 漏分号（`expected ';'`，报在下一行） | ✅ #1 | ✅ #37 `expected ';' in 'for' statement specifier` | ✅ #18 `expected_semi_colon` | **★★★** |
| 括号不配对 `expected ')'` | ✅ #23 | — | ✅ #13 | ★★ |
| 花括号不配对 / 文件末尾 `expected '}'` | ✅ #2 | — | ✅ #12 | ★★ |
| 多了一个右花括号 `extraneous closing brace` | — | — | ✅ #23 | ★ |
| 多了一个右括号 `extraneous ')'` | — | — | ✅ #24 | ★ |
| `expected identifier or '('` | ✅ #24 | — | ✅ #15 | ★★ |
| `expected expression` / `expected primary-expression` | ✅ #22, #31 | — | — | ★ |
| `expected '(' after 'if'` | — | — | ✅ #16 | ★ |
| `expected parameter declarator` | — | — | ✅ #17 | ★ |
| `do-while` 少结尾分号 | ✅ #28 | — | ✅ #19 | ★★ |
| `continue` 不在循环里 | — | — | ✅ #8 | ★ |
| `break` 不在循环里 | — | ✅ #32 | — | ★ |
| `continue` 不在循环里（dcc 版） | — | ✅ #31 | — | ★ |
| `function definition is not allowed here` | — | ✅ #20 | — | ★ |
| `has empty body`（if/while/for 空体） | — | ✅ #42 | ✅ #28 | ★★ |
| `type specifier missing, defaults to 'int'` | — | ✅ #53, #54 | ✅ #47 | ★★ |
| `invalid preprocessing directive` | — | ✅ #46 | ✅ #36 | ★★ |
| `unknown type name 'define'` | — | ✅ #59 | ✅ #4 | ★★ |
| `unknown type name 'include'` | — | ✅ #60 | — | ★ |
| `unknown type name 'X'`（通用，如 `size_t`/`bool`/`string`） | — | — | ✅ #50 | ★ |
| `extra tokens at end of #include directive` | — | ✅ #39, #40 | ✅ #22 | ★★ |

#### B. 声明/定义/链接类

| 错误 | c-error-helper | dcc | CS50 | 重叠 |
|---|---|---|---|---|
| 未声明标识符 `use of undeclared identifier` | ✅ #3, #32 | ✅ #58 | ✅ #53 | **★★★** |
| 隐式函数声明 `implicit declaration of function` | ✅ #9 | ✅ #16 | ✅ #30 | **★★★** |
| 隐式声明**库**函数 `implicitly declaring library function` | — | ✅ #14 | ✅ #31 | ★★ |
| 函数名拼错（如 `print`） | — | ✅ #15 | — | ★ |
| 重复定义 / 类型冲突 | ✅ #19 | — | ✅ #7 | ★★ |
| `undefined reference`（声明了没实现/没链接） | ✅ #7 | — | ✅ #48 | ★★ |
| 多个 `main` / `multiple definition of main` | — | ✅ #1 | — | ★ |
| 没有 `main` / `undefined reference to main` | — | ✅ #2 | — | ★ |
| `main` 返回类型不是 int | ✅ #20 | ✅ #47 | ✅ #37 | **★★★** |
| `main` 参数个数不对（only one parameter） | — | ✅ #49 | ✅ #41 | ★★ |
| `main` 第二个参数类型不是 `char **` | — | — | ✅ #43 | ★ |
| 函数与变量同名冲突 | — | ✅ #19 | — | ★ |
| 函数定义不允许在此处 | — | ✅ #20 | — | ★ |
| 头文件找不到 `file not found` | ✅ #8 | ✅ #41（专指 `<stdio.h>` 之类） | ✅ #25 | **★★★** |
| `bad include`（自定义逻辑） | — | — | ✅ #5 | ★ |

#### C. 类型 / 转换类

| 错误 | c-error-helper | dcc | CS50 | 重叠 |
|---|---|---|---|---|
| `implicit conversion from 'double' to 'int'` / 精度丢失 | ✅ #16 | ✅ #7 | — | ★★ |
| `incompatible conversion`（类型不兼容） | ✅ #29 | — | ✅ #32 | ★★ |
| 指针赋给整型（`incompatible pointer to integer conversion`） | ✅ #18（指针方向） | ✅ #11, #12, #13（三种） | — | ★★ |
| 函数指针赋给 int | — | ✅ #11 | — | ★ |
| 数组赋给 int | — | ✅ #12 | — | ★ |
| 指针赋给 int | — | ✅ #13 | — | ★ |
| 给整型赋了个函数 | — | ✅ #10 `assign_function_to_int` | — | ★ |
| 给数组赋值 `assign_to_array` | — | ✅ #9 | — | ★ |
| 给多维数组赋值 | — | ✅ #8 | — | ★ |
| `lvalue required as left operand of assignment` | ✅ #14 | — | — | ★ |
| 给 `void` 函数 `return` 值 | — | ✅ #63, #64 | ✅ #55 | ★★ |
| 非 void 函数某路径没 return | ✅ #26 | ✅ #33, #34 | ✅ #9 | **★★★** |
| 指针与整数比较 `comparison between pointer and integer` | — | ✅ #66 | — | ★ |
| 字符串字面量比较未定义 | — | ✅ #51 | ✅ #6 | ★★ |
| `expression is not assignable` | — | ✅ #17 | — | ★ |
| `indirection requires pointer operand` | — | ✅ #21 | — | ★ |
| `called object type ... is not a function`（把变量当函数调） | ✅ #27 | ✅ #5, #19 | — | ★★ |

#### D. 数组 / 下标类

| 错误 | c-error-helper | dcc | CS50 | 重叠 |
|---|---|---|---|---|
| 数组越界（含「下标超一位」） | ✅ #17 | ✅ #29 | ✅ #1, #33 | **★★★** |
| 下标不是整数 `array subscript is not an integer` | — | ✅ #30 | ✅ #2 | ★★ |
| 对非数组取下标 `subscripted value is not an array` | — | ✅ #52 | ✅ #45 | ★★ |
| 数组大小 0（运行期） | — | ✅（run-time） | — | ★ |

#### E. printf/scanf 格式串类（**高频重灾区**）

| 错误 | c-error-helper | dcc | CS50 | 重叠 |
|---|---|---|---|---|
| 格式符与参数类型不符 | ✅ #13 | ✅ #3, #4 | ✅ #26 | **★★★** |
| `scanf` 漏 `&`（参数类型是 `int *`） | ✅ #12 | ✅ #3 | — | ★★ |
| `scanf` 漏 `&`（指针 from integer 版） | ✅ #18 | — | — | ★ |
| `%` 转换符多于数据参数 | — | ✅ #36 | ✅ #39 | ★★ |
| 数据参数未被格式串使用（多余参数） | — | ✅ #35 | ✅ #51 | ★★ |
| 格式串不是字符串字面量 | — | — | ✅ #27 | ★ |
| `%` 用在浮点数上 `invalid operands to binary %` | ✅ #11 | — | — | ★ |
| `expected expression before ...`（格式串写坏） | ✅ #22, #31 | — | — | ★ |

#### F. 警告类（值得报但不阻断）

| 警告 | c-error-helper | dcc | CS50 | 重叠 |
|---|---|---|---|---|
| 未使用变量 | ✅ #25（标 `ig:1` 可忽略） | — | ✅ #52 | ★★ |
| 变量未初始化就使用 | ✅ #36 | ✅ #18, #61 | ✅ #54 | **★★★** |
| 变量在自身初始化中使用 | — | ✅ #62 | ✅ #44 | ★★ |
| `if (x = 5)` 把 `==` 写成 `=` | ✅ #35 | — | ✅ #3 | ★★ |
| `invalid '==' at end of declaration; did you mean '='` | — | ✅ #45 | ✅ #35 | ★★ |
| `if (x = 5)` 的 scanf 变体 | ✅ #18 | — | — | ★ |
| 表达式结果未使用 | — | ✅ #38 | ✅ #21 | ★★ |
| 关系比较结果未使用 | — | ✅ #50 | ✅ #42 | ★★ |
| 忽略函数返回值 | — | ✅ #43, #44 | ✅ #29 | ★★ |
| 重复的 if 条件 | — | ✅ #22 | — | ★ |
| 两个分支完全相同 | — | ✅ #23 | — | ★ |
| `or` 恒为真 / 重叠比较恒为真 | — | ✅ #24 | — | ★ |
| `and` 恒为假 / 重叠比较恒为假 | — | ✅ #25 | — | ★ |
| `and`/`or` 相同表达式 | — | ✅ #26 | — | ★ |
| 声明遮蔽局部变量 | — | ✅ #27 | ✅ #10 | ★★ |
| 除零（编译期常量） | — | — | ✅ #11 | ★ |
| 表达式不是整数常量 | — | — | ✅ #20 | ★ |
| `NULL` 传给要求非空参数 | — | ✅ #28 | — | ★ |
| 未定序的多次修改（`i = i++`） | — | ✅ #48 | ✅ #40 | ★★ |
| 未知转义序列 `unknown escape sequence` | — | ✅ #55, #56, #67 | ✅ #49 | ★★ |
| 字符串相加不追加 `adding 'x' to a string` | — | — | ✅ #34 | ★ |
| 赋值给不兼容类型（缺括号） | — | — | ✅ #38 | ★ |

#### G. 环境 / 工具链类（**中文场景独有，dcc 与 CS50 都没有**）

| 错误 | 覆盖项目 | 重叠 |
|---|---|---|
| `ld returned 1 exit status`（Dev-C++ 最出名；先关掉上次的黑窗口） | c-error-helper #6 | ★ |
| `cannot open output file` / `permission denied`（exe 被占用） | c-error-helper #21 | ★ |
| 中文全角标点 `stray '\357' in program` | c-error-helper #4 (`p:0`) | ★ |
| 数字后粘全角标点 `invalid suffix` | c-error-helper #5 (`p:0`) | ★ |
| `scanf_s` / `strcpy_s` / `strcat_s`（MSVC 特有，GCC 没有） | c-error-helper #30 | ★ |
| `for` 内声明变量但按 C89 编译 | c-error-helper #15 | ★ |
| 文件存成 `.cpp` 导致按 C++ 报错 | c-error-helper #27, #29, #32 | ★ |

#### H. 运行期错误

| 错误 | c-error-helper | dcc | CS50 | 重叠 |
|---|---|---|---|---|
| 段错误 `segmentation fault` | ✅ #34（含中文「程序崩溃」「将停止工作」） | ✅（SIGSEGV） | ✅ runtime | **★★★** |
| 浮点异常 / 除零 | — | ✅ SIGFPE + division by zero | ✅ runtime | ★★ |
| 用未初始化指针（含 `p->field`） | — | ✅ UBSan 分支 | — | ★ |
| 用 NULL 指针（含 `p->field`） | — | ✅ UBSan 分支 | — | ★ |
| 数组越界（运行期，带合法区间提示） | — | ✅ | ✅ | ★★ |
| 整型溢出/下溢 | — | ✅ | — | ★ |
| `free(): invalid` / `double free` / 堆损坏 | ✅ #33 | — | — | ★ |
| Valgrind 内存泄漏 `definitely lost` / `bytes lost` | — | — | ✅（7 条中 3 条） | ★ |
| Valgrind `Invalid read` / `Invalid write` | — | — | ✅ | ★ |
| Valgrind `Conditional jump depends on uninitialised value` | — | — | ✅ | ★ |
| Valgrind `Use of uninitialised value` | — | — | ✅ | ★ |
| Valgrind `All heap blocks were freed`（无泄漏，正面反馈） | — | — | ✅ | ★ |

### 7.2 高频 Top 榜（★ 数排序，即「必须有」清单）

**★★★ 三项目全部覆盖（10 条，最高频）：**

1. 漏分号 `expected ';'`
2. 未声明标识符 `use of undeclared identifier`
3. 隐式函数声明 `implicit declaration of function`
4. `main` 返回类型不是 `int`
5. 头文件找不到 `file not found`
6. 非 void 函数有路径没 `return`
7. 数组越界
8. 格式符与参数类型不符
9. 变量未初始化就使用
10. 段错误 `segmentation fault`

**★★ 两项目覆盖（高频）：**

11. 括号 / 花括号不配对
12. `expected identifier or '('`
13. `do-while` 少结尾分号
14. 空体 `has empty body`
15. `type specifier missing, defaults to 'int'`
16. `invalid preprocessing directive`
17. `unknown type name 'define'`
18. `extra tokens at end of #include`
19. 隐式声明**库**函数
20. 重复定义 / 类型冲突
21. `undefined reference`
22. `main` 参数个数不对
23. `double`→`int` 隐式转换
24. `incompatible conversion`
25. 指针/整数互相赋值
26. 给 `void` 函数 `return` 值
27. 字符串字面量比较未定义
28. 把变量当函数调用
29. 下标不是整数
30. 对非数组取下标
31. `scanf` 漏 `&`
32. `%` 转换符多于参数 / 参数多于转换符
33. 未使用变量
34. 变量在自身初始化中使用
35. `if` 里 `=` 当 `==`
36. `invalid '==' at end of declaration`
37. 表达式结果未使用
38. 关系比较结果未使用
39. 忽略函数返回值
40. 声明遮蔽局部变量
41. 未定序多次修改 `i = i++`
42. 未知转义序列
43. 浮点异常/除零
44. 运行期数组越界

### 7.3 你的中文库可以立刻补的三个空白

| 空白 | 只有谁在做 | 为什么值得补 |
|---|---|---|
| **中文输入法全角标点**（`stray '\357'`、`invalid suffix`） | 仅 c-error-helper，且标 `p:0` 最高优先级 | 英文项目结构上不可能覆盖；中国初学者第一高频坑 |
| **Dev-C++ / 课程环境类错误**（`ld returned 1 exit status`、exe 被占用、`scanf_s`、`.cpp` 后缀） | 仅 c-error-helper | 「不是代码错，是环境错」——纯编译器知识库的盲区 |
| **Valgrind / sanitizer 运行期错误** | CS50（Valgrind 7 条）+ dcc（UBSan/ASan/信号） | 你的库若只做编译期，学生「编译过了但崩了」就无解；dcc 的 `0xaa` 填充值指纹法可以直接搬 |

反之，**dcc 有、中文库容易漏的 12 条冷门 clang 诊断**：
`condition has identical branches`、`duplicated if condition`、`logical or/and always true/false`、`logical equal expressions`、`declaration shadows a local variable`、`argument N null where non-null expected`、`indirection requires pointer operand`、`called object type ... is not a function`、`expression is not assignable`、`multiple unsequenced modifications`、`data argument not used by format string`、`result of comparison against a string literal is unspecified`。

---

## 8. 四个可直接落地的工程做法（跨项目最值得抄的五件事）

1. **每条规则配一个可复现的最小 C 程序**（dcc 的 `reproduce` + CS50 的 `test_files/<name>.c` 同名约定）
   → 规则改名/改文案时，测试自动跟着走；也能做「覆盖率」度量。

2. **`no_following_explanations` / `catch_all 必须最后`：抑制寄生错误**
   → 一个漏分号能引发 20 条报错。中文库若逐条列 20 条，学生直接放弃。

3. **短解释 + 长文档两层**（dcc：`explanation` 短句 + `docs/<label>.md` 长文；c-error-helper：hover 短 + `Ctrl+.` 长）
   → 不打断心流是这类工具的核心 UX。

4. **从 `^` / `~~~` 下划线抽变量名和值**（CS50 `_caret_extract` / `_tilde_extract`）
   → 把「有个变量没用」升级成「变量 `count` 没用」。这是关键词匹配做不到的精度提升。

5. **反编译器的「视角」解释 + 反问语气**
   → c-error-helper：「报错的位置是撞墙的地方，不是出错的地方」。
   → CS50：「Are you sure you want to return a value?」（反复问，不给答案；dcc 论文实测 LLM 做不到这点）。

---

## 附：实际访问过的 URL

**cwhy**
- https://github.com/TeamSpen210/cwhy （**404，不存在**）
- https://github.com/plasma-umass/cwhy
- https://github.com/plasma-umass/cwhy （camelid/cwhy 为同一库）https://github.com/camelid/cwhy
- https://raw.githubusercontent.com/plasma-umass/cwhy/main/README.md
- https://raw.githubusercontent.com/plasma-umass/cwhy/main/src/cwhy/prompts.py
- https://raw.githubusercontent.com/plasma-umass/cwhy/main/src/cwhy/functions.py
- https://api.github.com/repos/plasma-umass/cwhy/git/trees/main?recursive=1
- https://pypi.org/project/cwhy/0.3.2/

**dcc**
- https://github.com/COMP90042-A2/dcc （**404，不存在**）
- https://github.com/COMP1511UNSW/dcc
- https://api.github.com/repos/COMP1511UNSW/dcc
- https://api.github.com/repos/COMP1511UNSW/dcc/git/trees/master?recursive=1
- https://raw.githubusercontent.com/COMP1511UNSW/dcc/master/compile_time_python/compiler_explanations.py
- https://raw.githubusercontent.com/COMP1511UNSW/dcc/master/run_time_python/explain_error.py
- https://raw.githubusercontent.com/COMP1511UNSW/dcc/master/compile_time_python/compile.py
- https://raw.githubusercontent.com/COMP1511UNSW/dcc/master/run_time_python/explain_context.py
- https://raw.githubusercontent.com/COMP1511UNSW/dcc/master/compile_time_python/util.py
- https://arxiv.org/abs/2308.11873 （*Dcc --help*, SIGCSE'24）
- https://arxiv.org/html/2308.11873v2
- https://cgi.cse.unsw.edu.au/~cs1511/26T1/resources/debugging_guide.html

**c-error-helper**
- https://github.com/xiaogaokunkun/c-error-helper
- https://api.github.com/repos/xiaogaokunkun/c-error-helper/git/trees/main?recursive=1
- https://raw.githubusercontent.com/xiaogaokunkun/c-error-helper/main/src/rules.ts
- https://marketplace.visualstudio.com/items?itemName=xiaogaokunkun.c-error-helper
- https://marketplace.visualstudio.com/items/xiaogaokunkun.c-error-helper/changelog

**wut**
- https://github.com/shobrook/wut （真正的 wut，1426 stars）
- https://github.com/cogpy/wut （0 star 的同名副本）
- https://raw.githubusercontent.com/shobrook/wut/master/wut/prompts.py
- https://raw.githubusercontent.com/shobrook/wut/master/wut/utils.py
- https://www.x-cmd.com/install/wut

**CS50 help50**
- https://github.com/cs50/help50-deprecated
- https://api.github.com/repos/cs50/help50-deprecated/git/trees/main?recursive=1
- https://raw.githubusercontent.com/cs50/help50-deprecated/main/help50/__main__.py
- https://raw.githubusercontent.com/cs50/help50-deprecated/main/help50/internal.py
- https://github.com/cs50/helpers （**知识库实际所在**）
- https://api.github.com/repos/cs50/helpers/git/trees/main?recursive=1
- https://raw.githubusercontent.com/cs50/helpers/main/helpers/clang.py
- https://raw.githubusercontent.com/cs50/helpers/main/helpers/valgrind.py
- https://raw.githubusercontent.com/cs50/helpers/main/helpers/runtime.py
- https://cs50.readthedocs.io/help50/
- https://spencertiberi.github.io/playbook50/resources/pdf/help50.pdf

**compiler-explorer**
- https://github.com/compiler-explorer/compiler-explorer
- https://raw.githubusercontent.com/compiler-explorer/compiler-explorer/main/README.md
- https://github.com/compiler-explorer/explain （解释的是汇编，非报错）
- https://api.github.com/repos/compiler-explorer/explain/git/trees/main?recursive=1
- https://raw.githubusercontent.com/compiler-explorer/explain/main/claude_explain.md
- https://raw.githubusercontent.com/compiler-explorer/explain/main/LICENSE （BSD-2-Clause）
- https://compiler-explorer.com/z/9MKdGjoz8

**其它 / 参考**
- https://github.com/Abhiram-kandiyana/Error-Explainer （GPL-3.0，已停更，无知识库）
- https://pypi.org/project/errex/
- https://clang.llvm.org/docs/DiagnosticsReference.html （clang `-fdiagnostics-*` / 全部诊断 flag 官方参考）
- https://chromium.googlesource.com/external/github.com/llvm/llvm-project/+/d2298e74235598f15594fe2c99bbac870a507c59/clang/docs/DiagnosticsReference.rst
- https://github.com/ferrocene/ferrocene/blob/main/compiler/rustc_error_codes/src/lib.rs （rustc 错误码索引，每个 `Exxxx` 一页文档，是「错误码 + 长文档」范式的最佳参考）
- https://rust.googlesource.com/rust/+/485c20d2489d94e2e1aead9788b5ff5973550510/compiler/rustc_error_codes/src/error_codes
