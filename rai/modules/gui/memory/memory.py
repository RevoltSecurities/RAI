from rai.modules.gui.colorutils.colorutils import COLORS
from rai.modules.gui.button.button import ModernButton
from rai.modules.gui.providers.providers import LLMS
from customtkinter import CTkToplevel,CTkLabel,StringVar,CTkOptionMenu,CTkEntry,CTkFrame,CTkTextbox 
import uuid
from collections import OrderedDict

class MemoryDialog(CTkToplevel):
    def __init__(self, master, save_callback):
        super().__init__(master)
        self.save_callback = save_callback
        self.title("🧠 Add Memory")
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

        CTkLabel(main_frame, text="🧠 New Memory Configuration",
                 font=("Arial", 18, "bold"), text_color=COLORS["accent"]).pack(pady=10)

        # Memory Model selection Frame
        
        CTkLabel(main_frame, text="🤖 Model", text_color=COLORS["text"]).pack(pady=5)
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
        self.model_option_menu.pack(pady=5, fill="x")

        CTkLabel(main_frame, text="🆔 Model ID", text_color=COLORS["text"]).pack(pady=5)
        self.model_id_entry = CTkEntry(
            main_frame,
            fg_color=COLORS["darker"],
            text_color=COLORS["text"],
            border_width=1,
            border_color=COLORS["border_light"],
            corner_radius=8
        )
        self.model_id_entry.pack(pady=5, fill="x")

        CTkLabel(main_frame, text="🔑 API Key", text_color=COLORS["text"]).pack(pady=5)
        self.apikey_entry = CTkEntry(
            main_frame,
            fg_color=COLORS["darker"],
            text_color=COLORS["text"],
            show="*",
            border_width=1,
            border_color=COLORS["border_light"],
            corner_radius=8
        )
        self.apikey_entry.pack(pady=5, fill="x")

        CTkLabel(main_frame, text="📝 Memory Context", text_color=COLORS["text"]).pack(pady=5)
        self.memory_content_textbox = CTkTextbox(
            main_frame,
            fg_color=COLORS["darker"],
            text_color=COLORS["text"],
            border_width=1,
            border_color=COLORS["border_light"],
            corner_radius=8,
            height=150, 
            wrap="word" 
        )
        # default memory context for user
        
        default_memory_content = """
        I am an ethical hacker focused in finding vulnerabilities and report professionaly
        and bug bounty hunter in platforms like hackerone,bugcrowd,intigriti and write
        report professionaly like pentester and good in DAST, SAST"""
        
        self.memory_content_textbox.insert("0.0", default_memory_content)
        self.memory_content_textbox.pack(pady=5, fill="both", expand=True)

        ModernButton(main_frame, text="💾 Save Memory", command=self.save_memory).pack(pady=10)

    def save_memory(self):
        memory_data = OrderedDict()
        memory_data["model"] = self.model_var.get()
        memory_data["model-id"] = self.model_id_entry.get()
        memory_data["apikey"] = self.apikey_entry.get()
        memory_data["user-id"] = str(uuid.uuid4())
        memory_data["session-id"] = str(uuid.uuid4())
        memory_data["memory-context"] = self.memory_content_textbox.get("0.0", "end-1c").strip() # Strip the trailing new lines here

        self.save_callback(memory_data)
        self.destroy()