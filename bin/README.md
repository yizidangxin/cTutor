# bin/ —— 便携工具链放这里

这个目录用于放置**免安装的编译器工具链**，使整个 `c-tutor/` 目录可以拷贝到任何机器上直接使用，
不需要管理员权限、不需要改注册表、不需要配置 PATH。

## 放什么

推荐 [w64devkit](https://github.com/skeeto/w64devkit)（便携版 GCC + GDB + Make，全离线）：

1. 下载 `w64devkit-x64-*.exe`（自解压包）
2. 解压到本目录，使路径形如：
   ```
   bin/w64devkit/bin/gcc.exe
   ```
3. 工具会自动发现它；也可以显式指定：

   ```bash
   set CTUTOR_GCC=%CD%\bin\w64devkit\bin\gcc.exe
   ```
   或写在 `config/ctutor.json` 的 `compiler.gcc_path`。

## 为什么单独放一个目录

- **发行形态**：`c-tutor/` + `bin/w64devkit/` 就是一个完整的离线包，拷走即用。
- **许可证**：w64devkit 里的 GCC 是 GPL，捆绑分发需要附带其许可证文件。
  官方包内已包含 `COPYING.MinGW-w64-runtime.txt` 等文件，**分发时不要删除**。
  你自己的 Python 代码与它是「并排分发」（mere aggregation），不会被 GPL 传染。

本目录内容默认被 `.gitignore` 忽略，不会进仓库。
