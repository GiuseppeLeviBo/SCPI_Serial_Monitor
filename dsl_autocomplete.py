import tkinter as tk

from dsl_commands import get_command_matches, get_builtin_matches


class DslAutocomplete:
    def __init__(self, text_widget: tk.Text):
        self.text = text_widget
        self.popup = None
        self.listbox = None
        self.matches = []

        self.text.bind("<KeyRelease>", self.on_key_release, add="+")
        self.text.bind("<Tab>", self.on_tab, add="+")
        self.text.bind("<Down>", self.on_down, add="+")
        self.text.bind("<Up>", self.on_up, add="+")
        self.text.bind("<Return>", self.on_return, add="+")
        self.text.bind("<Escape>", self.on_escape, add="+")

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

        self.listbox = tk.Listbox(self.popup, width=50, height=min(8, len(items)))
        self.listbox.pack()

        for item in items:
            self.listbox.insert(tk.END, item["insert"])

        self.listbox.selection_set(0)

    def hide_popup(self):
        if self.popup is not None:
            self.popup.destroy()
            self.popup = None
            self.listbox = None
            self.matches = []

    def on_key_release(self, event):
        if event.keysym in ("Up", "Down", "Return", "Tab", "Escape"):
            return

        token = self.get_current_token().strip()
        if not token:
            self.hide_popup()
            return

        if token.startswith("@"):
            items = get_command_matches(token)
        else:
            items = get_builtin_matches(token)

        if items:
            self.show_popup(items)
        else:
            self.hide_popup()

    def insert_selected(self):
        if not self.listbox or not self.matches:
            return False

        sel = self.listbox.curselection()
        if not sel:
            return False

        item = self.matches[sel[0]]
        insert_text = item["insert"]

        line, col = map(int, self.text.index(tk.INSERT).split("."))
        line_text = self.text.get(f"{line}.0", f"{line}.end")

        i = col
        while i > 0 and not line_text[i - 1].isspace():
            i -= 1

        self.text.delete(f"{line}.{i}", f"{line}.{col}")
        self.text.insert(f"{line}.{i}", insert_text)
        self.hide_popup()
        return True

    def on_tab(self, event):
        if self.popup:
            if self.insert_selected():
                return "break"
        return None

    def on_return(self, event):
        if self.popup:
            if self.insert_selected():
                return "break"
        return None

    def on_escape(self, event):
        if self.popup:
            self.hide_popup()
            return "break"
        return None

    def on_down(self, event):
        if self.listbox:
            sel = self.listbox.curselection()
            if sel:
                idx = min(sel[0] + 1, self.listbox.size() - 1)
            else:
                idx = 0
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(idx)
            self.listbox.activate(idx)
            return "break"
        return None

    def on_up(self, event):
        if self.listbox:
            sel = self.listbox.curselection()
            if sel:
                idx = max(sel[0] - 1, 0)
            else:
                idx = 0
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(idx)
            self.listbox.activate(idx)
            return "break"
        return None


def attach_autocomplete(text_widget: tk.Text):
    return DslAutocomplete(text_widget)