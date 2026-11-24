from ok import OK

from src.config import config

if __name__ == '__main__':
    OK({**config, 'debug': True}).start()
