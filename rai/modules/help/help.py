from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich import box


class Help:
    def __init__(self):
        self.console = Console()

    def help(self):
        description = Text(
            "RAI - A next-gen framework to automate the creation of intelligent agents & teams\n"
            "for cybersecurity and offensive operations",
            style="bold white", justify="center"
        )
        self.console.print(description, justify="center")
        self.console.print()

        self._print_table_section(
            "[CORE]",
            ["Flag", "Description"],
            [
                ("-h, --help", "show this help message and exit"),
                ("-v, --version", "show the current RAI version"),
                ("-cp, --config-path", "path to YAML config (default: ~/.config/RAI/raiagent.yaml)"),
                ("-sup, --show-updates", "show recent updates and changelog"),
                ("-up, --update", "update RAI to the latest version"),
                ("-V, --verbose","increase the verbosity in building LLM agents and Teams" )
            ]
        )

        self._print_table_section(
            "[INTERFACE]",
            ["Flag", "Description"],
            [
                ("-gui, --gui-config", "launch GUI to build agents, teams & memory"),
                ("-web, --web-api", "start RAI's Web/API interface"),
                ("-host, --host", "set host for Web/API (default: 127.0.0.1)"),
                ("-port, --port", "set port for Web/API (default: 7777)")
            ]
        )

        self.console.print(
            Text("\nExample Usage:", style="bold #4682B4"),
            Text("\n  rai -gui", style="white"),
            Text("  # Launch the RAI configuration GUI with default yaml file", style="italic #A9A9A9"),
            Text("\n  rai -gui -cp agents.yaml", style="white"),
            Text("  # Launch the RAI configuration GUI with custom yaml file", style="italic #A9A9A9"),
            Text("\n  rai -web -host 127.0.0.1 -port 8080", style="white"),
            Text("  # Start the Web/API interface on all interfaces port 8080", style="italic #A9A9A9"),
            Text("\n  rai -cp /path/to/custom_config.yaml", style="white"),
            Text("  # Run RAI with a custom configuration file", style="italic #A9A9A9")
        )

    def _print_table_section(self, header, columns, rows):
        self.console.print(
            Text(header, style="bold #4682B4") + Text(":", style="bold white"),
            style="bold white"
        )
        
        table = Table(
            box=box.ROUNDED,
            show_header=True,
            header_style="bold #4682B4",
            border_style="#4682B4",
            show_edge=True,
            pad_edge=True,
            show_lines=False
        )
        
        for col in columns:
            table.add_column(col, style="bold white", no_wrap=False)
        
        for row in rows:
            table.add_row(*[Text(cell, style="white") for cell in row])
        
        self.console.print(table)
        self.console.print()