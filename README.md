# History Diagram Pages

Notion DB를 읽어서 히스토리 다이어그램 HTML을 생성합니다.

GitHub Pages가 `main` 브랜치의 저장소 루트를 바라보면 아래처럼 배포됩니다.

- `afl/index.html` -> `https://chojozo.github.io/webpage/afl`
- `sample/index.html` -> `https://chojozo.github.io/webpage/sample`

## 로컬 실행

Notion Integration을 만들고, 대상 DB에 해당 Integration을 공유한 뒤 실행합니다.

```powershell
$env:NOTION_TOKEN = "secret_xxx"
python .\scripts\build_history.py --source-type notion --output afl\index.html
```

다른 페이지를 추가할 때는 출력 폴더만 바꾸면 됩니다.

```powershell
$env:NOTION_TOKEN = "secret_xxx"
python .\scripts\build_history.py --source-type notion --notion-database-id "NOTION_DB_ID" --output new-page\index.html
```

속성명을 자동 추론하기 어렵다면 아래 값을 지정할 수 있습니다.

```powershell
$env:NOTION_DATE_PROPERTY = "날짜"
$env:NOTION_TITLE_PROPERTY = "제목"
$env:NOTION_DESCRIPTION_PROPERTY = "내용"
```

## GitHub Actions 수동 업데이트

`.github/workflows/update-history.yml`은 자동 스케줄 없이 수동 실행만 합니다.

1. GitHub 저장소 `Settings > Pages`에서 `Deploy from a branch`를 선택합니다.
2. Branch는 `main`, folder는 `/root`로 설정합니다.
3. `Settings > Secrets and variables > Actions`에 `NOTION_TOKEN`을 등록합니다.
4. `Actions > Update AFL history diagram > Run workflow`를 누릅니다.
5. `slug`에 URL 경로를 입력합니다. 예: `afl`
6. `notion_database_id`에 DB ID 또는 URL을 넣습니다. 비우면 기본 AFL DB를 사용합니다.

워크플로가 실행되면 `{slug}/index.html`을 생성하고 변경사항을 자동 커밋/푸시합니다.

## 사내 LLM 요약

사내 LLM은 OpenAI 호환 `POST /v1/chat/completions` 형식으로 호출합니다. 키는 코드에 저장하지 말고 환경변수나 GitHub Secrets에 넣습니다.

```powershell
$env:AFL_LLM_BASE_URL = "http://192.168.1.128:9800"
$env:AFL_LLM_API_KEY = "발급받은 키"
$env:AFL_LLM_MODEL = "모델명"
$env:AFL_LLM_TIMEOUT = "90"
python .\scripts\build_history.py --source-type notion --output afl\index.html
```

LLM 연결이 실패하거나 결과 품질 검사를 통과하지 못하면 규칙 기반 요약으로 자동 생성합니다. LLM을 끄려면 `--no-llm`을 사용합니다.
