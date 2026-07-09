# land_page

`land_page` 폴더만 별도로 복사해도 배포할 수 있는 FoodFlow 랜딩페이지입니다.

## 구성

- `app.py`: Flask 엔트리포인트
- `wsgi.py`: PythonAnywhere WSGI import용 파일
- `index.html`: 랜딩페이지 본문
- `assets/`: 이미지 자산
- `data/korea_oem_odm_seed.csv`: 랜딩 시뮬레이터 공장 데이터
- `inputs.json`: 랜딩 카피 원본 입력
- `.env.example`: SAM 연결용 환경 변수 예시

## 로컬 실행

```powershell
cd C:\Users\82102\Desktop\dev_main\Obsidian_Food_OEM_ODM\land_page
Copy-Item .env.example .env
python -m pip install -r requirements.txt
python app.py
```

기본 포트는 `8002`입니다.

`.env`에 실제 값을 넣어야 LLM 모드가 활성화됩니다.

```env
SAM_BASE_URL=https://sam.soonsoon.ai
SAM_API_KEY=여기에_실제_키
SAM_MODEL=az-deepseek-v4-flash
```

키가 없으면 페이지는 계속 열리지만 시뮬레이터는 규칙 기반 폴백으로 동작합니다.

## PythonAnywhere 배포

1. `land_page` 폴더만 업로드합니다.
2. 서버 안에서 `.env.example`을 `.env`로 복사하고 실제 `SAM_API_KEY`를 넣습니다.
3. 가상환경에서 의존성을 설치합니다.

```bash
pip install -r /home/<username>/Obsidian_Food_OEM_ODM/land_page/requirements.txt
```

4. 웹 앱 WSGI 파일에 아래처럼 연결합니다.

```python
import sys

project_path = "/home/<username>/Obsidian_Food_OEM_ODM/land_page"
if project_path not in sys.path:
    sys.path.append(project_path)

from wsgi import application
```

## 배포 메모

- 이 폴더는 상위 `database/` 또는 `landing_page/` 폴더에 더 이상 의존하지 않습니다.
- 프론트의 자산 경로와 API 호출은 상대경로라서 하위 경로 배포에도 대응합니다.
- `SAM_API_KEY`는 공개 저장소에 하드코딩하지 마세요. 반드시 서버의 `.env` 또는 환경 변수로 넣어야 합니다.
- GitHub Pages 같은 정적 호스팅만으로는 `/api/simulate`가 없어서 LLM 시뮬레이터를 안전하게 운영할 수 없습니다.

## 확인 경로

- `/`: 랜딩페이지
- `/health`: 서버 상태 확인
