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

QT_NETWORK_COMPONENT_NAMES = {
    'QtNetwork.pyd',
    'QtNetwork.so',
    'QtNetwork.abi3.so',
    'Qt6Network.dll',
    'qnetworklistmanager.dll',
    'qnetworklistmanager.so',
    'qopensslbackend.dll',
    'qopensslbackend.so',
    'qschannelbackend.dll',
    'qschannelbackend.so',
    'qcertonlybackend.dll',
    'qcertonlybackend.so',
}


def remove_unused_qt_components(bundle: Path) -> None:
    for relative in UNUSED_QT_COMPONENTS:
        path = bundle / relative
        if path.is_file() or path.is_symlink():
            path.unlink()

    internal = bundle / '_internal'
    if not internal.is_dir():
        return
    for path in internal.rglob('*'):
        if not (path.is_file() or path.is_symlink()):
            continue
        name = path.name
        is_network_library = name.startswith('libQt6Network.so')
        if is_network_library or name == 'Qt6Network.dll':
            path.unlink()
            continue
        if 'PySide6' not in path.parts:
            continue
        is_network_binding = name.startswith('QtNetwork.') and (
            name.endswith('.pyd') or name.endswith('.so'))
        if name in QT_NETWORK_COMPONENT_NAMES or is_network_binding:
            path.unlink()
