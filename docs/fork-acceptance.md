# DigUp 定制版验收记录

验收日期：2026 年 10 月 15 日。源码位于
[brucetoo/DigUp](https://github.com/brucetoo/DigUp)，改造已通过
[PR #1](https://github.com/brucetoo/DigUp/pull/1) 合入 `main`。
macOS 安装包对应源码 `8a376882a9f9db665e30bd86a6e4819d11a3a386`。
复现方法见[维护与打包文档](fork-maintenance.md)。

## 功能与自动检查

默认语言为简体中文。你可以在设置中切换 English，选择持久保存；
Windows 的不同窗口通过事件同步语言。翻译不改变文件名、路径或搜索内容。
两平台启动时从安装包加载模型，先校验大小和 SHA256，
模型缺失或损坏时提示修复安装，不自动下载。

本次自动测试共 172 个通过：

| 范围 | 通过数量 |
| --- | ---: |
| Swift 引擎、状态和模型验证 | 107 |
| Python 模型打包、清单检查和上游同步 | 41 |
| JavaScript 语言切换、持久化和跨窗口事件 | 5 |
| Rust 设置、翻译、Windows 模型路径和桌面逻辑 | 19 |

[Fork checks](https://github.com/brucetoo/DigUp/actions/runs/38701191743)
通过，模型及翻译清单检查无失败。

## macOS arm64 安装包

`build.noindex/DigUp-Chinese-macOS-arm64.dmg` 已生成，约 594 MiB。
已挂载 DMG 检查实际应用的模型和签名结构，再卸载。
应用使用 ad-hoc 签名，未配置 Developer ID 或公证。

实际应用在独立空索引中直接加载 `Contents/Resources/Models`：
`recipe.txt` 首轮索引完成，用 `how long to bake the cake`
在搜索面板找到该文件。已查看中文搜索面板和切换后的英文设置页。
本地复核命令均返回成功：

```bash
codesign --verify --deep --strict build.noindex/DigUp.app
python3 scripts/bundle-models.py --verify-only \
  --to build.noindex/DigUp.app/Contents/Resources/Models
```

DMG 的 SHA256 为
`fe177b565c967cda56cfd7fb9000bd30e132454e08facf006cd64647d67648e6`。

## Windows x64 安装包

[Packages 构建](https://github.com/brucetoo/DigUp/actions/runs/38691573346)
生成完整安装包。首次安装验收发现 Tauri 的 `\\?\` 模型资源路径
被 Swift Foundation 解析为非法 URL，启动校验失败。
修复已提交为 `56a33b5debc69699a095644d8047157d9ab59dc1`，
并增加覆盖本地盘、中文路径、UNC 和显式覆盖的回归测试。
Windows GUI 构建的验收结果改用显式文件输出。

[Windows 安装验收](https://github.com/brucetoo/DigUp/actions/runs/38701195733)
已通过。Windows 本机运行 20 个 Rust 测试全部通过，NSIS 安装包
实际安装后，包内模型通过 Python 权威清单与 helper 的独立校验。
本轮复用原包未变更的 Swift helper、运行库、模型及许可证，
以当前源码重新构建 Rust 桌面层和 NSIS 安装包。

首次运行未指定模型目录，只使用安装目录中的 `models/`。
79 秒内完成首轮索引和面板搜索，查询 `how long to bake the cake`
找到并预览 `recipe.txt`。面板报告实际包含“1 个结果”“打开”
“在资源管理器中显示”等中文文字，设置文件保存 `zh-Hans`。
图片、截图、录音和文档共 5 个文件生成 5 个向量片段。
helper 在仅包含 Windows 系统目录的 PATH 下完成状态检查与语义搜索。

安装包位于 `build.noindex/windows/DigUp_0.7.0-beta.1_x64-setup.exe`，
约 576 MiB。原始报告保存在上述 CI 任务的
`Windows-package-report` artifact，包含 `panel.json`、`status.txt`
和模型、搜索、索引日志。

安装包的 SHA256 为
`4993813dac48fbdb20718f2f0e794b9770dad83f075353cd612e78d848fa033f`。

## 上游同步

[真实同步 dry-run](https://github.com/brucetoo/DigUp/actions/runs/38690828091)
通过。`upstream-main` 指向上游提交
`ec28973268b81f836be72187ff23b3a16ef4fd97`，定制提交保留在 `main`。
同步任务每周一 UTC 03:23 检查更新，也支持手动触发。
有新提交时生成草稿 PR；冲突、缺失翻译和模型版本变化写入报告，
由你审阅后合入。

两平台的应用更新入口指向自己的 Releases。当前未配置 Windows
Authenticode 和应用 updater 签名，不作为已签名的正式发行版发布。
