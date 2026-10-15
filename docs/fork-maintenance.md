# DigUp 定制版维护

本文说明 `brucetoo/DigUp` 的上游同步、检查与手动打包流程。
`main` 保留中文和离线模型定制，源码上游是 `ARahim3/DigUp main`。
同步只提交审阅 PR，不自动合入或发布，不向上游提交 PR。

## 首次配置

你需要 Git、Python 3.10 或更新版本，以及已授权的 GitHub CLI。
自动任务使用仓库的 `GITHUB_TOKEN`，不需要模型、应用签名或公证凭据。

1. 克隆自己的 fork，配置只用于获取源码的上游：

   ```bash
   git clone https://github.com/brucetoo/DigUp.git
   cd DigUp
   git remote add upstream https://github.com/ARahim3/DigUp.git
   git fetch origin main
   git fetch upstream main
   ```

2. 首次没有 `upstream-main` 时，以双方共同提交建立基线：

   ```bash
   git branch upstream-main "$(git merge-base origin/main upstream/main)"
   git push origin upstream-main
   ```

   已有基线时不要重新创建或覆盖。它必须属于上游历史，而且已包含在
   fork `main` 中；不要把定制提交放进去。无共同历史时停止并检查远端。

3. 在 fork 的 **Settings → Actions → General** 中启用 Actions，
   并开启 **Allow GitHub Actions to create and approve pull requests**。
   如果组织策略禁止写入或创建 PR，必须先解除限制。

4. 将本任务的文件通过人工审阅放入 fork 的默认 `main`，再运行：

   ```bash
   gh auth login
   gh workflow run sync-upstream.yml --repo brucetoo/DigUp -f dry_run=true
   gh run list --repo brucetoo/DigUp --workflow sync-upstream.yml
   ```

   手动任务和计划任务都从 fork `main` 读取可信同步脚本。
   `fork-checks.yml` 必须先存在于默认分支，才能被显式 dispatch。

## 同步与审阅

同步每周一 UTC 03:23 检查上游；你也可以随时手动触发。
脚本默认发布到自己的 fork，运行前必须确认你有该仓库的写权限。

先检查，再按需创建同步 PR：

```bash
# 获取并实际合并、检查，但不推送，不调用 GitHub PR/issue API
bash scripts/sync-upstream.sh --dry-run --report /tmp/digup-sync.md

# 与 dry-run 相同的本地行为
bash scripts/sync-upstream.sh --no-publish

# 创建草稿 PR；失败检查仍写入 PR 正文，命令返回非零
bash scripts/sync-upstream.sh

# GitHub runner 上执行同一流程
gh workflow run sync-upstream.yml --repo brucetoo/DigUp -f dry_run=false
```

脚本在临时 clone 中执行，退出时删除临时目录。即使你的工作目录有
未提交或未跟踪文件，它也不切换你的分支、不改你的索引或本地远端引用。
`--report` 只向你指定的已有父目录写入报告；不指定时报告输出到终端。

一次同步按以下顺序执行：

1. 获取 fork `main`、上游 `main`，验证共同历史和基线。
2. 以完整目标 SHA 命名 `sync/upstream-<SHA>`，从 fork `main`
   合并上游，保留双方历史，不使用 rebase、reset 或强制推送。
3. 若已有相同目标的 PR，复用并保持原分支不变；关闭的 PR 不自动重开。
   先前已推送但未建 PR 时，复用包含目标提交的远端分支并检查其实际内容。
4. 无冲突时运行翻译和模型固定值检查，创建草稿 PR，再显式运行
   `fork-checks.yml`。检查失败也保留草稿，脚本返回非零。
5. 由你审阅上游提交列表、检查报告和实际 diff，完成平台验收后人工合入。
6. 下次同步发现目标已包含在 `main` 中时，只以普通快进推送更新
   `upstream-main`。待审 PR 不推进基线，也不直接更新 `main`。

使用 merge commit 合入，或至少保留上游目标提交的祖先关系。
Squash/rebase 合入可能丢失该关系，导致脚本无法识别已同步。
同一 SHA 的关闭或已合入 PR 若目标仍不在 `main` 历史中，脚本停止，
需要你检查合入方式；不要重置基线或强制覆盖历史。

`GITHUB_TOKEN` 创建 PR 不会触发普通 `pull_request` workflow，
因此脚本使用 `actions: write` 显式 dispatch 检查。检查本身只有
`contents: read`，不使用 `pull_request_target` 或发布签名 secrets。
同步合并后只运行可信 checkout 的检查器，不运行上游提供的构建脚本。

## 冲突处理

合并冲突返回非零，先执行 `git merge --abort`，再在自己的 fork 创建
冲突 issue。报告包含目标 SHA、基线、上游提交范围及冲突文件。
相同 SHA 的已有 issue 会复用，包括已关闭的 issue；不会重复通知人员。
冲突时不推送同步分支、不改变 `main` 或 `upstream-main`。

你可以按 issue 中的完整 SHA，在独立分支解决冲突：

```bash
git fetch origin main
git fetch upstream main
git switch -c sync/manual-upstream origin/main
# 将下面变量设置为 issue 中的完整目标 SHA
read -r -p "输入 issue 中的完整 SHA: " UPSTREAM_SHA
git merge --no-ff "$UPSTREAM_SHA"
# 编辑冲突文件后，用 git add 标记解决，再完成 merge commit
python3 scripts/check-fork.py --base origin/main
```

不要直接在 `main` 上解决冲突。保留中文、模型加载、编辑器回显和
手动更新行为；确认上游已包含的修复不被重复实现。
取远端或 API 失败时脚本停止，不把所有失败误报为源码冲突。

## 翻译与模型检查

检查器直接读取磁盘上的源码和资源，没有生成键列表或硬编码模型快照。
你修改资源后再次运行即可得到新结果：

```bash
python3 scripts/check-fork.py
python3 scripts/check-fork.py --base origin/main
python3 -B -m unittest discover -s scripts/tests -p 'test_fork_*.py' -v
bash -n scripts/sync-upstream.sh
node --test shell/ui/i18n.test.cjs
```

Swift 检查扫描 `Sources/DigUpApp/**/*.swift` 的 `L10n.tr` 字符串，
将 `\(expression)` 按顺序转换为 `{0}`、`{1}`，并检查嵌套的 `tr`。
中文权威资源为 `Sources/DigUpApp/Localization/zh-Hans.json`。

JavaScript 检查扫描 `shell/ui/**/*.js` 的 `t(...)`，读取
`shell/ui/zh-Hans.json`。它处理英文直写字符串、模板插值、
静态三元分支和字符串相加。动态值以模板槽匹配实际字典键；
插值中的静态单复数分支分别检查。重复键、空翻译、新增未翻译键，
以及中文模板引用英文键中不存在的槽都会返回失败。

这是静态检查，不是 Swift/JavaScript 完整编译器。
`t(variable)`、`L10n.tr(variable)`、HTML 静态文本、Rust 消息、
运行时拼接及间接函数调用仍需要界面和行为测试。
输出中的 `runtime-only call(s)` 明确提示这部分覆盖限制。
新增英文直写但未接入翻译函数的控件也必须人工审阅。

模型清单来自 `Sources/DigUpKit/ModelFiles.swift`，
通过 `scripts/bundle-models.py` 的同一解析器验证。
`--base` 使用 Git 中指定提交的清单，对比 repository、revision、
文件名称、大小、SHA-256 和文件顺序。任何变化都失败并打印前后清单。
不指定 `--base` 只验证当前格式，不能检测历史变化。

模型变化必须人工审阅：确认许可证、更新 revision 缓存、重新打包，
并验证索引和向量兼容性。不要只修改报告或旧 `bundled.json` 绕过检查。
有意升级仍会产生失败报告；审阅确认后由维护者决定是否允许合入。
合入后的新基线验证不是对先前模型升级的自动批准。

本地同步测试以临时 bare 仓库和模拟 `gh` 运行；所有 commit/push
只发生在测试目录，不访问 GitHub。也可自行指定本地远端：

```bash
bash scripts/sync-upstream.sh \
  --origin /tmp/digup-fork.git --upstream /tmp/digup-upstream.git --no-publish
```

## macOS 应用打包

macOS 构建使用 Apple Silicon Mac、Xcode 26、Git、Python 和
CMake（或现有 llama 构建脚本支持的 `uv`）。应用最低支持 macOS 14。
模型构建可以使用本地权重目录，首次缓存许可证时可能仍需联网。

在仓库根目录运行：

```bash
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
export DIGUP_MODEL_SOURCE="$HOME/Models/embeddinggemma-2"
scripts/fetch-vendor.sh
scripts/build-llama.sh
swift test
SIGN_ID=- BUNDLE_ID=com.brucetoo.DigUp ./build.sh
python3 scripts/bundle-models.py --verify-only \
  --to build.noindex/DigUp.app/Contents/Resources/Models
codesign --verify --deep --strict build.noindex/DigUp.app
```

`build.sh` 生成 `build.noindex/DigUp.app`，将模型与许可证放入
`Contents/Resources/Models`，然后签名。`SIGN_ID=-` 明确使用
ad-hoc 签名，不代表 Developer ID 签名或公证成功。
`DIGUP_MODEL_SOURCE` 中的权重必须匹配当前大小和 SHA-256。

验收必须检查实际 `.app` 的中文资源、权限说明、模型及离线首次搜索，
不能仅以 `swift test` 或 SwiftPM 构建目录资源存在判定打包成功。
`build.sh` 显式复制 SwiftPM 本地化 bundle 和中英文权限说明，
并对最终包内模型再次校验；运行时在后台校验，通过后才启动推理。

## Windows 安装包打包

Windows x64 使用 Swift 6.4、Rust、Node、Python、Visual Studio
C++ 工具和 Vulkan SDK。在启用了 MSVC 开发环境的 Git Bash 中构建。
ffmpeg 构建单独在 MSYS2 MINGW64 中执行，工具安装步骤以
现有 [Packages workflow](../.github/workflows/packages.yml) 为准。

先在 MSYS2 中运行 `scripts/build-ffmpeg.sh`，再回到 Git Bash：

```bash
export DIGUP_MODEL_SOURCE="$HOME/Models/embeddinggemma-2"
export PATH="$(dirname "$(command -v cl)"):$PATH"
unset TAURI_SIGNING_PRIVATE_KEY TAURI_SIGNING_PRIVATE_KEY_PASSWORD
scripts/fetch-vendor.sh
scripts/build-llama.sh
swift test
cd shell/src-tauri
cargo test
cd ../..
scripts/package-windows.sh
scripts/check-windows-package.sh \
  shell/src-tauri/target/release/bundle/nsis/*-setup.exe
```

安装包输出到 `shell/src-tauri/target/release/bundle/nsis/`。
它携带 helper、运行库、ffmpeg、`models/` 和许可证。
检查脚本实际安装并验证安装目录模型，再进行首次加载与搜索。
它会安装应用并启动测试实例，只能在专用 Windows 测试环境运行。
macOS 上通过静态检查不等于 Windows 安装验收。

手动 CI 构建使用自己的 fork，不使用上游发布流程：

```bash
gh workflow run packages.yml --repo brucetoo/DigUp -f ref=main
gh run list --repo brucetoo/DigUp --workflow packages.yml
```

`packages.yml` 由父任务维护；同步工作流不调用它，不自动发布。
构建产物必须通过安装、离线运行和双语验收后再人工上传 Releases。

已有 CI 安装包可单独复核，`run_id` 使用生成该产物的任务 ID：

```bash
gh workflow run windows-package-check.yml --repo brucetoo/DigUp \
  -f run_id=38701195733 -f rebuild_shell=false
```

该任务保留首次运行报告和 helper 日志，失败时也会上传
`Windows-package-report`。`rebuild_shell=true` 仅适用于 Swift helper、
依赖、模型和许可证均未变更的桌面层修复：它从原包复用这些文件，
重新构建 Rust 桌面层和 NSIS 安装包。helper 或依赖有变化时必须
通过 `packages.yml` 完整构建。安装脚本使用 `--debug-report` 文件
检查搜索结果，不依赖 Windows GUI 程序的标准输出。

## 更新与发布边界

目前没有配置自己的 Developer ID、公证、Windows Authenticode、
Sparkle EdDSA 或 Tauri updater 签名凭据。不要复用上游密钥、
更新 feed 或 Homebrew 发布流程。
定制版发布策略是关闭自动 updater，只从
[自己的 Releases](https://github.com/brucetoo/DigUp/releases)
手动下载和安装更新。

Windows 的 `updates.rs` 和 macOS 的 `Updates.swift` 均只提供
fork 手动更新入口。Info.plist 已删除上游 feed 和公钥，Tauri
配置也已移除上游 updater 配置，两平台均使用 `com.brucetoo.DigUp`。

发布前检查两平台最终包的标识、更新入口、模型固定值、许可证、
中文资源和实际离线运行。签名与公证能力未配置时，只能将构建视为
待验收产物，不要描述为已签名、公证的正式发行版。

## 持续维护

workflow 在 fork `main` 部署后，运行 dry-run、审阅报告并检查每周同步。
分支保护可要求 **Fork checks**，平台构建与安装仍需另行验收。
