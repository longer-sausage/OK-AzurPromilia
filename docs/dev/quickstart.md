# 快速上手（QUICKSTART）

本项目仅支持 **Python 3.12**，请以管理员权限启动终端。依赖管理使用 [uv](https://docs.astral.sh/uv/)。

> 💡 **提示**：如果只需使用程序挂机或辅助，建议直接前往 [GitHub Releases](https://github.com/longer-sausage/OK-AzurPromilia/releases) 下载打包好的安装包运行，无需配置 Python 开发环境。

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

标记模板资源后（ok-script 的标注工具），在 debug 模式运行一次会生成
`src/data/feature_list.py` 枚举。之后可用 `self.wait_feature(FeatureList.xxx)`。

## 6. 测试

```bash
./run_tests.ps1
# 或
uv run python -m unittest discover -s tests
```
