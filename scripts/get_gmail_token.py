"""
Gmail OAuth2 refresh_token 획득 스크립트.

왜 이 스크립트가 필요한가:
    Gmail API는 OAuth2 인증이 필요. access_token은 1시간 후 만료되지만
    refresh_token이 있으면 자동 갱신 가능 (eng review issue #7).
    이 스크립트는 최초 1회만 실행 — 브라우저에서 Google 로그인하면
    refresh_token을 발급받아 .env에 저장.

실행 방법:
    1. .env에 GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET 설정
    2. python scripts/get_gmail_token.py
    3. 브라우저에서 Google 계정 로그인 + 권한 승인
    4. refresh_token이 .env에 자동 저장됨

의존성:
    google-auth-oauthlib → OAuth2 flow 처리
    이 스크립트 → .env 파일 → gmail_mcp.py가 읽음
"""

import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

# Gmail API scope:
# - gmail.readonly: 이메일 읽기 (fetch_emails)
# - gmail.modify: 이메일 상태 변경 (mark_read)
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

PROJECT_ROOT = Path(__file__).parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


def main():
    # .env에서 client_id, client_secret 읽기
    env_vars = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                env_vars[key.strip()] = value.strip()

    client_id = env_vars.get("GOOGLE_CLIENT_ID", "")
    client_secret = env_vars.get("GOOGLE_CLIENT_SECRET", "")

    if not client_id or not client_secret or client_id == "your-client-id":
        print("❌ .env에 GOOGLE_CLIENT_ID와 GOOGLE_CLIENT_SECRET을 먼저 설정하세요.")
        print(f"   파일 위치: {ENV_FILE}")
        return

    # OAuth flow 실행
    # InstalledAppFlow는 로컬 서버를 열어 브라우저 리다이렉트를 받음
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)

    print("🔑 브라우저에서 Google 계정 로그인 후 권한을 승인하세요...")
    creds = flow.run_local_server(port=0)

    # refresh_token 저장
    refresh_token = creds.refresh_token
    if not refresh_token:
        print("❌ refresh_token을 받지 못했습니다. OAuth 동의 화면에서 access_type=offline 확인하세요.")
        return

    # .env 업데이트
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    updated = False
    new_lines = []
    for line in lines:
        if line.startswith("GOOGLE_REFRESH_TOKEN="):
            new_lines.append(f"GOOGLE_REFRESH_TOKEN={refresh_token}")
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f"GOOGLE_REFRESH_TOKEN={refresh_token}")

    ENV_FILE.write_text("\n".join(new_lines) + "\n")

    # 토큰 정보도 별도 JSON으로 백업 (디버깅용)
    token_file = PROJECT_ROOT / "token.json"
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes),
    }
    token_file.write_text(json.dumps(token_data, indent=2))

    print(f"✅ refresh_token 저장 완료!")
    print(f"   .env: {ENV_FILE}")
    print(f"   token.json: {token_file} (백업)")
    print(f"\n   이제 gmail-mcp를 실행할 수 있습니다.")


if __name__ == "__main__":
    main()
