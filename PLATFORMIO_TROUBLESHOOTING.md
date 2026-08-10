# PlatformIO IDE "Could not find compatible PlatformIO Core" — 해결 기록

날짜: 2026-08-05
환경: Ubuntu 26.04, 시스템 Python 3.14만 설치됨 (apt로 3.9~3.12 설치 불가)

## 증상
PlatformIO IDE 확장에서 다음 오류 발생:
```
Could not find compatible PlatformIO Core
```
`platformio-ide.useBuiltinPIOCore`를 `true`로 설정해도 재현됨.

## 근본 원인

1. **Python 호환성**: 확장은 내부적으로 임베드된 부트스트랩 스크립트(`get-platformio-1.2.2.py`, `platformio-node-helpers/dist/index.js` 안의 JS 문자열 리터럴로 존재)를 실행해 Core 설치 여부를 체크한다. 이 체크가 시스템 Python 3.14로 실행되면 `Could not find Python venv module` 로 실패한다 — Debian/Ubuntu가 `venv`를 `python3.14-venv`라는 별도 apt 패키지로 분리했는데 미설치 상태였고, 애초에 3.14는 PlatformIO Core가 지원하기엔 너무 최신 버전이었다.
2. **`useBuiltinPython: true`는 Linux에서 완전히 무효**: 확장은 PlatformIO Registry에서 "python-portable" 패키지를 받으려 시도하지만, 레지스트리에는 `windows_*`/`darwin_*` 빌드만 있고 `linux_x86_64`용 에셋이 아예 없다. 다운로드는 `console.warn`으로 조용히 실패하고 시스템 PATH 검색으로 폴백되므로, 이 옵션을 켜봐야 아무 효과가 없다.
3. **재발 원인 (워크스페이스 설정 우선순위)**: 개별 프로젝트의 `.vscode/settings.json`을 모두 수정했음에도 오류가 재발했다. 실제로는 멀티루트 워크스페이스가 `2026_GwangjuBiennale.code-workspace` 파일로 열리고 있었고, 이 파일 최상위의 `"settings"` 블록이 폴더별 `.vscode/settings.json`보다 **우선순위가 높아** 오래된 `useBuiltinPIOCore: false`와 구버전 `customPATH`로 덮어쓰고 있었다.

## 해결 방법

1. `apt`/`sudo`/root 권한 없이 `uv` 정적 바이너리(`uv-x86_64-unknown-linux-gnu.tar.gz`)를 직접 다운로드해 압축 해제.
2. `uv python install 3.11` → 호환되는 CPython 3.11.15(`venv`/`distutils` 정상 포함)를 `~/.local/share/uv/python/cpython-3.11.15-linux-x86_64-gnu/`에 설치.
3. uv가 관리하는 원본 인터프리터를 그대로 심볼릭 링크하면 venv 인식이 깨지므로(venv 감지가 심볼릭 링크를 따라가 실제 base 설치로 `sys.prefix`가 잘못 잡힘), **반드시 venv를 새로 생성**:
   ```
   ~/.local/share/uv/python/cpython-3.11.15-linux-x86_64-gnu/bin/python3 -m venv ~/.local/share/pio-python3.11-venv
   ~/.local/share/pio-python3.11-venv/bin/pip install platformio
   ```
4. 전역 User `settings.json`과 모든 프로젝트의 `.vscode/settings.json`에 다음 설정 적용:
   - `platformio-ide.customPATH`: `~/.local/share/pio-python3.11-venv/bin` 을 기존 경로 앞에 추가
   - `platformio-ide.useBuiltinPIOCore`: `true`
   - `platformio-ide.useBuiltinPython`: `false` (Linux에서 no-op이므로 켜둘 이유 없음)
5. **가장 중요한 단계**: 실제 워크스페이스 진입점인 `2026_GwangjuBiennale.code-workspace`의 최상위 `"settings"` 블록도 위와 동일하게 수정 (폴더별 설정보다 우선순위가 높아 여기가 최종 적용됨).
6. VS Code를 **완전히 종료 후 재시작** (단순 "Reload Window"로는 확장 호스트가 새 PATH를 인식하지 못함).

## 검증 방법 (IDE 재시작 없이 빠르게 확인)
임베드된 부트스트랩 스크립트를 `/tmp`에 추출해 직접 실행:
```bash
<venv-python3> get-platformio-1.2.2.py check python
# → "interpreter is compatible"
<venv-python3> get-platformio-1.2.2.py check core --auto-upgrade --global --version-spec ">=6.1.6" --dump-state <file>
# → "Found compatible PlatformIO Core 6.1.19"
```

## 디버깅 팁
- `~/.config/Code/logs/<최신 세션>/window1/renderer.log`에서 `grep -i platformio`로 실제 오류의 stack trace를 확인하는 것이, "PlatformIO Installation" 출력 채널의 일반적인 "Failed to install PlatformIO IDE." 메시지보다 훨씬 유용하다.
- VS Code Settings Sync가 전역 `settings.json`을 재시작 시 조용히 초기화(리셋)할 수 있으므로, 지속적으로 유지되어야 하는 설정은 워크스페이스/폴더 레벨(`.vscode/settings.json`, `*.code-workspace`)에 두는 것이 안전하다.
- 이 저장소처럼 최상위 `*.code-workspace` 파일로 멀티루트 워크스페이스를 여는 경우, 폴더별 설정이 맞는데도 문제가 재발하면 **워크스페이스 설정 우선순위 문제**부터 의심하고 `*.code-workspace` 파일의 `settings` 블록을 먼저 확인할 것.
</content>
</invoke>
