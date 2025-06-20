from dataclasses import dataclass
from typing import Optional, Any
from agno.models.aimlapi import AIMLApi
from agno.models.anthropic import Claude
from agno.models.aws import AwsBedrock
from agno.models.aws import Claude as AwsClaude
from agno.models.azure import AzureOpenAI
from agno.models.azure import AzureAIFoundry
from agno.models.cohere import Cohere
from agno.models.cerebras import Cerebras
from agno.models.cerebras import CerebrasOpenAI
from agno.models.deepinfra import DeepInfra
from agno.models.deepseek import DeepSeek
from agno.models.fireworks import Fireworks
from agno.models.google import Gemini
from agno.models.groq import Groq
from agno.models.huggingface import HuggingFace
from agno.models.ibm import WatsonX
from agno.models.internlm import InternLM
from agno.models.litellm import LiteLLM
from agno.models.litellm import LiteLLMOpenAI
from agno.models.lmstudio import LMStudio
from agno.models.meta import Llama
from agno.models.mistral import MistralChat
from agno.models.nvidia import Nvidia
from agno.models.ollama import Ollama
from agno.models.openai import OpenAIChat
from agno.models.openai.responses import OpenAIResponses
from agno.models.openrouter import OpenRouter
from agno.models.perplexity import Perplexity
from agno.models.sambanova import Sambanova
from agno.models.together import Together
from agno.models.vercel import v0
from agno.models.xai import xAI


@dataclass
class ModelConfig:
    provider: str
    modelid: str
    apikey: Optional[str] = "sk-ant-api03-1234567890"


class ModelBuilder:

    Providers = {
        "AImlapi": AIMLApi,
        "anthropic": Claude,
        "awsbedrock": AwsBedrock,
        "awsclaude": AwsClaude,
        "azureopenAI": AzureOpenAI,
        "azureAIfoundary": AzureAIFoundry,
        "cerebras": Cerebras,
        "cerebrasopenAI":CerebrasOpenAI,
        "cohere": Cohere,
        "deepinfra": DeepInfra,
        "deepseek": DeepSeek,
        "fireworks": Fireworks,
        "gemini": Gemini,
        "groq": Groq,
        "huggingface": HuggingFace,
        "ibm": WatsonX,
        "internlm": InternLM,
        "litellmopenAI": LiteLLMOpenAI,
        "litellm":LiteLLM,
        "lmstudio": LMStudio,
        "meta": Llama,
        "mistral": MistralChat,
        "nvidia": Nvidia,
        "ollama": Ollama,
        "openAI": OpenAIChat,
        "openAIResponse": OpenAIResponses,
        "openrouter": OpenRouter,
        "perplexity": Perplexity,
        "sambanova": Sambanova,
        "together": Together,
        "vercelv0": v0,
        "xAI": xAI
    }

    @classmethod
    def _get_provider(cls, provider: str) -> Any:
        provider = cls.Providers.get(provider.lower())
        if not provider:
            raise ValueError(f"LLM Provider {provider} not found")
        return provider

    @classmethod
    def build(cls, config: ModelConfig) -> Any:
        LllModel = cls._get_provider(config.provider)
        return LllModel(id=config.modelid,api_key=config.apikey)