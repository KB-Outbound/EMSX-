# 독립 실행 파일의 오프라인 빌드와 배포

결과 파일은 `dist\KB증권 해외주식영업부 EMSX 모니터.exe`입니다.
Python·Tk/Tcl·SQLite·Python 확장·BLPAPI 네이티브 DLL과 경보음을 단일 파일로 묶습니다.
창 제목과 내부 PROD 제목은 기존 규칙을 유지합니다. 앱 자체에는 시작 시 pip 설치·업데이트,
웹 리소스·CDN 다운로드가 없습니다. Bloomberg 데이터에는 실행 중인 Terminal과 로컬
Desktop API(`localhost:8194`), 해당 서비스·필드 권한이 필요합니다. 인터넷이 없어도
Bloomberg 세션이 필요하다는 조건은 유지됩니다.

## 빌드 PC 준비

배포 대상 Windows 버전을 지원하는 승인된 Windows x64 PC와 x64 Python 3.10 이상을 사용합니다.
Linux에서 Windows 실행 파일로 교차 빌드할 수 없습니다. Python 설치 시 Tcl/Tk를 포함하십시오.
`EMSX_BUILD_PYTHON`에는 사용할 Python 실행 파일의 전체 경로를 지정할 수 있습니다.
격리된 가상환경에서 빌드하고, Python·PyInstaller·BLPAPI 버전 및 반입 파일 해시를 내부 배포 기록에 남기십시오.

연결 가능한 승인 PC에서 같은 Windows 아키텍처·Python 버전으로 다음 명령을 실행하여
BLPAPI·PyInstaller와 그 의존성의 wheel을 준비합니다. BLPAPI는 Bloomberg가 제공한 신뢰할 수 있는
배포 경로 또는 승인된 내부 저장소의 wheel을 사용하십시오. 예시의 버전은 조직 승인 버전으로 고정해야 합니다.

```bat
python -m pip download --only-binary=:all: --dest wheelhouse -r requirements.txt
```

필요한 BLPAPI wheel이 일반 패키지 저장소에 없으면 공식 배포본을 같은 폴더에 반입합니다.
인터넷에서 무작위 DLL을 내려받거나 검증을 끄지 마십시오. wheelhouse의 해시·서명은 반입 정책에 따라 확인합니다.
소스와 wheelhouse를 빌드 PC로 옮긴 뒤 실행합니다.

```bat
prepare_offline.bat
build_windows.bat
```

`prepare_offline.bat`는 `--no-index`로 로컬 wheel만 설치합니다.
`build_windows.bat`는 패키지 설치를 하지 않고 사전 검사·기존 테스트·spec 빌드·완성 파일 자체 검사를 수행합니다.
실패 시 0이 아닌 종료 코드를 반환합니다. `dist\빌드_패키지_목록.txt`에 설치 버전 기록을 남깁니다.
동일 빌드를 재현하려면 해당 버전의 wheelhouse를 보관해야 합니다. 현재 requirements.txt는 버전 잠금 파일이 아닙니다.

## Bloomberg DLL

spec은 설치된 blpapi 패키지의 데이터·확장·동적 라이브러리를 수집합니다.
Analysis 이후 `blpapi3_64.dll`이 번들에 없으면 중단합니다.
별도의 승인된 C++ SDK가 필요한 패키지라면 `BLPAPI_CPP_SDK`를 SDK 루트로 설정하십시오.
그 아래 `lib`에서 `blpapi3_64.dll`을 찾아 `bloomberg` 폴더로 포함합니다.
빌드 Python과 SDK의 아키텍처·버전이 호환되어야 하며 필요한 DLL의 종속 파일도 빌드 환경에서 로드되어야 합니다.
PyInstaller가 발견하는 네이티브 종속성을 수집하지만 모든 Windows 구성에서의 호환성을 보장하지는 않습니다.

런타임 훅이 번들 루트·blpapi·bloomberg 폴더를 DLL 검색 경로에 등록합니다.
설정 파일과 저널을 번들 내부에 넣지 않으며 기존 LOCALAPPDATA 경로를 유지합니다.
SDK/DLL의 재배포 범위와 Microsoft 런타임 설치 요건은 조직의 Bloomberg·IT 배포 조건을 확인하십시오.
승인된 Microsoft x64 앱 로컬 CRT 폴더가 있으면 `EMSX_VC_REDIST_DIR`로 지정하여
명시된 VC++ 런타임 DLL을 번들에 포함할 수 있습니다. OS 필수 구성요소가 필요한 경우
승인된 오프라인 설치 패키지로 준비합니다.
개인 PC의 Terminal 폴더에서 무관한 DLL을 일괄 복사하지 마십시오.

## 자산과 아이콘

`resource_paths.asset_path`는 소스 실행에서는 소스 폴더, 단일 파일에서는 `sys._MEIPASS`를 사용합니다.
`assets/desk_alert.wav`는 필수로 포함합니다. 자체 제작한 차콜·금색 모니터 아이콘 `assets/emsx_monitor.ico`도 포함하며 창·실행 파일에 사용합니다.
이는 공식 KB 로고가 아닙니다. 조직의 승인된 아이콘으로 교체할 수 있습니다.
로고를 외부에서 다운로드하지 않습니다.

단일 파일은 시작할 때 사용자 임시 폴더에 압축을 해제합니다. 대상 PC의 임시 폴더 쓰기·실행 허용과
보안 솔루션 정책을 확인하십시오. 서명·허용 목록이 필요하면 빌드 후 조직의 서명 절차를 적용합니다.

## Python 없는 대상 PC 검증

빌드 산출물만 복사하고 Python·wheelhouse·SDK 설치 경로가 없는 대상 PC에서 다음을 실행합니다.
출력 보고서는 쓰기 가능한 폴더를 지정하십시오.

```bat
start /wait "" "KB증권 해외주식영업부 EMSX 모니터.exe" --self-test "%TEMP%\EMSX_패키지_검증.json"
start "" "KB증권 해외주식영업부 EMSX 모니터.exe" --demo
```

자체 검사는 BLPAPI Python/C++ 버전, Tk 초기화, SQLite, 음원 파일 해석을 기록합니다.
성공하면 종료 코드 0을 반환합니다. Bloomberg에 연결하거나 Ack를 전송하지 않습니다.
이어 DEMO에서 표·ADV·아이콘과 `알림음 확인`을 점검합니다. 자체 검사만으로 실제 스피커 출력을 검증하지 않습니다.
마지막으로 승인된 Terminal 세션에서 기본 구독·참조데이터를 확인하고, 자동 Ack는 별도 승인 절차를 유지합니다.

현재 Linux 환경에서 Windows 실행 파일을 생성하지 않았습니다.
Windows 빌드·독립 실행·실제 Terminal·스피커 검증이 완료되었다고 간주하지 마십시오.

현재 검증: 기존 테스트와 패키징 보조 기능 테스트를 포함한 110개 통과, Python·spec 구문 및 정적 검사 통과.
이는 Windows 네이티브 빌드나 Python 없는 대상 PC의 검증 결과가 아닙니다.
