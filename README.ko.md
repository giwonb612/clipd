# clipd

macOS 터미널용 클립보드 히스토리 관리 CLI — 텍스트와 이미지를 자동으로 캡처하고, 이미지에서 OCR로 텍스트를 추출하며, 모든 데이터를 로컬에 저장합니다. 빠른 검색과 재사용을 위한 CLI를 제공합니다.

## 주요 기능

- **자동 캡처** — 백그라운드 데몬이 1초마다 클립보드를 감시
- **이미지 OCR** — Apple Vision으로 스크린샷/이미지에서 텍스트 추출 (완전 로컬, 네트워크 불필요)
- **풀텍스트 검색** — SQLite FTS5로 텍스트 내용과 OCR 결과를 동시에 검색
- **터미널 인라인 이미지** — Ghostty, iTerm2, WezTerm, Kitty에서 이미지 직접 표시
- **핀 & 태그** — 중요한 클립 고정 및 분류, 일괄 삭제 시 보호
- **내보내기** — 히스토리를 JSON 또는 CSV로 내보내기
- **백업 & 복원** — 이미지 포함 전체 히스토리를 명령어 하나로 새 컴퓨터로 이전
- **클라우드 없음** — 모든 데이터는 `~/.clipd/history.db`에 로컬 저장

## 요구사항

- macOS 12+
- Python 3.11+
- [pipx](https://pipx.pypa.io/) (설치 스크립트가 자동으로 설치)

## 설치

```bash
curl -fsSL https://raw.githubusercontent.com/giwonb612/clipd/main/install.sh | sh
```

pipx로 직접 설치:

```bash
pipx install git+https://github.com/giwonb612/clipd.git
```

## 빠른 시작

```bash
# 백그라운드 데몬 시작 (로그인 시 자동 실행)
clipd daemon start

# 최근 히스토리 조회
clipd list

# 검색 (이미지 OCR 내용 포함)
clipd search "회의 메모"

# 클립보드로 다시 복사
clipd copy 42
```

## 명령어

### 히스토리 조회

| 명령어 | 설명 |
|--------|------|
| `clipd list` | 최근 히스토리 목록 |
| `clipd list -n 50` | 최근 50개 표시 |
| `clipd list --type image` | 타입 필터 (`text` 또는 `image`) |
| `clipd list --tag work` | 태그 필터 |
| `clipd list --pinned` | 고정 항목만 표시 |
| `clipd list --full` | 전체 내용 표시 (이미지 인라인 렌더링) |
| `clipd search <쿼리>` | 텍스트 및 OCR 풀텍스트 검색 |
| `clipd show <id>` | 상세 보기 (긴 내용은 pager 자동 실행) |
| `clipd show <id> --raw` | 순수 텍스트만 출력 (파이프 연결용) |

### 작업

| 명령어 | 설명 |
|--------|------|
| `clipd copy <id>` | 클립보드로 복사 |
| `clipd copy <id> --ocr` | 이미지 클립의 OCR 텍스트 복사 |
| `clipd open <id>` | 이미지를 Quick Look으로 열기 |
| `clipd delete <id>` | 클립 삭제 |
| `clipd pin <id>` | 클립 고정 (`clear` 시 보호됨) |
| `clipd unpin <id>` | 고정 해제 |
| `clipd tag <id> <이름>` | 태그 추가 |
| `clipd untag <id> <이름>` | 태그 제거 |
| `clipd clear` | 고정되지 않은 전체 히스토리 삭제 |
| `clipd clear --days 7` | 7일 이전 항목 삭제 |
| `clipd export` | JSON으로 내보내기 (stdout) |
| `clipd export -f csv -o out.csv` | CSV 파일로 내보내기 |
| `clipd backup` | 전체 백업 생성 (텍스트 + 이미지) |
| `clipd backup -o ~/my.db` | 경로 지정 백업 |
| `clipd restore <file>` | 백업을 현재 히스토리에 병합 |
| `clipd restore <file> --replace` | 현재 히스토리를 백업으로 교체 |
| `clipd stats` | DB 통계 보기 |
| `clipd watch` | 클립보드 변경 실시간 모니터링 |

### 데몬 관리

| 명령어 | 설명 |
|--------|------|
| `clipd daemon start` | launchd에 등록 후 시작 |
| `clipd daemon stop` | 중지 및 등록 해제 |
| `clipd daemon restart` | 재시작 |
| `clipd daemon status` | 실행 상태 확인 |
| `clipd daemon log` | 최근 로그 보기 |
| `clipd daemon log -f` | 로그 실시간 스트림 |

## 터미널 인라인 이미지

지원 터미널에서 `clipd show <id>` 및 `clipd list --full` 실행 시 이미지가 터미널에 직접 표시됩니다:

| 터미널 | 프로토콜 |
|--------|---------|
| Ghostty | ESC]1337 |
| iTerm2 | ESC]1337 |
| WezTerm | ESC]1337 |
| Kitty | `kitty +kitten icat` |
| 그 외 | `clipd open <id>` 안내 |

## 데이터 저장 위치

| 경로 | 용도 |
|------|------|
| `~/.clipd/history.db` | SQLite 데이터베이스 (무제한 히스토리) |
| `~/.clipd/daemon.log` | 데몬 로그 |
| `~/Library/LaunchAgents/com.clipd.daemon.plist` | launchd 서비스 설정 |

## 새 컴퓨터로 이전

```bash
# 기존 컴퓨터에서
clipd backup -o ~/Desktop/clipd-backup.db

# AirDrop, USB, iCloud 등으로 전송 후, 새 컴퓨터에서
clipd restore ~/Desktop/clipd-backup.db
```

`restore`는 기본적으로 병합 방식 — 기존 클립은 유지하고 새 항목만 추가합니다 (콘텐츠 해시 기반 중복 제거). `--replace`를 사용하면 현재 히스토리 전체를 백업으로 교체합니다.

## 업그레이드

```bash
pipx upgrade clipd
```

## 라이선스

MIT
