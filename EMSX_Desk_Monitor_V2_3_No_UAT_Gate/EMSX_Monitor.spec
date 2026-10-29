# 승인된 Windows x64 Python 환경에서만 실행하는 단일 파일 빌드 정의다.
from pathlib import Path
import os
import sys
import struct
import hashlib
from PyInstaller.utils.hooks import collect_all

if sys.platform != 'win32' or struct.calcsize('P') != 8:
    raise RuntimeError('Windows x64 Python으로 빌드하십시오')
root = Path(SPECPATH)
sys.path.insert(0, str(root))
from blpapi_bundle import ffiutils_entry, preserve_ffiutils
import blpapi.internals as sdk_internals
ffi_entry = ffiutils_entry(sdk_internals)
data, binaries, hidden = collect_all('blpapi')
# 의존성 분석에는 원본을 제공하고, 최종 배치에서는 ABI 파일명을 복구한다.
binaries.append((ffi_entry[1], 'blpapi'))
# SDK를 별도로 사용하는 설치도 지원한다. DLL 자체는 재배포 승인을 확인해야 한다.
sdk = os.environ.get('BLPAPI_CPP_SDK')
if sdk:
    library = Path(sdk) / 'lib'
    matches = list(library.rglob('blpapi3_64.dll'))
    if not matches:
        raise RuntimeError('BLPAPI_CPP_SDK의 lib에서 blpapi3_64.dll을 찾지 못했습니다')
    if len(matches) != 1:
        raise RuntimeError('SDK DLL 후보가 여러 개입니다. 단일 x64 SDK 경로를 지정하십시오')
    existing = [Path(source) for source, destination in binaries
                if Path(source).name.lower() == 'blpapi3_64.dll']
    for source in existing:
        if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(matches[0].read_bytes()).digest():
            raise RuntimeError('wheel과 별도 SDK의 DLL이 다릅니다. 하나의 호환 배포본으로 통일하십시오')
    if not existing:
        binaries.append((str(matches[0]), 'bloomberg'))
# 승인된 Microsoft 앱 로컬 CRT를 제공한 경우 함께 포함한다.
redist = os.environ.get('EMSX_VC_REDIST_DIR')
if redist:
    redist_path = Path(redist)
    if not redist_path.is_dir():
        raise RuntimeError('EMSX_VC_REDIST_DIR 경로를 확인하십시오')
    for name in ('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll',
                 'msvcp140_1.dll', 'msvcp140_2.dll', 'concrt140.dll'):
        runtime = redist_path / name
        if runtime.is_file():
            binaries.append((str(runtime), '.'))
asset = root / 'assets' / 'desk_alert.wav'
if not asset.is_file():
    raise RuntimeError('경보음 파일이 없습니다')
data += [(str(asset), 'assets')]
market_asset = root / 'assets' / 'market_sessions.json'
if not market_asset.is_file():
    raise RuntimeError('시장 캘린더 파일이 없습니다')
data.append((str(market_asset), 'assets'))
for icon in (root / 'assets').glob('*.ico'):
    data.append((str(icon), 'assets'))
icons = list((root / 'assets').glob('*.ico'))
a = Analysis([str(root / 'emsx_entry.py')], pathex=[str(root)],
             binaries=binaries, datas=data,
             hiddenimports=hidden + ['tkinter', 'tkinter.ttk', 'winsound', 'sqlite3'],
             runtime_hooks=[str(root / 'packaging' / 'runtime_blpapi.py')],
             excludes=[], noarchive=False)
# SDK의 ctypes 로더가 glob으로 찾는 실제 ABI 파일명을 보존한다.
a.binaries = preserve_ffiutils(a.binaries, ffi_entry)
# Python 바인딩만 있고 C++ 런타임이 빠진 빌드는 배포하지 않는다.
runtime_dlls = [Path(entry[1]) for entry in a.binaries
                if Path(entry[0]).name.lower() == 'blpapi3_64.dll']
if not runtime_dlls:
    raise RuntimeError('번들에 blpapi3_64.dll이 없습니다. 승인된 wheel 또는 SDK를 준비하십시오')
for dll in runtime_dlls:
    contents = dll.read_bytes()
    offset = struct.unpack_from('<I', contents, 0x3c)[0]
    if contents[:2] != b'MZ' or contents[offset:offset+4] != b'PE\0\0':
        raise RuntimeError('Bloomberg DLL이 유효한 Windows PE 파일이 아닙니다')
    if struct.unpack_from('<H', contents, offset+4)[0] != 0x8664:
        raise RuntimeError('Bloomberg DLL이 Windows x64 파일이 아닙니다')

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          name='KB증권 해외주식영업부 EMSX 모니터', console=False,
          debug=False, strip=False, upx=False,
          version=str(root / 'windows_version_info.txt'),
          icon=str(icons[0]) if icons else None)
