from rai.modules.agentbuilder.agentbuilder import AgentBuilder
from rai.modules.agentcli.agentcli import AgentCLI
from rai.modules.logger.logger import Logger
from rai.modules.cli.cli import CLI
from rai.modules.config.config import Config
from rai.modules.banner.banner import Banner
from rai.modules.gitutils.gitutils import GitUtils
from rai.modules.help.help import Help
import asyncio
import tempfile

class RAI:
    def __init__(self):
        self.logger = Logger()
        self.args = CLI().cli()
        self.banner = Banner("RAI")
        self.configures = Config("RAI")
        self.file_path = self.configures.agent_config()
        self.gitutils = GitUtils("RevoltSecurities/RAI", "rai", tempfile.gettempdir())

    
    async def check_version(self):
        gitcurrent = "v1.0.0"
        gitversion = await self.gitutils.git_version()
        if not gitversion:
            self.logger.warn("unable to get the latest version of RAI")
            return
        
        if gitversion == gitcurrent:
            print(f"[{self.logger.blue}{self.logger.bold}version{self.logger.reset}]:{self.logger.bold}{self.logger.white}RAI current version {gitversion} ({self.logger.green}latest{self.logger.reset}{self.logger.bold}{self.logger.white}){self.logger.reset}")
        else:
            print(f"[{self.logger.blue}{self.logger.bold}version{self.logger.reset}]:{self.logger.bold}{self.logger.white}RAI current version {gitversion} ({self.logger.red}latest{self.logger.reset}{self.logger.bold}{self.logger.white}){self.logger.reset}")
        print("\n")
        return
    
    async def show_updates(self):
        await self.gitutils.show_update_log()
        return
    
    async def update(self):
        pypiversion = "1.0.0"
        gitcurrent = "v1.0.0"

        gitversion = await self.gitutils.git_version()
        if not gitversion:
            self.logger.warn("unable to get the latest version of RAI")
            return
        
        if gitversion == gitcurrent:
            self.logger.info("RAI is already in latest version")
            return
        
        zipurl = await self.gitutils.fetch_latest_zip_url()
        if not zipurl:
            self.logger.warn("unable to get the latest source code of RAI")
            return
        
        await self.gitutils.download_and_install(zipurl)

        newpypi = self.gitutils.current_version()
        if newpypi == pypiversion:
            self.logger.warn("unable to update RAI to the latest version, please try manually")
            return

        self.logger.info(f"RAI has been updated to version")
        await self.show_updates()
        return

    
    async def start(self, config_file: str = None):
        try:

            if self.args.config_path: # config can be overriden by command line argument, used as pkg or used as tool
                self.file_path = self.args.config_path

            if config_file:
                self.file_path = config_file

            agentobj = AgentBuilder(self.file_path)
            await agentobj.Load_Config()
            await agentobj.Build_All_Agents()
            await agentobj.Build_All_Teams()
            shell = AgentCLI(agentobj)
            await shell.initialize()
        except FileNotFoundError:
            self.logger.error("Config file not found, please check the path exists.")
        except Exception as e:
            self.logger.error(f"Error occurred in the RAI execution due to: {e}")
        finally:
            if agentobj:
                await agentobj.disconnect_tools()

    async def run(self):
        try:
            self.banner.render()
            if self.args.help:
                Help().help()
                return
                
            if self.args.version:
                self.logger.info("Version: v1.0.0")
                return
            if self.args.update:
                await self.update()
                return  
            if self.args.show_updates:
                await self.show_updates()
                return
            await self.check_version()
            await self.start()
        except Exception as e:
            self.logger.error(f"unable to create a runner for RAI due to: {e}")
        
def main():
    asyncio.run(RAI().run())

if __name__ == "__main__":
    main()