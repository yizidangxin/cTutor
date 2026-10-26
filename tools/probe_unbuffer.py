# -*- coding: utf-8 -*-
"""试几种"让 printf 立刻出来"的写法，看哪种在管道下真的有效。"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine import compilers, config as config_mod

WD = os.path.join(config_mod.path_of("build"), "unbuffer_lab")
os.makedirs(WD, exist_ok=True)
cfg = config_mod.load()
comp = compilers.detect("msvc", cfg)
fast = comp._fast_path(comp.vcvars, WD)
cl, env = fast
print("cl:", cl)

MAIN = '''#include <stdio.h>
#include <windows.h>
int main(void)
{
    printf("A\\n");
    Sleep(900);
    printf("B\\n");
    Sleep(900);
    printf("C\\n");
    return 0;
}
'''

VARIANTS = {
    "none": "",
    "xcu_iolbf": '''#include <stdio.h>
static void f(void){ setvbuf(stdout, NULL, _IOLBF, 8192); }
#pragma section(".CRT$XCU", long, read)
__declspec(allocate(".CRT$XCU")) static void (__cdecl *p)(void) = f;
''',
    "xcu_ionbf": '''#include <stdio.h>
static void f(void){ setvbuf(stdout, NULL, _IONBF, 0); }
#pragma section(".CRT$XCU", long, read)
__declspec(allocate(".CRT$XCU")) static void (__cdecl *p)(void) = f;
''',
    "xiu_ionbf": '''#include <stdio.h>
static void f(void){ setvbuf(stdout, NULL, _IONBF, 0); }
#pragma section(".CRT$XIU", long, read)
__declspec(allocate(".CRT$XIU")) static void (__cdecl *p)(void) = f;
''',
}


def measure(exe):
    t0 = time.time()
    p = subprocess.Popen([exe], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, cwd=WD,
                         creationflags=compilers._spawn_flags())
    hits = []
    buf = b""
    while True:
        d = p.stdout.read1(4096)
        if not d:
            break
        hits.append((round(time.time() - t0, 2), d.decode("utf-8", "replace").strip()))
    p.wait()
    return hits


for name, hdr in VARIANTS.items():
    main_c = os.path.join(WD, "m_%s.c" % name)
    open(main_c, "w", encoding="utf-8", newline="\n").write(MAIN)
    args = [cl, "/nologo", "/std:c17", main_c, "/Fe:%s.exe" % name]
    if hdr:
        hp = os.path.join(WD, "h_%s.h" % name)
        open(hp, "w", encoding="utf-8", newline="\n").write(hdr)
        args.insert(2, "/FI" + hp)
    r = subprocess.run(args, cwd=WD, env=env, capture_output=True,
                       creationflags=compilers._spawn_flags())
    exe = os.path.join(WD, name + ".exe")
    if not os.path.exists(exe):
        print("%-11s 编译失败: %s" % (name, (r.stdout + r.stderr).decode("utf-8", "replace")[:160]))
        continue
    print("%-11s 到达时刻: %s" % (name, measure(exe)))