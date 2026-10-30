# -*- coding: utf-8 -*-
"""在没有 node、没有无头 VS Code 的机器上，验证 extension.js 的逻辑。

做法：起一个只服务本地文件的小 http 服务，用浏览器（CDP）打开页面，
页面里跑 harness.js —— 它把 extension.js 原文取过来，用假的 vscode API
和假的内核子进程跑一遍，最后把断言结果挂在 window.__RESULT 上。

用法：
    python plugin/tests/serve_harness.py            # 打印端口后一直服务
"""
import http.server
import json
import os
import socketserver
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "harness")
PLUGIN = os.path.dirname(HERE)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HARNESS, **kwargs)

    def translate_path(self, path):
        clean = path.split("?", 1)[0]
        if clean in ("/", "/index.html"):
            return os.path.join(HARNESS, "index.html")
        if clean == "/extension.js":
            return os.path.join(PLUGIN, "extension.js")
        return os.path.join(HARNESS, clean.lstrip("/").replace("/", os.sep))

    def log_message(self, fmt, *args):        # 安静点
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8931
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port), Handler)
    print(json.dumps({"port": port, "url": "http://127.0.0.1:%d/" % port}), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()