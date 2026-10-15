"""Parse RT delivery lines and turn a successful exchange into an account payload."""

from __future__ import annotations

from dataclasses import dataclass

from utils.diagnostics import sanitize_diagnostic_text


_SEPARATOR = "----"
_MAX_CHARS = 1_000_000
_MAX_LINES = 2000


@dataclass(frozen=True)
class RefreshDeliveryRow:
    line_number: int
    email: str
    password: str
    refresh_token: str


def parse_refresh_token_delivery(
    text: str,
) -> tuple[list[RefreshDeliveryRow], list[dict[str, str]]]:
    """Split `账号----GPT密码----邮箱密码----refreshToken` lines.

    The mailbox password is discarded. Blank lines and `#` comments are ignored.
    """

    raw = str(text or "")
    if len(raw) > _MAX_CHARS:
        raise ValueError("发货文本过长")

    rows: list[RefreshDeliveryRow] = []
    errors: list[dict[str, str]] = []
    seen_tokens: set[str] = set()
    data_lines = 0
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        data_lines += 1
        if data_lines > _MAX_LINES:
            raise ValueError("一次最多导入 2000 行")
        parts = [part.strip() for part in line.split(_SEPARATOR)]
        if len(parts) != 4 or not parts[0] or not parts[3]:
            errors.append(
                {
                    "id": f"line-{line_number}",
                    "code": "invalid_delivery_line",
                    "message": f"第 {line_number} 行格式不正确",
                }
            )
            continue
        refresh_token = parts[3]
        if refresh_token in seen_tokens:
            errors.append(
                {
                    "id": parts[0],
                    "code": "duplicate_refresh_token",
                    "message": f"第 {line_number} 行的 RT 与前面重复",
                }
            )
            continue
        seen_tokens.add(refresh_token)
        rows.append(
            RefreshDeliveryRow(
                line_number=line_number,
                email=parts[0],
                password=parts[1],
                refresh_token=refresh_token,
            )
        )
    return rows, errors


def public_exchange_error(
    exc: BaseException,
    row: RefreshDeliveryRow,
    secrets: tuple[str, ...],
) -> dict[str, str]:
    detail = sanitize_diagnostic_text(
        exc,
        sensitive_values=secrets,
        limit=300,
    )
    message = f"RT 换 AT 失败：{detail}" if detail else "RT 换 AT 失败"
    return {
        "id": row.email,
        "code": "refresh_exchange_failed",
        "message": message,
    }


def build_refresh_token_delivery_accounts(
    rows: list[RefreshDeliveryRow],
    exchanged_by_line: dict[int, dict[str, str]],
    *,
    redirect_uri: str,
    target_group_id: str | None = None,
) -> list[dict[str, str]]:
    payloads: list[dict[str, str]] = []
    for row in rows:
        token_data = exchanged_by_line.get(row.line_number)
        if not token_data:
            continue
        access_token = str(token_data.get("access_token") or "").strip()
        refresh_token = str(token_data.get("refresh_token") or row.refresh_token).strip()
        if not access_token or not refresh_token:
            continue
        payload = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "email": row.email,
            "oauth_redirect_uri": redirect_uri,
            "source_type": "web",
        }
        identity_token = str(token_data.get("id_token") or "").strip()
        if identity_token:
            payload["id_token"] = identity_token
        if row.password:
            payload["password"] = row.password
        if target_group_id is not None:
            payload["group_id"] = target_group_id
        payloads.append(payload)
    return payloads
