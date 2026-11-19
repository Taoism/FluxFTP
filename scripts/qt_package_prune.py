"""Remove unused optional Qt components from release bundles."""
from pathlib import Path


UNUSED_QT_COMPONENTS = (
    '_internal/PySide6/Qt/lib/libQt6Pdf.so.6',
    '_internal/PySide6/Qt/lib/libQt6VirtualKeyboard.so.6',
    '_internal/PySide6/Qt/lib/libQt6VirtualKeyboardQml.so.6',
    '_internal/PySide6/Qt/plugins/imageformats/libqpdf.so',
    '_internal/PySide6/Qt/plugins/platforminputcontexts/libqtvirtualkeyboardplugin.so',
    # PyInstaller duplicates selected Qt libraries at _internal's root.
    '_internal/libQt6Pdf.so.6',
    '_internal/libQt6VirtualKeyboard.so.6',
    '_internal/libQt6VirtualKeyboardQml.so.6',
)


def remove_unused_qt_components(bundle: Path) -> None:
    for relative in UNUSED_QT_COMPONENTS:
        path = bundle / relative
        if path.is_file() or path.is_symlink():
            path.unlink()
