from enum import Enum


class FeatureList(str, Enum):
    account_switch = 'account_switch'
    close_button = 'close_button'
    confirm_button = 'confirm_button'
    confirm_button_2 = 'confirm_button_2'
    login_in = 'login_in'
    login_out = 'login_out'
    skip_confirm = 'skip_confirm'
    skip_dialog = 'skip_dialog'
