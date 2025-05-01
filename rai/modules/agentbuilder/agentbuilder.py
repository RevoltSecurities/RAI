from typing import List, Dict, Any, Optional
import yaml
from dataclasses import dataclass
import asyncio
import aiofiles
from agno.agent import Agent
from agno.team.team import Team
from agno.tools.mcp import MCPTools, SSEClientParams
from mcp import StdioServerParameters
from textwrap import dedent
import nest_asyncio
from agno.tools.reasoning import ReasoningTools

from rai.modules.toolconfig.toolconfig import ToolConfig
from rai.modules.teamconfig.teamconfig import TeamConfig
from rai.modules.modelconfig.modelconfig import ModelConfig, ModelBuilder
from rai.modules.logger.logger import Logger
nest_asyncio.apply()

@dataclass
class AgentConfig:
    name: str
    model: str
    model_id: str
    instructions: str
    apikey: Optional[str] = None
    role: Optional[str] = None
    description: Optional[str] = None
    tools: List[ToolConfig] = None
    think: bool = False


class AgentBuilder():
    def __init__(self, configfile:str, logging:bool = True):
        self.logging: bool = logging
        self.configfile = configfile
        self.agents_configured: Dict[str, AgentConfig] = {}
        self.teams_configured: Dict[str, TeamConfig] = {}
        self._builded_agents: Dict[str, Agent] = {}
        self._builded_teams: Dict[str, Team] = {}
        self.active_tools: List[Any] = []
        self._config_data = None
        self.logger = Logger()
    

    async def Load_Config(self):
        try:
            async with aiofiles.open(self.configfile, mode="r") as streamr:
                data = yaml.safe_load(await streamr.read())
                self._config_data = data
        except Exception as e:
            raise e
        await self._load_agents()
        await self._load_teams()

    async def _create_tool(self, tool_config:ToolConfig) -> Any:
        if tool_config.type == "sse":
            sse_params = tool_config.params
            mcp_tool = MCPTools(
                transport="sse",
                server_params=SSEClientParams(
                    url=sse_params["url"],
                    headers=sse_params.get("headers", {})
                )
            )
            await mcp_tool.__aenter__()
            self.active_tools.append(mcp_tool)
            return mcp_tool
        
        elif tool_config.type == "stdio":
            stdio_params = tool_config.params
            mcp_tool = MCPTools(
                server_params=StdioServerParameters(
                    command=stdio_params["command"],
                    args=stdio_params.get("args", []),
                    env=stdio_params.get("env", {})                    
                )
            )
            await mcp_tool.__aenter__()
            self.active_tools.append(mcp_tool)
            return mcp_tool
        else:
            raise ValueError(f"Unknown tool type: {tool_config.type}")
        
    async def disconnect_tools(self):
        try:
            for tool in self.active_tools:
                if hasattr(tool, '__aexit__'):
                    await tool.__aexit__(None, None, None)
        except RuntimeError:
            pass
        except Exception as e:
            if self.logging:
                self.logger.error(f"Error disconnecting tools due to: {e}")
        finally:
            self.active_tools = []
        

    async def _load_agents(self):
        for agent_config in self._config_data.get("agents", []):
            agent_name = agent_config["name"]
            tools = [ToolConfig(**tool) for tool in agent_config.get("tools", [])]
            self.agents_configured[agent_name] = AgentConfig(
                name=agent_name,
                model=agent_config["model"],
                model_id=agent_config["model-id"],
                instructions=agent_config["instructions"],
                role=agent_config["role"],
                description=agent_config["description"],
                apikey=agent_config.get("apikey"),
                tools=tools,
                think=agent_config.get("think", False)
            )

    async def _load_teams(self):
        for team_config in self._config_data.get("teams", []):
            team_name = team_config["name"]
            tools = [ToolConfig(**tool) for tool in team_config.get("tools", [])]
            self.teams_configured[team_name] = TeamConfig(
                name=team_name,
                model=team_config["model"],
                model_id=team_config["model-id"],
                instructions=team_config["instructions"],
                mode=team_config["mode"],
                members=team_config["members"],
                apikey=team_config.get("apikey"),
                tools=tools,
                success_criteria=team_config["success_criteria"],
                think=team_config.get("think", False)
            )
            
    async def _get_model(self, provider:str, model_id:str, apikey:str = None):
        model_config = ModelConfig(
            provider=provider, 
            modelid=model_id,
            apikey=apikey
        )
        model = ModelBuilder.build(model_config)
        llmmodel = model
        return llmmodel
    
    async def Build_agent(self, agent_name:str):
        if agent_name in self._builded_agents:
            return self._builded_agents[agent_name]
        
        agent_configured: AgentConfig = self.agents_configured[agent_name]
        
        tools = []
        for tool_config in agent_configured.tools:
            tool = await self._create_tool(tool_config)
            tools.append(tool)

        if agent_configured.think:
            tools.append(ReasoningTools(add_instructions=True))
        llmmodel = await self._get_model(agent_configured.model, agent_configured.model_id, agent_configured.apikey)

        agent = Agent(
            name=agent_name,
            model=llmmodel,
            tools=tools,
            role=agent_configured.role,
            description=dedent(agent_configured.description),
            instructions=dedent(agent_configured.instructions),
            markdown=True
            )
        
        self._builded_agents[agent_name] = agent
        return agent
    
    async def Build_Team(self, team_name:str) -> Team:
        if team_name in self._builded_teams:
            return self._builded_teams[team_name]
        
        team_configured: TeamConfig = self.teams_configured[team_name]
        
        members = []
        for agent_name in team_configured.members:
            agent = await self.Build_agent(agent_name)
            members.append(agent)
        
        team_tools = []
        for tool_config in team_configured.tools:
            tool = await self._create_tool(tool_config)
            team_tools.append(tool)

        if team_configured.think :
            team_tools.append(ReasoningTools(add_instructions=True))
        
        team_model = await self._get_model(team_configured.model, team_configured.model_id, team_configured.apikey)

        team = Team(
            name=team_name,
            model=team_model,
            members=members,
            tools=team_tools,
            instructions=dedent(team_configured.instructions),
            enable_team_history=True,
            show_tool_calls=True,
            markdown=True,
            show_members_responses=True,
            success_criteria=team_configured.success_criteria,
        )
        self._builded_teams[team_name] = team
        return team
    
    async def Build_All_Agents(self):
        for agent_name in self.agents_configured:
            await self.Build_agent(agent_name)
    
    async def Build_All_Teams(self):
        for team_name in self.teams_configured:
            await self.Build_Team(team_name)
    