from dataclasses import dataclass

@dataclass
class ToolConfig:
    type: str
    name: str
    params: dict