from customtkinter import CTkButton
from rai.modules.gui.colorutils.colorutils import COLORS

# Moderns Buttons configurations

class ModernButton(CTkButton):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.configure(
            fg_color=COLORS["darker"], 
            hover_color=COLORS["accent"], 
            text_color=COLORS["text"],
            border_width=2, 
            border_color=COLORS["accent"], 
            corner_radius=10 
        )