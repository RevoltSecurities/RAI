from rai.modules.gui.colorutils.colorutils import COLORS
from rai.modules.gui.guiutils.guiutils import MultilineStr
from rai.modules.gui.button.button import ModernButton
from rai.modules.gui.tools.tools import ToolDialog
from rai.modules.gui.providers.providers import LLMS
from customtkinter import CTkToplevel,CTkScrollableFrame,CTkLabel,StringVar,CTkOptionMenu,CTkEntry,CTkTextbox,CTkFrame,CTkCheckBox
from collections import OrderedDict
from tkinter import messagebox

class TeamDialog(CTkToplevel):
    def __init__(self, master, save_callback):
        super().__init__(master)
        self.save_callback = save_callback
        self.tools = []
        self.title("➕ Add Team")
        self.geometry("800x950")
        self.configure(fg_color=COLORS["darkest"])
        
        main_frame = CTkScrollableFrame(
            self, 
            fg_color=COLORS["dark"],
            border_width=1, 
            border_color=COLORS["border_light"], 
            corner_radius=10 
        )
        main_frame.pack(padx=20, pady=20, fill="both", expand=True)

        CTkLabel(main_frame, text="👥 New Team Configuration", 
                 font=("Arial", 18, "bold"), text_color=COLORS["accent"]).pack(pady=10)

        self.entries = OrderedDict()
        fields = [
            ("name", "🔖 Team Name"),
            ("model-id", "🆔 Model ID"),
            ("apikey", "🔑 API Key")
        ]
        
        CTkLabel(main_frame, text="🤖 Model", text_color=COLORS["text"]).pack(pady=2)
        self.model_var = StringVar(value="gemini") 
        self.model_option_menu = CTkOptionMenu(
            main_frame,
            values=LLMS,
            variable=self.model_var,
            fg_color=COLORS["darker"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"], 
            text_color=COLORS["text"],
            dropdown_fg_color=COLORS["darker"], 
            dropdown_hover_color=COLORS["dropdown_item_hover"], 
            dropdown_text_color=COLORS["text"],
            corner_radius=8
        )
        self.model_option_menu.pack(pady=2, fill="x")

        for field, label in fields:
            CTkLabel(main_frame, text=label, text_color=COLORS["text"]).pack(pady=2)
            
            # Same Like Agent
            
            show_char = "*" if field == "apikey" else ""
            entry = CTkEntry(
                main_frame,
                fg_color=COLORS["darker"],
                text_color=COLORS["text"],
                border_width=1, 
                border_color=COLORS["border_light"],
                corner_radius=8,
                show=show_char 
            )
            entry.pack(pady=2, fill="x")
            self.entries[field] = entry

        # Mode selection for Team to work with Agents
        
        CTkLabel(main_frame, text="🔄 Mode", text_color=COLORS["text"]).pack(pady=2)
        self.mode_var = StringVar(value="coordinate")
        mode_menu = CTkOptionMenu(
            main_frame,
            values=["coordinate", "collaborate","route"],
            variable=self.mode_var,
            fg_color=COLORS["darker"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"], 
            text_color=COLORS["text"],
            dropdown_fg_color=COLORS["darker"], 
            dropdown_hover_color=COLORS["dropdown_item_hover"],
            dropdown_text_color=COLORS["text"],
            corner_radius=8
        )
        mode_menu.pack(pady=2, fill="x")

        CTkLabel(main_frame, text="👥 Members (comma-separated)", text_color=COLORS["text"]).pack(pady=5)
        self.members_entry = CTkEntry(
            main_frame,
            fg_color=COLORS["darker"],
            text_color=COLORS["text"],
            border_width=1, 
            border_color=COLORS["border_light"],
            corner_radius=8
        )
        self.members_entry.pack(pady=2, fill="x")

        CTkLabel(main_frame, text="📜 Instructions", text_color=COLORS["text"]).pack(pady=5)
        self.instructions = CTkTextbox(
            main_frame, 
            height=100,
            fg_color=COLORS["darker"],
            text_color=COLORS["text"],
            border_width=1, 
            border_color=COLORS["border_light"],
            corner_radius=8
        )
        self.instructions.pack(pady=5, fill="both")
        self.instructions.insert("1.0", "You are a collaborative pentesting team...")

        CTkLabel(main_frame, text="🎯 Success Criteria", text_color=COLORS["text"]).pack(pady=5)
        self.success_criteria = CTkEntry(
            main_frame,
            fg_color=COLORS["darker"],
            text_color=COLORS["text"],
            border_width=1, 
            border_color=COLORS["border_light"],
            corner_radius=8
        )
        self.success_criteria.pack(pady=2, fill="x")
        self.success_criteria.insert(0, "You have collaboratively provided a detailed and ethical penetration testing analysis.")

        CTkLabel(main_frame, text="🔢 Interactions from History (Default: 15)", text_color=COLORS["text"]).pack(pady=5)
        self.num_interactions_entry = CTkEntry(
            main_frame,
            fg_color=COLORS["darker"],
            text_color=COLORS["text"],
            border_width=1,
            border_color=COLORS["border_light"],
            corner_radius=8
        )
        self.num_interactions_entry.insert(0, "15") 
        self.num_interactions_entry.pack(pady=2, fill="x")

        checkbox_frame = CTkFrame(main_frame, fg_color=COLORS["dark"])
        checkbox_frame.pack(fill="x", pady=10)
        
        self.checkboxes = OrderedDict([
            ("enable_team_history", "📚 Enable Team History"),
            ("show_tool_calls", "🔧 Show Tool Calls"),
            ("markdown", "📝 Enable Markdown"),
            ("show_members_responses", "👥 Show Members Responses"),
            ("think", "💭 Enable Think"),
            ("enable_user_memories", "🧠 Enable User Memories")
        ])

        row1 = CTkFrame(checkbox_frame, fg_color=COLORS["dark"])
        row1.pack(fill="x")
        for key, label in list(self.checkboxes.items())[:3]:
            var = CTkCheckBox(row1, text=label, text_color=COLORS["text"])
            var.pack(side="left", padx=10)
            var.select()
            self.checkboxes[key] = var

        row2 = CTkFrame(checkbox_frame, fg_color=COLORS["dark"])
        row2.pack(fill="x")
        for key, label in list(self.checkboxes.items())[3:]:
            var = CTkCheckBox(row2, text=label, text_color=COLORS["text"])
            var.pack(side="left", padx=10)
            var.select()
            self.checkboxes[key] = var

        button_frame = CTkFrame(main_frame, fg_color=COLORS["dark"])
        button_frame.pack(fill="x", pady=10)
        
        ModernButton(
            button_frame, 
            text="➕ Add Tool", 
            command=self.add_tool
        ).pack(side="left", padx=5)
        
        ModernButton(
            button_frame, 
            text="💾 Save Team", 
            command=self.save_team
        ).pack(side="right", padx=5)

    def add_tool(self):
        ToolDialog(self, self.tools.append)

    def save_team(self):
        team = OrderedDict()
        team["name"] = self.entries["name"].get()
        team["mode"] = self.mode_var.get()
        team["model"] = self.model_var.get() 
        team["model-id"] = self.entries["model-id"].get()
        team["apikey"] = self.entries["apikey"].get()
        team["instructions"] = MultilineStr('\n'.join(line.rstrip() for line in self.instructions.get("1.0", "end").strip().split('\n')))
        team["members"] = [m.strip() for m in self.members_entry.get().split(",")]
        team["success_criteria"] = self.success_criteria.get()
        
        if self.tools:
            team["tools"] = self.tools
        
        for key, chk in self.checkboxes.items():
            team[key] = bool(chk.get())
        
        try:
            team["num_of_interactions_from_history"] = int(self.num_interactions_entry.get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Interactions from History must be a number.")
            return
        
        self.save_callback(team)
        self.destroy()