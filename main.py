from ok import OK
from ok.util.GlobalConfig import CREATE_DESKTOP_SHORTCUT, CREATE_START_MENU_SHORTCUT, create_basic_options

from src.config import config
from src.patches.wgc_compat import install_wgc_compat


def main(*, debug=False):
    import pyappify

    install_wgc_compat()
    launch_config = config.copy()
    if debug:
        launch_config['debug'] = True

    if pyappify.pyappify_version:
        OK(launch_config).start()
        return

    shortcut_defaults = {CREATE_DESKTOP_SHORTCUT: False, CREATE_START_MENU_SHORTCUT: False}
    basic_options = create_basic_options(enable_blur=callable(launch_config.get('blur_area')))
    basic_options.default_config.update(shortcut_defaults)
    launch_config['global_configs'] = [*launch_config.get('global_configs', ()), basic_options]
    launch_config.setdefault('check_updates_on_startup', False)

    app = OK(launch_config)
    # ok-script 3.0.13 rewrites shortcut defaults during Basic Options registration.
    basic_options.default_config.update(shortcut_defaults)
    app.start()


if __name__ == '__main__':
    main()
