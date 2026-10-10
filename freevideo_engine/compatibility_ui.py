"""Small native compatibility controls shared by the EXE and portable launcher."""
from pathlib import Path

from .compatibility import Store, installed_identity, check_installation, LEVELS


def notify(window, root, zh=True):
    from tkinter import messagebox
    root = Path(root)
    if not (root/'machine.json').is_file():
        return
    try:
        value = check_installation(root)
        if value.get('notice'):
            level = value['level']
            messagebox.showinfo('FreeVideo',
                ('上次生成未正常结束，已自动开启兼容性设置（第 %d 档）。\n'
                 '可在“兼容性设置”中调节或关闭。分辨率、时长和步数保持不变。' % level) if zh else
                ('The previous generation ended unexpectedly. Compatibility level %d is now enabled.\n'
                 'You can adjust or disable it in Compatibility settings. Resolution, duration and steps are unchanged.' % level), parent=window)
            Store(root).acknowledge(installed_identity(root), value['notice']['id'])
    except Exception as error:
        messagebox.showerror('FreeVideo', str(error), parent=window)


def open_settings(window, root, zh=True):
    import tkinter as tk
    from tkinter import ttk, messagebox
    root = Path(root)
    try:
        identity = installed_identity(root)
        state = check_installation(root)
    except Exception as error:
        messagebox.showerror('FreeVideo', str(error), parent=window)
        return
    if not state['available']:
        messagebox.showinfo('FreeVideo',
            '尚未识别到设备信息，兼容性设置暂不可用。可继续正常使用 FreeVideo。' if zh else
            'Device information is unavailable. Compatibility settings are disabled; you can continue using FreeVideo.',
            parent=window)
        return
    dialog = tk.Toplevel(window)
    dialog.title('FreeVideo · '+('兼容性设置' if zh else 'Compatibility'))
    dialog.transient(window)
    dialog.geometry('520x330')
    body = ttk.Frame(dialog, padding=24); body.pack(fill='both', expand=True)
    text = tk.StringVar()
    level = tk.DoubleVar(value=state['level'])
    automatic = tk.BooleanVar(value=state['automatic'])
    ttk.Label(body, textvariable=text, anchor='center').pack(fill='x', pady=(8, 14))
    def changed(value=None):
        selected = int(round(level.get()))
        text.set(LEVELS[selected]['zh' if zh else 'en'])
        if value is not None and selected == 0:
            automatic.set(False)
    scale = ttk.Scale(body, from_=0, to=3, variable=level, command=changed)
    scale.pack(fill='x', pady=(0, 16)); changed()
    def released(_):
        level.set(round(level.get())); changed()
        if int(level.get()) == 0: automatic.set(False)
    scale.bind('<ButtonRelease-1>', released)
    ttk.Checkbutton(body, variable=automatic, text='异常中断后自动提高兼容档位' if zh else
                    'Raise the level automatically after an unexpected interruption').pack(anchor='w')
    ttk.Label(body, text='从下一次生成起生效。档位越高，显卡每次处理的数据越少，生成越慢，画面也会有细微差别；'
        '分辨率、时长和步数不变。' if zh else
        'Applies from the next generation. Higher levels have the GPU handle less at a time, so generation is slower '
        'and the picture differs slightly; resolution, duration and steps stay the same.',
        style='Muted.TLabel', wraplength=460).pack(fill='x', pady=16)
    def save():
        try:
            Store(root).set(identity, int(round(level.get())), automatic.get())
            dialog.destroy()
        except Exception as error:
            messagebox.showerror('FreeVideo', str(error), parent=dialog)
    ttk.Button(body, text='保存' if zh else 'Save', command=save, style='Primary.TButton').pack(anchor='e')
