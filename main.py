from src.config import config
from src.patches.dpi_policy import apply_high_dpi_policy
from src.patches.startup_patches import install_startup_patches

if __name__ == '__main__':
    # 必须赶在 QApplication 创建之前：Qt 只在还没有应用实例时才接受这个设置
    apply_high_dpi_policy()
    config = config
    install_startup_patches()
    import ok
    ok = ok.OK(config)
    ok.start()
