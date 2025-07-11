from rai.modules.agentbuilder.agentbuilder import AgentBuilder
from rai.modules.agentcli.agentcli import AgentCLI
from rai.modules.logger.logger import Logger
from rai.modules.cli.cli import CLI
from rai.modules.config.config import Config
from rai.modules.banner.banner import Banner
from rai.modules.gitutils.gitutils import GitUtils
from rai.modules.help.help import Help
from rai.modules.gui.gui import GUI
from rai.modules.version.version import Version
from agno.playground import Playground
from uvicorn import Server
from uvicorn import Config as Configuv
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.style import Style

import asyncio
import tempfile

class RAI:
    def __init__(self):
        self.logger = Logger()
        self.args = CLI().cli()
        self.banner = Banner("RAI")
        self.configures = Config("RAI")
        self.file_path = self.configures.agent_config()
        self.console = Console()
        self._version = Version()
        self.gitcurrent = self._version.git_version
        self.pypiversion = self._version.pypi
        self.gitutils = GitUtils("RevoltSecurities/RAI", "revolt-rai", tempfile.gettempdir())

    
    async def check_version(self):
        gitversion = await self.gitutils.git_version()
        if not gitversion:
            self.logger.warn("unable to get the latest version of RAI")
            return
        
        if gitversion == self.gitcurrent:
            print(f"[{self.logger.blue}{self.logger.bold}version{self.logger.reset}]:{self.logger.bold}{self.logger.white}RAI current version {gitversion} ({self.logger.green}latest{self.logger.reset}{self.logger.bold}{self.logger.white}){self.logger.reset}")
        else:
            print(f"[{self.logger.blue}{self.logger.bold}version{self.logger.reset}]:{self.logger.bold}{self.logger.white}RAI current version {gitversion} ({self.logger.red}outdated{self.logger.reset}{self.logger.bold}{self.logger.white}){self.logger.reset}")
        print("\n")
        return
    
    async def show_updates(self):
        await self.gitutils.show_update_log()
        return
    
    async def update(self):

        gitversion = await self.gitutils.git_version()
        if not gitversion:
            self.logger.warn("unable to get the latest version of RAI")
            return
        
        if gitversion == self.gitcurrent:
            self.logger.info("RAI is already in latest version")
            return
        
        zipurl = await self.gitutils.fetch_latest_zip_url()
        if not zipurl:
            self.logger.warn("unable to get the latest source code of RAI")
            return
        
        await self.gitutils.download_and_install(zipurl)

        newpypi = self.gitutils.current_version()
        if newpypi == self.pypiversion:
            self.logger.warn("unable to update RAI to the latest version, please try manually")
            return

        self.logger.info(f"RAI has been updated to version")
        await self.show_updates()
        return

    
    async def start(self, config_file: str = None, webui=False):
        try:

            if self.args.config_path:
                self.file_path = self.args.config_path

            if config_file:
                self.file_path = config_file

            agentobj = AgentBuilder(self.file_path,self.args.verbose)
            await agentobj.Load_Config()
            await agentobj.Build_All_Agents()
            await agentobj.Build_All_Teams()
            if webui:
                self.logger.info("Starting RAI web interface, please wait!")

                agents_for_playground = []
                teams_for_playground = []
                for agent_name, agent_obj_item in agentobj._builded_agents.items():
                    agents_for_playground.append(agent_obj_item)
                    if self.args.verbose:
                        self.logger.info(f"Adding agent to playground: {agent_name}")
                    
                for team_name, team_obj_item in agentobj._builded_teams.items():
                    teams_for_playground.append(team_obj_item)
                    if self.args.verbose:
                        self.logger.info(f"Adding team to playground: {team_name}")

                if not agents_for_playground:
                    self.logger.error("No agents were built from the configuration. The web playground cannot be started.")
                    return

                app_instance = Playground(agents=agents_for_playground, teams=teams_for_playground).get_app()
                config = Configuv(app=app_instance, host=self.args.host, port=self.args.port, reload=True)
                server = Server(config=config)
                
                endpoint_url = f"http://{self.args.host}:{self.args.port}/v1"
                doc_url = f"http://{self.args.host}:{self.args.port}/docs"
                agno_url = f"https://app.agno.com/playground/agents?endpoint={self.args.host}:{self.args.port}/v1"
                
                self.console.print(
                    
                    Panel.fit(
                        Text.from_markup(
                    f"""
                        [bold magenta]🌐 RAI Web Interface Started Successfully[/bold magenta]
                        
                        
                        [bold yellow]Instructions:[/bold yellow]
                        • Use [bold cyan]Agno Agent UI[/bold cyan] to interact with your agents and Teams in a web playground if you have privacy concern about your chats
                        • Ensure your agents are listening on the correct ports.

                        [bold green]API Endpoint:[/bold green] [cyan]{endpoint_url}[/cyan]
                        [bold green]Agno UI Link:[/bold green] [link={agno_url}]{agno_url}[/link]
                        [bold green]API Doc Link:[/bold green] [link={doc_url}]{doc_url}[/link]

                        [bold white]⚡ Tip:[/bold white] Use RAI [italic]GUI[/italic] mode to generate new agents and teams and  memory configurations
                    """,
                    style=Style(color="white")
                    ),
                    title="[bold white]RAI Web Playground[/bold white]",
                    border_style="bright_blue",
                    padding=(1, 2),
                    )
                    )
                
                await server.serve()
                
            else:
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
                return None
                
            if self.args.version:
                self.logger.info("Version: v1.0.0")
                return None
            if self.args.update:
                await self.update()
                return None
            if self.args.show_updates:
                await self.show_updates()
                return None
            if self.args.gui_config:
                if self.args.config_path:
                    self.file_path = self.args.config_path
                guiapp = GUI(self.file_path)
                guiapp.mainloop()
                return None
            
            await self.check_version()
            await self.start(webui=self.args.web_api)
        except Exception as e:
            self.logger.error(f"unable to create a runner for RAI due to: {e}")
        
def main():
    asyncio.run(RAI().run())

if __name__ == "__main__":
    main()
