# 快速上手（QUICKSTART）

本项目仅支持 **Python 3.12**，请以管理员权限启动终端。依赖管理使用 [uv](https://docs.astral.sh/uv/)。

> 💡 **提示**：如果只需使用程序挂机或辅助，建议直接前往 [GitHub Releases](https://github.com/longer-sausage/ok-ap/releases) 下载打包好的安装包运行，无需配置 Python 开发环境。

## 1. 安装依赖

```bash
uv sync
```

## 2. 从源码运行

```bash
# Release 版
uv run python main.py

# Debug 版（开启调试模式，输出更多识别信息）
uv run python main_debug.py
```

## 3. 新增一个一次性任务

1. 在 `src/tasks/onetime/` 下新建文件，继承 `BaseGameTask`（最简范例见 `src/tasks/test/test_screenshot_task.py`，完整业务任务见 `src/tasks/onetime/claim_daily_task.py` 等）。
2. 在 `__init__` 中设置 `name`、`description`、`icon` 与 `default_config`。
3. 实现 `run(self)`，使用 `self.wait_ocr` / `self.wait_click_feature` / `self.click_relative` 等 API。
   若是「看到某东西 → 点它 → 验证结果」这类流程，用 `self.wait_action_result`（识别源适配器见
   `src/core/detector/`）；只等一个结果用 `self.wait_expectation`。详见 `development.md` 的
   「Action 生命周期」一节。
4. 在 `src/config.py` 的 `onetime_tasks` 中注册：`["src.tasks.onetime.my_task", "MyTask"]`。

## 4. 新增一个触发式任务

1. 在 `src/tasks/trigger/` 下新建文件，继承 `BaseGameTask, TriggerTask`（见 `src/tasks/trigger/skip_dialog_task.py`）。
2. 设置 `trigger_interval` 与 `default_config`。
3. 在 `src/config.py` 的 `trigger_tasks` 中注册。

## 5. 模板匹配

队伍头像在「角色管理」页截图、绑定，存入本机 `user_data/char/`。备份时一起复制 PNG 与 `bindings.json`。

项目内置的界面识别模板随代码发布。普通 `git clone` 会一并取得 `ok_templates/` 下的原始截图和 `coco_annotations.json`。内置模板的改动直接提交到本仓库。

> 如果旧工作区初始化过 `ok_templates` 子模块，首次拉取这次迁移可能遇到 `would be overwritten by merge`。
> 在主仓库根目录运行 `git -C ok_templates status --short`。若有本地改动，可先提交到自己的模板分支，或将改动另存到目录外后恢复干净工作区。
> 等状态输出为空，执行 `git submodule deinit -- ok_templates`，再执行 `git pull`。更新后可将自己的模板改动合回。

在调试版的模板页完成标注后，打开导出对话框，选择拆分模板并保存到独立应用的
`assets`。勾选生成标签枚举，输出路径填 `FeatureList.py`。框架用输出文件名决定
Python 类名，生成后在仓库根目录执行以下命令，即可通过 `self.wait_feature(FeatureList.xxx)` 使用新模板。

```powershell
Move-Item -LiteralPath FeatureList.py -Destination src/data/feature_list.py -Force
```

## 6. 测试

```bash
./run_tests.ps1
# 或
uv run python -m unittest discover -s tests
```
