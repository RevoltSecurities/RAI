from pathlib import Path
from appdirs import user_config_dir

class Config:
    def __init__(self, app_name: str):
        self.app_name = app_name
        if not self.app_name:
            raise ValueError("App name is required for your application configurations")
        self._ensure_config_dir_exists()
        self._ensure_config_files_exist()

    def _ensure_config_dir_exists(self) -> None:
        if not self.config_dir.exists():
            self.config_dir.mkdir(parents=True, exist_ok=True)
    @property
    def config_dir(self) -> Path:
        return Path(user_config_dir(self.app_name))

    def agent_config(self) -> Path:
        return self.config_dir / "raiconfig.yaml"

    def agent_db(self) -> Path:
        return self.config_dir / "agentic_session.db"

    def team_db(self) -> Path:
        return self.config_dir / "team_session.db"

    def user_db(self) -> Path:
        return self.config_dir / "user_memories.db"

    def shared_session_db(self) -> Path:
        return self.config_dir / "shared_session.db"

    def get_custom_config_path(self, filename: str) -> Path:
        return self.config_dir / filename
    
    def _ensure_config_files_exist(self) -> None:
        files = [
            self.agent_config(),
            self.agent_db(),
            self.team_db(),
            self.user_db(),
            self.shared_session_db(),
        ]
        for file in files:
            if not file.exists():
                if file.suffix == '.yaml':
                    file.write_text("# Default configuration\n")
                else:
                    file.touch()
