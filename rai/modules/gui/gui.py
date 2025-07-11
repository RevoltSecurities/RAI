import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk
import requests
from io import BytesIO
import yaml
from collections import OrderedDict
from rai.modules.gui.colorutils.colorutils import COLORS
from rai.modules.gui.guiutils.guiutils import MultilineStr, multiline_str_representer
from rai.modules.gui.button.button import ModernButton
from rai.modules.gui.agent.agent import AgentDialog
from rai.modules.gui.team.team import TeamDialog
from rai.modules.gui.memory.memory import MemoryDialog
from customtkinter import CTkLabel,CTkFrame,CTk,set_appearance_mode,set_default_color_theme,CTkImage

set_appearance_mode("dark")
set_default_color_theme("dark-blue")

class GUI(CTk):
    def __init__(self, filename: str):
        super().__init__()
        self.title("RAI - Agent & Team Builder")
        self.geometry("800x700")
        self.agents = []
        self.teams = []
        self.memory = None
        self.filename = filename
        
        self.configure(fg_color=COLORS["darkest"])
        
        header_frame = CTkFrame(self, fg_color=COLORS["darkest"], height=150)
        header_frame.pack(fill="x", padx=20, pady=10)
        
        try:
            response = requests.get("https://avatars.githubusercontent.com/u/119435129", timeout=10)
            image = Image.open(BytesIO(response.content)).convert("RGBA")
            image = image.resize((80, 80), Image.Resampling.LANCZOS)
            logo_img = CTkImage(light_image=image, dark_image=image, size=(80, 80))

            logo_label = CTkLabel(header_frame, image=logo_img, text="")
            logo_label.image = logo_img  
            logo_label.pack(side="left", padx=20)
        except Exception as e:
            print(f"Error loading logo: {e}")
            logo_label = CTkLabel(header_frame, text="⚡", font=("Arial", 40, "bold"), text_color=COLORS["accent"])
            logo_label.pack(side="left", padx=20)

        # Here we set Title and Subtitle for Main Frame
        title_frame = CTkFrame(header_frame, fg_color=COLORS["darkest"])
        title_frame.pack(side="left", fill="y", expand=True)
        
        CTkLabel(
            title_frame, 
            text="RevoltSecurities", 
            font=("Arial", 28, "bold"), 
            text_color=COLORS["accent"]
        ).pack(anchor="w")
        
        CTkLabel(
            title_frame, 
            text="RAI Configuration Builder", 
            font=("Arial", 14), 
            text_color=COLORS["subtext"]
        ).pack(anchor="w")
        
        # Title and subtitleMain content frame with border for "floating" effect
        content_frame = CTkFrame(
            self, 
            fg_color=COLORS["dark"],
            border_width=1, 
            border_color=COLORS["border_light"], 
            corner_radius=10 
        )
        content_frame.pack(padx=20, pady=10, fill="both", expand=True)
        
        # Creating Button with Modern UI colors
        button_frame = CTkFrame(content_frame, fg_color=COLORS["dark"])
        button_frame.pack(pady=40)
        
        ModernButton(
            button_frame, 
            text="➕ Add Agent", 
            command=self.open_agent_dialog,
            width=200,
            height=50,
            font=("Arial", 16)
        ).pack(pady=15)
        
        ModernButton(
            button_frame, 
            text="👥 Add Team", 
            command=self.open_team_dialog,
            width=200,
            height=50,
            font=("Arial", 16)
        ).pack(pady=15)

        ModernButton(
            button_frame, 
            text="🧠 Add Memory", 
            command=self.open_memory_dialog,
            width=200,
            height=50,
            font=("Arial", 16)
        ).pack(pady=15)
        
        ModernButton(
            button_frame, 
            text="💾 Save Configuration", 
            command=self.save_yaml,
            width=200,
            height=50,
            font=("Arial", 16)
        ).pack(pady=15)

    def open_agent_dialog(self):
        AgentDialog(self, self.agents.append)

    def open_team_dialog(self):
        TeamDialog(self, self.teams.append)

    def open_memory_dialog(self):
        # The callback function will set self.memory to the collected data
        MemoryDialog(self, lambda data: setattr(self, 'memory', data))

    def save_yaml(self):
        yaml.add_representer(OrderedDict, 
            lambda dumper, data: dumper.represent_mapping('tag:yaml.org,2002:map', data.items()))
        yaml.add_representer(MultilineStr, multiline_str_representer)

        data = OrderedDict()
        if self.agents:
            data["agents"] = self.agents
        if self.teams:
            data["teams"] = self.teams
        if self.memory: 
            data["memory"] = self.memory

        with open(self.filename, "w") as f:
            yaml.dump(
                data,
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
                width=1000,
                indent=2,
                explicit_start=True
            )
        messagebox.showinfo("💾 Saved", f"Configuration saved to {self.filename}")