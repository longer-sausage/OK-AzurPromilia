"""多账户执行上下文与账号级配置覆盖。

移植自 ok-gf2 的 ``src/tasks/AccountMixin.py``，适配 ok-ap 的结构和约定。

核心功能：
- 提供多账户轮次执行能力
- 账号级配置覆盖（通过 AccountOverrideMixin）
- 登录切换逻辑（需要子类实现 login_flow）

与 ok-gf2 的差异：
- 使用 ok-ap 的 AccountOverrideMixin 和 account_scope_store
- 登录流程需要适配 Azur Promilia 的游戏界面
- 保留 ok-ap 的多账户配置约定

使用方式：
任务类继承本 mixin 并在 ``__init__`` 中调用 ``_init_account_config()``，
然后实现 ``login_flow()`` 方法。
"""

from __future__ import annotations
import re

from src.core.account_override_mixin import AccountOverrideMixin
from src.data.feature_list import FeatureList
from src.image.hsv_config import HSVRange
from src.image.frame_processes import make_hsv_isolator
from src.tasks.account.account_scope_store import (
    resolve_account_id as _store_resolve_account_id,
)


class AccountMixin(AccountOverrideMixin):
    """为任务提供多账户轮次执行能力与账号级配置覆盖。

    使用方式：任务类继承本 mixin 并在 ``__init__`` 中调用 ``_init_account_config()``，
    然后实现 ``login_flow()``。
    """

    def _init_account_config(self):
        """注册多账户相关配置。必须在 ``super().__init__`` 之后调用。"""
        self.current_user = ""
        self.current_account_id = ""
        self._logged_in = False

        self.default_config.update(
            {
                "多账户模式": False,
                "多账户独立配置": False,
                "账号列表": "\n",
            }
        )
        self.config_description.update(
            {
                "多账户模式": (
                    "开启后按账号列表逐个切换账号执行"
                ),
                "多账户独立配置": (
                    "开启后同一任务可为不同账号使用不同参数\n"
                    "在「账号配置」页为每个账号设置要覆盖的项"
                ),
                "账号列表": (
                    "每行一个账号，切换顺序即执行顺序"
                ),
            }
        )
        if not hasattr(self, "config_type") or self.config_type is None:
            self.config_type = {}
        self.config_type["多账户模式"] = {
            "sub_configs": {True: ["多账户独立配置", "账号列表"]},
        }

    def resolve_account_id(self, username: str) -> str:
        """返回账号的稳定唯一标识（``acc_xxxxxxxxxxxx``）。

        走 ``account_scope_store`` 的注册表，账号名不变则 ID 跨会话不变；
        需要自定义 ID 规则时可重写本方法。
        """
        return _store_resolve_account_id(username, create_if_missing=True) or username

    def get_account_list(self):
        """解析配置里的账号列表，返回 ``[{"account_id": ..., "username": ...}, ...]``。"""
        account_str = self.config.get("账号列表", "")
        account_list = []
        if not account_str:
            return account_list

        for line in account_str.splitlines():
            line = line.strip()
            if not line:
                continue
            # 兼容 `账号, 密码` 旧格式，密码忽略
            username = line.split(",", 1)[0].strip() if "," in line else line
            if not username:
                self.log_info(self.tr("账号格式错误，已跳过: {line}").format(line=line))
                continue
            account_list.append(
                {
                    "account_id": self.resolve_account_id(username),
                    "username": username,
                }
            )
        return account_list

    def set_current_account(self, username: str, account_id: str):
        """设置当前账号上下文。编排器据此把失败记录与轮次日志按账号分组。

        同时把 ``config.get`` 接到账号覆盖层上，使「多账户独立配置」生效。
        """
        self.current_user = username
        self.current_account_id = account_id
        self._bind_account_aware_config_get()

    def login_flow(self, username: str):
        """切换到指定账号：回主界面 → 设置 → 登出 → 确认 → 切号 → 选账号 → 登录。

        全程用 ``wait_click_feature`` / ``wait_click_ocr``，元素缺失时只记日志不中断
        （``raise_if_not_found=False``），因此调用后**务必用 ``_logged_in`` 或主界面检测确认结果**，
        否则可能在没切成的情况下继续跑下一轮。

        Args:
            username: 要切换到的账号标识（手机号）；界面按后四位匹配。
        """
        if not (result := self.find_feature(
        feature_name=FeatureList.login_out,
            mask_function=make_hsv_isolator(HSVRange.WHITE),
        )):
            return
        self.click(result)
        if not self.active_and_send_mouse_delta(0, 0, activate=True, only_activate=True):
            self.log_error("无法激活游戏窗口，已取消登录以避免误点其他窗口")
            return False
        if not self.wait_click_feature(
            feature=FeatureList.confirm_button_2,
            settle_time=1,
            box=self.box_of_screen(0.5756, 0.6126, 0.5932, 0.6420),
        ):
            return

        if not (result := self.wait_feature(feature=FeatureList.account_switch)):
            return
        self.click_at_box(result)

        if not (
            result := self.wait_ocr(
                match=re.compile(username),
                box=self.box_of_screen(0.3702, 0.5390, 0.5035, 0.7178),
            )
        ):
            return
        self.click_at_box(result)

        if not (result := self.wait_feature(feature=FeatureList.login_in)):
            return
        self.click_at_box(result)

    def iter_multi_account_context(
        self,
        repeat_times: int = 1,
        empty_accounts_message: str | None = None,
        account_log_suffix: str = "",
    ):
        """统一多账户执行上下文。

        开启多账户模式时读取账号列表逐个切换；否则按 ``repeat_times`` 重复执行。

        Args:
            repeat_times: 非多账户模式下的执行轮数。
            empty_accounts_message: 账号列表为空时的提示文案。
            account_log_suffix: 账号启动日志的后缀文本。

        Yields:
            tuple[int, int]: 当前轮次索引（从 0 开始）和总轮数。
        """
        multi = bool(self.config.get("多账户模式", False))
        if multi:
            accounts_list = self.get_account_list()
            if not accounts_list:
                if empty_accounts_message:
                    self.log_info(empty_accounts_message, notify=True)
                return
            repeat_times = len(accounts_list)
        else:
            accounts_list = []

        for repeat_idx in range(repeat_times):
            if multi:
                account = accounts_list[repeat_idx]
                username = str(account.get("username", "")).strip()
                account_id = str(account.get("account_id", "")).strip() or username
                if not username:
                    self.log_info(
                        self.tr("第 {idx}/{total} 个账号为空，已跳过").format(
                            idx=repeat_idx + 1, total=repeat_times
                        )
                    )
                    continue

                self.set_current_account(username, account_id)
                # 账号后缀是运行时用户数据，不过 tr（防污染收集池）；模板串过 tr
                self.log_info(
                    self.tr("开始第 {idx}/{total} 个账号({suffix}){log_suffix}").format(
                        idx=repeat_idx + 1,
                        total=repeat_times,
                        suffix=username[-4:],
                        log_suffix=account_log_suffix,
                    )
                )
                self.login_flow(username)
            else:
                self.set_current_account("", "")

            yield repeat_idx, repeat_times
