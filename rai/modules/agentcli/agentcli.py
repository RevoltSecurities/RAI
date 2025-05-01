import asyncio
from typing import Dict, Union, List, Optional
from rich.console import Console
from rich.prompt import Prompt
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
import os
from rich.table import Table
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory 
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI 
from prompt_toolkit.styles import Style 
from rai.modules.agentbuilder.agentbuilder import AgentBuilder, Agent, Team

class CommandHistory:
    def __init__(self, max_size: int = 1000):
        self.history: List[str] = []
        self.max_size = max_size

    def add(self, command: str):
        if command and (not self.history or command != self.history[-1]):
            self.history.append(command)
            if len(self.history) > self.max_size:
                self.history.pop(0)

    def __iter__(self):
        return iter(self.history)

    def __len__(self):
        return len(self.history)

class AgentCLICompleter(Completer):
    
    def __init__(self, agent_cli_instance):
        self.cli = agent_cli_instance 

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        words = text.split()

        
        if text.startswith(self.cli.command_prefix) and len(words) <= 1:
            command_prefix = words[0] if words else self.cli.command_prefix
            for cmd in self.cli.help_commands.keys():
                if cmd.startswith(command_prefix):
                    yield Completion(cmd, start_position=-len(command_prefix))

        elif len(words) > 1 and words[0] in (f"{self.cli.command_prefix}agents", f"{self.cli.command_prefix}teams"):
            command = words[0]
            arg_prefix = words[-1].lower()

            entity_type_filter = "Agent" if command == f"{self.cli.command_prefix}agents" else "Team"

            for name, entity in self.cli.available_entities.items():
                entity_type = "Team" if isinstance(entity, Team) else "Agent"
                if entity_type.lower() == entity_type_filter.lower() and name.lower().startswith(arg_prefix):
                    yield Completion(name, start_position=-len(arg_prefix))

class AgentCLI:
    def __init__(self, agent_builder: 'AgentBuilder'):
        self.console = Console()

        self.pt_history = InMemoryHistory()
        self.my_command_history = CommandHistory()

        self.agent_builder = agent_builder

        self.current_entity: Union[Agent, Team, None] = None
        self.available_entities: Dict[str, Union[Agent, Team]] = {}
        self.session_active = True
        self.command_prefix = "/" # Prefix for commands
        self.user = os.getlogin()

        self.help_commands = {
            f"{self.command_prefix}help": "Show this help message",
            f"{self.command_prefix}list": "List all available agents and teams",
            f"{self.command_prefix}agents": "List all available agents, or switch with /[name]",
            f"{self.command_prefix}teams": "List all available teams, or switch with /[name]",
            f"{self.command_prefix}exit": "Exit the shell",
            f"{self.command_prefix}history": "Show command history",
            f"{self.command_prefix}clear": "Clear screen",
        }

        self.session = PromptSession(
            history=self.pt_history, 
            completer=AgentCLICompleter(self),
        )

    async def initialize(self):
        try:
            
            self.available_entities.update(self.agent_builder._builded_agents)
            self.available_entities.update(self.agent_builder._builded_teams)

            self.show_welcome()
            await self.run_cli()

        except Exception as e:
            self.console.print(f"[bold red]An unexpected error occurred in shell initialization due to: {str(e)}[/bold red]")
            import traceback
            self.console.print(traceback.format_exc()) 
        finally:
            await self.agent_builder.disconnect_tools()
            self.console.print("[bold white]Closing RAI⚡ CLI Session[/bold white]")

    def show_welcome(self):
        welcome_msg = Text()
        welcome_msg.append("LLM Agent Communication Shell\n", style="bold cyan")
        welcome_msg.append("Commands:\n\n", style="bold green")
        for cmd, desc in self.help_commands.items():
            welcome_msg.append(f"  {cmd}: {desc}\n", style="bold yellow")
        welcome_msg.append("\nStart typing to chat or use commands to switch agents/teams", style="bold white italic")
        welcome_msg.append("\nPress Tab for command/entity suggestions.", style="bold white italic")
        welcome_msg.append("\nUse Up/Down arrows to navigate history.", style="bold white italic")


        self.console.print(Panel.fit(welcome_msg, title="Welcome to RAI⚡ CLI's Help Guide ", border_style="bold white"))
    async def list_entities(self, entity_type: str = "all"):
        if not self.available_entities:
            self.console.print("[bold yellow]No agents or teams available[/bold yellow]")
            return

        title = "Available Entities"
        if entity_type != "all":
            title = f"Available {entity_type.capitalize()}s"

        table = Table(title=title, show_lines=True)
        table.add_column("Name", style="bold cyan", no_wrap=True)
        table.add_column("Type", style="bold magenta")
        table.add_column("Description", style="bold green")
        table.add_column("Model", style="bold yellow")

        for name, entity in self.available_entities.items():
            current_entity_type = "Team" if isinstance(entity, Team) else "Agent"

            if entity_type != "all" and entity_type.lower() != current_entity_type.lower():
                continue

            description = getattr(entity, 'description', None) or getattr(entity, 'instructions', None)
            display_description = (description or "").split('\n')[0][:60] + "..." if description else ""

            model = getattr(entity.model, 'id', None)

            table.add_row(
                name,
                current_entity_type,
                display_description,
                model
            )

        self.console.print(table)

    def build_prompt_pt(self) -> ANSI:

        style_codes = {
            "bold": "1",
            "red": "31",
            "green": "32",
            "yellow": "33",
            "blue": "34",
            "magenta": "35",
            "cyan": "36",
            "white": "37",
            "dim": "2",
            "ansired": "31", 
            "ansigreen": "32", 
            "ansicyan": "36", 
            "ansibrightred": "91", 
            "ansibrightgreen": "92", 
            "ansibrightcyan": "96",
        }

        prompt_parts = []
        if self.current_entity:
            entity_type = "Team" if isinstance(self.current_entity, Team) else "Agent"
            prompt_parts.append(f"\033[{style_codes.get('ansibrightgreen', '')};{style_codes.get('bold', '')}m{entity_type}:\033[0m")
            prompt_parts.append(f"\033[{style_codes.get('ansibrightcyan', '')};{style_codes.get('bold', '')}m {self.user}㉿{self.current_entity.name}\033[0m")
            prompt_parts.append(f"\033[{style_codes.get('ansibrightcyan', '')};{style_codes.get('bold', '')}m > \033[0m")
        else:
            prompt_parts.append(f"\033[{style_codes.get('ansibrightcyan', '')};{style_codes.get('bold', '')}m {self.user}㉿rai > \033[0m")

        return ANSI("".join(prompt_parts))

    async def run_cli(self):
        while self.session_active:
            try:
               
                user_input = await self.session.prompt_async(
                    self.build_prompt_pt(), 
                )

                self.my_command_history.add(user_input)

                if user_input.startswith(self.command_prefix):
                    await self.process_command(user_input)
                else:
                    await self.process_prompt(user_input)

            except (EOFError, KeyboardInterrupt):
                self.console.print("\n[bold white]Exiting RAI⚡...[/bold white]")
                self.session_active = False
            except Exception as e:
                self.console.print(f"[bold red]An error occurred in CLI Session due to: {str(e)}[/bold red]")
                import traceback
                self.console.print(traceback.format_exc()) 


    async def process_command(self, command: str):
        
        parts = command[len(self.command_prefix):].strip().split(maxsplit=1)
        if not parts:
            return 
        command_name = parts[0].lower() 
        args = parts[1].strip() if len(parts) > 1 else None 

        if command_name == "exit":
            self.session_active = False
        elif command_name == "help":
            self.show_help()
        elif command_name == "list":
            await self.list_entities()
        elif command_name == "history":
            self.show_history()
        elif command_name == "clear":
            self.console.clear() 
        elif command_name == "agents":
            if args:
                await self.switch_entity(args, entity_type="Agent")
            else:
                await self.list_entities(entity_type="Agent")
        elif command_name == "teams":
            if args:
                await self.switch_entity(args, entity_type="Team")
            else:
                await self.list_entities(entity_type="Team")
        else:
            self.console.print(f"[bold yellow]Unknown command: /{command_name}[/bold yellow]")
            self.show_help() 

    async def switch_entity(self, entity_name: str, entity_type: Optional[str] = None):
        normalized_entity_name = entity_name.lower()
        found_entity = None
        found_name = None 

        for name, entity in self.available_entities.items():
            if name.lower() == normalized_entity_name:
                found_entity = entity
                found_name = name
                break 

        if found_entity:
            if entity_type:
                actual_type = "Team" if isinstance(found_entity, Team) else "Agent"
                if actual_type.lower() != entity_type.lower():
                    self.console.print(f"[bold red]{entity_name} is not a {entity_type}[/bold red]")
                    return 

            self.current_entity = found_entity
            current_entity_type = "Team" if isinstance(self.current_entity, Team) else "Agent"
            self.console.print(
                Panel.fit(
                    f"Switched to [bold cyan]{found_name}[/bold cyan] ({current_entity_type})",
                    border_style="green"
                )
            )
        else:
            self.console.print(f"[bold red]Unknown team or agent: {entity_name}[/bold red]")
            await self.list_entities(entity_type if entity_type else "all")

    async def process_prompt(self, prompt: str):
        if not self.current_entity:
            self.console.print("[bold yellow]No agent/team selected. Use /agents or /teams to select one.[/bold yellow]")
            await self.list_entities() 
            return

        if not prompt.strip():
            return

        try:
            self.console.print(
                Panel.fit(
                    prompt,
                    title=f"[bold]{self.user}[/bold] → [bold cyan]{self.current_entity.name}[/bold cyan]",
                    title_align="left",
                    border_style="bold white"
                )
            )
            await self.current_entity.aprint_response(
                prompt,
                stream=True, 
                markdown=True, 
                show_reasoning=True
            )
        except Exception as e:
            self.console.print(f"[bold red]Error responsing with {self.current_entity.name} due to : {str(e)}[/bold red]")
            import traceback
            self.console.print(traceback.format_exc()) 

    def show_help(self):
        help_table = Table(title="Available Commands", show_header=True, style="bold white")
        help_table.add_column("Command", style="bold cyan")
        help_table.add_column("Description", style="bold green")

        for cmd, desc in self.help_commands.items():
            help_table.add_row(cmd, desc)

        self.console.print(help_table)
        self.console.print("\nType / followed by Tab for command/entity suggestions.", style="bold white italic")
        self.console.print("Use Up/Down arrows to navigate history.", style="bold white italic")


    def show_history(self):
        if not self.my_command_history:
            self.console.print("[bold yellow]No history yet[/bold yellow]")
            return

        history_table = Table(title="Command History", show_lines=True, style="bold white")
        history_table.add_column("#", style="dim")
        history_table.add_column("Input", style="bold green")

        for i, item in enumerate(self.my_command_history, 1):
            display_item = item[:100] + "..." if len(item) > 100 else item
            history_table.add_row(str(i), display_item)

        self.console.print(history_table)
