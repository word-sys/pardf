a = Analysis(
    ['run_pardf.py'],
    pathex=[],
    binaries=[],
    datas=[('word_sys_pdf_editor/img', 'word_sys_pdf_editor/img')],
    hiddenimports=['gi.repository.Gtk', 'gi.repository.Gio', 'gi.repository.GLib', 'gi.repository.Adw', 'gi.repository.Gdk', 'gi.repository.GdkPixbuf', 'gi.repository.Pango', 'gi.repository.PangoCairo', 'cairo'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='word-sys-pdf-editor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='word-sys-pdf-editor',
)
