from functools import wraps

_PATCH_INSTALLED = False


def install_task_config_lock_patch():
    """Lock the task detail form and Reset button while its task runs."""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.ui.qt.tasks.tasks_page import TaskDetail

    original_refresh = TaskDetail.refresh

    @wraps(original_refresh)
    def refresh(self):
        original_refresh(self)
        if self.task is not None and self.form is not None:
            editable = not self.task.running
            self.form.setEnabled(editable)
            self.reset_button.setEnabled(editable)

    TaskDetail.refresh = refresh
    _PATCH_INSTALLED = True
