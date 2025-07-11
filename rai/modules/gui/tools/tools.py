from rai.modules.gui.colorutils.colorutils import COLORS
from rai.modules.gui.button.button import ModernButton
from customtkinter import CTkToplevel,CTkLabel,StringVar,CTkOptionMenu,CTkEntry,CTkTextbox,CTkFrame
from typing import OrderedDict
import json 
from tkinter import messagebox

class ToolDialog(CTkToplevel):
    def __init__(self, master, callback):
        super().__init__(master)
        self.callback = callback
        self.title("➕ Add Tool")
        self.geometry("800x950")
        self.configure(fg_color=COLORS["darkest"])
        
        main_frame = CTkFrame(
            self, 
            fg_color=COLORS["dark"],
            border_width=1, 
            border_color=COLORS["border_light"], 
            corner_radius=10 
        )
        main_frame.pack(padx=20, pady=20, fill="both", expand=True)
        
        CTkLabel(main_frame, text="🔧 Tool Type:", text_color=COLORS["text"]).pack(pady=5)
        self.tool_type = StringVar(value="stdio")
        self.type_option = CTkOptionMenu(
            main_frame, 
            values=["stdio", "sse", "streamable-http"], 
            variable=self.tool_type, 
            command=self.render_fields,
            fg_color=COLORS["darker"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            text_color=COLORS["text"],
            dropdown_fg_color=COLORS["darker"],
            dropdown_hover_color=COLORS["dropdown_item_hover"],
            dropdown_text_color=COLORS["text"],
            corner_radius=8 
        )
        self.type_option.pack(pady=5)

        self.dynamic_frame = CTkFrame(main_frame, fg_color=COLORS["dark"])
        self.dynamic_frame.pack(fill="both", expand=True, pady=10)
        self.render_fields("stdio") 

        ModernButton(main_frame, text="💾 Save Tool", command=self.collect_data).pack(pady=10)
    
    def collect_data(self):
        tool = OrderedDict()
        tool["type"] = self.tool_type.get()
        
        if tool["type"] == "stdio":
            tool["name"] = "custom-stdio-tool"
            args_list = [arg.strip() for arg in self.args_entry.get().split(",") if arg.strip()]
            
            env_data = {}
            env_input = self.env_text.get("1.0", "end").strip()
            if env_input:
                try:
                    # Attempt to parse as JSON, allowing single quotes for adding Environment variables to STDIO tools
                    
                    env_data = json.loads(env_input.replace("'", '"'))
                except json.JSONDecodeError as e:
                    messagebox.showerror("Input Error", f"Invalid JSON format for Environment Variables: {e}")
                    return
            
            tool["params"] = OrderedDict([
                ("command", self.cmd_entry.get()),
                ("args", args_list),
                ("env", env_data) # Added env here for stdio tools
            ])
            
        elif tool["type"] == "sse":
            headers_raw = self.headers_text.get("1.0", "end").strip().splitlines()
            headers = OrderedDict()
            for line in headers_raw:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip()] = value.strip()
            
            tool["name"] = "custom-sse-tool"
            tool["params"] = OrderedDict([
                ("url", self.url_entry.get()),
                ("headers", headers),
                ("timeout", int(self.timeout_entry.get())) 
            ])
            
        elif tool["type"] == "streamable-http": # Added a new tool type streamable-http
            headers_raw = self.headers_text.get("1.0", "end").strip().splitlines()
            headers = OrderedDict()
            for line in headers_raw:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip()] = value.strip()
            
            tool["name"] = "custom-streamable-http-tool"
            tool["params"] = OrderedDict([
                ("url", self.url_entry.get()),
                ("headers", headers),
                ("timeout", int(self.timeout_entry.get()))
            ])

        self.callback(tool)
        self.destroy()

    def render_fields(self, value):
        for widget in self.dynamic_frame.winfo_children():
            widget.destroy()

        if value == "stdio":
            CTkLabel(self.dynamic_frame, text="Command:", text_color=COLORS["text"]).pack(pady=(10,0))
            self.cmd_entry = CTkEntry(
                self.dynamic_frame, 
                placeholder_text="⌨️ Command (e.g., uv, pip)",
                fg_color=COLORS["darker"],
                text_color=COLORS["text"],
                border_width=1, 
                border_color=COLORS["border_light"],
                corner_radius=8 
            )
            self.cmd_entry.pack(pady=5, fill="x")
            
            CTkLabel(self.dynamic_frame, text="Arguments (comma-separated):", text_color=COLORS["text"]).pack(pady=(10,0))
            self.args_entry = CTkEntry(
                self.dynamic_frame, 
                placeholder_text="📋 Arguments (e.g., --version,install)",
                fg_color=COLORS["darker"],
                text_color=COLORS["text"],
                border_width=1, 
                border_color=COLORS["border_light"],
                corner_radius=8
            )
            self.args_entry.pack(pady=5, fill="x")

            CTkLabel(self.dynamic_frame, text="Environment Variables (JSON Dict):", text_color=COLORS["text"]).pack(pady=(10,0))
            self.env_text = CTkTextbox(
                self.dynamic_frame, 
                height=100,
                fg_color=COLORS["darker"],
                text_color=COLORS["text"],
                border_width=1, 
                border_color=COLORS["border_light"],
                corner_radius=8
            )
            
            # Just Default variables for env in stdio tools
            
            self.env_text.insert("1.0", '{\n    "TAVILY_API_KEY": "your-tavily-key",\n    "BRAVE_API_KEY": "your-brave-key"\n}')
            self.env_text.pack(pady=5, fill="both", expand=True)

        elif value in ["sse", "streamable-http"]: # Tools Common fileds for both sse and streamable-http tools
            CTkLabel(self.dynamic_frame, text="URL:", text_color=COLORS["text"]).pack(pady=(10,0))
            self.url_entry = CTkEntry(
                self.dynamic_frame, 
                placeholder_text="🌐 URL",
                fg_color=COLORS["darker"],
                text_color=COLORS["text"],
                border_width=1, 
                border_color=COLORS["border_light"],
                corner_radius=8
            )
            self.url_entry.pack(pady=5, fill="x")
            
            CTkLabel(self.dynamic_frame, text="Headers (key: value per line):", text_color=COLORS["text"]).pack(pady=(10,0))
            self.headers_text = CTkTextbox(
                self.dynamic_frame, 
                height=100,
                fg_color=COLORS["darker"],
                text_color=COLORS["text"],
                border_width=1, 
                border_color=COLORS["border_light"],
                corner_radius=8
            )
            self.headers_text.insert("1.0", "Authorization: Bearer <token>\nX-API: <token>")
            self.headers_text.pack(pady=5, fill="both", expand=True)

            CTkLabel(self.dynamic_frame, text="Timeout (seconds):", text_color=COLORS["text"]).pack(pady=(10,0))
            self.timeout_entry = CTkEntry(
                self.dynamic_frame, 
                placeholder_text="⏱️ Timeout (default 1800)",
                fg_color=COLORS["darker"],
                text_color=COLORS["text"],
                border_width=1, 
                border_color=COLORS["border_light"],
                corner_radius=8
            )
            self.timeout_entry.insert(0, "1800") 
            self.timeout_entry.pack(pady=5, fill="x")