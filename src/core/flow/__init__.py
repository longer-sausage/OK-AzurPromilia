"""流程（flow）：JSON 描述的可编程自动化，比键鼠录制更高一层。

一个流程就是一棵步骤树，支持嵌套：移动 / 点击 / 执行动作 / 按键 / 等待 /
等模板出现 / 循环 / 条件。硬限制写在 :mod:`src.core.flow.model`：

- 总步数 ≤ 500（含嵌套）
- 单层循环次数 ≤ 1000
- 嵌套深度 ≤ 4

用法：

```python
from src.core.flow import Flow, FlowExecutor, FlowContext, validate_flow

flow = Flow.from_dict(json.loads(text))
issues = validate_flow(flow)
executor = FlowExecutor(flow, FlowContext(task), checkpoint=store.for_flow(flow))
result = executor.run(resume=True)
```
"""

from src.core.flow.executor import (
    ACTION_ALLOWLIST,
    CheckpointStore,
    FlowContext,
    FlowExecutor,
    FlowRunResult,
)
from src.core.flow.model import (
    FLOW_FOLDER,
    MAX_DEPTH,
    MAX_LOOP_TIMES,
    MAX_STEPS,
    Flow,
    FlowFormatError,
    FlowIssue,
    FlowStep,
    flow_path,
    list_flows,
    load_flow,
    load_flow_by_name,
    parse_flow,
    save_flow,
    validate_flow,
)
from src.core.flow.recording import RecordingConversion, convert_recording

__all__ = [
    "ACTION_ALLOWLIST",
    "FLOW_FOLDER",
    "MAX_DEPTH",
    "MAX_LOOP_TIMES",
    "MAX_STEPS",
    "CheckpointStore",
    "Flow",
    "FlowContext",
    "FlowExecutor",
    "FlowFormatError",
    "FlowIssue",
    "FlowRunResult",
    "FlowStep",
    "RecordingConversion",
    "convert_recording",
    "flow_path",
    "list_flows",
    "load_flow",
    "load_flow_by_name",
    "parse_flow",
    "save_flow",
    "validate_flow",
]
