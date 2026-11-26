import unittest
from unittest.mock import MagicMock

from src.data.feature_list import FeatureList
from src.tasks.onetime.claim_daily_task import ClaimDailyTask


class TestClaimMail(unittest.TestCase):
    def test_timeout_continues_without_claim_confirmation(self):
        task = object.__new__(ClaimDailyTask)
        task.ui_ensure = MagicMock()
        task.loop = MagicMock(return_value=iter([None, None]))
        task.find_one = MagicMock(return_value="claim button")
        task.click = MagicMock()
        task.sleep = MagicMock()
        task._sub_task_cfg = MagicMock(return_value=False)

        with self.assertLogs("src.tasks.onetime.claim_daily_task", level="WARNING"):
            task.claim_mail()

        task.loop.assert_called_once_with(time_out=5, raise_if_time_out=False)
        self.assertEqual(task.click.call_count, 2)
        self.assertEqual(task.find_one.call_args.args[0], FeatureList.mail_button_claim_all)


if __name__ == "__main__":
    unittest.main()
