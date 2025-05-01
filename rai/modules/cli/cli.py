from rai.modules.logger.logger import Logger
import argparse

class CLI:
    def __init__(self):
        self.logger = Logger()
        
    def cli(self) -> argparse.Namespace | None:
        try:
            parser = argparse.ArgumentParser(add_help=False, usage=argparse.SUPPRESS, exit_on_error=False)
            parser.add_argument("-h", "--help", action="store_true", help="")
            parser.add_argument("-v", "--version", action="store_true", help="")
            parser.add_argument("-cp", "--config-path", type=str, default=None, help="")
            parser.add_argument("-sup", "--show-updates", action="store_true", help="")
            parser.add_argument("-up", "--update", action="store_true", help="")
            args = parser.parse_args()
            return args
        except Exception as e:
            self.logger.error(f"Error occurred in the CLI module due to: {e}")
            return None