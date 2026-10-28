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

탐색기에서 `windows_build.py`와 `requirements.txt`가 보이는 프로젝트 폴더를 엽니다.
주소창에 `cmd`를 입력하고 Enter를 누르면 해당 폴더의 명령 프롬프트가 열립니다.
아래 명령은 **한 줄씩 입력하고 Enter를 누른 뒤, 완료되어 프롬프트가 돌아오면 다음 명령을 실행**합니다.

```bat
python windows_build.py download
```

이 명령은 PyInstaller 의존성은 기존 pip 인덱스, BLPAPI는 Bloomberg 공식 Python 저장소
`https://blpapi.bloomberg.com/repository/releases/python/simple`에서 가져옵니다.
공식 저장소 접근이 제한되거나 현재 Python 버전의 wheel이 없으면 실패를 그대로 보고합니다.
인증서 검증을 끄거나 임의의 비공식 DLL로 대체하지 마십시오.

필요한 BLPAPI wheel이 일반 패키지 저장소에 없으면 공식 배포본을 같은 폴더에 반입합니다.
인터넷에서 무작위 DLL을 내려받거나 검증을 끄지 마십시오. wheelhouse의 해시·서명은 반입 정책에 따라 확인합니다.
소스와 wheelhouse를 빌드 PC로 옮긴 뒤 실행합니다.

먼저 설치를 실행합니다.

```bat
python windows_build.py prepare
```

설치가 성공한 뒤 빌드합니다.

```bat
python windows_build.py build
```

단축 실행기 `download_dependencies.bat`, `prepare_offline.bat`, `build_windows.bat`도 제공합니다.
이 파일들은 ASCII·CRLF이며 같은 Python 도구에 작업을 위임합니다.
`EMSX_BUILD_PYTHON`은 배치 실행기의 인터프리터 선택에 사용합니다.
Python 도구를 직접 호출하면 현재 명령의 Python 실행 파일을 계속 사용합니다.

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

현재 검증: 기존 테스트와 패키징 보조 기능 테스트를 포함한 128개 통과, Python·spec 구문 및 정적 검사 통과.
이는 Windows 네이티브 빌드나 Python 없는 대상 PC의 검증 결과가 아닙니다.


## 2.6.2 CMD 해석 오류 해결

이전 배치 파일의 LF·UTF-8 한글·긴 인라인 명령을 제거했습니다.
실제 배치 파일 바이트는 CRLF로 저장하고 `.gitattributes`로 Git의 LF 정규화를 막습니다.
GitHub ZIP에도 CRLF가 포함되도록 보존합니다. 의존성 설치·빌드·자체 검사·실패 종료 코드는
Python의 구조화된 subprocess 인수로 처리하여 CMD의 따옴표·한글 해석에 의존하지 않습니다.

`requirements.txtprepare_offline.bat`나 `requirements.txtcd /d` 오류는 두 명령이 붙어서
실행된 경우입니다. 입력 중인 줄이 남아 있으면 Esc로 비우고 명령 하나만 붙여넣은 뒤 Enter를 누르십시오.
`프로젝트를 압축 해제한 실제 경로`는 실행할 경로가 아니므로 입력하지 마십시오.
폴더 이동이 완료되고 requirements.txt를 확인한 상태라면 다시 이동할 필요가 없습니다.


## 2.6.3 Windows CP949 파일 읽기 오류

Windows Python 3.13에서 .gitattributes의 UTF-8 한글 주석을 기본 CP949로 읽어
빌드 전 테스트가 실패하는 사례를 수정했습니다. 프로젝트의 Path.read_text·write_text는
UTF-8을 명시하며 requirements.txt는 UTF-8 BOM도 허용합니다.
콘솔의 `chcp 65001`만으로 Python 파일 입출력의 기본 인코딩이 바뀌는 것은 아닙니다.

빌드 도구는 테스트·PyInstaller·pip 자식 Python에 `-X utf8`을 직접 전달합니다.
전역 Windows 로캘이나 기존 계정 설정을 바꾸지 않습니다.
기존 배포본에서 다운로드·설치가 이미 성공했다면 프로젝트 폴더에서 아래 한 줄로
새 CMD 프로세스와 그 자식에만 UTF-8 모드를 적용하여 빌드만 재시도할 수 있습니다.

```bat
cmd /d /c "set PYTHONUTF8=1&&python windows_build.py build"
```

모의 세션 테스트에는 openServiceAsync 대역을 추가하고 예상된 연결 단절 로그를 테스트에서
검증하도록 정리했습니다. 실제 연결 단절·순번 유실에 대한 검증을 건너뛰거나 무시하지 않습니다.
117개 테스트를 기본 인코딩 사용 경고를 오류로 처리하는 모드에서도 통과했습니다.
이는 실제 Windows 네이티브 빌드 완료를 뜻하지 않습니다.


## 2.6.4 BLPAPI ffiutils 로더 호환성

사용자 PC에서는 테스트와 EXE 생성이 성공한 뒤 자체 검사에서
`blpapi.internals._loadLibrary()`의 `UnboundLocalError: toPy`가 발생했습니다.
제공된 SDK 로더는 `blpapi` 폴더에서 `ffiutils.*.pyd`를 glob으로 검색하고 첫 파일을 PyDLL로 로드합니다.
이 검색에 맞는 파일이 없으면 toPy가 초기화되지 않은 채 반환되어 실패합니다.
일반 DLL 검색 경로를 늘리거나 blpapi3_64.dll만 포함하는 것으로는 이 조건을 해결할 수 없습니다.

새 spec은 정상 설치된 SDK가 실제 로드한 `libffastcalls._name`에서 원본 파일을 확인합니다.
필요한 종속성 분석을 수행한 후 Analysis가 끝난 시점에 원본 ABI 파일명을
`blpapi/ffiutils.<ABI>.pyd`의 명시적 BINARY 항목으로 보존합니다.
정규화된 `ffiutils.pyd`가 있어도 SDK의 원래 glob 검색이 일치하도록 합니다.
SDK 로더 소스나 바이너리 내용을 수정하지 않습니다. 실제 로드 파일을 확인할 수 없는 경우
후보가 하나일 때만 사용하고, 여러 후보를 임의 선택하지 않습니다.

실행 파일의 자체 검사는 UI 가져오기보다 먼저 시작합니다.
각 단계 직전에 JSON 보고서를 저장하고, BLPAPI 네이티브 SessionOptions 객체 생성으로 C++ API를 확인합니다.
이 검사는 Session을 시작하지 않으므로 Bloomberg 접속이나 Ack 전송을 하지 않습니다.
버전 조회 함수가 없는 SDK는 버전 메타데이터만 미제공으로 남기되, 네이티브 객체 생성은 반드시 통과해야 합니다.
자체 검사 프로세스가 종료 코드 1을 반환해도 보고서를 먼저 읽어 원래 스택과 단계를 콘솔에 표시합니다.
보고서 미생성·손상·시간 초과와 실제 검사 실패를 구분하며 이전 성공 보고서를 재사용하지 않습니다.

소스의 spec·`blpapi_bundle.py`·`emsx_entry.py`·검사 도구를 포함한 전체 수정본을 교체하고
현재 프로젝트 폴더에서 `python windows_build.py build`만 다시 실행하십시오.
앞선 다운로드·설치가 성공했다면 Python이나 BLPAPI를 다시 설치할 필요는 없습니다.
새 실행 파일이 실제 자체 검사를 통과하기 전에는 배포 완료로 간주하지 않습니다.

총 128개 테스트가 통과했습니다. SDK glob 불일치 재현과 ABI 파일명 보존, 보고서 오류 출력,
네이티브 API 실패와 버전 미제공 구분, UI 이전 자체 검사 진입을 포함합니다.
Linux에서 수행한 회귀 검증이며 실제 Windows DLL 실행·새 EXE 검증은 대상 PC 재빌드가 필요합니다.


## 2.6.5 배치 자동 Ack 적용

전체 수정 소스를 교체한 뒤 `python windows_build.py build`로 실행 파일을 다시 만드십시오.
앞선 다운로드·설치가 정상 완료되었다면 의존성을 다시 받을 필요가 없습니다.
기존 설정·저널·감사 파일은 보존합니다. DB 이관과 새 패키지 설치는 필요하지 않습니다.
자동 Ack는 기본 해제 상태이며, 배치 처리 세부 기준과 감사 형식은
[사용 안내](README_KO.md#265-자동-ack-배치-전송)에 정리했습니다.
실제 Windows 자체 검사 및 승인된 환경의 Bloomberg 배치 응답 확인은 별도로 수행해야 합니다.


## 2.6.6 전체 대기 목록 단일 요청 적용

이번 버전은 50개 분할과 단건 대체 전송을 제거합니다. 전체 소스를 교체한 뒤
기존 빌드 PC에서 `python windows_build.py build`를 실행하십시오.
의존성 다운로드·재설치나 DB 초기화는 필요하지 않습니다.
모니터링 시작 시 Event Log에 `버전 2.6.6`이 표시되어야 합니다.
자동 Ack 사용 시 Event Log의 `Order N개 단일 요청`과 진단 파일의
`[배치 Ack] Order N개 ... sendRequest=1 CID=...`를 확인할 수 있습니다.
이는 요청 제출 기록이며 실제 처리 결과는 배치 응답·저널·EMSX 상태로 대조합니다.
총 165개 오프라인 테스트를 통과했으며 대상 Windows 자체 검사와 Bloomberg 응답 검증은 별도입니다.


## 2.6.7 빌드 전 테스트의 WinError 32 수정

`test_worker_batches_on_background_thread_and_honors_stop_command` 종료 시
임시 폴더의 `feed_diagnostics.log`가 열려 있어 삭제에 실패하는 문제를 수정했습니다.
작업자 종료·초기화 실패 시 진단 파일을 정상 해제하며 기존 배치 Ack는 유지합니다.
`diagnostics.py`, `bloomberg_feed.py`, `tests/test_diagnostics.py` 등을 포함한 전체 소스를 교체하고
프로젝트 폴더에서 `python windows_build.py build`를 다시 실행하십시오.
기존 wheelhouse·설치 패키지·설정·DB는 유지하며 추가 다운로드나 재설치는 필요하지 않습니다.
총 172개 테스트를 통과했습니다. 대상 PC에서는 테스트에 이어
`빌드·독립 실행 검사 완료`가 출력되는지 확인하십시오.


## 2.6.8 지정 국가·MOC 경고 규칙 적용

전체 소스를 교체하고 `python windows_build.py build`로 실행 파일을 다시 만드십시오.
기존 wheelhouse·설정·DB는 유지하며 패키지 재설치가 필요하지 않습니다.
Route 지연과 No-Fill은 지정 12개 발행국 ISO 코드에만 적용하고,
Instruction에 MOC가 포함되면 No-Fill 시각·음향 경고에서 제외합니다.
시작 Event Log의 `버전 2.6.8`과 빌드 완료·독립 실행 검사 결과를 확인하십시오.
총 184개 오프라인 테스트를 통과했으며 실제 Windows와 Bloomberg 응답은 별도 확인이 필요합니다.
