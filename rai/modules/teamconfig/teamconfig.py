from dataclasses import dataclass
from typing import List, Optional
from rai.modules.toolconfig.toolconfig import ToolConfig

@dataclass
class TeamConfig:
    name: str
    model: str
    model_id: str
    instructions: str
    mode: str
    members: List[str]
    success_criteria: str
    think: bool
    apikey: Optional[str] = None
    tools: Optional[List[ToolConfig]] = None
    num_history_runs: int = 15
