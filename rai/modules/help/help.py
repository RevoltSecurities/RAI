from rai.modules.logger.logger import Logger

class Help:
    def __init__(self):
        self.logger = Logger()
        self.blue = self.logger.blue
        self.bold = self.logger.bold
        self.white = self.logger.white
        self.reset = self.logger.reset
    
    def help(self):
         print(f"""
{self.bold}{self.white}[{self.reset}{self.bold}{self.blue}DESCRIPTION{self.reset}{self.bold}{self.white}]{self.reset}: 

    {self.bold}{self.white}RAI is a next-gen CLI tool and framework to automate the creation of intelligent agents and teams for cybersecurity and offensive security operations{self.reset}

{self.bold}{self.white}[{self.reset}{self.bold}{self.blue}USAGE{self.reset}{self.bold}{self.white}]{self.reset}: 

    {self.bold}{self.white}rai [flags]{self.reset}

{self.bold}{self.white}[{self.reset}{self.bold}{self.blue}FLAGS{self.reset}{self.bold}{self.white}]{self.reset}:

    {self.bold}{self.white}-h,    --help                 :  Show this help message and exit.
    {self.bold}{self.white}-v,    --version              :  Show current version of RAI.
    {self.bold}{self.white}-cp,   --config-path          :  Path to YAML config file (default: $HOME/.config/RAI/raiagent.yaml).
    {self.bold}{self.white}-sup,  --show-updates         :  Show latest update details.
    {self.bold}{self.white}-up,   --update               :  Update RAI to the latest version (manual YAML update).{self.reset}
""")
