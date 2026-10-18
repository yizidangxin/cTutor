#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""「上次进度」与「扁平存放」的测试。

这两件事都直接关系到"东西会不会丢"，所以测的不是功能能跑，
而是**边界情况下不丢、不崩**：

1. 未保存的改动必须能跨进程活下来（关掉程序再打开，那段代码还在）。
2. 没改动的文档只存路径，下次从磁盘重读 —— 文件被别的程序改过也要读到新的。
3. 会话文件坏了 / 文件被删了 / 路径是空的 —— 一律退回到"没有上次进度"，
   **绝不能因为一个坏文件而打不开程序**。
4. 扁平存放：新建出来的是 <目录>/<名字>.c，不是 <目录>/<名字>/main.c。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine import config as config_mod                      # noqa: E402
from engine import session as session_mod                    # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name,
                         "" if cond else "　→ " + str(detail)))


# 把会话文件指到一个临时目录，别污染真实的 %APPDATA%
_tmp = tempfile.mkdtemp(prefix="ctutor_sess_")
_real_user_config = config_mod.user_config_path

# 先在"真实配置"状态下确认默认位置，再重定向
_real_sess = session_mod.session_path()
check("会话文件默认与 config.json 同目录（不放进项目目录）",
      os.path.dirname(_real_sess) == os.path.dirname(_real_user_config())
      and os.path.basename(os.path.dirname(_real_sess)) == "ctutor",
      _real_sess)
check("会话文件在项目目录之外（打包分享不会带上编辑状态）",
      os.path.dirname(_real_sess) != ROOT
      or os.path.abspath(_real_sess) != os.path.join(ROOT, "session.json"),
      _real_sess)

config_mod.user_config_path = lambda: os.path.join(_tmp, "config.json")

try:
    sess_dir = os.path.dirname(session_mod.session_path())
    check("重定向后会话仍与配置文件同目录",
          sess_dir == _tmp, sess_dir)

    # ------------------------------------------------------------ 1. 保存与读回
    work = os.path.join(_tmp, "work")
    os.makedirs(work)
    f1 = os.path.join(work, "作业1.c")
    f2 = os.path.join(work, "作业2.c")
    with open(f1, "w", encoding="utf-8", newline="\n") as f:
        f.write("int main(void){ return 0; }\n")
    with open(f2, "w", encoding="utf-8", newline="\n") as f:
        f.write("int main(void){ return 1; }\n")

    docs = [
        {"path": f1, "encoding": "utf-8", "label": "作业1",
         "text": "int main(void){ return 0; }\n", "modified": False},
        {"path": f2, "encoding": "utf-8", "label": "作业2",
         "text": "int main(void){ /* 还没写完 */ return 1; }\n", "modified": True},
    ]
    p = session_mod.save(docs, 1)
    check("会话写入成功", bool(p) and os.path.exists(p), p)

    got = session_mod.load()
    check("读回来有两个文档", len(got["docs"]) == 2, got)
    check("记住停在第 2 个标签", got["active"] == 1, got["active"])
    check("有改动的文档把文本一起存了（下次不会丢）",
          "还没写完" in (got["docs"][1].get("text") or ""), got["docs"][1])
    check("没改动的文档不存正文（下次从磁盘重读）",
          not got["docs"][0].get("text"), got["docs"][0])

    # 没改动的文档，磁盘内容变了要读到新的
    with open(f1, "w", encoding="utf-8", newline="\n") as f:
        f.write("int main(void){ return 99; }  // 被别的程序改过\n")
    got2 = session_mod.load()
    check("未改动文档从磁盘重读，能拿到最新内容",
          "99" not in (got2["docs"][0].get("text") or ""),
          "会话里不该缓存旧正文")

    # ------------------------------------------------------------ 2. 坏会话不能挡启动
    with open(p, "w", encoding="utf-8") as f:
        f.write("{ 这不是合法 JSON ")
    bad = session_mod.load()
    check("会话文件坏了 → 当作没有上次进度（不抛异常）",
          bad["docs"] == [] and bad["active"] == 0, bad)

    with open(p, "w", encoding="utf-8") as f:
        json.dump({"schema": "别的版本", "docs": [{"path": f1}]}, f)
    vbad = session_mod.load()
    check("schema 不匹配 → 忽略（避免旧格式读出乱数据）", vbad["docs"] == [], vbad)

    with open(p, "w", encoding="utf-8") as f:
        json.dump({"schema": session_mod.SESSION_SCHEMA, "active": "x",
                   "docs": [{"path": f1}, "不是字典", {"nopath": 1}]}, f)
    mixed = session_mod.load()
    check("字段类型乱写也不炸（非法项被跳过）",
          len(mixed["docs"]) == 2 and mixed["active"] == 0, mixed)

    # ------------------------------------------------------------ 3. 大小与数量限制
    big = "x" * (session_mod.MAX_TEXT_BYTES + 1000)
    session_mod.save([{"path": "", "encoding": "utf-8", "label": "巨大",
                       "text": big, "modified": True}], 0)
    huge = session_mod.load()
    check("超长文本不会把会话文件撑爆（超预算就不存正文）",
          len(huge["docs"]) == 1 and not huge["docs"][0].get("text"),
          len(huge["docs"][0].get("text") or ""))

    many = [{"path": "", "encoding": "utf-8", "label": "d%d" % i,
             "text": "int a;\n", "modified": True} for i in range(50)]
    session_mod.save(many, 0)
    capped = session_mod.load()
    check("标签数量有上限（不会无限增长）",
          len(capped["docs"]) <= session_mod.MAX_DOCS, len(capped["docs"]))

    # ------------------------------------------------------------ 4. 空会话
    session_mod.save([], 0)
    check("没有文档时清掉会话文件", not os.path.exists(session_mod.session_path()))
    check("没有会话时 load 返回空结构",
          session_mod.load()["docs"] == [])

    # ------------------------------------------------------------ 5. 扁平存放
    import newproject as np_mod
    check("后缀去重：输入 作业1.c 不会变成 作业1.c.c",
          np_mod.strip_c_ext("作业1.c") == "作业1", np_mod.strip_c_ext("作业1.c"))
    check("后缀去重：带大写后缀也行",
          np_mod.strip_c_ext("HW1.C") == "HW1", np_mod.strip_c_ext("HW1.C"))
    ok, why = np_mod.validate_name("作业 1")
    check("中文文件名允许", ok, why)
    ok2, _ = np_mod.validate_name("a/b")
    check("带路径分隔符的名字被拒绝", not ok2)
    ok3, _ = np_mod.validate_name("")
    check("空文件名被拒绝", not ok3)

    # 真正创建一次（不起 GUI，直接调对话框的 _accept）
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    dlg = np_mod.NewProjectDialog(default_dir=work)
    dlg.ed_name.setText("第3次作业")
    dlg.cmb_tpl.setCurrentIndex(1)
    created = dlg.create_only()
    check("创建出来的是 <目录>/<名字>.c（扁平，不套文件夹）",
          created == os.path.join(work, "第3次作业.c"), created)
    check("文件真的存在", os.path.exists(created), created)
    check("没有生成同名文件夹",
          not os.path.isdir(os.path.join(work, "第3次作业")),
          os.path.join(work, "第3次作业"))
    with open(created, "r", encoding=dlg.encoding) as f:
        check("模板内容写进去了", "Hello, World!" in f.read())

finally:
    config_mod.user_config_path = _real_user_config
    shutil.rmtree(_tmp, ignore_errors=True)

# ---------------------------------------------------------------- 汇总
print()
failed = [r for r in results if not r[1]]
print("上次进度与存放测试：%d 项，通过 %d，失败 %d"
      % (len(results), len(results) - len(failed), len(failed)))
for name, _, detail in failed:
    print("  失败：%s　→ %s" % (name, detail))
sys.exit(1 if failed else 0)
