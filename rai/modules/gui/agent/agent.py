from rai.modules.gui.colorutils.colorutils import COLORS
from rai.modules.gui.guiutils.guiutils import MultilineStr
from rai.modules.gui.button.button import ModernButton
from rai.modules.gui.tools.tools import ToolDialog
from rai.modules.gui.providers.providers import LLMS
from customtkinter import CTkToplevel,CTkScrollableFrame,CTkLabel,StringVar,CTkOptionMenu,CTkEntry,CTkTextbox,CTkFrame,CTkCheckBox
from tkinter import messagebox
from collections import OrderedDict


class AgentDialog(CTkToplevel):
    def __init__(self, master, save_callback):
        super().__init__(master)
        self.save_callback = save_callback
        self.tools = []
        self.title("➕ Add Agent")
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

        CTkLabel(main_frame, text="🛡️ New Agent Configuration", 
                 font=("Arial", 18, "bold"), text_color=COLORS["accent"]).pack(pady=10)

        self.entries = OrderedDict()
        fields = [
            ("name", "🔖 Name"),
            ("model-id", "🆔 Model ID"),
            ("apikey", "🔑 API Key"),
            ("role", "👤 Role")
        ]
        
        CTkLabel(main_frame, text="🤖 Model", text_color=COLORS["text"]).pack(pady=2)
        self.model_var = StringVar(value="gemini") # Here gemini will be default for users
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
            # To prevent API keys visualizing in GUI
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

        CTkLabel(main_frame, text="📝 Description", text_color=COLORS["text"]).pack(pady=5)
        self.description = CTkTextbox(
            main_frame, 
            height=100,
            fg_color=COLORS["darker"],
            text_color=COLORS["text"],
            border_width=1, 
            border_color=COLORS["border_light"],
            corner_radius=8
        )
        self.description.pack(pady=5, fill="both")

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


        # Adding Checkboxes in grid format
        
        checkbox_frame = CTkFrame(main_frame, fg_color=COLORS["dark"])
        checkbox_frame.pack(fill="x", pady=10)
        
        self.enable_history = CTkCheckBox(
            checkbox_frame, 
            text="📚 Enable History",
            text_color=COLORS["text"]
        )
        self.enable_history.pack(side="left", padx=10)
        self.enable_history.select()
        
        self.think =  CTkCheckBox(
            checkbox_frame, 
            text="💭 Agent Think",
            text_color=COLORS["text"]
        )
        
        self.think.pack(side="left", padx=10)
        self.think.select()
        
        self.markdown = CTkCheckBox(
            checkbox_frame, 
            text="📝 Enable Markdown",
            text_color=COLORS["text"]
        )
        self.markdown.pack(side="left", padx=10)
        self.markdown.select()

        # Buttons for adding tools and saving agents 
        
        button_frame = CTkFrame(main_frame, fg_color=COLORS["dark"])
        button_frame.pack(fill="x", pady=10)
        
        ModernButton(
            button_frame, 
            text="➕ Add Tool", 
            command=self.add_tool
        ).pack(side="left", padx=5)
        
        ModernButton(
            button_frame, 
            text="💾 Save Agent", 
            command=self.save_agent
        ).pack(side="right", padx=5)

    def add_tool(self):
        ToolDialog(self, self.tools.append)

    def save_agent(self):
        agent = OrderedDict()
        agent["name"] = self.entries["name"].get()
        agent["model"] = self.model_var.get()  
        agent["model-id"] = self.entries["model-id"].get()
        agent["apikey"] = self.entries["apikey"].get()
        agent["role"] = self.entries["role"].get()
        
        description = self.description.get("1.0", "end").strip()
        instructions = self.instructions.get("1.0", "end").strip()
        
        agent["description"] = MultilineStr('\n'.join(line.rstrip() for line in description.split('\n')))
        agent["instructions"] = MultilineStr('\n'.join(line.rstrip() for line in instructions.split('\n')))
        
        if self.tools:
            agent["tools"] = self.tools
        
        agent["enable_history"] = bool(self.enable_history.get())
        agent["think"] = bool(self.think.get())
        agent["markdown"] = bool(self.markdown.get())
        
        try:
            agent["num_of_interactions_from_history"] = int(self.num_interactions_entry.get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Interactions from History must be a number.")
            return

        self.save_callback(agent)
        self.destroy()