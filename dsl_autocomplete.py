import tkinter as tk
from tkinter import ttk
from dsl_commands import get_command_matches, get_builtin_matches

class DslAutocomplete:
    def __init__(self, text_widget: tk.Text):
        self.text = text_widget
        self.popup = None
        self.listbox = None
        self.help_var = None
        self.matches =[]

        self.text.bind("<KeyRelease>", self.on_key_release, add="+")
        self.text.bind("<Tab>", self.on_tab, add="+")
        self.text.bind("<Down>", self.on_down, add="+")
        self.text.bind("<Up>", self.on_up, add="+")
        self.text.bind("<Return>", self.on_return, add="+")
        self.text.bind("<Escape>", self.on_escape, add="+")
        
        # Binding per far sparire il popup se l'utente clicca via
        self.text.bind("<Button-1>", lambda e: self.hide_popup(), add="+")
        self.text.bind("<FocusOut>", lambda e: self.hide_popup(), add="+")

    def get_current_token(self) -> str:
        line, col = map(int, self.text.index(tk.INSERT).split("."))
        line_text = self.text.get(f"{line}.0", f"{line}.end")

        i = col
        while i > 0 and not line_text[i - 1].isspace():
            i -= 1
        return line_text[i:col]

    def show_popup(self, items):
        self.hide_popup()
        if not items:
            return

        self.matches = items
        self.popup = tk.Toplevel(self.text)
        self.popup.wm_overrideredirect(True)

        x, y, _, h = self.text.bbox(tk.INSERT)
        abs_x = self.text.winfo_rootx() + x
        abs_y = self.text.winfo_rooty() + y + h
        self.popup.geometry(f"+{abs_x}+{abs_y}")

        # Frame contenitore con bordino
        frame = tk.Frame(self.popup, highlightbackground="gray", highlightthickness=1)
        frame.pack(fill="both", expand=True)
        # --- NUOVO: Sotto-frame per affiancare Listbox e Scrollbar ---
        list_frame = tk.Frame(frame)
        list_frame.pack(fill="both", expand=True)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        self.listbox = tk.Listbox(list_frame, width=50, height=min(8, len(items)), borderwidth=0, yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.listbox.yview)

        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        # -------------------------------------------------------------
        # self.listbox = tk.Listbox(frame, width=50, height=min(8, len(items)), borderwidth=0)
        # self.listbox.pack(fill="x")

        # Label per l'Help testuale in basso (sfondo giallino tipo tooltip)
        self.help_var = tk.StringVar()
        help_label = ttk.Label(frame, textvariable=self.help_var, wraplength=350, justify="left", background="#ffffe1", padding=4)
        help_label.pack(fill="both", expand=True)

        for item in items:
            self.listbox.insert(tk.END, item["insert"])

        self.listbox.selection_set(0)
        self._update_help_text(0)
        
        # Aggiorna l'help se l'utente clicca su un elemento della lista
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._update_help_text(self.listbox.curselection()[0] if self.listbox.curselection() else 0))

    def _update_help_text(self, idx: int):
        if 0 <= idx < len(self.matches):
            item = self.matches[idx]
            sig = item.get("signature", "")
            hlp = item.get("help", "")
            self.help_var.set(f"{sig}\n{hlp}" if sig else hlp)

    def hide_popup(self):
        if self.popup is not None:
            self.popup.destroy()
            self.popup = None
            self.listbox = None
            self.help_var = None
            self.matches =[]

    def on_key_release(self, event):
        if event.keysym in ("Up", "Down", "Return", "Tab", "Escape"):
            return

        token = self.get_current_token().strip()
        if not token:
            self.hide_popup()
            return

        if token.startswith("@") or token.startswith("/"):
            items = get_command_matches(token)
        else:
            items = get_builtin_matches(token)
            
        # SE LA PAROLA E' STATA SCRITTA PER INTERO, IL POPUP SPARISCE! (Non è più invadente)
        if items and len(items) == 1 and items[0]["command"].lower() == token.lower():
            if "\n" not in items[0].get("insert", ""):
                self.hide_popup()
                return

        if items:
            self.show_popup(items)
        else:
            self.hide_popup()

    def insert_selected(self):
        if not self.listbox or not self.matches: return False
        sel = self.listbox.curselection()
        if not sel: return False
        
        insert_text = self.matches[sel[0]]["insert"]
        line, col = map(int, self.text.index(tk.INSERT).split("."))
        line_text = self.text.get(f"{line}.0", f"{line}.end")

        i = col
        while i > 0 and not line_text[i - 1].isspace(): i -= 1

        self.text.delete(f"{line}.{i}", f"{line}.{col}")
        if "$$CURSOR$$" in insert_text:
            parts = insert_text.split("$$CURSOR$$")
            # Inserisce la prima parte (es. "@loop 10\n    ")
            self.text.insert(f"{line}.{i}", parts[0])
            # Salva la posizione attuale del cursore
            cursor_pos = self.text.index(tk.INSERT)
            # Inserisce la seconda parte (es. "\n@endloop")
            self.text.insert(cursor_pos, parts[1])
            # Riporta il cursore al centro del blocco!
            self.text.mark_set(tk.INSERT, cursor_pos)
        else:
            self.text.insert(f"{line}.{i}", insert_text)
        self.hide_popup()
        return True

    def on_tab(self, event):
        if self.popup and self.insert_selected(): return "break"
        return None

    def on_return(self, event):
        if self.popup and self.insert_selected(): return "break"
        return None

    def on_escape(self, event):
        if self.popup:
            self.hide_popup()
            return "break"
        return None

    def on_down(self, event):
        if self.listbox:
            sel = self.listbox.curselection()
            idx = min(sel[0] + 1, self.listbox.size() - 1) if sel else 0
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(idx)
            self.listbox.activate(idx)
            self.listbox.see(idx)
            self._update_help_text(idx)
            return "break"
        return None

    def on_up(self, event):
        if self.listbox:
            sel = self.listbox.curselection()
            idx = max(sel[0] - 1, 0) if sel else 0
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(idx)
            self.listbox.activate(idx)
            self.listbox.see(idx)
            self._update_help_text(idx)
            return "break"
        return None

def attach_autocomplete(text_widget: tk.Text):
    return DslAutocomplete(text_widget)