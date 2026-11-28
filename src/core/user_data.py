"""由本项目功能自行持久化的数据路径，均相对于安装目录。"""

from pathlib import Path

USER_DATA = Path("user_data")
CHAR_DIR = USER_DATA / "char"
LEGACY_CHAR_DIR = USER_DATA / "chara"
ACCOUNT_OVERRIDES_FILE = USER_DATA / "configs" / "account_scoped_overrides.json"
REPORTS_DIR = USER_DATA / "reports"
