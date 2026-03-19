# clipd

macOS 클립보드 히스토리 관리 CLI — 텍스트/이미지 저장, 이미지 OCR, 풀텍스트 검색

## 설치

```bash
curl -fsSL https://raw.githubusercontent.com/giwonb612/clipd/main/install.sh | sh
```

또는 pipx로 직접:
```bash
pipx install git+https://github.com/giwonb612/clipd.git
```

## 시작하기

```bash
clipd daemon start   # 백그라운드 데몬 실행 (재부팅 후 자동 시작)
clipd list           # 히스토리 조회
```

## 명령어

| 명령어 | 설명 |
|--------|------|
| `clipd list [-n N] [--type text\|image] [--tag TAG] [--pinned]` | 히스토리 조회 |
| `clipd search <쿼리>` | 풀텍스트 검색 (OCR 포함) |
| `clipd show <id>` | 상세 보기 |
| `clipd copy <id> [--ocr]` | 클립보드로 복사 |
| `clipd pin <id>` / `unpin` | 고정 (clear 시 보호) |
| `clipd tag <id> <태그>` / `untag` | 태그 관리 |
| `clipd delete <id>` | 삭제 |
| `clipd clear [--days N]` | 일괄 삭제 (고정 항목 제외) |
| `clipd export [--format json\|csv]` | 내보내기 |
| `clipd watch` | 실시간 클립보드 모니터링 |
| `clipd open <id>` | 이미지 Quick Look 열기 |
| `clipd stats` | DB 통계 |
| `clipd daemon start\|stop\|restart\|status\|log` | 데몬 관리 |

## 요구사항

- macOS 12+
- Python 3.11+
- [pipx](https://pipx.pypa.io/) (install.sh가 자동 설치)

## 데이터 저장 위치

- DB: `~/.clipd/history.db`
- 로그: `~/.clipd/daemon.log`
