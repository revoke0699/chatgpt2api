"""Shared OpenAI OAuth protocol constants and request headers."""

from __future__ import annotations


auth_base = "https://auth.openai.com"
platform_base = "https://platform.openai.com"
platform_oauth_client_id = "app_2SKx67EdpoN0G6j64rFvigXD"
platform_oauth_redirect_uri = f"{platform_base}/auth/callback"
chatgpt_ios_oauth_redirect_uri = "com.openai.chat://auth0.openai.com/ios/com.openai.chat/callback"
platform_oauth_audience = "https://api.openai.com/v1"
platform_auth0_client = "eyJuYW1lIjoiYXV0aDAtc3BhLWpzIiwidmVyc2lvbiI6IjEuMjEuMCJ9"

user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)
sec_ch_ua = '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"'
sec_ch_ua_full_version_list = (
    '"Chromium";v="142.0.0.0", '
    '"Google Chrome";v="142.0.0.0", '
    '"Not_A Brand";v="99.0.0.0"'
)

def oauth_refresh_request(
    refresh_token: str,
    *,
    client_id: str,
    redirect_uri: str = "",
) -> tuple[dict[str, dict[str, str]], str]:
    """Build the refresh-token request for one Upstream Account.

    Accounts issued with the iOS redirect must send that redirect as JSON.
    Accounts without one keep the form body used by the existing refresh path.
    """

    body = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    redirect = str(redirect_uri or "").strip()
    if redirect:
        body["redirect_uri"] = redirect
        return {"json": body}, "application/json"
    return {"data": body}, "application/x-www-form-urlencoded"


common_headers = {
    "accept": "application/json",
    "accept-encoding": "gzip, deflate, br",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "no-cache",
    "connection": "keep-alive",
    "content-type": "application/json",
    "dnt": "1",
    "origin": auth_base,
    "priority": "u=1, i",
    "sec-gpc": "1",
    "sec-ch-ua": sec_ch_ua,
    "sec-ch-ua-arch": '"x86_64"',
    "sec-ch-ua-bitness": '"64"',
    "sec-ch-ua-full-version-list": sec_ch_ua_full_version_list,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": '""',
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-platform-version": '"10.0.0"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": user_agent,
}
