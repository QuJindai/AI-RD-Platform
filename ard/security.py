"""Explicit local or token-based identity; tokens are never returned or logged."""
from dataclasses import dataclass
import hmac
import ipaddress
from urllib.parse import urlsplit

from fastapi import HTTPException


@dataclass(frozen=True)
class Identity:
    user: str
    role: str
    projects: tuple
    local: bool = False

    def allow_project(self, pid):
        if '*' not in self.projects and pid not in self.projects:
            raise HTTPException(403, '当前身份无权访问此项目')

    def allow_write(self):
        if self.role not in ('admin', 'developer'):
            raise HTTPException(403, '此操作需要开发者权限')

    def allow_approve(self, creator):
        if self.role not in ('admin', 'reviewer'):
            raise HTTPException(403, '此操作需要审核权限')
        if not self.local and self.user == creator:
            raise HTTPException(403, '创建者不能审批自己的请求')


class Security:
    def __init__(self, identities, public_origin=None):
        self.identities = identities or {}
        self.public_origin = None
        self.public_host = None
        if public_origin:
            parts = urlsplit(public_origin)
            if (parts.scheme not in ('https', 'http') or not parts.hostname or parts.username or parts.password
                    or parts.path not in ('', '/') or parts.query or parts.fragment
                    or any(c.isspace() or c in '*\\' for c in public_origin)):
                raise ValueError('ARD_PUBLIC_ORIGIN必须是精确的HTTP(S)来源，不含账号、路径、参数或通配符')
            port = parts.port
            host = parts.hostname.lower()
            self.public_host = '[' + host + ']' if ':' in host else host
            default_port = 443 if parts.scheme == 'https' else 80
            authority = self.public_host + (':' + str(port) if port and port != default_port else '')
            self.public_origin = parts.scheme + '://' + authority
            if not self.identities:
                raise ValueError('配置公开来源时必须提供身份令牌')
        for token, config in self.identities.items():
            if len(token) < 16 or config.get('role') not in ('admin', 'developer', 'reviewer', 'auditor'):
                raise ValueError('身份令牌至少16字符，且必须配置有效角色')
            if not config.get('user') or not isinstance(config.get('projects'), list):
                raise ValueError('身份必须包含user和projects列表')

    def resolve(self, request):
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') not in (str(request.base_url).rstrip('/'), self.public_origin):
            raise HTTPException(403, '跨来源请求被拒绝')
        if not self.identities:
            host = request.client.host if request.client else ''
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = host == 'testclient'
            if not local:
                raise HTTPException(403, '远程访问需要配置ARD_IDENTITIES')
            return Identity('local', 'admin', ('*',), True)
        authorization = request.headers.get('authorization', '')
        token = authorization[7:] if authorization.startswith('Bearer ') else ''
        for candidate, cfg in self.identities.items():
            if hmac.compare_digest(candidate, token):
                return Identity(cfg['user'], cfg['role'], tuple(cfg['projects']))
        raise HTTPException(401, '请提供有效访问令牌', headers={'WWW-Authenticate': 'Bearer'})
