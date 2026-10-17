# -*- coding: utf-8 -*-
"""用真实编译器验证「初学者常见错误」到底报什么码、什么消息。

为什么要有这个工具：
  补知识库时最怕**照抄网上的错误码**——版本不同、编译器不同，码和消息都可能变。
  这里把每个错误写成一段最小可复现代码，真的编译一遍，把编译器原话打出来。
  写进知识库的每个码，都应当先经过这个工具确认。

用法：
    python tools/probe_snippets.py                 # 两个编译器都跑
    python tools/probe_snippets.py --compiler gcc  # 只跑 GCC
    python tools/probe_snippets.py --only 漏分号    # 只跑匹配名字的
    python tools/probe_snippets.py --json out.json
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
for _s in (sys.stdout, sys.stderr):
    try:
        if _s is not None:
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                           # noqa: BLE001
        pass

from engine import compilers, config as config_mod             # noqa: E402

# (名字, 类别, 代码)  —— 每段都是**能触发一个明确错误**的最小形态
SNIPPETS = [
    # ---------------- 语法 / 标点 ----------------
    ("漏分号", "语法", '#include <stdio.h>\nint main(void){int x = 1\nprintf("%d", x);return 0;}\n'),
    ("全角分号", "语法", '#include <stdio.h>\nint main(void){int x = 1；\nprintf("%d", x);return 0;}\n'),
    ("少右大括号", "语法", '#include <stdio.h>\nint main(void){\n  printf("hi");\n  return 0;\n'),
    ("多一个右大括号", "语法", '#include <stdio.h>\nint main(void){return 0;}}\n'),
    ("圆括号不配对", "语法", '#include <stdio.h>\nint main(void){printf("hi";return 0;}\n'),
    ("if 后面多余分号", "语法", '#include <stdio.h>\nint main(void){int x=1;if(x>0);{printf("a");}else{printf("b");}return 0;}\n'),
    ("数组初始化多余项", "语法", 'int main(void){int a[2]={1,2,3};return a[0];}\n'),
    ("赋值号写进 if 条件", "逻辑", '#include <stdio.h>\nint main(void){int a=1,b=2;if(a=b){printf("x");}return 0;}\n'),
    ("把 == 当语句用", "逻辑", 'int main(void){int a=1,b=2;a==b;return 0;}\n'),

    # ---------------- 名字 / 作用域 ----------------
    ("变量名拼写错", "名字", '#include <stdio.h>\nint main(void){int count=0;printf("%d",cont);return 0;}\n'),
    ("大小写写错", "名字", '#include <stdio.h>\nint main(void){int Count=0;printf("%d",count);return 0;}\n'),
    ("漏 #include", "名字", 'int main(void){printf("hi");return 0;}\n'),
    ("用了循环外的变量", "作用域", '#include <stdio.h>\nint main(void){for(int i=0;i<3;i++){}printf("%d",i);return 0;}\n'),
    ("变量没定义就赋值", "名字", 'int main(void){x = 5;return 0;}\n'),
    ("调用没声明的函数", "名字", 'int main(void){myFunc();return 0;}\n'),
    ("函数名拼错（链接期）", "链接", '#include <stdio.h>\nint mian(void){printf("hi");return 0;}\n'),

    # ---------------- 指针 / 数组 ----------------
    ("结构体用点号却是指针", "指针", 'struct P{int x;};\nint main(void){struct P p; struct P *q=&p; q.x=1; return 0;}\n'),
    ("结构体用箭头却不是指针", "指针", 'struct P{int x;};\nint main(void){struct P p; p->x=1; return 0;}\n'),
    ("给数组名赋值", "数组", 'int main(void){int a[3];a={1,2,3};return 0;}\n'),
    ("把整数当指针用", "指针", 'int main(void){int *p; p = 5; return 0;}\n'),
    ("指针和整数比较", "指针", 'int main(void){int a[2];int *p=a; if(p==0){} return 0;}\n'),
    ("两个指针相加", "指针", 'int main(void){int a[2];int *p=a,*q=a; p = p + q; return 0;}\n'),
    ("对非指针做解引用", "指针", 'int main(void){int x=1;int y=*x;return y;}\n'),
    ("数组下标越界常量", "数组", 'int main(void){int a[3];a[5]=1;return 0;}\n'),
    ("传参时没取地址", "指针", '#include <stdio.h>\nvoid f(int *p){*p=1;}\nint main(void){int x;f(x);return 0;}\n'),
    ("scanf 忘了取地址", "指针", '#include <stdio.h>\nint main(void){int x;scanf("%d",x);return 0;}\n'),
    ("函数返回局部数组", "指针", 'int *f(void){int a[3];return a;}\nint main(void){return 0;}\n'),

    # ---------------- 类型 ----------------
    ("给变量赋不兼容的类型", "类型", 'struct S{int x;};\nint main(void){int n; struct S s; n = s; return 0;}\n'),
    ("隐式声明返回值当 int", "类型", '#include <stdio.h>\nint main(void){char *s="hi";int n=s;return n;}\n'),
    ("函数实参个数不对", "类型", '#include <stdio.h>\nint add(int a,int b){return a+b;}\nint main(void){printf("%d",add(1));return 0;}\n'),
    ("函数返回值类型不符", "类型", 'int f(void){return "abc";}\nint main(void){return 0;}\n'),
    ("void 函数有返回值", "类型", 'void f(void){return 1;}\nint main(void){return 0;}\n'),
    ("void 函数没返回值", "类型", 'int f(void){}\nint main(void){return f();}\n'),
    ("变量当数组长度（VLA）", "类型", 'int main(void){int n=3;int a[n];return a[0];}\n'),
    ("函数返回值类型不一致", "类型", '#include <stdio.h>\nint f(void);\nchar *f(void){return "x";}\nint main(void){return 0;}\n'),

    # ---------------- 声明 / 定义 ----------------
    ("变量重复定义", "定义", 'int main(void){int x=1;int x=2;return x;}\n'),
    ("函数重复实现", "定义", 'int f(void){return 1;}\nint f(void){return 2;}\nint main(void){return 0;}\n'),
    ("同名不同类型", "定义", 'int main(void){int x=1;{char x;x=2;}return x;}\n'),
    ("结构体未定义就用", "定义", 'int main(void){struct Point p;return 0;}\n'),
    ("静态函数没实现", "定义", 'static int f(void);\nint main(void){return f();}\n'),

    # ---------------- 其它高频 ----------------
    ("重复 case", "逻辑", '#include <stdio.h>\nint main(void){int x=1;switch(x){case 1:break;case 1:break;}return 0;}\n'),
    ("switch 忘写 break", "逻辑", '#include <stdio.h>\nint main(void){int x=1;switch(x){case 1:printf("1");case 2:printf("2");}return 0;}\n'),
    ("未初始化就使用", "逻辑", '#include <stdio.h>\nint main(void){int sum;sum=sum+1;printf("%d",sum);return 0;}\n'),
    ("printf 格式符不匹配", "类型", '#include <stdio.h>\nint main(void){double d=1.5;printf("%d",d);return 0;}\n'),
    ("printf 参数个数不符", "类型", '#include <stdio.h>\nint main(void){printf("%d %d",1);return 0;}\n'),
    ("头文件名拼错", "包含", '#include <studio.h>\nint main(void){return 0;}\n'),
    ("除数写成了字面量 0", "运行", '#include <stdio.h>\nint main(void){int a=1;printf("%d",a/0);return 0;}\n'),
    ("数组名当整体输出", "逻辑", '#include <stdio.h>\nint main(void){int a[3]={1,2,3};printf("%d",a);return 0;}\n'),
    ("字符串用单引号", "语法", '#include <stdio.h>\nint main(void){char *s=\'abc\';printf("%s",s);return 0;}\n'),
    ("字符用双引号", "类型", '#include <stdio.h>\nint main(void){char c="a";printf("%c",c);return 0;}\n'),

    # ---------------- 第二批：中文特有 + 社区调研补充 ----------------
    # 这几个是**中文用户特有**的错误，英文项目结构上覆盖不到
    ("全角空格（U+3000）", "中文", 'int\u3000x = 1;\nint main(void){return x;}\n'),
    ("全角弯引号（U+201C）", "中文", 'char *s = \u201chi\u201d;\nint main(void){return 0;}\n'),
    ("全角逗号", "中文", 'int main(void){int a=1\uFF0Cb=2;return a;}\n'),
    ("全角括号", "中文", 'int main(void)\uFF08{return 0;}\n'),
    ("从 Word 复制带出的不间断空格", "中文", 'int main(void){int\u00a0a=1;return a;}\n'),

    # 数组 / 下标
    ("给数组名赋值（MSVC C2106）", "数组", 'int main(void){char s[10];s = "hi";return 0;}\n'),
    ("把普通变量当数组用", "数组", 'int main(void){int a;a[0]=1;return 0;}\n'),
    ("数组声明没给长度", "数组", 'int main(void){int a[];return 0;}\n'),

    # 类型转换 / 警告
    ("double 赋给 int（丢小数）", "类型", 'int main(void){int x = 3.7;return x;}\n'),
    ("double 赋给 float", "类型", 'int main(void){float f = 3.14;return f;}\n'),
    ("有符号和无符号比较", "类型", 'int main(void){unsigned int u=1u;int i=-1;if(i<u){}return 0;}\n'),
    ("size_t 赋给 int", "类型", '#include <string.h>\nint main(void){int n = strlen("hello");return n;}\n'),
    ("指针类型不兼容", "指针", 'void f(int *p){}\nint main(void){char c=0;f(&c);return 0;}\n'),
    ("丢掉了 const 限定符", "类型", 'int main(void){const char *s="a";char *p = s;return 0;}\n'),
    ("对 const 变量自增", "类型", 'int main(void){const int x=5;x++;return x;}\n'),

    # 字面量 / 转义
    ("以 0 开头的数（八进制）", "语法", 'int main(void){int x = 09;return x;}\n'),
    ("字符串里的反斜杠没转义", "语法", 'int main(void){char *s="C:\\temp";return 0;}\n'),

    # 声明 / 定义
    ("结构体重复定义", "定义", 'struct S{int a;};\nstruct S{int b;};\nint main(void){return 0;}\n'),
    ("形参重名", "定义", 'void f(int a,int a){}\nint main(void){return 0;}\n'),
    ("形参与声明类型不一致", "定义", 'void f(int);\nvoid f(double a){}\nint main(void){return 0;}\n'),
    ("给 void 类型的变量", "类型", 'int main(void){void v;return 0;}\n'),
    ("把变量当函数调用", "名字", 'int main(void){int x=0;x();return 0;}\n'),
    ("实参比形参多", "类型", 'int f(int a){return a;}\nint main(void){return f(1,2);}\n'),
    ("声明了但从没用过的变量", "警告", 'int main(void){int unused;return 0;}\n'),
    ("函数声明了没实现（static）", "定义", 'static int f(void);\nint main(void){return f();}\n'),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="用真实编译器验证常见错误的码与消息")
    ap.add_argument("--compiler", default="all", choices=["all", "msvc", "gcc"])
    ap.add_argument("--only", default=None, help="只跑名字包含该子串的样本")
    ap.add_argument("--json", default=None, help="把结果写到 JSON")
    ap.add_argument("--max-msg", type=int, default=110)
    args = ap.parse_args(argv)

    cfg = config_mod.load()
    want = []
    if args.compiler in ("all", "msvc"):
        want.append("msvc")
    if args.compiler in ("all", "gcc"):
        want.append("gcc")

    work = os.path.join(config_mod.path_of("build"), "probe")
    os.makedirs(work, exist_ok=True)

    snippets = SNIPPETS
    if args.only:
        snippets = [s for s in snippets if args.only in s[0]]
    if not snippets:
        print("没有匹配的样本")
        return 1

    report = {}
    for cname in want:
        comp = compilers.detect(cname, cfg)
        if comp is None or comp.name != cname:
            print("跳过 %s：本机没有" % cname)
            continue
        print("=" * 78)
        print("编译器：%s（%s）" % (comp.name, getattr(comp, cname, "")))
        print("=" * 78)
        rows = []
        for idx, (name, cat, src) in enumerate(snippets):
            d = os.path.join(work, "%s_%02d" % (cname, idx))
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, "t.c")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(src)
            res = comp.compile(path, d)
            errs = [x for x in res.diagnostics if x.is_error]
            warns = [x for x in res.diagnostics if x.is_warning]
            first = (errs or warns or [None])[0]
            print("%-22s %s" % (name, "错误%d 警告%d" % (len(errs), len(warns))))
            if first:
                print("    码=%s  级别=%s" % (first.code or "(无码)", first.level))
                print("    消息=%s" % first.message[:args.max_msg])
            else:
                print("    （无诊断）")
            for x in (errs + warns)[:2]:
                if x is first:
                    continue
                print("      · [%s] %s %s" % (x.level, x.code or "-", x.message[:70]))
            rows.append({
                "name": name, "category": cat,
                "codes": sorted({x.code for x in errs if x.code}),
                "warn_codes": sorted({x.code for x in warns if x.code}),
                "level": first.level if first else "",
                "code": (first.code if first else ""),
                "message": (first.message if first else ""),
            })
        report[cname] = rows
        print()

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("已写入 %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
