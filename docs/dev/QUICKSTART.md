# 从源码运行（QUICKSTART）

本模板仅支持 **Python 3.12**，请以管理员权限启动终端。依赖管理使用 [uv](https://docs.astral.sh/uv/)。

## 1. 安装依赖

```bash
uv sync
```

## 2. 运行

```bash
# Release 版
uv run python main.py

# Debug 版（开启调试模式，输出更多识别信息）
uv run python main_debug.py
```

## 3. 新增一个一次性任务

1. 在 `src/tasks/onetime/` 下新建文件，继承 `BaseGameTask`（见 `src/tasks/onetime/ExampleTask.py`）。
2. 在 `__init__` 中设置 `name`、`description`、`icon` 与 `default_config`。
3. 实现 `run(self)`，使用 `self.wait_ocr` / `self.wait_click_feature` / `self.click_relative` 等 API。
4. 在 `src/config.py` 的 `onetime_tasks` 中注册：`["src.tasks.onetime.MyTask", "MyTask"]`。

## 4. 新增一个触发式任务

1. 在 `src/tasks/trigger/` 下新建文件，继承 `BaseGameTask, TriggerTask`（见 `src/tasks/trigger/SkipDialogTask.py`）。
2. 设置 `trigger_interval` 与 `default_config`。
3. 在 `src/config.py` 的 `trigger_tasks` 中注册。

## 5. 模板匹配

标记模板资源后（ok-script 的标注工具），在 debug 模式运行一次会生成
`src/data/FeatureList.py` 枚举。之后可用 `self.wait_feature(FeatureList.xxx)`。

## 6. 测试

```bash
./run_tests.ps1
# 或
uv run python -m unittest discover -s tests
```